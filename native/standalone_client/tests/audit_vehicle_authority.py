#!/usr/bin/env python3
"""Build a temporary probe and compare actor phases against current mod source.

Run from any directory. This verifies the source port with frozen query fixtures;
it does not exercise Windows scene collision, networking, or presentation.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path,
                        help="Use an already built vehicle_authority_probe shared library")
    args = parser.parse_args()
    tests = Path(__file__).resolve().parent
    native = tests.parent
    with tempfile.TemporaryDirectory(prefix="wot-authority-audit-") as temporary:
        library = args.library
        if library is None:
            if os.name == "nt":
                parser.error("Build vehicle_authority_probe.cpp with the core sources and pass --library")
            library = Path(temporary) / ("probe.dylib" if sys.platform == "darwin" else "probe.so")
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++17", "-O2", "-ffp-contract=off", "-fPIC", "-shared",
                "-I", str(native / "include"),
                str(tests / "vehicle_authority_probe.cpp"),
                *[str(native / "src" / (name + ".cpp")) for name in
                  ("vehicle_authority", "vehicle_control", "vehicle_suspension")],
                "-o", str(library),
            ], check=True)
        environment = dict(os.environ, WOT_AUTHORITY_PROBE=str(library.resolve()),
                           PYTHONDONTWRITEBYTECODE="1")
        for phase in ("human", "bot", "human_ground", "bot_ground"):
            subprocess.run([sys.executable, str(tests / ("verify_vehicle_authority_" + phase + ".py"))],
                           check=True, env=environment)


if __name__ == "__main__":
    main()
