"""qpel verify: a port checked against its original, whether or not the port imports to qpel."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_test_support import NVIDIA, unavailable  # noqa: E402

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
        dropped = self.verify.port_report(self.port(source.replace('                         constant int& offset [[buffer(4)]],\n', '')
                                                    .replace('int(i) * offset', 'int(i)').replace('buffer(5)', 'buffer(4)')),
                                          self.report, self.plan)
        self.assertEqual(dropped['unused'], {'scale_offset': ['offset']})  # reported; the cases then differ
        renamed = source.replace('& scale', '& k').replace('* scale', '* k')
        with self.assertRaisesRegex(self.verify.PortError, "argument 4 \\(m\\) is f32; the original's \\(offset\\) is i32"):
            self.verify.port_report(self.port(renamed.replace('constant int& offset', 'constant float& m')
                                              .replace('* offset', '* int(m)')), self.report, self.plan)
        with self.assertRaisesRegex(self.verify.PortError, 'takes 5 arguments .* k match none by name'):
            self.verify.port_report(self.port(renamed.replace('                         constant int& offset [[buffer(4)]],\n', '')
                                              .replace('int(i) * offset', 'int(i)').replace('buffer(5)', 'buffer(4)')),
                                    self.report, self.plan)
        with self.assertRaisesRegex(self.verify.PortError, 'use --map scale_offset=PORT_KERNEL'):
            self.verify.port_report(self.port(source.replace('void scale_offset', 'void affine')), self.report, self.plan)
        renamed = self.verify.port_report(self.port(source.replace('void scale_offset', 'void affine')),
                                          self.report, self.plan, mapping={'scale_offset': 'affine'})
        self.assertEqual(renamed['qualified']['scale_offset'], 'affine')


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Arguments(unittest.TestCase):
    """Ports whose arguments are named, ordered or packed differently from the original's."""

    def setUp(self):
        from differential import verify
        from frontends.c_import import import_source
        from qpel_ir import Lowering
        self.verify = verify
        program, self.report = import_source(CUDA/'scale_offset.cu')
        self.plan = Lowering(program).plan()

    def port(self, text, suffix='.metal'):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name)/('port' + suffix)
        path.write_text(text)
        return path

    def test_same_names_in_another_order(self):
        report = self.verify.port_report(self.port(REORDERED), self.report, self.plan)
        self.assertEqual([(a['name'], a['binding']) for a in report['arguments']['scale_offset']],
                         [('n', 0), ('scale', 1), ('offset', 2), ('z', 3), ('y', 4), ('x', 5)])
        self.assertEqual([a.get('parameter') for a in report['arguments']['scale_offset']],
                         ['n', 'scale', 'offset', 'z', 'y', 'x'])

    def test_a_struct_is_filled_from_the_original_scalars(self):
        port = self.verify.Port(self.port(STRUCT_PORT), self.report, self.plan, arguments={'args.bias': 'offset * 2 + W'})
        self.assertEqual(port.mapped['scale_offset'][0]['from'],
                         'struct Args: scale, offset, n, bias=offset * 2 + W; zero: unused')
        from differential.simulate import Case
        case = port.adapt_case(Case({'n': (5, 1, 1)}, {}, {'scale': 1.5, 'offset': -3}))
        self.assertEqual(case.values['qpel_port_0_args'], {'scale': 1.5, 'offset': -3, 'n': 5, 'bias': -1, 'unused': 0})

    def test_expressions_and_conversions(self):
        port = self.verify.Port(self.port(REORDERED.replace('constant uint& n', 'constant long& count').replace('i < n', 'i < count')), self.report,
                                self.plan, arguments={'offset': 'offset - 1'})
        self.assertEqual([m['from'] for m in port.mapped['scale_offset']][:3], ['n (converted from u32)', 'scale', 'offset - 1'])
        with self.assertRaisesRegex(self.verify.PortError, 'not an argument of the original'):
            from differential.simulate import Case
            self.verify.Port(self.port(REORDERED), self.report, self.plan,
                             arguments={'offset': 'nope + 1'}).adapt_case(Case({'n': (5, 1, 1)}, {}, {'scale': 1.0, 'offset': 1}))


REORDERED = """#include <metal_stdlib>
using namespace metal;
kernel void scale_offset(constant uint& n [[buffer(0)]], constant float& scale [[buffer(1)]],
                         constant int& offset [[buffer(2)]], device int* z [[buffer(3)]],
                         device float* y [[buffer(4)]], device const float* x [[buffer(5)]],
                         uint i [[thread_position_in_grid]]) {
    if (i < n) {
        y[i] = x[i] * scale;
        z[i] = int(i) * offset - 7;
    }
}
"""

# llama.cpp's style: the scalars in one constant struct, the buffers as bytes.
STRUCT_PORT = """#include <metal_stdlib>
using namespace metal;
typedef struct { float scale; int offset; uint n; int bias; int unused; } Args;
kernel void scale_offset(constant Args& args [[buffer(0)]], device const char* src [[buffer(1)]],
                         device char* dst [[buffer(2)]], device int* z [[buffer(3)]],
                         uint i [[thread_position_in_grid]]) {
    if (i < args.n) {
        ((device float*)dst)[i] = ((device const float*)src)[i] * args.scale;
        z[i] = int(i) * args.offset - 7 + (args.bias - (args.offset * 2 + int(args.n)));
    }
}
"""

# The other way: a Metal original with an argument struct, ported to CUDA scalars.
STRUCT_ORIGINAL = """#include <metal_stdlib>
using namespace metal;
typedef struct { float scale; int offset; } Args;
kernel void scale_offset(device const float* x [[buffer(0)]], device float* y [[buffer(1)]],
                         device int* z [[buffer(2)]], constant Args& args [[buffer(3)]],
                         constant uint& n [[buffer(4)]], uint i [[thread_position_in_grid]]) {
    if (i < n) {
        y[i] = x[i] * args.scale;
        z[i] = int(i) * args.offset - 7;
    }
}
"""


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

    def test_packed_and_reordered_ports_agree(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, text in (('reordered', REORDERED), ('struct', STRUCT_PORT)):
                port = Path(folder)/f'{name}.metal'
                port.write_text(text)
                result = self.verify.verify(CUDA/'scale_offset.cu', port, arguments={'args.bias': 'offset * 2 + n'})
                self.assertTrue(result['passed'], (name, [c for c in result['cases'] if len(c) > 1]))
            original = Path(folder)/'struct_original.metal'
            original.write_text(STRUCT_ORIGINAL)
            result = self.verify.verify(original, CUDA/'scale_offset.cu')  # n, scale, offset: the struct's fields
            self.assertTrue(result['passed'], [c for c in result['cases'] if len(c) > 1])
            self.assertEqual([m['from'] for m in result['arguments']['scale_offset']][3:], ['args.scale', 'args.offset', 'n'])
            wrong = Path(folder)/'wrong.metal'
            wrong.write_text(STRUCT_PORT.replace('int(i) * args.offset', 'int(i) * args.offset + int(args.unused)'))
            result = self.verify.verify(CUDA/'scale_offset.cu', wrong, arguments={'args.bias': 'offset * 2 + n',
                                                                                    'args.unused': '1'})
            self.assertFalse(result['passed'])

    def test_a_port_that_cannot_run_exits_2(self):
        from differential.verify import main
        self.assertEqual(main([str(CUDA/'scale_offset.cu'), str(METAL/'threshold.metal')]), 2)


ENDLESS = """#include <metal_stdlib>
using namespace metal;
kernel void scale_offset(device const float* x [[buffer(0)]], device float* y [[buffer(1)]], device int* z [[buffer(2)]],
                         constant float& scale [[buffer(3)]], constant int& offset [[buffer(4)]],
                         constant uint& n [[buffer(5)]], uint i [[thread_position_in_grid]]) {
    uint slot = i;
    while (z[slot] != 1) { slot = (slot + 1) % n; }  // probes for a value no slot holds
}
"""


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class MetalOnCpu(unittest.TestCase):
    """--metal cpu|preflight: Metal in the CPU simulator, where a kernel that never ends times out.
    Nothing here uses the GPU."""

    def setUp(self):
        from frontends import clang_ast
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'xcrun metal required to read Metal signatures')
        try:
            from differential import verify
        except ImportError:
            unavailable(self, 'gpu', 'NumPy required')
        self.verify = verify

    def test_ports_agree_and_differ_as_on_the_gpu(self):
        for original, port in ((CUDA/'scale_offset.cu', METAL/'scale_offset.metal'),
                               (METAL/'grid_stride.metal', CUDA/'grid_stride.cu'),
                               (CUDA/'math_ops.cu', METAL/'math_ops.metal')):
            result = self.verify.verify(original, port, metal='cpu')
            self.assertTrue(result['passed'], (original.name, [c for c in result['cases'] if len(c) > 1]))
            self.assertIn('CPU simulator (Metal)', result['runs'].values())
        with tempfile.TemporaryDirectory() as folder:
            port = Path(folder)/'scale_offset.metal'
            port.write_text((METAL/'scale_offset.metal').read_text().replace('- 7;', '- 8;'))
            wrong = self.verify.verify(CUDA/'scale_offset.cu', port, metal='cpu', edges=False)
        self.assertIn('z[0]: original -7, port -8', wrong['cases'][0]['difference'])

    def test_a_port_that_never_ends_stops_before_the_gpu(self):
        with tempfile.TemporaryDirectory() as folder:
            port = Path(folder)/'endless.metal'
            port.write_text(ENDLESS)
            result = self.verify.verify(CUDA/'scale_offset.cu', port, metal='preflight', timeout=2, edges=False)
        self.assertFalse(result['passed'])
        self.assertIn('did not finish within 2 s', result['cases'][0]['error'])
        self.assertIn('the Apple GPU was not used', result['preflight'])
        self.assertEqual(result['runs']['port'], 'CPU simulator (Metal)')


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class OnNvidia(unittest.TestCase):
    """--nvidia: the CUDA sides on GPGPU-Sim (QPEL_NVIDIA=1)."""

    def setUp(self):
        if not NVIDIA:
            self.skipTest('GPGPU-Sim runs are slow; set QPEL_NVIDIA=1 (check_all.py --nvidia)')
        from differential import gpgpusim
        if not gpgpusim.image_ready(build=False):
            unavailable(self, 'nvidia', f'no Docker image {gpgpusim.IMAGE}')
        from differential import verify
        self.verify = verify

    def test_cuda_on_gpgpusim(self):
        with tempfile.TemporaryDirectory() as folder:
            port = Path(folder)/'port.cu'
            port.write_text((CUDA/'scale_offset.cu').read_text().replace('- 7;', '- 8;'))
            right = self.verify.verify(CUDA/'scale_offset.cu', CUDA/'scale_offset.cu', nvidia=True)
            wrong = self.verify.verify(CUDA/'scale_offset.cu', port, nvidia=True, edges=False)
        self.assertTrue(right['passed'], [c for c in right['cases'] if len(c) > 1])
        self.assertEqual(right['runs']['port'], 'GPGPU-Sim (CUDA, simulated NVIDIA GPU)')
        self.assertIn('z[0]: original -7, port -8', wrong['cases'][0]['difference'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
