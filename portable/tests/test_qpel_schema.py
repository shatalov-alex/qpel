"""Invalid portable programs must fail before either backend can emit source."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qpel_ir import Lowering, validate
from qpel_schema import validate_structure

FIXTURE = Path(__file__).parent / 'fixtures/qpel_ir/count'

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.ir = json.loads((FIXTURE / 'program.qpel.json').read_text())

    def node(self, kind):
        return next(n for n in self.ir['nodes'] if n['kind'] == kind)

    def rejected(self, message):
        for backend in ('metal', 'cuda'):
            with self.subTest(backend=backend):
                with self.assertRaisesRegex(ValueError, message):
                    validate(self.ir, backend)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, message):
                Lowering(self.ir).write(Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_missing_root_is_diagnostic(self):
        self.ir['roots']['module'] = 'missing'
        self.rejected('Invalid IR roots')

    def test_wrong_reference_category(self):
        self.node('Name')['links']['declaration'] = self.node('Function')['id']
        self.rejected('Invalid link target for declaration')

    def test_missing_required_child(self):
        fn = self.node('Function')
        # Keep all nodes reachable while putting resultType in a new role.
        fn['children']['unexpected'] = fn['children'].pop('resultType')
        self.rejected('Unknown child role')

    def test_missing_link(self):
        self.node('Name')['links'].clear()
        self.rejected('Invalid link roles')

    def test_child_category(self):
        fn = self.node('Function')
        fn['children']['body'], fn['children']['resultType'] = fn['children']['resultType'], fn['children']['body']
        self.rejected('Invalid child kind')

    def test_excess_single_child(self):
        fn = self.node('Function')
        clone = copy.deepcopy(self.node('VoidType'))
        clone['id'] = 'extraType'
        self.ir['nodes'].append(clone)
        fn['children']['resultType'].append(clone['id'])
        self.rejected('Invalid child count')

    def test_no_raw_source_literals(self):
        for value in ('0); bad(); //', '-1', '1.5', '0xffffffff', '4294967296', ''):
            with self.subTest(value=value):
                self.node('Literal')['attributes']['value'] = value
                self.rejected('decimal literal')

    def test_boolean_and_uint32_literal_boundaries(self):
        for value in ('true', 'false', '0', '4294967295u', '4294967295U'):
            self.node('Literal')['attributes']['value'] = value
            validate_structure({n['id']: n for n in self.ir['nodes']})

    def test_invalid_identifier_and_member(self):
        fn = self.node('Function')
        original = fn['attributes']['name']
        for name in ('../file', 'f(){}', 'qpel_index', 'while'):
            fn['attributes']['name'] = name
            self.rejected('identifier')
        fn['attributes']['name'] = original
        # Names are emitted verbatim into CUDA C++ and MSL; backend keywords would not compile.
        param = self.node('Parameter')
        kept = param['attributes']['name']
        for name in ('class', 'switch', 'constant', 'threadgroup', 'blockIdx', 'half'):
            param['attributes']['name'] = name
            self.rejected('identifier')
        param['attributes']['name'] = kept
        self.node('Member')['attributes']['member'] = 'x; bad()'
        self.rejected('shape member')

    def test_bool_not_integer_and_str_not_bool(self):
        self.node('Dispatch')['attributes']['blockSize'] = True
        self.rejected('attribute type')
        self.node('Dispatch')['attributes']['blockSize'] = 64
        self.node('Dispatch')['attributes']['completionBarrier'] = 'false'
        self.rejected('attribute type')

    def test_launch_ranges(self):
        for value in (0, -1, 257):
            self.node('Dispatch')['attributes']['blockSize'] = value
            self.rejected('Block size')
        self.node('Dispatch')['attributes']['blockSize'] = 64
        self.node('Parameter')['attributes']['binding'] = -1
        self.rejected('Binding')

    def test_malformed_envelope(self):
        for program in (None, [], {}, {'nodes': {}, 'roots': {}}, {'nodes': [None], 'roots': {}}):
            with self.subTest(program=program), self.assertRaises(ValueError):
                validate(program)
        self.node('Function')['children']['body'] = 'not-a-list'
        self.rejected('lists of string references')

if __name__ == '__main__':
    unittest.main(verbosity=2)
