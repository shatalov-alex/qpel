"""Metal threadgroup memory sized by the host (`threadgroup T* p [[threadgroup(n)]]`), thread
builtins read by subscript, and the pieces that run such originals: the rewrite that gives the
memory its length, template instantiations on the CPU runner, and kernel names after non-ASCII text."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from differential.metal_text import sized_threadgroups  # noqa: E402
from differential.metal_cpu import local_threadgroup  # noqa: E402

KERNEL = '''#include <metal_stdlib>
using namespace metal;
// A comment with non-ASCII text — before the kernels, as in ggml-metal.metal.
template <typename T>
kernel void swap_pairs(device T * y [[buffer(0)]], constant uint & n [[buffer(1)]],
                       threadgroup T * s [[threadgroup(0)]],
                       uint3 g [[threadgroup_position_in_grid]], uint3 t [[thread_position_in_threadgroup]],
                       uint3 b [[threads_per_threadgroup]]) {
  const uint i = g[0] * b[0] + t[0];
  s[t.x] = i < n ? y[i] : T(0);
  threadgroup_barrier(mem_flags::mem_threadgroup);
  if (i < n) y[i] = s[t.x ^ 1];
}
template [[host_name("swap_pairs_f32")]] kernel void swap_pairs<float>(
    device float * y [[buffer(0)]], constant uint & n [[buffer(1)]], threadgroup float * s [[threadgroup(0)]],
    uint3 g [[threadgroup_position_in_grid]], uint3 t [[thread_position_in_threadgroup]],
    uint3 b [[threads_per_threadgroup]]);
'''


class SizedThreadgroups(unittest.TestCase):
    def test_parameter_becomes_an_array_in_definitions_only(self):
        out = sized_threadgroups(KERNEL, {'s': 64})
        self.assertNotIn('[[threadgroup(0)]]', out)
        self.assertEqual(out.count('threadgroup T s[64];'), 1)  # in the template's body
        instantiation = out[out.index('swap_pairs<float>('):]
        instantiation = instantiation[:instantiation.index(');')]
        self.assertNotIn('threadgroup float', instantiation)  # the parameter left the declaration too
        self.assertNotIn('threadgroup float s[64]', out)  # the instantiation is a declaration

    def test_unknown_parameter(self):
        with self.assertRaisesRegex(ValueError, 'not found'):
            sized_threadgroups(KERNEL, {'missing': 8})

    def test_cpu_runner_shares_qpel_group_memory(self):
        text = local_threadgroup('#define QPEL_SHARED(T, name, n) threadgroup T name[n]\n'
                                 '#define QPEL_BARRIER_COUNT_SCRATCH threadgroup atomic_int qpel_barrier_counter;\n'
                                 '    threadgroup float tile[16];\n')
        self.assertIn('#define QPEL_SHARED(T, name, n) static T name[n]', text)
        self.assertIn('#define QPEL_BARRIER_COUNT_SCRATCH static atomic_int', text)
        self.assertIn('    static float tile[16];', text)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Import(unittest.TestCase):
    def setUp(self):
        from frontends import clang_ast
        if not clang_ast.metal_available():
            self.skipTest('no Metal toolchain (Xcode)')
        self.folder = Path(tempfile.mkdtemp())
        self.source = self.folder/'swap.metal'
        self.source.write_text(KERNEL)

    def test_needs_a_length(self):
        from frontends.c_import import import_source
        with self.assertRaisesRegex(ValueError, r"dynamic-shared.*shared=\{'s': N\}"):
            import_source(self.source, resources={'y': 'inout'})

    def test_shared_memory_of_the_given_length(self):
        from frontends.c_import import import_source
        from qpel_ir import validate
        program, report = import_source(self.source, resources={'y': 'inout'}, shared={'s': 64})
        for target in ('cuda', 'metal'):
            validate(program, target)
        shared = [n for n in program['nodes'] if n['kind'] == 'SharedType']
        self.assertEqual([(n['attributes']['element'], n['attributes']['length']) for n in shared], [('f32', 64)])
        self.assertEqual(report['shared'], {'s': 64})
        # The kernel is named by its host_name, read at clang's byte offset past the non-ASCII comment.
        self.assertEqual(report['qualified'], {'swap_pairs_f32': 'swap_pairs_f32'})
        self.assertNotIn('s', [a['name'] for a in report['arguments']['swap_pairs_f32']])  # no buffer argument
        # g[0], b[0] and t[0] read the builtins' x components, as g.x does.
        kinds = {n['kind'] for n in program['nodes']}
        self.assertLessEqual({'GroupIndex', 'GroupSize', 'LocalIndex'}, kinds)

    def test_cpu_runner_runs_the_instantiation(self):
        import numpy as np
        from frontends.c_import import import_source
        from qpel_ir import Lowering
        from differential import metal_cpu
        from differential.simulate import Case
        program, report = import_source(self.source, resources={'y': 'inout'}, shared={'s': 64},
                                        geometry={'groups': ['(W + 63)/64', '1', '1'], 'block': [64, 1, 1]})
        plan = Lowering(program).plan()
        runner = metal_cpu.program(self.source, plan, report, self.folder/'cpu', timeout=60)
        n = 100
        x = np.arange(n, dtype=np.float32)
        case = Case({'size': (n, 1, 1)}, {'y': x}, {'n': n}, 'swap')
        y = runner.run(case)['y']
        expected = x.copy()
        pairs = (n // 2) * 2
        expected[:pairs] = x[:pairs].reshape(-1, 2)[:, ::-1].reshape(-1)
        np.testing.assert_array_equal(y, expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
