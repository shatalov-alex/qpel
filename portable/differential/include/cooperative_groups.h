// Cooperative groups for the CPU simulator: thread ranks and sizes as CUDA defines them,
// from the simulator's builtins. Threads run one at a time here, so group barriers are
// refused like __syncthreads.
#pragma once
#include <cuda_runtime.h>
namespace cooperative_groups {
struct thread_block {
  unsigned int thread_rank() const { return threadIdx.x + blockDim.x * (threadIdx.y + blockDim.y * threadIdx.z); }
  unsigned int size() const { return blockDim.x * blockDim.y * blockDim.z; }
  unsigned int num_threads() const { return size(); }
  dim3 group_index() const { return dim3(blockIdx.x, blockIdx.y, blockIdx.z); }
  dim3 thread_index() const { return dim3(threadIdx.x, threadIdx.y, threadIdx.z); }
  dim3 group_dim() const { return blockDim; }
  dim3 dim_threads() const { return blockDim; }
#ifdef QPEL_SIM_THREADED
  void sync() const { __syncthreads(); }
#else
  void sync() const { qpel_simulator_needs_threads("thread_block::sync"); }
#endif
};
struct grid_group {
  unsigned long long thread_rank() const {
    unsigned long long block = blockIdx.x + (unsigned long long)gridDim.x * (blockIdx.y + (unsigned long long)gridDim.y * blockIdx.z);
    return block * this_block_size() + (threadIdx.x + blockDim.x * (threadIdx.y + blockDim.y * threadIdx.z));
  }
  unsigned long long size() const {
    return (unsigned long long)gridDim.x * gridDim.y * gridDim.z * this_block_size();
  }
  unsigned long long num_threads() const { return size(); }
  dim3 group_dim() const { return gridDim; }
  dim3 dim_blocks() const { return gridDim; }
  dim3 block_index() const { return dim3(blockIdx.x, blockIdx.y, blockIdx.z); }
  void sync() const { qpel_simulator_needs_threads("grid_group::sync"); }
 private:
  static unsigned long long this_block_size() { return (unsigned long long)blockDim.x * blockDim.y * blockDim.z; }
};
inline thread_block this_thread_block() { return thread_block(); }
inline grid_group this_grid() { return grid_group(); }
inline void sync(const thread_block& block) { block.sync(); }
// A tile of N threads: consecutive linear thread indices, as on NVIDIA. Its exchanges need the
// threads to run together (QPEL_SIM_THREADED); outside that they are refused.
template <unsigned int N, class Parent = void> struct thread_block_tile {
  thread_block_tile() {}
  template <class P> thread_block_tile(const thread_block_tile<N, P>&) {}
  unsigned int thread_rank() const { return qpel_rank() % N; }
  unsigned int size() const { return N; }
  unsigned int num_threads() const { return N; }
  unsigned int meta_group_rank() const { return qpel_rank() / N; }
  void sync() const { ::__syncwarp(); }
  template <class T> T shfl(T v, int lane) const { return ::__shfl_sync(0xffffffffu, v, lane, (int)N); }
  template <class T> T shfl_down(T v, unsigned int d) const { return ::__shfl_down_sync(0xffffffffu, v, d, (int)N); }
  template <class T> T shfl_up(T v, unsigned int d) const { return ::__shfl_up_sync(0xffffffffu, v, d, (int)N); }
  template <class T> T shfl_xor(T v, unsigned int m) const { return ::__shfl_xor_sync(0xffffffffu, v, (int)m, (int)N); }
  // votes over the tile's lanes: the warp's ballot, of this tile's N lanes
  unsigned int ballot(int p) const { return (::__ballot_sync(0xffffffffu, p) & qpel_lanes()) >> qpel_first(); }
  bool any(int p) const { return (::__ballot_sync(0xffffffffu, p) & qpel_lanes()) != 0u; }
  bool all(int p) const { return (::__ballot_sync(0xffffffffu, p) & qpel_lanes()) == qpel_lanes(); }
 private:
  static unsigned int qpel_first() { return (qpel_rank() % 32u) / N * N; }
  static unsigned int qpel_lanes() { return N >= 32 ? 0xffffffffu : ((1u << N) - 1u) << qpel_first(); }
  static unsigned int qpel_rank() { return threadIdx.x + blockDim.x * (threadIdx.y + blockDim.y * threadIdx.z); }
};
template <unsigned int N, class Parent> thread_block_tile<N, Parent> tiled_partition(const Parent&) { return thread_block_tile<N, Parent>(); }
// A coalesced group: the lanes of one warp in `mask`. One that coalesced_threads() makes is the
// threads that happen to run together; CUDA promises none but the caller, and here, as in qpel's
// reading of them (frontends/coalesced.py), it is the calling thread alone. One that
// labeled_partition makes of a 32-thread tile is every present lane of the warp with the caller's
// label, as CUDA defines it, and its reductions exchange values across the warp (threads only).
struct coalesced_group {
  unsigned int mask = 0;      // the group's lanes; 0 for the calling thread alone
  bool collective = false;    // made of a tile: every lane of the warp takes part in its operations
  unsigned int thread_rank() const { return mask ? (unsigned int)__builtin_popcount(mask & ((1u << qpel_lane()) - 1u)) : 0u; }
  unsigned int size() const { return mask ? (unsigned int)__builtin_popcount(mask) : 1u; }
  unsigned int num_threads() const { return size(); }
  unsigned int meta_group_rank() const { return 0; }
  void sync() const { if (collective) ::__syncwarp(); }
  template <class T> T shfl(T v, int) const { if (collective) qpel_simulator_needs_threads("coalesced_group::shfl of a labeled partition"); return v; }
  template <class T> T shfl_down(T v, unsigned int) const { if (collective) qpel_simulator_needs_threads("coalesced_group::shfl_down of a labeled partition"); return v; }
  static unsigned int qpel_lane() { return (threadIdx.x + blockDim.x * (threadIdx.y + blockDim.y * threadIdx.z)) & 31u; }
};
inline coalesced_group coalesced_threads() { return coalesced_group(); }
template <class Label> coalesced_group labeled_partition(const coalesced_group& g, Label) { return g.collective ? (qpel_simulator_needs_threads("labeled_partition of a labeled partition"), g) : coalesced_group(); }
inline coalesced_group binary_partition(const coalesced_group& g, bool) { return g.collective ? (qpel_simulator_needs_threads("binary_partition of a labeled partition"), g) : coalesced_group(); }
#ifdef QPEL_SIM_THREADED
template <class Parent, class Label> coalesced_group labeled_partition(const thread_block_tile<32, Parent>&, Label label) {
  Label labels[32];
  unsigned int present = qpel_warp_gather(label, labels), mask = 0;
  for (int k = 0; k < 32; ++k) if ((present >> k & 1u) && labels[k] == label) mask |= 1u << k;
  coalesced_group g; g.mask = mask; g.collective = true; return g;
}
// The group's values combined in lane order (CUDA leaves the order open).
template <class T, class Op> T reduce(const coalesced_group& g, T v, Op op) {
  if (!g.collective) return v;
  T values[32];
  qpel_warp_gather(v, values);
  bool first = true; T r = v;
  for (int k = 0; k < 32; ++k) if (g.mask >> k & 1u) { r = first ? values[k] : op(r, values[k]); first = false; }
  return r;
}
#else
template <class Parent, class Label> coalesced_group labeled_partition(const thread_block_tile<32, Parent>&, Label) {
  qpel_simulator_needs_threads("labeled_partition of a tile"); return coalesced_group();
}
template <class T, class Op> T reduce(const coalesced_group& g, T v, Op) {
  if (g.collective) qpel_simulator_needs_threads("reduce over a labeled partition");
  return v;
}
#endif
template <class T, class Op> T inclusive_scan(const coalesced_group& g, T v, Op) {
  if (g.collective) qpel_simulator_needs_threads("inclusive_scan over a labeled partition (not in the simulator)");
  return v;
}
template <class T = void> struct plus { T operator()(T a, T b) const { return a + b; } };
template <class T = void> struct less { T operator()(T a, T b) const { return a < b ? a : b; } };
template <class T = void> struct greater { T operator()(T a, T b) const { return a > b ? a : b; } };
// A tile's reduction: an xor butterfly over its lanes (16, 8, 4, 2, 1 for a warp), as CUDA's tiles
// reduce, every lane getting the result
template <unsigned int N, class Parent, class T, class Op> T reduce(const thread_block_tile<N, Parent>& g, T v, Op op) {
  for (unsigned int m = N / 2; m > 0; m /= 2) v = op(v, g.shfl_xor(v, m));
  return v;
}
template <class Group, class T, class Op> T reduce(const Group&, T, Op);  // (other groups: declared only)
template <class Group, class T, class Op> T inclusive_scan(const Group&, T, Op);
template <class Group, class T, class Op> T exclusive_scan(const Group&, T, Op);
}  // namespace cooperative_groups
