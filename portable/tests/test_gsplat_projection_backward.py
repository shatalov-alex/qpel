"""gsplat's EWA projection gradient (ProjectionEWA3DGSFused.cu, `projection_ewa_3dgs_fused_bwd_kernel`, unmodified):
the vector-Jacobian products of the projection, back through the blur's compensation, the camera transform and the
quaternion and scale (or the given covariance), summed per gaussian over the cameras and per camera over the
gaussians. The kernel sums each gaussian's (and camera's) gradients over a labeled partition of its warp and lets
one thread of each label add them atomically; qpel reads the partition as the calling thread alone
(frontends/coalesced.py), so every thread adds its own share. The compensation's gradient computes
`v_compensation * 0.5 / (compensation + 1e-6)` in double, which qpel computes exactly in software
(frontends/soft_double.py).

The forward kernel's original gives each scene's radii, conics and compensations. The original backward kernel runs
on the CPU simulator with real warps (its tiles and labeled partitions are CUDA's), qpel's CUDA there and qpel's
Metal on the Apple GPU (the kernel's 28 buffers and 5 presence flags pass Metal's 31, so the flags travel packed
in one buffer). gsplat labels the partitions by the gaussian (and the camera) alone, not the batch: a warp spanning
two batches adds one batch's gradients into the other's (with C * gaussian_count < 32 for the gaussians', with
C = 1 for the cameras'), which test_the_original_merges_batches shows. qpel's reading gives each batch its own, so
the reference is the original run one batch at a time. The atomic totals are the same sums in another order, so each
output is compared within BOUND * eps * (the number of shares in a total) * its largest entry (MATH_ULPS times more
on the Apple GPU, whose rsqrt and atan2 are bounded). Measured: on the CPU simulator the gaussians' gradients are
bit for bit and the cameras' within 0.05 of that unit; on the Apple GPU within 3.1 (the fisheye)."""
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

BOUND = 2
GRADIENTS = {'v_means': 3, 'v_covars': 6, 'v_quats': 4, 'v_scales': 3, 'v_viewmats': 16}


def backward_case(forward, original, seed):
    """The forward scene, the forward original's outputs and random output gradients."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(1000 + seed)
    out = original.run(forward)
    v = forward.values
    elements, gaussians, cameras = (forward.shapes[k][0] for k in ('elements', 'gaussians', 'cameras'))
    compensations = 'compensations' not in forward.omit
    inputs = {k: forward.inputs[k] for k in ('means', 'quats', 'scales', 'viewmats', 'Ks', 'covars') if k in forward.inputs}
    inputs |= {'radii': np.asarray(out['radii']).view(np.int32), 'conics': np.asarray(out['conics'], np.float32),
               'compensations': np.asarray(out['compensations'], np.float32) if compensations else np.zeros(elements, np.float32),
               'v_means2d': rng.standard_normal(elements * 2).astype(np.float32),
               'v_depths': rng.standard_normal(elements).astype(np.float32),
               'v_conics': rng.standard_normal(elements * 3).astype(np.float32),
               'v_compensations': rng.standard_normal(elements).astype(np.float32)}
    for name, scale in GRADIENTS.items():
        inputs[name] = np.zeros((cameras if name == 'v_viewmats' else gaussians) * scale, np.float32)
    given = 'covars' not in forward.omit
    omit = [] if given else ['covars', 'v_covars']
    omit += [] if compensations else ['v_compensations']
    values = {k: v[k] for k in ('gaussian_offset', 'gaussian_count', 'B', 'C', 'N', 'image_width', 'image_height',
                                'eps2d', 'camera_model')}
    return Case(dict(forward.shapes), {k: x for k, x in inputs.items() if k not in omit}, values,
                forward.label, omit=omit)


def reference(original, case):
    """The original's gradients, one batch at a time. Its labeled partitions are labeled by the gaussian (and the
    camera) alone, so a warp spanning two batches would add one batch's gradients into the other's: with
    C * gaussian_count < 32 for the gaussians', with C = 1 for the cameras'. Every resource is batch-major."""
    import numpy as np
    from differential.simulate import Case
    b = case.values['B']
    if b == 1:
        return original.run(case)
    parts = []
    for k in range(b):
        inputs = {n: np.split(np.asarray(x), b)[k] for n, x in case.inputs.items()}
        shapes = {n: (s[0] // b, *s[1:]) for n, s in case.shapes.items()}
        parts.append(original.run(Case(shapes, inputs, case.values | {'B': 1}, case.label, omit=list(case.omit))))
    return {n: np.concatenate([np.asarray(p[n]) for p in parts]) for n in parts[0]}


def within(test, want, got, case, factor):
    import numpy as np
    eps = float(np.finfo(np.float32).eps)
    v = case.values
    for name in GRADIENTS:
        if name in case.omit:
            continue
        a, b = np.asarray(want[name], np.float64), np.asarray(got[name], np.float64)
        shares = v['N'] if name == 'v_viewmats' else v['C']
        allowed = factor * eps * shares * float(np.abs(a).max())
        worst = float(np.abs(a - b).max())
        test.assertLessEqual(worst, allowed, f'{case.label} {name}: {worst / (eps * shares * max(float(np.abs(a).max()), 1e-30)):.3g}')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatProjectionBackward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-projection-bwd-')
        work = Path(cls.tmp.name)
        for name in ('fwd', 'bwd'):
            (work/name).mkdir()
        forward = projection.Built('projection_ewa_fwd', work/'fwd')
        cls.built = projection.Built('projection_ewa_bwd', work/'bwd')
        cls.cases = [backward_case(case, forward.original, seed) for seed, case in enumerate(projection.cases())]
        cls.want = {case.label: reference(cls.built.original, case) for case in cls.cases}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        text = print_program(self.built.program)
        self.assertIn('fn double_div(', text)
        self.assertIn('optional v_viewmats: buffer<f32, atomic>', text)
        notes = self.built.report['adaptations']
        self.assertTrue(any('labeled partitions of a warp' in a for a in notes))
        self.assertTrue(any('computed exactly in software' in a for a in notes))
        metal = (self.built.generated/(self.built.plan['module'] + '.metal')).read_text()
        self.assertIn('QPEL_PRESENCE_PARAM(28)', metal)  # (the presence flags packed in one buffer)

    def test_the_original_uses_real_warps(self):
        self.assertTrue(self.built.original.threaded)

    def test_the_original_merges_batches(self):
        """gsplat's labels leave out the batch: run whole, a warp spanning two batches mixes their gradients."""
        import numpy as np
        case = next(c for c in self.cases if c.values['B'] == 2)
        whole = self.built.original.run(case)
        self.assertFalse(np.allclose(np.asarray(whole['v_viewmats']), np.asarray(self.want[case.label]['v_viewmats'])))

    def test_cpu_simulator(self):
        for case in self.cases:
            with self.subTest(case=case.label):
                within(self, self.want[case.label], self.built.ours.run(case), case, BOUND)

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
                within(self, self.want[case.label], metal.run_generated(host, self.built.plan, case), case,
                       BOUND * MATH_ULPS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
