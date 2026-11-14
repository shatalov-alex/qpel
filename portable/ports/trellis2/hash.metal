// AI-generated Metal port (Claude, 2026-11-14) of TRELLIS.2 o-voxel hash.cu, commit 75fbf01, MIT licence (Microsoft). Written without qpel.
// Only the K = uint32_t, V = uint32_t instantiation is ported.
#include <metal_stdlib>
using namespace metal;

#define EMPTY_KEY 0xFFFFFFFFu   // std::numeric_limits<uint32_t>::max()
#define EMPTY_VAL 0xFFFFFFFFu   // std::numeric_limits<uint32_t>::max()

// 32 bit Murmur3 finaliser; result reduced modulo N in 64-bit like CUDA's size_t.
static inline ulong hash_u32(uint k, ulong N)
{
    k ^= k >> 16;
    k *= 0x85ebca6bu;
    k ^= k >> 13;
    k *= 0xc2b2ae35u;
    k ^= k >> 16;
    return (ulong)k % N;
}

// Equivalent of CUDA atomicCAS(addr, compare, val): returns the old value.
static inline uint atomic_cas_u32(device atomic_uint* addr, uint compare, uint val)
{
    uint expected = compare;
    while (!atomic_compare_exchange_weak_explicit(addr, &expected, val,
                                                  memory_order_relaxed,
                                                  memory_order_relaxed)) {
        // A weak CAS may fail spuriously; only a real mismatch ends the loop.
        if (expected != compare) return expected;
        expected = compare;
    }
    return compare;
}

static inline void linear_probing_insert(
    device atomic_uint* hashmap_keys,
    device uint* hashmap_values,
    const uint key,
    const uint value,
    const ulong N)
{
    ulong slot = hash_u32(key, N);
    while (true) {
        uint prev = atomic_cas_u32(&hashmap_keys[slot], EMPTY_KEY, key);
        if (prev == EMPTY_KEY || prev == key) {
            hashmap_values[slot] = value;
            return;
        }
        slot = slot + 1;
        if (slot >= N) slot = 0;
    }
}

static inline uint linear_probing_lookup(
    device const uint* hashmap_keys,
    device const uint* hashmap_values,
    const uint key,
    const ulong N)
{
    ulong slot = hash_u32(key, N);
    while (true) {
        uint prev = hashmap_keys[slot];
        if (prev == EMPTY_KEY) {
            return EMPTY_VAL;
        }
        if (prev == key) {
            return hashmap_values[slot];
        }
        slot = slot + 1;
        if (slot >= N) slot = 0;
    }
}

// (size_t)b * W * H * D + (size_t)x * H * D + (size_t)y * D + z, in 64-bit
// unsigned wrap-around arithmetic (ints sign-extend on conversion, as in C++).
static inline ulong flat_index(int b, int x, int y, int z, int W, int H, int D)
{
    return (ulong)b * (ulong)W * (ulong)H * (ulong)D
         + (ulong)x * (ulong)H * (ulong)D
         + (ulong)y * (ulong)D
         + (ulong)z;
}

kernel void hashmap_insert_cuda_kernel_u32_u32(
    constant ulong& N                  [[buffer(0)]],
    constant ulong& M                  [[buffer(1)]],
    device atomic_uint* hashmap_keys   [[buffer(2)]],
    device uint* hashmap_values        [[buffer(3)]],
    device const uint* keys            [[buffer(4)]],
    device const uint* values          [[buffer(5)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id < M) {
        uint key = keys[thread_id];
        uint value = values[thread_id];
        linear_probing_insert(hashmap_keys, hashmap_values, key, value, N);
    }
}

kernel void hashmap_lookup_cuda_kernel_u32_u32(
    constant ulong& N                  [[buffer(0)]],
    constant ulong& M                  [[buffer(1)]],
    device const uint* hashmap_keys    [[buffer(2)]],
    device const uint* hashmap_values  [[buffer(3)]],
    device const uint* keys            [[buffer(4)]],
    device uint* values                [[buffer(5)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id < M) {
        uint key = keys[thread_id];
        values[thread_id] = linear_probing_lookup(hashmap_keys, hashmap_values, key, N);
    }
}

kernel void hashmap_insert_3d_cuda_kernel_u32_u32(
    constant ulong& N                  [[buffer(0)]],
    constant ulong& M                  [[buffer(1)]],
    constant int& W                    [[buffer(2)]],
    constant int& H                    [[buffer(3)]],
    constant int& D                    [[buffer(4)]],
    device atomic_uint* hashmap_keys   [[buffer(5)]],
    device uint* hashmap_values        [[buffer(6)]],
    device const int* coords           [[buffer(7)]],
    device const uint* values          [[buffer(8)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id < M) {
        ulong base = (ulong)thread_id * 4;
        int b = coords[base + 0];
        int x = coords[base + 1];
        int y = coords[base + 2];
        int z = coords[base + 3];
        ulong flat_idx = flat_index(b, x, y, z, W, H, D);
        uint key = (uint)flat_idx;
        uint value = values[thread_id];
        linear_probing_insert(hashmap_keys, hashmap_values, key, value, N);
    }
}

kernel void hashmap_lookup_3d_cuda_kernel_u32_u32(
    constant ulong& N                  [[buffer(0)]],
    constant ulong& M                  [[buffer(1)]],
    constant int& W                    [[buffer(2)]],
    constant int& H                    [[buffer(3)]],
    constant int& D                    [[buffer(4)]],
    device const uint* hashmap_keys    [[buffer(5)]],
    device const uint* hashmap_values  [[buffer(6)]],
    device const int* coords           [[buffer(7)]],
    device uint* values                [[buffer(8)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id < M) {
        ulong base = (ulong)thread_id * 4;
        int b = coords[base + 0];
        int x = coords[base + 1];
        int y = coords[base + 2];
        int z = coords[base + 3];
        if (x < 0 || x >= W || y < 0 || y >= H || z < 0 || z >= D) {
            values[thread_id] = EMPTY_VAL;
            return;
        }
        ulong flat_idx = flat_index(b, x, y, z, W, H, D);
        uint key = (uint)flat_idx;
        values[thread_id] = linear_probing_lookup(hashmap_keys, hashmap_values, key, N);
    }
}

kernel void hashmap_insert_3d_idx_as_val_cuda_kernel_u32_u32(
    constant ulong& N                  [[buffer(0)]],
    constant ulong& M                  [[buffer(1)]],
    constant int& W                    [[buffer(2)]],
    constant int& H                    [[buffer(3)]],
    constant int& D                    [[buffer(4)]],
    device atomic_uint* hashmap        [[buffer(5)]],
    device uint* values                [[buffer(6)]],
    device const int* coords           [[buffer(7)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id < M) {
        ulong base = (ulong)thread_id * 4;
        int b = coords[base + 0];
        int x = coords[base + 1];
        int y = coords[base + 2];
        int z = coords[base + 3];
        ulong flat_idx = flat_index(b, x, y, z, W, H, D);
        uint key = (uint)flat_idx;
        uint value = thread_id;
        linear_probing_insert(hashmap, values, key, value, N);
    }
}
