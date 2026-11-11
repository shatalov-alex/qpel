"""Behaviour rows of the round-trip matrix: originals against what qpel makes of them.

For every original source (the hand-written CUDA and Metal examples, and corpus
files with a fixture), the source is imported, qpel builds CUDA and Metal from
the result, and the same cases run through the original and both outputs:

    CUDA original  (CPU simulator)  vs  qpel's CUDA (simulator)    row CUDA -> qpel -> CUDA
                                    vs  qpel's Metal (Apple GPU)   row CUDA -> qpel -> Metal
    Metal original (Apple GPU)      vs  qpel's Metal (Apple GPU)   row Metal -> qpel -> Metal
                                    vs  qpel's CUDA (simulator)    row Metal -> qpel -> CUDA

Every output resource must match exactly (floats bit for bit). Rows that need
the Apple GPU are skipped, and reported as such, when it is unavailable.

    python3 -m differential.matrix               # all sources
    python3 -m differential.matrix sam2_cc box_sum
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from frontends import roundtrip  # noqa: E402
from frontends.c_import import import_source  # noqa: E402
from qpel_ir import Lowering  # noqa: E402
from qpel_types import EXACT_MATH, MATH_ULPS, normalize_plan  # noqa: E402
from . import cases as test_cases  # noqa: E402
from .simulate import Program  # noqa: E402


def sources():
    """(program name, source path, dialect, plan, include, original grids) for every original."""
    out = []
    for source_name, name in roundtrip.EXAMPLES.items():
        plan = Lowering(roundtrip.fixture(name)).plan()
        for dialect in ('cuda', 'metal'):
            path = roundtrip.SOURCE_DIRS[dialect]/(source_name + roundtrip.SUFFIX[dialect])
            out.append((name, path, dialect, plan, [], {}))
    for entry in roundtrip.corpus_entries():
        if 'fixture' in entry:
            path = roundtrip.CORPUS/entry['source']
            plan = normalize_plan(json.loads((roundtrip.CORPUS/entry['plan']).read_text()))
            include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
            out.append((entry['fixture'], path, 'cuda' if path.suffix == '.cu' else 'metal', plan, include,
                        entry.get('grids', {})))
            for port in entry.get('ports', ()):
                port = HERE/port
                out.append((entry['fixture'], port, 'cuda' if port.suffix == '.cu' else 'metal', plan, [], {}))
    return out


def ulp_distance(a, b):
    """Distance in float32 units in the last place (ordered bit patterns); NaN to NaN is 0."""
    def ordered(x):
        bits = x.astype(np.float32).view(np.int32).astype(np.int64)
        return np.where(bits < 0, -(bits & 0x7fffffff), bits)
    distance = np.abs(ordered(a) - ordered(b))
    return np.where(np.isnan(a) & np.isnan(b), 0, distance)


def compare(expected, actual, names, defined=None, ulps=0):
    """None, or where the first difference is. `defined` limits outputs to their written prefix;
    `ulps` > 0 lets float32 outputs differ by that many units in the last place."""
    for name in names:
        a, b = np.asarray(expected[name]), np.asarray(actual[name])
        if defined and name in defined:
            a, b = a[:defined[name]], b[:defined[name]]
            expected, actual = dict(expected, **{name: a}), dict(actual, **{name: b})
        if a.shape != b.shape:
            return f'{name}: {a.shape} != {b.shape}'
        if ulps and a.dtype == np.float32:
            bad = np.flatnonzero(ulp_distance(a, b) > ulps)
            if bad.size:
                i = bad[0]
                return (f'{name}[{i}]: original {a[i].item()!r}, qpel {b[i].item()!r} '
                        f'({bad.size} differ by more than {ulps} ulp)')
            continue
        if a.dtype.kind == 'f':
            a, b = a.view(f'u{a.itemsize}'), b.view(f'u{b.itemsize}')
        bad = np.flatnonzero(a != b)
        if bad.size:
            i = bad[0]
            return f'{name}[{i}]: original {expected[name][i].item()!r}, qpel {actual[name][i].item()!r} ({bad.size} differ)'
    return None


def load_host(folder):
    spec = importlib.util.spec_from_file_location(f'host_{abs(hash(str(folder)))}', folder/'metal_host.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    return host


def check_source(name, path, dialect, plan, include, grids, work, gpu):
    """Yield (row, case label, error or None) for one original source."""
    from qpel import compile_program
    from . import metal
    program, report = import_source(path, plan=plan, include=include)
    saved = work/'program.qpel.json'
    saved.write_text(json.dumps(program))
    compile_program(saved, work/'package')
    generated = work/'package/package/generated'
    module = plan['module']
    outputs = [r['name'] for r in plan['resources'] if r['kind'] == 'output']
    simulated = Program(generated/(module + '.cu'), plan, work/'sim_qpel')
    original = None
    if dialect == 'cuda':
        original = Program(path, plan, work/'sim_original', original=report, include=include, grids=grids)
    host = load_host(generated) if gpu else None
    rows = {'cuda': (f'{dialect} -> qpel -> cuda', True), 'metal': (f'{dialect} -> qpel -> metal', gpu)}
    # Bounded math functions may differ by a few ulp between C libraries and GPUs; results
    # that pass through two of them (original and translation) get twice the bound.
    bounded = any(n['kind'] == 'Math' and n['attributes']['function'] not in EXACT_MATH for n in program['nodes'])
    ulps = 2*MATH_ULPS if bounded else 0
    for case in test_cases.for_program(name, plan):
        label = f'{path.name} {case.label}'
        if dialect == 'cuda':
            expected = original.run(case)
        elif gpu:
            expected = metal.run_original(path, plan, report, case)
        else:
            yield f'{dialect} original', label, 'skipped: no Apple GPU'
            continue
        yield rows['cuda'][0], label, compare(expected, simulated.run(case), outputs, case.defined, ulps)
        if rows['metal'][1]:
            yield rows['metal'][0], label, compare(expected, metal.run_generated(host, plan, case), outputs, case.defined, ulps)
        else:
            yield rows['metal'][0], label, 'skipped: no Apple GPU'


def rows(names=None, gpu=None):
    if gpu is None:
        try:
            from . import metal
            gpu = metal.available()
        except ImportError:
            gpu = False
    for index, (name, path, dialect, plan, include, grids) in enumerate(sources()):
        if names and name not in names:
            continue
        with tempfile.TemporaryDirectory(prefix='qpel-differential-') as work:
            work = Path(work)
            for folder in ('sim_qpel', 'sim_original'):
                (work/folder).mkdir()
            try:
                yield from check_source(name, path, dialect, plan, include, grids, work, gpu)
            except (ValueError, RuntimeError) as error:
                yield f'{dialect} -> qpel', path.name, str(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('programs', nargs='*', help='fixture names (default: all)')
    parser.add_argument('--cpu', action='store_true', help='only the simulator rows, without the Apple GPU')
    args = parser.parse_args(argv)
    summary = {}
    failures = skipped = 0
    for row, label, error in rows(args.programs or None, gpu=False if args.cpu else None):
        key = (row, label.split(' ')[0])
        summary.setdefault(key, [0, 0])
        if error and error.startswith('skipped'):
            skipped += 1
            continue
        summary[key][0] += 1
        if error:
            failures += 1
            summary[key][1] += 1
            print(f'FAIL {row:24} {label}\n     {error}')
    for (row, source), (count, failed) in summary.items():
        if count:
            print(f'{"PASS" if not failed else "FAIL"} {row:24} {source}: {count - failed}/{count} cases')
    print(('all behaviour rows passed' if not failures else f'{failures} cases failed')
          + (f' ({skipped} skipped: no Apple GPU)' if skipped else ''))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
