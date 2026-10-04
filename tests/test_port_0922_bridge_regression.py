"""Dynamic coverage for the 040026 bridge regression (synthetic geometry).

The native report does not contain continuous idle poses. This finite deck
exercises the actual player ground adapter, not a replay of that map's mesh.
"""
import math
import unittest
from unittest import mock

import test_port_0922_battle_runtime as runtime_tests


class BridgeTipRegressionTests(unittest.TestCase):
    def test_tilted_track_patch_cannot_reach_back_to_a_departed_bridge_deck(self):
        physics=runtime_tests.vehicle_physics
        params=physics.derive_suspension_params(runtime_tests._suspension_descriptor())
        for axis,pitch,roll in ((0,0.,math.pi/2),(1,math.pi/2,0.)):
            reach=params['footprint_half_width' if axis==0 else 'footprint_half_length']
            point=(reach*.75,0.) if axis==0 else (0.,reach*.75)
            spring=dict(x=0.,footprint_front=reach,footprint_rear=reach)
            queries=[]
            def deck(x,z,low,high):
                queries.append((x,z))
                return 0. if (x,z)[axis]<=0. and low<=0.<=high else None
            self.assertEqual(0.,physics.suspension_footprint_support(params,point,None,None,
                0.,deck,point_height=0.,spring=spring,reference_height=0.))
            queries[:]=[]
            self.assertIsNone(physics.suspension_footprint_support(params,point,None,None,
                0.,deck,point_height=0.,spring=spring,reference_height=0.,pitch=pitch,roll=roll))
            self.assertTrue(all(abs(probe[axis]-point[axis])<1.e-9 for probe in queries))

    def test_missing_plane_releases_a_remembered_edge_column(self):
        physics = runtime_tests.vehicle_physics
        unused, memory = physics.retained_ground_contact((0, 0), 1, None, .5)
        self.assertEqual((None, None), physics.retained_ground_contact(
            (.01, 0), None, memory, .5))
        # A proved flat plane still covers the existing short query gaps.
        self.assertEqual(1, physics.retained_ground_contact(
            (.01, 0), None, memory, .5, (0, 0))[0])

    def test_idle_tip_is_recorded_at_existing_cadence_without_native_probes(self):
        runtime = runtime_tests._runtime()
        battle = runtime_tests.BattleRuntime(runtime)
        now = [10.0]
        battle._clock = lambda: now[0]
        battle._local_support_rise_blocked = False
        battle._local_suspension_roll_velocity = 0.8
        probes = ((1.0, 2.0, -1.0, 1.0, None, None, 0.5, ()),)
        battle._local_suspension_probe_trace = probes
        args = ((0, 0, 0), (0, 0, 0), 0.02, 0.0, 'still')
        with mock.patch('sys.stdout') as output, \
                mock.patch.object(battle, '_suspension_ground_y') as native_query:
            for pitch, roll in ((0.0, 1.5), (1.5, 0.0)):
                battle._local_pitch, battle._local_roll = pitch, roll
                self.assertTrue(battle._report_local_motion_stall(*args))
                self.assertFalse(battle._report_local_motion_stall(*args))
                now[0] += 2.01
            battle._local_pitch = battle._local_roll = 0.0
            self.assertFalse(battle._report_local_motion_stall(*args))
            native_query.assert_not_called()
        text = ''.join(call.args[0] for call in output.write.call_args_list)
        self.assertEqual(2, text.count('LOCAL TILT SUPPORT'))
        self.assertIn('"roll_velocity": 0.8', text)
        self.assertIn('"spring_probes":', text)
        self.assertEqual(probes, battle._local_suspension_probe_trace)

    def test_tipped_body_does_not_keep_lifting_and_rocking_above_deck(self):
        self._fall_cases((25, 60), (-2.4, 2.4))

    def test_single_sided_support_does_not_freeze_at_high_frame_rates(self):
        # 044826: an unsupported side kept a zero angular velocity. Include
        # small per-frame impulses as well as the worker's longer slices.
        self._fall_cases((25, 60, 100, 144), (-.6, 0., .6), must_fall=True)

    def _fall_cases(self, rates, rolls, must_fall=False):
        physics = runtime_tests.vehicle_physics
        for hz in rates:
            for side in (-1.0, 1.0):
                for initial_roll in rolls:
                    with self.subTest(hz=hz, side=side, roll=initial_roll):
                        runtime = runtime_tests._runtime()
                        battle = runtime_tests.BattleRuntime(runtime)
                        battle._avatar = runtime.bigworld.avatar
                        battle._local_fall_armed = True
                        battle._local_roll = initial_roll
                        entity = runtime_tests._Vehicle(
                            10, runtime_tests._suspension_descriptor(),
                            runtime_tests._Vector(), (0, 0, 0), {'health': 500})

                        def ground(x, z, low, high, **kwargs):
                            # One finite deck edge with real missing columns.
                            # Respect the native query's vertical range.
                            limit = kwargs.get('flat_maximum_y')
                            if limit is not None:
                                high = min(high, limit)
                            return (0.0 if side * x <= 0.0 and
                                    low <= 0.0 <= high else None)

                        battle._suspension_ground_y = ground
                        params = physics.derive_suspension_params(entity.typeDescriptor)
                        posed = physics.suspension_pose_params(params, 0, initial_roll)
                        x = side * 0.1
                        offsets = [physics.suspension_point_offset(
                            point, 0.0, initial_roll) for point in posed['pseudo_contacts']]
                        # Start clear of the deck, with zero angular velocity:
                        # no deliberately intersecting hull or powered input.
                        height = max([0.0] + [-point[1] for point in offsets
                                     if side * (x + point[0]) <= 0.0])
                        position = (x, height, 0.0)
                        tail = []
                        with mock.patch('sys.stdout'):
                            for tick in range(10 * hz):
                                battle._local_support_motion_pose = position
                                position = battle._update_vertical_motion(
                                    entity, position, 0.0, 1.0 / hz)
                                self.assertIsNotNone(battle._local_suspension_params)
                                self.assertTrue(all(math.isfinite(value) for value in position))
                                if tick >= 8 * hz:
                                    tail.append((position[1], battle._local_roll))
                        # It may fall or settle on remaining body support.
                        # It must not stay in the introduced lift/recontact
                        # cycle, even when the user has released all controls.
                        self.assertLess(max(row[0] for row in tail), 0.0)
                        if must_fall:
                            self.assertLess(position[1], -10.0)
                        self.assertLess(max(row[1] for row in tail) -
                                        min(row[1] for row in tail), 0.06)


if __name__ == '__main__':
    unittest.main()
