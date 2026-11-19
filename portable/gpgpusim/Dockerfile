# GPGPU-Sim (accel-sim fork, dev branch, pinned, with qpel-fixes.patch) on the CUDA 12.6 devel image that
# nvcc_check.py already uses. It runs CUDA programs on a simulated NVIDIA GPU: a
# program linked against GPGPU-Sim's libcudart executes its embedded PTX there.
FROM nvidia/cuda:12.6.3-devel-ubuntu24.04
ARG GPGPUSIM_COMMIT=4195dd4f229cbf03f4f38c49139ae94d01d23f03
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential git xutils-dev bison flex zlib1g-dev libgl-dev python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/accel-sim/gpgpu-sim_distribution.git /opt/gpgpu-sim \
    && cd /opt/gpgpu-sim && git checkout $GPGPUSIM_COMMIT
WORKDIR /opt/gpgpu-sim
# qpel-fixes.patch: GPGPU-Sim bugs that made NVIDIA execution differ from real GPUs
# (README.md), plus the AccelWattch Makefile flags that break the build off x86.
# The first part was submitted upstream as two pull requests (their exact diff); the bar.red.popc
# parse fix (ptx_ir.cc) and the reset of barrier reduction counts after each barrier (shader.cc,
# abstract_hardware_model.h; functional mode in cuda-sim.cc/.h), all for __syncthreads_count,
# are local so far. qpel-shfl.patch (2026-11-14, upstream as #156): warp shuffles. shfl.sync with a d|p destination,
# as CUDA 12 emits it, lost its value (every shuffle read 0), and in functional mode every warp
# used warp 0's threads (getExecuteWarp did not set the warp id that shfl_impl and mma read).
# 2026-11-15: its in-range predicate is a zero flag (0 is true), which CUB's inline PTX reads.
# qpel-setp.patch (2026-11-16, upstream as #157): setp on .b16 and .b32 fell through to the .b64
# comparison (two missing breaks in CmpOp), so it compared bits above the register's width; after
# add.s32 1 + (-1) setp.ne.b32 gave true, and Triton's matmul loop never ended.
# qpel-ldmatrix.patch (2026-11-16, local so far): ldmatrix (sm_75), which Triton uses to convert a tile's
# layout through shared memory (a transposed operand of tl.dot, a 2D block), was not parsed.
# qpel-mma.patch (2026-11-17, local so far): mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32 (sm_75
# tensor cores), which qpel's CUDA uses for f16 matrices (IR version 13), was not parsed.
# qpel-scopes.patch (2026-11-17, upstream as #159): a register declared in a nested { } block was taken for a
# duplicate of the function's own and skipped, so the block wrote the function's register. nvcc writes
# __syncthreads_count as { .reg .pred %p1; ... bar.red.popc ... %p1; }: gsplat's rasterizer lost a live
# predicate and rendered wrong pixels.
# qpel-wmma.patch (2026-11-18, upstream as accel-sim PR #160, its exact diff): WMMA's wmma.mma with f16 inputs read each input's 16 bits as an
# address (*(reinterpret_cast<half *>(hex_val))), so every f16 WMMA crashed the simulator (SIGSEGV in mma_impl);
# and it summed the products in f16, rounding each product and partial sum, where tensor cores add exact
# products in f32: D = C + the products in k order, in f32.
COPY qpel-fixes.patch qpel-shfl.patch qpel-setp.patch qpel-ldmatrix.patch qpel-mma.patch qpel-scopes.patch qpel-wmma.patch /tmp/
RUN git apply /tmp/qpel-fixes.patch && git apply /tmp/qpel-shfl.patch && git apply /tmp/qpel-setp.patch \
    && git apply /tmp/qpel-ldmatrix.patch && git apply /tmp/qpel-mma.patch && git apply /tmp/qpel-scopes.patch \
    && git apply /tmp/qpel-wmma.patch
RUN bash -c 'set -eo pipefail; export CUDA_INSTALL_PATH=/usr/local/cuda; source setup_environment release; \
      make -j"$(nproc)" > /tmp/make.log 2>&1 || { tail -60 /tmp/make.log; exit 1; }; \
      ls lib/*/*/release/libcudart.so*'
ENV GPGPUSIM_ROOT=/opt/gpgpu-sim
