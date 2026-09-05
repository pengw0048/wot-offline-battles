"""Independent row-vector geometry oracle for solid-shell vehicle hits.

The production collision adapter is intentionally exercised through its public
entrypoint.  Expected component-local rays use the small affine oracle below,
not the fake Math.Matrix implementation that the production code receives.
"""

import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.entities.remote_vehicle import \
    collide_vehicle_at_matrix


EPSILON = 1.0e-8


class Vector(object):

    def __init__(self, value=(0.0, 0.0, 0.0), y=None, z=None):
        if y is not None and z is not None:
            value = (value, y, z)
        try:
            value = (value.x, value.y, value.z)
        except AttributeError:
            pass
        self.x, self.y, self.z = [float(entry) for entry in value]

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return Vector((self.x + other.x, self.y + other.y,
                       self.z + other.z))

    def __sub__(self, other):
        return Vector((self.x - other.x, self.y - other.y,
                       self.z - other.z))

    def __neg__(self):
        return Vector((-self.x, -self.y, -self.z))

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


def _identity():
    return ((1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0))


def _oracle_multiply(left, right):
    """Multiply affine matrices for points represented as row vectors."""
    return tuple(tuple(sum(left[row][mid] * right[mid][column]
                           for mid in range(4))
                       for column in range(4)) for row in range(4))


def _oracle_translation(value):
    x, y, z = value
    return ((1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (float(x), float(y), float(z), 1.0))


def _oracle_yaw(angle):
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return ((cosine, 0.0, -sine, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (sine, 0.0, cosine, 0.0),
            (0.0, 0.0, 0.0, 1.0))


def _oracle_pitch(angle):
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return ((1.0, 0.0, 0.0, 0.0),
            (0.0, cosine, -sine, 0.0),
            (0.0, sine, cosine, 0.0),
            (0.0, 0.0, 0.0, 1.0))


def _oracle_roll(angle):
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return ((cosine, -sine, 0.0, 0.0),
            (sine, cosine, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0))


def _oracle_ypr(yaw, pitch, roll):
    return _oracle_multiply(
        _oracle_multiply(_oracle_yaw(yaw), _oracle_pitch(pitch)),
        _oracle_roll(roll))


def _oracle_pose(yaw, pitch, roll, translation):
    return _oracle_multiply(_oracle_ypr(yaw, pitch, roll),
                            _oracle_translation(translation))


def _oracle_inverse_rigid(matrix):
    rotation = tuple(tuple(matrix[row][column] for row in range(3))
                     for column in range(3))
    translation = matrix[3][:3]
    inverse_translation = tuple(-sum(
        translation[row] * rotation[row][column] for row in range(3))
                                for column in range(3))
    return ((rotation[0][0], rotation[0][1], rotation[0][2], 0.0),
            (rotation[1][0], rotation[1][1], rotation[1][2], 0.0),
            (rotation[2][0], rotation[2][1], rotation[2][2], 0.0),
            (inverse_translation[0], inverse_translation[1],
             inverse_translation[2], 1.0))


def _oracle_point(matrix, point):
    x, y, z = point
    return tuple(x * matrix[0][column] + y * matrix[1][column] +
                 z * matrix[2][column] + matrix[3][column]
                 for column in range(3))


def _oracle_length(vector):
    return math.sqrt(sum(value * value for value in vector))


def _oracle_subtract(left, right):
    return tuple(left[index] - right[index] for index in range(3))


def _oracle_dot(left, right):
    return sum(left[index] * right[index] for index in range(3))


class Matrix(object):
    """Test double for the native API, deliberately separate from oracle."""

    def __init__(self, other=None):
        if isinstance(other, Matrix):
            self._values = tuple(tuple(row) for row in other._values)
            self.yaw, self.pitch, self.roll = other.yaw, other.pitch, other.roll
        else:
            self._values = _identity()
            self.yaw = self.pitch = self.roll = 0.0

    @staticmethod
    def _product(left, right):
        return tuple(tuple(sum(left[row][mid] * right[mid][column]
                               for mid in range(4))
                           for column in range(4)) for row in range(4))

    @property
    def translation(self):
        return Vector(self._values[3][:3])

    @translation.setter
    def translation(self, value):
        value = Vector(value)
        rows = [list(row) for row in self._values]
        rows[3][0:3] = (value.x, value.y, value.z)
        self._values = tuple(tuple(row) for row in rows)

    def setIdentity(self):
        self._values = _identity()
        self.yaw = self.pitch = self.roll = 0.0

    def setTranslate(self, value):
        value = Vector(value)
        self._values = ((1.0, 0.0, 0.0, 0.0),
                        (0.0, 1.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0, 0.0),
                        (value.x, value.y, value.z, 1.0))
        self.yaw = self.pitch = self.roll = 0.0

    def setRotateY(self, value):
        value = float(value)
        cosine, sine = math.cos(value), math.sin(value)
        self._values = ((cosine, 0.0, -sine, 0.0),
                        (0.0, 1.0, 0.0, 0.0),
                        (sine, 0.0, cosine, 0.0),
                        (0.0, 0.0, 0.0, 1.0))
        self.yaw, self.pitch, self.roll = value, 0.0, 0.0

    def setRotateX(self, value):
        value = float(value)
        cosine, sine = math.cos(value), math.sin(value)
        self._values = ((1.0, 0.0, 0.0, 0.0),
                        (0.0, cosine, -sine, 0.0),
                        (0.0, sine, cosine, 0.0),
                        (0.0, 0.0, 0.0, 1.0))
        self.yaw, self.pitch, self.roll = 0.0, value, 0.0

    def setRotateYPR(self, value):
        self.yaw, self.pitch, self.roll = [float(entry) for entry in value]
        yaw = Matrix()
        yaw.setRotateY(self.yaw)
        pitch = Matrix()
        pitch.setRotateX(self.pitch)
        cosine, sine = math.cos(self.roll), math.sin(self.roll)
        roll = ((cosine, -sine, 0.0, 0.0),
                (sine, cosine, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0, 1.0))
        self._values = Matrix._product(
            Matrix._product(yaw._values, pitch._values), roll)

    def postMultiply(self, other):
        self._values = Matrix._product(self._values, other._values)

    def preMultiply(self, other):
        self._values = Matrix._product(other._values, self._values)

    def invert(self):
        rotation = tuple(tuple(self._values[row][column]
                               for row in range(3))
                         for column in range(3))
        translation = self._values[3][:3]
        inverse = tuple(-sum(translation[row] * rotation[row][column]
                             for row in range(3)) for column in range(3))
        self._values = ((rotation[0][0], rotation[0][1], rotation[0][2], 0.0),
                        (rotation[1][0], rotation[1][1], rotation[1][2], 0.0),
                        (rotation[2][0], rotation[2][1], rotation[2][2], 0.0),
                        (inverse[0], inverse[1], inverse[2], 1.0))

    def applyPoint(self, value):
        value = Vector(value)
        return Vector(tuple(value[0] * self._values[0][column] +
                            value[1] * self._values[1][column] +
                            value[2] * self._values[2][column] +
                            self._values[3][column] for column in range(3)))

    def applyVector(self, value):
        value = Vector(value)
        return Vector(tuple(value[0] * self._values[0][column] +
                            value[1] * self._values[1][column] +
                            value[2] * self._values[2][column]
                            for column in range(3)))


class _PlaneHitTester(object):
    """A non-axis-aligned local plane that reports the native four-tuple."""

    def __init__(self, normal, plane_point, material_kind):
        length = _oracle_length(normal)
        self.normal = tuple(value / length for value in normal)
        self.offset = _oracle_dot(self.normal, plane_point)
        self.material_kind = material_kind
        self.calls = []

    def localHitTest(self, start, end):
        start = tuple(start)
        end = tuple(end)
        self.calls.append((start, end))
        direction = _oracle_subtract(end, start)
        denominator = _oracle_dot(self.normal, direction)
        if abs(denominator) <= EPSILON:
            return ()
        fraction = (self.offset - _oracle_dot(self.normal, start)) / denominator
        if fraction < 0.0 or fraction > 1.0:
            return ()
        distance = _oracle_length(direction) * fraction
        cosine = abs(denominator) / _oracle_length(direction)
        return ((distance, Vector(self.normal), cosine, self.material_kind),)


def _near_point(test, actual, expected):
    for index in range(3):
        test.assertAlmostEqual(expected[index], actual[index], places=7)


def _component_oracle(body, chassis, hull_offset, turret_offset, gun_offset,
                      turret_yaw, gun_pitch):
    hull = _oracle_translation(tuple(-value for value in hull_offset))
    turret = _oracle_multiply(
        _oracle_translation(tuple(-hull_offset[index] - turret_offset[index]
                                  for index in range(3))),
        _oracle_yaw(-turret_yaw))
    gun = _oracle_multiply(
        turret, _oracle_multiply(_oracle_translation(
            tuple(-value for value in gun_offset)), _oracle_pitch(-gun_pitch)))
    return (('vehicleChassis', chassis, _identity()),
            ('vehicleHull', body, hull),
            ('vehicleTurret', body, turret),
            ('vehicleGun', body, gun))


def _local_ray(root, component, start, end):
    world_to_root = _oracle_inverse_rigid(root)
    transform = _oracle_multiply(world_to_root, component)
    return (_oracle_point(transform, start), _oracle_point(transform, end))


def _point_on_ray(start, end, fraction):
    return tuple(start[index] + (end[index] - start[index]) * fraction
                 for index in range(3))


def _descriptor_for_ray(component_oracle, start, end, fractions):
    components = []
    normals = ((0.51, -0.22, 0.83), (-0.37, 0.91, 0.17),
               (0.62, 0.48, -0.59), (-0.71, 0.33, 0.62))
    materials = []
    rays = {}
    for index, (name, root, component) in enumerate(component_oracle):
        local_start, local_end = _local_ray(root, component, start, end)
        ray_point = _point_on_ray(local_start, local_end, fractions[index])
        tester = _PlaneHitTester(normals[index], ray_point, 40 + index)
        material = types.SimpleNamespace(name='material-%d' % index)
        components.append(types.SimpleNamespace(
            itemTypeName=name, hitTester=tester,
            materials={40 + index: material}))
        materials.append(material)
        rays[name] = (local_start, local_end)
    descriptor = types.SimpleNamespace(
        chassis=components[0], hull=components[1], turret=components[2],
        gun=components[3])
    descriptor.chassis.hullPosition = Vector(HULL_OFFSET)
    descriptor.hull.turretPositions = (Vector(TURRET_OFFSET),)
    descriptor.turret.gunPosition = Vector(GUN_OFFSET)
    descriptor.gun.staticTurretYaw = STATIC_TURRET_YAW
    descriptor.gun.staticPitch = STATIC_GUN_PITCH
    return descriptor, rays, tuple(materials)


HULL_OFFSET = (1.4, -0.8, 2.1)
TURRET_OFFSET = (-0.6, 1.7, 0.9)
GUN_OFFSET = (0.5, 0.4, 1.3)
STATIC_TURRET_YAW = 0.47
STATIC_GUN_PITCH = -0.28
START = (-20.0, -15.0, -25.0)
END = (28.0, 15.0, 22.0)
FRACTIONS = (0.79, 0.21, 0.52, 0.67)


class SolidCollisionOracleTests(unittest.TestCase):

    def _matrix(self, yaw, pitch, roll, translation):
        matrix = Matrix()
        matrix.setRotateYPR((yaw, pitch, roll))
        matrix.translation = Vector(translation)
        return matrix

    def _expected_collisions(self, component_oracle, fractions, materials):
        length = _oracle_length(_oracle_subtract(END, START))
        expected = []
        for index, (name, unused_root, unused_component) in enumerate(
                component_oracle):
            local_start, local_end = _local_ray(
                unused_root, unused_component, START, END)
            direction = _oracle_subtract(local_end, local_start)
            normal = ((0.51, -0.22, 0.83), (-0.37, 0.91, 0.17),
                      (0.62, 0.48, -0.59), (-0.71, 0.33, 0.62))[index]
            cosine = abs(_oracle_dot(normal, direction)) / (
                _oracle_length(normal) * _oracle_length(direction))
            expected.append((length * fractions[index], cosine,
                             materials[index], name))
        return sorted(expected, key=lambda item: item[0])

    def test_collision_uses_row_vector_component_chain_and_separate_chassis(self):
        body = _oracle_pose(0.63, -0.31, 0.22, (13.0, -4.0, 8.0))
        chassis = _oracle_pose(-0.23, 0.16, -0.19, (11.0, -4.7, 7.5))
        chain = _component_oracle(
            body, chassis, HULL_OFFSET, TURRET_OFFSET, GUN_OFFSET,
            STATIC_TURRET_YAW, STATIC_GUN_PITCH)
        descriptor, expected_rays, materials = _descriptor_for_ray(
            chain, START, END, FRACTIONS)
        vehicle = types.SimpleNamespace(
            typeDescriptor=descriptor,
            appearance=types.SimpleNamespace(
                turretMatrix=self._matrix(STATIC_TURRET_YAW, 0.0, 0.0,
                                          (0.0, 0.0, 0.0)),
                gunMatrix=self._matrix(0.0, STATIC_GUN_PITCH, 0.0,
                                       (0.0, 0.0, 0.0))))

        collisions = collide_vehicle_at_matrix(
            vehicle, self._matrix(0.63, -0.31, 0.22, (13.0, -4.0, 8.0)),
            Vector(START), Vector(END),
            types.SimpleNamespace(Vector3=Vector, Matrix=Matrix),
            chassis_matrix=self._matrix(-0.23, 0.16, -0.19,
                                        (11.0, -4.7, 7.5)))

        for component in (descriptor.chassis, descriptor.hull,
                          descriptor.turret, descriptor.gun):
            actual_start, actual_end = component.hitTester.calls[0]
            expected_start, expected_end = expected_rays[component.itemTypeName]
            _near_point(self, actual_start, expected_start)
            _near_point(self, actual_end, expected_end)
        expected = self._expected_collisions(chain, FRACTIONS, materials)
        self.assertEqual([item[3] for item in expected],
                         [item.compName for item in collisions])
        for actual, wanted in zip(collisions, expected):
            self.assertAlmostEqual(wanted[0], actual.dist, places=7)
            self.assertAlmostEqual(wanted[1], actual.hitAngleCos, places=7)
            self.assertIs(wanted[2], actual.matInfo)

        # This is a mutation-killer: swapping turret postMultiply to a
        # preMultiply changes a translated, rotated point by metres.  The
        # expected point is from the independent row-vector oracle above.
        wrong_turret = _oracle_multiply(
            _oracle_yaw(-STATIC_TURRET_YAW), _oracle_translation(
                tuple(-HULL_OFFSET[index] - TURRET_OFFSET[index]
                      for index in range(3))))
        wrong_turret_start, unused_end = _local_ray(
            body, wrong_turret, START, END)
        correct_turret_start = expected_rays['vehicleTurret'][0]
        self.assertGreater(_oracle_length(_oracle_subtract(
            correct_turret_start, wrong_turret_start)), 0.5)
        self.assertGreater(_oracle_length(_oracle_subtract(
            tuple(descriptor.turret.hitTester.calls[0][0]),
            wrong_turret_start)), 0.5)

        # Using the body base for the chassis is another plausible mutation.
        body_chassis_start, unused_end = _local_ray(
            body, _identity(), START, END)
        actual_chassis_start = descriptor.chassis.hitTester.calls[0][0]
        self.assertGreater(_oracle_length(_oracle_subtract(
            tuple(actual_chassis_start), body_chassis_start)), 0.5)

    def test_frozen_target_uses_historical_pose_and_static_angles_for_hits(self):
        body = _oracle_pose(0.63, -0.31, 0.22, (13.0, -4.0, 8.0))
        # A frozen historic pose has one body matrix.  The first tuple's root
        # therefore differs from the live hydraulic-chassis case above.
        chain = _component_oracle(
            body, body, HULL_OFFSET, TURRET_OFFSET, GUN_OFFSET,
            STATIC_TURRET_YAW, STATIC_GUN_PITCH)
        descriptor, expected_rays, materials = _descriptor_for_ray(
            chain, START, END, FRACTIONS)
        target = types.SimpleNamespace(typeDescriptor=descriptor)
        runtime = types.SimpleNamespace(
            math=types.SimpleNamespace(Vector3=Vector, Matrix=Matrix))
        battle = object.__new__(BattleRuntime)
        battle._runtime = runtime
        pose = {
            'x': 13.0, 'y': -4.0, 'z': 8.0,
            'yaw': 0.63, 'pitch': -0.31, 'roll': 0.22,
            # These values must not override the installed static gun data.
            'turret_yaw': -1.17, 'gun_pitch': 0.91,
        }

        frozen = battle._projectile_frozen_target(target, pose)
        collisions = collide_vehicle_at_matrix(
            frozen, frozen.matrix, Vector(START), Vector(END), runtime.math)

        self.assertAlmostEqual(STATIC_TURRET_YAW,
                               frozen.appearance.turretMatrix.yaw)
        self.assertAlmostEqual(STATIC_GUN_PITCH,
                               frozen.appearance.gunMatrix.pitch)
        _near_point(self, frozen.matrix.applyPoint(Vector((1.2, -0.4, 2.3))),
                    _oracle_point(body, (1.2, -0.4, 2.3)))
        for component in (descriptor.chassis, descriptor.hull,
                          descriptor.turret, descriptor.gun):
            actual_start, actual_end = component.hitTester.calls[0]
            expected_start, expected_end = expected_rays[component.itemTypeName]
            _near_point(self, actual_start, expected_start)
            _near_point(self, actual_end, expected_end)
        expected = self._expected_collisions(chain, FRACTIONS, materials)
        self.assertEqual([item[3] for item in expected],
                         [item.compName for item in collisions])


if __name__ == '__main__':
    unittest.main()
