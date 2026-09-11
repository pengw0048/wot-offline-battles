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
from unittest import mock


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

    def test_live_hydraulic_critical_rays_and_cones_use_the_armour_frames(self):
        from test_port_0922_battle_projectiles import _battle, _event, _Vector
        from gui.mods.offline_lan_0922 import (
            combat_rules, critical_damage, internal_geometry,
            internal_hit_layouts)

        body_pose = (0.63, -0.31, 0.22, (13.0, -4.0, 8.0))
        chassis_pose = (-0.23, 0.16, -0.19, (11.0, -4.7, 7.5))
        chain = _component_oracle(
            _oracle_pose(*body_pose), _oracle_pose(*chassis_pose),
            HULL_OFFSET, TURRET_OFFSET, GUN_OFFSET,
            STATIC_TURRET_YAW, STATIC_GUN_PITCH)
        descriptor, expected_rays, unused_materials = _descriptor_for_ray(
            chain, START, END, FRACTIONS)
        descriptor.isPitchHullAimingAvailable = True
        layout = {'valid': True, 'targets': [
            {'parent': name, 'entity': name}
            for name in ('chassis', 'hull', 'turret', 'gun')]}

        for mode, local in (('ap', False), ('ap', True),
                            ('he_direct', False), ('he_direct', True),
                            ('he_splash', False)):
            with self.subTest(mode=mode, local=local):
                battle, unused_bigworld = _battle()
                battle._worker_mode = True
                battle._runtime.math.Matrix = Matrix
                source = battle._server_entity(41)
                target = types.SimpleNamespace(
                    id=55, health=1000, isStarted=True,
                    isAlive=lambda: True, isTurretDetached=False,
                    typeDescriptor=descriptor, position=_Vector((11., -4.7, 7.5)),
                    matrix=self._matrix(0., 0., 0., (150., 0., 0.)),
                    model=types.SimpleNamespace(
                        matrix=self._matrix(0., 0., 0., (75., 0., 0.))),
                    appearance=types.SimpleNamespace(
                        turretMatrix=self._matrix(STATIC_TURRET_YAW, 0., 0., (0., 0., 0.)),
                        gunMatrix=self._matrix(0., STATIC_GUN_PITCH, 0., (0., 0., 0.))))
                # A stock component chain lives below its native model/filter
                # basis. Neither is the LAN body used by the armour query.
                target.getComponents = lambda: tuple(
                    (getattr(descriptor, name), Matrix(), True)
                    for name in ('chassis', 'hull', 'turret', 'gun'))
                record = {'engine_id': 55, 'network_id': 17, 'kind': 'bot',
                          'native_remote': not local, 'local': local,
                          'ready': True,
                          'state': {'health': 1000, 'alive': True}}
                battle._records = {'bot:17': record}
                battle._server_entity = lambda entity_id: (
                    target if entity_id == 55 else source)
                battle._remote_factory = types.SimpleNamespace(
                    projectile_collision_matrices=lambda *unused: (
                        self._matrix(*body_pose), self._matrix(*chassis_pose)))
                if local:
                    battle._local_matrix = self._matrix(*chassis_pose)
                    battle._local_body_pose = lambda: self._matrix(*body_pose)
                event = _event()
                if mode != 'ap':
                    event['source_shot']['shell'].update(
                        kind='HIGH_EXPLOSIVE', explosionRadius=5.)
                    event.update(is_he=True, splash_radius=5.)
                meta = battle._projectile_wire_meta(event)
                collision = types.SimpleNamespace(
                    dist=5., hitAngleCos=1., matInfo=object(), compName='vehicleHull')
                terminal = {'target_key': 'bot:17', 'collisions': [collision],
                            'query': (_Vector(START), _Vector(END)),
                            'impact': START, 'piercing_loss': 0.,
                            'penetration_factor': 1.}
                captured = {}

                def ray_interval(start, end, part):
                    captured[part['parent']] = (start, end)
                    return None

                def cone_hits(unused_layout, contexts, *args, **kwargs):
                    captured.update(contexts)
                    return ()

                def critical(vehicle, unused_layers, *args, **kwargs):
                    # Run the real proposal snapshot and interior transform
                    # consumers at the point the live fallback hands them off.
                    shadow = critical_damage._CriticalProposalVehicle(vehicle)
                    if mode == 'ap':
                        critical_damage._offh_internal_ray_hits(
                            shadow, descriptor, _Vector(START), _Vector(END))
                    else:
                        critical_damage._offh_internal_cone_hits(
                            shadow, descriptor, _Vector(START),
                            _Vector(END) - _Vector(START), {'caliber': 100.})
                    for name, unused_root, unused_component in chain:
                        parent = name[len('vehicle'):].lower()
                        expected_start, expected_end = expected_rays[name]
                        if mode == 'ap':
                            _near_point(self, captured[parent][0], expected_start)
                            _near_point(self, captured[parent][1], expected_end)
                        else:
                            _near_point(self, captured[parent]['point'], expected_start)
                            delta = _oracle_subtract(expected_end, expected_start)
                            length = _oracle_length(delta)
                            _near_point(self, captured[parent]['direction'],
                                        tuple(value / length for value in delta))
                    return 390, None, None

                math_module = types.SimpleNamespace(Matrix=Matrix, Vector3=Vector)
                with mock.patch.dict(sys.modules, {'Math': math_module}), \
                        mock.patch.object(critical_damage, '_offh_internal_layout', return_value=layout), \
                        mock.patch.object(internal_geometry, 'target_interval', side_effect=ray_interval), \
                        mock.patch.object(internal_hit_layouts, 'resolve_explosion', side_effect=cone_hits), \
                        mock.patch.object(combat_rules, 'resolve_armor_contact', return_value={'result': 2}), \
                        mock.patch.object(combat_rules, 'damage', return_value=390), \
                        mock.patch.object(critical_damage, 'propose_direct', side_effect=critical), \
                        mock.patch.object(critical_damage, 'propose_explosion', side_effect=critical):
                    if mode == 'he_splash':
                        battle._projectile_historic_pose = lambda *unused: {'x': 11., 'y': -4.7, 'z': 7.5}
                        battle._projectile_he_blast_contact = lambda *args, **kwargs: {
                            'damage': 390, 'collisions': [collision],
                            'point': START, 'direction': _oracle_subtract(END, START)}
                        effects = battle._projectile_splash_effects(
                            meta, START, None, state={'cursor_time': 0.})
                        self.assertEqual(1, len(effects))
                    else:
                        effect = battle._projectile_direct_effect(
                            meta, {'start': START, 'distance': 5.}, terminal)
                        self.assertEqual(390, effect['damage'])

    def test_missing_live_critical_body_does_not_invent_interior_geometry(self):
        from test_port_0922_battle_projectiles import _battle, _Vector
        from gui.mods.offline_lan_0922 import critical_damage

        battle, unused_bigworld = _battle()
        battle._runtime.math.Matrix = mock.Mock(
            side_effect=AssertionError('missing body must not become identity'))
        battle._projectile_vehicle_matrices = lambda *unused: (None, None)
        target = types.SimpleNamespace(
            id=55, health=500, position=_Vector(), matrix=object(),
            typeDescriptor=types.SimpleNamespace(),
            getComponents=mock.Mock(side_effect=AssertionError('stale native frame')))
        layout = {'valid': True, 'targets': [{'parent': 'hull'}]}
        for record in ({'native_remote': True}, {'local': True}):
            with self.subTest(record=record):
                proxy = battle._projectile_live_critical_target(record, target)
                shadow = critical_damage._CriticalProposalVehicle(proxy)
                with mock.patch.object(critical_damage, '_offh_internal_layout', return_value=layout):
                    self.assertEqual([], critical_damage._offh_internal_ray_hits(
                        shadow, target.typeDescriptor, _Vector(), _Vector((1., 0., 0.))))
                    self.assertEqual([], critical_damage._offh_internal_cone_hits(
                        shadow, target.typeDescriptor, _Vector(), _Vector((1., 0., 0.)),
                        {'caliber': 100.}))

    def test_historical_critical_chassis_keeps_its_frozen_body_frame(self):
        from test_port_0922_battle_projectiles import _battle

        battle, unused_bigworld = _battle()
        battle._runtime.math.Matrix = Matrix
        pose_args = (0.63, -0.31, 0.22, (13.0, -4.0, 8.0))
        body = _oracle_pose(*pose_args)
        chain = _component_oracle(
            body, body, HULL_OFFSET, TURRET_OFFSET, GUN_OFFSET,
            STATIC_TURRET_YAW, STATIC_GUN_PITCH)
        descriptor, expected_rays, unused_materials = _descriptor_for_ray(
            chain, START, END, FRACTIONS)
        source = types.SimpleNamespace(
            typeDescriptor=descriptor, isTurretDetached=False,
            matrix=self._matrix(0., 0., 0., (150., 0., 0.)))
        pose = dict(zip(('yaw', 'pitch', 'roll'), pose_args[:3]))
        pose.update(zip(('x', 'y', 'z'), pose_args[3]))
        frozen = battle._projectile_frozen_target(source, pose)
        inverse = Matrix(frozen.matrix)
        inverse.invert()
        for component, matrix, attached in frozen.getComponents():
            self.assertTrue(attached)
            start = matrix.applyPoint(inverse.applyPoint(Vector(START)))
            end = matrix.applyPoint(inverse.applyPoint(Vector(END)))
            expected_start, expected_end = expected_rays[component.itemTypeName]
            _near_point(self, tuple(start), expected_start)
            _near_point(self, tuple(end), expected_end)

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

    def test_a_detached_turret_leaves_no_armour_above_the_wreck(self):
        """Exact #1513 skips an unattached component before the hit tester.

        ``Vehicle.getComponents`` publishes ``not self.isTurretDetached`` as
        the turret and gun attachment bit and ``Vehicle.__collideSegment``
        starts its loop with ``if not isAttached: continue``.  Without that,
        an ammo-bay wreck keeps a full-armour turret and gun hanging in the
        air above a hull that no longer has either.
        """
        body = _oracle_pose(0.63, -0.31, 0.22, (13.0, -4.0, 8.0))
        chain = _component_oracle(
            body, body, HULL_OFFSET, TURRET_OFFSET, GUN_OFFSET,
            STATIC_TURRET_YAW, STATIC_GUN_PITCH)
        descriptor, unused_rays, unused_materials = _descriptor_for_ray(
            chain, START, END, FRACTIONS)
        appearance = types.SimpleNamespace(
            turretMatrix=self._matrix(STATIC_TURRET_YAW, 0.0, 0.0,
                                      (0.0, 0.0, 0.0)),
            gunMatrix=self._matrix(0.0, STATIC_GUN_PITCH, 0.0,
                                   (0.0, 0.0, 0.0)))
        math_module = types.SimpleNamespace(Vector3=Vector, Matrix=Matrix)
        matrix = self._matrix(0.63, -0.31, 0.22, (13.0, -4.0, 8.0))

        attached = types.SimpleNamespace(
            typeDescriptor=descriptor, appearance=appearance,
            isTurretDetached=False)
        before = collide_vehicle_at_matrix(
            attached, matrix, Vector(START), Vector(END), math_module)
        self.assertEqual(
            ['vehicleChassis', 'vehicleGun', 'vehicleHull', 'vehicleTurret'],
            sorted(item.compName for item in before))

        for component in (descriptor.chassis, descriptor.hull,
                          descriptor.turret, descriptor.gun):
            del component.hitTester.calls[:]
        detached = types.SimpleNamespace(
            typeDescriptor=descriptor, appearance=appearance,
            isTurretDetached=True)
        after = collide_vehicle_at_matrix(
            detached, matrix, Vector(START), Vector(END), math_module)

        self.assertEqual(
            ['vehicleChassis', 'vehicleHull'],
            sorted(item.compName for item in after))
        # The hull below the ring is untouched: only the thrown half stops
        # answering, and it stops answering before the ray is even built.
        self.assertTrue(descriptor.hull.hitTester.calls)
        self.assertEqual([], descriptor.turret.hitTester.calls)
        self.assertEqual([], descriptor.gun.hitTester.calls)

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
