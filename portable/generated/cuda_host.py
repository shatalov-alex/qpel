# Generated from qpel Pipeline. Edit the model or generator, not this file.
from functools import lru_cache
import hashlib
from pathlib import Path
import cupy as cp
import numpy as np

# By-value QpelShape: four uint32 fields, the fourth the element count (x * y * z, at most INT32_MAX).
SHAPE_DTYPE = np.dtype({'names': ['x', 'y', 'z', 'volume'],
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
    return np.array((w, h, n, w * h * n), dtype=SHAPE_DTYPE)[()], w * h * n

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


SHADER_FILE = 'components.cu'
SHADER_SHA256 = '9656fcad74bb2720a5a73458ccf6328a565f7109b6acd284c48393f67cb6fa40'

def run(inputs, shapes, values=None, *, device=0, omit=()):
    """CuPy arrays in/out; shapes are (W,H,N); values are scalars; each stage completes before the next.
    An optional input is left out by leaving it out of inputs, an optional output by naming it in omit:
    the kernel receives a null pointer."""
    values = {} if values is None else values
    if type(device) is not int or device < 0:
        raise ValueError("CUDA device must be a nonnegative integer")
    if not set(['mask']) <= set(inputs) <= set(['mask']) or set(shapes) != set(['shape']) or set(values) != set([]) or not set(omit) <= set([]):
        raise ValueError("Input/shape/value names do not match Pipeline")
    present = {name: name not in omit and (name in inputs or name not in ['mask']) for name in []}
    buffers, sizes = {}, {}
    buffers['shape'], sizes['shape'] = _shape(shapes['shape'])
    value = inputs['mask']
    if not isinstance(value, cp.ndarray) or value.device.id != device or value.dtype != cp.uint8 or value.size != sizes['shape']:
        raise ValueError("Input device, dtype or element count does not match Pipeline")
    with cp.cuda.Device(device):
        stream = cp.cuda.get_current_stream()
        lib = _library(device)
        buffers['mask'] = cp.ascontiguousarray(inputs['mask']).reshape(-1)
        buffers['parent'] = cp.empty(sizes['shape'], dtype=cp.int32)
        buffers['counts'] = cp.empty(sizes['shape'], dtype=cp.int32)
        buffers['labels'] = cp.empty(sizes['shape'], dtype=cp.int32)
        buffers['output'] = cp.empty(sizes['shape'], dtype=cp.int32)
        lib.get_function('initialize')(((sizes['shape'] + 127) // 128,), (128,), (buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'],), stream=stream)
        stream.synchronize()
        lib.get_function('merge')(((sizes['shape'] + 127) // 128,), (128,), (buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'],), stream=stream)
        stream.synchronize()
        lib.get_function('label_count')(((sizes['shape'] + 127) // 128,), (128,), (buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'], buffers['labels'],), stream=stream)
        stream.synchronize()
        lib.get_function('areas')(((sizes['shape'] + 127) // 128,), (128,), (buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'], buffers['labels'], buffers['output'],), stream=stream)
        stream.synchronize()
    result = {'labels': buffers['labels'], 'output': buffers['output']}
    return {name: value for name, value in result.items() if present.get(name, True)}
