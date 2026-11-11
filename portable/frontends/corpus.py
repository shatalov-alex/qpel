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
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
import re
from pathlib import Path

from . import clang_ast
from .c_import import Importer

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE/'corpus'
COVERAGE = CORPUS/'coverage.json'


def entries():
    return json.loads((CORPUS/'corpus.json').read_text())


def survey(entry):
    """{function: {'kind', 'issues', 'calls'}} for one corpus source."""
    path = CORPUS/entry['source']
    dialect = clang_ast.dialect_of(path)
    unit, source = clang_ast.parse(path, dialect, [CORPUS/folder for folder in entry.get('include', ())])
    return Importer(unit, source, path, dialect, path.stem, survey=True).survey()


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
    for info in survey(entry).values():
        for issue in info['issues']:
            found[(issue['code'], re.sub(r'\b\d+\b', 'N', issue['message']))] += 1
    return found


def source_coverage(entry, details=False):
    """{kernel: blockers}: their codes (what coverage.json records), or with locations."""
    found = kernels(survey(entry))
    if details:
        return found
    return {k: sorted({b.split(' ')[0] for b in blockers}) for k, blockers in found.items()}


def source_details(entry):
    return source_coverage(entry, details=True)


def coverage(details=False):
    """{source: {kernel: blocker codes}} for the whole corpus (with details, 'code at function:line');
    sources are surveyed in parallel."""
    listed = entries()
    with ProcessPoolExecutor() as pool:
        found = list(pool.map(source_details if details else source_coverage, listed))
    return {entry['source']: result for entry, result in zip(listed, found)}


def report(result):
    lines = []
    total = importable = 0
    blocked_by = Counter()
    for source, found in result.items():
        lines.append(source)
        for kernel, blockers in found.items():
            total += 1
            importable += not blockers
            codes = sorted({b.split(' ')[0] for b in blockers})  # works for codes and for details
            blocked_by.update(codes)
            lines.append(f'  {"ok     " if not blockers else "blocked"} {kernel}' + (f': {", ".join(codes)}' if codes else ''))
    lines.append(f'{importable} of {total} kernels importable')
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
    args = parser.parse_args(argv)
    if args.messages:
        with ProcessPoolExecutor() as pool:
            total = sum(pool.map(source_messages, entries()), Counter())
        for (code, message), count in total.most_common():
            print(f'{count:5} {code:22} {message}')
        return 0
    result = coverage(details=args.details)
    print(report(result))
    if args.details and (args.check or args.update):
        result = {source: {k: sorted({b.split(' ')[0] for b in v}) for k, v in found.items()} for source, found in result.items()}
    if args.check and result != json.loads(COVERAGE.read_text()):
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
