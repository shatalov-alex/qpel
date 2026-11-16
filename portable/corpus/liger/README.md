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
Twenty-seven kernels pass Triton's interpreter against qpel's CUDA (CPU simulator) and Metal (Apple
GPU): the norms (RMSNorm, its 2D-block and modulated forms, LayerNorm, fused add + RMSNorm, PolyNorm,
group norm) forward and backward, SwiGLU (also with fused gate and up projections) and GeGLU forward and
backward, softmax forward and backward, ReLU², DyT, RoPE, KL divergence forward and backward, total
variation distance and the fused cross entropy (both returning early for an ignored label). Liger's
own PTX agrees on GPGPU-Sim for all but the 2D-block RMSNorm (Triton converts its layout with
`ldmatrix`, which GPGPU-Sim does not parse). Constexprs naming a dtype are written `"tl.float32"`;
DyT's `@triton.autotune` is unwrapped (kernels.json gives the config). Left out:
`_softmax_multi_block_forward_kernel` and `_softmax_multi_block_backward_kernel` (they call
`tl.float32(...)`, which Triton 3.8 rejects).
