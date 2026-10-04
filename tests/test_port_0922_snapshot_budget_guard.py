import json
from pathlib import Path
import sys
import unittest


SERVER_ROOT = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(SERVER_ROOT))

from lan_battle_server import (  # noqa: E402
    BOT_MANIFEST_REFRESH_TICKS, BattleState, CLIENT_BUILD_0922,
    LEAN_SNAPSHOT_MANIFEST_CAPABILITY, MAX_LINE_BYTES, Player, TICK_HZ,
)


class _Connection(object):
    def __init__(self):
        self.messages = []

    def sendall(self, payload):
        self.messages.append(json.loads(payload.decode('utf-8')))


class SnapshotBudgetGuardTests(unittest.TestCase):
    """Oversized snapshots are deferred, never mistaken for dead peers."""

    def _state(self, manifest_bytes=0, base_bytes=0):
        state = BattleState(map_name='01_karelia', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        state._next_bot_planner_tick = 1000000
        connection = _Connection()
        player = Player(
            1, connection, ('127.0.0.1', 1000),
            capabilities=(LEAN_SNAPSHOT_MANIFEST_CAPABILITY,))
        state.players = {player.player_id: player}
        state.bot_authority_id = player.player_id
        state.bot_manifest_authority_id = player.player_id
        state.bot_roster = []
        state.bot_manifest = ([{
            'id': 2, 'team': 2, 'slot': 0,
            'vehicle': 'ussr:R11_MS-1',
            'wire_padding': 'm' * manifest_bytes,
        }] if manifest_bytes else [])
        state.bot_manifest_revision = int(bool(state.bot_manifest))
        state.bot_states = ({
            2: {
                'id': 2, 'team': 2, 'slot': 0,
                'vehicle': 'ussr:R11_MS-1', 'alive': True,
                'health': 1000, 'max_health': 1000,
                'x': 0.0, 'y': 0.0, 'z': 35.0,
                'wire_padding': 'b' * base_bytes,
            },
        } if base_bytes else {})
        state.bot_state_revision = int(bool(state.bot_states))
        player.bot_order_revision_sent = state.bot_orders['revision']
        player.bot_order_tick_sent = 0
        player.destructible_revision_sent = state.destructible_revision
        player.destructible_tick_sent = 0
        return state, player, connection

    @staticmethod
    def _mark_manifest_current(state, player, sent_tick=0):
        player.bot_manifest_round_id_sent = state.round_id
        player.bot_manifest_authority_epoch_sent = state.authority_epoch
        player.bot_manifest_revision_sent = state.bot_manifest_revision
        player.bot_manifest_tick_sent = sent_tick

    def test_oversized_periodic_manifest_replay_falls_back_to_lean_snapshot(self):
        # Each section fits independently; only their aligned replay is too
        # large.  The client already owns this exact lineage, so the static
        # manifest can wait without freezing current motion/combat state.
        state, player, connection = self._state(
            manifest_bytes=170000, base_bytes=100000)
        self._mark_manifest_current(state, player, sent_tick=0)
        state.tick = BOT_MANIFEST_REFRESH_TICKS

        state.tick_once(1.0 / TICK_HZ)

        snapshots = [message for message in connection.messages
                     if message.get('type') == 'snapshot']
        self.assertEqual(1, len(snapshots))
        lean = snapshots[0]
        self.assertNotIn('bot_manifest', lean)
        self.assertTrue(state._projectile_message_fits(lean))
        self.assertFalse(state._projectile_message_fits(
            dict(lean, bot_manifest=state.bot_manifest)))
        self.assertIs(state.players[player.player_id], player)
        self.assertTrue(player.connected)
        # Record the deferred cadence so the next frame can carry the other
        # independently replayable sections instead of retrying an impossible
        # manifest combination on every tick.
        self.assertEqual(state.tick, player.bot_manifest_tick_sent)
        self.assertEqual(state.tick, player.snapshot_tick_sent)

        state.tick = BOT_MANIFEST_REFRESH_TICKS * 2
        state.tick_once(1.0 / TICK_HZ)
        state.tick_once(1.0 / TICK_HZ)
        follow_up = [message for message in connection.messages
                     if message.get('type') == 'snapshot'][-1]
        self.assertNotIn('bot_manifest', follow_up)
        self.assertIn('bot_orders', follow_up)
        self.assertIn('destructibles', follow_up)

    def test_oversized_required_manifest_is_deferred_without_disconnect(self):
        # A new manifest revision cannot be omitted: a lean frame would make
        # the replica combine current bot bodies with stale static identities.
        # Defer the whole snapshot and retry rather than treating a local wire
        # budget decision as a dead TCP peer.
        state, player, connection = self._state(
            manifest_bytes=170000, base_bytes=100000)
        self.assertEqual(-1, player.bot_manifest_revision_sent)

        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual([], connection.messages)
        self.assertIs(state.players[player.player_id], player)
        self.assertTrue(player.connected)
        self.assertEqual(-1, player.snapshot_tick_sent)
        self.assertEqual(-1, player.bot_manifest_revision_sent)
        self.assertEqual(-1, player.bot_manifest_tick_sent)

    def test_oversized_mandatory_snapshot_is_deferred_without_disconnect(self):
        # There is no protocol-safe field to remove once the lean base itself
        # exceeds the frame limit.  Keeping the endpoint and all sent markers
        # unchanged permits a later, smaller coalesced state to recover.
        state, player, connection = self._state(base_bytes=MAX_LINE_BYTES)
        self._mark_manifest_current(state, player, sent_tick=0)

        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual([], connection.messages)
        self.assertIs(state.players[player.player_id], player)
        self.assertTrue(player.connected)
        self.assertEqual(-1, player.snapshot_tick_sent)
        self.assertEqual(0, player.bot_manifest_tick_sent)


if __name__ == '__main__':
    unittest.main()
