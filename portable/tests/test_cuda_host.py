"""Host orchestration recorder, not a CUDA emulator. NumPy checks the real ABI bytes."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
try:
    import numpy as np
except ImportError:
    np = None
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from generate_cuda_host import generate
from build_backends import build

class Array:
    def __init__(self, size, dtype='uint8', device=0):
        self.size, self.dtype = size, dtype
        self.device = types.SimpleNamespace(id=device)
    def reshape(self, *_): return self

@unittest.skipIf(np is None, 'NumPy required for by-value CUDA ABI checks')
class CudaHostTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((HERE/'generated/pipeline.json').read_text())
        self.events = []; self.fail = None
        owner = self
        class Device:
            def __init__(self, device): self.id = device
            def __enter__(self): owner.events.append(('device', self.id))
            def __exit__(self, *_): pass
        def get_function(name):
            def launch(grid, block, args, stream):
                owner.events.append((name, grid, block, args, stream))
                if name == owner.fail: raise RuntimeError('kernel failure')
            return launch
        self.lib = types.SimpleNamespace(get_function=get_function)
        self.stream = types.SimpleNamespace(synchronize=lambda:self.events.append(('sync',)))
        def compile_module(**kw):
            self.events.append(('compile',kw)); return self.lib
        self.cp = types.SimpleNamespace(ndarray=Array, uint8='uint8', int32='int32', float16='float16', float32='float32',
            cuda=types.SimpleNamespace(Device=Device, get_current_stream=lambda:self.stream),
            RawModule=compile_module, ascontiguousarray=lambda a:Array(a.size,a.dtype,a.device.id),
            empty=lambda n,dtype:Array(n,dtype))
    def host(self, root, plan=None):
        ns={'__file__':str(root/'cuda_host.py')}
        plan = plan or self.plan
        (root/(plan['module']+'.cu')).write_bytes(b'shader')
        with patch.dict(sys.modules,cupy=self.cp): exec(generate(plan,b'shader'),ns)
        return ns
    def test_grid3d_launch_and_values(self):
        plan = json.loads((HERE/'tests/fixtures/qpel_ir/box_sum/program.qpel.json').read_text())
        from qpel_ir import Lowering
        plan = Lowering(plan).plan()
        with tempfile.TemporaryDirectory() as d:
            h = self.host(Path(d), plan)
            h['run']({'image': Array(17*15*3)}, {'shape': (17, 15, 3)})
            launch = next(e for e in self.events if e[0] == 'box_sum')
            self.assertEqual((launch[1], launch[2]), ((2, 1, 3), (16, 16, 1)))
            with self.assertRaisesRegex(ValueError, 'too tall'):
                h['run']({'image': Array(1*1048576*1)}, {'shape': (1, 1048576, 1)})
    def test_dispatch_and_shape_abi(self):
        with tempfile.TemporaryDirectory() as d:
            h=self.host(Path(d)); mask=Array(258)
            out=h['run']({'mask':mask},{'shape':(43,3,2)})
            calls=[e for e in self.events if e[0] not in ('device','compile')]
            self.assertEqual([e[0] for e in calls],['initialize','sync','merge','sync','label_count','sync','areas','sync'])
            init,merge,count,areas=calls[::2]
            for e in calls[::2]:
                self.assertEqual(e[1:3],((3,),(128,)))
                self.assertIs(e[4],self.stream)
            self.assertEqual(init[3][3].tobytes(),struct.pack('=4I',43,3,2,0))
            self.assertEqual(init[3][3].itemsize,16)
            self.assertIsNot(init[3][0],mask)
            self.assertIs(init[3][1],merge[3][1])
            self.assertIs(out['labels'],count[3][4])
            self.assertIs(out['output'],areas[3][5])
            self.assertEqual(out['labels'].dtype,'int32')
    def test_invalid_inputs_before_gpu_work(self):
        with tempfile.TemporaryDirectory() as d:
            h=self.host(Path(d))
            for a in [Array(29),Array(30,'float32'),Array(30,device=1),object()]:
                with self.assertRaises(ValueError):h['run']({'mask':a},{'shape':(5,3,2)})
            for shape in [(0,1,1),(True,1,1),(2**31,1,1),(1,2)]:
                with self.assertRaises(ValueError):h['run']({'mask':Array(30)},{'shape':shape})
            for device in [-1,True,'cuda']:
                with self.assertRaises(ValueError):h['run']({'mask':Array(30)},{'shape':(5,3,2)},device=device)
            with self.assertRaises(ValueError):h['run']({}, {'shape':(5,3,2)})
            self.assertEqual(self.events,[])
    def test_failure_stops_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            h=self.host(Path(d));self.fail='merge'
            with self.assertRaisesRegex(RuntimeError,'kernel failure'):h['run']({'mask':Array(30)},{'shape':(5,3,2)})
            self.assertEqual([e[0] for e in self.events if e[0] not in ('device','compile')],['initialize','sync','merge'])
    def test_device_cache_and_hash_guard(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);h=self.host(root)
            h['_library'](0);h['_library'](0);h['_library'](1)
            self.assertEqual(len([e for e in self.events if e[0]=='compile']),2)
            (root/(self.plan['module']+'.cu')).write_bytes(b'tampered')
            h['_library'].cache_clear()
            with self.assertRaisesRegex(RuntimeError,'mismatch'):h['_library'](0)
    def test_pipeline_drives_block_and_kernel(self):
        with tempfile.TemporaryDirectory() as d:
            p=copy.deepcopy(self.plan);p['stages'][0]['kernel']='renamed';p['stages'][0]['blockSize']=3
            self.host(Path(d),p)['run']({'mask':Array(30)},{'shape':(5,3,2)})
            call=next(e for e in self.events if e[0]=='renamed')
            self.assertEqual(call[1:3],((10,),(3,)))

class PackageTests(unittest.TestCase):
    def test_cuda_host_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            m=build(HERE/'generated/components.qpel.h',root,HERE/'generated/pipeline.json')
            self.assertEqual(hashlib.sha256((root/'cuda_host.py').read_bytes()).hexdigest(),m['cuda_host_sha256'])
            self.assertIn(m['outputs']['cuda']['sha256'],(root/'cuda_host.py').read_text())

if __name__=='__main__':unittest.main()
