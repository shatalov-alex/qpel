"""Every program the fuzzer once failed on (fuzz/regressions/), one test each.

Each must print back to the same text, survive qpel -> CUDA/Metal -> qpel, and give the
same outputs from its CUDA on the CPU simulator and its Metal on the Apple GPU (when there
is one). The ones found on GPGPU-Sim also run there with QPEL_NVIDIA=1, in one container.
"""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import NVIDIA, unavailable  # noqa: E402

try:
    import numpy as np  # noqa: F401
except ImportError:
    np = None
READY = np is not None and shutil.which('clang') and shutil.which('clang++')
REGRESSIONS = sorted((HERE/'fuzz/regressions').glob('*.qpel'))


def text_of(path):
    from qpel_text import parse_program, print_program
    return print_program(parse_program(path.read_text()))


@unittest.skipUnless(READY, 'NumPy and clang required')
class Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from differential.matrix import gpu_available
        cls.gpu = gpu_available()

    def test_there_are_regressions(self):
        self.assertGreaterEqual(len(REGRESSIONS), 7)

    def test_on_gpgpu_sim(self):
        """The regressions as NVIDIA executes them (slow: QPEL_NVIDIA=1)."""
        if not NVIDIA:
            self.skipTest('GPGPU-Sim rows are slow; set QPEL_NVIDIA=1 (check_all.py --nvidia)')
        from differential import gpgpusim
        from fuzz.__main__ import check
        from differential.matrix import compare
        if not gpgpusim.image_ready():
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        self.assertIsNone(gpgpusim.selftest(), 'GPGPU-Sim self-test')
        root = Path(tempfile.mkdtemp(prefix='qpel-regressions-nvidia-')).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        pending = []
        for number, path in enumerate(REGRESSIONS):
            with tempfile.TemporaryDirectory() as work:
                self.assertIsNone(check(text_of(path), 0, Path(work), False, (root/f'p{number:02}', pending)), path.name)
        gpgpusim.run_all(root, [root, HERE])
        for target, folder, expected, outputs, size in pending:
            with self.subTest(program=target.source.parent.name, size=size):
                actual, error = target.outcome(folder)
                self.assertIsNone(error or compare(expected, actual, outputs))


def regression_test(path):
    def test(self):
        from fuzz.__main__ import check
        with tempfile.TemporaryDirectory(prefix='qpel-regression-') as work:
            result = check(text_of(path), 0, Path(work), self.gpu)
        self.assertIsNone(result, f'{path.name}: failed at {result and result[0]}')
    test.__doc__ = f'fuzz/regressions/{path.name}'
    return test


for _path in REGRESSIONS:
    setattr(Regressions, f'test_{_path.stem}', regression_test(_path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
