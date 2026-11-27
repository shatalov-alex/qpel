"""gsplat's 2DGS rasterizer and its gradient (RasterizeToPixels2DGSSerialBatchFwd.cu and ...Bwd.cu,
`rasterize_to_pixels_2dgs_fwd_kernel` and `rasterize_to_pixels_2dgs_bwd_kernel` for 3 channels, unmodified): each
tile's splats front to back (forward) and back to front (gradient), each a flat disc hit by the pixel's ray through
its ray transform, with the colours, alphas, normals, median depth and distortion they render, and their gradients
summed over the warp and added atomically by its first lane. Beyond the 3DGS gradient's readings
(test_gsplat_rasterize_backward.py) the kernels need:

- a shared view carved from another of its own type, `float *normals_batch = &rgbs_batch[block_size * CDIM]`;
- more shared memory than qpel allowed before (five views of 256 vec3: 17 KiB on Metal, whose float3 takes 16
  bytes; qpel's limit is now 28 KiB);
- the gradient takes 25 buffers and 9 scalars, past Metal's 31 buffers, so its scalars travel in one buffer of
  32-bit words (qpel_types.packed_values).

Scenes are the 3DGS gradient's kinds (differential/cases.py's gsplat_scene), each gaussian's 2D covariance
A A^T read as a splat through A (its Cholesky factor) and the mean, with a slight perspective in the ray
transform; the distortion given in every other scene. The original runs on the CPU simulator with real warps,
qpel's CUDA there and qpel's Metal on the Apple GPU. The forward kernel: bit for bit on the CPU simulator; on the
Apple GPU the ids equal and the rest within FORWARD * MATH_ULPS eps of each output's largest entry. The gradient
is fed the original's forward outputs and compared as the 3DGS one is, within BOUND * eps * PIXELS * its largest
entry (MATH_ULPS times more on the Apple GPU). Measured: the forward kernel's outputs within 5.8 eps of their
largest entries on the Apple GPU; every gradient within 0.006 of its unit on the CPU simulator, 0.029 on the
Apple GPU."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_test_support import unavailable  # noqa: E402

PLANS = HERE/'corpus/gsplat/plans'
# gsplat's own block s, in ints: 256 ids and 4 x 256 vec3 (forward), and 2 x 768 floats more (gradient)
SHARED = {'s': 3328, 'id_batch': 256, 'xy_opacity_batch': 256, 'u_Ms_batch': 256, 'v_Ms_batch': 256, 'w_Ms_batch': 256}
EXTRA = {'s': 5632, 'rgbs_batch': 768, 'normals_batch': 768}
OUTPUTS = {'render_colors': 3, 'render_alphas': 1, 'render_normals': 3, 'render_distort': 1, 'render_median': 1}
IDS = ('last_ids', 'median_ids')
GRADIENTS = {'v_means2d': 2, 'v_ray_transforms': 9, 'v_colors': 3, 'v_opacities': 1, 'v_normals': 3, 'v_densify': 2,
             'v_means2d_abs': 2}
BOUND = 1
FORWARD = 4
PIXELS = 256
# (images, width, height, gaussians, opaque, backgrounds, masks), as the 3DGS gradient's scenes
SCENES = ((1, 16, 16, 1, False, False, False), (1, 40, 24, 30, False, True, False), (2, 32, 16, 20, False, False, True),
          (1, 16, 16, 300, False, True, False), (1, 33, 17, 0, False, True, False), (1, 32, 32, 60, True, False, False))


class Kernel:
    def __init__(self, work, name, source, shared):
        from frontends import roundtrip
        from frontends.c_import import import_source
        from differential.simulate import Program
        from qpel import compile_program
        from qpel_ir import Lowering, validate
        entry = next(e for e in roundtrip.corpus_entries() if e['source'].endswith(source))
        include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
        plan = json.loads((PLANS/f'rasterize_to_pixels_2dgs_{name}.plan.json').read_text())
        self.program, self.report = import_source(roundtrip.CORPUS/entry['source'], plan=plan, include=include,
                                                  shared=shared, defines=roundtrip.corpus_defines(entry),
                                                  kernels=[f'rasterize_to_pixels_2dgs_{name}_kernel_3_f32'])
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.plan = Lowering(self.program).plan()
        self.generated = work/'build/package/generated'
        for folder in ('o', 'q'):
            (work/folder).mkdir()
        self.original = Program(roundtrip.CORPUS/entry['source'], self.plan, work/'o', original=self.report, include=include)
        self.ours = Program(self.generated/(self.plan['module'] + '.cu'), self.plan, work/'q')
        for program in (self.original, self.ours):
            program.background = True  # (each block's 256 threads exchange through warps: efficiency cores)


def scenes(seed=11):
    """Forward cases: the 3DGS scenes' gaussians as splats (see the module's description)."""
    import numpy as np
    from differential import cases
    from differential.simulate import Case
    rng = np.random.default_rng(seed)
    out = []
    for k, (images, width, height, count, opaque, background, masked) in enumerate(SCENES):
        inputs, values, shapes, tiles = cases.gsplat_scene(rng, images, width, height, max(count, 1), opaque=opaque,
                                                           away=count == 0)
        total = shapes['gaussians'][0]
        a, b, c = inputs.pop('conics').astype(np.float64).reshape(-1, 3).T
        covariance = np.linalg.inv(np.stack([np.stack([a, b], 1), np.stack([b, c], 1)], 1))
        factor = np.linalg.cholesky(covariance)  # pixel = mean + factor @ (u, v)
        means = inputs['means2d'].astype(np.float64).reshape(-1, 2)
        transform = np.zeros((total, 3, 3))
        transform[:, :2, :2], transform[:, :2, 2], transform[:, 2, 2] = factor, means, 1
        transform[:, 2, :2] = rng.uniform(-1e-3, 1e-3, (total, 2))  # a slight perspective
        normals = rng.standard_normal((total, 3))
        inputs |= {'ray_transforms': transform.astype(np.float32).reshape(-1),
                   'normals': (normals / np.linalg.norm(normals, axis=1, keepdims=True)).astype(np.float32).reshape(-1),
                   'tile_offsets': inputs.pop('isect_offsets')}
        shapes = dict(shapes, tiles=(images, values['tile_height'], values['tile_width']))  # gsplat's grid {I, rows, columns}
        omit = []
        if background:
            inputs['backgrounds'] = rng.uniform(0, 1, images * 3).astype(np.float32)
        else:
            omit.append('backgrounds')
        if masked:
            inputs['masks'] = (rng.uniform(0, 1, tiles) < 0.8).astype(np.uint8)
        else:
            omit.append('masks')
        if k % 2:
            omit.append('render_distort')
        label = (f'{images}x{width}x{height}, {count or "1 off-screen"} splats, {values["n_isects"]} intersections'
                 f'{", opaque" if opaque else ""}{", background" if background else ""}{", masks" if masked else ""}'
                 f'{"" if k % 2 else ", distortion"}')
        values = {n: v for n, v in values.items() if n != 'block_offset'}  # (the 3DGS kernel's chunk offset)
        out.append(Case(shapes, inputs, dict(values, tile_size=16), label, omit=omit))
    return out


def backward_case(scene, rendered, k):
    """The scene, the original forward kernel's outputs, random output gradients (the distortion's where the
    forward rendered one); v_means2d_abs given in every other scene."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(4000 + k)
    pixels, gaussians = scene.shapes['pixels'][0], scene.shapes['gaussians'][0]
    inputs = dict(scene.inputs)
    inputs |= {'render_colors': np.asarray(rendered['render_colors'], np.float32),
               'render_alphas': np.asarray(rendered['render_alphas'], np.float32),
               'last_ids': np.asarray(rendered['last_ids']).view(np.int32),
               'median_ids': np.asarray(rendered['median_ids']).view(np.int32),
               'v_render_colors': rng.standard_normal(pixels * 3).astype(np.float32),
               'v_render_alphas': rng.standard_normal(pixels).astype(np.float32),
               'v_render_normals': rng.standard_normal(pixels * 3).astype(np.float32),
               'v_render_median': rng.standard_normal(pixels).astype(np.float32)}
    omit = [n for n in scene.omit if n != 'render_distort']
    if 'render_distort' in scene.omit:
        omit.append('v_render_distort')
    else:
        inputs['v_render_distort'] = rng.standard_normal(pixels).astype(np.float32)
    for name, scale in GRADIENTS.items():
        inputs[name] = np.zeros(gaussians * scale, np.float32)
    if k % 3 == 1:
        omit.append('v_means2d_abs')
        del inputs['v_means2d_abs']
    return Case(dict(scene.shapes), inputs, dict(scene.values), scene.label + ('' if k % 3 == 1 else ', v_means2d_abs'),
                omit=omit)


def worst(want, got, case, names, scale):
    """{name: its largest difference in units of eps * scale * its largest entry}."""
    import numpy as np
    eps = float(np.finfo(np.float32).eps)
    out = {}
    for name in names:
        if name in case.omit:
            continue
        a, b = np.asarray(want[name], np.float64), np.asarray(got[name], np.float64)
        out[name] = float(np.abs(a - b).max()) / (eps * scale * max(float(np.abs(a).max()), 1e-30))
    return out


def apple_gpu(test):
    try:
        import torch
        if not torch.backends.mps.is_available():
            unavailable(test, 'gpu', 'no Apple GPU (PyTorch MPS)')
    except ImportError:
        unavailable(test, 'gpu', 'PyTorch required for the Apple GPU tests')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatRasterize2DGS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-raster-2dgs-')
        work = Path(cls.tmp.name)
        for name in ('fwd', 'bwd'):
            (work/name).mkdir()
        cls.forward = Kernel(work/'fwd', 'fwd', 'RasterizeToPixels2DGSSerialBatchFwd.cu', SHARED)
        cls.backward = Kernel(work/'bwd', 'bwd', 'RasterizeToPixels2DGSSerialBatchBwd.cu', SHARED | EXTRA)
        cls.scenes = scenes()
        cls.rendered = {s.label: cls.forward.original.run(s) for s in cls.scenes}
        cls.cases = [backward_case(s, cls.rendered[s.label], k) for k, s in enumerate(cls.scenes)]
        cls.want = {c.label: cls.backward.original.run(c) for c in cls.cases}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        self.assertIn('qpel.subgroup_max(', print_program(self.backward.program))
        for kernel in (self.forward, self.backward):
            self.assertTrue(any('became shared memory of their own' in a for a in kernel.report['adaptations']))
        import re
        used = re.compile(r'QPEL_VALUES_PARAM\(\d')  # (the backend header defines the macro in both)
        self.assertRegex((self.backward.generated/(self.backward.plan['module'] + '.cu')).read_text(), used)  # scalars packed
        self.assertNotRegex((self.forward.generated/(self.forward.plan['module'] + '.cu')).read_text(), used)

    def test_scenes_render_and_have_gradients(self):
        import numpy as np
        self.assertTrue(any(np.asarray(self.rendered[s.label]['render_alphas']).max() > 0.5 for s in self.scenes))
        self.assertTrue(any(np.abs(self.want[c.label]['v_ray_transforms']).max() > 0 for c in self.cases))

    def test_forward_cpu_simulator(self):
        import numpy as np
        for scene in self.scenes:
            with self.subTest(case=scene.label):
                got, want = self.forward.ours.run(scene), self.rendered[scene.label]
                for name in list(OUTPUTS) + list(IDS):
                    if name not in scene.omit:
                        self.assertTrue(np.array_equal(np.asarray(got[name]).view(np.uint32),
                                                       np.asarray(want[name]).view(np.uint32)), f'{scene.label} {name}')

    def test_forward_apple_gpu(self):
        apple_gpu(self)
        import numpy as np
        from differential import matrix, metal
        from qpel_types import MATH_ULPS
        host = matrix.load_host(self.forward.generated)
        for scene in self.scenes:
            with self.subTest(case=scene.label):
                got, want = metal.run_generated(host, self.forward.plan, scene), self.rendered[scene.label]
                for name in IDS:
                    self.assertTrue(np.array_equal(np.asarray(got[name]).view(np.int32),
                                                   np.asarray(want[name]).view(np.int32)), f'{scene.label} {name}')
                for name, units in worst(want, got, scene, OUTPUTS, 1).items():
                    self.assertLessEqual(units, FORWARD * MATH_ULPS, f'{scene.label} {name}: {units:.3g}')

    def test_gradient_cpu_simulator(self):
        for case in self.cases:
            with self.subTest(case=case.label):
                for name, units in worst(self.want[case.label], self.backward.ours.run(case), case, GRADIENTS, PIXELS).items():
                    self.assertLessEqual(units, BOUND, f'{case.label} {name}: {units:.3g}')

    def test_gradient_apple_gpu(self):
        apple_gpu(self)
        from differential import matrix, metal
        from qpel_types import MATH_ULPS
        host = matrix.load_host(self.backward.generated)
        for case in self.cases:
            with self.subTest(case=case.label):
                got = metal.run_generated(host, self.backward.plan, case)
                for name, units in worst(self.want[case.label], got, case, GRADIENTS, PIXELS).items():
                    self.assertLessEqual(units, BOUND * MATH_ULPS, f'{case.label} {name}: {units:.3g}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
