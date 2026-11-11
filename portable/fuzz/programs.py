"""Random well-typed qpel programs, as text (TEXT.md).

A program reads an i64 buffer `a` and an f32 buffer `f` and writes a few outputs of
random types, each a random expression over them: integer arithmetic at every width
(division only by values made odd, so never zero), shifts, comparisons and selects,
conversions in every direction (float -> integer saturates), and the exact math
functions. Locals hold shared subexpressions. The same seed gives the same program.
"""
import random

from qpel_types import arithmetic, bits, is_signed

INTEGERS = ['u8', 'i8', 'u16', 'i16', 'u32', 'i32', 'u64', 'i64']
TYPES = INTEGERS + ['f32']
EXACT = ['fabs', 'floor', 'ceil', 'trunc', 'round']


class Generator:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.locals = []  # (name, type) visible so far

    def literal(self, t):
        r = self.rng
        if t == 'f32':
            return f'{r.choice(["0.5", "1.0", "3.0", "0.125", "1000.0", "1e-3", "7.25"])}f'
        size, signed = bits(t), is_signed(t)
        high = 2**(size - 1) - 1 if signed else 2**size - 1
        value = r.choice([0, 1, 2, 3, 7, min(255, high), high, r.randrange(0, high + 1)])
        if signed and r.random() < 0.3:
            value = -value - r.choice([0, 1])  # down to the type's minimum
            return f'const<{t}>({value})' if value < 0 else f'{value}_{t}'
        return f'{value}_{t}'

    def leaf(self, t):
        r = self.rng
        options = [lambda: self.literal(t), lambda: f'cast<{t}>(a[qpel.index])', lambda: f'cast<{t}>(f[qpel.index])']
        mine = [name for name, lt in self.locals if lt == t]
        if mine:
            options.append(lambda: r.choice(mine))
        return r.choice(options)()

    def expr(self, t, depth):
        r = self.rng
        if depth <= 0 or r.random() < 0.2:
            return self.leaf(t)
        if t == 'f32':
            choice = r.randrange(5)
            if choice == 0:
                op = r.choice('+-*/')
                return f'({self.expr("f32", depth - 1)}) {op} ({self.expr("f32", depth - 1)})'
            if choice == 1:
                return f'math.{r.choice(EXACT)}({self.expr("f32", depth - 1)})'
            if choice == 2:
                fn = r.choice(['fmin', 'fmax', 'copysign'])
                return f'math.{fn}({self.expr("f32", depth - 1)}, {self.expr("f32", depth - 1)})'
            if choice == 3:
                return f'math.sqrt(math.fabs({self.expr("f32", depth - 1)}))'
            return f'cast<f32>({self.expr(r.choice(INTEGERS), depth - 1)})'
        wide = arithmetic(t)  # the type arithmetic on t happens in
        narrow = (lambda text: f'cast<{t}>({text})') if wide != t else (lambda text: text)
        choice = r.randrange(7)
        if choice == 0:
            op = r.choice(['+', '-', '*', '&', '|', '^'])
            return narrow(f'({self.expr(wide, depth - 1)}) {op} ({self.expr(wide, depth - 1)})')
        if choice == 1:
            op = r.choice(['/', '%'])
            one = '1u' if wide == 'u32' else '1' if wide == 'i32' else f'1_{wide}'
            return narrow(f'({self.expr(wide, depth - 1)}) {op} (({self.expr(wide, depth - 1)}) | {one})')
        if choice == 2:
            op = r.choice(['<<', '>>'])
            return narrow(f'({self.expr(wide, depth - 1)}) {op} ({self.expr(r.choice(["u32", "i32", "u8"]), depth - 1)})')
        if choice == 3:
            op = r.choice(['-', '~'])
            return narrow(f'{op}({self.expr(t, depth - 1)})')
        if choice == 4:
            u = r.choice(TYPES)
            op = r.choice(['<', '<=', '==', '!=', '>', '>='])
            return f'(({self.expr(u, depth - 1)}) {op} ({self.expr(u, depth - 1)})) ? ({self.expr(t, depth - 1)}) : ({self.expr(t, depth - 1)})'
        if choice == 5:
            return narrow(f'{r.choice(["min", "max"])}({self.expr(wide, depth - 1)}, {self.expr(wide, depth - 1)})')
        return f'cast<{t}>({self.expr(r.choice(TYPES), depth - 1)})'

    def program(self):
        r = self.rng
        body = ['    if (qpel.index >= ((count.x * count.y) * count.z)) {', '      return;', '    }']
        for k in range(r.randrange(1, 4)):
            t = r.choice(TYPES)
            body.append(f'    let v{k}: {t} = {self.expr(t, 3)};')
            self.locals.append((f'v{k}', t))
        outputs = r.sample(TYPES, r.randrange(1, 4))
        params = ['a: buffer<i64, read> @0', 'f: buffer<f32, read> @1']
        params += [f'o_{t}: buffer<{t}, write> @{i + 2}' for i, t in enumerate(outputs)]
        params.append(f'count: shape3 @{len(outputs) + 2}')
        for t in outputs:
            body.append(f'    o_{t}[qpel.index] = {self.expr(t, 4)};')
        bindings = ', '.join(['a = a', 'f = f'] + [f'o_{t} = o_{t}' for t in outputs] + ['count = count'])
        resources = ['  input a: buffer<i64, read> in count;', '  input f: buffer<f32, read> in count;']
        resources += [f'  output o_{t}: buffer<{t}, write> in count;' for t in outputs]
        resources.append('  shape count;')
        head = '  kernel fuzz(' + ', '.join(params) + ') grid1d {'
        return '\n'.join(['module fuzz {', head, *body, '  }', '}',
                          'pipeline fuzz_launch {', *resources,
                          f'  dispatch fuzz over count block (64) ({bindings});', '}']) + '\n'


def generate(seed):
    """Canonical text of a random program (printed by qpel_text, so print(parse(t)) == t)."""
    from qpel_text import parse_program, print_program
    return print_program(parse_program(Generator(seed).program()))
