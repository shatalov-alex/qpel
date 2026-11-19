"""llama.cpp's Metal kernel_mul_mm for every block format qpel reads (q1_0, q2_0, q4_0, q4_1, q5_0, q5_1, q8_0,
mxfp4, q2_K to q6_K), unmodified: the weights are blocks of bytes, read through pointers to llama.cpp's block
structs (IMPORT.md, Pointers to blocks of bytes), dequantised by each format's own function. The blocks are
random bytes with sane scales. With B the identity (N = K) the kernel writes the dequantised weights themselves,
C[k][m] = A[m][k] exactly (one product by 1, the rest zeros), so the original dequantises its own weights for
the oracle of the other cases: qpel's Metal agrees with the original bit for bit on the Apple GPU, and qpel's
CUDA on the CPU simulator dequantises to the same bits (the identity case) and is within the bound of a sum of
K half products otherwise."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zlib

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_ir import validate  # noqa: E402
from qpel_test_support import unavailable  # noqa: E402
from test_llama_mul_mm import CONSTANTS, SHARED  # noqa: E402

# format: (bytes per block, weights per block, {byte offset of a half scale}, byte offset of an E8M0 scale or None)
FORMATS = {
    'q1_0': (18, 128, (0,), None), 'q2_0': (18, 64, (0,), None), 'q4_0': (18, 32, (0,), None),
    'q4_1': (20, 32, (0, 2), None), 'q5_0': (22, 32, (0,), None), 'q5_1': (24, 32, (0, 2), None),
    'q8_0': (34, 32, (0,), None), 'mxfp4': (17, 32, (), 0), 'q2_K': (84, 256, (80, 82), None),
    'q3_K': (110, 256, (108,), None), 'q4_K': (144, 256, (0, 2), None), 'q5_K': (176, 256, (0, 2), None),
    'q6_K': (210, 256, (208,), None),
}
M, K = 128, 256
SHAPES = ((M, K), (M, 32), (64, 64))  # (M, N): the identity first (N = K), then random B


def weights(fmt):
    """M rows of K weights as random blocks of bytes, their scales half values in [0.01, 0.1] (E8M0: 2^-7 to 2^1)."""
    import numpy as np
    size, per, halves, exponent = FORMATS[fmt]
    rng = np.random.default_rng(zlib.crc32(fmt.encode()))
    blocks = rng.integers(0, 256, (M * K // per, size), dtype=np.uint8)
    for at in halves:
        blocks[:, at:at + 2] = rng.uniform(0.01, 0.1, len(blocks)).astype(np.float16).view(np.uint8).reshape(-1, 2)
    if exponent is not None:
        blocks[:, exponent] = rng.integers(120, 129, len(blocks))
    return blocks.reshape(-1).view(np.int8), size * K // per


def cases(fmt):
    import numpy as np
    from differential.simulate import Case
    a, row = weights(fmt)
    rng = np.random.default_rng(23)
    out = []
    for m, n in SHAPES:
        b = np.eye(K, dtype=np.float32) if n == K else rng.uniform(-2, 2, (n, K)).astype(np.float32)
        args = {'ne00': K, 'ne02': 1, 'nb01': row, 'nb02': row * m, 'nb03': row * m, 'ne12': 1, 'nb10': 4,
                'nb11': 4 * K, 'nb12': 4 * n * K, 'nb13': 4 * n * K, 'ne0': m, 'ne1': n, 'r2': 1, 'r3': 1}
        out.append(Case({'tiles': (n, m, 1), 'a_bytes': (row * m, 1, 1), 'b_bytes': (4 * n * K, 1, 1),
                         'c_bytes': (4 * m * n, 1, 1)}, {'src0': a[:row * m], 'src1': b.reshape(-1).view(np.int8)},
                        {'args': args}, f'{fmt} M={m} N={n}' + (' (B = identity)' if n == K else '')))
    return out


def oracle(case, dequantised):
    """C = B half(A)^T in float64 and the bound of any order of the K f32 sums, A from the identity case."""
    import numpy as np
    m, n = case.values['args']['ne0'], case.values['args']['ne1']
    a = dequantised[:m].astype(np.float64)
    b = case.inputs['src1'].view(np.float32).astype(np.float16).astype(np.float64).reshape(n, K)
    return (b @ a.T).reshape(-1), ((K + 2) * 2.0 ** -24 * (np.abs(b) @ np.abs(a).T)).reshape(-1)


class Blocks:
    FORMAT = None

    @classmethod
    def setUpClass(cls):
        if not shutil.which('clang'):
            raise unittest.SkipTest('clang required')
        from frontends.clang_ast import metal_available
        if not metal_available():
            raise unittest.SkipTest('Xcode Metal compiler required to import Metal')
        from frontends.corpus import entries, CORPUS, build_defines
        from frontends.c_import import import_source
        from qpel import compile_program
        cls.kernel = f'kernel_mul_mm_{cls.FORMAT}_f32'
        entry = next(e for e in entries() if e['source'].endswith('ggml-metal.metal'))
        cls.source = CORPUS/entry['source']
        cls.include = [CORPUS/f for f in entry.get('include', ())]
        plan = json.loads((CORPUS/'llama.cpp/plans/mul_mm_f32_f32.plan.json').read_text())
        plan['stages'][0]['kernel'] = cls.kernel
        plan['module'] = 'llama_metal_mul_mm'
        cls.program, cls.report = import_source(cls.source, include=cls.include, kernels=[cls.kernel],
                                                module='llama_metal_mul_mm', defines=build_defines(entry),
                                                constants=CONSTANTS, shared=SHARED, plan=plan)
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-mul-mm-blocks-')
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

    def results(self, run):
        import numpy as np
        out = []
        for case in cases(self.FORMAT):
            m, n = case.values['args']['ne0'], case.values['args']['ne1']
            out.append((case, np.asarray(run(case)['dst']).view(np.float32)[:m * n].copy()))
        return out

    def check(self, name, found, original=None):
        import numpy as np
        (identity, c), rest = found[0], found[1:]
        dequantised = c.reshape(K, M).T  # C[k][m] = A[m][k]
        self.assertTrue(np.all(np.isfinite(dequantised)), f'{name}: weights not finite')
        if original is not None:  # the same dequantised weights, bit for bit
            np.testing.assert_array_equal(c.view(np.uint32), original[0][1].view(np.uint32), err_msg=f'{name} {identity.label}')
            dequantised = original[0][1].reshape(K, M).T
        for case, got in rest:
            want, bound = oracle(case, dequantised)
            worst = np.max(np.abs(got.astype(np.float64) - want) / np.maximum(bound, 1e-30))
            self.assertTrue(np.all(np.abs(got.astype(np.float64) - want) <= bound), f'{name} {case.label}: worst {worst:.2f} of the bound')

    def test_against_the_original(self):
        """The original on the Apple GPU; qpel's Metal bit for bit; qpel's CUDA on the CPU simulator."""
        import numpy as np
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        from differential.simulate import Program
        host = matrix.load_host(self.generated)
        original = self.results(lambda case: metal.run_original(self.source, self.plan, self.report, case, include=self.include,
                                                                constants=CONSTANTS, whole_groups=True))
        self.check('original', original)
        ours = self.results(lambda case: metal.run_generated(host, self.plan, case))
        for (case, got), (_, want) in zip(ours, original):
            np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32), err_msg=f'qpel Metal {case.label}')
        if shutil.which('clang++'):
            (Path(self.work.name)/'sim').mkdir(exist_ok=True)
            program = Program(self.generated/'llama_metal_mul_mm.cu', self.plan, Path(self.work.name)/'sim')
            self.check('qpel CUDA', self.results(program.run), original)


for _fmt in FORMATS:
    globals()[f'MulMm_{_fmt}'] = type(f'MulMm_{_fmt}', (Blocks, unittest.TestCase), {'FORMAT': _fmt})
del _fmt


if __name__ == '__main__':
    unittest.main()
