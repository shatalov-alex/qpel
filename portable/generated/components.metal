// Generated backend package. Edit the MPS model or backend support.
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
#define QPEL_SHAPE_VOLUME(s, product) (product)  // (Metal's uint3 shape has no volume word)
#define QPEL_PARAM(type, name, binding) type name [[buffer(binding)]]
#define QPEL_READ_PARAM(type, name, binding) type name [[buffer(binding)]]
// An optional buffer (IR version 9): Metal cannot ask whether a buffer is bound, so the host
// passes a presence flag after all of the kernel's parameters (a placeholder buffer when absent).
#define QPEL_OPTIONAL_PARAM(type, name, binding, presence) type name [[buffer(binding)]], constant uint& qpel_present_##name [[buffer(presence)]]
#define QPEL_OPTIONAL_READ_PARAM(type, name, binding, presence) QPEL_OPTIONAL_PARAM(type, name, binding, presence)
#define QPEL_PRESENT(name) (qpel_present_##name != 0u)
// A kernel whose parameters and presence flags would pass Metal's 31 buffers receives the flags packed in one
// buffer after its parameters, each optional parameter reading its own at the kernel's start.
#define QPEL_OPTIONAL_PARAM_PACKED(type, name, binding, slot) type name [[buffer(binding)]]
#define QPEL_OPTIONAL_READ_PARAM_PACKED(type, name, binding, slot) QPEL_OPTIONAL_PARAM_PACKED(type, name, binding, slot)
#define QPEL_PRESENCE_PARAM(binding) , constant uint* qpel_presence [[buffer(binding)]]
#define QPEL_PRESENCE_LOCAL(name, slot) const uint qpel_present_##name = qpel_presence[slot];
// IR version 12: module-level constant tables, and the leading zero bits of an integer.
#define QPEL_CONSTANT_ARRAY(T, name, length) constant T name[length]
#define QPEL_CONSTANT(T, name) constant T name
#define QPEL_CLZ_U32(x) clz((uint)(x))
#define QPEL_CLZ_I32(x) clz((int)(x))
#define QPEL_CLZ_U64(x) clz((ulong)(x))
#define QPEL_CLZ_I64(x) clz((long)(x))
#define QPEL_GRID_PARAM(name) , uint name [[thread_position_in_grid]]
#define QPEL_GRID_LOCAL(name)
#define QPEL_LOAD(address) atomic_load_explicit((address), memory_order_relaxed)
#define QPEL_STORE(address, value) atomic_store_explicit((address), (value), memory_order_relaxed)
#define QPEL_ADD(address, value) atomic_fetch_add_explicit((address), (value), memory_order_relaxed)
#define QPEL_MIN(address, value) atomic_fetch_min_explicit((address), (value), memory_order_relaxed)

#define QPEL_F32 float
// IR version 4 reference parameters: the caller's local, read and written in place.
#define QPEL_REF(T) thread T&
#define QPEL_REF_ARRAY(T, name, length) thread T (&name)[length]  // an array passed by reference (IR version 10)
#define QPEL_SHARED_VIEW(T) threadgroup T*  // a view into shared memory (IR version 5)
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
// IR version 18: an unaligned view's element i, built from its bytes (Metal's loads of a wider type assume
// that type's alignment and would drop the address's low bits).
template <int N> struct qpel_bytes;
template <> struct qpel_bytes<1> {
  template <class T> static T load(const device uchar* b) { return as_type<T>(b[0]); }
  template <class T> static void store(device uchar* b, T v) { b[0] = as_type<uchar>(v); }
};
template <> struct qpel_bytes<2> {
  template <class T> static T load(const device uchar* b) { return as_type<T>(ushort(ushort(b[0]) | ushort(b[1]) << 8)); }
  template <class T> static void store(device uchar* b, T v) { ushort u = as_type<ushort>(v); b[0] = uchar(u); b[1] = uchar(u >> 8); }
};
template <> struct qpel_bytes<4> {
  template <class T> static T load(const device uchar* b) {
    return as_type<T>(uint(b[0]) | uint(b[1]) << 8 | uint(b[2]) << 16 | uint(b[3]) << 24);
  }
  template <class T> static void store(device uchar* b, T v) {
    uint u = as_type<uint>(v); b[0] = uchar(u); b[1] = uchar(u >> 8); b[2] = uchar(u >> 16); b[3] = uchar(u >> 24);
  }
};
template <> struct qpel_bytes<8> {
  template <class T> static T load(const device uchar* b) {
    return as_type<T>(ulong(qpel_bytes<4>::load<uint>(b)) | ulong(qpel_bytes<4>::load<uint>(b + 4)) << 32);
  }
  template <class T> static void store(device uchar* b, T v) {
    ulong w = as_type<ulong>(v); qpel_bytes<4>::store<uint>(b, uint(w)); qpel_bytes<4>::store<uint>(b + 4, uint(w >> 32));
  }
};
template <class T, class P> inline T qpel_load_unaligned(const device P* p, ulong i) {
  return qpel_bytes<sizeof(T)>::template load<T>((const device uchar*)p + i * sizeof(T));
}
template <class T, class P> inline void qpel_store_unaligned(device P* p, ulong i, T v) {
  qpel_bytes<sizeof(T)>::template store<T>((device uchar*)p + i * sizeof(T), v);
}
#define QPEL_LOAD_UNALIGNED(T, p, i) qpel_load_unaligned<T>((p), (i))
#define QPEL_STORE_UNALIGNED(T, p, i, v) qpel_store_unaligned<T>((p), (i), (T)(v))
// IR version 18: vectors of 8- and 16-bit integers (values only, never in shared memory)
#define QPEL_VEC2_U8 uchar2
#define QPEL_MAKE_VEC2_U8 uchar2
#define QPEL_VEC3_U8 uchar3
#define QPEL_MAKE_VEC3_U8 uchar3
#define QPEL_VEC4_U8 uchar4
#define QPEL_MAKE_VEC4_U8 uchar4
#define QPEL_VEC2_I8 char2
#define QPEL_MAKE_VEC2_I8 char2
#define QPEL_VEC3_I8 char3
#define QPEL_MAKE_VEC3_I8 char3
#define QPEL_VEC4_I8 char4
#define QPEL_MAKE_VEC4_I8 char4
#define QPEL_VEC2_U16 ushort2
#define QPEL_MAKE_VEC2_U16 ushort2
#define QPEL_VEC3_U16 ushort3
#define QPEL_MAKE_VEC3_U16 ushort3
#define QPEL_VEC4_U16 ushort4
#define QPEL_MAKE_VEC4_U16 ushort4
#define QPEL_VEC2_I16 short2
#define QPEL_MAKE_VEC2_I16 short2
#define QPEL_VEC3_I16 short3
#define QPEL_MAKE_VEC3_I16 short3
#define QPEL_VEC4_I16 short4
#define QPEL_MAKE_VEC4_I16 short4

// Whole-vector loads (qpel_opt.vector_loads): vec<T, N>(b[N i], ..., b[N i + N - 1]) as one N-wide
// load when b is aligned to the vector and N i + N - 1 does not wrap i's type; the element loads
// otherwise. Both read the same elements.
#define QPEL_VECTOR_ALIGNED(p, V) ((reinterpret_cast<ulong>(p) & (sizeof(V) - 1)) == 0)
#define QPEL_VECTOR_AT(V, p, i) (reinterpret_cast<device const V*>(p)[(i)])
// Whole-vector stores (qpel_opt.vector_stores): b[N i + k] = v_k for k < N as one N-wide store, the same condition.
#define QPEL_VECTOR_STORE(V, p, i, v) (reinterpret_cast<device V*>(p)[(i)] = (v))
template <int N> inline bool qpel_vector_fits(uint i) { return i <= (0xffffffffu - (N - 1)) / N; }
template <int N> inline bool qpel_vector_fits(int i) { return i >= 0 && i <= (0x7fffffff - (N - 1)) / N; }
template <int N> inline bool qpel_vector_fits(ulong i) { return i <= (~0ul - (N - 1)) / N; }
template <int N> inline bool qpel_vector_fits(long i) { return i >= 0 && i <= (0x7fffffffffffffffl - (N - 1)) / N; }
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
// One quotient for both, the remainder a - q b (wrapping; 0 for b = -1), so that a / b and a % b of the
// same operands share one division as plain C does (mmcv roi_align on sm_75: 3 divisions, not 6).
inline int qpel_i32_div(int a, int b) { int q = a / (b == -1 ? 1 : b); return b == -1 ? (int)(0u - (uint)a) : q; }
inline int qpel_i32_rem(int a, int b) { return (int)((uint)a - (uint)qpel_i32_div(a, b) * (uint)b); }
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
inline long qpel_i64_div(long a, long b) { long q = a / (b == -1 ? 1 : b); return b == -1 ? (long)(0ul - (ulong)a) : q; }
inline long qpel_i64_rem(long a, long b) { return (long)((ulong)a - (ulong)qpel_i64_div(a, b) * (ulong)b); }  // (as qpel_i32_rem)
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
#define QPEL_SUBGROUP_BARRIER() simdgroup_barrier(mem_flags::mem_device | mem_flags::mem_threadgroup)  // IR version 16
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
// (a function of its own, so that the importer reads qpel's Metal back as fast_exp, not exp)
inline float qpel_fast_exp(float x) { return metal::fast::exp(x); }
#define QPEL_MATH_FAST_EXP(x) qpel_fast_exp((x))  // IR version 20: metal::fast::exp, within 3 + floor(2|x|) ulp (measured)
#define QPEL_MATH_EXP2(x) metal::precise::exp2((x))
#define QPEL_MATH_ATAN2(y, x) metal::precise::atan2((y), (x))  // IR version 22
#define QPEL_MATH_RSQRT(x) metal::precise::rsqrt((x))  // IR version 23
#define QPEL_MATH_LOG(x) metal::precise::log((x))
#define QPEL_MATH_LOG2(x) metal::precise::log2((x))
#define QPEL_MATH_SIN(x) metal::precise::sin((x))
#define QPEL_MATH_COS(x) metal::precise::cos((x))
#define QPEL_MATH_TANH(x) metal::precise::tanh((x))
#define QPEL_MATH_POW(a, b) metal::precise::pow((a), (b))  // IR version 17 (C's special cases, measured)
// erf, expm1 and log1p, which Metal does not have (IR version 8; NUMERICS.md, Math functions):
// polynomials fitted for these ranges (backends/math_fit.py prints this code), evaluated with fma,
// and Metal's precise exp and log. Apple GPU, 2^20 inputs: erf 1, expm1 3, log1p 3 ulp at most.
inline float qpel_erf(float x) {
    float a = metal::fabs(x);
    if (a < 1.0f) {
        float t = a * a;
        float r = x * metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(-1.0521326032630895e-07f, t, 1.5330498399634962e-06f), t, -1.479969887441257e-05f), t, 0.00012046751362504438f), t, -0.0008547963807359338f), t, 0.005223968531936407f), t, -0.026866169646382332f), t, 0.11283791810274124f), t, -0.37612637877464294f), t, -5.8635389876826594e-08f);
        return metal::fma(x, 1.128379225730896f, r);  // 2/sqrt(pi) split off: x * (c0 + R(x^2))
    }
    if (a < 3.92f) {  // erf = 1 - exp(-x^2) * erfcx(x), erfcx fitted on [1, 2] and [2, 3.92]
        float q = a < 2.0f ? metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(2.3998281903914176e-05f, (a - 1.5f), -7.76194574427791e-05f), (a - 1.5f), 0.00022314851230476052f), (a - 1.5f), -0.0006606808747164905f), (a - 1.5f), 0.0018861298449337482f), (a - 1.5f), -0.005146094132214785f), (a - 1.5f), 0.01337734330445528f), (a - 1.5f), -0.032930899411439896f), (a - 1.5f), 0.076151043176651f), (a - 1.5f), -0.1636229157447815f), (a - 1.5f), 0.32158541679382324f)
                           : metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(3.585280126117141e-07f, (a - 2.9600000381469727f), -1.5581474599457579e-06f), (a - 2.9600000381469727f), 5.586210136243608e-06f), (a - 2.9600000381469727f), -2.2518788682646118e-05f), (a - 2.9600000381469727f), 8.96720594028011e-05f), (a - 2.9600000381469727f), -0.00034497989690862596f), (a - 2.9600000381469727f), 0.0012899191351607442f), (a - 2.9600000381469727f), -0.004680399317294359f), (a - 2.9600000381469727f), 0.016433853656053543f), (a - 2.9600000381469727f), -0.05566482990980148f), (a - 2.9600000381469727f), 0.1812017410993576f);
        float r = 1.0f - metal::precise::exp(-(a * a)) * q;
        return metal::copysign(r, x);
    }
    return metal::isnan(x) ? x : metal::copysign(1.0f, x);  // erf rounds to 1 in float32 from 3.92 on
}
inline float qpel_expm1(float x) {
    if (metal::fabs(x) < 1.0f) return x * metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(2.0849264537048384e-09f, x, 2.5532418845841676e-08f), x, 2.756363812750351e-07f), x, 2.755197783699259e-06f), x, 2.4801505787763745e-05f), x, 0.00019841297762468457f), x, 0.0013888889225199819f), x, 0.00833333283662796f), x, 0.0416666679084301f), x, 0.1666666716337204f), x, 0.5f), x, 1.0f);
    return metal::precise::exp(x) - 1.0f;  // (NaN, infinities and |x| >= 1: little cancellation)
}
inline float qpel_log1p(float x) {
    if (metal::fabs(x) < 0.5f) return x * metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(metal::fma(-0.1658092886209488f, x, 0.17766889929771423f), x, -0.045648183673620224f), x, 0.05051194131374359f), x, -0.10686452686786652f), x, 0.11852062493562698f), x, -0.12438149005174637f), x, 0.14218182861804962f), x, -0.16669248044490814f), x, 0.20002880692481995f), x, -0.24999961256980896f), x, 0.3333328664302826f), x, -0.5f), x, 1.0f);
    return metal::precise::log(1.0f + x);
}
#define QPEL_MATH_ERF(x) qpel_erf((x))
#define QPEL_MATH_EXPM1(x) qpel_expm1((x))
#define QPEL_MATH_LOG1P(x) qpel_log1p((x))
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

#ifdef QPEL_ATOMICS_ENABLED
// IR version 6 atomics (NUMERICS.md, Atomics), relaxed at device scope like the others.
#define QPEL_ATOMIC_F32 device atomic_float*
#define QPEL_ATOMIC_U64 device atomic_ulong*
#define QPEL_MAX(address, value) atomic_fetch_max_explicit((address), (value), memory_order_relaxed)
#define QPEL_AND(address, value) atomic_fetch_and_explicit((address), (value), memory_order_relaxed)
#define QPEL_OR(address, value) atomic_fetch_or_explicit((address), (value), memory_order_relaxed)
#define QPEL_XOR(address, value) atomic_fetch_xor_explicit((address), (value), memory_order_relaxed)
#define QPEL_EXCHANGE(address, value) atomic_exchange_explicit((address), (value), memory_order_relaxed)
#define QPEL_LOAD_F32(address) atomic_load_explicit((address), memory_order_relaxed)
// Metal's 64-bit atomics are min and max (Apple GPU family 8 and later) without a result. It has no
// 64-bit add, so the value is added in two 32-bit halves, the low half's carry going to the high
// half. Once every addition is done the sum is exact (modulo 2^64); a kernel that adds to 64-bit
// atomics takes no min or max of them (qpel_types.py), which would see the halves apart.
inline void qpel_add_u64(device atomic_ulong* address, ulong value) {
    device atomic_uint* halves = (device atomic_uint*)address;
    uint low = (uint)value, high = (uint)(value >> 32);
    uint before = atomic_fetch_add_explicit(halves, low, memory_order_relaxed);
    high += (uint)(before + low < before);
    if (high != 0u) atomic_fetch_add_explicit(halves + 1, high, memory_order_relaxed);
}
#define QPEL_ADD_U64(address, value) qpel_add_u64((address), (value))
#define QPEL_MIN_U64(address, value) atomic_min_explicit((address), (value), memory_order_relaxed)
#define QPEL_MAX_U64(address, value) atomic_max_explicit((address), (value), memory_order_relaxed)
#endif

#ifdef QPEL_BITCAST_ENABLED
// IR version 6: a 32-bit value's bits read as another 32-bit type.
#define QPEL_BITCAST_U32(x) as_type<uint>((x))
#define QPEL_BITCAST_I32(x) as_type<int>((x))
#define QPEL_BITCAST_F32(x) as_type<float>((x))
#define QPEL_BITCAST_U16(x) as_type<ushort>((x))  // IR version 19
#define QPEL_BITCAST_F16(x) as_type<half>((x))
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

#ifdef QPEL_MATRIX_ENABLED
// IR version 13: an 8x8 matrix held by a simdgroup (NUMERICS.md, Matrices): Apple's simdgroup_matrix,
// loaded and stored row by row (elements_per_row = the stride), c + a b by simdgroup_multiply_accumulate.
#include <metal_simdgroup_matrix>
#define QPEL_MATRIX_F32 simdgroup_float8x8
#define QPEL_MATRIX_F16 simdgroup_half8x8
#define QPEL_MATRIX_FILL_F32(x) make_filled_simdgroup_matrix<float, 8, 8>((x))
#define QPEL_MATRIX_FILL_F16(x) make_filled_simdgroup_matrix<half, 8, 8>((half)(x))
template <class M, class P> inline M qpel_matrix_load(P base, uint offset, uint stride) {
  M m; simdgroup_load(m, base + offset, (ulong)stride); return m; }
template <class M, class P> inline void qpel_matrix_store(M m, P base, uint offset, uint stride) {
  simdgroup_store(m, base + offset, (ulong)stride); }
#define QPEL_MATRIX_LOAD_F32(base, offset, stride) qpel_matrix_load<simdgroup_float8x8>((base), (offset), (stride))
#define QPEL_MATRIX_LOAD_F16(base, offset, stride) qpel_matrix_load<simdgroup_half8x8>((base), (offset), (stride))
#define QPEL_MATRIX_STORE_F32(m, base, offset, stride) qpel_matrix_store((m), (base), (offset), (stride))
#define QPEL_MATRIX_STORE_F16(m, base, offset, stride) qpel_matrix_store((m), (base), (offset), (stride))
// IR version 15: column-major memory (Metal's transposed load and store)
template <class M, class P> inline M qpel_matrix_load_columns(P base, uint offset, uint stride) {
  M m; simdgroup_load(m, base + offset, (ulong)stride, ulong2(0), true); return m; }
template <class M, class P> inline void qpel_matrix_store_columns(M m, P base, uint offset, uint stride) {
  simdgroup_store(m, base + offset, (ulong)stride, ulong2(0), true); }
#define QPEL_MATRIX_LOAD_COLUMNS_F32(base, offset, stride) qpel_matrix_load_columns<simdgroup_float8x8>((base), (offset), (stride))
#define QPEL_MATRIX_LOAD_COLUMNS_F16(base, offset, stride) qpel_matrix_load_columns<simdgroup_half8x8>((base), (offset), (stride))
#define QPEL_MATRIX_STORE_COLUMNS_F32(m, base, offset, stride) qpel_matrix_store_columns((m), (base), (offset), (stride))
#define QPEL_MATRIX_STORE_COLUMNS_F16(m, base, offset, stride) qpel_matrix_store_columns((m), (base), (offset), (stride))
inline simdgroup_float8x8 qpel_matrix_mma_f32(simdgroup_float8x8 a, simdgroup_float8x8 b, simdgroup_float8x8 c) {
  simdgroup_float8x8 d; simdgroup_multiply_accumulate(d, a, b, c); return d; }
inline simdgroup_float8x8 qpel_matrix_mma_f16(simdgroup_half8x8 a, simdgroup_half8x8 b, simdgroup_float8x8 c) {
  simdgroup_float8x8 d; simdgroup_multiply_accumulate(d, a, b, c); return d; }
#define QPEL_MATRIX_MMA_F32(a, b, c) qpel_matrix_mma_f32((a), (b), (c))
#define QPEL_MATRIX_MMA_F16(a, b, c) qpel_matrix_mma_f16((a), (b), (c))
// two f16 multiplies with the same B (qpel_opt.matrix_pairs; one instruction on CUDA): here each its own
struct qpel_matrix_pair_f32 { simdgroup_float8x8 m[2]; };
#define QPEL_MATRIX_PAIR_F32 qpel_matrix_pair_f32
inline qpel_matrix_pair_f32 qpel_matrix_pair_f32_of(simdgroup_float8x8 c0, simdgroup_float8x8 c1) {
  qpel_matrix_pair_f32 p; p.m[0] = c0; p.m[1] = c1; return p; }
inline qpel_matrix_pair_f32 qpel_matrix_mma2_f16(simdgroup_half8x8 a0, simdgroup_half8x8 a1, simdgroup_half8x8 b, qpel_matrix_pair_f32 c) {
  qpel_matrix_pair_f32 d; simdgroup_multiply_accumulate(d.m[0], a0, b, c.m[0]); simdgroup_multiply_accumulate(d.m[1], a1, b, c.m[1]); return d; }
#define QPEL_MATRIX_PAIR_F32_OF(c0, c1) qpel_matrix_pair_f32_of((c0), (c1))
#define QPEL_MATRIX_MMA2_F16(a0, a1, b, c) qpel_matrix_mma2_f16((a0), (a1), (b), (c))
#endif

// Generated from qpel IR. Do not edit.
// qpel numeric contract: u32-modulo-v1
QPEL_KERNEL void initialize(QPEL_READ_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(QPEL_SHAPE_VOLUME(shape, ((QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))) * (QPEL_U32)(shape.z)))))) {
return ;
 }
QPEL_STORE((parent + (QPEL_U32)(qpel_index)), qpel_index);
QPEL_STORE((counts + (QPEL_U32)(qpel_index)), 0u);
 }
}

QPEL_DEVICE QPEL_U32 root(QPEL_ATOMIC_U32 p, QPEL_U32 i) {
{
QPEL_U32 next = QPEL_LOAD((p + (QPEL_U32)(i)));
while (((QPEL_U32)(next) != (QPEL_U32)(i))) {
i = next;
next = QPEL_LOAD((p + (QPEL_U32)(i)));
 }
return i;
 }
}

QPEL_DEVICE void unite(QPEL_ATOMIC_U32 p, QPEL_U32 a, QPEL_U32 b) {
{
while (true) {
a = root(p, a);
b = root(p, b);
if (((QPEL_U32)(a) == (QPEL_U32)(b))) {
return ;
 }
QPEL_U32 lo = min((QPEL_U32)(a), (QPEL_U32)(b));
QPEL_U32 hi = max((QPEL_U32)(a), (QPEL_U32)(b));
QPEL_U32 old = QPEL_MIN((p + (QPEL_U32)(hi)), lo);
if (((QPEL_U32)(old) == (QPEL_U32)(hi))) {
return ;
 }
a = old;
b = lo;
 }
 }
}

QPEL_KERNEL void merge(QPEL_READ_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
QPEL_U32 w = shape.x;
QPEL_U32 h = shape.y;
if ((((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(w) * (QPEL_U32)(h))) * (QPEL_U32)(shape.z)))) || (!mask[qpel_index]))) {
return ;
 }
QPEL_U32 x = QPEL_U32_REM((QPEL_U32)(qpel_index), (QPEL_U32)(w));
QPEL_U32 y = QPEL_U32_REM((QPEL_U32)(QPEL_U32_DIV((QPEL_U32)(qpel_index), (QPEL_U32)(w))), (QPEL_U32)(h));
if ((((QPEL_U32)(x) > (QPEL_U32)(0u)) && mask[((QPEL_U32)(qpel_index) - (QPEL_U32)(1u))])) {
unite(parent, qpel_index, ((QPEL_U32)(qpel_index) - (QPEL_U32)(1u)));
 }
if (((QPEL_U32)(y) > (QPEL_U32)(0u))) {
if (mask[((QPEL_U32)(qpel_index) - (QPEL_U32)(w))]) {
unite(parent, qpel_index, ((QPEL_U32)(qpel_index) - (QPEL_U32)(w)));
 }
if ((((QPEL_U32)(x) > (QPEL_U32)(0u)) && mask[((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) - (QPEL_U32)(1u))])) {
unite(parent, qpel_index, ((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) - (QPEL_U32)(1u)));
 }
if ((((QPEL_U32)(((QPEL_U32)(x) + (QPEL_U32)(1u))) < (QPEL_U32)(w)) && mask[((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) + (QPEL_U32)(1u))])) {
unite(parent, qpel_index, ((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) + (QPEL_U32)(1u)));
 }
 }
 }
}

QPEL_KERNEL void label_count(QPEL_READ_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3), QPEL_PARAM(QPEL_WRITE_U32, labels, 4) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(QPEL_SHAPE_VOLUME(shape, ((QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))) * (QPEL_U32)(shape.z)))))) {
return ;
 }
if ((!mask[qpel_index])) {
labels[qpel_index] = 0u;
return ;
 }
QPEL_U32 r = root(parent, qpel_index);
labels[qpel_index] = ((QPEL_U32)(QPEL_U32_REM((QPEL_U32)(r), (QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))))) + (QPEL_U32)(1u));
QPEL_ADD((counts + (QPEL_U32)(r)), 1u);
 }
}

QPEL_KERNEL void areas(QPEL_READ_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3), QPEL_PARAM(QPEL_WRITE_U32, labels, 4), QPEL_PARAM(QPEL_WRITE_U32, output, 5) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
QPEL_U32 plane = ((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y));
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(plane) * (QPEL_U32)(shape.z))))) {
return ;
 }
QPEL_U32 label = labels[qpel_index];
output[qpel_index] = (label ? QPEL_LOAD((((counts + (QPEL_U32)(((QPEL_U32)(QPEL_U32_DIV((QPEL_U32)(qpel_index), (QPEL_U32)(plane))) * (QPEL_U32)(plane)))) + (QPEL_U32)(label)) - (QPEL_U32)(1u))) : 0u);
 }
}
