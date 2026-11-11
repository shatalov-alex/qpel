"""Structural contract for the portable qpel graph, independent of the editor.

Checks grammar and reference categories, not expression types, scope or effects.
"""
import re
import math
import struct

from qpel_types import INTEGER_TERMS, INTEGERS_BITS, MATH, SCALAR_TERMS, buffer, is_valid

TYPE_KINDS = set('VoidType BoolType U8Type U32Type I32Type ReadOnlyI32BufferType I32BufferType F32Type ReadOnlyF32BufferType F32BufferType F16Type ReadOnlyF16BufferType F16BufferType ReadOnlyU8BufferType U32BufferType AtomicU32BufferType AtomicI32BufferType Shape3DType ScalarType BufferType StructType'.split())
EXPRESSIONS = set('Name Literal I32Literal Constant Cast View Math GroupIndex LocalIndex GroupSize GroupCount I32Cast U32Cast U8Cast F32Literal F32Cast F16Cast F32Fma ReduceSumF32 WorkgroupSumF32 Binary Unary Index Member Call Select GridIndex ThreadIndex Minimum Maximum AtomicLoad AtomicStore AtomicAdd AtomicMin'.split())
STATEMENTS = set('Block Variable Assign Evaluate If While DoWhile For Break Continue Return'.split())

# Child role -> (allowed concepts, minimum count, maximum count or None).
def one(kinds):
    return (set(kinds.split()) if isinstance(kinds, str) else kinds, 1, 1)

def many(kinds):
    return (set(kinds.split()) if isinstance(kinds, str) else kinds, 0, None)

def optional(kinds):
    return (set(kinds.split()) if isinstance(kinds, str) else kinds, 0, 1)

CHILDREN = {
    'GpuModule': {'functions': many('Function'), 'structs': many('StructDecl')},
    'StructDecl': {'fields': many('Field')},
    'Field': {'type': one(TYPE_KINDS)},
    'Function': {'parameters': many('Parameter'), 'resultType': one(TYPE_KINDS), 'body': one('Block'), 'domain': optional('Grid1D Grid3D Groups')},
    'Parameter': {'type': one(TYPE_KINDS)},
    'F32Cast': {'operand': one(EXPRESSIONS)},
    'I32Cast': {'operand': one(EXPRESSIONS)},
    'U32Cast': {'operand': one(EXPRESSIONS)},
    'U8Cast': {'operand': one(EXPRESSIONS)},
    'Cast': {'operand': one(EXPRESSIONS)},
    'Math': {'arguments': many(EXPRESSIONS)},
    'WorkgroupSumF32': {'value': one(EXPRESSIONS)},
    'ReduceSumF32': {role: one(EXPRESSIONS) for role in ('buffer','start','count')},
    'F16Cast': {'operand': one(EXPRESSIONS)},
    'F32Fma': {role: one(EXPRESSIONS) for role in ('left', 'right', 'addend')},
    'Block': {'statements': many(STATEMENTS)},
    'Variable': {'type': one(TYPE_KINDS), 'value': optional(EXPRESSIONS)},  # only buffer locals may omit it
    'View': {'base': one(EXPRESSIONS)},
    'Binary': {'left': one(EXPRESSIONS), 'right': one(EXPRESSIONS)},
    'Unary': {'operand': one(EXPRESSIONS)},
    'Index': {'base': one(EXPRESSIONS), 'index': one(EXPRESSIONS)},
    'Member': {'base': one(EXPRESSIONS)},
    'Call': {'arguments': many(EXPRESSIONS)},
    'Select': {'condition': one(EXPRESSIONS), 'yes': one(EXPRESSIONS), 'no': one(EXPRESSIONS)},
    'Assign': {'target': one('Name Index'), 'value': one(EXPRESSIONS)},
    'Evaluate': {'value': one(EXPRESSIONS)},
    'If': {'condition': one(EXPRESSIONS), 'thenBody': one('Block'), 'elseBody': optional('Block')},
    'While': {'condition': one(EXPRESSIONS), 'body': one('Block')},
    # do body while (condition); continue evaluates the condition.
    'DoWhile': {'body': one('Block'), 'condition': one(EXPRESSIONS)},
    # for (init; condition; step) body. A missing condition means true.
    'For': {'init': optional('Variable Assign'), 'condition': optional(EXPRESSIONS),
            'step': optional('Assign Evaluate'), 'body': one('Block')},
    'Return': {'value': optional(EXPRESSIONS)},
    'Pipeline': {'resources': many('Resource'), 'stages': many('Dispatch')},
    'Resource': {'type': one(TYPE_KINDS)},
    'Dispatch': {'bindings': many('Binding')},
}
for kind in ('Minimum', 'Maximum'):
    CHILDREN[kind] = CHILDREN['Binary']
for kind in ('AtomicLoad', 'AtomicStore', 'AtomicAdd', 'AtomicMin'):
    CHILDREN[kind] = {'address': one(EXPRESSIONS)}
    if kind != 'AtomicLoad':
        CHILDREN[kind]['value'] = one(EXPRESSIONS)

# Links are required except Resource.extent (shape resources have no extent).
LINKS = {
    'Name': {'declaration': {'Parameter', 'Variable'}},
    'Call': {'function': {'Function'}},
    'Pipeline': {'module': {'GpuModule'}},
    'Resource': {'extent': {'Resource'}},
    'Dispatch': {'kernel': {'Function'}, 'grid': {'Resource'}},
    'Binding': {'parameter': {'Parameter'}, 'resource': {'Resource'}},
}
ATTRIBUTES = {
    'GpuModule': {'name': str}, 'Function': {'name': str, 'isKernel': bool},
    'Parameter': {'name': str, 'binding': int, 'isBuffer': bool},
    'Variable': {'name': str}, 'Literal': {'value': str}, 'I32Literal': {'value': str}, 'F32Literal': {'value': str},
    'Binary': {'operator': str}, 'Unary': {'operator': str},
    'Member': {'member': str}, 'Pipeline': {'name': str},
    'Resource': {'name': str, 'kind': str},
    'Dispatch': {'blockSize': int, 'blockHeight': int, 'blockDepth': int, 'completionBarrier': bool,
                 'groupsX': str, 'groupsY': str, 'groupsZ': str},
    'ThreadIndex': {'axis': str},
    # IR version 3
    'ScalarType': {'scalar': str}, 'BufferType': {'element': str, 'access': str},
    'Cast': {'to': str}, 'Constant': {'value': str, 'scalar': str}, 'Math': {'function': str},
    'StructDecl': {'name': str}, 'Field': {'name': str}, 'StructType': {'name': str}, 'View': {'element': str},
    'GroupIndex': {'axis': str}, 'LocalIndex': {'axis': str}, 'GroupSize': {'axis': str}, 'GroupCount': {'axis': str},
}
DEFAULTED = {'Function': {'isKernel'}, 'Parameter': {'binding', 'isBuffer'},
             'Dispatch': {'blockHeight', 'blockDepth', 'groupsX', 'groupsY', 'groupsZ'}}
# Every qpel name is emitted verbatim into both CUDA C++ and Metal Shading Language, so it
# must avoid qpel keywords, C++ keywords, CUDA built-ins and Metal keywords and type names.
QPEL_KEYWORDS = set('void bool uint int float return if while for true false kernel device min max'.split()) | \
    set('let fn module pipeline dispatch cast view math atomic qpel'.split())  # the text syntax (TEXT.md)
CPP_KEYWORDS = set('''alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t
    char32_t class compl concept const consteval constexpr constinit const_cast continue co_await co_return
    co_yield decltype default delete do double dynamic_cast else enum explicit export extern false float for
    friend goto if inline int long mutable namespace new noexcept not not_eq nullptr operator or or_eq private
    protected public register reinterpret_cast requires return short signed sizeof static static_assert
    static_cast struct switch template this thread_local throw true try typedef typeid typename union unsigned
    using virtual void volatile wchar_t while xor xor_eq'''.split())
CUDA_NAMES = set('blockIdx blockDim threadIdx gridDim warpSize dim3 uint3 int3 float3 half'.split())
METAL_NAMES = set('''device constant threadgroup threadgroup_imageblock thread ray_data object_data kernel
    vertex fragment visible metal half uchar ushort uint ulong bfloat size_t ptrdiff_t uint2 uint3 uint4
    float2 float3 float4 half2 half3 half4 atomic_uint atomic_int'''.split())
RESERVED = QPEL_KEYWORDS | CPP_KEYWORDS | CUDA_NAMES | METAL_NAMES
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]*')
MAX_BLOCK_SIZE = 256


def is_identifier(name):
    """True for a name that is safe to emit unchanged into every backend."""
    return (isinstance(name, str) and IDENTIFIER.fullmatch(name) is not None
            and not name.startswith('qpel_') and name not in RESERVED)


def is_group_count(text):
    """An expression for a number of groups: integers, W, H and N (the dispatch shape),
    + - * / (C's division of non-negative numbers) and parentheses."""
    return isinstance(text, str) and len(text) <= 200 and re.fullmatch(r'[0-9WHN+\-*/() ]+', text) is not None \
        and _parses(text)


def _parses(text):
    import ast
    try:
        tree = ast.parse(text, mode='eval')
    except SyntaxError:
        return False
    allowed = (ast.Expression, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Constant, ast.Name, ast.Load,
               ast.UnaryOp, ast.USub)
    return all(isinstance(n, allowed) for n in ast.walk(tree)) and \
        all(type(n.value) is int for n in ast.walk(tree) if isinstance(n, ast.Constant))


def group_count(text, shape):
    """Evaluate a group-count expression for a shape (W, H, N)."""
    import ast
    names = dict(zip('WHN', shape))

    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return names[n.id]
        if isinstance(n, ast.UnaryOp):
            return -walk(n.operand)
        a, b = walk(n.left), walk(n.right)
        if isinstance(n.op, ast.Add):
            return a + b
        if isinstance(n.op, ast.Sub):
            return a - b
        if isinstance(n.op, ast.Mult):
            return a * b
        if b == 0:
            raise ValueError('group count divides by zero: ' + text)
        return int(a / b) if a * b < 0 else a // b  # C truncates
    return walk(ast.parse(text, mode='eval'))


def is_block_size(value):
    return type(value) is int and 1 <= value <= MAX_BLOCK_SIZE


def validate_envelope(program):
    """Reject malformed serialized data with a diagnostic, before dereferencing it."""
    if not isinstance(program, dict):
        raise ValueError('IR program must be an object')
    if not isinstance(program.get('nodes'), list) or not isinstance(program.get('roots'), dict):
        raise ValueError('IR requires nodes list and roots object')
    if any(not isinstance(v, str) for v in program['roots'].values()):
        raise ValueError('IR root references must be strings')
    for n in program['nodes']:
        if not isinstance(n, dict) or set(n) != {'id', 'kind', 'attributes', 'children', 'links'}:
            raise ValueError('IR node requires id, kind, attributes, children and links')
        if not isinstance(n['id'], str) or not n['id'] or not isinstance(n['kind'], str):
            raise ValueError('IR node id/kind must be nonempty/string')
        if any(not isinstance(n[k], dict) for k in ('attributes', 'children', 'links')):
            raise ValueError('IR node fields must be objects')
        if any(not isinstance(v, str) for v in n['links'].values()):
            raise ValueError('IR links must be string references')
        if any(not isinstance(v, list) or any(not isinstance(t, str) for t in v) for v in n['children'].values()):
            raise ValueError('IR children must be lists of string references')


def validate_structure(nodes):
    for n in nodes.values():
        kind = n['kind']
        def fail(message):
            raise ValueError(f"{n['id']} ({kind}): {message}")
        children = CHILDREN.get(kind, {})
        if set(n['children']) - set(children):
            fail('Unknown child role')
        for role, (allowed, minimum, maximum) in children.items():
            values = n['children'].get(role, [])
            if len(values) < minimum or maximum is not None and len(values) > maximum:
                fail('Invalid child count for ' + role)
            if any(nodes[v]['kind'] not in allowed for v in values):
                fail('Invalid child kind for ' + role)
        links = LINKS.get(kind, {})
        required = set(links) if kind != 'Resource' else set()
        if set(n['links']) - set(links) or required - set(n['links']):
            fail('Invalid link roles')
        for role, target in n['links'].items():
            if nodes[target]['kind'] not in links[role]:
                fail('Invalid link target for ' + role)
        attrs = ATTRIBUTES.get(kind, {})
        if set(n['attributes']) - set(attrs) or set(attrs) - DEFAULTED.get(kind, set()) - set(n['attributes']):
            fail('Invalid attribute names')
        for key, value in n['attributes'].items():
            if type(value) is not attrs[key]:
                fail('Invalid attribute type for ' + key)
        a = n['attributes']
        if 'name' in a:
            name = a['name']
            if not is_identifier(name):
                fail('Invalid or reserved identifier')
        if kind == 'Literal':
            value = a['value']
            if value not in ('true', 'false'):
                if not re.fullmatch(r'[0-9]+[uU]?', value) or len(value.rstrip('uU')) > 10 or int(value.rstrip('uU')) > 4294967295:
                    fail('Expected boolean or unsigned 32-bit decimal literal')
        if kind == 'I32Literal':
            value = a['value']
            if not re.fullmatch(r'[0-9]{1,10}', value) or int(value) > 2147483647:
                fail('Expected nonnegative signed 32-bit decimal literal')
        if kind == 'F32Literal':
            value = a['value']
            if not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
                fail('Expected finite nonnegative decimal float32 literal')
            try:
                rounded = struct.unpack('f', struct.pack('f', float(value)))[0]
                if not math.isfinite(rounded):raise OverflowError()
            except (ValueError, OverflowError):
                fail('Float32 literal is out of range')
        if kind == 'ScalarType' and a['scalar'] not in SCALAR_TERMS | {'void'}:
            fail('Unknown scalar type ' + a['scalar'])
        if kind == 'BufferType' and not is_valid(buffer(a['element'], a['access'])):
            fail(f"Invalid buffer type buffer<{a['element']}, {a['access']}>")
        if kind == 'Math' and a['function'] not in MATH:
            fail('Unknown math function ' + a['function'])
        if kind == 'Cast' and a['to'] not in SCALAR_TERMS - {'bool'}:
            fail('Unsupported conversion target ' + a['to'])
        if kind == 'Constant':
            if a['scalar'] not in INTEGER_TERMS or not re.fullmatch(r'-?[0-9]{1,20}', a['value']):
                fail('Expected a decimal integer constant')
            size, signed = INTEGERS_BITS[a['scalar']]
            low, high = (-2**(size - 1), 2**(size - 1) - 1) if signed else (0, 2**size - 1)
            if not low <= int(a['value']) <= high:
                fail(f"Constant {a['value']} is out of range for {a['scalar']}")
        if kind == 'Member' and not (a['member'] in ('x', 'y', 'z') or is_identifier(a['member'])):
            fail('Unsupported shape member or field name')
        if kind == 'StructType' and not is_identifier(a['name']):
            fail('Invalid struct name')
        if kind == 'Resource' and a['kind'] not in ('input', 'output', 'scratch', 'zeroed', 'shape', 'value'):
            fail('Unsupported resource kind')
        if kind == 'Dispatch':
            dims = (a['blockSize'], a.get('blockHeight', 1), a.get('blockDepth', 1))
            if not all(is_block_size(d) for d in dims) or dims[0]*dims[1]*dims[2] > MAX_BLOCK_SIZE:
                fail(f'Block size must be between 1 and {MAX_BLOCK_SIZE} threads in total')
        if kind in ('ThreadIndex', 'GroupIndex', 'LocalIndex', 'GroupSize', 'GroupCount') and a['axis'] not in ('x', 'y', 'z'):
            fail(f'{kind} axis must be x, y or z')
        if kind == 'Dispatch':
            for axis in 'XYZ':
                if 'groups' + axis in a and not is_group_count(a['groups' + axis]):
                    fail(f"groups{axis} must be an integer expression of W, H and N: {a['groups' + axis]!r}")
        if kind == 'Parameter' and a.get('binding', 0) < 0:
            fail('Binding must be nonnegative')
