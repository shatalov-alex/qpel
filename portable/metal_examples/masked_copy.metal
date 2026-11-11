#include <metal_stdlib>
using namespace metal;

kernel void masked_copy(device const uchar* pixels [[buffer(0)]],
                        device const uchar* mask [[buffer(1)]],
                        device uint* output [[buffer(2)]],
                        constant uint& count [[buffer(3)]],
                        uint i [[thread_position_in_grid]]) {
    if (i >= count) return;
    output[i] = mask[i] != 0u ? pixels[i] : 0u;
}
