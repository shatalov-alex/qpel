"""Run clang on a CUDA or Metal source and return its JSON AST, offline.

CUDA is parsed host-only against the small stub headers in include/, so no
CUDA SDK is needed. Metal is parsed with Xcode's `xcrun metal`. Neither run
generates code; the AST is only read.
"""
import functools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

INCLUDE = Path(__file__).resolve().parent/'include'
DIALECTS = {'.cu': 'cuda', '.cuh': 'cuda', '.metal': 'metal'}


class SourceError(ValueError):
    """The source could not be parsed by clang at all."""


@functools.lru_cache(maxsize=None)
def metal_available():
    """True when Xcode's Metal compiler can be run (macOS with the Metal toolchain). Without it,
    Metal sources cannot be imported; the checks that need them report themselves skipped."""
    if not shutil.which('xcrun'):
        return False
    return subprocess.run(['xcrun', '--find', 'metal'], capture_output=True).returncode == 0


def flags(defines):
    """Compiler flags for a source's build settings: macros (`NAME` or `NAME=value`) and a language
    standard written `-std=c++20` (a corpus entry's "standard"), which overrides the default."""
    return [d if d.startswith('-std=') else '-D' + d for d in defines]


# C++23 sources are parsed with LLVM's own clang (20 or later), not Xcode's: gsplat's Lidars.cuh reads a
# static member through a reference in a constant expression (`constexpr float k = 1.f /
# lidar.ANGLE_TO_PIXEL_SCALING_FACTOR`), which P2280 allows and nvcc accepts; LLVM's clang implements
# P2280 in C++23 mode, Apple clang 17 not at all. QPEL_LLVM_CLANG names it, else Homebrew's llvm.
MODERN = ('-std=c++23', '-std=c++2b', '-std=c++26', '-std=c++2c')
LLVM_CLANG_CANDIDATES = ('/opt/homebrew/opt/llvm/bin/clang', '/usr/local/opt/llvm/bin/clang')


@functools.lru_cache(maxsize=None)
def llvm_clang():
    """LLVM's clang (version 20 or later) for C++23 sources, or None."""
    for candidate in filter(None, (os.environ.get('QPEL_LLVM_CLANG'), *LLVM_CLANG_CANDIDATES)):
        if not os.access(candidate, os.X_OK):
            continue
        version = re.search(r'clang version (\d+)', subprocess.run([candidate, '--version'], capture_output=True,
                                                                     text=True).stdout)
        if version and int(version.group(1)) >= 20:
            return candidate
    return None


def compiler(extra):
    if any(flag in MODERN for flag in extra):
        found = llvm_clang()
        if found is None:
            raise SourceError('a C++23 source needs LLVM clang 20 or later (brew install llvm, or QPEL_LLVM_CLANG)')
        return found
    return 'clang'


def command(path, dialect, include=(), defines=()):
    extra = [arg for folder in include for arg in ('-I', str(folder))] + flags(defines)
    if dialect == 'cuda':
        return [compiler(extra), '-std=c++17', '-x', 'cuda', '--cuda-host-only', '-nocudainc', '-nocudalib',
                '-I', str(INCLUDE), '-I', str(INCLUDE/'libraries'), '-I', str(INCLUDE/'qpel_cub'), *extra,
                # clang's cuda_wrappers/new uses ::malloc/::free, which -nocudainc no longer declares;
                # libc++'s <new> happens to (macOS), libstdc++'s does not (Linux).
                '-include', 'stdlib.h',
                '-include', 'cuda_runtime.h',  # nvcc includes it implicitly
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


@functools.lru_cache(maxsize=4096)
def is_system(file):
    # Linux clang names libstdc++ headers '/usr/bin/../lib/gcc/.../../../../include/c++/13/new':
    # normalise before comparing prefixes.
    return file is not None and (file.startswith(SYSTEM) or ('..' in file and os.path.normpath(file).startswith(SYSTEM)))


def last_location(text, state):
    """Update state {'file', 'line'} to the last location written in `text` (clang's JSON order)."""
    text = INCLUDED_FROM.sub('', text)
    events = [(m.start(), 'file', json.loads('"' + m.group(1) + '"')) for m in FILE.finditer(text)]
    events += [(m.start(), 'line', int(m.group(1))) for m in LINE.finditer(text)]
    for _, key, value in sorted(events):
        state[key] = value


DECLARATION_LIMIT = int(os.environ.get('QPEL_AST_DECLARATION_MB', 1024)) << 20  # JSON one top-level declaration may have
UNIT_LIMIT = int(os.environ.get('QPEL_AST_UNIT_MB', 1024)) << 20  # kept JSON for a whole source (about 10x in Python)


TRAIT_NAMES = ('is_same_v',)  # std:: variable templates whose instances qpel evaluates (statics.constant_values)


def system_traits(text):
    """The instances of std::is_same_v (TRAIT_NAMES) in a system declaration's JSON, which read_unit
    leaves out with the rest of the system headers: {'id', 'name', 'arguments': [type spellings]}."""
    if not any(f'"name": "{name}"' in text for name in TRAIT_NAMES):
        return []
    lines, found = text.split('\n'), []
    for i, line in enumerate(lines):
        if line.strip() != '"kind": "VarTemplateSpecializationDecl",':
            continue
        indent = len(line) - len(line.lstrip()) - 2
        start = next((j for j in range(i - 1, -1, -1) if lines[j] == ' ' * indent + '{'), None)
        end = next((j for j in range(i + 1, len(lines)) if lines[j].rstrip(',') == ' ' * indent + '}'), None)
        if start is None or end is None:
            continue
        decl = json.loads('\n'.join(lines[start:end]) + '\n' + ' ' * indent + '}')
        if decl.get('name') not in TRAIT_NAMES:
            continue
        arguments = [c['type'].get('desugaredQualType', c['type'].get('qualType', '')) for c in decl.get('inner', ())
                     if c.get('kind') == 'TemplateArgument' and 'type' in c]
        found.append({'id': decl.get('id'), 'name': decl['name'], 'arguments': arguments})
    return found


STATIC_VALUE_CLASSES = ('integral_constant', 'is_same', 'bool_constant')  # whose static `value` qpel reads


def system_values(text):
    """{declaration id: bool or int} of the static constexpr `value` members in a system declaration's JSON
    (metal::is_same<A, B>::value, an integral_constant<bool, b>), which read_unit leaves out."""
    if not any(f'"name": "{name}"' in text for name in STATIC_VALUE_CLASSES):
        return {}
    lines, found = text.split('\n'), {}
    for i, line in enumerate(lines):
        if line.strip() != '"kind": "VarDecl",' or i + 1 >= len(lines):
            continue
        indent = len(line) - len(line.lstrip()) - 2
        start = next((j for j in range(i - 1, -1, -1) if lines[j] == ' ' * indent + '{'), None)
        end = next((j for j in range(i + 1, len(lines)) if lines[j].rstrip(',') == ' ' * indent + '}'), None)
        if start is None or end is None or end - start > 400:
            continue
        try:
            decl = json.loads('\n'.join(lines[start:end]) + '\n' + ' ' * indent + '}')
        except ValueError:
            continue
        if decl.get('name') != 'value' or not decl.get('constexpr') or not decl.get('inner'):
            continue
        init = decl['inner'][-1]
        while init.get('kind') in ('ImplicitCastExpr', 'SubstNonTypeTemplateParmExpr', 'ParenExpr', 'ConstantExpr') and init.get('inner'):
            init = init['inner'][-1]
        if init.get('kind') == 'CXXBoolLiteralExpr':
            found[decl['id']] = bool(init.get('value'))
        elif init.get('kind') == 'IntegerLiteral':
            found[decl['id']] = int(init['value'])
    return found


def read_unit(stream):
    """The translation unit from clang's JSON dump, without system-header declarations.

    clang prints one top-level declaration per 4-space-indented block, and writes a
    location's file and line only when they change, so each kept declaration gets
    the file and line in effect before it ('_seed', used by annotate).

    Memory is bounded: a declaration whose JSON passes DECLARATION_LIMIT is read past without
    being kept and named in unit['_skipped'] (mamba's host launcher: nested BOOL_SWITCH lambdas
    make 100 GB of JSON for one function), and a source whose kept JSON passes UNIT_LIMIT is
    refused (SourceError), so that importing never takes the machine's memory."""
    head, decls, chunk, state, started = [], [], None, {'file': None, 'line': None}, False
    size, kept, skipped, oversized, kept_traits, kept_values = 0, 0, [], None, [], {}
    for line in stream:
        if chunk is None and oversized is None:
            if line == '    {\n':
                chunk, started, size = [line], True, len(line)
            elif not started:
                head.append(line)
            continue
        if oversized is not None:  # streamed past: only the locations it writes are followed
            last_location(line, state)
            if oversized['name'] is None and line.startswith('      "name": '):
                oversized['name'] = line.split('"')[3]
            if line in ('    },\n', '    }\n'):
                skipped.append({'name': oversized['name'] or '?', 'kernel': oversized['kernel'] or '"kind": "CUDAGlobalAttr"' in line,
                                'system': is_system(oversized['file'])})
                oversized = None
            continue
        chunk.append(line)
        size += len(line)
        if size > DECLARATION_LIMIT:
            text = ''.join(chunk)
            name = re.search(r'^      "name": "([^"]*)"', text, re.M)
            last_location(text, state)
            oversized = {'name': name.group(1) if name else None, 'kernel': '"kind": "CUDAGlobalAttr"' in text,
                         'file': state['file']}
            chunk = None
            continue
        if line in ('    },\n', '    }\n'):
            text = ''.join(chunk)
            before = dict(state)
            at = dict(state)  # where the declaration itself is: its "loc", written before its "range"
            last_location(text[:text.find('"range"')], at)
            last_location(text, state)
            if is_system(at['file']):
                kept_traits.extend(system_traits(text))
                kept_values.update(system_values(text))
            else:
                kept += len(text)
                if kept > UNIT_LIMIT:
                    raise SourceError(f'the source\'s AST passes {UNIT_LIMIT >> 20} MB of JSON; qpel does not read it '
                                      'whole (template instantiations multiply it)')
                decl = json.loads(text.rstrip().rstrip(','))
                decl['_seed'] = before
                decls.append(decl)
            chunk = None
    unit = json.loads(re.sub(r',\s*"inner": \[\s*$', '', ''.join(head)) + '}')
    unit['inner'] = decls
    unit['_skipped'] = skipped
    unit['_system_traits'] = kept_traits
    unit['_system_values'] = kept_values
    unit['_kept_bytes'] = kept
    return unit


_CACHE = {}  # (path, mtime, dialect, include) -> (unit, text); the importer never changes a unit
_CACHE_SIZE = 4


def parse(path, dialect, include=(), defines=()):
    """Return (translation unit, source text). Locations are annotated, see annotate().

    `include` lists extra header folders, such as stubs for a library the source includes.
    Declarations from system headers are left out (see SYSTEM). The last few results are
    kept: importing several kernels of one library file parses it once."""
    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime_ns, dialect, tuple(str(Path(f).resolve()) for f in include),
           tuple(defines))
    if key in _CACHE:
        return _CACHE[key]
    result = _parse(path, dialect, include, tuple(defines))
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def _parse(path, dialect, include, defines=()):
    with tempfile.TemporaryFile('w+') as errors:
        process = subprocess.Popen(command(path, dialect, include, defines), stdout=subprocess.PIPE, stderr=errors, text=True,
                                   bufsize=1 << 20)
        with process.stdout:
            try:
                unit = read_unit(process.stdout)
            except json.JSONDecodeError:
                unit = None
            except SourceError as error:  # too large to read: stop clang, say which source
                process.kill()
                process.wait()
                raise SourceError(f'{path}: {error}') from None
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
