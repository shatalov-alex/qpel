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
"""
import json
import re

from qpel_types import is_buffer, access, element, term

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
