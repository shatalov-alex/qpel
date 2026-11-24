"""Cooperative groups read as the calling thread alone, before import.

Coalesced groups. `cg::coalesced_threads()` is the set of a warp's threads that happen to run together
at that point. CUDA promises nothing about which threads that is: since Volta each thread may be
scheduled on its own, and a group of only the calling thread is one of the executions the original
allows. qpel reads every such group that way (gsplat's spherical-harmonics gradients combine over
`labeled_partition(coalesced_threads(), id)` before one thread of each label adds them atomically):

- `auto g = cg::coalesced_threads();` and `auto h = cg::labeled_partition(g, label);` (or
  `binary_partition`) of such a group declare nothing;
- `cg::reduce(h, v, op)` and `cg::inclusive_scan(h, v, op)` are v, the combination of v alone;
- `h.thread_rank()` and `h.meta_group_rank()` are 0, `h.size()` and `h.num_threads()` are 1.

The results are those of the original when its warp runs that thread alone; sums gsplat then adds
atomically are added one thread at a time instead, which changes only the order of float additions
an atomic sum leaves open anyway.

Labeled partitions of a warp. `cg::labeled_partition(cg::tiled_partition<32>(cg::this_thread_block()),
label)` is every thread of the warp with the caller's label, as CUDA defines it, not a scheduling
choice. gsplat's projection gradients sum a thread's gradients over it (`warpSum(v_mean, group)`, a
helper of `cg::reduce(group, x, cg::plus<float>())`), and then the group's rank-0 thread adds the sums
atomically. Read as the calling thread alone, every thread adds its own gradients instead: the
output receives the same sum, its float additions in another order. qpel reads the group that way
only where that holds, which this pass checks:

- the group is used only to sum (`cg::reduce` with `cg::plus`, as `x = cg::reduce(h, x, plus)`, or a
  call of a helper doing only that to its reference parameters, such as gsplat's `warpSum`), and
  in conditions `h.thread_rank() == 0` of `if`s without `else`;
- the summed locals are read after the sum only inside such an `if` of the same group, and there
  only as the value of an atomic add, alone or in sums and differences of summed values (a function
  of them that is a sum, so adding it once per thread adds it for the group);
- such an `if` holds only those atomic adds, loops of them and moves of pointer parameters
  (`v_means += k`), and the sum is not in a loop.

A function using either kind of group any other way is left as it is, and the importer reports it.
The CPU simulator's cooperative_groups.h gives originals coalesced groups of the calling thread, and
labeled partitions of a warp as CUDA defines them, summed across the warp's threads.
"""
from .statics import walk, _body

GROUP_TYPES = ('cooperative_groups::coalesced_group',)
MAKERS = {'coalesced_threads'}
PARTITIONS = {'labeled_partition', 'binary_partition'}
COMBINERS = {'reduce', 'inclusive_scan'}
CONSTANTS = {'thread_rank': 0, 'meta_group_rank': 0, 'size': 1, 'num_threads': 1}
ATOMIC_ADDS = {'gpuAtomicAdd', 'atomicAdd', 'atomicAdd_system'}
WRAPPERS = ('ImplicitCastExpr', 'ParenExpr', 'ExprWithCleanups', 'ConstantExpr', 'MaterializeTemporaryExpr',
            'CXXBindTemporaryExpr', 'CXXFunctionalCastExpr', 'CXXConstructExpr')
LOOPS = ('ForStmt', 'WhileStmt', 'DoStmt', 'CXXForRangeStmt')


def _peel(node):
    while node.get('kind') in WRAPPERS[:6] and node.get('inner'):
        node = node['inner'][0]
    return node


def _callee(call):
    """The name of the function a CallExpr calls, else None."""
    if call.get('kind') != 'CallExpr' or not call.get('inner'):
        return None
    target = _peel(call['inner'][0])
    return target.get('referencedDecl', {}).get('name') if target.get('kind') == 'DeclRefExpr' else None


def _callee_id(call):
    target = _peel(call['inner'][0]) if call.get('kind') == 'CallExpr' and call.get('inner') else {}
    return target.get('referencedDecl', {}).get('id') if target.get('kind') == 'DeclRefExpr' else None


def _type(decl):
    spelled = decl.get('type', {})
    return ' '.join(spelled.get(k, '') for k in ('desugaredQualType', 'qualType'))


def _is_group(decl):
    spelled = decl.get('type', {})
    return any(spelled.get(k, '').replace('const ', '').replace('&', '').strip() in GROUP_TYPES
               for k in ('desugaredQualType', 'qualType'))


def _ref(node):
    """The declaration id a (peeled) DeclRefExpr names, else None."""
    node = _peel(node)
    return node.get('referencedDecl', {}).get('id') if node.get('kind') == 'DeclRefExpr' else None


def _literal(value, like):
    return {'kind': 'IntegerLiteral', 'type': {'qualType': 'unsigned int'}, 'valueCategory': 'prvalue',
            'value': str(value), **{k: like[k] for k in ('range', 'loc') if k in like}}


def _is_plus(op):
    return 'plus' in _type(_peel(op)) or 'plus' in str(_peel(op).get('type', {}))


def _root(node):
    """The declaration an lvalue path (members, subscripts, glm's operator[]) starts at, else None."""
    node = _peel(node)
    while True:
        kind = node.get('kind')
        if kind == 'DeclRefExpr':
            return node.get('referencedDecl', {}).get('id')
        if kind in ('MemberExpr', 'ArraySubscriptExpr') and node.get('inner'):
            node = _peel(node['inner'][0])
        elif kind == 'CXXOperatorCallExpr' and len(node.get('inner', ())) >= 2 and \
                _peel(node['inner'][0]).get('referencedDecl', {}).get('name') == 'operator[]':
            node = _peel(node['inner'][1])
        else:
            return None


def _shape(node):
    """A comparable spelling of an expression tree (kinds, names, operators, literals)."""
    node = _peel(node)
    head = (node.get('kind'), node.get('name'), node.get('opcode'), node.get('value'),
            node.get('referencedDecl', {}).get('id'))
    return (head, tuple(_shape(c) for c in node.get('inner', ()) or () if isinstance(c, dict)))


class _Sums:
    """Helpers that only sum reference parameters over a group parameter (gsplat's warpSum)."""

    def __init__(self, unit):
        self.functions = {f['id']: f for f in walk(unit) if f.get('kind') in ('FunctionDecl', 'CXXMethodDecl')}
        self.known = {}

    def summed(self, call, group_arg):
        """The argument positions a call of a sum helper sums (other than its group), else None."""
        function = self.functions.get(_callee_id(call))
        if function is None or _body(function) is None or not self.helper(function['id']):
            return None
        return self.helper(function['id'])[1] if self.helper(function['id'])[0] == group_arg else None

    def helper(self, fid):
        """(group parameter position, positions of summed parameters) for a sum helper, else None."""
        if fid in self.known:
            return self.known[fid]
        self.known[fid] = None  # recursion guard
        function = self.functions[fid]
        params = [p for p in function.get('inner', ()) if p.get('kind') == 'ParmVarDecl']
        groups = [i for i, p in enumerate(params) if _is_group(p)]
        if len(groups) != 1:
            return None
        group = params[groups[0]]['id']
        values = {p['id']: i for i, p in enumerate(params) if i != groups[0] and p.get('type', {}).get('qualType', '').endswith('&')}
        body = _body(function)
        for statement in body.get('inner', ()):
            statement = _peel(statement)
            if statement.get('kind') == 'BinaryOperator' and statement.get('opcode') == '=':
                target, value = statement['inner']
                value = _peel(value)
                if not (_callee(value) == 'reduce' and len(value['inner']) == 4 and _ref(value['inner'][1]) == group
                        and _is_plus(value['inner'][3]) and _shape(value['inner'][2]) == _shape(target)
                        and _root(target) in values):
                    return None
            elif statement.get('kind') == 'CallExpr':
                inner = self.helper(_callee_id(statement)) if _callee_id(statement) in self.functions else None
                args = statement['inner'][1:]
                if inner is None or _ref(args[inner[0]]) != group or \
                        any(_root(args[i]) not in values for i in inner[1]) or len(args) != len(inner[1]) + 1:
                    return None
            else:
                return None
        self.known[fid] = (groups[0], tuple(sorted(values.values())))
        return self.known[fid]


def _parents(body):
    parents = {}
    for node in walk(body):
        for child in node.get('inner', ()) or ():
            if isinstance(child, dict):
                parents[id(child)] = node
    return parents


def _statement(node, parents):
    """The statement of a CompoundStmt that holds node, else None."""
    while id(node) in parents:
        parent = parents[id(node)]
        if parent.get('kind') == 'CompoundStmt':
            return node
        node = parent
    return None


def _after(statement, parents, body):
    """The statements that run after one (the rest of each enclosing block), or None inside a loop."""
    out, node = [], statement
    while node is not body:
        parent = parents.get(id(node))
        if parent is None or parent.get('kind') in LOOPS:
            return None
        if parent.get('kind') == 'CompoundStmt':
            siblings = parent['inner']
            out += siblings[next(i for i, s in enumerate(siblings) if s is node) + 1:]
        node = parent
    return out


def _rank_zero(statement, groups):
    """The group of `if (h.thread_rank() == 0) ...` without else, else None."""
    if statement.get('kind') != 'IfStmt' or statement.get('hasElse') or len(statement.get('inner', ())) != 2:
        return None
    condition = _peel(statement['inner'][0])
    if condition.get('kind') != 'BinaryOperator' or condition.get('opcode') != '==':
        return None
    rank, zero = (_peel(c) for c in condition['inner'])
    if rank.get('kind') == 'IntegerLiteral':
        rank, zero = zero, rank
    if zero.get('kind') != 'IntegerLiteral' or zero.get('value') != '0' or rank.get('kind') != 'CXXMemberCallExpr':
        return None
    member = rank['inner'][0]
    if member.get('kind') != 'MemberExpr' or member.get('name') != 'thread_rank':
        return None
    group = _ref(member['inner'][0])
    return group if group in groups else None


def _linear(node, summed):
    """True when node is a sum or difference of summed lvalues (each a path from a summed local)."""
    node = _peel(node)
    if node.get('kind') == 'BinaryOperator' and node.get('opcode') in '+-':
        return all(_linear(c, summed) for c in node['inner'])
    return _root(node) in summed


def _adds_only(node, summed, pointers):
    """True when a rank-0 block holds only atomic adds of summed values, loops of them and moves of
    pointer parameters."""
    kind = node.get('kind')
    if kind in ('CompoundStmt', 'AttributedStmt'):
        return all(_adds_only(c, summed, pointers) for c in node.get('inner', ()) if not c.get('kind', '').endswith('Attr'))
    if kind == 'ForStmt':
        *head, loop_body = node['inner']
        return not _mentions(head, summed) and _adds_only(loop_body, summed, pointers)
    if kind in ('CompoundAssignOperator', 'BinaryOperator') and node.get('opcode') in ('+=', '-=', '='):
        return _ref(node['inner'][0]) in pointers and not _mentions(node['inner'][1:], summed)
    node = _peel(node)
    kind = node.get('kind')
    if kind == 'CallExpr' and _callee(node) in ATOMIC_ADDS and len(node['inner']) == 3:
        return not _mentions([node['inner'][1]], summed) and _linear(node['inner'][2], summed)
    if kind == 'NullStmt':
        return True
    return False


def _mentions(nodes, decls):
    return any(n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') in decls
               for top in nodes if isinstance(top, dict) for n in walk(top))


def _note(function, tiles):
    name = function.get('name')
    if tiles:
        return (f'{name}: its labeled partitions of a warp are read as the calling thread alone: each thread adds '
                'its own values atomically rather than one thread of each label adding the label\'s sum, the '
                'same sums with their float additions in another order (frontends/coalesced.py)')
    return (f'{name}: its coalesced group of threads is the calling thread alone, one of the executions CUDA '
            'allows (a reduction over it is the value itself, its thread rank 0; frontends/coalesced.py)')


def coalesced_singletons(unit):
    """Rewrite each function's groups as described above; returns (function id, adaptation) of each one changed."""
    changed, sums = [], None
    for function in walk(unit):
        if function.get('kind') not in ('FunctionDecl', 'CXXMethodDecl') or _body(function) is None:
            continue
        if '_qpel_coalesced' in function:  # rewritten by an earlier import of this (cached) unit
            changed.append((function['id'], function['_qpel_coalesced']))
            continue
        body = _body(function)
        groups, tiles, kinds = {}, {}, {}  # VarDecl id -> its DeclStmt; tile ids; group id -> 'coalesced' or 'tile'
        for statement in walk(body):
            if statement.get('kind') != 'DeclStmt':
                continue
            decls = statement.get('inner', ())
            if len(decls) != 1 or decls[0].get('kind') != 'VarDecl' or not decls[0].get('inner'):
                continue
            init = _peel(decls[0]['inner'][0])
            name = _callee(init)
            if 'thread_block_tile<32' in _type(decls[0]) and name == 'tiled_partition' and len(init['inner']) == 2 \
                    and _callee(_peel(init['inner'][1])) == 'this_thread_block':
                tiles[decls[0]['id']] = statement
            elif not _is_group(decls[0]):
                continue
            elif name in MAKERS and len(init['inner']) == 1:
                groups[decls[0]['id']] = statement
                kinds[decls[0]['id']] = 'coalesced'
            elif name in PARTITIONS and len(init['inner']) >= 2:
                parent = _ref(init['inner'][1])
                if parent in groups or (parent in tiles and name == 'labeled_partition'):
                    groups[decls[0]['id']] = statement
                    kinds[decls[0]['id']] = kinds.get(parent, 'tile')
        if not groups:
            continue
        if sums is None:
            sums = _Sums(unit)
        parents = _parents(body)
        # every use must be one this pass rewrites
        replaced, removed, allowed, summed, combines = [], [], set(), {}, {}  # summed: local id -> group
        ok = True
        for node in walk(body):
            name = _callee(node)
            group = _ref(node['inner'][1]) if name and len(node.get('inner', ())) >= 2 else None
            if name in PARTITIONS and (group in groups or group in tiles):
                allowed.add(id(_peel(node['inner'][1])))
            elif name in COMBINERS and group in groups:
                allowed.add(id(_peel(node['inner'][1])))
                if len(node['inner']) != 4:
                    ok = False
                elif kinds[group] == 'coalesced':
                    replaced.append((node, node['inner'][2]))
                else:  # x = reduce(h, x, plus) as a statement
                    statement = _statement(node, parents)
                    assign = _peel(statement) if statement else {}
                    if name != 'reduce' or not _is_plus(node['inner'][3]) or assign.get('kind') != 'BinaryOperator' \
                            or assign.get('opcode') != '=' or _peel(assign['inner'][1]) is not node \
                            or _shape(assign['inner'][0]) != _shape(node['inner'][2]) or _root(node['inner'][2]) is None:
                        ok = False
                        continue
                    removed.append(statement)
                    summed.setdefault(_root(node['inner'][2]), set()).add(group)
                    combines.setdefault(group, []).append(statement)
            elif name and node.get('kind') == 'CallExpr':
                args = node['inner'][1:]
                spots = [i for i, a in enumerate(args) if _ref(a) in groups]
                if not spots:
                    continue
                positions = sums.summed(node, spots[0]) if len(spots) == 1 else None
                statement = _statement(node, parents)
                if positions is None or statement is None or _peel(statement) is not node:
                    ok = False
                    continue
                group = _ref(args[spots[0]])
                allowed.add(id(_peel(args[spots[0]])))
                removed.append(statement)
                if kinds[group] == 'tile':
                    for i in positions:
                        if _root(args[i]) is None:
                            ok = False
                        summed.setdefault(_root(args[i]), set()).add(group)
                    combines.setdefault(group, []).append(statement)
            elif node.get('kind') == 'CXXMemberCallExpr' and len(node.get('inner', ())) == 1:
                member = node['inner'][0]
                group = _ref(member['inner'][0]) if member.get('kind') == 'MemberExpr' and member.get('inner') else None
                if group in groups and member.get('name') in CONSTANTS:
                    if kinds[group] == 'tile' and member['name'] != 'thread_rank':
                        continue  # refused below: a use not allowed
                    allowed.add(id(_peel(member['inner'][0])))
                    replaced.append((node, _literal(CONSTANTS[member['name']], node)))
        uses = [n for n in walk(body) if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') in groups | tiles.keys()]
        if not ok or any(id(n) not in allowed for n in uses) or not self_check(function, body, parents, kinds, summed, combines):
            continue
        for node, value in replaced:
            value = dict(value)
            node.clear()
            node.update(value)
        statements = {id(s) for s in list(groups.values()) + list(tiles.values()) + removed}
        for block in walk(body):
            if isinstance(block.get('inner'), list):
                block['inner'] = [c for c in block['inner'] if id(c) not in statements]
        function['_qpel_coalesced'] = _note(function, any(k == 'tile' for k in kinds.values()))
        changed.append((function['id'], function['_qpel_coalesced']))
    return changed


def self_check(function, body, parents, kinds, summed, combines):
    """For labeled partitions of a warp: the conditions in the module docstring, past the uses."""
    tile_groups = {g for g, k in kinds.items() if k == 'tile'}
    if not tile_groups:
        return True
    pointers = {p['id'] for p in function.get('inner', ()) if p.get('kind') == 'ParmVarDecl'
                and '*' in p.get('type', {}).get('qualType', '')}
    local_ids = {d['id'] for d in walk(body) if d.get('kind') == 'VarDecl'}
    zones = {}  # id(sum statement) -> ids of the nodes that run after it
    for statements in combines.values():
        for statement in statements:
            rest = _after(statement, parents, body)
            if rest is None:
                return False
            zones[id(statement)] = {id(n) for top in rest for n in walk(top)}
    owner = {}  # summed local -> its one group
    for v, gs in summed.items():
        if gs & tile_groups:
            if len(gs) != 1 or v not in local_ids:
                return False
            owner[v] = next(iter(gs))
    sums_of = {}  # summed local -> its sum statements
    for group, statements in combines.items():
        for statement in statements:
            for v in {n.get('referencedDecl', {}).get('id') for n in walk(statement) if n.get('kind') == 'DeclRefExpr'}:
                if owner.get(v) == group:
                    sums_of.setdefault(v, []).append(statement)
    in_blocks = {}
    for block in (s for s in walk(body) if _rank_zero(s, tile_groups)):
        group = _rank_zero(block, tile_groups)
        mine = {v for v, g in owner.items() if g == group}
        if not _adds_only(block['inner'][1], mine, pointers):
            return False
        read = {n.get('referencedDecl', {}).get('id') for n in walk(block) if n.get('kind') == 'DeclRefExpr'} & mine
        if any(not any(id(block) in zones[id(s)] for s in sums_of.get(v, ())) for v in read):
            return False  # read before its sum
        for n in walk(block):
            in_blocks[id(n)] = group
    for v, statements in sums_of.items():
        for statement in statements:
            zone = zones[id(statement)]
            if any(id(n) in zone and n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == v
                   and in_blocks.get(id(n)) != owner[v] for n in walk(body)):
                return False
    return True
