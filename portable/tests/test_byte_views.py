"""Byte views and unions in the importer, as llama.cpp's iq formats dequantise (IMPORT.md, Byte views): a pointer
to narrower unsigned integers into a local (`(thread const uint8_t *)&aux32`) reads shifts of the local's current
value; one into a program-scope table (`(constant uint8_t *)(iq2xxs_grid + i)`) is the element index, its bytes
shifts of the table's elements, and is set again to other elements; a local union of scalars of one width
(iq1m_scale_t: half and uint16_t) is one unsigned word, its float member a bitcast (IR version 19). The unmodified
original runs on the Metal-on-CPU runner and the Apple GPU against NumPy, qpel's CUDA on the simulator and qpel's
Metal on the Apple GPU."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import unavailable  # noqa: E402

GRID = [0x0807060504030201, 0x1817161514131211, 0xf0e0d0c0b0a09080, 0xffeeddccbbaa9988]
WORDS = [0x04030201, 0xa0b0c0d0, 0x11223344]
SOURCE = '''#include <metal_stdlib>
using namespace metal;
constexpr constant static uint64_t grid[4] = {0x0807060504030201ull, 0x1817161514131211ull, 0xf0e0d0c0b0a09080ull,
                                              0xffeeddccbbaa9988ull};
constexpr constant static uint32_t words[3] = {0x04030201u, 0xa0b0c0d0u, 0x11223344u};
typedef union { half f16; uint16_t u16; } scale_t;

kernel void bytes(device const uint * x [[buffer(0)]], device uint * out [[buffer(1)]], device float * scales [[buffer(2)]],
                  constant uint & n [[buffer(3)]], uint i [[thread_position_in_grid]]) {
  if (i >= n) return;
  uint32_t w = x[i];
  thread const uint8_t * b = (thread const uint8_t *)&w;
  uint sum = b[0] + 2u * b[1] + 3u * b[2] + 5u * b[3];
  w = w ^ 0xffffffffu;
  sum += b[i % 4u];
  thread const uint16_t * h = (thread const uint16_t *)&w;
  sum += 7u * h[1];
  constant uint8_t * g = (constant uint8_t *)(grid + (x[i] & 3u));
  for (int k = 0; k < 8; ++k) {
    sum += g[k] * uint(k + 1);
  }
  g = (constant uint8_t *)(grid + ((x[i] >> 2) & 3u));
  sum += 11u * g[i % 8u];
  constant uint8_t * v = (constant uint8_t *)(words + (x[i] % 2u));
  sum += 13u * v[5];
  out[i] = sum;
  scale_t s;
  s.u16 = (ushort)(x[i] >> 16) & 0x3bffu;
  scales[i] = s.f16;
  s.f16 = -s.f16;
  out[i] = sum ^ uint(s.u16);
}
'''


def oracle(x):
    import numpy as np
    x = x.astype(np.uint64)
    i = np.arange(x.size, dtype=np.uint64)
    byte = lambda w, k: (w >> (8 * k)) & 0xff
    sums = byte(x, 0) + 2 * byte(x, 1) + 3 * byte(x, 2) + 5 * byte(x, 3)
    w = x ^ 0xffffffff
    sums += byte(w, i % 4) + 7 * ((w >> 16) & 0xffff)
    grid = np.array(GRID, np.uint64)
    first = grid[x & 3]
    sums += sum(byte(first, k) * (k + 1) for k in range(8))
    sums += 11 * byte(grid[(x >> 2) & 3], i % 8)
    sums += 13 * byte(np.array(WORDS, np.uint64)[x % 2 + 1], 1)
    bits = (x >> 16) & 0x3bff
    scales = bits.astype(np.uint16).view(np.float16).astype(np.float32)
    return ((sums % 2**32) ^ bits ^ 0x8000).astype(np.uint32), scales


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class ByteViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends import clang_ast
        if not clang_ast.metal_available():
            raise unittest.SkipTest('no Metal toolchain (Xcode)')
        cls.folder = Path(tempfile.mkdtemp())
        cls.source = cls.folder/'bytes.metal'
        cls.source.write_text(SOURCE)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def imported(self, source=None):
        from frontends.c_import import import_source
        if source is None:
            return import_source(self.source)
        path = self.folder/'rejected.metal'
        path.write_text(source)
        return import_source(path)

    def built(self, name):
        from qpel_ir import Lowering
        from qpel import compile_program
        program, report = self.imported()
        (self.folder/f'{name}.json').write_text(json.dumps(program))
        compile_program(self.folder/f'{name}.json', self.folder/name)
        return program, report, Lowering(program).plan(), self.folder/name/'package/generated'

    def cases(self, plan):
        import numpy as np
        from differential.simulate import Case
        rng = np.random.default_rng(19)
        for n in (1, 37, 256):
            shapes = {r['name']: (n, 1, 1) for r in plan['resources'] if r['kind'] == 'shape'}
            values = {r['name']: n for r in plan['resources'] if r['kind'] == 'value'}
            yield Case(shapes, {'x': rng.integers(0, 2**32, n, dtype=np.uint32)}, values, str(n)), n

    def check(self, got, case, n):
        import numpy as np
        sums, scales = oracle(case.inputs['x'][:n])
        np.testing.assert_array_equal(np.asarray(got['out'], np.uint32)[:n], sums, err_msg=case.label)
        np.testing.assert_array_equal(np.asarray(got['scales'], np.float32)[:n].view(np.uint32), scales.view(np.uint32),
                                      err_msg=case.label)

    def test_import(self):
        from qpel_ir import validate
        from qpel_text import print_program
        program, report = self.imported()
        self.assertEqual(program['version'], 19)
        for target in ('cuda', 'metal'):
            validate(program, target)
        text = print_program(program)
        self.assertIn('bitcast<f16>(', text)
        self.assertIn('bitcast<u16>(', text)
        self.assertTrue(any('reads the bytes of table elements' in a for a in report['adaptations']))
        self.assertTrue(any('union s is one u16' in a for a in report['adaptations']))
        program.update(version=18, semantics='qpel-portable-core-v18')
        with self.assertRaisesRegex(ValueError, 'IR version 18 cannot use: bitcast between f16 and u16'):
            validate(program)

    def test_rejections(self):
        for old, new, message in (
                ('thread const uint8_t * b = (thread const uint8_t *)&w;',
                 'thread uint8_t * b = (thread uint8_t *)&w;\n  b[0] = 1;', 'does not write'),
                ('g = (constant uint8_t *)(grid + ((x[i] >> 2) & 3u));',
                 'g = (constant uint8_t *)(words + 1);', 'another element of its table'),
                ('typedef union { half f16; uint16_t u16; } scale_t;',
                 'typedef union { float f16; uint16_t u16; } scale_t;', 'struct locals')):
            self.assertIn(old, SOURCE)
            with self.assertRaisesRegex(ValueError, message):
                self.imported(SOURCE.replace(old, new, 1))

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_original_and_translation(self):
        """The original on the Metal-on-CPU runner, qpel's CUDA on the simulator."""
        from differential import metal_cpu
        from differential.simulate import Program
        program, report, plan, generated = self.built('build')
        original = metal_cpu.program(self.source, plan, report, self.folder/'cpu', timeout=60)
        (self.folder/'qpel').mkdir(exist_ok=True)
        ours = Program(generated/'bytes.cu', plan, self.folder/'qpel')
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
        from differential import matrix, metal
        program, report, plan, generated = self.built('gpu')
        host = matrix.load_host(generated)
        for case, n in self.cases(plan):
            self.check(metal.run_original(self.source, plan, report, case), case, n)
            self.check(metal.run_generated(host, plan, case), case, n)


if __name__ == '__main__':
    unittest.main(verbosity=2)
