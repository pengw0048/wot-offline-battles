#!/bin/sh
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="$PORT_ROOT/native/offline_worker_starter.exe"

docker run --rm \
  -v "$PORT_ROOT:/src" \
  -w /src \
  debian:bookworm-slim \
  /bin/sh -c '
    set -eu
    apt-get update >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gcc-mingw-w64-i686 binutils-mingw-w64-i686 >/dev/null
    i686-w64-mingw32-gcc -m32 -municode -mwindows -std=c99 -Os \
      -Wall -Wextra -Werror -fno-ident -fno-asynchronous-unwind-tables \
      -s -Wl,--no-insert-timestamp \
      -o /src/native/offline_worker_starter.exe \
      /src/native/offline_worker_starter.c -luser32 -lws2_32
    i686-w64-mingw32-objdump -p \
      /src/native/offline_worker_starter.exe > /tmp/starter.pe
    grep -q "Subsystem.*Windows GUI" /tmp/starter.pe
    grep -q "CreateDesktopW" /tmp/starter.pe
    grep -q "CreateProcessW" /tmp/starter.pe
    grep -q "CheckRemoteDebuggerPresent" /tmp/starter.pe
    grep -q "OpenEventW" /tmp/starter.pe
    grep -q "SetEvent" /tmp/starter.pe
  '

echo "Built $OUTPUT"
