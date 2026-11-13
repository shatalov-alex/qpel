"""IR version 4 shared memory and barriers: text, rules, both backends (CPU simulator with threads, Apple GPU), both importers."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

PROGRAM = '''module tiles {
  kernel tile_sums(x: buffer<f32, read> @0, y: buffer<f32, write> @1, counts: buffer<i32, write> @2, n: shape3 @3) groups {
    let cache: shared<f32, 64>;
    let pos: shared<vec<f32, 2>, 64>;
    let i: u32 = (qpel.group.x * qpel.group_size.x) + qpel.local.x;
    let v: f32 = 0.0f;
    if (i < ((n.x * n.y) * n.z)) {
      v = x[i];
    }
    cache[qpel.local.x] = v;
    pos[qpel.local.x] = vec<f32, 2>(v, 1.0f);
    qpel.barrier();
    let total: f32 = 0.0f;
    for (let k: u32 = 0u; k < qpel.group_size.x; k = k + 1u) {
      total = total + (cache[k] * pos[(k + 1u) % qpel.group_size.x].y);
    }
    let big: i32 = qpel.barrier_count(v > 2.0f);
    if (i < ((n.x * n.y) * n.z)) {
      y[i] = total - v;
      counts[i] = big;
    }
  }
}
pipeline tiles_launch {
  shape n;
  input x: buffer<f32, read> in n;
  output y: buffer<f32, write> in n;
  output counts: buffer<i32, write> in n;
  dispatch tile_sums over n block (64, 1, 1) groups ("(W*H*N + 63)/64", "1", "1") (x = x, y = y, counts = counts, n = n);
}
'''
BARRIER = '    qpel.barrier();\n'


def oracle(x):
    import numpy as np
    n = len(x)
    m = -(-n // 64) * 64
    v = np.zeros(m, np.float32)
    v[:n] = x
    y, counts = np.zeros(n, np.float32), np.zeros(n, np.int32)
    for start in range(0, m, 64):
        block = v[start:start + 64]
        total = np.float32(0)
        for k in range(64):  # cache[k] * pos[k + 1].y in order; pos[].y is 1
            total = np.float32(total + np.float32(block[k] * np.float32(1)))
        big = int((block > 2).sum())
        for j in range(64):
            if start + j < n:
                y[start + j], counts[start + j] = np.float32(total - block[j]), big
    return y, counts


class Rules(unittest.TestCase):
    def test_round_trip(self):
        program = parse_program(PROGRAM)
        validate(program)
        self.assertEqual(program['version'], 4)
        self.assertEqual(print_program(parse_program(print_program(program))), print_program(program))

    def test_barriers_are_reached_by_every_thread(self):
        cases = {
            'under a condition of the thread': (BARRIER, '    if (i < 7u) {\n      qpel.barrier();\n    }\n'),
            'after a return some threads take': (BARRIER, '    if (v > 1.0f) {\n      return;\n    }\n' + BARRIER),
            'in a loop some threads leave early': (BARRIER, '    for (let r: u32 = 0u; r < 4u; r = r + 1u) {\n'
                                                            '      if (v > 1.0f) {\n        break;\n      }\n'
                                                            '      qpel.barrier();\n    }\n'),
            'under a condition read from shared memory': (
                '    let big: i32 = qpel.barrier_count(v > 2.0f);',
                '    let big: i32 = 0;\n    if (cache[3u] > 0.0f) {\n      big = qpel.barrier_count(v > 2.0f);\n    }'),
        }
        for label, (old, new) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, 'BARRIER_UNIFORM'):
                validate(parse_program(PROGRAM.replace(old, new)))
        uniform = PROGRAM.replace(BARRIER, '    for (let r: u32 = 0u; r < qpel.group_size.x; r = r + 1u, v = v + 0.0f) {\n'
                                           '      if (qpel.group.x > 3u) {\n        break;\n      }\n'
                                           '      qpel.barrier();\n    }\n')
        validate(parse_program(uniform))  # group-wide conditions; a loop with two steps

    def test_where_shared_memory_and_barriers_go(self):
        with self.assertRaisesRegex(ValueError, 'SHARED_PLACE'):
            validate(parse_program(PROGRAM.replace(
                '    let total: f32 = 0.0f;', '    if (true) {\n      let other: shared<f32, 64>;\n    }\n    let total: f32 = 0.0f;')))
        with self.assertRaisesRegex(ValueError, 'Invalid shared type'):
            validate(parse_program(PROGRAM.replace('shared<f32, 64>', 'shared<f32, 5000>')))
        with self.assertRaisesRegex(ValueError, 'SHARED_PLACE|BARRIER_CONTEXT'):  # both belong to Groups kernels
            validate(parse_program(PROGRAM.replace(') groups {', ') grid1d {').replace(
                'block (64, 1, 1) groups ("(W*H*N + 63)/64", "1", "1")', 'block (64)')))


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-shared-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(PROGRAM)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def cases(self):
        import numpy as np
        from differential.simulate import Case
        for n in (1, 5, 64, 200):
            x = np.random.default_rng(n).uniform(-1, 4, n).astype(np.float32)
            yield Case({'n': (n, 1, 1)}, {'x': x}, {}, str(n)), x

    def test_cpu_simulator_runs_a_group_together(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'tiles.cu', self.plan, Path(self.work.name)/'sim')
        self.assertTrue(program.threaded)
        for case, x in self.cases():
            out, (y, counts) = program.run(case), oracle(x)
            np.testing.assert_array_equal(out['y'], y)
            np.testing.assert_array_equal(out['counts'], counts)

    def test_apple_gpu(self):
        try:
            import numpy as np
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case, x in self.cases():
            out, (y, counts) = metal.run_generated(host, self.plan, case), oracle(x)
            np.testing.assert_array_equal(out['y'], y)
            np.testing.assert_array_equal(out['counts'], counts)

    def test_both_importers_give_the_program_back(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from frontends.roundtrip import canonical
        original = canonical(parse_program(PROGRAM))
        for source in ('tiles.cu', 'tiles.metal'):
            if source.endswith('.metal') and not clang_ast.metal_available():
                unavailable(self, 'metal', 'Metal toolchain required')
            with self.subTest(source):
                program, _ = import_source(self.generated/source, plan=self.plan, module='tiles')
                self.assertEqual(canonical(program), original)


@unittest.skipUnless(shutil.which('clang') and shutil.which('clang++'), 'clang required')
class CudaImport(unittest.TestCase):
    def test_shared_memory_cooperative_groups_and_counts(self):
        from frontends.c_import import import_source
        source = '''
#include <cooperative_groups.h>
namespace cg = cooperative_groups;
__global__ void k(const float* x, int* y, unsigned int n) {
  auto block = cg::this_thread_block();
  unsigned int i = block.group_index().x * 64 + block.thread_index().x;
  __shared__ float cache[64];
  cache[block.thread_rank()] = i < n ? x[i] : 0.0f;
  block.sync();
  int s = 0;
  for (int r = 0; r < 2; r++, s += 2) {
    int votes = __syncthreads_count(cache[(block.thread_rank() + 1) % 64] > 1.0f);
    if (i < n) y[i] = votes + s;
    __syncthreads();
  }
}'''
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'k.cu'
            path.write_text(source)
            program, _ = import_source(path)
        validate(program)
        text = print_program(program)
        self.assertIn('let cache: shared<f32, 64>;', text)
        self.assertIn('qpel.barrier();', text)
        self.assertIn('qpel.barrier_count(cache[', text)
        self.assertIn('r = r + 1, s = s + 2)', text)
        self.assertIn('qpel.group.x', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
