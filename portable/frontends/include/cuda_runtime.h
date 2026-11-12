// Minimal declarations so clang can parse CUDA kernels without the CUDA SDK.
// Only used for importing (-fsyntax-only); never for code generation.
#pragma once
#include <stddef.h>
#define __global__ __attribute__((global))
#define __device__ __attribute__((device))
#define __host__ __attribute__((host))
#define __shared__ __attribute__((shared))
#define __constant__ __attribute__((constant))
#define __launch_bounds__(...) __attribute__((launch_bounds(__VA_ARGS__)))
#define __forceinline__ __attribute__((always_inline)) inline
struct uint3 { unsigned int x, y, z; };
// Vector types (vector_types.h in the SDK), as plain aggregates.
struct int2 { int x, y; };
struct int3 { int x, y, z; };
struct int4 { int x, y, z, w; };
struct uint2 { unsigned int x, y; };
struct uint4 { unsigned int x, y, z, w; };
struct float2 { float x, y; };
struct float3 { float x, y, z; };
struct float4 { float x, y, z, w; };
struct char4 { signed char x, y, z, w; };
struct uchar4 { unsigned char x, y, z, w; };
struct short2 { short x, y; };
struct ushort2 { unsigned short x, y; };
struct double2 { double x, y; };
struct longlong2 { long long x, y; };
inline __host__ __device__ int2 make_int2(int x, int y) { return {x, y}; }
inline __host__ __device__ int3 make_int3(int x, int y, int z) { return {x, y, z}; }
inline __host__ __device__ int4 make_int4(int x, int y, int z, int w) { return {x, y, z, w}; }
inline __host__ __device__ uint2 make_uint2(unsigned int x, unsigned int y) { return {x, y}; }
inline __host__ __device__ uint3 make_uint3(unsigned int x, unsigned int y, unsigned int z) { return {x, y, z}; }
inline __host__ __device__ float2 make_float2(float x, float y) { return {x, y}; }
inline __host__ __device__ float3 make_float3(float x, float y, float z) { return {x, y, z}; }
inline __host__ __device__ float4 make_float4(float x, float y, float z, float w) { return {x, y, z, w}; }
inline __host__ __device__ char4 make_char4(signed char x, signed char y, signed char z, signed char w) { return {x, y, z, w}; }
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
__device__ float __fsub_rn(float, float);
__device__ float __fdiv_rn(float, float);
__device__ void __syncthreads();
typedef struct CUstream_st* cudaStream_t;
typedef int cudaError_t;
// Needed by clang to parse kernel<<<grid, block, shared, stream>>>(...) launches in host code.
extern "C" cudaError_t cudaConfigureCall(dim3, dim3, unsigned long = 0, cudaStream_t = 0);
// Warp-level and miscellaneous device intrinsics (declarations only).
template <class T> __device__ T __shfl_sync(unsigned, T, int, int = 32);
template <class T> __device__ T __shfl_xor_sync(unsigned, T, int, int = 32);
template <class T> __device__ T __shfl_up_sync(unsigned, T, unsigned, int = 32);
template <class T> __device__ T __shfl_down_sync(unsigned, T, unsigned, int = 32);
__device__ int __any_sync(unsigned, int);
__device__ int __all_sync(unsigned, int);
__device__ unsigned __ballot_sync(unsigned, int);
__device__ void __syncwarp(unsigned = 0xffffffffu);
__device__ unsigned __umulhi(unsigned, unsigned);
__device__ int __dp4a(int, int, int);
__device__ unsigned __dp4a(unsigned, unsigned, unsigned);
__device__ int __vsubss4(int, int);
__device__ unsigned __vcmpeq4(unsigned, unsigned);
__device__ unsigned __vcmpne4(unsigned, unsigned);
__device__ int __popc(unsigned);
__device__ int __clz(int);
__device__ int __ffs(int);
__device__ unsigned __byte_perm(unsigned, unsigned, unsigned);
__device__ unsigned __activemask();
__device__ void __trap();
__device__ void __threadfence();
__device__ void __threadfence_block();
__device__ int __syncthreads_count(int);
__device__ int __syncthreads_or(int);
__device__ int __syncthreads_and(int);
__device__ float __expf(float);
__device__ float __logf(float);
__device__ float __fdividef(float, float);
__device__ float __saturatef(float);
__device__ float __int_as_float(int);
__device__ int __float_as_int(float);
__device__ unsigned __float_as_uint(float);
__device__ float __uint_as_float(unsigned);
__device__ int __float2int_rn(float);
__device__ int __float2int_rz(float);
__device__ float __fmul_rn(float, float);
__device__ float __frcp_rn(float);
__device__ float __fsqrt_rn(float);
__device__ float rsqrtf(float);
__device__ float erff(float);
__device__ float tanhf(float);
__device__ float expm1f(float);
__device__ float log1pf(float);
__device__ float copysignf(float, float);
__device__ float roundf(float);
__device__ float truncf(float);
__device__ float floorf(float);
__device__ float ceilf(float);
__device__ float sqrtf(float);
__device__ float __fsqrt_rn(float);
__device__ float exp2f(float);
__device__ float log2f(float);
__device__ float truncf(float);
__device__ float expf(float);
__device__ float logf(float);
__device__ float sinf(float);
__device__ float cosf(float);
__device__ float fabsf(float);
__device__ float fminf(float, float);
__device__ float fmaxf(float, float);
__device__ float powf(float, float);
__device__ float ldexpf(float, int);
__device__ float fmodf(float, float);
__device__ int min(int, int);
__device__ int max(int, int);
__device__ float min(float, float);
__device__ float max(float, float);
__device__ long long atomicAdd(long long*, long long);
__device__ unsigned long long atomicAdd(unsigned long long*, unsigned long long);
__device__ int atomicCAS(int*, int, int);
__device__ unsigned atomicCAS(unsigned*, unsigned, unsigned);
__device__ unsigned long long atomicCAS(unsigned long long*, unsigned long long, unsigned long long);
// Runtime API used by host code around kernels (host code is skipped on import).
typedef struct CUevent_st* cudaEvent_t;
typedef struct CUgraph_st* cudaGraph_t;
typedef struct CUgraphExec_st* cudaGraphExec_t;
typedef struct CUgraphNode_st* cudaGraphNode_t;
struct cudaDeviceProp { char name[256]; size_t totalGlobalMem; int major, minor, multiProcessorCount, warpSize; size_t sharedMemPerBlock, sharedMemPerBlockOptin; int integrated; };
enum { cudaSuccess = 0, cudaErrorNotReady = 600, cudaEventDisableTiming = 2, cudaStreamNonBlocking = 1 };
extern "C" {
cudaError_t cudaGetLastError();
cudaError_t cudaGetDevice(int*);
cudaError_t cudaSetDevice(int);
const char* cudaGetErrorString(cudaError_t);
cudaError_t cudaFuncSetAttribute(const void*, int, int);
cudaError_t cudaMemcpyAsync(void*, const void*, size_t, int, cudaStream_t);
cudaError_t cudaMemsetAsync(void*, int, size_t, cudaStream_t);
cudaError_t cudaStreamCreateWithFlags(cudaStream_t*, unsigned);
cudaError_t cudaStreamDestroy(cudaStream_t);
cudaError_t cudaEventCreateWithFlags(cudaEvent_t*, unsigned);
cudaError_t cudaEventDestroy(cudaEvent_t);
cudaError_t cudaEventRecord(cudaEvent_t, cudaStream_t = 0);
cudaError_t cudaStreamWaitEvent(cudaStream_t, cudaEvent_t, unsigned = 0);
}
// Device-side libc (provided by the SDK's device runtime).
extern "C" __device__ int printf(const char*, ...);
extern "C" __device__ void* memcpy(void*, const void*, size_t);
extern "C" __device__ void* memset(void*, int, size_t);
__device__ long long min(long long, long long);
__device__ long long max(long long, long long);
__device__ long min(long, long);
__device__ long max(long, long);
__device__ unsigned long long min(unsigned long long, unsigned long long);
__device__ unsigned long long max(unsigned long long, unsigned long long);
// Mixed signs, as the SDK's math_functions.hpp declares them: the comparison is unsigned.
__device__ unsigned int min(unsigned int, int);
__device__ unsigned int min(int, unsigned int);
__device__ unsigned int max(unsigned int, int);
__device__ unsigned int max(int, unsigned int);
__device__ unsigned long long min(unsigned long long, long long);
__device__ unsigned long long min(long long, unsigned long long);
__device__ unsigned long long max(unsigned long long, long long);
__device__ unsigned long long max(long long, unsigned long long);
__device__ float round(float);
__device__ float trunc(float);
__device__ float floor(float);
__device__ float ceil(float);
__device__ float fabs(float);
__device__ float sqrt(float);
__device__ float exp(float);
__device__ float log(float);
// assert() in device code (macOS spelling; the SDK declares the glibc one).
extern "C" __device__ void __assert_rtn(const char*, const char*, int, const char*);
extern "C" __device__ void __assert_fail(const char*, const char*, unsigned, const char*);
enum cudaMemcpyKind { cudaMemcpyHostToHost = 0, cudaMemcpyHostToDevice = 1, cudaMemcpyDeviceToHost = 2, cudaMemcpyDeviceToDevice = 3, cudaMemcpyDefault = 4 };
enum cudaFuncAttribute { cudaFuncAttributeMaxDynamicSharedMemorySize = 8 };
