"""gsplat's EWA projection of 3D gaussians into cameras (ProjectionEWA3DGSFused.cu, the forward kernel, unmodified):
each gaussian's mean and covariance into camera space, the pinhole, orthographic or fisheye projection (a
mat3x2 Jacobian), the 2D covariance blurred and inverted (glm::inverse of a mat2), its opacity-aware radius.
glm is read by running glm's own code (frontends/glm.py); the grid is the product B * C * gaussian_count the
kernel compares its thread index with; the fisheye projection reaches math.atan2 (IR version 22).

The original (gsplat's CUDA with the real glm headers) runs on the CPU simulator, qpel's CUDA there: every output
bit for bit. qpel's Metal on the Apple GPU: bit for bit too, except where a bounded function decides (atan2 for
the fisheye camera, log for an opacity's radius), which is within its bound (radii, integers from ceil, may then
differ by one)."""
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

PLANS = HERE/'corpus/gsplat/plans'
SOURCE = 'ProjectionEWA3DGSFused.cu'
PINHOLE, ORTHO, FISHEYE = 0, 1, 2
WIDTH, HEIGHT = 64, 48


class Built:
    def __init__(self, name, work, source=SOURCE):
        from frontends import roundtrip
        from frontends.c_import import import_source
        from qpel import compile_program
        from qpel_ir import Lowering, validate
        from differential.simulate import Program
        entry = next(e for e in roundtrip.corpus_entries() if e['source'].endswith(source))
        self.source = roundtrip.CORPUS/entry['source']
        include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
        plan = json.loads((PLANS/f'{name}.plan.json').read_text())
        self.program, self.report = import_source(self.source, plan=plan, include=include,
                                                  defines=roundtrip.corpus_defines(entry),
                                                  kernels=[s['kernel'] for s in plan['stages']])
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.plan = Lowering(self.program).plan()
        self.generated = work/'build/package/generated'
        for folder in ('o', 'q'):
            (work/folder).mkdir()
        self.original = Program(self.source, self.plan, work/'o', original=self.report, include=include)
        self.ours = Program(self.generated/(self.plan['module'] + '.cu'), self.plan, work/'q')
        self.outputs = [r['name'] for r in self.plan['resources'] if r['kind'] == 'output']


def rotation(angle):
    import numpy as np
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def scene(seed, batches, cameras, n, model, given_covars, opacities):
    """Gaussians in front of the cameras (a few behind the near plane or far off), random rotations and sizes."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(seed)
    z = rng.uniform(1.5, 9, (batches, n))
    z[:, :max(1, n // 10)] = 0.2  # behind the near plane
    xy = rng.uniform(-0.7, 0.7, (batches, n, 2)) * z[..., None] * (0.4 if model == FISHEYE else 1)
    means = np.concatenate([xy, z[..., None]], -1).astype(np.float32)
    quats = rng.standard_normal((batches, n, 4)).astype(np.float32)
    scales = np.exp(rng.uniform(-4, -1, (batches, n, 3))).astype(np.float32)
    a = rng.standard_normal((batches, n, 3, 3)) * 0.05
    full = a @ np.swapaxes(a, -1, -2) + 1e-4 * np.eye(3)
    covars = full[..., [0, 0, 0, 1, 1, 2], [0, 1, 2, 1, 2, 2]].astype(np.float32)
    views, ks = [], []
    for b in range(batches):
        for c in range(cameras):
            view = np.eye(4)
            view[:3, :3] = rotation(0.15 * c)
            view[:3, 3] = [0.1 * c, -0.05 * c, 0.3 * c]
            views.append(view)
            fx = WIDTH * (0.05 if model == ORTHO else 0.8)
            ks.append(np.array([[fx, 0, WIDTH / 2 + c], [0, fx, HEIGHT / 2 - c], [0, 0, 1]]))
    inputs = {'means': means.reshape(-1), 'quats': quats.reshape(-1), 'scales': scales.reshape(-1),
              'viewmats': np.array(views, np.float32).reshape(-1), 'Ks': np.array(ks, np.float32).reshape(-1),
              'covars': covars.reshape(-1), 'opacities': rng.uniform(0.005, 1, batches * n).astype(np.float32)}
    omit = ([] if given_covars else ['covars']) + ([] if opacities else ['opacities', 'compensations'])
    values = {'gaussian_offset': 0, 'gaussian_count': n, 'B': batches, 'C': cameras, 'N': n, 'image_width': WIDTH,
              'image_height': HEIGHT, 'eps2d': 0.3, 'near_plane': 0.5, 'far_plane': 50.0, 'radius_clip': 0.0, 'camera_model': model}
    shapes = {'elements': (batches * cameras * n, 1, 1), 'gaussians': (batches * n, 1, 1), 'cameras': (batches * cameras, 1, 1)}
    label = f'{["pinhole", "ortho", "fisheye"][model]} B={batches} C={cameras} N={n}' + \
        (' covariances' if given_covars else ' quaternions') + (' opacities' if opacities else '')
    return Case(shapes, inputs, values, label, omit=omit)


def cases():
    out, seed = [], 0
    for model in (PINHOLE, ORTHO, FISHEYE):
        for batches, cameras, n in ((1, 2, 40), (2, 1, 9)):
            for given in (False, True):
                for opacities in (False, True):
                    seed += 1
                    out.append(scene(seed, batches, cameras, n, model, given, opacities))
    return out


SCALES = {'radii': 2, 'means2d': 2, 'depths': 1, 'conics': 3, 'compensations': 1}


def check(test, got, want, case, ulps=0):
    """radii everywhere; the other outputs where the original projected the gaussian (a culled one is not written)."""
    import numpy as np
    from differential.compare import compare
    radii = np.asarray(want['radii']).view(np.int32).reshape(-1, 2)
    kept = radii.any(1)
    test.assertTrue(kept.any() and not kept.all(), case.label)  # (some projected, some culled)
    if ulps:  # an integer from ceil of a bounded result may differ by one
        difference = np.abs(np.asarray(got['radii']).view(np.int32).reshape(-1, 2) - radii)
        test.assertLessEqual(int(difference.max()), 1, case.label)
        kept &= (difference == 0).all(1)
    else:
        test.assertIsNone(compare(want, got, ['radii']), case.label)
    for name, scale in SCALES.items():
        if name == 'radii' or name not in want:
            continue
        mask = np.repeat(kept, scale)
        a = {name: np.asarray(want[name]).reshape(-1)[mask]}
        b = {name: np.asarray(got[name]).reshape(-1)[mask]}
        test.assertIsNone(compare(a, b, [name], ulps=ulps), case.label)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatProjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-projection-')
        (Path(cls.tmp.name)/'fwd').mkdir()
        cls.built = Built('projection_ewa_fwd', Path(cls.tmp.name)/'fwd')
        cls.cases = cases()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        text = print_program(self.built.program)
        self.assertEqual(self.built.program['version'], 22)  # math.atan2 (the fisheye projection)
        self.assertIn('elements: shape3', text)
        self.assertIn('camera_model: i32', text)
        self.assertNotIn('operator', text)

    def test_cpu_simulator(self):
        for case in self.cases:
            with self.subTest(case=case.label):
                check(self, self.built.ours.run(case), self.built.original.run(case), case)

    def test_apple_gpu(self):
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
                bounded = case.values['camera_model'] == FISHEYE or 'opacities' in case.inputs and 'opacities' not in case.omit
                check(self, metal.run_generated(host, self.built.plan, case), self.built.original.run(case), case,
                      ulps=2 * MATH_ULPS if bounded else 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
