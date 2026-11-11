#include <metal_stdlib>
using namespace metal;

// A fused multiply-add with one rounding: x * (1 - 2^-23) - 1.
kernel void masked_copy(device const float* pixels [[buffer(0)]],
                        device const uchar* mask [[buffer(1)]],
                        device float* output [[buffer(2)]],
                        constant uint3& count [[buffer(3)]],
                        uint i [[thread_position_in_grid]]) {
    if (i >= count.x * count.y * count.z) return;
    output[i] = mask[i] != 0u ? fma(pixels[i], 0.99999988079071044921875f, -1.0f) : 0.0f;
}
