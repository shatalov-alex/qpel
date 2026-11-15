"""A reader for Triton's TTIR (the `tt`, `arith`, `math` and `scf` dialects as Triton prints them).

Only what qpel's Triton frontend reads: one `tt.func`, its arguments, and its operations with
their results, operands, attributes, types and regions (`scf.for`, `scf.if`, `tt.reduce`).
Locations (`loc(...)`) are dropped. Anything else is kept as an operation the importer reports.
"""
import re
from dataclasses import dataclass, field

NAME = r'%[A-Za-z0-9_$.#-]+'


@dataclass
class Type:
    shape: tuple  # () for a scalar
    element: str  # 'i1' 'i32' 'i64' 'f16' 'bf16' 'f32' or 'ptr<f32>' ...

    @property
    def is_tensor(self):
        return bool(self.shape)

    @property
    def is_pointer(self):
        return self.element.startswith('ptr<')

    @property
    def pointee(self):
        return self.element[4:-1]

    @property
    def numel(self):
        n = 1
        for d in self.shape:
            n *= d
        return n

    def __str__(self):
        return ('tensor<' + 'x'.join(map(str, self.shape)) + 'x' + self.element + '>') if self.shape else self.element


@dataclass
class Op:
    name: str
    results: list
    operands: list
    text: str  # the operation's own text after its name, locations removed
    types: list  # the types after ':' (operands, or a function type's inputs)
    result_types: list
    regions: list = field(default_factory=list)  # [Block]
    line: int = 0

    def attribute(self, key, default=None):
        found = re.search(rf'\b{key}\s*=\s*([^,}}]+)', self.text)
        if not found:
            return default
        value = found.group(1).strip()
        number = re.match(r'(-?[0-9]+)\s*:\s*i[0-9]+$', value)
        return int(number.group(1)) if number else value


@dataclass
class Block:
    arguments: list  # [(name, Type)]
    ops: list


@dataclass
class Function:
    name: str
    arguments: list  # [(name, Type)]
    body: Block


class ParseError(Exception):
    pass


def strip_locations(text):
    """Remove every `loc(...)` (balanced parentheses) and the `#locN = ...` lines."""
    out, i = [], 0
    while True:
        j = text.find('loc(', i)
        if j < 0:
            out.append(text[i:])
            break
        if j > 0 and (text[j - 1].isalnum() or text[j - 1] in '_#'):
            out.append(text[i:j + 4])
            i = j + 4
            continue
        out.append(text[i:j].rstrip(' '))
        depth, k = 0, j + 3
        while k < len(text):
            if text[k] == '(':
                depth += 1
            elif text[k] == ')':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1
    return '\n'.join(line for line in ''.join(out).splitlines() if not line.startswith('#loc'))


def parse_type(text):
    text = text.strip()
    if text.startswith('!tt.ptr<'):
        return Type((), 'ptr<' + parse_type(text[8:-1]).element + '>')
    if text.startswith('tensor<'):
        inner = text[7:-1]
        # dims are the leading [0-9]+x; the element may itself contain x (!tt.ptr<f32>)
        dims = []
        while True:
            m = re.match(r'([0-9]+)x', inner)
            if not m:
                break
            dims.append(int(m.group(1)))
            inner = inner[m.end():]
        inner = re.sub(r',\s*#.*$', '', inner)  # a TTGIR layout encoding
        return Type(tuple(dims), parse_type(inner).element)
    if re.fullmatch(r'[if][0-9]+|bf16|index', text):
        return Type((), text)
    raise ParseError(f'type {text} is not read')


def split_top(text, sep=','):
    """Split on sep outside <>, (), [] and {}."""
    parts, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c in '<([{':
            depth += 1
        elif c in '>)]}':
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def types_of(text):
    """The types of an operation's trailing `: ...`: a list, or (inputs, results) for a function type."""
    text = text.strip()
    if text.startswith('(') and ')' in text and '->' in text:
        depth = 0
        for i, c in enumerate(text):
            depth += c == '('
            depth -= c == ')'
            if depth == 0:
                break
        inputs = [parse_type(t) for t in split_top(text[1:i])]
        results = text[i + 1:].strip()[2:].strip()
        results = results[1:-1] if results.startswith('(') else results
        return inputs, [parse_type(t) for t in split_top(results)]
    if '->' in text:
        left, right = text.split('->', 1)
        right = right.strip()
        right = right[1:-1] if right.startswith('(') else right
        return [parse_type(t) for t in split_top(left)], [parse_type(t) for t in split_top(right)]
    if ' to ' in text:
        left, right = text.split(' to ', 1)
        return [parse_type(left)], [parse_type(right)]
    return [parse_type(t) for t in split_top(text)], None


def trailing_types(text):
    """(text before the trailing ` : types`, the types text) of an operation."""
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        c = text[i]
        if c == '>' and i > 0 and text[i - 1] == '-':
            continue  # the arrow of a function type
        if c in '>)]}':
            depth += 1
        elif c in '<([{':
            depth -= 1
        elif c == ':' and depth == 0 and i > 0 and text[i - 1] == ' ':
            return text[:i].rstrip(), text[i + 1:].strip()
    return text, ''


class Reader:
    def __init__(self, text):
        self.lines = [line.strip() for line in strip_locations(text).splitlines()]
        self.lines = [line for line in self.lines if line]
        self.i = 0

    def peek(self):
        return self.lines[self.i] if self.i < len(self.lines) else None

    def next(self):
        line = self.lines[self.i]
        self.i += 1
        return line

    def function(self):
        while self.peek() is not None and not self.peek().startswith('tt.func'):
            self.next()
        if self.peek() is None:
            raise ParseError('no tt.func')
        line = self.next()
        m = re.match(r'tt\.func\s+(?:public\s+|private\s+)?@([A-Za-z0-9_]+)\((.*)\)\s*(?:->.*?)?(?:attributes\s*\{.*\})?\s*\{$', line)
        if not m:
            raise ParseError(f'function header not read: {line[:120]}')
        arguments = []
        for part in split_top(m.group(2)):
            name, _, t = part.partition(':')
            t = re.sub(r'\{.*\}$', '', t).strip()
            arguments.append((name.strip(), parse_type(t)))
        return Function(m.group(1), arguments, Block(arguments, self.ops()))

    def ops(self, end=('}',)):
        """Operations up to a line starting with one of `end` (not consumed)."""
        ops = []
        while True:
            line = self.peek()
            if line is None:
                raise ParseError('unexpected end of TTIR')
            if any(line.startswith(e) for e in end):
                return ops
            ops.append(self.op(self.next()))

    def op(self, line):
        number = self.i
        results = []
        m = re.match(rf'((?:{NAME}(?::[0-9]+)?)(?:\s*,\s*{NAME})*)\s*=\s*(.*)$', line)
        body = line
        if m:
            for r in split_top(m.group(1)):
                if ':' in r:  # %x:2 = ... : results %x#0 and %x#1
                    base, count = r.split(':')
                    results += [f'{base}#{j}' for j in range(int(count))]
                else:
                    results.append(r)
            body = m.group(2)
        generic = re.match(r'"([a-z_.]+)"(.*)$', body)
        name, rest = (generic.group(1), generic.group(2)) if generic else body.split(' ', 1) if ' ' in body else (body, '')
        regions = []
        if rest.rstrip().endswith('({'):  # a generic op with a region: "tt.reduce"(%x) <{...}> ({ ^bb0(...): ... }) : ...
            regions.append(self.block())
            close = self.next()  # }) : (types) -> types
            rest = rest.rstrip()[:-2] + close[2:]
        elif rest.rstrip().endswith('{'):  # scf.for ... {, scf.if ... {
            rest = rest.rstrip()[:-1].rstrip()
            regions.append(Block([], self.ops()))
            close = self.next()
            while close.startswith('} else {'):
                regions.append(Block([], self.ops()))
                close = self.next()
            if close != '}':
                rest += ' ' + close[1:].strip()
        before, types_text = trailing_types(rest)
        operand_types, result_types = types_of(types_text) if types_text else ([], None)
        operands = re.findall(NAME, re.sub(r'\{[^{}]*\}', '', before))
        return Op(name, results, operands, rest, operand_types, result_types, regions, number)

    def block(self):
        header = self.next()
        m = re.match(r'\^bb[0-9]+\((.*)\):$', header)
        arguments = []
        if m:
            for part in split_top(m.group(1)):
                name, _, t = part.partition(':')
                arguments.append((name.strip(), parse_type(t)))
        return Block(arguments, self.ops(end=('})',)))


def parse(text):
    return Reader(text).function()
