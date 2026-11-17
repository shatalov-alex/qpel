"""Function objects read as functions and their fields, before import.

CUDA libraries pass operators as objects: an empty struct whose operator() combines two values
(mamba's `SSMScanOp<float>`, CUB's scan operators), and stateful callbacks (mamba's
`SSMScanPrefixCallbackOp`, a struct holding `running_prefix` whose operator() updates it). qpel has
no struct values, so each such object is taken apart into its fields ("scalar replacement"):

- operator() of the struct becomes a function `<Struct>_<arguments>_call` taking the object's fields
  first, by reference (by value for a const operator()), then its own parameters; `running_prefix`
  in its body is the field's parameter;
- a local object `S obj(args)` becomes one local per field, `obj_running_prefix`, initialized as the
  constructor's member initializers say (a constructor with an empty body; anything else is left);
- `obj.f` reads that local, `obj(x)` calls the function with obj's fields, and a temporary of an
  empty struct, `S()(a, b)`, calls it with none;
- a parameter of the struct type (by value or by reference) becomes one parameter per field, of the
  same kind, and every call passes the fields of its argument.

Only structs with an operator() that has a body and no base classes are taken apart; an object used
in any other way (its address taken, copied whole into another object) is left as it is, and the
importer reports it where it is read.
"""
import copy
import re

from .statics import walk, _body, _peel, _spelling, _plain, arguments, _aliases, resolve_aliases

RECORDS = ('CXXRecordDecl', 'ClassTemplateSpecializationDecl')
WRAPPERS = ('ImplicitCastExpr', 'ParenExpr', 'MaterializeTemporaryExpr', 'CXXBindTemporaryExpr', 'ExprWithCleanups',
            'CXXFunctionalCastExpr', 'ConstantExpr')


def _unwrap(node):
    while node.get('kind') in WRAPPERS and node.get('inner'):
        node = node['inner'][0]
    return node


def _type_key(t):
    """A struct type as records are keyed: no const, reference, `struct` or spaces after commas."""
    spelled = _plain(_spelling(t)).replace('&', ' ').replace('const ', ' ')
    return re.sub(r'\s+', ' ', spelled).replace('< ', '<').replace(' >', '>').strip()


class Functor:
    def __init__(self, record, key):
        self.record, self.key = record, key
        inner = record.get('inner', ())
        self.fields = [c for c in inner if c.get('kind') == 'FieldDecl']
        self.call = next((c for c in inner if c.get('kind') == 'CXXMethodDecl' and c.get('name') == 'operator()'
                          and _body(c) is not None), None)
        self.constructors = [c for c in inner if c.get('kind') == 'CXXConstructorDecl' and _body(c) is not None
                             and not c.get('isImplicit')]
        self.field_ids = {f['id']: f['name'] for f in self.fields}
        self.function = None  # the lifted operator()

    @property
    def const(self):
        return self.call['type']['qualType'].rstrip().endswith(' const')


def functors(unit):
    """{struct type key: Functor} of the structs with an operator() that has a body and no bases."""
    found = {}
    for node in walk(unit):
        if node.get('kind') not in RECORDS or not node.get('name') or node.get('bases'):
            continue
        if not any(c.get('kind') == 'CXXMethodDecl' and c.get('name') == 'operator()' and _body(c) is not None
                   for c in node.get('inner', ())):
            continue
        if node.get('kind') == 'ClassTemplateSpecializationDecl':
            args = [c['type'].get('qualType', '') if 'type' in c else str(c.get('value')) for c in node['inner']
                    if c.get('kind') == 'TemplateArgument']
            key = f'{node["name"]}<{", ".join(args)}>'
        else:
            key = node['name']
        found.setdefault(key, Functor(node, key))
    return found


def _reference(target, t=None):
    return {'kind': 'DeclRefExpr', 'type': dict(t or target['type']), 'valueCategory': 'lvalue',
            'referencedDecl': {'id': target['id'], 'kind': target['kind'], 'name': target['name'],
                               'type': dict(target['type'])}}


def _field_type(field, reference):
    spelled = _plain(_spelling(field['type']))
    return {'qualType': spelled + (' &' if reference else '')}


def scalarize_functors(unit):
    """Take function objects apart (see the module's description). Returns the lifted functions' names."""
    if unit.get('_qpel_functors') is not None:
        return unit['_qpel_functors']
    table = functors(unit)
    by_method = {f.call['id']: f for f in table.values() if f.call}
    by_field = {fid: f for f in table.values() for fid in f.field_ids}
    lifted = []

    def functor_of(t):
        return table.get(_type_key(t))

    # 1. operator() of each struct as a function of the fields and its parameters
    tops = {}
    for top, node in enumerate(unit.get('inner', ())):
        for inner in walk(node):
            if inner.get('kind') in RECORDS:
                tops.setdefault(inner.get('id'), top)
    inserts = []
    for f in table.values():
        if not f.call:
            continue
        name = re.sub(r'\W', '_', '_'.join([f.record['name']] + (arguments(f.record) if f.record['kind'] ==
                                                                 'ClassTemplateSpecializationDecl' else []) + ['call']))
        params = [{'id': f'{f.call["id"]}.{field["name"]}', 'kind': 'ParmVarDecl', 'name': f'self_{field["name"]}',
                   'type': _field_type(field, not f.const), 'loc': field.get('loc'), 'range': field.get('range'),
                   '_qpel_field': field['name']} for field in f.fields]
        by_name = {p['_qpel_field']: p for p in params}
        body = copy.deepcopy(_body(f.call))
        for node in walk(body):
            if node.get('kind') == 'MemberExpr' and node.get('isArrow') and node.get('inner') and \
                    _unwrap(node['inner'][0]).get('kind') == 'CXXThisExpr' and node.get('name') in by_name:
                target = by_name[node['name']]
                node.clear()
                node.update(_reference(target, {'qualType': _plain(_spelling(target['type'])).rstrip(' &')}))
        own = [copy.deepcopy(c) for c in f.call['inner'] if c.get('kind') == 'ParmVarDecl']
        attrs = [copy.deepcopy(c) for c in f.call['inner'] if c.get('kind', '').endswith('Attr')]
        signature = f.call['type']['qualType'].rstrip()
        signature = signature[:-len(' const')] if signature.endswith(' const') else signature
        function = {'id': f'{f.call["id"]}.call', 'kind': 'FunctionDecl', 'name': name, 'loc': f.call.get('loc'),
                    'range': f.call.get('range'), 'type': {'qualType': signature},
                    'inner': params + own + attrs + [body], '_qpel_lifted': f.key}
        # the struct's own type aliases (`using scan_t = float2;`), which a method spells its types with
        local, members = _aliases(f.record), unit.get('_qpel_alias_members', {})
        for node in walk(function):
            t = node.get('type')
            if isinstance(t, dict) and t.get('qualType'):
                spelled = t.get('desugaredQualType', t['qualType'])
                resolved = resolve_aliases(spelled, members, local)
                if resolved != spelled:
                    if node is function:
                        t['qualType'] = resolved
                    else:
                        t['desugaredQualType'] = resolved
        f.function = function
        lifted.append(name)
        inserts.append((tops.get(f.record['id'], len(unit['inner']) - 1), function))
    for top, function in sorted(inserts, key=lambda x: -x[0]):
        unit['inner'].insert(top + 1, function)

    # 2. the objects of each function: parameters and locals of a functor type, as their fields
    functions = [n for n in walk(unit) if n.get('kind') == 'FunctionDecl' and _body(n) is not None]
    objects = {}  # clang id of a parameter or local object -> (Functor, [field declarations, in field order])
    expanded = {}  # function id -> {parameter position: Functor}
    for function in functions:
        params = [c for c in function['inner'] if c.get('kind') == 'ParmVarDecl']
        kernel = any(c.get('kind') == 'CUDAGlobalAttr' for c in function['inner'])  # (its arguments are the original's)
        replaced = []
        for position, p in enumerate(params):
            f = functor_of(p.get('type', {}))
            if kernel or f is None or not f.call or '*' in _spelling(p['type']):
                replaced.append([p])
                continue
            reference = '&' in p['type'].get('qualType', '')
            fields = [{'id': f'{p["id"]}.{field["name"]}', 'kind': 'ParmVarDecl', 'name': f'{p.get("name", "op")}_{field["name"]}',
                       'type': _field_type(field, reference), 'loc': p.get('loc'), 'range': p.get('range')}
                      for field in f.fields]
            objects[p['id']] = (f, fields)
            expanded.setdefault(function['id'], {})[position] = f
            replaced.append(fields)
        if function['id'] in expanded:
            rest = [c for c in function['inner'] if c.get('kind') != 'ParmVarDecl']
            function['inner'] = [q for group in replaced for q in group] + rest
        for statement in walk(_body(function)):
            if statement.get('kind') != 'DeclStmt':
                continue
            new_inner, changed = [], False
            for decl in statement.get('inner', ()):
                f = functor_of(decl.get('type', {})) if decl.get('kind') == 'VarDecl' else None
                locals_ = _object_locals(decl, f) if f is not None and f.call else None
                if locals_ is None:
                    new_inner.append(decl)
                    continue
                objects[decl['id']] = (f, locals_)
                new_inner.extend(locals_)
                changed = True
            if changed:
                statement['inner'] = new_inner
                if not new_inner:
                    statement.clear()
                    statement['kind'] = 'NullStmt'

    def fields_of(expression):
        """The field references an object expression stands for, or None when it is not an object qpel took apart."""
        core = _unwrap(expression)
        if core.get('kind') == 'DeclRefExpr' and core.get('referencedDecl', {}).get('id') in objects:
            return [_reference(d, {'qualType': _plain(_spelling(d['type'])).rstrip(' &')})
                    for d in objects[core['referencedDecl']['id']][1]]
        if core.get('kind') in ('CXXTemporaryObjectExpr', 'CXXConstructExpr') and not core.get('inner'):
            f = functor_of(core.get('type', {}))
            if f is not None and not f.fields:
                return []  # a temporary of an empty struct: SSMScanOp<float>()
        return None

    # 3. uses: fields, operator() calls, and calls passing objects
    called = set()
    for function in functions:
        for node in walk(_body(function)):
            kind = node.get('kind')
            if kind == 'MemberExpr' and not node.get('isArrow') and node.get('inner'):
                base = _unwrap(node['inner'][0])
                if base.get('kind') == 'DeclRefExpr' and base.get('referencedDecl', {}).get('id') in objects:
                    f, decls = objects[base['referencedDecl']['id']]
                    name = f.field_ids.get(node.get('referencedMemberDecl'), node.get('name'))
                    target = next((d for d, field in zip(decls, f.fields) if field['name'] == name), None)
                    if target is not None:
                        node.clear()
                        node.update(_reference(target, {'qualType': _plain(_spelling(target['type'])).rstrip(' &')}))
            elif kind == 'CXXOperatorCallExpr' and len(node.get('inner', ())) >= 2:
                method = _unwrap(node['inner'][0]).get('referencedDecl', {}).get('id')
                f = by_method.get(method)
                fields = fields_of(node['inner'][1]) if f is not None and f.function is not None else None
                if fields is None:
                    continue
                callee = {'kind': 'DeclRefExpr', 'type': dict(f.function['type']), 'valueCategory': 'lvalue',
                          'referencedDecl': {'id': f.function['id'], 'kind': 'FunctionDecl', 'name': f.function['name'],
                                             'type': dict(f.function['type'])}}
                call = {'kind': 'CallExpr', 'type': dict(node.get('type', {})), 'valueCategory': node.get('valueCategory'),
                        'range': node.get('range'), 'inner': [callee] + fields + node['inner'][2:]}
                node.clear()
                node.update(call)
                called.add(f.function['name'])
        for node in walk(_body(function)):
            if node.get('kind') != 'CallExpr' or not node.get('inner'):
                continue
            target = _unwrap(node['inner'][0]).get('referencedDecl', {}).get('id')
            if target not in expanded:
                continue
            args, out, ok = node['inner'][1:], [], True
            for position, arg in enumerate(args):
                if position in expanded[target]:
                    fields = fields_of(arg)
                    if fields is None:
                        ok = False
                        break
                    out.extend(fields)
                else:
                    out.append(arg)
            if ok:
                node['inner'] = [node['inner'][0]] + out
    unit['_qpel_functors'] = [name for name in lifted if name in called]
    return unit['_qpel_functors']


def _object_locals(decl, f):
    """The locals one per field for `S obj(args);` (a constructor whose body is empty, the fields given
    by its member initializers with the arguments in place of its parameters), or None."""
    init = _unwrap(decl['inner'][-1]) if decl.get('inner') and 'init' in decl else None
    if init is not None and init.get('kind') not in ('CXXConstructExpr', 'CXXTemporaryObjectExpr'):
        return None
    given = [_unwrap(a) if a.get('kind') == 'MaterializeTemporaryExpr' else a for a in (init or {}).get('inner', ())]
    values = {}
    if f.fields:
        ctor = next((c for c in f.constructors if len([p for p in c['inner'] if p.get('kind') == 'ParmVarDecl']) == len(given)
                     and not _body(c).get('inner')), None)
        if ctor is None:
            return None
        params = {p['id']: a for p, a in zip([p for p in ctor['inner'] if p.get('kind') == 'ParmVarDecl'], given)}
        for initializer in ctor['inner']:
            if initializer.get('kind') != 'CXXCtorInitializer' or not initializer.get('inner'):
                continue
            field = initializer.get('anyInit', {}).get('name')
            value = copy.deepcopy(initializer['inner'][0])
            for node in walk(value):
                ref = node.get('referencedDecl', {}).get('id') if node.get('kind') == 'DeclRefExpr' else None
                if ref in params:  # the constructor's parameter: the argument it was given
                    node.clear()
                    node.update(copy.deepcopy(params[ref]))
            values[field] = value
    out = []
    for field in f.fields:
        local = {'id': f'{decl["id"]}.{field["name"]}', 'kind': 'VarDecl', 'name': f'{decl["name"]}_{field["name"]}',
                 'type': _field_type(field, False), 'loc': decl.get('loc'), 'range': decl.get('range')}
        if field['name'] in values:
            local['init'] = 'c'
            local['inner'] = [values[field['name']]]
        out.append(local)
    return out
