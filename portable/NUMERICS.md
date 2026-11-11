# qpel integer contract: u32-modulo-v1

New exports use `qpel-portable-core-v1` (IR container format remains version 1). Previously exported v0 graphs must be regenerated from their models; changing the header is not a semantics-preserving migration. Frozen old GPU packages retain their old behavior and evidence.

- Decimal integer literals denote u32 values 0..4294967295. Leading zeroes are decimal, not octal. Emission canonicalizes them and appends `u`. Boolean literals remain bool.
- Arithmetic `+ - *`, bitwise `& | ^`, unary negation, min/max and numeric comparisons explicitly convert numeric operands to u32 before the operation. Arithmetic results wrap modulo 2^32. In particular u8 subtraction does not become signed int arithmetic, and mixed u8/u32 min/max has matching operand types.
- `/` and `%` use unsigned arithmetic. Literal-zero divisors are rejected. Dynamic divisors must be nonzero: no runtime zero guard or proof is implemented.
- Comparisons return bool. Logical operators retain short-circuit behavior. Select retains lazy branch evaluation and its checked common type. No new implicit bool/integer conversion or u32-to-u8 narrowing is introduced.
- Pointer offsets are separate from modular scalar arithmetic: the unsigned offset is converted as specified, but the address must still remain valid. This contract does not make out-of-bounds access safe.

## IR version 2 and the signed contract: i32-wrap-v1

IR version 2 (`"version": 2`, semantics `qpel-portable-core-v2`) adds `else`, `for` and `do`/`while` with `break`/`continue`, signed 32-bit integers and their atomics, shifts and `~`, conversion to u8, scalar kernel arguments, 2D/3D kernels and zeroed scratch. Version 1 programs keep their meaning and their generated kernel code; a version 1 program that uses a version 2 construct is rejected. The importers write version 1 unless a version 2 construct is needed. Programs that use `i32` carry a second contract line, `// qpel numeric contract: i32-wrap-v1`, and their manifest records `u32-modulo-v1, i32-wrap-v1`.

- `I32Type`, `ReadOnlyI32BufferType`, `I32BufferType`. `I32Literal` is a non-negative decimal up to 2147483647; negative values are written with unary minus.
- No implicit conversions between i32 and the unsigned types: `I32Cast` (from u8, u32 or i32) and `U32Cast` (from i32, u8 or u32) are explicit. Converting keeps the 32-bit pattern (two's complement). `U8Cast` (from any integer) keeps the low 8 bits, as C's conversion to `unsigned char` does. `F32Cast` accepts i32. There is no float-to-integer conversion yet.
- `+ - *` and unary minus wrap modulo 2^32, as for u32. They are emitted through unsigned arithmetic, so the overflow that C leaves undefined for int cannot happen.
- `/` truncates toward zero and `%` has the sign of the dividend, as in C. `INT_MIN / -1` is `INT_MIN` and `INT_MIN % -1` is 0 (C leaves both undefined). Literal zero divisors are rejected; dynamic divisors must be nonzero, as for u32.
- `& | ^ ~` act on the 32-bit pattern. Comparisons are signed. min/max take two i32 or two unsigned operands.
- Shifts `<<` and `>>` take any integer count and use it modulo 32 (C leaves counts of 32 or more undefined). Unsigned `>>` is logical. Signed `<<` is done on the bit pattern; signed `>>` is arithmetic (sign-extending), which nvcc and Metal both implement.
- Conditions (`if`, `while`, `for`, `!`, `&&`, `||`, `?:`) accept i32 as well as bool and unsigned values.

Atomics on int: `AtomicI32BufferType` (`int*` in CUDA, `device atomic_int*` in Metal) takes the same relaxed `AtomicLoad`, `AtomicStore`, `AtomicAdd` and `AtomicMin` as the u32 atomic buffer; values and results are i32, `AtomicAdd` wraps and `AtomicMin` is signed. As before, atomic buffers are not indexed directly, and an address is the buffer plus an unsigned offset.

`do body while (condition);` is `DoWhile`; `continue` goes to the condition, as in C.

Zeroed scratch: a Pipeline resource of kind `zeroed` is scratch the host fills with zeros before the first stage, so a stage may read (or atomically update) it before any stage writes it. It models a host's `cudaMemset` / `torch::zeros`. Both hosts allocate it with `zeros` and do not return it.

Scalar kernel arguments: a kernel parameter of type U32Type, I32Type or F32Type binds a Pipeline resource of kind `value` (no extent). The host passes it per call: `run(inputs, shapes, values={'scale': 0.5, 'offset': -3})`. Both hosts check that integers are Python ints within the type's range and floats are numbers, before anything is allocated. CUDA receives the value by value; Metal reads it from a small constant buffer (`constant T&`), which is how Metal passes scalars.

2D/3D kernels: a kernel with domain `Grid3D` runs one thread per element of its grid shape (W, H, N) and reads its position with `ThreadIndex` (axis x, y or z, each u32). `GridIndex`, the flattened 1D index, belongs to `Grid1D` kernels only, and workgroup sums stay 1D. Its Dispatch has a block of `blockSize` × `blockHeight` × `blockDepth` threads (at most 256 in all); 1D kernels keep `blockHeight` and `blockDepth` at 1. CUDA launches ceil(W/bx) × ceil(H/by) × ceil(N/bz) blocks, so the kernel must guard every axis it uses. The CUDA host refuses grids taller or deeper than 65535 blocks. Metal dispatches exactly W × H × N threads. Buffers are still sized by the shape volume. The launch plan marks such stages with `"domain": "grid3d"` and `"blockShape": [bx, by, bz]`.

`if` may have an `elseBody`. A helper whose `if` and `else` both return counts as always returning. `for (init; condition; step) body` takes an optional Variable or Assign as init, an optional condition (missing means true), an optional Assign or Evaluate as step. The init variable is visible only inside the loop. `break` and `continue` must be inside a loop, and `continue` runs the step. Kernels with a workgroup sum still cannot contain loops, returns, `break` or `continue`.

Evidence: `tests/test_qpel_v2.py` compiles the emitted helpers with clang++ and UBSan and checks signed `+ - * / %`, the `INT_MIN` cases, `& ^ ~`, signed comparison and min, shift counts from 0 to 39 (every third) for i32 and u32, both casts, and a `for` loop with `continue`, `break` and `else`, against exact Python values. The `signed_delta` fixture compiles with nvcc (sm_75 to sm_90) and Metal and passes 134 cases on the Apple GPU against a NumPy model of C's semantics.

## IR version 3: types as terms, all integer widths: integers-v3

IR version 3 (`"version": 3`, semantics `qpel-portable-core-v3`) writes types as terms instead of one concept per type. Scalars: `bool u8 i8 u16 i16 u32 i32 u64 i64 f16 bf16 f32 void`; `shape3`; and `buffer<T, read|write|atomic>` for any scalar T but bool (`write` buffers are also readable; atomic buffers hold u32 or i32). The nodes are `ScalarType {scalar}` and `BufferType {element, access}`. The version 1 and 2 concept names stay valid spellings of the same terms (`U32Type` is `u32`, `ReadOnlyF32BufferType` is `buffer<f32, read>`), so earlier programs keep their meaning and their generated code. Two new expression nodes: `Cast {to}` converts to any scalar but bool, and `Constant {scalar, value}` is an integer constant of any width (a decimal, negative for signed types, range-checked). A program that uses a version 3 node must declare version 3. Generated sources carry `// qpel numeric contract: integers-v3`; manifests record it. Launch plans spell resource types as terms (`"schema": 2`); schema 1 plans with concept names are still read.

- **Widths.** u8/i8, u16/i16, u32/i32, u64/i64 (`unsigned char`/`signed char`, `unsigned short`/`short`, `unsigned int`/`int`, `unsigned long long`/`long long` in CUDA; `uchar`/`char`, `ushort`/`short`, `uint`/`int`, `ulong`/`long` in Metal).
- **Arithmetic types.** 8- and 16-bit values compute in 32 bits: unsigned ones as u32, signed ones as i32 (so `u16 * u16` is u32 and can never overflow into undefined int arithmetic, which C's promotion to `int` allows). 32- and 64-bit values compute at their own width. Storing a 32-bit result into a narrower variable or element needs a `Cast`.
- **Implicit conversions are lossless only.** A value converts implicitly when its type fits the target: an unsigned type into any wider integer, a signed type into a signed type at least as wide (`u8 → u32`, `u32 → i64`, `i16 → i64`; not `u32 → i32`, `i64 → u64` or `i8 → u8`). A binary operator on two integers uses the type both operands fit, then its arithmetic type: `i64 + u32` is i64, `i16 | u8` is i32, `u32 + i32` and `u64 + i64` are rejected (convert one explicitly).
- **Wrapping and division.** `+ - *` and unary minus wrap modulo 2^width for signed and unsigned types, emitted through the unsigned type of the same width. Signed `/` truncates and `%` follows the dividend; `MIN / -1` is MIN and `MIN % -1` is 0, at 32 and 64 bits alike. Comparisons and min/max convert both operands to the common type.
- **Shifts.** The count is taken modulo the width of the left operand's arithmetic type (32 or 64). Signed `>>` is arithmetic; signed `<<` works on the bit pattern.
- **Integer → integer `Cast`** keeps the low bits (two's complement), as C's conversion to an unsigned type does and as nvcc and Metal do for signed types.
- **float32 → integer `Cast`** truncates toward zero, **saturates** values outside the target's range to its minimum or maximum, and maps NaN to 0. C leaves out-of-range conversions undefined; qpel writes the comparisons out (`qpel_f32_to_*` helpers in both backends) so both GPUs give the same answer.
- **Integer → float32 `Cast`** rounds to nearest, ties to even (also for 64-bit integers, which float32 cannot hold exactly).
- **bf16** is storage only, like f16: `Cast` to f32 widens exactly; `Cast` from f32 to bf16 rounds to nearest even (`__float2bfloat16_rn`, Metal `bfloat`). Metal needs language version 3.1 (macOS 14) for `bfloat`; such packages record `"standard": "metal3.1"`. Subnormal floats are not exercised: Apple GPUs flush them to zero.
- **Kernel arguments** may be any integer type or f32. The hosts check the range per type; u16, u32 and u64 travel through torch as the signed type of the same width, and bf16 buffers travel through CuPy as their 16 bits.

Evidence: the `int_widths` fixture (`tests/fixtures/qpel_ir/int_widths/`, built by `build.py`) reads i8, u16, i16, i64, u64, f32 and bf16 buffers and an i64 argument and writes seven outputs: mixed-width arithmetic, 64-bit shifts and division including `LLONG_MIN / -1`, saturating conversions of NaN, ±inf and out-of-range floats, wrapping narrowing, negative constants and bf16 rounding. Its oracle (`oracle.py`) is plain Python integers. The generated CUDA matches it bit for bit on the CPU simulator (`tests/test_qpel_v3.py`), the generated Metal matches it on the Apple GPU (`gpu_tests`), nvcc (sm_75 to sm_90) and Metal compile it with warnings as errors, and it survives qpel → CUDA → qpel and qpel → Metal → qpel.

## Launch geometry: Groups kernels

A kernel with domain `Groups` (IR version 3) states its geometry instead of running one thread per element. It reads `GroupIndex`, `LocalIndex`, `GroupSize` and `GroupCount` (axis x, y or z; each u32): CUDA's `blockIdx`, `threadIdx`, `blockDim` and `gridDim`, Metal's `threadgroup_position_in_grid`, `thread_position_in_threadgroup`, `threads_per_threadgroup` and `threadgroups_per_grid`. Its Dispatch gives the group size (`blockSize` × `blockHeight` × `blockDepth`) and the number of groups per axis (`groupsX`, `groupsY`, `groupsZ`): integer expressions of the dispatch shape's W, H and N with `+ - * /` (C's division) and parentheses, such as `(W*H*N + 255)/256`. Both hosts launch exactly that many whole groups: CUDA `<<<groups, block>>>`, and Metal a grid of groups × block threads, so every group is full and `threads_per_threadgroup` equals `blockDim`. The kernel therefore guards its own range, as CUDA kernels do. A Groups kernel needs no shape parameter: its sizes stay ordinary arguments (`long long n`), and the stage's shape resource sizes the buffers and feeds the group counts. Launch plans mark such stages `"domain": "groups"` with `"groups": [x, y, z]`.

## Floating point: f32-strict-v1

Every float32 `+ - * /` rounds to nearest even on its own, as written; only `F32Fma` fuses a multiply and an add. Compilers otherwise contract `a*b + c` into an FMA when they choose (nvcc does by default; so does Metal), which changes the last bit. qpel's CUDA uses `__fadd_rn`, `__fsub_rn`, `__fmul_rn` and `__fdiv_rn`, which nvcc never contracts whatever its flags; its Metal turns contraction off with `#pragma clang fp contract(off)`. Generated sources that compute in float32 carry `// qpel numeric contract: f32-strict-v1`. The behaviour rows run originals the same way (the CPU simulator with `-ffp-contract=off`, Metal originals with the same pragma), i.e. as written. Found by the `grid_stride` example (`scale * x[i] + bias`), whose Metal output differed by one ulp before. Division on the Apple GPU was correctly rounded on 65536 random normal operands. Subnormals still flush to zero on Apple GPUs.

## Math functions

`Math {function}` (IR version 3) applies a function to float32 arguments and gives float32. Two classes:

- **Exact**: `fabs fmin fmax floor ceil trunc round copysign sqrt`. They give the same bits on every backend: `round` rounds halves away from zero and keeps the sign of a zero result (Metal's `round` returns +0 for -0.3 and even for -0, so qpel's Metal writes `copysign(round(x), x)`; found by llama.cpp's round operation), `sqrt` is correctly rounded (CUDA `__fsqrt_rn` whatever `-prec-sqrt` says; Metal `precise::sqrt`, measured correctly rounded on 65536 inputs). `fmin`/`fmax` return the other operand when one is NaN; the sign of a zero result from `fmin(-0, +0)` is not pinned.
- **Bounded**: `exp exp2 log log2 sin cos tanh` are within `MATH_ULPS` = 4 ulp of the exact result. CUDA documents at most 2 ulp for each (without `-use_fast_math`); Metal's `precise::` versions measured at most 1 (exp, exp2), 2 (log, log2, cos, tanh) and 3 (sin) ulp on 65536 random inputs against float64. qpel's Metal always calls `metal::precise::`, never `metal::fast::`. Behaviour rows compare outputs of programs that use a bounded function within twice the bound (original and translation each contribute) or, after cancellation, within that many epsilons of max(1, |value|); everything else bit for bit. Subnormal results compare as zeros of their sign.

Metal has no `erf`, `expm1` or `log1p`; qpel will define them itself rather than take each platform's.

## Small additions

- Scalar arguments may be `f16` (CUDA `__half` by value, Metal `constant half&`).
- `bool` converts to integers and float32 as C does (1 or 0), written as a `Select`.
- An element count of any integer type (`int k`, `long long n`) may size a 1D kernel; its uses read the shape's volume converted to that type.

## Single emitter

Since 2026-10-19 MPS no longer generates code; it exports IR only, and this numeric emitter is the only one. `build_backends.build` rejects source without the `u32-modulo-v1` contract line, and every manifest records `numeric_contract: u32-modulo-v1` (plus `i32-wrap-v1` when the program uses i32, `integers-v3` when it uses a version 3 construct, and `f32-strict-v1` when it computes in float32).

## Evidence

80 tests pass. The numerical test compiles emitted helper functions with clang++ and UBSan, checks all 65536 u8 pairs (including subtraction used inside a comparison, unary negation and mixed min), and checks eight u32 boundary values pairwise for arithmetic, bitwise and nonzero division/remainder. A literal multiplication formerly susceptible to signed overflow is also exercised. This is CPU C++ execution of generated expressions, not nvcc or Metal GPU validation.

Floating-point types were not introduced by the first revision (see FLOAT32.md and FLOAT16.md).
