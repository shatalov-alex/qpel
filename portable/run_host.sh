#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD=$(mktemp -d "${TMPDIR:-/tmp}/qpel-host.XXXXXX")
trap 'rm -rf "$BUILD"' EXIT
c++ -std=c++17 -O2 -I "$HERE/generated" "$HERE/tests/host.cpp" -o "$BUILD/test"
"$BUILD/test"
