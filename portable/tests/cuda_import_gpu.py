"""Requires Apple GPU; test the generated threshold host and shader together."""
import os
os.environ['PYTORCH_ENABLE_MPS_FALLBACK']='0'
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import torch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from generated import metal_host


def main():
    provenance=json.loads((HERE/'import-report.json').read_text())
    if hashlib.sha256((HERE/'original.cu').read_bytes()).hexdigest()!=provenance['source_sha256']:
        raise RuntimeError('Original CUDA source no longer matches imported model provenance')
    if not torch.backends.mps.is_available():raise RuntimeError('Run in ordinary macOS Terminal: Apple GPU unavailable here')
    rng=np.random.default_rng(20260925)
    v=np.arange(256,dtype=np.uint8)
    cases=[(np.broadcast_to(v[:,None],(256,256))[None].copy(),np.broadcast_to(v[None,:],(256,256))[None].copy())]
    for a,b in [(127,128),(128,128),(255,0),(0,255)]:cases.append((np.array([[[a]]],np.uint8),np.array([[[b]]],np.uint8)))
    for i in range(129):
        n,h,w=[int(x) for x in (rng.integers(1,5),rng.integers(1,36),rng.integers(1,66))]
        cases.append((rng.integers(0,256,(n,h,w),dtype=np.uint8),rng.integers(0,256,(n,h,w),dtype=np.uint8)))
    for index,(a,b) in enumerate(cases):
        source=torch.from_numpy(a).to('mps');cutoff=torch.from_numpy(b).to('mps')
        if index%3==0:
            source=source.transpose(-1,-2);cutoff=cutoff.transpose(-1,-2)
            a=a.swapaxes(-1,-2);b=b.swapaxes(-1,-2)
        n,h,w=a.shape
        actual=metal_host.run({'intensity':source,'cutoff':cutoff},{'dimensions':(w,h,n)})['binary']
        assert actual.device.type=='mps' and actual.dtype==torch.int32
        np.testing.assert_array_equal(actual.cpu().numpy().reshape(a.shape),(a>=b).astype(np.int32))
    manifest=json.loads((HERE/'generated/manifest.json').read_text())
    for name,expected in [('metal_host.py',manifest['metal_host_sha256']),('cuda_threshold.metal',manifest['outputs']['metal']['sha256']),('pipeline.json',manifest['pipeline_sha256'])]:
        assert hashlib.sha256((HERE/'generated'/name).read_bytes()).hexdigest()==expected
    report={'gpu_tested':True,'cases_passed':len(cases),'all_uint8_pairs':65536,'torch':torch.__version__,
            'metal_host_sha256':manifest['metal_host_sha256'],'shader_sha256':manifest['outputs']['metal']['sha256'],
            'pipeline_sha256':manifest['pipeline_sha256'],'cpu_fallback_enabled':False,
            'original_cuda_sha256':hashlib.sha256((HERE/'original.cu').read_bytes()).hexdigest()}
    (HERE/'gpu-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS: {len(cases)} CUDA-imported threshold GPU cases; all 65536 uint8 pairs, boundary values, batches, odd sizes and noncontiguous inputs')
    print('PASS: CUDA source → qpel model → generated Metal and host: GPU results match oracle')
    print('Report:',HERE/'gpu-report.json')

if __name__=='__main__':main()
