import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate, Lowering
from qpel_pipeline import PipelineChecker, READ, WRITE
FIXTURE = Path(__file__).parent / 'fixtures/qpel_ir/components'

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.ir = json.loads((FIXTURE / 'program.qpel.json').read_text())
        self.c = PipelineChecker(self.ir)
        self.p = self.c.pipeline
        self.resources = self.c.children(self.p, 'resources')
        self.stages = self.c.children(self.p, 'stages')

    def resource(self, name):
        return next(r for r in self.resources if r['attributes']['name'] == name)

    def reject(self, code):
        for backend in ('metal', 'cuda'):
            with self.subTest(backend=backend), self.assertRaisesRegex(ValueError, code):
                validate(self.ir, backend)
        with self.assertRaisesRegex(ValueError, code):
            Lowering(self.ir)

    def test_missing_barrier(self):
        self.stages[0]['attributes']['completionBarrier'] = False
        self.reject('BARRIER')

    def test_helper_cannot_be_dispatched(self):
        helper = next(f for f in self.c.functions if not f['attributes'].get('isKernel'))
        self.stages[0]['links']['kernel'] = helper['id']
        self.reject('DISPATCH_TARGET')

    def test_parameter_order(self):
        bindings = self.stages[0]['children']['bindings']
        bindings[0], bindings[1] = bindings[1], bindings[0]
        self.reject('PARAM_ORDER')

    def test_missing_binding(self):
        removed = self.stages[0]['children']['bindings'].pop()
        self.ir['nodes'] = [n for n in self.ir['nodes'] if n['id'] != removed]
        self.reject('DISPATCH_ARITY')

    def test_alias(self):
        bindings = self.c.children(self.stages[0], 'bindings')
        bindings[1]['links']['resource'] = bindings[0]['links']['resource']
        self.reject('ALIAS')

    def test_buffer_type(self):
        self.c.child(self.resource('parent'), 'type')['kind'] = 'U32BufferType'
        self.reject('BIND_TYPE')

    def test_write_to_input(self):
        self.resource('parent')['attributes']['kind'] = 'input'
        self.reject('INPUT_WRITE')

    def test_stage_reordering_reads_uninitialized(self):
        stages = self.p['children']['stages']
        stages[0], stages[1] = stages[1], stages[0]
        self.reject('READ_BEFORE_WRITE')

    def test_readonly_must_be_input(self):
        next(r for r in self.resources if self.c.declared(r) == 'buffer<u8, read>')['attributes']['kind'] = 'scratch'
        self.reject('RESOURCE_ACCESS')

    def test_extent_must_be_shape(self):
        self.resource('parent')['links']['extent'] = self.resource('parent')['id']
        self.reject('EXTENT')

    def test_grid_must_be_shape(self):
        self.stages[0]['links']['grid'] = self.resource('parent')['id']
        self.reject('DISPATCH_GRID')

    def test_unwritten_output(self):
        original = self.resource('parent')
        extra = copy.deepcopy(original)
        extra['id'] = 'extraResource'
        extra['attributes'].update(name='unused_output', kind='output')
        typ = copy.deepcopy(self.c.child(original, 'type'))
        typ['id'] = 'extraResourceType'
        extra['children']['type'] = [typ['id']]
        self.ir['nodes'].extend([extra, typ])
        self.p['children']['resources'].append(extra['id'])
        self.reject('OUTPUT_UNWRITTEN')

    def test_helper_effects_propagate(self):
        merge = next(f for f in self.c.functions if f['attributes']['name'] == 'merge')
        parent = next(p for p in self.c.children(merge, 'parameters') if p['attributes']['name'] == 'parent')
        self.assertEqual(self.c.effects(merge)[parent['id']], READ | WRITE)

    def test_both_pointer_select_branches_tracked(self):
        # Feed a helper through a typed buffer select: both potential bases count.
        function = next(f for f in self.c.functions if f['attributes']['name'] == 'merge')
        params = self.c.children(function, 'parameters')
        parent = next(p for p in params if p['attributes']['name'] == 'parent')
        alternative = copy.deepcopy(parent)
        alternative['id'] = 'otherParam'
        alternative['attributes']['name'] = 'other'
        self.c.nodes[alternative['id']] = alternative
        def name(i, param):
            n = {'id': i, 'kind': 'Name', 'attributes': {}, 'children': {}, 'links': {'declaration': param['id']}}
            self.c.nodes[i] = n
            return i
        select = {'id': 'selection', 'kind': 'Select', 'attributes': {}, 'links': {}, 'children': {'yes': [name('yesRef', parent)], 'no': [name('noRef', alternative)]}}
        self.assertEqual(self.c.bases(select), {parent['id'], alternative['id']})

if __name__ == '__main__':
    unittest.main(verbosity=2)
