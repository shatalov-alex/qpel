// qpel backend: device-scope relaxed unsigned 32-bit atomics.
#include <metal_stdlib>
using namespace metal;
// f32-strict-v1: every float32 operation rounds on its own; only QPEL_FMA_F32 fuses.
#pragma clang fp contract(off)
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
#define QPEL_READ_F32 device const float*
#define QPEL_WRITE_F32 device float*

#define QPEL_FMA_F32(a, b, c) metal::fma((a), (b), (c))
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
