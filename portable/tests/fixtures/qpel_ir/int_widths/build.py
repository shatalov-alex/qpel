"""Write program.qpel.json for the int_widths fixture (IR version 3).

One Grid1D kernel reads buffers of i8, u16, i16, i64, u64, f32 and bf16 and an i64
argument, and writes i64, u64, i32, u8, i16, bf16 and f32 results. It covers mixed-width
integer arithmetic, 64-bit shifts and division (with LLONG_MIN / -1), saturating
float -> integer conversion, narrowing, negative constants and bf16 rounding.

    python3 tests/fixtures/qpel_ir/int_widths/build.py
"""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))
from frontends.c_import import Graph  # noqa: E402

g = Graph()
add = g.add


def scalar(t):
    return add('ScalarType', {'scalar': t})


def buffer(t, access):
    return add('BufferType', {'element': t, 'access': access})


params, declared = [], {}


def param(name, typ):
    ident = add('Parameter', {'name': name, 'binding': len(params), 'isBuffer': True}, {'type': typ})
    params.append(ident)
    declared[name] = ident
    return ident


for name, t in [('a', 'i8'), ('b', 'u16'), ('c', 'i16'), ('d', 'i64'), ('e', 'u64'), ('f', 'f32'), ('h', 'bf16')]:
    param(name, buffer(t, 'read'))
param('k', scalar('i64'))
for name, t in [('o_i64', 'i64'), ('o_u64', 'u64'), ('o_i32', 'i32'), ('o_u8', 'u8'), ('o_i16', 'i16'),
                ('o_bf16', 'bf16'), ('o_f32', 'f32')]:
    param(name, buffer(t, 'write'))
param('count', add('Shape3DType'))


def name(n):
    return add('Name', links={'declaration': declared[n]})


def at(n):
    return add('Index', children={'base': name(n), 'index': add('GridIndex')})


def binary(op, left, right):
    return add('Binary', {'operator': op}, {'left': left, 'right': right})


def cast(to, value):
    return add('Cast', {'to': to}, {'operand': value})


def const(t, value):
    return add('Constant', {'scalar': t, 'value': str(value)})


def local(n, t, value):
    ident = add('Variable', {'name': n}, {'type': scalar(t), 'value': value})
    declared[n] = ident
    return ident


def store(n, value):
    return add('Assign', children={'target': at(n), 'value': value})


member = lambda m: add('Member', {'member': m}, {'base': name('count')})
statements = [
    local('n', 'u32', binary('*', binary('*', member('x'), member('y')), member('z'))),
    add('If', children={'condition': binary('>=', add('GridIndex'), name('n')),
                        'thenBody': add('Block', children={'statements': [add('Return')]})}),
    # t = d >> b (count masked to 0..63); m = min(t, -5)
    local('t', 'i64', binary('>>', at('d'), at('b'))),
    local('m', 'i64', add('Minimum', children={'left': name('t'), 'right': const('i64', -5)})),
    # (d * k + a) / (c | 1) + m: i8 and i16 widen to i64; c | 1 is never zero
    store('o_i64', binary('+', binary('/', binary('+', binary('*', at('d'), name('k')), at('a')),
                                      binary('|', at('c'), add('I32Literal', {'value': '1'}))), name('m'))),
    # ((e << b) ^ (e >> 7)) - b, modulo 2^64
    store('o_u64', binary('-', binary('^', binary('<<', at('e'), at('b')), binary('>>', at('e'), add('Literal', {'value': '7u'}))),
                          at('b'))),
    # max(saturate_i32(f), (i32)d): float -> int saturates, int -> int wraps
    store('o_i32', add('Maximum', children={'left': cast('i32', at('f')), 'right': cast('i32', at('d'))})),
    store('o_u8', cast('u8', binary('*', at('f'), add('F32Literal', {'value': '4'})))),
    # (i16)(-c - (-32768)): negation in i32, then wrapping narrowing
    store('o_i16', cast('i16', binary('-', add('Unary', {'operator': '-'}, {'operand': at('c')}), const('i16', -32768)))),
    store('o_bf16', cast('bf16', binary('*', cast('f32', at('h')), at('f')))),
    store('o_f32', binary('+', cast('f32', at('d')), cast('f32', at('e')))),
]
body = add('Block', children={'statements': statements})
kernel = add('Function', {'name': 'widths', 'isKernel': True},
             {'parameters': params, 'resultType': scalar('void'), 'body': body, 'domain': add('Grid1D')})
module = add('GpuModule', {'name': 'int_widths'}, {'functions': [kernel]})

resources, ids = [], {}
shape = add('Resource', {'name': 'count', 'kind': 'shape'}, {'type': add('Shape3DType')})
ids['count'] = shape
for n, kind, typ in ([(n, 'input', ('buffer', t, 'read')) for n, t in [('a', 'i8'), ('b', 'u16'), ('c', 'i16'), ('d', 'i64'), ('e', 'u64'), ('f', 'f32'), ('h', 'bf16')]]
                     + [('k', 'value', ('scalar', 'i64'))]
                     + [(n, 'output', ('buffer', t, 'write')) for n, t in [('o_i64', 'i64'), ('o_u64', 'u64'), ('o_i32', 'i32'), ('o_u8', 'u8'), ('o_i16', 'i16'), ('o_bf16', 'bf16'), ('o_f32', 'f32')]]):
    typ = scalar(typ[1]) if typ[0] == 'scalar' else buffer(typ[1], typ[2])
    ids[n] = add('Resource', {'name': n, 'kind': kind}, {'type': typ}, {'extent': shape} if kind != 'value' else {})
order = ['a', 'b', 'c', 'd', 'e', 'f', 'h', 'k', 'o_i64', 'o_u64', 'o_i32', 'o_u8', 'o_i16', 'o_bf16', 'o_f32', 'count']
bindings = [add('Binding', links={'parameter': p, 'resource': ids[r]}) for p, r in zip(params, order)]
stage = add('Dispatch', {'blockSize': 64, 'completionBarrier': True}, {'bindings': bindings}, {'kernel': kernel, 'grid': shape})
pipeline = add('Pipeline', {'name': 'int_widths_launch'}, {'resources': [shape] + [ids[r] for r in order if r != 'count'],
                                                              'stages': [stage]}, {'module': module})
program = g.program(module, pipeline)
(HERE/'program.qpel.json').write_text(json.dumps(program, indent=1) + '\n')
print('wrote', HERE/'program.qpel.json', 'version', program['version'])
