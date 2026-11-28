<p align="center">
  <img src="assets/qpel-icon.svg" width="160" height="160" alt="qpel: a cupel holding a bead of refined metal">
</p>

<h1 align="center">qpel</h1>

<p align="center">
  <b>A neutral intermediate language for GPU kernels: from CUDA or Metal to qpel, and from qpel to either.</b>
</p>

<p align="center">
  <a href="https://pypi.org/project/qpel/"><img alt="PyPI" src="https://img.shields.io/pypi/v/qpel.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab.svg">
  <img alt="Targets: CUDA and Metal" src="https://img.shields.io/badge/targets-CUDA%20%7C%20Metal-76b900.svg">
  <img alt="Status: Phase 7" src="https://img.shields.io/badge/status-phase%207-orange.svg">
</p>

---

qpel lets GPU code move between NVIDIA CUDA and Apple Metal in either direction, so that
applications and libraries run on the hardware people actually have. Kernels are imported
from unmodified CUDA or Metal source into a small typed IR. qpel checks the IR and generates
CUDA and Metal from it, with the same numerical results on both platforms.

```
 CUDA source ──┐                        ┌──▶ CUDA  (nvcc, sm_75–sm_90)
               ├──▶  qpel IR / .qpel  ──┤
Metal source ──┘    typed, validated    └──▶ Metal (Apple GPUs)
```

The name is a nod to the cupel, the small bone-ash cup in which assayers refine metal: the base
metals soak away and one bright bead is left. qpel takes kernels written for one platform and
leaves the part that both can run.

## Highlights

- **Real code, unmodified.** 385 of the 2364 kernels in a pinned corpus of real projects
  (llama.cpp, SAM 2, TRELLIS.2, mamba, pytorch_sparse, mmcv, gsplat, bitsandbytes) import as
  they are. Whole pipelines run against their originals:
  - llama.cpp's matrix multiply, `kernel_mul_mm`, for every quantised format, and its
    mixture-of-experts form, bit for bit on the Apple GPU;
  - gsplat's 3D Gaussian Splatting forward pass, from projection through spherical harmonics and
    tile intersection to rasterisation;
  - mamba's selective scan, and bitsandbytes' quantisation and dequantisation.
- **Specified numerics.** Integer widths from 8 to 64 bits, `f16`/`bf16` storage and float32
  arithmetic in which every operation is rounded separately. Exact math functions give the
  same bits on both platforms; transcendentals stay within stated ulp bounds. The rules are in
  [`NUMERICS.md`](portable/NUMERICS.md).
- **Checked by execution, not only by compilation.** Every original and every translation runs
  on a CPU CUDA simulator, on Apple GPUs, on [GPGPU-Sim](portable/gpgpusim/README.md) (a
  simulated NVIDIA GPU) and on a real NVIDIA Tesla T4. Differential fuzzing keeps comparing all
  of them.
- **Performance kept.** On a T4, qpel's CUDA takes x1.00 of the original's time (geometric
  mean over 83 programs, [benchmarks](portable/README.md)). Matrix tiles map WMMA and `mma.sync` to Metal's `simdgroup_matrix`.
- **Beyond single kernels.** Device-wide CUB (scans and radix, segmented and merge sorts) is
  provided as qpel pipelines, checked bit for bit against NVIDIA's CUB. A Triton frontend runs 49
  Triton kernels, Liger-Kernel's among them, on Metal.
- **`qpel verify` for any port.** It checks a hand-written or AI-generated port against its
  original, and `qpel mcp` serves the check to AI porting agents.
- **Platform bugs found and handled.** These include an Apple Metal compiler miscompilation,
  sign-of-zero and subnormal differences, and GPGPU-Sim bugs fixed and submitted upstream
  ([#154](https://github.com/accel-sim/gpgpu-sim_distribution/pull/154),
  [#156](https://github.com/accel-sim/gpgpu-sim_distribution/pull/156),
  [#159](https://github.com/accel-sim/gpgpu-sim_distribution/pull/159),
  [#160](https://github.com/accel-sim/gpgpu-sim_distribution/pull/160)).
- **Text syntax.** Programs can be written, printed and reviewed as `.qpel` text
  ([`TEXT.md`](portable/TEXT.md)). JetBrains MPS remains an optional structured editor that
  exports the same IR.

## Quick start

Requires Python 3.10+. The compiler itself uses only the standard library. Importing source
needs `clang`; the Metal importer also needs Xcode's `metal`. C++23 sources (a few of gsplat's)
need LLVM's clang 20 or later (`brew install llvm`, or `QPEL_LLVM_CLANG`).

From PyPI, as a `qpel` command:

```sh
pip install qpel                 # the compiler; "qpel[mps]" adds PyTorch for the Metal hosts, "qpel[verify]" NumPy
qpel build program.qpel --out build/
qpel translate kernels.cu --out translated/
```

From the repository, with the tests, the corpus and the differential tools:

```sh
git clone https://github.com/shatalov-alex/qpel.git && cd qpel/portable

# Build CUDA and Metal from a program written in qpel
python3 qpel.py build examples/saxpy.qpel --out /tmp/saxpy

# Import an existing kernel and translate it to both platforms
python3 qpel.py translate cuda_examples/grid_stride.cu --out /tmp/grid_stride

# Print any program as qpel text
python3 qpel.py import metal_examples/box_sum.metal --out /tmp/box_sum.qpel.json
python3 qpel.py print /tmp/box_sum.qpel.json
```

A program in qpel text:

```
module saxpy {
  fn axpy(a: f32, x: f32, y: f32) -> f32 {
    return (a * x) + y;
  }
  kernel saxpy(x: buffer<f32, read> @0, y: buffer<f32, read> @1, z: buffer<f32, write> @2,
               a: f32 @3, n: i64 @4) groups {
    let i: i64 = (cast<i64>(qpel.group.x) * cast<i64>(qpel.group_size.x)) + cast<i64>(qpel.local.x);
    if (i < n) {
      z[i] = axpy(a, x[i], y[i]);
    }
  }
}
```

## Status

qpel is in active development. Phases 0 to 6 of the [roadmap](portable/ROADMAP.md) are complete; Phase 7 is in progress. The source is public since 2026-11-23, and
[0.7.0](https://github.com/shatalov-alex/qpel/releases/tag/v0.7.0) is on [PyPI](https://pypi.org/project/qpel/).

| Phase | Scope | State |
|---|---|---|
| 0 | Consolidation: one compiler, IR as the source of truth | done |
| 1 | Offline Clang-based CUDA and Metal importers, round-trip matrix | done |
| 2 | IR v3, real-world corpus (llama.cpp), text syntax, fuzzing, NVIDIA gate | done |
| 3 | First real port: PyTorch extensions, header stubs, PyTorch op host, benchmarks | done |
| 4 | `qpel verify`: check any port (hand-written or AI-generated) against its original | done; source public 2026-11-23 |
| 5 | Shared memory, atomics, warp/simdgroup ops, block scans | done |
| 6 | Matrix tiles and performance | done |
| 7 | Library mapping (device-wide CUB first), Triton | in progress |

The corpus coverage report (`python3 -m frontends.corpus`) currently counts 519 of 2364
kernels (llama.cpp, SAM 2, TRELLIS.2 and the extensions corpus: mamba, pytorch_sparse, mmcv, gsplat,
bitsandbytes) whose import validates.

### Limitations

qpel is pre-1.0 and maintained by one person. Most kernels in the corpus do not import yet; the
report (`python3 -m frontends.corpus --details`) lists every blocker. Host code is translated
only through the PyTorch op host and the generated Python hosts: there is no host-API IR
(streams, events, `cudaMalloc`) yet. Float atomics give results that differ from run to run and
are checked within a tolerance. Apple M5's matrix tiles are untested. Hosted CI is not enabled;
`check_all.py` runs every check locally.

## Repository layout

| Path | Contents |
|---|---|
| [`portable/`](portable/) | The compiler: IR, validation, lowering, importers, hosts, tests |
| `portable/frontends/` | Clang-based CUDA and Metal importers, round-trip checks, corpus coverage |
| `portable/differential/` | Behaviour rows: CPU simulator, Apple GPU, GPGPU-Sim runners |
| `portable/fuzz/` | Differential fuzzer and saved regressions |
| `portable/gpgpusim/` | GPGPU-Sim Docker image, simulator fixes, self-test |
| `portable/corpus/` | Pinned, unmodified third-party sources with their licenses (see [NOTICE](NOTICE)) |
| `portable/frontends/triton/` | Triton frontend (TTIR to qpel) |
| `languages/`, `.mps/` | JetBrains MPS language (optional authoring frontend) |

## Testing

```sh
cd portable
python3 -m unittest discover -s tests -p 'test_*.py'   # compiler, importers, simulator
python3 check_all.py                                   # everything that needs no GPU
python3 check_all.py --gpu --nvidia --python <venv>    # plus Apple GPU and GPGPU-Sim
```

`check_all.py` also compiles the generated CUDA with NVIDIA's nvcc in Docker. See
[`portable/README.md`](portable/README.md) for the full list of checks and their prerequisites.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), and please follow the
[Code of Conduct](CODE_OF_CONDUCT.md). To report a security issue, see [SECURITY.md](SECURITY.md).

## License

qpel is licensed under the [Apache License 2.0](LICENSE). Third-party code in
`portable/corpus/` and the GPGPU-Sim patch keep their own licenses; see [NOTICE](NOTICE).
The qpel name and logo are not licensed for use; see [TRADEMARKS.md](TRADEMARKS.md).
