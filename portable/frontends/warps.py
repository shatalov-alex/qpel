"""Warps of a thread block as qpel subgroups, before import.

`cg::thread_block_tile<32> warp = cg::tiled_partition<32>(block)` of the whole thread block
(`block = cg::this_thread_block()`) is the caller's warp: the 32 threads of consecutive thread ranks
that hold it. qpel's subgroup (IR version 5) is that warp on NVIDIA and the SIMD-group of 32 lanes on
Apple's GPUs, formed the same way from the thread's place in its group, so the tile's operations are
qpel's subgroup operations (gsplat's rasterizer gradients):

- `cg::reduce(warp, v, op)` is `qpel_subgroup_<op>(v)`, op `cg::plus` add, `cg::greater` max,
  `cg::less` min, `cg::bit_and`/`bit_or`/`bit_xor` and, or, xor;
- `warp.any(p)` and `warp.all(p)` are `qpel_subgroup_or((unsigned int)(p)) != 0` and `_and`;
- `warp.thread_rank()` is `qpel_subgroup_lane()`, `warp.meta_group_rank()` `qpel_subgroup_index()`,
  `warp.size()` and `warp.num_threads()` 32, `warp.sync()` `__syncwarp()`;
- a helper's parameter of the tile type (`warpSum(float &val, WarpT &warp)`) is removed, and so is
  the argument at every call: the helper's operations are on the caller's warp.

The tile's declaration goes. A function using a tile any other way (a shuffle, a ballot, a partition
of it other than coalesced.py's labeled partitions, the tile passed to something not rewritten here) is
left as it is, and the importer reports it; a helper left as it is keeps its callers as they are too.
A float sum over the warp adds in qpel's fixed order (xor 16, 8, 4, 2, 1), which CUDA leaves open for
cg::reduce.
"""
from .statics import walk, _body

TILE = 'thread_block_tile<32'
WRAPPERS = ('ImplicitCastExpr', 'ParenExpr', 'ExprWithCleanups', 'ConstantExpr', 'MaterializeTemporaryExpr',
            'CXXBindTemporaryExpr')
OPERATORS = (('bit_and', 'and'), ('bit_or', 'or'), ('bit_xor', 'xor'), ('plus', 'add'), ('greater', 'max'),
             ('less', 'min'))
METHODS = {'thread_rank': 'qpel_subgroup_lane', 'meta_group_rank': 'qpel_subgroup_index'}


def _peel(node):
    while node.get('kind') in WRAPPERS and node.get('inner'):
        node = node['inner'][0]
    return node


def _ref(node):
    node = _peel(node)
    return node.get('referencedDecl', {}).get('id') if node.get('kind') == 'DeclRefExpr' else None


def _callee(call):
    if call.get('kind') != 'CallExpr' or not call.get('inner'):
        return None
    target = _peel(call['inner'][0])
    return target.get('referencedDecl', {}).get('name') if target.get('kind') == 'DeclRefExpr' else None


def _callee_id(call):
    target = _peel(call['inner'][0]) if call.get('kind') == 'CallExpr' and call.get('inner') else {}
    return target.get('referencedDecl', {}).get('id') if target.get('kind') == 'DeclRefExpr' else None


def _type(node):
    spelled = node.get('type', {})
    return ' '.join(spelled.get(k, '') for k in ('desugaredQualType', 'qualType'))


def _where(like):
    return {k: like[k] for k in ('range', 'loc') if k in like}


def _call(name, args, result, like):
    callee = {'kind': 'DeclRefExpr', 'type': {'qualType': f'{result} ()'}, 'valueCategory': 'prvalue',
              'referencedDecl': {'id': f'qpel-warps-{name}', 'kind': 'FunctionDecl', 'name': name}, **_where(like)}
    decay = {'kind': 'ImplicitCastExpr', 'type': {'qualType': f'{result} (*)()'}, 'valueCategory': 'prvalue',
             'castKind': 'FunctionToPointerDecay', 'inner': [callee], **_where(like)}  # (as clang writes a call)
    return {'kind': 'CallExpr', 'type': {'qualType': result}, 'valueCategory': 'prvalue',
            'inner': [decay] + list(args), **_where(like)}


def _unsigned(value, like):
    return {'kind': 'IntegerLiteral', 'type': {'qualType': 'unsigned int'}, 'valueCategory': 'prvalue',
            'value': str(value), **_where(like)}


def _vote(operator, predicate, like):
    """`qpel_subgroup_<operator>((unsigned int)(predicate)) != 0u`."""
    cast = {'kind': 'CStyleCastExpr', 'type': {'qualType': 'unsigned int'}, 'valueCategory': 'prvalue',
            'castKind': 'IntegralCast', 'inner': [predicate], **_where(like)}
    return {'kind': 'BinaryOperator', 'type': {'qualType': 'bool'}, 'valueCategory': 'prvalue', 'opcode': '!=',
            'inner': [_call(f'qpel_subgroup_{operator}', [cast], 'unsigned int', like), _unsigned(0, like)], **_where(like)}


def _whole_block(arg, blocks):
    """The argument of tiled_partition is the thread block: this_thread_block() or a local holding it."""
    arg = _peel(arg)
    return _callee(arg) == 'this_thread_block' or _ref(arg) in blocks


def _plan(function, tile_params, helpers):
    """(replacements, statements to drop, call-argument positions to drop) when every use of the
    function's tiles is rewritten, else None. helpers: function id -> its tile parameter positions."""
    body = _body(function)
    blocks = {d['id'] for d in walk(body) if d.get('kind') == 'VarDecl' and d.get('inner')
              and _callee(_peel(d['inner'][0])) == 'this_thread_block'}
    tiles, drop = set(tile_params), []
    for statement in walk(body):
        if statement.get('kind') != 'DeclStmt' or len(statement.get('inner', ())) != 1:
            continue
        decl = statement['inner'][0]
        if decl.get('kind') != 'VarDecl' or TILE not in _type(decl) or not decl.get('inner'):
            continue
        init = _peel(decl['inner'][0])
        while init.get('kind') in ('CXXConstructExpr', 'CXXFunctionalCastExpr') and len(init.get('inner', ())) == 1:
            init = _peel(init['inner'][0])  # (the tile copied from tiled_partition's result)
        if _callee(init) == 'tiled_partition' and len(init['inner']) == 2 and _whole_block(init['inner'][1], blocks):
            tiles.add(decl['id'])
            drop.append(statement)
    if not tiles:
        return None
    replaced, allowed, arguments = [], set(), []  # arguments: (call, positions to remove)
    for node in walk(body):
        name = _callee(node)
        args = node.get('inner', [])[1:] if node.get('kind') == 'CallExpr' else []
        if name == 'reduce' and len(args) == 3 and _ref(args[0]) in tiles:
            spelled = _type(_peel(args[2]))
            operator = next((o for key, o in OPERATORS if key in spelled), None)
            if operator is None:
                continue
            allowed.add(id(_peel(args[0])))
            replaced.append((node, _call(f'qpel_subgroup_{operator}', [args[1]], node['type']['qualType'], node)))
        elif node.get('kind') == 'CallExpr' and any(_ref(a) in tiles for a in args):
            spots = tuple(i for i, a in enumerate(args) if _ref(a) in tiles)
            if helpers.get(_callee_id(node)) != spots:
                continue
            allowed.update(id(_peel(args[i])) for i in spots)
            arguments.append((node, spots))
        elif node.get('kind') == 'CXXMemberCallExpr' and node.get('inner'):
            member = node['inner'][0]
            if member.get('kind') != 'MemberExpr' or not member.get('inner') or _ref(member['inner'][0]) not in tiles:
                continue
            method, given = member.get('name'), node['inner'][1:]
            if method in METHODS and not given:
                value = _call(METHODS[method], [], 'unsigned int', node)
            elif method in ('size', 'num_threads') and not given:
                value = _unsigned(32, node)
            elif method == 'sync' and not given:
                value = _call('__syncwarp', [], 'void', node)
            elif method in ('any', 'all') and len(given) == 1:
                value = _vote('or' if method == 'any' else 'and', given[0], node)
            else:
                continue
            allowed.add(id(_peel(member['inner'][0])))
            replaced.append((node, value))
    uses = [n for n in walk(body) if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') in tiles
            and not any(n is s['inner'][0] for s in drop)]
    if any(id(n) not in allowed for n in uses):
        return None
    return replaced, drop, arguments


def warp_tiles(unit):
    """Rewrite each function's warps as described above; returns (function id, adaptation) of each one changed."""
    functions = [f for f in walk(unit) if f.get('kind') in ('FunctionDecl', 'CXXMethodDecl') and _body(f) is not None]
    changed = [(f['id'], f['_qpel_warps']) for f in functions if '_qpel_warps' in f]  # an earlier import of this unit
    if changed:
        return changed
    params = {f['id']: [p for p in f.get('inner', ()) if p.get('kind') == 'ParmVarDecl'] for f in functions}
    helpers = {fid: tuple(i for i, p in enumerate(ps) if TILE in _type(p)) for fid, ps in params.items()}
    helpers = {fid: spots for fid, spots in helpers.items() if spots}
    plans = {}
    while True:  # a helper that cannot be rewritten keeps its tile parameter, and its callers stay as they are
        plans = {}
        for function in functions:
            tile_params = {params[function['id']][i]['id'] for i in helpers.get(function['id'], ())}
            if any(n.get('kind') == 'DeclStmt' for n in walk(_body(function))) or tile_params:
                found = _plan(function, tile_params, helpers)
                if found is not None:
                    plans[function['id']] = found
        failed = [fid for fid in helpers if fid not in plans]
        if not failed:
            break
        for fid in failed:
            del helpers[fid]
    for function in functions:
        if function['id'] not in plans:
            continue
        replaced, drop, arguments = plans[function['id']]
        for call, spots in arguments:
            call['inner'] = [call['inner'][0]] + [a for i, a in enumerate(call['inner'][1:]) if i not in spots]
        for node, value in replaced:
            node.clear()
            node.update(value)
        statements = {id(s) for s in drop}
        for block in walk(_body(function)):
            if isinstance(block.get('inner'), list):
                block['inner'] = [c for c in block['inner'] if id(c) not in statements]
        if function['id'] in helpers:
            spots = helpers[function['id']]
            kept = [p for i, p in enumerate(params[function['id']]) if i not in spots]
            gone = {id(p) for p in params[function['id']]} - {id(p) for p in kept}
            function['inner'] = [c for c in function['inner'] if id(c) not in gone]
        function['_qpel_warps'] = (f'{function.get("name")}: its warp (cg::tiled_partition<32> of the thread block) is '
                                   'qpel\'s subgroup: cg::reduce over it a subgroup reduction (a float sum in qpel\'s '
                                   'fixed order), any/all a subgroup or/and, thread_rank() the lane (frontends/warps.py)')
        changed.append((function['id'], function['_qpel_warps']))
    return changed
