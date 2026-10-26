#!/usr/bin/env python3
"""Check native header adaptation against the pinned client's original banks."""

import argparse
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile


def sound_id(name):
    value = 2166136261
    for byte in name.lower().encode("ascii"):
        value = ((value * 16777619) & 0xffffffff) ^ byte
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", type=Path)
    parser.add_argument("--compiler", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="wot-wwise-bank-") as temporary:
        temporary = Path(temporary)
        probe = temporary / "bank-probe"
        subprocess.run([
            args.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-I", str(root / "include"), str(root / "src/wwise_bank.cpp"),
            str(root / "tests/test_wwise_bank.cpp"), "-o", str(probe),
        ], check=True)
        subprocess.run([str(probe)], check=True)
        headers = set()
        with zipfile.ZipFile(args.client / "res/packages/audioww.pkg") as package:
            banks = sorted(name for name in package.namelist() if name.endswith(".bnk"))
            assert banks, "Pinned audio package contains no banks"
            for name in banks:
                original = package.read(name)
                source, output = temporary / "encoded.bnk", temporary / "decoded.bnk"
                source.write_bytes(original)
                subprocess.run([str(probe), str(source), str(output)],
                               check=True, stdout=subprocess.DEVNULL)
                decoded = output.read_bytes()
                assert len(decoded) == len(original), name
                assert decoded[:8] == original[:8] and decoded[24:] == original[24:], name
                version, bank_id = struct.unpack_from("<II", decoded, 8)
                assert version == 125, name
                assert bank_id == sound_id(Path(name).stem), name
                offset = 0
                while offset < len(decoded):
                    assert len(decoded) - offset >= 8, name
                    unused_tag, length = struct.unpack_from("<4sI", decoded, offset)
                    offset += 8 + length
                    assert offset <= len(decoded), name
                assert offset == len(decoded), name
                headers.add(struct.unpack_from("<I", decoded, 4)[0])
        print("Native adaptation checked %d original banks; BKHD sizes: %s" %
              (len(banks), ", ".join(map(str, sorted(headers)))))
        print("Byte/header validation only; SDK loading and playback were not exercised")


if __name__ == "__main__":
    main()
