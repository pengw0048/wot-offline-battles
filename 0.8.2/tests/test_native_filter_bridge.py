import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/native_filter_bridge.py"
)


class NativeFilterBridgeTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        self.logs = []
        for name in ("gui", "gui.mods", "gui.mods.offhangar"):
            sys.modules[name] = types.ModuleType(name)
        logging = types.ModuleType("gui.mods.offhangar.logging")
        logging.LOG_NOTE = lambda message: self.logs.append(("note", message))
        logging.LOG_ERROR = lambda message: self.logs.append(("error", message))
        sys.modules["gui.mods.offhangar.logging"] = logging

        spec = importlib.util.spec_from_file_location(
            "native_filter_bridge_under_test", BRIDGE_PATH
        )
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self.saved_modules)

    def install_native_module(self):
        calls = {"seed": [], "output": [], "owner": [], "root": []}
        native = types.ModuleType(
            "gui.mods.offhangar.offhangar_native_seed"
        )
        native.seed_filter = lambda *args: calls["seed"].append(args)
        native.output_filter = lambda *args: calls["output"].append(args)
        native.filter_has_physics = lambda *args: calls["owner"].append(args)
        native.publish_physics_root = lambda *args: calls["root"].append(args)
        sys.modules["gui.mods.offhangar.offhangar_native_seed"] = native
        sys.modules["gui.mods.offhangar"].offhangar_native_seed = native
        return calls

    def test_installed_seed_loads_once_and_marshals_seed(self):
        calls = self.install_native_module()
        vehicle_filter = object()

        self.assertTrue(self.bridge.seed_filter(
            vehicle_filter, 123.5, 7,
            (1.0, 2.0, 3.0), (0.1, 0.2, 0.3),
        ))
        self.assertTrue(self.bridge.seed_filter(
            vehicle_filter, 124.5, 7,
            (4.0, 5.0, 6.0), (0.0, 0.0, 0.4),
        ))

        self.assertEqual(2, len(calls["seed"]))
        self.assertEqual((vehicle_filter, 123.5, 7, 0,
                          1.0, 2.0, 3.0, 0.1, 0.2, 0.3), calls["seed"][0])
        # Native history order is yaw, pitch, roll.
        self.assertEqual((0.1, 0.2, 0.3), calls["seed"][0][-3:])
        self.assertEqual(1, sum(
            "NATIVE_FILTER_BRIDGE loaded" in message
            for unused_level, message in self.logs
        ))

    def test_a_missing_seed_names_the_path_and_logs_once(self):
        sys.modules.pop("gui.mods.offhangar.offhangar_native_seed", None)
        if hasattr(sys.modules["gui.mods.offhangar"],
                   "offhangar_native_seed"):
            del sys.modules["gui.mods.offhangar"].offhangar_native_seed
        self.bridge._seed_path = lambda: "/nowhere/offhangar_native_seed.pyd"

        self.assertIsNone(self.bridge.load())
        self.assertIsNone(self.bridge.load())
        self.assertEqual(1, sum(
            "native seed not installed at /nowhere" in message
            for unused_level, message in self.logs
        ))

    def test_native_seed_exception_is_contained(self):
        self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]

        def fail(*unused_args):
            raise RuntimeError("seed refused")

        native.seed_filter = fail
        self.assertFalse(self.bridge.seed_filter(
            object(), 1.0, 2, (4, 5, 6), (0, 0, 0.7)
        ))
        self.assertTrue(any(
            "seed failed: seed refused" in message
            for unused_level, message in self.logs
        ))

    def test_nonfinite_seed_is_rejected_before_native_code(self):
        calls = self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]
        self.bridge.load = lambda: native

        self.assertFalse(self.bridge.seed_filter(
            object(), float("nan"), 2, (4, 5, 6), (0, 0, 0.7)
        ))
        self.assertFalse(self.bridge.seed_filter(
            object(), 1.0, 2, (float("inf"), 5, 6), (0, 0, 0.7)
        ))
        self.assertFalse(self.bridge.seed_filter(
            object(), 1.0, 2, (4, 5, 6), (0, 0, float("-inf"))
        ))

        self.assertEqual([], calls["seed"])
        self.assertEqual(3, sum(
            "seed rejected: non-finite" in message
            for unused_level, message in self.logs
        ))

    def test_out_of_range_seed_is_rejected_before_float32_cast(self):
        calls = self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]
        self.bridge.load = lambda: native

        self.assertFalse(self.bridge.seed_filter(
            object(), 1.0, 2, (1e100, 5, 6), (0, 0, 0.7)
        ))
        self.assertFalse(self.bridge.seed_filter(
            object(), 1.0, 2, (12001.0, 5, 6), (0, 0, 0.7)
        ))

        self.assertEqual([], calls["seed"])
        errors = [message for level, message in self.logs if level == "error"]
        self.assertTrue(any("exceeds float32 range" in message for message in errors))
        self.assertTrue(any("exceeds world bounds" in message for message in errors))

    def test_matching_executable_marshals_filter_output(self):
        calls = self.install_native_module()
        vehicle_filter = object()

        self.assertTrue(self.bridge.output_filter(vehicle_filter, 125.25))

        self.assertEqual([(vehicle_filter, 125.25)], calls["output"])

    def test_nonfinite_output_is_rejected_before_native_code(self):
        calls = self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]
        self.bridge.load = lambda: native

        self.assertFalse(self.bridge.output_filter(object(), float("nan")))
        self.assertFalse(self.bridge.output_filter(object(), float("inf")))

        self.assertEqual([], calls["output"])

    def test_native_output_exception_is_contained(self):
        self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]

        def fail(*unused_args):
            raise RuntimeError("output refused")

        native.output_filter = fail
        self.assertFalse(self.bridge.output_filter(object(), 2.0))
        self.assertTrue(any(
            "output failed: output refused" in message
            for unused_level, message in self.logs
        ))

    def test_matching_executable_marshals_filter_physics_owner_check(self):
        calls = self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]
        self.bridge.load = lambda: native
        vehicle_filter = object()
        vehicle_physics = object()

        self.assertTrue(self.bridge.filter_has_physics(
            vehicle_filter, vehicle_physics
        ))

        self.assertEqual(
            [(vehicle_filter, vehicle_physics)], calls["owner"]
        )

    def test_native_filter_physics_owner_mismatch_is_contained(self):
        self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]
        self.bridge.load = lambda: native

        def fail(*unused_args):
            raise RuntimeError("owner mismatch")

        native.filter_has_physics = fail
        self.assertFalse(self.bridge.filter_has_physics(object(), object()))
        self.assertTrue(any(
            "owner check failed: owner mismatch" in message
            for unused_level, message in self.logs
        ))

    def test_matching_executable_marshals_physics_root_publish(self):
        calls = self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]
        self.bridge.load = lambda: native
        vehicle_filter = object()
        vehicle_physics = object()

        self.assertTrue(self.bridge.publish_physics_root(
            vehicle_filter, vehicle_physics, 126.5, 7
        ))

        self.assertEqual(
            [(vehicle_filter, vehicle_physics, 126.5, 7)], calls["root"]
        )

    def test_nonfinite_physics_root_publish_is_rejected(self):
        calls = self.install_native_module()
        native = sys.modules["gui.mods.offhangar.offhangar_native_seed"]
        self.bridge.load = lambda: native

        self.assertFalse(self.bridge.publish_physics_root(
            object(), object(), float("nan"), 7
        ))

        self.assertEqual([], calls["root"])


if __name__ == "__main__":
    unittest.main()
