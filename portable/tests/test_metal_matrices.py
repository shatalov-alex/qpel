"""Metal's matrix types (IMPORT.md): half4x4, float2x4 and the others read as arrays of their C * R scalars,
column-major as Metal stores them. A pointer to one is a buffer of its scalars, a local or a `thread T &`
parameter an array, m[i][j] element i R + j, and a matrix assigned, converted or stored through a pointer
one element at a time. A kernel written with llama.cpp's kernel_mul_mm's idioms (a float4x4 dequantised into
a half4x4 by a template helper, a half2x4 stored into threadgroup memory) runs on the Apple GPU against
the original, and as qpel's CUDA on the CPU simulator against numpy."""
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

METAL = r'''
#include <metal_stdlib>
using namespace metal;
template <typename type4x4>
void dequantize_f32(device const float4x4 * src, short il, thread type4x4 & reg) {
    reg = (type4x4)(*src);
}
kernel void mats(device const float* a [[buffer(0)]], device const float* b [[buffer(1)]], device half* out [[buffer(2)]],
                 device half* pairs [[buffer(3)]], constant uint& n [[buffer(4)]],
                 uint group [[threadgroup_position_in_grid]], ushort tiitg [[thread_index_in_threadgroup]]) {
    threadgroup half tile[64 * 8];
    const uint i = group * 64 + tiitg;
    if (group * 64 >= n) return;
    half4x4 temp_a;
    dequantize_f32((device const float4x4 *)(a) + i, 0, temp_a);
    for (short k = 0; k < 16; k++) {
        out[i * 16 + k] = temp_a[k/4][k%4] + (half)1.0f;
    }
    *(threadgroup half2x4 *)(tile + 8 * tiitg) = (half2x4)(*((device const float2x4 *) (b + 8 * i)));
    threadgroup_barrier(mem_flags::mem_threadgroup);
    for (short k = 0; k < 8; k++) {
        pairs[i * 8 + k] = tile[8 * ((tiitg + 1) % 64) + k];
    }
}
'''
GEOMETRY = {'groups': ['2', '1', '1'], 'block': [64, 1, 1]}
N = 128  # two groups of 64 threads


def case():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(20)
    return Case({'size': (N * 16, 1, 1)}, {'a': rng.uniform(-4, 4, N * 16).astype(np.float32),
                                          'b': rng.uniform(-4, 4, N * 16).astype(np.float32)}, {'n': N}, 'mats')


def expected(c):
    import numpy as np
    a, b = c.inputs['a'], c.inputs['b']
    out = (a[:N * 16].astype(np.float16) + np.float16(1.0))
    tiles = b[:N * 8].astype(np.float16).reshape(N // 64, 64, 8)
    pairs = np.roll(tiles, -1, axis=1).reshape(-1)
    return out, pairs


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class MetalMatrices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends.clang_ast import metal_available
        if not metal_available():
            raise unittest.SkipTest('Xcode Metal compiler required to import Metal')
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-metal-matrices-')
        work = Path(cls.work.name)
        cls.source = work/'mats.metal'
        cls.source.write_text(METAL)
        cls.program, cls.report = import_source(cls.source, kernels=['mats'], module='mats', geometry=GEOMETRY)
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
        self.assertIn('let temp_a: array<f16, 16>', text)
        self.assertIn('ref reg: array<f16, 16>', text)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        c = case()
        got = Program(self.generated/'mats.cu', self.plan, Path(self.work.name)/'sim').run(c)
        out, pairs = expected(c)
        np.testing.assert_array_equal(np.asarray(got['out'][:N * 16]).view(np.uint16), out.view(np.uint16))
        np.testing.assert_array_equal(np.asarray(got['pairs'][:N * 8]).view(np.uint16), pairs.view(np.uint16))

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
        original = metal.run_original(self.source, self.plan, self.report, c, whole_groups=True)
        ours = metal.run_generated(matrix.load_host(self.generated), self.plan, c)
        out, pairs = expected(c)
        for name, want, count in (('out', out, N * 16), ('pairs', pairs, N * 8)):
            a = np.asarray(original[name])[:count].astype(np.float16)
            b = np.asarray(ours[name])[:count].astype(np.float16)
            np.testing.assert_array_equal(b.view(np.uint16), a.view(np.uint16), err_msg=name)
            np.testing.assert_array_equal(a.view(np.uint16), want.view(np.uint16), err_msg=name)


if __name__ == '__main__':
    unittest.main()
