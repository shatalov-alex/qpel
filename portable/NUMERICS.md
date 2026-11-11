# qpel integer contract: u32-modulo-v1

New exports use `qpel-portable-core-v1` (IR container format remains version 1). Previously exported v0 graphs must be regenerated from their models; changing the header is not a semantics-preserving migration. Frozen old GPU packages retain their old behavior and evidence.

- Decimal integer literals denote u32 values 0..4294967295. Leading zeroes are decimal, not octal. Emission canonicalizes them and appends `u`. Boolean literals remain bool.
- Arithmetic `+ - *`, bitwise `& | ^`, unary negation, min/max and numeric comparisons explicitly convert numeric operands to u32 before the operation. Arithmetic results wrap modulo 2^32. In particular u8 subtraction does not become signed int arithmetic, and mixed u8/u32 min/max has matching operand types.
- `/` and `%` use unsigned arithmetic. Literal-zero divisors are rejected. Dynamic divisors must be nonzero: no runtime zero guard or proof is implemented.
- Comparisons return bool. Logical operators retain short-circuit behavior. Select retains lazy branch evaluation and its checked common type. No new implicit bool/integer conversion or u32-to-u8 narrowing is introduced.
- Pointer offsets are separate from modular scalar arithmetic: the unsigned offset is converted as specified, but the address must still remain valid. This contract does not make out-of-bounds access safe.

## IR version 2 and the signed contract: i32-wrap-v1

IR version 2 (`"version": 2`, semantics `qpel-portable-core-v2`) adds `else`, `for` with `break`/`continue`, signed 32-bit integers, shifts and `~`. Version 1 programs keep their meaning and their generated kernel code; a version 1 program that uses a version 2 construct is rejected. The importers write version 1 unless a version 2 construct is needed. Programs that use `i32` carry a second contract line, `// qpel numeric contract: i32-wrap-v1`, and their manifest records `u32-modulo-v1, i32-wrap-v1`.

- `I32Type`, `ReadOnlyI32BufferType`, `I32BufferType`. `I32Literal` is a non-negative decimal up to 2147483647; negative values are written with unary minus.
- No implicit conversions between i32 and the unsigned types: `I32Cast` (from u8, u32 or i32) and `U32Cast` (from i32, u8 or u32) are explicit. Converting keeps the 32-bit pattern (two's complement). `F32Cast` accepts i32. There is no float-to-integer conversion yet.
- `+ - *` and unary minus wrap modulo 2^32, as for u32. They are emitted through unsigned arithmetic, so the overflow that C leaves undefined for int cannot happen.
- `/` truncates toward zero and `%` has the sign of the dividend, as in C. `INT_MIN / -1` is `INT_MIN` and `INT_MIN % -1` is 0 (C leaves both undefined). Literal zero divisors are rejected; dynamic divisors must be nonzero, as for u32.
- `& | ^ ~` act on the 32-bit pattern. Comparisons are signed. min/max take two i32 or two unsigned operands.
- Shifts `<<` and `>>` take any integer count and use it modulo 32 (C leaves counts of 32 or more undefined). Unsigned `>>` is logical. Signed `<<` is done on the bit pattern; signed `>>` is arithmetic (sign-extending), which nvcc and Metal both implement.
- Conditions (`if`, `while`, `for`, `!`, `&&`, `||`, `?:`) accept i32 as well as bool and unsigned values.

`if` may have an `elseBody`. A helper whose `if` and `else` both return counts as always returning. `for (init; condition; step) body` takes an optional Variable or Assign as init, an optional condition (missing means true), an optional Assign or Evaluate as step. The init variable is visible only inside the loop. `break` and `continue` must be inside a loop, and `continue` runs the step. Kernels with a workgroup sum still cannot contain loops, returns, `break` or `continue`.

Evidence: `tests/test_qpel_v2.py` compiles the emitted helpers with clang++ and UBSan and checks signed `+ - * / %`, the `INT_MIN` cases, `& ^ ~`, signed comparison and min, shift counts from 0 to 39 (every third) for i32 and u32, both casts, and a `for` loop with `continue`, `break` and `else`, against exact Python values. The `signed_delta` fixture compiles with nvcc (sm_75 to sm_90) and Metal and passes 134 cases on the Apple GPU against a NumPy model of C's semantics.

## Single emitter

Since 2026-10-19 MPS no longer generates code; it exports IR only, and this numeric emitter is the only one. `build_backends.build` rejects source without the `u32-modulo-v1` contract line, and every manifest records `numeric_contract: u32-modulo-v1` (plus `i32-wrap-v1` when the program uses i32).

## Evidence

80 tests pass. The numerical test compiles emitted helper functions with clang++ and UBSan, checks all 65536 u8 pairs (including subtraction used inside a comparison, unary negation and mixed min), and checks eight u32 boundary values pairwise for arithmetic, bitwise and nonzero division/remainder. A literal multiplication formerly susceptible to signed overflow is also exercised. This is CPU C++ execution of generated expressions, not nvcc or Metal GPU validation.

Floating-point types were not introduced by the first revision (see FLOAT32.md and FLOAT16.md).
