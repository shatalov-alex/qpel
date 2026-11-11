#include <cuda_runtime.h>

// A fused multiply-add with one rounding: x * (1 - 2^-23) - 1.
__global__ void masked_copy(const float* pixels, const unsigned char* mask, float* output, uint3 count) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= count.x * count.y * count.z) return;
    output[i] = mask[i] != 0u ? __fmaf_rn(pixels[i], 0.99999988079071044921875f, -1.0f) : 0.0f;
}
