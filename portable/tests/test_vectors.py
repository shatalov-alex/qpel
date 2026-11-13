"""IR version 4 vectors (vec<T, N>) and reference parameters, through text, both backends and both importers."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import Lowering, validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

PROGRAM = '''module vecs {
  fn mirror(a: vec<f32, 3>, k: f32) -> vec<f32, 3> {
    return vec<f32, 3>(a.z, a.y * k, a.x);
  }
  fn split(v: vec<f32, 3>, ref lo: vec<f32, 3>, ref count: u32) -> bool {
    lo = vec<f32, 3>(v.x - 1.0f, v.y, v.z);
    lo.z = lo.z * 2.0f;
    count = count + 1u;
    return v.x > 4.0f;
  }
  kernel k(x: buffer<f32, read> @0, y: buffer<f32, write> @1, n: shape3 @2) grid1d {
    if (qpel.index < ((n.x * n.y) * n.z)) {
      let v: vec<f32, 3> = vec<f32, 3>(x[qpel.index], 2.0f, 3.0f);
      let w: vec<f32, 3> = mirror(v, 0.5f);
      w.y = w.y + 1.0f;
      v = w;
      let b: vec<i32, 4> = vec<i32, 4>(1, 2, 3, 4);
      let lo: vec<f32, 3> = vec<f32, 3>(0.0f, 0.0f, 0.0f);
      let c: u32 = 0u;
      let big: bool = split(vec<f32, 3>(x[qpel.index], 1.0f, 1.0f), lo, c);
      y[qpel.index] = (((v.x + v.y) + cast<f32>(b.w)) + (lo.x + lo.z)) + (big ? cast<f32>(c) : 0.0f);
    }
  }
}
pipeline vecs_launch {
  shape n;
  input x: buffer<f32, read> in n;
  output y: buffer<f32, write> in n;
  dispatch k over n block (64) (x = x, y = y, n = n);
}
'''


def expected(x):
    import numpy as np
    # v = (3, 2, x) after the mirror; lo = (x - 1, 1, 2); c = 1 when x > 4; float32, in the kernel's order
    f = np.float32
    lo = (x - f(1)) + f(2)
    return ((f(3) + f(2)) + f(4) + lo + np.where(x > f(4), f(1), f(0))).astype(np.float32)


class Text(unittest.TestCase):
    def test_round_trip_and_version(self):
        program = parse_program(PROGRAM)
        validate(program)
        self.assertEqual(program['version'], 4)
        self.assertEqual(print_program(parse_program(print_program(program))), print_program(program))

    def test_lowering_uses_the_backend_vectors(self):
        lowering = Lowering(parse_program(PROGRAM))
        text = lowering.emit(lowering.nodes[lowering.program['roots']['module']])
        self.assertIn('QPEL_DEVICE QPEL_VEC3_F32 mirror(QPEL_VEC3_F32 a, QPEL_F32 k)', text)
        self.assertIn('QPEL_REF(QPEL_VEC3_F32) lo, QPEL_REF(QPEL_U32) count', text)
        self.assertIn('QPEL_MAKE_VEC4_I32(1, 2, 3, 4)', text)
        self.assertIn('w.y = ', text)

    def test_rules(self):
        cases = {
            'MEMBER': ('lo.z = lo.z * 2.0f;', 'lo.w = lo.z * 2.0f;'),
            'VECTOR_VALUE': ('vec<i32, 4>(1, 2, 3, 4)', 'vec<i32, 4>(1, 2, 3)'),
            'REFERENCE_ARGUMENT': ('split(vec<f32, 3>(x[qpel.index], 1.0f, 1.0f), lo, c)',
                                   'split(vec<f32, 3>(x[qpel.index], 1.0f, 1.0f), mirror(lo, 1.0f), c)'),
            'REFERENCE': ('kernel k(x: buffer<f32, read> @0', 'kernel k(ref x: buffer<f32, read> @0'),
            'Invalid vector type': ('let b: vec<i32, 4> = vec<i32, 4>(1, 2, 3, 4);', 'let b: vec<i32, 5> = vec<i32, 5>(1, 2, 3, 4, 5);'),
        }
        for code, (old, new) in cases.items():
            with self.subTest(code), self.assertRaisesRegex(ValueError, code):
                validate(parse_program(PROGRAM.replace(old, new)))
        alias = PROGRAM.replace('fn split(v: vec<f32, 3>, ref lo: vec<f32, 3>, ref count: u32)',
                                'fn split(v: vec<f32, 3>, ref lo: vec<f32, 3>, ref count: vec<f32, 3>)')
        alias = alias.replace('count = count + 1u;', '').replace('split(vec<f32, 3>(x[qpel.index], 1.0f, 1.0f), lo, c)',
                                                                 'split(vec<f32, 3>(x[qpel.index], 1.0f, 1.0f), lo, lo)')
        with self.assertRaisesRegex(ValueError, 'REFERENCE_ALIAS'):
            validate(parse_program(alias))


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-vectors-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(PROGRAM)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def case(self):
        import numpy as np
        from differential.simulate import Case
        x = np.linspace(-3, 9, 37, dtype=np.float32)
        return Case({'n': (37, 1, 1)}, {'x': x}, {}, '37'), x

    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        case, x = self.case()
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        y = Program(self.generated/'vecs.cu', self.plan, Path(self.work.name)/'sim').run(case)['y']
        np.testing.assert_array_equal(y, expected(x))

    def test_apple_gpu(self):
        try:
            import numpy as np
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        case, x = self.case()
        y = metal.run_generated(matrix.load_host(self.generated), self.plan, case)['y']
        np.testing.assert_array_equal(y, expected(x))

    def test_both_importers_give_the_program_back(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from frontends.roundtrip import canonical
        original = canonical(parse_program(PROGRAM))
        for source in ('vecs.cu', 'vecs.metal'):
            if source.endswith('.metal') and not clang_ast.metal_available():
                unavailable(self, 'metal', 'Metal toolchain required')
            with self.subTest(source):
                program, _ = import_source(self.generated/source, plan=self.plan, module='vecs')
                self.assertEqual(canonical(program), original)


@unittest.skipUnless(shutil.which('clang') and shutil.which('clang++'), 'clang required')
class CudaImport(unittest.TestCase):
    def test_vectors_references_and_make(self):
        from frontends.c_import import import_source
        source = '''
__device__ float3 scaled(const float3& p, float k) { return make_float3(p.x * k, p.y * k, p.z * k); }
__device__ void bounds(const int4 box, uint2& lo, uint2& hi) {
  lo = { (unsigned int)box.x, (unsigned int)box.y };
  hi.x = (unsigned int)box.z; hi.y = (unsigned int)box.w;
}
__global__ void k(const float* x, unsigned int* y, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) {
    float3 p = scaled(*(float3*)x, 2.0f);
    uint2 lo, hi;
    bounds(make_int4(1, 2, (int)p.z, 4), lo, hi);
    y[i] = lo.x + hi.y + hi.x;
  }
}'''
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'k.cu'
            path.write_text(source)
            program, report = import_source(path)
        text = print_program(program)
        self.assertIn('fn scaled(p: vec<f32, 3>, k: f32) -> vec<f32, 3>', text)
        self.assertIn('fn bounds(box: vec<i32, 4>, ref lo: vec<u32, 2>, ref hi: vec<u32, 2>) -> void', text)
        self.assertIn('scaled(vec<f32, 3>(x[0u], x[1u], x[2u]), 2.0f)', text)
        self.assertIn('hi.x = cast<u32>(box.z);', text)
        self.assertTrue(any('local lo had no initializer' in a for a in report['adaptations']))

    def test_kernel_reference_arguments_are_refused(self):
        from frontends.c_import import Unsupported, import_source
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'k.cu'
            path.write_text('__global__ void k(unsigned int* y, unsigned int& n) { y[0] = n; }')
            with self.assertRaisesRegex(Unsupported, 'reference'):
                import_source(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
