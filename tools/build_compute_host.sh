#!/bin/sh
# Build the host-interpreter compute bridge for differential tests and the
# local benchmark.  This artifact is never packaged and never loaded by the
# game; it proves the computation and the buffer contract only.
#
# Usage: tools/build_compute_host.sh [python-interpreter] [output-directory]
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${1:-python2.7}
OUTPUT_DIR=${2:-$PORT_ROOT/build/compute-host}
INCLUDE=$("$PYTHON" -c \
	'import sysconfig; print(sysconfig.get_paths()["include"])')

mkdir -p "$OUTPUT_DIR"
cc -shared -fPIC -std=c99 -O2 -Wall -Wextra -Werror -ffp-contract=off \
	-I"$INCLUDE" -I"$PORT_ROOT/native" \
	-o "$OUTPUT_DIR/offline_compute_native.so" \
	"$PORT_ROOT/native/offline_compute_host.c" \
	"$PORT_ROOT/native/offline_compute_core.c"

echo "Built $OUTPUT_DIR/offline_compute_native.so with $("$PYTHON" -V 2>&1)"
