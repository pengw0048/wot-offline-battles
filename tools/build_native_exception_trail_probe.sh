#!/bin/sh
# Build the Windows probe that exercises the shipped first-chance exception
# recorder. It uses the same pinned cross toolchain as the sidecar itself, so
# the probe runs the production source, not a copy of it.
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT_DIR=${1:-$PORT_ROOT/dist}
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(CDPATH= cd -- "$OUTPUT_DIR" && pwd)

docker run --rm \
	-v "$PORT_ROOT:/src" \
	-v "$OUTPUT_DIR:/out" \
	-w /src \
	debian:bookworm-slim \
	/bin/sh -c '
		set -eu
		apt-get update >/dev/null
		DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
			gcc-mingw-w64-i686 binutils-mingw-w64-i686 >/dev/null
		# The frame pointer keeps the probe walkable, exactly like the
		# stock #1513 frames the recorder has to describe.
		i686-w64-mingw32-gcc -m32 -std=c99 -Os -fno-omit-frame-pointer \
			-Wall -Wextra -Werror \
			-o /out/native_exception_trail.exe \
			/src/tests/native_exception_trail.c -luser32
	'

echo "Built $OUTPUT_DIR/native_exception_trail.exe"
