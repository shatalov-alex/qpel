// Stand-in: CUDA's WMMA header (nvcuda::wmma), for clang's parse of an original and for running one on
// the CPU simulator. Only the 16 x 16 x 16 shape with half inputs and float accumulators, which the
// importer reads (IMPORT.md, WMMA). NVIDIA spreads a fragment over the warp in a layout it leaves
// unspecified; here every thread keeps the whole tile, loads it, multiplies it (each element c plus the
// products of a row and a column, in k order by fmaf: f16 products are exact in f32) and stores it,
// every thread writing the same values. `x` and `num_elements` are left out: a program that reads
// fragment elements one by one depends on NVIDIA's layout, which qpel does not have.
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

template <class Use, int M, int N, int K> struct qpel_extent;
template <int M, int N, int K> struct qpel_extent<matrix_a, M, N, K> { enum { rows = M, columns = K }; };
template <int M, int N, int K> struct qpel_extent<matrix_b, M, N, K> { enum { rows = K, columns = N }; };
template <int M, int N, int K> struct qpel_extent<accumulator, M, N, K> { enum { rows = M, columns = N }; };

template <class Use, int M, int N, int K, class T, class Layout = void> struct fragment {
  enum { qpel_rows = qpel_extent<Use, M, N, K>::rows, qpel_columns = qpel_extent<Use, M, N, K>::columns };
  T qpel_tile[qpel_rows * qpel_columns];
};

template <class Use, int M, int N, int K, class T, class Layout, class V>
__device__ inline void fill_fragment(fragment<Use, M, N, K, T, Layout>& f, const V& value) {
  for (int i = 0; i < f.qpel_rows * f.qpel_columns; ++i) f.qpel_tile[i] = (T)value;
}

template <class Use, int M, int N, int K, class T, class Layout>
__device__ inline void qpel_load(fragment<Use, M, N, K, T, Layout>& f, const T* p, unsigned ldm, bool column_major) {
  for (int r = 0; r < f.qpel_rows; ++r)
    for (int c = 0; c < f.qpel_columns; ++c)
      f.qpel_tile[r * f.qpel_columns + c] = column_major ? p[c * ldm + r] : p[r * ldm + c];
}
template <class Use, int M, int N, int K, class T>
__device__ inline void load_matrix_sync(fragment<Use, M, N, K, T, row_major>& f, const T* p, unsigned ldm) { qpel_load(f, p, ldm, false); }
template <class Use, int M, int N, int K, class T>
__device__ inline void load_matrix_sync(fragment<Use, M, N, K, T, col_major>& f, const T* p, unsigned ldm) { qpel_load(f, p, ldm, true); }
template <int M, int N, int K, class T>
__device__ inline void load_matrix_sync(fragment<accumulator, M, N, K, T>& f, const T* p, unsigned ldm, layout_t layout) {
  qpel_load(f, p, ldm, layout == mem_col_major);
}

template <int M, int N, int K, class T>
__device__ inline void store_matrix_sync(T* p, const fragment<accumulator, M, N, K, T>& f, unsigned ldm, layout_t layout) {
  for (int r = 0; r < M; ++r)
    for (int c = 0; c < N; ++c)
      (layout == mem_col_major ? p[c * ldm + r] : p[r * ldm + c]) = f.qpel_tile[r * N + c];
}

template <int M, int N, int K, class LA, class LB>
__device__ inline void mma_sync(fragment<accumulator, M, N, K, float>& d, const fragment<matrix_a, M, N, K, __half, LA>& a,
                                const fragment<matrix_b, M, N, K, __half, LB>& b, const fragment<accumulator, M, N, K, float>& c) {
  float out[M * N];
  for (int r = 0; r < M; ++r)
    for (int j = 0; j < N; ++j) {
      float s = c.qpel_tile[r * N + j];
      for (int k = 0; k < K; ++k) s = fmaf(__half2float(a.qpel_tile[r * K + k]), __half2float(b.qpel_tile[k * N + j]), s);
      out[r * N + j] = s;
    }
  for (int i = 0; i < M * N; ++i) d.qpel_tile[i] = out[i];
}
}  // namespace wmma
}  // namespace nvcuda
