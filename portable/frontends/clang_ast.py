"""Run clang on a CUDA or Metal source and return its JSON AST, offline.

CUDA is parsed host-only against the small stub headers in include/, so no
CUDA SDK is needed. Metal is parsed with Xcode's `xcrun metal`. Neither run
generates code; the AST is only read.
"""
import json
from pathlib import Path
import re
import subprocess
import tempfile

INCLUDE = Path(__file__).resolve().parent/'include'
DIALECTS = {'.cu': 'cuda', '.cuh': 'cuda', '.metal': 'metal'}


class SourceError(ValueError):
    """The source could not be parsed by clang at all."""


def command(path, dialect, include=()):
    extra = [arg for folder in include for arg in ('-I', str(folder))]
    if dialect == 'cuda':
        return ['clang', '-std=c++17', '-x', 'cuda', '--cuda-host-only', '-nocudainc', '-nocudalib',
                '-I', str(INCLUDE), *extra, '-include', 'cuda_runtime.h',  # nvcc includes it implicitly
                '-fsyntax-only', '-Xclang', '-ast-dump=json', str(path)]
    if dialect == 'metal':
        return ['xcrun', 'metal', '-std=metal3.1', *extra, '-fsyntax-only', '-Xclang', '-ast-dump=json', str(path)]
    raise ValueError('Unknown dialect: ' + str(dialect))


def dialect_of(path):
    try:
        return DIALECTS[Path(path).suffix]
    except KeyError:
        raise ValueError(f'Cannot tell the dialect of {path}; use .cu or .metal') from None


# Declarations from these folders (the C++ standard library, the macOS SDK, the
# Metal toolchain) are never imported, and are most of the AST of a real file:
# llama.cpp's headers pull in <vector>, <unordered_map>, ... (~600 MB of JSON).
SYSTEM = ('/Applications/Xcode', '/Library/Developer/', '/usr/include/', '/usr/lib/', '/opt/homebrew/')
FILE = re.compile(r'"file": "((?:[^"\\]|\\.)*)"')
LINE = re.compile(r'"line": (\d+)')
INCLUDED_FROM = re.compile(r'"includedFrom": \{[^{}]*\}')


def is_system(file):
    return file is not None and file.startswith(SYSTEM)


def last_location(text, state):
    """Update state {'file', 'line'} to the last location written in `text` (clang's JSON order)."""
    text = INCLUDED_FROM.sub('', text)
    events = [(m.start(), 'file', json.loads('"' + m.group(1) + '"')) for m in FILE.finditer(text)]
    events += [(m.start(), 'line', int(m.group(1))) for m in LINE.finditer(text)]
    for _, key, value in sorted(events):
        state[key] = value


def read_unit(stream):
    """The translation unit from clang's JSON dump, without system-header declarations.

    clang prints one top-level declaration per 4-space-indented block, and writes a
    location's file and line only when they change, so each kept declaration gets
    the file and line in effect before it ('_seed', used by annotate)."""
    head, decls, chunk, state, started = [], [], None, {'file': None, 'line': None}, False
    for line in stream:
        if chunk is None:
            if line == '    {\n':
                chunk, started = [line], True
            elif not started:
                head.append(line)
            continue
        chunk.append(line)
        if line in ('    },\n', '    }\n'):
            text = ''.join(chunk)
            before = dict(state)
            at = dict(state)  # where the declaration itself is: its "loc", written before its "range"
            last_location(text[:text.find('"range"')], at)
            last_location(text, state)
            if not is_system(at['file']):
                decl = json.loads(text.rstrip().rstrip(','))
                decl['_seed'] = before
                decls.append(decl)
            chunk = None
    unit = json.loads(re.sub(r',\s*"inner": \[\s*$', '', ''.join(head)) + '}')
    unit['inner'] = decls
    return unit


_CACHE = {}  # (path, mtime, dialect, include) -> (unit, text); the importer never changes a unit
_CACHE_SIZE = 4


def parse(path, dialect, include=()):
    """Return (translation unit, source text). Locations are annotated, see annotate().

    `include` lists extra header folders, such as stubs for a library the source includes.
    Declarations from system headers are left out (see SYSTEM). The last few results are
    kept: importing several kernels of one library file parses it once."""
    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime_ns, dialect, tuple(str(Path(f).resolve()) for f in include))
    if key in _CACHE:
        return _CACHE[key]
    result = _parse(path, dialect, include)
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def _parse(path, dialect, include):
    with tempfile.TemporaryFile('w+') as errors:
        process = subprocess.Popen(command(path, dialect, include), stdout=subprocess.PIPE, stderr=errors, text=True,
                                   bufsize=1 << 20)
        with process.stdout:
            try:
                unit = read_unit(process.stdout)
            except json.JSONDecodeError:
                unit = None
        process.wait()
        errors.seek(0)
        stderr = errors.read()
    if process.returncode != 0 or unit is None:
        lines = [line for line in stderr.splitlines() if 'error:' in line]
        raise SourceError(f'{path}: clang rejected the source:\n' + '\n'.join(lines[:10] or [stderr[-2000:]]))
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
        if '_seed' in node:  # first kept declaration after left-out ones, see read_unit()
            state.update(node.pop('_seed'))
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


def span(node):
    """[start, end) byte offsets of a node in its file, after macro expansion."""
    def offset(loc):
        loc = loc.get('expansionLoc', loc)
        return loc['offset'], loc.get('tokLen', 0)
    start, _ = offset(node['range']['begin'])
    end, length = offset(node['range']['end'])
    return [start, end + length]


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
