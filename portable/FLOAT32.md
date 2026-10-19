# qpel float32

Implemented F32Type, ReadOnlyF32BufferType, F32BufferType, F32Literal and F32Cast in the portable graph. Same-type float32 arithmetic (+,-,*,/), comparisons, unary negation, selection, locals, helper signatures and buffer reads/writes are checked and emitted for both backends. Integer-to-float conversion is explicit; bool conversion, float-to-integer conversion, float indices, implicit mixed arithmetic, float atomics, remainder and bitwise operations are unsupported. Float min/max and float16 remain pending.

The Metal host generator validates/allocates torch.float32 tensors. This is not a claim of a production CUDA host runtime.

## Precision

Float literals must be finite nonnegative decimal values that round to finite float32; use Unary minus for negatives. Tiny values may round to zero. Arithmetic can still produce infinities or NaNs. Device compiler contraction, fast-math and subnormal handling are not pinned by this milestone: cross-device bitwise equality is not promised. Integer-to-float can round large integers. Accumulation stays float32; there is no float16 or mixed-precision accumulation yet. A strict precision policy and real-device tolerance tests are still needed.

## Evidence and limits

89 automated tests pass, including execution of a saved native MPS export. A masked float scaling kernel generated and packaged from the graph was compiled as CPU C++ with a small CUDA execution shim, UBSan and contraction disabled. It verified 257 elements and five padded invocations. This does not validate nvcc, Metal compilation, GPU performance or cross-device floating-point behavior.

program.qpel.json is a reproducible portable-core example; generated/ contains CUDA, Metal and the Metal host. The original graph example now also has native MPS counterparts: float_masked_scale and float_masked_scale_launch in qpel.sandbox. All five float concepts have native structure declarations, editors, checks and TextGen. Four negative native checks passed and the valid model was restored. The native export is saved in tests/fixtures/qpel_ir/float32. Existing integer MPS models and the native migration guard remain supported.

## GPU confirmation and FMA

All eight float32 scaling GPU cases pass (`python3 portable/gpu_tests/run_gpu_tests.py float32`, torch 2.14.0, 2026-10-19). This evidence covers the tested finite scaling cases only. Explicit F32Fma is now also available natively and in the common core; see FMA.md.
