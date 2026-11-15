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

The common core now validates resource types/extents, local kernel dispatches, exact binding order/count/type, grid/shape agreement (a buffer is sized by the dispatch grid or by a shape the kernel receives in the same dispatch, so a kernel can know the length of everything it indexes; buffers of one dispatch may differ in size), alias restrictions and completion barriers. Read/write effects propagate through helper calls and buffer pointer offsets; conditional buffer expressions conservatively include both branches. Address computations are traversed for additional reads. Outputs require a producing stage; input resources cannot be written.

Resources become available only after the preceding stage completes. This deliberately conservative whole-resource model rejects a same-stage read/write of an uninitialized resource, even if a particular kernel orders its local accesses. Conversely, a conditional or partial write still counts as a resource write: the checker is not proof of complete initialization, convergence, bounds or freedom from data races. Runtime size compatibility and numeric lowering need separate work.

`validate(program, "metal")` and `validate(program, "cuda")` run the same graph, grammar, scalar typing, scope and pipeline checks without connecting to MPS. MPS keeps its own editor-time checks (`QpelChecks`) for immediate feedback; the Python checks are authoritative for builds.

## IR version 3

Types are terms: `ScalarType {scalar}` and `BufferType {element, access}` construct `u8 … i64`, `bf16`, `f16`, `f32`, `bool` and `buffer<T, read|write|atomic>`; the earlier concept names (`U32Type`, `ReadOnlyF32BufferType`, ...) are spellings of the same terms. Every checker reads types through `qpel_types.term`. See NUMERICS.md for the version 3 numeric rules.

## IR version 4

Thread-private local arrays, compare-and-swap and in-place resources. `ArrayType {element, length}` (term `array<T, N>`, a scalar `T` and 1 to 1024 elements) types a local `Variable`, whose value is an `ArrayValue` with exactly N `elements`. An array is not a value: its name may only be the base of an `Index`, which reads an element or, as an `Assign` target, writes one. Both backends emit `T name[N] = {...};` (thread memory in Metal). A constant index outside the array is a validation error; NUMERICS.md has the rules for the rest. `AtomicCompareExchange {address, expected, value}` returns the value it found, as CUDA's `atomicCAS` does. IR version 6 adds `AtomicMax`, `AtomicAnd`, `AtomicOr`, `AtomicXor` and `AtomicExchange {address, value}`, and atomic buffers of `f32` (load, store, add, exchange) and `u64` (add, min, max, as statements; a kernel does not both add and take min/max), checked per element in `qpel_types.ATOMIC_OPERATIONS` (NUMERICS.md, Atomics). `Bitcast {to, operand}` (version 6) reads an f32's bits as u32 or i32 and back. `VectorType {element, length}` (term `vec<T, N>`, T one of i32, u32, f32 and N 2 to 4) is a value: a local, a helper parameter or result, assigned whole; `VectorValue {element, length}` builds one from N elements, and `Member` x, y, z, w reads an element or, as an `Assign` target of a vector variable, writes one. Backends use the platform vectors (CUDA `float3`/`make_float3`, Metal `float3(...)`); there is no vector arithmetic. A helper `Parameter` with `reference` (text `ref p: T`, scalar or vector) is the caller's variable, read and written in place (CUDA `T&`, Metal `thread T&`); its argument must be a local variable or reference parameter of exactly that type, and one variable cannot be passed by reference twice in a call. Shared memory and barriers (Groups kernels): `SharedType {element, length}` (term `shared<T, N>`, T a scalar or vector, at most 16384 bytes per kernel) types a `Variable` without a value at the top level of the kernel body; it is only indexed (read and written). `Barrier` (text `qpel.barrier()`, a statement) waits for every thread of the group and orders its device and shared memory; `BarrierCount {value}` (`qpel.barrier_count(c)`) is a barrier that returns how many threads' condition holds (CUDA `__syncthreads_count`; on Metal a threadgroup atomic between barriers). Every barrier must be reached by every thread of the group: qpel_uniform.py checks that each is under conditions and loops that are the same for the whole group (built from parameters, group indices and sizes, barrier counts and buffer loads at such indices, never from the thread's own index or shared-memory reads), inside no loop that some threads leave early, and that a kernel with barriers has no return only some threads take. A `For` may have several steps (`i = i + 1, n = n - 8`). A buffer `Resource` has an extent (any shape of the pipeline; a kernel need not receive it, as the original's need not) or a fixed `length` (text `[16]`, a camera matrix). It may have a `scale` (2 to 4096, text `in M * 4`): it holds that many elements per element of its extent, as an `[M, 4]` tensor does; hosts size it volume × scale and refuse more than INT32_MAX elements. A pipeline `Resource` of kind `inout` arrives with data, as an input does, may be written by the kernels, and is returned, as an output is. A loop whose condition is the literal `true` (or a `for` without one) and that no `break` of its own leaves counts as ending a function, so a helper may end with `while (true) { ... return x; }`. Programs without these constructs stay at their earlier version.

## IR version 5

Subgroup (SIMD-group, warp) operations, in Groups kernels and the helpers they call. A subgroup is 32 lanes with consecutive linear thread indices in the group (NVIDIA warps, Apple SIMD-groups). `SubgroupLane`, `SubgroupIndex` and `SubgroupSize` (text `qpel.subgroup_lane`, `qpel.subgroup_index`, `qpel.subgroup_size`; u32, kernels only) are the lane within the subgroup, the subgroup within the group, and 32. `SubgroupShuffle {mode, value, operand}` (text `qpel.shuffle(v, lane)`, `qpel.shuffle_xor(v, mask)`, `qpel.shuffle_down(v, delta)`, `qpel.shuffle_up(v, delta)`) reads `value` (u32, i32 or f32) from another lane. `SubgroupReduce {operator, value}` (text `qpel.subgroup_add|min|max(v)` on u32, i32 or f32; `qpel.subgroup_and|or|xor(v)` on u32 or i32) gives every lane the same combination of all 32 values. NUMERICS.md (Subgroups) defines the results exactly. All 32 lanes must take part: qpel_uniform.py checks, as for barriers, that each shuffle or reduction (or a call to a helper using one) runs under conditions and loops that are the same for the whole subgroup, and that no return only some lanes take comes before one. Uniform for a subgroup, besides everything uniform for the group: the subgroup index and size, reduction results, and a thread's x index divided by a multiple of 32, shifted right by 5 or more, or compared with a multiple of 32. Helpers are checked with their parameters taken as varying. A dispatch of a kernel using them, directly or through calls, has a block whose x size is a multiple of 32. Backends: CUDA `__shfl_*_sync` with the full mask, Metal `simd_shuffle*` with `[[thread_index_in_simdgroup]]` and `[[simdgroup_index_in_threadgroup]]`; reductions are written out as shuffles on both. The importers read CUDA `__shfl_sync`, `__shfl_xor_sync`, `__shfl_down_sync` and `__shfl_up_sync` (full mask, width 32), `warpSize`, Metal `simd_shuffle*`, `simd_sum|min|max|and|or|xor` and the SIMD-group builtins.

Shared views. `SharedViewType {element}` (term `shared<T>`, T a scalar or vector) is a thread's pointer into its group's shared memory: a shared array (its name), or a view, plus or minus an integer (`Binary` + or -, giving `shared<T>`). It types a local `Variable`, which needs a value and may be assigned another view, and a helper `Parameter`; a kernel parameter cannot be one. A shared array of `shared<T, N>` passes where `shared<T>` is expected. `Index` on a view reads or writes an element; nothing checks its bounds statically (the CPU simulator does when it runs). For the barrier and subgroup checks, a read through a view varies, as a shared-array read does, and a write through one changes no local. Backends: CUDA `qpel_shared_view<T>` (an alias of `T*`, so the importer reads qpel's own output back), Metal `threadgroup T*`.

## IR version 7

Barriers in helper functions, as library code has them (LIBRARIES.md, qpel's CUB subset). A `Barrier` may be in a helper that Groups kernels call; `BarrierCount` stays in kernels (its Metal scratch is the kernel's). qpel_uniform.py checks a helper with barriers as it checks a kernel, with the helper's parameters taken as varying: each barrier under control flow that is the same for the whole group, and no return only some threads take before one. A call of such a helper counts as a barrier in its caller, so it too must be reached by every thread, and a kernel that calls one, directly or not, is a Groups kernel. Both backends emit the same `__syncthreads()` / `threadgroup_barrier` in the helper. A program with a barrier in a helper is version 7.

## Numeric revision

New exports use `qpel-portable-core-v1` and the `u32-modulo-v1` numeric emitter. See NUMERICS.md for exact rules, migration limitations and CPU UBSan evidence. Earlier sections describing exact native source parity and incomplete numeric lowering refer to the preceding revision; only the compatibility rendering now has native token parity. Dynamic division-by-zero, pointer validity and GPU execution remain unchecked.

See FLOAT32.md for the integrated native/common float32 milestone, CPU compilation evidence and GPU/precision limitations.

Explicit float32 fused multiply-add is implemented in both native MPS and the common IR. See FMA.md for the precision boundary, source specifications, CPU oracle and pending GPU test.

See FLOAT16.md for half storage, explicit conversions, exhaustive finite-half CPU evidence and the pending Apple GPU runner. Ordered serial and cooperative workgroup float32 sums are implemented. General shared-memory programming, tiling, matrix operations and tuning remain upcoming milestones.

See REDUCTIONS.md for the ordered per-invocation sum contract, range preconditions and pending GPU checks. This primitive does not yet provide inter-thread cooperation.

See WORKGROUPS.md for cooperative tree semantics, synchronization restrictions, threaded CPU validation and pending real GPU verification.

## Constant memory-range diagnostics

The shared validator now rejects statically impossible kernel-origin access ranges for both backend profiles. It reports memory_checks separately and explicitly keeps bounds_proven=false. See BOUNDS.md for the checked subset and limitations; dynamic bounds safety remains unproven.
