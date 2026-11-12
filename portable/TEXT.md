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
      value  n: i64;
      dispatch k over s block (64) (p = x, ...);        // 1D block
      dispatch k over s block (16, 16, 1) groups ("(W + 15)/16", "H", "1") (...);
    }

Helpers come before their callers. Kernel parameters carry their binding (`@0`).
`groups` gives a Groups kernel's group counts as expressions of the dispatch shape's
W, H and N (NUMERICS.md, launch geometry).

## Types

`bool u8 i8 u16 i16 u32 i32 u64 i64 f16 bf16 f32 void`, `shape3`,
`buffer<T, read|write|atomic>`, `struct S`, and for locals `array<T, N>` (IR version 4),
initialized with an array value `[a, b, c]` of exactly N elements and indexed as `a[i]`.

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
| `view<T>(buffer)` | View |
| `math.exp(e)`, `math.fmin(a, b)`, ... | Math |
| `min(a, b)`, `max(a, b)` | Minimum, Maximum |
| `qpel.fma(a, b, c)` | F32Fma |
| `qpel.sum_f32(buffer, start, count)`, `qpel.group_sum_f32(v)` | ReduceSumF32, WorkgroupSumF32 |
| `qpel.index` | GridIndex (Grid1D) |
| `qpel.thread.x` | ThreadIndex (Grid3D, Groups) |
| `qpel.group.x`, `qpel.local.x`, `qpel.group_size.x`, `qpel.group_count.x` | Groups builtins |
| `atomic.load(p)`, `atomic.store(p, v)`, `atomic.add(p, v)`, `atomic.min(p, v)` | atomics on `buffer + offset` |
| `b[i]`, `s.field`, `f(args)` | Index, Member, Call |

`let fn module pipeline dispatch cast view math atomic qpel` are reserved names.
