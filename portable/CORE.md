# qpel common language core

Target architecture: author in qpel or import a supported frontend -> backend-neutral typed program graph -> shared validation/optimization -> target-specific CUDA and Metal lowering and runtime.

## Implemented in this revision

MPS is an editor and exporter only. Building `qpel.sandbox` in MPS runs its TextGen, which writes each Pipeline root together with its module as `<pipeline>.qpel.json` in `languages/qpel.sandbox/source_gen/qpel/sandbox/`. The serializer (`QpelChecks.irJson` in the MPS typesystem model) is a generic tree walk: depth-first node ids, concept names as kinds, declared properties as typed attributes, children and references by role. It contains no qpel-specific lowering. All code generation happens in this Python compiler. `tests/test_qpel_ir.py` checks that every IR fixture equals the corresponding MPS export, so model and fixture drift is caught.

The IR can originate from directly authored qpel, including the SAM 2 connected-components model. The restricted CUDA importer is only one frontend. A shared lowering emits the portable code body and launch plan; existing CUDA and Metal backend headers implement target ABI and primitives. Both backend packages are built from this same graph.

Backend profiles describe the implemented qpel subset, not all hardware capabilities. The common core supports integer, float32 and float16 buffers, flattened 1D grids, control flow and device-relaxed uint32 atomics. Float32 concepts also have native MPS structure, editors and checking rules. Unknown concepts/types/operators, missing references, containment cycles and unsupported backend profiles fail explicitly. qpel_schema.py additionally checks serialized node fields, child roles/cardinalities/concept categories, reference target categories, attribute types, identifier/literal syntax, shape members and launch-value ranges before lowering. Malformed graphs fail with ValueError diagnostics before source files are emitted. qpel_types.py now checks scalar expression types, lexical visibility, assignments, helper calls/returns, duplicate declarations, kernel ABI and atomic operand types. qpel_pipeline.py additionally checks whole-resource pipeline contracts and helper effects; generation alone is not a safety proof. Type checking does not prove numeric lowering, bounds or race safety. NUMERICS.md specifies the corrected integer lowering and its separate execution tests.

## Validation

106 tests passed (5 workgroup tests, 4 ordered-reduction tests, 5 half-storage tests, 3 FMA tests, 9 float32 tests plus the preceding 80): IR 8, portable grammar 12, scalar types/scope 16, pipeline/effects 14, numeric contract 5, translation workflow 7, CUDA parser 11, host generator 7. The grammar negative tests exercise both backend validation paths and ensure failed lowering creates no source files. Seven native model fixtures (including SAM 2 components) match the compatibility rendering token-for-token and the common launch plans exactly. Corrected numeric lowering deliberately differs; see NUMERICS.md. A package generated through IR passed 402 source/generated CPU parity cases and 134 UBSan cases. New Metal/NVIDIA execution is not claimed. Native model references and existing GPU-tested frozen artifacts were not modified. These CPU validation tests are not GPU execution evidence.

## Next language milestones

1. Specify numeric promotion/overflow, bounds, shape/layout and memory-order contracts more completely; move reusable full semantic validation to the shared core.
2. Extend precision policy and real-device coverage. Half storage with explicit float32 computation is now implemented in native MPS and the common graph; half arithmetic remains unsupported.
3. Add reductions, tiling and subgroup/threadgroup operations with capability checks and alternative lowering where needed.
4. Add matrix/attention operations that can lower to backend-optimized implementations rather than forcing literal instruction translation.
5. Separate launch tuning and optimizations (fusion, buffer reuse, scheduling) from program meaning; verify numerical tolerances and performance on each device.
6. Extend CUDA and eventually Triton frontends; a Metal source frontend and reverse source import do not yet exist.

A portable program cannot automatically guarantee equal performance or access to every vendor feature. Unsupported features require an implementation, an explicit fallback, or a diagnostic. Host runtime generation now includes PyTorch/Metal and an initial CuPy/CUDA host from the same Pipeline. CUDA host orchestration is CPU-tested, but NVIDIA compilation/execution and production readiness remain unverified. See CUDA_HOST.md.

## Offline export and generation

Build `qpel.sandbox` in MPS, then compile the exported IR:

```sh
python3 portable/qpel.py build languages/qpel.sandbox/source_gen/qpel/sandbox/PIPELINE.qpel.json --out /tmp/qpel-build
```

Use the Pipeline root's name for PIPELINE (for example `components_launch`).

## Editor storage versus language

MPS stores the editable qpel AST in `.mps` XML model files; `.mpl` files describe language modules. These are persistence and project formats, not the syntax users should author by hand. Model changes should use the MPS editor or its model API. The portable `program.qpel.json` graph is a separate compiler representation with local references. Neither XML nor JSON is executed by the GPU; the backends emit Metal/CUDA source and launch metadata.

A plain-text syntax now parses into this same graph and prints from it (TEXT.md, `qpel_text.py`). The shared grammar validation is a prerequisite for accepting graphs from additional frontends, and shared scalar type/scope validation now follows it. Whole-resource pipeline/effect checks are now shared too. Per-element initialization, numeric promotion/overflow lowering and memory safety remain incomplete. The CUDA and Metal importers (IMPORT.md) are such frontends: they build this graph from clang's AST and then run the same validation.

## Shared scalar typing (core v0)

The editor-independent checker follows the existing native rules: u8 can widen to u32; implicit u32-to-u8 narrowing and bool-to-integer conversions are rejected. Locals are scalar, declarations become visible only after their initializer, and nested locals do not escape their block. Helpers must precede callers; recursion and direct calls to kernels are rejected. Unsigned arithmetic is typed as u32 and signed arithmetic (IR version 2) as i32, with no implicit mixing; comparisons are bool; ordinary indexing and atomic operations have separate buffer contracts. Type judgments do not yet guarantee that every emitted C/C++ promotion matches this numeric model.

The tests mutate valid saved native models to exercise type errors, read-only writes, helper calls, atomic operands, kernel bindings, self-references, nested visibility and unconditional returns. Both target validation paths and lowering reject these invalid programs. Every fixture is checked against the IR that MPS exports for the matching sandbox model (`test_fixtures_match_mps_export`).

## Shared pipeline validation

The common core now validates resource types/extents, local kernel dispatches, exact binding order/count/type, grid/shape agreement, alias restrictions and completion barriers. Read/write effects propagate through helper calls and buffer pointer offsets; conditional buffer expressions conservatively include both branches. Address computations are traversed for additional reads. Outputs require a producing stage; input resources cannot be written.

Resources become available only after the preceding stage completes. This deliberately conservative whole-resource model rejects a same-stage read/write of an uninitialized resource, even if a particular kernel orders its local accesses. Conversely, a conditional or partial write still counts as a resource write: the checker is not proof of complete initialization, convergence, bounds or freedom from data races. Runtime size compatibility and numeric lowering need separate work.

`validate(program, "metal")` and `validate(program, "cuda")` run the same graph, grammar, scalar typing, scope and pipeline checks without connecting to MPS. MPS keeps its own editor-time checks (`QpelChecks`) for immediate feedback; the Python checks are authoritative for builds.

## IR version 3

Types are terms: `ScalarType {scalar}` and `BufferType {element, access}` construct `u8 … i64`, `bf16`, `f16`, `f32`, `bool` and `buffer<T, read|write|atomic>`; the earlier concept names (`U32Type`, `ReadOnlyF32BufferType`, ...) are spellings of the same terms. Every checker reads types through `qpel_types.term`. See NUMERICS.md for the version 3 numeric rules.

## IR version 4

Thread-private local arrays. `ArrayType {element, length}` (term `array<T, N>`, a scalar `T` and 1 to 1024 elements) types a local `Variable`, whose value is an `ArrayValue` with exactly N `elements`. An array is not a value: its name may only be the base of an `Index`, which reads an element or, as an `Assign` target, writes one. Both backends emit `T name[N] = {...};` (thread memory in Metal). A constant index outside the array is a validation error; NUMERICS.md has the rules for the rest. Programs without arrays stay at their earlier version.

## Numeric revision

New exports use `qpel-portable-core-v1` and the `u32-modulo-v1` numeric emitter. See NUMERICS.md for exact rules, migration limitations and CPU UBSan evidence. Earlier sections describing exact native source parity and incomplete numeric lowering refer to the preceding revision; only the compatibility rendering now has native token parity. Dynamic division-by-zero, pointer validity and GPU execution remain unchecked.

See FLOAT32.md for the integrated native/common float32 milestone, CPU compilation evidence and GPU/precision limitations.

Explicit float32 fused multiply-add is implemented in both native MPS and the common IR. See FMA.md for the precision boundary, source specifications, CPU oracle and pending GPU test.

See FLOAT16.md for half storage, explicit conversions, exhaustive finite-half CPU evidence and the pending Apple GPU runner. Ordered serial and cooperative workgroup float32 sums are implemented. General shared-memory programming, tiling, matrix operations and tuning remain upcoming milestones.

See REDUCTIONS.md for the ordered per-invocation sum contract, range preconditions and pending GPU checks. This primitive does not yet provide inter-thread cooperation.

See WORKGROUPS.md for cooperative tree semantics, synchronization restrictions, threaded CPU validation and pending real GPU verification.

## Constant memory-range diagnostics

The shared validator now rejects statically impossible kernel-origin access ranges for both backend profiles. It reports memory_checks separately and explicitly keeps bounds_proven=false. See BOUNDS.md for the checked subset and limitations; dynamic bounds safety remains unproven.
