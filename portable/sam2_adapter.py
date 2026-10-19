"""SAM 2 tensor convention mapped to the qpel-generated Metal host API."""
import torch
from generated import metal_host


def connected_components(mask):
    if mask.device.type != 'mps':
        raise ValueError('The generated Metal adapter requires an MPS tensor')
    if mask.ndim != 4 or mask.shape[1] != 1:
        raise ValueError('Expected [N, 1, H, W]')
    if mask.dtype not in (torch.bool, torch.uint8):
        raise TypeError('Expected bool or uint8 mask')
    n, _, h, w = mask.shape
    result = metal_host.run({'mask': mask.to(dtype=torch.uint8)},
                           {'shape': (w, h, n)}, device=mask.device)
    return result['labels'].view(mask.shape), result['output'].view(mask.shape)
