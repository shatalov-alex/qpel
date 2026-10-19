# Restricted CUDA import into qpel

The standalone source original.cu was parsed and imported into the existing JetBrains MPS project as cuda_threshold and cuda_threshold_launch. These are actual qpel AST nodes with declaration references; no generated code was pasted into a raw-text node. Native MPS checks and TextGen build passed. The resulting source and plan were packaged by the existing backend and Metal host generators.

## Accepted CUDA subset

One optional `extern "C"` `__global__ void` kernel; one or more `const unsigned char*` inputs, one `uint3` shape and one `unsigned int*` output. Parameter order/names are preserved. A leading `unsigned int` (optionally const) index must use `blockIdx.x * blockDim.x + threadIdx.x`. An early return must guard `index >= shape.x * shape.y * shape.z`. The single output write is at that index; its expression can copy an input byte, write a decimal uint32 literal or select numeric values using comparisons. Parentheses, comments and the cuda_runtime.h include are supported. Numeric bitwise &, ^ and | are also supported, with CUDA precedence; shifts and complement remain unsupported. Output arithmetic, helpers, loops, macros, floating point, atomics, shared memory and other CUDA facilities are rejected with line/column diagnostics.

This is a handwritten parser for a declared subset, not a complete CUDA C++ frontend or a Triton importer. Full source parsing finishes before any IDE mutation. A failed multi-call IDE import can leave partial roots; import-report.json records their references. Existing root names are rejected rather than overwritten. The importer never edits .mps XML directly.

## Execution contract

Pipeline generation uses an explicit importer convention: every input/output has the shape's element count, the single writable pointer is an output, the kernel covers the one-dimensional flattened volume, and dispatch completion is required. Block size is a CLI option (64 for this example). CUDA host launch code is not imported or inferred. Source `uint3` is semantically mapped to qpel Shape3D; generated CUDA uses padded QpelShape by value, and Metal uses a padded shape buffer. This is not preservation of a pre-existing binary host ABI. The generated Metal host enforces positive dimensions and INT32_MAX volume.

## Evidence

- 6 parser test groups passed, including 24 unsupported-source cases. Renaming and changing comparison/branch values change the parsed expression.
- Native imported module and Pipeline checks and MPS build passed.
- 402 CPU cases compare original CUDA, imported/generated CUDA and an independent oracle. Includes all 65,536 byte pairs, edge values, batches, odd dimensions and padded threads. Another 134 cases passed UBSan.
- Original and generated CUDA are compiled by the CPU compiler with a limited CUDA shim. This is not nvcc or NVIDIA execution.
- 7 Metal host-generator regression tests passed; previous qpel examples' source/plan hashes are unchanged.
- Generated Metal shader bytes match the previously GPU-tested manual threshold model. Actual execution of this imported package remains pending.

## Real Apple GPU test

```sh
sh <outputs>/qpel-cuda-import/run_metal.sh
```

The runner compares 134 GPU cases to NumPy and saves gpu-report.json with shader, host, plan and original source hashes. CPU oracle transfers are test-only; CPU fallback is disabled.

## Use the importer

From the existing IJ-MPS-test-Project root, inspect a source without changing MPS:

```sh
python3 portable/cuda_import.py portable/cuda_examples/threshold.cu
```

With the local MPS MCP server enabled, import to a NEW root name:

```sh
python3 portable/import_cuda_mps.py portable/cuda_examples/threshold.cu --project "$PWD" --module another_threshold --report /tmp/another-threshold-import.json
python3 portable/build_backends.py --source languages/qpel.sandbox/source_gen/qpel/sandbox/another_threshold.qpel.h --out portable/generated-another-threshold
```

The endpoint defaults to localhost:64342/stream and can be set with --endpoint. The model defaults to qpel.sandbox. --report must be a new path; review an existing journal before retrying a failed import.

Tests: `python3 portable/tests/test_cuda_import.py`. Frozen CPU parity test: `python3 outputs/qpel-cuda-import/run_cpu.py` from the Codex task directory.

## Mutation and preservation checks

A second imported model, cuda_classify, uses reordered arguments, renamed symbols, a different comparison, branch values 7/3, and block size 32. Original-source/generated-source parity passed 402 CPU and 134 UBSan cases. Rejected unsupported/duplicate imports left all model exports unchanged. Evidence and a combined two-package GPU runner are in outputs/qpel-cuda-classify and outputs/qpel-cuda-import/run_all_metal.sh in the Codex task.

## Bitwise mask example

cuda_examples/combine_masks.cu imports to cuda_masks. It exercises &, ^, | and the uint32 high bit. qpel checks numeric operands; literals at or above 2^31 retain an explicit unsigned suffix in emitted code. CPU parity and native regression tests passed; the imported packages GPU checks remain pending.

## Scalar count size parameters

An unsigned int count can replace uint3, with the required guard index >= count. It normalizes to qpel shape volume; pass (count, 1, 1) for that shape in the generated host. This is explicit host ABI adaptation, not general scalar-parameter support. Count in output expressions and multiple/mixed size arguments are rejected. The cuda_masked_copy model and outputs/qpel-cuda-count contain the working example and CPU parity evidence.
