"""How the behaviour rows and qpel verify compare an original's outputs with another run's:
bit for bit, or within an ulp bound, with subnormals flushed and zero signs optional (NUMERICS.md)."""
import numpy as np

from .simulate import DTYPES


def ulp_distance(a, b):
    """Distance in units in the last place of the arrays' float type (ordered bit patterns);
    NaN to NaN is 0."""
    a, b = np.asarray(a), np.asarray(b)
    signed = np.int32 if a.dtype == np.float32 else np.int16
    mask = 0x7fffffff if a.dtype == np.float32 else 0x7fff

    def ordered(x):
        bits = x.view(signed).astype(np.int64)
        return np.where(bits < 0, -(bits & mask), bits)
    distance = np.abs(ordered(a) - ordered(b))
    return np.where(np.isnan(a) & np.isnan(b), 0, distance)


def flushed(x):
    """Subnormal floats as zeros of the same sign: Apple GPUs flush them (NUMERICS.md)."""
    tiny = np.finfo(x.dtype).tiny
    return np.where(np.abs(x) < tiny, np.copysign(np.zeros_like(x), x), x).astype(x.dtype)


def compare(expected, actual, names, defined=None, ulps=0, views=None, zero_sign=True):
    """None, or where the first difference is. `defined` limits outputs to their written prefix.
    Float outputs compare with subnormals flushed. With `ulps` > 0 (programs that use bounded
    math functions) a float element also matches when it is within that many ulp, or within
    ulps * epsilon * max(1, |value|), which allows for cancellation after a bounded function."""
    for name in names:
        a, b = np.asarray(expected[name]), np.asarray(actual[name])
        if views and name in views:  # a byte buffer holding values of another type
            dtype = DTYPES[views[name]]
            a, b = a.view(dtype), b.astype(a.dtype, copy=False).view(dtype)
        if defined and name in defined and isinstance(defined[name], str):
            mask = np.asarray(expected[defined[name]]) != 0  # defined where another output is nonzero
            a, b = a[mask], b[mask]
        elif defined and name in defined:
            a, b = a[:defined[name]], b[:defined[name]]
        if a.shape != b.shape:
            return f'{name}: {a.shape} != {b.shape}'
        if a.dtype.kind == 'f':
            fa, fb = flushed(a), flushed(b.astype(a.dtype))
            same = (fa.view(f'u{a.itemsize}') == fb.view(f'u{a.itemsize}')) | (np.isnan(fa) & np.isnan(fb))
            if not zero_sign:  # Apple's round gives +0 for -0.3; qpel keeps C's -0 (NUMERICS.md)
                same |= (fa == 0) & (fb == 0)
            if ulps:
                epsilon = float(np.finfo(a.dtype).eps)
                with np.errstate(all='ignore'):
                    near = np.abs(fa.astype(np.float64) - fb.astype(np.float64)) <= \
                        ulps*epsilon*np.maximum(1.0, np.abs(fa.astype(np.float64)))
                same |= (ulp_distance(fa, fb) <= ulps) | near
            bad = np.flatnonzero(~same)
        else:
            bad = np.flatnonzero(a != b)
        if bad.size:
            i = bad[0]
            return (f'{name}[{i}]: original {a[i].item()!r}, qpel {b[i].item()!r} ({bad.size} differ'
                    + (f' beyond {ulps} ulp' if ulps else '') + ')')
    return None
