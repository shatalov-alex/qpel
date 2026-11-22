"""qpel's device-wide radix sort (frontends/include/libraries/qpel_library/device_radix_sort.cuh,
frontends/library.py): cub::DeviceRadixSort::SortPairs on 32- and 64-bit keys over a bit range, as
qpel kernels and the device scan. The library's CUDA runs as the original on the CPU simulator, qpel's
CUDA there, qpel's Metal on the Apple GPU, all against numpy's stable sort of the same bits; with
QPEL_NVIDIA=1, NVIDIA's own CUB on GPGPU-Sim and qpel's CUDA there, bit for bit."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_test_support import unavailable  # noqa: E402

NVIDIA = os.environ.get('QPEL_NVIDIA') == '1'
# NVIDIA's DeviceRadixSort runs right in GPGPU-Sim for 32-bit keys, and for 64-bit keys up to this many; beyond,
# its 64-bit sort gives wrong orders there (gpgpusim/README.md, Open). GPGPU-Sim compares qpel with the oracle at
# every size and with NVIDIA's CUB where it runs right; the T4 (differential/hardware.py) compares at every size.
CUB_SIMULATED = 257
NUMPY = {'u32': 'uint32', 'i32': 'int32', 'u64': 'uint64', 'i64': 'int64', 'f32': 'float32'}
# (key, value): gsplat sorts int64 intersection ids with int32 gaussian ids
VARIANTS = (('u32', 'u32'), ('i32', 'f32'), ('u64', 'u32'), ('i64', 'i32'))
SIZES = (1, 255, 256, 257, 3000, 20000)


def bit_ranges(key):
    bits = 32 if key in ('u32', 'i32') else 64
    return ((0, bits), (0, 20), (4, 28)) if bits == 32 else ((0, bits), (0, 46), (13, 40))


def data(key, value, n, few):
    """Keys over the full range (or, with few, from 16 values, so that stability shows) and values."""
    import numpy as np
    rng = np.random.default_rng(n * 7 + few)
    dtype = np.dtype(NUMPY[key])
    info = np.iinfo(dtype)
    keys = rng.integers(info.min, info.max, n, dtype=dtype, endpoint=True)
    if few:
        keys = keys[rng.integers(0, 16, n)] if n >= 16 else keys
    values = (np.arange(n) * 3 - n).astype(NUMPY[value]) if value != 'f32' else rng.standard_normal(n).astype(np.float32)
    return keys, values


def oracle(keys, values, begin, end):
    """CUB's result: keys stably sorted by bits [begin, end) of their bits, a signed key's sign bit flipped."""
    import numpy as np
    bits = keys.view(np.uint32 if keys.itemsize == 4 else np.uint64).copy()
    if keys.dtype.kind == 'i':
        bits ^= np.array(1 << (8 * keys.itemsize - 1), bits.dtype)
    width = end - begin
    mask = (1 << width) - 1
    digits = (bits.astype(np.uint64) >> np.uint64(begin)) & np.uint64(mask)
    order = np.argsort(digits, kind='stable')
    return keys[order], values[order]


def case(key, value, n, begin, end, few=False):
    from differential.simulate import Case
    keys, values = data(key, value, n, few)
    return Case({'n': (n, 1, 1)}, {'keys_in': keys, 'values_in': values}, {'begin_bit': begin, 'end_bit': end},
                f'{key}/{value} n={n} bits={begin}..{end}{" few" if few else ""}'), keys, values


def cases(key, value, sizes=SIZES):
    for n in sizes:
        for begin, end in bit_ranges(key):
            for few in (False, True):
                yield case(key, value, n, begin, end, few) + (begin, end)


class Built:
    def __init__(self, key, value, work):
        from frontends.c_import import import_source
        from frontends.library import device_radix_sort_pairs
        from qpel import compile_program
        from qpel_ir import validate
        self.key, self.value, self.work = key, value, work
        text, self.plan = device_radix_sort_pairs(key, value)
        self.source = work/(self.plan['module'] + '.cu')
        self.source.write_text(text)
        self.program, self.report = import_source(self.source, plan=self.plan)
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.generated = work/'build/package/generated'
        self.cuda = self.generated/(self.plan['module'] + '.cu')


def check(test, got_keys, got_values, keys, values, begin, end):
    import numpy as np
    want_keys, want_values = oracle(keys, values, begin, end)
    np.testing.assert_array_equal(np.asarray(got_keys).view(keys.dtype), want_keys)
    np.testing.assert_array_equal(np.asarray(got_values).view(values.dtype), want_values)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class DeviceRadixSort(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-radix-sort-')
        cls.built = {}
        for key, value in VARIANTS:
            work = Path(cls.tmp.name)/f'{key}_{value}'
            work.mkdir()
            cls.built[key, value] = Built(key, value, work)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_plan(self):
        from qpel_text import print_program
        b = self.built['i64', 'i32']
        self.assertEqual(len(b.plan['stages']), 8 * 6)  # 8 passes of 8 bits: histogram, 3 scan stages, scatter, advance
        self.assertEqual(len(self.built['u32', 'u32'].plan['stages']), 4 * 6)
        text = print_program(b.program)
        self.assertIn('scratch qlib_radix_counts: buffer<u32, write> in n / 256 * 256;', text)
        self.assertIn('zeroed qlib_radix_state: buffer<u32, write> [1];', text)
        last = b.plan['stages'][-2]
        self.assertEqual({x['parameter']: x['resource'] for x in last['bindings']}['keys_out'], 'keys_out')

    def test_cpu_simulator(self):
        from differential.simulate import Program
        for (key, value), b in self.built.items():
            for folder in ('o', 'q'):
                (b.work/folder).mkdir(exist_ok=True)
            original = Program(b.source, b.plan, b.work/'o', original=b.report)
            ours = Program(b.cuda, b.plan, b.work/'q')
            for c, keys, values, begin, end in cases(key, value, SIZES[:-1]):
                with self.subTest(c.label):
                    for program in (original, ours):
                        out = program.run(c)
                        check(self, out['keys_out'], out['values_out'], keys, values, begin, end)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for (key, value), b in self.built.items():
            host = matrix.load_host(b.generated)
            for c, keys, values, begin, end in cases(key, value):
                with self.subTest(c.label):
                    out = metal.run_generated(host, b.plan, c)
                    check(self, out['keys_out'], out['values_out'], keys, values, begin, end)

    @unittest.skipUnless(NVIDIA, 'GPGPU-Sim rows are slow; set QPEL_NVIDIA=1')
    def test_gpgpusim_against_nvidia_cub(self):
        import numpy as np
        from differential import gpgpusim
        from frontends.library import cub_sort_main
        if not gpgpusim.image_ready(build=False):
            unavailable(self, 'nvidia', 'no GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-radix-sort-nvidia-')).resolve()
        try:
            ours, runs = {}, {}
            for (key, value), b in self.built.items():
                name = f'{key}_{value}'
                cub = root/f'p_cub_{name}'
                cub.mkdir()
                (cub/'main.cu').write_text(cub_sort_main(key, value))
                (cub/'nvcc.args').write_text('\n')
                ours[name] = gpgpusim.GpuProgram(b.cuda, b.plan, root/f'p_qpel_{name}')
                for i, (c, keys, values, begin, end) in enumerate(cases(key, value, (257, 3000))):
                    folder = cub/f'c{i}'
                    folder.mkdir()
                    keys.tofile(folder/'keys.bin')
                    values.tofile(folder/'values.bin')
                    np.array([begin, end], np.uint32).tofile(folder/'bits.bin')
                    (ours[name].work/f'c{i}').mkdir()
                    ours[name].write_case(c, ours[name].work/f'c{i}')
                    runs[name, i] = (c, keys, values, begin, end)
            gpgpusim.run_all(root, [root, HERE, Path(self.tmp.name).resolve()])
            for (name, i), (c, keys, values, begin, end) in runs.items():
                with self.subTest(c.label):
                    got, error = ours[name].outcome(ours[name].work/f'c{i}', c)
                    self.assertIsNone(error)
                    check(self, got['keys_out'], got['values_out'], keys, values, begin, end)
                    if keys.itemsize == 8 and len(keys) > CUB_SIMULATED:
                        continue  # NVIDIA's 64-bit sort of several tiles goes wrong in GPGPU-Sim (gpgpusim/README.md, Open)
                    folder = root/f'p_cub_{name}'/f'c{i}'
                    status = (folder/'status').read_text().strip()
                    self.assertEqual(status, '0', (folder/'run.log').read_text()[-2000:])
                    want_keys = np.fromfile(folder/'keys_out.bin', dtype=keys.dtype)
                    want_values = np.fromfile(folder/'values_out.bin', dtype=values.dtype)
                    np.testing.assert_array_equal(got['keys_out'].view(keys.dtype), want_keys)  # NVIDIA's CUB, bit for bit
                    np.testing.assert_array_equal(got['values_out'].view(values.dtype), want_values)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
