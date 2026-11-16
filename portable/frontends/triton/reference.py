"""Run Triton kernels in Triton's interpreter for reference outputs (run in the qpel/triton image).

    TRITON_INTERPRET=1 python reference.py kernels.py kernels.json cases/
    TRITON_INTERPRET=1 python reference.py corpus_root/ kernels.json cases/   # a package (corpus/liger)

Every folder of cases/ holds case.json ({kernel, grid, args: [{name, buffer: file} or
{name, value}]}) and the buffers' .npy files. The kernel runs once with those arguments (buffers as
CPU torch tensors, the constexprs of kernels.json), and every buffer is written back as
out_<file> (the interpreter computes with numpy: IEEE float32 operations, in the order the
program gives them; a reduction in numpy's order).
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

assert os.environ.get('TRITON_INTERPRET') == '1', 'run with TRITON_INTERPRET=1'
import numpy as np  # noqa: E402
import torch  # noqa: E402

import triton.language as tl  # noqa: E402
from triton.language.extra import libdevice  # noqa: E402
from triton.runtime import interpreter  # noqa: E402

# Triton's interpreter cannot run libdevice (its functions are stubs there that return None). Kernels
# that call them run with these numpy float32 versions, installed in triton.language.extra.libdevice
# before the corpus is imported (Liger binds `from ...libdevice import rsqrt` at import): rsqrt as the
# interpreter's own tl.math.rsqrt (1 / np.sqrt, two roundings), tanh as numpy's float32 tanh, erf as the
# interpreter's own float32 erf. They are bounded functions: rows compare within 2 * MATH_ULPS.
NUMPY = {
    'rsqrt': lambda x: np.float32(1) / np.sqrt(x),
    'tanh': np.tanh,
    'erf': lambda x: interpreter.np_erf_fp32(x),
}


def numpy_libdevice(name, f):
    def call(x, _semantic=None):
        if x.dtype != tl.float32:
            raise NotImplementedError(f'libdevice.{name} on {x.dtype} (frontends/triton/reference.py)')
        data = np.asarray(f(x.handle.data), np.float32)
        return tl.core.tensor(interpreter.TensorHandle(data, x.handle.dtype.scalar), x.type)
    return call


def scalar(value):
    return tl.core.tensor(interpreter.TensorHandle(np.array([value], np.float32), tl.float32), tl.float32)


for _name, _f in NUMPY.items():
    setattr(libdevice, _name, numpy_libdevice(_name, _f))
source, specs, cases = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
if source.is_dir():  # a vendored package (corpus/liger): kernels.json names functions as module:name
    sys.path.insert(0, str(source))
    module = None
else:
    loader = importlib.util.spec_from_file_location(source.stem, source)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)


def kernel(name, spec):
    if module is not None:
        return getattr(module, name)
    path, function = spec['function'].split(':')
    return getattr(importlib.import_module(path), function)


for folder in sorted(p for p in cases.iterdir() if p.is_dir()):
    case = json.loads((folder/'case.json').read_text())
    spec = specs[case['kernel']]
    args, buffers = [], {}
    for arg in case['args']:
        if 'buffer' in arg:
            buffers[arg['buffer']] = torch.from_numpy(np.load(folder/arg['buffer']))
            args.append(buffers[arg['buffer']])
        else:
            kind = spec['signature'][arg['name']]
            # a float scalar as a float32 scalar of the interpreter (as compiled code has it: Liger calls
            # gate_multiplier.to(tl.float32), which a Python float has not)
            args.append(scalar(np.float32(arg['value'])) if kind.startswith('fp') else int(arg['value']))
    kernel(case['kernel'], spec)[tuple(case['grid'])](*args, **spec.get('constexprs', {}),
                                                         num_warps=spec.get('num_warps', 4))
    for name, tensor in buffers.items():
        np.save(folder/('out_' + name), tensor.numpy())
    print('ran', folder.name)
