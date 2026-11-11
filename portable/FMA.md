# Explicit float32 FMA in qpel

F32Fma(left, right, addend) requires three float32 expressions and returns float32. Integer operands require F32Cast. The common IR has a distinct float32_fma capability. Native MPS has the structure concept, editor and checker. qpel.sandbox contains float_fma and float_fma_launch.

CUDA lowers to __fmaf_rn; Metal lowers to metal::fma. This explicitly requests a fused multiplication/addition with no intermediate product rounding. Ordinary Binary multiply/add expressions stay unfused since f32-strict-v1 (NUMERICS.md): CUDA uses separately rounded intrinsics and Metal turns contraction off. No blanket cross-device bitwise guarantee for subnormals, exceptional values or fast-math compilation is made.

Primary specifications: [NVIDIA single-precision intrinsics](https://docs.nvidia.com/cuda/archive/12.0.1/cuda-math-api/group__CUDA__MATH__INTRINSIC__SINGLE.html), [Metal Shading Language specification](https://developer.apple.com/metal/Metal-Shading-Language-Specification.pdf), sections 1.6.3 and 6.7/Table 6.4.

## Verification

92 offline tests passed, including nine native fixtures. The new native model was generated and its packaged CUDA source executed on CPU with a CUDA shim, explicit std::fma and UBSan. An independent oracle computes selected dyadic expressions exactly in binary64 before final float32 rounding: 517 inputs plus padded invocations. It includes `(1+2^-23)*(1-2^-23)-1`: fused result -2^-46, whereas separately rounded operations give zero. This validates generated logic, not NVIDIA execution.

A native negative test rejects an integer FMA argument. The valid model was restored. All five native MPS model checks and the rebuild succeeded.

## Apple GPU

Native float32 scaling (8 cases, `float32` fixture) and FMA (4 cases, including fused cancellation -2^-46) pass on the Apple GPU.

```sh
python3 portable/gpu_tests/run_gpu_tests.py fma32
```

Expected: four native-qpel FMA GPU cases with the cancellation result -2^-46. Cases include masks, batch sizes 1/2, noncontiguous input and padded grids. No performance improvement is claimed.
