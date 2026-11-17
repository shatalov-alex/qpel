// A miniature of mamba's selective-scan kernel (corpus/mamba) for what qpel reads before import
// (frontends/statics.py, frontends/views.py): a traits class whose type aliases and static constexpr
// members the kernel reads (`typename Ktraits::input_t *`, std::is_same_v, a constexpr helper,
// sizeof), the kernel's arguments as one struct of pointers (`void *` fields cast where they are
// used, a bool flag, a 64-bit stride, an optional buffer tested against null), kNRows = 1 rows of
// everything (`T vals[kNRows][kNItems]`, `for (int r = 0; r < kNRows; ++r)`), and dynamic shared
// memory carved into views: CUB TempStorages and a running carry. One block adds up its row in
// chunks of kNThreads * kNItems with cub::BlockScan, carrying the total from chunk to chunk.
#include <cub/cub.cuh>
#include <algorithm>
#include <cstdint>
#include <type_traits>

struct Params {
  using index_t = uint64_t;
  int seqlen;
  bool softplus;
  index_t row_stride;
  void *__restrict__ x_ptr;
  void *__restrict__ bias_ptr;  // optional
  void *__restrict__ out_ptr;
  void *__restrict__ unused_ptr;  // never read: not an argument of qpel's kernel
};

template <typename T>
constexpr T constexpr_min(T a, T b) {
  return std::min(a, b);
}

template <int kNThreads_, int kNItems_, int kNRows_, typename input_t_>
struct Traits {
  using input_t = input_t_;
  static constexpr int kNThreads = kNThreads_;
  static constexpr int kNItems = kNItems_;
  static constexpr int kNRows = kNRows_;
  static constexpr int kNBytes = sizeof(input_t);
  static constexpr int kNElts = kNBytes == 4 ? 4 : constexpr_min(8, kNItems);
  static constexpr bool kIsFloat = std::is_same_v<input_t, float>;
  using BlockLoadT = cub::BlockLoad<input_t, kNThreads, kNItems, cub::BLOCK_LOAD_WARP_TRANSPOSE>;
  using BlockStoreT = cub::BlockStore<float, kNThreads, kNItems, cub::BLOCK_STORE_WARP_TRANSPOSE>;
  using BlockScanT = cub::BlockScan<float, kNThreads, cub::BLOCK_SCAN_WARP_SCANS>;
};

template <typename Ktraits>
__device__ void load_input(typename Ktraits::input_t *x, typename Ktraits::input_t (&vals)[Ktraits::kNItems],
                           typename Ktraits::BlockLoadT::TempStorage &smem_load, int valid) {
  typename Ktraits::BlockLoadT(smem_load).Load(x, vals, valid, 0.f);
}

template <typename Ktraits>
__device__ void store_output(float *out, float (&vals)[Ktraits::kNItems],
                             typename Ktraits::BlockStoreT::TempStorage &smem_store, int valid) {
  typename Ktraits::BlockStoreT(smem_store).Store(out, vals, valid);
}

template <typename Ktraits>
__global__ void running_sums(Params params) {
  constexpr int kNItems = Ktraits::kNItems;
  constexpr int kNRows = Ktraits::kNRows;
  constexpr bool kIsFloat = Ktraits::kIsFloat;
  using input_t = typename Ktraits::input_t;
  static_assert(Ktraits::kNElts == 4, "four floats per vector");
  extern __shared__ char smem_[];
  auto &smem_load = reinterpret_cast<typename Ktraits::BlockLoadT::TempStorage &>(smem_);
  auto &smem_store = reinterpret_cast<typename Ktraits::BlockStoreT::TempStorage &>(smem_);
  auto &smem_scan = *reinterpret_cast<typename Ktraits::BlockScanT::TempStorage *>(smem_ + 512);
  float *smem_carry = reinterpret_cast<float *>(smem_ + 1024);
  input_t *x = reinterpret_cast<input_t *>(params.x_ptr) + blockIdx.x * params.row_stride;
  float *out = reinterpret_cast<float *>(params.out_ptr) + blockIdx.x * params.row_stride;
  float bias[kNRows] = {0};
  if (params.bias_ptr != nullptr) {
#pragma unroll
    for (int r = 0; r < kNRows; ++r) {
      bias[r] = reinterpret_cast<float *>(params.bias_ptr)[blockIdx.x * kNRows + r];
    }
  }
  constexpr int kChunk = Ktraits::kNThreads * kNItems;
  const int n_chunks = (params.seqlen + kChunk - 1) / kChunk;
  if (threadIdx.x == 0) {
    smem_carry[0] = 0.f;
  }
  for (int chunk = 0; chunk < n_chunks; ++chunk) {
    input_t vals[kNRows][kNItems];
    float sums[kNRows][kNItems];
#pragma unroll
    for (int r = 0; r < kNRows; ++r) {
      if (r > 0) {
        __syncthreads();
      }
      __syncthreads();
      load_input<Ktraits>(x + chunk * kChunk, vals[r], smem_load, params.seqlen - chunk * kChunk);
#pragma unroll
      for (int i = 0; i < kNItems; ++i) {
        const float v = float(vals[r][i]) + bias[r];
        sums[r][i] = params.softplus && kIsFloat ? log1pf(expf(v)) : v;
      }
      float total;
      typename Ktraits::BlockScanT(smem_scan).InclusiveSum(sums[r], sums[r], total);
      const float carry = smem_carry[0];
#pragma unroll
      for (int i = 0; i < kNItems; ++i) {
        sums[r][i] += carry;
      }
      __syncthreads();
      if (threadIdx.x == 0) {
        smem_carry[0] = carry + total;
      }
      store_output<Ktraits>(out + chunk * kChunk, sums[r], smem_store, params.seqlen - chunk * kChunk);
    }
  }
}

template __global__ void running_sums<Traits<32, 4, 1, float>>(Params);
