"""math.atan2 (IR version 22): C's atan2f, a bounded function within MATH_ULPS (CUDA documents atan2f within 3 ulp;
Metal's precise::atan2 is measured here). examples/math_atan2.qpel on the CPU simulator (the host's atan2f),
GPGPU-Sim and the Apple GPU against float64, special cases exactly; CUDA's atan2f imports and runs against its
original (gsplat's fisheye projection reaches it through glm::atan)."""
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
from qpel_types import MATH_ULPS  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

TEXT = (HERE/'examples/math_atan2.qpel').read_text()
SPECIAL = [(0.0, 0.0), (-0.0, 0.0), (0.0, -0.0), (-0.0, -0.0), (0.0, -1), (-0.0, -1), (0.0, 1), (-0.0, 1), (-1, 0.0),
           (1, -0.0), (1, -float('inf')), (-1, -float('inf')), (1, float('inf')), (-1, float('inf')), (float('inf'), 2), (-float('inf'), -2), (float('inf'), -float('inf')),
           (-float('inf'), -float('inf')), (float('inf'), float('inf')), (-float('inf'), float('inf')), (float('nan'), 1), (1, float('nan')), (1e-30, 1e30), (1e30, 1e-30)]


def inputs(seed=17, n=4096):
    import numpy as np
    rng = np.random.default_rng(seed)
    magnitude = lambda k: np.exp(rng.uniform(-40, 40, k)) * rng.choice([-1, 1], k)
    y = np.concatenate([[a for a, _ in SPECIAL], rng.uniform(-4, 4, n // 2), magnitude(n // 2)]).astype(np.float32)
    x = np.concatenate([[b for _, b in SPECIAL], rng.uniform(-4, 4, n // 2), magnitude(n // 2)]).astype(np.float32)
    return y, x


def check(test, got, y, x):
    """Zeros and NaN as C's atan2 gives them, signs included; every other result within MATH_ULPS of float64."""
    import numpy as np
    with np.errstate(all='ignore'):
        want = np.arctan2(y.astype(np.float64), x.astype(np.float64))
    got = np.asarray(got, np.float64)[:len(x)]
    k = len(SPECIAL)
    exact = (want[:k] == 0) | np.isnan(want[:k])  # zeros of either sign and NaN exactly; the angles (+-pi, +-pi/2, ...)
    np.testing.assert_array_equal(got[:k][exact], want[:k][exact].astype(np.float32).astype(np.float64))  # within the bound:
    np.testing.assert_array_equal(np.signbit(got[:k]), np.signbit(want[:k]))  # macOS's atan2f(0, -1) is pi rounded down
    near = ~exact
    spacing = np.abs(np.spacing(want[:k][near].astype(np.float32)).astype(np.float64))
    test.assertLessEqual(float(np.max(np.abs(got[:k][near] - want[:k][near]) / spacing)), MATH_ULPS)
    normal = np.isfinite(want) & (np.abs(want) > 1.1754944e-38) & (np.abs(want) < 3.4e38)
    normal[:k] = False
    ulp = np.abs(np.spacing(want[normal].astype(np.float32)).astype(np.float64))
    worst = float(np.max(np.abs(got[normal] - want[normal]) / ulp))
    test.assertLessEqual(worst, MATH_ULPS, f'worst {worst:.2f} ulp')
    return worst


class Rules(unittest.TestCase):
    def test_version_22(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 22)
        self.assertEqual(print_program(program), TEXT[TEXT.index('module'):])
        program.update(version=21, semantics='qpel-portable-core-v21')
        with self.assertRaisesRegex(ValueError, 'IR version 21 cannot use: math.atan2'):
            validate(program)


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-math-atan2-')
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
        y, x = inputs()
        return Case({'n': (len(x), 1, 1)}, {'y': y, 'x': x}, {}, 'atan2'), y, x

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        case, y, x = self.case()
        check(self, Program(self.generated/'math_atan2.cu', self.plan, Path(self.work.name)/'sim').run(case)['z'], y, x)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        case, y, x = self.case()
        print('Apple GPU atan2: worst', round(check(self, metal.run_generated(matrix.load_host(self.generated), self.plan, case)['z'], y, x), 2), 'ulp')

    def test_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-math-atan2-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'math_atan2.cu', self.plan, root/'qpel')
            case, y, x = self.case()
            folder = program.work/'c000'
            folder.mkdir()
            program.write_case(case, folder)
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            got, error = program.outcome(folder, case)
            self.assertIsNone(error)
            check(self, got['z'], y, x)
        finally:
            shutil.rmtree(root, ignore_errors=True)


CUDA = r'''#include <cuda_runtime.h>
__global__ void angle(const float *y, const float *x, float *z, unsigned n) {
  const unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) z[i] = atan2f(y[i], x[i]);
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
            program, report = import_source(source, kernels=['angle'], module='angle')
            validate(program)
            self.assertIn('math.atan2(', print_program(program))
            (work/'p.json').write_text(json.dumps(program))
            compile_program(work/'p.json', work/'build')
            generated = work/'build/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'o').mkdir()
            (work/'q').mkdir()
            y, x = inputs(18, 512)
            shape = next(r['name'] for r in plan['resources'] if r['kind'] == 'shape')
            case = Case({shape: (len(x), 1, 1)}, {'y': y, 'x': x}, {}, 'atan2')
            want = Program(source, plan, work/'o', original=report).run(case)['z'][:len(x)]
            got = Program(generated/'angle.cu', plan, work/'q').run(case)['z'][:len(x)]
            np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32))  # (both the host's atan2f)


if __name__ == '__main__':
    unittest.main()
