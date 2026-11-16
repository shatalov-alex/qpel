# qpel's Triton frontend

Triton kernels (`@triton.jit`) become qpel programs, and from them CUDA and Metal. The input
is Triton's own TTIR (the tensor program after Triton's frontend), so Triton itself is
the parser: no second reading of Python.

    python3 -m frontends.triton kernel.ttir --num-warps 4 --out kernel.qpel   # TTIR -> qpel text
    python3 qpel.py build kernel.qpel --out build/                            # -> CUDA and Metal
    python3 -m differential.triton                                            # behaviour rows

## Getting TTIR without a GPU

Triton has no macOS build, so a Linux container compiles kernels (`Dockerfile`: Triton 3.8.0,
numpy, and the CPU build of torch for Triton's interpreter):

    docker build --label qpel=1 -t qpel/triton:3.8.0 frontends/triton
    docker run --rm --label qpel=1 --memory 4g --cpus 2 -v $PWD/frontends/triton:/tool:ro \
        -v $PWD/corpus/triton:/corpus qpel/triton:3.8.0 \
        python /tool/compile.py /corpus/kernels.py /corpus/kernels.json /corpus/build

For a vendored library the corpus folder is the package root and `kernels.json` names each kernel's
`function` as `module:name` (corpus/liger, Liger-Kernel; `--network none`: its code runs offline):

    docker run --rm --network none --label qpel=1 --memory 4g --cpus 2 -v $PWD/frontends/triton:/tool:ro \
        -v $PWD/corpus/liger:/corpus qpel/triton:3.8.0 \
        python /tool/compile.py /corpus /corpus/kernels.json /corpus/build

`compile.py` compiles each kernel of `kernels.json` (signature, constexprs, num_warps) for
NVIDIA sm_75 with `enable_fp_fusion=False` (so `a * b + c` stays two roundings, as qpel
computes it), and writes `build/<kernel>.ttir`, `.ttgir`, `.ptx` and `.json` (launch metadata).
The build is committed, so the importer, its tests and the round-trip rows need no Docker.

## How a Triton program maps to qpel

One Triton program is one qpel workgroup of `num_warps * 32` threads (a Groups kernel): its
`tt.get_program_id` is the group index and the launch grid is the group count. Tensors are of
three kinds (`lower.py`):

- **Index tensors**: pure functions of the element's coordinates and of scalars
  (`tt.make_range`, `tt.splat`, `tt.broadcast`, `tt.expand_dims`, constants, and element-wise
  operations of those). They are never stored. Each use computes its element where it is needed.
- **Data tensors** (loaded, or computed from loaded values): element e (row-major) of n elements
  is owned by thread e % T, in slot e / T of a local array of ceil(n / T). All tensors of one shape
  share this distribution, so element-wise operations read their operands where they are owned.
- **Shared tensors**: a data tensor written to the group's shared memory between two barriers,
  where a broadcast, an expanded dimension or a reduction needs any thread to read any element.

A combiner region (`tt.reduce`, `tt.scan`) becomes scalar statements in the fold loop. A scan is
inclusive: element i combines elements 0 to i in index order (n - 1 down to i when reversed).
An atomic runs per owned element under its mask; only relaxed atomics are read (qpel's atomics
are relaxed), so `tl.atomic_add(..., sem="relaxed")`. Masked loads give the load's `other` (zero without one). A masked store writes only where the
mask holds. A reduction folds its axis in index order, 0 to n - 1. Triton leaves the order open
(its GPU code combines in a layout-dependent tree), so a float sum is Triton's semantics in any
order. A reduction to a scalar is computed by every thread from shared memory, so every thread has
the same value, as Triton's scalars do. `scf.for` loops carry scalars and tensors (`iter_args`).

Supported: `tt.get_program_id`, `tt.get_num_programs`, `tt.make_range`, `tt.splat`,
`tt.broadcast`, `tt.expand_dims`, `tt.reshape`, `tt.addptr`, `tt.load` (mask, other),
`tt.store` (mask), `tt.reduce` and `tt.scan` along any axis with any combiner of scalar operations
and any number of operands (argmax carries values and indices), `tt.dot`, `tt.atomic_rmw` (relaxed
add, min, max, and, or, xor, exchange on 32-bit integers; add and exchange on float32), `scf.for`
(with scalars, tensors and tensors of pointers carried), `scf.if` on a scalar (results yielded by
both branches), `scf.while` carrying scalars and tensors (`while (true)` with the condition's `break`; a loop that carries tensors or reduces tests `qpel.barrier_count(c) == 0`, since its scalar condition is the same in every thread but not provably so), `tt.join` and `tt.split` (a last axis of 2),
`tt.trans`, an early `return` under an `if` on a scalar (Triton's `cf.cond_br` to a block ending in `tt.return`: `if (c) { ...; return; }`), `tl.debug_barrier()`, libdevice calls (`triton.language.extra.libdevice`, `tt.extern_elementwise`) with a
qpel equivalent (erf, tanh, exp, log, log1p, expm1, sin, cos, sqrt, floor, ceil, fma and others),
exact operations (`tl.maximum`/`tl.minimum` with `propagate_nan`, `tl.clamp`,
`tl.math.div_rn`, `tl.math.sqrt_rn`, `tl.math.fma`, `tl.umulhi`, bitcasts), block pointers (`tl.make_block_ptr`, `tl.advance`: Triton's own TTIR passes
rewrite them to pointer tensors with boundary masks, which the lowering reads like any others), `arith` integer and
float arithmetic, comparisons, `select`, casts, `maxnumf`/`minnumf`/`maxsi`/`minsi`, and the
`math` functions qpel has (`exp`, `exp2`, `log`, `log2`, `sqrt`, `absf`, `sin`, `cos`, `tanh`,
`floor`, `ceil`). Anything else is reported by operation and line (`Unsupported`).

## Numerics and the behaviour rows

`differential/triton.py` runs every kernel of `corpus/triton` on seeded cases through Triton's
interpreter (`TRITON_INTERPRET=1`, numpy on the CPU, in the container: the reference), qpel's
CUDA on the CPU simulator, and qpel's Metal on the Apple GPU.

- **Element-wise float operations** (+ - * /, sqrt, comparisons, selects) agree bit for bit.
  The reference is the Triton language, not Triton's NVIDIA code: by default Triton lowers fp32
  `/` and `exp` to approximate PTX instructions (`div.full.f32`, `ex2.approx`). qpel's division is
  correctly rounded, as the interpreter's is.
- **Bounded math functions** (`exp`, `log`, ...) agree within 2 * MATH_ULPS (NUMERICS.md).
  Triton's interpreter cannot run libdevice; `reference.py` installs numpy float32 versions of
  `rsqrt` (1 / sqrt, as the interpreter's own `tl.math.rsqrt`), `tanh` and `erf` in its place. qpel
  computes libdevice's `rsqrt` as `1.0f / math.sqrt(x)`. Where a later step cancels (Liger's GeGLU
  backward: `1 - tanh^2`), a case gives the output a scale from the terms around it (`case.scales`).
- **Float sum reductions** over n elements agree within 2 * ceil(log2 n) + 4 more epsilons of
  max(1, |value|): numpy sums pairwise, qpel in index order. On these cases the largest difference
  measured was 3 epsilons.
- **Dots** (`tl.dot`, float32 with `input_precision="ieee"`) fold K in index order, every product
  and sum rounded. They compare like sums, with epsilons of max(1, |value|, Σ|a_ik b_kj|): the size
  of the products bounds a dot's rounding error, not the size of its result. On the matmul cases
  qpel is within 1.8 of those epsilons of the exact product (float64), the interpreter within 1.4.
  A dot in tf32 or bf16 input precision is rejected (it rounds its inputs: another computation).
  A dot of float16 (or bfloat16) tensors converts them to float32 first: their products are
  exact in float32, so it is the float32 dot of their values.
- **float16 outputs** of a program with any tolerance may differ by one float16 ulp: a float32
  result within the tolerance can round to either neighbour. Fused attention (float16 Q, K, V and
  output): 10040 of 10048 outputs bit for bit, 8 one float16 ulp apart.
- **`tl.math.fma`**: Triton's interpreter computes it as x * y + z with two roundings (numpy
  has no fused multiply-add); Triton's NVIDIA code (`fma.rn.f32`) and qpel round once. An fma's
  result compares with the interpreter within one epsilon of |x * y| + |z|, and with Triton's own
  PTX on GPGPU-Sim bit for bit (precise_ops_kernel: 137 of 1000 values differ from the interpreter,
  none from Triton's PTX).
- **libdevice**: Triton's interpreter cannot run libdevice calls, so a kernel using them has a
  float64 oracle instead (`ORACLES` in differential/triton.py), compared within the bounded-math
  tolerance; its Triton PTX on GPGPU-Sim compares with qpel's CUDA as any other's.
- qpel's CUDA and Metal agree with each other bit for bit, bounded math functions apart.

## Triton's own NVIDIA code (GPGPU-Sim)

`python3 -m differential.triton_nvidia` (and `QPEL_NVIDIA=1` in the tests) runs the PTX Triton
compiled for each corpus kernel on GPGPU-Sim (gpgpusim/README.md), on the same cases, against
Triton's interpreter and against qpel's CUDA. GPGPU-Sim implements none of the driver API Triton's
launcher uses, so a stub CUDA program with the kernel's name and parameters (and Triton's two
scratch pointers) is built, then rebuilt from nvcc's own steps after the PTX with Triton's PTX in
its place; the executable embeds Triton's code and GPGPU-Sim runs it as any CUDA program's.
Directives GPGPU-Sim's parser does not read, and that do not change the computation, are
rewritten first (`for_gpgpusim`: the PTX version, `.reqntid`, debug information, pointer
qualifiers, one-register braces, `.shared::cta`, `atom`'s default order and scope spelled out).
Triton's NVIDIA code approximates fp32 `/` (`div.full.f32`) and `exp` (`ex2.approx`), so a
kernel that divides or calls `exp` compares within a few more ulp; the others compare as the
interpreter rows do. All seventeen kernels pass both rows. Getting there found a GPGPU-Sim bug:
`setp.ne.b32` compared bits above the register's width, and Triton's matmul loop never ended
(`gpgpusim/qpel-setp.patch`, image `qpel14`).

## Corpus

`corpus/triton/kernels.py` holds kernels in the style of the Triton tutorials (vector addition,
fused softmax, layer norm, matrix multiplication with the grouped program order), plus an
element-wise select with scalar arguments, a 2D strided transpose-copy, per-row statistics of a
2D tile (reductions along axis 1), a row argmax (`return_indices`), a block cumsum, a row clip
under `if`, an integer histogram with atomics, the forward pass of fused attention (the
tutorial's online softmax over key blocks with two dots per block, float16 inputs and output),
a matmul through block pointers, a register transpose (`tl.trans`) and a data-dependent `while`
(Collatz steps). Each is a fixture (`triton_<kernel>`), so it is part of the round-trip matrix
(the TTIR lowers to exactly the fixture, and qpel's own CUDA and Metal import back to it) and of
the nvcc check.
