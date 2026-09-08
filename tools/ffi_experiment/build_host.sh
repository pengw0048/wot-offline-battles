#!/bin/sh
# Build an unshipped host bridge with the caller's CPython ABI.
set -eu
EXPERIMENT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EXPERIMENT_PYTHON=${1:-python3}
EXPERIMENT_OUTPUT=${2:?Pass a temporary build directory}
EXPERIMENT_INCLUDE=$("$EXPERIMENT_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')
mkdir -p "$EXPERIMENT_OUTPUT"
cc -std=c99 -O2 -fPIC -Wall -Wextra -Werror -I"$EXPERIMENT_INCLUDE" \
    -c "$EXPERIMENT_ROOT/astar_host.c" -o "$EXPERIMENT_OUTPUT/astar_host.o"
c++ -std=c++11 -O2 -fPIC -Wall -Wextra -Werror -ffp-contract=off \
    -fno-fast-math -shared "$EXPERIMENT_ROOT/astar_core.cpp" \
    "$EXPERIMENT_ROOT/combat_core.cpp" \
    "$EXPERIMENT_ROOT/driver_core.cpp" \
    "$EXPERIMENT_ROOT/perception_core.cpp" \
    "$EXPERIMENT_ROOT/world_core.cpp" \
    "$EXPERIMENT_ROOT/query_bridge.cpp" \
    "$EXPERIMENT_ROOT/navigation_flow.cpp" \
    "$EXPERIMENT_ROOT/motion_core.cpp" \
    "$EXPERIMENT_OUTPUT/astar_host.o" -o "$EXPERIMENT_OUTPUT/offline_astar_native.so"
