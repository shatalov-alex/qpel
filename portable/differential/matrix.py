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
from frontends.clang_ast import metal_available  # noqa: E402
from qpel_ir import Lowering  # noqa: E402
from qpel_types import EXACT_MATH, MATH_ULPS, normalize_plan  # noqa: E402
from . import cases as test_cases  # noqa: E402
from .simulate import Program  # noqa: E402


def sources():
    """(program name, source path, dialect, plan, include, original grids, options) for every original;
    options may select kernels ('kernels', 'module') and constrain values ('values')."""
    out = []
    for source_name, name in roundtrip.EXAMPLES.items():
        plan = Lowering(roundtrip.fixture(name)).plan()
        for dialect in ('cuda', 'metal'):
            path = roundtrip.SOURCE_DIRS[dialect]/(source_name + roundtrip.SUFFIX[dialect])
            out.append((name, path, dialect, plan, [], {}, {}))
    for entry in roundtrip.corpus_entries():
        if 'fixture' in entry:
            path = roundtrip.CORPUS/entry['source']
            plan = normalize_plan(json.loads((roundtrip.CORPUS/entry['plan']).read_text()))
            include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
            out.append((entry['fixture'], path, 'cuda' if path.suffix == '.cu' else 'metal', plan, include,
                        entry.get('grids', {}), {}))
            for port in entry.get('ports', ()):
                port = HERE/port
                out.append((entry['fixture'], port, 'cuda' if port.suffix == '.cu' else 'metal', plan, [], {}, {}))
    for entry, program in roundtrip.corpus_programs():
        path = roundtrip.CORPUS/entry['source']
        include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
        plan = Lowering(roundtrip.fixture(program['fixture'])).plan()
        options = {'kernels': program['kernels'], 'module': program['fixture'], 'values': program.get('values', {}),
                   'constants': program.get('constants'), 'geometry': program.get('geometry'),
                   'shapes': program.get('shapes'), 'inputs': program.get('inputs'), 'outputs': program.get('outputs')}
        out.append((program['fixture'], path, 'cuda' if path.suffix == '.cu' else 'metal', plan, include, {}, options))
    return out


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
            dtype = test_cases.DTYPES[views[name]]
            a, b = a.view(dtype), b.astype(a.dtype, copy=False).view(dtype)
        if defined and name in defined:
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


def load_host(folder):
    spec = importlib.util.spec_from_file_location(f'host_{abs(hash(str(folder)))}', folder/'metal_host.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    return host


class Prepared:
    """One original imported to qpel, with qpel's generated package built from it."""

    def __init__(self, name, path, dialect, plan, include, options, work):
        from qpel import compile_program
        self.name, self.path, self.dialect, self.plan, self.include, self.options = \
            name, path, dialect, plan, include, options
        self.program, self.report = import_source(path, plan=plan, include=include, kernels=options.get('kernels'),
                                                  module=options.get('module'), constants=options.get('constants'))
        saved = work/'program.qpel.json'
        saved.write_text(json.dumps(self.program))
        compile_program(saved, work/'package')
        self.generated = work/'package/package/generated'
        self.cuda = self.generated/(plan['module'] + '.cu')
        self.outputs = [r['name'] for r in plan['resources'] if r['kind'] == 'output']
        # Bounded math functions may differ by a few ulp between C libraries and GPUs; results
        # that pass through two of them (original and translation) get twice the bound.
        bounded = any(n['kind'] == 'Math' and n['attributes']['function'] not in EXACT_MATH
                      for n in self.program['nodes'])
        self.ulps = 2*MATH_ULPS if bounded else 0

    def cases(self):
        o = self.options
        return test_cases.for_program(self.name, self.plan, values=o.get('values'), shapes=o.get('shapes'),
                                      inputs=o.get('inputs'))

    def compare(self, expected, actual, case):
        """compare() with this program's outputs, tolerance and byte-buffer views."""
        sign = self.dialect == 'cuda'  # a Metal original carries Apple's zero signs (see compare)
        return compare(expected, actual, self.outputs, case.defined, self.ulps, self.options.get('outputs'), sign)

    def run_metal_original(self, case):
        from . import metal
        return metal.run_original(self.path, self.plan, self.report, case, self.include, self.options.get('constants'))


def check_source(name, path, dialect, plan, include, grids, options, work, gpu):
    """Yield (row, case label, error or None) for one original source."""
    p = Prepared(name, path, dialect, plan, include, options, work)
    simulated = Program(p.cuda, plan, work/'sim_qpel')
    original = None
    if dialect == 'cuda':
        original = Program(path, plan, work/'sim_original', original=p.report, include=include, grids=grids)
    host = load_host(p.generated) if gpu else None
    rows = {'cuda': (f'{dialect} -> qpel -> cuda', True), 'metal': (f'{dialect} -> qpel -> metal', gpu)}
    for case in p.cases():
        label = f'{path.name} {case.label}'
        if dialect == 'cuda':
            expected = original.run(case)
        elif gpu:
            expected = p.run_metal_original(case)
        else:
            yield f'{dialect} original', label, 'skipped: no Apple GPU'
            continue
        yield rows['cuda'][0], label, p.compare(expected, simulated.run(case), case)
        if rows['metal'][1]:
            from . import metal  # PyTorch; only the Apple GPU rows need it
            yield rows['metal'][0], label, p.compare(expected, metal.run_generated(host, plan, case), case)
        else:
            yield rows['metal'][0], label, 'skipped: no Apple GPU'


def rows(names=None, gpu=None):
    if gpu is None:
        try:
            from . import metal
            gpu = metal.available()
        except ImportError:
            gpu = False
    for index, (name, path, dialect, plan, include, grids, options) in enumerate(sources()):
        if names and name not in names:
            continue
        if dialect == 'metal' and not metal_available():
            yield 'metal original', path.name, 'skipped: no Metal toolchain (Xcode) to import it'
            continue
        with tempfile.TemporaryDirectory(prefix='qpel-differential-') as work:
            work = Path(work)
            for folder in ('sim_qpel', 'sim_original'):
                (work/folder).mkdir()
            try:
                yield from check_source(name, path, dialect, plan, include, grids, options, work, gpu)
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
          + (f' ({skipped} skipped: no Apple GPU or Metal toolchain)' if skipped else ''))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
