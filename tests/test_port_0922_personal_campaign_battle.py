"""Mission settlement decisions use real target facts, never totals by guess."""

import copy
import types
import unittest
import xml.etree.ElementTree as ET

import test_port_0922_garage as garage_fixture


def node(xml):
    def convert(element):
        return {'value': (element.text or '').strip(),
                'children': [(child.tag, convert(child)) for child in element]}
    return convert(ET.fromstring(xml))


def definition(main, additional='<win/>', tags='mediumTank', minimum=6,
               required=''):
    def quest(body):
        return node('<quest><conditions><postBattle><and>' + body +
                    '</and></postBattle></conditions></quest>')
    return {
        'metadata': node('<metadata><tags>' + tags + '</tags><minLevel>' +
                         str(minimum) + '</minLevel><maxLevel>10</maxLevel>' +
                         '<requiredUnlocks>' + required +
                         '</requiredUnlocks></metadata>'),
        'main': quest(main), 'add': quest(main + additional),
    }


# The #1513 regular_4_3_15 main/add expressions; tests keep no dependency on
# an unshipped developer reference tree or an installed Windows client.
MT15_MAIN = ('<vehicleDamage><classes>AT-SPG</classes>'
             '<greaterOrEqual>3500</greaterOrEqual></vehicleDamage>')
MT15_ADD = ('<results><key>damaged</key><greaterOrEqual>8</greaterOrEqual>'
            '</results><win/>')


class PersonalCampaignBattleTests(unittest.TestCase):
    def setUp(self):
        self.policy = garage_fixture._load_port_module('personal_campaign_battle')
        self.definitions = {270: definition(MT15_MAIN, MT15_ADD)}
        self.snapshot = {
            'personalMissionSelections': {'regular': [270]},
            'personalMissionProgress': {},
        }
        self.descriptions = {
            'ussr:medium': ('mediumTank', 10),
            'ussr:destroyer': ('AT-SPG', 10),
            'germany:heavy': ('heavyTank', 10),
            'usa:light': ('lightTank', 4),
        }
        self.vehicles = types.SimpleNamespace(VehicleDescr=self.describe)
        self.receipt = {
            'battle_mode': 'regular', 'premature_leave': False,
            'vehicle': 'ussr:medium', 'team': 1, 'winner': 1,
            'player_id': 1, 'death_reason': -1, 'finish_reason': 1,
            'stats': {'damage': 6000, 'damaged': 8, 'damage_received': 1000,
                      'damage_blocked': 2000, 'capture_points': 0},
            'rewards': {'xp': 800},
            'public_results': [
                {'actor_kind': 'player', 'actor_id': 1, 'team': 1,
                 'vehicle': 'ussr:medium', 'xp': 800,
                 'stats': {'damage': 6000}},
                {'actor_kind': 'bot', 'actor_id': 2, 'team': 2,
                 'vehicle': 'ussr:destroyer', 'xp': 9000,
                 'stats': {'damage': 4000}},
                {'actor_kind': 'bot', 'actor_id': 3, 'team': 2,
                 'vehicle': 'germany:heavy', 'xp': 300,
                 'stats': {'damage': 100}},
                {'actor_kind': 'bot', 'actor_id': 4, 'team': 2,
                 'vehicle': 'ussr:destroyer', 'xp': 500,
                 'stats': {'damage': 1000}},
            ],
            'interactions': [
                {'target_kind': 'bot', 'target_id': 2, 'damage': 2000,
                 'target_kills': 1, 'death_reason': 0},
                {'target_kind': 'bot', 'target_id': 3, 'damage': 2500,
                 'target_kills': 0, 'death_reason': -1},
                {'target_kind': 'bot', 'target_id': 4, 'damage': 1500,
                 'target_kills': 1, 'death_reason': 0},
            ],
        }

    def describe(self, typeName):
        kind, level = self.descriptions[typeName]
        return types.SimpleNamespace(type=types.SimpleNamespace(
            tags=set((kind,)), level=level))

    def evaluate(self):
        return self.policy.evaluate(self.snapshot, self.receipt, self.vehicles,
                                    definition_provider=self.definitions.get)

    def use_condition(self, condition, additional='<win/>'):
        self.definitions[270] = definition(condition, additional)

    def test_object_260_mt15_totals_only_tank_destroyer_damage(self):
        self.assertEqual({'270': 2}, self.evaluate()['completed'])
        self.receipt['interactions'][2]['damage'] = 1499
        self.assertEqual({}, self.evaluate()['completed'])

    def test_reset_completed_mt15_can_complete_again_without_stale_state(self):
        self.snapshot['personalMissionProgress'] = {'270': 2}
        self.assertEqual({}, self.evaluate()['completed'])
        self.snapshot['personalMissionProgress'] = {}
        before = copy.deepcopy(self.snapshot)
        self.assertEqual({'270': 2}, self.evaluate()['completed'])
        self.assertEqual(before, self.snapshot)

    def test_main_does_not_require_win_or_eight_damaged_vehicles(self):
        self.receipt['winner'] = 2
        self.receipt['stats']['damaged'] = 3
        self.assertEqual({'270': 1}, self.evaluate()['completed'])

    def test_honours_retry_requires_main_condition_in_the_same_battle(self):
        self.snapshot['personalMissionProgress'] = {'270': 1}
        self.receipt['interactions'][2]['damage'] = 0
        self.assertEqual({}, self.evaluate()['completed'])
        self.receipt['interactions'][2]['damage'] = 1500
        self.assertEqual({'270': 2}, self.evaluate()['completed'])

    def test_vehicle_class_and_tier_come_from_actual_descriptor(self):
        self.receipt['vehicle'] = 'germany:heavy'
        self.assertEqual({}, self.evaluate()['completed'])
        self.receipt['vehicle'] = 'ussr:medium'
        self.descriptions['ussr:medium'] = ('mediumTank', 5)
        self.assertEqual({}, self.evaluate()['completed'])

    def test_missing_target_descriptor_cannot_count_as_a_tank_destroyer(self):
        del self.descriptions['ussr:destroyer']
        result = self.evaluate()
        self.assertEqual({}, result['completed'])
        self.assertEqual(['target vehicle descriptor'], result['unsupported']['270'])

    def test_required_unlocks_must_be_completed_but_need_not_have_honours(self):
        self.definitions[270] = definition(MT15_MAIN, MT15_ADD,
                                          required='268 269')
        self.snapshot['personalMissionProgress'] = {'268': 2}
        self.assertEqual({}, self.evaluate()['completed'])
        self.snapshot['personalMissionProgress']['269'] = 1
        self.assertEqual({'270': 2}, self.evaluate()['completed'])

    def test_training_and_premature_leaving_do_not_complete_campaigns(self):
        self.receipt['battle_mode'] = 'training'
        self.assertEqual({}, self.evaluate()['completed'])
        self.receipt['battle_mode'] = 'regular'
        self.receipt['premature_leave'] = True
        self.assertEqual({}, self.evaluate()['completed'])

    def test_friendly_target_damage_is_excluded_even_in_malformed_input(self):
        self.receipt['public_results'][3]['team'] = 1
        self.assertEqual({}, self.evaluate()['completed'])

    def test_unknown_event_modifier_never_becomes_plain_damage(self):
        for modifier in ('eventCount', 'whileInvisible', 'whileFullHealth',
                         'enemyImmobilized', 'limittedTime', 'distance'):
            with self.subTest(modifier=modifier):
                self.use_condition('<vehicleDamage><' + modifier + '/>' +
                                   '<greaterOrEqual>1</greaterOrEqual>' +
                                   '</vehicleDamage>')
                result = self.evaluate()
                self.assertEqual({}, result['completed'])
                self.assertIn(modifier, result['unsupported']['270'][0])

    def test_mt2_damage_event_thresholds_for_all_four_operations(self):
        for target_id in (5, 6):
            target = dict(self.receipt['public_results'][1], actor_id=target_id)
            self.receipt['public_results'].append(target)
            self.receipt['interactions'].append(dict(
                self.receipt['interactions'][0], target_id=target_id))
        for operation, required, kills in ((1, 6, 1), (2, 9, 2),
                                           (3, 12, 3), (4, 15, 5)):
            with self.subTest(operation=operation):
                qid = 32 + (operation - 1) * 75
                self.snapshot['personalMissionSelections']['regular'] = [qid]
                self.definitions[qid] = definition(
                    '<vehicleDamage><eventCount/><greaterOrEqual>%d'
                    '</greaterOrEqual></vehicleDamage>' % required,
                    '<vehicleKills><greaterOrEqual>%d'
                    '</greaterOrEqual></vehicleKills>' % kills)
                for event in self.receipt['interactions']:
                    event['damage_events'] = 0
                    event['target_kills'] = 0
                # Repeated HP damage to the same enemy counts each time.
                first = self.receipt['interactions'][0]
                first.update(damage_events=required)
                for event in self.receipt['interactions'][:kills]:
                    event['target_kills'] = 1
                self.assertEqual({str(qid): 2}, self.evaluate()['completed'])
                self.receipt['interactions'][kills - 1]['target_kills'] = 0
                self.assertEqual({str(qid): 1}, self.evaluate()['completed'])
                first['damage_events'] = required - 1
                self.assertEqual({}, self.evaluate()['completed'])

    def test_damage_event_modifier_payload_is_not_silently_ignored(self):
        self.use_condition('<vehicleDamage><eventCount><whileInvisible/>'
                           '</eventCount><greaterOrEqual>1</greaterOrEqual>'
                           '</vehicleDamage>')
        self.receipt['interactions'][0]['damage_events'] = 100
        self.assertEqual({}, self.evaluate()['completed'])
        self.assertIn('eventCount modifier', self.evaluate()['unsupported']['270'][0])

    def test_unsupported_honours_still_allows_proven_main_completion(self):
        self.use_condition(MT15_MAIN, '<multiStunEvent/>')
        result = self.evaluate()
        self.assertEqual({'270': 1}, result['completed'])
        self.assertIn('multiStunEvent', result['unsupported']['270'][0])

    def test_supported_solo_branch_can_satisfy_platoon_or_solo_condition(self):
        self.use_condition('<or><unit><unitVehicleKills><greaterOrEqual>2' +
                           '</greaterOrEqual></unitVehicleKills></unit>' +
                           '<vehicleKills><greaterOrEqual>2</greaterOrEqual>' +
                           '</vehicleKills></or>')
        result = self.evaluate()
        self.assertEqual({'270': 2}, result['completed'])
        self.assertEqual({}, result['unsupported'])

    def test_xp_rank_uses_base_xp_and_respects_team_vs_all_players(self):
        self.receipt['awarded'] = {'xp': 20000}
        self.use_condition('<results><key>xp</key><max>1</max></results>')
        self.assertEqual({'270': 2}, self.evaluate()['completed'])
        self.use_condition('<results><key>xp</key><max>1</max><total/></results>')
        self.assertEqual({}, self.evaluate()['completed'])

    def test_combined_heavy_tank_damage_includes_received_and_blocked(self):
        self.use_condition('<results><plus><key>damageDealt</key>' +
                           '<key>damageReceived</key><key>damageBlockedByArmor' +
                           '</key></plus><greaterOrEqual>9000</greaterOrEqual>' +
                           '</results>')
        self.assertEqual({'270': 2}, self.evaluate()['completed'])
        self.receipt['stats']['damage_blocked'] = 1999
        self.assertEqual({}, self.evaluate()['completed'])

    def test_percentage_uses_own_team_damage(self):
        self.use_condition('<results><key>percentFromTotalTeamDamage</key>' +
                           '<greaterOrEqual>90</greaterOrEqual></results>')
        self.assertEqual({'270': 2}, self.evaluate()['completed'])
        self.receipt['public_results'][1]['team'] = 1
        self.assertEqual({}, self.evaluate()['completed'])

    def test_capture_result_requires_actual_capture_finish(self):
        self.use_condition('<results><key>isEnemyBaseCaptured</key>' +
                           '<equal>1</equal></results>')
        self.receipt['stats']['capture_points'] = 100
        self.assertEqual({}, self.evaluate()['completed'])
        self.receipt['finish_reason'] = 2
        self.assertEqual({'270': 2}, self.evaluate()['completed'])

    def test_restricted_kill_uses_the_recorded_death_reason(self):
        self.use_condition('<vehicleKills><attackReason>2</attackReason>' +
                           '<greaterOrEqual>1</greaterOrEqual></vehicleKills>')
        self.assertEqual({}, self.evaluate()['completed'])
        self.receipt['interactions'][0]['death_reason'] = 2
        self.assertEqual({'270': 2}, self.evaluate()['completed'])

    def test_missing_stat_is_unknown_and_never_zero_for_zero_requirements(self):
        self.use_condition('<results><key>isAnyOurCrittedInnerModules</key>' +
                           '<equal>0</equal></results>')
        self.assertEqual({}, self.evaluate()['completed'])
        self.assertIn('isAnyOurCrittedInnerModules',
                      self.evaluate()['unsupported']['270'][0])

    def test_assistance_vehicle_counts_use_distinct_recorded_targets(self):
        self.use_condition('<results><key>damagedVehicleCntAssistedTrack</key>' +
                           '<greaterOrEqual>2</greaterOrEqual></results>')
        for item, amount in zip(self.receipt['interactions'], (1000, 0, 30)):
            item['assist_track'] = amount
        self.assertEqual({'270': 2}, self.evaluate()['completed'])
        self.receipt['interactions'][2]['assist_track'] = 0
        self.assertEqual({}, self.evaluate()['completed'])

    def test_empty_selections_do_not_load_installed_mission_resources(self):
        self.snapshot['personalMissionSelections']['regular'] = []
        self.assertEqual({'completed': {}, 'unsupported': {}},
                         self.policy.evaluate(self.snapshot, self.receipt))

    def test_installed_threshold_is_used_instead_of_a_baked_mt15_number(self):
        self.definitions[270] = definition(
            MT15_MAIN.replace('3500', '3501'), MT15_ADD)
        self.assertEqual({}, self.evaluate()['completed'])

    def test_new_condition_group_is_not_discarded(self):
        self.definitions[270]['main'] = node(
            '<quest><conditions><preBattle><account/></preBattle>' +
            '<postBattle><win/></postBattle></conditions></quest>')
        result = self.evaluate()
        self.assertEqual({}, result['completed'])
        self.assertEqual(['preBattle restriction'], result['unsupported']['270'])

    def test_resource_read_failure_is_reported_without_blocking_settlement(self):
        def unavailable(unused_qid):
            raise ValueError('missing XML')
        result = self.policy.evaluate(self.snapshot, self.receipt, self.vehicles,
                                      definition_provider=unavailable)
        self.assertEqual({}, result['completed'])
        self.assertEqual(['mission resource: missing XML'],
                         result['unsupported']['270'])


if __name__ == '__main__':
    unittest.main()
