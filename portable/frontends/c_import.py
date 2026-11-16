"""Map a clang AST of CUDA or Metal kernels to qpel IR (qpel-portable-core-v1).

One mapper serves both languages; the differences (how a kernel, a buffer
binding, the thread index and atomics are spelled) live in small dialect
tables. Anything without a qpel equivalent fails with a source location and a
short reason code, never by guessing. The finished program then goes through
the normal qpel checks, which decide types, scopes and pipeline effects.
"""
from pathlib import Path
import re
import struct

from qpel_schema import METAL_FUNCTIONS, is_identifier
from qpel_types import (SCALAR_TERMS, VALUE_TERMS, access, buffer as type_buffer, fits, is_buffer as type_is_buffer,
                        is_signed, node_spec, normalize_plan, term as type_term)
from qpel_types import access as type_access, bits as type_bits, INTEGER_TERMS, MATH, is_struct, is_array
from qpel_types import arithmetic as type_arithmetic, element as element_of, is_vector, vector_element, vector_length, is_shared
from qpel_types import is_shared_view, view_element, shared_bytes, BITCASTS
from . import clang_ast
from .clang_ast import position
from .facades import inline_facades
from .demote import demote_unit
from .statics import lift_statics, read_enums, local_pointers_as_references

IR_HEADER = {'format': 'qpel.ir', 'version': 1, 'semantics': 'qpel-portable-core-v1'}
U32_MAX = 2**32 - 1


class Unsupported(ValueError):
    """A construct the current qpel IR cannot represent."""

    def __init__(self, node, code, message):
        file, line, col = position(node) if node else (None, None, None)
        self.code = code
        self.location = f'{file}:{line}:{col}'
        super().__init__(f'{self.location}: [{code}] {message}')

    def __reduce__(self):  # survives the trip back from a survey worker process
        return (_unsupported, (self.code, self.location, self.args[0]))


def _unsupported(code, location, text):
    error = Unsupported(None, code, '')
    error.location, error.args = location, (text,)
    return error


# --- types -----------------------------------------------------------------

QUALIFIERS = {'const', 'volatile', 'device', 'constant', 'threadgroup', 'thread', 'struct',
              '__restrict', '__restrict__', 'restrict'}
SCALARS = {'unsigned int': 'u32', 'unsigned': 'u32', 'uint': 'u32', 'uint32_t': 'u32',
           'unsigned char': 'u8', 'uchar': 'u8', 'uint8_t': 'u8', 'bool': 'bool', 'float': 'f32',
           '__half': 'f16', 'half': 'f16', 'int': 'i32', 'signed int': 'i32', 'signed': 'i32', 'int32_t': 'i32',
           'void': 'void', 'double': 'f64',
           'atomic_uint': 'atomic', 'atomic_int': 'atomic_i32', 'atomic_float': 'atomic_f32', 'atomic_ulong': 'atomic_u64',
           # IR version 3. Plain char is signed on every CUDA and Metal target.
           'signed char': 'i8', 'char': 'i8', 'int8_t': 'i8',
           'unsigned short': 'u16', 'ushort': 'u16', 'uint16_t': 'u16', 'unsigned short int': 'u16',
           'short': 'i16', 'short int': 'i16', 'signed short': 'i16', 'int16_t': 'i16',
           'long long': 'i64', 'long long int': 'i64', 'long': 'i64', 'long int': 'i64', 'int64_t': 'i64',
           'ptrdiff_t': 'i64', 'signed long long': 'i64',
           'unsigned long long': 'u64', 'unsigned long long int': 'u64', 'unsigned long': 'u64', 'ulong': 'u64',
           'unsigned long int': 'u64', 'uint64_t': 'u64', 'size_t': 'u64',
           '__nv_bfloat16': 'bf16', 'nv_bfloat16': 'bf16', 'bfloat': 'bf16'}
SHAPES = {'uint3', 'QpelShape', 'dim3'}
# CUDA's and Metal's vector types (IR version 4 vec<T, N>). uint3 stays a shape: it is how CUDA
# and Metal spell thread positions and grid sizes.
VECTOR_NAMES = {f'{c}{n}': f'vec<{t}, {n}>' for c, t in (('int', 'i32'), ('uint', 'u32'), ('float', 'f32'))
                for n in (2, 3, 4) if f'{c}{n}' != 'uint3'}
SCALARS.update(VECTOR_NAMES)


def has_effects(node):
    """True when evaluating node could change anything: a call, an assignment, ++ or --.
    The operand of sizeof is never evaluated."""
    if node.get('kind') == 'UnaryExprOrTypeTraitExpr':
        return False
    if node.get('kind') in ('CallExpr', 'CXXMemberCallExpr', 'CXXOperatorCallExpr', 'CompoundAssignOperator') or \
            node.get('kind') in ('BinaryOperator', 'UnaryOperator') and node.get('opcode') in ('=', '++', '--'):
        return True
    return any(has_effects(c) for c in node.get('inner', ()) if isinstance(c, dict))


def view_spelling(decl):
    """The type of a declaration as describe() reads it: qpel's CUDA spells a shared view
    `qpel_shared_view<float>` (an alias of float*, backends/cuda.h), which clang desugars."""
    t = decl['type']
    return t.get('desugaredQualType', t['qualType']) if 'qpel_shared_view<' in t['qualType'] else t['qualType']


def is_reference(qual_type):
    """A non-const reference (`uint2& r`, Metal `thread float3 &r`): the callee writes the caller's variable."""
    if '&' not in qual_type:
        return False
    return 'const' not in qual_type.split('&')[0].split()
ATOMIC_SCALARS = {'atomic': 'u32', 'atomic_i32': 'i32', 'atomic_f32': 'f32', 'atomic_u64': 'u64'}  # Metal atomic element -> qpel element
NEW_INTEGERS = {'i8', 'u16', 'i16', 'u64', 'i64'}  # spelled with Constant and Cast (IR version 3)


def words(text):
    text = text.replace('metal::', '').replace('&', ' ')
    return [w for w in text.split() if w not in QUALIFIERS]


def describe(qual_type):
    """('pointer', const, scalar) | ('shape',) | ('scalar', scalar) | ('other', text)."""
    if '[' in qual_type or '(' in qual_type:
        return ('other', qual_type)
    if '*' in qual_type:
        pointee, _, rest = qual_type.rpartition('*')
        if '*' in pointee or words(rest):
            return ('other', qual_type)
        base = ' '.join(words(pointee))
        return ('pointer', 'const' in pointee.split(), SCALARS.get(base, base))
    base = ' '.join(words(qual_type))
    if base in SHAPES:
        return ('shape',)
    return ('scalar', SCALARS.get(base, base))


def scalar_of(node):
    # A typedef inside a template (`type`, `value_type`) names its real type only desugared.
    found = node.get('type', {})
    t = describe(found.get('qualType', ''))
    if t[0] == 'scalar' and t[1] not in SCALAR_TERMS and not t[1].startswith('vec<') and found.get('desugaredQualType'):
        t = describe(found['desugaredQualType'])
    return t[1] if t[0] == 'scalar' else t[0]


# --- clang tree helpers ----------------------------------------------------

PASSTHROUGH = {'ParenExpr', 'ImplicitCastExpr', 'CStyleCastExpr', 'CXXFunctionalCastExpr',
               'CXXStaticCastExpr', 'MaterializeTemporaryExpr', 'ExprWithCleanups',
               'CXXBindTemporaryExpr', 'ConstantExpr'}
EXPLICIT_CASTS = {'CStyleCastExpr', 'CXXFunctionalCastExpr', 'CXXStaticCastExpr'}
VALUE_CASTS = {'NoOp', 'LValueToRValue', 'IntegralCast', 'IntegralToBoolean', 'FloatingCast', 'FloatingToIntegral',
               'IntegralToFloating', 'UserDefinedConversion', 'ArrayToPointerDecay',
               'FunctionToPointerDecay', 'BuiltinFnToFnPtr', 'ConstructorConversion'}


def peel(node):
    """Strip parentheses, value-preserving casts and conversions; return the core node.

    An explicit cast to float or half is kept as the core: qpel spells it F32Cast or
    F16Cast even when the value already has that type."""
    while True:
        kind = node['kind']
        if kind in EXPLICIT_CASTS and scalar_of(node) in ('f32', 'f16'):
            return node
        if kind in PASSTHROUGH:
            cast = node.get('castKind')
            if cast == 'BitCast' and scalar_of(node) == 'pointer':
                return node  # a pointer reinterpretation: a qpel View, read by Importer.core
            if cast in ('NullToPointer', 'PointerToBoolean'):
                return node  # a null pointer, or a pointer as a condition: Importer.presence reads the tests
            if cast and cast not in VALUE_CASTS:
                raise Unsupported(node, 'conversion', f'{cast} has no qpel equivalent')
            node = node['inner'][0]
        elif kind == 'SubstNonTypeTemplateParmExpr':
            node = node['inner'][-1]  # the value the template parameter was replaced by
        elif kind == 'CXXMemberCallExpr' and node['inner'][0].get('name', '').startswith('operator '):
            node = node['inner'][0]['inner'][0]  # conversion operator, e.g. __half -> float
        else:
            return node


def decl_name(node):
    """The name a node refers to, or None (also when a conversion on the way is not in qpel)."""
    try:
        node = peel(node)
    except Unsupported:
        return None
    if node['kind'] == 'DeclRefExpr':
        return node['referencedDecl'].get('name')
    return None


def member_name(node, source):
    """Field of a member access. clang's JSON omits Metal vector accessors, so read the last token."""
    if node.get('name'):
        return node['name']
    end = node['range']['end']
    end = end.get('spellingLoc', end)  # inside a macro argument, the token is where it was spelled
    file = end.get('_file')
    if file not in _FILE_BYTES:  # clang's offsets count bytes, in the file the token is in
        _FILE_BYTES[file] = Path(file).read_bytes() if file and Path(file).exists() else source.encode()
    return _FILE_BYTES[file][end['offset']:end['offset'] + end['tokLen']].decode()


_FILE_BYTES = {}


def is_member(node, base, field):
    node = peel(node)
    return (node['kind'] == 'MemberExpr' and node.get('name') == field and decl_name(node['inner'][0]) == base)


TEMPORARIES = ('ImplicitCastExpr', 'MaterializeTemporaryExpr', 'CXXBindTemporaryExpr', 'ExprWithCleanups', 'ParenExpr')


BARRIER_CALLS = {'__syncthreads', '__syncthreads_count', 'threadgroup_barrier', 'qpel_barrier_count'}


def predicate(node):
    """A barrier count's condition without `c ? 1 : 0` and `x != 0` around it (qpel's own
    spellings, and C's bool-to-int promotion): they count the same threads."""
    while True:
        stripped = predicate_once(node)
        if stripped is node:
            return node
        node = stripped


def predicate_once(node):
    core = node
    while core.get('kind') in TEMPORARIES and core.get('inner'):
        core = core['inner'][0]
    if core is not node and scalar_of(core) == 'bool':
        return core  # C's promotion of a bool to int
    if core.get('kind') == 'ConditionalOperator':
        _, yes, no = core['inner']
        if [integer_constant(yes), integer_constant(no)] == [1, 0]:
            return core['inner'][0]
    if core.get('kind') == 'BinaryOperator' and core.get('opcode') == '!=' and integer_constant(core['inner'][1]) == 0:
        left = core['inner'][0]
        while left.get('kind') in TEMPORARIES and left.get('inner'):
            left = left['inner'][0]  # (bool) promoted to int for the comparison
        return left
    return node


def cooperative_rank(node):
    """'grid' for cooperative_groups::this_grid().thread_rank(), 'block' for
    this_thread_block().thread_rank(), else None."""
    while node.get('kind') in TEMPORARIES and node.get('inner'):
        node = node['inner'][0]
    if node.get('kind') != 'CXXMemberCallExpr' or not node.get('inner'):
        return None
    method = node['inner'][0]
    if method.get('kind') != 'MemberExpr' or method.get('name') != 'thread_rank' or not method.get('inner'):
        return None
    call = method['inner'][0]
    while call.get('kind') in TEMPORARIES and call.get('inner'):
        call = call['inner'][0]
    if call.get('kind') != 'CallExpr' or not call.get('inner'):
        return None
    callee = call['inner'][0]
    while callee.get('kind') in TEMPORARIES and callee.get('inner'):
        callee = callee['inner'][0]
    name = (callee.get('referencedDecl') or {}).get('name') if callee.get('kind') == 'DeclRefExpr' else None
    return {'this_grid': 'grid', 'this_thread_block': 'block'}.get(name)


def grid_axis(node):
    """'x', 'y' or 'z' for blockIdx.a * blockDim.a + threadIdx.a (operands in any order), else None.
    cooperative_groups::this_grid().thread_rank() is the x index: the grid rank of a thread is
    the linear index over the whole grid, which for qpel's one-dimensional launch is exactly
    blockIdx.x * blockDim.x + threadIdx.x (Importer.expr refuses it in 2D/3D kernels)."""
    if cooperative_rank(node) == 'grid':
        return 'x'
    node = peel(node)
    if node['kind'] != 'BinaryOperator' or node['opcode'] != '+':
        return None
    for product, thread in (node['inner'], node['inner'][::-1]):
        product = peel(product)
        if product['kind'] != 'BinaryOperator' or product['opcode'] != '*':
            continue
        a, b = product['inner']
        for axis in 'xyz':
            if is_member(thread, 'threadIdx', axis) and (
                    (is_member(a, 'blockIdx', axis) and is_member(b, 'blockDim', axis)) or
                    (is_member(a, 'blockDim', axis) and is_member(b, 'blockIdx', axis))):
                return axis
    return None


FIXED_WIDTH = {'unsigned long long': 'uint64_t', 'unsigned long': 'uint64_t', 'long long': 'int64_t', 'long': 'int64_t'}


def portable_spelling(arguments):
    """Template arguments for calling an original: 64-bit types as uint64_t/int64_t. clang on macOS
    desugars uint64_t to unsigned long long, which is another type on Linux (unsigned long), so
    an overload on uint64_t would no longer match there."""
    return ', '.join(FIXED_WIDTH.get(a.strip(), a.strip()) for a in arguments.split(','))


def is_position_vector(param):
    """A Metal [[thread_position_in_grid]] of type uint2 or uint3."""
    return ' '.join(words(param['type']['qualType'])) in ('uint2', 'uint3')


def is_grid_index(node):
    return grid_axis(node) == 'x'


def walk(node):
    if not node:  # clang writes {} for an absent part, such as a for loop without an init
        return
    yield node
    for child in node.get('inner', ()):
        yield from walk(child)


def attributes(decl):
    return {child['kind']: child for child in decl.get('inner', ()) if child['kind'].endswith('Attr')}


def body_of(decl):
    return next((c for c in decl.get('inner', ()) if c['kind'] == 'CompoundStmt'), None)



LOOSE = {'ImplicitCastExpr', 'ParenExpr', 'CStyleCastExpr', 'CXXStaticCastExpr', 'CXXFunctionalCastExpr',
         'MaterializeTemporaryExpr', 'CXXBindTemporaryExpr', 'ExprWithCleanups'}


def launch_tensor(node):
    """('size', name) for a launch argument `t.size(0)` or `t.numel()`, ('data', name) for
    `t.data_ptr<T>()` (PyTorch's way of passing a tensor's length and its storage), or None."""
    while node.get('kind') in LOOSE | {'CXXReinterpretCastExpr'} and node.get('inner'):
        node = node['inner'][0]
    if node.get('kind') not in ('CXXMemberCallExpr', 'CallExpr') or not node.get('inner'):
        return None
    callee = node['inner'][0]
    if callee.get('kind') not in ('MemberExpr', 'UnresolvedMemberExpr', 'CXXDependentScopeMemberExpr') or not callee.get('inner'):
        return None
    base = callee['inner'][0]
    while base.get('kind') in LOOSE and base.get('inner'):
        base = base['inner'][0]
    if base.get('kind') != 'DeclRefExpr':
        return None
    tensor = base['referencedDecl'].get('name')
    method = callee.get('name') or callee.get('member')
    arguments = node['inner'][1:]
    if method == 'numel' and not arguments:
        return ('size', tensor)
    if method == 'size' and len(arguments) == 1:
        index = arguments[0]
        while index.get('kind') in LOOSE and index.get('inner'):
            index = index['inner'][0]
        if index.get('kind') == 'IntegerLiteral' and index.get('value') == '0':
            return ('size', tensor)
    if method in ('data_ptr', 'data') and not arguments:
        return ('data', tensor)
    return None


def launch_sites(unit):
    """{kernel name as written at the call: [argument lists]} for every `kernel<<<...>>>(...)`."""
    found = {}
    for node in walk(unit):
        if node.get('kind') != 'CUDAKernelCallExpr' or not node.get('inner'):
            continue
        callee = node['inner'][0]
        while callee.get('kind') in LOOSE and callee.get('inner'):
            callee = callee['inner'][0]
        name = callee.get('name') if callee.get('kind') == 'UnresolvedLookupExpr' else \
            callee.get('referencedDecl', {}).get('name') if callee.get('kind') == 'DeclRefExpr' else None
        if name:
            found.setdefault(name, []).append(node['inner'][2:])
    return found


def params_of(decl):
    return [c for c in decl.get('inner', ()) if c['kind'] == 'ParmVarDecl']


BITCAST_CALLS = {'__float_as_uint': 'u32', '__float_as_int': 'i32', '__uint_as_float': 'f32', '__int_as_float': 'f32',
                 'qpel_bits_as_f32': 'f32'}
METAL_EXCHANGES = {'atomic_compare_exchange_weak_explicit'}  # Metal has no strong one


def conditional_exchanges(body):
    """Ids of Metal compare-exchange calls that only some evaluations reach: the right of && or ||,
    a branch of ?:. Moving them before their statement would run them unconditionally."""
    found = set()
    for node in walk(body):
        later = node.get('inner', ())[1:] if (node.get('kind') == 'BinaryOperator' and node.get('opcode') in ('&&', '||')
                                              or node.get('kind') == 'ConditionalOperator') else ()
        for part in later:
            found |= {n['id'] for n in walk(part) if n.get('kind') == 'CallExpr' and
                      decl_name(n['inner'][0]) in METAL_EXCHANGES}
    return found


def own_continue(body):
    """True when a continue in body belongs to the loop around body (not to a loop inside it)."""
    if not body:
        return False
    if body.get('kind') == 'ContinueStmt':
        return True
    if body.get('kind') in ('ForStmt', 'WhileStmt', 'DoStmt'):
        return False
    return any(own_continue(child) for child in body.get('inner', ()))


def atomic_address_base(node):
    """Parameter name behind an atomic address p, p + i or &p[i]."""
    node = peel(node)
    if node['kind'] == 'BinaryOperator' and node['opcode'] == '+':
        return decl_name(node['inner'][0])
    if node['kind'] == 'UnaryOperator' and node['opcode'] == '&':
        inner = peel(node['inner'][0])
        if inner['kind'] == 'ArraySubscriptExpr':
            return decl_name(inner['inner'][0])
    return decl_name(node)


# --- dialects --------------------------------------------------------------

class Dialect:
    name = None
    atomics = {}  # callee -> (IR kind, has value, has memory order)

    def is_kernel(self, decl):
        raise NotImplementedError

    def is_host(self, decl):
        """True for CPU code that a GPU source may also contain."""
        return False

    def binding(self, param, position):
        return position

    def builtin(self, param):
        """'grid' for the global thread index, a name for another thread builtin, else None."""
        return None


class Cuda(Dialect):
    name = 'cuda'
    atomics = {'atomicAdd': ('AtomicAdd', True, False), 'atomicExch': ('AtomicStore', True, False),
               'atomicMin': ('AtomicMin', True, False), 'atomicCAS': ('AtomicCompareExchange', True, False),
               # IR version 6
               'atomicMax': ('AtomicMax', True, False), 'atomicAnd': ('AtomicAnd', True, False),
               'atomicOr': ('AtomicOr', True, False), 'atomicXor': ('AtomicXor', True, False),
               'atomicSub': ('AtomicSub', True, False), 'qpel_load_f32': ('AtomicLoad', False, False)}

    def is_kernel(self, decl):
        return 'CUDAGlobalAttr' in attributes(decl)

    def is_host(self, decl):
        attrs = attributes(decl)
        return 'CUDAGlobalAttr' not in attrs and 'CUDADeviceAttr' not in attrs


class Metal(Dialect):
    name = 'metal'
    atomics = {'atomic_load_explicit': ('AtomicLoad', False, True),
               'atomic_store_explicit': ('AtomicStore', True, True),
               'atomic_fetch_add_explicit': ('AtomicAdd', True, True),
               'atomic_fetch_min_explicit': ('AtomicMin', True, True),
               'qpel_atomic_cas': ('AtomicCompareExchange', True, False),  # qpel's own Metal spelling
               # IR version 6; Metal's 64-bit min and max give no result
               'atomic_fetch_max_explicit': ('AtomicMax', True, True), 'atomic_fetch_and_explicit': ('AtomicAnd', True, True),
               'atomic_fetch_or_explicit': ('AtomicOr', True, True), 'atomic_fetch_xor_explicit': ('AtomicXor', True, True),
               'atomic_exchange_explicit': ('AtomicExchange', True, True), 'atomic_fetch_sub_explicit': ('AtomicSub', True, True),
               'atomic_min_explicit': ('AtomicMin', True, True), 'atomic_max_explicit': ('AtomicMax', True, True),
               'qpel_add_u64': ('AtomicAdd', True, False)}

    def is_kernel(self, decl):
        return 'MetalKernelAttr' in attributes(decl)

    def binding(self, param, position):
        attr = attributes(param).get('MetalBufferIndexAttr')
        return int(attr['inner'][0]['value']) if attr else position

    def builtin(self, param):
        attrs = attributes(param)
        if 'MetalThreadPosGridAttr' in attrs:
            return 'grid'
        for attr, role in GROUP_BUILTINS.items():
            if attr in attrs:
                return role
        other = [k for k in attrs if k.startswith('Metal') and k != 'MetalBufferIndexAttr']
        return other[0] if other else None


DIALECT = {'cuda': Cuda(), 'metal': Metal()}
# Metal threadgroup builtins and CUDA's built-in variables -> the qpel Groups builtin they are.
GROUP_BUILTINS = {'MetalThreadGroupPosGridAttr': 'group', 'MetalThreadPosGroupAttr': 'local',
                  'MetalThreadsPerGroupAttr': 'size', 'MetalThreadGroupsPerGridAttr': 'count'}
CUDA_BUILTINS = {'blockIdx': 'group', 'threadIdx': 'local', 'blockDim': 'size', 'gridDim': 'count'}
HIDDEN_NAMES = {'group': 'block_idx', 'local': 'thread_idx', 'size': 'block_dim', 'count': 'grid_dim'}  # helper parameters
GROUP_NODES = {'group': 'GroupIndex', 'local': 'LocalIndex', 'size': 'GroupSize', 'count': 'GroupCount'}
GROUPS_BLOCK = 64  # group size of a Groups kernel without a launch plan
# IR version 5 subgroup operations: Metal's SIMD-group builtins, and the calls of both languages.
SUBGROUP_BUILTINS = {'MetalThreadIndexSIMDGroupAttr': 'SubgroupLane', 'MetalSIMDGroupIndexGroupAttr': 'SubgroupIndex',
                     'MetalThreadsPerSIMDGroupAttr': 'SubgroupSize'}
SHUFFLE_CALLS = {'__shfl_sync': 'index', '__shfl_xor_sync': 'xor', '__shfl_down_sync': 'down', '__shfl_up_sync': 'up',
                 'simd_shuffle': 'index', 'simd_shuffle_xor': 'xor', 'simd_shuffle_down': 'down', 'simd_shuffle_up': 'up',
                 'qpel_shuffle_index': 'index', 'qpel_shuffle_xor': 'xor', 'qpel_shuffle_down': 'down', 'qpel_shuffle_up': 'up'}
REDUCE_CALLS = {'simd_sum': 'add', 'simd_min': 'min', 'simd_max': 'max', 'simd_and': 'and', 'simd_or': 'or', 'simd_xor': 'xor',
                **{f'qpel_subgroup_{op}': op for op in ('add', 'min', 'max', 'and', 'or', 'xor')}}
SUBGROUP_CALLS = set(SHUFFLE_CALLS) | set(REDUCE_CALLS) | {'qpel_subgroup_lane', 'qpel_subgroup_index', 'qpel_subgroup_size'}
QPEL_GROUP_SUM_PARAMS = {'qpel_lane', 'qpel_group_size'}  # builtins backends/metal.h adds for a workgroup sum
ATOMIC_ELEMENTS = {'u32': 'atomic', 'i32': 'atomic_i32', 'f32': 'atomic_f32', 'u64': 'atomic_u64'}  # element -> scalar of its atomic buffer
ATOMIC_POINTEES = set(ATOMIC_ELEMENTS) | set(ATOMIC_ELEMENTS.values())
LOCAL_ARRAY = re.compile(r'(.*\S)\s*\[([0-9]+)\]')  # `unsigned int [3]`: a local array's type
# CUDA's vector types (int4, uint2, float3, ...): a local of one becomes a local array
VECTOR_TYPE = re.compile(r'(?:const\s+)?(int|uint|float|short|ushort|char|uchar|longlong|ulonglong)([234])')
VECTOR_SCALARS = {'int': 'i32', 'uint': 'u32', 'float': 'f32', 'short': 'i16', 'ushort': 'u16', 'char': 'i8',
                  'uchar': 'u8', 'longlong': 'i64', 'ulonglong': 'u64'}
VECTOR_FIELDS = 'xyzw'
ZERO = {'bool': ('Literal', 'false'), 'u32': ('Literal', '0u'), 'i32': ('I32Literal', '0'), 'f32': ('F32Literal', '0.0')}
GRID3D_BLOCK = (16, 16, 1)  # default block for 2D/3D kernels without a launch plan
MINIMUM = {'min', 'umin'}
MAXIMUM = {'max', 'umax'}
FMA = {'__fmaf_rn', 'fmaf', 'fma'}
TO_F16 = {'__float2half_rn', '__float2half'}
TO_BF16 = {'__float2bfloat16_rn', '__float2bfloat16'}
# C, CUDA and Metal spellings of qpel's math functions (on float arguments).
MATH_CALLS = {name: f for f in ('fabs', 'fmin', 'fmax', 'floor', 'ceil', 'trunc', 'round', 'copysign', 'sqrt',
                                'exp', 'exp2', 'log', 'log2', 'sin', 'cos', 'tanh', 'erf', 'expm1', 'log1p')
              for name in (f, f + 'f')}
MATH_CALLS.update({'abs': 'fabs', '__fsqrt_rn': 'sqrt'})
MATH_CALLS.update({'qpel_' + f: f for f in ('erf', 'expm1', 'log1p')})  # qpel's own Metal versions (backends/metal.h)
ROUNDED = {'__fadd_rn': '+', '__fsub_rn': '-', '__fmul_rn': '*', '__fdiv_rn': '/'}
SATURATE = {f'qpel_f32_to_{t}': t for t in ('u8', 'i8', 'u16', 'i16', 'u32', 'i32', 'u64', 'i64')}
TO_F32 = {'__half2float'}
COMPARISONS = {'==', '!=', '<', '>', '<=', '>='}
BINARY = {'+', '-', '*', '/', '%', '&', '|', '^', '&&', '||', '<<', '>>'} | COMPARISONS


# --- IR graph --------------------------------------------------------------

class Graph:
    def __init__(self):
        self.nodes = {}
        self.count = 0

    def reserve(self):
        self.count += 1
        return f't{self.count}'

    def put(self, ident, kind, attributes=None, children=None, links=None):
        kids = {}
        for role, value in (children or {}).items():
            value = value if isinstance(value, list) else [value]
            if value:
                kids[role] = value
        self.nodes[ident] = {'id': ident, 'kind': kind, 'attributes': attributes or {},
                             'children': kids, 'links': links or {}}
        return ident

    def add(self, kind, attributes=None, children=None, links=None):
        return self.put(self.reserve(), kind, attributes, children, links)

    def program(self, module, pipeline):
        order = []

        def visit(ident):
            order.append(ident)
            for values in self.nodes[ident]['children'].values():
                for child in values:
                    visit(child)
        visit(module)
        visit(pipeline)
        new = {old: f'n{i}' for i, old in enumerate(order)}
        nodes = []
        for old in order:
            n = self.nodes[old]
            nodes.append({'id': new[old], 'kind': n['kind'], 'attributes': n['attributes'],
                          'children': {r: [new[c] for c in v] for r, v in n['children'].items()},
                          'links': {r: new[t] for r, t in n['links'].items()}})
        from qpel_ir import minimum_version, SEMANTICS
        program = dict(IR_HEADER, roots={'module': new[module], 'pipeline': new[pipeline]}, nodes=nodes)
        version = minimum_version(program)  # stay on version 1 unless a version 2 construct is used
        program.update(version=version, semantics=SEMANTICS[version])
        return program


# --- importer --------------------------------------------------------------

class Importer:
    def __init__(self, unit, source, path, dialect, module, survey=False):
        self.dialect = DIALECT[dialect]
        read_enums(unit)  # SUM, MEAN, ...: their values; enum-typed expressions: the underlying integer
        lift_statics(unit)  # Reducer<T, R>::update(...) -> Reducer_f32_0_update(...), __ldg(p) -> *p (statics.py)
        self.local_references = local_pointers_as_references(unit)  # update(&result, ...) of update(T *val, ...): T &val (statics.py)
        inline_facades(unit)  # cub::BlockScan(storage).InclusiveSum(...) -> the function it forwards to
        demote_unit(unit)  # T hy = 1. - ly; and the like: float arithmetic in double's clothes (demote.py)
        self.unit = unit
        self.sites = None  # launch sites of the original's host code, read when first needed
        self.extents = {}  # kernel -> {buffer parameter: shape parameter that sizes it}
        self.views = {}  # kernel -> {buffer parameter: elements per vector it is read as (its scale)}
        self.vector_params = {}  # clang id of a kernel's vector-buffer parameter (int4* p) -> N
        self.grid_params = {}  # kernel -> its shape parameter that is the dispatch grid
        # In a survey every blocker is recorded as (function, error) and the rest of the
        # source is still read; otherwise the first one is raised.
        self.issues = [] if survey else None
        self.current = None
        self.host = []  # CUDA host functions, which are not GPU code
        self.host_spans = []  # their [start, end) byte offsets, for running the device code alone
        self.rejected = set()  # functions that could not be read at all, such as templates
        self.qualified = {}  # function -> name with its namespaces, for calling the original
        self.arguments = {}  # kernel -> how each original argument is fed from the qpel pipeline
        self.namespace = ''
        self.namespace_names = {}  # clang id of a namespace -> its qualified prefix, 'CUDA::'

        def namespaces(nodes, prefix):
            for decl in nodes:
                if decl.get('kind') == 'NamespaceDecl':
                    own = prefix + decl.get('name', '') + '::'
                    self.namespace_names[decl['id']] = own
                    namespaces(decl.get('inner', ()), own)
        namespaces(unit.get('inner', ()), '')
        self.source = source
        self.path = path
        self.module = module
        self.graph = Graph()
        self.functions = {}  # source name -> (decl, IR id, is kernel)
        self.unsigned = 0  # > 0 while reading promoted unsigned chars as unsigned
        self.domains = {}  # kernel name -> 'grid1d' | 'grid3d'
        self.grid3d = False
        self.adaptations = []
        self.instances = {}  # clang id of a template instantiation -> its qpel function name
        self.bool_params = set()  # clang ids of bool kernel arguments, passed as u32
        self.records = records(unit)  # struct name -> its clang record, for argument structs
        self.structs = {}  # struct name -> [(field, type term)] used by imported functions
        self.function_constants = {}  # clang id -> name of a Metal [[function_constant]] variable
        self.globals = {}  # clang id -> name of another program-scope variable
        self.global_values = {}  # clang id -> (name, initializer) of a program-scope scalar constant
        self.taken = {}  # function name -> clang id of the declaration that has it
        self.constants = {}  # name -> value, set by import_source(constants=...)
        self.shared_lengths = {}  # threadgroup parameter -> elements, set by import_source(shared=...)
        self.files = {}  # path -> text, for reading attributes clang's JSON leaves out
        self.function_decls = {d['id']: d for d in walk(unit) if d.get('kind') == 'FunctionDecl' and body_of(d)}
        main = list(clang_ast.main_file_decls(unit, path))
        # Device functions defined in the library's own headers (llama.cpp's common.cuh) are
        # imported when the file's GPU code reaches them; system headers never reach here.
        self.reachable = cached_reachable(unit, path, main, self.dialect)
        self.header = False
        decls = []
        main_ids = {id(node) for node in main}
        self.header_host_bodies = header_host_bodies(unit, main_ids, self.dialect)
        for node in unit.get('inner', ()):
            if id(node) in main_ids:
                self.header = False
                decls.extend(self.attempt(lambda: self.top_level(node), []))
            elif node.get('kind') == 'FunctionDecl' and VECTOR_TYPE.fullmatch(node.get('name', '')[5:]) and \
                    node.get('name', '').startswith('make_'):
                continue  # CUDA's make_int4 and friends: vector locals read them as element lists
            elif not node.get('isImplicit') and contains_any(node, self.reachable):
                self.header = True
                decls.extend(self.attempt(lambda: self.top_level(node), []))
        self.header = False

        def header_constants(nodes):  # `int const threadsPerBlock = 64;` in a library header
            for node in nodes:
                if node.get('kind') in ('NamespaceDecl', 'LinkageSpecDecl'):
                    header_constants(node.get('inner', ()))
                elif node.get('kind') == 'VarDecl' and id(node) not in main_ids and 'init' in node and node.get('inner'):
                    t = describe(node['type']['qualType'])
                    if t[0] == 'scalar' and t[1] in SCALAR_TERMS and 'const' in node['type']['qualType'].split():
                        self.global_values.setdefault(node['id'], (node['name'], node['inner'][0]))
        header_constants(unit.get('inner', ()))
        headers = [d['name'] for d in decls if d.get('_header')]
        if headers:
            self.adaptations.append('device functions from included headers were imported: ' + ', '.join(headers))
        self.decls = decls

    def attempt(self, read, fallback=None):
        """read(), or in a survey record its Unsupported and return fallback."""
        if self.issues is None:
            return read()
        try:
            return read()
        except Unsupported as error:
            self.issues.append((self.current, error))
            return fallback

    def constant_value(self, name, scalar, node):
        """A Metal function constant, whose value the caller of the import gives."""
        if name not in self.constants:
            raise Unsupported(node, 'function-constant', f'{name} is a Metal function constant; '
                                                         'import with constants={...} to specialise it')
        value = self.constants[name]
        message = f'function constant {name} = {value!r}'
        if message not in self.adaptations:
            self.adaptations.append(message)
        if scalar == 'bool':
            return self.graph.add('Literal', {'value': 'true' if value else 'false'})
        if scalar in INTEGER_TERMS:
            if scalar == 'i32' and value >= 0:
                return self.graph.add('I32Literal', {'value': str(value)})
            if scalar == 'u32':
                return self.graph.add('Literal', {'value': f'{value % 2**32}u'})
            return self.constant(node, scalar, value)
        raise Unsupported(node, 'function-constant', f'{name}: function constants of type {scalar} are not in qpel yet')

    def struct_type(self, name, where):
        """The qpel struct for a C struct argument of scalar fields (ggml's kargs)."""
        if name not in self.structs:
            fields = []
            for f in self.records[name].get('inner', ()):
                if f.get('kind') != 'FieldDecl':
                    continue
                t = describe(f['type']['qualType'])
                if t[0] != 'scalar' or t[1] not in SCALAR_TERMS:
                    raise Unsupported(where, 'struct-field', f'struct {name}: field {f.get("name")} of type '
                                                             f'{f["type"]["qualType"]} is not in qpel yet')
                fields.append((self.safe_name(f['name']), t[1]))
            self.structs[self.safe_name(name)] = fields
        return 'struct ' + self.safe_name(name)

    def template_arguments(self, decl):
        """The template arguments of an instantiation, as source text."""
        out = []
        for c in decl.get('inner', ()):
            if c['kind'] != 'TemplateArgument':
                continue
            parts = c['inner'] if c.get('isPack') else [c]
            for part in parts:
                if 'decl' in part:
                    out.append(part['decl'].get('name', '?'))
                elif 'type' in part:
                    out.append(part['type']['qualType'])
                elif 'value' in part:
                    out.append(str(part['value']))
                else:
                    out.append('?')
        return out

    def spelled_arguments(self, use, template):
        """template_arguments for calling the instance from C++: an enum value (clang's JSON gives only
        the number) is cast to its parameter's type, `(cub::BlockScanAlgorithm)0`, and a bool is true or false."""
        params = [p for p in template.get('inner', ()) if p.get('kind') in
                  ('TemplateTypeParmDecl', 'NonTypeTemplateParmDecl', 'TemplateTemplateParmDecl')]
        out = self.template_arguments(use)
        if any(p.get('isParameterPack') for p in params) or len(params) != len(out):
            return out
        for i, p in enumerate(params):
            kind = describe(p.get('type', {}).get('qualType', ''))
            if p['kind'] == 'NonTypeTemplateParmDecl' and kind[0] == 'scalar' and kind[1] not in SCALAR_TERMS \
                    and re.fullmatch(r'-?[0-9]+', out[i]):
                out[i] = f'({p["type"]["qualType"]}){out[i]}'
            elif p['kind'] == 'NonTypeTemplateParmDecl' and kind == ('scalar', 'bool') and re.fullmatch(r'-?[0-9]+', out[i]):
                # clang's JSON gives true as -1 (a one-bit value) and 1 converts to no bool argument
                out[i] = 'false' if out[i] == '0' else 'true'
        return out

    def host_name(self, decl):
        """The name a Metal explicit instantiation gives a kernel: [[host_name("...")]]."""
        for c in decl.get('inner', ()):
            if c['kind'] == 'MetalHostNameAttr':
                begin = c['range']['begin']
                file = begin.get('_file')
                if file not in self.files:
                    self.files[file] = Path(file).read_bytes()  # clang's offsets count bytes, not characters
                text = self.files[file][begin['offset']:begin['offset'] + 300].decode('utf-8', 'replace')
                found = re.match(r'host_name\s*\(\s*"([^"]+)"', text)
                if found:
                    return found.group(1)
        return None

    def top_level(self, node, qualified=True):
        kind = node['kind']
        if kind == 'LinkageSpecDecl':
            return [d for child in node.get('inner', ()) for d in self.top_level(child)]
        if kind == 'NamespaceDecl':
            # qpel has one flat module; function names must still be unique, which run() checks.
            outer = self.namespace
            self.namespace = outer + node.get('name', '') + '::'
            try:
                found = [d for child in node.get('inner', ()) for d in self.attempt(lambda: self.top_level(child), [])]
            finally:
                self.namespace = outer
            message = f'namespace {node.get("name", "(anonymous)")} was flattened'
            if found and message not in self.adaptations:  # only where it held functions qpel imports
                self.adaptations.append(message)
            return found
        if kind == 'FunctionTemplateDecl':
            pattern = next((d for d in node.get('inner', ()) if d['kind'] == 'FunctionDecl'), None)
            if pattern is not None and self.dialect.is_host(pattern):
                if not self.header:  # CPU code: blank the whole template, `template <...>` included
                    self.host.append(node['name'])
                    self.host_spans.append(clang_ast.span(node))
                return []
            uses = [d for d in node.get('inner', ()) if d['kind'] == 'FunctionDecl' and body_of(d) is not None
                    and any(c['kind'] == 'TemplateArgument' for c in d.get('inner', ()))]
            if self.header:
                uses = [d for d in uses if d['id'] in self.reachable]
            if not uses:
                return []  # never instantiated, so never called
            if len(uses) == 1 and not self.host_name(uses[0]):
                arguments = ', '.join(self.template_arguments(uses[0]))
                self.adaptations.append(f'template {node["name"]} is used only with <{arguments}> and was imported for it')
                self.qualified[node['name']] = (f'{self.namespace}{node["name"]}'
                                                f'<{portable_spelling(", ".join(self.spelled_arguments(uses[0], node)))}>')
                return self.top_level(uses[0], qualified=False)
            # Each instantiation is its own function: qpel has no templates.
            out = []
            taken = set()
            for use in uses:
                arguments = self.template_arguments(use)
                name = self.host_name(use) or instance_name(node['name'], arguments)
                while name in taken:
                    name += '_'
                taken.add(name)
                self.instances[use['id']] = name
                instance = dict(use, name=name)
                imported = self.attempt(lambda: self.top_level(instance, qualified=False), [])
                if imported:
                    spelled = ', '.join(self.spelled_arguments(use, node))
                    self.qualified[name] = self.host_name(use) or f'{self.namespace}{node["name"]}<{portable_spelling(spelled)}>'
                out.extend(imported)
            self.adaptations.append(f'template {node["name"]} became {len(uses)} functions, one per instantiation: '
                                    + ', '.join(self.instances[u['id']] for u in uses))
            return out
        if kind == 'FunctionDecl':
            if body_of(node) is None or node.get('name', '').startswith('qpel_'):
                return []  # declarations and qpel backend helpers
            if self.header and node['id'] not in self.reachable:
                return []
            if self.dialect.is_host(node):
                if not self.header:
                    self.host.append(node['name'])
                    self.host_spans.append(clang_ast.span(node))
                return []
            if node['name'] in self.taken and self.taken[node['name']] != node['id'] and node['id'] not in self.instances:
                # an overload: its own function, named by its parameter types; calls resolve by declaration
                name = instance_name(node['name'], [p['type']['qualType'] for p in params_of(node)])
                while name in self.taken:
                    name += '_'
                self.instances[node['id']] = name
                self.adaptations.append(f'overload {node["name"]}({", ".join(p["type"]["qualType"] for p in params_of(node))}) became {name}')
                node = dict(node, name=name)
            self.taken.setdefault(node['name'], node['id'])
            if qualified:
                # an out-of-line definition (`void CUDA::k(...)`) names its namespace by id
                prefix = self.namespace or self.namespace_names.get(node.get('parentDeclContextId'), '')
                self.qualified[node['name']] = prefix + node['name']
            if self.header:
                node = dict(node, _header=True)
            return [node]
        if kind in ('StaticAssertDecl', 'UsingDirectiveDecl', 'UsingDecl', 'NamespaceAliasDecl', 'TypedefDecl') or \
                (kind == 'CXXRecordDecl' and node.get('name') == 'QpelShape'):
            return []
        if self.header:
            return []  # header types and variables matter only through the functions that use them
        if kind == 'VarDecl' and 'MetalFunctionConstantAttr' in attributes(node):
            self.function_constants[node['id']] = node['name']  # resolved at import, see constant_value()
            return []
        if kind in ('CXXRecordDecl', 'RecordDecl', 'TypedefDecl', 'EnumDecl', 'TypeAliasDecl', 'TypeAliasTemplateDecl',
                    'ClassTemplateDecl', 'ClassTemplateSpecializationDecl', 'ClassTemplatePartialSpecializationDecl'):
            return []  # types are read where parameters use them; their static methods are functions (statics.py)
        if kind == 'VarDecl':
            t = describe(node['type']['qualType'])
            init = node['inner'][0] if 'init' in node and node.get('inner') else None
            if t[0] == 'scalar' and t[1] in SCALAR_TERMS and init is not None and 'const' in node['type']['qualType'].split():
                self.global_values[node['id']] = (node['name'], init)  # a named constant: read as its value
            else:
                self.globals[node['id']] = node['name']  # lookup tables and the like: an error only where used
            return []
        raise Unsupported(node, 'top-level', f'{kind} {node.get("name", "")} outside a function is not supported')

    # -- parameter types, with atomic usage inferred across calls --

    def parameter_types(self, plan_groups):
        """Return {(function, index): IR type kind} for every parameter.

        CUDA spells atomic buffers as plain `unsigned int*`, so a u32 pointer is
        atomic when it is used by an atomic call anywhere it flows (through helper
        calls, or a pipeline resource shared between kernels)."""
        parent = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)
        index = {}
        atomic = set()
        for decl in self.decls:
            fname = decl['name']
            params = params_of(decl)
            for i, p in enumerate(params):
                index[(fname, p['name'])] = (fname, i)
                t = describe(p['type']['qualType'])
                if t[0] == 'pointer' and t[2] in ATOMIC_ELEMENTS.values():
                    atomic.add((fname, i))
                if self.dialect.is_kernel(decl) and plan_groups is not None:  # a survey reads kernels independently
                    group = plan_groups.get((fname, p['name']), p['name'])
                    union((fname, i), ('resource', group))
        for decl in self.decls:
            fname = decl['name']
            for node in walk(body_of(decl)):
                if node['kind'] != 'CallExpr':
                    continue
                callee = self.callee(node)
                args = node['inner'][1:]
                if callee in self.dialect.atomics and args:
                    base = atomic_address_base(args[0])
                    if (fname, base) in index:
                        atomic.add(index[(fname, base)])
                elif callee in self.functions:
                    for j, arg in enumerate(args):
                        name = decl_name(arg)
                        if (fname, name) in index:
                            union(index[(fname, name)], (callee, j))
        atomic_groups = {find(slot) for slot in atomic}
        self.shared_slots, self.device_slots = self.pointer_spaces(index)
        types = {}
        for decl in self.decls:
            for i, p in enumerate(params_of(decl)):
                if self.dialect.is_kernel(decl) and self.dialect.builtin(p):
                    continue  # thread builtins are not kernel arguments
                t = describe(p['type']['qualType'])
                self.current = decl['name']
                types[(decl['name'], i)] = self.attempt(lambda: self.parameter_type(decl, i, p, find, atomic_groups))
        return types

    def pointer_spaces(self, index):
        """({(helper, i)}, {(helper, i)}): CUDA helper pointer parameters that calls pass shared memory
        (a __shared__ array, a pointer into one, or such a parameter), and those they pass anything else.
        CUDA spells both `T*`; qpel's helper parameter is shared<T> for the first."""
        shared_decls = {n['id'] for n in walk(self.unit) if n.get('kind') == 'VarDecl' and 'CUDASharedAttr' in attributes(n)}
        params = {(d['name'], i): p['id'] for d in self.decls for i, p in enumerate(params_of(d))}
        shared, device = set(), set()

        def base(arg):
            try:
                return base_of(arg)
            except Unsupported:
                return None  # a conversion qpel reports where the argument is read

        def base_of(arg):
            node = peel(arg)
            while node.get('kind') == 'BinaryOperator' and node.get('opcode') in ('+', '-'):
                left, right = node['inner']
                node = peel(left if 'pointer' in (scalar_of(left), describe(left['type']['qualType'])[0]) else right)
            if node.get('kind') == 'UnaryOperator' and node.get('opcode') == '&' and node.get('inner'):
                node = peel(node['inner'][0])
                while node.get('kind') == 'ArraySubscriptExpr':
                    node = peel(node['inner'][0])
            return node.get('referencedDecl', {}).get('id') if node.get('kind') == 'DeclRefExpr' else None
        changed = True
        while changed:  # shared through helpers that pass their own parameters on
            changed = False
            shared_ids = shared_decls | {params[s] for s in shared if s in params}
            for decl in self.decls:
                for node in walk(body_of(decl)):
                    if node['kind'] != 'CallExpr':
                        continue
                    callee = self.callee(node)
                    if callee not in self.functions or self.functions[callee][2]:
                        continue
                    for j, arg in enumerate(node['inner'][1:]):
                        if describe(arg.get('type', {}).get('qualType', ''))[0] != 'pointer':
                            continue
                        slot = (callee, j)
                        space = shared if base(arg) in shared_ids else device
                        if slot not in space:
                            space.add(slot)
                            changed = True
        return shared, device

    def parameter_type(self, decl, i, p, find, atomic_groups):
        t = describe(view_spelling(p))
        slot = (decl['name'], i)
        if t[0] == 'pointer' and not self.dialect.is_kernel(decl) and slot in getattr(self, 'shared_slots', ()) and \
                slot in getattr(self, 'device_slots', ()):
            raise Unsupported(p, 'parameter-type', f'{p["name"]} is passed shared memory at one call and device memory at '
                                                   'another; a qpel helper parameter is one or the other')
        if t[0] == 'pointer' and ('threadgroup' in p['type']['qualType'].replace('*', ' ').split()
                                  or 'qpel_shared_view<' in p['type']['qualType']
                                  or slot in getattr(self, 'shared_slots', ())) and not self.dialect.is_kernel(decl):
            if t[2] not in SCALAR_TERMS - {'bool'} and not is_vector(t[2]):
                raise Unsupported(p, 'parameter-type', f'parameter type {p["type"]["qualType"]} is not in qpel yet')
            return f'shared<{t[2]}>'  # a helper's view into the caller's group memory (IR version 5)
        if t[0] == 'pointer':
            if find((decl['name'], i)) in atomic_groups or t[2] in ATOMIC_ELEMENTS.values():
                scalar = t[2] if t[2] in ATOMIC_ELEMENTS else ATOMIC_SCALARS.get(t[2])
                if scalar is None:
                    if t[2] == 'i64':
                        raise Unsupported(p, 'atomic-type', f'atomics on {p["type"]["qualType"]} are not portable: Metal\'s '
                                                            '64-bit atomics are unsigned')
                    raise Unsupported(p, 'atomic-type', f'atomics on {p["type"]["qualType"]} are not in qpel; only on '
                                                        'unsigned int, int, float and unsigned 64-bit')
                return type_buffer(scalar, 'atomic')
            if is_vector(t[2]):
                # int4* bboxes: a buffer of the vector's scalars, N per element of its extent
                if not self.dialect.is_kernel(decl):
                    raise Unsupported(p, 'buffer-type', f'{p["type"]["qualType"]} helper arguments are not in qpel yet')
                self.vector_params[p['id']] = vector_length(t[2])
                if self.views.setdefault(decl['name'], {}).setdefault(self.safe_name(p['name']), vector_length(t[2])) != vector_length(t[2]):
                    raise Unsupported(p, 'buffer-type', f'{p["name"]} is read as vectors of two lengths')
                return type_buffer(vector_element(t[2]), 'read' if t[1] else 'write')
            if t[2] == 'bool':  # one byte per element on both platforms: a u8 buffer holding 0 or 1
                message = f'{decl["name"]}: {p["name"]} (bool *) became a u8 buffer of 0 and 1'
                if message not in self.adaptations:
                    self.adaptations.append(message)
                return type_buffer('u8', 'read' if t[1] else 'write')
            if t[2] not in SCALAR_TERMS:
                raise Unsupported(p, 'buffer-type', f'{p["type"]["qualType"]} buffers are not in qpel yet')
            if t[2] == 'u8' and not t[1] and p['name'] not in written_buffers(body_of(decl)):
                self.adaptations.append(f'{decl["name"]}: {p["name"]} is never written, so it became read-only')
                return 'buffer<u8, read>'
            return type_buffer(t[2], 'read' if t[1] else 'write')
        if t[0] == 'shape':
            return 'shape3'
        if t[0] == 'scalar' and t[1] in SCALAR_TERMS:
            return t[1]
        if t[0] == 'scalar' and is_vector(t[1]):
            if self.dialect.is_kernel(decl):
                raise Unsupported(p, 'parameter-type', f'vector kernel arguments ({p["type"]["qualType"]}) are not in qpel yet')
            return t[1]
        if t[0] == 'scalar' and t[1] in self.records:
            return self.struct_type(t[1], p)
        raise Unsupported(p, 'parameter-type', f'parameter type {p["type"]["qualType"]} is not in qpel yet')

    # -- functions --

    def register(self):
        unique = []
        for decl in self.decls:
            self.current = decl['name']
            if decl['name'] in self.functions:
                self.attempt(lambda: self.reject(Unsupported(decl, 'overload', 'overloaded or repeated function ' + decl['name'])))
                continue
            self.functions[decl['name']] = (decl, self.graph.reserve(), self.dialect.is_kernel(decl))
            unique.append(decl)
        self.decls = unique
        for entry in self.local_references:
            if entry.split('(')[0] in self.functions:
                self.adaptations.append(f'{entry}: a pointer parameter every call passes the address of a local '
                                        'was read as a reference to it')

    @staticmethod
    def reject(error):
        raise error

    def survey(self):
        """Read every function, recording each blocker instead of stopping at the first.

        Returns {function: {'kind': 'kernel' | 'device', 'issues': [...], 'calls': [...]}};
        CUDA host functions are listed with kind 'host'."""
        self.register()
        types = self.parameter_types(None)
        self.kernel_params = {}
        for decl in self.decls:
            self.current = decl['name']
            self.attempt(lambda: self.function(decl, types))
        results = {name: {'kind': 'host', 'issues': [], 'calls': []} for name in self.host}
        for decl in self.decls:
            calls = sorted({self.callee(n) for n in walk(body_of(decl)) if n['kind'] == 'CallExpr'}
                           & (set(self.functions) | self.rejected))
            results[decl['name']] = {'kind': 'kernel' if self.functions[decl['name']][2] else 'device',
                                     'issues': [], 'calls': calls}
        for function, error in self.issues:
            if error.code == 'untyped':
                continue  # a consequence of a parameter blocker that is already listed
            kind = 'template' if function in self.rejected else 'other'
            entry = results.setdefault(function or '(file)', {'kind': kind, 'issues': [], 'calls': []})
            entry['issues'].append({'code': error.code, 'at': error.location, 'message': error.args[0].split('] ', 1)[1]})
        return results

    def select(self, kernels):
        """Keep only these kernels and the device functions they reach."""
        by_name = {d['name']: d for d in self.decls}
        missing = [k for k in kernels if k not in by_name or not self.dialect.is_kernel(by_name[k])]
        if missing:
            raise ValueError(f'{", ".join(missing)}: not kernels in {self.path}')
        needed, stack = set(), list(kernels)
        while stack:
            name = stack.pop()
            if name in needed or name not in by_name:
                continue
            needed.add(name)
            stack.extend(self.callee(n) for n in self.live(body_of(by_name[name])) if n.get('kind') == 'CallExpr')
        self.decls = [d for d in self.decls if d['name'] in needed]
        # report only the templates and headers that the selected kernels use
        self.adaptations = [a for a in self.adaptations
                            if not a.startswith(('template ', 'overload ', 'device functions from included headers'))
                            or any(re.search(r'\b' + re.escape(n) + r'\b', a) for n in needed)]

    def static_value(self, node):
        """The value of a condition that function constants decide (`FC_OP == 10`), else None.
        Only conditions that mention a function constant are folded."""
        if not self.constants or not any(n.get('kind') == 'DeclRefExpr' and n['referencedDecl']['id'] in self.function_constants
                                         for n in walk(node)):
            return None

        def value(n):
            n = strip_parens(n)
            kind = n['kind']
            if kind in PASSTHROUGH and n.get('inner'):
                return value(n['inner'][0])
            if kind == 'IntegerLiteral':
                return int(n['value'])
            if kind == 'CXXBoolLiteralExpr':
                return int(n['value'])
            if kind == 'DeclRefExpr':
                name = self.function_constants.get(n['referencedDecl']['id'])
                return int(self.constants[name]) if name in self.constants else None
            if kind == 'UnaryOperator' and n['opcode'] in ('!', '-'):
                v = value(n['inner'][0])
                return None if v is None else (int(not v) if n['opcode'] == '!' else -v)
            if kind == 'BinaryOperator':
                a, b = (value(x) for x in n['inner'])
                op = n['opcode']
                if op == '&&' and a == 0 or op == '||' and a not in (0, None):
                    return int(op == '||')
                if a is None or b is None:
                    return None
                ops = {'==': a == b, '!=': a != b, '<': a < b, '<=': a <= b, '>': a > b, '>=': a >= b,
                       '&&': bool(a and b), '||': bool(a or b), '+': a + b, '-': a - b, '*': a * b}
                return int(ops[op]) if op in ops else None
            return None
        v = value(node)
        return None if v is None else bool(v)

    def live(self, node):
        """walk(), skipping branches that function constants rule out."""
        if not node:
            return
        yield node
        if node.get('kind') == 'IfStmt' and not node.get('hasInit') and not node.get('hasVar'):
            cond, then, *otherwise = node['inner']
            known = self.static_value(cond)
            if known is not None:
                chosen = then if known else (otherwise[0] if otherwise else None)
                if chosen is not None:
                    yield from self.live(chosen)
                return
        for child in node.get('inner', ()):
            yield from self.live(child)

    def run(self, plan=None, block_size=64, kernels=None):
        if kernels:
            self.select(kernels)
        self.register()
        plan_groups = {}
        if plan:
            for stage in plan['stages']:
                for b in stage['bindings']:
                    plan_groups[(stage['kernel'], b['parameter'])] = b['resource']
        types = self.parameter_types(plan_groups)
        self.kernel_params = {}
        function_ids = []
        for decl in self.decls:
            self.current = decl['name']
            function_ids.append(self.function(decl, types))
        structs = [self.graph.add('StructDecl', {'name': name},
                                  {'fields': [self.graph.add('Field', {'name': f}, {'type': self.type_node(t)}) for f, t in fields]})
                   for name, fields in self.structs.items()]
        module = self.graph.add('GpuModule', {'name': self.module}, {'functions': function_ids, 'structs': structs})
        pipeline = self.pipeline(module, plan, block_size)
        return self.graph.program(module, pipeline)

    def function(self, decl, types):
        name = decl['name']
        _, ident, kernel = self.functions[name]
        self.scope = {}
        self.kernel = kernel
        self.assigned = assigned_decls(body_of(decl))
        self.names = {self.safe_name(p['name']) for p in params_of(decl) if p.get('name')}  # unique per function
        self.conditional_exchanges = conditional_exchanges(body_of(decl))
        # optional buffers (IR version 9): kernel pointer parameters the body tests against null, and
        # (qpel's own Metal read back) the buffers whose presence flag qpel_present_<name> it receives
        tested = {t[0] for t in (null_tested(x) for x in walk(body_of(decl))) if t} if kernel else set()
        flags = {p['name'][len('qpel_present_'):]: p['id'] for p in params_of(decl)
                 if kernel and p.get('name', '').startswith('qpel_present_')}
        optional_ids = tested | {p['id'] for p in params_of(decl) if p.get('name') in flags}
        params = []
        position = 0
        shape_param = None
        self.groups = kernel and self.uses_groups(decl)
        self.grid3d = kernel and not self.groups and self.uses_grid3d(decl)
        self.dynamic_shared = []  # threadgroup parameters made shared memory (threadgroup_parameter)
        count = self.attempt(lambda: self.element_count(decl, types)) if kernel and not self.grid3d and not self.groups else None
        sizes = self.launch_sizes(decl, types) if count is not None else {}
        sized = {p['id']: p for p in params_of(decl) if p['name'] in sizes and p['id'] != count['id']}
        for i, p in enumerate(params_of(decl)):
            builtin = self.dialect.builtin(p) if kernel else None
            if builtin in GROUP_NODES and self.groups:
                vector = ' '.join(words(p['type']['qualType'])) not in ('uint', 'ushort', 'unsigned int', 'unsigned short')
                self.scope[p['id']] = ('group-builtin', builtin, vector)
                continue
            if builtin == 'grid' and self.groups:
                self.scope[p['id']] = ('position',) if is_position_vector(p) else ('grid', 'x')
                continue
            if builtin in SUBGROUP_BUILTINS:
                self.scope[p['id']] = ('subgroup-builtin', SUBGROUP_BUILTINS[builtin], scalar_of(p))
                continue
            if builtin == 'grid':
                vector = is_position_vector(p)
                self.scope[p['id']] = ('position',) if vector else ('grid', 'x')
                continue
            if builtin == 'MetalLocalIndexAttr':  # threadgroup T* p [[threadgroup(n)]]: sized by the host
                self.threadgroup_parameter(p, decl)
                continue
            if builtin:
                self.scope[p['id']] = ('builtin', p['name'])
                continue
            if kernel and p.get('name', '').startswith('qpel_present_') and p['name'][len('qpel_present_'):] in \
                    {q.get('name') for q in params_of(decl)}:
                self.scope[p['id']] = ('presence', p['name'][len('qpel_present_'):])  # qpel's Metal presence flag
                continue
            kind = types[(name, i)]
            if kind is None:  # a survey already recorded why this parameter has no qpel type
                self.scope[p['id']] = ('untyped', p['name'])
                continue
            attrs = {'name': self.safe_name(p['name'])}
            if is_reference(p['type']['qualType']):
                if kernel:
                    self.attempt(lambda: self.reject(Unsupported(p, 'reference', 'kernel arguments cannot be references')))
                attrs['reference'] = True  # the caller's variable, written in place (IR version 4)
            if kernel:
                attrs.update(binding=self.dialect.binding(p, position), isBuffer=True)
                if p['id'] in optional_ids and type_is_buffer(kind):
                    attrs['optional'] = True  # the caller may pass no buffer (IR version 9)
                if kind == 'shape3':
                    shape_param = p
                elif count is not None and p['id'] == count['id'] or p['id'] in sized:
                    kind = 'shape3'
                elif kind == 'bool':
                    # `const bool aligned`: a u32 argument (0 or 1, as C passes it), read as `aligned != 0u`
                    kind = 'u32'
                    self.bool_params.add(p['id'])
                    message = f'{name}: bool argument {p["name"]} became a u32 argument, read as {p["name"]} != 0'
                    if message not in self.adaptations:
                        self.adaptations.append(message)
                elif not type_is_buffer(kind) and kind not in VALUE_TERMS and not is_struct(kind):
                    self.attempt(lambda: self.reject(Unsupported(
                        p, 'scalar-kernel-argument', 'kernel arguments can be integers or float')))
            position += 1
            ir = self.graph.add('Parameter', attrs, {'type': self.type_node(kind)})
            self.scope[p['id']] = ('param', ir, kind)
            params.append(ir)
        self.hidden_params = {}
        if not kernel:
            for role, axis in self.builtin_reads(name):
                hidden = self.unique_name(HIDDEN_NAMES[role] + '_' + axis)
                ir = self.graph.add('Parameter', {'name': hidden}, {'type': self.type_node('u32')})
                self.hidden_params[(role, axis)] = ir
                params.append(ir)
            if self.hidden_params:
                self.adaptations.append(f'{name} reads thread builtins, which a qpel helper is passed: it takes '
                                        + ', '.join(self.graph.nodes[p]['attributes']['name'] for p in
                                                    self.hidden_params.values()) + ' as parameters')
        if kernel and self.groups:
            shape_param = True  # sizes stay ordinary arguments; the plan sizes the buffers
        if kernel and self.grid3d and shape_param is None and not any(
                e and e[0] == 'untyped' for e in self.scope.values()):
            extents = self.attempt(lambda: self.axis_extents(decl, params))
            if extents:
                shape_param = self.extent_shape(decl, params, extents)
        if kernel:
            if count is not None:
                entry = self.scope[count['id']]
                index = next(i for i, p in enumerate(params_of(decl)) if p['id'] == count['id'])
                self.scope[count['id']] = ('count', entry[1], types[(name, index)])
                self.adaptations.append(f'{name}: count {count["name"]} became a shape (count, 1, 1)')
                self.grid_params[name] = self.safe_name(count['name'])
                for p in sized.values():
                    entry = self.scope[p['id']]
                    index = next(i for i, q in enumerate(params_of(decl)) if q['id'] == p['id'])
                    self.scope[p['id']] = ('count', entry[1], types[(name, index)])
                    self.adaptations.append(f'{name}: {p["name"]}, the length of {", ".join(sorted(sizes[p["name"]]))} at the '
                                            f'launch, became a shape ({p["name"]}, 1, 1) that sizes {"it" if len(sizes[p["name"]]) == 1 else "them"}')
                self.extents[name] = {self.safe_name(b): self.safe_name(scalar) for scalar, buffers in sizes.items()
                                      for b in buffers}
            elif shape_param is None:
                self.attempt(lambda: self.reject(Unsupported(
                    decl, 'no-extent', 'a 2D/3D kernel needs a uint3 shape argument' if self.grid3d else
                    'a kernel needs a uint3 shape, or an unsigned element count that the thread index is compared with')))
            self.kernel_params[name] = {self.graph.nodes[p]['attributes']['name']: p for p in params}
            self.arguments[name] = self.argument_map(decl)
        result = describe(decl['type']['qualType'].split('(')[0])
        if result[0] != 'scalar' or result[1] not in SCALAR_TERMS | {'void'} and not (is_vector(result[1]) and not kernel):
            self.attempt(lambda: self.reject(Unsupported(decl, 'result-type', 'unsupported result type ' + decl['type']['qualType'])))
            result = ('scalar', 'void')
        self.hoisted, self.depth = list(self.dynamic_shared), 0
        body = self.block(body_of(decl))
        statements = self.graph.nodes[body]['children'].get('statements', [])
        shared = self.hoisted + [x for x in statements if self.graph.nodes[x]['kind'] == 'Variable' and
                                 self.graph.nodes[self.graph.nodes[x]['children']['type'][0]]['kind'] == 'SharedType']
        rest = [x for x in statements if x not in shared]
        if len(rest) == 1 and self.graph.nodes[rest[0]]['kind'] == 'Block':
            # `{ { ... } }`, the shape qpel's own output has (shared memory declared before the inner block)
            inner = self.graph.nodes[rest[0]]['children'].get('statements', [])
            body = self.graph.add('Block', children={'statements': shared + inner}) if shared else rest[0]
        elif shared:  # shared memory first: where it is declared does not matter, and qpel's output hoists it
            self.graph.nodes[body]['children']['statements'] = shared + rest
        own = self.safe_name(name)
        if own in METAL_FUNCTIONS:  # normalize, dot, ...: Metal's own would make calls ambiguous
            own = own + '_'
            self.adaptations.append(f'{name} is a Metal library function name and was renamed {own}')
        attrs = {'name': own, 'isKernel': True} if kernel else {'name': own}
        children = {'parameters': params, 'resultType': self.type_node(result[1]), 'body': body}
        if kernel:
            children['domain'] = self.graph.add('Groups' if self.groups else 'Grid3D' if self.grid3d else 'Grid1D')
            self.domains[name] = 'groups' if self.groups else 'grid3d' if self.grid3d else 'grid1d'
        return self.graph.put(ident, 'Function', attrs, children)

    def launch_sizes(self, decl, types):
        """{integer parameter: {buffer parameters}} from the original's launch sites: an argument
        `t.size(0)` (or `t.numel()`) is the length of the buffer passed as `t.data_ptr<T>()` in
        the same launch. PyTorch extensions pass sizes this way; a buffer sized by no argument
        is sized later by the buffers it is indexed with (Importer.shared_extents)."""
        if self.dialect.name != 'cuda':
            return {}
        if self.sites is None:
            self.sites = launch_sites(self.unit)
        written = re.sub(r'<.*', '', self.qualified.get(decl['name'], decl['name'])).rpartition('::')[2]
        params = [(i, p) for i, p in enumerate(params_of(decl)) if not self.dialect.builtin(p)]
        found = {}
        for arguments in self.sites.get(written, ()):
            if len(arguments) != len(params):
                continue
            tensors = {}
            for (i, p), argument in zip(params, arguments):
                use = launch_tensor(argument)
                if use:
                    tensors.setdefault(use[1], {})[use[0]] = (i, p['name'])
            for use in tensors.values():
                if 'size' in use and 'data' in use and types.get((decl['name'], use['size'][0])) in INTEGER_TERMS \
                        and type_is_buffer(types.get((decl['name'], use['data'][0])) or ''):
                    found.setdefault(use['size'][1], set()).add(use['data'][1])
        owners = {}
        for scalar, buffers in found.items():
            for b in buffers:
                owners.setdefault(b, set()).add(scalar)
        for b, scalars in owners.items():
            if len(scalars) > 1:
                raise Unsupported(decl, 'extent', f'{b} is sized by {" and ".join(sorted(scalars))} at different launches')
        return found

    def argument_map(self, decl):
        """How to call the original kernel with a qpel pipeline's data: one entry per argument."""
        out, position = [], 0
        for p in params_of(decl):
            if self.dialect.builtin(p):
                continue
            entry = self.scope.get(p['id'])
            item = {'name': p['name'], 'binding': self.dialect.binding(p, position), 'type': describe(p['type']['qualType'])[-1]}
            position += 1
            if entry is None or entry[0] == 'untyped':
                item['source'] = 'unknown'
            elif entry[0] == 'extent':
                item.update(source='extent', parameter=self.graph.nodes[entry[1]]['attributes']['name'], axis=entry[2])
            elif entry[0] == 'count':
                item.update(source='volume', parameter=self.safe_name(p['name']))
            elif entry[0] == 'presence':  # qpel's Metal: whether the optional buffer was given (IR version 9)
                item.update(source='presence', parameter=self.safe_name(entry[1]))
            else:
                item.update(source='parameter', parameter=self.safe_name(p['name']))
            out.append(item)
        return out

    def threadgroup_parameter(self, p, kernel):
        """A Metal `threadgroup T* p [[threadgroup(n)]]` kernel parameter: group memory whose length
        the host sets at the launch (setThreadgroupMemoryLength). qpel's shared memory has a fixed
        length, so the import specialises it: import_source(shared={'p': N}) makes it shared<T, N>,
        declared at the kernel's top, as function constants are specialised to their values."""
        name = p['name']
        self.scope[p['id']] = ('untyped', name)
        t = describe(p['type']['qualType'])
        item = t[2] if t[0] == 'pointer' else None
        if item not in SCALAR_TERMS - {'bool'} and not is_vector(item or ''):
            self.attempt(lambda: self.reject(Unsupported(
                p, 'shared', f'threadgroup memory of {p["type"]["qualType"]} is not in qpel yet')))
            return
        if name not in self.shared_lengths:
            self.attempt(lambda: self.reject(Unsupported(
                p, 'dynamic-shared', f'{name} is threadgroup memory sized by the host; import with '
                                     f'shared={{{name!r}: N}} to give its length in elements')))
            return
        length = int(self.shared_lengths[name])
        if item in ('i8', 'u8'):  # untyped memory (threadgroup char*) that every use reads as one type
            typed = self.typed_bytes(p, kernel)
            if typed and typed != item:
                size = shared_bytes(f'shared<{typed}, 1>')
                if length % size:
                    self.attempt(lambda: self.reject(Unsupported(
                        p, 'shared', f'{name} has {length} bytes, not a whole number of {typed}')))
                    return
                self.adaptations.append(f'{self.current}: threadgroup memory {name} of {length} bytes is only read as '
                                        f'{typed}, so it became {length // size} elements of {typed}')
                item, length = typed, length // size
        self.names.discard(self.safe_name(name))  # the parameter's name passes to the shared array
        ir = self.graph.add('Variable', {'name': self.unique_name(name)},
                            {'type': self.graph.add('SharedType', {'element': item, 'length': length})})
        self.scope[p['id']] = ('local', ir, f'shared<{item}, {length}>')
        self.dynamic_shared.append(ir)
        self.adaptations.append(f'{self.current}: threadgroup parameter {name}, sized by the host, became shared '
                                f'memory of {length} elements declared in the kernel')

    def typed_bytes(self, p, kernel):
        """The one element type every use of a `threadgroup char*` parameter casts it to
        (`(threadgroup uint16_t *) shmem + k`), or None when it has uses of several types or uncast uses."""
        found, uses, cast = set(), 0, 0
        for node in walk(body_of(kernel)):
            if node.get('kind') == 'DeclRefExpr' and node.get('referencedDecl', {}).get('id') == p['id']:
                uses += 1
            if node.get('castKind') == 'BitCast' and scalar_of(node) == 'pointer':
                inner = node['inner'][0]
                while inner.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and inner.get('castKind') != 'BitCast' \
                        and inner.get('inner'):
                    inner = inner['inner'][0]
                if inner.get('kind') == 'DeclRefExpr' and inner.get('referencedDecl', {}).get('id') == p['id']:
                    cast += 1
                    found.add(describe(node['type']['qualType'])[2])
        if len(found) != 1 or cast != uses:
            return None
        typed = found.pop()
        return typed if typed in SCALAR_TERMS - {'bool'} or is_vector(typed or '') else None

    def builtin_component(self, node):
        """(scope entry, 'x'|'y'|'z') for `v[0]`, `v[1]`, `v[2]` of a Metal thread-position or group
        builtin vector (a subscript reads a vector element as .x .y .z do), else None."""
        if node.get('kind') != 'ArraySubscriptExpr':
            return None
        base, index = peel(node['inner'][0]), peel(node['inner'][1])
        if base['kind'] != 'DeclRefExpr' or index['kind'] != 'IntegerLiteral' or str(index.get('value')) not in ('0', '1', '2'):
            return None
        entry = self.scope.get(base['referencedDecl']['id'])
        if not entry or not (entry[0] == 'position' or entry[0] == 'group-builtin' and entry[2]):
            return None
        return entry, 'xyz'[int(index['value'])]

    def axis_of(self, node):
        """'x', 'y' or 'z' when node is a thread-index axis (CUDA expression or Metal position member)."""
        node = peel(node)
        axis = grid_axis(node)
        if axis:
            return axis
        component = self.builtin_component(node)
        if component and component[0][0] == 'position':
            return component[1]
        if node['kind'] in ('MemberExpr', 'ExtVectorElementExpr'):
            base = peel(node['inner'][0])
            entry = self.scope.get(base.get('referencedDecl', {}).get('id')) if base['kind'] == 'DeclRefExpr' else None
            field = member_name(node, self.source)
            if entry and entry[0] == 'position' and field in 'xyz':
                return field
        if node['kind'] == 'DeclRefExpr':
            entry = self.scope.get(node['referencedDecl']['id'])
            if entry and entry[0] == 'grid':
                return entry[1]
        return None

    def axis_extents(self, decl, params):
        """{axis: scalar parameter} for a 2D/3D kernel sized by scalar arguments.

        A local computed from one thread-index axis (`row = (blockIdx.y * blockDim.y +
        threadIdx.y) * 2`), or the axis itself, compared with a scalar argument
        (`row < H`) makes that argument the extent of the axis."""
        body = body_of(decl)
        derived = {}
        for node in walk(body):
            if node.get('kind') == 'VarDecl' and 'init' in node:
                axes = {self.axis_of(x) for x in walk(node['inner'][0])} - {None}
                if len(axes) == 1:
                    derived[node['id']] = axes.pop()
        scalars = {self.graph.nodes[ir]['attributes']['name']: ir for ir in params
                   if type_term(self.graph.nodes[self.graph.nodes[ir]['children']['type'][0]]) in ('u32', 'i32')}
        ids = {p['id']: p['name'] for p in params_of(decl) if p['name'] in scalars}

        def axis(node):
            core = peel(node)
            if core['kind'] == 'DeclRefExpr' and core['referencedDecl']['id'] in derived:
                return derived[core['referencedDecl']['id']]
            return self.axis_of(core)
        found = {}
        for node in walk(body):
            if node.get('kind') == 'BinaryOperator' and node['opcode'] in ('<', '<=', '>', '>='):
                a, b = node['inner']
                for index, other in ((a, b), (b, a)):
                    other = peel(other)
                    if axis(index) and other['kind'] == 'DeclRefExpr' and other['referencedDecl']['id'] in ids:
                        found.setdefault(axis(index), set()).add(ids[other['referencedDecl']['id']])
        used = {axis(n) for n in walk(body) if n.get('kind') in ('BinaryOperator', 'MemberExpr', 'DeclRefExpr', 'ExtVectorElementExpr')} - {None}
        extents = {}
        for a in sorted(used):
            names = found.get(a, set())
            if len(names) != 1:
                raise Unsupported(decl, 'no-extent', f'the {a} thread index is compared with ' +
                                  (f'several arguments ({", ".join(sorted(names))})' if names else 'no scalar argument') +
                                  '; pass a uint3 shape')
            extents[a] = names.pop()
        if len(set(extents.values())) != len(extents):
            raise Unsupported(decl, 'no-extent', 'two thread-index axes are compared with the same argument; pass a uint3 shape')
        return extents

    def extent_shape(self, decl, params, extents):
        """Replace the extent arguments by one shape (W, H, N); uses of them read its members."""
        names = {self.graph.nodes[ir]['attributes']['name']: ir for ir in params}
        first = min(params.index(names[n]) for n in extents.values())
        taken = set(names) | {v.get('name') for v in walk(body_of(decl)) if v.get('kind') == 'VarDecl'}
        shape_name = next(n for n in ('shape', 'grid_shape', 'qshape') if n not in taken)
        binding = self.graph.nodes[params[first]]['attributes']['binding']
        shape = self.graph.add('Parameter', {'name': shape_name, 'binding': binding, 'isBuffer': True},
                               {'type': self.type_node('shape3')})
        for p in params_of(decl):
            for a, n in extents.items():
                if p['name'] == n:
                    kind = self.scope[p['id']][2]
                    self.scope[p['id']] = ('extent', shape, a, kind)
        removed = {names[n] for n in extents.values()}
        params[:] = [ir for ir in params[:first] if ir not in removed] + [shape] + [ir for ir in params[first:] if ir not in removed]
        if self.dialect.name == 'cuda':  # CUDA bindings follow parameter order
            for position, ir in enumerate(params):
                self.graph.nodes[ir]['attributes']['binding'] = position
        sizes = [extents.get(a, '1') for a in 'xyz']
        self.adaptations.append(f'{decl["name"]}: {", ".join(extents[a] for a in sorted(extents))} became a shape ({", ".join(sizes)})')
        return shape

    def block_method(self, node):
        """The method of a cooperative-groups thread_block that `node` calls (`block.sync()`,
        `cg::this_thread_block().thread_rank()`), or None."""
        while node.get('kind') in TEMPORARIES and node.get('inner'):
            node = node['inner'][0]
        if node.get('kind') != 'CXXMemberCallExpr' or not node.get('inner'):
            return None
        member = node['inner'][0]
        if member.get('kind') != 'MemberExpr' or not member.get('inner'):
            return None
        base = member['inner'][0]
        while base.get('kind') in TEMPORARIES + ('CXXConstructExpr',) and base.get('inner'):
            base = base['inner'][0]
        if base.get('kind') == 'DeclRefExpr' and self.scope.get(base['referencedDecl']['id'], ('',))[0] == 'cg-block':
            return member.get('name')
        if base.get('kind') == 'CallExpr' and decl_name(base['inner'][0]) == 'this_thread_block':
            return member.get('name')
        return None

    def uses_groups(self, decl):
        """A kernel is a Groups kernel when it reads a thread builtin other than the global
        index: CUDA's threadIdx, blockIdx, blockDim or gridDim outside blockIdx * blockDim +
        threadIdx, or a Metal threadgroup position, thread position in the group, group size
        or group count."""
        names = {p['name'] for p in params_of(decl)}
        if any(self.dialect.builtin(p) in SUBGROUP_BUILTINS for p in params_of(decl)) or self.uses_subgroups(decl):
            return True  # subgroup operations need whole groups
        if self.dialect.name == 'cuda' and self.calls_builtin_helpers(decl):
            return True  # it passes thread builtins to helpers
        if any(self.dialect.builtin(p) in GROUP_NODES and p['name'] not in QPEL_GROUP_SUM_PARAMS
               or self.dialect.builtin(p) == 'MetalLocalIndexAttr' for p in params_of(decl)) or 'qpel_group' in names:
            return True  # (qpel_lane and qpel_group_size alone belong to qpel's own workgroup sum)
        body = body_of(decl)
        for node in walk(body):  # a block of threads that work together: barriers, shared memory, a thread_block
            if node.get('kind') == 'CallExpr' and decl_name(node['inner'][0]) in BARRIER_CALLS | {'this_thread_block'} or \
                    node.get('kind') == 'VarDecl' and 'CUDASharedAttr' in attributes(node) and \
                    not node.get('name', '').startswith('qpel_'):  # (qpel's own workgroup-sum scratch)
                return True
        idiom = set()
        for node in walk(body):
            if node.get('kind') == 'BinaryOperator' and grid_axis(node) or \
                    node.get('kind') == 'CallExpr' and (decl_name(node['inner'][0]) or '').startswith('qpel_'):
                idiom.update(id(n) for n in walk(node))  # the global index, or qpel's own backend helpers
        return any(n.get('kind') == 'MemberExpr' and id(n) not in idiom and decl_name(n['inner'][0]) in CUDA_BUILTINS
                   for n in walk(body))

    def builtin_reads(self, name, seen=None):
        """[(role, axis)] of the CUDA thread builtins (threadIdx, blockIdx, blockDim, gridDim
        components) a device function reads, itself or through the functions it calls. A qpel
        helper cannot read them, so the import passes each as a u32 parameter (thread_idx_x, ...)."""
        cache = self.__dict__.setdefault('_builtin_reads', {})
        if name in cache:
            return cache[name]
        seen = set() if seen is None else seen
        seen.add(name)
        decl = self.functions[name][0]
        found = set()
        if self.dialect.name == 'cuda' and body_of(decl) is not None:
            for node in walk(body_of(decl)):
                if node.get('kind') == 'MemberExpr' and decl_name(node['inner'][0]) in CUDA_BUILTINS:
                    field = member_name(node, self.source)
                    if field in ('x', 'y', 'z'):
                        found.add((CUDA_BUILTINS[decl_name(node['inner'][0])], field))
                if node.get('kind') == 'CallExpr' and node.get('inner'):
                    callee = self.callee(node)
                    if callee in self.functions and callee not in seen and not self.functions[callee][2]:
                        found.update(self.builtin_reads(callee, seen))
        result = sorted(found, key=lambda ra: (list(GROUP_NODES).index(ra[0]), ra[1]))
        cache[name] = result
        return result

    def calls_builtin_helpers(self, decl):
        """A kernel that calls a helper reading thread builtins (it passes them, so it is a Groups kernel)."""
        for node in walk(body_of(decl)):
            if node.get('kind') == 'CallExpr' and node.get('inner'):
                callee = self.callee(node)
                if callee in self.functions and not self.functions[callee][2] and self.builtin_reads(callee):
                    return True
        return False

    def uses_subgroups(self, decl, seen=None):
        """The function calls a subgroup operation, itself or through the functions it calls."""
        seen = set() if seen is None else seen
        for node in walk(body_of(decl)):
            if node.get('kind') != 'CallExpr' or not node.get('inner'):
                continue
            name = decl_name(node['inner'][0])
            if name in SUBGROUP_CALLS:
                return True
            if name in self.functions and name not in seen:
                seen.add(name)
                if self.uses_subgroups(self.functions[name][0], seen):
                    return True
        return False

    def uses_grid3d(self, decl):
        """A kernel is 2D/3D when it uses a y or z thread index, or a Metal uint2/uint3 position."""
        for p in params_of(decl):
            if self.dialect.builtin(p) == 'grid' and is_position_vector(p):
                return True
        return any(grid_axis(node) in ('y', 'z') for node in walk(body_of(decl)) if node.get('kind') == 'BinaryOperator')

    def element_count(self, decl, types):
        """The unsigned scalar that sizes the grid of a kernel without a uint3 shape.

        It is the one unsigned int argument that the thread index is compared with
        (`if (i >= n) return;`, `if (i < n) {...}`). qpel turns it into a shape
        (n, 1, 1); every other scalar stays an ordinary kernel argument."""
        params = params_of(decl)
        # A kernel with a uint3/dim3 argument is sized by it, unless the thread index is compared
        # with a count instead (TRELLIS.2's preprocess: `if (idx >= num_nodes)`, with a dim3 tile grid).
        shaped = any(types.get((decl['name'], i)) == 'shape3' for i in range(len(params)))
        unsigned = {p['id']: p for i, p in enumerate(params)
                    if types.get((decl['name'], i)) in INTEGER_TERMS and not self.dialect.builtin(p)}
        grid = {p['id'] for p in params if self.dialect.builtin(p) == 'grid'}
        body = body_of(decl)
        for node in walk(body):
            if node.get('kind') == 'VarDecl' and 'init' in node and is_grid_index(node['inner'][0]):
                grid.add(node['id'])

        def refers(node, ids):
            core = peel(node)
            return core['kind'] == 'DeclRefExpr' and core['referencedDecl']['id'] in ids
        compared = {}
        for node in walk(body):
            if node.get('kind') == 'BinaryOperator' and node['opcode'] in COMPARISONS:
                a, b = node['inner']
                for index, other in ((a, b), (b, a)):
                    if (refers(index, grid) or is_grid_index(index)) and refers(other, unsigned):
                        compared[peel(other)['referencedDecl']['id']] = True
        if shaped and len(compared) != 1:
            return None
        if len(compared) > 1:
            raise Unsupported(decl, 'no-extent', 'the thread index is compared with several counts; pass a uint3 shape')
        if compared:
            return unsigned[next(iter(compared))]
        return None

    # -- statements --

    def block(self, node):
        statements = []
        self.depth = getattr(self, 'depth', 0) + 1
        try:
            for child in node.get('inner', ()):
                statements.extend(self.statement(child))
        finally:
            self.depth -= 1
        return self.graph.add('Block', children={'statements': statements})

    def as_block(self, node):
        if node['kind'] == 'CompoundStmt':
            return self.block(node)
        return self.graph.add('Block', children={'statements': self.statement(node)})

    def statement(self, node):
        outer, self.prelude = getattr(self, 'prelude', None), []
        try:
            statements = self.attempt(lambda: self.read_statement(node), [])
            return self.prelude + statements if statements else statements
        finally:
            self.prelude = outer

    def condition_prelude(self, cond):
        """A loop condition and the statements it needs before it (a compare-exchange), read apart
        from the enclosing statement's prelude."""
        outer, self.prelude = self.prelude, []
        try:
            return self.expr(cond), self.prelude
        finally:
            self.prelude = outer

    def read_statement(self, node):
        kind = node['kind']
        add = self.graph.add
        if kind == 'CompoundStmt':
            return [self.block(node)]
        if kind == 'NullStmt':
            return []
        if kind == 'CStyleCastExpr' and node.get('castKind') == 'ToVoid' and (
                peel_void(node)['kind'] == 'DeclRefExpr' or not has_effects(node)):
            return []  # (void)x; or ggml's (void)sizeof((a, b, 0)): marks locals as unused and does nothing
        if kind == 'IfStmt' and node.get('isConstexpr') and node['inner'][0].get('kind') == 'ConstantExpr' \
                and node['inner'][0].get('value') in ('true', 'false'):
            # if constexpr in a template instance: clang has chosen the branch; the other is not code
            if node['inner'][0]['value'] == 'true':
                return self.read_statement(node['inner'][1])
            return self.read_statement(node['inner'][-1]) if node.get('hasElse') else []
        if kind == 'AttributedStmt':  # [[unroll]], likely/unlikely: hints that do not change meaning
            return self.read_statement(node['inner'][-1])
        if kind == 'DeclStmt':
            return self.local_group(node)
        if kind == 'IfStmt':
            if node.get('hasInit') or node.get('hasVar'):
                raise Unsupported(node, 'if-declaration', 'declarations inside if (...) are not supported')
            cond, then, *otherwise = node['inner']
            known = self.static_value(cond)
            if known is not None:  # decided by function constants: keep only the branch taken, as Metal does
                note = f'{self.current}: branches decided by function constants were folded'
                if note not in self.adaptations:
                    self.adaptations.append(note)
                chosen = then if known else (otherwise[0] if otherwise else None)
                return [] if chosen is None else self.read_statement(chosen)
            children = {'condition': self.expr(cond), 'thenBody': self.as_block(then)}
            if otherwise:
                children['elseBody'] = self.as_block(otherwise[0])
            return [add('If', children=children)]
        if kind == 'ForStmt':
            init, variable, cond, step, body = node['inner']
            if variable:
                raise Unsupported(node, 'for-declaration', 'declarations inside the for condition are not supported')
            children = {'body': None}
            if init:
                parts = self.local_group(init) if init['kind'] == 'DeclStmt' else [self.expression_statement(init)]
                if len(parts) != 1 or self.graph.nodes[parts[0]]['kind'] not in ('Variable', 'Assign'):
                    raise Unsupported(init, 'for-init', 'a for loop can start with one declaration or assignment')
                children['init'] = parts[0]
            if cond:
                children['condition'], before = self.condition_prelude(cond)
                if before:
                    raise Unsupported(node, 'atomic-cas', 'a compare-exchange in a for condition is not in qpel yet')
            if step:
                steps = []
                def split(x):  # `i++, toDo -= 8`: one step each, run in order
                    while x['kind'] in ('ExprWithCleanups', 'ParenExpr'):
                        x = x['inner'][0]
                    if x['kind'] == 'BinaryOperator' and x['opcode'] == ',':
                        split(x['inner'][0])
                        split(x['inner'][1])
                    else:
                        steps.append(self.expression_statement(x))
                split(step)
                children['step'] = steps
            children['body'] = self.as_block(body)
            return [add('For', children=children)]
        if kind == 'BreakStmt':
            return [add('Break')]
        if kind == 'ContinueStmt':
            return [add('Continue')]
        if kind == 'WhileStmt':
            if node.get('hasVar'):
                raise Unsupported(node, 'while-declaration', 'declarations inside while (...) are not supported')
            cond, body = node['inner']
            condition, before = self.condition_prelude(cond)
            if before:  # while (true) { before; if (!condition) break; body }: continue runs `before` again
                leave = add('If', children={'condition': add('Unary', {'operator': '!'}, {'operand': condition}),
                                            'thenBody': add('Block', children={'statements': [add('Break')]})})
                statements = before + [leave, self.as_block(body)]
                return [add('While', children={'condition': add('Literal', {'value': 'true'}),
                                               'body': add('Block', children={'statements': statements})})]
            return [add('While', children={'condition': condition, 'body': self.as_block(body)})]
        if kind == 'DoStmt':
            body, cond = node['inner']
            block = self.as_block(body)
            if not self.graph.nodes[block]['children'].get('statements') and integer_constant(cond) == 0:
                return []  # do { } while (0): what is left of a macro such as GGML_UNUSED_VARS
            condition, before = self.condition_prelude(cond)
            if before:  # while (true) { body; before; if (!condition) break; }: the condition reads before's locals
                if own_continue(body):
                    raise Unsupported(node, 'atomic-cas', 'a do loop with continue and a compare-exchange in its '
                                                          'condition is not in qpel yet')
                leave = add('If', children={'condition': add('Unary', {'operator': '!'}, {'operand': condition}),
                                            'thenBody': add('Block', children={'statements': [add('Break')]})})
                return [add('While', children={'condition': add('Literal', {'value': 'true'}),
                                               'body': add('Block', children={'statements': [block] + before + [leave]})})]
            return [add('DoWhile', children={'body': block, 'condition': condition})]
        if kind == 'ReturnStmt':
            value = [self.expr(node['inner'][0])] if node.get('inner') else []
            return [add('Return', children={'value': value})]
        later = {'SwitchStmt': 'switch', 'GotoStmt': 'goto', 'LabelStmt': 'goto'}
        if kind in later:
            if self.issues is not None:  # a survey still reads what is inside, for further blockers
                self.survey_inside(node)
            raise Unsupported(node, later[kind], f'{kind[:-4].lower()} statements are not in qpel yet')
        return [self.expression_statement(node)]

    def survey_inside(self, node):
        for child in node.get('inner', ()):
            if child.get('kind') in ('CompoundStmt', 'CaseStmt', 'DefaultStmt', 'LabelStmt'):
                self.survey_inside(child)  # the labels themselves are part of the rejected statement
            elif child.get('kind', '').endswith('Stmt'):
                self.as_block(child)
            elif child:
                self.attempt(lambda: self.expr(child))

    def local_group(self, node):
        return [s for d in node['inner'] for s in self.local(d)]

    def local(self, decl):
        if decl['kind'] in ('TypedefDecl', 'TypeAliasDecl', 'UsingDecl', 'UsingDirectiveDecl') or decl.get('_qpel_facade'):
            return []  # local type names are read where they are used; a library facade object is inlined
        if decl['kind'] != 'VarDecl':
            raise Unsupported(decl, 'declaration', decl['kind'] + ' inside a function is not supported')
        name = decl['name']
        if name.startswith('qpel_') and decl.get('storageClass') != 'auto' and ('init' not in decl or 'threadgroup' in decl['type']['qualType']):
            self.scope[decl['id']] = ('builtin', name)  # backend scratch for workgroup collectives
            return []
        if 'thread_block' in decl['type']['qualType']:
            self.scope[decl['id']] = ('cg-block',)  # cg::this_thread_block(): the group, read through its methods
            return []
        self.scope[decl['id']] = ('untyped', name)  # until it is read, so later uses are not reported again
        t = describe(view_spelling(decl))
        if t[0] == 'pointer' and self.into_shared(decl):
            return self.shared_view_local(decl, name, t[2])
        if t[0] == 'pointer' and t[2] in SCALAR_TERMS - {'bool'} and \
                not {'threadgroup', 'constant', 'thread'} & set(decl['type']['qualType'].replace('*', ' ').split()):
            return self.buffer_local(decl, name, type_buffer(t[2], 'read' if t[1] else 'write'))
        array = LOCAL_ARRAY.fullmatch(decl['type']['qualType'].strip()) or \
            LOCAL_ARRAY.fullmatch(decl['type'].get('desugaredQualType', '').strip())  # BlockScanT::TempStorage
        if array:
            return self.array_local(decl, name, array)
        unsized = re.fullmatch(r'(.+?)\s*\[\]', decl['type']['qualType'].strip())
        if unsized and 'CUDASharedAttr' in attributes(decl):
            return self.dynamic_shared_local(decl, name, describe(unsized.group(1)))
        if t[0] == 'scalar' and is_vector(t[1]):
            return self.vector_local(decl, name, t[1])
        if t[0] == 'scalar' and t[1] in SCALAR_TERMS - {'bool'} and (
                'CUDASharedAttr' in attributes(decl) or 'threadgroup' in decl['type']['qualType'].split()):
            # `__shared__ float carry;`: one value for the group, a shared<T, 1> read and written at [0]
            out = self.shared_local(decl, name, t, 1)
            self.scope[decl['id']] = ('shared-scalar', self.scope[decl['id']][1], t[1])
            return out
        if t[0] == 'scalar' and t[1] in self.records:
            raise Unsupported(decl, 'local-type', f'local {name}: struct locals are not in qpel yet')
        if t[0] != 'scalar' or t[1] not in SCALAR_TERMS:
            raise Unsupported(decl, 'local-type', f'local {name} of type {decl["type"]["qualType"]} is not in qpel yet')
        init = decl['inner'][0] if 'init' in decl else None
        if init is None:
            if t[1] not in ZERO and t[1] not in NEW_INTEGERS:
                raise Unsupported(decl, 'uninitialized-local', f'local {name} needs an initializer')
            # Reading an uninitialized local is undefined in C, so any value is a faithful start.
            self.adaptations.append(f'{self.current}: local {name} had no initializer; qpel starts it at zero')
            name = self.unique_name(name)
            zero = (self.graph.add(ZERO[t[1]][0], {'value': ZERO[t[1]][1]}) if t[1] in ZERO
                    else self.graph.add('Constant', {'scalar': t[1], 'value': '0'}))
            ir = self.graph.add('Variable', {'name': name}, {'type': self.type_node(t[1]), 'value': zero})
            self.scope[decl['id']] = ('local', ir, t[1])
            return [ir]
        axis = grid_axis(init) or (self.axis_of(init) if peel(init)['kind'] in ('MemberExpr', 'ExtVectorElementExpr', 'ArraySubscriptExpr') else None)
        if self.kernel and axis and decl['id'] not in self.assigned and not self.groups:
            self.scope[decl['id']] = ('grid', axis)  # a name for the thread index (CUDA expression or Metal position.y)
            return []
        value = self.expr(init)
        name = self.unique_name(name)
        ir = self.graph.add('Variable', {'name': name},
                            {'type': self.type_node(t[1]), 'value': value})
        self.scope[decl['id']] = ('local', ir, t[1])
        return [ir]

    def zero(self, scalar):
        if scalar in ZERO:
            return self.graph.add(ZERO[scalar][0], {'value': ZERO[scalar][1]})
        if scalar in INTEGER_TERMS:
            return self.graph.add('Constant', {'scalar': scalar, 'value': '0'})
        return self.convert_types(self.graph.add('F32Literal', {'value': '0.0'}), 'f32', scalar, None)

    def array_local(self, decl, name, array):
        """A thread-private array, `uint32_t point[3] = {...}` (IR version 4). Elements the
        initializer leaves out are zero, as in C; an array without one starts all zero (C leaves
        it undefined)."""
        element = describe(array.group(1))
        length = int(array.group(2))
        space = set(decl['type']['qualType'].replace('[', ' ').split()) & {'threadgroup', 'constant', 'device'}
        if 'CUDASharedAttr' in attributes(decl) or 'threadgroup' in space:
            return self.shared_local(decl, name, element, length)
        if space or decl.get('storageClass') == 'static':  # one copy for all: not a thread's own array
            raise Unsupported(decl, 'local-type', f'local {name}: a static or constant-space array is not in qpel yet')
        if element[0] != 'scalar' or element[1] not in SCALAR_TERMS or not 1 <= length <= 1024:
            raise Unsupported(decl, 'local-type', f'local {name} of type {decl["type"]["qualType"]} is not in qpel: '
                                                  'local arrays hold 1 to 1024 scalars')
        scalar = element[1]
        init = decl['inner'][0] if 'init' in decl else None
        while init is not None and init['kind'] in ('ExprWithCleanups', 'ConstantExpr') and init.get('inner'):
            init = init['inner'][0]
        values = []
        if init is not None:
            if init['kind'] != 'InitListExpr':
                raise Unsupported(decl, 'array-init', f'local array {name} is initialized with a list or not at all')
            items = list(init.get('inner', ()))
            if 'array_filler' in init:
                # clang's JSON for a partly filled list: the filler for the missing elements first,
                # then the written elements (and no 'inner').
                filler, *written = init['array_filler']
                if filler.get('kind') != 'ImplicitValueInitExpr':
                    raise Unsupported(decl, 'array-init', f'local array {name}: only zero fills its missing elements')
                items = written + items
            if len(items) > length:
                raise Unsupported(decl, 'array-init', f'local array {name} has more initializers than elements')
            values = [self.expr(item) for item in items]
        else:
            self.adaptations.append(f'{self.current}: local array {name} had no initializer; qpel starts it at zero')
        values += [self.zero(scalar) for _ in range(length - len(values))]
        ir = self.graph.add('Variable', {'name': self.unique_name(name)},
                            {'type': self.graph.add('ArrayType', {'element': scalar, 'length': length}),
                             'value': self.graph.add('ArrayValue', children={'elements': values})})
        self.scope[decl['id']] = ('local', ir, f'array<{scalar}, {length}>')
        return [ir]

    def dynamic_shared_local(self, decl, name, element):
        """`extern __shared__ float smem[];`: shared memory whose length the launch gives (its third
        <<<>>> argument, in bytes). As for Metal threadgroup parameters, the import specialises it to
        import_source(shared={'smem': N}) elements."""
        if name not in self.shared_lengths:
            raise Unsupported(decl, 'dynamic-shared', f'{name} is shared memory sized by the launch; import with '
                                                      f'shared={{{name!r}: N}} to give its length in elements')
        length = int(self.shared_lengths[name])
        self.adaptations.append(f'{self.current}: dynamic shared memory {name}, sized by the launch, became '
                                f'{length} elements declared in the kernel')
        return self.shared_local(decl, name, element, length)

    def shared_local(self, decl, name, element, length):
        """`__shared__ float3 collected[64]` (Metal `threadgroup float3 ...`): memory of the group
        (IR version 4 shared<T, N>), declared at the top of a Groups kernel."""
        item = element[1] if element[0] == 'scalar' else None
        if not self.kernel or not self.groups:
            raise Unsupported(decl, 'shared', f'shared memory {name} is only in kernels whose threads work as a group')
        if item not in SCALAR_TERMS - {'bool'} and not is_vector(item or ''):
            raise Unsupported(decl, 'shared', f'shared memory of {decl["type"]["qualType"]} is not in qpel yet')
        init = decl['inner'][0] if 'init' in decl and decl.get('inner') else None
        if init is not None and not (init['kind'] in ('CXXConstructExpr', 'ArrayInitLoopExpr') and not any(
                x.get('kind') not in ('CXXConstructExpr', 'ArrayInitLoopExpr', 'ArrayInitIndexExpr', 'OpaqueValueExpr')
                for x in walk(init))):
            raise Unsupported(decl, 'shared', f'shared memory {name} cannot have an initializer')  # (a default constructor is none)
        ir = self.graph.add('Variable', {'name': self.unique_name(name)},
                            {'type': self.graph.add('SharedType', {'element': item, 'length': length})})
        self.scope[decl['id']] = ('local', ir, f'shared<{item}, {length}>')
        if getattr(self, 'depth', 1) > 1:  # declared in an inner block: the group's memory all the same
            self.hoisted.append(ir)
            message = f'{self.current}: shared memory {name}, declared in an inner block, was moved to the kernel\'s top'
            if message not in self.adaptations:
                self.adaptations.append(message)
            return []
        return [ir]

    def vector_local(self, decl, name, vtype):
        """A vector local (`int4 coord = ...`, `float3 p_view;`), IR version 4 vec<T, N>."""
        init = decl['inner'][0] if 'init' in decl else None
        if init is not None and init['kind'] == 'CXXConstructExpr' and not init.get('inner'):
            init = None  # `uint2 lo;`: the trivial default constructor leaves it undefined
        if init is None:
            # Reading an uninitialized local is undefined in C, so any value is a faithful start.
            value = self.graph.add('VectorValue', {'element': vector_element(vtype), 'length': vector_length(vtype)},
                                   {'elements': [self.zero(vector_element(vtype)) for _ in range(vector_length(vtype))]})
            self.adaptations.append(f'{self.current}: local {name} had no initializer; qpel starts it at zero')
        else:
            value = self.vector_value(init, vtype)
        ir = self.graph.add('Variable', {'name': self.unique_name(name)}, {'type': self.type_node(vtype), 'value': value})
        self.scope[decl['id']] = ('local', ir, vtype)
        return [ir]

    def vector_value(self, node, vtype):
        """A vector expression: a list `{a, b, c}` (or make_float3, Metal float3(...)), a load from a
        buffer of its elements, or any other expression of the vector's type."""
        while node['kind'] in LOOSE | {'ConstantExpr'} and len(node.get('inner', ())) == 1:
            node = node['inner'][0]
        if node['kind'] == 'CXXConstructExpr' and len(node.get('inner', ())) == 1:
            return self.vector_value(node['inner'][0], vtype)  # a copy
        if node['kind'] in ('InitListExpr', 'MetalVectorInitExpr'):
            return self.vector_elements(node, node.get('inner', ()), vtype)
        if self.vector_load_base(node) is not None:
            return self.vector_load(node, vtype)
        if node['kind'] == 'CXXConstructExpr':
            raise Unsupported(node, 'vector-init', f'this {vtype} constructor is not in qpel')
        if scalar_of(node) != vtype:
            raise Unsupported(node, 'vector-init', f'expected a {vtype}, got {scalar_of(node)}')
        return self.expr(node)

    def vector_elements(self, node, items, vtype):
        element, length = vector_element(vtype), vector_length(vtype)
        items = list(items)
        if len(items) != length or any(is_vector(scalar_of(i)) for i in items):
            raise Unsupported(node, 'vector-init', f'a {vtype} is built from {length} scalars '
                                                   '(splats and vectors of vectors are not in qpel)')
        values = [self.convert_types(self.expr(a), scalar_of(a), element, a) if scalar_of(a) != element else self.expr(a)
                  for a in items]
        return self.graph.add('VectorValue', {'element': element, 'length': length}, {'elements': values})

    def vector_load_base(self, node):
        """For `reinterpret_cast<const int4*>(p)[i]` or `*(float3*)p`: (p's clang node, index node or None)."""
        if node['kind'] == 'ArraySubscriptExpr':
            base, index = node['inner']
        elif node['kind'] == 'UnaryOperator' and node.get('opcode') == '*':
            base, index = node['inner'][0], None
        else:
            return None
        cast = base
        while cast['kind'] in ('ImplicitCastExpr', 'ParenExpr') and cast.get('inner'):
            cast = cast['inner'][0]
        if cast['kind'] not in ('CXXReinterpretCastExpr', 'CStyleCastExpr') or describe(cast['type']['qualType'])[0] != 'pointer':
            return None
        if not is_vector(describe(cast['type']['qualType'])[2]):
            return None
        return cast['inner'][0], index

    def vector_load(self, node, vtype):
        """`reinterpret_cast<const int4*>(p)[i]`: vec(p[N*i], ..., p[N*i + N-1]); `*(float3*)p` reads p[0..N-1].
        p is a kernel's buffer of the vector's scalars; the plan sizes it as N elements per element
        of its extent (an [M, N] tensor)."""
        pointer, index = self.vector_load_base(node)
        element, length = vector_element(vtype), vector_length(vtype)
        while pointer['kind'] in ('ImplicitCastExpr', 'ParenExpr') and pointer.get('inner'):
            pointer = pointer['inner'][0]
        entry = self.scope.get(pointer.get('referencedDecl', {}).get('id')) if pointer['kind'] == 'DeclRefExpr' else None
        if not (self.kernel and entry and entry[0] == 'param' and type_is_buffer(entry[2]) and element_of(entry[2]) == element):
            raise Unsupported(node, 'vector-init', f'a {vtype} is read from a kernel buffer of {element}')
        values = []
        if index is None:
            for k in range(length):
                values.append(self.graph.add('Index', children={'base': self.name(entry[1]), 'index': self.int_value('u32', k)}))
        else:
            buffer = self.graph.nodes[entry[1]]['attributes']['name']
            views = self.views.setdefault(self.current, {})
            if views.setdefault(buffer, length) != length:
                raise Unsupported(node, 'vector-init', f'{buffer} is read as vectors of {views[buffer]} and of {length}')
            kind = scalar_of(index)
            kind = kind if kind in INTEGER_TERMS else 'u32'
            for k in range(length):
                scaled = self.graph.add('Binary', {'operator': '*'}, {'left': self.expr(index), 'right': self.int_value(kind, length)})
                offset = self.graph.add('Binary', {'operator': '+'}, {'left': scaled, 'right': self.int_value(kind, k)})
                values.append(self.graph.add('Index', children={'base': self.name(entry[1]), 'index': offset}))
        return self.graph.add('VectorValue', {'element': element, 'length': length}, {'elements': values})

    def vector_element_ref(self, node):
        """(qpel parameter, index node, N, element) for `p[i]`, p a kernel's vector buffer (int4* p), or None."""
        node = peel(node) if node.get('kind') != 'ArraySubscriptExpr' else node
        if node.get('kind') != 'ArraySubscriptExpr':
            return None
        base, index = node['inner']
        core = base
        while core.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and core.get('inner'):
            core = core['inner'][0]
        if core.get('kind') != 'DeclRefExpr' or core['referencedDecl']['id'] not in self.vector_params:
            return None
        if any(x.get('kind') in ('CallExpr', 'CompoundAssignOperator') or x.get('opcode') in ('=', '++', '--')
               for x in walk(index)):
            raise Unsupported(node, 'vector-buffer', 'a vector buffer is indexed by an expression without side effects')
        entry = self.scope[core['referencedDecl']['id']]
        return entry[1], index, self.vector_params[core['referencedDecl']['id']], element_of(entry[2])

    def vector_element_index(self, ref, k):
        """p[N*i + k] for a vector buffer element p[i] (see vector_element_ref)."""
        param, index, length, _ = ref
        kind = scalar_of(index)
        kind = kind if kind in INTEGER_TERMS else 'u32'
        scaled = self.graph.add('Binary', {'operator': '*'}, {'left': self.expr(index), 'right': self.int_value(kind, length)})
        offset = self.graph.add('Binary', {'operator': '+'}, {'left': scaled, 'right': self.int_value(kind, k)})
        return self.graph.add('Index', children={'base': self.name(param), 'index': offset})

    def vector_store(self, node, target, value):
        """`p[i] = v` into a vector buffer: one store per element, through a local when v is not a list."""
        ref = self.vector_element_ref(target)
        element, length = ref[3], ref[2]
        vtype = f'vec<{element}, {length}>'
        add = self.graph.add
        statements = []
        inner = value
        while inner['kind'] in LOOSE | {'ConstantExpr'} and len(inner.get('inner', ())) == 1:
            inner = inner['inner'][0]
        if inner['kind'] == 'InitListExpr':
            elements = self.graph.nodes[self.vector_elements(inner, inner.get('inner', ()), vtype)]['children']['elements']
            parts = elements
        else:
            temp = add('Variable', {'name': self.unique_name('stored')}, {'type': self.type_node(vtype), 'value': self.vector_value(value, vtype)})
            statements.append(temp)
            parts = [add('Member', {'member': VECTOR_FIELDS[k]}, {'base': self.name(temp)}) for k in range(length)]
        for k, part in enumerate(parts):
            statements.append(add('Assign', children={'target': self.vector_element_index(ref, k), 'value': part}))
        return add('Block', children={'statements': statements})

    def int_value(self, scalar, value):
        if scalar == 'u32':
            return self.graph.add('Literal', {'value': f'{value}u'})
        if scalar == 'i32':
            return self.graph.add('I32Literal', {'value': str(value)})
        return self.graph.add('Constant', {'scalar': scalar, 'value': str(value)})

    def local_array(self, base):
        """The qpel Variable of a local array that `base` names, or None."""
        core = base
        while core.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and core.get('inner'):
            core = core['inner'][0]
        if core.get('kind') != 'DeclRefExpr':
            return None
        entry = self.scope.get(core['referencedDecl']['id'])
        group = entry and entry[0] in ('local', 'param') and (is_shared(entry[2]) or is_shared_view(entry[2]))
        return entry[1] if entry and (entry[0] == 'local' and is_array(entry[2]) or group) else None

    def safe_name(self, name):
        """A C name that qpel or a backend reserves (min, max, kernel, half, ...) gets a trailing _."""
        if is_identifier(name) or name.startswith('qpel_'):
            return name  # (qpel_ names are the backends' own builtins in re-imported code)
        renamed = name + '_'
        if not is_identifier(renamed):
            renamed = 'v_' + name  # a name that only a prefix makes safe
        if not is_identifier(renamed):
            raise Unsupported(None, 'name', f'{name} cannot be a qpel name')
        message = f'{self.current}: {name} is reserved in qpel or a backend and was renamed {renamed}'
        if message not in self.adaptations:
            self.adaptations.append(message)
        return renamed

    def into_shared(self, decl):
        """True for a pointer local into the group's memory: Metal `threadgroup T* p`, or a CUDA
        `T* p` whose value starts from a shared array or view (`float* row = tile + k`)."""
        if 'threadgroup' in decl['type']['qualType'].replace('*', ' ').split() or 'qpel_shared_view<' in decl['type']['qualType']:
            return True
        init = decl['inner'][0] if 'init' in decl and decl.get('inner') else None
        while init is not None:
            core = peel(init)
            if core.get('kind') == 'BinaryOperator' and core['opcode'] in ('+', '-'):
                init = core['inner'][0]
                continue
            entry = self.scope.get(core.get('referencedDecl', {}).get('id')) if core.get('kind') == 'DeclRefExpr' else None
            return bool(entry and entry[0] in ('local', 'param') and (is_shared(entry[2]) or is_shared_view(entry[2])))
        return False

    def shared_view_local(self, decl, name, item):
        """`threadgroup float * ss = shmem + k` (CUDA `float* ss = tile + k`): a view into shared
        memory, shared<T> (IR version 5), indexed as the array it points into."""
        if item not in SCALAR_TERMS - {'bool'} and not is_vector(item or ''):
            raise Unsupported(decl, 'local-type', f'local {name} of type {decl["type"]["qualType"]} is not in qpel yet')
        if 'init' not in decl:
            raise Unsupported(decl, 'local-type', f'pointer {name} into shared memory needs a value')
        kind = f'shared<{item}>'
        root = decl['inner'][0]
        while True:  # the shared array or view the value starts from, through offsets and casts
            core = peel(root)
            if core.get('kind') == 'BinaryOperator' and core['opcode'] in ('+', '-'):
                root = core['inner'][0]
            elif core.get('kind') in ('CStyleCastExpr', 'CXXStaticCastExpr', 'CXXReinterpretCastExpr', 'ImplicitCastExpr') \
                    and core.get('inner'):
                root = core['inner'][0]
            else:
                break
        entry = self.scope.get(core.get('referencedDecl', {}).get('id')) if core.get('kind') == 'DeclRefExpr' else None
        if entry and entry[0] in ('local', 'param') and (is_shared(entry[2]) or is_shared_view(entry[2])) \
                and view_element(entry[2]) != item:
            raise Unsupported(decl, 'shared', f'{name} reads shared memory of {view_element(entry[2])} as {item}; '
                                              'reinterpreting shared memory is not in qpel yet')
        value = self.expr(decl['inner'][0], address=True)
        ir = self.graph.add('Variable', {'name': self.unique_name(name)}, {'type': self.type_node(kind), 'value': value})
        self.scope[decl['id']] = ('local', ir, kind)
        return [ir]

    def buffer_local(self, decl, name, kind):
        """A pointer local into a device buffer (a view): `device float* row = (device float*)(dst + off)`."""
        children = {'type': self.type_node(kind)}
        if 'init' in decl:
            children['value'] = self.expr(decl['inner'][0], address=True)
        ir = self.graph.add('Variable', {'name': self.unique_name(name)}, children)
        self.scope[decl['id']] = ('local', ir, kind)
        return [ir]

    def unique_name(self, name):
        """C allows one name in sibling blocks; qpel names are unique in a function."""
        name = self.safe_name(name)
        if name not in self.names:
            self.names.add(name)
            return name
        number = 2
        while f'{name}_{number}' in self.names:
            number += 1
        renamed = f'{name}_{number}'
        self.names.add(renamed)
        self.adaptations.append(f'{self.current}: a second local {name} was renamed {renamed}')
        return renamed

    def expression_statement(self, node):
        add = self.graph.add
        while node['kind'] in ('ExprWithCleanups', 'ParenExpr'):
            node = node['inner'][0]
        kind = node['kind']
        if kind == 'BinaryOperator' and node['opcode'] == ',':  # `a = x, b = y;`: one statement after the other
            return add('Block', children={'statements': [self.expression_statement(x) for x in node['inner']]})
        if kind == 'BinaryOperator' and node['opcode'] == '=':
            inner = node['inner'][1]
            while inner['kind'] in ('ImplicitCastExpr', 'ParenExpr') and inner.get('inner') and \
                    inner.get('castKind') in (None, 'LValueToRValue', 'NoOp'):
                inner = inner['inner'][0]
            if inner['kind'] == 'BinaryOperator' and inner['opcode'] == '=' and \
                    peel(inner['inner'][0])['kind'] == 'DeclRefExpr':
                # `a = b = 0;`: b = 0, then a = b (the value b was given, converted to a's type)
                first = self.expression_statement(inner)
                then = add('Assign', children={'target': self.lvalue(node['inner'][0]),
                                               'value': self.convert_types(self.expr(inner['inner'][0]),
                                                                           scalar_of(inner['inner'][0]),
                                                                           scalar_of(node['inner'][0]), node)})
                return add('Block', children={'statements': [first, then]})
        if kind in ('BinaryOperator', 'CompoundAssignOperator', 'UnaryOperator') and node.get('opcode') in ('=', '++', '--') \
                or kind == 'CompoundAssignOperator':
            if self.is_atomic_element(node['inner'][0]):
                if node['opcode'] != '=':
                    raise Unsupported(node, 'atomic-update', f'{node["opcode"]} on an atomic buffer element is a race; use an atomic call')
                target, value = node['inner']
                self.note_atomic_access(peel(target)['inner'][0])
                store = add('AtomicStore', children={'address': self.element_address(peel(target)), 'value': self.expr(value)})
                return add('Evaluate', children={'value': store})
        if kind == 'BinaryOperator' and node['opcode'] == '=' and self.vector_element_ref(node['inner'][0]):
            return self.vector_store(node, *node['inner'])
        if kind in ('CXXOperatorCallExpr',) and decl_name(node['inner'][0]) == 'operator=' and self.vector_element_ref(node['inner'][1]):
            return self.vector_store(node, node['inner'][1], node['inner'][2])
        if kind == 'BinaryOperator' and node['opcode'] == '=':
            target, value = node['inner']
            return add('Assign', children={'target': self.lvalue(target), 'value': self.expr(value)})
        if kind == 'CompoundAssignOperator':
            # x op= y computes in C's computation type, then converts back to x's type.
            op = node['opcode'][:-1]
            target, value = node['inner']
            own = scalar_of(target)
            computation = describe(node.get('computeLHSType', {}).get('qualType', target['type']['qualType']))
            computation = computation[1] if computation[0] == 'scalar' else own
            left = self.convert_types(self.expr(target), own, computation, node)
            combined = add('Binary', {'operator': op}, {'left': left, 'right': self.expr(value)})
            result = self.convert_types(combined, computation, own, node)
            return add('Assign', children={'target': self.lvalue(target), 'value': result})
        if kind == 'CXXOperatorCallExpr' and decl_name(node['inner'][0]) == 'operator=':
            return add('Assign', children={'target': self.lvalue(node['inner'][1]), 'value': self.expr(node['inner'][2])})
        if kind == 'UnaryOperator' and node['opcode'] in ('++', '--'):
            target = node['inner'][0]
            scalar = scalar_of(target)
            one = {'u32': ('Literal', {'value': '1u'}), 'i32': ('I32Literal', {'value': '1'})}.get(scalar)
            if one is None and scalar in NEW_INTEGERS | {'u8'}:
                one = ('Constant', {'scalar': scalar, 'value': '1'})
            if one is None:
                raise Unsupported(node, 'increment', 'only integers can be incremented')
            step = add('Binary', {'operator': node['opcode'][0]}, {'left': self.expr(target), 'right': add(*one)})
            if scalar in ('u8', 'i8', 'u16', 'i16'):  # the sum is in 32 bits; C stores it back narrowed
                step = add('Cast', {'to': scalar}, {'operand': step})
            return add('Assign', children={'target': self.lvalue(target), 'value': step})
        return add('Evaluate', children={'value': self.expr(node, statement=True)})

    def lvalue(self, node):
        core = peel(node)
        if core['kind'] == 'DeclRefExpr':
            entry = self.typed(self.scope.get(core['referencedDecl']['id']), node)
            if entry and entry[0] in ('param', 'local'):
                return self.graph.add('Name', links={'declaration': entry[1]})
            if entry and entry[0] == 'shared-scalar':
                return self.graph.add('Index', children={'base': self.graph.add('Name', links={'declaration': entry[1]}),
                                                         'index': self.int_value('u32', 0)})
            raise Unsupported(node, 'assign-target', 'cannot assign to ' + core['referencedDecl'].get('name', '?'))
        if core['kind'] == 'ArraySubscriptExpr':
            if self.vector_element_ref(core):
                raise Unsupported(node, 'vector-buffer', 'a whole vector buffer element is stored by `p[i] = v;`')
            return self.index(core)
        if core['kind'] == 'UnaryOperator' and core['opcode'] == '*' and scalar_of(core['inner'][0]) == 'pointer' \
                and scalar_of(core) in SCALAR_TERMS:
            # *p = v is p[0] = v (a buffer or shared view; atomics keep their own calls)
            return self.graph.add('Index', children={'base': self.expr(core['inner'][0], address=True),
                                                     'index': self.int_value('u32', 0)})
        if core['kind'] in ('MemberExpr', 'ExtVectorElementExpr') and is_vector(scalar_of(core['inner'][0])):
            ref = self.vector_element_ref(core['inner'][0])
            if ref and member_name(core, self.source) in VECTOR_FIELDS:  # ranges[t].x = ...
                return self.vector_element_index(ref, VECTOR_FIELDS.index(member_name(core, self.source)))
            if peel(core['inner'][0])['kind'] == 'DeclRefExpr':
                return self.expr(core)  # v.x = ...: an element of a vector variable
        raise Unsupported(node, 'assign-target', 'only names and buffer elements can be assigned')

    # -- expressions --

    def expr(self, node, statement=False, address=False):
        wrapped = self.wrapping_idiom(node)
        if wrapped is None:
            wrapped = self.converted_wrapping(node)
        if wrapped is not None:
            return wrapped
        target = scalar_of(node)
        if target in NEW_INTEGERS:
            folded = integer_constant(node)
            if folded is not None:
                return self.constant(node, target, folded)
        step = conversion_step(node)
        if step is not None:  # a chain of conversions: convert one step at a time
            return self.convert_types(self.expr(step), scalar_of(step), target, node)
        core = peel(node)
        if core['kind'] == 'IntegerLiteral':
            return self.literal(core, 'u32' if self.unsigned else target)
        if target in ('u32', 'bool') and scalar_of(core) == 'i32' and nonnegative_int(core):
            return self.as_unsigned(core)
        value = self.core(core, statement, address)
        return self.convert_types(value, scalar_of(core), target, node)

    def wrapping_idiom(self, node):
        """Read back how qpel spells wrapping int arithmetic in C, so that re-importing
        generated code gives the original program:
        (int)((unsigned)a op (unsigned)b) is a op b, and (int)(0u - (unsigned)a) is -a."""
        n = strip_parens(node)
        if n['kind'] not in EXPLICIT_CASTS or scalar_of(n) not in ('i32', 'i64'):
            return None
        while True:  # (long long)((long long)(...)): qpel's min/max convert their operands again
            inner = strip_parens(cast_operand(n))
            if inner['kind'] in EXPLICIT_CASTS and scalar_of(inner) == scalar_of(n):
                n = inner
            else:
                break
        unsigned = 'u' + scalar_of(n)[1:]
        inner = strip_parens(cast_operand(n))
        if inner['kind'] != 'BinaryOperator' or scalar_of(inner) != unsigned or inner['opcode'] not in ('+', '-', '*', '<<'):
            return None
        left, right = (signed_operand(x, unsigned) for x in inner['inner'])
        op = inner['opcode']
        if op == '<<' and left is not None:
            return self.graph.add('Binary', {'operator': '<<'}, {'left': self.expr(left), 'right': self.shift_count(inner['inner'][1])})
        if op == '-' and right is not None and is_zero(inner['inner'][0]):
            return self.graph.add('Unary', {'operator': '-'}, {'operand': self.expr(right)})
        if op != '<<' and left is not None and right is not None:
            return self.graph.add('Binary', {'operator': op}, {'left': self.expr(left), 'right': self.expr(right)})
        return None

    def converted_wrapping(self, node):
        """(unsigned)(int)((unsigned)a op (unsigned)b), qpel's spelling of U32Cast(a op b) on ints."""
        n = strip_parens(node)
        if n['kind'] not in EXPLICIT_CASTS or scalar_of(n) != 'u32':
            return None
        inner = strip_parens(cast_operand(n))
        while inner['kind'] in EXPLICIT_CASTS and scalar_of(inner) == 'u32' and scalar_of(cast_operand(inner)) == 'u32':
            inner = strip_parens(cast_operand(inner))  # (unsigned)(unsigned x) is x
        if inner['kind'] in EXPLICIT_CASTS and scalar_of(inner) == 'u32':
            return self.converted_wrapping(inner)
        signed = self.wrapping_idiom(inner)
        return None if signed is None else self.graph.add('U32Cast', children={'operand': signed})

    def shift_count(self, node):
        """qpel masks shift counts as ((unsigned)(c) & 31u); masking again changes nothing."""
        n = strip_parens(node)
        if n['kind'] == 'BinaryOperator' and n['opcode'] == '&' and peel(n['inner'][1])['kind'] == 'IntegerLiteral' \
                and int(peel(n['inner'][1])['value']) in (31, 63):
            count = strip_parens(n['inner'][0])
            if count['kind'] in EXPLICIT_CASTS and scalar_of(count) == 'u32':
                count = cast_operand(count)
            return self.expr(count)
        return self.expr(node)

    def as_unsigned(self, core):
        """C promotes unsigned char to int. Where such an int provably never goes negative
        (see nonnegative_int), qpel keeps it unsigned, as the source author meant."""
        self.unsigned += 1
        try:
            return self.core(core, False, False)
        finally:
            self.unsigned -= 1

    def literal(self, n, target):
        value = int(n['value'])
        if target in NEW_INTEGERS:
            return self.constant(n, target, value)
        if value > U32_MAX and target == 'f32':  # a 64-bit literal converted to float: rounded to nearest
            return self.graph.add('F32Literal', {'value': repr(float(struct.unpack('<f', struct.pack('<f', float(value)))[0]))})
        if value > U32_MAX and scalar_of(n) in ('u64', 'i64') and target in ('u8', 'u32', 'i32'):
            return self.constant(n, target, value)  # a 64-bit literal converted: C keeps the low bits
        if value > U32_MAX:
            raise Unsupported(n, 'literal-range', 'integer literal does not fit in 32 bits')
        if target == 'f32':
            return self.graph.add('F32Literal', {'value': str(value)})
        if target == 'i32':
            if value >= 2**31:  # an unsigned literal converted to int: C wraps it (4294967295u -> -1)
                return self.constant(n, 'i32', value - 2**32)
            return self.graph.add('I32Literal', {'value': str(value)})
        if target == 'u8':
            return self.graph.add('U8Cast', children={'operand': self.literal(n, 'u32')})
        unsigned = scalar_of(n) == 'u32' or value >= 2**31
        return self.graph.add('Literal', {'value': str(value) + ('u' if unsigned else '')})

    def constant(self, n, target, value):
        """A Constant of `target`, with C's conversion of value to it (wrapping)."""
        size, signed = type_bits(target), is_signed(target)
        value %= 2**size
        if signed and value >= 2**(size - 1):
            value -= 2**size
        return self.graph.add('Constant', {'scalar': target, 'value': str(value)})

    def convert_types(self, value, source, target, outer):
        add = self.graph.add
        if source == target or 'pointer' in (source, target):
            return value
        if self.unsigned and (source, target) in (('u8', 'i32'), ('i32', 'u32'), ('u32', 'i32')):
            return value
        if target == 'bool':
            return value  # qpel conditions accept integers, as C does
        if (source, target) == ('u8', 'u32'):
            return value
        if source == 'bool' and target in ('u32', 'i32'):
            one, zero = (('I32Literal', '1'), ('I32Literal', '0')) if target == 'i32' else (('Literal', '1u'), ('Literal', '0u'))
            return add('Select', children={'condition': value, 'yes': add(one[0], {'value': one[1]}),
                                           'no': add(zero[0], {'value': zero[1]})})
        if source == 'bool' and target in INTEGER_TERMS | {'f32'}:  # C converts true to 1 and false to 0
            if target == 'f32':
                one, zero = add('F32Literal', {'value': '1'}), add('F32Literal', {'value': '0'})
            else:
                one, zero = (add('Constant', {'scalar': target, 'value': v}) for v in ('1', '0'))
            return add('Select', children={'condition': value, 'yes': one, 'no': zero})
        if target == 'i32' and source in ('u8', 'u32'):
            return add('I32Cast', children={'operand': value})
        if (source, target) == ('i32', 'u32'):
            node = self.graph.nodes[value]
            if node['kind'] == 'I32Cast' and self.graph.nodes[node['children']['operand'][0]]['kind'] == 'Member':
                return node['children']['operand'][0]  # an int extent used as unsigned is the shape member itself
            return add('U32Cast', children={'operand': value})
        if target == 'f32' and source in ('u32', 'u8', 'i32', 'f16'):
            return add('F32Cast', children={'operand': value})
        if (source, target) == ('f32', 'f16'):
            return add('F16Cast', children={'operand': value})
        if target == 'u8' and source in ('u32', 'i32'):
            return add('U8Cast', children={'operand': value})  # keeps the low 8 bits, as C does
        if source in INTEGER_TERMS and target in INTEGER_TERMS:
            # A widening that keeps signedness and the arithmetic type (u8 -> u32, i8 -> i16) changes
            # nothing qpel computes. Any other conversion is explicit: i32 -> i64 must stay, or
            # (long long)i * (long long)n would multiply in 32 bits (found by fuzz/).
            if fits(target, source) and is_signed(source) == is_signed(target) and \
                    type_arithmetic(source) == type_arithmetic(target):
                return value
            return add('Cast', {'to': target}, {'operand': value})
        if target in INTEGER_TERMS and source == 'f32':
            return add('Cast', {'to': target}, {'operand': value})  # C leaves out-of-range undefined; qpel saturates
        if target == 'f32' and source in INTEGER_TERMS | {'bf16'}:
            return add('Cast', {'to': 'f32'}, {'operand': value})
        if target == 'bf16' and source == 'f32':
            return add('Cast', {'to': 'bf16'}, {'operand': value})
        raise Unsupported(outer, 'conversion', f'conversion from {source} to {target} is not in qpel')

    def type_node(self, t):
        kind, attributes = node_spec(t)
        return self.graph.add(kind, attributes)

    def name(self, ident):
        return self.graph.add('Name', links={'declaration': ident})

    def typed(self, entry, node):
        """Stop at a use of a parameter whose type a survey has already reported."""
        if entry and entry[0] == 'untyped':
            raise Unsupported(node, 'untyped', f'{entry[1]} has no qpel type')
        return entry

    def core(self, n, statement, address):
        add = self.graph.add
        kind = n['kind']
        if '_facade_error' in n:
            raise Unsupported(n, 'library', n['_facade_error'])
        present = self.presence(n)
        if present is not None:
            return present
        if kind == 'UnaryExprOrTypeTraitExpr':
            value = integer_constant(n)
            if value is None or scalar_of(n) not in INTEGER_TERMS:
                raise Unsupported(n, 'expression', f'{n.get("name", "sizeof")} of this type is not in qpel')
            return self.int_value(scalar_of(n), value)
        if kind == 'CXXScalarValueInitExpr' and scalar_of(n) in SCALAR_TERMS - {'bool'}:
            return self.zero(scalar_of(n))  # T(): zero
        if is_vector(scalar_of(n)) and (kind in ('InitListExpr', 'MetalVectorInitExpr', 'CXXConstructExpr')
                                        or self.vector_load_base(n) is not None):
            return self.vector_value(n, scalar_of(n))
        if kind == 'MetalAsTypeCastExpr':  # as_type<uint>(x): the bits as another type (IR version 6)
            return self.bitcast(n, n['inner'][0], scalar_of(n))
        if kind == 'CXXConstructExpr' and len(n.get('inner', ())) == 1 and \
                ' '.join(words(n['type']['qualType'])) == ' '.join(words(n['inner'][0]['type']['qualType'])):
            return self.expr(n['inner'][0])  # a copy (a dim3 or vector passed by value)
        if kind == 'ArraySubscriptExpr' and self.vector_element_ref(n):
            ref = self.vector_element_ref(n)
            return add('VectorValue', {'element': ref[3], 'length': ref[2]},
                       {'elements': [self.vector_element_index(ref, k) for k in range(ref[2])]})
        if kind == 'DeclRefExpr':
            ref = n['referencedDecl']
            entry = self.typed(self.scope.get(ref['id']), n)
            if entry is None and ref['id'] in self.function_constants:
                return self.constant_value(self.function_constants[ref['id']], scalar_of(n), n)
            if entry is None and ref['id'] in self.global_values:
                name, init = self.global_values[ref['id']]
                message = f'program-scope constant {name} was read as its value'
                if message not in self.adaptations:
                    self.adaptations.append(message)
                return self.expr(init)
            if entry is None and ref['id'] in self.globals:
                raise Unsupported(n, 'global-variable', f'{self.globals[ref["id"]]} is a program-scope variable; '
                                                        'qpel has no global data yet')
            if entry is None and ref.get('name') == 'warpSize' and self.dialect.name == 'cuda':
                return self.literal({'value': '32'}, scalar_of(n))  # 32 lanes on every NVIDIA GPU so far
            if entry is None:
                if ref.get('name') in ('threadIdx', 'blockIdx', 'blockDim', 'gridDim'):
                    raise Unsupported(n, 'thread-builtin', 'only blockIdx.x * blockDim.x + threadIdx.x is supported')
                raise Unsupported(n, 'unknown-name', f'{ref.get("name")} is not a parameter or local')
            if entry[0] == 'shared-scalar':
                return add('Index', children={'base': self.name(entry[1]), 'index': self.int_value('u32', 0)})
            if entry[0] == 'param' and ref['id'] in self.bool_params:
                return add('Binary', {'operator': '!='}, {'left': self.name(entry[1]), 'right': self.int_value('u32', 0)})
            if entry[0] == 'local' and is_array(entry[2]):
                raise Unsupported(n, 'array-use', f'local array {ref.get("name")} can only be indexed in qpel '
                                                  '(not passed, copied or used as a pointer)')
            if entry[0] == 'grid':
                index = self.thread_index(entry[1], n)
                own = scalar_of(n)  # `const int i = blockIdx.x * blockDim.x + threadIdx.x` is an int
                return self.convert_types(index, 'u32', own, n) if own in INTEGER_TERMS and own != 'u32' else index
            if entry[0] == 'position':
                raise Unsupported(n, 'thread-builtin', 'use the .x, .y and .z components of the thread position')
            if entry[0] == 'extent':
                member = add('Member', {'member': entry[2]}, {'base': self.name(entry[1])})
                return add('I32Cast', children={'operand': member}) if entry[3] == 'i32' else member
            if entry[0] == 'count':
                member = lambda m: add('Member', {'member': m}, {'base': self.name(entry[1])})
                xy = add('Binary', {'operator': '*'}, {'left': member('x'), 'right': member('y')})
                volume = add('Binary', {'operator': '*'}, {'left': xy, 'right': member('z')})
                # the count had its own type (int k, long long n); the shape's volume is unsigned
                return self.convert_types(volume, 'u32', entry[2], n) if entry[2] != 'u32' else volume
            if entry[0] == 'group-builtin':
                if entry[2]:
                    raise Unsupported(n, 'thread-builtin', 'use the .x, .y and .z components of a thread builtin')
                return add(GROUP_NODES[entry[1]], {'axis': 'x'})
            if entry[0] == 'subgroup-builtin':
                node = add(entry[1])
                return self.convert_types(node, 'u32', entry[2], n) if entry[2] in INTEGER_TERMS and entry[2] != 'u32' else node
            if entry[0] == 'builtin':
                raise Unsupported(n, 'thread-builtin', f'{entry[1]} is only supported as qpel uses it')
            return self.name(entry[1])
        if kind == 'IntegerLiteral':
            return self.literal(n, 'u32' if self.unsigned else scalar_of(n))
        if kind == 'CXXBoolLiteralExpr':
            return add('Literal', {'value': 'true' if n['value'] else 'false'})
        if kind == 'FloatingLiteral':
            if scalar_of(n) != 'f32':
                raise Unsupported(n, 'double', 'double precision is not in qpel; write a float literal such as 0.5f')
            return add('F32Literal', {'value': n['value']})
        method = self.block_method(n)
        if method in ('thread_rank', 'sync', 'size', 'num_threads'):
            if method == 'sync':
                return add('Barrier')
            size = lambda a: add('GroupSize', {'axis': a})
            if method == 'thread_rank':  # x + size.x * (y + size.y * z)
                inner = add('Binary', {'operator': '+'}, {'left': add('LocalIndex', {'axis': 'y'}), 'right': add(
                    'Binary', {'operator': '*'}, {'left': size('y'), 'right': add('LocalIndex', {'axis': 'z'})})})
                return add('Binary', {'operator': '+'}, {'left': add('LocalIndex', {'axis': 'x'}), 'right': add(
                    'Binary', {'operator': '*'}, {'left': size('x'), 'right': inner})})
            xy = add('Binary', {'operator': '*'}, {'left': size('x'), 'right': size('y')})
            return add('Binary', {'operator': '*'}, {'left': xy, 'right': size('z')})
        if method:
            raise Unsupported(n, 'thread-builtin', f'thread_block::{method}() is supported through .x, .y and .z')
        if cooperative_rank(n) and (self.groups or self.grid3d or cooperative_rank(n) == 'block'):
            raise Unsupported(n, 'thread-builtin', 'cooperative groups thread_rank() is supported as the grid rank '
                                                   'of a one-dimensional kernel')
        if grid_axis(n) and not self.groups and (self.kernel or not getattr(self, 'hidden_params', None)):
            # (a Groups kernel, or a helper given the builtins, reads it literally: group * size + local)
            if not self.kernel:
                raise Unsupported(n, 'thread-builtin', 'the thread index is only available in kernels')
            return self.thread_index(grid_axis(n), n)
        if kind == 'BinaryOperator':
            op = n['opcode']
            if op not in BINARY:
                raise Unsupported(n, 'operator', f'operator {op} cannot be used inside an expression')
            left, right = n['inner']
            if op in COMPARISONS and not self.unsigned and all(
                    scalar_of(x) == 'i32' and nonnegative_int(x) for x in (left, right)):
                return self.as_unsigned(n)  # e.g. two unsigned chars compared after promotion to int
            if op in ('<<', '>>'):
                return add('Binary', {'operator': op}, {'left': self.expr(left), 'right': self.shift_count(right)})
            if scalar_of(n) == 'pointer':
                if op not in ('+', '-') or scalar_of(left) != 'pointer':
                    raise Unsupported(n, 'pointer', 'only pointer + integer and pointer - integer are supported')
                offset = self.offset(right) if address else self.expr(right)  # atomics keep their unsigned offsets
                return add('Binary', {'operator': op}, {'left': self.expr(left, address=True), 'right': offset})
            return add('Binary', {'operator': op}, {'left': self.expr(left), 'right': self.expr(right)})
        if kind == 'UnaryOperator':
            op = n['opcode']
            operand = n['inner'][0]
            if op in ('!', '-', '~'):
                return add('Unary', {'operator': op}, {'operand': self.expr(operand)})
            if op == '+':
                return self.expr(operand)
            if op == '&' and address and peel(operand)['kind'] == 'ArraySubscriptExpr':
                return self.element_address(peel(operand))
            if op == '*' and scalar_of(operand) == 'pointer' and scalar_of(n) in SCALAR_TERMS:
                # *p is p[0]: `*((uint32_t*)&depths[i])` reads the bits of a float as an integer
                return add('Index', children={'base': self.expr(operand, address=True), 'index': self.int_value('u32', 0)})
            raise Unsupported(n, 'operator', f'operator {op} is not supported here')
        if n.get('castKind') == 'BitCast':  # (device float*)(src + off): the bytes read as another type
            target = describe(n['type']['qualType'])
            root = peel(n['inner'][0]) if n.get('inner') else {}
            entry = self.scope.get(root.get('referencedDecl', {}).get('id')) if root.get('kind') == 'DeclRefExpr' else None
            if entry and entry[0] in ('local', 'param') and (is_shared(entry[2]) or is_shared_view(entry[2])) \
                    and target[0] == 'pointer' and target[2] == view_element(entry[2]):
                return self.expr(n['inner'][0], address=True)  # (threadgroup T*) of memory already of T
            if target[0] != 'pointer' or target[2] not in SCALAR_TERMS - {'bool'}:
                raise Unsupported(n, 'conversion', f'reinterpreting as {n["type"]["qualType"]} is not in qpel')
            return add('View', {'element': target[2]}, {'base': self.expr(n['inner'][0], address=True)})
        if kind in EXPLICIT_CASTS:  # explicit cast to float or half, see peel()
            operand = peel(n['inner'][0])
            source = scalar_of(operand)
            allowed = INTEGER_TERMS | {'f32', 'f16', 'bf16'} if scalar_of(n) == 'f32' else {'f32'}
            if source == scalar_of(n) == 'f16':
                return self.core(operand, False, False)  # (half)h is h
            if source == 'bool':  # (float)(x > 0): 1 or 0
                return self.convert_types(self.expr(operand), 'bool', scalar_of(n), n)
            inner = n['inner'][0]
            if has_explicit_cast(inner):  # (float)(int)i: convert to int first, then to float
                source = scalar_of(inner)
                if source not in allowed:
                    raise Unsupported(n, 'conversion', f'conversion from {source} to {scalar_of(n)} is not in qpel')
                value = self.expr(inner)
                if scalar_of(inner) == scalar_of(n):
                    return value  # the conversion to float is already inside (a NoOp cast around it)
                return add('F32Cast' if scalar_of(n) == 'f32' else 'F16Cast', children={'operand': value})
            if source not in allowed:
                raise Unsupported(n, 'conversion', f'conversion from {source} to {scalar_of(n)} is not in qpel')
            value = self.core(operand, False, False)
            return add('F32Cast' if scalar_of(n) == 'f32' else 'F16Cast', children={'operand': value})
        if kind == 'ConditionalOperator':
            c, yes, no = n['inner']
            return add('Select', children={'condition': self.expr(c), 'yes': self.expr(yes), 'no': self.expr(no)})
        if kind == 'ArraySubscriptExpr' and self.builtin_component(n):
            entry, field = self.builtin_component(n)  # Metal vectors also read by subscript: tgpig[1] is tgpig.y
            if entry[0] == 'position':
                return self.thread_index(field, n)
            return add(GROUP_NODES[entry[1]], {'axis': field})
        if kind == 'ArraySubscriptExpr':
            return self.index(n)
        if kind in ('MemberExpr', 'ExtVectorElementExpr'):
            field = member_name(n, self.source)
            base = peel(n['inner'][0])
            entry = self.scope.get(base.get('referencedDecl', {}).get('id')) if base['kind'] == 'DeclRefExpr' else None
            if entry and entry[0] == 'position' and field in ('x', 'y', 'z'):
                return self.thread_index(field, n)
            method = self.block_method(base)
            if method in ('group_index', 'thread_index', 'group_dim', 'dim_threads') and field in ('x', 'y', 'z'):
                kind = {'group_index': 'GroupIndex', 'thread_index': 'LocalIndex'}.get(method, 'GroupSize')
                return add(kind, {'axis': field})
            if entry and entry[0] == 'group-builtin' and field in ('x', 'y', 'z'):
                return add(GROUP_NODES[entry[1]], {'axis': field})
            if not self.kernel and decl_name(base) in CUDA_BUILTINS and (CUDA_BUILTINS[decl_name(base)], field) \
                    in getattr(self, 'hidden_params', {}):
                return self.name(self.hidden_params[(CUDA_BUILTINS[decl_name(base)], field)])
            if self.groups and decl_name(base) in CUDA_BUILTINS and field in ('x', 'y', 'z'):
                return add(GROUP_NODES[CUDA_BUILTINS[decl_name(base)]], {'axis': field})
            if is_vector(scalar_of(n['inner'][0])) and not (entry and entry[0] in ('position', 'group-builtin')):
                vtype = scalar_of(n['inner'][0])
                if field not in VECTOR_FIELDS[:vector_length(vtype)]:
                    raise Unsupported(n, 'member', f'{field} of a {vtype}: only single elements .x .y .z .w')
                ref = self.vector_element_ref(n['inner'][0])
                if ref:  # bboxes[i].w reads one element
                    return self.vector_element_index(ref, VECTOR_FIELDS.index(field))
                return add('Member', {'member': field}, {'base': self.vector_value(n['inner'][0], vtype)})
            if entry and entry[0] in ('param', 'local') and is_struct(entry[2]):
                return add('Member', {'member': self.safe_name(field)}, {'base': self.name(entry[1])})
            if entry and entry[0] == 'param' and entry[2] == 'shape3' and field in ('x', 'y', 'z'):
                return add('Member', {'member': field}, {'base': self.name(entry[1])})
            if decl_name(base) in ('threadIdx', 'blockIdx', 'blockDim', 'gridDim'):
                raise Unsupported(n, 'thread-builtin', 'only blockIdx.x * blockDim.x + threadIdx.x is supported')
            raise Unsupported(n, 'member', 'only shape.x, shape.y and shape.z members are supported')
        if kind == 'CallExpr':
            return self.call(n, statement)
        if kind == 'CXXConstructExpr' and len(n.get('inner', ())) == 1 and scalar_of(n) in ('f16', 'bf16', 'f32'):
            argument = n['inner'][0]  # __half(f), __nv_bfloat16(f): a conversion written as a constructor
            return self.convert_types(self.expr(argument), scalar_of(argument), scalar_of(n), n)
        raise Unsupported(n, 'expression', f'{kind} is not supported')

    def thread_index(self, axis, node):
        if self.groups:  # Metal's position in the grid, as CUDA spells it: group * size + local
            add = self.graph.add
            product = add('Binary', {'operator': '*'}, {'left': add('GroupIndex', {'axis': axis}),
                                                        'right': add('GroupSize', {'axis': axis})})
            return add('Binary', {'operator': '+'}, {'left': product, 'right': add('LocalIndex', {'axis': axis})})
        if self.grid3d:
            return self.graph.add('ThreadIndex', {'axis': axis})
        if axis != 'x':
            raise Unsupported(node, 'thread-builtin', 'internal: 1D kernel with a y/z index')
        return self.graph.add('GridIndex')

    def buffer_kind(self, node):
        core = peel(node)
        entry = self.scope.get(core.get('referencedDecl', {}).get('id')) if core['kind'] == 'DeclRefExpr' else None
        self.typed(entry, node)
        if core.get('castKind') == 'BitCast' and scalar_of(core) == 'pointer':
            target = describe(core['type']['qualType'])  # ((uint*)(depths + i))[0]: a view, as qpel writes it
            if target[2] in SCALAR_TERMS - {'bool'}:
                return type_buffer(target[2], 'read' if target[1] else 'write')
        if core['kind'] != 'DeclRefExpr' and scalar_of(core) == 'pointer':
            raise Unsupported(node, 'buffer', 'index a buffer or a buffer local, not a pointer expression')
        if not entry or entry[0] not in ('param', 'local') or not type_is_buffer(entry[2]):
            raise Unsupported(node, 'buffer', 'only buffers and buffer locals can be indexed')
        return entry[2]

    def buffer(self, node):
        self.buffer_kind(node)
        core = peel(node)
        if core.get('castKind') == 'BitCast':
            return self.core(core, False, True)  # a view (see buffer_kind)
        return self.name(self.scope[core['referencedDecl']['id']][1])

    def offset(self, node):
        """A pointer offset, which qpel takes unsigned. A negative int offset from a buffer's
        start is out of bounds in C too, so reading it as unsigned changes no valid program."""
        value = self.expr(node)
        return self.graph.add('U32Cast', children={'operand': value}) if scalar_of(node) == 'i32' else value

    def element_address(self, node):
        base, index = node['inner']
        return self.graph.add('Binary', {'operator': '+'}, {'left': self.buffer(base), 'right': self.offset(index)})

    def is_atomic_element(self, node):
        node = peel(node)
        return node['kind'] == 'ArraySubscriptExpr' and self.local_array(node['inner'][0]) is None and \
            type_access(self.buffer_kind(node['inner'][0])) == 'atomic'

    def index(self, node):
        base, index = node['inner']
        array = self.local_array(base)
        if array is not None:
            return self.graph.add('Index', children={'base': self.name(array), 'index': self.expr(index)})
        core = peel(base)
        if core['kind'] == 'BinaryOperator' and core['opcode'] in ('+', '-') and scalar_of(core) == 'pointer':
            # (p + i + j)[k], as qpel writes *(p + i + j): an element of the offset view, read as *p is
            return self.graph.add('Index', children={'base': self.expr(base, address=True), 'index': self.expr(index)})
        if self.is_atomic_element(node):
            # CUDA reads buffers that other threads update atomically with plain loads;
            # qpel makes them relaxed atomic loads.
            self.note_atomic_access(base)
            return self.graph.add('AtomicLoad', children={'address': self.element_address(node)})
        return self.graph.add('Index', children={'base': self.buffer(base), 'index': self.expr(index)})

    def note_atomic_access(self, base):
        message = f'{self.current}: plain accesses to atomic buffer {decl_name(base)} became relaxed atomic loads and stores'
        if message not in self.adaptations:
            self.adaptations.append(message)

    def presence(self, n):
        """BufferPresent (IR version 9) for a null test of an optional buffer parameter, or for qpel's
        Metal test of a presence flag (`qpel_present_x != 0u`); None for anything else."""
        add = self.graph.add
        found = null_tested(n)
        if found:
            entry = self.scope.get(found[0])
            if entry and entry[0] == 'param' and self.graph.nodes[entry[1]]['attributes'].get('optional'):
                test = add('BufferPresent', links={'parameter': entry[1]})
                return add('Unary', {'operator': '!'}, {'operand': test}) if found[1] else test
            return None
        if n.get('kind') == 'BinaryOperator' and n.get('opcode') in ('==', '!=') and len(n.get('inner', ())) == 2:
            for a, b in (n['inner'], n['inner'][::-1]):
                core = peel(a)
                entry = self.scope.get(core.get('referencedDecl', {}).get('id')) if core.get('kind') == 'DeclRefExpr' else None
                if entry and entry[0] == 'presence' and integer_constant(b) == 0:
                    param = next(i for i in self.graph.nodes.values() if i['kind'] == 'Parameter'
                                 and i['attributes']['name'] == self.safe_name(entry[1])
                                 and i['attributes'].get('optional'))
                    test = add('BufferPresent', links={'parameter': param['id']})
                    return add('Unary', {'operator': '!'}, {'operand': test}) if n['opcode'] == '==' else test
        return None

    def callee_name(self, node):
        """The callee of node when it is a call, else None."""
        node = peel(node)
        return self.callee(node) if node['kind'] == 'CallExpr' else None

    def callee(self, call):
        """Name of the function a call reaches; a template instantiation has its own name."""
        target = peel(call['inner'][0])
        if target['kind'] == 'UnaryOperator' and target['opcode'] == '&':
            target = peel(target['inner'][0])  # a function passed as a template argument: op = &op_abs
        if target['kind'] == 'DeclRefExpr':
            return self.instances.get(target['referencedDecl']['id'], target['referencedDecl'].get('name'))
        return decl_name(call['inner'][0])

    def subgroup_call(self, n, callee, args):
        """A shuffle, a subgroup reduction or qpel's own lane, index and size (IR version 5)."""
        add = self.graph.add
        if callee in ('qpel_subgroup_lane', 'qpel_subgroup_index', 'qpel_subgroup_size'):
            return add({'lane': 'SubgroupLane', 'index': 'SubgroupIndex', 'size': 'SubgroupSize'}[callee[14:]])
        if callee.startswith('__shfl'):  # CUDA: (mask, value, lane or delta[, width])
            if len(args) not in (3, 4):
                raise Unsupported(n, 'call', f'{callee} takes a mask, a value, a lane and a width')
            mask = integer_constant(args[0])
            if mask is None or mask % 2**32 != 0xffffffff:
                raise Unsupported(n, 'subgroup-mask', f'{callee}: only the full mask 0xffffffff is portable '
                                                      '(Metal SIMD-group functions take every active lane)')
            if len(args) == 4 and args[3].get('kind') != 'CXXDefaultArgExpr' and integer_constant(args[3]) != 32:
                raise Unsupported(n, 'subgroup-width', f'{callee}: only the width 32 (a whole warp) is supported')
            args = args[1:3]
        if callee in SHUFFLE_CALLS:
            if len(args) != 2:
                raise Unsupported(n, 'call', f'{callee} takes a value and a lane, mask or delta')
            operand, kind = self.expr(args[1]), scalar_of(args[1])
            if kind in INTEGER_TERMS and kind != 'u32':  # the lane, mask or delta is taken as u32 (NUMERICS.md)
                operand = self.convert_types(operand, kind, 'u32', args[1])
            return add('SubgroupShuffle', {'mode': SHUFFLE_CALLS[callee]}, {'value': self.expr(args[0]), 'operand': operand})
        if len(args) != 1:
            raise Unsupported(n, 'call', f'{callee} takes one value')
        if callee == 'simd_sum' and scalar_of(args[0]) == 'f32':
            message = ('simd_sum of float32 was read as qpel.subgroup_add, which adds in a fixed order (xor 16, 8, 4, '
                       '2, 1); Apple does not document the order of simd_sum')
            if message not in self.adaptations:
                self.adaptations.append(message)
        return add('SubgroupReduce', {'operator': REDUCE_CALLS[callee]}, {'value': self.expr(args[0])})

    def call(self, n, statement):
        add = self.graph.add
        callee = self.callee(n)
        args = n['inner'][1:]
        folded = constexpr_call(n, self.function_decls)
        if folded is not None and scalar_of(n) in INTEGER_TERMS:
            return self.int_value(scalar_of(n), folded)  # ggml_cuda_get_physical_warp_size(): 32
        if callee in ('__syncthreads', 'threadgroup_barrier'):  # (Metal's flags: qpel's barrier orders both)
            return add('Barrier')
        if callee in ('__syncthreads_count', 'qpel_barrier_count'):
            return add('BarrierCount', children={'value': self.expr(predicate(args[0]))})
        template = decl_name(n['inner'][0])  # the template's own name: instances may be renamed (_f32, _i32)
        if template in SUBGROUP_CALLS:
            return self.subgroup_call(n, template, args)
        if callee and callee.startswith('make_') and callee[5:] in VECTOR_NAMES:  # CUDA's make_float3(x, y, z)
            return self.vector_elements(n, args, VECTOR_NAMES[callee[5:]])
        if callee in self.functions:
            decl, ident, kernel = self.functions[callee]
            if kernel:
                raise Unsupported(n, 'call-kernel', 'kernels cannot be called from device code')
            arguments = [self.expr(a, address=True) for a in args]
            for role, axis in self.builtin_reads(callee):  # the thread builtins the helper reads
                arguments.append(add(GROUP_NODES[role], {'axis': axis}) if self.kernel
                                 else self.name(self.hidden_params[(role, axis)]))
            return add('Call', children={'arguments': arguments}, links={'function': ident})

        def need(count):
            if len(args) != count:
                raise Unsupported(n, 'call', f'{callee} expects {count} arguments')
        target = peel(n['inner'][0]) if n.get('inner') else {}
        if target.get('kind') == 'DeclRefExpr' and target['referencedDecl'].get('kind') == 'CXXMethodDecl' \
                and callee in ('max', 'min', 'lowest') and not args:
            # std::numeric_limits<T>::max() and friends (the class is in a system header): a constant
            limit = scalar_of(n)
            if limit == 'f32':  # FLT_MAX, -FLT_MAX and FLT_MIN (the least normal float), exact float values
                value = {'max': 3.4028234663852886e38, 'lowest': -3.4028234663852886e38, 'min': 1.1754943508222875e-38}[callee]
                magnitude = self.graph.add('F32Literal', {'value': repr(abs(value))})
                return self.negated(magnitude, 'f32') if value < 0 else magnitude
            if limit not in INTEGER_TERMS:
                raise Unsupported(n, 'numeric-limits', f'{callee}() of {limit} is only supported for integers')
            size, signed = type_bits(limit), is_signed(limit)
            value = {'max': 2**(size - signed) - 1, 'min': -2**(size - 1) if signed else 0,
                     'lowest': -2**(size - 1) if signed else 0}[callee]
            return self.constant(n, limit, value)
        if callee in MINIMUM or callee in MAXIMUM:
            need(2)
            result = scalar_of(n)
            if result == 'f32' and all(scalar_of(a) == 'f32' for a in args):  # CUDA's min(float, float) is fminf
                return add('Math', {'function': 'fmin' if callee in MINIMUM else 'fmax'},
                           {'arguments': [self.expr(a) for a in args]})
            # CUDA's min(unsigned int, int) and friends compare in the result type
            operands = [self.convert_types(self.expr(a), scalar_of(a), result, a)
                        if result in INTEGER_TERMS and scalar_of(a) in INTEGER_TERMS and scalar_of(a) != result else self.expr(a)
                        for a in args]
            return add('Minimum' if callee in MINIMUM else 'Maximum', children={'left': operands[0], 'right': operands[1]})
        if callee in FMA:
            need(3)
            return add('F32Fma', children={r: self.expr(a) for r, a in zip(('left', 'right', 'addend'), args)})
        if callee in TO_F16 or callee in TO_F32:
            need(1)
            return add('F16Cast' if callee in TO_F16 else 'F32Cast', children={'operand': self.expr(args[0])})
        if callee in ('qpel_i32_div', 'qpel_i32_rem', 'qpel_i64_div', 'qpel_i64_rem', 'qpel_u32_div', 'qpel_u32_rem'):  # qpel's / and %
            need(2)
            return add('Binary', {'operator': '/' if callee.endswith('div') else '%'},
                       {'left': self.expr(args[0]), 'right': self.expr(args[1])})
        if callee in ('copysign', 'copysignf') and len(args) == 2 and self.callee_name(args[0]) in ('round', 'roundf') \
                and same_expression(peel(args[0])['inner'][1], args[1]):
            # qpel's Metal spelling of round, which keeps the sign of a zero result
            return add('Math', {'function': 'round'}, {'arguments': [self.expr(args[1])]})
        if callee == 'clamp' and len(args) == 3 and all(scalar_of(a) == 'f32' for a in args):
            # Metal's clamp(x, lo, hi) is min(max(x, lo), hi)
            low = add('Math', {'function': 'fmax'}, {'arguments': [self.expr(args[0]), self.expr(args[1])]})
            return add('Math', {'function': 'fmin'}, {'arguments': [low, self.expr(args[2])]})
        if callee == 'select' and len(args) == 3 and scalar_of(args[2]) == 'bool':
            # Metal's select(a, b, c) is c ? b : a
            return add('Select', children={'condition': self.expr(args[2]), 'yes': self.expr(args[1]), 'no': self.expr(args[0])})
        if callee in MATH_CALLS and all(scalar_of(a) == 'f32' for a in args):
            function = MATH_CALLS[callee]
            need(MATH[function])
            return add('Math', {'function': function}, {'arguments': [self.expr(a) for a in args]})
        if callee in ROUNDED:  # CUDA's separately rounded float operations are qpel's float operators
            need(2)
            return add('Binary', {'operator': ROUNDED[callee]}, {'left': self.expr(args[0]), 'right': self.expr(args[1])})
        if callee in BITCAST_CALLS:  # __float_as_uint(x) and the others, qpel's qpel_bits_as_f32
            need(1)
            return self.bitcast(n, args[0], BITCAST_CALLS[callee] or scalar_of(n))
        if callee in SATURATE:  # qpel's saturating float -> integer conversion
            need(1)
            return add('Cast', {'to': SATURATE[callee]}, {'operand': self.expr(args[0])})
        if callee in TO_BF16:
            need(1)
            return add('Cast', {'to': 'bf16'}, {'operand': self.expr(args[0])})
        if callee == 'qpel_sum_f32':
            need(3)
            return add('ReduceSumF32', children={'buffer': self.expr(args[0], address=True),
                                                 'start': self.expr(args[1]), 'count': self.expr(args[2])})
        if callee == 'qpel_group_sum_f32':
            return add('WorkgroupSumF32', children={'value': self.expr(args[0])})
        if callee in METAL_EXCHANGES and self.dialect.name == 'metal':
            return self.metal_exchange(n, args)
        if callee in self.dialect.atomics and self.dialect.atomics[callee][0] == 'AtomicCompareExchange':
            need(3)  # atomicCAS(address, expected, value): the value found
            pointee = describe(peel(args[0]).get('type', {}).get('qualType', ''))
            if pointee[0] == 'pointer' and pointee[2] not in ('u32', 'i32', 'atomic', 'atomic_i32'):
                raise Unsupported(n, 'atomic-type', f'{callee} on {pointee[2]} is not portable: Metal has no '
                                                    'compare-and-swap beyond 32 bits' if pointee[2] in ('u64', 'i64')
                                  else f'{callee} on {pointee[2]} is not in qpel; atomics are on unsigned int and int')
            return add('AtomicCompareExchange', children={'address': self.expr(args[0], address=True),
                                                          'expected': self.atomic_value(args[1], pointee),
                                                          'value': self.atomic_value(args[2], pointee)})
        if callee in self.dialect.atomics:
            kind, has_value, has_order = self.dialect.atomics[callee]
            need(1 + has_value + has_order)
            if has_order and decl_name(args[-1]) != 'memory_order_relaxed':
                raise Unsupported(n, 'memory-order', 'only memory_order_relaxed atomics are in qpel')
            pointee = describe(peel(args[0]).get('type', {}).get('qualType', ''))
            if pointee[0] == 'pointer' and pointee[2] not in ATOMIC_POINTEES:
                raise Unsupported(n, 'atomic-type', f'{callee} on {pointee[2]} is not in qpel; atomics are on unsigned int, '
                                                    'int, float and unsigned 64-bit')
            value = self.atomic_value(args[1], pointee) if has_value else None
            if kind == 'AtomicSub':  # atomicSub(p, v) is atomicAdd(p, -v): exact, for floats too
                kind, value = 'AtomicAdd', self.negated(value, ATOMIC_SCALARS.get(pointee[2], pointee[2]))
            if kind == 'AtomicAdd' and peel(args[1])['kind'] == 'IntegerLiteral' and int(peel(args[1])['value']) == 0:
                kind, value = 'AtomicLoad', None  # CUDA's atomic read idiom
            if kind == 'AtomicStore' and self.dialect.name == 'cuda' and not statement:
                kind = 'AtomicExchange'  # atomicExch whose previous value is used (IR version 6)
            children = {'address': self.expr(args[0], address=True)}
            if value is not None:
                children['value'] = value
            return add(kind, children=children)
        if callee in self.rejected:
            raise Unsupported(n, 'call-rejected', f'{callee} could not be imported, see its own blockers')
        if callee in {s['name'] for s in self.unit.get('_skipped', ())}:
            raise Unsupported(n, 'ast-too-large', f'{callee} makes more JSON AST than qpel reads at once (clang_ast)')
        raise Unsupported(n, 'call', f'{callee} is not a qpel operation or a function in this file')

    def metal_exchange(self, n, args):
        """Metal's atomic_compare_exchange_weak_explicit(p, &expected, desired, order, order):
        true when it stored `desired`; otherwise `expected` gets the value found. It becomes, before the
        statement, `found = cas(p, expected, desired); exchanged = found == expected; expected = found;`
        and the call reads `exchanged`. qpel's exchange is strong, which a weak one may be."""
        if len(args) != 5:
            raise Unsupported(n, 'call-arity', f'{self.callee(n)} takes 5 arguments')
        if any(decl_name(a) != 'memory_order_relaxed' for a in args[3:]):
            raise Unsupported(n, 'memory-order', 'only memory_order_relaxed atomics are in qpel')
        if n['id'] in self.conditional_exchanges or self.prelude is None:
            raise Unsupported(n, 'atomic-cas', 'a compare-exchange that only some evaluations reach (after && or ||, '
                                               'in ?:) is not in qpel yet')
        pointee = describe(peel(args[0]).get('type', {}).get('qualType', ''))
        element = ATOMIC_SCALARS.get(pointee[2], pointee[2]) if pointee[0] == 'pointer' else None
        if element not in ('u32', 'i32'):
            raise Unsupported(n, 'atomic-type', f'compare-exchange on {pointee[2]} is not in qpel; it is on atomic_uint '
                                                'and atomic_int')
        target = peel(args[1])
        local = peel(target['inner'][0]) if target['kind'] == 'UnaryOperator' and target['opcode'] == '&' else None
        entry = self.scope.get(local.get('referencedDecl', {}).get('id')) if local and local['kind'] == 'DeclRefExpr' else None
        if entry is None or entry[0] != 'local' or entry[2] != element:
            raise Unsupported(n, 'atomic-cas', f'the expected value of a compare-exchange must be a {element} local (&x)')
        add = self.graph.add
        exchange = add('AtomicCompareExchange', children={'address': self.expr(args[0], address=True),
                                                         'expected': self.name(entry[1]),
                                                         'value': self.atomic_value(args[2], pointee)})
        found = add('Variable', {'name': self.unique_name('found')}, {'type': self.type_node(element), 'value': exchange})
        same = add('Binary', {'operator': '=='}, {'left': self.name(found), 'right': self.name(entry[1])})
        exchanged = add('Variable', {'name': self.unique_name('exchanged')}, {'type': self.type_node('bool'), 'value': same})
        self.prelude.extend([found, exchanged, add('Assign', children={'target': self.name(entry[1]), 'value': self.name(found)})])
        note = f'{self.current}: compare-exchange loops became qpel compare-and-swap (strong, as a weak one may be)'
        if note not in self.adaptations:
            self.adaptations.append(note)
        return self.name(exchanged)

    def bitcast(self, n, operand, target):
        source = scalar_of(operand)
        if (source, target) not in BITCASTS:
            raise Unsupported(n, 'bitcast', f'reading the bits of {source} as {target} is not in qpel; bitcasts are '
                                            'between float and unsigned int or int')
        return self.graph.add('Bitcast', {'to': target}, {'operand': self.expr(operand)})

    def negated(self, value, element):
        """-value in an atomic buffer's element type: unsigned integers wrap (0u - v)."""
        if element in ('u32', 'u64'):
            zero = self.graph.add('Literal', {'value': '0u'}) if element == 'u32' else \
                self.graph.add('Constant', {'scalar': 'u64', 'value': '0'})
            return self.graph.add('Binary', {'operator': '-'}, {'left': zero, 'right': value})
        return self.graph.add('Unary', {'operator': '-'}, {'operand': value})

    def atomic_value(self, node, pointee):
        """The value of an atomic call in the buffer's element type. Metal's atomic functions
        are templates, so clang leaves e.g. a uint stored into an atomic_int unconverted."""
        element = ATOMIC_SCALARS.get(pointee[2], pointee[2]) if pointee[0] == 'pointer' else 'u32'
        if element not in ATOMIC_ELEMENTS:
            element = 'u32'
        core = peel(node)
        if core['kind'] == 'IntegerLiteral':
            return self.literal(core, element)
        return self.convert_types(self.expr(node), scalar_of(node), element, node)

    # -- pipeline --

    def pipeline(self, module, plan, block_size):
        add = self.graph.add
        kernels = [d['name'] for d in self.decls if self.functions[d['name']][2]]
        if not kernels:
            raise Unsupported(None, 'no-kernel', f'{self.path} has no kernel')
        if plan is None:
            plan = self.synthesize_plan(kernels, block_size)
            for r in plan['resources']:
                if r['name'] in getattr(self, 'resource_kinds', {}):
                    r['kind'] = self.resource_kinds[r['name']]
            zeroed = self.read_first(module, self.dispatch(module, plan), plan)
            for r in plan['resources']:
                if r['name'] in zeroed:
                    r['kind'] = 'zeroed'
                    self.adaptations.append(f'{r["name"]} is updated atomically before anything writes it, '
                                            'so it starts as zeros (pass a plan if the host fills it)')
        return self.dispatch(module, plan)

    def read_first(self, module, pipeline, plan):
        """Atomic scratch resources that a stage reads before any stage writes them."""
        from qpel_pipeline import PipelineChecker, READ, WRITE
        checker = PipelineChecker(self.graph.program(module, pipeline))
        kinds = {r['name']: r['kind'] for r in plan['resources']}
        ready, found = {n for n, k in kinds.items() if k != 'scratch' and k != 'output'}, set()
        for stage in checker.children(checker.pipeline, 'stages'):
            kernel = checker.target(stage, 'kernel')
            effects = checker.effects(kernel)
            written = set()
            for binding in checker.children(stage, 'bindings'):
                name = checker.target(binding, 'resource')['attributes']['name']
                flags = effects.get(binding['links']['parameter'], 0)
                if flags & READ and name not in ready and kinds[name] == 'scratch':
                    found.add(name)
                if flags & WRITE:
                    written.add(name)
            ready |= written | found
        return found

    def dispatch(self, module, plan):
        add = self.graph.add
        resources, ids = [], {}
        optional = {b['resource'] for s in plan['stages'] if s['kernel'] in self.kernel_params for b in s['bindings']
                    if b['parameter'] in self.kernel_params[s['kernel']] and
                    self.graph.nodes[self.kernel_params[s['kernel']][b['parameter']]]['attributes'].get('optional')}
        for r in plan['resources']:
            kind, attributes = node_spec(r['type'])
            attrs = {'name': r['name'], 'kind': r['kind']} | ({'scale': r['scale']} if r.get('scale', 1) != 1 else {}) \
                | ({'length': r['length']} if r.get('length') else {}) \
                | ({'optional': True} if r.get('optional') or r['name'] in optional else {})
            ids[r['name']] = add('Resource', attrs, {'type': add(kind, attributes)})
            resources.append((r, ids[r['name']]))
        for r, ident in resources:
            if r.get('extent'):
                self.graph.nodes[ident]['links']['extent'] = ids[r['extent']]
        stages = []
        for s in plan['stages']:
            if s['kernel'] not in self.kernel_params:
                raise ValueError(f'Plan dispatches {s["kernel"]}, which is not a kernel in {self.path}')
            params = self.kernel_params[s['kernel']]
            bindings = [add('Binding', links={'parameter': params[b['parameter']], 'resource': ids[b['resource']]})
                        for b in s['bindings']]
            attrs = {'blockSize': s['blockSize'], 'completionBarrier': s['completionBarrier']}
            if s.get('domain', 'grid1d') != self.domains[s['kernel']]:
                raise ValueError(f'Plan and source disagree on how {s["kernel"]} is launched: '
                                 f'{s.get("domain", "grid1d")} vs {self.domains[s["kernel"]]}')
            if s.get('domain') in ('grid3d', 'groups'):
                attrs.update(blockHeight=s['blockShape'][1], blockDepth=s['blockShape'][2])
            if s.get('domain') == 'groups':
                attrs.update(groupsX=s['groups'][0], groupsY=s['groups'][1], groupsZ=s['groups'][2])
            stages.append(add('Dispatch', attrs,
                              {'bindings': bindings},
                              {'kernel': self.functions[s['kernel']][1], 'grid': ids[s['grid']]}))
        return add('Pipeline', {'name': plan.get('pipeline', self.module + '_launch')},
                   {'resources': [i for _, i in resources], 'stages': stages}, {'module': module})

    def shared_extents(self, kernel):
        """{buffer parameter: shape parameter} for a kernel with more than one shape. A buffer
        the launch sized (launch_sizes) keeps that size. Any other buffer takes the size of the
        buffers it is indexed with by the same local (`keys[slot]`, `values[slot]`), following
        helper calls; a buffer indexed by the thread index, or by nothing it shares, is sized by
        the grid. Two different sizes for one buffer are refused."""
        nodes = self.graph.nodes
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        def parameter(ident):
            n = nodes[ident]
            target = nodes.get(n['links'].get('declaration')) if n['kind'] == 'Name' else None
            return target['id'] if target and target['kind'] == 'Parameter' else None

        def key(ident, function):
            n = nodes[ident]
            while n['kind'].endswith('Cast') and n['children'].get('operand'):
                n = nodes[n['children']['operand'][0]]
            if n['kind'] == 'GridIndex':
                return ('grid', function)
            if n['kind'] == 'Name' and nodes.get(n['links'].get('declaration'), {}).get('kind') == 'Variable':
                return ('local', n['links']['declaration'])
            return None

        def visit(ident, function):
            n = nodes[ident]
            base = index = None
            if n['kind'] == 'Index':
                base, index = n['children']['base'][0], n['children']['index'][0]
            elif n['kind'].startswith('Atomic') and n['children'].get('address'):
                address = nodes[n['children']['address'][0]]
                if address['kind'] == 'Binary' and address['attributes'].get('operator') == '+':
                    base, index = address['children']['left'][0], address['children']['right'][0]
            elif n['kind'] == 'Call':
                callee = nodes[n['links']['function']]
                for param, argument in zip(callee['children'].get('parameters', []), n['children'].get('arguments', [])):
                    if parameter(argument):
                        union(parameter(argument), param)
            if base and parameter(base) and key(index, function):
                union(parameter(base), key(index, function))
            for values in n['children'].values():
                for child in values:
                    visit(child, function)
        for _, ident, _ in self.functions.values():
            if ident in nodes:
                visit(ident, ident)
        params = {nodes[i]['attributes']['name']: i for i in self.kernel_params[kernel].values()}
        own = self.functions[kernel][1]
        seeds = {find(('grid', own)): {self.grid_params[kernel]}}
        for b, shape in self.extents.get(kernel, {}).items():
            seeds.setdefault(find(params[b]), set()).add(shape)
        result = {}
        for name, ident in params.items():
            if not type_is_buffer(type_term(nodes[nodes[ident]['children']['type'][0]])):
                continue
            if name in self.extents.get(kernel, {}):
                result[name] = self.extents[kernel][name]
                continue
            found = seeds.get(find(ident), set())
            if len(found) > 1:
                raise Unsupported(None, 'extent', f'{kernel}: {name} is indexed together with buffers of sizes '
                                                  f'{" and ".join(sorted(found))}; pass a launch plan')
            result[name] = next(iter(found)) if found else self.grid_params[kernel]
        return result

    def synthesize_plan(self, kernels, block_size):
        """One dispatch per kernel in source order; parameters with one name share a resource."""
        resources = {}
        stages = []
        extents = {}  # buffer resource -> shape resource
        for kernel in kernels:
            grid = None
            bindings = []
            for name, ident in self.kernel_params[kernel].items():
                kind = type_term(self.graph.nodes[self.graph.nodes[ident]['children']['type'][0]])
                role = ('shape' if kind == 'shape3' else 'value' if kind in VALUE_TERMS or is_struct(kind) else 'input' if access(kind) == 'read'
                        else 'scratch' if access(kind) == 'atomic' else 'output')
                previous = resources.setdefault(name, {'name': name, 'kind': role, 'type': kind})
                if previous['type'] != kind:
                    raise Unsupported(None, 'resource-type', f'{name} has type {previous["type"]} and {kind} in different kernels')
                if role == 'shape' and (grid is None or name == self.grid_params.get(kernel)):
                    grid = name
                bindings.append({'parameter': name, 'resource': name})
            stage = {'kernel': kernel, 'grid': grid, 'blockSize': block_size, 'completionBarrier': True, 'bindings': bindings}
            if self.domains[kernel] == 'grid3d':
                stage.update(blockSize=GRID3D_BLOCK[0], domain='grid3d', blockShape=list(GRID3D_BLOCK))
            if self.domains[kernel] == 'groups' and getattr(self, 'geometry', None):
                block = list(self.geometry['block'])
                stage.update(blockSize=block[0], domain='groups', blockShape=block, groups=list(self.geometry['groups']))
            elif self.domains[kernel] == 'groups':
                # Without a plan: one thread per element of the shape, in whole groups.
                stage.update(blockSize=GROUPS_BLOCK, domain='groups', blockShape=[GROUPS_BLOCK, 1, 1],
                             groups=[f'(W*H*N + {GROUPS_BLOCK - 1})/{GROUPS_BLOCK}', '1', '1'])
            stages.append(stage)
            if len([b for b in bindings if resources[b['resource']]['kind'] == 'shape']) > 1:
                if kernel not in self.grid_params:
                    raise Unsupported(None, 'extent', f'{kernel} takes several shapes; pass a launch plan')
                for b, shape in self.shared_extents(kernel).items():
                    if extents.setdefault(b, shape) != shape:
                        raise Unsupported(None, 'extent', f'{b} is sized by {extents[b]} and {shape} in different kernels')
        shapes = [r['name'] for r in resources.values() if r['kind'] == 'shape']
        if not shapes and all(s['grid'] is None for s in stages):
            # Groups kernels take no shape; a shape resource still sizes the buffers.
            name = next(n for n in ('size', 'grid_size', 'qpel_shape') if n not in resources)
            resources[name] = {'name': name, 'kind': 'shape', 'type': 'shape3'}
            shapes = [name]
            self.adaptations.append(f'no launch plan: buffers are sized by a shape {name}, and Groups kernels '
                                    'run one thread per element of it in groups of ' + str(GROUPS_BLOCK))
        for s in stages:
            s['grid'] = s['grid'] or (shapes[0] if len(shapes) == 1 else None)
        grids = {s['grid'] for s in stages}
        if len(grids) != 1 or None in grids:
            raise Unsupported(None, 'extent', 'kernels use different shape parameters; pass a launch plan')
        for r in resources.values():
            r['extent'] = None if r['kind'] in ('shape', 'value') else extents.get(r['name'], next(iter(grids)))
            scales = {self.views.get(k, {}).get(r['name']) for k in kernels} - {None}
            if len(scales) > 1:
                raise Unsupported(None, 'extent', f'{r["name"]} is read as vectors of different lengths in different kernels')
            if scales:
                r['scale'] = scales.pop()
        return {'resources': list(resources.values()), 'stages': stages}


def strip_parens(node):
    while node['kind'] == 'ParenExpr':
        node = node['inner'][0]
    return node


def cast_operand(cast):
    """The operand of an explicit integer cast, before clang's implicit conversion."""
    inner = cast['inner'][0]
    while inner['kind'] == 'ImplicitCastExpr' and inner.get('castKind') == 'IntegralCast':
        inner = inner['inner'][0]
    return inner


def signed_operand(node, unsigned='u32'):
    """x when node is an explicit (unsigned) cast of x, an integer expression whose value the
    cast keeps modulo 2^width: a signed integer at most as wide, or a narrower unsigned one."""
    n = strip_parens(node)
    if n['kind'] in EXPLICIT_CASTS and scalar_of(n) == unsigned:
        inner = scalar_of(cast_operand(n))
        width = 32 if unsigned == 'u32' else 64
        if inner in INTEGER_TERMS and (is_signed(inner) and type_bits(inner) <= width
                                       or not is_signed(inner) and type_bits(inner) < width) and \
                (unsigned == 'u64' or inner in ('i32', 'i16', 'i8')):
            return cast_operand(n)
    return None


def records(unit):
    """{name: clang record} for every struct the unit defines, under its own or its typedef name."""
    by_id, named = {}, {}

    def visit(node):
        kind = node.get('kind')
        if kind in ('CXXRecordDecl', 'RecordDecl') and node.get('completeDefinition'):
            by_id[node['id']] = node
            if node.get('name'):
                named[node['name']] = node
        if kind in ('NamespaceDecl', 'LinkageSpecDecl'):
            for child in node.get('inner', ()):
                visit(child)
    for node in unit.get('inner', ()):
        visit(node)

    def typedefs(node):
        if node.get('kind') == 'TypedefDecl':
            for t in walk(node):
                decl = t.get('decl', {})
                if t.get('kind') == 'RecordType' and decl.get('id') in by_id:
                    named.setdefault(node['name'], by_id[decl['id']])
        if node.get('kind') in ('NamespaceDecl', 'LinkageSpecDecl'):
            for child in node.get('inner', ()):
                typedefs(child)
    for node in unit.get('inner', ()):
        typedefs(node)
    return named


def instance_name(base, arguments):
    """An identifier for one instantiation: kernel<op_abs, __half> -> kernel_op_abs_half."""
    parts = []
    for a in arguments:
        a = a.replace('__attribute__((ext_vector_type(', 'x').replace(')))', '')
        a = SCALARS.get(' '.join(words(a)), a)
        parts.append(re.sub(r'[^A-Za-z0-9]+', '_', a).strip('_'))
    return re.sub(r'_+', '_', base + '_' + '_'.join(parts)).strip('_')


def header_host_bodies(unit, main_ids, dialect):
    """{header path: [[start, end], ...]}: bodies of CPU functions in the library's own headers.

    A simulator runs GPU code as C++; host code in headers may use <<<>>> launches, so it
    blanks these bodies (keeping the declarations, which other host code may call)."""
    out = {}

    def visit(node, template=False):
        kind = node.get('kind')
        if kind in ('NamespaceDecl', 'LinkageSpecDecl'):
            for child in node.get('inner', ()):
                visit(child)
        elif kind == 'FunctionTemplateDecl':
            pattern = next((d for d in node.get('inner', ()) if d.get('kind') == 'FunctionDecl'), None)
            if pattern is not None:
                visit(pattern, True)
        elif kind == 'FunctionDecl' and dialect.is_host(node):
            body = body_of(node)
            if body is not None and body.get('range'):
                begin = body['range']['begin']
                file = begin.get('expansionLoc', begin).get('_file')
                if file:
                    out.setdefault(file, []).append(clang_ast.span(body))
    for node in unit.get('inner', ()):
        if id(node) not in main_ids and not node.get('isImplicit'):
            visit(node)
    return out


_REACHABLE = {}  # (id(unit), path, dialect) -> (unit, ids); clang_ast caches units, which never change


def cached_reachable(unit, path, main, dialect):
    """reachable_functions, once per parsed unit: importing several kernels of one large
    library file (llama.cpp's ggml-metal.metal) would otherwise walk all of it every time."""
    key = (id(unit), str(path), dialect)
    hit = _REACHABLE.get(key)
    if hit and hit[0] is unit:
        return hit[1]
    if len(_REACHABLE) >= 8:
        _REACHABLE.clear()
    ids = frozenset(reachable_functions(unit, main, dialect))
    _REACHABLE[key] = (unit, ids)
    return ids


def reachable_functions(unit, main, dialect):
    """Clang ids of the functions GPU code in the main file calls, directly or not."""
    functions = {}

    def collect(node):
        if node.get('kind') == 'FunctionDecl':
            functions[node['id']] = node
        for child in node.get('inner', ()):
            if child.get('kind') in ('FunctionDecl', 'FunctionTemplateDecl', 'NamespaceDecl', 'LinkageSpecDecl'):
                collect(child)
    for node in unit.get('inner', ()):
        collect(node)
    start = []
    for node in main:
        collected = {}
        def mine(n):
            if n.get('kind') == 'FunctionDecl' and not dialect.is_host(n):
                start.append(n['id'])
            elif n.get('kind') == 'FunctionDecl' and body_of(n) is not None:
                # host code of this file: the kernels it launches (`k<<<g, b>>>(...)`), wherever they are
                # defined (PyTorch extensions keep kernels in .cuh headers and launch them from the .cu)
                for call in walk(body_of(n)):
                    if call.get('kind') == 'CUDAKernelCallExpr' and call.get('inner'):
                        callee = call['inner'][0]
                        while callee.get('kind') in ('ImplicitCastExpr', 'ParenExpr', 'CStyleCastExpr') and callee.get('inner'):
                            callee = callee['inner'][0]
                        if callee.get('kind') == 'DeclRefExpr' and callee['referencedDecl'].get('kind') == 'FunctionDecl':
                            start.append(callee['referencedDecl']['id'])
            for c in n.get('inner', ()):
                if c.get('kind') in ('FunctionDecl', 'FunctionTemplateDecl', 'NamespaceDecl', 'LinkageSpecDecl'):
                    mine(c)
        mine(node)
    if any(not skipped['system'] for skipped in unit.get('_skipped', ())):
        # a declaration of this source was too large to read (clang_ast.DECLARATION_LIMIT), such as mamba's
        # host launcher: the kernels instantiated in the library's own headers are the ones it launched
        # (system headers never reach the unit: clang_ast.read_unit drops them)
        start += [i for i, f in functions.items() if dialect.is_kernel(f)
                  and any(c.get('kind') == 'TemplateArgument' for c in f.get('inner', ()))]
    seen, stack = set(), start
    while stack:
        ident = stack.pop()
        if ident in seen or ident not in functions:
            continue
        seen.add(ident)
        for n in walk(body_of(functions[ident])):
            if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('kind') == 'FunctionDecl':
                stack.append(n['referencedDecl']['id'])
    return seen


def contains_any(node, ids):
    """True when node is, or contains, a function declaration with one of these ids."""
    if node.get('id') in ids:
        return True
    return any(contains_any(c, ids) for c in node.get('inner', ())
               if c.get('kind') in ('FunctionDecl', 'FunctionTemplateDecl', 'NamespaceDecl', 'LinkageSpecDecl'))


def is_null_pointer(node):
    """True for a null pointer constant: nullptr, NULL (__null) or 0 converted to a pointer."""
    converted = False
    while node.get('kind') in ('ImplicitCastExpr', 'CStyleCastExpr', 'ParenExpr', 'CXXStaticCastExpr') and node.get('inner'):
        converted |= node.get('castKind') == 'NullToPointer'
        node = node['inner'][0]
    return node.get('kind') in ('CXXNullPtrLiteralExpr', 'GNUNullExpr') or \
        converted and node.get('kind') == 'IntegerLiteral' and int(node.get('value', '1')) == 0


def null_tested(node):
    """(clang id of a parameter tested against null, True when the test is p == null) for a test
    `p != nullptr`, `p == NULL`, `nullptr != p` or `if (p)`, else None."""
    def parameter(x):
        while x.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and x.get('inner') and x.get('castKind') != 'PointerToBoolean':
            x = x['inner'][0]
        ref = x.get('referencedDecl', {}) if x.get('kind') == 'DeclRefExpr' else {}
        return ref.get('id') if ref.get('kind') == 'ParmVarDecl' else None
    if node.get('kind') == 'BinaryOperator' and node.get('opcode') in ('==', '!=') and len(node.get('inner', ())) == 2:
        left, right = node['inner']
        for a, b in ((left, right), (right, left)):
            if is_null_pointer(b) and parameter(a):
                return parameter(a), node['opcode'] == '=='
    if node.get('kind') == 'ImplicitCastExpr' and node.get('castKind') == 'PointerToBoolean' and node.get('inner'):
        found = parameter(node['inner'][0])
        if found:
            return found, False
    return None


def peel_void(node):
    """The operand of (void)expr, without parentheses or implicit conversions."""
    node = node['inner'][0]
    while node['kind'] in ('ParenExpr', 'ImplicitCastExpr') and node.get('inner'):
        node = node['inner'][0]
    return node


def has_explicit_cast(node):
    """True when the chain of conversions above node's core includes a cast written in the source."""
    while node['kind'] in PASSTHROUGH and node.get('inner'):
        if node['kind'] in EXPLICIT_CASTS:
            return True
        node = node['inner'][0]
    return False


def same_expression(a, b):
    """True when two clang expressions are the same computation (kinds, operators, names, values)."""
    a, b = peel(a), peel(b)
    if a['kind'] != b['kind'] or a.get('opcode') != b.get('opcode') or a.get('value') != b.get('value') \
            or a.get('name') != b.get('name') \
            or a.get('referencedDecl', {}).get('id') != b.get('referencedDecl', {}).get('id'):
        return False
    ia, ib = a.get('inner', ()), b.get('inner', ())
    return len(ia) == len(ib) and all(same_expression(x, y) for x, y in zip(ia, ib))


def conversion_step(node):
    """The node just below the first type change, when a chain of casts changes type
    more than once (`(long long)(unsigned char)x`); converting through every type keeps
    each truncation. None for zero or one change."""
    chain = [node]
    while chain[-1]['kind'] in PASSTHROUGH and chain[-1].get('inner'):
        n = chain[-1]
        if n['kind'] in EXPLICIT_CASTS and scalar_of(n) in ('f32', 'f16') and n is not node:
            break  # peel() stops at explicit float casts too
        if n['kind'] == 'ImplicitCastExpr' and n.get('castKind') not in VALUE_CASTS:
            break
        chain.append(n['inner'][0])
    types = [scalar_of(n) for n in chain]
    numeric = INTEGER_TERMS | {'f32', 'bf16'}
    changes = [i for i in range(1, len(types)) if types[i] != types[i - 1]]
    if len(changes) < 2 or not all(types[i] in numeric for i in [0] + changes + [c - 1 for c in changes]):
        return None
    return chain[changes[0]]


def constexpr_call(node, functions):
    """The value of a call, without arguments, to a constexpr function whose body is one
    `return <integer constant>;` (ggml's `ggml_cuda_get_physical_warp_size()` is 32), or None.
    `functions` maps clang ids to the translation unit's function declarations."""
    if node.get('kind') != 'CallExpr' or len(node.get('inner', ())) != 1:
        return None
    target = peel_void(node['inner'][0]) if node.get('inner') else {}
    while target.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and target.get('inner'):
        target = target['inner'][0]
    decl = target.get('referencedDecl', {}) if target.get('kind') == 'DeclRefExpr' else {}
    function = functions.get(decl.get('id'))
    if not function or not function.get('constexpr'):
        return None
    body = body_of(function)
    statements = (body or {}).get('inner', ())
    if len(statements) != 1 or statements[0].get('kind') != 'ReturnStmt' or not statements[0].get('inner'):
        return None
    return integer_constant(statements[0]['inner'][0])


SIZEOF = {'char': 1, 'signed char': 1, 'unsigned char': 1, 'bool': 1, 'short': 2, 'unsigned short': 2,
          'int': 4, 'unsigned int': 4, 'long': 8, 'unsigned long': 8, 'long long': 8, 'long long int': 8,
          'unsigned long long': 8, 'unsigned long long int': 8, 'float': 4, 'double': 8, '__half': 2,
          'half': 2}  # sizeof on both platforms (LP64)


def integer_constant(node):
    """The value of an integer expression made only of literals, + - * and casts, with C's
    conversions applied (wrapping), or None."""
    n = strip_parens(node)
    kind = n['kind']
    t = scalar_of(n)
    def fit(v):
        if t not in INTEGER_TERMS:
            return None if t != 'bool' else v
        size, signed = type_bits(t), is_signed(t)
        v %= 2**size
        return v - 2**size if signed and v >= 2**(size - 1) else v
    if kind == 'IntegerLiteral':
        return fit(int(n['value']))
    if kind == 'UnaryExprOrTypeTraitExpr' and n.get('name') == 'sizeof' and 'argType' in n:
        size = SIZEOF.get(' '.join(words(n['argType'].get('desugaredQualType', n['argType']['qualType']))))
        return None if size is None else fit(size)
    if kind == 'SubstNonTypeTemplateParmExpr' and n.get('inner'):  # a template's int argument (width = 32)
        inner = integer_constant(n['inner'][-1])
        return None if inner is None else fit(inner)
    if kind in PASSTHROUGH and n.get('inner'):
        inner = integer_constant(n['inner'][0])
        return None if inner is None else fit(inner)
    if kind == 'UnaryOperator' and n['opcode'] in ('-', '+'):
        inner = integer_constant(n['inner'][0])
        return None if inner is None else fit(-inner if n['opcode'] == '-' else inner)
    if kind == 'BinaryOperator' and n['opcode'] in ('+', '-', '*'):
        a, b = (integer_constant(x) for x in n['inner'])
        if a is None or b is None:
            return None
        return fit(a + b if n['opcode'] == '+' else a - b if n['opcode'] == '-' else a * b)
    return None


def is_zero(node):
    n = peel(node)
    return n['kind'] == 'IntegerLiteral' and int(n['value']) == 0


def nonnegative_int(node):
    """An int-typed expression that provably equals its unsigned reading.

    True for non-negative literals, promoted unsigned chars, and &, |, ^ of those."""
    node = peel(node)
    if node['kind'] == 'IntegerLiteral':
        return int(node['value']) < 2**31
    if node['kind'] == 'BinaryOperator' and node['opcode'] in ('&', '|', '^'):
        return all(nonnegative_int(x) for x in node['inner'])
    return scalar_of(node) == 'u8'


def written_buffers(body):
    """Names of pointers whose elements are written in the body (or handed to another function)."""
    out = set()
    for node in walk(body):
        target = None
        if node['kind'] in ('BinaryOperator', 'CompoundAssignOperator') and node.get('opcode', '').endswith('=') \
                and node['opcode'] not in ('==', '!=', '<=', '>='):
            target = node['inner'][0]
        elif node['kind'] == 'UnaryOperator' and node['opcode'] in ('++', '--'):
            target = node['inner'][0]
        elif node['kind'] == 'CallExpr':
            out.update(decl_name(a) for a in node['inner'][1:] if scalar_of(peel(a)) == 'pointer')
            out.update(atomic_address_base(a) for a in node['inner'][1:2])
        if target is not None and peel(target)['kind'] == 'ArraySubscriptExpr':
            out.add(decl_name(peel(target)['inner'][0]))
    return out


def assigned_decls(body):
    """Declaration ids that are written somewhere in the body."""
    out = set()
    for node in walk(body):
        target = None
        if node['kind'] in ('BinaryOperator', 'CompoundAssignOperator') and node.get('opcode', '').endswith('=') \
                and node['opcode'] not in ('==', '!=', '<=', '>='):
            target = node['inner'][0]
        elif node['kind'] == 'UnaryOperator' and node['opcode'] in ('++', '--'):
            target = node['inner'][0]
        if target is not None:
            core = peel(target)
            if core['kind'] == 'DeclRefExpr':
                out.add(core['referencedDecl']['id'])
    return out


def import_source(path, dialect=None, module=None, plan=None, block_size=64, include=(), kernels=None,
                  constants=None, geometry=None, resources=None, shared=None, defines=()):
    """Import every kernel and device function in `path`. Returns (program, report).

    `plan` is an optional launch plan (the pipeline.json qpel writes); without one,
    each kernel becomes one dispatch over the single shape parameter. `kernels`
    imports only those kernels and the functions they call; `constants` gives the
    values of Metal function constants, specialising the kernels to them. `geometry`
    ({'groups': [x, y, z], 'block': [x, y, z]}) launches Groups kernels without a plan
    the way the original's host does. `resources` ({name: kind}) overrides the kind of
    resources the plan made without one gets, such as 'inout' for a buffer the caller fills
    and the kernels update in place. `shared` ({parameter: elements}) gives the length of
    Metal threadgroup parameters (`threadgroup float* s [[threadgroup(0)]]`), which the
    original's host sets at the launch."""
    path = Path(path)
    dialect = dialect or clang_ast.dialect_of(path)
    unit, source = clang_ast.parse(path, dialect, include, defines)  # defines: macros the original's build sets
    module = module or (plan or {}).get('module') or path.stem
    if not is_identifier(module):
        raise ValueError(f'{module!r} cannot be a qpel module name; pass one explicitly')
    plan = normalize_plan(plan) if plan else plan
    importer = Importer(unit, source, path, dialect, module)
    importer.constants = dict(constants or {})
    importer.shared_lengths = dict(shared or {})
    importer.geometry = geometry
    importer.resource_kinds = dict(resources or {})
    program = importer.run(plan, block_size, kernels)
    report = {'source': str(path), 'dialect': dialect, 'module': module,
              'kernels': [d['name'] for d in importer.decls if importer.functions[d['name']][2]],
              'helpers': [d['name'] for d in importer.decls if not importer.functions[d['name']][2]],
              'adaptations': importer.adaptations, 'warnings': unguarded_kernels(program),
              'qualified': {k: importer.qualified[k] for k in importer.arguments}, 'arguments': importer.arguments,
              'host_spans': importer.host_spans, 'header_host_bodies': importer.header_host_bodies,
              'shared': dict(importer.shared_lengths), 'defines': list(defines)}
    return program, report


def unguarded_kernels(program):
    """Kernels with a thread-index axis that is never compared. CUDA rounds the grid up
    to whole blocks, so such a kernel also runs for indices past the shape."""
    nodes = {n['id']: n for n in program['nodes']}

    derived = {}  # Variable id -> axis, for locals computed from one thread-index axis

    def axes(node):
        found = {direct(node)} - {None}
        for values in node['children'].values():
            for c in values:
                found |= axes(nodes[c])
        return found

    def direct(node):
        if node['kind'] == 'GridIndex':
            return 'x'
        if node['kind'] == 'ThreadIndex':
            return node['attributes']['axis']
        if node['kind'] == 'Name':
            return derived.get(node['links']['declaration'])
        return None

    axis = direct
    for n in program['nodes']:  # in document order, so a local is seen before its uses
        if n['kind'] == 'Variable' and n['children'].get('value'):  # buffer locals may start without one
            found = axes(nodes[n['children']['value'][0]])
            if len(found) == 1:
                derived[n['id']] = found.pop()
    warnings = []
    for n in program['nodes']:
        if n['kind'] != 'Function' or not n['attributes'].get('isKernel'):
            continue
        used, compared, stack = set(), set(), list(n['children']['body'])
        while stack:
            node = nodes[stack.pop()]
            if axis(node):
                used.add(axis(node))
            if node['kind'] == 'Binary' and node['attributes']['operator'] in ('<', '<=', '>', '>='):
                compared.update(axis(nodes[c]) for r in ('left', 'right') for c in node['children'][r] if axis(nodes[c]))
            stack.extend(c for cs in node['children'].values() for c in cs)
        if used - compared:
            warnings.append(f'{n["attributes"]["name"]}: no bounds guard on the thread index'
                            + (f' ({", ".join(sorted(used - compared))})' if len(used) > 1 else ''))
    return warnings
