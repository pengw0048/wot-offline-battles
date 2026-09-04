#!/usr/bin/env python3
"""Build exact #1513 engine-config variants for isolated preferences."""

from __future__ import print_function

import argparse
import os
import sys


_TOOLS_ROOT = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_ROOT not in sys.path:
    sys.path.insert(0, _TOOLS_ROOT)
import packed_xml


STOCK_PREFERENCES = b"preferences.xml"
VARIANTS = (
    ("engine_config.offline-player.xml", b"playerprefs.xml"),
    ("engine_config.offline-worker.xml", b"workerprefs.xml"),
)


def _root_preferences(root):
    matches = [
        value for name, value in root.children if name == b"preferences"
    ]
    if len(matches) != 1:
        raise ValueError(
            "engine_config.xml must contain exactly one root preferences field"
        )
    value = matches[0]
    if (value.value_type != packed_xml.TYPE_STRING or
            value.value != STOCK_PREFERENCES):
        raise ValueError(
            "engine_config.xml has an unexpected root preferences value"
        )
    return value


def _variant_data(stock_data, preferences_leaf):
    if len(preferences_leaf) != len(STOCK_PREFERENCES):
        raise ValueError("the preferences replacement must preserve length")
    if (b"/" in preferences_leaf or b"\\" in preferences_leaf or
            b"\0" in preferences_leaf):
        raise ValueError("the preferences replacement must be a leaf name")
    if stock_data.count(STOCK_PREFERENCES) != 1:
        raise ValueError(
            "engine_config.xml must contain one encoded preferences leaf"
        )
    result = stock_data.replace(STOCK_PREFERENCES, preferences_leaf, 1)
    root = packed_xml.read_packed_xml(result)
    matches = [
        value for name, value in root.children if name == b"preferences"
    ]
    if (len(matches) != 1 or
            matches[0].value_type != packed_xml.TYPE_STRING or
            matches[0].value != preferences_leaf):
        raise ValueError("the generated engine config failed validation")
    if result.replace(preferences_leaf, STOCK_PREFERENCES, 1) != stock_data:
        raise ValueError("the generated engine config changed extra bytes")
    return result


def _write_new_or_same(path, payload):
    if os.path.exists(path):
        if not os.path.isfile(path):
            raise ValueError("output path is not a regular file: %s" % path)
        with open(path, "rb") as stream:
            current = stream.read()
        if current != payload:
            raise ValueError("refusing to overwrite existing file: %s" % path)
        return False

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return True


def build_preferences_configs(stock_path, output_directory):
    with open(stock_path, "rb") as stream:
        stock_data = stream.read()
    _root_preferences(packed_xml.read_packed_xml(stock_data))

    if not os.path.isdir(output_directory):
        os.makedirs(output_directory)
    outputs = []
    for filename, preferences_leaf in VARIANTS:
        payload = _variant_data(stock_data, preferences_leaf)
        destination = os.path.join(output_directory, filename)
        _write_new_or_same(destination, payload)
        outputs.append(destination)
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Build player and worker Packed XML configs from the exact "
            "#1513 stock engine_config.xml."
        )
    )
    parser.add_argument("stock_engine_config")
    parser.add_argument("output_directory")
    args = parser.parse_args(argv)
    try:
        outputs = build_preferences_configs(
            os.path.abspath(args.stock_engine_config),
            os.path.abspath(args.output_directory),
        )
    except (IOError, OSError, ValueError) as error:
        parser.error(str(error))
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
