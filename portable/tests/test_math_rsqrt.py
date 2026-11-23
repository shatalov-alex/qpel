"""math.rsqrt (IR version 23): 1 / sqrt(x), a bounded function within MATH_ULPS (CUDA documents rsqrtf within
2 ulp; Metal's precise::rsqrt is measured here). examples/math_rsqrt.qpel on the CPU simulator (the host's
1 / sqrtf), GPGPU-Sim and the Apple GPU against float64, special cases exactly; CUDA's rsqrtf and Metal's rsqrt
import as it (gsplat's spherical harmonics normalise the view direction with rsqrtf)."""
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

TEXT = (HERE/'examples/math_rsqrt.qpel').read_text()
SPECIAL = [0.0, -0.0, float('inf'), -float('inf'), -1.0, -1e-30, float('nan'), 1.0, 4.0, 0.25, 1.1754944e-38, 3.4028235e38]


def inputs(seed=23, n=4096):
    """Normal floats over every binade (Metal may flush subnormal arguments, which qpel leaves unspecified)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    spread = np.exp2(rng.uniform(-126, 127.9, n // 2))
    return np.concatenate([SPECIAL, rng.uniform(0.5, 4, n // 2), spread]).astype(np.float32)


def check(test, got, x, negative_zero=True):
    """Infinities, zeros and NaN as C's 1 / sqrt(x) gives them, signs included; every other result within MATH_ULPS
    of float64. (Without negative_zero, rsqrt(-0) is not compared: GPGPU-Sim gives +infinity, where PTX's
    rsqrt.approx.f32 is -infinity; gpgpusim/README.md, Open.)"""
    import numpy as np
    with np.errstate(all='ignore'):
        want = 1 / np.sqrt(x.astype(np.float64))
    want[(x == 0) & np.signbit(x)] = -np.inf  # 1 / sqrt(-0) = 1 / -0
    got = np.asarray(got, np.float64)[:len(x)]
    exact = ~np.isfinite(want) | (want == 0)
    compared = np.ones(len(x), bool) if negative_zero else ~((x == 0) & np.signbit(x))
    np.testing.assert_array_equal(got[exact & compared], want[exact & compared])
    near = ~exact & compared
    ulp = np.abs(np.spacing(want[near].astype(np.float32)).astype(np.float64))
    worst = float(np.max(np.abs(got[near] - want[near]) / ulp))
    test.assertLessEqual(worst, MATH_ULPS, f'worst {worst:.2f} ulp')
    return worst


class Rules(unittest.TestCase):
    def test_version_23(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 23)
        self.assertEqual(print_program(program), TEXT[TEXT.index('module'):])
        program.update(version=22, semantics='qpel-portable-core-v22')
        with self.assertRaisesRegex(ValueError, 'IR version 22 cannot use: math.rsqrt'):
            validate(program)


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-math-rsqrt-')
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
        x = inputs()
        return Case({'n': (len(x), 1, 1)}, {'x': x}, {}, 'rsqrt'), x

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        case, x = self.case()
        check(self, Program(self.generated/'math_rsqrt.cu', self.plan, Path(self.work.name)/'sim').run(case)['z'], x)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        case, x = self.case()
        print('Apple GPU rsqrt: worst', round(check(self, metal.run_generated(matrix.load_host(self.generated), self.plan, case)['z'], x), 2), 'ulp')

    def test_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-math-rsqrt-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'math_rsqrt.cu', self.plan, root/'qpel')
            case, x = self.case()
            folder = program.work/'c000'
            folder.mkdir()
            program.write_case(case, folder)
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            got, error = program.outcome(folder, case)
            self.assertIsNone(error)
            check(self, got['z'], x, negative_zero=False)
        finally:
            shutil.rmtree(root, ignore_errors=True)


CUDA = r'''#include <cuda_runtime.h>
__global__ void reciprocal_root(const float *x, float *z, unsigned n) {
  const unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) z[i] = rsqrtf(x[i]);
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
            program, report = import_source(source, kernels=['reciprocal_root'], module='reciprocal_root')
            validate(program)
            self.assertIn('math.rsqrt(', print_program(program))
            (work/'p.json').write_text(json.dumps(program))
            compile_program(work/'p.json', work/'build')
            generated = work/'build/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'o').mkdir()
            (work/'q').mkdir()
            x = inputs(24, 512)
            shape = next(r['name'] for r in plan['resources'] if r['kind'] == 'shape')
            case = Case({shape: (len(x), 1, 1)}, {'x': x}, {}, 'rsqrt')
            want = Program(source, plan, work/'o', original=report).run(case)['z'][:len(x)]
            got = Program(generated/'reciprocal_root.cu', plan, work/'q').run(case)['z'][:len(x)]
            np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32))  # (both the host's 1 / sqrtf)


if __name__ == '__main__':
    unittest.main()
