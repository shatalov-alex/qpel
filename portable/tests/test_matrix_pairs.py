"""qpel_opt.matrix_pairs: two f16 multiply chains with the same B become one mma.sync.m16n8k8 per step
on CUDA (the second A in rows 8 to 15), as NVIDIA's WMMA does for a 16 x 16 fragment. The facts, the
statements they may not move across, and the generated code; the WMMA tests (test_wmma.py) run it on
the CPU simulator, the Apple GPU and GPGPU-Sim, whose mma.sync executes the paired instruction."""
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

LOOP = '''        for (let j: u32 = 0u; j < 4u; j = j + 1u) {
          sums[j] = qpel.matrix_mma(left[j / 2u], right[j % 2u], sums[j]);
        }
'''
UNROLLED = '''        sums[0u] = qpel.matrix_mma(left[0u], right[0u], sums[0u]);
        sums[1u] = qpel.matrix_mma(left[0u], right[1u], sums[1u]);
        sums[2u] = qpel.matrix_mma(left[1u], right[0u], sums[2u]);
        sums[3u] = qpel.matrix_mma(left[1u], right[1u], sums[3u]);
'''


def program(f16_body, f32_body=None):
    """matrix_blocks.qpel with the f16 kernel's (and optionally the f32 kernel's) multiply loop replaced."""
    from qpel_text import parse_program
    text = (HERE/'examples/matrix_blocks.qpel').read_text()
    first, second = text.index(LOOP), text.rindex(LOOP)
    assert first != second
    text = text[:second] + f16_body + text[second + len(LOOP):]
    if f32_body is not None:
        text = text[:first] + f32_body + text[first + len(LOOP):]
    return parse_program(text)


def pairs(p):
    from qpel_ir import Lowering
    lowering = Lowering(p)
    return lowering, lowering.matrix_pairs


class MatrixPairs(unittest.TestCase):
    def targets(self, lowering, found):
        out = []
        for first, (second, *_rest) in found.items():
            out.append(tuple(lowering.emit(lowering.child(lowering.nodes[s], 'target')) for s in (first, second)))
        return sorted(out)

    def test_row_blocks_with_the_same_b_pair(self):
        lowering, found = pairs(program(UNROLLED, UNROLLED))
        # sums[0] and sums[2] share right[0], sums[1] and sums[3] right[1]; the f32 kernel's are not paired
        self.assertEqual(self.targets(lowering, found), [('sums[0u]', 'sums[2u]'), ('sums[1u]', 'sums[3u]')])

    def test_loop_is_not_paired(self):
        self.assertEqual(pairs(program(LOOP))[1], {})

    def test_dependencies_stop_a_pair(self):
        # the second reads the first's result
        reads_first = UNROLLED.replace('qpel.matrix_mma(left[1u], right[0u], sums[2u])', 'qpel.matrix_mma(left[1u], right[0u], sums[0u])')
        lowering, found = pairs(program(reads_first))
        self.assertEqual(self.targets(lowering, found), [('sums[1u]', 'sums[3u]')])
        # a statement between writes what the second reads
        writes_operand = UNROLLED.replace('sums[1u] = qpel.matrix_mma(left[0u], right[1u], sums[1u]);',
                                          'left[1u] = left[0u];')
        self.assertEqual(pairs(program(writes_operand))[1], {})
        # a statement between reads what the second writes
        reads_target = UNROLLED.replace('qpel.matrix_mma(left[0u], right[1u], sums[1u])', 'qpel.matrix_mma(left[0u], right[1u], sums[2u])')
        lowering, found = pairs(program(reads_target))
        self.assertNotIn(('sums[0u]', 'sums[2u]'), self.targets(lowering, found))
        # a non-literal index may be any element
        dynamic = UNROLLED.replace('sums[2u] = qpel.matrix_mma(left[1u], right[0u], sums[2u]);',
                                   'sums[k / 8u % 4u] = qpel.matrix_mma(left[1u], right[0u], sums[2u]);')
        lowering, found = pairs(program(dynamic))
        self.assertNotIn('sums[0u]', [t[0] for t in self.targets(lowering, found)])

    def test_generated(self):
        import json
        import tempfile
        from qpel import compile_program
        with tempfile.TemporaryDirectory() as work:
            (Path(work)/'p.json').write_text(json.dumps(program(UNROLLED)))
            compile_program(Path(work)/'p.json', Path(work)/'build')
            body = (Path(work)/'build/package/generated/matrix_blocks.qpel.h').read_text()
        self.assertEqual(body.count('QPEL_MATRIX_MMA2_F16('), 2)
        self.assertIn('QPEL_MATRIX_PAIR_F32_OF(sums[0u], sums[2u])', body)
        self.assertIn('sums[2u] = qpel_pair_1.m[1];', body)
        for header in ('cuda.h', 'metal.h'):
            text = (HERE/'backends'/header).read_text()
            self.assertIn('#define QPEL_MATRIX_MMA2_F16(a0, a1, b, c)', text)
        self.assertIn('"r"(qpel_half_pair(a1.v[0], a1.v[1]))', (HERE/'backends/cuda.h').read_text())  # rows 8 to 15

    @unittest.skipUnless(__import__('shutil').which('clang++'), 'clang++ required')
    def test_cpu_simulator_same_bits(self):
        import json
        import tempfile
        import numpy as np
        from qpel import compile_program
        from differential.simulate import Case, Program
        from qpel_ir import Lowering
        results = []
        with tempfile.TemporaryDirectory() as work:
            for name, body in (('loop', LOOP), ('pairs', UNROLLED)):
                folder = Path(work)/name
                folder.mkdir()
                p = program(body)
                (folder/'p.json').write_text(json.dumps(p))
                compile_program(folder/'p.json', folder/'build')
                plan = Lowering(p).plan()
                (folder/'sim').mkdir()
                sim = Program(folder/'build/package/generated/matrix_blocks.cu', plan, folder/'sim')
                rng = np.random.default_rng(3)
                n, m, k = 32, 16, 24
                size = n * m * k  # (the pipeline sizes every buffer by the shape)
                inputs = {'a': rng.uniform(-2, 2, size).astype(np.float32), 'b': rng.uniform(-2, 2, size).astype(np.float32),
                          'a16': rng.uniform(-2, 2, size).astype(np.float16), 'b16': rng.uniform(-2, 2, size).astype(np.float16)}
                results.append(sim.run(Case({'n': (n, m, k)}, inputs, {}, 'n=32 m=16 k=24'))['c16'])
        np.testing.assert_array_equal(results[0], results[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
