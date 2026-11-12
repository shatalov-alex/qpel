"""Behaviour rows for every original source: one test per source (differential/matrix.py).

Each test is one original (a hand-written example, SAM 2, or a pinned llama.cpp program):
the unmodified original runs against qpel's CUDA on the CPU simulator, and against qpel's
Metal on the Apple GPU when there is one. Sources are prepared in parallel worker processes
when the class starts; each test then reports its own source, one subtest per row.

With QPEL_NVIDIA=1 the same sources also run on GPGPU-Sim (one container, minutes).
"""
from collections import defaultdict
from pathlib import Path
import shutil
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import NVIDIA, unavailable  # noqa: E402

try:
    import numpy as np  # noqa: F401
except ImportError:
    np = None
READY = np is not None and shutil.which('clang') and shutil.which('clang++')
if READY:
    from differential import matrix

SOURCES = matrix.sources() if READY else []


def check(case, rows):
    """Subtests per row kind: failures listed, all-skipped rows reported as skips."""
    grouped = defaultdict(list)
    for row, label, error in rows:
        grouped[row].append((label, error))
    case.assertTrue(grouped, 'no rows ran')
    for row, results in grouped.items():
        with case.subTest(row=row):
            failed = [f'{label}: {error}' for label, error in results if error and not error.startswith('skipped')]
            if failed:
                case.fail(f'{len(failed)} of {len(results)} cases differ:\n' + '\n'.join(failed[:5]))
            skipped = [error for _, error in results if error and error.startswith('skipped')]
            if skipped and len(skipped) == len(results):
                needs = 'metal' if 'Metal toolchain' in skipped[0] else 'gpu' if 'GPU' in skipped[0] else 'nvidia'
                unavailable(case, needs, skipped[0].removeprefix('skipped: '))


@unittest.skipUnless(READY, 'NumPy and clang required')
class BehaviourRows(unittest.TestCase):
    executor = futures = None

    @classmethod
    def setUpClass(cls):
        cls.executor, cls.futures = matrix.parallel()

    @classmethod
    def tearDownClass(cls):
        cls.executor.shutdown(cancel_futures=True)

    def test_every_source_has_a_test(self):
        names = [n for n in dir(self) if n.startswith('test_') and n != 'test_every_source_has_a_test']
        self.assertEqual(len(names), len(SOURCES))


def source_test(index):
    def test(self):
        check(self, self.futures[index].result()[index])
    return test


seen = set()
for number, (name, path, dialect, *_rest) in enumerate(SOURCES):
    method = f'test_{dialect}_{name}'
    if method in seen:  # a second original of the same program in the same language
        method += '_' + path.stem
    seen.add(method)
    test = source_test(number)
    test.__doc__ = f'{path.relative_to(HERE)} ({dialect}) as {name}'
    setattr(BehaviourRows, method, test)


@unittest.skipUnless(READY, 'NumPy and clang required')
class GpgpuSimRows(unittest.TestCase):
    """The same sources as NVIDIA executes them, on GPGPU-Sim (slow: QPEL_NVIDIA=1)."""

    def test_every_source_on_gpgpu_sim(self):
        if not NVIDIA:
            self.skipTest('GPGPU-Sim rows are slow; set QPEL_NVIDIA=1 (check_all.py --nvidia)')
        from differential import gpgpusim
        if not gpgpusim.image_ready():
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        by_source = defaultdict(list)
        for row, label, error in gpgpusim.rows():
            source = label.split(' ')[0] + (' ' + label.rsplit(' ', 1)[1] if label.endswith(']') else '')
            by_source[source].append((row, label, error))
        for source, rows in sorted(by_source.items()):
            with self.subTest(source=source):
                check(self, rows)


if __name__ == '__main__':
    unittest.main(verbosity=2)
