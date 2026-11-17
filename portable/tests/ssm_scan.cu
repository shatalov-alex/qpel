// A miniature of mamba's selective scan (corpus/mamba, selective_scan_common.h): the recurrence
// x -> a x + b as a scan of (a, b) pairs under SSMScanOp, with a block prefix callback that carries
// the running prefix from chunk to chunk, as mamba writes them (frontends/functors.py reads both
// function objects; qpel's CUB subset scans with them in CUB 2.5's order). One block scans the whole
// sequence in chunks of THREADS * ITEMS pairs; out_a and out_b receive every prefix.
#include <cub/cub.cuh>

template <typename scalar_t> struct SSMScanOp;

template <>
struct SSMScanOp<float> {
  __device__ __forceinline__ float2 operator()(const float2 &ab0, const float2 &ab1) const {
    return make_float2(ab1.x * ab0.x, ab1.x * ab0.y + ab1.y);
  }
};

template <typename scalar_t>
struct SSMScanPrefixCallbackOp {
  using scan_t = float2;
  scan_t running_prefix;
  // Constructor
  __device__ SSMScanPrefixCallbackOp(scan_t running_prefix_) : running_prefix(running_prefix_) {}
  // Callback operator to be entered by the first warp of threads in the block.
  // Thread-0 is responsible for returning a value for seeding the block-wide scan.
  __device__ scan_t operator()(scan_t block_aggregate) {
    scan_t old_prefix = running_prefix;
    running_prefix = SSMScanOp<scalar_t>()(running_prefix, block_aggregate);
    return old_prefix;
  }
};

template <int THREADS, int ITEMS>
__global__ void ssm_scan(const float *a, const float *b, float *out_a, float *out_b, int seqlen) {
  typedef cub::BlockScan<float2, THREADS, cub::BLOCK_SCAN_WARP_SCANS> BlockScanT;
  __shared__ typename BlockScanT::TempStorage smem_scan;
  __shared__ float2 smem_running_prefix[1];
  constexpr int kChunkSize = THREADS * ITEMS;
  for (int chunk = 0; chunk * kChunkSize < seqlen; ++chunk) {
    float2 thread_data[ITEMS];
    for (int i = 0; i < ITEMS; ++i) {
      const int idx = chunk * kChunkSize + (int)threadIdx.x * ITEMS + i;
      if (idx < seqlen) {
        const float decay = 1.f / (1.f + a[idx] * a[idx]);  // in (0, 1]: the products stay finite
        thread_data[i] = make_float2(decay, b[idx]);
      } else {
        thread_data[i] = make_float2(1.f, 0.f);  // so that the last state is correct
      }
    }
    // If we use WARP_SCAN then all lane 0 of all warps (not just thread 0) needs to read
    float2 running_prefix =
        chunk > 0 && threadIdx.x % 32 == 0 ? smem_running_prefix[0] : make_float2(1.f, 0.f);
    SSMScanPrefixCallbackOp<float> prefix_op(running_prefix);
    BlockScanT(smem_scan).InclusiveScan(thread_data, thread_data, SSMScanOp<float>(), prefix_op);
    // There's a syncthreads in the scan op, so we don't need to sync here.
    if (threadIdx.x == 0) {
      smem_running_prefix[0] = prefix_op.running_prefix;
    }
    for (int i = 0; i < ITEMS; ++i) {
      const int idx = chunk * kChunkSize + (int)threadIdx.x * ITEMS + i;
      if (idx < seqlen) {
        out_a[idx] = thread_data[i].x;
        out_b[idx] = thread_data[i].y;
      }
    }
    __syncthreads();
  }
}

template __global__ void ssm_scan<32, 4>(const float *, const float *, float *, float *, int);  // mamba's 32 x 4
template __global__ void ssm_scan<64, 4>(const float *, const float *, float *, float *, int);  // two warps
template __global__ void ssm_scan<32, 1>(const float *, const float *, float *, float *, int);  // one item per thread
