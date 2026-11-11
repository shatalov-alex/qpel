#include <metal_stdlib>
using namespace metal;

// An ordered float sum of up to four pixels (see cuda_examples/window_sum.cu).
kernel void window_sum(device const float* pixels [[buffer(0)]],
                       device const uchar* mask [[buffer(1)]],
                       device float* output [[buffer(2)]],
                       constant uint3& count [[buffer(3)]],
                       uint i [[thread_position_in_grid]]) {
    uint n = count.x * count.y * count.z;
    if (i >= n) return;
    float total = 0.0f;
    uint width = min(4u, n - i);
    for (uint k = 0u; k < width; ++k) {
        total += pixels[i + k];
    }
    output[i] = mask[i] != 0u ? total : 0.0f;
}
