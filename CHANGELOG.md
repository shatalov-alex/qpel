# Changelog

Notable changes to qpel, by milestone. Dates are when the milestone was reached on `main`.
The IR has its own version (currently 7), recorded in every program and launch plan;
older programs keep building.

## [Unreleased] — Phase 5

### Added
- **Liger-Kernel as a Triton corpus** (`portable/corpus/liger`, BSD-2, vendored unmodified):
  twenty-seven kernels (norms, gated activations, softmax, RoPE, DyT, KL divergence, TVD, fused cross
  entropy; forward and backward) pass Triton's interpreter against qpel's CUDA (CPU simulator) and
  Metal (Apple GPU), and Liger's own PTX on GPGPU-Sim (all but one, which uses `ldmatrix`). Early
  returns and `tl.debug_barrier()` are read. The Triton frontend reads libdevice `rsqrt`, int64
  program ids, offsets and loop indices; Triton corpora are listed in `frontends/triton/corpora.py`.
- **Subgroup operations (IR version 5):** `qpel.subgroup_lane|index|size`; `qpel.shuffle`,
  `shuffle_xor`, `shuffle_down` and `shuffle_up`; and `qpel.subgroup_add|min|max|and|or|xor`.
  Reductions use one fixed butterfly order, so float sums have the same bits on every backend.
  Every lane must take part; this is checked through helper functions. CUDA uses
  `__shfl_*_sync`; Metal uses `simd_shuffle*` and the SIMD-group builtins.
- The CUDA simulator runs warp operations (shuffles, `__ballot_sync`, `__any_sync`,
  `__all_sync`, `__syncwarp`), and the Metal-on-CPU runner runs SIMD-group functions.
- The importers read CUDA `__shfl_*_sync` (full mask, width 32) and `warpSize`, and Metal
  `simd_shuffle*`, `simd_sum|min|max|and|or|xor` and the SIMD-group builtins.
- Shared memory declared in an inner block is moved to the kernel's top.
- llama.cpp `argmax_f32` is a corpus program and passes both behaviour rows.

- GPGPU-Sim image `qpel12` with `gpgpusim/qpel-shfl.patch`: two warp shuffle bugs fixed. Every
  `shfl.sync` read 0, and in functional mode every warp used warp 0's threads. The self-test now
  covers idx, bfly, down and up. Submitted upstream as accel-sim PR #156.
- **Threadgroup memory sized by the host:** a Metal kernel parameter
  `threadgroup T* p [[threadgroup(n)]]` imports as shared memory of the length the import is
  given (`import_source(shared={'p': N})`, `"shared"` in a corpus program), as function constants
  are specialised. Without a length the import reports `dynamic-shared`. To run the unmodified
  original, the parameter becomes a kernel-scope `threadgroup T p[N]` (`metal_text.sized_threadgroups`),
  which is what the host's `setThreadgroupMemoryLength` gives it.
- Metal thread builtins read by subscript (`tgpig[0]`, `ntg[1]`) import as `.x`, `.y`, `.z`.
  14 more llama.cpp Metal kernels import (126 → 140 of 719).
- llama.cpp `kernel_conv_transpose_2d_f32_f32` (threadgroup memory, a barrier) is a corpus
  program and passes both behaviour rows: the unmodified original on the Apple GPU against
  qpel's CUDA on the simulator and qpel's Metal, bit for bit.
- The Metal-on-CPU runner runs explicit template instantiations (`template [[host_name("x")]]
  kernel void f<float>(...)`).
- **Shared views (IR version 5):** `shared<T>`, a thread's pointer into its group's shared memory
  (CUDA `T*`, Metal `threadgroup T*`): locals (`let row: shared<f32> = tile + 32u;`) and helper
  parameters, indexed like the array. Reads through a view count as varying for the barrier and
  subgroup checks. The importers read Metal `threadgroup T*` locals and helper parameters and
  CUDA pointers that start from shared memory.
- Untyped threadgroup memory (`threadgroup char*`) that every use casts to one type imports as
  shared memory of that type. llama.cpp `kernel_mul_mm_id_map0_ne20_4` (MoE expert routing:
  views into it, barriers in a loop) is a corpus program and passes both rows on the Apple GPU.
  The corpus report counts 16 more kernels that need only a threadgroup length.
- Corpus program inputs can be integers in a range (`"i32 0..7"`).
- **CUDA dynamic shared memory** (`extern __shared__ T s[];`) imports with a given length, and the
  originals run with it on the simulator, GPGPU-Sim and NVIDIA hardware.
- **CUDA helpers reading thread builtins** get them as parameters (`thread_idx_x`, ...), passed by
  their callers. Also: constexpr calls returning a constant, `if constexpr` in template
  instances, `(void)` statements, `*p = v` stores. 18 more llama.cpp Metal kernels import (158 of
  719); 17 more need only a shared-memory length.
- llama.cpp `cumsum_kernel` (dynamic shared memory, a warp scan helper, barriers in a loop) is a
  corpus program and passes both rows: the unmodified CUDA on the simulator against qpel's CUDA
  and qpel's Metal on the Apple GPU.
- **Atomics (IR version 6):** `atomic.max`, `and`, `or`, `xor` and `exchange` on u32/i32; atomic
  `f32` buffers (load, store, add, exchange); atomic `u64` buffers (add, min, max, as statements,
  since Metal's 64-bit atomics give no result). Metal has no 64-bit add, so qpel's Metal adds in
  two 32-bit halves with a carry, exact once the kernel is done; a kernel therefore does not both
  add and take min/max of 64-bit atomics (checked). The float atomic load reads the stored bits.
  The importers read the CUDA and Metal spellings, including `atomicSub` and `atomicExch` whose
  result is used. Programs with float atomics get quarter-integer test inputs, whose sums are exact
  in any order. The `atomics` fixture passes against an oracle on the CPU simulator, the
  Metal-on-CPU runner and the Apple GPU, and both importers give it back.
- **Bitcasts (IR version 6):** `bitcast<T>(e)` reads an f32's bits as u32 or i32 and back (CUDA
  `__float_as_uint` and the others, Metal `as_type<T>`).
- **Metal compare-exchange loops** (`atomic_compare_exchange_weak_explicit`) import as qpel
  compare-and-swap, re-run every iteration of the loop they control. A capped counter and a
  modular sum written that way pass on the Metal-on-CPU runner and the Apple GPU (the unmodified
  original and qpel's Metal) and on the CUDA simulator (qpel's CUDA).
- **CUB block scans and reductions** (LIBRARIES.md): `cub::BlockScan` (raking, raking-memoize and
  warp-scans algorithms; inclusive and exclusive sums and scans, with or without the block total)
  and `cub::BlockReduce` (`Sum`, `Reduce`, full or partial tiles) with `cub::Sum`, `Max` and
  `Min`, one int, unsigned int or float per thread. qpel's own `<cub/cub.cuh>` writes them as
  library functions in CUB 2.5's order of operations; the importer inlines each method call
  (`frontends/facades.py`) and imports the functions. `tests/cub_blocks.cu` agrees bit for bit with
  NVIDIA's CUB on GPGPU-Sim (56 runs) and with qpel's CUDA and Metal; llama.cpp's
  `cumsum_cub_kernel` passes on the CPU simulator, the Apple GPU and GPGPU-Sim (coverage 159/719).
- **Barriers in helper functions (IR version 7):** `qpel.barrier()` may be in a helper that Groups
  kernels call; the uniformity check covers the helper and its calls. `barrier_count` stays in
  kernels.
- Corpus entries may set the macros their real build sets (`"defines"`); the importer reads enum
  template arguments of kernels it calls back (`(cub::BlockScanAlgorithm)0`), `T()` and local
  `typedef`s.

- **Extensions corpus:** mamba (`selective_scan`), pytorch_sparse, mmcv (`ops/csrc`) and gsplat (with glm),
  unmodified at pinned commits with licences and `SHA256SUMS`; the coverage report gives each repository
  its own number (mmcv 65/473, gsplat 2/206, pytorch_sparse 2/22, mamba 0/480; 228/1900 overall).
  Corpus entries can set a language standard (`"standard"`). The PyTorch, CUDA, cooperative-groups and
  libcu++ stand-ins cover what these libraries include; a source clang still rejects is recorded as such.
  Kernels in a library's headers count when the source's host code launches them.

- **mmcv pilots:** `nms_cuda` and `roi_align_forward_cuda_kernel`, unmodified, pass the CPU
  simulator, the Apple GPU and GPGPU-Sim (hand-written plans, case generators with valid RoIs and
  clustered boxes). The importer reads double that is float in disguise as float where the bits
  are the same (NUMERICS.md), `a = b = c` and comma statements, `bool` kernel arguments and `bool*`
  buffers, `sizeof`, constants from library headers, and helper `T*` parameters passed shared
  memory as `shared<T>`. Coverage 250 of 1902 (mmcv 84 of 475, pytorch_sparse 4 of 22).

### Fixed
- **Importing is memory-bounded.** A survey of the extension sources ran one clang per core and read
  each AST whole; mamba's host launcher alone is 100 GB of JSON, and the Mac ran out of memory and
  restarted. clang's JSON is now streamed with limits per declaration and per source (oversized
  declarations are skipped and reported as `ast-too-large`), and surveys run two sources at once.
- A CUDA `__shared__` scalar (`__shared__ float carry;`) was imported as each thread's own local;
  it is now group memory (`shared<T, 1>`). No earlier corpus program had one.
- GPGPU-Sim image `qpel13`: the shuffle fix of `qpel12` wrote the in-range predicate of
  `shfl.sync d|p` inverted (GPGPU-Sim's predicates are zero flags), which CUB's inline PTX reads;
  the self-test now checks it. Also pushed to upstream PR #156 as a third commit.
- The CPU simulator compiles originals whose other kernels have launch-sized `extern __shared__`
  arrays, and calls kernels whose template arguments name a namespace.
- Metal kernels instantiated from templates got numbered names (`kernel_argsort_f32_i32_0`)
  instead of their `host_name` after the first non-ASCII character of ggml-metal.metal: the
  importer sliced the file by clang's byte offsets as characters. 471 kernel names changed.
- The Metal-on-CPU runner gave every thread its own copy of qpel's shared memory
  (`QPEL_SHARED`), the barrier counter and the workgroup-sum scratch, so a qpel Metal kernel
  using them ran wrong on the CPU (never on the GPU).
- The Metal-on-CPU runner can run kernels with a shape argument (`constant uint3&`).

### Found
- Apple's `simd_sum` on float32 adds in a different order from CUDA's shuffle-down sum, so
  results differ in the last bits. A float `simd_sum` therefore imports as qpel's fixed-order sum,
  and the import records the change (NUMERICS.md, Subgroups).

## Phase 4 — 2026-11-14 (release steps pending)

### Added
- **`qpel verify original port`:** a port (CUDA or Metal, hand-written or AI-generated) against
  its original on the same cases. The port is read only for its kernel signatures, so it need
  not import to qpel. The cases include edge values (NaN, infinities, signed zeros, limits;
  subnormals where neither side is Metal). It reports the first difference per case, writes a
  JSON report, and exits 0, 1 or 2. Ships in the wheel (`qpel[verify]`).
  `differential/compare.py` holds the comparison shared with the behaviour rows.
- **Argument mapping in `qpel verify`:** port arguments match the original's by name, by a
  field of the original's argument struct, or by position within buffers and scalars. A Metal
  `constant Args&` struct is filled from separate CUDA scalars. `--arg PORT=EXPR` sets one
  explicitly, and `--value` and `--constant` set original scalars and Metal function constants.
  Byte buffers are accepted for any element type, and integer widths are converted. Unused and
  zeroed arguments are listed.
- **`--dispatch groups`:** a Metal port gets whole threadgroups, as ggml's Metal host launches them.
- **`--nvidia` in `qpel verify`:** CUDA sides run on GPGPU-Sim, all cases in one container.
- **Metal on the CPU (`differential/metal_cpu.py`, `--metal cpu|preflight`):** a Metal source
  runs in the CUDA simulator through generated wrappers (Metal types, atomics and math in
  `include/metal_cpu.h`), where each case has a timeout (`--timeout`). `preflight` uses the Apple
  GPU only after every case finished on the CPU: a Metal kernel that never ends cannot be stopped
  on the GPU, and repeated hangs can restart the machine. The simulator builds with
  `-fno-finite-loops`, so a loop that never ends times out instead of being optimised away.
- **`qpel mcp`:** an MCP server (JSON-RPC on stdio) with `verify` and `translate` tools, for AI
  porting agents. `qpel version`.
- **Ports written outside qpel (`corpus/ports.json`, `python3 -m differential.ports`):**
  - llama.cpp's hand-written Metal backend against its CUDA backend: 21 of 23 operations agree.
    `leaky_relu` gives NaN for ±inf inputs on Metal, and `hardswish` differs in the last bit.
  - Metal ports of nine TRELLIS.2 o-voxel kernels, written by an AI model from the CUDA alone
    (`ports/trellis2/`): all agree bit for bit, on the CPU and on the Apple GPU.

### Fixed
- The Metal runner sent element counts as 32 bits whatever the argument's type; a
  `constant ulong& N` read garbage, and a hash-table probe on it never ended.
- A case on the CPU simulator that runs too long is reported as a failed case (it did not
  finish within N s), not as an exception that stopped the run.

## Phase 3 — 2026-11-14

### Added
- **CUDA benchmarks v0 (`differential/bench_cuda.py`):** on the Kaggle T4, every CUDA original
  against qpel's CUDA. Inputs are generated on the GPU machine and launches timed with CUDA
  events. All 65 give the same outputs; geometric mean x1.00, range x0.78 to x1.97 (TRELLIS.2
  render, open). TRELLIS.2's hash tables and rasteriser get vectorised benchmark-size cases.
- **NVIDIA hardware run (2026-11-14):** 998/998 behaviour-row cases, the self-test and 21/21
  CuPy host fixtures on a Tesla T4. TRELLIS.2's render passes there, so its GPGPU-Sim failure is
  the simulator's.
- **Benchmarks v0 (`differential/bench.py`):** every Metal original against qpel's Metal at
  8M threads on the Apple GPU, the two sides alternating; all 39 timed, none slower,
  geometric mean x0.93 in two runs (M4 Pro).
  Special case generators (`grid_stride`, `sam2_cc`, `scale_offset`) take shapes, so they scale.
- **pip packaging (not published):** `portable/packaging/build_wheel.py` builds a `qpel` wheel and
  sdist (0.4.0.dev0, `qpel` command, `[mps]` extra) and checks the wheel in a new environment.
- **`qpel.py torch-op`:** PyTorch operations from a package and the original extension's
  launch code (`generated/torch_ops.py`), one per `<<<>>>` launch, run on `mps` tensors with
  in-place outputs like the launch. 21 of TRELLIS.2 o-voxel's 25 programs get one.
- **First pilot, TRELLIS.2 o-voxel** (corpus/trellis2): the Z-order and Hilbert serialisation
  kernels, the 32-bit-key hash insert and every hash lookup import and pass the behaviour rows
  (CPU simulator, Apple GPU, GPGPU-Sim). The 64-bit-key insert is refused: Metal has no 64-bit
  compare-and-swap.
- **PyTorch extension stand-ins** for the importer (`frontends/include/libraries`): torch,
  ATen, c10, pybind11, cuBLAS declarations and `cooperative_groups.h`.
- **IR version 4:** thread-private local arrays (`array<T, N>`), compare-and-swap
  (`atomic.cas`, feature `atomic_cas`) and `inout` resources (filled by the caller, updated
  by the kernels, returned).
- **Buffers of different sizes in one dispatch:** a buffer may be sized by any shape the kernel
  receives. The importer reads sizes from the original's launch (`t.size(0)` next to
  `t.data_ptr<T>()`) and from buffers indexed together; the hash tables are now tested larger
  than their key lists.
- **Vectors and reference parameters (IR v4):** `vec<T, N>` values (i32, u32, f32; 2 to 4), copied,
  passed and returned, with `.x .y .z .w`; CUDA's `int4`/`float3`/`make_float3` and Metal's vectors
  import to them and print back. Helper parameters may be `ref` (CUDA `T&`, Metal `thread T&`); a
  non-const C++ reference was previously read as a copy.
- **Shared memory and barriers (IR v4):** `shared<T, N>`, `qpel.barrier()`, `qpel.barrier_count(c)`
  in Groups kernels, with a static check that every thread of a group reaches each barrier.
  The CPU simulator runs a block's threads together for sources with barriers.
- **GPGPU-Sim `bar.red` (`__syncthreads_count/and/or`):** four simulator bugs fixed in
  `qpel-fixes.patch` and checked by its self-test (parse assertion; counts never reset; never
  delivered in functional mode; `and`/`or` predicates inverted), submitted upstream as
  accel-sim/gpgpu-sim_distribution#155. Every TRELLIS.2 program passes the NVIDIA rows except `render`,
  which GPGPU-Sim still gets wrong for the original too (skipped; for the T4 run).
- **TRELLIS.2 pilot ported:** `render` passes the rows, so every o-voxel kernel but the two
  64-bit-key inserts runs from qpel on CUDA and Metal.
- **TRELLIS.2 rasteriser, 3 of 4 kernels:** preprocess (camera projection, float math bit-exact
  on both GPUs), duplicateWithKeys and identifyTileRanges pass the rows, with vector buffers
  (`int4*`), fixed-length buffers (`[16]`) and per-program launch plans.
- **Scaled buffers:** a
  buffer may hold k elements per element of its extent (`in M * 4`). The eight 3D hash kernels
  of TRELLIS.2 pass the behaviour rows (coverage 111 to 119 of 719 kernels).
- **Compare-and-swap under contention:** up to 50,000 threads inserting colliding and repeated
  keys into small TRELLIS.2 hash tables on the Apple GPU; every run must give a valid table.
- The CPU simulator ends every buffer at an inaccessible page: an access past a buffer's end
  stops the run instead of passing unnoticed.
- **Unit tests for every component** (353, from 168): behaviour rows per original source
  (examples, SAM 2, all 52 llama.cpp programs), fuzz regressions, declared features per
  fixture, the whole-corpus coverage floor and the Apple GPU fixture tests. GPGPU-Sim rows
  with `QPEL_NVIDIA=1`; `QPEL_REQUIRE` makes a missing capability fail instead of skip.

### Changed
- `check_all.py` runs the behaviour rows, coverage floor and Apple GPU tests through the unit
  tests; `--gpu` and `--nvidia` now make those capabilities required.
- Importing several kernels of one large file walks its call graph once, and Metal originals
  compile only the template instantiations they run (behaviour rows: 176 s to about 80 s).

## [0.3.0] — 2026-11-12 — Phase 2

### Added
- **IR version 3:** types as terms (`u8` … `i64`, `f16`, `bf16`, `f32`,
  `buffer<T, read|write|atomic>`), `Cast` and `Constant` at every width, and saturating
  float → integer conversion.
- **Real-world corpus:** a pinned llama.cpp slice. 28 CUDA operations and 24 Metal
  specialisations import unmodified and pass every behaviour row; 101 of 691 corpus kernels
  import.
- **Explicit launch geometry** (`groups`), template instantiations, library-header device
  functions, argument structs, buffer views and Metal function constants.
- **Math functions:** exact ones (bit-identical) and bounded ones (≤ 4 ulp).
- **Text syntax** (`.qpel`): a printer and parser that round-trip (`TEXT.md`).
- **Declared features:** each program records what it uses, and the build enables only that.
- **Differential fuzzing** across the CPU simulator, Apple GPUs and GPGPU-Sim.
- **NVIDIA execution on GPGPU-Sim** in Docker: 720 cases pass. Eleven simulator bugs were
  fixed and submitted upstream (accel-sim/gpgpu-sim_distribution#153, #154).
- **NVIDIA hardware run** (`differential/hardware.py`): the same 720 cases, the simulator
  self-test and the generated CuPy host's fixture tests pass on a Tesla T4. Programs are
  built here for x86-64; the GPU machine only runs them (`--kaggle`, or `--bundle-only`).
- `gpu_tests/run_gpu_tests.py --device cuda` runs the fixture tests through the CuPy host.
- The compiler, the CUDA importer and the CPU simulator run on Linux (clang 18 with libstdc++) as well
  as macOS; without Xcode, the Metal paths are reported as skipped.

### Changed
- float32 arithmetic is rounded operation by operation on both backends (f32-strict-v1),
  instead of being left open to contraction.

### Fixed
- Apple Metal miscompiles unsigned division by a constant dividend close to 2^32; the Metal
  backend now works around it.
- Metal's `round` drops the sign of a zero result; the Metal backend restores it.
- The importer dropped i32 → i64 widenings, and turned unsigned literals converted to
  `int` into invalid literals.

## [0.2.0] — 2026-11-11 — Phase 1

### Added
- Offline Clang-based CUDA and Metal importers (JSON AST), with per-construct diagnostics.
- IR version 2: `else`, `for`/`break`/`continue`, signed `i32`, shifts, scalar arguments,
  2D/3D kernels.
- A round-trip matrix covering all five rows, including behaviour rows against
  unmodified originals (CPU simulator and Apple GPU).
- SAM 2 connected components imported unmodified and run on the Apple GPU.

### Changed
- JetBrains MPS became export-only; the Python compiler is the single code generator.

## [0.1.0] — 2026-10-19 — Phase 0

### Added
- The qpel MPS language and the portable Python compiler: IR validation, CUDA and Metal
  lowering, generated Python hosts (PyTorch/Metal, CuPy/CUDA).
- `check_all.py`, the nvcc compile check in Docker, and GPU tests that run in place.

[Unreleased]: https://github.com/shatalov-alex/qpel/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/shatalov-alex/qpel/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/shatalov-alex/qpel/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shatalov-alex/qpel/releases/tag/v0.1.0
