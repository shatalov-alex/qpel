// CPU simulator for CUDA kernels, used to run original and qpel-generated CUDA
// sources on the host and compare their outputs (differential/README.md).
//
// A launch runs every block one after another. Without QPEL_SIM_THREADED its threads
// also run one after another, which is one valid CUDA schedule: exact for kernels whose
// result does not depend on the schedule (race-free kernels, atomics whose combined
// effect is order-independent). With QPEL_SIM_THREADED (sources with barriers) the
// threads of a block run together as host threads: __syncthreads and friends wait for
// every thread of the block that has not exited (as on Volta and later), __shared__ is
// one copy for the block (blocks run one at a time), and atomics take a lock.
#pragma once
#include <cfloat>  // FLT_MAX and friends, which CUDA's own headers bring in
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
static constexpr int warpSize = 32;  // a constant on every NVIDIA GPU so far
struct dim3 {
  unsigned int x, y, z;
  dim3(unsigned int x = 1, unsigned int y = 1, unsigned int z = 1) : x(x), y(y), z(z) {}
  dim3(uint3 v) : x(v.x), y(v.y), z(v.z) {}  // as CUDA's dim3
};
#ifdef QPEL_SIM_THREADED
#include <condition_variable>
#include <mutex>
inline thread_local uint3 threadIdx, blockIdx;
#else
inline uint3 threadIdx, blockIdx;
#endif
inline dim3 blockDim, gridDim;
typedef struct CUstream_st* cudaStream_t;
typedef int cudaError_t;

#ifdef QPEL_SIM_THREADED
// One block's barrier. `active` threads have not exited; a barrier opens when all of them wait.
struct QpelBlockBarrier {
  std::mutex m; std::condition_variable cv;
  int active = 0, waiting = 0, generation = 0, count = 0, result = 0;
  void open() { result = count; count = 0; waiting = 0; ++generation; cv.notify_all(); }
};
inline QpelBlockBarrier qpel_barrier;
inline std::mutex qpel_atomic_lock;
// One warp's barrier and exchange slots: shuffles, ballots and __syncwarp wait for the warp's
// threads that have not exited. Warps are 32 consecutive linear thread indices, as on NVIDIA.
struct QpelWarp {
  std::mutex m; std::condition_variable cv;
  int active = 0, waiting = 0, generation = 0;
  unsigned long long slot[32]; unsigned int ballot = 0;
};
inline QpelWarp qpel_warps[32];  // 1024 threads at most
inline unsigned int qpel_linear() { return threadIdx.x + blockDim.x * (threadIdx.y + blockDim.y * threadIdx.z); }
inline void qpel_block_start(int threads) {
  qpel_barrier.active = threads; qpel_barrier.waiting = 0; qpel_barrier.count = 0;
  for (int w = 0; w < 32; ++w) {
    int left = threads - 32 * w;
    qpel_warps[w].active = left < 0 ? 0 : left > 32 ? 32 : left; qpel_warps[w].waiting = 0;
  }
}
template <class F> inline void qpel_warp_sync(F arrive) {
  QpelWarp& w = qpel_warps[qpel_linear() >> 5];
  std::unique_lock<std::mutex> lock(w.m);
  int generation = w.generation;
  arrive(w);
  if (++w.waiting == w.active) { w.waiting = 0; ++w.generation; w.cv.notify_all(); }
  else w.cv.wait(lock, [&] { return w.generation != generation; });
}
inline int qpel_sync(int predicate) {  // the number of waiting threads whose predicate is nonzero
  std::unique_lock<std::mutex> lock(qpel_barrier.m);
  int generation = qpel_barrier.generation;
  qpel_barrier.count += predicate != 0;
  if (++qpel_barrier.waiting == qpel_barrier.active) qpel_barrier.open();
  else qpel_barrier.cv.wait(lock, [&] { return qpel_barrier.generation != generation; });
  return qpel_barrier.result;
}
inline void qpel_thread_exit() {
  {
    QpelWarp& w = qpel_warps[qpel_linear() >> 5];
    std::lock_guard<std::mutex> lock(w.m);
    if (--w.active == w.waiting && w.waiting > 0) { w.waiting = 0; ++w.generation; w.cv.notify_all(); }
  }
  std::lock_guard<std::mutex> lock(qpel_barrier.m);
  if (--qpel_barrier.active == qpel_barrier.waiting && qpel_barrier.waiting > 0) qpel_barrier.open();
}
inline void __syncthreads() { qpel_sync(0); }
#define QPEL_SIM_ATOMIC std::lock_guard<std::mutex> qpel_guard(qpel_atomic_lock);
#else
inline void __syncthreads() {
  std::fprintf(stderr, "qpel simulator: __syncthreads needs threads that run together\n");
  std::abort();
}
#define QPEL_SIM_ATOMIC
#endif

// Atomics. Signed arithmetic wraps, as the hardware does.
inline unsigned int atomicAdd(unsigned int* a, unsigned int v) { QPEL_SIM_ATOMIC unsigned int o = *a; *a = o + v; return o; }
inline int atomicAdd(int* a, int v) { QPEL_SIM_ATOMIC int o = *a; *a = (int)((unsigned int)o + (unsigned int)v); return o; }
inline float atomicAdd(float* a, float v) { QPEL_SIM_ATOMIC float o = *a; *a = o + v; return o; }
inline unsigned int atomicExch(unsigned int* a, unsigned int v) { QPEL_SIM_ATOMIC unsigned int o = *a; *a = v; return o; }
inline int atomicExch(int* a, int v) { QPEL_SIM_ATOMIC int o = *a; *a = v; return o; }
inline unsigned int atomicMin(unsigned int* a, unsigned int v) { QPEL_SIM_ATOMIC unsigned int o = *a; if (v < o) *a = v; return o; }
inline int atomicMin(int* a, int v) { QPEL_SIM_ATOMIC int o = *a; if (v < o) *a = v; return o; }
inline unsigned int atomicMax(unsigned int* a, unsigned int v) { QPEL_SIM_ATOMIC unsigned int o = *a; if (v > o) *a = v; return o; }
inline int atomicMax(int* a, int v) { QPEL_SIM_ATOMIC int o = *a; if (v > o) *a = v; return o; }

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
inline float __fsqrt_rn(float a) { return std::sqrt(a); }
inline float __fsub_rn(float a, float b) { volatile float r = a - b; return r; }
inline float __fmul_rn(float a, float b) { volatile float r = a * b; return r; }
inline float __fdiv_rn(float a, float b) { volatile float r = a / b; return r; }

// --- The rest of the SDK surface library headers use (llama.cpp's common.cuh) ---
// Library headers declare helpers for many kernels; only what a simulated kernel
// actually calls has to work. Anything that needs threads running together aborts.
#include <cstddef>
#include <cstring>
#define __launch_bounds__(...)
#define __shared__ static
#define __align__(n) __attribute__((aligned(n)))  // (CUDA's __align__(16) char smem[])
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
inline int2 make_int2(int x, int y) { return {x, y}; }
inline int3 make_int3(int x, int y, int z) { return {x, y, z}; }
inline int4 make_int4(int x, int y, int z, int w) { return {x, y, z, w}; }
inline uint2 make_uint2(unsigned int x, unsigned int y) { return {x, y}; }
inline uint3 make_uint3(unsigned int x, unsigned int y, unsigned int z) { return {x, y, z}; }
inline float2 make_float2(float x, float y) { return {x, y}; }
inline float3 make_float3(float x, float y, float z) { return {x, y, z}; }
inline float4 make_float4(float x, float y, float z, float w) { return {x, y, z, w}; }
inline char4 make_char4(signed char x, signed char y, signed char z, signed char w) { return {x, y, z, w}; }
// IR version 18: vectors of 8- and 16-bit integers
struct uchar2 { unsigned char x, y; };
inline uchar2 make_uchar2(unsigned char x, unsigned char y) { return {x, y}; }
struct uchar3 { unsigned char x, y, z; };
inline uchar3 make_uchar3(unsigned char x, unsigned char y, unsigned char z) { return {x, y, z}; }
inline uchar4 make_uchar4(unsigned char x, unsigned char y, unsigned char z, unsigned char w) { return {x, y, z, w}; }
struct char2 { signed char x, y; };
inline char2 make_char2(signed char x, signed char y) { return {x, y}; }
struct char3 { signed char x, y, z; };
inline char3 make_char3(signed char x, signed char y, signed char z) { return {x, y, z}; }
inline ushort2 make_ushort2(unsigned short x, unsigned short y) { return {x, y}; }
struct ushort3 { unsigned short x, y, z; };
inline ushort3 make_ushort3(unsigned short x, unsigned short y, unsigned short z) { return {x, y, z}; }
struct ushort4 { unsigned short x, y, z, w; };
inline ushort4 make_ushort4(unsigned short x, unsigned short y, unsigned short z, unsigned short w) { return {x, y, z, w}; }
inline short2 make_short2(short x, short y) { return {x, y}; }
struct short3 { short x, y, z; };
inline short3 make_short3(short x, short y, short z) { return {x, y, z}; }
struct short4 { short x, y, z, w; };
inline short4 make_short4(short x, short y, short z, short w) { return {x, y, z, w}; }

[[noreturn]] inline void qpel_simulator_needs_threads(const char* what) {
  std::fprintf(stderr, "qpel simulator: %s needs threads that run together\n", what);
  std::abort();
}
#ifdef QPEL_SIM_THREADED
// Warp operations: every thread of the warp that has not exited takes part (the full mask).
// A source lane outside the thread's width segment gives the thread its own value, as on NVIDIA.
template <class T> inline T qpel_warp_exchange(T v, unsigned int source) {
  static_assert(sizeof(T) <= 8, "the simulator shuffles values of up to 8 bytes");
  unsigned long long bits = 0; std::memcpy(&bits, &v, sizeof v);
  unsigned int lane = qpel_linear() & 31u;
  unsigned long long found = 0;
  qpel_warp_sync([&](QpelWarp& w) { w.slot[lane] = bits; });
  found = qpel_warps[qpel_linear() >> 5].slot[source & 31u];
  qpel_warp_sync([](QpelWarp&) {});  // nobody overwrites a slot before every thread has read
  T r; std::memcpy(&r, &found, sizeof r); return r;
}
template <class T> T __shfl_sync(unsigned, T v, int src, int width = 32) {
  unsigned int lane = qpel_linear() & 31u, base = lane & ~(unsigned)(width - 1);
  return qpel_warp_exchange(v, base + ((unsigned)src & (unsigned)(width - 1))); }
template <class T> T __shfl_xor_sync(unsigned, T v, int mask, int width = 32) {
  unsigned int lane = qpel_linear() & 31u, j = lane ^ (unsigned)mask;
  return qpel_warp_exchange(v, j / width == lane / width && j < 32u ? j : lane); }
template <class T> T __shfl_up_sync(unsigned, T v, unsigned d, int width = 32) {
  unsigned int lane = qpel_linear() & 31u;
  return qpel_warp_exchange(v, lane % width >= d ? lane - d : lane); }
template <class T> T __shfl_down_sync(unsigned, T v, unsigned d, int width = 32) {
  unsigned int lane = qpel_linear() & 31u;
  return qpel_warp_exchange(v, lane % width + d < (unsigned)width ? lane + d : lane); }
inline unsigned __ballot_sync(unsigned, int p) {
  unsigned int lane = qpel_linear() & 31u, found = 0;
  qpel_warp_sync([&](QpelWarp& w) { if (w.waiting == 0) w.ballot = 0; if (p) w.ballot |= 1u << lane; });
  found = qpel_warps[qpel_linear() >> 5].ballot;
  qpel_warp_sync([](QpelWarp&) {});
  return found;
}
// Every present lane's value (lanes that have exited are absent): the mask of present lanes.
template <class T> inline unsigned int qpel_warp_gather(T v, T out[32]) {
  static_assert(sizeof(T) <= 8, "the simulator gathers values of up to 8 bytes");
  unsigned long long bits = 0; std::memcpy(&bits, &v, sizeof v);
  unsigned int lane = qpel_linear() & 31u, present = 0;
  qpel_warp_sync([&](QpelWarp& w) { if (w.waiting == 0) w.ballot = 0; w.slot[lane] = bits; w.ballot |= 1u << lane; });
  QpelWarp& w = qpel_warps[qpel_linear() >> 5];
  present = w.ballot;
  for (int k = 0; k < 32; ++k) if (present >> k & 1u) std::memcpy(&out[k], &w.slot[k], sizeof(T));
  qpel_warp_sync([](QpelWarp&) {});  // nobody overwrites a slot before every thread has read
  return present;
}
inline int __any_sync(unsigned m, int p) { return __ballot_sync(m, p) != 0; }
inline int __all_sync(unsigned m, int p) { return __ballot_sync(m, !p) == 0; }
inline void __syncwarp(unsigned = 0xffffffffu) { qpel_warp_sync([](QpelWarp&) {}); }
inline unsigned __activemask() { return __ballot_sync(0xffffffffu, 1); }
#else
template <class T> T __shfl_sync(unsigned, T, int, int = 32) { qpel_simulator_needs_threads("__shfl_sync"); }
template <class T> T __shfl_xor_sync(unsigned, T, int, int = 32) { qpel_simulator_needs_threads("__shfl_xor_sync"); }
template <class T> T __shfl_up_sync(unsigned, T, unsigned, int = 32) { qpel_simulator_needs_threads("__shfl_up_sync"); }
template <class T> T __shfl_down_sync(unsigned, T, unsigned, int = 32) { qpel_simulator_needs_threads("__shfl_down_sync"); }
inline int __any_sync(unsigned, int) { qpel_simulator_needs_threads("__any_sync"); }
inline int __all_sync(unsigned, int) { qpel_simulator_needs_threads("__all_sync"); }
inline unsigned __ballot_sync(unsigned, int) { qpel_simulator_needs_threads("__ballot_sync"); }
inline void __syncwarp(unsigned = 0xffffffffu) { qpel_simulator_needs_threads("__syncwarp"); }
inline unsigned __activemask() { qpel_simulator_needs_threads("__activemask"); }
#endif
// CUDA's shuffles before the _sync forms (deprecated, and on HIP the only ones): the whole warp takes part
template <class T> T __shfl(T v, int src, int width = 32) { return __shfl_sync(0xffffffffu, v, src, width); }
template <class T> T __shfl_xor(T v, int mask, int width = 32) { return __shfl_xor_sync(0xffffffffu, v, mask, width); }
template <class T> T __shfl_up(T v, unsigned d, int width = 32) { return __shfl_up_sync(0xffffffffu, v, d, width); }
template <class T> T __shfl_down(T v, unsigned d, int width = 32) { return __shfl_down_sync(0xffffffffu, v, d, width); }
// __ldg: a load through the read-only data cache, the value at p
template <class T> T __ldg(const T* p) { return *p; }
template <class T> T __ldcs(const T* p) { return *p; }  // a streaming load (bitsandbytes)
#ifdef QPEL_SIM_THREADED
inline int __syncthreads_count(int p) { return qpel_sync(p); }
inline int __syncthreads_or(int p) { return qpel_sync(p) != 0; }
inline int __syncthreads_and(int p) { int n = qpel_barrier.active; return qpel_sync(p) == n; }
#else
inline int __syncthreads_count(int) { qpel_simulator_needs_threads("__syncthreads_count"); }
inline int __syncthreads_or(int) { qpel_simulator_needs_threads("__syncthreads_or"); }
inline int __syncthreads_and(int) { qpel_simulator_needs_threads("__syncthreads_and"); }
#endif
inline void __threadfence() {}
inline void __threadfence_block() {}
inline void __trap() { std::abort(); }
inline unsigned __umulhi(unsigned a, unsigned b) { return (unsigned)(((unsigned long long)a * b) >> 32); }
inline int __popc(unsigned x) { return __builtin_popcount(x); }
inline int __clz(int x) { return x ? __builtin_clz((unsigned)x) : 32; }
inline int __clzll(long long x) { return x ? __builtin_clzll((unsigned long long)x) : 64; }
inline int __ffs(int x) { return __builtin_ffs(x); }
inline int __dp4a(int a, int b, int c) {
  for (int k = 0; k < 4; ++k) c += (signed char)(a >> 8 * k) * (signed char)(b >> 8 * k);
  return c;
}
inline unsigned __byte_perm(unsigned x, unsigned y, unsigned s) {
  unsigned long long v = (unsigned long long)y << 32 | x; unsigned r = 0;
  for (int k = 0; k < 4; ++k) r |= (unsigned)((v >> 8 * ((s >> 4 * k) & 7)) & 0xff) << 8 * k;
  return r;
}
inline float __expf(float x) { return std::exp(x); }
inline float __logf(float x) { return std::log(x); }
inline float __powf(float x, float y) { return std::pow(x, y); }
inline float __fdividef(float a, float b) { return a / b; }
inline float __saturatef(float x) { return x < 0 ? 0.0f : x > 1 ? 1.0f : x; }
inline float __int_as_float(int x) { float f; std::memcpy(&f, &x, 4); return f; }
inline int __float_as_int(float f) { int x; std::memcpy(&x, &f, 4); return x; }
inline unsigned __float_as_uint(float f) { unsigned x; std::memcpy(&x, &f, 4); return x; }
inline float __uint_as_float(unsigned x) { float f; std::memcpy(&f, &x, 4); return f; }
inline int __float2int_rn(float x) { return (int)std::nearbyint(x); }
inline int __float2int_rz(float x) { return (int)x; }
inline float __frcp_rn(float x) { volatile float r = 1.0f / x; return r; }
inline float rsqrtf(float x) { return 1.0f / std::sqrt(x); }
// CUDA's float math functions (sinf, powf, ...) by their global C names: libstdc++ 13 has no
// std::sinf, while both C++ libraries declare ::sinf through <math.h>.
#include <math.h>
inline long long atomicAdd(long long* a, long long v) { QPEL_SIM_ATOMIC long long o = *a; *a = (long long)((unsigned long long)o + (unsigned long long)v); return o; }
inline unsigned long long atomicAdd(unsigned long long* a, unsigned long long v) { QPEL_SIM_ATOMIC unsigned long long o = *a; *a = o + v; return o; }
// system and block scope: on one simulated device the same as device scope (gsplat's add_counts_kernel)
template <class T, class V> inline T atomicAdd_system(T* a, V v) { return atomicAdd(a, (T)v); }
template <class T, class V> inline T atomicAdd_block(T* a, V v) { return atomicAdd(a, (T)v); }
inline int atomicCAS(int* a, int c, int v) { QPEL_SIM_ATOMIC int o = *a; if (o == c) *a = v; return o; }
inline unsigned atomicCAS(unsigned* a, unsigned c, unsigned v) { QPEL_SIM_ATOMIC unsigned o = *a; if (o == c) *a = v; return o; }
inline unsigned long long atomicCAS(unsigned long long* a, unsigned long long c, unsigned long long v) { QPEL_SIM_ATOMIC unsigned long long o = *a; if (o == c) *a = v; return o; }
inline unsigned long long atomicMin(unsigned long long* a, unsigned long long v) { QPEL_SIM_ATOMIC unsigned long long o = *a; if (v < o) *a = v; return o; }
inline unsigned long long atomicMax(unsigned long long* a, unsigned long long v) { QPEL_SIM_ATOMIC unsigned long long o = *a; if (v > o) *a = v; return o; }
inline float atomicExch(float* a, float v) { QPEL_SIM_ATOMIC float o = *a; *a = v; return o; }
inline unsigned int atomicSub(unsigned int* a, unsigned int v) { QPEL_SIM_ATOMIC unsigned int o = *a; *a = o - v; return o; }
inline int atomicSub(int* a, int v) { QPEL_SIM_ATOMIC int o = *a; *a = (int)((unsigned int)o - (unsigned int)v); return o; }
#define QPEL_SIM_BITWISE(name, op) \
  inline unsigned int name(unsigned int* a, unsigned int v) { QPEL_SIM_ATOMIC unsigned int o = *a; *a = o op v; return o; } \
  inline int name(int* a, int v) { QPEL_SIM_ATOMIC int o = *a; *a = o op v; return o; } \
  inline unsigned long long name(unsigned long long* a, unsigned long long v) { QPEL_SIM_ATOMIC unsigned long long o = *a; *a = o op v; return o; }
QPEL_SIM_BITWISE(atomicAnd, &)
QPEL_SIM_BITWISE(atomicOr, |)
QPEL_SIM_BITWISE(atomicXor, ^)

// Runtime API: declarations only. Host code in library headers mentions them; the
// simulator never calls them (host functions of the simulated file are blanked).
typedef struct CUevent_st* cudaEvent_t;
typedef struct CUgraph_st* cudaGraph_t;
typedef struct CUgraphExec_st* cudaGraphExec_t;
typedef struct CUgraphNode_st* cudaGraphNode_t;
struct cudaDeviceProp { char name[256]; size_t totalGlobalMem; int major, minor, multiProcessorCount, warpSize; size_t sharedMemPerBlock, sharedMemPerBlockOptin; int integrated; };
enum { cudaSuccess = 0, cudaErrorNotReady = 600, cudaEventDisableTiming = 2, cudaStreamNonBlocking = 1 };
enum cudaMemcpyKind { cudaMemcpyHostToHost = 0, cudaMemcpyHostToDevice = 1, cudaMemcpyDeviceToHost = 2, cudaMemcpyDeviceToDevice = 3, cudaMemcpyDefault = 4 };
enum cudaFuncAttribute { cudaFuncAttributeMaxDynamicSharedMemorySize = 8 };
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
cudaError_t cudaEventRecord(cudaEvent_t, cudaStream_t);
cudaError_t cudaStreamWaitEvent(cudaStream_t, cudaEvent_t, unsigned);
cudaError_t cudaConfigureCall(dim3, dim3, unsigned long, cudaStream_t);
}
// CUDA's overload for a kernel itself (mamba's launcher: cudaFuncSetAttribute(kernel, ..., kSmemSize)).
template <class T>
inline cudaError_t cudaFuncSetAttribute(T* entry, cudaFuncAttribute attribute, int value) {
  return cudaFuncSetAttribute((const void*)entry, (int)attribute, value);
}
#if defined(__APPLE__)
// CUDA's sincosf (glibc has one; macOS only __sincosf), used by mamba's complex exponentials.
#include <cmath>
inline void sincosf(float x, float* s, float* c) { *s = sinf(x); *c = cosf(x); }
#endif
