"""Build the qpel compiler as a pip-installable wheel (and sdist), without publishing it.

The repository keeps the compiler as flat modules next to its tests and tools (qpel.py,
qpel_ir.py, frontends/, backends/). This stages the compiler alone as one `qpel` package:

    qpel/cli.py           <- qpel.py (the `qpel` command)
    qpel/qpel_*.py, qpel/build_backends.py, qpel/generate_*_host.py
    qpel/backends/*.h     <- the backend headers the build embeds
    qpel/frontends/       <- the CUDA/Metal importers, torch-op, the clang stub headers

and rewrites the imports between them (`from qpel_ir import` -> `from qpel.qpel_ir import`).
Tests, the behaviour matrix, fuzzing and the corpus stay in the repository. Then it builds
with `uv build` and checks the wheel in a new virtual environment: the `qpel` command builds
a fixture program and the package imports without the repository on the path.

    python3 portable/packaging/build_wheel.py [--out DIR]

Nothing is uploaded. The version is VERSION below; PyPI publishing is a separate, manual step.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PORTABLE = HERE.parent
ROOT = PORTABLE.parent
VERSION = re.search(r"^VERSION = '([^']+)'", (PORTABLE/'qpel.py').read_text(), re.M).group(1)

MODULES = ['qpel_bounds', 'qpel_ir', 'qpel_opt', 'qpel_pipeline', 'qpel_schema', 'qpel_text', 'qpel_types',
           'qpel_uniform', 'build_backends', 'generate_cuda_host', 'generate_metal_host', 'qpel_mcp']
FRONTENDS = ['__init__', 'c_import', 'clang_ast', 'demote', 'facades', 'functors', 'glm', 'records', 'statics',
             'torch_op', 'views']  # roundtrip and corpus need the repository's corpus
DIFFERENTIAL = ['__init__', 'cases', 'compare', 'metal', 'metal_cpu', 'metal_text', 'simulate', 'verify']  # what `qpel verify` runs
FLAT = '|'.join(MODULES)
# every top-level module of the repository: one that a packaged module imports but MODULES lacks is left behind
TOP = '|'.join(sorted(path.stem for path in PORTABLE.glob('*.py') if path.stem != 'qpel'))
IMPORTS = [
    (re.compile(rf'^(\s*)from ({FLAT}) import ', re.M), r'\1from qpel.\2 import '),
    (re.compile(rf'^(\s*)import ({FLAT})(\s*#.*)?$', re.M), r'\1import qpel.\2 as \2\3'),
    (re.compile(r'^(\s*)from frontends(\.\w+)? import ', re.M), r'\1from qpel.frontends\2 import '),
    (re.compile(r'^(\s*)from differential(\.\w+)? import ', re.M), r'\1from qpel.differential\2 import '),
    (re.compile(r'^(\s*)from qpel import ', re.M), r'\1from qpel.cli import '),
]
LEFT_BEHIND = re.compile(rf'^\s*(from|import) ({TOP}|frontends|qpel_test_support|differential|fuzz)\b', re.M)
LEFT_BEHIND_RELATIVE = re.compile(r'^\s*from \.(matrix|roundtrip|gpgpusim|hardware|bench\w*) import', re.M)

PYPROJECT = f'''[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "qpel"
version = "{VERSION}"
description = "A portable kernel language and translator between CUDA and Apple Metal"
readme = "README.md"
license = "Apache-2.0"
license-files = ["LICENSE", "NOTICE", "TRADEMARKS.md"]
authors = [{{name = "Alex Shatalov"}}]
requires-python = ">=3.10"
dependencies = []
keywords = ["cuda", "metal", "gpu", "kernels", "compiler", "translation", "apple-silicon", "triton", "cub"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: GPU :: NVIDIA CUDA",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "Operating System :: MacOS",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Software Development :: Compilers",
]

[project.urls]
Homepage = "https://github.com/shatalov-alex/qpel"
Source = "https://github.com/shatalov-alex/qpel"
Issues = "https://github.com/shatalov-alex/qpel/issues"
Changelog = "https://github.com/shatalov-alex/qpel/blob/main/CHANGELOG.md"

[project.optional-dependencies]
mps = ["torch>=2.6", "numpy>=2.0"]
verify = ["numpy>=2.0"]  # qpel verify; running Metal sides also needs the mps extra

[project.scripts]
qpel = "qpel.cli:main"

[tool.setuptools]
packages = ["qpel", "qpel.backends", "qpel.frontends", "qpel.differential"]
include-package-data = true

[tool.setuptools.package-data]
"qpel.backends" = ["*.h"]
"qpel.frontends" = ["include/**/*"]
"qpel.differential" = ["include/**/*"]
'''

README = '''<p align="center"><img src="https://raw.githubusercontent.com/shatalov-alex/qpel/main/assets/qpel-icon-512.png" width="128" alt="qpel"></p>

# qpel

qpel is a small portable kernel language between CUDA and Apple Metal: CUDA or Metal
kernels import to qpel, and qpel builds both CUDA and Metal again, with documented numerics.

```sh
pip install qpel                 # the compiler: standard library only
pip install "qpel[mps]"          # PyTorch and NumPy, to run the generated Metal hosts on Apple GPUs
qpel build program.qpel --out build/
qpel translate kernels.cu --out translated/      # needs clang on the PATH
qpel torch-op translated/ --launches extension_api.cu
pip install "qpel[verify]"       # NumPy, for checking ports
qpel verify original.cu port.metal               # a port against its original, on the same cases
qpel mcp                                         # verify and translate as MCP tools for AI agents
```

Importing CUDA needs clang (Xcode's or LLVM's); importing Metal needs Xcode's Metal toolchain.
Source, documentation and changelog: https://github.com/shatalov-alex/qpel.
This is a development release. The name qpel is a trademark (TRADEMARKS.md); the code is
Apache-2.0.
'''


TOP_LEVEL_RELATIVE = re.compile(r'^from \.(\w*) import (\w+)', re.M)  # at module level: needed to import at all


def rewrite(text, name):
    for pattern, replacement in IMPORTS:
        text = pattern.sub(replacement, text)
    left = LEFT_BEHIND.findall(text) + LEFT_BEHIND_RELATIVE.findall(text)
    packaged = DIFFERENTIAL if name.startswith('differential/') else FRONTENDS
    left += [module or member for module, member in TOP_LEVEL_RELATIVE.findall(text)
             if (module or member) not in packaged]
    if left:
        raise SystemExit(f'{name}: imports outside the package remain: {left}')
    return text


def stage(folder):
    package = folder/'qpel'
    (package/'backends').mkdir(parents=True)
    (package/'frontends').mkdir()
    (package/'__init__.py').write_text(f'"""qpel: a portable kernel language between CUDA and Apple Metal."""\n'
                                       f'__version__ = {VERSION!r}\n')
    (package/'__main__.py').write_text('from qpel.cli import main\n\nmain()\n')
    (package/'cli.py').write_text(rewrite((PORTABLE/'qpel.py').read_text(), 'qpel.py'))
    for name in MODULES:
        (package/f'{name}.py').write_text(rewrite((PORTABLE/f'{name}.py').read_text(), name))
    for header in sorted((PORTABLE/'backends').glob('*.h')):
        shutil.copy(header, package/'backends')
    (package/'backends/__init__.py').write_text('')
    for name in FRONTENDS:
        (package/'frontends'/f'{name}.py').write_text(rewrite((PORTABLE/'frontends'/f'{name}.py').read_text(), name))
    shutil.copytree(PORTABLE/'frontends/include', package/'frontends/include')
    (package/'differential').mkdir()
    for name in DIFFERENTIAL:
        (package/'differential'/f'{name}.py').write_text(
            rewrite((PORTABLE/'differential'/f'{name}.py').read_text(), 'differential/' + name))
    shutil.copytree(PORTABLE/'differential/include', package/'differential/include',
                    ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('LICENSE', 'NOTICE', 'TRADEMARKS.md'):
        shutil.copy(ROOT/name, folder)
    (folder/'README.md').write_text(README)
    (folder/'pyproject.toml').write_text(PYPROJECT)


def smoke_test(wheel, work):
    """Install the wheel into a new virtual environment and use it from outside the repository."""
    venv = work/'venv'
    subprocess.run(['uv', 'venv', '-q', str(venv)], check=True)
    python = venv/'bin/python'
    subprocess.run(['uv', 'pip', 'install', '-q', '--python', str(python), str(wheel)], check=True)
    program = PORTABLE/'tests/fixtures/qpel_ir/trellis2_z_order_encode/program.qpel.json'
    out = work/'build'
    subprocess.run([str(venv/'bin/qpel'), 'build', str(program), '--out', str(out)], check=True, cwd=work,
                   stdout=subprocess.DEVNULL)
    generated = out/'package/generated'
    for name in ('trellis2_z_order_encode.cu', 'trellis2_z_order_encode.metal', 'metal_host.py', 'cuda_host.py'):
        if not (generated/name).is_file():
            raise SystemExit(f'smoke test: the installed qpel did not write {name}')
    check = ('import qpel, qpel.cli, qpel.frontends.c_import, qpel.frontends.torch_op, qpel.qpel_text, sys; '
             'assert "frontends" not in sys.modules and "qpel_ir" not in sys.modules; '
             'assert qpel.cli.__file__.startswith(sys.prefix), qpel.cli.__file__; print(qpel.__version__)')
    version = subprocess.run([str(python), '-I', '-c', check], check=True, cwd=work, capture_output=True, text=True)
    if shutil.which('clang'):  # qpel verify from the installed package: CUDA against itself, CPU simulator
        subprocess.run(['uv', 'pip', 'install', '-q', '--python', str(python), 'numpy'], check=True)
        source = str(PORTABLE/'cuda_examples/scale_offset.cu')
        result = subprocess.run([str(venv/'bin/qpel'), 'verify', source, source], cwd=work, capture_output=True, text=True)
        if result.returncode or 'cases agree' not in result.stdout:
            raise SystemExit('smoke test: qpel verify failed:\n' + result.stdout[-1500:] + result.stderr[-1500:])
        if shutil.which('xcrun'):  # a Metal port on the CPU simulator, without PyTorch
            port = str(PORTABLE/'metal_examples/scale_offset.metal')
            result = subprocess.run([str(venv/'bin/qpel'), 'verify', source, port, '--metal', 'cpu'], cwd=work,
                                    capture_output=True, text=True)
            if result.returncode or 'CPU simulator (Metal)' not in result.stdout:
                raise SystemExit('smoke test: qpel verify --metal cpu failed:\n' + result.stdout[-1500:] + result.stderr[-1500:])
    if shutil.which('clang'):  # import a CUDA source through the installed package (clang stub headers)
        result = subprocess.run([str(venv/'bin/qpel'), 'translate', str(PORTABLE/'cuda_examples/scale_offset.cu'),
                                 '--out', str(work/'translated')], cwd=work, capture_output=True, text=True)
        if result.returncode:
            raise SystemExit('smoke test: qpel translate failed:\n' + result.stderr[-2000:])
    return version.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', type=Path, default=ROOT/'dist', help='folder for the wheel and sdist')
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='qpel-wheel-') as folder:
        folder = Path(folder)
        stage(folder/'src')
        args.out.mkdir(parents=True, exist_ok=True)
        subprocess.run(['uv', 'build', '-q', '--out-dir', str(args.out), str(folder/'src')], check=True)
        wheel = args.out/f'qpel-{VERSION}-py3-none-any.whl'
        version = smoke_test(wheel, folder)
    print(f'built {wheel.name} and qpel-{VERSION}.tar.gz in {args.out}; installed in a new environment: qpel {version}')


if __name__ == '__main__':
    sys.exit(main())
