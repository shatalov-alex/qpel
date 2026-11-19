"""CUDA benchmarks v0: qpel's CUDA against the CUDA original it was translated from, on NVIDIA.

differential/hardware.py writes bench.json while it prepares the behaviour rows: for every CUDA
original, the program folders of the original and of qpel's CUDA (the same host program,
GpuProgram, built for both). On the GPU machine, `run` generates one case per original at the
benchmark size, with the program's own value rules (cases.scaled_case), so no large inputs
travel with the bundle. Then it runs both programs on that case. Each program runs the case once
(its outputs are kept for the comparison), then times its launches between CUDA events, with
the inout buffers restored and the outputs cleared before each repetition (GpuProgram.program).
The two sides run alternately, ROUNDS times each (original first, then qpel first, ...), and
each side's fastest median counts: one run per side measured the T4's clock and thermal state as
much as the code (run 6: llama.cpp's elementwise kernels, the same SASS on both sides, spread from
x0.85 to x1.30). Both sides' outputs (of the first run) are compared, and bench_cuda.json gets the
times, their ratio, every round's median and the differences.

    python3 -m differential.bench_cuda run ROOT     # on the GPU machine (run.sh does this)
    python3 -m differential.bench_cuda report FILE  # here: the table and the summary

The original is compiled with -fmad=false, as in the behaviour rows. qpel's CUDA is f32-strict,
so neither side contracts.
"""
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from . import cases  # noqa: E402
from .simulate import Program  # noqa: E402

ROUNDS = 3  # the sides alternate (original first, then qpel first, ...); each side's fastest median counts


def stub(entry, side):
    """What Program.write_case and read_outputs need from a program, without building it."""
    program = entry['programs'][side]
    plan = entry['plan']
    return SimpleNamespace(plan=plan, resources={r['name']: r for r in plan['resources']}, scalars=program['scalars'],
                           grids=program['grids'], original=program['original'])


def differences(entry, case, expected, actual):
    """{output: number of elements that differ} and the largest float difference in ulps."""
    typed = entry['options'].get('outputs') or {}
    differ, worst = {}, 0
    for name in entry['outputs']:
        a, b = expected[name], actual[name]
        if name in typed:  # a byte buffer holding values of another type
            dtype = cases.DTYPES[typed[name]]
            a, b = a.view(dtype), b.view(dtype)
        defined = (case.defined or {}).get(name)
        if isinstance(defined, str):  # defined where that output is nonzero (the rasteriser's depth: alpha)
            where = np.asarray(expected[defined]) != 0
            a, b = a[where], b[where]
        elif defined is not None:  # the first `defined` elements
            a, b = a[:defined], b[:defined]
        if a.dtype.kind == 'f':
            signed = np.int32 if a.dtype.itemsize == 4 else np.int16 if a.dtype.itemsize == 2 else np.int64
            mask = (1 << (8 * a.dtype.itemsize - 1)) - 1

            def ordered(x):
                bits = x.view(signed).astype(np.int64)
                return np.where(bits < 0, -(bits & mask), bits)
            distance = np.where(np.isnan(a) & np.isnan(b), 0, np.abs(ordered(a) - ordered(b)))
            differ[name] = int(np.count_nonzero(distance))
            worst = max(worst, int(distance.max(initial=0)))
        else:
            differ[name] = int(np.count_nonzero(a != b))
    return differ, worst


def run(root):
    root = Path(root)
    results = []
    for entry in json.loads((root/'bench.json').read_text()):
        result = {'program': entry['name'], 'source': entry['source']}
        results.append(result)
        try:
            case = cases.scaled_case(entry['name'], entry['plan'], entry['options'], entry['size'])
        except Exception as error:  # a program whose value rules do not scale is reported, not fatal
            case, result['skipped'] = None, f'no case at this size: {type(error).__name__}: {error}'
        if case is None:
            result.setdefault('skipped', "inputs depend on the program's own sizes; not scaled")
            continue
        times, outputs = {'original': [], 'qpel': []}, {}
        for side in [side for r in range(ROUNDS) for side in (('original', 'qpel') if r % 2 == 0 else ('qpel', 'original'))]:
            folder = root/entry['programs'][side]['dir']
            work = folder/'bench'
            work.mkdir(exist_ok=True)
            program = stub(entry, side)
            try:
                Program.write_case(program, case, work)
                (work/'time').touch()
                if not (folder/'program').exists():
                    raise RuntimeError('did not compile')
                done = subprocess.run([str(folder/'program'), str(work)], capture_output=True, text=True, timeout=300)
                if done.returncode:
                    raise RuntimeError(f'exit {done.returncode}: {done.stderr[-500:]}')
                ms, reps = (work/'time.txt').read_text().split()
                times[side].append((float(ms), int(reps)))
                if side not in outputs:
                    outputs[side] = Program.read_outputs(program, work)
            except Exception as error:
                result['skipped'] = f'{side}: {type(error).__name__}: {str(error)[:300]}'
                break
            finally:
                shutil.rmtree(work, ignore_errors=True)
        if 'skipped' in result:
            continue
        differ, worst = differences(entry, case, outputs['original'], outputs['qpel'])
        shape = case.shapes[entry['plan']['stages'][0]['grid']]
        best = {side: min(ms for ms, _ in runs) for side, runs in times.items()}
        result.update({'shape': list(shape), 'stages': len(entry['plan']['stages']),
                       'original_ms': round(best['original'], 5), 'qpel_ms': round(best['qpel'], 5),
                       'ratio': round(best['qpel'] / best['original'], 3) if best['original'] else None,
                       'reps': min(reps for runs in times.values() for _, reps in runs), 'differ': differ, 'max_ulp': worst,
                       'rounds': {side: [round(ms, 5) for ms, _ in runs] for side, runs in times.items()}})
        print(f'{entry["name"]:<40} original {best["original"]:9.4f} ms   qpel {best["qpel"]:9.4f} ms', flush=True)
    (root/'bench_cuda.json').write_text(json.dumps(results, indent=1) + '\n')
    return results


def report(results):
    """Print the table and the summary; return the timed ratios."""
    for r in results:
        if 'skipped' in r:
            print(f'SKIP {r["program"]:<40} {r["skipped"]}')
            continue
        differ = sum(r['differ'].values())
        note = '' if not differ else f'   {differ} elements differ (max {r["max_ulp"]} ulp)'
        print(f'{r["program"]:<40} original {r["original_ms"]:9.4f} ms   qpel {r["qpel_ms"]:9.4f} ms'
              f'   x{r["ratio"]:.2f}{note}')
    timed = [r['ratio'] for r in results if r.get('ratio')]
    if timed:
        print(f'\n{len(timed)} timed, {len(results) - len(timed)} skipped; qpel/original geometric mean '
              f'x{math.exp(sum(map(math.log, timed)) / len(timed)):.2f}, median x{statistics.median(timed):.2f}, '
              f'range x{min(timed):.2f} to x{max(timed):.2f}')
    return timed


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ('run', 'report'):
        raise SystemExit('usage: python3 -m differential.bench_cuda run ROOT | report FILE')
    if argv[0] == 'run':
        run(argv[1])
    else:
        report(json.loads(Path(argv[1]).read_text()))


if __name__ == '__main__':
    main()
