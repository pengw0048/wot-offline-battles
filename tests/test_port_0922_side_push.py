"""A held traverse motor can move a lighter hull without free yaw overlap."""
import unittest

from test_port_0922_tank_collision import _tank, tank_collision as contact
from gui.mods.offline_lan_0922 import vehicle_physics as drive
import test_port_0922_bot_runtime as bots


class SustainedSidePushTests(unittest.TestCase):
    def _push(self, mass, horsepower, peer_mass, hz, sign=1., actor=1):
        dt = 1. / hz
        params = dict(drive._DEFAULTS, mass=mass,
                      powerW=horsepower*735.49875, rotSpd=.4)
        a = _tank(actor, 0., 0., mass=mass)
        b = _tank(actor+1, 3., 0., mass=peer_mass)
        for unused in range(4*hz):
            a['traverse_speed'], a['traverse_torque'] = drive.contact_traverse(
                params, 1.5, 0., sign, dt)
            step = a['traverse_speed']*dt
            a['yaw'] += step*contact.rotation_fraction(
                (a['x'], 0., a['z']), a['yaw'], a['yaw']+step, a['shape'], [b])
            for body in (a, b):
                body['contact_decel'] = drive.contact_push_decel(
                    dict(params, mass=body['mass']), False)
            impulses = contact.traverse_impulses([a, b], dt)
            for body in (a, b):
                dvx, dvz = impulses[body['id']]
                body['vx'], body['vz'] = drive.contact_push_step(
                    dict(params, mass=body['mass']), body['vx']+dvx,
                    body['vz']+dvz, body['yaw'], dt)
                body['x'] += body['vx']*dt
                body['z'] += body['vz']*dt
            overlap = contact.obb_contact(
                a['x'], a['z'], a['yaw'], a['shape'],
                b['x'], b['z'], b['yaw'], b['shape'])
            self.assertTrue(overlap is None or overlap[2] <= contact.POSITION_SLOP+1e-5)
        return b['x']-3., a['yaw']

    def test_ordinary_heavy_turns_open_a_side_hug_at_each_tick_rate(self):
        distances = []
        for hz in (15, 30, 60):
            for sign in (-1., 1.):
                for actor in (1, 1000001):
                    distance, yaw = self._push(130000., 1200., 25000., hz, sign, actor)
                    self.assertGreater(distance, 1.)
                    self.assertLess(distance, 4.)
                    self.assertGreater(yaw*sign, .25)
                    distances.append(distance)
        self.assertLess(max(distances)/min(distances), 1.2)

    def test_lighter_motor_or_disabled_engine_cannot_walk_the_heavier_hull(self):
        for hz in (15, 30, 60):
            for mass, power, peer in ((30000., 1000., 130000.),
                                      (130000., 20., 25000.),
                                      (130000., 0., 25000.)):
                distance, yaw = self._push(mass, power, peer, hz)
                self.assertEqual(0., distance)
                self.assertLess(abs(yaw), .004)


class SidePushWorldBoundaryTests(unittest.TestCase):
    setUp = bots.ShovedWreckTests.setUp
    tearDown = bots.ShovedWreckTests.tearDown
    _runtime = bots.ShovedWreckTests._runtime

    def _side_contact(self, clear=True, ground=0., wreck=False):
        runtime = self._runtime(clear=clear, ground=ground)
        pusher, peer = runtime.states[11], runtime.states[12]
        shape = (1.5, 3.5, -.8, 2.)
        for state, x, mass, power, turn in (
                (pusher, 0., 130000., 1200., 1),
                (peer, 3., 25000., 300., 0)):
            state.update(x=x, y=0., z=0., yaw=0., speed=0., pitch=0., roll=0.,
                         mass=mass, half_width=shape[0], half_length=shape[1],
                         collision_shape=shape, alive=True, team=1,
                         push_x=0., push_z=0., rotation_dir=turn, movement_dir=0)
            runtime._physics_params_for(state['id']).update(
                mass=mass, powerW=power*735.49875, rotSpd=.4)
        if wreck:
            peer.update(alive=False, health=0)
        for frame in range(5):
            runtime._resolve_tank_contacts([], frame*.04, .04)
        return peer

    def test_angular_push_reaches_the_worker_integrator(self):
        peer = self._side_contact()
        self.assertGreater(peer['x'], 3.)
        self.assertEqual(0., peer['y'])

    def test_world_wall_still_vetoes_the_same_angular_push(self):
        peer = self._side_contact(clear=False)
        self.assertEqual(3., peer['x'])
        self.assertEqual(0., peer['push_x'])

    def test_wreck_push_cannot_settle_through_a_bridge_into_missing_support(self):
        peer = self._side_contact(ground=-40., wreck=True)
        self.assertEqual(3., peer['x'])
        self.assertEqual(0., peer['y'])
        self.assertEqual(0., peer['push_x'])
