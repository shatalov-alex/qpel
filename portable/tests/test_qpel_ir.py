import copy,json,re,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qpel_ir import export,validate,Lowering,PROFILES
FIXTURES=Path(__file__).parent/'fixtures/qpel_ir'
def tokens(s):return re.findall(r'[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]',re.sub(r'//[^\n]*','',s))
class IRTests(unittest.TestCase):
    def pair(self):return [json.loads((FIXTURES/'count'/f).read_text()) for f in ['module.json','pipeline.json']]
    def test_all_fixtures_lower_like_native_textgen(self):
        for folder in sorted(FIXTURES.iterdir()):
            with self.subTest(example=folder.name):
                ir=export(json.loads((folder/'module.json').read_text()),json.loads((folder/'pipeline.json').read_text()))
                lower=Lowering(ir, _native_compat=True)
                self.assertEqual(tokens(lower.emit(lower.nodes[ir['roots']['module']])),tokens((folder/'native.qpel.h').read_text()))
                self.assertEqual(lower.plan(),json.loads((folder/'native-plan.json').read_text()))
                for target in ['metal','cuda']:validate(ir,target)
    def test_mps_ids_do_not_affect_program(self):
        module,plan=self.pair();first=export(module,plan)
        def remap(x):
            if isinstance(x,dict):return {k:'different:'+v if k in ('reference','targetReference') and isinstance(v,str) else remap(v) for k,v in x.items()}
            if isinstance(x,list):return [remap(v) for v in x]
            return x
        self.assertEqual(first,export(remap(module),remap(plan)))
    def test_round_trip_json(self):
        ir=export(*self.pair());self.assertEqual(ir,json.loads(json.dumps(ir)))
        self.assertNotIn('r:98adf',json.dumps(ir))
    def test_external_reference_rejected(self):
        a,b=self.pair();b['references'][0]['targetReference']='foreign'
        with self.assertRaisesRegex(ValueError,'outside'):export(a,b)
    def test_unsupported_type_and_backend(self):
        ir=export(*self.pair())
        with self.assertRaisesRegex(ValueError,'Unknown backend'):validate(ir,'vulkan')
        ir['nodes'][2]['kind']='F64Type'
        with self.assertRaisesRegex(ValueError,'Unsupported core'):validate(ir,'metal')
    def test_backend_capability_failure(self):
        ir=export(*self.pair());PROFILES['test-empty']=set()
        try:
            with self.assertRaisesRegex(ValueError,'lacks capabilities'):validate(ir,'test-empty')
        finally:del PROFILES['test-empty']
    def test_broken_graph_rejected(self):
        ir=export(*self.pair())
        ir['nodes'][0]['children']['functions'].append('missing')
        with self.assertRaisesRegex(ValueError,'Dangling'):validate(ir)
    def test_duplicate_and_cycle_rejected(self):
        ir=export(*self.pair());ir['nodes'].append(copy.deepcopy(ir['nodes'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):validate(ir)
        ir=export(*self.pair());ir['nodes'][0]['children']['functions']=[ir['nodes'][0]['id']]
        with self.assertRaisesRegex(ValueError,'cycle'):validate(ir)
if __name__=='__main__':unittest.main(verbosity=2)
