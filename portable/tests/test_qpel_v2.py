"""IR version 2: else, for/break/continue, signed 32-bit integers, shifts and ~.

The compiled test runs emitted helpers under UBSan against exact Python
references, including every case C leaves undefined for int.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate, Lowering, minimum_version

HERE = Path(__file__).resolve().parents[1]
FIXTURE = HERE/'tests/fixtures/qpel_ir/count'
INT_MIN, INT_MAX = -2**31, 2**31 - 1


def wrap(x):
    x &= 2**32 - 1
    return x - 2**32 if x >= 2**31 else x


def c_div(a, b):
    q = abs(a)//abs(b)
    return wrap(q if (a < 0) == (b < 0) else -q)


class Builder:
    """Adds helper functions to the count fixture's module."""

    def __init__(self, version=2):
        self.ir = json.loads(FIXTURE.joinpath('program.qpel.json').read_text())
        self.ir['version'], self.ir['semantics'] = version, f'qpel-portable-core-v{version}'
        self.count = 0
        self.module = next(n for n in self.ir['nodes'] if n['id'] == self.ir['roots']['module'])

    def add(self, kind, attrs=None, **children):
        links = children.pop('links', {})
        self.count += 1
        n = {'id': f'v{self.count}', 'kind': kind, 'attributes': attrs or {},
             'children': {r: v if isinstance(v, list) else [v] for r, v in children.items()}, 'links': links}
        self.ir['nodes'].append(n)
        return n['id']

    def lit(self, v): return self.add('Literal', {'value': v})
    def i32(self, v): return self.add('I32Literal', {'value': v})
    def bin(self, op, l, r): return self.add('Binary', {'operator': op}, left=l, right=r)
    def name(self, decl): return self.add('Name', links={'declaration': decl})
    def block(self, *statements): return self.add('Block', statements=list(statements))
    def ret(self, value=None): return self.add('Return', **({'value': value} if value else {}))

    def helper(self, name, types, body, result):
        """body(params) -> list of statements."""
        params = [self.add('Parameter', {'name': n}, type=self.add(t)) for n, t in zip('abcd', types)]
        fn = self.add('Function', {'name': name}, parameters=params, resultType=self.add(result),
                      body=self.block(*body(params)))
        self.module['children']['functions'].insert(len(self.module['children']['functions']) - 1, fn)
        return fn

    def binary_helper(self, name, op, typ='I32Type', result=None):
        return self.helper(name, [typ, typ], lambda p: [self.ret(self.bin(op, self.name(p[0]), self.name(p[1])))],
                           result or typ)


class Checks(unittest.TestCase):
    def test_version_1_cannot_use_version_2(self):
        b = Builder(version=1)
        b.helper('f', ['I32Type'], lambda p: [b.ret(b.name(p[0]))], 'I32Type')
        with self.assertRaisesRegex(ValueError, 'declare version 2'):
            validate(b.ir)
        self.assertEqual(minimum_version(b.ir), 2)
        b.ir['version'], b.ir['semantics'] = 2, 'qpel-portable-core-v2'
        validate(b.ir)

    def test_fixtures_declare_their_minimum_version(self):
        for folder in (HERE/'tests/fixtures/qpel_ir').iterdir():
            program = json.loads((folder/'program.qpel.json').read_text())
            self.assertEqual(program['version'], minimum_version(program), folder.name)

    def test_type_errors(self):
        cases = {
            'BINARY': lambda b: b.helper('f', ['I32Type', 'U32Type'], lambda p: [b.ret(b.bin('+', b.name(p[0]), b.name(p[1])))], 'I32Type'),
            'LOOP_CONTEXT': lambda b: b.helper('f', ['U32Type'], lambda p: [b.add('Break'), b.ret(b.name(p[0]))], 'U32Type'),
            'ZERO_DIVISOR': lambda b: b.helper('f', ['I32Type'], lambda p: [b.ret(b.bin('/', b.name(p[0]), b.i32('0')))], 'I32Type'),
            'MISSING_RETURN': lambda b: b.helper('f', ['BoolType'], lambda p: [
                b.add('If', condition=b.name(p[0]), thenBody=b.block(b.ret(b.lit('1'))))], 'U32Type'),
            'CAST_TYPE': lambda b: b.helper('f', ['F32Type'], lambda p: [b.ret(b.add('I32Cast', operand=b.name(p[0])))], 'I32Type'),
        }
        for code, build in cases.items():
            with self.subTest(code=code):
                b = Builder()
                build(b)
                with self.assertRaisesRegex(ValueError, code):
                    validate(b.ir)

    def test_for_variable_is_scoped_to_the_loop(self):
        b = Builder()
        holder = {}

        def body(p):
            k = b.add('Variable', {'name': 'k'}, type=b.add('U32Type'), value=b.lit('0'))
            holder['k'] = k
            loop = b.add('For', init=k, condition=b.bin('<', b.name(k), b.name(p[0])), body=b.block())
            return [loop, b.ret(b.name(k))]
        b.helper('f', ['U32Type'], body, 'U32Type')
        with self.assertRaisesRegex(ValueError, 'SCOPE'):
            validate(b.ir)

    def test_if_else_counts_as_returning(self):
        b = Builder()
        b.helper('f', ['BoolType'], lambda p: [b.add('If', condition=b.name(p[0]), thenBody=b.block(b.ret(b.lit('1'))),
                                                     elseBody=b.block(b.ret(b.lit('2'))))], 'U32Type')
        validate(b.ir)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Compiled(unittest.TestCase):
    def test_signed_shift_and_loop_semantics(self):
        b = Builder()
        for name, op in [('add', '+'), ('sub', '-'), ('mul', '*'), ('div', '/'), ('rem', '%'),
                         ('band', '&'), ('bxor', '^'), ('lt', '<')]:
            b.binary_helper('i_' + name, op, result='BoolType' if op == '<' else None)
        b.binary_helper('i_shl', '<<')
        b.binary_helper('i_shr', '>>')
        b.binary_helper('u_shl', '<<', 'U32Type')
        b.binary_helper('u_shr', '>>', 'U32Type')
        b.helper('i_neg', ['I32Type'], lambda p: [b.ret(b.add('Unary', {'operator': '-'}, operand=b.name(p[0])))], 'I32Type')
        b.helper('i_not', ['I32Type'], lambda p: [b.ret(b.add('Unary', {'operator': '~'}, operand=b.name(p[0])))], 'I32Type')
        b.helper('u_not', ['U32Type'], lambda p: [b.ret(b.add('Unary', {'operator': '~'}, operand=b.name(p[0])))], 'U32Type')
        b.helper('i_min', ['I32Type', 'I32Type'], lambda p: [b.ret(b.add('Minimum', left=b.name(p[0]), right=b.name(p[1])))], 'I32Type')
        b.helper('to_i32', ['U32Type'], lambda p: [b.ret(b.add('I32Cast', operand=b.name(p[0])))], 'I32Type')
        b.helper('to_u32', ['I32Type'], lambda p: [b.ret(b.add('U32Cast', operand=b.name(p[0])))], 'U32Type')

        def count_bits(p):
            # count = 0; for (k = 0; k < 32; k = k + 1) { if (!((v >> k) & 1)) continue; count += 1; if (count == limit) break; }
            count = b.add('Variable', {'name': 'count'}, type=b.add('U32Type'), value=b.lit('0'))
            k = b.add('Variable', {'name': 'k'}, type=b.add('U32Type'), value=b.lit('0'))
            bit = b.bin('&', b.bin('>>', b.name(p[0]), b.name(k)), b.lit('1'))
            body = b.block(
                b.add('If', condition=b.add('Unary', {'operator': '!'}, operand=bit), thenBody=b.block(b.add('Continue'))),
                b.add('Assign', target=b.name(count), value=b.bin('+', b.name(count), b.lit('1'))),
                b.add('If', condition=b.bin('==', b.name(count), b.name(p[1])), thenBody=b.block(b.add('Break')),
                      elseBody=b.block()))
            step = b.add('Assign', target=b.name(k), value=b.bin('+', b.name(k), b.lit('1')))
            loop = b.add('For', init=k, condition=b.bin('<', b.name(k), b.lit('32')), step=step, body=body)
            return [count, loop, b.ret(b.name(count))]
        b.helper('count_bits', ['U32Type', 'U32Type'], count_bits, 'U32Type')
        validate(b.ir)
        lower = Lowering(b.ir)
        module = lower.emit(lower.nodes[b.ir['roots']['module']])
        helpers = module[module.index('QPEL_DEVICE'):module.index('QPEL_KERNEL')]
        support = (HERE/'backends/cuda.h').read_text()
        support = support[support.index('#ifdef QPEL_I32_ENABLED'):]
        values = [0, 1, -1, 2, -2, 7, -7, 1000, INT_MAX, INT_MIN, INT_MIN + 1, 123456789, -987654321]
        checks = []
        for a in values:
            checks.append(f'check(i_neg({a}) == {wrap(-a)});')
            checks.append(f'check(i_not({a}) == {~a});')
            checks.append(f'check(to_u32({a}) == {a & 0xffffffff}u);')
            for s in range(0, 40, 3):
                checks.append(f'check(i_shl({a}, {s}) == {wrap(a << (s & 31))});')
                checks.append(f'check(i_shr({a}, {s}) == {a >> (s & 31)});')
            for c in values:
                checks.append(f'check(i_add({a}, {c}) == {wrap(a + c)});')
                checks.append(f'check(i_sub({a}, {c}) == {wrap(a - c)});')
                checks.append(f'check(i_mul({a}, {c}) == {wrap(a * c)});')
                checks.append(f'check(i_band({a}, {c}) == {a & c});')
                checks.append(f'check(i_bxor({a}, {c}) == {a ^ c});')
                checks.append(f'check(i_lt({a}, {c}) == {str(a < c).lower()});')
                checks.append(f'check(i_min({a}, {c}) == {min(a, c)});')
                if c:
                    q = c_div(a, c)
                    checks.append(f'check(i_div({a}, {c}) == {q});')
                    checks.append(f'check(i_rem({a}, {c}) == {wrap(a - wrap(q * c))});')
        for u in [0, 1, 0x7fffffff, 0x80000000, 0xffffffff, 0x12345678]:
            checks.append(f'check(to_i32({u}u) == {wrap(u)});')
            checks.append(f'check(u_not({u}u) == {~u & 0xffffffff}u);')
            for s in range(0, 40, 3):
                checks.append(f'check(u_shl({u}u, {s}u) == {(u << (s & 31)) & 0xffffffff}u);')
                checks.append(f'check(u_shr({u}u, {s}u) == {u >> (s & 31)}u);')
            for limit in (0, 1, 3, 40):
                expected = bin(u).count('1') if limit == 0 or limit > bin(u).count('1') else limit
                checks.append(f'check(count_bits({u}u, {limit}u) == {expected}u);')
        source = ('#include <cstdint>\n#include <algorithm>\n#include <cstdio>\n#include <cstdlib>\n'
                  'using std::min; using std::max;\n#define __device__\n#define __forceinline__ inline\n'
                  '#define QPEL_I32_ENABLED 1\n#define QPEL_DEVICE\n#define QPEL_U8 uint8_t\n#define QPEL_U32 uint32_t\n'
                  + support + helpers +
                  '\nstatic int failures = 0;\nstatic void check(bool ok) { if (!ok) ++failures; }\n'
                  'int main() {\n' + '\n'.join('  ' + c.replace(str(INT_MIN), f'({INT_MIN + 1} - 1)') for c in checks)
                  + '\n  if (failures) { std::printf("%d failures\\n", failures); return 1; }\n}\n')
        with tempfile.TemporaryDirectory() as folder:
            cpp, binary = Path(folder)/'v2.cpp', Path(folder)/'v2'
            cpp.write_text(source)
            build = subprocess.run(['clang++', '-std=c++17', '-O1', '-fsanitize=undefined', '-fno-sanitize-recover=all',
                                    str(cpp), '-o', str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stderr[-3000:])
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr[-3000:])


if __name__ == '__main__':
    unittest.main(verbosity=2)
