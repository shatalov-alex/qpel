// Minimal __nv_bfloat16 for importing; mirrors the conversions of the real cuda_bf16.h.
#pragma once
#include <cuda_runtime.h>
struct __nv_bfloat16 { unsigned short bits; __device__ operator float() const; };
struct __nv_bfloat162 { __nv_bfloat16 x, y; };
typedef __nv_bfloat16 nv_bfloat16;
typedef __nv_bfloat162 nv_bfloat162;
__device__ __nv_bfloat16 __float2bfloat16(float);
__device__ __nv_bfloat16 __float2bfloat16_rn(float);
__device__ float __bfloat162float(__nv_bfloat16);
__device__ __nv_bfloat162 __floats2bfloat162_rn(float, float);
__device__ float2 __bfloat1622float2(__nv_bfloat162);
