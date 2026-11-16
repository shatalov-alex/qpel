# Liger-Kernel (Triton corpus)

[Liger-Kernel](https://github.com/linkedin/Liger-Kernel) (BSD 2-Clause, `LICENSE`), commit
83746f40 (2026-11-14): its Triton kernels for LLM training (RMSNorm, LayerNorm, SwiGLU, GeGLU,
softmax, KL divergence, PolyNorm, ...), vendored unmodified (`SHA256SUMS`, checked by
`tests/test_corpus.py`): `liger_kernel/utils.py`, `liger_kernel/ops/utils.py` and the ops modules.
The two `__init__.py` files are qpel's (empty: Liger's own import its transformers integrations).
`jsd.py` is left out (it imports `liger_kernel.functional`).

`kernels.json` lists the kernels qpel compiles, each with its `function` (module:name), signature
(float32 buffers) and constexprs; `build/` holds Triton's TTIR, TTGIR, PTX and launch metadata
(frontends/triton/README.md: `compile.py` with this folder as the package root, no network).
Fifteen kernels pass Triton's interpreter against qpel's CUDA (CPU simulator) and Metal (Apple GPU),
and Liger's own PTX on GPGPU-Sim. Left out: `_softmax_multi_block_forward_kernel` (it calls
`tl.float32(...)`, which Triton 3.8 rejects).
