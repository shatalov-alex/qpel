#pragma once
// Stand-in for PyTorch's ATen/cuda/Atomic.cuh (importing only): gpuAtomicAdd is atomicAdd.
#include <torch/extension.h>
template <typename T> __device__ inline T gpuAtomicAdd(T* address, T value) { return atomicAdd(address, value); }
template <typename T> __device__ inline void fastAtomicAdd(T* tensor, size_t index, size_t numel, T value, bool fast) {
  atomicAdd(tensor + index, value);
}
