"""CUDA's `__syncthreads_or(p)`: a block barrier that is nonzero when any thread's predicate holds
(gsplat's packed projections and rasterize backward). It imports as `qpel.barrier_count(p) != 0`,
uniform over the block, so a barrier under it passes the uniformity check."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

SOURCE = '''
__global__ void any_positive(const float* x, int* flags, float* y, unsigned n) {
  __shared__ float cache[64];
  unsigned i = blockIdx.x * 64 + threadIdx.x;
  float v = i < n ? x[i] : 0.0f;
  cache[threadIdx.x] = v;
  const bool any = __syncthreads_or(v > 1.5f);
  if (__syncthreads_or(v < -1.5f)) {
    __syncthreads();  // a barrier under it: the condition is the block's, not the thread's
    v = cache[63 - threadIdx.x];
  }
  if (i < n) {
    flags[i] = any ? 1 : 0;
    y[i] = v;
  }
}
'''


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class SyncthreadsOr(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.source = self.folder/'any.cu'
        self.source.write_text(SOURCE)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def imported(self):
        from frontends.c_import import import_source
        return import_source(self.source, geometry={'groups': ['(W + 63)/64', '1', '1'], 'block': [64, 1, 1]})

    def test_import(self):
        from qpel_ir import validate
        from qpel_text import print_program
        program, _ = self.imported()
        for target in ('cuda', 'metal'):
            validate(program, target)
        text = print_program(program)
        self.assertEqual(text.count('qpel.barrier_count('), 2)
        self.assertIn('!= 0', text)

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
        ours = Program(self.folder/'build/package/generated/any.cu', plan, self.folder/'qpel')
        for n, scale in ((1, 1.0), (40, 1.0), (64, 3.0), (200, 1.7)):
            x = (np.random.default_rng(n).uniform(-1, 1, n)*scale).astype(np.float32)
            shapes = {r['name']: (n, 1, 1) for r in plan['resources'] if r['kind'] == 'shape'}
            values = {r['name']: n for r in plan['resources'] if r['kind'] == 'value'}
            case = Case(shapes, {'x': x}, values, str(n))
            want, got = original.run(case), ours.run(case)
            for name in ('flags', 'y'):
                np.testing.assert_array_equal(want[name], got[name])
            blocks = [x[b:b + 64] for b in range(0, n, 64)]  # the oracle: any element of the block over 1.5
            expected = np.concatenate([np.full(len(b), int((b > 1.5).any()), np.int32) for b in blocks])
            np.testing.assert_array_equal(want['flags'], expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
