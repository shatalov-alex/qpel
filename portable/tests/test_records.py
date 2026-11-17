"""What gsplat's rasterizer needed from the CUDA importer (frontends/records.py and c_import.py):

- glm's vectors (`using vec3 = glm::vec<3, float>`) as qpel vectors: their components through glm's
  anonymous unions, constructors of N scalars, buffers of them as buffers of their scalars;
- plain struct values taken apart into their fields: a function returning `Weight {vis, alpha, valid}`
  returns them through reference parameters, a local `Weight w` is three locals;
- a 2D local array `float acc[P][C]` as one of P * C elements;
- a pointer parameter moved with `p += n` as the parameter and an offset (an optional buffer stays
  optional: `if (masks != nullptr) masks += n` still tests the argument);
- `__builtin_ctz` of a constant, folded; `__expf` as qpel's exp, recorded as an adaptation.

A kernel using all of them runs against the original on the CPU simulator, bit for bit (both sides'
__expf and exp are the host's expf there)."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import print_program  # noqa: E402

GLM = HERE/'corpus/gsplat/gsplat/cuda/csrc/third_party/glm'
SOURCE = r'''
#include <cstdint>
#include <glm/glm.hpp>
using vec2 = glm::vec<2, float>;
using vec3 = glm::vec<3, float>;
struct Weight {
  float vis;
  float alpha;
  bool valid;
};
__device__ Weight weigh(const vec3 &c, const float dx, const float dy) {
  const float s = 0.5f * (c.x * dx * dx + c.z * dy * dy) + c.y * dx * dy;
  Weight w;
  w.vis = __expf(-s);
  w.alpha = fminf(0.99f, w.vis);
  w.valid = !(s < 0.f);
  return w;
}
template <uint32_t C, uint32_t TILE>
__global__ void blend(const vec2 *__restrict__ means, const vec3 *__restrict__ conics, const float *__restrict__ colors,
                      const bool *__restrict__ masks, float *__restrict__ out, const uint32_t first, const uint32_t count,
                      const uint32_t n) {
  constexpr uint32_t SHIFT = __builtin_ctz(TILE);
  const uint32_t i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i >= n) return;
  colors += first * C;
  if (masks != nullptr) {
    masks += first;
  }
  const float px = (float)(i >> SHIFT) + 0.5f, py = (float)(i & (TILE - 1)) + 0.5f;
  float acc[2][C] = {0.f};
  for (uint32_t g = 0; g < count; ++g) {
    if (masks != nullptr && !masks[g]) continue;
    const vec2 xy = means[first + g];
    const vec3 conic = conics[first + g];
    const Weight w = weigh(conic, xy.x - px, xy.y - py);
    if (!w.valid) continue;
    const vec3 scaled = {conic.x * w.alpha, conic.y, w.vis};
    for (uint32_t k = 0; k < C; ++k) {
      acc[g & 1][k] += colors[g * C + k] * w.alpha + scaled.x * 0.25f;
    }
  }
  for (uint32_t k = 0; k < C; ++k) {
    out[i * C + k] = acc[0][k] + acc[1][k];
  }
}
template __global__ void blend<3, 16>(const vec2 *, const vec3 *, const float *, const bool *, float *, uint32_t, uint32_t, uint32_t);
'''
KERNEL = 'blend'  # (one instantiation keeps the template's name)


def resource(name, kind, type_, extent=None, scale=None, optional=False):
    r = {'name': name, 'kind': kind, 'type': type_, 'extent': extent}
    r.update({'scale': scale} if scale else {}, **({'optional': True} if optional else {}))
    return r


PLAN = {'schema': 2, 'module': 'records', 'resources': [
    resource('pixels', 'shape', 'shape3'), resource('gaussians', 'shape', 'shape3'),
    resource('means', 'input', 'buffer<f32, read>', 'gaussians', 2), resource('conics', 'input', 'buffer<f32, read>', 'gaussians', 3),
    resource('colors', 'input', 'buffer<f32, read>', 'gaussians', 3), resource('masks', 'input', 'buffer<u8, read>', 'gaussians', optional=True),
    resource('out', 'output', 'buffer<f32, write>', 'pixels', 3), resource('first', 'value', 'u32'), resource('count', 'value', 'u32')],
    'stages': [{'kernel': KERNEL, 'grid': 'pixels', 'blockSize': 64, 'completionBarrier': True, 'bindings': [
        {'parameter': p, 'resource': p} for p in ('means', 'conics', 'colors', 'masks', 'out', 'first', 'count')] +
        [{'parameter': 'n', 'resource': 'pixels'}]}]}


@unittest.skipUnless(shutil.which('clang') and GLM.is_dir(), 'clang and the vendored glm (corpus/gsplat) required')
class Records(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends.c_import import import_source
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-records-')
        (Path(cls.work.name)/'src').mkdir()
        cls.path = Path(cls.work.name)/'src/k.cu'
        cls.path.write_text(SOURCE)
        cls.glm = Path(cls.work.name)/'src/glm'  # beside the source: the simulator copies their common folder
        shutil.copytree(GLM/'glm', cls.glm/'glm')
        cls.program, cls.report = import_source(cls.path, kernels=[KERNEL], module='records', include=[cls.glm], plan=PLAN,
                                                defines=['__CUDACC__', 'CUDA_VERSION=12060'])

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_imported(self):
        validate(self.program)
        text = print_program(self.program)
        self.assertIn('ref result_vis: f32, ref result_alpha: f32, ref result_valid: bool', text)
        self.assertIn('weigh(conic, ', text)
        self.assertIn('w_alpha', text)
        self.assertIn('colors_offset', text)
        self.assertIn('qpel.present(masks)', text)
        self.assertIn('array<f32, 6>', text)
        self.assertIn('math.exp(', text)
        self.assertIn('let SHIFT: u32 = cast<u32>(4);', text)  # __builtin_ctz(16), folded
        self.assertTrue(any('__expf' in a for a in self.report['adaptations']), self.report['adaptations'])

    def test_against_the_original(self):
        import numpy as np
        from qpel import compile_program
        from differential.simulate import Program, Case
        work = Path(self.work.name)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        generated = work/'build/package/generated'
        plan = json.loads((generated/'pipeline.json').read_text())
        (work/'o').mkdir()
        (work/'q').mkdir()
        original = Program(self.path, plan, work/'o', original=self.report, include=[self.glm])
        ours = Program(generated/'records.cu', plan, work/'q')
        rng = np.random.default_rng(17)
        for n, total, first, count, masked in ((1, 1, 0, 1, False), (256, 40, 5, 30, True), (300, 64, 0, 64, False)):
            inputs = {'means': rng.uniform(0, 16, total * 2).astype(np.float32),
                      'conics': np.tile(np.array([0.2, 0.05, 0.3], np.float32), total) * rng.uniform(0.5, 2, total * 3).astype(np.float32),
                      'colors': rng.uniform(0, 1, total * 3).astype(np.float32)}
            if masked:
                inputs['masks'] = (rng.uniform(0, 1, total) < 0.7).astype(np.uint8)
            case = Case({'pixels': (n, 1, 1), 'gaussians': (total, 1, 1)}, inputs, {'first': first, 'count': count},
                        f'n={n} first={first} count={count}', omit=() if masked else ('masks',))
            want, got = original.run(case), ours.run(case)
            np.testing.assert_array_equal(got['out'][:n * 3].view(np.uint32), want['out'][:n * 3].view(np.uint32), err_msg=case.label)


if __name__ == '__main__':
    unittest.main()
