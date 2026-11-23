"""qpel's segmented and merge sorts (frontends/include/libraries/qpel_library/device_segmented_sort.cuh,
frontends/library.py): cub::DeviceSegmentedRadixSort::SortPairs and cub::DeviceMergeSort::SortPairs by less,
the two other sorts gsplat calls, from the radix sort and a few small kernels. The library's CUDA runs as the
original on the CPU simulator, qpel's CUDA there and qpel's Metal on the Apple GPU, against numpy: each
segment stably sorted by its bits (elements in no segment left in place), and the merge sort as a stable sort
(one of the orders NVIDIA's merge sort, which is not stable, may give). NVIDIA's own CUB at every size is for a
T4 (differential/extra_rows.py)."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'tests'))
from qpel_test_support import unavailable  # noqa: E402
from test_device_radix_sort import NUMPY, data, oracle as radix_oracle  # noqa: E402

# (key, value, offset): gsplat sorts int64 intersection ids with int32 gaussian ids in segments given by int64 offsets
SEGMENTED = (('i64', 'i32', 'i64'), ('u32', 'f32', 'i32'))
MERGE = (('i64', 'i32'), ('u32', 'f32'))
SIZES = (1, 256, 257, 3000, 20000)
LAYOUTS = ('one', 'contiguous', 'scattered')


def segments(n, layout, rng):
    """[(begin, end)]: one segment of everything; segments covering everything in order (gsplat's images, some
    empty); or segments in shuffled order with gaps between them."""
    if layout == 'one':
        return [(0, n)]
    cuts = sorted(rng.integers(0, n + 1, max(1, n // 300) + 2).tolist())
    if layout == 'contiguous':
        bounds = [0] + cuts + [n]
        return list(zip(bounds[:-1], bounds[1:]))
    out = [(a, b) for a, b in zip(cuts[::2], cuts[1::2])]
    order = rng.permutation(len(out))
    return [out[i] for i in order] or [(0, 0)]


def segmented_oracle(keys, values, spans, begin, end):
    """CUB's result in each segment (stable by the bits), the input elsewhere."""
    k, v = keys.copy(), values.copy()
    for a, b in spans:
        if b > a:
            k[a:b], v[a:b] = radix_oracle(keys[a:b], values[a:b], begin, end)
    return k, v


def written(n, spans):
    """Where CUB writes: in segments."""
    import numpy as np
    mask = np.zeros(n, bool)
    for a, b in spans:
        mask[a:b] = True
    return mask


def segmented_cases(key, value, offset, sizes=SIZES):
    import numpy as np
    from differential.simulate import Case
    bits = 32 if key in ('u32', 'i32') else 64
    for n in sizes:
        for layout in LAYOUTS:
            for begin, end in ((0, bits), (0, 20) if bits == 32 else (0, 46)):
                few = layout != 'scattered'
                keys, values = data(key, value, n, few)
                rng = np.random.default_rng(n * 11 + LAYOUTS.index(layout))
                spans = segments(n, layout, rng)
                b = np.array([s[0] for s in spans], NUMPY[offset])
                e = np.array([s[1] for s in spans], NUMPY[offset])
                c = Case({'n': (n, 1, 1), 'segments': (len(spans), 1, 1)},
                         {'keys_in': keys, 'values_in': values, 'begin_offsets': b, 'end_offsets': e},
                         {'begin_bit': begin, 'end_bit': end},
                         f'{key}/{value}/{offset} n={n} {layout} ({len(spans)} segments) bits={begin}..{end}')
                yield c, keys, values, spans, begin, end


def merge_oracle(keys, values):
    import numpy as np
    order = np.argsort(keys, kind='stable')
    return keys[order], values[order]


def merge_cases(key, value, sizes=SIZES):
    from differential.simulate import Case
    for n in sizes:
        for few in (False, True):
            keys, values = data(key, value, n, few)
            yield Case({'n': (n, 1, 1)}, {'keys': keys, 'values': values}, {},
                       f'{key}/{value} n={n}{" few" if few else ""}'), keys, values


def same_pairs(got_keys, got_values, want_keys, want_values):
    """None if the keys are the same and so is each run of equal keys' set of values (NVIDIA's merge sort is
    not stable), else the problem."""
    import numpy as np
    if not np.array_equal(got_keys, want_keys):
        return 'keys differ'
    bits = lambda v: v.view(np.uint32 if v.itemsize == 4 else np.uint64)
    order = lambda k, v: np.lexsort((bits(v), k))
    a, b = order(got_keys, got_values), order(want_keys, want_values)
    if not np.array_equal(bits(got_values)[a], bits(want_values)[b]):
        return "a run of equal keys has other values"
    return None


class Built:
    def __init__(self, text, plan, work):
        from frontends.c_import import import_source
        from qpel import compile_program
        from qpel_ir import validate
        self.plan, self.work = plan, work
        self.source = work/(plan['module'] + '.cu')
        self.source.write_text(text)
        self.program, self.report = import_source(self.source, plan=plan)
        validate(self.program)
        (work/'p.json').write_text(json.dumps(self.program))
        compile_program(work/'p.json', work/'build')
        self.generated = work/'build/package/generated'
        self.cuda = self.generated/(plan['module'] + '.cu')


def segmented_built(key, value, offset, work):
    from frontends.library import device_segmented_radix_sort_pairs
    return Built(*device_segmented_radix_sort_pairs(key, value, offset), work)


def merge_built(key, value, work):
    from frontends.library import device_merge_sort_pairs
    return Built(*device_merge_sort_pairs(key, value), work)


def check_segmented(test, out, keys, values, spans, begin, end):
    import numpy as np
    want_keys, want_values = segmented_oracle(keys, values, spans, begin, end)
    np.testing.assert_array_equal(np.asarray(out['keys_out']).view(keys.dtype), want_keys)
    np.testing.assert_array_equal(np.asarray(out['values_out']).view(values.dtype), want_values)


def check_merge(test, out, keys, values):
    import numpy as np
    want_keys, want_values = merge_oracle(keys, values)
    np.testing.assert_array_equal(np.asarray(out['keys']).view(keys.dtype), want_keys)
    np.testing.assert_array_equal(np.asarray(out['values']).view(values.dtype), want_values)


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class SegmentedAndMergeSorts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qpel-segmented-sort-')
        cls.segmented, cls.merge = {}, {}
        for variant in SEGMENTED:
            work = Path(cls.tmp.name)/('s_' + '_'.join(variant))
            work.mkdir()
            cls.segmented[variant] = segmented_built(*variant, work)
        for variant in MERGE:
            work = Path(cls.tmp.name)/('m_' + '_'.join(variant))
            work.mkdir()
            cls.merge[variant] = merge_built(*variant, work)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_plan(self):
        from qpel_text import print_program
        b = self.segmented['i64', 'i32', 'i64']
        # init, mark, 8 passes of the keys, gather, 4 passes of the places, 2 gathers
        self.assertEqual(len(b.plan['stages']), 2 + 8 * 6 + 1 + 4 * 6 + 2)
        mark = b.plan['stages'][1]
        self.assertEqual(mark['grid'], 'segments')
        text = print_program(b.program)
        self.assertIn('input begin_offsets: buffer<i64, write> in segments;', text)
        m = self.merge['i64', 'i32']
        self.assertEqual(len(m.plan['stages']), 8 * 6)
        self.assertIn('inout keys: buffer<i64, write> in n;', print_program(m.program))

    def test_rejected(self):
        from frontends.library import device_segmented_radix_sort_pairs, expand
        with self.assertRaises(ValueError):
            device_segmented_radix_sort_pairs('i64', 'i32', 'f32')
        with self.assertRaises(ValueError):
            expand('', {'resources': [], 'stages': [{'library': 'cub::DeviceMergeSort::SortPairs', 'key': 'i64',
                                                     'value': 'i32', 'compare': 'greater', 'bindings': {}}]})

    def test_cpu_simulator(self):
        from differential.simulate import Program
        for variant, b in self.segmented.items():
            original, ours = self.programs(Program, b)
            for c, keys, values, spans, begin, end in segmented_cases(*variant, SIZES[:-1]):
                with self.subTest(c.label):
                    for program in (original, ours):
                        check_segmented(self, program.run(c), keys, values, spans, begin, end)
        for variant, b in self.merge.items():
            original, ours = self.programs(Program, b)
            for c, keys, values in merge_cases(*variant, SIZES[:-1]):
                with self.subTest(c.label):
                    for program in (original, ours):
                        check_merge(self, program.run(c), keys, values)

    @staticmethod
    def programs(Program, b):
        for folder in ('o', 'q'):
            (b.work/folder).mkdir(exist_ok=True)
        return Program(b.source, b.plan, b.work/'o', original=b.report), Program(b.cuda, b.plan, b.work/'q')

    def test_apple_gpu(self):
        try:
            import torch
            if not torch.backends.mps.is_available():
                unavailable(self, 'gpu', 'no Apple GPU (PyTorch MPS)')
        except ImportError:
            unavailable(self, 'gpu', 'PyTorch required for the Apple GPU tests')
        from differential import matrix, metal
        for variant, b in self.segmented.items():
            host = matrix.load_host(b.generated)
            for c, keys, values, spans, begin, end in segmented_cases(*variant):
                with self.subTest(c.label):
                    check_segmented(self, metal.run_generated(host, b.plan, c), keys, values, spans, begin, end)
        for variant, b in self.merge.items():
            host = matrix.load_host(b.generated)
            for c, keys, values in merge_cases(*variant):
                with self.subTest(c.label):
                    check_merge(self, metal.run_generated(host, b.plan, c), keys, values)


if __name__ == '__main__':
    unittest.main(verbosity=2)
