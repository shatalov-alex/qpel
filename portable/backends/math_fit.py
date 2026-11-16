"""The polynomials behind qpel's Metal erf, expm1 and log1p (backends/metal.h; IR version 8).

Metal has no erf, expm1 or log1p, so qpel's Metal brings its own:

- erf: |x| < 1: x * (c0 + R(x^2)), c0 = 2/sqrt(pi) rounded to float32 and R fitted to erf(x)/x - c0
  (so c0's rounding error is in R); 1 <= |x| < 3.92: 1 - exp(-x^2) * Q(|x| - m), Q fitted to
  erfc(x) * exp(x^2) on [1, 2] and [2, 3.92]; beyond, +-1 (erf rounds to 1 in float32); NaN stays NaN.
- expm1: |x| < 1: x * P(x), P fitted to expm1(x)/x; beyond, exp(x) - 1 (little cancellation there).
- log1p: |x| < 0.5: x * P(x), P fitted to log1p(x)/x; beyond, log(1 + x).

Coefficients are least-squares fits on Chebyshev nodes in float64, rounded to float32; the
polynomials are evaluated with fma (one rounding per step). This script fits them, reports the
largest error of a float32 emulation against float64, and prints the Metal source that
backends/metal.h holds:

    python3 backends/math_fit.py

The Apple GPU's own errors are measured by `gpu_tests/run_gpu_tests.py math_v8` (2^20 inputs:
erf 1, expm1 3, log1p 3 ulp at most on an M4 Pro, 2026-11-16) and tests/test_math_v8.py.
"""
import math

import numpy as np
from numpy.polynomial import chebyshev

f32 = np.float32


def fma(a, b, c):
    return f32(np.float64(a) * np.float64(b) + np.float64(c))


def horner(coefficients, t):
    r = f32(coefficients[0])
    for c in coefficients[1:]:
        r = fma(r, t, f32(c))
    return r


def fit(fn, lo, hi, degree, var=lambda x: x, relative=True):
    """Coefficients (highest power first, rounded to float32) of a polynomial in var(x) near fn on [lo, hi]."""
    x = (chebyshev.chebpts1(4000) * (hi - lo) + (hi + lo)) / 2
    t, y = var(x), fn(x)
    a = np.vander(t, degree + 1)
    w = 1 / np.abs(y) if relative else np.ones_like(y)
    coefficients, *_ = np.linalg.lstsq(a * w[:, None], y * w, rcond=None)
    return [float(f32(c)) for c in coefficients]


ERF = np.vectorize(math.erf)
ERFC = np.vectorize(math.erfc)
C0 = float(f32(2 / math.sqrt(math.pi)))
ERF_SMALL = fit(lambda x: ERF(x) / x - C0, 1e-6, 1.0, 9, var=lambda x: x * x, relative=False)
ERF_PIECES = [(lo, float(f32((lo + hi) / 2)), fit(lambda x: ERFC(x) * np.exp(x * x), lo, hi, 10,
                                                  var=lambda x, m=(lo + hi) / 2: x - m))
              for lo, hi in ((1.0, 2.0), (2.0, 3.92))]
EXPM1 = fit(lambda x: np.expm1(x) / x, -1, 1, 11)
LOG1P = fit(lambda x: np.log1p(x) / x, -0.5, 0.5, 13)


def erf32(x):
    a = f32(abs(x))
    if a < 1:
        return fma(f32(x), f32(C0), f32(f32(x) * horner(ERF_SMALL, f32(a * a))))
    if a < f32(3.92):
        _, mid, q = ERF_PIECES[0] if a < 2 else ERF_PIECES[1]
        r = f32(1) - f32(f32(np.exp(np.float64(-f32(a * a)))) * horner(q, f32(a - f32(mid))))
        return r if x > 0 else -r
    return f32(math.copysign(1, x)) if not math.isnan(x) else f32(x)


def expm1_32(x):
    return f32(x) * horner(EXPM1, f32(x)) if abs(x) < 1 else f32(f32(np.exp(np.float64(x))) - f32(1))


def log1p32(x):
    return f32(x) * horner(LOG1P, f32(x)) if abs(x) < 0.5 else f32(np.log(np.float64(f32(f32(1) + f32(x)))))


def worst_ulp(impl, reference, xs):
    got = np.array([impl(f32(x)) for x in xs], np.float64)
    want = reference(xs.astype(np.float64))
    ulp = np.spacing(np.abs(want.astype(f32))).astype(np.float64)
    error = np.abs(got - want) / np.maximum(ulp, np.finfo(np.float32).tiny)
    return float(error.max()), float(xs[int(error.argmax())])


def literal(v):
    text = repr(float(v))
    return text + 'f' if ('e' in text or '.' in text) else text + '.0f'


def metal_horner(coefficients, var):
    expr = literal(coefficients[0])
    for c in coefficients[1:]:
        expr = f'metal::fma({expr}, {var}, {literal(c)})'
    return expr


def metal_source():
    (_, m1, q1), (_, m2, q2) = ERF_PIECES
    return f'''// erf, expm1 and log1p, which Metal does not have (IR version 8; NUMERICS.md, Math functions):
// polynomials fitted for these ranges (backends/math_fit.py prints this code), evaluated with fma,
// and Metal's precise exp and log. Apple GPU, 2^20 inputs: erf 1, expm1 3, log1p 3 ulp at most.
inline float qpel_erf(float x) {{
    float a = metal::fabs(x);
    if (a < 1.0f) {{
        float t = a * a;
        float r = x * {metal_horner(ERF_SMALL, "t")};
        return metal::fma(x, {literal(C0)}, r);  // 2/sqrt(pi) split off: x * (c0 + R(x^2))
    }}
    if (a < 3.92f) {{  // erf = 1 - exp(-x^2) * erfcx(x), erfcx fitted on [1, 2] and [2, 3.92]
        float q = a < 2.0f ? {metal_horner(q1, f"(a - {literal(m1)})")}
                           : {metal_horner(q2, f"(a - {literal(m2)})")};
        float r = 1.0f - metal::precise::exp(-(a * a)) * q;
        return metal::copysign(r, x);
    }}
    return metal::isnan(x) ? x : metal::copysign(1.0f, x);  // erf rounds to 1 in float32 from 3.92 on
}}
inline float qpel_expm1(float x) {{
    if (metal::fabs(x) < 1.0f) return x * {metal_horner(EXPM1, "x")};
    return metal::precise::exp(x) - 1.0f;  // (NaN, infinities and |x| >= 1: little cancellation)
}}
inline float qpel_log1p(float x) {{
    if (metal::fabs(x) < 0.5f) return x * {metal_horner(LOG1P, "x")};
    return metal::precise::log(1.0f + x);
}}'''


if __name__ == '__main__':
    rng = np.random.default_rng(1)
    for name, impl, reference, xs in (
            ('erf', erf32, ERF, np.concatenate([rng.uniform(-5, 5, 40000), rng.uniform(-1.2, 1.2, 20000)])),
            ('expm1', expm1_32, np.expm1, np.concatenate([rng.uniform(-3, 3, 30000), rng.uniform(-1e-3, 1e-3, 5000)])),
            ('log1p', log1p32, np.log1p, np.concatenate([rng.uniform(-0.999, 3, 30000), rng.uniform(-1e-3, 1e-3, 5000)]))):
        worst, at = worst_ulp(impl, reference, xs.astype(np.float32))
        print(f'// {name}: float32 emulation within {worst:.2f} ulp (at {at})')
    print(metal_source())
