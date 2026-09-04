import importlib.util
from pathlib import Path
import tempfile
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]


def _load_tool(name):
    path = PORT_ROOT / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreferencesConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = _load_tool("build_preferences_configs")
        cls.packed_xml = _load_tool("packed_xml")

    def _stock_data(self, preferences=b"preferences.xml"):
        packed = self.packed_xml
        return packed.write_packed_xml(packed.PackedElement(children=[
            (b"preferences", packed.PackedValue(
                packed.TYPE_STRING, preferences)),
            (b"renderer", packed.PackedValue(
                packed.TYPE_INTEGER, 120)),
        ]))

    def test_builds_byte_preserving_player_and_worker_variants(self):
        stock_data = self._stock_data()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock = root / "engine_config.xml"
            output = root / "res_mods" / "0.9.22.0.1"
            stock.write_bytes(stock_data)

            paths = self.builder.build_preferences_configs(stock, output)

            self.assertEqual([
                output / "engine_config.offline-player.xml",
                output / "engine_config.offline-worker.xml",
            ], [Path(path) for path in paths])
            for path, unused_filename_leaf in zip(
                    paths, self.builder.VARIANTS):
                filename, leaf = unused_filename_leaf
                self.assertEqual(filename, Path(path).name)
                payload = Path(path).read_bytes()
                self.assertEqual(len(stock_data), len(payload))
                self.assertEqual(
                    stock_data,
                    payload.replace(leaf, self.builder.STOCK_PREFERENCES, 1))
                root_section = self.packed_xml.read_packed_xml(payload)
                preferences = [
                    value for name, value in root_section.children
                    if name == b"preferences"
                ]
                self.assertEqual(leaf, preferences[0].value)

    def test_is_idempotent_but_refuses_unknown_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock = root / "engine_config.xml"
            output = root / "out"
            stock.write_bytes(self._stock_data())
            self.builder.build_preferences_configs(stock, output)
            self.builder.build_preferences_configs(stock, output)

            player = output / "engine_config.offline-player.xml"
            player.write_bytes(b"third-party")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                self.builder.build_preferences_configs(stock, output)
            self.assertEqual(b"third-party", player.read_bytes())

    def test_rejects_an_unexpected_stock_preferences_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock = root / "engine_config.xml"
            stock.write_bytes(self._stock_data(b"other.xml"))
            with self.assertRaisesRegex(ValueError, "unexpected"):
                self.builder.build_preferences_configs(stock, root / "out")

    def test_packager_accepts_the_checked_in_exact_base_pair(self):
        packager = _load_tool("../build_wotmod")
        source = (
            PORT_ROOT / "client_overlay" / "res_mods" / "0.9.22.0.1"
        )

        payloads = packager._preferences_config_payloads(str(source))

        self.assertEqual(
            [name for name, unused_payload in payloads],
            [name for name, unused_leaf in packager.PREFERENCES_CONFIGS],
        )
        restored = []
        for (filename, payload), (unused_name, leaf) in zip(
                payloads, packager.PREFERENCES_CONFIGS):
            self.assertEqual(filename, unused_name)
            self.assertEqual(1, payload.count(leaf.encode("ascii")))
            restored.append(payload.replace(
                leaf.encode("ascii"), self.builder.STOCK_PREFERENCES, 1))
        self.assertEqual(restored[0], restored[1])


if __name__ == "__main__":
    unittest.main()
