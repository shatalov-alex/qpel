"""Static member functions of a library's structs, read as ordinary functions before import.

pytorch_sparse groups its reductions as static methods of a class template,
`Reducer<scalar_t, REDUCE>::update(&result, val, &arg, c)`. A static method has no object: it is
a function whose name happens to be qualified by a class. Every instantiated class
(ClassTemplateSpecializationDecl, as clang's AST lists them) gives its static methods with a body
to the unit as FunctionDecls of their own, named after the class, its template arguments and the
method (`Reducer_f32_0_update`), and every reference to such a method refers to that function.
Everything after this (reachability, instances, imports, the originals the simulator calls) then
sees ordinary functions. Methods of the facade namespaces (facades.py) and of the standard
library (std::numeric_limits, read as constants by the importer) are left as they are.

`__ldg(p)`, CUDA's load through the read-only data cache, reads the value at p and is read as `*p`.
"""
import re

SKIP_NAMESPACES = {'std', 'cub', 'cuda', 'thrust'}
CONTAINERS = {'NamespaceDecl', 'LinkageSpecDecl', 'ClassTemplateDecl'}
SCALAR_NAMES = {'float': 'f32', 'double': 'f64', 'int': 'i32', 'unsigned int': 'u32', 'long': 'i64',
                'long long': 'i64', 'unsigned long': 'u64', 'unsigned long long': 'u64', 'short': 'i16',
                'unsigned short': 'u16', 'signed char': 'i8', 'unsigned char': 'u8', 'char': 'i8', 'bool': 'bool',
                'int64_t': 'i64', 'uint64_t': 'u64', 'int32_t': 'i32', 'uint32_t': 'u32', 'at::Half': 'f16',
                'c10::Half': 'f16', '__half': 'f16', 'half': 'f16'}


def walk(node):
    yield node
    for child in node.get('inner', ()) or ():
        if isinstance(child, dict):
            yield from walk(child)


def arguments(record):
    """The template arguments of a class specialization, as name parts."""
    out = []
    for c in record.get('inner', ()):
        if c.get('kind') != 'TemplateArgument':
            continue
        for part in (c.get('inner', ()) if c.get('isPack') else [c]):
            if 'type' in part:
                out.append(SCALAR_NAMES.get(part['type'].get('qualType', '').replace('const ', '').strip(),
                                            part['type'].get('qualType', '')))
            elif 'value' in part:
                out.append(str(part['value']).replace('-', 'm'))
            elif 'decl' in part:
                out.append(part['decl'].get('name', 'x'))
    return out


def has_body(method):
    return any(c.get('kind') == 'CompoundStmt' for c in method.get('inner', ()))


def static_methods(unit):
    """[(index in unit['inner'], record, [static methods with bodies])] of the library's own
    class specializations (and plain structs) outside the skipped namespaces."""
    found = []

    def visit(node, top, skip, pattern=False):  # pattern: the record a ClassTemplateDecl holds (dependent types)
        kind = node.get('kind')
        if kind == 'NamespaceDecl' and node.get('name') in SKIP_NAMESPACES:
            skip = True
        if (kind == 'ClassTemplateSpecializationDecl' or kind == 'CXXRecordDecl' and not pattern) and not skip \
                and not node.get('isImplicit'):
            methods = [m for m in node.get('inner', ()) if m.get('kind') == 'CXXMethodDecl'
                       and m.get('storageClass') == 'static' and has_body(m)]
            if methods:
                found.append((top, node, methods))
        if kind in CONTAINERS:
            for child in node.get('inner', ()):
                if isinstance(child, dict):
                    visit(child, top, skip, kind == 'ClassTemplateDecl')
    for i, node in enumerate(unit.get('inner', ())):
        visit(node, i, False)
    return found


def lift_statics(unit):
    """Rewrite unit in place, once (clang_ast caches units); return {new function name: qualified
    C++ name} of the methods lifted."""
    if unit.get('_qpel_statics') is not None:
        return unit['_qpel_statics']
    lifted = {}
    renamed = {}  # clang id of a method -> its FunctionDecl
    inserts = []
    taken = set()
    for top, record, methods in static_methods(unit):
        args = arguments(record)
        base = re.sub(r'_+', '_', '_'.join([record.get('name', 'S')] + [re.sub(r'[^A-Za-z0-9]+', '_', a) for a in args]))
        for method in methods:
            name = f'{base}_{method["name"]}'
            while name in taken:
                name += '_'
            taken.add(name)
            function = dict(method, kind='FunctionDecl', name=name, _static_of=record.get('name'))
            function.pop('storageClass', None)
            renamed[method['id']] = function
            inserts.append((top, function))
            spelled = ', '.join(a for a in record_arguments_spelled(record))
            lifted[name] = f'{record.get("name")}<{spelled}>::{method["name"]}' if spelled else \
                f'{record.get("name")}::{method["name"]}'
    for top, function in sorted(inserts, key=lambda x: -x[0]):
        unit['inner'].insert(top + 1, function)
    if renamed:
        for node in walk(unit):
            if node.get('kind') == 'DeclRefExpr' and node.get('referencedDecl', {}).get('id') in renamed:
                function = renamed[node['referencedDecl']['id']]
                node['referencedDecl'] = dict(node['referencedDecl'], kind='FunctionDecl', name=function['name'])
    for node in walk(unit):  # __ldg(p) -> *p
        if node.get('kind') == 'CallExpr' and node.get('inner') and callee_name(node) in ('__ldg', '__ldcs') and len(node['inner']) == 2:
            pointer = node['inner'][1]
            node.clear()
            node.update({'kind': 'UnaryOperator', 'opcode': '*', 'type': dict(pointer.get('type', {})),
                         'valueCategory': 'prvalue', 'inner': [pointer], 'range': pointer.get('range'), '_ldg': True})
            pointee = pointer.get('type', {}).get('qualType', '')
            node['type'] = {'qualType': re.sub(r'\s*\*\s*$', '', pointee).replace('const ', '').strip()}
            desugared = pointer.get('type', {}).get('desugaredQualType')
            if desugared:
                node['type']['desugaredQualType'] = re.sub(r'\s*\*\s*$', '', desugared).replace('const ', '').strip()
            unit['_qpel_ldg'] = True
    unit['_qpel_statics'] = lifted
    return lifted


def record_arguments_spelled(record):
    out = []
    for c in record.get('inner', ()):
        if c.get('kind') != 'TemplateArgument':
            continue
        for part in (c.get('inner', ()) if c.get('isPack') else [c]):
            if 'type' in part:
                out.append(part['type'].get('qualType', ''))
            elif 'value' in part:
                out.append(str(part['value']))
    return out


def callee_name(call):
    target = call['inner'][0]
    while target.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and target.get('inner'):
        target = target['inner'][0]
    return target.get('referencedDecl', {}).get('name') if target.get('kind') == 'DeclRefExpr' else None


def enum_names(unit):
    """{enum name: its name qualified by its namespaces}."""
    found = {}

    def visit(node, scope):
        for child in node.get('inner', ()):
            if child.get('kind') == 'EnumDecl' and child.get('name'):
                found.setdefault(child['name'], scope + child['name'])
            elif child.get('kind') == 'NamespaceDecl':
                visit(child, scope + (child.get('name') or '') + '::' if child.get('name') else scope)
            elif child.get('kind') in ('LinkageSpecDecl',):
                visit(child, scope)
    visit(unit, '')
    return found


def enum_values(unit):
    """{clang id of an enumerator: its value} and {enum name: underlying type} of the unit's enums."""
    values, types = {}, {}
    for node in walk(unit):
        if node.get('kind') != 'EnumDecl':
            continue
        if node.get('name'):
            types[node['name']] = node.get('fixedUnderlyingType', {}).get('qualType', 'int')
        last = -1
        for constant in node.get('inner', ()):
            if constant.get('kind') != 'EnumConstantDecl':
                continue
            if constant.get('inner'):
                # an initializer: clang gives its value on the ConstantExpr around it
                init = constant['inner'][0]
                while init.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and init.get('inner'):
                    init = init['inner'][0]
                evaluated = init.get('value') if init.get('kind') == 'ConstantExpr' else None
                evaluated = {'true': '1', 'false': '0'}.get(evaluated, evaluated)
                if evaluated is None or not re.fullmatch(r'-?[0-9]+', str(evaluated)):
                    last = None  # an enumerator qpel cannot evaluate: left as a name the importer reports,
                    continue     # and so are the ones after it, which count from it
                last = int(evaluated)
            elif last is None:
                continue
            else:
                last += 1
            values[constant['id']] = last
    return values, types


def read_enums(unit):
    """Enumerators as integer literals of the enum's underlying type, and enum-typed expressions as
    that type (an unscoped enum is its underlying integer in every C++ expression qpel reads)."""
    if unit.get('_qpel_enums'):
        return
    values, types = enum_values(unit)
    qualified = enum_names(unit)
    for node in walk(unit):
        if node.get('kind') == 'DeclRefExpr' and node.get('referencedDecl', {}).get('id') in values:
            value = values[node['referencedDecl']['id']]
            name = node['referencedDecl'].get('name')
            node.clear()
            node.update({'kind': 'IntegerLiteral', 'type': {'qualType': 'int'}, 'valueCategory': 'prvalue',
                         'value': str(value), '_enumerator': name})
        t = node.get('type')
        if isinstance(t, dict) and 'desugaredQualType' not in t:
            spelled = t.get('qualType', '').replace('const ', '').strip()
            if spelled in types:
                t['desugaredQualType'] = types[spelled]
                t['_qpel_enum'] = qualified.get(spelled, spelled)
        elif isinstance(t, dict):  # `const gsplat::CameraModelType`: the enum, spelled with its namespace
            full = t['desugaredQualType'].replace('const ', '').strip()
            spelled = full.rpartition('::')[2]
            if spelled in types and full.rpartition('::')[0].replace('::', '').isidentifier():
                t['desugaredQualType'] = types[spelled]
                t['_qpel_enum'] = full  # (what a call of the original passes: static_cast<gsplat::CameraModelType>(v))
    unit['_qpel_enums'] = True


ALIAS_KINDS = ('TypeAliasDecl', 'TypedefDecl')
NAME = re.compile(r'\b([A-Za-z_][\w:]*)<')


def _enumerators(unit):
    """{qualified enum name: {value: qualified enumerator}}, `cub::BlockScanAlgorithm`: {2: 'cub::BLOCK_SCAN_WARP_SCANS'}
    (an unscoped enum's enumerators are named in the enclosing namespace)."""
    out = {}

    def visit(node, scope):
        kind = node.get('kind')
        if kind == 'NamespaceDecl':
            scope = scope + [node.get('name', '')]
        if kind == 'EnumDecl' and node.get('name'):
            prefix = '::'.join(scope + ([node['name']] if node.get('scopedEnumTag') else []))
            values, last = {}, -1
            for c in node.get('inner', ()):
                if c.get('kind') != 'EnumConstantDecl':
                    continue
                given = _literal(c['inner'][0]) if c.get('inner') else None
                last = given if given is not None else last + 1
                values.setdefault(last, (prefix + '::' if prefix else '') + c['name'])
            out['::'.join(scope + [node['name']])] = values
            out.setdefault(node['name'], values)  # as a template parameter inside its namespace spells it
            return
        for c in node.get('inner', ()) or ():
            if isinstance(c, dict) and c.get('kind') in ('NamespaceDecl', 'EnumDecl', 'LinkageSpecDecl'):
                visit(c, scope)
    visit(unit, [])
    return out


def _template_parameters(unit):
    """{class template name: [parameter kind: 'type' | 'bool' | 'value' | {enum value: enumerator}]}."""
    out, enums = {}, _enumerators(unit)
    for node in walk(unit):
        if node.get('kind') == 'ClassTemplateDecl' and node.get('name'):
            kinds = []
            for c in node.get('inner', ()):
                if c.get('kind') == 'TemplateTypeParmDecl':
                    kinds.append('type')
                elif c.get('kind') == 'NonTypeTemplateParmDecl':
                    t = c.get('type', {}).get('qualType', '')
                    kinds.append('bool' if t == 'bool' else enums.get(t, enums.get(t.split('::')[-1], 'value'))
                                 if t not in ('int', 'unsigned int', 'size_t', 'long') else 'value')
            out.setdefault(node['name'], kinds)
    return out


def _specialization_spelling(record, kinds):
    """`Traits<32, 4, true, float>` as clang spells the specialization in types (bools are -1 and 0
    in the JSON's template arguments)."""
    parts = []
    for i, c in enumerate(c for c in record.get('inner', ()) if c.get('kind') == 'TemplateArgument'):
        if c.get('isPack') or not ('type' in c or 'value' in c):
            return None
        if 'type' in c:
            parts.append(c['type'].get('qualType', ''))
        elif i < len(kinds) and kinds[i] == 'bool':
            parts.append('true' if c['value'] else 'false')
        elif i < len(kinds) and isinstance(kinds[i], dict) and c['value'] in kinds[i]:
            parts.append(kinds[i][c['value']])  # an enum: by its enumerator, as types spell it
        else:
            parts.append(str(c['value']))
    return f'{record["name"]}<{", ".join(parts)}>'


def _aliases(scope):
    """{alias name: the type it names} of the type aliases directly in a record or a function body."""
    out = {}
    for c in scope.get('inner', ()):
        decls = c.get('inner', ()) if c.get('kind') == 'DeclStmt' else [c]
        for d in decls:
            if d.get('kind') in ALIAS_KINDS and d.get('name') and 'type' in d:
                out[d['name']] = d['type'].get('desugaredQualType', d['type'].get('qualType', ''))
    return out


def _closing(text, start):
    """Index of the `>` that closes the `<` at text[start]."""
    depth = 0
    for i in range(start, len(text)):
        depth += {'<': 1, '>': -1}.get(text[i], 0)
        if depth == 0:
            return i
    return -1


def _specialization(members, key):
    """The alias table of the class specialization a type spells `cub::BlockLoad<uint4, 32, 1>`: by its
    name with or without the namespace, and, where the spelling leaves default arguments out, the one
    specialization whose arguments begin with those given."""
    if key in members:
        return members[key]
    name, _, rest = key.partition('<')
    short = name.split('::')[-1] + '<' + rest
    if short in members:
        return members[short]
    found = [k for k in members if k.startswith(short[:-1] + ', ')]
    return members[found[0]] if len(found) == 1 else None


def resolve_aliases(text, members, local):
    """A type spelling with the aliases of class specializations (`typename Traits<32, float>::input_t`)
    and of the enclosing function (`input_t`) replaced by the types they name, until none is left."""
    for _ in range(8):
        new, at = [], 0
        for m in NAME.finditer(text):
            if m.start() < at:
                continue
            end = _closing(text, m.end() - 1)
            if end < 0:
                break
            key = text[m.start():end + 1].replace('typename ', '')
            member = re.match(r'::(\w+)', text[end + 1:])
            table = _specialization(members, key) if member else None
            if table and member.group(1) in table:
                begin = m.start()
                if text[max(0, begin - 9):begin] == 'typename ':
                    begin -= 9
                new.append(text[at:begin] + table[member.group(1)])
                at = end + 1 + member.end()
        new.append(text[at:])
        out = ''.join(new)
        if local:
            out = re.sub(r'(?<![\w:])(' + '|'.join(map(re.escape, local)) + r')(?![\w<])',
                         lambda m: local[m.group(1)], out)
        if out == text:
            return out
        text = out
    return text


def alias_templates(unit):
    """{alias template name: (class template, member)} of `template <class T> using a = typename C<T>::m;`
    (ATen's `opmath_type`), whose instances desugar_aliases reads from C's specializations."""
    out = {}
    for node in walk(unit):
        if node.get('kind') != 'TypeAliasTemplateDecl':
            continue
        params = [c.get('name') for c in node.get('inner', ()) if c.get('kind') == 'TemplateTypeParmDecl']
        alias = next((c for c in node.get('inner', ()) if c.get('kind') == 'TypeAliasDecl'), None)
        if len(params) != 1 or alias is None:
            continue
        found = re.fullmatch(r'typename\s+(\w+)<\s*' + re.escape(params[0]) + r'\s*>::(\w+)', alias['type'].get('qualType', ''))
        if found:
            out[node['name']] = (found.group(1), found.group(2))
    return out


def desugar_aliases(unit):
    """Types that clang spells through type aliases give their desugared spelling only at the top
    (`scan_t` names float2, but `scan_t *` and `scan_t[16]` say nothing more), so in an instance of
    a template over a traits class (`typename Ktraits::input_t *`, mamba's kernels) qpel would not
    know the types. Every type in a function instance that names an alias of a class specialization
    or of the function's own body gets desugaredQualType: the spelling with the aliases replaced."""
    if unit.get('_qpel_aliases'):
        return
    kinds = _template_parameters(unit)
    members = {}
    for node in walk(unit):
        if node.get('kind') == 'ClassTemplateSpecializationDecl' and node.get('inner'):
            spelled = _specialization_spelling(node, kinds.get(node.get('name'), []))
            if spelled:
                found = _aliases(node)
                if found:
                    members.setdefault(spelled, {}).update(found)
    unit['_qpel_alias_members'] = members  # (functors.py resolves the aliases of lifted methods with it)
    templates = alias_templates(unit)
    if templates:  # at::opmath_type<__half>, `template <class T> using opmath_type = typename OpMathType<T>::type`
        pattern = re.compile(r'(?<![\w])(?:[\w]+::)*(' + '|'.join(map(re.escape, templates)) + r')<([^<>]+)>')

        def instance(found):
            record, member = templates[found.group(1)]
            argument = found.group(2).strip()
            value = members.get(f'{record}<{argument}>', {}).get(member)
            return value if value else found.group(0)
        for node in walk(unit):
            t = node.get('type')
            if isinstance(t, dict) and any(name in t.get('qualType', '') for name in templates):
                spelled = t.get('desugaredQualType', t['qualType'])
                resolved = pattern.sub(instance, spelled)
                if resolved != spelled:
                    t['desugaredQualType'] = resolved
    if members:
        for function in walk(unit):
            if function.get('kind') != 'FunctionDecl':
                continue
            body = _body(function)
            local = _aliases(body) if body else {}
            named = re.compile(r'(?<![\w:])(' + '|'.join(map(re.escape, local)) + r')(?![\w<])') if local else None
            for node in walk(function):
                t = node.get('type')
                if node is function or not isinstance(t, dict):
                    continue
                spelled = t.get('desugaredQualType', t.get('qualType', ''))
                if '::' not in spelled and not (named and named.search(spelled)):
                    continue
                resolved = resolve_aliases(spelled, members, local)
                if resolved != spelled:
                    t['desugaredQualType'] = resolved
    unit['_qpel_aliases'] = True


SIZES = {'char': 1, 'signed char': 1, 'unsigned char': 1, 'bool': 1, 'int8_t': 1, 'uint8_t': 1, 'short': 2,
         'unsigned short': 2, 'int16_t': 2, 'uint16_t': 2, '__half': 2, 'half': 2, 'at::Half': 2, 'c10::Half': 2,
         '__nv_bfloat16': 2, 'at::BFloat16': 2, 'c10::BFloat16': 2, 'int': 4, 'unsigned int': 4, 'float': 4,
         'int32_t': 4, 'uint32_t': 4, 'long': 8, 'unsigned long': 8, 'long long': 8, 'unsigned long long': 8,
         'int64_t': 8, 'uint64_t': 8, 'double': 8, 'float2': 8, 'int2': 8, 'uint2': 8, 'float4': 16, 'int4': 16,
         'uint4': 16, 'ushort4': 8, 'short4': 8, 'half2': 4, '__half2': 4, 'ushort2': 4, 'short2': 4,
         'c10::complex<float>': 8, 'complex<float>': 8}  # sizeof, for constants (constant_values)
PASS = ('ImplicitCastExpr', 'ParenExpr', 'ConstantExpr', 'ExprWithCleanups', 'MaterializeTemporaryExpr',
        'CXXFunctionalCastExpr', 'CStyleCastExpr', 'CXXStaticCastExpr', 'SubstNonTypeTemplateParmExpr')
BINARY = {'+': lambda a, b: a + b, '-': lambda a, b: a - b, '*': lambda a, b: a * b,
          '/': lambda a, b: int(a / b) if b else None, '%': lambda a, b: a - b * int(a / b) if b else None,
          '<<': lambda a, b: a << b, '>>': lambda a, b: a >> b, '&': lambda a, b: a & b, '|': lambda a, b: a | b,
          '^': lambda a, b: a ^ b, '<': lambda a, b: a < b, '>': lambda a, b: a > b, '<=': lambda a, b: a <= b,
          '>=': lambda a, b: a >= b, '==': lambda a, b: a == b, '!=': lambda a, b: a != b,
          '&&': lambda a, b: bool(a) and bool(b), '||': lambda a, b: bool(a) or bool(b)}


def constant_values(unit):
    """{VarDecl id: int or bool} of the static constexpr members of class specializations (a traits
    class's `kNItems`, `kIsComplex = std::is_same_v<weight_t, complex_t>`), which clang's JSON gives
    only as expressions: literals, operators, other such constants, `std::is_same_v` and calls of
    constexpr functions whose body returns one expression (`constexpr_min`, `std::min`). A member
    whose value needs more (sizeof, initializer lists) is left out."""
    if '_qpel_constant_values' in unit:
        return unit['_qpel_constant_values']
    decls, functions = {}, {}
    for node in walk(unit):
        kind = node.get('kind')
        if kind in ('VarDecl', 'VarTemplateSpecializationDecl') and node.get('constexpr') and node.get('id'):
            if len(node.get('inner', ())) >= len(decls.get(node['id'], {}).get('inner', ())):
                decls[node['id']] = node  # clang lists some declarations twice, once without their initializer
        elif kind == 'FunctionDecl' and node.get('constexpr') and _body(node) is not None:
            functions[node['id']] = node
    memo, busy = {}, set()
    for trait in unit.get('_system_traits', ()):  # std::is_same_v<A, B> instances (clang_ast.system_traits)
        if trait['name'] == 'is_same_v' and len(trait['arguments']) == 2:
            a, b = (t.replace('const ', '').strip() for t in trait['arguments'])
            memo.setdefault(trait['id'], a == b)

    def type_of(arg):
        t = arg.get('type', {})
        return t.get('desugaredQualType', t.get('qualType', '')).replace('const ', '').strip()

    def decl_value(decl):
        if decl['id'] in memo:
            return memo[decl['id']]
        if decl['id'] in busy:
            return None
        busy.add(decl['id'])
        value = None
        if decl.get('kind') == 'VarTemplateSpecializationDecl' and decl.get('name') == 'is_same_v':
            args = [c for c in decl.get('inner', ()) if c.get('kind') == 'TemplateArgument' and 'type' in c]
            if len(args) == 2:
                value = type_of(args[0]) == type_of(args[1])
        else:
            init = [c for c in decl.get('inner', ()) if c.get('kind') not in ('TemplateArgument',)]
            if init:
                value = ev(init[-1], {})
        if isinstance(value, bool) or isinstance(value, int):
            if 'bool' in decl.get('type', {}).get('qualType', '').split():
                value = bool(value)
        busy.discard(decl['id'])
        memo[decl['id']] = value
        return value

    def ev(n, env):
        kind = n.get('kind')
        inner = [c for c in n.get('inner', ()) if isinstance(c, dict)]
        if kind in PASS and inner:
            value = ev(inner[-1], env)
            if value is not None and n.get('type', {}).get('qualType') == 'bool':
                return bool(value)
            return value
        if kind == 'IntegerLiteral':
            return int(n['value'])
        if kind == 'UnaryExprOrTypeTraitExpr' and n.get('name') == 'sizeof' and 'argType' in n:
            return SIZES.get(type_of({'type': n['argType']}))
        if kind == 'CXXBoolLiteralExpr':
            return bool(n['value'])
        if kind == 'UnaryOperator' and inner:
            value = ev(inner[0], env)
            if value is None:
                return None
            return {'!': lambda v: not v, '-': lambda v: -v, '+': lambda v: v, '~': lambda v: ~v}.get(
                n.get('opcode'), lambda v: None)(value)
        if kind == 'BinaryOperator' and len(inner) == 2 and n.get('opcode') in BINARY:
            a, b = ev(inner[0], env), ev(inner[1], env)
            return None if a is None or b is None else BINARY[n['opcode']](a, b)
        if kind == 'ConditionalOperator' and len(inner) == 3:
            c = ev(inner[0], env)
            return None if c is None else ev(inner[1] if c else inner[2], env)
        if kind == 'DeclRefExpr':
            ref = n.get('referencedDecl', {})
            if ref.get('id') in env:
                return env[ref['id']]
            if ref.get('id') in decls:
                return decl_value(decls[ref['id']])
            return memo.get(ref.get('id'))  # a system trait
        if kind == 'CallExpr' and inner:
            callee = _peel(inner[0])
            ref = callee.get('referencedDecl', {}) if callee.get('kind') == 'DeclRefExpr' else {}
            args = [ev(a, env) for a in inner[1:]]
            if any(a is None for a in args):
                return None
            if ref.get('name') in ('min', 'max') and len(args) == 2:  # std::min, std::max
                return (min if ref['name'] == 'min' else max)(args)
            function = functions.get(ref.get('id'))
            body = _body(function) if function else None
            params = [c for c in function.get('inner', ()) if c.get('kind') == 'ParmVarDecl'] if function else []
            statements = body.get('inner', ()) if body else ()
            if len(statements) == 1 and statements[0].get('kind') == 'ReturnStmt' and len(params) == len(args) \
                    and statements[0].get('inner'):
                return ev(statements[0]['inner'][0], {p['id']: a for p, a in zip(params, args)})
        return None

    values = {}
    for decl_id, decl in decls.items():
        value = decl_value(decl)
        if value is not None:
            values[decl_id] = value
    for trait_id, value in memo.items():  # std::is_same_v<A, B> instances read in device code
        if value is not None:
            values.setdefault(trait_id, value)
    unit['_qpel_constant_values'] = values
    return values


CASTS = ('CXXReinterpretCastExpr', 'CStyleCastExpr', 'CXXStaticCastExpr')


def _spelling(t):
    return t.get('desugaredQualType', t.get('qualType', ''))


def _plain(spelled):
    return ' '.join(w for w in spelled.replace('*', ' * ').split() if w not in ('__restrict__', '__restrict', 'restrict', 'struct'))


def flatten_argument_structs(unit):
    """A kernel argument that is a struct holding pointers (mamba's `SSMParamsBase params`, with
    `void *u_ptr` and strides) becomes one buffer argument per pointer field the kernel reads,
    `params_u_ptr`, and one struct value `params` of the scalar fields it reads (`SSMParamsBase_values`;
    one argument rather than twenty: Metal has 31 buffer slots): `params.u_ptr` reads the buffer and
    `params.seqlen` the value's member. qpel buffers are kernel arguments, so this is
    how a struct of pointers maps onto qpel; the originals are still called with the struct (the
    harness rebuilds it field by field from the argument map, `source: 'struct'`). A `void *` field
    takes the type every use casts it to (`reinterpret_cast<float *>(params.u_ptr)`); the cast is
    then of a float* to float* and is read as the argument itself. A struct that is used otherwise
    than through its fields (passed on whole) is left as it is."""
    if unit.get('_qpel_flattened') is not None:
        return unit['_qpel_flattened']
    records = {}
    for node in walk(unit):
        if node.get('kind') == 'CXXRecordDecl' and node.get('completeDefinition') and node.get('name'):
            fields = [c for c in node.get('inner', ()) if c.get('kind') == 'FieldDecl']
            if fields:
                records.setdefault(node['name'], fields)
    flattened, value_records = {}, {}
    for function in list(walk(unit)):
        if function.get('kind') != 'FunctionDecl' or not any(a.get('kind') == 'CUDAGlobalAttr' for a in function.get('inner', ())):
            continue
        body = _body(function)
        if body is None:
            continue
        for p in [c for c in function['inner'] if c.get('kind') == 'ParmVarDecl']:
            struct = _plain(_spelling(p.get('type', {})))
            fields = {f.get('name'): f for f in records.get(struct, ())}
            if not fields or not any('*' in _spelling(f['type']) for f in fields.values()):
                continue
            members, references = [], 0
            for node in walk(body):
                if node.get('kind') == 'DeclRefExpr' and node.get('referencedDecl', {}).get('id') == p['id']:
                    references += 1
                elif node.get('kind') == 'MemberExpr' and node.get('inner') and \
                        _peel(node['inner'][0]).get('kind') == 'DeclRefExpr' and \
                        _peel(node['inner'][0]).get('referencedDecl', {}).get('id') == p['id'] and not node.get('isArrow'):
                    members.append(node)
            if not members or len(members) != references or any(m.get('name') not in fields for m in members):
                continue
            casts = {}  # field -> {the pointer types its uses are cast to}
            for node in walk(body):
                if node.get('kind') in CASTS and node.get('inner'):
                    inner = _peel(node['inner'][0])
                    if inner.get('kind') == 'MemberExpr' and any(inner is m for m in members):
                        casts.setdefault(inner['name'], set()).add(_plain(_spelling(node['type'])))
            used = [name for name in fields if any(m['name'] == name for m in members)]
            pointers = [name for name in used if '*' in _spelling(fields[name]['type'])]
            scalars = [name for name in used if name not in pointers]
            synthetic = {}
            for name in pointers:
                t = dict(fields[name]['type'])
                if _plain(_spelling(t)).replace(' ', '') in ('void*', 'constvoid*'):
                    if len(casts.get(name, ())) != 1:
                        break  # cast to several types, or never: the struct stays (the importer reports it)
                    t = {'qualType': next(iter(casts[name]))}
                elif '*' in _spelling(t):
                    t = {'qualType': _plain(_spelling(t))}
                synthetic[name] = {'id': f'{p["id"]}.{name}', 'kind': 'ParmVarDecl', 'name': f'{p["name"]}_{name}',
                                   'type': t, 'loc': p.get('loc'), 'range': p.get('range'),
                                   '_qpel_field_of': p['name'], '_qpel_field': name, '_qpel_struct': struct}
            else:
                for m in members:
                    if m['name'] not in synthetic:
                        continue  # a scalar field: a member of the struct value
                    target = synthetic[m['name']]
                    reference = {'kind': 'DeclRefExpr', 'id': m.get('id'), 'range': m.get('range'),
                                 'type': dict(target['type']), 'valueCategory': 'lvalue',
                                 'referencedDecl': {'id': target['id'], 'kind': 'ParmVarDecl', 'name': target['name'],
                                                    'type': dict(target['type'])}}
                    m.clear()
                    m.update(reference)
                ids = {t['id']: t for t in synthetic.values()}
                for node in walk(body):  # casts of a field to its own type: the argument itself
                    if node.get('kind') in CASTS and node.get('inner'):
                        inner = _peel(node['inner'][0])
                        target = ids.get(inner.get('referencedDecl', {}).get('id')) if inner.get('kind') == 'DeclRefExpr' else None
                        if target and _plain(_spelling(node['type'])) == _plain(_spelling(target['type'])):
                            node.clear()
                            node.update(dict(inner, valueCategory='prvalue'))
                values = []
                if scalars:  # the scalar fields read travel as one struct value (Metal has 31 buffer slots)
                    key = (struct, tuple(scalars))
                    if key not in value_records:
                        name = f'{struct}_values' + (f'_{len(value_records) + 1}' if value_records else '')
                        record = {'id': f'{p["id"]}.values', 'kind': 'CXXRecordDecl', 'name': name, 'tagUsed': 'struct',
                                  'completeDefinition': True, 'loc': p.get('loc'), 'range': p.get('range'),
                                  'inner': [{'id': f'{p["id"]}.values.{f}', 'kind': 'FieldDecl', 'name': f,
                                             'type': {'qualType': _plain(_spelling(fields[f]['type'])).replace('const ', '')}}
                                            for f in scalars]}
                        value_records[key] = record
                        unit['inner'].insert(0, record)
                    p['type'] = {'qualType': value_records[key]['name']}
                    p['_qpel_values_of'], p['_qpel_value_fields'], p['_qpel_struct'] = p['name'], scalars, struct
                    values = [p]
                at = function['inner'].index(p)
                function['inner'][at:at + 1] = values + [synthetic[name] for name in pointers]
                flattened.setdefault(function.get('name'), {})[p['name']] = {
                    'struct': struct, 'fields': [(name, synthetic[name]['name'] if name in synthetic else p['name'])
                                                 for name in used]}
    unit['_qpel_flattened'] = flattened
    return flattened


def instances_to_definitions(unit):
    """clang lists a function template's instantiations under its first declaration. When that is a
    prototype in a header (bitsandbytes' kernels.cuh) and the definition is in the source (kernels.cu,
    which instantiates them explicitly), the instantiations, with their bodies, move to the
    definition's FunctionTemplateDecl, so they are the source's own kernels."""
    if unit.get('_qpel_instances_moved'):
        return
    def pattern(template):
        return next((c for c in template.get('inner', ()) if c.get('kind') == 'FunctionDecl'), None)
    templates = [n for n in walk(unit) if n.get('kind') == 'FunctionTemplateDecl' and pattern(n) is not None]
    defined = {}
    for t in templates:
        p = pattern(t)
        if _body(p) is not None:
            defined.setdefault((t.get('name'), p.get('type', {}).get('qualType')), t)
    for t in templates:
        p = pattern(t)
        if _body(p) is not None:
            continue
        target = defined.get((t.get('name'), p.get('type', {}).get('qualType')))
        if target is None:
            continue
        moved = [c for c in t['inner'][1:] if c.get('kind') == 'FunctionDecl' and _body(c) is not None]
        if not moved:
            continue
        ids = {c['id'] for c in moved}
        t['inner'] = [c for c in t['inner'] if c.get('id') not in ids]
        target['inner'] = [c for c in target['inner'] if not (c.get('kind') == 'FunctionDecl' and c.get('id') in ids)] + moved
    unit['_qpel_instances_moved'] = True


def _switch_groups(body):
    """[(labels, statements)] of a switch body, labels a list of values and 'default', each group
    ending at its `break` (left out), a `return` or `continue`, or the end; None when a group falls
    through into the next one or a `break` is nested inside it (qpel has no switch: it reads one
    whose cases do not fall through as an if-else chain)."""
    groups, labels, statements = [], [], []

    def label_of(node):  # CaseStmt [value, statement] nests for `case 1: case 2:`; DefaultStmt [statement]
        found = []
        while node.get('kind') in ('CaseStmt', 'DefaultStmt'):
            if node['kind'] == 'CaseStmt':
                value = node['inner'][0]
                if value.get('kind') != 'ConstantExpr' or 'value' not in value:
                    return None, None
                found.append(int(value['value']))
            else:
                found.append('default')
            node = node['inner'][-1] if node.get('inner') and (node['kind'] == 'DefaultStmt' or len(node['inner']) > 1) else {}
        return found, node

    def close():
        if labels:
            groups.append((list(labels), list(statements)))
        labels.clear()
        statements.clear()

    for child in body.get('inner', ()):
        if child.get('kind') in ('CaseStmt', 'DefaultStmt'):
            if labels and statements and statements[-1].get('kind') not in ('ReturnStmt', 'ContinueStmt'):
                return None  # falls through into this case
            if labels and not statements:
                pass  # `case 1:` directly followed by `case 2:` in clang's flat form
            elif labels:
                close()
            found, first = label_of(child)
            if found is None:
                return None
            labels.extend(found)
            if first:
                statements.append(first)
        elif child.get('kind') == 'BreakStmt' and labels:
            close()
        elif labels:
            statements.append(child)
        else:
            return None  # statements before the first label
    if labels and statements and statements[-1].get('kind') not in ('ReturnStmt', 'ContinueStmt', 'BreakStmt'):
        pass  # the last group may run to the end of the switch
    close()
    for _, group in groups:
        if any(_escapes_switch(n) for n in group):
            return None
    return groups


def _escapes_switch(node):
    """A break inside node that leaves the switch (not one of a nested loop or switch)."""
    if node.get('kind') == 'BreakStmt':
        return True
    if node.get('kind') in LOOPS + ('SwitchStmt',):
        return False
    return any(_escapes_switch(c) for c in node.get('inner', ()) if isinstance(c, dict))


def lower_switches(unit):
    """`switch` as qpel reads it (see _switch_groups): on a constant (a template parameter, bitsandbytes'
    `switch (DATA_TYPE)`) the statements of the case it takes; otherwise its value in a local and an
    if-else chain over the cases, the default last. A switch qpel cannot read so is left (reported)."""
    if unit.get('_qpel_switches'):
        return
    counter = [0]
    for function in walk(unit):
        if function.get('kind') != 'FunctionDecl' or _body(function) is None:
            continue
        for node in [n for n in walk(_body(function)) if n.get('kind') == 'SwitchStmt']:
            inner = [c for c in node.get('inner', ()) if isinstance(c, dict)]
            if len(inner) != 2 or inner[1].get('kind') != 'CompoundStmt':
                continue
            cond, body = inner
            groups = _switch_groups(body)
            if groups is None:
                continue
            value = _literal(cond)
            if value is not None:
                taken = next((g for labels, g in groups if value in labels), None)
                if taken is None:
                    taken = next((g for labels, g in groups if 'default' in labels), [])
                node.clear()
                node.update({'kind': 'CompoundStmt', 'inner': list(taken)})
                continue
            counter[0] += 1
            t = dict(cond.get('type', {'qualType': 'int'}))
            local = {'id': f'{function.get("id")}.switch{counter[0]}', 'kind': 'VarDecl', 'name': f'switch_value_{counter[0]}',
                     'type': t, 'init': 'c', 'inner': [cond], 'range': node.get('range'), 'loc': node.get('loc')}
            def ref():
                return {'kind': 'ImplicitCastExpr', 'type': dict(t), 'valueCategory': 'prvalue', 'castKind': 'LValueToRValue',
                        'inner': [{'kind': 'DeclRefExpr', 'type': dict(t), 'valueCategory': 'lvalue',
                                   'referencedDecl': {'id': local['id'], 'kind': 'VarDecl', 'name': local['name'], 'type': dict(t)}}]}
            chain = None
            ordered = [g for g in groups if 'default' not in g[0]] + [g for g in groups if 'default' in g[0]]
            for labels, group in reversed(ordered):
                block = {'kind': 'CompoundStmt', 'inner': list(group)}
                values = [v for v in labels if v != 'default']
                if 'default' in labels:
                    chain = block  # (cases that share the default's group need no test)
                    continue
                test = None
                for v in values:
                    eq = {'kind': 'BinaryOperator', 'type': {'qualType': 'bool'}, 'valueCategory': 'prvalue', 'opcode': '==',
                          'inner': [ref(), {'kind': 'IntegerLiteral', 'type': dict(t), 'valueCategory': 'prvalue', 'value': str(v)}]}
                    test = eq if test is None else {'kind': 'BinaryOperator', 'type': {'qualType': 'bool'},
                                                    'valueCategory': 'prvalue', 'opcode': '||', 'inner': [test, eq]}
                chain = {'kind': 'IfStmt', 'inner': [test, block] + ([chain] if chain is not None else []),
                         **({'hasElse': True} if chain is not None else {})}
            node.clear()
            node.update({'kind': 'CompoundStmt', 'inner': [{'kind': 'DeclStmt', 'inner': [local]}] + ([chain] if chain else [])})
    unit['_qpel_switches'] = True


LOOPS = ('ForStmt', 'WhileStmt', 'DoStmt', 'CXXForRangeStmt')
UNIT_ROWS = re.compile(r'(.*\S)\s*\[1\]\[([0-9]+)\]')


def _literal(node):
    """The value of an expression of integer literals and operators, or None."""
    node = _peel(node)
    while node.get('kind') in ('SubstNonTypeTemplateParmExpr', 'ConstantExpr') and node.get('inner'):
        if node.get('kind') == 'ConstantExpr' and 'value' in node and str(node['value']).lstrip('-').isdigit():
            return int(node['value'])
        node = _peel(node['inner'][-1])
    kind = node.get('kind')
    inner = [c for c in node.get('inner', ()) if isinstance(c, dict)]
    if kind == 'IntegerLiteral':
        return int(node['value'])
    if kind == 'CXXBoolLiteralExpr':
        return int(bool(node['value']))
    if kind == 'BinaryOperator' and len(inner) == 2 and node.get('opcode') in BINARY:
        a, b = _literal(inner[0]), _literal(inner[1])
        return None if a is None or b is None else int(BINARY[node['opcode']](a, b))
    if kind == 'UnaryOperator' and node.get('opcode') == '!' and inner:
        a = _literal(inner[0])
        return None if a is None else int(not a)
    return None


def _refers(node, decl_id):
    node = _peel(node)
    return node.get('kind') == 'DeclRefExpr' and node.get('referencedDecl', {}).get('id') == decl_id


def _escapes(body):
    """True when a break or continue in body leaves it (one not inside a nested loop or switch)."""
    def visit(node):
        if node.get('kind') in ('BreakStmt', 'ContinueStmt'):
            return True
        if node.get('kind') in LOOPS + ('SwitchStmt',):
            return False
        return any(visit(c) for c in node.get('inner', ()) if isinstance(c, dict))
    return visit(body)


def _assigns(body, decl_id):
    for node in walk(body):
        inner = node.get('inner') or ()
        if node.get('kind') in ('BinaryOperator', 'CompoundAssignOperator') and node.get('opcode', '').endswith('=') \
                and node.get('opcode') not in ('==', '!=', '<=', '>=') and inner and _refers(inner[0], decl_id):
            return True
        if node.get('kind') == 'UnaryOperator' and node.get('opcode') in ('++', '--', '&') and inner and _refers(inner[0], decl_id):
            return True
    return False


def single_rows(unit):
    """mamba's kernels keep kNRows rows of everything (`float vals[kNRows][kNItems]`, loops
    `for (int r = 0; r < kNRows; ++r)`) and are only built with kNRows = 1. Such a loop with one
    trip is its body with r = 0; an `if` that is then constant (`if (r > 0) __syncthreads();`) is
    folded; and a local array of one row, `T a[1][N]`, is the row `T a[N]` (`a[0][i]` is `a[i]`,
    and `a[0]` passed on is `a`). Rows other than 0 (kNRows > 1) are left as they are."""
    if unit.get('_qpel_single_rows'):
        return
    values = constant_values(unit)

    def bound(node):
        node = _peel(node)
        if node.get('kind') == 'DeclRefExpr':
            return values.get(node.get('referencedDecl', {}).get('id'))
        return _literal(node)

    for function in walk(unit):
        if function.get('kind') != 'FunctionDecl' or _body(function) is None:
            continue
        body = _body(function)
        for node in [n for n in walk(body) if n.get('kind') == 'ForStmt' and len(n.get('inner', ())) == 5]:
            init, _, cond, step, loop_body = node['inner']
            decls = init.get('inner', ()) if init.get('kind') == 'DeclStmt' else ()
            if len(decls) != 1 or decls[0].get('kind') != 'VarDecl' or not decls[0].get('inner') or \
                    _literal(decls[0]['inner'][0]) != 0:
                continue
            index = decls[0]['id']
            if not (cond.get('kind') == 'BinaryOperator' and cond.get('opcode') == '<' and _refers(cond['inner'][0], index)
                    and bound(cond['inner'][1]) == 1):
                continue
            if not (step.get('kind') == 'UnaryOperator' and step.get('opcode') == '++' and _refers(step['inner'][0], index)):
                continue
            if _escapes(loop_body) or _assigns(loop_body, index):
                continue
            for ref in walk(loop_body):
                if ref.get('kind') == 'DeclRefExpr' and ref.get('referencedDecl', {}).get('id') == index:
                    ref.clear()
                    ref.update({'kind': 'IntegerLiteral', 'type': {'qualType': 'int'}, 'valueCategory': 'prvalue', 'value': '0',
                                '_qpel_row': True})
            node.clear()
            node.update(loop_body if loop_body.get('kind') == 'CompoundStmt' else
                        {'kind': 'CompoundStmt', 'inner': [loop_body]})
        for node in walk(body):
            if node.get('kind') == 'IfStmt' and not node.get('hasVar') and not node.get('hasInit') and node.get('inner') \
                    and any(n.get('_qpel_row') for n in walk(node['inner'][0])):  # a test of the row index only
                value = _literal(node['inner'][0])
                if value is None:
                    continue
                branches = node['inner'][1:]
                taken = branches[0] if value else (branches[1] if len(branches) > 1 and node.get('hasElse') else None)
                node.clear()
                node.update(taken if taken is not None else {'kind': 'NullStmt'})
        for decl in [n for n in walk(body) if n.get('kind') == 'VarDecl' and 'init' not in n]:
            rows = UNIT_ROWS.fullmatch(_spelling(decl.get('type', {})).strip())
            if not rows:
                continue
            uses = [n for n in walk(body) if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == decl['id']]
            outer = [n for n in walk(body) if n.get('kind') == 'ArraySubscriptExpr' and n.get('inner') and
                     _refers(n['inner'][0], decl['id'])]
            if len(outer) != len(uses) or any(_literal(n['inner'][1]) != 0 for n in outer):
                continue
            for key in ('qualType', 'desugaredQualType'):
                if key in decl['type']:
                    found = UNIT_ROWS.fullmatch(decl['type'][key].strip())
                    if found:
                        decl['type'][key] = f'{found.group(1)}[{found.group(2)}]'
            for n in outer:
                ref = _peel(n['inner'][0])
                ref = dict(ref, type=dict(decl['type']), referencedDecl=dict(ref['referencedDecl'], type=dict(decl['type'])))
                n.clear()
                n.update(ref)
    unit['_qpel_single_rows'] = True


def _peel(node):
    while node.get('kind') in ('ImplicitCastExpr', 'ParenExpr') and node.get('inner'):
        node = node['inner'][0]
    return node


def _body(function):
    return next((c for c in function.get('inner', ()) if c.get('kind') == 'CompoundStmt'), None)


def local_pointers_as_references(unit):
    """`update(scalar_t *val, ...)` called only as `update(&result, ...)`: the pointer names one
    variable of the caller, which the helper reads and writes through `*val`. That is a reference
    parameter (IR version 4): `*val` reads as val and `&result` passes result. A parameter that
    any call passes something else (a buffer, a pointer into one), or that the body uses other than
    as `*val`, is left a pointer."""
    if unit.get('_qpel_local_pointers') is not None:
        return unit['_qpel_local_pointers']
    functions = {n['id']: n for n in walk(unit) if n.get('kind') == 'FunctionDecl' and _body(n) is not None}
    locals_ = {n['id'] for f in functions.values() for n in walk(_body(f)) if n.get('kind') == 'VarDecl'}
    calls = {}  # function id -> [call]
    for f in functions.values():
        for n in walk(_body(f)):
            if n.get('kind') == 'CallExpr' and n.get('inner'):
                target = _peel(n['inner'][0])
                if target.get('kind') == 'DeclRefExpr' and target.get('referencedDecl', {}).get('id') in functions:
                    calls.setdefault(target['referencedDecl']['id'], []).append(n)
    changed = []
    for ident, uses in calls.items():
        function = functions[ident]
        params = [p for p in function.get('inner', ()) if p.get('kind') == 'ParmVarDecl']
        for i, p in enumerate(params):
            spelled = p.get('type', {}).get('qualType', '')
            if not spelled.rstrip().endswith('*') or spelled.count('*') != 1 or 'const' in spelled.split('*')[0].split():
                continue
            args = [c['inner'][1 + i] if len(c['inner']) > 1 + i else None for c in uses]
            if not all(a is not None and _peel(a).get('kind') == 'UnaryOperator' and _peel(a).get('opcode') == '&'
                       and _peel(_peel(a)['inner'][0]).get('kind') == 'DeclRefExpr'
                       and _peel(_peel(a)['inner'][0])['referencedDecl'].get('id') in locals_ for a in args):
                continue
            derefs, other = [], False

            def visit(node, parent_deref):
                nonlocal other
                if node.get('kind') == 'UnaryOperator' and node.get('opcode') == '*' and node.get('inner') and \
                        _peel(node['inner'][0]).get('kind') == 'DeclRefExpr' and \
                        _peel(node['inner'][0])['referencedDecl'].get('id') == p['id']:
                    derefs.append(node)
                    return
                if node.get('kind') == 'DeclRefExpr' and node.get('referencedDecl', {}).get('id') == p['id']:
                    other = True
                for c in node.get('inner', ()) or ():
                    if isinstance(c, dict):
                        visit(c, False)
            visit(_body(function), False)
            if other:
                continue
            pointee = spelled.rstrip()[:-1].rstrip()
            desugared = p['type'].get('desugaredQualType', '').rstrip()
            p['type'] = {'qualType': pointee + ' &'}
            if desugared.endswith('*'):
                p['type']['desugaredQualType'] = desugared[:-1].rstrip() + ' &'
            value_type = {'qualType': pointee}
            if desugared.endswith('*'):
                value_type['desugaredQualType'] = desugared[:-1].rstrip()
            for d in derefs:
                d.clear()
                d.update({'kind': 'DeclRefExpr', 'type': dict(value_type), 'valueCategory': 'lvalue',
                          'referencedDecl': {'id': p['id'], 'kind': 'ParmVarDecl', 'name': p.get('name'),
                                             'type': dict(p['type'])}})
            for call, a in zip(uses, args):
                ref = _peel(_peel(a)['inner'][0])
                call['inner'][1 + i] = ref
            changed.append(f'{function.get("name")}({p.get("name")})')
    unit['_qpel_local_pointers'] = changed
    return changed
