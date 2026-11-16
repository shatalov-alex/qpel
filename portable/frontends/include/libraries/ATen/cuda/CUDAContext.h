#pragma once
#include <torch/extension.h>
namespace at { namespace cuda {
struct CUDAStream { cudaStream_t stream() const; operator cudaStream_t() const; c10::Device device() const; int8_t device_index() const; };
CUDAStream getCurrentCUDAStream(int8_t device = -1);
CUDAStream getStreamFromPool(bool high_priority = false, int8_t device = -1);
cudaDeviceProp* getCurrentDeviceProperties();
} }

