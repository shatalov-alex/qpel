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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('names', nargs='*', help='program names (default: every Metal original)')
    parser.add_argument('--json', type=Path, help='write the results here')
    parser.add_argument('--size', type=int, nargs=3, default=SIZE, metavar=('W', 'H', 'N'))
    args = parser.parse_args(argv)
    if not metal.available():
        parser.exit(1, 'bench: needs PyTorch with an Apple GPU (MPS)\n')
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
