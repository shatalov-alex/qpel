"""IR version 5 subgroup (SIMD-group, warp) operations: text, rules, both backends (CPU simulator
with warps, Apple GPU) against an oracle, and both importers."""
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

PROGRAM = '''module warp_ops {
  fn lane_sum(v: f32) -> f32 {
    let s: f32 = v;
    for (let m: u32 = 16u; m > 0u; m = m >> 1u) {
      s = s + qpel.shuffle_xor(s, m);
    }
    return s;
  }
  kernel warp_ops(x: buffer<f32, read> @0, sums: buffer<f32, write> @1, ids: buffer<u32, write> @2, moved: buffer<i32, write> @3, n: shape3 @4) groups {
    let i: u32 = (qpel.group.x * qpel.group_size.x) + qpel.local.x;
    let count: u32 = (n.x * n.y) * n.z;
    let v: f32 = 0.0f;
    if (i < count) {
      v = x[i];
    }
    let total: f32 = qpel.subgroup_add(v);
    let helper: f32 = lane_sum(v);
    let biggest: i32 = qpel.subgroup_max(cast<i32>(i) - 40);
    let down: i32 = qpel.shuffle_down(cast<i32>(qpel.subgroup_lane), 5u);
    let up: i32 = qpel.shuffle_up(cast<i32>(qpel.subgroup_lane), 33u);
    let pick: f32 = qpel.shuffle(v, qpel.subgroup_lane + 7u);
    let first: u32 = 0u;
    if (qpel.subgroup_index == 1u) {
      first = qpel.subgroup_or(qpel.subgroup_lane * 3u);
    }
    let row: u32 = 0u;
    if ((qpel.local.x / 32u) == 0u) {
      row = qpel.subgroup_min(i + 1u);
    }
    if (i < count) {
      sums[i] = (total - helper) + pick;
      ids[i] = ((qpel.subgroup_index * 1000u) + first) + row;
      moved[i] = ((down * 100) + up) + biggest;
    }
  }
}
pipeline warp_ops_launch {
  shape n;
  input x: buffer<f32, read> in n;
  output sums: buffer<f32, write> in n;
  output ids: buffer<u32, write> in n;
  output moved: buffer<i32, write> in n;
  dispatch warp_ops over n block (64, 1, 1) groups ("(W*H*N + 63)/64", "1", "1") (x = x, sums = sums, ids = ids, moved = moved, n = n);
}
'''
TOTAL = '    let total: f32 = qpel.subgroup_add(v);\n'


def oracle(x):
    """sums, ids, moved as defined (NUMERICS.md, Subgroups)."""
    import numpy as np
    n = len(x)
    m = -(-n // 64) * 64
    v = np.zeros(m, np.float32)
    v[:n] = x
    sums, ids, moved = np.zeros(n, np.float32), np.zeros(n, np.uint32), np.zeros(n, np.int32)
    lanes = np.arange(32)
    for base in range(0, m, 32):
        warp = v[base:base + 32].copy()
        for mask in (16, 8, 4, 2, 1):  # the butterfly: every lane ends with the same bits
            warp = (warp + warp[lanes ^ mask]).astype(np.float32)
        total = warp
        pick = v[base + ((lanes + 7) & 31)]
        index = (base % 64) // 32
        first = int(np.bitwise_or.reduce(lanes * 3)) if index == 1 else 0
        row = base + 1 if index == 0 else 0
        biggest = base + 31 - 40
        down = np.where(lanes + 5 < 32, lanes + 5, lanes)
        for lane in range(32):
            i = base + lane
            if i < n:
                sums[i] = np.float32(np.float32(total[lane] - total[lane]) + pick[lane])
                ids[i] = index * 1000 + first + row
                moved[i] = down[lane] * 100 + lane + biggest
    return sums, ids, moved


class Rules(unittest.TestCase):
    def test_round_trip(self):
        program = parse_program(PROGRAM)
        validate(program)
        self.assertEqual(program['version'], 5)
        self.assertEqual(print_program(parse_program(print_program(program))), print_program(program))

    def test_every_lane_of_a_subgroup_takes_part(self):
        cases = {
            'under a condition of the thread': '    let total: f32 = 0.0f;\n    if (i < count) {\n      total = qpel.subgroup_add(v);\n    }\n',
            'under a condition of the lane': '    let total: f32 = 0.0f;\n    if (qpel.subgroup_lane < 4u) {\n      total = qpel.shuffle(v, 0u);\n    }\n',
            'in a helper called by some lanes': '    let total: f32 = 0.0f;\n    if (v > 1.0f) {\n      total = lane_sum(v);\n    }\n',
            'after a return some lanes take': '    if (v > 1.0f) {\n      return;\n    }\n' + TOTAL,
        }
        for label, new in cases.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, 'SUBGROUP_UNIFORM'):
                validate(parse_program(PROGRAM.replace(TOTAL, new)))
        with self.assertRaisesRegex(ValueError, 'SUBGROUP_UNIFORM'):  # a helper's condition on its parameter
            validate(parse_program(PROGRAM.replace('    let s: f32 = v;\n', '    let s: f32 = v;\n    if (v > 0.0f) {\n'
                                                   '      s = qpel.shuffle(v, 1u);\n    }\n')))

    def test_where_subgroup_operations_go(self):
        with self.assertRaisesRegex(ValueError, 'SUBGROUP_BLOCK'):
            validate(parse_program(PROGRAM.replace('block (64, 1, 1)', 'block (48, 1, 1)')))
        with self.assertRaisesRegex(ValueError, 'SUBGROUP_BLOCK'):  # 16 lanes: a subgroup not full
            validate(parse_program(PROGRAM.replace('block (64, 1, 1)', 'block (16, 1, 1)')))
        validate(parse_program(PROGRAM.replace('block (64, 1, 1)', 'block (16, 16, 1)')))  # x divides 32, full subgroups
        with self.assertRaisesRegex(ValueError, 'SUBGROUP_TYPE'):
            validate(parse_program(PROGRAM.replace('qpel.subgroup_or(qpel.subgroup_lane * 3u)',
                                                   'cast<u32>(qpel.subgroup_xor(cast<f32>(qpel.subgroup_lane)))')))
        with self.assertRaisesRegex(ValueError, 'IR version 4 cannot use'):
            program = parse_program(PROGRAM)
            program.update(version=4, semantics='qpel-portable-core-v4')
            validate(program)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-subgroups-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(PROGRAM)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def cases(self):
        import numpy as np
        from differential.simulate import Case
        for n in (1, 33, 64, 200):
            x = np.random.default_rng(n).uniform(-1, 4, n).astype(np.float32)
            yield Case({'n': (n, 1, 1)}, {'x': x}, {}, str(n)), x

    def check(self, out, x):
        import numpy as np
        sums, ids, moved = oracle(x)
        np.testing.assert_array_equal(out['sums'], sums)
        np.testing.assert_array_equal(out['ids'], ids)
        np.testing.assert_array_equal(out['moved'], moved)

    def test_cpu_simulator_runs_warps(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'warp_ops.cu', self.plan, Path(self.work.name)/'sim')
        self.assertTrue(program.threaded)
        for case, x in self.cases():
            self.check(program.run(case), x)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case, x in self.cases():
            self.check(metal.run_generated(host, self.plan, case), x)

    def test_both_importers_give_the_program_back(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from frontends.roundtrip import canonical
        original = canonical(parse_program(PROGRAM))
        for source in ('warp_ops.cu', 'warp_ops.metal'):
            if source.endswith('.metal') and not clang_ast.metal_available():
                unavailable(self, 'metal', 'Metal toolchain required')
            with self.subTest(source):
                program, _ = import_source(self.generated/source, plan=self.plan, module='warp_ops')
                self.assertEqual(canonical(program), original)


CUDA_SOURCE = """
__device__ float warp_sum(float v) {
  for (int offset = warpSize / 2; offset > 0; offset /= 2) v += __shfl_down_sync(0xffffffff, v, offset);
  return v;
}
__global__ void row_sums(const float* x, float* y, int n) {
  int lane = threadIdx.x % 32, warp = threadIdx.x / 32;
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  float v = i < n ? x[i] : 0.0f;
  v = warp_sum(v);
  int first = __shfl_sync(0xffffffffu, i, 0);
  if (lane == 0 && i < n) y[blockIdx.x * (blockDim.x / 32) + warp] = v + (float)(first - i);
}
"""
METAL_SOURCE = """#include <metal_stdlib>
using namespace metal;
kernel void row_sums(device const float* x [[buffer(0)]], device float* y [[buffer(1)]], constant uint& n [[buffer(2)]],
                     uint i [[thread_position_in_grid]], uint lane [[thread_index_in_simdgroup]],
                     uint warp [[simdgroup_index_in_threadgroup]], uint group [[threadgroup_position_in_grid]]) {
  float v = i < n ? x[i] : 0.0f;
  float s = simd_sum(v);
  uint top = simd_max(i);
  float right = simd_shuffle_down(v, 1);
  if (lane == 0 && i < n) y[group * 2 + warp] = s + (float)(top - i) + right * 0.0f;
}
"""


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Import(unittest.TestCase):
    def imported(self, name, text):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/name
            path.write_text(text)
            program, report = import_source(path)
        validate(program)
        return print_program(program), report

    def test_cuda_warp_reduction(self):
        text, _ = self.imported('row_sums.cu', CUDA_SOURCE)
        self.assertIn('qpel.shuffle_down(v, cast<u32>(offset))', text.replace('qpel.shuffle_down(v, offset)',
                                                                             'qpel.shuffle_down(v, cast<u32>(offset))'))
        self.assertIn('qpel.shuffle(', text)
        self.assertIn(') groups {', text)

    def test_metal_simd_group_functions(self):
        from frontends import clang_ast
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        text, report = self.imported('row_sums.metal', METAL_SOURCE)
        for spelled in ('qpel.subgroup_add(v)', 'qpel.subgroup_max(', 'qpel.shuffle_down(v, ', 'qpel.subgroup_lane',
                        'qpel.subgroup_index'):
            self.assertIn(spelled, text)
        self.assertTrue(any('simd_sum of float32' in a for a in report.get('adaptations', [])), report.get('adaptations'))

    def test_partial_masks_are_refused(self):
        from frontends.c_import import Unsupported, import_source  # noqa: F401
        with self.assertRaisesRegex(Exception, 'full mask'):
            self.imported('masked.cu', CUDA_SOURCE.replace('0xffffffffu, i, 0', '0x0000ffffu, i, 0'))

WARP_SUM_CUDA = """__device__ float warp_sum(float v) {
  for (int offset = 16; offset > 0; offset /= 2) v += __shfl_down_sync(0xffffffff, v, offset);
  return v;
}
__global__ void warp_sum_kernel(const float* x, float* sums, unsigned int n) {
  unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  float v = i < n ? x[i] : 0.0f;
  v = warp_sum(v);
  if ((threadIdx.x & 31) == 0 && i < n) sums[i / 32] = v;
}
"""
WARP_SUM_SIMD = """#include <metal_stdlib>
using namespace metal;
kernel void warp_sum_kernel(device const float* x [[buffer(0)]], device float* sums [[buffer(1)]], constant uint& n [[buffer(2)]],
                            uint group [[threadgroup_position_in_grid]], uint local [[thread_position_in_threadgroup]],
                            uint size [[threads_per_threadgroup]], uint lane [[thread_index_in_simdgroup]]) {
  uint i = group * size + local;
  float v = i < n ? x[i] : 0.0f;
  float s = simd_sum(v);
  if (lane == 0 && i < n) sums[i / 32] = s;
}
"""
WARP_SUM_XOR = """#include <metal_stdlib>
using namespace metal;
kernel void warp_sum_kernel(device const float* x [[buffer(0)]], device float* sums [[buffer(1)]], constant uint& n [[buffer(2)]],
                            uint group [[threadgroup_position_in_grid]], uint local [[thread_position_in_threadgroup]],
                            uint size [[threads_per_threadgroup]], uint lane [[thread_index_in_simdgroup]]) {
  uint i = group * size + local;
  float v = i < n ? x[i] : 0.0f;
  float s = v;
  for (ushort m = 16; m > 0; m >>= 1) s += simd_shuffle_xor(s, m);
  if (lane == 0 && i < n) sums[i / 32] = s;
}
"""


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Verify(unittest.TestCase):
    """A CUDA warp sum (shuffle-down tree) against Metal ports, with qpel verify."""

    def run_verify(self, metal, port_text):
        from frontends import clang_ast
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        from differential import verify
        with tempfile.TemporaryDirectory() as d:
            original, port = Path(d)/'warp_sum.cu', Path(d)/'warp_sum.metal'
            original.write_text(WARP_SUM_CUDA)
            port.write_text(port_text)
            return verify.verify(original, port, values={'n': 'volume'}, edges=False, metal=metal)

    def test_on_the_cpu(self):
        for text in (WARP_SUM_XOR, WARP_SUM_SIMD):  # the simulator's simd_sum adds in qpel's order
            result = self.run_verify('cpu', text)
            self.assertTrue(result['passed'], [c for c in result['cases'] if len(c) > 1])

    def test_on_the_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        xor = self.run_verify('preflight', WARP_SUM_XOR)
        self.assertTrue(xor['passed'], [c for c in xor['cases'] if len(c) > 1])
        # Apple's simd_sum adds in another, undocumented order: last-bit differences (NUMERICS.md, Subgroups)
        simd = self.run_verify('preflight', WARP_SUM_SIMD)
        self.assertFalse(simd['passed'])
        self.assertTrue(all('error' not in c for c in simd['cases']), simd['cases'])


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class OnNvidia(unittest.TestCase):
    """The CUDA warp sum on GPGPU-Sim (QPEL_NVIDIA=1): against itself, and against the Metal
    xor-butterfly port on the Apple GPU."""

    def setUp(self):
        from qpel_test_support import NVIDIA
        if not NVIDIA:
            self.skipTest('GPGPU-Sim runs are slow; set QPEL_NVIDIA=1 (check_all.py --nvidia)')
        from differential import gpgpusim
        if not gpgpusim.image_ready(build=False):
            unavailable(self, 'nvidia', f'no Docker image {gpgpusim.IMAGE}')

    def test_warp_sum(self):
        from differential import verify
        with tempfile.TemporaryDirectory() as d:
            original, port = Path(d)/'warp_sum.cu', Path(d)/'warp_sum.metal'
            original.write_text(WARP_SUM_CUDA)
            port.write_text(WARP_SUM_XOR)
            same = verify.verify(original, original, values={'n': 'volume'}, edges=False, nvidia=True)
            self.assertTrue(same['passed'], [c for c in same['cases'] if len(c) > 1])
            try:
                import torch
                if not torch.backends.mps.is_available():
                    return
            except ImportError:
                return
            ported = verify.verify(original, port, values={'n': 'volume'}, edges=False, nvidia=True, metal='preflight')
            self.assertTrue(ported['passed'], [c for c in ported['cases'] if len(c) > 1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
