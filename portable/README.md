# qpel compiler

The qpel compiler: IR validation, CUDA and Metal generation, the Clang-based CUDA and Metal
importers, the text syntax, and the checks that run all of them. For an overview, see the
[project README](../README.md).

## Current scope

- **IR version 3** (`CORE.md`): typed programs with integer widths from 8 to 64 bits, `f16`/`bf16`
  storage, float32 arithmetic (f32-strict-v1), exact and bounded math functions, structs, buffer
  views, 1D/2D/3D and explicit-group launch geometry, and multi-stage launch plans.
- **Importers** (`IMPORT.md`): unmodified CUDA and Metal source through clang's JSON AST, with
  per-construct diagnostics; templates, library-header device functions and Metal function
  constants are supported.
- **Backends:** CUDA (nvcc, sm_75–sm_90) and Metal (3.0/3.1), plus Python hosts
  (PyTorch/Metal, CuPy/CUDA).
- **Text syntax** (`TEXT.md`): `.qpel` files that print and parse back to the same IR.
- **Not yet:** shared memory, barriers, warp/simdgroup operations and matrix tiles (Phase 3), and
  host-API and library mapping (Phase 4). See `ROADMAP.md`.

JetBrains MPS (`../languages/`) is an optional structured editor that exports the same IR. Its
files are MPS persistence, not text to edit by hand.

## Offline check and build

From the project root, with Python 3.10 or later:

```sh
python3 portable/qpel.py check /path/to/program.qpel.json
python3 portable/qpel.py build /path/to/program.qpel.json --out /tmp/qpel-build
```

An exported graph is required. `check` writes nothing. `build` checks both backend profiles, then creates `/tmp/qpel-build/package/` with the original graph, neutral source, generated CUDA/Metal source, both host modules, launch metadata and a hash report. Existing output paths are rejected. Complete contents are published inside an exclusively reserved output directory. A failed build removes only its own empty reservation. Build reports distinguish source generation from compilation and GPU testing.

Checking/building requires only Python's standard library, without MPS, PyTorch, CuPy or GPU access. Native model authoring/export uses the IDE; importing CUDA or Metal source needs only clang and Xcode's `metal` (see IMPORT.md). The graph's shared semantic validation runs offline; it does not rerun native editor checks. The generated hosts require their platform dependencies at execution time. See CUDA_HOST.md for the CUDA API and restrictions.

## Authoring and import

CORE.md describes the graph, the MPS export and validation. IMPORT.md describes the CUDA and Metal importers and the round-trip matrix:

```sh
python3 portable/qpel.py import kernels.cu --out program.qpel.json      # or a .metal file
python3 portable/qpel.py translate kernels.cu --out /tmp/qpel-translated  # import, then build both backends
```

## Validation

Run everything that needs no NVIDIA hardware with one command:

```sh
python3 portable/check_all.py --gpu --python /path/to/venv/bin/python
```

This runs the unit tests, the Metal compile check, the nvcc compile check and the Apple GPU tests, then prints a summary. `--python` must be an interpreter with the packages in `requirements.txt` (NumPy, PyTorch). Without `--gpu`, or with `--no-nvcc`, the summary lists what was not run. To run only the compiler tests:

```sh
python3 -m unittest discover -s portable/tests -p 'test_*.py'
```

Numerical checks need clang++; CUDA host ABI tests need NumPy. Missing optional prerequisites produce skips, which must not be treated as release validation. CUDA orchestration and CPU simulation are not proof of NVIDIA execution; nvcc compilation is covered by the check below.

### Apple GPU tests

`gpu_tests/run_gpu_tests.py` builds every IR fixture with the current compiler and runs it on the Apple GPU against an independent CPU oracle, with CPU fallback disabled. It covers all 65536 uint8 pairs for the elementwise kernels, float32/FMA/float16 rounding, ordered and workgroup sums, and the SAM 2 connected-components kernel (133 masks against a flood-fill oracle). Pass fixture names to run a subset. Full SAM 2 model inference is not part of it.

### NVIDIA compile check without an NVIDIA GPU

`nvcc_check.py` builds every IR fixture with the offline compiler, then compiles each generated `.cu` with NVIDIA's real nvcc/ptxas inside the `nvidia/cuda:12.6.3-devel-ubuntu24.04` Docker image (arm64 works on Apple Silicon; no GPU or driver needed). It targets sm_75, sm_80, sm_89 and sm_90, treats warnings as errors, prints ptxas register use, checks the checked-in `generated/components.cu`, and links the `tests/cuda.cu` harness:

```sh
python3 portable/nvcc_check.py            # --arch sm_86 to override, --keep DIR to keep sources and logs
```

The first run pulls a multi-GB image. Passing proves NVIDIA's compiler accepts the code; it does not execute it. NVRTC (the CuPy host's runtime compile path) is not exercised.

### Metal compile check

`metal_check.py` builds the same fixtures and compiles each generated `.metal`, plus the checked-in `generated/components.metal`, with `xcrun metal -std=metal3.0 -Werror`. It needs Xcode's Metal Toolchain (`xcodebuild -downloadComponent MetalToolchain`) but no GPU:

```sh
python3 portable/metal_check.py           # --std metal3.1 to change the standard, --keep DIR to keep sources and AIR
```

### Checked-in SAM 2 package

`generated/` is the components package used by `sam2_adapter.py`, `run_host.sh`, `tests/cuda.cu` and the GPU runners. Rebuild it from the IR fixture with `python3 portable/regenerate.py`; don't edit it by hand. Its manifest stores paths relative to `generated/`, and `program.qpel.json` there is the IR it was built from.

Precision and safety contracts: NUMERICS.md, FLOAT32.md, FMA.md, FLOAT16.md, REDUCTIONS.md, WORKGROUPS.md and BOUNDS.md. Older milestone counts in those documents describe their historical revisions.

See RELEASE.md for remaining release gates. No production-stability, universal translation or inference speedup claim is made.
