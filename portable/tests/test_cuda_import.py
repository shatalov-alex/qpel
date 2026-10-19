import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cuda_import import parse,UnsupportedCUDA
SOURCE=(Path(__file__).resolve().parents[1]/'cuda_examples/threshold.cu').read_text()

class ImportTests(unittest.TestCase):
    def test_threshold(self):
        result=parse(SOURCE)
        self.assertEqual(result['value'],{'condition':{'op':'>=','left':{'input':'intensity'},'right':{'input':'cutoff'}},'yes':{'literal':1},'no':{'literal':0}})
        self.assertEqual([p['type'] for p in result['parameters']],['ReadOnlyU8BufferType','ReadOnlyU8BufferType','Shape3DType','U32BufferType'])
    def test_renaming(self):
        result=parse(SOURCE.replace('intensity','pixels').replace('cutoff','limits').replace('dimensions','extent').replace('binary','result').replace('apply_threshold','segment'))
        self.assertEqual(result['kernel'],'segment');self.assertEqual(result['shape'],'extent')
        self.assertEqual(result['value']['condition']['left'],{'input':'pixels'})
    def test_expression_changes_are_preserved(self):
        for op in ['>','<','<=','==','!=']:
            result=parse(SOURCE.replace('intensity[i] >= cutoff[i] ? 1u : 0u',f'intensity[i] {op} cutoff[i] ? 7u : 3u'))
            self.assertEqual(result['value']['condition']['op'],op)
            self.assertEqual(result['value']['yes'],{'literal':7});self.assertEqual(result['value']['no'],{'literal':3})
    def test_copy(self):
        self.assertEqual(parse(SOURCE.replace('intensity[i] >= cutoff[i] ? 1u : 0u','intensity[i]'))['value'],{'input':'intensity'})
    def test_comments_and_parentheses(self):
        s=SOURCE.replace('return;','{ /* early exit */ return; }').replace('dimensions.x * dimensions.y * dimensions.z','(dimensions.x * (dimensions.y * dimensions.z))')
        self.assertEqual(parse(s)['value'],parse(SOURCE)['value'])
    def test_bitwise_precedence(self):
        source=SOURCE.replace('intensity[i] >= cutoff[i] ? 1u : 0u','intensity[i] | cutoff[i] & 170u ^ 85u | 2147483648u')
        value=parse(source)['value']
        self.assertEqual(value['op'],'|')
        self.assertEqual(value['right'],{'literal':2147483648})
        self.assertEqual(value['left']['op'],'|')
        self.assertEqual(value['left']['right']['op'],'^')
        self.assertEqual(value['left']['right']['left']['op'],'&')
    def test_bitwise_in_comparison(self):
        source=SOURCE.replace('intensity[i] >= cutoff[i]','(intensity[i] & 170u) == (cutoff[i] ^ 85u)')
        value=parse(source)['value']['condition']
        self.assertEqual(value['op'],'==')
        self.assertEqual(value['left']['op'],'&')
        self.assertEqual(value['right']['op'],'^')
    def test_unsafe_or_unsupported_bitwise_rejected(self):
        for expr in ['intensity[i] << 24u','intensity[i] >> 1u','~intensity[i]',
                     'intensity[i] && cutoff[i]','intensity[i] || cutoff[i]',
                     'intensity[i] & (cutoff[i] == 0u)']:
            with self.subTest(expr=expr),self.assertRaises(UnsupportedCUDA):
                parse(SOURCE.replace('intensity[i] >= cutoff[i] ? 1u : 0u',expr))
    def test_scalar_count_normalization(self):
        source=SOURCE.replace('uint3 dimensions','unsigned int count').replace('dimensions.x * dimensions.y * dimensions.z','count')
        ir=parse(source)
        self.assertEqual(ir['size_kind'],'count');self.assertEqual(ir['shape'],'count')
        self.assertEqual(ir['parameters'][2],{'name':'count','type':'Shape3DType','source_type':'unsigned int'})
        self.assertEqual(ir['guard'],parse(SOURCE)['guard'])
    def test_scalar_count_rejections(self):
        base=SOURCE.replace('uint3 dimensions','unsigned int count').replace('dimensions.x * dimensions.y * dimensions.z','count')
        for source in [base.replace('i >= count','i > count'),base.replace('i >= count','i >= count.x'),
                       base.replace('1u : 0u','count : 0u'),base.replace('unsigned int count','unsigned int count, unsigned int extra'),
                       base.replace('unsigned int count','unsigned int count, uint3 extent')]:
            with self.subTest(source=source),self.assertRaises(UnsupportedCUDA):parse(source)
    def test_rejections(self):
        mutations=[
            ('shared memory',SOURCE.replace('binary[i] =','__shared__ unsigned int cache[64]; binary[i] =')),
            ('barrier',SOURCE.replace('binary[i] =','__syncthreads(); binary[i] =')),
            ('call',SOURCE.replace('1u : 0u','abs(1) : 0u')),
            ('loop',SOURCE.replace('binary[i] =','while (true) {} binary[i] =')),
            ('macro','#define THRESHOLD 1\n'+SOURCE),
            ('other include',SOURCE.replace('<cuda_runtime.h>','<other.h>')),
            ('float',SOURCE.replace('const unsigned char* intensity','const float* intensity')),
            ('signed output',SOURCE.replace('unsigned int* binary','int* binary')),
            ('missing guard',SOURCE.replace('if (i >= dimensions.x * dimensions.y * dimensions.z) return;','')),
            ('off-by-one guard',SOURCE.replace('i >= dimensions','i > dimensions')),
            ('wrong volume',SOURCE.replace('dimensions.x * dimensions.y * dimensions.z','dimensions.x * dimensions.x * dimensions.z')),
            ('2d grid',SOURCE.replace('blockIdx.x','blockIdx.y')),
            ('extra kernel',SOURCE+SOURCE),
            ('trailing syntax',SOURCE+';'),
            ('read output',SOURCE.replace('intensity[i] >=','binary[i] >=')),
            ('write input',SOURCE.replace('binary[i] =','intensity[i] =')),
            ('index offset',SOURCE.replace('intensity[i]','intensity[i+1]')),
            ('overflow literal',SOURCE.replace('1u :','4294967296u :')),
            ('octal literal',SOURCE.replace('1u :','077u :')),
            ('negative literal',SOURCE.replace('1u :','-1 :')),
            ('unsupported arithmetic',SOURCE.replace('1u :','(2*3) :')),
            ('duplicate name',SOURCE.replace('cutoff','intensity')),
            ('unknown scalar',SOURCE.replace('1u :','unknown :')),
            ('missing end',SOURCE[:-2]),
        ]
        for label,source in mutations:
            with self.subTest(label=label),self.assertRaisesRegex(UnsupportedCUDA,r'\d+:\d+:'):parse(source)

if __name__=='__main__':unittest.main(verbosity=2)
