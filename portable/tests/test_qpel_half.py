import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qpel_ir import export,validate,Lowering
from build_backends import build
FIXTURE=Path(__file__).parent/'fixtures/qpel_ir/half'

class HalfTests(unittest.TestCase):
    def setUp(self):
        self.ir=export(*(json.loads((FIXTURE/f).read_text()) for f in ('module.json','pipeline.json')))
        self.nodes={n['id']:n for n in self.ir['nodes']}
        self.fma=next(n for n in self.ir['nodes'] if n['kind']=='F32Fma')

    def reject(self,code):
        for backend in ('cuda','metal'):
            with self.subTest(backend=backend),self.assertRaisesRegex(ValueError,code):validate(self.ir,backend)

    def remove_promotion(self):
        cast=self.nodes[self.fma['children']['left'][0]]
        self.fma['children']['left']=cast['children']['operand']
        self.ir['nodes'].remove(cast)

    def test_no_implicit_half_fma(self):
        self.remove_promotion();self.reject('FMA_TYPE')

    def test_no_implicit_half_arithmetic(self):
        self.remove_promotion()
        removed=self.fma['children'].pop('addend')[0]
        self.ir['nodes'].remove(self.nodes[removed])
        self.fma.update(kind='Binary',attributes={'operator':'+'})
        self.reject('BINARY')

    def test_no_implicit_narrowing(self):
        store=next(n for n in self.ir['nodes'] if n['kind']=='F16Cast')
        assignment=next(n for n in self.ir['nodes'] if n['kind']=='Assign')
        assignment['children']['value']=store['children']['operand']
        self.ir['nodes'].remove(store)
        self.reject('ASSIGN_TYPE')

    def test_input_half_readonly(self):
        assignment=next(n for n in self.ir['nodes'] if n['kind']=='Assign')
        lhs=self.nodes[assignment['children']['target'][0]]
        base=self.nodes[lhs['children']['base'][0]]
        pixels=next(n for n in self.ir['nodes'] if n['kind']=='Parameter' and n['attributes']['name']=='pixels')
        base['links']['declaration']=pixels['id'];self.reject('READ_ONLY')

    @unittest.skipUnless(shutil.which('clang++'),'clang++ required')
    def test_all_finite_half_encodings_on_cpu(self):
        values=[];expected=[]
        for bits in range(65536):
            if bits & 0x7c00 == 0x7c00:continue
            packed=struct.pack('<H',bits);x=struct.unpack('<e',packed)[0]
            # F32 FMA x*1 + 2^-11: binary64 is exact for these dyadic inputs.
            f32=struct.unpack('<f',struct.pack('<f',x+2**-11))[0]
            values.append(packed);expected.append(struct.pack('<e',f32))
        self.assertEqual(len(values),63488)
        for backend in ('metal','cuda'):self.assertIn('float16_storage',validate(self.ir,backend)['required_features'])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);source=Lowering(self.ir).write(folder/'neutral');build(source,folder/'generated')
            host=(folder/'generated/metal_host.py').read_text();self.assertIn('torch.float16',host)
            (folder/'input.bin').write_bytes(b''.join(values));(folder/'expected.bin').write_bytes(b''.join(expected))
            (folder/'cuda_runtime.h').write_text('''#pragma once
#include <cmath>
#define __global__
#define __device__
#define __forceinline__ inline
struct Dim { unsigned int x; };
static Dim blockIdx{0},blockDim{1},threadIdx{0};
inline float __fmaf_rn(float a,float b,float c){return std::fma(a,b,c);}
''')
            (folder/'cuda_fp16.h').write_text('''#pragma once
using __half = _Float16;
inline __half __float2half_rn(float v){return (__half)v;}
''')
            (folder/'main.cpp').write_text('''#include <cassert>
#include <fstream>
#include <vector>
#include <cstring>
#include <cfenv>
#include "generated/half_mixed_scale.cu"
int main(){
 std::fesetround(FE_TONEAREST);
 constexpr unsigned N=63488;
 static_assert(sizeof(__half)==2);
 std::vector<__half> input(N),output(N),expected(N);
 std::vector<unsigned char> mask(N,1);
 std::ifstream("input.bin",std::ios::binary).read((char*)input.data(),N*2);
 std::ifstream("expected.bin",std::ios::binary).read((char*)expected.data(),N*2);
 for(unsigned i=0;i<N+5;++i){threadIdx.x=i;masked_copy(input.data(),mask.data(),output.data(),QpelShape{N,1,1,0});}
 assert(std::memcmp(output.data(),expected.data(),N*2)==0);
}
''')
            subprocess.run(['clang++','-std=c++17','-O1','-ffp-contract=off','-fsanitize=undefined','-fno-sanitize-recover=all','-I',d,str(folder/'main.cpp'),'-o',str(folder/'test')],check=True,capture_output=True,text=True,timeout=60)
            subprocess.run([str(folder/'test')],cwd=folder,check=True,capture_output=True,text=True,timeout=30)

if __name__=='__main__':unittest.main(verbosity=2)
