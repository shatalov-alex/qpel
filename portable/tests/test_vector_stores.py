"""qpel_opt.vector_stores (CORE.md, Optimisation passes): N consecutive statements of one block storing N
consecutive elements of one kernel buffer, `b[N i] = v0; ...; b[N i + N - 1] = v(N-1);`, are emitted as one
aligned N-wide store, with the element stores as the fallback. The wide store evaluates every value first, so
only values that read no memory, of the buffer's element type, qualify; anything else stays element by
element. The results are the IR's either way: on the CPU simulator and the Apple GPU against NumPy."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate, Lowering  # noqa: E402
from qpel_text import parse_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402
import qpel_opt  # noqa: E402

TEXT = '''module vector_stores {
  kernel boxes(x: buffer<f32, read> @0, b: buffer<i32, write> @1, c: buffer<f32, write> @2, d: buffer<u32, write> @3, e: buffer<u32, write> @4, n: shape3 @5) grid1d {
    let i: u32 = qpel.index;
    if (i >= n.x) {
      return;
    }
    let v: f32 = x[i];
    let k: i32 = cast<i32>(v);
    b[i * 4u] = k - 1;
    b[(i * 4u) + 1u] = k;
    b[(i * 4u) + 2u] = k + 1;
    b[(i * 4u) + 3u] = 7;
    c[i * 2u] = v * 2.0f;
    c[(i * 2u) + 1u] = v;
    d[(i * 2u) + 1u] = i;
    d[i * 2u] = i + 1u;
    e[i * 2u] = i;
    e[(i * 2u) + 1u] = cast<u32>(x[i]);
  }
}
pipeline vector_stores_launch {
  shape n;
  input x: buffer<f32, read> in n;
  output b: buffer<i32, write> in n * 4;
  output c: buffer<f32, write> in n * 2;
  output d: buffer<u32, write> in n * 2;
  output e: buffer<u32, write> in n * 2;
  dispatch boxes over n block (64) (x = x, b = b, c = c, d = d, e = e, n = n);
}
'''


def case(n):
    import numpy as np
    from differential.simulate import Case
    x = np.random.default_rng(n).uniform(0, 1000, n).astype(np.float32)
    return Case({'n': (n, 1, 1)}, {'x': x}, {}, f'n={n}'), x


def check(got, x):
    import numpy as np
    n, k = len(x), x.astype(np.int32)
    i = np.arange(n, dtype=np.uint32)
    np.testing.assert_array_equal(np.asarray(got['b'])[:4 * n].reshape(n, 4), np.stack([k - 1, k, k + 1, np.full(n, 7, np.int32)], 1))
    np.testing.assert_array_equal(np.asarray(got['c'], np.float32)[:2 * n].reshape(n, 2), np.stack([x * np.float32(2), x], 1))
    np.testing.assert_array_equal(np.asarray(got['d'], np.uint32)[:2 * n].reshape(n, 2), np.stack([i + 1, i], 1))
    np.testing.assert_array_equal(np.asarray(got['e'], np.uint32)[:2 * n].reshape(n, 2), np.stack([i, x.astype(np.uint32)], 1))


class Detection(unittest.TestCase):
    def test_only_pure_consecutive_stores(self):
        program = parse_program(TEXT)
        validate(program)
        found = qpel_opt.vector_stores(program, Lowering(program).expression_types)
        nodes = {n['id']: n for n in program['nodes']}
        stored = sorted((nodes[base['links']['declaration']]['attributes']['name'], length) for base, _, _, length, _ in found.values())
        # b: four at once; c: two; d's stores are out of order; e's second value reads memory
        self.assertEqual(stored, [('b', 4), ('c', 2)])


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-vector-stores-')
        work = Path(cls.work.name)
        (work/'p.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_spelled(self):
        cuda = (self.generated/'vector_stores.cu').read_text()
        self.assertEqual(cuda.count('QPEL_VECTOR_STORE(QPEL_VEC4_I32, b, '), 1)
        self.assertEqual(cuda.count('QPEL_VECTOR_STORE(QPEL_VEC2_F32, c, '), 1)
        self.assertEqual(cuda.count('QPEL_VECTOR_STORE('), 2 + cuda.count('#define QPEL_VECTOR_STORE('))
        self.assertIn('reinterpret_cast<device V*>', (self.generated/'vector_stores.metal').read_text())

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'vector_stores.cu', self.plan, Path(self.work.name)/'sim')
        for n in (1, 70, 300):
            c, x = case(n)
            check(program.run(c), x)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for n in (1, 70, 300):
            c, x = case(n)
            check(metal.run_generated(host, self.plan, c), x)


if __name__ == '__main__':
    unittest.main()
