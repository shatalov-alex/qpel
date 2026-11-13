"""Benchmarks v0: a Metal original and qpel's Metal timed on the same inputs at a scaled size."""
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import unavailable  # noqa: E402


class Bench(unittest.TestCase):
    def setUp(self):
        try:
            from differential import metal
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch and NumPy required for the benchmarks')
        if not metal.available():
            unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')

    def bench(self, name, size):
        from differential import bench, matrix
        source = next(s for s in matrix.sources() if s[0] == name and s[2] == 'metal')
        with tempfile.TemporaryDirectory() as folder:
            return bench.bench_source(source, Path(folder), size)

    def test_programs_with_their_own_case_rules_scale(self):
        for name in ('grid_stride', 'sam2_cc'):  # n within the buffers; even mask sizes
            result = self.bench(name, (256, 64, 2))
            self.assertIn('ratio', result, result)
            self.assertGreater(result['original_ms'], 0)

    def test_byte_row_programs_scale_by_element(self):  # llama.cpp: W counts bytes of f32 rows
        result = self.bench('llama_metal_unary_relu_f32', (256, 8, 2))
        self.assertIn('ratio', result, result)
        self.assertEqual(result['shape'], [1024, 8, 2])


if __name__ == '__main__':
    unittest.main(verbosity=2)
