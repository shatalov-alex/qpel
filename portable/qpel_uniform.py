"""Every thread of a group reaches each barrier (IR version 4).

CUDA requires all threads of a block that have not exited to reach each __syncthreads, and
Metal all threads of a threadgroup to reach each threadgroup_barrier. qpel checks the
stricter, common rule statically: a Barrier or BarrierCount only runs under control flow
that is the same for every thread of a group (uniform), inside no loop that some threads
leave early, and a kernel with barriers has no return that only some threads take.

Uniform values: literals and constants, kernel parameters and their members, the group's
index, size and count, BarrierCount, operations and calls on uniform values, and buffer loads
at a uniform index. Not uniform: the thread's own index (GridIndex, ThreadIndex, LocalIndex),
atomics, shared-memory reads, and anything computed from them. A local is uniform when every
value it is given is uniform and given under uniform control; this is a fixed point over the
kernel and its loops.

SIMD-group operations (IR version 5: shuffles and reductions) need all 32 lanes of a subgroup,
so the same check runs one level down: such an operation, or a call to a function that uses
one, only runs under control flow that is the same for every lane of a subgroup. Uniform there,
besides everything uniform in the group: the subgroup's index and size, reduction results, and
a thread index divided by a multiple of 32 or compared with one (the pipeline requires groups
whose x size is a multiple of 32), the index written group_size.x * group.x + local.x included
(CUDA's blockDim.x * blockIdx.x + threadIdx.x). Helper functions are checked with their
parameters taken as varying, and a kernel using subgroup operations, directly or through calls,
is a Groups kernel.

IR version 7 allows barriers in helpers (library code such as qpel's CUB subset): a helper with
barriers is checked as a kernel is, with its parameters taken as varying, a call of it counts as
a barrier, and a kernel that reaches one through calls is a Groups kernel.
"""
from qpel_types import TypeChecker, is_shared, is_shared_view, term

THREAD_OWN = {'GridIndex', 'ThreadIndex', 'LocalIndex', 'SubgroupLane'}
SUBGROUP_OPS = {'SubgroupShuffle', 'SubgroupReduce'}
SUBGROUP_WIDE = {'SubgroupIndex', 'SubgroupSize', 'SubgroupReduce'}
LANE_ROWS = {'LocalIndex', 'ThreadIndex', 'GridIndex'}  # x / 32 is the same for the 32 lanes of a subgroup
GROUP_WIDE = {'GroupIndex', 'GroupSize', 'GroupCount', 'BarrierCount', 'Literal', 'I32Literal', 'Constant',
              'F32Literal', 'WorkgroupSumF32'}


class Uniformity(TypeChecker):
    def __init__(self, program, level='group', parameters_uniform=True):
        super().__init__(program)
        self.level, self.parameters_uniform = level, parameters_uniform
        self.ops = {'Barrier', 'BarrierCount'} if level == 'group' else SUBGROUP_OPS
        self.using = set()  # functions that use an operation of this level, directly or through calls
        self.varying = set()  # Variable ids whose value can differ between the threads of a group
        self.divergent = set()  # loop ids that some threads of a group leave (or skip ahead in) early
        self.changed = False

    def is_op(self, node):
        return node['kind'] in self.ops or (node['kind'] == 'Call' and self.target(node, 'function')['id'] in self.using)

    def per_subgroup(self, node):
        """x / 32k, x >> (5 or more), or x compared with 32k, for a thread's x index."""
        if node['kind'] != 'Binary':
            return False
        left, right = self.child(node, 'left'), self.child(node, 'right')
        if not self.thread_x(left):
            return False
        value = self.constant(right)
        if value is None:
            return False
        op = node['attributes']['operator']
        if op == '>>':
            return value >= 5
        return op in ('/', '<', '>=') and value > 0 and value % 32 == 0

    def constant(self, node):
        """The integer a node always has: a literal, a conversion of one, or a local set to one and
        never assigned again (`constexpr int warp_size = 32`); else None."""
        while node['kind'] in ('Cast', 'I32Cast', 'U32Cast'):
            node = self.child(node, 'operand')
        if node['kind'] in ('Literal', 'I32Literal', 'Constant') and node['attributes']['value'] not in ('true', 'false'):
            return int(node['attributes']['value'].rstrip('uU'))
        if node['kind'] == 'Name':
            declaration = self.target(node, 'declaration')
            if declaration['kind'] == 'Variable' and self.children(declaration, 'value') \
                    and declaration['id'] not in self.reassigned():
                return self.constant(self.child(declaration, 'value'))
        return None

    def thread_x(self, node):
        """True for a thread's x index: the builtin, a conversion of it, or a local set to one
        and never assigned again (`const int tid = threadIdx.x`)."""
        while node['kind'] in ('Cast', 'I32Cast', 'U32Cast'):
            node = self.child(node, 'operand')
        if node['kind'] in LANE_ROWS:
            return node['attributes'].get('axis', 'x') == 'x'
        if node['kind'] == 'Binary' and node['attributes']['operator'] == '+':
            # group_size.x * group.x + local.x (CUDA's blockDim.x * blockIdx.x + threadIdx.x): a multiple of
            # the group's x size, itself a multiple of 32, plus a thread's x index
            left, right = self.child(node, 'left'), self.child(node, 'right')
            return any(self.thread_x(a) and self.multiple_of_group(b) for a, b in ((left, right), (right, left)))
        if node['kind'] == 'Name':
            declaration = self.target(node, 'declaration')
            if declaration['kind'] == 'Variable' and self.children(declaration, 'value') \
                    and declaration['id'] not in self.reassigned():
                return self.thread_x(self.child(declaration, 'value'))
        return False

    def multiple_of_group(self, node):
        """True for group_size.x times a uniform value (a whole number of x groups), or a constant multiple of 32."""
        while node['kind'] in ('Cast', 'I32Cast', 'U32Cast'):
            node = self.child(node, 'operand')
        value = self.constant(node)
        if value is not None:
            return value % 32 == 0
        if node['kind'] == 'GroupSize':
            return node['attributes'].get('axis', 'x') == 'x'
        if node['kind'] == 'Binary' and node['attributes']['operator'] == '*':
            left, right = self.child(node, 'left'), self.child(node, 'right')
            return any(self.multiple_of_group(a) and self.uniform(b) for a, b in ((left, right), (right, left)))
        return False

    def reassigned(self):
        """Variable ids that an Assign changes (as a whole or in part)."""
        if not hasattr(self, '_reassigned'):
            self._reassigned = set()
            for n in self.nodes.values():
                if n['kind'] == 'Assign':
                    target = self.child(n, 'target')
                    while target['kind'] in ('Member', 'Index'):
                        target = self.child(target, 'base')
                    if target['kind'] == 'Name':
                        self._reassigned.add(target['links']['declaration'])
        return self._reassigned

    def uniform(self, node):
        k = node['kind']
        if self.level == 'subgroup' and (k in SUBGROUP_WIDE or self.per_subgroup(node)):
            return True  # one value for the 32 lanes, whatever its operands
        if k in THREAD_OWN or k.startswith('Atomic') or k == 'SubgroupShuffle':
            return False
        if k in GROUP_WIDE:
            return True
        if k == 'Name':
            declaration = self.target(node, 'declaration')
            if declaration['kind'] == 'Parameter':
                return self.parameters_uniform
            return declaration['id'] not in self.varying
        if k == 'Index':
            base = self.child(node, 'base')
            while base['kind'] == 'Binary':  # (tile + k)[i]
                base = self.child(base, 'left')
            if base['kind'] == 'Name' and self.group_memory(self.declared(self.target(base, 'declaration'))):
                return False  # another thread may have written it
        return all(self.uniform(self.nodes[c]) for values in node['children'].values() for c in values)

    @staticmethod
    def group_memory(t):
        return is_shared(t) or is_shared_view(t)

    def mark(self, variable):
        if variable['id'] not in self.varying:
            self.varying.add(variable['id'])
            self.changed = True

    def assigned(self, target):
        """The local a store changes (x, x.y, x[i]), or None (buffers and shared memory)."""
        original = target
        while target['kind'] in ('Member', 'Index'):
            target = self.child(target, 'base')
        if target['kind'] == 'Name':
            declaration = self.target(target, 'declaration')
            if declaration['kind'] == 'Variable' and not is_shared(self.declared(declaration)):
                if is_shared_view(self.declared(declaration)) and original['kind'] == 'Index':
                    return None  # p[i] = v writes shared memory, not the view p
                return declaration
        return None

    def calls(self, node, uniform_control):
        """Locals passed by reference to a call that runs under varying control, or with varying arguments."""
        for n in self.walk(node):
            if n['kind'] != 'Call':
                continue
            params = self.children(self.target(n, 'function'), 'parameters')
            args = self.children(n, 'arguments')
            same = uniform_control and all(self.uniform(a) for a in args)
            for param, arg in zip(params, args):
                if param['attributes'].get('reference') and not same and arg['kind'] == 'Name':
                    declaration = self.target(arg, 'declaration')
                    if declaration['kind'] == 'Variable':
                        self.mark(declaration)

    def walk(self, node):
        yield node
        for values in node['children'].values():
            for c in values:
                yield from self.walk(self.nodes[c])

    def statements(self, block, control, loop, report):
        for s in self.children(block, 'statements'):
            self.statement(s, control, loop, report)

    def statement(self, s, control, loop, report):
        k = s['kind']
        if k == 'Block':
            return self.statements(s, control, loop, report)
        for n in self.walk(s) if k not in ('If', 'While', 'DoWhile', 'For') else ():
            if self.is_op(n) and report is not None and not control:
                report.append(n)
        if k == 'Variable':
            self.calls(s, control)
            if not is_shared(self.declared(s)) and (not control or any(not self.uniform(v) for v in self.children(s, 'value'))):
                self.mark(s)
        elif k == 'Assign':
            self.calls(s, control)
            variable = self.assigned(self.child(s, 'target'))
            if variable is not None and (not control or not self.uniform(self.child(s, 'value'))
                                         or not self.uniform(self.child(s, 'target'))):
                self.mark(variable)
        elif k in ('Evaluate', 'Return'):
            self.calls(s, control)
            if k == 'Return' and not control and report is not None:
                report.append(s)
        elif k in ('Break', 'Continue'):
            if not control and loop is not None and loop['id'] not in self.divergent:
                self.divergent.add(loop['id'])
                self.changed = True
        elif k == 'If':
            self.check_condition(s, 'condition', control, report)
            inner = control and self.uniform(self.child(s, 'condition'))
            self.statements(self.child(s, 'thenBody'), inner, loop, report)
            for b in self.children(s, 'elseBody'):
                self.statements(b, inner, loop, report)
        elif k in ('While', 'DoWhile', 'For'):
            for init in self.children(s, 'init'):
                self.statement(init, control, loop, report)
            conditions = self.children(s, 'condition')
            for c in conditions:
                self.check_condition(s, 'condition', control, report)
            inner = control and s['id'] not in self.divergent and all(self.uniform(c) for c in conditions)
            self.statements(self.child(s, 'body'), inner, s, report)
            for step in self.children(s, 'step'):
                self.statement(step, inner, s, report)

    def check_condition(self, s, role, control, report):
        for c in self.children(s, role):
            for n in self.walk(c):
                if self.is_op(n) and report is not None and not control:
                    report.append(n)

    def find_using(self):
        for function in self.functions:  # helpers precede their callers
            if any(self.is_op(n) for n in self.walk(self.child(function, 'body'))):
                self.using.add(function['id'])

    def check(self):
        self.find_using()
        for function in self.functions:
            body = self.child(function, 'body')
            if function['id'] not in self.using:
                continue
            kernel = function['attributes'].get('isKernel')
            if self.level == 'subgroup' and kernel:
                self.require(function, self.domain(function) == 'Groups', 'SUBGROUP_CONTEXT',
                             'Subgroup operations (shuffles, reductions) are in Groups kernels and the functions they call')
            if self.level == 'group' and kernel:
                self.require(function, self.domain(function) == 'Groups', 'BARRIER_CONTEXT',
                             'Barriers are in Groups kernels and the helpers they call')
            self.parameters_uniform = bool(kernel)
            self.varying, self.divergent = set(), set()
            self.changed = True
            while self.changed:  # a fixed point: locals and loops only ever become varying
                self.changed = False
                self.statements(body, True, None, None)
            report = []
            self.statements(body, True, None, report)
            for node in report:
                if node['kind'] == 'Return' and self.level == 'subgroup':
                    self.require(node, False, 'SUBGROUP_UNIFORM', 'In a function with subgroup operations, a return taken '
                                                                  'by some lanes of a subgroup and not others leaves them '
                                                                  'without partners')
                if node['kind'] == 'Return':
                    self.require(node, False, 'BARRIER_UNIFORM', 'In a kernel with barriers, a return taken by some threads '
                                                                 'of a group and not others leaves them waiting')
                if self.level == 'subgroup':
                    self.require(node, False, 'SUBGROUP_UNIFORM', 'A shuffle or subgroup reduction must be reached by all 32 '
                                                                  'lanes of a subgroup: it is under a condition or in a loop '
                                                                  'that differs between them')
                self.require(node, False, 'BARRIER_UNIFORM', 'A barrier must be reached by every thread of the group: it is '
                                                             'under a condition or in a loop that differs between threads')


def check_subgroup_dispatch(program, using):
    """A kernel with subgroup operations runs in groups whose x size is a multiple of 32: every
    subgroup is then full, and x / 32 names one subgroup."""
    for n in program['nodes']:
        if n['kind'] == 'Dispatch' and n['links'].get('kernel') in using and n['attributes']['blockSize'] % 32:
            raise ValueError(f"{n['id']} (Dispatch) SUBGROUP_BLOCK: a kernel with subgroup operations needs a block "
                             f"whose x size is a multiple of 32, not {n['attributes']['blockSize']}")


def check_barriers(program):
    Uniformity(program).check()
    subgroups = Uniformity(program, 'subgroup')
    subgroups.check()
    check_subgroup_dispatch(program, {f for f in subgroups.using
                                      if subgroups.nodes[f]['attributes'].get('isKernel')})
