"""8x8 matrices held by a subgroup (IR version 13): qpel.matrix_fill, matrix_load, matrix_store and
matrix_mma, lowered to Metal's simdgroup_matrix and, on CUDA, to the mma.sync fragment layout (f32 by
shuffles and fmaf in a fixed order, f16 by tensor cores on the GPU). examples/matrix_tiles.qpel, a
tiled GEMM, runs on the CPU simulator (bit for bit against the fixed-order oracle) and on the Apple
GPU (within the bound of a sum of K products, in whatever order Metal adds them)."""
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

TEXT = (HERE/'examples/matrix_tiles.qpel').read_text()
BODY = TEXT[TEXT.index('module'):]
SHAPES = ((8, 8, 8), (16, 24, 32), (64, 32, 40), (40, 8, 128))


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(13)
    out = []
    for n in SHAPES:
        length = n[0] * n[1] * n[2]
        inputs = {'a': rng.uniform(-1, 1, length).astype(np.float32), 'b': rng.uniform(-1, 1, length).astype(np.float32),
                  'a16': rng.uniform(-2, 2, length).astype(np.float16), 'b16': rng.uniform(-2, 2, length).astype(np.float16),
                  'bias': rng.uniform(-4, 4, length).astype(np.float32)}
        out.append(Case({'n': n}, inputs, {}, f'n={n}'))
    return out


def oracle(case, which):
    """C row by row: c = fma(a[r][k], b[k][j], c) for k = 0 .. K - 1, as qpel's CUDA adds them, and
    the error bound of any order: K ulp-sized steps of the sum of |a b|."""
    import numpy as np
    columns, rows, depth = case.shapes['n']
    if which == 'c':
        a, b, c = case.inputs['a'], case.inputs['b'], np.zeros(rows * columns, np.float32)
    else:
        a, b, c = case.inputs['a16'], case.inputs['b16'], case.inputs['bias']
    return gemm(a, b, c, rows, columns, depth)


def gemm(a, b, c, rows, columns, depth):
    """(C, bound) for row-major A (rows x depth), B (depth x columns) and C (rows x columns, the start)."""
    import numpy as np
    a = a[:rows * depth].astype(np.float64).reshape(rows, depth)
    b = b[:depth * columns].astype(np.float64).reshape(depth, columns)
    c = np.asarray(c[:rows * columns], np.float32).reshape(rows, columns).copy()
    magnitude = np.abs(c).astype(np.float64)
    for k in range(depth):  # (an f32 product is exact in f64; rounding the sum once is fmaf but for ties)
        c = (a[:, k:k + 1] * b[k:k + 1, :] + c.astype(np.float64)).astype(np.float32)
        magnitude += np.abs(a[:, k:k + 1] * b[k:k + 1, :])
    return c.reshape(-1), (depth + 2) * 2.0 ** -24 * magnitude.reshape(-1)


class Rules(unittest.TestCase):
    def test_version_13_and_text(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 13)
        self.assertEqual(print_program(program), BODY)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=12, semantics='qpel-portable-core-v12')
        with self.assertRaisesRegex(ValueError, 'IR version 12 cannot use: matrix'):
            validate(program)

    def reject(self, old, new, code):
        self.assertIn(old, TEXT)
        with self.assertRaisesRegex(ValueError, code):
            validate(parse_program(TEXT.replace(old, new, 1)))

    def test_types(self):
        # f16 matrices accumulate into f32 only; a matrix stores to memory of its own element type
        self.reject('= qpel.matrix_load(b, (k * n.x) + column, n.x);\n        sum = qpel.matrix_mma(left, right, sum);\n      }\n      qpel.matrix_store(sum, c, (row * n.x) + column, n.x);\n    }\n  }\n}',
                    '= qpel.matrix_load(b, (k * n.x) + column, n.x);\n        sum = qpel.matrix_mma(left, sum, sum);\n      }\n      qpel.matrix_store(sum, c, (row * n.x) + column, n.x);\n    }\n  }\n}', 'MATRIX_TYPE')
        self.reject('qpel.matrix_store(sum, c, (row * n.x) + column, n.x);\n    }\n  }\n  kernel gemm_f16',
                    'qpel.matrix_store(sum, a, (row * n.x) + column, n.x);\n    }\n  }\n  kernel gemm_f16', 'READ_ONLY')
        self.reject('let left: matrix<f16, 8, 8> = qpel.matrix_load(a, (row * n.z) + k, n.z);',
                    'let left: matrix<f16, 8, 8> = qpel.matrix_load(bias, (row * n.z) + k, n.z);', 'VARIABLE_TYPE')
        self.reject('qpel.matrix_load(a, (row * n.z) + k, n.z);', 'qpel.matrix_load(a, (row * n.z) + k, 8);', 'MATRIX_MEMORY')

    def test_whole_subgroups(self):
        # every lane of a subgroup takes part: not under a per-lane condition, only in groups kernels
        self.reject('if (tile < (across * (n.y / 8u))) {', 'if (qpel.local.x < 40u) {', 'SUBGROUP_UNIFORM')
        self.reject("block (64, 1, 1) groups (\"(W*H/64 + 1)/2\", \"1\", \"1\") (a = a, b = b, c = c",
                    "block (48, 1, 1) groups (\"(W*H/64 + 1)/2\", \"1\", \"1\") (a = a, b = b, c = c", 'SUBGROUP_BLOCK')


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-matrix-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_spellings(self):
        cuda = (self.generated/'matrix_tiles.cu').read_text()
        metal = (self.generated/'matrix_tiles.metal').read_text()
        self.assertIn('QPEL_MATRIX_MMA_F16(left, right, sum)', cuda)
        self.assertIn('mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32', cuda)
        self.assertIn('simdgroup_multiply_accumulate', metal)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'matrix_tiles.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases():
            got = program.run(case)
            rows, columns = case.shapes['n'][1], case.shapes['n'][0]
            for name in ('c', 'c16'):
                want, _ = oracle(case, name)
                np.testing.assert_array_equal(got[name][:rows * columns].view(np.uint32), want.view(np.uint32),
                                              err_msg=f'{case.label} {name}')

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        import numpy as np
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in cases():
            got = metal.run_generated(host, self.plan, case)
            rows, columns = case.shapes['n'][1], case.shapes['n'][0]
            for name in ('c', 'c16'):
                want, bound = oracle(case, name)
                error = np.abs(np.asarray(got[name][:rows * columns], np.float64) - want)
                self.assertTrue(np.all(error <= bound), f'{case.label} {name}: worst {np.max(error / np.maximum(bound, 1e-30)):.2f} of the bound')


    def test_gpgpusim(self):
        """On GPGPU-Sim's RTX 2060 (sm_75): f32 by the fixed-order chain, bit for bit; f16 by
        mma.sync's tensor cores, whose sum order is the hardware's, within the bound."""
        import numpy as np
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-matrix-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'matrix_tiles.cu', self.plan, root/'qpel')
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
                rows, columns = case.shapes['n'][1], case.shapes['n'][0]
                want, bound = oracle(case, 'c')
                np.testing.assert_array_equal(got['c'][:rows * columns].view(np.uint32), want.view(np.uint32), err_msg=case.label)
                want, bound = oracle(case, 'c16')
                error = np.abs(np.asarray(got['c16'][:rows * columns], np.float64) - want)
                self.assertTrue(np.all(error <= bound), f'{case.label} c16: worst {np.max(error / np.maximum(bound, 1e-30)):.2f} of the bound')
        finally:
            shutil.rmtree(root, ignore_errors=True)


METAL = """#include <metal_stdlib>
using namespace metal;
kernel void gemm_float(device const float* a [[buffer(0)]], device const float* b [[buffer(1)]], device float* c [[buffer(2)]],
                       constant uint& n [[buffer(3)]], constant uint& m [[buffer(4)]], constant uint& k [[buffer(5)]],
                       uint group [[threadgroup_position_in_grid]], uint sg [[simdgroup_index_in_threadgroup]]) {
  uint tile = group * 2 + sg;
  uint across = n / 8;
  if (tile >= across * (m / 8)) return;
  uint row = tile / across * 8, column = tile % across * 8;
  simdgroup_float8x8 sum = make_filled_simdgroup_matrix<float, 8, 8>(0.0f);
  for (uint i = 0; i < k; i += 8) {
    simdgroup_float8x8 left, right;
    simdgroup_load(left, a + row * k + i, k);
    simdgroup_load(right, b + i * n + column, n);
    simdgroup_multiply_accumulate(sum, left, right, sum);
  }
  simdgroup_store(sum, c + row * n + column, n);
}
kernel void gemm_half(device const half* a [[buffer(0)]], device const half* b [[buffer(1)]], device float* c [[buffer(2)]],
                      constant uint& n [[buffer(3)]], constant uint& m [[buffer(4)]], constant uint& k [[buffer(5)]],
                      uint group [[threadgroup_position_in_grid]], uint sg [[simdgroup_index_in_threadgroup]]) {
  uint tile = group * 2 + sg;
  uint across = n / 8;
  if (tile >= across * (m / 8)) return;
  uint row = tile / across * 8, column = tile % across * 8;
  simdgroup_float8x8 sum;
  simdgroup_load(sum, c + row * n + column, n);
  for (uint i = 0; i < k; i += 8) {
    simdgroup_half8x8 left, right;
    simdgroup_load(left, a + row * k + i, k);
    simdgroup_load(right, b + i * n + column, n);
    simdgroup_multiply_accumulate(sum, left, right, sum);
  }
  simdgroup_store(sum, c + row * n + column, n);
}
"""


class MetalImport(unittest.TestCase):
    """Metal's simdgroup matrices read as qpel matrices: the original on the Apple GPU against
    qpel's Metal (the same simdgroup_multiply_accumulate: the same bits) and qpel's CUDA on the
    CPU simulator (k order: within the bound)."""

    @classmethod
    def setUpClass(cls):
        from frontends.clang_ast import metal_available
        if not metal_available():
            raise unittest.SkipTest('Xcode Metal compiler required to import Metal')
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-matrix-metal-')
        work = Path(cls.work.name)
        cls.source = work/'gemm.metal'
        cls.source.write_text(METAL)
        cls.built = {}
        for kernel in ('gemm_float', 'gemm_half'):
            program, report = import_source(cls.source, kernels=[kernel], module=kernel,
                                            resources={'c': 'inout'} if kernel == 'gemm_half' else None)
            (work/kernel).mkdir()
            (work/kernel/'p.json').write_text(json.dumps(program))
            compile_program(work/kernel/'p.json', work/kernel/'build')
            generated = work/kernel/'build/package/generated'
            cls.built[kernel] = (program, report, generated, json.loads((generated/'pipeline.json').read_text()))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def cases(self, kernel):
        import numpy as np
        from differential.simulate import Case
        rng = np.random.default_rng(14)
        dtype = np.float32 if kernel == 'gemm_float' else np.float16
        out = []
        for columns, rows, depth in ((8, 8, 8), (24, 16, 32), (64, 40, 24)):
            length = -(-max(rows * depth, depth * columns, rows * columns) // 64) * 64
            inputs = {'a': rng.uniform(-1, 1, length).astype(dtype), 'b': rng.uniform(-1, 1, length).astype(dtype)}
            if kernel == 'gemm_half':
                inputs['c'] = rng.uniform(-2, 2, length).astype(np.float32)
            out.append(Case({'size': (length, 1, 1)}, inputs, {'n': columns, 'm': rows, 'k': depth},
                            f'{kernel} n={columns} m={rows} k={depth}'))
        return out

    def test_imported(self):
        for kernel, (program, report, generated, plan) in self.built.items():
            validate(program)
            self.assertEqual(program['version'], 13)
            text = print_program(program)
            self.assertIn('qpel.matrix_mma(left, right, sum)', text)
            self.assertIn('qpel.matrix_load(a, (row * k) + i, k)', text)
            self.assertIn('qpel.matrix_store(sum, c, (row * n) + column, n)', text)

    def test_not_in_qpel(self):
        from frontends.c_import import import_source
        for old, new, message in (
                ('simdgroup_load(right, b + i * n + column, n);', 'simdgroup_load(right, b + i * n + column, n, ulong2(0), true);',
                 'transposed'),
                ('simdgroup_float8x8 sum = make_filled_simdgroup_matrix<float, 8, 8>(0.0f);', 'simdgroup_float8x8 sum(1.0f);',
                 'diagonal')):
            with self.subTest(new), tempfile.TemporaryDirectory() as work:
                path = Path(work)/'k.metal'
                path.write_text(METAL.replace(old, new))
                with self.assertRaisesRegex(Exception, message):
                    import_source(path, kernels=['gemm_float'], module='gemm_float')

    def test_against_the_original(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        from differential.simulate import Program
        for kernel, (program, report, generated, plan) in self.built.items():
            host = matrix.load_host(generated)
            (Path(self.work.name)/kernel/'sim').mkdir(exist_ok=True)
            simulated = Program(generated/(kernel + '.cu'), plan, Path(self.work.name)/kernel/'sim')
            for case in self.cases(kernel):
                columns, rows, depth = case.values['n'], case.values['m'], case.values['k']
                original = np.asarray(metal.run_original(self.source, plan, report, case, whole_groups=True)['c'])
                ours = np.asarray(metal.run_generated(host, plan, case)['c'])
                np.testing.assert_array_equal(ours.view(np.uint32), original.view(np.uint32), err_msg=case.label)
                start = case.inputs.get('c', np.zeros(rows * columns, np.float32))
                want, bound = gemm(case.inputs['a'], case.inputs['b'], start, rows, columns, depth)
                cuda = simulated.run(case)['c'][:rows * columns]
                np.testing.assert_array_equal(cuda.view(np.uint32), want.view(np.uint32), err_msg=case.label)
                error = np.abs(original[:rows * columns].astype(np.float64) - want)
                self.assertTrue(np.all(error <= bound), case.label)

if __name__ == '__main__':
    unittest.main()
