#include <metal_stdlib>
using namespace metal;

// The grid-stride loop of cuda_examples/grid_stride.cu, with Metal's threadgroup builtins.
kernel void grid_stride(device const float* x [[buffer(0)]], device float* y [[buffer(1)]],
                        constant float& scale [[buffer(2)]], constant float& bias [[buffer(3)]],
                        constant long& n [[buffer(4)]],
                        uint3 tgpig [[threadgroup_position_in_grid]], uint3 tpitg [[thread_position_in_threadgroup]],
                        uint3 ntg [[threads_per_threadgroup]], uint3 ngroups [[threadgroups_per_grid]]) {
    const long stride = (long)ntg.x * ngroups.x;
    for (long i = (long)tgpig.x * ntg.x + tpitg.x; i < n; i += stride) {
        y[i] = scale * x[i] + bias;
    }
}
