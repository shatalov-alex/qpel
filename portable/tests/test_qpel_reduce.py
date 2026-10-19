import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qpel_ir import export,validate,Lowering,PROFILES
from qpel_types import TypeChecker
from build_backends import build
FIXTURE=Path(__file__).parent/'fixtures/qpel_ir/reduce'

def f32(x):return struct.unpack('f',struct.pack('f',x))[0]
def ordered(xs):
    result=0.0
    for x in xs:result=f32(result+x)
    return result

class ReduceTests(unittest.TestCase):
    def setUp(self):
        self.ir=export(*(json.loads((FIXTURE/f).read_text()) for f in ('module.json','pipeline.json')))
        self.c=TypeChecker(self.ir);self.nodes=self.c.nodes
        self.reduce=next(n for n in self.ir['nodes'] if n['kind']=='ReduceSumF32')

    def reject(self,code):
        for target in ('metal','cuda'):
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,code):validate(self.ir,target)

    def test_integer_buffer_rejected(self):
        mask=next(n for n in self.ir['nodes'] if n['kind']=='Parameter' and n['attributes']['name']=='mask')
        self.c.child(self.reduce,'buffer')['links']['declaration']=mask['id']
        self.reject('REDUCE_BUFFER')

    def test_read_effect_is_tracked(self):
        for n in self.ir['nodes']:
            if n['kind'] in ('Parameter','Resource') and n['attributes']['name']=='pixels':
                self.c.child(n,'type')['kind']='F32BufferType'
                if n['kind']=='Resource':n['attributes']['kind']='scratch'
        self.reject('READ_BEFORE_WRITE')

    def test_capability(self):
        self.assertIn('ordered_sum_f32',validate(self.ir,'metal')['required_features'])
        PROFILES['no-reduce']=PROFILES['metal']-{'ordered_sum_f32'}
        try:
            with self.assertRaisesRegex(ValueError,'ordered_sum_f32'):validate(self.ir,'no-reduce')
        finally:del PROFILES['no-reduce']

    @unittest.skipUnless(shutil.which('clang++'),'clang++ required')
    def test_cpu_ordered_sum(self):
        xs=[16777216.0,1.0,-16777216.0,1.0]+[i/8 for i in range(-128,129)]
        ys=[ordered(xs[i:i+4]) for i in range(len(xs))]
        self.assertEqual(ys[0],1.0)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);source=Lowering(self.ir).write(folder/'neutral');build(source,folder/'generated')
            (folder/'cuda_runtime.h').write_text('''#pragma once
#define __global__
#define __device__
#define __forceinline__ inline
struct Dim {unsigned int x;};
static Dim blockIdx{0},blockDim{1},threadIdx{0};
inline float __fadd_rn(float a,float b){volatile float result=a+b;return result;}
''')
            source='''#include <cassert>
#include <algorithm>
using std::min;using std::max;
#include "generated/float_window_sum.cu"
int main(){float x[]={XS};float expected[]={YS};float out[N];unsigned char mask[N];
for(unsigned i=0;i<N;++i)mask[i]=1;
for(unsigned i=0;i<N+5;++i){threadIdx.x=i;masked_copy(x,mask,out,QpelShape{N,1,1,0});}
for(unsigned i=0;i<N;++i)assert(out[i]==expected[i]);
assert(qpel_sum_f32(x,N,0)==0.0f);
assert(qpel_sum_f32(x,0,4)==1.0f);
assert(qpel_sum_f32(x,N-1,1)==x[N-1]);
}
'''.replace('XS',','.join(x.hex()+'f' for x in xs)).replace('YS',','.join(y.hex()+'f' for y in ys)).replace('N',str(len(xs)))
            (folder/'main.cpp').write_text(source)
            subprocess.run(['clang++','-std=c++17','-O1','-ffp-contract=off','-fsanitize=undefined','-fno-sanitize-recover=all','-I',d,str(folder/'main.cpp'),'-o',str(folder/'test')],check=True,capture_output=True,text=True,timeout=60)
            subprocess.run([str(folder/'test')],check=True,capture_output=True,text=True,timeout=30)

if __name__=='__main__':unittest.main(verbosity=2)
