// Minimal __half for importing; mirrors the conversions of the real cuda_fp16.h.
#pragma once
#include <cuda_runtime.h>
struct __half {
  unsigned short bits;
  __half() = default;
  __host__ __device__ __half(float);
  __device__ operator float() const;
  __host__ __device__ explicit operator __half() const { return *this; }  // c10::Half's own conversion, called by name
};
__device__ __half __float2half_rn(float);
__device__ __half __float2half(float);
__device__ float __half2float(__half);
typedef __half half;
struct __half2 { __half x, y; };
typedef __half2 half2;
__device__ __half2 make_half2(__half, __half);
__device__ __half2 __halves2half2(__half, __half);
__device__ __half2 __half2half2(__half);
__device__ __half2 __float2half2_rn(float);
__device__ __half2 __floats2half2_rn(float, float);
__device__ float2 __half22float2(__half2);
__device__ float __low2float(__half2);
__device__ float __high2float(__half2);
__device__ __half __low2half(__half2);
__device__ __half __high2half(__half2);
__device__ __half __hadd(__half, __half);
__device__ __half __hmul(__half, __half);
__device__ __half __hmax(__half, __half);
__device__ __half2 __hadd2(__half2, __half2);
__device__ __half2 __hmul2(__half2, __half2);
__device__ __half2 __hmax2(__half2, __half2);
__device__ __half2 __hfma2(__half2, __half2, __half2);
__device__ __half __ushort_as_half(unsigned short);
__device__ unsigned short __half_as_ushort(__half);
__device__ __half2 operator+(__half2, __half2);
__device__ __half2 operator*(__half2, __half2);
__device__ __half2& operator+=(__half2&, __half2);
__device__ __half __float2half_ru(float);
__device__ __half& operator+=(__half&, __half);
__device__ __half operator+(__half, __half);
__device__ __half operator*(__half, __half);
