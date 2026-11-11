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

def _input(value, dtype, count, device):
    if value.device != device or value.dtype != dtype or value.numel() != count:
        raise ValueError("Input device, dtype or element count does not match Pipeline")
    return value.contiguous().view(-1)


SHADER_FILE = 'components.metal'
SHADER_SHA256 = 'b2ceb9ed82720108f22f10bf58792b5c2888be4c64c9c64a677ce01966ffb056'

def run(inputs, shapes, *, device="mps"):
    """Inputs are tensors; shapes map names to (W,H,N); outputs are flat tensors."""
    if set(inputs) != set(['mask']) or set(shapes) != set(['shape']):
        raise ValueError("Input/shape names do not match Pipeline")
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
