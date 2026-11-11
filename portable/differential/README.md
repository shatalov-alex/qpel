# Behaviour rows: originals against what qpel makes of them

`python3 -m differential.matrix` runs the four behaviour rows of the round-trip
matrix (ROADMAP.md, Phase 1) for every original source: the hand-written
`cuda_examples/` and `metal_examples/` pairs, and corpus files with a fixture
(SAM 2's unmodified CUDA and its hand port to Metal).

For each original, qpel imports it, builds CUDA and Metal from the result, and
runs the same test cases (`cases.py`) through all three:

| Original | Runs on | Compared with |
|---|---|---|
| CUDA | CPU simulator | qpel's CUDA on the simulator (CUDA → qpel → CUDA), qpel's Metal on the Apple GPU (CUDA → qpel → Metal) |
| Metal | Apple GPU | qpel's Metal on the GPU (Metal → qpel → Metal), qpel's CUDA on the simulator (Metal → qpel → CUDA) |

Every output must match exactly; floats are compared bit for bit.

- **CPU simulator** (`simulate.py`, `include/cuda_runtime.h`): CUDA sources compiled as C++ with clang++,
  `-ffp-contract=off` and UBSan, so an original that executes undefined behaviour fails loudly instead of
  producing a reference. Each launch runs all blocks and threads one after another: one valid CUDA schedule,
  exact for race-free kernels and for atomics whose combined effect does not depend on order. Barriers and
  shared memory are rejected (they need threads that run together; Phase 3).
- **Originals are driven from qpel's launch plan.** The importer records how each original argument maps onto
  qpel's (`report['arguments']`): the same resource, a count that became a shape (it receives W·H·N), or
  scalar extents (they receive the shape's members). Host functions are blanked out by their source ranges, so
  an unmodified file with PyTorch host code runs as is. Original launch geometry from host code can be given
  per kernel (`grids` in `corpus/corpus.json`); SAM 2 runs with its own half-resolution grids.
- **Metal originals** (`metal.py`) run through `torch.mps.compile_shader`, one thread per shape element in the
  plan's threadgroups, which is how qpel's generated Metal host dispatches.
- **Test vectors** (`cases.py`) respect each original's preconditions, so no original executes undefined
  behaviour: `scale_offset` keeps `(int)i * offset` within int, SAM 2 gets even sizes and masks of varied
  density, floats stay normal (subnormal handling differs between CPUs and Apple GPUs).

`check_all.py` runs the simulator rows always and the Apple GPU rows with `--gpu`
(`--cpu` here does the same). `tests/test_differential.py` checks that a wrong
translation and undefined behaviour in an original are both caught.
