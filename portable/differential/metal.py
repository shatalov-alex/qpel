"""Run Metal sources on the Apple GPU through torch.mps.compile_shader.

`run_original` drives an unmodified Metal source from a qpel launch plan, using
the argument map the importer recorded (as simulate.Program does for CUDA): each
stage dispatches the original kernel with its arguments in [[buffer(n)]] order,
one thread per element of the stage's shape, in the plan's threadgroups. That
is how qpel's generated Metal host launches the same stage.
"""
import os
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '0')  # a CPU fallback would hide GPU failures

import numpy as np
import torch

from .simulate import DTYPES, VALUE_TYPES, volume

TORCH = {np.uint8: torch.uint8, np.uint32: torch.int32, np.int32: torch.int32, np.float32: torch.float32,
         np.float16: torch.float16}
SCALAR = {'u32': torch.int32, 'i32': torch.int32, 'f32': torch.float32, 'u64': torch.int64, 'i64': torch.int64}
WRAP = {'u32': 32, 'u64': 64}  # unsigned values travel as the signed type's bit pattern


def available():
    return torch.backends.mps.is_available()


def tensor(array, dtype):
    array = np.ascontiguousarray(array).reshape(-1)
    if array.dtype == np.uint32:
        array = array.view(np.int32)
    return torch.from_numpy(array.copy()).to('mps')


def numpy(value, dtype):
    out = value.cpu().numpy()
    return out.view(np.uint32) if dtype == np.uint32 else out


def run_original(source, plan, report, case):
    """{data resource: output array} after running every stage of `plan` with the original kernels."""
    # Run the original as written: no FMA contraction, as the CPU simulator (-ffp-contract=off).
    lib = torch.mps.compile_shader('#pragma clang fp contract(off)\n' + open(source).read())
    resources = {r['name']: r for r in plan['resources']}
    buffers = {}
    for r in plan['resources']:
        if r['kind'] in ('shape', 'value'):
            continue
        dtype = DTYPES[r['type']]
        count = volume(case.shapes[r['extent']])
        array = case.inputs[r['name']] if r['kind'] == 'input' else np.zeros(count, dtype)
        buffers[r['name']] = tensor(np.asarray(array, dtype), dtype)
    for s in plan['stages']:
        params = {b['parameter']: b['resource'] for b in s['bindings']}
        shape = case.shapes[s['grid']]
        arguments = sorted(report['arguments'][s['kernel']], key=lambda a: a['binding'])
        if [a['binding'] for a in arguments] != list(range(len(arguments))):
            raise ValueError(f'{s["kernel"]}: buffer indices must be 0..{len(arguments) - 1} for compile_shader')
        args = []
        for a in arguments:
            name = params.get(a.get('parameter'))
            if a['source'] == 'extent':
                args.append(torch.tensor([case.shapes[name]['xyz'.index(a['axis'])]], device='mps', dtype=torch.int32))
            elif a['source'] == 'volume':
                args.append(torch.tensor([volume(case.shapes[name])], device='mps', dtype=torch.int32))
            elif a['source'] == 'parameter' and resources[name]['kind'] == 'shape':
                args.append(torch.tensor([*case.shapes[name], 0], device='mps', dtype=torch.int32))
            elif a['source'] == 'parameter' and resources[name]['kind'] == 'value':
                kind = VALUE_TYPES[resources[name]['type']]
                value = case.values[name]
                if kind in WRAP and value >= 2**(WRAP[kind] - 1):
                    value -= 2**WRAP[kind]
                args.append(torch.tensor([value], device='mps', dtype=SCALAR[kind]))
            elif a['source'] == 'parameter':
                args.append(buffers[name])
            else:
                raise ValueError(f'{s["kernel"]}: argument {a["name"]} has no qpel equivalent')
        function = getattr(lib, report['qualified'][s['kernel']])  # Metal names namespaced kernels in full
        if s.get('domain') == 'groups':
            from qpel_schema import group_count
            block = tuple(s['blockShape'])
            threads = tuple(group_count(g, shape)*b for g, b in zip(s['groups'], block))
            function(*args, threads=threads, group_size=block)
        elif s.get('domain') == 'grid3d':
            function(*args, threads=tuple(shape), group_size=tuple(s['blockShape']))
        else:
            function(*args, threads=volume(shape), group_size=s['blockSize'])
        torch.mps.synchronize()
    return {name: numpy(t, DTYPES[resources[name]['type']]) for name, t in buffers.items()}


def run_generated(host, plan, case):
    """{output resource: array} from a qpel-generated Metal host module."""
    inputs = {r['name']: tensor(np.asarray(case.inputs[r['name']], DTYPES[r['type']]), DTYPES[r['type']])
              for r in plan['resources'] if r['kind'] == 'input'}
    result = host.run(inputs, case.shapes, case.values)
    types = {r['name']: DTYPES[r['type']] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
    return {name: numpy(t, types[name]) for name, t in result.items()}
