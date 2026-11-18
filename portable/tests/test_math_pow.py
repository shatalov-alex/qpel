"""math.pow (IR version 17): C's powf, a bounded function with its own bound (qpel_types.MATH_BOUNDS, 9 ulp:
CUDA's documented powf bound; Metal's precise::pow measured 1.5). examples/math_pow.qpel on the CPU
simulator (the host's powf), GPGPU-Sim and the Apple GPU against float64, special cases exactly; CUDA's
powf imports and runs against its original."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_types import MATH_BOUNDS  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

TEXT = (HERE/'examples/math_pow.qpel').read_text()
SPECIAL = [(-2, 3), (-2, 2), (-2, 0.5), (-0.0, -1), (0.0, -1), (-1, float('inf')), (1, float('nan')), (float('inf'), -2),
           (float('-inf'), 3), (float('nan'), 0), (2, float('inf')), (0, 0), (0.5, 0.25), (10, -3)]


def inputs(seed=17, n=4096):
    import numpy as np
    rng = np.random.default_rng(seed)
    x = np.concatenate([[a for a, _ in SPECIAL], rng.uniform(0, 4, n // 2), np.exp(rng.uniform(-30, 30, n // 2))]).astype(np.float32)
    y = np.concatenate([[b for _, b in SPECIAL], rng.uniform(-20, 20, n // 2), rng.uniform(-2, 2, n // 2)]).astype(np.float32)
    return x, y


def check(test, got, x, y):
    """Special cases as C's pow gives them; normal results within MATH_BOUNDS['pow'] ulp of float64."""
    import numpy as np
    with np.errstate(all='ignore'):
        want = np.power(x.astype(np.float64), y.astype(np.float64))
    got = np.asarray(got, np.float64)[:len(x)]
    k = len(SPECIAL)
    np.testing.assert_array_equal(got[:k], want[:k].astype(np.float32).astype(np.float64))
    normal = np.isfinite(want) & (np.abs(want) > 1.1754944e-38) & (np.abs(want) < 3.4e38)
    normal[:k] = False
    ulp = np.abs(np.spacing(want[normal].astype(np.float32)).astype(np.float64))
    worst = float(np.max(np.abs(got[normal] - want[normal]) / ulp))
    test.assertLessEqual(worst, MATH_BOUNDS['pow'], f'worst {worst:.2f} ulp')
    return worst


class Rules(unittest.TestCase):
    def test_version_17(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 17)
        self.assertEqual(print_program(program), TEXT[TEXT.index('module'):])
        program.update(version=16, semantics='qpel-portable-core-v16')
        with self.assertRaisesRegex(ValueError, 'IR version 16 cannot use: math.pow'):
            validate(program)


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-math-pow-')
        work = Path(cls.work.name)
        (work/'p.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def case(self):
        from differential.simulate import Case
        x, y = inputs()
        return Case({'n': (len(x), 1, 1)}, {'x': x, 'y': y}, {}, 'pow'), x, y

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        case, x, y = self.case()
        check(self, Program(self.generated/'math_pow.cu', self.plan, Path(self.work.name)/'sim').run(case)['z'], x, y)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        case, x, y = self.case()
        check(self, metal.run_generated(matrix.load_host(self.generated), self.plan, case)['z'], x, y)

    def test_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-math-pow-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'math_pow.cu', self.plan, root/'qpel')
            case, x, y = self.case()
            folder = program.work/'c000'
            folder.mkdir()
            program.write_case(case, folder)
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            got, error = program.outcome(folder, case)
            self.assertIsNone(error)
            check(self, got['z'], x, y)
        finally:
            shutil.rmtree(root, ignore_errors=True)


CUDA = r'''#include <cuda_runtime.h>
__global__ void power(const float *x, const float *y, float *z, unsigned n) {
  const unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) z[i] = powf(x[i], y[i]);
}
'''


@unittest.skipUnless(shutil.which('clang') and shutil.which('clang++'), 'clang and clang++ required')
class Import(unittest.TestCase):
    def test_against_the_original(self):
        import numpy as np
        from frontends.c_import import import_source
        from qpel import compile_program
        from differential.simulate import Program, Case
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            source = work/'k.cu'
            source.write_text(CUDA)
            program, report = import_source(source, kernels=['power'], module='power')
            validate(program)
            self.assertIn('math.pow(', print_program(program))
            (work/'p.json').write_text(json.dumps(program))
            compile_program(work/'p.json', work/'build')
            generated = work/'build/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'o').mkdir()
            (work/'q').mkdir()
            x, y = inputs(18, 512)
            shape = next(r['name'] for r in plan['resources'] if r['kind'] == 'shape')
            case = Case({shape: (len(x), 1, 1)}, {'x': x, 'y': y}, {}, 'pow')
            want = Program(source, plan, work/'o', original=report).run(case)['z'][:len(x)]
            got = Program(generated/'power.cu', plan, work/'q').run(case)['z'][:len(x)]
            np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32))  # (both the host's powf)


if __name__ == '__main__':
    unittest.main()
