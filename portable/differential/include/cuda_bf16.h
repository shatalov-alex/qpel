// CPU simulator: bfloat16 as its 16 bits, with CUDA's round-to-nearest-even conversion.
#pragma once
#include <cstdint>
#include <cstring>
struct __nv_bfloat16 {
  unsigned short bits;
  operator float() const { unsigned int w = (unsigned int)bits << 16; float f; std::memcpy(&f, &w, 4); return f; }
};
inline __nv_bfloat16 __float2bfloat16_rn(float f) {
  unsigned int w;
  std::memcpy(&w, &f, 4);
  __nv_bfloat16 r;
  if ((w & 0x7fffffffu) > 0x7f800000u) { r.bits = (unsigned short)((w >> 16) | 0x40u); return r; }  // quiet NaN
  w += 0x7fffu + ((w >> 16) & 1u);  // round to nearest, ties to even
  r.bits = (unsigned short)(w >> 16);
  return r;
}
typedef __nv_bfloat16 nv_bfloat16;
struct __nv_bfloat162 { __nv_bfloat16 x, y; };
typedef __nv_bfloat162 nv_bfloat162;
inline __nv_bfloat16 __float2bfloat16(float f) { return __float2bfloat16_rn(f); }
inline float __bfloat162float(__nv_bfloat16 b) { return (float)b; }
inline __nv_bfloat162 __floats2bfloat162_rn(float a, float b) { return {__float2bfloat16_rn(a), __float2bfloat16_rn(b)}; }
#include "cuda_runtime.h"
inline float2 __bfloat1622float2(__nv_bfloat162 b) { return {(float)b.x, (float)b.y}; }
