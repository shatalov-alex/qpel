// Minimal __half for importing; mirrors the conversions of the real cuda_fp16.h.
#pragma once
#include <cuda_runtime.h>
struct __half { unsigned short bits; __device__ operator float() const; };
__device__ __half __float2half_rn(float);
__device__ __half __float2half(float);
__device__ float __half2float(__half);
