// GPGPU-Sim self-test: the PTX behaviour qpel-fixes.patch corrects (bar.red, shfl.sync, setp on bit types), and a sweep of the
// integer and float conversions qpel's CUDA uses (device against host C semantics, in
// ranges where C defines them). differential/gpgpusim.py runs it before any NVIDIA row.
#include <cstdio>
#include <cuda_fp16.h>
#include <cstdlib>
#include <cstring>
#include <cmath>
#define N 4096
__host__ __device__ inline unsigned fbits(float f) { unsigned u; memcpy(&u, &f, 4); return u; }
__host__ __device__ inline float bitsf(unsigned u) { float f; memcpy(&f, &u, 4); return f; }
#define OPS(X) \
  X(0, (unsigned long long)(unsigned short)(signed char)v) \
  X(1, (unsigned long long)(unsigned int)(signed char)v) \
  X(2, (unsigned long long)(short)(signed char)v) \
  X(3, (unsigned long long)(unsigned short)(short)v) \
  X(4, (unsigned long long)(unsigned int)(short)v) \
  X(5, (unsigned long long)(long long)(int)v) \
  X(6, (unsigned long long)(long long)(signed char)v) \
  X(7, (unsigned long long)(long long)(short)v) \
  X(8, (unsigned long long)(unsigned char)(short)v) \
  X(9, (unsigned long long)(~(unsigned int)(unsigned short)(signed char)v)) \
  X(10, (unsigned long long)fbits((float)(long long)v)) \
  X(11, (unsigned long long)fbits((float)(unsigned long long)v)) \
  X(12, (unsigned long long)fbits((float)(int)v)) \
  X(13, (unsigned long long)fbits((float)(unsigned int)v)) \
  X(14, (unsigned long long)(long long)(float)(int)(v >> 40)) \
  X(15, (unsigned long long)(long long)bitsf((unsigned)v & 0xde7fffffu)) \
  X(16, (unsigned long long)(unsigned long long)bitsf((unsigned)v & 0x5e7fffffu)) \
  X(17, (unsigned long long)(int)bitsf((unsigned)v & 0xceffffffu)) \
  X(18, (unsigned long long)(unsigned int)bitsf((unsigned)v & 0x4f7fffffu)) \
  X(19, (unsigned long long)((long long)v / ((long long)(v >> 33) | 1))) \
  X(20, (unsigned long long)((long long)v % ((long long)(v >> 33) | 1))) \
  X(21, (unsigned long long)((unsigned long long)v / ((v >> 29) | 1))) \
  X(22, (unsigned long long)((long long)v >> (v & 63))) \
  X(23, (unsigned long long)(short)((short)v * (short)(v >> 16))) \
  X(24, (unsigned long long)(signed char)((signed char)v + (signed char)(v >> 8))) \
  X(25, (unsigned long long)((int)v >> (v >> 59)))
#define COUNT 26
#if defined(__CUDACC__) && !defined(HOST_ONLY)
#define FN __host__ __device__
#else
#define FN
#endif
FN inline void eval(unsigned long long v, unsigned long long* o) {
#define X(i, e) o[i] = e;
  OPS(X)
#undef X
}
__global__ void k(const unsigned long long* in, unsigned long long* out) {
  unsigned i = blockIdx.x * blockDim.x + threadIdx.x; if (i < N) eval(in[i], out + i * COUNT);
}
// Fixed-value checks of single PTX instructions (results computed independently).
__global__ void bit_compares(unsigned* out, const unsigned* in) {
  unsigned a = in[0], b = in[1];
  asm volatile("{ .reg .b32 r; .reg .pred p; add.s32 r, %1, -1; setp.ne.b32 p, r, 0; selp.u32 %0, 1, 0, p; }" : "=r"(out[0]) : "r"(a));
  asm volatile("{ .reg .b32 r; .reg .pred p; add.s32 r, %1, -1; setp.eq.b32 p, r, 0; selp.u32 %0, 1, 0, p; }" : "=r"(out[1]) : "r"(a));
  asm volatile("{ .reg .b16 h; .reg .pred p; cvt.u16.u32 h, %1; setp.eq.b16 p, h, 0; selp.u32 %0, 1, 0, p; }" : "=r"(out[2]) : "r"(b));
  asm volatile("{ .reg .b16 h; .reg .pred p; cvt.u16.u32 h, %1; setp.ne.b16 p, h, 0; selp.u32 %0, 1, 0, p; }" : "=r"(out[3]) : "r"(b));
}
__global__ void asm_checks(unsigned long long* o, const unsigned* in) {
  unsigned a = in[0], b = in[1], s = in[2]; unsigned long long r;
  asm("bfi.b64 %0, %1, %2, 32, 32;" : "=l"(r) : "l"((unsigned long long)a), "l"((unsigned long long)b)); o[0] = r;
  asm("mad.wide.u32 %0, %1, %2, %3;" : "=l"(r) : "r"(a), "r"(b), "l"(0x100000000ull)); o[1] = r;
  asm("shr.u64 %0, %1, %2;" : "=l"(r) : "l"(0xF000000000000000ull), "r"(s)); o[2] = r;
  long long w; asm("mul.hi.s64 %0, %1, %2;" : "=l"(w) : "l"(-0x123456789abcdefll), "l"(0x7edcba9876543210ll)); o[3] = w;
  asm("mul.hi.u64 %0, %1, %2;" : "=l"(r) : "l"(0xfedcba9876543210ull), "l"(0x123456789abcdef1ull)); o[4] = r;
  float z; asm("abs.f32 %0, %1;" : "=f"(z) : "f"(-0.0f)); o[5] = fbits(z);
  asm("neg.f32 %0, %1;" : "=f"(z) : "f"(0.0f)); o[6] = fbits(z);
  o[7] = fbits(sinf(__uint_as_float(in[3])));  // 200000.0f: the large-argument path
}
// bar.red (__syncthreads_count/and/or): parsed, delivered and reset per barrier, in every block;
// .and/.or results written as predicates. Each block's results: 10, 3, 5 + 6 + 7, 2 (and), 2 (or).
__global__ void barrier_counts(int* out) {
  int t = threadIdx.x, a = __syncthreads_count(t < 10), b = __syncthreads_count(t < 3), c = 0;
  for (int r = 0; r < 3; r++) c += __syncthreads_count(t < 5 + r);
  int all = __syncthreads_and(t < blockDim.x) ? 2 : 0, some = __syncthreads_and(t < 10) ? 1 : 0;
  int one = __syncthreads_or(t == 5) ? 2 : 0, none = __syncthreads_or(t > 1000) ? 1 : 0;
  int* o = out + blockIdx.x * 5;
  if (t == 0) { o[0] = a; o[1] = b; o[2] = c; o[3] = all + some; o[4] = one + none; }
}
// shfl.sync (__shfl_*_sync, as CUDA 12 emits it with a d|p destination): in every warp of every
// block, value 1000 * warp + lane. Index 3, xor 5, down 4 and up 4; out-of-range lanes keep their own.
__global__ void shuffles(unsigned* out) {
  unsigned lane = threadIdx.x & 31u, v = 1000u * (blockIdx.x * 2u + threadIdx.x / 32u) + lane;
  unsigned* o = out + (blockIdx.x * blockDim.x + threadIdx.x) * 4;
  o[0] = __shfl_sync(0xffffffffu, v, 3);
  o[1] = __shfl_xor_sync(0xffffffffu, v, 5);
  o[2] = __shfl_down_sync(0xffffffffu, v, 4);
  o[3] = __shfl_up_sync(0xffffffffu, v, 4);
}
// The predicate of shfl.sync d|p (whether the source lane was in range), which hand-written PTX
// such as CUB's scans reads: an inclusive sum of ones over a warp, steps 1, 2, 4, 8, 16.
__global__ void shuffle_predicates(unsigned* out) {
  unsigned x = 1u, mask = 0xffffffffu, c = 0u;
  for (unsigned offset = 1u; offset < 32u; offset *= 2u) {
    unsigned y;
    asm volatile("{ .reg .u32 r0; .reg .pred p; shfl.sync.up.b32 r0|p, %1, %2, %3, %4; @p add.u32 r0, r0, %1; mov.u32 %0, r0; }"
                 : "=r"(y) : "r"(x), "r"(offset), "r"(c), "r"(mask));
    x = y;
  }
  out[blockIdx.x * blockDim.x + threadIdx.x] = x;
}
// ldmatrix (qpel-ldmatrix.patch): 4 8x8 matrices of 16-bit values m * 64 + r * 8 + c in shared memory;
// lane l gives row l % 8 of matrix l / 8. Per lane: the x1 load, the four x4 registers, the four .trans.
__global__ void load_matrices(unsigned* out) {
  __shared__ unsigned short tile[4 * 64];
  unsigned lane = threadIdx.x;
  for (unsigned i = lane; i < 256u; i += 32u) tile[i] = (unsigned short)i;
  __syncwarp();
  unsigned addr = (unsigned)__cvta_generic_to_shared(&tile[(lane / 8u) * 64u + (lane % 8u) * 8u]);
  unsigned* o = out + lane * 9u;
  asm volatile("ldmatrix.sync.aligned.m8n8.x1.shared.b16 {%0}, [%1];" : "=r"(o[0]) : "r"(addr));
  asm volatile("ldmatrix.sync.aligned.m8n8.x4.shared.b16 {%0, %1, %2, %3}, [%4];"
               : "=r"(o[1]), "=r"(o[2]), "=r"(o[3]), "=r"(o[4]) : "r"(addr));
  asm volatile("ldmatrix.sync.aligned.m8n8.x4.trans.shared.b16 {%0, %1, %2, %3}, [%4];"
               : "=r"(o[5]), "=r"(o[6]), "=r"(o[7]), "=r"(o[8]) : "r"(addr));
}
// mma.sync m16n8k8 f16 -> f32 (qpel-mma.patch): small integers, so any order of the sums gives the
// same bits. With g = lane / 4 and t = lane % 4, lane l holds A[g][2t + j] (register 0) and
// A[g + 8][2t + j] (register 1), B[2t + j][g], and C, D[g][2t + j] (j) and [g + 8][2t + j] (2 + j).
__device__ __host__ inline float mma_a(unsigned r, unsigned k) { return (float)((int)((r * 3u + k * 5u) % 7u) - 3); }
__device__ __host__ inline float mma_b(unsigned k, unsigned c) { return (float)((int)((k * 5u + c * 2u) % 9u) - 4); }
__device__ __host__ inline float mma_c(unsigned r, unsigned c) { return (float)((int)r - 2 * (int)c); }
__device__ inline unsigned mma_pack(float lo, float hi) {
  return (unsigned)__half_as_ushort(__float2half_rn(lo)) | ((unsigned)__half_as_ushort(__float2half_rn(hi)) << 16);
}
__global__ void multiply_matrices(float* out) {
  unsigned lane = threadIdx.x, g = lane / 4u, t = lane % 4u;
  unsigned a0 = mma_pack(mma_a(g, 2u * t), mma_a(g, 2u * t + 1u)), a1 = mma_pack(mma_a(g + 8u, 2u * t), mma_a(g + 8u, 2u * t + 1u));
  unsigned b = mma_pack(mma_b(2u * t, g), mma_b(2u * t + 1u, g));
  float c0 = mma_c(g, 2u * t), c1 = mma_c(g, 2u * t + 1u), c2 = mma_c(g + 8u, 2u * t), c3 = mma_c(g + 8u, 2u * t + 1u), d[4];
  asm volatile("mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32 {%0, %1, %2, %3}, {%4, %5}, {%6}, {%7, %8, %9, %10};"
               : "=f"(d[0]), "=f"(d[1]), "=f"(d[2]), "=f"(d[3]) : "r"(a0), "r"(a1), "r"(b), "f"(c0), "f"(c1), "f"(c2), "f"(c3));
  for (unsigned i = 0; i < 4u; ++i) out[lane * 4u + i] = d[i];
}
// A register declared in a nested { } block shadows the enclosing one (qpel-scopes.patch): the inner
// t is 7, the outer keeps x.
__global__ void nested_scopes(unsigned* out, unsigned x) {
  unsigned y;
  asm volatile("{ .reg .u32 t; mov.u32 t, %1; { .reg .u32 t; mov.u32 t, 7; } mov.u32 %0, t; }" : "=r"(y) : "r"(x));
  out[threadIdx.x] = y;
}
int main() {
  int failures = 0;
  { // nested register scopes
    unsigned h[32], *d; cudaMalloc(&d, sizeof h);
    nested_scopes<<<1, 32>>>(d, 41u); cudaDeviceSynchronize(); cudaMemcpy(h, d, sizeof h, cudaMemcpyDeviceToHost);
    if (h[0] != 41u) { printf("SELFTEST FAIL nested .reg scopes: the outer register became %u, want 41\n", h[0]); ++failures; }
  }
  { // mma.sync m16n8k8: D = A B + C, laid out across the warp as the PTX ISA documents
    static float h[32 * 4]; float* d; cudaMalloc(&d, sizeof h);
    multiply_matrices<<<1, 32>>>(d); cudaDeviceSynchronize(); cudaMemcpy(h, d, sizeof h, cudaMemcpyDeviceToHost);
    for (unsigned l = 0; l < 32u; ++l) {
      for (unsigned i = 0; i < 4u; ++i) {
        unsigned r = l / 4u + (i >= 2u ? 8u : 0u), c = 2u * (l % 4u) + (i & 1u);
        float want = mma_c(r, c);
        for (unsigned k = 0; k < 8u; ++k) want += mma_a(r, k) * mma_b(k, c);
        if (h[l * 4u + i] != want) {
          printf("SELFTEST FAIL mma.sync.m16n8k8: lane %u register %u gives %g, want %g\n", l, i, h[l * 4u + i], want);
          ++failures; l = 32u; break;
        }
      }
    }
  }
  { // ldmatrix: lane l gets columns 2 (l % 4) and 2 (l % 4) + 1 of row l / 4 (.trans: rows of column l / 4)
    static unsigned h[32 * 9]; unsigned* d; cudaMalloc(&d, sizeof h);
    load_matrices<<<1, 32>>>(d); cudaDeviceSynchronize(); cudaMemcpy(h, d, sizeof h, cudaMemcpyDeviceToHost);
    for (unsigned l = 0; l < 32u; ++l) {
      unsigned r = l / 4u, c = 2u * (l % 4u);
      for (unsigned m = 0; m < 9u; ++m) {
        unsigned mat = m == 0 ? 0u : (m - 1u) % 4u;
        unsigned want = m < 5u ? ((mat * 64u + r * 8u + c) | ((mat * 64u + r * 8u + c + 1u) << 16))
                               : ((mat * 64u + c * 8u + r) | ((mat * 64u + (c + 1u) * 8u + r) << 16));
        if (h[l * 9u + m] != want) {
          printf("SELFTEST FAIL ldmatrix%s: lane %u register %u gives %08x, want %08x\n", m < 5u ? "" : ".trans", l, m, h[l * 9u + m], want);
          ++failures; l = 32u; break;
        }
      }
    }
  }
  { // warp shuffles, 3 blocks of 2 warps
    static unsigned h[3 * 64 * 4]; unsigned* d; cudaMalloc(&d, sizeof h);
    shuffles<<<3, 64>>>(d); cudaDeviceSynchronize(); cudaMemcpy(h, d, sizeof h, cudaMemcpyDeviceToHost);
    const char* names[4] = {"shfl.sync.idx", "shfl.sync.bfly", "shfl.sync.down", "shfl.sync.up"};
    for (int t = 0; t < 3 * 64; ++t) {
      unsigned lane = t & 31u, base = 1000u * (t / 32u);
      unsigned want[4] = {base + 3u, base + (lane ^ 5u), base + (lane + 4u < 32u ? lane + 4u : lane), base + (lane >= 4u ? lane - 4u : lane)};
      for (int m = 0; m < 4; ++m) if (h[t * 4 + m] != want[m]) {
        printf("SELFTEST FAIL %s: thread %d gives %u, want %u\n", names[m], t, h[t * 4 + m], want[m]);
        ++failures; break;
      }
    }
  }
  { // shfl.sync predicates: lane t's inclusive sum of ones is t + 1
    static unsigned h[64]; unsigned* d; cudaMalloc(&d, sizeof h);
    shuffle_predicates<<<1, 64>>>(d); cudaDeviceSynchronize(); cudaMemcpy(h, d, sizeof h, cudaMemcpyDeviceToHost);
    for (int t = 0; t < 64; ++t) if (h[t] != (t & 31u) + 1u) {
      printf("SELFTEST FAIL shfl.sync predicate: thread %d gives %u, want %u\n", t, h[t], (t & 31u) + 1u);
      ++failures; break;
    }
  }
  { // barrier reductions, in each of 4 blocks
    const int want[5] = {10, 3, 18, 2, 2};
    const char* names[5] = {"bar.red.popc", "bar.red.popc (second)", "bar.red.popc (loop)", "bar.red.and", "bar.red.or"};
    int h[20], *d; cudaMalloc(&d, sizeof h); barrier_counts<<<4, 64>>>(d); cudaDeviceSynchronize();
    cudaMemcpy(h, d, sizeof h, cudaMemcpyDeviceToHost);
    for (int i = 0; i < 20; ++i) if (h[i] != want[i % 5]) {
      printf("SELFTEST FAIL %s: block %d gives %d, want %d\n", names[i % 5], i / 5, h[i], want[i % 5]);
      ++failures;
    }
  }
  { // setp on bit types (qpel-setp.patch): the 32 and 16 low bits are compared, whatever the register
    // holds above them. add.s32 1 + (-1) leaves bit 32 set in GPGPU-Sim's register; before the fix
    // setp.ne.b32 fell through to the 64-bit comparison and gave true (Triton's matmul loop never ended).
    const unsigned in[2] = {1u, 0x10000u}, want[4] = {0u, 1u, 1u, 0u};
    const char* names[4] = {"setp.ne.b32", "setp.eq.b32", "setp.eq.b16", "setp.ne.b16"};
    unsigned h[4], *di, *dout; cudaMalloc(&di, sizeof in); cudaMalloc(&dout, sizeof h);
    cudaMemcpy(di, in, sizeof in, cudaMemcpyHostToDevice);
    bit_compares<<<1, 1>>>(dout, di); cudaDeviceSynchronize(); cudaMemcpy(h, dout, sizeof h, cudaMemcpyDeviceToHost);
    for (int i = 0; i < 4; ++i) if (h[i] != want[i]) { printf("SELFTEST FAIL %s: %u, want %u\n", names[i], h[i], want[i]); ++failures; }
  }
  { // asm checks
    const unsigned in[4] = {0xDEADBEEFu, 0xCAFEBABEu, 40u, 0x48435000u};
    const unsigned long long want[8] = {0xdeadbeefcafebabeull, 0xb092ab7c88cf5b62ull, 0x0000000000f00000ull,
      0xff6fa8b3175e0fb5ull, 0x121fa00ad77d7423ull, 0x00000000ull, 0x80000000ull, 0};
    unsigned *di; unsigned long long o[8], *dout;
    cudaMalloc(&di, sizeof in); cudaMalloc(&dout, sizeof o); cudaMemcpy(di, in, sizeof in, cudaMemcpyHostToDevice);
    asm_checks<<<1, 1>>>(dout, di); cudaDeviceSynchronize(); cudaMemcpy(o, dout, sizeof o, cudaMemcpyDeviceToHost);
    const char* names[8] = {"bfi.b64", "mad.wide.u32", "shr.u64", "mul.hi.s64", "mul.hi.u64", "abs.f32 -0", "neg.f32 +0", "sinf(200000)"};
    // sinf is accurate to 2 ulp (CUDA's documented bound), not correctly rounded: compare with the host's.
    long long ulps = (long long)o[7] - (long long)fbits(sinf(200000.0f));
    if (ulps < -4 || ulps > 4) { printf("SELFTEST FAIL sinf(200000): %016llx, %lld ulp from the host\n", o[7], ulps); ++failures; }
    for (int i = 0; i < 7; ++i) if (o[i] != want[i]) { printf("SELFTEST FAIL %s: %016llx, want %016llx\n", names[i], o[i], want[i]); ++failures; }
  }
  { // conversion and integer sweep: device against host C semantics
    static unsigned long long in[N], out[N * COUNT], ref[COUNT];
    unsigned long long x = 88172645463325252ull;
    for (int i = 0; i < N; ++i) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; in[i] = i < 256 ? (unsigned long long)(long long)(i - 128) * (i % 3 ? 1 : 0x10001) : x; }
    unsigned long long *din, *dout; cudaMalloc(&din, sizeof in); cudaMalloc(&dout, sizeof out);
    cudaMemcpy(din, in, sizeof in, cudaMemcpyHostToDevice); k<<<N / 128, 128>>>(din, dout); cudaDeviceSynchronize();
    cudaMemcpy(out, dout, sizeof out, cudaMemcpyDeviceToHost);
    for (int j = 0; j < COUNT; ++j) {
      int bad = 0; unsigned long long v = 0, want = 0, got = 0;
      for (int i = 0; i < N; ++i) { eval(in[i], ref); if (ref[j] != out[i * COUNT + j] && !bad++) { v = in[i]; want = ref[j]; got = out[i * COUNT + j]; } }
      if (bad) { printf("SELFTEST FAIL sweep op %d: %d of %d differ (v=%016llx: %016llx, want %016llx)\n", j, bad, N, v, got, want); ++failures; }
    }
  }
  printf(failures ? "SELFTEST FAILED\n" : "SELFTEST PASSED\n");
  return failures ? 1 : 0;
}
