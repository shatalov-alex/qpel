"""Arrays passed to helpers by reference (IR version 10): `ref items: array<f32, 4>`, CUDA's
`float (&items)[4]`, Metal's `thread float (&items)[4]`, as kernels pass several items per thread
(mamba's selective scan, CUB's blocked arrangements)."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program  # noqa: E402

TEXT = (HERE/'examples/array_refs.qpel').read_text()


class Rules(unittest.TestCase):
    def test_version_10(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 10)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=9, semantics='qpel-portable-core-v9')
        with self.assertRaisesRegex(ValueError, 'IR version 9 cannot use: array reference parameter'):
            validate(program)

    def test_the_length_must_match(self):
        text = TEXT.replace('fn total(ref items: array<f32, 4>)', 'fn total(ref items: array<f32, 8>)')
        with self.assertRaisesRegex(ValueError, 'ARG_TYPE|REFERENCE_ARGUMENT'):
            validate(parse_program(text))

    def test_an_array_is_still_not_a_value(self):
        text = TEXT.replace('fn total(ref items: array<f32, 4>) -> f32 {\n    return',
                            'fn total(ref items: array<f32, 4>) -> f32 {\n    let copy: f32 = items;\n    return')
        with self.assertRaises(ValueError):
            validate(parse_program(text))


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Importer(unittest.TestCase):
    SOURCE = '''
__device__ void load(const float* x, unsigned int base, float (&items)[4]) {
  for (int k = 0; k < 4; ++k) items[k] = x[base + k] * 2.0f;
}
__device__ float total(const float (&items)[4]) {
  return ((items[0] + items[1]) + items[2]) + items[3];
}
__global__ void sums(const float* x, float* y, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) {
    float items[4];
    load(x, i * 4, items);
    y[i] = total(items);
  }
}
'''

    def test_cuda_array_references(self):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text(self.SOURCE)
            program, _ = import_source(path)
        validate(program)
        refs = [n['attributes']['name'] for n in program['nodes'] if n['kind'] == 'Parameter' and n['attributes'].get('reference')]
        self.assertEqual(sorted(refs), ['items', 'items'])
        self.assertEqual(program['version'], 10)


if __name__ == '__main__':
    unittest.main()
