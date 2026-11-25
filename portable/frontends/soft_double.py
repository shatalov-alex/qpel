"""Exact software doubles for the importer (NUMERICS.md, double precision).

qpel has no f64: Metal has no double type. A CUDA expression that computes in double, such as gsplat's
`v_compensation * 0.5 / (compensation + 1e-6)` (a float times a double constant), is imported exactly: each double
value is its IEEE 754 binary64 bit pattern in a u64, and each operation is a call to one of the functions below,
which round to nearest even, keep subnormals, infinities and signed zeros, and give a NaN for a NaN (any NaN, as
CUDA's double operations may). The importer copies into the program the functions it calls, and the functions
those call. tests/test_soft_double.py checks every function against numpy, bit for bit, on the CPU simulator and
the Apple GPU.

Values: `double_round_pack(sign, e, m)` packs a significand m whose leading bit is bit 55 (three guard bits below
the 53) with value m * 2^(e - 1078). `double_shr_sticky` shifts right and keeps any lost bit as bit 0."""

LIBRARY = r'''

  fn double_shr_sticky(m: u64, n: u32) -> u64 {
    if (n == 0u) {
      return m;
    }
    if (n >= 64u) {
      if (m != 0_u64) {
        return 1_u64;
      }
      return 0_u64;
    }
    let kept: u64 = m >> cast<u64>(n);
    if ((m & ((1_u64 << cast<u64>(n)) - 1_u64)) != 0_u64) {
      return kept | 1_u64;
    }
    return kept;
  }
  fn double_round_pack(sign: u64, e: i32, m: u64) -> u64 {
    let exponent: i32 = e;
    let sig: u64 = m;
    if (exponent < 1) {
      sig = double_shr_sticky(sig, cast<u32>(1 - exponent));
      exponent = 1;
    }
    if (exponent > 2046) {
      return sign | 9218868437227405312_u64;
    }
    let low: u64 = sig & 7_u64;
    sig = sig >> 3_u64;
    if ((low > 4_u64) || ((low == 4_u64) && ((sig & 1_u64) != 0_u64))) {
      sig = sig + 1_u64;
    }
    let bits: u64 = (cast<u64>(exponent - 1) << 52_u64) + sig;
    if (bits >= 9218868437227405312_u64) {
      return sign | 9218868437227405312_u64;
    }
    return sign | bits;
  }
  fn double_is_nan(a: u64) -> u32 {
    if ((a & 9223372036854775807_u64) > 9218868437227405312_u64) {
      return 1u;
    }
    return 0u;
  }
  fn double_exponent(a: u64) -> i32 {
    let field: u64 = (a >> 52_u64) & 2047_u64;
    if (field != 0_u64) {
      return cast<i32>(field);
    }
    let fraction: u64 = a & 4503599627370495_u64;
    return 12 - cast<i32>(qpel.clz(fraction));
  }
  fn double_significand(a: u64) -> u64 {
    let field: u64 = (a >> 52_u64) & 2047_u64;
    let fraction: u64 = a & 4503599627370495_u64;
    if (field != 0_u64) {
      return fraction | 4503599627370496_u64;
    }
    return fraction << (qpel.clz(fraction) - 11_u64);
  }
  fn double_add(a: u64, b: u64) -> u64 {
    let sign_bit: u64 = 9223372036854775808_u64;
    let magnitude: u64 = 9223372036854775807_u64;
    let infinity: u64 = 9218868437227405312_u64;
    if ((double_is_nan(a) != 0u) || (double_is_nan(b) != 0u)) {
      return 9221120237041090560_u64;
    }
    let x: u64 = a;
    let y: u64 = b;
    if ((y & magnitude) > (x & magnitude)) {
      x = b;
      y = a;
    }
    if ((x & magnitude) == infinity) {
      if (((y & magnitude) == infinity) && ((x & sign_bit) != (y & sign_bit))) {
        return 9221120237041090560_u64;
      }
      return x;
    }
    if ((y & magnitude) == 0_u64) {
      if ((x & magnitude) == 0_u64) {
        return x & y;
      }
      return x;
    }
    let sign: u64 = x & sign_bit;
    let ex: i32 = double_exponent(x);
    let ey: i32 = double_exponent(y);
    let mx: u64 = double_significand(x) << 3_u64;
    let my: u64 = double_shr_sticky(double_significand(y) << 3_u64, cast<u32>(ex - ey));
    if ((x & sign_bit) == (y & sign_bit)) {
      let sum: u64 = mx + my;
      if (sum >= 72057594037927936_u64) {
        return double_round_pack(sign, ex + 1, double_shr_sticky(sum, 1u));
      }
      return double_round_pack(sign, ex, sum);
    }
    let difference: u64 = mx - my;
    if (difference == 0_u64) {
      return 0_u64;
    }
    let shift: u64 = qpel.clz(difference) - 8_u64;
    return double_round_pack(sign, ex - cast<i32>(shift), difference << shift);
  }
  fn double_sub(a: u64, b: u64) -> u64 {
    return double_add(a, b ^ 9223372036854775808_u64);
  }
  fn double_mul(a: u64, b: u64) -> u64 {
    let magnitude: u64 = 9223372036854775807_u64;
    let infinity: u64 = 9218868437227405312_u64;
    if ((double_is_nan(a) != 0u) || (double_is_nan(b) != 0u)) {
      return 9221120237041090560_u64;
    }
    let sign: u64 = (a ^ b) & 9223372036854775808_u64;
    if (((a & magnitude) == infinity) || ((b & magnitude) == infinity)) {
      if (((a & magnitude) == 0_u64) || ((b & magnitude) == 0_u64)) {
        return 9221120237041090560_u64;
      }
      return sign | infinity;
    }
    if (((a & magnitude) == 0_u64) || ((b & magnitude) == 0_u64)) {
      return sign;
    }
    let sa: u64 = double_significand(a);
    let sb: u64 = double_significand(b);
    let a0: u64 = sa & 4294967295_u64;
    let a1: u64 = sa >> 32_u64;
    let b0: u64 = sb & 4294967295_u64;
    let b1: u64 = sb >> 32_u64;
    let p00: u64 = a0 * b0;
    let middle: u64 = (a0 * b1) + (a1 * b0);
    let low: u64 = p00 + ((middle & 4294967295_u64) << 32_u64);
    let carry: u64 = 0_u64;
    if (low < p00) {
      carry = 1_u64;
    }
    let high: u64 = ((a1 * b1) + (middle >> 32_u64)) + carry;
    let e: i32 = (double_exponent(a) + double_exponent(b)) - 1072;
    let shift: u64 = 49_u64;
    if (high >= 2199023255552_u64) {
      shift = 50_u64;
      e = e + 1;
    }
    let m: u64 = (high << (64_u64 - shift)) | (low >> shift);
    if ((low & ((1_u64 << shift) - 1_u64)) != 0_u64) {
      m = m | 1_u64;
    }
    return double_round_pack(sign, e + 49, m);
  }
  fn double_div(a: u64, b: u64) -> u64 {
    let magnitude: u64 = 9223372036854775807_u64;
    let infinity: u64 = 9218868437227405312_u64;
    if ((double_is_nan(a) != 0u) || (double_is_nan(b) != 0u)) {
      return 9221120237041090560_u64;
    }
    let sign: u64 = (a ^ b) & 9223372036854775808_u64;
    if ((a & magnitude) == infinity) {
      if ((b & magnitude) == infinity) {
        return 9221120237041090560_u64;
      }
      return sign | infinity;
    }
    if ((b & magnitude) == infinity) {
      return sign;
    }
    if ((b & magnitude) == 0_u64) {
      if ((a & magnitude) == 0_u64) {
        return 9221120237041090560_u64;
      }
      return sign | infinity;
    }
    if ((a & magnitude) == 0_u64) {
      return sign;
    }
    let sa: u64 = double_significand(a);
    let sb: u64 = double_significand(b);
    let e: i32 = (double_exponent(a) - double_exponent(b)) + 1023;
    if (sa < sb) {
      sa = sa << 1_u64;
      e = e - 1;
    }
    let r: u64 = sa;
    let q: u64 = 0_u64;
    for (let k: u32 = 0u; k < 56u; k = k + 1u) {
      q = q << 1_u64;
      if (r >= sb) {
        r = r - sb;
        q = q | 1_u64;
      }
      r = r << 1_u64;
    }
    if (r != 0_u64) {
      q = q | 1_u64;
    }
    return double_round_pack(sign, e, q);
  }
  fn double_from_f32(x: f32) -> u64 {
    let bits: u32 = bitcast<u32>(x);
    let sign: u64 = cast<u64>(bits >> 31u) << 63_u64;
    let field: u32 = (bits >> 23u) & 255u;
    let fraction: u32 = bits & 8388607u;
    if (field == 255u) {
      if (fraction == 0u) {
        return sign | 9218868437227405312_u64;
      }
      return (sign | 9221120237041090560_u64) | (cast<u64>(fraction) << 29_u64);
    }
    if (field == 0u) {
      if (fraction == 0u) {
        return sign;
      }
      let shift: u32 = qpel.clz(fraction) - 8u;
      let normal: u32 = (fraction << shift) & 8388607u;
      return (sign | (cast<u64>(897u - shift) << 52_u64)) | (cast<u64>(normal) << 29_u64);
    }
    return (sign | (cast<u64>(field + 896u) << 52_u64)) | (cast<u64>(fraction) << 29_u64);
  }
  fn double_from_magnitude(sign: u64, value: u64) -> u64 {
    if (value == 0_u64) {
      return sign;
    }
    let shift: u64 = qpel.clz(value) - 11_u64;
    return sign | ((cast<u64>(1086 - cast<i32>(qpel.clz(value))) << 52_u64) + ((value << shift) & 4503599627370495_u64));
  }
  fn double_from_i32(i: i32) -> u64 {
    if (i < 0) {
      return double_from_magnitude(9223372036854775808_u64, cast<u64>(0_i64 - cast<i64>(i)));
    }
    return double_from_magnitude(0_u64, cast<u64>(cast<i64>(i)));
  }
  fn double_from_u32(u: u32) -> u64 {
    return double_from_magnitude(0_u64, cast<u64>(u));
  }
  fn double_to_f32(d: u64) -> f32 {
    let sign: u32 = cast<u32>(d >> 32_u64) & 2147483648u;
    let field: i32 = cast<i32>((d >> 52_u64) & 2047_u64);
    let fraction: u64 = d & 4503599627370495_u64;
    if (field == 2047) {
      if (fraction == 0_u64) {
        return bitcast<f32>(sign | 2139095040u);
      }
      return bitcast<f32>(2143289344u);
    }
    if (field == 0) {
      return bitcast<f32>(sign);
    }
    let s: u64 = fraction | 4503599627370496_u64;
    let e8: i32 = field - 896;
    let shift: i32 = 29;
    if (e8 < 1) {
      shift = 30 - e8;
      e8 = 1;
    }
    if (shift >= 64) {
      return bitcast<f32>(sign);
    }
    let kept: u64 = s >> cast<u64>(shift);
    let rest: u64 = s & ((1_u64 << cast<u64>(shift)) - 1_u64);
    let midpoint: u64 = 1_u64 << cast<u64>(shift - 1);
    if ((rest > midpoint) || ((rest == midpoint) && ((kept & 1_u64) != 0_u64))) {
      kept = kept + 1_u64;
    }
    let bits: u64 = (cast<u64>(e8 - 1) << 23_u64) + kept;
    if (bits >= 2139095040_u64) {
      return bitcast<f32>(sign | 2139095040u);
    }
    return bitcast<f32>(sign | cast<u32>(bits));
  }
  fn double_less(a: u64, b: u64) -> u32 {
    if ((double_is_nan(a) != 0u) || (double_is_nan(b) != 0u)) {
      return 0u;
    }
    let magnitude: u64 = 9223372036854775807_u64;
    if (((a | b) & magnitude) == 0_u64) {
      return 0u;
    }
    let ka: u64 = a | 9223372036854775808_u64;
    if ((a >> 63_u64) != 0_u64) {
      ka = ~a;
    }
    let kb: u64 = b | 9223372036854775808_u64;
    if ((b >> 63_u64) != 0_u64) {
      kb = ~b;
    }
    if (ka < kb) {
      return 1u;
    }
    return 0u;
  }
  fn double_less_equal(a: u64, b: u64) -> u32 {
    if ((double_is_nan(a) != 0u) || (double_is_nan(b) != 0u)) {
      return 0u;
    }
    if (double_less(b, a) != 0u) {
      return 0u;
    }
    return 1u;
  }
  fn double_equal(a: u64, b: u64) -> u32 {
    if ((double_is_nan(a) != 0u) || (double_is_nan(b) != 0u)) {
      return 0u;
    }
    if ((a == b) || (((a | b) & 9223372036854775807_u64) == 0_u64)) {
      return 1u;
    }
    return 0u;
  }
'''

SIGN = 2**63


def _parse():
    from qpel_text import parse_program
    stub = ('  kernel double_library(n: shape3 @0) grid1d {\n  }\n')
    pipeline = 'pipeline double_library_launch {\n  shape n;\n  dispatch double_library over n block (64) (n = n);\n}\n'
    program = parse_program('module double_library {\n' + LIBRARY + stub + '}\n\n' + pipeline)
    nodes = {n['id']: n for n in program['nodes']}
    functions = {nodes[f]['attributes']['name']: f for f in nodes[program['roots']['module']]['children']['functions']}
    del functions['double_library']
    return nodes, functions


_LIBRARY = None


def names():
    """The library's functions, each after the functions it calls."""
    return list(_library()[1])


def _library():
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = _parse()
    return _LIBRARY


def _subtree(nodes, root):
    out, stack = [], [root]
    while stack:
        ident = stack.pop()
        out.append(ident)
        for values in nodes[ident]['children'].values():
            stack.extend(values)
    return out


def copy(graph, name, copied):
    """Copy function `name` of the library, and those it calls, into the importer's graph (Graph.put).
    `copied` maps the names already copied to their graph ids; it is updated. Returns the id of `name`."""
    nodes, functions = _library()
    if name in copied:
        return copied[name]
    tree = _subtree(nodes, functions[name])
    for ident in tree:  # callees first, so each Call links to a function already copied
        target = nodes[ident]['links'].get('function')
        if nodes[ident]['kind'] == 'Call' and target is not None:
            copy(graph, nodes[target]['attributes']['name'], copied)
    by_function = {v: copied[k] for k, v in functions.items() if k in copied}
    new = {ident: graph.reserve() for ident in tree}
    for ident in tree:
        n = nodes[ident]
        links = {role: new.get(target) or by_function[target] for role, target in n['links'].items()}
        graph.put(new[ident], n['kind'], dict(n['attributes']),
                  {role: [new[c] for c in values] for role, values in n['children'].items()}, links)
    copied[name] = new[functions[name]]
    return copied[name]


def ordered(copied):
    """The ids of the copied functions in library order (each after its callees)."""
    return [copied[name] for name in names() if name in copied]
