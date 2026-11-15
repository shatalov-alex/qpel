"""IR version 6 atomics: max, and, or, xor and exchange on u32/i32; load, store, add and exchange on
f32; add, min and max on u64 as statements (Metal has 64-bit min/max without a result and no 64-bit
add, which qpel's Metal makes of two 32-bit adds). Rules, the CPU simulator, the Metal runner on the
CPU, the Apple GPU, and both importers giving the program back."""
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

PROGRAM = '''module atomics6 {
  kernel scatter(x: buffer<f32, read> @0, slot: buffer<u32, read> @1, sums: buffer<f32, atomic> @2, swap: buffer<f32, atomic> @3, previous: buffer<f32, write> @4, n: shape3 @5) grid1d {
    let i: u32 = qpel.index;
    if (i < ((n.x * n.y) * n.z)) {
      atomic.add(sums + (slot[i] % 16u), x[i]);
      let old: f32 = atomic.exchange(swap + i, x[i]);
      previous[i] = old + atomic.load(swap + i);
    }
  }
  kernel bits(keys: buffer<u32, read> @0, flags: buffer<u32, atomic> @1, top: buffer<i32, atomic> @2, n: shape3 @3) grid1d {
    let i: u32 = qpel.index;
    if (i < ((n.x * n.y) * n.z)) {
      let v: u32 = keys[i];
      atomic.or(flags + (v % 4u), 1u << (v % 32u));
      atomic.and(flags + (4u + (v % 2u)), ~(1u << ((v >> 5u) % 32u)));
      atomic.xor(flags + 6u, v);
      atomic.max(flags + 7u, v);
      atomic.max(top, cast<i32>(v));
    }
  }
  kernel wide_add(keys: buffer<u32, read> @0, total: buffer<u64, atomic> @1, n: shape3 @2) grid1d {
    let i: u32 = qpel.index;
    if (i < ((n.x * n.y) * n.z)) {
      atomic.add(total, cast<u64>(keys[i]) * 1000003_u64);
    }
  }
  kernel wide_order(keys: buffer<u32, read> @0, best: buffer<u64, atomic> @1, n: shape3 @2) grid1d {
    let i: u32 = qpel.index;
    if (i < ((n.x * n.y) * n.z)) {
      let key: u64 = (cast<u64>(keys[i]) * 4294967296_u64) + cast<u64>(i);
      atomic.max(best, key);
      atomic.min(best + 1u, key);
    }
  }
}
pipeline atomics6_launch {
  shape n;
  input x: buffer<f32, read> in n;
  input slot: buffer<u32, read> in n;
  input keys: buffer<u32, read> in n;
  inout sums: buffer<f32, atomic> [16];
  inout swap: buffer<f32, atomic> in n;
  output previous: buffer<f32, write> in n;
  inout flags: buffer<u32, atomic> [8];
  inout top: buffer<i32, atomic> [1];
  inout total: buffer<u64, atomic> [1];
  inout best: buffer<u64, atomic> [2];
  dispatch scatter over n block (64) (x = x, slot = slot, sums = sums, swap = swap, previous = previous, n = n);
  dispatch bits over n block (64) (keys = keys, flags = flags, top = top, n = n);
  dispatch wide_add over n block (64) (keys = keys, total = total, n = n);
  dispatch wide_order over n block (64) (keys = keys, best = best, n = n);
}
'''


def oracle(case):
    """Every result is independent of the order of the atomic operations."""
    import numpy as np
    x, slot, keys = (case.inputs[k] for k in ('x', 'slot', 'keys'))
    sums = case.inputs['sums'].astype(np.float64)
    np.add.at(sums, slot % 16, x.astype(np.float64))  # exact: quarter-integers (cases.exact_sums)
    swap = case.inputs['swap']
    flags = [int(f) for f in case.inputs['flags']]
    top = int(case.inputs['top'][0])
    total = int(case.inputs['total'][0])
    best = [int(b) for b in case.inputs['best']]
    for i, v in enumerate(int(k) for k in keys):
        flags[v % 4] |= 1 << (v % 32)
        flags[4 + v % 2] &= ~(1 << ((v >> 5) % 32)) & 0xffffffff
        flags[6] ^= v
        flags[7] = max(flags[7], v)
        top = max(top, v - 2**32 if v >= 2**31 else v)
        total = (total + v * 1000003) % 2**64
        key = v * 2**32 + i
        best = [max(best[0], key), min(best[1], key)]
    return {'sums': sums.astype(np.float32), 'swap': x, 'previous': (swap + x).astype(np.float32),
            'flags': np.array(flags, np.uint32), 'top': np.array([top], np.int32),
            'total': np.array([total], np.uint64), 'best': np.array(best, np.uint64)}


class Rules(unittest.TestCase):
    def program(self, text=PROGRAM):
        return parse_program(text)

    def test_text_round_trip_and_version(self):
        program = self.program()
        self.assertEqual(print_program(program), PROGRAM)
        self.assertEqual(program['version'], 6)
        for target in ('cuda', 'metal'):
            validate(program, target)

    def test_version_5_cannot_use_them(self):
        program = self.program()
        program.update(version=5, semantics='qpel-portable-core-v5')
        with self.assertRaisesRegex(ValueError, 'IR version 5 cannot use'):
            validate(program)

    def reject(self, old, new, code):
        self.assertIn(old, PROGRAM)
        with self.assertRaisesRegex(ValueError, code):
            validate(self.program(PROGRAM.replace(old, new)))

    def test_float_atomics_have_no_min(self):
        self.reject('atomic.add(sums + (slot[i] % 16u), x[i]);', 'atomic.min(sums + (slot[i] % 16u), x[i]);',
                    'ATOMIC_OPERATION')

    def test_wide_atomics_give_no_value(self):
        self.reject('      atomic.max(best, key);', '      let was: u64 = atomic.max(best, key);', 'ATOMIC_RESULT')

    def test_wide_atomics_have_no_load(self):
        self.reject('      atomic.max(best, key);', '      atomic.store(best, key);', 'ATOMIC_OPERATION')

    def test_no_wide_add_with_min_or_max(self):
        self.reject('      atomic.min(best + 1u, key);', '      atomic.min(best + 1u, key);\n      atomic.add(best, key);',
                    'ATOMIC_MIX')

    def test_i64_is_not_atomic(self):
        self.reject('inout total: buffer<u64, atomic> [1];', 'inout total: buffer<i64, atomic> [1];', '')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-atomics6-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(PROGRAM)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def cases(self):
        from differential import cases
        return cases.generic(self.plan, 6, shapes=[(1, 1, 1), (40, 1, 1), (64, 1, 1), (17, 3, 2), (300, 1, 1)])

    def check(self, got, case):
        import numpy as np
        for name, want in oracle(case).items():
            np.testing.assert_array_equal(got[name], want, err_msg=f'{name} ({case.label})')

    def test_spellings(self):
        cuda = (self.generated/'atomics6.cu').read_text()
        metal = (self.generated/'atomics6.metal').read_text()
        for text, source in (('QPEL_ADD_U64(total, ', cuda), ('QPEL_LOAD_F32((swap + (QPEL_U32)(i)))', metal),
                             ('QPEL_PARAM(QPEL_ATOMIC_F32, sums, 2)', metal), ('QPEL_MIN_U64((best + ', cuda)):
            self.assertTrue(text in source, text)

    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'atomics6.cu', self.plan, Path(self.work.name)/'sim')
        for case in self.cases():
            self.check(program.run(case), case)

    def test_metal_on_the_cpu(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from differential import metal_cpu
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        source = self.generated/'atomics6.metal'
        _, report = import_source(source, plan=self.plan, module='atomics6')
        runner = metal_cpu.program(source, self.plan, report, Path(self.work.name)/'metal_cpu', timeout=60)
        for case in self.cases():
            self.check(runner.run(case), case)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in self.cases():
            self.check(metal.run_generated(host, self.plan, case), case)

    def test_both_importers_give_the_program_back(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from frontends.roundtrip import canonical
        original = canonical(parse_program(PROGRAM))
        for source in ('atomics6.cu', 'atomics6.metal'):
            if source.endswith('.metal') and not clang_ast.metal_available():
                unavailable(self, 'metal', 'Metal toolchain required')
            with self.subTest(source):
                program, _ = import_source(self.generated/source, plan=self.plan, module='atomics6')
                self.assertEqual(canonical(program), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
