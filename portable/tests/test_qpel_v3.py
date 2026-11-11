"""IR version 3: types as terms, new integer widths, bf16 storage and saturating conversions."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import Lowering, validate  # noqa: E402
from qpel_types import fits, join, node_spec, normalize_plan, term  # noqa: E402
from fixture_builds import build_fixture, fixture_program  # noqa: E402

FIXTURE = HERE/'tests/fixtures/qpel_ir/int_widths'
spec = importlib.util.spec_from_file_location('int_widths_oracle', FIXTURE/'oracle.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


class Terms(unittest.TestCase):
    def test_legacy_names_are_spellings_of_terms(self):
        self.assertEqual(term('ReadOnlyF32BufferType'), 'buffer<f32, read>')
        self.assertEqual(term('AtomicI32BufferType'), 'buffer<i32, atomic>')
        self.assertEqual(node_spec('buffer<f32, write>'), ('F32BufferType', {}))
        self.assertEqual(node_spec('buffer<u8, write>'), ('BufferType', {'element': 'u8', 'access': 'write'}))
        self.assertEqual(node_spec('i64'), ('ScalarType', {'scalar': 'i64'}))

    def test_lossless_widening_only(self):
        self.assertTrue(fits('i64', 'u32'))
        self.assertTrue(fits('i32', 'u16'))
        self.assertFalse(fits('i32', 'u32'))
        self.assertFalse(fits('u64', 'i8'))
        self.assertIsNone(join('i8', 'u8'))  # neither fits the other: convert explicitly
        self.assertEqual(join('i16', 'u8'), 'i32')
        self.assertEqual(join('u32', 'i64'), 'i64')
        self.assertIsNone(join('u32', 'i32'))
        self.assertIsNone(join('u64', 'i64'))

    def test_old_plans_are_normalized(self):
        plan = {'schema': 1, 'module': 'm', 'stages': [],
                'resources': [{'name': 'x', 'kind': 'input', 'type': 'ReadOnlyU8BufferType', 'extent': 's'}]}
        self.assertEqual(normalize_plan(plan)['resources'][0]['type'], 'buffer<u8, read>')

    def test_version_3_constructs_need_version_3(self):
        program = fixture_program('int_widths')
        self.assertEqual(program['version'], 3)
        old = dict(program, version=2, semantics='qpel-portable-core-v2')
        with self.assertRaisesRegex(ValueError, 'declare version 3'):
            validate(old)
        report = validate(program, 'metal')
        self.assertTrue({'int8_16', 'int64', 'bfloat16_storage'} <= set(report['required_features']))

    def test_constant_range_and_atomic_elements_are_checked(self):
        program = fixture_program('int_widths')
        constant = next(n for n in program['nodes'] if n['kind'] == 'Constant')
        constant['attributes']['value'] = str(2**63)
        with self.assertRaisesRegex(ValueError, 'out of range'):
            validate(program)
        program = fixture_program('int_widths')
        buffer = next(n for n in program['nodes'] if n['kind'] == 'BufferType')
        buffer['attributes'].update(element='u64', access='atomic')
        with self.assertRaisesRegex(ValueError, 'Invalid buffer type'):
            validate(program)

    def test_plan_spells_types_as_terms(self):
        plan = Lowering(fixture_program('int_widths')).plan()
        self.assertEqual(plan['schema'], 2)
        types = {r['name']: r['type'] for r in plan['resources']}
        self.assertEqual(types['k'], 'i64')
        self.assertEqual(types['h'], 'buffer<bf16, read>')
        self.assertEqual(types['o_u8'], 'buffer<u8, write>')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Simulated(unittest.TestCase):
    def test_generated_cuda_matches_the_oracle(self):
        from differential.simulate import Case, Program
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            generated = build_fixture('int_widths', work/'build')
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'sim').mkdir()
            program = Program(generated/'int_widths.cu', plan, work/'sim')
            for shape, inputs, k in oracle.cases():
                result = program.run(Case({'count': shape}, inputs, {'k': k}, str(shape)))
                error = oracle.compare(oracle.expected(inputs, k), result)
                self.assertIsNone(error, f'{shape}, k={k}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
