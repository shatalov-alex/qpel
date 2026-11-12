# Contributing to qpel

Thank you for your interest in qpel. Bug reports, kernels that fail to import, platform
differences and pull requests are all welcome.

## Before you start

- **Bugs and import failures:** open an issue with the source (or a minimal kernel), the
  command you ran and its output. For translation mismatches, include the failing
  behaviour row or fuzz seed.
- **Larger changes:** open an issue first, so we can agree on the approach. The
  [roadmap](portable/ROADMAP.md) says what is planned and in which order.

## Development setup

```sh
git clone https://github.com/shatalov-alex/qpel.git
cd qpel/portable
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

| Prerequisite | Needed for |
|---|---|
| Python 3.10+ | everything (the compiler needs only the standard library) |
| clang | CUDA import and the CPU simulator |
| Xcode with the Metal toolchain (macOS) | Metal import, Metal compile check, Apple GPU rows |
| NumPy, PyTorch | generated hosts, behaviour rows, Apple GPU tests |
| Docker | nvcc compile check, GPGPU-Sim (NVIDIA execution) rows |

## Checks

Run the checks that match your change before opening a pull request:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'   # always
python3 check_all.py --python .venv/bin/python         # compiler, importers, nvcc, fuzzing
python3 check_all.py --gpu --nvidia --python .venv/bin/python   # adds Apple GPU and GPGPU-Sim
```

CI runs the unit tests, corpus coverage, CPU behaviour rows and fuzzing on Linux. The
Apple GPU and GPGPU-Sim rows run locally.

## Changes to numerics or the IR

- Rules that affect results (rounding, conversions, math accuracy) belong in
  [`NUMERICS.md`](portable/NUMERICS.md), with a fixture that checks them on every backend.
- A new IR construct needs validation, lowering for both backends, import from both
  dialects where it exists, a text-syntax form, and a test.
- When the fuzzer finds a bug, add the reduced program to `portable/fuzz/regressions/`.

## Pull requests

- Keep each pull request to one topic, with a clear title and a description of what
  changed and how it was checked.
- Write commit messages in the imperative mood ("Add bf16 loads"), with a body that
  explains why when it is not obvious.
- New behaviour comes with tests; bug fixes come with a test that failed before.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the license of this project.
