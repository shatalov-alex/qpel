"""Run Triton kernels in Triton's interpreter for reference outputs (run in the qpel/triton image).

    TRITON_INTERPRET=1 python reference.py kernels.py kernels.json cases/

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

source, specs, cases = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
loader = importlib.util.spec_from_file_location(source.stem, source)
module = importlib.util.module_from_spec(loader)
loader.loader.exec_module(module)
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
            args.append(float(np.float32(arg['value'])) if kind.startswith('fp') else int(arg['value']))
    getattr(module, case['kernel'])[tuple(case['grid'])](*args, **spec.get('constexprs', {}),
                                                         num_warps=spec.get('num_warps', 4))
    for name, tensor in buffers.items():
        np.save(folder/('out_' + name), tensor.numpy())
    print('ran', folder.name)
