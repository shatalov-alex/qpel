"""IR version 5 shared views, shared<T>: a thread's pointer into its group's shared memory (CUDA T*,
Metal threadgroup T*), as locals and helper parameters. Text, rules, the CPU simulator, the Metal
runner on the CPU, the Apple GPU, and both importers giving the program back."""
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
from qpel_test_support import unavailable  # noqa: E402

# Each group of 64 reverses its 64 inputs (through a view of the tile's second upper and a helper
# that reads a view), and writes the sum of its upper's first element and the element read.
PROGRAM = '''module tile_views {
  fn read_back(row: shared<f32>, k: u32) -> f32 {
    return row[k];
  }
  kernel tile_views(x: buffer<f32, read> @0, y: buffer<f32, write> @1, n: shape3 @2) groups {
    let tile: shared<f32, 64>;
    let i: u32 = (qpel.group.x * qpel.group_size.x) + qpel.local.x;
    let count: u32 = (n.x * n.y) * n.z;
    let v: f32 = 0.0f;
    if (i < count) {
      v = x[i];
    }
    tile[qpel.local.x] = v;
    qpel.barrier();
    let upper: shared<f32> = tile + 32u;
    let base: shared<f32> = tile;
    if (qpel.local.x >= 32u) {
      base = upper;
    }
    let mirrored: f32 = read_back(tile, 63u - qpel.local.x);
    if (i < count) {
      y[i] = mirrored + base[0u];
    }
  }
}
pipeline tile_views_launch {
  shape n;
  input x: buffer<f32, read> in n;
  output y: buffer<f32, write> in n;
  dispatch tile_views over n block (64, 1, 1) groups ("(W*H*N + 63)/64", "1", "1") (x = x, y = y, n = n);
}
'''


def oracle(x):
    import numpy as np
    n = len(x)
    padded = np.zeros(-(-n // 64) * 64, np.float32)
    padded[:n] = x
    out = np.empty_like(padded)
    for g in range(0, len(padded), 64):
        tile = padded[g:g + 64]
        for t in range(64):
            out[g + t] = np.float32(tile[63 - t]) + np.float32(tile[32 if t >= 32 else 0])
    return out[:n]


class Rules(unittest.TestCase):
    def program(self, text=PROGRAM):
        return parse_program(text)

    def test_text_round_trip_and_version(self):
        program = self.program()
        self.assertEqual(print_program(program), PROGRAM)
        self.assertEqual(program['version'], 5)
        for target in ('cuda', 'metal'):
            validate(program, target)
        self.assertIn('SharedViewType', {n['kind'] for n in program['nodes']})

    def test_version_4_cannot_use_views(self):
        program = self.program()
        program.update(version=4, semantics='qpel-portable-core-v4')
        with self.assertRaisesRegex(ValueError, 'IR version 4 cannot use'):
            validate(program)

    def reject(self, old, new, code):
        self.assertIn(old, PROGRAM)
        with self.assertRaisesRegex(ValueError, code):
            validate(self.program(PROGRAM.replace(old, new)))

    def test_a_view_needs_a_value(self):
        self.reject('let upper: shared<f32> = tile + 32u;', 'let upper: shared<f32>;\n    upper = tile + 32u;',
                    'VARIABLE_TYPE')

    def test_element_types_must_agree(self):
        self.reject('let upper: shared<f32> = tile + 32u;', 'let upper: shared<u32> = tile + 32u;', 'VARIABLE_TYPE')

    def test_a_shared_array_is_still_not_a_value(self):
        self.reject('let v: f32 = 0.0f;', 'let v: f32 = 0.0f;\n    let w: u32 = tile;', 'VARIABLE_TYPE|ARRAY_USE')

    def test_not_a_kernel_argument(self):
        self.reject('x: buffer<f32, read> @0,', 'x: buffer<f32, read> @0, s: shared<f32> @3,', 'KERNEL_ABI')

    def test_reads_through_a_view_vary(self):
        # A barrier under a condition read through a view: another thread may have written it.
        self.reject('    let mirrored: f32', '    if (upper[0u] > 0.0f) {\n      qpel.barrier();\n    }\n    let mirrored: f32',
                    'BARRIER_UNIFORM')


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-views-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(PROGRAM)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def cases(self):
        import numpy as np
        from differential.simulate import Case
        for n in (1, 40, 64, 200):
            x = np.random.default_rng(n).uniform(-4, 4, n).astype(np.float32)
            yield Case({'n': (n, 1, 1)}, {'x': x}, {}, str(n)), x

    def test_spellings(self):
        cuda = (self.generated/'tile_views.cu').read_text()
        metal = (self.generated/'tile_views.metal').read_text()
        self.assertIn('QPEL_SHARED_VIEW(QPEL_F32) upper = (tile + ', cuda)
        self.assertIn('QPEL_SHARED_VIEW(QPEL_F32) row', metal)

    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'tile_views.cu', self.plan, Path(self.work.name)/'sim')
        for case, x in self.cases():
            np.testing.assert_array_equal(program.run(case)['y'], oracle(x))

    def test_metal_on_the_cpu(self):
        import numpy as np
        from frontends import clang_ast
        from frontends.c_import import import_source
        from differential import metal_cpu
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        source = self.generated/'tile_views.metal'
        _, report = import_source(source, plan=self.plan, module='tile_views')
        runner = metal_cpu.program(source, self.plan, report, Path(self.work.name)/'metal_cpu', timeout=60)
        for case, x in self.cases():
            np.testing.assert_array_equal(runner.run(case)['y'], oracle(x))

    def test_apple_gpu(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case, x in self.cases():
            np.testing.assert_array_equal(metal.run_generated(host, self.plan, case)['y'], oracle(x))

    def test_both_importers_give_the_program_back(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from frontends.roundtrip import canonical
        original = canonical(parse_program(PROGRAM))
        for source in ('tile_views.cu', 'tile_views.metal'):
            if source.endswith('.metal') and not clang_ast.metal_available():
                unavailable(self, 'metal', 'Metal toolchain required')
            with self.subTest(source):
                program, _ = import_source(self.generated/source, plan=self.plan, module='tile_views')
                self.assertEqual(canonical(program), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
