"""Native pawn command, refundable orders and delayed reward publication."""
import copy
import sys
import types
import unittest
from unittest import mock

import test_port_0922_account_rpc as fixture
from gui.mods.offline_lan_0922 import personal_campaign

data = fixture.account_data
requests = fixture.account_requests


def node(value='', **children):
    return {'value': str(value), 'children': list(children.items())}


def definition(mission_id=15):
    credits = node(bonus=node(credits=node(100)))
    return {'main': credits, 'main_award_list': credits,
            'add': node(bonus=node(token=node(id=node('free_award_list'),
                                             count=node(1))))
                   if mission_id % 15 == 0 else node(bonus=node())}


class NativeCache:
    def questByPersonalMissionID(self, mission_id):
        operation = (mission_id - 1) // 75 + 1
        required = set(range((operation - 2) * 75 + 15,
                             (operation - 1) * 75 + 1, 15)) if operation > 1 else set()
        return types.SimpleNamespace(
            branch=0, tileID=operation, isFinal=mission_id % 15 == 0,
            mainAwardListQuestID='mission_%d_main_award_list' % mission_id,
            maySelectQuest=lambda completed: required <= set(completed))

    def initialMissionQuestIDByOperationIDChainID(self, operation, chain):
        return (operation - 1) * 75 + (chain - 1) * 15 + 1


def native_module():
    class Storage:
        def __init__(self, storage):
            self.storage = storage

        def makeCompDescr(self):
            return repr(sorted(self.storage.items())).encode('ascii')
    return types.SimpleNamespace(g_cache=NativeCache(), PMStorage=Storage,
        PM_STATE=types.SimpleNamespace(MAIN_REWARD_GOTTEN=3,
            ALL_REWARDS_GOTTEN=6, NEED_GET_MAIN_REWARD=2,
            NEED_GET_ALL_REWARDS=5, NEED_GET_ADD_REWARD=4))


class PersonalOrderTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(sys.modules, {'personal_missions': native_module()})
        patch.start()
        self.addCleanup(patch.stop)

    def state(self, **fields):
        snapshot = copy.deepcopy(fixture.SELECTED_VEHICLE)
        earned = dict((str(qid), 2) for qid in (30, 45, 60, 75))
        snapshot.update(personalMissionOrders=4,
                        personalMissionProgress=earned.copy(),
                        personalMissionRewarded=earned.copy(),
                        personalMissionTankwomen=dict((key, True) for key in earned),
                        personalMissionRewardJournal=dict(('orders:' + key, {'count': 1})
                                                         for key in earned),
                        wallet={'credits': 0, 'gold': 0, 'freeXP': 0, 'crystal': 0})
        snapshot.update(fields)
        return requests.garage.GarageState(snapshot)

    def settle_patch(self):
        original = personal_campaign.settle
        return mock.patch.object(personal_campaign, 'settle', side_effect=
            lambda state: original(state, definitions={qid: definition(qid)
                                    for qid in range(1, 301)}))

    def test_pawn_uses_event_type_and_debits_native_one_or_four_order_cost(self):
        for mission_id, cost in ((1, 1), (15, 4)):
            state = self.state()
            with self.settle_patch():
                result = requests.dispatch(requests.commands.CMD_PAWN_FREE_AWARD_LIST, {'garage': state}, ([8, mission_id],))
            self.assertEqual(requests.commands.RES_SUCCESS, result.result_id)
            saved = state.snapshot()
            self.assertEqual(4 - cost, saved['personalMissionOrders'])
            self.assertEqual({str(mission_id): cost}, saved['personalMissionPawned'])
            self.assertEqual(1, saved['personalMissionProgress'][str(mission_id)])
            self.assertEqual(100, saved['wallet']['credits'])
            self.assertEqual((4104777660, 1), data.personal_mission_tokens(saved)[
                'mission_%d_main_award_list' % mission_id])
            before = copy.deepcopy(saved)
            with self.settle_patch():
                repeated = requests.dispatch(requests.commands.CMD_PAWN_FREE_AWARD_LIST, {'garage': state}, ([8, mission_id],))
            self.assertEqual(requests.commands.RES_FAILURE, repeated.result_id)
            self.assertEqual(before, state.snapshot())

    def test_later_operation_and_insufficient_balance_reject_without_mutation(self):
        for fields, request in (({}, [8, 270]),
                                ({'personalMissionProgress': {'30': 2, '45': 2, '60': 2}}, [8, 15]),
                                ({'personalMissionOrders': 21,
                                  'personalMissionProgress': {}}, [8, 15]),
                                ({}, [0, 15])):
            state = self.state(**fields)
            before = copy.deepcopy(state.snapshot())
            result = requests.dispatch(requests.commands.CMD_PAWN_FREE_AWARD_LIST, {'garage': state}, (request,))
            self.assertEqual(requests.commands.RES_FAILURE, result.result_id)
            self.assertEqual(before, state.snapshot())

    def test_pawn_refresh_publishes_balance_completion_and_reward_together(self):
        state = self.state()
        updates = []
        with self.settle_patch():
            result = requests.dispatch(requests.commands.CMD_PAWN_FREE_AWARD_LIST,
                {'garage': state, 'push_update': updates.append}, ([8, 15],))
        self.assertEqual(requests.commands.RES_SUCCESS, result.result_id)
        self.assertEqual([], updates)
        result.before_response()
        self.assertEqual(1, len(updates))
        self.assertEqual(100, updates[0]['stats']['credits'])
        self.assertEqual((4104777660, 0), updates[0]['tokens']['free_award_list'])
        self.assertEqual((4104777660, 1),
                         updates[0]['tokens']['mission_15_main_award_list'])
        self.assertIn(b'(15, (0, 2))', updates[0]['potapovQuests']['compDescr'])

    def test_failed_reward_or_save_rolls_back_orders_completion_and_cash(self):
        state = self.state()
        before = copy.deepcopy(state.snapshot())
        with mock.patch.object(personal_campaign, 'settle', return_value={
                'pending': [(15, 'missing reward resources')]}):
            failed = requests.dispatch(requests.commands.CMD_PAWN_FREE_AWARD_LIST, {'garage': state}, ([8, 15],))
        self.assertEqual(requests.commands.RES_FAILURE, failed.result_id)
        self.assertEqual(before, state.snapshot())
        store = mock.Mock()
        store.flush.return_value = False
        with self.settle_patch():
            failed = requests.dispatch(requests.commands.CMD_PAWN_FREE_AWARD_LIST, {'garage': state, 'garage_store': store}, ([8, 15],))
        self.assertEqual(requests.commands.RES_FAILURE, failed.result_id)
        self.assertEqual(before, state.snapshot())

    def test_honors_replaces_manual_balance_and_returns_only_earned_orders_once(self):
        for initial, pawned, expected in ((21, {}, 1), (17, {'15': 4}, 1)):
            state = self.state(personalMissionOrders=initial,
                personalMissionProgress={'15': 2}, personalMissionRewarded={'15': 1},
                personalMissionPawned=pawned)
            result = personal_campaign.settle(state, definitions={15: definition()})
            self.assertFalse(result['pending'])
            self.assertEqual(expected, state.snapshot()['personalMissionOrders'])
            self.assertEqual({}, state.snapshot()['personalMissionPawned'])
            self.assertEqual((4104777660, 0), data.personal_mission_tokens(
                state.snapshot())['mission_15_main_award_list'])
            personal_campaign.settle(state, definitions={15: definition()})
            self.assertEqual(expected, state.snapshot()['personalMissionOrders'])

    def test_final_mission_keeps_native_crew_claim_pending_until_selected(self):
        state = self.state(personalMissionProgress={'15': 1, '30': 2},
                           personalMissionRewarded={}, personalMissionTankwomen={})
        self.assertEqual(b'[(15, (0, 2)), (30, (0, 5))]',
                         data.personal_missions(state.snapshot())['compDescr'])
        state.snapshot()['personalMissionTankwomen'] = {'15': True, '30': True}
        state.snapshot()['personalMissionRewarded'] = {'15': 1, '30': 2}
        self.assertEqual(b'[(15, (0, 3)), (30, (0, 6))]',
                         data.personal_missions(state.snapshot())['compDescr'])

    def test_native_completion_state_reflects_cash_and_delayed_crew_receipts(self):
        for mission_id in (1, 15):
            for completed in (1, 2):
                for paid in (0, 1, 2):
                    for crew in (False, True):
                        with self.subTest(mission=mission_id, completed=completed,
                                          paid=paid, crew=crew):
                            key = str(mission_id)
                            fields = {'personalMissionProgress': {key: completed},
                                      'personalMissionTankwomen': {key: crew}}
                            # Old launcher snapshots have no reward ledger.
                            if paid:
                                fields['personalMissionRewarded'] = {key: paid}
                            delivered = mission_id == 1 or crew
                            if completed == 1:
                                expected = 3 if paid and delivered else 2
                            else:
                                expected = (6 if paid == 2 and delivered else
                                            4 if paid and delivered else 5)
                            self.assertEqual(
                                repr([(mission_id, (0, expected))]).encode('ascii'),
                                data.personal_missions(fields)['compDescr'])

    def test_retry_claim_settles_unpaid_stage_and_rejects_duplicate_payment(self):
        for completed, paid, cash, orders in ((1, 0, 100, 0), (2, 1, 0, 0)):
            state = self.state(personalMissionProgress={'1': completed},
                personalMissionRewarded={'1': paid} if paid else {})
            with self.settle_patch():
                result = requests.dispatch(requests.commands.CMD_GET_POTAPOV_QUEST_REWARD,
                    {'garage': state}, ([0, 1, 0, 0, 0, 0],))
            self.assertEqual(requests.commands.RES_SUCCESS, result.result_id)
            self.assertEqual(completed, state.snapshot()['personalMissionRewarded']['1'])
            self.assertEqual(cash, state.snapshot()['wallet']['credits'])
            self.assertEqual(orders, state.snapshot()['personalMissionOrders'])
            before = copy.deepcopy(state.snapshot())
            with mock.patch.object(personal_campaign, 'settle') as settle:
                duplicate = requests.dispatch(requests.commands.CMD_GET_POTAPOV_QUEST_REWARD,
                    {'garage': state}, ([0, 1, 0, 0, 0, 0],))
            self.assertEqual(requests.commands.RES_FAILURE, duplicate.result_id)
            self.assertEqual('NO_REWARD', duplicate.error)
            settle.assert_not_called()
            self.assertEqual(before, state.snapshot())

    def test_claim_checks_paid_ledger_and_rolls_back_failed_rewards_or_crew(self):
        for pending in ([], [(15, 'missing reward resources')],
                        [('operation', 'operation reward unavailable')]):
            for need_tankman in (0, 1):
                state = self.state(personalMissionProgress={'15': 1})
                before = copy.deepcopy(state.snapshot())
                def incomplete_settlement(target):
                    target.snapshot()['wallet']['credits'] = 100
                    return {'pending': pending}
                with mock.patch.object(personal_campaign, 'settle',
                        side_effect=incomplete_settlement), mock.patch.object(
                            personal_campaign, 'claim_tankwoman') as claim:
                    result = requests.dispatch(requests.commands.CMD_GET_POTAPOV_QUEST_REWARD,
                        {'garage': state}, ([0, 15, need_tankman, 2, 17, 3],))
                self.assertEqual(requests.commands.RES_FAILURE, result.result_id)
                self.assertEqual(before, state.snapshot())
                claim.assert_not_called()
        state = self.state(personalMissionProgress={'15': 1})
        before = copy.deepcopy(state.snapshot())
        with self.settle_patch(), mock.patch.object(personal_campaign, 'claim_tankwoman',
                side_effect=requests.garage.GarageError('INVALID_CREW')):
            result = requests.dispatch(requests.commands.CMD_GET_POTAPOV_QUEST_REWARD,
                {'garage': state}, ([0, 15, 1, 2, 17, 3],))
        self.assertEqual(requests.commands.RES_FAILURE, result.result_id)
        self.assertEqual(before, state.snapshot())

    def test_mission_dossier_refresh_preserves_lifetime_battle_progress(self):
        state = self.state()
        updates = []
        history = {'account': {'battlesCount': 37, 'wins': 19}}
        postbattle = mock.Mock()
        postbattle.progress.return_value = history
        def dossier(progress, badges=None, mission_dossier=None):
            return {'history': copy.deepcopy(progress),
                    'missions': dict(mission_dossier or {})}
        def grant_medal(target):
            target.snapshot()['personalMissionDossier'] = {'achievements:medal': 1}
        with mock.patch.object(data, 'account_dossier', side_effect=dossier):
            result = requests._fitting(
                {'garage': state, 'postbattle_store': postbattle,
                 'push_update': updates.append}, grant_medal)
            result.before_response()
        self.assertEqual(history, updates[0]['stats']['dossier']['history'])
        self.assertEqual({'achievements:medal': 1},
                         updates[0]['stats']['dossier']['missions'])

    def test_claim_request_preserves_native_six_field_selection_and_rejects_bad_shape(self):
        state = self.state(personalMissionProgress={'15': 1})
        with self.settle_patch(), mock.patch.object(personal_campaign, 'claim_tankwoman', return_value=701) as claim:
            result = requests.dispatch(requests.commands.CMD_GET_POTAPOV_QUEST_REWARD, {'garage': state},
                ([0, 15, 1, 2, 17, 3],))
        self.assertEqual(requests.commands.RES_SUCCESS, result.result_id)
        claim.assert_called_once_with(state, 15, 2, 17, 3)
        for command, args in ((requests.commands.CMD_PAWN_FREE_AWARD_LIST, (8, 15)),
                (requests.commands.CMD_PAWN_FREE_AWARD_LIST, ([8],)),
                (requests.commands.CMD_GET_POTAPOV_QUEST_REWARD, ([0, 15],))):
            result = requests.dispatch(command, {'garage': state}, args)
            self.assertEqual(requests.commands.RES_FAILURE, result.result_id)

    def test_committed_balance_and_tokens_survive_the_publication_shape(self):
        snapshot = self.state(personalMissionOrders=22,
            personalMissionTokens={'operation_done': [4104777660, 3]}).snapshot()
        tokens = data.personal_mission_tokens(snapshot)
        self.assertEqual((4104777660, 22), tokens['free_award_list'])
        self.assertEqual((4104777660, 3), tokens['operation_done'])


if __name__ == '__main__':
    unittest.main()
