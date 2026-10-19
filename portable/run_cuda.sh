#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v nvcc >/dev/null 2>&1 || { echo 'CUDA Toolkit (nvcc) is required; this test must run on an NVIDIA machine.' >&2; exit 1; }
BUILD=$(mktemp -d "${TMPDIR:-/tmp}/qpel-cuda.XXXXXX")
trap 'rm -rf "$BUILD"' EXIT
nvcc -std=c++17 -O2 -I "$HERE/generated" "$HERE/tests/cuda.cu" -o "$BUILD/test"
"$BUILD/test"
