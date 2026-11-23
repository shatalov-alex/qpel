# NVIDIA execution on GPGPU-Sim

The first half of the Phase 2 NVIDIA gate: qpel's CUDA, and the unmodified CUDA
originals, executed as an NVIDIA GPU executes them, without NVIDIA hardware.
[GPGPU-Sim](https://github.com/accel-sim/gpgpu-sim_distribution) (accel-sim fork, `dev`
at 4195dd4, BSD) runs CUDA programs against its own `libcudart`, which executes the
program's embedded PTX on a simulated GPU (`SM75_RTX2060`, Turing).

```sh
python3 -m differential.gpgpusim            # every behaviour-matrix source (~3 min)
python3 -m differential.gpgpusim box_sum    # selected fixtures
python3 -m fuzz --nvidia                    # random programs, also on GPGPU-Sim
python3 check_all.py --gpu --nvidia         # all of it, with the other checks
```

The image (`qpel/gpgpu-sim:dev-4195dd4-qpel20`, from this folder's Dockerfile on the
CUDA 12.6 devel image `nvcc_check.py` uses) is built on first use and takes about a
minute. It is built and tested natively on arm64 (Apple silicon, Docker Desktop). On
x86-64 the upstream build flags are kept, but that build is untested. Change the tag in
`differential/gpgpusim.py` whenever anything here changes.

## What runs

`differential/gpgpusim.py` writes a real CUDA host program for each source from the
same launch plan the CPU simulator uses (`cudaMalloc`, `cudaMemcpy`, `<<<>>>` with the
plan's geometry, the importer's argument mapping for originals). All programs compile
with nvcc in one container, then every case runs in parallel, each in its own folder.

| Row | Compared with |
|---|---|
| `nvidia: cuda original` | the same original on the CPU simulator |
| `nvidia: cuda -> qpel -> cuda` | the CUDA original on the CPU simulator |
| `nvidia: metal -> qpel -> cuda` | the Metal original on the Apple GPU |

The comparison rules are the matrix's (bit for bit; bounded math within 2 × 4 ulp).
Originals compile with `-fmad=false`, which is how the simulator and the Metal originals
run them (as written). qpel's CUDA compiles with nvcc's defaults: it rounds every float
operation explicitly (f32-strict-v1), so contraction cannot change it. Functional
simulation (`PTX_SIM_MODE_FUNC=1`) is the default; `--timing` runs the cycle model,
which executes the same instructions, more slowly.

**2026-11-12: every row passes.** That is 720 cases, covering 28 llama.cpp CUDA
originals, 24 llama.cpp Metal originals translated to CUDA, the SAM 2 kernels and the
examples. Fuzzing with `--nvidia` passes 300 of 300 random programs (seed 1) and the 7 regressions.

## Simulator bugs found and fixed (`qpel-fixes.patch`)

Each of these first showed up as qpel's CUDA *and* the unmodified original disagreeing
with the CPU simulator, with identical wrong values, and was then reduced to single
instructions. `selftest.cu` checks every one of them, plus a sweep of 26 integer and float
conversions against the host, before any NVIDIA row runs. A simulator that loses a
fix is reported as such, not as a qpel failure.

| Instruction | Upstream behaviour | Found by |
|---|---|---|
| `cvt.rn.f16.f32`, f16 arithmetic | rounds ties away from zero (`half.hpp` configured for speed; arithmetic truncates) | `half_bias`, `scale_f16` |
| `bfi.b64` | inserts with 32-bit masks: bits above 31 lost | `sinf` for \|x\| > 105615 returned 0 |
| `mad.wide.u32/s32`, `mad.hi` | 32×32 product formed in 32 bits; wide addend read at 32 bits | same |
| 64-bit integer immediates | the PTX lexer stores literals in a C `int` | same (`mov.u64 %rd, 4294967296`) |
| `cvt.f32/f64.{s,u}64` | converts the uninitialised result register instead of the source | same |
| `shl/shr.b64` | reads the 32-bit shift amount as 64 bits | same |
| `cvt.rzi.{s,u}64.f32` | converts through a 32-bit `int` (clamped to its range) | fuzzing |
| `ld.s8` (and signed vector loads) | zero-extended; vector elements kept stale upper bits | fuzzing |
| `mul.hi.u64` / `mul.hi.s64` | unimplemented (assertion) / returns the low half | fuzzing (64-bit division by constants) |
| `abs.f32/f64`, `neg.f32/f64/f16` | `x < 0 ? -x : x` and `0 - x`: wrong sign of zero | fuzzing |
| `bar.red.popc` (`__syncthreads_count`) | the parser asserts on the `.popc` option | TRELLIS.2 `render` |
| `bar.red`, timing model | counts are never reset: each barrier adds to every earlier one in the same CTA slot | same (10, 13, 73 for 10, 3, 18) |
| `bar.red`, functional mode | the result is never delivered to the destination register (always 0) | same |
| `bar.red.and`, `bar.red.or` (`__syncthreads_and/or`) | the predicate result is written as a plain 0/1 (inverted, since predicate registers hold a zero flag); `.and` accumulates into storage that starts at 0 | testing the `bar.red` fixes |

The patch also drops AccelWattch's x86-only compiler flags off x86. None of these are
qpel issues, and qpel needed no change for any of them.

**Upstream (2026-11-12):** submitted to accel-sim/gpgpu-sim_distribution, which has
issues disabled, so as pull requests against `dev` (all three merge cleanly together). `qpel-fixes.patch` is exactly their
diff:

- [#154](https://github.com/accel-sim/gpgpu-sim_distribution/pull/154): the PTX fixes
  above up to `abs`/`neg`, one commit per bug (abs and neg share one).
- [#153](https://github.com/accel-sim/gpgpu-sim_distribution/pull/153): the AccelWattch
  Makefile flags.
- [#155](https://github.com/accel-sim/gpgpu-sim_distribution/pull/155) (2026-11-13): the four
  `bar.red` fixes above, one commit each.

Once the pull requests are merged, pin `GPGPUSIM_COMMIT` past them and drop the patch.

**Open (2026-11-17):** GPGPU-Sim's PTX grammar takes only registers in a vector operand, so `mov.b32 %f12, {0,%rs306};` (cuda_bf16.hpp widening a bfloat16) is a syntax error, after which the functions that follow are missing and `add_ptxinfo` asserts. bitsandbytes' `kernels.cu` has such bf16 kernels, so its originals are skipped here (`SIMULATOR_ISSUES`); qpel's CUDA of the dequantisation programs passes.

**Resolved (2026-11-17, `qpel-scopes.patch`):** TRELLIS.2's `render` found no voxel on
GPGPU-Sim, for the original and qpel's CUDA alike, from 2026-11-13. Tracing one pixel had shown a
bool (`inside`) changing between its definition and its use, across a scoped `bar.red` block: it
was the predicate that block declared again, which GPGPU-Sim did not scope (see the fix above).
With image `qpel17` both pass all cases, as they did on a Tesla T4 (2026-11-14). The same fuzzing run found one qpel bug, in the importer
(`fuzz/regressions/unsigned_to_i32_literal.qpel`).

**Fixed (2026-11-14), `qpel-shfl.patch`, upstream as [#156](https://github.com/accel-sim/gpgpu-sim_distribution/pull/156):** warp shuffles were wrong in two ways, so no
shuffle result from GPGPU-Sim could be trusted before image `qpel12`.
- **Every shuffle read 0.** `shfl.sync` with a `d|p` destination, as CUDA 12 emits it, lost
  its value: `set_operand_value` handles two-register destinations of other forms.
- **Warps shared warp 0's threads.** In functional mode every warp used warp 0's threads:
  `getExecuteWarp` did not set the warp id that `shfl_impl` (and `mma`) read. Shuffles in
  warp 1 read and wrote warp 0, and llama.cpp's `argmax_f32` crashed in `shfl_impl`.

The self-test checks idx, bfly, down and up in 3 blocks of 2 warps. llama.cpp `argmax_f32`
passes for the original and qpel's CUDA. The pull request has the same two changes, one commit each, reformatted with clang-format.
**Fixed (2026-11-15), image `qpel13`:** the first fix gave `shfl.sync d|p` its in-range
predicate as 1 for true, but GPGPU-Sim's predicate registers hold a zero flag (0 is true, as
`setp_impl` writes them), so `@p` after a shuffle ran exactly where it should not. nvcc's own
shuffles never read that predicate (they compare lanes with `setp`), so the self-test and every
fixture passed; CUB's scans and reductions are inline PTX that do (`shfl.sync.up.b32 r0|p ...;
@p add.f32 r0, r0, x;`), and a 256-thread `cub::BlockScan` gave wrong sums from lane 8 on. The
self-test now runs that PTX (an inclusive sum of ones over a warp). #156 has the fix as a third
commit (2026-11-15).

**Fixed (2026-11-16), `qpel-setp.patch`, image `qpel14` (submitted upstream as accel-sim PR #157):** `setp` on `.b16` and
`.b32` compared the wrong bits. `CmpOp` has no `break` after the `B16_TYPE` and `B32_TYPE` cases,
so both fell through to the `B64_TYPE` comparison of the whole 64-bit register value, including
bits above the register's width that earlier instructions left there. After `add.s32 r, 1, -1`
(the immediate taken as 0xffffffff) the register's low 32 bits are 0 and bit 32 is set, so
`setp.ne.b32 p, r, 0` gave true. Every CUDA row passed with the bug in place (their
comparisons never met a register with such bits); Triton's PTX counts its loops down and tests
them with `setp.ne.b32`, and Triton's matmul ran forever. The self-test checks `setp.ne` and `setp.eq` on `.b32` and `.b16`.

**Added (2026-11-16), `qpel-ldmatrix.patch`, image `qpel15` (submitted upstream as accel-sim PR #158, which builds on #156: functional simulation needs its warp id fix for every warp but the first):** `ldmatrix.sync.aligned.m8n8.x{1,2,4}[.trans].shared.b16`
(sm_75), which GPGPU-Sim did not parse ("Syntax error"). Triton emits it to move a tile into a
`tl.dot` operand's layout (a transposed operand: the attention backward kernels) or to convert a 2D
block's layout (Liger's 2D-block RMSNorm). A warp instruction like `shfl`: lanes 8i to 8i+7 give the
row addresses of matrix i, each lane receives its pairs of 16-bit elements as the PTX ISA lays out
the fragments (with `.trans`, of the transposed matrix). The self-test checks `.x1`, `.x4` and
`.x4.trans` for every lane, in functional mode and the timing model.

**Added (2026-11-17), `qpel-mma.patch`, image `qpel16` (local so far):**
`mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32` (sm_75 tensor cores), which GPGPU-Sim did not
parse (it has only `wmma`). qpel's CUDA multiplies f16 matrices with it (IR version 13). A warp
instruction like `ldmatrix`: with g = lane / 4 and t = lane % 4, lane l holds A[g][2t + j] and
A[g + 8][2t + j], B[2t + j][g] and C, D[g][2t + j] and [g + 8][2t + j], as the PTX ISA lays out the
fragments; the products of f16 values are exact in f32 and are added in k order (the hardware's
order is not documented, so qpel's tests compare f16 products within a bound). The opcode is
counted as a tensor-core instruction in the timing model. The self-test multiplies small integers
(exact in any order) and checks every lane's four results, here and on the T4.

**Fixed (2026-11-17), `qpel-scopes.patch`, image `qpel17` (submitted upstream as accel-sim PR #159):** a
register declared in a nested `{ }` block with the name of one in the enclosing scope was taken for
a duplicate and skipped ("Warning %p1 was declared previous ... skipping new declaration"), so the
block wrote the enclosing register. CUDA 12.6 writes `__syncthreads_count` as `{ .reg .pred %p1;
setp ...; bar.red.popc ... %p1; }` inside functions with a `%p1` of their own: gsplat's unmodified
rasterizer lost a live predicate and rendered wrong pixels on GPGPU-Sim (qpel's translation passed
only because its `%p1` was not live there). A nested block's declarations are now checked against
that block only. The self-test checks a shadowing `.reg` in inline assembly.

**Fixed (2026-11-18), `qpel-wmma.patch`, image `qpel19` (submitted upstream as accel-sim PR #160, its exact diff):** two bugs in `mma_impl`, WMMA's
`wmma.mma` (16 x 16 x 16). With f16 inputs it read each input's 16 bits as an address
(`*(reinterpret_cast<half *>(hex_val))`), so every f16 WMMA crashed the simulator with SIGSEGV; and it
summed the 16 products in f16, rounding every product and partial sum to 11 bits, where tensor cores
multiply f16 exactly and add in at least f32 (a random 16 x 16 x 16 product was off by up to 900 times the
f32 bound). It now adds the products to C in f32, in k order. The self-test checks a WMMA product of small
integers element by element, and 1 + 15 x 2^-12, which summing in f16 rounds to 1.

Timing (performance) mode still aborts on shuffles, before and after the fixes
(`bitset::test` out of range in `shader_core_ctx::execute`); qpel runs functional mode only.

**Fixed (2026-11-22), `qpel-cub.patch`, image `qpel20` (local so far):** what NVIDIA's device-wide CUB 2.5
needs, found comparing qpel's device-wide scan and radix sort with it (LIBRARIES.md). `ld.relaxed`, `ld.acquire`,
`st.relaxed` and `st.release` at `.cta`, `.gpu` or `.sys` scope (the decoupled look-back of `DeviceScan`) did not
parse (`operand "elaxed" has no declaration`); they are LD and ST, which functional simulation already orders
as strongly. `%lanemask_eq`, `le`, `lt`, `ge` and `gt` (CUB's warp scans and radix ranking) aborted as not
implemented; they are the lanes at, up to, below, from and above the thread's. `nanosleep` (the look-back's
back-off) was not an instruction; it does nothing. The self-test checks every lanemask of every lane and the
four memory orders.

**Open (2026-11-22):** with those fixed, NVIDIA's `DeviceScan` gives the right sums in GPGPU-Sim only on 32-bit
integers within one of its tiles: across tiles its decoupled look-back gives wrong prefixes or asserts in
`generic_to_shared` (`isspace_shared`), and its 64-bit scan is wrong from the 15th element of 300. Its
`DeviceRadixSort` is right for 32-bit keys and for 64-bit keys up to one tile (257 here), wrong beyond. qpel's
own scan and sort are right at every size there (against numpy), and agree with NVIDIA's CUB where it runs
right (tests/test_device_scan.py, tests/test_device_radix_sort.py); the T4 compares at every size.

## What this does not prove

GPGPU-Sim executes PTX. The SASS that `ptxas` makes from it for a real architecture is
compiled (with warnings as errors, for sm_75–sm_90) by `nvcc_check.py`, but nothing
here executes it. Hardware behaviour outside the PTX specification, such as scheduling
or races, is out of scope too: qpel's kernels have none. The second half of the gate
ran the same programs, compiled to SASS, on a real NVIDIA GPU (Tesla T4, 2026-11-12):
every case and `selftest.cu` pass there too (`python3 -m differential.hardware`, see
`../README.md`).

**Open (2026-11-23):** GPGPU-Sim's `rsqrt.approx.f32` of -0 is +infinity; the PTX ISA (and NVIDIA's GPUs) give
-infinity. Every other value of `examples/math_rsqrt.qpel` is within its bound there, so
`tests/test_math_rsqrt.py` leaves that one case out of its GPGPU-Sim row (the Apple GPU and the CPU simulator
compare it).
