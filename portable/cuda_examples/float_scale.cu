#include <cuda_runtime.h>

// float32: halve the pixels the mask selects. The explicit (float) is kept by
// qpel as a conversion, so this file imports to the float32 fixture exactly.
__global__ void masked_copy(const float* pixels, const unsigned char* mask, float* output, uint3 count) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= count.x * count.y * count.z) return;
    output[i] = mask[i] != 0u ? pixels[i] * (float)0.5f : 0.0f;
}
