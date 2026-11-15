"""Every IR fixture on the Apple GPU against its independent CPU oracle (gpu_tests/).

The same tests `gpu_tests/run_gpu_tests.py` runs, one unittest per fixture, through the
generated Metal host. Skipped without PyTorch on an Apple GPU (QPEL_REQUIRE=gpu fails
instead). Corpus programs are left out: their Metal rows are in test_behaviour_rows (Triton kernels: test_triton).
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import unavailable  # noqa: E402
from fixture_builds import FIXTURES  # noqa: E402


def runner():
    """gpu_tests/run_gpu_tests.py as a module, or None without PyTorch and NumPy."""
    try:
        import numpy  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return None
    spec = importlib.util.spec_from_file_location('run_gpu_tests', HERE/'gpu_tests/run_gpu_tests.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = runner()
NAMES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


class AppleGpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-gpu-fixtures-')
        cls.corpus = RUNNER.corpus_fixtures() if RUNNER else set()

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        if RUNNER is None:
            unavailable(self, 'gpu', 'PyTorch and NumPy required for the Apple GPU tests')
        import torch
        if not torch.backends.mps.is_available():
            unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')

    def test_every_fixture_has_a_gpu_test(self):
        missing = [n for n in NAMES if n not in RUNNER.TESTS and n not in self.corpus]
        self.assertEqual(missing, [])


def fixture_test(name):
    def test(self):
        if name in self.corpus:
            self.skipTest('corpus program: its Metal rows run in test_behaviour_rows (Triton kernels: test_triton)')
        host, plan = RUNNER.load_host(name, Path(self.work.name))
        self.assertGreater(RUNNER.TESTS[name](name, host, plan), 0)
    test.__doc__ = f'{name} on the Apple GPU'
    return test


from frontends.roundtrip import corpus_programs, triton_kernels  # noqa: E402
CORPUS = {program['fixture'] for _, program in corpus_programs()} | {f'triton_{k}' for k in triton_kernels()}
for _name in NAMES:
    if _name not in CORPUS:  # corpus programs: their Metal rows run in test_behaviour_rows
        setattr(AppleGpu, f'test_{_name}', fixture_test(_name))


if __name__ == '__main__':
    unittest.main(verbosity=2)
