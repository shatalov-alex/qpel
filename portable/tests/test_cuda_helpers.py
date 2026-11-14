"""CUDA idioms of library kernels (llama.cpp's cumsum): helpers that read thread builtins (passed
as parameters), constexpr constants, `if constexpr` in template instances, (void) statements,
stores through `*p`, and the subgroup check seeing through `const int tid = threadIdx.x`."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

SOURCE = '''
static constexpr __device__ int lanes() { return 32; }
template <typename T> __device__ T convert(float x) {
  if constexpr (sizeof(T) == 4) { return x; } else { return T(0); }
}
__device__ float warp_scan(float x) {
  const int lane = threadIdx.x % 32;
  for (int offset = 1; offset < 32; offset <<= 1) {
    const float t = __shfl_up_sync(0xffffffff, x, offset, 32);
    if (lane >= offset) x += t;
  }
  return x;
}
__global__ void scan_rows(const float* x, float* y, float* total, unsigned n) {
  (void)sizeof((x, y, 0));
  extern __shared__ float sums[];
  constexpr int width = lanes();
  const int tid = threadIdx.x;
  const int warp = tid / width;
  unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
  float v = warp_scan(i < n ? x[i] : 0.0f);
  if (tid % width == width - 1) sums[warp] = v;
  __syncthreads();
  if (warp == 0) {
    float s = warp_scan(tid < 2 ? sums[tid] : 0.0f);
    if (tid == 1) *total = convert<float>(s);
  }
  if (i < n) y[i] = v;
}
'''


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class CudaHelpers(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.source = self.folder/'scan.cu'
        self.source.write_text(SOURCE)

    def imported(self):
        from frontends.c_import import import_source
        return import_source(self.source, shared={'sums': 2}, resources={'total': 'inout'},
                             geometry={'groups': ['(W + 63)/64', '1', '1'], 'block': [64, 1, 1]})

    def test_import(self):
        from qpel_ir import validate
        from qpel_text import print_program
        program, report = self.imported()
        for target in ('cuda', 'metal'):
            validate(program, target)  # the shuffles under `warp == 0` pass the subgroup check
        text = print_program(program)
        self.assertIn('fn warp_scan(x: f32, thread_idx_x: u32) -> f32', text)
        self.assertIn('warp_scan(', text)
        self.assertIn('qpel.local.x)', text)  # the kernel passes its builtin
        self.assertIn('let width: i32 = 32;', text)  # constexpr lanes() folded
        self.assertIn('total[0u] = ', text)  # *total = ...
        self.assertNotIn('sizeof', text)
        self.assertTrue(any('thread_idx_x as parameters' in a for a in report['adaptations']))

    def test_original_and_translation_agree_on_the_simulator(self):
        import numpy as np
        from qpel_ir import Lowering
        from qpel import compile_program
        from differential.simulate import Case, Program
        program, report = self.imported()
        plan = Lowering(program).plan()
        (self.folder/'p.json').write_text(json.dumps(program))
        compile_program(self.folder/'p.json', self.folder/'build')
        for folder in ('original', 'qpel'):
            (self.folder/folder).mkdir()
        original = Program(self.source, plan, self.folder/'original', original=report)
        ours = Program(self.folder/'build/package/generated/scan.cu', plan, self.folder/'qpel')
        for n in (1, 40, 64, 100):
            x = np.random.default_rng(n).uniform(-2, 2, n).astype(np.float32)
            shapes = {r['name']: (n, 1, 1) for r in plan['resources'] if r['kind'] == 'shape'}
            values = {r['name']: n for r in plan['resources'] if r['kind'] == 'value'}
            case = Case(shapes, {'x': x, 'total': np.zeros(n, np.float32)}, values, str(n))
            want, got = original.run(case), ours.run(case)
            for name in ('y', 'total'):
                np.testing.assert_array_equal(want[name], got[name])
            first = np.cumsum(x[:32], dtype=np.float64)
            np.testing.assert_allclose(want['y'][:min(n, 32)], first[:min(n, 32)], rtol=1e-5, atol=1e-5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
