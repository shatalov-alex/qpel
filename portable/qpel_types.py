"""Editor-independent scalar type and lexical-scope checks for the qpel core.

Types are terms (IR version 3): scalars `bool u8 i8 u16 i16 u32 i32 u64 i64 f16
bf16 f32 void`, `shape3`, and `buffer<T, read|write|atomic>` (write buffers are
also readable). Version 3 writes them with the ScalarType and BufferType nodes;
the concept names of versions 1 and 2 (U32Type, ReadOnlyF32BufferType, ...) stay
valid spellings of the same terms (LEGACY). Pipeline resource effects,
bounds/races and numerical lowering are checked elsewhere.
"""
import re

INTEGERS_BITS = {'u8': (8, False), 'i8': (8, True), 'u16': (16, False), 'i16': (16, True),
                 'u32': (32, False), 'i32': (32, True), 'u64': (64, False), 'i64': (64, True)}
INTEGER_TERMS = set(INTEGERS_BITS)
FLOATS = {'f32'}                        # arithmetic floats
STORAGE_FLOATS = {'f16', 'bf16'}        # storage only: convert to f32 to compute
SCALAR_TERMS = INTEGER_TERMS | FLOATS | STORAGE_FLOATS | {'bool'}
ACCESSES = ('read', 'write', 'atomic')
ATOMIC_ELEMENTS = {'u32', 'i32'}
VALUE_TERMS = INTEGER_TERMS | {'f32'}   # scalar kernel arguments
LEGACY = {'VoidType': 'void', 'BoolType': 'bool', 'U8Type': 'u8', 'U32Type': 'u32', 'I32Type': 'i32',
          'F32Type': 'f32', 'F16Type': 'f16', 'Shape3DType': 'shape3',
          'ReadOnlyU8BufferType': 'buffer<u8, read>', 'U32BufferType': 'buffer<u32, write>',
          'AtomicU32BufferType': 'buffer<u32, atomic>', 'AtomicI32BufferType': 'buffer<i32, atomic>',
          'ReadOnlyI32BufferType': 'buffer<i32, read>', 'I32BufferType': 'buffer<i32, write>',
          'ReadOnlyF32BufferType': 'buffer<f32, read>', 'F32BufferType': 'buffer<f32, write>',
          'ReadOnlyF16BufferType': 'buffer<f16, read>', 'F16BufferType': 'buffer<f16, write>'}
BUFFER = re.compile(r'buffer<(\w+), (read|write|atomic)>')
COMPARISONS = {'==', '!=', '<', '>', '<=', '>='}
LEGACY_CASTS = {'I32Cast': 'i32', 'U32Cast': 'u32', 'U8Cast': 'u8', 'F32Cast': 'f32', 'F16Cast': 'f16'}


def term(t):
    """The canonical term for a term, a legacy concept name, or a type node."""
    if isinstance(t, dict):
        if t['kind'] == 'ScalarType':
            return t['attributes']['scalar']
        if t['kind'] == 'BufferType':
            return buffer(t['attributes']['element'], t['attributes']['access'])
        return LEGACY.get(t['kind'], t['kind'])
    return LEGACY.get(t, t)


def buffer(element, access):
    return f'buffer<{element}, {access}>'


def is_buffer(t):
    return BUFFER.fullmatch(term(t)) is not None


def element(t):
    return BUFFER.fullmatch(term(t)).group(1)


def access(t):
    return BUFFER.fullmatch(term(t)).group(2)


def is_valid(t):
    t = term(t)
    if t in SCALAR_TERMS | {'void', 'shape3'}:
        return True
    m = BUFFER.fullmatch(t)
    return bool(m) and m.group(1) in SCALAR_TERMS - {'bool'} and (m.group(2) != 'atomic' or m.group(1) in ATOMIC_ELEMENTS)


def is_integer(t):
    return term(t) in INTEGER_TERMS


def is_signed(t):
    return INTEGERS_BITS[term(t)][1]


def bits(t):
    return INTEGERS_BITS[term(t)][0]


def unsigned_of(t):
    return 'u' + term(t)[1:]


def arithmetic(t):
    """The type integer arithmetic on t happens in: 8- and 16-bit values widen to 32 bits."""
    t = term(t)
    if t in INTEGER_TERMS and bits(t) < 32:
        return 'i32' if is_signed(t) else 'u32'
    return t


def fits(expected, actual):
    """True when a value of type `actual` converts to `expected` without loss."""
    expected, actual = term(expected), term(actual)
    if expected == actual:
        return True
    if expected in INTEGER_TERMS and actual in INTEGER_TERMS:
        (eb, es), (ab, a_signed) = INTEGERS_BITS[expected], INTEGERS_BITS[actual]
        if a_signed:
            return es and eb >= ab
        return eb > ab if es else eb >= ab
    return False


def join(left, right):
    """Common arithmetic type of two integers, or None when neither fits the other."""
    if fits(left, right):
        return arithmetic(left)
    if fits(right, left):
        return arithmetic(right)
    return None


# Pre-v3 names, still imported by other modules.
NUMBERS = {'u8', 'u32'}
INTEGERS = INTEGER_TERMS
CONDITIONS = INTEGER_TERMS | {'bool'}
SCALARS = SCALAR_TERMS
VALUES = VALUE_TERMS


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
        return term(self.child(node, 'type'))

    def domain(self, function):
        domain = self.children(function, 'domain')
        return domain[0]['kind'] if domain else None

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

    def cast(self, node, source, target):
        """Type of a conversion of a `source` value to `target` (Cast and the legacy casts)."""
        def check(ok, message):
            self.require(node, ok, 'CAST_TYPE', message)
        if target in INTEGER_TERMS:
            check(source in INTEGER_TERMS or source == 'f32', 'Integer conversion requires an integer or float32')
        elif target == 'f32':
            check(source in INTEGER_TERMS | FLOATS | STORAGE_FLOATS, 'Float32 conversion requires numeric scalar')
        elif target in STORAGE_FLOATS:
            check(source == 'f32', f'{target} storage conversion requires float32')
        else:
            check(False, 'Unsupported conversion target ' + target)
        return target

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
            return 'bool' if a['value'] in ('true', 'false') else 'u32'
        if k == 'I32Literal':
            return 'i32'
        if k == 'Constant':
            return a['scalar']
        if k in LEGACY_CASTS and k not in ('F32Cast', 'F16Cast'):
            check(e('operand') in INTEGER_TERMS, 'CAST_TYPE', 'Integer conversion requires an integer')
            return LEGACY_CASTS[k]
        if k == 'Cast':
            return self.cast(node, e('operand'), a['to'])
        if k == 'F32Literal':
            return 'f32'
        if k == 'WorkgroupSumF32':
            parent = self.parents.get(node['id'])
            body = self.child(function, 'body')
            check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Grid1D' and parent is not None and parent['kind'] == 'Variable' and parent['id'] in body['children'].get('statements', []) and self.child(parent, 'value')['id'] == node['id'], 'COLLECTIVE_CONTEXT', 'Group sum must directly initialize a top-level kernel local')
            check(e('value') == 'f32', 'GROUP_SUM_TYPE', 'Group sum requires float32')
            return 'f32'
        if k == 'ReduceSumF32':
            check(e('buffer') in {'buffer<f32, read>', 'buffer<f32, write>'}, 'REDUCE_BUFFER', 'Sum requires a float32 buffer')
            check(e('start') in NUMBERS and e('count') in NUMBERS, 'REDUCE_RANGE', 'Start and count require unsigned integers')
            return 'f32'
        if k == 'F32Fma':
            types = [e(role) for role in ('left', 'right', 'addend')]
            check(all(t == 'f32' for t in types), 'FMA_TYPE', 'FMA requires three float32 operands; convert integers explicitly')
            return 'f32'
        if k == 'F16Cast':
            check(e('operand') == 'f32', 'CAST_TYPE', 'Float16 storage conversion requires float32')
            return 'f16'
        if k == 'F32Cast':
            check(e('operand') in INTEGER_TERMS | {'f32', 'f16', 'bf16'}, 'CAST_TYPE', 'Float32 conversion requires numeric scalar')
            return 'f32'
        if k == 'GridIndex':
            check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Grid1D', 'GRID_CONTEXT', 'Grid index requires a Grid1D kernel')
            return 'u32'
        if k == 'ThreadIndex':
            check(function['attributes'].get('isKernel', False) and self.domain(function) in ('Grid3D', 'Groups'), 'GRID_CONTEXT', 'Thread index requires a Grid3D or Groups kernel')
            return 'u32'
        if k in ('GroupIndex', 'LocalIndex', 'GroupSize', 'GroupCount'):
            check(function['attributes'].get('isKernel', False) and self.domain(function) == 'Groups', 'GRID_CONTEXT', k + ' requires a Groups kernel')
            return 'u32'
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
            return term(self.child(target, 'resultType'))
        if k == 'Member':
            check(e('base') == 'shape3', 'MEMBER', 'Members require Shape3D')
            return 'u32'
        if k == 'Index':
            base, index = e('base'), e('index')
            check(index in INTEGER_TERMS, 'INDEX_TYPE', 'Index must be an integer')
            check(is_buffer(base) and access(base) != 'atomic', 'INDEX_BASE', 'Ordinary buffer required; use atomic operations for atomic buffers')
            return element(base)
        if k.startswith('Atomic'):
            address = e('address')
            check(is_buffer(address) and access(address) == 'atomic', 'ATOMIC_ADDRESS', 'Atomic buffer address required')
            item = element(address)
            if k != 'AtomicLoad':
                check(fits(item, e('value')), 'ATOMIC_VALUE', f'{item} value required')
            return 'void' if k == 'AtomicStore' else item
        if k == 'Unary':
            operand = e('operand')
            if a['operator'] == '!' and operand in CONDITIONS:
                return 'bool'
            if a['operator'] in ('-', '~') and operand in INTEGER_TERMS:
                return arithmetic(operand)
            if a['operator'] == '-' and operand == 'f32':
                return 'f32'
            check(False, 'UNARY', 'Unsupported operand type')
        if k in {'Binary', 'Minimum', 'Maximum'}:
            left, right = e('left'), e('right')
            if k != 'Binary':
                common = join(left, right) if left in INTEGER_TERMS and right in INTEGER_TERMS else None
                check(common is not None, 'MINMAX_TYPE', 'Two integers of compatible signedness required')
                return common
            op = a['operator']
            divisor = self.child(node, 'right')
            if op in {'/', '%'} and divisor['kind'] in ('Literal', 'I32Literal', 'Constant') and right in INTEGER_TERMS:
                check(int(divisor['attributes']['value'].rstrip('uU')) != 0, 'ZERO_DIVISOR', 'Division/remainder by literal zero')
            if op in {'<<', '>>'}:
                check(left in INTEGER_TERMS and right in INTEGER_TERMS, 'SHIFT_TYPE', 'Shifts require integers')
                return arithmetic(left)
            if left in INTEGER_TERMS and right in INTEGER_TERMS:
                common = join(left, right)
                if common is not None:
                    if op in {'+', '-', '*', '/', '%', '&', '|', '^'}:
                        return common
                    if op in COMPARISONS:
                        return 'bool'
            if left == right == 'f32':
                if op in {'+', '-', '*', '/'}:
                    return 'f32'
                if op in COMPARISONS:
                    return 'bool'
            if op in {'+', '-'} and is_buffer(left) and right in NUMBERS:
                return left
            if op in {'&&', '||'} and left in CONDITIONS and right in CONDITIONS:
                return 'bool'
            if op in {'==', '!='} and left == right == 'bool':
                return 'bool'
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
            check(expected in SCALAR_TERMS and fits(expected, actual), 'VARIABLE_TYPE', f'Invalid initializer {expected} <- {actual}')
            env.add(node['id'])
        elif k == 'Assign':
            target = self.child(node, 'target')
            expected, actual = e('target'), e('value')
            writable = target['kind'] == 'Name' and expected in SCALAR_TERMS
            if target['kind'] == 'Index':
                base = self.expr(self.child(target, 'base'), env, function)
                writable = is_buffer(base) and access(base) == 'write'
            check(writable, 'READ_ONLY', 'Assignment target is not writable')
            check(fits(expected, actual), 'ASSIGN_TYPE', f'Incompatible assignment {expected} <- {actual}')
        elif k == 'Evaluate':
            e('value')
        elif k == 'Return':
            actual = e('value') if self.children(node, 'value') else 'void'
            expected = term(self.child(function, 'resultType'))
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
        elif k == 'DoWhile':
            self.block(self.child(node, 'body'), env, function, names, loops + 1)
            check(e('condition') in CONDITIONS, 'CONDITION', 'Scalar condition required')
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
            result = term(self.child(function, 'resultType'))
            domain = self.children(function, 'domain')
            params = self.children(function, 'parameters')
            self.require(function, result in SCALAR_TERMS | {'void'}, 'RESULT_TYPE', 'Result must be scalar or void')
            self.require(function, (result == 'void' and bool(domain) and bool(params)) if kernel else not domain, 'FUNCTION_ABI', 'Kernel needs void result, Grid1D and parameters; helpers cannot have domains')
            names, bindings, env = set(), set(), set()
            for param in params:
                self.unique(param, names)
                t, a = self.declared(param), param['attributes']
                self.require(param, is_valid(t) and t != 'void', 'PARAM_TYPE', 'Unsupported parameter type')
                if kernel:
                    self.require(param, (is_buffer(t) or t in VALUE_TERMS or t == 'shape3') and a.get('isBuffer', False), 'KERNEL_ABI', 'Kernel parameter must be a bound buffer, shape, or integer/f32 value')
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
                    for ref in values:
                        yield from descendants(self.nodes[ref])
            kinds = {n['kind'] for n in descendants(function)}
            if 'WorkgroupSumF32' in kinds:
                self.require(function, not kinds & {'Return', 'While', 'DoWhile', 'For', 'Break', 'Continue'}, 'COLLECTIVE_FLOW', 'Kernels with group sum cannot contain returns or loops; mask input and guard writes instead')
            returns = self.block(self.child(function, 'body'), env, function, names)
            self.require(function, result == 'void' or returns, 'MISSING_RETURN', 'Non-void helper requires unconditional return')


def validate_types(program):
    """Run after graph and grammar validation."""
    TypeChecker(program).validate()


def node_spec(t):
    """(kind, attributes) of the IR node that spells a type term, preferring the pre-v3 name."""
    t = term(t)
    for kind, legacy in LEGACY.items():
        if legacy == t:
            return kind, {}
    if is_buffer(t):
        return 'BufferType', {'element': element(t), 'access': access(t)}
    return 'ScalarType', {'scalar': t}


def normalize_plan(plan):
    """A launch plan with type terms (schema 2); schema 1 plans spell types with concept names."""
    if plan.get('schema') == 2:
        return plan
    if plan.get('schema') != 1:
        raise ValueError('Unsupported launch schema')
    resources = [dict(r, type=term(r['type'])) for r in plan['resources']]
    return dict(plan, schema=2, resources=resources)


# Host-side storage of each element type. u16, u32 and u64 travel through torch as the signed
# type of the same width (torch on MPS lacks them); CuPy has no bfloat16, so bf16 travels as its bits.
TORCH_DTYPES = {'u8': 'uint8', 'i8': 'int8', 'u16': 'int16', 'i16': 'int16', 'u32': 'int32', 'i32': 'int32',
                'u64': 'int64', 'i64': 'int64', 'f16': 'float16', 'bf16': 'bfloat16', 'f32': 'float32'}
NUMPY_DTYPES = {'u8': 'uint8', 'i8': 'int8', 'u16': 'uint16', 'i16': 'int16', 'u32': 'uint32', 'i32': 'int32',
                'u64': 'uint64', 'i64': 'int64', 'f16': 'float16', 'bf16': 'uint16', 'f32': 'float32'}
