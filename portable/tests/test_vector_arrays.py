"""Arrays of vectors (IR version 11): `array<vec<f32, 2>, 4>`, CUDA's `float2 items[4]`, as CUB-style
code keeps several items of a pair type per thread (mamba's selective scan composes (a, b) pairs).
examples/vector_arrays.qpel runs on the CPU simulator and the Apple GPU against an oracle, and the
CUDA importer reads the same program written in CUDA."""
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

TEXT = (HERE/'examples/vector_arrays.qpel').read_text()
BODY = TEXT[TEXT.index('module'):]


def oracle(x):
    """Each thread's four (a, b) pairs and their prefixes under (a0, b0) then (a1, b1) = (a1 a0, a1 b0 + b1)."""
    import numpy as np
    pairs = x.reshape(-1, 4, 2).copy()
    for k in range(1, 4):
        a0, b0 = pairs[:, k - 1, 0].copy(), pairs[:, k - 1, 1].copy()
        a1, b1 = pairs[:, k, 0].copy(), pairs[:, k, 1].copy()
        pairs[:, k, 0] = a1 * a0
        pairs[:, k, 1] = a1 * b0 + b1  # float32 throughout, unfused, as qpel computes
    return pairs.reshape(-1).astype(np.float32)


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(11)
    return [Case({'n': (n, 1, 1)}, {'x': rng.standard_normal(n*8).astype(np.float32)}, {}, f'n={n}')
            for n in (1, 63, 200)]


class Rules(unittest.TestCase):
    def test_version_11_and_text(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 11)
        self.assertEqual(print_program(program), BODY)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=10, semantics='qpel-portable-core-v10')
        with self.assertRaisesRegex(ValueError, 'IR version 10 cannot use: array of vectors'):
            validate(program)

    def test_elements_are_vectors(self):
        text = TEXT.replace('      items[k] = compose(items[k - 1u], items[k]);', '      items[k] = 1.0f;')
        with self.assertRaisesRegex(ValueError, 'ASSIGN|TYPE'):
            validate(parse_program(text))

    def test_vectors_of_two_to_four(self):
        text = TEXT.replace('let items: array<vec<f32, 2>, 4>', 'let items: array<vec<f32, 5>, 4>')
        with self.assertRaises(ValueError):
            validate(parse_program(text))


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-vector-arrays-')
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
        want = oracle(case.inputs['x'])
        np.testing.assert_array_equal(np.asarray(got['y']).view(np.uint32), want.view(np.uint32), err_msg=case.label)

    def test_spellings(self):
        cuda = (self.generated/'vector_arrays.cu').read_text()
        self.assertIn('QPEL_VEC2_F32 items[4] = {QPEL_MAKE_VEC2_F32(1.0f, 0.0f)', cuda)
        self.assertIn('QPEL_REF_ARRAY(QPEL_VEC2_F32, items, 4)', cuda)

    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'vector_arrays.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases():
            self.check(program.run(case), case)

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
            self.check(metal.run_generated(host, self.plan, case), case)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Importer(unittest.TestCase):
    SOURCE = '''
__device__ float2 compose(const float2 &first, const float2 &second) {
  return make_float2(second.x * first.x, second.x * first.y + second.y);
}
__device__ void prefixes(float2 (&items)[4]) {
  for (unsigned int k = 1; k < 4; ++k) items[k] = compose(items[k - 1], items[k]);
}
__global__ void vector_arrays(const float* x, float* y, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) {
    float2 items[4];  // default-constructed: no initializer
    for (unsigned int k = 0; k < 4; ++k) items[k] = make_float2(x[(i * 4 + k) * 2], x[(i * 4 + k) * 2 + 1]);
    prefixes(items);
    for (unsigned int k = 0; k < 4; ++k) {
      y[(i * 4 + k) * 2] = items[k].x;
      y[(i * 4 + k) * 2 + 1] = items[k].y;
    }
  }
}
'''

    def test_cuda_arrays_of_vectors(self):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text(self.SOURCE)
            program, _ = import_source(path)
        validate(program)
        self.assertEqual(program['version'], 11)
        text = print_program(program)
        self.assertIn('fn prefixes(ref items: array<vec<f32, 2>, 4>) -> void', text)
        self.assertIn('let items: array<vec<f32, 2>, 4> = [vec<f32, 2>(0.0f, 0.0f)', text)
        self.assertIn('= items[k_2].x;', text)


if __name__ == '__main__':
    unittest.main()
