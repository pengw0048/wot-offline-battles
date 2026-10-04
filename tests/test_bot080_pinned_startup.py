"""Exercise the retained battle startup with the actual restored Bot planner."""
import base64
import types
import unittest
from unittest import mock

from test_port_0922_battle_runtime import (
    BattleRuntime, _runtime, _Client, _minimal_start, _Descriptor,
    _plain_bot_factors, bot_runtime)


class PinnedBotStartupTests(unittest.TestCase):
    def test_hidden_worker_reaches_running_with_human_and_pinned_bot(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        runtime.nations = types.SimpleNamespace(
            AVAILABLE_NAMES=('ussr',), INDICES={'ussr': 0})
        name = 'ussr:R11_MS-1'
        runtime.vehicles.g_list = types.SimpleNamespace(getList=lambda nation: {
            1: types.SimpleNamespace(name=name, level=1, tags=('lightTank',))})

        def descriptor(typeName=None, compactDescr=None):
            # The fixture encodes the vehicle name instead of a native binary
            # descriptor. Decode bytes as Python 2 does; retain the type check.
            if isinstance(compactDescr, bytes):
                compactDescr = compactDescr.decode('utf8')
            return _Descriptor(typeName or compactDescr or name, loaded=False)

        runtime.vehicles.VehicleDescr = descriptor
        battle = BattleRuntime(runtime)
        client = _Client()
        client.player_id = -1
        client.name = 'Worker'
        client.bot_authority_id = -1
        client.is_bot_authority = lambda: True
        client.send_bot_manifest = lambda *args: True
        start = _minimal_start()
        start['players'][0]['vehicle_compact_descr'] = base64.b64encode(
            name.encode('utf8')).decode('ascii')
        start['bots'] = [{'id': 2, 'team': 2, 'slot': 0, 'vehicle': name}]
        start['bot_lineup'] = [{'team': 2, 'slot': 0, 'vehicle': name}]
        config = {'map': '01_karelia', 'vehicle': name,
                  'name': 'Worker', 'worker_mode': True}
        with mock.patch.object(bot_runtime, '_bot_default_crew_factors',
                               side_effect=_plain_bot_factors):
            self.assertTrue(battle.start(config, start, client))
            # Drive the existing native-ready fixture, not the planner or
            # roster validation. Both remain their real production methods.
            for unused in range(12):
                if battle._server is not None:
                    runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
                if runtime.bigworld.callbacks:
                    runtime.bigworld.callbacks.pop(0)()
                if battle.state in ('running', 'failed'):
                    break
            self.assertEqual('running', battle.state)
            self.assertEqual({(2, 0): name}, battle._bot_vehicle_assignments)
            self.assertIsNotNone(battle._bots)


if __name__ == '__main__':
    unittest.main()
