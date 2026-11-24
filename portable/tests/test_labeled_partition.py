"""Labeled partitions of a warp read as the calling thread alone (frontends/coalesced.py).

`cg::labeled_partition(cg::tiled_partition<32>(cg::this_thread_block()), label)` is every thread of the warp
with the caller's label. A kernel that sums values over it (directly, or through a helper such as gsplat's
`warpSum`) and lets the group's rank-0 thread add the sums atomically is read with each thread adding its own
values: the same sums, the float additions in another order. The kernel below moves its pointer parameter and adds
a value, a sum and a difference of summed values. Its inputs are quarter-integers, so every sum is exact in any
order: the original (on the CPU simulator, whose labeled partitions are CUDA's, summed across the warp's threads),
qpel's CUDA there and qpel's Metal on the Apple GPU all give numpy's sums bit for bit. With random floats the
original and qpel's CUDA agree within the float error of the sums. Each way of using a group that would change the
results (a sum read outside the rank-0 block, a product of sums, a constant added per group, an `else`, a
maximum) leaves the kernel unimported."""
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

SOURCE = '''#include <cooperative_groups.h>
#include <cooperative_groups/reduce.h>
namespace cg = cooperative_groups;

template <class G> __device__ void sum2(float &a, float &b, G &g) {
  a = cg::reduce(g, a, cg::plus<float>());
  b = cg::reduce(g, b, cg::plus<float>());
}

__global__ void scatter(const float *x, const int *label, float *sums, int n) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i >= n) return;
  float a = x[i], b = 2.0f * x[i];
  auto warp = cg::tiled_partition<32>(cg::this_thread_block());
  auto g = cg::labeled_partition(warp, label[i]);
  sum2(a, b, g);
  float c = x[i] * x[i];
  c = cg::reduce(g, c, cg::plus<float>());
  if (g.thread_rank() == 0) {
    sums += label[i] * 3;
    atomicAdd(sums, a);
    atomicAdd(sums + 1, b + c);
    atomicAdd(sums + 2, b - a);
  }
}
'''
LABELS = 8  # sums holds 3 per label, and n >= 3 * LABELS


def build(work, source=SOURCE):
    from frontends.c_import import import_source
    path = Path(work)/'k.cu'
    path.write_text(source)
    resources = [{'name': 'x', 'kind': 'input', 'type': 'buffer<f32, read>', 'extent': 'n'},
                 {'name': 'label', 'kind': 'input', 'type': 'buffer<i32, read>', 'extent': 'n'},
                 {'name': 'sums', 'kind': 'inout', 'type': 'buffer<f32, atomic>', 'extent': 'n'},
                 {'name': 'n', 'kind': 'shape', 'type': 'shape3', 'extent': None}]
    plan = {'schema': 2, 'module': 'labeled', 'resources': resources,
            'stages': [{'kernel': 'scatter', 'grid': 'n', 'blockSize': 64, 'completionBarrier': True,
                        'bindings': [{'parameter': r['name'], 'resource': r['name']} for r in resources]}]}
    return import_source(path, plan=plan, kernels=['scatter'])


def oracle(x, label):
    import numpy as np
    sums = np.zeros(len(x), np.float64)
    for v, k in zip(x.astype(np.float64), label):
        sums[3 * k:3 * k + 3] += (v, 2 * v + v * v, v)
    return sums


def cases(exact=True):
    import numpy as np
    from differential.simulate import Case
    out = []
    for seed, (n, layout) in enumerate((n, layout) for n in (24, 31, 100, 257) for layout in ('random', 'runs', 'one')):
        rng = np.random.default_rng(seed)
        if layout == 'random':
            label = rng.integers(0, LABELS, n)
        elif layout == 'runs':  # consecutive threads share a label, as gsplat's camera ids do
            label = np.sort(rng.integers(0, LABELS, n))
        else:
            label = np.full(n, 5)
        x = (rng.integers(-32, 33, n) / 4) if exact else rng.standard_normal(n) * np.exp(rng.uniform(-4, 4, n))
        out.append(Case({'n': (n, 1, 1)}, {'x': x.astype(np.float32), 'label': label.astype(np.int32),
                                           'sums': np.zeros(n, np.float32)}, {},
                        f'n={n} {layout}'))
    return out


class Import(unittest.TestCase):
    def test_reads_the_group_as_the_thread(self):
        from qpel_text import print_program
        with tempfile.TemporaryDirectory() as work:
            program, report = build(work)
        text = print_program(program)
        self.assertIn('sums: buffer<f32, atomic>', text)
        self.assertTrue(any('labeled partitions of a warp' in a for a in report['adaptations']))

    def refused(self, old, new):
        from frontends.c_import import Unsupported
        self.assertIn(old, SOURCE)
        with tempfile.TemporaryDirectory() as work, self.assertRaises(Unsupported):
            build(work, SOURCE.replace(old, new))

    def test_a_sum_read_outside_the_block(self):
        self.refused('  float c = x[i] * x[i];', '  sums[i] = a;\n  float c = x[i] * x[i];')

    def test_a_product_of_sums(self):
        self.refused('atomicAdd(sums + 1, b + c);', 'atomicAdd(sums + 1, b * c);')

    def test_a_constant_per_group(self):
        self.refused('atomicAdd(sums + 2, b - a);', 'atomicAdd(sums + 2, 1.0f);')

    def test_an_else(self):
        self.refused('    atomicAdd(sums + 2, b - a);\n  }', '    atomicAdd(sums + 2, b - a);\n  } else { sums[i] = 0.0f; }')

    def test_a_maximum(self):
        self.refused('c = cg::reduce(g, c, cg::plus<float>());', 'c = cg::reduce(g, c, cg::greater<float>());')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Run(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        from qpel_ir import Lowering
        from differential.simulate import Program
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-labeled-partition-')
        work = Path(cls.tmp.name)
        cls.program, cls.report = build(work)
        (work/'p.json').write_text(json.dumps(cls.program))
        compile_program(work/'p.json', work/'build')
        cls.plan = Lowering(cls.program).plan()
        cls.generated = work/'build/package/generated'
        for folder in ('o', 'q'):
            (work/folder).mkdir()
        cls.original = Program(work/'k.cu', cls.plan, work/'o', original=cls.report)
        cls.ours = Program(cls.generated/(cls.plan['module'] + '.cu'), cls.plan, work/'q')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, got, case):
        import numpy as np
        want = oracle(case.inputs['x'], case.inputs['label']).astype(np.float32)
        self.assertTrue(np.array_equal(np.asarray(got['sums']).view(np.uint32), want.view(np.uint32)), case.label)

    def test_the_original_uses_real_groups(self):
        self.assertTrue(self.original.threaded)

    def test_cpu_simulator(self):
        for case in cases():
            with self.subTest(case=case.label):
                self.check(self.original.run(case), case)
                self.check(self.ours.run(case), case)

    def test_cpu_simulator_floats(self):
        import numpy as np
        eps = float(np.finfo(np.float32).eps)
        for case in cases(exact=False):
            with self.subTest(case=case.label):
                want, got = self.original.run(case)['sums'], self.ours.run(case)['sums']
                x, label = case.inputs['x'].astype(np.float64), case.inputs['label']
                scale = np.zeros(len(x))
                for v, k in zip(x, label):
                    scale[3 * k:3 * k + 3] += (abs(v), 2 * abs(v) + v * v, 3 * abs(v))
                count = np.bincount(label, minlength=len(x) // 3 + 1)[np.arange(len(x)) // 3]
                bound = 2 * (count + 2) * eps * scale
                self.assertTrue(np.all(np.abs(np.asarray(want, np.float64) - np.asarray(got, np.float64)) <= bound), case.label)

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
            with self.subTest(case=case.label):
                self.check(metal.run_generated(host, self.plan, case), case)


if __name__ == '__main__':
    unittest.main(verbosity=2)
