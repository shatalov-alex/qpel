"""qpel's device-wide scan (frontends/include/libraries/qpel_library/device_scan.cuh, frontends/library.py):
cub::DeviceScan::InclusiveSum and ExclusiveSum on 32- and 64-bit integers, as three qpel kernels.
The library's CUDA runs as the original on the CPU simulator, qpel's CUDA there, qpel's Metal on the
Apple GPU, all against numpy's wrapping cumsum; with QPEL_NVIDIA=1, NVIDIA's own CUB on GPGPU-Sim
and qpel's CUDA there, bit for bit."""
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
# NVIDIA's DeviceScan runs right in GPGPU-Sim on 32-bit integers within one of its tiles; across tiles its
# decoupled look-back gives wrong prefixes there or crashes, and its 64-bit scan is wrong from the 15th element
# (gpgpusim/README.md, Open). So GPGPU-Sim compares qpel with NVIDIA's CUB there and with the oracle at every size
# and type; the T4 (differential/hardware.py) compares with NVIDIA's CUB at every size.
CUB_SIMULATED = 300
SIZES = (1, 255, 1023, 1024, 1025, 4100, 300000)  # 300000: 293 tiles, so the partials are scanned 256 at a time
# (sum type, exclusive, input type): torch's cumsum of gsplat's int32 counts gives int64
VARIANTS = [(e, x, e) for e in ('u32', 'i32', 'u64', 'i64') for x in (False, True)] + [('i64', False, 'i32')]
NUMPY = {'u32': 'uint32', 'i32': 'int32', 'u64': 'uint64', 'i64': 'int64'}


def values(element, n):
    import numpy as np
    rng = np.random.default_rng(n)
    dtype = np.dtype(NUMPY[element])
    info = np.iinfo(dtype)
    return rng.integers(info.min, info.max, n, dtype=dtype, endpoint=True)  # sums wrap


def oracle(x, exclusive, element=None):
    import numpy as np
    dtype = np.dtype(NUMPY[element]) if element else x.dtype
    inclusive = np.cumsum(x, dtype=dtype)  # (numpy's integer cumsum wraps, as qpel and CUB do)
    return np.concatenate([np.zeros(1, dtype), inclusive[:-1]]) if exclusive else inclusive


def case(element, n):
    from differential.simulate import Case
    x = values(element, n)
    return Case({'n': (n, 1, 1)}, {'in': x}, {}, f'{element} n={n}'), x


class Built:
    """One variant imported and built."""

    def __init__(self, element, exclusive, source, work):
        from frontends.c_import import import_source
        from frontends.library import device_scan
        from qpel import compile_program
        from qpel_ir import validate
        self.element, self.exclusive, self.input, self.work = element, exclusive, source, work
        self.source_text, self.plan = device_scan(element, exclusive, source)
        self.source = work/(self.plan['module'] + '.cu')
        self.source.write_text(self.source_text)
        self.program, self.report = import_source(self.source, plan=self.plan)
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.generated = work/'build/package/generated'
        self.cuda = self.generated/(self.plan['module'] + '.cu')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class DeviceScan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-device-scan-')
        cls.built = {}
        for element, exclusive, source in VARIANTS:
            work = Path(cls.tmp.name)/f'{element}_{int(exclusive)}_{source}'
            work.mkdir()
            cls.built[element, exclusive, source] = Built(element, exclusive, source, work)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_imported(self):
        from qpel_text import print_program
        b = self.built['u32', False, 'u32']
        self.assertEqual(b.program['version'], 21)  # partials: one element per tile
        text = print_program(b.program)
        self.assertIn('scratch partials: buffer<u32, write> in n / 1024;', text)
        for kernel in ('qlib_scan_reduce', 'qlib_scan_partials', 'qlib_scan_tiles'):
            self.assertIn(f'kernel {kernel}(', text)

    def test_floats_are_refused(self):
        from frontends.library import device_scan
        with self.assertRaisesRegex(ValueError, 'integers'):
            device_scan('f32')

    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        for (element, exclusive, source), b in self.built.items():
            for folder in ('o', 'q'):
                (b.work/folder).mkdir(exist_ok=True)
            original = Program(b.source, b.plan, b.work/'o', original=b.report)
            ours = Program(b.cuda, b.plan, b.work/'q')
            for n in SIZES[:-1] if element in ('u64', 'i64') else SIZES:
                with self.subTest(element=element, exclusive=exclusive, n=n):
                    c, x = case(source, n)
                    want = oracle(x, exclusive, element)
                    np.testing.assert_array_equal(original.run(c)['out'].view(want.dtype), want)
                    np.testing.assert_array_equal(ours.run(c)['out'].view(want.dtype), want)

    def test_apple_gpu(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for (element, exclusive, source), b in self.built.items():
            host = matrix.load_host(b.generated)
            for n in SIZES:
                with self.subTest(element=element, exclusive=exclusive, n=n):
                    c, x = case(source, n)
                    want = oracle(x, exclusive, element)
                    got = np.asarray(metal.run_generated(host, b.plan, c)['out']).view(want.dtype)
                    np.testing.assert_array_equal(got, want)

    @unittest.skipUnless(NVIDIA, 'GPGPU-Sim rows are slow; set QPEL_NVIDIA=1')
    def test_gpgpusim_against_nvidia_cub(self):
        import numpy as np
        from differential import gpgpusim
        from frontends.library import cub_scan_main
        if not gpgpusim.image_ready(build=False):
            unavailable(self, 'nvidia', 'no GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-device-scan-nvidia-')).resolve()
        try:
            ours, cases = {}, {}
            sizes = (1, 300, 1025, 4100, 300000)
            for (element, exclusive, source), b in self.built.items():
                if source != element:
                    continue  # CUB sums an int input as int (its input type); torch's cumsum, which gsplat calls, as int64
                key = f'{element}_{int(exclusive)}'
                cub = root/f'p_cub_{key}'
                cub.mkdir()
                (cub/'main.cu').write_text(cub_scan_main(element, exclusive))
                (cub/'nvcc.args').write_text('\n')
                ours[key] = gpgpusim.GpuProgram(b.cuda, b.plan, root/f'p_qpel_{key}')
                for i, n in enumerate(sizes):
                    c, x = case(element, n)
                    (cub/f'c{i}').mkdir()
                    x.tofile(cub/f'c{i}'/'in.bin')
                    (ours[key].work/f'c{i}').mkdir()
                    ours[key].write_case(c, ours[key].work/f'c{i}')
                    cases[key, i] = (c, x, exclusive)
            gpgpusim.run_all(root, [root, HERE, Path(self.tmp.name).resolve()])
            for (key, i), (c, x, exclusive) in cases.items():
                with self.subTest(variant=key, n=len(x)):
                    got, error = ours[key].outcome(ours[key].work/f'c{i}', c)
                    self.assertIsNone(error)
                    np.testing.assert_array_equal(got['out'].view(x.dtype), oracle(x, exclusive))
                    if len(x) > CUB_SIMULATED or x.itemsize == 8:
                        continue  # where NVIDIA's CUB goes wrong in GPGPU-Sim (above)
                    status = (root/f'p_cub_{key}'/f'c{i}'/'status').read_text().strip()
                    self.assertEqual(status, '0', (root/f'p_cub_{key}'/f'c{i}'/'run.log').read_text()[-2000:])
                    want = np.fromfile(root/f'p_cub_{key}'/f'c{i}'/'out.bin', dtype=x.dtype)
                    np.testing.assert_array_equal(got['out'].view(x.dtype), want)  # NVIDIA's CUB, bit for bit
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
