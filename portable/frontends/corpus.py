"""Coverage of real GPU sources: which kernels qpel can import, and what blocks the rest.

The corpus (corpus/corpus.json) lists unmodified third-party sources. Each one is
read in survey mode, which records every construct the importer cannot map instead
of stopping at the first. A kernel is importable when it and every device function
it calls have no blockers.

corpus/coverage.json records the current result; tests/test_corpus.py fails when
the result changes, so progress (or a regression) is always a visible diff:

    python3 -m frontends.corpus            # print the report
    python3 -m frontends.corpus --update   # rewrite coverage.json after a change
"""
import argparse
from collections import Counter
import json
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


def coverage():
    return {entry['source']: kernels(survey(entry)) for entry in entries()}


def report(result):
    lines = []
    total = importable = 0
    blocked_by = Counter()
    for source, found in result.items():
        lines.append(source)
        for kernel, blockers in found.items():
            total += 1
            importable += not blockers
            codes = sorted({b.split(' ')[0] for b in blockers})
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
    args = parser.parse_args(argv)
    result = coverage()
    print(report(result))
    if args.details:
        for source, found in result.items():
            for kernel, blockers in found.items():
                for blocker in blockers:
                    print(f'{source} {kernel}: {blocker}')
    if args.update:
        COVERAGE.write_text(json.dumps(result, indent=2) + '\n')
        print('wrote', COVERAGE.relative_to(HERE))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
