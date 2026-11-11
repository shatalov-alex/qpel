"""Generate a synchronous CuPy/CUDA host from the same checked Pipeline as Metal."""
import hashlib
from generate_metal_host import BUFFER_TYPES, validate_resources
from qpel_types import NUMPY_DTYPES, VALUE_TERMS, element, is_struct, normalize_plan, struct_layout
NUMPY_VALUES = {t: NUMPY_DTYPES[t] for t in VALUE_TERMS}
CUPY_BUFFERS = dict(BUFFER_TYPES, bf16='uint16')  # CuPy has no bfloat16: bf16 buffers hold its bits

PRELUDE = '''# Generated from qpel Pipeline. Edit the model or generator, not this file.
from functools import lru_cache
import hashlib
from pathlib import Path
import cupy as cp
import numpy as np

# By-value QpelShape: four uint32 fields, including explicit padding.
SHAPE_DTYPE = np.dtype({'names': ['x', 'y', 'z', 'padding'],
                       'formats': ['uint32'] * 4,
                       'offsets': [0, 4, 8, 12], 'itemsize': 16})

@lru_cache(maxsize=None)
def _library(device):
    source = Path(__file__).with_name(SHADER_FILE).read_bytes()
    if hashlib.sha256(source).hexdigest() != SHADER_SHA256:
        raise RuntimeError("Shader/host mismatch: regenerate the complete package")
    with cp.cuda.Device(device):
        return cp.RawModule(code=source.decode(), options=('--std=c++17',), backend='nvrtc')

def _shape(value):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("Shape must be (width, height, batch)")
    if any(type(v) is not int or v <= 0 for v in value):
        raise ValueError("Shape dimensions must be positive integers")
    w, h, n = value
    if w * h * n > 2**31 - 1:
        raise ValueError("Shape exceeds INT32_MAX elements")
    return np.array((w, h, n, 0), dtype=SHAPE_DTYPE)[()], w * h * n

def _grid3d(shape, block):
    """Blocks covering a (W, H, N) shape; CUDA limits grid y and z to 65535 blocks."""
    grid = tuple((int(shape[axis]) + b - 1) // b for axis, b in zip('xyz', block))
    if grid[1] > 65535 or grid[2] > 65535:
        raise ValueError("Shape is too tall or deep for one CUDA launch")
    return grid

def _groups(expressions, dims, block):
    """Group counts of a Groups stage (expressions of W, H, N, checked by qpel) and the threads they hold."""
    w, h, n = (int(v) for v in dims)
    counts = tuple(eval(e.replace('/', '//'), {'__builtins__': {}}, {'W': w, 'H': h, 'N': n}) for e in expressions)
    if any(c < 1 for c in counts):
        raise ValueError("Group counts must be positive: " + repr(counts))
    return counts, tuple(c * b for c, b in zip(counts, block))

def _struct(value, layout, size):
    """A struct argument: {field: value}, packed in C's layout (checked like scalar arguments)."""
    import struct as _pack
    if not isinstance(value, dict) or set(value) != {f for f, _, _ in layout}:
        raise ValueError("Struct argument needs exactly the fields " + ", ".join(f for f, _, _ in layout))
    data = bytearray(size)
    for field, kind, offset in layout:
        number = value[field] if kind == 'bf16' else _value(value[field], kind) if kind in ('f32', 'f16') else value[field]
        if kind not in ('f32', 'f16', 'bf16'):
            _value(number, kind)  # range check; packed with its own type below
        _pack.pack_into('<' + _PACK[kind], data, offset, number)
    return bytes(data)

_PACK = {'u8': 'B', 'i8': 'b', 'u16': 'H', 'i16': 'h', 'f16': 'e', 'bf16': 'H', 'u32': 'I', 'i32': 'i', 'f32': 'f', 'u64': 'Q', 'i64': 'q'}

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
    return value

'''


def generate(plan, shader):
    plan = normalize_plan(plan)
    validate_resources(plan)
    resources = plan['resources']
    inputs = [r['name'] for r in resources if r['kind'] == 'input']
    shapes = [r['name'] for r in resources if r['kind'] == 'shape']
    outputs = [r['name'] for r in resources if r['kind'] == 'output']
    values = [r['name'] for r in resources if r['kind'] == 'value']
    lines = [PRELUDE, 'SHADER_FILE = ' + repr(plan['module'] + '.cu'),
             'SHADER_SHA256 = ' + repr(hashlib.sha256(shader).hexdigest()), '',
             'def run(inputs, shapes, values=None, *, device=0):',
             '    """CuPy arrays in/out; shapes are (W,H,N); values are scalars; each stage completes before the next."""',
             '    values = {} if values is None else values',
             '    if type(device) is not int or device < 0:',
             '        raise ValueError("CUDA device must be a nonnegative integer")',
             f'    if set(inputs) != set({inputs!r}) or set(shapes) != set({shapes!r}) or set(values) != set({values!r}):',
             '        raise ValueError("Input/shape/value names do not match Pipeline")',
             '    buffers, sizes = {}, {}']
    for name in shapes:
        lines.append(f'    buffers[{name!r}], sizes[{name!r}] = _shape(shapes[{name!r}])')
    for r in resources:
        if r['kind'] == 'value':
            if is_struct(r['type']):  # passed by value: a NumPy void scalar holding the struct's bytes
                layout, size = struct_layout(plan['structs'][r['type'][7:]])
                lines.append(f"    buffers[{r['name']!r}] = np.frombuffer(_struct(values[{r['name']!r}], {layout!r}, {size}), dtype=np.dtype((np.void, {size})))[0]")
            else:
                lines.append(f"    buffers[{r['name']!r}] = np.{NUMPY_VALUES[r['type']]}(_value(values[{r['name']!r}], {r['type']!r}))")
    for r in resources:
        if r['kind'] == 'input':
            name = r['name']
            lines += [f'    value = inputs[{name!r}]',
                      f"    if not isinstance(value, cp.ndarray) or value.device.id != device or value.dtype != cp.{CUPY_BUFFERS[element(r['type'])]} or value.size != sizes[{r['extent']!r}]:",
                      '        raise ValueError("Input device, dtype or element count does not match Pipeline")']
    lines += ['    with cp.cuda.Device(device):', '        stream = cp.cuda.get_current_stream()',
              '        lib = _library(device)']
    for r in resources:
        name = r['name']
        if r['kind'] == 'input':
            lines.append(f'        buffers[{name!r}] = cp.ascontiguousarray(inputs[{name!r}]).reshape(-1)')
        elif r['kind'] not in ('shape', 'value'):
            allocate = 'zeros' if r['kind'] == 'zeroed' else 'empty'  # zeroed scratch starts at zero
            lines.append(f"        buffers[{name!r}] = cp.{allocate}(sizes[{r['extent']!r}], dtype=cp.{CUPY_BUFFERS[element(r['type'])]})")
    for s in plan['stages']:
        args = ', '.join(f"buffers[{b['resource']!r}]" for b in s['bindings'])
        block = s['blockSize']
        if s.get('domain') == 'groups':
            bx, by, bz = s['blockShape']
            lines += [f"        grid, _ = _groups({tuple(s['groups'])!r}, tuple(int(buffers[{s['grid']!r}][a]) for a in 'xyz'), {(bx, by, bz)!r})",
                      "        if grid[1] > 65535 or grid[2] > 65535:",
                      "            raise ValueError('Too many groups in y or z for one CUDA launch')",
                      f"        lib.get_function({s['kernel']!r})(grid, {(bx, by, bz)!r}, ({args},), stream=stream)",
                      '        stream.synchronize()']
        elif s.get('domain') == 'grid3d':
            bx, by, bz = s['blockShape']
            lines += [f"        grid = _grid3d(buffers[{s['grid']!r}], {(bx, by, bz)!r})",
                      f"        lib.get_function({s['kernel']!r})(grid, {(bx, by, bz)!r}, ({args},), stream=stream)",
                      '        stream.synchronize()']
        else:
            lines += [f"        lib.get_function({s['kernel']!r})(((sizes[{s['grid']!r}] + {block - 1}) // {block},), ({block},), ({args},), stream=stream)",
                      '        stream.synchronize()']
    lines.append('    return {' + ', '.join(f'{n!r}: buffers[{n!r}]' for n in outputs) + '}')
    return '\n'.join(lines) + '\n'
