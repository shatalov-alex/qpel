"""Round-trip matrix: qpel -> CUDA/Metal -> qpel, and CUDA/Metal sources -> qpel.

Two programs are equal when their trees match after normalising spelling that
carries no meaning: conversions that change neither a value nor the arithmetic done
with it, integer constants however they are spelled (-3, cast<u8>(255u)), node ids, literal suffixes (7 vs 7u), float spelling (0.5 vs
5E-1), attributes left at their defaults, and the IR version 1/2 spellings of
types, casts and literals (U32Type is ScalarType u32, I32Cast is Cast to i32,
I32Literal 3 is Constant i32 3).

CUDA -> Metal and Metal -> CUDA follow from these rows: a source that imports to
a fixture's IR lowers to exactly that fixture's CUDA and Metal, which the nvcc,
Metal and Apple GPU checks cover.
"""
import json
from pathlib import Path
import struct
import tempfile

from .c_import import import_source
from qpel_types import INTEGER_TERMS, LEGACY, LEGACY_CASTS, arithmetic, bits, fits, is_signed, join, term

TYPE_KINDS = set(LEGACY) | {'ScalarType', 'BufferType'}

HERE = Path(__file__).resolve().parents[1]
FIXTURES = HERE/'tests/fixtures/qpel_ir'
# Hand-written CUDA and Metal sources, each with the fixture it must import to.
EXAMPLES = {'threshold': 'import', 'classify_bytes': 'classify', 'combine_masks': 'masks', 'masked_copy': 'count',
            'signed_delta': 'signed_delta', 'scale_offset': 'scale_offset', 'box_sum': 'box_sum',
            'float_scale': 'float32', 'fma_bias': 'fma32', 'half_bias': 'half', 'window_sum': 'window_sum',
            'grid_stride': 'grid_stride', 'math_ops': 'math_ops', 'sort_bytes': 'sort_bytes'}
SOURCE_DIRS = {'cuda': HERE/'cuda_examples', 'metal': HERE/'metal_examples'}
CORPUS = HERE/'corpus'  # unmodified real sources; entries with a fixture must import to it


def corpus_entries():
    return json.loads((CORPUS/'corpus.json').read_text())


def corpus_programs():
    """(entry, program) for every corpus kernel pinned as its own fixture (llama.cpp's operations)."""
    for entry in corpus_entries():
        for program in entry.get('programs', ()):
            yield entry, program
SUFFIX = {'cuda': '.cu', 'metal': '.metal'}


def canonical(program):
    nodes = {n['id']: n for n in program['nodes']}
    types = expression_types(program)

    def no_op_cast(n, parent, role):
        """A conversion whose removal changes neither a value nor the type its parent computes in."""
        if n['kind'] not in LEGACY_CASTS and n['kind'] != 'Cast':
            return False
        to = LEGACY_CASTS.get(n['kind']) or n['attributes']['to']
        source = types.get(n['children']['operand'][0])
        if source == to:
            return True
        if source not in INTEGER_TERMS or to not in INTEGER_TERMS or not fits(to, source):
            return False  # only lossless integer widenings can be no-ops
        if parent is None:
            return False
        kind, op = parent['kind'], parent['attributes'].get('operator')
        if kind == 'Binary' and op in ('<<', '>>'):
            return role == 'right' or arithmetic(source) == arithmetic(to)  # the count's type never matters
        if kind in ('Binary', 'Minimum', 'Maximum') and op not in ('&&', '||'):
            other = types.get(parent['children']['right' if role == 'left' else 'left'][0])
            return other in INTEGER_TERMS and join(other, source) == join(other, to)
        if kind == 'Unary':
            return arithmetic(source) == arithmetic(to)
        if kind == 'Select':
            if role == 'condition':
                return True  # a condition only asks for zero
            other = types.get(parent['children']['no' if role == 'yes' else 'yes'][0])
            return other in INTEGER_TERMS and join(other, source) == join(other, to)
        if kind in ('Cast',) + tuple(LEGACY_CASTS):
            return True  # converting the same value again
        if kind in ('Variable', 'Assign', 'Return', 'Index', 'Call', 'Evaluate') or kind.startswith('Atomic'):
            return True  # the value is converted where it is used anyway
        return False

    def wrap(value, scalar):
        size, signed = bits(scalar), is_signed(scalar)
        value %= 2**size
        return value - 2**size if signed and value >= 2**(size - 1) else value

    def constant_of(ident):
        """(scalar, value) when the node computes a known integer: constants, casts of them,
        and + - * unary - ~ on them, with qpel's wrapping at the node's type."""
        n = nodes[ident]
        a = n['attributes']
        if n['kind'] in ('Literal', 'I32Literal', 'Constant') and a.get('value') not in ('true', 'false'):
            return a.get('scalar', 'i32' if n['kind'] == 'I32Literal' else 'u32'), int(a['value'].rstrip('uU'))
        t = types.get(ident)
        if t not in INTEGER_TERMS:
            return None
        if n['kind'] == 'Unary' and a['operator'] in ('-', '~'):
            inner = constant_of(n['children']['operand'][0])
            if inner:
                return t, wrap(-inner[1] if a['operator'] == '-' else ~inner[1], t)
        if n['kind'] == 'Binary' and a['operator'] in ('+', '-', '*'):
            left, right = (constant_of(n['children'][r][0]) for r in ('left', 'right'))
            if left and right:
                ops = {'+': left[1] + right[1], '-': left[1] - right[1], '*': left[1] * right[1]}
                return t, wrap(ops[a['operator']], t)
        if n['kind'] in LEGACY_CASTS or n['kind'] == 'Cast':
            inner = constant_of(n['children']['operand'][0])
            if inner:
                return t, wrap(inner[1], t)  # qpel's integer conversion keeps the low bits
        return None

    def effective(scalar, parent, role):
        """The type a constant is computed in where it is used: what its own type is spelled as
        does not matter when the operator joins it with a wider operand anyway."""
        if parent is None:
            return scalar
        kind, op = parent['kind'], parent['attributes'].get('operator')
        if kind == 'Binary' and op in ('<<', '>>') and role == 'right':
            return 'count'
        if kind in ('Binary', 'Minimum', 'Maximum') and op not in ('&&', '||', '<<', '>>'):
            sibling = parent['children']['right' if role == 'left' else 'left'][0]
            if op in ('==', '!=', '<', '>', '<=', '>=') and constant_of(sibling):
                return 'value'  # two constants compared: only their values matter
            other = types.get(sibling)
            joined = join(other, scalar) if other in INTEGER_TERMS else None
            return joined or scalar
        if kind == 'Select' and role in ('yes', 'no'):
            return types.get(parent['id'], scalar)
        if kind in ('Variable', 'Assign', 'Return', 'Index', 'Call') or kind.startswith('Atomic'):
            return 'value'
        return scalar

    def label(ident):
        n = nodes[ident]
        return n['attributes'].get('name', n['kind'])

    def tree(ident, parent=None, role=None):
        n = nodes[ident]
        a = dict(n['attributes'])
        kind = n['kind']
        if no_op_cast(n, parent, role):
            return tree(n['children']['operand'][0], parent, role)
        if kind in ('Cast', 'F32Cast') and (a.get('to') == 'f32' or kind == 'F32Cast'):
            inner = constant_of(n['children']['operand'][0])
            if inner:  # an integer constant converted to float is that float (rounded to nearest)
                return ['F32Literal', {'value': struct.unpack('<I', struct.pack('<f', float(inner[1])))[0]}, {}, {}]
        folded = constant_of(ident)
        if folded:
            return ['Constant', {'scalar': effective(folded[0], parent, role), 'value': folded[1]}, {}, {}]
        if kind == 'Binary' and a['operator'] == '-' and types.get(ident) in INTEGER_TERMS:
            zero = constant_of(n['children']['left'][0])
            if zero and zero[1] == 0:  # 0 - x is -x (both wrap)
                return ['Unary', {'operator': '-'}, {}, {'operand': [tree(n['children']['right'][0], n, 'operand')]}]
        if kind in TYPE_KINDS:  # U32Type and ScalarType{u32} are the same type
            return ['Type', {'term': term(n)}, {}, {}]
        if kind in LEGACY_CASTS:  # I32Cast is Cast{to: i32}
            kind, a = 'Cast', {'to': LEGACY_CASTS[kind]}
        if kind in ('Literal', 'I32Literal', 'Constant') and a['value'] not in ('true', 'false'):
            scalar = a.get('scalar', 'i32' if kind == 'I32Literal' else 'u32')
            return ['Constant', {'scalar': scalar, 'value': int(a['value'].rstrip('uU'))}, {}, {}]
        if n['kind'] == 'Literal' and a['value'] not in ('true', 'false'):
            a['value'] = int(a['value'].rstrip('uU'))
        if n['kind'] == 'F32Literal':
            a['value'] = struct.unpack('<I', struct.pack('<f', float(a['value'])))[0]
        if n['kind'] == 'Function':
            a.setdefault('isKernel', False)
        if n['kind'] == 'Parameter':
            a.setdefault('isBuffer', False)
            a.setdefault('binding', 0)
        return [kind, a, {r: label(t) for r, t in sorted(n['links'].items())},
                {r: [tree(c, n, r) for c in cs] for r, cs in sorted(n['children'].items())}]
    return {k: tree(v) for k, v in sorted(program['roots'].items())}


def expression_types(program):
    """{node id: type term} of every expression (empty when the program does not type-check)."""
    from qpel_types import TypeChecker
    checker = TypeChecker(program)
    try:
        checker.validate()
    except ValueError:
        return {}
    return checker.expression_types


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
        for port in entry.get('ports', ()):  # hand ports to the other language must give the same program
            path = HERE/port
            yield f'{"cuda" if path.suffix == ".cu" else "metal"} -> qpel', port, check(fixture(entry['fixture']), path, plan, validate)
    for entry, program in corpus_programs():
        source = CORPUS/entry['source']
        include = [CORPUS/folder for folder in entry.get('include', ())]
        dialect = 'cuda' if source.suffix == '.cu' else 'metal'
        yield (f'{dialect} -> qpel', f'corpus/{entry["source"]} {",".join(program["kernels"])}',
               check(fixture(program['fixture']), source, program_plan(program), validate, include, program['kernels'], program['fixture'],
                     program.get('constants'), program.get('geometry'), program.get('resources'), program.get('shared')))


def program_plan(program):
    """A corpus program's own launch plan (sizes the original's host computes), or None."""
    return json.loads((CORPUS/program['plan']).read_text()) if program.get('plan') else None


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
        from frontends.clang_ast import metal_available
        for dialect in ('cuda', 'metal') if metal_available() else ('cuda',):  # Metal import needs Xcode
            results[dialect] = check(program, next(generated.glob('*' + SUFFIX[dialect])), plan, validate)
    return results


def check(original, source, plan, validate, include=(), kernels=None, module=None, constants=None, geometry=None,
          resources=None, shared=None):
    try:
        program, _ = import_source(source, plan=plan, include=include, kernels=kernels, module=module,
                                   constants=constants, geometry=geometry, resources=resources, shared=shared)
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
