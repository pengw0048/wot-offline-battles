"""Exercise the real old-Bot observation producer and current presentation.

Only native visibility geometry is replaced. Radio admission, the production
update, message packing, relay and presentation methods are not replaced.
"""
import copy
import unittest
from unittest import mock
import test_port_0922_bot_runtime as bot_fixture
import test_port_0922_battle_runtime as client_fixture


class Bot080RadioIntegrationTests(unittest.TestCase):
    setUp = bot_fixture.BotRuntimeTests.setUp
    tearDown = bot_fixture.BotRuntimeTests.tearDown

    def scene(self, ally_x=500.0):
        self.start['bots'] = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Ally'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Enemy'}]
        self.runtime.battle_start(self.start)
        self.runtime.states[11].update(x=ally_x, y=0.0, z=0.0)
        self.runtime.states[12].update(x=750.0, y=0.0, z=0.0)
        # Native vision boundary: only Bot 11 currently has direct sight.
        self.runtime._visible = lambda source, target, *args, **kwargs: (
            source.get('kind', 'bot') == 'bot' and source['id'] == 11)
        player = bot_fixture._admit_player({
            'id': 1, 'team': 1, 'alive': True, 'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'health': 1000})
        player['effective_params']['physics']['rotationIsAroundCenter'] = True
        self.players = [player]
        return player

    def observation(self, now=1.0):
        messages = self.runtime.update(0.04, now, players=self.players)
        return next(m for m in messages if m['type'] == 'bot_observation')

    def enemy_contact(self, message):
        return next(c for c in message['contacts']
                    if c['observing_team'] == 1 and c['target_kind'] == 'bot'
                    and c['target_id'] == 12)

    def client(self, distance=500.0):
        f = client_fixture
        runtime = f._runtime()
        battle = f.BattleRuntime(runtime)
        battle.client = f._Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._local_position = (0.0, 0.0, 0.0)
        ally = f.RemoteVehicle(1000, f._Descriptor(), {
            'publicInfo': {'team': 1, 'name': 'Ally'}, 'health': 500,
            'isCrewActive': True, 'gunAnglesPacked': 0},
            f._Vector(distance, 0.0, 0.0), (0.0, 0.0, 0.0), runtime.math)
        ally.model = f._Model(); ally.appearance.attach(ally.model)
        ally.isStarted = True; ally.inWorld = True
        runtime.bigworld.entities[1000] = ally
        battle._remote_factory = f.types.SimpleNamespace(get=lambda ident: ally)
        battle._spotting_observers = lambda: (((0, 0, 0), f._Descriptor(), None),)
        battle._spot_line_of_sight = lambda *a, **kw: False
        record = {'engine_id': 1000, 'kind': 'bot', 'network_id': 11,
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'native_remote': False,
            'world_marker_started': False, 'minimap_started': False,
            'spot_visible': False, 'spot_marker_visible': False,
            'state': {'team': 1, 'health': 500, 'alive': True}}
        battle._records = {
            'player:1': {'local': True, 'state': {'team': 1, 'alive': True}},
            'bot:11': record,
            'bot:12': {'kind': 'bot', 'network_id': 12, 'state': {'team': 2},
                       'presentation': False, 'spot_until': 0.0}}
        return battle, record, ally

    def test_public_update_emits_links_outside_own_445_view(self):
        self.scene()
        message = self.observation()
        self.assertIn('radio_links', message)
        own = next(row for row in message['radio_links'] if row['id'] == 1)
        self.assertEqual([{'kind': 'bot', 'id': 11}], own['allies'])

    def test_teammate_spot_has_a_local_recipient_without_self_spot(self):
        self.scene()
        message = self.observation()
        contact = self.enemy_contact(message)
        self.assertNotIn(1, contact['visible_by_player_ids'])
        self.assertTrue(any(row['kind'] == 'human' and row['id'] == 1
                            and row['time_left'] > 0
                            for row in contact.get('radio_recipients', [])))
        battle, _, _ = self.client()
        battle._apply_team_observation(message, 1.0)
        self.assertGreater(battle._records['bot:12']['radio_spot_until'], 1.0)

    def test_producer_link_keeps_ally_model_without_direct_sight(self):
        self.scene()
        message = self.observation()
        battle, record, _ = self.client(500.0)
        battle._apply_team_observation(message, 1.0)
        battle._update_spotting(1.0)
        self.assertTrue(record['spot_visible'])
        self.assertTrue(record['spot_marker_visible'])

    def test_radio_does_not_override_render_distance(self):
        self.scene(600.0)
        message = self.observation()
        battle, record, _ = self.client(600.0)
        battle._apply_team_observation(message, 1.0)
        battle._update_spotting(1.0)
        self.assertFalse(record['spot_visible'])
        self.assertTrue(record['spot_marker_visible'])

    def test_lost_radio_clears_ally_and_enemy_receipts(self):
        self.scene()
        battle, record, _ = self.client()
        battle._apply_team_observation(self.observation(), 1.0)
        self.assertIn(('bot', 11), battle._radio_ally_contacts)
        self.runtime.states[11]['x'] = 2000.0
        message = self.observation(2.0)
        own = next(row for row in message['radio_links'] if row['id'] == 1)
        self.assertEqual([], own['allies'])
        battle._apply_team_observation(message, 2.0)
        self.assertNotIn(('bot', 11), battle._radio_ally_contacts)
        self.assertEqual(2.0, battle._records['bot:12']['radio_spot_until'])

    def test_never_seen_enemy_is_not_revealed_by_radio(self):
        self.scene()
        self.runtime._visible = lambda *a, **kw: False
        message = self.observation()
        self.assertIn('radio_links', message)
        self.assertFalse(self.enemy_contact(message)['visible'])
        self.assertEqual([], self.enemy_contact(message)['radio_recipients'])

    def test_real_producer_passes_server_validation_then_reaches_client(self):
        self.scene()
        message = self.observation()
        server, _, _ = bot_fixture.ServerBotObservationRelayTests._server()
        server.players.pop(2)
        server.bot_states = copy.deepcopy(self.runtime.states)
        server.bot_manifest = [dict(row, vehicle='ussr:R11_MS-1', max_health=1000)
                               for row in self.start['bots']]
        message['round_id'] = server.round_id
        relay = server.update_bot_observation(
            bot_fixture.SIMULATION_WORKER_AUTHORITY_ID, message)
        self.assertIsInstance(relay, dict)
        battle, record, _ = self.client()
        battle._apply_team_observation(relay, 1.0)
        battle._update_spotting(1.0)
        self.assertTrue(record['spot_visible'])
        self.assertGreater(battle._records['bot:12']['radio_spot_until'], 1.0)

    def test_next_round_cannot_keep_old_radio_contacts(self):
        self.scene()
        self.observation()
        self.assertTrue(self.runtime._radio_network.observations)
        self.runtime.battle_start(dict(self.start, round_id=6))
        self.assertEqual({}, self.runtime._radio_network.actors)
        self.assertEqual({}, self.runtime._radio_network.observations)

    def test_actual_descriptor_and_player_snapshot_determine_radio_range(self):
        player = self.scene()
        bot = self.runtime.states[11]
        self.assertAlmostEqual(700.0, self.runtime._source_radio_range(bot))
        self.assertAlmostEqual(700.0, self.runtime._source_radio_range(
            dict(player, kind='human')))
        player['effective_params']['loadout']['radio_factor'] = 0.5
        self.assertAlmostEqual(350.0, self.runtime._source_radio_range(
            dict(player, kind='human')))
        self.assertEqual(0.0, self.runtime._source_radio_range(
            {'id': 3, 'kind': 'human'}))


if __name__ == '__main__':
    unittest.main()
