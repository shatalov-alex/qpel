// Metal Shading Language on the CPU, for running a Metal source in the CUDA simulator
// (cuda_runtime.h) instead of on the Apple GPU: differential/metal_cpu.py pastes the source
// into namespace qpel_metal after this header and calls its kernels from CUDA-style wrappers.
//
// Why: a Metal kernel that never ends (a probe or retry loop given a wrong size) cannot be
// stopped on an Apple GPU, and repeated hangs can take the whole machine down. On the CPU it
// is a process with a timeout. Only what self-contained ports use is here: scalar and vector
// types, the common math, integer and float atomics (relaxed; the simulator runs atomics one at
// a time), threadgroup memory and barriers. SIMD-group functions, textures and
// [[host_name]] template instantiations are not, and do not compile.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <type_traits>

// Address spaces and function qualifiers have no meaning in one address space.
#define kernel
#define device
#define constant
#define thread
// A local `threadgroup T x[n];` is one copy for the block (blocks run one at a time), as the
// simulator does for __shared__; metal_cpu.py turns those declarations into `static` ones.
#define threadgroup
#define METAL_FUNC inline

namespace qpel_metal {
typedef unsigned char uchar;
typedef unsigned short ushort;
typedef unsigned int uint;
typedef unsigned long ulong;
typedef _Float16 half;

#define QPEL_METAL_VECTORS(T)                                                \
  typedef T T##2 __attribute__((ext_vector_type(2)));                         \
  typedef T T##3 __attribute__((ext_vector_type(3)));                         \
  typedef T T##4 __attribute__((ext_vector_type(4)));
QPEL_METAL_VECTORS(char) QPEL_METAL_VECTORS(uchar) QPEL_METAL_VECTORS(short) QPEL_METAL_VECTORS(ushort)
QPEL_METAL_VECTORS(int) QPEL_METAL_VECTORS(uint) QPEL_METAL_VECTORS(long) QPEL_METAL_VECTORS(ulong)
QPEL_METAL_VECTORS(float) QPEL_METAL_VECTORS(half)
#undef QPEL_METAL_VECTORS

namespace metal {
enum memory_order { memory_order_relaxed, memory_order_acquire, memory_order_release, memory_order_acq_rel,
                    memory_order_seq_cst };
enum class mem_flags { mem_none = 0, mem_device = 1, mem_threadgroup = 2, mem_texture = 4 };
inline mem_flags operator|(mem_flags a, mem_flags b) { return mem_flags((int)a | (int)b); }
inline void threadgroup_barrier(mem_flags) { __syncthreads(); }

template <class T> struct _atomic { T value; };
typedef _atomic<int> atomic_int;
typedef _atomic<uint> atomic_uint;
typedef _atomic<float> atomic_float;
typedef _atomic<bool> atomic_bool;
typedef _atomic<ulong> atomic_ulong;

template <class T> T atomic_load_explicit(const _atomic<T>* a, memory_order) { QPEL_SIM_ATOMIC return a->value; }
template <class T> void atomic_store_explicit(_atomic<T>* a, T v, memory_order) { QPEL_SIM_ATOMIC a->value = v; }
template <class T> T atomic_exchange_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; a->value = v; return old; }
// Integer arithmetic wraps, as on the GPU.
template <class T> T _wrap_add(T a, T b) {
  if constexpr (std::is_integral_v<T>) return (T)((std::make_unsigned_t<T>)a + (std::make_unsigned_t<T>)b);
  else return a + b; }
template <class T> T _wrap_sub(T a, T b) {
  if constexpr (std::is_integral_v<T>) return (T)((std::make_unsigned_t<T>)a - (std::make_unsigned_t<T>)b);
  else return a - b; }
template <class T> T atomic_fetch_add_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; a->value = _wrap_add(old, v); return old; }
template <class T> T atomic_fetch_sub_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; a->value = _wrap_sub(old, v); return old; }
template <class T> T atomic_fetch_min_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; if (v < old) a->value = v; return old; }
template <class T> T atomic_fetch_max_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; if (v > old) a->value = v; return old; }
template <class T> T atomic_fetch_and_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; a->value = old & v; return old; }
template <class T> T atomic_fetch_or_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; a->value = old | v; return old; }
template <class T> T atomic_fetch_xor_explicit(_atomic<T>* a, T v, memory_order) {
  QPEL_SIM_ATOMIC T old = a->value; a->value = old ^ v; return old; }
// Metal's 64-bit min and max (atomic_ulong) give no result.
template <class T> void atomic_min_explicit(_atomic<T>* a, T v, memory_order) { QPEL_SIM_ATOMIC if (v < a->value) a->value = v; }
template <class T> void atomic_max_explicit(_atomic<T>* a, T v, memory_order) { QPEL_SIM_ATOMIC if (v > a->value) a->value = v; }
// A weak compare-exchange may fail spuriously on the GPU; here it never does (one valid behaviour).
template <class T> bool atomic_compare_exchange_weak_explicit(_atomic<T>* a, T* expected, T desired, memory_order,
                                                              memory_order) {
  QPEL_SIM_ATOMIC
  if (a->value == *expected) { a->value = desired; return true; }
  *expected = a->value;
  return false;
}
template <class T> bool atomic_compare_exchange_strong_explicit(_atomic<T>* a, T* expected, T desired,
                                                                memory_order s, memory_order f) {
  return atomic_compare_exchange_weak_explicit(a, expected, desired, s, f);
}

#ifdef QPEL_SIM_THREADED
// SIMD-groups are the simulator's warps: 32 consecutive linear thread indices, every lane that
// has not exited taking part. Apple does not document the order of its SIMD-group reductions;
// here they combine in qpel's butterfly order (xor 16, 8, 4, 2, 1), as qpel's own output does.
template <class T> T simd_shuffle(T v, ushort lane) { return ::__shfl_sync(0xffffffffu, v, (int)lane); }
template <class T> T simd_broadcast(T v, ushort lane) { return ::__shfl_sync(0xffffffffu, v, (int)lane); }
template <class T> T simd_shuffle_xor(T v, ushort m) { return ::__shfl_xor_sync(0xffffffffu, v, (int)m); }
template <class T> T simd_shuffle_down(T v, ushort d) { return ::__shfl_down_sync(0xffffffffu, v, (unsigned)d); }
template <class T> T simd_shuffle_up(T v, ushort d) { return ::__shfl_up_sync(0xffffffffu, v, (unsigned)d); }
#define QPEL_METAL_SIMD_REDUCE(name, combine)                                                   \
  template <class T> T name(T v) {                                                              \
    for (ushort m = 16; m > 0; m >>= 1) { T o = simd_shuffle_xor(v, m); v = combine; }          \
    return v; }
QPEL_METAL_SIMD_REDUCE(simd_sum, v + o) QPEL_METAL_SIMD_REDUCE(simd_min, (o < v ? o : v))
QPEL_METAL_SIMD_REDUCE(simd_max, (v < o ? o : v)) QPEL_METAL_SIMD_REDUCE(simd_and, v & o)
QPEL_METAL_SIMD_REDUCE(simd_or, v | o) QPEL_METAL_SIMD_REDUCE(simd_xor, v ^ o)
#undef QPEL_METAL_SIMD_REDUCE
inline bool simd_all(bool p) { return ::__all_sync(0xffffffffu, p); }
inline bool simd_any(bool p) { return ::__any_sync(0xffffffffu, p); }
inline void simdgroup_barrier(mem_flags) { ::__syncwarp(); }
#endif

template <class T, class U> T as_type(U u) {
  static_assert(sizeof(T) == sizeof(U), "as_type needs types of one size");
  T t; std::memcpy(&t, &u, sizeof t); return t; }
template <class T> T min(T a, T b) { return b < a ? b : a; }
template <class T> T max(T a, T b) { return a < b ? b : a; }
inline float fmin(float a, float b) { return std::fmin(a, b); }
inline float fmax(float a, float b) { return std::fmax(a, b); }
template <class T> T clamp(T x, T lo, T hi) { return min(max(x, lo), hi); }
template <class T> T select(T a, T b, bool c) { return c ? b : a; }
template <class T> T abs(T x) { return x < T(0) ? -x : x; }
inline float fabs(float x) { return std::fabs(x); }
inline float fma(float a, float b, float c) { return std::fma(a, b, c); }
inline float sqrt(float x) { return std::sqrt(x); }
inline float rsqrt(float x) { return 1.0f / std::sqrt(x); }
inline float exp(float x) { return std::exp(x); }
inline float exp2(float x) { return std::exp2(x); }
inline float log(float x) { return std::log(x); }
inline float log2(float x) { return std::log2(x); }
inline float sin(float x) { return std::sin(x); }
inline float cos(float x) { return std::cos(x); }
inline float tan(float x) { return std::tan(x); }
inline float tanh(float x) { return std::tanh(x); }
inline float erf(float x) { return std::erf(x); }
inline float pow(float x, float y) { return std::pow(x, y); }
inline float floor(float x) { return std::floor(x); }
inline float ceil(float x) { return std::ceil(x); }
inline float round(float x) { return std::round(x); }
inline float rint(float x) { return std::rint(x); }
inline float trunc(float x) { return std::trunc(x); }
inline float copysign(float x, float y) { return std::copysign(x, y); }
inline bool isnan(float x) { return std::isnan(x); }
inline bool isinf(float x) { return std::isinf(x); }
inline bool isfinite(float x) { return std::isfinite(x); }
inline bool signbit(float x) { return std::signbit(x); }
inline uint popcount(uint x) { return (uint)__builtin_popcount(x); }
inline uint clz(uint x) { return x ? (uint)__builtin_clz(x) : 32u; }
inline int clz(int x) { return (int)clz((uint)x); }
inline ulong clz(ulong x) { return x ? (ulong)__builtin_clzll(x) : 64ul; }
inline long clz(long x) { return (long)clz((ulong)x); }
inline uint ctz(uint x) { return x ? (uint)__builtin_ctz(x) : 32u; }
namespace precise { using metal::sqrt; using metal::exp; using metal::log; using metal::sin; using metal::cos;
                    using metal::tanh; using metal::pow; using metal::rsqrt; }
namespace fast { using metal::sqrt; using metal::exp; using metal::log; using metal::sin; using metal::cos;
                 using metal::tanh; using metal::pow; using metal::rsqrt; }
}  // namespace metal
}  // namespace qpel_metal
