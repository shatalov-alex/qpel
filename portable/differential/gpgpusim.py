"""NVIDIA execution rows: CUDA run on GPGPU-Sim, a simulated NVIDIA GPU, in Docker.

The CPU simulator (simulate.py) runs CUDA as C++. This module runs the same
CUDA as NVIDIA executes it: nvcc compiles a real CUDA host program (cudaMalloc,
cudaMemcpy, <<<>>> launches) driven from the same launch plan, and the program
runs against GPGPU-Sim's libcudart, which executes the embedded PTX on a
simulated GPU (SM75_RTX2060 by default; gpgpusim/Dockerfile builds the image).

For every original source of the behaviour matrix (differential/matrix.py):

    nvidia: cuda original            the unmodified CUDA original on GPGPU-Sim
    nvidia: <dialect> -> qpel -> cuda   qpel's CUDA translation on GPGPU-Sim

both compared with the same reference and rules as the matrix rows: the CUDA
original on the CPU simulator, or the Metal original on the Apple GPU. Originals
compile with -fmad=false, which is how the simulator (-ffp-contract=off) and the
Metal originals (fp contract off) run them; qpel's CUDA compiles with nvcc's
defaults, because qpel rounds every float operation explicitly (f32-strict-v1).

Everything compiles and runs in one container: all programs build in parallel,
then every case runs in parallel, each in its own folder.

    python3 -m differential.gpgpusim                  # all sources
    python3 -m differential.gpgpusim box_sum llama_arange_f32
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from . import matrix  # noqa: E402
from .simulate import Program  # noqa: E402

IMAGE = 'qpel/gpgpu-sim:dev-4195dd4-qpel10'  # change the tag whenever gpgpusim/ changes
DOCKERFILE = HERE/'gpgpusim'
# Programs GPGPU-Sim cannot run correctly yet, for the original and qpel's CUDA alike, while
# the CPU simulator and the Apple GPU agree (gpgpusim/README.md, Open). Real NVIDIA hardware
# (differential/hardware.py) runs them.
SIMULATOR_ISSUES = {'trellis2_raster_render': 'skipped: GPGPU-Sim finds no voxel for the original either '
                                              '(gpgpusim/README.md, Open); to check on real NVIDIA hardware (Kaggle T4)'}
CONFIG = 'SM75_RTX2060'
ARCH = 'compute_75'

# Runs inside the container with ROOT, CONFIG and ARCH set. Programs are directories
# p*/ holding main.cu and nvcc.args; cases are their c*/ subdirectories.
CONTAINER_SCRIPT = r'''
cd /opt/gpgpu-sim && source setup_environment release > /dev/null || exit 1
set -u
export PTX_SIM_MODE_FUNC=${PTX_SIM_MODE_FUNC:-1}
build() {
  cd "$1" && nvcc -std=c++17 -O2 -cudart shared -gencode arch=$ARCH,code=$ARCH -w $(cat nvcc.args) \
    main.cu -o program > build.log 2>&1 && touch built
}
run() {
  program=$(dirname "$1"); cd "$1" || return
  if [ ! -f "$program/built" ]; then echo 125 > status; return; fi
  ln -sf /opt/gpgpu-sim/configs/tested-cfgs/$CONFIG/* .
  "$program/program" "$1" > run.log 2>&1; echo $? > status
}
export -f build run
find "$ROOT" -name main.cu -printf '%h\n' | xargs -P "$(nproc)" -I{} bash -c 'build {}'
find "$ROOT" -mindepth 2 -type d -name 'c[0-9]*' | xargs -P "$(nproc)" -I{} bash -c 'run {}'
'''


class GpuProgram(Program):
    """A real CUDA host program for one source and launch plan, compiled and run later in the
    container (run_all). Same inputs, launch geometry and argument mapping as the simulator."""

    MAIN = 'main.cu'
    HEADERS = ('<cuda_runtime.h>', '<cuda_fp16.h>', '<cuda_bf16.h>', '<cstdio>')

    def __init__(self, source, plan, work, fmad=True, **options):
        Path(work).mkdir(parents=True, exist_ok=True)
        self.fmad = fmad
        super().__init__(source, plan, work, **options)

    def build(self):
        args = [] if self.fmad else ['-fmad=false']
        args += [arg for folder in self.include for arg in ('-I', str(folder))]
        # The importer's library stand-ins (PyTorch, ATen, c10, pybind11, cuBLAS), for extensions;
        # they hold no CUDA runtime headers, so NVIDIA's own stay in force.
        args += ['-I', str(HERE/'frontends/include/libraries')]
        # PyTorch's extension builder (torch.utils.cpp_extension) always passes it: extensions
        # call constexpr host functions such as std::numeric_limits<K>::max() in device code.
        args += ['--expt-relaxed-constexpr']
        (self.work/'nvcc.args').write_text(' '.join(args) + '\n')

    def upload(self, data):
        return [f'  void* d_{r["name"]} = nullptr; cudaMalloc(&d_{r["name"]}, b_{r["name"]}.size() + 1); '
                f'cudaMemcpy(d_{r["name"]}, b_{r["name"]}.data(), b_{r["name"]}.size(), cudaMemcpyHostToDevice);'
                for r in data]

    def download(self, data):
        return [f'  cudaMemcpy(b_{r["name"]}.data(), d_{r["name"]}, b_{r["name"]}.size(), cudaMemcpyDeviceToHost);'
                for r in data]

    def buffer(self, name):
        return f'Ptr{{d_{name}}}'

    def launch(self, kernel, callee, args, g, b):
        return [f'  {{ dim3 g({", ".join(g)}), b({", ".join(b)});',
                f'    {callee}<<<g, b>>>({", ".join(args)});',
                '    cudaError_t e = cudaGetLastError(); if (e == cudaSuccess) e = cudaDeviceSynchronize();',
                f'    if (e != cudaSuccess) {{ std::fprintf(stderr, "{kernel}: %s\\n", cudaGetErrorString(e)); return 3; }} }}']

    def outcome(self, folder):
        """(outputs, None) after the container ran this case, or (None, error)."""
        folder = Path(folder)
        status = (folder/'status').read_text().strip() if (folder/'status').exists() else 'not run'
        if status == '125':
            log = (self.work/'build.log').read_text(errors='replace') if (self.work/'build.log').exists() else ''
            return None, f'{self.source.name} does not compile with nvcc:\n{log[-3000:]}'
        if status != '0':
            log = (folder/'run.log').read_text(errors='replace') if (folder/'run.log').exists() else ''
            return None, f'GPGPU-Sim run failed (status {status}):\n{log[-2000:]}'
        return self.read_outputs(folder), None


def image_ready(build=True):
    """True when the GPGPU-Sim image exists; builds it from gpgpusim/Dockerfile if asked."""
    if not shutil.which('docker'):
        return False
    if subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True).returncode == 0:
        return True
    if not build:
        return False
    print(f'building {IMAGE} (GPGPU-Sim, a few minutes once)', flush=True)
    return subprocess.run(['docker', 'build', '-t', IMAGE, str(DOCKERFILE)]).returncode == 0


SELFTEST = DOCKERFILE/'selftest.cu'


def selftest(image=None):
    """None, or why the simulator image cannot be trusted: gpgpusim/selftest.cu checks the PTX
    behaviour qpel-fixes.patch corrects and sweeps integer/float conversions against the host."""
    with tempfile.TemporaryDirectory(prefix='qpel-gpgpusim-selftest-') as folder:
        folder = Path(folder).resolve()
        shutil.copy(SELFTEST, folder/'selftest.cu')
        script = ('cd /opt/gpgpu-sim && source setup_environment release > /dev/null && cd "$W" && '
                  f'nvcc -O2 -fmad=false -cudart shared -gencode arch={ARCH},code={ARCH} -w selftest.cu -o selftest && '
                  f'ln -sf /opt/gpgpu-sim/configs/tested-cfgs/{CONFIG}/* . && PTX_SIM_MODE_FUNC=1 ./selftest')
        result = subprocess.run(['docker', 'run', '--rm', '-v', f'{folder}:{folder}', '-e', f'W={folder}',
                                 '-e', 'CUDA_INSTALL_PATH=/usr/local/cuda', image or IMAGE, 'bash', '-c', script],
                                capture_output=True, text=True, timeout=900)
    lines = [line for line in result.stdout.splitlines() if line.startswith('SELFTEST')]
    if result.returncode == 0 and lines and lines[-1] == 'SELFTEST PASSED':
        return None
    return '\n'.join(lines) or (result.stdout + result.stderr)[-2000:]


def run_all(root, mounts, config=CONFIG, functional=True):
    """Compile every program under root and run every case, in one container."""
    volumes = [arg for m in sorted(set(mounts)) for arg in ('-v', f'{m}:{m}')]
    result = subprocess.run(['docker', 'run', '--rm', *volumes, '-e', f'ROOT={root}', '-e', f'CONFIG={config}',
                             '-e', f'ARCH={ARCH}', '-e', 'CUDA_INSTALL_PATH=/usr/local/cuda',
                             '-e', f'PTX_SIM_MODE_FUNC={1 if functional else 0}', IMAGE, 'bash', '-c', CONTAINER_SCRIPT],
                            capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise RuntimeError(f'GPGPU-Sim container failed:\n{result.stderr[-3000:]}')


def rows(names=None, gpu=None, config=CONFIG, functional=True, keep=False):
    """Yield (row, case label, error or None) for every original, like matrix.rows."""
    if not image_ready():
        yield 'nvidia', 'GPGPU-Sim', 'skipped: no Docker or GPGPU-Sim image'
        return
    problem = selftest()
    yield 'nvidia: simulator self-test', 'selftest.cu', problem
    if problem:
        return
    yield from prepared_rows(names, gpu, lambda root: run_all(root, [root, HERE], config, functional), keep,
                             'qpel-gpgpusim-', skip=SIMULATOR_ISSUES)


def prepared_rows(names, gpu, execute, keep=False, prefix='qpel-nvidia-', device='nvidia', skip=None):
    """Write a CUDA host program and its cases for every original (GpuProgram), call
    execute(root) to compile and run them all, then compare each case with its reference.
    Program folders are root/s*/p_*/ (main.cu, nvcc.args), cases their c*/ subfolders."""
    if gpu is None:
        try:
            from . import metal
            gpu = metal.available()
        except ImportError:
            gpu = False
    root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    pending = []  # (row, label, prepared, program, case, folder, expected)
    try:
        for index, (name, path, dialect, plan, include, grids, options) in enumerate(matrix.sources()):
            if names and name not in names:
                continue
            if name in (skip or {}):
                yield f'{device}: {dialect} original', f'{path.name} [{name}]', skip[name]
                continue
            if dialect == 'metal' and not matrix.metal_available():
                yield f'{device}: metal original', path.name, 'skipped: no Metal toolchain (Xcode) to import it'
                continue
            work = root/f's{index:03}'
            (work/'sim_original').mkdir(parents=True)
            try:
                p = matrix.Prepared(name, path, dialect, plan, include, options, work)
                targets = [(f'{device}: {dialect} -> qpel -> cuda', GpuProgram(p.cuda, plan, work/'p_qpel'))]
                reference = None
                if dialect == 'cuda':
                    reference = Program(path, plan, work/'sim_original', original=p.report, include=include,
                                        grids=grids)
                    targets.append((f'{device}: cuda original',
                                    GpuProgram(path, plan, work/'p_original', fmad=False, original=p.report,
                                               include=include, grids=grids)))
                for number, case in enumerate(p.cases()):
                    label = f'{path.name} {case.label} [{name}]'
                    if reference:
                        expected = reference.run(case)
                    elif gpu:
                        expected = p.run_metal_original(case)
                    else:
                        yield f'{device}: {dialect} original', label, 'skipped: no Apple GPU for the reference'
                        continue
                    for row, program in targets:
                        folder = program.work/f'c{number:03}'
                        folder.mkdir()
                        program.write_case(case, folder)
                        pending.append((row, label, p, program, case, folder, expected))
            except (ValueError, RuntimeError) as error:
                yield f'{device}: {dialect} -> qpel', path.name, str(error)
        if pending:
            execute(root)
        for row, label, p, program, case, folder, expected in pending:
            actual, error = program.outcome(folder)
            yield row, label, error or p.compare(expected, actual, case)
    finally:
        if keep:
            print(f'kept {root}')
        else:
            shutil.rmtree(root, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('programs', nargs='*', help='fixture names (default: all)')
    parser.add_argument('--config', default=CONFIG, help=f'GPGPU-Sim tested config (default {CONFIG})')
    parser.add_argument('--timing', action='store_true',
                        help='cycle-level performance simulation instead of functional simulation (much slower)')
    parser.add_argument('--keep', action='store_true', help='keep the work folder (programs, logs, outputs)')
    args = parser.parse_args(argv)
    summary, failures, skipped = {}, 0, 0
    for row, label, error in rows(args.programs or None, config=args.config, functional=not args.timing,
                                  keep=args.keep):
        if error and error.startswith('skipped'):
            skipped += 1
            print(f'SKIP {row:34} {label}: {error}')
            continue
        key = (row, label.split(' ')[0])
        summary.setdefault(key, [0, 0])
        summary[key][0] += 1
        if error:
            failures += 1
            summary[key][1] += 1
            print(f'FAIL {row:34} {label}\n     {error}')
    for (row, source), (count, failed) in summary.items():
        print(f'{"PASS" if not failed else "FAIL"} {row:34} {source}: {count - failed}/{count} cases')
    print(('all NVIDIA rows passed' if not failures else f'{failures} cases failed')
          + (f' ({skipped} skipped)' if skipped else '') + f' on GPGPU-Sim {args.config}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
