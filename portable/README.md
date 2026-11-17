# qpel compiler

The qpel compiler: IR validation, CUDA and Metal generation, the Clang-based CUDA and Metal
importers, the text syntax, and the checks that run all of them. For an overview, see the
[project README](../README.md).

## Current scope

- **IR version 14** (`CORE.md`): typed programs with integer widths from 8 to 64 bits, `f16`/`bf16`
  storage, float32 arithmetic (f32-strict-v1), exact and bounded math functions, structs, buffer
  views, vectors and local arrays, 1D/2D/3D and explicit-group launch geometry, shared memory and
  barriers, subgroup shuffles and reductions, atomics, module constants, 8x8 matrices held by a
  subgroup (Metal's `simdgroup_matrix`, NVIDIA's `mma.sync`) and arrays of them, and multi-stage launch plans.
- **Importers** (`IMPORT.md`): unmodified CUDA and Metal source through clang's JSON AST, with
  per-construct diagnostics; templates, library-header device functions, Metal function
  constants, a CUB subset and Metal's simdgroup matrices are supported. A Triton pilot reads
  Triton's TTIR (`frontends/triton/README.md`).
- **Backends:** CUDA (nvcc, sm_75–sm_90) and Metal (3.0/3.1), plus Python hosts
  (PyTorch/Metal, CuPy/CUDA). Optimisation passes (`qpel_opt.py`) are kept apart from the
  portable meaning: whole-vector loads where the original read vectors.
- **Text syntax** (`TEXT.md`): `.qpel` files that print and parse back to the same IR.
- **Not yet:** Apple M5's matrix tiles, NVIDIA's WMMA fragments in the importers, and host-API and
  library mapping (Phase 7). See `ROADMAP.md`.

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

Triton kernels come in through Triton's own TTIR (frontends/triton/README.md: compiled without a GPU in a Linux container, one Triton program per qpel workgroup):

```sh
python3 -m frontends.triton kernel.ttir --num-warps 4 --out kernel.qpel   # run in portable/
python3 -m differential.triton          # corpus/triton against Triton's interpreter
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

## Verifying a port

`qpel verify original.cu port.metal` checks a port (hand-written, AI-generated or translated)
against its original on the same inputs. Either side can be CUDA or Metal.

```sh
python3 portable/qpel.py verify cuda_examples/scale_offset.cu metal_examples/scale_offset.metal
python3 portable/qpel.py verify original.cu port.metal --map scale_offset=affine --json report.json
```

- **The original is the reference.** It is imported to qpel, which gives the launch plan, the
  cases and the numeric tolerance.
- **The port is only read for its kernel signatures.** It need not import to qpel, so it can use
  constructs qpel does not cover. Its kernel is the one with the same name, or `--map ORIGINAL=PORT`.
- **Arguments are matched by name, then by position.** Each port argument (builtins such as
  `[[thread_position_in_grid]]` aside) takes, in this order: the expression `--arg PORT=EXPR`
  gives it; the original's argument of the same name, or a field of the original's argument
  struct; otherwise the original's next unclaimed argument of the same kind (buffer, scalar).
  A Metal `constant Args& args` is filled field by field the same way (`--arg args.ne0=N`);
  fields left unmatched are zero and listed. Buffers must have the original's element type or be
  byte buffers; integer scalars of another width are converted; floats and integers are not
  mixed. When the argument counts differ, the port is refused with the arguments that match
  nothing. `--value NAME=RULE` sets an original scalar (`volume`, a number, or an expression of
  W, H, N), and `--constant NAME=VALUE` a Metal function constant, for whichever side declares it.
- **Dispatch.** A Metal port gets an exact thread count (dispatchThreads), so its last
  threadgroup may be partial. `--dispatch groups` launches whole threadgroups instead, as CUDA
  launches blocks and as ggml's Metal host does (its kernels index with `threads_per_threadgroup`).
- **Both sides run the same cases.** These are the behaviour matrix's cases, plus copies whose
  input buffers mix in edge values: NaN, infinities, signed zeros, type limits, and subnormals
  where neither side is Metal (Apple GPUs flush subnormals). CUDA runs on the CPU simulator, or
  with `--nvidia` on GPGPU-Sim (a simulated NVIDIA GPU, in Docker). Metal runs on the Apple GPU,
  or with `--metal cpu` in the same CPU simulator (`differential/metal_cpu.py`).
- **Ports whose loops may not end: use `--metal preflight`.** A Metal kernel that never ends (a
  hash-table probe or a compare-and-swap retry loop given a wrong size) cannot be stopped on an
  Apple GPU: killing the process leaves the GPU work running, and repeated hangs can restart the
  machine. `--metal preflight` runs every case on the CPU first, where a case is stopped after
  `--timeout` seconds (60 by default), and uses the GPU only if every case finished there.
- **Outputs are compared under NUMERICS.md.** They must be bit-exact, or within twice the ulp
  bound where the original calls bounded math functions. Each failing case reports its first
  difference, for example `z[0]: original -7, port -8`.
- **Edge values the original rejects.** An edge case on which the original itself fails (an edge
  value used as an index, say) is skipped, not counted against the port.

Exit status is 0 when every case agrees, 1 when one differs, and 2 when the port cannot be run.
`--json` writes the machine-readable report. The 14 example pairs (`cuda_examples/` and
`metal_examples/`) agree in both directions on all 10 cases each. A one-character bug, or a port
that turns NaN into 0, is caught.

**For AI porting agents: `qpel mcp`.** It serves `verify` and `translate` as MCP tools on stdin
and stdout (JSON-RPC), so an agent can check a port inside its own loop:

```sh
claude mcp add qpel -- qpel mcp        # or: python3 portable/qpel.py mcp
```

**Ports written outside qpel (`corpus/ports.json`).** `python3 -m differential.ports` (from
`portable/`) verifies every listed port against its original; `--kind hand|ai`, `--only TEXT`,
`--json FILE`. Results on 2026-11-14:

| Port | Kind | Result |
|---|---|---|
| llama.cpp's own Metal backend (`ggml-metal.metal`) against its CUDA backend: 23 unary and element-wise operations | hand-written | 21 agree. `leaky_relu` gives NaN for ±inf where CUDA gives ±inf (Metal computes `0 * inf`); `hardswish` differs in the last bit (CUDA `(x + 3) / 6`, Metal `x / 6 + 0.5`) |
| TRELLIS.2 o-voxel: Z-order and Hilbert encode/decode, the five 32-bit hash-table kernels (`ports/trellis2/`) | AI-generated from the CUDA alone, without qpel | 9/9 agree bit for bit on the CPU and on the Apple GPU (hash ports with `--metal preflight`) |

## Installing with pip

`python3 portable/packaging/build_wheel.py` builds `dist/qpel-<version>-py3-none-any.whl` and the
sdist. It stages the compiler alone as a `qpel` package (`qpel/cli.py` is `qpel.py`; imports are
rewritten to `qpel.*`; tests, the matrix and the corpus stay here), builds it with `uv build`,
installs the wheel in a new virtual environment and runs `qpel build` and `qpel translate` there.
Nothing is uploaded. The compiler needs only the standard library; `pip install "qpel[mps]"` adds
PyTorch and NumPy for the generated Metal hosts and `torch-op`, and `qpel[verify]` adds NumPy for
`qpel verify`, which the smoke test also runs from the installed package.

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
qpel's), so the claim is no added cost, not a speedup.

**CUDA.** `differential/hardware.py --kaggle` also times every CUDA original against qpel's CUDA
on the T4 (`differential/bench_cuda.py`). Each is timed at 8M threads; TRELLIS.2's hash tables and
rasteriser get about 1M keys or voxels in a 2048 x 1024 image, gsplat's rasteriser a 2048 x 1024
frame of 131k gaussians, bitsandbytes 8M values in blocks of 64. The inputs are generated on the
GPU machine, the launches are timed between CUDA events (median of up to 200 repetitions), and
both sides' outputs are compared. Harness v1, 2026-11-17 (Kaggle run 4): 80 programs timed,
qpel/original x0.99 as a geometric mean, median x1.00, range x0.78 to x1.35; every NVIDIA row
passed on the T4. The pilots: gsplat x0.86, bitsandbytes 8-bit x0.99, FP4 x0.97, NF4 x0.97,
mamba's selective scan x1.00, TRELLIS.2 `render` x1.00 (x1.94 before: `__fdiv_rn` and atomic
loads read as read-modify-writes; Phase 6), `sam2_cc` x1.07.

| Program | Original | qpel | Ratio |
| --- | --- | --- | --- |
| gsplat `rasterize_to_pixels_3dgs_fwd` (2048 x 1024, 131k gaussians) | 9.36 ms | 8.04 ms | x0.86 |
| bitsandbytes dequantise 8-bit (8M values) | 0.209 ms | 0.207 ms | x0.99 |
| bitsandbytes dequantise FP4 | 0.193 ms | 0.186 ms | x0.97 |
| bitsandbytes dequantise NF4 | 0.193 ms | 0.187 ms | x0.97 |
| TRELLIS.2 `render` | 0.535 ms | 0.536 ms | x1.00 |

bitsandbytes was x1.6 to x3.5 in run 3: qpel's CUB subset stored a warp-transposing `BlockStore`
directly, so neighbouring threads wrote 64 bytes apart. It now goes through shared memory as CUB
does (LIBRARIES.md). gsplat's outputs differ at this size (2.5M elements, at most 1168 ulp, in
low-magnitude colours and alphas): the original's `__expf` is read as qpel's bounded `exp`, more
accurate than the original, and the difference compounds over the blended layers; the behaviour
cases agree within their tolerance. Slower, and open: llama.cpp `arange` x1.35 and `fill` x1.14,
TRELLIS.2 `preprocess` x1.13. Their SASS matches the originals' but for computing the element
count from qpel's shape (three uniform-datapath instructions per warp, in kernels whose warps make
one store each) — a candidate is passing the count precomputed.

**Pilots on the Apple GPU.** A CUDA original has no Metal side to time against;
`python3 -m differential.bench --cuda <program>` times qpel's Metal alone, and bitsandbytes'
against the same dequantisation as PyTorch operations on MPS (written here, not bitsandbytes'
code; the outputs are identical). On an M4 Pro: gsplat 9.8 ms for the frame above; bitsandbytes
0.20 to 0.36 ms for 8M values, against 1.7 to 4.7 ms for PyTorch.

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

`--kaggle` needs the Kaggle CLI signed in. It uploads the bundle as a private dataset and runs a private script kernel on one T4 with a time limit (`--timeout`, default 1500 s). On 2026-11-14, on a Tesla T4 (sm_75, driver 580.178.04): all 998 behaviour-row cases (TRELLIS.2 included, its rasteriser's render too), the self-test and 21/21 CuPy host fixtures passed. The rows took 247 s on the GPU, the CUDA benchmarks 196 s and the host tests 13 s. `--no-bench` leaves the benchmarks out; `--bench-size W H N` changes their size.

### Metal compile check

`metal_check.py` builds the same fixtures and compiles each generated `.metal`, plus the checked-in `generated/components.metal`, with `xcrun metal -std=metal3.0 -Werror`. It needs Xcode's Metal Toolchain (`xcodebuild -downloadComponent MetalToolchain`) but no GPU:

```sh
python3 portable/metal_check.py           # --std metal3.1 to change the standard, --keep DIR to keep sources and AIR
```

### Checked-in SAM 2 package

`generated/` is the components package used by `sam2_adapter.py`, `run_host.sh`, `tests/cuda.cu` and the GPU runners. Rebuild it from the IR fixture with `python3 portable/regenerate.py`; don't edit it by hand. Its manifest stores paths relative to `generated/`, and `program.qpel.json` there is the IR it was built from.

Precision and safety contracts: NUMERICS.md, FLOAT32.md, FMA.md, FLOAT16.md, REDUCTIONS.md, WORKGROUPS.md and BOUNDS.md. Older milestone counts in those documents describe their historical revisions.

See RELEASE.md for remaining release gates. No production-stability, universal translation or inference speedup claim is made.
