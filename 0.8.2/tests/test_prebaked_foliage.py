import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "scripts/client/gui/mods/offhangar"
LOADER_PATH = MODULE_DIR / "prebaked_foliage.py"
FOLIAGE_PATH = MODULE_DIR / "foliage.py"


def load_foliage_loader(mod_directory):
    names = (
        "gui", "gui.mods", "gui.mods.offhangar",
        "gui.mods.offhangar.paths", "gui.mods.offhangar.foliage",
    )
    previous = {name: sys.modules.get(name) for name in names}
    paths = types.ModuleType("gui.mods.offhangar.paths")
    paths.mod_dir = lambda: str(mod_directory)
    sys.modules.update({
        "gui": types.ModuleType("gui"),
        "gui.mods": types.ModuleType("gui.mods"),
        "gui.mods.offhangar": types.ModuleType("gui.mods.offhangar"),
        "gui.mods.offhangar.paths": paths,
    })
    try:
        foliage_spec = importlib.util.spec_from_file_location(
            "gui.mods.offhangar.foliage", FOLIAGE_PATH
        )
        foliage = importlib.util.module_from_spec(foliage_spec)
        sys.modules["gui.mods.offhangar.foliage"] = foliage
        foliage_spec.loader.exec_module(foliage)
        loader_spec = importlib.util.spec_from_file_location(
            "prebaked_foliage_under_test", LOADER_PATH
        )
        loader = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(loader)
        return loader
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


class PrebakedFoliageTests(unittest.TestCase):
    def data(self):
        return {
            "format": "offhangar-foliage",
            "version": 1,
            "game_version": "0.8.2",
            "map": "07_lakeville",
            "cell_size": 32.0,
            "instances": [[0.0, 0.0, 0.0, 5.0,
                            0.5, 0.0, 0.0, 0.5, 0.15, 2.83]],
            "cells": {"0,0": [0]},
        }

    def test_loader_checks_version_and_map(self):
        with tempfile.TemporaryDirectory() as directory:
            foliage_dir = Path(directory) / "foliage"
            foliage_dir.mkdir()
            payload = json.dumps(self.data(), sort_keys=True).encode("utf-8")
            path = foliage_dir / "07_lakeville.json"
            path.write_bytes(payload)
            (foliage_dir / "manifest.json").write_text(json.dumps({
                "format": "offhangar-foliage-manifest",
                "version": 1,
                "game_version": "0.8.2",
                "maps": [{
                    "map": "07_lakeville",
                    "file": "07_lakeville.json",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            }))
            loader = load_foliage_loader(directory)
            loader.STOCK_MAPS = ("07_lakeville",)
            loaded = loader.load_foliage("spaces/07_lakeville")
            self.assertEqual("07_lakeville", loaded.map_name)
            # A different line ending is still the same foliage.
            path.write_bytes(payload + b"\n")
            self.assertEqual(
                "07_lakeville",
                loader.load_foliage("07_lakeville").map_name)

    def test_shipped_bundle_is_complete_and_has_no_skipped_bushes(self):
        loader = load_foliage_loader(MODULE_DIR)
        manifest = json.loads((MODULE_DIR / "foliage/manifest.json").read_text())
        self.assertEqual(set(loader.STOCK_MAPS), {
            record["map"] for record in manifest["maps"]
        })
        total = 0
        for map_name in loader.STOCK_MAPS:
            foliage_map = loader.load_foliage(map_name)
            self.assertGreater(len(foliage_map.instances), 0, map_name)
            total += len(foliage_map.instances)
            data = json.loads((MODULE_DIR / "foliage" /
                               (map_name + ".json")).read_text())
            self.assertEqual(0, data["bake"]["skipped_instances"], map_name)
        self.assertEqual(37671, total)


if __name__ == "__main__":
    unittest.main()
