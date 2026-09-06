"""Durable offline battle receipts and exact #1513 result packing.

This file owns ``postbattle_state.json``.  The launcher may preserve this file
when repairing startup, but a full offline-data reset must remove it.  Raw LAN
receipts stay JSON-only; native compact result lists are created on demand by
the exact client ``battle_results_shared`` packers and are never persisted.

Only an unacknowledged receipt is persisted in full.  Once #1513 has cached a
result and confirmed it with 1501, the archived row keeps its identity so a
retried delivery is still applied exactly once, but its body lives only in
this process.  Re-opening a result from the notification list therefore works
for the whole session and not across a restart, which is the explicit product
choice; account and per-vehicle progress, including medal counts, is carried
by ``progress`` and does survive.
"""

from __future__ import print_function

import copy
import json
import os
import time
import uuid
import zlib

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922.battle_achievements import (
    AWARDABLE_ACHIEVEMENTS, RECEIPT_STAT_NAMES)


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


SCHEMA = 1
STATE_PATH = os.path.join(
    port_config.USER_DATA_DIR, 'postbattle_state.json')
LEGACY_STATE_PATH = os.path.join(
    port_config.LEGACY_USER_DATA_DIR, 'postbattle_state.json')
MAX_HISTORY = 256
INTERACTION_FIELDS = (
    ('spotted', 'spotted', 0, 1),
    ('death_reason', 'deathReason', -1, 10),
    ('direct_hits', 'directHits', 0, 65535),
    ('explosion_hits', 'explosionHits', 0, 65535),
    ('piercings', 'piercings', 0, 65535),
    ('damage', 'damageDealt', 0, 65535),
    ('assist_track', 'damageAssistedTrack', 0, 65535),
    ('assist_radio', 'damageAssistedRadio', 0, 65535),
    ('assist_stun', 'damageAssistedStun', 0, 65535),
    ('crits', 'crits', 0, 4294967295),
    ('fire', 'fire', 0, 65535),
    ('stun_num', 'stunNum', 0, 65535),
    ('stun_duration', 'stunDuration', 0, 65535),
    ('damage_blocked', 'damageBlockedByArmor', 0, 4294967295),
    ('damage_received', 'damageReceived', 0, 65535),
    ('ricochets_received', 'rickochetsReceived', 0, 65535),
    ('no_damage_direct_hits_received', 'noDamageDirectHitsReceived',
     0, 65535),
    ('target_kills', 'targetKills', 0, 255),
)


def _int(value, default=0):
    if isinstance(value, bool):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _bounded_text(value, limit):
    if value is None:
        return ''
    try:
        value = unicode(value)
    except NameError:
        value = str(value)
    return value[:limit]


def _wire_utf8(value):
    """Return a Python-2 ``str`` for stock packer string fields."""
    try:
        text_type = unicode
    except NameError:
        text_type = str
    if isinstance(value, bytes):
        return value
    if isinstance(value, text_type):
        return value.encode('utf-8')
    return str(value)


def _receipt(value):
    """Return one bounded canonical receipt or raise ValueError."""
    if not isinstance(value, dict):
        raise ValueError('battle receipt must be an object')
    required = ('receipt_id', 'arena_unique_id', 'round_id', 'account_key',
                'player_name', 'vehicle', 'team', 'winner', 'map', 'stats',
                'rewards')
    if any(name not in value for name in required):
        raise ValueError('battle receipt is incomplete')
    receipt_id = _bounded_text(value.get('receipt_id'), 96)
    account_key = _bounded_text(value.get('account_key'), 64)
    player_name = _bounded_text(value.get('player_name'), 32)
    vehicle = _bounded_text(value.get('vehicle'), 96)
    map_name = _bounded_text(value.get('map'), 96)
    if not all((receipt_id, account_key, player_name, vehicle, map_name)):
        raise ValueError('battle receipt identity is empty')
    arena_unique_id = _int(value.get('arena_unique_id'), -1)
    round_id = _int(value.get('round_id'), -1)
    player_id = _int(value.get('player_id'), 1)
    team = _int(value.get('team'), -1)
    winner = _int(value.get('winner'), -1)
    if (arena_unique_id < 0 or round_id < 1 or player_id < 1 or
            team not in (1, 2)):
        raise ValueError('battle receipt identity is invalid')
    if winner not in (0, 1, 2):
        raise ValueError('battle receipt winner is invalid')
    raw_stats = value.get('stats')
    raw_rewards = value.get('rewards')
    if not isinstance(raw_stats, dict) or not isinstance(raw_rewards, dict):
        raise ValueError('battle receipt summary is invalid')
    stats = dict((name, max(0, _int(raw_stats.get(name))))
                 for name in RECEIPT_STAT_NAMES)
    rewards = dict((name, max(0, _int(raw_rewards.get(name)))) for name in (
        'credits', 'xp', 'free_xp', 'repair_cost', 'ammo_cost'))
    # Offline battles never debit service costs.  Rejecting a positive debit
    # is safer than silently applying an untrusted server value.
    if rewards['repair_cost'] or rewards['ammo_cost']:
        raise ValueError('offline service costs must be zero')
    public_results = []
    raw_public = value.get('public_results')
    if raw_public is None:
        # Schema-1 state written before complete team results remains readable.
        raw_public = [{
            'actor_kind': 'player', 'actor_id': player_id,
            'name': player_name, 'vehicle': vehicle, 'team': team,
            'health': 0,
            'death_reason': _int(value.get('death_reason'), -1),
            'killer_kind': '', 'killer_id': 0,
            'is_team_killer': False, 'xp': rewards['xp'],
            'stats': stats,
        }]
    if (not isinstance(raw_public, (list, tuple)) or
            not 1 <= len(raw_public) <= 30):
        raise ValueError('battle receipt public roster is invalid')
    seen = set()
    for raw in raw_public:
        if not isinstance(raw, dict):
            raise ValueError('battle receipt public row is invalid')
        actor_kind = _bounded_text(raw.get('actor_kind'), 8)
        actor_id = _int(raw.get('actor_id'), -1)
        identity = (actor_kind, actor_id)
        row_name = _bounded_text(raw.get('name'), 32)
        row_vehicle = _bounded_text(raw.get('vehicle'), 96)
        row_team = _int(raw.get('team'), -1)
        health = _int(raw.get('health'), -1)
        death_reason = _int(raw.get('death_reason'), -2)
        xp = _int(raw.get('xp'), -1)
        killer_kind = _bounded_text(raw.get('killer_kind'), 8)
        killer_id = _int(raw.get('killer_id'), -1)
        raw_row_stats = raw.get('stats')
        if (actor_kind not in ('player', 'bot') or actor_id < 1 or
                identity in seen or not row_name or not row_vehicle or
                row_team not in (1, 2) or health < 0 or xp < 0 or
                not -1 <= death_reason <= 255 or
                killer_kind not in ('', 'player', 'bot') or killer_id < 0 or
                bool(killer_kind) != bool(killer_id) or
                not isinstance(raw.get('is_team_killer'), bool) or
                not isinstance(raw_row_stats, dict)):
            raise ValueError('battle receipt public row is invalid')
        row_stats = dict((name, max(0, _int(raw_row_stats.get(name))))
                         for name in stats)
        # Receipts stored before offline achievements shipped have no list.
        raw_achievements = raw.get('achievements', [])
        if (not isinstance(raw_achievements, (list, tuple)) or
                len(raw_achievements) > len(AWARDABLE_ACHIEVEMENTS) or
                len(set(raw_achievements)) != len(raw_achievements) or
                any(name not in AWARDABLE_ACHIEVEMENTS
                    for name in raw_achievements)):
            raise ValueError('battle receipt achievements are invalid')
        public_results.append({
            'actor_kind': actor_kind, 'actor_id': actor_id,
            'name': row_name, 'vehicle': row_vehicle, 'team': row_team,
            'health': health, 'death_reason': death_reason,
            'killer_kind': killer_kind, 'killer_id': killer_id,
            'is_team_killer': raw['is_team_killer'], 'xp': xp,
            'stats': row_stats,
            'achievements': sorted(raw_achievements),
        })
        seen.add(identity)
    personal_rows = [row for row in public_results
                     if (row['actor_kind'], row['actor_id']) ==
                     ('player', player_id)]
    if not personal_rows:
        raise ValueError('battle receipt has no personal public row')
    personal = personal_rows[0]
    if (personal['name'] != player_name or personal['vehicle'] != vehicle or
            personal['team'] != team or personal['death_reason'] != max(
                -1, min(_int(value.get('death_reason'), -1), 255)) or
            personal['xp'] != rewards['xp'] or personal['stats'] != stats):
        raise ValueError('battle receipt personal public row is inconsistent')
    public_by_identity = dict(
        ((row['actor_kind'], row['actor_id']), row)
        for row in public_results)
    interactions = []
    raw_interactions = value.get('interactions', [])
    if (not isinstance(raw_interactions, (list, tuple)) or
            len(raw_interactions) > len(public_results)):
        raise ValueError('battle receipt interaction details are invalid')
    interaction_keys = set(field[0] for field in INTERACTION_FIELDS) | {
        'target_kind', 'target_id'}
    interaction_targets = set()
    for raw in raw_interactions:
        if not isinstance(raw, dict) or set(raw) != interaction_keys:
            raise ValueError('battle receipt interaction row is invalid')
        target = (
            _bounded_text(raw.get('target_kind'), 8),
            _int(raw.get('target_id'), -1))
        target_public = public_by_identity.get(target)
        if (target_public is None or target in interaction_targets or
                target == ('player', player_id) or
                target_public['team'] == team):
            raise ValueError('battle receipt interaction target is invalid')
        interaction = {
            'target_kind': target[0], 'target_id': target[1],
        }
        for field_name, unused_native, minimum, maximum in INTERACTION_FIELDS:
            raw_value = raw.get(field_name)
            if (isinstance(raw_value, bool) or
                    not isinstance(raw_value, integer_types) or
                    raw_value < minimum or raw_value > maximum):
                raise ValueError(
                    'battle receipt interaction value is invalid')
            interaction[field_name] = int(raw_value)
        interactions.append(interaction)
        interaction_targets.add(target)
    return {
        'receipt_id': receipt_id,
        'arena_unique_id': arena_unique_id,
        'round_id': round_id,
        'player_id': player_id,
        'account_key': account_key,
        'player_name': player_name,
        'vehicle': vehicle,
        'team': team,
        'winner': winner,
        'map': map_name,
        'finish_reason': _int(value.get('finish_reason'), 1),
        'death_reason': max(-1, min(
            _int(value.get('death_reason'), -1), 255)),
        'duration': max(0, _int(value.get('duration'))),
        'premature_leave': bool(value.get('premature_leave', False)),
        'stats': stats,
        'rewards': rewards,
        # The personal row owns the medal list; mirroring it here keeps the
        # durable progress transaction from re-deriving the roster.
        'achievements': list(personal['achievements']),
        'public_results': public_results,
        'interactions': interactions,
    }


def _compressed(value):
    return zlib.compress(_pickle.dumps(value, _pickle.HIGHEST_PROTOCOL))


def _vehicle_type_compact_descr(type_name):
    from items import vehicles
    descriptor = vehicles.VehicleDescr(typeName=str(type_name))
    nation_id, vehicle_type_id = descriptor.type.id
    return int(vehicles.makeIntCompactDescrByID(
        'vehicle', nation_id, vehicle_type_id))


def _arena_type_id(geometry_name):
    import ArenaType
    items = getattr(ArenaType.g_cache, 'iteritems', ArenaType.g_cache.items)
    for arena_type_id, arena_type in items():
        if (getattr(arena_type, 'geometryName', None) == geometry_name and
                getattr(arena_type, 'gameplayName', None) == 'ctf'):
            return int(arena_type_id)
    return 0


def _add_value_replays(packers, vehicle, replay_types=None):
    """Populate the non-empty replay chains consumed by the #1513 UI."""
    if replay_types is None:
        from ValueReplay import ValueReplay, ValueReplayConnector
    else:
        ValueReplay, ValueReplayConnector = replay_types
    connector = ValueReplayConnector(packers.VEH_FULL_RESULTS, vehicle)
    for record_name, start_name, result_name in (
            ('credits', 'originalCredits', 'creditsReplay'),
            ('xp', 'originalXP', 'xpReplay'),
            ('freeXP', 'originalFreeXP', 'freeXPReplay'),
            ('gold', 'originalGold', 'goldReplay'),
            ('crystal', 'originalCrystal', 'crystalReplay')):
        replay = ValueReplay(
            connector, recordName=record_name, startRecordName=start_name)
        vehicle[result_name] = replay.pack()


def _pack_interaction_details(receipt, vehicle_ids, vehicle_type_cds,
                              interaction_details_type=None):
    """Pack receipt rows with #1513's native interaction serializer."""
    if not receipt['interactions']:
        return None
    if interaction_details_type is None:
        from battle_results_shared import VehicleInteractionDetails
        interaction_details_type = VehicleInteractionDetails
    details = interaction_details_type([], [])
    for interaction in receipt['interactions']:
        identity = (
            interaction['target_kind'], interaction['target_id'])
        record = details[(
            vehicle_ids[identity], vehicle_type_cds[identity])]
        for field_name, native_name, unused_minimum, unused_maximum in (
                INTERACTION_FIELDS):
            record[native_name] = interaction[field_name]
    return details.pack()


def _achievement_counts(value):
    """Return one medal-counter map safe to hand the #1513 dossier builder.

    Persisted progress is optional state a player can edit or a partial write
    can corrupt.  Only names this build awards survive, and only as positive
    whole counts, because the native dossier block coerces with ``int`` and a
    bad value there breaks the account snapshot the garage is built from.
    """
    if not isinstance(value, dict):
        return {}
    counts = {}
    for name in AWARDABLE_ACHIEVEMENTS:
        count = value.get(name)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            continue
        counts[name] = count
    return counts


def _achievement_records(names, record_db_ids=None):
    """Return sorted ``(name, database id)`` pairs for awarded medals.

    A name the pinned client does not register is dropped rather than packed;
    the results window indexes ``DB_ID_TO_RECORD`` and would raise on it.
    """
    if not names:
        return []
    if record_db_ids is None:
        from dossiers2.custom.records import RECORD_DB_IDS
        record_db_ids = RECORD_DB_IDS
    result = []
    for name in sorted(names):
        db_id = record_db_ids.get(('achievements', name))
        if db_id is not None:
            result.append((name, int(db_id)))
    return sorted(result, key=lambda row: row[1])


def _achievement_db_ids(names, record_db_ids=None):
    return [db_id for unused_name, db_id in _achievement_records(
        names, record_db_ids=record_db_ids)]


def pack_battle_result(receipt, packers=None, replay_types=None,
                       interaction_details_type=None, record_db_ids=None,
                       achievement_counts=None):
    """Build the four-tuple consumed by #1513 ``BattleResultsCache``.

    Every compact list comes from the stock packer.  Supplying ``packers`` is
    only a test seam for proving which packer receives which stock field names.
    ``achievement_counts`` carries the account's post-battle total for each
    medal, which #1513 renders as the counter on the results badge.
    """
    receipt = _receipt(receipt)
    if packers is None:
        import battle_results_shared as packers
    counts = achievement_counts if isinstance(achievement_counts, dict) else {}
    stats = receipt['stats']
    rewards = receipt['rewards']
    account_dbid = 1
    vehicle_type_cd = _vehicle_type_compact_descr(receipt['vehicle'])
    won = receipt['winner'] == receipt['team']
    vehicle = {
        'accountDBID': account_dbid,
        'typeCompDescr': vehicle_type_cd,
        'team': receipt['team'],
        'shots': stats['shots'],
        'directHits': stats['direct_hits'],
        'piercings': stats['piercings'],
        'damageDealt': stats['damage'],
        'damageReceived': stats['damage_received'],
        'damageBlockedByArmor': stats['damage_blocked'],
        'damageAssistedTrack': stats['assist_track'],
        'damageAssistedRadio': stats['assist_radio'],
        'damageAssistedStun': stats['assist_stun'],
        'kills': stats['kills'],
        'spotted': stats['spotted'],
        'capturePoints': stats['capture_points'],
        'droppedCapturePoints': stats['dropped_capture_points'],
        'directHitsReceived': stats['hits_received'],
        'potentialDamageReceived': stats['potential_damage_received'],
        'deathReason': receipt['death_reason'],
        'killerID': 0,
        'credits': rewards['credits'],
        'originalCredits': rewards['credits'],
        'factualCredits': rewards['credits'],
        'subtotalCredits': rewards['credits'],
        'xp': rewards['xp'],
        'originalXP': rewards['xp'],
        'factualXP': rewards['xp'],
        'subtotalXP': rewards['xp'],
        'freeXP': rewards['free_xp'],
        'originalFreeXP': rewards['free_xp'],
        'factualFreeXP': rewards['free_xp'],
        'subtotalFreeXP': rewards['free_xp'],
        'gold': 0,
        'originalGold': 0,
        'crystal': 0,
        'originalCrystal': 0,
        'creditsToDraw': 0,
        'originalCreditsToDraw': 0,
        'autoRepairCost': 0,
        'autoLoadCost': (0, 0),
        'autoEquipCost': (0, 0, 0),
        'isPrematureLeave': receipt['premature_leave'],
        'watchedBattleToTheEnd': not receipt['premature_leave'],
        'isTeamKiller': False,
    }
    avatar = {
        'accountDBID': account_dbid, 'team': receipt['team'],
        'credits': rewards['credits'], 'xp': rewards['xp'],
        'freeXP': rewards['free_xp'], 'crystal': 0,
        # These are damage and kills caused by the avatar outside its
        # vehicles.  The stock result model adds them to the per-vehicle
        # totals, so mirroring vehicle statistics here doubles both columns.
        'avatarDamageDealt': 0,
        'avatarKills': 0,
        'isPrematureLeave': receipt['premature_leave'],
        'watchedBattleToTheEnd': not receipt['premature_leave'],
    }
    common = {
        'arenaTypeID': _arena_type_id(receipt['map']),
        # #1513 constants.getArenaStartTime reads the low 32 bits of the
        # arenaUniqueID; keep the duplicated field identical.
        'arenaCreateTime': int(receipt['arena_unique_id'] & 0xffffffff),
        'winnerTeam': receipt['winner'],
        'finishReason': receipt['finish_reason'],
        'duration': receipt['duration'],
        'bonusType': 1,
        'guiType': 1,
        'bots': {},
    }
    _add_value_replays(packers, vehicle, replay_types=replay_types)
    avatar_packed = packers.AVATAR_FULL_RESULTS.pack(avatar)
    personal_identity = ('player', receipt['player_id'])
    rows = list(receipt['public_results'])
    rows.sort(key=lambda row: (
        0 if (row['actor_kind'], row['actor_id']) == personal_identity else 1,
        row['team'], 0 if row['actor_kind'] == 'player' else 1,
        row['actor_id']))
    identity_to_vehicle_id = {}
    identity_to_account_dbid = {}
    identity_to_vehicle_cd = {}
    for index, row in enumerate(rows):
        identity = (row['actor_kind'], row['actor_id'])
        # The #1513 main team iterator requires a positive account DBID.
        # These process-local IDs project the server's disjoint actor identity;
        # they do not claim to be retail account database values.
        projected_id = index + 1
        identity_to_account_dbid[identity] = projected_id
        identity_to_vehicle_id[identity] = projected_id
        identity_to_vehicle_cd[identity] = (
            vehicle_type_cd if identity == personal_identity else
            _vehicle_type_compact_descr(row['vehicle']))

    packed_details = _pack_interaction_details(
        receipt, identity_to_vehicle_id, identity_to_vehicle_cd,
        interaction_details_type=interaction_details_type)
    if packed_details is not None:
        vehicle['details'] = packed_details

    personal_public = next(
        row for row in rows
        if (row['actor_kind'], row['actor_id']) == personal_identity)
    vehicle['health'] = personal_public['health']
    vehicle['killerID'] = identity_to_vehicle_id.get((
        personal_public['killer_kind'], personal_public['killer_id']), 0)
    vehicle['isTeamKiller'] = personal_public['is_team_killer']
    # ``achievements`` drives the team table ribbon; ``dossierPopUps`` drives
    # the personal results medals, and #1513 shows its value as the counter.
    vehicle['achievements'] = _achievement_db_ids(
        personal_public['achievements'], record_db_ids=record_db_ids)
    vehicle['dossierPopUps'] = [
        (db_id, max(1, _int(counts.get(name, 1), 1)))
        for name, db_id in _achievement_records(
            personal_public['achievements'], record_db_ids=record_db_ids)]
    vehicle_full_packed = packers.VEH_FULL_RESULTS.pack(vehicle)

    players = {}
    vehicles = {}
    avatars = {}
    for row in rows:
        identity = (row['actor_kind'], row['actor_id'])
        projected_account = identity_to_account_dbid[identity]
        projected_vehicle = identity_to_vehicle_id[identity]
        row_stats = row['stats']
        row_vehicle_cd = identity_to_vehicle_cd[identity]
        killer_identity = (row['killer_kind'], row['killer_id'])
        public_vehicle = {
            'accountDBID': projected_account,
            'typeCompDescr': row_vehicle_cd,
            'team': row['team'],
            'health': row['health'],
            'shots': row_stats['shots'],
            'directHits': row_stats['direct_hits'],
            'piercings': row_stats['piercings'],
            'damageDealt': row_stats['damage'],
            'damageReceived': row_stats['damage_received'],
            'damageBlockedByArmor': row_stats['damage_blocked'],
            'damageAssistedTrack': row_stats['assist_track'],
            'damageAssistedRadio': row_stats['assist_radio'],
            'damageAssistedStun': row_stats['assist_stun'],
            'kills': row_stats['kills'],
            'spotted': row_stats['spotted'],
            'capturePoints': row_stats['capture_points'],
            'droppedCapturePoints': row_stats['dropped_capture_points'],
            'directHitsReceived': row_stats['hits_received'],
            'potentialDamageReceived': row_stats['potential_damage_received'],
            'deathReason': row['death_reason'],
            'killerID': identity_to_vehicle_id.get(killer_identity, 0),
            'isTeamKiller': row['is_team_killer'],
            'xp': row['xp'],
            # #1513 renders one medal ribbon per team row from this list.
            'achievements': _achievement_db_ids(
                row['achievements'], record_db_ids=record_db_ids),
        }
        public_avatar = {
            # Regular offline battles have no avatar-only combat.  Team
            # results add these fields to VEH_PUBLIC_RESULTS, so keep the
            # vehicle-owned damage and kills in exactly one native block.
            'avatarDamageDealt': 0,
            'avatarKills': 0,
        }
        player = {
            'name': _wire_utf8(row['name']),
            'clanDBID': 0, 'clanAbbrev': '',
            'prebattleID': 0, 'team': row['team'], 'igrType': 0,
        }
        players[projected_account] = packers.PLAYER_INFO.pack(player)
        vehicles[projected_vehicle] = {
            row_vehicle_cd: packers.VEH_PUBLIC_RESULTS.pack(public_vehicle),
        }
        avatars[projected_account] = packers.AVATAR_PUBLIC_RESULTS.pack(
            public_avatar)
        if row['actor_kind'] == 'bot':
            common['bots'][projected_vehicle] = (
                row_vehicle_cd, _wire_utf8(row['name']))

    common_packed = packers.COMMON_RESULTS.pack(common)
    public = (
        common_packed,
        players,
        vehicles,
        avatars,
    )
    return (
        receipt['arena_unique_id'],
        _compressed(avatar_packed),
        _compressed({vehicle_type_cd: vehicle_full_packed}),
        _compressed(public),
    )


class PostBattleStore(object):
    """Apply each LAN receipt once and retain it until the native 1501 ack."""

    def __init__(self, path=STATE_PATH):
        self._path = (port_config.migrate_legacy_user_file(
            path, LEGACY_STATE_PATH) if path == STATE_PATH else path)
        self._account_key = uuid.uuid4().hex
        self._pending = {}
        self._history = []
        self._progress = self._empty_progress()
        self._progress_applier = None
        self._load()

    def set_progress_applier(self, callback):
        """Bind the garage-owned, idempotent crew-XP transaction."""
        if callback is not None and not callable(callback):
            raise TypeError('postbattle progress applier must be callable')
        self._progress_applier = callback

    @staticmethod
    def _empty_progress():
        return {
            'credits': 0, 'freeXP': 0, 'battles': 0, 'wins': 0,
            'losses': 0,
            'damage': 0, 'kills': 0, 'achievements': {}, 'vehicles': {},
        }

    @property
    def account_key(self):
        return self._account_key

    def progress(self):
        return json.loads(json.dumps(self._progress))

    def pending_arenas(self):
        return sorted(int(key) for key in self._pending)

    def latest_archived_arena(self):
        """Return one replayable result for rebuilding the process-local UI."""
        for receipt in reversed(self._history):
            if 'account_key' in receipt:
                arena_unique_id = _int(receipt.get('arena_unique_id'), -1)
                return arena_unique_id if arena_unique_id >= 0 else None
        return None

    def accept(self, value):
        receipt = _receipt(value)
        if receipt['account_key'] != self._account_key:
            raise ValueError('battle receipt belongs to another account')
        receipt_id = receipt['receipt_id']
        if any(row.get('receipt_id') == receipt_id for row in self._history):
            return False
        if any(row.get('receipt_id') == receipt_id
               for row in self._pending.values()):
            return False
        arena_key = str(receipt['arena_unique_id'])
        if arena_key in self._pending:
            raise ValueError('arena already has a different receipt')
        policy = {}
        if self._progress_applier is not None:
            policy = self._progress_applier(receipt) or {}
        previous = self._snapshot()
        self._pending[arena_key] = receipt
        self._apply_progress(
            receipt, vehicle_xp=(0 if policy.get('accelerated') else
                                 receipt['rewards']['xp']))
        try:
            self._save()
        except Exception:
            self._restore(previous)
            raise
        return True

    def result(self, arena_unique_id, packers=None, replay_types=None,
               interaction_details_type=None, record_db_ids=None):
        arena_unique_id = _int(arena_unique_id, -1)
        receipt = self._pending.get(str(arena_unique_id))
        if receipt is None:
            for archived in reversed(self._history):
                if ('account_key' in archived and
                        _int(archived.get('arena_unique_id'), -2) ==
                        arena_unique_id):
                    receipt = archived
                    break
        if receipt is None:
            return None
        return pack_battle_result(
            receipt, packers=packers, replay_types=replay_types,
            interaction_details_type=interaction_details_type,
            record_db_ids=record_db_ids,
            achievement_counts=self._progress.get('achievements', {}))

    def service_message_data(self, arena_unique_id):
        """Return the exact BattleResultsFormatter input summary."""
        arena_unique_id = _int(arena_unique_id, -1)
        receipt = self._pending.get(str(arena_unique_id))
        if receipt is None:
            for archived in reversed(self._history):
                if ('account_key' in archived and
                        _int(archived.get('arena_unique_id'), -2) ==
                        arena_unique_id):
                    receipt = archived
                    break
        if receipt is None:
            return None
        rewards = receipt['rewards']
        vehicle_type_cd = _vehicle_type_compact_descr(receipt['vehicle'])
        winner = receipt['winner']
        result_key = (0 if winner == 0 else
                      (1 if winner == receipt['team'] else -1))
        return {
            'arenaTypeID': _arena_type_id(receipt['map']),
            'arenaCreateTime': int(
                receipt['arena_unique_id'] & 0xffffffff),
            'playerVehicles': {vehicle_type_cd: {}},
            'xp': rewards['xp'], 'credits': rewards['credits'],
            'crystal': 0, 'creditsToDraw': 0,
            'isWinner': result_key, 'team': receipt['team'],
            'winnerIfDraw': 0, 'guiType': 1,
            'arenaUniqueID': receipt['arena_unique_id'],
        }

    def should_show_immediately(self, arena_unique_id):
        """Whether this result came from a battle watched to its end."""
        arena_unique_id = _int(arena_unique_id, -1)
        receipt = self._pending.get(str(arena_unique_id))
        if receipt is None:
            for archived in reversed(self._history):
                if ('account_key' in archived and
                        _int(archived.get('arena_unique_id'), -2) ==
                        arena_unique_id):
                    receipt = archived
                    break
        return bool(receipt is not None and
                    not receipt.get('premature_leave', False))

    def acknowledge(self, arena_unique_id):
        key = str(_int(arena_unique_id, -1))
        receipt = self._pending.get(key)
        if receipt is None:
            return any(_int(row.get('arena_unique_id'), -2) == _int(
                arena_unique_id, -1) for row in self._history)
        previous = self._snapshot()
        # #1513 clears its own disk cache on every process start.  Retain the
        # bounded canonical receipt after 1501 so a later 1500 can rebuild it.
        self._history.append(receipt)
        self._history = self._history[-MAX_HISTORY:]
        del self._pending[key]
        try:
            self._save()
        except Exception:
            self._restore(previous)
            raise
        return True

    def _apply_progress(self, receipt, vehicle_xp=None):
        rewards = receipt['rewards']
        stats = receipt['stats']
        progress = self._progress
        progress['credits'] += rewards['credits']
        progress['freeXP'] += rewards['free_xp']
        progress['battles'] += 1
        progress['wins'] += int(receipt['winner'] == receipt['team'])
        progress['losses'] = int(progress.get('losses', 0)) + int(
            receipt['winner'] in (1, 2) and
            receipt['winner'] != receipt['team'])
        progress['damage'] += stats['damage']
        progress['kills'] += stats['kills']
        vehicles = progress['vehicles']
        row = vehicles.setdefault(receipt['vehicle'], {
            'xp': 0, 'battles': 0, 'wins': 0, 'losses': 0,
            'damage': 0, 'kills': 0, 'achievements': {},
        })
        if vehicle_xp is None:
            vehicle_xp = rewards['xp']
        row['xp'] += max(0, _int(vehicle_xp))
        row['battles'] += 1
        row['wins'] += int(receipt['winner'] == receipt['team'])
        row['losses'] = int(row.get('losses', 0)) + int(
            receipt['winner'] in (1, 2) and
            receipt['winner'] != receipt['team'])
        row['damage'] += stats['damage']
        row['kills'] += stats['kills']
        # #1513 counts every medal in both the account and the vehicle
        # dossier; the results badge renders the account total.
        account_medals = _achievement_counts(progress.get('achievements'))
        vehicle_medals = _achievement_counts(row.get('achievements'))
        progress['achievements'] = account_medals
        row['achievements'] = vehicle_medals
        for name in receipt['achievements']:
            account_medals[name] = account_medals.get(name, 0) + 1
            vehicle_medals[name] = vehicle_medals.get(name, 0) + 1
        for target_name, source_name in (
                ('shots', 'shots'), ('directHits', 'direct_hits'),
                ('piercings', 'piercings'), ('spotted', 'spotted'),
                ('damageReceived', 'damage_received'),
                ('damageBlockedByArmor', 'damage_blocked'),
                ('damageAssistedTrack', 'assist_track'),
                ('damageAssistedRadio', 'assist_radio'),
                ('damageAssistedStun', 'assist_stun'),
                ('capturePoints', 'capture_points'),
                ('droppedCapturePoints', 'dropped_capture_points')):
            row[target_name] = int(row.get(target_name, 0)) + int(
                stats[source_name])
        row['survivedBattles'] = int(row.get(
            'survivedBattles', 0)) + int(
                receipt['death_reason'] < 0 and
                not receipt['premature_leave'])
        # DossierCache asks for rows newer than its maxChangeTime.  The global
        # battle ordinal is stable across restarts and strictly increases.
        row['changeTime'] = progress['battles']

    def _snapshot(self):
        """Capture enough state to undo one failed durable transaction.

        The server ships settlement inside the terminal round barrier, so
        ``accept`` runs in a BigWorld callback at the instant the victory
        condition is met.  A JSON round-trip of the whole store cost time
        proportional to the archived history there -- hundreds of
        milliseconds of visible freeze on a saturated profile -- while
        neither ``accept`` nor ``acknowledge`` mutates an archived or pending
        receipt in place.  Copy the two containers, not their rows, and deep
        copy only the counters ``_apply_progress`` edits in place.
        """
        return {
            'pending': dict(self._pending),
            'history': list(self._history),
            'progress': copy.deepcopy(self._progress),
        }

    def _restore(self, value):
        self._pending = value['pending']
        self._history = value['history']
        self._progress = value['progress']

    def _load(self):
        if self._path is None or not os.path.isfile(self._path):
            return
        try:
            with open(self._path, 'rb') as stream:
                value = json.load(stream)
            if not isinstance(value, dict) or value.get('schema') != SCHEMA:
                return
            account_key = _bounded_text(value.get('accountKey'), 64)
            if not account_key:
                return
            pending = {}
            for raw in value.get('pending', ()):
                receipt = _receipt(raw)
                if receipt['account_key'] != account_key:
                    return
                pending[str(receipt['arena_unique_id'])] = receipt
            history = []
            for raw in value.get('history', ()):
                # An archived row is persisted as its identity alone.  That
                # is a complete dedupe mark for a retried delivery, and it
                # deliberately cannot reconstruct a result compact
                # descriptor.  Files written by an older build carry the
                # whole receipt here; keep honouring those bodies.
                if isinstance(raw, dict) and all(
                        name in raw for name in ('receipt_id',
                                                'arena_unique_id')):
                    if 'account_key' in raw:
                        raw = _receipt(raw)
                        if raw['account_key'] != account_key:
                            return
                    history.append(raw)
            progress = value.get('progress')
            if not isinstance(history, list) or not isinstance(progress, dict):
                return
            self._account_key = account_key
            self._pending = pending
            self._history = history[-MAX_HISTORY:]
            self._progress = progress
            self._progress.setdefault(
                'losses', max(0, int(self._progress.get('battles', 0)) -
                              int(self._progress.get('wins', 0))))
            # Files written before offline achievements shipped have no
            # medal counters; an empty map is the correct starting total.
            # A corrupt or hand-edited counter must not reach the dossier
            # builder, so only known names with positive whole counts survive.
            self._progress['achievements'] = _achievement_counts(
                self._progress.get('achievements'))
            for row in self._progress.get('vehicles', {}).values():
                if isinstance(row, dict):
                    row.setdefault(
                        'losses', max(0, int(row.get('battles', 0)) -
                                      int(row.get('wins', 0))))
                    row['achievements'] = _achievement_counts(
                        row.get('achievements'))
        except (IOError, OSError, TypeError, ValueError):
            # Keep a corrupt optional cache from preventing an offline login.
            self._pending = {}
            self._history = []
            self._progress = self._empty_progress()

    def _archived_identities(self):
        """Project the archive down to what a restart actually needs.

        An acknowledged receipt is only consulted again to reject a retried
        delivery, and its identity carries that on its own.  Writing the
        whole body instead made the terminal round barrier rewrite every
        archived 30-vehicle roster at the instant the round ended.
        """
        return [{'receipt_id': row['receipt_id'],
                 'arena_unique_id': row['arena_unique_id']}
                for row in self._history]

    def _save(self):
        if self._path is None:
            return
        value = {
            'schema': SCHEMA, 'accountKey': self._account_key,
            'pending': list(self._pending.values()),
            'history': self._archived_identities(),
            'progress': self._progress,
        }
        # This file is a machine-owned cache rewritten on the terminal round
        # barrier.  Only ``_load`` reads it, so sorted and indented output
        # buys nothing and costs the embedded 2.7 runtime its C JSON encoder.
        port_config.write_json(self._path, value, compact=True)
