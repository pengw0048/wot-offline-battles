"""Player vehicle property, roster and spawn data for the 2.3.1.2 client.

All schemas here mirror the 2.3.1.2 entity defs: Vehicle.def plus its
implemented interfaces, and the VEHICLES_INFO fixed dict consumed by
ClientArena.updateVehiclesList().
"""
from __future__ import absolute_import

import math

PLAYER_NAME = 'Player'
PLAYER_TEAM = 1
ENEMY_TEAM = 2
PLAYER_SESSION_ID = 'offline_battle'
VEHICLE_PHYSICS_MODE_STANDARD = 1
VEHICLE_SIEGE_STATE_DISABLED = 0
NONE_ROSTER_POSITION = (-32768, -32768)

BASE_SPAWN_FORWARD_METRES = 20.0
SPAWN_BOUNDS_MARGIN_METRES = 8.0
MATURE_CTF_SPAWNS = {
    '01_karelia': ((382.0, 386.0), (-386.0, -386.0)),
}

ROSTER_KEYS = (
    'vehicleID', 'isAlive', 'outfitCD', 'compDescr', 'fakeName', 'name',
    'team', 'isAvatarReady', 'isTeamKiller', 'accountDBID', 'clanAbbrev',
    'clanDBID', 'prebattleID', 'isPrebattleCreator',
    'forbidInBattleInvitations', 'igrType', 'avatarSessionID',
    'overriddenBadge', 'customRoleSlotTypeId', 'botDisplayStatus',
    'teamPanelMode', 'maxHealth', 'prestigeLevel', 'prestigeGradeMarkID',
    'vehPostProgression', 'personalMissionIDs', 'personalMissionInfo',
    'events', 'badges', 'ranked', 'deathInfo', 'respawnID', 'stFrags',
    'frags', 'tkills', 'fogOfWar', 'position', '__generation')

PUBLIC_VEHICLE_INFO_KEYS = (
    'name', 'compDescr', 'outfit', 'outfitLevel', 'index', 'team',
    'prebattleID', 'marksOnGun', 'crewGroups', 'commanderSkinID',
    'maxHealth', 'respawnID', 'stFrags')


def public_vehicle_info(comp_descr, max_health, name=PLAYER_NAME,
                        team=PLAYER_TEAM):
    return {
        'name': name,
        'compDescr': comp_descr,
        'outfit': '',
        'outfitLevel': 0,
        'index': 0,
        'team': team,
        'prebattleID': 0,
        'marksOnGun': 0,
        'crewGroups': [],
        'commanderSkinID': 0,
        'maxHealth': max_health,
        'respawnID': 0,
        'stFrags': 0,
    }


def vehicle_properties(comp_descr, max_health, avatar_id, arena_type_id,
                       arena_bonus_type, name=PLAYER_NAME, team=PLAYER_TEAM):
    """Vehicle.def client property values for BigWorld.createEntity."""
    return {
        'publicInfo': public_vehicle_info(comp_descr, max_health, name, team),
        'health': max_health,
        'isCrewActive': True,
        'isStrafing': False,
        'postmortemViewPointName': '',
        'isHidden': False,
        'physicsMode': VEHICLE_PHYSICS_MODE_STANDARD,
        'siegeState': VEHICLE_SIEGE_STATE_DISABLED,
        'gunAnglesPacked': 0,
        'engineMode': (0, 0),
        'damageStickers': [],
        'publicStateModifiers': [],
        'stunInfo': 0.0,
        'crewCompactDescrs': (),
        'enhancements': {},
        'setups': {},
        'setupsIndexes': {},
        'customRoleSlotTypeId': 0,
        'vehPerks': {},
        'vehPostProgression': [],
        'disabledSwitches': [],
        'avatarID': avatar_id,
        'masterVehID': 0,
        'arenaTypeID': arena_type_id,
        'arenaBonusType': arena_bonus_type,
        'arenaUniqueID': 0,
        'debuff': 0,
        'isSpeedCapturing': False,
        'isBlockingCapture': False,
        'isMyVehicle': True,
        'quickShellChangerFactor': 1.0,
        'onRespawnReloadTimeFactor': 1.0,
        'enableExternalRespawn': False,
        'botDisplayStatus': 0,
        'steeringAngles': (),
        'wheelsScroll': (),
        'wheelsState': 0,
        'burnoutLevel': 0,
        'dotEffect': None,
        'inspiringEffect': None,
        'healingEffect': None,
        'inspired': None,
        'healing': None,
        'healOverTime': None,
        'ownVehiclePosition': None,
        'perkEffects': {'equipment': []},
        'perks': [],
        'perksRibbonNotify': [],
        'dogTag': {
            'dogTag': {'components': []},
            'defaultDogTag': {'components': []},
            'showDogTagToKiller': False,
        },
    }


def roster_entry(vehicle_id, comp_descr, max_health, name=PLAYER_NAME,
                 team=PLAYER_TEAM):
    """One VEHICLES_INFO dict for ClientArena.updateVehiclesList()."""
    return {
        'vehicleID': vehicle_id,
        'isAlive': True,
        'outfitCD': '',
        'compDescr': comp_descr,
        'fakeName': '',
        'name': name,
        'team': team,
        'isAvatarReady': True,
        'isTeamKiller': False,
        'accountDBID': 0,
        'clanAbbrev': '',
        'clanDBID': 0,
        'prebattleID': 0,
        'isPrebattleCreator': False,
        'forbidInBattleInvitations': False,
        'igrType': 0,
        'avatarSessionID': PLAYER_SESSION_ID,
        'overriddenBadge': 0,
        'customRoleSlotTypeId': 0,
        'botDisplayStatus': 0,
        'teamPanelMode': 0,
        'maxHealth': max_health,
        'prestigeLevel': 0,
        'prestigeGradeMarkID': 0,
        'vehPostProgression': [],
        'personalMissionIDs': [],
        'personalMissionInfo': {},
        'events': {},
        'badges': ((), ()),
        'ranked': None,
        'deathInfo': None,
        'respawnID': 0,
        'stFrags': 0,
        'frags': 0,
        'tkills': 0,
        'fogOfWar': 0,
        'position': NONE_ROSTER_POSITION,
        '__generation': 1,
    }


def _vector2_xz(value):
    if value is None:
        return None
    try:
        return float(value.x), float(value.y)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        return float(value[0]), float(value[1])
    except (IndexError, TypeError, ValueError):
        return None


def _first_team_point(team_points):
    if not team_points:
        return None
    if isinstance(team_points, dict):
        for key in sorted(team_points):
            point = _vector2_xz(team_points[key])
            if point is not None:
                return point
        return None
    for value in team_points:
        point = _vector2_xz(value)
        if point is not None:
            return point
    return None


def _team_points(value, team_index):
    try:
        return value[team_index]
    except (IndexError, KeyError, TypeError):
        return None


def spawn_pose(arena_type):
    """Mature CTF spawn rule: proven spawn, then stock spawn, then the base."""
    spawn_points = getattr(arena_type, 'teamSpawnPoints', None)
    base_points = getattr(arena_type, 'teamBasePositions', None)
    geometry_name = getattr(arena_type, 'geometryName', None)
    mature_spawns = MATURE_CTF_SPAWNS.get(geometry_name)
    mature_team_spawn = _vector2_xz(_team_points(mature_spawns, 0))
    team_spawn = (mature_team_spawn or
                  _first_team_point(_team_points(spawn_points, 0)))
    own_base = _first_team_point(_team_points(base_points, 0))
    enemy_base = _first_team_point(_team_points(base_points, 1))

    anchor = team_spawn or own_base
    if anchor is None:
        anchor = (50.0, 50.0)
    x, z = anchor
    heading_anchor = own_base or anchor
    if enemy_base is not None:
        yaw = math.atan2(
            enemy_base[0] - heading_anchor[0],
            enemy_base[1] - heading_anchor[1])
    else:
        yaw = math.atan2(-x, -z)

    source = ('mature_ctf_spawn' if mature_team_spawn is not None else
              'team_spawn')
    if team_spawn is None and own_base is not None:
        source = 'team_base_formation'
        x += math.sin(yaw) * BASE_SPAWN_FORWARD_METRES
        z += math.cos(yaw) * BASE_SPAWN_FORWARD_METRES
    elif team_spawn is None:
        source = 'viewer_fallback'

    bounds = getattr(arena_type, 'boundingBox', None)
    try:
        bottom_left = _vector2_xz(bounds[0])
        upper_right = _vector2_xz(bounds[1])
    except (IndexError, TypeError):
        bottom_left = upper_right = None
    if bottom_left is not None and upper_right is not None:
        x = max(bottom_left[0] + SPAWN_BOUNDS_MARGIN_METRES,
                min(upper_right[0] - SPAWN_BOUNDS_MARGIN_METRES, x))
        z = max(bottom_left[1] + SPAWN_BOUNDS_MARGIN_METRES,
                min(upper_right[1] - SPAWN_BOUNDS_MARGIN_METRES, z))
    return x, z, yaw, source
