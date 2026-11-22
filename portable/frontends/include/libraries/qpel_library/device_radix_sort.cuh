// qpel's device-wide radix sort (LIBRARIES.md, device-wide CUB): what cub::DeviceRadixSort::SortPairs
// and SortKeys compute, as plain CUDA kernels that qpel imports. A stable least-significant-digit
// sort, 8 bits a pass, over tiles of 256 keys (one per thread):
//
//   histogram         each tile's count of every digit, digit-major: counts[digit * tiles + tile]
//   (device_scan.cuh) the exclusive sum of the counts: where each digit of each tile starts
//   scatter           each key (and value) to its digit's start plus its rank among the keys of its
//                     tile with that digit before it, from shared memory: stable
//   advance           one thread: the pass number, in state[0], goes up by one
//
// As for the scan, frontends/library.py writes the kernels, wrappers that declare the shared memory
// (256 digits) and call the bodies here.
//
// A pass sorts bits [begin_bit + 8 p, begin_bit + 8 p + 8) of the keys, for p = state[0]; a pass that
// starts at or after end_bit copies its keys and values in order (the plan has as many passes as the
// key type's bits need, an even number, so the result lands in the buffers the keys came from). Keys
// are unsigned or signed 32- or 64-bit integers; a signed key's sign bit is flipped, as CUB does, so
// negative keys sort first. The sort is stable, so its result is CUB's exactly.
//
// The names start with qlib_: qpel_ is the backends' own, which the importer leaves out.
#pragma once

#define QLIB_RADIX_THREADS 256u
#define QLIB_RADIX_BITS 8u
#define QLIB_RADIX_DIGITS 256u

// The key's bits as an unsigned integer, with a signed key's sign bit flipped (CUB's twiddle).
__device__ unsigned int qlib_radix_bits(unsigned int key) { return key; }
__device__ unsigned int qlib_radix_bits(int key) { return (unsigned int)key ^ 0x80000000u; }
__device__ unsigned long long qlib_radix_bits(unsigned long long key) { return key; }
__device__ unsigned long long qlib_radix_bits(long long key) { return (unsigned long long)key ^ 0x8000000000000000ull; }

// The digit of this pass, or 256 when the pass sorts nothing (its first bit at or after end_bit).
template <typename K>
__device__ unsigned int qlib_radix_digit(K key, unsigned int pass, unsigned int begin_bit, unsigned int end_bit) {
  const unsigned int shift = begin_bit + pass * QLIB_RADIX_BITS;
  if (shift >= end_bit) {
    return QLIB_RADIX_DIGITS;
  }
  const unsigned int width = end_bit - shift < QLIB_RADIX_BITS ? end_bit - shift : QLIB_RADIX_BITS;
  return (unsigned int)(qlib_radix_bits(key) >> shift) & ((1u << width) - 1u);
}

template <typename K>
__device__ void qlib_radix_histogram_body(unsigned int* digits, K* keys, unsigned int* counts, unsigned int* state,
                                     unsigned int begin_bit, unsigned int end_bit, uint3 n) {
  const unsigned int count = n.x * n.y * n.z;
  const unsigned int tiles = (count + QLIB_RADIX_THREADS - 1u) / QLIB_RADIX_THREADS;
  const unsigned int t = threadIdx.x, i = blockIdx.x * QLIB_RADIX_THREADS + t;
  const unsigned int pass = state[0];
  // a key past the end, or a pass that sorts nothing: every key counts as digit 0 (kept in order)
  unsigned int digit = i < count ? qlib_radix_digit(keys[i], pass, begin_bit, end_bit) : QLIB_RADIX_DIGITS + 1u;
  if (digit == QLIB_RADIX_DIGITS) {
    digit = 0u;
  }
  digits[t] = digit;
  __syncthreads();
  unsigned int found = 0u;  // thread t counts digit t in its tile
  for (unsigned int j = 0u; j < QLIB_RADIX_THREADS; ++j) {
    if (digits[j] == t) {
      found = found + 1u;
    }
  }
  counts[t * tiles + blockIdx.x] = found;
}

template <typename K, typename V>
__device__ void qlib_radix_scatter_body(unsigned int* digits, K* keys_in, K* keys_out, V* values_in, V* values_out,
                                   unsigned int* starts, unsigned int* state, unsigned int begin_bit, unsigned int end_bit,
                                   uint3 n) {
  const unsigned int count = n.x * n.y * n.z;
  const unsigned int tiles = (count + QLIB_RADIX_THREADS - 1u) / QLIB_RADIX_THREADS;
  const unsigned int t = threadIdx.x, i = blockIdx.x * QLIB_RADIX_THREADS + t;
  const unsigned int pass = state[0];
  unsigned int digit = QLIB_RADIX_DIGITS + 1u;
  if (i < count) {
    digit = qlib_radix_digit(keys_in[i], pass, begin_bit, end_bit);
    if (digit == QLIB_RADIX_DIGITS) {
      digit = 0u;
    }
  }
  digits[t] = digit;
  __syncthreads();
  if (i < count) {
    unsigned int rank = 0u;  // keys of this tile with this digit before this one
    for (unsigned int j = 0u; j < t; ++j) {
      if (digits[j] == digit) {
        rank = rank + 1u;
      }
    }
    const unsigned int to = starts[digit * tiles + blockIdx.x] + rank;
    keys_out[to] = keys_in[i];
    values_out[to] = values_in[i];
  }
}

__device__ void qlib_radix_advance_body(unsigned int* state) {
  if (threadIdx.x == 0u && blockIdx.x == 0u) {
    state[0] = state[0] + 1u;
  }
}
