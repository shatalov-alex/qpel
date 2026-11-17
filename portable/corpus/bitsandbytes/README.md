# bitsandbytes (CUDA corpus)

[bitsandbytes](https://github.com/bitsandbytes-foundation/bitsandbytes) (MIT, `LICENSE`), commit
a616886c (2026-10-27): its CUDA kernels for 8-bit and 4-bit quantisation (blockwise quantisation
and dequantisation in the 8-bit, FP4 and NF4 formats, 8-bit optimisers, the 4-bit inference GEMV and
GEMMs), vendored unmodified (`SHA256SUMS`, checked by `tests/test_corpus.py`): the `csrc/` CUDA
sources and headers. Left out: the CPU, XPU and Python bindings (`cpu_ops.*`, `xpu_*`,
`pythonInterface.cpp`), which no CUDA kernel includes.

Its code is only compiled and run by qpel's simulators and containers (the Python package is never
installed or run). It is the Phase 6 pilot for dequantisation (ROADMAP.md): `kDequantizeBlockwise`
in the 8-bit, FP4 and NF4 formats against qpel's CUDA and Metal.
