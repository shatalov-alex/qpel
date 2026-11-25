"""gsplat's 2DGS projection and its gradient (Projection2DGSFused.cu, `projection_2dgs_fused_fwd_kernel` and
`projection_2dgs_fused_bwd_kernel`, unmodified): each gaussian a flat disc, projected to its pixel-space ray
transform, normal, depth and radius. The kernels build matrices through glm's converting constructors with double
constants (`mat3(scale[0], 0.0, 0.0, 0.0, scale[1], 0.0, 0.0, 0.0, 1.0)`); glm converts each to float, which the
import does exactly at the constant (frontends/soft_double.py's conversion), so no double code is left.

The forward kernel: the original and qpel's CUDA on the CPU simulator, bit for bit; qpel's Metal on the Apple GPU,
radii within one pixel and the other outputs within MATH_ULPS of the gaussians the original kept. The gradient sums
over labeled partitions of a warp as gsplat's EWA gradient does, labeled by the gaussian and the camera alone, so it
is compared with the original run one batch at a time (test_gsplat_projection_backward.reference), within
BOUND * eps * (the shares of a total) * its largest entry (MATH_ULPS times more on the Apple GPU). Measured: every
gradient within 0.08 of that unit on both, the per-gaussian ones bit for bit when no other camera shares them."""
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
import test_gsplat_projection_backward as backward  # noqa: E402

SOURCE = 'Projection2DGSFused.cu'
OUTPUTS = {'radii': 2, 'means2d': 2, 'depths': 1, 'ray_transforms': 9, 'normals': 3}
GRADIENTS = {'v_means': 3, 'v_quats': 4, 'v_scales': 3, 'v_viewmats': 16}
VALUES = ('B', 'C', 'N', 'image_width', 'image_height')
BOUND = 2


def cases():
    """Pinhole scenes (2DGS has no camera model): one batch of two cameras, two batches of one."""
    from differential.simulate import Case
    out = []
    for seed, (batches, cameras, n) in enumerate(((1, 2, 40), (2, 1, 9), (1, 3, 70))):
        s = projection.scene(100 + seed, batches, cameras, n, projection.PINHOLE, False, False)
        inputs = {k: s.inputs[k] for k in ('means', 'quats', 'scales', 'viewmats', 'Ks')}
        values = {k: s.values[k] for k in VALUES + ('near_plane', 'far_plane', 'radius_clip')}
        out.append(Case(dict(s.shapes), inputs, values, f'B={batches} C={cameras} N={n}'))
    return out


def kept(want):
    import numpy as np
    return np.asarray(want['radii']).view(np.int32).reshape(-1, 2).all(1)


def check_forward(test, want, got, case, ulps=0):
    import numpy as np
    from differential.compare import compare
    keep = kept(want)
    test.assertTrue(keep.any() and not keep.all(), case.label)  # (some projected, some culled)
    radii, mine = (np.asarray(x['radii']).view(np.int32).reshape(-1, 2) for x in (want, got))
    if ulps:  # an integer from ceil of a bounded result may differ by one
        test.assertLessEqual(int(np.abs(mine - radii).max()), 1, case.label)
        keep &= (mine == radii).all(1)
    else:
        test.assertTrue(np.array_equal(mine, radii), case.label)
    for name, scale in OUTPUTS.items():
        if name == 'radii':
            continue
        mask = np.repeat(keep, scale)
        a, b = ({name: np.asarray(x[name]).reshape(-1)[mask]} for x in (want, got))
        test.assertIsNone(compare(a, b, [name], ulps=ulps), f'{case.label} {name}')


def backward_case(case, out, seed):
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(2000 + seed)
    elements, gaussians, cameras = (case.shapes[k][0] for k in ('elements', 'gaussians', 'cameras'))
    inputs = dict(case.inputs) | {
        'radii': np.asarray(out['radii']).view(np.int32), 'ray_transforms': np.asarray(out['ray_transforms'], np.float32),
        'v_means2d': rng.standard_normal(elements * 2).astype(np.float32),
        'v_depths': rng.standard_normal(elements).astype(np.float32),
        'v_normals': rng.standard_normal(elements * 3).astype(np.float32),
        'v_ray_transforms': rng.standard_normal(elements * 9).astype(np.float32)}
    for name, scale in GRADIENTS.items():
        inputs[name] = np.zeros((cameras if name == 'v_viewmats' else gaussians) * scale, np.float32)
    return Case(dict(case.shapes), inputs, {k: case.values[k] for k in VALUES}, case.label)


def within(test, want, got, case, factor):
    import numpy as np
    eps = float(np.finfo(np.float32).eps)
    for name in GRADIENTS:
        a, b = np.asarray(want[name], np.float64), np.asarray(got[name], np.float64)
        shares = case.values['N'] if name == 'v_viewmats' else case.values['C']
        scale = eps * shares * max(float(np.abs(a).max()), 1e-30)
        worst = float(np.abs(a - b).max())
        test.assertLessEqual(worst, factor * scale, f'{case.label} {name}: {worst / scale:.3g}')


def apple_gpu(test):
    try:
        import torch
        if not torch.backends.mps.is_available():
            unavailable(test, 'gpu', 'no Apple GPU (PyTorch MPS)')
    except ImportError:
        unavailable(test, 'gpu', 'PyTorch required for the Apple GPU tests')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatProjection2DGS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-2dgs-')
        work = Path(cls.tmp.name)
        for name in ('fwd', 'bwd'):
            (work/name).mkdir()
        cls.forward = projection.Built('projection_2dgs_fwd', work/'fwd', SOURCE)
        cls.backward = projection.Built('projection_2dgs_bwd', work/'bwd', SOURCE)
        cls.cases = cases()
        cls.outputs = {c.label: cls.forward.original.run(c) for c in cls.cases}
        cls.gradients = [backward_case(c, cls.outputs[c.label], k) for k, c in enumerate(cls.cases)]
        cls.want = {c.label: backward.reference(cls.backward.original, c) for c in cls.gradients}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_double_code_is_left(self):
        from qpel_text import print_program
        for built in (self.forward, self.backward):
            self.assertNotIn('double_', print_program(built.program))
        self.assertTrue(any('labeled partitions of a warp' in a for a in self.backward.report['adaptations']))

    def test_forward_cpu_simulator(self):
        for case in self.cases:
            with self.subTest(case=case.label):
                check_forward(self, self.outputs[case.label], self.forward.ours.run(case), case)

    def test_forward_apple_gpu(self):
        apple_gpu(self)
        from differential import matrix, metal
        from qpel_types import MATH_ULPS
        host = matrix.load_host(self.forward.generated)
        for case in self.cases:
            with self.subTest(case=case.label):
                check_forward(self, self.outputs[case.label], metal.run_generated(host, self.forward.plan, case),
                              case, MATH_ULPS)

    def test_gradient_cpu_simulator(self):
        for case in self.gradients:
            with self.subTest(case=case.label):
                within(self, self.want[case.label], self.backward.ours.run(case), case, BOUND)

    def test_gradient_apple_gpu(self):
        apple_gpu(self)
        from differential import matrix, metal
        from qpel_types import MATH_ULPS
        host = matrix.load_host(self.backward.generated)
        for case in self.gradients:
            with self.subTest(case=case.label):
                within(self, self.want[case.label], metal.run_generated(host, self.backward.plan, case), case,
                       BOUND * MATH_ULPS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
