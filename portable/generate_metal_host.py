"""Generate a standalone eager PyTorch/Metal host module from a checked Pipeline."""
import hashlib
import keyword

from qpel_types import TORCH_DTYPES, VALUE_TERMS, access, element, is_buffer, is_struct, normalize_plan, packed_presence, packed_values, struct_layout

# Buffer element type -> torch dtype of the tensors the CuPy host historically shared (u32 as int32).
BUFFER_TYPES = {'u8': 'uint8', 'i8': 'int8', 'u16': 'int16', 'i16': 'int16', 'u32': 'int32', 'i32': 'int32',
                'u64': 'int64', 'i64': 'int64', 'f16': 'float16', 'bf16': 'bfloat16', 'f32': 'float32'}
VALUE_TYPES = {t: TORCH_DTYPES[t] for t in VALUE_TERMS}


def validate_resources(plan):
    plan = normalize_plan(plan)
    resources = {r['name']: r for r in plan['resources']}
    for r in resources.values():
        kind, typ = r['kind'], r['type']
        if kind == 'shape':
            if typ != 'shape3' or r.get('extent') is not None:
                raise ValueError('Shape requires shape3 and no extent')
        elif kind == 'value':
            if typ not in VALUE_TYPES and not (is_struct(typ) and typ[7:] in plan.get('structs', {})) or r.get('extent') is not None:
                raise ValueError('A value requires an integer, float or known struct type and no extent')
        elif kind in ('input', 'inout', 'output', 'scratch', 'zeroed'):
            if not is_buffer(typ) or element(typ) not in BUFFER_TYPES:
                raise ValueError('Unsupported buffer type: ' + typ)
            extent = resources.get(r.get('extent'))
            if (extent is None or extent['kind'] != 'shape') and not r.get('length'):
                raise ValueError('Buffer requires a shape extent')
            if kind != 'input' and access(typ) == 'read':
                raise ValueError('Read-only buffers must be inputs')
        else:
            raise ValueError('Unsupported resource kind: ' + kind)
    for stage in plan['stages']:
        if keyword.iskeyword(stage['kernel']):
            raise ValueError('Python keyword kernel name')


PRELUDE = '''# Generated from qpel Pipeline. Edit the model or generator, not this file.
from functools import lru_cache
import hashlib
from pathlib import Path
import torch

@lru_cache(maxsize=1)
def _library():
    source = Path(__file__).with_name(SHADER_FILE).read_bytes()
    if hashlib.sha256(source).hexdigest() != SHADER_SHA256:
        raise RuntimeError("Shader/host mismatch: regenerate the complete package")
    return torch.mps.compile_shader(source.decode())

def _shape(value):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("Shape must be (width, height, batch)")
    if any(type(v) is not int or v <= 0 for v in value):
        raise ValueError("Shape dimensions must be positive integers")
    w, h, n = value
    if w * h * n > 2**31 - 1:
        raise ValueError("Shape exceeds INT32_MAX elements")
    return (w, h, n), w * h * n

def _struct(value, layout, size):
    """A struct argument: {field: value}, packed in C's layout (checked like scalar arguments)."""
    import struct as _pack
    if not isinstance(value, dict) or set(value) != {f for f, _, _ in layout}:
        raise ValueError("Struct argument needs exactly the fields " + ", ".join(f for f, _, _ in layout))
    data = bytearray(size)
    for field, kind, offset in layout:
        number = value[field] if kind == 'bf16' else _value(value[field], kind) if kind in ('f32', 'f16') else value[field]
        if kind == 'bool':  # (a C bool field: one byte, 0 or 1)
            number = bool(number)
        elif kind not in ('f32', 'f16', 'bf16'):
            _value(number, kind)  # range check; packed with its own type below
        _pack.pack_into('<' + _PACK[kind], data, offset, number)
    return bytes(data)

_PACK = {'u8': 'B', 'i8': 'b', 'bool': '?', 'u16': 'H', 'i16': 'h', 'f16': 'e', 'bf16': 'H', 'u32': 'I', 'i32': 'i', 'f32': 'f', 'u64': 'Q', 'i64': 'q'}

def _value(value, kind):
    """A scalar kernel argument, checked against its qpel type."""
    if kind in ('f32', 'f16'):
        if type(value) not in (int, float):
            raise ValueError("Float argument must be a Python number")
        return float(value)
    if type(value) is not int:
        raise ValueError("Integer argument must be a Python int")
    bits = int(kind[1:])
    low, high = (0, 2**bits - 1) if kind[0] == 'u' else (-2**(bits - 1), 2**(bits - 1) - 1)
    if not low <= value <= high:
        raise ValueError("Integer argument is out of range for " + kind)
    if kind[0] == 'u' and kind != 'u8' and value >= 2**(bits - 1):
        return value - 2**bits  # unsigned values travel as the bit pattern of the signed type
    return value

def _groups(expressions, dims, block):
    """Group counts of a Groups stage (expressions of W, H, N, checked by qpel) and the threads they hold."""
    w, h, n = (int(v) for v in dims)
    counts = tuple(eval(e.replace('/', '//'), {'__builtins__': {}}, {'W': w, 'H': h, 'N': n}) for e in expressions)
    if any(c < 1 for c in counts):
        raise ValueError("Group counts must be positive: " + repr(counts))
    return counts, tuple(c * b for c, b in zip(counts, block))

def _input(value, dtype, count, device):
    if value.device != device or value.dtype != dtype or value.numel() != count:
        raise ValueError("Input device, dtype or element count does not match Pipeline")
    return value.contiguous().view(-1)

def _words(values, device):
    """Scalar arguments ((value, kind), in parameter order) as the 32-bit words of a packed-values buffer:
    a 64-bit value in two words, low first; a narrower integer extended to 32 bits; a half in the low 16."""
    import struct
    out = bytearray()
    for value, kind in values:
        if kind in ('u64', 'i64'):
            out += struct.pack('<Q', value & (2**64 - 1))
        elif kind == 'f32':
            out += struct.pack('<f', value)
        elif kind == 'f16':
            out += struct.pack('<eH', value, 0)
        else:
            out += struct.pack('<I', value & 0xffffffff)
    return torch.frombuffer(out, dtype=torch.int32).to(device)

'''


def _count(r):
    """Generated-code expression for a buffer's element count: its extent's volume, times its scale."""
    if r.get('length'):
        return str(r['length'])  # a fixed-length buffer
    count = f"sizes[{r['extent']!r}]" if r.get('per', 1) == 1 else f"(-(-sizes[{r['extent']!r}] // {r['per']}))"  # per tile (IR version 21)
    return count + (f" * {r['scale']}" if r.get('scale', 1) != 1 else '')


def generate(plan, shader):
    plan = normalize_plan(plan)
    validate_resources(plan)
    resources = plan['resources']
    inputs = [r['name'] for r in resources if r['kind'] in ('input', 'inout')]
    required = [r['name'] for r in resources if r['kind'] in ('input', 'inout') and not r.get('optional')]
    optional = {r['name'] for r in resources if r.get('optional')}
    shapes = [r['name'] for r in resources if r['kind'] == 'shape']
    outputs = [r['name'] for r in resources if r['kind'] in ('output', 'inout')]
    values = [r['name'] for r in resources if r['kind'] == 'value']
    lines = [PRELUDE, 'SHADER_FILE = ' + repr(plan['module'] + '.metal'),
             'SHADER_SHA256 = ' + repr(hashlib.sha256(shader).hexdigest()), '',
             'def run(inputs, shapes, values=None, *, device="mps", omit=()):',
             '    """Inputs are tensors; shapes map names to (W,H,N); values are scalars; outputs are flat tensors.',
             '    An optional input is left out by leaving it out of inputs, an optional output by naming it in omit."""',
             '    values = {} if values is None else values',
             '    if not set(' + repr(required) + ') <= set(inputs) <= set(' + repr(inputs) + ') or set(shapes) != set(' + repr(shapes) + ') or set(values) != set(' + repr(values) + ') or not set(omit) <= set(' + repr(sorted(optional)) + '):',
             '        raise ValueError("Input/shape/value names do not match Pipeline")',
             '    present = {name: name not in omit and (name in inputs or name not in ' + repr(inputs) + ') for name in ' + repr(sorted(optional)) + '}',
             '    device = torch.device(device)',
             '    if device.type != "mps":',
             '        raise ValueError("This generated backend requires MPS")',
             '    if device.index is None:',
             '        device = torch.device("mps:0")',
             '    dims, sizes, buffers = {}, {}, {}']
    for name in shapes:
        lines.append(f'    dims[{name!r}], sizes[{name!r}] = _shape(shapes[{name!r}])')
    for r in resources:
        if r.get('scale', 1) != 1:  # a scaled buffer can be longer than its extent's checked volume
            lines += [f'    if {_count(r)} > 2**31 - 1:', '        raise ValueError("Buffer exceeds INT32_MAX elements")']
    for r in resources:
        if r['kind'] == 'value':
            if is_struct(r['type']):
                layout, size = struct_layout(plan['structs'][r['type'][7:]])
                lines.append(f"    scalar_{r['name']} = _struct(values[{r['name']!r}], {layout!r}, {size})")
            else:
                lines.append(f"    scalar_{r['name']} = _value(values[{r['name']!r}], {r['type']!r})")
    # Validate all inputs before compiling or allocating GPU resources.
    for r in resources:
        if r['kind'] in ('input', 'inout'):  # an inout buffer is updated in a copy, returned as an output
            line = (f"buffers[{r['name']!r}] = _input(inputs[{r['name']!r}], torch.{BUFFER_TYPES[element(r['type'])]}, {_count(r)}, device)"
                    + ('.clone()' if r['kind'] == 'inout' else ''))
            if r.get('optional'):  # left out: a one-element placeholder, and its presence flag 0
                lines.append(f"    if present[{r['name']!r}]:")
                lines.append('        ' + line)
                lines.append('    else:')
                lines.append(f"        buffers[{r['name']!r}] = torch.zeros(1, device=device, dtype=torch.{BUFFER_TYPES[element(r['type'])]})")
            else:
                lines.append('    ' + line)
    lines.append('    lib = _library()')
    for r in resources:
        name = r['name']
        if r['kind'] == 'shape':
            lines.append(f'    buffers[{name!r}] = torch.tensor([*dims[{name!r}], 0], device=device, dtype=torch.int32)')
        elif r['kind'] == 'value':
            if is_struct(r['type']):  # Metal reads it as constant S& from a small buffer of its bytes
                lines.append(f"    buffers[{name!r}] = torch.frombuffer(bytearray(scalar_{name}), dtype=torch.uint8).to(device)")
            else:
                lines.append(f"    buffers[{name!r}] = torch.tensor([scalar_{name}], device=device, dtype=torch.{VALUE_TYPES[r['type']]})")
        elif r['kind'] not in ('input', 'inout'):
            allocate = 'zeros' if r['kind'] == 'zeroed' else 'empty'  # zeroed scratch starts at zero
            count = f"({_count(r)} if present[{name!r}] else 1)" if r.get('optional') else _count(r)
            lines.append(f"    buffers[{name!r}] = torch.{allocate}({count}, device=device, dtype=torch.{BUFFER_TYPES[element(r['type'])]})")
    for r in resources:
        if r.get('optional'):  # its presence flag, bound after the kernel's parameters (qpel_ir presence_binding)
            lines.append(f"    buffers[{('present', r['name'])!r}] = torch.tensor([1 if present[{r['name']!r}] else 0], device=device, dtype=torch.int32)")
    for s in plan['stages']:
        flags = [b['resource'] for b in s['bindings'] if b['resource'] in optional]
        presence = [f"buffers[{('present', f)!r}]" for f in flags]
        if packed_presence(len(s['bindings']), len(flags)):  # one buffer of the flags (qpel_ir packed_presence)
            presence = ['torch.cat([' + ', '.join(presence) + '])']
        kinds = {r['name']: r for r in resources}
        scalars = [b['resource'] for b in s['bindings'] if kinds[b['resource']]['kind'] == 'value'
                   and not is_struct(kinds[b['resource']]['type'])]
        if packed_values(len(s['bindings']), len(scalars), len(flags)):  # one buffer of the values' words (qpel_ir value_table)
            words = '_words([' + ', '.join(f"(scalar_{v}, {kinds[v]['type']!r})" for v in scalars) + '], device)'
            args = ', '.join([f"buffers[{b['resource']!r}]" for b in s['bindings'] if b['resource'] not in scalars]
                             + [words] + presence)
        else:
            args = ', '.join([f"buffers[{b['resource']!r}]" for b in s['bindings']] + presence)
        if s.get('domain') == 'groups':  # whole threadgroups, as CUDA launches whole blocks
            lines.append(f"    _, threads = _groups({tuple(s['groups'])!r}, dims[{s['grid']!r}], {tuple(s['blockShape'])!r})")
            lines.append(f"    getattr(lib, {s['kernel']!r})({args}, threads=threads, group_size={tuple(s['blockShape'])!r})")
        elif s.get('domain') == 'grid3d':
            lines.append(f"    getattr(lib, {s['kernel']!r})({args}, threads=dims[{s['grid']!r}], group_size={tuple(s['blockShape'])!r})")
        else:
            lines.append(f"    getattr(lib, {s['kernel']!r})({args}, threads=sizes[{s['grid']!r}], group_size={s['blockSize']})")
        lines.append('    torch.mps.synchronize()')
    lines.append('    result = {' + ', '.join(f'{n!r}: buffers[{n!r}]' for n in outputs) + '}')
    lines.append('    return {name: value for name, value in result.items() if present.get(name, True)}')
    return '\n'.join(lines) + '\n'
