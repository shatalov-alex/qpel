"""Compile emitted expressions with UBSan; compare to explicit modular arithmetic."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qpel_ir import validate, Lowering
FIXTURE = Path(__file__).parent / 'fixtures/qpel_ir/count'

class NumericTests(unittest.TestCase):
    def setUp(self):
        self.ir = json.loads((FIXTURE / 'program.qpel.json').read_text())
        self.counter = 0

    def add(self, kind, attrs=None, children=None, links=None):
        self.counter += 1
        n = dict(id='numeric'+str(self.counter), kind=kind, attributes=attrs or {}, children=children or {}, links=links or {})
        self.ir['nodes'].append(n)
        return n['id']

    def literal(self, value):
        return self.add('Literal', {'value': value})

    def binary(self, op, left, right):
        return self.add('Binary', {'operator': op}, {'left': [left], 'right': [right]})

    def helper(self, name, typ, expression, result='U32Type'):
        params = [self.add('Parameter', {'name': n}, {'type': [self.add(typ)]}) for n in ('a', 'b')]
        def ref(i):
            return self.add('Name', links={'declaration': params[i]})
        value = expression(ref)
        body = self.add('Block', children={'statements': [self.add('Return', children={'value': [value]})]})
        fn = self.add('Function', {'name': name}, {'parameters': params, 'resultType': [self.add(result)], 'body': [body]})
        module = next(n for n in self.ir['nodes'] if n['id'] == self.ir['roots']['module'])
        module['children']['functions'].insert(0, fn)
        return fn

    def test_old_numeric_contract_rejected(self):
        self.ir['semantics'] = 'qpel-portable-core-v0'
        with self.assertRaisesRegex(ValueError, 'version/semantics'):
            validate(self.ir)

    def test_zero_divisor_rejected(self):
        self.helper('zero_divide', 'U32Type', lambda r:self.binary('/', r(0), self.literal('0u')))
        for backend in ('metal', 'cuda'):
            with self.assertRaisesRegex(ValueError, 'ZERO_DIVISOR'):
                validate(self.ir, backend)

    def test_unsigned_decimal_canonicalization(self):
        self.helper('decimal_value', 'U32Type', lambda r:self.literal('010'))
        text = Lowering(self.ir).emit(next(n for n in self.ir['nodes'] if n['kind']=='GpuModule'))
        self.assertIn('return 10u;', text)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required for numerical execution')
    def test_compiled_modular_arithmetic(self):
        functions = []
        functions.append(self.helper('sub_byte', 'U8Type', lambda r:self.binary('-', r(0), r(1))))
        functions.append(self.helper('sub_greater', 'U8Type', lambda r:self.binary('>', self.binary('-', r(0), r(1)), self.literal('0')), 'BoolType'))
        functions.append(self.helper('neg_byte', 'U8Type', lambda r:self.add('Unary', {'operator':'-'}, {'operand':[r(0)]})))
        functions.append(self.helper('min_mixed', 'U8Type', lambda r:self.add('Minimum', children={'left':[r(0)], 'right':[self.literal('4294967295u')]})))
        for name, op in [('add_word','+'), ('sub_word','-'), ('mul_word','*'), ('div_word','/'), ('rem_word','%'), ('xor_word','^')]:
            functions.append(self.helper(name, 'U32Type', lambda r, op=op:self.binary(op, r(0), r(1))))
        functions.append(self.helper('literal_overflow', 'U32Type', lambda r:self.binary('*', self.literal('2147483647'), self.literal('2'))))
        lower = Lowering(self.ir)
        source = '''#include <cstdint>
#include <algorithm>
#include <cassert>
using std::min; using std::max;
#define QPEL_DEVICE
#define QPEL_U8 uint8_t
#define QPEL_U32 uint32_t
#define QPEL_U32_DIV(a, b) ((a) / (b))
#define QPEL_U32_REM(a, b) ((a) % (b))
'''+''.join(lower.emit(lower.nodes[f]) for f in functions)+'''
int main() {
  for (uint32_t a=0; a<256; ++a) for(uint32_t b=0; b<256; ++b) {
    uint32_t expected=uint32_t(uint64_t(a)+0x100000000ULL-b);
    assert(sub_byte(a,b)==expected);
    assert(sub_greater(a,b)==(expected>0));
    assert(neg_byte(a,b)==uint32_t(0x100000000ULL-a));
    assert(min_mixed(a,b)==a);
  }
  uint32_t values[]={0,1,255,65535,65536,2147483647u,2147483648u,4294967295u};
  for(auto a:values) for(auto b:values) {
    assert(add_word(a,b)==uint32_t(uint64_t(a)+b));
    assert(sub_word(a,b)==uint32_t(uint64_t(a)+0x100000000ULL-b));
    assert(mul_word(a,b)==uint32_t(uint64_t(a)*b));
    if(b) { assert(div_word(a,b)==uint64_t(a)/b); assert(rem_word(a,b)==uint64_t(a)%b); }
    assert(xor_word(a,b)==(a^b));
  }
  assert(literal_overflow(0,0)==4294967294u);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp, binary = Path(folder)/'numeric.cpp', Path(folder)/'numeric'
            cpp.write_text(source)
            subprocess.run(['clang++','-std=c++17','-O1','-fsanitize=undefined','-fno-sanitize-recover=all',str(cpp),'-o',str(binary)], check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=30)

if __name__ == '__main__':
    unittest.main(verbosity=2)
