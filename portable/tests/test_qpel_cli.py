import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from qpel_ir import export
from qpel import compile_program

class OfflineCliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'program.qpel.json'
        fixture=HERE/'tests/fixtures/qpel_ir/workgroup'
        self.program=export(*(json.loads((fixture/f).read_text()) for f in ('module.json','pipeline.json')))
        self.source.write_text(json.dumps(self.program))
        self.out=self.root/'result'
    def test_check_creates_nothing(self):
        before=list(self.root.iterdir());report=compile_program(self.source)
        self.assertEqual(report['status'],'checked');self.assertEqual(before,list(self.root.iterdir()))
    def test_complete_package_hashes_and_paths(self):
        report=compile_program(self.source,self.out);root=self.out/'package'
        for name,digest in report['artifact_sha256'].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)
        manifest=json.loads((root/'generated/manifest.json').read_text())
        for key in ('mps_source','mps_pipeline'):
            self.assertTrue(Path(manifest[key]).is_file())
            self.assertTrue(Path(manifest[key]).is_relative_to(root.resolve()))
        for backend in ('cuda','metal'):
            self.assertTrue((root/f'generated/{backend}_host.py').is_file())
            self.assertFalse(report[backend+'_compiled'])
        self.assertFalse(report['gpu_tested'])
    def test_existing_output_untouched(self):
        self.out.mkdir();sentinel=self.out/'keep';sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError,'already exists'):compile_program(self.source,self.out)
        self.assertEqual(sentinel.read_text(),'keep')
    def test_invalid_input_no_output(self):
        self.program['semantics']='unknown';self.source.write_text(json.dumps(self.program))
        with self.assertRaises(ValueError):compile_program(self.source,self.out)
        self.assertFalse(self.out.exists())
    def test_build_failure_no_partial_package(self):
        with patch('qpel.build',side_effect=RuntimeError('injected packaging failure')):
            with self.assertRaises(RuntimeError):compile_program(self.source,self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.iterdir()),[self.source])
    def test_cli_process_and_error(self):
        run=subprocess.run([sys.executable,str(HERE/'qpel.py'),'check',str(self.source)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(json.loads(run.stdout)['status'],'checked')
        self.source.write_text('{invalid')
        run=subprocess.run([sys.executable,str(HERE/'qpel.py'),'check',str(self.source)],capture_output=True,text=True)
        self.assertEqual(run.returncode,1);self.assertNotIn('Traceback',run.stderr)

if __name__=='__main__':unittest.main()
