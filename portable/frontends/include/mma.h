// Stand-in: CUDA's WMMA header (nvcuda::wmma) for importing: declarations only. The importer reads a
// 16 x 16 x 16 fragment as a 2 x 2 array of qpel's 8 x 8 matrices (IMPORT.md, WMMA); differential/include/mma.h
// runs the same calls on the CPU simulator.
#pragma once
#include <cuda_fp16.h>

namespace nvcuda {
namespace wmma {
struct matrix_a {};
struct matrix_b {};
struct accumulator {};
struct row_major {};
struct col_major {};
enum layout_t { mem_row_major, mem_col_major };

template <class Use, int M, int N, int K, class T, class Layout = void> struct fragment {
  T qpel_tile[M * N];  // (its elements are not read one by one: NVIDIA leaves their place in the warp unspecified)
};

template <class Use, int M, int N, int K, class T, class Layout, class V>
__device__ void fill_fragment(fragment<Use, M, N, K, T, Layout>& f, const V& value);
template <class Use, int M, int N, int K, class T, class Layout>
__device__ void load_matrix_sync(fragment<Use, M, N, K, T, Layout>& f, const T* p, unsigned ldm);
template <int M, int N, int K, class T>
__device__ void load_matrix_sync(fragment<accumulator, M, N, K, T>& f, const T* p, unsigned ldm, layout_t layout);
template <int M, int N, int K, class T>
__device__ void store_matrix_sync(T* p, const fragment<accumulator, M, N, K, T>& f, unsigned ldm, layout_t layout);
template <int M, int N, int K, class TA, class TC, class LA, class LB>
__device__ void mma_sync(fragment<accumulator, M, N, K, TC>& d, const fragment<matrix_a, M, N, K, TA, LA>& a,
                         const fragment<matrix_b, M, N, K, TA, LB>& b, const fragment<accumulator, M, N, K, TC>& c,
                         bool satf = false);
}  // namespace wmma
}  // namespace nvcuda
