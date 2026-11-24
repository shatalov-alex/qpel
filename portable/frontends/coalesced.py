"""Cooperative groups of coalesced threads, read as the calling thread alone, before import.

`cg::coalesced_threads()` is the set of a warp's threads that happen to run together at that point.
CUDA promises nothing about which threads that is: since Volta each thread may be scheduled on its
own, and a group of only the calling thread is one of the executions the original allows. qpel reads
every such group that way (gsplat's backward kernels combine gradients over `labeled_partition(
coalesced_threads(), id)` before one thread of each label adds them atomically):

- `auto g = cg::coalesced_threads();` and `auto h = cg::labeled_partition(g, label);` (or
  `binary_partition`) of such a group declare nothing;
- `cg::reduce(h, v, op)` and `cg::inclusive_scan(h, v, op)` are v, the combination of v alone;
- `h.thread_rank()` and `h.meta_group_rank()` are 0, `h.size()` and `h.num_threads()` are 1.

The results are those of the original when its warp runs that thread alone; sums gsplat then adds
atomically are added one thread at a time instead, which changes only the order of float additions
an atomic sum leaves open anyway. A function using such a group any other way is left as it is, and
the importer reports it. The CPU simulator's cooperative_groups.h gives the original the same groups,
so the original and qpel's output compute the same values there.
"""
from .statics import walk, _body

GROUP_TYPES = ('cooperative_groups::coalesced_group',)
MAKERS = {'coalesced_threads'}
PARTITIONS = {'labeled_partition', 'binary_partition'}
COMBINERS = {'reduce', 'inclusive_scan'}
CONSTANTS = {'thread_rank': 0, 'meta_group_rank': 0, 'size': 1, 'num_threads': 1}


def _peel(node):
    while node.get('kind') in ('ImplicitCastExpr', 'ParenExpr', 'ExprWithCleanups', 'ConstantExpr',
                               'MaterializeTemporaryExpr', 'CXXBindTemporaryExpr') and node.get('inner'):
        node = node['inner'][0]
    return node


def _callee(call):
    """The name of the function a CallExpr calls, else None."""
    if call.get('kind') != 'CallExpr' or not call.get('inner'):
        return None
    target = _peel(call['inner'][0])
    return target.get('referencedDecl', {}).get('name') if target.get('kind') == 'DeclRefExpr' else None


def _is_group(decl):
    spelled = decl.get('type', {})
    return any(spelled.get(k, '').replace('const ', '').strip() in GROUP_TYPES for k in ('desugaredQualType', 'qualType'))


def _literal(value, like):
    return {'kind': 'IntegerLiteral', 'type': {'qualType': 'unsigned int'}, 'valueCategory': 'prvalue',
            'value': str(value), **{k: like[k] for k in ('range', 'loc') if k in like}}


def coalesced_singletons(unit):
    """Rewrite each function's coalesced groups as described above; returns (function id, adaptation) of each one changed."""
    changed = []
    for function in walk(unit):
        if function.get('kind') not in ('FunctionDecl', 'CXXMethodDecl') or _body(function) is None:
            continue
        if '_qpel_coalesced' in function:  # rewritten by an earlier import of this (cached) unit
            changed.append((function['id'], function['_qpel_coalesced']))
            continue
        body = _body(function)
        groups = {}  # VarDecl id -> its DeclStmt
        for statement in walk(body):
            if statement.get('kind') != 'DeclStmt':
                continue
            decls = statement.get('inner', ())
            if len(decls) != 1 or decls[0].get('kind') != 'VarDecl' or not _is_group(decls[0]) or not decls[0].get('inner'):
                continue
            init = _peel(decls[0]['inner'][0])
            name = _callee(init)
            if name in MAKERS and len(init['inner']) == 1:
                groups[decls[0]['id']] = statement
            elif name in PARTITIONS and len(init['inner']) >= 2:
                parent = _peel(init['inner'][1])
                if parent.get('kind') == 'DeclRefExpr' and parent.get('referencedDecl', {}).get('id') in groups:
                    groups[decls[0]['id']] = statement
        if not groups:
            continue
        # every use must be one this pass rewrites: a partition's parent, a combination's group, or a constant member
        replaced, allowed = [], set()
        for node in walk(body):
            name = _callee(node)
            if name in PARTITIONS | COMBINERS and len(node.get('inner', ())) >= 2:
                group = _peel(node['inner'][1])
                if group.get('kind') == 'DeclRefExpr' and group.get('referencedDecl', {}).get('id') in groups:
                    allowed.add(id(group))
                    if name in COMBINERS and len(node['inner']) == 4:
                        replaced.append((node, node['inner'][2]))
            elif node.get('kind') == 'CXXMemberCallExpr' and len(node.get('inner', ())) == 1:
                member = node['inner'][0]
                group = _peel(member['inner'][0]) if member.get('kind') == 'MemberExpr' and member.get('inner') else {}
                if group.get('kind') == 'DeclRefExpr' and group.get('referencedDecl', {}).get('id') in groups \
                        and member.get('name') in CONSTANTS:
                    allowed.add(id(group))
                    replaced.append((node, _literal(CONSTANTS[member['name']], node)))
        uses = [n for n in walk(body) if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') in groups]
        if any(id(n) not in allowed for n in uses):
            continue
        for node, value in replaced:
            value = dict(value)
            node.clear()
            node.update(value)
        statements = {id(s) for s in groups.values()}
        for block in walk(body):
            if isinstance(block.get('inner'), list):
                block['inner'] = [c for c in block['inner'] if id(c) not in statements]
        function['_qpel_coalesced'] = (f'{function.get("name")}: its coalesced group of threads is the calling thread '
                                       'alone, one of the executions CUDA allows (a reduction over it is the value itself, '
                                       'its thread rank 0; frontends/coalesced.py)')
        changed.append((function['id'], function['_qpel_coalesced']))
    return changed
