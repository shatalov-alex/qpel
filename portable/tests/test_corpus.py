"""Survey mode of the importer, and the pinned coverage of the real-source corpus."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from frontends import clang_ast, corpus
from frontends.c_import import Importer, import_source, Unsupported
from qpel_test_support import unavailable

HAS_CLANG = shutil.which('clang') is not None


@unittest.skipUnless(HAS_CLANG, 'clang required')
class Survey(unittest.TestCase):
    def survey(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'sample.cu'
            path.write_text(text)
            unit, source = clang_ast.parse(path, 'cuda')
            results = Importer(unit, source, path, 'cuda', 'sample', survey=True).survey()
        return {name: [i['code'] for i in info['issues']] for name, info in results.items()}, results

    def test_collects_every_blocker_and_skips_host_code(self):
        codes, results = self.survey('''
namespace lib {
template <typename T> __device__ T twice(T v) { return v + v; }
__device__ int lowest(long long* s, int n) {
  double done[2];
  switch (n) { case 0: atomicAdd(s + n, 1ll); case 1: break; }  // case 0 falls through: not in qpel
  return n;
}
__global__ void k(long long* s, float* out, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) out[i] = twice(1) + twice(1u) + lowest(s, (int)i);
}
}
void launch(long long* s, float* out, unsigned int n) { lib::k<<<1, 1>>>(s, out, n); }
''')
        self.assertEqual(results['launch']['kind'], 'host')
        # a template used with two types becomes one function per instantiation
        self.assertEqual((codes['twice_u32'], codes['twice_i32']), ([], []))
        # a switch whose case falls through is reported, and so is what is inside it
        self.assertEqual(sorted(set(codes['lowest'])), ['atomic-type', 'local-type', 'switch'])
        # k's own blocker (signed 64-bit atomics, through lowest's parameter) and those of the functions it calls
        self.assertEqual(codes['k'], ['atomic-type'])
        self.assertTrue(any(b.startswith('switch at lowest') for b in corpus.kernels(results)['k']))
        self.assertEqual(results['k']['calls'], ['lowest', 'twice_i32', 'twice_u32'])

    def test_one_parameter_blocker_is_reported_once(self):
        codes, _ = self.survey('''
__global__ void k(const unsigned char* in, double* out, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) { out[i] = in[i]; out[i] = out[i] + 1; }
}''')
        self.assertEqual(codes['k'], ['buffer-type'])

    def test_normal_import_still_stops_at_the_first_blocker(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'sample.cu'
            path.write_text('''
__global__ void k(int* s, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) { atomicAdd(s + i, 1); s[i] += 1; }
}''')
            with self.assertRaisesRegex(Unsupported, r'\[atomic-update\]'):
                import_source(path)

    def test_host_functions_are_not_imported(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'sample.cu'
            path.write_text('''
__global__ void k(unsigned int* out, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) out[i] = i;
}
void launch(unsigned int* out, unsigned int n) { k<<<(n + 63) / 64, 64>>>(out, n); }''')
            _, report = import_source(path)
        self.assertEqual((report['kernels'], report['helpers']), (['k'], []))

    def test_pytorch_extensions_parse_with_the_stand_ins(self):
        """A typical PyTorch CUDA extension (torch/extension.h, ATen, cooperative groups, dtype
        dispatch, pybind11) parses offline; its kernel imports and its host code is left out."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'ext.cu'
            path.write_text('''
#include <torch/extension.h>
#include <ATen/cuda/CUDAContext.h>
#include <c10/cuda/CUDAGuard.h>
#include <cooperative_groups.h>
#include <cuda.h>
namespace cg = cooperative_groups;
__global__ void scale(const float* x, float* y, float a, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) y[i] = x[i] * a;
}
torch::Tensor scale_cuda(torch::Tensor x, double a) {
  TORCH_CHECK(x.is_cuda(), "x must be a CUDA tensor");
  TORCH_CHECK(x.dtype() == torch::kFloat32, "float32 only");
  const at::cuda::OptionalCUDAGuard guard(x.device());
  auto y = torch::empty_like(x);
  auto stream = at::cuda::getCurrentCUDAStream();
  unsigned int n = x.numel();
  AT_DISPATCH_FLOATING_TYPES(x.scalar_type(), "scale", [&] {
    scale<<<(n + 255) / 256, 256, 0, stream>>>(x.data_ptr<float>(), y.data_ptr<float>(), (float)a, n);
  });
  auto sorted = std::get<0>(torch::sort(y));
  return torch::zeros({n}, torch::dtype(torch::kInt64).device(x.device())) + sorted;
}
PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) { m.def("scale", &scale_cuda, py::call_guard<py::gil_scoped_release>()); }
''')
            _, report = import_source(path)
        self.assertEqual(report['kernels'], ['scale'])


@unittest.skipUnless(HAS_CLANG, 'clang required')
class SurveyLengths(unittest.TestCase):
    """Lengths of launch-sized shared memory for the survey: each kernel's own, from its template arguments."""
    ENTRY = {'kernel_args': '_kernel_(?P<CDIM>\\d+)_f32$',
             'shared': {'id_batch': 256, 'rgbs_batch': '256*CDIM', 's': '256*(28 + 4*CDIM)', 'w': 'CDIM // 0'}}

    def test_lengths_follow_the_template_arguments(self):
        self.assertEqual(corpus.survey_lengths(self.ENTRY, 'bwd_kernel_513_f32'),
                         {'id_batch': 256, 'rgbs_batch': 256 * 513, 's': 256 * (28 + 4 * 513)})

    def test_a_length_needing_missing_arguments_is_left_out(self):
        self.assertEqual(corpus.survey_lengths(self.ENTRY, 'other_kernel'), {'id_batch': 256})
        self.assertIsNone(corpus.survey_lengths({'shared': {'x': 'N'}}, 'k'))

    def test_only_arithmetic(self):
        self.assertIsNone(corpus._arithmetic('__import__("os")', {}))
        self.assertEqual(corpus._arithmetic('(2 + 3) * N - 1', {'N': 4}), 19)


class Coverage(unittest.TestCase):
    def test_coverage_matches_the_recorded_result(self):
        """The coverage floor: every corpus kernel imports, or is blocked, exactly as recorded in
        corpus/coverage.json. Metal sources need Xcode's Metal toolchain; without it only the
        CUDA sources are checked (and the test says so)."""
        recorded = json.loads(corpus.COVERAGE.read_text())
        dialect = None if clang_ast.metal_available() else 'cuda'
        found = corpus.coverage(dialect=dialect)
        self.assertTrue(found)
        for source, kernels in found.items():
            with self.subTest(source=source):
                self.assertEqual(kernels, recorded[source],
                                 'corpus coverage changed; check it, then run python3 -m frontends.corpus --update')
        if dialect:
            unavailable(self, 'metal', 'no Metal toolchain: only the CUDA sources were checked')

    def test_corpus_sources_are_unmodified(self):
        for entry in corpus.entries():
            data = (corpus.CORPUS/entry['source']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'], entry['source'])

    def test_vendored_headers_are_unmodified(self):
        for sums in corpus.CORPUS.glob('*/SHA256SUMS'):
            for line in sums.read_text().splitlines():
                digest, name = line.split(maxsplit=1)
                data = (sums.parent/name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, sums.parent.name + '/' + name)



class Compiler(unittest.TestCase):
    """C++23 sources (gsplat's Lidar sources: P2280) are parsed with LLVM's clang 20 or later."""
    SOURCE = ('struct P { static constexpr float F = 2.f; };\n'
              '__global__ void k(const P &p, float *y) { constexpr float s = 1.f / p.F; y[0] = s; }\n')

    def test_other_standards_keep_xcode_clang(self):
        self.assertEqual(clang_ast.command('k.cu', 'cuda', defines=['-std=c++20'])[0], 'clang')

    def test_a_reference_in_a_constant_expression(self):
        if clang_ast.llvm_clang() is None:
            self.skipTest('LLVM clang 20 or later required (brew install llvm, or QPEL_LLVM_CLANG)')
        self.assertEqual(clang_ast.command('k.cu', 'cuda', defines=['-std=c++23'])[0], clang_ast.llvm_clang())
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text(self.SOURCE)
            unit, _ = clang_ast.parse(path, 'cuda', (), ('-std=c++23',))
            self.assertTrue(unit['inner'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
