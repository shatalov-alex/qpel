#pragma once
#include <torch/extension.h>
namespace at { namespace cuda {
struct CUDAStream { cudaStream_t stream() const; operator cudaStream_t() const; };
CUDAStream getCurrentCUDAStream(int8_t device = -1);
cudaDeviceProp* getCurrentDeviceProperties();
} }
