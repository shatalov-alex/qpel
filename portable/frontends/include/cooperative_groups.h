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
// Tiles and partitions (warp-level groups), declared so that sources using them parse; the
// importer reports their calls.
template <unsigned int N, class Parent = void> struct thread_block_tile {
  thread_block_tile() = default;
  template <class P> __device__ thread_block_tile(const thread_block_tile<N, P>&);  // a tile of any parent
  __device__ unsigned int thread_rank() const;
  __device__ unsigned int size() const;
  __device__ unsigned int meta_group_rank() const;
  __device__ unsigned int meta_group_size() const;
  __device__ void sync() const;
  template <class T> __device__ T shfl(T, int) const;
  template <class T> __device__ T shfl_down(T, unsigned int) const;
  template <class T> __device__ T shfl_up(T, unsigned int) const;
  template <class T> __device__ T shfl_xor(T, unsigned int) const;
  __device__ unsigned int ballot(int) const;
  __device__ int any(int) const;
  __device__ int all(int) const;
};
struct coalesced_group {
  __device__ unsigned int thread_rank() const;
  __device__ unsigned int size() const;
  __device__ unsigned int num_threads() const;
  __device__ void sync() const;
  template <class T> __device__ T shfl(T, int) const;
};
template <unsigned int N, class Parent> __device__ thread_block_tile<N, Parent> tiled_partition(const Parent&);
__device__ coalesced_group coalesced_threads();
template <class Group> __device__ coalesced_group labeled_partition(const Group&, int);
template <class T = void> struct plus { __device__ T operator()(T a, T b) const; };
template <class T = void> struct less { __device__ T operator()(T a, T b) const; };
template <class T = void> struct greater { __device__ T operator()(T a, T b) const; };
template <class Group, class T, class Op> __device__ T reduce(const Group&, T, Op);
template <class Group, class T, class Op> __device__ T inclusive_scan(const Group&, T, Op);
template <class Group, class T, class Op> __device__ T exclusive_scan(const Group&, T, Op);
__device__ thread_block this_thread_block();
__device__ grid_group this_grid();
__device__ void sync(const thread_block&);
}  // namespace cooperative_groups
