"""IR version 4: thread-private local arrays, compare-and-swap and inout resources."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import Lowering, validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402

PROGRAM = '''module v4 {
  fn first_free(slots: buffer<u32, atomic>, start: u32, size: u32) -> u32 {
    let slot: u32 = start;
    while (true) {
      if (atomic.cas(slots + slot, 0u, 1u) == 0u) {
        return slot;
      }
      slot = (slot + 1u) % size;
    }
  }
  kernel claim(x: buffer<u32, read> @0, slots: buffer<u32, atomic> @1, table: buffer<u32, write> @2, n: shape3 @3) grid1d {
    if (qpel.index < ((n.x * n.y) * n.z)) {
      let p: array<u32, 3> = [x[qpel.index], 5u, 0u];
      for (let k: i32 = 0; k < 3; k = k + 1) {
        p[k] = p[k] + cast<u32>(k);
      }
      table[qpel.index] = (p[0] ^ p[2]) + first_free(slots, qpel.index, (n.x * n.y) * n.z);
    }
  }
}
pipeline v4_launch {
  input x: buffer<u32, read> in n;
  inout slots: buffer<u32, atomic> in n;
  inout table: buffer<u32, write> in n;
  shape n;
  dispatch claim over n block (64) (x = x, slots = slots, table = table, n = n);
}
'''


def program(text=PROGRAM):
    return parse_program(text)


class Version4(unittest.TestCase):
    def test_text_round_trip(self):
        p = program()
        self.assertEqual(p['version'], 4)
        self.assertEqual(print_program(parse_program(print_program(p))), print_program(p))
        validate(p)

    def test_version_4_constructs_need_version_4(self):
        p = dict(program(), version=3, semantics='qpel-portable-core-v3')
        with self.assertRaisesRegex(ValueError, 'declare version 4'):
            validate(p)

    def test_lowering(self):
        lowering = Lowering(program())
        text = lowering.emit(lowering.nodes[lowering.program['roots']['module']])
        self.assertIn('QPEL_U32 p[3] = {x[qpel_index], 5u, 0u};', text)
        self.assertIn('QPEL_CAS(', text)
        self.assertIn('atomic_cas', lowering.features())

    def test_an_array_is_only_indexed(self):
        bad = PROGRAM.replace('table[qpel.index] = (p[0] ^ p[2])', 'let q: u32 = first_free(slots, 0u, 1u);\n      table[qpel.index] = (p[0] ^ p[2])')
        validate(parse_program(bad))  # still fine: arrays are not involved
        with self.assertRaisesRegex(ValueError, 'ARRAY_USE'):
            validate(parse_program(PROGRAM.replace('p[k] = p[k] + cast<u32>(k);', 'p = p;')))

    def test_array_values_and_bounds(self):
        with self.assertRaisesRegex(ValueError, 'ARRAY_VALUE'):
            validate(parse_program(PROGRAM.replace('[x[qpel.index], 5u, 0u]', '[x[qpel.index], 5u]')))
        with self.assertRaisesRegex(ValueError, 'ARRAY_BOUNDS'):
            validate(parse_program(PROGRAM.replace('(p[0] ^ p[2])', '(p[0] ^ p[3])')))
        with self.assertRaisesRegex(ValueError, 'Invalid array type'):
            validate(parse_program(PROGRAM.replace('array<u32, 3>', 'array<u32, 2000>')))

    def test_endless_loop_counts_as_return(self):
        validate(program())  # first_free ends with while (true) { ... return ... }
        with self.assertRaisesRegex(ValueError, 'MISSING_RETURN'):
            validate(parse_program(PROGRAM.replace('slot = (slot + 1u) % size;', 'slot = (slot + 1u) % size;\n      break;')))
        with self.assertRaisesRegex(ValueError, 'MISSING_RETURN'):
            validate(parse_program(PROGRAM.replace('while (true)', 'while (slot < size)')))

    def test_inout_is_ready_and_returned(self):
        plan = Lowering(program()).plan()
        kinds = {r['name']: r['kind'] for r in plan['resources']}
        self.assertEqual((kinds['slots'], kinds['table']), ('inout', 'inout'))
        from generate_cuda_host import generate as cuda_host
        from generate_metal_host import generate as metal_host
        for host in (cuda_host(plan, b''), metal_host(plan, b'')):
            self.assertIn("'slots'", host)
            self.assertIn("return {'slots': buffers['slots'], 'table': buffers['table']}", host)


@unittest.skipUnless(shutil.which('clang') and shutil.which('clang++'), 'clang required')
class Importing(unittest.TestCase):
    def import_text(self, text, suffix='.cu', **options):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/('k' + suffix)
            path.write_text(text)
            return import_source(path, **options)

    def test_local_arrays_and_cas(self):
        program_, report = self.import_text('''
#include <limits>
__global__ void k(const unsigned int* x, unsigned int* slots, unsigned int* y, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) {
    unsigned int v[4] = {x[i]};
    unsigned int w[2];
    v[x[i] % 4u] += 1u;
    unsigned int prev = atomicCAS(&slots[i], std::numeric_limits<unsigned int>::max(), v[1]);
    y[i] = prev + v[0] + w[1];
  }
}''')
        text = print_program(program_)
        self.assertIn('let v: array<u32, 4> = [x[qpel.index], 0u, 0u, 0u];', text)
        self.assertIn('let w: array<u32, 2> = [0u, 0u];', text)
        self.assertIn('atomic.cas(slots + qpel.index, 4294967295_u32, v[1])', text)
        self.assertTrue(any('local array w had no initializer' in a for a in report['adaptations']))

    def test_shared_and_64_bit_atomics_are_refused(self):
        from frontends.c_import import Unsupported
        with self.assertRaisesRegex(Unsupported, 'static or constant-space array'):
            self.import_text('''__global__ void k(unsigned int* y, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  static unsigned int cache[64];
  if (i < n) y[i] = cache[0];
}''')
        with self.assertRaisesRegex(Unsupported, 'Metal has no compare-and-swap beyond 32 bits'):
            self.import_text('''__global__ void k(unsigned long long* y, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) atomicCAS(&y[i], 0ull, 1ull);
}''')

    def test_resource_kinds_can_be_given(self):
        program_, _ = self.import_text('''__global__ void k(unsigned int* t, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) t[i] = t[i] + 1u;
}''', resources={'t': 'inout'})
        self.assertIn('inout t: buffer<u32, write> in n;', print_program(program_))


if __name__ == '__main__':
    unittest.main(verbosity=2)
