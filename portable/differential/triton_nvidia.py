"""Triton's own NVIDIA code on GPGPU-Sim, against Triton's interpreter and qpel's translation.

Triton compiles each corpus kernel to PTX for sm_75 (corpus/triton/build/<kernel>.ptx,
frontends/triton/compile.py). GPGPU-Sim runs PTX but implements none of the driver API Triton's
launcher uses (cuModuleLoadData, cuLaunchKernel). So a stub CUDA program declares a kernel of
the same name and parameters (Triton appends two scratch pointers, passed null here), launches it
with the runtime API, and GPGPU-Sim is told to run Triton's PTX in its place
(PTX_SIM_USE_PTX_FILE, PTX_SIM_KERNELFILE). The block is num_warps * 32 threads and the dynamic
shared memory Triton's metadata gives. Triton 3.8 writes `.version 8.8`; the container's ptxas
(CUDA 12.6, which GPGPU-Sim calls for register counts) reads up to 8.5, and sm_75 code uses
nothing newer, so the version line is rewritten to 8.5.

Rows (same cases as differential/triton.py):

    nvidia: triton ptx            Triton's PTX on GPGPU-Sim against Triton's interpreter
    nvidia: triton ptx = qpel     Triton's PTX against qpel's CUDA on the CPU simulator

Triton's NVIDIA code is not IEEE-exact where Triton chooses speed: fp32 `/` is `div.full.f32`
(about 2 ulp) and `exp` is `ex2.approx` of x * log2(e). These rows therefore compare within
TRITON_PTX_ULPS ulp (plus the reduction tolerance of differential/triton.py), and report how far
apart the results are.

    python3 -m differential.triton_nvidia            # every kernel
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from . import triton  # noqa: E402
from .gpgpusim import CONFIG, IMAGE, image_ready  # noqa: E402
from qpel_types import MATH_ULPS  # noqa: E402

TRITON_PTX_ULPS = 4  # div.full.f32 and ex2.approx: a few ulp each (see the module docstring)
CTYPES = {'fp32': 'float', 'i32': 'int', 'i64': 'long long', 'fp16': '__half', 'u32': 'unsigned int'}
POINTERS = {'*fp32': 'float', '*i32': 'int', '*fp16': '__half', '*i64': 'long long', '*u32': 'unsigned int'}

SCRIPT = r'''
cd /opt/gpgpu-sim && source setup_environment release > /dev/null || exit 1
export PTX_SIM_MODE_FUNC=1
for k in "$ROOT"/*/; do
  (cd "$k" && nvcc -O2 -cudart shared -gencode arch=compute_75,code=compute_75 -w main.cu -o program > build.log 2>&1 && touch built)
done
run() {
  kernel=$(dirname "$1"); cd "$1" || return
  if [ ! -f "$kernel/built" ]; then echo 125 > status; return; fi
  ln -sf /opt/gpgpu-sim/configs/tested-cfgs/$CONFIG/* .
  PTX_SIM_USE_PTX_FILE=1 PTX_SIM_KERNELFILE="$kernel/kernel.ptx" "$kernel/program" "$1" > run.log 2>&1; echo $? > status
}
export -f run
find "$ROOT" -mindepth 2 -type d -name 'c[0-9]*' | xargs -P "$(nproc)" -I{} bash -c 'run {}'
'''


def stub(name, meta):
    """main.cu: the kernel's stub and a host program that reads, launches and writes one case."""
    params, args, setup, finish = [], [], [], []
    for i, (pname, kind) in enumerate(meta['signature'].items()):
        if kind.startswith('*'):
            ctype = POINTERS[kind]
            params.append(f'{ctype} *p{i}')
            setup.append(f'  {ctype} *d{i} = ({ctype} *) load(dir, "{pname}", &bytes[{i}]);')
            args.append(f'd{i}')
            finish.append(f'  save(dir, "{pname}", d{i}, bytes[{i}]);')
        else:
            ctype = CTYPES[kind]
            params.append(f'{ctype} p{i}')
            setup.append(f'  {ctype} v{i}; read_value(dir, "{pname}", &v{i}, sizeof v{i});')
            args.append(f'v{i}')
    params += ['void *scratch0', 'void *scratch1']
    args += ['nullptr', 'nullptr']
    count = len(meta['signature'])
    return f'''#include <cuda_runtime.h>
#include <cuda_fp16.h>
#include <cstdio>
#include <cstdlib>
#include <string>
// A stub: GPGPU-Sim runs Triton's PTX for this kernel (PTX_SIM_KERNELFILE).
extern "C" __global__ void {name}({", ".join(params)}) {{}}

static void *load(const char *dir, const char *name, size_t *bytes) {{
  FILE *f = fopen((std::string(dir) + "/" + name + ".bin").c_str(), "rb");
  fseek(f, 0, SEEK_END); *bytes = ftell(f); fseek(f, 0, SEEK_SET);
  void *host = malloc(*bytes); fread(host, 1, *bytes, f); fclose(f);
  void *device; cudaMalloc(&device, *bytes ? *bytes : 4);
  cudaMemcpy(device, host, *bytes, cudaMemcpyHostToDevice); free(host);
  return device;
}}
static void save(const char *dir, const char *name, void *device, size_t bytes) {{
  void *host = malloc(bytes ? bytes : 4); cudaMemcpy(host, device, bytes, cudaMemcpyDeviceToHost);
  FILE *f = fopen((std::string(dir) + "/out_" + name + ".bin").c_str(), "wb"); fwrite(host, 1, bytes, f); fclose(f); free(host);
}}
static void read_value(const char *dir, const char *name, void *value, size_t size) {{
  FILE *f = fopen((std::string(dir) + "/" + name + ".bin").c_str(), "rb"); fread(value, 1, size, f); fclose(f);
}}
int main(int argc, char **argv) {{
  const char *dir = argv[1];
  size_t bytes[{count}] = {{0}};
  unsigned grid[3]; read_value(dir, "grid", grid, sizeof grid);
{chr(10).join(setup)}
  {name}<<<dim3(grid[0], grid[1], grid[2]), {meta["num_warps"] * 32}, {meta["shared"]}>>>({", ".join(args)});
  cudaError_t error = cudaDeviceSynchronize();
  if (error != cudaSuccess) {{ fprintf(stderr, "launch: %s\\n", cudaGetErrorString(error)); return 1; }}
{chr(10).join(finish)}
  return 0;
}}
'''


def run(jobs):
    """{(kernel, case index): {buffer: array}} from Triton's PTX on GPGPU-Sim, for [(kernel, cases)]."""
    with tempfile.TemporaryDirectory(prefix='qpel-triton-nvidia-') as work:
        work = Path(work)
        layout = {}
        for name, cases in jobs:
            meta = json.loads((triton.BUILD/f'{name}.json').read_text())
            folder = work/name
            folder.mkdir()
            (folder/'main.cu').write_text(stub(name, meta))
            ptx = (triton.BUILD/f'{name}.ptx').read_text().replace('.version 8.8', '.version 8.5', 1)
            (folder/'kernel.ptx').write_text(ptx)
            for i, case in enumerate(cases):
                cdir = folder/f'c{i:03d}'
                cdir.mkdir()
                np.asarray(case.shapes['programs'], np.uint32).tofile(cdir/'grid.bin')
                buffers = []
                for pname, kind in meta['signature'].items():
                    if kind.startswith('*'):
                        np.asarray(case.inputs[pname]).tofile(cdir/f'{pname}.bin')
                        buffers.append((pname, np.asarray(case.inputs[pname]).dtype))
                    else:
                        dtype = {'fp32': np.float32, 'i32': np.int32, 'i64': np.int64, 'u32': np.uint32}[kind]
                        np.asarray(case.values[pname], dtype).tofile(cdir/f'{pname}.bin')
                layout[(name, i)] = (cdir, buffers)
        result = subprocess.run(['docker', 'run', '--rm', '--label', 'qpel=1', '--memory', '6g', '--cpus', '4',
                                 '-v', f'{work}:{work}', '-e', f'ROOT={work}', '-e', f'CONFIG={CONFIG}',
                                 '-e', 'CUDA_INSTALL_PATH=/usr/local/cuda', IMAGE, 'bash', '-c', SCRIPT],
                                capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise RuntimeError(f'GPGPU-Sim container failed:\n{result.stderr[-3000:]}')
        out = {}
        for key, (cdir, buffers) in layout.items():
            status = (cdir/'status').read_text().strip() if (cdir/'status').exists() else 'none'
            if status != '0':
                log = (cdir.parent/'build.log').read_text()[-1500:] if status == '125' else \
                    (cdir/'run.log').read_text()[-1500:] if (cdir/'run.log').exists() else ''
                out[key] = f'status {status}: {log}'
                continue
            out[key] = {b: np.fromfile(cdir/f'out_{b}.bin', dtype) for b, dtype in buffers}
        return out


def rows(names=None):
    """Yield (row, case label, error or None)."""
    from qpel_ir import Lowering
    from qpel import compile_program
    from .simulate import Program
    if not image_ready() or not triton.docker_ready():
        yield 'nvidia: triton ptx', 'GPGPU-Sim', 'skipped: no Docker, GPGPU-Sim or qpel/triton image'
        return
    names = names or triton.kernels()
    jobs, built = [], {}
    with tempfile.TemporaryDirectory(prefix='qpel-triton-qpel-') as work:
        work = Path(work)
        for name in names:
            prog, lowering = triton.program(name)
            folder = work/name
            (folder/'sim').mkdir(parents=True)
            (folder/'p.json').write_text(json.dumps(prog))
            compile_program(folder/'p.json', folder/'package')
            plan = Lowering(prog).plan()
            cases = triton.cases(name)
            built[name] = (lowering, plan, folder, cases)
            jobs.append((name, cases))
        references = triton.references([(n, built[n][0].params, built[n][3]) for n in names])
        ptx = run(jobs)
        for name in names:
            lowering, plan, folder, cases = built[name]
            outputs = [r['name'] for r in plan['resources'] if r['kind'] in ('output', 'inout')]
            # Triton's approximations (div.full, ex2.approx) only where the kernel divides or calls exp
            approximate = lowering.divides or lowering.bounded
            ulps = triton.tolerance(None, lowering) + (TRITON_PTX_ULPS + 2 * MATH_ULPS if approximate else 0)
            simulated = Program(folder/'package/package/generated'/(plan['module'] + '.cu'), plan, folder/'sim')
            for i, case in enumerate(cases):
                label = f'{name} {case.label}'
                got = ptx[(name, i)]
                if isinstance(got, str):
                    yield 'nvidia: triton ptx', label, got
                    continue
                reduction = bool(lowering.float_sums)
                yield 'nvidia: triton ptx', label, triton.check(references[(name, i)], got, outputs, ulps, case, reduction)
                yield 'nvidia: triton ptx = qpel', label, triton.check(got, simulated.run(case), outputs, ulps, case, reduction)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('kernels', nargs='*')
    args = parser.parse_args(argv)
    summary, failures = {}, 0
    for row, label, error in rows(args.kernels or None):
        if error and error.startswith('skipped'):
            print(f'SKIP {row}: {error}')
            continue
        key = (row, label.split(' ')[0])
        summary.setdefault(key, [0, 0])
        summary[key][0] += 1
        if error:
            failures += 1
            summary[key][1] += 1
            print(f'FAIL {row:28} {label}\n     {error[:600]}')
    for (row, kernel), (count, failed) in summary.items():
        print(f'{"PASS" if not failed else "FAIL"} {row:28} {kernel}: {count - failed}/{count} cases')
    print('all rows passed' if not failures else f'{failures} cases failed')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
