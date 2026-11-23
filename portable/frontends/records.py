"""Struct values read as their fields, before import.

qpel has no struct values: a struct is a kernel argument or a helper parameter, never a local or a
result. Device code returns small aggregates anyway (gsplat's `GaussianWeight {vis, alpha, valid}`
from `eval_gaussian_weight`), so each plain struct (scalar fields only, no base classes, no methods
with bodies) is taken apart into its fields ("scalar replacement"), as functors.py does for function
objects:

- a function returning one returns void and takes one reference parameter per field after its own,
  `result_vis`, ...; `return out;` of a local of the struct assigns its fields there and returns, and
  `return {a, b, c};` assigns a, b and c;
- a local `S x;` becomes one local per field, `x_vis`, ... (uninitialised, as the struct's were); `S x
  = f(args);` declares them and then calls `f(args, x_vis, ...)`; `S y = x;` copies field by field;
- `x.f` reads or writes that field's local.

A struct used any other way (passed whole, its address taken, a temporary member `f(args).vis`) is
left as it is, and the importer reports it where it is read.
"""
import copy
import re

from .statics import walk, _body, _spelling, _plain
from .functors import RECORDS, _unwrap, _type_key, _reference, _field_type

BUILTIN_VECTOR = re.compile(r'(u?char|u?short|u?int|u?long|u?longlong|float|double|half|bfloat16|__half|__nv_bfloat16)[1-4]|dim3|__half2|__nv_bfloat162')
SCALAR_SPELLINGS = {'float', 'double', 'bool', 'int', 'unsigned int', 'uint', 'uint32_t', 'int32_t', 'int64_t',
                    'uint64_t', 'long', 'unsigned long', 'long long', 'unsigned long long', 'short', 'unsigned short',
                    'char', 'unsigned char', 'signed char', 'int8_t', 'uint8_t', 'int16_t', 'uint16_t', 'half', '__half',
                    'size_t'}


GLM_NAMES = {'r': 'x', 'g': 'y', 'b': 'z', 'a': 'w', 's': 'x', 't': 'y', 'p': 'z', 'q': 'w'}


def glm_members(unit):
    """glm's vec<N, T> names its components through anonymous unions (`union { T x, r, s; }`): `v.x` is
    a member of an unnamed member of v. Read it as the member of v itself, as for CUDA's float3, with
    glm's other names (r, g, b, a; s, t, p, q) as x, y, z, w."""
    for node in walk(unit):
        if node.get('kind') != 'MemberExpr' or node.get('isArrow') or not node.get('inner'):
            continue
        inner = node['inner'][0]
        if inner.get('kind') != 'MemberExpr' or inner.get('name') or inner.get('isArrow') or not inner.get('inner'):
            continue
        if 'anonymous union' not in inner.get('type', {}).get('qualType', '') and \
                'anonymous struct' not in inner.get('type', {}).get('qualType', ''):
            continue
        base = inner['inner'][0]
        spelled = base.get('type', {}).get('desugaredQualType', base.get('type', {}).get('qualType', ''))
        if 'vec<' not in spelled and not any(w in spelled.split() for w in ('vec2', 'vec3', 'vec4')) and \
                not _plain(spelled).replace('const ', '').strip() in ('vec2', 'vec3', 'vec4', 'ivec2', 'ivec3', 'ivec4', 'uvec2', 'uvec4'):
            continue
        node['inner'] = [base]
        node['name'] = GLM_NAMES.get(node.get('name'), node.get('name'))


def plain_structs(unit):
    """{type key: record} of the structs made of scalar fields only, with no bases and no methods with bodies."""
    found = {}
    for node in walk(unit):
        if node.get('kind') not in RECORDS or not node.get('name') or node.get('bases') or \
                node.get('tagUsed') not in ('struct', 'class', None) or not node.get('completeDefinition', True):
            continue
        inner = node.get('inner', ())
        fields = [c for c in inner if c.get('kind') == 'FieldDecl']
        if not fields or any(c.get('kind') in ('CXXMethodDecl', 'CXXConstructorDecl') and _body(c) is not None
                             and not c.get('isImplicit') for c in inner):
            continue
        if any(_plain(_spelling(f['type'])).replace('const ', '').strip() not in SCALAR_SPELLINGS for f in fields):
            continue
        if BUILTIN_VECTOR.fullmatch(node['name']) or any(part in str(node.get('loc', {}).get('_file', ''))
                                                         for part in ('/frontends/include/', '/differential/include/')):
            continue  # CUDA's own vector types (float2, uint3, dim3) and qpel's stand-in headers
        found.setdefault(node['name'], node)
    return found


def _fields(record):
    return [c for c in record.get('inner', ()) if c.get('kind') == 'FieldDecl']


def _local(decl, field):
    return {'id': f'{decl["id"]}.{field["name"]}', 'kind': 'VarDecl', 'name': f'{decl["name"]}_{field["name"]}',
            'type': _field_type(field, False), 'loc': decl.get('loc'), 'range': decl.get('range')}


def _value_type(decl):
    return {'qualType': _plain(_spelling(decl['type'])).replace('&', '').strip()}


def _assign(target, value):
    """target = value, as clang spells an assignment statement."""
    lhs = _reference(target, _value_type(target))
    rhs = value if value.get('kind') == 'ImplicitCastExpr' else {
        'kind': 'ImplicitCastExpr', 'type': _value_type(target), 'valueCategory': 'prvalue', 'castKind': 'LValueToRValue',
        'inner': [value]} if value.get('valueCategory') == 'lvalue' else value
    return {'kind': 'BinaryOperator', 'type': _value_type(target), 'valueCategory': 'lvalue', 'opcode': '=',
            'inner': [lhs, rhs]}


def scalarize_struct_values(unit):
    """Take plain struct values apart (see the module's description). Returns the functions changed."""
    if unit.get('_qpel_struct_values') is not None:
        return unit['_qpel_struct_values']
    structs = plain_structs(unit)
    if not structs:
        unit['_qpel_struct_values'] = []
        return []

    def struct_of(t):
        key = _type_key(t or {})
        return structs.get(key) or structs.get(key.rpartition('::')[2])

    functions = [n for n in walk(unit) if n.get('kind') == 'FunctionDecl' and _body(n) is not None]
    changed = []
    results = {}  # function id -> (record, [result parameters])
    for function in functions:
        if any(c.get('kind') == 'CUDAGlobalAttr' for c in function['inner']):
            continue
        result = function['type']['qualType'].split('(')[0].strip()
        record = struct_of({'qualType': result})
        if record is None:
            continue
        params = [{'id': f'{function["id"]}.result_{f["name"]}', 'kind': 'ParmVarDecl', 'name': f'result_{f["name"]}',
                   'type': _field_type(f, True), 'loc': function.get('loc'), 'range': function.get('range')}
                  for f in _fields(record)]
        own = [c for c in function['inner'] if c.get('kind') == 'ParmVarDecl']
        rest = [c for c in function['inner'] if c.get('kind') != 'ParmVarDecl']
        function['inner'] = own + params + rest
        signature = function['type']['qualType']
        inside = signature[signature.index('(') + 1:signature.rindex(')')].strip()
        extra = ', '.join(p['type']['qualType'] for p in params)
        function['type'] = {'qualType': f'void ({inside + ", " if inside and inside != "void" else ""}{extra})'}
        results[function['id']] = (record, params)
        changed.append(function['name'])

    objects = {}  # clang id of a local struct -> (record, [field locals])
    for function in functions:
        body = _body(function)
        for block in walk(body):
            if block.get('kind') != 'CompoundStmt':
                continue
            out = []
            for statement in block.get('inner', ()):
                if statement.get('kind') != 'DeclStmt':
                    out.append(statement)
                    continue
                kept, after = [], []
                for decl in statement.get('inner', ()):
                    record = struct_of(decl.get('type')) if decl.get('kind') == 'VarDecl' else None
                    if record is None or '*' in _spelling(decl['type']) or '[' in _spelling(decl['type']):
                        kept.append(decl)
                        continue
                    init = _unwrap(decl['inner'][-1]) if decl.get('inner') and 'init' in decl else None
                    if init is not None and init.get('kind') in ('CXXConstructExpr',) and len(init.get('inner', ())) == 1:
                        init = _unwrap(init['inner'][0])  # a copy or a moved temporary
                    locals_ = [_local(decl, f) for f in _fields(record)]
                    if init is None or init.get('kind') == 'CXXConstructExpr' and not init.get('inner'):
                        pass  # uninitialised, as the struct's fields were
                    elif init.get('kind') == 'CallExpr' and _unwrap(init['inner'][0]).get('referencedDecl', {}).get('id') in results:
                        call = copy.deepcopy(init)
                        target = _unwrap(call['inner'][0])
                        callee = next(f for f in functions if f['id'] == target['referencedDecl']['id'])
                        target['type'] = dict(callee['type'])
                        target['referencedDecl']['type'] = dict(callee['type'])
                        call['type'] = {'qualType': 'void'}
                        call['inner'] += [_reference(local, _value_type(local)) for local in locals_]
                        after.append(call)
                    elif init.get('kind') == 'DeclRefExpr' and init.get('referencedDecl', {}).get('id') in objects:
                        for local, source in zip(locals_, objects[init['referencedDecl']['id']][1]):
                            local['init'] = 'c'
                            local['inner'] = [{'kind': 'ImplicitCastExpr', 'type': _value_type(local), 'valueCategory': 'prvalue',
                                               'castKind': 'LValueToRValue', 'inner': [_reference(source, _value_type(source))]}]
                    elif init.get('kind') == 'InitListExpr' and len(init.get('inner', ())) == len(locals_):
                        for local, value in zip(locals_, init['inner']):
                            local['init'] = 'c'
                            local['inner'] = [copy.deepcopy(value)]
                    else:
                        kept.append(decl)
                        continue
                    objects[decl['id']] = (record, locals_)
                    kept.extend(locals_)
                if kept:
                    out.append(dict(statement, inner=kept))
                out.extend(after)
            block['inner'] = out

    for function in functions:
        own = results.get(function['id'])
        for node in walk(_body(function)):
            kind = node.get('kind')
            if kind == 'MemberExpr' and not node.get('isArrow') and node.get('inner'):
                base = _unwrap(node['inner'][0])
                if base.get('kind') == 'DeclRefExpr' and base.get('referencedDecl', {}).get('id') in objects:
                    record, decls = objects[base['referencedDecl']['id']]
                    target = next((d for d, f in zip(decls, _fields(record)) if f['name'] == node.get('name')), None)
                    if target is not None:
                        node.clear()
                        node.update(_reference(target, _value_type(target)))
            elif kind == 'ReturnStmt' and own is not None and node.get('inner'):
                value = _unwrap(node['inner'][0])
                if value.get('kind') == 'CXXConstructExpr' and len(value.get('inner', ())) == 1:
                    value = _unwrap(value['inner'][0])
                record, params = own
                if value.get('kind') == 'DeclRefExpr' and value.get('referencedDecl', {}).get('id') in objects:
                    sources = [_reference(d, _value_type(d)) for d in objects[value['referencedDecl']['id']][1]]
                elif value.get('kind') == 'InitListExpr' and len(value.get('inner', ())) == len(params):
                    sources = [copy.deepcopy(v) for v in value['inner']]
                else:
                    continue
                statements = [_assign(p, s) for p, s in zip(params, sources)] + [{'kind': 'ReturnStmt'}]
                node.clear()
                node.update({'kind': 'CompoundStmt', 'inner': statements})
    unit['_qpel_struct_values'] = changed
    return changed


TWO_D = re.compile(r'(.+?)\s*\[(\d+)\]\s*\[(\d+)\]')
UNSIGNED = {'qualType': 'unsigned int'}


def _as_unsigned(node):
    if node.get('type', {}).get('qualType') in ('unsigned int', 'uint32_t', 'uint'):
        return node
    return {'kind': 'ImplicitCastExpr', 'type': dict(UNSIGNED), 'valueCategory': 'prvalue', 'castKind': 'IntegralCast',
            'inner': [node]}


def _flat_items(init, rows, columns):
    """The elements of a nested list `{{a, b}, {c}}` in order, or None when a missing element comes before
    a written one (only a zero-filled tail is read: `= {0.f}`)."""
    def items(node):
        if node.get('kind') != 'InitListExpr':
            return None
        if 'array_filler' in node:
            filler, *written = node['array_filler']
            return written + list(node.get('inner', ())), True
        return list(node.get('inner', ())), False
    outer = items(init)
    if outer is None:
        return None
    flat, ended = [], False
    for row in outer[0]:
        inner = items(row)
        if inner is None or ended and inner[0]:
            return None
        flat.extend(inner[0])
        if inner[1] or len(inner[0]) < columns:
            ended = True
    return flat


def flatten_local_matrices(unit):
    """`float pix_out[P][C]` (a thread's 2D array) as `float pix_out[P * C]`, read at `[p * C + c]`: qpel's
    local arrays have one dimension. Only arrays read and written element by element are changed."""
    for function in walk(unit):
        if function.get('kind') != 'FunctionDecl' or _body(function) is None:
            continue
        body = _body(function)
        for decl in walk(body):
            if decl.get('kind') != 'VarDecl' or any(c.get('kind') == 'CUDASharedAttr' for c in decl.get('inner', ())):
                continue
            found = TWO_D.fullmatch(decl.get('type', {}).get('qualType', '').strip())
            if not found or decl.get('storageClass') == 'static':
                continue
            element, rows, columns = found.group(1), int(found.group(2)), int(found.group(3))
            uses = [n for n in walk(body) if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == decl['id']]
            pairs = []
            for node in walk(body):
                if node.get('kind') != 'ArraySubscriptExpr' or not node.get('inner'):
                    continue
                row = _peel_cast(node['inner'][0])
                if row.get('kind') != 'ArraySubscriptExpr':
                    continue
                base = _peel_cast(row['inner'][0])
                if base.get('kind') == 'DeclRefExpr' and base.get('referencedDecl', {}).get('id') == decl['id']:
                    pairs.append((node, row, base))
            if len(pairs) != len(uses):
                continue  # a row used whole (passed on, or its address taken)
            init = decl['inner'][-1] if decl.get('inner') and 'init' in decl else None
            flat = None
            if init is not None:
                core = _peel_cast(init)
                flat = _flat_items(core, rows, columns)
                if flat is None:
                    continue
            flat_type = {'qualType': f'{element}[{rows * columns}]'}
            decl['type'] = flat_type
            if init is not None:
                decl['inner'] = [{'kind': 'InitListExpr', 'type': dict(flat_type), 'valueCategory': 'prvalue',
                                  'array_filler': [{'kind': 'ImplicitValueInitExpr', 'type': {'qualType': element}}] + flat}]
            for node, row, base in pairs:
                i, j = row['inner'][1], node['inner'][1]
                scaled = {'kind': 'BinaryOperator', 'type': dict(UNSIGNED), 'valueCategory': 'prvalue', 'opcode': '*',
                          'inner': [_as_unsigned(i), {'kind': 'IntegerLiteral', 'type': dict(UNSIGNED), 'valueCategory': 'prvalue',
                                                      'value': str(columns)}]}
                index = {'kind': 'BinaryOperator', 'type': dict(UNSIGNED), 'valueCategory': 'prvalue', 'opcode': '+',
                         'inner': [scaled, _as_unsigned(j)]}
                base['type'] = dict(flat_type)
                base['referencedDecl'] = dict(base['referencedDecl'], type=dict(flat_type))
                decayed = {'kind': 'ImplicitCastExpr', 'type': {'qualType': element + ' *'}, 'valueCategory': 'prvalue',
                           'castKind': 'ArrayToPointerDecay', 'inner': [base]}
                node['inner'] = [decayed, index]


def _peel_cast(node):
    while node.get('kind') in ('ImplicitCastExpr', 'ParenExpr', 'ExprWithCleanups', 'ConstantExpr') and node.get('inner'):
        node = node['inner'][0]
    return node


OFFSET_TYPE = {'qualType': 'long'}
CASTISH = ('ImplicitCastExpr', 'ParenExpr')


def _parents(node, parent=None, out=None):
    """{id(child): parent} over a clang subtree."""
    out = {} if out is None else out
    for child in node.get('inner', ()):
        if isinstance(child, dict):
            out[id(child)] = node
            _parents(child, node, out)
    return out


def _long(node):
    if node.get('type', {}).get('qualType') in ('long', 'int64_t', 'long long'):
        return node
    return {'kind': 'ImplicitCastExpr', 'type': dict(OFFSET_TYPE), 'valueCategory': 'prvalue', 'castKind': 'IntegralCast',
            'inner': [node]}


def _is_null(node):
    node = _peel_cast(node)
    return node.get('kind') in ('CXXNullPtrLiteralExpr', 'GNUNullExpr') or \
        node.get('kind') == 'IntegerLiteral' and node.get('value') == '0'


def _element_of(pointer_type):
    """`const __half *__restrict` -> `__half`."""
    text = pointer_type.replace('__restrict', '').replace('const ', '').replace('volatile ', '').strip()
    return text[:-1].strip() if text.endswith('*') else None


def _int(value):
    return {'kind': 'IntegerLiteral', 'type': {'qualType': 'int'}, 'valueCategory': 'prvalue', 'value': str(value)}


def wide_copies(unit):
    """A wide load or store through reinterpret_cast (gsplat's spherical harmonics:
    `reinterpret_cast<uint4 *>(mem)[0] = reinterpret_cast<const uint4 *>(coeffs)[i]`, 16 bytes at once)
    as the element copies it stands for: with W = sizeof(V) / sizeof(T) elements of T per V,
    `mem[0 * W + j] = coeffs[i * W + j]` for j < W. Both sides must point to the same element type T;
    the bits copied are the same, only the width of each access differs."""
    from .statics import SIZES

    def side(node):
        node = _peel_cast(node) if node.get('kind') != 'ArraySubscriptExpr' else node
        if node.get('kind') != 'ArraySubscriptExpr' or len(node.get('inner', ())) != 2:
            return None
        base = node['inner'][0]
        while base.get('kind') == 'ParenExpr' and base.get('inner'):
            base = base['inner'][0]
        if base.get('kind') != 'CXXReinterpretCastExpr' or not base.get('inner'):
            return None
        pointer = base['inner'][0]
        element = _element_of(pointer.get('type', {}).get('qualType', ''))
        wide = _element_of(base.get('type', {}).get('desugaredQualType', base.get('type', {}).get('qualType', '')))
        return pointer, element, wide, node['inner'][1]

    for parent in walk(unit):
        statements = parent.get('inner')
        if parent.get('kind') != 'CompoundStmt' or not statements:
            continue
        for k, node in enumerate(statements):
            if node.get('kind') == 'CXXOperatorCallExpr' and len(node.get('inner', ())) == 3 and \
                    _peel_cast(node['inner'][0]).get('referencedDecl', {}).get('name') == 'operator=':
                left, right = node['inner'][1], node['inner'][2]
                wide_type = node.get('type', {}).get('desugaredQualType', node.get('type', {}).get('qualType', ''))
            elif node.get('kind') == 'BinaryOperator' and node.get('opcode') == '=':
                left, right = node['inner']
                wide_type = node.get('type', {}).get('desugaredQualType', node.get('type', {}).get('qualType', ''))
            else:
                continue
            a, b = side(left), side(right)
            if a is None or b is None or a[1] != b[1] or a[1] not in SIZES:
                continue
            wide_type = wide_type.replace('const ', '').strip()
            if wide_type not in SIZES or SIZES[wide_type] % SIZES[a[1]]:
                continue
            width = SIZES[wide_type] // SIZES[a[1]]
            element = a[1]

            def at(pointer, index, j, const):
                scaled = {'kind': 'BinaryOperator', 'opcode': '*', 'type': {'qualType': 'int'}, 'valueCategory': 'prvalue',
                          'inner': [copy.deepcopy(index), _int(width)]}
                return {'kind': 'ArraySubscriptExpr', 'type': {'qualType': ('const ' if const else '') + element},
                        'valueCategory': 'lvalue', 'range': node.get('range'),
                        'inner': [copy.deepcopy(pointer), {'kind': 'BinaryOperator', 'opcode': '+', 'type': {'qualType': 'int'},
                                                           'valueCategory': 'prvalue', 'inner': [scaled, _int(j)]}]}
            copies = []
            for j in range(width):
                read = {'kind': 'ImplicitCastExpr', 'castKind': 'LValueToRValue', 'type': {'qualType': element},
                        'valueCategory': 'prvalue', 'inner': [at(b[0], b[3], j, True)]}
                copies.append({'kind': 'BinaryOperator', 'opcode': '=', 'type': {'qualType': element}, 'valueCategory': 'lvalue',
                               'range': node.get('range'), 'inner': [at(a[0], a[3], j, False), read]})
            statements[k] = {'kind': 'CompoundStmt', 'range': node.get('range'), 'inner': copies}


def _array_of(node):
    """(the local array's DeclRefExpr, element, length) for `items` decayed to a pointer, else None."""
    while node.get('kind') == 'ImplicitCastExpr' and node.get('castKind') == 'NoOp' and node.get('inner'):
        node = node['inner'][0]  # (to const T *)
    if node.get('kind') != 'ImplicitCastExpr' or node.get('castKind') != 'ArrayToPointerDecay' or not node.get('inner'):
        return None
    ref = node['inner'][0]
    while ref.get('kind') == 'ParenExpr' and ref.get('inner'):
        ref = ref['inner'][0]
    spelled = ref.get('type', {}).get('desugaredQualType', ref.get('type', {}).get('qualType', ''))
    found = re.fullmatch(r'(?:const )?([\w:]+(?: [\w:]+)*) ?\[(\d+)\]', spelled)
    if ref.get('kind') != 'DeclRefExpr' or ref.get('referencedDecl', {}).get('kind') != 'VarDecl' or not found:
        return None
    if {'threadgroup', 'device', 'constant', 'thread', '__shared__', 'static'} & set(spelled.split()):
        return None  # (shared memory, or an address space qpel's own output spells: left as it is)
    return ref, found.group(1), int(found.group(2))


def array_arguments(unit):
    """A helper whose pointer parameter is given a local array (gsplat's `sh_coeffs_to_color_fast(..., sh_coeffs,
    out_color)` of `float sh_coeffs[48]`, the same helper given buffers elsewhere) as a copy of the helper for those
    arrays: each such parameter `const T *p` becomes `const T (&p)[N]` (an array by reference, IR version 10), a
    copy per set of lengths, named with them (`_a48_3`). The helper's body is the same; it reads p[i] either way."""
    functions, owners = {}, {}
    for owner in walk(unit):
        for child in owner.get('inner', ()) if isinstance(owner.get('inner'), list) else ():
            if isinstance(child, dict) and child.get('kind') == 'FunctionDecl' and _body(child) is not None:
                functions[child['id']] = child
                owners[child['id']] = owner
    shared = {d['id'] for d in walk(unit) if d.get('kind') == 'VarDecl' and
              (any(a.get('kind') == 'CUDASharedAttr' for a in d.get('inner', ())) or d.get('storageClass') == 'static')}
    made = {}
    for function in list(functions.values()):
        for call in walk(_body(function)):
            if call.get('kind') != 'CallExpr' or not call.get('inner'):
                continue
            target = _peel_cast(call['inner'][0])
            callee = functions.get(target.get('referencedDecl', {}).get('id')) if target.get('kind') == 'DeclRefExpr' else None
            if callee is None:
                continue
            params = [p for p in callee.get('inner', ()) if p.get('kind') == 'ParmVarDecl']
            arrays = {}
            for i, arg in enumerate(call['inner'][1:]):
                found = _array_of(arg)
                if found and found[0]['referencedDecl']['id'] in shared:
                    found = None
                if found and i < len(params) and _element_of(params[i].get('type', {}).get('qualType', '')) is not None:
                    pointee = params[i]['type']['qualType'].replace('__restrict', '').strip()
                    if pointee.endswith('*') and '*' not in pointee[:-1]:
                        arrays[i] = found
            if not arrays:
                continue
            key = (callee['id'], tuple((i, arrays[i][2]) for i in sorted(arrays)))
            if key not in made:
                suffix = '_a' + '_'.join(str(arrays[i][2]) for i in sorted(arrays))
                clone = copy.deepcopy(callee)
                clone['id'] = f'{callee["id"]}{suffix}'
                clone['_qpel_suffix'] = suffix
                if not any(c.get('kind') == 'TemplateArgument' for c in callee.get('inner', ())):
                    clone['name'] = callee['name'] + suffix
                cloned = [p for p in clone['inner'] if p.get('kind') == 'ParmVarDecl']
                for i in arrays:
                    p = cloned[i]
                    const = 'const' in p['type']['qualType'].split('*')[0].split()
                    element = _element_of(p['type'].get('desugaredQualType', p['type']['qualType']))
                    array_type = {'qualType': f'{"const " if const else ""}{element} (&)[{arrays[i][2]}]'}
                    p['type'] = array_type
                    body = _body(clone)
                    parents = _parents(body)
                    for use in walk(body):
                        if use.get('kind') == 'DeclRefExpr' and use.get('referencedDecl', {}).get('id') == p['id']:
                            use['type'] = {'qualType': f'{"const " if const else ""}{element}[{arrays[i][2]}]'}
                            use['referencedDecl'] = dict(use['referencedDecl'], type=dict(array_type))
                            above = parents.get(id(use))
                            if above is not None and above.get('kind') == 'ImplicitCastExpr' and above.get('castKind') == 'LValueToRValue':
                                above['castKind'] = 'ArrayToPointerDecay'
                owner = owners[callee['id']]
                owner['inner'].insert(owner['inner'].index(callee) + 1, clone)
                made[key] = clone
            clone = made[key]
            target['referencedDecl'] = dict(target['referencedDecl'], id=clone['id'], name=clone['name'])
            for i, (ref, _, _) in arrays.items():
                call['inner'][1 + i] = ref


def pointer_offsets(unit):
    """A pointer parameter moved by `p += n` (gsplat: `isect_offsets += image_id * tiles`, `if (masks !=
    nullptr) masks += ...`) as the parameter and an offset: a local `p_offset` starts at 0, `p += n` adds
    to it, `p[i]` reads `p[p_offset + i]` and `p + j` is `p + (p_offset + j)`. A comparison with
    nullptr still tests the parameter itself (an optional buffer stays optional). qpel's buffer
    parameters are never reassigned; a pointer assigned any other way is left as it is."""
    for function in walk(unit):
        if function.get('kind') != 'FunctionDecl' or _body(function) is None:
            continue
        body = _body(function)
        params = [p for p in function['inner'] if p.get('kind') == 'ParmVarDecl' and '*' in p.get('type', {}).get('qualType', '')]
        for p in params:
            def refers(n):
                n = _peel_cast(n)
                return n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == p['id']
            moves = [n for n in walk(body) if n.get('kind') == 'CompoundAssignOperator' and n.get('opcode') in ('+=', '-=')
                     and refers(n['inner'][0])]
            others = [n for n in walk(body) if (n.get('kind') == 'BinaryOperator' and n.get('opcode') == '=' or
                                               n.get('kind') == 'UnaryOperator' and n.get('opcode') in ('++', '--')) and
                      n.get('inner') and refers(n['inner'][0])]
            if not moves or others:
                continue
            offset = {'id': f'{p["id"]}.offset', 'kind': 'VarDecl', 'name': f'{p["name"]}_offset', 'type': dict(OFFSET_TYPE),
                      'init': 'c', 'inner': [{'kind': 'IntegerLiteral', 'type': dict(OFFSET_TYPE), 'valueCategory': 'prvalue',
                                              'value': '0'}], 'loc': p.get('loc'), 'range': p.get('range')}
            moved_ids = set()
            for move in moves:
                move['inner'][0] = _reference(offset, OFFSET_TYPE)
                move['inner'][1] = _long(move['inner'][1])
                move['type'] = dict(OFFSET_TYPE)
                move['computeLHSType'] = dict(OFFSET_TYPE)
                move['computeResultType'] = dict(OFFSET_TYPE)
                moved_ids.add(id(move))
            parents = _parents(body)
            uses = [n for n in walk(body) if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == p['id']]
            for use in uses:
                top = use
                tested = False
                while id(top) in parents and parents[id(top)].get('kind') in CASTISH:
                    top = parents[id(top)]
                    tested = tested or top.get('castKind') == 'PointerToBoolean'
                if tested:
                    continue  # `p ? a : b`, `if (p)`: is the buffer given, as p != nullptr
                above = parents.get(id(top))
                if above is None:
                    continue
                kind, op = above.get('kind'), above.get('opcode')
                if kind == 'BinaryOperator' and op in ('==', '!=') and any(_is_null(x) for x in above['inner']):
                    continue  # p != nullptr: is the buffer given
                plus = lambda value: {'kind': 'BinaryOperator', 'type': dict(OFFSET_TYPE), 'valueCategory': 'prvalue',
                                      'opcode': '+', 'inner': [{'kind': 'ImplicitCastExpr', 'type': dict(OFFSET_TYPE),
                                                                'valueCategory': 'prvalue', 'castKind': 'LValueToRValue',
                                                                'inner': [_reference(offset, OFFSET_TYPE)]}, _long(value)]}
                if kind == 'ArraySubscriptExpr' and above['inner'][0] is top:
                    above['inner'][1] = plus(above['inner'][1])
                elif kind == 'BinaryOperator' and op in ('+', '-') and above['inner'][0] is top and \
                        '*' in above.get('type', {}).get('qualType', ''):
                    right = above['inner'][1]
                    if op == '-':
                        right = {'kind': 'UnaryOperator', 'opcode': '-', 'type': dict(OFFSET_TYPE), 'valueCategory': 'prvalue',
                                 'inner': [_long(right)]}
                        above['opcode'] = '+'
                    above['inner'][1] = plus(right)
                else:  # passed on whole: the moved pointer, p + p_offset
                    moved = {'kind': 'BinaryOperator', 'type': dict(top.get('type', p['type'])), 'valueCategory': 'prvalue',
                             'opcode': '+', 'inner': [copy.deepcopy(top), {'kind': 'ImplicitCastExpr', 'type': dict(OFFSET_TYPE),
                                                                            'valueCategory': 'prvalue', 'castKind': 'LValueToRValue',
                                                                            'inner': [_reference(offset, OFFSET_TYPE)]}]}
                    top.clear()
                    top.update(moved)
            body['inner'].insert(0, {'kind': 'DeclStmt', 'inner': [offset]})
