#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

docker run --rm --platform linux/amd64 \
  -v "$PROJECT_ROOT:/work" \
  -w /work \
  python:2.7.18 \
  python build_wotmod.py
