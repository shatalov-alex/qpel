"""Editor-independent scalar type and lexical-scope checks for qpel core v0.

Pipeline resource effects, bounds/races and numerical lowering remain separate.
"""
NUMBERS = {'U8Type', 'U32Type'}  # unsigned integers
INTEGERS = NUMBERS | {'I32Type'}
CONDITIONS = INTEGERS | {'BoolType'}
SCALARS = CONDITIONS | {'F32Type', 'F16Type'}
ELEMENTS = {'ReadOnlyU8BufferType': 'U8Type', 'U32BufferType': 'U32Type', 'ReadOnlyI32BufferType': 'I32Type',
            'I32BufferType': 'I32Type', 'ReadOnlyF32BufferType': 'F32Type', 'F32BufferType': 'F32Type',
            'ReadOnlyF16BufferType': 'F16Type', 'F16BufferType': 'F16Type'}
READ_ONLY = {'ReadOnlyU8BufferType', 'ReadOnlyI32BufferType', 'ReadOnlyF32BufferType', 'ReadOnlyF16BufferType'}
BUFFERS = set(ELEMENTS) | {'AtomicU32BufferType'}
COMPARISONS = {'==', '!=', '<', '>', '<=', '>='}


def fits(expected, actual):
    return expected == actual or (expected == 'U32Type' and actual == 'U8Type')


class TypeChecker:
    def __init__(self, program):
        self.nodes = {n['id']: n for n in program['nodes']}
        self.functions = self.children(self.nodes[program['roots']['module']], 'functions')
        self.expression_types = {}
        self.parents = {ref: n for n in program['nodes'] for values in n['children'].values() for ref in values}
        self.order = {f['id']: i for i, f in enumerate(self.functions)}

    def children(self, node, role):
        return [self.nodes[v] for v in node['children'].get(role, [])]

    def child(self, node, role):
        return self.children(node, role)[0]

    def target(self, node, role):
        return self.nodes[node['links'][role]]

    def declared(self, node):
        return self.child(node, 'type')['kind']

    def require(self, node, condition, code, message):
        if not condition:
            raise ValueError(f"{node['id']} ({node['kind']}) {code}: {message}")

    def unique(self, node, names):
        name = node['attributes']['name']
        self.require(node, name not in names, 'DUPLICATE', 'Duplicate identifier: ' + name)
        names.add(name)

    def expr(self, node, env, function):
        result = self._expr(node, env, function)
        self.expression_types[node["id"]] = result
        return result

    def _expr(self, node, env, function):
        k, a = node['kind'], node['attributes']
        def e(role):
            return self.expr(self.child(node, role), env, function)
        def check(ok, code, message):
            self.require(node, ok, code, message)
        if k == 'Name':
            target = self.target(node, 'declaration')
            check(target['id'] in env, 'SCOPE', 'Declaration is not visible here')
            return self.declared(target)
        if k == 'Literal':
            return 'BoolType' if a['value'] in ('true', 'false') else 'U32Type'
        if k == 'I32Literal':
            return 'I32Type'
        if k in ('I32Cast', 'U32Cast'):
            check(e('operand') in INTEGERS, 'CAST_TYPE', 'Integer conversion requires an integer')
            return 'I32Type' if k == 'I32Cast' else 'U32Type'
        if k == 'F32Literal':return 'F32Type'
        if k == 'WorkgroupSumF32':
            parent = self.parents.get(node['id'])
            body = self.child(function, 'body')
            check(function['attributes'].get('isKernel', False) and parent is not None and parent['kind'] == 'Variable' and parent['id'] in body['children'].get('statements', []) and self.child(parent,'value')['id'] == node['id'], 'COLLECTIVE_CONTEXT', 'Group sum must directly initialize a top-level kernel local')
            check(e('value') == 'F32Type', 'GROUP_SUM_TYPE', 'Group sum requires float32')
            return 'F32Type'
        if k == 'ReduceSumF32':
            check(e('buffer') in {'ReadOnlyF32BufferType', 'F32BufferType'}, 'REDUCE_BUFFER', 'Sum requires a float32 buffer')
            check(e('start') in NUMBERS and e('count') in NUMBERS, 'REDUCE_RANGE', 'Start and count require unsigned integers')
            return 'F32Type'
        if k == 'F32Fma':
            types = [e(role) for role in ('left', 'right', 'addend')]
            check(all(t == 'F32Type' for t in types), 'FMA_TYPE', 'FMA requires three float32 operands; convert integers explicitly')
            return 'F32Type'
        if k == 'F16Cast':
            check(e('operand') == 'F32Type', 'CAST_TYPE', 'Float16 storage conversion requires float32')
            return 'F16Type'
        if k == 'F32Cast':
            check(e('operand') in INTEGERS | {'F32Type', 'F16Type'}, 'CAST_TYPE', 'Float32 conversion requires numeric scalar')
            return 'F32Type'
        if k == 'GridIndex':
            check(function['attributes'].get('isKernel', False), 'GRID_CONTEXT', 'Grid index requires a kernel')
            return 'U32Type'
        if k == 'Call':
            target = self.target(node, 'function')
            check(target['id'] in self.order, 'CALL_TARGET', 'Function belongs to another module')
            check(not target['attributes'].get('isKernel', False), 'CALL_KERNEL', 'Use dispatch to invoke kernels')
            check(self.order[target['id']] < self.order[function['id']], 'CALL_ORDER', 'Helpers must precede callers; recursion is unsupported')
            args, params = self.children(node, 'arguments'), self.children(target, 'parameters')
            check(len(args) == len(params), 'ARITY', 'Argument count does not match function')
            for arg, param in zip(args, params):
                actual, expected = self.expr(arg, env, function), self.declared(param)
                self.require(arg, fits(expected, actual), 'ARG_TYPE', f'Expected {expected}, got {actual}')
            return self.child(target, 'resultType')['kind']
        if k == 'Member':
            check(e('base') == 'Shape3DType', 'MEMBER', 'Members require Shape3D')
            return 'U32Type'
        if k == 'Index':
            base, index = e('base'), e('index')
            check(index in INTEGERS, 'INDEX_TYPE', 'Index must be an integer')
            check(base in ELEMENTS, 'INDEX_BASE', 'Ordinary buffer required; use atomic operations for atomic buffers')
            return ELEMENTS[base]
        if k.startswith('Atomic'):
            check(e('address') == 'AtomicU32BufferType', 'ATOMIC_ADDRESS', 'Atomic buffer address required')
            if k != 'AtomicLoad':
                check(e('value') in NUMBERS, 'ATOMIC_VALUE', 'Unsigned integer required')
            return 'VoidType' if k == 'AtomicStore' else 'U32Type'
        if k == 'Unary':
            operand = e('operand')
            if a['operator'] == '!' and operand in CONDITIONS:
                return 'BoolType'
            if a['operator'] in ('-', '~') and operand in NUMBERS:
                return 'U32Type'
            if a['operator'] in ('-', '~') and operand == 'I32Type':
                return 'I32Type'
            if a['operator'] == '-' and operand == 'F32Type':return 'F32Type'
            check(False, 'UNARY', 'Unsupported operand type')
        if k in {'Binary', 'Minimum', 'Maximum'}:
            left, right = e('left'), e('right')
            if k != 'Binary':
                if left == right == 'I32Type':
                    return 'I32Type'
                check(left in NUMBERS and right in NUMBERS, 'MINMAX_TYPE', 'Two unsigned or two signed integers required')
                return 'U32Type'
            op = a['operator']
            divisor = self.child(node, 'right')
            if op in {'/', '%'} and divisor['kind'] in ('Literal', 'I32Literal') and right in INTEGERS:
                check(int(divisor['attributes']['value'].rstrip('uU')) != 0, 'ZERO_DIVISOR', 'Division/remainder by literal zero')
            if op in {'<<', '>>'}:
                check(left in INTEGERS and right in INTEGERS, 'SHIFT_TYPE', 'Shifts require integers')
                return 'I32Type' if left == 'I32Type' else 'U32Type'
            if left == right == 'I32Type':
                if op in {'+', '-', '*', '/', '%', '&', '|', '^'}:return 'I32Type'
                if op in COMPARISONS:return 'BoolType'
            if left == right == 'F32Type':
                if op in {'+', '-', '*', '/'}:return 'F32Type'
                if op in {'==', '!=', '<', '>', '<=', '>='}:return 'BoolType'
            if op in {'+', '-'} and left in BUFFERS and right in NUMBERS:
                return left
            if op in {'+', '-', '*', '/', '%', '&', '|', '^'} and left in NUMBERS and right in NUMBERS:
                return 'U32Type'
            if op in {'==', '!=', '<', '>', '<=', '>='} and left in NUMBERS and right in NUMBERS:
                return 'BoolType'
            if op in {'&&', '||'} and left in CONDITIONS and right in CONDITIONS:
                return 'BoolType'
            if op in {'==', '!='} and left == right == 'BoolType':
                return 'BoolType'
            check(False, 'BINARY', f'Unsupported types: {left} {op} {right}')
        if k == 'Select':
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
            yes, no = e('yes'), e('no')
            if fits(yes, no):
                return yes
            if fits(no, yes):
                return no
            check(False, 'SELECT_TYPE', 'Incompatible branches')
        check(False, 'EXPRESSION', 'Unsupported expression')

    def block(self, block, inherited, function, names, loops=0):
        """Check a block in a copy of the scope; True if it always returns."""
        env, returns = set(inherited), False
        for node in self.children(block, 'statements'):
            returns = self.statement(node, env, function, names, loops) or returns
        return returns

    def statement(self, node, env, function, names, loops):
        """Check one statement; a Variable extends env. True if it always returns."""
        k = node['kind']
        def e(role):
            return self.expr(self.child(node, role), env, function)
        def check(ok, code, message):
            self.require(node, ok, code, message)
        if k == 'Variable':
            self.unique(node, names)
            expected, actual = self.declared(node), e('value')
            check(expected in SCALARS and fits(expected, actual), 'VARIABLE_TYPE', f'Invalid initializer {expected} <- {actual}')
            env.add(node['id'])
        elif k == 'Assign':
            target = self.child(node, 'target')
            expected, actual = e('target'), e('value')
            writable = target['kind'] == 'Name' and expected in SCALARS
            if target['kind'] == 'Index':
                writable = self.expr(self.child(target, 'base'), env, function) in BUFFERS - READ_ONLY
            check(writable, 'READ_ONLY', 'Assignment target is not writable')
            check(fits(expected, actual), 'ASSIGN_TYPE', f'Incompatible assignment {expected} <- {actual}')
        elif k == 'Evaluate':
            e('value')
        elif k == 'Return':
            actual = e('value') if self.children(node, 'value') else 'VoidType'
            expected = self.child(function, 'resultType')['kind']
            check(fits(expected, actual), 'RETURN_TYPE', f'Expected {expected}, got {actual}')
            return True
        elif k == 'If':
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
            then = self.block(self.child(node, 'thenBody'), env, function, names, loops)
            otherwise = [self.block(b, env, function, names, loops) for b in self.children(node, 'elseBody')]
            return then and bool(otherwise) and otherwise[0]
        elif k == 'While':
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
            self.block(self.child(node, 'body'), env, function, names, loops + 1)
        elif k == 'For':
            inner = set(env)  # the init variable is visible only inside the loop
            for init in self.children(node, 'init'):
                self.statement(init, inner, function, names, loops)
            for condition in self.children(node, 'condition'):
                check(self.expr(condition, inner, function) in CONDITIONS, 'CONDITION', 'Scalar condition required')
            for step in self.children(node, 'step'):
                self.statement(step, inner, function, names, loops)
            self.block(self.child(node, 'body'), inner, function, names, loops + 1)
        elif k in ('Break', 'Continue'):
            check(loops > 0, 'LOOP_CONTEXT', k.lower() + ' must be inside a loop')
        elif k == 'Block':
            return self.block(node, env, function, names, loops)
        return False

    def validate(self):
        function_names = set()
        for function in self.functions:
            self.unique(function, function_names)
            kernel = function['attributes'].get('isKernel', False)
            result = self.child(function, 'resultType')['kind']
            domain = self.children(function, 'domain')
            params = self.children(function, 'parameters')
            self.require(function, result in SCALARS | {'VoidType'}, 'RESULT_TYPE', 'Result must be scalar or void')
            self.require(function, (result == 'VoidType' and bool(domain) and bool(params)) if kernel else not domain, 'FUNCTION_ABI', 'Kernel needs void result, Grid1D and parameters; helpers cannot have domains')
            names, bindings, env = set(), set(), set()
            for param in params:
                self.unique(param, names)
                t, a = self.declared(param), param['attributes']
                self.require(param, t in SCALARS | BUFFERS | {'Shape3DType'}, 'PARAM_TYPE', 'Unsupported parameter type')
                if kernel:
                    self.require(param, t in BUFFERS | {'Shape3DType'} and a.get('isBuffer', False), 'KERNEL_ABI', 'Kernel parameter must be a bound buffer or shape')
                    binding = a.get('binding', 0)
                    self.require(param, 0 <= binding <= 30, 'BINDING_INDEX', 'Binding must be between 0 and 30')
                    self.require(param, binding not in bindings, 'BINDING_DUPLICATE', 'Duplicate binding')
                    bindings.add(binding)
                else:
                    self.require(param, not a.get('isBuffer', False), 'HELPER_BINDING', 'Helper parameter cannot have kernel binding')
                env.add(param['id'])
            def descendants(n):
                yield n
                for values in n['children'].values():
                    for ref in values:yield from descendants(self.nodes[ref])
            kinds = {n['kind'] for n in descendants(function)}
            if 'WorkgroupSumF32' in kinds:
                self.require(function, not kinds & {'Return', 'While', 'For', 'Break', 'Continue'}, 'COLLECTIVE_FLOW', 'Kernels with group sum cannot contain returns or loops; mask input and guard writes instead')
            returns = self.block(self.child(function, 'body'), env, function, names)
            self.require(function, result == 'VoidType' or returns, 'MISSING_RETURN', 'Non-void helper requires unconditional return')


def validate_types(program):
    """Run after graph and grammar validation."""
    TypeChecker(program).validate()
