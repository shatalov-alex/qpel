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
- Template functions used with several sets of arguments: each instantiation becomes its own function, named by Metal's `[[host_name("...")]]` or by its arguments (`unary_op_kernel<op_abs, __half>` → `unary_op_kernel_op_abs_f16`); the report keeps the original spelling for calling it. Non-type template parameters read as the value they were replaced by, and a function passed as a template argument is called directly.
- Device functions defined in the library's own headers (llama.cpp's `common.cuh`) are imported when the file's GPU code reaches them; system headers never are. The report lists them as an adaptation.
- Raw thread builtins: a kernel that reads CUDA's `threadIdx`, `blockIdx`, `blockDim` or `gridDim` outside the global index, or takes Metal's threadgroup position, thread position in the group, group size or group count, becomes a `Groups` kernel (NUMERICS.md, launch geometry). Its size arguments stay arguments, and inside it the global index is read literally (group × size + local), so CUDA and Metal spellings import to the same program. Without a plan, buffers are sized by a shape `size` and the kernel runs one thread per element of it in groups of 64.
- CUDA's `__fadd_rn`/`__fsub_rn`/`__fmul_rn`/`__fdiv_rn` are qpel's ordinary float operators (f32-strict-v1).
- C names that qpel or a backend reserves (`min`, `max`, `half`, `kernel`, ...) get a trailing `_`, listed as an adaptation.
- Struct arguments of scalar fields (ggml's `constant ggml_metal_kargs_* & args`, or a struct by value in CUDA) and their fields; pointer locals into device buffers, pointer casts between element types (`View`), and pointer arithmetic; Metal function constants, given with `import_source(..., constants={...})`, with the branches they decide folded; program-scope scalar constants (`constant float SQRT_2_OVER_PI = ...`), read as their value; overloaded functions, each its own function; Metal's `clamp` and `select`; attributed statements (`[[unroll]]`). Program-scope arrays (lookup tables) are an error only where used.
- `import_source(..., kernels=[...])` (and the corpus programs) imports only the named kernels and the functions they reach, so each instantiation of a library operation is its own program.
- Math functions on `float`: `fabsf`/`fabs`/Metal `abs`, `fminf`, `fmaxf`, `floorf`, `ceilf`, `truncf`, `roundf`, `copysignf`, `sqrtf`/`__fsqrt_rn`, `expf`, `exp2f`, `logf`, `log2f`, `sinf`, `cosf`, `tanhf`, and the same names without `f` (NUMERICS.md, math functions). Conversions written as constructors (`__half(f)`), `bool` to numbers, `__half` scalar arguments, and element counts of any integer type (`int k`).
- IR version 3 (NUMERICS.md): `signed char`/`char`, `unsigned short`/`short`, `long long`/`int64_t`/`long`, `unsigned long long`/`uint64_t`/`size_t` (Metal `ushort`, `long`, `ulong`), `__nv_bfloat16`/`bfloat`, as locals, arguments and buffers of any of them, writable `unsigned char` buffers, and float-to-integer conversions (which qpel saturates). C's conversions between integer types become explicit `Cast` nodes, except widenings that keep signedness, which change nothing qpel computes. A chain of casts is converted one step at a time, so `(long long)(unsigned char)x` keeps the truncation to 8 bits. Integer constant expressions of the new types (`-5ll`, `(short)(-32767 - 1)`) become one `Constant`. Re-importing generated code reads back qpel's own spellings at every width: wrapping `(long long)((unsigned long long)a op (unsigned long long)b)`, `qpel_i64_div`/`qpel_i64_rem`, shift counts masked with 63, `qpel_f32_to_*`.

- Scalar kernel arguments of type `unsigned int`, `int` and `float` (in Metal, `constant T&` parameters) stay ordinary arguments; the hosts take them as `values=`.
- IR version 4: local arrays of scalars (`uint32_t point[3] = {...}`, Metal `uint v[3]`), indexed by any integer expression, including as assignment targets (`point[i] ^= t`). Elements an initializer list leaves out are zero; an array without an initializer starts at zero (an adaptation). An array used other than by indexing (passed to a function, decayed to a pointer) is rejected (`array-use`).
- Vectors (`int2`-`int4`, `uint2`, `uint4`, `float2`-`float4`; `uint3` stays a shape): locals, helper parameters (by value or `const&`) and results become `vec<T, N>`, built from lists, `make_float3(...)` or Metal `float3(...)`. Non-const reference parameters become `ref` parameters; a kernel argument cannot be one. A vector is read from a kernel buffer of its element type (`reinterpret_cast<const int4*>(coords)[i]` reads `coords[4*i]` to `coords[4*i + 3]`, and the plan sizes `coords` as `in M * 4`), built with `make_int4(...)` or a full list, or starts at zero. Other vector uses are rejected (`vector-init`).
- Compare-and-swap: CUDA's `atomicCAS` on `unsigned int*` and `int*` (the buffer becomes atomic), returning the value found; `atomicCAS` on 64-bit values is rejected as not portable (Metal has none). `std::numeric_limits<T>::max()`, `min()` and `lowest()` of an integer type become constants. `import_source(..., resources={'table': 'inout'})` gives a resource of the plan made without one another kind, for a buffer the caller fills and the kernels update in place.
- PyTorch extensions: `frontends/include/` has declaration-only stand-ins for the CUDA runtime and driver API and `cooperative_groups.h`, and `frontends/include/libraries/` for `torch/extension.h`, ATen, c10, pybind11 and cuBLAS (kept apart, so the CPU simulator and a real CUDA compiler can use them next to their own CUDA headers), so an extension's `.cu` files parse offline; its host functions are skipped as usual. `cooperative_groups::this_grid().thread_rank()` is the x thread index of a 1D kernel (refused in 2D/3D and Groups kernels). Namespace aliases and `using` declarations are skipped. Kernels defined out of line in a namespace (`__global__ void CUDA::k(...)`) keep their qualified name in the report, for calling the original.
- CUDA host functions (neither `__global__` nor `__device__`) are skipped and listed as host code; kernel launches in them parse but are not imported. Namespaces are flattened (function names must still be unique), which is listed as an adaptation.
- A function template used with one set of types is imported as that one function. `do ... while`, atomics on `int` (`atomicAdd`/`atomicMin`/`atomicExch` on `int*`, Metal `atomic_int`), conversions to `unsigned char` (low 8 bits), and locals declared without a value.
- 2D/3D kernels sized by scalar arguments: when each thread-index axis (or a local computed from one axis, such as `row = (blockIdx.y * blockDim.y + threadIdx.y) * 2`) is compared with exactly one `unsigned int` or `int` argument, those arguments become one shape `(W, H, 1)` and their uses read its members. Like the 1D count, this changes the host interface.
- Buffers of different sizes (PyTorch extensions): a 1D kernel's integer argument that the original's launch passes as `t.size(0)` or `t.numel()`, next to `t.data_ptr<T>()` for one of its buffers, is that buffer's length. It becomes a second shape `(N, 1, 1)` that sizes the buffer, and its uses read the shape's volume, as the 1D count does. A buffer the launch does not size takes the size of the buffers it is indexed with by the same local, through helper calls (TRELLIS.2's `hashmap_values[slot]` next to `hashmap_keys[slot]`); a buffer indexed by the thread index or by nothing it shares is sized by the grid. A buffer indexed together with buffers of two sizes is refused; pass a launch plan. Every size is checked by running: the CPU simulator puts an inaccessible page right after each buffer, so an access past the end stops the run.

Rejected for now, each with its own code: atomics on anything but `unsigned int` and `int`, `+=` or `++` on an element other threads update atomically, `switch`/`goto`, `double`, conversions involving `bool` and floats, thread builtins in device helpers, Metal's flat `thread_index_in_threadgroup` and simdgroup builtins, shared memory and barriers in user code, pointer locals, structs, vectors, and buffers of `double`, `void` or structs.

## Adaptations and warnings

`import-report.json` (in a translated package) and the `import` command's output list:

- **adaptations**: a kernel without a `uint3` shape is sized by the `unsigned int` that its thread index is compared with (`if (i >= n) return;`, `if (i < n) {...}`). That one argument becomes a qpel shape, and the generated hosts take `(n, 1, 1)` for it instead of the plain number. This is a host ABI change; all other scalar arguments keep their meaning.
- **adaptations** also list: plain reads and writes of a buffer that is updated atomically somewhere (CUDA allows this; qpel makes them relaxed atomic loads and stores), a non-const `unsigned char*` that is never written (read-only), a local without a value (starts at zero; reading it first is undefined in C), a second local with an existing name (renamed `name_2`), flattened namespaces, single-use templates, and an atomic accumulator that is updated before anything writes it (a `zeroed` resource, when there is no plan).
- **warnings**: kernels with a thread-index axis that is never compared against anything (directly, or through a local computed from it). CUDA rounds the grid up to whole blocks, so such a kernel also runs for indices past the end of its buffers.

## Launch plan

Source files do not say how kernels are launched. Without `--plan`, each kernel becomes one dispatch in source order with `--block` threads per group. Parameters with the same name share one resource, and the single shape parameter is the grid. With `--plan`, the given `pipeline.json` (the format qpel writes) is used instead. Buffers that a stage reads before any stage writes them must be inputs, so an accumulator needs an initialising kernel first, as in the `components` fixture.

## Real-source corpus and coverage

`corpus/` holds unmodified third-party GPU sources with their licences, listed in `corpus/corpus.json` with origin and SHA-256. The importer's declaration-only stand-ins (`frontends/include/`: CUDA, cooperative groups; `frontends/include/libraries/`: PyTorch, ATen, c10, pybind11, cuBLAS) only let clang parse them. `python3 -m frontends.corpus` reads each one in survey mode: the importer records every blocker instead of stopping at the first, keeps reading inside a rejected statement, and reports a parameter or local without a qpel type once rather than at every use. A kernel is importable when it and the device functions it calls have no blockers.

`corpus/coverage.json` records the blocker codes of every kernel (`--details` gives each blocker as `code at function:line`). `python3 -m frontends.corpus --check` (run by `check_all.py`) fails when it changes, and `tests/test_corpus.py` pins the SAM 2 part, so a new feature shows up as kernels becoming importable, and a regression cannot slip through; after checking the change, run `python3 -m frontends.corpus --update`. `--details` lists every blocker; `--messages` ranks blocker messages over the whole corpus. Sources are surveyed in parallel. In a survey, a template used with several types is reported once and its first instantiation is read too, so its other blockers show; kernels are read independently (parameters of different kernels are not linked by name).

clang's JSON AST is filtered while it streams in: declarations from system headers (libc++, the macOS SDK, the Metal toolchain) are dropped, keeping the file and line state clang's relative locations need. A llama.cpp file produces ~600 MB of AST JSON, nearly all of it libc++.

Metal corpus programs (2026-11-11): `kernel_arange_f32` and `kernel_unary_f32_f32` specialised by its function constants to 20 operations (scale, fill, clamp, sqr, sqrt, sin, log, leaky_relu, tanh, relu, sigmoid, gelu, silu, neg, abs, step, exp, softplus, floor, round) plus `kernel_unary_f16_f16` for scale, gelu and silu, from the unmodified `ggml-metal.metal`. Programs can give the function constants, the original's launch geometry (`geometry`), test sizes (`shapes`), the element type held in byte buffers (`inputs`, `outputs`) and argument rules that describe a consistent tensor (byte strides from the shape). All 24 pass both rows: the original on the Apple GPU against qpel's CUDA (simulator) and qpel's Metal.

**Corpus programs.** A corpus entry can pin single kernels as programs (`programs` in `corpus.json`): each is imported on its own (`kernels=`), written as fixture `llama_<kernel>` (`python3 -m frontends.corpus --fixtures`), and from then on must import to exactly that fixture (a round-trip row), survive qpel → CUDA/Metal → qpel, and pass the behaviour rows against the unmodified original, with argument rules in `values` (differential/README.md). The Apple GPU suite runs their Metal rows. On 2026-11-11, 28 llama.cpp operations are pinned this way and all pass: `arange_f32`, `scale_f32`, `softcap_f32`, `fill` and `clamp` in f32 and f16, and from `unary.cu` abs, sgn, neg, step, gelu (also f16), silu, tanh, relu, sigmoid, hardswish, exp, sqrt, sin, log, round (also f16), softplus, gated silu, leaky_relu and swiglu_oai.

Second entry (2026-11-11): a slice of llama.cpp, commit 91d2fc3 (MIT): ten CUDA files and `ggml-metal.metal`, with the headers they include, unmodified under `corpus/llama.cpp/` and pinned by `SHA256SUMS` (checked by a unit test). The CUDA SDK, cuBLAS and bf16 headers are declaration-only stubs (`frontends/include/`, `corpus/include/`). It started at 0 of its 123 kernels; with template instantiations counted separately the corpus has 691 kernels, of which 78 import on 2026-11-11 (ROADMAP.md, Phase 2, ranks what blocks the rest).

Third entry (2026-11-12): Microsoft TRELLIS.2's `o-voxel` extension, commit 75fbf01 (MIT), the Phase 3 pilot: its serialisation (`z_order.cu`, `hilbert.cu`), hash-table (`hash.cu`) and voxel-rasterisation (`rasterize.cu`) kernels with their headers, under `corpus/trellis2/` and pinned by `SHA256SUMS`. The four serialisation kernels are corpus programs (`trellis2_z_order_encode`, `..._decode`, `trellis2_hilbert_encode`, `..._decode`) and pass both behaviour rows against the unmodified originals; they needed cooperative-groups thread ranks, out-of-line namespace definitions and local arrays (IR version 4). Six hash-table kernels are programs too: insertion with 32-bit keys (`trellis2_hashmap_insert_u32_u32`, `..._u32_u64`, with the table `inout`) and lookup with every key and value width. Their cases (differential/cases.py) give insertion distinct keys in distinct slots, so the table is the same in any thread order, and lookup a table built by inserting one key at a time. All pass both rows; insertion with 64-bit keys is rejected (Metal has no 64-bit compare-and-swap). The 3D hash variants (`trellis2_hash3d_insert_*`, `..._insert_idx_*`, `..._lookup_*`, eight programs) read `int4` coordinates from an `[M, 4]` tensor and pass both rows with tables larger than their key lists; the rasteriser still needs vector arithmetic, structs and shared memory (ROADMAP.md, Phase 3).

First entry: SAM 2 `sam2/csrc/connected_components.cu` (commit 2b90b9f). On 2026-11-11 it started at 0 of 6 kernels importable. The blockers were scalar `W`/`H` sizing (all 6), `int32_t` atomics, `do`/`while`, an uninitialized `bool`, the `hasBit<T>` template and a read-only `uint8_t*` image. The same day all six import, with the adaptations listed above. The file never needed shared memory or barriers.

`corpus/sam2/connected_components.plan.json` is the launch plan transcribed from the original host function (`get_connected_componnets`): `img` input, `label` and `count_final` outputs, `count_init` zeroed (`torch::zeros`), six stages in its order with 16 × 16 blocks. Imported with it, the file must give the `sam2_cc` fixture exactly (a row of the round-trip matrix). That fixture compiles with nvcc and Metal and passes 65 cases on the Apple GPU against an independent NumPy model of the original's labelling (8-connected components; label = 1 + smallest 2 × 2 block index in the component; count = area).

The original launches one thread per 2 × 2 pixel block and loops over the batch on the host. qpel runs one thread per pixel of a `(W, H, 1)` shape. Threads past the original's grid fail the kernels' own guards, so the result is the same, but 4 times as many threads are launched. Launch geometry belongs to the host layer (Phase 7).

## Fuzzing

`python3 -m fuzz --count N --seed S` generates random well-typed programs (`fuzz/programs.py`: integer arithmetic at every width, shifts, division by values made odd, comparisons and selects, conversions in every direction, exact math) as qpel text and requires, for each: text that prints back identically; qpel → CUDA → qpel and qpel → Metal → qpel giving the same program; and identical outputs from the CUDA on the CPU simulator and the Metal on the Apple GPU. `fuzz/regressions/` keeps every program it once failed on; they run first. `check_all.py` runs 25 programs with seed 1.

## Behaviour rows

`python3 -m differential.matrix` runs every original (the example pairs, SAM 2 and its Metal port) and qpel's CUDA and Metal for it on the same cases, and requires identical outputs: CUDA on a CPU simulator, Metal on the Apple GPU. See `differential/README.md`.

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
