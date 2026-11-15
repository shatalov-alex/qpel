// qpel's CUB subset: cub::BlockScan and cub::BlockReduce for one item per thread, written as
// plain CUDA that qpel imports. Each method forwards to a function in cub::qpel_detail, which
// combines values in the order CUB 2.5 does (BlockScanRaking, BlockScanWarpScans,
// BlockReduceWarpReductions with WarpScanShfl and WarpReduceShfl), so float results agree bit
// for bit with the real library. The importer reads this file in place of NVIDIA's; the CPU
// simulator runs originals with it; nvcc and GPGPU-Sim use the real CUB (LIBRARIES.md).
//
// Supported: 1D blocks whose size is a multiple of 32 (1 to 32 warps), T = int, unsigned int or
// float, the operators cub::Sum, cub::Max and cub::Min. Every method ends with the barriers CUB
// has; as with CUB, a second use of the same TempStorage needs a __syncthreads() first.
#pragma once

#ifndef QPEL_CUB_SUBSET
#define QPEL_CUB_SUBSET 1

#include <type_traits>

namespace cub {

enum BlockScanAlgorithm { BLOCK_SCAN_RAKING, BLOCK_SCAN_RAKING_MEMOIZE, BLOCK_SCAN_WARP_SCANS };
enum BlockReduceAlgorithm { BLOCK_REDUCE_RAKING_COMMUTATIVE_ONLY, BLOCK_REDUCE_RAKING, BLOCK_REDUCE_WARP_REDUCTIONS };

// The operators, as CUB defines them: a + b; Max and Min through CUB_MAX and CUB_MIN.
struct Sum { enum { qpel_op = 0 }; };
struct Max { enum { qpel_op = 1 }; };
struct Min { enum { qpel_op = 2 }; };

#ifndef QPEL_SHARED_VIEW
template <class T> using qpel_shared_view = T*;  // qpel's spelling of a pointer into shared memory (backends/cuda.h)
#endif

namespace qpel_detail {

// An operator of the subset (cub::Sum, Max, Min) has a qpel_op; other functors reach the
// declared-only overloads, which the importer reports.
template <class Op, class = void> struct known : std::false_type {};
template <class Op> struct known<Op, decltype((void)Op::qpel_op)> : std::true_type {};
template <class Op> using if_known = typename std::enable_if<known<Op>::value, int>::type;
template <class Op> using if_unknown = typename std::enable_if<!known<Op>::value, int>::type;

template <int OP, typename T>
__device__ __forceinline__ T combine(T a, T b) {
  if constexpr (OP == 0 && T(-1) < T(0) && T(1) / T(2) == T(0)) {
    return (T)((unsigned)a + (unsigned)b);  // int: wraps, as CUB's add.s32 does (a + b would be undefined)
  } else if constexpr (OP == 0) {
    return a + b;
  } else if constexpr (OP == 1) {
    return (b > a) ? b : a;  // CUB_MAX(a, b)
  } else {
    return (b < a) ? b : a;  // CUB_MIN(a, b)
  }
}

// WarpScanShfl::InclusiveScan: steps 1, 2, 4, 8, 16, each op(the value `offset` lanes below, own).
template <int OP, typename T>
__device__ __forceinline__ T warp_inclusive(T x, unsigned lane) {
  for (int offset = 1; offset < 32; offset *= 2) {
    T below = __shfl_up_sync(0xffffffffu, x, offset);
    if (lane >= (unsigned)offset) {
      x = combine<OP, T>(below, x);
    }
  }
  return x;
}

// WarpReduceShfl::Reduce: steps 1, 2, 4, 8, 16, each op(own, the value `offset` lanes above) for
// the lanes up to last_lane; lane 0 has the warp's reduction.
template <int OP, typename T>
__device__ __forceinline__ T warp_reduce(T x, unsigned lane, int last_lane) {
  for (int offset = 1; offset < 32; offset *= 2) {
    T above = __shfl_down_sync(0xffffffffu, x, offset);
    if ((int)lane + offset <= last_lane) {
      x = combine<OP, T>(x, above);
    }
  }
  return x;
}

template <int THREADS>
struct Raking {
  enum {
    SEGMENT = THREADS / 32,  // shared partials each of the 32 raking threads reduces in order
    PADDING = (SEGMENT % 2 == 0 && SEGMENT > 2) ? 1 : 0,  // BlockRakingLayout's bank-conflict padding
    GRID = 32 * (SEGMENT + PADDING),
    LENGTH = GRID + 1  // the raking grid, then the block aggregate
  };
};

// BlockScanRaking (and _MEMOIZE, which only caches): the inclusive scan, or with `exclusive` the
// exclusive one; `seeded` uses `initial` (ExclusiveScan with an initial value, ExclusiveSum).
// Every warp runs the warp scan, so its shuffles have all 32 lanes; only warp 0 (the raking
// threads) reads and writes the grid.
template <int OP, typename T, int THREADS, bool EXCLUSIVE, bool SEEDED>
__device__ __forceinline__ T raking_scan(qpel_shared_view<T> storage, T input, T initial, T& aggregate) {
  constexpr int SEGMENT = THREADS / 32;  // (Raking<THREADS>, written out: qpel reads constexpr locals)
  constexpr int PADDING = (SEGMENT % 2 == 0 && SEGMENT > 2) ? 1 : 0;
  constexpr int GRID = 32 * (SEGMENT + PADDING);
  const unsigned tid = threadIdx.x;
  const unsigned lane = tid % 32u;
  if constexpr (THREADS == 32) {  // WARP_SYNCHRONOUS: the warp scan alone
    T inclusive = warp_inclusive<OP, T>(input, lane);
    aggregate = __shfl_sync(0xffffffffu, inclusive, 31);
    if constexpr (!EXCLUSIVE) {
      return inclusive;
    } else {
      if constexpr (SEEDED) {
        inclusive = combine<OP, T>(initial, inclusive);
      }
      T exclusive = __shfl_up_sync(0xffffffffu, inclusive, 1);
      if constexpr (SEEDED) {
        if (lane == 0u) {
          exclusive = initial;
        }
      }
      return exclusive;
    }
  } else {
    const unsigned place = tid + (unsigned)PADDING * (tid / (unsigned)SEGMENT);
    storage[place] = input;
    __syncthreads();
    const bool raking = tid < 32u;
    const unsigned base = lane * (unsigned)(SEGMENT + PADDING);
    T segment[SEGMENT];
    T partial = input;
    if (raking) {  // Upsweep: the segment, reduced in order
      for (int k = 0; k < SEGMENT; ++k) {
        segment[k] = storage[base + (unsigned)k];
      }
      partial = segment[0];
      for (int k = 1; k < SEGMENT; ++k) {
        partial = combine<OP, T>(partial, segment[k]);
      }
    }
    T inclusive = warp_inclusive<OP, T>(partial, lane);
    T total = __shfl_sync(0xffffffffu, inclusive, 31);
    if constexpr (SEEDED) {
      inclusive = combine<OP, T>(initial, inclusive);
    }
    T prefix = __shfl_up_sync(0xffffffffu, inclusive, 1);
    if constexpr (SEEDED) {
      if (lane == 0u) {
        prefix = initial;
      }
    }
    if (raking) {  // Downsweep: the segment scanned in order from the warp's exclusive prefix
      const bool apply = SEEDED || tid != 0u;
      if constexpr (EXCLUSIVE) {
        T running = segment[0];
        if (apply) {
          running = combine<OP, T>(prefix, running);
        }
        storage[base] = prefix;
        for (int k = 1; k < SEGMENT; ++k) {
          T next = combine<OP, T>(running, segment[k]);
          storage[base + (unsigned)k] = running;
          running = next;
        }
      } else {
        T running = segment[0];
        if (apply) {
          running = combine<OP, T>(prefix, running);
        }
        storage[base] = running;
        for (int k = 1; k < SEGMENT; ++k) {
          running = combine<OP, T>(running, segment[k]);
          storage[base + (unsigned)k] = running;
        }
      }
      if (tid == 31u) {
        storage[GRID] = total;
      }
    }
    __syncthreads();
    aggregate = storage[GRID];
    return storage[place];
  }
}

// BlockScanWarpScans: a scan in every warp; the last lane of each shares its warp's total; every
// thread adds the totals of the warps before its own in order.
template <int OP, typename T, int THREADS, bool EXCLUSIVE, bool SEEDED>
__device__ __forceinline__ T warp_scans_scan(qpel_shared_view<T> storage, T input, T initial, T& aggregate) {
  constexpr int WARPS = THREADS / 32;
  const unsigned tid = threadIdx.x;
  const unsigned lane = tid % 32u;
  const unsigned warp = tid / 32u;
  T inclusive = warp_inclusive<OP, T>(input, lane);
  T exclusive = __shfl_up_sync(0xffffffffu, inclusive, 1);
  if (lane == 31u) {
    storage[warp] = inclusive;
  }
  __syncthreads();
  T prefix = storage[0];
  T total = storage[0];
  for (int w = 1; w < WARPS; ++w) {
    if (warp == (unsigned)w) {
      prefix = total;
    }
    total = combine<OP, T>(total, storage[(unsigned)w]);
  }
  aggregate = total;
  if constexpr (!EXCLUSIVE) {
    if (warp != 0u) {
      inclusive = combine<OP, T>(prefix, inclusive);
    }
    return inclusive;
  } else {
    if constexpr (SEEDED) {
      prefix = combine<OP, T>(initial, prefix);
      if (warp == 0u) {
        prefix = initial;
      }
      exclusive = combine<OP, T>(prefix, exclusive);
      if (lane == 0u) {
        exclusive = prefix;
      }
    } else {
      if (warp != 0u) {
        exclusive = combine<OP, T>(prefix, exclusive);
        if (lane == 0u) {
          exclusive = prefix;
        }
      }
    }
    return exclusive;
  }
}

template <int OP, typename T, int THREADS, int ALGORITHM, bool EXCLUSIVE, bool SEEDED>
__device__ __forceinline__ T block_scan(qpel_shared_view<T> storage, T input, T initial, T& aggregate) {
  if constexpr (ALGORITHM == BLOCK_SCAN_WARP_SCANS) {
    return warp_scans_scan<OP, T, THREADS, EXCLUSIVE, SEEDED>(storage, input, initial, aggregate);
  } else {
    return raking_scan<OP, T, THREADS, EXCLUSIVE, SEEDED>(storage, input, initial, aggregate);
  }
}

// The same without the block aggregate.
template <int OP, typename T, int THREADS, int ALGORITHM, bool EXCLUSIVE, bool SEEDED>
__device__ __forceinline__ T block_scan_value(qpel_shared_view<T> storage, T input, T initial) {
  T aggregate = input;
  return block_scan<OP, T, THREADS, ALGORITHM, EXCLUSIVE, SEEDED>(storage, input, initial, aggregate);
}

// BlockReduceWarpReductions: a reduction in every warp, then thread 0 adds the warps' results in
// order. Only thread 0's result is the block's (as in CUB); there is no barrier after it.
template <int OP, typename T, int THREADS>
__device__ __forceinline__ T block_reduce(qpel_shared_view<T> storage, T input, int valid) {
  constexpr int WARPS = THREADS / 32;
  const unsigned tid = threadIdx.x;
  const unsigned lane = tid % 32u;
  const unsigned warp = tid / 32u;
  const int offset = (int)warp * 32;
  const int last = (offset + 32 <= valid) ? 31 : valid - offset - 1;
  T result = warp_reduce<OP, T>(input, lane, last);
  if (lane == 0u) {
    storage[warp] = result;
  }
  __syncthreads();
  if (tid == 0u) {
    for (int w = 1; w < WARPS; ++w) {
      if (w * 32 < valid) {
        result = combine<OP, T>(result, storage[(unsigned)w]);
      }
    }
  }
  return result;
}

}  // namespace qpel_detail

template <typename T, int BLOCK_DIM_X, BlockScanAlgorithm ALGORITHM = BLOCK_SCAN_RAKING, int BLOCK_DIM_Y = 1,
          int BLOCK_DIM_Z = 1, int LEGACY_PTX_ARCH = 0>
class BlockScan {
  static_assert(BLOCK_DIM_Y == 1 && BLOCK_DIM_Z == 1, "qpel's CUB subset: 1D blocks");
  static_assert(BLOCK_DIM_X % 32 == 0 && BLOCK_DIM_X <= 1024, "qpel's CUB subset: whole warps");

 public:
  typedef T TempStorage[ALGORITHM == BLOCK_SCAN_WARP_SCANS ? BLOCK_DIM_X / 32 : qpel_detail::Raking<BLOCK_DIM_X>::LENGTH];

  __device__ __forceinline__ BlockScan(TempStorage& storage) : storage(storage) {}

  __device__ __forceinline__ void InclusiveSum(T input, T& output) {
    output = qpel_detail::block_scan_value<0, T, BLOCK_DIM_X, ALGORITHM, false, false>(storage, input, T());
  }
  __device__ __forceinline__ void InclusiveSum(T input, T& output, T& block_aggregate) {
    output = qpel_detail::block_scan<0, T, BLOCK_DIM_X, ALGORITHM, false, false>(storage, input, T(), block_aggregate);
  }
  __device__ __forceinline__ void ExclusiveSum(T input, T& output) {
    output = qpel_detail::block_scan_value<0, T, BLOCK_DIM_X, ALGORITHM, true, true>(storage, input, T(0));
  }
  __device__ __forceinline__ void ExclusiveSum(T input, T& output, T& block_aggregate) {
    output = qpel_detail::block_scan<0, T, BLOCK_DIM_X, ALGORITHM, true, true>(storage, input, T(0), block_aggregate);
  }
  template <typename Op, qpel_detail::if_known<Op> = 0>
  __device__ __forceinline__ void InclusiveScan(T input, T& output, Op) {
    output = qpel_detail::block_scan_value<Op::qpel_op, T, BLOCK_DIM_X, ALGORITHM, false, false>(storage, input, T());
  }
  template <typename Op, qpel_detail::if_known<Op> = 0>
  __device__ __forceinline__ void InclusiveScan(T input, T& output, Op, T& block_aggregate) {
    output = qpel_detail::block_scan<Op::qpel_op, T, BLOCK_DIM_X, ALGORITHM, false, false>(storage, input, T(),
                                                                                           block_aggregate);
  }
  template <typename Op, qpel_detail::if_known<Op> = 0>
  __device__ __forceinline__ void ExclusiveScan(T input, T& output, T initial_value, Op) {
    output = qpel_detail::block_scan_value<Op::qpel_op, T, BLOCK_DIM_X, ALGORITHM, true, true>(storage, input, initial_value);
  }
  template <typename Op, qpel_detail::if_known<Op> = 0>
  __device__ __forceinline__ void ExclusiveScan(T input, T& output, T initial_value, Op, T& block_aggregate) {
    output = qpel_detail::block_scan<Op::qpel_op, T, BLOCK_DIM_X, ALGORITHM, true, true>(storage, input, initial_value,
                                                                                         block_aggregate);
  }

  // Declared only (not in the subset yet): several items per thread, and block prefix callbacks.
  template <int ITEMS> __device__ void InclusiveSum(T (&input)[ITEMS], T (&output)[ITEMS]);
  template <int ITEMS> __device__ void ExclusiveSum(T (&input)[ITEMS], T (&output)[ITEMS]);
  template <int ITEMS, typename Op> __device__ void InclusiveScan(T (&input)[ITEMS], T (&output)[ITEMS], Op op);
  template <int ITEMS, typename Op, typename Callback>
  __device__ void InclusiveScan(T (&input)[ITEMS], T (&output)[ITEMS], Op op, Callback& prefix);
  template <int ITEMS, typename Op, typename Callback>
  __device__ void ExclusiveScan(T (&input)[ITEMS], T (&output)[ITEMS], Op op, Callback& prefix);
  template <typename Op, typename Callback> __device__ void InclusiveScan(T input, T& output, Op op, Callback& prefix);
  template <typename Op, qpel_detail::if_unknown<Op> = 0> __device__ void InclusiveScan(T input, T& output, Op op);
  template <typename Op, qpel_detail::if_unknown<Op> = 0>
  __device__ void ExclusiveScan(T input, T& output, T initial_value, Op op);
  template <typename Op, typename Callback> __device__ void ExclusiveScan(T input, T& output, Op op, Callback& prefix);

 private:
  T* storage;
};

template <typename T, int BLOCK_DIM_X, BlockReduceAlgorithm ALGORITHM = BLOCK_REDUCE_WARP_REDUCTIONS, int BLOCK_DIM_Y = 1,
          int BLOCK_DIM_Z = 1, int LEGACY_PTX_ARCH = 0>
class BlockReduce {
  static_assert(BLOCK_DIM_Y == 1 && BLOCK_DIM_Z == 1, "qpel's CUB subset: 1D blocks");
  static_assert(BLOCK_DIM_X % 32 == 0 && BLOCK_DIM_X <= 1024, "qpel's CUB subset: whole warps");
  static_assert(ALGORITHM == BLOCK_REDUCE_WARP_REDUCTIONS, "qpel's CUB subset: BLOCK_REDUCE_WARP_REDUCTIONS");

 public:
  typedef T TempStorage[BLOCK_DIM_X / 32];

  __device__ __forceinline__ BlockReduce(TempStorage& storage) : storage(storage) {}

  __device__ __forceinline__ T Sum(T input) {
    return qpel_detail::block_reduce<0, T, BLOCK_DIM_X>(storage, input, BLOCK_DIM_X);
  }
  __device__ __forceinline__ T Sum(T input, int num_valid) {
    return qpel_detail::block_reduce<0, T, BLOCK_DIM_X>(storage, input, num_valid);
  }
  template <typename Op, qpel_detail::if_known<Op> = 0>
  __device__ __forceinline__ T Reduce(T input, Op) {
    return qpel_detail::block_reduce<Op::qpel_op, T, BLOCK_DIM_X>(storage, input, BLOCK_DIM_X);
  }
  template <typename Op, qpel_detail::if_known<Op> = 0>
  __device__ __forceinline__ T Reduce(T input, Op, int num_valid) {
    return qpel_detail::block_reduce<Op::qpel_op, T, BLOCK_DIM_X>(storage, input, num_valid);
  }

  // Declared only (not in the subset yet): several items per thread, and other operators.
  template <int ITEMS> __device__ T Sum(T (&inputs)[ITEMS]);
  template <int ITEMS, typename Op> __device__ T Reduce(T (&inputs)[ITEMS], Op op);
  template <typename Op, qpel_detail::if_unknown<Op> = 0> __device__ T Reduce(T input, Op op);
  template <typename Op, qpel_detail::if_unknown<Op> = 0> __device__ T Reduce(T input, Op op, int num_valid);

 private:
  T* storage;
};

// Declared only, so that sources using them parse; the importer reports their calls (LIBRARIES.md).
enum BlockLoadAlgorithm { BLOCK_LOAD_DIRECT, BLOCK_LOAD_STRIPED, BLOCK_LOAD_VECTORIZE, BLOCK_LOAD_TRANSPOSE,
                          BLOCK_LOAD_WARP_TRANSPOSE, BLOCK_LOAD_WARP_TRANSPOSE_TIMESLICED };
enum BlockStoreAlgorithm { BLOCK_STORE_DIRECT, BLOCK_STORE_STRIPED, BLOCK_STORE_VECTORIZE, BLOCK_STORE_TRANSPOSE,
                           BLOCK_STORE_WARP_TRANSPOSE, BLOCK_STORE_WARP_TRANSPOSE_TIMESLICED };
template <typename T, int BLOCK_DIM_X, int ITEMS_PER_THREAD, BlockLoadAlgorithm ALGORITHM = BLOCK_LOAD_DIRECT,
          int BLOCK_DIM_Y = 1, int BLOCK_DIM_Z = 1, int LEGACY_PTX_ARCH = 0>
class BlockLoad {
 public:
  struct TempStorage { T qpel_unsupported[BLOCK_DIM_X * ITEMS_PER_THREAD]; };
  __device__ BlockLoad(TempStorage& storage);
  template <typename Input> __device__ void Load(Input input, T (&items)[ITEMS_PER_THREAD]);
  template <typename Input> __device__ void Load(Input input, T (&items)[ITEMS_PER_THREAD], int valid);
  template <typename Input, typename Default>
  __device__ void Load(Input input, T (&items)[ITEMS_PER_THREAD], int valid, Default fill);
};
template <typename T, int BLOCK_DIM_X, int ITEMS_PER_THREAD, BlockStoreAlgorithm ALGORITHM = BLOCK_STORE_DIRECT,
          int BLOCK_DIM_Y = 1, int BLOCK_DIM_Z = 1, int LEGACY_PTX_ARCH = 0>
class BlockStore {
 public:
  struct TempStorage { T qpel_unsupported[BLOCK_DIM_X * ITEMS_PER_THREAD]; };
  __device__ BlockStore(TempStorage& storage);
  template <typename Output> __device__ void Store(Output output, T (&items)[ITEMS_PER_THREAD]);
  template <typename Output> __device__ void Store(Output output, T (&items)[ITEMS_PER_THREAD], int valid);
};

template <typename Key> struct DoubleBuffer {
  Key* d_buffers[2];
  int selector;
  __host__ __device__ DoubleBuffer(Key*, Key*);
  __host__ __device__ Key* Current();
  __host__ __device__ Key* Alternate();
};
#define QPEL_CUB_DEVICE(NAME, ...) struct NAME { template <typename... A> static cudaError_t __VA_ARGS__(A...); };
QPEL_CUB_DEVICE(DeviceSegmentedRadixSort, SortKeys); QPEL_CUB_DEVICE(DeviceMergeSort, SortKeys);
QPEL_CUB_DEVICE(DeviceSelect, Flagged); QPEL_CUB_DEVICE(DeviceRunLengthEncode, Encode);
QPEL_CUB_DEVICE(DeviceSegmentedReduce, Sum); QPEL_CUB_DEVICE(DeviceHistogram, HistogramEven);
QPEL_CUB_DEVICE(DevicePartition, Flagged);
#undef QPEL_CUB_DEVICE
struct DeviceRadixSort {
  template <typename... A> static cudaError_t SortKeys(A...);
  template <typename... A> static cudaError_t SortPairs(A...);
};

// Device-wide algorithms are host calls (temporary storage, several launches): declared so that
// host code using them parses; qpel does not translate them (a library mapping, ROADMAP Phase 7).
struct DeviceScan {
  template <typename In, typename Out, typename N>
  static cudaError_t InclusiveSum(void* temp, size_t& temp_bytes, In in, Out out, N count, cudaStream_t stream = 0);
  template <typename In, typename Out, typename N>
  static cudaError_t ExclusiveSum(void* temp, size_t& temp_bytes, In in, Out out, N count, cudaStream_t stream = 0);
};
struct DeviceReduce {
  template <typename In, typename Out, typename N>
  static cudaError_t Sum(void* temp, size_t& temp_bytes, In in, Out out, N count, cudaStream_t stream = 0);
};

}  // namespace cub

#endif  // QPEL_CUB_SUBSET
