# One-command CUDA translation

From the existing IJ-MPS-test-Project directory:

```sh
python3 portable/translate_cuda.py portable/cuda_examples/masked_copy.cu --module my_masked_copy --out /tmp/my-masked-copy-package
```

Use a new module name and output path. The command performs source validation, native MPS import, model checks/build, and backend/Metal-host packaging. Target is the existing qpel.sandbox model and its standard source_gen layout. MPS must be open with its local MCP server enabled. --project and --endpoint override their defaults; --block sets a size from 1 to 256.

For an offline source-only check:

```sh
python3 portable/translate_cuda.py portable/cuda_examples/masked_copy.cu --module my_masked_copy --check
```

--check does not contact MPS or create output files. It validates only the supported source subset, not GPU execution or all native MPS semantic checks. Source limitations and shape/count ABI adaptation are documented in CUDA_IMPORT.md.

## Package

- original.cu: exact source bytes.
- program.qpel.json: the IR that MPS exported for the imported model.
- neutral/: shared lowering output.
- generated/: Metal, CUDA, portable body, plan, generated Python host and hash manifest.
- import-report.json: source hash, parsed representation, MPS references and build status.
- translation-report.json: overall status, artifact hashes and explicit untested GPU/nvcc flags.
- USAGE.md: generated API input/output names and shape convention.

The generated Metal host can run without the JetBrains MPS IDE, using PyTorch with compile_shader and an Apple GPU. Generated package success means translation/build/package completed; it does not mean a GPU test passed.

Existing output directories and root names are rejected. Partial backend output is staged in a temporary directory; only a complete package appears as generated/. On failure, reports preserve model references. Multi-call MPS edits are not transactional: partial roots may remain and require inspection before a retry. No overwrite or implicit cleanup of user models is attempted.

## Verified workflow

The live run imported masked_copy.cu as cuda_cli_demo in the existing project and produced outputs/qpel-cli-demo in the Codex task. All recorded artifact hashes match. Its kernel bytes equal the earlier CPU-tested masked-copy package. Six CLI failure/no-mutation tests, eleven parser test groups and seven host tests passed. Real GPU validation remains pending for imported packages.
