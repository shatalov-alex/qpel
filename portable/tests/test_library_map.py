"""frontends/library_map.py: every library and runtime API the corpus calls is classified, and the
recorded corpus/library_map.json matches the corpus."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontends import library_map  # noqa: E402


class LibraryMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = library_map.result()

    def test_recorded(self):
        self.assertEqual(json.loads(library_map.RESULT.read_text()), self.entries,
                         'corpus/library_map.json is out of date: python3 -m frontends.library_map --update')

    def test_everything_classified(self):
        self.assertEqual([name for name, entry in self.entries.items() if entry['status'] == 'unclassified'], [])

    def test_cub_status_follows_qpel(self):
        cub = library_map.cub_defined()
        self.assertEqual(library_map.classify('cub::DeviceScan::InclusiveSum', cub)[0], 'library')
        self.assertEqual(library_map.classify('cub::BlockScan', cub)[0], 'library')
        self.assertEqual(library_map.classify('cub::DeviceHistogram::HistogramEven', cub)[0], 'unmapped')
        self.assertEqual(library_map.classify('cub::NoSuchHelper', cub)[0], 'unmapped')
        self.assertEqual(library_map.classify('cudaNoSuchCall')[0], 'unclassified')

    def test_comments_and_includes_do_not_count(self):
        text = '#include <cublas_v2.h>\n// cudaMalloc(&p, n);\n/* cublasGemmEx */ cudaFree(p);\n'
        found = [match.group(1) for match in library_map.IDENTIFIER.finditer(library_map.STRIPPED.sub('', text))]
        self.assertEqual(found, ['cudaFree'])


if __name__ == '__main__':
    unittest.main()
