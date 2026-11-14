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

The image (`qpel/gpgpu-sim:dev-4195dd4-qpel12`, from this folder's Dockerfile on the
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

**Open (2026-11-13):** with the fixes, TRELLIS.2's `render` still finds no voxel on
GPGPU-Sim, for the original and qpel's CUDA alike (the CPU simulator and the Apple GPU
agree with each other). Tracing one pixel shows a bool (`inside`) changing between its
definition and its use; the instructions involved (16-bit bool registers, predicates
across scoped `bar.red` blocks) each behave correctly in isolation. On real NVIDIA hardware
(Tesla T4, 2026-11-14) `render` passes all 24 cases for the original and for qpel's CUDA, so
this is a simulator problem; the GPGPU-Sim row stays skipped. The same fuzzing run found one qpel bug, in the importer
(`fuzz/regressions/unsigned_to_i32_literal.qpel`).

**Fixed locally (2026-11-14), `qpel-shfl.patch`:** warp shuffles were wrong in two ways, so no
shuffle result from GPGPU-Sim could be trusted before image `qpel12`.
- **Every shuffle read 0.** `shfl.sync` with a `d|p` destination, as CUDA 12 emits it, lost
  its value: `set_operand_value` handles two-register destinations of other forms.
- **Warps shared warp 0's threads.** In functional mode every warp used warp 0's threads:
  `getExecuteWarp` did not set the warp id that `shfl_impl` (and `mma`) read. Shuffles in
  warp 1 read and wrote warp 0, and llama.cpp's `argmax_f32` crashed in `shfl_impl`.

The self-test checks idx, bfly, down and up in 3 blocks of 2 warps. llama.cpp `argmax_f32`
passes for the original and qpel's CUDA. These two fixes are not yet submitted upstream.
Timing (performance) mode still crashes on shuffles; qpel runs functional mode only.

## What this does not prove

GPGPU-Sim executes PTX. The SASS that `ptxas` makes from it for a real architecture is
compiled (with warnings as errors, for sm_75–sm_90) by `nvcc_check.py`, but nothing
here executes it. Hardware behaviour outside the PTX specification, such as scheduling
or races, is out of scope too: qpel's kernels have none. The second half of the gate
ran the same programs, compiled to SASS, on a real NVIDIA GPU (Tesla T4, 2026-11-12):
every case and `selftest.cu` pass there too (`python3 -m differential.hardware`, see
`../README.md`).
