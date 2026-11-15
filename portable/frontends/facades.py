"""Facade classes of qpel's library headers, inlined into the AST before import.

qpel's CUB subset (include/qpel_cub/cub/cub.cuh) writes cub::BlockScan and cub::BlockReduce as
facades: a class built from one argument (the TempStorage), kept as its member, whose every method
is a single statement forwarding to a function: `output = f(storage, input, ...);` or
`return f(storage, input, ...);`. A call `BlockScanT(temp_storage).InclusiveSum(x, y, total)` (or
on a local `BlockScanT scan(temp_storage);`) is replaced, in clang's AST, by that statement with
the method's parameters replaced by the call's arguments and the member by the constructor's
argument: `y = block_scan<...>(temp_storage, x, T(), total)`. Everything after this (which
functions are reached, which thread builtins they read, subgroup and barrier checks) then sees
ordinary calls of ordinary functions, imported like any other header device function.

A call that does not have this shape is marked, and the importer reports it where it is read.
"""
import copy

FACADE_NAMESPACES = {'cub'}
CONTAINERS = {'NamespaceDecl', 'ClassTemplateDecl', 'ClassTemplateSpecializationDecl', 'CXXRecordDecl',
              'FunctionTemplateDecl', 'LinkageSpecDecl'}
WRAPPERS = {'MaterializeTemporaryExpr', 'CXXFunctionalCastExpr', 'CXXBindTemporaryExpr', 'ImplicitCastExpr',
            'ParenExpr', 'ExprWithCleanups', 'ConstantExpr'}


def facade_methods(unit):
    """{clang id: CXXMethodDecl with a body} of the classes in the facade namespaces."""
    methods = {}

    def visit(node, inside):  # (methods without a body are recorded too: declared, not supported)
        kind = node.get('kind')
        if kind == 'NamespaceDecl' and node.get('name') in FACADE_NAMESPACES:
            inside = True
        if inside and kind == 'CXXMethodDecl':
            methods[node['id']] = node
        if kind in CONTAINERS:
            for child in node.get('inner', ()):
                if isinstance(child, dict):
                    visit(child, inside)
    for node in unit.get('inner', ()):
        visit(node, False)
    return methods


def walk(node):
    yield node
    for child in node.get('inner', ()) or ():
        if isinstance(child, dict):
            yield from walk(child)


def constructed_from(node):
    """The one argument a facade object is built from, or None."""
    while node.get('kind') in WRAPPERS and node.get('inner'):
        node = node['inner'][0]
    if node.get('kind') in ('CXXConstructExpr', 'CXXTemporaryObjectExpr'):
        args = [a for a in node.get('inner', ()) if a.get('kind') != 'CXXDefaultArgExpr']
        if len(args) == 1:
            return args[0]
    return None


def has_effects(node):
    return any(n.get('kind') in ('CallExpr', 'CXXMemberCallExpr', 'CompoundAssignOperator') or
               n.get('kind') == 'BinaryOperator' and n.get('opcode') == '=' or
               n.get('kind') == 'UnaryOperator' and n.get('opcode') in ('++', '--') for n in walk(node))


def inline_facades(unit):
    """Rewrite every facade method call in `unit` in place; return how many were inlined."""
    if unit.get('_qpel_facades') is not None:
        return unit['_qpel_facades']  # clang_ast caches units: each is rewritten once
    methods = facade_methods(unit)
    count = 0
    if methods:
        locals_ = {}  # clang id of a local facade object (`BlockScanT scan(temp_storage);`) -> its VarDecl
        for node in walk(unit):
            if node.get('kind') == 'VarDecl' and node.get('init') == 'call' and node.get('inner'):
                locals_[node['id']] = node

        def inline(call):
            nonlocal count
            member = call['inner'][0]
            method = methods[member['referencedMemberDecl']]
            name = method.get('name', '?')
            base = member['inner'][0] if member.get('inner') else {}
            peeled = base
            while peeled.get('kind') in WRAPPERS and peeled.get('inner'):
                peeled = peeled['inner'][0]
            storage = None
            if peeled.get('kind') == 'DeclRefExpr' and peeled.get('referencedDecl', {}).get('id') in locals_:
                local = locals_[peeled['referencedDecl']['id']]
                storage = constructed_from(local['inner'][0])
                if storage is not None:
                    local['_qpel_facade'] = True  # the object itself is not a qpel local
            else:
                storage = constructed_from(base)
            if storage is None:
                return dict(call, _facade_error=f'{name}: a library object is used where it is built from its '
                                                'TempStorage, or as a local built from it')
            params = [p['id'] for p in method.get('inner', ()) if p.get('kind') == 'ParmVarDecl']
            args = call['inner'][1:]
            if len(args) != len(params) or any(a.get('kind') == 'CXXDefaultArgExpr' for a in args):
                return dict(call, _facade_error=f'{name}: default arguments are not in qpel\'s library subset')
            body = next((c for c in method.get('inner', ()) if c.get('kind') == 'CompoundStmt'), None)
            if body is None:
                return dict(call, _facade_error=f'{name} with these arguments is not in qpel\'s CUB subset '
                                                '(LIBRARIES.md)')
            statements = body.get('inner', ())
            if len(statements) != 1:
                return dict(call, _facade_error=f'{name} is not a forwarding method of qpel\'s library headers')
            statement = statements[0]
            if statement.get('kind') == 'ReturnStmt':
                statement = statement['inner'][0]
            binding = dict(zip(params, args))
            uses = {}

            def substitute(node):
                kind = node.get('kind')
                if kind == 'DeclRefExpr' and node.get('referencedDecl', {}).get('id') in binding:
                    ident = node['referencedDecl']['id']
                    uses[ident] = uses.get(ident, 0) + 1
                    return copy.deepcopy(binding[ident])
                if kind == 'MemberExpr' and node.get('inner') and node['inner'][0].get('kind') == 'CXXThisExpr':
                    return copy.deepcopy(storage)
                if kind == 'CXXThisExpr':
                    raise LookupError
                out = dict(node)
                if node.get('inner'):
                    out['inner'] = [substitute(c) if isinstance(c, dict) else c for c in node['inner']]
                return out
            try:
                inlined = substitute(statement)
            except LookupError:
                return dict(call, _facade_error=f'{name} uses its object beyond the storage it was built from')
            if any(uses.get(p, 0) > 1 and has_effects(binding[p]) for p in params):
                return dict(call, _facade_error=f'{name}: an argument with side effects would run twice')
            inlined['_facade'] = name
            count += 1
            return inlined

        def rewrite(node):
            inner = node.get('inner')
            if inner:
                for i, child in enumerate(inner):
                    if isinstance(child, dict):
                        inner[i] = rewrite(child)
            if node.get('kind') == 'CXXMemberCallExpr' and node.get('inner') and \
                    node['inner'][0].get('kind') == 'MemberExpr' and \
                    node['inner'][0].get('referencedMemberDecl') in methods:
                return inline(node)
            return node
        for node in unit.get('inner', ()):
            rewrite(node)
    unit['_qpel_facades'] = count
    return count
