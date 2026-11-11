#include <cuda_runtime.h>

// Signed arithmetic, an if/else-if chain and a loop with continue and break.
__global__ void signed_delta(const unsigned char* a,
                             const unsigned char* b,
                             int* delta,
                             unsigned int* bits,
                             uint3 shape) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= shape.x * shape.y * shape.z) return;
    int d = (int)a[i] - (int)b[i];
    if (d < 0) {
        delta[i] = d / 3 + d % 5 * 7;
    } else if (d > 100) {
        delta[i] = (d % 7 - 3) * 4;
    } else {
        delta[i] = ~d >> 1;
    }
    unsigned int v = a[i];
    unsigned int count = 0u;
    for (unsigned int k = 0u; k < 8u; ++k) {
        if (((v >> k) & 1u) == 0u) continue;
        count += 1u;
        if (count == 3u) break;
    }
    bits[i] = (count << 4) | (~v & 15u);
}
