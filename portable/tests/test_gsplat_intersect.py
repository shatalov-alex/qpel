"""Phase 7 pilot: gsplat's forward tile intersection, ported whole (LIBRARIES.md, device-wide CUB).

gsplat's host code (Intersect.cpp, not vendored) counts each projected gaussian's tiles with
intersect_tile_kernel, takes their cumulative sum (torch::cumsum, int32 into int64), reads the total
back, writes each intersection's 64-bit key (image | tile | depth) and gaussian id with a second
intersect_tile_kernel pass, sorts them (cub::DeviceRadixSort::SortPairs over 32 + tile and image bits)
and finds each tile's first intersection with intersect_offset_kernel. Here that is two qpel pipelines
(corpus/gsplat/plans/intersect_tile_count and _emit), their scan and sort library stages expanded by
frontends/library.py; the kernels are gsplat's own, unmodified.

The original (gsplat's CUDA with qpel's library kernels for torch's cumsum and CUB's sort) runs on the
CPU simulator, qpel's CUDA there and on GPGPU-Sim (QPEL_NVIDIA=1), qpel's Metal on the Apple GPU: every
output agrees bit for bit, and with numpy's cumsum, sortedness and offsets. The intersections then feed
gsplat's rasterizer (the gsplat_rasterize_fwd corpus program), so on the Apple GPU gsplat's forward pass
runs from projected gaussians to pixels."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_test_support import unavailable  # noqa: E402

NVIDIA = os.environ.get('QPEL_NVIDIA') == '1'
PLANS = HERE/'corpus/gsplat/plans'
TILE = 16


def scenes():
    """(label, images, width, height, gaussians per image, masked, off screen) of the cases."""
    return (('2 images 96x64, 200 gaussians', 2, 96, 64, 200, False, False),
            ('1 image 40x24, 60 gaussians, a tile mask', 1, 40, 24, 60, True, False),
            ('3 images 33x17, 25 gaussians', 3, 33, 17, 25, False, False),
            ('1 image 48x32, nothing on screen', 1, 48, 32, 30, False, True))


def scene(seed, images, width, height, count, masked, away):
    """Inputs and values of the counting pass: gsplat_scene's gaussians (differential/cases.py) with the
    radii and depths gsplat's projection gives them (radius = ceil(3 sigma) per axis)."""
    import numpy as np
    from differential.cases import gsplat_scene
    rng = np.random.default_rng(seed)
    inputs, values, _, _ = gsplat_scene(rng, images, width, height, count, tile=TILE, away=away)
    total = images * count
    conics = inputs['conics'].reshape(total, 3).astype(np.float64)
    det = conics[:, 0] * conics[:, 2] - conics[:, 1] ** 2
    cov = np.stack([conics[:, 2] / det, conics[:, 0] / det], 1)  # the covariance's diagonal
    radii = np.ceil(3 * np.sqrt(cov)).astype(np.int32)
    radii[rng.random(total) < 0.1] = 0  # culled by the projection
    tw, th = (width + TILE - 1) // TILE, (height + TILE - 1) // TILE
    n_tiles = tw * th
    shapes = {'gaussians': (total, 1, 1), 'tiles': (images * n_tiles, 1, 1)}
    data = {'image_ids': np.zeros(total, np.int64), 'gaussian_ids': np.zeros(total, np.int64),
            'means2d': inputs['means2d'], 'radii': radii.reshape(-1), 'depths': rng.uniform(0.1, 10, total).astype(np.float32),
            'conics': inputs['conics'], 'opacities': inputs['opacities']}
    if masked:
        data['tile_mask'] = (rng.random(images * n_tiles) < 0.7).astype(np.uint8)
    settings = {'offset': 0, 'packed': 0, 'I': images, 'N': count, 'nnz': 0, 'tile_size': TILE, 'tile_width': tw,
                'tile_height': th, 'tile_n_bits': n_tiles.bit_length(), 'image_n_bits': images.bit_length(), 'key_start': 0,
                'key_end': images * n_tiles}
    return shapes, data, settings, inputs


class Pipeline:
    """One plan of the pilot, its library stages expanded, imported and built."""

    def __init__(self, name, work):
        from frontends import roundtrip
        from frontends.c_import import import_source
        from frontends.library import expand
        from qpel import compile_program
        from qpel_ir import Lowering, validate
        from differential.simulate import Program
        entry = next(e for e in roundtrip.corpus_entries() if e['source'].endswith('IntersectTile.cu'))
        source = roundtrip.CORPUS/entry['source']
        include = [roundtrip.CORPUS/f for f in entry.get('include', ())] + [source.parent]
        text, plan = expand(source.read_text(), json.loads((PLANS/f'{name}.plan.json').read_text()))
        self.work = work
        self.source = work/'IntersectTile.cu'  # gsplat's source with qpel's library kernels appended
        self.source.write_text(text)
        self.program, report = import_source(self.source, plan=plan, include=include, defines=roundtrip.corpus_defines(entry),
                                             kernels=sorted({s['kernel'] for s in plan['stages']}))
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.plan = Lowering(self.program).plan()
        self.generated = work/'build/package/generated'
        self.cuda = self.generated/(self.plan['module'] + '.cu')
        for folder in ('o', 'q'):
            (work/folder).mkdir()
        self.original = Program(self.source, self.plan, work/'o', original=report, include=include, located=source.parent)
        self.ours = Program(self.cuda, self.plan, work/'q')
        self.outputs = [r['name'] for r in self.plan['resources'] if r['kind'] == 'output']


def cases(count, emit):
    """[(label, counting case, emitting case or None, tiles)] for every scene, the original's counts deciding
    the emitting pass's size (as gsplat's host reads the cumulative sum's last element back)."""
    from differential.simulate import Case
    out = []
    for seed, (label, images, width, height, n, masked, away) in enumerate(scenes()):
        shapes, data, settings, _ = scene(seed, images, width, height, n, masked, away)
        first = Case(dict(shapes), dict(data), dict(settings), label, omit=['cum_tiles_per_gauss'] + ([] if masked else ['tile_mask']))
        cum = count.original.run(first)['cumsum'].view('int64')
        total = int(cum[-1])
        second = None
        if total:  # (gsplat returns without the second pass when nothing intersects)
            values = dict(settings, begin_bit=0, end_bit=32 + settings['tile_n_bits'] + settings['image_n_bits'], n_isects=total,
                          n_tiles=settings['tile_width'] * settings['tile_height'])
            second = Case(dict(shapes, isects=(total, 1, 1)), dict(data, cum_tiles_per_gauss=cum), values, label,
                          omit=[] if masked else ['tile_mask'])
        out.append((label, first, second, images * settings['tile_width'] * settings['tile_height']))
    return out


def check_intersections(test, out, case, tiles):
    """numpy's view of the emitting pass: keys sorted, offsets at each tile's first intersection."""
    import numpy as np
    keys = np.asarray(out['isect_ids_sorted']).view(np.int64)
    test.assertTrue(np.all(np.diff(keys) >= 0))
    bits = case.values['tile_n_bits']
    t = keys >> 32
    tile_ids = (t >> bits) * case.values['n_tiles'] + (t & ((1 << bits) - 1))
    np.testing.assert_array_equal(np.asarray(out['offsets']).view(np.int64), np.searchsorted(tile_ids, np.arange(tiles)))


def same(test, got, want, names, label):
    import numpy as np
    for name in names:
        with test.subTest(label=label, output=name):
            np.testing.assert_array_equal(np.asarray(got[name]).reshape(-1).view(want[name].dtype), want[name].reshape(-1))


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatIntersect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-intersect-')
        root = Path(cls.tmp.name)
        cls.pipelines = {}
        for name in ('intersect_tile_count', 'intersect_tile_emit'):
            (root/name).mkdir()
            cls.pipelines[name] = Pipeline(name, root/name)
        cls.cases = cases(cls.pipelines['intersect_tile_count'], cls.pipelines['intersect_tile_emit'])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_plans(self):
        count, emit = self.pipelines['intersect_tile_count'], self.pipelines['intersect_tile_emit']
        self.assertEqual(count.program['version'], 21)
        self.assertEqual(len(count.plan['stages']), 1 + 3)  # the counting pass, the scan
        self.assertEqual(len(emit.plan['stages']), 1 + 8 * 6 + 1)  # the emitting pass, 8 sort passes, the offsets
        self.assertGreater(sum(1 for _, _, second, _ in self.cases if second), 2)
        self.assertTrue(any(second is None for _, _, second, _ in self.cases))  # nothing on screen

    def test_cpu_simulator(self):
        import numpy as np
        count, emit = self.pipelines['intersect_tile_count'], self.pipelines['intersect_tile_emit']
        for label, first, second, tiles in self.cases:
            want = count.original.run(first)
            same(self, count.ours.run(first), want, count.outputs, label)
            counts = want['tiles_per_gauss'].view(np.int32).astype(np.int64)
            np.testing.assert_array_equal(want['cumsum'].view(np.int64), np.cumsum(counts))
            if second is not None:
                want = emit.original.run(second)
                check_intersections(self, want, second, tiles)
                same(self, emit.ours.run(second), want, emit.outputs, label)

    def apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        return matrix, metal

    def test_apple_gpu(self):
        matrix, metal = self.apple_gpu()
        for pipeline, index in ((self.pipelines['intersect_tile_count'], 1), (self.pipelines['intersect_tile_emit'], 2)):
            host = matrix.load_host(pipeline.generated)
            for row in self.cases:
                case = row[index]
                if case is not None:
                    same(self, metal.run_generated(host, pipeline.plan, case), pipeline.original.run(case), pipeline.outputs, row[0])

    def test_rasterized_on_the_apple_gpu(self):
        """Projected gaussians to pixels on the Apple GPU: qpel's Metal intersections into qpel's Metal of
        gsplat's rasterizer, against the original's intersections into the original rasterizer (CPU simulator)."""
        import numpy as np
        matrix, metal = self.apple_gpu()
        from differential.simulate import Case
        source = next(s for s in matrix.sources() if s[0] == 'gsplat_rasterize_fwd' and s[1].suffix == '.cu')
        name, path, dialect, plan, include, grids, options = source
        work = Path(self.tmp.name)/'rasterize'
        work.mkdir()
        raster = matrix.Prepared(name, path, dialect, plan, include, options, work)
        (work/'o').mkdir()
        from differential.simulate import Program
        original = Program(path, plan, work/'o', original=raster.report, include=include, grids=grids)
        host = matrix.load_host(raster.generated)
        emit = self.pipelines['intersect_tile_emit']
        emit_host = matrix.load_host(emit.generated)
        for seed, (label, images, width, height, n, masked, away) in enumerate(scenes()):
            row = self.cases[seed]
            if row[2] is None:
                continue
            _, _, _, raw = scene(seed, images, width, height, n, masked, away)
            ours = metal.run_generated(emit_host, emit.plan, row[2])
            theirs = emit.original.run(row[2])
            tw, th = row[2].values['tile_width'], row[2].values['tile_height']
            for side, run in (('original', theirs), ('qpel', ours)):
                offsets = np.asarray(run['offsets']).view(np.int64)
                ids = np.asarray(run['flatten_ids_sorted']).view(np.int32)
                inputs = {'means2d': raw['means2d'], 'conics': raw['conics'], 'colors': raw['colors'], 'opacities': raw['opacities'],
                          'isect_offsets': offsets, 'flatten_ids': ids}
                values = {'N': n, 'n_isects': len(ids), 'packed': 0, 'image_width': width, 'image_height': height, 'I': images,
                          'tile_width': tw, 'tile_height': th, 'block_offset': 0}
                shapes = {'gaussians': (images * n, 1, 1), 'images': (images, 1, 1), 'tiles': (tw, th, images),
                          'isects': (len(ids), 1, 1), 'pixels': (width * height * images, 1, 1)}
                case = Case(shapes, inputs, values, f'{label}, {side} intersections', omit=['backgrounds', 'masks'])
                if side == 'original':
                    want = original.run(case)
                else:
                    with self.subTest(label=label):
                        self.assertIsNone(raster.compare(want, metal.run_generated(host, plan, case), case))

    @unittest.skipUnless(NVIDIA, 'GPGPU-Sim rows are slow; set QPEL_NVIDIA=1')
    def test_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-gsplat-intersect-nvidia-')).resolve()
        try:
            runs = []
            for name, index in (('intersect_tile_count', 1), ('intersect_tile_emit', 2)):
                pipeline = self.pipelines[name]
                program = GpuProgram(pipeline.cuda, pipeline.plan, root/name)
                for i, row in enumerate(self.cases):
                    if row[index] is not None:
                        folder = program.work/f'c{i:03d}'
                        folder.mkdir()
                        program.write_case(row[index], folder)
                        runs.append((pipeline, program, folder, row[index], row[0]))
            run_all(root, [root, HERE, Path(self.tmp.name).resolve()])
            for pipeline, program, folder, case, label in runs:
                got, error = program.outcome(folder, case)
                self.assertIsNone(error)
                same(self, got, pipeline.original.run(case), pipeline.outputs, label)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
