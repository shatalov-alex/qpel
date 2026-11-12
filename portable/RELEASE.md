# Release readiness

> Design decisions, the development roadmap and current status live in [ROADMAP.md](ROADMAP.md). This file lists what a supported release needs beyond them. Last reviewed 2026-11-12.

## Release scope

A public alpha can expose the documented qpel subset (IR v3, [CORE.md](CORE.md), [TEXT.md](TEXT.md)), the CUDA and Metal importers, offline compilation and the two generated backends, with the numerics in [NUMERICS.md](NUMERICS.md). A stable 1.0 needs a frozen contract and a reproducible installation, validation and support story. Migrating arbitrary CUDA, Metal or Triton code needs the Phase 3 and 4 work in the roadmap, and an alpha must not imply it. A universal translator with equal performance on both vendors is not a credible release promise.

## Blocking work for a supported release

1. **NVIDIA execution.** Done 2026-11-12: every behaviour row passes on GPGPU-Sim and on a Tesla T4, and so does the CuPy host ([CUDA_HOST.md](CUDA_HOST.md)). Open: newer architectures (sm_80+) and a hosted rerun per release.
2. **Reproducible distribution.** Done: git repository, Apache-2.0 license, NOTICE for third-party code, pinned corpus with checksums, `requirements.txt`. Open: package the compiler (and optionally the MPS language), declare supported dependency versions, and verify installation on a clean machine.
3. **One semantic contract.** Done: MPS only exports IR, Python is the single emitter, and NUMERICS.md specifies overflow, rounding and contraction, f16/bf16, subnormals and math accuracy. Open: freeze the IR/ABI versions with explicit migration tests, and document aliasing, layout and memory ordering.
4. **Diagnostics.** The importers report every unsupported construct with a position and a reason. Open: map compiler diagnostics back to `.qpel` text positions and MPS editor concepts.
5. **Safety boundary.** Document and test the bounds, initialisation and synchronisation preconditions; add debugging diagnostics for dynamic accesses and reductions. Conservative constant checks are not a safety proof; formal race and bounds proofs are not a prerequisite if the contract and diagnostics are honest.
6. **Runtime hardening.** Test supported dependency versions, streams and devices, allocation and compilation failures, repeated invocation, module caching and resource lifetime. CuPy and PyTorch expose different array ecosystems; decide and document the integration surface rather than silently copying between them.
7. **Performance evidence.** Warm and cold timing, compilation time, throughput and memory against the native originals (Phase 3 benchmark harness). Re-run full SAM 2 inference against the release package. No speedup is claimed.
8. **Release process.** `check_all.py` runs every gate locally (hosted CI is configured but disabled for now). Keep the support matrix, examples, CHANGELOG and known limitations current, and tie every advertised feature to evidence from the released source.
