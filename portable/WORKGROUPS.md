# Workgroup float32 sum

WorkgroupSumF32(value) adds one float32 contribution from every participating invocation in a 1D workgroup and broadcasts the sum to every invocation. Both native MPS and shared IR support the concept, editor, checking and generation.

The implementation uses 256 float slots (1 KiB) of CUDA shared / Metal threadgroup memory. Each lane writes its input; barriers separate adjacent-pair tree steps at strides 1,2,4,... . A final barrier ensures all lanes read the result before a subsequent collective reuses scratch. CUDA uses __syncthreads and __fadd_rn; Metal uses threadgroup_barrier and float32 fma for each addition. Kernel ABI adds Metal local-lane and actual-group-size builtins only for collective packages. Workgroup size is restricted to the checked Pipeline range 1..256. Direct invocation with larger groups is outside the contract.

## Uniform participation restrictions

A collective must be the direct initializer of a local declared at the top level of a kernel body. It is rejected inside conditions, loops, nested expressions and helper functions. A kernel containing collectives cannot contain Return or While nodes anywhere. These conservative restrictions ensure supported control flow reaches each collective in the same order. They do not prove termination of arbitrary helper computation, memory bounds or absence of all data races.

Padded CUDA lanes must contribute zero and still execute the collective; only their final stores may be guarded. Metal may dispatch a partial final group, using its actual width. The example uses explicit lazy input masking to avoid out-of-range reads. Group sizes and grouping are part of the numerical algorithm: changing blockSize can change output. The adjacent-pair tree intentionally differs from the ordered serial ReduceSumF32. Exceptional/subnormal behavior and full cross-device bitwise identity are not claimed.

## Native example and validation

qpel.sandbox has float_group_sum / float_group_sum_launch. It masks and scales each input by 0.5, sums within its workgroup, and writes the broadcast sum at each valid index. It is a collective primitive demonstration, not a complete tensor-reduction API.

106 tests pass, with twelve native fixtures. A threaded CPU CUDA shim executes generated source with real mutex/condition-variable barriers and UBSan: 18 kernel configurations (group sizes 1,3,8,32,64,256 and element counts 1,17,67), plus three consecutive-collective scratch-reuse cases. This is not nvcc, GPU scheduling, or a formal race proof. Common-core tests reject early return, conditional/nested collectives and missing capabilities. MPS rejects early return; the valid native model was restored and rebuilt. Five native models check cleanly.

## Apple GPU check

```sh
python3 portable/gpu_tests/run_gpu_tests.py workgroup
```

The runner covers 18 cases: generated host allocation/dispatch with strided inputs plus direct shader tests at group sizes 1,3,4,31,64,128,256. Tests include partial groups and a cancellation case distinguishing the tree from serial addition. Passed on 2026-10-19 (torch 2.14, Apple GPU). Performance has not been benchmarked.

