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
| `cub::BlockLoad<T, N, ITEMS, ALGORITHM>`, `cub::BlockStore<...>`: `DIRECT`, `STRIPED`, `VECTORIZE`, `TRANSPOSE`, `WARP_TRANSPOSE` | `Load(ptr, items)`, `Load(ptr, items, valid)`, `Load(ptr, items, valid, default)`, `Store(ptr, items)`, `Store(ptr, items, valid)`: the blocked arrangement (thread t's items are elements t * ITEMS + k), or the striped one (t + k * N) for `STRIPED`. `TRANSPOSE` and `WARP_TRANSPOSE` move the tile through their TempStorage (shared memory, padded against bank conflicts when CUB pads: more than 4 items per thread, a power of two) as CUB does, so global memory is read and written in runs per warp (per block for `TRANSPOSE`); a warp's exchange orders only the warp (`__syncwarp`, CUB's `WARP_SYNC`), the block's the whole block. The others read and write directly, their TempStorage unused (2026-11-17: bitsandbytes' dequantisation was x1.6 to x3.5 on a T4 while qpel wrote its warp-transposing store directly, x0.97 to x0.99 since; 2026-11-19: the warp exchanges had block barriers, three per quantisation kernel where CUB has none, the likely cause of quantise FP4 x1.26 and NF4 x1.21 on the T4) |
| `BlockScan<T, N, BLOCK_SCAN_WARP_SCANS>::InclusiveScan(items, out, op, prefix_callback)` with an operator functor without state and a block prefix callback (mamba's `SSMScanOp` and `SSMScanPrefixCallbackOp` on `float2` pairs) | the items reduced with `op`, an exclusive warp-scans scan of the partials whose aggregate warp 0 passes to the callback (every thread of warp 0 calls it; lane 0's result is shared through TempStorage and prefixes every thread's partial), then the items scanned from that prefix, in CUB 2.5's order (`WarpScanShfl`'s generic step, a `float2` shuffled field by field). The functors are read as functions of their fields (frontends/functors.py): `op(a, b)` a call, the callback's `running_prefix` a reference argument |
| `cub::Sum`, `cub::Max`, `cub::Min` | `a + b`, `CUB_MAX` (`b > a ? b : a`) and `CUB_MIN`, NaNs included |
| `__shared__ typename BlockScanT::TempStorage s;` | `shared<T, L>` of the length the algorithm needs (raking: 32 segments with CUB's padding and the total; warp scans: a total per warp and the block prefix) |
| `auto& s = reinterpret_cast<typename BlockScanT::TempStorage&>(smem_)`, views of dynamic shared memory | shared memory of its own per view (frontends/views.py); the original runs with the block's bytes, given at import (`shared={'smem_': bytes}`) |
| `BlockScanT(s).InclusiveSum(...)`, or a named `BlockScanT scan(s);` | a call of the library function |

T is `int`, `unsigned int` or `float` (and any copyable type, `float2` among them, for a scan with a functor), one item per thread or several; N is a multiple of 32 up to 256
(qpel's block limit), in a 1D block. Signed sums wrap, as CUB's `add.s32` does. `cub::DeviceScan`
and `cub::DeviceReduce` are declared, so host code that uses them parses; the device-wide calls
are qpel library pipelines instead (Device-wide CUB, below).

A load with `valid` and no default leaves the items from `valid` on as they were, as CUB's direct
and striped loads do; CUB's transposing loads put other threads' unset values there instead, so a
program must not read them (with a default, every algorithm gives the default).

Not yet: block prefix callbacks other than with warp scans on items (one-item and exclusive
forms, the raking algorithms), the timesliced load and store, loads through iterators, functors
with state as the operator, the raking `BlockReduce` algorithms, `WarpScan`/`WarpReduce` on their
own, and TempStorage in unions. A source using one of these does
not compile against qpel's header (a `static_assert` or a missing declaration names it), and
the importer reports it.

## Device-wide CUB (Phase 7)

`cub::DeviceScan` and `cub::DeviceRadixSort` are host calls: each launches several kernels with
temporary storage between them. qpel writes those kernels as plain CUDA in
`frontends/include/libraries/qpel_library/` and imports them like any other source;
`frontends/library.py` gives, per call and element type, the CUDA that launches them (so the
importer sees one instance of each template) and the launch plan of their stages, with the
temporary storage as scratch buffers sized per tile of the input (IR version 21, CORE.md).

| CUB | qpel library | Stages |
|---|---|---|
| `DeviceScan::InclusiveSum`, `ExclusiveSum` on `int`, `unsigned int`, `long long`, `unsigned long long` | `device_scan(element, exclusive)`: `qlib_scan_reduce` (each tile of 1024's total, by a tree in shared memory), `qlib_scan_partials` (one group: the totals' exclusive prefix, 256 at a time), `qlib_scan_tiles` (each tile from its prefix) | 3 |
| `DeviceRadixSort::SortPairs(keys_in, keys_out, values_in, values_out, n, begin_bit, end_bit)`, keys of those four types, values of those or `float` | `device_radix_sort_pairs(key, value)`: per 8 bits of the key type, `qlib_radix_histogram` (each tile of 256's count of every digit, digit-major), the device scan of the counts, `qlib_radix_scatter` (each key to its digit's start plus its rank among the tile's keys with that digit before it: stable), `qlib_radix_advance` (the pass number, kept in a one-word buffer, goes up) | 6 per pass: 24 for 32-bit keys, 48 for 64-bit |
| `DeviceSegmentedRadixSort::SortPairs(keys_in, keys_out, values_in, values_out, n, segments, begin_offsets, end_offsets, begin_bit, end_bit)`, the same keys and values, offsets of the four integer types | `device_segmented_radix_sort_pairs(key, value, offset)` (`device_segmented_sort.cuh`): `qlib_segment_init` (each element's place is its index), `qlib_segment_mark` (one thread per segment: its elements' place is its begin), the radix sort of the keys and their indices by the bits, `qlib_gather` of the sorted keys' places, the radix sort of the places (all 32 bits, carrying the indices), `qlib_gather` of the keys and values from the indices | 77 for 64-bit keys, 53 for 32-bit |
| `DeviceMergeSort::SortPairs(keys, values, n, less)` and `StableSortPairs`, in place, the same keys and values | `device_merge_sort_pairs(key, value)`: the radix sort over every bit of the key type, in place | as the radix sort |

Integer sums wrap and do not depend on their order, and a stable sort has one result, so both are
CUB's bit for bit. So is the segmented sort, in its segments: both of its sorts are stable, the second
puts every key back in its segment (a segment's elements all have its begin as their place, and the places
come back in order, the segments not overlapping), in the first sort's order; an element in no segment has
its own index as its place and returns to it (CUB leaves those undefined). gsplat's one `offsets` array of
segments + 1 is `begin_offsets = offsets[:-1]`, `end_offsets = offsets[1:]`. NVIDIA's merge sort is not
stable: with equal keys its values may come in any order, of which the stable sort is one (StableSortPairs
exactly); the keys are NVIDIA's bit for bit. Signed keys have their sign bit flipped before their digits are taken, as CUB
does, so negative keys sort first. A pass whose bits start at or after `end_bit` copies its keys in
order; the passes alternate between the outputs and a scratch pair, and their number is even, so
the result is in the outputs. Sums are made in the unsigned type of the same width (`qlib_add`), so
the library's CUDA is defined C++ when the CPU simulator runs it with UBSan.

Not offered: float scans (CUB's add in an order that varies with the timing of its decoupled
look-back), `SortKeys` without values, descending sorts, merge sorts by other comparisons, and `DeviceReduce`. Not yet fast: the
scatter finds a key's rank by reading the tile's 256 digits, where CUB uses warp ballots and
match instructions; the segmented sort marks a segment's places with one thread.

A port's launch plan names a call as a library stage, `{"library": "cub::DeviceRadixSort::SortPairs",
"key": "i64", "value": "i32", "bindings": {"keys_in": ..., "keys_out": ..., "values_in": ..., "values_out": ...,
"begin_bit": ..., "end_bit": ..., "n": shape}}` (or `cub::DeviceScan::InclusiveSum` and `ExclusiveSum` with
`"input"`, `"element"` and bindings `in`, `out`, `n`; `cub::DeviceSegmentedRadixSort::SortPairs` with `"offset"`
as well and bindings `begin_offsets`, `end_offsets` and `segments`, a shape, besides the radix sort's;
`cub::DeviceMergeSort::SortPairs` or `StableSortPairs` with `"compare": "less"` and bindings `keys`, `values`, `n`); `library.expand(source, plan)` appends the call's kernels
to the source (one `__global__` wrapper per stage, named after the stage's prefix, so several calls of
different types share a source) and puts its stages and scratch buffers in the plan. The original then runs
with the same library kernels on the CPU simulator, as qpel's CUB subset stands in for CUB's block
algorithms.

**Pilot: gsplat's forward tile intersection (2026-11-22).** gsplat's host code counts each projected
gaussian's tiles (`intersect_tile_kernel`), takes the counts' cumulative sum (`torch::cumsum`, int32 into
int64), reads the total back, writes each intersection's 64-bit key (image | tile | depth) and gaussian id
(`intersect_tile_kernel` again), sorts them (`cub::DeviceRadixSort::SortPairs` over 32 + tile and image bits)
and finds each tile's first intersection (`intersect_offset_kernel`). That is two qpel pipelines,
`corpus/gsplat/plans/intersect_tile_count.plan.json` (4 stages) and `intersect_tile_emit.plan.json` (50), of
gsplat's own kernels, unmodified, and the library's scan and sort. On the CPU simulator, the Apple GPU and
GPGPU-Sim every output agrees with the original bit for bit, over scenes with several images, partial tiles,
culled gaussians, a tile mask and nothing on screen; and the intersections feed gsplat's rasterizer (the
`gsplat_rasterize_fwd` corpus program), so on the Apple GPU gsplat's forward pass runs from projected
gaussians to pixels (tests/test_gsplat_intersect.py).

Evidence: `tests/test_device_scan.py` (9 variants, sizes 1 to 300000 so that the partials are scanned in
several rounds, int32 into int64 as torch's cumsum) and `tests/test_device_radix_sort.py` (4 key and value
types, three bit ranges each, keys over the full range and from 16 values so that stability shows) run the
library's CUDA as the original on the CPU simulator, qpel's CUDA there and qpel's Metal on the Apple GPU,
against numpy's wrapping cumsum and stable argsort. With `QPEL_NVIDIA=1` qpel's CUDA runs on GPGPU-Sim
against the same oracle at every size, and against NVIDIA's own CUB where CUB runs right in the simulator
(32-bit scans within one of its tiles, 32-bit sorts, 64-bit sorts of up to 257 keys; gsplat's 64-bit sort
of more is wrong there: gpgpusim/README.md, Open, and `qpel-cub.patch` for what it needed to run at all).
NVIDIA's CUB at every size runs on a T4 (differential/hardware.py): on 2026-11-23 the scans (63 cases), radix sorts
(144), segmented radix sorts (60) and merge sorts (20) all matched it on a Tesla T4.

## What the corpus calls (the library-mapping report)

`python3 -m frontends.library_map` counts every CUDA runtime, CUB, cuBLAS, NCCL and PyTorch CUDA API
identifier in the corpus's sources, kernels and host code alike, and says for each what qpel maps it to
(corpus/library_map.json; tests/test_library_map.py fails when it changes or when an identifier is not
classified). On 2026-11-23, of 926 uses:

| Status | Uses | What |
| --- | --- | --- |
| library | 57 | CUB's block-level templates (from qpel_cub, imported with the kernel) and the four device-wide algorithms above, ATen's `at::cuda::cub` wrapper included |
| host | 672 | PyTorch's streams and device guards (`getCurrentCUDAStream` 195, `CUDAGuard` 90), `cudaStream_t`, launch status checks: qpel's PyTorch op host runs the op on PyTorch's MPS device and current stream, so none of them needs a counterpart |
| planned | 156 | host code for the host-API IR: per-kernel shared memory opt-in (`cudaFuncSetAttribute`, 26, mostly gsplat), stream-ordered allocation, events and stream waits (gsplat), memsets and copies, device attributes, llama.cpp's CUDA graphs and programmatic dependent launch |
| unmapped | 41 | cuBLAS and cuBLASLt (handles in llama.cpp; bitsandbytes' int8 `cublasLtMatmul` and `cublasGemmEx` paths: MPSGraph matmul would stand in), NCCL in llama.cpp, and eight CUB helpers (`ShuffleDown`, `BlockExchange`, ...) in mamba's `reverse_scan.cuh`, which only its backward kernels use |

So the libraries the corpus needs and qpel lacks are cuBLAS (bitsandbytes' matmuls), NCCL (llama.cpp's
multi-GPU path) and mamba's backward-scan helpers; the rest is host code, most of it gsplat's.

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
