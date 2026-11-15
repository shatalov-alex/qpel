# Triton, to compile @triton.jit kernels to TTIR (and PTX) without a GPU, and to run them in
# Triton's interpreter (TRITON_INTERPRET=1, numpy on the CPU) for reference outputs:
# frontends/triton/README.md. torch is the CPU build (the interpreter reads torch tensors).
FROM python:3.12-slim
RUN pip install --no-cache-dir triton==3.8.0 numpy==2.2.6 && \
    pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
WORKDIR /work
