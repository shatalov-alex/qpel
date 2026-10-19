# qpel mixed precision: float16 storage, float32 computation

Implemented in both the shared core and native MPS: F16Type, ReadOnlyF16BufferType, F16BufferType and F16Cast. F32Cast now accepts a half value. Each new native concept has an editor and TextGen; compiled checking rules and all five language/sandbox model checks pass.

The contract is explicit: load half -> F32Cast -> float32 arithmetic/FMA -> F16Cast -> store half. There is no implicit widening, narrowing, half arithmetic, half FMA or half atomics. Half values may be passed, selected or stored without arithmetic. F16Cast requires float32; F32Cast is required first for integer inputs.

CUDA conversion uses __float2half_rn and CUDA half storage. Metal uses half conversion (default round-to-nearest ties-to-even); source builds that override conversion rounding are outside this contract. The CUDA half header is enabled only for packages containing half constructs. The generated Metal host validates/allocates torch.float16 buffers.

## Native example

qpel.sandbox contains half_mixed_scale and half_mixed_scale_launch. It computes x*1 + 2^-11 using float32 FMA from half inputs, selects zero for masked elements and explicitly rounds into half output. At 1.0 the result is halfway and rounds to 1.0; at 1.0009765625 it rounds to 1.001953125. The model is saved as a native regression fixture in portable/tests/fixtures/qpel_ir/half.

## Validation

97 tests passed. Ten native fixtures retain compatibility-rendered source and launch-plan checks. The half model's packaged CUDA source ran as CPU C++ using a CUDA shim, clang _Float16 storage, std::fma and UBSan. It checked all 63,488 finite half bit patterns against Python binary16/binary32 packing, including padded invocations. This is not CUDA/nvcc or GPU verification. Native build succeeded and a negative FMA operand check was rejected, then the valid model was restored.

Earlier user GPU results are confirmed: 8 native float32 scaling cases and 4 FMA cases, including fused cancellation -2^-46. These are separate artifacts, not verification of the new half package.

## Apple GPU check

```sh
sh <outputs>/qpel-half/run_metal.sh
```

Expected four cases: half input/output, float32 FMA, ties-to-even, masks, batches and strided inputs. Only normal-range values and zero are in the GPU smoke test. GPU subnormal handling, NaN payloads, overflow behavior, general cross-device reproducibility and performance have not been established. Float16 reduces storage size for these buffers; no model-level speedup is claimed.

User GPU confirmation: all four native mixed float16/float32 cases passed, including ties-to-even. Saved report: outputs/qpel-half/gpu-report.json. This does not expand the GPU evidence to subnormal/exceptional values.
