// Declaration-only stand-in for CUDA's cooperative groups: the block and grid groups and their
// ranks, sizes and barriers. The importer maps these calls onto qpel's group geometry.
#pragma once
#include <cuda_runtime.h>
namespace cooperative_groups {
struct thread_block {
  __device__ unsigned int thread_rank() const;
  __device__ unsigned int size() const;
  __device__ unsigned int num_threads() const;
  __device__ dim3 group_index() const;
  __device__ dim3 thread_index() const;
  __device__ dim3 group_dim() const;
  __device__ dim3 dim_threads() const;
  __device__ void sync() const;
};
struct grid_group {
  __device__ unsigned long long thread_rank() const;
  __device__ unsigned long long size() const;
  __device__ unsigned long long num_threads() const;
  __device__ dim3 group_dim() const;
  __device__ dim3 dim_blocks() const;
  __device__ dim3 block_index() const;
  __device__ void sync() const;
};
__device__ thread_block this_thread_block();
__device__ grid_group this_grid();
__device__ void sync(const thread_block&);
}  // namespace cooperative_groups
