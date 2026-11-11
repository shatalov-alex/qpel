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
