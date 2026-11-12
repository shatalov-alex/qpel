# Real-source corpus

Unmodified third-party GPU sources, used to measure how much real code qpel can
import. Each entry in `corpus.json` records where the file came from, its licence
and its SHA-256; the licence files sit next to the sources. Clang parses them offline
against the importer's declaration-only stand-ins (`frontends/include/`: CUDA runtime and
driver API, cooperative groups, PyTorch/ATen/c10, pybind11, cuBLAS); `include/` is for
stand-ins only one corpus library needs.

    python3 -m frontends.corpus             # coverage report (run in portable/)
    python3 -m frontends.corpus --details   # every blocker with its location
    python3 -m frontends.corpus --update    # rewrite coverage.json

`coverage.json` is the recorded result; `--check` (run by check_all.py) fails
when it changes. Multi-file vendored trees (`llama.cpp/`, `trellis2/`) carry a `SHA256SUMS`
for every file, headers included, checked by `tests/test_corpus.py`. See IMPORT.md, "Real-source corpus and coverage".

Entries may pin single kernels as `programs` (one kernel imported on its own, its
fixture, argument rules for the behaviour rows); see IMPORT.md.
