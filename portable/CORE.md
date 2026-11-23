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

## IR version 8

Three more bounded math functions, which Metal does not have: `erf`, `expm1` and `log1p` (`Math {function}`, text `math.erf(x)` and so on). qpel's Metal defines them itself; NUMERICS.md (Math functions) gives how and how accurately. A program that uses one is version 8.

## IR version 9

Optional buffers, as PyTorch extensions pass optional tensors (`if (bias != nullptr)`). A kernel buffer `Parameter` may be `optional` (text `optional bias: buffer<f32, read> @1`); `BufferPresent` (text `qpel.present(bias)`, a bool the same for every thread) tells whether the caller gave it, linked to a parameter of the kernel it is in. A pipeline `Resource` bound to it is `optional` too (text `optional input bias: ...`), and only such a resource binds such a parameter (`OPTIONAL_BINDING`). Hosts leave an optional input out when the caller does not give it and an optional output when the caller names it in `omit`; reading or writing a buffer that was left out is undefined, as in CUDA. CUDA keeps the pointer (null when left out) and tests `bias != nullptr`. Metal cannot ask whether a buffer is bound, so its kernel also takes `constant uint& qpel_present_bias`, bound after all of the kernel's parameters in the order of the optional ones, which the host sets (a one-element placeholder buffer stands in for the missing one). The CUDA importer reads `p != nullptr`, `p != NULL`, `p == nullptr`, `nullptr != p` and `if (p)` on a kernel pointer parameter as presence tests and makes the parameter optional; the Metal importer reads qpel's own presence flags back. A program with an optional buffer is version 9.

## IR version 10

Arrays passed to helpers by reference, as kernels pass several items per thread (mamba's selective scan, CUB's blocked arrangements): a helper `Parameter` with `reference` may have an `ArrayType` (text `ref items: array<f32, 4>`). Its argument is the name of a local array, or of such a parameter, of exactly that element type and length; the helper reads and writes the caller's array in place. An array is still not a value otherwise (it is only indexed). CUDA writes `T (&items)[N]`, Metal `thread T (&items)[N]`. The CUDA and Metal importers read `T (&)[N]` and `const T (&)[N]` helper parameters. A program with one is version 10.

## IR version 11

Arrays of vectors: an `ArrayType` element may be a vector (`array<vec<f32, 2>, 4>`), as CUB-style code keeps several items of a pair type per thread (mamba's selective scan scans `float2` pairs). Everything that holds for arrays holds for these: thread-private locals initialized element by element (an `ArrayValue` of `VectorValue`s, zero vectors where C has no initializer), indexed, never values otherwise, and passed to helpers by reference. An element is a vector value (`items[k].x`, `items[k] = vec<f32, 2>(a, b)`). CUDA and Metal write `float2 items[4]`. The CUDA importer reads such locals (a default-constructed `float2 items[4]` has no initializer) and `float2 (&)[N]` parameters. examples/vector_arrays.qpel, tests/test_vector_arrays.py. A program with one is version 11.

## IR version 12

Module-level constants and `qpel.clz`, as bitsandbytes dequantises its 4-bit codes.

- **Constants.** `GpuModule` has `constants`: `ConstantDecl {name}` with a type (a scalar, or an array of scalars) and a value given by literals (an `ArrayValue` of one literal per element; a negative number is `-` of a literal). Every function may read it by `Name`, an array only indexed (`nf4[code]`), and none may assign it. Text: `const nf4: array<f32, 16> = [...];` among the module's functions. CUDA writes `__device__ const T name[N]`, Metal `constant T name[N]`.
- **`qpel.clz(x)`** (a `Unary` with operator `clz`) is the number of leading zero bits of an integer's pattern: on `u32` and `i32` 0 to 32, on `u64` and `i64` 0 to 64, of the operand's type (CUDA `__clz`/`__clzll`, Metal `clz`).
- **The CUDA importer** reads a program-scope array with an initializer list that no function writes, takes the address of or passes on (`__device__ static float lut[8] = {...}`) as a module constant where a function first reads it, and `__clz`, `__clzll` and Metal's `clz` as `qpel.clz`. It also reads `switch` statements, which qpel does not have: on a constant (a template parameter) as the case taken, otherwise as a local holding the value and an if-else chain, when no case falls through and no `break` is nested in a case.

examples/lookup_tables.qpel, tests/test_lookup_tables.py. A program with either is version 12.

## IR version 13

Matrices for matrix multiplication, held by a subgroup together: Metal's `simdgroup_matrix`, NVIDIA's `mma.sync` fragments.

- **Type.** `MatrixType {element}`, text `matrix<f32, 8, 8>` or `matrix<f16, 8, 8>`: an 8 by 8 matrix whose elements are spread over the 32 lanes of a subgroup. A matrix is a value of locals, expressions and (IR version 14) local arrays only (not a parameter, field or buffer element), and its elements are never read or written one by one.
- **Operations**, each an expression that every lane of the subgroup reaches together (the uniformity rules of shuffles and subgroup reductions, IR version 5), only in `groups` kernels:
  - `qpel.matrix_fill<T>(x)` (`MatrixFill {element}`, an f32 `value`): every element `x`, rounded to f16 for `T = f16`.
  - `qpel.matrix_load(base, offset, stride)` (`MatrixLoad`): element (r, c) is `base[offset + r stride + c]`, from a buffer or shared memory of f32 or f16 (its element type), `offset` and `stride` u32.
  - `qpel.matrix_store(m, base, offset, stride)` (`MatrixStore`, void): the inverse, to a writable buffer or shared memory of the matrix's element type.
  - `qpel.matrix_mma(a, b, c)` (`MatrixMma`): `c + a b`, an f32 matrix, for `a` and `b` of one element type and `c` f32.
- **Numerics** (NUMERICS.md, Matrices): each element of `c + a b` is `c` plus 8 products. With f32 elements, qpel's CUDA adds them in k order by fmaf (the same bits on the CPU simulator), and Metal in its own order, within the bound of a sum of 8 products; with f16, the products are exact in f32 and the sum order is the hardware's on both.
- **Lowering.** Metal: `simdgroup_float8x8`/`simdgroup_half8x8`, `simdgroup_load`/`simdgroup_store` with the stride as elements per row, `simdgroup_multiply_accumulate`, `make_filled_simdgroup_matrix`. CUDA: two elements per lane, row `lane / 4`, columns `2 (lane % 4)` and the next (an `mma.sync` accumulator's layout); f32 by shuffles and fmaf (tensor cores would round f32 inputs to tf32); f16 by `mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32` on sm_75 and later, with rows 8 to 15 of A zero and B's column fragment loaded with the matrix (an f16 matrix carries both layouts; nvcc drops the one a matrix never uses), and by the f32 path elsewhere.
- **Feature** `matrix` (`QPEL_MATRIX_ENABLED`), which implies `subgroups`.

examples/matrix_tiles.qpel (a tiled GEMM, f32 and f16 with f32 accumulation), tests/test_matrix_tiles.py. A program with a matrix is version 13.

## IR version 14

Arrays of matrices, as llama.cpp's `kernel_mul_mm` keeps its accumulators (`simdgroup_float8x8 mc[8]`).

- **Type.** `ArrayType {element, length}` with a matrix element, text `array<matrix<f32, 8, 8>, 4>`: a local array (1 to 1024 matrices), initialized with one matrix value per element, as every local array is. Its elements are read (`m[i]`, a matrix value) and assigned whole (`m[i] = qpel.matrix_mma(a[i / 2u], b[i % 2u], m[i])`); like a matrix, it is not a parameter, and neither is a reference to one.
- **Uniformity.** The 32 lanes hold each element together, so they pick the same one: an index is a value the same for every lane of the subgroup (built from constants, parameters, group and subgroup indices, uniform locals such as a loop counter, or `x / 32k` of a thread's x index), checked with the subgroup rules of IR version 5 (`SUBGROUP_UNIFORM`).
- **Lowering.** Both backends declare `QPEL_MATRIX_F32 m[N] = {...}`: an array of `simdgroup_float8x8` on Metal, of the two-element fragment struct on CUDA (registers once nvcc unrolls the loops with constant trip counts).

examples/matrix_blocks.qpel (each subgroup a 16 x 16 block of C from 2 x 2 accumulators, f32 and f16), tests/test_matrix_arrays.py: the CPU simulator bit for bit in k order, GPGPU-Sim (f32 bit for bit, f16 within the bound) and the Apple GPU (within the bound). A program with an array of matrices is version 14.

## IR version 15

Column-major matrix memory, as BLAS, NVIDIA's WMMA samples (B as `col_major`) and Metal's transposed `simdgroup_load` keep matrices.

- **Operations.** `qpel.matrix_load_columns(base, offset, stride)` and `qpel.matrix_store_columns(m, base, offset, stride)` (`MatrixLoad` and `MatrixStore` with `columns: true`): element (r, c) is `base[offset + c stride + r]`, where `matrix_load` has `base[offset + r stride + c]`. The rules are those of IR version 13.
- **Lowering.** Metal: `simdgroup_load` and `simdgroup_store` transposed (`ulong2(0), true`). CUDA: each lane's two elements, row `lane / 4` and columns `2 (lane % 4)` and the next, at `offset + 2 (lane % 4) stride + lane / 4` and one stride further.

examples/matrix_columns.qpel (row-major A times column-major B into column-major C, f32 and f16), tests/test_matrix_columns.py: the CPU simulator bit for bit in k order, GPGPU-Sim (f32 bit for bit, f16 within the bound) and the Apple GPU (within the bound). A program with column-major matrix memory is version 15.

## IR version 16

`SubgroupBarrier` (text `qpel.subgroup_barrier()`, a statement): the 32 lanes of a subgroup wait for each other, and each lane's earlier writes to shared and device memory are visible to the others' later reads (CUDA `__syncwarp()`, Metal `simdgroup_barrier(mem_flags::mem_device | mem_flags::mem_threadgroup)`). It orders nothing between subgroups; `qpel.barrier()` does. Every lane of the subgroup reaches it, under the subgroup rules of IR version 5 (`SUBGROUP_UNIFORM`), in Groups kernels and the functions they call.

examples/subgroup_barrier.qpel (neighbouring lanes swap values through shared memory), tests/test_subgroup_barrier.py: the CPU simulator, GPGPU-Sim and the Apple GPU. A program with a subgroup barrier is version 16.

## IR version 17

`math.pow(x, y)` (`Math {function: pow}`), a bounded function with its own bound of 9 ulp (NUMERICS.md, math functions): CUDA `powf`, Metal `metal::precise::pow`. examples/math_pow.qpel, tests/test_math_pow.py. A program with `math.pow` is version 17.

## IR version 18

Vectors of 8- and 16-bit integers: `vec<u8, N>`, `vec<i8, N>`, `vec<u16, N>`, `vec<i16, N>` for N of 2 to 4 (CUDA `uchar2`, `char4`, `ushort2`, ...; Metal the same names). As every vector they are values: built (`VectorValue`, each element converted as an assignment converts), read and written by element, copied, passed and returned; qpel has no vector arithmetic. Unlike the 32-bit vectors they are not elements of shared memory or shared views: Metal pads a `uchar3` to 4 bytes and CUDA does not. llama.cpp's `get_scale_min_k4_just2` returns a `uchar2` (the q4_K and q5_K scales).

Unaligned views: `View {element, unaligned: true}` (text `view_unaligned<T>(p)`) reads and writes its elements byte by byte, at any byte address: CUDA copies them with `memcpy` (NVIDIA faults on a misaligned wider load), Metal builds them from `uchar` loads with `as_type` (the Apple GPU's wider loads assume the type's alignment and drop the address's low bits; tests/test_llama_mul_mm_blocks.py found it in llama.cpp's q5_0, whose `uint32_t` quant bits sit at byte 2 of a 22-byte block). An unaligned view is the base of an `Index`, directly or after `+` and `-` (`view_unaligned<u32>(p + 3u)[0u]`, `(v + i)[0u]`); it is not held in a local, where its alignment would be lost. examples/unaligned_bytes.qpel, tests/test_unaligned_bytes.py.

A program with either is version 18.

## IR version 19

Bitcasts between `f16` and `u16`: `Bitcast {to: u16}` of an f16 and `Bitcast {to: f16}` of a u16 (text `bitcast<u16>(h)`, `bitcast<f16>(b)`) read the 16 bits exactly as the other type (CUDA `__half_as_ushort`, `__ushort_as_half`; Metal `as_type<ushort>`, `as_type<half>`). The importer reads a local union of a half and a 16-bit integer through them (llama.cpp's iq1m_scale_t; IMPORT.md, Unions).

Module constants of more than 1024 elements: a `ConstantDecl`'s array of scalars may hold up to 65536 bytes (local arrays stay at 1024 elements). Both backends keep such a table out of the small constant banks: CUDA `__device__ const` is global memory, Metal's program-scope `constant` the constant address space. llama.cpp's `iq1s_grid_gpu` has 2048 u32.

A program with either is version 19.

## IR version 20

`math.fast_exp(x)` (`Math {function: fast_exp}`): e^x within 3 + floor(2|x|) ulp (NUMERICS.md, math functions): CUDA `__expf`, Metal `metal::fast::exp` (backends/metal.h `qpel_fast_exp`). CUDA's `__expf` imports as it. tests/test_fast_exp.py. A program with `math.fast_exp` is version 20.

## IR version 21

Buffers of a count per tile of their extent: a resource's `per` attribute (2 to 2^20) makes its length ceil(volume(extent) / per) elements, times its `scale` if it has one; in text `output t: buffer<u32, write> in n / 256 * 2;` (2 elements per 256 elements of n, rounded up). Device-wide scans and sorts keep a partial or a histogram per tile of their input this way (LIBRARIES.md). The plan carries `per`, and the hosts (`generate_cuda_host.py`, `generate_metal_host.py`), the CPU simulator and the case generator size such buffers by it. `examples/buffer_tiles.qpel`, tests/test_buffer_tiles.py. A program with such a buffer is version 21.


Pipelines (any IR version, since 2026-11-22): a buffer resource that a pipeline writes may be bound to a later stage's read-only parameter of the same element type (`buffer<i64, write>` to `buffer<i64, read>`): a sort's output read by the next kernel. Every other binding has the parameter's own type.


## IR version 22

`math.atan2(y, x)` (`Math {function: atan2}`, two arguments): the angle of (x, y) in [-pi, pi], within `MATH_ULPS` (NUMERICS.md, math functions): CUDA `atan2f`, Metal `metal::precise::atan2`. CUDA's `atan2f` and `atan2` on floats import as it (gsplat's fisheye projection, through `glm::atan(y, x)`). examples/math_atan2.qpel, tests/test_math_atan2.py. A program with `math.atan2` is version 22.

## Optimisation passes

`qpel_opt.py` holds passes that read a validated program and tell the lowering how to emit it faster, with the same results; they never change the IR, whose meaning stays the portable one. Kept apart from the portability lowering (ROADMAP, Phase 6):

- **Whole-vector loads.** A vector built from N consecutive elements of one kernel buffer, `vec<T, N>(b[N i], ..., b[N i + N - 1])` for N of 2 or 4 (how the CUDA importer reads an original's `reinterpret_cast<const uint2 *>(b)[i]`), is emitted as one N-wide load when b's address is aligned to the vector and N i + N - 1 does not wrap i's type, and as the N element loads otherwise (a buffer passed at an odd offset). Both read the same elements. TRELLIS.2's `render` reads its tile ranges with one 64-bit load again, as the original does.
- **Whole-vector stores.** N consecutive statements of one block storing N consecutive elements of one kernel buffer, `b[N i] = v0; ...; b[N i + N - 1] = v(N-1);` for N of 2 or 4 (how the CUDA importer writes an original's `int4* bboxes; bboxes[i] = v`), are emitted as one N-wide store under the same conditions, and as the N element stores otherwise. The wide store evaluates every value before it stores any, so only values that read no memory and have no effect (locals, parameters, literals and arithmetic on them), of the buffer's element type, qualify. TRELLIS.2's `preprocess` writes its bounding boxes with two 128-bit stores on sm_75 again, as the original does (tests/test_vector_stores.py).
- **Reused atomic loads.** A loop whose condition compares an atomic load with a value that reads no memory, and whose body's first statement assigns the same load, `while (atomic.load(p + n) != n) { n = atomic.load(p + n); ... }` (the union-find of SAM 2's connected components and of disjoint-set forests generally), reads the location once: the condition assigns a local `qpel_reuse_K`, which the body reads. Nothing but the rest of the condition comes between the two relaxed loads, so both returning the same write is one of the executions the relaxed order allows. A volatile pair cannot be merged by nvcc, where the originals' plain loads were: SAM 2's `merge` has 8 strong loads on sm_75 instead of 24 (the original has 16 plain ones), 330 instructions instead of 403 (384). The importer reads the local back as the load, so the program round-trips (tests/test_reused_loads.py).
- **Read-only cache.** A kernel's read buffers are `const T* __restrict__` on CUDA (`QPEL_READ_PARAM`, `QPEL_OPTIONAL_READ_PARAM`): the pipeline binds no resource to two parameters of one dispatch (ALIAS) and nothing writes a read buffer, so nvcc may read them through the read-only data cache (`LDG.E.CONSTANT`), as it does for the originals' `const __restrict__` parameters (gsplat's rasterizer). Helpers' parameters are left as they are, since a write buffer may be passed to a helper as a read one. Metal's `device const T*` is unchanged.
- **Paired f16 multiplies.** Two statements of one block, `c = mma(a_n, b_n, ... mma(a_1, b_1, c))` and `c' = mma(a'_n, b_n, ... mma(a'_1, b_1, c'))`, f16 multiply chains with the same B at every step (how the CUDA importer writes a 16 x 16 WMMA fragment's two row blocks), are emitted as one chain over a pair of accumulators, `QPEL_MATRIX_MMA2_F16`. On CUDA each step is one `mma.sync.m16n8k8` with a in rows 0 to 7 and a' in rows 8 to 15; each row of the result is that row of A times B alone, so every element is the one its own instruction gives. Metal and the CPU simulator multiply each. The operands are locals (`x` or `x[k]`, k a literal); the second statement reads nothing the first or a statement between them writes, and the statements between (multiply chains themselves) neither read nor write what the second writes. A 16 x 16 x 16 WMMA GEMM has 4 HMMA on sm_75, as NVIDIA's original, instead of 8 (tests/test_matrix_pairs.py).
- Constant folding and common subexpressions are left to nvcc and Apple's Metal compiler, which do both: qpel's generated code for llama.cpp's `fill`, `clamp` and `float32` gives the same SASS as the originals (2026-11-16).

## Numeric revision

New exports use `qpel-portable-core-v1` and the `u32-modulo-v1` numeric emitter. See NUMERICS.md for exact rules, migration limitations and CPU UBSan evidence. Earlier sections describing exact native source parity and incomplete numeric lowering refer to the preceding revision; only the compatibility rendering now has native token parity. Dynamic division-by-zero, pointer validity and GPU execution remain unchecked.

See FLOAT32.md for the integrated native/common float32 milestone, CPU compilation evidence and GPU/precision limitations.

Explicit float32 fused multiply-add is implemented in both native MPS and the common IR. See FMA.md for the precision boundary, source specifications, CPU oracle and pending GPU test.

See FLOAT16.md for half storage, explicit conversions, exhaustive finite-half CPU evidence and the pending Apple GPU runner. Ordered serial and cooperative workgroup float32 sums are implemented. General shared-memory programming, tiling, matrix operations and tuning remain upcoming milestones.

See REDUCTIONS.md for the ordered per-invocation sum contract, range preconditions and pending GPU checks. This primitive does not yet provide inter-thread cooperation.

See WORKGROUPS.md for cooperative tree semantics, synchronization restrictions, threaded CPU validation and pending real GPU verification.

## Constant memory-range diagnostics

The shared validator now rejects statically impossible kernel-origin access ranges for both backend profiles. It reports memory_checks separately and explicitly keeps bounds_proven=false. See BOUNDS.md for the checked subset and limitations; dynamic bounds safety remains unproven.
