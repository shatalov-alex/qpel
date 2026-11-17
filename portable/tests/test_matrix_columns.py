"""Column-major matrix memory (IR version 15): qpel.matrix_load_columns and qpel.matrix_store_columns read
and write element (r, c) at base[offset + c stride + r] (Metal's transposed simdgroup_load and store; on
CUDA each lane's two elements one stride apart). examples/matrix_columns.qpel multiplies a row-major A by a
column-major B into a column-major C: the CPU simulator bit for bit in k order, GPGPU-Sim (f32 bit for bit,
f16 within the bound) and the Apple GPU (within the bound)."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program, print_program  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402
from test_matrix_tiles import gemm  # noqa: E402

TEXT = (HERE/'examples/matrix_columns.qpel').read_text()
BODY = TEXT[TEXT.index('module'):]
SHAPES = ((8, 8, 8), (16, 24, 32), (64, 32, 40), (40, 8, 128))  # (N, M, K)


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(15)
    out = []
    for n in SHAPES:
        length = n[0] * n[1] * n[2]
        inputs = {'a': rng.uniform(-1, 1, length).astype(np.float32), 'b': rng.uniform(-1, 1, length).astype(np.float32),
                  'a16': rng.uniform(-2, 2, length).astype(np.float16), 'b16': rng.uniform(-2, 2, length).astype(np.float16)}
        out.append(Case({'n': n}, inputs, {}, f'n={n}'))
    return out


def oracle(case, which):
    """(C column-major, bound): B arrives column-major, so its row-major form is B^T read back."""
    import numpy as np
    columns, rows, depth = case.shapes['n']
    a, b = (case.inputs['a'], case.inputs['b']) if which == 'c' else (case.inputs['a16'], case.inputs['b16'])
    b_rows = np.ascontiguousarray(b[:columns * depth].reshape(columns, depth).T).reshape(-1)
    want, bound = gemm(a, b_rows, np.zeros(rows * columns, np.float32), rows, columns, depth)
    as_columns = lambda x: np.ascontiguousarray(x.reshape(rows, columns).T).reshape(-1)
    return as_columns(want), as_columns(bound)


class Rules(unittest.TestCase):
    def test_version_15_and_text(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 15)
        self.assertEqual(print_program(program), BODY)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=14, semantics='qpel-portable-core-v14')
        with self.assertRaisesRegex(ValueError, 'IR version 14 cannot use: column-major matrix memory'):
            validate(program)
        row_major = parse_program(TEXT.replace('_columns(', '('))
        self.assertEqual(row_major['version'], 13)
        self.assertFalse(any(n['attributes'].get('columns') for n in row_major['nodes']))


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-matrix-columns-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def check(self, got, case, exact):
        import numpy as np
        rows, columns = case.shapes['n'][1], case.shapes['n'][0]
        for name in ('c', 'c16'):
            want, bound = oracle(case, name)
            have = np.asarray(got[name][:rows * columns])
            if exact and name == 'c':
                np.testing.assert_array_equal(have.view(np.uint32), want.view(np.uint32), err_msg=f'{case.label} {name}')
            error = np.abs(have.astype(np.float64) - want)
            self.assertTrue(np.all(error <= bound), f'{case.label} {name}: worst {np.max(error / np.maximum(bound, 1e-30)):.2f} of the bound')

    def test_spellings(self):
        cuda = (self.generated/'matrix_columns.cu').read_text()
        metal = (self.generated/'matrix_columns.metal').read_text()
        self.assertIn('QPEL_MATRIX_LOAD_COLUMNS_F16(b, ', cuda)
        self.assertIn('QPEL_MATRIX_STORE_COLUMNS_F32(sum, c, ', cuda)
        self.assertIn('simdgroup_load(m, base + offset, (ulong)stride, ulong2(0), true)', metal)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'matrix_columns.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases():
            got = program.run(case)
            self.check(got, case, exact=True)
            rows, columns = case.shapes['n'][1], case.shapes['n'][0]
            want, _ = oracle(case, 'c16')
            np.testing.assert_array_equal(got['c16'][:rows * columns].view(np.uint32), want.view(np.uint32), err_msg=case.label)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in cases():
            self.check(metal.run_generated(host, self.plan, case), case, exact=False)

    def test_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-matrix-columns-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'matrix_columns.cu', self.plan, root/'qpel')
            pending = []
            for number, case in enumerate(cases()):
                folder = program.work/f'c{number:03}'
                folder.mkdir()
                program.write_case(case, folder)
                pending.append((case, folder))
            run_all(root, [root, HERE, Path(self.work.name).resolve()])
            for case, folder in pending:
                got, error = program.outcome(folder, case)
                self.assertIsNone(error, case.label)
                self.check(got, case, exact=True)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
