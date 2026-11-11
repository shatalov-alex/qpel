#include <metal_stdlib>
using namespace metal;

kernel void combine_masks(device const uchar* left [[buffer(0)]],
                          device const uchar* right [[buffer(1)]],
                          constant uint3& extent [[buffer(2)]],
                          device uint* result [[buffer(3)]],
                          uint i [[thread_position_in_grid]]) {
    if (i >= extent.x * extent.y * extent.z) return;
    // Same precedence as CUDA: & first, then ^, then |. Bit 31 is kept.
    result[i] = left[i] | right[i] & 170u ^ 85u | 2147483648u;
}
