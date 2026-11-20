"""qpel_opt.reused_loads (CORE.md, Optimisation passes): a loop whose condition reads an atomic location
and whose body first reads the same location again, `while (atomic.load(p + n) != n) { n = atomic.load(p + n); }`
(the union-find of SAM 2's connected components), reads it once: the condition's value is the body's. Under
the relaxed order the two reads may return the same write, so that is one of the IR's executions. A loop whose
body does anything before the second read, or reads another address, is left alone. The results are the IR's:
on the CPU simulator and the Apple GPU against NumPy, and the generated loop reads back as the same IR."""
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate, Lowering  # noqa: E402
from qpel_text import parse_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402
import qpel_opt  # noqa: E402

TEXT = '''module reused_loads {
  fn root(p: buffer<u32, atomic>, n: u32) -> u32 {
    while (atomic.load(p + n) != n) {
      n = atomic.load(p + n);
    }
    return n;
  }
  fn depth(p: buffer<u32, atomic>, n: u32) -> u32 {
    let steps: u32 = 0u;
    while (atomic.load(p + n) != n) {
      steps = steps + 1u;
      n = atomic.load(p + n);
    }
    return steps;
  }
  fn root_other(p: buffer<u32, atomic>, n: u32) -> u32 {
    while (atomic.load(p + n) != n) {
      n = atomic.load(p + (n - 0u));
    }
    return n;
  }
  kernel init(p: buffer<u32, atomic> @0, s: shape3 @1) grid1d {
    let i: u32 = qpel.index;
    if (i >= s.x) {
      return;
    }
    if ((i & 7u) == 0u) {
      atomic.store(p + i, i);
    } else {
      atomic.store(p + i, i - 1u);
    }
  }
  kernel walk(p: buffer<u32, atomic> @0, roots: buffer<u32, write> @1, depths: buffer<u32, write> @2, others: buffer<u32, write> @3, s: shape3 @4) grid1d {
    let i: u32 = qpel.index;
    if (i >= s.x) {
      return;
    }
    roots[i] = root(p, i);
    depths[i] = depth(p, i);
    others[i] = root_other(p, i);
  }
}
pipeline reused_loads_launch {
  shape s;
  output p: buffer<u32, atomic> in s;
  output roots: buffer<u32, write> in s;
  output depths: buffer<u32, write> in s;
  output others: buffer<u32, write> in s;
  dispatch init over s block (64) (p = p, s = s);
  dispatch walk over s block (64) (p = p, roots = roots, depths = depths, others = others, s = s);
}
'''
COUNT = 1000


def case():
    from differential.simulate import Case
    return Case({'s': (COUNT, 1, 1)}, {}, {}, f'n={COUNT}')


def check(got):
    import numpy as np
    i = np.arange(COUNT, dtype=np.uint32)
    np.testing.assert_array_equal(np.asarray(got['roots'], np.uint32)[:COUNT], i & ~np.uint32(7))
    np.testing.assert_array_equal(np.asarray(got['depths'], np.uint32)[:COUNT], i & np.uint32(7))
    np.testing.assert_array_equal(np.asarray(got['others'], np.uint32)[:COUNT], i & ~np.uint32(7))


class Detection(unittest.TestCase):
    def test_only_the_same_read_first(self):
        program = parse_program(TEXT)
        validate(program)
        found = qpel_opt.reused_loads(program, Lowering(program).expression_types)
        nodes = {n['id']: n for n in program['nodes']}
        owner = {}
        for n in nodes.values():
            if n['kind'] == 'Function':
                stack = [n['id']]
                while stack:
                    x = nodes[stack.pop()]
                    owner[x['id']] = n['attributes']['name']
                    stack.extend(i for ids in x['children'].values() for i in ids)
        # depth counts a step before its second read; root_other reads another (equal) address
        self.assertEqual(sorted(owner[w] for w in found), ['root'])
        self.assertEqual([t for _, _, t in found.values()], ['u32'])

    def test_sam2_reads_once(self):
        import json
        program = json.loads((HERE/'tests/fixtures/qpel_ir/sam2_cc/program.qpel.json').read_text())
        self.assertEqual(len(qpel_opt.reused_loads(program, Lowering(program).expression_types)), 2)  # find, find_n_compress

    def test_spelled(self):
        program = parse_program(TEXT)
        validate(program)
        work = Path(tempfile.mkdtemp(prefix='qpel-reuse-spelled-'))
        from qpel import compile_program
        import json
        (work/'p.json').write_text(json.dumps(program))
        compile_program(work/'p.json', work/'build')
        text = next((work/'build/package/generated').glob('*.qpel.h')).read_text()
        self.assertEqual(text.count('qpel_reuse_'), 3)  # declared, read in the condition, reused in the body
        self.assertEqual(text.count('QPEL_LOAD('), 2 + 2 + 1)  # depth's two, root_other's two, root's one


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-reuse-')
        work = Path(cls.work.name)
        (work/'p.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_cpu_simulator(self):
        import shutil
        if not shutil.which('clang++'):
            self.skipTest('clang++ required')
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        check(Program(self.generated/'reused_loads.cu', self.plan, Path(self.work.name)/'sim').run(case()))

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        check(metal.run_generated(matrix.load_host(self.generated), self.plan, case()))

    def test_reads_back(self):
        from frontends import roundtrip
        import json
        original = json.loads((Path(self.work.name)/'p.json').read_text())
        for suffix in ('.cu', '.metal'):
            source = next(self.generated.glob('*' + suffix))
            self.assertIsNone(roundtrip.check(original, source, self.plan, validate), suffix)


if __name__ == '__main__':
    unittest.main()
