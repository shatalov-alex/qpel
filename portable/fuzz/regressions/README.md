Programs the fuzzer once failed on, run first by every `python3 -m fuzz`:

- `u32_constant_dividend.qpel`: Apple's Metal compiler miscompiled `(~3u) / x` (a
  constant unsigned dividend within 127 of 2^32); qpel's Metal now launders the
  dividend (NUMERICS.md).
- `widening.qpel`: the importer dropped i32 -> i64 widenings, which would make
  `(long long)i * (long long)n` multiply in 32 bits.
- `joined_i32.qpel`: comparing an i8 with an i16 joins them in i32, which the declared
  features missed (QPEL_I32 undeclared).
- `unsigned_to_i32_literal.qpel`: re-importing `(int)4294967295u` made an `I32Literal`
  of 4294967295 (invalid); C wraps it to -1, which the importer now writes as a constant.

Found with `--nvidia` (GPGPU-Sim bugs, fixed by `gpgpusim/qpel-fixes.patch`; on the CPU
and Apple GPU paths these are ordinary programs):

- `nvidia_signed_byte_load.qpel`: `ld.global.s8` was not sign-extended.
- `nvidia_wide_integers.qpel`: 64-bit division by constants (`mul.hi.s64`), 64-bit
  shifts and conversions between f32 and 64-bit integers (large-argument `sinf`, which
  needs `bfi.b64` and `mad.wide`, is bounded math: `gpgpusim/selftest.cu` checks it).
- `nvidia_zero_signs.qpel`: `abs.f32(-0.0)` kept the sign; `neg.f32(+0.0)` lost it.
