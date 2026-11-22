"""Buffers of a count per tile of their extent (IR version 21): `in n / 256 * 2` holds
ceil(volume(n) / 256) * 2 elements, the scratch of device-wide scans and sorts (LIBRARIES.md)."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_ir import Lowering, validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

EXAMPLE = HERE/'examples/buffer_tiles.qpel'
SIZES = (1, 255, 256, 257, 1000, 4096)


def oracle(x):
    import numpy as np
    tiles = -(-len(x) // 256)
    out = np.zeros(2 * tiles, np.uint32)
    out[0::2] = x[0::256]
    out[1::2] = np.arange(0, len(x), 256, dtype=np.uint32)
    return out


def case(n):
    import numpy as np
    from differential.simulate import Case
    x = np.random.default_rng(n).integers(0, 2**32, n, dtype=np.uint32)
    return Case({'n': (n, 1, 1)}, {'x': x}, {}, f'n={n}'), x


class Rules(unittest.TestCase):
    def test_version_and_text(self):
        program = parse_program(EXAMPLE.read_text())
        validate(program)
        self.assertEqual(program['version'], 21)
        self.assertIn('output t: buffer<u32, write> in n / 256 * 2;', print_program(program))
        self.assertEqual(parse_program(print_program(program)), program)
        with self.assertRaisesRegex(ValueError, 'buffer per tile; declare version 21'):
            validate(dict(program, version=20, semantics='qpel-portable-core-v20'))
        for bad in ('in n / 1 * 2;', 'in n / 2097152 * 2;'):
            with self.subTest(bad), self.assertRaisesRegex(ValueError, 'buffer per tile'):
                validate(parse_program(EXAMPLE.read_text().replace('in n / 256 * 2;', bad)))

    def test_lengths(self):
        from qpel_types import buffer_length
        from generate_cuda_host import generate as cuda_host
        from generate_metal_host import generate as metal_host
        from differential import cases
        plan = Lowering(parse_program(EXAMPLE.read_text())).plan()
        t = next(r for r in plan['resources'] if r['name'] == 't')
        self.assertEqual((t['per'], t['scale']), (256, 2))
        self.assertEqual([buffer_length(t, {'n': (n, 1, 1)}) for n in SIZES], [2, 2, 2, 4, 8, 32])
        self.assertEqual(buffer_length(t, {'n': (16, 16, 2)}), 4)  # the volume, 512, in tiles
        for host in (cuda_host(plan, b''), metal_host(plan, b'')):
            self.assertIn("(-(-sizes['n'] // 256)) * 2", host)
        for c in cases.generic(plan, 1):
            self.assertEqual(len(c.inputs['x']), cases.volume(c.shapes['n']))


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-tiles-')
        work = Path(cls.work.name)
        program = parse_program(EXAMPLE.read_text())
        (work/'p.json').write_text(json.dumps(program))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = Lowering(program).plan()

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'buffer_tiles.cu', self.plan, Path(self.work.name)/'sim')
        for n in SIZES:
            c, x = case(n)
            np.testing.assert_array_equal(program.run(c)['t'], oracle(x))

    def test_apple_gpu(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for n in SIZES:
            c, x = case(n)
            np.testing.assert_array_equal(np.asarray(metal.run_generated(host, self.plan, c)['t']).astype(np.uint32), oracle(x))


if __name__ == '__main__':
    unittest.main(verbosity=2)
