"""The behaviour harness: CPU simulation of CUDA originals and the comparison itself."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
try:
    import numpy as np
except ImportError:
    np = None

if np is not None:
    from differential import cases, matrix
    from differential.simulate import Program, Case, evaluate
from frontends.c_import import import_source
from qpel_ir import Lowering

THRESHOLD = (HERE/'cuda_examples/threshold.cu').read_text()


@unittest.skipUnless(np is not None and shutil.which('clang++') and shutil.which('clang'), 'NumPy and clang required')
class Simulator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def original(self, text, plan_from=THRESHOLD):
        """Simulate `text` as the original, with the plan qpel derives from `plan_from`."""
        (self.tmp/'truth.cu').write_text(plan_from)
        program, _ = import_source(self.tmp/'truth.cu')
        plan = Lowering(program).plan()
        (self.tmp/'k.cu').write_text(text)
        _, report = import_source(self.tmp/'k.cu')
        (self.tmp/'sim').mkdir(exist_ok=True)
        return Program(self.tmp/'k.cu', plan, self.tmp/'sim', original=report), plan

    def test_a_wrong_translation_is_caught(self):
        simulated, plan = self.original(THRESHOLD.replace('>=', '>'))
        (self.tmp/'q').mkdir()
        program, _ = import_source(HERE/'cuda_examples/threshold.cu')
        from qpel import compile_program
        (self.tmp/'p.json').write_text(json.dumps(program))
        compile_program(self.tmp/'p.json', self.tmp/'pkg')
        qpel = Program(self.tmp/'pkg/package/generated/threshold.cu', plan, self.tmp/'q')
        value = np.arange(256, dtype=np.uint8)
        case = Case({'dimensions': (256, 1, 1)}, {'intensity': value, 'cutoff': value})
        difference = matrix.compare(simulated.run(case), qpel.run(case), ['binary'])
        self.assertRegex(difference, r'^binary\[0\]: original 0, qpel 1 \(256 differ\)$')

    def test_undefined_behaviour_in_an_original_is_reported(self):
        source = THRESHOLD.replace('binary[i] =', 'int big = 2147483647; big += (int)intensity[i] + 1; binary[i] =')
        simulated, _ = self.original(source)
        case = Case({'dimensions': (4, 1, 1)}, {'intensity': np.ones(4, np.uint8), 'cutoff': np.ones(4, np.uint8)})
        with self.assertRaisesRegex(RuntimeError, 'overflow'):
            simulated.run(case)

    def test_grid_expressions(self):
        self.assertEqual(evaluate('((W + 1) / 2 + 16 - 1) / 16', {'W': 33}), 2)
        self.assertEqual(evaluate('(W + 16) / 16', {'W': 16}), 2)
        with self.assertRaises(ValueError):
            evaluate('__import__("os")', {})

    def test_float_comparison_is_bitwise(self):
        a = {'y': np.array([0.0, 1.0], np.float32)}
        self.assertIsNotNone(matrix.compare(a, {'y': np.array([-0.0, 1.0], np.float32)}, ['y']))
        self.assertIsNone(matrix.compare(a, {'y': np.array([0.0, 1.0], np.float32)}, ['y']))

    def test_every_original_is_in_the_matrix(self):
        names = {(name, dialect) for name, _, dialect, *_ in matrix.sources()}
        self.assertIn(('sam2_cc', 'cuda'), names)
        self.assertIn(('sam2_cc', 'metal'), names)
        programs = sum(1 for _ in matrix.roundtrip.corpus_programs())
        self.assertEqual(sum(1 for _ in matrix.sources()), 2*len(matrix.roundtrip.EXAMPLES) + 2 + programs)


if __name__ == '__main__':
    unittest.main(verbosity=2)
