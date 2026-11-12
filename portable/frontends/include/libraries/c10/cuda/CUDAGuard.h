#pragma once
#include <torch/extension.h>
namespace c10 { namespace cuda {
struct CUDAGuard { explicit CUDAGuard(int8_t); explicit CUDAGuard(c10::Device); };
struct OptionalCUDAGuard { OptionalCUDAGuard(); explicit OptionalCUDAGuard(c10::Device); };
} }
namespace at { namespace cuda { using c10::cuda::CUDAGuard; using c10::cuda::OptionalCUDAGuard; } }
