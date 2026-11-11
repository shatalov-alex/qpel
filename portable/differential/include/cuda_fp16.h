// CPU simulator: IEEE binary16 through clang's _Float16.
#pragma once
typedef _Float16 __half;
inline __half __float2half_rn(float f) { return (__half)f; }
inline __half __float2half(float f) { return (__half)f; }
inline float __half2float(__half h) { return (float)h; }
