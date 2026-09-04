from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import loadout
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime


class _Consumables(object):

    def __init__(self, compact_descrs, installed):
        self._compact_descrs = tuple(compact_descrs)
        self._installed = tuple(installed)

    def getIntCDs(self, unused_slot):
        return self._compact_descrs

    def getInstalledItems(self):
        return self._installed


class WorkerGarageLoadoutTests(unittest.TestCase):

    @staticmethod
    def _battle(worker):
        battle = BattleRuntime.__new__(BattleRuntime)
        battle._worker_mode = bool(worker)
        battle._garage_loadout = None
        battle._arena_type = types.SimpleNamespace(
            vehicleCamouflageKind='summer')
        return battle

    @staticmethod
    def _garage_item(crew=()):
        descriptor = types.SimpleNamespace(
            type=types.SimpleNamespace(name='china:Ch37_WZ111G_FT'),
            makeCompactDescr=lambda: b'wz111g-ft-fitting')
        return types.SimpleNamespace(
            shells=(types.SimpleNamespace(intCD=101, count=30),),
            equipment=types.SimpleNamespace(
                regularConsumables=_Consumables(
                    (401, 0, 403), ('ration', 'repair-kit'))),
            crew=tuple(crew),
            descriptor=descriptor,
            getBonusCamo=lambda: types.SimpleNamespace(id=37),
            getOutfit=lambda unused_season: types.SimpleNamespace(
                strCompactDescr=b'wz111g-ft-outfit'))

    @staticmethod
    def _client_modules(item):
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            isPresent=lambda: True, item=item)
        items = types.ModuleType('items')
        items.__path__ = []
        components = types.ModuleType('items.components')
        components.__path__ = []
        c11n_constants = types.ModuleType(
            'items.components.c11n_constants')

        class SeasonType(object):

            @staticmethod
            def fromArenaKind(unused_kind):
                return 1

        c11n_constants.SeasonType = SeasonType
        return {
            'CurrentVehicle': current_vehicle,
            'items': items,
            'items.components': components,
            'items.components.c11n_constants': c11n_constants,
        }

    def test_worker_ignores_every_field_from_its_unrelated_garage_item(self):
        item = self._garage_item(crew=((0, 'commander'), (1, 'driver')))
        battle = self._battle(worker=True)

        with mock.patch.dict(sys.modules, self._client_modules(item)):
            snapshot = battle._garage_loadout_snapshot()

        self.assertIsNone(snapshot['shells'])
        self.assertIsNone(snapshot['equipment_ids'])
        self.assertEqual((), snapshot['equipments'])
        self.assertEqual((), snapshot['crew'])
        self.assertIsNone(snapshot['camouflage_id'])
        self.assertEqual('', snapshot['outfit'])
        self.assertIsNone(snapshot['fitting'])

    def test_visible_client_keeps_every_field_from_its_mounted_vehicle(self):
        crew = ((0, 'commander'), (1, 'loader'))
        item = self._garage_item(crew=crew)
        battle = self._battle(worker=False)

        with mock.patch.dict(sys.modules, self._client_modules(item)):
            snapshot = battle._garage_loadout_snapshot()

        self.assertEqual({101: 30}, snapshot['shells'])
        self.assertEqual([401, 0, 403], snapshot['equipment_ids'])
        self.assertEqual(('ration', 'repair-kit'), snapshot['equipments'])
        self.assertEqual(crew, snapshot['crew'])
        self.assertEqual(37, snapshot['camouflage_id'])
        self.assertEqual(b'wz111g-ft-outfit', snapshot['outfit'])
        self.assertEqual(
            (b'wz111g-ft-fitting', 'china:Ch37_WZ111G_FT'),
            snapshot['fitting'])

    def test_worker_uses_target_default_crew_for_any_garage_role_shape(self):
        target_type = types.SimpleNamespace(
            id=(3, 1),
            crewRoles=(
                ('commander',), ('gunner',), ('driver',),
                ('loader', 'radioman')))
        target = types.SimpleNamespace(type=target_type, optionalDevices=[])
        default_crew = tuple(
            'type62-default:%s' % roles[0]
            for roles in target_type.crewRoles)

        source_shapes = {
            # Ch37_WZ111G_FT -> Ch02_Type62 used to fail on slot 1.
            'different-role-order': (
                'commander', 'driver', 'gunner', 'loader'),
            # A same-order foreign vehicle used to pass native validation and
            # silently apply its specialization penalty to the target tank.
            'same-role-order': (
                'commander', 'gunner', 'driver', 'loader'),
        }
        for label, roles in source_shapes.items():
            with self.subTest(label=label):
                source_crew = tuple(
                    (index, types.SimpleNamespace(
                        strCD='garage-source:%s:%s' % (role, index)))
                    for index, role in enumerate(roles))
                item = self._garage_item(crew=source_crew)
                battle = self._battle(worker=True)
                utils = types.SimpleNamespace(
                    generateDefaultCrew=mock.Mock(return_value=default_crew),
                    makeDefaultVehicleAttributeFactors=mock.Mock(
                        return_value={}))
                modules = (
                    utils, types.SimpleNamespace(MAX_SKILL_LEVEL=100),
                    object(), types.SimpleNamespace(DEFAULT=0, WHEN_STILL=1),
                    object(), object(), object())
                calls = []

                def update(unused_descriptor, compact_descrs,
                           unused_equipments, unused_factors, unused_flags,
                           unused_is_fire, unused_aspects,
                           unused_qualifier_type, unused_crew_class,
                           unused_qualifiers_class):
                    calls.append(tuple(compact_descrs))

                with mock.patch.dict(
                        sys.modules, self._client_modules(item)), \
                        mock.patch.object(
                            loadout, '_client_modules', return_value=modules), \
                        mock.patch.object(
                            loadout,
                            '_update_native_attribute_factors_with_split',
                            side_effect=update):
                    snapshot = battle._garage_loadout_snapshot()
                    factors = loadout.attribute_factors(
                        target, snapshot['crew'] or None)

                utils.generateDefaultCrew.assert_called_once_with(
                    target_type, 100)
                self.assertEqual([default_crew], calls)
                self.assertEqual((0, 1), factors['_aspects'])


if __name__ == '__main__':
    unittest.main()
