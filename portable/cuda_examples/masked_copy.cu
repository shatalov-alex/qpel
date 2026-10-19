#include <cuda_runtime.h>

__global__ void masked_copy(const unsigned char* pixels,
                            const unsigned char* mask,
                            unsigned int* output,
                            unsigned int count) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= count) return;
    output[i] = mask[i] != 0u ? pixels[i] : 0u;
}
