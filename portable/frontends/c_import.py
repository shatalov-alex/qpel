"""Map a clang AST of CUDA or Metal kernels to qpel IR (qpel-portable-core-v1).

One mapper serves both languages; the differences (how a kernel, a buffer
binding, the thread index and atomics are spelled) live in small dialect
tables. Anything without a qpel equivalent fails with a source location and a
short reason code, never by guessing. The finished program then goes through
the normal qpel checks, which decide types, scopes and pipeline effects.
"""
from pathlib import Path

from qpel_schema import is_identifier
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
           'atomic_uint': 'atomic', 'atomic_int': 'atomic_i32'}
SHAPES = {'uint3', 'QpelShape', 'dim3'}
SCALAR_KIND = {'u32': 'U32Type', 'u8': 'U8Type', 'i32': 'I32Type', 'bool': 'BoolType', 'f32': 'F32Type',
               'f16': 'F16Type', 'void': 'VoidType'}
BUFFER_KIND = {('u8', True): 'ReadOnlyU8BufferType', ('u32', False): 'U32BufferType',
               ('atomic', False): 'AtomicU32BufferType', ('atomic_i32', False): 'AtomicI32BufferType', ('i32', True): 'ReadOnlyI32BufferType',
               ('i32', False): 'I32BufferType', ('f32', True): 'ReadOnlyF32BufferType',
               ('f32', False): 'F32BufferType', ('f16', True): 'ReadOnlyF16BufferType',
               ('f16', False): 'F16BufferType'}


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
VALUE_CASTS = {'NoOp', 'LValueToRValue', 'IntegralCast', 'IntegralToBoolean', 'FloatingCast',
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
            if cast and cast not in VALUE_CASTS:
                raise Unsupported(node, 'conversion', f'{cast} has no qpel equivalent')
            node = node['inner'][0]
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
    return source[end['offset']:end['offset'] + end['tokLen']]


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
        other = [k for k in attrs if k.startswith('Metal') and k != 'MetalBufferIndexAttr']
        return other[0] if other else None


DIALECT = {'cuda': Cuda(), 'metal': Metal()}
ATOMIC_ELEMENTS = {'u32': 'atomic', 'i32': 'atomic_i32'}  # element -> scalar of its atomic buffer
ZERO = {'bool': ('Literal', 'false'), 'u32': ('Literal', '0u'), 'i32': ('I32Literal', '0'), 'f32': ('F32Literal', '0.0')}
VALUE_KINDS = {'U32Type', 'I32Type', 'F32Type'}
GRID3D_BLOCK = (16, 16, 1)  # default block for 2D/3D kernels without a launch plan
MINIMUM = {'min', 'umin'}
MAXIMUM = {'max', 'umax'}
FMA = {'__fmaf_rn', 'fmaf', 'fma'}
TO_F16 = {'__float2half_rn', '__float2half'}
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
        decls = []
        for node in clang_ast.main_file_decls(unit, path):
            decls.extend(self.attempt(lambda: self.top_level(node), []))
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

    def top_level(self, node):
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
            uses = [d for d in node.get('inner', ()) if d['kind'] == 'FunctionDecl' and body_of(d) is not None
                    and any(c['kind'] == 'TemplateArgument' for c in d.get('inner', ()))]
            if not uses:
                return []  # never instantiated, so never called
            if len(uses) == 1:
                arguments = ', '.join(c.get('type', {}).get('qualType', '?') for c in uses[0]['inner'] if c['kind'] == 'TemplateArgument')
                self.adaptations.append(f'template {node["name"]} is used only with <{arguments}> and was imported for it')
                return self.top_level(uses[0])
            error = Unsupported(node, 'template', f'template {node.get("name", "")} is used with {len(uses)} types; qpel has no templates')
            if self.issues is not None:  # survey the first instantiation, so the template's other blockers show too
                self.issues.append((node.get('name'), error))
                return self.top_level(uses[0])
            self.rejected.add(node.get('name'))
            self.current = node.get('name')
            raise Unsupported(node, 'template', f'template {node.get("name", "")} is used with {len(uses)} types; qpel has no templates')
        if kind == 'FunctionDecl':
            if body_of(node) is None or node.get('name', '').startswith('qpel_'):
                return []  # declarations and qpel backend helpers
            if self.dialect.is_host(node):
                self.host.append(node['name'])
                self.host_spans.append(clang_ast.span(node))
                return []
            self.qualified[node['name']] = self.namespace + node['name']
            return [node]
        if kind in ('StaticAssertDecl', 'UsingDirectiveDecl', 'TypedefDecl') or \
                (kind == 'CXXRecordDecl' and node.get('name') == 'QpelShape'):
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
                callee = decl_name(node['inner'][0])
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
                scalar = ATOMIC_ELEMENTS.get(t[2], t[2] if t[2] in ATOMIC_ELEMENTS.values() else None)
                if scalar is None:
                    raise Unsupported(p, 'atomic-type', f'atomics on {p["type"]["qualType"]} are not in qpel; only on unsigned int and int')
                return BUFFER_KIND[(scalar, False)]
            if t[2] == 'u8' and not t[1]:
                if p['name'] in written_buffers(body_of(decl)):
                    raise Unsupported(p, 'buffer-type', 'writable unsigned char buffers are not in qpel yet')
                self.adaptations.append(f'{decl["name"]}: {p["name"]} is never written, so it became read-only')
                return 'ReadOnlyU8BufferType'
            kind = BUFFER_KIND.get((t[2], t[1]))
            if kind is None:
                raise Unsupported(p, 'buffer-type', f'{p["type"]["qualType"]} buffers are not in qpel yet')
            return kind
        if t[0] == 'shape':
            return 'Shape3DType'
        if t[0] == 'scalar' and t[1] in SCALAR_KIND and t[1] != 'void':
            return SCALAR_KIND[t[1]]
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
            calls = sorted({decl_name(n['inner'][0]) for n in walk(body_of(decl)) if n['kind'] == 'CallExpr'}
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

    def run(self, plan=None, block_size=64):
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
        module = self.graph.add('GpuModule', {'name': self.module}, {'functions': function_ids})
        pipeline = self.pipeline(module, plan, block_size)
        return self.graph.program(module, pipeline)

    def function(self, decl, types):
        name = decl['name']
        _, ident, kernel = self.functions[name]
        self.scope = {}
        self.kernel = kernel
        self.assigned = assigned_decls(body_of(decl))
        self.names = {p.get('name') for p in params_of(decl)}  # qpel names are unique per function
        params = []
        position = 0
        shape_param = None
        self.grid3d = kernel and self.uses_grid3d(decl)
        count = self.attempt(lambda: self.element_count(decl, types)) if kernel and not self.grid3d else None
        for i, p in enumerate(params_of(decl)):
            builtin = self.dialect.builtin(p) if kernel else None
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
            attrs = {'name': p['name']}
            if kernel:
                attrs.update(binding=self.dialect.binding(p, position), isBuffer=True)
                if kind == 'Shape3DType':
                    shape_param = p
                elif count is not None and p['id'] == count['id']:
                    kind = 'Shape3DType'
                elif kind not in BUFFER_KIND.values() and kind not in VALUE_KINDS:
                    self.attempt(lambda: self.reject(Unsupported(
                        p, 'scalar-kernel-argument', 'kernel arguments can be unsigned int, int or float')))
            position += 1
            ir = self.graph.add('Parameter', attrs, {'type': self.graph.add(kind)})
            self.scope[p['id']] = ('param', ir, kind)
            params.append(ir)
        if kernel and self.grid3d and shape_param is None and not any(
                e and e[0] == 'untyped' for e in self.scope.values()):
            extents = self.attempt(lambda: self.axis_extents(decl, params))
            if extents:
                shape_param = self.extent_shape(decl, params, extents)
        if kernel:
            if count is not None:
                entry = self.scope[count['id']]
                self.scope[count['id']] = ('count', entry[1], entry[2])
                self.adaptations.append(f'{name}: count {count["name"]} became a shape (count, 1, 1)')
            elif shape_param is None:
                self.attempt(lambda: self.reject(Unsupported(
                    decl, 'no-extent', 'a 2D/3D kernel needs a uint3 shape argument' if self.grid3d else
                    'a kernel needs a uint3 shape, or an unsigned element count that the thread index is compared with')))
            self.kernel_params[name] = {self.graph.nodes[p]['attributes']['name']: p for p in params}
            self.arguments[name] = self.argument_map(decl)
        result = describe(decl['type']['qualType'].split('(')[0])
        if result[0] != 'scalar' or result[1] not in SCALAR_KIND:
            self.attempt(lambda: self.reject(Unsupported(decl, 'result-type', 'unsupported result type ' + decl['type']['qualType'])))
            result = ('scalar', 'void')
        body = self.block(body_of(decl))
        statements = self.graph.nodes[body]['children'].get('statements', [])
        if len(statements) == 1 and self.graph.nodes[statements[0]]['kind'] == 'Block':
            body = statements[0]  # `{ { ... } }`, the shape qpel's own output has
        attrs = {'name': name, 'isKernel': True} if kernel else {'name': name}
        children = {'parameters': params, 'resultType': self.graph.add(SCALAR_KIND[result[1]]), 'body': body}
        if kernel:
            children['domain'] = self.graph.add('Grid3D' if self.grid3d else 'Grid1D')
            self.domains[name] = 'grid3d' if self.grid3d else 'grid1d'
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
                item.update(source='volume', parameter=p['name'])
            else:
                item.update(source='parameter', parameter=p['name'])
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
                   if self.graph.nodes[self.graph.nodes[ir]['children']['type'][0]]['kind'] in ('U32Type', 'I32Type')}
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
                               {'type': self.graph.add('Shape3DType')})
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
        if any(types.get((decl['name'], i)) == 'Shape3DType' for i in range(len(params))):
            return None
        unsigned = {p['id']: p for i, p in enumerate(params)
                    if types.get((decl['name'], i)) == 'U32Type' and not self.dialect.builtin(p)}
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
        if kind == 'DeclStmt':
            return self.local_group(node)
        if kind == 'IfStmt':
            if node.get('hasInit') or node.get('hasVar'):
                raise Unsupported(node, 'if-declaration', 'declarations inside if (...) are not supported')
            cond, then, *otherwise = node['inner']
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
        if t[0] != 'scalar' or t[1] not in SCALAR_KIND or t[1] == 'void':
            raise Unsupported(decl, 'local-type', f'local {name} of type {decl["type"]["qualType"]} is not in qpel yet')
        init = decl['inner'][0] if 'init' in decl else None
        if init is None:
            if t[1] not in ZERO:
                raise Unsupported(decl, 'uninitialized-local', f'local {name} needs an initializer')
            # Reading an uninitialized local is undefined in C, so any value is a faithful start.
            self.adaptations.append(f'{self.current}: local {name} had no initializer; qpel starts it at zero')
            name = self.unique_name(name)
            ir = self.graph.add('Variable', {'name': name}, {'type': self.graph.add(SCALAR_KIND[t[1]]),
                                                               'value': self.graph.add(*ZERO[t[1]][:1], {'value': ZERO[t[1]][1]})})
            self.scope[decl['id']] = ('local', ir, SCALAR_KIND[t[1]])
            return [ir]
        axis = grid_axis(init) or (self.axis_of(init) if peel(init)['kind'] in ('MemberExpr', 'ExtVectorElementExpr') else None)
        if self.kernel and axis and decl['id'] not in self.assigned:
            self.scope[decl['id']] = ('grid', axis)  # a name for the thread index (CUDA expression or Metal position.y)
            return []
        value = self.expr(init)
        name = self.unique_name(name)
        ir = self.graph.add('Variable', {'name': name},
                            {'type': self.graph.add(SCALAR_KIND[t[1]]), 'value': value})
        self.scope[decl['id']] = ('local', ir, SCALAR_KIND[t[1]])
        return [ir]

    def unique_name(self, name):
        """C allows one name in sibling blocks; qpel names are unique in a function."""
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
            one = {'u32': ('Literal', '1u'), 'i32': ('I32Literal', '1')}.get(scalar_of(target))
            if one is None:
                raise Unsupported(node, 'increment', 'only 32-bit integers can be incremented')
            step = add('Binary', {'operator': node['opcode'][0]},
                       {'left': self.expr(target), 'right': add(one[0], {'value': one[1]})})
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
        core = peel(node)
        target = scalar_of(node)
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
        if n['kind'] not in EXPLICIT_CASTS or scalar_of(n) != 'i32':
            return None
        inner = strip_parens(cast_operand(n))
        if inner['kind'] != 'BinaryOperator' or scalar_of(inner) != 'u32' or inner['opcode'] not in ('+', '-', '*', '<<'):
            return None
        left, right = (signed_operand(x) for x in inner['inner'])
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
                and int(peel(n['inner'][1])['value']) == 31:
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
        if value > U32_MAX:
            raise Unsupported(n, 'literal-range', 'integer literal does not fit in 32 bits')
        if target == 'f32':
            return self.graph.add('F32Literal', {'value': str(value)})
        if target == 'i32':
            return self.graph.add('I32Literal', {'value': str(value)})
        if target == 'u8':
            return self.graph.add('U8Cast', children={'operand': self.literal(n, 'u32')})
        unsigned = scalar_of(n) == 'u32' or value >= 2**31
        return self.graph.add('Literal', {'value': str(value) + ('u' if unsigned else '')})

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
        raise Unsupported(outer, 'conversion', f'conversion from {source} to {target} is not in qpel')

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
            if entry is None:
                if ref.get('name') in ('threadIdx', 'blockIdx', 'blockDim', 'gridDim'):
                    raise Unsupported(n, 'thread-builtin', 'only blockIdx.x * blockDim.x + threadIdx.x is supported')
                raise Unsupported(n, 'unknown-name', f'{ref.get("name")} is not a parameter or local')
            if entry[0] == 'grid':
                return self.thread_index(entry[1], n)
            if entry[0] == 'position':
                raise Unsupported(n, 'thread-builtin', 'use the .x, .y and .z components of the thread position')
            if entry[0] == 'extent':
                member = add('Member', {'member': entry[2]}, {'base': self.name(entry[1])})
                return add('I32Cast', children={'operand': member}) if entry[3] == 'I32Type' else member
            if entry[0] == 'count':
                member = lambda m: add('Member', {'member': m}, {'base': self.name(entry[1])})
                xy = add('Binary', {'operator': '*'}, {'left': member('x'), 'right': member('y')})
                return add('Binary', {'operator': '*'}, {'left': xy, 'right': member('z')})
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
        if grid_axis(n):
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
                if not address or op not in ('+', '-') or scalar_of(left) != 'pointer':
                    raise Unsupported(n, 'pointer', 'pointer arithmetic is only supported as an atomic address')
                return add('Binary', {'operator': op}, {'left': self.expr(left, address=True), 'right': self.offset(right)})
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
        if kind in EXPLICIT_CASTS:  # explicit cast to float or half, see peel()
            operand = peel(n['inner'][0])
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
            if entry and entry[0] == 'param' and entry[2] == 'Shape3DType' and field in ('x', 'y', 'z'):
                return add('Member', {'member': field}, {'base': self.name(entry[1])})
            if decl_name(base) in ('threadIdx', 'blockIdx', 'blockDim', 'gridDim'):
                raise Unsupported(n, 'thread-builtin', 'only blockIdx.x * blockDim.x + threadIdx.x is supported')
            raise Unsupported(n, 'member', 'only shape.x, shape.y and shape.z members are supported')
        if kind == 'CallExpr':
            return self.call(n, statement)
        raise Unsupported(n, 'expression', f'{kind} is not supported')

    def thread_index(self, axis, node):
        if self.grid3d:
            return self.graph.add('ThreadIndex', {'axis': axis})
        if axis != 'x':
            raise Unsupported(node, 'thread-builtin', 'internal: 1D kernel with a y/z index')
        return self.graph.add('GridIndex')

    def buffer_kind(self, node):
        core = peel(node)
        entry = self.scope.get(core.get('referencedDecl', {}).get('id')) if core['kind'] == 'DeclRefExpr' else None
        self.typed(entry, node)
        if not entry or entry[0] != 'param' or entry[2] not in BUFFER_KIND.values():
            raise Unsupported(node, 'buffer', 'only buffer parameters can be indexed')
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
        return node['kind'] == 'ArraySubscriptExpr' and self.buffer_kind(node['inner'][0]).startswith('Atomic')

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

    def call(self, n, statement):
        add = self.graph.add
        callee = decl_name(n['inner'][0])
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
        if callee in ('qpel_i32_div', 'qpel_i32_rem'):  # qpel's defined signed / and %
            need(2)
            return add('Binary', {'operator': '/' if callee == 'qpel_i32_div' else '%'},
                       {'left': self.expr(args[0]), 'right': self.expr(args[1])})
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
            ids[r['name']] = add('Resource', {'name': r['name'], 'kind': r['kind']}, {'type': add(r['type'])})
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
            if (s.get('domain') == 'grid3d') != (self.domains[s['kernel']] == 'grid3d'):
                raise ValueError(f'Plan and source disagree on whether {s["kernel"]} is 2D/3D')
            if s.get('domain') == 'grid3d':
                attrs.update(blockHeight=s['blockShape'][1], blockDepth=s['blockShape'][2])
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
                kind = self.graph.nodes[self.graph.nodes[ident]['children']['type'][0]]['kind']
                role = ('shape' if kind == 'Shape3DType' else 'value' if kind in VALUE_KINDS else 'input' if kind.startswith('ReadOnly')
                        else 'scratch' if kind.startswith('Atomic') else 'output')
                previous = resources.setdefault(name, {'name': name, 'kind': role, 'type': kind})
                if previous['type'] != kind:
                    raise Unsupported(None, 'resource-type', f'{name} has type {previous["type"]} and {kind} in different kernels')
                if role == 'shape':
                    grid = name
                bindings.append({'parameter': name, 'resource': name})
            stage = {'kernel': kernel, 'grid': grid, 'blockSize': block_size, 'completionBarrier': True, 'bindings': bindings}
            if self.domains[kernel] == 'grid3d':
                stage.update(blockSize=GRID3D_BLOCK[0], domain='grid3d', blockShape=list(GRID3D_BLOCK))
            stages.append(stage)
        shapes = [r['name'] for r in resources.values() if r['kind'] == 'shape']
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


def signed_operand(node):
    """x when node is an explicit (unsigned) cast of an int expression x."""
    n = strip_parens(node)
    if n['kind'] in EXPLICIT_CASTS and scalar_of(n) == 'u32' and scalar_of(cast_operand(n)) == 'i32':
        return cast_operand(n)
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


def import_source(path, dialect=None, module=None, plan=None, block_size=64, include=()):
    """Import every kernel and device function in `path`. Returns (program, report).

    `plan` is an optional launch plan (the pipeline.json qpel writes); without one,
    each kernel becomes one dispatch over the single shape parameter."""
    path = Path(path)
    dialect = dialect or clang_ast.dialect_of(path)
    unit, source = clang_ast.parse(path, dialect, include)
    module = module or (plan or {}).get('module') or path.stem
    if not is_identifier(module):
        raise ValueError(f'{module!r} cannot be a qpel module name; pass one explicitly')
    importer = Importer(unit, source, path, dialect, module)
    program = importer.run(plan, block_size)
    report = {'source': str(path), 'dialect': dialect, 'module': module,
              'kernels': [d['name'] for d in importer.decls if importer.functions[d['name']][2]],
              'helpers': [d['name'] for d in importer.decls if not importer.functions[d['name']][2]],
              'adaptations': importer.adaptations, 'warnings': unguarded_kernels(program),
              'qualified': {k: importer.qualified[k] for k in importer.arguments}, 'arguments': importer.arguments,
              'host_spans': importer.host_spans}
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
        if n['kind'] == 'Variable':
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
