#!/bin/sh
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="$PORT_ROOT/native/offline_instance_guard_native.pyd"

docker run --rm \
	-v "$PORT_ROOT:/src" \
	-w /src \
	debian:bookworm-slim \
	/bin/sh -c '
		set -eu
		apt-get update >/dev/null
		DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
			gcc-mingw-w64-i686 binutils-mingw-w64-i686 >/dev/null
		i686-w64-mingw32-gcc -m32 -std=c99 -Os -Wall -Wextra -Werror \
			-fno-ident -fno-asynchronous-unwind-tables -shared -static-libgcc -s \
			-Wl,--no-insert-timestamp -Wl,--kill-at \
			-o /src/native/offline_instance_guard_native.pyd \
			/src/native/offline_instance_guard_native.c -luser32
		i686-w64-mingw32-objdump -p \
			/src/native/offline_instance_guard_native.pyd > /tmp/bridge.pe
		grep -q "offline_instance_guard_native" /tmp/bridge.pe
		grep -q "initoffline_instance_guard_native" /tmp/bridge.pe
		sed -n "s/^[[:space:]]*DLL Name: //p" /tmp/bridge.pe |
			while IFS= read -r dependency; do
				case "$dependency" in
					KERNEL32.dll|msvcrt.dll|USER32.dll) ;;
					*)
						echo "native bridge unexpectedly imports $dependency" >&2
						exit 1
						;;
				esac
			done
	'

echo "Built $OUTPUT"
