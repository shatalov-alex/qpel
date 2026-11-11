#include <cuda_runtime.h>

// Scalar kernel arguments next to the element count. The count n becomes a
// qpel shape (n, 1, 1); scale and offset stay ordinary arguments.
__global__ void scale_offset(const float* x,
                             float* y,
                             int* z,
                             float scale,
                             int offset,
                             unsigned int n) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        y[i] = x[i] * scale;
        z[i] = (int)i * offset - 7;
    }
}
