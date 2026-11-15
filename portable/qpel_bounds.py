"""Conservative constant-range diagnostics, not a bounds-safety proof.

Both generated hosts limit allocations to INT32_MAX elements. Only addresses
rooted in a kernel parameter have a known allocation origin here. Helper
parameters can be interior pointers and are deliberately not treated as origins.
"""
from qpel_types import TypeChecker, ATOMIC_KINDS
MAX_ELEMENTS = 2**31 - 1
MASK = 2**32 - 1


class BoundsChecker(TypeChecker):
    def constant(self, node):
        k = node['kind']
        if k == 'Literal':
            value = node['attributes']['value']
            return {'true': True, 'false': False}.get(value) if value in ('true', 'false') else int(value.rstrip('uU'))
        if k == 'Unary':
            x = self.constant(self.child(node, 'operand'))
            if x is not None:
                op = node['attributes']['operator']
                return not x if op == '!' else (~x) & MASK if op == '~' else (-x) & MASK
        if k == 'Select':
            condition = self.constant(self.child(node, 'condition'))
            if condition is not None:
                return self.constant(self.child(node, 'yes' if condition else 'no'))
        if k not in {'Binary', 'Minimum', 'Maximum'}:
            return None
        left = self.constant(self.child(node, 'left'))
        right = self.constant(self.child(node, 'right'))
        op = node['attributes'].get('operator')
        if op == '&&' and left is not None and not left: return False
        if op == '||' and left is not None and left: return True
        if left is None or right is None: return None
        if k == 'Minimum': return min(left, right)
        if k == 'Maximum': return max(left, right)
        if op in ('/', '%') and right == 0: return None
        operations = {'+': lambda: (left+right)&MASK, '-': lambda: (left-right)&MASK,
                      '*': lambda: (left*right)&MASK, '/': lambda: left//right,
                      '%': lambda: left%right, '&': lambda: left&right,
                      '|': lambda: left|right, '^': lambda: left^right,
                      '<': lambda: left<right, '<=': lambda: left<=right,
                      '>': lambda: left>right, '>=': lambda: left>=right,
                      '==': lambda: left==right, '!=': lambda: left!=right,
                      '&&': lambda: bool(left and right), '||': lambda: bool(left or right),
                      '<<': lambda: (left << (right & 31)) & MASK, '>>': lambda: left >> (right & 31)}
        return operations[op]()

    def origin_offset(self, node):
        if node['kind'] == 'Name':
            param = self.target(node, 'declaration')
            parent = self.parents.get(param['id'])
            if param['kind'] == 'Parameter' and parent and parent['kind'] == 'Function' and parent['attributes'].get('isKernel'):
                return 0
        if node['kind'] == 'Binary' and node['attributes']['operator'] in ('+', '-'):
            base = self.origin_offset(self.child(node, 'left'))
            offset = self.constant(self.child(node, 'right'))
            if base is not None and offset is not None:
                result = base + (offset if node['attributes']['operator'] == '+' else -offset)
                # Even intermediate pointer formation must stay in the allocation
                # (one-past is allowed until dereferenced).
                self.require(node, 0 <= result <= MAX_ELEMENTS, 'STATIC_BOUNDS', 'Constant pointer offset cannot fit any supported allocation')
                return result
        return None

    def check_ranges(self):
        accesses = 0
        for node in self.nodes.values():
            kind = node['kind']
            if kind == 'Index':
                base = self.origin_offset(self.child(node, 'base'))
                index = self.constant(self.child(node, 'index'))
                start = None if base is None or index is None else base + index
                count = 1
            elif kind in ATOMIC_KINDS:
                start = self.origin_offset(self.child(node, 'address')); count = 1
            elif kind == 'ReduceSumF32':
                count = self.constant(self.child(node, 'count'))
                # Empty sum performs no access, even with an arbitrary start.
                if count == 0: continue
                base = self.origin_offset(self.child(node, 'buffer'))
                index = self.constant(self.child(node, 'start'))
                start = None if base is None or index is None else base + index
            else:
                continue
            accesses += 1
            if start is not None and count is not None:
                self.require(node, 0 <= start and start + count <= MAX_ELEMENTS,
                             'STATIC_BOUNDS', 'Constant access range cannot fit any supported allocation')
        return {'policy': 'constant-ranges-v1', 'access_sites': accesses,
                'bounds_proven': False,
                'scope': 'Constant kernel-origin ranges checked against INT32_MAX allocation limit; runtime extents, dynamic indices, helpers and reachability are not proven'}


def validate_static_bounds(program):
    return BoundsChecker(program).check_ranges()
