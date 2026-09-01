#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

port_root="$(cd "$(dirname "$0")" && pwd)"
project_root="$(cd "$port_root/.." && pwd)"

if command -v python2.7 >/dev/null 2>&1; then
  build_output="$(python2.7 \
    "$port_root/tools/build_hidden_worker_profiler.py" "$@")"
elif command -v pyenv >/dev/null 2>&1 && \
    PYENV_VERSION=2.7.18 pyenv which python2.7 >/dev/null 2>&1; then
  py27_path="$(PYENV_VERSION=2.7.18 pyenv which python2.7)"
  build_output="$("$py27_path" \
    "$port_root/tools/build_hidden_worker_profiler.py" "$@")"
else
  build_output="$(docker run --rm --platform linux/amd64 \
    -v "$project_root:/work" \
    -w /work/0.9.22 \
    python:2.7.18 \
    python tools/build_hidden_worker_profiler.py "$@")"
fi
echo "$build_output"

archive="$(echo "$build_output" | tail -1)"
if [[ "$archive" == /work/* ]]; then
  archive="$project_root/${archive#/work/}"
fi
if [[ -z "$archive" ]]; then
  echo "diagnostic ZIP was not produced" >&2
  exit 1
fi
python3 "$port_root/tools/validate_hidden_worker_profiler_zip.py" "$archive"
echo "$archive"
