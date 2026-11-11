#include <cuda_runtime.h>
#include <cuda_fp16.h>

// half storage, float arithmetic: widen, add 2^-11 with one rounding, narrow.
__global__ void masked_copy(const __half* pixels, const unsigned char* mask, __half* output, uint3 count) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= count.x * count.y * count.z) return;
    output[i] = __float2half_rn(mask[i] != 0u ? __fmaf_rn((float)pixels[i], 1.0f, 0.00048828125f) : 0.0f);
}
