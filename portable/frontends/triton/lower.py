"""Triton TTIR -> qpel: one Triton program is one qpel workgroup (frontends/triton/README.md).

A Triton program computes on tensors (blocks of values); qpel's kernels are per thread. A group
of T = num_warps * 32 threads runs one program (its `tt.get_program_id` is the group index), and
every tensor is one of:

- an index tensor: a pure function of its element's coordinates and of scalars (`tt.make_range`,
  `tt.splat`, `tt.broadcast`, `tt.expand_dims`, constants, and element-wise operations on those).
  It is never stored: each use computes its element where it is needed (`Lazy`).
- a data tensor: one value per element, computed by the thread that owns the element. Element e
  (row-major) of a tensor of n elements is owned by thread e % T, in slot e / T of a local array
  of ceil(n / T) values (`Data`). Every tensor of a shape uses the same distribution, so an
  element-wise operation reads its operands' elements in the thread that owns them.
- a shared tensor: a data tensor written to the group's shared memory (`Shared`), so that any
  thread reads any element: what a broadcast or an expanded dimension of data, and a reduction,
  need. A barrier separates the writes from the reads (and from earlier reads of the same array).

Loads and stores run per owned element under the tensor's mask; a masked-off load gives the
load's `other` (or zero). A reduction (`tt.reduce`) folds the axis in index order, 0 to n - 1:
Triton does not specify the order (its GPU code combines in a tree that depends on the layout),
so any order is Triton's semantics, and float sums are compared against Triton within a
tolerance. Each thread of the group computes a reduction to a scalar itself, from shared memory,
so the scalar is the same in every thread, as Triton's scalars are.
"""
import math
import re
import struct

from .ttir import Op, Type, parse
from qpel_schema import is_identifier

SCALARS = {'i1': 'bool', 'i8': 'i8', 'i16': 'i16', 'i32': 'i32', 'i64': 'i64', 'f16': 'f16', 'bf16': 'bf16', 'f32': 'f32'}
ZERO = {'bool': 'false', 'f32': '0.0f', 'i32': '0', 'i64': '0_i64', 'f16': 'cast<f16>(0.0f)', 'u32': '0u'}
RESERVED = {'let', 'fn', 'module', 'pipeline', 'dispatch', 'cast', 'bitcast', 'view', 'math', 'atomic', 'qpel',
            'kernel', 'struct', 'if', 'else', 'for', 'while', 'do', 'return', 'break', 'continue', 'true', 'false',
            'shape', 'input', 'output', 'inout', 'value', 'in', 'over', 'block', 'groups', 'ref'}
INT_BINARY = {'arith.addi': '+', 'arith.subi': '-', 'arith.muli': '*', 'arith.divsi': '/', 'arith.remsi': '%',
              'arith.andi': '&', 'arith.ori': '|', 'arith.xori': '^', 'arith.shli': '<<', 'arith.shrsi': '>>'}
FLOAT_BINARY = {'arith.addf': '+', 'arith.subf': '-', 'arith.mulf': '*', 'arith.divf': '/'}
CALLS = {'arith.maxnumf': 'math.fmax', 'arith.minnumf': 'math.fmin', 'arith.maxsi': 'max', 'arith.minsi': 'min',
         'math.exp': 'math.exp', 'math.exp2': 'math.exp2', 'math.log': 'math.log', 'math.log2': 'math.log2',
         'math.sqrt': 'math.sqrt', 'math.absf': 'math.fabs', 'math.sin': 'math.sin', 'math.cos': 'math.cos',
         'math.floor': 'math.floor', 'math.ceil': 'math.ceil', 'math.tanh': 'math.tanh'}
CMPI = {'slt': '<', 'sle': '<=', 'sgt': '>', 'sge': '>=', 'eq': '==', 'ne': '!='}
CMPI_UNSIGNED = {'ult': '<', 'ule': '<=', 'ugt': '>', 'uge': '>='}
CMPF = {'olt': '<', 'ole': '<=', 'ogt': '>', 'oge': '>=', 'oeq': '==', 'une': '!='}
CASTS = {'arith.sitofp', 'arith.fptosi', 'arith.extf', 'arith.truncf', 'arith.extsi', 'arith.trunci', 'arith.bitcast'}
COMBINERS = {'arith.addf': lambda a, b: f'({a} + {b})', 'arith.addi': lambda a, b: f'({a} + {b})',
             'arith.mulf': lambda a, b: f'({a} * {b})', 'arith.maxnumf': lambda a, b: f'math.fmax({a}, {b})',
             'arith.minnumf': lambda a, b: f'math.fmin({a}, {b})', 'arith.maxsi': lambda a, b: f'max({a}, {b})',
             'arith.minsi': lambda a, b: f'min({a}, {b})'}
MATH_BOUNDED = {'math.exp', 'math.exp2', 'math.log', 'math.log2', 'math.sin', 'math.cos', 'math.tanh'}


class Unsupported(Exception):
    def __init__(self, op, message):
        where = f'{op.name} (line {op.line})' if isinstance(op, Op) else str(op)
        super().__init__(f'{where}: {message}')
        self.code = 'triton'


def scalar_term(element):
    if element not in SCALARS:
        raise Unsupported('type', f'{element} is not in qpel')
    return SCALARS[element]


class Lazy:
    """An index tensor: gen(coords) is its element's expression."""
    def __init__(self, t, gen, buffer=None):
        self.type, self.gen, self.buffer = t, gen, buffer


class Data:
    def __init__(self, t, name, buffer=None):
        self.type, self.name, self.buffer = t, name, buffer


class Shared:
    def __init__(self, t, name, buffer=None):
        self.type, self.name, self.buffer = t, name, buffer


class Scalar:
    def __init__(self, t, text, buffer=None):  # a pointer is (buffer, offset text)
        self.type, self.text, self.buffer = t, text, buffer


def float_literal(value):
    value = struct.unpack('f', struct.pack('f', value))[0]
    if math.isnan(value) or math.isinf(value):
        return f'bitcast<f32>({struct.unpack("I", struct.pack("f", value))[0]}u)'
    text = repr(value)
    if 'e' not in text and '.' not in text:
        text += '.0'
    return f'{text}f' if value >= 0 else f'(-{text[1:]}f)'


def constant_text(raw, element):
    raw = raw.strip()
    term = scalar_term(element)
    if raw in ('true', 'false'):
        return raw
    if raw.startswith('0x'):
        bits = int(raw, 16)
        if element == 'f32':
            return float_literal(struct.unpack('f', struct.pack('I', bits))[0])
        raise Unsupported('constant', f'hexadecimal {element} constant {raw}')
    if element.startswith('f') or element == 'bf16':
        text = float_literal(float(raw))
        return text if term == 'f32' else f'cast<{term}>({text})'
    value = int(raw)
    if term == 'bool':
        return 'true' if value else 'false'
    if term == 'i32':
        return str(value) if value >= 0 else f'(-{-value})'
    return f'{value}_{term}' if value >= 0 else f'const<{term}>({value})'


class Lowering:
    def __init__(self, function, num_warps=4, module=None):
        self.function = function
        self.T = num_warps * 32
        self.module = module or function.name
        self.values = {}
        self.taken = set()
        self.shared = []  # (name, type text) declared at the kernel's top
        self.lines = []
        self.depth = 1
        self.reads, self.writes = set(), set()
        self.adaptations = []
        self.k = self.e = None  # the slot and element of the innermost owned() loop
        self.float_sums = []  # lengths of the float axes reduced by + or * (compared within a tolerance)
        self.bounded = False  # a bounded math function is used (exp, log, ...): compared within MATH_ULPS

    # -- names and text --

    def fresh(self, hint):
        base = re.sub(r'[^A-Za-z0-9_]', '_', hint.lstrip('%')).strip('_') or 'v'
        if not base[0].isalpha():
            base = 'v' + base
        if base in RESERVED or not is_identifier(base):
            base = 'v_' + base
        name, n = base, 1
        while name in self.taken or not is_identifier(name):
            name = f'{base}_{n}'
            n += 1
        self.taken.add(name)
        return name

    def emit(self, text):
        self.lines.append('  ' * self.depth + text)

    def open(self, text):
        self.emit(text + ' {')
        self.depth += 1

    def close(self):
        self.depth -= 1
        self.emit('}')

    # -- values --

    def value(self, name):
        if name not in self.values:
            raise Unsupported(name, 'value defined by an operation qpel does not read')
        return self.values[name]

    def element(self, v, coords, own):
        """The expression of v's element at coords (own: the coords are the current thread's
        own element of a tensor of v's shape, in slot k)."""
        if isinstance(v, Scalar):
            return v.text
        if isinstance(v, Lazy):
            return v.gen(coords, own)
        if isinstance(v, Data):
            if not own:
                raise Unsupported('data', 'internal: a data tensor read away from its owner')
            return f'{v.name}[{self.k}]'
        if isinstance(v, Shared):
            return f'{v.name}[cast<u32>({self.linear(v.type.shape, coords)})]'
        raise Unsupported('value', f'internal: {v}')

    @staticmethod
    def linear(shape, coords):
        text = coords[0]
        for d, c in zip(shape[1:], coords[1:]):
            text = f'(({text}) * {d} + {c})'
        return text

    @staticmethod
    def coords_of(shape, e='e'):
        coords, stride = [], 1
        for d in reversed(shape):
            coords.append(f'(({e} / {stride}) % {d})' if stride > 1 else f'({e} % {d})')
            stride *= d
        return list(reversed(coords))

    def slots(self, t):
        return max(1, -(-t.numel // self.T))

    def owned(self, t, body):
        """Emit `for each slot k this thread owns of a tensor of type t: body(coords)` (self.k and
        self.e name the slot and the element while body runs)."""
        k, e = self.fresh('k'), self.fresh('e')
        outer = (self.k, self.e)
        self.k, self.e = k, e
        self.open(f'for (let {k}: i32 = 0; {k} < {self.slots(t)}; {k} = {k} + 1)')
        self.emit(f'let {e}: i32 = ({k} * {self.T}) + cast<i32>(qpel.local.x);')
        self.open(f'if ({e} < {t.numel})')
        body(self.coords_of(t.shape, e))
        self.close()
        self.close()
        self.k, self.e = outer

    def new_data(self, t, hint):
        name = self.fresh(hint)
        term = scalar_term(t.element) if not t.is_pointer else 'i32'
        zero = ZERO.get(term, f'cast<{term}>(0)')
        self.emit(f'let {name}: array<{term}, {self.slots(t)}> = [{", ".join([zero] * self.slots(t))}];')
        return name

    def materialize(self, v, hint='tile'):
        """v as a shared tensor (written by its owners, then a barrier)."""
        if isinstance(v, Shared):
            return v
        if v.type.is_pointer:
            raise Unsupported('pointer', 'a tensor of pointers computed from loaded data that is broadcast or reduced')
        term = scalar_term(v.type.element)
        if term == 'bool':
            raise Unsupported('bool', 'a loaded or computed mask that is broadcast or reduced')
        name = self.fresh('s_' + hint)
        self.shared.append((name, f'shared<{term}, {v.type.numel}>'))
        self.emit('qpel.barrier();')  # earlier readers of this array (in a loop) are done
        self.owned(v.type, lambda c: self.emit(f'{name}[cast<u32>({self.e})] = {self.element(v, c, True)};'))
        self.emit('qpel.barrier();')
        return Shared(v.type, name, v.buffer)

    # -- the function --

    def run(self):
        f = self.function
        self.params = []
        for name, t in f.arguments:
            pname = self.fresh(name)
            self.params.append((pname, t))
            if t.is_pointer:
                self.values[name] = Scalar(t, '0', buffer=pname)
            else:
                self.values[name] = Scalar(t, pname)
        self.block(f.body.ops)
        return self.text()

    def block(self, ops):
        for op in ops:
            self.op(op)

    def result_type(self, op):
        if op.result_types:
            return op.result_types[0]
        if op.name in ('arith.cmpi', 'arith.cmpf'):
            return Type(op.types[0].shape, 'i1')
        if op.name == 'arith.select':
            return op.types[-1]
        if op.name == 'tt.load':
            t = op.types[0]
            return Type(t.shape, t.pointee)
        if op.types:
            return op.types[0]
        raise Unsupported(op, 'result type not known')

    def define(self, op, value):
        self.values[op.results[0]] = value

    def op(self, op):
        name = op.name
        if name in ('tt.return', 'scf.yield', 'tt.reduce.return'):
            return
        if name == 'arith.constant':
            return self.constant(op)
        if name == 'tt.get_program_id':
            axis = re.match(r'\s*([xyz])', op.text).group(1)
            return self.scalar(op, Type((), 'i32'), f'cast<i32>(qpel.group.{axis})')
        if name == 'tt.get_num_programs':
            axis = re.match(r'\s*([xyz])', op.text).group(1)
            return self.scalar(op, Type((), 'i32'), f'cast<i32>(qpel.group_count.{axis})')
        if name == 'tt.make_range':
            start = op.attribute('start', 0)
            t = self.result_type(op)
            return self.define(op, Lazy(t, lambda c, own, s=start: f'({s} + {c[0]})' if s else c[0]))
        if name == 'tt.splat':
            src = self.value(op.operands[0])
            t = self.result_type(op)
            return self.define(op, Lazy(t, lambda c, own, s=src: s.text, buffer=src.buffer))
        if name in ('tt.broadcast', 'tt.expand_dims', 'tt.reshape'):
            return self.reshape(op)
        if name == 'tt.addptr':
            return self.addptr(op)
        if name == 'tt.load':
            return self.load(op)
        if name == 'tt.store':
            return self.store(op)
        if name == 'tt.reduce':
            return self.reduce(op)
        if name == 'scf.for':
            return self.loop(op)
        if name in INT_BINARY or name in FLOAT_BINARY or name in CALLS or name in CASTS or \
                name in ('arith.cmpi', 'arith.cmpf', 'arith.select', 'arith.negf'):
            if name in MATH_BOUNDED:
                self.bounded = True
            return self.elementwise(op)
        raise Unsupported(op, f'{name} is not in qpel\'s Triton subset')

    def constant(self, op):
        t = self.result_type(op)
        dense = re.match(r'\s*dense<(.*?)>\s*$', op.text.rsplit(':', 1)[0])
        raw = dense.group(1) if dense else op.text.rsplit(':', 1)[0]
        if t.is_tensor:
            text = constant_text(raw, t.element)
            return self.define(op, Lazy(t, lambda c, own, s=text: s))
        return self.define(op, Scalar(t, constant_text(raw, t.element)))

    def scalar(self, op, t, text):
        name = self.fresh(op.results[0])
        self.emit(f'let {name}: {scalar_term(t.element)} = {text};')
        self.define(op, Scalar(t, name))

    def reshape(self, op):
        src = self.value(op.operands[0])
        t = self.result_type(op)
        st = src.type
        if isinstance(src, Data):
            src = self.materialize(src, op.results[0])
        if op.name == 'tt.expand_dims':
            axis = op.attribute('axis')
            def gen(c, own, s=src, a=axis):
                return self.element(s, c[:a] + c[a + 1:], False)
        elif op.name == 'tt.broadcast':
            def gen(c, own, s=src, shape=st.shape):
                return self.element(s, [ci if d != 1 else '0' for ci, d in zip(c, shape)], False)
        else:  # reshape: the same row-major element
            def gen(c, own, s=src, shape=t.shape, old=st.shape):
                return self.element(s, self.coords_of(old, self.linear(shape, c)), False)
        if isinstance(src, Lazy) or isinstance(src, Shared) or isinstance(src, Scalar):
            self.define(op, Lazy(t, gen, buffer=src.buffer))

    def addptr(self, op):
        base, offset = (self.value(x) for x in op.operands)
        t = self.result_type(op)
        if not t.is_tensor:
            text = f'({base.text} + {offset.text})' if base.text != '0' else offset.text
            name = self.fresh(op.results[0])
            self.emit(f'let {name}: i32 = {text};')
            return self.define(op, Scalar(t, name, buffer=base.buffer))
        return self.elementwise(op, combine=lambda a, b: b if a == '0' else f'({a} + {b})', buffer=base.buffer)

    def elementwise(self, op, combine=None, buffer=None):
        operands = [self.value(x) for x in op.operands]
        t = self.result_type(op)
        name = op.name
        if combine is None:
            combine = self.expression(op, operands)
        if not t.is_tensor:
            name_ = self.fresh(op.results[0])
            term = scalar_term(t.element)
            self.emit(f'let {name_}: {term} = {combine(*[o.text for o in operands])};')
            return self.define(op, Scalar(t, name_))
        if all(isinstance(o, (Lazy, Scalar)) for o in operands):
            return self.define(op, Lazy(t, lambda c, own, ops=operands: combine(*[self.element(o, c, own) for o in ops]),
                                        buffer=buffer))
        operands = [self.materialize(o) if isinstance(o, Data) and o.type.shape != t.shape else o for o in operands]
        out = self.new_data(t, op.results[0])
        self.owned(t, lambda c: self.emit(f'{out}[{self.k}] = {combine(*[self.element(o, c, True) for o in operands])};'))
        self.define(op, Data(t, out, buffer=buffer))

    def expression(self, op, operands):
        name = op.name
        if name in INT_BINARY:
            symbol = INT_BINARY[name]
            if operands[0].type.element == 'i1':
                symbol = {'&': '&&', '|': '||', '^': '!='}.get(symbol, symbol)
            return lambda a, b: f'({a} {symbol} {b})'
        if name in FLOAT_BINARY:
            return lambda a, b, s=FLOAT_BINARY[name]: f'({a} {s} {b})'
        if name == 'arith.negf':
            return lambda a: f'(-{a})'
        if name in CALLS:
            return lambda *a, f=CALLS[name]: f'{f}({", ".join(a)})'
        if name == 'arith.select':
            return lambda c, a, b: f'({c} ? {a} : {b})'
        if name == 'arith.cmpi':
            predicate = op.text.split(',')[0].strip()
            if predicate in CMPI:
                return lambda a, b, s=CMPI[predicate]: f'({a} {s} {b})'
            if predicate in CMPI_UNSIGNED:
                return lambda a, b, s=CMPI_UNSIGNED[predicate]: f'(cast<u32>({a}) {s} cast<u32>({b}))'
            raise Unsupported(op, f'comparison {predicate}')
        if name == 'arith.cmpf':
            predicate = op.text.split(',')[0].strip()
            if predicate in CMPF:
                return lambda a, b, s=CMPF[predicate]: f'({a} {s} {b})'
            if predicate == 'one':
                return lambda a, b: f'(({a} < {b}) || ({a} > {b}))'
            raise Unsupported(op, f'comparison {predicate}')
        if name in CASTS:
            target = scalar_term(self.result_type(op).element)
            if name == 'arith.bitcast':
                return lambda a, s=target: f'bitcast<{s}>({a})'
            return lambda a, s=target: f'cast<{s}>({a})'
        raise Unsupported(op, f'{name} is not in qpel\'s Triton subset')

    def pointer_parts(self, ptr, coords, own):
        if ptr.buffer is None:
            raise Unsupported('pointer', 'a pointer not derived from a kernel argument')
        return ptr.buffer, self.element(ptr, coords, own)

    def load(self, op):
        ptr = self.value(op.operands[0])
        mask = self.value(op.operands[1]) if len(op.operands) > 1 else None
        other = self.value(op.operands[2]) if len(op.operands) > 2 else None
        t = self.result_type(op)
        buffer = ptr.buffer
        self.reads.add(buffer)
        if not t.is_tensor:
            name = self.fresh(op.results[0])
            self.emit(f'let {name}: {scalar_term(t.element)} = {buffer}[cast<u32>({ptr.text})];')
            return self.define(op, Scalar(t, name))
        out = self.new_data(t, op.results[0])
        zero = ZERO.get(scalar_term(t.element), '0')

        def body(c):
            address = f'{buffer}[cast<u32>({self.element(ptr, c, True)})]'
            if mask is None:
                self.emit(f'{out}[{self.k}] = {address};')
                return
            self.open(f'if ({self.element(mask, c, True)})')
            self.emit(f'{out}[{self.k}] = {address};')
            self.close()
            self.open('else')
            self.emit(f'{out}[{self.k}] = {self.element(other, c, True) if other is not None else zero};')
            self.close()
        operands = [ptr, mask, other]
        if any(isinstance(o, Data) and o.type.shape != t.shape for o in operands if o is not None):
            raise Unsupported(op, 'internal: operand shapes differ')
        self.owned(t, body)
        self.define(op, Data(t, out))

    def store(self, op):
        ptr, value = self.value(op.operands[0]), self.value(op.operands[1])
        mask = self.value(op.operands[2]) if len(op.operands) > 2 else None
        buffer = ptr.buffer
        self.writes.add(buffer)
        t = ptr.type
        if not t.is_tensor:
            self.open('if (qpel.local.x == 0u)')  # a scalar store: once per program
            self.emit(f'{buffer}[cast<u32>({ptr.text})] = {value.text};')
            self.close()
            return

        def body(c):
            target = f'{buffer}[cast<u32>({self.element(ptr, c, True)})] = {self.element(value, c, True)};'
            if mask is None:
                self.emit(target)
            else:
                self.open(f'if ({self.element(mask, c, True)})')
                self.emit(target)
                self.close()
        self.owned(t, body)

    def reduce(self, op):
        src = self.value(op.operands[0])
        axis = op.attribute('axis')
        region = op.regions[0]
        body = [o for o in region.ops if o.name != 'tt.reduce.return']
        if len(op.operands) != 1 or len(body) != 1 or body[0].name not in COMBINERS or \
                body[0].operands != [a for a, _ in region.arguments]:
            raise Unsupported(op, 'a reduction other than one sum, product, min or max of its two arguments')
        combine = COMBINERS[body[0].name]
        t = src.type
        if body[0].name in ('arith.addf', 'arith.mulf'):
            self.float_sums.append(t.shape[axis])
        shared = self.materialize(src, op.results[0]) if not isinstance(src, (Lazy, Shared)) else src
        term = scalar_term(t.element)
        n = t.shape[axis]
        out_shape = t.shape[:axis] + t.shape[axis + 1:]
        if not out_shape:
            name = self.fresh(op.results[0])
            self.emit(f'let {name}: {term} = {self.element(shared, ["0"], False)};')
            j = self.fresh('j')
            self.open(f'for (let {j}: i32 = 1; {j} < {n}; {j} = {j} + 1)')
            self.emit(f'{name} = {combine(name, self.element(shared, [j], False))};')
            self.close()
            return self.define(op, Scalar(Type((), t.element), name))
        rt = Type(out_shape, t.element)
        out = self.new_data(rt, op.results[0])
        j = self.fresh('j')

        def fold(c):
            at = lambda index: self.element(shared, c[:axis] + [index] + c[axis:], False)
            self.emit(f'{out}[{self.k}] = {at("0")};')
            self.open(f'for (let {j}: i32 = 1; {j} < {n}; {j} = {j} + 1)')
            self.emit(f'{out}[{self.k}] = {combine(f"{out}[{self.k}]", at(j))};')
            self.close()
        self.owned(rt, fold)
        self.define(op, Data(rt, out))

    def loop(self, op):
        m = re.match(r'\s*(%[\w$.#-]+)\s*=\s*(%[\w$.#-]+)\s+to\s+(%[\w$.#-]+)\s+step\s+(%[\w$.#-]+)', op.text)
        if not m:
            raise Unsupported(op, 'loop header not read')
        iv, lb, ub, step = m.groups()
        carried = re.findall(r'(%[\w$.#-]+)\s*=\s*(%[\w$.#-]+)', op.text.split('iter_args', 1)[1]) if 'iter_args' in op.text else []
        variables = []
        for (arg, init), result in zip(carried, op.results):
            v = self.value(init)
            if v.type.is_tensor:
                name = self.new_data(v.type, arg)
                self.owned(v.type, lambda c, v=v, name=name: self.emit(f'{name}[{self.k}] = {self.element(v, c, True)};'))
                variables.append((arg, Data(v.type, name), result))
            else:
                name = self.fresh(arg)
                self.emit(f'let {name}: {scalar_term(v.type.element)} = {v.text};')
                variables.append((arg, Scalar(v.type, name), result))
        for arg, var, _ in variables:
            self.values[arg] = var
        index = self.fresh(iv)
        bounds = [self.value(x).text for x in (lb, ub, step)]
        self.open(f'for (let {index}: i32 = {bounds[0]}; {index} < {bounds[1]}; {index} = {index} + {bounds[2]})')
        self.values[iv] = Scalar(Type((), 'i32'), index)
        body = op.regions[0].ops
        self.block(body)
        yielded = next((o for o in body if o.name == 'scf.yield'), None)
        if yielded is not None:
            new = [self.value(x) for x in yielded.operands]
            temps = []
            for (arg, var, _), v in zip(variables, new):  # yields are computed before any is assigned
                if isinstance(var, Data):
                    if isinstance(v, Data) and v.name == var.name:
                        temps.append(None)
                        continue
                    tmp = self.new_data(var.type, arg + '_next')
                    self.owned(var.type, lambda c, v=v, tmp=tmp: self.emit(f'{tmp}[{self.k}] = {self.element(v, c, True)};'))
                    temps.append(tmp)
                else:
                    tmp = self.fresh(arg + '_next')
                    self.emit(f'let {tmp}: {scalar_term(var.type.element)} = {v.text};')
                    temps.append(tmp)
            for (arg, var, _), tmp in zip(variables, temps):
                if tmp is None:
                    continue
                if isinstance(var, Data):
                    self.owned(var.type, lambda c, var=var, tmp=tmp: self.emit(f'{var.name}[{self.k}] = {tmp}[{self.k}];'))
                else:
                    self.emit(f'{var.text} = {tmp};')
        self.close()
        for arg, var, result in variables:
            self.values[result] = var

    # -- output --

    def text(self):
        params = []
        for i, (name, t) in enumerate(self.params):
            if t.is_pointer:
                access = 'write' if name in self.writes else 'read'
                params.append(f'{name}: buffer<{scalar_term(t.pointee)}, {access}> @{i}')
            else:
                params.append(f'{name}: {scalar_term(t.element)} @{i}')
        head = [f'module {self.module} {{', f'  kernel {self.function.name}({", ".join(params)}) groups {{']
        head += [f'    let {name}: {t};' for name, t in self.shared]
        body = ['  ' + line for line in self.lines]
        return '\n'.join(head + body + ['  }', '}']) + '\n'

    def pipeline(self):
        """The pipeline text: a shape per buffer (its length), the grid of programs, scalar values."""
        lines = [f'pipeline {self.module}_launch {{', '  shape programs;']
        binds = []
        for name, t in self.params:
            if t.is_pointer:
                lines.append(f'  shape {name}_size;')
        for name, t in self.params:
            if t.is_pointer:
                kind = 'inout' if name in self.writes else 'input'
                access = 'write' if name in self.writes else 'read'
                lines.append(f'  {kind} {name}: buffer<{scalar_term(t.pointee)}, {access}> in {name}_size;')
            else:
                lines.append(f'  value {name}: {scalar_term(t.element)};')
            binds.append(f'{name} = {name}')
        lines.append(f'  dispatch {self.function.name} over programs block ({self.T}, 1, 1) groups ("W", "H", "N") '
                     f'({", ".join(binds)});')
        lines.append('}')
        return '\n'.join(lines) + '\n'


def lower(text, num_warps=4, module=None):
    """(qpel text of the module and its pipeline, Lowering) for one TTIR function."""
    lowering = Lowering(parse(text), num_warps, module)
    module_text = lowering.run()
    return module_text + lowering.pipeline(), lowering
