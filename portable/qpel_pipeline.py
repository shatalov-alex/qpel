"""Portable pipeline contracts and conservative whole-resource access analysis.

A write marks a resource produced, not every element initialized. This is not a
bounds, path-coverage or race proof. Every stage must complete before the next.
"""
from qpel_types import BUFFERS, READ_ONLY, VALUES, TypeChecker

READ, WRITE = 1, 2


class PipelineChecker(TypeChecker):
    def __init__(self, program):
        super().__init__(program)
        self.pipeline = self.nodes[program['roots']['pipeline']]
        self.effect_cache = {}

    def bases(self, node):
        """Possible parameter bases, including both sides of a pointer select."""
        k = node['kind']
        if k == 'Name':
            target = self.target(node, 'declaration')
            return {target['id']} if target['kind'] == 'Parameter' else set()
        if k == 'Binary':
            return self.bases(self.child(node, 'left'))
        if k in {'Index', 'Member'}:
            return self.bases(self.child(node, 'base'))
        if k == 'Select':
            return self.bases(self.child(node, 'yes')) | self.bases(self.child(node, 'no'))
        return set()

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
                self.require(resource, typ == 'Shape3DType' and extent is None, 'RESOURCE_TYPE', 'Shape requires Shape3D without extent')
                ready.add(resource['id'])
            elif kind == 'value':
                self.require(resource, typ in VALUES and extent is None, 'RESOURCE_TYPE', 'A value is a u32, i32 or f32 scalar without extent')
                ready.add(resource['id'])
            else:
                self.require(resource, typ in BUFFERS, 'RESOURCE_TYPE', 'Data resource requires buffer type')
                self.require(resource, extent in local and self.nodes[extent]['attributes']['kind'] == 'shape' and self.declared(self.nodes[extent]) == 'Shape3DType', 'EXTENT', 'Extent must reference a local shape')
                self.require(resource, typ not in READ_ONLY or kind == 'input', 'RESOURCE_ACCESS', 'Read-only buffers must be inputs')
                if kind == 'input':
                    ready.add(resource['id'])
        for stage in self.children(self.pipeline, 'stages'):
            kernel, grid = self.target(stage, 'kernel'), self.target(stage, 'grid')
            self.require(stage, kernel['id'] in self.order and kernel['attributes'].get('isKernel', False), 'DISPATCH_TARGET', 'Dispatch requires a local kernel')
            self.require(stage, grid['id'] in local and grid['attributes']['kind'] == 'shape', 'DISPATCH_GRID', 'Grid must be a local shape')
            self.require(stage, stage['attributes']['completionBarrier'], 'BARRIER', 'Each stage must complete before the next')
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
                if typ in BUFFERS:
                    self.require(binding, resource['links'].get('extent') == grid['id'], 'GRID_EXTENT', 'Buffer extent must match grid')
                if typ in VALUES:
                    self.require(binding, resource['attributes']['kind'] == 'value', 'VALUE_BINDING', 'Scalar parameters bind value resources')
                if typ == 'Shape3DType':
                    self.require(binding, rid == grid['id'], 'GRID_SHAPE', 'Shape parameter must match grid')
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
