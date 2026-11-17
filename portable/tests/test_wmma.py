"""NVIDIA's WMMA (mma.h) read by the CUDA importer: a 16 x 16 x 16 fragment is a 2 x 2 array of qpel's 8 x 8
matrices (IR version 14), fill_fragment, load_matrix_sync, store_matrix_sync and mma_sync the same on each
block (mma_sync as two matrix_mma in k order). A GEMM written like NVIDIA's WMMA sample imports; on the CPU
simulator the original (through the stand-in mma.h, k order by fmaf) and qpel's CUDA agree bit for bit; on
GPGPU-Sim the original's own WMMA and qpel's mma.sync, and on the Apple GPU qpel's Metal, are within the bound
of a sum of products in any order."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_ir import validate  # noqa: E402
from qpel_text import print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402
from test_matrix_tiles import gemm  # noqa: E402

SOURCE = r'''#include <mma.h>
using namespace nvcuda;
// d = c + a b for row-major a (m x k), b (k x n), c and d (m x n), each warp one 16 x 16 tile of d
__global__ void wmma_gemm(const half* a, const half* b, const float* c, float* d, unsigned n, unsigned m, unsigned k) {
  const unsigned warp = (blockIdx.x * blockDim.x + threadIdx.x) / 32;
  const unsigned across = n / 16;
  if (warp >= across * (m / 16)) return;
  const unsigned row = warp / across * 16, column = warp % across * 16;
  wmma::fragment<wmma::matrix_a, 16, 16, 16, half, wmma::row_major> a_frag;
  wmma::fragment<wmma::matrix_b, 16, 16, 16, half, wmma::row_major> b_frag;
  wmma::fragment<wmma::accumulator, 16, 16, 16, float> acc;
  wmma::fill_fragment(acc, 0.0f);
  wmma::load_matrix_sync(acc, c + row * n + column, n, wmma::mem_row_major);
  for (unsigned i = 0; i < k; i += 16) {
    wmma::load_matrix_sync(a_frag, a + row * k + i, k);
    wmma::load_matrix_sync(b_frag, b + i * n + column, n);
    wmma::mma_sync(acc, a_frag, b_frag, acc);
  }
  wmma::store_matrix_sync(d + row * n + column, acc, n, wmma::mem_row_major);
}
'''
GEOMETRY = {'groups': ['8', '1', '1'], 'block': [64, 1, 1]}  # 16 warps: enough for the largest case (12 tiles)
SHAPES = ((16, 16, 16), (32, 48, 32), (64, 32, 48), (48, 64, 16))  # (n, m, k)


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(16)
    out = []
    for n, m, k in SHAPES:
        length = max(m * k, k * n, m * n)
        inputs = {'a': rng.uniform(-2, 2, length).astype(np.float16), 'b': rng.uniform(-2, 2, length).astype(np.float16),
                  'c': rng.uniform(-4, 4, length).astype(np.float32)}
        out.append(Case({'size': (length, 1, 1)}, inputs, {'n': n, 'm': m, 'k': k}, f'n={n} m={m} k={k}'))
    return out


def oracle(case):
    n, m, k = case.values['n'], case.values['m'], case.values['k']
    return gemm(case.inputs['a'], case.inputs['b'], case.inputs['c'], m, n, k)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Wmma(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-wmma-')
        work = Path(cls.work.name)
        cls.source = work/'wmma.cu'
        cls.source.write_text(SOURCE)
        cls.program, cls.report = import_source(cls.source, kernels=['wmma_gemm'], module='wmma_gemm', geometry=GEOMETRY)
        (work/'p.json').write_text(json.dumps(cls.program))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_imported(self):
        validate(self.program)
        self.assertEqual(self.program['version'], 14)
        text = print_program(self.program)
        self.assertIn('let a_frag: array<matrix<f16, 8, 8>, 4> = [qpel.matrix_fill<f16>(0.0f)', text)
        self.assertIn('let acc: array<matrix<f32, 8, 8>, 4> =', text)
        self.assertIn('acc[3u] = qpel.matrix_mma(a_frag[3u], b_frag[3u], qpel.matrix_mma(a_frag[2u], b_frag[1u], acc[3u]));', text)

    def test_not_in_qpel(self):
        from frontends.c_import import import_source
        for old, new, message in (
                ('wmma::matrix_b, 16, 16, 16, half, wmma::row_major', 'wmma::matrix_b, 16, 16, 16, half, wmma::col_major', 'column-major'),
                ('acc, n, wmma::mem_row_major);\n}', 'acc, n, wmma::mem_col_major);\n}', 'column-major'),
                ('wmma::accumulator, 16, 16, 16, float', 'wmma::accumulator, 16, 16, 16, half', 'half accumulator'),
                ('wmma::fragment<wmma::matrix_a, 16, 16, 16, half', 'wmma::fragment<wmma::matrix_a, 32, 8, 16, half', '32x8x16')):
            with self.subTest(new), tempfile.TemporaryDirectory() as work:
                self.assertIn(old, SOURCE)
                text = SOURCE.replace(old, new)
                if 'float' not in new and 'accumulator' in new:  # a half accumulator loads and stores half
                    text = text.replace('const float* c, float* d', 'const half* c, half* d')
                if '32, 8, 16' in new:
                    text = text.replace('wmma::matrix_b, 16, 16, 16', 'wmma::matrix_b, 32, 8, 16').replace(
                        'wmma::accumulator, 16, 16, 16', 'wmma::accumulator, 32, 8, 16')
                path = Path(work)/'k.cu'
                path.write_text(text)
                with self.assertRaisesRegex(Exception, message):
                    import_source(path, kernels=['wmma_gemm'], module='wmma_gemm', geometry=GEOMETRY)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        work = Path(self.work.name)
        (work/'o').mkdir(exist_ok=True)
        (work/'q').mkdir(exist_ok=True)
        original = Program(self.source, self.plan, work/'o', original=self.report)
        ours = Program(self.generated/'wmma_gemm.cu', self.plan, work/'q')
        for case in cases():
            n, m = case.values['n'], case.values['m']
            want, _ = oracle(case)
            for name, program in (('original', original), ('qpel', ours)):
                got = program.run(case)['d'][:m * n]
                np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32), err_msg=f'{name} {case.label}')

    def test_apple_gpu(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in cases():
            n, m = case.values['n'], case.values['m']
            want, bound = oracle(case)
            got = np.asarray(metal.run_generated(host, self.plan, case)['d'])[:m * n]
            error = np.abs(got.astype(np.float64) - want)
            self.assertTrue(np.all(error <= bound), f'{case.label}: worst {np.max(error / np.maximum(bound, 1e-30)):.2f} of the bound')

    def test_gpgpusim(self):
        """The original's WMMA (wmma.load, wmma.mma) and qpel's mma.sync on GPGPU-Sim's sm_75: each within the bound."""
        import numpy as np
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-wmma-nvidia-')).resolve()
        try:
            programs = {'original': GpuProgram(self.source, self.plan, root/'original', original=self.report),
                        'qpel': GpuProgram(self.generated/'wmma_gemm.cu', self.plan, root/'qpel')}
            pending = []
            for name, program in programs.items():
                for number, case in enumerate(cases()):
                    folder = program.work/f'c{number:03}'
                    folder.mkdir()
                    program.write_case(case, folder)
                    pending.append((name, program, case, folder))
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            for name, program, case, folder in pending:
                got, error = program.outcome(folder, case)
                self.assertIsNone(error, f'{name} {case.label}')
                n, m = case.values['n'], case.values['m']
                want, bound = oracle(case)
                error = np.abs(np.asarray(got['d'][:m * n], np.float64) - want)
                self.assertTrue(np.all(error <= bound), f'{name} {case.label}: worst {np.max(error / np.maximum(bound, 1e-30)):.2f} of the bound')
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
