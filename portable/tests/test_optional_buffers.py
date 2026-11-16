"""Optional buffers (IR version 9): a kernel buffer parameter the caller may leave out, and
qpel.present(x), as PyTorch extensions test optional tensors (`if (bias != nullptr)`). CUDA passes a
null pointer; Metal, which cannot ask whether a buffer is bound, a presence flag after the kernel's
parameters. Every combination of given and left-out buffers runs on the CPU simulator, the
Metal-on-CPU runner and the Apple GPU against an oracle; the CUDA importer reads the null tests."""
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
from qpel_test_support import unavailable  # noqa: E402

TEXT = (HERE/'examples/optional_bias.qpel').read_text()
BODY = TEXT[TEXT.index('module'):]


def oracle(case):
    import numpy as np
    x = case.inputs['x']
    y = x * np.float32(case.values['scale']) + (case.inputs['bias'] if 'bias' in case.inputs else np.float32(0))
    out = {'y': y.astype(np.float32)}
    if 'where' not in case.omit:
        out['where'] = np.arange(x.size, dtype=np.uint32)
    return out


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(9)
    out = []
    for n in (1, 70, 300):
        x, b = rng.standard_normal(n).astype(np.float32), rng.standard_normal(n).astype(np.float32)
        for bias in (True, False):
            for where in (True, False):
                out.append(Case({'n': (n, 1, 1)}, {'x': x} | ({'bias': b} if bias else {}), {'scale': -1.5},
                                f'n={n} bias={bias} where={where}', omit=() if where else ('where',)))
    return out


class Rules(unittest.TestCase):
    def test_version_9_and_text(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 9)
        self.assertEqual(print_program(program), BODY)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=8, semantics='qpel-portable-core-v8')
        with self.assertRaisesRegex(ValueError, 'IR version 8 cannot use: optional buffer, qpel.present'):
            validate(program)

    def reject(self, old, new, code):
        self.assertIn(old, TEXT)
        with self.assertRaisesRegex(ValueError, code):
            validate(parse_program(TEXT.replace(old, new)))

    def test_present_needs_an_optional_parameter(self):
        self.reject('qpel.present(bias)', 'qpel.present(x)', 'PRESENT_OPTIONAL')

    def test_optional_parameters_bind_optional_resources(self):
        self.reject('  optional input bias:', '  input bias:', 'OPTIONAL_BINDING')
        self.reject('optional bias: buffer<f32, read> @1', 'bias: buffer<f32, read> @1', 'OPTIONAL_BINDING|PRESENT_OPTIONAL')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-optional-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def check(self, got, case):
        import numpy as np
        want = oracle(case)
        self.assertEqual('where' in got, 'where' in want, case.label)  # an omitted output is not returned
        for name, value in want.items():
            np.testing.assert_array_equal(np.asarray(got[name]), value, err_msg=f'{name} ({case.label})')

    def test_spellings(self):
        cuda = (self.generated/'optional_bias.cu').read_text()
        metal = (self.generated/'optional_bias.metal').read_text()
        self.assertIn('QPEL_OPTIONAL_PARAM(QPEL_READ_F32, bias, 1, 6)', cuda)
        self.assertIn('#define QPEL_PRESENT(name) ((name) != nullptr)', cuda)
        self.assertIn('constant uint& qpel_present_##name [[buffer(presence)]]', metal)

    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'optional_bias.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases():
            self.check(program.run(case), case)

    def test_metal_on_the_cpu(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from differential import metal_cpu
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        source = self.generated/'optional_bias.metal'
        _, report = import_source(source, plan=self.plan, module='optional_bias')
        runner = metal_cpu.program(source, self.plan, report, Path(self.work.name)/'metal_cpu', timeout=60)
        for case in cases():
            self.check(runner.run(case), case)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix
        host = matrix.load_host(self.generated)
        for case in cases():
            inputs = {k: torch.from_numpy(v).to('mps') for k, v in case.inputs.items()}
            got = host.run(inputs, case.shapes, case.values, omit=tuple(case.omit))
            self.check({k: v.cpu().numpy() for k, v in got.items()}, case)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Importer(unittest.TestCase):
    SOURCE = '''
__global__ void pooled(const float* x, const float* bias, float* y, int* argmax, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) {
    float v = x[i];
    if (bias != nullptr) v += bias[i];
    if (!(argmax == NULL)) argmax[i] = (int)i;
    y[i] = v;
  }
}
'''

    def test_null_tests_are_presence_tests(self):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text(self.SOURCE)
            program, _ = import_source(path)
        validate(program)
        optional = sorted(n['attributes']['name'] for n in program['nodes'] if n['kind'] == 'Parameter'
                          and n['attributes'].get('optional'))
        self.assertEqual(optional, ['argmax', 'bias'])
        resources = sorted(n['attributes']['name'] for n in program['nodes'] if n['kind'] == 'Resource'
                           and n['attributes'].get('optional'))
        self.assertEqual(resources, ['argmax', 'bias'])
        self.assertEqual(sum(n['kind'] == 'BufferPresent' for n in program['nodes']), 2)
        self.assertEqual(program['version'], 9)

    def test_a_pointer_as_a_condition(self):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text(self.SOURCE.replace('if (bias != nullptr)', 'if (bias)'))
            program, _ = import_source(path)
        self.assertEqual(sum(n['kind'] == 'BufferPresent' for n in program['nodes']), 2)


if __name__ == '__main__':
    unittest.main()
