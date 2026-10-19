#include <cuda_runtime.h>

// Different kernel name, parameter order, index name, comparison and outputs.
__global__ void classify_bytes(
    unsigned int* classes,
    uint3 extent,
    const unsigned char* limits,
    const unsigned char* pixels) {
    const unsigned int lane = blockIdx.x * blockDim.x + threadIdx.x;
    if (lane >= extent.x * (extent.y * extent.z)) { return; }
    classes[lane] = pixels[lane] < limits[lane] ? 7u : 3u;
}
