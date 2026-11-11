#include <metal_stdlib>
using namespace metal;

// Ordinary Metal Shading Language; no qpel macros or annotations.
kernel void apply_threshold(device const uchar* intensity [[buffer(0)]],
                            device const uchar* cutoff [[buffer(1)]],
                            constant uint3& dimensions [[buffer(2)]],
                            device uint* binary [[buffer(3)]],
                            uint i [[thread_position_in_grid]]) {
    if (i >= dimensions.x * dimensions.y * dimensions.z) return;
    binary[i] = intensity[i] >= cutoff[i] ? 1u : 0u;
}
