"""Half and bfloat arithmetic in imported kernels (IMPORT.md): `half * half` and the other operations are
read as the f32 operation rounded once to half, which is the correctly rounded half result (f32 has at
least 2 p + 2 bits of a p-bit type), so the IR needs no half arithmetic of its own. A Metal kernel with
half + - * /, negation, += and comparisons runs on the Apple GPU against the original."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

METAL = """#include <metal_stdlib>
using namespace metal;
kernel void halves(device const half* a [[buffer(0)]], device const half* b [[buffer(1)]], device half* out [[buffer(2)]],
                   device half* acc [[buffer(3)]], constant uint& n [[buffer(4)]], uint i [[thread_position_in_grid]]) {
  if (i >= n) return;
  const half x = a[i], y = b[i];
  half s = x * y + x;
  s -= y / (x + half(3.0));
  out[i] = (x < y) ? -s : s;
  acc[i] += x * y;
}
"""


def case(n=4096):
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(19)
    a = rng.uniform(-8, 8, n).astype(np.float16)
    b = rng.uniform(-8, 8, n).astype(np.float16)
    acc = rng.uniform(-100, 100, n).astype(np.float16)
    return Case({'n': (n, 1, 1)}, {'a': a, 'b': b, 'acc': acc}, {}, f'n={n}')


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class HalfArithmetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends.clang_ast import metal_available
        if not metal_available():
            raise unittest.SkipTest('Xcode Metal compiler required to import Metal')
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-half-arithmetic-')
        work = Path(cls.work.name)
        cls.source = work/'halves.metal'
        cls.source.write_text(METAL)
        cls.program, cls.report = import_source(cls.source, kernels=['halves'], module='halves', resources={'acc': 'inout'})
        (work/'p.json').write_text(json.dumps(cls.program))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_imported(self):
        validate(self.program)
        text = print_program(self.program)
        self.assertIn('cast<f16>(cast<f32>(x) * cast<f32>(y))', text)

    def test_numpy(self):
        """The rule itself: numpy's float16 operations are IEEE half, correctly rounded."""
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        c = case()
        got = Program(self.generated/'halves.cu', self.plan, Path(self.work.name)/'sim').run(c)
        x, y, acc = c.inputs['a'], c.inputs['b'], c.inputs['acc']
        s = x * y + x
        s = s - y / (x + np.float16(3.0))
        want = np.where(x < y, -s, s)
        np.testing.assert_array_equal(np.asarray(got['out'][:len(x)]).view(np.uint16), want.view(np.uint16))
        np.testing.assert_array_equal(np.asarray(got['acc'][:len(x)]).view(np.uint16), (acc + x * y).view(np.uint16))

    def test_against_the_original(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        c = case()
        original = metal.run_original(self.source, self.plan, self.report, c)
        ours = metal.run_generated(matrix.load_host(self.generated), self.plan, c)
        for name in ('out', 'acc'):
            a = np.asarray(original[name])[:4096].astype(np.float16)
            b = np.asarray(ours[name])[:4096].astype(np.float16)
            different = np.flatnonzero(a.view(np.uint16) != b.view(np.uint16))
            self.assertEqual(len(different), 0, f'{name}: {len(different)} differ, e.g. {a[different[:3]]} vs {b[different[:3]]}')


if __name__ == '__main__':
    unittest.main()
