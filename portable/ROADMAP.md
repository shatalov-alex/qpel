# qpel roadmap

qpel is a neutral intermediate language between NVIDIA CUDA and Apple Metal: kernels are imported from either platform into a typed IR and generated for both, so that applications and libraries can move in either direction. This file records the design decisions behind it, the phases of work, and where each phase stands. [RELEASE.md](RELEASE.md) remains the release-readiness checklist.

## Where things stand (2026-11-12)

| Phase | Scope | State |
|---|---|---|
| 0 | Consolidation: one compiler, IR as the source of truth | done 2026-10-19 |
| 1 | Offline Clang-based CUDA and Metal importers, round-trip matrix | done 2026-11-11 |
| 2 | IR v3, real-world corpus (llama.cpp), text syntax, fuzzing, NVIDIA gate | done 2026-11-12 (GPGPU-Sim and a Tesla T4) |
| 3 | Shared memory, barriers, warp/simdgroup ops, matrix tiles, benchmarks, test enrichment | next |
| 4 | Host API and library mapping, Triton frontend | planned |

## 1. Design decisions

qpel was first designed in JetBrains MPS 2026.1, a projectional language workbench (unrelated to Apple's Metal Performance Shaders; these docs always say "JetBrains MPS" and "Metal"). MPS was quick for designing the language: concepts, an editor, checks while editing and text output went from nothing to working in a day, and it cannot produce syntactically invalid programs. It is the wrong place for the compiler and the source of truth, for three reasons:

- **Migration means reading existing source.** CUDA C++, Metal Shading Language and host C++ are text, and real codebases are large. A projectional editor is awkward to parse them into; the first CUDA importer had to drive a running IDE.
- **A compiler has to run headless,** in CI and at scale.
- **The GPU tooling ecosystem is elsewhere:** Clang's CUDA frontend, MLIR (gpu, nvvm, spirv dialects), SPIR-V with SPIRV-Cross, Slang. MPS can reuse none of it.

So:

- The **qpel IR** (`.qpel.json`, [CORE.md](CORE.md)) is the single source of truth, and the Python core is the reference compiler.
- **JetBrains MPS is an optional authoring frontend** that exports IR. It has no code generation of its own, and new IR versions land in Python first.
- A **text syntax** (`.qpel`, [TEXT.md](TEXT.md)) lets people and tools write qpel without an IDE.
- **Importers use Clang** (its JSON AST, offline) instead of hand-written parsers.
- **Numerics are specified, not inherited** from either platform ([NUMERICS.md](NUMERICS.md)): defined integer wrapping, explicit casts, FMA distinct from `a*b + c`, separately rounded float32, f16/bf16 for storage with float32 arithmetic, stated bounds for transcendentals. Every rule is checked by running the same program on both platforms.
- Longer term, lowering qpel IR to **MLIR** would connect it to existing optimisation passes and to SPIR-V/Vulkan as a third backend.

## 2. Starting point (2026-10-19)

The first version generated Metal and CUDA from MPS-authored programs, and SAM 2's connected-components kernel already ran on an Apple M4 Pro with a 0-pixel difference in SAM 2.1 Tiny's output. Backend differences lived in two macro headers (`backends/cuda.h`, `backends/metal.h`), an approach that has held up. The gaps at that point, and what closed them:

| Gap | Closed by |
|---|---|
| Not bidirectional: no Metal importer, and the CUDA importer accepted one fixed kernel shape | Phase 1: Clang-based CUDA and Metal importers |
| CUDA output never compiled with nvcc or executed | Phase 0 `nvcc_check.py`; Phase 2 NVIDIA gate (GPGPU-Sim, Tesla T4) |
| MPS TextGen and the Python lowering produced different code | Phase 0: MPS exports IR only |
| IR shaped after MPS concepts, with a fixed set of combined types | Phase 2: IR v3 type terms (`buffer<T, access>`, `u8 … i64`, `bf16`) |
| Narrow language: 1D grid, no `else`/`for`, no signed or 64-bit integers | Phase 1 (IR v2, 2D/3D) and Phase 2 (IR v3, `Groups` geometry) |
| Duplicated validation tables, backend features found by string search | Phase 0 `qpel_schema.py`; Phase 2 declared features |
| No shared memory, barriers, subgroup ops, vectors or tiles | Phase 3 |
| Python-only hosts (CuPy, torch.mps), nothing measured | Phase 3 benchmarks, Phase 4 host IR |
| No host-API or library mapping (streams, cuBLAS, cuDNN, CUB → Metal, MPSGraph, Accelerate), which is where most migration effort goes | Phase 4 |

## 3. Roadmap

### Phase 0: consolidate (≈1 week)
- Declare `.qpel.json` the source of truth. ~~Regenerate `portable/generated/`~~ done 2026-10-19: `regenerate.py` rebuilds it from the components IR fixture (u32-modulo-v1, with `cuda_host.py`, relative manifest paths). The CPU host test still passes all 133 cases; The components kernel passes all 133 cases on the Apple GPU; full SAM 2 model inference has not been re-run.
- ~~Metal compile check~~ done 2026-10-19: `metal_check.py` compiles all 12 fixtures plus the checked-in package with `xcrun metal -Werror`.
- ~~Turn MPS TextGen into a thin "export IR" step.~~ Done: MPS emits only `<pipeline>.qpel.json`; all code-generating TextGen and the token cross-check are gone.
- ~~git, `requirements.txt`, one check script~~ done 2026-10-19: repo initialised; `check_all.py` runs unit tests, Metal and nvcc compile checks and (with `--gpu`) the Apple GPU tests. Hosted CI: `.github/workflows/ci.yml` (Linux unit tests, CUDA corpus, CPU rows, fuzzing, nvcc) is in the repository but disabled for now; every check runs locally, including on Linux in a container.
- ~~Hardcoded paths and GPU runners~~ done 2026-10-19: `gpu_tests/run_gpu_tests.py` replaces the earlier out-of-tree runners and the unrunnable `tests/*_gpu.py` / `*_parity.cpp` copies; it builds each fixture fresh and passes all 12 on the Apple GPU. The removed CUDA-vs-original parity checks return as the Phase 1 round-trip matrix.
- ~~Deduplicate identifier and block-size rules~~ done 2026-10-19: one `is_identifier`/`is_block_size` in `qpel_schema.py`. This fixed a bug: names such as `constant`, `threadgroup`, `class` or `switch` passed validation and then broke the generated Metal or CUDA. The MPS-side `QpelChecks` still has the old short list. (The host dtype and macro type tables map to different things and stay separate.)
- ~~Workgroup and local indices~~ superseded: 2D/3D kernels (`Grid3D`, `ThreadIndex`, Phase 1) and the `Groups` domain (`GroupIndex`, `LocalIndex`, `GroupSize`, `GroupCount`, Phase 2).
- ~~nvcc compile~~ done 2026-10-19: `nvcc_check.py` compiles all 12 fixtures plus the checked-in SAM 2 package with nvcc 12.6/ptxas for sm_75–sm_90 in Docker, warnings as errors, and links `tests/cuda.cu`.
- Deferred until a major milestone (end of Phase 2): NVIDIA *execution* via GPGPU-Sim functional mode in Docker, then one real NVIDIA run (free Colab/Kaggle T4 or any card) for the CuPy host. Until then the nvcc check is the CUDA gate.

### Phase 1: bidirectional importers and the round-trip matrix (3–5 weeks)
qpel exists to sit between CUDA and Metal, so both import directions come first. The IR grows only as far as the importers need it.

**Status 2026-11-11:** importers and the matrix are in (see IMPORT.md). `frontends/` imports CUDA and Metal through clang's JSON AST, offline. The fixed-shape parser and the MCP/IDE import path are removed. The matrix passes for all 12 fixtures in both qpel → X → qpel rows, and for the four examples in both CUDA → qpel and Metal → qpel. The guard shape and any index operand order work now. IR version 2 (same day) adds `else`, `for`/`break`/`continue`, `i32` with defined wrapping (NUMERICS.md), shifts and `~`, with a GPU-tested `signed_delta` example in both languages. MPS does not have these constructs yet (Python leads; decided 2026-11-11). Scalar kernel arguments followed (`value` resources, `scale_offset` example). 2D/3D kernels followed (`Grid3D`, `ThreadIndex`, 3D blocks; `box_sum` example). The SAM 2 CUDA original is in `corpus/` with a pinned coverage report (`python3 -m frontends.corpus`, IMPORT.md). All 6 of its kernels import unmodified (scalar `W`/`H` sizing, i32 atomics, `do`/`while`, `U8Cast`, zeroed scratch and importer adaptations). The `sam2_cc` fixture passes 65 Apple GPU cases against an independent oracle and compiles with nvcc. The behaviour rows followed (`differential/`): CUDA originals run on a CPU simulator (clang++ with UBSan, sequential schedule), Metal originals on the Apple GPU, and every output of qpel's CUDA and Metal must match them bit for bit. SAM 2 got a hand port to Metal that imports to the same program as the CUDA original. The corpus gained float32, FMA, half and an ordered-sum loop written as ordinary CUDA and Metal.

**Phase 1 closed 2026-11-11.** Every corpus original passes every row that applies to it:

| Corpus | CUDA → qpel, Metal → qpel (same program) | Behaviour rows 1–4 | qpel → X → qpel |
|---|---|---|---|
| 11 hand-written CUDA/Metal pairs (threshold, classify, masks, masked copy, signed delta, scale/offset, box sum, float32, FMA, half, window sum) | all import to their fixture | all four rows, 6 cases each | yes |
| SAM 2 `connected_components.cu` (unmodified) and its Metal port | both import to `sam2_cc` | all four rows, 18 cases each; the original runs with its own launch grids | yes |
| Fixtures with no external original (components, cli, threshold, reduce, workgroup) | n/a | n/a | yes |

The nvcc check compiles every fixture (sm_75–sm_90), the Metal check compiles them, and the Apple GPU suite runs all 17 against independent oracles. One exception from the original Phase 1 corpus list: the workgroup sum has no hand-written original, because one would use `__shared__` memory and `__syncthreads`, which are Phase 3. NVIDIA execution stays deferred to the Phase 2 milestone gate, as decided.

- **CUDA → qpel, offline.** Replace the fixed-shape parser and the live-IDE MCP path with a Clang-based frontend: libclang or `clang -x cuda -Xclang -ast-dump=json`, with `-nocudainc` plus a small header of CUDA declarations. The output is `program.qpel.json` directly. MPS can open it afterwards, but MPS is not in the import path.
- **Metal → qpel.** MSL is based on C++14, so parse it the same way: through `xcrun metal -Xclang -ast-dump=json`, or Clang with a header of Metal declarations. Map `[[buffer(n)]]`, `[[thread_position_in_grid]]`, `threadgroup` memory, `atomic_uint` and `metal::fma`/`min`/`max` onto the existing qpel concepts. Local prerequisite: `xcodebuild -downloadComponent MetalToolchain`, because the Metal compiler is installed but the toolchain isn't downloaded.
- **Report unsupported constructs.** Every construct that is rejected or not portable gets a diagnostic with its source position and a reason code. Examples: dynamic shared memory, cooperative groups, textures, argument buffers, warp intrinsics. Nothing is dropped silently.
- **Minimum IR extensions the importers need:** `else`, `for`, signed `i32`, the `if (i < n) {…}` guard shape, any index-line operand order, scalar (non-buffer) kernel parameters, and 2D/3D grid indices. Each extension gets a version bump and the rules in NUMERICS.md.
- **Round-trip test matrix** (in CI). Every corpus kernel runs through:

  | Path | Requirement |
  |---|---|
  | CUDA → qpel → CUDA | nvcc accepts it (`nvcc_check.py`). Behaviour matches the original on the CPU simulator. |
  | Metal → qpel → Metal | `xcrun metal` accepts it. Behaviour matches the original on the Apple GPU. |
  | CUDA → qpel → Metal | Metal compiles, and outputs match the CUDA original on shared test vectors. |
  | Metal → qpel → CUDA | nvcc compiles, and outputs match the Metal original on shared test vectors. |
  | qpel → X → qpel | Re-importing the generated CUDA or Metal gives back the same qpel graph, modulo normalisation. |

- **Starting corpus:** the four `cuda_examples/`, hand-written MSL equivalents of the same kernels, the SAM 2 connected-components kernel (CUDA original and the Metal port), plus the float32/FMA/half/reduction/workgroup fixtures written out as CUDA and MSL source.
- **Exit criterion:** every corpus kernel passes all five rows of the matrix, and the MCP/IDE import path is retired.

### Phase 2: IR v2, text syntax and real-world coverage (3–6 weeks)
**Status 2026-11-11: survey done, work order set by it.** The corpus now holds a pinned slice of llama.cpp (commit 91d2fc3, MIT, `corpus/llama.cpp/` with `SHA256SUMS`): ten CUDA files (arange, scale, clamp, fill, softcap, unary, diag_mask, argmax, sum_rows, cumsum) and all of `ggml-metal.metal`. clang parses them offline against declaration-only SDK stubs; system-header declarations are dropped while clang's JSON streams in (a llama.cpp file is ~600 MB of AST JSON, nearly all of it libc++). Coverage: **6 of 129 kernels** import (the SAM 2 ones). `python3 -m frontends.corpus --messages` ranks every blocker. Grouped, in order of how many kernels they block:

| Blocker | Where | Needed for |
|---|---|---|
| 64/16/8-bit integers (`int64_t`/`size_t` sizes and strides, `short`/`ushort`, writable `uint8_t`) | both | almost every kernel |
| Byte-addressed buffers and typed row pointers (`device const char* src0`, `(device float*)(src0 + i01*nb01)`, `const float* row = x + i*n`) | Metal (all), CUDA (row kernels) | ggml's tensor layout: strides are in bytes |
| Argument structs (`constant ggml_metal_kargs_* & args`) | Metal | every Metal kernel |
| Templates used with several types; non-type template parameters; Metal function constants | both | unary, binary, getrows, ... |
| Launch geometry beyond one thread per element: `blockIdx.y` rows, `threadIdx.x` loops with `blockDim.x` stride, grid-stride loops, Metal `tgpig`/`tpitg`/`ntg` | both | most kernels |
| Math library (`expf`, `tanhf`, `sqrtf`, `fabs`, `fminf`/`fmaxf`, `sin`/`cos`, `log`, `erf`, `floor`/`round`, `select`) | both | unary ops, norms, softmax |
| Device functions in the library's own headers (`common.cuh`: `ggml_cuda_pdl_sync`, `fastdiv`, `warp_reduce_sum`) | CUDA | most CUDA kernels |
| Threadgroup memory, barriers, `simd_sum`/warp shuffles | both | reductions (Phase 3 core) |
| Vectors (`float4`, `half4`), `float[N]` locals, matrices | Metal | vectorised variants, matmul (Phase 3) |

**Step 1 done 2026-11-11: IR version 3.** Types are terms (`u8 … i64`, `bf16`, `buffer<T, read|write|atomic>`, `shape3`) written with `ScalarType`/`BufferType`; the version 1/2 concept names remain spellings of the same terms, so no fixture or MPS export changed and every earlier package generates byte-identical kernels. `Cast` and `Constant` cover every width; float → integer saturates. Launch plans spell types as terms (schema 2). The `int_widths` fixture checks the rules bit for bit on the CPU simulator and the Apple GPU against a Python-integer oracle and compiles with nvcc and Metal (bf16 needs Metal 3.1). The importers read all the new C types and qpel's own spellings at every width; the corpus no longer has 64-bit-integer blockers, and the next walls are argument structs, pointer locals and launch geometry. Step 2's part on integer widths is done with it.

**Steps 2 and 4 in progress (2026-11-11).** Template instantiations import one by one, and device functions from the library's own headers are imported when reached. The `Groups` domain gives kernels explicit geometry (group/local index, group size and count; group counts as expressions of the shape), launched identically on both backends; size arguments stay arguments. The `grid_stride` CUDA/Metal pair (llama.cpp's grid-stride loop with a 64-bit count) passes all four behaviour rows and the Apple GPU. It also exposed float contraction: a translated `a*b + c` differed by one ulp on Metal, so float32 is now pinned to separately rounded operations (f32-strict-v1).

**Real llama.cpp kernels pass (2026-11-11).** Math functions (exact, and bounded within 4 ulp), signed counts, f16 arguments, constructor conversions and calls through function template arguments bring the corpus to 78 of 691 kernels. 28 llama.cpp operations are pinned as corpus programs and pass every row: unmodified originals on the CPU simulator (with `common.cuh`, host bodies blanked) against qpel's CUDA and qpel's Metal on the Apple GPU. They found two more platform differences, now handled: Metal's `round` drops the sign of a zero result, and Apple GPUs flush subnormal results.

**llama.cpp's Metal side imports (2026-11-11).** Argument structs, buffer views over byte buffers, Metal function constants (specialised at import, branches folded), overloads, program-scope constants, `clamp`/`select` and attributed statements. 24 unmodified `ggml-metal.metal` kernels (arange and 23 specialisations of `kernel_unary`) pass both behaviour rows, next to the 28 CUDA ones. Corpus: 101 of 691 kernels import.

**Step 6 done (2026-11-11): text syntax.** `qpel_text.py` prints any program as text and parses text back to IR; every fixture round-trips (parse(print(p)) = p, print again = same text). `qpel.py` builds `.qpel` files, prints and parses, and imports CUDA/Metal straight to text. TEXT.md describes the syntax; `examples/saxpy.qpel` is written by hand and runs.

**Step 7 done (2026-11-11): declared features** (see the struck-through item below).

**Step 8 done (2026-11-11): differential fuzzing** (`python3 -m fuzz`, a fixed set in `check_all.py`). Random well-typed programs, written as qpel text, must print back identically, survive qpel → CUDA/Metal → qpel, and give the same outputs from their CUDA on the CPU simulator and their Metal on the Apple GPU. It found: an importer bug that dropped i32 → i64 widenings (so `(long long)i * (long long)n` would multiply in 32 bits), a missing feature (comparisons joined in i32), 64-bit literals in narrow and float contexts, and **a Apple Metal compiler bug** in unsigned division by constant dividends near 2^32, now worked around (NUMERICS.md). The round-trip comparison now normalises conversions and constants by meaning (frontends/roundtrip.py). Failing programs are kept as regressions in `fuzz/regressions/`.

**Work order:** (1) IR v3 types as constructors: `i8…u64`, `bool`, `f16`, `bf16`, `f32`, and `buffer<T, read|write|atomic>`, with v1/v2 migration. (2) Importer: header device functions, template instantiations imported one by one, 64-bit and 16-bit integers. (3) Pointers into buffers and byte-addressed buffers, argument structs (flattened), function constants as specialisation constants. (4) Launch geometry in the IR: group id, local id, group size, explicit launch plans. (5) Math functions with a stated accuracy per function (NUMERICS.md), so the behaviour rows compare them within ULP bounds instead of bit for bit. (6) Text syntax. (7) Feature sets per program and backend. (8) Fuzzing. Then the NVIDIA gate. Shared memory and simdgroup reductions stay in Phase 3, which the survey confirms is the next wall for reductions.

- Redesign types as constructors: scalars `u8/u16/u32/u64/i8…i64/f16/bf16/f32`, `vec<T,N>`, `buffer<T, read|write|atomic>`, `shared<T,N>`. Add an IR migration from v1.
- Add `break`/`continue` and explicit `barrier(workgroup)`, and generalise the existing uniformity checks.
- **Text syntax** (`.qpel`) with a parser to IR and a printer from IR that round-trip. This becomes the main way of writing qpel; MPS stays optional.
- ~~Replace substring capability detection with a declared feature set per program and per backend profile (CUDA sm_XX, Metal version).~~ Done 2026-11-11: `Lowering.features()` derives the program's features from its IR, the launch plan and manifest record them, and `build_backends.FEATURE_DEFINES` / `METAL_STANDARD` map them to backend support blocks and the Metal language version. All 144 generated sources stayed byte-identical. No feature needs more than sm_75 on CUDA yet.
- Grow the corpus to real code: llama.cpp `ggml-cuda` and `ggml-metal` element-wise and reduction kernels, and PyTorch MPS custom kernels. Publish a **coverage metric** (% importable, % round-trip-clean) per corpus and direction.
- Add differential fuzzing across backends to the round-trip matrix.
- **Milestone gate:** at the end of this phase, run the deferred NVIDIA execution tests (GPGPU-Sim, then one real card).
  **GPGPU-Sim half done 2026-11-12** (gpgpusim/README.md): every behaviour-matrix source runs as real CUDA on a simulated Turing GPU in Docker (`python3 -m differential.gpgpusim`, `check_all.py --nvidia`). 720 cases pass: the unmodified CUDA originals, and qpel's CUDA from both CUDA and Metal originals, against the CPU-simulator and Apple-GPU references. Fuzzing runs there too (`fuzz --nvidia`). Getting there fixed eleven GPGPU-Sim bugs (`gpgpusim/qpel-fixes.patch`, checked by `selftest.cu`), among them f16 tie rounding, 64-bit immediates, `bfi.b64`/`mad.wide` (CUDA's `sinf` for large arguments returned 0), signed byte loads and the sign of zero in `abs`/`neg`. It also found one qpel importer bug (unsigned literals converted to `int`). qpel's generated code needed no change.
  **Real-card half done 2026-11-12; Phase 2 closed.** `differential/hardware.py` builds the same programs for x86-64 here and runs them on a Kaggle Tesla T4 (sm_75): all 720 cases and `gpgpusim/selftest.cu` pass, and the generated CuPy host passes all 20 fixture tests (`gpu_tests/run_gpu_tests.py --device cuda`). No qpel change was needed.

### Phase 3: performance-capable core (4–8 weeks)
- Shared-memory tiling, warp/simdgroup ops (`shuffle`, `ballot`, `simd_sum`), and subgroup size as a queried capability.
- Matrix tiles mapped to WMMA/mma.sync on CUDA and `simdgroup_matrix` on Metal: the minimum for GEMM and attention.
- Atomics beyond u32 (f32 add, i32/u64 where supported), with capability gating.
- Optimisation passes kept separate from portability lowering: constant folding, CSE, vectorised loads.
- A benchmark harness reporting time per kernel against the native original on both platforms.
- **Test enrichment: unit tests beyond SAM 2.** Today the unit tests run behaviour rows only for the hand-written examples and SAM 2; the llama.cpp programs, fuzz regressions and declared features are checked only by `check_all.py`. Move them into `tests/`: one test per corpus program (CPU-simulator rows always, Apple GPU rows when available, GPGPU-Sim rows when Docker is), a test that replays every `fuzz/regressions/` program, a test that fixture features match their generated support blocks, and a coverage floor per corpus (`--check`). Keep the default run fast by sharing compiled programs between cases and marking the slow rows, so `unittest discover` covers every component, not just the first one ported.

### Phase 4: host and library layer, for "migrate apps" (ongoing)
- A host API IR: buffers, queues/streams, events, launch, copies. Backends for CUDA runtime C++, Metal (Swift/ObjC++/metal-cpp), and the existing Python hosts.
- A library-mapping table and shims: cuBLAS GEMM → MPSMatrixMultiplication/MPSGraph; CUB/Thrust reductions and scans → qpel library kernels; cuDNN conv → MPSGraph. Unmappable calls are reported, not silently dropped.
- A Triton frontend (an early motivation for the project). Triton's block/tile model needs Phase 3 tiles. Importing Triton TTIR/TTGIR is likely easier than CUDA C++.

### Feature wish-list (good additions, roughly by value)
1. Clang-based CUDA and MSL importers with coverage reports
2. Text syntax and LSP, for editor support outside MPS
3. Workgroup memory, barriers, subgroup ops
4. Matrix tiles (WMMA ↔ simdgroup_matrix)
5. bf16, i32 and i64 types, vector types
6. Differential testing / fuzzing harness across backends
7. Host IR plus C++/Swift host generation
8. Library mapping (BLAS, conv, reductions)
9. Triton TTIR import
10. MLIR export, to plug into existing optimisation and SPIR-V/Vulkan as a third backend

## Verification baseline
- `python3 check_all.py --gpu --python <venv with torch>`: all four steps pass on 2026-10-19 (128 unit tests with 0 skipped, 13 Metal files, 13 CUDA files plus host link, 12/12 fixtures on the Apple GPU).
- `python3 metal_check.py`: all 13 Metal files compile with `-Werror` (2026-10-19).
- `python3 nvcc_check.py`: all 13 CUDA files compile with nvcc 12.6 for sm_75–sm_90, and the host harness links (2026-10-19).
- Phase 0 exit criterion: the unit tests, the nvcc check and the Metal check pass in CI, and the Apple GPU runners pass from inside the repo.
- Phase 1 exit criterion: every corpus kernel passes all five rows of the round-trip matrix. Met on 2026-11-11, see the Phase 1 status above.
- `python3 check_all.py --gpu --python <venv with torch>` on 2026-11-11: all seven steps pass: 155 unit tests, the round-trip matrix (17 fixtures × 2 plus 11 example pairs and the SAM 2 corpus file and port), corpus coverage 6/6, 18 Metal files, 18 CUDA files with nvcc plus the host link, the behaviour rows (48 source × row combinations: 11 pairs × 4 rows, SAM 2 CUDA × 2, SAM 2 Metal port × 2), and 17/17 fixtures on the Apple GPU.
- `python3 check_all.py --gpu --nvidia --python <venv with torch>` on 2026-11-12: every step passes on macOS (unit tests, round trips, corpus, Metal and nvcc compiles, fuzzing, behaviour rows, Apple GPU, GPGPU-Sim). On Ubuntu 24.04 with clang 18: 168 unit tests, `corpus --check --dialect cuda`, `matrix --cpu` and `fuzz --cpu` pass; Metal paths are skipped without Xcode.
