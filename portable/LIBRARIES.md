# Libraries: qpel's CUB subset

CUDA libraries often do their work in `cub::BlockScan` and `cub::BlockReduce`: a block of threads
scans or reduces one value per thread through shared memory and warp shuffles. Metal has no CUB.
qpel imports these calls as ordinary qpel code: library functions that combine the values in the
same order as CUB, so a float scan has the same bits on CUDA, on Metal and in the original.

## What is supported

`frontends/include/qpel_cub/cub/cub.cuh` stands in for `<cub/cub.cuh>` when qpel imports a source
and when the CPU simulator runs the original. nvcc, GPGPU-Sim and real NVIDIA GPUs use NVIDIA's
own CUB, which the tests compare against.

| CUB | qpel |
|---|---|
| `cub::BlockScan<T, N, ALGORITHM>`, `BLOCK_SCAN_RAKING` (the default), `BLOCK_SCAN_RAKING_MEMOIZE`, `BLOCK_SCAN_WARP_SCANS` | `InclusiveSum(x, y)`, `InclusiveSum(x, y, total)`, `ExclusiveSum(x, y)`, `ExclusiveSum(x, y, total)`, `InclusiveScan(x, y, op)`, `InclusiveScan(x, y, op, total)`, `ExclusiveScan(x, y, initial, op)`, `ExclusiveScan(x, y, initial, op, total)` |
| `cub::BlockReduce<T, N>`, `BLOCK_REDUCE_WARP_REDUCTIONS` (the default) | `Sum(x)`, `Sum(x, valid)`, `Reduce(x, op)`, `Reduce(x, op, valid)`; the result is the block's in thread 0 only, as in CUB |
| `BlockScan` and `BlockReduce` on several items per thread (`T (&items)[ITEMS]`, IR version 10) | the same methods with item arrays (`InclusiveSum(items, out)`, `ExclusiveScan(items, out, initial, op, total)`, `Sum(items)`, `Reduce(items, op)`, ...): the thread's items reduced in order, a block scan of those partials, the items scanned from the thread's prefix, in CUB 2.5's order (`ThreadReduce`, `ThreadScanInclusive`/`Exclusive`) |
| `cub::BlockLoad<T, N, ITEMS, ALGORITHM>`, `cub::BlockStore<...>`: `DIRECT`, `STRIPED`, `VECTORIZE`, `TRANSPOSE`, `WARP_TRANSPOSE` | `Load(ptr, items)`, `Load(ptr, items, valid)`, `Load(ptr, items, valid, default)`, `Store(ptr, items)`, `Store(ptr, items, valid)`: the blocked arrangement (thread t's items are elements t * ITEMS + k), or the striped one (t + k * N) for `STRIPED`, read and written directly; their TempStorage is not used |
| `BlockScan<T, N, BLOCK_SCAN_WARP_SCANS>::InclusiveScan(items, out, op, prefix_callback)` with an operator functor without state and a block prefix callback (mamba's `SSMScanOp` and `SSMScanPrefixCallbackOp` on `float2` pairs) | the items reduced with `op`, an exclusive warp-scans scan of the partials whose aggregate warp 0 passes to the callback (every thread of warp 0 calls it; lane 0's result is shared through TempStorage and prefixes every thread's partial), then the items scanned from that prefix, in CUB 2.5's order (`WarpScanShfl`'s generic step, a `float2` shuffled field by field). The functors are read as functions of their fields (frontends/functors.py): `op(a, b)` a call, the callback's `running_prefix` a reference argument |
| `cub::Sum`, `cub::Max`, `cub::Min` | `a + b`, `CUB_MAX` (`b > a ? b : a`) and `CUB_MIN`, NaNs included |
| `__shared__ typename BlockScanT::TempStorage s;` | `shared<T, L>` of the length the algorithm needs (raking: 32 segments with CUB's padding and the total; warp scans: a total per warp and the block prefix) |
| `auto& s = reinterpret_cast<typename BlockScanT::TempStorage&>(smem_)`, views of dynamic shared memory | shared memory of its own per view (frontends/views.py); the original runs with the block's bytes, given at import (`shared={'smem_': bytes}`) |
| `BlockScanT(s).InclusiveSum(...)`, or a named `BlockScanT scan(s);` | a call of the library function |

T is `int`, `unsigned int` or `float` (and any copyable type, `float2` among them, for a scan with a functor), one item per thread or several; N is a multiple of 32 up to 256
(qpel's block limit), in a 1D block. Signed sums wrap, as CUB's `add.s32` does. `cub::DeviceScan`
and `cub::DeviceReduce` are declared, so host code that uses them parses, but they are host calls
(temporary storage, several launches) and not translated.

A load with `valid` and no default leaves the items from `valid` on as they were, as CUB's direct
and striped loads do; CUB's transposing loads put other threads' unset values there instead, so a
program must not read them (with a default, every algorithm gives the default).

Not yet: block prefix callbacks other than with warp scans on items (one-item and exclusive
forms, the raking algorithms), the timesliced load and store, loads through iterators, functors
with state as the operator, the raking `BlockReduce` algorithms, `WarpScan`/`WarpReduce` on their
own, and TempStorage in unions. A source using one of these does
not compile against qpel's header (a `static_assert` or a missing declaration names it), and
the importer reports it.

## How it works

The header writes `BlockScan` and `BlockReduce` as facades: a class built from its TempStorage,
whose every method is one statement forwarding to a function in `cub::qpel_detail`:
`output = block_scan<OP, T, N, ALGORITHM, EXCLUSIVE, SEEDED>(storage, input, initial, total);`.
Before import, `frontends/facades.py` replaces each method call in clang's AST by that statement
with the arguments substituted, so the importer only sees calls of template functions, which it
already imports one instantiation at a time. Those functions are plain CUDA: shared memory through
`qpel_shared_view<T>`, `__syncthreads()`, `__shfl_up_sync`, `__shfl_down_sync`, `__shfl_sync`,
local arrays and constexpr locals.

They follow CUB 2.5 (CUDA 12.6):

- **Raking scan** (`BlockScanRaking`): each thread puts its value in the grid (one padding slot per
  segment when a segment is even and longer than 2); the 32 threads of warp 0 each reduce a
  segment of N/32 values in order; a warp inclusive scan of those partials (shuffle up by 1, 2,
  4, 8, 16, each `op(lower lane, own)`); the exclusive prefix (shuffle up by 1, or `initial` in
  lane 0 when seeded, after `op(initial, inclusive)`); each segment scanned in order from it.
  N = 32 is the warp scan alone. qpel runs the warp scan in all warps (only warp 0 reads and
  writes the grid), because a shuffle needs all 32 lanes and qpel checks it statically; the
  extra warps compute values nothing uses.
- **Warp-scans scan** (`BlockScanWarpScans`): a warp scan in every warp; the last lane of each
  writes its total; every thread folds the totals of the warps before its own, in order.
- **Warp-reductions reduce** (`BlockReduceWarpReductions`): a shuffle-down reduction in every warp
  (offsets 1 to 16, `op(own, upper lane)` while the upper lane is valid), then thread 0 folds the
  warps' results in order. There is no barrier after it, as in CUB.

Barriers inside these functions are why IR version 7 allows `qpel.barrier()` in helpers
(CORE.md): the kernel and helper checks of qpel_uniform.py cover them.

## Evidence

- `tests/test_cub.py` runs `tests/cub_blocks.cu` (every method above, both scan algorithms, int,
  unsigned int and float, blocks of 32, 64, 96, 128, 160 and 256 threads) as the original on the
  CPU simulator, as qpel's CUDA there and as qpel's Metal on the Apple GPU, bit for bit. With
  `QPEL_NVIDIA=1` the original compiled by nvcc with NVIDIA's CUB 2.5 and qpel's CUDA run on
  GPGPU-Sim: all 56 runs agree bit for bit.
- `tests/cub_items.cu` does the same for several items per thread: `BlockLoad` and `BlockStore` in
  the direct, striped, transposed and warp-transposed algorithms (full and partial tiles), and
  every scan and reduction method on item arrays (blocks of 32 to 128 threads, 2 to 8 items, int,
  unsigned int and float), bit for bit on the CPU simulator, the Apple GPU and, against NVIDIA's
  CUB 2.5, on GPGPU-Sim (`test_cub.ItemRows`).
- `tests/ssm_scan.cu` is mamba's scan in miniature: `float2` pairs under `SSMScanOp` with the
  prefix callback carrying the running prefix from chunk to chunk (32 threads x 4 items as mamba
  launches it, 64 x 4, 32 x 1), bit for bit on the CPU simulator, the Apple GPU and, against
  NVIDIA's CUB 2.5, on GPGPU-Sim (`test_ssm_scan.py`). `tests/traits.cu` covers what mamba's
  kernels read around it: traits classes, a struct of pointers as the argument, views of dynamic
  shared memory (`test_traits.py`).
- llama.cpp's `cumsum_cub_kernel` (an unmodified corpus source, `GGML_CUDA_USE_CUB`) passes the
  behaviour rows on the CPU simulator, the Apple GPU and GPGPU-Sim (fixture `llama_cumsum_cub_f32`).
- On the way, GPGPU-Sim turned out to invert the in-range predicate of `shfl.sync` (our own shuffle
  fix, image `qpel12`); CUB's inline PTX reads it, nvcc's shuffles do not. Fixed in image `qpel13`
  (gpgpusim/README.md).
