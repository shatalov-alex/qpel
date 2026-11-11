"""Run clang on a CUDA or Metal source and return its JSON AST, offline.

CUDA is parsed host-only against the small stub headers in include/, so no
CUDA SDK is needed. Metal is parsed with Xcode's `xcrun metal`. Neither run
generates code; the AST is only read.
"""
import json
from pathlib import Path
import subprocess

INCLUDE = Path(__file__).resolve().parent/'include'
DIALECTS = {'.cu': 'cuda', '.cuh': 'cuda', '.metal': 'metal'}


class SourceError(ValueError):
    """The source could not be parsed by clang at all."""


def command(path, dialect):
    if dialect == 'cuda':
        return ['clang', '-x', 'cuda', '--cuda-host-only', '-nocudainc', '-nocudalib',
                '-I', str(INCLUDE), '-include', 'cuda_runtime.h',  # nvcc includes it implicitly
                '-fsyntax-only', '-Xclang', '-ast-dump=json', str(path)]
    if dialect == 'metal':
        return ['xcrun', 'metal', '-std=metal3.0', '-fsyntax-only', '-Xclang', '-ast-dump=json', str(path)]
    raise ValueError('Unknown dialect: ' + str(dialect))


def dialect_of(path):
    try:
        return DIALECTS[Path(path).suffix]
    except KeyError:
        raise ValueError(f'Cannot tell the dialect of {path}; use .cu or .metal') from None


def parse(path, dialect):
    """Return (translation unit, source text). Locations are annotated, see locate()."""
    path = Path(path)
    result = subprocess.run(command(path, dialect), capture_output=True, text=True)
    if result.returncode != 0:
        errors = [line for line in result.stderr.splitlines() if 'error:' in line]
        raise SourceError(f'{path}: clang rejected the source:\n' + '\n'.join(errors[:10] or [result.stderr[-2000:]]))
    unit = json.loads(result.stdout)
    annotate(unit)
    return unit, path.read_text()


def annotate(unit):
    """Fill in the file/line that clang's JSON omits when unchanged since the last location.

    clang writes each location relative to the previously written one, in document
    order, so a single ordered pass restores them. Every location object gets
    '_file' and '_line'.
    """
    state = {'file': None, 'line': None}

    def point(loc):
        if 'file' in loc:
            state['file'] = loc['file']
        if 'line' in loc:
            state['line'] = loc['line']
        loc['_file'], loc['_line'] = state['file'], state['line']

    def location(loc):
        if not isinstance(loc, dict) or not loc:
            return
        if 'spellingLoc' in loc:
            point(loc['spellingLoc'])
            point(loc['expansionLoc'])
        else:
            point(loc)

    def visit(node):
        location(node.get('loc'))
        r = node.get('range')
        if r:
            location(r.get('begin'))
            location(r.get('end'))
        for child in node.get('inner', ()):
            visit(child)

    visit(unit)


def position(node):
    """(file, line, column) where the node starts, after macro expansion."""
    loc = node.get('range', {}).get('begin') or node.get('loc') or {}
    loc = loc.get('expansionLoc', loc)
    return loc.get('_file'), loc.get('_line'), loc.get('col')


def main_file_decls(unit, path):
    """Top-level declarations written in `path` itself (not in included headers)."""
    target = str(Path(path))
    for node in unit.get('inner', ()):
        if node.get('isImplicit'):
            continue
        loc = node.get('loc', {})
        loc = loc.get('expansionLoc', loc)
        if loc.get('_file') == target and 'includedFrom' not in loc:
            yield node
