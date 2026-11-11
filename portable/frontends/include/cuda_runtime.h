// Minimal declarations so clang can parse CUDA kernels without the CUDA SDK.
// Only used for importing (-fsyntax-only); never for code generation.
#pragma once
#define __global__ __attribute__((global))
#define __device__ __attribute__((device))
#define __host__ __attribute__((host))
#define __shared__ __attribute__((shared))
#define __constant__ __attribute__((constant))
#define __forceinline__ __attribute__((always_inline)) inline
struct uint3 { unsigned int x, y, z; };
struct dim3 {
  unsigned int x, y, z;
  __host__ __device__ dim3(unsigned int x = 1, unsigned int y = 1, unsigned int z = 1) : x(x), y(y), z(z) {}
};
extern const __device__ uint3 threadIdx, blockIdx;
extern const __device__ dim3 blockDim, gridDim;
__device__ unsigned int atomicAdd(unsigned int*, unsigned int);
__device__ unsigned int atomicExch(unsigned int*, unsigned int);
__device__ unsigned int atomicMin(unsigned int*, unsigned int);
__device__ int atomicAdd(int*, int);
__device__ int atomicExch(int*, int);
__device__ int atomicMin(int*, int);
__device__ int atomicMax(int*, int);
__device__ unsigned int atomicMax(unsigned int*, unsigned int);
__device__ unsigned int min(unsigned int, unsigned int);
__device__ unsigned int max(unsigned int, unsigned int);
__device__ unsigned int umin(unsigned int, unsigned int);
__device__ unsigned int umax(unsigned int, unsigned int);
__device__ float atomicAdd(float*, float);
__device__ float __fmaf_rn(float, float, float);
__device__ float fmaf(float, float, float);
__device__ float __fadd_rn(float, float);
__device__ void __syncthreads();
typedef struct CUstream_st* cudaStream_t;
typedef int cudaError_t;
// Needed by clang to parse kernel<<<grid, block, shared, stream>>>(...) launches in host code.
extern "C" cudaError_t cudaConfigureCall(dim3, dim3, unsigned long = 0, cudaStream_t = 0);
