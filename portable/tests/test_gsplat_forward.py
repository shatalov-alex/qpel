"""gsplat's whole forward pass, from 3D gaussians to pixels, every kernel gsplat's own and unmodified: the EWA
projection into each camera (ProjectionEWA3DGSFused.cu), the colours from spherical harmonics of degree 3 seen
from each camera (SphericalHarmonicsCUDA.cu), the tile intersections (IntersectTile.cu: count, torch's cumsum,
emit, CUB's radix sort, offsets) and the rasterizer (RasterizeToPixels3DGSFwd.cu). Between the kernels, the host
steps gsplat's Python does: SH colours + 0.5 clamped at 0, the gaussians a camera culled masked out of the SH
pass, the total intersection count read back.

The original chain runs on the CPU simulator, kernel after kernel, each fed by the one before; qpel's CUDA chain
there gives every intermediate buffer and every pixel bit for bit. qpel's Metal chain on the Apple GPU, fed only by
its own results: the projection and the intersections bit for bit (a pinhole camera, no opacity-aware radius: no
bounded function decides them), the image within the rasterizer's tolerance and the SH colours' bound."""
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

TILE = 16
SCENES = (('1 batch, 2 cameras 64x48, 150 gaussians', 1, 2, 150, 64, 48),
          ('2 batches, 1 camera 40x32, 60 gaussians', 2, 1, 60, 40, 32))


def world(seed, batches, cameras, n, width, height):
    """Gaussians in front of every camera; DC colours of either sign, smaller higher bands (as trained scenes have)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    z = rng.uniform(2, 6, (batches, n))
    xy = rng.uniform(-0.45, 0.45, (batches, n, 2)) * z[..., None]
    means = np.concatenate([xy, z[..., None]], -1)
    views, ks = [], []
    for b in range(batches):
        for c in range(cameras):
            angle = 0.08 * c
            view = np.eye(4)
            view[:3, :3] = [[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]]
            view[:3, 3] = [0.15 * c, -0.05 * c, 0.2 * c]
            views.append(view)
            f = 0.9 * width
            ks.append([[f, 0, width / 2 + c], [0, f, height / 2 - c], [0, 0, 1]])
    coeffs = np.concatenate([rng.standard_normal((n, 1, 3)) * 0.8, rng.standard_normal((n, 15, 3)) * 0.1], 1)
    return {'means': means.astype(np.float32).reshape(-1), 'quats': rng.standard_normal((batches, n, 4)).astype(np.float32).reshape(-1),
            'scales': np.exp(rng.uniform(-3.5, -2, (batches, n, 3))).astype(np.float32).reshape(-1),
            'opacities': rng.uniform(0.3, 0.95, (batches, n)).astype(np.float32),
            'viewmats': np.array(views, np.float32).reshape(-1), 'Ks': np.array(ks, np.float32).reshape(-1),
            'coeffs': coeffs.astype(np.float32).reshape(-1)}


class Chain:
    """The five programs, built once: each stage's original and qpel's CUDA on the CPU simulator, its Metal host."""

    def __init__(self, work):
        from differential import matrix
        from differential.simulate import Program
        from test_gsplat_projection import Built as Projection
        from test_gsplat_spherical_harmonics import Built as Harmonics
        from test_gsplat_intersect import Pipeline
        for name in ('projection', 'harmonics', 'count', 'emit', 'raster'):
            (work/name).mkdir()
        self.projection = Projection('projection_ewa_fwd', work/'projection')
        self.harmonics = Harmonics('f32', 3, work/'harmonics')
        self.count = Pipeline('intersect_tile_count', work/'count')
        self.emit = Pipeline('intersect_tile_emit', work/'emit')
        source = next(s for s in matrix.sources() if s[0] == 'gsplat_rasterize_fwd' and s[1].suffix == '.cu')
        name, path, dialect, plan, include, grids, options = source
        self.raster = matrix.Prepared(name, path, dialect, plan, include, options, work/'raster')
        for folder in ('o', 'q'):
            (work/'raster'/folder).mkdir()
        self.raster_original = Program(path, plan, work/'raster/o', original=self.raster.report, include=include, grids=grids)
        self.raster_ours = Program(self.raster.cuda, plan, work/'raster/q')

    def run(self, scene, side, metal=None, record=None):
        """Every stage of one scene on one side ('original', 'cuda' or 'metal'); record gets each stage's outputs."""
        import numpy as np
        from differential.simulate import Case
        from test_gsplat_projection import PINHOLE
        label, batches, cameras, n, width, height = scene
        inputs = world(SCENES.index(scene), batches, cameras, n, width, height)
        images = batches * cameras
        record = {} if record is None else record

        def stage(name, built, case, program=None):
            original, ours = program or (built.original, built.ours)
            if side == 'original':
                out = original.run(case)
            elif side == 'cuda':
                out = ours.run(case)
            else:
                out = metal.run_generated(metal_hosts(built), built.plan, case)
            record[name] = {k: np.asarray(v) for k, v in out.items()}
            return out

        def metal_hosts(built):
            from differential import matrix
            key = id(built)
            if key not in self.__dict__.setdefault('_hosts', {}):
                self._hosts[key] = matrix.load_host(built.generated)
            return self._hosts[key]

        # 1. projection: radii, means2d, depths, conics per (batch, camera, gaussian)
        values = {'gaussian_offset': 0, 'gaussian_count': n, 'B': batches, 'C': cameras, 'N': n, 'image_width': width,
                  'image_height': height, 'eps2d': 0.3, 'near_plane': 0.01, 'far_plane': 100.0, 'radius_clip': 0.0,
                  'camera_model': PINHOLE}
        shapes = {'elements': (images * n, 1, 1), 'gaussians': (batches * n, 1, 1), 'cameras': (images, 1, 1)}
        projected = stage('projection', self.projection, Case(shapes, dict(inputs, opacities=inputs['opacities'].reshape(-1)), values,
                                                              label, omit=['covars', 'opacities', 'compensations']))
        radii = np.asarray(projected['radii']).view(np.int32)[:2 * images * n]
        visible = radii.reshape(-1, 2).any(1)
        # 2. colours: SH of degree 3 for the visible (batch, camera, gaussian), + 0.5 clamped at 0 (gsplat's Python)
        e = images * n
        sh = {'means': inputs['means'], 'viewmats': inputs['viewmats'], 'coeffs': inputs['coeffs'], 'masks': visible.astype(np.uint8),
              'batch_ids': np.zeros(e, np.int64), 'camera_ids': np.zeros(e, np.int64), 'gaussian_ids': np.zeros(e, np.int64)}
        coloured = stage('harmonics', self.harmonics, Case({'E': (e, 1, 1), 'points': (batches * n, 1, 1), 'cameras': (images, 1, 1),
                                                            'rows': (n, 1, 1)}, sh, {'B': batches, 'C': cameras, 'N': n}, label,
                                                           omit=['batch_ids', 'camera_offsets']))
        colors = np.asarray(coloured['colors'], np.float32).reshape(-1)[:3 * e].reshape(-1, 3)
        colors = np.where(visible[:, None], np.maximum(colors + np.float32(0.5), np.float32(0)), np.float32(0)).astype(np.float32)
        # 3. intersections: count, cumsum, emit, sort, offsets
        tw, th = (width + TILE - 1) // TILE, (height + TILE - 1) // TILE
        opacities = np.repeat(inputs['opacities'], cameras, 0).reshape(-1)  # [B, C, N]
        means2d = np.asarray(projected['means2d'], np.float32).reshape(-1)[:2 * e]
        conics = np.asarray(projected['conics'], np.float32).reshape(-1)[:3 * e]
        depths = np.asarray(projected['depths'], np.float32).reshape(-1)[:e]
        data = {'image_ids': np.zeros(e, np.int64), 'gaussian_ids': np.zeros(e, np.int64), 'means2d': means2d,
                'radii': radii, 'depths': depths, 'conics': conics, 'opacities': opacities}
        settings = {'offset': 0, 'packed': 0, 'I': images, 'N': n, 'nnz': 0, 'tile_size': TILE, 'tile_width': tw, 'tile_height': th,
                    'tile_n_bits': (tw * th).bit_length(), 'image_n_bits': images.bit_length(), 'key_start': 0, 'key_end': images * tw * th}
        shapes = {'gaussians': (e, 1, 1), 'tiles': (images * tw * th, 1, 1)}
        counted = stage('count', self.count, Case(dict(shapes), dict(data), dict(settings), label, omit=['cum_tiles_per_gauss', 'tile_mask']))
        cum = np.asarray(counted['cumsum']).view(np.int64)[:e]
        total = int(cum[-1])
        assert total > 0, label
        values = dict(settings, begin_bit=0, end_bit=32 + settings['tile_n_bits'] + settings['image_n_bits'], n_isects=total,
                      n_tiles=tw * th)
        emitted = stage('emit', self.emit, Case(dict(shapes, isects=(total, 1, 1)), dict(data, cum_tiles_per_gauss=cum), values, label,
                                               omit=['tile_mask']))
        offsets = np.asarray(emitted['offsets']).view(np.int64)[:images * tw * th]
        ids = np.asarray(emitted['flatten_ids_sorted']).view(np.int32)[:total]
        # 4. rasterize
        case = Case({'gaussians': (e, 1, 1), 'images': (images, 1, 1), 'tiles': (tw, th, images), 'isects': (total, 1, 1),
                     'pixels': (width * height * images, 1, 1)},
                    {'means2d': means2d, 'conics': conics, 'colors': colors.reshape(-1), 'opacities': opacities,
                     'isect_offsets': offsets, 'flatten_ids': ids},
                    {'N': n, 'n_isects': total, 'packed': 0, 'image_width': width, 'image_height': height, 'I': images,
                     'tile_width': tw, 'tile_height': th, 'block_offset': 0}, label, omit=['backgrounds', 'masks'])
        stage('raster', self.raster, case, program=(self.raster_original, self.raster_ours))
        return record, case


EXACT = {'projection': ('radii', 'means2d', 'depths', 'conics'), 'count': ('tiles_per_gauss', 'cumsum'),
         'emit': ('isect_ids_sorted', 'flatten_ids_sorted', 'offsets')}


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatForward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-forward-')
        cls.chain = Chain(Path(cls.tmp.name))
        cls.original = {}
        for scene in SCENES:
            record = {}
            cls.original[scene] = (record, cls.chain.run(scene, 'original', record=record)[1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def same(self, got, want, stage, names, label):
        import numpy as np
        for name in names:
            with self.subTest(label=label, stage=stage, output=name):
                a, b = np.asarray(want[stage][name]).reshape(-1), np.asarray(got[stage][name]).reshape(-1)[:len(np.asarray(want[stage][name]).reshape(-1))]
                np.testing.assert_array_equal(b.view(np.uint8), a.view(np.uint8))

    def test_cpu_simulator(self):
        """qpel's CUDA chain: every stage's outputs and the image bit for bit."""
        for scene in SCENES:
            want, _ = self.original[scene]
            got = {}
            self.chain.run(scene, 'cuda', record=got)
            for stage, names in dict(EXACT, harmonics=('colors',), raster=('render_colors', 'render_alphas', 'last_ids')).items():
                if stage == 'harmonics':
                    import numpy as np
                    visible = want['projection']['radii'].view(np.int32).reshape(-1)[:2 * len(want['harmonics']['colors'].reshape(-1)) // 3]
                    keep = np.repeat(visible.reshape(-1, 2).any(1), 3)
                    np.testing.assert_array_equal(got[stage]['colors'].reshape(-1)[:len(keep)][keep].view(np.uint32),
                                                  want[stage]['colors'].reshape(-1)[:len(keep)][keep].view(np.uint32))
                    continue
                self.same(got, want, stage, names, scene[0])

    def test_apple_gpu(self):
        """qpel's Metal chain, fed only by itself: projection and intersections bit for bit, the image within bounds."""
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import metal
        for scene in SCENES:
            want, case = self.original[scene]
            got = {}
            self.chain.run(scene, 'metal', metal=metal, record=got)
            for stage, names in EXACT.items():
                self.same(got, want, stage, names, scene[0])
            with self.subTest(label=scene[0], stage='raster'):
                self.assertIsNone(self.chain.raster.compare(want['raster'], got['raster'], case))


if __name__ == '__main__':
    unittest.main(verbosity=2)
