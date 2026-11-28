"""Coverage of real GPU sources: which kernels qpel can import, and what blocks the rest.

The corpus (corpus/corpus.json) lists unmodified third-party sources. Each one is
read in survey mode, which records every construct the importer cannot map instead
of stopping at the first. A kernel is importable when it and every device function
it calls have no blockers.

corpus/coverage.json records the current result; tests/test_corpus.py fails when
the result changes, so progress (or a regression) is always a visible diff:

    python3 -m frontends.corpus            # print the report
    python3 -m frontends.corpus --update   # rewrite coverage.json after a change
    python3 -m frontends.corpus --messages # every blocker message, most frequent first
    python3 -m frontends.corpus --check    # fail if the result differs (check_all runs this)
"""
import os
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
import re
from pathlib import Path

from . import clang_ast
from .c_import import Importer, Unsupported

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE/'corpus'
COVERAGE = CORPUS/'coverage.json'
# Parallel surveys: each may hold a source's AST (clang_ast.UNIT_LIMIT, about 4 GB in Python), so at
# most three at once (one per core took all of a 48 GB Mac's memory on the extension sources).
UNPARSED = '(source does not parse)'  # coverage.json key of a source clang rejects
SURVEY_WORKERS = int(os.environ.get('QPEL_SURVEY_WORKERS', 2))
# Each source is surveyed in a fresh worker process (max_tasks_per_child=1): a worker would otherwise keep
# the units clang_ast caches (up to 4, a gigabyte or more each) from one source to the next, and two
# workers then hold over 14 GB by the end of the corpus.


def build_defines(entry):
    """An entry's macros and language standard, as clang_ast.flags reads them."""
    return list(entry.get('defines', ())) + ([f'-std={entry["standard"]}'] if entry.get('standard') else [])


def entries():
    return json.loads((CORPUS/'corpus.json').read_text())


def survey(entry):
    """{function: {'kind', 'issues', 'calls'}} for one corpus source."""
    path = CORPUS/entry['source']
    dialect = clang_ast.dialect_of(path)
    unit, source = clang_ast.parse(path, dialect, [CORPUS/folder for folder in entry.get('include', ())],
                                   build_defines(entry))
    found = Importer(unit, source, path, dialect, path.stem, survey=True).survey()
    for skipped in unit.get('_skipped', ()):  # too large to read (clang_ast.DECLARATION_LIMIT)
        if skipped['kernel'] and not skipped['system']:
            found.setdefault(skipped['name'], {'kind': 'kernel', 'calls': [], 'issues': [{
                'code': 'ast-too-large', 'at': f'{path}:0:0',
                'message': 'its instantiations make more JSON AST than qpel reads at once'}]})
    return found


def kernels(functions):
    """{kernel: sorted blockers}, each blocker 'code at function:line' including called functions."""
    out = {}
    for name, info in functions.items():
        if info['kind'] != 'kernel':
            continue
        seen, stack, blockers = set(), [name], set()
        while stack:
            current = stack.pop()
            if current in seen or current not in functions:
                continue
            seen.add(current)
            for issue in functions[current]['issues']:
                line = issue['at'].split(':')[-2]
                blockers.add(f'{issue["code"]} at {current}:{line}')
            stack.extend(functions[current]['calls'])
        out[name] = sorted(blockers)
    return out


def source_messages(entry):
    """Counter of (code, message with numbers blanked) over every function of one source."""
    found = Counter()
    try:
        surveyed = survey(entry)
    except clang_ast.SourceError:  # counted as a source that does not parse (coverage: UNPARSED)
        found[('parse', 'the source does not parse with qpel\'s stand-ins')] += 1
        return found
    for info in surveyed.values():
        for issue in info['issues']:
            found[(issue['code'], re.sub(r'\b\d+\b', 'N', issue['message']))] += 1
    return found


def source_coverage(entry, details=False):
    """{kernel: blockers}: their codes (what coverage.json records), or with locations. A source clang
    cannot parse with qpel's stand-in headers is {UNPARSED: ['parse at <first error>']}, kernels unknown."""
    try:
        found = kernels(survey(entry))
    except clang_ast.SourceError as error:
        first = next((line for line in str(error).splitlines() if 'error:' in line), str(error).splitlines()[-1])
        found = {UNPARSED: ['parse at ' + first.split('error: ', 1)[-1][:200]]}
    for kernel in [k for k, blockers in found.items() if not blockers and k != UNPARSED]:
        problem = validation_problem(entry, kernel)
        if problem:
            found[kernel] = [f'invalid at {kernel}:0 ({problem})']
    if entry.get('shared'):  # lengths for shared memory the launch sizes: such a kernel counts when it validates with them
        for kernel in [k for k, blockers in found.items() if blockers and k != UNPARSED
                       and {b.split(' ')[0] for b in blockers} == {'dynamic-shared'}]:
            lengths = survey_lengths(entry, kernel)
            if lengths is None:
                continue
            problem = validation_problem(entry, kernel, shared=lengths)
            if problem != 'dynamic-shared':  # (a length the entry does not give: it stays as it is)
                found[kernel] = [f'invalid at {kernel}:0 ({problem})'] if problem else []
    if details:
        return found
    return {k: sorted({b.split(' ')[0] for b in blockers}) for k, blockers in found.items()}


def _arithmetic(text, names):
    """The value of an integer expression of + - * // and parentheses over `names`, or None."""
    import ast

    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv):
            left, right = value(node.left), value(node.right)
            if left is None or right is None or isinstance(node.op, ast.FloorDiv) and right == 0:
                return None
            return {ast.Add: left + right, ast.Sub: left - right, ast.Mult: left * right,
                    ast.FloorDiv: left // (right or 1)}[type(node.op)]
        return None
    return value(ast.parse(text, mode='eval').body)


def survey_lengths(entry, kernel):
    """The lengths an entry gives (`shared`) for one kernel's launch-sized shared memory, or None. A length
    is a number or an expression in the template arguments `kernel_args` (a regular expression with named
    groups) reads from the kernel's name (gsplat's `"rgbs_batch": "256*CDIM"`), the length the original's
    host passes at that instantiation; a name whose expression needs arguments the name lacks is left out."""
    found = re.search(entry['kernel_args'], kernel) if entry.get('kernel_args') else None
    names = {k: int(v) for k, v in found.groupdict().items()} if found else {}
    out = {}
    for name, length in entry['shared'].items():
        value = length if isinstance(length, int) else _arithmetic(length, names)
        if value is not None:
            out[name] = value
    return out or None


def validation_problem(entry, kernel, shared=None):
    """None when the kernel, imported on its own with the plan qpel makes for it, validates as a whole program;
    else what stopped it (the import's blocker code, or the validator's). A kernel counts as importable only
    then: the survey reads every function, validation checks the program they make. `shared` gives the lengths
    of shared memory the launch sizes (the entry's `shared`)."""
    from .c_import import import_source
    from qpel_ir import validate
    try:
        program, _ = import_source(CORPUS/entry['source'], include=[CORPUS/f for f in entry.get('include', ())],
                                   kernels=[kernel], module='survey', shared=shared, defines=build_defines(entry))
        validate(program)
    except Unsupported as error:
        return error.code
    except (ValueError, KeyError, TypeError) as error:
        found = re.search(r'\) ([A-Z_]+):', str(error))
        return found.group(1) if found else type(error).__name__
    return None


def source_details(entry):
    return source_coverage(entry, details=True)


def coverage(details=False, dialect=None):
    """{source: {kernel: blocker codes}} for the whole corpus (with details, 'code at function:line');
    sources are surveyed in parallel. dialect ('cuda' or 'metal') limits it to that dialect's sources."""
    listed = [e for e in entries() if not dialect or clang_ast.dialect_of(CORPUS/e['source']) == dialect]
    with ProcessPoolExecutor(SURVEY_WORKERS, max_tasks_per_child=1) as pool:
        found = list(pool.map(source_details if details else source_coverage, listed))
    return {entry['source']: result for entry, result in zip(listed, found)}


def per_repository(result):
    """{repository (the source's first folder): (importable kernels, kernels)}, the headline numbers."""
    out = {}
    for source, found in result.items():
        ok, count = out.get(source.split('/')[0], (0, 0))
        found = {k: b for k, b in found.items() if k != UNPARSED}  # its kernels are not known
        out[source.split('/')[0]] = (ok + sum(not b for b in found.values()), count + len(found))
    return out


def unparsed(result):
    """{repository: sources clang could not parse}."""
    out = Counter()
    for source, found in result.items():
        out[source.split('/')[0]] += UNPARSED in found
    return out


def report(result):
    lines = []
    total = importable = 0
    blocked_by = Counter()
    for source, found in result.items():
        lines.append(source)
        for kernel, blockers in found.items():
            if kernel == UNPARSED:
                lines.append(f'  does not parse: {blockers[0][9:] if blockers[0].startswith("parse at ") else blockers[0]}')
                continue
            total += 1
            importable += not blockers
            codes = sorted({b.split(' ')[0] for b in blockers})  # works for codes and for details
            blocked_by.update(codes)
            lines.append(f'  {"ok     " if not blockers else "blocked"} {kernel}' + (f': {", ".join(codes)}' if codes else ''))
    lines.append(f'{importable} of {total} kernels importable')
    lines.append('per repository:')
    for repo, (ok, count) in sorted(per_repository(result).items()):
        failed = unparsed(result)[repo]
        lines.append(f'  {repo:16} {ok:4} of {count:4}' + (f'  ({failed} sources do not parse)' if failed else ''))
    sized = sum(1 for found in result.values() for blockers in found.values()
                if blockers and {b.split(' ')[0] for b in blockers} == {'dynamic-shared'})
    if sized:
        lines.append(f'{sized} more need only the length of their threadgroup memory (shared={{...}})')
    if blocked_by:
        lines.append('blockers, by number of kernels they block:')
        lines.extend(f'  {count:3} {code}' for code, count in blocked_by.most_common())
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--update', action='store_true', help='rewrite corpus/coverage.json')
    parser.add_argument('--details', action='store_true', help='list every blocker with its location')
    parser.add_argument('--messages', action='store_true', help='rank blocker messages over the whole corpus')
    parser.add_argument('--check', action='store_true', help='fail when the result differs from coverage.json')
    parser.add_argument('--dialect', choices=('cuda', 'metal'),
                        help='only this dialect\'s sources (Metal needs Xcode; CI on Linux checks CUDA)')
    parser.add_argument('--fixtures', action='store_true',
                        help='write the fixture of every corpus program (one kernel imported on its own)')
    args = parser.parse_args(argv)
    if args.fixtures:
        from .c_import import import_source
        fixtures = HERE/'tests/fixtures/qpel_ir'
        for entry in entries():
            for program in entry.get('programs', ()):
                from .roundtrip import program_plan
                imported, _ = import_source(CORPUS/entry['source'], include=[CORPUS/f for f in entry.get('include', ())],
                                            kernels=program['kernels'], module=program['fixture'], plan=program_plan(program),
                                            constants=program.get('constants'), geometry=program.get('geometry'),
                                            shared=program.get('shared'),
                                            resources=program.get('resources'), defines=build_defines(entry))
                folder = fixtures/program['fixture']
                folder.mkdir(exist_ok=True)
                (folder/'program.qpel.json').write_text(json.dumps(imported, indent=2) + '\n')
                print('wrote', folder.relative_to(HERE))
        return 0
    if args.messages:
        with ProcessPoolExecutor(SURVEY_WORKERS, max_tasks_per_child=1) as pool:
            total = sum(pool.map(source_messages, entries()), Counter())
        for (code, message), count in total.most_common():
            print(f'{count:5} {code:22} {message}')
        return 0
    result = coverage(details=args.details, dialect=args.dialect)
    print(report(result))
    if args.details and (args.check or args.update):
        result = {source: {k: sorted({b.split(' ')[0] for b in v}) for k, v in found.items()} for source, found in result.items()}
    recorded = json.loads(COVERAGE.read_text())
    if args.dialect:
        if args.update:
            parser.error('--update records the whole corpus; run it without --dialect')
        recorded = {source: found for source, found in recorded.items() if source in result}
    if args.check and result != recorded:
        print('corpus coverage changed; check it, then run python3 -m frontends.corpus --update')
        return 1
    if args.details:
        for source, found in result.items():
            for kernel, blockers in found.items():
                for blocker in blockers:
                    print(f'{source} {kernel}: {blocker}')
    if args.update:
        COVERAGE.write_text(json.dumps(result, indent=1, separators=(',', ':')) + '\n')
        print('wrote', COVERAGE.relative_to(HERE))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
