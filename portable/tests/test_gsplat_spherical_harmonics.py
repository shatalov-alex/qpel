"""gsplat's spherical harmonics, forward (SphericalHarmonicsCUDA.cu, the K = 16, D = 3 kernel gsplat runs for RGB
colours, unmodified): per gaussian and camera, the view direction (from the camera's position or a precomputed
offset), its SH basis up to the degree in use, and the three colour channels. One kernel per coefficient type
(float, half) and degree (0-3). Its wide loads (`reinterpret_cast<const uint4 *>(coeffs)[i]`) read as the element
copies they stand for (records.wide_copies); the local arrays it hands the evaluation helper make a copy of the
helper taking arrays (records.array_arguments); `rsqrtf` is math.rsqrt (IR version 23).

The original runs on the CPU simulator, qpel's CUDA there: every colour bit for bit. qpel's Metal on the Apple
GPU: within the bound of rsqrt (degree >= 1 normalises the direction with it), bit for bit at degree 0."""
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

SOURCE = 'SphericalHarmonicsCUDA.cu'
KERNEL = 'spherical_harmonics_fwd_kernel_k16_3channel'
VARIANTS = [(coefficients, degree) for coefficients in ('f32', 'f16') for degree in range(4)]


def plan(coefficients, degree):
    """The launch gsplat's host makes (launch_spherical_harmonics_fwd_kernel): E threads of 256."""
    def resource(name, kind, type, extent=None, scale=None, optional=False):
        out = {'name': name, 'kind': kind, 'type': type, 'extent': extent}
        if scale:
            out['scale'] = scale
        if optional:
            out['optional'] = True
        return out
    resources = [resource('B', 'value', 'u32'), resource('C', 'value', 'u32'), resource('N', 'value', 'u32'),
                 resource('means', 'input', 'buffer<f32, read>', 'points', 3),
                 resource('viewmats', 'input', 'buffer<f32, read>', 'cameras', 16),
                 resource('camera_offsets', 'input', 'buffer<f32, read>', 'cameras', 3, True),
                 resource('coeffs', 'input', f'buffer<{coefficients}, read>', 'rows', 48),
                 resource('masks', 'input', 'buffer<u8, read>', 'E', optional=True),
                 resource('batch_ids', 'input', 'buffer<i64, read>', 'E', optional=True),
                 resource('camera_ids', 'input', 'buffer<i64, read>', 'E'),
                 resource('gaussian_ids', 'input', 'buffer<i64, read>', 'E'),
                 resource('colors', 'output', 'buffer<f32, write>', 'E', 3)]
    resources += [resource(s, 'shape', 'shape3') for s in ('E', 'points', 'cameras', 'rows')]
    names = ['B', 'C', 'N', 'E', 'means', 'viewmats', 'camera_offsets', 'coeffs', 'masks', 'batch_ids', 'camera_ids',
             'gaussian_ids', 'colors']
    return {'schema': 2, 'module': f'gsplat_sh_fwd_{coefficients}_{degree}', 'resources': resources,
            'stages': [{'kernel': f'{KERNEL}_{coefficients}_f32_{degree}', 'grid': 'E', 'blockSize': 256,
                        'completionBarrier': True, 'bindings': [{'parameter': n, 'resource': n} for n in names]}]}


class Built:
    def __init__(self, coefficients, degree, work):
        from frontends import roundtrip
        from frontends.c_import import import_source
        from qpel import compile_program
        from qpel_ir import Lowering, validate
        from differential.simulate import Program
        entry = next(e for e in roundtrip.corpus_entries() if e['source'].endswith(SOURCE))
        self.source = roundtrip.CORPUS/entry['source']
        include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
        launch = plan(coefficients, degree)
        self.program, self.report = import_source(self.source, plan=launch, include=include,
                                                  defines=roundtrip.corpus_defines(entry),
                                                  kernels=[s['kernel'] for s in launch['stages']])
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.plan = Lowering(self.program).plan()
        self.generated = work/'build/package/generated'
        for folder in ('o', 'q'):
            (work/folder).mkdir()
        self.original = Program(self.source, self.plan, work/'o', original=self.report, include=include)
        self.ours = Program(self.generated/(self.plan['module'] + '.cu'), self.plan, work/'q')


def scene(seed, coefficients, batches, cameras, n, packed, masked, offsets):
    """Gaussians around the origin seen from cameras on a circle; coefficients of either sign and size."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(seed)
    views, centres = [], []
    for k in range(batches * cameras):
        angle = 0.7 * k
        rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
        centre = np.array([3 * np.sin(angle), 0.2 * k, -3 * np.cos(angle)])
        view = np.eye(4)
        view[:3, :3] = rotation
        view[:3, 3] = -rotation @ centre
        views.append(view)
        centres.append(centre)
    means = rng.uniform(-1, 1, (batches * n, 3))
    if packed:  # some (batch, camera, gaussian) triples, as the packed projection gives them
        triples = [(b, c, g) for b in range(batches) for c in range(cameras) for g in range(n) if rng.random() < 0.6]
        batch_ids, camera_ids, gaussian_ids = (np.array(x, np.int64) for x in zip(*triples))
        elements = len(triples)
    else:
        elements = batches * cameras * n
        batch_ids = camera_ids = gaussian_ids = np.zeros(elements, np.int64)  # (not read)
    rows = elements if packed else n
    coeffs = (rng.standard_normal((rows, 16, 3)) * np.exp(rng.uniform(-3, 1, (rows, 16, 1)))).astype(
        np.float16 if coefficients == 'f16' else np.float32)
    inputs = {'means': means.astype(np.float32).reshape(-1), 'viewmats': np.array(views, np.float32).reshape(-1),
              'coeffs': coeffs.reshape(-1), 'masks': (rng.random(elements) < 0.8).astype(np.uint8),
              'batch_ids': batch_ids, 'camera_ids': camera_ids, 'gaussian_ids': gaussian_ids,
              'camera_offsets': (-np.array(centres)).astype(np.float32).reshape(-1)}
    omit = ([] if masked else ['masks']) + ([] if packed else ['batch_ids']) + ([] if offsets else ['camera_offsets'])
    shapes = {'E': (elements, 1, 1), 'points': (batches * n, 1, 1), 'cameras': (batches * cameras, 1, 1), 'rows': (rows, 1, 1)}
    label = f'B={batches} C={cameras} N={n}' + (' packed' if packed else '') + (' masks' if masked else '') + \
        (' offsets' if offsets else '')
    return Case(shapes, inputs, {'B': batches, 'C': cameras, 'N': n}, label, omit=omit)


def cases(coefficients):
    out, seed = [], 0
    for batches, cameras, n in ((1, 2, 300), (2, 3, 41)):
        for packed in (False, True):
            for masked in (False, True):
                for offsets in (False, True):
                    seed += 1
                    out.append(scene(seed, coefficients, batches, cameras, n, packed, masked, offsets))
    return out


def check(test, got, want, case, degree=None):
    """Every colour the original wrote (a masked element's are left as they were): bit for bit, or (degree given)
    within the error rsqrt's bound allows. A colour is a sum of up to 16 terms c_k Y_k(dir) that may cancel, and
    rsqrt's error moves the direction, so each term by at most `degree` times that relative error; the bound is
    2 * MATH_ULPS float epsilons times (degree + 1) times sum |c_k| max |Y_k| (|Y_k| <= 1.1 on the unit sphere for
    degrees up to 3), not a number of ulps of the colour."""
    import numpy as np
    from differential.compare import compare
    from qpel_types import MATH_ULPS
    elements = case.shapes['E'][0]
    kept = case.inputs['masks'].astype(bool) if 'masks' not in case.omit else np.ones(elements, bool)
    a, b = (np.asarray(x['colors']).reshape(-1)[:3 * elements].reshape(-1, 3)[kept] for x in (want, got))
    if degree is None:
        test.assertIsNone(compare({'colors': a.reshape(-1)}, {'colors': b.reshape(-1)}, ['colors']), case.label)
        return
    rows = np.arange(elements) if 'batch_ids' not in case.omit else np.arange(elements) % case.values['N']
    coeffs = np.asarray(case.inputs['coeffs'], np.float64).reshape(-1, 16, 3)[rows][kept][:, :(degree + 1) ** 2]
    scale = 1.1 * np.abs(coeffs).sum(1)
    bound = 2 * MATH_ULPS * float(np.finfo(np.float32).eps) * (degree + 1) * scale
    excess = np.abs(a.astype(np.float64) - b.astype(np.float64)) - bound
    test.assertLessEqual(float(excess.max()), 0.0, f'{case.label}: worst {float((excess + bound).max()):.3g} '
                                                    f'beyond its bound by {float(excess.max()):.3g}')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatSphericalHarmonics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-sh-')
        cls.built = {}
        for coefficients, degree in VARIANTS:
            work = Path(cls.tmp.name)/f'{coefficients}_{degree}'
            work.mkdir()
            cls.built[coefficients, degree] = Built(coefficients, degree, work)
        cls.cases = {c: cases(c) for c in ('f32', 'f16')}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        text = print_program(self.built['f16', 3].program)
        self.assertEqual(self.built['f16', 3].program['version'], 23)  # math.rsqrt
        self.assertIn('coeffs: buffer<f16, read>', text)
        self.assertIn('_a48_3(3u, 3u, 0u, dir, sh_coeffs, out_color)', text)  # the helper's copy taking arrays
        self.assertIn('viewmat_rs_absent', text)  # camera_offset_from_world_to_camera(viewmat), its default nullptr
        self.assertNotIn('reinterpret', text)

    def test_cpu_simulator(self):
        for (coefficients, degree), built in self.built.items():
            for case in self.cases[coefficients]:
                with self.subTest(variant=f'{coefficients} degree {degree}', case=case.label):
                    check(self, built.ours.run(case), built.original.run(case), case)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for (coefficients, degree), built in self.built.items():
            host = matrix.load_host(built.generated)
            for case in self.cases[coefficients]:
                with self.subTest(variant=f'{coefficients} degree {degree}', case=case.label):
                    check(self, metal.run_generated(host, built.plan, case), built.original.run(case), case,
                          degree=degree or None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
