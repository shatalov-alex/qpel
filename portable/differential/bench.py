"""Benchmarks v0: the time qpel's Metal takes against the Metal original it was translated from.

For every Metal original in the behaviour matrix (llama.cpp's ggml-metal kernels, the
examples), the original and qpel's Metal (Metal -> qpel -> Metal) run the same inputs at a
larger size than the behaviour cases, on the Apple GPU. Each runs once through its normal
runner, which also checks the two agree; the kernel dispatches it made are recorded and then
replayed and timed between GPU synchronisations, the two sides alternating batch by batch:
the dispatches only, without compiling, allocating, copying or host-side checks. The figure is
the median over batches of replays.

    python3 -m differential.bench                 # every Metal original
    python3 -m differential.bench --json out.json kernel_name ...

The original runs as the behaviour matrix runs it, without FMA contraction (`fp contract(off)`);
qpel's Metal is f32-strict, so neither contracts. Nothing here measures CUDA (that needs an
NVIDIA GPU) or the host overhead of the generated torch hosts.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import statistics
import sys
import tempfile

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from . import matrix, metal  # noqa: E402
from . import cases as test_cases  # noqa: E402

SIZE = (2048, 1024, 4)  # 8M threads: below about 1M, dispatch overhead dominates and ratios are noise
MIN_SECONDS, MAX_REPS = 0.3, 200


class Recorder:
    """Stands in for a compiled shader library: runs each kernel and keeps the call for replay."""

    def __init__(self, lib, calls):
        self.lib, self.calls = lib, calls

    def __getattr__(self, name):
        function = getattr(self.lib, name)

        def call(*args, **kwargs):
            self.calls.append((function, args, kwargs))
            return function(*args, **kwargs)
        return call


@contextmanager
def recording(module, attribute, calls):
    """Replace module.attribute (a function returning a library) by one returning a Recorder."""
    original = getattr(module, attribute)
    setattr(module, attribute, lambda *a, **k: Recorder(original(*a, **k), calls))
    try:
        yield
    finally:
        setattr(module, attribute, original)


def replay(calls, count):
    """Milliseconds for `count` replays of the calls, between two GPU synchronisations."""
    import time
    import torch
    torch.mps.synchronize()
    start = time.perf_counter()
    for _ in range(count):
        for function, args, kwargs in calls:
            function(*args, **kwargs)
    torch.mps.synchronize()
    return (time.perf_counter() - start) * 1000


def gpu_times(*sides):
    """Median milliseconds of one replay of each side's calls, and the replays timed per side.

    Wall time between two synchronisations, over batches long enough (>= 5 ms) that the
    synchronisation itself is small. The sides alternate batch by batch after a common warm-up
    (>= 50 ms each), so neither gets the cold GPU or the warm caches. MPS events would time the
    GPU alone, but Event.elapsed_time waits forever for compile_shader dispatches (torch 2.14)."""
    import time
    inner = []
    for calls in sides:
        once = max(replay(calls, 1), 1e-3)
        replay(calls, max(1, int(50 / once)))  # warm up
        inner.append(max(1, min(1000, int(5 / once))))
    times, started = [[] for _ in sides], time.perf_counter()
    while len(times[0]) < MAX_REPS and (time.perf_counter() - started < MIN_SECONDS or len(times[0]) < 5):
        for calls, count, kept in zip(sides, inner, times):
            kept.append(replay(calls, count) / count)
    return [statistics.median(kept) for kept in times], len(times[0]) * min(inner)


def large_case(prepared, size):
    """One behaviour-style case at `size` (cases.scaled_case)."""
    return test_cases.scaled_case(prepared.name, prepared.plan, prepared.options, size)


def bench_source(source, work, size):
    name, path, dialect, plan, include, _, options = source
    prepared = matrix.Prepared(name, path, dialect, plan, include, options, work)
    case = large_case(prepared, size)
    if case is None:
        return {'program': name, 'skipped': 'inputs depend on the program\'s own sizes; not scaled'}
    original_calls, generated_calls = [], []
    with recording(metal, '_compiled', original_calls):
        metal._LIBRARIES.clear()  # compiled outside the recording otherwise
        expected = prepared.run_metal_original(case)
    host = matrix.load_host(prepared.generated)
    with recording(host, '_library', generated_calls):
        actual = metal.run_generated(host, plan, case)
    error = prepared.compare(expected, actual, case)
    if error:
        return {'program': name, 'skipped': 'outputs differ at this size: ' + str(error)[:200]}
    (original_ms, qpel_ms), reps = gpu_times(original_calls, generated_calls)
    shape = case.shapes[plan['stages'][0]['grid']]
    return {'program': name, 'source': str(Path(path).relative_to(HERE)), 'shape': list(shape),
            'dispatches': len(generated_calls), 'original_ms': round(original_ms, 4), 'qpel_ms': round(qpel_ms, 4),
            'ratio': round(qpel_ms / original_ms, 3), 'reps': reps}


FP4 = [0.0, 0.005208333333, 0.66666667, 1.0, 0.33333333, 0.5, 0.16666667, 0.25]  # bitsandbytes' kernels.cu tables
NF4 = [-1.0, -0.6961928009986877, -0.5250730514526367, -0.39491748809814453, -0.28444138169288635, -0.18477343022823334,
       -0.09105003625154495, 0.0, 0.07958029955625534, 0.16093020141124725, 0.24611230194568634, 0.33791524171829224,
       0.44070982933044434, 0.5626170039176941, 0.7229568362236023, 1.0]


def torch_dequantize(name, case):
    """bitsandbytes' blockwise dequantisation as PyTorch tensor operations on the Apple GPU (how a PyTorch
    backend without bitsandbytes' kernels computes it; written here, not bitsandbytes' own code): a
    baseline for qpel's Metal where no Metal original exists. Returns a function computing the output."""
    import torch
    device = torch.device('mps')
    codes = torch.from_numpy(case.inputs['A'].astype(np.int64)).to(device)
    absmax = torch.from_numpy(case.inputs['absmax']).to(device)
    n, block = case.values['n'], case.values['blocksize']
    if name.endswith('8bit'):
        code = torch.from_numpy(case.inputs['code']).to(device)
        index = torch.arange(n, device=device) // block
        return lambda: code[codes[:n]] * absmax[index]
    if name.endswith('nf4'):
        table = torch.tensor(NF4, dtype=torch.float32, device=device)
    else:
        magnitude = torch.tensor(FP4, dtype=torch.float32, device=device)
        table = torch.cat([magnitude, -magnitude])  # the sign is bit 3
    index = torch.arange(n, device=device) // (2 * block)  # 4 bits: blocksize / 2 bytes per block of absmax
    def run():
        nibbles = torch.stack([codes >> 4, codes & 15], 1).reshape(-1)[:n]
        return table[nibbles] * absmax[index]
    return run


def torch_time(run):
    """Median milliseconds of run() on the Apple GPU (batches between synchronisations, as gpu_times)."""
    import time
    import torch
    def batch(count):
        torch.mps.synchronize()
        start = time.perf_counter()
        for _ in range(count):
            run()
        torch.mps.synchronize()
        return (time.perf_counter() - start) * 1000 / count
    once = max(batch(1), 1e-3)
    batch(max(1, int(50 / once)))
    inner = max(1, min(1000, int(5 / once)))
    times, started = [], time.perf_counter()
    while len(times) < MAX_REPS and (time.perf_counter() - started < MIN_SECONDS or len(times) < 5):
        times.append(batch(inner))
    return statistics.median(times)


BASELINES = {'bnb_dequantize_': ('PyTorch on MPS', torch_dequantize)}


def bench_translation(source, work, size):
    """qpel's Metal for a CUDA original (a pilot: gsplat, bitsandbytes), alone: there is no Metal original
    to time it against. Its outputs are checked against the original by the behaviour rows, at their
    sizes; here only the dispatches are timed."""
    name, path, dialect, plan, include, _, options = source
    prepared = matrix.Prepared(name, path, dialect, plan, include, options, work)
    case = large_case(prepared, size)
    if case is None:
        return {'program': name, 'skipped': 'inputs depend on the program\'s own sizes; not scaled'}
    calls = []
    host = matrix.load_host(prepared.generated)
    with recording(host, '_library', calls):
        ours = metal.run_generated(host, plan, case)
    (qpel_ms,), reps = gpu_times(calls)
    result = {'program': name, 'source': str(Path(path).relative_to(HERE)), 'case': case.label,
              'dispatches': len(calls), 'qpel_ms': round(qpel_ms, 4), 'reps': reps}
    for prefix, (label, make) in BASELINES.items():
        if name.startswith(prefix):
            run = make(name, case)
            output = next(iter(r['name'] for r in plan['resources'] if r['kind'] == 'output'))
            same = np.array_equal(run().cpu().numpy().view(np.uint32), np.asarray(ours[output])[:case.values['n']].view(np.uint32))
            result.update(baseline=label, baseline_same=bool(same), baseline_ms=round(torch_time(run), 4))
            result['ratio'] = round(qpel_ms / result['baseline_ms'], 3)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('names', nargs='*', help='program names (default: every Metal original)')
    parser.add_argument('--json', type=Path, help='write the results here')
    parser.add_argument('--size', type=int, nargs=3, default=SIZE, metavar=('W', 'H', 'N'))
    parser.add_argument('--cuda', action='store_true',
                        help='time qpel\'s Metal for the named CUDA originals, alone (pilots without a Metal original)')
    args = parser.parse_args(argv)
    if not metal.available():
        parser.exit(1, 'bench: needs PyTorch with an Apple GPU (MPS)\n')
    if args.cuda:
        results = []
        with tempfile.TemporaryDirectory(prefix='qpel-bench-') as folder:
            for i, source in enumerate(s for s in matrix.sources() if s[2] == 'cuda' and s[0] in args.names):
                work = Path(folder)/f'{i}_{source[0]}'
                work.mkdir()
                result = bench_translation(source, work, tuple(args.size))
                results.append(result)
                print(f'{result["program"]:<40} ' + (f'SKIP {result["skipped"]}' if 'skipped' in result else
                                                      f'qpel\'s Metal {result["qpel_ms"]:9.4f} ms   ({result["case"]})') +
                      (f'   {result["baseline"]} {result["baseline_ms"]:9.4f} ms x{result["ratio"]:.2f}'
                       f'{"" if result["baseline_same"] else " (outputs differ)"}' if 'baseline' in result else ''), flush=True)
        if args.json:
            args.json.write_text(json.dumps({'size': list(args.size), 'results': results}, indent=1) + '\n')
        return
    sources = [s for s in matrix.sources() if s[2] == 'metal' and (not args.names or s[0] in args.names)]
    results = []
    with tempfile.TemporaryDirectory(prefix='qpel-bench-') as folder:
        for i, source in enumerate(sources):
            work = Path(folder)/f'{i}_{source[0]}'
            work.mkdir()
            try:
                result = bench_source(source, work, tuple(args.size))
            except Exception as error:  # a program that cannot run at this size is reported, not fatal
                result = {'program': source[0], 'skipped': f'{type(error).__name__}: {str(error)[:200]}'}
            results.append(result)
            if 'skipped' in result:
                print(f'SKIP {result["program"]:<40} {result["skipped"]}', flush=True)
            else:
                print(f'{result["program"]:<40} original {result["original_ms"]:9.4f} ms   qpel {result["qpel_ms"]:9.4f} ms'
                      f'   x{result["ratio"]:.2f}', flush=True)
    timed = [r['ratio'] for r in results if 'ratio' in r]
    if timed:
        print(f'\n{len(timed)} timed, {len(results) - len(timed)} skipped; qpel/original geometric mean '
              f'x{float(np.exp(np.mean(np.log(timed)))):.2f}, median x{statistics.median(timed):.2f}, '
              f'range x{min(timed):.2f} to x{max(timed):.2f}')
    if args.json:
        args.json.write_text(json.dumps({'size': list(args.size), 'results': results}, indent=1) + '\n')


if __name__ == '__main__':
    main()
