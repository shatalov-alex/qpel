#include <metal_stdlib>
using namespace metal;

// Signed arithmetic, an if/else-if chain and a loop with continue and break.
kernel void signed_delta(device const uchar* a [[buffer(0)]],
                         device const uchar* b [[buffer(1)]],
                         device int* delta [[buffer(2)]],
                         device uint* bits [[buffer(3)]],
                         constant uint3& shape [[buffer(4)]],
                         uint i [[thread_position_in_grid]]) {
    if (i >= shape.x * shape.y * shape.z) return;
    int d = int(a[i]) - int(b[i]);
    if (d < 0) {
        delta[i] = d / 3 + d % 5 * 7;
    } else if (d > 100) {
        delta[i] = (d % 7 - 3) * 4;
    } else {
        delta[i] = ~d >> 1;
    }
    uint v = a[i];
    uint count = 0;
    for (uint k = 0; k < 8; k++) {
        if (((v >> k) & 1) == 0) continue;
        count += 1;
        if (count == 3) break;
    }
    bits[i] = (count << 4) | (~v & 15u);
}
