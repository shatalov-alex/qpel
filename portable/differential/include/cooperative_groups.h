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
}  // namespace cooperative_groups
