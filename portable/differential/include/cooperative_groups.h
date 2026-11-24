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
// Declared only, so that library headers which define warp helpers with them compile (gsplat's
// Utils.cuh): a program that runs one fails to link, rather than computing something else.
template <unsigned int N, class Parent = void> struct thread_block_tile {
  unsigned int thread_rank() const; unsigned int size() const; unsigned int meta_group_rank() const;
  void sync() const;
  template <class T> T shfl(T, int) const;
  template <class T> T shfl_down(T, unsigned int) const;
  template <class T> T shfl_up(T, unsigned int) const;
  template <class T> T shfl_xor(T, unsigned int) const;
  template <class P> thread_block_tile(const thread_block_tile<N, P>&);
  thread_block_tile();
};
template <unsigned int N, class Parent> thread_block_tile<N, Parent> tiled_partition(const Parent&);
// A coalesced group is the threads that happen to run together; CUDA promises none but the caller,
// and here, as in qpel's reading of them (frontends/coalesced.py), it is the calling thread alone.
struct coalesced_group {
  unsigned int thread_rank() const { return 0; }
  unsigned int size() const { return 1; }
  unsigned int num_threads() const { return 1; }
  unsigned int meta_group_rank() const { return 0; }
  void sync() const {}
  template <class T> T shfl(T v, int) const { return v; }
  template <class T> T shfl_down(T v, unsigned int) const { return v; }
};
inline coalesced_group coalesced_threads() { return coalesced_group(); }
template <class Label> coalesced_group labeled_partition(const coalesced_group&, Label) { return coalesced_group(); }
inline coalesced_group binary_partition(const coalesced_group&, bool) { return coalesced_group(); }
template <class Group, class Label> coalesced_group labeled_partition(const Group&, Label);  // (other groups: declared only)
template <class T, class Op> T reduce(const coalesced_group&, T v, Op) { return v; }
template <class T, class Op> T inclusive_scan(const coalesced_group&, T v, Op) { return v; }
template <class T = void> struct plus { T operator()(T a, T b) const { return a + b; } };
template <class T = void> struct less { T operator()(T a, T b) const { return a < b ? a : b; } };
template <class T = void> struct greater { T operator()(T a, T b) const { return a > b ? a : b; } };
template <class Group, class T, class Op> T reduce(const Group&, T, Op);
template <class Group, class T, class Op> T inclusive_scan(const Group&, T, Op);
template <class Group, class T, class Op> T exclusive_scan(const Group&, T, Op);
}  // namespace cooperative_groups
