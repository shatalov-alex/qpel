"""Behaviour rows of qpel's Triton frontend: Triton kernels against what qpel makes of them.

For every kernel of the Triton corpora (corpus/triton and corpus/liger: frontends/triton/corpora.py), the
TTIR is lowered to qpel (frontends/triton/lower.py), qpel builds CUDA and Metal, and the same
cases run through

    Triton's interpreter (numpy, in the qpel/triton Docker image)   the reference
    qpel's CUDA on the CPU simulator                                row triton -> qpel -> cuda
    qpel's Metal on the Apple GPU                                   row triton -> qpel -> metal
    qpel's CUDA against qpel's Metal                                row qpel cuda = metal

Element-wise float operations compare bit for bit; a bounded math function (exp, log, ...) within
2 * MATH_ULPS. A program with a float sum reduction over n elements is compared within
2 * ceil(log2 n) + 4 more epsilons of max(1, |value|): Triton leaves a reduction's order open,
numpy sums pairwise and qpel in index order (the largest difference measured on these cases
was 3 epsilons, softmax over 128 columns and layer norm over 200). A dot (tl.dot) is compared
the same way, its epsilons taken of max(1, |value|, sum of |a_ik * b_kj|): the size of the
products, not of their sum, bounds its rounding error (largest measured: 1.5 of those; against
the exact product in float64, qpel is within 1.8 and the interpreter within 1.4). qpel's CUDA and Metal run the same order and agree exactly
(bounded math functions apart).

    python3 -m differential.triton                 # every kernel
    python3 -m differential.triton softmax_kernel
"""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from frontends.triton import corpora  # noqa: E402
from frontends.triton.lower import lower  # noqa: E402
from qpel_ir import Lowering  # noqa: E402
from qpel_text import parse_program  # noqa: E402
from qpel_types import MATH_ULPS  # noqa: E402
from .compare import compare  # noqa: E402
from .simulate import Case, Program  # noqa: E402

# Triton's interpreter computes tl.math.fma as x * y + z with two roundings (numpy has no fused
# multiply-add); Triton's NVIDIA code (fma.rn.f32) and qpel's qpel.fma round once. Programs with an
# fma compare with the interpreter within this many more ulp; against Triton's PTX they must agree
# exactly (differential/triton_nvidia.py).
FMA_ULPS = 1  # (in epsilons of the case's scale where it gives one: |x * y| + |z|)
IMAGE = 'qpel/triton:3.8.0'


def kernels():
    return corpora.kernels()


def program(name):
    """(qpel IR, the Lowering) of one corpus kernel."""
    text, lowering = lower(corpora.ttir(name), corpora.metadata(name)['num_warps'], 'triton_' + name)
    return parse_program(text), lowering


# -- cases --

def specials(rng, n):
    x = rng.standard_normal(n).astype(np.float32)
    if n >= 8:
        x[:4] = [np.inf, -np.inf, -0.0, 3.4e38]
        x[4] = np.nan
    return x


def add_cases(rng):
    out = []
    for n in (1, 255, 256, 257, 1000, 4097):
        x, y = specials(rng, n), rng.standard_normal(n).astype(np.float32)
        out.append(Case({'programs': (-(-n // 256), 1, 1), 'x_ptr_size': (n, 1, 1), 'y_ptr_size': (n, 1, 1),
                         'output_ptr_size': (n, 1, 1)},
                        {'x_ptr': x, 'y_ptr': y, 'output_ptr': np.zeros(n, np.float32)}, {'n_elements': n}, f'n={n}'))
    return out


def scale_shift_cases(rng):
    out = []
    for n, scale, shift in ((1, 2.0, 0.5), (127, -1.5, 3.0), (128, 0.1, -0.2), (1000, 1e30, 1e30), (513, 3.0, 0.0)):
        out.append(Case({'programs': (-(-n // 128), 1, 1), 'x_ptr_size': (n, 1, 1), 'out_ptr_size': (n, 1, 1)},
                        {'x_ptr': specials(rng, n), 'out_ptr': np.zeros(n, np.float32)},
                        {'n_elements': n, 'scale': scale, 'shift': shift}, f'n={n} scale={scale} shift={shift}'))
    return out


def softmax_cases(rng):
    out = []
    for rows, cols, pad in ((1, 1, 0), (3, 17, 0), (8, 128, 0), (5, 100, 7), (16, 64, 3)):
        stride = cols + pad
        x = (rng.standard_normal(rows * stride) * 3).astype(np.float32)
        out.append(Case({'programs': (rows, 1, 1), 'output_ptr_size': (rows * stride, 1, 1),
                         'input_ptr_size': (rows * stride, 1, 1)},
                        {'output_ptr': np.zeros(rows * stride, np.float32), 'input_ptr': x},
                        {'input_row_stride': stride, 'output_row_stride': stride, 'n_cols': cols},
                        f'{rows}x{cols} stride {stride}'))
    return out


def copy2d_cases(rng):
    out = []
    for m, n, pad_src, pad_dst in ((1, 1, 0, 0), (16, 32, 0, 0), (17, 33, 2, 1), (40, 70, 0, 5), (5, 100, 3, 0)):
        ss, sd = n + pad_src, m + pad_dst
        out.append(Case({'programs': (-(-m // 16), -(-n // 32), 1), 'src_ptr_size': (m * ss, 1, 1),
                         'dst_ptr_size': (n * sd, 1, 1)},
                        {'src_ptr': specials(rng, m * ss), 'dst_ptr': np.zeros(n * sd, np.float32)},
                        {'M': m, 'N': n, 'stride_src': ss, 'stride_dst': sd}, f'{m}x{n} strides {ss}, {sd}'))
    return out


def layer_norm_cases(rng):
    out = []
    for rows, n, pad in ((1, 1, 0), (2, 63, 0), (4, 64, 0), (3, 65, 2), (5, 200, 0), (2, 129, 5)):
        stride = n + pad
        x = (rng.standard_normal(rows * stride) * 2 + 0.5).astype(np.float32)
        out.append(Case({'programs': (rows, 1, 1), 'X_size': (rows * stride, 1, 1), 'Y_size': (rows * stride, 1, 1),
                         'W_size': (n, 1, 1), 'B_size': (n, 1, 1)},
                        {'X': x, 'Y': np.zeros(rows * stride, np.float32),
                         'W': rng.standard_normal(n).astype(np.float32), 'B': rng.standard_normal(n).astype(np.float32)},
                        {'stride': stride, 'N': n, 'eps': 1e-5}, f'{rows}x{n} stride {stride}'))
    return out


def matmul_cases(rng):
    """C = A @ B with 32 x 32 tiles over K in steps of 16: tiles that end inside M, N and K, a
    transposed B (its strides swapped), and the grouped program order of the Triton tutorial."""
    out = []
    for m, n, k, transposed in ((1, 1, 1, False), (32, 32, 16, False), (33, 17, 40, False), (64, 96, 48, True),
                                (70, 50, 33, False), (129, 40, 7, True)):
        a = rng.standard_normal(m * k).astype(np.float32)
        b = rng.standard_normal(k * n).astype(np.float32)
        sbk, sbn = (1, k) if transposed else (n, 1)  # B as [N, K] in memory, read through its strides
        programs = -(-m // 32) * -(-n // 32)
        bm = b.reshape(n, k).T if transposed else b.reshape(k, n)
        scale = (np.abs(a.reshape(m, k)).astype(np.float64) @ np.abs(bm).astype(np.float64)).ravel()
        out.append(Case({'programs': (programs, 1, 1), 'a_ptr_size': (m * k, 1, 1), 'b_ptr_size': (k * n, 1, 1),
                         'c_ptr_size': (m * n, 1, 1)},
                        {'a_ptr': a, 'b_ptr': b, 'c_ptr': np.zeros(m * n, np.float32)},
                        {'M': m, 'N': n, 'K': k, 'stride_am': k, 'stride_ak': 1, 'stride_bk': sbk, 'stride_bn': sbn,
                         'stride_cm': n, 'stride_cn': 1}, f'{m}x{k} @ {k}x{n}{" (B transposed)" if transposed else ""}'))
        out[-1].scales = {'c_ptr': scale}  # sum of |a_ik * b_kj|: what a dot's rounding error is relative to
    return out


def row_stats_cases(rng):
    out = []
    for m, n, pad in ((1, 1, 0), (8, 64, 0), (13, 20, 3), (40, 63, 1), (3, 64, 0)):
        stride = n + pad
        x = (rng.standard_normal(m * stride) * 4).astype(np.float32)
        out.append(Case({'programs': (-(-m // 8), 1, 1), 'x_ptr_size': (m * stride, 1, 1), 'max_ptr_size': (m, 1, 1),
                         'mean_ptr_size': (m, 1, 1)},
                        {'x_ptr': x, 'max_ptr': np.zeros(m, np.float32), 'mean_ptr': np.zeros(m, np.float32)},
                        {'M': m, 'N': n, 'stride': stride}, f'{m}x{n} stride {stride}'))
    return out


def argmax_cases(rng):
    """Per-row maximum and its index: ties (values rounded to integers, the lower index wins), rows
    shorter than the block (masked with -inf), all-equal rows."""
    out = []
    for rows, cols, pad in ((1, 1, 0), (6, 64, 0), (5, 33, 4), (9, 10, 0)):
        stride = cols + pad
        x = np.round(rng.standard_normal(rows * stride) * 2).astype(np.float32)
        if rows > 2:
            x[stride:2 * stride] = 1.5  # one row of equal values: index 0
        out.append(Case({'programs': (rows, 1, 1), 'x_ptr_size': (rows * stride, 1, 1), 'index_ptr_size': (rows, 1, 1),
                         'value_ptr_size': (rows, 1, 1)},
                        {'x_ptr': x, 'index_ptr': np.zeros(rows, np.int32), 'value_ptr': np.zeros(rows, np.float32)},
                        {'n_cols': cols, 'stride': stride}, f'{rows}x{cols} stride {stride}'))
    return out


def cumsum_cases(rng):
    out = []
    for n in (1, 127, 128, 300, 1024):
        out.append(Case({'programs': (-(-n // 128), 1, 1), 'x_ptr_size': (n, 1, 1), 'out_ptr_size': (n, 1, 1)},
                        {'x_ptr': rng.integers(-1000, 1000, n, dtype=np.int32), 'out_ptr': np.zeros(n, np.int32)},
                        {'n': n}, f'n={n} (per block of 128)'))
    return out


def clip_rows_cases(rng):
    out = []
    for rows, cols, pad, limit in ((1, 1, 0, 0.5), (8, 64, 0, 6.0), (5, 40, 3, 4.0), (3, 17, 0, 100.0)):
        stride = cols + pad
        x = (rng.standard_normal(rows * stride) * rng.uniform(0.1, 2, rows * stride)).astype(np.float32)
        out.append(Case({'programs': (rows, 1, 1), 'x_ptr_size': (rows * stride, 1, 1), 'out_ptr_size': (rows * stride, 1, 1)},
                        {'x_ptr': x, 'out_ptr': np.zeros(rows * stride, np.float32)},
                        {'n_cols': cols, 'stride': stride, 'limit': limit}, f'{rows}x{cols} limit {limit}'))
    return out


def histogram_cases(rng):
    out = []
    for n, bins in ((1, 4), (128, 10), (1000, 16), (5000, 7)):
        out.append(Case({'programs': (-(-n // 128), 1, 1), 'x_ptr_size': (n, 1, 1), 'bins_ptr_size': (bins, 1, 1)},
                        {'x_ptr': rng.integers(-3, bins + 3, n, dtype=np.int32), 'bins_ptr': np.zeros(bins, np.int32)},
                        {'n': n, 'n_bins': bins}, f'n={n} into {bins} bins (out of range clamped)'))
    return out


def attention_cases(rng):
    """softmax(Q K^T / sqrt(d)) V per head, float16 inputs and output: contexts shorter than, equal
    to and longer than the 16-key blocks (the last block masked), two heads."""
    out = []
    heads, d = 2, 32
    for n in (1, 16, 40, 100):
        size = heads * n * d
        q, k, v = (rng.standard_normal(size).astype(np.float16) for _ in range(3))
        out.append(Case({'programs': (-(-n // 16), heads, 1), 'Q_size': (size, 1, 1), 'K_size': (size, 1, 1),
                         'V_size': (size, 1, 1), 'Out_size': (size, 1, 1)},
                        {'Q': q, 'K': k, 'V': v, 'Out': np.zeros(size, np.float16)},
                        {'sm_scale': float(1 / np.sqrt(d)), 'N_CTX': n, 'stride_h': n * d, 'stride_m': d},
                        f'{heads} heads x {n} tokens x {d}'))
    return out


def block_ptr_matmul_cases(rng):
    """matmul_cases' shapes through block pointers (one program per output tile, no grouping)."""
    out = []
    for case in matmul_cases(rng):
        v = case.values
        case.shapes['programs'] = (-(-v['M'] // 32), -(-v['N'] // 32), 1)
        out.append(case)
    return out


def transpose_cases(rng):
    out = []
    for m, n in ((1, 1), (32, 32), (33, 70), (100, 17)):
        out.append(Case({'programs': (-(-m // 32), -(-n // 32), 1), 'x_ptr_size': (m * n, 1, 1), 'y_ptr_size': (m * n, 1, 1)},
                        {'x_ptr': specials(rng, m * n), 'y_ptr': np.zeros(m * n, np.float32)}, {'M': m, 'N': n}, f'{m}x{n}'))
    return out


def collatz_cases(rng):
    """Steps of the Collatz map to 1, one program per start value: all end (the map is checked far
    beyond these values), the longest here after a few hundred steps."""
    out = []
    for n in (1, 7, 100):
        x = np.concatenate([[1, 27, 97, 871], rng.integers(1, 100000, max(0, n - 4))])[:n].astype(np.int32)
        out.append(Case({'programs': (n, 1, 1), 'x_ptr_size': (n, 1, 1), 'steps_ptr_size': (n, 1, 1)},
                        {'x_ptr': x, 'steps_ptr': np.zeros(n, np.int32)}, {'n': n}, f'{n} start values'))
    return out


def precise_ops_cases(rng):
    """Edges of the exact operations: NaN in either operand, -0 against +0, infinities, huge values,
    division by zero, values on both sides of the clamp bounds; and random bits for umulhi."""
    out = []
    edges = np.array([np.nan, 1.0, -0.0, 0.0, np.inf, -np.inf, 3.4e38, -2.0, 2.5, -1.5, 0.0, 1e-30], np.float32)
    partners = np.array([1.0, np.nan, 0.0, -0.0, 2.0, np.inf, 3.4e38, 0.0, -0.0, 7.0, -0.0, 3.0], np.float32)
    for n in (len(edges), 128, 1000):
        x, y = (rng.standard_normal(n) * 3).astype(np.float32), (rng.standard_normal(n) * 3).astype(np.float32)
        x[:len(edges)], y[:len(edges)] = edges, partners
        out.append(Case({'programs': (-(-n // 128), 1, 1), 'x_ptr_size': (n, 1, 1), 'y_ptr_size': (n, 1, 1),
                         'out_ptr_size': (n, 1, 1), 'fma_ptr_size': (n, 1, 1), 'bits_ptr_size': (n, 1, 1)},
                        {'x_ptr': x, 'y_ptr': y, 'out_ptr': np.zeros(n, np.float32), 'fma_ptr': np.zeros(n, np.float32),
                         'bits_ptr': np.zeros(n, np.int32)},
                        {'n': n}, f'n={n} with the edges'))
        clamped = np.minimum(np.maximum(x, np.float32(-1.5)), np.float32(2.0)).astype(np.float64)
        with np.errstate(all='ignore'):  # what an unfused x * y + c's extra rounding is relative to
            out[-1].scales = {'fma_ptr': np.abs(x.astype(np.float64) * y) + np.abs(clamped)}
    return out


def gelu_cases(rng):
    out = []
    for n in (1, 128, 1000):
        x = (rng.standard_normal(n) * 3).astype(np.float32)
        out.append(Case({'programs': (-(-n // 128), 1, 1), 'x_ptr_size': (n, 1, 1), 'erf_ptr_size': (n, 1, 1),
                         'tanh_ptr_size': (n, 1, 1)},
                        {'x_ptr': x, 'erf_ptr': np.zeros(n, np.float32), 'tanh_ptr': np.zeros(n, np.float32)},
                        {'n': n}, f'n={n}'))
    return out


def gelu_oracle(case):
    """The GELU kernel's two outputs in float64, rounded to float32: Triton's interpreter has no
    libdevice (libdevice.erf gives None there), so this is the reference for this kernel."""
    import math
    x = case.inputs['x_ptr'].astype(np.float64)
    erf = np.array([math.erf(v * 0.7071067811865476) for v in x])
    inner = 0.7978845608028654 * (x + 0.044715 * x ** 3)
    return {'x_ptr': case.inputs['x_ptr'], 'erf_ptr': (0.5 * x * (1 + erf)).astype(np.float32),
            'tanh_ptr': (0.5 * x * (1 + np.tanh(inner))).astype(np.float32)}


# Kernels Triton's interpreter cannot run: an oracle gives their reference outputs instead.
ORACLES = {'gelu_libdevice_kernel': gelu_oracle}


CASES = {'gelu_libdevice_kernel': gelu_cases, 'precise_ops_kernel': precise_ops_cases, 'block_ptr_matmul_kernel': block_ptr_matmul_cases, 'transpose_kernel': transpose_cases,
         'collatz_kernel': collatz_cases, 'attention_fwd_kernel': attention_cases, 'argmax_kernel': argmax_cases, 'cumsum_kernel': cumsum_cases, 'clip_rows_kernel': clip_rows_cases,
         'histogram_kernel': histogram_cases, 'matmul_kernel': matmul_cases, 'row_stats_kernel': row_stats_cases, 'add_kernel': add_cases, 'scale_shift_kernel': scale_shift_cases, 'softmax_kernel': softmax_cases,
         'copy2d_kernel': copy2d_cases, 'layer_norm_kernel': layer_norm_cases}


def cases(name, seed=20261115):
    from .liger_cases import CASES as LIGER
    return (CASES | LIGER)[name](np.random.default_rng(seed))


# -- the reference --

def docker_ready():
    if not shutil.which('docker'):
        return False
    return subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True).returncode == 0


def references(jobs):
    """{(kernel, case index): {buffer: array}} from Triton's interpreter, for [(kernel, params, cases)],
    in one container (memory and CPUs capped)."""
    with tempfile.TemporaryDirectory(prefix='qpel-triton-') as work:
        work = Path(work)
        folders = {}
        oracle = {(name, i): ORACLES[name](case) for name, _, kernel_cases in jobs if name in ORACLES
                  for i, case in enumerate(kernel_cases)}
        jobs = [job for job in jobs if job[0] not in ORACLES]
        for name, params, kernel_cases in jobs:
            for i, case in enumerate(kernel_cases):
                folder = work/f'{name}__{i:03d}'
                folder.mkdir()
                args = []
                for pname, t in params:
                    if t.is_pointer:
                        np.save(folder/f'{pname}.npy', np.asarray(case.inputs[pname]))
                        args.append({'name': pname, 'buffer': f'{pname}.npy'})
                    else:
                        args.append({'name': pname, 'value': case.values[pname]})
                (folder/'case.json').write_text(json.dumps({'kernel': name, 'grid': list(case.shapes['programs']),
                                                            'args': args}))
                folders[(name, i)] = (folder, [p for p, t in params if t.is_pointer])
        for corpus in {corpora.corpus_of(name) for name, _, _ in jobs}:  # one container per corpus
            root = corpora.CORPORA[corpus]
            names = {name for name, _, _ in jobs if corpora.corpus_of(name) == corpus}
            here = work/corpus
            here.mkdir()
            for (name, i), (folder, _) in folders.items():
                if name in names:
                    folder.rename(here/folder.name)
                    folders[(name, i)] = (here/folder.name, folders[(name, i)][1])
            result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--label', 'qpel=1', '--memory', '4g',
                                     '--cpus', '2', '-e', 'TRITON_INTERPRET=1', '-v', f'{HERE/"frontends/triton"}:/tool:ro',
                                     '-v', f'{root}:/corpus:ro', '-v', f'{here}:/cases', IMAGE, 'python', '/tool/reference.py',
                                     f'/corpus/{corpora.source(next(iter(names)))}', '/corpus/kernels.json', '/cases'],
                                    capture_output=True, text=True, timeout=1800)
            if result.returncode:
                raise RuntimeError(f'Triton interpreter failed:\n{result.stderr[-3000:]}')
        return {key: {b: np.load(folder/f'out_{b}.npy') for b in buffers} for key, (folder, buffers) in folders.items()} | oracle


# -- rows --

def tolerance(prog, lowering):
    """ulps for compare(): bounded math functions, and float sum reductions (their longest axis)."""
    ulps = 2 * MATH_ULPS if lowering.bounded else 0
    if lowering.float_sums:
        ulps += 2 * math.ceil(math.log2(max(2, max(lowering.float_sums)))) + 4
    return ulps


def check(expected, actual, outputs, ulps, case, reduction, fused=False):
    """compare(), and for a program with float reductions, a case may give each output a scale (for a
    dot, the sum of |products|, which bounds its rounding error): an element then also matches
    within `reduction` epsilons of max(1, |value|, scale)."""
    halves = [o for o in outputs if np.asarray(expected[o]).dtype == np.float16]
    if ulps and halves:  # a float32 result within the tolerance can round to either neighbouring float16
        error = compare(expected, actual, halves, None, 1)
        if error:
            return error
        outputs = [o for o in outputs if o not in halves]
    scales = getattr(case, 'scales', {})
    error = compare(expected, actual, [o for o in outputs if o not in scales], None, ulps)
    if error:
        return error  # outputs without a scale: as compare() says
    ulps = ulps + (FMA_ULPS if fused else 0)
    error = compare(expected, actual, [o for o in outputs if o in scales], None, ulps)
    if error is None:
        return None
    epsilon = float(np.finfo(np.float32).eps)
    for name in outputs:
        a, b = np.asarray(expected[name], np.float64), np.asarray(actual[name], np.float64)
        scale = np.maximum(1.0, np.maximum(np.abs(a), scales.get(name, 0)))
        with np.errstate(all='ignore'):
            bad = ~((np.abs(a - b) <= ulps * epsilon * scale) | (a == b) | (np.isnan(a) & np.isnan(b)))
        if bad.any():
            i = int(np.flatnonzero(bad)[0])
            return f'{name}[{i}]: original {a[i]}, qpel {b[i]} ({int(bad.sum())} differ beyond {ulps} epsilons of the scale)'
    return None


def rows(names=None, gpu=None):
    """Yield (row, case label, error or None)."""
    from qpel import compile_program
    from .matrix import gpu_available, load_host
    if gpu is None:
        gpu = gpu_available()
    names = names or kernels()
    if not docker_ready():
        yield 'triton', 'interpreter', f'skipped: no Docker or {IMAGE} image (frontends/triton/README.md)'
        return
    built = {}
    with tempfile.TemporaryDirectory(prefix='qpel-triton-rows-') as work:
        work = Path(work)
        jobs = []
        for name in names:
            folder = work/name
            folder.mkdir()
            saved = folder/'program.qpel.json'
            try:
                prog, lowering = program(name)
                saved.write_text(json.dumps(prog))
                compile_program(saved, folder/'package')
            except Exception as error:  # noqa: BLE001 - reported as the row's failure
                yield 'triton -> qpel', name, f'{type(error).__name__}: {error}'
                continue
            plan = Lowering(prog).plan()
            generated = folder/'package/package/generated'
            (folder/'sim').mkdir()
            built[name] = (prog, lowering, plan, generated, cases(name))
            jobs.append((name, lowering.params, built[name][4]))
        expected_all = references(jobs) if jobs else {}
        for name, (prog, lowering, plan, generated, kernel_cases) in built.items():
            outputs = [r['name'] for r in plan['resources'] if r['kind'] in ('output', 'inout')]
            ulps = tolerance(prog, lowering)
            simulated = Program(generated/(plan['module'] + '.cu'), plan, work/name/'sim')
            host = load_host(generated) if gpu else None
            for i, case in enumerate(kernel_cases):
                label = f'{name} {case.label}'
                expected = expected_all[(name, i)]
                cuda = simulated.run(case)
                reduction = bool(lowering.float_sums)
                yield 'triton -> qpel -> cuda', label, check(expected, cuda, outputs, ulps, case, reduction, lowering.fused)
                if gpu:
                    from . import metal
                    apple = metal.run_generated(host, plan, case)
                    yield 'triton -> qpel -> metal', label, check(expected, apple, outputs, ulps, case, reduction, lowering.fused)
                    same = 2 * MATH_ULPS if lowering.bounded else 0
                    # (the same order of operations; bounded functions may differ, scaled as the case says)
                    yield 'qpel cuda = metal', label, (check(cuda, apple, outputs, same, case, False) if same
                                                       else compare(cuda, apple, outputs, None, 0))
                else:
                    yield 'triton -> qpel -> metal', label, 'skipped: no Apple GPU'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('kernels', nargs='*', help='kernel names (default: every Triton corpus kernel)')
    parser.add_argument('--cpu', action='store_true', help='without the Apple GPU rows')
    args = parser.parse_args(argv)
    summary, failures, skipped = {}, 0, 0
    for row, label, error in rows(args.kernels or None, gpu=False if args.cpu else None):
        if error and error.startswith('skipped'):
            skipped += 1
            print(f'SKIP {row}: {error}')
            continue
        key = (row, label.split(' ')[0])
        summary.setdefault(key, [0, 0])
        summary[key][0] += 1
        if error:
            failures += 1
            summary[key][1] += 1
            print(f'FAIL {row:26} {label}\n     {error}')
    for (row, kernel), (count, failed) in summary.items():
        print(f'{"PASS" if not failed else "FAIL"} {row:26} {kernel}: {count - failed}/{count} cases')
    print('all Triton rows passed' if not failures else f'{failures} cases failed')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
