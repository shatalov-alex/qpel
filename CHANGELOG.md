# Changelog

Notable changes to qpel, by milestone. Dates are when the milestone was reached on `main`.
The IR has its own version (currently 3), recorded in every program and launch plan;
older programs keep building.

## [Unreleased] — Phase 2

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

[Unreleased]: https://github.com/shatalov-alex/qpel/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/shatalov-alex/qpel/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shatalov-alex/qpel/releases/tag/v0.1.0
