"""IR version 18: unaligned views and vectors of 8- and 16-bit integers. examples/unaligned_bytes.qpel reads packed
7-byte records (a u16 at byte 1, a u32 at byte 3 of each) and writes a u32 back at byte 3, through views that
are read and written byte by byte, and passes a vec<u8, 2> out of a helper; on the CPU simulator, the Apple GPU
and GPGPU-Sim against NumPy. The rules: version 18 is required, an unaligned view is indexed directly (after + or
- at most), and the narrow vectors are not shared-memory elements."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_types import is_valid  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

TEXT = (HERE/'examples/unaligned_bytes.qpel').read_text()
COUNT = 1000


def case():
    import numpy as np
    from differential.simulate import Case
    records = np.random.default_rng(18).integers(0, 256, 7 * COUNT, dtype=np.uint8)
    return Case({'n': (COUNT, 1, 1), 'bytes': (7 * COUNT, 1, 1)}, {'records': records}, {}, 'records'), records


def check(test, got, records):
    import numpy as np
    r = records.reshape(COUNT, 7).astype(np.uint64)
    small = r[:, 1] | r[:, 2] << 8
    word = r[:, 3] | r[:, 4] << 8 | r[:, 5] << 16 | r[:, 6] << 24
    want = (word + small + r[:, 0] * 65536 + r[:, 6]) % 2**32
    np.testing.assert_array_equal(np.asarray(got['wide'], np.uint64)[:COUNT], want)
    copied = np.asarray(got['copied'], np.uint8)[:7 * COUNT].reshape(COUNT, 7)[:, 3:7].astype(np.uint64)
    stored = copied[:, 0] | copied[:, 1] << 8 | copied[:, 2] << 16 | copied[:, 3] << 24
    np.testing.assert_array_equal(stored, word ^ 0xAAAAAAAA)


class Rules(unittest.TestCase):
    def test_version_18(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 18)
        self.assertEqual(print_program(program), TEXT[TEXT.index('module'):])
        program.update(version=17, semantics='qpel-portable-core-v17')
        with self.assertRaisesRegex(ValueError, 'IR version 17 cannot use: unaligned view, vector of 8- or 16-bit integers'):
            validate(program)

    def test_indexed_directly(self):
        text = TEXT.replace('let word: u32 = view_unaligned<u32>(records + (at + 3u))[0u];',
                            'let words: buffer<u32, read> = view_unaligned<u32>(records + (at + 3u));\n    let word: u32 = words[0u];')
        with self.assertRaisesRegex(ValueError, 'An unaligned view is indexed directly'):
            validate(parse_program(text))

    def test_not_shared(self):
        self.assertTrue(is_valid('shared<vec<u32, 2>, 8>'))
        self.assertFalse(is_valid('shared<vec<u8, 2>, 8>'))
        self.assertTrue(is_valid('array<vec<u8, 2>, 8>'))


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-unaligned-')
        work = Path(cls.work.name)
        (work/'p.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        c, records = case()
        check(self, Program(self.generated/'unaligned_bytes.cu', self.plan, Path(self.work.name)/'sim').run(c), records)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        c, records = case()
        check(self, metal.run_generated(matrix.load_host(self.generated), self.plan, c), records)

    def test_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-unaligned-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'unaligned_bytes.cu', self.plan, root/'qpel')
            c, records = case()
            folder = program.work/'c000'
            folder.mkdir()
            program.write_case(c, folder)
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            got, error = program.outcome(folder, c)
            self.assertIsNone(error)
            check(self, got, records)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
