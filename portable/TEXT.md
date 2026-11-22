# The qpel text syntax

qpel programs can be written as text (`*.qpel`) as well as IR (`program.qpel.json`).
The text follows the IR one to one: `qpel_text.py` prints any program and parses it
back to the same program (`tests/test_qpel_text.py` checks this for every fixture:
parse(print(p)) equals p up to node ids and spelling, and printing again gives the
same text). Parsed programs always use IR version 3 spellings.

    python3 qpel.py print program.qpel.json          # IR -> text
    python3 qpel.py parse prog.qpel --out p.qpel.json # text -> IR (checked)
    python3 qpel.py build prog.qpel --out build/     # text builds like IR
    python3 qpel.py import kernel.cu --out k.qpel    # CUDA or Metal -> text

See `examples/saxpy.qpel`.

## Structure

    module NAME {
      struct S { field: T; ... }
      fn helper(a: T, ...) -> T { ... }                 // device function
      kernel k(p: T @binding, ...) grid1d|grid3d|groups { ... }
    }
    pipeline NAME {
      shape s;                                          // resources, in order
      input  x: buffer<f32, read>  in s;                // kind name: type in extent
      output y: buffer<f32, write> in s;
      inout  t: buffer<u32, atomic> in s;              // filled by the caller, returned (v4)
      input  c: buffer<i32, read>  in s * 4;            // 4 elements per element of s (v4)
      zeroed h: buffer<u32, atomic> in s / 256 * 16;   // 16 per 256 elements of s, rounded up (v21)
      input  m: buffer<f32, read>  [16];                // a fixed length (v4)
      value  n: i64;
      dispatch k over s block (64) (p = x, ...);        // 1D block
      dispatch k over s block (16, 16, 1) groups ("(W + 15)/16", "H", "1") (...);
    }

Helpers come before their callers. Kernel parameters carry their binding (`@0`). An optional
buffer (IR version 9) is `optional bias: buffer<f32, read> @1`, its pipeline resource
`optional input bias: ...`, and `qpel.present(bias)` tells whether the caller gave it.
`groups` gives a Groups kernel's group counts as expressions of the dispatch shape's
W, H and N (NUMERICS.md, launch geometry).

## Types

`bool u8 i8 u16 i16 u32 i32 u64 i64 f16 bf16 f32 void`, `shape3`,
`buffer<T, read|write|atomic>`, `struct S`, and for locals `array<T, N>` (IR version 4),
initialized with an array value `[a, b, c]` of exactly N elements and indexed as `a[i]`; a helper
may take one by reference (`ref items: array<f32, 4>`, IR version 10).
Shared memory is `shared<T, N>` (`let tile: shared<f32, 64>;` at a Groups kernel's top), and a
view into it is `shared<T>` (IR version 5): `let row: shared<f32> = tile + 32u;`, indexed as
`row[i]`, and a helper parameter type (`fn f(row: shared<f32>)`, called as `f(tile)`).
A matrix held by a subgroup is `matrix<f32, 8, 8>` or `matrix<f16, 8, 8>` (IR version 13), a local
only; `array<matrix<f32, 8, 8>, 4>` is an array of them (IR version 14), indexed by a value the same
for all 32 lanes.

## Statements

`let x: T = e;` (a buffer local may omit `= e`), `target = e;`, `e;`, `return;`,
`return e;`, `if (c) { } else { }`, `while (c) { }`, `do { } while (c);`,
`for (let i: T = e; c; i = i + 1) { }`, `break;`, `continue;`, `{ }`.

## Expressions

C's operators and precedence (`|| && | ^ & == != < > <= >= << >> + - * / %`,
unary `- ! ~`, `c ? a : b`); the printer parenthesises every nested operation.
Literals: `7u` (u32), `7` (i32), `7_i64` (any integer type; negative ones
`const<i64>(-7)`), `0.5f` (f32), `true`, `false`. Then:

| Text | IR |
|---|---|
| `cast<T>(e)` | Cast (and the version 1/2 casts) |
| `bitcast<T>(e)` | Bitcast: an f32's bits as u32 or i32 and back (IR version 6) |
| `view<T>(buffer)` | View |
| `math.exp(e)`, `math.fmin(a, b)`, ... | Math |
| `min(a, b)`, `max(a, b)` | Minimum, Maximum |
| `qpel.fma(a, b, c)` | F32Fma |
| `qpel.sum_f32(buffer, start, count)`, `qpel.group_sum_f32(v)` | ReduceSumF32, WorkgroupSumF32 |
| `qpel.index` | GridIndex (Grid1D) |
| `qpel.thread.x` | ThreadIndex (Grid3D, Groups) |
| `qpel.group.x`, `qpel.local.x`, `qpel.group_size.x`, `qpel.group_count.x` | Groups builtins |
| `qpel.subgroup_lane`, `qpel.subgroup_index`, `qpel.subgroup_size` | Subgroup builtins (Groups kernels, IR version 5) |
| `qpel.shuffle(v, lane)`, `qpel.shuffle_xor(v, m)`, `qpel.shuffle_down(v, d)`, `qpel.shuffle_up(v, d)` | SubgroupShuffle |
| `qpel.subgroup_add(v)`, `qpel.subgroup_min(v)`, `qpel.subgroup_max(v)`, `qpel.subgroup_and(v)`, `qpel.subgroup_or(v)`, `qpel.subgroup_xor(v)` | SubgroupReduce |
| `atomic.load(p)`, `atomic.store(p, v)`, `atomic.add(p, v)`, `atomic.min(p, v)`, `atomic.cas(p, expected, v)` | atomics on `buffer + offset`; `cas` returns the value found (IR version 4) |
| `atomic.max(p, v)`, `atomic.and(p, v)`, `atomic.or(p, v)`, `atomic.xor(p, v)`, `atomic.exchange(p, v)` | IR version 6, also `buffer<f32, atomic>` and `buffer<u64, atomic>` (NUMERICS.md, Atomics) |
| `qpel.subgroup_barrier()` | SubgroupBarrier: the subgroup's lanes wait for each other (IR version 16) |
| `qpel.matrix_fill<T>(x)`, `qpel.matrix_load(b, offset, stride)`, `qpel.matrix_store(m, b, offset, stride)`, `qpel.matrix_mma(a, b, c)` | MatrixFill, MatrixLoad, MatrixStore, MatrixMma (IR version 13; CORE.md) |
| `qpel.matrix_load_columns(b, offset, stride)`, `qpel.matrix_store_columns(m, b, offset, stride)` | MatrixLoad and MatrixStore of column-major memory (IR version 15) |
| `b[i]`, `s.field`, `f(args)` | Index, Member, Call |

`let fn module pipeline dispatch cast bitcast view math atomic qpel optional` are reserved names.
