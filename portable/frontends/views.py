"""Views of a kernel's dynamic shared memory, and helper parameters left unused, read before import.

A library kernel often declares one block of dynamic shared memory and carves it up:

    extern __shared__ char smem_[];
    auto& smem_load = reinterpret_cast<typename Ktraits::BlockLoadT::TempStorage&>(smem_);
    auto& smem_scan = *reinterpret_cast<typename Ktraits::BlockScanT::TempStorage*>(smem_ + kSmemIOSize);
    scan_t *smem_running_prefix = reinterpret_cast<scan_t *>(smem_ + Ktraits::kSmemSize);

(mamba's selective scan). qpel's shared memory is typed, so each view becomes shared memory of its
own: a reference to an array type (a CUB TempStorage, `T[N]` in qpel's CUB subset) a `__shared__
T name[N]`, and a pointer `T *name` an `extern __shared__ T name[]`, whose length the import is
given like any dynamic shared memory (import_source(shared={'smem_running_prefix': 256})). The
byte block itself goes. This keeps a program's results when its views are used as separate
memories, which CUB's reuse rules require (a TempStorage is reused only after a barrier, and each
use writes what it then reads); a program that wrote through one view and read the same bytes
through another would not be read correctly, and is not recognised: every use of the block must be
the initializer of one view.

After the facades of qpel's CUB subset are inlined (facades.py), a load or store no longer touches
its TempStorage, so a helper that only passed it on (`load_input(..., smem_load, ...)`) has a
parameter it never reads. Such a parameter, of a reference type qpel has no value for, is removed,
and so is the argument at every call.
"""
import re

from .statics import walk, _body, _peel, _spelling, CASTS

ARRAY_REF = re.compile(r'(?:const\s+)?(.*?\S)\s*(?:\(&\))?\[([0-9]+)\]\s*&?')
POINTER = re.compile(r'(?:const\s+)?(.*?\S)\s*\*\s*(?:__restrict__|__restrict)?')
UNSIZED = re.compile(r'(?:const\s+)?(?:unsigned\s+)?char\s*\[\]')


def _attributes(decl):
    return {c.get('kind') for c in decl.get('inner', ()) if isinstance(c, dict)}


def _callee_id(call):
    target = _peel(call['inner'][0]) if call.get('inner') else {}
    return target.get('referencedDecl', {}).get('id') if target.get('kind') == 'DeclRefExpr' else None


def dead_parameters(unit):
    """Remove the unused reference parameters (a TempStorage passed on to a facade that no longer
    reads it) of device functions, and their arguments at every call. Returns the removed names."""
    removed = []
    functions = [n for n in walk(unit) if n.get('kind') == 'FunctionDecl' and _body(n) is not None
                 and 'CUDAGlobalAttr' not in _attributes(n)]
    for function in functions:
        body = _body(function)
        params = [c for c in function['inner'] if c.get('kind') == 'ParmVarDecl']
        used = {n.get('referencedDecl', {}).get('id') for n in walk(body) if n.get('kind') == 'DeclRefExpr'}
        dead = [i for i, p in enumerate(params) if p['id'] not in used and '&' in p.get('type', {}).get('qualType', '')
                and 'TempStorage' in p['type']['qualType']]
        if not dead:
            continue
        calls = [n for n in walk(unit) if n.get('kind') == 'CallExpr' and _callee_id(n) == function['id']]
        if any(len(c['inner']) - 1 != len(params) for c in calls):
            continue  # default arguments or the like: left as it is
        for call in calls:
            call['inner'] = [call['inner'][0]] + [a for i, a in enumerate(call['inner'][1:]) if i not in dead]
        drop = {params[i]['id'] for i in dead}
        function['inner'] = [c for c in function['inner'] if c.get('id') not in drop]
        removed.extend(f'{function.get("name")}({params[i]["name"]})' for i in dead)
    return removed


def _view_source(init, block):
    """The view's cast when init is `reinterpret_cast<X&>(block)`, `*reinterpret_cast<X*>(block + offset)`
    or `reinterpret_cast<X*>(block + offset)`; else None."""
    node = _peel(init)
    if node.get('kind') == 'UnaryOperator' and node.get('opcode') == '*' and node.get('inner'):
        node = _peel(node['inner'][0])
    if node.get('kind') not in CASTS or not node.get('inner'):
        return None
    operand = _peel(node['inner'][0])
    if operand.get('kind') == 'BinaryOperator' and operand.get('opcode') == '+':
        operand = _peel(operand['inner'][0])
    if operand.get('kind') == 'DeclRefExpr' and operand.get('referencedDecl', {}).get('id') == block:
        return node
    return None


def shared_views(unit):
    """Give each view of a kernel's `extern __shared__ char` block shared memory of its own (see the
    module's description). Returns the adaptations made, one line per kernel."""
    notes = []
    for function in walk(unit):
        if function.get('kind') != 'FunctionDecl' or 'CUDAGlobalAttr' not in _attributes(function):
            continue
        body = _body(function)
        if body is None:
            continue
        blocks = [d for d in walk(body) if d.get('kind') == 'VarDecl' and 'CUDASharedAttr' in _attributes(d)
                  and UNSIZED.fullmatch(d.get('type', {}).get('qualType', '').strip())]
        for block in blocks:
            uses = [n for n in walk(body) if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == block['id']]
            views = [d for d in walk(body) if d.get('kind') == 'VarDecl' and d is not block and d.get('inner')
                     and 'init' in d and _view_source(d['inner'][-1], block['id']) is not None]
            if len(views) != len(uses):
                continue  # the block is used other than as the start of a view
            plans = []  # (view, None when unused | (spelling, pointer?))
            for view in views:
                spelled = _spelling(view['type']).replace('&', ' & ').strip()
                referenced = any(n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == view['id']
                                 for n in walk(body))
                array = ARRAY_REF.fullmatch(spelled.replace(' & ', '').replace(' &', '').strip())
                pointer = POINTER.fullmatch(spelled) if '*' in spelled and '&' not in spelled else None
                if not referenced:
                    plans.append((view, None))
                elif array:
                    plans.append((view, (f'{array.group(1)}[{array.group(2)}]', False)))
                elif pointer:
                    plans.append((view, (f'{pointer.group(1)}[]', True)))
                else:
                    break  # a view qpel has no shared type for: the kernel stays as it is
            else:
                made, drop = [], {block['id']}
                for view, plan in plans:
                    if plan is None:
                        drop.add(view['id'])
                        continue
                    t, pointer = plan
                    made.append(f'{view["name"]}: {"dynamic shared " if pointer else "shared "}{t}')
                    view['type'] = {'qualType': t}
                    view['inner'] = [{'kind': 'CUDASharedAttr'}]
                    view.pop('init', None)
                    if pointer:
                        view['storageClass'] = 'extern'
                    for n in walk(body):
                        if n.get('kind') == 'DeclRefExpr' and n.get('referencedDecl', {}).get('id') == view['id']:
                            n['type'] = dict(view['type'])
                            n['referencedDecl'] = dict(n['referencedDecl'], type=dict(view['type']))
                for statement in walk(body):  # the block's declaration and the unused views' go
                    if statement.get('kind') == 'DeclStmt' and any(d.get('id') in drop for d in statement.get('inner', ())):
                        statement['inner'] = [d for d in statement['inner'] if d.get('id') not in drop]
                        if not statement['inner']:
                            statement.clear()
                            statement['kind'] = 'NullStmt'
                notes.append(f'{function.get("name")}: the views of {block["name"]} became shared memory of their own'
                             + (f' ({"; ".join(made)})' if made else ''))
    return notes
