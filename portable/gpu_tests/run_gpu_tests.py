"""Run every IR fixture on a GPU against an independent CPU oracle.

Each fixture is built fresh with the offline compiler, so this tests the current
compiler output rather than frozen artifacts. By default the generated Metal host
runs on the Apple GPU: macOS with Metal, PyTorch (MPS) and NumPy; run from an ordinary
Terminal (sandboxed shells may hide the GPU). With --device cuda the generated CuPy
host runs the same tests on an NVIDIA GPU (PyTorch with CUDA, CuPy, NumPy).

    python3 portable/gpu_tests/run_gpu_tests.py            # all fixtures
    python3 portable/gpu_tests/run_gpu_tests.py half fma32 # selected fixtures
    python3 portable/gpu_tests/run_gpu_tests.py --device cuda --skip-corpus
"""
import os
os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '0'  # A CPU fallback would hide GPU failures.

import argparse
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import traceback

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_builds import FIXTURES, build_fixture
from qpel_types import element, is_buffer, normalize_plan

DEVICE = 'mps'  # set by main(): 'mps' runs metal_host.py, 'cuda' runs cuda_host.py


def f32(x): return struct.unpack('<f', struct.pack('<f', x))[0]
def f16(x): return struct.unpack('<e', struct.pack('<e', x))[0]


def load_host(name, work):
    generated = build_fixture(name, work/name)
    kind = 'metal_host' if DEVICE == 'mps' else 'cuda_host'
    spec = importlib.util.spec_from_file_location(f'{name}_{kind}', generated/f'{kind}.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    plan = json.loads((generated/'pipeline.json').read_text())
    return (host if DEVICE == 'mps' else CupyHost(host, plan)), plan


class CupyHost:
    """The generated CuPy host behind the Metal host's interface: torch tensors in and out,
    with the same storage types (u16/u32/u64 as the signed type of their width, bf16 as
    torch.bfloat16). Tensors cross as zero-copy views, so strided inputs stay strided."""

    def __init__(self, host, plan):
        import cupy
        self.host, self.cp = host, cupy
        self.types = {r['name']: element(r['type']) for r in normalize_plan(plan)['resources']
                      if r['kind'] in ('input', 'output') and is_buffer(r['type'])}

    def _library(self):
        return self.host._library(0)

    def run(self, inputs, shapes, values=None):
        cp = self.cp
        arrays = {}
        for name, tensor in inputs.items():
            if tensor.dtype == torch.bfloat16:
                tensor = tensor.view(torch.int16)
            array = cp.asarray(tensor)
            wanted = cp.dtype(self.host_dtype(name))
            arrays[name] = array.view(wanted) if array.dtype.itemsize == wanted.itemsize else array
        results = self.host.run(arrays, shapes, values)
        out = {}
        for name, array in results.items():
            if self.types.get(name) == 'bf16':
                out[name] = torch.from_dlpack(array.view(cp.int16)).view(torch.bfloat16)
            else:
                out[name] = torch.from_dlpack(array)
        return out

    def host_dtype(self, name):
        from generate_cuda_host import CUPY_BUFFERS
        return CUPY_BUFFERS[self.types[name]]


def strided(values, dtype):
    """The same values as a noncontiguous MPS view, to exercise host input handling."""
    expanded = torch.empty(values.numel()*2, dtype=dtype)
    expanded[::2] = values
    return expanded.to(DEVICE)[::2]


# --- uint8 pair kernels: all 65536 pairs, boundaries, random batches, transposed inputs ---

def u8_pair_cases():
    rng = np.random.default_rng(20260925)
    v = np.arange(256, dtype=np.uint8)
    cases = [(np.broadcast_to(v[:, None], (256, 256))[None].copy(), np.broadcast_to(v[None, :], (256, 256))[None].copy())]
    for a, b in [(127, 128), (128, 128), (255, 0), (0, 255)]:
        cases.append((np.array([[[a]]], np.uint8), np.array([[[b]]], np.uint8)))
    for _ in range(129):
        n, h, w = (int(x) for x in (rng.integers(1, 5), rng.integers(1, 36), rng.integers(1, 66)))
        cases.append((rng.integers(0, 256, (n, h, w), dtype=np.uint8), rng.integers(0, 256, (n, h, w), dtype=np.uint8)))
    return cases


def masks_oracle(a, b):
    expected = np.full(a.shape, 2147483648, dtype=np.uint32)
    for bit in range(8):
        av = (a.astype(np.uint32) >> bit) & 1
        bv = (b.astype(np.uint32) >> bit) & 1
        enabled = (av != 0) | (((bv != 0) & bool((170 >> bit) & 1)) != bool((85 >> bit) & 1))
        expected |= enabled.astype(np.uint32) << bit
    return expected


# fixture: (input names, shape resource, output, shape is flattened count, oracle)
U8_PAIRS = {
    'threshold': (('intensity', 'cutoff'), 'dimensions', 'binary', False, lambda a, b: a >= b),
    'import': (('intensity', 'cutoff'), 'dimensions', 'binary', False, lambda a, b: a >= b),
    'classify': (('pixels', 'limits'), 'extent', 'classes', False, lambda a, b: np.where(a < b, 7, 3)),
    'masks': (('left', 'right'), 'extent', 'result', False, masks_oracle),
    'count': (('pixels', 'mask'), 'count', 'output', True, lambda a, b: np.where(b != 0, a, 0)),
    'cli': (('pixels', 'mask'), 'count', 'output', True, lambda a, b: np.where(b != 0, a, 0)),
}


def run_u8_pair(name, host, plan):
    (left, right), shape_name, output, flat, oracle = U8_PAIRS[name]
    cases = u8_pair_cases()
    for index, (a, b) in enumerate(cases):
        x, y = torch.from_numpy(a).to(DEVICE), torch.from_numpy(b).to(DEVICE)
        if index % 3 == 0:
            x, y = x.transpose(-1, -2), y.transpose(-1, -2)
            a, b = a.swapaxes(-1, -2), b.swapaxes(-1, -2)
        n, h, w = a.shape
        shape = (w*h*n, 1, 1) if flat else (w, h, n)
        actual = host.run({left: x, right: y}, {shape_name: shape})[output]
        assert actual.device.type == DEVICE
        # u32 buffers come back as int32 tensors; compare their bit patterns.
        np.testing.assert_array_equal(actual.cpu().numpy().view(np.uint32).reshape(a.shape),
                                      oracle(a, b).astype(np.uint32))
    return len(cases)


# --- IR version 2: signed arithmetic, if/else and a loop with continue/break ---

def signed_delta_oracle(a, b):
    def div(x, y): return np.sign(x)*(np.abs(x)//y)  # C truncates toward zero; y > 0 here
    def rem(x, y): return x - div(x, y)*y
    d = a.astype(np.int64) - b.astype(np.int64)
    delta = np.where(d < 0, div(d, 3) + rem(d, 5)*7, np.where(d > 100, (rem(d, 7) - 3)*4, (~d) >> 1))
    v = a.astype(np.uint32)
    ones = sum(((v >> k) & 1) for k in range(8))
    bits = (np.minimum(ones, 3) << 4) | (~v & 15)
    return delta.astype(np.int32), bits.astype(np.uint32)


def run_signed_delta(name, host, plan):
    cases = u8_pair_cases()
    for a, b in cases:
        n, h, w = a.shape
        result = host.run({'a': torch.from_numpy(a).to(DEVICE), 'b': torch.from_numpy(b).to(DEVICE)}, {'shape': (w, h, n)})
        delta, bits = signed_delta_oracle(a.reshape(-1), b.reshape(-1))
        np.testing.assert_array_equal(result['delta'].cpu().numpy(), delta)
        np.testing.assert_array_equal(result['bits'].cpu().numpy().view(np.uint32), bits)
    return len(cases)


# --- IR version 2: scalar kernel arguments ---

def run_scale_offset(name, host, plan):
    cases = 0
    for n in (1, 63, 64, 65, 1000):
        x = (np.arange(n, dtype=np.float32) - 17)*np.float32(0.375)
        # Results stay normal: subnormal float32 handling is not pinned (FLOAT32.md).
        for scale, offset in [(0.5, 3), (-1.25, -7), (3.0e38, 2**30), (1e-30, 2**31 - 1), (0, -2**31)]:
            result = host.run({'x': torch.from_numpy(x).to(DEVICE)}, {'n': (n, 1, 1)}, {'scale': scale, 'offset': offset})
            np.testing.assert_array_equal(result['y'].cpu().numpy(), x*np.float32(scale))
            z = (np.arange(n, dtype=np.int64)*offset - 7) & 0xffffffff
            np.testing.assert_array_equal(result['z'].cpu().numpy().view(np.uint32), z.astype(np.uint32))
            cases += 1
    for bad in ({'scale': 1.0, 'offset': 2**31}, {'scale': 'x', 'offset': 0}, {'scale': 1.0}):
        try:
            host.run({'x': torch.zeros(4, device=DEVICE)}, {'n': (4, 1, 1)}, bad)
        except ValueError:
            cases += 1
        else:
            raise AssertionError(f'{bad} was accepted')
    return cases


# --- IR version 2: a 2D/3D kernel ---

def box_sum_oracle(image):
    n, h, w = image.shape
    padded = np.zeros((n, h + 2, w + 2), np.uint32)
    padded[:, 1:-1, 1:-1] = image
    return sum(padded[:, 1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1))


def run_box_sum(name, host, plan):
    rng = np.random.default_rng(20261111)
    shapes = [(1, 1, 1), (1, 1, 17), (1, 17, 1), (16, 16, 1), (17, 15, 3), (33, 1, 2), (1, 40, 2), (100, 37, 4)]
    cases = 0
    for w, h, n in shapes:
        for image in (rng.integers(0, 256, (n, h, w), dtype=np.uint8), np.full((n, h, w), 255, np.uint8)):
            result = host.run({'image': torch.from_numpy(image).to(DEVICE)}, {'shape': (w, h, n)})['sums']
            np.testing.assert_array_equal(result.cpu().numpy().view(np.uint32).reshape(n, h, w), box_sum_oracle(image))
            cases += 1
    return cases


# --- floating point kernels ---

def run_float32(name, host, plan):
    cases = 0
    for width, height, batch in [(1, 1, 1), (17, 3, 2), (257, 1, 1), (33, 17, 2)]:
        size = width*height*batch
        values = (torch.arange(size, dtype=torch.float32) - size)*0.125
        mask = (torch.arange(size, dtype=torch.int32) % 3).to(torch.uint8)
        for noncontiguous in (False, True):
            pixels = strided(values, torch.float32) if noncontiguous else values.to(DEVICE)
            result = host.run({'pixels': pixels, 'mask': mask.to(DEVICE)}, {'count': (width, height, batch)})['output'].cpu()
            torch.testing.assert_close(result, torch.where(mask != 0, values*0.5, torch.zeros_like(values)), rtol=0, atol=0)
            cases += 1
    return cases


def run_masked_elementwise(host, dtype, xs, ys):
    cases = 0
    for batch in (1, 2):
        values = torch.tensor(xs*batch, dtype=dtype)
        mask = (torch.arange(values.numel()) % 3 != 2).to(torch.uint8)
        expected = torch.tensor(ys*batch, dtype=dtype)
        expected[mask == 0] = 0
        for noncontiguous in (False, True):
            pixels = strided(values, dtype) if noncontiguous else values.to(DEVICE)
            result = host.run({'pixels': pixels, 'mask': mask.to(DEVICE)}, {'count': (len(xs), 1, batch)})['output'].cpu()
            assert result.dtype == dtype
            torch.testing.assert_close(result, expected, rtol=0, atol=0)
            cases += 1
    return cases


def run_fma32(name, host, plan):
    # Fused multiply-add rounds once: (1+2^-23)(1-2^-23) - 1 = -2^-46, not 0.
    xs = [1 + 2**-23, 1.0, 0.0, -1.0] + [i/8 for i in range(-256, 257)]
    ys = [f32(x*(1 - 2**-23) - 1) for x in xs]
    assert ys[0] == -2**-46 and f32(f32(xs[0]*(1 - 2**-23)) - 1) == 0
    return run_masked_elementwise(host, torch.float32, xs, ys)


def run_half(name, host, plan):
    # float16 storage, float32 arithmetic, ties-to-even narrowing on store.
    xs = [1.0, 1.0009765625, -1.0, -1.0009765625, 0.0, 65504.0, -65504.0] + [i/8 for i in range(-128, 129)]
    ys = [f16(f32(x + 2**-11)) for x in xs]
    assert ys[0] == 1.0 and ys[1] == 1.001953125
    return run_masked_elementwise(host, torch.float16, xs, ys)


def run_reduce(name, host, plan):
    def ordered(values):
        total = 0.0
        for v in values: total = f32(total + v)
        return total
    base = [16777216.0, 1.0, -16777216.0, 1.0] + [i/8 for i in range(-128, 129)]
    cases = 0
    for xs, shape in [([2.0], (1, 1, 1)), (base, (len(base), 1, 1)), (base*2, (len(base), 1, 2))]:
        values = torch.tensor(xs, dtype=torch.float32)
        mask = (torch.arange(len(xs)) % 3 != 2).to(torch.uint8)
        expected = torch.tensor([ordered(xs[i:i+4]) if mask[i] else 0.0 for i in range(len(xs))], dtype=torch.float32)
        for noncontiguous in (False, True):
            pixels = strided(values, torch.float32) if noncontiguous else values.to(DEVICE)
            actual = host.run({'pixels': pixels, 'mask': mask.to(DEVICE)}, {'count': shape})['output'].cpu()
            torch.testing.assert_close(actual, expected, rtol=0, atol=0)
            if len(xs) > 1: assert actual[0].item() == 1.0  # Summation order is fixed.
            cases += 1
    return cases


def run_workgroup(name, host, plan):
    def tree(values):
        values, stride = list(values), 1
        while stride < len(values):
            for i in range(0, len(values), stride*2):
                if i + stride < len(values): values[i] = f32(values[i] + values[i + stride])
            stride *= 2
        return values[0]

    def expected(xs, mask, width):
        out = []
        for start in range(0, len(xs), width):
            vals = [f32(x*0.5) if mask[start + i] else 0.0 for i, x in enumerate(xs[start:start + width])]
            count = len(vals)
            vals.extend([0.0]*(width - count))
            out.extend([tree(vals)]*count)
        return torch.tensor(out, dtype=torch.float32)

    default, kernel = plan['stages'][0]['blockSize'], plan['stages'][0]['kernel']
    cases = 0
    # Generated allocation/dispatch path, strided input.
    for n in (1, 17, default + 3, default*2 + 1):
        xs = [(i - 9)*0.25 for i in range(n)]
        mask = (torch.arange(n) % 3 != 2).to(torch.uint8)
        actual = host.run({'pixels': strided(torch.tensor(xs), torch.float32), 'mask': mask.to(DEVICE)}, {'count': (n, 1, 1)})['output'].cpu()
        torch.testing.assert_close(actual, expected(xs, mask.tolist(), default), rtol=0, atol=0)
        cases += 1
    if DEVICE != 'mps':
        return cases  # CUDA's group size is fixed at compile time; the dispatch path covers it
    # Direct dispatch with other group sizes against the same shader (Metal).
    for width in (1, 3, 4, 31, 64, 128, 256):
        for n in (4, width + 3):
            xs = ([2**26, 2.0, -2**26, 2.0] + [(i - 5)*0.25 for i in range(n)])[:n]
            mask = torch.ones(n, dtype=torch.uint8)
            output = torch.empty(n, device=DEVICE, dtype=torch.float32)
            dims = torch.tensor([n, 1, 1, 0], device=DEVICE, dtype=torch.int32)
            getattr(host._library(), kernel)(torch.tensor(xs, device=DEVICE, dtype=torch.float32), mask.to(DEVICE), output, dims,
                                             threads=n, group_size=width)
            torch.mps.synchronize()
            torch.testing.assert_close(output.cpu(), expected(xs, mask.tolist(), width), rtol=0, atol=0)
            if n == 4 and width == 4: assert output[0].item() == 0.0  # A left fold would give 1.
            cases += 1
    return cases


# --- SAM 2 connected components (8-connectivity union-find) ---

def components_oracle(mask, w, h, n):
    """Label = 1 + plane-local index of the component's first pixel; area per pixel."""
    labels = np.zeros(mask.size, np.uint32)
    areas = np.zeros(mask.size, np.uint32)
    plane = w*h
    for b in range(n):
        for j in range(plane):
            start = b*plane + j
            if not mask[start] or labels[start]: continue
            queue = [start]
            labels[start] = j + 1
            k = 0
            while k < len(queue):
                local = queue[k] - b*plane
                x, y = local % w, local // w
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            nxt = b*plane + ny*w + nx
                            if mask[nxt] and not labels[nxt]:
                                labels[nxt] = j + 1
                                queue.append(nxt)
                k += 1
            areas[queue] = len(queue)
    return labels, areas


def components_cases():
    def make(w, h, n, fill=0): return (w, h, n, np.full(w*h*n, fill, np.uint8))
    cases = [make(8, 8, 1), make(8, 8, 1, 1)]
    diagonal = make(8, 8, 1); diagonal[3][::9] = 1; cases.append(diagonal)
    islands = make(9, 9, 1); islands[3].reshape(9, 9)[::2, ::2] = 1; cases.append(islands)
    cases += [make(7, 5, 3, 1), make(3, 5, 2, 255), make(13, 11, 1, 1), make(1, 1, 1, 1)]
    rng = np.random.default_rng(20260924)
    for k in range(125):
        w, h, n = int(rng.integers(1, 36)), int(rng.integers(1, 30)), int(rng.integers(1, 5))
        density = (k*13) % 101
        values = rng.integers(1, 256, w*h*n).astype(np.uint8)
        cases.append((w, h, n, np.where(rng.integers(0, 100, w*h*n) < density, values, 0).astype(np.uint8)))
    return cases


def run_components(name, host, plan):
    cases = components_cases()
    for w, h, n, mask in cases:
        result = host.run({'mask': torch.from_numpy(mask).to(DEVICE)}, {'shape': (w, h, n)})
        labels, areas = components_oracle(mask, w, h, n)
        np.testing.assert_array_equal(result['labels'].cpu().numpy().view(np.uint32), labels)
        np.testing.assert_array_equal(result['output'].cpu().numpy().view(np.uint32), areas)
    return len(cases)



# --- an ordered float sum written as a plain loop (cuda_examples/window_sum.cu) ---

def run_window_sum(name, host, plan):
    rng = np.random.default_rng(20261112)
    cases = 0
    for w, h, n in [(1, 1, 1), (3, 1, 1), (4, 1, 1), (17, 3, 2), (257, 1, 1), (33, 17, 2)]:
        size = w*h*n
        pixels = (rng.standard_normal(size)*np.exp2(rng.integers(-8, 9, size))).astype(np.float32)
        mask = rng.integers(0, 3, size).astype(np.uint8)
        expected = np.zeros(size, np.float32)
        for i in range(size):
            total = np.float32(0)
            for k in range(min(4, size - i)):
                total = np.float32(total + pixels[i + k])  # in order, rounded at every step
            expected[i] = total if mask[i] else np.float32(0)
        result = host.run({'pixels': torch.from_numpy(pixels).to(DEVICE), 'mask': torch.from_numpy(mask).to(DEVICE)},
                          {'count': (w, h, n)})['output'].cpu().numpy()
        np.testing.assert_array_equal(result.view(np.uint32), expected.view(np.uint32))
        cases += 1
    return cases


# --- IR version 3 integer widths, bf16 and saturating conversions (oracle next to the fixture) ---

def run_int_widths(name, host, plan):
    spec = importlib.util.spec_from_file_location('int_widths_oracle', FIXTURES/'int_widths'/'oracle.py')
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    torch_types = {np.uint16: torch.int16, np.uint64: torch.int64}  # MPS lacks unsigned 16/64-bit tensors
    cases = 0
    for shape, inputs, k in oracle.cases():
        tensors = {}
        for n, values in inputs.items():
            if n == 'h':
                tensors[n] = torch.from_numpy(values.view(np.int16).copy()).view(torch.bfloat16).to(DEVICE)
            elif values.dtype.type in torch_types:
                tensors[n] = torch.from_numpy(values.view(values.dtype.str.replace('u', 'i')).copy()).to(DEVICE)
            else:
                tensors[n] = torch.from_numpy(values.copy()).to(DEVICE)
        result = host.run(tensors, {'count': shape}, {'k': k})
        actual = {}
        for n, t in result.items():
            t = t.cpu()
            actual[n] = (t.view(torch.int16) if t.dtype == torch.bfloat16 else t).numpy().view(oracle.OUTPUTS[n])
        error = oracle.compare(oracle.expected(inputs, k), actual)
        if error:
            raise AssertionError(f'{shape}, k={k}: {error}')
        cases += 1
    return cases


# --- Groups kernels: a grid-stride loop, launched in whole groups ---

def run_grid_stride(name, host, plan):
    rng = np.random.default_rng(20261113)
    cases = 0
    for w, h, n in [(1, 1, 1), (63, 1, 1), (64, 1, 1), (65, 1, 1), (1000, 3, 2), (4097, 1, 1)]:
        size = w*h*n
        for count in sorted({0, 1, size // 2, size}):
            x = (rng.standard_normal(size)*np.exp2(rng.integers(-8, 9, size))).astype(np.float32)
            scale, bias = float(np.float32(rng.uniform(-4, 4))), float(np.float32(rng.uniform(-4, 4)))
            out = host.run({'x': torch.from_numpy(x).to(DEVICE)}, {'size': (w, h, n)},
                           {'scale': scale, 'bias': bias, 'n': count})['y'].cpu().numpy()
            expected = (np.float32(scale)*x[:count]).astype(np.float32) + np.float32(bias)
            np.testing.assert_array_equal(out[:count].view(np.uint32), expected.astype(np.float32).view(np.uint32))
            cases += 1
    return cases


# --- math functions: exact ones bit for bit, bounded ones within the documented ulp bound ---

def run_math_ops(name, host, plan):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qpel_types import MATH_ULPS
    from differential.matrix import ulp_distance
    rng = np.random.default_rng(20261114)
    cases = 0
    for size in (1, 7, 64, 1000, 4099):
        v = (rng.standard_normal(size)*np.exp2(rng.integers(-6, 12, size))).astype(np.float32)
        v[:min(size, 6)] = np.float32([0.5, -0.5, 2.5, -1e-3, 1e6, -0.0])[:min(size, 6)]
        out = host.run({'x': torch.from_numpy(v).to(DEVICE)}, {'n': (size, 1, 1)})
        f = np.float32
        x = v.astype(np.float64)
        rounded = np.where(np.abs(x - np.trunc(x)) >= 0.5, np.trunc(x) + np.sign(x), np.trunc(x))  # half away from zero
        with np.errstate(all='ignore'):
            inner = (np.floor(v) + np.ceil(v)).astype(f) - np.trunc(v)
            clamped = np.maximum(np.minimum(inner.astype(f), f(100)), f(-100))
            exact = (clamped + np.copysign(rounded, -x).astype(f)).astype(f) + np.sqrt(np.abs(v)).astype(f)
            terms = [np.exp((v*f(0.01)).astype(np.float64)),
                     np.log((np.abs(v) + f(1)).astype(np.float64)),
                     np.sin(x)*np.cos(x), np.tanh(x), np.exp2(-np.abs(x)), np.log2((np.abs(v) + f(2)).astype(np.float64))]
        np.testing.assert_array_equal(out['exact'].cpu().numpy().view(np.uint32), exact.astype(f).view(np.uint32))
        bounded = sum(terms).astype(f)
        far = ulp_distance(out['bounded'].cpu().numpy(), bounded) > 4*MATH_ULPS  # six bounded terms add up
        assert not far.any(), f'{size}: bounded math off by more than {4*MATH_ULPS} ulp at {np.flatnonzero(far)[:5]}'
        cases += 1
    return cases


# --- corpus kernels (llama.cpp operations): the oracle is the original source itself ---

def corpus_fixtures():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from frontends.roundtrip import corpus_programs
    return {program['fixture'] for _, program in corpus_programs()}


def run_corpus_fixture(name):
    """qpel's Metal on the Apple GPU against the unmodified original on the CPU simulator."""
    from differential.matrix import rows
    cases = 0
    for row, label, error in rows([name], gpu=True):
        if row.endswith('-> metal'):
            if error:
                raise AssertionError(f'{label}: {error}')
            cases += 1
    if not cases:
        raise AssertionError('no Metal row ran')
    return cases


# --- SAM 2 connected components, imported from the unmodified CUDA original ---

def sam2_oracle(mask, w, h):
    """Labels and areas as SAM 2's get_connected_componnets defines them, for one image.

    Components are 8-connected. Every foreground pixel of a 2x2 block is in one
    component; a component's label is 1 + the smallest index of a block top-left
    pixel (row and column even) among its pixels. Background is 0, as is its area."""
    from collections import deque
    labels = np.zeros(w*h, np.int32)
    areas = np.zeros(w*h, np.int32)
    seen = np.zeros(w*h, bool)
    for start in range(w*h):
        if not mask[start] or seen[start]:
            continue
        seen[start] = True
        queue, members = deque([start]), []
        while queue:
            p = queue.popleft()
            members.append(p)
            y, x = divmod(p, w)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny*w + nx] and not seen[ny*w + nx]:
                        seen[ny*w + nx] = True
                        queue.append(ny*w + nx)
        label = 1 + min((p // w) // 2 * 2 * w + (p % w) // 2 * 2 for p in members)
        labels[members] = label
        areas[members] = len(members)
    return labels, areas


def sam2_cases():
    """(w, h, mask) cases; SAM 2 requires even width and height."""
    cases = [(2, 2, np.zeros(4, np.uint8)), (2, 2, np.ones(4, np.uint8)), (16, 16, np.ones(256, np.uint8))]
    checker = np.indices((10, 12)).sum(axis=0) % 2  # diagonal neighbours only
    cases.append((12, 10, checker.astype(np.uint8).ravel()))
    rows = np.zeros((8, 34), np.uint8); rows[::3] = 1  # long thin components across blocks
    cases.append((34, 8, rows.ravel()))
    rng = np.random.default_rng(20261111)
    for k in range(60):
        w, h = 2*int(rng.integers(1, 40)), 2*int(rng.integers(1, 30))
        density = (k*17) % 101
        values = rng.integers(1, 256, w*h).astype(np.uint8)
        cases.append((w, h, np.where(rng.integers(0, 100, w*h) < density, values, 0).astype(np.uint8)))
    return cases


def run_sam2_cc(name, host, plan):
    cases = sam2_cases()
    for w, h, mask in cases:
        result = host.run({'img': torch.from_numpy(mask).to(DEVICE)}, {'shape': (w, h, 1)})
        labels, areas = sam2_oracle(mask, w, h)
        np.testing.assert_array_equal(result['label'].cpu().numpy(), labels, err_msg=f'labels {w}x{h}')
        np.testing.assert_array_equal(result['count_final'].cpu().numpy(), areas, err_msg=f'areas {w}x{h}')
    return len(cases)


# --- IR version 4: thread-private arrays with data-dependent indices ---

def sort_bytes_oracle(x):
    v = np.sort(np.stack([x & 255, (x >> 8) & 255, (x >> 16) & 255], axis=1), axis=1).astype(np.uint32)
    h = np.zeros((x.size, 4), np.uint32)
    h[:, 0] = x >> 30
    for k in range(3):
        np.add.at(h, (np.arange(x.size), v[:, k] % 4), 1)
    return v[:, 0] | (v[:, 1] << 8) | (v[:, 2] << 16), h[:, 0] | (h[:, 1] << 8) | (h[:, 2] << 16) | (h[:, 3] << 24)


def run_sort_bytes(name, host, plan):
    rng = np.random.default_rng(20261112)
    cases = 0
    for n in (1, 63, 64, 65, 1000, 4097):
        x = rng.integers(0, 2**32, n, dtype=np.uint64).astype(np.uint32)
        x[:min(n, 4)] = [0, 0xffffffff, 0x00030201, 0xc0010101][:min(n, 4)]
        result = host.run({'x': torch.from_numpy(x.view(np.int32)).to(DEVICE)}, {'n': (n, 1, 1)})
        sorted_, counts = sort_bytes_oracle(x)
        np.testing.assert_array_equal(result['sorted'].cpu().numpy().view(np.uint32), sorted_)
        np.testing.assert_array_equal(result['counts'].cpu().numpy().view(np.uint32), counts)
        cases += 1
    return cases


# --- IR version 6 atomics: max/and/or/xor/exchange, float32 and 64-bit ---

def test_module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1]/f'tests/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_atomics(name, host, plan):
    """Every result is independent of the order of the atomics (tests/test_atomics_v6.py's oracle)."""
    from differential import cases as case_sets
    from differential.simulate import DTYPES
    module = test_module('test_atomics_v6')
    signed = {np.dtype(np.uint16): np.int16, np.dtype(np.uint32): np.int32, np.dtype(np.uint64): np.int64}
    cases = case_sets.generic(plan, 6, shapes=[(1, 1, 1), (40, 1, 1), (17, 3, 2), (300, 1, 1)])
    for case in cases:
        inputs = {}
        for r in plan['resources']:
            if r['kind'] in ('input', 'inout'):
                array = np.ascontiguousarray(case.inputs[r['name']], DTYPES[r['type']]).reshape(-1)
                inputs[r['name']] = torch.from_numpy(array.view(signed.get(array.dtype, array.dtype)).copy()).to(DEVICE)
        result = host.run(inputs, case.shapes, case.values)
        for out, want in module.oracle(case).items():
            got = result[out].cpu().numpy()
            np.testing.assert_array_equal(got.view(want.dtype) if got.dtype != want.dtype else got, want, err_msg=f'{out} {case.label}')
    return len(cases)


def run_bitcast(name, host, plan):
    """f32 bits as u32 and i32 and back (tests/test_bitcast.py's oracle)."""
    from differential import cases as case_sets
    module = test_module('test_bitcast')
    cases = case_sets.generic(plan, 6, shapes=[(1, 1, 1), (40, 1, 1), (300, 1, 1)])
    for case in cases:
        result = host.run({'x': torch.from_numpy(case.inputs['x'].copy()).to(DEVICE)}, case.shapes, case.values)
        y, z = module.oracle(case.inputs['x'])
        np.testing.assert_array_equal(result['y'].cpu().numpy().view(np.uint32), y, err_msg=case.label)
        np.testing.assert_array_equal(result['z'].cpu().numpy(), z, err_msg=case.label)
    return len(cases)


TESTS = {**{name: run_u8_pair for name in U8_PAIRS}, 'float32': run_float32, 'fma32': run_fma32,
         'half': run_half, 'reduce': run_reduce, 'workgroup': run_workgroup, 'components': run_components,
         'signed_delta': run_signed_delta, 'scale_offset': run_scale_offset, 'box_sum': run_box_sum,
         'sam2_cc': run_sam2_cc, 'window_sum': run_window_sum, 'int_widths': run_int_widths, 'grid_stride': run_grid_stride, 'math_ops': run_math_ops, 'sort_bytes': run_sort_bytes, 'atomics': run_atomics, 'bitcast': run_bitcast}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('fixtures', nargs='*', help='Fixture names (default: all)')
    parser.add_argument('--skip-corpus', action='store_true',
                        help='leave out corpus programs, whose Metal rows the behaviour rows already run')
    parser.add_argument('--device', choices=('mps', 'cuda'), default='mps',
                        help='mps: the Metal host on the Apple GPU (default); cuda: the CuPy host on NVIDIA')
    args = parser.parse_args(argv)
    global DEVICE
    DEVICE = args.device
    if DEVICE == 'cuda':
        args.skip_corpus = True  # corpus rows compare against Metal and the CPU simulator; see differential/
    names = args.fixtures or sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
    if args.skip_corpus:
        names = [n for n in names if n not in corpus_fixtures()]
    corpus = corpus_fixtures()
    missing = [n for n in names if n not in TESTS and n not in corpus]
    if missing:
        sys.exit('No GPU test for: ' + ', '.join(missing))
    if DEVICE == 'mps' and not torch.backends.mps.is_available():
        sys.exit('Apple GPU unavailable; run from an ordinary macOS Terminal')
    if DEVICE == 'cuda' and not torch.cuda.is_available():
        sys.exit('no CUDA device visible to PyTorch')
    gpu = 'Apple GPU' if DEVICE == 'mps' else torch.cuda.get_device_name(0)
    print(f'torch {torch.__version__}, device {DEVICE} ({gpu})')
    failed = []
    with tempfile.TemporaryDirectory(prefix='qpel-gpu-') as temp:
        for name in names:
            try:
                if name in corpus:
                    print(f'PASS {name}: {run_corpus_fixture(name)} cases')
                    continue
                host, plan = load_host(name, Path(temp))
                print(f'PASS {name}: {TESTS[name](name, host, plan)} cases')
            except Exception:
                failed.append(name)
                print(f'FAIL {name}')
                traceback.print_exc()
    print(f'{len(names) - len(failed)}/{len(names)} fixtures passed on {gpu}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
