// qpel's device-wide scan (LIBRARIES.md, device-wide CUB): what cub::DeviceScan::InclusiveSum and
// ExclusiveSum compute, as plain CUDA that qpel imports. An input of n elements is cut into tiles of
// 1024 (256 threads x 4 consecutive items), in three kernels:
//
//   reduce    each tile's total, by a tree in shared memory, into partials[tile]
//   partials  one group: the exclusive prefix of the totals, in place, 256 at a time
//   tiles     each tile scanned from its prefix: the thread's items in order, after an exclusive
//             scan of the threads' totals (Hillis-Steele in shared memory)
//
// The kernels themselves are written per call by frontends/library.py, each a __global__ wrapper
// named for its stage that declares the shared memory and calls a body here, so one source can hold
// several scans (and sorts) of different types. IN is the input's type and T the sum's (an integer:
// int, unsigned int, long long, unsigned long long; IN converts to T, as torch's cumsum of int32
// gives int64). Integer sums wrap and do not depend on their order, so the result is CUB's exactly;
// CUB's float scans add in an order that varies with the timing of its decoupled look-back, and
// are not offered. Each body takes a shape as a uint3; the input has ceil(x * y * z / PER) * SCALE
// elements (PER = SCALE = 1: the shape's own; a radix sort scans 256 counts per tile of 256 keys).
// partials holds one element per tile (`in n / 1024`, IR version 21).
//
// Buffer parameters are not const: a pipeline binds one buffer (one type) to kernels that read and
// write it, as a sort's scratch is read in one pass and written in the next. The names start with
// qlib_: qpel_ is the backends' own, which the importer leaves out.
#pragma once

#define QLIB_SCAN_THREADS 256u
#define QLIB_SCAN_ITEMS 4u
#define QLIB_SCAN_TILE 1024u

// a + b, wrapping: in the unsigned type of the same width, so that signed sums are defined in C++
// (as add.s32 and qpel's i32 + wrap; the CPU simulator runs this code with UBSan)
__device__ unsigned int qlib_add(unsigned int a, unsigned int b) { return a + b; }
__device__ int qlib_add(int a, int b) { return (int)((unsigned int)a + (unsigned int)b); }
__device__ unsigned long long qlib_add(unsigned long long a, unsigned long long b) { return a + b; }
__device__ long long qlib_add(long long a, long long b) { return (long long)((unsigned long long)a + (unsigned long long)b); }

// The exclusive sum of x over the block's 256 threads, and the block's total; s is 256 elements of
// shared memory, free on entry and on return.
template <typename T>
__device__ T qlib_block_exclusive_sum(T* s, T x, T& total) {
  const unsigned t = threadIdx.x;
  s[t] = x;
  __syncthreads();
  for (unsigned offset = 1u; offset < QLIB_SCAN_THREADS; offset = offset * 2u) {
    const T lower = t >= offset ? s[t - offset] : (T)0;
    __syncthreads();
    s[t] = qlib_add(s[t], lower);
    __syncthreads();
  }
  const T exclusive = t > 0u ? s[t - 1u] : (T)0;
  total = s[QLIB_SCAN_THREADS - 1u];
  __syncthreads();
  return exclusive;
}

template <typename IN, typename T, unsigned int PER, unsigned int SCALE>
__device__ void qlib_scan_reduce_body(T* sums, IN* in, T* partials, uint3 n) {
  const unsigned count = (n.x * n.y * n.z + PER - 1u) / PER * SCALE;
  const unsigned t = threadIdx.x;
  const unsigned first = blockIdx.x * QLIB_SCAN_TILE + t * QLIB_SCAN_ITEMS;
  T sum = (T)0;
  for (unsigned k = 0u; k < QLIB_SCAN_ITEMS; ++k) {
    if (first + k < count) {
      sum = qlib_add(sum, (T)in[first + k]);
    }
  }
  sums[t] = sum;
  __syncthreads();
  for (unsigned half = QLIB_SCAN_THREADS / 2u; half > 0u; half = half / 2u) {
    if (t < half) {
      sums[t] = qlib_add(sums[t], sums[t + half]);
    }
    __syncthreads();
  }
  if (t == 0u) {
    partials[blockIdx.x] = sums[0];
  }
}

// (the partials kernel's loop, whose bound is the shape's, is in its wrapper: frontends/library.py, since qpel
// checks a barrier in a helper with the helper's parameters taken as varying)

template <typename IN, typename T, int EXCLUSIVE, unsigned int PER, unsigned int SCALE>
__device__ void qlib_scan_tiles_body(T* s, IN* in, T* out, T* partials, uint3 n) {
  const unsigned count = (n.x * n.y * n.z + PER - 1u) / PER * SCALE;
  const unsigned t = threadIdx.x;
  const unsigned first = blockIdx.x * QLIB_SCAN_TILE + t * QLIB_SCAN_ITEMS;
  T items[QLIB_SCAN_ITEMS];
  T sum = (T)0;
  for (unsigned k = 0u; k < QLIB_SCAN_ITEMS; ++k) {
    items[k] = first + k < count ? (T)in[first + k] : (T)0;
    sum = qlib_add(sum, items[k]);
  }
  T total = (T)0;
  T running = qlib_add(partials[blockIdx.x], qlib_block_exclusive_sum(s, sum, total));
  for (unsigned k = 0u; k < QLIB_SCAN_ITEMS; ++k) {
    if (EXCLUSIVE) {
      if (first + k < count) {
        out[first + k] = running;
      }
      running = qlib_add(running, items[k]);
    } else {
      running = qlib_add(running, items[k]);
      if (first + k < count) {
        out[first + k] = running;
      }
    }
  }
}
