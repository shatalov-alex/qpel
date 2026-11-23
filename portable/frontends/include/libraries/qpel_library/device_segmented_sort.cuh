// qpel's device-wide segmented radix sort (LIBRARIES.md, device-wide CUB): what
// cub::DeviceSegmentedRadixSort::SortPairs computes, from the radix sort (device_radix_sort.cuh) and
// these small kernels. Each segment [begin[s], end[s]) is sorted on its own, stably, by bits
// [begin_bit, end_bit) of its keys; elements in no segment stay where they are.
//
//   init      positions[i] = i (where element i belongs), order[i] = i
//   mark      one thread per segment: positions[j] = begin[s] for j in the segment
//   radix sort of (keys, order) by the bits: sorted keys and where each came from (order1)
//   gather    places[j] = positions[order1[j]]: where the j-th sorted key belongs
//   radix sort of (places, order1) by all 32 bits, stably: within one place the keys stay in the
//             first sort's order, and the places come back in order, so a segment starting at b fills
//             [b, b + its length) (segments do not overlap) and an element in none returns to its index
//   gather    keys_out[i] = keys_in[order2[i]], values_out[i] = values_in[order2[i]]
//
// Both sorts are stable, so the result is the one CUB's segmented sort gives (in segments; CUB leaves
// elements in no segment undefined). As for the other library kernels, frontends/library.py writes the
// __global__ wrappers that call these bodies.
#pragma once

__device__ void qlib_segment_init_body(unsigned int* positions, unsigned int* order, uint3 n) {
  const unsigned int count = n.x * n.y * n.z;
  const unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < count) {
    positions[i] = i;
    order[i] = i;
  }
}

// begin and end are the segments' offsets (CUB's begin_offsets and end_offsets iterators); a segment
// whose end is not past its begin is empty.
template <typename O>
__device__ void qlib_segment_mark_body(O* begin, O* end, unsigned int* positions, uint3 segments) {
  const unsigned int count = segments.x * segments.y * segments.z;
  const unsigned int s = blockIdx.x * blockDim.x + threadIdx.x;
  if (s < count) {
    const unsigned int first = (unsigned int)begin[s];
    const unsigned int last = (unsigned int)end[s];
    for (unsigned int j = first; j < last; j = j + 1u) {
      positions[j] = first;
    }
  }
}

template <typename T>
__device__ void qlib_gather_body(T* from, T* to, unsigned int* order, uint3 n) {
  const unsigned int count = n.x * n.y * n.z;
  const unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < count) {
    to[i] = from[order[i]];
  }
}
