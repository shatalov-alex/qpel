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

Masked loads give the load's `other` (zero without one). A masked store writes only where the
mask holds. A reduction folds its axis in index order, 0 to n - 1. Triton leaves the order open
(its GPU code combines in a layout-dependent tree), so a float sum is Triton's semantics in any
order. A reduction to a scalar is computed by every thread from shared memory, so every thread has
the same value, as Triton's scalars do. `scf.for` loops carry scalars and tensors (`iter_args`).

Supported: `tt.get_program_id`, `tt.get_num_programs`, `tt.make_range`, `tt.splat`,
`tt.broadcast`, `tt.expand_dims`, `tt.reshape`, `tt.addptr`, `tt.load` (mask, other),
`tt.store` (mask), `tt.reduce` (one sum, product, min or max, along any axis), `tt.dot`, `scf.for`
(with scalars, tensors and tensors of pointers carried), `arith` integer and
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
- **Float sum reductions** over n elements agree within 2 * ceil(log2 n) + 4 more epsilons of
  max(1, |value|): numpy sums pairwise, qpel in index order. On these cases the largest difference
  measured was 3 epsilons.
- **Dots** (`tl.dot`, float32 with `input_precision="ieee"`) fold K in index order, every product
  and sum rounded. They compare like sums, with epsilons of max(1, |value|, Σ|a_ik b_kj|): the size
  of the products bounds a dot's rounding error, not the size of its result. On the matmul cases
  qpel is within 1.8 of those epsilons of the exact product (float64), the interpreter within 1.4.
  A dot in tf32 or bf16 input precision is rejected (it rounds its inputs: another computation).
- qpel's CUDA and Metal agree with each other bit for bit, bounded math functions apart.

## Corpus

`corpus/triton/kernels.py` holds kernels in the style of the Triton tutorials (vector addition,
fused softmax, layer norm, matrix multiplication with the grouped program order), plus an
element-wise select with scalar arguments, a 2D strided transpose-copy and per-row statistics of a
2D tile (reductions along axis 1). Each is a fixture (`triton_<kernel>`), so it is part of the round-trip matrix
(the TTIR lowers to exactly the fixture, and qpel's own CUDA and Metal import back to it) and of
the nvcc check.
