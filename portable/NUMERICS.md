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
- **float32 → integer `Cast`** truncates toward zero, **saturates** values outside the target's range to its minimum or maximum, and maps NaN to 0. C leaves out-of-range conversions undefined; both backends give that answer: qpel's Metal and the CPU simulator write the comparisons out (`qpel_f32_to_*`), and qpel's CUDA on the GPU uses PTX's own conversion (`cvt.rzi`, through `__float2int_rz` and friends), which saturates and gives 0 for NaN; 8- and 16-bit targets convert to 32 bits and clamp.
- **Integer → float32 `Cast`** rounds to nearest, ties to even (also for 64-bit integers, which float32 cannot hold exactly).
- **bf16** is storage only, like f16: `Cast` to f32 widens exactly; `Cast` from f32 to bf16 rounds to nearest even (`__float2bfloat16_rn`, Metal `bfloat`). Metal needs language version 3.1 (macOS 14) for `bfloat`; such packages record `"standard": "metal3.1"`. Subnormal floats are not exercised: Apple GPUs flush them to zero.
- **Kernel arguments** may be any integer type or f32. The hosts check the range per type; u16, u32 and u64 travel through torch as the signed type of the same width, and bf16 buffers travel through CuPy as their 16 bits.

Evidence: the `int_widths` fixture (`tests/fixtures/qpel_ir/int_widths/`, built by `build.py`) reads i8, u16, i16, i64, u64, f32 and bf16 buffers and an i64 argument and writes seven outputs: mixed-width arithmetic, 64-bit shifts and division including `LLONG_MIN / -1`, saturating conversions of NaN, ±inf and out-of-range floats, wrapping narrowing, negative constants and bf16 rounding. Its oracle (`oracle.py`) is plain Python integers. The generated CUDA matches it bit for bit on the CPU simulator (`tests/test_qpel_v3.py`), the generated Metal matches it on the Apple GPU (`gpu_tests`), nvcc (sm_75 to sm_90) and Metal compile it with warnings as errors, and it survives qpel → CUDA → qpel and qpel → Metal → qpel.

## Launch geometry: Groups kernels

A kernel with domain `Groups` (IR version 3) states its geometry instead of running one thread per element. It reads `GroupIndex`, `LocalIndex`, `GroupSize` and `GroupCount` (axis x, y or z; each u32): CUDA's `blockIdx`, `threadIdx`, `blockDim` and `gridDim`, Metal's `threadgroup_position_in_grid`, `thread_position_in_threadgroup`, `threads_per_threadgroup` and `threadgroups_per_grid`. Its Dispatch gives the group size (`blockSize` × `blockHeight` × `blockDepth`) and the number of groups per axis (`groupsX`, `groupsY`, `groupsZ`): integer expressions of the dispatch shape's W, H and N with `+ - * /` (C's division) and parentheses, such as `(W*H*N + 255)/256`. Both hosts launch exactly that many whole groups: CUDA `<<<groups, block>>>`, and Metal a grid of groups × block threads, so every group is full and `threads_per_threadgroup` equals `blockDim`. The kernel therefore guards its own range, as CUDA kernels do. A Groups kernel needs no shape parameter: its sizes stay ordinary arguments (`long long n`), and the stage's shape resource sizes the buffers and feeds the group counts. Launch plans mark such stages `"domain": "groups"` with `"groups": [x, y, z]`.

## Floating point: f32-strict-v1

Every float32 `+ - * /` rounds to nearest even on its own, as written; only `F32Fma` fuses a multiply and an add. Compilers otherwise contract `a*b + c` into an FMA when they choose (nvcc does by default; so does Metal), which changes the last bit. qpel's CUDA uses `__fadd_rn`, `__fsub_rn` and `__fmul_rn`, which nvcc never contracts whatever its flags, and writes division `/` (division never contracts; it is correctly rounded under nvcc's default `-prec-div=true`, so qpel's CUDA is built without `-use_fast_math` and `-prec-div=false`, as its bounded math functions already require; `__fdiv_rn` gives the same bits but kept nvcc from using its reciprocal and from hoisting divisions out of loops, Phase 6); its Metal turns contraction off with `#pragma clang fp contract(off)`. Generated sources that compute in float32 carry `// qpel numeric contract: f32-strict-v1`. The behaviour rows run originals the same way (the CPU simulator with `-ffp-contract=off`, Metal originals with the same pragma), i.e. as written. Found by the `grid_stride` example (`scale * x[i] + bias`), whose Metal output differed by one ulp before. Division on the Apple GPU was correctly rounded on 65536 random normal operands. Subnormals still flush to zero on Apple GPUs.

## Math functions

`Math {function}` (IR version 3) applies a function to float32 arguments and gives float32. Two classes:

- **Exact**: `fabs fmin fmax floor ceil trunc round copysign sqrt`. They give the same bits on every backend: `round` rounds halves away from zero and keeps the sign of a zero result (Metal's `round` returns +0 for -0.3 and even for -0, so qpel's Metal writes `copysign(round(x), x)`; found by llama.cpp's round operation), `sqrt` is correctly rounded (CUDA `__fsqrt_rn` whatever `-prec-sqrt` says; Metal `precise::sqrt`, measured correctly rounded on 65536 inputs). `fmin`/`fmax` return the other operand when one is NaN; the sign of a zero result from `fmin(-0, +0)` is not pinned.
- **Bounded**: `exp exp2 log log2 sin cos tanh` are within `MATH_ULPS` = 4 ulp of the exact result; `pow` (IR version 17) within its own 9 ulp (`MATH_BOUNDS`): CUDA documents powf within 9 ulp over its full range, and Metal's `precise::pow` measured at most 1.5 ulp on 2^21 inputs against float64, with C's special cases (a negative base with an integer exponent, signed zeros, infinities, NaN). A program's rows use the bound of its least accurate function. CUDA documents at most 2 ulp for each (without `-use_fast_math`); Metal's `precise::` versions measured at most 1 (exp, exp2), 2 (log, log2, cos, tanh) and 3 (sin) ulp on 65536 random inputs against float64. qpel's Metal calls `metal::precise::`, never `metal::fast::`, except for `fast_exp`. **`fast_exp`** (IR version 20) is e^x within 3 + floor(2|x|) ulp, a bound that grows with |x|: CUDA calls `__expf` (documented within 2 + floor(1.173|x|) ulp) and Metal `metal::fast::exp` (measured on the Apple GPU against float64 over 5M arguments in [-87, 88]: at most 1.7 ulp below |x| = 1, 8 below 8, 63 at x = -84.6, never above 3 + floor(2|x|); two arguments exceed CUDA's documented bound, so the contract takes Metal's). It is what CUDA's `__expf` imports as, so a program that asked for the fast exponential gets one on both backends. Tolerances are one number per program, so `fast_exp` counts as 19 ulp (`MATH_BOUNDS`, its bound at |x| = 8); a program with larger arguments states its own. Behaviour rows compare outputs of programs that use a bounded function within twice the bound (original and translation each contribute) or, after cancellation, within that many epsilons of max(1, |value|); everything else bit for bit. Subnormal results compare as zeros of their sign.

IR version 8 adds three bounded functions Metal does not have: `erf`, `expm1` and `log1p`. CUDA calls `erff`, `expm1f` and `log1pf` (documented within 2, 1 and 1 ulp); qpel's Metal brings its own (`qpel_erf`, `qpel_expm1`, `qpel_log1p` in backends/metal.h). They are polynomials fitted in float64 and evaluated with `fma`, together with Metal's `precise::exp` and `precise::log` (backends/math_fit.py fits them and prints the Metal code, which a test keeps identical):

- `erf`: below |x| = 1, x · (c0 + R(x²)), with c0 = 2/√π rounded to float32 and R carrying both the rest and c0's rounding error. From 1 to 3.92, 1 − exp(−x²) · Q(|x| − m), with Q fitted to exp(x²) · erfc(x) on [1, 2] and [2, 3.92]. Beyond that, ±1 (what erf rounds to in float32); NaN stays NaN.
- `expm1`: below |x| = 1, x · P(x); otherwise exp(x) − 1, where the subtraction loses at most a factor e/(e − 1) of exp's accuracy.
- `log1p`: below |x| = 0.5, x · P(x); otherwise log(1 + x).

On 2^20 inputs on the Apple GPU (uniform on [−6, 6], magnitudes from 1e−8 to 1e3 of both signs, and the range edges), the largest errors against float64 are 1 ulp (erf), 3 ulp (expm1) and 3 ulp (log1p), all within `MATH_ULPS` (gpu_tests/run_gpu_tests.py `math_v8`; tests/test_math_v8.py runs the same check on the CPU simulator and the Metal-on-CPU runner). The CUDA importer reads `erff`, `expm1f` and `log1pf` (and `erf`, `expm1`, `log1p` on floats), and the Metal importer reads qpel's own functions back.

## Small additions

- Scalar arguments may be `f16` (CUDA `__half` by value, Metal `constant half&`).
- `bool` converts to integers and float32 as C does (1 or 0), written as a `Select`.
- An element count of any integer type (`int k`, `long long n`) may size a 1D kernel; its uses read the shape's volume converted to that type.

## Local arrays (IR version 4)

- `array<T, N>` locals are private to each thread and always initialized: an `ArrayValue` gives every element. The importer fills the elements a C initializer list leaves out with zero (as C does), and gives an array without an initializer all zeros (C leaves it undefined; listed as an adaptation).
- Reading or writing an element outside the array is undefined, as for buffers. A constant index outside it is a validation error; the CPU simulator (UBSan) traps the rest when a test reaches them.

## Compare-and-swap and inout resources (IR version 4)

- `AtomicCompareExchange` on a `u32` or `i32` atomic buffer is a relaxed compare-and-swap that returns the value it found, CUDA's `atomicCAS`. Metal's exchange is weak (it may fail although it found the expected value), so qpel's Metal retries until it either succeeds or finds another value, which gives CUDA's strong result. It needs the `atomic_cas` feature. Metal has no 64-bit compare-and-swap, so qpel has none either; the importer says so. IR version 6 adds the 64-bit atomics Metal has (see Atomics).
- An `inout` resource is filled by the caller and updated by the kernels. Both generated hosts update a copy and return it, so the caller's buffer is unchanged.

## Argument structs and buffer views

- **Structs** (IR version 3): the module declares `StructDecl {name}` with scalar `Field`s; `StructType {name}` (term `struct Name`) types kernel parameters, which bind `value` resources. `Member` reads a field. Fields are laid out as C does (each aligned to its size, in order), which CUDA and Metal share; CUDA passes the struct by value, Metal as `constant Name&`. Launch plans carry the layouts (`"structs"`), and both hosts pack `{field: value}` dictionaries with the same range checks as scalar arguments.
- **Views**: a local of buffer type is a pointer into a buffer; it may start without a value and be assigned later. `View {element}` reads a buffer's bytes as another element type (`(device float*)(src0 + offset)`); `buffer + integer` offsets in elements of the buffer's type, so on an `i8` buffer it offsets in bytes. As in C, a view must be aligned for its element type and stay inside the buffer. A writable buffer may be read through a read view. Pipeline effects follow views and buffer locals (initializer and every assignment) back to their parameters.
- **Function constants** (Metal) are not part of the IR: the importer specialises a kernel to given values (`constants=`), folding the branches they decide, as Metal does when it builds the pipeline. Each specialisation is its own qpel program.

## Subgroups (IR version 5)
- **Lanes.** A subgroup is the 32 threads of a group with consecutive linear indices
  (`x + size.x * (y + size.y * z)`). `subgroup_lane` is that index mod 32, and
  `subgroup_index` is that index / 32. Every lane takes part in every operation (checked).
- **Shuffles** read the value another lane holds. The operand is taken as u32:
  - `shuffle(v, lane)` reads lane `lane mod 32`;
  - `shuffle_xor(v, m)` reads lane `l xor m`;
  - `shuffle_down(v, d)` reads lane `l + d`;
  - `shuffle_up(v, d)` reads lane `l - d`.
  A lane whose source would fall outside 0..31, or whose mask or delta is 32 or more, keeps its
  own value. That is CUDA's rule and Apple's for deltas below 32, and the generated code makes
  it hold for every operand.
- **Reductions** combine in one fixed order, the butterfly. For m = 16, 8, 4, 2, 1, each lane
  combines its value with lane `l xor m`'s. Every lane ends with the same bits.
  - Integer `add` wraps.
  - `min` and `max` on f32 are `fmin` and `fmax`, so NaN is ignored unless every value is NaN.
  - The f32 `add` is defined by that order. Lane 0's result equals the common CUDA idiom
    (`v += __shfl_down_sync(full, v, 16, 8, 4, 2, 1)`).
- **Apple's `simd_sum` is not that order.** On an M4 Pro, a Metal port using `simd_sum` on
  float32 differs from the CUDA shuffle-down original in the last bits (tests/test_subgroups.py,
  Verify). An explicit xor butterfly agrees bit for bit. The Metal importer therefore reads a
  float `simd_sum` as `subgroup_add` and records an adaptation. qpel's own Metal output never
  calls `simd_sum`. Integer `simd_sum`, min and max are exact.

## Matrices (IR version 13)
- **Held by a subgroup.** A `matrix<T, 8, 8>` belongs to the 32 lanes of a subgroup together, and
  every matrix operation is reached by all of them (the subgroup rules above). How its elements
  are spread over the lanes is the backend's (Metal's is not documented); programs never see it.
- **f32: `c + a b` in k order on CUDA.** Each element is `fmaf(a[r][7], b[7][c], ... fmaf(a[r][0],
  b[0][c], c[r][c]))`, the same bits on the CPU simulator and on NVIDIA GPUs (shuffles and fmaf,
  not tensor cores, which would round f32 inputs to tf32). Metal's `simdgroup_multiply_accumulate`
  has no documented order. The contract is the bound of any order of a sum of 8 products,
  |error| <= (8 + 2) 2^-24 (|c| + sum |a b|) per step of K / 8 products. On an M4 Pro, Metal gives
  the k-order bits for every element tested (tests/test_matrix_tiles.py), which is observed, not
  promised.
- **f16: exact products, the hardware's sum.** An f16 product is exact in f32, so `c + a b` differs
  only in the order and the precision of its additions: on NVIDIA tensor cores (`mma.sync`, sm_75
  and later) the hardware's, on Metal Apple's, on the CPU simulator and older NVIDIA GPUs k order by
  fmaf. The same bound holds. `matrix_fill<f16>(x)` and loads of f16 memory are exact; nothing
  rounds an f32 matrix to f16 (a store of an f32 matrix to f16 memory is rejected). On a Tesla T4
  (2026-11-17) the f32 GEMM matches k order bit for bit and the f16 one is within the bound.

## Atomics (IR version 6)
Every atomic is relaxed at device scope, as before. Version 6 adds operations and two element types;
each element has the operations both platforms give it:

| element | operations |
|---|---|
| `u32`, `i32` | load, store, add, min, cas (earlier versions); max, and, or, xor, exchange |
| `f32` | load, store, add, exchange |
| `u64` | add, min, max, as statements |

- **Loads and stores** are relaxed single accesses: Metal's `atomic_load_explicit` and
  `atomic_store_explicit`, and on NVIDIA GPUs `volatile` accesses, which PTX gives the semantics of
  `ld`/`st.relaxed.sys` (no read-modify-write: until Phase 6 qpel's CUDA loaded with `atomicAdd(p, 0)`
  and stored with `atomicExch`, which made sam2's union-find x1.47 slower on a T4).
- **Results are the previous value**, as CUDA's `atomicMax` and Metal's `atomic_fetch_max_explicit`
  return it; `exchange` stores its value and returns the one it replaced.
- **Order.** Atomic operations from different threads happen in no defined order. Integer add, min,
  max, and, or and xor give the same final value in any order. A float add does not: its rounding
  depends on the order, so a sum is only reproducible when every partial sum is exact. The behaviour
  rows give a program with a float atomic buffer quarter-integer float inputs of small size
  (`cases.exact_sums`), so every order gives the same bits. The value an operation returns depends
  on the order too, except for an operation on an address no other thread touches.
- **Float add** rounds to nearest even. Subnormal operands and results may be flushed to zero:
  CUDA's `atom.add.f32` on global memory does so. qpel's float atomic load reads the stored bits
  (CUDA: an atomic or with 0), so it neither flushes nor turns -0 into +0, as an add of zero would.
  There is no float min, max or compare-and-swap: Metal has none.
- **64-bit atomics** are unsigned. Metal's are min and max without a result (Apple GPU family 8 and
  later), and it has no 64-bit add. So a `u64` atomic operation gives no value, which the checker
  enforces (`ATOMIC_RESULT`); there is no 64-bit load, store or compare-and-swap. qpel's Metal adds
  a `u64` in two 32-bit halves, the low half's carry going to the high half. Once every addition is
  done, the sum is exact modulo 2^64, but between two halves another thread would see the value
  apart. A kernel, with the helpers it calls, therefore either adds to 64-bit atomics or takes
  their min and max, never both (`ATOMIC_MIX`). Separate dispatches may do either.
- Programs that use any of these need the `atomics` feature (`QPEL_ATOMICS_ENABLED`). Float atomic
  buffers require `atomics_f32`, and 64-bit ones `atomics_u64`.
- **Bitcasts** (`Bitcast {to}`, text `bitcast<T>(e)`) read the bits of an `f32` as `u32` or `i32`
  and back, exactly: NaN payloads and the sign of zero are kept (CUDA `__float_as_uint` and the
  others, Metal `as_type<T>`). They are how library code stores a float in an integer atomic.
  IR version 19 adds an `f16`'s bits as `u16` and back (CUDA `__half_as_ushort`, `__ushort_as_half`),
  as llama.cpp's iq1_m builds its half scale from bits in a union.
- **Metal compare-exchange loops.** `atomic_compare_exchange_weak_explicit(p, &expected, desired,
  ...)` imports as qpel's compare-and-swap: before its statement,
  `found = cas(p, expected, desired); exchanged = found == expected; expected = found;`, and the
  call reads `exchanged`. As a `while` condition this runs at the top of every iteration
  (`while (true) { ...; if (!condition) break; body }`); in a `do` loop at the end of the body.
  qpel's exchange is strong, which a weak one is allowed to be. Exchanges that only some
  evaluations reach (the right of `&&` or `||`, a branch of `?:`) are rejected, since moving them
  would run them every time.

## Double precision that is float (import)

qpel has no double: Metal has none. CUDA kernels instantiated for float often meet double
constants anyway (`T hy = 1. - ly;`, `y < -1.0`, `(T)0.5`, mmcv's RoIAlign and NMS), and C then
computes in double. The importer (`frontends/demote.py`) reads these as float only where the
result is the same bits:

- a double constant converted to float is that constant rounded once to float;
- a comparison of two doubles that are floats (promoted floats, constants exactly representable)
  compares the same values;
- one `+`, `-`, `*` or `/` of two such doubles, converted back to float, is the float
  operation: double carries more than 2 × 24 + 2 significant bits, so rounding the exact result
  to double and then to float is innocuous (Figueroa 1995);
- `x op= c` with float x and such a constant is the float operation.

A double intermediate used by a second operation, or a double kernel argument or buffer, stays
an error (`double`).

## Features

Each program's features (`float16_storage`, `int64`, `groups`, `math`, `scalar_arguments`, ...) come from its IR: `qpel_ir.Lowering.features()` reads its types and nodes. The launch plan and the build manifest record them, and the build enables exactly the matching support blocks of `backends/cuda.h` and `backends/metal.h` (`build_backends.FEATURE_DEFINES`); a backend profile adds what a feature needs on that target (`METAL_STANDARD`: bf16 needs Metal 3.1). Before 2026-11-11 the build searched the generated text for macro names; the switch left every generated source byte-identical.

## A Metal compiler bug: constant unsigned dividends

On the Apple GPU (via `torch.mps.compile_shader`, 2026-11-11), unsigned 32-bit `/` and `%` give wrong results when the compiler knows the dividend and it lies within 127 of 2^32: `4294967292u / 3` gave 3579139454, `(~3u) / 1` gave 4294967294. Divisions of a runtime dividend, signed division, 64-bit division and smaller constants were exact in every test. qpel's neutral code spells unsigned 32-bit division `QPEL_U32_DIV` / `QPEL_U32_REM`; CUDA uses the plain operators, and Metal passes the dividend through a `volatile` local so the compiler cannot use its constant-dividend expansion. Found by the fuzzer (`fuzz/regressions/u32_constant_dividend.qpel`).

## Single emitter

Since 2026-10-19 MPS no longer generates code; it exports IR only, and this numeric emitter is the only one. `build_backends.build` rejects source without the `u32-modulo-v1` contract line, and every manifest records `numeric_contract: u32-modulo-v1` (plus `i32-wrap-v1` when the program uses i32, `integers-v3` when it uses a version 3 construct, and `f32-strict-v1` when it computes in float32).

## Evidence

80 tests pass. The numerical test compiles emitted helper functions with clang++ and UBSan, checks all 65536 u8 pairs (including subtraction used inside a comparison, unary negation and mixed min), and checks eight u32 boundary values pairwise for arithmetic, bitwise and nonzero division/remainder. A literal multiplication formerly susceptible to signed overflow is also exercised. This is CPU C++ execution of generated expressions, not nvcc or Metal GPU validation.

Floating-point types were not introduced by the first revision (see FLOAT32.md and FLOAT16.md).
