"""gsplat's spherical-harmonics gradients, unmodified: `spherical_harmonics_bwd_kernel` (SphericalHarmonicsCUDA.cu)
and `spherical_harmonics_l1_plus_bwd_kernel` (SphericalHarmonicsL1PlusCUDA.cu), one kernel per degree. One thread
per coefficient row and channel accumulates the coefficient gradients over the images in a `std::array` and writes
them once; the view-direction gradients of the channels of a row are combined over `cg::labeled_partition(
cg::coalesced_threads(), row)` and added atomically to v_means and v_viewdirs by one thread of each label.

qpel reads the coalesced group as the calling thread alone (frontends/coalesced.py), one of the executions CUDA
allows, so each thread adds its own share; the simulator's cooperative groups do the same for the original.

The original runs on the CPU simulator, qpel's CUDA there, both with their threads one after another: every gradient
bit for bit (the threads add in the same order). qpel's Metal on the Apple GPU: rsqrt is within its bound and the atomic additions come in another order,
so v_coeffs is compared within 2 MATH_ULPS epsilons x (degree + 1) x 3 x the sum over its images of |v_color|
(|Y_k| < 3 up to degree 4), and v_means and v_viewdirs within 2 MATH_ULPS epsilons x (degree + 1) x their largest
entry. Measured over these cases: at most 10.2 epsilons of the largest v_coeffs entry, 5.0 of the largest direction
gradient, and nothing at degree 0 (no rsqrt; the direction gradients are zero there)."""
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

KERNELS = {'generic': ('SphericalHarmonicsCUDA.cu', 'spherical_harmonics_bwd_kernel_f32_f32', 0, (0, 1, 2, 3, 4)),
           'l1_plus': ('SphericalHarmonicsL1PlusCUDA.cu', 'spherical_harmonics_l1_plus_bwd_kernel_f32_f32', 1, (1, 2, 3, 4))}
VALUES = [('gaussian_offset', 'i64'), ('gaussian_count', 'i64'), ('B', 'u32'), ('C', 'u32'), ('N', 'u32'),
          ('K', 'u32'), ('D', 'u32')]


def plan(kernel):
    """The launch gsplat's host makes: (packed ? E : N) * D threads in blocks of 256."""
    def resource(name, kind, type, extent=None, optional=False):
        out = {'name': name, 'kind': kind, 'type': type, 'extent': extent}
        if optional:
            out['optional'] = True
        return out
    resources = [resource(n, 'value', t) for n, t in VALUES]
    resources += [resource('means', 'input', 'buffer<f32, read>', 'points'),
                  resource('viewmats', 'input', 'buffer<f32, read>', 'views'),
                  resource('camera_offsets', 'input', 'buffer<f32, read>', 'offsets', True),
                  resource('coeffs', 'input', 'buffer<f32, read>', 'coefficients'),
                  resource('masks', 'input', 'buffer<u8, read>', 'outputs', True),
                  resource('batch_ids', 'input', 'buffer<i64, read>', 'ids', True),
                  resource('camera_ids', 'input', 'buffer<i64, read>', 'ids'),
                  resource('gaussian_ids', 'input', 'buffer<i64, read>', 'ids'),
                  resource('v_colors', 'input', 'buffer<f32, read>', 'channels'),
                  resource('v_coeffs', 'output', 'buffer<f32, write>', 'coefficients'),
                  resource('v_means', 'inout', 'buffer<f32, atomic>', 'points', True),
                  resource('v_viewdirs', 'inout', 'buffer<f32, atomic>', 'directions', True)]
    resources += [resource(s, 'shape', 'shape3') for s in ('elements', 'points', 'views', 'offsets', 'coefficients',
                                                            'outputs', 'ids', 'channels', 'directions')]
    names = [n for n, _ in VALUES] + ['means', 'viewmats', 'camera_offsets', 'coeffs', 'masks', 'batch_ids', 'camera_ids',
                                      'gaussian_ids', 'v_colors', 'v_coeffs', 'v_means', 'v_viewdirs', 'elements']
    return {'schema': 2, 'module': f'gsplat_{kernel}', 'resources': resources,
            'stages': [{'kernel': kernel, 'grid': 'elements', 'blockSize': 256, 'completionBarrier': True,
                        'bindings': [{'parameter': n, 'resource': n} for n in names]}]}


class Built:
    def __init__(self, source, kernel, work):
        from frontends import roundtrip
        from frontends.c_import import import_source
        from qpel import compile_program
        from qpel_ir import Lowering, validate
        from differential.simulate import Program
        entry = next(e for e in roundtrip.corpus_entries() if e['source'].endswith(source))
        self.source = roundtrip.CORPUS/entry['source']
        include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
        self.program, self.report = import_source(self.source, plan=plan(kernel), include=include,
                                                  defines=roundtrip.corpus_defines(entry), kernels=[kernel])
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.plan = Lowering(self.program).plan()
        self.generated = work/'build/package/generated'
        for folder in ('o', 'q'):
            (work/folder).mkdir()
        # The file's other kernels have barriers, so the simulator would run the original's threads together, each
        # atomic addition in whatever order the host threads reach it. The gradient kernels have none: their threads
        # run one after another, as qpel's do (one CUDA schedule), so both add in the same order
        self.original = Program(self.source, self.plan, work/'o', original=self.report, include=include, sequential=True)
        self.ours = Program(self.generated/(self.plan['module'] + '.cu'), self.plan, work/'q')


def scene(seed, skip, d, degree, batches, cameras, n, packed, masked, offsets, wanted, chunk=None):
    """Gaussians around the origin seen from cameras on a circle; `wanted` names the optional gradients asked for."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(seed)
    k = (degree + 1) ** 2
    views, centres = [], []
    for v in range(batches * cameras):
        angle = 0.7 * v
        rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
        centre = np.array([3 * np.sin(angle), 0.2 * v, -3 * np.cos(angle)])
        view = np.eye(4)
        view[:3, :3] = rotation
        view[:3, 3] = -rotation @ centre
        views.append(view)
        centres.append(centre)
    means = rng.uniform(-1, 1, (batches * n, 3))
    if packed:
        triples = [(b, c, g) for b in range(batches) for c in range(cameras) for g in range(n) if rng.random() < 0.6]
        batch_ids, camera_ids, gaussian_ids = (np.array(x, np.int64) for x in zip(*triples))
        outputs = len(triples)
        offset, count = 0, outputs
    else:
        outputs = batches * cameras * n
        batch_ids = camera_ids = gaussian_ids = np.zeros(1, np.int64)  # (not read)
        offset, count = chunk or (0, n)
    rows = outputs if packed else n
    coeffs = rng.standard_normal((rows, k - skip, d)) * np.exp(rng.uniform(-3, 1, (rows, k - skip, 1)))
    elements = count * d
    inputs = {'means': means.astype(np.float32).reshape(-1), 'viewmats': np.array(views, np.float32).reshape(-1),
              'coeffs': coeffs.astype(np.float32).reshape(-1), 'masks': (rng.random(outputs) < 0.8).astype(np.uint8),
              'batch_ids': batch_ids, 'camera_ids': camera_ids, 'gaussian_ids': gaussian_ids,
              'camera_offsets': (-np.array(centres)).astype(np.float32).reshape(-1),
              'v_colors': rng.standard_normal(outputs * d).astype(np.float32),
              'v_means': np.zeros(means.size, np.float32), 'v_viewdirs': np.zeros(outputs * 3, np.float32)}  # (gsplat zeroes them)
    omit = ([] if masked else ['masks']) + ([] if packed else ['batch_ids']) + ([] if offsets else ['camera_offsets'])
    omit += [g for g in ('v_means', 'v_viewdirs') if g not in wanted]
    shapes = {'elements': (elements, 1, 1), 'points': (means.size, 1, 1), 'views': (16 * batches * cameras, 1, 1),
              'offsets': (3 * batches * cameras, 1, 1), 'coefficients': (coeffs.size, 1, 1), 'outputs': (outputs, 1, 1),
              'ids': (len(camera_ids), 1, 1), 'channels': (outputs * d, 1, 1), 'directions': (outputs * 3, 1, 1)}
    values = {'gaussian_offset': offset, 'gaussian_count': count, 'B': batches, 'C': cameras, 'N': n, 'K': k - skip, 'D': d}
    label = f'D={d} degree {degree} B={batches} C={cameras} N={n}' + (' packed' if packed else '') + \
        (' masks' if masked else '') + (' offsets' if offsets else '') + ''.join(' ' + g for g in wanted) + \
        (f' chunk {offset}+{count}' if chunk else '')
    return Case(shapes, inputs, values, label, omit=omit)


def cases(skip, degree):
    out, seed = [], 10 * degree + skip
    for d, wanted in ((3, ('v_means', 'v_viewdirs')), (1, ('v_means',)), (2, ())):
        for packed in (False, True):
            seed += 1
            out.append(scene(seed, skip, d, degree, 2, 3, 23, packed, masked=seed % 2 == 0, offsets=seed % 3 == 0,
                             wanted=wanted))
    out.append(scene(99 + degree, skip, 3, degree, 2, 2, 30, False, True, True, ('v_means', 'v_viewdirs'),
                     chunk=(11, 13)))  # a device's chunk of the gaussians
    return out


def outputs_of(built, case):
    return [o for o in ('v_coeffs', 'v_means', 'v_viewdirs') if o not in case.omit]


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatSphericalHarmonicsBackward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-sh-bwd-')
        cls.built, cls.cases = {}, {}
        for key, (source, kernel, skip, degrees) in KERNELS.items():
            for degree in degrees:
                work = Path(cls.tmp.name)/f'{key}_{degree}'
                work.mkdir()
                cls.built[(key, degree)] = Built(source, f'{kernel}_{degree}', work)
                cls.cases[(key, degree)] = cases(skip, degree)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        for (key, degree), built in self.built.items():
            text = print_program(built.program)
            self.assertIn('optional v_means: buffer<f32, atomic>', text, key)
            self.assertNotIn('coalesced', text, key)
            self.assertTrue(any('coalesced group' in a for a in built.report.get('adaptations', ())), key)

    def test_cpu_simulator(self):
        from differential.compare import compare
        for (key, degree), built in self.built.items():
            for case in self.cases[(key, degree)]:
                with self.subTest(kernel=key, degree=degree, case=case.label):
                    want, got = built.original.run(case), built.ours.run(case)
                    self.assertIsNone(compare(want, got, outputs_of(built, case)), case.label)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for (key, degree), built in self.built.items():
            host = matrix.load_host(built.generated)
            for case in self.cases[(key, degree)]:
                with self.subTest(kernel=key, degree=degree, case=case.label):
                    want, got = built.original.run(case), metal.run_generated(host, built.plan, case)
                    within(self, want, got, case, degree, KERNELS[key][2])


def written_rows(case):
    """The coefficient rows the launch writes: its chunk, gaussian_offset to gaussian_offset + gaussian_count."""
    v = case.values
    return slice(v['gaussian_offset'], v['gaussian_offset'] + v['gaussian_count'])


def within(test, want, got, case, degree, skip):
    """The Apple GPU's gradients against the original's, within the bounds of the module docstring."""
    import numpy as np
    from qpel_types import MATH_ULPS
    eps = float(np.finfo(np.float32).eps)
    v, d = case.values, case.values['D']
    packed = 'batch_ids' not in case.omit
    outputs = case.shapes['outputs'][0]
    images = 1 if packed else v['B'] * v['C']
    active = np.ones(outputs, bool) if 'masks' in case.omit else case.inputs['masks'].astype(bool)
    colours = np.abs(np.asarray(case.inputs['v_colors'], np.float64)).reshape(outputs, d) * active[:, None]
    # per coefficient row and channel: the sum over its images of |v_color|
    if packed:
        per_row = colours
    else:
        per_row = colours.reshape(images, v['N'], d).sum(0)
    k = v['K']
    bound = 2 * MATH_ULPS * eps * (degree + 1) * 3.0 * per_row  # |Y_k| < 3 for degree <= 4
    rows = written_rows(case)  # (a chunk's launch writes its rows only; the others are what the buffer held)
    a = np.asarray(want['v_coeffs'], np.float64).reshape(-1, k, d)[rows]
    b = np.asarray(got['v_coeffs'], np.float64).reshape(-1, k, d)[rows]
    excess = np.abs(a - b) - bound[rows][:, None, :]
    test.assertLessEqual(float(excess.max()), 0.0, f'{case.label} v_coeffs: worst beyond its bound by {float(excess.max()):.3g}')
    # direction gradients: sums of the channels' and images' shares in another order, each share through rsqrt
    for output in ('v_means', 'v_viewdirs'):
        if output in case.omit:
            continue
        a = np.asarray(want[output], np.float64)
        b = np.asarray(got[output], np.float64)
        allowed = 2 * MATH_ULPS * (degree + 1) * eps * float(np.abs(a).max())
        test.assertLessEqual(float(np.abs(a - b).max()), allowed, f'{case.label} {output}')

if __name__ == '__main__':
    unittest.main(verbosity=2)
