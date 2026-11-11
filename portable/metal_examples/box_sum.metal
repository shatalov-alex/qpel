#include <metal_stdlib>
using namespace metal;

// A 2D/3D kernel: one thread per pixel of each image in a batch, summing the
// 3x3 neighbourhood that lies inside the image.
kernel void box_sum(device const uchar* image [[buffer(0)]],
                    device uint* sums [[buffer(1)]],
                    constant uint3& shape [[buffer(2)]],
                    uint3 p [[thread_position_in_grid]]) {
    if (p.x >= shape.x || p.y >= shape.y || p.z >= shape.z) return;
    uint total = 0;
    for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
            int nx = int(p.x) + dx;
            int ny = int(p.y) + dy;
            if (nx < 0 || ny < 0 || nx >= int(shape.x) || ny >= int(shape.y)) continue;
            total += image[(p.z * shape.y + uint(ny)) * shape.x + uint(nx)];
        }
    }
    sums[(p.z * shape.y + p.y) * shape.x + p.x] = total;
}
