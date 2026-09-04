from pathlib import Path
import json
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import equipment_mechanics


def _equipment(name, item_id, compact_descr, **values):
    defaults = {
        'name': name,
        'id': (0, item_id),
        'compactDescr': compact_descr,
        'tags': (),
        'reuseCount': 0,
        'cooldownSeconds': 0,
    }
    defaults.update(values)
    return types.SimpleNamespace(**defaults)


def _defaults():
    return (
        _equipment(
            'autoExtinguishers', 1, 11001, reuseCount=-1,
            cooldownSeconds=90, autoactivate=True,
            fireStartingChanceFactor=0.9),
        _equipment(
            'largeMedkit', 3, 11003, tags=('medkit',),
            reuseCount=-1, cooldownSeconds=90, repairAll=True,
            bonusValue=0.3),
        _equipment(
            'largeRepairkit', 5, 11005, tags=('repairkit',),
            reuseCount=-1, cooldownSeconds=90, repairAll=True,
            bonusValue=0.1),
    )


class EquipmentProjectionTests(unittest.TestCase):

    def test_projection_preserves_every_exact_descriptor_field(self):
        descriptor = _equipment(
            'removedRpmLimiter', 12, 11012, tags=('trigger',),
            reuseCount=0, cooldownSeconds=7,
            enginePowerFactor=1.1, engineHpLossPerSecond=1.5)

        projection = equipment_mechanics.project_equipment(descriptor)

        self.assertEqual('rpm_limiter', projection['kind'])
        self.assertEqual(12, projection['id'])
        self.assertEqual(11012, projection['compactDescr'])
        self.assertEqual(0, projection['reuseCount'])
        self.assertEqual(7.0, projection['cooldownSeconds'])
        self.assertEqual(1.1, projection['enginePowerFactor'])
        self.assertEqual(1.5, projection['engineHpLossPerSecond'])
        self.assertEqual(projection, json.loads(json.dumps(projection)))

    def test_projection_default_factor_keys_do_not_reclassify_passive_item(self):
        projection = equipment_mechanics.project_equipment(_equipment(
            'unknownPassive', 13, 11013))

        self.assertEqual('passive', projection['kind'])
        self.assertEqual(
            'passive', equipment_mechanics.equipment_kind(projection))

    def test_default_bot_loadout_comes_from_the_exact_cache(self):
        descriptors = _defaults()
        ids = dict((value.name, value.id[1]) for value in descriptors)
        by_id = dict((value.id[1], value) for value in descriptors)
        cache = types.SimpleNamespace(
            equipmentIDs=lambda: ids,
            equipments=lambda: by_id)

        projected = equipment_mechanics.default_bot_consumables(cache)

        self.assertEqual(
            list(equipment_mechanics.DEFAULT_BOT_CONSUMABLE_NAMES),
            [value['name'] for value in projected])
        self.assertEqual([1, 3, 5], [value['id'] for value in projected])
        self.assertEqual([90.0, 90.0, 90.0],
                         [value['cooldownSeconds'] for value in projected])
        self.assertTrue(projected[0]['autoactivate'])
        self.assertEqual(0.0, projected[0]['autoReactionSeconds'])


class EquipmentStateTests(unittest.TestCase):

    def test_auto_extinguisher_uses_next_canonical_observation(self):
        contract = equipment_mechanics.project_equipment(_defaults()[0])
        state = equipment_mechanics.EquipmentState(contract)
        fire = {'fire': True}

        effect = state.poll_auto(10.0, fire)

        self.assertEqual({'action': 'extinguish_fire'}, effect)
        self.assertEqual(-1, state.uses_left)
        self.assertAlmostEqual(100.0, state.ready_at)
        self.assertFalse(state.ready(99.999))
        self.assertTrue(state.ready(100.0))

    def test_zero_reaction_auto_item_activates_on_first_observation(self):
        contract = equipment_mechanics.project_equipment(
            _defaults()[0], reaction_seconds=0.0)
        state = equipment_mechanics.EquipmentState(contract)

        self.assertEqual(
            {'action': 'extinguish_fire'},
            state.poll_auto(10.0, {'fire': True}))

    def test_exhausted_auto_item_never_opens_an_unrestorable_pending_timer(self):
        contract = equipment_mechanics.project_equipment(_equipment(
            'autoExtinguishers', 1, 11001, reuseCount=0,
            cooldownSeconds=90, autoactivate=True))
        state = equipment_mechanics.EquipmentState(contract)

        self.assertEqual(
            {'action': 'extinguish_fire'},
            state.poll_auto(10.0, {'fire': True}))
        self.assertEqual(0, state.uses_left)

        self.assertIsNone(state.poll_auto(1000.0, {'fire': True}))
        snapshot = state.snapshot(1000.0)
        self.assertIsNone(snapshot['autoPendingElapsed'])
        restored = equipment_mechanics.EquipmentState(contract, 2000.0)
        self.assertTrue(restored.restore(snapshot, 2000.0))

    def test_reuse_count_is_additional_to_the_initial_charge(self):
        contract = equipment_mechanics.project_equipment(_equipment(
            'smallRepairkit', 4, 11004, tags=('repairkit',),
            reuseCount=1))
        state = equipment_mechanics.EquipmentState(contract)
        critical = {'destroyed': ['engineHealth']}

        self.assertIsNotNone(state.activate(
            1.0, critical, selected='engineHealth'))
        self.assertIsNotNone(state.activate(
            2.0, critical, selected='engineHealth'))
        self.assertIsNone(state.activate(
            3.0, critical, selected='engineHealth'))
        self.assertEqual(0, state.uses_left)

    def test_large_kits_apply_all_targets_and_keep_their_bonus(self):
        med = equipment_mechanics.project_equipment(_defaults()[1])
        repair = equipment_mechanics.project_equipment(_defaults()[2])
        critical = {
            'crew_ko': ['driver', 'loader1'],
            'destroyed': ['engineHealth'],
            'devices': [{'name': 'gunHealth', 'state': 'critical'}],
        }

        med_effect = equipment_mechanics.effect_policy(med, critical)
        repair_effect = equipment_mechanics.effect_policy(repair, critical)

        self.assertEqual('restore_crew', med_effect['action'])
        self.assertTrue(med_effect['repairAll'])
        self.assertEqual(0.3, med_effect['bonusValue'])
        self.assertEqual('repair_devices', repair_effect['action'])
        self.assertTrue(repair_effect['repairAll'])
        self.assertEqual(0.1, repair_effect['bonusValue'])

    def test_repairkit_ignores_hidden_hp_loss_before_a_module_is_critical(self):
        repair = equipment_mechanics.project_equipment(_defaults()[2])
        critical = {
            'devices': [{
                'name': 'engineHealth', 'state': 'normal',
                'hp': 80.0, 'max_hp': 100.0,
            }],
        }

        self.assertIsNone(
            equipment_mechanics.effect_policy(repair, critical))

    def test_bot_large_kit_waits_for_a_later_frame_and_honours_cooldown(self):
        contract = equipment_mechanics.project_equipment(_defaults()[2])
        state = equipment_mechanics.EquipmentState(contract)
        critical = {'destroyed': ['engineHealth']}

        self.assertIsNone(state.poll_bot(5.0, critical))
        self.assertIsNone(state.poll_bot(5.0, critical))
        self.assertEqual(
            'repair_devices', state.poll_bot(5.01, critical)['action'])
        self.assertIsNone(state.poll_bot(95.009, critical))
        self.assertIsNone(state.poll_bot(95.01, critical))
        self.assertEqual(
            'repair_devices', state.poll_bot(95.02, critical)['action'])

    def test_bot_large_medkit_waits_then_clears_stun_without_crew_damage(self):
        contract = equipment_mechanics.project_equipment(_defaults()[1])
        state = equipment_mechanics.EquipmentState(contract)

        self.assertIsNone(state.poll_bot(5.0, {}, stunned=True))
        self.assertIsNone(state.poll_bot(5.0, {}, stunned=True))
        effect = state.poll_bot(5.01, {}, stunned=True)

        self.assertEqual('restore_crew', effect['action'])
        self.assertTrue(effect['clearStun'])
        self.assertFalse(state.ready(5.01))

    def test_relative_snapshot_restores_cooldown_and_both_pending_clocks(self):
        auto = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(
                _defaults()[0], reaction_seconds=1.0))
        self.assertIsNone(auto.poll_auto(10.0, {'fire': True}))
        snapshot = auto.snapshot(10.2)
        restored = equipment_mechanics.EquipmentState(auto.contract, 100.0)

        self.assertTrue(restored.restore(snapshot, 100.0))
        self.assertIsNone(restored.poll_auto(100.799, {'fire': True}))
        self.assertEqual(
            'extinguish_fire',
            restored.poll_auto(100.8, {'fire': True})['action'])

        repair = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(_defaults()[2]))
        self.assertIsNone(repair.poll_bot(
            20.0, {'destroyed': ['engineHealth']}))
        repair_snapshot = repair.snapshot(20.0)
        repaired = equipment_mechanics.EquipmentState(repair.contract, 200.0)
        repaired.restore(repair_snapshot, 200.0)
        self.assertIsNone(repaired.poll_bot(
            200.0, {'destroyed': ['engineHealth']}))
        self.assertEqual(
            'repair_devices', repaired.poll_bot(
                200.01, {'destroyed': ['engineHealth']})['action'])

    def test_wire_snapshot_rejects_partial_or_changed_contract(self):
        state = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(_defaults()[2]))
        snapshot = state.snapshot(0.0)
        snapshot.pop('aiPendingElapsed')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            state.restore(snapshot, 1.0)

        snapshot = state.snapshot(0.0)
        snapshot['equipment']['bonusValue'] = 0.5
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            state.restore(snapshot, 1.0)

    def test_trusted_snapshot_reuses_the_validated_contract(self):
        state = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(_defaults()[2]))
        self.assertIsNotNone(state.activate(
            10.0, {'destroyed': ['engineHealth']}))

        trusted = state.trusted_snapshot(12.0)
        detached = state.snapshot(12.0)

        self.assertEqual(detached, trusted)
        self.assertIs(state.contract, trusted['equipment'])
        self.assertIsNot(state.contract, detached['equipment'])

        edge = state.trusted_snapshot_edge(12.0)
        refreshed = state.refresh_trusted_snapshot(trusted, 13.0)
        self.assertIs(trusted, refreshed)
        self.assertEqual(edge, state.trusted_snapshot_edge(13.0))
        self.assertAlmostEqual(87.0, trusted['cooldownTimeLeft'])

    def test_trusted_snapshot_edge_tracks_pending_start_and_clear(self):
        state = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(_defaults()[2]))
        baseline = state.trusted_snapshot_edge(10.0)

        self.assertIsNone(state.poll_bot(
            10.0, {'destroyed': ['engineHealth']}))
        pending = state.trusted_snapshot_edge(10.0)
        self.assertNotEqual(baseline, pending)
        wire = state.trusted_snapshot(10.0)
        self.assertEqual(0.0, wire['aiPendingElapsed'])

        self.assertEqual(pending, state.trusted_snapshot_edge(11.0))
        self.assertIs(wire, state.refresh_trusted_snapshot(wire, 11.0))
        self.assertEqual(1.0, wire['aiPendingElapsed'])

        self.assertIsNone(state.poll_bot(11.0, {}))
        self.assertNotEqual(pending, state.trusted_snapshot_edge(11.0))
        state.refresh_trusted_snapshot(wire, 11.0)
        self.assertIsNone(wire['aiPendingElapsed'])

    def test_bot_wire_validator_reuses_one_canonical_parse(self):
        contracts = [equipment_mechanics.project_equipment(value)
                     for value in _defaults()]
        states = [equipment_mechanics.EquipmentState(contract)
                  for contract in contracts]
        snapshots = [state.snapshot(0.0) for state in states]

        canonical = equipment_mechanics.canonical_bot_equipment_states(
            snapshots)

        self.assertEqual(tuple(contracts), tuple(
            row[0] for row in canonical))
        self.assertEqual((-1, -1, -1), tuple(
            row[1] for row in canonical))
        self.assertTrue(
            equipment_mechanics.validate_bot_equipment_states(snapshots))

        malformed = [dict(snapshot) for snapshot in snapshots]
        malformed[1] = dict(malformed[1])
        malformed[1]['cooldownTimeLeft'] = 91.0
        with self.assertRaisesRegex(ValueError, 'cooldown'):
            equipment_mechanics.canonical_bot_equipment_states(malformed)

    def test_fuel_food_extinguisher_and_active_rpm_have_distinct_effects(self):
        extinguisher = equipment_mechanics.project_equipment(_defaults()[0])
        food = equipment_mechanics.project_equipment(_equipment(
            'chocolate', 9, 11009, crewLevelIncrease=10.0))
        fuel = equipment_mechanics.project_equipment(_equipment(
            'gasoline105', 8, 11008, enginePowerFactor=1.1,
            turretRotationSpeedFactor=1.1))
        limiter = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(_equipment(
                'removedRpmLimiter', 12, 11012,
                enginePowerFactor=1.1, engineHpLossPerSecond=1.5)))

        self.assertIsNotNone(limiter.activate(
            0.0, requested_active=True))
        factors = equipment_mechanics.passive_effects(
            (extinguisher, food, fuel, limiter))

        self.assertAlmostEqual(0.9, factors['fireStartingChanceFactor'])
        self.assertAlmostEqual(10.0, factors['crewLevelIncrease'])
        self.assertAlmostEqual(1.21, factors['enginePowerFactor'])
        self.assertAlmostEqual(1.1, factors['turretRotationSpeedFactor'])
        self.assertAlmostEqual(1.5, factors['engineHpLossPerSecond'])


if __name__ == '__main__':
    unittest.main()
