# Generated from qpel Pipeline. Edit the model or generator, not this file.
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


SHADER_FILE = 'components.cu'
SHADER_SHA256 = '969e32ee221eebf85dec75e40a7dd51473a0fa5854ca1872299ad6eebdde3e98'

def run(inputs, shapes, *, device=0):
    """CuPy arrays in/out; shapes are (W,H,N); each stage completes before the next."""
    if type(device) is not int or device < 0:
        raise ValueError("CUDA device must be a nonnegative integer")
    if set(inputs) != set(['mask']) or set(shapes) != set(['shape']):
        raise ValueError("Input/shape names do not match Pipeline")
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
    return {'labels': buffers['labels'], 'output': buffers['output']}
