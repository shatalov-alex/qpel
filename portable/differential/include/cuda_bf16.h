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
