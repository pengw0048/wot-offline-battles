"""Gender survives garage retirement and every native voice reset owner."""
import types
import unittest

import test_port_0922_offline_services_ui as ui_fixture
crew_voice = ui_fixture.fixture._load_port_module('crew_voice')


class CrewVoiceTests(unittest.TestCase):
    def test_battle_freezes_gender_before_the_garage_selection_is_retired(self):
        from unittest import mock
        import test_port_0922_battle_runtime as runtime_fixture
        module = runtime_fixture._mounted_current_vehicle_module()
        module.g_currentVehicle.item.crew = ((0, types.SimpleNamespace(
            descriptor=types.SimpleNamespace(role='commander', group=13))),)
        battle = runtime_fixture.BattleRuntime(runtime_fixture._runtime())
        with mock.patch.dict('sys.modules', {'CurrentVehicle': module}):
            self.assertEqual(13, battle._garage_loadout_snapshot()['crew_group'])
            module.g_currentVehicle.isPresent = lambda: False
            module.g_currentVehicle.item = None
            self.assertEqual(13, battle._garage_loadout_snapshot()['crew_group'])

    def test_only_the_mounted_commander_owns_the_composite_group(self):
        def member(role, group):
            return types.SimpleNamespace(descriptor=types.SimpleNamespace(role=role, group=group))
        self.assertEqual(14, crew_voice.commander_group((
            (2, member('loader', 1)), (0, member('commander', 14)))))
        self.assertEqual(13, crew_voice.commander_group((member('commander', 13),)))
        self.assertEqual(0, crew_voice.commander_group(((0, None),)))

    def test_standard_national_settings_and_postmortem_use_attached_gender(self):
        calls = []
        class Modes(object):
            def setCurrentNation(self, nation, gender='male'):
                calls.append((nation, gender))
                return 'native-result'
        original = Modes.setCurrentNation
        patches = []
        def patch(owner, name, value):
            patches.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        attached = types.SimpleNamespace(id=10)
        player = types.SimpleNamespace(fakeServer=object(),
            getVehicleAttached=lambda: attached,
            arena=types.SimpleNamespace(vehicles={10: {'crewGroup': 13}, 11: {'crewGroup': 0}}))
        garage = types.SimpleNamespace(isPresent=lambda: True, item=types.SimpleNamespace(
            crew=((0, types.SimpleNamespace(descriptor=types.SimpleNamespace(
                role='commander', group=1))),)))
        exports = {
            'BigWorld': {'player': lambda: player},
            'CurrentVehicle': {'g_currentVehicle': garage},
            'SoundGroups': {'SoundModes': Modes,
                'CREW_GENDER_SWITCHES': types.SimpleNamespace(
                    DEFAULT='male', MALE='male', FEMALE='female')},
            'account_helpers.settings_core.options': {'AltVoicesSetting': type(
                'Setting', (), {'setSystemValue': lambda self, value: True,
                                'clearPreviewSound': lambda self: None})},
        }
        try:
            with ui_fixture.native_modules(exports):
                crew_voice.install(patch)
                modes = Modes()
                # Standard (Chinese) reset and native national refresh.
                self.assertEqual('native-result', modes.setCurrentNation('default'))
                modes.setCurrentNation('germany', 'male')
                self.assertEqual([('default', 'female'), ('germany', 'female')], calls)
                # Spectating another vehicle and starting another round must
                # not reuse the departed female commander's switch.
                attached.id = 11
                modes.setCurrentNation('france', 'female')
                self.assertEqual(('france', 'male'), calls[-1])
                attached.id = 99
                modes.setCurrentNation('ussr', 'female')
                self.assertEqual(('ussr', 'female'), calls[-1])
                player.arena = None
                modes.setCurrentNation('default')
                self.assertEqual(('default', 'female'), calls[-1])
                player.fakeServer = None
                modes.setCurrentNation('default')
                self.assertEqual(('default', 'male'), calls[-1])
        finally:
            for owner, name, value in reversed(patches):
                setattr(owner, name, value)
        self.assertIs(Modes.setCurrentNation, original)

    def test_live_mode_changes_and_preview_cleanup_refresh_client_only_attachment(self):
        calls, patches = [], []
        player = types.SimpleNamespace(fakeServer=object(), vehicle=None,
            arena=types.SimpleNamespace(vehicles={10: {'crewGroup': 1}}))

        class Modes(object):
            def __init__(self):
                self.national = False

            def setCurrentNation(self, nation, gender='male'):
                calls.append((nation if self.national else 'zh', gender))
                return True

        modes = Modes()
        vehicle = types.SimpleNamespace(id=10, nation='germany', inWorld=True,
                                        isStarted=True, special=None)

        def refresh():
            if vehicle.special:
                calls.append((vehicle.special, 'native-special'))
            else:
                modes.setCurrentNation(vehicle.nation)

        vehicle.refreshNationalVoice = refresh
        player.getVehicleAttached = lambda: vehicle

        class Setting(object):
            def setSystemValue(self, value):
                if value == 'invalid':
                    return False
                # Native Standard resets the nation before replacing the
                # mapping; Commander only replaces the national preset.
                if value == 'standard':
                    modes.setCurrentNation('default')
                modes.national = value == 'commander'
                return True

            def clearPreviewSound(self):
                calls.append('preview-stopped')
                # The real clearPreviewSound has this guard. A client-only
                # Avatar has the attribute but never the engine attachment.
                if hasattr(player, 'vehicle'):
                    if player.vehicle is not None:
                        player.vehicle.refreshNationalVoice()
                else:
                    modes.setCurrentNation('default')
                return 'native-clear-result'

        original_set, original_clear = Setting.setSystemValue, Setting.clearPreviewSound
        exports = {
            'BigWorld': {'player': lambda: player},
            'SoundGroups': {'SoundModes': Modes,
                'CREW_GENDER_SWITCHES': types.SimpleNamespace(
                    DEFAULT='male', MALE='male', FEMALE='female')},
            'account_helpers.settings_core.options': {'AltVoicesSetting': Setting},
        }

        def patch(owner, name, replacement):
            patches.append((owner, name, getattr(owner, name)))
            setattr(owner, name, replacement)

        try:
            with ui_fixture.native_modules(exports):
                crew_voice.install(patch)
                setting = Setting()
                for group, gender in ((1, 'female'), (0, 'male')):
                    player.arena.vehicles[10]['crewGroup'] = group
                    for nation in ('germany', 'ussr', 'china', 'japan'):
                        vehicle.nation = nation
                        for value, language in (('commander', nation), ('standard', 'zh'),
                                                ('commander', nation), ('standard', 'zh')):
                            self.assertTrue(setting.setSystemValue(value))
                            self.assertEqual((language, gender), calls[-1])
                        setting.setSystemValue('commander')
                        modes.setCurrentNation('preview-nation')
                        self.assertEqual('native-clear-result', setting.clearPreviewSound())
                        self.assertEqual(['preview-stopped', (nation, gender)], calls[-2:])
                # Reverting a preview uses the same setting owner; special
                # crews stay owned by the native Vehicle refresh method.
                vehicle.special = 'sabaton'
                setting.setSystemValue('standard')
                self.assertEqual(('sabaton', 'native-special'), calls[-1])
                vehicle.special = None
                before = list(calls)
                self.assertFalse(setting.setSystemValue('invalid'))
                self.assertEqual(before, calls)
                for field, value in (('inWorld', False), ('isStarted', False)):
                    setattr(vehicle, field, value)
                    before = list(calls)
                    setting.setSystemValue('commander')
                    self.assertEqual(before, calls)
                    setattr(vehicle, field, True)
                player.vehicle = vehicle
                setting.clearPreviewSound()
                self.assertEqual(['preview-stopped', ('japan', 'male')], calls[-2:])
                player.vehicle = None
                player.fakeServer = None
                before = list(calls)
                setting.setSystemValue('commander')
                self.assertEqual(before, calls)
                setting.clearPreviewSound()
                self.assertEqual('preview-stopped', calls[-1])
                player.fakeServer = object()
                player.arena = None
                before = list(calls)
                setting.setSystemValue('commander')
                self.assertEqual(before, calls)
        finally:
            for owner, name, original in reversed(patches):
                setattr(owner, name, original)
        self.assertIs(Setting.setSystemValue, original_set)
        self.assertIs(Setting.clearPreviewSound, original_clear)


if __name__ == '__main__':
    unittest.main()
