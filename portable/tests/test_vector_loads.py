"""qpel_opt.vector_loads (CORE.md, Optimisation passes): a vector of N consecutive elements of one kernel
buffer, `vec<T, N>(b[N i], ..., b[N i + N - 1])`, is emitted as one aligned N-wide load, with the element
loads as the fallback; anything else stays element by element. The results are the IR's either way."""
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
import qpel_opt  # noqa: E402

TEXT = '''module vector_loads {
  kernel pairs(x: buffer<u32, read> @0, w: buffer<f32, read> @1, v: buffer<u32, read> @2, y: buffer<u32, write> @3, z: buffer<f32, write> @4, n: shape3 @5) grid1d {
    let i: u32 = qpel.index;
    if (i >= n.x) {
      return;
    }
    let p: vec<u32, 2> = vec<u32, 2>(x[(i * 2u) + 0u], x[(i * 2u) + 1u]);
    let q: vec<f32, 4> = vec<f32, 4>(w[(i * 4u) + 0u], w[(i * 4u) + 1u], w[(i * 4u) + 2u], w[(i * 4u) + 3u]);
    let swapped: vec<u32, 2> = vec<u32, 2>(x[(i * 2u) + 1u], x[(i * 2u) + 0u]);
    let mixed: vec<u32, 2> = vec<u32, 2>(x[(i * 2u) + 0u], v[(i * 2u) + 1u]);
    y[i] = (p.x + p.y) + (swapped.x * 0u) + (mixed.x * 0u);
    z[i] = ((q.x + q.y) + q.z) + q.w;
  }
}
pipeline vector_loads_launch {
  shape n;
  input x: buffer<u32, read> in n * 2;
  input w: buffer<f32, read> in n * 4;
  input v: buffer<u32, read> in n * 2;
  output y: buffer<u32, write> in n * 2;
  output z: buffer<f32, write> in n;
  dispatch pairs over n block (64) (x = x, w = w, v = v, y = y, z = z, n = n);
}
'''


class Detection(unittest.TestCase):
    def test_only_consecutive_elements_of_one_buffer(self):
        program = parse_program(TEXT)
        validate(program)
        lowering = Lowering(program)
        found = qpel_opt.vector_loads(program, lowering.expression_types)
        nodes = {n['id']: n for n in program['nodes']}
        named = {}
        for n in program['nodes']:
            if n['kind'] == 'Variable' and n['children'].get('value'):
                named[n['attributes']['name']] = n['children']['value'][0]
        self.assertIn(named['p'], found)
        self.assertIn(named['q'], found)
        self.assertNotIn(named['swapped'], found)  # elements out of order
        self.assertNotIn(named['mixed'], found)  # two buffers
        self.assertEqual(nodes[found[named['p']][0]['links']['declaration']]['attributes']['name'], 'x')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Emitted(unittest.TestCase):
    def test_spelled_and_run(self):
        import numpy as np
        from qpel import compile_program
        from differential.simulate import Program, Case
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            (work/'p.json').write_text(json.dumps(parse_program(TEXT)))
            compile_program(work/'p.json', work/'build')
            generated = work/'build/package/generated'
            cuda = (generated/'vector_loads.cu').read_text()
            metal = (generated/'vector_loads.metal').read_text()
            self.assertEqual(cuda.count('QPEL_VECTOR_ALIGNED(x, QPEL_VEC2_U32)'), 1)
            self.assertEqual(cuda.count('QPEL_VECTOR_ALIGNED(w, QPEL_VEC4_F32)'), 1)
            self.assertIn('reinterpret_cast<device const V*>', metal)
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'s').mkdir()
            program = Program(generated/'vector_loads.cu', plan, work/'s')
            rng = np.random.default_rng(3)
            for n in (1, 70, 300):
                x = rng.integers(0, 2**31, 2 * n, dtype=np.uint32)
                w = rng.standard_normal(4 * n).astype(np.float32)
                got = program.run(Case({'n': (n, 1, 1)}, {'x': x, 'w': w, 'v': x.copy()}, {}, f'n={n}'))
                np.testing.assert_array_equal(got['y'][:n], (x[0::2] + x[1::2]).astype(np.uint32))
                want = (((w[0::4] + w[1::4]).astype(np.float32) + w[2::4]).astype(np.float32) + w[3::4]).astype(np.float32)
                np.testing.assert_array_equal(got['z'].view(np.uint32), want.view(np.uint32))


if __name__ == '__main__':
    unittest.main()
