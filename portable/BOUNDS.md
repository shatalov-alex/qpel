# Constant memory-range diagnostics

The shared compiler now rejects constant access ranges that cannot fit any allocation accepted by either generated host (maximum INT32_MAX elements). This is a conservative diagnostic pass, not a bounds-safety proof, and it does not insert GPU guards or change generated algorithms.

After graph/type/pipeline checks, validate() runs qpel_bounds.py for both Metal and CUDA. Diagnostics use STATIC_BOUNDS with the offending graph node ID. Lowering fails before producing source. No native MPS concept or model persistence changes are required; immediate editor diagnostics have not been extended.

Checks cover ordinary reads/writes, atomic accesses and ordered float32 reduction ranges rooted in a kernel buffer parameter. Constant pointer offsets are separate from scalar unsigned arithmetic: pointer subtraction before the allocation is invalid, while scalar subtraction wraps modulo 2^32. Constant arithmetic, bitwise operators, comparisons, min/max, unary operations and lazy selects are evaluated with the existing u32 semantics. Mutable variable initializers are not substituted.

Examples:
- pixels[0u - 1u] is rejected: its unsigned index is 4294967295.
- pixels[2147483647u] is rejected: even the largest supported allocation ends at index 2147483646.
- pixels[4294967295u + 1u] is not rejected by this pass: the scalar index wraps to zero.
- A kernel-origin pointer minus one is rejected when used as an access base.
- A nonempty reduction whose constant start + count exceeds the limit is rejected; that range addition uses mathematical integers, not wrapping arithmetic.
- An empty reduction performs no access and is excluded from the range check.

The pass does not establish reachability, so a statically invalid constant access is rejected even in an unreachable branch. Helper parameters may be interior pointers and are not assumed to be allocation origins. Dynamic indices, actual runtime extents, parameter-dependent reductions, conditional pointers, helper call substitution, initialization and races remain unproven. A constant index below the global limit can still be out of bounds for an actual input.

validate() includes memory_checks with policy constant-ranges-v1, access_sites and bounds_proven=false. access_sites counts syntactic memory accesses examined (excluding statically empty reductions), including sites whose bounds are unknown. It must not be interpreted as a count of safe accesses.

Regression coverage includes impossible indices, modulo wrapping, negative pointer offsets, atomic one-past access, reduction ranges/empty sums, helper-origin conservatism, rejection before file emission and all 12 saved native fixtures on both backend profiles. No new GPU run is required to exercise these compiler diagnostics; this does not add GPU execution evidence.
