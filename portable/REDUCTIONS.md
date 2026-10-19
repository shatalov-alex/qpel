# Ordered float32 sum reduction

ReduceSumF32(buffer, start, count) is now a native qpel concept with editor/checker/TextGen and a shared-IR operation. It accepts a readonly or writable float32 buffer and unsigned start/count. Starting at positive zero, it adds consecutive elements in increasing index order, rounding each step to float32. An empty slice returns zero without reading elements. The entire addressed range must be valid and start+count must not wrap; no static bounds proof or runtime guard is added.

This is a serial per-invocation baseline, not a subgroup/threadgroup reduction or an acceleration claim. A tree reduction changes rounding order and will require a separate numerical contract, scheduling and barrier validation. SIMD width is not assumed identical on CUDA and Metal.

CUDA uses __fadd_rn; Metal expresses the rounded add through fma(1, value, accumulator). Compiler settings and device treatment of subnormals/exceptional values still limit general bitwise portability. Buffer reads are included in pipeline/helper effect analysis; even a statically empty slice conservatively counts as a read there.

## Native example and evidence

float_window_sum / float_window_sum_launch in qpel.sandbox computes masked windows of up to four elements over the flattened input. Windows can cross row/batch boundaries; this is deliberate flattened-slice semantics, not independent row reduction. Last windows shrink to remain within the allocated buffer.

101 tests pass, including eleven native fixtures. The packaged CUDA code ran on CPU with a shim and UBSan against an independent ordered float32 oracle. Tests cover all 261 output windows, padded invocations, empty and singleton slices and cancellation. For [2^24,1,-2^24,1], the specified left fold gives 1, distinguishing it from some tree sums. Pipeline tests reject reading an unwritten scratch buffer through the reduction. A native MPS negative check rejects a byte buffer; the valid model was restored and project rebuild succeeded. All five native model checks passed.

## Apple GPU runner (not yet executed)

```sh
sh <outputs>/qpel-reduce/run_metal.sh
```

Expected six cases including singleton input, cancellation, masks, tail windows, batches and strided inputs. GPU result is not claimed until that run passes. The earlier mixed float16/float32 runner passed four user GPU cases, recorded in ../qpel-half/gpu-report.json.

User GPU confirmation: all six native ordered-sum cases passed, including cancellation-order verification; recorded in outputs/qpel-reduce/gpu-report.json.
