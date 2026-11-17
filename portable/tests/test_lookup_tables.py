"""Module-level constants and qpel.clz (IR version 12): lookup tables a module declares once and every
function reads (CUDA `__device__ const T t[N]`, Metal `constant T t[N]`), as bitsandbytes dequantises
NF4 and FP4 codes, and the leading zero bits of an integer (`__clz`, Metal `clz`). examples/lookup_tables.qpel
runs on the CPU simulator and the Apple GPU against an oracle; the CUDA importer reads program-scope
tables that nothing writes as module constants, `__clz` as qpel.clz, and `switch` statements as qpel
has none (the case taken on a constant, an if-else chain otherwise)."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

TEXT = (HERE/'examples/lookup_tables.qpel').read_text()
BODY = TEXT[TEXT.index('module'):]
NF4 = [-1.0, -0.6961928009986877, -0.5250730514526367, -0.39491748809814453, -0.28444138169288635, -0.18477343022823334,
       -0.09105003625154495, 0.0, 0.07958029955625534, 0.16093020141124725, 0.24611230194568634, 0.33791524171829224,
       0.44070982933044434, 0.5626170039176941, 0.7229568362236023, 1.0]


def oracle(case):
    import numpy as np
    table = np.array(NF4, dtype=np.float32)
    codes = case.inputs['codes'].astype(np.int64)
    blocksize = case.values['blocksize']
    shift = 31 - (32 - int(blocksize).bit_length())
    i = np.arange(codes.size)
    scale = case.inputs['absmax'][(i * 2) >> shift]
    out = np.empty(codes.size * 2, np.float32)
    out[0::2] = table[codes >> 4] * scale
    out[1::2] = table[codes & 15] * scale
    return out


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(12)
    out = []
    for n, blocksize in ((1, 64), (100, 64), (1000, 256), (4096, 4096)):
        codes = rng.integers(0, 256, n, dtype=np.uint8)
        absmax = rng.uniform(0.1, 3, n).astype(np.float32)  # (more than the blocks: indexed by i * 2 >> log2(bs))
        out.append(Case({'n': (n, 1, 1)}, {'codes': codes, 'absmax': absmax}, {'blocksize': blocksize},
                        f'n={n} blocksize={blocksize}'))
    return out


class Rules(unittest.TestCase):
    def test_version_12_and_text(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 12)
        self.assertEqual(print_program(program), BODY)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=11, semantics='qpel-portable-core-v11')
        with self.assertRaisesRegex(ValueError, 'IR version 11 cannot use: module constant, qpel.clz'):
            validate(program)

    def reject(self, old, new, code):
        self.assertIn(old, TEXT)
        with self.assertRaisesRegex(ValueError, code):
            validate(parse_program(TEXT.replace(old, new)))

    def test_constants_are_read_only(self):
        self.reject('    let byte: u32 = cast<u32>(codes[qpel.index]);',
                    '    nf4[0u] = 0.0f;\n    let byte: u32 = cast<u32>(codes[qpel.index]);', 'READ_ONLY')
        self.reject('    let byte: u32 = cast<u32>(codes[qpel.index]);',
                    '    shift_bias = 1;\n    let byte: u32 = cast<u32>(codes[qpel.index]);', 'READ_ONLY')

    def test_constants_are_literals_one_per_element(self):
        self.reject('-1.0f, -0.6961928009986877f,', '-1.0f,', 'CONSTANT_VALUE')
        self.reject('const shift_bias: i32 = 31;', 'const shift_bias: i32 = 31 + 0;', 'CONSTANT_VALUE')

    def test_clz_takes_integers(self):
        self.reject('qpel.clz(blocksize)', 'qpel.clz(cast<f32>(blocksize))', 'UNARY')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-tables-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def check(self, got, case):
        import numpy as np
        np.testing.assert_array_equal(np.asarray(got['out']).view(np.uint32), oracle(case).view(np.uint32), err_msg=case.label)

    def test_spellings(self):
        cuda = (self.generated/'lookup_tables.cu').read_text()
        metal = (self.generated/'lookup_tables.metal').read_text()
        self.assertIn('QPEL_CONSTANT_ARRAY(QPEL_F32, nf4, 16) = {(-1.0f),', cuda)
        self.assertIn('QPEL_CLZ_I32(blocksize)', cuda)
        self.assertIn('#define QPEL_CONSTANT_ARRAY(T, name, length) constant T name[length]', metal)

    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'lookup_tables.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases():
            self.check(program.run(case), case)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in cases():
            self.check(metal.run_generated(host, self.plan, case), case)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Importer(unittest.TestCase):
    SOURCE = '''
__device__ static float lut[4] = {-1.0f, 0, 0.5f, 2};
__device__ static float scratch[4] = {0, 0, 0, 0};  // written below: not a constant
template <int MODE>
__global__ void decode(const unsigned char* codes, float* out, int blocksize, int n) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i >= n) return;
  int shift = 31 - __clz(blocksize);
  float v = 0.0f;
  switch (MODE) {
  case 0: v = lut[codes[i] & 3]; break;
  case 1:
  case 2: v = (float)(i >> shift); break;
  default: v = -1.0f;
  }
  switch (codes[i] & 7) {
  case 1: v += 1.0f; break;
  case 2: case 3: v *= 2.0f; break;
  case 4: out[i] = 0.0f; return;
  default: v -= 1.0f;
  }
  out[i] = v;
}
__global__ void touch(float x) { scratch[0] = x; }
template __global__ void decode<0>(const unsigned char*, float*, int, int);
template __global__ void decode<1>(const unsigned char*, float*, int, int);
'''

    def test_tables_clz_and_switches(self):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text(self.SOURCE)
            program, report = import_source(path, kernels=['decode_0', 'decode_1'])
        validate(program)
        self.assertEqual(program['version'], 12)
        text = print_program(program)
        self.assertIn('const lut: array<f32, 4> = [-1.0f, 0.0f, 0.5f, 2.0f];', text)
        self.assertNotIn('scratch', text)
        self.assertIn('qpel.clz(blocksize)', text)
        self.assertIn('switch_value_', text)  # the switch on a variable
        decode_1 = text[text.index('kernel decode_1'):]
        self.assertNotIn('lut[', decode_1)  # the constant switch keeps only its case

    def test_switches_against_the_original(self):
        """Both kernels against their original on the CPU simulator, every code."""
        import numpy as np
        from frontends.c_import import import_source
        from qpel import compile_program
        from differential.simulate import Program, Case
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            path = work/'k.cu'
            path.write_text(self.SOURCE)
            for kernel in ('decode_0', 'decode_1'):
                program, report = import_source(path, kernels=[kernel], module=kernel)
                (work/kernel).mkdir()
                (work/kernel/'p.json').write_text(json.dumps(program))
                compile_program(work/kernel/'p.json', work/kernel/'build')
                generated = work/kernel/'build/package/generated'
                plan = json.loads((generated/'pipeline.json').read_text())
                (work/kernel/'o').mkdir()
                (work/kernel/'q').mkdir()
                original = Program(path, plan, work/kernel/'o', original=report)
                ours = Program(generated/(kernel + '.cu'), plan, work/kernel/'q')
                codes = np.arange(256, dtype=np.uint8)
                shapes = {r['name']: (256, 1, 1) for r in plan['resources'] if r['kind'] == 'shape'}
                given = {'blocksize': 64, 'n': 256}
                values = {r['name']: given[r['name']] for r in plan['resources'] if r['kind'] == 'value'}
                case = Case(shapes, {'codes': codes}, values, kernel)
                want, got = original.run(case), ours.run(case)
                np.testing.assert_array_equal(got['out'].view(np.uint32), want['out'].view(np.uint32), err_msg=kernel)


if __name__ == '__main__':
    unittest.main()
