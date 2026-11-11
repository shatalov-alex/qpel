#include <cuda_runtime.h>

// An ordered float sum of up to four pixels starting at each selected one,
// written as an ordinary loop. qpel has its own ordered-sum operation; this
// file checks that the plain loop keeps its order through qpel too.
__global__ void window_sum(const float* pixels, const unsigned char* mask, float* output, uint3 count) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    unsigned int n = count.x * count.y * count.z;
    if (i >= n) return;
    float total = 0.0f;
    unsigned int width = min(4u, n - i);
    for (unsigned int k = 0u; k < width; ++k) {
        total += pixels[i + k];
    }
    output[i] = mask[i] != 0u ? total : 0.0f;
}
