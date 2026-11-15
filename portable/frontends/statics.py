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
        if node.get('kind') == 'CallExpr' and node.get('inner') and callee_name(node) == '__ldg' and len(node['inner']) == 2:
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
    unit['_qpel_enums'] = True


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
