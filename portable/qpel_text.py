"""The qpel text syntax: print any qpel program as text, and parse text back into IR.

    module NAME { struct ... ; kernel ... ; fn ... }
    pipeline NAME { shape ...; input ...; dispatch ...; }

The syntax follows the IR one to one (TEXT.md describes it): parse(print(p)) is p up
to node ids and spelling (version 1/2 concept names come back as version 3 terms,
e.g. U32Type as u32, I32Cast as cast<i32>), and print(parse(text)) is text.

    python3 qpel_text.py print program.qpel.json      # IR -> text
    python3 qpel_text.py parse program.qpel > out.json # text -> IR
"""
import argparse
import json
import re
import sys
from pathlib import Path

from qpel_types import LEGACY_CASTS, MATH, is_shared_view, term

BUILTIN_AXES = {'ThreadIndex': 'thread', 'GroupIndex': 'group', 'LocalIndex': 'local', 'GroupSize': 'group_size',
                'GroupCount': 'group_count'}
ATOMICS = {'AtomicLoad': 'load', 'AtomicStore': 'store', 'AtomicAdd': 'add', 'AtomicMin': 'min',
           'AtomicCompareExchange': 'cas'}
SUBGROUP_BUILTINS = {'SubgroupLane': 'subgroup_lane', 'SubgroupIndex': 'subgroup_index', 'SubgroupSize': 'subgroup_size'}
SHUFFLES = {'index': 'shuffle', 'xor': 'shuffle_xor', 'down': 'shuffle_down', 'up': 'shuffle_up'}
DOMAINS = {'Grid1D': 'grid1d', 'Grid3D': 'grid3d', 'Groups': 'groups'}
PRECEDENCE = [('||',), ('&&',), ('|',), ('^',), ('&',), ('==', '!='), ('<', '>', '<=', '>='), ('<<', '>>'),
              ('+', '-'), ('*', '/', '%')]


# --- printing ---------------------------------------------------------------

class Printer:
    def __init__(self, program):
        self.nodes = {n['id']: n for n in program['nodes']}
        self.roots = program['roots']

    def children(self, n, role):
        return [self.nodes[i] for i in n['children'].get(role, [])]

    def child(self, n, role):
        return self.children(n, role)[0]

    def link(self, n, role):
        return self.nodes[n['links'][role]]

    def type(self, n):
        return term(n)

    def program(self):
        module = self.nodes[self.roots['module']]
        lines = [f'module {module["attributes"]["name"]} {{']
        for s in self.children(module, 'structs'):
            fields = ' '.join(f'{f["attributes"]["name"]}: {self.type(self.child(f, "type"))};'
                              for f in self.children(s, 'fields'))
            lines.append(f'  struct {s["attributes"]["name"]} {{ {fields} }}')
        for f in self.children(module, 'functions'):
            lines.extend(self.function(f))
        lines.append('}')
        lines.extend(self.pipeline(self.nodes[self.roots['pipeline']]))
        return '\n'.join(lines) + '\n'

    def function(self, f):
        a = f['attributes']
        params = []
        for p in self.children(f, 'parameters'):
            pa = p['attributes']
            text = ('ref ' if pa.get('reference') else '') + f'{pa["name"]}: {self.type(self.child(p, "type"))}'
            if pa.get('isBuffer'):
                text += f' @{pa.get("binding", 0)}'
            params.append(text)
        if a.get('isKernel'):
            domain = DOMAINS[self.child(f, 'domain')['kind']]
            head = f'  kernel {a["name"]}({", ".join(params)}) {domain} '
        else:
            head = f'  fn {a["name"]}({", ".join(params)}) -> {self.type(self.child(f, "resultType"))} '
        return [head + self.block(self.child(f, 'body'), 1)]

    def block(self, b, depth):
        inner = [self.statement(s, depth + 1) for s in self.children(b, 'statements')]
        if not inner:
            return '{ }'
        return '{\n' + '\n'.join(inner) + '\n' + '  '*depth + '}'

    def statement(self, s, depth, bare=False):
        pad = '' if bare else '  '*depth
        end = '' if bare else ';'
        k = s['kind']
        if k == 'Variable':
            value = f' = {self.expr(self.child(s, "value"))}' if s['children'].get('value') else ''
            return f'{pad}let {s["attributes"]["name"]}: {self.type(self.child(s, "type"))}{value}{end}'
        if k == 'Assign':
            return f'{pad}{self.expr(self.child(s, "target"))} = {self.expr(self.child(s, "value"))}{end}'
        if k == 'Evaluate':
            return f'{pad}{self.expr(self.child(s, "value"))}{end}'
        if k == 'Return':
            value = ' ' + self.expr(self.child(s, 'value')) if s['children'].get('value') else ''
            return f'{pad}return{value};'
        if k in ('Break', 'Continue'):
            return f'{pad}{k.lower()};'
        if k == 'Block':
            return pad + self.block(s, depth)
        if k == 'If':
            text = f'{pad}if ({self.expr(self.child(s, "condition"))}) {self.block(self.child(s, "thenBody"), depth)}'
            if s['children'].get('elseBody'):
                text += ' else ' + self.block(self.child(s, 'elseBody'), depth)
            return text
        if k == 'While':
            return f'{pad}while ({self.expr(self.child(s, "condition"))}) {self.block(self.child(s, "body"), depth)}'
        if k == 'DoWhile':
            return f'{pad}do {self.block(self.child(s, "body"), depth)} while ({self.expr(self.child(s, "condition"))});'
        if k == 'For':
            part = lambda role, f: f(self.child(s, role)) if s['children'].get(role) else ''
            init = part('init', lambda n: self.statement(n, 0, bare=True))
            cond = part('condition', self.expr)
            step = ', '.join(self.statement(n, 0, bare=True) for n in self.children(s, 'step'))
            return f'{pad}for ({init}; {cond}; {step}) {self.block(self.child(s, "body"), depth)}'
        raise ValueError('Not a statement: ' + k)

    def operand(self, n):
        text = self.expr(n)
        return f'({text})' if n['kind'] in ('Binary', 'Select') else text

    def expr(self, n):
        k, a = n['kind'], n['attributes']
        e = lambda role: self.expr(self.child(n, role))
        args = lambda role: ', '.join(self.expr(x) for x in self.children(n, role))
        if k == 'Name':
            return self.link(n, 'declaration')['attributes']['name']
        if k == 'ArrayValue':
            return '[' + args('elements') + ']'
        if k == 'VectorValue':
            return f'vec<{a["element"]}, {a["length"]}>(' + args('elements') + ')'
        if k == 'Literal':
            return a['value'] if a['value'] in ('true', 'false') else str(int(a['value'].rstrip('uU'))) + 'u'
        if k == 'I32Literal':
            return a['value']
        if k == 'Constant':
            value = int(a['value'])
            return f'{value}_{a["scalar"]}' if value >= 0 else f'const<{a["scalar"]}>({value})'
        if k == 'F32Literal':
            v = a['value']
            return v + ('' if '.' in v or 'e' in v.lower() else '.0') + 'f'
        if k == 'Cast' or k in LEGACY_CASTS:
            return f'cast<{a["to"] if k == "Cast" else LEGACY_CASTS[k]}>({e("operand")})'
        if k == 'View':
            return f'view<{a["element"]}>({e("base")})'
        if k == 'Math':
            return f'math.{a["function"]}({args("arguments")})'
        if k in ('Minimum', 'Maximum'):
            return f'{"min" if k == "Minimum" else "max"}({e("left")}, {e("right")})'
        if k == 'F32Fma':
            return f'qpel.fma({e("left")}, {e("right")}, {e("addend")})'
        if k == 'ReduceSumF32':
            return f'qpel.sum_f32({e("buffer")}, {e("start")}, {e("count")})'
        if k == 'WorkgroupSumF32':
            return f'qpel.group_sum_f32({e("value")})'
        if k == 'Barrier':
            return 'qpel.barrier()'
        if k == 'BarrierCount':
            return f'qpel.barrier_count({e("value")})'
        if k == 'GridIndex':
            return 'qpel.index'
        if k in SUBGROUP_BUILTINS:
            return f'qpel.{SUBGROUP_BUILTINS[k]}'
        if k == 'SubgroupShuffle':
            return f'qpel.{SHUFFLES[a["mode"]]}({e("value")}, {e("operand")})'
        if k == 'SubgroupReduce':
            return f'qpel.subgroup_{a["operator"]}({e("value")})'
        if k in BUILTIN_AXES:
            return f'qpel.{BUILTIN_AXES[k]}.{a["axis"]}'
        if k == 'AtomicCompareExchange':
            return f'atomic.cas({e("address")}, {e("expected")}, {e("value")})'
        if k in ATOMICS:
            value = f', {e("value")}' if n['children'].get('value') else ''
            return f'atomic.{ATOMICS[k]}({e("address")}{value})'
        if k == 'Index':
            return f'{self.operand(self.child(n, "base"))}[{e("index")}]'
        if k == 'Member':
            return f'{self.operand(self.child(n, "base"))}.{a["member"]}'
        if k == 'Call':
            return f'{self.link(n, "function")["attributes"]["name"]}({args("arguments")})'
        if k == 'Unary':
            return f'{a["operator"]}{self.operand(self.child(n, "operand"))}'
        if k == 'Binary':
            return f'{self.operand(self.child(n, "left"))} {a["operator"]} {self.operand(self.child(n, "right"))}'
        if k == 'Select':
            return f'{self.operand(self.child(n, "condition"))} ? {self.operand(self.child(n, "yes"))} : {self.operand(self.child(n, "no"))}'
        raise ValueError('Not an expression: ' + k)

    def pipeline(self, p):
        lines = [f'pipeline {p["attributes"]["name"]} {{']
        for r in self.children(p, 'resources'):
            a = r['attributes']
            text = f'  {a["kind"]} {a["name"]}'
            if a['kind'] != 'shape':
                text += f': {self.type(self.child(r, "type"))}'
            if 'extent' in r['links']:
                text += f' in {self.link(r, "extent")["attributes"]["name"]}'
                if 'scale' in r['attributes']:
                    text += f' * {r["attributes"]["scale"]}'
            if 'length' in r['attributes']:
                text += f' [{r["attributes"]["length"]}]'
            lines.append(text + ';')
        for s in self.children(p, 'stages'):
            a = s['attributes']
            block = str(a['blockSize'])
            if 'blockHeight' in a or 'blockDepth' in a:
                block += f', {a.get("blockHeight", 1)}, {a.get("blockDepth", 1)}'
            text = f'  dispatch {self.link(s, "kernel")["attributes"]["name"]} over {self.link(s, "grid")["attributes"]["name"]} block ({block})'
            if 'groupsX' in a:
                text += ' groups (' + ', '.join(json.dumps(a['groups' + x]) for x in 'XYZ') + ')'
            bindings = ', '.join(f'{self.link(b, "parameter")["attributes"]["name"]} = {self.link(b, "resource")["attributes"]["name"]}'
                                 for b in self.children(s, 'bindings'))
            lines.append(f'{text} ({bindings});')
        lines.append('}')
        return lines


def print_program(program):
    return Printer(program).program()


# --- parsing ----------------------------------------------------------------

TOKEN = re.compile(r'''
    (?P<space>\s+|//[^\n]*)
  | (?P<string>"[^"]*")
  | (?P<number>[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?(?:f|u|_[a-z0-9]+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|<=|>=|&&|\|\||<<|>>|->|[-+*/%&|^~!<>=?:;,.(){}\[\]@])
''', re.X)


class ParseError(ValueError):
    pass


class Parser:
    def __init__(self, text):
        self.tokens = []
        position = 0
        while position < len(text):
            m = TOKEN.match(text, position)
            if not m:
                line = text.count('\n', 0, position) + 1
                raise ParseError(f'line {line}: unexpected {text[position:position + 10]!r}')
            if m.lastgroup != 'space':
                self.tokens.append((m.lastgroup, m.group(), text.count('\n', 0, position) + 1))
            position = m.end()
        self.tokens.append(('end', '', text.count('\n') + 1))
        self.at = 0
        from frontends.c_import import Graph
        self.graph = Graph()
        self.scopes = []
        self.functions = {}  # name -> (IR id, is kernel)
        self.structs = set()

    # tokens
    def peek(self, offset=0):
        return self.tokens[self.at + offset]

    def fail(self, message):
        raise ParseError(f'line {self.peek()[2]}: {message} (at {self.peek()[1]!r})')

    def take(self, value=None, kind=None):
        token = self.peek()
        if value is not None and token[1] != value or kind is not None and token[0] != kind:
            self.fail(f'expected {value or kind}')
        self.at += 1
        return token[1]

    def accept(self, value):
        if self.peek()[1] == value:
            self.at += 1
            return True
        return False

    def add(self, kind, attributes=None, children=None, links=None):
        return self.graph.add(kind, attributes, children, links)

    # scopes
    def declare(self, name, ident):
        self.scopes[-1][name] = ident

    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        self.fail(f'unknown name {name}')

    # program
    def program(self):
        self.take('module')
        module_name = self.take(kind='name')
        self.take('{')
        structs, functions = [], []
        while not self.accept('}'):
            if self.peek()[1] == 'struct':
                structs.append(self.struct())
            else:
                functions.append(self.function())
        module = self.add('GpuModule', {'name': module_name}, {'functions': functions, 'structs': structs})
        pipeline = self.pipeline(module)
        self.take(kind='end')
        return self.graph.program(module, pipeline)

    def struct(self):
        self.take('struct')
        name = self.take(kind='name')
        self.structs.add(name)
        self.take('{')
        fields = []
        while not self.accept('}'):
            field = self.take(kind='name')
            self.take(':')
            fields.append(self.add('Field', {'name': field}, {'type': self.type()}))
            self.take(';')
        return self.add('StructDecl', {'name': name}, {'fields': fields})

    def type_term(self):
        name = self.take(kind='name')
        if name == 'buffer':
            self.take('<')
            element = self.take(kind='name')
            self.take(',')
            access = self.take(kind='name')
            self.take('>')
            return f'buffer<{element}, {access}>'
        if name == 'struct':
            return 'struct ' + self.take(kind='name')
        if name == 'shared':
            self.take('<')
            element = self.type_term()
            if self.accept('>'):
                return f'shared<{element}>'  # a view into shared memory (IR version 5)
            self.take(',')
            length = self.take(kind='number')
            self.take('>')
            return f'shared<{element}, {length}>'
        if name == 'vec':
            self.take('<')
            element = self.take(kind='name')
            self.take(',')
            length = self.take(kind='number')
            self.take('>')
            return f'vec<{element}, {length}>'
        if name == 'array':
            self.take('<')
            element = self.take(kind='name')
            self.take(',')
            length = self.take(kind='number')
            self.take('>')
            return f'array<{element}, {length}>'
        return name

    def type(self):
        """A type node, always in version 3 spelling."""
        t = self.type_term()
        if t == 'shape3':
            return self.add('Shape3DType')
        if t.startswith('buffer<'):
            element, access = t[len('buffer<'):-1].split(', ')
            return self.add('BufferType', {'element': element, 'access': access})
        if t.startswith('struct '):
            return self.add('StructType', {'name': t[len('struct '):]})
        if t.startswith('shared<') and is_shared_view(t):
            return self.add('SharedViewType', {'element': t[len('shared<'):-1]})
        if t.startswith('shared<'):
            element, length = t[len('shared<'):-1].rsplit(', ', 1)
            return self.add('SharedType', {'element': element, 'length': int(length)})
        if t.startswith('vec<'):
            element, length = t[len('vec<'):-1].split(', ')
            if not length.isdigit():
                self.fail('a vector length is 2, 3 or 4')
            return self.add('VectorType', {'element': element, 'length': int(length)})
        if t.startswith('array<'):
            element, length = t[len('array<'):-1].split(', ')
            if not length.isdigit():
                self.fail('an array length is a decimal number')
            return self.add('ArrayType', {'element': element, 'length': int(length)})
        return self.add('ScalarType', {'scalar': t})

    def function(self):
        kernel = self.take() == 'kernel'
        name = self.take(kind='name')
        ident = self.graph.reserve()
        self.functions[name] = (ident, kernel)
        self.scopes.append({})
        self.take('(')
        params = []
        while not self.accept(')'):
            reference = self.peek()[1] == 'ref' and self.peek(1)[0] == 'name'
            if reference:
                self.take('ref')
            pname = self.take(kind='name')
            self.take(':')
            ptype = self.type()
            attrs = {'name': pname} | ({'reference': True} if reference else {})
            if self.accept('@'):
                attrs.update(binding=int(self.take(kind='number')), isBuffer=True)
            params.append(self.add('Parameter', attrs, {'type': ptype}))
            self.declare(pname, params[-1])
            self.accept(',')
        children = {'parameters': params}
        if kernel:
            domain = {v: k for k, v in DOMAINS.items()}[self.take(kind='name')]
            children.update(domain=self.add(domain), resultType=self.add('ScalarType', {'scalar': 'void'}))
        else:
            self.take('->')
            children['resultType'] = self.type()
        children['body'] = self.block()
        self.scopes.pop()
        attrs = {'name': name, 'isKernel': True} if kernel else {'name': name}
        return self.graph.put(ident, 'Function', attrs, children)

    def block(self):
        self.take('{')
        self.scopes.append({})
        statements = []
        while not self.accept('}'):
            statements.append(self.statement())
        self.scopes.pop()
        return self.add('Block', children={'statements': statements})

    def statement(self, bare=False):
        word = self.peek()[1]
        if word == '{':
            return self.block()
        if word == 'let':
            self.take('let')
            name = self.take(kind='name')
            self.take(':')
            children = {'type': self.type()}
            if self.accept('='):
                children['value'] = self.expr()
            ident = self.add('Variable', {'name': name}, children)
            self.declare(name, ident)
            if not bare:
                self.take(';')
            return ident
        if word == 'return':
            self.take('return')
            children = {} if self.peek()[1] == ';' else {'value': self.expr()}
            self.take(';')
            return self.add('Return', children=children)
        if word in ('break', 'continue'):
            self.take()
            self.take(';')
            return self.add(word.capitalize())
        if word == 'if':
            self.take('if')
            self.take('(')
            condition = self.expr()
            self.take(')')
            children = {'condition': condition, 'thenBody': self.block()}
            if self.accept('else'):
                children['elseBody'] = self.block()
            return self.add('If', children=children)
        if word == 'while':
            self.take('while')
            self.take('(')
            condition = self.expr()
            self.take(')')
            return self.add('While', children={'condition': condition, 'body': self.block()})
        if word == 'do':
            self.take('do')
            body = self.block()
            self.take('while')
            self.take('(')
            condition = self.expr()
            self.take(')')
            self.take(';')
            return self.add('DoWhile', children={'body': body, 'condition': condition})
        if word == 'for':
            self.take('for')
            self.take('(')
            self.scopes.append({})
            children = {}
            if self.peek()[1] != ';':
                children['init'] = self.statement(bare=True)
            self.take(';')
            if self.peek()[1] != ';':
                children['condition'] = self.expr()
            self.take(';')
            steps = []
            while self.peek()[1] != ')':
                steps.append(self.statement(bare=True))
                self.accept(',')
            if steps:
                children['step'] = steps
            self.take(')')
            children['body'] = self.block()
            self.scopes.pop()
            return self.add('For', children=children)
        target = self.expr()
        if self.accept('='):
            node = self.add('Assign', children={'target': target, 'value': self.expr()})
        else:
            node = self.add('Evaluate', children={'value': target})
        if not bare:
            self.take(';')
        return node

    # expressions
    def expr(self):
        condition = self.binary(0)
        if self.accept('?'):
            yes = self.expr()
            self.take(':')
            no = self.expr()
            return self.add('Select', children={'condition': condition, 'yes': yes, 'no': no})
        return condition

    def binary(self, level):
        if level == len(PRECEDENCE):
            return self.unary()
        left = self.binary(level + 1)
        while self.peek()[0] == 'op' and self.peek()[1] in PRECEDENCE[level]:
            op = self.take()
            right = self.binary(level + 1)
            left = self.add('Binary', {'operator': op}, {'left': left, 'right': right})
        return left

    def unary(self):
        if self.peek()[1] in ('-', '!', '~'):
            op = self.take()
            return self.add('Unary', {'operator': op}, {'operand': self.unary()})
        return self.postfix(self.primary())

    def postfix(self, node):
        while True:
            if self.accept('['):
                index = self.expr()
                self.take(']')
                node = self.add('Index', children={'base': node, 'index': index})
            elif self.peek()[1] == '.' and self.peek(1)[0] == 'name':
                self.take('.')
                node = self.add('Member', {'member': self.take(kind='name')}, {'base': node})
            else:
                return node

    def arguments(self):
        self.take('(')
        args = []
        while not self.accept(')'):
            args.append(self.expr())
            self.accept(',')
        return args

    def primary(self):
        kind, value, _ = self.peek()
        if value == '[':  # an array value, initializing a local array
            self.take('[')
            elements = []
            while not self.accept(']'):
                elements.append(self.expr())
                if self.peek()[1] != ']':
                    self.take(',')
            return self.add('ArrayValue', children={'elements': elements})
        if value == '(':
            self.take('(')
            node = self.expr()
            self.take(')')
            return node
        if kind == 'number':
            self.take()
            if value.endswith('f'):
                return self.add('F32Literal', {'value': value[:-1]})
            if value.endswith('u'):
                return self.add('Literal', {'value': value})
            if '_' in value:
                number, scalar = value.split('_', 1)
                return self.add('Constant', {'scalar': scalar, 'value': number})
            if '.' in value or 'e' in value.lower():
                self.fail('float literals end in f')
            return self.add('I32Literal', {'value': value})
        if kind != 'name':
            self.fail('expected an expression')
        self.take()
        if value in ('true', 'false'):
            return self.add('Literal', {'value': value})
        if value == 'vec':  # vec<f32, 3>(a, b, c)
            self.take('<')
            element = self.take(kind='name')
            self.take(',')
            length = self.take(kind='number')
            self.take('>')
            if not length.isdigit():
                self.fail('a vector length is 2, 3 or 4')
            return self.add('VectorValue', {'element': element, 'length': int(length)},
                            {'elements': self.arguments()})
        if value in ('cast', 'view', 'const'):
            self.take('<')
            target = self.take(kind='name')
            self.take('>')
            args = self.arguments()
            if value == 'cast':
                return self.add('Cast', {'to': target}, {'operand': args[0]})
            if value == 'view':
                return self.add('View', {'element': target}, {'base': args[0]})
            negative = self.graph.nodes[args[0]]
            if negative['kind'] != 'Unary' or negative['attributes']['operator'] != '-':
                self.fail('const<T>(-n) is for negative constants; write n_T otherwise')
            magnitude = self.graph.nodes[negative['children']['operand'][0]]
            return self.add('Constant', {'scalar': target, 'value': '-' + magnitude['attributes']['value']})
        if value == 'math':
            self.take('.')
            function = self.take(kind='name')
            if function not in MATH:
                self.fail('unknown math function ' + function)
            return self.add('Math', {'function': function}, {'arguments': self.arguments()})
        if value in ('min', 'max'):
            left, right = self.arguments()
            return self.add('Minimum' if value == 'min' else 'Maximum', children={'left': left, 'right': right})
        if value == 'atomic':
            self.take('.')
            op = self.take(kind='name')
            kind = {v: k for k, v in ATOMICS.items()}[op]
            args = self.arguments()
            children = {'address': args[0]}
            if kind == 'AtomicCompareExchange':
                if len(args) != 3:
                    self.fail('atomic.cas takes an address, the expected value and the new value')
                children.update(expected=args[1], value=args[2])
            elif len(args) > 1:
                children['value'] = args[1]
            return self.add(kind, children=children)
        if value == 'qpel':
            self.take('.')
            what = self.take(kind='name')
            if what == 'index':
                return self.add('GridIndex')
            if what == 'barrier':
                self.take('(')
                self.take(')')
                return self.add('Barrier')
            if what == 'barrier_count':
                return self.add('BarrierCount', children={'value': self.arguments()[0]})
            if what in SUBGROUP_BUILTINS.values():
                return self.add({v: k for k, v in SUBGROUP_BUILTINS.items()}[what])
            if what in SHUFFLES.values():
                args = self.arguments()
                if len(args) != 2:
                    self.fail(f'qpel.{what} takes a value and a lane, mask or delta')
                mode = {v: k for k, v in SHUFFLES.items()}[what]
                return self.add('SubgroupShuffle', {'mode': mode}, {'value': args[0], 'operand': args[1]})
            if what.startswith('subgroup_') and what[9:] in ('add', 'min', 'max', 'and', 'or', 'xor'):
                args = self.arguments()
                if len(args) != 1:
                    self.fail(f'qpel.{what} takes one value')
                return self.add('SubgroupReduce', {'operator': what[9:]}, {'value': args[0]})
            if what in BUILTIN_AXES.values():
                self.take('.')
                axis = self.take(kind='name')
                return self.add({v: k for k, v in BUILTIN_AXES.items()}[what], {'axis': axis})
            args = self.arguments()
            if what == 'fma':
                return self.add('F32Fma', children=dict(zip(('left', 'right', 'addend'), args)))
            if what == 'sum_f32':
                return self.add('ReduceSumF32', children=dict(zip(('buffer', 'start', 'count'), args)))
            if what == 'group_sum_f32':
                return self.add('WorkgroupSumF32', children={'value': args[0]})
            self.fail('unknown builtin qpel.' + what)
        if self.peek()[1] == '(':
            if value not in self.functions:
                self.fail(f'unknown function {value}')
            return self.add('Call', children={'arguments': self.arguments()}, links={'function': self.functions[value][0]})
        return self.add('Name', links={'declaration': self.lookup(value)})

    def pipeline(self, module):
        self.take('pipeline')
        name = self.take(kind='name')
        self.take('{')
        resources, stages, ids, extents = [], [], {}, []
        while not self.accept('}'):
            word = self.take(kind='name')
            if word == 'dispatch':
                stages.append(self.dispatch(ids))
                continue
            rname = self.take(kind='name')
            if word == 'shape':
                rtype = self.add('Shape3DType')
            else:
                self.take(':')
                rtype = self.type()
            ident = self.add('Resource', {'name': rname, 'kind': word}, {'type': rtype})
            if self.accept('['):  # a fixed-length buffer: buffer<f32, read> [16]
                self.graph.nodes[ident]['attributes']['length'] = int(self.take(kind='number'))
                self.take(']')
            elif self.accept('in'):
                extents.append((ident, self.take(kind='name')))
                if self.accept('*'):  # elements per element of the extent: `in M * 4`
                    self.graph.nodes[ident]['attributes']['scale'] = int(self.take(kind='number'))
            self.take(';')
            ids[rname] = ident
            resources.append(ident)
        for ident, extent in extents:
            self.graph.nodes[ident]['links']['extent'] = ids[extent]
        return self.add('Pipeline', {'name': name}, {'resources': resources, 'stages': stages}, {'module': module})

    def dispatch(self, ids):
        kernel = self.take(kind='name')
        self.take('over')
        grid = self.take(kind='name')
        self.take('block')
        self.take('(')
        sizes = [int(self.take(kind='number'))]
        while self.accept(','):
            sizes.append(int(self.take(kind='number')))
        self.take(')')
        attrs = {'blockSize': sizes[0], 'completionBarrier': True}
        if len(sizes) == 3:
            attrs.update(blockHeight=sizes[1], blockDepth=sizes[2])
        if self.accept('groups'):
            self.take('(')
            counts = [json.loads(self.take(kind='string'))]
            while self.accept(','):
                counts.append(json.loads(self.take(kind='string')))
            self.take(')')
            attrs.update(groupsX=counts[0], groupsY=counts[1], groupsZ=counts[2])
        self.take('(')
        function, _ = self.functions[kernel]
        params = {self.graph.nodes[p]['attributes']['name']: p for p in self.graph.nodes[function]['children']['parameters']}
        bindings = []
        while not self.accept(')'):
            param = self.take(kind='name')
            self.take('=')
            resource = self.take(kind='name')
            bindings.append(self.add('Binding', links={'parameter': params[param], 'resource': ids[resource]}))
            self.accept(',')
        self.take(';')
        return self.add('Dispatch', attrs, {'bindings': bindings}, {'kernel': function, 'grid': ids[grid]})


def parse_program(text):
    """IR for a program in the qpel text syntax (version 3 spellings)."""
    return Parser(text).program()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=('print', 'parse'))
    parser.add_argument('path', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'print':
        sys.stdout.write(print_program(json.loads(args.path.read_text())))
    else:
        sys.stdout.write(json.dumps(parse_program(args.path.read_text()), indent=1) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
