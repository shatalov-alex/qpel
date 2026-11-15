"""Compile the kernels of a Triton source to TTIR, TTGIR and PTX (run in the qpel/triton image).

    python compile.py kernels.py kernels.json out/

Each kernel of kernels.json (its signature and constexpr values, optionally num_warps) is
compiled for an NVIDIA sm_75 target (the GPGPU-Sim config qpel runs) without a GPU, with
floating-point contraction off (enable_fp_fusion=False: a * b + c stays two roundings, as qpel
computes it), and written as out/<kernel>.ttir, .ttgir, .ptx and .json (the launch metadata).
"""
import importlib.util
import json
import sys
from pathlib import Path

import triton
from triton.backends.compiler import GPUTarget
from triton.compiler import ASTSource

source, specs, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
loader = importlib.util.spec_from_file_location(source.stem, source)
module = importlib.util.module_from_spec(loader)
loader.loader.exec_module(module)
for name, spec in specs.items():
    if len(sys.argv) > 4 and name not in sys.argv[4:]:
        continue
    fn = getattr(module, name)
    signature = dict(spec['signature'], **{k: 'constexpr' for k in spec.get('constexprs', {})})
    options = {'num_warps': spec.get('num_warps', 4), 'enable_fp_fusion': False}
    compiled = triton.compile(ASTSource(fn, signature=signature, constexprs=spec.get('constexprs', {})),
                              target=GPUTarget('cuda', 75, 32), options=options)
    for key in ('ttir', 'ttgir', 'ptx'):
        (out/f'{name}.{key}').write_text(compiled.asm[key])
    meta = compiled.metadata
    (out/f'{name}.json').write_text(json.dumps({'kernel': name, 'triton': triton.__version__, 'num_warps': meta.num_warps,
                                                'shared': meta.shared, 'ptx_name': meta.name,
                                                'signature': spec['signature'], 'constexprs': spec.get('constexprs', {}),
                                                'options': options, 'target': 'cuda sm_75'}, indent=1) + '\n')
    print('compiled', name, 'warps', meta.num_warps, 'shared', meta.shared)
