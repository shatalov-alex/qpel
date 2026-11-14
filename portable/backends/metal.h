// qpel backend: device-scope relaxed unsigned 32-bit atomics.
#include <metal_stdlib>
using namespace metal;
// f32-strict-v1: every float32 operation rounds on its own; only QPEL_FMA_F32 fuses.
#pragma clang fp contract(off)
// Generated conditions keep their own parentheses: `if ((a == b))` is intended.
#pragma clang diagnostic ignored "-Wparentheses-equality"
#define QPEL_KERNEL kernel
#define QPEL_DEVICE
#define QPEL_U8 uchar
#define QPEL_U32 uint
#define QPEL_READ_U8 device const uchar*
#define QPEL_ATOMIC_U32 device atomic_uint*
#define QPEL_WRITE_U32 device uint*
#define QPEL_SHAPE constant uint3&
#define QPEL_PARAM(type, name, binding) type name [[buffer(binding)]]
#define QPEL_GRID_PARAM(name) , uint name [[thread_position_in_grid]]
#define QPEL_GRID_LOCAL(name)
#define QPEL_LOAD(address) atomic_load_explicit((address), memory_order_relaxed)
#define QPEL_STORE(address, value) atomic_store_explicit((address), (value), memory_order_relaxed)
#define QPEL_ADD(address, value) atomic_fetch_add_explicit((address), (value), memory_order_relaxed)
#define QPEL_MIN(address, value) atomic_fetch_min_explicit((address), (value), memory_order_relaxed)

#define QPEL_F32 float
// IR version 4 reference parameters: the caller's local, read and written in place.
#define QPEL_REF(T) thread T&
// IR version 4 vectors: vec<T, N> is the platform vector type, built by its constructor.
#define QPEL_VEC2_I32 int2
#define QPEL_MAKE_VEC2_I32 int2
#define QPEL_VEC3_I32 int3
#define QPEL_MAKE_VEC3_I32 int3
#define QPEL_VEC4_I32 int4
#define QPEL_MAKE_VEC4_I32 int4
#define QPEL_VEC2_U32 uint2
#define QPEL_MAKE_VEC2_U32 uint2
#define QPEL_VEC3_U32 uint3
#define QPEL_MAKE_VEC3_U32 uint3
#define QPEL_VEC4_U32 uint4
#define QPEL_MAKE_VEC4_U32 uint4
#define QPEL_VEC2_F32 float2
#define QPEL_MAKE_VEC2_F32 float2
#define QPEL_VEC3_F32 float3
#define QPEL_MAKE_VEC3_F32 float3
#define QPEL_VEC4_F32 float4
#define QPEL_MAKE_VEC4_F32 float4
#define QPEL_READ_F32 device const float*
#define QPEL_WRITE_F32 device float*

// A local the program never reads (both compilers would warn).
#define QPEL_UNUSED __attribute__((unused))
#define QPEL_FMA_F32(a, b, c) metal::fma((a), (b), (c))
// Apple's Metal compiler miscompiles unsigned 32-bit division and remainder when it knows the
// dividend and it lies within 127 of 2^32 (4294967292u / 3 gave 3579139454; found by fuzz/).
// A volatile local hides the dividend from it; divisions by a variable are then exact.
inline uint qpel_u32_div(uint a, uint b) { volatile uint dividend = a; return dividend / b; }
inline uint qpel_u32_rem(uint a, uint b) { volatile uint dividend = a; return dividend % b; }
#define QPEL_U32_DIV(a, b) qpel_u32_div((a), (b))
#define QPEL_U32_REM(a, b) qpel_u32_rem((a), (b))
#define QPEL_FADD(a, b) ((a) + (b))
#define QPEL_FSUB(a, b) ((a) - (b))
#define QPEL_FMUL(a, b) ((a) * (b))
#define QPEL_FDIV(a, b) ((a) / (b))

#define QPEL_F16 half
#define QPEL_READ_F16 device const half*
#define QPEL_WRITE_F16 device half*
#define QPEL_TO_F16(value) half((value))

#ifdef QPEL_SUM_F32_ENABLED
inline float qpel_sum_f32(device const float* values, uint start, uint count) {
    float result = 0.0f;
    for (uint offset = 0; offset < count; ++offset)
        result = metal::fma(1.0f, values[start + offset], result);
    return result;
}
#define QPEL_SUM_F32(values, start, count) qpel_sum_f32((values), (start), (count))
#endif

#ifdef QPEL_GROUP_SUM_F32_ENABLED
#undef QPEL_GRID_PARAM
#undef QPEL_GRID_LOCAL
#define QPEL_GRID_PARAM(name) , uint name [[thread_position_in_grid]], uint qpel_lane [[thread_index_in_threadgroup]], uint qpel_group_size [[threads_per_threadgroup]]
#define QPEL_GRID_LOCAL(name) threadgroup float qpel_group_scratch[256];
inline float qpel_group_sum_f32(float value, threadgroup float* scratch, uint lane, uint width) {
    scratch[lane] = value;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    for (uint stride = 1; stride < width; stride *= 2) {
        if (lane % (stride * 2) == 0 && lane + stride < width)
            scratch[lane] = metal::fma(1.0f, scratch[lane + stride], scratch[lane]);
        threadgroup_barrier(mem_flags::mem_threadgroup);
    }
    float result = scratch[0];
    threadgroup_barrier(mem_flags::mem_threadgroup);
    return result;
}
#define QPEL_GROUP_SUM_F32(value) qpel_group_sum_f32((value), qpel_group_scratch, qpel_lane, qpel_group_size)
#endif

#ifdef QPEL_I32_ENABLED
#define QPEL_I32 int
#define QPEL_READ_I32 device const int*
#define QPEL_WRITE_I32 device int*
// Signed division truncates toward zero. INT_MIN / -1 wraps to INT_MIN and
// INT_MIN % -1 is 0 (C leaves both undefined). Divisors must be nonzero.
inline int qpel_i32_div(int a, int b) { return b == -1 ? (int)(0u - (uint)a) : a / b; }
inline int qpel_i32_rem(int a, int b) { return b == -1 ? 0 : a % b; }
#define QPEL_ATOMIC_I32 device atomic_int*
#define QPEL_I32_DIV(a, b) qpel_i32_div((a), (b))
#define QPEL_I32_REM(a, b) qpel_i32_rem((a), (b))
#endif

#ifdef QPEL_VALUE_ENABLED
// Scalar kernel arguments arrive in small constant buffers.
#define QPEL_VALUE(type, name, binding) constant type& name [[buffer(binding)]]
#endif

#ifdef QPEL_GRID3D_ENABLED
// Grid3D kernels: one thread per shape element in (x, y, z) threadgroups.
#define QPEL_GRID3D_PARAM , uint3 qpel_position [[thread_position_in_grid]]
#define QPEL_THREAD_INDEX(axis) qpel_position.axis
#endif

#ifdef QPEL_INT_WIDTHS_ENABLED
// IR version 3: 8-, 16- and 64-bit integers and buffers of any element type.
#define QPEL_I8 char
#define QPEL_U16 ushort
#define QPEL_I16 short
#define QPEL_U64 ulong
#define QPEL_I64 long
#define QPEL_READ(type) device const type*
#define QPEL_WRITE(type) device type*
// As for i32: LONG_MIN / -1 wraps to LONG_MIN and LONG_MIN % -1 is 0. Divisors must be nonzero.
inline long qpel_i64_div(long a, long b) { return b == -1 ? (long)(0ul - (ulong)a) : a / b; }
inline long qpel_i64_rem(long a, long b) { return b == -1 ? 0 : a % b; }
#define QPEL_I64_DIV(a, b) qpel_i64_div((a), (b))
#define QPEL_I64_REM(a, b) qpel_i64_rem((a), (b))
#endif

#ifdef QPEL_BF16_ENABLED
#define QPEL_BF16 bfloat
#define QPEL_TO_BF16(value) bfloat((value))
#endif

#ifdef QPEL_F32_TO_INT_ENABLED
// float32 -> integer truncates toward zero, saturates out of range and maps NaN to 0.
#define QPEL_F32_TO_INT_FUNCTION(name, T, low, high, lowest, highest) \
    inline T name(float x) { return isnan(x) ? (T)0 : x < (low) ? (T)(lowest) : x >= (high) ? (T)(highest) : (T)x; }
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u8, uchar, 0.0f, 256.0f, 0, 255)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i8, char, -128.0f, 128.0f, -128, 127)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u16, ushort, 0.0f, 65536.0f, 0, 65535)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i16, short, -32768.0f, 32768.0f, -32768, 32767)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u32, uint, 0.0f, 4294967296.0f, 0u, 4294967295u)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i32, int, -2147483648.0f, 2147483648.0f, (-2147483647 - 1), 2147483647)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u64, ulong, 0.0f, 18446744073709551616.0f, 0ul, 18446744073709551615ul)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i64, long, -9223372036854775808.0f, 9223372036854775808.0f, (-9223372036854775807l - 1), 9223372036854775807l)
#define QPEL_F32_TO_U8(x) qpel_f32_to_u8((x))
#define QPEL_F32_TO_I8(x) qpel_f32_to_i8((x))
#define QPEL_F32_TO_U16(x) qpel_f32_to_u16((x))
#define QPEL_F32_TO_I16(x) qpel_f32_to_i16((x))
#define QPEL_F32_TO_U32(x) qpel_f32_to_u32((x))
#define QPEL_F32_TO_I32(x) qpel_f32_to_i32((x))
#define QPEL_F32_TO_U64(x) qpel_f32_to_u64((x))
#define QPEL_F32_TO_I64(x) qpel_f32_to_i64((x))
#endif

#ifdef QPEL_GROUPS_ENABLED
// Groups kernels: explicit geometry. The host dispatches whole threadgroups, so
// threads_per_threadgroup is the same for every group, as blockDim is in CUDA.
#define QPEL_GROUPS_PARAM , uint3 qpel_group [[threadgroup_position_in_grid]], uint3 qpel_local [[thread_position_in_threadgroup]], uint3 qpel_group_size [[threads_per_threadgroup]], uint3 qpel_group_count [[threadgroups_per_grid]]
#define QPEL_GROUP_INDEX(axis) qpel_group.axis
#define QPEL_LOCAL_INDEX(axis) qpel_local.axis
#define QPEL_GROUP_SIZE(axis) qpel_group_size.axis
#define QPEL_GROUP_COUNT(axis) qpel_group_count.axis
#define QPEL_GROUPS_THREAD_INDEX(axis) (qpel_group.axis * qpel_group_size.axis + qpel_local.axis)
// IR version 4 shared memory and barriers (Groups kernels). A barrier orders device and
// threadgroup memory, as __syncthreads does; barrier_count counts with a threadgroup atomic.
#define QPEL_SHARED(T, name, n) threadgroup T name[n]
#define QPEL_BARRIER() threadgroup_barrier(mem_flags::mem_device | mem_flags::mem_threadgroup)
#define QPEL_BARRIER_COUNT_SCRATCH threadgroup atomic_int qpel_barrier_counter;
inline int qpel_barrier_count(bool p, threadgroup atomic_int* counter, uint rank) {
    if (rank == 0) atomic_store_explicit(counter, 0, memory_order_relaxed);
    threadgroup_barrier(mem_flags::mem_device | mem_flags::mem_threadgroup);
    if (p) atomic_fetch_add_explicit(counter, 1, memory_order_relaxed);
    threadgroup_barrier(mem_flags::mem_threadgroup);
    int count = atomic_load_explicit(counter, memory_order_relaxed);
    threadgroup_barrier(mem_flags::mem_threadgroup);
    return count;
}
#define QPEL_BARRIER_COUNT(p) qpel_barrier_count((p) != 0, &qpel_barrier_counter, qpel_local.x + qpel_group_size.x * (qpel_local.y + qpel_group_size.y * qpel_local.z))
#endif

#ifdef QPEL_MATH_ENABLED
// Math functions (NUMERICS.md): the precise versions, never metal::fast.
#define QPEL_MATH_FABS(x) metal::fabs((x))
#define QPEL_MATH_FMIN(a, b) metal::fmin((a), (b))
#define QPEL_MATH_FMAX(a, b) metal::fmax((a), (b))
#define QPEL_MATH_FLOOR(x) metal::floor((x))
#define QPEL_MATH_CEIL(x) metal::ceil((x))
#define QPEL_MATH_TRUNC(x) metal::trunc((x))
#define QPEL_MATH_ROUND(x) metal::copysign(metal::round((x)), (x))  // Metal's round drops the sign of a zero result
#define QPEL_MATH_COPYSIGN(a, b) metal::copysign((a), (b))
#define QPEL_MATH_SQRT(x) metal::precise::sqrt((x))
#define QPEL_MATH_EXP(x) metal::precise::exp((x))
#define QPEL_MATH_EXP2(x) metal::precise::exp2((x))
#define QPEL_MATH_LOG(x) metal::precise::log((x))
#define QPEL_MATH_LOG2(x) metal::precise::log2((x))
#define QPEL_MATH_SIN(x) metal::precise::sin((x))
#define QPEL_MATH_COS(x) metal::precise::cos((x))
#define QPEL_MATH_TANH(x) metal::precise::tanh((x))
#endif

#ifdef QPEL_CAS_ENABLED
// Compare-and-swap (IR version 4) with CUDA's atomicCAS result: the value found. Metal's
// exchange is weak (it may fail although the value found is the expected one), so it is
// retried until it either succeeds or finds another value.
inline uint qpel_atomic_cas(device atomic_uint* address, uint expected, uint value) {
    uint found = expected;
    while (!atomic_compare_exchange_weak_explicit(address, &found, value, memory_order_relaxed, memory_order_relaxed)) {
        if (found != expected) return found;
        found = expected;
    }
    return expected;
}
inline int qpel_atomic_cas(device atomic_int* address, int expected, int value) {
    int found = expected;
    while (!atomic_compare_exchange_weak_explicit(address, &found, value, memory_order_relaxed, memory_order_relaxed)) {
        if (found != expected) return found;
        found = expected;
    }
    return expected;
}
#define QPEL_CAS(address, expected, value) qpel_atomic_cas((address), (expected), (value))
#endif

#ifdef QPEL_SUBGROUPS_ENABLED
// SIMD-group operations, IR version 5 (NUMERICS.md, Subgroups): the same definitions as
// backends/cuda.h. Apple forms SIMD-groups of 32 from consecutive linear thread indices.
#undef QPEL_GROUPS_PARAM
#define QPEL_GROUPS_PARAM , uint3 qpel_group [[threadgroup_position_in_grid]], uint3 qpel_local [[thread_position_in_threadgroup]], uint3 qpel_group_size [[threads_per_threadgroup]], uint3 qpel_group_count [[threadgroups_per_grid]], uint qpel_simd_lane [[thread_index_in_simdgroup]], uint qpel_simd_group [[simdgroup_index_in_threadgroup]]
#define QPEL_SUBGROUP_LANE() qpel_simd_lane
#define QPEL_SUBGROUP_INDEX() qpel_simd_group
inline uint qpel_subgroup_size() { return 32u; }
#define QPEL_SUBGROUP_SIZE() qpel_subgroup_size()
template <class T> inline T qpel_shuffle_index(T v, uint lane) { return simd_shuffle(v, (ushort)(lane & 31u)); }
template <class T> inline T qpel_shuffle_xor(T v, uint m) { T r = simd_shuffle_xor(v, (ushort)(m & 31u)); return m < 32u ? r : v; }
template <class T> inline T qpel_shuffle_down(T v, uint d) { T r = simd_shuffle_down(v, (ushort)(d & 31u)); return d < 32u ? r : v; }
template <class T> inline T qpel_shuffle_up(T v, uint d) { T r = simd_shuffle_up(v, (ushort)(d & 31u)); return d < 32u ? r : v; }
inline uint qpel_combine_add(uint a, uint b) { return a + b; }
inline int qpel_combine_add(int a, int b) { return (int)((uint)a + (uint)b); }
inline float qpel_combine_add(float a, float b) { return a + b; }
inline uint qpel_combine_min(uint a, uint b) { return b < a ? b : a; }
inline int qpel_combine_min(int a, int b) { return b < a ? b : a; }
inline float qpel_combine_min(float a, float b) { return metal::fmin(a, b); }
inline uint qpel_combine_max(uint a, uint b) { return a < b ? b : a; }
inline int qpel_combine_max(int a, int b) { return a < b ? b : a; }
inline float qpel_combine_max(float a, float b) { return metal::fmax(a, b); }
template <class T> inline T qpel_combine_and(T a, T b) { return a & b; }
template <class T> inline T qpel_combine_or(T a, T b) { return a | b; }
template <class T> inline T qpel_combine_xor(T a, T b) { return a ^ b; }
#define QPEL_SUBGROUP_REDUCE(op) \
  template <class T> inline T qpel_subgroup_##op(T v) { \
    for (ushort m = 16; m > 0; m >>= 1) v = qpel_combine_##op(v, simd_shuffle_xor(v, m)); \
    return v; }
QPEL_SUBGROUP_REDUCE(add) QPEL_SUBGROUP_REDUCE(min) QPEL_SUBGROUP_REDUCE(max)
QPEL_SUBGROUP_REDUCE(and) QPEL_SUBGROUP_REDUCE(or) QPEL_SUBGROUP_REDUCE(xor)
#endif
