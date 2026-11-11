"""Compile every generated CUDA backend with the real NVIDIA toolchain inside Docker.

No NVIDIA GPU is needed: nvcc and ptxas run in an arm64 (or amd64) nvidia/cuda
devel image. This proves NVIDIA's compiler accepts the generated code for real
GPU architectures; it does not execute it.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from fixture_builds import build_fixtures

HERE = Path(__file__).resolve().parent
IMAGE = 'nvidia/cuda:12.6.3-devel-ubuntu24.04'
# Turing, Ampere, Ada, Hopper: the oldest and newest architectures CUDA 12 targets well.
ARCHS = ('sm_75', 'sm_80', 'sm_89', 'sm_90')

# Runs inside the container. Each kernel file is compiled to one object holding SASS
# for every architecture; ptxas resource usage is printed; warnings fail the check.
# Register spills are warnings; local memory is not, because CUDA's own sinf/cosf use a
# small stack frame for large-argument range reduction (math_ops).
CONTAINER_SCRIPT = r'''
set -u
cd /work
nvcc --version | tail -1
gencode=""
for a in $ARCHS; do gencode="$gencode -gencode arch=compute_${a#sm_},code=$a"; done
failed=0
for cu in cases/*/*.cu; do
  name=${cu%.cu}
  if nvcc -std=c++17 -O2 -c $gencode -Werror all-warnings -Xptxas -v,-warn-spills \
       -o "$name.o" "$cu" > "$name.log" 2>&1; then
    regs=$(grep -o 'Used [0-9]* registers' "$name.log" | sort -u | tr '\n' ' ')
    echo "PASS $cu  ($regs)"
  else
    echo "FAIL $cu"; sed 's/^/    /' "$name.log"; failed=1
  fi
done
# Whole-program link of the C++ CUDA harness against the generated SAM 2 package.
if [ -d host ]; then
  if nvcc -std=c++17 -O2 $gencode -Werror all-warnings -I host/generated -I host/tests \
       host/tests/cuda.cu -o host/cuda_test > host/link.log 2>&1; then
    echo "PASS host link tests/cuda.cu"
  else
    echo "FAIL host link tests/cuda.cu"; sed 's/^/    /' host/link.log; failed=1
  fi
fi
exit $failed
'''


def stage_cases(work):
    """Build each IR fixture with the offline compiler; copy its generated .cu files."""
    cases = work/'cases'
    with tempfile.TemporaryDirectory() as temp:
        for name, generated in build_fixtures(Path(temp)):
            (cases/name).mkdir(parents=True)
            for cu in generated.glob('*.cu'):
                shutil.copy(cu, cases/name/cu.name)
    # The checked-in SAM 2 package is shipped by sam2_adapter.py, so check it as-is too.
    (cases/'checked_in').mkdir()
    shutil.copy(HERE/'generated/components.cu', cases/'checked_in/components.cu')
    # The host harness includes "components.cu" + "pipeline.inc" from generated/.
    host = work/'host'
    shutil.copytree(HERE/'generated', host/'generated')
    (host/'tests').mkdir()
    for name in ('cuda.cu', 'cases.hpp'):
        shutil.copy(HERE/'tests'/name, host/'tests'/name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--arch', action='append', help='Override target architectures (e.g. sm_80)')
    parser.add_argument('--keep', type=Path, help='Copy the staged sources and logs to this new directory')
    args = parser.parse_args(argv)
    if shutil.which('docker') is None:
        sys.exit('docker is required')
    with tempfile.TemporaryDirectory(prefix='qpel-nvcc-') as temp:
        work = Path(temp)
        stage_cases(work)
        result = subprocess.run(
            ['docker', 'run', '--rm', '-v', f'{work}:/work', '-e', 'ARCHS=' + ' '.join(args.arch or ARCHS),
             args.image, 'bash', '-c', CONTAINER_SCRIPT])
        if args.keep:
            shutil.copytree(work, args.keep)
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
