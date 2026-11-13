"""Buffers of different sizes in one dispatch: a kernel receives every shape that sizes a buffer."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import Lowering, validate  # noqa: E402
from qpel_text import parse_program  # noqa: E402

PROGRAM = '''module tables {
  kernel count_into(keys: buffer<u32, read> @0, table: buffer<u32, atomic> @1, n: shape3 @2, slots: shape3 @3) grid1d {
    if (qpel.index < ((n.x * n.y) * n.z)) {
      let s: u32 = keys[qpel.index] % ((slots.x * slots.y) * slots.z);
      atomic.add(table + s, 1u);
    }
  }
}
pipeline tables_launch {
  shape n;
  shape slots;
  input keys: buffer<u32, read> in n;
  zeroed table: buffer<u32, atomic> in slots;
  dispatch count_into over n block (64) (keys = keys, table = table, n = n, slots = slots);
}
'''


class Pipeline(unittest.TestCase):
    def test_a_buffer_can_be_sized_by_a_shape_the_kernel_receives(self):
        program = parse_program(PROGRAM)
        validate(program)
        plan = Lowering(program).plan()
        extents = {r['name']: r.get('extent') for r in plan['resources']}
        self.assertEqual((extents['keys'], extents['table']), ('n', 'slots'))

    def test_a_buffer_may_be_sized_by_a_shape_the_kernel_does_not_receive(self):
        # as TRELLIS.2's tile keys, whose length (all tiles touched) no kernel argument gives
        text = PROGRAM.replace('shape slots;', 'shape slots;\n  shape other;').replace('in slots;', 'in other;')
        validate(parse_program(text))
        with self.assertRaisesRegex(ValueError, 'EXTENT'):
            validate(parse_program(PROGRAM.replace('in slots;', 'in keys;')))

    def test_a_fixed_length_buffer(self):
        from qpel_text import print_program
        from generate_metal_host import generate as metal_host
        from differential import cases
        text = PROGRAM.replace('zeroed table: buffer<u32, atomic> in slots;', 'zeroed table: buffer<u32, atomic> [16];')
        program = parse_program(text)
        validate(program)
        self.assertIn('zeroed table: buffer<u32, atomic> [16];', print_program(program))
        plan = Lowering(program).plan()
        self.assertEqual(next(r for r in plan['resources'] if r['name'] == 'table')['length'], 16)
        self.assertIn('torch.zeros(16,', metal_host(plan, b''))
        self.assertEqual(cases.buffer_length(next(r for r in plan['resources'] if r['name'] == 'table'), {}), 16)
        with self.assertRaisesRegex(ValueError, 'fixed-length'):
            validate(parse_program(text.replace('[16]', '[0]')))

    def test_a_scaled_buffer_holds_k_elements_per_element_of_its_extent(self):
        from qpel_text import print_program
        from generate_cuda_host import generate as cuda_host
        from generate_metal_host import generate as metal_host
        text = PROGRAM.replace('input keys: buffer<u32, read> in n;', 'input keys: buffer<u32, read> in n * 4;')
        program = parse_program(text)
        validate(program)
        self.assertEqual(program['version'], 4)
        self.assertIn('in n * 4;', print_program(program))
        plan = Lowering(program).plan()
        self.assertEqual(next(r for r in plan['resources'] if r['name'] == 'keys')['scale'], 4)
        for host in (cuda_host(plan, b''), metal_host(plan, b'')):
            self.assertIn("sizes['n'] * 4", host)
            self.assertIn('Buffer exceeds INT32_MAX elements', host)
        from differential import cases
        for c in cases.generic(plan, 1):
            self.assertEqual(c.inputs['keys'].size, 4 * cases.volume(c.shapes['n']))
        with self.assertRaisesRegex(ValueError, 'version 4'):
            validate(dict(program, version=3, semantics='qpel-portable-core-v3'))
        with self.assertRaisesRegex(ValueError, 'buffer scale'):
            validate(parse_program(text.replace('in n * 4;', 'in n * 1;')))

    def test_generic_cases_give_other_shapes_their_own_size(self):
        from differential import cases
        plan = Lowering(parse_program(PROGRAM)).plan()
        generated = cases.generic(plan, 1)
        self.assertTrue(any(c.shapes['n'] != c.shapes['slots'] for c in generated))
        for c in generated:
            self.assertEqual(c.inputs['keys'].size, cases.volume(c.shapes['n']))

    def test_the_backend_stage_list_accepts_it(self):
        from build_backends import launch_header
        plan = Lowering(parse_program(PROGRAM)).plan()
        self.assertIn("QPEL_STAGE(count_into, n, 64, keys, table, n, slots)", launch_header(plan))


@unittest.skipUnless(shutil.which('clang') and shutil.which('clang++'), 'clang required')
class Importing(unittest.TestCase):
    HOST = '''
#include <torch/extension.h>
__global__ void insert(const unsigned int N, const unsigned int M, unsigned int* table_keys,
                       unsigned int* table_values, const unsigned int* keys) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < M) {
    unsigned int slot = keys[i] % N;
    table_keys[slot] = keys[i];
    table_values[slot] = i;
  }
}
void insert_host(torch::Tensor& table_keys, torch::Tensor& table_values, const torch::Tensor& keys) {
  insert<<<(keys.size(0) + 63) / 64, 64>>>(table_keys.size(0), keys.size(0), table_keys.data_ptr<unsigned int>(),
                                          table_values.data_ptr<unsigned int>(), keys.data_ptr<unsigned int>());
}
'''

    def import_text(self, text, **options):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'k.cu'
            path.write_text(text)
            return import_source(path, **options)

    def test_sizes_come_from_the_launch_and_shared_indices(self):
        program, report = self.import_text(self.HOST)
        plan = Lowering(program).plan()
        extents = {r['name']: r.get('extent') for r in plan['resources']}
        # table_keys is sized at the launch; table_values shares its index `slot`
        self.assertEqual(extents, {'N': None, 'M': None, 'table_keys': 'N', 'table_values': 'N', 'keys': 'M'})
        self.assertEqual([s['grid'] for s in plan['stages']], ['M'])
        sources = {a['name']: a['source'] for a in report['arguments']['insert']}
        self.assertEqual((sources['N'], sources['M']), ('volume', 'volume'))
        self.assertTrue(any('N, the length of table_keys at the launch' in a for a in report['adaptations']))

    def test_a_buffer_indexed_with_two_sizes_is_refused(self):
        from frontends.c_import import Unsupported
        text = self.HOST.replace('table_values[slot] = i;', 'table_values[slot] = i;\n    table_values[i] = slot;')
        with self.assertRaisesRegex(Unsupported, 'indexed together with buffers of sizes M and N'):
            self.import_text(text)

    def test_a_vector_read_from_a_buffer_scales_it(self):
        from qpel_text import print_program
        program, report = self.import_text('''
#include <torch/extension.h>
__global__ void sum4(const int M, const int32_t* __restrict__ coords, int* out) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < M) {
    int4 c = reinterpret_cast<const int4*>(coords)[i];
    int4 d = make_int4(1, 2, 3, 4);
    d.w = c.x;
    out[i] = c.x + c.y * d.y + c.z - c.w + d.w;
  }
}
void host(const torch::Tensor& coords, torch::Tensor& out) {
  sum4<<<1, 64>>>(coords.size(0), coords.data_ptr<int32_t>(), out.data_ptr<int>());
}''')
        text = print_program(program)
        self.assertIn('let c: vec<i32, 4> = vec<i32, 4>(coords[(cast<i32>(qpel.index) * 4) + 0], coords[(cast<i32>(qpel.index) * 4) + 1]', text)
        self.assertIn('d.w = c.x;', text)
        self.assertIn('input coords: buffer<i32, read> in M * 4;', text)

    def test_without_a_launch_site_one_size_remains(self):
        text = self.HOST[:self.HOST.index('void insert_host')]
        program, _ = self.import_text(text)
        plan = Lowering(program).plan()
        self.assertEqual({r.get('extent') for r in plan['resources'] if r['kind'] not in ('shape', 'value')}, {'M'})


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Simulator(unittest.TestCase):
    def test_an_access_past_a_buffer_end_stops_the_run(self):
        import copy
        from differential import cases
        from differential.simulate import Program
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            path = work/'program.qpel.json'
            import json
            path.write_text(json.dumps(parse_program(PROGRAM)))
            from qpel import compile_program
            compile_program(path, work/'build')
            generated = work/'build/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            case = cases.generic(plan, 1)[3]
            (work/'sim').mkdir()
            program = Program(generated/'tables.cu', plan, work/'sim')
            program.run(case)
            small = copy.deepcopy(plan)  # the table claims the size of the keys: too small here
            for r in small['resources']:
                if r['name'] == 'table':
                    r['extent'] = 'n'
            case.shapes['slots'] = (4096, 1, 1)
            (work/'small').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'past the end of a buffer'):
                Program(generated/'tables.cu', small, work/'small').run(case)


if __name__ == '__main__':
    unittest.main(verbosity=2)
