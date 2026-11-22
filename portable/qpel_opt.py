"""Optimisation passes, kept apart from the portability lowering (ROADMAP Phase 6).

A pass reads a validated program and returns facts the lowering may use to emit faster code with
exactly the same results; it never changes the program, so the portable meaning is the IR's alone.

vector_loads: a vector built from N consecutive elements of one kernel buffer,
`vec<T, N>(b[N i], b[N i + 1], ..., b[N i + N - 1])`, as the CUDA importer reads the originals'
`reinterpret_cast<const uint2*>(b)[i]`. The lowering emits one N-wide load when the buffer's address
is aligned to the vector and N i + N - 1 does not wrap the index type, and the N element loads
otherwise: the same elements either way, so the same bits.

vector_stores: N consecutive statements of one block storing N consecutive elements of one kernel
buffer, `b[N i] = v0; b[N i + 1] = v1; ...; b[N i + N - 1] = v(N-1);`, as the CUDA importer writes
an original's `int4* bboxes; bboxes[i] = v`. The lowering emits one N-wide store under the same
conditions as a whole-vector load, and the N element stores otherwise. The wide store evaluates every
value before it stores any, so the values and the index must be pure and read no memory (locals,
parameters, literals and arithmetic on them), and of the buffer's element type (no conversion).

reused_loads: a loop that reads one atomic location in its condition and again, first thing, in its body,
`while (atomic.load(a) != n) { n = atomic.load(a); ... }`, the union-find of SAM 2's connected components
and of every disjoint-set forest. The two relaxed loads have nothing between them but the rest of the
condition, which reads no memory, so reading once and using the value twice is one of the executions
the relaxed order already allows (no other write can be required to fall between them). The lowering
reads the location into a local in the condition and assigns that local in the body; nvcc's plain loads
in the original were merged the same way, and a volatile pair could not be.

matrix_pairs: two statements of one block, `c = mma(a_n, b_n, ... mma(a_1, b_1, c))` and
`c' = mma(a'_n, b_n, ... mma(a'_1, b_1, c'))`, f16 multiply chains with the same B at every step, as the
CUDA importer writes a 16 x 16 WMMA fragment's two row blocks. CUDA's mma.sync.m16n8k8 multiplies a
16 x 8 A by an 8 x 8 B, and each row of the result is that row of A times B alone: one instruction with
a in rows 0 to 7 and a' in rows 8 to 15 gives both results, each element the same as from its own
instruction with the other rows zero. The operands are locals (`x` or `x[k]`, k a literal), the second
statement reads nothing the first or a statement between them writes, and the statements between
neither read nor write what the second writes, so evaluating both chains at the first statement's
place, before either assignment, is the same execution. Metal and the CPU simulator multiply each.
"""
import json
import re

from qpel_types import is_buffer, access, element, term, is_matrix, matrix_element

LITERAL = re.compile(r'(\d+)u?')


def literal_int(node):
    """The value of an integer literal node, or None."""
    a = node.get('attributes', {})
    if node['kind'] in ('Literal', 'I32Literal', 'Constant'):
        found = LITERAL.fullmatch(str(a.get('value', '')))
        return int(found.group(1)) if found else None
    return None


class Program:
    def __init__(self, program):
        self.nodes = {n['id']: n for n in program['nodes']}

    def child(self, node, role):
        values = node['children'].get(role, [])
        return self.nodes[values[0]] if len(values) == 1 else None

    def shape(self, node):
        """A canonical form of an expression tree: equal for the same computation of the same names."""
        return json.dumps([node['kind'], node.get('attributes', {}), node.get('links', {}),
                           {role: [self.shape(self.nodes[i]) for i in ids] for role, ids in sorted(node['children'].items())}],
                          sort_keys=True)

    def element_index(self, index, length):
        """(x, k) when index is (x * length) + k, or x * length (k = 0), else None."""
        if index['kind'] == 'Binary' and index['attributes'].get('operator') == '*' and \
                literal_int(self.child(index, 'right')) == length:
            return self.child(index, 'left'), 0
        if index['kind'] != 'Binary' or index['attributes'].get('operator') != '+':
            return None
        scaled, k = self.child(index, 'left'), literal_int(self.child(index, 'right'))
        if k is None or scaled['kind'] != 'Binary' or scaled['attributes'].get('operator') != '*':
            return None
        if literal_int(self.child(scaled, 'right')) != length:
            return None
        return self.child(scaled, 'left'), k


def vector_loads(program, types):
    """{VectorValue id: (buffer Name node, index node)} for the vectors read whole from a kernel buffer."""
    p = Program(program)
    kernel_buffers = set()
    for n in p.nodes.values():
        if n['kind'] == 'Function' and n['attributes'].get('isKernel'):
            for pid in n['children'].get('parameters', []):
                t = types.get(pid) or term(p.nodes[p.nodes[pid]['children']['type'][0]])
                if is_buffer(t) and access(t) in ('read', 'write'):
                    kernel_buffers.add(pid)
    found = {}
    for n in p.nodes.values():
        if n['kind'] != 'VectorValue' or n['attributes']['length'] not in (2, 4):
            continue
        length, elements = n['attributes']['length'], [p.nodes[i] for i in n['children'].get('elements', [])]
        if len(elements) != length or any(e['kind'] != 'Index' for e in elements):
            continue
        bases = [p.child(e, 'base') for e in elements]
        if any(b['kind'] != 'Name' or b['links'].get('declaration') not in kernel_buffers for b in bases) or \
                len({b['links']['declaration'] for b in bases}) != 1:
            continue
        parts = [p.element_index(p.child(e, 'index'), length) for e in elements]
        if None in parts or [k for _, k in parts] != list(range(length)) or len({p.shape(x) for x, _ in parts}) != 1:
            continue
        if types.get(parts[0][0]['id']) not in ('u32', 'i32', 'u64', 'i64'):
            continue
        found[n['id']] = (bases[0], parts[0][0])
    return found


PURE = set('Name Literal I32Literal F32Literal Constant Cast I32Cast U32Cast U8Cast F32Cast F16Cast F32Fma Binary Unary '
           'Member Select Minimum Maximum Math Bitcast GridIndex ThreadIndex GroupIndex LocalIndex GroupSize GroupCount '
           'SubgroupLane SubgroupIndex SubgroupSize VectorValue BufferPresent'.split())
WIDE = ('u32', 'i32', 'f32')  # the elements of the vectors both backends store whole


def pure(p, node):
    """True when evaluating node reads no memory and has no effect (so it can move before a store)."""
    return node['kind'] in PURE and all(pure(p, p.nodes[i]) for ids in node['children'].values() for i in ids)


def vector_stores(program, types):
    """{id of the first Assign: (buffer Name node, index node, [value nodes], N, element)} for the N
    consecutive element stores of one block that the lowering may emit as one N-wide store."""
    p = Program(program)
    buffers = {}
    for n in p.nodes.values():
        if n['kind'] == 'Function' and n['attributes'].get('isKernel'):
            for pid in n['children'].get('parameters', []):
                t = types.get(pid) or term(p.nodes[p.nodes[pid]['children']['type'][0]])
                if is_buffer(t) and access(t) == 'write' and element(t) in WIDE:
                    buffers[pid] = element(t)
    found = {}

    def store(statement):
        """(declaration, Name node, index node, value node) of `b[index] = value` into a kernel buffer, or None."""
        if statement['kind'] != 'Assign':
            return None
        target, value = p.child(statement, 'target'), p.child(statement, 'value')
        if target['kind'] != 'Index' or p.child(target, 'base')['kind'] != 'Name':
            return None
        base = p.child(target, 'base')
        if base['links'].get('declaration') not in buffers:
            return None
        return base['links']['declaration'], base, p.child(target, 'index'), value

    for block in p.nodes.values():
        if block['kind'] != 'Block':
            continue
        statements = [p.nodes[i] for i in block['children'].get('statements', [])]
        at = 0
        while at < len(statements):
            taken = 0
            for length in (4, 2):
                group = [store(s) for s in statements[at:at + length]]
                if len(group) < length or None in group or len({g[0] for g in group}) != 1:
                    continue
                element_type = buffers[group[0][0]]
                parts = [p.element_index(g[2], length) for g in group]
                if None in parts or [k for _, k in parts] != list(range(length)) or len({p.shape(x) for x, _ in parts}) != 1:
                    continue
                x = parts[0][0]
                if types.get(x['id']) not in ('u32', 'i32', 'u64', 'i64') or not pure(p, x):
                    continue
                values = [g[3] for g in group]
                if any(types.get(v['id']) != element_type or not pure(p, v) for v in values):
                    continue
                found[statements[at]['id']] = (group[0][1], x, values, length, element_type)
                taken = length
                break
            at += taken or 1
    return found


def reused_loads(program, types):
    """{While id: (condition AtomicLoad id, body AtomicLoad id, its value type)} for the loops whose
    condition and first body statement read the same atomic location (see reused_loads above)."""
    p = Program(program)
    found = {}
    for n in p.nodes.values():
        if n['kind'] != 'While':
            continue
        condition, body = p.child(n, 'condition'), p.child(n, 'body')
        if condition is None or body is None or condition['kind'] != 'Binary' or body['kind'] != 'Block':
            continue
        sides = [p.child(condition, 'left'), p.child(condition, 'right')]
        loads = [s for s in sides if s is not None and s['kind'] == 'AtomicLoad']
        if len(loads) != 1:
            continue
        load = loads[0]
        other = sides[1] if sides[0] is load else sides[0]
        address = p.child(load, 'address')
        if other is None or address is None or not pure(p, other) or not pure(p, address):
            continue
        statements = body['children'].get('statements', [])
        if not statements:
            continue
        first = p.nodes[statements[0]]
        if first['kind'] != 'Assign' or p.child(first, 'target')['kind'] != 'Name':
            continue
        again = p.child(first, 'value')
        if again['kind'] != 'AtomicLoad' or p.shape(again) != p.shape(load) or load['id'] not in types:
            continue
        found[n['id']] = (load['id'], again['id'], types[load['id']])
    return found


def matrix_pairs(program, types):
    """{id of the first Assign: (id of the second, [(a, a', b) per step, innermost first], c, c')} for two
    f16 multiply chains of one block that the lowering may emit as one chain over a pair of accumulators
    (see matrix_pairs above)."""
    p = Program(program)

    def slot(node):
        """(declaration, literal index or None) of a local `x` or `x[k]`, else None."""
        if node['kind'] == 'Name':
            return node['links'].get('declaration'), 'whole'
        if node['kind'] == 'Index' and p.child(node, 'base') is not None and p.child(node, 'base')['kind'] == 'Name':
            index = p.child(node, 'index')
            return p.child(node, 'base')['links'].get('declaration'), literal_int(index) if index is not None else None
        return None

    def chain(value):
        """([(a, b) innermost first], c) of mma(a_n, b_n, ... mma(a_1, b_1, c)) over f16 slots, or None."""
        steps = []
        while value is not None and value['kind'] == 'MatrixMma' and not value.get('attributes', {}).get('columns'):
            a, b = p.child(value, 'a'), p.child(value, 'b')
            if a is None or b is None or slot(a) is None or slot(b) is None or not (types.get(a['id']) and is_matrix(types[a['id']]) and matrix_element(types[a['id']]) == 'f16'):
                return None
            steps.append((a, b))
            value = p.child(value, 'c')
        if not steps or value is None or slot(value) is None:
            return None
        return steps[::-1], value

    def simple(statement):
        """(target slot, [read slots], chain) of `slot = mma chain`, or None."""
        if statement['kind'] != 'Assign':
            return None
        target = slot(p.child(statement, 'target'))
        found = chain(p.child(statement, 'value'))
        if target is None or found is None:
            return None
        steps, c = found
        return target, [slot(x) for a, b in steps for x in (a, b)] + [slot(c)], found

    def overlap(x, y):
        return x[0] == y[0] and (x[1] == y[1] or None in (x[1], y[1]) or 'whole' in (x[1], y[1]))

    found = {}
    for block in p.nodes.values():
        if block['kind'] != 'Block':
            continue
        statements = [p.nodes[i] for i in block['children'].get('statements', [])]
        forms = [simple(s) for s in statements]
        taken = set()
        for i, first in enumerate(forms):
            if first is None or i in taken:
                continue
            for j in range(i + 1, len(forms)):
                second = forms[j]
                if second is None:
                    break  # only across statements whose reads and writes are known
                if j in taken:
                    continue
                (t1, _, (s1, c1)), (t2, r2, (s2, c2)) = first, second
                between = [forms[m] for m in range(i + 1, j)]
                # the second moves up to the first: it may not read what the first or anything between writes,
                # and nothing between may read or write what it writes
                if overlap(t1, t2) or any(overlap(t1, r) for r in r2) or \
                   any(overlap(f[0], r) for f in between for r in r2) or \
                   any(overlap(t2, x) for f in between for x in [f[0]] + f[1]):
                    continue
                if len(s1) != len(s2) or any(p.shape(b1) != p.shape(b2) for (_, b1), (_, b2) in zip(s1, s2)):
                    continue
                found[statements[i]['id']] = (statements[j]['id'], [(a1, a2, b1) for (a1, b1), (a2, _) in zip(s1, s2)], c1, c2)
                taken.update((i, j))
                break
    return found
