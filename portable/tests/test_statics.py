"""Static methods of library structs, enumerators, pointers to locals and __ldg (frontends/statics.py),
as pytorch_sparse's spmm writes them, and the per-subgroup thread row they index by."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from frontends.c_import import import_source, Unsupported  # noqa: E402
from qpel_ir import validate  # noqa: E402

REDUCER = '''
#include <limits>
enum Mode { ADD, TIMES = 4, BIGGEST, WIDE = sizeof(int) > 2 };  // (WIDE: a bool-valued initializer)

template <typename T, Mode M> struct Folder {
  static inline __host__ __device__ T init() {
    if (M == BIGGEST) return std::numeric_limits<T>::lowest();
    return M == TIMES ? (T)1 : (T)0;
  }
  static inline __host__ __device__ void update(T *acc, T v, int *at, int i) {
    if (M == ADD) *acc = *acc + v;
    else if (M == TIMES) *acc = *acc * v;
    else if (v > *acc) { *acc = v; *at = i; }
  }
};

template <typename T, Mode M>
__global__ void fold(const T *x, T *out, int *where, int n, unsigned int count) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < count) {
    T acc = Folder<T, M>::init();
    int at = -1;
    for (int k = 0; k < n; k++)
      Folder<T, M>::update(&acc, __ldg(x + k), &at, k);
    out[i] = acc;
    where[i] = at;
  }
}

template __global__ void fold<float, BIGGEST>(const float *, float *, int *, int, unsigned int);
'''

ROWS = '''
__global__ void row_sums(const float *x, float *out, int rows, int k) {
  int t = blockDim.x * blockIdx.x + threadIdx.x;
  int row = t >> 5, lane = t & 31;
  if (row < rows) {
    float v = 0.0f;
    for (int c = lane; c < k; c += 32) v += x[row * k + c];
    for (int d = 16; d > 0; d /= 2) v += __shfl_down_sync(0xffffffff, v, d);
    if (lane == 0) out[row] = v;
  }
}
'''


@unittest.skipUnless(shutil.which('clang'), 'needs clang')
class Statics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def program(self, text):
        path = Path(self.tmp.name)/'sample.cu'
        path.write_text(text)
        program, report = import_source(path)
        validate(program)
        return program, report

    def test_static_methods_enumerators_and_local_pointers(self):
        program, report = self.program(REDUCER)
        functions = {n['attributes']['name']: n for n in program['nodes'] if n['kind'] == 'Function'}
        self.assertIn('Folder_f32_5_init', functions)  # BIGGEST is 5: after TIMES = 4
        self.assertIn('Folder_f32_5_update', functions)
        references = [n['attributes']['name'] for n in program['nodes']
                      if n['kind'] == 'Parameter' and n['attributes'].get('reference')]
        self.assertEqual(sorted(references), ['acc', 'at'])
        literals = [n['attributes']['value'] for n in program['nodes'] if n['kind'] == 'F32Literal']
        self.assertIn('3.4028234663852886e+38', literals)  # lowest() of float: -FLT_MAX
        self.assertTrue(any('read as a reference' in a for a in report['adaptations']))
        self.assertIn('fold<float, (Mode)5>', report['qualified'].values())  # how the original is called

    def test_a_pointer_passed_a_buffer_stays_a_buffer(self):
        text = REDUCER.replace('Folder<T, M>::update(&acc, __ldg(x + k), &at, k);',
                               'Folder<T, M>::update(&acc, __ldg(x + k), where, k);')
        program, _ = self.program(text)
        references = [n['attributes']['name'] for n in program['nodes']
                      if n['kind'] == 'Parameter' and n['attributes'].get('reference')]
        self.assertEqual(references, ['acc'])

    def test_rows_of_the_grid_index_are_per_subgroup(self):
        program, _ = self.program(ROWS)  # validate() runs the subgroup check
        self.assertIn('SubgroupShuffle', [n['kind'] for n in program['nodes']])

    def test_a_row_of_another_index_is_not(self):
        with self.assertRaises((Unsupported, ValueError)):
            self.program(ROWS.replace('int t = blockDim.x * blockIdx.x + threadIdx.x;',
                                      'int t = blockIdx.x + threadIdx.x;'))


if __name__ == '__main__':
    unittest.main()
