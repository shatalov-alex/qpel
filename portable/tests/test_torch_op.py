"""qpel torch-op: PyTorch operations generated from TRELLIS.2's own launch code, run on the Apple GPU."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import unavailable  # noqa: E402

FIXTURES = HERE/'tests/fixtures/qpel_ir'
O_VOXEL = HERE/'corpus/trellis2/o-voxel/src'
INCLUDE = [HERE/'corpus/include']


def expand_bits(v):
    import numpy as np
    v = v.astype(np.uint32)
    for multiplier, mask in ((0x10001, 0xFF0000FF), (0x101, 0x0F00F00F), (0x11, 0xC30C30C3), (0x5, 0x49249249)):
        v = (v * np.uint32(multiplier)) & np.uint32(mask)
    return v


def load(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Packages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        from frontends import torch_op
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-torch-op-')
        cls.ops, cls.skipped = {}, {}
        for fixture, host in (('trellis2_z_order_encode', 'serialize/api.cu'), ('trellis2_z_order_decode', 'serialize/api.cu'),
                              ('trellis2_hashmap_insert_u32_u32', 'hash/hash.cu'),
                              ('trellis2_hashmap_insert_u32_u64', 'hash/hash.cu')):
            out = Path(cls.work.name)/fixture
            compile_program(FIXTURES/fixture/'program.qpel.json', out)
            path, cls.skipped[fixture] = torch_op.write(out/'package', [O_VOXEL/host], INCLUDE)
            cls.ops[fixture] = path

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Generate(Packages):
    def test_signatures_follow_the_launches(self):
        z_order = self.ops['trellis2_z_order_encode'].read_text()
        self.assertIn('def z_order_encode_cuda(x, y, z, codes):', z_order)  # codes: the host function's own output
        self.assertIn("shapes = {'N': (x.size(0), 1, 1)}", z_order)
        insert = self.ops['trellis2_hashmap_insert_u32_u32'].read_text()
        self.assertIn('def dispatch_hashmap_insert_cuda(hashmap_keys, hashmap_values, keys, values):', insert)
        self.assertIn("_store(hashmap_keys, 'hashmap_keys', out['hashmap_keys'])", insert)  # in place, like the launch
        self.assertEqual(self.skipped['trellis2_z_order_encode'], {})

    def test_launches_it_cannot_follow_are_reported(self):
        from frontends import torch_op
        source = (O_VOXEL/'serialize/api.cu').read_text()
        launch = source.index('CUDA::z_order_encode<<<')
        source = source[:launch] + source[launch:].replace('        x.size(0),\n', '        x.size(0) / 2,\n', 1)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'api.cu'
            path.write_text(source)
            shutil.copytree(O_VOXEL/'serialize', Path(d), dirs_exist_ok=True, ignore=shutil.ignore_patterns('api.cu'))
            with self.assertRaisesRegex(ValueError, 'argument for N is neither'):
                torch_op.generate(self.ops['trellis2_z_order_encode'].parents[1], [path], INCLUDE)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class AppleGpu(Packages):
    def setUp(self):
        try:
            import numpy  # noqa: F401
            import torch
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch and NumPy required for the Apple GPU tests')
        if not torch.backends.mps.is_available():
            unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')

    def test_z_order_encode(self):
        import numpy as np
        import torch
        ops = load(self.ops['trellis2_z_order_encode'], 'z_order_ops')
        rng = np.random.default_rng(3)
        for n in (1, 63, 64, 1000, 100_003):
            x, y, z = (rng.integers(0, 1024, n, dtype=np.int32) for _ in range(3))
            codes = torch.empty(n, dtype=torch.int32, device='mps')
            wide = torch.from_numpy(np.stack([x, y], 1)).to('mps')  # x as a strided view, as .contiguous() allows
            ops.z_order_encode_cuda(wide[:, 0], torch.from_numpy(y).to('mps'), torch.from_numpy(z).to('mps'), codes)
            want = expand_bits(x) * np.uint32(4) + expand_bits(y) * np.uint32(2) + expand_bits(z)
            np.testing.assert_array_equal(codes.cpu().numpy().view(np.uint32), want, f'{n} points')

    def test_z_order_round_trip(self):  # decode's outputs are the host function's own tensors
        import torch
        encode = load(self.ops['trellis2_z_order_encode'], 'z_order_ops').z_order_encode_cuda
        decode = load(self.ops['trellis2_z_order_decode'], 'z_order_decode_ops').z_order_decode_cuda
        x, y, z = (torch.randint(0, 1024, (5000,), dtype=torch.int32, device='mps') for _ in range(3))
        codes = torch.empty_like(x)
        encode(x, y, z, codes)
        back = [torch.empty_like(x) for _ in range(3)]
        decode(codes, *back)
        for got, want in zip(back, (x, y, z)):
            self.assertTrue(torch.equal(got, want))

    def test_wrong_tensors_are_refused(self):
        import torch
        ops = load(self.ops['trellis2_z_order_encode'], 'z_order_ops')
        x = torch.zeros(8, dtype=torch.int32, device='mps')
        with self.assertRaisesRegex(ValueError, 'mps device'):
            ops.z_order_encode_cuda(x.cpu(), x, x, x.clone())
        with self.assertRaisesRegex(ValueError, '8-byte elements'):
            ops.z_order_encode_cuda(x.long(), x, x, x.clone())
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            ops.z_order_encode_cuda(x, x, x, torch.zeros(16, dtype=torch.int32, device='mps')[::2])
        with self.assertRaises(ValueError):  # shorter than x: the host checks every buffer's length
            ops.z_order_encode_cuda(x, x[:4], x, x.clone())

    def test_hash_insert_updates_the_table_in_place(self):
        import numpy as np
        import torch
        from differential.cases import murmur_slot
        from test_cas_contention import check_table
        rng = np.random.default_rng(11)
        for fixture, value_type in (('trellis2_hashmap_insert_u32_u32', np.int32), ('trellis2_hashmap_insert_u32_u64', np.int64)):
            ops = load(self.ops[fixture], fixture + '_ops')
            slots, count = 1024, 700
            keys = rng.choice(2**31, count, replace=False).astype(np.int32)
            values = rng.integers(0, 2**31, count).astype(value_type)
            table_keys = torch.full((slots,), -1, dtype=torch.int32, device='mps')  # 0xffffffff: empty
            table_values = torch.zeros(slots, dtype=torch.from_numpy(values).dtype, device='mps')
            ops.dispatch_hashmap_insert_cuda(table_keys, table_values, torch.from_numpy(keys).to('mps'),
                                             torch.from_numpy(values).to('mps'))
            error = check_table(keys.view(np.uint32), values.view(np.uint32 if value_type == np.int32 else np.uint64),
                                table_keys.cpu().numpy().view(np.uint32), table_values.cpu().numpy().view(
                                    np.uint32 if value_type == np.int32 else np.uint64), murmur_slot, 32, 2**32 - 1)
            self.assertIsNone(error, fixture)


if __name__ == '__main__':
    unittest.main(verbosity=2)
