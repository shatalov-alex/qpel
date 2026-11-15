// qpel's CUB subset against CUB: every BlockScan and BlockReduce method it has, both scan
// algorithms, int, unsigned int and float, and blocks of 1, 2, 3, 4, 5 and 8 warps (raking
// segments of 1 to 8, with and without padding; qpel blocks have at most 256 threads). Each
// thread's results go to its own elements.
#include <cub/cub.cuh>

// A template kernel (qpel has shared memory in kernels, not in device functions); each
// instantiation is its own qpel kernel.
template <typename T, int N, cub::BlockScanAlgorithm ALGORITHM>
__global__ void blocks(const T* x, T* inclusive, T* exclusive, T* maxima, T* minima, T* totals, T* reduced,
                       unsigned n) {
  typedef cub::BlockScan<T, N, ALGORITHM> Scan;
  typedef cub::BlockReduce<T, N> Reduce;
  __shared__ typename Scan::TempStorage scan_storage;
  __shared__ typename Reduce::TempStorage reduce_storage;
  const unsigned i = blockIdx.x * N + threadIdx.x;
  const T v = i < n ? x[i] : T(0);
  T a, b, c, d, total, other;
  Scan(scan_storage).InclusiveSum(v, a, total);
  __syncthreads();
  Scan(scan_storage).ExclusiveSum(v, b, other);
  __syncthreads();
  Scan scan(scan_storage);  // a named object, used twice
  scan.InclusiveScan(v, c, cub::Max());
  __syncthreads();
  scan.ExclusiveScan(v, d, T(7), cub::Min(), other);
  __syncthreads();
  T sum = Reduce(reduce_storage).Sum(v);
  __syncthreads();
  T top = Reduce(reduce_storage).Reduce(v, cub::Max(), (int)(n % N) + 1);  // a partial tile
  if (i < n) {
    inclusive[i] = a;
    exclusive[i] = b;
    maxima[i] = c;
    minima[i] = d;
    totals[i] = total + other;
    reduced[i] = threadIdx.x == 0 ? sum - top : T(0);  // only thread 0 has the block's reductions
  }
}

#define LAUNCH(T, N, ALGORITHM) blocks<T, N, cub::ALGORITHM><<<(n + N - 1) / N, N>>>(x, a, b, c, d, t, r, n)
void floats(const float* x, float* a, float* b, float* c, float* d, float* t, float* r, unsigned n) {
  LAUNCH(float, 256, BLOCK_SCAN_RAKING);
  LAUNCH(float, 96, BLOCK_SCAN_RAKING);
  LAUNCH(float, 160, BLOCK_SCAN_RAKING_MEMOIZE);
  LAUNCH(float, 128, BLOCK_SCAN_WARP_SCANS);
  LAUNCH(float, 256, BLOCK_SCAN_WARP_SCANS);
}
void ints(const int* x, int* a, int* b, int* c, int* d, int* t, int* r, unsigned n) { LAUNCH(int, 64, BLOCK_SCAN_RAKING); }
void uints(const unsigned* x, unsigned* a, unsigned* b, unsigned* c, unsigned* d, unsigned* t, unsigned* r, unsigned n) {
  LAUNCH(unsigned, 32, BLOCK_SCAN_RAKING);
}
