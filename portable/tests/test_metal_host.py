"""CPU-only orchestration checks. The recorder is NOT a GPU or kernel emulator."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from build_backends import launch_header
from generate_metal_host import generate

class Device:
    def __init__(self, name):
        self.type, _, index = str(name).partition(':')
        self.index = int(index) if index else None
    def __eq__(self, other): return (self.type, self.index) == (other.type, other.index)
    def __str__(self): return self.type + (':' + str(self.index) if self.index is not None else '')

class Tensor:
    def __init__(self, count, device, dtype, data=None):
        self.count, self.device, self.dtype, self.data = count, device, dtype, data
    def numel(self): return self.count
    def contiguous(self): return self
    def view(self, *args): return self

class Recorder:
    def __init__(self): self.events = []; self.fail = None
    def __getattr__(self, name):
        def kernel(*args, **kw):
            self.events.append((name, args, kw))
            if name == self.fail: raise RuntimeError('kernel failure')
        return kernel
    def sync(self): self.events.append(('sync',))

class HostTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((HERE/'generated/pipeline.json').read_text())
        self.rec = Recorder()
        self.torch = types.SimpleNamespace(device=Device, int32='i32', uint8='u8',
            empty=lambda n, **kw: Tensor(n, **kw),
            tensor=lambda data, **kw: Tensor(len(data), data=data, **kw),
            mps=types.SimpleNamespace(compile_shader=lambda source:self.rec, synchronize=self.rec.sync))
        self.mask = Tensor(30, Device('mps:0'), 'u8')
    def host(self, plan=None):
        plan = plan or self.plan
        launch_header(plan)
        ns = {'__file__': str(HERE/'generated/metal_host.py')}
        with patch.dict(sys.modules, torch=self.torch): exec(generate(plan, b'fake shader'), ns)
        ns['_library'] = lambda:self.rec
        return ns
    def test_allocations_bindings_and_completion(self):
        result = self.host()['run']({'mask':self.mask}, {'shape':(5,3,2)})
        events = self.rec.events
        self.assertEqual([e[0] for e in events], ['initialize','sync','merge','sync','label_count','sync','areas','sync'])
        init, merge, count, areas = events[::2]
        self.assertIs(init[1][0], self.mask)
        self.assertEqual(init[1][3].data, [5,3,2,0])
        self.assertIs(init[1][1], merge[1][1])
        self.assertIs(init[1][2], count[1][2])
        self.assertIs(result['labels'], count[1][4]); self.assertIs(result['output'], areas[1][5])
        self.assertIsNot(result['labels'], result['output'])
        for event in events[::2]: self.assertEqual(event[2], {'threads':30,'group_size':128})
        for value in result.values():
            self.assertEqual(value.dtype, 'i32'); self.assertEqual(value.count, 30)
    def test_model_changes_drive_generation(self):
        p=copy.deepcopy(self.plan)
        for r in p['resources']:
            r['name']='renamed_'+r['name']
            if r['extent']: r['extent']='renamed_'+r['extent']
        for s in p['stages']:
            s['grid']='renamed_'+s['grid'];s['blockSize']=64
            for b in s['bindings']: b['resource']='renamed_'+b['resource']
        p['stages'][1]['kernel']='different_kernel'
        result=self.host(p)['run']({'renamed_mask':self.mask},{'renamed_shape':(5,3,2)})
        self.assertEqual(set(result), {'renamed_labels','renamed_output'})
        self.assertEqual(self.rec.events[2][0], 'different_kernel')
        self.assertEqual(self.rec.events[2][2]['group_size'],64)
    def test_invalid_shapes(self):
        for dims in [(0,3,2),(-1,3,2),(True,3,2),(1.0,3,2),(1,2),(2**31,1,1)]:
            with self.subTest(dims=dims), self.assertRaises(ValueError):
                self.host()['run']({'mask':self.mask},{'shape':dims})
        self.assertEqual(self.rec.events, [])
    def test_invalid_inputs(self):
        for tensor in [Tensor(29,Device('mps:0'),'u8'),Tensor(30,Device('cpu'),'u8'),Tensor(30,Device('mps:0'),'i32')]:
            with self.assertRaises(ValueError): self.host()['run']({'mask':tensor},{'shape':(5,3,2)})
        with self.assertRaises(ValueError): self.host()['run']({}, {'shape':(5,3,2)})
        with self.assertRaises(ValueError): self.host()['run']({'mask':self.mask},{'shape':(5,3,2)},device='cpu')
        self.assertEqual(self.rec.events, [])
    def test_failure_stops_later_launches(self):
        self.rec.fail='merge'
        with self.assertRaisesRegex(RuntimeError,'kernel failure'):
            self.host()['run']({'mask':self.mask},{'shape':(5,3,2)})
        self.assertEqual([e[0] for e in self.rec.events],['initialize','sync','merge'])
    def test_unsupported_resource_rejected(self):
        for field,value in [('type','F64BufferType'),('extent','missing'),('kind','unknown')]:
            p=copy.deepcopy(self.plan);p['resources'][0][field]=value
            with self.assertRaises(ValueError): self.host(p)
    def test_shader_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            ns={'__file__':str(Path(d)/'metal_host.py')}
            (Path(d)/'components.metal').write_bytes(b'wrong source')
            with patch.dict(sys.modules, torch=self.torch):exec(generate(self.plan,b'right source'),ns)
            with self.assertRaisesRegex(RuntimeError,'mismatch'):ns['_library']()

if __name__=='__main__': unittest.main(verbosity=2)
