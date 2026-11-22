"""llama.cpp's Metal kernel_mul_mm_id (the mixture-of-experts matrix multiply, its simdgroup-matrix path), unmodified.
Each token is routed to ne20 of E experts; kernel_mul_mm_id_map0 lists, per expert, the tokens routed to it (htpe:
how many, hids: token*ne20 + slot for each), and kernel_mul_mm_id multiplies expert e's weights A_e (M x K) by those
tokens' rows of B, a group per 64 rows of A, 32 of the expert's tokens and the expert (z), writing
dst[token][slot] = A_e B[token][slot % ne11]. Its output goes from threadgroup memory to the device as float4s
through vector pointer locals (D4, C4), which import as element accesses. The original runs on the Apple GPU
against qpel's Metal, bit for bit; qpel's CUDA on the CPU simulator is within the bound of a sum of K half products
(test_llama_mul_mm's oracle). M a multiple of 64 and K of 32, as without llama.cpp's bounds-check constants."""
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
from test_llama_mul_mm import BLOCK, CONSTANTS, SHARED, quantized  # noqa: E402

# (M, K, experts E, used per token ne20, tokens T, ne11: B rows per token, 1 (broadcast) or ne20)
SHAPES = ((64, 32, 4, 2, 5, 1), (128, 64, 4, 2, 40, 2), (64, 96, 3, 1, 70, 1), (192, 32, 8, 4, 33, 4))


def plan(kernel):
    shape = lambda name: {'name': name, 'kind': 'shape', 'type': 'shape3', 'extent': None}
    buffer = lambda name, kind, extent, access='read': {'name': name, 'kind': kind, 'type': f'buffer<i8, {access}>',
                                                        'extent': extent}
    names = ('args', 'src0', 'src1', 'htpe', 'hids', 'dst')
    return {'schema': 2, 'module': 'llama_metal_mul_mm_id',
            'resources': [shape(s) for s in ('tiles', 'a_bytes', 'b_bytes', 'tpe_bytes', 'ids_bytes', 'c_bytes')] + [
                {'name': 'args', 'kind': 'value', 'type': 'struct ggml_metal_kargs_mul_mm_id', 'extent': None},
                buffer('src0', 'input', 'a_bytes'), buffer('src1', 'input', 'b_bytes'),
                buffer('htpe', 'input', 'tpe_bytes'), buffer('hids', 'input', 'ids_bytes'),
                buffer('dst', 'output', 'c_bytes', 'write')],
            'stages': [{'kernel': kernel, 'grid': 'tiles', 'blockSize': 128, 'completionBarrier': True,
                        'bindings': [{'parameter': n, 'resource': n} for n in names], 'domain': 'groups',
                        'blockShape': [128, 1, 1], 'groups': ['(W + 31) / 32', '(H + 63) / 64', 'N']}]}


def cases(weights='f32'):
    """Random routing (ne20 distinct experts per token), listed per expert in token order as map0 lists them."""
    import numpy as np
    from differential.simulate import Case
    rng = np.random.default_rng(23)
    out = []
    for m, k, experts, used, tokens, ne11 in SHAPES:
        if weights in BLOCK:
            a, _ = quantized(rng, experts * m, k, weights)
            row = BLOCK[weights] * k // 32
        else:
            size = 4 if weights == 'f32' else 2
            a = rng.uniform(-2, 2, experts * m * k).astype(np.float32 if weights == 'f32' else np.float16).view(np.int8)
            row = size * k
        b = rng.uniform(-2, 2, tokens * ne11 * k).astype(np.float32)
        routes = np.array([rng.choice(experts, used, replace=False) for _ in range(tokens)], np.int32)
        tpe = np.zeros(experts, np.uint32)
        ids = np.zeros((experts, tokens), np.int32)
        for t in range(tokens):
            for slot, e in enumerate(routes[t]):
                ids[e, tpe[e]] = t * used + slot
                tpe[e] += 1
        args = {'ne00': k, 'ne02': experts, 'nb01': row, 'nb02': row * m, 'nb03': row * m * experts, 'ne11': ne11,
                'nb10': 4, 'nb11': 4 * k, 'nb12': 4 * k * ne11, 'nb13': 4 * k * ne11 * tokens, 'ne20': used,
                'ne21': tokens, 'ne0': m, 'ne1': used, 'r2': 1, 'r3': 1}
        case = Case({'tiles': (tokens, m, experts), 'a_bytes': (row * m * experts, 1, 1), 'b_bytes': (4 * b.size, 1, 1),
                     'tpe_bytes': (4 * experts, 1, 1), 'ids_bytes': (4 * ids.size, 1, 1),
                     'c_bytes': (4 * tokens * used * m, 1, 1)},
                    {'src0': a, 'src1': b.view(np.int8), 'htpe': tpe.view(np.int8), 'hids': ids.reshape(-1).view(np.int8)},
                    {'args': args}, f'M={m} K={k} E={experts} used={used} T={tokens} ne11={ne11}')
        case.routes = routes
        out.append(case)
    return out


def oracle(case, weights='f32'):
    """dst[t][s][m] = sum_k half(A_e[m][k]) half(B[t][s % ne11][k]) in float64 (e = routes[t][s]), and the bound."""
    import numpy as np
    args = case.values['args']
    m, k, experts, used, tokens, ne11 = args['ne0'], args['ne00'], args['ne02'], args['ne20'], args['ne21'], args['ne11']
    if weights in BLOCK:
        blocks = case.inputs['src0'].view(np.uint8).reshape(-1, BLOCK[weights])
        d = blocks[:, :2].copy().view(np.float16).astype(np.float64)
        if weights == 'q8_0':
            values = blocks[:, 2:].view(np.int8).astype(np.float64)
        else:
            values = np.concatenate([blocks[:, 2:] & 15, blocks[:, 2:] >> 4], axis=1).astype(np.float64) - 8
        a = (values * d).astype(np.float16).astype(np.float64).reshape(experts, m, k)
    else:
        a = case.inputs['src0'].view(np.float32 if weights == 'f32' else np.float16).astype(np.float16) \
            .astype(np.float64).reshape(experts, m, k)
    b = case.inputs['src1'].view(np.float32).astype(np.float16).astype(np.float64).reshape(tokens, ne11, k)
    want = np.zeros((tokens, used, m))
    bound = np.zeros((tokens, used, m))
    for t in range(tokens):
        for s in range(used):
            ae, bt = a[case.routes[t][s]], b[t, s % ne11]
            want[t, s] = ae @ bt
            bound[t, s] = (k + 2) * 2.0 ** -24 * (np.abs(ae) @ np.abs(bt))
    return want.reshape(-1), bound.reshape(-1)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class LlamaMulMmId(unittest.TestCase):
    KERNEL, WEIGHTS = 'kernel_mul_mm_id_f32_f32', 'f32'

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
        cls.program, cls.report = import_source(cls.source, include=cls.include, kernels=[cls.KERNEL],
                                                module='llama_metal_mul_mm_id', defines=build_defines(entry),
                                                constants=CONSTANTS, shared=SHARED, plan=plan(cls.KERNEL))
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-llama-mul-mm-id-')
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
        self.assertTrue(any('reads and writes its vec<f32, 4> elements as 4 scalars' in a for a in self.report['adaptations']))

    @unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
    def test_cpu_simulator(self):
        import numpy as np
        from differential.simulate import Program
        (Path(self.work.name)/'sim').mkdir(exist_ok=True)
        program = Program(self.generated/'llama_metal_mul_mm_id.cu', self.plan, Path(self.work.name)/'sim')
        for case in cases(self.WEIGHTS):
            want, bound = oracle(case, self.WEIGHTS)
            got = np.asarray(program.run(case)['dst']).view(np.float32)[:len(want)].astype(np.float64)
            self.assertTrue(np.all(np.abs(got - want) <= bound),
                            f'{case.label}: worst {np.max(np.abs(got - want) / np.maximum(bound, 1e-30)):.2f} of the bound')

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


class LlamaMulMmIdHalf(LlamaMulMmId):
    KERNEL, WEIGHTS = 'kernel_mul_mm_id_f16_f32', 'f16'


class LlamaMulMmIdQ8(LlamaMulMmId):
    KERNEL, WEIGHTS = 'kernel_mul_mm_id_q8_0_f32', 'q8_0'


class LlamaMulMmIdQ4(LlamaMulMmId):
    KERNEL, WEIGHTS = 'kernel_mul_mm_id_q4_0_f32', 'q4_0'


if __name__ == '__main__':
    unittest.main()
