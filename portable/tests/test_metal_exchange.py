"""Metal compare-exchange loops (atomic_compare_exchange_weak_explicit) import as qpel
compare-and-swap: `found = cas(p, expected, desired); exchanged = found == expected; expected = found;`
before the statement, re-run each iteration when it is a while condition. The unmodified original
runs on the Metal-on-CPU runner and the Apple GPU; qpel's CUDA runs on the simulator."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import unavailable  # noqa: E402

SOURCE = '''#include <metal_stdlib>
using namespace metal;
// Each thread takes one place in its bin, of at most 5 places (an if inside a while (c) loop).
kernel void capped(device atomic_uint* counts [[buffer(0)]], device const uint* bins [[buffer(1)]],
                   device uint* taken [[buffer(2)]], constant uint& n [[buffer(3)]],
                   uint i [[thread_position_in_grid]]) {
  if (i >= n) return;
  uint slot = bins[i] % 8u;
  uint old = atomic_load_explicit(counts + slot, memory_order_relaxed);
  bool won = false;
  while (old < 5u) {
    if (atomic_compare_exchange_weak_explicit(counts + slot, &old, old + 1u, memory_order_relaxed,
                                              memory_order_relaxed)) {
      won = true;
      break;
    }
  }
  taken[i] = won ? 1u : 0u;
}
// A sum modulo 1000 (while (!exchange) and do ... while (!exchange) loops).
kernel void modular(device atomic_uint* acc [[buffer(0)]], device const uint* x [[buffer(1)]],
                    constant uint& n [[buffer(2)]], uint i [[thread_position_in_grid]]) {
  if (i >= n) return;
  uint old = atomic_load_explicit(acc, memory_order_relaxed);
  while (!atomic_compare_exchange_weak_explicit(acc, &old, (old + x[i]) % 1000u, memory_order_relaxed,
                                                memory_order_relaxed)) {
  }
  uint seen = atomic_load_explicit(acc + 1, memory_order_relaxed);
  do {
  } while (!atomic_compare_exchange_weak_explicit(acc + 1, &seen, (seen + 2u * x[i]) % 1000u,
                                                  memory_order_relaxed, memory_order_relaxed));
}
'''
RESOURCES = {'counts': 'inout', 'acc': 'inout'}


def oracle(case, n):
    import numpy as np
    counts = case.inputs['counts'][:8].astype(np.int64)
    bins = case.inputs['bins'][:n] % 8
    hits = np.bincount(bins, minlength=8)
    final = np.where(counts >= 5, counts, np.minimum(5, counts + hits))
    acc = case.inputs['acc'][:2].astype(np.int64)
    x = case.inputs['x'][:n].astype(np.int64)
    sums = [(acc[0] + x.sum()) % 1000, (acc[1] + 2 * x.sum()) % 1000]
    return final.astype(np.uint32), np.array(sums, np.uint32), (final - counts)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class MetalExchange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends import clang_ast
        if not clang_ast.metal_available():
            raise unittest.SkipTest('no Metal toolchain (Xcode)')
        cls.folder = Path(tempfile.mkdtemp())
        cls.source = cls.folder/'exchange.metal'
        cls.source.write_text(SOURCE)

    def imported(self):
        from frontends.c_import import import_source
        return import_source(self.source, resources=RESOURCES)

    def cases(self, plan):
        import numpy as np
        from differential.simulate import Case
        rng = np.random.default_rng(7)
        for n in (8, 9, 64, 300):  # the plan sizes every buffer by n; counts uses 8 and acc 2
            shapes = {r['name']: (n, 1, 1) for r in plan['resources'] if r['kind'] == 'shape'}
            inputs = {'counts': rng.integers(0, 7, n).astype(np.uint32), 'bins': rng.integers(0, 2**32, n, dtype=np.uint32),
                      'acc': rng.integers(0, 1000, n).astype(np.uint32), 'x': rng.integers(0, 2**20, n).astype(np.uint32)}
            values = {r['name']: n for r in plan['resources'] if r['kind'] == 'value'}
            yield Case(shapes, inputs, values, str(n)), n

    def check(self, got, case, n):
        import numpy as np
        counts, sums, wins = oracle(case, n)
        np.testing.assert_array_equal(got['counts'][:8], counts, err_msg=case.label)
        np.testing.assert_array_equal(got['acc'][:2], sums, err_msg=case.label)
        bins = case.inputs['bins'][:n] % 8
        np.testing.assert_array_equal(np.bincount(bins, weights=got['taken'][:n], minlength=8), wins, err_msg=case.label)

    def test_import(self):
        from qpel_ir import validate
        from qpel_text import print_program
        program, report = self.imported()
        for target in ('cuda', 'metal'):
            validate(program, target)
        text = print_program(program)
        self.assertIn('atomic.cas(', text)
        self.assertIn('while (true) {', text)
        self.assertTrue(any('compare-exchange loops' in a for a in report['adaptations']))

    def test_rejections(self):
        from frontends.c_import import import_source
        for old, new, message in (
                ('while (old < 5u) {', 'while (old < 5u && n > 0u) {', None),
                ('if (atomic_compare', 'if (n > 0u && atomic_compare', 'only some evaluations'),
                ('  do {\n  } while', '  do {\n    if (seen == 7u) continue;\n  } while', 'continue')):
            source = self.folder/'rejected.metal'
            source.write_text(SOURCE.replace(old, new, 1))
            if message is None:
                import_source(source, resources=RESOURCES)  # the exchange is not under the &&
                continue
            with self.assertRaisesRegex(ValueError, message):
                import_source(source, resources=RESOURCES)

    def test_original_and_translation(self):
        """The original on the Metal-on-CPU runner against qpel's CUDA on the simulator (both bounded:
        each failed exchange means another thread's succeeded)."""
        from qpel_ir import Lowering
        from qpel import compile_program
        from differential import metal_cpu
        from differential.simulate import Program
        program, report = self.imported()
        plan = Lowering(program).plan()
        (self.folder/'p.json').write_text(json.dumps(program))
        compile_program(self.folder/'p.json', self.folder/'build')
        original = metal_cpu.program(self.source, plan, report, self.folder/'cpu', timeout=60)
        (self.folder/'qpel').mkdir(exist_ok=True)
        ours = Program(self.folder/'build/package/generated/exchange.cu', plan, self.folder/'qpel')
        for case, n in self.cases(plan):
            for runner in (original, ours):
                self.check(runner.run(case), case, n)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from qpel_ir import Lowering
        from qpel import compile_program
        from differential import matrix, metal
        program, report = self.imported()
        plan = Lowering(program).plan()
        (self.folder/'g.json').write_text(json.dumps(program))
        compile_program(self.folder/'g.json', self.folder/'gpu')
        host = matrix.load_host(self.folder/'gpu/package/generated')
        for case, n in self.cases(plan):
            self.check(metal.run_original(self.source, plan, report, case), case, n)
            self.check(metal.run_generated(host, plan, case), case, n)


if __name__ == '__main__':
    unittest.main(verbosity=2)
