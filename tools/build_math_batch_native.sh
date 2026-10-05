#!/bin/sh
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="$PORT_ROOT/native/offline_math_batch_native.pyd"

build_bridge() {
    i686-w64-mingw32-g++ -m32 -std=c++17 -O3 -msse2 -mfpmath=sse \
        -fno-fast-math -ffp-contract=off \
        -fno-builtin-sin -fno-builtin-cos -fno-builtin-pow \
        -fno-builtin-hypot -fno-builtin-fmod \
        -Wall -Wextra -Wpedantic -Werror -fno-ident \
        -shared -static -static-libgcc -static-libstdc++ -s \
        -Wl,--no-insert-timestamp -Wl,--kill-at \
        -o "$OUTPUT" \
        "$PORT_ROOT/native/offline_math_batch_geometry.cpp" \
        "$PORT_ROOT/native/offline_math_batch_native.cpp"
    PE_INFO=$(i686-w64-mingw32-objdump -p "$OUTPUT")
    echo "$PE_INFO" | grep -q 'file format pei-i386'
    echo "$PE_INFO" | grep -q 'initoffline_math_batch_native'
    echo "$PE_INFO" | sed -n 's/^[[:space:]]*DLL Name: //p' |
        while IFS= read -r dependency; do
            case "$dependency" in
                KERNEL32.dll|msvcrt.dll|api-ms-win-crt-*.dll) ;;
                *)
                    echo "native math bridge unexpectedly imports $dependency" >&2
                    exit 1
                    ;;
            esac
        done
}

if command -v i686-w64-mingw32-g++ >/dev/null 2>&1 &&
        command -v i686-w64-mingw32-objdump >/dev/null 2>&1; then
    build_bridge
else
    docker run --rm -v "$PORT_ROOT:/src" -w /src debian:bookworm-slim \
        /bin/sh -c '
            set -eu
            apt-get update >/dev/null
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
                g++-mingw-w64-i686 binutils-mingw-w64-i686 >/dev/null
            /bin/sh /src/tools/build_math_batch_native.sh
        '
fi

echo "Built $OUTPUT"
