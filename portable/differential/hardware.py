"""NVIDIA hardware rows: the GPGPU-Sim rows' programs on a real NVIDIA GPU.

The GPU machine needs only an NVIDIA driver. Everything else happens here:

1. prepare: the same CUDA host programs, cases and references as differential/gpgpusim.py
   (CUDA originals on the CPU simulator, Metal originals on the Apple GPU);
2. build: nvcc compiles every program for x86-64 Linux in an amd64 nvidia/cuda container
   (SASS for sm_75, sm_80, sm_89 plus compute_75 PTX), with gpgpusim/selftest.cu;
3. bundle: programs, case inputs, libcudart and run.sh (plus the compiler sources, for the
   generated CuPy host's fixture tests: gpu_tests/run_gpu_tests.py --device cuda);
4. run: on the GPU machine, `bash run.sh` runs every case and writes results.tar.gz;
5. check: here, the results are compared with the references under the matrix's rules.

    python3 -m differential.hardware --kaggle             # steps 1-5 on a Kaggle T4
    python3 -m differential.hardware --bundle-only OUT    # steps 1-3; carry OUT/ elsewhere

--kaggle needs the Kaggle CLI, signed in (~/.kaggle). It uploads the bundle as a private
dataset, runs a private script kernel on one T4 with a time limit, and downloads results.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from . import gpgpusim  # noqa: E402

IMAGE = 'nvidia/cuda:12.6.3-devel-ubuntu22.04'  # x86-64 Linux, glibc 2.35: runs on Kaggle and most hosts
GENCODE = '-gencode arch=compute_75,code=[sm_75,sm_80,sm_89,compute_75]'

BUILD_SCRIPT = r'''
set -u
build() {
  cd "$1" && nvcc -std=c++17 -O2 -cudart shared GENCODE -w $(cat nvcc.args) main.cu -o program \
    > build.log 2>&1 && touch built
}
export -f build
find "$ROOT" -name main.cu -printf '%h\n' | xargs -P "$(nproc)" -I{} bash -c 'build {}'
cd "$ROOT" && nvcc -O2 -fmad=false -cudart shared GENCODE -w "$SELFTEST" -o selftest > selftest.build.log 2>&1
mkdir -p "$ROOT/lib" && cp -L /usr/local/cuda/lib64/libcudart.so.12 "$ROOT/lib/"
'''.replace('GENCODE', GENCODE)

# Runs on the GPU machine from the unpacked bundle. Only the NVIDIA driver is needed for the
# behaviour rows; the CuPy host step uses the machine's PyTorch and CuPy when present.
RUN_SCRIPT = r'''#!/bin/bash
set -u
cd "$(dirname "$0")" && B=$PWD
export LD_LIBRARY_PATH=$B/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
nvidia-smi --query-gpu=name,driver_version,compute_cap,memory.total --format=csv > gpu.txt 2>&1
touch .start
cp bundle_id.txt ran_bundle_id.txt 2>/dev/null
start=$(date +%s)
./selftest > selftest.log 2>&1; echo $? > selftest.status
run() {
  program=$(dirname "$1"); cd "$1" || return
  if [ ! -x "$program/program" ]; then echo 125 > status; return; fi
  "$program/program" "$1" > run.log 2>&1; echo $? > status
}
export -f run
find "$B" -mindepth 3 -maxdepth 3 -type d -name 'c[0-9]*' | xargs -P 4 -I{} bash -c 'run {}'
echo "rows: $(( $(date +%s) - start ))s" > timing.txt
if [ -f bench.json ] && [ -d portable ]; then
  start=$(date +%s)
  (cd portable && timeout 1200 python3 -m differential.bench_cuda run "$B") > bench.log 2>&1
  echo $? > bench.status
  echo "benchmarks: $(( $(date +%s) - start ))s" >> timing.txt
fi
if [ -d portable ] && [ "${QPEL_SKIP_CUPY:-0}" != 1 ]; then
  start=$(date +%s)
  (cd portable && timeout 900 python3 gpu_tests/run_gpu_tests.py --device cuda) > cupy_host.log 2>&1
  echo $? > cupy_host.status
  echo "cupy host: $(( $(date +%s) - start ))s" >> timing.txt
fi
find . -newer .start -type f ! -name results.tar.gz -print0 | tar czf results.tar.gz --null -T -
'''

# The compiler sources the CuPy host step needs (no corpus, no generated packages).
PORTABLE_SKIP = {'corpus', 'generated', '__pycache__', '.pytest_cache'}


def build(root):
    """Compile every program under root (and the self-test) for x86-64 Linux, in Docker."""
    result = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/amd64',
                             '-v', f'{root}:{root}', '-v', f'{HERE}:{HERE}', '-e', f'ROOT={root}',
                             '-e', f'SELFTEST={gpgpusim.SELFTEST}', IMAGE, 'timeout', '3000', 'bash', '-c',
                             BUILD_SCRIPT], capture_output=True, text=True, timeout=3300)
    if result.returncode or not (root/'selftest').exists():
        log = (root/'selftest.build.log').read_text(errors='replace') if (root/'selftest.build.log').exists() else ''
        raise RuntimeError(f'x86-64 build failed:\n{result.stderr[-3000:]}\n{log[-2000:]}')


def bundle(root, out, cupy=True):
    """cupy: also the compiler sources, which the CuPy host tests and the benchmarks need."""
    # out/bundle.tar.gz: what the GPU machine needs, and nothing else.
    out.mkdir(parents=True, exist_ok=True)
    (root/'run.sh').write_text(RUN_SCRIPT)
    (root/'bundle_id.txt').write_text(uuid.uuid4().hex + '\n')
    path = out/'bundle.tar.gz'

    def keep(info):
        parts = Path(info.name).parts
        if parts[0] == 'portable':
            if parts[1:] in (('corpus',), ('corpus', 'corpus.json')) or \
                    len(parts) in (3, 4) and parts[1] == 'corpus' and (len(parts) == 3 and (Path(HERE)/'corpus'/parts[2]/'kernels.json').exists()
                                                                     or parts[3:] == ('kernels.json',)):
                return info  # the corpus indexes only (corpus.json, */kernels.json): the fixture tests skip corpus programs by them
            return None if PORTABLE_SKIP & set(parts) else info
        if len(parts) >= 2 and parts[1] == 'sim_original':
            return None
        if parts[-1] in ('main.cu', 'nvcc.args', 'build.log', 'built', 'selftest.build.log'):
            return None
        return info

    with tarfile.open(path, 'w:gz', compresslevel=9) as tar:
        for item in sorted(root.iterdir()):
            tar.add(item, arcname=item.name, filter=keep)
        if cupy:
            tar.add(HERE, arcname='portable', filter=keep)
    return path


def unpack_results(root, results):
    with tarfile.open(results) as tar:
        tar.extractall(root, filter='data')


# --- Kaggle ---

KERNEL = r'''# qpel NVIDIA hardware rows: unpack the bundle, run it, keep the results.
import glob, os, shutil, subprocess, sys, tarfile, time
began = time.time()
def mark(what):
    print(f'{time.time() - began:7.1f}s {what}', flush=True)
mark('start')
# The bundle is uploaded as one file Kaggle does not unpack (bundle.qpelbundle, a .tar.gz): reading it
# once and unpacking locally takes seconds, where copying Kaggle's unpacked tree took 10 to 30 minutes.
work = '/tmp/qpel'
archive = (glob.glob('/kaggle/input/**/bundle.qpelbundle', recursive=True) or
           glob.glob('/kaggle/input/**/bundle.tar.gz', recursive=True))
if archive:
    os.makedirs(work)
    tarfile.open(archive[0], 'r:gz').extractall(work)
else:
    shutil.copytree(os.path.dirname(glob.glob('/kaggle/input/**/run.sh', recursive=True)[0]), work)
mark('bundle unpacked')
expected = 'BUNDLE_ID'
found_id = open(work + '/bundle_id.txt').read().strip() if os.path.exists(work + '/bundle_id.txt') else 'none'
if found_id != expected:  # Kaggle attached an older version of the dataset: stop before using the GPU
    open('/kaggle/working/stale_bundle.txt', 'w').write(f'{found_id} (expected {expected})\n')
    mark(f'stale bundle {found_id}, expected {expected}')
    sys.exit(0)
for p in glob.glob(work + '/**/program', recursive=True) + [work + '/selftest']:
    os.chmod(p, 0o755)
env = dict(os.environ)
try:
    import cupy  # noqa: F401
except ImportError:
    if subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'cupy-cuda12x']).returncode:
        env['QPEL_SKIP_CUPY'] = '1'
    mark('cupy installed')
subprocess.run(['bash', work + '/run.sh'], env=env)
mark('run.sh done')
for name in ('results.tar.gz', 'gpu.txt', 'timing.txt', 'selftest.log', 'cupy_host.log', 'bench.log',
             'bench_cuda.json'):
    if os.path.exists(f'{work}/{name}'):
        shutil.copy(f'{work}/{name}', '/kaggle/working/')
'''


class Kaggle:
    def __init__(self, cli, slug='qpel-nvidia', timeout=1500, queue_limit=4 * 3600):
        # timeout counts from when the kernel runs: Kaggle can keep it queued for hours waiting for a T4
        self.cli, self.slug, self.timeout, self.queue_limit = cli, slug, timeout, queue_limit
        self.user = self.kaggle('config', 'view', capture=True)
        self.user = next((line.split(':', 1)[1].strip() for line in self.user.splitlines()
                          if line.strip().startswith('- username')), None)
        if not self.user:
            raise RuntimeError('Kaggle CLI is not signed in (kaggle config view shows no username)')

    def kaggle(self, *args, capture=False):
        result = subprocess.run([self.cli, *args], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'kaggle {" ".join(args)}:\n{result.stdout[-2000:]}{result.stderr[-2000:]}')
        return result.stdout

    def upload(self, path):
        """The bundle as a private dataset (a new version when it exists); waits until ready."""
        folder = Path(tempfile.mkdtemp(prefix='qpel-kaggle-dataset-'))
        shutil.copy(path, folder/'bundle.qpelbundle')  # (a name Kaggle leaves packed; see KERNEL)
        ident = f'{self.user}/{self.slug}-bundle'
        (folder/'dataset-metadata.json').write_text(json.dumps(
            {'title': 'qpel nvidia bundle', 'id': ident, 'licenses': [{'name': 'other'}]}))
        exists = subprocess.run([self.cli, 'datasets', 'status', ident], capture_output=True, text=True)
        if exists.returncode == 0 and 'ready' in exists.stdout.lower():
            self.kaggle('datasets', 'version', '-p', str(folder), '-m', 'bundle', '-q')
            # status answers 'ready' for the previous version until the new one is processed: wait
            # for it to leave 'ready' (or a few minutes), then for 'ready' again. The kernel also
            # checks the bundle id, so a version attached too early stops it at once.
            for _ in range(30):
                time.sleep(10)
                status = subprocess.run([self.cli, 'datasets', 'status', ident], capture_output=True, text=True)
                if status.returncode or 'ready' not in status.stdout.lower():
                    break
        else:
            self.kaggle('datasets', 'create', '-p', str(folder), '-q')
        for _ in range(120):  # a new dataset answers 403 for a while; keep asking
            time.sleep(10)
            status = subprocess.run([self.cli, 'datasets', 'status', ident], capture_output=True, text=True)
            if status.returncode == 0 and 'ready' in status.stdout.lower():
                return ident
        raise RuntimeError(f'dataset {ident} did not become ready')

    def run(self, dataset, out, bundle_id):
        """Push and run the kernel on one T4, wait, download its output into out/."""
        folder = Path(tempfile.mkdtemp(prefix='qpel-kaggle-kernel-'))
        ident = f'{self.user}/{self.slug}'
        (folder/'run.py').write_text(KERNEL.replace('BUNDLE_ID', bundle_id))
        (folder/'kernel-metadata.json').write_text(json.dumps({
            'id': ident, 'title': self.slug, 'code_file': 'run.py', 'language': 'python', 'kernel_type': 'script',
            'is_private': True, 'enable_gpu': True, 'enable_internet': True, 'dataset_sources': [dataset],
            'competition_sources': [], 'kernel_sources': []}))
        self.kaggle('kernels', 'push', '-p', str(folder), '--accelerator', 'NvidiaTeslaT4', '-t', str(self.timeout))
        pushed, started = time.time(), None
        while True:
            time.sleep(20)
            try:
                status = self.kaggle('kernels', 'status', ident, capture=True).lower()
            except RuntimeError:
                continue  # transient API errors while the kernel starts
            if any(word in status for word in ('complete', 'error', 'cancel')):
                break
            now = time.time()
            if 'queued' in status:
                if now - pushed > self.queue_limit:
                    raise RuntimeError(f'kernel {ident} still queued after {self.queue_limit}s (no T4 free); '
                                       'push it again or wait with `kaggle kernels status`')
                continue
            if started is None:
                started = now
                print(f'kernel {ident} running after {now - pushed:.0f}s in the queue', file=sys.stderr)
            if now - started > self.timeout + 600:
                raise RuntimeError(f'kernel {ident} still running after {self.timeout + 600}s: {status}')
        out.mkdir(parents=True, exist_ok=True)
        self.kaggle('kernels', 'output', ident, '-p', str(out), '-o', '-q')
        if (out/'stale_bundle.txt').exists():
            raise RuntimeError(f'Kaggle attached an older bundle: {(out/"stale_bundle.txt").read_text().strip()}; '
                               'run again once the dataset version is ready')
        if 'complete' not in status:
            log = next(iter(out.glob('*.log')), None)
            raise RuntimeError(f'kernel {ident}: {status.strip()}\n' + (log.read_text()[-3000:] if log else ''))
        return out/'results.tar.gz'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('programs', nargs='*', help='fixture names (default: all)')
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument('--kaggle', action='store_true', help='run on a Kaggle T4 through the Kaggle CLI')
    where.add_argument('--bundle-only', type=Path, metavar='OUT', help='write OUT/bundle.tar.gz and stop')
    where.add_argument('--results', type=Path, metavar='TAR',
                       help="check the results.tar.gz an earlier run downloaded (its rows are prepared again)")
    parser.add_argument('--cupy-only', action='store_true',
                        help='only the self-test and the CuPy host fixture tests (no behaviour rows)')
    parser.add_argument('--kaggle-cli', default=shutil.which('kaggle') or 'kaggle')
    parser.add_argument('--timeout', type=int, default=1500, help='Kaggle run time limit in seconds, from when it starts')
    parser.add_argument('--queue-limit', type=int, default=4 * 3600,
                        help='how long to wait for Kaggle to start the run (seconds)')
    parser.add_argument('--no-cupy', action='store_true', help='leave out the CuPy host fixture tests')
    parser.add_argument('--bench-size', type=int, nargs=3, default=(2048, 1024, 4), metavar=('W', 'H', 'N'),
                        help='benchmark size of every CUDA original and its translation (differential/bench_cuda.py)')
    parser.add_argument('--no-bench', action='store_true', help='leave out the CUDA benchmarks')
    parser.add_argument('--no-extra', action='store_true',
                        help='leave out the library and gsplat rows (differential/extra_rows.py)')
    parser.add_argument('--out', type=Path, help='keep the bundle and the downloaded results here')
    args = parser.parse_args(argv)
    out = (args.bundle_only or args.out or Path(tempfile.mkdtemp(prefix='qpel-hardware-'))).resolve()
    remote = Kaggle(args.kaggle_cli, timeout=args.timeout, queue_limit=args.queue_limit) if args.kaggle else None
    details = {}

    def execute(root):
        if args.results:  # results.tar.gz of an earlier run: the same rows prepared again (fixed seeds), not built
            unpack_results(root, args.results.resolve())
            ran = (root/'ran_bundle_id.txt').read_text().strip() if (root/'ran_bundle_id.txt').exists() else 'none'
            print(f'checking {args.results} (bundle {ran}) against the rows prepared again', flush=True)
            for name in ('gpu.txt', 'timing.txt', 'selftest.log', 'selftest.status', 'cupy_host.log', 'cupy_host.status',
                         'bench.log', 'bench.status', 'bench_cuda.json'):
                for folder in (root, args.results.resolve().parent):
                    if (folder/name).exists():
                        details[name] = (folder/name).read_text(errors='replace').strip()
                        break
            return
        started = time.time()
        build(root)
        if args.no_cupy and (root/'bench.json').exists():
            (root/'bench.json').unlink()  # the benchmarks run from the compiler sources the CuPy step ships
        path = bundle(root, out, cupy=not args.no_cupy)
        print(f'built and bundled in {time.time() - started:.0f}s: {path} ({path.stat().st_size >> 10} KiB)',
              flush=True)
        if args.bundle_only:
            raise SystemExit(0)
        dataset = remote.upload(path)
        print(f'uploaded {dataset}; running on Kaggle', flush=True)
        results = remote.run(dataset, out/'kaggle', (root/'bundle_id.txt').read_text().strip())
        unpack_results(root, results)
        ran = (root/'ran_bundle_id.txt').read_text().strip() if (root/'ran_bundle_id.txt').exists() else 'none'
        if ran != (root/'bundle_id.txt').read_text().strip():
            raise RuntimeError(f'the results are from bundle {ran}, not this one')
        for name in ('gpu.txt', 'timing.txt', 'selftest.log', 'selftest.status', 'cupy_host.log', 'cupy_host.status',
                     'bench.log', 'bench.status', 'bench_cuda.json'):
            for folder in (root, out/'kaggle'):
                if (folder/name).exists():
                    details[name] = (folder/name).read_text(errors='replace').strip()
                    break

    failures, summary = 0, {}
    if args.cupy_only:
        root = Path(tempfile.mkdtemp(prefix='qpel-hardware-')).resolve()
        try:
            execute(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        found = []
    else:
        found = gpgpusim.prepared_rows(args.programs or None, None, execute, keep=False,
                                       prefix='qpel-hardware-', device='nvidia hw',
                                       bench_size=None if args.no_bench else tuple(args.bench_size),
                                       extra=not args.no_extra)
    for row, label, error in found:
        if error and error.startswith('skipped'):
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
    print('GPU:', details.get('gpu.txt', 'unknown').replace('\n', ' | '))
    selftest = details.get('selftest.log', '').splitlines()
    print('self-test:', selftest[-1] if selftest else 'no log', f'(exit {details.get("selftest.status", "?")})')
    cupy = details.get('cupy_host.log', '').splitlines()
    print('CuPy host:', cupy[-1] if cupy else 'not run', f'(exit {details.get("cupy_host.status", "?")})')
    print('remote time:', details.get('timing.txt', '?').replace('\n', ', '))
    if 'bench_cuda.json' in details:
        from . import bench_cuda
        (out/'bench_cuda.json').write_text(details['bench_cuda.json'] + '\n')
        print(f'\n=== CUDA benchmarks ({out/"bench_cuda.json"})')
        bench_cuda.report(json.loads(details['bench_cuda.json']))
    elif not args.no_bench and not args.cupy_only:
        print('CUDA benchmarks: no results', details.get('bench.log', '')[-1500:])
    print(('all NVIDIA hardware rows passed' if not failures else f'{failures} cases failed'))
    selftest_ok = details.get('selftest.status') == '0'
    cupy_ok = args.no_cupy or details.get('cupy_host.status') == '0'
    return 1 if failures or not selftest_ok or not cupy_ok else 0


if __name__ == '__main__':
    raise SystemExit(main())
