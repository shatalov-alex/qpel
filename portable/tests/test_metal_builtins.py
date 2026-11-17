"""Metal spellings llama.cpp's kernels use (IMPORT.md): sign(x) (1, -1, else +0, as Apple GPUs return it;
kernel_geglu_erf's erf_approx), a buffer declared `device const` but written through a cast (kernel_tri's
dst), and a bool converted to half (kernel_tri's mask). A kernel with all three runs on the Apple GPU against
qpel's Metal (the same bits) and as qpel's CUDA on the CPU simulator (against NumPy)."""
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
kernel void builtins(device const float* x [[buffer(0)]], device const float* signs [[buffer(1)]],
                     device half* positive [[buffer(2)]], constant uint& n [[buffer(3)]],
                     uint i [[thread_position_in_grid]]) {
  if (i >= n) return;
  device float* out = (device float*) signs;
  out[i] = sign(x[i]);
  positive[i] = static_cast<half>(x[i] > 0.0f);
}
"""


def values():
    import numpy as np
    special = np.array([0.0, -0.0, np.nan, np.inf, -np.inf, 3.5, -2.25], np.float32)  # (no subnormals: Apple GPUs flush them)
    rng = np.random.default_rng(18)
    return np.concatenate([special, rng.standard_normal(57).astype(np.float32)])


def metal_sign(x):
    import numpy as np
    out = np.where(x > 0, 1.0, np.where(x < 0, -1.0, 0.0)).astype(np.float32)  # (+0 for +-0 and NaN)
    return out


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class MetalBuiltins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends.clang_ast import metal_available
        if not metal_available():
            raise unittest.SkipTest('Xcode Metal compiler required to import Metal')
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-metal-builtins-')
        work = Path(cls.work.name)
        cls.source = work/'builtins.metal'
        cls.source.write_text(METAL)
        cls.program, cls.report = import_source(cls.source, kernels=['builtins'], module='builtins')
        (work/'p.json').write_text(json.dumps(cls.program))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def case(self):
        import numpy as np
        from differential.simulate import Case
        x = values()
        return Case({'n': (len(x), 1, 1)}, {'x': x, 'signs': np.zeros(len(x), np.float32)}, {}, 'specials'), x

    def test_imported(self):
        validate(self.program)
        text = print_program(self.program)
        self.assertIn('signs: buffer<f32, write>', text)
        self.assertTrue(any('declared const but written through a cast' in a for a in self.report['adaptations']))

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        case, x = self.case()
        got = Program(self.generated/'builtins.cu', self.plan, Path(self.work.name)/'sim').run(case)
        np.testing.assert_array_equal(np.asarray(got['signs'][:len(x)]).view(np.uint32), metal_sign(x).view(np.uint32))
        np.testing.assert_array_equal(np.asarray(got['positive'][:len(x)]), (x > 0).astype(np.float16))

    def test_against_the_original(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        case, x = self.case()
        original = metal.run_original(self.source, self.plan, self.report, case)
        ours = metal.run_generated(matrix.load_host(self.generated), self.plan, case)
        for name in ('signs', 'positive'):
            a, b = np.asarray(original[name])[:len(x)], np.asarray(ours[name])[:len(x)]
            np.testing.assert_array_equal(a.view(np.uint32 if a.dtype == np.float32 else np.uint16),
                                          b.view(np.uint32 if b.dtype == np.float32 else np.uint16), err_msg=name)
        np.testing.assert_array_equal(np.asarray(original['signs'])[:len(x)].view(np.uint32), metal_sign(x).view(np.uint32))


if __name__ == '__main__':
    unittest.main()
