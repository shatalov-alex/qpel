#include <metal_stdlib>
using namespace metal;

// cuda_examples/math_ops.cu with Metal's function names.
kernel void math_ops(device const float* x [[buffer(0)]], device float* exact [[buffer(1)]],
                     device float* bounded [[buffer(2)]], constant uint& n [[buffer(3)]],
                     uint i [[thread_position_in_grid]]) {
    if (i >= n) return;
    float v = x[i];
    exact[i] = fmax(fmin(floor(v) + ceil(v) - trunc(v), 100.0f), -100.0f) + copysign(round(v), -v) + sqrt(fabs(v));
    bounded[i] = exp(v * 0.01f) + log(fabs(v) + 1.0f) + sin(v) * cos(v) + tanh(v) + exp2(-fabs(v)) + log2(fabs(v) + 2.0f);
}
