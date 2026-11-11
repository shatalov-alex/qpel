#include <cuda_runtime.h>

// A 2D/3D kernel: one thread per pixel of each image in a batch, summing the
// 3x3 neighbourhood that lies inside the image.
__global__ void box_sum(const unsigned char* image, unsigned int* sums, uint3 shape) {
    unsigned int x = blockIdx.x * blockDim.x + threadIdx.x;
    unsigned int y = blockIdx.y * blockDim.y + threadIdx.y;
    unsigned int z = blockIdx.z * blockDim.z + threadIdx.z;
    if (x >= shape.x || y >= shape.y || z >= shape.z) return;
    unsigned int total = 0u;
    for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
            int nx = (int)x + dx;
            int ny = (int)y + dy;
            if (nx < 0 || ny < 0 || nx >= (int)shape.x || ny >= (int)shape.y) continue;
            total += image[(z * shape.y + (unsigned int)ny) * shape.x + (unsigned int)nx];
        }
    }
    sums[(z * shape.y + y) * shape.x + x] = total;
}
