"""Round-trip matrix: qpel -> CUDA/Metal -> qpel, and CUDA/Metal sources -> qpel.

Two programs are equal when their trees match after normalising spelling that
carries no meaning: node ids, literal suffixes (7 vs 7u), float spelling (0.5 vs
5E-1) and attributes left at their defaults.

CUDA -> Metal and Metal -> CUDA follow from these rows: a source that imports to
a fixture's IR lowers to exactly that fixture's CUDA and Metal, which the nvcc,
Metal and Apple GPU checks cover.
"""
import json
from pathlib import Path
import struct
import tempfile

from .c_import import import_source

HERE = Path(__file__).resolve().parents[1]
FIXTURES = HERE/'tests/fixtures/qpel_ir'
# Hand-written sources, each with the fixture it must import to.
EXAMPLES = {'threshold': 'import', 'classify_bytes': 'classify', 'combine_masks': 'masks', 'masked_copy': 'count',
            'signed_delta': 'signed_delta', 'scale_offset': 'scale_offset', 'box_sum': 'box_sum'}
SOURCE_DIRS = {'cuda': HERE/'cuda_examples', 'metal': HERE/'metal_examples'}
CORPUS = HERE/'corpus'  # unmodified real sources; entries with a fixture must import to it


def corpus_entries():
    return json.loads((CORPUS/'corpus.json').read_text())
SUFFIX = {'cuda': '.cu', 'metal': '.metal'}


def canonical(program):
    nodes = {n['id']: n for n in program['nodes']}

    def label(ident):
        n = nodes[ident]
        return n['attributes'].get('name', n['kind'])

    def tree(ident):
        n = nodes[ident]
        a = dict(n['attributes'])
        if n['kind'] == 'Literal' and a['value'] not in ('true', 'false'):
            a['value'] = int(a['value'].rstrip('uU'))
        if n['kind'] == 'F32Literal':
            a['value'] = struct.unpack('<I', struct.pack('<f', float(a['value'])))[0]
        if n['kind'] == 'Function':
            a.setdefault('isKernel', False)
        if n['kind'] == 'Parameter':
            a.setdefault('isBuffer', False)
            a.setdefault('binding', 0)
        return [n['kind'], a, {r: label(t) for r, t in sorted(n['links'].items())},
                {r: [tree(c) for c in cs] for r, cs in sorted(n['children'].items())}]
    return {k: tree(v) for k, v in sorted(program['roots'].items())}


def difference(expected, actual):
    """First path where two programs differ, or None."""
    def walk(a, b, path):
        if type(a) is not type(b):
            return f'{path}: {a!r} != {b!r}'
        if isinstance(a, dict):
            for k in sorted(set(a) | set(b), key=str):
                found = walk(a.get(k), b.get(k), f'{path}/{k}')
                if found:
                    return found
            return None
        if isinstance(a, list):
            if len(a) != len(b):
                return f'{path}: {len(a)} items != {len(b)} items'
            for i, (x, y) in enumerate(zip(a, b)):
                found = walk(x, y, f'{path}[{i}]')
                if found:
                    return found
            return None
        return None if a == b else f'{path}: {a!r} != {b!r}'
    return walk(canonical(expected), canonical(actual), '')


def fixture(name):
    return json.loads((FIXTURES/name/'program.qpel.json').read_text())


def rows(fixtures=None):
    """Yield (row label, case, error or None) for the whole matrix."""
    from qpel import compile_program
    from qpel_ir import Lowering, validate
    names = fixtures or sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
    with tempfile.TemporaryDirectory(prefix='qpel-roundtrip-') as work:
        for name in names:
            original = fixture(name)
            compile_program(FIXTURES/name/'program.qpel.json', Path(work)/name)
            generated = Path(work)/name/'package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            for dialect in ('cuda', 'metal'):
                source = next(generated.glob('*' + SUFFIX[dialect]))
                yield f'qpel -> {dialect} -> qpel', name, check(original, source, plan, validate)
    for source_name, name in EXAMPLES.items():
        original = fixture(name)
        plan = Lowering(original).plan()
        for dialect in ('cuda', 'metal'):
            source = SOURCE_DIRS[dialect]/(source_name + SUFFIX[dialect])
            yield f'{dialect} -> qpel', source.name, check(original, source, plan, validate)
    for entry in corpus_entries():
        if 'fixture' not in entry:
            continue
        source = CORPUS/entry['source']
        plan = json.loads((CORPUS/entry['plan']).read_text())
        include = [CORPUS/folder for folder in entry.get('include', ())]
        dialect = 'cuda' if source.suffix == '.cu' else 'metal'
        yield f'{dialect} -> qpel', 'corpus/' + entry['source'], check(fixture(entry['fixture']), source, plan, validate, include)


def reimport(program):
    """Build any program to CUDA and Metal and import both back: {dialect: difference or None}."""
    from qpel import compile_program
    from qpel_ir import validate
    results = {}
    with tempfile.TemporaryDirectory(prefix='qpel-reimport-') as work:
        saved = Path(work)/'program.qpel.json'
        saved.write_text(json.dumps(program))
        compile_program(saved, Path(work)/'out')
        generated = Path(work)/'out/package/generated'
        plan = json.loads((generated/'pipeline.json').read_text())
        for dialect in ('cuda', 'metal'):
            results[dialect] = check(program, next(generated.glob('*' + SUFFIX[dialect])), plan, validate)
    return results


def check(original, source, plan, validate, include=()):
    try:
        program, _ = import_source(source, plan=plan, include=include)
        validate(program)
    except ValueError as error:
        return str(error)
    return difference(original, program)


def main():
    failures = 0
    for row, case, error in rows():
        failures += error is not None
        print(('FAIL ' if error else 'PASS ') + f'{row:22} {case}' + (f'\n     {error}' if error else ''))
    print(f'{"all round trips passed" if not failures else f"{failures} round trips failed"}')
    return failures


if __name__ == '__main__':
    raise SystemExit(1 if main() else 0)
