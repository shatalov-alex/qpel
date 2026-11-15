"""IR version 6 bitcasts: an f32's bits as u32 or i32 and back (CUDA __float_as_uint and the others,
Metal as_type<T>). Rules, the CPU simulator, the Metal runner on the CPU, the Apple GPU, and both
importers giving the program back."""
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

PROGRAM = '''module bits {
  kernel bits(x: buffer<f32, read> @0, y: buffer<u32, write> @1, z: buffer<f32, write> @2, n: shape3 @3) grid1d {
    let i: u32 = qpel.index;
    if (i < ((n.x * n.y) * n.z)) {
      y[i] = bitcast<u32>(x[i]) ^ cast<u32>(bitcast<i32>(x[i]) >> 31);
      z[i] = bitcast<f32>(bitcast<u32>(x[i]) & 2147483647u) + bitcast<f32>(cast<i32>(i) + 1065353216);
    }
  }
}
pipeline bits_launch {
  shape n;
  input x: buffer<f32, read> in n;
  output y: buffer<u32, write> in n;
  output z: buffer<f32, write> in n;
  dispatch bits over n block (64) (x = x, y = y, z = z, n = n);
}
'''


def oracle(x):
    import numpy as np
    bits = x.view(np.uint32)
    y = bits ^ np.where(x.view(np.int32) < 0, np.uint32(0xffffffff), np.uint32(0))  # >> 31 is arithmetic
    ones = (np.arange(len(x), dtype=np.int64) + 1065353216).astype(np.int32).view(np.float32)
    return y, (np.abs(x) + ones).astype(np.float32)


class Rules(unittest.TestCase):
    def test_round_trip_and_version(self):
        program = parse_program(PROGRAM)
        self.assertEqual(print_program(program), PROGRAM)
        self.assertEqual(program['version'], 6)
        for target in ('cuda', 'metal'):
            validate(program, target)

    def test_only_32_bit_float_and_integer(self):
        for old, new in (('bitcast<u32>(x[i])', 'bitcast<u64>(x[i])'), ('bitcast<i32>(x[i])', 'bitcast<f32>(x[i])')):
            with self.subTest(new), self.assertRaisesRegex(ValueError, 'BITCAST'):
                validate(parse_program(PROGRAM.replace(old, new, 1)))


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-bitcast-')
        work = Path(cls.work.name)
        (work/'program.qpel.json').write_text(json.dumps(parse_program(PROGRAM)))
        compile_program(work/'program.qpel.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def cases(self):
        from differential import cases
        return cases.generic(self.plan, 6, shapes=[(1, 1, 1), (40, 1, 1), (300, 1, 1)])

    def check(self, got, case):
        import numpy as np
        y, z = oracle(case.inputs['x'])
        np.testing.assert_array_equal(got['y'], y, err_msg=case.label)
        np.testing.assert_array_equal(got['z'], z, err_msg=case.label)

    def test_cpu_simulator(self):
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'bits.cu', self.plan, Path(self.work.name)/'sim')
        for case in self.cases():
            self.check(program.run(case), case)

    def test_metal_on_the_cpu(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from differential import metal_cpu
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')
        source = self.generated/'bits.metal'
        _, report = import_source(source, plan=self.plan, module='bits')
        runner = metal_cpu.program(source, self.plan, report, Path(self.work.name)/'metal_cpu', timeout=60)
        for case in self.cases():
            self.check(runner.run(case), case)

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in self.cases():
            self.check(metal.run_generated(host, self.plan, case), case)

    def test_both_importers_give_the_program_back(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        from frontends.roundtrip import canonical
        original = canonical(parse_program(PROGRAM))
        for source in ('bits.cu', 'bits.metal'):
            if source.endswith('.metal') and not clang_ast.metal_available():
                unavailable(self, 'metal', 'Metal toolchain required')
            with self.subTest(source):
                program, _ = import_source(self.generated/source, plan=self.plan, module='bits')
                self.assertEqual(canonical(program), original)

    def test_cuda_spellings_import(self):
        """CUDA's own intrinsics, as a library kernel writes them."""
        from frontends.c_import import import_source
        source = Path(self.work.name)/'flip.cu'
        source.write_text('__global__ void flip(const float* x, float* y, unsigned n) {\n'
                          '  unsigned i = blockIdx.x * blockDim.x + threadIdx.x;\n'
                          '  if (i < n) y[i] = __uint_as_float(__float_as_uint(x[i]) ^ 0x80000000u) + __int_as_float(__float_as_int(x[i]));\n'
                          '}\n')
        program, _ = import_source(source)
        self.assertEqual(sum(n['kind'] == 'Bitcast' for n in program['nodes']), 4)
        for target in ('cuda', 'metal'):
            validate(program, target)


if __name__ == '__main__':
    unittest.main(verbosity=2)
