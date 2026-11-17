"""Arrays of matrices (IR version 14), as llama.cpp's kernel_mul_mm keeps simdgroup_float8x8 mc[8]:
examples/matrix_blocks.qpel computes a 16 x 16 block of C per subgroup from 2 x 2 accumulators, two
matrices of A and two of B at a time, in f32 and in f16 with f32 accumulation. Its elements are
indexed by values the same for all 32 lanes. It runs on the CPU simulator (bit for bit against the
k-order oracle of test_matrix_tiles), GPGPU-Sim and the Apple GPU (within the bound)."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402
from test_matrix_tiles import gemm  # noqa: E402

TEXT = (HERE/'examples/matrix_blocks.qpel').read_text()
BODY = TEXT[TEXT.index('module'):]
SHAPES = ((16, 16, 8), (32, 48, 40), (64, 32, 24), (48, 16, 128))


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(14)
    out = []
    for n in SHAPES:
        length = n[0] * n[1] * n[2]
        inputs = {'a': rng.uniform(-1, 1, length).astype(np.float32), 'b': rng.uniform(-1, 1, length).astype(np.float32),
                  'a16': rng.uniform(-2, 2, length).astype(np.float16), 'b16': rng.uniform(-2, 2, length).astype(np.float16)}
        out.append(Case({'n': n}, inputs, {}, f'n={n}'))
    return out


def oracle(case, which):
    import numpy as np
    columns, rows, depth = case.shapes['n']
    a, b = (case.inputs['a'], case.inputs['b']) if which == 'c' else (case.inputs['a16'], case.inputs['b16'])
    return gemm(a, b, np.zeros(rows * columns, np.float32), rows, columns, depth)


class Rules(unittest.TestCase):
    def test_version_14_and_text(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 14)
        self.assertEqual(print_program(program), BODY)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=13, semantics='qpel-portable-core-v13')
        with self.assertRaisesRegex(ValueError, 'IR version 13 cannot use: array of matrices'):
            validate(program)

    def reject(self, old, new, code):
        self.assertIn(old, TEXT)
        with self.assertRaisesRegex(ValueError, code):
            validate(parse_program(TEXT.replace(old, new, 1)))

    def test_lanes_index_alike(self):
        # the 32 lanes hold each matrix together: they pick the same one
        self.reject('left[j / 2u]', 'left[qpel.subgroup_lane % 2u]', 'SUBGROUP_UNIFORM')
        self.reject('sums[s], c,', 'sums[qpel.local.x % 4u], c,', 'SUBGROUP_UNIFORM')
        self.reject('right[i] = qpel', 'right[qpel.local.x % 2u] = qpel', 'SUBGROUP_UNIFORM')
        validate(parse_program(TEXT.replace('right[i] = qpel', 'right[qpel.local.x / 64u] = qpel', 1)))  # x / 32k: one per subgroup

    def test_not_a_parameter(self):
        self.reject('module matrix_blocks {\n', 'module matrix_blocks {\n  fn keep(ref m: array<matrix<f32, 8, 8>, 2>) -> void {\n  }\n',
                    'PARAM_TYPE')
        self.reject('module matrix_blocks {\n', 'module matrix_blocks {\n  fn keep(m: matrix<f32, 8, 8>) -> void {\n  }\n',
                    'PARAM_TYPE')

    def test_element_types(self):
        self.reject('sums[j] = qpel.matrix_mma(left[j / 2u], right[j % 2u], sums[j]);\n        }\n      }\n      for (let s: u32 = 0u; s < 4u; s = s + 1u) {\n        qpel.matrix_store(sums[s], c, (((row + ((s / 2u) * 8u)) * n.x) + column) + ((s % 2u) * 8u), n.x);\n      }\n    }\n  }\n}',
                    'left[j / 2u] = qpel.matrix_mma(left[j / 2u], right[j % 2u], sums[j]);\n        }\n      }\n      for (let s: u32 = 0u; s < 4u; s = s + 1u) {\n        qpel.matrix_store(sums[s], c, (((row + ((s / 2u) * 8u)) * n.x) + column) + ((s % 2u) * 8u), n.x);\n      }\n    }\n  }\n}',
                    'ASSIGN|TYPE')


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-matrix-arrays-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def check(self, got, case, exact):
        import numpy as np
        rows, columns = case.shapes['n'][1], case.shapes['n'][0]
        for name in ('c', 'c16'):
            want, bound = oracle(case, name)
            have = np.asarray(got[name][:rows * columns])
            if exact and name == 'c':
                np.testing.assert_array_equal(have.view(np.uint32), want.view(np.uint32), err_msg=f'{case.label} {name}')
            error = np.abs(have.astype(np.float64) - want)
            self.assertTrue(np.all(error <= bound), f'{case.label} {name}: worst {np.max(error / np.maximum(bound, 1e-30)):.2f} of the bound')

    def test_spellings(self):
        cuda = (self.generated/'matrix_blocks.cu').read_text()
        metal = (self.generated/'matrix_blocks.metal').read_text()
        self.assertIn('QPEL_MATRIX_F32 sums[4] = {', cuda)
        self.assertIn('QPEL_MATRIX_F16 left[2] = {', metal)
        self.assertIn('QPEL_MATRIX_MMA_F16(left[QPEL_U32_DIV((QPEL_U32)(j), (QPEL_U32)(2u))], ', cuda)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        """f32 bit for bit in k order; f16 too (its products are exact, the CPU adds them in k order)."""
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'matrix_blocks.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases():
            got = program.run(case)
            self.check(got, case, exact=True)
            rows, columns = case.shapes['n'][1], case.shapes['n'][0]
            want, _ = oracle(case, 'c16')
            np.testing.assert_array_equal(got['c16'][:rows * columns].view(np.uint32), want.view(np.uint32), err_msg=case.label)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in cases():
            self.check(metal.run_generated(host, self.plan, case), case, exact=False)

    def test_gpgpusim(self):
        """On GPGPU-Sim's RTX 2060 (sm_75): f32 by the fixed-order chain, bit for bit; f16 by
        mma.sync's tensor cores, within the bound."""
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-matrix-arrays-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'matrix_blocks.cu', self.plan, root/'qpel')
            pending = []
            for number, case in enumerate(cases()):
                folder = program.work/f'c{number:03}'
                folder.mkdir()
                program.write_case(case, folder)
                pending.append((case, folder))
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            for case, folder in pending:
                got, error = program.outcome(folder, case)
                self.assertIsNone(error, case.label)
                self.check(got, case, exact=True)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# llama.cpp's kernel_mul_mm, f32 and without its dequantisation: each threadgroup of 4 simdgroups
# stages a 32 x 8 tile of A and an 8 x 32 tile of B in threadgroup memory, and each simdgroup
# multiplies its 16 x 16 block with ma[2], mb[2] and the accumulators mc[4], loaded as the original
# does (simdgroup_load(m, p, stride, 0, false), between simdgroup_barrier(mem_none)s).
METAL = """#include <metal_stdlib>
using namespace metal;
kernel void mul_mm(device const float* a [[buffer(0)]], device const float* b [[buffer(1)]], device float* c [[buffer(2)]],
                   constant uint& n [[buffer(3)]], constant uint& m [[buffer(4)]], constant uint& k [[buffer(5)]],
                   uint group [[threadgroup_position_in_grid]], ushort tiitg [[thread_index_in_threadgroup]],
                   ushort sgitg [[simdgroup_index_in_threadgroup]]) {
  threadgroup float sa[256];
  threadgroup float sb[256];
  const uint across = n / 32;
  if (group >= across * (m / 32)) return;
  const uint r0 = group / across * 32, r1 = group % across * 32;
  simdgroup_float8x8 ma[2];
  simdgroup_float8x8 mb[2];
  simdgroup_float8x8 mc[4];
  for (short i = 0; i < 4; i++) {
    mc[i] = make_filled_simdgroup_matrix<float, 8>(0.f);
  }
  for (uint loop_k = 0; loop_k < k; loop_k += 8) {
    threadgroup_barrier(mem_flags::mem_threadgroup);
    for (short i = 0; i < 2; i++) {
      const short e = tiitg * 2 + i;
      sa[e] = a[(r0 + e / 8) * k + loop_k + e % 8];
      sb[e] = b[(loop_k + e / 32) * n + r1 + e % 32];
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    threadgroup const float * lsma = sa + 128 * (sgitg % 2);
    threadgroup const float * lsmb = sb + 16 * (sgitg / 2);
    simdgroup_barrier(mem_flags::mem_none);
    for (short i = 0; i < 2; i++) {
      simdgroup_load(ma[i], lsma + 64 * i, 8, 0, false);
    }
    simdgroup_barrier(mem_flags::mem_none);
    for (short i = 0; i < 2; i++) {
      simdgroup_load(mb[i], lsmb + 8 * i, 32, 0, false);
    }
    for (short i = 0; i < 4; i++) {
      simdgroup_multiply_accumulate(mc[i], ma[i / 2], mb[i % 2], mc[i]);
    }
  }
  for (short i = 0; i < 4; i++) {
    simdgroup_store(mc[i], c + (r0 + 16 * (sgitg % 2) + 8 * (i / 2)) * n + r1 + 16 * (sgitg / 2) + 8 * (i % 2), n);
  }
}
"""
GROUPS = 8  # enough for the largest case (64 x 64: four blocks); the others return whole


class MetalImport(unittest.TestCase):
    """The original on the Apple GPU against qpel's Metal (the same simdgroup_multiply_accumulate: the
    same bits) and qpel's CUDA on the CPU simulator (k order, as the oracle)."""

    @classmethod
    def setUpClass(cls):
        from frontends.clang_ast import metal_available
        if not metal_available():
            raise unittest.SkipTest('Xcode Metal compiler required to import Metal')
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-matrix-arrays-metal-')
        work = Path(cls.work.name)
        cls.source = work/'mul_mm.metal'
        cls.source.write_text(METAL)
        cls.program, cls.report = import_source(cls.source, kernels=['mul_mm'], module='mul_mm',
                                                geometry={'groups': [str(GROUPS), '1', '1'], 'block': [128, 1, 1]})
        (work/'p.json').write_text(json.dumps(cls.program))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def cases(self):
        import numpy as np
        from differential.simulate import Case
        rng = np.random.default_rng(15)
        out = []
        for columns, rows, depth in ((32, 32, 8), (64, 32, 40), (32, 64, 16), (64, 64, 24)):
            length = max(rows * depth, depth * columns, rows * columns)
            inputs = {'a': rng.uniform(-1, 1, length).astype(np.float32), 'b': rng.uniform(-1, 1, length).astype(np.float32)}
            out.append(Case({'size': (length, 1, 1)}, inputs, {'n': columns, 'm': rows, 'k': depth},
                            f'n={columns} m={rows} k={depth}'))
        return out

    def test_imported(self):
        validate(self.program)
        self.assertEqual(self.program['version'], 14)
        text = print_program(self.program)
        self.assertIn('let mc: array<matrix<f32, 8, 8>, 4> = [qpel.matrix_fill<f32>(0.0f)', text)
        self.assertIn('qpel.matrix_mma(ma[', text)
        self.assertTrue(any('simdgroup_barrier(mem_none)' in a for a in self.report['adaptations']), self.report['adaptations'])

    def test_not_in_qpel(self):
        from frontends.c_import import import_source
        for old, new, message in (
                ('simdgroup_load(ma[i], lsma + 64 * i, 8, 0, false);', 'simdgroup_load(ma[i], lsma + 64 * i, 8, 0, true);',
                 'transposed'),
                ('simdgroup_load(ma[i], lsma + 64 * i, 8, 0, false);', 'simdgroup_load(ma[i], lsma + 64 * i, 8, ulong2(0, 1), false);',
                 'origin'),
                ('mc[i] = make_filled', 'mc[tiitg % 4] = make_filled', 'SUBGROUP_UNIFORM')):
            with self.subTest(new), tempfile.TemporaryDirectory() as work:
                path = Path(work)/'k.metal'
                path.write_text(METAL.replace(old, new))
                self.assertIn(old, METAL)
                with self.assertRaisesRegex(Exception, message):
                    program, _ = import_source(path, kernels=['mul_mm'], module='mul_mm',
                                               geometry={'groups': [str(GROUPS), '1', '1'], 'block': [128, 1, 1]})
                    validate(program)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        simulated = Program(self.generated/'mul_mm.cu', self.plan, Path(self.work.name)/'sim')
        for case in self.cases():
            columns, rows, depth = case.values['n'], case.values['m'], case.values['k']
            want, _ = gemm(case.inputs['a'], case.inputs['b'], np.zeros(rows * columns, np.float32), rows, columns, depth)
            got = simulated.run(case)['c'][:rows * columns]
            np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32), err_msg=case.label)

    def test_against_the_original(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in self.cases():
            columns, rows, depth = case.values['n'], case.values['m'], case.values['k']
            original = np.asarray(metal.run_original(self.source, self.plan, self.report, case, whole_groups=True)['c'])
            ours = np.asarray(metal.run_generated(host, self.plan, case)['c'])
            np.testing.assert_array_equal(ours.view(np.uint32), original.view(np.uint32), err_msg=case.label)
            want, bound = gemm(case.inputs['a'], case.inputs['b'], np.zeros(rows * columns, np.float32), rows, columns, depth)
            error = np.abs(original[:rows * columns].astype(np.float64) - want)
            self.assertTrue(np.all(error <= bound), case.label)


if __name__ == '__main__':
    unittest.main()
