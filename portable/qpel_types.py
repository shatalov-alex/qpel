"""Editor-independent scalar type and lexical-scope checks for the qpel core.

Types are terms (IR version 3): scalars `bool u8 i8 u16 i16 u32 i32 u64 i64 f16
bf16 f32 void`, `shape3`, and `buffer<T, read|write|atomic>` (write buffers are
also readable). Version 3 writes them with the ScalarType and BufferType nodes;
the concept names of versions 1 and 2 (U32Type, ReadOnlyF32BufferType, ...) stay
valid spellings of the same terms (LEGACY). Pipeline resource effects,
bounds/races and numerical lowering are checked elsewhere.
"""
import re

INTEGERS_BITS = {'u8': (8, False), 'i8': (8, True), 'u16': (16, False), 'i16': (16, True),
                 'u32': (32, False), 'i32': (32, True), 'u64': (64, False), 'i64': (64, True)}
INTEGER_TERMS = set(INTEGERS_BITS)
FLOATS = {'f32'}                        # arithmetic floats
STORAGE_FLOATS = {'f16', 'bf16'}        # storage only: convert to f32 to compute
SCALAR_TERMS = INTEGER_TERMS | FLOATS | STORAGE_FLOATS | {'bool'}
ACCESSES = ('read', 'write', 'atomic')
ATOMIC_ELEMENTS = {'u32', 'i32', 'f32', 'u64'}  # f32 and u64: IR version 6
# The operations each atomic element has on both platforms (IR version 6 adds max, and, or, xor,
# exchange, float32 and 64-bit). Metal's 64-bit atomics are min and max without a result and it
# has no 64-bit add, which qpel's Metal makes of two 32-bit adds: a u64 operation is a statement.
ATOMIC_OPERATIONS = {
    'u32': {'AtomicLoad', 'AtomicStore', 'AtomicAdd', 'AtomicMin', 'AtomicMax', 'AtomicAnd', 'AtomicOr', 'AtomicXor',
            'AtomicExchange', 'AtomicCompareExchange'},
    'f32': {'AtomicLoad', 'AtomicStore', 'AtomicAdd', 'AtomicExchange'},
    'u64': {'AtomicAdd', 'AtomicMin', 'AtomicMax'},
}
ATOMIC_OPERATIONS['i32'] = ATOMIC_OPERATIONS['u32']
ATOMIC_STATEMENTS = {'u64'}  # elements whose atomic operations give no value
ATOMIC_KINDS = set().union(*ATOMIC_OPERATIONS.values())
# IR version 6 bitcasts: a 32-bit float's bits as an integer and back.
BITCASTS = {('f32', 'u32'), ('f32', 'i32'), ('u32', 'f32'), ('i32', 'f32'), ('f16', 'u16'), ('u16', 'f16')}  # f16: IR version 19
VALUE_TERMS = INTEGER_TERMS | {'f32', 'f16'}   # scalar kernel arguments
LEGACY = {'VoidType': 'void', 'BoolType': 'bool', 'U8Type': 'u8', 'U32Type': 'u32', 'I32Type': 'i32',
          'F32Type': 'f32', 'F16Type': 'f16', 'Shape3DType': 'shape3',
          'ReadOnlyU8BufferType': 'buffer<u8, read>', 'U32BufferType': 'buffer<u32, write>',
          'AtomicU32BufferType': 'buffer<u32, atomic>', 'AtomicI32BufferType': 'buffer<i32, atomic>',
          'ReadOnlyI32BufferType': 'buffer<i32, read>', 'I32BufferType': 'buffer<i32, write>',
          'ReadOnlyF32BufferType': 'buffer<f32, read>', 'F32BufferType': 'buffer<f32, write>',
          'ReadOnlyF16BufferType': 'buffer<f16, read>', 'F16BufferType': 'buffer<f16, write>'}
BUFFER = re.compile(r'buffer<(\w+), (read|write|atomic)>')
# IR version 4: a fixed-size array of scalars, for thread-private local variables only; IR version 11:
# of vectors too (`array<vec<f32, 2>, 4>`, CUB items of a pair type, mamba's scan); IR version 14: of
# matrices (`array<matrix<f32, 8, 8>, 8>`, llama.cpp's kernel_mul_mm), indexed the same by every lane.
ARRAY = re.compile(r'array<(\w+|vec<\w+, [234]>|matrix<(?:f32|f16), 8, 8>), ([1-9][0-9]{0,3})>')
MAX_ARRAY = 1024
# IR version 4: a small vector value (CUDA's int4, uint2, float3; Metal's vectors), copied whole,
# passed to and returned from functions; its elements are read and written as .x .y .z .w.
VECTOR = re.compile(r'vec<(\w+), ([234])>')
VECTOR_ELEMENTS = {'i32', 'u32', 'f32', 'u8', 'i8', 'u16', 'i16'}
# IR version 18: vectors of 8- and 16-bit integers (Metal's uchar2, llama.cpp's scale pairs), as values only:
# not in shared memory, where Metal pads a uchar3 to 4 bytes and CUDA does not.
WIDE_VECTOR_ELEMENTS = {'i32', 'u32', 'f32'}
VECTOR_FIELDS = 'xyzw'
# IR version 4: memory shared by the threads of one group (CUDA __shared__, Metal threadgroup),
# declared at the top of a Groups kernel; its elements are scalars or vectors.
SHARED = re.compile(r'shared<(\w+|vec<\w+, [234]>), ([1-9][0-9]{0,4})>')
MAX_SHARED_BYTES = 16384  # within Metal's 32 KiB and CUDA's 48 KiB, with room for the backends' own
# IR version 5: a thread's view into its group's shared memory (CUDA T*, Metal threadgroup T*): a
# shared array or another view, offset by an integer. It is indexed like the array it points into.
SHARED_VIEW = re.compile(r'shared<(\w+|vec<\w+, [234]>)>')
# IR version 13: an 8x8 matrix held by a subgroup together (Metal's simdgroup_matrix, NVIDIA's mma
# fragments), for matrix multiplication; its elements are f32 or f16 and are never indexed.
MATRIX = re.compile(r'matrix<(f32|f16), 8, 8>')
COMPARISONS = {'==', '!=', '<', '>', '<=', '>='}
# Math functions on float32: name -> arity. EXACT ones give the same bits on every backend;
# the others are within MATH_ULPS of the exact result (NUMERICS.md, math functions).
MATH = {'fabs': 1, 'fmin': 2, 'fmax': 2, 'floor': 1, 'ceil': 1, 'trunc': 1, 'round': 1, 'copysign': 2, 'sqrt': 1,
        'exp': 1, 'exp2': 1, 'log': 1, 'log2': 1, 'sin': 1, 'cos': 1, 'tanh': 1,
        'erf': 1, 'expm1': 1, 'log1p': 1, 'pow': 2, 'fast_exp': 1, 'atan2': 2}  # (erf, expm1, log1p: IR version 8; pow: 17; fast_exp: 20; atan2: 22)
V8_MATH = {'erf', 'expm1', 'log1p'}
V17_MATH = {'pow'}
V20_MATH = {'fast_exp'}
V22_MATH = {'atan2'}
EXACT_MATH = {'fabs', 'fmin', 'fmax', 'floor', 'ceil', 'trunc', 'round', 'copysign', 'sqrt'}
MATH_ULPS = 4
# A bounded function's own bound where it is wider than MATH_ULPS: CUDA documents powf within 9 ulp
# (Metal's precise::pow measured 1.5 ulp; NUMERICS.md, math functions).
# fast_exp (IR version 20) is within 3 + floor(2|x|) ulp of e^x, a bound that grows with x (CUDA documents __expf
# within 2 + floor(1.173|x|); Metal's fast::exp measured within 3 + floor(2|x|), NUMERICS.md). Tolerances here are
# one number per program, so they take the bound at |x| = 8, 19 ulp; a program with larger arguments gives its own.
FAST_EXP_ULPS = 19
MATH_BOUNDS = {'pow': 9, 'fast_exp': FAST_EXP_ULPS}


def fast_exp_ulps(x):
    """fast_exp's bound at argument x: 3 + floor(2|x|) ulp."""
    import math
    return 3 + math.floor(2*abs(x))


def math_ulps(functions):
    """The bound of the least accurate of these bounded functions (0 for none)."""
    bounded = [MATH_BOUNDS.get(f, MATH_ULPS) for f in functions if f not in EXACT_MATH]
    return max(bounded, default=0)
LEGACY_CASTS = {'I32Cast': 'i32', 'U32Cast': 'u32', 'U8Cast': 'u8', 'F32Cast': 'f32', 'F16Cast': 'f16'}


def term(t):
    """The canonical term for a term, a legacy concept name, or a type node."""
    if isinstance(t, dict):
        if t['kind'] == 'ScalarType':
            return t['attributes']['scalar']
        if t['kind'] == 'StructType':
            return 'struct ' + t['attributes']['name']
        if t['kind'] == 'BufferType':
            return buffer(t['attributes']['element'], t['attributes']['access'])
        if t['kind'] == 'ArrayType':
            return array(t['attributes']['element'], t['attributes']['length'])
        if t['kind'] == 'VectorType':
            return vector(t['attributes']['element'], t['attributes']['length'])
        if t['kind'] == 'SharedType':
            return shared(t['attributes']['element'], t['attributes']['length'])
        if t['kind'] == 'SharedViewType':
            return shared_view(t['attributes']['element'])
        if t['kind'] == 'MatrixType':
            return matrix(t['attributes']['element'])
        return LEGACY.get(t['kind'], t['kind'])
    return LEGACY.get(t, t)


def buffer(element, access):
    return f'buffer<{element}, {access}>'


def is_buffer(t):
    return BUFFER.fullmatch(term(t)) is not None


def element(t):
    return BUFFER.fullmatch(term(t)).group(1)


def access(t):
    return BUFFER.fullmatch(term(t)).group(2)


def array(element, length):
    return f'array<{element}, {length}>'


def is_array(t):
    return ARRAY.fullmatch(term(t)) is not None


def array_element(t):
    return ARRAY.fullmatch(term(t)).group(1)


def array_length(t):
    return int(ARRAY.fullmatch(term(t)).group(2))


def vector(element, length):
    return f'vec<{element}, {length}>'


def is_vector(t):
    return VECTOR.fullmatch(term(t)) is not None


def is_wide_vector(t):
    """A vector of 32-bit elements, the ones shared memory may hold (IR version 4)."""
    return is_vector(t) and vector_element(t) in WIDE_VECTOR_ELEMENTS


def vector_element(t):
    return VECTOR.fullmatch(term(t)).group(1)


def vector_length(t):
    return int(VECTOR.fullmatch(term(t)).group(2))


def matrix(element):
    return f'matrix<{element}, 8, 8>'


def is_matrix(t):
    return MATRIX.fullmatch(term(t)) is not None


def matrix_element(t):
    return MATRIX.fullmatch(term(t)).group(1)


def shared(element, length):
    return f'shared<{element}, {length}>'


def is_shared(t):
    return SHARED.fullmatch(term(t)) is not None


def shared_element(t):
    return SHARED.fullmatch(term(t)).group(1)


def shared_length(t):
    return int(SHARED.fullmatch(term(t)).group(2))


def shared_view(element):
    return f'shared<{element}>'


def is_shared_view(t):
    return SHARED_VIEW.fullmatch(term(t)) is not None


def view_element(t):
    """The element type of a shared array or a shared view."""
    return (SHARED_VIEW.fullmatch(term(t)) or SHARED.fullmatch(term(t))).group(1)


def shared_bytes(t):
    """Bytes of a shared array on the larger backend (Metal's 3-vectors take 16 bytes)."""
    item = shared_element(t)
    size = 4 * (4 if vector_length(item) == 3 else vector_length(item)) if is_vector(item) else SIZES[item]
    return size * shared_length(t)


def is_struct(t):
    return term(t).startswith('struct ')


def is_valid(t):
    t = term(t)
    if t in SCALAR_TERMS | {'void', 'shape3'} or is_struct(t) or MATRIX.fullmatch(t):
        return True
    m = ARRAY.fullmatch(t)
    if m:
        return (m.group(1) in SCALAR_TERMS or is_valid(m.group(1)) and (is_vector(m.group(1)) or is_matrix(m.group(1)))) and \
            1 <= int(m.group(2)) <= MAX_ARRAY  # (vectors: IR version 11; matrices: IR version 14)
    m = VECTOR.fullmatch(t)
    if m:
        return m.group(1) in VECTOR_ELEMENTS
    m = SHARED.fullmatch(t)
    if m:
        return (m.group(1) in SCALAR_TERMS - {'bool'} or is_valid(m.group(1)) and is_wide_vector(m.group(1))) \
            and shared_bytes(t) <= MAX_SHARED_BYTES
    m = SHARED_VIEW.fullmatch(t)
    if m:
        return m.group(1) in SCALAR_TERMS - {'bool'} or is_valid(m.group(1)) and is_wide_vector(m.group(1))
    m = BUFFER.fullmatch(t)
    return bool(m) and m.group(1) in SCALAR_TERMS - {'bool'} and (m.group(2) != 'atomic' or m.group(1) in ATOMIC_ELEMENTS)


def is_integer(t):
    return term(t) in INTEGER_TERMS


def is_signed(t):
    return INTEGERS_BITS[term(t)][1]


def bits(t):
    return INTEGERS_BITS[term(t)][0]


def unsigned_of(t):
    return 'u' + term(t)[1:]


def arithmetic(t):
    """The type integer arithmetic on t happens in: 8- and 16-bit values widen to 32 bits."""
    t = term(t)
    if t in INTEGER_TERMS and bits(t) < 32:
        return 'i32' if is_signed(t) else 'u32'
    return t


def fits(expected, actual):
    """True when a value of type `actual` converts to `expected` without loss."""
    expected, actual = term(expected), term(actual)
    if expected == actual:
        return True
    if is_buffer(expected) and is_buffer(actual):  # a writable buffer can be read through a read view
        return element(expected) == element(actual) and access(expected) == 'read' and access(actual) == 'write'
    if is_shared_view(expected) and is_shared(actual):  # a shared array is a view of its first element
        return view_element(expected) == view_element(actual)
    if expected in INTEGER_TERMS and actual in INTEGER_TERMS:
        (eb, es), (ab, a_signed) = INTEGERS_BITS[expected], INTEGERS_BITS[actual]
        if a_signed:
            return es and eb >= ab
        return eb > ab if es else eb >= ab
    return False


def join(left, right):
    """Common arithmetic type of two integers, or None when neither fits the other."""
    if fits(left, right):
        return arithmetic(left)
    if fits(right, left):
        return arithmetic(right)
    return None


# Pre-v3 names, still imported by other modules.
NUMBERS = {'u8', 'u32'}
INTEGERS = INTEGER_TERMS
CONDITIONS = INTEGER_TERMS | {'bool'}
SCALARS = SCALAR_TERMS
VALUES = VALUE_TERMS


class TypeChecker:
    def __init__(self, program):
        self.nodes = {n['id']: n for n in program['nodes']}
        self.functions = self.children(self.nodes[program['roots']['module']], 'functions')
        self.expression_types = {}
        self.shared_bytes = {}
        self.parents = {ref: n for n in program['nodes'] for values in n['children'].values() for ref in values}
        self.order = {f['id']: i for i, f in enumerate(self.functions)}
        # IR version 3: struct name -> {field: type term}, for kernel arguments such as ggml's kargs
        # IR version 12: module-level constants (lookup tables), visible in every function
        self.constants = self.children(self.nodes[program['roots']['module']], 'constants')
        self.constant_ids = {c['id'] for c in self.constants}
        self.structs = {}
        for decl in self.children(self.nodes[program['roots']['module']], 'structs'):
            self.structs[decl['attributes']['name']] = {f['attributes']['name']: term(self.child(f, 'type'))
                                                        for f in self.children(decl, 'fields')}

    def children(self, node, role):
        return [self.nodes[v] for v in node['children'].get(role, [])]

    def child(self, node, role):
        return self.children(node, role)[0]

    def target(self, node, role):
        return self.nodes[node['links'][role]]

    def declared(self, node):
        return term(self.child(node, 'type'))

    def domain(self, function):
        domain = self.children(function, 'domain')
        return domain[0]['kind'] if domain else None

    def require(self, node, condition, code, message):
        if not condition:
            raise ValueError(f"{node['id']} ({node['kind']}) {code}: {message}")

    def unique(self, node, names):
        name = node['attributes']['name']
        self.require(node, name not in names, 'DUPLICATE', 'Duplicate identifier: ' + name)
        names.add(name)

    def array_reference(self, call, arg):
        """True when arg is passed to an array parameter by reference (IR version 10)."""
        params = self.children(self.target(call, 'function'), 'parameters')
        args = call['children'].get('arguments', [])
        index = args.index(arg['id'])
        return index < len(params) and params[index]['attributes'].get('reference') and is_array(self.declared(params[index]))

    def expr(self, node, env, function):
        result = self._expr(node, env, function)
        self.expression_types[node["id"]] = result
        return result

    def cast(self, node, source, target):
        """Type of a conversion of a `source` value to `target` (Cast and the legacy casts)."""
        def check(ok, message):
            self.require(node, ok, 'CAST_TYPE', message)
        if target in INTEGER_TERMS:
            check(source in INTEGER_TERMS or source == 'f32', 'Integer conversion requires an integer or float32')
        elif target == 'f32':
            check(source in INTEGER_TERMS | FLOATS | STORAGE_FLOATS, 'Float32 conversion requires numeric scalar')
        elif target in STORAGE_FLOATS:
            check(source == 'f32', f'{target} storage conversion requires float32')
        else:
            check(False, 'Unsupported conversion target ' + target)
        return target

    def _expr(self, node, env, function):
        k, a = node['kind'], node['attributes']
        def e(role):
            return self.expr(self.child(node, role), env, function)
        def check(ok, code, message):
            self.require(node, ok, code, message)
        if k == 'Name':
            target = self.target(node, 'declaration')
            check(target['id'] in env, 'SCOPE', 'Declaration is not visible here')
            declared = self.declared(target)
            if is_array(declared) or is_shared(declared):  # not values: they are only indexed (IR version 4)
                parent = self.parents.get(node['id'])
                indexed = parent is not None and parent['kind'] == 'Index' and parent['children']['base'] == [node['id']]
                # IR version 5: a shared array is also where a view starts (shared<T> = tile, tile + k)
                viewed = is_shared(declared) and parent is not None and (
                    parent['kind'] == 'Binary' and parent['children']['left'] == [node['id']] or
                    parent['kind'] in ('Variable', 'Call', 'Select') or
                    parent['kind'] == 'Assign' and parent['children']['value'] == [node['id']])
                # IR version 10: a local array is also the argument of an array parameter passed by reference
                passed = is_array(declared) and parent is not None and parent['kind'] == 'Call' and \
                    node['id'] in parent['children'].get('arguments', []) and self.array_reference(parent, node)
                check(indexed or viewed or passed, 'ARRAY_USE', 'A local or shared array can only be indexed' +
                      (' or viewed (shared<T>)' if is_shared(declared) else ''))
            return declared
        if k == 'Literal':
            return 'bool' if a['value'] in ('true', 'false') else 'u32'
        if k == 'I32Literal':
            return 'i32'
        if k == 'Constant':
            return a['scalar']
        if k in LEGACY_CASTS and k not in ('F32Cast', 'F16Cast'):
            check(e('operand') in INTEGER_TERMS, 'CAST_TYPE', 'Integer conversion requires an integer')
            return LEGACY_CASTS[k]
        if k == 'Cast':
            return self.cast(node, e('operand'), a['to'])
        if k == 'View':
            base = e('base')
            check(is_buffer(base) and access(base) != 'atomic', 'VIEW', 'A view needs an ordinary buffer')
            check(a['element'] in SCALAR_TERMS - {'bool'}, 'VIEW', 'A view has a scalar element type')
            if a.get('unaligned'):  # IR version 18: read and written byte by byte, so only indexed directly
                below, parent = node, self.parents.get(node['id'])  # (moved first: view + i, view - i)
                while parent is not None and parent['kind'] == 'Binary' and parent['attributes'].get('operator') in ('+', '-') \
                        and self.child(parent, 'left')['id'] == below['id']:
                    below, parent = parent, self.parents.get(parent['id'])
                check(parent is not None and parent['kind'] == 'Index' and self.child(parent, 'base')['id'] == below['id'],
                      'VIEW', 'An unaligned view is indexed directly (after moving it by + or - if at all)')
            return buffer(a['element'], access(base))
        if k == 'F32Literal':
            return 'f32'
        if k == 'WorkgroupSumF32':
            parent = self.parents.get(node['id'])
            body = self.child(function, 'body')
            check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Grid1D' and parent is not None and parent['kind'] == 'Variable' and parent['id'] in body['children'].get('statements', []) and self.child(parent, 'value')['id'] == node['id'], 'COLLECTIVE_CONTEXT', 'Group sum must directly initialize a top-level kernel local')
            check(e('value') == 'f32', 'GROUP_SUM_TYPE', 'Group sum requires float32')
            return 'f32'
        if k == 'ReduceSumF32':
            check(e('buffer') in {'buffer<f32, read>', 'buffer<f32, write>'}, 'REDUCE_BUFFER', 'Sum requires a float32 buffer')
            check(e('start') in NUMBERS and e('count') in NUMBERS, 'REDUCE_RANGE', 'Start and count require unsigned integers')
            return 'f32'
        if k == 'F32Fma':
            types = [e(role) for role in ('left', 'right', 'addend')]
            check(all(t == 'f32' for t in types), 'FMA_TYPE', 'FMA requires three float32 operands; convert integers explicitly')
            return 'f32'
        if k == 'Math':
            args = self.children(node, 'arguments')
            check(a['function'] in MATH and len(args) == MATH[a['function']], 'MATH', f"{a['function']} takes {MATH.get(a['function'], '?')} arguments")
            check(all(self.expr(x, env, function) == 'f32' for x in args), 'MATH_TYPE', a['function'] + ' takes float32 arguments')
            return 'f32'
        if k == 'F16Cast':
            check(e('operand') == 'f32', 'CAST_TYPE', 'Float16 storage conversion requires float32')
            return 'f16'
        if k == 'F32Cast':
            check(e('operand') in INTEGER_TERMS | {'f32', 'f16', 'bf16'}, 'CAST_TYPE', 'Float32 conversion requires numeric scalar')
            return 'f32'
        if k == 'GridIndex':
            check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Grid1D', 'GRID_CONTEXT', 'Grid index requires a Grid1D kernel')
            return 'u32'
        if k == 'ThreadIndex':
            check(function['attributes'].get('isKernel', False) and self.domain(function) in ('Grid3D', 'Groups'), 'GRID_CONTEXT', 'Thread index requires a Grid3D or Groups kernel')
            return 'u32'
        if k in ('GroupIndex', 'LocalIndex', 'GroupSize', 'GroupCount'):
            check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Groups', 'GRID_CONTEXT', k + ' requires a Groups kernel')
            return 'u32'
        if k == 'Call':
            target = self.target(node, 'function')
            check(target['id'] in self.order, 'CALL_TARGET', 'Function belongs to another module')
            check(not target['attributes'].get('isKernel', False), 'CALL_KERNEL', 'Use dispatch to invoke kernels')
            check(self.order[target['id']] < self.order[function['id']], 'CALL_ORDER', 'Helpers must precede callers; recursion is unsupported')
            args, params = self.children(node, 'arguments'), self.children(target, 'parameters')
            check(len(args) == len(params), 'ARITY', 'Argument count does not match function')
            referenced = set()
            for arg, param in zip(args, params):
                actual, expected = self.expr(arg, env, function), self.declared(param)
                self.require(arg, fits(expected, actual), 'ARG_TYPE', f'Expected {expected}, got {actual}')
                if param['attributes'].get('reference'):  # the caller's own variable, passed in place
                    declaration = self.target(arg, 'declaration') if arg['kind'] == 'Name' else None
                    self.require(arg, declaration is not None and actual == expected and (
                        declaration['kind'] == 'Variable' or declaration['attributes'].get('reference')), 'REFERENCE_ARGUMENT',
                        'A reference parameter takes a local variable (or a reference parameter) of its exact type')
                    self.require(arg, declaration['id'] not in referenced, 'REFERENCE_ALIAS', 'One variable passed by reference twice')
                    referenced.add(declaration['id'])
            return term(self.child(target, 'resultType'))
        if k == 'Member':
            base = e('base')
            if is_struct(base):
                fields = self.structs.get(base[len('struct '):], {})
                check(a['member'] in fields, 'MEMBER', f"{base} has no field {a['member']}")
                return fields[a['member']]
            if is_vector(base):
                check(a['member'] in VECTOR_FIELDS[:vector_length(base)], 'MEMBER', f"{base} has no element {a['member']}")
                return vector_element(base)
            check(base == 'shape3' and a['member'] in ('x', 'y', 'z'), 'MEMBER', 'Members require Shape3D, a vector or a struct')
            return 'u32'
        if k == 'Index':
            base, index = e('base'), e('index')
            check(index in INTEGER_TERMS, 'INDEX_TYPE', 'Index must be an integer')
            if is_shared_view(base):
                return view_element(base)
            if is_shared(base):
                at = self.child(node, 'index')
                if at['kind'] in ('Literal', 'I32Literal', 'Constant'):
                    value = int(at['attributes']['value'].rstrip('uU'))
                    check(0 <= value < shared_length(base), 'ARRAY_BOUNDS', f'Index {value} is outside {base}')
                return shared_element(base)
            if is_array(base):
                at = self.child(node, 'index')
                if at['kind'] in ('Literal', 'I32Literal', 'Constant'):
                    value = int(at['attributes']['value'].rstrip('uU'))
                    check(0 <= value < array_length(base), 'ARRAY_BOUNDS',
                          f'Index {value} is outside {base}')
                return array_element(base)
            check(is_buffer(base) and access(base) != 'atomic', 'INDEX_BASE', 'Ordinary buffer required; use atomic operations for atomic buffers')
            return element(base)
        if k.startswith('Atomic'):
            address = e('address')
            check(is_buffer(address) and access(address) == 'atomic', 'ATOMIC_ADDRESS', 'Atomic buffer address required')
            item = element(address)
            check(k in ATOMIC_OPERATIONS[item], 'ATOMIC_OPERATION',
                  f"{k} is not an operation on {item} atomics; they have "
                  + ', '.join(sorted(ATOMIC_OPERATIONS[item])))
            if item in ATOMIC_STATEMENTS:
                parent = self.parents.get(node['id'])
                check(parent is not None and parent['kind'] == 'Evaluate', 'ATOMIC_RESULT',
                      f'{item} atomics are statements: Metal gives no previous value')
            if k != 'AtomicLoad':
                check(fits(item, e('value')), 'ATOMIC_VALUE', f'{item} value required')
            if k == 'AtomicCompareExchange':
                check(fits(item, e('expected')), 'ATOMIC_VALUE', f'{item} expected value required')
            return 'void' if k == 'AtomicStore' or item in ATOMIC_STATEMENTS else item
        if k == 'Bitcast':
            source = e('operand')
            check((source, a['to']) in BITCASTS, 'BITCAST', f"bitcast from {source} to {a['to']}: bitcasts are between f32 and u32 or i32, and f16 and u16")
            return a['to']
        if k == 'Unary':
            operand = e('operand')
            if a['operator'] == '!' and operand in CONDITIONS:
                return 'bool'
            if a['operator'] in ('-', '~') and operand in INTEGER_TERMS:
                return arithmetic(operand)
            if a['operator'] == '-' and operand == 'f32':
                return 'f32'
            if a['operator'] == 'clz' and operand in ('u32', 'i32', 'u64', 'i64'):  # IR version 12
                return operand  # the leading zero bits of the pattern, 0 to its width
            check(False, 'UNARY', 'Unsupported operand type')
        if k in {'Binary', 'Minimum', 'Maximum'}:
            left, right = e('left'), e('right')
            if k != 'Binary':
                common = join(left, right) if left in INTEGER_TERMS and right in INTEGER_TERMS else None
                check(common is not None, 'MINMAX_TYPE', 'Two integers of compatible signedness required')
                return common
            op = a['operator']
            divisor = self.child(node, 'right')
            if op in {'/', '%'} and divisor['kind'] in ('Literal', 'I32Literal', 'Constant') and right in INTEGER_TERMS:
                check(int(divisor['attributes']['value'].rstrip('uU')) != 0, 'ZERO_DIVISOR', 'Division/remainder by literal zero')
            if op in {'<<', '>>'}:
                check(left in INTEGER_TERMS and right in INTEGER_TERMS, 'SHIFT_TYPE', 'Shifts require integers')
                return arithmetic(left)
            if left in INTEGER_TERMS and right in INTEGER_TERMS:
                common = join(left, right)
                if common is not None:
                    if op in {'+', '-', '*', '/', '%', '&', '|', '^'}:
                        return common
                    if op in COMPARISONS:
                        return 'bool'
            if left == right == 'f32':
                if op in {'+', '-', '*', '/'}:
                    return 'f32'
                if op in COMPARISONS:
                    return 'bool'
            if op in {'+', '-'} and is_buffer(left) and right in INTEGER_TERMS:
                return left  # an element offset
            if op in {'+', '-'} and (is_shared(left) or is_shared_view(left)) and right in INTEGER_TERMS:
                return shared_view(view_element(left))  # a view further into shared memory (IR version 5)
            if op in {'&&', '||'} and left in CONDITIONS and right in CONDITIONS:
                return 'bool'
            if op in {'==', '!='} and left == right == 'bool':
                return 'bool'
            check(False, 'BINARY', f'Unsupported types: {left} {op} {right}')
        if k == 'ArrayValue':
            parent = self.parents.get(node['id'])
            check(parent is not None and parent['kind'] == 'Variable' and is_array(self.declared(parent)),
                  'ARRAY_VALUE', 'An array value only initializes a local array')
            expected = self.declared(parent)
            items = self.children(node, 'elements')
            check(len(items) == array_length(expected), 'ARRAY_VALUE',
                  f'{expected} needs {array_length(expected)} elements, not {len(items)}')
            for item in items:
                actual = self.expr(item, env, function)
                self.require(item, fits(array_element(expected), actual), 'ARRAY_VALUE',
                             f'Expected {array_element(expected)}, got {actual}')
            return expected
        if k == 'BufferPresent':  # IR version 9
            param = self.target(node, 'parameter')
            check(param['attributes'].get('optional', False), 'PRESENT_OPTIONAL',
                  'qpel.present() takes an optional buffer parameter')
            check(param['id'] in function['children'].get('parameters', []), 'PRESENT_SCOPE',
                  'qpel.present() takes a parameter of the kernel it is in')
            return 'bool'
        if k in ('SubgroupLane', 'SubgroupIndex', 'SubgroupSize'):  # IR version 5
            check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Groups', 'GRID_CONTEXT',
                  k + ' requires a Groups kernel')
            return 'u32'
        if k in ('MatrixFill', 'MatrixLoad', 'MatrixStore', 'MatrixMma'):  # IR version 13
            check(function is not None and function['attributes'].get('isKernel', False) and self.domain(function) == 'Groups',
                  'MATRIX_CONTEXT', 'Matrices are used by Groups kernels, whose subgroups hold them')
            if k == 'MatrixFill':
                check(a.get('element') in ('f32', 'f16') and e('value') == 'f32', 'MATRIX_TYPE',
                      'A matrix of f32 or f16 is filled with an f32 value')
                return matrix(a['element'])
            if k == 'MatrixMma':
                left, right, accumulator = e('a'), e('b'), e('c')
                check(is_matrix(left) and left == right and accumulator == matrix('f32'), 'MATRIX_TYPE',
                      'c + a b takes two matrices of one element type and an f32 accumulator')
                return matrix('f32')
            base = e('base')
            element_type = (element(base) if is_buffer(base) else view_element(base)
                            if is_shared(base) or is_shared_view(base) else None)
            check(element_type in ('f32', 'f16'), 'MATRIX_MEMORY', 'Matrices load from and store to f32 or f16 buffers or shared memory')
            check(e('offset') == 'u32' and e('stride') == 'u32', 'MATRIX_MEMORY', 'A matrix offset and row stride are u32')
            if k == 'MatrixLoad':
                return matrix(element_type)
            stored = e('matrix')
            check(stored == matrix(element_type), 'MATRIX_TYPE', f'A {stored} is stored to memory of {element_type}')
            check(not is_buffer(base) or access(base) == 'write', 'READ_ONLY', 'A matrix is stored to a writable buffer')
            return 'void'
        if k == 'SubgroupShuffle':
            value = e('value')
            check(value in ('u32', 'i32', 'f32'), 'SUBGROUP_TYPE', 'Shuffles move u32, i32 or f32 values')
            check(e('operand') in INTEGER_TERMS, 'SUBGROUP_TYPE', 'A shuffle lane, mask or delta is an integer')
            return value
        if k == 'SubgroupReduce':
            value = e('value')
            bitwise = a['operator'] in ('and', 'or', 'xor')
            check(value in (('u32', 'i32') if bitwise else ('u32', 'i32', 'f32')), 'SUBGROUP_TYPE',
                  f"subgroup_{a['operator']} takes {'u32 or i32' if bitwise else 'u32, i32 or f32'} values")
            return value
        if k == 'SubgroupBarrier':  # IR version 16: the subgroup's lanes wait for each other, ordering their memory
            parent = self.parents.get(node['id'])
            check(parent is not None and parent['kind'] == 'Evaluate', 'BARRIER_CONTEXT', 'qpel.subgroup_barrier() is a statement')
            return 'void'
        if k in ('Barrier', 'BarrierCount'):
            kernel = function['attributes'].get('isKernel', False)
            # IR version 7: a barrier may be in a helper (qpel_uniform.py checks the kernels that call it)
            check(kernel and self.domain(function) == 'Groups' or not kernel and k == 'Barrier', 'BARRIER_CONTEXT',
                  'Barriers are in Groups kernels and the helpers they call; barrier_count only in a kernel')
            if k == 'Barrier':
                parent = self.parents.get(node['id'])
                check(parent is not None and parent['kind'] == 'Evaluate', 'BARRIER_CONTEXT', 'qpel.barrier() is a statement')
                return 'void'
            check(e('value') in CONDITIONS, 'CONDITION', 'barrier_count counts threads whose condition holds')
            return 'i32'
        if k == 'VectorValue':
            expected = vector(a['element'], a['length'])
            check(is_valid(expected), 'VECTOR_VALUE', f'Invalid vector type {expected}')
            items = self.children(node, 'elements')
            check(len(items) == a['length'], 'VECTOR_VALUE', f'{expected} needs {a["length"]} elements, not {len(items)}')
            for item in items:
                actual = self.expr(item, env, function)
                self.require(item, fits(a['element'], actual), 'VECTOR_VALUE', f'Expected {a["element"]}, got {actual}')
            return expected
        if k == 'Select':
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
            yes, no = e('yes'), e('no')
            if fits(yes, no):
                return yes
            if fits(no, yes):
                return no
            check(False, 'SELECT_TYPE', 'Incompatible branches')
        check(False, 'EXPRESSION', 'Unsupported expression')

    def block(self, block, inherited, function, names, loops=0):
        """Check a block in a copy of the scope; True if it always returns."""
        env, returns = set(inherited), False
        for node in self.children(block, 'statements'):
            returns = self.statement(node, env, function, names, loops) or returns
        return returns

    def statement(self, node, env, function, names, loops):
        """Check one statement; a Variable extends env. True if it always returns."""
        k = node['kind']
        def e(role):
            return self.expr(self.child(node, role), env, function)
        def check(ok, code, message):
            self.require(node, ok, code, message)
        if k == 'Variable':
            self.unique(node, names)
            expected = self.declared(node)
            pointer = is_buffer(expected) and access(expected) != 'atomic'  # a view into a buffer (IR version 3)
            if is_shared(expected):  # IR version 4: declared at the top of a Groups kernel, without a value
                body = self.child(function, 'body')
                check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Groups'
                      and node['id'] in body['children'].get('statements', []), 'SHARED_PLACE',
                      'Shared memory is declared at the top level of a Groups kernel')
                check(not self.children(node, 'value'), 'VARIABLE_TYPE', 'Shared memory starts undefined; write it first')
                self.shared_bytes[function['id']] = self.shared_bytes.get(function['id'], 0) + shared_bytes(expected)
                check(self.shared_bytes[function['id']] <= MAX_SHARED_BYTES, 'SHARED_SIZE',
                      f'A kernel has at most {MAX_SHARED_BYTES} bytes of shared memory')
            elif is_array(expected):  # IR version 4: always initialized, element by element
                check(self.children(node, 'value') and self.child(node, 'value')['kind'] == 'ArrayValue',
                      'VARIABLE_TYPE', 'A local array needs an array value')
                e('value')
            elif self.children(node, 'value'):
                actual = e('value')
                check((expected in SCALAR_TERMS or pointer or is_vector(expected) or is_shared_view(expected)
                       or is_matrix(expected) and actual == expected)
                      and (is_matrix(expected) or fits(expected, actual)), 'VARIABLE_TYPE', f'Invalid initializer {expected} <- {actual}')
            else:
                check(pointer, 'VARIABLE_TYPE', 'Only buffer locals may start without a value')
            env.add(node['id'])
        elif k == 'Assign':
            target = self.child(node, 'target')
            expected, actual = e('target'), e('value')
            writable = target['kind'] == 'Name' and (expected in SCALAR_TERMS or is_vector(expected) or is_matrix(expected) or
                                                      (is_buffer(expected) or is_shared_view(expected))
                                                      and self.target(target, 'declaration')['kind'] == 'Variable')
            if target['kind'] == 'Name' and self.target(target, 'declaration')['kind'] == 'ConstantDecl':
                writable = False
            if target['kind'] == 'Member' and self.child(target, 'base')['kind'] == 'Name':
                writable = is_vector(self.expr(self.child(target, 'base'), env, function))  # v.x = ...
            if target['kind'] == 'Index':
                base = self.expr(self.child(target, 'base'), env, function)
                indexed = self.child(target, 'base')
                if indexed['kind'] == 'Name' and self.target(indexed, 'declaration')['kind'] == 'ConstantDecl':
                    base = 'void'  # a constant table is read-only (IR version 12)
                writable = is_buffer(base) and access(base) == 'write' or is_array(base) or is_shared(base) \
                    or is_shared_view(base)
            check(writable, 'READ_ONLY', 'Assignment target is not writable')
            check(fits(expected, actual), 'ASSIGN_TYPE', f'Incompatible assignment {expected} <- {actual}')
        elif k == 'Evaluate':
            e('value')
        elif k == 'Return':
            actual = e('value') if self.children(node, 'value') else 'void'
            expected = term(self.child(function, 'resultType'))
            check(fits(expected, actual), 'RETURN_TYPE', f'Expected {expected}, got {actual}')
            return True
        elif k == 'If':
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
            then = self.block(self.child(node, 'thenBody'), env, function, names, loops)
            otherwise = [self.block(b, env, function, names, loops) for b in self.children(node, 'elseBody')]
            return then and bool(otherwise) and otherwise[0]
        elif k == 'While':
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
            self.block(self.child(node, 'body'), env, function, names, loops + 1)
            return self.endless(node, 'condition')
        elif k == 'DoWhile':
            self.block(self.child(node, 'body'), env, function, names, loops + 1)
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
            return self.endless(node, 'condition')
        elif k == 'For':
            inner = set(env)  # the init variable is visible only inside the loop
            for init in self.children(node, 'init'):
                self.statement(init, inner, function, names, loops)
            for condition in self.children(node, 'condition'):
                check(self.expr(condition, inner, function) in CONDITIONS, 'CONDITION', 'Scalar condition required')
            for step in self.children(node, 'step'):
                self.statement(step, inner, function, names, loops)
            self.block(self.child(node, 'body'), inner, function, names, loops + 1)
            return self.endless(node, 'condition')
        elif k in ('Break', 'Continue'):
            check(loops > 0, 'LOOP_CONTEXT', k.lower() + ' must be inside a loop')
        elif k == 'Block':
            return self.block(node, env, function, names, loops)
        return False

    def endless(self, loop, role):
        """True for a loop that never ends except by return: its condition is the literal true
        (or, for `for`, absent) and no break of its own leaves it. Code after it is unreachable,
        so a helper may end with it (`while (true) { ... return x; }`)."""
        conditions = self.children(loop, role)
        if conditions and not (conditions[0]['kind'] == 'Literal' and conditions[0]['attributes']['value'] == 'true'):
            return False
        if not conditions and loop['kind'] != 'For':
            return False
        def breaks(node):
            for values in node['children'].values():
                for ref in values:
                    child = self.nodes[ref]
                    if child['kind'] == 'Break':
                        return True
                    if child['kind'] not in ('While', 'DoWhile', 'For') and breaks(child):
                        return True  # a break inside a nested loop leaves only that loop
            return False
        return not breaks(self.child(loop, 'body'))

    def validate(self):
        for name, fields in self.structs.items():
            for field, t in fields.items():
                if t not in SCALAR_TERMS:
                    raise ValueError(f'struct {name}: field {field} must be a scalar, not {t}')
        constant_names = set()
        for constant in self.constants:
            self.unique(constant, constant_names)
            t = self.declared(constant)
            value = self.child(constant, 'value')
            literal = lambda n: n['kind'] in ('Literal', 'I32Literal', 'F32Literal', 'Constant') or \
                n['kind'] == 'Unary' and n['attributes'].get('operator') == '-' and literal(self.child(n, 'operand'))
            if is_array(t):
                self.require(constant, value['kind'] == 'ArrayValue' and len(self.children(value, 'elements')) == array_length(t)
                             and all(literal(x) for x in self.children(value, 'elements')), 'CONSTANT_VALUE',
                             'A constant array is initialized with one literal per element')
                for x in self.children(value, 'elements'):
                    self.require(x, fits(array_element(t), self.expr(x, set(), None)), 'CONSTANT_VALUE',
                                 f'Expected {array_element(t)}')
            else:
                self.require(constant, t in SCALAR_TERMS and literal(value) and fits(t, self.expr(value, set(), None)),
                             'CONSTANT_VALUE', 'A constant is a scalar or an array of scalars, given by literals')
        function_names = set(constant_names)
        for function in self.functions:
            self.unique(function, function_names)
            kernel = function['attributes'].get('isKernel', False)
            result = term(self.child(function, 'resultType'))
            domain = self.children(function, 'domain')
            params = self.children(function, 'parameters')
            self.require(function, result in SCALAR_TERMS | {'void'} or is_vector(result) and is_valid(result), 'RESULT_TYPE',
                         'Result must be a scalar, a vector or void')
            self.require(function, (result == 'void' and bool(domain) and bool(params)) if kernel else not domain, 'FUNCTION_ABI', 'Kernel needs void result, Grid1D and parameters; helpers cannot have domains')
            names, bindings, env = set(), set(), set(self.constant_ids)
            for param in params:
                self.unique(param, names)
                t, a = self.declared(param), param['attributes']
                self.require(param, is_valid(t) and t != 'void', 'PARAM_TYPE', 'Unsupported parameter type')
                self.require(param, not is_struct(t) or t[len('struct '):] in self.structs, 'PARAM_TYPE', 'Unknown struct ' + t)
                self.require(param, not is_matrix(t) and not (is_array(t) and is_matrix(array_element(t))), 'PARAM_TYPE',
                             'A matrix, or an array of them, is a local of the function that uses it, not a parameter')
                if kernel:
                    self.require(param, (is_buffer(t) or t in VALUE_TERMS or t == 'shape3' or is_struct(t)) and a.get('isBuffer', False), 'KERNEL_ABI', 'Kernel parameter must be a bound buffer, shape, struct, or integer/f32 value')
                    binding = a.get('binding', 0)
                    self.require(param, 0 <= binding <= 30, 'BINDING_INDEX', 'Binding must be between 0 and 30')
                    self.require(param, binding not in bindings, 'BINDING_DUPLICATE', 'Duplicate binding')
                    bindings.add(binding)
                else:
                    self.require(param, not a.get('isBuffer', False), 'HELPER_BINDING', 'Helper parameter cannot have kernel binding')
                self.require(param, not a.get('reference') or not kernel and (t in SCALAR_TERMS or is_vector(t) or is_array(t)), 'REFERENCE',
                             'Only helper parameters of scalar or vector type can be references')
                env.add(param['id'])
            def descendants(n):
                yield n
                for values in n['children'].values():
                    for ref in values:
                        yield from descendants(self.nodes[ref])
            kinds = {n['kind'] for n in descendants(function)}
            if 'WorkgroupSumF32' in kinds:
                self.require(function, not kinds & {'Return', 'While', 'DoWhile', 'For', 'Break', 'Continue'}, 'COLLECTIVE_FLOW', 'Kernels with group sum cannot contain returns or loops; mask input and guard writes instead')
            returns = self.block(self.child(function, 'body'), env, function, names)
            self.require(function, result == 'void' or returns, 'MISSING_RETURN', 'Non-void helper requires unconditional return')
        self.wide_atomics()

    def wide_atomics(self):
        """A kernel (with the helpers it calls) adds to u64 atomics or takes their min and max, not
        both: qpel's Metal adds in two 32-bit halves, which a concurrent min or max would see apart."""
        def descendants(n):
            yield n
            for values in n['children'].values():
                for ref in values:
                    yield from descendants(self.nodes[ref])
        uses = {}
        for function in self.functions:  # helpers precede their callers
            found = set()
            for n in descendants(function):
                if n['kind'] == 'Call':
                    found |= uses.get(n['links']['function'], set())
                elif n['kind'] in ATOMIC_KINDS:
                    address = self.expression_types.get(self.child(n, 'address')['id'])
                    if address and is_buffer(address) and element(address) in ATOMIC_STATEMENTS:
                        found.add('add' if n['kind'] == 'AtomicAdd' else 'order')
            uses[function['id']] = found
            self.require(function, found != {'add', 'order'}, 'ATOMIC_MIX',
                         'A kernel adds to 64-bit atomics or takes their min/max, not both')


def validate_types(program):
    """Run after graph and grammar validation."""
    TypeChecker(program).validate()


def node_spec(t):
    """(kind, attributes) of the IR node that spells a type term, preferring the pre-v3 name."""
    t = term(t)
    for kind, legacy in LEGACY.items():
        if legacy == t:
            return kind, {}
    if is_buffer(t):
        return 'BufferType', {'element': element(t), 'access': access(t)}
    if is_struct(t):
        return 'StructType', {'name': t[len('struct '):]}
    if is_vector(t):
        return 'VectorType', {'element': vector_element(t), 'length': vector_length(t)}
    if is_shared(t):
        return 'SharedType', {'element': shared_element(t), 'length': shared_length(t)}
    if is_shared_view(t):
        return 'SharedViewType', {'element': view_element(t)}
    if is_array(t):
        return 'ArrayType', {'element': array_element(t), 'length': array_length(t)}
    if is_matrix(t):
        return 'MatrixType', {'element': matrix_element(t)}
    return 'ScalarType', {'scalar': t}


def buffer_length(resource, shapes):
    """Number of elements of a buffer resource of a plan, for {shape name: (W, H, N)}."""
    if resource.get('length'):
        return resource['length']
    w, h, n = shapes[resource['extent']]
    per = resource.get('per', 1)  # IR version 21: a count per tile of `per` elements, rounded up
    return -(-(w * h * n) // per) * resource.get('scale', 1)


def normalize_plan(plan):
    """A launch plan with type terms (schema 2); schema 1 plans spell types with concept names."""
    if plan.get('schema') == 2:
        return plan
    if plan.get('schema') != 1:
        raise ValueError('Unsupported launch schema')
    resources = [dict(r, type=term(r['type'])) for r in plan['resources']]
    return dict(plan, schema=2, resources=resources)


# Host-side storage of each element type. u16, u32 and u64 travel through torch as the signed
# type of the same width (torch on MPS lacks them); CuPy has no bfloat16, so bf16 travels as its bits.
TORCH_DTYPES = {'u8': 'uint8', 'i8': 'int8', 'u16': 'int16', 'i16': 'int16', 'u32': 'int32', 'i32': 'int32',
                'u64': 'int64', 'i64': 'int64', 'f16': 'float16', 'bf16': 'bfloat16', 'f32': 'float32'}
NUMPY_DTYPES = {'u8': 'uint8', 'i8': 'int8', 'u16': 'uint16', 'i16': 'int16', 'u32': 'uint32', 'i32': 'int32',
                'u64': 'uint64', 'i64': 'int64', 'f16': 'float16', 'bf16': 'uint16', 'f32': 'float32'}


SIZES = {'u8': 1, 'i8': 1, 'bool': 1, 'u16': 2, 'i16': 2, 'f16': 2, 'bf16': 2, 'u32': 4, 'i32': 4, 'f32': 4,
         'u64': 8, 'i64': 8}
PACK = {'u8': 'B', 'i8': 'b', 'bool': '?', 'u16': 'H', 'i16': 'h', 'f16': 'e', 'bf16': 'H', 'u32': 'I', 'i32': 'i',
        'f32': 'f', 'u64': 'Q', 'i64': 'q'}


def struct_layout(fields):
    """[(field, type, offset)], size: C's layout of scalar fields (each aligned to its size), which
    CUDA and Metal share. bf16 fields are given as their 16 bits."""
    out, offset, largest = [], 0, 1
    for name, t in fields:
        size = SIZES[t]
        offset = -(-offset // size) * size
        out.append((name, t, offset))
        offset += size
        largest = max(largest, size)
    return out, -(-offset // largest) * largest


def pack_struct(fields, value):
    """bytes of a struct value {field: number} in struct_layout's C layout (bf16 as its bits)."""
    import struct
    layout, size = struct_layout(fields)
    data = bytearray(size)
    for name, t, offset in layout:
        struct.pack_into('<' + PACK[t], data, offset, value[name])
    return bytes(data)
