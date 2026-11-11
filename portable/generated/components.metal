// Generated backend package. Edit the MPS model or backend support.
// qpel backend: device-scope relaxed unsigned 32-bit atomics.
#include <metal_stdlib>
using namespace metal;
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
