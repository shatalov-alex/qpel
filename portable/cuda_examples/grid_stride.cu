#include <cuda_runtime.h>

// A grid-stride loop with a 64-bit count, as llama.cpp's element-wise kernels are
// written: any number of blocks covers any n. The count stays an ordinary argument.
__global__ void grid_stride(const float* x, float* y, float scale, float bias, long long n) {
    const long long stride = (long long)blockDim.x * gridDim.x;
    for (long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x; i < n; i += stride) {
        y[i] = scale * x[i] + bias;
    }
}
