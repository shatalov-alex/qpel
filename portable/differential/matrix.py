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
from qpel_types import EXACT_MATH, MATH_ULPS, math_ulps, normalize_plan  # noqa: E402
from . import cases as test_cases
from .compare import compare, flushed, ulp_distance  # noqa: E402,F401  (used here and by callers)
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
                   'shapes': program.get('shapes'), 'inputs': program.get('inputs'), 'outputs': program.get('outputs'),
                   'shared': program.get('shared'), 'ulps': program.get('ulps'), 'defines': roundtrip.corpus_defines(entry)}
        out.append((program['fixture'], path, 'cuda' if path.suffix == '.cu' else 'metal', plan, include, {}, options))
    return out


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
                                                  module=options.get('module'), constants=options.get('constants'),
                                                  shared=options.get('shared'), defines=options.get('defines', ()))
        saved = work/'program.qpel.json'
        saved.write_text(json.dumps(self.program))
        compile_program(saved, work/'package')
        self.generated = work/'package/package/generated'
        self.cuda = self.generated/(plan['module'] + '.cu')
        self.outputs = [r['name'] for r in plan['resources'] if r['kind'] in ('output', 'inout')]
        # Bounded math functions may differ by a few ulp between C libraries and GPUs; results
        # that pass through two of them (original and translation) get twice the bound.
        bounded = any(n['kind'] == 'Math' and n['attributes']['function'] not in EXACT_MATH
                      for n in self.program['nodes'])
        self.ulps = 2*math_ulps({n['attributes']['function'] for n in self.program['nodes'] if n['kind'] == 'Math'})
        if bounded and options.get('ulps'):  # a program's own bound, where bounded results compound
            self.ulps = max(self.ulps, int(options['ulps']))  # (corpus.json "ulps", with its reason)

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


def gpu_available():
    """True when the Apple GPU rows can run (PyTorch with MPS)."""
    try:
        from . import metal
        return metal.available()
    except ImportError:
        return False


def rows(names=None, gpu=None, index=None):
    """Yield (row, case label, error or None); `index` limits it to sources()[index]."""
    if gpu is None:
        gpu = gpu_available()
    for number, (name, path, dialect, plan, include, grids, options) in enumerate(sources()):
        if names and name not in names or index is not None and number != index:
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


def source_rows(indices, gpu=None):
    """{index: every row of sources()[index]} for the given sources: the unit of work for
    parallel runs. Sources of one file share its parse (clang_ast caches per process)."""
    return {index: list(rows(index=index, gpu=gpu)) for index in indices}


def parallel(gpu=None, workers=None, chunk=6, metal_chunk=6):
    """(executor, {source index: future of source_rows}) computed in fresh worker processes
    (spawned, so PyTorch's GPU state is never forked); the caller shuts the executor down.
    Sources are grouped by file, in chunks of `chunk`, so a large library file is parsed a
    few times in parallel rather than once per program. Metal files get larger chunks: after
    the first program a worker reuses the file's parse and its compiled library, and Metal
    compiles of ggml-metal.metal running side by side wait for each other."""
    import multiprocessing
    import os
    from concurrent.futures import ProcessPoolExecutor
    gpu = gpu_available() if gpu is None else gpu
    by_file = {}
    for index, (_, path, *_rest) in enumerate(sources()):
        by_file.setdefault(path, []).append(index)
    tasks = []
    for path, group in by_file.items():
        size = metal_chunk if path.suffix == '.metal' else chunk
        tasks += [group[i:i + size] for i in range(0, len(group), size)]
    tasks.sort(key=len, reverse=True)  # the long ones first
    # memory: a worker holding ggml-metal.metal's parse reaches 6-7 GB, so two at once as the corpus survey
    workers = workers or max(1, min(len(tasks), (os.cpu_count() or 2) // 2, int(os.environ.get('QPEL_SURVEY_WORKERS', 2))))
    executor = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context('spawn'))
    futures = {}
    for task in tasks:
        future = executor.submit(source_rows, task, gpu)
        futures.update({index: future for index in task})
    return executor, futures


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
