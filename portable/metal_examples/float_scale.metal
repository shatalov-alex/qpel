#include <metal_stdlib>
using namespace metal;

// float32: halve the pixels the mask selects (see cuda_examples/float_scale.cu).
kernel void masked_copy(device const float* pixels [[buffer(0)]],
                        device const uchar* mask [[buffer(1)]],
                        device float* output [[buffer(2)]],
                        constant uint3& count [[buffer(3)]],
                        uint i [[thread_position_in_grid]]) {
    if (i >= count.x * count.y * count.z) return;
    output[i] = mask[i] != 0u ? pixels[i] * (float)0.5f : 0.0f;
}
