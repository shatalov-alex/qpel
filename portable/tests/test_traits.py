"""What qpel reads before import for kernels written like mamba's selective scan (tests/traits.cu;
frontends/statics.py, frontends/views.py): a traits class's type aliases and static constexpr
members, a struct of pointers as the kernel's argument (flattened into one argument per field it
reads; the original is still called with the struct, rebuilt by the harness), single-row loops and
`T a[1][N]` arrays, and dynamic shared memory carved into views. The unmodified kernel runs as the
original on the CPU simulator (with qpel's CUB header) and, with QPEL_NVIDIA=1, with NVIDIA's CUB
on GPGPU-Sim; qpel's CUDA and Metal must agree with it bit for bit."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_test_support import NVIDIA, unavailable  # noqa: E402
from qpel_types import MATH_ULPS  # noqa: E402

SOURCE = HERE/'tests/traits.cu'
GEOMETRY = {'groups': ['1', '1', '1'], 'block': [32, 1, 1]}
SHARED = {'smem_carry': 1, 'smem_': 1028}  # the carry's floats; the original's bytes (the carry at 1024)
SHAPES = [(1, 1, 1), (100, 1, 1), (128, 1, 1), (300, 1, 1), (1000, 1, 1)]


def imported():
    from frontends.c_import import import_source
    return import_source(SOURCE, module='traits', geometry=GEOMETRY, shared=SHARED)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Import(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program, cls.report = imported()

    def test_valid_on_both_targets(self):
        for target in ('cuda', 'metal'):
            validate(self.program, target)

    def test_the_struct_is_one_argument_per_field_read(self):
        from qpel_text import print_program
        text = print_program(self.program)
        self.assertIn('struct Params_values { seqlen: i32; softplus: bool; row_stride: u64; }', text)
        self.assertIn('kernel running_sums(params: struct Params_values @0, params_x_ptr: buffer<f32, write> @1, '
                      'optional params_bias_ptr: buffer<f32, write> @2, params_out_ptr: buffer<f32, write> @3) groups', text)
        self.assertNotIn('unused_ptr', text)
        self.assertIn('inout params_x_ptr: buffer<f32, write> in size;', text)  # a void * field: read or written
        (argument,) = self.report['arguments']['running_sums']
        self.assertEqual((argument['name'], argument['source'], argument['struct']), ('params', 'struct', 'Params'))
        self.assertEqual(argument['values'], 'Params_values')
        self.assertEqual([(f['field'], f['source']) for f in argument['fields']],
                         [('seqlen', 'member'), ('softplus', 'member'), ('row_stride', 'member'), ('x_ptr', 'parameter'),
                          ('bias_ptr', 'parameter'), ('out_ptr', 'parameter')])

    def test_traits_constants_rows_and_views(self):
        from qpel_text import print_program
        text = print_program(self.program)
        for line in ('let kNItems: i32 = 4;', 'let kIsFloat: bool = true;', 'let vals: array<f32, 4>',
                     'let smem_scan: shared<f32, 2>;', 'let smem_carry: shared<f32, 1>;'):
            self.assertIn(line, text)
        self.assertNotIn('smem_load', text)  # unused once the facades are inlined
        adaptations = '\n'.join(self.report['adaptations'])
        self.assertIn('the views of smem_ became shared memory of their own', adaptations)
        self.assertEqual(self.report['shared'], {'smem_': 1028})  # what the original runs with

    def test_a_carved_block_needs_its_bytes(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        with self.assertRaisesRegex(clang_ast.SourceError, "smem_ is carved into views"):
            import_source(SOURCE, module='traits', geometry=GEOMETRY, shared={'smem_carry': 1})


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Rows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = Path(tempfile.mkdtemp(prefix='qpel-traits-'))
        cls.program, cls.report = imported()
        (cls.work/'program.qpel.json').write_text(json.dumps(cls.program))
        compile_program(cls.work/'program.qpel.json', cls.work/'build')
        cls.generated = cls.work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def cases(self):
        """Each case with the softplus flag off and on, with and without the optional bias."""
        import copy
        from differential import cases
        out = []
        for case in cases.for_program('traits', self.plan, values={'params.seqlen': 'volume'}, shapes=SHAPES):
            for flag in (0, 1):
                for bias in (True, False):
                    variant = copy.deepcopy(case)
                    variant.values['params']['softplus'] = flag
                    if not bias:
                        del variant.inputs['params_bias_ptr']
                    variant.label = f'{case.label} softplus={flag} bias={bias}'
                    out.append(variant)
        return out

    def same(self, expected, actual, label, ulps=0):
        """Bit for bit, or (Metal's exp and log1p, NUMERICS.md) within the behaviour rows' ulp bound."""
        from differential.compare import compare
        problems = compare(expected, actual, ['params_bias_ptr', 'params_out_ptr', 'params_x_ptr'], ulps=ulps)
        self.assertFalse(problems, f'{label}: {problems}')

    def references(self):
        """The original, called with its struct, on the CPU simulator (qpel's CUB header)."""
        from differential.simulate import Program
        folder = self.work/'original'
        folder.mkdir(exist_ok=True)
        original = Program(SOURCE, self.plan, folder, original=self.report)
        return [(case, original.run(case)) for case in self.cases()]

    def test_cases_cover_the_optional_buffer(self):
        self.assertTrue(any('params_bias_ptr' in case.inputs for case in self.cases()))
        self.assertTrue(any('params_bias_ptr' not in case.inputs for case in self.cases()))

    def test_cpu_simulator(self):
        from differential.simulate import Program
        folder = self.work/'qpel'
        folder.mkdir(exist_ok=True)
        ours = Program(self.generated/'traits.cu', self.plan, folder)
        for case, expected in self.references():
            self.same(expected, ours.run(case), case.label)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case, expected in self.references():
            self.same(expected, metal.run_generated(host, self.plan, case), case.label, ulps=2*MATH_ULPS)

    @unittest.skipUnless(NVIDIA, 'GPGPU-Sim rows are slow; set QPEL_NVIDIA=1')
    def test_original_with_nvidia_cub_on_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready():
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-traits-nvidia-')).resolve()
        try:
            built = (GpuProgram(SOURCE, self.plan, root/'original', fmad=False, original=self.report),
                     GpuProgram(self.generated/'traits.cu', self.plan, root/'qpel'))
            pending = []
            for number, (case, expected) in enumerate(self.references()):
                for side in built:
                    folder = side.work/f'c{number:03}'
                    folder.mkdir()
                    side.write_case(case, folder)
                    pending.append((side, case, folder, expected))
            run_all(root, [root, HERE, self.work.resolve()])
            for side, case, folder, expected in pending:
                with self.subTest(f'{side.work.name} {case.label}'):
                    actual, error = side.outcome(folder, case)
                    self.assertIsNone(error)
                    self.same(expected, actual, f'{side.work.name} {case.label}')
        finally:
            shutil.rmtree(root, ignore_errors=True)

if __name__ == '__main__':
    unittest.main()
