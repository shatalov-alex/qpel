"""Compile every generated Metal backend with Apple's Metal compiler.

Needs Xcode's Metal Toolchain (`xcodebuild -downloadComponent MetalToolchain`).
This proves the Metal compiler accepts the generated shaders; it does not run them.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from fixture_builds import build_fixtures

HERE = Path(__file__).resolve().parent
STANDARD = 'metal3.0'


def compile_shader(source, standard):
    """Compile one shader to AIR with warnings as errors; return (ok, compiler output)."""
    result = subprocess.run(
        ['xcrun', 'metal', f'-std={standard}', '-Werror', '-c', str(source), '-o', str(source.with_suffix('.air'))],
        capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--std', default=STANDARD, help='Metal language standard (default %(default)s)')
    parser.add_argument('--keep', type=Path, help='Copy the staged sources and AIR files to this new directory')
    args = parser.parse_args(argv)
    if shutil.which('xcrun') is None:
        sys.exit('xcrun is required (Xcode)')
    version = subprocess.run(['xcrun', 'metal', '--version'], capture_output=True, text=True)
    if version.returncode != 0:
        sys.exit(version.stderr.strip() or 'Metal compiler unavailable')
    print(version.stdout.splitlines()[0])
    failed = False
    with tempfile.TemporaryDirectory(prefix='qpel-metal-') as temp:
        work = Path(temp)
        cases = work/'cases'
        standards = {}
        for name, generated in build_fixtures(work/'build'):
            (cases/name).mkdir(parents=True)
            manifest = json.loads((generated/'manifest.json').read_text())
            for shader in generated.glob('*.metal'):
                shutil.copy(shader, cases/name/shader.name)
                # a package that needs a newer standard (bfloat: Metal 3.1) records it
                standards[cases/name/shader.name] = manifest['outputs']['metal'].get('standard')
        # The checked-in SAM 2 package is shipped by sam2_adapter.py, so check it as-is too.
        (cases/'checked_in').mkdir()
        shutil.copy(HERE/'generated/components.metal', cases/'checked_in/components.metal')
        for shader in sorted(cases.glob('*/*.metal')):
            label = shader.relative_to(work)
            needed = standards.get(shader)
            ok, output = compile_shader(shader, max(args.std, needed or args.std))
            print(('PASS ' if ok else 'FAIL ') + str(label))
            if not ok:
                failed = True
                print(''.join('    ' + line + '\n' for line in output.splitlines()), end='')
        if args.keep:
            shutil.copytree(cases, args.keep)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
