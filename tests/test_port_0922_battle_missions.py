"""TAB receives only the local vehicle's selected, eligible mission."""

import copy
import types
import unittest

import test_port_0922_garage as garage_fixture
from test_port_0922_personal_campaign_battle import definition


class BattleMissionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.adapter = garage_fixture._load_port_module('battle_missions')
        self.snapshot = {
            'personalMissionSelections': {'regular': [1, 16, 31, 46, 61]},
            'personalMissionProgress': {},
        }
        self.classes = ('lightTank', 'heavyTank', 'mediumTank', 'AT-SPG', 'SPG')
        self.definitions = dict(
            (1 + index * 15, definition('<win/>', tags=kind, minimum=4))
            for index, kind in enumerate(self.classes))

    def selected(self, kind='mediumTank', level=6):
        descriptor = types.SimpleNamespace(type=types.SimpleNamespace(
            tags=set((kind, 'premium')), level=level))
        return self.adapter.selected_mission_ids(
            self.snapshot, descriptor, self.definitions.get)

    def test_each_vehicle_class_sees_only_its_selected_mission(self):
        for index, kind in enumerate(self.classes):
            self.assertEqual((1 + index * 15,), self.selected(kind))

    def test_tier_and_operation_prerequisites_are_from_client_definition(self):
        self.assertEqual((), self.selected(level=3))
        self.assertEqual((), self.selected(level=11))
        self.assertEqual((31,), self.selected(level=4))
        self.definitions[31] = definition(
            '<win/>', tags='mediumTank', required='15 30 45 60 75')
        self.snapshot['personalMissionProgress'] = {
            '15': 1, '30': 1, '45': 1, '60': 1}
        self.assertEqual((), self.selected())
        self.snapshot['personalMissionProgress']['75'] = 1
        self.assertEqual((31,), self.selected())

    def test_main_completion_keeps_honors_attempt_but_full_completion_hides(self):
        self.snapshot['personalMissionProgress']['31'] = 1
        self.assertEqual((31,), self.selected())
        self.snapshot['personalMissionProgress']['31'] = 2
        self.assertEqual((), self.selected())
        self.snapshot['personalMissionProgress'].pop('31')
        self.assertEqual((31,), self.selected())

    def test_no_automatic_selection_or_save_mutation(self):
        before = copy.deepcopy(self.snapshot)
        self.assertEqual((31,), self.selected())
        self.assertEqual(before, self.snapshot)
        self.snapshot['personalMissionSelections'] = {'regular': []}
        self.assertEqual((), self.selected())

    def test_missing_vehicle_or_mission_does_not_invent_a_task(self):
        self.assertEqual((), self.adapter.selected_mission_ids(
            self.snapshot, None, self.definitions.get))
        self.definitions.pop(31)
        self.assertEqual((), self.selected())
