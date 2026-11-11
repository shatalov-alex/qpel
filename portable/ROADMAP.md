# qpel: platform evaluation, code assessment, and roadmap — 2026-10-19

Supersedes the "Additional language work" list in [RELEASE.md](RELEASE.md); that file remains the release-readiness checklist.

## Context
qpel is meant to be a neutral intermediate language between CUDA and Apple Metal, so that applications and libraries can move in either direction. The language was started in JetBrains MPS 2026.1. Over roughly four weeks a Codex agent added a Python toolchain, `portable/`, next to the MPS project. This document answers three questions: (1) is MPS the right platform, (2) what does the existing code actually do and how well, and (3) what should come next. Part (4), the history, is reconstructed from `~/.codex/sessions` (one long conversation starting 2026-09-23; the other log files are approval checks or unrelated work).

---

## 1. Is JetBrains MPS the right platform?

**Verdict: good for designing and editing the language, wrong as the compiler and the source of truth. The repo already moved that way without saying so.**

What MPS is good for here:
- Language design: concepts, a projectional editor, checks while editing, and textual output from the model. That is how 59 concepts, 9 check rules and output went from zero to working in one day.
- It cannot produce syntactically invalid programs, and it gives you a structural model for free.
- "JetBrains MPS" has nothing to do with Apple's Metal Performance Shaders. The name clash is still worth avoiding in docs (call it "JetBrains MPS" and "Metal").

What is wrong for the stated goal of migrating entire applications or libraries:
- **Migration means reading existing source code.** CUDA C++, Metal Shading Language and host C++ are all text. MPS is projectional, so parsing large real codebases into it is awkward. The CUDA importer at the time had to drive a *running IDE* over MCP JSON-RPC, and those edits were not transactional (replaced by the offline clang importers, see Phase 1).
- **A compiler has to run headless, in CI and at scale.** Codex already moved all checking and lowering into Python (`portable/qpel_ir.py`, `qpel_types.py`, `qpel_pipeline.py`, `qpel_bounds.py`) for exactly this reason. MPS TextGen and the Python lowering now **disagree**: Python adds the u32-modulo casts, MPS TextGen does not.
- **The "typesystem" in MPS is a string-matching validator** (`QpelChecks.java`, 696 lines). It re-checks the whole root for every node. Behavior, constraints, the generator and the runtime aspects are empty. The MPS side is mostly a form-based editor.
- **The ecosystem is elsewhere.** Real GPU portability stacks are built on MLIR (gpu, nvvm, spirv dialects), SPIR-V with SPIRV-Cross (which produces MSL), Slang (CUDA and Metal backends), and Clang's CUDA frontend. MPS can't reuse any of them.

**Recommendation:**
- Make the **qpel IR (`.qpel.json`, `portable/CORE.md`) the single source of truth**, with the Python core as the reference compiler.
- Keep MPS only as an **optional authoring and visualisation frontend** that exports IR. MPS TextGen should be removed, or reduced to IR export.
- Add a **text syntax** (small grammar, e.g. Lark or a hand-written parser) so people and tools can write qpel without the IDE.
- Longer term, look at lowering qpel IR to **MLIR**, or reusing Clang to import CUDA, rather than growing hand-written regex/descent importers.

## 2. What the existing code does

### Working and verified
- **128 Python tests pass** (5 skipped because NumPy is missing). Numeric tests compile the generated CUDA as CPU C++ with UBSan.
- **SAM 2 connected-components:** written in qpel, turned into Metal, run on an M4 Pro. 133 GPU cases pass, and SAM 2.1 Tiny gives a 0-pixel diff (user-run).
- **GPU confirmation (user-run) for:** threshold, float32, FMA, float16, ordered sum and workgroup sum.
- **Numeric rules are deliberately specified and versioned:** u32-modulo integers, explicit casts, FMA separate from `a*b+c`, float16 for storage with float32 math, ordered and tree reductions.
- **Build is defensive:** `qpel.py check|build` publishes atomically, records hashes and fails closed.

### Architecture as built
```
CUDA (tiny subset) ──cuda_import.py──▶ live MPS IDE (MCP) ──▶ MPS model ──export──▶ .qpel.json IR
                                                                 │                        │
                                                         MPS TextGen (.qpel.h)   Python validate + Lowering
                                                                 └──── token cross-check ─┘
                                                                              │
                                              neutral macro body + backends/{cuda,metal}.h
                                                    ▼                         ▼
                                        components.cu + CuPy host   components.metal + torch.mps host
```
Backend differences live in two macro headers (`portable/backends/cuda.h`, `metal.h`). The approach is sound for a kernel-level language.

### Gaps and defects (ranked)
1. **Not bidirectional.** There is no Metal importer. "CUDA ↔ Metal" is really "qpel → CUDA and Metal" plus a toy CUDA → qpel.
2. **The CUDA importer handles one fixed kernel shape.** It rejects common idioms (`if (i < n) {…}`, `int i`, operand order swapped in the index line, `+ - << >>`, loops, floats, shared memory). Everything beyond the four examples was written natively in MPS.
3. **CUDA output has never been compiled with nvcc or run on NVIDIA hardware.** The CuPy host is tested only against a fake driver.
4. **Two compilers have drifted apart.** MPS TextGen and Python Lowering produce different numeric code. `portable/generated/` (which ships `sam2_adapter.py`) is stale: it predates the u32 rules, has no `cuda_host.py`, and its manifest has absolute paths.
5. **IR is MPS-shaped.** Node kinds are MPS concept names (`U32BufferType`, `ReadOnlyF16BufferType`), and types are a fixed set of combined concepts instead of a type constructor (`buffer<T, access>`). This will not scale to i32, i64, bf16, vectors or tiles.
6. **Language coverage is narrow.** 1D grid only. No `else`, no `for`, no signed integers, no 64-bit types, no vector types, no general shared memory, no explicit barriers, no structs. Operators are strings, not enums.
7. **MPS-side quality.** Empty constraints means no reference scoping. Redundant properties (`isBuffer`, `completionBarrier`, `Grid1D`). Bare default editors. `MODULE_INVALID` false positive (`QpelChecks.pipeline()` around lines 443–451). textGen components misleadingly named `*_Metal`. Duplicate sandbox modules.
8. **Python-side quality.** Identifier rules and reserved words duplicated in 5 places. Backend features detected by string search (`build_backends.py:51-53`, `qpel_ir.py:67-69`). Identical backend profiles. Validation runs 3–4 times per build. GPU runner scripts in `tests/` can't run in place. Docs point to `~/Documents/Codex/.../outputs`.
9. **Host side is minimal.** Python-only hosts (torch.mps, CuPy). No C++, Swift or ObjC host, no streams, no buffer reuse, a sync after every stage. Nothing about speed has been measured.
10. **Scope mismatch with the goal.** Migrating whole apps or libraries needs host-API mapping (cudaMalloc, streams and events → MTLBuffer, MTLCommandQueue, MTLEvent) and library mapping (cuBLAS, cuDNN, CUB, Thrust → MPS, MPSGraph, Accelerate). None of that exists, and that is where most migration effort goes.

## 3. Roadmap

### Phase 0: consolidate (≈1 week)
- Declare `.qpel.json` the source of truth. ~~Regenerate `portable/generated/`~~ done 2026-10-19: `regenerate.py` rebuilds it from the components IR fixture (u32-modulo-v1, with `cuda_host.py`, relative manifest paths). The CPU host test still passes all 133 cases; The components kernel passes all 133 cases on the Apple GPU; full SAM 2 model inference has not been re-run.
- ~~Metal compile check~~ done 2026-10-19: `metal_check.py` compiles all 12 fixtures plus the checked-in package with `xcrun metal -Werror`.
- ~~Turn MPS TextGen into a thin "export IR" step.~~ Done: MPS emits only `<pipeline>.qpel.json`; all code-generating TextGen and the token cross-check are gone.
- ~~git, `requirements.txt`, one check script~~ done 2026-10-19: repo initialised; `check_all.py` runs unit tests, Metal and nvcc compile checks and (with `--gpu`) the Apple GPU tests. **CI is still open:** it needs a remote (e.g. GitHub, macOS runner for Metal, Ubuntu runner for nvcc).
- ~~Hardcoded paths and GPU runners~~ done 2026-10-19: `gpu_tests/run_gpu_tests.py` replaces the Codex-folder runners and the unrunnable `tests/*_gpu.py` / `*_parity.cpp` copies; it builds each fixture fresh and passes all 12 on the Apple GPU. The removed CUDA-vs-original parity checks return as the Phase 1 round-trip matrix.
- ~~Deduplicate identifier and block-size rules~~ done 2026-10-19: one `is_identifier`/`is_block_size` in `qpel_schema.py`. This fixed a bug: names such as `constant`, `threadgroup`, `class` or `switch` passed validation and then broke the generated Metal or CUDA. The MPS-side `QpelChecks` still has the old short list. (The host dtype and macro type tables map to different things and stay separate.)
- Apply the 2026-10-19 `WorkgroupIndex`/`LocalIndex` work, which is still only in `x20-2/outputs/qpel-group-index/`.
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
- **Minimum IR extensions the importers need:** `else`, `for`, signed `i32`, the `if (i < n) {…}` guard shape, any index-line operand order, scalar (non-buffer) kernel parameters, and 2D/3D grid indices (building on the 10-19 `WorkgroupIndex`/`LocalIndex` work). Each extension gets a version bump and the rules in NUMERICS.md.
- **Round-trip test matrix** (in CI). Every corpus kernel runs through:

  | Path | Requirement |
  |---|---|
  | CUDA → qpel → CUDA | nvcc accepts it (`nvcc_check.py`). Behaviour matches the original on the CPU simulator. |
  | Metal → qpel → Metal | `xcrun metal` accepts it. Behaviour matches the original on the Apple GPU (user-run). |
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

**Work order:** (1) IR v3 types as constructors: `i8…u64`, `bool`, `f16`, `bf16`, `f32`, and `buffer<T, read|write|atomic>`, with v1/v2 migration. (2) Importer: header device functions, template instantiations imported one by one, 64-bit and 16-bit integers. (3) Pointers into buffers and byte-addressed buffers, argument structs (flattened), function constants as specialisation constants. (4) Launch geometry in the IR: group id, local id, group size, explicit launch plans. (5) Math functions with a stated accuracy per function (NUMERICS.md), so the behaviour rows compare them within ULP bounds instead of bit for bit. (6) Text syntax. (7) Feature sets per program and backend. (8) Fuzzing. Then the NVIDIA gate. Shared memory and simdgroup reductions stay in Phase 3, which the survey confirms is the next wall for reductions.

- Redesign types as constructors: scalars `u8/u16/u32/u64/i8…i64/f16/bf16/f32`, `vec<T,N>`, `buffer<T, read|write|atomic>`, `shared<T,N>`. Add an IR migration from v1.
- Add `break`/`continue` and explicit `barrier(workgroup)`, and generalise the existing uniformity checks.
- **Text syntax** (`.qpel`) with a parser to IR and a printer from IR that round-trip. This becomes the main way of writing qpel; MPS stays optional.
- Replace substring capability detection with a declared feature set per program and per backend profile (CUDA sm_XX, Metal version).
- Grow the corpus to real code: llama.cpp `ggml-cuda` and `ggml-metal` element-wise and reduction kernels, and PyTorch MPS custom kernels. Publish a **coverage metric** (% importable, % round-trip-clean) per corpus and direction.
- Add differential fuzzing across backends to the round-trip matrix.
- **Milestone gate:** at the end of this phase, run the deferred NVIDIA execution tests (GPGPU-Sim, then one real card).

### Phase 3: performance-capable core (4–8 weeks)
- Shared-memory tiling, warp/simdgroup ops (`shuffle`, `ballot`, `simd_sum`), and subgroup size as a queried capability.
- Matrix tiles mapped to WMMA/mma.sync on CUDA and `simdgroup_matrix` on Metal: the minimum for GEMM and attention.
- Atomics beyond u32 (f32 add, i32/u64 where supported), with capability gating.
- Optimisation passes kept separate from portability lowering, as Codex already argued: constant folding, CSE, vectorised loads.
- A benchmark harness reporting time per kernel against the native original on both platforms.

### Phase 4: host and library layer, for "migrate apps" (ongoing)
- A host API IR: buffers, queues/streams, events, launch, copies. Backends for CUDA runtime C++, Metal (Swift/ObjC++/metal-cpp), and the existing Python hosts.
- A library-mapping table and shims: cuBLAS GEMM → MPSMatrixMultiplication/MPSGraph; CUB/Thrust reductions and scans → qpel library kernels; cuDNN conv → MPSGraph. Unmappable calls are reported, not silently dropped.
- A Triton frontend (the user's original stated priority). Triton's block/tile model needs Phase 3 tiles. Importing Triton TTIR/TTGIR is likely easier than CUDA C++.

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
