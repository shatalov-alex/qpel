"""math.fast_exp (IR version 20): e^x within 3 + floor(2|x|) ulp (qpel_types.fast_exp_ulps; CUDA __expf, Metal
fast::exp). examples/math_fast_exp.qpel on the CPU simulator, GPGPU-Sim and the Apple GPU against float64, each
result within the bound at its own argument; CUDA's __expf imports as it and runs against its original; qpel's
Metal reads back as it."""
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
from qpel_types import fast_exp_ulps, MATH_BOUNDS, FAST_EXP_ULPS  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

TEXT = (HERE/'examples/math_fast_exp.qpel').read_text()


def inputs(seed=20, n=8192):
    import numpy as np
    rng = np.random.default_rng(seed)
    return np.concatenate([[0.0, -0.0, 1.0, -1.0, 0.5, 88.0, -87.0], rng.uniform(-1, 1, n // 4),
                           rng.uniform(-8, 8, n // 4), rng.uniform(-87, 88, n // 2)]).astype(np.float32)


def check(test, got, x):
    """Every normal result within 3 + floor(2|x|) ulp of float64's e^x; the worst ratio to the bound."""
    import numpy as np
    want = np.exp(x.astype(np.float64))
    got = np.asarray(got, np.float64)[:len(x)]
    normal = (want > 1.1754944e-38) & (want < 3.4e38)
    ulp = np.spacing(want[normal].astype(np.float32)).astype(np.float64)
    error = np.abs(got[normal] - want[normal]) / ulp
    bound = np.array([fast_exp_ulps(v) for v in x[normal]])
    test.assertTrue((error <= bound).all(), f'worst {float(np.max(error - bound)):.2f} ulp over the bound')
    small = np.abs(x[normal]) <= 8
    test.assertLessEqual(float(error[small].max()), FAST_EXP_ULPS)  # the one-number bound tolerances use
    return float(np.max(error / bound))


class Rules(unittest.TestCase):
    def test_version_20(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 20)
        self.assertEqual(print_program(program), TEXT[TEXT.index('module'):])
        program.update(version=19, semantics='qpel-portable-core-v19')
        with self.assertRaisesRegex(ValueError, 'IR version 19 cannot use: math.fast_exp'):
            validate(program)

    def test_bound(self):
        self.assertEqual(MATH_BOUNDS['fast_exp'], fast_exp_ulps(8))
        self.assertEqual([fast_exp_ulps(v) for v in (0, -0.5, 1, -84.6)], [3, 4, 5, 172])


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-fast-exp-')
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
        return Case({'n': (len(x), 1, 1)}, {'x': x}, {}, 'fast_exp'), x

    def test_generated(self):
        self.assertIn('QPEL_MATH_FAST_EXP(', (self.generated/'math_fast_exp.cu').read_text())

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        case, x = self.case()
        check(self, Program(self.generated/'math_fast_exp.cu', self.plan, Path(self.work.name)/'sim').run(case)['z'], x)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        case, x = self.case()
        ratio = check(self, metal.run_generated(matrix.load_host(self.generated), self.plan, case)['z'], x)
        self.assertGreater(ratio, 0.05)  # Metal's fast::exp, not precise::exp (1.5 ulp everywhere)

    def test_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-fast-exp-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'math_fast_exp.cu', self.plan, root/'qpel')
            case, x = self.case()
            folder = program.work/'c000'
            folder.mkdir()
            program.write_case(case, folder)
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            got, error = program.outcome(folder, case)
            self.assertIsNone(error)
            check(self, got['z'], x)
        finally:
            shutil.rmtree(root, ignore_errors=True)


CUDA = r'''#include <cuda_runtime.h>
__global__ void falloff(const float *x, float *z, unsigned n) {
  const unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) z[i] = __expf(-0.5f * x[i] * x[i]) + expf(x[i]);
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
            program, report = import_source(source, kernels=['falloff'], module='falloff')
            validate(program)
            text = print_program(program)
            self.assertIn('math.fast_exp(', text)
            self.assertIn('math.exp(', text)  # expf stays the bounded exp
            self.assertEqual(program['version'], 20)
            self.assertFalse(any('__expf' in a for a in report['adaptations']), report['adaptations'])
            (work/'p.json').write_text(json.dumps(program))
            compile_program(work/'p.json', work/'build')
            generated = work/'build/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'o').mkdir()
            (work/'q').mkdir()
            x = np.random.default_rng(21).uniform(-6, 6, 700).astype(np.float32)
            shape = next(r['name'] for r in plan['resources'] if r['kind'] == 'shape')
            case = Case({shape: (len(x), 1, 1)}, {'x': x}, {}, 'fast_exp')
            want = Program(source, plan, work/'o', original=report).run(case)['z'][:len(x)]
            got = Program(generated/'falloff.cu', plan, work/'q').run(case)['z'][:len(x)]
            np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32))  # (both the host's expf)
            metal = (generated/'falloff.metal').read_text()
            self.assertIn('QPEL_MATH_FAST_EXP(', metal)

    @unittest.skipUnless(shutil.which('xcrun'), 'the Metal toolchain required')
    def test_metal_reads_back(self):
        from frontends.c_import import import_source
        from frontends.clang_ast import metal_available
        if not metal_available():
            unavailable(self, 'metal', 'no Metal toolchain')
        source = '#define QPEL_MATH_ENABLED 1\n' + (HERE/'backends/metal.h').read_text() + r'''
kernel void back(device const float *x [[buffer(0)]], device float *z [[buffer(1)]], constant uint &n [[buffer(2)]],
                 uint i [[thread_position_in_grid]]) {
  if (i < n) z[i] = qpel_fast_exp(x[i]);
}
'''
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.metal'
            path.write_text(source)
            program, _ = import_source(path, kernels=['back'], module='back')
            self.assertIn('math.fast_exp(', print_program(program))


if __name__ == '__main__':
    unittest.main()
