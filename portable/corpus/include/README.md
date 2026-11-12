Corpus-specific header stand-ins, searched after the importer's own (`frontends/include/`), which
now hold the general ones: the CUDA runtime, driver API, fp16/bf16, cooperative groups, cuBLAS,
and PyTorch's extension API (torch, ATen, c10, pybind11). Add a header here only when a single
corpus library needs it.
