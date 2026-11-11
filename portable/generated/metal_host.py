# Generated from qpel Pipeline. Edit the model or generator, not this file.
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


SHADER_FILE = 'components.metal'
SHADER_SHA256 = 'fd09576611dd63c921a0ded9da9f8f757997b76aee7f494d110b7e63b1c46513'

def run(inputs, shapes, values=None, *, device="mps"):
    """Inputs are tensors; shapes map names to (W,H,N); values are scalars; outputs are flat tensors."""
    values = {} if values is None else values
    if set(inputs) != set(['mask']) or set(shapes) != set(['shape']) or set(values) != set([]):
        raise ValueError("Input/shape/value names do not match Pipeline")
    device = torch.device(device)
    if device.type != "mps":
        raise ValueError("This generated backend requires MPS")
    if device.index is None:
        device = torch.device("mps:0")
    dims, sizes, buffers = {}, {}, {}
    dims['shape'], sizes['shape'] = _shape(shapes['shape'])
    buffers['mask'] = _input(inputs['mask'], torch.uint8, sizes['shape'], device)
    lib = _library()
    buffers['parent'] = torch.empty(sizes['shape'], device=device, dtype=torch.int32)
    buffers['counts'] = torch.empty(sizes['shape'], device=device, dtype=torch.int32)
    buffers['shape'] = torch.tensor([*dims['shape'], 0], device=device, dtype=torch.int32)
    buffers['labels'] = torch.empty(sizes['shape'], device=device, dtype=torch.int32)
    buffers['output'] = torch.empty(sizes['shape'], device=device, dtype=torch.int32)
    getattr(lib, 'initialize')(buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'], threads=sizes['shape'], group_size=128)
    torch.mps.synchronize()
    getattr(lib, 'merge')(buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'], threads=sizes['shape'], group_size=128)
    torch.mps.synchronize()
    getattr(lib, 'label_count')(buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'], buffers['labels'], threads=sizes['shape'], group_size=128)
    torch.mps.synchronize()
    getattr(lib, 'areas')(buffers['mask'], buffers['parent'], buffers['counts'], buffers['shape'], buffers['labels'], buffers['output'], threads=sizes['shape'], group_size=128)
    torch.mps.synchronize()
    return {'labels': buffers['labels'], 'output': buffers['output']}
