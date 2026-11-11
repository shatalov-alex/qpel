# Importing CUDA and Metal into qpel

`qpel.py import` reads ordinary CUDA (`.cu`) or Metal Shading Language (`.metal`) source and writes a qpel program (`program.qpel.json`). `qpel.py translate` also builds the CUDA and Metal package from it, so a CUDA kernel comes out as Metal and the other way round. Neither needs the MPS IDE, a CUDA SDK or a GPU.

```sh
python3 portable/qpel.py import portable/cuda_examples/threshold.cu --out /tmp/threshold.qpel.json
python3 portable/qpel.py translate portable/metal_examples/threshold.metal --out /tmp/threshold-pkg --block 32
python3 portable/qpel.py import kernels.cu --plan pipeline.json --out program.qpel.json
```

## How it works

clang parses the source and dumps its AST as JSON (`frontends/clang_ast.py`). CUDA is parsed host-only against the small stub headers in `frontends/include/` (the stubs only declare names; no code is generated from them). Metal is parsed by `xcrun metal`. Macros and includes are therefore expanded exactly as the real compilers would.

One mapper (`frontends/c_import.py`) turns that AST into qpel IR for both languages. The language differences sit in two small dialect tables: how a kernel is marked (`__global__` / `kernel`), where a buffer binding comes from (parameter position / `[[buffer(n)]]`), how the thread index is spelled, and the atomic calls. The finished program then goes through the normal qpel checks (types, scopes, pipeline effects, constant bounds), which make the final decision.

Anything without a qpel equivalent is rejected with its location and a reason code, for example:

```
kernels.cu:14:9: [for-loop] for statements are not in qpel yet
kernels.cu:21:30: [do-loop] do statements are not in qpel yet
```

## What is accepted

- Any number of kernels and device helper functions in one file; helpers must come before their callers.
- The thread index `blockIdx.a * blockDim.a + threadIdx.a` for each axis a = x, y, z (operands in any order, in a local or inline), or a Metal `[[thread_position_in_grid]]` parameter of type `uint`, `uint2` or `uint3`. A kernel that uses only the x index stays 1D; one that uses y or z, or a `uint2`/`uint3` position, becomes a 2D/3D kernel over its `uint3` shape. Without a launch plan its block is 16 × 16 × 1.
- Buffers of `unsigned char` (read-only), `unsigned int`, `int`, `float` and `half`/`__half`; a `uint3` shape, either by value or as `constant uint3&`.
- CUDA atomic buffers are plain `unsigned int*`. A buffer counts as atomic when an atomic call uses it, either directly or through a helper, or through another kernel that shares its resource. `atomicAdd(p, 0u)` becomes an atomic load. Metal atomics must use `memory_order_relaxed`.
- `if`/`else`, `while`, `for`, `break`, `continue`, `return`, locals with an initializer, `=`, the compound assignments (computed in C's type and converted back, as C does) and `++`/`--` on 32-bit integers.
- `unsigned int` and `int` arithmetic, comparison, logical, bitwise, shift and `~` operators; `?:`; `min`/`max`; `fmaf`/`__fmaf_rn`/`fma`; float/half conversions; int/unsigned conversions, both implicit and explicit. Signed arithmetic gets qpel's defined wrapping (NUMERICS.md).
- C promotes `unsigned char` to `int`. When such a value provably can't go negative (a comparison of two bytes, `&`/`|`/`^` of bytes), it stays unsigned, so byte kernels don't turn into signed code.
- Programs that need `else`, `for`, `int`, shifts or `~` are written as IR version 2; all others stay version 1.

- Scalar kernel arguments of type `unsigned int`, `int` and `float` (in Metal, `constant T&` parameters) stay ordinary arguments; the hosts take them as `values=`.
- CUDA host functions (neither `__global__` nor `__device__`) are skipped and listed as host code; kernel launches in them parse but are not imported. Namespaces are flattened (function names must still be unique), which is listed as an adaptation.

Rejected for now, each with its own code: templates, atomics on anything but `unsigned int`, `do`/`switch`/`goto`, `double`, float-to-integer conversion, narrowing to `unsigned char`, raw `threadIdx`/`blockIdx`/`blockDim` outside the global index (block-local indices), 2D/3D kernels sized by scalar width/height arguments instead of a `uint3`, shared memory and barriers in user code, other scalar argument types, and buffers of other types.

## Adaptations and warnings

`import-report.json` (in a translated package) and the `import` command's output list:

- **adaptations**: a kernel without a `uint3` shape is sized by the `unsigned int` that its thread index is compared with (`if (i >= n) return;`, `if (i < n) {...}`). That one argument becomes a qpel shape, and the generated hosts take `(n, 1, 1)` for it instead of the plain number. This is a host ABI change; all other scalar arguments keep their meaning.
- **warnings**: kernels with a thread-index axis that is never compared against anything. CUDA rounds the grid up to whole blocks, so such a kernel also runs for indices past the end of its buffers.

## Launch plan

Source files do not say how kernels are launched. Without `--plan`, each kernel becomes one dispatch in source order with `--block` threads per group. Parameters with the same name share one resource, and the single shape parameter is the grid. With `--plan`, the given `pipeline.json` (the format qpel writes) is used instead. Buffers that a stage reads before any stage writes them must be inputs, so an accumulator needs an initialising kernel first, as in the `components` fixture.

## Real-source corpus and coverage

`corpus/` holds unmodified third-party GPU sources with their licences, listed in `corpus/corpus.json` with origin and SHA-256. Header stubs in `corpus/include/` (PyTorch, ATen, pybind11) only let clang parse them. `python3 -m frontends.corpus` reads each one in survey mode: the importer records every blocker instead of stopping at the first, keeps reading inside a rejected statement, and reports a parameter or local without a qpel type once rather than at every use. A kernel is importable when it and the device functions it calls have no blockers.

`corpus/coverage.json` records the result per kernel (`code at function:line`). `tests/test_corpus.py` fails when it changes, so a new feature shows up as kernels becoming importable, and a regression cannot slip through; after checking the change, run `python3 -m frontends.corpus --update`. `--details` lists every blocker.

First entry, 2026-11-11: SAM 2 `sam2/csrc/connected_components.cu` (commit 2b90b9f), 0 of 6 kernels importable:

| Blocker | Kernels | Where |
|---|---|---|
| `no-extent` | 6 | every kernel is 2D and sized by scalar `W`, `H` arguments, not a `uint3` |
| `atomic-type` | 2 | `atomicMin` on `int32_t` labels (`union_`), `atomicAdd` on `int32_t` counts |
| `do-loop` | 1 | `do { ... } while (!done)` in `union_` |
| `uninitialized-local` | 1 | `bool done;` in `union_` |
| `template` | 1 | `hasBit<T>` |
| `buffer-type` | 1 | `merge` takes a non-const `uint8_t*` image it only reads |

It needs no shared memory or barriers. Scalar `W`/`H` sizing alone would make 4 of the 6 kernels importable; i32 atomics a fifth. The original also launches one thread per 2 × 2 pixel block. qpel's shape-sized grid would run 4 times as many threads, which the kernels' own guards make harmless but wasteful. Launch geometry lives in host code, which is the host-layer part of the roadmap.

## Round-trip matrix

`python3 -m frontends.roundtrip` (also part of `check_all.py` and the unit tests) checks:

| Row | Cases |
|---|---|
| qpel → CUDA → qpel | all 12 fixtures |
| qpel → Metal → qpel | all 12 fixtures |
| CUDA → qpel | `cuda_examples/*.cu` must import to their fixture |
| Metal → qpel | `metal_examples/*.metal` must import to the same fixture |

`box_sum` covers 2D/3D kernels: a 3×3 neighbourhood sum over a batch of images, with signed offsets and nested `for` loops, GPU-tested on sizes that are not multiples of the block. `scale_offset` covers scalar arguments: a `float` and an `int` argument next to the element count, GPU-tested with wrapping offsets and the hosts' argument checks. The fifth example, `signed_delta`, is the other version 2 case: signed arithmetic, an `if`/`else if`/`else` chain and a `for` loop with `continue` and `break`. Its fixture was made by importing the CUDA file, so its CUDA row only guards against regressions. The independent checks are the Metal file importing to the same program, both re-imports, nvcc and Metal compiling it, and the Apple GPU test against a NumPy model of C's semantics. Re-importing generated code also reads back qpel's own spellings: wrapping `(int)((unsigned)a + (unsigned)b)`, `qpel_i32_div`/`qpel_i32_rem` and masked shift counts.

Programs are compared as trees, so node ids, literal suffixes and float spelling don't count. CUDA → Metal and Metal → CUDA follow from these rows. A source that imports to a fixture's IR produces exactly that fixture's CUDA and Metal, and those are covered by the nvcc, Metal and Apple GPU checks.

## History

The first CUDA importer (September 2026) was a hand-written parser for one fixed kernel shape. It imported into a running MPS IDE over MCP. The clang importer replaces both, and imports the same four examples to the same programs.
