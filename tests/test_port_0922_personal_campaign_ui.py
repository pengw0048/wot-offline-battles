"""Receipt-truth and native system-message delivery for personal missions."""

import copy
import os
import sys
import types
import unittest
from unittest import mock

import test_port_0922_garage as fixture


def node(value='', **children):
    return {'value': str(value), 'children': [(key, node(value))
                                             for key, value in children.items()]}


def bonus(*children):
    return {'value': '', 'children': list(children)}


def mission(**values):
    row = {'id': 270, 'before': 0, 'after': 1, 'paid_before': 0,
           'paid_after': 1, 'paid_stages': [1], 'rewards': [],
           'orders_earned': 0, 'orders_refunded': 0,
           'tankwoman_pending': False}
    row.update(values)
    return row


class PersonalCampaignMessageTests(unittest.TestCase):
    def setUp(self):
        self.ui = fixture._load_port_module('personal_campaign_ui')
        patch = mock.patch.dict(os.environ, {'WOT_OFFLINE_UI_LANGUAGE': 'en'})
        patch.start()
        self.addCleanup(patch.stop)
        self.native_name = 'MT-15: The reward'
        quest = types.SimpleNamespace(getUserName=lambda: self.native_name,
                                      getShortUserName=lambda: 'MT-15')
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(eventsCache=types.SimpleNamespace(
            personalMissions=types.SimpleNamespace(getQuests=lambda: {270: quest})))
        gui_shared = types.ModuleType('gui.shared')
        gui_shared.__path__ = []
        gui_shared.personality = personality
        patch = mock.patch.dict(sys.modules, {'gui.shared': gui_shared,
                                             'gui.shared.personality': personality})
        patch.start()
        self.addCleanup(patch.stop)

    def message(self, row):
        return '\n'.join(self.ui.messages({'missions': [row]}))

    def test_first_completion_reports_only_paid_main_reward_and_selectable_crew(self):
        row = mission(rewards=[bonus(('credits', node(100000)),
            ('token', node(id='token:pt:final:s1:t4', count=1)),
            ('token', node(id='token:pt:regular:secret', count=1)))],
            tankwoman_pending=True)
        result = self.message(row)
        self.assertIn('Personal mission completed: MT-15: The reward.', result)
        self.assertIn('Credits: 100000', result)
        self.assertIn('Reward vehicle components: 1', result)
        self.assertIn('Female crew member available: choose', result)
        self.assertNotIn('token:', result)
        self.assertNotIn('Orders earned', result)

    def test_honors_upgrade_distinguishes_new_order_from_four_refunded_orders(self):
        row = mission(before=1, after=2, paid_before=1, paid_after=2,
                      paid_stages=[2], orders_earned=1, orders_refunded=4,
                      rewards=[bonus(('credits', node(500000)),
                                     ('token', node(id='free_award_list', count=1)))])
        result = self.message(row)
        self.assertIn('completed with honors', result)
        self.assertIn('Credits: 500000', result)
        self.assertEqual(1, result.count('Orders earned: 1.'))
        self.assertIn('Committed orders returned: 4.', result)
        self.assertNotIn('free_award_list', result)

    def test_repeat_completion_after_reset_does_not_announce_old_economic_rewards(self):
        row = mission(paid_before=2, paid_after=2, paid_stages=[],
                      rewards=[bonus(('credits', node(500000)))])
        result = self.message(row)
        self.assertIn('Personal mission completed:', result)
        self.assertIn('Previously claimed rewards', result)
        self.assertNotIn('500000', result)
        self.assertNotIn('Rewards granted', result)

    def test_restored_honors_reissues_only_earned_order(self):
        result = self.message(mission(before=1, after=2, paid_before=2,
            paid_after=2, paid_stages=[], orders_earned=1))
        self.assertIn('completed with honors', result)
        self.assertIn('Orders earned: 1.', result)
        self.assertIn('Previously claimed rewards', result)
        self.assertNotIn('Rewards granted', result)

    def test_incomplete_and_unchanged_rows_do_not_notify_even_with_pending_crew(self):
        rows = [mission(before=level, after=level, paid_before=level,
                        paid_after=level, paid_stages=[], tankwoman_pending=True)
                for level in (0, 1, 2)]
        rows.append(mission(before=2, after=1, paid_stages=[]))
        self.assertEqual([], self.ui.messages({'missions': rows}))

    def test_late_reward_payment_not_falsely_described_as_new_completion(self):
        result = self.message(mission(before=1, after=1,
            rewards=[bonus(('gold', node(50)))]))
        self.assertIn('Personal mission rewards:', result)
        self.assertIn('Gold: 50', result)
        self.assertNotIn('completed:', result)

    def test_refund_only_is_reported_without_new_completion_or_payment(self):
        result = self.message(mission(before=2, after=2, paid_before=2,
            paid_after=2, paid_stages=[], orders_refunded=4))
        self.assertIn('Committed orders returned: 4.', result)
        self.assertNotIn('Orders earned:', result)
        self.assertNotIn('Rewards granted', result)
        self.assertNotIn('completed', result)

    def test_unicode_names_are_decoded_and_escaped_for_native_html(self):
        self.native_name = '中坦任务 <最终> & 奖励'.encode('utf-8')
        with mock.patch.dict(os.environ, {'WOT_OFFLINE_UI_LANGUAGE': 'zh'}):
            result = self.message(mission(tankwoman_pending=True))
        self.assertIn('个人任务完成：中坦任务 &lt;最终&gt; &amp; 奖励。', result)
        self.assertIn('可招募一名女成员', result)
        self.assertNotIn('Female crew', result)

    def test_missing_cache_uses_readable_mission_label(self):
        with mock.patch.dict(sys.modules, {'gui.shared.personality': None}):
            result = self.message(mission())
        self.assertIn('Personal mission 270', result)

    def test_rewards_aggregate_actual_amounts_and_native_item_names(self):
        items = types.ModuleType('items')
        items.vehicles = types.SimpleNamespace(getItemByCompactDescr=lambda cd:
            types.SimpleNamespace(userString='Large repair kit') if cd == 555
            else None)
        row = mission(rewards=[bonus(('credits', node(100)), ('premium', node(1)),
            ('slots', node(1)), ('berths', node(4)), ('freeXP', node(25)),
            ('crystal', node(10)), ('item', node(555, count=2))),
            bonus(('credits', node(200)), ('premium', node(2)),
                  ('item', node(555, count=1)), ('item', node(999, count=5)))])
        with mock.patch.dict(sys.modules, {'items': items}):
            result = self.message(row)
        for expected in ('Credits: 300', 'Premium account: 3 day(s)',
                         'Garage slots: 1', 'Barracks bunks: 4', 'Free XP: 25',
                         'Bonds: 10', 'Large repair kit x3', 'Item x5'):
            self.assertIn(expected, result)
        self.assertNotIn('999', result)

    def test_notify_makes_one_native_push_for_entire_receipt_and_no_modal(self):
        messages = types.SimpleNamespace(SM_TYPE=types.SimpleNamespace(Information=6),
                                        pushMessage=mock.Mock())
        gui = sys.modules['gui']
        settlement = {'missions': [mission(), mission(id=1)]}
        before = copy.deepcopy(settlement)
        with mock.patch.object(gui, 'SystemMessages', messages, create=True):
            self.assertTrue(self.ui.notify(settlement))
            self.assertFalse(self.ui.notify({'missions': []}))
        messages.pushMessage.assert_called_once()
        args, kwargs = messages.pushMessage.call_args
        self.assertEqual({'type': 6}, kwargs)
        self.assertEqual(2, len(args[0].splitlines()))
        self.assertEqual(before, settlement)

    def test_native_push_failure_propagates_so_receipt_delivery_can_retry(self):
        messages = types.SimpleNamespace(SM_TYPE=types.SimpleNamespace(Information=6),
            pushMessage=mock.Mock(side_effect=RuntimeError('Lobby messages not ready')))
        with mock.patch.object(sys.modules['gui'], 'SystemMessages', messages, create=True):
            with self.assertRaisesRegex(RuntimeError, 'not ready'):
                self.ui.notify({'missions': [mission()]})

    def test_full_reward_withdrawal_uses_actual_assets_orders_and_crew_counts(self):
        row = mission(before=2, after=0, paid_before=2, paid_after=0,
            paid_stages=[], phase='revoked', orders_revoked=1,
            tankwomen_revoked=1, tankwoman_pending=True,
            rewards=[bonus(('credits', node(600000)), ('premium', node(1)),
                ('token', node(id='free_award_list', count=1)))])
        result = self.message(row)
        self.assertIn('completion withdrawn:', result)
        self.assertIn('Rewards withdrawn: Credits: 600000', result)
        self.assertIn('Premium account: 1 day(s)', result)
        self.assertIn('Orders withdrawn: 1.', result)
        self.assertIn('Female crew members withdrawn: 1.', result)
        self.assertNotIn('available: choose', result)
        self.assertNotIn('Rewards granted', result)
        self.assertNotIn('free_award_list', result)

    def test_honors_withdrawal_does_not_claim_main_reward_or_crew_removed(self):
        result = self.message(mission(before=2, after=1, paid_before=2,
            paid_after=1, paid_stages=[], phase='revoked', orders_revoked=1,
            rewards=[{'kind': 'credits', 'count': 500000}]))
        self.assertIn('honors withdrawn:', result)
        self.assertIn('Rewards withdrawn: Credits: 500000.', result)
        self.assertNotIn('completion withdrawn', result)
        self.assertNotIn('Female crew', result)

    def test_completion_after_full_reversal_announces_newly_paid_rewards(self):
        result = self.message(mission(before=0, after=2, paid_before=0,
            paid_after=2, paid_stages=[1, 2], orders_earned=1,
            rewards=[{'kind': 'credits', 'count': 600000}]))
        self.assertIn('Rewards granted: Credits: 600000.', result)
        self.assertIn('Orders earned: 1.', result)
        self.assertNotIn('Previously claimed', result)

    def test_operation_vehicle_and_compensation_follow_actual_receipt(self):
        items = types.ModuleType('items')
        items.vehicles = types.SimpleNamespace(getVehicleType=lambda cd:
            types.SimpleNamespace(userString='Object 260') if cd == 999 else None)
        granted = {'id': 'secret:operation:4', 'rewards': [
            {'kind': 'vehicle', 'vehicle_type': 999, 'vehicle': 'ussr:internal'},
            {'kind': 'slots', 'count': 1}, {'kind': 'badge', 'id': '10', 'count': 1}]}
        compensation = {'id': 'secret:operation:4', 'rewards': [
            {'kind': 'compensation', 'vehicle': 'ussr:internal', 'credits': 3050000}]}
        with mock.patch.dict(sys.modules, {'items': items}):
            result = '\n'.join(self.ui.messages({'operations': [granted, compensation]}))
        self.assertIn('Vehicle: Object 260', result)
        self.assertIn('Garage slots: 1', result)
        self.assertIn('Badges: 1', result)
        self.assertIn('Vehicle compensation: 3050000 credits', result)
        self.assertNotIn('secret:', result)
        self.assertNotIn('ussr:', result)

    def test_operation_withdrawal_is_shown_in_same_native_notification_batch(self):
        result = self.ui.messages({'missions': [mission()], 'operations': [
            {'id': 'operation', 'phase': 'revoked', 'rewards': [
                {'kind': 'vehicle', 'vehicle': 'missing'},
                {'kind': 'credits', 'count': 1000}]}]})
        self.assertEqual(2, len(result))
        self.assertIn('operation rewards withdrawn:', result[1])
        self.assertIn('Vehicle: Reward vehicle', result[1])
        self.assertIn('Credits: 1000', result[1])

    def test_launcher_account_changes_include_localized_assets_and_actual_currency(self):
        with mock.patch.object(self.ui, '_vehicle_name', return_value='Object 260'), \
                mock.patch.object(self.ui, '_badge_name', return_value='Campaign champion'):
            result = self.ui.messages({'account_changes': [
                {'phase': 'granted', 'rewards': [
                    {'kind': 'vehicle', 'vehicle': 'ussr:R110_Object_260'},
                    {'kind': 'crew', 'count': 4},
                    {'kind': 'badge', 'id': '10', 'count': 1},
                    {'kind': 'compensation', 'vehicle': 'ussr:R110_Object_260', 'credits': 1000}]},
                {'phase': 'revoked', 'rewards': [{'kind': 'freeXP', 'count': 200}]}]})
        self.assertEqual(2, len(result))
        for expected in ('Account assets received:', 'Object 260', 'Crew members: 4',
                         'Badge: Campaign champion', 'Vehicle compensation: 1000 credits'):
            self.assertIn(expected, result[0])
        self.assertIn('Account assets removed: Free XP: 200', result[1])

    def test_permanently_dismissed_woman_is_not_reported_as_withdrawn(self):
        result = self.message(mission(before=1, after=0, paid_stages=[],
            phase='revoked', tankwomen_revoked=0, tankwomen_already_dismissed=1))
        self.assertIn('already permanently dismissed: 1', result)
        self.assertNotIn('Female crew members withdrawn:', result)

    def test_unexplained_operation_ids_and_internal_tokens_do_not_invent_awards(self):
        self.assertEqual([], self.ui.messages({
            'operation_rewards': ['operation:4'],
            'operations': [{'id': 'operation:4', 'rewards': [
                {'kind': 'token', 'id': 'token:pt:internal', 'count': 5}]}]}))

    def test_only_exact_final_component_tokens_are_displayed(self):
        tokens = ('token:pt:final:s1:t4', 'token:pt:final:s1:t4:main',
                  'token:pt:final:s1:t4:add', 'token:pt:final:s1:t4:extra',
                  'token:pt:final:s1:t4\n', 'token:pt:final:s1:t4suffix')
        xml_result = self.message(mission(rewards=[bonus(*[
            ('token', node(id=identifier, count=1)) for identifier in tokens])]))
        structured_result = self.message(mission(rewards=[
            {'kind': 'token', 'id': identifier, 'count': 1} for identifier in tokens]))
        for result in (xml_result, structured_result):
            self.assertEqual(1, result.count('Reward vehicle components: 1'))
            self.assertNotIn('components: 6', result)
            self.assertNotIn('token:', result)
        self.assertEqual([], self.ui._reward_parts([bonus(*[
            ('token', node(id=identifier, count=1)) for identifier in tokens[1:]])]))

    def test_premium_withdrawal_formats_actual_ledger_seconds_without_day_rounding(self):
        ledger = fixture._load_port_module('personal_campaign_rewards')
        rows = ledger.notification_rows([{
            'kind': 'premium', 'start': 100000, 'end': 186400, 'seconds': 45001}])
        self.assertEqual(45001 / 86400.0, rows[0]['count'])
        result = self.message(mission(before=2, after=1, phase='revoked',
            paid_stages=[], rewards=rows))
        self.assertIn('Rewards withdrawn: Premium account: 0 d 12 h 30 min 1 s.', result)
        self.assertNotIn('1 day(s)', result)

    def test_premium_seconds_override_stale_day_count_and_ignore_zero_duration(self):
        rows = [{'kind': 'premium', 'seconds': 86400, 'count': 7.0},
                {'kind': 'premium', 'seconds': 0, 'count': 3.0}]
        result = self.message(mission(rewards=rows))
        self.assertIn('Premium account: 1 day(s)', result)
        self.assertNotIn('7 day(s)', result)
        self.assertEqual([], self.ui._payout_parts([rows[1]]))

    def test_actual_ledger_numeric_customization_type_displays_camouflage(self):
        ledger = fixture._load_port_module('personal_campaign_rewards')
        rows = ledger.notification_rows([{'kind': 'customization',
            'customization_type': 1, 'item_id': 72, 'vehicle_type': 15617, 'count': 3}])
        result = self.message(mission(rewards=rows))
        self.assertIn('Camouflage: 3', result)
        self.assertNotIn('15617', result)

    def test_reset_failure_without_reward_rows_still_has_a_readable_notification(self):
        with mock.patch.dict(os.environ, {'WOT_OFFLINE_UI_LANGUAGE': 'zh'}):
            lines = self.ui.messages({'reset_error':
                'PERSONAL_MISSION_RESET_WALLET_UNAVAILABLE: credits required=1000 available=50'})
        self.assertEqual(1, len(lines))
        self.assertIn('个人任务重置未生效', lines[0])
        self.assertIn('银币不足', lines[0])
        self.assertIn('需要 1000，现有 50', lines[0])
        self.assertNotIn('PERSONAL_MISSION_RESET', lines[0])

    def test_reset_source_errors_do_not_leak_internal_vehicle_names_or_tracebacks(self):
        for error in ('PERSONAL_MISSION_RESET_VEHICLE_SOURCE_UNAVAILABLE: ussr:secret',
                      'Traceback <internal> /unsafe/path'):
            result = self.ui.messages({'reset_error': error})[0]
            self.assertIn('Personal mission reset was not applied.', result)
            self.assertNotIn('ussr:', result)
            self.assertNotIn('Traceback', result)
            self.assertNotIn('/unsafe/path', result)


if __name__ == '__main__':
    unittest.main()
