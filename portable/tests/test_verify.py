"""qpel verify: a port checked against its original, whether or not the port imports to qpel."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import unavailable  # noqa: E402

CUDA, METAL = HERE/'cuda_examples', HERE/'metal_examples'


def apple_gpu(test):
    try:
        from differential import metal
    except ImportError:
        unavailable(test, 'gpu', 'PyTorch and NumPy required to run Metal')
    if not metal.available():
        unavailable(test, 'gpu', 'no Apple GPU (PyTorch MPS)')


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Signatures(unittest.TestCase):
    """The port's signature, read without importing it, mapped onto the original's."""

    def setUp(self):
        from differential import verify
        from frontends.c_import import import_source
        from qpel_ir import Lowering
        self.verify = verify
        program, self.report = import_source(CUDA/'scale_offset.cu')
        self.plan = Lowering(program).plan()

    def port(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name)/'port.metal'
        path.write_text(text)
        return path

    def test_metal_arguments_take_the_original_roles(self):
        report = self.verify.port_report(METAL/'scale_offset.metal', self.report, self.plan)
        arguments = report['arguments']['scale_offset']
        self.assertEqual([a['name'] for a in arguments], ['x', 'y', 'z', 'scale', 'offset', 'n'])
        self.assertEqual([a['binding'] for a in arguments], [0, 1, 2, 3, 4, 5])
        self.assertEqual(arguments[-1]['source'], 'volume')  # the count, as in the original

    def test_a_port_with_constructs_qpel_does_not_import_still_maps(self):
        source = (METAL/'scale_offset.metal').read_text().replace(
            '    if (i < n) {', '    threadgroup float unused[4]; unused[i % 4] = 0;\n    if (i < n) {')
        report = self.verify.port_report(self.port(source), self.report, self.plan)
        self.assertEqual(len(report['arguments']['scale_offset']), 6)

    def test_signatures_that_do_not_map_are_refused(self):
        source = (METAL/'scale_offset.metal').read_text()
        with self.assertRaisesRegex(self.verify.PortError, 'argument 3 \\(scale\\) is i32; the original.s \\(scale\\) is f32'):
            self.verify.port_report(self.port(source.replace('constant float& scale', 'constant int& scale')),
                                    self.report, self.plan)
        with self.assertRaisesRegex(self.verify.PortError, 'takes 5 arguments'):
            self.verify.port_report(self.port(source.replace('                         constant int& offset [[buffer(4)]],\n', '')
                                              .replace('int(i) * offset', 'int(i)').replace('buffer(5)', 'buffer(4)')),
                                    self.report, self.plan)
        with self.assertRaisesRegex(self.verify.PortError, 'use --map scale_offset=PORT_KERNEL'):
            self.verify.port_report(self.port(source.replace('void scale_offset', 'void affine')), self.report, self.plan)
        renamed = self.verify.port_report(self.port(source.replace('void scale_offset', 'void affine')),
                                          self.report, self.plan, mapping={'scale_offset': 'affine'})
        self.assertEqual(renamed['qualified']['scale_offset'], 'affine')


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Runs(unittest.TestCase):
    def setUp(self):
        apple_gpu(self)
        from differential import verify
        self.verify = verify

    def test_hand_ports_agree_both_ways(self):
        for name in ('scale_offset', 'grid_stride', 'math_ops'):
            for original, port in ((CUDA/f'{name}.cu', METAL/f'{name}.metal'), (METAL/f'{name}.metal', CUDA/f'{name}.cu')):
                result = self.verify.verify(original, port)
                self.assertTrue(result['passed'], (original.name, [c for c in result['cases'] if len(c) > 1]))
        self.assertGreater(result['ulps'], 0)  # math_ops calls bounded math functions

    def test_a_wrong_port_is_reported_with_its_first_difference(self):
        with tempfile.TemporaryDirectory() as folder:
            port = Path(folder)/'scale_offset.metal'
            port.write_text((METAL/'scale_offset.metal').read_text().replace('- 7;', '- 8;'))
            report = Path(folder)/'report.json'
            from differential.verify import main
            self.assertEqual(main([str(CUDA/'scale_offset.cu'), str(port), '--json', str(report)]), 1)
            result = json.loads(report.read_text())
        self.assertFalse(result['passed'])
        self.assertIn('z[0]: original -7, port -8', result['cases'][-1]['difference'])

    def test_edge_values_find_what_random_values_miss(self):
        with tempfile.TemporaryDirectory() as folder:
            port = Path(folder)/'scale_offset.metal'  # NaN inputs become 0: invisible to ordinary values
            port.write_text((METAL/'scale_offset.metal').read_text().replace('x[i] * scale', '(isnan(x[i]) ? 0.0f : x[i]) * scale'))
            plain = self.verify.verify(CUDA/'scale_offset.cu', port, edges=False)
            self.assertTrue(plain['passed'])
            edged = self.verify.verify(CUDA/'scale_offset.cu', port)
        self.assertFalse(edged['passed'])
        failing = [c['case'] for c in edged['cases'] if 'difference' in c]
        self.assertTrue(failing and all(label.endswith('edge values') for label in failing), failing)

    def test_a_port_that_cannot_run_exits_2(self):
        from differential.verify import main
        self.assertEqual(main([str(CUDA/'scale_offset.cu'), str(METAL/'threshold.metal')]), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
