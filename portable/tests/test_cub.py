"""qpel's CUB subset (frontends/include/qpel_cub, LIBRARIES.md): cub::BlockScan and cub::BlockReduce
calls import as calls of library functions in CUB's own order, which need barriers in helpers (IR
version 7). tests/cub_blocks.cu uses every method of the subset; it runs as the original on the CPU
simulator (with qpel's header), as qpel's CUDA there, as qpel's Metal on the Apple GPU and, with
QPEL_NVIDIA=1, as the original compiled with NVIDIA's real CUB on GPGPU-Sim, all bit for bit."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program  # noqa: E402
from qpel_test_support import NVIDIA, unavailable  # noqa: E402

SOURCE = HERE/'tests/cub_blocks.cu'
KERNELS = {'blocks_f32_256_0': 256, 'blocks_f32_96_0': 96, 'blocks_f32_160_1': 160, 'blocks_f32_128_2': 128,
           'blocks_f32_256_2': 256, 'blocks_i32_64_0': 64, 'blocks_u32_32_0': 32}
SHAPES = [(1, 1, 1), (37, 1, 1), (256, 1, 1), (1000, 1, 1)]

HELPER = '''module helper {
  fn wait(x: f32) -> f32 {
    qpel.barrier();
    return x;
  }
  kernel k(a: buffer<f32, read> @0, b: buffer<f32, write> @1, n: shape3 @2) groups {
    let i: u32 = (qpel.group.x * 64u) + qpel.local.x;
    let v: f32 = wait(a[i]);
    b[i] = v;
  }
}
pipeline helper_launch {
  shape n;
  input a: buffer<f32, read> in n;
  output b: buffer<f32, write> in n;
  dispatch k over n block (64) groups ("W / 64", "1", "1") (a = a, b = b, n = n);
}
'''


def geometry(threads):
    return {'groups': [f'(W + {threads - 1}) / {threads}', '1', '1'], 'block': [threads, 1, 1]}


class Rules(unittest.TestCase):
    def test_barrier_in_a_helper_is_version_7(self):
        program = parse_program(HELPER)
        self.assertEqual(program['version'], 7)
        for target in ('cuda', 'metal'):
            validate(program, target)
        program.update(version=6, semantics='qpel-portable-core-v6')
        with self.assertRaisesRegex(ValueError, 'barrier in a helper function'):
            validate(program)

    def test_helper_barriers_are_reached_by_every_thread(self):
        text = HELPER.replace('    qpel.barrier();\n    return x;', '    if (x > 0.0f) {\n      qpel.barrier();\n    }\n    return x;')
        with self.assertRaisesRegex(ValueError, 'BARRIER_UNIFORM'):
            validate(parse_program(text))

    def test_calls_of_a_helper_with_barriers_are_uniform(self):
        text = HELPER.replace('    let v: f32 = wait(a[i]);', '    let v: f32 = 0.0f;\n    if (i < 5u) {\n      v = wait(a[i]);\n    }')
        with self.assertRaisesRegex(ValueError, 'BARRIER_UNIFORM'):
            validate(parse_program(text))

    def test_barrier_count_stays_in_kernels(self):
        text = HELPER.replace('    qpel.barrier();\n    return x;', '    let c: i32 = qpel.barrier_count(x > 0.0f);\n    return x;')
        with self.assertRaisesRegex(ValueError, 'BARRIER_CONTEXT'):
            validate(parse_program(text))


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Import(unittest.TestCase):
    def test_calls_become_library_functions(self):
        from frontends.c_import import import_source
        from qpel_text import print_program
        program, report = import_source(SOURCE, kernels=['blocks_f32_96_0'], module='m', geometry=geometry(96))
        for target in ('cuda', 'metal'):
            validate(program, target)
        text = print_program(program)
        for helper in ('raking_scan_', 'block_reduce_', 'warp_inclusive_', 'warp_reduce_'):  # one per instance
            self.assertIn('fn ' + helper, text)
        self.assertIn('let scan_storage: shared<f32, 97>;', text)  # 32 segments of 3, no padding, the aggregate

    def test_outside_the_subset(self):
        from frontends import clang_ast
        from frontends.c_import import import_source
        work = Path(tempfile.mkdtemp())
        try:
            source = work/'raking.cu'
            source.write_text(SOURCE.read_text().replace('typedef cub::BlockReduce<T, N> Reduce;',
                                                         'typedef cub::BlockReduce<T, N, cub::BLOCK_REDUCE_RAKING> Reduce;'))
            with self.assertRaisesRegex(clang_ast.SourceError, 'BLOCK_REDUCE_WARP_REDUCTIONS'):
                import_source(source, kernels=['blocks_f32_96_0'], geometry=geometry(96))
        finally:
            shutil.rmtree(work, ignore_errors=True)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Rows(unittest.TestCase):
    """Each kernel imported on its own (its block size), compiled, and run."""

    @classmethod
    def setUpClass(cls):
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = Path(tempfile.mkdtemp(prefix='qpel-cub-'))
        cls.kernels = {}
        for kernel, threads in KERNELS.items():
            folder = cls.work/kernel
            folder.mkdir()
            program, report = import_source(SOURCE, kernels=[kernel], module=kernel, geometry=geometry(threads))
            (folder/'program.qpel.json').write_text(json.dumps(program))
            compile_program(folder/'program.qpel.json', folder/'build')
            generated = folder/'build/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            cls.kernels[kernel] = (program, report, generated, plan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def cases(self, plan):
        from differential import cases
        signed = any(r['name'] == 'x' and r['type'] == 'buffer<i32, read>' for r in plan['resources'])
        # int sums in the test's own code (total + other) must not overflow: that is undefined in C++
        return cases.for_program('cub', plan, values={'n': 'volume'}, shapes=SHAPES,
                                 inputs={'x': 'i32 -1000000..1000000'} if signed else None)

    def same(self, expected, actual, label):
        import numpy as np
        for name, want in expected.items():
            if name not in actual:
                continue
            got = actual[name]
            if want.dtype == np.float32:
                want, got = want.view(np.uint32), got.view(np.uint32)
            np.testing.assert_array_equal(got, want, err_msg=f'{name} ({label})')

    def references(self, kernel):
        """The original with qpel's header on the CPU simulator, for every case."""
        from differential.simulate import Program
        program, report, generated, plan = self.kernels[kernel]
        folder = self.work/kernel/'original'
        folder.mkdir(exist_ok=True)
        original = Program(SOURCE, plan, folder, original=report)
        return [(case, original.run(case)) for case in self.cases(plan)]

    def test_cpu_simulator(self):
        from differential.simulate import Program
        for kernel in KERNELS:
            with self.subTest(kernel):
                program, report, generated, plan = self.kernels[kernel]
                folder = self.work/kernel/'qpel'
                folder.mkdir(exist_ok=True)
                ours = Program(generated/(kernel + '.cu'), plan, folder)
                for case, expected in self.references(kernel):
                    self.same(expected, ours.run(case), f'{kernel} {case.label}')

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for kernel in KERNELS:
            with self.subTest(kernel):
                program, report, generated, plan = self.kernels[kernel]
                host = matrix.load_host(generated)
                for case, expected in self.references(kernel):
                    self.same(expected, metal.run_generated(host, plan, case), f'{kernel} {case.label}')

    def test_both_importers_give_the_program_back(self):
        from frontends import clang_ast
        from frontends.roundtrip import reimport
        for kernel in ('blocks_f32_160_1', 'blocks_f32_128_2', 'blocks_u32_32_0'):
            with self.subTest(kernel):
                for dialect, difference in reimport(self.kernels[kernel][0]).items():
                    self.assertIsNone(difference, f'{kernel} from {dialect}')
        if not clang_ast.metal_available():
            unavailable(self, 'metal', 'Metal toolchain required')

    @unittest.skipUnless(NVIDIA, 'GPGPU-Sim rows are slow; set QPEL_NVIDIA=1')
    def test_real_cub_on_gpgpusim(self):
        """The original compiled with NVIDIA's CUB (nvcc's own headers), and qpel's CUDA, on GPGPU-Sim."""
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready():
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-cub-nvidia-')).resolve()
        pending = []
        try:
            for kernel in KERNELS:
                program, report, generated, plan = self.kernels[kernel]
                built = (GpuProgram(SOURCE, plan, root/kernel/'original', fmad=False, original=report),
                         GpuProgram(generated/(kernel + '.cu'), plan, root/kernel/'qpel'))
                for number, (case, expected) in enumerate(self.references(kernel)):
                    for side in built:
                        folder = side.work/f'c{number:03}'
                        folder.mkdir()
                        side.write_case(case, folder)
                        pending.append((kernel, side, case, folder, expected))
            run_all(root, [root, HERE, self.work.resolve()])
            for kernel, side, case, folder, expected in pending:
                with self.subTest(f'{kernel} {side.work.name} {case.label}'):
                    actual, error = side.outcome(folder)
                    self.assertIsNone(error)
                    self.same(expected, actual, f'{kernel} {side.work.name} {case.label}')
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
