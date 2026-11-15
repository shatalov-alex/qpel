"""Behaviour rows of qpel's Triton frontend: Triton kernels against what qpel makes of them.

For every kernel of corpus/triton (kernels.py, compiled to TTIR in corpus/triton/build), the
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
from frontends.triton.lower import lower  # noqa: E402
from qpel_ir import Lowering  # noqa: E402
from qpel_text import parse_program  # noqa: E402
from qpel_types import MATH_ULPS  # noqa: E402
from .compare import compare  # noqa: E402
from .simulate import Case, Program  # noqa: E402

CORPUS = HERE/'corpus/triton'
BUILD = CORPUS/'build'
IMAGE = 'qpel/triton:3.8.0'


def kernels():
    return list(json.loads((CORPUS/'kernels.json').read_text()))


def program(name):
    """(qpel IR, the Lowering) of one corpus kernel."""
    meta = json.loads((BUILD/f'{name}.json').read_text())
    text, lowering = lower((BUILD/f'{name}.ttir').read_text(), meta['num_warps'], 'triton_' + name)
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


CASES = {'matmul_kernel': matmul_cases, 'row_stats_kernel': row_stats_cases, 'add_kernel': add_cases, 'scale_shift_kernel': scale_shift_cases, 'softmax_kernel': softmax_cases,
         'copy2d_kernel': copy2d_cases, 'layer_norm_kernel': layer_norm_cases}


def cases(name, seed=20261115):
    return CASES[name](np.random.default_rng(seed))


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
        result = subprocess.run(['docker', 'run', '--rm', '--label', 'qpel=1', '--memory', '4g', '--cpus', '2',
                                 '-e', 'TRITON_INTERPRET=1', '-v', f'{HERE/"frontends/triton"}:/tool:ro',
                                 '-v', f'{CORPUS}:/corpus:ro', '-v', f'{work}:/cases', IMAGE,
                                 'python', '/tool/reference.py', '/corpus/kernels.py', '/corpus/kernels.json', '/cases'],
                                capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(f'Triton interpreter failed:\n{result.stderr[-3000:]}')
        return {key: {b: np.load(folder/f'out_{b}.npy') for b in buffers} for key, (folder, buffers) in folders.items()}


# -- rows --

def tolerance(prog, lowering):
    """ulps for compare(): bounded math functions, and float sum reductions (their longest axis)."""
    ulps = 2 * MATH_ULPS if lowering.bounded else 0
    if lowering.float_sums:
        ulps += 2 * math.ceil(math.log2(max(2, max(lowering.float_sums)))) + 4
    return ulps


def check(expected, actual, outputs, ulps, case, reduction):
    """compare(), and for a program with float reductions, a case may give each output a scale (for a
    dot, the sum of |products|, which bounds its rounding error): an element then also matches
    within `reduction` epsilons of max(1, |value|, scale)."""
    error = compare(expected, actual, outputs, None, ulps)
    scales = getattr(case, 'scales', {})
    if error is None or not reduction or not scales:
        return error
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
            try:
                prog, lowering = program(name)
            except Exception as error:  # noqa: BLE001 - reported as the row's failure
                yield 'triton -> qpel', name, f'{type(error).__name__}: {error}'
                continue
            folder = work/name
            folder.mkdir()
            saved = folder/'program.qpel.json'
            saved.write_text(json.dumps(prog))
            compile_program(saved, folder/'package')
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
                yield 'triton -> qpel -> cuda', label, check(expected, cuda, outputs, ulps, case, reduction)
                if gpu:
                    from . import metal
                    apple = metal.run_generated(host, plan, case)
                    yield 'triton -> qpel -> metal', label, check(expected, apple, outputs, ulps, case, reduction)
                    same = 2 * MATH_ULPS if lowering.bounded else 0
                    yield 'qpel cuda = metal', label, compare(cuda, apple, outputs, None, same)
                else:
                    yield 'triton -> qpel -> metal', label, 'skipped: no Apple GPU'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('kernels', nargs='*', help='kernel names (default: all of corpus/triton/kernels.json)')
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
