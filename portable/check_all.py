"""Run every check that needs no NVIDIA hardware; exit nonzero if any of them fails.

    python3 portable/check_all.py                 # unit tests, round trips, Metal and nvcc compile, fuzzing
    python3 portable/check_all.py --gpu --python .venv/bin/python   # Apple GPU rows required (torch)
    python3 portable/check_all.py --gpu --nvidia  # also GPGPU-Sim rows and fuzzing (milestone gates)

The unit tests hold the behaviour rows of every original (CPU simulator; Apple GPU when there
is one), the fuzz regressions, declared features, the corpus coverage floor and the Apple GPU
fixture tests. They run with --python (default: this interpreter), so NumPy- and
PyTorch-dependent tests are not silently skipped when it has them. --gpu and --nvidia make
those capabilities required (QPEL_REQUIRE): a missing Apple GPU, Metal toolchain or
GPGPU-Sim image then fails the run instead of skipping.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gpu', action='store_true', help='Require the Apple GPU rows and tests (no skipping)')
    parser.add_argument('--no-nvcc', action='store_true', help='Skip the Docker nvcc check')
    parser.add_argument('--nvidia', action='store_true',
                        help='Also run the NVIDIA execution rows and fuzzing on GPGPU-Sim in Docker (milestone gates)')
    parser.add_argument('--python', default=sys.executable, help='Interpreter for unit and GPU tests')
    args = parser.parse_args(argv)
    required = (['gpu', 'metal'] if args.gpu else []) + (['nvidia'] if args.nvidia else [])
    env = {'PYTHONDONTWRITEBYTECODE': '1', 'QPEL_REQUIRE': ','.join(required)}
    if args.nvidia:
        env['QPEL_NVIDIA'] = '1'
    steps = [('unit tests', [args.python, '-m', 'unittest', 'discover', '-s', str(HERE/'tests'), '-p', 'test_*.py']),
             ('round trips', [sys.executable, '-m', 'frontends.roundtrip']),
             ('Metal compile', [sys.executable, str(HERE/'metal_check.py')])]
    if not args.no_nvcc:
        steps.append(('nvcc compile', [sys.executable, str(HERE/'nvcc_check.py')]))
    # Originals against what qpel makes of them; the Metal rows need the Apple GPU.
    steps.append(('fuzzing', [args.python, '-m', 'fuzz', '--count', '25', '--seed', '1'] + ([] if args.gpu else ['--cpu'])
                  + (['--nvidia'] if args.nvidia else [])))
    results = []
    for label, command in steps:
        print(f'\n=== {label}', flush=True)
        results.append((label, subprocess.run(command, cwd=HERE, env={**os.environ, **env}).returncode))
    print('\n=== summary')
    for label, code in results:
        print(f'{"PASS" if code == 0 else "FAIL"} {label}')
    skipped = [s for s in ('nvcc compile',) if args.no_nvcc] + ([] if args.gpu else ['Apple GPU required']) \
        + ([] if args.nvidia else ['GPGPU-Sim rows'])
    if skipped:
        print('NOT RUN', ', '.join(skipped), '(the unit tests ran what this machine has; see their skips)')
    return 0 if all(code == 0 for _, code in results) else 1


if __name__ == '__main__':
    sys.exit(main())
