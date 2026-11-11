import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate,Lowering,PROFILES
from build_backends import build
FIXTURE=Path(__file__).parent/'fixtures/qpel_ir/fma32'

def f32(x):return struct.unpack('f',struct.pack('f',x))[0]

def vectors():
    # These dyadic products/sums are exactly representable in binary64;
    # only the final pack rounds to binary32. No FMA implementation in the oracle.
    xs=[1+2**-23,1.0,0.0,-1.0]+[i/8 for i in range(-256,257)]
    ys=[f32(x*(1-2**-23)-1) for x in xs]
    assert ys[0]==-2**-46
    assert f32(f32(xs[0]*(1-2**-23))-1)==0
    return xs,ys

class FmaTests(unittest.TestCase):
    def setUp(self):
        self.ir=json.loads((FIXTURE / 'program.qpel.json').read_text())
        self.nodes={n['id']:n for n in self.ir['nodes']}
        self.fma=next(n for n in self.ir['nodes'] if n['kind']=='F32Fma')

    def test_integer_operand_rejected(self):
        n=self.nodes[self.fma['children']['right'][0]]
        n.update(kind='Literal',attributes={'value':'1'})
        for backend in ('cuda','metal'):
            with self.assertRaisesRegex(ValueError,'FMA_TYPE'):validate(self.ir,backend)

    def test_capability_is_explicit(self):
        for backend in ('cuda','metal'):self.assertIn('float32_fma',validate(self.ir,backend)['required_features'])
        PROFILES['no-fma']=PROFILES['metal']-{'float32_fma'}
        try:
            with self.assertRaisesRegex(ValueError,'float32_fma'):validate(self.ir,'no-fma')
        finally:del PROFILES['no-fma']

    @unittest.skipUnless(shutil.which('clang++'),'clang++ required')
    def test_native_fma_against_independent_oracle(self):
        xs,ys=vectors()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);source=Lowering(self.ir).write(folder/'neutral');build(source,folder/'generated')
            self.assertIn('__fmaf_rn',(folder/'generated/float_fma.cu').read_text())
            self.assertIn('metal::fma',(folder/'generated/float_fma.metal').read_text())
            (folder/'cuda_runtime.h').write_text('''#pragma once
#include <cmath>
#define __global__
#define __device__
#define __forceinline__ inline
struct Dim { unsigned int x; };
static Dim blockIdx{0}, blockDim{1}, threadIdx{0};
inline float __fmaf_rn(float a,float b,float c){return std::fma(a,b,c);}
''')
            source='''#include <cassert>
#include "generated/float_fma.cu"
int main(){
float x[]={XS};float expected[]={YS};
float output[N];unsigned char mask[N];
for(unsigned i=0;i<N;++i) mask[i]=1;
for(unsigned i=0;i<N+5;++i){threadIdx.x=i;masked_copy(x,mask,output,QpelShape{N,1,1,0});}
for(unsigned i=0;i<N;++i) assert(output[i]==expected[i]);
assert(output[0]!=0.0f);
}
'''.replace('XS',','.join(x.hex()+'f' for x in xs)).replace('YS',','.join(y.hex()+'f' for y in ys)).replace('N',str(len(xs)))
            (folder/'main.cpp').write_text(source)
            subprocess.run(['clang++','-std=c++17','-O1','-ffp-contract=off','-fsanitize=undefined','-fno-sanitize-recover=all','-I',d,str(folder/'main.cpp'),'-o',str(folder/'test')],check=True,capture_output=True,text=True,timeout=60)
            subprocess.run([str(folder/'test')],check=True,capture_output=True,text=True,timeout=30)

if __name__=='__main__':unittest.main(verbosity=2)
