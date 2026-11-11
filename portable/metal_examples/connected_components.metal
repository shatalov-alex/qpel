// A hand port of SAM 2's connected-components kernels (corpus/sam2/
// connected_components.cu) to Metal, kept as close to the CUDA as Metal
// allows: atomic buffers are atomic_int and are read and written with
// relaxed atomics, and the thread position comes from [[thread_position_in_grid]].
// It must import to the same qpel program as the CUDA original.
#include <metal_stdlib>
using namespace metal;

namespace cc2d {

template <typename T>
inline uchar hasBit(T bitmap, uchar pos) {
  return (bitmap >> pos) & 1;
}

inline int32_t find(device atomic_int* s_buf, int32_t n) {
  while (atomic_load_explicit(s_buf + n, memory_order_relaxed) != n)
    n = atomic_load_explicit(s_buf + n, memory_order_relaxed);
  return n;
}

inline int32_t find_n_compress(device atomic_int* s_buf, int32_t n) {
  const int32_t id = n;
  while (atomic_load_explicit(s_buf + n, memory_order_relaxed) != n) {
    n = atomic_load_explicit(s_buf + n, memory_order_relaxed);
    atomic_store_explicit(s_buf + id, n, memory_order_relaxed);
  }
  return n;
}

inline void union_(device atomic_int* s_buf, int32_t a, int32_t b) {
  bool done;
  do {
    a = find(s_buf, a);
    b = find(s_buf, b);

    if (a < b) {
      int32_t old = atomic_fetch_min_explicit(s_buf + b, a, memory_order_relaxed);
      done = (old == b);
      b = old;
    } else if (b < a) {
      int32_t old = atomic_fetch_min_explicit(s_buf + a, b, memory_order_relaxed);
      done = (old == a);
      a = old;
    } else
      done = true;

  } while (!done);
}

kernel void init_labeling(device atomic_int* label [[buffer(0)]],
                          constant uint32_t& W [[buffer(1)]],
                          constant uint32_t& H [[buffer(2)]],
                          uint2 position [[thread_position_in_grid]]) {
  const uint32_t row = position.y * 2;
  const uint32_t col = position.x * 2;
  const uint32_t idx = row * W + col;

  if (row < H && col < W)
    atomic_store_explicit(label + idx, idx, memory_order_relaxed);
}

kernel void merge(device uint8_t* img [[buffer(0)]],
                  device atomic_int* label [[buffer(1)]],
                  constant uint32_t& W [[buffer(2)]],
                  constant uint32_t& H [[buffer(3)]],
                  uint2 position [[thread_position_in_grid]]) {
  const uint32_t row = position.y * 2;
  const uint32_t col = position.x * 2;
  const uint32_t idx = row * W + col;

  if (row >= H || col >= W)
    return;

  uint32_t P = 0;

  if (img[idx])
    P |= 0x777;
  if (row + 1 < H && img[idx + W])
    P |= 0x777 << 4;
  if (col + 1 < W && img[idx + 1])
    P |= 0x777 << 1;

  if (col == 0)
    P &= 0xEEEE;
  if (col + 1 >= W)
    P &= 0x3333;
  else if (col + 2 >= W)
    P &= 0x7777;

  if (row == 0)
    P &= 0xFFF0;
  if (row + 1 >= H)
    P &= 0xFF;

  if (P > 0) {
    // If need check about top-left pixel(if flag the first bit) and hit the
    // top-left pixel
    if (hasBit(P, 0) && img[idx - W - 1]) {
      union_(label, idx, idx - 2 * W - 2); // top left block
    }

    if ((hasBit(P, 1) && img[idx - W]) || (hasBit(P, 2) && img[idx - W + 1]))
      union_(label, idx, idx - 2 * W); // top bottom block

    if (hasBit(P, 3) && img[idx + 2 - W])
      union_(label, idx, idx - 2 * W + 2); // top right block

    if ((hasBit(P, 4) && img[idx - 1]) || (hasBit(P, 8) && img[idx + W - 1]))
      union_(label, idx, idx - 2); // just left block
  }
}

kernel void compression(device atomic_int* label [[buffer(0)]],
                        constant int32_t& W [[buffer(1)]],
                        constant int32_t& H [[buffer(2)]],
                        uint2 position [[thread_position_in_grid]]) {
  const uint32_t row = position.y * 2;
  const uint32_t col = position.x * 2;
  const uint32_t idx = row * W + col;

  if (row < H && col < W)
    find_n_compress(label, idx);
}

kernel void final_labeling(device const uint8_t* img [[buffer(0)]],
                           device atomic_int* label [[buffer(1)]],
                           constant int32_t& W [[buffer(2)]],
                           constant int32_t& H [[buffer(3)]],
                           uint2 position [[thread_position_in_grid]]) {
  const uint32_t row = position.y * 2;
  const uint32_t col = position.x * 2;
  const uint32_t idx = row * W + col;

  if (row >= H || col >= W)
    return;

  int32_t y = atomic_load_explicit(label + idx, memory_order_relaxed) + 1;

  if (img[idx])
    atomic_store_explicit(label + idx, y, memory_order_relaxed);
  else
    atomic_store_explicit(label + idx, 0, memory_order_relaxed);

  if (col + 1 < W) {
    if (img[idx + 1])
      atomic_store_explicit(label + (idx + 1), y, memory_order_relaxed);
    else
      atomic_store_explicit(label + (idx + 1), 0, memory_order_relaxed);

    if (row + 1 < H) {
      if (img[idx + W + 1])
        atomic_store_explicit(label + (idx + W + 1), y, memory_order_relaxed);
      else
        atomic_store_explicit(label + (idx + W + 1), 0, memory_order_relaxed);
    }
  }

  if (row + 1 < H) {
    if (img[idx + W])
      atomic_store_explicit(label + (idx + W), y, memory_order_relaxed);
    else
      atomic_store_explicit(label + (idx + W), 0, memory_order_relaxed);
  }
}

kernel void init_counting(device atomic_int* label [[buffer(0)]],
                          device atomic_int* count_init [[buffer(1)]],
                          constant int32_t& W [[buffer(2)]],
                          constant int32_t& H [[buffer(3)]],
                          uint2 position [[thread_position_in_grid]]) {
  const uint32_t row = position.y;
  const uint32_t col = position.x;
  const uint32_t idx = row * W + col;

  if (row >= H || col >= W)
    return;

  int32_t y = atomic_load_explicit(label + idx, memory_order_relaxed);
  if (y > 0) {
    int32_t count_idx = y - 1;
    atomic_fetch_add_explicit(count_init + count_idx, 1, memory_order_relaxed);
  }
}

kernel void final_counting(device atomic_int* label [[buffer(0)]],
                           device atomic_int* count_init [[buffer(1)]],
                           device int32_t* count_final [[buffer(2)]],
                           constant int32_t& W [[buffer(3)]],
                           constant int32_t& H [[buffer(4)]],
                           uint2 position [[thread_position_in_grid]]) {
  const uint32_t row = position.y;
  const uint32_t col = position.x;
  const uint32_t idx = row * W + col;

  if (row >= H || col >= W)
    return;

  int32_t y = atomic_load_explicit(label + idx, memory_order_relaxed);
  if (y > 0) {
    int32_t count_idx = y - 1;
    count_final[idx] = atomic_load_explicit(count_init + count_idx, memory_order_relaxed);
  } else {
    count_final[idx] = 0;
  }
}

} // namespace cc2d
