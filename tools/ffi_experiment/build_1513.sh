#!/bin/sh
# Build a separate, unshipped x86 #1513 prototype. No game files are changed.
set -eu
EXPERIMENT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EXPERIMENT_OUTPUT=${1:?Pass a temporary build directory}
mkdir -p "$EXPERIMENT_OUTPUT"
i686-w64-mingw32-gcc -std=c99 -O2 -Wall -Wextra -Werror \
    -msse2 -mfpmath=sse -ffp-contract=off \
    -c "$EXPERIMENT_ROOT/astar_1513.c" -o "$EXPERIMENT_OUTPUT/astar_1513.o"
i686-w64-mingw32-g++ -std=c++11 -O2 -Wall -Wextra -Werror \
    -msse2 -mfpmath=sse -ffp-contract=off -fno-fast-math \
    -static -static-libgcc -static-libstdc++ -shared -s \
    -Wl,--no-insert-timestamp -Wl,--kill-at \
    "$EXPERIMENT_ROOT/astar_core.cpp" "$EXPERIMENT_ROOT/combat_core.cpp" \
    "$EXPERIMENT_ROOT/driver_core.cpp" \
    "$EXPERIMENT_ROOT/perception_core.cpp" \
    "$EXPERIMENT_OUTPUT/astar_1513.o" \
    -o "$EXPERIMENT_OUTPUT/offline_astar_native.pyd"
i686-w64-mingw32-objdump -p "$EXPERIMENT_OUTPUT/offline_astar_native.pyd" \
    > "$EXPERIMENT_OUTPUT/astar_1513.pe.txt"
python3 - "$EXPERIMENT_OUTPUT/astar_1513.pe.txt" <<'PY'
import re
import sys
text = open(sys.argv[1]).read()
assert 'file format pei-i386' in text
assert 'initoffline_astar_native' in text
imports = re.findall(r'DLL Name: (\S+)', text)
assert set(name.lower() for name in imports) <= {'kernel32.dll', 'msvcrt.dll'}, imports
print('Unshipped #1513 prototype: x86; imports: ' + ', '.join(imports))
PY
