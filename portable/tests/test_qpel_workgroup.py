import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate,Lowering,PROFILES
from qpel_types import TypeChecker
from build_backends import build
FIXTURE=Path(__file__).parent/'fixtures/qpel_ir/workgroup'

class WorkgroupTests(unittest.TestCase):
    def setUp(self):
        self.ir=json.loads((FIXTURE / 'program.qpel.json').read_text())
        self.c=TypeChecker(self.ir)
        self.fn=self.c.functions[0];self.body=self.c.child(self.fn,'body')
        self.local=self.c.children(self.body,'statements')[0]
        self.group=self.c.child(self.local,'value')

    def reject(self,code):
        for target in ('cuda','metal'):
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,code):validate(self.ir,target)

    def test_early_return_rejected(self):
        self.ir['nodes'].append(dict(id='return',kind='Return',attributes={},links={},children={}))
        self.body['children']['statements'].insert(0,'return');self.reject('COLLECTIVE_FLOW')

    def test_conditional_collective_rejected(self):
        guard=next(n for n in self.ir['nodes'] if n['kind']=='If')
        nested=self.c.child(guard,'thenBody')
        self.body['children']['statements'].remove(self.local['id'])
        nested['children']['statements'].insert(0,self.local['id'])
        self.reject('COLLECTIVE_CONTEXT')

    def test_nested_collective_rejected(self):
        self.ir['nodes'].append(dict(id='outergroup',kind='WorkgroupSumF32',attributes={},links={},children={'value':[self.group['id']]}))
        self.local['children']['value']=['outergroup'];self.reject('COLLECTIVE_CONTEXT')

    def test_capability(self):
        self.assertIn('workgroup_sum_f32',validate(self.ir,'metal')['required_features'])
        PROFILES['no-group']=PROFILES['metal']-{'workgroup_sum_f32'}
        try:
            with self.assertRaisesRegex(ValueError,'workgroup_sum_f32'):validate(self.ir,'no-group')
        finally:del PROFILES['no-group']

    def test_metal_grid_builtin_dimensions(self):
        # Metal requires matching dimensions for grid position and group size.
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            source=Lowering(self.ir).write(folder/'neutral')
            build(source,folder/'generated')
            shader=(folder/'generated/float_group_sum.metal').read_text()
            # The Grid3D support block is compiled only for 3D kernels; it is not part of this 1D shader.
            self.assertNotIn('#define QPEL_GRID3D_ENABLED',shader)
            self.assertNotIn('#define QPEL_GROUPS_ENABLED',shader)
            shader=re.sub(r'#ifdef QPEL_(GRID3D|GROUPS)_ENABLED.*?#endif','',shader,flags=re.S)
            types=re.findall(r'\b(uint[23]?)\s+\w+\s*\[\[(?:thread_position_in_grid|threads_per_threadgroup)\]\]',shader)
            self.assertGreaterEqual(len(types),2)
            self.assertEqual(set(types),{'uint'},'1D dispatch requires scalar grid builtins')
            self.assertNotIn('qpel_group_size.x',shader)

    @unittest.skipUnless(shutil.which('clang++'),'clang++ required')
    def test_threaded_cuda_simulation_and_scratch_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);source=Lowering(self.ir).write(folder/'neutral');build(source,folder/'generated')
            (folder/'cuda_runtime.h').write_text('''#pragma once
#include <condition_variable>
#include <mutex>
#define __global__
#define __device__
#define __forceinline__ inline
#define __shared__ static
struct Dim {unsigned int x;};
static Dim blockIdx{0},blockDim{1};
static thread_local Dim threadIdx{0};
struct Barrier {
 unsigned width,arrived=0,generation=0;std::mutex mutex;std::condition_variable cv;
 explicit Barrier(unsigned n):width(n){}
 void wait(){std::unique_lock<std::mutex> lock(mutex);auto g=generation;
 if(++arrived==width){arrived=0;++generation;cv.notify_all();}
 else cv.wait(lock,[&]{return generation!=g;});}
};
static Barrier* barrier;
inline void __syncthreads(){barrier->wait();}
inline float __fadd_rn(float a,float b){volatile float result=a+b;return result;}
inline float __fsub_rn(float a,float b){volatile float r=a-b;return r;}
inline float __fmul_rn(float a,float b){volatile float r=a*b;return r;}
inline float __fdiv_rn(float a,float b){volatile float r=a/b;return r;}
''')
            (folder/'main.cpp').write_text('''#include <cassert>
#include <thread>
#include <vector>
#include "generated/float_group_sum.cu"
float oracle(std::vector<float> v){
 for(unsigned s=1;s<v.size();s*=2)for(unsigned i=0;i+s<v.size();i+=2*s){volatile float x=v[i]+v[i+s];v[i]=x;}
 return v[0];
}
int main(){
 for(unsigned width:{1u,3u,8u,32u,64u,256u})for(unsigned n:{1u,17u,67u}){
  std::vector<float>x(n),out(n);std::vector<unsigned char>mask(n);
  for(unsigned i=0;i<n;++i){x[i]=(float(i)-8)*0.25f;mask[i]=i%3;}
  blockDim.x=width;
  for(unsigned start=0;start<n;start+=width){
   blockIdx.x=start/width;Barrier b(width);barrier=&b;std::vector<std::thread>threads;
   for(unsigned lane=0;lane<width;++lane)threads.emplace_back([&,lane]{threadIdx.x=lane;masked_copy(x.data(),mask.data(),out.data(),QpelShape{n,1,1,0});});
   for(auto&thread:threads)thread.join();
   std::vector<float> vals(width,0);for(unsigned lane=0;lane<width&&start+lane<n;++lane)vals[lane]=mask[start+lane]?x[start+lane]*0.5f:0;
   float expected=oracle(vals);for(unsigned lane=0;lane<width&&start+lane<n;++lane)assert(out[start+lane]==expected);
  }
 }
 // Two consecutive collectives share scratch; all prior readers must finish.
 for(unsigned width:{3u,32u,65u}){
  float scratch[256];std::vector<float>a(width),b(width);Barrier sync(width);barrier=&sync;std::vector<std::thread>threads;
  for(unsigned lane=0;lane<width;++lane)threads.emplace_back([&,lane]{
   a[lane]=qpel_group_sum_f32(float(lane),scratch,lane,width);
   b[lane]=qpel_group_sum_f32(1.0f,scratch,lane,width);
  });
  for(auto&thread:threads)thread.join();
  for(unsigned lane=0;lane<width;++lane){assert(a[lane]==float(width*(width-1)/2));assert(b[lane]==float(width));}
 }
}
''')
            subprocess.run(['clang++','-std=c++17','-O1','-pthread','-ffp-contract=off','-fsanitize=undefined','-fno-sanitize-recover=all','-I',d,str(folder/'main.cpp'),'-o',str(folder/'test')],check=True,capture_output=True,text=True,timeout=60)
            subprocess.run([str(folder/'test')],check=True,capture_output=True,text=True,timeout=60)

if __name__=='__main__':unittest.main(verbosity=2)
