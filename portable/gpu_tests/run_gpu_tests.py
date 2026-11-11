"""Run every IR fixture on the Apple GPU against an independent CPU oracle.

Each fixture is built fresh with the offline compiler, so this tests the current
compiler output rather than frozen artifacts. Requires macOS with Metal, PyTorch
(MPS) and NumPy; run from an ordinary Terminal (sandboxed shells may hide the GPU).

    python3 portable/gpu_tests/run_gpu_tests.py            # all fixtures
    python3 portable/gpu_tests/run_gpu_tests.py half fma32 # selected fixtures
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


def f32(x): return struct.unpack('<f', struct.pack('<f', x))[0]
def f16(x): return struct.unpack('<e', struct.pack('<e', x))[0]


def load_host(name, work):
    generated = build_fixture(name, work/name)
    spec = importlib.util.spec_from_file_location(f'{name}_metal_host', generated/'metal_host.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    return host, json.loads((generated/'pipeline.json').read_text())


def strided(values, dtype):
    """The same values as a noncontiguous MPS view, to exercise host input handling."""
    expanded = torch.empty(values.numel()*2, dtype=dtype)
    expanded[::2] = values
    return expanded.to('mps')[::2]


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
        x, y = torch.from_numpy(a).to('mps'), torch.from_numpy(b).to('mps')
        if index % 3 == 0:
            x, y = x.transpose(-1, -2), y.transpose(-1, -2)
            a, b = a.swapaxes(-1, -2), b.swapaxes(-1, -2)
        n, h, w = a.shape
        shape = (w*h*n, 1, 1) if flat else (w, h, n)
        actual = host.run({left: x, right: y}, {shape_name: shape})[output]
        assert actual.device.type == 'mps'
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
        result = host.run({'a': torch.from_numpy(a).to('mps'), 'b': torch.from_numpy(b).to('mps')}, {'shape': (w, h, n)})
        delta, bits = signed_delta_oracle(a.reshape(-1), b.reshape(-1))
        np.testing.assert_array_equal(result['delta'].cpu().numpy(), delta)
        np.testing.assert_array_equal(result['bits'].cpu().numpy().view(np.uint32), bits)
    return len(cases)


# --- floating point kernels ---

def run_float32(name, host, plan):
    cases = 0
    for width, height, batch in [(1, 1, 1), (17, 3, 2), (257, 1, 1), (33, 17, 2)]:
        size = width*height*batch
        values = (torch.arange(size, dtype=torch.float32) - size)*0.125
        mask = (torch.arange(size, dtype=torch.int32) % 3).to(torch.uint8)
        for noncontiguous in (False, True):
            pixels = strided(values, torch.float32) if noncontiguous else values.to('mps')
            result = host.run({'pixels': pixels, 'mask': mask.to('mps')}, {'count': (width, height, batch)})['output'].cpu()
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
            pixels = strided(values, dtype) if noncontiguous else values.to('mps')
            result = host.run({'pixels': pixels, 'mask': mask.to('mps')}, {'count': (len(xs), 1, batch)})['output'].cpu()
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
            pixels = strided(values, torch.float32) if noncontiguous else values.to('mps')
            actual = host.run({'pixels': pixels, 'mask': mask.to('mps')}, {'count': shape})['output'].cpu()
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
        actual = host.run({'pixels': strided(torch.tensor(xs), torch.float32), 'mask': mask.to('mps')}, {'count': (n, 1, 1)})['output'].cpu()
        torch.testing.assert_close(actual, expected(xs, mask.tolist(), default), rtol=0, atol=0)
        cases += 1
    # Direct dispatch with other group sizes against the same shader.
    for width in (1, 3, 4, 31, 64, 128, 256):
        for n in (4, width + 3):
            xs = ([2**26, 2.0, -2**26, 2.0] + [(i - 5)*0.25 for i in range(n)])[:n]
            mask = torch.ones(n, dtype=torch.uint8)
            output = torch.empty(n, device='mps', dtype=torch.float32)
            dims = torch.tensor([n, 1, 1, 0], device='mps', dtype=torch.int32)
            getattr(host._library(), kernel)(torch.tensor(xs, device='mps', dtype=torch.float32), mask.to('mps'), output, dims,
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
        result = host.run({'mask': torch.from_numpy(mask).to('mps')}, {'shape': (w, h, n)})
        labels, areas = components_oracle(mask, w, h, n)
        np.testing.assert_array_equal(result['labels'].cpu().numpy().view(np.uint32), labels)
        np.testing.assert_array_equal(result['output'].cpu().numpy().view(np.uint32), areas)
    return len(cases)


TESTS = {**{name: run_u8_pair for name in U8_PAIRS}, 'float32': run_float32, 'fma32': run_fma32,
         'half': run_half, 'reduce': run_reduce, 'workgroup': run_workgroup, 'components': run_components,
         'signed_delta': run_signed_delta}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('fixtures', nargs='*', help='Fixture names (default: all)')
    args = parser.parse_args(argv)
    names = args.fixtures or sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
    missing = [n for n in names if n not in TESTS]
    if missing:
        sys.exit('No GPU test for: ' + ', '.join(missing))
    if not torch.backends.mps.is_available():
        sys.exit('Apple GPU unavailable; run from an ordinary macOS Terminal')
    print(f'torch {torch.__version__}, device mps')
    failed = []
    with tempfile.TemporaryDirectory(prefix='qpel-gpu-') as temp:
        for name in names:
            try:
                host, plan = load_host(name, Path(temp))
                print(f'PASS {name}: {TESTS[name](name, host, plan)} cases')
            except Exception:
                failed.append(name)
                print(f'FAIL {name}')
                traceback.print_exc()
    print(f'{len(names) - len(failed)}/{len(names)} fixtures passed on the Apple GPU')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
