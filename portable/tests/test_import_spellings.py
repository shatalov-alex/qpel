"""Small C spellings the corpus uses (IMPORT.md): assert(e) left out (qpel kernels cannot stop the device; a
kernel whose assertions hold runs the same), __builtin_expect(x, v) as x, a stray `;` at file scope, and
sizeof of CUDA's vector types. A kernel with all four runs against the original on the CPU simulator."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402

SOURCE = r'''#include <cassert>
#include <cuda_runtime.h>
;
__global__ void spellings(const float* x, float* y, int n) {
  const int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (__builtin_expect(i >= n, 0)) return;
  assert(i < n);
  constexpr int lanes = sizeof(int4) / sizeof(int);
  y[i] = x[i] * (float)lanes;
}
'''


@unittest.skipUnless(shutil.which('clang') and shutil.which('clang++'), 'clang and clang++ required')
class Spellings(unittest.TestCase):
    def test_against_the_original(self):
        import numpy as np
        from frontends.c_import import import_source
        from qpel import compile_program
        from differential.simulate import Program, Case
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            source = work/'spellings.cu'
            source.write_text(SOURCE)
            program, report = import_source(source, kernels=['spellings'], module='spellings')
            validate(program)
            self.assertTrue(any('assert was left out' in a for a in report['adaptations']), report['adaptations'])
            (work/'p.json').write_text(json.dumps(program))
            compile_program(work/'p.json', work/'build')
            generated = work/'build/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'o').mkdir()
            (work/'q').mkdir()
            original = Program(source, plan, work/'o', original=report)
            ours = Program(generated/'spellings.cu', plan, work/'q')
            for n in (1, 77, 300):
                x = np.random.default_rng(n).standard_normal(n).astype(np.float32)
                shape = next(r['name'] for r in plan['resources'] if r['kind'] == 'shape')
                case = Case({shape: (n, 1, 1)}, {'x': x}, {}, f'n={n}')
                want, got = original.run(case)['y'][:n], ours.run(case)['y'][:n]
                np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32))
                np.testing.assert_array_equal(want, x * 4)


if __name__ == '__main__':
    unittest.main()
