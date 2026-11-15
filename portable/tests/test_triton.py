"""qpel's Triton frontend: the TTIR reader, the lowering to qpel, and the behaviour rows against
Triton's interpreter (those need Docker and the qpel/triton image: frontends/triton/README.md)."""
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from frontends.triton import ttir  # noqa: E402
from frontends.triton.lower import Unsupported, lower  # noqa: E402
from frontends.triton.__main__ import corpus_kernels, corpus_program  # noqa: E402
from frontends import roundtrip  # noqa: E402
from qpel_ir import validate  # noqa: E402
from qpel_text import parse_program  # noqa: E402

BUILD = HERE/'corpus/triton/build'

REDUCE_ROWS = '''
module {
  tt.func public @row_sums(%x: !tt.ptr<f32> {tt.divisibility = 16 : i32} loc("x"(#loc)), %out: !tt.ptr<f32> loc("out"(#loc))) attributes {noinline = false} {
    %r = tt.make_range {end = 4 : i32, start = 0 : i32} : tensor<4xi32> loc(#loc1)
    %c = tt.make_range {end = 8 : i32, start = 0 : i32} : tensor<8xi32> loc(#loc1)
    %c8 = arith.constant dense<8> : tensor<4x1xi32> loc(#loc1)
    %r1 = tt.expand_dims %r {axis = 1 : i32} : tensor<4xi32> -> tensor<4x1xi32> loc(#loc1)
    %r2 = arith.muli %r1, %c8 : tensor<4x1xi32> loc(#loc1)
    %r3 = tt.broadcast %r2 : tensor<4x1xi32> -> tensor<4x8xi32> loc(#loc1)
    %c1 = tt.expand_dims %c {axis = 0 : i32} : tensor<8xi32> -> tensor<1x8xi32> loc(#loc1)
    %c2 = tt.broadcast %c1 : tensor<1x8xi32> -> tensor<4x8xi32> loc(#loc1)
    %o = arith.addi %r3, %c2 : tensor<4x8xi32> loc(#loc1)
    %p = tt.splat %x : !tt.ptr<f32> -> tensor<4x8x!tt.ptr<f32>> loc(#loc1)
    %p1 = tt.addptr %p, %o : tensor<4x8x!tt.ptr<f32>>, tensor<4x8xi32> loc(#loc1)
    %v = tt.load %p1 : tensor<4x8x!tt.ptr<f32>> loc(#loc1)
    %s = "tt.reduce"(%v) <{axis = 1 : i32}> ({
    ^bb0(%a: f32 loc(callsite(#loc1 at #loc2)), %b: f32 loc(callsite(#loc1 at #loc2))):
      %m = arith.maxnumf %a, %b : f32 loc(#loc1)
      tt.reduce.return %m : f32 loc(#loc1)
    }) : (tensor<4x8xf32>) -> tensor<4xf32> loc(#loc1)
    %q = tt.splat %out : !tt.ptr<f32> -> tensor<4x!tt.ptr<f32>> loc(#loc1)
    %q1 = tt.addptr %q, %r : tensor<4x!tt.ptr<f32>>, tensor<4xi32> loc(#loc1)
    tt.store %q1, %s : tensor<4x!tt.ptr<f32>> loc(#loc1)
    tt.return loc(#loc)
  } loc(#loc)
} loc(#loc)
#loc = loc("k.py":1:0)
#loc1 = loc("k.py":2:0)
#loc2 = loc("k.py":3:0)
'''


class Reader(unittest.TestCase):
    def test_types(self):
        self.assertEqual(ttir.parse_type('tensor<16x32x!tt.ptr<f32>>').shape, (16, 32))
        self.assertEqual(ttir.parse_type('tensor<16x32x!tt.ptr<f32>>').element, 'ptr<f32>')
        self.assertEqual(ttir.parse_type('!tt.ptr<f16>').pointee, 'f16')
        self.assertEqual(ttir.parse_type('tensor<128xi1>').numel, 128)

    def test_locations_are_dropped(self):
        self.assertEqual(ttir.strip_locations('a loc(callsite(#loc1 at #loc2)) b\n#loc1 = loc("x":1:2)'), 'a b')

    def test_corpus_functions(self):
        for name in corpus_kernels():
            f = ttir.parse((BUILD/f'{name}.ttir').read_text())
            self.assertEqual(f.name, name)
            self.assertTrue(f.body.ops)
        loop = next(o for o in ttir.parse((BUILD/'layer_norm_kernel.ttir').read_text()).body.ops if o.name == 'scf.for')
        self.assertTrue(loop.regions[0].ops)
        softmax = ttir.parse((BUILD/'softmax_kernel.ttir').read_text())
        reduce = next(o for o in softmax.body.ops if o.name == 'tt.reduce')
        self.assertEqual(reduce.attribute('axis'), 0)
        self.assertEqual([str(t) for t in reduce.result_types], ['f32'])
        self.assertEqual(len(reduce.regions[0].arguments), 2)


class Lowering(unittest.TestCase):
    def test_corpus_kernels_validate_and_match_their_fixtures(self):
        for name in corpus_kernels():
            program = corpus_program(name)
            for target in ('cuda', 'metal'):
                validate(program, target)
            self.assertIsNone(roundtrip.difference(roundtrip.fixture(f'triton_{name}'), program), name)

    def test_one_program_is_one_group(self):
        program = corpus_program('softmax_kernel')
        kinds = [n['kind'] for n in program['nodes']]
        self.assertIn('Barrier', kinds)  # the row is shared for its reductions
        dispatch = next(n for n in program['nodes'] if n['kind'] == 'Dispatch')
        self.assertEqual(dispatch['attributes']['blockSize'], 128)  # num_warps 4

    def test_a_reduction_along_the_last_axis(self):
        text, lowering = lower(REDUCE_ROWS, 1)
        program = parse_program(text)
        validate(program)
        self.assertEqual(lowering.float_sums, [])  # a max: exact
        self.assertIn('shared<f32, 32>', text)

    def test_an_unknown_operation_is_reported(self):
        with self.assertRaises(Unsupported) as caught:
            lower(REDUCE_ROWS.replace('arith.maxnumf', 'arith.maximumf'), 1)
        self.assertIn('reduction', str(caught.exception))
        with self.assertRaises(Unsupported):
            lower(REDUCE_ROWS.replace('tt.load %p1', 'tt.atomic_rmw fadd, relaxed, gpu, %p1'), 1)


class Behaviour(unittest.TestCase):
    def test_rows(self):
        from differential import triton
        if not triton.docker_ready():
            self.skipTest('needs Docker and the qpel/triton image (frontends/triton/README.md)')
        results = list(triton.rows())
        self.assertTrue(results)
        failures = [r for r in results if r[2] and not r[2].startswith('skipped')]
        self.assertEqual(failures, [])
        rows = {r[0] for r in results if not r[2]}
        self.assertIn('triton -> qpel -> cuda', rows)


@unittest.skipUnless(__import__('os').environ.get('QPEL_NVIDIA') == '1', 'GPGPU-Sim rows run with QPEL_NVIDIA=1')
class Nvidia(unittest.TestCase):
    def test_triton_ptx_rows(self):
        """Triton's own PTX on GPGPU-Sim against Triton's interpreter and qpel's CUDA."""
        from differential import triton_nvidia
        results = list(triton_nvidia.rows())
        if results and results[0][2] and results[0][2].startswith('skipped'):
            self.skipTest(results[0][2])
        self.assertEqual([r for r in results if r[2]], [])


if __name__ == '__main__':
    unittest.main()
