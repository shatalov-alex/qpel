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
#define __align__(n) __attribute__((aligned(n)))
#define __grid_constant__
struct uint3 { unsigned int x, y, z; };
static constexpr int warpSize = 32;  // a constant on every NVIDIA GPU so far
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
struct longlong3 { long long x, y, z; };
struct longlong4 { long long x, y, z, w; };
struct double3 { double x, y, z; };
struct double4 { double x, y, z, w; };
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
__device__ void sincosf(float, float*, float*);
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
__device__ unsigned long long atomicMin(unsigned long long*, unsigned long long);
__device__ unsigned long long atomicMax(unsigned long long*, unsigned long long);
__device__ float atomicExch(float*, float);
__device__ unsigned int atomicSub(unsigned int*, unsigned int);
__device__ int atomicSub(int*, int);
__device__ unsigned int atomicAnd(unsigned int*, unsigned int);
__device__ int atomicAnd(int*, int);
__device__ unsigned long long atomicAnd(unsigned long long*, unsigned long long);
__device__ unsigned int atomicOr(unsigned int*, unsigned int);
__device__ int atomicOr(int*, int);
__device__ unsigned long long atomicOr(unsigned long long*, unsigned long long);
__device__ unsigned int atomicXor(unsigned int*, unsigned int);
__device__ int atomicXor(int*, int);
__device__ unsigned long long atomicXor(unsigned long long*, unsigned long long);
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
cudaError_t cudaEventCreate(cudaEvent_t*);
cudaError_t cudaStreamSynchronize(cudaStream_t);
cudaError_t cudaMemPrefetchAsync(const void*, size_t, int, cudaStream_t = 0);
cudaError_t cudaEventDestroy(cudaEvent_t);
cudaError_t cudaEventRecord(cudaEvent_t, cudaStream_t = 0);
cudaError_t cudaStreamWaitEvent(cudaStream_t, cudaEvent_t, unsigned = 0);
}
// the C++ API's overload (cuda_runtime.h): an event with flags
inline cudaError_t cudaEventCreate(cudaEvent_t* e, unsigned flags) { return cudaEventCreateWithFlags(e, flags); }
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
// The runtime's typed overload (C++ linkage, so outside the C declarations).
template <class F> cudaError_t cudaFuncSetAttribute(F*, int, int);
// Read-only cache loads (sm_35+): the value at p.
template <class T> __device__ T __ldg(const T* p);
// nvcc's cuda_runtime.h brings in these C headers.
#include <float.h>
#include <assert.h>
// Host overloads of the integer min/max the device code has (CUDA's math headers declare both).
__host__ int min(int, int);
__host__ int max(int, int);
__host__ unsigned int min(unsigned int, unsigned int);
__host__ unsigned int max(unsigned int, unsigned int);
__host__ long long min(long long, long long);
__host__ long long max(long long, long long);
// float overloads of the C++ math functions (device code calls abs(x) on a scalar_t).
__device__ float abs(float);
__device__ double __longlong_as_double(long long);
__device__ long long __double_as_longlong(double);
__host__ __device__ double2 make_double2(double, double);
__host__ __device__ double4 make_double4(double, double, double, double);
__host__ __device__ longlong4 make_longlong4(long long, long long, long long, long long);
cudaError_t cudaLaunchKernel(const void*, dim3, dim3, void**, size_t, cudaStream_t);
// Device overloads of the <cmath> float functions (CUDA's math headers have them; -nocudainc drops
// them): declared once, globally, and brought into std as the real headers do.
__device__ float asin(float);
__device__ float acos(float);
__device__ float atan(float);
__device__ float sin(float);
__device__ float cos(float);
__device__ float tan(float);
__device__ float sqrt(float);
__device__ float exp(float);
__device__ float log(float);
__device__ float fabs(float);
__device__ float floor(float);
__device__ float ceil(float);
__device__ float round(float);
__device__ float tanh(float);
__device__ float exp2(float);
__device__ float log2(float);
__device__ float atan2(float, float);
__device__ float pow(float, float);
__device__ float fmin(float, float);
__device__ float fmax(float, float);
__device__ float hypot(float, float);
__device__ bool isnan(float);
__device__ bool isinf(float);
__device__ bool isfinite(float);
namespace std {
using ::asin; using ::acos; using ::atan; using ::sin; using ::cos; using ::tan; using ::sqrt; using ::exp; using ::log; using ::fabs; using ::floor; using ::ceil; using ::round; using ::tanh; using ::exp2; using ::log2; using ::atan2; using ::pow; using ::fmin; using ::fmax; using ::hypot; using ::isnan; using ::isinf; using ::isfinite; using ::abs;
template <class T> __device__ constexpr const T& min(const T&, const T&);
template <class T> __device__ constexpr const T& max(const T&, const T&);
template <class T> __device__ constexpr const T& clamp(const T&, const T&, const T&);
#if __cplusplus < 202002L
template <class T> struct type_identity { using type = T; };  // C++20, which these libraries build with
#endif
}  // namespace std
__host__ __device__ ushort2 make_ushort2(unsigned short, unsigned short);
__device__ float fmod(float, float);
// The float math functions of CUDA's device library, each declared once more (repeats are harmless).
__device__ float sqrtf(float);
__device__ float rsqrtf(float);
__device__ float cbrtf(float);
__device__ float rcbrtf(float);
__device__ float sinf(float);
__device__ float cosf(float);
__device__ float tanf(float);
__device__ float asinf(float);
__device__ float acosf(float);
__device__ float atanf(float);
__device__ float sinhf(float);
__device__ float coshf(float);
__device__ float tanhf(float);
__device__ float asinhf(float);
__device__ float acoshf(float);
__device__ float atanhf(float);
__device__ float expf(float);
__device__ float exp2f(float);
__device__ float exp10f(float);
__device__ float expm1f(float);
__device__ float logf(float);
__device__ float log2f(float);
__device__ float log10f(float);
__device__ float log1pf(float);
__device__ float fabsf(float);
__device__ float floorf(float);
__device__ float ceilf(float);
__device__ float roundf(float);
__device__ float rintf(float);
__device__ float truncf(float);
__device__ float erff(float);
__device__ float erfcf(float);
__device__ float erfinvf(float);
__device__ float lgammaf(float);
__device__ float tgammaf(float);
__device__ float __expf(float);
__device__ float __logf(float);
__device__ float __sinf(float);
__device__ float __cosf(float);
__device__ float __saturatef(float);
__device__ float atan2f(float, float);
__device__ float powf(float, float);
__device__ float fminf(float, float);
__device__ float fmaxf(float, float);
__device__ float fmodf(float, float);
__device__ float hypotf(float, float);
__device__ float copysignf(float, float);
__device__ float remainderf(float, float);
__device__ float fdimf(float, float);
__device__ float __powf(float, float);
__device__ float __fdividef(float, float);
__device__ float fmaf(float, float, float);
__device__ float norm3df(float, float, float);
__device__ float rnorm3df(float, float, float);
__device__ int __float2int_rn(float);
template <class T> __device__ T atomicAdd_system(T*, T);
namespace cuda {  // libcu++ <cuda/cmath>
template <class T, class U> __host__ __device__ constexpr T ceil_div(T a, U b) { return (a + T(b) - 1) / T(b); }
}
#if __cplusplus < 202002L
namespace std { template <class T> constexpr int bit_width(T x) { int n = 0; while (x) { x >>= 1; ++n; } return n; } }  // C++20
#endif
struct ushort4 { unsigned short x, y, z, w; };
cudaError_t cudaMallocAsync(void**, size_t, cudaStream_t);
template <class T> cudaError_t cudaMallocAsync(T** p, size_t n, cudaStream_t s) { return cudaMallocAsync((void**)p, n, s); }
cudaError_t cudaFreeAsync(void*, cudaStream_t);
__device__ int __popcll(unsigned long long);
__device__ int __clzll(long long);
__device__ int __ffsll(long long);
cudaError_t cudaMallocHost(void**, size_t);
template <class T> cudaError_t cudaMallocHost(T** p, size_t n) { return cudaMallocHost((void**)p, n); }
cudaError_t cudaFreeHost(void*);
