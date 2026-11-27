"""Which pointers into a kernel's carved shared memory are views of their own (frontends/views.py).

A view is declared directly in the kernel's body, at an offset that is the same in every thread
(gsplat's `&rgbs_batch[block_size * CDIM]`). A thread's own pointer into a view, such as
`&tile[threadIdx.x]`, or a pointer made inside a branch, points into the view's memory: making it
separate memory would lose what is written through it. The block is then left as it is."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

HEAD = '''#include <cstdint>
__global__ void k(float *out, uint32_t n) {
    extern __shared__ char smem[];
    const uint32_t size = blockDim.x;
    float *tile = reinterpret_cast<float *>(smem);
'''
TAIL = '''    tile[threadIdx.x] = 1.0f;
    __syncthreads();
    out[threadIdx.x] = tile[(threadIdx.x + 1) % size];
}
'''
UNIFORM = '    float *rest = &tile[size];\n    rest[threadIdx.x] = 2.0f;\n'
PER_THREAD = '    float *mine = &tile[threadIdx.x];\n    mine[0] = 2.0f;\n'
IN_BRANCH = '    if (n > 0) {\n        float *rest = &tile[size];\n        rest[threadIdx.x] = 2.0f;\n    }\n'


def adaptations(body):
    """The import's notes on carved views (the import itself must succeed: a block left whole imports too)."""
    from frontends.c_import import import_source
    with tempfile.TemporaryDirectory(prefix='qpel-views-') as tmp:
        path = Path(tmp)/'k.cu'
        path.write_text(HEAD + body + TAIL)
        _, report = import_source(path, shared={'tile': 64, 'rest': 64, 'mine': 64, 'smem': 512})
    return [a for a in report['adaptations'] if 'became shared memory of their own' in a]


@unittest.skipUnless(shutil.which('clang++'), 'clang++ required')
class SharedViews(unittest.TestCase):
    def test_a_uniform_offset_carves_a_view(self):
        carved = adaptations(UNIFORM)
        self.assertEqual(len(carved), 1)
        self.assertIn('rest: dynamic shared float[]', carved[0])

    def test_a_threads_pointer_is_not_a_view(self):
        self.assertEqual(adaptations(PER_THREAD), [])

    def test_a_pointer_made_in_a_branch_is_not_a_view(self):
        self.assertEqual(adaptations(IN_BRANCH), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
