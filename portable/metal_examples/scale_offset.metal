#include <metal_stdlib>
using namespace metal;

// Scalar kernel arguments next to the element count. The count n becomes a
// qpel shape (n, 1, 1); scale and offset stay ordinary arguments.
kernel void scale_offset(device const float* x [[buffer(0)]],
                         device float* y [[buffer(1)]],
                         device int* z [[buffer(2)]],
                         constant float& scale [[buffer(3)]],
                         constant int& offset [[buffer(4)]],
                         constant uint& n [[buffer(5)]],
                         uint i [[thread_position_in_grid]]) {
    if (i < n) {
        y[i] = x[i] * scale;
        z[i] = int(i) * offset - 7;
    }
}
