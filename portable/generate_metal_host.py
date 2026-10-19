"""Generate a standalone eager PyTorch/Metal host module from a checked Pipeline."""
import hashlib
import keyword

BUFFER_TYPES = {'ReadOnlyF16BufferType': 'float16', 'F16BufferType': 'float16', 'ReadOnlyF32BufferType': 'float32', 'F32BufferType': 'float32', 'ReadOnlyU8BufferType': 'uint8', 'U32BufferType': 'int32', 'AtomicU32BufferType': 'int32'}


def validate_resources(plan):
    resources = {r['name']: r for r in plan['resources']}
    for r in resources.values():
        kind, typ = r['kind'], r['type']
        if kind == 'shape':
            if typ != 'Shape3DType' or r.get('extent') is not None:
                raise ValueError('Shape requires Shape3DType and no extent')
        elif kind in ('input', 'output', 'scratch'):
            if typ not in BUFFER_TYPES:
                raise ValueError('Unsupported buffer type: ' + typ)
            extent = resources.get(r.get('extent'))
            if extent is None or extent['kind'] != 'shape':
                raise ValueError('Buffer requires a shape extent')
            if kind != 'input' and typ in {'ReadOnlyU8BufferType', 'ReadOnlyF32BufferType', 'ReadOnlyF16BufferType'}:
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

def _input(value, dtype, count, device):
    if value.device != device or value.dtype != dtype or value.numel() != count:
        raise ValueError("Input device, dtype or element count does not match Pipeline")
    return value.contiguous().view(-1)

'''


def generate(plan, shader):
    validate_resources(plan)
    resources = plan['resources']
    inputs = [r['name'] for r in resources if r['kind'] == 'input']
    shapes = [r['name'] for r in resources if r['kind'] == 'shape']
    outputs = [r['name'] for r in resources if r['kind'] == 'output']
    lines = [PRELUDE, 'SHADER_FILE = ' + repr(plan['module'] + '.metal'),
             'SHADER_SHA256 = ' + repr(hashlib.sha256(shader).hexdigest()), '',
             'def run(inputs, shapes, *, device="mps"):',
             '    """Inputs are tensors; shapes map names to (W,H,N); outputs are flat tensors."""',
             '    if set(inputs) != set(' + repr(inputs) + ') or set(shapes) != set(' + repr(shapes) + '):',
             '        raise ValueError("Input/shape names do not match Pipeline")',
             '    device = torch.device(device)',
             '    if device.type != "mps":',
             '        raise ValueError("This generated backend requires MPS")',
             '    if device.index is None:',
             '        device = torch.device("mps:0")',
             '    dims, sizes, buffers = {}, {}, {}']
    for name in shapes:
        lines.append(f'    dims[{name!r}], sizes[{name!r}] = _shape(shapes[{name!r}])')
    # Validate all inputs before compiling or allocating GPU resources.
    for r in resources:
        if r['kind'] == 'input':
            lines.append(f"    buffers[{r['name']!r}] = _input(inputs[{r['name']!r}], torch.{BUFFER_TYPES[r['type']]}, sizes[{r['extent']!r}], device)")
    lines.append('    lib = _library()')
    for r in resources:
        name = r['name']
        if r['kind'] == 'shape':
            lines.append(f'    buffers[{name!r}] = torch.tensor([*dims[{name!r}], 0], device=device, dtype=torch.int32)')
        elif r['kind'] != 'input':
            lines.append(f"    buffers[{name!r}] = torch.empty(sizes[{r['extent']!r}], device=device, dtype=torch.{BUFFER_TYPES[r['type']]})")
    for s in plan['stages']:
        args = ', '.join(f"buffers[{b['resource']!r}]" for b in s['bindings'])
        lines.append(f"    getattr(lib, {s['kernel']!r})({args}, threads=sizes[{s['grid']!r}], group_size={s['blockSize']})")
        lines.append('    torch.mps.synchronize()')
    lines.append('    return {' + ', '.join(f'{n!r}: buffers[{n!r}]' for n in outputs) + '}')
    return '\n'.join(lines) + '\n'
