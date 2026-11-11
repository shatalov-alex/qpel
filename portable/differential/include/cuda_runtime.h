// CPU simulator for CUDA kernels, used to run original and qpel-generated CUDA
// sources on the host and compare their outputs (differential/README.md).
//
// A launch runs every block and every thread one after another, which is one
// valid CUDA schedule. That is exact for kernels whose result does not depend
// on the schedule, such as all race-free kernels and atomics whose combined
// effect is order-independent (atomicAdd, atomicMin on integers). Barriers and
// shared memory need threads running together and are rejected at run time.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#define __global__
#define __device__
#define __host__
#define __forceinline__ inline
#define __restrict__
#define __constant__ static

struct uint3 { unsigned int x, y, z; };
struct dim3 {
  unsigned int x, y, z;
  dim3(unsigned int x = 1, unsigned int y = 1, unsigned int z = 1) : x(x), y(y), z(z) {}
};
inline uint3 threadIdx, blockIdx;
inline dim3 blockDim, gridDim;
typedef struct CUstream_st* cudaStream_t;
typedef int cudaError_t;

inline void __syncthreads() {
  std::fprintf(stderr, "qpel simulator: __syncthreads needs threads that run together\n");
  std::abort();
}

// Atomics. Signed arithmetic wraps, as the hardware does.
inline unsigned int atomicAdd(unsigned int* a, unsigned int v) { unsigned int o = *a; *a = o + v; return o; }
inline int atomicAdd(int* a, int v) { int o = *a; *a = (int)((unsigned int)o + (unsigned int)v); return o; }
inline float atomicAdd(float* a, float v) { float o = *a; *a = o + v; return o; }
inline unsigned int atomicExch(unsigned int* a, unsigned int v) { unsigned int o = *a; *a = v; return o; }
inline int atomicExch(int* a, int v) { int o = *a; *a = v; return o; }
inline unsigned int atomicMin(unsigned int* a, unsigned int v) { unsigned int o = *a; if (v < o) *a = v; return o; }
inline int atomicMin(int* a, int v) { int o = *a; if (v < o) *a = v; return o; }
inline unsigned int atomicMax(unsigned int* a, unsigned int v) { unsigned int o = *a; if (v > o) *a = v; return o; }
inline int atomicMax(int* a, int v) { int o = *a; if (v > o) *a = v; return o; }

// CUDA's integer min/max, including the mixed overloads (which compare unsigned).
inline unsigned int min(unsigned int a, unsigned int b) { return a < b ? a : b; }
inline unsigned int max(unsigned int a, unsigned int b) { return a > b ? a : b; }
inline int min(int a, int b) { return a < b ? a : b; }
inline int max(int a, int b) { return a > b ? a : b; }
inline unsigned int min(int a, unsigned int b) { return min((unsigned int)a, b); }
inline unsigned int min(unsigned int a, int b) { return min(a, (unsigned int)b); }
inline unsigned int max(int a, unsigned int b) { return max((unsigned int)a, b); }
inline unsigned int max(unsigned int a, int b) { return max(a, (unsigned int)b); }
inline long long min(long long a, long long b) { return a < b ? a : b; }
inline long long max(long long a, long long b) { return a > b ? a : b; }
inline unsigned long long min(unsigned long long a, unsigned long long b) { return a < b ? a : b; }
inline unsigned long long max(unsigned long long a, unsigned long long b) { return a > b ? a : b; }
inline float min(float a, float b) { return std::fmin(a, b); }
inline float max(float a, float b) { return std::fmax(a, b); }
inline unsigned int umin(unsigned int a, unsigned int b) { return min(a, b); }
inline unsigned int umax(unsigned int a, unsigned int b) { return max(a, b); }

// Correctly rounded float operations; compile with -ffp-contract=off.
inline float __fmaf_rn(float a, float b, float c) { return std::fma(a, b, c); }
inline float fmaf(float a, float b, float c) { return std::fma(a, b, c); }
inline float __fadd_rn(float a, float b) { volatile float r = a + b; return r; }
inline float __fsub_rn(float a, float b) { volatile float r = a - b; return r; }
inline float __fmul_rn(float a, float b) { volatile float r = a * b; return r; }
inline float __fdiv_rn(float a, float b) { volatile float r = a / b; return r; }
