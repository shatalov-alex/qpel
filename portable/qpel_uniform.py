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
"""
from qpel_types import TypeChecker, is_shared, term

THREAD_OWN = {'GridIndex', 'ThreadIndex', 'LocalIndex'}
GROUP_WIDE = {'GroupIndex', 'GroupSize', 'GroupCount', 'BarrierCount', 'Literal', 'I32Literal', 'Constant',
              'F32Literal', 'WorkgroupSumF32'}


class Uniformity(TypeChecker):
    def __init__(self, program):
        super().__init__(program)
        self.varying = set()  # Variable ids whose value can differ between the threads of a group
        self.divergent = set()  # loop ids that some threads of a group leave (or skip ahead in) early
        self.changed = False

    def uniform(self, node):
        k = node['kind']
        if k in THREAD_OWN or k.startswith('Atomic'):
            return False
        if k in GROUP_WIDE:
            return True
        if k == 'Name':
            declaration = self.target(node, 'declaration')
            return declaration['kind'] == 'Parameter' or declaration['id'] not in self.varying
        if k == 'Index':
            base = self.child(node, 'base')
            if base['kind'] == 'Name' and is_shared(self.declared(self.target(base, 'declaration'))):
                return False  # another thread may have written it
        return all(self.uniform(self.nodes[c]) for values in node['children'].values() for c in values)

    def mark(self, variable):
        if variable['id'] not in self.varying:
            self.varying.add(variable['id'])
            self.changed = True

    def assigned(self, target):
        """The local a store changes (x, x.y, x[i]), or None (buffers and shared memory)."""
        while target['kind'] in ('Member', 'Index'):
            target = self.child(target, 'base')
        if target['kind'] == 'Name':
            declaration = self.target(target, 'declaration')
            if declaration['kind'] == 'Variable' and not is_shared(self.declared(declaration)):
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
            if n['kind'] in ('Barrier', 'BarrierCount') and report is not None and not control:
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
                if n['kind'] == 'BarrierCount' and report is not None and not control:
                    report.append(n)

    def check(self):
        for function in self.functions:
            body = self.child(function, 'body')
            if not function['attributes'].get('isKernel') or not any(
                    n['kind'] in ('Barrier', 'BarrierCount') for n in self.walk(body)):
                continue
            self.changed = True
            while self.changed:  # a fixed point: locals and loops only ever become varying
                self.changed = False
                self.statements(body, True, None, None)
            report = []
            self.statements(body, True, None, report)
            for node in report:
                if node['kind'] == 'Return':
                    self.require(node, False, 'BARRIER_UNIFORM', 'In a kernel with barriers, a return taken by some threads '
                                                                 'of a group and not others leaves them waiting')
                self.require(node, False, 'BARRIER_UNIFORM', 'A barrier must be reached by every thread of the group: it is '
                                                             'under a condition or in a loop that differs between threads')


def check_barriers(program):
    Uniformity(program).check()
