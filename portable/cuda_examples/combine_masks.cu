#include <cuda_runtime.h>

__global__ void combine_masks(const unsigned char* left,
                             const unsigned char* right,
                             uint3 extent,
                             unsigned int* result) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= extent.x * extent.y * extent.z) return;
    // CUDA precedence is &: highest, then ^, then |. Preserve bit 31 too.
    result[i] = left[i] | right[i] & 170u ^ 85u | 2147483648u;
}
