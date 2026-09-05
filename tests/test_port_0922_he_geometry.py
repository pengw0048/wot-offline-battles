import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.entities.remote_vehicle import (  # noqa: E402
    vehicle_blast_probe_points_at_matrix)


class _Vector(object):

    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            try:
                x, y, z = x[0], x[1], x[2]
            except (TypeError, IndexError):
                x, y, z = x.x, x.y, x.z
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __neg__(self):
        return _Vector(-self.x, -self.y, -self.z)


class _Matrix(object):
    """Small rigid Math.Matrix fake with the transforms used by this helper."""

    def __init__(self, other=None):
        if other is None:
            self._rotation = ((1.0, 0.0, 0.0),
                              (0.0, 1.0, 0.0),
                              (0.0, 0.0, 1.0))
            self.translation = _Vector()
        else:
            self._rotation = tuple(tuple(row) for row in other._rotation)
            self.translation = _Vector(other.translation)
        self.yaw = getattr(other, 'yaw', 0.0)
        self.pitch = getattr(other, 'pitch', 0.0)

    @staticmethod
    def _multiply(left, right):
        return tuple(tuple(sum(left[row][mid] * right[mid][column]
                               for mid in range(3))
                           for column in range(3))
                     for row in range(3))

    @staticmethod
    def _apply(rotation, point):
        return _Vector(
            sum(rotation[0][axis] * (point.x, point.y, point.z)[axis]
                for axis in range(3)),
            sum(rotation[1][axis] * (point.x, point.y, point.z)[axis]
                for axis in range(3)),
            sum(rotation[2][axis] * (point.x, point.y, point.z)[axis]
                for axis in range(3)))

    def setIdentity(self):
        self.__init__()

    def setTranslate(self, value):
        self._rotation = ((1.0, 0.0, 0.0),
                          (0.0, 1.0, 0.0),
                          (0.0, 0.0, 1.0))
        self.translation = _Vector(value)

    def setRotateY(self, value):
        cosine = math.cos(value)
        sine = math.sin(value)
        self._rotation = ((cosine, 0.0, sine),
                          (0.0, 1.0, 0.0),
                          (-sine, 0.0, cosine))
        self.translation = _Vector()
        self.yaw = float(value)

    def setRotateX(self, value):
        cosine = math.cos(value)
        sine = math.sin(value)
        self._rotation = ((1.0, 0.0, 0.0),
                          (0.0, cosine, -sine),
                          (0.0, sine, cosine))
        self.translation = _Vector()
        self.pitch = float(value)

    def postMultiply(self, other):
        self.translation = self._apply(self._rotation, other.translation) + \
            self.translation
        self._rotation = self._multiply(self._rotation, other._rotation)

    def preMultiply(self, other):
        self.translation = self._apply(other._rotation, self.translation) + \
            other.translation
        self._rotation = self._multiply(other._rotation, self._rotation)

    def invert(self):
        rotation = tuple(tuple(self._rotation[column][row]
                               for column in range(3)) for row in range(3))
        self.translation = -self._apply(rotation, self.translation)
        self._rotation = rotation

    def applyPoint(self, point):
        return self._apply(self._rotation, _Vector(point)) + self.translation


class _Tester(object):

    def __init__(self, lower, upper, callable_test=True):
        self.bbox = (_Vector(lower), _Vector(upper), None)
        if callable_test:
            self.localHitTest = self._local_hit_test

    def _local_hit_test(self, unused_start, unused_end):
        raise AssertionError('blast candidate generation must not ray-test')


def _component(name, lower=None, upper=None, factor=1.0,
               callable_test=True, armor=0.0):
    tester = None if lower is None else _Tester(lower, upper, callable_test)
    return types.SimpleNamespace(
        itemTypeName=name, hitTester=tester,
        materials={1: types.SimpleNamespace(
            armor=armor, vehicleDamageFactor=factor)})


def _vehicle(chassis, hull, turret, gun, hull_offset=(0.0, 0.0, 0.0),
             turret_offset=(0.0, 0.0, 0.0), gun_offset=(0.0, 0.0, 0.0),
             turret_yaw=0.0, gun_pitch=0.0):
    chassis.hullPosition = _Vector(hull_offset)
    hull.turretPositions = (_Vector(turret_offset),)
    turret.gunPosition = _Vector(gun_offset)
    descriptor = types.SimpleNamespace(
        chassis=chassis, hull=hull, turret=turret, gun=gun)
    appearance = types.SimpleNamespace(
        turretMatrix=_pose_matrix(turret_yaw), gunMatrix=_pitch_matrix(gun_pitch))
    return types.SimpleNamespace(typeDescriptor=descriptor, appearance=appearance)


def _pose_matrix(yaw):
    matrix = _Matrix()
    matrix.setRotateY(yaw)
    return matrix


def _pitch_matrix(pitch):
    matrix = _Matrix()
    matrix.setRotateX(pitch)
    return matrix


def _xyz(point):
    return (round(point.x, 9), round(point.y, 9), round(point.z, 9))


class HEBlastGeometryTest(unittest.TestCase):

    def setUp(self):
        self.math = types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix)

    def _empty_component(self, name):
        return _component(name, None, None)

    def test_rotated_offset_hull_uses_surface_inside_radius_not_vehicle_origin(self):
        chassis = self._empty_component('vehicleChassis')
        hull = _component('vehicleHull', (0.0, 0.0, 0.0), (2.0, 2.0, 2.0),
                          armor=0.0)
        vehicle = _vehicle(
            chassis, hull, self._empty_component('vehicleTurret'),
            self._empty_component('vehicleGun'), hull_offset=(1.0, 0.0, 0.0))
        vehicle_matrix = _pose_matrix(math.pi / 2.0)
        vehicle_matrix.translation = _Vector(10.0, 2.0, -4.0)
        burst = _Vector(11.1, 3.0, -5.0)

        points = vehicle_blast_probe_points_at_matrix(
            vehicle, vehicle_matrix, burst, 0.25, self.math)

        self.assertIn((11.0, 3.0, -5.0), tuple(_xyz(point) for point in points))
        self.assertGreater(
            math.sqrt((burst.x - vehicle_matrix.translation.x) ** 2 +
                      (burst.y - vehicle_matrix.translation.y) ** 2 +
                      (burst.z - vehicle_matrix.translation.z) ** 2), 0.25)

    def test_turret_offset_and_chassis_matrix_keep_their_distinct_roots(self):
        chassis = _component('vehicleChassis', (0.0, 0.0, 0.0),
                             (1.0, 1.0, 1.0))
        hull = self._empty_component('vehicleHull')
        turret = _component('vehicleTurret', (0.0, 0.0, 0.0),
                            (1.0, 1.0, 1.0))
        vehicle = _vehicle(
            chassis, hull, turret, self._empty_component('vehicleGun'),
            hull_offset=(1.0, 0.0, 0.0), turret_offset=(0.0, 2.0, 0.0),
            turret_yaw=0.5)
        body = _Matrix()
        body.translation = _Vector(10.0, 0.0, 0.0)
        chassis_root = _Matrix()
        chassis_root.translation = _Vector(-10.0, 0.0, 0.0)

        turret_points = vehicle_blast_probe_points_at_matrix(
            vehicle, body, _Vector(11.4, 2.5, 0.4), 0.2, self.math,
            chassis_matrix=chassis_root)
        chassis_points = vehicle_blast_probe_points_at_matrix(
            vehicle, body, _Vector(-9.9, 0.5, 0.5), 0.2, self.math,
            chassis_matrix=chassis_root)

        self.assertIn((11.389342956, 2.5, 0.380492411),
                      tuple(_xyz(point) for point in turret_points))
        self.assertIn((-10.0, 0.5, 0.5),
                      tuple(_xyz(point) for point in chassis_points))

    def test_external_only_missing_bbox_and_noncallable_tester_do_not_produce_points(self):
        chassis = _component('vehicleChassis', (0.0, 0.0, 0.0),
                             (1.0, 1.0, 1.0), factor=0.0)
        hull = _component('vehicleHull', (0.0, 0.0, 0.0),
                          (1.0, 1.0, 1.0), callable_test=False)
        turret = self._empty_component('vehicleTurret')
        gun = _component('vehicleGun', (0.0, 0.0, 0.0), (1.0, 1.0, 1.0),
                         factor=0.0)
        vehicle = _vehicle(chassis, hull, turret, gun)

        points = vehicle_blast_probe_points_at_matrix(
            vehicle, _Matrix(), _Vector(0.5, 0.5, 0.5), 1.0, self.math)

        self.assertEqual((), points)

    def test_zero_armour_structural_bbox_is_kept_and_candidates_are_bounded(self):
        hull = _component('vehicleHull', (-1.0, -1.0, -1.0),
                          (2.0, 3.0, 4.0), armor=0.0, factor=1.0)
        vehicle = _vehicle(
            self._empty_component('vehicleChassis'), hull,
            self._empty_component('vehicleTurret'),
            self._empty_component('vehicleGun'))

        points = vehicle_blast_probe_points_at_matrix(
            vehicle, _Matrix(), _Vector(0.23, 0.41, 0.67), 1.0, self.math)

        self.assertEqual(13, len(points))
        self.assertEqual(len(points), len(set(_xyz(point) for point in points)))

    def test_bbox_lower_distance_rejects_out_of_radius_component(self):
        hull = _component('vehicleHull', (10.0, 0.0, 0.0),
                          (12.0, 2.0, 2.0))
        vehicle = _vehicle(
            self._empty_component('vehicleChassis'), hull,
            self._empty_component('vehicleTurret'),
            self._empty_component('vehicleGun'))

        points = vehicle_blast_probe_points_at_matrix(
            vehicle, _Matrix(), _Vector(0.0, 1.0, 1.0), 9.99, self.math)

        self.assertEqual((), points)


if __name__ == '__main__':
    unittest.main()
