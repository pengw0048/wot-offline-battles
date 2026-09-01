import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import destructibles_sensor, world_collision


class _Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            x, y, z = x.x, x.y, x.z
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y,
                       self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y,
                       self.z - other.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


def _miss_mat_info_1513(*unused):
    return False, _Vector(), _Vector(), 0, '', 0, 0


def _catalog(resources):
    return {
        'format': 'offline-lan-0922-destructible-catalog',
        'version': 1,
        'game_version': '0.9.22',
        'map': '06_ensk',
        'locator_quantization': 1000,
        'resources': resources,
    }


class _ItemMatrix(object):
    def __init__(self, translation=None):
        self.translation = translation or _Vector()

    def applyVector(self, point):
        return _Vector(point.x, point.y, point.z)

    def applyPoint(self, point):
        return self.translation + self.applyVector(point)


class WorldCollisionTests(unittest.TestCase):

    def setUp(self):
        destructibles_sensor.set_diagnostics(False)

    def tearDown(self):
        destructibles_sensor.set_event_sink(None)
        destructibles_sensor.set_diagnostics(False)
        destructibles_sensor.set_catalog(None)

    def _soft_recast_fixture(self, centers, hard_wall=None):
        """Install exact soft OBBs and a native ray that retains their skins."""
        filename = 'content/environment/test/normal/lod0/soft-item.model'
        destructibles_sensor.xrange = range
        destructibles_sensor.set_catalog(_catalog({
            filename: {
                'kind': 'fragile',
                'boxes': [[-0.4, -0.2, -0.5, 0.4, 1.5, 0.5, None]],
            },
        }))
        record = destructibles_sensor._destructible_catalog[
            'resources'][filename]
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        instances = {}
        bins = {}
        for index, center_z in enumerate(centers):
            boxes = destructibles_sensor._world_catalog_boxes(
                record, _ItemMatrix(_Vector(0.0, 0.0, center_z)),
                _Vector(), math_module)
            identity = (22, 37 + index)
            instance = {
                'filename': filename,
                'descriptor_filename': filename,
                'kind': 'fragile',
                'boxes': boxes,
                'item_scale': 1.0,
            }
            instances[identity] = instance
            destructibles_sensor._index_catalog_instance_1513(
                bins, identity, instance)
        destructibles_sensor.g_offh_destr_instances = instances
        destructibles_sensor.g_offh_destr_contact_bins = bins

        normal = _Vector(0.0, 0.0, -1.0)

        def collide(unused_space, start, end, unused_mask):
            if end.z <= start.z:
                return None
            hits = []
            for center_z in centers:
                entry = float(center_z) - 0.5
                exit_point = float(center_z) + 0.5
                if start.z <= exit_point and end.z >= entry:
                    hits.append(max(start.z, entry))
            if (hard_wall is not None and
                    start.z <= float(hard_wall) <= end.z):
                hits.append(float(hard_wall))
            if not hits:
                return None
            hit_z = min(hits)
            return (_Vector(start.x, start.y, hit_z), normal, 75)

        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = mock.Mock(side_effect=collide)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda unused: {
                'type': area.DESTR_TYPE_FRAGILE,
                'health': 5,
                'kineticDamageCorrection': 1.0,
            })
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * health)
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_fragile=mock.Mock(return_value=True))
        descriptor = _Strict1513Component(physics={'weight': 10000.0})
        return (bigworld, math_module, area, cache, authority,
                descriptor, normal)

    def _run_soft_recast(self, centers, hard_wall=None):
        (bigworld, math_module, area, cache, authority,
         descriptor, normal) = self._soft_recast_fixture(
             centers, hard_wall)
        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        collision = (_Vector(0.0, 0.7, float(centers[0]) - 0.5),
                     normal, 75)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    world_collision, '_try_destroy_solid_hit',
                    return_value=True) as destroy, \
                mock.patch.object(
                    destructibles_sensor, 'note_destroyed') as note, \
                mock.patch.object(
                    destructibles_sensor, '_publish_destroyed') as publish:
            cleared = world_collision._destroy_and_recast(
                1, start, end, collision, 0.0, 20.0, descriptor)

        destroy.assert_called_once_with(
            1, start, collision[0], normal, 0.0, 20.0, descriptor)
        authority.destroy_fragile.assert_not_called()
        note.assert_not_called()
        publish.assert_not_called()
        return cleared, bigworld.wg_collideSegment

    def _run_pending_recast(self, centers, pending_indices=(),
                            destroyed_indices=(), hard_wall=None,
                            now=100.0, deadline=100.2,
                            first_kind=None):
        """Revisit a native skin after its authoritative destroy callback."""
        (bigworld, math_module, area, cache, authority,
         descriptor, normal) = self._soft_recast_fixture(
             centers, hard_wall)
        identities = tuple((22, 37 + index)
                           for index in range(len(centers)))
        pending_identities = set(identities[index]
                                 for index in pending_indices)
        destroyed_identities = set(identities[index]
                                   for index in destroyed_indices)
        destructibles_sensor.g_offh_destr_pending = dict(
            ((identity[0], identity[1], None), float(deadline))
            for identity in pending_identities)
        authority.is_destroyed.side_effect = (
            lambda chunk_id, item_index, unused_mat_kind:
            (chunk_id, item_index) in destroyed_identities)
        bigworld.time = mock.Mock(return_value=float(now))
        if first_kind is not None:
            destructibles_sensor.g_offh_destr_instances[
                identities[0]]['kind'] = first_kind

        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        collision = (_Vector(0.0, 0.7, float(centers[0]) - 0.5),
                     normal, 75)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    world_collision, '_try_destroy_solid_hit',
                    return_value=False) as destroy, \
                mock.patch.object(
                    destructibles_sensor, 'note_destroyed') as note, \
                mock.patch.object(
                    destructibles_sensor, '_publish_destroyed') as publish:
            cleared = world_collision._destroy_and_recast(
                1, start, end, collision, 0.0, 0.0, descriptor)

        return (cleared, bigworld.wg_collideSegment, authority, destroy,
                note, publish)

    def test_destroyed_hide_skin_exact_obb_exit_can_clear_world_ray(self):
        cleared, collide = self._run_soft_recast((4.0,))

        self.assertTrue(cleared)
        self.assertEqual(2, collide.call_count)
        helper_start = collide.call_args_list[1][0][1]
        self.assertGreater(helper_start.z, 4.5)

    def test_destroyed_hide_skin_then_second_soft_prop_can_clear_world_ray(self):
        cleared, collide = self._run_soft_recast((4.0, 5.1))

        self.assertTrue(cleared)
        self.assertEqual(3, collide.call_count)
        self.assertGreater(collide.call_args_list[1][0][1].z, 4.5)
        self.assertGreater(collide.call_args_list[2][0][1].z, 5.6)

    def test_soft_chain_keeps_wall_one_centimetre_behind_second_prop_solid(self):
        cleared, collide = self._run_soft_recast(
            (4.0, 5.1), hard_wall=5.61)

        self.assertFalse(cleared)
        self.assertEqual(3, collide.call_count)
        final_recast_start = collide.call_args_list[-1][0][1].z
        self.assertGreater(final_recast_start, 5.6)
        self.assertLess(final_recast_start, 5.61)

    def test_soft_chain_over_world_recast_limit_stays_blocked(self):
        cleared, collide = self._run_soft_recast(
            (4.0, 5.1, 6.2, 7.3, 8.4))

        self.assertFalse(cleared)
        self.assertEqual(5, collide.call_count)

    def test_later_hull_lane_reuses_authority_without_second_destroy(self):
        (bigworld, math_module, area, cache, authority,
         descriptor, normal) = self._soft_recast_fixture((4.0,))
        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        collision = (_Vector(0.0, 0.7, 3.5), normal, 75)
        crush_state = [False]

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    world_collision, '_try_destroy_solid_hit',
                    return_value=True) as destroy:
            first = world_collision._destroy_and_recast(
                1, start, end, collision, 0.0, 20.0, descriptor,
                crush_state)
            second = world_collision._destroy_and_recast(
                1, start, end, collision, 0.0, 20.0, descriptor,
                crush_state)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual([True], crush_state)
        destroy.assert_called_once()
        authority.destroy_fragile.assert_not_called()

    def test_pending_hide_skin_clears_next_tick_without_repeat_destroy(self):
        (cleared, collide, authority, destroy,
         note, publish) = self._run_pending_recast(
             (4.0,), pending_indices=(0,), destroyed_indices=(0,))

        self.assertTrue(cleared)
        destroy.assert_not_called()
        authority.destroy_fragile.assert_not_called()
        note.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(1, collide.call_count)
        self.assertGreater(collide.call_args[0][1].z, 4.5)

    def test_native_horizontal_filter_skips_only_the_broken_identity(self):
        (bigworld, math_module, area, cache, authority,
         unused_descriptor, normal) = self._soft_recast_fixture((4.0,))
        authority.destroyed_keys = lambda chunk_id: (
            set([(37, None)]) if chunk_id == 22 else set())
        start = _Vector(0.0, 0.6, 0.0)
        end = _Vector(0.0, 0.6, 10.0)
        broken_hit = (_Vector(0.0, 0.6, 3.5), normal, 75)
        wall_hit = (_Vector(0.0, 0.6, 4.8), normal, 1)

        def collide(unused_space, unused_start, unused_end, unused_mask,
                    collision_filter=None):
            if collision_filter is None:
                return broken_hit
            if collision_filter(75, 0, 37, 22):
                return broken_hit
            # The same callback keeps every unrelated native identity, so the
            # engine can return a real backing wall instead of a false clear.
            self.assertTrue(collision_filter(1, 0, -1, -1))
            return wall_hit

        bigworld.wg_collideSegment = mock.Mock(side_effect=collide)
        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            result = world_collision._collide_horizontal(1, start, end)

        self.assertIs(result, wall_hit)
        self.assertEqual(5, len(bigworld.wg_collideSegment.call_args[0]))

    def test_first_destroy_recasts_with_native_filter_without_hide_delay(self):
        (bigworld, math_module, area, cache, authority,
         descriptor, normal) = self._soft_recast_fixture((4.0,))
        accepted_keys = set()
        authority.destroyed_keys = lambda chunk_id: (
            accepted_keys if chunk_id == 22 else set())
        start = _Vector(0.0, 0.6, 0.0)
        end = _Vector(0.0, 0.6, 10.0)
        collision = (_Vector(0.0, 0.6, 3.5), normal, 75)

        def accept_destroy(*unused_args):
            accepted_keys.add((37, None))
            return True

        def collide(unused_space, unused_start, unused_end, unused_mask,
                    collision_filter=None):
            self.assertIsNotNone(collision_filter)
            self.assertFalse(collision_filter(75, 0, 37, 22))
            return None

        bigworld.wg_collideSegment = mock.Mock(side_effect=collide)
        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    world_collision, '_try_destroy_solid_hit',
                    side_effect=accept_destroy) as destroy:
            cleared = world_collision._destroy_and_recast(
                1, start, end, collision, 0.0, 20.0, descriptor)

        self.assertTrue(cleared)
        destroy.assert_called_once()
        self.assertEqual(set([(37, None)]), accepted_keys)
        self.assertEqual(1, bigworld.wg_collideSegment.call_count)
        self.assertEqual(5, len(bigworld.wg_collideSegment.call_args[0]))

    def test_pending_hide_skin_cannot_skip_wall_one_centimetre_behind(self):
        (cleared, collide, authority, unused_destroy,
         unused_note, unused_publish) = self._run_pending_recast(
             (4.0,), pending_indices=(0,), destroyed_indices=(0,),
             hard_wall=4.51)

        self.assertFalse(cleared)
        authority.destroy_fragile.assert_not_called()
        self.assertEqual(1, collide.call_count)
        recast_start = collide.call_args[0][1].z
        self.assertGreater(recast_start, 4.5)
        self.assertLess(recast_start, 4.51)

    def test_broken_skin_stays_transparent_after_the_hide_window(self):
        (cleared, collide, authority, unused_destroy,
         unused_note, unused_publish) = self._run_pending_recast(
             (4.0,), pending_indices=(0,), destroyed_indices=(0,),
             now=100.2, deadline=100.2)

        self.assertTrue(cleared)
        authority.destroy_fragile.assert_not_called()
        self.assertEqual(1, collide.call_count)

    def test_broken_skin_with_kinetic_hint_never_probes_material(self):
        (bigworld, math_module, area, cache, authority,
         descriptor, normal) = self._soft_recast_fixture((4.0,))
        destructibles_sensor.g_offh_destr_pending = {
            (22, 37, None): 100.2,
        }
        authority.is_destroyed.return_value = True
        bigworld.time = mock.Mock(return_value=100.2)
        material_probe = mock.Mock(return_value=_miss_mat_info_1513())
        bigworld.wg_getMatInfoNearPoint = material_probe
        start = _Vector(0.0, 0.7, 0.0)
        end = _Vector(0.0, 0.7, 10.0)
        collision = (_Vector(0.0, 0.7, 3.5), normal, 75)

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            status = world_collision._destroy_and_recast(
                1, start, end, collision, 0.0, 1.0, descriptor,
                allow_kinetic=True, kinetic_speed=20.0)

        self.assertTrue(status)
        material_probe.assert_not_called()
        authority.destroy_fragile.assert_not_called()

    def test_world_kinetic_hint_at_hull_front_does_not_commit_catalog_prop(self):
        (bigworld, math_module, area, cache, authority,
         descriptor, normal) = self._soft_recast_fixture((4.3,))
        descriptor.hull = _Strict1513Component(
            hitTester=types.SimpleNamespace(bbox=(
                (-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None)))
        bigworld.time = mock.Mock(return_value=10.0)
        bigworld.wg_collideSegment = mock.Mock(side_effect=lambda unused_space,
            start, end, unused_mask: (
                (_Vector(start.x, start.y, 3.8), normal, 75)
                if (end.z > start.z and start.z <= 4.8 and
                    abs(start.x) < 0.5) else None))
        bigworld.wg_getMatInfoNearPoint = mock.Mock(
            return_value=_miss_mat_info_1513())

        with mock.patch.dict(
                sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                              'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority):
            world_status = world_collision._destroy_and_recast(
                1, _Vector(0.0, 0.6, -0.5),
                _Vector(0.0, 0.6, 4.0),
                (_Vector(0.0, 0.6, 3.8), normal, 75),
                0.0, 1.0, descriptor,
                allow_kinetic=True, kinetic_speed=20.0)
            detail = destructibles_sensor._catalog_motion_blocked(
                1, _Vector(), 0.0, 1.0, descriptor, 10.0,
                dt=0.04, kinetic_speed=20.0, return_detail=True,
                kinetic_commit=True)

        self.assertIn(world_status, ('kinetic', False))
        self.assertIn(detail['status'], ('approach', 'clear'))
        self.assertFalse(detail['accepted_now'])
        authority.destroy_fragile.assert_not_called()

    def test_a_felled_column_stops_blocking_the_vehicle(self):
        """Retail lets a vehicle drive over a knocked-down pole, so the
        refreshed catalog OBB must not keep acting as an obstacle."""
        (cleared, unused_collide, authority, unused_destroy,
         unused_note, unused_publish) = self._run_pending_recast(
             (4.0,), pending_indices=(0,), destroyed_indices=(0,),
             first_kind='falling')

        self.assertTrue(cleared)
        authority.destroy_fragile.assert_not_called()

    def test_a_standing_column_still_blocks_the_vehicle(self):
        (cleared, unused_collide, authority, unused_destroy,
         unused_note, unused_publish) = self._run_pending_recast(
             (4.0,), pending_indices=(), destroyed_indices=(),
             first_kind='falling')

        self.assertFalse(cleared)
        authority.destroy_fragile.assert_not_called()

    def test_active_first_contact_cannot_reach_later_pending_skin(self):
        (cleared, collide, authority, unused_destroy,
         unused_note, unused_publish) = self._run_pending_recast(
             (4.0, 5.1), pending_indices=(1,), destroyed_indices=(1,))

        self.assertFalse(cleared)
        authority.destroy_fragile.assert_not_called()
        collide.assert_not_called()

    def test_two_continuous_pending_skins_clear_through_exact_exits(self):
        (cleared, collide, authority, unused_destroy,
         unused_note, unused_publish) = self._run_pending_recast(
             (4.0, 5.0), pending_indices=(0, 1),
             destroyed_indices=(0, 1))

        self.assertTrue(cleared)
        authority.destroy_fragile.assert_not_called()
        self.assertEqual(2, collide.call_count)
        self.assertGreater(collide.call_args_list[0][0][1].z, 4.5)
        self.assertGreater(collide.call_args_list[1][0][1].z, 5.5)

    def test_native_1513_hull_uses_attributes_without_mapping_protocol(self):
        horizontal_calls = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            horizontal_calls.append((start, end))
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.8, -0.8, -3.4),
                    (1.8, 1.0, 3.4), None))))

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            descriptor, False, 0.04)

        self.assertFalse(blocked)
        self.assertTrue(horizontal_calls)

    def test_sideways_motion_sweeps_hull_width_across_front_back_lanes(self):
        horizontal_calls = []

        def collide(unused_space, start, end, unused_mask):
            horizontal_calls.append((start, end))
            return None

        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = collide
        bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=_Strict1513Component(bbox=(
                    (-1.6, -1.0, -4.0),
                    (1.6, 1.0, 6.0), None))))

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            descriptor, False, 0.04, motion_yaw=math.pi / 2.0)

        self.assertFalse(blocked)
        lower_rays = [
            (start, end) for start, end in horizontal_calls
            if abs(start.y - 0.6) < 0.001]
        self.assertEqual(3, len(lower_rays))
        for start, end in lower_rays:
            self.assertAlmostEqual(1.9, end.x)
            self.assertGreater(end.x, start.x)
            self.assertAlmostEqual(start.z, end.z)
        lanes = sorted((start.z, start.x)
                       for start, unused_end in lower_rays)
        for (actual_z, actual_x), (expected_z, expected_x) in zip(
                lanes, ((-4.0, -1.5), (0.0, -0.5), (6.0, -1.5))):
            self.assertAlmostEqual(expected_z, actual_z)
            self.assertAlmostEqual(expected_x, actual_x)

    def test_diagonal_motion_sweeps_each_projected_extreme_corner(self):
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=_Strict1513Component(bbox=(
                    (-1.6, -1.0, -4.0),
                    (1.6, 1.0, 6.0), None))))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector

        for motion_yaw, velocity in (
                (0.55, 5.0), (-0.55, 5.0),
                (1.0, 5.0), (-1.0, 5.0),
                (0.55 + math.pi, -5.0)):
            motion_x = math.sin(motion_yaw)
            motion_z = math.cos(motion_yaw)
            perp_x = motion_z
            perp_z = -motion_x
            corners = []
            for corner_x, corner_z in (
                    (-1.5, -4.0), (1.5, -4.0),
                    (1.5, 6.0), (-1.5, 6.0)):
                corners.append((
                    corner_x * motion_x + corner_z * motion_z,
                    corner_x * perp_x + corner_z * perp_z,
                    corner_x, corner_z))
            extremes = (
                min(corners, key=lambda value: value[1]),
                max(corners, key=lambda value: value[1]))
            candidates = [value for value in extremes if value[0] < -0.5]
            self.assertTrue(candidates)
            unused_u, unused_v, corner_x, corner_z = min(candidates)
            obstacle_x = corner_x + motion_x * 0.1
            obstacle_z = corner_z + motion_z * 0.1

            def collide(unused_space, start, end, unused_mask):
                delta_x = end.x - start.x
                delta_z = end.z - start.z
                length_squared = delta_x * delta_x + delta_z * delta_z
                if length_squared <= 1.0e-12:
                    return None
                progress = ((obstacle_x - start.x) * delta_x +
                            (obstacle_z - start.z) * delta_z) / length_squared
                if progress < 0.0 or progress > 1.0:
                    return None
                nearest_x = start.x + delta_x * progress
                nearest_z = start.z + delta_z * progress
                distance_squared = ((nearest_x - obstacle_x) ** 2 +
                                    (nearest_z - obstacle_z) ** 2)
                if distance_squared > 1.0e-10:
                    return None
                return (_Vector(nearest_x, start.y, nearest_z),
                        _Vector(0.0, 0.0, -1.0), 0)

            bigworld = types.ModuleType('BigWorld')
            bigworld.wg_collideSegment = collide
            bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513

            self.assertEqual(
                'hard', world_collision.check_horizontal_collision(
                    bigworld, math_module, 1, _Vector(), 0.0, velocity,
                    descriptor, False, 0.04, True,
                    commit_enabled=False, motion_yaw=motion_yaw))

    def test_diagonal_motion_lower_rays_cover_all_four_corner_paths(self):
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=_Strict1513Component(bbox=(
                    (-1.6, -1.0, -4.0),
                    (1.6, 1.0, 6.0), None))))
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector

        for velocity in (5.0, -5.0):
            for candidate_yaw in (0.55, -0.55, 1.0, -1.0):
                motion_yaw = (candidate_yaw if velocity > 0.0 else
                              candidate_yaw + math.pi)
                horizontal_calls = []

                def collide(unused_space, start, end, unused_mask):
                    horizontal_calls.append((start, end))
                    return None

                bigworld = types.ModuleType('BigWorld')
                bigworld.wg_collideSegment = collide
                bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513
                self.assertFalse(
                    world_collision.check_horizontal_collision(
                        bigworld, math_module, 1, _Vector(), 0.0, velocity,
                        descriptor, False, 0.04,
                        motion_yaw=motion_yaw))
                lower_rays = [
                    (start, end) for start, end in horizontal_calls
                    if abs(start.y - 0.6) < 0.001]
                self.assertEqual(5, len(lower_rays))
                motion_x = math.sin(motion_yaw)
                motion_z = math.cos(motion_yaw)
                for corner_x, corner_z in (
                        (-1.5, -4.0), (1.5, -4.0),
                        (1.5, 6.0), (-1.5, 6.0)):
                    target_x = corner_x + motion_x * 0.1
                    target_z = corner_z + motion_z * 0.1
                    distances = []
                    for start, end in lower_rays:
                        delta_x = end.x - start.x
                        delta_z = end.z - start.z
                        length_squared = (
                            delta_x * delta_x + delta_z * delta_z)
                        progress = ((target_x - start.x) * delta_x +
                                    (target_z - start.z) * delta_z)
                        progress = max(
                            0.0, min(1.0, progress / length_squared))
                        nearest_x = start.x + delta_x * progress
                        nearest_z = start.z + delta_z * progress
                        distances.append(
                            (nearest_x - target_x) ** 2 +
                            (nearest_z - target_z) ** 2)
                    self.assertLess(min(distances), 1.0e-10)

    def test_diagonal_drivable_profile_samples_the_hit_corner_segment(self):
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=_Strict1513Component(bbox=(
                    (-1.6, -1.0, -4.0),
                    (1.6, 1.0, 6.0), None))))
        hit_segment = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - 0.6) < 0.001 and not hit_segment:
                hit_segment.append((start, end))
                return (_Vector(
                    (start.x + end.x) * 0.5, start.y,
                    (start.z + end.z) * 0.5),
                    _Vector(0.0, 1.0, 0.0), 0)
            return None

        def ground_profile(unused_space, unused_math, unused_pos,
                           unused_x, unused_z, unused_sin, unused_cos,
                           unused_direction, look, segment_count=6):
            segment = float(look) / float(segment_count)
            return ([index * segment * 0.5
                     for index in range(segment_count + 1)], segment)

        bigworld = types.ModuleType('BigWorld')
        bigworld.wg_collideSegment = collide
        bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Vector
        with mock.patch.object(
                world_collision, '_ground_profile',
                side_effect=ground_profile) as profile:
            self.assertFalse(world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                descriptor, False, 0.04, motion_yaw=0.55))

        start, end = hit_segment[0]
        call = profile.call_args.args
        self.assertAlmostEqual(start.x, call[3])
        self.assertAlmostEqual(start.z, call[4])
        self.assertAlmostEqual(
            ((end.x - start.x) ** 2 + (end.z - start.z) ** 2) ** 0.5,
            call[8])

    def test_omitted_motion_yaw_preserves_forward_and_reverse_sweeps(self):
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=_Strict1513Component(bbox=(
                    (-1.6, -1.0, -4.0),
                    (1.6, 1.0, 6.0), None))))

        def lower_rays_for(velocity):
            horizontal_calls = []

            def collide(unused_space, start, end, unused_mask):
                horizontal_calls.append((start, end))
                return None

            bigworld = types.ModuleType('BigWorld')
            bigworld.wg_collideSegment = collide
            bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513
            math_module = types.ModuleType('Math')
            math_module.Vector3 = _Vector
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, velocity,
                descriptor, False, 0.04)
            self.assertFalse(blocked)
            lower_rays = [
                (start, end) for start, end in horizontal_calls
                if abs(start.y - 0.6) < 0.001]
            self.assertEqual(3, len(lower_rays))
            return lower_rays

        forward_rays = lower_rays_for(5.0)
        reverse_rays = lower_rays_for(-5.0)

        for start, end in forward_rays:
            self.assertAlmostEqual(-0.5, start.z)
            self.assertAlmostEqual(6.4, end.z)
            self.assertGreater(end.z, start.z)
            self.assertAlmostEqual(start.x, end.x)
        for start, end in reverse_rays:
            self.assertAlmostEqual(0.5, start.z)
            self.assertAlmostEqual(-4.4, end.z)
            self.assertLess(end.z, start.z)
            self.assertAlmostEqual(start.x, end.x)
        for rays in (forward_rays, reverse_rays):
            lane_positions = [start.x for start, unused_end in rays]
            for actual, expected in zip(lane_positions, (-1.5, 0.0, 1.5)):
                self.assertAlmostEqual(expected, actual)

    def test_slow_frame_sweep_reaches_wall_beyond_old_lookahead_cap(self):
        wall_z = [5.0]
        horizontal_ends = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            horizontal_ends.append(end.z)
            low = min(start.z, end.z)
            high = max(start.z, end.z)
            if low <= wall_z[0] <= high:
                return (_Vector(start.x, start.y, wall_z[0]),
                        _Vector(0.0, 0.0, -1.0), 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        descriptor = _Strict1513Component(
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.5, -1.0, -3.5),
                    (1.5, 1.0, 3.5), None))))

        self.assertTrue(world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 20.0,
            descriptor, False, 0.1))
        self.assertGreaterEqual(max(horizontal_ends), 5.7)

        horizontal_ends[:] = []
        wall_z[0] = 5.8
        self.assertFalse(world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 20.0,
            descriptor, False, 0.1))
        self.assertGreaterEqual(max(horizontal_ends), 5.7)

    def test_level_street_still_runs_wall_rays(self):
        calls = []

        def collide(unused_space, start, end, unused_mask):
            calls.append((start, end))
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 0.0, -1.0), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertTrue(blocked)
        self.assertTrue(any(abs(start.y - end.y) < 0.01
                            for start, end in calls))

    def test_gradually_rising_ground_remains_drivable(self):
        horizontal_calls = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, start.z * 0.10, start.z),)
            horizontal_calls.append((start, end))
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 1.0, -0.10), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertFalse(blocked)
        self.assertTrue(horizontal_calls)

    def test_gradually_descending_ground_remains_drivable(self):
        horizontal_calls = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, -start.z * 0.20, start.z),)
            horizontal_calls.append((start, end))
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 1.0, 0.20), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        for frame_rate in (30, 60, 120):
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 12.0,
                None, False, 1.0 / frame_rate)
            self.assertFalse(blocked, frame_rate)
        self.assertTrue(horizontal_calls)

    def test_steep_continuous_descent_is_drivable_but_ascent_stays_bounded(self):
        segment = 0.5
        descending = (0.0, -0.8, -1.6, -2.4, -3.2)
        ascending = tuple(reversed(descending))

        self.assertTrue(world_collision._drivable_ground_profile(
            descending, segment))
        self.assertFalse(world_collision._drivable_ground_profile(
            ascending, segment))
        self.assertFalse(world_collision._drivable_ground_profile(
            (0.0, -0.8, 0.05, -0.8, -1.6), segment))
        steep_normal = _Vector(0.0, 1.0, 1.6)
        self.assertTrue(world_collision._drivable_surface(
            (None, steep_normal),
            world_collision._MAX_DESCENDING_GRADIENT))
        self.assertFalse(world_collision._drivable_surface(
            (None, steep_normal)))

    def test_full_collision_chain_admits_steep_descent_not_same_ascent(self):
        gradient = [-1.6]

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                ground_y = gradient[0] * start.z
                if min(start.y, end.y) <= ground_y <= max(start.y, end.y):
                    return (_Vector(start.x, ground_y, start.z),)
                return None
            if abs(gradient[0]) <= 1.0e-6:
                return None
            hit_z = start.y / gradient[0]
            if min(start.z, end.z) <= hit_z <= max(start.z, end.z):
                return (_Vector(start.x, start.y, hit_z),
                        _Vector(0.0, 1.0, -gradient[0]), 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        self.assertFalse(world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 12.0,
            None, False, 0.04))
        gradient[0] = 1.6
        self.assertTrue(world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 12.0,
            None, False, 0.04))

    def test_descending_ground_does_not_hide_an_independent_wall(self):
        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, -start.z * 0.20, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 0.0, -1.0), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertTrue(blocked)

    def test_drivable_lower_slope_does_not_hide_a_wall_above_it(self):
        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, -start.z * 0.20, start.z),)
            if abs(start.y - 0.6) < 0.01:
                return (_Vector(start.x, start.y,
                                start.z + (end.z - start.z) * 0.5),
                        _Vector(0.0, 1.0, 0.20), 0)
            if abs(start.y - 1.6) < 0.01:
                return (_Vector(start.x, start.y,
                                start.z + (end.z - start.z) * 0.5),
                        _Vector(0.0, 0.0, -1.0), 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertTrue(blocked)

    def test_abrupt_downward_step_is_not_a_drivable_profile(self):
        heights = (0.0, -0.1, -0.2, -2.0, -2.1)

        self.assertFalse(world_collision._drivable_ground_profile(
            heights, 0.5))

    def test_abrupt_rising_step_stays_blocked(self):
        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                height = 2.0 if start.z >= 2.0 else 0.0
                return (_Vector(start.x, height, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 0.0, -1.0), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        blocked = world_collision.check_horizontal_collision(
            bigworld, math_module, 1, _Vector(), 0.0, 5.0,
            None, False, 0.04)

        self.assertTrue(blocked)

    def test_native_destructible_failure_is_not_silently_passable(self):
        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    _Vector(0.0, 0.0, -1.0), 0)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                world_collision, '_try_destroy_solid_hit',
                side_effect=RuntimeError('native destroy failed')):
            with self.assertRaisesRegex(RuntimeError, 'native destroy failed'):
                world_collision.check_horizontal_collision(
                    bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                    None, False, 0.04)

    def test_unidentified_low_solid_cannot_become_ghost_geometry(self):
        surface_normal = _Vector(0.0, 0.0, -1.0)

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            if abs(start.y - 0.6) < 0.01:
                return (_Vector(start.x, start.y,
                                start.z + (end.z - start.z) * 0.5),
                        surface_normal, 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                world_collision, '_try_destroy_solid_hit',
                return_value=False) as destroy:
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertTrue(blocked)
        destroy.assert_called_once()
        self.assertIs(surface_normal, destroy.call_args[0][3])
        segment_start, hit_point = destroy.call_args[0][1:3]
        self.assertAlmostEqual(0.6, segment_start.y)
        self.assertLess(segment_start.z, hit_point.z)

    def test_low_soft_contact_does_not_hide_a_farther_upper_wall(self):
        normal = _Vector(0.0, 0.0, -1.0)

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            if abs(start.y - 0.6) < 0.01:
                return (_Vector(start.x, start.y, 2.0), normal, 0)
            if abs(start.y - 1.6) < 0.01:
                return (_Vector(start.x, start.y, 3.0), normal, 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        def resolve(unused_space, start, unused_end, unused_hit,
                    unused_yaw, unused_vel, unused_td, unused_state,
                    unused_allow_kinetic, unused_kinetic_speed):
            return abs(start.y - 0.6) < 0.01

        with mock.patch.object(
                world_collision, '_destroy_and_recast',
                side_effect=resolve) as destroy:
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertTrue(blocked)
        self.assertEqual([0.6, 1.6], [
            call_args[0][1].y for call_args in destroy.call_args_list])

    def test_nearer_upper_wall_wins_before_farther_low_prop(self):
        normal = _Vector(0.0, 0.0, -1.0)

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            if abs(start.y - 0.6) < 0.01:
                return (_Vector(start.x, start.y, 3.0), normal, 0)
            if abs(start.y - 1.6) < 0.01:
                return (_Vector(start.x, start.y, 2.0), normal, 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                world_collision, '_destroy_and_recast',
                return_value=False) as destroy:
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertTrue(blocked)
        self.assertEqual(1, destroy.call_count)
        self.assertAlmostEqual(1.6, destroy.call_args[0][1].y)

    def test_upper_wall_blocks_when_lower_hull_ray_is_clear(self):
        normal = _Vector(0.0, 0.0, -1.0)

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            if abs(start.y - 1.6) < 0.01:
                return (_Vector(start.x, start.y, 2.0), normal, 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                world_collision, '_destroy_and_recast',
                return_value=False) as destroy:
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertTrue(blocked)
        destroy.assert_called_once()
        self.assertAlmostEqual(1.6, destroy.call_args[0][1].y)

    def test_lower_endpoint_does_not_hide_nearer_upper_wall(self):
        normal = _Vector(0.0, 0.0, -1.0)

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            if abs(start.y - 0.6) < 0.01:
                return (_Vector(end.x, start.y, end.z), normal, 0)
            if abs(start.y - 1.6) < 0.01:
                return (_Vector(start.x, start.y, 2.0), normal, 0)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                world_collision, '_destroy_and_recast',
                return_value=False) as destroy:
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertTrue(blocked)
        destroy.assert_called_once()
        self.assertAlmostEqual(1.6, destroy.call_args[0][1].y)

    def test_destroyed_contact_remains_blocked_until_native_ray_clears(self):
        surface_normal = _Vector(1.0, 0.0, 0.0)

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            return (_Vector(start.x, start.y,
                            start.z + (end.z - start.z) * 0.5),
                    surface_normal, 75)

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        contacts = []

        def destroy(unused_space, segment_start, hit_point, normal,
                    unused_yaw, unused_velocity, unused_descriptor):
            contacts.append((segment_start, hit_point, normal))
            return True

        with mock.patch.object(
                world_collision, '_try_destroy_solid_hit',
                side_effect=destroy):
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertTrue(blocked)
        self.assertTrue(contacts)
        self.assertTrue(all(normal is surface_normal
                            for unused_start, unused_point, normal in contacts))
        self.assertTrue(all(start.z < point.z
                            for start, point, unused_normal in contacts))

    def test_destroyed_contact_becomes_passable_only_after_native_recast(self):
        surface_normal = _Vector(0.0, 0.0, -1.0)
        horizontal_calls = [0]

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            horizontal_calls[0] += 1
            if horizontal_calls[0] == 1:
                return (_Vector(start.x, start.y,
                                start.z + (end.z - start.z) * 0.5),
                        surface_normal, 75)
            return None

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)

        with mock.patch.object(
                world_collision, '_try_destroy_solid_hit',
                return_value=True):
            blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                None, False, 0.04)

        self.assertFalse(blocked)
        self.assertGreaterEqual(horizontal_calls[0], 2)

    def test_ruinberg_fragile_truck_contact_reaches_native_authority(self):
        """Connect the exact #1513 collision and material-hit boundaries."""
        truck_filename = (
            'content/Environment/env419_OldGTruck/normal/lod0/'
            'env418_OldGMercedes_01.model')
        surface_normal = _Vector(1.0, 0.0, 0.0)
        material_calls = []
        destroyed = set()
        authority_calls = []

        solid_present = [True]

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 10.0:
                return (_Vector(start.x, 0.0, start.z),)
            if solid_present[0]:
                return (_Vector(start.x, start.y,
                                start.z + (end.z - start.z) * 0.5),
                        surface_normal, 112)
            return None

        def material_probe(unused_space, start, stop, point, unused_cb):
            material_calls.append((start, stop, point))
            # The independent centre-lane scan does not own a collision point.
            # Only the stock point-normal*3 / point+normal*2 probe identifies
            # this compiled type-2 Ruinberg prop.
            if (abs((point.x - start.x) - 3.0) < 0.001 and
                    abs((stop.x - point.x) - 2.0) < 0.001):
                return (True, point, surface_normal, 73, truck_filename,
                        37, 22)
            return _miss_mat_info_1513()

        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getMatInfoNearPoint=material_probe,
            time=lambda: 10.0)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = object()
        area.DESTR_TYPE_TREE = 1
        area.DESTR_TYPE_FALLING_ATOM = 2
        area.DESTR_TYPE_FRAGILE = 3
        area.DESTR_TYPE_STRUCTURE = 4
        area.g_cache = types.SimpleNamespace(
            unitVehicleMass=10000.0,
            getDescByFilename=lambda filename: (
                {'type': area.DESTR_TYPE_FRAGILE, 'health': 19,
                 'kineticDamageCorrection': 1.0}
                if filename == truck_filename else None))
        cache = types.ModuleType('DestructiblesCache')
        cache.scaledDestructibleHealth = (
            lambda scale, health: scale * health)
        destructibles_sensor.g_offh_destr_instances = {
            (22, 37): {'filename': truck_filename.lower(),
                       'kind': 'fragile', 'boxes': (),
                       'item_scale': 1.0},
        }
        descriptor = _Strict1513Component(
            physics={'weight': 40000.0},
            hull=_Strict1513Component(
                hitTester=types.SimpleNamespace(bbox=(
                    (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5), None))))

        def destroy_fragile(*args):
            authority_calls.append(args)
            destroyed.add((args[1], args[2]))
            return True

        authority = types.SimpleNamespace(
            is_destroyed=lambda chunk_id, item_index, *unused: (
                (chunk_id, item_index) in destroyed),
            destroy_fragile=destroy_fragile)

        with mock.patch.dict(
                sys.modules, {'AreaDestructibles': area,
                              'DestructiblesCache': cache}), \
                mock.patch.object(
                    destructibles_sensor, '_get_destr_authority',
                    return_value=authority), \
                mock.patch.object(
                    destructibles_sensor, '_event_sink',
                    lambda unused_event: True):
            first_blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                descriptor, False, 0.04)
            solid_present[0] = False
            second_blocked = world_collision.check_horizontal_collision(
                bigworld, math_module, 1, _Vector(), 0.0, 5.0,
                descriptor, False, 0.04)

        self.assertTrue(first_blocked)
        self.assertFalse(second_blocked)
        self.assertTrue(material_calls)
        self.assertEqual(1, len(authority_calls))
        self.assertEqual((1, 22, 37), authority_calls[0][:3])
        self.assertEqual(False, authority_calls[0][4])


if __name__ == '__main__':
    unittest.main()
