// AI-generated Metal port (Claude, 2026-11-14) of TRELLIS.2 o-voxel z_order.cu, commit 75fbf01, MIT licence (Microsoft). Written without qpel.
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

kernel void z_order_encode(
    constant ulong& N    [[buffer(0)]],
    device const uint* x [[buffer(1)]],
    device const uint* y [[buffer(2)]],
    device const uint* z [[buffer(3)]],
    device uint* codes   [[buffer(4)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id >= N) return;

    uint xx = expandBits(x[thread_id]);
    uint yy = expandBits(y[thread_id]);
    uint zz = expandBits(z[thread_id]);

    codes[thread_id] = xx * 4u + yy * 2u + zz;
}

kernel void z_order_decode(
    constant ulong& N        [[buffer(0)]],
    device const uint* codes [[buffer(1)]],
    device uint* x           [[buffer(2)]],
    device uint* y           [[buffer(3)]],
    device uint* z           [[buffer(4)]],
    uint thread_id [[thread_position_in_grid]])
{
    if ((ulong)thread_id >= N) return;

    x[thread_id] = extractBits(codes[thread_id] >> 2);
    y[thread_id] = extractBits(codes[thread_id] >> 1);
    z[thread_id] = extractBits(codes[thread_id]);
}
