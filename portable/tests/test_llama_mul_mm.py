"""llama.cpp's Metal kernel_mul_mm (the f32 and f16 instances, its simdgroup-matrix path), unmodified: C (N x M) =
B A^T for row-major A (M x K) and B (N x K), staged through threadgroup memory as halves and multiplied in
8x8 simdgroup matrices, ma[4], mb[2], mc[8]. The import reads its Metal matrix types, its threadgroup
char memory carved into typed views, is_same<T0_4x4, block_q>::value and the function constants llama.cpp
sets (no bounds checks: M a multiple of 64, N of 32, K of 32). The original runs on the Apple GPU against
qpel's Metal, bit for bit (the same simdgroup_multiply_accumulate); qpel's CUDA on the CPU simulator is
within the bound of a sum of K half products, as the matrix tests' oracle measures it."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_ir import validate  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402

CONSTANTS = {'FC_mul_mm_ne12': 1, 'FC_mul_mm_r2': 1, 'FC_mul_mm_r3': 1, 'FC_mul_mm_bc_inp': False, 'FC_mul_mm_bc_out': False}
SHARED = {'shmem': 8192}
SHAPES = ((64, 32, 32), (128, 64, 64), (64, 64, 96), (192, 32, 32))  # (M, N, K)


BLOCK = {'q8_0': 34, 'q4_0': 18}  # bytes per block of 32 weights: a half scale d, then the quants


def quantized(rng, m, k, weights):
    """(bytes of M rows of K/32 blocks, the weights they hold as halves): q8_0 holds d q (q an int8), q4_0 d (q - 8)
    (q a nibble: weight j of a block the low nibble of byte j, weight 16 + j its high nibble). Both products are
    exact in float, so the kernel's float arithmetic rounds once, to half."""
    import numpy as np
    blocks = m * k // 32
    d = rng.uniform(0.01, 0.1, blocks).astype(np.float16)
    out = np.zeros((blocks, BLOCK[weights]), np.uint8)
    out[:, :2] = d.view(np.uint8).reshape(blocks, 2)
    if weights == 'q8_0':
        q = rng.integers(-127, 128, (blocks, 32), dtype=np.int8)
        out[:, 2:] = q.view(np.uint8)
        values = q.astype(np.float64)
    else:
        q = rng.integers(0, 16, (blocks, 32), dtype=np.uint8)
        out[:, 2:] = q[:, :16] | (q[:, 16:] << 4)
        values = q.astype(np.float64) - 8
    weights_ = (values * d.astype(np.float64)[:, None]).astype(np.float16)
    return out.reshape(-1).view(np.int8), weights_.reshape(m, k)


def cases(weights='f32'):
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(21)
    out = []
    for m, n, k in SHAPES:
        if weights in BLOCK:
            a, _ = quantized(rng, m, k, weights)
            row = BLOCK[weights] * k // 32
        else:
            size = 4 if weights == 'f32' else 2
            a = rng.uniform(-2, 2, m * k).astype(np.float32 if weights == 'f32' else np.float16).view(np.int8)
            row = size * k
        b = rng.uniform(-2, 2, n * k).astype(np.float32)
        args = {'ne00': k, 'ne02': 1, 'nb01': row, 'nb02': row * m, 'nb03': row * m, 'ne12': 1, 'nb10': 4,
                'nb11': 4 * k, 'nb12': 4 * n * k, 'nb13': 4 * n * k, 'ne0': m, 'ne1': n, 'r2': 1, 'r3': 1}
        out.append(Case({'tiles': (n, m, 1), 'a_bytes': (row * m, 1, 1), 'b_bytes': (4 * n * k, 1, 1),
                         'c_bytes': (4 * m * n, 1, 1)}, {'src0': a, 'src1': b.view(np.int8)},
                        {'args': args}, f'M={m} N={n} K={k}'))
    return out


def oracle(case, weights='f32'):
    """C[n][m] = sum_k half(A[m][k]) half(B[n][k]) in float64, and the bound of any order of the K f32 sums."""
    import numpy as np
    m, n, k = case.values['args']['ne0'], case.values['args']['ne1'], case.values['args']['ne00']
    if weights in BLOCK:
        blocks = case.inputs['src0'].view(np.uint8).reshape(-1, BLOCK[weights])
        d = blocks[:, :2].copy().view(np.float16).astype(np.float64)
        if weights == 'q8_0':
            values = blocks[:, 2:].view(np.int8).astype(np.float64)
        else:
            values = np.concatenate([blocks[:, 2:] & 15, blocks[:, 2:] >> 4], axis=1).astype(np.float64) - 8
        a = (values * d).astype(np.float16).astype(np.float64).reshape(m, k)
    else:
        a = case.inputs['src0'].view(np.float32 if weights == 'f32' else np.float16).astype(np.float16).astype(np.float64).reshape(m, k)
    b = case.inputs['src1'].view(np.float32).astype(np.float16).astype(np.float64).reshape(n, k)
    c = b @ a.T
    bound = (k + 2) * 2.0 ** -24 * (np.abs(b) @ np.abs(a).T)
    return c.reshape(-1), bound.reshape(-1)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class LlamaMulMm(unittest.TestCase):
    KERNEL, WEIGHTS = 'kernel_mul_mm_f32_f32', 'f32'

    @classmethod
    def setUpClass(cls):
        from frontends.clang_ast import metal_available
        if not metal_available():
            raise unittest.SkipTest('Xcode Metal compiler required to import Metal')
        from frontends.corpus import entries, CORPUS, build_defines
        from frontends.c_import import import_source
        from qpel import compile_program
        entry = next(e for e in entries() if e['source'].endswith('ggml-metal.metal'))
        cls.source = CORPUS/entry['source']
        cls.include = [CORPUS/f for f in entry.get('include', ())]
        plan = json.loads((CORPUS/'llama.cpp/plans/mul_mm_f32_f32.plan.json').read_text())
        plan['stages'][0]['kernel'] = cls.KERNEL
        plan['module'] = 'llama_metal_mul_mm'
        cls.program, cls.report = import_source(cls.source, include=cls.include, kernels=[cls.KERNEL],
                                                module='llama_metal_mul_mm', defines=build_defines(entry),
                                                constants=CONSTANTS, shared=SHARED, plan=plan)
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-llama-mul-mm-')
        work = Path(cls.work.name)
        (work/'p.json').write_text(json.dumps(cls.program))
        compile_program(work/'p.json', work/'build')
        cls.generated = work/'build/package/generated'
        cls.plan = json.loads((cls.generated/'pipeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_imported(self):
        validate(self.program)

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'llama_metal_mul_mm.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases(self.WEIGHTS):
            want, bound = oracle(case, self.WEIGHTS)
            got = np.asarray(program.run(case)['dst']).view(np.float32)[:len(want)].astype(np.float64)
            self.assertTrue(np.all(np.abs(got - want) <= bound), f'{case.label}: worst {np.max(np.abs(got - want) / np.maximum(bound, 1e-30)):.2f} of the bound')

    def test_against_the_original(self):
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        host = matrix.load_host(self.generated)
        for case in cases(self.WEIGHTS):
            want, bound = oracle(case, self.WEIGHTS)
            original = np.asarray(metal.run_original(self.source, self.plan, self.report, case, include=self.include,
                                                     constants=CONSTANTS, whole_groups=True)['dst']).view(np.float32)[:len(want)]
            ours = np.asarray(metal.run_generated(host, self.plan, case)['dst']).view(np.float32)[:len(want)]
            np.testing.assert_array_equal(ours.view(np.uint32), original.view(np.uint32), err_msg=case.label)
            self.assertTrue(np.all(np.abs(original.astype(np.float64) - want) <= bound), case.label)


class LlamaMulMmHalf(LlamaMulMm):
    KERNEL, WEIGHTS = 'kernel_mul_mm_f16_f32', 'f16'


class LlamaMulMmQ8(LlamaMulMm):
    """q8_0 weights: blocks of a half scale and 32 int8 quants, read through llama.cpp's block_q8_0 pointers."""
    KERNEL, WEIGHTS = 'kernel_mul_mm_q8_0_f32', 'q8_0'


class LlamaMulMmQ4(LlamaMulMm):
    """q4_0 weights: blocks of a half scale and 32 nibbles, dequantised through 16-bit reads of the block."""
    KERNEL, WEIGHTS = 'kernel_mul_mm_q4_0_f32', 'q4_0'


if __name__ == '__main__':
    unittest.main()
