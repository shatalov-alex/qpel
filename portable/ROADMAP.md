# qpel roadmap

qpel is a neutral intermediate language between NVIDIA CUDA and Apple Metal: kernels are imported from either platform into a typed IR and generated for both, so that applications and libraries can move in either direction. This file records the design decisions behind it, the phases of work, and where each phase stands. [RELEASE.md](RELEASE.md) remains the release-readiness checklist.

## Where things stand (2026-11-15)

| Phase | Scope | State |
|---|---|---|
| 0 | Consolidation: one compiler, IR as the source of truth | done 2026-10-19 |
| 1 | Offline Clang-based CUDA and Metal importers, round-trip matrix | done 2026-11-11 |
| 2 | IR v3, real-world corpus (llama.cpp), text syntax, fuzzing, NVIDIA gate | done 2026-11-12 (GPGPU-Sim and a Tesla T4) |
| 3 | First real port: PyTorch extensions (TRELLIS.2 pilot), header stubs, PyTorch op host, benchmarks v0 | done 2026-11-14 (Apple GPU, CPU simulator, Tesla T4) |
| 4 | `qpel verify` for any port; public release | in progress (verify v0 done) |
| 5 | Breadth: shared memory, atomics, warp/simdgroup ops, block scans | planned |
| 6 | Performance: matrix tiles, benchmarks | planned |
| 7 | Libraries, Triton | later (Triton frontend pilot started 2026-11-15) |

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
- **Translator and verifier** (2026-11-12): besides translating, qpel checks that any port, written by hand or by an AI tool, behaves like its original on both platforms. Progress is measured by real ports of CUDA-only libraries that people ask to run on Macs.
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

### Replan (2026-11-12)
After Phase 2 the plan was reordered around users. The people asking for CUDA code on Macs are mostly users and maintainers of CUDA-only PyTorch extensions and kernel libraries, and the part of qpel no other tool offers is proving that a port behaves like its original. So qpel is a **translator and a verifier**, progress is measured by real ports a maintainer can accept, and every phase ends with a pilot on a real repository. The old Phase 3 (performance core) is split into Phases 5 and 6; host APIs, libraries and Triton move last.

| Phase | Scope | Pilot / exit |
|---|---|---|
| 3 | First real port: PyTorch extensions | TRELLIS.2 `o-voxel` kernels translated, verified, timed |
| 4 | `qpel verify`: equivalence of any port; public release | a third-party hand port and an AI-generated port verified |
| 5 | Breadth: shared memory, atomics, warp/simdgroup ops, block scans | mamba selective scan, pytorch_sparse, mmcv NMS/RoIAlign |
| 6 | Performance: matrix tiles, vector loads, benchmarks | gsplat rasterisation, bitsandbytes dequantisation |
| 7 | Libraries (cuBLAS/CUB/Thrust → MPS/Accelerate), Triton | re-evaluated when reached |

### Phase 3: first real port, PyTorch extensions (≈3–4 weeks, v0.4.0)
Done before the replan: **test enrichment.** `unittest discover` covers every component (353 tests, from 168). One test per original source (all 81: examples, SAM 2, 52 llama.cpp programs) runs the CPU-simulator rows and, when there is one, the Apple GPU rows; the GPGPU-Sim rows run with `QPEL_NVIDIA=1`. One test per fuzz regression, one per fixture for declared features against the generated support blocks, the whole-corpus coverage floor, and the Apple GPU fixture tests. Rows are prepared in parallel workers grouped by source file; the importer now walks a large file's call graph once per parse and the Metal originals compile only the template instantiations they run (rows: 176 s to about 80 s). `QPEL_REQUIRE` turns a missing GPU, Metal toolchain or GPGPU-Sim into a failure (`check_all.py --gpu --nvidia`).

Pilot: Microsoft TRELLIS.2's `o-voxel` extension (13 kernels: Z-order and Hilbert serialisation, a hash table, a rasteriser), whose maintainers asked for non-CUDA backends.

**Progress (2026-11-12):** stand-ins for PyTorch, ATen, c10, pybind11 and cooperative groups (every extension surveyed now parses); the o-voxel kernels in the corpus; IR version 4 with local arrays, compare-and-swap and `inout` resources; cooperative-groups grid ranks, `std::numeric_limits`, out-of-line namespace kernels. Ten TRELLIS.2 kernels are corpus programs and pass both behaviour rows: the four serialisation kernels, 32-bit-key hash insertion and every hash lookup. Not portable: 64-bit-key insertion (Metal has no 64-bit compare-and-swap). Still to do: `int4` locals (the 3D hash variants), buffers of different sizes in one dispatch (a hash table larger than its key list), the rasteriser (vectors, structs, shared memory), a contention test of compare-and-swap on the GPU, then the PyTorch op host, benchmarks and packaging.
- Declaration-only header stubs for `cuda.h`, `cooperative_groups.h` (`this_grid`, `this_thread_block`, `thread_rank`), `torch/extension.h` and the ATen/c10 basics, with host bodies blanked as for llama.cpp. Today clang stops at these includes in every PyTorch extension surveyed.
- Importer: cooperative-groups thread ranks, `atomicCAS` and the other integer atomics at 32 and 64 bits, `size_t` indexing idioms, and whatever else the pilot surfaces.
- Minimal shared memory: static `__shared__` arrays and `__syncthreads` ↔ `threadgroup` arrays and `threadgroup_barrier`, as far as the rasteriser needs (general rules in Phase 5).
- **PyTorch op host:** a generated Python function with the extension's signature that runs the Metal kernels on MPS tensors, so a Mac path sits next to the CUDA one.
- **Benchmark harness v0:** time per kernel. Done for Metal (2026-11-13, `differential/bench.py`): all 39 Metal originals against qpel's Metal at 8M threads, none slower, geometric mean x0.93 in two runs (README, Benchmarks). On NVIDIA (2026-11-14, Tesla T4, `differential/bench_cuda.py`): all 65 CUDA originals against qpel's CUDA, same outputs, geometric mean x1.00, range x0.78 to x1.97. Open for Phase 6: TRELLIS.2 render x1.97 and five kernels between x1.12 and x1.47 (README, Benchmarks).
- `pip install qpel` (wheel, `qpel` command). Done, not published: `packaging/build_wheel.py` builds and checks the wheel; upload is a manual step at the release.
- **Exit:** the pilot's kernels translated and verified on the CPU simulator, the Apple GPU and NVIDIA hardware, and timed; the port is ready to offer to the maintainers (at the public release, or earlier as standalone code).

### Phase 4: `qpel verify`, equivalence of any port (≈3 weeks, v0.5.0, public release)
- `qpel verify original.cu port.metal`: cases derived from the launch plan, both sides run (CPU simulator, Apple GPU, NVIDIA through GPGPU-Sim or a hardware runner), outputs compared under NUMERICS.md (bit-exact, or within stated ulp bounds), first difference reported. Unlike today's matrix, the port need not import to the same IR; only its signature must map.
- Edge cases per kernel from the fuzzer: NaN, ±0, subnormals, boundaries, odd sizes.
- A machine-readable report, and an entry point an AI porting agent can call inside its loop.
- Public release: final review of the docs, private vulnerability reporting, repository public; the Phase 3 port offered upstream.
- **Exit:** a third-party hand port and an AI-generated port verified, with the results published.

**Progress (2026-11-14):** verify v0 (`differential/verify.py`, `qpel verify`). The port is read only for its signatures; edge-value cases (NaN, ±inf, ±0, limits, subnormals where no Metal runs); first difference per case; JSON report; exit 0/1/2; in the wheel. All 14 example pairs agree both ways. Then, the same day: argument mapping by name, struct field or position (`--arg`, `--value`, `--constant`), `--dispatch groups`, the CUDA side on GPGPU-Sim (`--nvidia`), Metal on the CPU simulator with a timeout (`--metal cpu|preflight`, so a port whose loops may not end never reaches the Apple GPU unproven), and `qpel mcp` for agents.

**Exit criterion met locally (2026-11-14):** a third-party hand port and an AI-generated port verified (`corpus/ports.json`, `python3 -m differential.ports`). llama.cpp's own Metal backend against its CUDA backend: 21 of 23 operations agree; `leaky_relu` (NaN for ±inf on Metal) and `hardswish` (last bit) differ in llama.cpp itself. Nine TRELLIS.2 o-voxel kernels ported to Metal by an AI model from the CUDA alone: 9/9 agree bit for bit on the CPU and the Apple GPU. Left for the owner: publishing the results, private vulnerability reporting, making the repository public, the PyPI upload and the TRELLIS.2 offer (draft in the project notes).

### Phase 5: breadth for kernel libraries (≈6–8 weeks, v0.6.0)
- Shared memory in general (static and dynamic) and barriers, with the uniformity checks generalised.
- Atomics beyond integers: float add, 64-bit, compare-and-swap loops, gated by capability.
- Warp/simdgroup operations: shuffle, ballot, reductions (`__shfl_*` ↔ `simd_shuffle`, `simd_sum`), with the subgroup width as a capability (32 on both platforms today).
- A block-level library subset: CUB BlockLoad, BlockScan and BlockReduce ↔ qpel library kernels.
- An extensions corpus with coverage per repository (TRELLIS.2, Hunyuan3D-2, mamba, pytorch_sparse, mmcv, gsplat), replacing the corpus percentage as the headline number.
- **Pilots:** mamba's selective-scan forward kernel, pytorch_sparse, mmcv NMS and RoIAlign.

**Progress (2026-11-14):** subgroup operations, IR version 5 (CORE.md, NUMERICS.md Subgroups). It adds lane, subgroup index and size; shuffles (index, xor, down, up); and reductions (add, min, max, and, or, xor) in one defined butterfly order. A uniformity check covers subgroups, through helper functions, and the dispatch block's x size must be a multiple of 32. The backends use `__shfl_*_sync` and `simd_shuffle*`. The CPU simulator runs warps (shuffles, ballots, `__syncwarp`), and the Metal-on-CPU runner runs the SIMD-group functions. Both importers read these operations: CUDA `__shfl_*_sync` with the full mask, and `warpSize`; Metal `simd_shuffle*`, `simd_sum|min|max|...` and the SIMD-group builtins. Shared memory declared in an inner block is now moved to the kernel's top. llama.cpp's `argmax_f32` (shuffles, shared memory, a barrier) is a corpus program and passes both behaviour rows; coverage is 126 of 719 kernels. One finding: Apple's `simd_sum` on float32 adds in a different, undocumented order from CUDA's shuffle-down tree, so results differ in the last bits; an explicit xor butterfly agrees bit for bit. nvcc compiles every fixture (sm_75 to sm_90). On GPGPU-Sim, warp shuffles turned out to be broken: they read 0, and every warp used warp 0's threads. Both are fixed in `gpgpusim/qpel-shfl.patch` (image `qpel12`), with self-test checks; argmax passes there for the original and qpel's CUDA. Shuffle fixes submitted upstream (accel-sim PR #156). Threadgroup memory sized by the host (Metal `[[threadgroup(n)]]` parameters) imports with a given length, and thread builtins read by subscript import: coverage 140 of 719. llama.cpp's `kernel_conv_transpose_2d_f32_f32` passes both rows against the unmodified original on the Apple GPU. Fixed on the way: template kernels after the first non-ASCII character of ggml-metal.metal had lost their `host_name` (byte versus character offsets), and the Metal-on-CPU runner gave each thread its own copy of qpel's shared memory. Shared views (`shared<T>`, pointers into shared memory) and untyped threadgroup memory read as one type import; llama.cpp's `kernel_mul_mm_id_map0_ne20_4` passes both rows too, and 16 more kernels need only a threadgroup length. CUDA `extern __shared__` imports with a given length, and CUDA helpers that read thread builtins get them as parameters: llama.cpp's `cumsum_kernel` passes both rows, and coverage is 158 of 719 (plus 17 that need only a shared length). Atomics (IR version 6, 2026-11-15): max, and, or, xor and exchange on 32-bit integers; float32 load, store, add and exchange; unsigned 64-bit add, min and max as statements, the add made of two 32-bit adds on Metal (which has none); bitcasts between f32 and 32-bit integers; Metal compare-exchange loops import as qpel compare-and-swap. The `atomics` fixture passes against an oracle on the CPU simulator, the Metal-on-CPU runner and the Apple GPU, and a Metal CAS-loop test passes for the unmodified original on the Apple GPU. The current corpus barely uses these (coverage stays 158 of 719); the extensions corpus will. CUB block subset (2026-11-15, LIBRARIES.md): `cub::BlockScan` and `cub::BlockReduce`, one item per thread, import through qpel's own `<cub/cub.cuh>` as library functions in CUB 2.5's order, which needs barriers in helpers (IR version 7). A test of every method agrees bit for bit with NVIDIA's CUB on GPGPU-Sim, and llama.cpp's `cumsum_cub_kernel` passes all rows (coverage 159 of 719). GPGPU-Sim's shuffle predicate, inverted by our own fix, is corrected (image `qpel13`). Extensions corpus (2026-11-15): mamba, pytorch_sparse, mmcv and gsplat vendored (107 sources), coverage per repository in the report: mmcv 65 of 473, gsplat 2 of 206, pytorch_sparse 2 of 22, mamba 0 of 480; the whole corpus 228 of 1900. Importing is now memory-bounded (a parallel survey of these sources had taken all of the Mac's memory). mmcv pilots (2026-11-15): the unmodified `nms_cuda` and `roi_align_forward_cuda_kernel` pass the CPU simulator, the Apple GPU and GPGPU-Sim against qpel's translations (6 and 5 cases), after the importer learned float that C computes in double, chained and comma statements, bool arguments and buffers, `sizeof`, header constants and helper pointers into shared memory; coverage 250 of 1902 (mmcv 84 of 475). Open: RoIAlign backward (float atomic gradients need a tolerance, their order differs run to run), NMS `gather_keep_from_mask` (a barrier under a shared-memory value, which qpel's static check rejects; on a closer look the kernel races: a thread reads `removed[nblock]` while the thread that owns that word may already be or-ing the next candidate's mask into it, with no barrier between. The race is benign in its result (2026-11-16): `nms_cuda` sets mask bits only for later boxes, so the early bits mark only boxes after that candidate, which every thread keeps, and every thread takes the same branches; it is a data race in CUDA's memory model, not a wrong output, so it was not reported upstream. qpel's static check cannot show the barrier uniform, and the rejection stands). pytorch_sparse pilot (2026-11-15): `spmm_kernel` (sum with values, mean, max with `arg_out`) and `spmm_value_bw_kernel` pass the CPU simulator, the Apple GPU and GPGPU-Sim against qpel's translations (20 cases each), after the importer learned static methods of library structs, enumerators, pointers to locals as references and `__ldg`; pytorch_sparse 22 of 22, the corpus 268 of 1902. Optional buffers (IR version 9, 2026-11-16): kernel buffers the caller may leave out, tested with `qpel.present`, as PyTorch extensions test optional tensors against null; mmcv's RoI pooling forward passes with and without its optional `argmax` on the CPU simulator, the Apple GPU and GPGPU-Sim. Next: mamba's selective scan, which needs several items per thread (arrays as helper parameters), `BlockLoad`/`BlockStore`, prefix callbacks and `float2` scans.

### Phase 6: performance (≈6–8 weeks, v0.7.0)
- Matrix tiles: WMMA/`mma.sync` ↔ `simdgroup_matrix` (M1–M4) and the M5 matrix tiles; the minimum for GEMM and attention.
- Vectorised loads, and optimisation passes kept separate from portability lowering (constant folding, CSE).
- Benchmark harness v1: against the native originals on both platforms, published per pilot.
- **Pilots:** gsplat rasterisation, bitsandbytes dequantisation.

### Phase 7: libraries and other frontends (later; re-evaluated when reached)
- A host-API IR and a library-mapping table: cuBLAS GEMM → MPSMatrixMultiplication/MPSGraph; CUB/Thrust reductions and scans → qpel library kernels; cuDNN convolution → MPSGraph. Unmappable calls are reported, not dropped.
- Triton: first as an input to `qpel verify`; a Triton frontend only if Triton's own Apple backend does not land.

**Started early (2026-11-15): a Triton frontend pilot** (frontends/triton/README.md). Triton's own TTIR, compiled without a GPU in a Linux container, is lowered to qpel: one Triton program per workgroup, index tensors recomputed per element, data tensors in per-thread slots, shared memory for broadcasts and reductions, `scf.for` with tensor accumulators. Seventeen kernels (vector add, an element-wise select, exact operations, GELU through libdevice, fused softmax, layer norm, a 2D strided transpose, per-row statistics, matrix multiplication with `tl.dot` and with block pointers, argmax, cumsum, a clip under `if`, a histogram with atomics, fused attention forward in float16, `tl.trans`, and a `while` loop) are fixtures and pass against Triton's interpreter on the CPU simulator and the Apple GPU (72 cases; element-wise bit for bit, float sums within a stated tolerance, since Triton leaves their order open), and qpel's CUDA and Metal agree bit for bit. Triton's own PTX runs on GPGPU-Sim for all seventeen and agrees with both (except where Triton's interpreter computes `tl.math.fma` unfused, which Triton's own PTX does not) (differential/triton_nvidia.py), which found a GPGPU-Sim bug (`setp` on 16- and 32-bit types, image `qpel14`). A real-world corpus followed (2026-11-16): fifteen of Liger-Kernel's kernels, vendored unmodified (corpus/liger: RMSNorm, LayerNorm forward and backward, fused add + RMSNorm, PolyNorm, SwiGLU and GeGLU forward and backward, softmax forward and backward, ReLU², KL divergence, the cross-entropy row scaling), pass Triton's interpreter on the CPU simulator and the Apple GPU, and Liger's own PTX on GPGPU-Sim. They needed libdevice `rsqrt`, int64 program ids and loop indices, and numpy libdevice functions in the interpreter. Next: more of Liger (cross-entropy, RoPE, group norm, sparsemax), `scf.while` carrying tensors, `tt.join`/`tt.split`/`tt.cat`, and the backward kernels of attention and layer norm.

### Deferred
- Bringing the JetBrains MPS editor up to IR v2/v3: the text syntax is the main way to write qpel.
- LSP, MLIR export and SPIR-V/Vulkan as a third backend: after Phase 7.

## Verification baseline
- `python3 check_all.py --gpu --python <venv with torch>`: all four steps pass on 2026-10-19 (128 unit tests with 0 skipped, 13 Metal files, 13 CUDA files plus host link, 12/12 fixtures on the Apple GPU).
- `python3 metal_check.py`: all 13 Metal files compile with `-Werror` (2026-10-19).
- `python3 nvcc_check.py`: all 13 CUDA files compile with nvcc 12.6 for sm_75–sm_90, and the host harness links (2026-10-19).
- Phase 0 exit criterion: the unit tests, the nvcc check and the Metal check pass in CI, and the Apple GPU runners pass from inside the repo.
- Phase 1 exit criterion: every corpus kernel passes all five rows of the round-trip matrix. Met on 2026-11-11, see the Phase 1 status above.
- `python3 check_all.py --gpu --python <venv with torch>` on 2026-11-11: all seven steps pass: 155 unit tests, the round-trip matrix (17 fixtures × 2 plus 11 example pairs and the SAM 2 corpus file and port), corpus coverage 6/6, 18 Metal files, 18 CUDA files with nvcc plus the host link, the behaviour rows (48 source × row combinations: 11 pairs × 4 rows, SAM 2 CUDA × 2, SAM 2 Metal port × 2), and 17/17 fixtures on the Apple GPU.
- `python3 check_all.py --gpu --nvidia --python <venv with torch>` on 2026-11-12: every step passes on macOS (unit tests, round trips, corpus, Metal and nvcc compiles, fuzzing, behaviour rows, Apple GPU, GPGPU-Sim). On Ubuntu 24.04 with clang 18: 168 unit tests, `corpus --check --dialect cuda`, `matrix --cpu` and `fuzz --cpu` pass; Metal paths are skipped without Xcode.
