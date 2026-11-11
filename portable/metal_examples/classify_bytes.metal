#include <metal_stdlib>
using namespace metal;

// Different kernel name, parameter order, index name, comparison and outputs.
kernel void classify_bytes(device uint* classes [[buffer(0)]],
                           constant uint3& extent [[buffer(1)]],
                           device const uchar* limits [[buffer(2)]],
                           device const uchar* pixels [[buffer(3)]],
                           uint lane [[thread_position_in_grid]]) {
    if (lane >= extent.x * (extent.y * extent.z)) { return; }
    classes[lane] = pixels[lane] < limits[lane] ? 7u : 3u;
}
