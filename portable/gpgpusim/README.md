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

The image (`qpel/gpgpu-sim:dev-4195dd4-qpel6`, from this folder's Dockerfile on the
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

The patch also drops AccelWattch's x86-only compiler flags off x86. None of these are
qpel issues, and qpel needed no change for any of them.

**Upstream (2026-11-12):** submitted to accel-sim/gpgpu-sim_distribution, which has
issues disabled, so as pull requests against `dev`. `qpel-fixes.patch` is exactly their
diff:

- [#154](https://github.com/accel-sim/gpgpu-sim_distribution/pull/154): the PTX fixes
  above, one commit per bug (abs and neg share one).
- [#153](https://github.com/accel-sim/gpgpu-sim_distribution/pull/153): the AccelWattch
  Makefile flags.

Once they are merged, pin `GPGPUSIM_COMMIT` past them and drop the patch. The same fuzzing run found one qpel bug, in the importer
(`fuzz/regressions/unsigned_to_i32_literal.qpel`).

## What this does not prove

GPGPU-Sim executes PTX. The SASS that `ptxas` makes from it for a real architecture is
compiled (with warnings as errors, for sm_75–sm_90) by `nvcc_check.py`, but nothing
here executes it. Hardware behaviour outside the PTX specification, such as scheduling
or races, is out of scope too: qpel's kernels have none. The second half of the gate
is one run on a real NVIDIA card.
