"""gsplat's rasterizer gradient (RasterizeToPixels3DGSSerialBatchBwd.cu, `rasterize_to_pixels_3dgs_bwd_kernel`
for 3 channels, unmodified): each tile's gaussians back to front, the gradients of a pixel's colour and alpha with
respect to each gaussian's 2D mean, conic, colour and opacity, summed over the warp and added atomically by its
first lane. The kernel needs four new readings (frontends/warps.py, views.py, records.py):

- its warp, `cg::tiled_partition<32>(block)`, is qpel's subgroup: `cg::reduce` over it a subgroup reduction
  (`cg::greater` the maximum, gsplat's `warpSum` helpers a float sum in qpel's fixed order), `warp.any` a
  subgroup or, `warp.thread_rank()` the lane;
- its dynamic shared memory is carved at `block.size()` (`&id_batch[block_size]`), so each view is dynamic
  shared memory of its own, its length given at import (256 per view for the 16 x 16 tiles of the plan);
- `dim3 block_index(...)` is three unsigned locals;
- `vec3 *v_conics` added to through `(float *)(v_conics) + 3 * g` is an atomic buffer of floats.

Scenes are small ones of the forward rasterizer's kinds (differential/cases.py's gsplat_scene: partial tiles,
two images, a tile of more gaussians than one batch, an empty scene, opaque gaussians, backgrounds and masks),
their outputs from the original forward kernel, the output gradients random. The original runs on the CPU
simulator with real warps (the simulator's tiles reduce in the same xor butterfly), qpel's CUDA there and qpel's
Metal on the Apple GPU. Each gradient is a float sum (over a warp, then over the warps' atomic adds, whose order
the threads decide) of one term per pixel the gaussian touches, so each is compared within
BOUND * eps * PIXELS * its largest entry (MATH_ULPS times more on the Apple GPU), PIXELS = 256 the pixels of a
tile. Measured: every gradient within 0.011 of that unit on the CPU simulator, 0.044 on the Apple GPU."""
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

SOURCE = 'RasterizeToPixels3DGSSerialBatchBwd.cu'
KERNEL = 'rasterize_to_pixels_3dgs_bwd_kernel_3_f32'
SHARED = {'s': 2560, 'id_batch': 256, 'xy_opacity_batch': 256, 'conic_batch': 256, 'rgbs_batch': 768}
GRADIENTS = {'v_means2d': 2, 'v_conics': 3, 'v_colors': 3, 'v_opacities': 1, 'v_means2d_abs': 2}
PLANS = HERE/'corpus/gsplat/plans'
BOUND = 1
PIXELS = 256


class Built:
    def __init__(self, work):
        from differential import matrix
        from differential.simulate import Program
        from frontends import roundtrip
        from frontends.c_import import import_source
        from qpel import compile_program
        from qpel_ir import Lowering, validate
        source = next(s for s in matrix.sources() if s[0] == 'gsplat_rasterize_fwd' and s[1].suffix == '.cu')
        name, path, dialect, plan, include, grids, options = source
        (work/'fwd').mkdir()
        self.forward_plan = plan
        forward = matrix.Prepared(name, path, dialect, plan, include, options, work/'fwd')
        (work/'fwd/o').mkdir()
        self.forward = Program(path, plan, work/'fwd/o', original=forward.report, include=include, grids=grids)
        entry = next(e for e in roundtrip.corpus_entries() if e['source'].endswith(SOURCE))
        self.source = roundtrip.CORPUS/entry['source']
        include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
        plan = json.loads((PLANS/'rasterize_to_pixels_3dgs_bwd.plan.json').read_text())
        self.program, self.report = import_source(self.source, plan=plan, include=include, shared=SHARED,
                                                  defines=roundtrip.corpus_defines(entry), kernels=[KERNEL])
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.plan = Lowering(self.program).plan()
        self.generated = work/'build/package/generated'
        for folder in ('o', 'q'):
            (work/folder).mkdir()
        self.original = Program(self.source, self.plan, work/'o', original=self.report, include=include)
        self.ours = Program(self.generated/(self.plan['module'] + '.cu'), self.plan, work/'q')
        for program in (self.forward, self.original, self.ours):
            program.background = True  # (each block's 256 threads exchange through warps: efficiency cores)


# (images, width, height, gaussians, opaque, backgrounds, masks): small scenes of the forward rasterizer's kinds
# (the simulator runs each block's 256 threads together; larger scenes load the machine for minutes)
SCENES = ((1, 16, 16, 1, False, False, False), (1, 40, 24, 30, False, True, False), (2, 32, 16, 20, False, False, True),
          (1, 16, 16, 300, False, True, False), (1, 33, 17, 0, False, True, False), (1, 32, 32, 60, True, False, False))


def scenes(seed=7):
    """Forward rasterizer cases (differential/cases.py's scenes): partial tiles, two images, a tile of more
    gaussians than one batch of 256, an empty scene, nearly opaque gaussians, backgrounds and masks."""
    import numpy as np
    from differential import cases
    from differential.simulate import Case
    rng = np.random.default_rng(seed)
    out = []
    for images, width, height, count, opaque, background, masked in SCENES:
        inputs, values, shapes, tiles = cases.gsplat_scene(rng, images, width, height, max(count, 1), opaque=opaque,
                                                           away=count == 0)
        omit = []
        if background:
            inputs['backgrounds'] = rng.uniform(0, 1, images * 3).astype(np.float32)
        else:
            omit.append('backgrounds')
        if masked:
            inputs['masks'] = (rng.uniform(0, 1, tiles) < 0.8).astype(np.uint8)
        else:
            omit.append('masks')
        label = (f'{images}x{width}x{height}, {count or "1 off-screen"} gaussians, {values["n_isects"]} intersections'
                 f'{", opaque" if opaque else ""}{", background" if background else ""}{", masks" if masked else ""}')
        out.append(Case(shapes, inputs, values, label, omit=omit))
    return out


def backward_cases(built):
    """The scenes, the original forward rasterizer's alphas and last ids, random output gradients; the
    optional v_means2d_abs given in every other scene."""
    import numpy as np
    from differential.simulate import Case
    out = []
    for k, scene in enumerate(scenes()):
        rendered = built.forward.run(scene)
        rng = np.random.default_rng(3000 + k)
        pixels, gaussians = scene.shapes['pixels'][0], scene.shapes['gaussians'][0]
        inputs = {n: x for n, x in scene.inputs.items() if n != 'isect_offsets'}
        inputs |= {'tile_offsets': scene.inputs['isect_offsets'],
                   'render_alphas': np.asarray(rendered['render_alphas'], np.float32),
                   'last_ids': np.asarray(rendered['last_ids']).view(np.int32),
                   'v_render_colors': rng.standard_normal(pixels * 3).astype(np.float32),
                   'v_render_alphas': rng.standard_normal(pixels).astype(np.float32)}
        omit = list(scene.omit)
        for name, scale in GRADIENTS.items():
            inputs[name] = np.zeros(gaussians * scale, np.float32)
        if k % 2:
            omit.append('v_means2d_abs')
            del inputs['v_means2d_abs']
        values = dict(scene.values, tile_size=16)
        out.append(Case(dict(scene.shapes), inputs, values, scene.label + ('' if k % 2 else ', v_means2d_abs'), omit=omit))
    return out


def worst(want, got, case):
    """{gradient: its largest difference in units of eps * PIXELS * its largest entry}."""
    import numpy as np
    eps = float(np.finfo(np.float32).eps)
    out = {}
    for name in GRADIENTS:
        if name in case.omit:
            continue
        a, b = np.asarray(want[name], np.float64), np.asarray(got[name], np.float64)
        out[name] = float(np.abs(a - b).max()) / (eps * PIXELS * max(float(np.abs(a).max()), 1e-30))
    return out


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatRasterizeBackward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-raster-bwd-')
        cls.built = Built(Path(cls.tmp.name))
        cls.cases = backward_cases(cls.built)
        cls.want = {c.label: cls.built.original.run(c) for c in cls.cases}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        text = print_program(self.built.program)
        self.assertIn('qpel.subgroup_max(', text)
        self.assertIn('qpel.subgroup_or(', text)
        self.assertIn('v_conics: buffer<f32, atomic>', text)
        notes = self.built.report['adaptations']
        self.assertTrue(any('frontends/warps.py' in a for a in notes))
        self.assertTrue(any('became shared memory of their own' in a for a in notes))

    def test_the_original_uses_real_warps(self):
        self.assertTrue(self.built.original.threaded)

    def test_scenes_have_gradients(self):
        import numpy as np
        self.assertTrue(any(np.abs(self.want[c.label]['v_conics']).max() > 0 for c in self.cases))

    def test_gradient_cpu_simulator(self):
        for case in self.cases:
            with self.subTest(case=case.label):
                for name, units in worst(self.want[case.label], self.built.ours.run(case), case).items():
                    self.assertLessEqual(units, BOUND, f'{case.label} {name}: {units:.3g}')

    def test_gradient_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        from qpel_types import MATH_ULPS
        host = matrix.load_host(self.built.generated)
        for case in self.cases:
            with self.subTest(case=case.label):
                got = metal.run_generated(host, self.built.plan, case)
                for name, units in worst(self.want[case.label], got, case).items():
                    self.assertLessEqual(units, BOUND * MATH_ULPS, f'{case.label} {name}: {units:.3g}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
