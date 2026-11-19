"""CUDA and Metal importers (clang JSON AST -> qpel IR) and the round-trip matrix."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from frontends.c_import import import_source, Unsupported
from frontends.clang_ast import SourceError
from frontends import roundtrip
from qpel_ir import validate
import qpel


def available(*command):
    try:
        return subprocess.run(list(command), capture_output=True).returncode == 0
    except OSError:
        return False


HAS_CLANG = shutil.which('clang') is not None
HAS_METAL = available('xcrun', '--find', 'metal')
THRESHOLD_CU = (HERE/'cuda_examples/threshold.cu').read_text()
THRESHOLD_METAL = (HERE/'metal_examples/threshold.metal').read_text()


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text, suffix):
        path = Path(self.tmp.name)/('sample' + suffix)
        path.write_text(text)
        return path

    def program(self, text, suffix='.cu', **options):
        program, report = import_source(self.write(text, suffix), **options)
        validate(program)
        return program, report

    def kinds(self, program):
        return [n['kind'] for n in program['nodes']]


@unittest.skipUnless(HAS_CLANG and HAS_METAL, 'needs clang and xcrun metal')
class RoundTripMatrix(unittest.TestCase):
    def test_every_row_matches(self):
        results = list(roundtrip.rows())
        self.assertEqual(len(results), 2*len(list(roundtrip.FIXTURES.iterdir())) + 2*len(roundtrip.EXAMPLES)
                         + sum('fixture' in e for e in roundtrip.corpus_entries())
                         + sum(len(e.get('ports', ())) for e in roundtrip.corpus_entries())
                     + sum(1 for _ in roundtrip.corpus_programs()) + len(roundtrip.triton_kernels()))
        failures = [(row, case, error) for row, case, error in results if error]
        self.assertEqual(failures, [])

    def test_difference_reports_a_path(self):
        a = roundtrip.fixture('threshold')
        b = json.loads(json.dumps(a))
        next(n for n in b['nodes'] if n['kind'] == 'Binary')['attributes']['operator'] = '>'
        self.assertIn('operator', roundtrip.difference(a, b))
        self.assertIsNone(roundtrip.difference(a, a))


@unittest.skipUnless(HAS_CLANG, 'needs clang')
class CudaIdioms(Sources):
    KERNEL = '''__global__ void scale(const float* x, float* y, unsigned int n) {{
        {index}
        {body}
    }}'''

    def kernel(self, body, index='unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;'):
        return self.KERNEL.format(index=index, body=body)

    def test_guard_block_and_any_index_order(self):
        for index in ['unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;',
                      'const unsigned i = threadIdx.x + blockDim.x * blockIdx.x;',
                      'unsigned int i = threadIdx.x + blockIdx.x * blockDim.x;']:
            with self.subTest(index=index):
                program, report = self.program(self.kernel('if (i < n) { y[i] = x[i] * 2.0f; }', index))
                self.assertIn('GridIndex', self.kinds(program))
                self.assertEqual(report['adaptations'], ['scale: count n became a shape (count, 1, 1)'])

    def test_missing_guard_is_reported(self):
        _, report = self.program(THRESHOLD_CU.replace('return;', 'binary[i] = 0u;').replace('if (', 'if (0u * '))
        self.assertEqual(report['warnings'], ['apply_threshold: no bounds guard on the thread index'])
        self.assertEqual(self.program(THRESHOLD_CU)[1]['warnings'], [])

    def test_index_used_inline(self):
        program, _ = self.program(self.kernel(
            'if (blockIdx.x * blockDim.x + threadIdx.x < n) y[blockIdx.x * blockDim.x + threadIdx.x] = 1.0f;', ''))
        self.assertEqual(self.kinds(program).count('GridIndex'), 2)

    def test_float_operations(self):
        program, _ = self.program(self.kernel('if (i >= n) return; y[i] = fmaf(x[i], 0.5f, (float)i);'))
        self.assertIn('F32Fma', self.kinds(program))
        self.assertIn('F32Cast', self.kinds(program))

    def test_helpers_atomics_and_compound_assignment(self):
        program, report = self.program('''
            __device__ unsigned int bump(unsigned int* counts, unsigned int i) {
                return atomicAdd(&counts[i], 1u);
            }
            __global__ void clear(unsigned int* counts, uint3 shape) {
                unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
                if (i < shape.x * shape.y * shape.z) atomicExch(&counts[i], 0u);
            }
            __global__ void count(const unsigned char* mask, unsigned int* counts, uint3 shape) {
                unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
                if (i >= shape.x * shape.y * shape.z) return;
                unsigned int steps = 0u;
                while (steps < 3u) { steps += 1u; ++steps; }
                if (mask[i]) bump(counts, i);
                atomicMin(counts + i, steps);
            }''')
        kinds = self.kinds(program)
        for kind in ['Call', 'AtomicAdd', 'AtomicMin', 'While', 'Assign']:
            self.assertIn(kind, kinds)
        types = {n['attributes'].get('name'): program['nodes'][int(n['children']['type'][0][1:])]['kind']
                 for n in program['nodes'] if n['kind'] == 'Parameter'}
        self.assertEqual(types['counts'], 'AtomicU32BufferType')  # inferred from atomic use
        self.assertEqual(report['helpers'], ['bump'])

    def test_atomic_read_idiom(self):
        program, _ = self.program('''
            __global__ void clear(unsigned int* c, uint3 s) {
                unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
                if (i < s.x * s.y * s.z) atomicExch(c + i, 7u);
            }
            __global__ void k(unsigned int* c, unsigned int* out, uint3 s) {
                unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
                if (i >= s.x * s.y * s.z) return;
                out[i] = atomicAdd(&c[i], 0u);
            }''')
        self.assertIn('AtomicLoad', self.kinds(program))

    def test_macros_are_expanded(self):
        program, _ = self.program('#define LIMIT(d) (d.x * d.y * d.z)\n' +
                                  THRESHOLD_CU.replace('dimensions.x * dimensions.y * dimensions.z', 'LIMIT(dimensions)'))
        self.assertIsNone(roundtrip.difference(self.program(THRESHOLD_CU)[0], program))

    def test_rejections_name_the_construct(self):
        cases = {
            'switch': THRESHOLD_CU.replace('binary[i] =', 'switch (i) { default: break; } binary[i] ='),
            'atomic-update': THRESHOLD_CU.replace('binary[i] =', 'atomicAdd(binary, 1u); binary[i] +='),
            'conversion': THRESHOLD_CU.replace('1u :', '(unsigned int)(unsigned long long)binary :'),
            'for-init': THRESHOLD_CU.replace('binary[i] =', 'for (unsigned j = 0, k = 1; j < 2u; ++j) {} binary[i] ='),
            'double': THRESHOLD_CU.replace('intensity[i] >= cutoff[i]', '0.1 * 3.0 > 0.25'),  # double arithmetic (0.5 > 0.25 is float)
            # (a helper reading threadIdx.x is passed it; the whole builtin as a value is not portable)
            'thread-builtin': '__device__ unsigned int lane(uint3 t) { return t.x; }\n' +
                              THRESHOLD_CU.replace('binary[i] =', 'lane(threadIdx); binary[i] ='),
            'call': '__device__ void elsewhere();\n' + THRESHOLD_CU.replace('binary[i] =', 'elsewhere(); binary[i] ='),  # (defined in another unit)
            'buffer-type': THRESHOLD_CU.replace('unsigned int* binary', 'double* binary'),
            'local-type': THRESHOLD_CU.replace('binary[i] =', 'double2 packed; binary[i] ='),  # (char4 is IR version 18)
            'scalar-kernel-argument': '#include <cuda_bf16.h>\n' + THRESHOLD_CU.replace(
                'uint3 dimensions', 'uint3 dimensions, __nv_bfloat16 extra'),  # (bool is now a u32 argument)
            'no-extent': THRESHOLD_CU.replace('uint3 dimensions', 'unsigned int n, unsigned int m').replace(
                'dimensions.x * dimensions.y * dimensions.z', 'n || i >= m'),
        }
        for code, source in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(Unsupported) as caught:
                    self.program(source)
                self.assertEqual(caught.exception.code, code)
                self.assertRegex(str(caught.exception), r'sample\.cu:\d+:\d+: \[')

    def test_raw_thread_builtins_make_a_groups_kernel(self):
        program, report = self.program('''
            __global__ void scale(const float* x, float* y, float s, long long n) {
                long long stride = (long long)blockDim.x * gridDim.x;
                for (long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x; i < n; i += stride)
                    y[i] = s * x[i];
            }''')
        nodes = {n['id']: n for n in program['nodes']}
        kinds = {n['kind'] for n in program['nodes']}
        self.assertIn('Groups', kinds)
        self.assertTrue({'GroupIndex', 'LocalIndex', 'GroupSize', 'GroupCount'} <= kinds)
        params = [n for n in program['nodes'] if n['kind'] == 'Parameter']
        self.assertEqual([p['attributes']['name'] for p in params], ['x', 'y', 's', 'n'])  # n stays an argument
        stage = next(n for n in program['nodes'] if n['kind'] == 'Dispatch')
        self.assertEqual(stage['attributes']['groupsX'], '(W*H*N + 63)/64')
        self.assertTrue(any('no launch plan' in a for a in report['adaptations']))

    def test_template_instantiations_become_functions(self):
        program, report = self.program('''template <typename T> __device__ T twice(T v) { return v + v; }
''' + THRESHOLD_CU.replace('1u :', '(twice(1u) + (unsigned)twice(1)) :'))
        self.assertEqual(report['helpers'], ['twice_u32', 'twice_i32'])
        self.assertTrue(any('one per instantiation' in a for a in report['adaptations']))

    def test_version_2_constructs(self):
        program, _ = self.program('''
            __device__ int clamp_sum(const int* x, unsigned int n) {
                int total = 0;
                for (unsigned int k = 0; k < n; k++) {
                    if (x[k] < 0) continue; else if (x[k] > 1000) break;
                    total += x[k] << 1;
                }
                return total > 0 ? total : -total;
            }
            __global__ void k(const int* x, int* y, unsigned int* z, unsigned int n) {
                unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
                if (i < n) {
                    y[i] = clamp_sum(x, 4u) / -3 + (int)(i % 2u);
                    z[i] = ~(unsigned)x[i] >> 3;
                } else {
                    return;
                }
            }''')
        self.assertEqual(program['version'], 2)
        kinds = self.kinds(program)
        for kind in ['For', 'Break', 'Continue', 'I32Literal', 'I32Cast', 'U32Cast', 'I32BufferType', 'ReadOnlyI32BufferType']:
            self.assertIn(kind, kinds)
        self.assertTrue(any(n['kind'] == 'If' and 'elseBody' in n['children'] for n in program['nodes']))
        if HAS_METAL:
            self.assertEqual(roundtrip.reimport(program), {'cuda': None, 'metal': None})

    def test_scalar_arguments_stay_arguments(self):
        program, report = self.program(THRESHOLD_CU.replace('uint3 dimensions', 'uint3 dimensions, unsigned int bias, float gain')
                                       .replace('1u :', 'bias + (unsigned int)(gain > 0.5f) :'))
        self.assertEqual(report['adaptations'], [])
        values = {n['attributes']['name']: n for n in program['nodes'] if n['kind'] == 'Resource' and n['attributes']['kind'] == 'value'}
        self.assertEqual(set(values), {'bias', 'gain'})
        self.assertEqual(program['version'], 2)

    def test_2d_kernels(self):
        source = '''__global__ void k(const float* a, float* b, uint3 s) {
            unsigned int x = threadIdx.x + blockDim.x * blockIdx.x;
            unsigned int y = blockIdx.y * blockDim.y + threadIdx.y;
            if (x < s.x && y < s.y) b[y * s.x + x] = a[y * s.x + x] * 2.0f;
        }'''
        program, report = self.program(source)
        self.assertEqual({n['attributes']['axis'] for n in program['nodes'] if n['kind'] == 'ThreadIndex'}, {'x', 'y'})
        self.assertIn('Grid3D', self.kinds(program))
        self.assertEqual(report['warnings'], [])
        # Sized by scalar width and height: they become one shape (w, h, 1).
        scalar = source.replace('uint3 s', 'unsigned int w, int h').replace('s.x', 'w').replace('y < s.y', 'y < h')
        sized, report = self.program(scalar)
        self.assertIn('k: w, h became a shape (w, h, 1)', report['adaptations'])
        params = [n['attributes']['name'] for n in sized['nodes'] if n['kind'] == 'Parameter']
        self.assertEqual(params, ['a', 'b', 'shape'])
        self.assertEqual(roundtrip.reimport(sized), {'cuda': None, **({'metal': None} if HAS_METAL else {})})
        with self.assertRaises(Unsupported) as caught:
            self.program(scalar.replace('x < w && ', ''))  # nothing bounds x
        self.assertEqual(caught.exception.code, 'no-extent')
        _, report = self.program(source.replace('x < s.x && ', ''))
        self.assertEqual(report['warnings'], ['k: no bounds guard on the thread index (x)'])

    def test_unsigned_chars_compared_as_int_stay_unsigned(self):
        program, _ = self.program(THRESHOLD_CU)
        self.assertEqual(program['version'], 1)
        self.assertNotIn('I32Cast', self.kinds(program))

    def test_mixed_compound_assignment_converts_like_c(self):
        program, _ = self.program(THRESHOLD_CU.replace('binary[i] =', 'int s = 5; s += i; binary[i] = (unsigned)s +'))
        self.assertIn('U32Cast', self.kinds(program))
        self.assertIn('I32Cast', self.kinds(program))

    def test_qpel_checks_still_apply(self):
        for label, source in [('write input', THRESHOLD_CU.replace('binary[i] =', 'binary[i] = 0u; intensity[i] =')),
                              ('duplicate name', THRESHOLD_CU.replace('cutoff', 'intensity'))]:
            with self.subTest(label=label), self.assertRaises((ValueError, SourceError)):
                self.program(source)

    def test_clang_errors_are_reported(self):
        with self.assertRaisesRegex(SourceError, 'clang rejected'):
            self.program(THRESHOLD_CU[:-2])


@unittest.skipUnless(HAS_METAL, 'needs xcrun metal')
class MetalIdioms(Sources):
    def test_matches_cuda_equivalent(self):
        if not HAS_CLANG:
            self.skipTest('needs clang')
        cuda, _ = self.program(THRESHOLD_CU, module='threshold')
        metal, _ = self.program(THRESHOLD_METAL, '.metal', module='threshold')
        self.assertIsNone(roundtrip.difference(cuda, metal))

    def test_buffer_indices_become_bindings(self):
        source = THRESHOLD_METAL.replace('buffer(0)', 'buffer(5)')
        program, _ = self.program(source, '.metal')
        bindings = {n['attributes']['name']: n['attributes']['binding'] for n in program['nodes'] if n['kind'] == 'Parameter'}
        self.assertEqual(bindings['intensity'], 5)

    def test_atomics_and_half(self):
        program, _ = self.program('''
            #include <metal_stdlib>
            using namespace metal;
            kernel void clear(device atomic_uint* c [[buffer(0)]], constant uint3& s [[buffer(1)]],
                              uint i [[thread_position_in_grid]]) {
                if (i < s.x * s.y * s.z) atomic_store_explicit(c + i, 0u, memory_order_relaxed);
            }
            kernel void k(device const half* x [[buffer(0)]], device atomic_uint* c [[buffer(1)]],
                          device half* y [[buffer(2)]], constant uint3& s [[buffer(3)]],
                          uint i [[thread_position_in_grid]]) {
                if (i >= s.x * s.y * s.z) return;
                atomic_fetch_add_explicit(c + i, 1u, memory_order_relaxed);
                y[i] = half(fma(float(x[i]), 2.0f, 1.0f));
            }''', '.metal')
        for kind in ['AtomicAdd', 'F16Cast', 'F32Cast', 'F32Fma']:
            self.assertIn(kind, self.kinds(program))

    def test_uint2_position(self):
        program, _ = self.program('''
            #include <metal_stdlib>
            using namespace metal;
            kernel void k(device float* b [[buffer(0)]], constant uint3& s [[buffer(1)]], uint2 p [[thread_position_in_grid]]) {
                if (p.x < s.x && p.y < s.y) b[p.y * s.x + p.x] = 1.0f;
            }''', '.metal')
        self.assertEqual(sorted(n['attributes']['axis'] for n in program['nodes'] if n['kind'] == 'ThreadIndex'), ['x', 'x', 'y', 'y'])

    def test_rejections(self):
        cases = {
            'thread-builtin': ('uint i [[thread_position_in_grid]]',
                               'uint i [[thread_position_in_grid]], uint t [[thread_index_in_threadgroup]]'),
        }
        base = '''#include <metal_stdlib>
            using namespace metal;
            kernel void k(device atomic_uint* c [[buffer(0)]], constant uint3& s [[buffer(1)]],
                          uint i [[thread_position_in_grid]]) {
                if (i >= s.x * s.y * s.z) return;
                atomic_fetch_add_explicit(c + i, 1u, memory_order_relaxed);
            }'''
        for code, (old, new) in cases.items():
            source = base.replace(old, new)
            if code == 'thread-builtin':
                source = source.replace('1u, memory', 't, memory')
            with self.subTest(code=code), self.assertRaises(Unsupported) as caught:
                self.program(source, '.metal')
            self.assertEqual(caught.exception.code, code)


@unittest.skipUnless(HAS_CLANG and HAS_METAL, 'needs clang and xcrun metal')
class Command(Sources):
    def test_translate_builds_both_backends(self):
        out = Path(self.tmp.name)/'pkg'
        report = qpel.translate(HERE/'cuda_examples/masked_copy.cu', out, block=32)
        self.assertEqual(report['status'], 'generated')
        generated = out/'package/generated'
        for suffix in ('.cu', '.metal'):
            self.assertTrue((generated/('masked_copy' + suffix)).is_file())
        self.assertEqual(json.loads((generated/'pipeline.json').read_text())['stages'][0]['blockSize'], 32)
        self.assertTrue((out/'package/original.cu').is_file())
        self.assertIn('adaptations', json.loads((out/'package/import-report.json').read_text()))

    def test_rejected_source_writes_nothing(self):
        out = Path(self.tmp.name)/'pkg'
        source = self.write(THRESHOLD_CU.replace('return;', 'return; switch (i) { default: break; }'), '.cu')
        with self.assertRaises(Unsupported):
            qpel.translate(source, out)
        self.assertFalse(out.exists())

    def test_import_command_refuses_existing_output(self):
        out = self.write('{}', '.json')
        with self.assertRaises(SystemExit):
            qpel.main(['import', str(HERE/'cuda_examples/threshold.cu'), '--out', str(out)])
        self.assertEqual(out.read_text(), '{}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
