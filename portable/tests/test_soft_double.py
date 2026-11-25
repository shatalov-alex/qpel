"""Exact software doubles (frontends/soft_double.py, NUMERICS.md).

The library's functions, run by a qpel kernel, give numpy's double results bit for bit (any NaN for a NaN) on
random bit patterns, doubles close in exponent (cancellation), promoted floats, subnormals, specials and doubles
near overflow: on the CPU simulator and the Apple GPU. A CUDA kernel computing in double (gsplat's
`v * 0.5 / (c + 1e-6)`, negation, a division of an int, comparisons, double constants converted to float) is
imported with them and gives the original's results bit for bit (the original computes in hardware doubles on the
CPU simulator). A double kept in a local, a double math function and a double converted to an integer are
reported."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_test_support import unavailable  # noqa: E402

KERNEL = '''
  kernel run(a: buffer<u64, read> @0, b: buffer<u64, read> @1, f: buffer<f32, read> @2, i: buffer<i32, read> @3, add: buffer<u64, write> @4, sub: buffer<u64, write> @5, mul: buffer<u64, write> @6, div: buffer<u64, write> @7, wide: buffer<u64, write> @8, narrow: buffer<f32, write> @9, whole: buffer<u64, write> @10, whole_u: buffer<u64, write> @11, order: buffer<u32, write> @12, n: shape3 @13) grid1d {
    let k: u32 = qpel.index;
    if (k >= n.x) {
      return;
    }
    add[k] = double_add(a[k], b[k]);
    sub[k] = double_sub(a[k], b[k]);
    mul[k] = double_mul(a[k], b[k]);
    div[k] = double_div(a[k], b[k]);
    wide[k] = double_from_f32(f[k]);
    narrow[k] = double_to_f32(a[k]);
    whole[k] = double_from_i32(i[k]);
    whole_u[k] = double_from_u32(cast<u32>(i[k]));
    order[k] = (double_less(a[k], b[k]) + (2u * double_less_equal(a[k], b[k]))) + (4u * double_equal(a[k], b[k]));
  }
'''
OUTPUTS = ('add', 'sub', 'mul', 'div', 'wide', 'narrow', 'whole', 'whole_u', 'order')


def library_program():
    from frontends.soft_double import LIBRARY
    from qpel_text import parse_program
    buffers = {'a': 'u64', 'b': 'u64', 'f': 'f32', 'i': 'i32'}
    lines = ['pipeline soft_double_launch {', '  shape n;']
    lines += [f'  input {name}: buffer<{t}, read> in n;' for name, t in buffers.items()]
    lines += [f'  output {name}: buffer<{"f32" if name == "narrow" else "u32" if name == "order" else "u64"}, write> in n;'
              for name in OUTPUTS]
    names = list(buffers) + list(OUTPUTS) + ['n']
    lines += ['  dispatch run over n block (64) (' + ', '.join(f'{x} = {x}' for x in names) + ');', '}']
    return parse_program('module soft_double {\n' + LIBRARY + KERNEL + '}\n\n' + '\n'.join(lines) + '\n')


def operands(rng, n):
    import numpy as np
    kinds = rng.integers(0, 6, n)
    sign = rng.integers(0, 2, n, dtype=np.uint64) << np.uint64(63)
    bits = rng.integers(0, 2**63, n, dtype=np.uint64) | sign
    near = (rng.standard_normal(n) * 2.0 ** rng.integers(-30, 30, n)).view(np.uint64)
    floats = rng.standard_normal(n).astype(np.float32).astype(np.float64).view(np.uint64)
    subnormal = rng.integers(0, 2**52, n, dtype=np.uint64) | sign
    special = np.array([0, 2**63, 0x7FF0000000000000, 0xFFF0000000000000, 0x7FF8000000000000, 1, 0x000FFFFFFFFFFFFF,
                        0x0010000000000000, 0x7FEFFFFFFFFFFFFF, 0x3FF0000000000000, 0xBFF0000000000000,
                        0x36A0000000000000, 0x3810000000000000, 0x47EFFFFFE0000000, 0x47EFFFFFF0000000],
                       np.uint64)[rng.integers(0, 15, n)]
    large = (rng.integers(0x7C0, 0x7FF, n, dtype=np.uint64) << np.uint64(52)) | rng.integers(0, 2**52, n, dtype=np.uint64)
    return np.choose(kinds, [bits, near, floats, subnormal, special, large])


def library_cases(seeds=3, n=8192):
    import numpy as np
    from differential.simulate import Case
    out = []
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        a, b = operands(rng, n), operands(rng, n)
        b[: n // 8] = a[: n // 8]  # equal operands, for the comparisons
        f = np.concatenate([rng.standard_normal(n - 64).astype(np.float32),
                            np.array([0, -0.0, np.inf, -np.inf, np.nan, 1e-45, -1e-45, 1.1754942e-38, 3.4028235e38] * 7
                                     + [1.0], np.float32)])
        i = rng.integers(-2**31, 2**31, n, dtype=np.int64).astype(np.int32)
        i[:4] = [0, -2**31, 2**31 - 1, -1]
        out.append(Case({'n': (n, 1, 1)}, {'a': a, 'b': b, 'f': f, 'i': i}, {}, f'seed {seed}'))
    return out


def expected(case):
    import numpy as np
    a, b, f, i = (case.inputs[k] for k in 'abfi')
    with np.errstate(all='ignore'):
        da, db = a.view(np.float64), b.view(np.float64)
        return {'add': da + db, 'sub': da - db, 'mul': da * db, 'div': da / db, 'wide': f.astype(np.float64),
                'narrow': da.astype(np.float32), 'whole': i.astype(np.float64),
                'whole_u': i.view(np.uint32).astype(np.float64),
                'order': ((da < db) + 2 * (da <= db) + 4 * (da == db)).astype(np.uint32)}


def same(want, got):
    """Bit for bit, any NaN matching any NaN."""
    import numpy as np
    want = np.asarray(want)
    got = np.asarray(got).view(want.dtype) if want.dtype.kind == 'f' else np.asarray(got)
    if want.dtype.kind != 'f':
        return want == got
    bits = 'u8' if want.dtype == np.float64 else 'u4'
    return (np.isnan(want) & np.isnan(got)) | (want.view(bits) == got.view(bits))


SOURCE = '''__global__ void mix(const float *x, const float *y, const int *k, float *ratio, float *sum, float *wide, float *tenth,
                    int *cmp, int n) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i >= n) return;
  float v = x[i], c = y[i];
  ratio[i] = v * 0.5 / (c + 1e-6);
  sum[i] = -(v * 0.1) + k[i] / 3.0;
  wide[i] = (float)(v - 1e-30 * c);
  tenth[i] = 0.1;
  cmp[i] = (v * 1.1 < c) + 2 * (v + 0.1 >= c) + 4 * (v / 3.0 == c / 3.0) + 8 * (v * 0.3 != c) + 16 * (v * 0.1 > c);
}
'''


def build(work, source=SOURCE):
    from frontends.c_import import import_source
    path = Path(work)/'k.cu'
    path.write_text(source)
    return import_source(path, kernels=['mix'])


def mix_cases():
    import numpy as np
    from differential.simulate import Case
    out = []
    for seed, n in enumerate((64, 1000, 4099)):
        rng = np.random.default_rng(seed)
        x = (rng.standard_normal(n) * np.exp(rng.uniform(-20, 20, n))).astype(np.float32)
        y = np.where(rng.random(n) < 0.3, x * np.float32(1.1), rng.standard_normal(n) * np.exp(rng.uniform(-20, 20, n)))
        y = y.astype(np.float32)
        x[:6] = [0, -0.0, np.inf, np.nan, 1e-45, 3e38]
        y[:6] = [-1e-6, 0, 1, 2, -1e-45, 1e-38]
        k = rng.integers(-2**31, 2**31, n, dtype=np.int64).astype(np.int32)
        out.append(Case({'n': (n, 1, 1)}, {'x': x, 'y': y, 'k': k}, {}, f'n={n}'))
    return out


class Import(unittest.TestCase):
    def test_double_expressions_call_the_library(self):
        from qpel_text import print_program
        with tempfile.TemporaryDirectory() as work:
            program, report = build(work)
        text = print_program(program)
        self.assertIn('double_to_f32(double_div(double_mul(double_from_f32(v), 4602678819172646912_u64)', text)
        self.assertIn('= 0.10000000149011612f;', text)  # a double constant converted to float, folded
        self.assertNotIn('fn double_from_u32', text)  # only the functions used, and those they call
        self.assertTrue(any('computed exactly in software' in a for a in report['adaptations']))

    def refused(self, old, new):
        from frontends.c_import import Unsupported
        self.assertIn(old, SOURCE)
        with tempfile.TemporaryDirectory() as work, self.assertRaises(Unsupported) as caught:
            build(work, SOURCE.replace(old, new))
        self.assertTrue(any(f'[{code}]' in str(caught.exception) for code in ('double', 'local-type')), str(caught.exception))

    def test_a_double_local(self):
        self.refused('  ratio[i] = v * 0.5 / (c + 1e-6);', '  double d = v * 0.5;\n  ratio[i] = d / (c + 1e-6);')

    def test_a_double_math_function(self):
        self.refused('(float)(v - 1e-30 * c)', '(float)sqrt(v - 1e-30 * c)')

    def test_a_double_to_an_integer(self):
        self.refused('cmp[i] = (v * 1.1 < c)', 'cmp[i] = (int)(v * 1.1) + (v * 1.1 < c)')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Run(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        from qpel_ir import Lowering
        from differential.simulate import Program
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-soft-double-')
        work = Path(cls.tmp.name)
        cls.runs = {}
        library = library_program()
        mix, report = build(work)
        for name, program in (('library', library), ('mix', mix)):
            (work/name).mkdir()
            (work/name/'p.json').write_text(json.dumps(program))
            compile_program(work/name/'p.json', work/name/'build')
            plan = Lowering(program).plan()
            generated = work/name/'build/package/generated'
            (work/name/'q').mkdir()
            cls.runs[name] = (plan, generated, Program(generated/(plan['module'] + '.cu'), plan, work/name/'q'))
        (work/'o').mkdir()
        cls.original = Program(work/'k.cu', cls.runs['mix'][0], work/'o', original=report)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_library(self, run):
        for case in library_cases():
            got, want = run(case), expected(case)
            for name in OUTPUTS:
                with self.subTest(case=case.label, output=name):
                    ok = same(want[name], got[name])
                    self.assertTrue(ok.all(), f'{int((~ok).sum())} mismatches')

    def check_mix(self, run):
        import numpy as np
        for case in mix_cases():
            want, got = self.original.run(case), run(case)
            for name in ('ratio', 'sum', 'wide', 'tenth', 'cmp'):
                with self.subTest(case=case.label, output=name):
                    w, g = np.asarray(want[name]), np.asarray(got[name])
                    self.assertTrue(same(w, g).all(), f'{int((~same(w, g)).sum())} mismatches')

    def test_library_cpu_simulator(self):
        self.check_library(self.runs['library'][2].run)

    def test_kernel_cpu_simulator(self):
        self.check_mix(self.runs['mix'][2].run)

    def gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        plan, generated, _ = self.runs[self.which]
        host = matrix.load_host(generated)
        return lambda case: metal.run_generated(host, plan, case)

    def test_library_apple_gpu(self):
        self.which = 'library'
        self.check_library(self.gpu())

    def test_kernel_apple_gpu(self):
        self.which = 'mix'
        self.check_mix(self.gpu())


if __name__ == '__main__':
    unittest.main(verbosity=2)
