#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <World of Tanks 0.9.22.0.1 #1513 client root>" >&2
  exit 2
fi

port_root="$(cd "$(dirname "$0")" && pwd)"
python3 "$port_root/tools/inspect_client.py" "$1"

if command -v python2.7 >/dev/null 2>&1; then
  python2.7 "$port_root/tools/audit_embedded_types.py"
  python2.7 "$port_root/tools/audit_native_resource_ownership.py" \
    "$port_root/src/res/scripts/client/gui/mods/offline_lan_0922"
  python2.7 "$port_root/tools/audit_client_abi.py" "$1"
  python2.7 "$port_root/tools/audit_lobby_consumers.py" "$1"
  python2.7 "$port_root/tools/audit_client_lifecycle.py" "$1"
  python2.7 "$port_root/build_wotmod.py"
elif command -v pyenv >/dev/null 2>&1 && \
    PYENV_VERSION=2.7.18 pyenv which python2.7 >/dev/null 2>&1; then
  py27_path="$(PYENV_VERSION=2.7.18 pyenv which python2.7)"
  "$py27_path" "$port_root/tools/audit_embedded_types.py"
  "$py27_path" "$port_root/tools/audit_native_resource_ownership.py" \
    "$port_root/src/res/scripts/client/gui/mods/offline_lan_0922"
  "$py27_path" "$port_root/tools/audit_client_abi.py" "$1"
  "$py27_path" "$port_root/tools/audit_lobby_consumers.py" "$1"
  "$py27_path" "$port_root/tools/audit_client_lifecycle.py" "$1"
  "$py27_path" "$port_root/build_wotmod.py"
else
  docker run --rm --platform linux/amd64 \
    -v "$port_root:/work:ro" \
    python:2.7.18 python /work/tools/audit_embedded_types.py
  docker run --rm --platform linux/amd64 \
    -v "$port_root:/work:ro" \
    python:2.7.18 python /work/tools/audit_native_resource_ownership.py \
      /work/src/res/scripts/client/gui/mods/offline_lan_0922
  docker run --rm --platform linux/amd64 \
    -v "$port_root:/work:ro" -v "$(cd "$1" && pwd):/client:ro" \
    python:2.7.18 python /work/tools/audit_client_abi.py /client
  docker run --rm --platform linux/amd64 \
    -v "$port_root:/work:ro" -v "$(cd "$1" && pwd):/client:ro" \
    python:2.7.18 python /work/tools/audit_lobby_consumers.py /client
  docker run --rm --platform linux/amd64 \
    -v "$port_root:/work:ro" -v "$(cd "$1" && pwd):/client:ro" \
    python:2.7.18 python /work/tools/audit_client_lifecycle.py /client
  "$port_root/build_wotmod_docker.sh"
fi

python3 "$port_root/tools/validate_wotmod.py" \
  "$port_root/dist/org.peng.offline_lan_0922_0.6.5.wotmod"
