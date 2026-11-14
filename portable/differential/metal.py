"""Run Metal sources on the Apple GPU through torch.mps.compile_shader.

`run_original` drives an unmodified Metal source from a qpel launch plan, using
the argument map the importer recorded (as simulate.Program does for CUDA): each
stage dispatches the original kernel with its arguments in [[buffer(n)]] order,
one thread per element of the stage's shape, in the plan's threadgroups. That
is how qpel's generated Metal host launches the same stage.
"""
import os
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '0')  # a CPU fallback would hide GPU failures

import re

import numpy as np
import torch

from .simulate import DTYPES, VALUE_TYPES, volume
from qpel_types import buffer_length, is_struct, pack_struct
from .metal_text import inline_includes, instantiating_only, specialise  # noqa: F401 (re-exported)

TORCH = {np.uint8: torch.uint8, np.uint32: torch.int32, np.int32: torch.int32, np.float32: torch.float32,
         np.float16: torch.float16}
SCALAR = {'u32': torch.int32, 'i32': torch.int32, 'f32': torch.float32, 'u64': torch.int64, 'i64': torch.int64,
          'f16': torch.float16}
WRAP = {'u32': 32, 'u64': 64}  # unsigned values travel as the signed type's bit pattern


def available():
    return torch.backends.mps.is_available()


def tensor(array, dtype):
    array = np.ascontiguousarray(array).reshape(-1)
    signed = {np.dtype(np.uint16): np.int16, np.dtype(np.uint32): np.int32, np.dtype(np.uint64): np.int64}
    if array.dtype in signed:  # the hosts' storage types (qpel_types.TORCH_DTYPES)
        array = array.view(signed[array.dtype])
    return torch.from_numpy(array.copy()).to('mps')


def numpy(value, dtype):
    """A tensor as the qpel element type: u16, u32 and u64 travel as signed tensors, bf16 as bfloat16."""
    if value.dtype == torch.bfloat16:
        value = value.view(torch.int16)
    out = value.cpu().numpy()
    dtype = np.dtype(dtype)
    return out.view(dtype) if out.dtype != dtype and out.dtype.itemsize == dtype.itemsize else out


_LIBRARIES = {}


def _compiled(text):
    """compile_shader, once per source text (ggml-metal.metal is 11,000 lines)."""
    if text not in _LIBRARIES:
        _LIBRARIES.clear()
        _LIBRARIES[text] = torch.mps.compile_shader(text)
    return _LIBRARIES[text]


def run_original(source, plan, report, case, include=(), constants=None, whole_groups=False):
    """{data resource: output array} after running every stage of `plan` with the original kernels.

    One thread per element is dispatched as an exact thread count, so the last threadgroup may
    be partial (dispatchThreads), unless whole_groups: then whole threadgroups covering the
    elements (dispatchThreadgroups, as CUDA launches blocks and as ggml's Metal host does), and
    the kernels must check their bounds."""
    needed = {report['qualified'][s['kernel']] for s in plan['stages']}
    # Run the original as written: no FMA contraction, as the CPU simulator (-ffp-contract=off).
    text = instantiating_only(specialise(inline_includes(source, include), constants, report.get('shared')), needed)
    lib = _compiled('#pragma clang fp contract(off)\n' + text)
    resources = {r['name']: r for r in plan['resources']}
    buffers = {}
    for r in plan['resources']:
        if r['kind'] in ('shape', 'value'):
            continue
        dtype = DTYPES[r['type']]
        count = buffer_length(r, case.shapes)
        array = case.inputs[r['name']] if r['kind'] in ('input', 'inout') else np.zeros(count, dtype)
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
            # A count goes at the argument's own width: a `constant ulong& N` reads 8 bytes.
            width = SCALAR.get(a.get('type'), torch.int32)
            if a['source'] == 'extent':
                args.append(torch.tensor([case.shapes[name]['xyz'.index(a['axis'])]], device='mps', dtype=width))
            elif a['source'] == 'volume':
                args.append(torch.tensor([volume(case.shapes[name])], device='mps', dtype=width))
            elif a['source'] == 'parameter' and resources[name]['kind'] == 'shape':
                args.append(torch.tensor([*case.shapes[name], 0], device='mps', dtype=torch.int32))
            elif a['source'] == 'parameter' and resources[name]['kind'] == 'value' and is_struct(resources[name]['type']):
                packed = pack_struct(plan['structs'][resources[name]['type'][7:]], case.values[name])
                args.append(torch.frombuffer(bytearray(packed), dtype=torch.uint8).to('mps'))
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
            block = tuple(s['blockShape'])
            threads = tuple(-(-n // b) * b for n, b in zip(shape, block)) if whole_groups else tuple(shape)
            function(*args, threads=threads, group_size=block)
        else:
            block = s['blockSize']
            function(*args, threads=-(-volume(shape) // block) * block if whole_groups else volume(shape), group_size=block)
        torch.mps.synchronize()
    return {name: numpy(t, DTYPES[resources[name]['type']]) for name, t in buffers.items()}


def run_generated(host, plan, case):
    """{output resource: array} from a qpel-generated Metal host module."""
    inputs = {r['name']: tensor(np.asarray(case.inputs[r['name']], DTYPES[r['type']]), DTYPES[r['type']])
              for r in plan['resources'] if r['kind'] in ('input', 'inout')}
    result = host.run(inputs, case.shapes, case.values)
    types = {r['name']: DTYPES[r['type']] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
    return {name: numpy(t, types[name]) for name, t in result.items()}
