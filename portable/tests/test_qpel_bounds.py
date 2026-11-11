import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate,Lowering
from qpel_bounds import BoundsChecker,MAX_ELEMENTS
FIXTURES=Path(__file__).parent/'fixtures/qpel_ir'

class BoundsTests(unittest.TestCase):
    def load(self,name='float32'):
        folder=FIXTURES/name
        self.ir=json.loads((folder / 'program.qpel.json').read_text())
    def replace(self,node,kind,attributes=None,children=None):
        ids=set()
        def visit(n):
            for refs in n['children'].values():
                for ref in refs:
                    ids.add(ref);visit(next(x for x in self.ir['nodes'] if x['id']==ref))
        visit(node)
        self.ir['nodes'][:]=[n for n in self.ir['nodes'] if n['id'] not in ids]
        node.update(kind=kind,attributes=attributes or {},children=children or {},links={})
    def literal(self,node,value):self.replace(node,'Literal',{'value':str(value)})
    def index(self):
        c=BoundsChecker(self.ir)
        n=next(n for n in self.ir['nodes'] if n['kind']=='Index')
        return c.child(n,'index')
    def reject(self):
        for target in ('cuda','metal'):
            with self.assertRaisesRegex(ValueError,'STATIC_BOUNDS'):validate(self.ir,target)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'STATIC_BOUNDS'):Lowering(self.ir).write(Path(d))
            self.assertEqual(list(Path(d).iterdir()),[])
    def test_out_of_range_index(self):
        for value in (MAX_ELEMENTS,2**32-1):
            self.load();self.literal(self.index(),value);self.reject()
    def test_limit_does_not_claim_runtime_safety(self):
        self.load();self.literal(self.index(),MAX_ELEMENTS-1)
        report=validate(self.ir,'cuda')['memory_checks']
        self.assertFalse(report['bounds_proven'])
        self.assertGreater(report['access_sites'],0)
    def test_modular_constant_index(self):
        self.load();node=self.index()
        self.replace(node,'Binary',{'operator':'-'},{'left':['const_a'],'right':['const_b']})
        for name,value in [('const_a','0'),('const_b','1')]:
            self.ir['nodes'].append(dict(id=name,kind='Literal',attributes={'value':value},children={},links={}))
        self.reject() # unsigned 0 - 1 is 4294967295
    def test_wrapping_to_zero_is_not_false_positive(self):
        self.load();node=self.index()
        self.replace(node,'Binary',{'operator':'+'},{'left':['const_a'],'right':['const_b']})
        for name,value in [('const_a','4294967295'),('const_b','1')]:
            self.ir['nodes'].append(dict(id=name,kind='Literal',attributes={'value':value},children={},links={}))
        validate(self.ir,'metal')
    def test_negative_pointer_offset(self):
        self.load();c=BoundsChecker(self.ir)
        access=next(n for n in self.ir['nodes'] if n['kind']=='Index')
        base=c.child(access,'base');old=dict(base);old['id']='oldbase'
        self.replace(base,'Binary',{'operator':'-'},{'left':['oldbase'],'right':['offset']})
        self.ir['nodes'] += [old,dict(id='offset',kind='Literal',attributes={'value':'1'},children={},links={})]
        self.reject()
    def test_reduction_range_and_empty_sum(self):
        self.load('reduce');c=BoundsChecker(self.ir)
        node=next(n for n in self.ir['nodes'] if n['kind']=='ReduceSumF32')
        start,count=c.child(node,'start'),c.child(node,'count')
        self.literal(start,MAX_ELEMENTS-1);self.literal(count,2);self.reject()
        self.literal(start,2**32-1);self.literal(count,0)
        validate(self.ir,'cuda')
    def test_atomic_one_past_access_rejected(self):
        self.load('components');c=BoundsChecker(self.ir)
        def kernel_of(n):
            while n['kind'] != 'Function': n=c.parents[n['id']]
            return n
        atomic=next(n for n in self.ir['nodes'] if n['kind'] in {'AtomicLoad','AtomicStore','AtomicAdd','AtomicMin'} and kernel_of(n)['attributes'].get('isKernel'))
        param=next(p for p in c.children(kernel_of(atomic),'parameters') if c.declared(p)=='buffer<u32, atomic>')
        address=c.child(atomic,'address')
        self.replace(address,'Binary',{'operator':'+'},{'left':['atomic_base'],'right':['atomic_offset']})
        self.ir['nodes'] += [dict(id='atomic_base',kind='Name',attributes={},children={},links={'declaration':param['id']}),dict(id='atomic_offset',kind='Literal',attributes={'value':str(MAX_ELEMENTS)},children={},links={})]
        self.reject()

    def test_all_existing_fixtures_remain_valid(self):
        for folder in FIXTURES.iterdir():
            if (folder/'program.qpel.json').exists():
                self.load(folder.name)
                for target in ('metal','cuda'):validate(self.ir,target)
    def test_helper_pointer_origin_is_unknown(self):
        self.load('components');c=BoundsChecker(self.ir)
        helper=next(f for f in c.functions if not f['attributes'].get('isKernel'))
        params={p['id'] for p in c.children(helper,'parameters')}
        name=next(n for n in self.ir['nodes'] if n['kind']=='Name' and n['links']['declaration'] in params)
        self.assertIsNone(c.origin_offset(name))

if __name__=='__main__':unittest.main()
