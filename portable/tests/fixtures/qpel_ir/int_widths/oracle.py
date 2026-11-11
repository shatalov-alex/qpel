"""Test cases and an independent oracle for the int_widths fixture (see build.py).

The oracle follows NUMERICS.md with Python integers: wrapping two's-complement
arithmetic at the operation's width, C's truncating division with LLONG_MIN / -1
wrapping, shift counts modulo the width, saturating float -> integer conversion
(NaN -> 0) and round-to-nearest-even float conversions. Subnormal floats are
avoided: Apple GPUs flush them to zero.
"""
import math

import numpy as np

INPUTS = {'a': np.int8, 'b': np.uint16, 'c': np.int16, 'd': np.int64, 'e': np.uint64, 'f': np.float32, 'h': np.uint16}
OUTPUTS = {'o_i64': np.int64, 'o_u64': np.uint64, 'o_i32': np.int32, 'o_u8': np.uint8, 'o_i16': np.int16,
           'o_bf16': np.uint16, 'o_f32': np.float32}


def wrap(value, bits, signed):
    value %= 2**bits
    return value - 2**bits if signed and value >= 2**(bits - 1) else value


def saturate(x, bits, signed):
    if math.isnan(x):
        return 0
    low, high = (-2**(bits - 1), 2**(bits - 1) - 1) if signed else (0, 2**bits - 1)
    if x < low:
        return low
    if x >= high + 1:
        return high
    return int(x)  # truncates toward zero


def bf16_bits(x):
    """float32 -> bfloat16 bits, round to nearest even (NaN stays NaN)."""
    w = int(np.float32(x).view(np.uint32))
    if (w & 0x7fffffff) > 0x7f800000:
        return (w >> 16) | 0x40
    return ((w + 0x7fff + ((w >> 16) & 1)) >> 16) & 0xffff


def bf16_value(bits):
    return np.uint32(int(bits) << 16).view(np.float32)


def element(a, b, c, d, e, f, h, k):
    t = d >> (b & 63)                       # arithmetic shift of an i64
    m = min(t, -5)
    num = wrap(wrap(d*k, 64, True) + a, 64, True)
    div = c | 1
    if div == -1:
        q = wrap(-num, 64, True)
    else:
        q = abs(num)//abs(div)*(1 if (num < 0) == (div < 0) else -1)
    o_i64 = wrap(q + m, 64, True)
    o_u64 = wrap((wrap(e << (b & 63), 64, False) ^ (e >> 7)) - b, 64, False)
    o_i32 = max(saturate(float(f), 32, True), wrap(d, 32, True))
    o_u8 = saturate(float(np.float32(np.float32(f)*np.float32(4))), 8, False)
    o_i16 = wrap(-c + 32768, 16, True)
    o_bf16 = bf16_bits(np.float32(bf16_value(h))*np.float32(f))
    o_f32 = np.float32(np.float32(np.int64(d)) + np.float32(np.uint64(e)))
    return o_i64, o_u64, o_i32, o_u8, o_i16, o_bf16, o_f32


def expected(inputs, k):
    with np.errstate(all='ignore'):  # overflow to inf and NaN products are part of the cases
        return _expected(inputs, k)


def _expected(inputs, k):
    count = len(inputs['a'])
    out = {name: np.zeros(count, dtype) for name, dtype in OUTPUTS.items()}
    for i in range(count):
        row = element(*(int(inputs[n][i]) if n not in ('f',) else float(inputs[n][i]) for n in 'abcdefh'), k)
        for name, value in zip(OUTPUTS, row):
            out[name][i] = value
    return out


def cases(seed=20261112):
    """[(shape, inputs, k)]: edge values first, then random batches."""
    rng = np.random.default_rng(seed)
    int64_min, int64_max = -2**63, 2**63 - 1
    edge_f = [float('nan'), float('inf'), -float('inf'), 3e9, -3e9, 1e20, -1e20, -0.7, 255.9, 63.9, 64.0,
              -128.5, 2147483520.0, -2147483648.0, 0.0, -0.0]
    n = len(edge_f)
    edge = {'a': np.array([0, -128, 127, -1] * 4, np.int8)[:n],
            'b': np.array([0, 1, 63, 64, 65, 127, 65535, 7] * 2, np.uint16)[:n],
            'c': np.array([-1, -2, 0, 1, -32768, 32767, -1, 5] * 2, np.int16)[:n],
            'd': np.array([int64_min, int64_max, -1, 0, int64_min, 12345678901234, -5, 2**53 + 1] * 2, np.int64)[:n],
            'e': np.array([0, 2**64 - 1, 2**63, 1, 2**53 + 1, 123, 2**32, 7] * 2, np.uint64)[:n],
            'f': np.array(edge_f, np.float32),
            'h': np.array([bf16_bits(x) for x in [1.0, -2.5, 0.0, 3.0e38, 1e-30, 7.0, -1.0, 0.5] * 2], np.uint16)[:n]}
    out = [((n, 1, 1), edge, 1), ((n, 1, 1), edge, -1)]
    for shape in [(1, 1, 1), (65, 1, 1), (17, 3, 2)]:
        size = shape[0]*shape[1]*shape[2]
        inputs = {'a': rng.integers(-128, 128, size).astype(np.int8),
                  'b': rng.integers(0, 2**16, size).astype(np.uint16),
                  'c': rng.integers(-2**15, 2**15, size).astype(np.int16),
                  'd': rng.integers(int64_min, int64_max, size, dtype=np.int64, endpoint=True),
                  'e': rng.integers(0, 2**64 - 1, size, dtype=np.uint64, endpoint=True),
                  'f': (rng.standard_normal(size)*np.exp2(rng.integers(-4, 40, size))).astype(np.float32),
                  'h': np.array([bf16_bits(x) for x in rng.standard_normal(size)*1e3], np.uint16)}
        out.append((shape, inputs, int(rng.integers(int64_min, int64_max, dtype=np.int64, endpoint=True))))
    return out


def compare(expected_out, actual):
    """None, or the first difference. Floats compare bit for bit; any NaN matches any NaN."""
    for name, dtype in OUTPUTS.items():
        want, got = np.asarray(expected_out[name]), np.asarray(actual[name]).astype(dtype, copy=False)
        if name == 'o_bf16':
            nan_w, nan_g = (want & 0x7fff) > 0x7f80, (got & 0x7fff) > 0x7f80
            bad = np.flatnonzero((want != got) & ~(nan_w & nan_g))
        elif name == 'o_f32':
            bad = np.flatnonzero((want.view(np.uint32) != got.view(np.uint32)) & ~(np.isnan(want) & np.isnan(got)))
        else:
            bad = np.flatnonzero(want != got)
        if bad.size:
            i = bad[0]
            return f'{name}[{i}]: expected {want[i]!r}, got {got[i]!r} ({bad.size} differ)'
    return None
