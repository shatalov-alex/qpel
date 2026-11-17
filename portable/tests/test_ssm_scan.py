"""mamba's scan of (a, b) pairs (tests/ssm_scan.cu): cub::BlockScan::InclusiveScan on float2 items
with an operator functor (SSMScanOp) and a block prefix callback (SSMScanPrefixCallbackOp) carrying
the running prefix from chunk to chunk. frontends/functors.py reads the function objects as
functions of their fields, qpel's CUB subset scans with them in CUB 2.5's order, and the items are
an array of vectors (IR version 11). The unmodified kernels run as the original on the CPU
simulator (qpel's CUB header) and, with QPEL_NVIDIA=1, with NVIDIA's CUB on GPGPU-Sim; qpel's CUDA
and Metal must agree with them bit for bit."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from qpel_ir import validate  # noqa: E402
from qpel_test_support import NVIDIA, unavailable  # noqa: E402

SOURCE = HERE/'tests/ssm_scan.cu'
KERNELS = {'ssm_scan_32_4': 32, 'ssm_scan_64_4': 64, 'ssm_scan_32_1': 32}  # kernel: threads
SHAPES = [(1, 1, 1), (100, 1, 1), (128, 1, 1), (300, 1, 1), (777, 1, 1)]


def geometry(threads):
    return {'groups': ['1', '1', '1'], 'block': [threads, 1, 1]}


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Import(unittest.TestCase):
    def test_function_objects_become_functions_of_their_fields(self):
        from frontends.c_import import import_source
        from qpel_text import print_program
        program, report = import_source(SOURCE, kernels=['ssm_scan_32_4'], module='ssm', geometry=geometry(32))
        for target in ('cuda', 'metal'):
            validate(program, target)
        self.assertEqual(program['version'], 11)
        text = print_program(program)
        self.assertIn('fn SSMScanOp_f32_call(ab0: vec<f32, 2>, ab1: vec<f32, 2>) -> vec<f32, 2>', text)
        self.assertIn('fn SSMScanPrefixCallbackOp_f32_call(ref self_running_prefix: vec<f32, 2>, '
                      'block_aggregate: vec<f32, 2>) -> vec<f32, 2>', text)
        self.assertIn('let prefix_op_running_prefix: vec<f32, 2> = running_prefix;', text)
        self.assertIn('smem_running_prefix[0] = prefix_op_running_prefix;', text)
        self.assertIn('let smem_scan: shared<vec<f32, 2>, 2>;', text)  # the warp's total and the block prefix
        adaptations = '\n'.join(report['adaptations'])
        self.assertIn('thread_data is both input and output of a CUB method', adaptations)
        self.assertIn('SSMScanPrefixCallbackOp_f32_call', adaptations)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class Rows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.work = Path(tempfile.mkdtemp(prefix='qpel-ssm-'))
        cls.kernels = {}
        for kernel, threads in KERNELS.items():
            folder = cls.work/kernel
            folder.mkdir()
            program, report = import_source(SOURCE, kernels=[kernel], module=kernel, geometry=geometry(threads))
            (folder/'program.qpel.json').write_text(json.dumps(program))
            compile_program(folder/'program.qpel.json', folder/'build')
            generated = folder/'build/package/generated'
            cls.kernels[kernel] = (report, generated, json.loads((generated/'pipeline.json').read_text()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def cases(self, plan):
        from differential import cases
        return cases.for_program('ssm', plan, values={'seqlen': 'volume'}, shapes=SHAPES)

    def same(self, expected, actual, label):
        from differential.compare import compare
        problems = compare(expected, actual, ['out_a', 'out_b'])
        self.assertFalse(problems, f'{label}: {problems}')

    def references(self, kernel):
        """The original on the CPU simulator (qpel's CUB header), for every case."""
        from differential.simulate import Program
        report, generated, plan = self.kernels[kernel]
        folder = self.work/kernel/'original'
        folder.mkdir(exist_ok=True)
        original = Program(SOURCE, plan, folder, original=report)
        return [(case, original.run(case)) for case in self.cases(plan)]

    def test_cpu_simulator(self):
        from differential.simulate import Program
        for kernel in KERNELS:
            with self.subTest(kernel):
                report, generated, plan = self.kernels[kernel]
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
                report, generated, plan = self.kernels[kernel]
                host = matrix.load_host(generated)
                for case, expected in self.references(kernel):
                    self.same(expected, metal.run_generated(host, plan, case), f'{kernel} {case.label}')

    @unittest.skipUnless(NVIDIA, 'GPGPU-Sim rows are slow; set QPEL_NVIDIA=1')
    def test_original_with_nvidia_cub_on_gpgpusim(self):
        from differential.gpgpusim import GpuProgram, image_ready, run_all
        if not image_ready():
            unavailable(self, 'nvidia', 'no Docker or GPGPU-Sim image')
        root = Path(tempfile.mkdtemp(prefix='qpel-ssm-nvidia-')).resolve()
        pending = []
        try:
            for kernel in KERNELS:
                report, generated, plan = self.kernels[kernel]
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
                    actual, error = side.outcome(folder, case)
                    self.assertIsNone(error)
                    self.same(expected, actual, f'{kernel} {side.work.name} {case.label}')
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
