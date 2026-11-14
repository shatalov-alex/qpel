// AI-generated Metal port (Claude, 2026-11-14) of TRELLIS.2 o-voxel hilbert.cu, commit 75fbf01, MIT licence (Microsoft). Written without qpel.
#include <metal_stdlib>
using namespace metal;

// Expands a 10-bit integer into 30 bits by inserting 2 zeros after each bit.
static inline uint expandBits(uint v)
{
    v = (v * 0x00010001u) & 0xFF0000FFu;
    v = (v * 0x00000101u) & 0x0F00F00Fu;
    v = (v * 0x00000011u) & 0xC30C30C3u;
    v = (v * 0x00000005u) & 0x49249249u;
    return v;
}

// Removes 2 zeros after each bit in a 30-bit integer.
static inline uint extractBits(uint v)
{
    v = v & 0x49249249u;
    v = (v ^ (v >>  2)) & 0x030C30C3u;
    v = (v ^ (v >>  4)) & 0x0300F00Fu;
    v = (v ^ (v >>  8)) & 0x030000FFu;
    v = (v ^ (v >> 16)) & 0x000003FFu;
    return v;
}

kernel void hilbert_encode(
    constant ulong& N    [[buffer(0)]],
    device const uint* x [[buffer(1)]],
    device const uint* y [[buffer(2)]],
    device const uint* z [[buffer(3)]],
    device uint* codes   [[buffer(4)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id >= N) return;

    uint point[3] = {x[thread_id], y[thread_id], z[thread_id]};

    uint m = 1u << 9, q, p, t;

    // Inverse undo excess work
    q = m;
    while (q > 1u) {
        p = q - 1u;
        for (int i = 0; i < 3; i++) {
            if (point[i] & q) {
                point[0] ^= p;  // invert
            } else {
                t = (point[0] ^ point[i]) & p;
                point[0] ^= t;
                point[i] ^= t;
            }
        }
        q >>= 1;
    }

    // Gray encode
    for (int i = 1; i < 3; i++) {
        point[i] ^= point[i - 1];
    }
    t = 0u;
    q = m;
    while (q > 1u) {
        if (point[2] & q) {
            t ^= q - 1u;
        }
        q >>= 1;
    }
    for (int i = 0; i < 3; i++) {
        point[i] ^= t;
    }

    // Convert to 3D Hilbert code
    uint xx = expandBits(point[0]);
    uint yy = expandBits(point[1]);
    uint zz = expandBits(point[2]);

    codes[thread_id] = xx * 4u + yy * 2u + zz;
}

kernel void hilbert_decode(
    constant ulong& N        [[buffer(0)]],
    device const uint* codes [[buffer(1)]],
    device uint* x           [[buffer(2)]],
    device uint* y           [[buffer(3)]],
    device uint* z           [[buffer(4)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id >= N) return;

    uint point[3];
    point[0] = extractBits(codes[thread_id] >> 2);
    point[1] = extractBits(codes[thread_id] >> 1);
    point[2] = extractBits(codes[thread_id]);

    uint m = 2u << 9, q, p, t;

    // Gray decode by H ^ (H/2)
    t = point[2] >> 1;
    for (int i = 2; i > 0; i--) {
        point[i] ^= point[i - 1];
    }
    point[0] ^= t;

    // Undo excess work
    q = 2u;
    while (q != m) {
        p = q - 1u;
        for (int i = 2; i >= 0; i--) {
            if (point[i] & q) {
                point[0] ^= p;
            } else {
                t = (point[0] ^ point[i]) & p;
                point[0] ^= t;
                point[i] ^= t;
            }
        }
        q <<= 1;
    }

    x[thread_id] = point[0];
    y[thread_id] = point[1];
    z[thread_id] = point[2];
}
