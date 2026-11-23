"""gsplat's covariances from quaternions and scales (QuatScaleToCovarCUDA.cu, unmodified): the forward kernel
(covariance R S S^T R^T and precision R S^-1 S^-T R^T, full or upper triangle, each optional) and its gradient
kernel. They compute with glm: quat_to_rotmat builds a mat3, mat3 products, transposes, glm::make_mat3 of a
buffer, vec4 arithmetic. qpel reads glm by running glm's own code (frontends/glm.py), so each scalar is computed
as glm computes it.

The original (gsplat's CUDA with the real glm headers) runs on the CPU simulator, qpel's CUDA there and qpel's
Metal on the Apple GPU: every output agrees bit for bit."""
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
SOURCE = 'QuatScaleToCovarCUDA.cu'


class Built:
    def __init__(self, name, work):
        from frontends import roundtrip
        from frontends.c_import import import_source
        from qpel import compile_program
        from qpel_ir import Lowering, validate
        from differential.simulate import Program
        entry = next(e for e in roundtrip.corpus_entries() if e['source'].endswith(SOURCE))
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


def gaussians(rng, n):
    import numpy as np
    quats = rng.standard_normal((n, 4)).astype(np.float32)
    scales = np.exp(rng.uniform(-4, 1.5, (n, 3))).astype(np.float32)
    return quats.reshape(-1), scales.reshape(-1)


def forward_cases():
    import numpy as np
    from differential.simulate import Case
    out = []
    for seed, n in enumerate((1, 7, 300)):
        rng = np.random.default_rng(seed)
        quats, scales = gaussians(rng, n)
        for triu in (0, 1):
            for omit in ([], ['precis'], ['covars']):
                label = f'n={n} triu={triu}' + (f' without {omit[0]}' if omit else '')
                defined = {'covars': 6 * n, 'precis': 6 * n} if triu else None  # (the upper triangle: 6 of 9 per gaussian)
                out.append(Case({'N': (n, 1, 1)}, {'quats': quats, 'scales': scales}, {'offset': 0, 'triu': triu}, label,
                                defined=defined, omit=omit))
    return out


def backward_cases():
    import numpy as np
    from differential.simulate import Case
    out = []
    for seed, n in enumerate((1, 7, 300)):
        rng = np.random.default_rng(100 + seed)
        quats, scales = gaussians(rng, n)
        v_covars = rng.standard_normal(9 * n).astype(np.float32)
        v_precis = rng.standard_normal(9 * n).astype(np.float32)
        for triu in (0, 1):
            for omit in ([], ['v_precis'], ['v_covars']):
                label = f'n={n} triu={triu}' + (f' without {omit[0]}' if omit else '')
                out.append(Case({'N': (n, 1, 1)}, {'quats': quats, 'scales': scales, 'v_covars': v_covars, 'v_precis': v_precis},
                                {'offset': 0, 'triu': triu}, label, omit=omit))
    return out


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class GsplatCovariance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-gsplat-covariance-')
        root = Path(cls.tmp.name)
        cls.built = {}
        for name in ('quat_scale_to_covar_fwd', 'quat_scale_to_covar_bwd'):
            (root/name).mkdir()
            cls.built[name] = Built(name, root/name)
        cls.cases = {'quat_scale_to_covar_fwd': forward_cases(), 'quat_scale_to_covar_bwd': backward_cases()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import(self):
        from qpel_text import print_program
        text = print_program(self.built['quat_scale_to_covar_fwd'].program)
        self.assertIn('ref result: array<f32, 9>', text)  # quat_to_rotmat's mat3, into the caller's array
        self.assertIn('ref covar: array<f32, 9>, ref preci: array<f32, 9>, covar_present: u32, preci_present: u32', text)
        self.assertNotIn('operator', text)

    def check(self, got, want, outputs, case):
        from differential.compare import compare
        self.assertIsNone(compare(want, got, outputs, case.defined), case.label)

    def test_cpu_simulator(self):
        for name, built in self.built.items():
            for case in self.cases[name]:
                with self.subTest(name=name, case=case.label):
                    want = built.original.run(case)
                    self.check(built.ours.run(case), want, built.outputs, case)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for name, built in self.built.items():
            host = matrix.load_host(built.generated)
            for case in self.cases[name]:
                with self.subTest(name=name, case=case.label):
                    self.check(metal.run_generated(host, built.plan, case), built.original.run(case), built.outputs, case)


if __name__ == '__main__':
    unittest.main(verbosity=2)
