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
- **Not yet:** PyTorch extension headers and op hosts (Phase 3), general shared memory, barriers and
  warp/simdgroup operations (Phase 5), matrix tiles (Phase 6), and host-API and library mapping
  (Phase 7). See `ROADMAP.md`.

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

### PyTorch operations

For a PyTorch CUDA extension, `torch-op` reads the extension's own `<<<>>>` launches and adds
`generated/torch_ops.py` to a built package: one Python function per launch of the package's
kernel, named after the host function around it, taking the tensors of that launch (the host
function's tensor parameters in its order, then the ones it allocates) and its scalar parameters.
It runs the Metal kernel on `mps` tensors and writes the outputs and in-place tensors back,
as the launch does. The caller allocates outputs, as the host function does before launching.

```sh
python3 portable/qpel.py build portable/tests/fixtures/qpel_ir/trellis2_z_order_encode/program.qpel.json --out /tmp/z_order
python3 portable/qpel.py torch-op /tmp/z_order --launches portable/corpus/trellis2/o-voxel/src/serialize/api.cu -I portable/corpus/include
```

```python
ops.z_order_encode_cuda(x, y, z, codes)   # x, y, z, codes: int32 tensors on mps; codes is written
```

Each launch argument must be a tensor's storage (`t.data_ptr<T>()`, `t.contiguous().data_ptr<T>()`),
its length (`t.size(0)`, `t.numel()`), a parameter of the host function or a literal; other
launches are reported with the reason and get no function. Inputs may be strided (they are
copied, as `.contiguous()` does); outputs must be contiguous. Tensors are checked for device,
element size and length. In TRELLIS.2's o-voxel, 21 of the 25 imported programs get an
operation (`tests/test_torch_op.py` runs z-order and the hash inserts with them); the four
rasteriser launches pass host-computed grid sizes and pointers into a shared buffer.

## Installing with pip

`python3 portable/packaging/build_wheel.py` builds `dist/qpel-<version>-py3-none-any.whl` and the
sdist. It stages the compiler alone as a `qpel` package (`qpel/cli.py` is `qpel.py`; imports are
rewritten to `qpel.*`; tests, the matrix and the corpus stay here), builds it with `uv build`,
installs the wheel in a new virtual environment and runs `qpel build` and `qpel translate` there.
Nothing is uploaded. The compiler needs only the standard library; `pip install "qpel[mps]"` adds
PyTorch and NumPy for the generated Metal hosts and `torch-op`.

## Benchmarks

`python3 -m differential.bench [--size W H N] [--json out.json] [program ...]` times every Metal
original in the behaviour matrix against qpel's Metal (Metal -> qpel -> Metal) on the same inputs,
on the Apple GPU. Each side runs once through its normal runner (which also checks they agree);
its dispatches are recorded and replayed, timed between GPU synchronisations. Programs sized in
bytes (llama.cpp rows) are scaled by element size. The two sides alternate batch by batch, so
neither gets the cold GPU. v0 results on an M4 Pro (macOS 26.5, torch 2.14), 39 programs at
2048 x 1024 x 4 (8M threads), ratio qpel/original, two runs:

| Run | Geometric mean | Median | Range |
| --- | --- | --- | --- |
| 1 | x0.93 | x0.95 | x0.74 to x1.00 |
| 2 | x0.93 | x0.96 | x0.75 to x1.02 |

No program is slower after translation. The kernels are element-wise and memory-bound; single
llama.cpp kernels vary by up to 0.15 between runs (their originals' times spread more than
qpel's), so the claim is no added cost, not a speedup. CUDA timing needs an NVIDIA GPU and is part
of the release run.

## Validation

Run everything that needs no NVIDIA hardware with one command:

```sh
python3 portable/check_all.py --gpu --python /path/to/venv/bin/python
```

This runs the unit tests, the round trips, the Metal and nvcc compile checks and the fuzzer, then prints a summary. `--python` must be an interpreter with the packages in `requirements.txt` (NumPy, PyTorch). `--gpu` makes the Apple GPU and the Metal toolchain required, and `--nvidia` adds the GPGPU-Sim rows; without them, or with `--no-nvcc`, the summary lists what was not run. To run only the unit tests (about 3.5 minutes on an M4 Pro):

```sh
python3 -m unittest discover -s portable/tests -p 'test_*.py'
```

Besides the compiler, importer and host tests, the unit tests hold one test per component:

| Tests | What each one checks |
|---|---|
| `test_behaviour_rows` | one original (example, SAM 2, each pinned llama.cpp program): the unmodified original against qpel's CUDA on the CPU simulator, and against qpel's Metal on the Apple GPU |
| `test_fuzz_regressions` | one saved fuzzer failure: text round trip, re-import, CPU simulator against Apple GPU |
| `test_features` | one fixture: declared features = launch plan = manifest = enabled support blocks |
| `test_corpus` | the coverage floor: every corpus kernel imports or is blocked exactly as `corpus/coverage.json` records |
| `test_gpu_fixtures` | one fixture on the Apple GPU against its independent oracle (`gpu_tests/`) |

The behaviour rows are prepared in parallel worker processes, grouped by source file. Optional capabilities skip when absent: the Apple GPU (PyTorch with MPS), the Metal toolchain (Xcode) and GPGPU-Sim. `QPEL_NVIDIA=1` adds the GPGPU-Sim rows (minutes, in Docker), and `QPEL_REQUIRE=gpu,metal,nvidia` turns a missing capability into a failure; `check_all.py --gpu --nvidia` sets both.

Numerical checks need clang++; CUDA host ABI tests need NumPy. Missing optional prerequisites produce skips, which must not be treated as release validation. CUDA orchestration and CPU simulation are not proof of NVIDIA execution; nvcc compilation is covered by the check below.

### Apple GPU tests

`gpu_tests/run_gpu_tests.py` builds every IR fixture with the current compiler and runs it on the Apple GPU against an independent CPU oracle, with CPU fallback disabled. It covers all 65536 uint8 pairs for the elementwise kernels, float32/FMA/float16 rounding, ordered and workgroup sums, and the SAM 2 connected-components kernel (133 masks against a flood-fill oracle). Pass fixture names to run a subset. Full SAM 2 model inference is not part of it.

### NVIDIA compile check without an NVIDIA GPU

`nvcc_check.py` builds every IR fixture with the offline compiler, then compiles each generated `.cu` with NVIDIA's real nvcc/ptxas inside the `nvidia/cuda:12.6.3-devel-ubuntu24.04` Docker image (arm64 works on Apple Silicon; no GPU or driver needed). It targets sm_75, sm_80, sm_89 and sm_90, treats warnings as errors, prints ptxas register use, checks the checked-in `generated/components.cu`, and links the `tests/cuda.cu` harness:

```sh
python3 portable/nvcc_check.py            # --arch sm_86 to override, --keep DIR to keep sources and logs
```

The first run pulls a multi-GB image. Passing proves NVIDIA's compiler accepts the code; it does not execute it. NVRTC (the CuPy host's runtime compile path) is not exercised.

### NVIDIA hardware run

`differential/hardware.py` runs the GPGPU-Sim rows' programs (`gpgpusim/README.md`) on a real NVIDIA GPU, plus `gpgpusim/selftest.cu` and the generated CuPy host's fixture tests (`gpu_tests/run_gpu_tests.py --device cuda`). Everything except execution happens on this machine: the references (CPU simulator, Apple GPU), and nvcc for x86-64 Linux in the amd64 `nvidia/cuda:12.6.3-devel-ubuntu22.04` image (SASS for sm_75/80/89 plus compute_75 PTX). The GPU machine gets a ~9 MB bundle of ready programs, case inputs and `libcudart`, and needs only an NVIDIA driver (PyTorch and CuPy for the host tests):

```sh
python3 -m differential.hardware --kaggle               # bundle, run on a Kaggle T4, compare
python3 -m differential.hardware --kaggle --cupy-only   # only the self-test and the CuPy host
python3 -m differential.hardware --bundle-only OUT      # OUT/bundle.tar.gz; run `bash run.sh` on any NVIDIA box
```

`--kaggle` needs the Kaggle CLI signed in. It uploads the bundle as a private dataset and runs a private script kernel on one T4 with a time limit (`--timeout`, default 1500 s). On 2026-11-12, on a Tesla T4 (sm_75, driver 580.178.04): all 720 behaviour-row cases, the self-test and 20/20 CuPy host fixtures passed. The rows took 170 s on the GPU and the host tests 12 s.

### Metal compile check

`metal_check.py` builds the same fixtures and compiles each generated `.metal`, plus the checked-in `generated/components.metal`, with `xcrun metal -std=metal3.0 -Werror`. It needs Xcode's Metal Toolchain (`xcodebuild -downloadComponent MetalToolchain`) but no GPU:

```sh
python3 portable/metal_check.py           # --std metal3.1 to change the standard, --keep DIR to keep sources and AIR
```

### Checked-in SAM 2 package

`generated/` is the components package used by `sam2_adapter.py`, `run_host.sh`, `tests/cuda.cu` and the GPU runners. Rebuild it from the IR fixture with `python3 portable/regenerate.py`; don't edit it by hand. Its manifest stores paths relative to `generated/`, and `program.qpel.json` there is the IR it was built from.

Precision and safety contracts: NUMERICS.md, FLOAT32.md, FMA.md, FLOAT16.md, REDUCTIONS.md, WORKGROUPS.md and BOUNDS.md. Older milestone counts in those documents describe their historical revisions.

See RELEASE.md for remaining release gates. No production-stability, universal translation or inference speedup claim is made.
