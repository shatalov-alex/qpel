# Release readiness — 2026-10-14

> Platform evaluation and the development roadmap (2026-10-19) live in [ROADMAP.md](ROADMAP.md).

## Release scope

A first public alpha can reasonably expose the documented qpel subset, MPS authoring, offline graph compilation and two generated backends. A stable 1.0 needs a frozen contract and reproducible installation, validation and support story. The original universal CUDA/Metal/Triton ambition requires substantially more language and frontend work; it must not be implied by an alpha version.

## Blocking work for a supported release (in addition to NVIDIA execution)

1. **Reproducible distribution.** Establish version control/release ownership for the project, choose a license, package the MPS language and compiler, declare dependency versions, and verify installation on a clean machine. The inspected project root has no Git repository or release package metadata. Do not choose the owner's license automatically. Offline qpel.py check/build now exists, but is not an installer.
2. **One semantic contract.** Done for code generation: MPS now only exports qpel IR, and the Python compiler is the single emitter. Freeze graph/ABI versions and add explicit migration tests. Clarify overflow, rounding/contraction, NaN/Inf/subnormal behavior, layout, aliasing and memory ordering, with documented exclusions where appropriate.
3. **Usable authoring and diagnostics.** Exercise create/edit/check/build from a clean MPS install. Improve scopes/completion and map common-compiler node diagnostics back to editor concepts. Document examples without local machine paths. A text frontend is optional for an MPS-first alpha, but would broaden adoption later.
4. **Safety boundary.** Document and test required bounds/initialization/synchronization preconditions. Add practical debugging diagnostics for dynamic accesses and reductions. Conservative constant checks are not a safety proof. Full formal race/bounds proof is not a prerequisite if the supported contract and diagnostics are honest and sufficient.
5. **Runtime hardening.** Test supported dependency versions, current streams/devices, allocation/compilation failures, repeated invocation, module caching and resource lifetime. CUDA/CuPy and Metal/PyTorch currently expose different array ecosystems; decide and document the integration surface rather than silently copying between them.
6. **Repeatable integration and performance evidence.** Automate CPU/negative tests and package integrity checks, add required compiler checks without silently skipped gates, and provide Apple GPU runs on supported configurations. Re-run SAM 2 against the exact release compiler/package, not only frozen historical artifacts. Add warm/cold timing, compilation time, throughput and memory baselines against suitable native implementations. No measured inference speedup exists yet.
7. **Release process.** Maintain a current support matrix, examples, changelog, known limitations and reproducible build instructions; exercise rollback/migration and designate support ownership. All advertised features need evidence tied to the released source/package hashes.

## Additional language work for the original broader goal

- General shared memory, uniform barriers, tiled data access and multidimensional/layout-aware operations.
- Matrix operations and reductions that can select optimized implementations for each backend; later attention/inference kernels with measured benefit.
- Expand the CUDA frontend beyond the current restricted elementwise subset; design and implement a Triton frontend. Metal-source import is also absent.
- Separate portable program meaning from launch tuning, subgroup widths, vendor features and fusion; unsupported constructs must fail explicitly or use an explicit fallback.

These are product-development milestones, not merely testing tasks. A universal arbitrary-source translator with equal performance on both vendors is not a credible release promise.

## Current implementation step

The new `qpel.py check/build` command validates a saved graph for both backends without the IDE or GPU libraries. Build emits both hosts and a complete package with artifact hashes, refuses existing destinations, cleans its own reservation on failure, and reports compilation/GPU status as false. Tests cover no-write checks, complete package hashes/final provenance paths, no overwrite, invalid input, injected failure and CLI errors. This closes the offline entrypoint gap only.

Suggested order: reproducible distribution and semantic convergence; CI plus runtime/integration hardening; then broader language capabilities and performance work. An explicitly experimental alpha can precede the stable contract, provided its limitations and migration policy are clear.
