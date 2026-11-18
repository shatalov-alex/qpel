"""A barrier of the subgroup's lanes (IR version 16): qpel.subgroup_barrier(), CUDA's __syncwarp() and
Metal's simdgroup_barrier, which order the subgroup's memory. examples/subgroup_barrier.qpel swaps
neighbouring lanes' values through shared memory: the CPU simulator, GPGPU-Sim and the Apple GPU.
CUDA's __syncwarp() and Metal's simdgroup_barrier(mem_flags::mem_threadgroup) import, and run against
their originals."""
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

TEXT = (HERE/'examples/subgroup_barrier.qpel').read_text()
BODY = TEXT[TEXT.index('module'):]
SIZES = (1, 64, 100, 300)


def cases():
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(16)
    return [Case({'n': (n, 1, 1)}, {'x': rng.integers(0, 2**32, n, dtype=np.uint32)}, {}, f'n={n}') for n in SIZES]


def swapped(x, n):
    """Lane l reads lane l ^ 1 of its group; a lane past the end reads 0 (what it wrote)."""
    import numpy as np
    padded = np.zeros(-(-n // 64) * 64, np.uint32)
    padded[:n] = x[:n]
    return padded[np.arange(len(padded)) ^ 1][:n]


class Rules(unittest.TestCase):
    def test_version_16_and_text(self):
        program = parse_program(TEXT)
        self.assertEqual(program['version'], 16)
        self.assertEqual(print_program(program), BODY)
        program.update(version=15, semantics='qpel-portable-core-v15')
        with self.assertRaisesRegex(ValueError, 'IR version 15 cannot use: subgroup barrier'):
            validate(program)

    def test_every_lane(self):
        text = TEXT.replace('    qpel.subgroup_barrier();\n', '    if (i < n.x) {\n      qpel.subgroup_barrier();\n    }\n')
        with self.assertRaisesRegex(ValueError, 'SUBGROUP_UNIFORM'):
            validate(parse_program(text))


class Backends(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qpel import compile_program
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-subgroup-barrier-')
        work = Path(cls.work.name)
        (work/'p.json').write_text(json.dumps(parse_program(TEXT)))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_spellings(self):
        self.assertIn('QPEL_SUBGROUP_BARRIER();', (self.generated/'subgroup_barrier.cu').read_text())
        self.assertIn('simdgroup_barrier(mem_flags::mem_device | mem_flags::mem_threadgroup)', (self.generated/'subgroup_barrier.metal').read_text())

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'subgroup_barrier.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases():
            n = case.shapes['n'][0]
            np.testing.assert_array_equal(program.run(case)['y'][:n], swapped(case.inputs['x'], n), err_msg=case.label)

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
        for case in cases():
            n = case.shapes['n'][0]
            got = np.asarray(metal.run_generated(host, self.plan, case)['y'])[:n].astype(np.uint32)
            np.testing.assert_array_equal(got, swapped(case.inputs['x'], n), err_msg=case.label)

    def test_gpgpusim(self):
        import numpy as np
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready(build=False):
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-subgroup-barrier-nvidia-')).resolve()
        try:
            program = GpuProgram(self.generated/'subgroup_barrier.cu', self.plan, root/'qpel')
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
                n = case.shapes['n'][0]
                np.testing.assert_array_equal(got['y'][:n], swapped(case.inputs['x'], n), err_msg=case.label)
        finally:
            shutil.rmtree(root, ignore_errors=True)


CUDA = r'''#include <cuda_runtime.h>
__global__ void swap_pairs(const unsigned *x, unsigned *y, unsigned n) {
  __shared__ unsigned slots[64];
  const unsigned i = blockIdx.x * 64 + threadIdx.x;
  slots[threadIdx.x] = i < n ? x[i] : 0u;
  __syncwarp();
  const unsigned partner = slots[threadIdx.x ^ 1u];
  if (i < n) y[i] = partner;
}
'''
METAL = '''#include <metal_stdlib>
using namespace metal;
kernel void swap_pairs(device const uint* x [[buffer(0)]], device uint* y [[buffer(1)]], constant uint& n [[buffer(2)]],
                       uint group [[threadgroup_position_in_grid]], uint lane [[thread_position_in_threadgroup]]) {
  threadgroup uint slots[64];
  const uint i = group * 64 + lane;
  slots[lane] = i < n ? x[i] : 0u;
  simdgroup_barrier(mem_flags::mem_threadgroup);
  const uint partner = slots[lane ^ 1u];
  if (i < n) y[i] = partner;
}
'''
GEOMETRY = {'groups': ['8', '1', '1'], 'block': [64, 1, 1]}


class Import(unittest.TestCase):
    def imported(self, text, suffix):
        from frontends.c_import import import_source
        from qpel import compile_program
        work = Path(tempfile.mkdtemp(prefix='qpel-subgroup-barrier-import-'))
        self.addCleanup(shutil.rmtree, work, True)
        source = work/('k' + suffix)
        source.write_text(text)
        program, report = import_source(source, kernels=['swap_pairs'], module='swap_pairs', geometry=GEOMETRY)
        validate(program)
        self.assertIn('qpel.subgroup_barrier();', print_program(program))
        (work/'p.json').write_text(json.dumps(program))
        compile_program(work/'p.json', work/'build')
        generated = work/'build/package/generated'
        return work, source, report, generated, json.loads((generated/'pipeline.json').read_text())

    def case(self, n):
        import numpy as np
        from differential.simulate import Case
        return Case({'size': (512, 1, 1)}, {'x': np.random.default_rng(n).integers(0, 2**32, 512, dtype=np.uint32)}, {'n': n}, f'n={n}')

    @unittest.skipUnless(shutil.which('clang') and shutil.which('clang++'), 'clang and clang++ required')
    def test_cuda(self):
        import numpy as np
        from differential.simulate import Program
        work, source, report, generated, plan = self.imported(CUDA, '.cu')
        (work/'o').mkdir()
        (work/'q').mkdir()
        original = Program(source, plan, work/'o', original=report)
        ours = Program(generated/'swap_pairs.cu', plan, work/'q')
        for n in (1, 64, 100, 300):
            case = self.case(n)
            np.testing.assert_array_equal(ours.run(case)['y'][:n], original.run(case)['y'][:n], err_msg=case.label)
            np.testing.assert_array_equal(original.run(case)['y'][:n], swapped(case.inputs['x'], n))

    @unittest.skipUnless(shutil.which('clang'), 'clang required')
    def test_cuda_some_lanes(self):
        from frontends.c_import import import_source
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'k.cu'
            path.write_text(CUDA.replace('__syncwarp();', '__syncwarp(0x0000ffffu);'))
            with self.assertRaisesRegex(Exception, 'some lanes'):
                import_source(path, kernels=['swap_pairs'], module='swap_pairs', geometry=GEOMETRY)

    def test_metal(self):
        import numpy as np
        from frontends.clang_ast import metal_available
        if not metal_available():
            self.skipTest('Xcode Metal compiler required to import Metal')
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        work, source, report, generated, plan = self.imported(METAL, '.metal')
        host = matrix.load_host(generated)
        for n in (1, 64, 100, 300):
            case = self.case(n)
            original = np.asarray(metal.run_original(source, plan, report, case, whole_groups=True)['y'])[:n].astype(np.uint32)
            ours = np.asarray(metal.run_generated(host, plan, case)['y'])[:n].astype(np.uint32)
            np.testing.assert_array_equal(ours, original, err_msg=case.label)
            np.testing.assert_array_equal(original, swapped(case.inputs['x'], n))


if __name__ == '__main__':
    unittest.main()
