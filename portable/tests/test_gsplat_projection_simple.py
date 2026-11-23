"""gsplat's simple EWA projection and its gradient (ProjectionEWASimple.cu, both kernels unmodified): camera-space
means and covariances to 2D means and covariances through the pinhole, orthographic or fisheye Jacobian, and
back (`persp_proj_vjp`, `ortho_proj_vjp`, `fisheye_proj_vjp`: the vector-Jacobian products of the means and
covariances). The kernels compare their thread index with the product `B * C * N` written in the comparison, so
qpel launches them over a shape of that many elements.

The original (gsplat's CUDA with the real glm headers) runs on the CPU simulator, qpel's CUDA there: every output
bit for bit. qpel's Metal on the Apple GPU: bit for bit for the pinhole and orthographic cameras. The fisheye's
atan2 is bounded (IR version 22, MATH_ULPS), and the Jacobian and its derivative combine theta = atan2(r, z) and
theta / r in sums that may cancel, so each output is compared within 16 * MATH_ULPS * epsilon of the largest
entry of its gaussian's output (its 2 means, its 4 or 9 covariance entries); the worst measured over 36 random
scenes was 35 epsilon (v_means), the bound 64."""
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
import test_gsplat_projection as projection  # noqa: E402

PINHOLE, ORTHO, FISHEYE = 0, 1, 2
WIDTH, HEIGHT = 64, 48


class Built(projection.Built):
    def __init__(self, name, work):
        super().__init__(name, work, source='ProjectionEWASimple.cu')


def scene(seed, batches, cameras, n, model):
    """Camera-space gaussians in front of the cameras, symmetric positive covariances, random output gradients."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(seed)
    count = batches * cameras * n
    z = rng.uniform(1.5, 9, count)
    xy = rng.uniform(-0.8, 0.8, (count, 2)) * z[:, None] * (0.4 if model == FISHEYE else 1)
    xy[:max(1, count // 8)] *= 4  # beyond the image, where the perspective Jacobian clamps
    means = np.concatenate([xy, z[:, None]], -1).astype(np.float32)
    a = rng.standard_normal((count, 3, 3)) * 0.05
    covars = (a @ np.swapaxes(a, -1, -2) + 1e-4 * np.eye(3)).astype(np.float32)
    ks = []
    for c in range(batches * cameras):
        fx = WIDTH * (0.05 if model == ORTHO else 0.8)
        ks.append([[fx, 0, WIDTH / 2 + c], [0, fx, HEIGHT / 2 - c], [0, 0, 1]])
    inputs = {'means': means.reshape(-1), 'covars': covars.reshape(-1), 'Ks': np.array(ks, np.float32).reshape(-1),
              'v_means2d': rng.standard_normal(count * 2).astype(np.float32),
              'v_covars2d': rng.standard_normal(count * 4).astype(np.float32)}
    values = {'B': batches, 'C': cameras, 'N': n, 'width': WIDTH, 'height': HEIGHT, 'camera_model': model}
    shapes = {'elements': (count, 1, 1), 'cameras': (batches * cameras, 1, 1)}
    return Case(shapes, inputs, values, f'{["pinhole", "ortho", "fisheye"][model]} B={batches} C={cameras} N={n}')


def cases():
    return [scene(seed, b, c, n, model) for seed, (model, (b, c, n)) in enumerate(
        (model, size) for model in (PINHOLE, ORTHO, FISHEYE) for size in ((1, 2, 40), (2, 3, 9), (1, 1, 1)))]


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatProjectionSimple(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-projection-simple-')
        cls.built = {}
        for name in ('projection_ewa_simple_fwd', 'projection_ewa_simple_bwd'):
            (Path(cls.tmp.name)/name).mkdir()
            cls.built[name] = Built(name, Path(cls.tmp.name)/name)
        cls.cases = cases()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        for name, built in self.built.items():
            text = print_program(built.program)
            self.assertIn('elements: shape3', text, name)
            self.assertNotIn('operator', text, name)
            self.assertTrue(any('B * C * N' in line for line in built.report.get('adaptations', ())), name)

    def test_cpu_simulator(self):
        from differential.compare import compare
        for name, built in self.built.items():
            for case in self.cases:
                with self.subTest(kernel=name, case=case.label):
                    want, got = built.original.run(case), built.ours.run(case)
                    self.assertIsNone(compare(want, got, built.outputs), case.label)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        import numpy as np
        from differential import matrix, metal
        from differential.compare import compare
        from qpel_types import MATH_ULPS
        for name, built in self.built.items():
            host = matrix.load_host(built.generated)
            for case in self.cases:
                with self.subTest(kernel=name, case=case.label):
                    want, got = built.original.run(case), metal.run_generated(host, built.plan, case)
                    if case.values['camera_model'] != FISHEYE:
                        self.assertIsNone(compare(want, got, built.outputs), case.label)
                        continue
                    for output in built.outputs:
                        a = np.asarray(want[output], np.float64)
                        b = np.asarray(got[output], np.float64)
                        per = a.size // case.shapes['elements'][0]
                        scale = np.abs(a.reshape(-1, per)).max(1, keepdims=True)
                        bound = 16 * MATH_ULPS * float(np.finfo(np.float32).eps) * scale
                        excess = (np.abs(a - b).reshape(-1, per) - bound).max()
                        self.assertLessEqual(float(excess), 0.0, f'{case.label} {output}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
