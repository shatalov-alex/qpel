// Generated backend package. Edit the MPS model or backend support.
// qpel backend: device-scope relaxed unsigned 32-bit atomics.
#include <cuda_runtime.h>
struct alignas(16) QpelShape { unsigned int x, y, z, padding; };
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
#define QPEL_GRID_PARAM(name)
#define QPEL_GRID_LOCAL(name) const unsigned int name = blockIdx.x * blockDim.x + threadIdx.x;
// Legacy CUDA atomics have relaxed ordering at device scope. A zero-add is an
// atomic read implemented as an RMW: correct, but potentially slower than load.
#define QPEL_LOAD(address) atomicAdd((address), 0u)
#define QPEL_STORE(address, value) atomicExch((address), (value))
#define QPEL_ADD(address, value) atomicAdd((address), (value))
#define QPEL_MIN(address, value) atomicMin((address), (value))

#define QPEL_F32 float
#define QPEL_READ_F32 const float*
#define QPEL_WRITE_F32 float*

#define QPEL_FMA_F32(a, b, c) __fmaf_rn((a), (b), (c))

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
#define QPEL_F32_TO_INT_FUNCTION(name, T, low, high, lowest, highest) \
    __device__ __forceinline__ T name(float x) { return x != x ? (T)0 : x < (low) ? (T)(lowest) : x >= (high) ? (T)(highest) : (T)x; }
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u8, unsigned char, 0.0f, 256.0f, 0, 255)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i8, signed char, -128.0f, 128.0f, -128, 127)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u16, unsigned short, 0.0f, 65536.0f, 0, 65535)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i16, short, -32768.0f, 32768.0f, -32768, 32767)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u32, unsigned int, 0.0f, 4294967296.0f, 0u, 4294967295u)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i32, int, -2147483648.0f, 2147483648.0f, (-2147483647 - 1), 2147483647)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_u64, unsigned long long, 0.0f, 18446744073709551616.0f, 0ull, 18446744073709551615ull)
QPEL_F32_TO_INT_FUNCTION(qpel_f32_to_i64, long long, -9223372036854775808.0f, 9223372036854775808.0f, (-9223372036854775807ll - 1), 9223372036854775807ll)
#define QPEL_F32_TO_U8(x) qpel_f32_to_u8((x))
#define QPEL_F32_TO_I8(x) qpel_f32_to_i8((x))
#define QPEL_F32_TO_U16(x) qpel_f32_to_u16((x))
#define QPEL_F32_TO_I16(x) qpel_f32_to_i16((x))
#define QPEL_F32_TO_U32(x) qpel_f32_to_u32((x))
#define QPEL_F32_TO_I32(x) qpel_f32_to_i32((x))
#define QPEL_F32_TO_U64(x) qpel_f32_to_u64((x))
#define QPEL_F32_TO_I64(x) qpel_f32_to_i64((x))
#endif

// Generated from qpel IR. Do not edit.
// qpel numeric contract: u32-modulo-v1
QPEL_KERNEL void initialize(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))) * (QPEL_U32)(shape.z))))) {
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

QPEL_KERNEL void merge(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
QPEL_U32 w = shape.x;
QPEL_U32 h = shape.y;
if ((((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(w) * (QPEL_U32)(h))) * (QPEL_U32)(shape.z)))) || (!mask[qpel_index]))) {
return ;
 }
QPEL_U32 x = ((QPEL_U32)(qpel_index) % (QPEL_U32)(w));
QPEL_U32 y = ((QPEL_U32)(((QPEL_U32)(qpel_index) / (QPEL_U32)(w))) % (QPEL_U32)(h));
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

QPEL_KERNEL void label_count(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3), QPEL_PARAM(QPEL_WRITE_U32, labels, 4) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))) * (QPEL_U32)(shape.z))))) {
return ;
 }
if ((!mask[qpel_index])) {
labels[qpel_index] = 0u;
return ;
 }
QPEL_U32 r = root(parent, qpel_index);
labels[qpel_index] = ((QPEL_U32)(((QPEL_U32)(r) % (QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))))) + (QPEL_U32)(1u));
QPEL_ADD((counts + (QPEL_U32)(r)), 1u);
 }
}

QPEL_KERNEL void areas(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3), QPEL_PARAM(QPEL_WRITE_U32, labels, 4), QPEL_PARAM(QPEL_WRITE_U32, output, 5) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
QPEL_U32 plane = ((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y));
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(plane) * (QPEL_U32)(shape.z))))) {
return ;
 }
QPEL_U32 label = labels[qpel_index];
output[qpel_index] = (label ? QPEL_LOAD((((counts + (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(qpel_index) / (QPEL_U32)(plane))) * (QPEL_U32)(plane)))) + (QPEL_U32)(label)) - (QPEL_U32)(1u))) : 0u);
 }
}
