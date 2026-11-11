import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate,PROFILES
FIXTURES=Path(__file__).parent/'fixtures/qpel_ir'
# MPS writes each Pipeline root (with its module) here as <pipeline>.qpel.json.
MPS_EXPORTS=Path(__file__).resolve().parents[2]/'languages/qpel.sandbox/source_gen/qpel/sandbox'
class IRTests(unittest.TestCase):
    def program(self):return json.loads((FIXTURES/'count'/'program.qpel.json').read_text())
    def test_all_fixtures_validate_for_both_backends(self):
        for folder in sorted(FIXTURES.iterdir()):
            with self.subTest(example=folder.name):
                ir=json.loads((folder/'program.qpel.json').read_text())
                for target in ['metal','cuda']:validate(ir,target)
    def test_fixtures_match_mps_export(self):
        # Each fixture is the IR that MPS exported for a sandbox model; drift means a stale fixture.
        exports={}
        for path in MPS_EXPORTS.glob('*.qpel.json'):
            ir=json.loads(path.read_text())
            pipeline=next(n for n in ir['nodes'] if n['id']==ir['roots']['pipeline'])
            exports[pipeline['attributes']['name']]=ir
        for folder in sorted(FIXTURES.iterdir()):
            with self.subTest(example=folder.name):
                ir=json.loads((folder/'program.qpel.json').read_text())
                if ir['version']>=2:
                    continue  # Python leads for new IR versions; MPS gains these constructs later.
                name=next(n for n in ir['nodes'] if n['id']==ir['roots']['pipeline'])['attributes']['name']
                self.assertIn(name,exports,'No MPS export for '+name+'; rebuild qpel.sandbox in MPS')
                self.assertEqual(ir,exports[name])
    def test_round_trip_json_without_mps_ids(self):
        ir=self.program();self.assertEqual(ir,json.loads(json.dumps(ir)))
        self.assertNotIn('r:',json.dumps(ir))
    def test_unsupported_type_and_backend(self):
        ir=self.program()
        with self.assertRaisesRegex(ValueError,'Unknown backend'):validate(ir,'vulkan')
        ir['nodes'][2]['kind']='F64Type'
        with self.assertRaisesRegex(ValueError,'Unsupported core'):validate(ir,'metal')
    def test_backend_capability_failure(self):
        ir=self.program();PROFILES['test-empty']=set()
        try:
            with self.assertRaisesRegex(ValueError,'lacks capabilities'):validate(ir,'test-empty')
        finally:del PROFILES['test-empty']
    def test_broken_graph_rejected(self):
        ir=self.program()
        ir['nodes'][0]['children']['functions'].append('missing')
        with self.assertRaisesRegex(ValueError,'Dangling'):validate(ir)
    def test_duplicate_and_cycle_rejected(self):
        ir=self.program();ir['nodes'].append(copy.deepcopy(ir['nodes'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):validate(ir)
        ir=self.program();ir['nodes'][0]['children']['functions']=[ir['nodes'][0]['id']]
        with self.assertRaisesRegex(ValueError,'cycle'):validate(ir)
if __name__=='__main__':unittest.main(verbosity=2)
