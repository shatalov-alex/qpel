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

from qpel_schema import is_identifier
from qpel_types import (SCALAR_TERMS, VALUE_TERMS, access, buffer as type_buffer, fits, is_buffer as type_is_buffer,
                        is_signed, node_spec, normalize_plan, term as type_term)
from qpel_types import access as type_access, bits as type_bits, INTEGER_TERMS, MATH, is_struct
from qpel_types import arithmetic as type_arithmetic
from . import clang_ast
from .clang_ast import position

IR_HEADER = {'format': 'qpel.ir', 'version': 1, 'semantics': 'qpel-portable-core-v1'}
U32_MAX = 2**32 - 1


class Unsupported(ValueError):
    """A construct the current qpel IR cannot represent."""

    def __init__(self, node, code, message):
        file, line, col = position(node) if node else (None, None, None)
        self.code = code
        self.location = f'{file}:{line}:{col}'
        super().__init__(f'{self.location}: [{code}] {message}')


# --- types -----------------------------------------------------------------

QUALIFIERS = {'const', 'volatile', 'device', 'constant', 'threadgroup', 'thread', 'struct',
              '__restrict', '__restrict__', 'restrict'}
SCALARS = {'unsigned int': 'u32', 'unsigned': 'u32', 'uint': 'u32', 'uint32_t': 'u32',
           'unsigned char': 'u8', 'uchar': 'u8', 'uint8_t': 'u8', 'bool': 'bool', 'float': 'f32',
           '__half': 'f16', 'half': 'f16', 'int': 'i32', 'signed int': 'i32', 'signed': 'i32', 'int32_t': 'i32',
           'void': 'void', 'double': 'f64',
           'atomic_uint': 'atomic', 'atomic_int': 'atomic_i32',
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
ATOMIC_SCALARS = {'atomic': 'u32', 'atomic_i32': 'i32'}  # Metal atomic element -> qpel element
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
    t = describe(node.get('type', {}).get('qualType', ''))
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


def grid_axis(node):
    """'x', 'y' or 'z' for blockIdx.a * blockDim.a + threadIdx.a (operands in any order), else None."""
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


def params_of(decl):
    return [c for c in decl.get('inner', ()) if c['kind'] == 'ParmVarDecl']


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
               'atomicMin': ('AtomicMin', True, False)}

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
               'atomic_fetch_min_explicit': ('AtomicMin', True, True)}

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
GROUP_NODES = {'group': 'GroupIndex', 'local': 'LocalIndex', 'size': 'GroupSize', 'count': 'GroupCount'}
GROUPS_BLOCK = 64  # group size of a Groups kernel without a launch plan
QPEL_GROUP_SUM_PARAMS = {'qpel_lane', 'qpel_group_size'}  # builtins backends/metal.h adds for a workgroup sum
ATOMIC_ELEMENTS = {'u32': 'atomic', 'i32': 'atomic_i32'}  # element -> scalar of its atomic buffer
ZERO = {'bool': ('Literal', 'false'), 'u32': ('Literal', '0u'), 'i32': ('I32Literal', '0'), 'f32': ('F32Literal', '0.0')}
GRID3D_BLOCK = (16, 16, 1)  # default block for 2D/3D kernels without a launch plan
MINIMUM = {'min', 'umin'}
MAXIMUM = {'max', 'umax'}
FMA = {'__fmaf_rn', 'fmaf', 'fma'}
TO_F16 = {'__float2half_rn', '__float2half'}
TO_BF16 = {'__float2bfloat16_rn', '__float2bfloat16'}
# C, CUDA and Metal spellings of qpel's math functions (on float arguments).
MATH_CALLS = {name: f for f in ('fabs', 'fmin', 'fmax', 'floor', 'ceil', 'trunc', 'round', 'copysign', 'sqrt',
                                'exp', 'exp2', 'log', 'log2', 'sin', 'cos', 'tanh')
              for name in (f, f + 'f')}
MATH_CALLS.update({'abs': 'fabs', '__fsqrt_rn': 'sqrt'})
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
        self.records = records(unit)  # struct name -> its clang record, for argument structs
        self.structs = {}  # struct name -> [(field, type term)] used by imported functions
        self.function_constants = {}  # clang id -> name of a Metal [[function_constant]] variable
        self.globals = {}  # clang id -> name of another program-scope variable
        self.global_values = {}  # clang id -> (name, initializer) of a program-scope scalar constant
        self.taken = {}  # function name -> clang id of the declaration that has it
        self.constants = {}  # name -> value, set by import_source(constants=...)
        self.files = {}  # path -> text, for reading attributes clang's JSON leaves out
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
            elif not node.get('isImplicit') and contains_any(node, self.reachable):
                self.header = True
                decls.extend(self.attempt(lambda: self.top_level(node), []))
        self.header = False
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

    def host_name(self, decl):
        """The name a Metal explicit instantiation gives a kernel: [[host_name("...")]]."""
        for c in decl.get('inner', ()):
            if c['kind'] == 'MetalHostNameAttr':
                begin = c['range']['begin']
                file = begin.get('_file')
                if file not in self.files:
                    self.files[file] = Path(file).read_text()
                found = re.match(r'host_name\s*\(\s*"([^"]+)"', self.files[file][begin['offset']:begin['offset'] + 300])
                if found:
                    return found.group(1)
        return None

    def top_level(self, node, qualified=True):
        kind = node['kind']
        if kind == 'LinkageSpecDecl':
            return [d for child in node.get('inner', ()) for d in self.top_level(child)]
        if kind == 'NamespaceDecl':
            # qpel has one flat module; function names must still be unique, which run() checks.
            self.adaptations.append(f'namespace {node.get("name", "(anonymous)")} was flattened')
            outer = self.namespace
            self.namespace = outer + node.get('name', '') + '::'
            try:
                return [d for child in node.get('inner', ()) for d in self.attempt(lambda: self.top_level(child), [])]
            finally:
                self.namespace = outer
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
                self.qualified[node['name']] = f'{self.namespace}{node["name"]}<{arguments}>'
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
                    self.qualified[name] = self.host_name(use) or f'{self.namespace}{node["name"]}<{", ".join(arguments)}>'
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
                self.qualified[node['name']] = self.namespace + node['name']
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
        if kind in ('CXXRecordDecl', 'RecordDecl', 'TypedefDecl', 'EnumDecl', 'TypeAliasDecl'):
            return []  # types are read where parameters use them
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
        types = {}
        for decl in self.decls:
            for i, p in enumerate(params_of(decl)):
                if self.dialect.is_kernel(decl) and self.dialect.builtin(p):
                    continue  # thread builtins are not kernel arguments
                t = describe(p['type']['qualType'])
                self.current = decl['name']
                types[(decl['name'], i)] = self.attempt(lambda: self.parameter_type(decl, i, p, find, atomic_groups))
        return types

    def parameter_type(self, decl, i, p, find, atomic_groups):
        t = describe(p['type']['qualType'])
        if t[0] == 'pointer':
            if find((decl['name'], i)) in atomic_groups or t[2] in ATOMIC_ELEMENTS.values():
                scalar = t[2] if t[2] in ATOMIC_ELEMENTS else ATOMIC_SCALARS.get(t[2])
                if scalar is None:
                    raise Unsupported(p, 'atomic-type', f'atomics on {p["type"]["qualType"]} are not in qpel; only on unsigned int and int')
                return type_buffer(scalar, 'atomic')
            if t[2] not in SCALAR_TERMS or t[2] == 'bool':
                raise Unsupported(p, 'buffer-type', f'{p["type"]["qualType"]} buffers are not in qpel yet')
            if t[2] == 'u8' and not t[1] and p['name'] not in written_buffers(body_of(decl)):
                self.adaptations.append(f'{decl["name"]}: {p["name"]} is never written, so it became read-only')
                return 'buffer<u8, read>'
            return type_buffer(t[2], 'read' if t[1] else 'write')
        if t[0] == 'shape':
            return 'shape3'
        if t[0] == 'scalar' and t[1] in SCALAR_TERMS:
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
        params = []
        position = 0
        shape_param = None
        self.groups = kernel and self.uses_groups(decl)
        self.grid3d = kernel and not self.groups and self.uses_grid3d(decl)
        count = self.attempt(lambda: self.element_count(decl, types)) if kernel and not self.grid3d and not self.groups else None
        for i, p in enumerate(params_of(decl)):
            builtin = self.dialect.builtin(p) if kernel else None
            if builtin in GROUP_NODES and self.groups:
                vector = ' '.join(words(p['type']['qualType'])) not in ('uint', 'ushort', 'unsigned int', 'unsigned short')
                self.scope[p['id']] = ('group-builtin', builtin, vector)
                continue
            if builtin == 'grid' and self.groups:
                self.scope[p['id']] = ('position',) if is_position_vector(p) else ('grid', 'x')
                continue
            if builtin == 'grid':
                vector = is_position_vector(p)
                self.scope[p['id']] = ('position',) if vector else ('grid', 'x')
                continue
            if builtin:
                self.scope[p['id']] = ('builtin', p['name'])
                continue
            kind = types[(name, i)]
            if kind is None:  # a survey already recorded why this parameter has no qpel type
                self.scope[p['id']] = ('untyped', p['name'])
                continue
            attrs = {'name': self.safe_name(p['name'])}
            if kernel:
                attrs.update(binding=self.dialect.binding(p, position), isBuffer=True)
                if kind == 'shape3':
                    shape_param = p
                elif count is not None and p['id'] == count['id']:
                    kind = 'shape3'
                elif not type_is_buffer(kind) and kind not in VALUE_TERMS and not is_struct(kind):
                    self.attempt(lambda: self.reject(Unsupported(
                        p, 'scalar-kernel-argument', 'kernel arguments can be integers or float')))
            position += 1
            ir = self.graph.add('Parameter', attrs, {'type': self.type_node(kind)})
            self.scope[p['id']] = ('param', ir, kind)
            params.append(ir)
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
            elif shape_param is None:
                self.attempt(lambda: self.reject(Unsupported(
                    decl, 'no-extent', 'a 2D/3D kernel needs a uint3 shape argument' if self.grid3d else
                    'a kernel needs a uint3 shape, or an unsigned element count that the thread index is compared with')))
            self.kernel_params[name] = {self.graph.nodes[p]['attributes']['name']: p for p in params}
            self.arguments[name] = self.argument_map(decl)
        result = describe(decl['type']['qualType'].split('(')[0])
        if result[0] != 'scalar' or result[1] not in SCALAR_TERMS | {'void'}:
            self.attempt(lambda: self.reject(Unsupported(decl, 'result-type', 'unsupported result type ' + decl['type']['qualType'])))
            result = ('scalar', 'void')
        body = self.block(body_of(decl))
        statements = self.graph.nodes[body]['children'].get('statements', [])
        if len(statements) == 1 and self.graph.nodes[statements[0]]['kind'] == 'Block':
            body = statements[0]  # `{ { ... } }`, the shape qpel's own output has
        attrs = {'name': name, 'isKernel': True} if kernel else {'name': name}
        children = {'parameters': params, 'resultType': self.type_node(result[1]), 'body': body}
        if kernel:
            children['domain'] = self.graph.add('Groups' if self.groups else 'Grid3D' if self.grid3d else 'Grid1D')
            self.domains[name] = 'groups' if self.groups else 'grid3d' if self.grid3d else 'grid1d'
        return self.graph.put(ident, 'Function', attrs, children)

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
            else:
                item.update(source='parameter', parameter=self.safe_name(p['name']))
            out.append(item)
        return out

    def axis_of(self, node):
        """'x', 'y' or 'z' when node is a thread-index axis (CUDA expression or Metal position member)."""
        node = peel(node)
        axis = grid_axis(node)
        if axis:
            return axis
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

    def uses_groups(self, decl):
        """A kernel is a Groups kernel when it reads a thread builtin other than the global
        index: CUDA's threadIdx, blockIdx, blockDim or gridDim outside blockIdx * blockDim +
        threadIdx, or a Metal threadgroup position, thread position in the group, group size
        or group count."""
        names = {p['name'] for p in params_of(decl)}
        if any(self.dialect.builtin(p) in GROUP_NODES and p['name'] not in QPEL_GROUP_SUM_PARAMS for p in params_of(decl)) \
                or 'qpel_group' in names:
            return True  # (qpel_lane and qpel_group_size alone belong to qpel's own workgroup sum)
        body = body_of(decl)
        idiom = set()
        for node in walk(body):
            if node.get('kind') == 'BinaryOperator' and grid_axis(node) or \
                    node.get('kind') == 'CallExpr' and (decl_name(node['inner'][0]) or '').startswith('qpel_'):
                idiom.update(id(n) for n in walk(node))  # the global index, or qpel's own backend helpers
        return any(n.get('kind') == 'MemberExpr' and id(n) not in idiom and decl_name(n['inner'][0]) in CUDA_BUILTINS
                   for n in walk(body))

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
        if any(types.get((decl['name'], i)) == 'shape3' for i in range(len(params))):
            return None
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
        if len(compared) > 1:
            raise Unsupported(decl, 'no-extent', 'the thread index is compared with several counts; pass a uint3 shape')
        if compared:
            return unsigned[next(iter(compared))]
        return None

    # -- statements --

    def block(self, node):
        statements = []
        for child in node.get('inner', ()):
            statements.extend(self.statement(child))
        return self.graph.add('Block', children={'statements': statements})

    def as_block(self, node):
        if node['kind'] == 'CompoundStmt':
            return self.block(node)
        return self.graph.add('Block', children={'statements': self.statement(node)})

    def statement(self, node):
        return self.attempt(lambda: self.read_statement(node), [])

    def read_statement(self, node):
        kind = node['kind']
        add = self.graph.add
        if kind == 'CompoundStmt':
            return [self.block(node)]
        if kind == 'NullStmt':
            return []
        if kind == 'CStyleCastExpr' and node.get('castKind') == 'ToVoid' and peel_void(node)['kind'] == 'DeclRefExpr':
            return []  # (void)x; marks a local as unused and does nothing
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
                children['condition'] = self.expr(cond)
            if step:
                children['step'] = self.expression_statement(step)
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
            return [add('While', children={'condition': self.expr(cond), 'body': self.as_block(body)})]
        if kind == 'DoStmt':
            body, cond = node['inner']
            return [add('DoWhile', children={'body': self.as_block(body), 'condition': self.expr(cond)})]
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
        if decl['kind'] != 'VarDecl':
            raise Unsupported(decl, 'declaration', decl['kind'] + ' inside a function is not supported')
        name = decl['name']
        if name.startswith('qpel_') and decl.get('storageClass') != 'auto' and 'init' not in decl:
            self.scope[decl['id']] = ('builtin', name)  # backend scratch for workgroup collectives
            return []
        self.scope[decl['id']] = ('untyped', name)  # until it is read, so later uses are not reported again
        t = describe(decl['type']['qualType'])
        if t[0] == 'pointer' and t[2] in SCALAR_TERMS - {'bool'} and \
                not {'threadgroup', 'constant', 'thread'} & set(decl['type']['qualType'].replace('*', ' ').split()):
            return self.buffer_local(decl, name, type_buffer(t[2], 'read' if t[1] else 'write'))
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
        axis = grid_axis(init) or (self.axis_of(init) if peel(init)['kind'] in ('MemberExpr', 'ExtVectorElementExpr') else None)
        if self.kernel and axis and decl['id'] not in self.assigned and not self.groups:
            self.scope[decl['id']] = ('grid', axis)  # a name for the thread index (CUDA expression or Metal position.y)
            return []
        value = self.expr(init)
        name = self.unique_name(name)
        ir = self.graph.add('Variable', {'name': name},
                            {'type': self.type_node(t[1]), 'value': value})
        self.scope[decl['id']] = ('local', ir, t[1])
        return [ir]

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
        if kind in ('BinaryOperator', 'CompoundAssignOperator', 'UnaryOperator') and node.get('opcode') in ('=', '++', '--') \
                or kind == 'CompoundAssignOperator':
            if self.is_atomic_element(node['inner'][0]):
                if node['opcode'] != '=':
                    raise Unsupported(node, 'atomic-update', f'{node["opcode"]} on an atomic buffer element is a race; use an atomic call')
                target, value = node['inner']
                self.note_atomic_access(peel(target)['inner'][0])
                store = add('AtomicStore', children={'address': self.element_address(peel(target)), 'value': self.expr(value)})
                return add('Evaluate', children={'value': store})
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
            raise Unsupported(node, 'assign-target', 'cannot assign to ' + core['referencedDecl'].get('name', '?'))
        if core['kind'] == 'ArraySubscriptExpr':
            return self.index(core)
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
            if entry is None:
                if ref.get('name') in ('threadIdx', 'blockIdx', 'blockDim', 'gridDim'):
                    raise Unsupported(n, 'thread-builtin', 'only blockIdx.x * blockDim.x + threadIdx.x is supported')
                raise Unsupported(n, 'unknown-name', f'{ref.get("name")} is not a parameter or local')
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
        if grid_axis(n) and not self.groups:  # a Groups kernel reads it literally: group * size + local
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
            raise Unsupported(n, 'operator', f'operator {op} is not supported here')
        if n.get('castKind') == 'BitCast':  # (device float*)(src + off): the bytes read as another type
            target = describe(n['type']['qualType'])
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
        if kind == 'ArraySubscriptExpr':
            return self.index(n)
        if kind in ('MemberExpr', 'ExtVectorElementExpr'):
            field = member_name(n, self.source)
            base = peel(n['inner'][0])
            entry = self.scope.get(base.get('referencedDecl', {}).get('id')) if base['kind'] == 'DeclRefExpr' else None
            if entry and entry[0] == 'position' and field in ('x', 'y', 'z'):
                return self.thread_index(field, n)
            if entry and entry[0] == 'group-builtin' and field in ('x', 'y', 'z'):
                return add(GROUP_NODES[entry[1]], {'axis': field})
            if self.groups and decl_name(base) in CUDA_BUILTINS and field in ('x', 'y', 'z'):
                return add(GROUP_NODES[CUDA_BUILTINS[decl_name(base)]], {'axis': field})
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
        if core['kind'] != 'DeclRefExpr' and scalar_of(core) == 'pointer':
            raise Unsupported(node, 'buffer', 'index a buffer or a buffer local, not a pointer expression')
        if not entry or entry[0] not in ('param', 'local') or not type_is_buffer(entry[2]):
            raise Unsupported(node, 'buffer', 'only buffers and buffer locals can be indexed')
        return entry[2]

    def buffer(self, node):
        self.buffer_kind(node)
        return self.name(self.scope[peel(node)['referencedDecl']['id']][1])

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
        return node['kind'] == 'ArraySubscriptExpr' and type_access(self.buffer_kind(node['inner'][0])) == 'atomic'

    def index(self, node):
        base, index = node['inner']
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

    def call(self, n, statement):
        add = self.graph.add
        callee = self.callee(n)
        args = n['inner'][1:]
        if callee in self.functions:
            decl, ident, kernel = self.functions[callee]
            if kernel:
                raise Unsupported(n, 'call-kernel', 'kernels cannot be called from device code')
            return add('Call', children={'arguments': [self.expr(a, address=True) for a in args]},
                       links={'function': ident})

        def need(count):
            if len(args) != count:
                raise Unsupported(n, 'call', f'{callee} expects {count} arguments')
        if callee in MINIMUM or callee in MAXIMUM:
            need(2)
            return add('Minimum' if callee in MINIMUM else 'Maximum',
                       children={'left': self.expr(args[0]), 'right': self.expr(args[1])})
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
        if callee in self.dialect.atomics:
            kind, has_value, has_order = self.dialect.atomics[callee]
            need(1 + has_value + has_order)
            if has_order and decl_name(args[-1]) != 'memory_order_relaxed':
                raise Unsupported(n, 'memory-order', 'only memory_order_relaxed atomics are in qpel')
            pointee = describe(peel(args[0]).get('type', {}).get('qualType', ''))
            if pointee[0] == 'pointer' and pointee[2] not in ('u32', 'i32', 'atomic', 'atomic_i32'):
                raise Unsupported(n, 'atomic-type', f'{callee} on {pointee[2]} is not in qpel yet; atomics are on unsigned int and int')
            value = self.atomic_value(args[1], pointee) if has_value else None
            if kind == 'AtomicAdd' and peel(args[1])['kind'] == 'IntegerLiteral' and int(peel(args[1])['value']) == 0:
                kind, value = 'AtomicLoad', None  # CUDA's atomic read idiom
            if kind == 'AtomicStore' and self.dialect.name == 'cuda' and not statement:
                raise Unsupported(n, 'atomic-exchange', "atomicExch's previous value has no qpel equivalent")
            children = {'address': self.expr(args[0], address=True)}
            if value is not None:
                children['value'] = value
            return add(kind, children=children)
        if callee in self.rejected:
            raise Unsupported(n, 'call-rejected', f'{callee} could not be imported, see its own blockers')
        raise Unsupported(n, 'call', f'{callee} is not a qpel operation or a function in this file')

    def atomic_value(self, node, pointee):
        """The value of an atomic call in the buffer's element type. Metal's atomic functions
        are templates, so clang leaves e.g. a uint stored into an atomic_int unconverted."""
        element = 'i32' if pointee[0] == 'pointer' and pointee[2] in ('i32', 'atomic_i32') else 'u32'
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
        for r in plan['resources']:
            kind, attributes = node_spec(r['type'])
            ids[r['name']] = add('Resource', {'name': r['name'], 'kind': r['kind']}, {'type': add(kind, attributes)})
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

    def synthesize_plan(self, kernels, block_size):
        """One dispatch per kernel in source order; parameters with one name share a resource."""
        resources = {}
        stages = []
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
                if role == 'shape':
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
        if len(shapes) != 1:
            raise Unsupported(None, 'extent', 'kernels use different shape parameters; pass a launch plan')
        for r in resources.values():
            r['extent'] = None if r['kind'] in ('shape', 'value') else shapes[0]
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
            for c in n.get('inner', ()):
                if c.get('kind') in ('FunctionDecl', 'FunctionTemplateDecl', 'NamespaceDecl', 'LinkageSpecDecl'):
                    mine(c)
        mine(node)
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
                  constants=None, geometry=None):
    """Import every kernel and device function in `path`. Returns (program, report).

    `plan` is an optional launch plan (the pipeline.json qpel writes); without one,
    each kernel becomes one dispatch over the single shape parameter. `kernels`
    imports only those kernels and the functions they call; `constants` gives the
    values of Metal function constants, specialising the kernels to them. `geometry`
    ({'groups': [x, y, z], 'block': [x, y, z]}) launches Groups kernels without a plan
    the way the original's host does."""
    path = Path(path)
    dialect = dialect or clang_ast.dialect_of(path)
    unit, source = clang_ast.parse(path, dialect, include)
    module = module or (plan or {}).get('module') or path.stem
    if not is_identifier(module):
        raise ValueError(f'{module!r} cannot be a qpel module name; pass one explicitly')
    plan = normalize_plan(plan) if plan else plan
    importer = Importer(unit, source, path, dialect, module)
    importer.constants = dict(constants or {})
    importer.geometry = geometry
    program = importer.run(plan, block_size, kernels)
    report = {'source': str(path), 'dialect': dialect, 'module': module,
              'kernels': [d['name'] for d in importer.decls if importer.functions[d['name']][2]],
              'helpers': [d['name'] for d in importer.decls if not importer.functions[d['name']][2]],
              'adaptations': importer.adaptations, 'warnings': unguarded_kernels(program),
              'qualified': {k: importer.qualified[k] for k in importer.arguments}, 'arguments': importer.arguments,
              'host_spans': importer.host_spans, 'header_host_bodies': importer.header_host_bodies}
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
