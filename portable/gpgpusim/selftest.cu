// GPGPU-Sim self-test: the PTX behaviour qpel-fixes.patch corrects, and a sweep of the
// integer and float conversions qpel's CUDA uses (device against host C semantics, in
// ranges where C defines them). differential/gpgpusim.py runs it before any NVIDIA row.
#include <cstdio>
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
int main() {
  int failures = 0;
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
