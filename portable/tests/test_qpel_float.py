import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate, Lowering
from build_backends import build
from qpel_types import TypeChecker
FIXTURE = Path(__file__).parent/'fixtures/qpel_ir/count'


def float_program():
    p = json.loads((FIXTURE / 'program.qpel.json').read_text())
    nodes = {n['id']:n for n in p['nodes']}
    for n in p['nodes']:
        if n['kind'] in ('Parameter','Resource') and n['attributes']['name'] in ('pixels','output'):
            nodes[n['children']['type'][0]]['kind'] = 'ReadOnlyF32BufferType' if n['attributes']['name']=='pixels' else 'F32BufferType'
    select = next(n for n in p['nodes'] if n['kind']=='Select')
    no = nodes[select['children']['no'][0]]
    no.update(kind='F32Literal',attributes={'value':'0.0'})
    original = select['children']['yes'][0]
    p['nodes'].extend([
        dict(id='scale',kind='F32Literal',attributes={'value':'0.5'},children={},links={}),
        dict(id='product',kind='Binary',attributes={'operator':'*'},children={'left':[original],'right':['scale']},links={}),
    ])
    select['children']['yes']=['product']
    return p


class FloatTests(unittest.TestCase):
    def setUp(self):
        self.p=float_program()
        self.nodes={n['id']:n for n in self.p['nodes']}

    def reject(self, message):
        for backend in ('cuda','metal'):
            with self.subTest(backend=backend),self.assertRaisesRegex(ValueError,message):validate(self.p,backend)

    def test_float_capability_and_host_dtype(self):
        for backend in ('cuda','metal'):self.assertIn('float32',validate(self.p,backend)['required_features'])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);src=Lowering(self.p).write(folder/'neutral');build(src,folder/'generated')
            host=(folder/'generated/metal_host.py').read_text()
            self.assertIn('torch.float32',host)
            compile(host,'metal_host.py','exec')
            self.assertIn('device const float*',(folder/'generated/cuda_masked_copy.metal').read_text())

    def test_no_implicit_int_float_mix(self):
        self.nodes['scale']['kind']='Literal';self.nodes['scale']['attributes']['value']='1'
        self.reject('BINARY')

    def test_no_float_bitwise(self):
        self.nodes['product']['attributes']['operator']='&'
        self.reject('BINARY')

    def test_explicit_integer_to_float(self):
        literal=self.nodes['scale'];literal.update(kind='Literal',attributes={'value':'2'})
        self.p['nodes'].append(dict(id='cast',kind='F32Cast',attributes={},links={},children={'operand':['scale']}))
        self.nodes['product']['children']['right']=['cast']
        validate(self.p)
        lower=Lowering(self.p)
        self.assertIn('(QPEL_F32)(2u)',lower.emit(lower.nodes['cast']))

    def test_bool_conversion_rejected(self):
        self.nodes['scale'].update(kind='Literal',attributes={'value':'true'})
        self.p['nodes'].append(dict(id='cast',kind='F32Cast',attributes={},links={},children={'operand':['scale']}))
        self.nodes['product']['children']['right']=['cast']
        self.reject('CAST_TYPE')

    def test_nonfinite_or_source_literal_rejected(self):
        for value in ('nan','inf','1e100','0);oops()', '-1.0'):
            self.nodes['scale']['attributes']['value']=value
            self.reject('literal')

    def test_readonly_float_write(self):
        assign=next(n for n in self.p['nodes'] if n['kind']=='Assign')
        lhs=self.nodes[assign['children']['target'][0]]
        base=self.nodes[lhs['children']['base'][0]]
        pixels=next(n for n in self.p['nodes'] if n['kind']=='Parameter' and n['attributes']['name']=='pixels')
        base['links']['declaration']=pixels['id']
        self.reject('READ_ONLY')

    @unittest.skipUnless(shutil.which('clang++'),'clang++ required')
    def test_packaged_cuda_source_on_cpu(self):
        self.compile_and_run()

    @unittest.skipUnless(shutil.which('clang++'),'clang++ required')
    def test_native_mps_export_on_cpu(self):
        fixture = FIXTURE.parent/'float32'
        self.p = json.loads((fixture / 'program.qpel.json').read_text())
        self.compile_and_run()

    def compile_and_run(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);src=Lowering(self.p).write(folder/'neutral');build(src,folder/'generated')
            (folder/'cuda_runtime.h').write_text('''#pragma once
#define __global__
#define __device__
#define __forceinline__ inline
struct Dim { unsigned int x; };
static Dim blockIdx{0}, blockDim{1}, threadIdx{0};
''')
            module=Lowering(self.p).plan()['module']
            (folder/'main.cpp').write_text(('''#include <cassert>
#include <cmath>
#include "generated/cuda_masked_copy.cu"
int main(){
 float pixels[257], output[257]; unsigned char mask[257];
 for(unsigned i=0;i<257;++i){pixels[i]=(float(i)-128.0f)*0.125f;mask[i]=i%3;output[i]=12345;}
 for(unsigned i=0;i<262;++i){threadIdx.x=i;masked_copy(pixels,mask,output,QpelShape{257,1,1,0});}
 for(unsigned i=0;i<257;++i)assert(output[i]==(mask[i]?pixels[i]*0.5f:0.0f));
}
''').replace('generated/cuda_masked_copy.cu', 'generated/'+module+'.cu'))
            subprocess.run(['clang++','-std=c++17','-O1','-ffp-contract=off','-fsanitize=undefined','-fno-sanitize-recover=all','-I',str(folder),str(folder/'main.cpp'),'-o',str(folder/'test')],check=True,capture_output=True,text=True,timeout=60)
            subprocess.run([str(folder/'test')],check=True,capture_output=True,text=True,timeout=30)

if __name__=='__main__':unittest.main(verbosity=2)
