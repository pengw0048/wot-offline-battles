from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.entities.native_remote_vehicle import \
    _NativeRemoteState
from gui.mods.offline_lan_0922.entities.remote_vehicle import RemoteVehicle


class _Matrix(object):

    def __init__(self):
        self.rotation = None

    def setRotateYPR(self, value):
        self.rotation = tuple(map(float, value))


def _appearance():
    return types.SimpleNamespace(
        turretMatrix=_Matrix(), gunMatrix=_Matrix())


class RemoteAimTests(unittest.TestCase):

    def test_fallback_remote_uses_dynamic_angles_for_a_normal_gun(self):
        vehicle = object.__new__(RemoteVehicle)
        vehicle.typeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace())
        vehicle.appearance = _appearance()

        vehicle.set_aim(0.25, 1.25, -0.3)

        self.assertAlmostEqual(1.0, vehicle.appearance.turretMatrix.rotation[0])
        self.assertAlmostEqual(-0.3, vehicle.appearance.gunMatrix.rotation[1])

    def test_static_gun_angles_drive_both_remote_presentations(self):
        descriptor = types.SimpleNamespace(gun=types.SimpleNamespace(
            staticTurretYaw=0.4, staticPitch=0.15))

        fallback = object.__new__(RemoteVehicle)
        fallback.typeDescriptor = descriptor
        fallback.appearance = _appearance()
        fallback.set_aim(0.25, 1.25, -0.3)

        native = object.__new__(_NativeRemoteState)
        native.aim = _appearance()
        native.entity = types.SimpleNamespace(typeDescriptor=descriptor)
        native._authority_geometry = False
        native._aim_relative_yaw = None
        native._aim_gun_pitch = None
        native.set_aim(0.25, 1.25, -0.3)

        for appearance in (fallback.appearance, native.aim):
            self.assertEqual(
                (0.4, 0.0, 0.0), appearance.turretMatrix.rotation)
            self.assertEqual(
                (0.0, 0.15, 0.0), appearance.gunMatrix.rotation)
        self.assertEqual(1.25, native.entity._aim_yaw)
        self.assertEqual(-0.3, native.entity._gun_pitch)


if __name__ == '__main__':
    unittest.main()
