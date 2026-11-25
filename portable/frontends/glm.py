"""glm (OpenGL Mathematics, the header-only C++ library gsplat computes with), read by running glm's own code.

glm's types are C++ classes: vec<N, T> (N components), mat<C, R, T> (C columns of vec<R, T>) and qua<T>, with
constructors, operators and functions defined in headers. The importer does not reimplement them. It inlines
glm's own instantiated code (the clang AST of each function the source reaches) symbolically, while reading:

- a glm object is a list of its scalar components, column-major for matrices (m[c][r] is component c R + r);
- each scalar a glm function computes is the importer's reading of that very C expression (Importer.expr), so the
  arithmetic is glm's, operation for operation, in glm's order: a 3 x 3 product is glm's `tmp0 = A0 * B0.x;
  tmp0 += A1 * B0.y; ...`, a 4-vector dot product glm's `(x + y) + (z + w)`. Results are the original's bit for
  bit where the original rounds each operation (the CPU simulator's -ffp-contract=off);
- glm's control flow is decided while reading: an operator[]'s switch on its constant index, an `if constexpr`,
  a loop over a matrix's columns (unrolled); glm's asserts are left out, as the importer does for any assert;
- a scalar glm computes that is not a name or a literal becomes a local (named glm_N) before the statement, so
  each is computed once.

At the source's own level, a glm vector is a qpel vector (vec<T, N>, as before this module) and a glm matrix an
array of its C * R scalars: a local `array<f32, 9>`, a helper parameter taking the caller's array by reference,
a helper result an extra reference parameter `result` the helper writes. A pointer to a glm object a helper
receives (gsplat's `mat3 *covar`, given `covars ? &covar : nullptr`) is the caller's object by reference and a
u32 `<name>_present` (1 when the caller passed one). An element m[i][j] of the source's own matrices may have
indices known only at run time (a loop's): it is component i R + j of the array.

IMPORT.md, glm, has the rules and their limits."""
import re

from qpel_types import is_array, is_vector, vector_element, vector_length

from .c_import import Unsupported, walk, body_of, params_of, scalar_of, ASSERT_CALLS


def integer_constant(node):
    from .c_import import integer_constant as found  # (defined after the importer)
    return found(node)


GLM_FILE = re.compile(r'[/\\]glm[/\\]')
ELEMENTS = {'float': 'f32', 'int': 'i32', 'unsigned int': 'u32', 'uint': 'u32'}
FIELDS = {name: k for k, names in enumerate(('xrs', 'ygt', 'zbp', 'waq')) for name in names}
VECTOR_FIELDS = 'xyzw'
_TYPE = re.compile(
    r'(?:const\s+)?(?:volatile\s+)?(?:typename\s+)?(?:(vec)<(\d), ([a-z ]+?)(?:, [^<>]*)?>|(mat)<(\d), (\d), ([a-z ]+?)'
    r'(?:, [^<>]*)?>(?:::(col_type|row_type))?|(qua)<([a-z ]+?)(?:, [^<>]*)?>)\s*(?:const\s*)?(&&|&|\*)?\s*(?:const)?\s*')
STRIP = {'ParenExpr', 'ExprWithCleanups', 'MaterializeTemporaryExpr', 'CXXBindTemporaryExpr', 'ConstantExpr'}
VALUE_CASTS = (None, 'NoOp', 'LValueToRValue', 'ConstructorConversion', 'UncheckedDerivedToBase', 'DerivedToBase')
LIMIT = 1024  # iterations of one glm loop read while inlining (glm's loops run over columns or components)
INTEGERS = ('i32', 'u32', 'i64', 'u64', 'bool', 'i16', 'u16', 'i8', 'u8')


class GlmType:
    """vec: columns 1, rows N; mat: C columns of R; qua: 4 components x, y, z, w (glm's storage order)."""
    def __init__(self, kind, columns, rows, element, ref=None):
        self.kind, self.columns, self.rows, self.element, self.ref = kind, columns, rows, element, ref

    @property
    def size(self):
        return self.columns * self.rows

    @property
    def term(self):
        """The qpel type of a value of this type at the source's level."""
        if self.kind == 'mat':
            return f'array<{self.element}, {self.size}>'
        return f'vec<{self.element}, {self.rows}>'

    def value(self):
        return GlmType(self.kind, self.columns, self.rows, self.element)

    def column(self):
        return GlmType('vec', 1, self.rows, self.element)

    def same(self, other):
        return other is not None and (self.kind, self.columns, self.rows, self.element) == \
            (other.kind, other.columns, other.rows, other.element)

    def __repr__(self):
        return f'{self.kind}<{self.columns}x{self.rows} {self.element}>'


ALIASES = {}  # the unit's names of glm types (gsplat: using mat3 = glm::mat<3, 3, float>; glm's own typedefs), per import
_ALIAS = re.compile(r'(?:const\s+)?(?:volatile\s+)?(?:(?:glm|gsplat)::)?(\w+)\s*(?:const\s*)?(&&|&|\*)?\s*(?:const)?\s*')


def normal(spelling):
    """A type or signature spelled as glm's in-class declarations are: no qualifier argument, no glm::."""
    return re.sub(r',\s*\((?:glm::)?qualifier\)\d+U?', '', spelling or '').replace('glm::', '').replace(' noexcept', '').strip()


def glm_aliases(unit):
    """{name: glm type spelling} of the type aliases and typedefs in the unit that name glm types."""
    found = {}
    for node in walk(unit):
        if node.get('kind') in ('TypeAliasDecl', 'TypedefDecl') and node.get('name'):
            t = node.get('type', {})
            spelled = t.get('desugaredQualType') or t.get('qualType') or ''
            if re.match(r'(?:glm::)?(?:vec|mat|qua)<', spelled) and node['name'] not in found:
                found[node['name']] = spelled
    return found


def glm_type(t):
    """The GlmType of a clang type ({'qualType', 'desugaredQualType'}), or None."""
    if not isinstance(t, dict):
        return None
    spellings = [t.get('desugaredQualType'), t.get('qualType')]
    alias = _ALIAS.fullmatch(t.get('qualType') or '')
    if alias and alias.group(1) in ALIASES:  # mat3 *: glm::mat<3, 3, float> *
        spellings.append(ALIASES[alias.group(1)] + (' ' + alias.group(2) if alias.group(2) else ''))
    for spelling in spellings:
        if not spelling:
            continue
        text = normal(spelling)
        found = _TYPE.fullmatch(text)
        if not found:
            continue
        g = found.groups()
        ref = g[10]
        if g[0]:
            element = ELEMENTS.get(g[2].strip())
            return GlmType('vec', 1, int(g[1]), element, ref) if element and 1 <= int(g[1]) <= 4 else None
        if g[3]:
            element = ELEMENTS.get(g[6].strip())
            if not element:
                return None
            c, r = int(g[4]), int(g[5])
            if g[7] == 'col_type':
                return GlmType('vec', 1, r, element, ref)
            if g[7] == 'row_type':
                return GlmType('vec', 1, c, element, ref)
            return GlmType('mat', c, r, element, ref)
        element = ELEMENTS.get(g[9].strip())
        return GlmType('qua', 1, 4, element, ref) if element else None
    return None


def node_glm(node):
    return glm_type(node.get('type')) if isinstance(node, dict) else None


_RECORD_FILE = re.compile(r'type_vec(\d)\.(?:inl|hpp)$|type_mat(\d)x(\d)\.(?:inl|hpp)$|type_quat\.(?:inl|hpp)$')


def record_of_file(decl):
    """(kind, columns, rows) of the glm class a member is written for, read from its file: glm defines each class in
    its own files (detail/type_vec3.inl, type_mat3x3.inl, gtc/type_quat... ext/type_quat.inl)."""
    loc = decl.get('loc', {})
    loc = loc.get('expansionLoc', loc)
    found = _RECORD_FILE.search(loc.get('_file') or '')
    if not found:
        return None
    if found.group(1):
        return ('vec', 1, int(found.group(1)))
    if found.group(2):
        return ('mat', int(found.group(2)), int(found.group(3)))
    return ('qua', 1, 4)


def is_glm_decl(decl):
    loc = decl.get('loc', {}) if decl else {}
    loc = loc.get('expansionLoc', loc)
    return bool(GLM_FILE.search(loc.get('_file') or ''))


def strip(node, casts=VALUE_CASTS):
    n = node
    while n.get('kind') in STRIP | {'ImplicitCastExpr', 'CStyleCastExpr', 'CXXStaticCastExpr', 'CXXFunctionalCastExpr'} and \
            n.get('inner') and (n.get('kind') in STRIP or n.get('castKind') in casts):
        n = n['inner'][0]
    return n


def is_null(node):
    n = strip(node, VALUE_CASTS + ('NullToPointer',))
    return n.get('kind') in ('CXXNullPtrLiteralExpr', 'GNUNullExpr') or n.get('kind') == 'IntegerLiteral' and n.get('value') == '0'


def is_assert(n):
    """An assert(e) as the C library spells it: `(e) ? (void)0 : __assert_rtn(...)`."""
    core = n
    while core.get('kind') in ('ParenExpr', 'CStyleCastExpr', 'ImplicitCastExpr') and core.get('inner'):
        core = core['inner'][0]
    if core.get('kind') != 'ConditionalOperator' or len(core.get('inner', ())) != 3:
        return False
    return any(x.get('kind') == 'DeclRefExpr' and x.get('referencedDecl', {}).get('name') in ASSERT_CALLS
               for branch in core['inner'][1:] for x in walk(branch))


def operator_name(n):
    target = n['inner'][0]
    while target.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and target.get('inner'):
        target = target['inner'][0]
    return (target.get('referencedDecl') or {}).get('name')


# --- values ------------------------------------------------------------------

class Const:
    """A value known while reading (glm's indices, lengths, template flags)."""
    def __init__(self, value, term):
        self.value, self.term = value, term


class Atom:
    """A scalar that can be read any number of times: build() makes a new IR node for each read."""
    def __init__(self, build, term, reads=frozenset()):
        self.build, self.term, self.reads = build, term, reads


class Obj:
    """A glm object, or a part of one (a matrix column): `size` components of `cells` from `offset`. `user` is
    (mode, IR declaration, the cells as read) for the source's own variable the object is."""
    def __init__(self, cells, offset, t, user=None):
        self.cells, self.offset, self.t, self.user = cells, offset, t, user

    def get(self, k):
        return self.cells[self.offset + k]

    def set(self, k, value):
        self.cells[self.offset + k] = value

    def column(self, c):
        if self.t.kind != 'mat' or not 0 <= c < self.t.columns:
            raise Unsupported({}, 'glm', f'column {c} of a {self.t}')
        return Obj(self.cells, self.offset + c * self.t.rows, self.t.column(), self.user)

    def copy(self):
        return Obj([self.get(k) for k in range(self.t.size)], 0, self.t.value())


class Columns:
    """A matrix's member `value`, its array of columns."""
    def __init__(self, obj):
        self.obj = obj


class Field:
    """One component of an object, as an lvalue (v.x, m[i][j], a T & result)."""
    def __init__(self, obj, k):
        self.obj, self.k = obj, k


class Pointer:
    def __init__(self, target):
        self.target = target


class Functor:
    """std::plus<T>() and the other function objects glm's vector arithmetic passes to its functor1/2 (detail/
    _vectorize.hpp): calling one is the operator."""
    def __init__(self, operator):
        self.operator = operator


FUNCTORS = {'plus': '+', 'minus': '-', 'multiplies': '*', 'divides': '/', 'modulus': '%', 'negate': 'neg',
            'bit_and': '&', 'bit_or': '|', 'bit_xor': '^', 'equal_to': '==', 'not_equal_to': '!=', 'less': '<',
            'greater': '>', 'less_equal': '<=', 'greater_equal': '>=', 'logical_and': '&&', 'logical_or': '||'}
_FUNCTOR = re.compile(r'(?:const\s+)?(?:std::)?(?:__1::)?(' + '|'.join(FUNCTORS) + r')<[^<>]*>\s*&?')


def functor_of(node):
    """The Functor an argument constructs (std::multiplies<float>()), or None."""
    t = node.get('type', {}) if isinstance(node, dict) else {}
    for spelling in (t.get('desugaredQualType'), t.get('qualType')):
        found = _FUNCTOR.fullmatch((spelling or '').strip())
        if found:
            return Functor(FUNCTORS[found.group(1)])
    return None


class Local:
    """A scalar local or by-value parameter of an inlined glm function: assignable."""
    def __init__(self, value):
        self.value = value


class Return(Exception):
    pass


class Break(Exception):
    pass


# --- the reader ------------------------------------------------------------------

def guarded(method):
    """An entry point from the importer: a Python error while reading glm code is a blocker of that code, not a
    failure of the import."""
    def run(self, node, *rest):
        try:
            return method(self, node, *rest)
        except Unsupported:
            raise
        except (KeyError, IndexError, TypeError, AttributeError, ValueError, RecursionError) as error:
            raise Unsupported(node if isinstance(node, dict) else {}, 'glm', f'glm code qpel does not read ({type(error).__name__}: {error})') from None
    run.__name__, run.__doc__ = method.__name__, method.__doc__
    return run


class GlmReader:
    """Mixed into Importer: what it reads of glm."""

    def glm_setup(self, unit):
        self.glm_frames = []
        self.glm_bodies = {}  # clang id of a glm function (its declaration or definition) -> the definition
        self.glm_pointer_params = {}  # clang id of a helper's pointer-to-glm parameter -> its <name>_present Parameter
        self.glm_decl_types = {}  # clang id of the source's glm matrix local or parameter -> its GlmType
        self.glm_result = None  # the result parameter of the helper being read, when it gives a glm matrix
        self.glm_counter = 0
        ALIASES.clear()
        ALIASES.update(glm_aliases(unit))
        self.glm_ctors = {}  # (kind, columns, rows, element, signature) -> clang id of that glm constructor
        self.glm_members = {}  # clang id of a member of a glm class's instance -> (kind, columns, rows)
        self.glm_decls = {}  # clang id -> a glm function's declaration (with or without its body)
        self.glm_defs = {}  # (kind, columns, rows, name, signature) -> definitions of members written outside the class
        for record in walk(unit):
            if record.get('kind') != 'ClassTemplateSpecializationDecl' or record.get('name') not in ('vec', 'mat', 'qua') or \
                    not is_glm_decl(record):
                continue
            args = [x for x in record.get('inner', ()) if x.get('kind') == 'TemplateArgument']
            values = [x.get('value', normal(x.get('type', {}).get('qualType'))) for x in args]
            if record['name'] == 'vec' and len(values) == 3 and values[2] == 0 and values[1] in ELEMENTS:
                key = ('vec', 1, values[0], ELEMENTS[values[1]])
            elif record['name'] == 'mat' and len(values) == 4 and values[3] == 0 and values[2] in ELEMENTS:
                key = ('mat', values[0], values[1], ELEMENTS[values[2]])
            elif record['name'] == 'qua' and len(values) == 2 and values[1] == 0 and values[0] in ELEMENTS:
                key = ('qua', 1, 4, ELEMENTS[values[0]])
            else:
                continue
            members = []
            for x in record.get('inner', ()):
                if x.get('kind') == 'CXXConstructorDecl':
                    members.append(x)
                elif x.get('kind') == 'FunctionTemplateDecl':
                    members.extend(c for c in x.get('inner', ()) if c.get('kind') == 'CXXConstructorDecl')
            for c in members:
                signature = key + (normal(c.get('type', {}).get('qualType')),)
                if signature not in self.glm_ctors or self.glm_ctors[signature] not in self.glm_bodies:
                    self.glm_ctors[signature] = c['id']
            for x in record.get('inner', ()):
                for m in [x] + [c for c in x.get('inner', ()) if x.get('kind') == 'FunctionTemplateDecl']:
                    if m.get('kind') in ('CXXMethodDecl', 'CXXConstructorDecl', 'CXXConversionDecl'):
                        self.glm_members[m['id']] = key[:3]
        for d in walk(unit):
            if d.get('kind') in ('FunctionDecl', 'CXXMethodDecl', 'CXXConstructorDecl', 'CXXConversionDecl') and is_glm_decl(d):
                self.glm_decls.setdefault(d['id'], d)
                if body_of(d) is None:
                    continue
                self.glm_bodies[d['id']] = d
                if d.get('previousDecl'):
                    self.glm_bodies.setdefault(d['previousDecl'], d)
                record = record_of_file(d) if d['kind'] != 'FunctionDecl' else None
                if record:
                    key = record + (d.get('name'), normal(d.get('type', {}).get('qualType')))
                    self.glm_defs.setdefault(key, []).append(d)

    # -- small tools --

    def glm_fail(self, node, message):
        raise Unsupported(node or {}, 'glm', message)

    def glm_fresh_name(self, stem):
        while True:
            self.glm_counter += 1
            name = f'{stem}{self.glm_counter}'
            if name not in self.names:
                self.names.add(name)
                return name

    def glm_temp(self, term, value, node):
        """A local before the statement, holding value."""
        if self.prelude is None:
            self.glm_fail(node, 'a glm computation here would need a local before the statement')
        var = self.graph.add('Variable', {'name': self.glm_fresh_name('glm_')}, {'type': self.type_node(term), 'value': value})
        self.prelude.append(var)
        return var

    def glm_clone(self, ident):
        n = self.graph.nodes[ident]
        children = {role: [self.glm_clone(c) for c in kids] for role, kids in n['children'].items()}
        return self.graph.add(n['kind'], dict(n['attributes']), children, dict(n['links']))

    def glm_reads(self, ident):
        n = self.graph.nodes[ident]
        found = {n['links']['declaration']} if n['kind'] == 'Name' and 'declaration' in n['links'] else set()
        for kids in n['children'].values():
            for c in kids:
                found |= self.glm_reads(c)
        return found

    def glm_cheap(self, ident):
        """A name, a literal, or one element of a named vector or array at a literal position."""
        n = self.graph.nodes[ident]
        if not n['children']:
            return True
        if n['kind'] in ('Member', 'Index'):
            return all(self.graph.nodes[c]['kind'] == 'Name' or not self.graph.nodes[c]['children']
                       for kids in n['children'].values() for c in kids)
        return False

    def glm_atom(self, ident, term, node):
        """An Atom of an IR value: itself when it is cheap to read again, else a local holding it."""
        if self.glm_cheap(ident):
            return Atom(lambda: self.glm_clone(ident), term, frozenset(self.glm_reads(ident)))
        var = self.glm_temp(term, ident, node)
        return Atom(lambda: self.name(var), term)

    def glm_build(self, value, term=None, node=None):
        """A fresh IR node for a scalar value, converted to term when given."""
        if isinstance(value, Local):
            value = value.value
        if isinstance(value, Field):
            value = value.obj.get(value.k)
        if isinstance(value, Const):
            ir = self.graph.add('Literal', {'value': 'true' if value.value else 'false'}) if value.term == 'bool' else \
                self.int_value(value.term, int(value.value))
        elif isinstance(value, Atom):
            ir = value.build()
        else:
            self.glm_fail(node, f'a glm {type(value).__name__} is not a scalar')
        if term and value.term != term:
            ir = self.convert_types(ir, value.term, term, node or {})
        return ir

    def glm_vector_ir(self, obj, node):
        e, n = obj.t.element, obj.t.size
        return self.graph.add('VectorValue', {'element': e, 'length': n},
                              {'elements': [self.glm_build(obj.get(k), e, node) for k in range(n)]})

    def glm_frame(self):
        return self.glm_frames[-1] if self.glm_frames else None

    # -- constants --

    def glm_const(self, node):
        """The value of an integer or bool expression known while reading, else None."""
        n = node
        while n.get('kind') in STRIP | {'ImplicitCastExpr', 'CStyleCastExpr', 'CXXStaticCastExpr', 'CXXFunctionalCastExpr',
                                         'SubstNonTypeTemplateParmExpr'} and n.get('inner'):
            if n.get('kind') == 'ConstantExpr' and n.get('value') is not None:
                v = n['value']
                return v == 'true' if v in ('true', 'false') else int(v)
            if n.get('kind') in ('ImplicitCastExpr', 'CStyleCastExpr', 'CXXStaticCastExpr') and scalar_of(n) in ('f32', 'f16', 'bf16'):
                return None
            n = n['inner'][-1] if n.get('kind') == 'SubstNonTypeTemplateParmExpr' else n['inner'][0]
        kind = n.get('kind')
        if kind == 'IntegerLiteral':
            return int(n['value'])
        if kind == 'CXXBoolLiteralExpr':
            return bool(n['value'])
        if kind == 'DeclRefExpr':
            frame = self.glm_frame()
            ref = n.get('referencedDecl', {})
            if frame is not None and ref.get('id') in frame['vars']:
                v = frame['vars'][ref['id']]
                v = v.value if isinstance(v, Local) else v
                return v.value if isinstance(v, Const) else None
            if ref.get('kind') in ('EnumConstantDecl', 'VarDecl'):
                return integer_constant(n)
            return None
        if kind == 'UnaryOperator':
            v = self.glm_const(n['inner'][0])
            if v is None:
                return None
            return {'-': -v, '!': not v, '~': ~v, '+': v}.get(n['opcode'])
        if kind == 'BinaryOperator':
            a, b = (self.glm_const(x) for x in n['inner'])
            op = n['opcode']
            if a is None or b is None:
                if op == '&&' and a is False or op == '||' and a is True:
                    return a
                return None
            if op in ('/', '%'):
                if not b:
                    return None
                q = abs(a) // abs(b) * (1 if (a >= 0) == (b >= 0) else -1)
                return q if op == '/' else a - q * b
            return {'+': a + b, '-': a - b, '*': a * b, '<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b, '==': a == b,
                    '!=': a != b, '&&': bool(a and b), '||': bool(a or b), '&': a & b, '|': a | b, '^': a ^ b,
                    '<<': a << b, '>>': a >> b}.get(op)
        if kind == 'ConditionalOperator':
            c = self.glm_const(n['inner'][0])
            return None if c is None else self.glm_const(n['inner'][1 if c else 2])
        if kind in ('CXXMemberCallExpr', 'CallExpr') and scalar_of(n) in INTEGERS:
            callee = self.glm_callee(n)
            if callee is not None and not params_of(callee):  # length(): `return 3;`
                found = [x for x in walk(body_of(callee)) if x.get('kind') == 'ReturnStmt' and x.get('inner')]
                if len(found) == 1:
                    return self.glm_const(found[0]['inner'][0])
        if kind in ('UnaryExprOrTypeTraitExpr', 'CXXNoexceptExpr'):
            return integer_constant(n)
        return None

    # -- callees --

    def glm_callee(self, n):
        """The glm function definition a call or construction reaches, or None."""
        kind = n.get('kind')
        ref = None
        if kind in ('CXXConstructExpr', 'CXXTemporaryObjectExpr'):
            ref = n.get('ctorDecl')  # (clang's JSON gives a construction only its constructor's type)
            t = node_glm(n)
            if not ref and t is not None:
                ref = {'id': self.glm_ctors.get((t.kind, t.columns, t.rows, t.element, normal(n.get('ctorType', {}).get('qualType'))))}
        elif kind == 'CXXMemberCallExpr':
            ref = {'id': n['inner'][0].get('referencedMemberDecl')}
        elif kind in ('CallExpr', 'CXXOperatorCallExpr'):
            target = n['inner'][0]
            while target.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and target.get('inner'):
                target = target['inner'][0]
            if target.get('kind') == 'DeclRefExpr':
                ref = target.get('referencedDecl', {})
            elif target.get('kind') == 'MemberExpr':
                ref = {'id': target.get('referencedMemberDecl')}
        if not ref and kind in ('CXXConstructExpr', 'CXXTemporaryObjectExpr') and node_glm(n) is not None:
            t = node_glm(n)  # a constructor template's instance: its definition, written outside the class
            return self.glm_unique((t.kind, t.columns, t.rows, {'vec': 'vec', 'mat': 'mat', 'qua': 'qua'}[t.kind],
                                    normal(n.get('ctorType', {}).get('qualType'))))
        if not ref:
            return None
        return self.glm_definition(ref.get('id'))

    def glm_definition(self, ident):
        """The definition (with its body) of the glm function or member a clang id names, or None."""
        if ident in self.glm_bodies:
            return self.glm_bodies[ident]
        decl = self.glm_decls.get(ident)
        if decl is None or ident not in self.glm_members:
            return None
        return self.glm_unique(self.glm_members[ident] + (decl.get('name'), normal(decl.get('type', {}).get('qualType'))))

    def glm_unique(self, key):
        found = self.glm_defs.get(key, ())
        return found[0] if len({d['id'] for d in found}) == 1 else None

    def glm_reaches(self, n):
        """True when n is a call, operator or construction of glm's own code."""
        if n.get('kind') not in ('CallExpr', 'CXXOperatorCallExpr', 'CXXMemberCallExpr', 'CXXConstructExpr',
                                 'CXXTemporaryObjectExpr'):
            return False
        if self.glm_callee(n) is not None:
            return True
        if n.get('kind') == 'CXXOperatorCallExpr' and operator_name(n) == 'operator()' and len(n.get('inner', ())) > 1 and \
                self.glm_functor(n['inner'][1]) is not None:
            return True  # Func(a.x, b.x) of a std function object glm passes along
        return n.get('kind') == 'CXXOperatorCallExpr' and operator_name(n) == 'operator=' and \
            any(node_glm(a) for a in n.get('inner', ())[1:])  # (an implicit copy assignment: no body)

    # -- objects --

    def glm_user_obj(self, decl_id, node):
        """The source's own glm variable or parameter as an object reading (and written through) its IR."""
        entry = self.scope.get(decl_id)
        t = self.glm_decl_types.get(decl_id) or node_glm(node)
        if not entry or entry[0] not in ('local', 'param') or t is None:
            return None
        ident = entry[1]
        if is_array(entry[2]) and t.kind == 'mat':
            cells = [Atom((lambda k: lambda: self.graph.add('Index', children={
                'base': self.name(ident), 'index': self.int_value('u32', k)}))(k), t.element, frozenset({ident}))
                     for k in range(t.size)]
            return Obj(cells, 0, t.value(), user=('array', ident, list(cells)))
        if is_vector(entry[2]) and t.kind in ('vec', 'qua') and vector_length(entry[2]) == t.size:
            cells = [Atom((lambda k: lambda: self.graph.add('Member', {'member': VECTOR_FIELDS[k]}, {'base': self.name(ident)}))(k),
                          t.element, frozenset({ident})) for k in range(t.size)]
            return Obj(cells, 0, t.value(), user=('vector', ident, list(cells)))
        return None

    def glm_obj(self, node):
        """The glm object an expression is (a view: writes through it change what it names)."""
        n = strip(node)
        kind = n.get('kind')
        frame = self.glm_frame()
        if kind == 'DeclRefExpr':
            ref = n['referencedDecl'].get('id')
            if frame is not None and ref in frame['vars']:
                v = frame['vars'][ref]
                if isinstance(v, Obj):
                    return v
                if isinstance(v, Pointer) and isinstance(v.target, Obj):
                    return v.target
                self.glm_fail(n, f'{n["referencedDecl"].get("name")} is not a glm object')
            found = self.glm_user_obj(ref, n)
            if found is not None:
                return found
            if is_vector(scalar_of(n)):
                return self.glm_vector_obj(n)
            self.glm_fail(n, f'{n["referencedDecl"].get("name")} is not a glm object qpel reads')
        if kind == 'UnaryOperator' and n['opcode'] == '*':
            target = self.glm_pointer(n['inner'][0])
            if isinstance(target, Obj):
                return target
            self.glm_fail(n, 'this pointer does not point to a glm object')
        if kind == 'MemberExpr':
            base = self.glm_pointer(n['inner'][0]) if n.get('isArrow') else self.glm_obj(n['inner'][0])
            if not n.get('name') and isinstance(base, Obj):
                return base  # an anonymous union or struct of the object: the object itself
            self.glm_fail(n, f'member {n.get("name")} of a glm object is not an object')
        if kind == 'ArraySubscriptExpr':
            base, index = n['inner']
            columns = self.glm_columns(base)
            k = self.glm_const(index)
            if columns is None or k is None:
                self.glm_fail(n, 'a glm matrix column is read at a constant index here')
            return columns.obj.column(k)
        if kind in ('CXXConstructExpr', 'CXXTemporaryObjectExpr'):
            return self.glm_construct(n)
        if kind in ('CallExpr', 'CXXOperatorCallExpr', 'CXXMemberCallExpr'):
            if frame is None and self.callee(n) in self.functions:
                found = self.glm_user_call(n)
                if found is not None:
                    return found
            if frame is None and not self.glm_reaches(n) and is_vector(scalar_of(n)):
                return self.glm_vector_obj(n)
            result = self.glm_call(n)
            if isinstance(result, Obj):
                return result
            if isinstance(result, Pointer) and isinstance(result.target, Obj):
                return result.target
            self.glm_fail(n, 'this call gives no glm object')
        if kind == 'ConditionalOperator':
            cond, yes, no = n['inner']
            c = self.glm_const(cond)
            if c is not None:
                return self.glm_obj(yes if c else no)
            condition = self.glm_atom(self.expr(cond), 'bool', cond)
            a, b = self.glm_obj(yes), self.glm_obj(no)
            out = a.copy()
            for k in range(a.t.size):
                value = self.graph.add('Select', children={'condition': condition.build(),
                                                          'yes': self.glm_build(a.get(k), a.t.element, n),
                                                          'no': self.glm_build(b.get(k), a.t.element, n)})
                out.set(k, self.glm_atom(value, a.t.element, n))
            return out
        if is_vector(scalar_of(n)):
            return self.glm_vector_obj(n)
        self.glm_fail(n, f'{kind} of a glm type is not read here')

    def glm_vector_obj(self, n):
        """A vector the importer reads itself (a shared-memory element, a helper's vector result) as an object."""
        t = node_glm(n)
        term = scalar_of(n)
        temp = self.glm_atom(self.expr(n), term, n)
        cells = [Atom((lambda k: lambda: self.graph.add('Member', {'member': VECTOR_FIELDS[k]}, {'base': temp.build()}))(k),
                      vector_element(term), temp.reads) for k in range(vector_length(term))]
        return Obj(cells, 0, GlmType(t.kind if t is not None else 'vec', 1, vector_length(term), vector_element(term)))

    def glm_pointer(self, node):
        """What a pointer expression points to: `this`, &x, a pointer parameter."""
        n = strip(node)
        frame = self.glm_frame()
        if n.get('kind') == 'CXXThisExpr':
            if frame is None or frame.get('this') is None:
                self.glm_fail(n, 'this outside a glm method')
            return frame['this']
        if n.get('kind') == 'UnaryOperator' and n['opcode'] == '&':
            return self.glm_lvalue(n['inner'][0])
        if n.get('kind') == 'DeclRefExpr':
            ref = n['referencedDecl'].get('id')
            if frame is not None and ref in frame['vars'] and isinstance(frame['vars'][ref], Pointer):
                return frame['vars'][ref].target
            if ref in self.glm_pointer_params:  # a helper's mat3 *covar: the caller's object
                found = self.glm_user_obj(ref, n)
                if found is not None:
                    return found
        if n.get('kind') in ('CallExpr', 'CXXMemberCallExpr', 'CXXOperatorCallExpr'):
            result = self.glm_call(n)
            if isinstance(result, Pointer):
                return result.target
        self.glm_fail(n, 'a glm pointer here is this, &x or a pointer parameter')

    def glm_lvalue(self, node):
        """An Obj, a Field, a Columns or a Local an expression names."""
        n = strip(node, (None, 'NoOp'))
        if node_glm(n) is not None and n.get('kind') != 'MemberExpr':
            return self.glm_obj(n)
        kind = n.get('kind')
        frame = self.glm_frame()
        if kind == 'DeclRefExpr':
            ref = n['referencedDecl'].get('id')
            if frame is not None and ref in frame['vars']:
                v = frame['vars'][ref]
                return v.target if isinstance(v, Pointer) and isinstance(v.target, Field) else v
            self.glm_fail(n, f'{n["referencedDecl"].get("name")} is not a glm value')
        if kind == 'MemberExpr':
            base = self.glm_pointer(n['inner'][0]) if n.get('isArrow') else self.glm_obj(n['inner'][0])
            name = n.get('name')
            if not name:
                return base
            if name == 'value' and base.t.kind == 'mat':
                return Columns(base)
            if name in FIELDS and base.t.kind in ('vec', 'qua') and FIELDS[name] < base.t.size:
                return Field(base, FIELDS[name])
            self.glm_fail(n, f'member {name} of a glm {base.t.kind}')
        if kind == 'ArraySubscriptExpr':
            return self.glm_obj(n)
        if kind == 'UnaryOperator' and n['opcode'] == '*':
            return self.glm_pointer(n['inner'][0])
        if kind in ('CallExpr', 'CXXOperatorCallExpr', 'CXXMemberCallExpr'):
            result = self.glm_call(n)
            if isinstance(result, (Field, Obj)):
                return result
        self.glm_fail(n, f'{kind} is not a glm lvalue')

    def glm_columns(self, node):
        n = strip(node, VALUE_CASTS + ('ArrayToPointerDecay',))
        if n.get('kind') == 'MemberExpr' and n.get('name') == 'value':
            found = self.glm_lvalue(n)
            return found if isinstance(found, Columns) else None
        return None

    # -- scalars --

    def glm_scalar(self, node):
        """A scalar value of an expression inside glm code (or at a glm call's arguments)."""
        term = scalar_of(node)
        if term in INTEGERS:
            c = self.glm_const(node)
            if c is not None:
                return Const(c, term)
        if term == 'f64':  # a double argument of glm's converting constructors (mat3(s, 0.0, ...)): its 64 bits
            return self.glm_atom(self.expr(node, double=True), term, node)  # (frontends/soft_double.py)
        return self.glm_atom(self.expr(node), term, node)

    @guarded
    def glm_core(self, n):
        """Importer.core's reading of glm: None when n is not glm's to read."""
        kind = n.get('kind')
        frame = self.glm_frame()
        if kind == 'DeclRefExpr' and frame is not None and n['referencedDecl'].get('id') in frame['vars']:
            v = frame['vars'][n['referencedDecl']['id']]
            if isinstance(v, Obj):
                if v.t.kind == 'mat':
                    self.glm_fail(n, 'a glm matrix is not a value')
                return self.glm_vector_ir(v, n)
            if isinstance(v, Pointer):
                self.glm_fail(n, 'a pointer inside glm code is read through * or ->')
            if isinstance(v, Columns):
                self.glm_fail(n, 'a matrix\'s columns are read one at a time')
            return self.glm_build(v, None, n)
        if kind == 'MemberExpr' and (frame is not None or self.glm_reaches(strip(n['inner'][0]))):
            place = self.glm_lvalue(n)
            if isinstance(place, Field):
                return self.glm_build(place.obj.get(place.k), None, n)
            if isinstance(place, Obj) and place.t.kind != 'mat':
                return self.glm_vector_ir(place, n)
        if kind == 'CXXOperatorCallExpr' and frame is None:
            element = self.glm_user_element(n)
            if element is not None:
                return element()
        constructs = kind in ('CXXConstructExpr', 'CXXTemporaryObjectExpr')
        if kind in ('CallExpr', 'CXXOperatorCallExpr', 'CXXMemberCallExpr') and self.glm_reaches(n) or \
                constructs and node_glm(n) is not None and (frame is not None or self.glm_reaches(n) and not self.glm_plain(n)):
            if node_glm(n) is not None or is_vector(scalar_of(n)):
                obj = self.glm_obj(n)
                if obj.t.kind == 'mat':
                    self.glm_fail(n, 'a glm matrix is not a value: it is assigned to a local, passed or returned')
                return self.glm_vector_ir(obj, n)
            result = self.glm_call(n)
            if isinstance(result, (Const, Atom, Field, Local)):
                return self.glm_build(result, None, n)
            self.glm_fail(n, 'this glm call gives no value')
        if kind == 'ImplicitCastExpr' and n.get('castKind') == 'PointerToBoolean' and self.glm_present(n['inner'][0]) is not None:
            return self.graph.add('Binary', {'operator': '!='}, {'left': self.glm_present(n['inner'][0]), 'right': self.int_value('u32', 0)})
        if kind == 'BinaryOperator' and n['opcode'] in ('==', '!='):
            a, b = n['inner']
            for p, q in ((a, b), (b, a)):
                flag = self.glm_present(p)
                if flag is not None and is_null(q):
                    return self.graph.add('Binary', {'operator': n['opcode']}, {'left': flag, 'right': self.int_value('u32', 0)})
        return None

    def glm_plain(self, n):
        """True for a glm vector built from as many scalars as it has components (vec3(x, y, z)), which the importer
        reads itself (as before glm.py)."""
        t = node_glm(n)
        args = [a for a in n.get('inner', ()) if a.get('kind') != 'CXXDefaultArgExpr']
        return t is not None and t.kind == 'vec' and len(args) == t.size and \
            all(scalar_of(a) in INTEGERS + ('f32', 'f16', 'bf16') for a in args) and \
            not any(self.glm_reaches(strip(a)) for a in args)

    def glm_present(self, node):
        """The presence flag of a helper's pointer-to-glm parameter, or None."""
        n = strip(node)
        if n.get('kind') == 'DeclRefExpr' and n['referencedDecl'].get('id') in self.glm_pointer_params:
            return self.name(self.glm_pointer_params[n['referencedDecl']['id']])
        return None

    def glm_user_element(self, n):
        """A builder for m[i][j] of the source's own glm matrix (indices maybe known only at run time) or v[k] of
        its vector (k constant); None for anything else."""
        if n.get('kind') != 'CXXOperatorCallExpr' or operator_name(n) != 'operator[]' or self.glm_frame() is not None:
            return None
        base, index = n['inner'][1], n['inner'][2]
        inner = strip(base)
        if inner.get('kind') == 'CXXOperatorCallExpr' and operator_name(inner) == 'operator[]':
            target = self.glm_user_target(inner['inner'][1])
            if target is None or target.t.kind != 'mat':
                return None
            column, row, ident, rows = inner['inner'][2], index, target.user[1], target.t.rows
            def build():
                at = self.graph.add('Binary', {'operator': '+'}, {'left': self.graph.add('Binary', {'operator': '*'}, {
                    'left': self.glm_index(column), 'right': self.int_value('u32', rows)}), 'right': self.glm_index(row)})
                return self.graph.add('Index', children={'base': self.name(ident), 'index': at})
            return build
        target = self.glm_user_target(base)
        if target is None or target.t.kind == 'mat':
            return None
        k = self.glm_const(index)
        if k is not None:
            return (lambda: target.get(k).build()) if 0 <= k < target.t.size else None
        # v[k], k known only at run time: glm's operator[] is `switch (k) { default: case 0: return x; case 1: ... }`
        position = self.glm_atom(self.glm_index(index), 'u32', n)
        return lambda: self.glm_select_chain(target, position)

    def glm_select_chain(self, target, position):
        """target[k] for k known only at run time, as glm's switch reads it: k == 1 ? y : (k == 2 ? z : x) and so on,
        x for any k that is not a component (the switch's default)."""
        value = target.get(0).build()
        for c in range(target.t.size - 1, 0, -1):
            same = self.graph.add('Binary', {'operator': '=='}, {'left': position.build(), 'right': self.int_value('u32', c)})
            value = self.graph.add('Select', children={'condition': same, 'yes': target.get(c).build(), 'no': value})
        return value

    def glm_index(self, node):
        k = self.glm_const(node)
        if k is not None:
            return self.int_value('u32', k)
        n = node
        while n.get('kind') == 'ImplicitCastExpr' and n.get('castKind') in ('IntegralCast', 'LValueToRValue', 'NoOp') and \
                scalar_of(n['inner'][0]) in ('u32', 'i32', 'u16', 'i16', 'u8', 'i8'):
            n = n['inner'][0]
        value = self.expr(n)
        return value if scalar_of(n) == 'u32' else self.convert_types(value, scalar_of(n), 'u32', n)

    def glm_user_target(self, node):
        """The source's own glm variable, parameter or pointed-to object an expression names, or None."""
        n = strip(node)
        if n.get('kind') == 'DeclRefExpr':
            return self.glm_user_obj(n['referencedDecl'].get('id'), n)
        if n.get('kind') == 'UnaryOperator' and n['opcode'] == '*':
            inner = strip(n['inner'][0])
            if inner.get('kind') == 'DeclRefExpr' and inner['referencedDecl'].get('id') in self.glm_pointer_params:
                return self.glm_user_obj(inner['referencedDecl']['id'], n)
        return None

    # -- calls --

    def glm_call(self, n):
        """Inline a call of glm code; the result is an Obj, a Field (a T & result), a Pointer, a scalar or None."""
        kind = n['kind']
        callee = self.glm_callee(n)
        args = list(n['inner'][1:])
        if callee is None:
            if operator_name(n) == 'operator=' and kind == 'CXXOperatorCallExpr':  # an implicit copy assignment
                return self.glm_assign(self.glm_obj(args[0]), self.glm_obj(args[1]), n)
            function = self.glm_functor(args[0]) if kind == 'CXXOperatorCallExpr' and operator_name(n) == 'operator()' else None
            if function is not None:  # Func(a.x, b.x) of std::multiplies<T>: a.x * b.x
                operands = args[1:]
                if function.operator == 'neg' and len(operands) == 1:
                    synthetic = {'kind': 'UnaryOperator', 'opcode': '-', 'type': n['type'], 'valueCategory': 'prvalue',
                                 'inner': operands, 'range': n.get('range', {}), 'id': str(n.get('id')) + 'f'}
                elif len(operands) == 2:
                    synthetic = {'kind': 'BinaryOperator', 'opcode': function.operator, 'type': n['type'],
                                 'valueCategory': 'prvalue', 'inner': operands, 'range': n.get('range', {}),
                                 'id': str(n.get('id')) + 'f'}
                else:
                    self.glm_fail(n, 'a function object called with this many arguments')
                return self.glm_scalar(synthetic)
            self.glm_fail(n, f'{operator_name(n) or kind}: not glm code qpel reads')
        name = callee.get('name') or ''
        if name.startswith('make_') and self.glm_frame() is None:
            return self.glm_make(n, args)
        this = None
        if kind == 'CXXMemberCallExpr':
            member = n['inner'][0]
            this = self.glm_pointer(member['inner'][0]) if member.get('isArrow') else self.glm_obj(member['inner'][0])
        elif kind == 'CXXOperatorCallExpr' and callee['kind'] == 'CXXMethodDecl':
            this, args = self.glm_obj(args[0]), args[1:]
        if name == 'operator=' and isinstance(this, Obj) and len(args) == 1 and this.t.same(node_glm(args[0])):
            return self.glm_assign(this, self.glm_obj(args[0]), n)  # a copy (or move) assignment: the components
        return self.glm_inline(callee, this, args, n)

    def glm_functor(self, node):
        n = strip(node)
        frame = self.glm_frame()
        if n.get('kind') == 'DeclRefExpr' and frame is not None and isinstance(frame['vars'].get(n['referencedDecl'].get('id')), Functor):
            return frame['vars'][n['referencedDecl']['id']]
        return None

    def glm_make(self, n, args):
        """glm::make_vec3(p) and friends (glm/gtc/type_ptr.hpp, a memcpy of the object's components from p):
        the components p[0] ... p[size - 1]."""
        t = node_glm(n)
        if t is None or len(args) != 1:
            self.glm_fail(n, 'glm::make_* takes one pointer')
        spelling = {'f32': 'float', 'i32': 'int', 'u32': 'unsigned int'}[t.element]
        cells = []
        for k in range(t.size):
            self.glm_counter += 1
            literal = {'kind': 'IntegerLiteral', 'type': {'qualType': 'int'}, 'valueCategory': 'prvalue', 'value': str(k),
                       'id': f'glm-{self.glm_counter}', 'range': n.get('range', {})}
            element = {'kind': 'ArraySubscriptExpr', 'type': {'qualType': 'const ' + spelling}, 'valueCategory': 'lvalue',
                       'inner': [args[0], literal], 'id': f'glm-{self.glm_counter}e', 'range': n.get('range', {})}
            cells.append(self.glm_atom(self.expr(element), t.element, n))
        return Obj(cells, 0, t.value())

    def glm_bind(self, callee, args, n):
        """{clang id: value} of a glm function's parameters for these arguments, read in the caller's frame."""
        bound = {}
        for k, p in enumerate(params_of(callee)):
            if k < len(args) and args[k].get('kind') != 'CXXDefaultArgExpr':
                a = args[k]
            elif p.get('inner') and p.get('init'):
                a = p['inner'][-1]
            else:
                self.glm_fail(n, f'{callee.get("name")}: argument {k + 1}')
            pt = p.get('type', {})
            t = glm_type(pt)
            spelled = pt.get('desugaredQualType', pt.get('qualType', '')).rstrip()
            if t is not None and t.ref != '*':
                obj = self.glm_obj(a)
                bound[p['id']] = obj if t.ref else obj.copy()
            elif functor_of(p) is not None:
                bound[p['id']] = functor_of(p)
            elif t is not None or spelled.endswith('*'):
                bound[p['id']] = Pointer(self.glm_pointer(a))
            elif spelled.endswith('&') and not re.match(r'(?:const\s+\w[\w:]*|\w[\w:]*\s+const)\s*&$', spelled):
                bound[p['id']] = self.glm_lvalue(a)  # T &: the caller's scalar, written in place
            else:
                bound[p['id']] = Local(self.glm_scalar(a))  # T, or T const &: its value
        return bound

    def glm_inline(self, callee, this, args, n):
        if len(self.glm_frames) > 64:
            self.glm_fail(n, 'glm calls nested more than 64 deep')
        bound = self.glm_bind(callee, args, n)
        result_type = callee.get('type', {}).get('qualType', '').split('(')[0].strip()
        self.glm_frames.append({'vars': bound, 'this': this, 'decl': callee})
        try:
            try:
                self.glm_run(body_of(callee))
                value = None
            except Return as done:
                value = done.args[0]
        finally:
            self.glm_frames.pop()
        if isinstance(value, Obj) and not result_type.endswith('&'):
            return value.copy()
        if isinstance(value, Field) and not result_type.endswith('&'):
            return value.obj.get(value.k)
        if isinstance(value, Local):
            return value.value
        return value

    def glm_construct(self, n):
        t = node_glm(n)
        if t is None:
            self.glm_fail(n, 'a construction of a type that is not glm\'s')
        t = t.value()
        args = [a for a in n.get('inner', ()) if a.get('kind') != 'CXXDefaultArgExpr']
        callee = self.glm_callee(n)
        copying = len(args) == 1 and t.same(node_glm(args[0]))
        if callee is None or copying and len(params_of(callee)) == 1 and \
                params_of(callee)[0].get('type', {}).get('qualType', '').rstrip().endswith('&'):
            if not args:  # a default construction: glm leaves the components undefined (qpel: zero)
                zero = self.glm_atom(self.zero(t.element), t.element, n)
                return Obj([zero] * t.size, 0, t)
            if copying:
                return self.glm_obj(args[0]).copy()  # a copy (or move)
            self.glm_fail(n, f'{t} constructor without glm code')
        obj = Obj([Const(0, t.element)] * t.size, 0, t)
        bound = self.glm_bind(callee, args, n)
        self.glm_frames.append({'vars': bound, 'this': obj, 'decl': callee})
        try:
            for init in [c for c in callee.get('inner', ()) if c.get('kind') == 'CXXCtorInitializer']:
                field = (init.get('anyInit') or {}).get('name')
                value = init['inner'][0] if init.get('inner') else None
                if field in FIELDS and t.kind in ('vec', 'qua'):
                    obj.set(FIELDS[field], self.glm_scalar(value))
                elif field == 'value' and t.kind == 'mat':
                    items = strip(value)
                    if items.get('kind') != 'InitListExpr' or len(items.get('inner', ())) != t.columns:
                        self.glm_fail(init, 'a glm matrix\'s columns are initialized with one list')
                    for c, item in enumerate(items['inner']):
                        column = self.glm_obj(item)
                        for r in range(t.rows):
                            obj.set(c * t.rows + r, column.get(r))
                elif field is None and value is not None and node_glm(value) is not None:
                    other = self.glm_obj(value)  # a delegating constructor: this object is that one
                    for k in range(t.size):
                        obj.set(k, other.get(k))
                else:
                    self.glm_fail(init, f'a glm constructor initializing {field!r}')
            try:
                self.glm_run(body_of(callee))
            except Return:
                pass
        finally:
            self.glm_frames.pop()
        return obj

    def glm_assign(self, target, value, n):
        """target = value, component by component (all read first, so they may overlap)."""
        values = [value.get(k) for k in range(value.t.size)]
        if len(values) != target.t.size:
            self.glm_fail(n, 'a glm assignment between objects of different sizes')
        for k, v in enumerate(values):
            target.set(k, v)
        return target

    # -- statements of glm code --

    def glm_run(self, s):
        kind = s.get('kind')
        if kind == 'CompoundStmt':
            for child in s.get('inner', ()):
                self.glm_run(child)
            return
        if kind in ('NullStmt', 'StaticAssertDecl'):
            return
        if kind == 'DeclStmt':
            for d in s.get('inner', ()):
                if d.get('kind') == 'VarDecl':
                    self.glm_declare(d)
            return
        if kind == 'ReturnStmt':
            if not s.get('inner'):
                raise Return(None)
            value = s['inner'][0]
            result_type = self.glm_frame()['decl'].get('type', {}).get('qualType', '').split('(')[0].strip()
            if node_glm(value) is not None and not result_type.endswith('*'):
                raise Return(self.glm_obj(value))
            if result_type.endswith('&'):
                raise Return(self.glm_lvalue(value))
            if result_type.endswith('*'):
                raise Return(Pointer(self.glm_pointer(value)))
            raise Return(self.glm_scalar(value))
        if kind == 'IfStmt':
            c = self.glm_const(s['inner'][0])
            if c is None:
                self.glm_fail(s, 'a glm branch on a value known only at run time')
            if c:
                self.glm_run(s['inner'][1])
            elif s.get('hasElse'):
                self.glm_run(s['inner'][2])
            return
        if kind == 'SwitchStmt':
            selector = self.glm_const(s['inner'][0])
            if selector is None:
                self.glm_fail(s, 'a glm switch on a value known only at run time')
            flat = []
            def flatten(x):  # (labels nest: `default: case 0: return x;`)
                if x.get('kind') in ('CaseStmt', 'DefaultStmt'):
                    flat.append(('label', self.glm_const(x['inner'][0]) if x['kind'] == 'CaseStmt' else 'default'))
                    flatten(x['inner'][-1])
                else:
                    flat.append(('statement', x))
            for x in s['inner'][-1].get('inner', ()):
                flatten(x)
            start = next((i for i, (k, v) in enumerate(flat) if k == 'label' and v == selector), None)
            if start is None:
                start = next((i for i, (k, v) in enumerate(flat) if k == 'label' and v == 'default'), None)
            if start is None:
                return
            try:
                for k, v in flat[start:]:
                    if k == 'statement':
                        self.glm_run(v)
            except Break:
                pass
            return
        if kind == 'BreakStmt':
            raise Break()
        if kind == 'ForStmt':
            init, _, cond, step, body = s['inner']
            if init:
                self.glm_run(init) if init.get('kind') == 'DeclStmt' else self.glm_effect(init)
            for _ in range(LIMIT):
                if cond:
                    c = self.glm_const(cond)
                    if c is None:
                        self.glm_fail(s, 'a glm loop whose end is known only at run time')
                    if not c:
                        return
                try:
                    self.glm_run(body)
                except Break:
                    return
                if step:
                    self.glm_effect(step)
            self.glm_fail(s, f'a glm loop of more than {LIMIT} iterations')
        if kind == 'AttributedStmt':
            return self.glm_run(s['inner'][-1])
        self.glm_effect(s)

    def glm_declare(self, d):
        frame = self.glm_frame()
        t = glm_type(d.get('type'))
        init = d['inner'][-1] if d.get('inner') and 'init' in d else None
        spelled = d.get('type', {}).get('desugaredQualType', d.get('type', {}).get('qualType', '')).rstrip()
        if t is not None and t.ref == '&':
            frame['vars'][d['id']] = self.glm_obj(init)
        elif t is not None and t.ref is None:
            if init is None:
                frame['vars'][d['id']] = Obj([Const(0, t.element)] * t.size, 0, t.value())
            else:
                frame['vars'][d['id']] = self.glm_obj(init).copy()
        elif spelled.endswith('&'):
            frame['vars'][d['id']] = self.glm_lvalue(init)
        elif spelled.endswith('*') or t is not None:
            frame['vars'][d['id']] = Pointer(self.glm_pointer(init))
        elif init is None:
            self.glm_fail(d, f'glm local {d.get("name")} without a value')
        else:
            frame['vars'][d['id']] = Local(self.glm_scalar(init))

    def glm_effect(self, e):
        n = e
        while n.get('kind') in ('ExprWithCleanups', 'ParenExpr') and n.get('inner'):
            n = n['inner'][0]
        kind = n.get('kind')
        if is_assert(n) or kind == 'CStyleCastExpr' and n.get('castKind') == 'ToVoid':
            return
        if kind == 'BinaryOperator' and n['opcode'] == '=':
            self.glm_store(self.glm_lvalue(n['inner'][0]), n['inner'][1], n)
            return
        if kind == 'BinaryOperator' and n['opcode'] == ',':
            for x in n['inner']:
                self.glm_effect(x)
            return
        if kind == 'CompoundAssignOperator':
            self.glm_store(self.glm_lvalue(n['inner'][0]), self.compound_value(n), n)
            return
        if kind == 'UnaryOperator' and n['opcode'] in ('++', '--'):
            target = self.glm_lvalue(n['inner'][0])
            if isinstance(target, Local) and isinstance(target.value, Const):
                target.value = Const(target.value.value + (1 if n['opcode'] == '++' else -1), target.value.term)
                return
            self.glm_fail(n, f'{n["opcode"]} of a glm value known only at run time')
        if kind in ('CallExpr', 'CXXOperatorCallExpr', 'CXXMemberCallExpr'):
            self.glm_call(n)
            return
        self.glm_fail(n, f'{kind} as a glm statement')

    def compound_value(self, n):
        """The clang expression `a op b` that `a op= b` assigns, converted to a's type."""
        synthetic = {'kind': 'BinaryOperator', 'opcode': n['opcode'][:-1], 'type': n.get('computeResultType', n['type']),
                     'valueCategory': 'prvalue', 'inner': [n['inner'][0], n['inner'][1]], 'range': n.get('range', {}),
                     'id': str(n.get('id', '')) + 'c'}
        if scalar_of(synthetic) != scalar_of(n['inner'][0]):
            synthetic = {'kind': 'ImplicitCastExpr', 'castKind': 'FloatingCast' if scalar_of(n['inner'][0]) in ('f32', 'f16')
                         else 'IntegralCast', 'type': n['inner'][0]['type'], 'valueCategory': 'prvalue',
                         'inner': [synthetic], 'range': n.get('range', {}), 'id': str(n.get('id', '')) + 'v'}
        return synthetic

    def glm_store(self, target, value_node, n):
        if isinstance(target, Obj):
            self.glm_assign(target, self.glm_obj(value_node), n)
        elif isinstance(target, Field):
            term = target.obj.t.element
            value = self.glm_scalar(value_node)
            target.obj.set(target.k, value if value.term == term else self.glm_atom(self.glm_build(value, term, n), term, n))
        elif isinstance(target, Local):
            target.value = self.glm_scalar(value_node)
        else:
            self.glm_fail(n, 'a glm assignment to this target')

    # -- the source's own level --

    @guarded
    def glm_statement(self, node):
        """An expression statement of the source that assigns or updates a glm object (m = a * b, v += w,
        *covar = M, m[i][j] = x): its IR statements, or None when it is not one."""
        n = node
        while n.get('kind') in ('ExprWithCleanups', 'ParenExpr') and n.get('inner'):
            n = n['inner'][0]
        kind = n.get('kind')
        if kind in ('BinaryOperator', 'CompoundAssignOperator') and n.get('opcode') in ('=', '+=', '-=', '*=', '/='):
            target = strip(n['inner'][0])
            element = self.glm_user_element(target) if target.get('kind') == 'CXXOperatorCallExpr' else None
            if element is None:
                return None
            place = element()
            value_node = n['inner'][1] if kind == 'BinaryOperator' else self.compound_value(n)
            term = scalar_of(n['inner'][0])
            value = self.expr(value_node)
            if scalar_of(value_node) != term:
                value = self.convert_types(value, scalar_of(value_node), term, n)
            return [self.graph.add('Assign', children={'target': place, 'value': value})]
        if kind != 'CXXOperatorCallExpr' or len(n.get('inner', ())) != 3:
            return None
        operator = operator_name(n)
        if operator not in ('operator=', 'operator+=', 'operator-=', 'operator*=', 'operator/='):
            return None
        target = self.glm_user_target(n['inner'][1])
        if target is None:
            return None
        if operator == 'operator=':
            if target.t.kind != 'mat' and not self.glm_reaches(strip(n['inner'][2])):
                return None  # (a vector assigned what the importer reads itself)
            self.glm_assign(target, self.glm_obj(n['inner'][2]), n)
        else:
            callee = self.glm_callee(n)
            if callee is None:
                self.glm_fail(n, f'{operator} without glm code')
            self.glm_inline(callee, target, [n['inner'][2]], n)
        return self.glm_write_back(target, n)

    def glm_write_back(self, target, n):
        """Assignments for the components of the source's object that changed; values that read the object
        itself are kept in locals first, so that no component is read after it is written."""
        mode, ident, original = target.user
        changed = [k for k in range(target.t.size) if target.cells[k] is not original[k]]
        overlapping = any(isinstance(target.cells[k], Atom) and ident in target.cells[k].reads for k in changed)
        values = {}
        for k in changed:
            v = target.cells[k]
            ir = self.glm_build(v, target.t.element, n)
            if overlapping and isinstance(v, Atom) and ident in v.reads:
                ir = self.name(self.glm_temp(target.t.element, ir, n))
            values[k] = ir
        statements = []
        for k in changed:
            place = self.graph.add('Index', children={'base': self.name(ident), 'index': self.int_value('u32', k)}) \
                if mode == 'array' else self.graph.add('Member', {'member': VECTOR_FIELDS[k]}, {'base': self.name(ident)})
            statements.append(self.graph.add('Assign', children={'target': place, 'value': values[k]}))
        return statements

    @guarded
    def glm_matrix_local(self, decl, name):
        """A local glm matrix: an array of its components, set from its initializer (column-major)."""
        t = node_glm(decl).value()
        init = decl['inner'][-1] if decl.get('inner') and 'init' in decl else None
        if init is not None:
            core = strip(init)
            if core.get('kind') == 'CXXConstructExpr' and not core.get('inner'):
                init = None  # `mat3 covar;`: default-constructed, which glm leaves undefined
        if init is None:
            values = [self.zero(t.element) for _ in range(t.size)]
            note = f'{self.current}: glm matrix {name} had no initializer; qpel starts it at zero'
            if note not in self.adaptations:
                self.adaptations.append(note)
        else:
            obj = self.glm_obj(init)
            values = [self.glm_build(obj.get(k), t.element, decl) for k in range(t.size)]
        ir = self.graph.add('Variable', {'name': self.unique_name(name)},
                            {'type': self.graph.add('ArrayType', {'element': t.element, 'length': t.size}),
                             'value': self.graph.add('ArrayValue', children={'elements': values})})
        self.scope[decl['id']] = ('local', ir, t.term)
        self.glm_decl_types[decl['id']] = t
        return [ir]

    def glm_matrix_temp(self, obj, node):
        """A local array holding a matrix value, before the statement."""
        if self.prelude is None:
            self.glm_fail(node, 'a glm matrix here would need a local before the statement')
        values = [self.glm_build(obj.get(k), obj.t.element, node) for k in range(obj.t.size)]
        var = self.graph.add('Variable', {'name': self.glm_fresh_name('glm_m')},
                             {'type': self.graph.add('ArrayType', {'element': obj.t.element, 'length': obj.t.size}),
                              'value': self.graph.add('ArrayValue', children={'elements': values})})
        self.prelude.append(var)
        return var

    def glm_signature(self, decl):
        """True when a helper of the source takes or gives glm matrices, or takes pointers to glm objects."""
        result = glm_type({'qualType': decl['type']['qualType'].split('(')[0].strip()})
        return result is not None and result.kind == 'mat' or any(
            p['id'] in self.glm_pointer_params or (glm_type(p.get('type')) or GlmType('vec', 1, 1, 'f32')).kind == 'mat'
            for p in params_of(decl))

    @guarded
    def glm_user_call(self, n):
        """The Obj of a call of the source's own helper whose result is a glm matrix (made before the statement), or
        None when the helper gives something else."""
        decl, ident, _ = self.functions[self.callee(n)]
        if not self.glm_signature(decl):
            return None
        call, obj = self.glm_helper_call(n, decl, ident)
        return obj

    @guarded
    def glm_helper_call(self, n, decl, ident):
        """A call of the source's own helper that takes or gives glm matrices (or pointers to glm objects): each
        matrix argument as an array (a temporary copied first), each pointer with its presence, a matrix result into
        a local the call writes. Returns (the IR call, None), or for a matrix result (None, the Obj of that local),
        the call then made before the statement."""
        args = n['inner'][1:]
        arguments, flags = [], []
        for p, a in zip(params_of(decl), args):
            t = glm_type(p.get('type'))
            if p['id'] in self.glm_pointer_params:
                obj, present = self.glm_pointer_argument(a, t)
                arguments.append(obj)
                flags.append(present)
            elif t is not None and t.kind == 'mat':
                target = self.glm_user_target(a)
                if target is not None:
                    arguments.append(self.name(target.user[1]))
                elif t.ref == '&' and 'const' not in p['type']['qualType']:
                    self.glm_fail(a, 'a glm matrix passed by reference is a variable')
                else:
                    arguments.append(self.name(self.glm_matrix_temp(self.glm_obj(a), a)))
            elif self.local_array(a) is not None and is_array(self.scope.get(local_id(a), (None, None, ''))[2]):
                arguments.append(self.name(self.local_array(a)))
            else:
                arguments.append(self.expr(a, address=True))
        arguments.extend(flags)
        for role, axis in self.builtin_reads(self.callee(n)):
            from .c_import import GROUP_NODES
            arguments.append(self.graph.add(GROUP_NODES[role], {'axis': axis}) if self.kernel
                             else self.name(self.hidden_params[(role, axis)]))
        result = glm_type({'qualType': decl['type']['qualType'].split('(')[0].strip()})
        if result is None or result.kind != 'mat':
            return self.graph.add('Call', children={'arguments': arguments}, links={'function': ident}), None
        result = result.value()
        out = self.glm_matrix_temp(Obj([Atom(lambda: self.zero(result.element), result.element)] * result.size, 0, result), n)
        arguments.insert(len(arguments) - len(self.builtin_reads(self.callee(n))), self.name(out))
        call = self.graph.add('Call', children={'arguments': arguments}, links={'function': ident})
        self.prelude.append(self.graph.add('Evaluate', children={'value': call}))
        cells = [Atom((lambda k: lambda: self.graph.add('Index', children={'base': self.name(out), 'index': self.int_value('u32', k)}))(k),
                      result.element, frozenset({out})) for k in range(result.size)]
        return None, Obj(cells, 0, result, user=('array', out, list(cells)))

    def glm_pointer_argument(self, a, t):
        """(the object, its presence) a pointer-to-glm argument gives: &x, nullptr, c ? &x : nullptr, or the
        caller's own pointer parameter."""
        core = strip(a)
        if is_null(core):
            zero = Obj([Atom(lambda: self.zero(t.element), t.element)] * t.size, 0, t.value())
            dummy = self.glm_matrix_temp(zero, a) if t.kind == 'mat' else self.glm_temp(t.term, self.glm_vector_ir(zero, a), a)
            return self.name(dummy), self.int_value('u32', 0)
        if core.get('kind') == 'UnaryOperator' and core['opcode'] == '&':
            target = self.glm_user_target(core['inner'][0])
            if target is None:
                self.glm_fail(a, 'a pointer to a glm object passed to a helper is &x of a variable')
            return self.name(target.user[1]), self.int_value('u32', 1)
        if core.get('kind') == 'DeclRefExpr' and core['referencedDecl'].get('id') in self.glm_pointer_params:
            target = self.glm_user_obj(core['referencedDecl']['id'], core)
            return self.name(target.user[1]), self.name(self.glm_pointer_params[core['referencedDecl']['id']])
        if core.get('kind') == 'ConditionalOperator':
            from .c_import import predicate
            cond, yes, no = core['inner']
            condition = self.presence(strip(cond)) or self.expr(predicate(cond))  # (covars ? ...: the buffer's presence)
            if is_null(no):
                obj, present = self.glm_pointer_argument(yes, t)
                return obj, self.graph.add('Select', children={'condition': condition, 'yes': present, 'no': self.int_value('u32', 0)})
            if is_null(yes):
                obj, present = self.glm_pointer_argument(no, t)
                return obj, self.graph.add('Select', children={'condition': condition, 'yes': self.int_value('u32', 0), 'no': present})
        self.glm_fail(a, 'a pointer to a glm object passed to a helper is &x, nullptr, c ? &x : nullptr or a pointer parameter')

    @guarded
    def glm_return(self, node):
        """`return m;` in a helper whose result is a glm matrix: its components into the result parameter."""
        obj = self.glm_obj(node['inner'][0])
        statements = []
        for k in range(obj.t.size):
            place = self.graph.add('Index', children={'base': self.name(self.glm_result), 'index': self.int_value('u32', k)})
            statements.append(self.graph.add('Assign', children={'target': place, 'value': self.glm_build(obj.get(k), obj.t.element, node)}))
        return statements + [self.graph.add('Return', children={'value': []})]


def local_id(node):
    n = strip(node)
    return n.get('referencedDecl', {}).get('id') if n.get('kind') == 'DeclRefExpr' else None
