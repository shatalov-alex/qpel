# Behaviour rows: originals against what qpel makes of them

`python3 -m differential.matrix` runs the four behaviour rows of the round-trip
matrix (ROADMAP.md, Phase 1) for every original source: the hand-written
`cuda_examples/` and `metal_examples/` pairs, corpus files with a fixture
(SAM 2's unmodified CUDA and its hand port to Metal), and corpus programs: single
kernels of an unmodified library file pinned as their own fixture (28 llama.cpp
operations from `arange.cu`, `scale.cu`, `fill.cu`, `clamp.cu`, `softcap.cu` and
`unary.cu`).

For each original, qpel imports it, builds CUDA and Metal from the result, and
runs the same test cases (`cases.py`) through all three:

| Original | Runs on | Compared with |
|---|---|---|
| CUDA | CPU simulator | qpel's CUDA on the simulator (CUDA → qpel → CUDA), qpel's Metal on the Apple GPU (CUDA → qpel → Metal) |
| Metal | Apple GPU | qpel's Metal on the GPU (Metal → qpel → Metal), qpel's CUDA on the simulator (Metal → qpel → CUDA) |

Every output must match exactly; floats are compared bit for bit, with subnormal
results read as zeros of their sign (Apple GPUs flush them). A program that uses a
bounded math function (`exp`, `tanh`, ...; NUMERICS.md) matches a float element within
2 × 4 ulp, or within that many epsilons of max(1, |value|), which covers cancellation
after such a function (llama.cpp's gelu computes `1 + tanh(...)` near -1). Outputs a
kernel leaves unwritten (`Case.defined`) are not compared.

- **CPU simulator** (`simulate.py`, `include/cuda_runtime.h`): CUDA sources compiled as C++ with clang++,
  `-ffp-contract=off` and UBSan, so an original that executes undefined behaviour fails loudly instead of
  producing a reference. Each launch runs all blocks and threads one after another: one valid CUDA schedule,
  exact for race-free kernels and for atomics whose combined effect does not depend on order. Barriers and
  shared memory are rejected (they need threads that run together; Phases 3 and 5).
- **Originals are driven from qpel's launch plan.** The importer records how each original argument maps onto
  qpel's (`report['arguments']`): the same resource, a count that became a shape (it receives W·H·N), or
  scalar extents (they receive the shape's members). Host functions are blanked out by their source ranges, so
  an unmodified file with PyTorch host code runs as is. Original launch geometry from host code can be given
  per kernel (`grids` in `corpus/corpus.json`); SAM 2 runs with its own half-resolution grids.
- **Library headers.** An original that includes its library's headers (llama.cpp's `common.cuh`) runs on a
  copy of the source tree in which the bodies of CPU functions in those headers are replaced by `;`
  (`report['header_host_bodies']`): they may contain `<<<>>>` launches, which are not C++, and their
  declarations stay for other host code. `include/` mirrors the SDK surface such headers use (vector types,
  half2, bfloat16, runtime and driver API declarations); warp and block collectives abort if a simulated kernel
  reaches them.
- **Metal library originals.** `compile_shader` takes one string and cannot pass function-constant values, so the
  harness pastes the library's quoted includes in (as ggml embeds its own headers) and turns each function
  constant the program specialises into a constant with that value, as Metal does when building the pipeline.
  Apple's `round` gives +0 where C gives -0 (NUMERICS.md), so zero results compare without their sign when the
  original is Metal source. Byte buffers are filled and compared as the element type they hold (`inputs`,
  `outputs` in corpus.json); an input may give an inclusive range for its integers (`"i32 0..7"`, indices that
  mean something only in range). Threadgroup memory the host sizes (`"shared"`) becomes a kernel-scope array of
  that length in the original's text (`metal_text.sized_threadgroups`), as `setThreadgroupMemoryLength` would.
- **Metal originals** (`metal.py`) run through `torch.mps.compile_shader`, one thread per shape element in the
  plan's threadgroups (whole groups for Groups kernels), which is how qpel's generated Metal host dispatches,
  with `#pragma clang fp contract(off)`: as written, like the simulator's `-ffp-contract=off`.
- **Test vectors** (`cases.py`) respect each original's preconditions, so no original executes undefined
  behaviour: `scale_offset` keeps `(int)i * offset` within int, SAM 2 gets even sizes and masks of varied
  density, floats stay normal (subnormal handling differs between CPUs and Apple GPUs). A corpus program can
  constrain scalar arguments (`values` in `corpus.json`): `volume` is the dispatch shape's element count (a
  count the buffers must hold), `W`/`H`/`N` one of its sizes, an integer itself.

**NVIDIA execution** (`gpgpusim.py`, `../gpgpusim/README.md`): the same sources as real CUDA
host programs on GPGPU-Sim, a simulated NVIDIA GPU in Docker. Its rows are
`nvidia: cuda original` and `nvidia: <dialect> -> qpel -> cuda`, against the same references
(`python3 -m differential.gpgpusim`; `check_all.py --nvidia`).

`check_all.py` runs the simulator rows always and the Apple GPU rows with `--gpu`
(`--cpu` here does the same). `tests/test_differential.py` checks that a wrong
translation and undefined behaviour in an original are both caught.
