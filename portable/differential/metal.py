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
from qpel_types import is_struct, pack_struct

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


def inline_includes(path, include=(), done=None):
    """Source text with its quoted #includes from the library's folders pasted in, once each
    (compile_shader takes one string; ggml embeds its headers the same way)."""
    import re
    from pathlib import Path
    done = set() if done is None else done
    path = Path(path)
    out = []
    for line in path.read_text().splitlines():
        found = re.match(r'\s*#\s*include\s+"([^"]+)"', line)
        if found:
            for folder in [path.parent] + [Path(f) for f in include]:
                candidate = (folder/found.group(1)).resolve()
                if candidate.exists():
                    if candidate not in done:
                        done.add(candidate)
                        out.append(inline_includes(candidate, include, done))
                    break
            else:
                out.append(line)
            continue
        out.append(line)
    return '\n'.join(out)


def specialise(text, constants):
    """Give function constants their values, as pipeline creation would: compile_shader cannot
    pass constant values, and Metal will not build a pipeline for an unspecialised kernel."""
    import re
    for name, value in (constants or {}).items():
        literal = ('true' if value else 'false') if isinstance(value, bool) else repr(value)
        text, count = re.subn(r'constant\s+(\w+)\s+' + re.escape(name) + r'\s*\[\[\s*function_constant\([^]]*\)\s*\]\]\s*;',
                              lambda m: f'constant {m.group(1)} {name} = {literal};', text)
        if count != 1:
            raise ValueError(f'function constant {name}: found {count} declarations')
    return text


INSTANTIATION = re.compile(r'template\s*\[\[\s*host_name\("([^"]+)"\)\s*\]\][^;{]*;')


def instantiating_only(text, names):
    """The source without the explicit template instantiations (`template [[host_name("x")]]
    kernel ... f<T>;`) of kernels other than `names`. Nothing else changes, so the kernels that
    run are the original's; ggml-metal.metal has 544 such instantiations, and compiling them
    all for every specialisation is most of the cost of running one of its kernels."""
    return INSTANTIATION.sub(lambda m: m.group(0) if m.group(1) in names else '', text)


def run_original(source, plan, report, case, include=(), constants=None):
    """{data resource: output array} after running every stage of `plan` with the original kernels."""
    needed = {report['qualified'][s['kernel']] for s in plan['stages']}
    # Run the original as written: no FMA contraction, as the CPU simulator (-ffp-contract=off).
    text = instantiating_only(specialise(inline_includes(source, include), constants), needed)
    lib = _compiled('#pragma clang fp contract(off)\n' + text)
    resources = {r['name']: r for r in plan['resources']}
    buffers = {}
    for r in plan['resources']:
        if r['kind'] in ('shape', 'value'):
            continue
        dtype = DTYPES[r['type']]
        count = volume(case.shapes[r['extent']])
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
            if a['source'] == 'extent':
                args.append(torch.tensor([case.shapes[name]['xyz'.index(a['axis'])]], device='mps', dtype=torch.int32))
            elif a['source'] == 'volume':
                args.append(torch.tensor([volume(case.shapes[name])], device='mps', dtype=torch.int32))
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
            function(*args, threads=tuple(shape), group_size=tuple(s['blockShape']))
        else:
            function(*args, threads=volume(shape), group_size=s['blockSize'])
        torch.mps.synchronize()
    return {name: numpy(t, DTYPES[resources[name]['type']]) for name, t in buffers.items()}


def run_generated(host, plan, case):
    """{output resource: array} from a qpel-generated Metal host module."""
    inputs = {r['name']: tensor(np.asarray(case.inputs[r['name']], DTYPES[r['type']]), DTYPES[r['type']])
              for r in plan['resources'] if r['kind'] in ('input', 'inout')}
    result = host.run(inputs, case.shapes, case.values)
    types = {r['name']: DTYPES[r['type']] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
    return {name: numpy(t, types[name]) for name, t in result.items()}
