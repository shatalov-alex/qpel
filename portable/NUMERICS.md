# qpel integer contract: u32-modulo-v1

New exports use `qpel-portable-core-v1` (IR container format remains version 1). Previously exported v0 graphs must be regenerated from their models; changing the header is not a semantics-preserving migration. Frozen old GPU packages retain their old behavior and evidence.

- Decimal integer literals denote u32 values 0..4294967295. Leading zeroes are decimal, not octal. Emission canonicalizes them and appends `u`. Boolean literals remain bool.
- Arithmetic `+ - *`, bitwise `& | ^`, unary negation, min/max and numeric comparisons explicitly convert numeric operands to u32 before the operation. Arithmetic results wrap modulo 2^32. In particular u8 subtraction does not become signed int arithmetic, and mixed u8/u32 min/max has matching operand types.
- `/` and `%` use unsigned arithmetic. Literal-zero divisors are rejected. Dynamic divisors must be nonzero: no runtime zero guard or proof is implemented.
- Comparisons return bool. Logical operators retain short-circuit behavior. Select retains lazy branch evaluation and its checked common type. No new implicit bool/integer conversion or u32-to-u8 narrowing is introduced.
- Pointer offsets are separate from modular scalar arithmetic: the unsigned offset is converted as specified, but the address must still remain valid. This contract does not make out-of-bounds access safe.

## Native migration guard

MPS still runs its native checks. The CLI compares a non-packagable compatibility rendering of the IR to native TextGen, checking structural export equivalence. Actual packaging uses the new numeric emitter. Source token equality with native TextGen is **not** claimed for the corrected emitter. Launch plans remain identical. Packaging manifests identify `u32-modulo-v1` versus `legacy-native`. Direct packaging of old native TextGen does not apply this correction.

## Evidence

80 tests pass. The numerical test compiles emitted helper functions with clang++ and UBSan, checks all 65536 u8 pairs (including subtraction used inside a comparison, unary negation and mixed min), and checks eight u32 boundary values pairwise for arithmetic, bitwise and nonzero division/remainder. A literal multiplication formerly susceptible to signed overflow is also exercised. This is CPU C++ execution of generated expressions, not nvcc or Metal GPU validation.

Full GPU regression and updating native TextGen to the same numeric contract remain pending. Floating-point types are not introduced by this revision.
