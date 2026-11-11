#include <metal_stdlib>
using namespace metal;

// half storage, float arithmetic: widen, add 2^-11 with one rounding, narrow.
kernel void masked_copy(device const half* pixels [[buffer(0)]],
                        device const uchar* mask [[buffer(1)]],
                        device half* output [[buffer(2)]],
                        constant uint3& count [[buffer(3)]],
                        uint i [[thread_position_in_grid]]) {
    if (i >= count.x * count.y * count.z) return;
    output[i] = half(mask[i] != 0u ? fma(float(pixels[i]), 1.0f, 0.00048828125f) : 0.0f);
}
