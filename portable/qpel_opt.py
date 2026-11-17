"""Optimisation passes, kept apart from the portability lowering (ROADMAP Phase 6).

A pass reads a validated program and returns facts the lowering may use to emit faster code with
exactly the same results; it never changes the program, so the portable meaning is the IR's alone.

vector_loads: a vector built from N consecutive elements of one kernel buffer,
`vec<T, N>(b[N i], b[N i + 1], ..., b[N i + N - 1])`, as the CUDA importer reads the originals'
`reinterpret_cast<const uint2*>(b)[i]`. The lowering emits one N-wide load when the buffer's address
is aligned to the vector and N i + N - 1 does not wrap the index type, and the N element loads
otherwise: the same elements either way, so the same bits.
"""
import json
import re

from qpel_types import is_buffer, access, term

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
        """(x, k) when index is (x * length) + k, else None."""
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
