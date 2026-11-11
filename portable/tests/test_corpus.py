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
__device__ int lowest(float* s, int n) {
  float done[2];
  switch (n) { default: atomicAdd(s + n, 0.5f); }
  return n;
}
__global__ void k(float* s, float* out, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) out[i] = twice(1) + twice(1u) + lowest(s, (int)i);
}
}
void launch(float* s, unsigned int n) { lib::k<<<1, 1>>>(s, s, n); }
''')
        self.assertEqual(results['launch']['kind'], 'host')
        self.assertEqual(codes['twice'], ['template'])
        # the switch is reported, and so is what is inside it
        self.assertEqual(sorted(set(codes['lowest'])), ['atomic-type', 'local-type', 'switch'])
        # k's own blocker (float atomics, through lowest's parameter) and those of the functions it calls
        self.assertEqual(codes['k'], ['atomic-type'])
        self.assertTrue(any(b.startswith('template at twice') for b in corpus.kernels(results)['k']))
        self.assertEqual(results['k']['calls'], ['lowest', 'twice'])

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


@unittest.skipUnless(HAS_CLANG, 'clang required')
class Coverage(unittest.TestCase):
    # The whole corpus takes minutes, so check_all runs `frontends.corpus --check`; SAM 2 stays a unit test.
    def test_sam2_coverage_matches_the_recorded_result(self):
        recorded = json.loads(corpus.COVERAGE.read_text())
        entry = next(e for e in corpus.entries() if e['source'].startswith('sam2/'))
        self.assertEqual(corpus.source_coverage(entry), recorded[entry['source']],
                         'corpus coverage changed; check it, then run python3 -m frontends.corpus --update')

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
