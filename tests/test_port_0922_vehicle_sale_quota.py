"""Vehicle sale permission follows ownership through live account updates."""

import copy
import unittest
from unittest import mock

import test_port_0922_economy as economy_fixture
import test_port_0922_garage as garage_fixture


class VehicleSaleQuotaTests(unittest.TestCase):
    def test_selling_and_rebuying_refresh_permission_before_each_callback(self):
        requests, commands, garage = garage_fixture._request_modules()
        purchases = economy_fixture.VehiclePurchaseTests()
        snapshot = purchases._two_vehicles()
        snapshot['wallet']['gold'] = 20000
        with mock.patch.object(economy_fixture, 'GARAGE', garage):
            state = economy_fixture._state(snapshot)
        cache = requests.data.stats(state.snapshot())['stats']
        self.assertEqual(1, cache['vehicleSellsLeft'])
        published = []

        def update(diff):
            published.append(diff)
            cache.update(diff.get('stats', {}))

        context = {'garage': state, 'push_update': update}

        def apply(command, values, allowance, vehicle_count):
            result = requests.dispatch(command, context, (values,))
            self.assertEqual(commands.RES_SUCCESS, result.result_id)
            result.before_response()
            self.assertEqual(allowance, published[-1]['stats']['vehicleSellsLeft'])
            self.assertEqual(allowance, cache['vehicleSellsLeft'])
            self.assertEqual(vehicle_count, len(state.snapshot()['vehicles']))

        apply(commands.CMD_SELL_VEHICLE, [17, 10, 1, 0, 0], 0, 1)
        self.assertIsNone(published[-1]['inventory'][1]['compDescr'][10])
        self.assertEqual(2600000, cache['credits'])
        with purchases._built([]):
            apply(commands.CMD_BUY_VEHICLE,
                  [17, economy_fixture.SECOND_VEHICLE_CD, 0, 0, -1], 1, 2)
        self.assertEqual(7500, cache['gold'])
        new_id = state.snapshot()['vehicles'][-1]['id']
        apply(commands.CMD_SELL_VEHICLE, [17, new_id, 1, 0, 0], 0, 1)

        before = copy.deepcopy(state.snapshot())
        result = requests.dispatch(
            commands.CMD_SELL_VEHICLE, context, ([17, 9, 1, 0, 0],))
        self.assertEqual(commands.RES_FAILURE, result.result_id)
        self.assertEqual(3, len(published))
        self.assertEqual(0, cache['vehicleSellsLeft'])
        self.assertEqual(before, state.snapshot())


if __name__ == '__main__':
    unittest.main()
