#include <cuda_runtime.h>

// qpel's math functions: exact ones (the same bits everywhere) and bounded ones
// (within a few ulp), each written the C way.
__global__ void math_ops(const float* x, float* exact, float* bounded, unsigned int n) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float v = x[i];
    exact[i] = fmaxf(fminf(floorf(v) + ceilf(v) - truncf(v), 100.0f), -100.0f) + copysignf(roundf(v), -v) + sqrtf(fabsf(v));
    bounded[i] = expf(v * 0.01f) + logf(fabsf(v) + 1.0f) + sinf(v) * cosf(v) + tanhf(v) + exp2f(-fabsf(v)) + log2f(fabsf(v) + 2.0f);
}
