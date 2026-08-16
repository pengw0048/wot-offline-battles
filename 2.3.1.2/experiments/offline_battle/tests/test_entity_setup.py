import importlib.util
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'

# Frozen from the unpacked 2.3.1.2 entity defs (scripts.pkg).
VEHICLES_INFO_KEYS = {
    'vehicleID', 'isAlive', 'outfitCD', 'compDescr', 'fakeName', 'name',
    'team', 'isAvatarReady', 'isTeamKiller', 'accountDBID', 'clanAbbrev',
    'clanDBID', 'prebattleID', 'isPrebattleCreator',
    'forbidInBattleInvitations', 'igrType', 'avatarSessionID',
    'overriddenBadge', 'customRoleSlotTypeId', 'botDisplayStatus',
    'teamPanelMode', 'maxHealth', 'prestigeLevel', 'prestigeGradeMarkID',
    'vehPostProgression', 'personalMissionIDs', 'personalMissionInfo',
    'events', 'badges', 'ranked', 'deathInfo', 'respawnID', 'stFrags',
    'frags', 'tkills', 'fogOfWar', 'position', '__generation'}

PUBLIC_VEHICLE_INFO_KEYS = {
    'name', 'compDescr', 'outfit', 'outfitLevel', 'index', 'team',
    'prebattleID', 'marksOnGun', 'crewGroups', 'commanderSkinID',
    'maxHealth', 'respawnID', 'stFrags'}

VEHICLE_CLIENT_PROPERTIES = {
    'remoteCamera', 'steeringAngles', 'wheelsScroll', 'wheelsState',
    'burnoutLevel', 'perkEffects', 'perks', 'perksRibbonNotify',
    'isStrafing', 'postmortemViewPointName', 'isHidden', 'physicsMode',
    'siegeState', 'gunAnglesPacked', 'publicInfo', 'health', 'isCrewActive',
    'engineMode', 'damageStickers', 'publicStateModifiers', 'stunInfo',
    'crewCompactDescrs', 'enhancements', 'setups', 'setupsIndexes',
    'customRoleSlotTypeId', 'vehPerks', 'vehPostProgression',
    'disabledSwitches', 'avatarID', 'masterVehID', 'arenaTypeID',
    'arenaBonusType', 'arenaUniqueID', 'inspiringEffect', 'healingEffect',
    'dotEffect', 'inspired', 'healing', 'healOverTime', 'debuff',
    'isSpeedCapturing', 'isBlockingCapture', 'dogTag', 'isMyVehicle',
    'quickShellChangerFactor', 'onRespawnReloadTimeFactor',
    'ownVehiclePosition', 'enableExternalRespawn', 'botDisplayStatus'}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entity_setup = _load(
    'offline_battle_entity_setup',
    MODS / 'offline_battle_2312' / 'entity_setup.py')
entry = _load('offline_battle_entry', MODS / 'mod_offline_2312_battle.py')


class RosterSchemaTests(unittest.TestCase):
    def test_roster_entry_matches_vehicles_info_schema(self):
        roster = entity_setup.roster_entry(1000, 'cd', 100)
        self.assertEqual(set(roster), VEHICLES_INFO_KEYS)
        self.assertEqual(tuple(sorted(entity_setup.ROSTER_KEYS)),
                         tuple(sorted(VEHICLES_INFO_KEYS)))

    def test_roster_entry_marks_a_real_player(self):
        roster = entity_setup.roster_entry(1000, 'cd', 100)
        self.assertTrue(roster['isAlive'])
        self.assertTrue(roster['avatarSessionID'])
        self.assertEqual(roster['vehicleID'], 1000)
        self.assertEqual(roster['maxHealth'], 100)
        self.assertIsNone(roster['deathInfo'])
        self.assertEqual(roster['position'],
                         entity_setup.NONE_ROSTER_POSITION)

    def test_public_info_matches_alias_schema(self):
        info = entity_setup.public_vehicle_info('cd', 100)
        self.assertEqual(set(info), PUBLIC_VEHICLE_INFO_KEYS)
        self.assertEqual(tuple(sorted(entity_setup.PUBLIC_VEHICLE_INFO_KEYS)),
                         tuple(sorted(PUBLIC_VEHICLE_INFO_KEYS)))

    def test_vehicle_properties_are_valid_def_properties(self):
        properties = entity_setup.vehicle_properties('cd', 100, 5, 1, 1)
        unknown = set(properties) - VEHICLE_CLIENT_PROPERTIES
        self.assertEqual(unknown, set())
        self.assertEqual(set(properties['publicInfo']),
                         PUBLIC_VEHICLE_INFO_KEYS)
        self.assertEqual(properties['health'], 100)
        self.assertTrue(properties['isMyVehicle'])
        self.assertEqual(properties['avatarID'], 5)
        self.assertEqual(properties['physicsMode'],
                         entity_setup.VEHICLE_PHYSICS_MODE_STANDARD)
        # constants.py: VEHICLE_PHYSICS_MODE.STANDARD = 0, DETAILED = 1
        self.assertEqual(entity_setup.VEHICLE_PHYSICS_MODE_STANDARD, 0)
        self.assertEqual(entity_setup.VEHICLE_SIEGE_STATE_DISABLED, 0)


class SpawnPoseTests(unittest.TestCase):
    class _ArenaType(object):
        geometryName = '01_karelia'
        gameplayName = 'ctf'
        teamSpawnPoints = [[(10.0, 20.0)], [(-10.0, -20.0)]]
        teamBasePositions = [{1: (300.0, 300.0)}, {1: (-300.0, -300.0)}]
        boundingBox = ((-500.0, -500.0), (500.0, 500.0))

    def test_karelia_uses_mature_spawn(self):
        x, z, yaw, source = entity_setup.spawn_pose(self._ArenaType())
        self.assertEqual(source, 'mature_ctf_spawn')
        self.assertAlmostEqual(x, 382.0)
        self.assertAlmostEqual(z, 386.0)
        expected_yaw = math.atan2(-300.0 - 300.0, -300.0 - 300.0)
        self.assertAlmostEqual(yaw, expected_yaw)

    def test_unknown_map_uses_stock_spawn(self):
        arena_type = self._ArenaType()
        arena_type.geometryName = '99_unknown'
        x, z, yaw, source = entity_setup.spawn_pose(arena_type)
        self.assertEqual(source, 'team_spawn')
        self.assertAlmostEqual(x, 10.0)
        self.assertAlmostEqual(z, 20.0)


class ParseRequestTests(unittest.TestCase):
    def test_activation(self):
        argv = ['', 'offlineBattle', 'offline', 'spaces/01_karelia']
        self.assertEqual(entry.parse_request(argv),
                         ('spaces/01_karelia', '01_karelia'))

    def test_missing_token(self):
        argv = ['', 'offline', 'spaces/01_karelia']
        self.assertIsNone(entry.parse_request(argv))

    def test_rejects_nested_path(self):
        argv = ['', 'offlineBattle', 'offline', 'spaces/a/b']
        self.assertIsNone(entry.parse_request(argv))




class NeutralAuxPhysicsTests(unittest.TestCase):
    """Probe-based calibration against a synthetic packer of the same shape
    as the client's: contiguous unsigned fields, value 0 meaning minimum."""

    LAYOUT = (
        (0, 10, -math.pi, math.pi),
        (10, 8, -math.pi / 2, math.pi / 2),
        (18, 10, -math.pi, math.pi),
        (28, 8, -15.0, 15.0),
        (36, 8, -15.0, 15.0),
        (44, 8, 0.0, 4000.0),
    )

    def _unpack(self, value):
        result = []
        for shift, bits, low, high in self.LAYOUT:
            code = value >> shift & (1 << bits) - 1
            result.append(low + (high - low) * code / float((1 << bits) - 1))
        return tuple(result)

    def test_zero_decodes_to_field_minimums(self):
        self.assertEqual(self._unpack(0),
                         tuple(low for _, _, low, _ in self.LAYOUT))

    def test_neutral_value_decodes_close_to_zero(self):
        value, overlaps = entity_setup.neutral_aux_physics_data(self._unpack)
        self.assertEqual(overlaps, 0)
        decoded = self._unpack(value)
        for index, (_, bits, low, high) in enumerate(self.LAYOUT):
            step = (high - low) / float((1 << bits) - 1)
            self.assertLessEqual(abs(decoded[index]), step,
                                 'field %d decoded %s' % (index, decoded[index]))

    def test_neutral_value_is_not_zero(self):
        value, _ = entity_setup.neutral_aux_physics_data(self._unpack)
        self.assertNotEqual(value, 0)

    def test_calibration_survives_unresolvable_low_bits(self):
        """Float output can hide a field's least significant bits; the
        composition must still land as close to neutral as it can."""
        layout = self.LAYOUT

        def coarse_unpack(value):
            decoded = list(self._unpack(value))
            decoded[0] = round(decoded[0], 2)
            return tuple(decoded)

        value, overlaps = entity_setup.neutral_aux_physics_data(coarse_unpack)
        self.assertEqual(overlaps, 0)
        yaw = self._unpack(value)[0]
        step = (layout[0][3] - layout[0][2]) / float((1 << layout[0][1]) - 1)
        self.assertLessEqual(abs(yaw), 0.02 + step)


if __name__ == '__main__':
    unittest.main()
