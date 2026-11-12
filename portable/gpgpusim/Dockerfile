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
# Simulator bugs that made NVIDIA execution differ from real GPUs (README.md): 64-bit
# immediates truncated to 32 bits; bfi.b64, mad.wide, mul.hi.{s,u}64, 64-bit shifts, 64-bit
# int <-> float conversions and signed byte loads wrong (CUDA sinf/cosf for |x| > 105615
# returned 0); f32 -> f16 conversions and f16 arithmetic rounding ties away from zero;
# abs/neg on floats getting the sign of zero wrong.
COPY qpel-fixes.patch /tmp/
RUN git apply /tmp/qpel-fixes.patch
# AccelWattch's makefile passes x86-only flags (-m64, -msse2, -mfpmath=sse); arm64 g++ rejects them.
RUN if [ "$(uname -m)" != x86_64 ]; then sed -i 's/ -m64//; s/ -msse2 -mfpmath=sse//' src/accelwattch/mcpat.mk; fi
RUN bash -c 'set -eo pipefail; export CUDA_INSTALL_PATH=/usr/local/cuda; source setup_environment release; \
      make -j"$(nproc)" > /tmp/make.log 2>&1 || { tail -60 /tmp/make.log; exit 1; }; \
      ls lib/*/*/release/libcudart.so*'
ENV GPGPUSIM_ROOT=/opt/gpgpu-sim
