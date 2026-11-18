// qpel backend: device-scope relaxed unsigned 32-bit atomics.
#include <cuda_runtime.h>
// volume: x * y * z, which the host fills (it refuses more than INT32_MAX elements, so the product is exact)
struct alignas(16) QpelShape { unsigned int x, y, z, volume; };
#define QPEL_SHAPE_VOLUME(s, product) ((s).volume)
static_assert(sizeof(unsigned int) == 4, "qpel requires 32-bit unsigned int");
static_assert(sizeof(QpelShape) == 16, "qpel shape ABI must be 16 bytes");
#define QPEL_KERNEL extern "C" __global__
#define QPEL_DEVICE __device__ __forceinline__
#define QPEL_U8 unsigned char
#define QPEL_U32 unsigned int
#define QPEL_READ_U8 const unsigned char*
#define QPEL_ATOMIC_U32 unsigned int*
#define QPEL_WRITE_U32 unsigned int*
#define QPEL_SHAPE QpelShape
#define QPEL_PARAM(type, name, binding) type name
// An optional buffer (IR version 9): the pointer, null when the caller leaves the buffer out.
#define QPEL_OPTIONAL_PARAM(type, name, binding, presence) type name
#define QPEL_PRESENT(name) ((name) != nullptr)
// IR version 12: module-level constant tables, and the leading zero bits of an integer.
#define QPEL_CONSTANT_ARRAY(T, name, length) __device__ const T name[length]
#define QPEL_CONSTANT(T, name) __device__ const T name
#define QPEL_CLZ_U32(x) ((unsigned int)__clz((int)(x)))
#define QPEL_CLZ_I32(x) (__clz((x)))
#define QPEL_CLZ_U64(x) ((unsigned long long)__clzll((long long)(x)))
#define QPEL_CLZ_I64(x) ((long long)__clzll((x)))
#define QPEL_GRID_PARAM(name)
#define QPEL_GRID_LOCAL(name) const unsigned int name = blockIdx.x * blockDim.x + threadIdx.x;
// Legacy CUDA atomics have relaxed ordering at device scope. An atomic load or store is a
// volatile access on the GPU: PTX gives ld/st.volatile the semantics of ld/st.relaxed.sys, the
// relaxed order Metal's atomic_load/store_explicit have, without the read-modify-write of a
// zero-add or an exchange (sam2_cc's union-find reads its labels this way: x1.47 before).
// The CPU simulator (no __CUDA_ARCH__) keeps the atomics.
#if defined(__CUDA_ARCH__)
template <class T> __device__ __forceinline__ T qpel_relaxed_load(T* address) { return *(volatile T*)address; }
template <class T, class U> __device__ __forceinline__ void qpel_relaxed_store(T* address, U value) { *(volatile T*)address = (T)value; }
#define QPEL_LOAD(address) qpel_relaxed_load((address))
#define QPEL_STORE(address, value) qpel_relaxed_store((address), (value))
#else
#define QPEL_LOAD(address) atomicAdd((address), 0u)
#define QPEL_STORE(address, value) atomicExch((address), (value))
#endif
#define QPEL_ADD(address, value) atomicAdd((address), (value))
#define QPEL_MIN(address, value) atomicMin((address), (value))

#define QPEL_F32 float
// IR version 4 reference parameters: the caller's local, read and written in place.
#define QPEL_REF(T) T&
#define QPEL_REF_ARRAY(T, name, length) T (&name)[length]  // an array passed by reference (IR version 10)
// A view into shared memory (IR version 5): T*, under a name that keeps it a view when the
// importer reads qpel's own output back (CUDA pointers carry no address space).
template <class T> using qpel_shared_view = T*;
#define QPEL_SHARED_VIEW(T) qpel_shared_view<T>
// IR version 4 vectors: vec<T, N> is the platform vector type, built by its constructor.
#define QPEL_VEC2_I32 int2
#define QPEL_MAKE_VEC2_I32 make_int2
#define QPEL_VEC3_I32 int3
#define QPEL_MAKE_VEC3_I32 make_int3
#define QPEL_VEC4_I32 int4
#define QPEL_MAKE_VEC4_I32 make_int4
#define QPEL_VEC2_U32 uint2
#define QPEL_MAKE_VEC2_U32 make_uint2
#define QPEL_VEC3_U32 uint3
#define QPEL_MAKE_VEC3_U32 make_uint3
#define QPEL_VEC4_U32 uint4
#define QPEL_MAKE_VEC4_U32 make_uint4
#define QPEL_VEC2_F32 float2
#define QPEL_MAKE_VEC2_F32 make_float2
#define QPEL_VEC3_F32 float3
#define QPEL_MAKE_VEC3_F32 make_float3
#define QPEL_VEC4_F32 float4
#define QPEL_MAKE_VEC4_F32 make_float4

// Whole-vector loads (qpel_opt.vector_loads): vec<T, N>(b[N i], ..., b[N i + N - 1]) as one N-wide
// load when b is aligned to the vector and N i + N - 1 does not wrap i's type; the element loads
// otherwise. Both read the same elements.
#define QPEL_VECTOR_ALIGNED(p, V) ((reinterpret_cast<unsigned long long>(p) & (sizeof(V) - 1)) == 0)
#define QPEL_VECTOR_AT(V, p, i) (reinterpret_cast<const V*>(p)[(i)])
template <int N> __device__ inline bool qpel_vector_fits(unsigned int i) { return i <= (0xffffffffu - (N - 1)) / N; }
template <int N> __device__ inline bool qpel_vector_fits(int i) { return i >= 0 && i <= (0x7fffffff - (N - 1)) / N; }
template <int N> __device__ inline bool qpel_vector_fits(unsigned long long i) { return i <= (~0ull - (N - 1)) / N; }
template <int N> __device__ inline bool qpel_vector_fits(long long i) { return i >= 0 && i <= (0x7fffffffffffffffll - (N - 1)) / N; }
#define QPEL_READ_F32 const float*
#define QPEL_WRITE_F32 float*

// A local the program never reads (both compilers would warn).
#define QPEL_UNUSED __attribute__((unused))
#define QPEL_FMA_F32(a, b, c) __fmaf_rn((a), (b), (c))
#define QPEL_U32_DIV(a, b) ((a) / (b))
#define QPEL_U32_REM(a, b) ((a) % (b))
// f32-strict-v1: every float32 operation rounds on its own. These intrinsics are never
// contracted into an FMA, whatever nvcc's --fmad setting; only QPEL_FMA_F32 fuses.
#define QPEL_FADD(a, b) __fadd_rn((a), (b))
#define QPEL_FSUB(a, b) __fsub_rn((a), (b))
#define QPEL_FMUL(a, b) __fmul_rn((a), (b))
// Division never contracts, so it is written `/`: correctly rounded under nvcc's default
// -prec-div=true (qpel's CUDA is not for -use_fast_math or -prec-div=false; its bounded math
// functions assume that too). __fdiv_rn gives the same bits, but nvcc does not treat it as a
// division: `1.0f / x` gets no reciprocal and is not hoisted out of loops (TRELLIS.2's render: x1.97).
#define QPEL_FDIV(a, b) ((a) / (b))

#ifdef QPEL_F16_ENABLED
#include <cuda_fp16.h>
#endif
#define QPEL_F16 __half
#define QPEL_READ_F16 const __half*
#define QPEL_WRITE_F16 __half*
#define QPEL_TO_F16(value) __float2half_rn((value))

#ifdef QPEL_SUM_F32_ENABLED
__device__ __forceinline__ float qpel_sum_f32(const float* values, unsigned int start, unsigned int count) {
    float result = 0.0f;
    for (unsigned int offset = 0; offset < count; ++offset)
        result = __fadd_rn(result, values[start + offset]);
    return result;
}
#define QPEL_SUM_F32(values, start, count) qpel_sum_f32((values), (start), (count))
#endif

#ifdef QPEL_GROUP_SUM_F32_ENABLED
#undef QPEL_GRID_LOCAL
#define QPEL_GRID_LOCAL(name) const unsigned int name = blockIdx.x * blockDim.x + threadIdx.x; __shared__ float qpel_group_scratch[256];
__device__ __forceinline__ float qpel_group_sum_f32(float value, float* scratch, unsigned int lane, unsigned int width) {
    scratch[lane] = value;
    __syncthreads();
    for (unsigned int stride = 1; stride < width; stride *= 2) {
        if (lane % (stride * 2) == 0 && lane + stride < width)
            scratch[lane] = __fadd_rn(scratch[lane], scratch[lane + stride]);
        __syncthreads();
    }
    float result = scratch[0];
    __syncthreads(); // All readers finish before a later collective reuses scratch.
    return result;
}
#define QPEL_GROUP_SUM_F32(value) qpel_group_sum_f32((value), qpel_group_scratch, threadIdx.x, blockDim.x)
#endif

#ifdef QPEL_I32_ENABLED
#define QPEL_I32 int
#define QPEL_READ_I32 const int*
#define QPEL_WRITE_I32 int*
// Signed division truncates toward zero. INT_MIN / -1 wraps to INT_MIN and
// INT_MIN % -1 is 0 (C leaves both undefined). Divisors must be nonzero.
__device__ __forceinline__ int qpel_i32_div(int a, int b) { return b == -1 ? (int)(0u - (unsigned int)a) : a / b; }
__device__ __forceinline__ int qpel_i32_rem(int a, int b) { return b == -1 ? 0 : a % b; }
#define QPEL_ATOMIC_I32 int*
#define QPEL_I32_DIV(a, b) qpel_i32_div((a), (b))
#define QPEL_I32_REM(a, b) qpel_i32_rem((a), (b))
#endif

#ifdef QPEL_VALUE_ENABLED
// Scalar kernel arguments are passed by value.
#define QPEL_VALUE(type, name, binding) type name
#endif

#ifdef QPEL_GRID3D_ENABLED
// Grid3D kernels: one thread per shape element in (x, y, z) blocks.
#define QPEL_GRID3D_PARAM
#define QPEL_THREAD_INDEX(axis) (blockIdx.axis * blockDim.axis + threadIdx.axis)
#endif

#ifdef QPEL_INT_WIDTHS_ENABLED
// IR version 3: 8-, 16- and 64-bit integers and buffers of any element type.
#define QPEL_I8 signed char
#define QPEL_U16 unsigned short
#define QPEL_I16 short
#define QPEL_U64 unsigned long long
#define QPEL_I64 long long
static_assert(sizeof(short) == 2 && sizeof(long long) == 8, "qpel requires 16-bit short and 64-bit long long");
#define QPEL_READ(type) const type*
#define QPEL_WRITE(type) type*
// As for i32: LLONG_MIN / -1 wraps to LLONG_MIN and LLONG_MIN % -1 is 0. Divisors must be nonzero.
__device__ __forceinline__ long long qpel_i64_div(long long a, long long b) { return b == -1 ? (long long)(0ull - (unsigned long long)a) : a / b; }
__device__ __forceinline__ long long qpel_i64_rem(long long a, long long b) { return b == -1 ? 0 : a % b; }
#define QPEL_I64_DIV(a, b) qpel_i64_div((a), (b))
#define QPEL_I64_REM(a, b) qpel_i64_rem((a), (b))
#endif

#ifdef QPEL_BF16_ENABLED
#include <cuda_bf16.h>
#define QPEL_BF16 __nv_bfloat16
#define QPEL_TO_BF16(value) __float2bfloat16_rn((value))
#endif

#ifdef QPEL_F32_TO_INT_ENABLED
// float32 -> integer truncates toward zero, saturates out of range and maps NaN to 0 (C leaves
// out-of-range conversions undefined). Written out so both backends agree.
// On the GPU this is PTX's own float-to-integer cvt.rzi, which saturates and gives 0 for NaN (the
// 8- and 16-bit ones through the 32-bit conversion, then clamped); the CPU simulator writes it out.
#if defined(__CUDA_ARCH__)
#define QPEL_F32_TO_INT_FUNCTION(name, T, low, high, lowest, highest) \
    __device__ __forceinline__ T name(float x) { \
      const int v = __float2int_rz(x); return (T)(v < (int)(lowest) ? (int)(lowest) : v > (int)(highest) ? (int)(highest) : v); }
#define QPEL_F32_TO_WIDE_FUNCTION(name, T, conversion) \
    __device__ __forceinline__ T name(float x) { return (T)conversion(x); }
#else
#define QPEL_F32_TO_INT_FUNCTION(name, T, low, high, lowest, highest) \
    __device__ __forceinline__ T name(float x) { return x != x ? (T)0 : x < (low) ? (T)(lowest) : x >= (high) ? (T)(highest) : (T)x; }
#endif
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u8, unsigned char, 0.0f, 256.0f, 0, 255)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i8, signed char, -128.0f, 128.0f, -128, 127)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u16, unsigned short, 0.0f, 65536.0f, 0, 65535)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i16, short, -32768.0f, 32768.0f, -32768, 32767)
#if defined(__CUDA_ARCH__)
QPEL_F32_TO_WIDE_FUNCTION(qpel_f32_to_u32, unsigned int, __float2uint_rz)
QPEL_F32_TO_WIDE_FUNCTION(qpel_f32_to_i32, int, __float2int_rz)
QPEL_F32_TO_WIDE_FUNCTION(qpel_f32_to_u64, unsigned long long, __float2ull_rz)
QPEL_F32_TO_WIDE_FUNCTION(qpel_f32_to_i64, long long, __float2ll_rz)
#else
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u32, unsigned int, 0.0f, 4294967296.0f, 0u, 4294967295u)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i32, int, -2147483648.0f, 2147483648.0f, (-2147483647 - 1), 2147483647)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u64, unsigned long long, 0.0f, 18446744073709551616.0f, 0ull, 18446744073709551615ull)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i64, long long, -9223372036854775808.0f, 9223372036854775808.0f, (-9223372036854775807ll - 1), 9223372036854775807ll)
#endif
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
// Groups kernels: explicit geometry, the host launches whole groups (no partial blocks).
#define QPEL_GROUPS_PARAM
#define QPEL_GROUP_INDEX(axis) blockIdx.axis
#define QPEL_LOCAL_INDEX(axis) threadIdx.axis
#define QPEL_GROUP_SIZE(axis) blockDim.axis
#define QPEL_GROUP_COUNT(axis) gridDim.axis
#define QPEL_GROUPS_THREAD_INDEX(axis) (blockIdx.axis * blockDim.axis + threadIdx.axis)
// IR version 4 shared memory and barriers (Groups kernels).
#define QPEL_SHARED(T, name, n) __shared__ T name[n]
#define QPEL_BARRIER() __syncthreads()
#define QPEL_SUBGROUP_BARRIER() __syncwarp()  // IR version 16: the warp's lanes, ordering their memory
#define QPEL_BARRIER_COUNT(p) __syncthreads_count((p) ? 1 : 0)
#define QPEL_BARRIER_COUNT_SCRATCH
#endif

#ifdef QPEL_MATH_ENABLED
// Math functions (NUMERICS.md). Exact: the same bits on every backend. sqrt is __fsqrt_rn, which is
// correctly rounded whatever -prec-sqrt says. Bounded: CUDA documents at most 2 ulp for these.
#define QPEL_MATH_FABS(x) fabsf((x))
#define QPEL_MATH_FMIN(a, b) fminf((a), (b))
#define QPEL_MATH_FMAX(a, b) fmaxf((a), (b))
#define QPEL_MATH_FLOOR(x) floorf((x))
#define QPEL_MATH_CEIL(x) ceilf((x))
#define QPEL_MATH_TRUNC(x) truncf((x))
#define QPEL_MATH_ROUND(x) roundf((x))
#define QPEL_MATH_COPYSIGN(a, b) copysignf((a), (b))
#define QPEL_MATH_SQRT(x) __fsqrt_rn((x))
#define QPEL_MATH_EXP(x) expf((x))
#define QPEL_MATH_EXP2(x) exp2f((x))
#define QPEL_MATH_LOG(x) logf((x))
#define QPEL_MATH_LOG2(x) log2f((x))
#define QPEL_MATH_SIN(x) sinf((x))
#define QPEL_MATH_COS(x) cosf((x))
#define QPEL_MATH_TANH(x) tanhf((x))
#define QPEL_MATH_ERF(x) erff((x))
#define QPEL_MATH_EXPM1(x) expm1f((x))
#define QPEL_MATH_LOG1P(x) log1pf((x))
#define QPEL_MATH_POW(a, b) powf((a), (b))  // IR version 17: within 9 ulp (CUDA's documented bound)
#endif

#ifdef QPEL_CAS_ENABLED
// Compare-and-swap (IR version 4): the value found, as atomicCAS returns it.
#define QPEL_CAS(address, expected, value) atomicCAS((address), (expected), (value))
#endif

#ifdef QPEL_ATOMICS_ENABLED
// IR version 6 atomics (NUMERICS.md, Atomics), relaxed at device scope like the others.
#define QPEL_ATOMIC_F32 float*
#define QPEL_ATOMIC_U64 unsigned long long*
#define QPEL_MAX(address, value) atomicMax((address), (value))
#define QPEL_AND(address, value) atomicAnd((address), (value))
#define QPEL_OR(address, value) atomicOr((address), (value))
#define QPEL_XOR(address, value) atomicXor((address), (value))
#define QPEL_EXCHANGE(address, value) atomicExch((address), (value))
// A float32 read gets the stored bits: an or with 0 changes nothing, where an add of zero would
// turn -0 into +0 (and flush subnormals).
#if defined(__CUDA_ARCH__)
__device__ __forceinline__ float qpel_load_f32(float* address) { return *(volatile float*)address; }  // (see QPEL_LOAD)
#else
__device__ __forceinline__ float qpel_load_f32(float* address) { return __int_as_float(atomicOr((int*)address, 0)); }
#endif
#define QPEL_LOAD_F32(address) qpel_load_f32((address))
// 64-bit atomics are statements (Metal gives no previous value).
#define QPEL_ADD_U64(address, value) atomicAdd((address), (value))
#define QPEL_MIN_U64(address, value) atomicMin((address), (value))
#define QPEL_MAX_U64(address, value) atomicMax((address), (value))
#endif

#ifdef QPEL_BITCAST_ENABLED
// IR version 6: a 32-bit value's bits read as another 32-bit type.
__device__ __forceinline__ float qpel_bits_as_f32(unsigned int x) { return __uint_as_float(x); }
__device__ __forceinline__ float qpel_bits_as_f32(int x) { return __int_as_float(x); }
#define QPEL_BITCAST_U32(x) __float_as_uint((x))
#define QPEL_BITCAST_I32(x) __float_as_int((x))
#define QPEL_BITCAST_F32(x) qpel_bits_as_f32((x))
#endif

#ifdef QPEL_SUBGROUPS_ENABLED
// SIMD-group (warp) operations, IR version 5 (NUMERICS.md, Subgroups). A subgroup is 32 lanes
// of consecutive linear thread indices in the group; qpel_uniform.py ensures all 32 take part.
// A shuffle operand of 32 or more gives the lane its own value; a reduction combines the lanes
// in one fixed butterfly order (xor 16, 8, 4, 2, 1), so float sums are the same bits everywhere.
__device__ inline unsigned int qpel_subgroup_lane() { return (threadIdx.x + blockDim.x * (threadIdx.y + blockDim.y * threadIdx.z)) & 31u; }
__device__ inline unsigned int qpel_subgroup_index() { return (threadIdx.x + blockDim.x * (threadIdx.y + blockDim.y * threadIdx.z)) >> 5; }
__device__ inline unsigned int qpel_subgroup_size() { return 32u; }
#define QPEL_SUBGROUP_LANE() qpel_subgroup_lane()
#define QPEL_SUBGROUP_INDEX() qpel_subgroup_index()
#define QPEL_SUBGROUP_SIZE() qpel_subgroup_size()
#define QPEL_FULL_MASK 0xffffffffu
template <class T> __device__ inline T qpel_shuffle_index(T v, unsigned int lane) { return __shfl_sync(QPEL_FULL_MASK, v, (int)(lane & 31u)); }
template <class T> __device__ inline T qpel_shuffle_xor(T v, unsigned int m) { T r = __shfl_xor_sync(QPEL_FULL_MASK, v, (int)(m & 31u)); return m < 32u ? r : v; }
template <class T> __device__ inline T qpel_shuffle_down(T v, unsigned int d) { T r = __shfl_down_sync(QPEL_FULL_MASK, v, d & 31u); return d < 32u ? r : v; }
template <class T> __device__ inline T qpel_shuffle_up(T v, unsigned int d) { T r = __shfl_up_sync(QPEL_FULL_MASK, v, d & 31u); return d < 32u ? r : v; }
__device__ inline unsigned int qpel_combine_add(unsigned int a, unsigned int b) { return a + b; }
__device__ inline int qpel_combine_add(int a, int b) { return (int)((unsigned int)a + (unsigned int)b); }
__device__ inline float qpel_combine_add(float a, float b) { return __fadd_rn(a, b); }
__device__ inline unsigned int qpel_combine_min(unsigned int a, unsigned int b) { return b < a ? b : a; }
__device__ inline int qpel_combine_min(int a, int b) { return b < a ? b : a; }
__device__ inline float qpel_combine_min(float a, float b) { return fminf(a, b); }
__device__ inline unsigned int qpel_combine_max(unsigned int a, unsigned int b) { return a < b ? b : a; }
__device__ inline int qpel_combine_max(int a, int b) { return a < b ? b : a; }
__device__ inline float qpel_combine_max(float a, float b) { return fmaxf(a, b); }
template <class T> __device__ inline T qpel_combine_and(T a, T b) { return a & b; }
template <class T> __device__ inline T qpel_combine_or(T a, T b) { return a | b; }
template <class T> __device__ inline T qpel_combine_xor(T a, T b) { return a ^ b; }
#define QPEL_SUBGROUP_REDUCE(op) \
  template <class T> __device__ inline T qpel_subgroup_##op(T v) { \
    for (unsigned int m = 16u; m > 0u; m >>= 1) v = qpel_combine_##op(v, __shfl_xor_sync(QPEL_FULL_MASK, v, (int)m)); \
    return v; }
QPEL_SUBGROUP_REDUCE(add) QPEL_SUBGROUP_REDUCE(min) QPEL_SUBGROUP_REDUCE(max)
QPEL_SUBGROUP_REDUCE(and) QPEL_SUBGROUP_REDUCE(or) QPEL_SUBGROUP_REDUCE(xor)
#endif

#ifdef QPEL_MATRIX_ENABLED
// IR version 13: an 8x8 matrix held by a subgroup (NUMERICS.md, Matrices). Lane l holds row l / 4 at
// columns 2 (l % 4) and 2 (l % 4) + 1, the layout of an mma.sync accumulator (and of its row-major A
// fragment). f32: c + a b by shuffles and fmaf, k = 0 to 7 in order, keeping f32 precision (tensor
// cores would round f32 to tf32). f16 on the GPU: mma.sync.m16n8k8 with rows 8 to 15 zero (tensor
// cores; sm_75 and later), whose products are exact and whose sum order is the hardware's.
struct qpel_matrix_f32 { float v[2]; };
#define QPEL_MATRIX_F32 qpel_matrix_f32
#ifdef QPEL_F16_ENABLED  // (f16 matrices load f16 memory, which needs f16 storage)
// col: the same matrix as an mma.sync B fragment (b[2 (l % 4)][l / 4] and b[2 (l % 4) + 1][l / 4] in its two
// halves), loaded with it, so a multiply needs no shuffles; nvcc drops it where the matrix is only an A.
struct qpel_matrix_f16 { __half v[2]; unsigned int col; };
#define QPEL_MATRIX_F16 qpel_matrix_f16
#endif
__device__ __forceinline__ unsigned int qpel_matrix_lane() { return qpel_subgroup_lane(); }
__device__ __forceinline__ qpel_matrix_f32 qpel_matrix_fill_f32(float x) { qpel_matrix_f32 m; m.v[0] = x; m.v[1] = x; return m; }
#define QPEL_MATRIX_FILL_F32(x) qpel_matrix_fill_f32((x))
#ifdef QPEL_F16_ENABLED
__device__ __forceinline__ unsigned int qpel_half_pair(__half lo, __half hi) {
  return (unsigned int)__half_as_ushort(lo) | ((unsigned int)__half_as_ushort(hi) << 16); }
__device__ __forceinline__ qpel_matrix_f16 qpel_matrix_fill_f16(float x) {
  qpel_matrix_f16 m; m.v[0] = __float2half_rn(x); m.v[1] = m.v[0]; m.col = qpel_half_pair(m.v[0], m.v[0]); return m; }
#define QPEL_MATRIX_FILL_F16(x) qpel_matrix_fill_f16((x))
#endif
template <class M, class P> __device__ __forceinline__ M qpel_matrix_load(P base, unsigned int offset, unsigned int stride) {
  const unsigned int l = qpel_matrix_lane(), at = offset + (l >> 2) * stride + (l & 3u) * 2u;
  M m; m.v[0] = base[at]; m.v[1] = base[at + 1u]; return m; }
#ifdef QPEL_F16_ENABLED
template <class P> __device__ __forceinline__ qpel_matrix_f16 qpel_matrix_load_f16(P base, unsigned int offset, unsigned int stride) {
  qpel_matrix_f16 m = qpel_matrix_load<qpel_matrix_f16>(base, offset, stride);
  const unsigned int l = qpel_matrix_lane(), col = offset + (l & 3u) * 2u * stride + (l >> 2);  // b[2q][g], b[2q + 1][g]
  m.col = qpel_half_pair(base[col], base[col + stride]); return m; }
template <class P> __device__ __forceinline__ qpel_matrix_f16 qpel_matrix_load_columns_f16(P base, unsigned int offset, unsigned int stride);
#endif
template <class M, class P> __device__ __forceinline__ void qpel_matrix_store(M m, P base, unsigned int offset, unsigned int stride) {
  const unsigned int l = qpel_matrix_lane(), at = offset + (l >> 2) * stride + (l & 3u) * 2u;
  base[at] = m.v[0]; base[at + 1u] = m.v[1]; }
#define QPEL_MATRIX_LOAD_F32(base, offset, stride) qpel_matrix_load<qpel_matrix_f32>((base), (offset), (stride))
#define QPEL_MATRIX_LOAD_F16(base, offset, stride) qpel_matrix_load_f16((base), (offset), (stride))
#define QPEL_MATRIX_STORE_F32(m, base, offset, stride) qpel_matrix_store((m), (base), (offset), (stride))
#define QPEL_MATRIX_STORE_F16(m, base, offset, stride) qpel_matrix_store((m), (base), (offset), (stride))
// IR version 15: column-major memory, element (r, c) at base[offset + c stride + r]
template <class M, class P> __device__ __forceinline__ M qpel_matrix_load_columns(P base, unsigned int offset, unsigned int stride) {
  const unsigned int l = qpel_matrix_lane(), at = offset + (l & 3u) * 2u * stride + (l >> 2);
  M m; m.v[0] = base[at]; m.v[1] = base[at + stride]; return m; }
template <class M, class P> __device__ __forceinline__ void qpel_matrix_store_columns(M m, P base, unsigned int offset, unsigned int stride) {
  const unsigned int l = qpel_matrix_lane(), at = offset + (l & 3u) * 2u * stride + (l >> 2);
  base[at] = m.v[0]; base[at + stride] = m.v[1]; }
#define QPEL_MATRIX_LOAD_COLUMNS_F32(base, offset, stride) qpel_matrix_load_columns<qpel_matrix_f32>((base), (offset), (stride))
#ifdef QPEL_F16_ENABLED
template <class P> __device__ __forceinline__ qpel_matrix_f16 qpel_matrix_load_columns_f16(P base, unsigned int offset, unsigned int stride) {
  qpel_matrix_f16 m = qpel_matrix_load_columns<qpel_matrix_f16>(base, offset, stride);
  const unsigned int l = qpel_matrix_lane(), col = offset + (l >> 2) * stride + (l & 3u) * 2u;  // b[2q + i][g] at g stride + 2q + i
  m.col = qpel_half_pair(base[col], base[col + 1u]); return m; }
#endif
#define QPEL_MATRIX_LOAD_COLUMNS_F16(base, offset, stride) qpel_matrix_load_columns_f16((base), (offset), (stride))
#define QPEL_MATRIX_STORE_COLUMNS_F32(m, base, offset, stride) qpel_matrix_store_columns((m), (base), (offset), (stride))
#define QPEL_MATRIX_STORE_COLUMNS_F16(m, base, offset, stride) qpel_matrix_store_columns((m), (base), (offset), (stride))
// c[r][j] = fma(a[r][k], b[k][j], ...) for k = 0..7: a[r][k] is element k % 2 of lane 4 r + k / 2, and
// b[k][j], j = 2 (l % 4) + i, element i of lane 4 k + l % 4.
template <class M> __device__ __forceinline__ qpel_matrix_f32 qpel_matrix_mma_chain(M a, M b, qpel_matrix_f32 c) {
  const unsigned int l = qpel_matrix_lane(), r = l >> 2, q = l & 3u;
  float x0 = c.v[0], x1 = c.v[1];
  for (unsigned int k = 0; k < 8u; ++k) {
    const float ark = __shfl_sync(0xffffffffu, (float)a.v[k & 1u], (int)(r * 4u + (k >> 1)));
    const float b0 = __shfl_sync(0xffffffffu, (float)b.v[0], (int)(k * 4u + q));
    const float b1 = __shfl_sync(0xffffffffu, (float)b.v[1], (int)(k * 4u + q));
    x0 = __fmaf_rn(ark, b0, x0);
    x1 = __fmaf_rn(ark, b1, x1);
  }
  qpel_matrix_f32 d; d.v[0] = x0; d.v[1] = x1; return d;
}
__device__ __forceinline__ qpel_matrix_f32 qpel_matrix_mma_f32(qpel_matrix_f32 a, qpel_matrix_f32 b, qpel_matrix_f32 c) {
  return qpel_matrix_mma_chain(a, b, c); }
#ifdef QPEL_F16_ENABLED
__device__ __forceinline__ qpel_matrix_f32 qpel_matrix_mma_f16(qpel_matrix_f16 a, qpel_matrix_f16 b, qpel_matrix_f32 c) {
#if defined(__CUDA_ARCH__) && __CUDA_ARCH__ >= 750
  // B's column fragment came with it (qpel_matrix_f16::col); A's row fragment is its own layout
  const unsigned int b_frag = b.col;
  const unsigned int a_frag = qpel_half_pair(a.v[0], a.v[1]);
  float d0, d1, d2, d3;
  asm volatile("mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32 {%0, %1, %2, %3}, {%4, %5}, {%6}, {%7, %8, %9, %10};"
               : "=f"(d0), "=f"(d1), "=f"(d2), "=f"(d3)
               : "r"(a_frag), "r"(0u), "r"(b_frag), "f"(c.v[0]), "f"(c.v[1]), "f"(0.0f), "f"(0.0f));
  (void)d2; (void)d3;
  qpel_matrix_f32 d; d.v[0] = d0; d.v[1] = d1; return d;
#else
  return qpel_matrix_mma_chain(a, b, c);  // (f16 products are exact in f32)
#endif
}
#endif
#define QPEL_MATRIX_MMA_F32(a, b, c) qpel_matrix_mma_f32((a), (b), (c))
#define QPEL_MATRIX_MMA_F16(a, b, c) qpel_matrix_mma_f16((a), (b), (c))
#endif
