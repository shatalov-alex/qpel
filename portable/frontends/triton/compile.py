"""Compile the kernels of a Triton source to TTIR, TTGIR and PTX (run in the qpel/triton image).

    python compile.py kernels.py kernels.json out/
    python compile.py corpus_root/ kernels.json out/      # a package: names are module:kernel


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
if source.is_dir():  # a vendored package (corpus/liger): kernels are named module:function
    sys.path.insert(0, str(source))
    module = None
else:
    loader = importlib.util.spec_from_file_location(source.stem, source)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)


def constexprs(spec):
    """kernels.json's constexprs; a string "tl.float32" is that Triton dtype (Liger's X_dtype)."""
    import triton.language as tl
    return {k: getattr(tl, v[3:]) if isinstance(v, str) and v.startswith('tl.') else v
            for k, v in spec.get('constexprs', {}).items()}


def kernel(name, spec):
    if module is not None:
        return getattr(module, name)
    path, function = spec['function'].split(':')
    fn = getattr(importlib.import_module(path), function)
    from triton.runtime.autotuner import Autotuner, Heuristics
    while isinstance(fn, (Autotuner, Heuristics)):
        fn = fn.fn  # an @triton.autotune (or heuristics) wrapper: kernels.json gives the config's constexprs
    return fn


failed = []
for name, spec in specs.items():
    if len(sys.argv) > 4 and name not in sys.argv[4:]:
        continue
    fn = kernel(name, spec)
    signature = dict(spec['signature'], **{k: 'constexpr' for k in spec.get('constexprs', {})})
    options = {'num_warps': spec.get('num_warps', 4), 'enable_fp_fusion': False}
    try:
        compiled = triton.compile(ASTSource(fn, signature=signature, constexprs=constexprs(spec)),
                                  target=GPUTarget('cuda', 75, 32), options=options)
    except Exception as error:  # noqa: BLE001 - reported; the other kernels still compile
        failed.append(name)
        print('FAILED', name, str(error).strip().splitlines()[-1][:300])
        continue
    for key in ('ttir', 'ttgir', 'ptx'):
        (out/f'{name}.{key}').write_text(compiled.asm[key])
    meta = compiled.metadata
    (out/f'{name}.json').write_text(json.dumps({'kernel': name, **({'function': spec['function']} if 'function' in spec else {}), 'triton': triton.__version__, 'num_warps': meta.num_warps,
                                                'shared': meta.shared, 'ptx_name': meta.name,
                                                'signature': spec['signature'], 'constexprs': spec.get('constexprs', {}),
                                                'options': options, 'target': 'cuda sm_75'}, indent=1) + '\n')
    print('compiled', name, 'warps', meta.num_warps, 'shared', meta.shared)
if failed:
    sys.exit(f'{len(failed)} kernels did not compile: ' + ', '.join(failed))
