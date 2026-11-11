// CPU simulator: IEEE binary16 through clang's _Float16.
#pragma once
#include <cstring>
typedef _Float16 __half;
typedef __half half;
inline __half __float2half_rn(float f) { return (__half)f; }
inline __half __float2half(float f) { return (__half)f; }
inline float __half2float(__half h) { return (float)h; }
struct __half2 { __half x, y; };
typedef __half2 half2;
inline __half2 make_half2(__half x, __half y) { return {x, y}; }
inline __half2 __halves2half2(__half x, __half y) { return {x, y}; }
inline __half2 __half2half2(__half x) { return {x, x}; }
inline __half2 __float2half2_rn(float f) { return {(__half)f, (__half)f}; }
inline __half2 __floats2half2_rn(float a, float b) { return {(__half)a, (__half)b}; }
struct float2;
inline float __low2float(__half2 h) { return (float)h.x; }
inline float __high2float(__half2 h) { return (float)h.y; }
inline __half __low2half(__half2 h) { return h.x; }
inline __half __high2half(__half2 h) { return h.y; }
inline __half __hadd(__half a, __half b) { return a + b; }
inline __half __hmul(__half a, __half b) { return a * b; }
inline __half __hmax(__half a, __half b) { return a > b ? a : b; }
inline __half2 __hadd2(__half2 a, __half2 b) { return {(__half)(a.x + b.x), (__half)(a.y + b.y)}; }
inline __half2 __hmul2(__half2 a, __half2 b) { return {(__half)(a.x * b.x), (__half)(a.y * b.y)}; }
inline __half2 __hmax2(__half2 a, __half2 b) { return {a.x > b.x ? a.x : b.x, a.y > b.y ? a.y : b.y}; }
inline __half __ushort_as_half(unsigned short b) { __half h; std::memcpy(&h, &b, 2); return h; }
inline unsigned short __half_as_ushort(__half h) { unsigned short b; std::memcpy(&b, &h, 2); return b; }
inline __half2 operator+(__half2 a, __half2 b) { return __hadd2(a, b); }
inline __half2 operator*(__half2 a, __half2 b) { return __hmul2(a, b); }
inline __half2& operator+=(__half2& a, __half2 b) { a = a + b; return a; }
#include "cuda_runtime.h"
inline float2 __half22float2(__half2 h) { return {(float)h.x, (float)h.y}; }
