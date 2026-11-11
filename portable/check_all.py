"""Run every check that needs no NVIDIA hardware; exit nonzero if any of them fails.

    python3 portable/check_all.py                 # unit tests, round trips, Metal and nvcc compile, CUDA behaviour rows
    python3 portable/check_all.py --gpu           # also run gpu_tests/ on the Apple GPU
    python3 portable/check_all.py --gpu --python .venv/bin/python   # interpreter with torch

Unit tests and the GPU tests run with --python (default: this interpreter), so
NumPy-dependent tests are not silently skipped when it has NumPy installed.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gpu', action='store_true', help='Also run the Apple GPU tests')
    parser.add_argument('--no-nvcc', action='store_true', help='Skip the Docker nvcc check')
    parser.add_argument('--python', default=sys.executable, help='Interpreter for unit and GPU tests')
    args = parser.parse_args(argv)
    env = {'PYTHONDONTWRITEBYTECODE': '1'}
    steps = [('unit tests', [args.python, '-m', 'unittest', 'discover', '-s', str(HERE/'tests'), '-p', 'test_*.py']),
             ('round trips', [sys.executable, '-m', 'frontends.roundtrip']),
             ('corpus coverage', [sys.executable, '-m', 'frontends.corpus']),
             ('Metal compile', [sys.executable, str(HERE/'metal_check.py')])]
    if not args.no_nvcc:
        steps.append(('nvcc compile', [sys.executable, str(HERE/'nvcc_check.py')]))
    # Originals against what qpel makes of them; the Metal rows need the Apple GPU.
    steps.append(('behaviour rows', [args.python, '-m', 'differential.matrix'] + ([] if args.gpu else ['--cpu'])))
    if args.gpu:
        steps.append(('Apple GPU', [args.python, str(HERE/'gpu_tests/run_gpu_tests.py')]))
    results = []
    for label, command in steps:
        print(f'\n=== {label}', flush=True)
        results.append((label, subprocess.run(command, cwd=HERE, env={**os.environ, **env}).returncode))
    print('\n=== summary')
    for label, code in results:
        print(f'{"PASS" if code == 0 else "FAIL"} {label}')
    skipped = [s for s in ('nvcc compile',) if args.no_nvcc] + ([] if args.gpu else ['Apple GPU'])
    if skipped:
        print('NOT RUN', ', '.join(skipped))
    return 0 if all(code == 0 for _, code in results) else 1


if __name__ == '__main__':
    sys.exit(main())
