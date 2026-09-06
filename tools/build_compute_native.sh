#!/bin/sh
# Build the exact-build sweep preparation extension for #1513.
#
# The client is 32-bit x86, so the extension must be i686.  Prefer the same
# pinned container the instance-guard bridge uses; fall back to a local i686
# mingw toolchain when Docker is unavailable.
#
# -msse2 -mfpmath=sse -ffp-contract=off keeps the arithmetic on IEEE-754
# doubles with no x87 80-bit intermediates and no fused multiply-add, which is
# what #1513's interpreter does (its float add compiles to addsd).  Without
# these the extension and Python disagree in the last places.
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="$PORT_ROOT/native/offline_compute_native.pyd"

BUILD_COMMAND='
	set -eu
	i686-w64-mingw32-gcc -m32 -std=c99 -O2 -Wall -Wextra -Werror \
		-msse2 -mfpmath=sse -ffp-contract=off \
		-fno-ident -fno-asynchronous-unwind-tables -shared -s \
		-Wl,--no-insert-timestamp -Wl,--kill-at \
		-o "$OUTPUT_PATH" \
		"$SOURCE_ROOT/native/offline_compute_native.c" \
		"$SOURCE_ROOT/native/offline_compute_core.c"
	i686-w64-mingw32-objdump -p "$OUTPUT_PATH" > "$INSPECT_PATH"
	grep -q "offline_compute_native" "$INSPECT_PATH"
	grep -q "initoffline_compute_native" "$INSPECT_PATH"
	if grep -qi "python[0-9].*\.dll" "$INSPECT_PATH"; then
		echo "native compute bridge unexpectedly imports a Python DLL" >&2
		exit 1
	fi
'

if command -v docker >/dev/null 2>&1; then
	docker run --rm -v "$PORT_ROOT:/src" -w /src debian:bookworm-slim \
		/bin/sh -c "
			set -eu
			apt-get update >/dev/null
			DEBIAN_FRONTEND=noninteractive apt-get install -y \
				--no-install-recommends gcc-mingw-w64-i686 \
				binutils-mingw-w64-i686 >/dev/null
			SOURCE_ROOT=/src
			OUTPUT_PATH=/src/native/offline_compute_native.pyd
			INSPECT_PATH=/tmp/compute.pe
			$BUILD_COMMAND
		"
elif command -v i686-w64-mingw32-gcc >/dev/null 2>&1; then
	echo "Docker is unavailable; building with the local i686 toolchain." >&2
	SOURCE_ROOT="$PORT_ROOT" \
	OUTPUT_PATH="$OUTPUT" \
	INSPECT_PATH="$(mktemp)" \
	/bin/sh -c "$BUILD_COMMAND"
else
	echo "no i686 mingw toolchain and no Docker" >&2
	exit 1
fi

echo "Built $OUTPUT"
