"""Physical contact survives the asynchronous human/worker boundary."""
import math
import unittest

from test_port_0922_tank_collision import tank_collision, _tank
from gui.mods.offline_lan_0922 import tank_contact_ledger as ledger
from gui.mods.offline_lan_0922 import bot_state_codec
import test_port_0922_bot_runtime as bot_tests


class ContactLedgerTests(unittest.TestCase):
    def test_pending_separation_cannot_open_space_inside_a_presented_bot(self):
        import types
        from unittest import mock
        import test_port_0922_battle_runtime as t
        for bot_mass in (31370., 12495.):
            for hz in (25, 60, 144):
                with self.subTest(bot=bot_mass, hz=hz):
                    runtime = t._runtime()
                    battle = t.BattleRuntime(runtime)
                    battle.client = t._Client()
                    battle._avatar = runtime.bigworld.avatar
                    battle._local_physics = dict(t._effective_params_snapshot()['physics'], mass=100575.)
                    local = t._Vehicle(10, t._Descriptor(), t._Vector(), (0,0,0), {'health':500})
                    remote = t._Vehicle(11, t._Descriptor(), t._Vector(0,0,6), (0,0,0), {'health':500})
                    runtime.bigworld.entities[11] = remote
                    shape = (1.5,3.5,-.8,2.)
                    state = dict(id=11, x=0., y=0., z=6., yaw=0., speed=0.,
                                 alive=True, team=1, mass=bot_mass, collision_shape=shape)
                    battle._records = {'bot:11': dict(engine_id=11, network_id=11,
                        kind='bot', local=False, ready=True, tombstone=False,
                        state=state, presented_pose=dict(state))}
                    battle._bots = types.SimpleNamespace(states={11:state},
                        replica_contact_params=lambda raw, descriptor: dict(
                            t.battle_runtime_module.vehicle_physics.derive_params(descriptor),
                            mass=bot_mass))
                    battle._collision_shape = lambda unused: shape
                    battle._motion_is_clear = lambda *args, **kw: True
                    battle._baked_pose_safe = lambda *args: True
                    battle._poll_local_ram_contact_episodes = lambda *args: None
                    position = (0.,0.,0.)
                    # The old version treated this unaccepted request as
                    # space, even when a world collision held the receiver.
                    battle._local_contact_pushes[11] = [11,1,0.,0.,0.,20., 0.0]
                    with mock.patch('sys.stdout'):
                        # A 200-ms worker/snapshot gap contains many render
                        # frames, but the displayed hull remains occupied.
                        for unused in range(int(.2*hz)):
                            before = position
                            position = battle._resolve_local_tank_contacts(local,position,0.,1./hz)
                            self.assertLess(position[2], before[2])
                        # Ownership no longer assigns the entire separation
                        # to the local tank. A fixed snapshot converges at
                        # the true mass share; inward travel stays blocked
                        # throughout, even before that recovery finishes.
                        own = _tank(-1, 0., position[2], mass=100575.)
                        peer = _tank(11, 0., 6., mass=bot_mass)
                        self.assertEqual(0., tank_collision.translation_fraction(
                            own, (0., 20.), [peer]))
                        checkpoint = battle._local_contact_pushes[11]
                        self.assertEqual(20.,checkpoint[5])
                        state['z'] += 20.
                        state['contact_push_acks'] = [[battle.client.player_id]+checkpoint[1:]]
                        # ACK and canonical pose arrive before the renderer
                        # catches up. They do not clear its occupied space.
                        after = battle._resolve_local_tank_contacts(local,position,0.,1./hz)
                    self.assertLessEqual(after[2],position[2])
                    self.assertGreaterEqual(after[2],-.99)

    def test_mass_weighted_reciprocal_impulse_survives_a_late_worker(self):
        for human_mass, bot_mass in ((10000, 100000), (100000, 10000), (25000, 25000)):
            with self.subTest(human=human_mass, bot=bot_mass):
                human = _tank(1001, 0, 0, mass=human_mass, vz=10)
                bot = _tank(11, 0, 5, mass=bot_mass)
                result = tank_collision.resolve_tank(human, [bot])
                bot_delta = result['responses'][0][1]
                human_delta = result['delta_velocity']
                self.assertAlmostEqual(0, human_mass * human_delta[1] + bot_mass * bot_delta[1])
                self.assertAlmostEqual(human_mass * 10 / (human_mass + bot_mass), bot_delta[1])
                sent = {}
                ledger.record(sent, 11, (bot_delta[0] * bot_mass, bot_delta[1] * bot_mass))
                # While the worker has not seen the frame, its presented Bot
                # already carries the pending reciprocal velocity locally.
                human['vz'] += human_delta[1]
                bot['vz'] += ledger.pending(sent, 11, [], 1)[1] / bot_mass
                again = tank_collision.resolve_tank(human, [bot])
                self.assertAlmostEqual(0, again['delta_velocity'][1])

    def test_coalescing_retries_and_reordering_preserve_only_unseen_momentum(self):
        sent = {}
        ledger.record(sent, 11, (1, 2))
        first = list(sent[11])
        ledger.record(sent, 11, (-0.5, 3))
        latest = sent[11]
        self.assertEqual((0.5, 5), ledger.unseen(latest, None))
        self.assertEqual((-0.5, 3), ledger.unseen(latest, first))
        self.assertEqual((0, 0), ledger.unseen(first, latest))
        self.assertEqual((0, 0), ledger.unseen(latest, latest))
        self.assertEqual((0, 0), ledger.pending(sent, 11, [[1] + latest[1:]], 1))

    def test_separation_survives_coalescing_without_creating_momentum(self):
        sent = {}
        ledger.record(sent, 11, (0,0), separation=(.4,-.2))
        first = list(sent[11])
        ledger.record(sent, 11, (0,0), separation=(.1,.3))
        last = sent[11]
        self.assertEqual((0.,0.),ledger.unseen(last,None))
        self.assertAlmostEqual(.1,ledger.unseen(last,first,separation=True)[0])
        self.assertAlmostEqual(.3,ledger.unseen(last,first,separation=True)[1])
        self.assertEqual((0.,0.),ledger.unseen(first,last,separation=True))
        self.assertEqual((0.,0.),ledger.unseen(last,last,separation=True))

    def test_bot_velocity_and_acknowledgement_roundtrip_atomically(self):
        state = {'id': 11, 'speed': 3, 'push_x': 2.4, 'push_z': -1.3,
                 'contact_push_acks': [[1, 8, 2.4, -1.3, .125, -.25, 0.0]]}
        decoded = bot_state_codec.decode_row(bot_state_codec.encode_row(state), {})
        for key in ('speed', 'push_x', 'push_z', 'contact_push_acks'):
            self.assertEqual(state[key], decoded[key])

    def test_malformed_checkpoint_cannot_poison_a_round(self):
        for value in (None, [[11, 1, 0, 0]],
                      [[11, 1, float('nan'), 0, 0, 0, 0.0]], [[11, True, 0, 0, 0, 0, 0.0]],
                      [[11, 1, 0, 0, 0, 0, 0.0], [11, 2, 0, 0, 0, 0, 0.0]],
                      [[11, 1, float('inf'), 0, 0, 0, 0.0]],
                      [[11, 1, 0, 0, float('nan'), 0, 0.0]],
                      [[11, 1, 0, 0, 0, float('inf'), 0.0]]):
            with self.assertRaises((ValueError, TypeError, OverflowError)):
                ledger.normalize(value)


class WorkerContactLedgerTests(unittest.TestCase):
    setUp = bot_tests.ShovedWreckTests.setUp
    tearDown = bot_tests.ShovedWreckTests.tearDown
    _runtime = bot_tests.ShovedWreckTests._runtime

    def test_dead_human_remains_solid_without_a_visible_integrator(self):
        runtime = self._runtime()
        runtime.states.pop(12)
        state = runtime.states[11]
        state.update(x=0.,y=0.,z=0.,yaw=0.,speed=0.,push_x=0.,push_z=0.)
        runtime._clear = lambda *args: True
        runtime._player_collision_profile = lambda raw: {
            'mass':100575.,'shape':tank_collision.DEFAULT_SHAPE,'ram_profile':{},
            'physics':runtime._physics_params_for(11)}
        raw=dict(id=1,team=1,x=0.,y=0.,z=-6.,yaw=0.,speed=0.,
                 alive=False,tank_pushes=[])
        runtime._resolve_tank_contacts([raw],1.,.1)
        self.assertGreater(state['z'],0.)

    def test_worker_does_not_teleport_for_a_remote_separation_checkpoint(self):
        for clear in (True,False):
            runtime = self._runtime()
            runtime.states.pop(12)
            state = runtime.states[11]
            state.update(x=0., y=0., z=0., yaw=0., speed=0., push_x=0., push_z=0.)
            runtime._clear = lambda *args: clear
            runtime._player_collision_profile = lambda raw: {
                'mass':100575.,'shape':tank_collision.DEFAULT_SHAPE,'ram_profile':{},
                'physics':runtime._physics_params_for(11)}
            raw=dict(id=1,team=1,x=0.,y=0.,z=-60.,yaw=0.,speed=0.,
                     tank_pushes=[[11,2,0.,0.,0.,.5, 0.0]])
            runtime._resolve_tank_contacts([raw],1.,.1)
            self.assertAlmostEqual(0.,state['z'])
            runtime._resolve_tank_contacts([raw],1.1,.1)
            self.assertAlmostEqual(0.,state['z'])
            self.assertEqual([[1,2,0.,0.,0.,.5, 0.0]],state['contact_push_acks'])

    def test_engine_and_mass_survive_visible_to_worker_contact(self):
        physics = self.module.vehicle_physics
        for human_mass, power_hp, bot_mass, moves in (
                (100575., 1200., 12495., True),
                (31370., 680., 100575., False),
                (100575., 1., 12495., False)):
            for dt in (.02, .1):
                with self.subTest(mass=human_mass, power=power_hp, dt=dt):
                    runtime = self._runtime()
                    runtime.states.pop(12)
                    state = runtime.states[11]
                    state.update(x=0., y=0., z=0., yaw=0., speed=0.,
                                 push_x=0., push_z=0., mass=bot_mass)
                    params = runtime._physics_params_for(11)
                    params['mass'] = bot_mass
                    human_params = dict(params, mass=human_mass,
                                        powerW=power_hp * 735.49875)
                    speed = physics.longitudinal_step(human_params, 0., 1., False, 0., dt)
                    human = _tank(-1, 0., -6.9, mass=human_mass, vz=speed, team=1)
                    bot = _tank(11, 0., 0., mass=bot_mass, team=1)
                    bot['contact_decel'] = physics.contact_push_decel(params, False)
                    result = tank_collision.resolve_pairs([human, bot], dt, anchor=-1)
                    delta = result[11]['delta_velocity']
                    sent = {}
                    ledger.record(sent, 11, (delta[0]*bot_mass, delta[1]*bot_mass))
                    runtime._player_collision_profile = lambda raw: {
                        'mass': human_mass, 'shape': tank_collision.DEFAULT_SHAPE,
                        'ram_profile': {}, 'physics': human_params}
                    # Coalescing may deliver momentum after the overlap has
                    # ended. The real worker receipt path still has to move
                    # the light body and retain the heavy body's track hold.
                    raw = dict(id=1, team=1, x=100., y=0., z=100., yaw=0.,
                               speed=0., tank_pushes=list(sent.values()))
                    runtime._consume_human_contact_pushes([raw], 1.)
                    state['z'] += state['speed']*dt
                    runtime._resolve_tank_contacts([raw], 1., dt)
                    self.assertEqual(moves, state['z'] > 0.)
                    before = state['push_z']
                    runtime._resolve_tank_contacts([raw], 1.+dt, dt)
                    self.assertLessEqual(state['push_z'], before)

    def test_receipt_moves_bot_without_a_current_overlap_or_armour_proof(self):
        runtime = self._runtime()
        state = runtime.states[11]
        state.update(x=0, y=0, z=0, yaw=0, speed=0, push_x=0, push_z=0)
        runtime.states.pop(12)
        runtime._player_collision_profile = lambda raw: {
            'mass': 100000, 'shape': tank_collision.DEFAULT_SHAPE, 'ram_profile': {},
            'physics': runtime._physics_params_for(11)}
        human = {'id': 1, 'x': 100, 'y': 0, 'z': 100, 'yaw': 0,
                 'tank_pushes': [[11, 1, 0, 4 * state['mass'], 0, 0, 0.0]], 'team': 1}
        runtime._consume_human_contact_pushes([human], 1.0)
        state['z'] += state['speed']*.1
        runtime._resolve_tank_contacts([human], 1.0, .1)
        self.assertGreater(state['z'], .3)
        self.assertEqual([[1, 1, 0., 4 * state['mass'], 0., 0., 0.0]], state['contact_push_acks'])
        speed_after = state['speed']
        runtime._resolve_tank_contacts([human], 1.1, .1)
        self.assertEqual(state['speed'], speed_after)
        self.assertEqual([[1, 1, 0., 4 * state['mass'], 0., 0., 0.0]], state['contact_push_acks'])

class ServerContactRelayTests(unittest.TestCase):
    def test_server_relays_cumulative_momentum_without_reset_on_replay(self):
        from test_port_0922_server_projectiles import _state, _gun_checkpoint
        state = _state(players=1)
        state.bot_states[11] = {'id': 11, 'alive': True}
        player = state.players[1]
        def send(rows):
            return state.update_input(1, {
                'type': 'input', 'round_id': state.round_id,
                'input_seq': player.input_processed_seq + 1,
                'pose_time_us': state._logical_motion_time_us(),
                'forward': 1, 'turn': 0, 'speed': 0,
                'x': player.x, 'y': player.y, 'z': player.z,
                'yaw': 0, 'pitch': 0, 'roll': 0, 'fire_seq': 0,
                'aim_yaw': 0, 'gun_pitch': 0,
                'shell_index': 0, 'next_shell_index': 0,
                'shell_change_pending': False,
                'gun_checkpoint': _gun_checkpoint(), 'tank_pushes': rows})
        latest = [11, 4, 150000, -50000, .2, -.3, 0.0]
        self.assertTrue(send([latest]))
        self.assertEqual([latest], state._public_player(player)['tank_pushes'])
        self.assertTrue(send([[11, 1, 30000, 0, .1, 0, 0.0]]))
        self.assertEqual([latest], state._public_player(player)['tank_pushes'])
