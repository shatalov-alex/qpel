Programs the fuzzer once failed on, run first by every `python3 -m fuzz`:

- `u32_constant_dividend.qpel`: Apple's Metal compiler miscompiled `(~3u) / x` (a
  constant unsigned dividend within 127 of 2^32); qpel's Metal now launders the
  dividend (NUMERICS.md).
- `widening.qpel`: the importer dropped i32 -> i64 widenings, which would make
  `(long long)i * (long long)n` multiply in 32 bits.
- `joined_i32.qpel`: comparing an i8 with an i16 joins them in i32, which the declared
  features missed (QPEL_I32 undeclared).
