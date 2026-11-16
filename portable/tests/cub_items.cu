// qpel's CUB subset against CUB, several items per thread: cub::BlockLoad and cub::BlockStore (direct,
// striped, transposed and warp-transposed; full tiles and partial ones), and the BlockScan and
// BlockReduce methods on item arrays (`T (&items)[ITEMS]`), in blocks of 1 to 4 warps with 2 to 8
// items per thread. Each tile of N * ITEMS elements is loaded, scanned and stored by one block.
#include <cub/cub.cuh>

template <typename T, int N, int ITEMS, cub::BlockLoadAlgorithm LOAD, cub::BlockStoreAlgorithm STORE,
          cub::BlockScanAlgorithm SCAN>
__global__ void items(const T* x, T* inclusive, T* exclusive, T* maxima, T* minima, T* totals, T* reduced,
                      unsigned n) {
  typedef cub::BlockLoad<T, N, ITEMS, LOAD> Load;
  typedef cub::BlockStore<T, N, ITEMS, STORE> Store;
  typedef cub::BlockScan<T, N, SCAN> Scan;
  typedef cub::BlockReduce<T, N> Reduce;
  __shared__ typename Load::TempStorage load_storage;
  __shared__ typename Store::TempStorage store_storage;
  __shared__ typename Scan::TempStorage scan_storage;
  __shared__ typename Reduce::TempStorage reduce_storage;
  const unsigned tile = blockIdx.x * (unsigned)(N * ITEMS);
  const int remaining = (int)(n - tile);
  const int valid = remaining < N * ITEMS ? remaining : N * ITEMS;
  T v[ITEMS];
  if (valid == N * ITEMS) {
    Load(load_storage).Load(x + tile, v);  // a full tile
  } else {
    Load(load_storage).Load(x + tile, v, valid, T(0));  // a partial one: the rest are 0
  }
  __syncthreads();
  T a[ITEMS], b[ITEMS], c[ITEMS], d[ITEMS];
  T total, other;
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
  T top = Reduce(reduce_storage).Reduce(v, cub::Max());
  __syncthreads();
  Store(store_storage).Store(inclusive + tile, a, valid);
  __syncthreads();
  Store(store_storage).Store(exclusive + tile, b, valid);
  __syncthreads();
  Store(store_storage).Store(maxima + tile, c, valid);
  __syncthreads();
  if (valid == N * ITEMS) {
    Store(store_storage).Store(minima + tile, d);
  } else {
    Store(store_storage).Store(minima + tile, d, valid);
  }
  for (int k = 0; k < ITEMS; ++k) {  // the block's totals in each of the thread's elements
    const unsigned i = tile + threadIdx.x * (unsigned)ITEMS + (unsigned)k;
    if (i < n) {
      totals[i] = total + other;
      reduced[i] = threadIdx.x == 0 ? sum - top : T(0);  // only thread 0 has the block's reductions
    }
  }
}

#define LAUNCH(T, N, ITEMS, LOAD, STORE, SCAN)                                                       \
  items<T, N, ITEMS, cub::LOAD, cub::STORE, cub::SCAN><<<(n + N * ITEMS - 1) / (N * ITEMS), N>>>(x, a, b, c, d, t, r, n)
void floats(const float* x, float* a, float* b, float* c, float* d, float* t, float* r, unsigned n) {
  LAUNCH(float, 128, 4, BLOCK_LOAD_WARP_TRANSPOSE, BLOCK_STORE_WARP_TRANSPOSE, BLOCK_SCAN_WARP_SCANS);  // mamba's
  LAUNCH(float, 32, 8, BLOCK_LOAD_DIRECT, BLOCK_STORE_DIRECT, BLOCK_SCAN_RAKING);
  LAUNCH(float, 64, 3, BLOCK_LOAD_TRANSPOSE, BLOCK_STORE_TRANSPOSE, BLOCK_SCAN_RAKING_MEMOIZE);
  LAUNCH(float, 96, 2, BLOCK_LOAD_STRIPED, BLOCK_STORE_STRIPED, BLOCK_SCAN_WARP_SCANS);
}
void ints(const int* x, int* a, int* b, int* c, int* d, int* t, int* r, unsigned n) {
  LAUNCH(int, 64, 2, BLOCK_LOAD_STRIPED, BLOCK_STORE_DIRECT, BLOCK_SCAN_RAKING);
}
void uints(const unsigned* x, unsigned* a, unsigned* b, unsigned* c, unsigned* d, unsigned* t, unsigned* r, unsigned n) {
  LAUNCH(unsigned, 32, 4, BLOCK_LOAD_WARP_TRANSPOSE, BLOCK_STORE_WARP_TRANSPOSE, BLOCK_SCAN_RAKING);
}
