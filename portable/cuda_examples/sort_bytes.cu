#include <cuda_runtime.h>

// Thread-private arrays (IR version 4): each element's three low bytes are sorted in a
// local array with data-dependent indices, and a partly initialized array counts the
// bytes by their value modulo 4 (the elements its initializer leaves out start at zero).
__global__ void sort_bytes(const unsigned int* x, unsigned int* sorted, unsigned int* counts,
                           unsigned int n) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        unsigned int v[3] = {x[i] & 255u, (x[i] >> 8) & 255u, (x[i] >> 16) & 255u};
        for (int a = 0; a < 2; a++) {
            for (int b = 0; b < 2 - a; b++) {
                if (v[b] > v[b + 1]) {
                    unsigned int t = v[b];
                    v[b] = v[b + 1];
                    v[b + 1] = t;
                }
            }
        }
        unsigned int h[4] = {x[i] >> 30};
        for (int k = 0; k < 3; k++) {
            h[v[k] % 4u] += 1u;
        }
        sorted[i] = v[0] | (v[1] << 8) | (v[2] << 16);
        counts[i] = h[0] | (h[1] << 8) | (h[2] << 16) | (h[3] << 24);
    }
}
