"""gsplat's generic spherical-harmonics forward kernels, unmodified: `spherical_harmonics_fwd_kernel`
(SphericalHarmonicsCUDA.cu, the kernel gsplat runs when K != 16, D != 3 or the coefficients are misaligned, and
for its multi-device chunks) and `spherical_harmonics_l1_plus_fwd_kernel` (SphericalHarmonicsL1PlusCUDA.cu, the
same without the degree-0 coefficient). One thread per output element and channel; any K, D and degree up to 4.

Their grid is `(packed ? gaussian_count : B * C * gaussian_count) * D` threads, a count the kernel computes and
compares its index with: qpel launches them over a shape of that many elements, which the caller gives (as
gsplat's host computes `n_elements` for the launch).

The original runs on the CPU simulator, qpel's CUDA there: every colour bit for bit. qpel's Metal on the Apple
GPU: within the bound rsqrt allows (test_gsplat_spherical_harmonics.check), bit for bit at degree 0."""
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

KERNELS = {'generic': ('SphericalHarmonicsCUDA.cu', 'spherical_harmonics_fwd_kernel', 0),
           'l1_plus': ('SphericalHarmonicsL1PlusCUDA.cu', 'spherical_harmonics_l1_plus_fwd_kernel', 1)}
VALUES = [('gaussian_offset', 'i64'), ('gaussian_count', 'i64'), ('B', 'u32'), ('C', 'u32'), ('N', 'u32'),
          ('K', 'u32'), ('D', 'u32'), ('degrees_to_use', 'u32')]


def plan(kernel):
    """The launch gsplat's host makes: (packed ? E : B * C * gaussian_count) * D threads in blocks of 256."""
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
                  resource('colors', 'output', 'buffer<f32, write>', 'channels')]
    resources += [resource(s, 'shape', 'shape3') for s in
                  ('elements', 'points', 'views', 'offsets', 'coefficients', 'outputs', 'ids', 'channels')]
    names = [n for n, _ in VALUES] + ['means', 'viewmats', 'camera_offsets', 'coeffs', 'masks', 'batch_ids',
                                      'camera_ids', 'gaussian_ids', 'colors', 'elements']
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
        self.original = Program(self.source, self.plan, work/'o', original=self.report, include=include)
        self.ours = Program(self.generated/(self.plan['module'] + '.cu'), self.plan, work/'q')


def scene(seed, skip, k, d, degree, batches, cameras, n, packed, masked, offsets, chunk=None):
    """Gaussians around the origin seen from cameras on a circle. `chunk` = (offset, count): one device's share of
    the gaussians, as gsplat's multi-device launch gives it (dense only)."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(seed)
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
    elements = (count if packed else batches * cameras * count) * d
    inputs = {'means': means.astype(np.float32).reshape(-1), 'viewmats': np.array(views, np.float32).reshape(-1),
              'coeffs': coeffs.astype(np.float32).reshape(-1), 'masks': (rng.random(outputs) < 0.8).astype(np.uint8),
              'batch_ids': batch_ids, 'camera_ids': camera_ids, 'gaussian_ids': gaussian_ids,
              'camera_offsets': (-np.array(centres)).astype(np.float32).reshape(-1)}
    omit = ([] if masked else ['masks']) + ([] if packed else ['batch_ids']) + ([] if offsets else ['camera_offsets'])
    shapes = {'elements': (elements, 1, 1), 'points': (means.size, 1, 1), 'views': (16 * batches * cameras, 1, 1),
              'offsets': (3 * batches * cameras, 1, 1), 'coefficients': (coeffs.size, 1, 1), 'outputs': (outputs, 1, 1),
              'ids': (len(camera_ids), 1, 1), 'channels': (outputs * d, 1, 1)}
    # K is coeffs.size(-2), as gsplat's hosts pass it: the coefficients stored (L1-plus: without the degree-0 one)
    values = {'gaussian_offset': offset, 'gaussian_count': count, 'B': batches, 'C': cameras, 'N': n, 'K': k - skip, 'D': d,
              'degrees_to_use': degree}
    label = f'K={k} D={d} degree {degree} B={batches} C={cameras} N={n}' + (' packed' if packed else '') + \
        (' masks' if masked else '') + (' offsets' if offsets else '') + (f' chunk {offset}+{count}' if chunk else '')
    return Case(shapes, inputs, values, label, omit=omit)


def cases(skip):
    out, seed = [], 0
    for k, d, degree in ((1, 3, 0), (4, 1, 1), (9, 2, 2), (16, 3, 3), (16, 3, 1), (25, 3, 4), (25, 5, 2), (16, 4, 0)):
        if skip and k == 1:
            continue
        for packed in (False, True):
            seed += 1
            out.append(scene(seed, skip, k, d, degree, 2, 3, 37, packed, masked=seed % 2 == 0, offsets=seed % 3 == 0))
    out.append(scene(99, skip, 16, 3, 3, 2, 2, 50, False, True, True, chunk=(20, 17)))  # a device's chunk
    return out


def written(case):
    """(output element, channel) mask of the colours the kernel writes: its chunk's gaussians, unmasked."""
    import numpy as np
    v = case.values
    outputs, d = case.shapes['outputs'][0], v['D']
    keep = np.ones(outputs, bool)
    if 'batch_ids' in case.omit:  # dense: output_id = image * N + gaussian
        gaussian = np.arange(outputs) % v['N']
        keep &= (gaussian >= v['gaussian_offset']) & (gaussian < v['gaussian_offset'] + v['gaussian_count'])
    if 'masks' not in case.omit:
        keep &= case.inputs['masks'].astype(bool)
    return keep


def check(test, got, want, case, skip, degree=None):
    import numpy as np
    from differential.compare import compare
    from qpel_types import MATH_ULPS
    keep = written(case)
    d = case.values['D']
    a, b = (np.asarray(x['colors']).reshape(-1)[:keep.size * d].reshape(-1, d)[keep] for x in (want, got))
    if degree is None:
        test.assertIsNone(compare({'colors': a.reshape(-1)}, {'colors': b.reshape(-1)}, ['colors']), case.label)
        return
    # the bound of test_gsplat_spherical_harmonics.check: 2 MATH_ULPS epsilons x (degree + 1) x sum |c_k| max |Y_k|
    outputs, k = keep.size, case.values['K']
    rows = np.arange(outputs) if 'batch_ids' not in case.omit else np.arange(outputs) % case.values['N']
    coeffs = np.asarray(case.inputs['coeffs'], np.float64).reshape(-1, k, d)[rows][keep][:, :(degree + 1) ** 2 - skip]
    bound = 2 * MATH_ULPS * float(np.finfo(np.float32).eps) * (degree + 1) * 1.1 * np.abs(coeffs).sum(1)
    excess = np.abs(a.astype(np.float64) - b.astype(np.float64)) - bound
    if excess.size:
        test.assertLessEqual(float(excess.max()), 0.0, f'{case.label}: worst {float((excess + bound).max()):.3g} '
                                                        f'beyond its bound by {float(excess.max()):.3g}')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatGenericSphericalHarmonics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-sh-generic-')
        cls.built = {}
        for key, (source, kernel, _) in KERNELS.items():
            work = Path(cls.tmp.name)/key
            work.mkdir()
            cls.built[key] = Built(source, kernel, work)
        cls.cases = {key: cases(skip) for key, (_, _, skip) in KERNELS.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        for key, built in self.built.items():
            text = print_program(built.program)
            self.assertIn('elements: shape3', text, key)  # the computed, conditional count's shape
            self.assertIn('optional batch_ids: buffer<i64, read>', text, key)

    def test_cpu_simulator(self):
        for key, built in self.built.items():
            for case in self.cases[key]:
                with self.subTest(kernel=key, case=case.label):
                    check(self, built.ours.run(case), built.original.run(case), case, KERNELS[key][2])

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for key, built in self.built.items():
            host = matrix.load_host(built.generated)
            for case in self.cases[key]:
                with self.subTest(kernel=key, case=case.label):
                    degree = case.values['degrees_to_use']
                    check(self, metal.run_generated(host, built.plan, case), built.original.run(case), case,
                          KERNELS[key][2], degree=degree or None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
