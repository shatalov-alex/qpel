import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate, Lowering
from qpel_types import TypeChecker

FIXTURES = Path(__file__).parent / 'fixtures/qpel_ir'

class TypeTests(unittest.TestCase):
    def setUp(self):
        self.ir = json.loads((FIXTURES / 'components' / 'program.qpel.json').read_text())
        self.c = TypeChecker(self.ir)

    def node(self, kind):
        return next(n for n in self.ir['nodes'] if n['kind'] == kind)

    def reject(self, code):
        for backend in ('metal', 'cuda'):
            with self.subTest(backend=backend), self.assertRaisesRegex(ValueError, code):
                validate(self.ir, backend)
        with self.assertRaisesRegex(ValueError, code):
            Lowering(self.ir)

    def test_reference_to_variable_before_declaration(self):
        variable = self.node('Variable')
        expression = self.c.child(variable, 'value')
        # Replace subtree with a structurally valid self-reference.
        self.replace(expression, 'Name', links={'declaration': variable['id']})
        self.reject('SCOPE')

    def replace(self, node, kind, attributes=None, links=None):
        removed = set()
        def descend(n):
            for values in n['children'].values():
                for ref in values:
                    removed.add(ref)
                    descend(self.c.nodes[ref])
        descend(node)
        self.ir['nodes'] = [n for n in self.ir['nodes'] if n['id'] not in removed]
        node.update(kind=kind, attributes=attributes or {}, links=links or {}, children={})

    def test_no_implicit_bool_to_integer(self):
        self.replace(self.c.child(self.node('Variable'), 'value'), 'Literal', {'value': 'true'})
        self.reject('VARIABLE_TYPE')

    def test_return_type(self):
        self.replace(self.c.child(next(n for n in self.ir['nodes'] if n['kind'] == 'Return' and n['children'].get('value')), 'value'), 'Literal', {'value': 'false'})
        self.reject('RETURN_TYPE')

    def test_atomic_address(self):
        self.replace(self.c.child(self.node('AtomicLoad'), 'address'), 'Literal', {'value': '0'})
        self.reject('ATOMIC_ADDRESS')

    def test_atomic_value(self):
        self.replace(self.c.child(self.node('AtomicAdd'), 'value'), 'Literal', {'value': 'true'})
        self.reject('ATOMIC_VALUE')

    def test_call_argument_type(self):
        call = self.node('Call')
        self.replace(self.c.children(call, 'arguments')[0], 'Literal', {'value': 'true'})
        self.reject('ARG_TYPE')

    def test_recursion_rejected(self):
        # First helper calling itself is illegal, even with valid parameter refs.
        call = self.node('Call')
        call['links']['function'] = next(f['id'] for f in self.c.functions if call in self.descendants(f))
        self.reject('CALL_ORDER')

    def descendants(self, node):
        out = [node]
        for values in node['children'].values():
            for ref in values:
                out.extend(self.descendants(self.c.nodes[ref]))
        return out

    def test_helper_grid_index(self):
        self.replace(self.c.child(self.node('Variable'), 'value'), 'GridIndex')
        self.reject('GRID_CONTEXT')

    def test_duplicate_binding(self):
        fn = next(f for f in self.c.functions if f['attributes'].get('isKernel'))
        params = self.c.children(fn, 'parameters')
        params[1]['attributes']['binding'] = params[0]['attributes']['binding']
        self.reject('BINDING_DUPLICATE')

    def test_binding_limit(self):
        fn = next(f for f in self.c.functions if f['attributes'].get('isKernel'))
        self.c.children(fn, 'parameters')[0]['attributes']['binding'] = 31
        self.reject('BINDING_INDEX')

    def test_duplicate_name(self):
        self.c.functions[1]['attributes']['name'] = self.c.functions[0]['attributes']['name']
        self.reject('DUPLICATE')

    def test_read_only_write(self):
        self.ir = json.loads((FIXTURES / 'count' / 'program.qpel.json').read_text())
        self.c = TypeChecker(self.ir)
        assignment = self.node('Assign')
        index = self.c.child(assignment, 'target')
        base = self.c.child(index, 'base')
        readonly = next(n for n in self.ir['nodes'] if n['kind'] == 'Parameter' and self.c.declared(n) == 'buffer<u8, read>')
        base['links']['declaration'] = readonly['id']
        self.reject('READ_ONLY')

    def test_no_implicit_narrowing(self):
        variable = self.node('Variable')
        self.c.child(variable, 'type')['kind'] = 'U8Type'
        self.reject('VARIABLE_TYPE')

    def test_nested_local_not_visible_outside_block(self):
        fn = next(f for f in self.c.functions if self.c.child(f, 'resultType')['kind'] == 'U32Type')
        body = self.c.child(fn, 'body')
        variable = next(n for n in self.c.children(body, 'statements') if n['kind'] == 'Variable')
        nested = {'id': 'nestedScope', 'kind': 'Block', 'attributes': {}, 'links': {}, 'children': {'statements': [variable['id']]}}
        body['children']['statements'] = ['nestedScope' if ref == variable['id'] else ref for ref in body['children']['statements']]
        self.ir['nodes'].append(nested)
        self.reject('SCOPE')

    def test_missing_unconditional_return(self):
        fn = next(f for f in self.c.functions if self.c.child(f, 'resultType')['kind'] == 'U32Type')
        for node in self.descendants(fn):
            if node['kind'] == 'Return':
                if node['children'].get('value'):
                    node['kind'] = 'Evaluate'
                else:
                    node['kind'] = 'Block'
        self.reject('MISSING_RETURN')

    def test_unsigned_byte_widening_is_allowed(self):
        from qpel_types import fits
        self.assertTrue(fits('U32Type', 'U8Type'))
        self.assertFalse(fits('U8Type', 'U32Type'))
        self.assertFalse(fits('U32Type', 'BoolType'))

if __name__ == '__main__':
    unittest.main(verbosity=2)
