"""Generate a standalone eager PyTorch/Metal host module from a checked Pipeline."""
import hashlib
import keyword

from qpel_types import TORCH_DTYPES, VALUE_TERMS, access, element, is_buffer, normalize_plan

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
            if typ not in VALUE_TYPES or r.get('extent') is not None:
                raise ValueError('A value requires an integer or f32 type and no extent')
        elif kind in ('input', 'output', 'scratch', 'zeroed'):
            if not is_buffer(typ) or element(typ) not in BUFFER_TYPES:
                raise ValueError('Unsupported buffer type: ' + typ)
            extent = resources.get(r.get('extent'))
            if extent is None or extent['kind'] != 'shape':
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

'''


def generate(plan, shader):
    plan = normalize_plan(plan)
    validate_resources(plan)
    resources = plan['resources']
    inputs = [r['name'] for r in resources if r['kind'] == 'input']
    shapes = [r['name'] for r in resources if r['kind'] == 'shape']
    outputs = [r['name'] for r in resources if r['kind'] == 'output']
    values = [r['name'] for r in resources if r['kind'] == 'value']
    lines = [PRELUDE, 'SHADER_FILE = ' + repr(plan['module'] + '.metal'),
             'SHADER_SHA256 = ' + repr(hashlib.sha256(shader).hexdigest()), '',
             'def run(inputs, shapes, values=None, *, device="mps"):',
             '    """Inputs are tensors; shapes map names to (W,H,N); values are scalars; outputs are flat tensors."""',
             '    values = {} if values is None else values',
             '    if set(inputs) != set(' + repr(inputs) + ') or set(shapes) != set(' + repr(shapes) + ') or set(values) != set(' + repr(values) + '):',
             '        raise ValueError("Input/shape/value names do not match Pipeline")',
             '    device = torch.device(device)',
             '    if device.type != "mps":',
             '        raise ValueError("This generated backend requires MPS")',
             '    if device.index is None:',
             '        device = torch.device("mps:0")',
             '    dims, sizes, buffers = {}, {}, {}']
    for name in shapes:
        lines.append(f'    dims[{name!r}], sizes[{name!r}] = _shape(shapes[{name!r}])')
    for r in resources:
        if r['kind'] == 'value':
            lines.append(f"    scalar_{r['name']} = _value(values[{r['name']!r}], {r['type']!r})")
    # Validate all inputs before compiling or allocating GPU resources.
    for r in resources:
        if r['kind'] == 'input':
            lines.append(f"    buffers[{r['name']!r}] = _input(inputs[{r['name']!r}], torch.{BUFFER_TYPES[element(r['type'])]}, sizes[{r['extent']!r}], device)")
    lines.append('    lib = _library()')
    for r in resources:
        name = r['name']
        if r['kind'] == 'shape':
            lines.append(f'    buffers[{name!r}] = torch.tensor([*dims[{name!r}], 0], device=device, dtype=torch.int32)')
        elif r['kind'] == 'value':
            lines.append(f"    buffers[{name!r}] = torch.tensor([scalar_{name}], device=device, dtype=torch.{VALUE_TYPES[r['type']]})")
        elif r['kind'] != 'input':
            allocate = 'zeros' if r['kind'] == 'zeroed' else 'empty'  # zeroed scratch starts at zero
            lines.append(f"    buffers[{name!r}] = torch.{allocate}(sizes[{r['extent']!r}], device=device, dtype=torch.{BUFFER_TYPES[element(r['type'])]})")
    for s in plan['stages']:
        args = ', '.join(f"buffers[{b['resource']!r}]" for b in s['bindings'])
        if s.get('domain') == 'groups':  # whole threadgroups, as CUDA launches whole blocks
            lines.append(f"    _, threads = _groups({tuple(s['groups'])!r}, dims[{s['grid']!r}], {tuple(s['blockShape'])!r})")
            lines.append(f"    getattr(lib, {s['kernel']!r})({args}, threads=threads, group_size={tuple(s['blockShape'])!r})")
        elif s.get('domain') == 'grid3d':
            lines.append(f"    getattr(lib, {s['kernel']!r})({args}, threads=dims[{s['grid']!r}], group_size={tuple(s['blockShape'])!r})")
        else:
            lines.append(f"    getattr(lib, {s['kernel']!r})({args}, threads=sizes[{s['grid']!r}], group_size={s['blockSize']})")
        lines.append('    torch.mps.synchronize()')
    lines.append('    return {' + ', '.join(f'{n!r}: buffers[{n!r}]' for n in outputs) + '}')
    return '\n'.join(lines) + '\n'
