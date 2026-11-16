"""erf, expm1 and log1p (IR version 8): CUDA's erff, expm1f and log1pf, and qpel's own Metal versions
(backends/metal.h, backends/math_fit.py; Metal has none), each within MATH_ULPS of the exact result
on the CPU simulator, the Metal-on-CPU runner and the Apple GPU."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program  # noqa: E402
from qpel_types import MATH_ULPS  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

TEXT = (HERE/'examples/math_v8.qpel').read_text()


def oracle():
    spec = importlib.util.spec_from_file_location('run_gpu_tests', HERE/'gpu_tests/run_gpu_tests.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Rules(unittest.TestCase):
    def test_version_8(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 8)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=7, semantics='qpel-portable-core-v7')
        with self.assertRaisesRegex(ValueError, 'IR version 7 cannot use: math.erf, math.expm1, math.log1p'):
            validate(program)

    def test_metal_header_holds_the_fitted_code(self):
        spec = importlib.util.spec_from_file_location('math_fit', HERE/'backends/math_fit.py')
        math_fit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(math_fit)
        self.assertIn(math_fit.metal_source(), (HERE/'backends/metal.h').read_text())

    def test_the_importer_reads_them(self):
        from frontends.c_import import import_source
        if not shutil.which('clang'):
            self.skipTest('clang required')
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text('__global__ void k(const float* x, float* y, unsigned int n) {\n'
                            '  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;\n'
                            '  if (i < n) y[i] = erff(x[i]) + expm1f(x[i]) + log1pf(x[i]);\n}\n')
            program, _ = import_source(path)
        self.assertEqual(sorted(n['attributes']['function'] for n in program['nodes'] if n['kind'] == 'Math'),
                         ['erf', 'expm1', 'log1p'])


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-math8-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())
        cls.gpu = oracle()
        cls.inputs = cls.gpu.math_v8_inputs(1 << 16)
        cls.want = cls.gpu.math_v8_reference(cls.inputs)
        cls.np = np

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def case(self):
        from differential.simulate import Case
        return Case({'n': (self.inputs.size, 1, 1)}, {'x': self.inputs}, {}, 'math_v8')

    def check(self, got):
        for key, want in zip(('e', 'm', 'l'), self.want):
            worst, i = self.gpu.math_v8_worst(got[key], want)
            self.assertLessEqual(worst, MATH_ULPS, f'{key}: {worst} ulp at x = {self.inputs[i]!r}')

    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        self.check(Program(self.generated/'math_v8.cu', self.plan, Path(self.work.name)/'sim').run(self.case()))

    def test_metal_on_the_cpu(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from differential import metal_cpu
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        source = self.generated/'math_v8.metal'
        _, report = import_source(source, plan=self.plan, module='math_v8')
        runner = metal_cpu.program(source, self.plan, report, Path(self.work.name)/'metal_cpu', timeout=120)
        self.check(runner.run(self.case()))

    def test_metal_source_imports_back(self):
        from frontends.c_import import import_source
        from frontends import clang_ast, roundtrip
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        for source in ('math_v8.cu', 'math_v8.metal'):
            program, _ = import_source(self.generated/source, plan=self.plan, module='math_v8')
            self.assertIsNone(roundtrip.difference(parse_program(TEXT), program), source)


if __name__ == '__main__':
    unittest.main()
