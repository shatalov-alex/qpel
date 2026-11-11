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
- The 1D thread index `blockIdx.x * blockDim.x + threadIdx.x` (operands in any order, in a local or inline), or a Metal `[[thread_position_in_grid]]` parameter.
- Buffers of `unsigned char` (read-only), `unsigned int`, `int`, `float` and `half`/`__half`; a `uint3` shape, either by value or as `constant uint3&`.
- CUDA atomic buffers are plain `unsigned int*`. A buffer counts as atomic when an atomic call uses it, either directly or through a helper, or through another kernel that shares its resource. `atomicAdd(p, 0u)` becomes an atomic load. Metal atomics must use `memory_order_relaxed`.
- `if`/`else`, `while`, `for`, `break`, `continue`, `return`, locals with an initializer, `=`, the compound assignments (computed in C's type and converted back, as C does) and `++`/`--` on 32-bit integers.
- `unsigned int` and `int` arithmetic, comparison, logical, bitwise, shift and `~` operators; `?:`; `min`/`max`; `fmaf`/`__fmaf_rn`/`fma`; float/half conversions; int/unsigned conversions, both implicit and explicit. Signed arithmetic gets qpel's defined wrapping (NUMERICS.md).
- C promotes `unsigned char` to `int`. When such a value provably can't go negative (a comparison of two bytes, `&`/`|`/`^` of bytes), it stays unsigned, so byte kernels don't turn into signed code.
- Programs that need `else`, `for`, `int`, shifts or `~` are written as IR version 2; all others stay version 1.

Rejected for now, each with its own code: `do`/`switch`/`goto`, `double`, float-to-integer conversion, narrowing to `unsigned char`, 2D/3D thread indices, shared memory and barriers in user code, scalar kernel arguments other than one element count, and buffers of other types.

## Adaptations and warnings

`import-report.json` (in a translated package) and the `import` command's output list:

- **adaptations**: a kernel's single `unsigned int` element count becomes a qpel shape. The generated hosts then take the shape `(count, 1, 1)` instead of the original scalar argument. This is a host ABI change.
- **warnings**: kernels that never compare the thread index against anything. CUDA rounds the grid up to whole blocks, so such a kernel also runs for indices past the end of its buffers.

## Launch plan

Source files do not say how kernels are launched. Without `--plan`, each kernel becomes one dispatch in source order with `--block` threads per group. Parameters with the same name share one resource, and the single shape parameter is the grid. With `--plan`, the given `pipeline.json` (the format qpel writes) is used instead. Buffers that a stage reads before any stage writes them must be inputs, so an accumulator needs an initialising kernel first, as in the `components` fixture.

## Round-trip matrix

`python3 -m frontends.roundtrip` (also part of `check_all.py` and the unit tests) checks:

| Row | Cases |
|---|---|
| qpel → CUDA → qpel | all 12 fixtures |
| qpel → Metal → qpel | all 12 fixtures |
| CUDA → qpel | `cuda_examples/*.cu` must import to their fixture |
| Metal → qpel | `metal_examples/*.metal` must import to the same fixture |

The fifth example, `signed_delta`, is the version 2 case: signed arithmetic, an `if`/`else if`/`else` chain and a `for` loop with `continue` and `break`. Its fixture was made by importing the CUDA file, so its CUDA row only guards against regressions. The independent checks are the Metal file importing to the same program, both re-imports, nvcc and Metal compiling it, and the Apple GPU test against a NumPy model of C's semantics. Re-importing generated code also reads back qpel's own spellings: wrapping `(int)((unsigned)a + (unsigned)b)`, `qpel_i32_div`/`qpel_i32_rem` and masked shift counts.

Programs are compared as trees, so node ids, literal suffixes and float spelling don't count. CUDA → Metal and Metal → CUDA follow from these rows. A source that imports to a fixture's IR produces exactly that fixture's CUDA and Metal, and those are covered by the nvcc, Metal and Apple GPU checks.

## History

The first CUDA importer (September 2026) was a hand-written parser for one fixed kernel shape. It imported into a running MPS IDE over MCP. The clang importer replaces both, and imports the same four examples to the same programs.
