#include <cuda_runtime.h>

// Ordinary CUDA syntax; no qpel macros or annotations.
extern "C" __global__ void apply_threshold(
    const unsigned char* intensity,
    const unsigned char* cutoff,
    uint3 dimensions,
    unsigned int* binary) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= dimensions.x * dimensions.y * dimensions.z) return;
    binary[i] = intensity[i] >= cutoff[i] ? 1u : 0u;
}
