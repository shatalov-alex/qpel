import json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import translate_cuda as cli
SOURCE=Path(__file__).resolve().parents[1]/'cuda_examples/masked_copy.cu'

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.out=self.root/'package'
        (self.root/'languages/qpel.sandbox/source_gen/qpel/sandbox').mkdir(parents=True)
    def test_check_has_no_side_effects(self):
        with patch.object(cli,'MPS',side_effect=AssertionError('network')):
            report=cli.translate(SOURCE,self.root,'example',self.out,check=True)
        self.assertEqual(report['status'],'source_accepted');self.assertFalse(self.out.exists())
    def test_invalid_source_has_no_side_effects(self):
        source=self.root/'invalid.cu';source.write_text('#define BAD 1\n'+SOURCE.read_text())
        with patch.object(cli,'MPS',side_effect=AssertionError('network')),self.assertRaises(ValueError):
            cli.translate(source,self.root,'example',self.out)
        self.assertFalse(self.out.exists())
    def test_invalid_arguments_have_no_side_effects(self):
        for name,block in [('bad-name',64),('example',0),('example',257)]:
            with patch.object(cli,'MPS',side_effect=AssertionError('network')),self.assertRaises(ValueError):
                cli.translate(SOURCE,self.root,name,self.out,block=block)
            self.assertFalse(self.out.exists())
    def test_existing_output_preserved(self):
        self.out.mkdir();sentinel=self.out/'keep';sentinel.write_text('user data')
        with patch.object(cli,'MPS',side_effect=AssertionError('network')),self.assertRaises(ValueError):
            cli.translate(SOURCE,self.root,'example',self.out)
        self.assertEqual(sentinel.read_text(),'user data');self.assertEqual(len(list(self.out.iterdir())),1)
    def test_connection_failure_is_reported(self):
        with patch.object(cli,'MPS',side_effect=OSError('offline')),self.assertRaises(OSError):
            cli.translate(SOURCE,self.root,'example',self.out)
        report=json.loads((self.out/'translation-report.json').read_text())
        self.assertEqual(report['status'],'failed');self.assertFalse(report['gpu_tested'])
        self.assertFalse((self.out/'generated').exists())
    def test_checked_models_produce_both_backends_from_one_ir(self):
        from unittest.mock import MagicMock
        fixtures=Path(__file__).parent/'fixtures/qpel_ir/count'
        generated=self.root/'languages/qpel.sandbox/source_gen/qpel/sandbox'
        (generated/'cuda_masked_copy_launch.qpel.json').write_text((fixtures/'program.qpel.json').read_text())
        def imported(mps,ir,module,model,block,journal):
            journal.update(module_reference='module-ref',pipeline_reference='plan-ref',status='complete')
        with patch.object(cli,'MPS',return_value=MagicMock()),patch.object(cli,'import_model',side_effect=imported):
            result=cli.translate(SOURCE,self.root,'cuda_masked_copy',self.out)
        self.assertEqual(result['status'],'generated')
        self.assertTrue((self.out/'program.qpel.json').exists())
        self.assertEqual(set(result['backend_capabilities']),{'metal','cuda'})
        for ext in ['metal','cu']:self.assertTrue((self.out/'generated'/('cuda_masked_copy.'+ext)).exists())
        for path,digest in result['artifact_sha256'].items():self.assertEqual(cli.digest(self.out/path),digest)
    def test_missing_mps_export_is_reported(self):
        from unittest.mock import MagicMock
        def imported(mps,ir,module,model,block,journal):
            journal.update(module_reference='module-ref',pipeline_reference='plan-ref',status='complete')
        with patch.object(cli,'MPS',return_value=MagicMock()),patch.object(cli,'import_model',side_effect=imported),self.assertRaisesRegex(ValueError,'did not export'):
            cli.translate(SOURCE,self.root,'example',self.out)
        self.assertFalse((self.out/'generated').exists())
    def test_packaging_failure_preserves_model_references(self):
        def imported(mps,ir,module,model,block,journal):
            journal.update(module_reference='module-ref',pipeline_reference='plan-ref',status='complete')
        fixtures=Path(__file__).parent/'fixtures/qpel_ir/count'
        generated=self.root/'languages/qpel.sandbox/source_gen/qpel/sandbox'
        (generated/'example_launch.qpel.json').write_text((fixtures/'program.qpel.json').read_text())
        from unittest.mock import MagicMock
        with patch.object(cli,'MPS',return_value=MagicMock()),patch.object(cli,'import_model',side_effect=imported),patch.object(cli,'build',side_effect=RuntimeError('package failure')),self.assertRaisesRegex(RuntimeError,'package failure'):
            cli.translate(SOURCE,self.root,'example',self.out)
        report=json.loads((self.out/'translation-report.json').read_text())
        self.assertEqual(report['status'],'failed');self.assertEqual(report['module_reference'],'module-ref')
        self.assertFalse((self.out/'generated').exists());self.assertFalse(list(self.out.glob('.package-*')))

if __name__=='__main__':unittest.main(verbosity=2)
