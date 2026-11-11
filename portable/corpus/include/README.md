Declaration-only stubs for the headers corpus sources include (PyTorch, ATen,
pybind11, the CUDA driver API), so clang can parse the files offline. They are
used for importing only and declare just what the corpus files use.
