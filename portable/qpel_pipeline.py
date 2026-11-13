"""Portable pipeline contracts and conservative whole-resource access analysis.

A write marks a resource produced, not every element initialized. This is not a
bounds, path-coverage or race proof. Every stage must complete before the next.
"""
from qpel_types import VALUE_TERMS, TypeChecker, access, is_buffer, is_struct

READ, WRITE = 1, 2


class PipelineChecker(TypeChecker):
    def __init__(self, program):
        super().__init__(program)
        self.pipeline = self.nodes[program['roots']['pipeline']]
        self.effect_cache = {}

    def bases(self, node, seen=None):
        """Possible parameter bases, including both sides of a pointer select, through views and
        buffer locals (their initializer and every assignment)."""
        seen = set() if seen is None else seen
        k = node['kind']
        if k == 'Name':
            target = self.target(node, 'declaration')
            if target['kind'] == 'Parameter':
                return {target['id']}
            if target['kind'] == 'Variable' and target['id'] not in seen:
                seen.add(target['id'])
                found = set()
                for value in self.local_values(target):
                    found |= self.bases(value, seen)
                return found
            return set()
        if k == 'View':
            return self.bases(self.child(node, 'base'), seen)
        if k == 'Binary':
            return self.bases(self.child(node, 'left'), seen)
        if k in {'Index', 'Member'}:
            return self.bases(self.child(node, 'base'), seen)
        if k == 'Select':
            return self.bases(self.child(node, 'yes'), seen) | self.bases(self.child(node, 'no'), seen)
        return set()

    def local_values(self, variable):
        """The initializer of a local and every value assigned to it."""
        if not hasattr(self, '_assigned'):
            self._assigned = {}
            for n in self.nodes.values():
                if n['kind'] == 'Assign':
                    target = self.child(n, 'target')
                    if target['kind'] == 'Name':
                        self._assigned.setdefault(target['links']['declaration'], []).append(self.child(n, 'value'))
        return self.children(variable, 'value') + self._assigned.get(variable['id'], [])

    def effects(self, function):
        if function['id'] in self.effect_cache:
            return self.effect_cache[function['id']]
        result = {}
        def mark(node, flags):
            for base in self.bases(node):
                result[base] = result.get(base, 0) | flags
        def walk(node):
            k = node['kind']
            if k == 'Assign':
                lhs = self.child(node, 'target')
                if lhs['kind'] == 'Index':
                    mark(lhs, WRITE)
                    # Computing an address can itself read buffers, even on a store.
                    walk(self.child(lhs, 'base'))
                    walk(self.child(lhs, 'index'))
                else:
                    walk(lhs)
                walk(self.child(node, 'value'))
                return
            if k == 'ReduceSumF32':
                mark(self.child(node, 'buffer'), READ)
            if k in {'Index', 'Member'}:
                mark(node, READ)
            if k.startswith('Atomic'):
                mark(self.child(node, 'address'), READ if k == 'AtomicLoad' else WRITE if k == 'AtomicStore' else READ | WRITE)
            if k == 'Call':
                callee = self.target(node, 'function')
                inner = self.effects(callee)
                for param, arg in zip(self.children(callee, 'parameters'), self.children(node, 'arguments')):
                    mark(arg, inner.get(param['id'], 0))
            for values in node['children'].values():
                for ref in values:
                    walk(self.nodes[ref])
        walk(self.child(function, 'body'))
        self.effect_cache[function['id']] = result
        return result

    def validate(self):
        resources = self.children(self.pipeline, 'resources')
        local = {r['id'] for r in resources}
        ready, names = set(), set()
        for resource in resources:
            self.unique(resource, names)
            kind, typ = resource['attributes']['kind'], self.declared(resource)
            extent = resource['links'].get('extent')
            if kind == 'shape':
                self.require(resource, typ == 'shape3' and extent is None, 'RESOURCE_TYPE', 'Shape requires Shape3D without extent')
                ready.add(resource['id'])
            elif kind == 'value':
                self.require(resource, (typ in VALUE_TERMS or is_struct(typ)) and extent is None, 'RESOURCE_TYPE', 'A value is an integer, float or struct without extent')
                ready.add(resource['id'])
            else:
                self.require(resource, is_buffer(typ), 'RESOURCE_TYPE', 'Data resource requires buffer type')
                fixed = 'length' in resource['attributes']  # buffer<f32, read> [16]: the same size in every launch
                self.require(resource, fixed or extent in local and self.nodes[extent]['attributes']['kind'] == 'shape' and self.declared(self.nodes[extent]) == 'shape3', 'EXTENT', 'Extent must reference a local shape')
                self.require(resource, not is_buffer(typ) or access(typ) != 'read' or kind == 'input', 'RESOURCE_ACCESS', 'Read-only buffers must be inputs')
                # zeroed scratch is filled with zeros before the first stage; an inout buffer
                # arrives with data, as an input does, and is returned, as an output is
                if kind in ('input', 'inout', 'zeroed'):
                    ready.add(resource['id'])
        for stage in self.children(self.pipeline, 'stages'):
            kernel, grid = self.target(stage, 'kernel'), self.target(stage, 'grid')
            self.require(stage, kernel['id'] in self.order and kernel['attributes'].get('isKernel', False), 'DISPATCH_TARGET', 'Dispatch requires a local kernel')
            self.require(stage, grid['id'] in local and grid['attributes']['kind'] == 'shape', 'DISPATCH_GRID', 'Grid must be a local shape')
            self.require(stage, stage['attributes']['completionBarrier'], 'BARRIER', 'Each stage must complete before the next')
            if self.domain(kernel) == 'Grid1D':
                self.require(stage, stage['attributes'].get('blockHeight', 1) == stage['attributes'].get('blockDepth', 1) == 1, 'BLOCK_SHAPE', 'A Grid1D kernel needs a one-dimensional block')
            groups = {a for a in ('groupsX', 'groupsY', 'groupsZ') if a in stage['attributes']}
            if self.domain(kernel) == 'Groups':
                self.require(stage, groups == {'groupsX', 'groupsY', 'groupsZ'}, 'GROUPS', 'A Groups kernel needs groupsX, groupsY and groupsZ')
            else:
                self.require(stage, not groups, 'GROUPS', 'Only Groups kernels take group counts; others run one thread per element')
            params, bindings = self.children(kernel, 'parameters'), self.children(stage, 'bindings')
            self.require(stage, len(params) == len(bindings), 'DISPATCH_ARITY', 'Bind every parameter exactly once')
            effects, seen, written = self.effects(kernel), set(), set()
            for param, binding in zip(params, bindings):
                self.require(binding, binding['links']['parameter'] == param['id'], 'PARAM_ORDER', 'Bindings must follow parameter declaration order')
                resource = self.target(binding, 'resource')
                rid, typ = resource['id'], self.declared(resource)
                self.require(binding, rid in local, 'RESOURCE_SCOPE', 'Binding requires a local resource')
                self.require(binding, rid not in seen, 'ALIAS', 'Aliasing within a dispatch is unsupported')
                seen.add(rid)
                self.require(binding, typ == self.declared(param), 'BIND_TYPE', 'Resource and parameter types must match')
                if typ in VALUE_TERMS or is_struct(typ):
                    self.require(binding, resource['attributes']['kind'] == 'value', 'VALUE_BINDING', 'Scalar parameters bind value resources')
                if typ == 'shape3':
                    self.require(binding, resource['attributes']['kind'] == 'shape', 'GRID_SHAPE', 'Shape parameters bind shape resources')
                flags = effects.get(param['id'], 0)
                self.require(binding, not flags & READ or rid in ready, 'READ_BEFORE_WRITE', 'Read requires input or a write by an earlier stage')
                if flags & WRITE:
                    self.require(binding, resource['attributes']['kind'] not in {'input', 'shape'}, 'INPUT_WRITE', 'Cannot write an input resource')
                    written.add(rid)
            ready.update(written)
        for resource in resources:
            self.require(resource, resource['attributes']['kind'] != 'output' or resource['id'] in ready, 'OUTPUT_UNWRITTEN', 'No stage produces this output')


def validate_pipeline(program):
    """Run after structural/type/scope checks (which reject recursive calls)."""
    PipelineChecker(program).validate()
