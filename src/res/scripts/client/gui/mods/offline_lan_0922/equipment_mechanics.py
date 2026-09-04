from __future__ import print_function

"""Engine-free #1513 consumable projection, state and effect policy.

The client descriptor remains the authority for every magnitude.  This module
only turns its public fields into JSON-safe values and applies the small state
machine shared by player and bot consumers.  It deliberately imports neither
BigWorld nor battle runtime code, so the server and desktop tests can use it.
"""

import math


DEFAULT_BOT_CONSUMABLE_NAMES = (
    'autoExtinguishers', 'largeMedkit', 'largeRepairkit')

EQUIPMENT_CONTRACT_FIELDS = (
    'name', 'kind', 'id', 'compactDescr', 'tags', 'reuseCount',
    'cooldownSeconds', 'autoactivate', 'fireStartingChanceFactor',
    'repairAll', 'bonusValue', 'crewLevelIncrease', 'enginePowerFactor',
    'turretRotationSpeedFactor', 'engineHpLossPerSecond',
    'autoReactionSeconds')
EQUIPMENT_SNAPSHOT_FIELDS = (
    'equipment', 'usesLeft', 'cooldownTimeLeft', 'active',
    'autoPendingElapsed', 'aiPendingElapsed')


def _value(source, name, default=None):
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        value = float(default)
    if math.isnan(value) or math.isinf(value):
        return float(default)
    return value


def _integer(value, default=0):
    if isinstance(value, bool):
        return int(default)
    try:
        exact = int(value)
        if float(value) != exact:
            return int(default)
        return exact
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _tags(descriptor):
    try:
        return tuple(sorted(str(tag).lower() for tag in
                            (_value(descriptor, 'tags', ()) or ())))
    except TypeError:
        return ()


def equipment_kind(descriptor):
    """Classify one exact equipment descriptor without localized strings."""
    name = str(_value(descriptor, 'name', '') or '').lower()
    tags = set(_tags(descriptor))
    if 'medkit' in tags or 'medkit' in name:
        return 'medkit'
    if 'repairkit' in tags or 'repairkit' in name:
        return 'repairkit'
    if ('extinguisher' in name or any(
            'extinguisher' in tag for tag in tags)):
        return 'extinguisher'
    if ('removedrpmlimiter' in name or
            _number(_value(descriptor, 'engineHpLossPerSecond')) > 0.0):
        return 'rpm_limiter'
    if _number(_value(descriptor, 'crewLevelIncrease')) != 0.0:
        return 'stimulator'
    if (_number(_value(descriptor, 'enginePowerFactor'), 1.0) != 1.0 or
            _number(_value(
                descriptor, 'turretRotationSpeedFactor'), 1.0) != 1.0):
        return 'fuel'
    return 'passive'


def _equipment_id(descriptor):
    raw = _value(descriptor, 'id')
    if isinstance(raw, (list, tuple)) and len(raw) > 1:
        return _integer(raw[1], 0)
    return _integer(raw, 0)


def project_equipment(descriptor, reaction_seconds=None):
    """Return one exact equipment descriptor as a plain JSON contract."""
    if descriptor is None:
        raise ValueError('equipment descriptor is unavailable')
    name = str(_value(descriptor, 'name', '') or '')
    if not name:
        raise ValueError('equipment descriptor has no name')
    kind = equipment_kind(descriptor)
    autoactivate = bool(_value(descriptor, 'autoactivate', False))
    if reaction_seconds is None:
        # #1513 exposes autoactivation but no reaction-delay descriptor.
        # Preserve that native boolean without inventing a timing constant;
        # the canonical server loop supplies the next observation boundary.
        reaction_seconds = 0.0
    result = {
        'name': name,
        'kind': kind,
        'id': _equipment_id(descriptor),
        'compactDescr': _integer(
            _value(descriptor, 'compactDescr'), 0),
        'tags': list(_tags(descriptor)),
        # Exact items.artefacts fields and their #1513 reader defaults.
        'reuseCount': _integer(_value(descriptor, 'reuseCount'), 0),
        'cooldownSeconds': max(
            0.0, _number(_value(descriptor, 'cooldownSeconds'), 0.0)),
        'autoactivate': autoactivate,
        'fireStartingChanceFactor': max(
            0.0, _number(_value(
                descriptor, 'fireStartingChanceFactor'), 1.0)),
        'repairAll': bool(_value(descriptor, 'repairAll', False)),
        'bonusValue': _number(_value(descriptor, 'bonusValue'), 0.0),
        'crewLevelIncrease': _number(
            _value(descriptor, 'crewLevelIncrease'), 0.0),
        'enginePowerFactor': max(
            0.0, _number(_value(descriptor, 'enginePowerFactor'), 1.0)),
        'turretRotationSpeedFactor': max(
            0.0, _number(_value(
                descriptor, 'turretRotationSpeedFactor'), 1.0)),
        'engineHpLossPerSecond': max(
            0.0, _number(_value(
                descriptor, 'engineHpLossPerSecond'), 0.0)),
        'autoReactionSeconds': max(0.0, _number(reaction_seconds, 0.0)),
    }
    return result


def default_bot_consumables(cache):
    """Project the three bot defaults from this exact client's item cache."""
    ids = cache.equipmentIDs()
    descriptors = cache.equipments()
    result = []
    for name in DEFAULT_BOT_CONSUMABLE_NAMES:
        descriptor = descriptors.get(ids.get(name))
        if descriptor is None:
            raise ValueError('client equipment %r is unavailable' % (name,))
        projection = project_equipment(descriptor)
        if projection['name'] != name:
            raise ValueError(
                'client equipment %r resolved as %r' %
                (name, projection['name']))
        result.append(projection)
    return result


def _validate_contract(contract):
    """Return one strict JSON-safe projection used by runtime state."""
    if not isinstance(contract, dict):
        raise ValueError('equipment contract is not an object')
    if set(contract) != set(EQUIPMENT_CONTRACT_FIELDS):
        raise ValueError('equipment contract fields are incomplete')
    name = str(contract.get('name') or '')
    kind = str(contract.get('kind') or '')
    if not name or kind != equipment_kind(contract):
        raise ValueError('equipment contract identity is invalid')
    tags = contract.get('tags')
    if not isinstance(tags, (list, tuple)):
        raise ValueError('equipment contract tags are invalid')
    raw_id = contract.get('id')
    raw_compact = contract.get('compactDescr')
    raw_reuse = contract.get('reuseCount')
    result = {
        'name': name,
        'kind': kind,
        'id': _integer(contract.get('id'), -1),
        'compactDescr': _integer(contract.get('compactDescr'), -1),
        'tags': [str(tag).lower() for tag in tags],
        'reuseCount': _integer(contract.get('reuseCount'), 0),
        'cooldownSeconds': _number(contract.get('cooldownSeconds'), -1.0),
        'autoactivate': contract.get('autoactivate'),
        'fireStartingChanceFactor': _number(
            contract.get('fireStartingChanceFactor'), -1.0),
        'repairAll': contract.get('repairAll'),
        'bonusValue': _number(contract.get('bonusValue'), 0.0),
        'crewLevelIncrease': _number(
            contract.get('crewLevelIncrease'), 0.0),
        'enginePowerFactor': _number(
            contract.get('enginePowerFactor'), -1.0),
        'turretRotationSpeedFactor': _number(
            contract.get('turretRotationSpeedFactor'), -1.0),
        'engineHpLossPerSecond': _number(
            contract.get('engineHpLossPerSecond'), -1.0),
        'autoReactionSeconds': _number(
            contract.get('autoReactionSeconds'), -1.0),
    }
    if (result['id'] < 0 or result['compactDescr'] < 0 or
            result['cooldownSeconds'] < 0.0 or
            result['fireStartingChanceFactor'] < 0.0 or
            result['enginePowerFactor'] < 0.0 or
            result['turretRotationSpeedFactor'] < 0.0 or
            result['engineHpLossPerSecond'] < 0.0 or
            result['autoReactionSeconds'] < 0.0 or
            not isinstance(result['autoactivate'], bool) or
            not isinstance(result['repairAll'], bool)):
        raise ValueError('equipment contract values are invalid')
    for raw, parsed in ((raw_id, result['id']),
                        (raw_compact, result['compactDescr']),
                        (raw_reuse, result['reuseCount'])):
        try:
            exact = not isinstance(raw, bool) and float(raw) == parsed
        except (TypeError, ValueError, OverflowError):
            exact = False
        if not exact:
            raise ValueError('equipment contract integer is invalid')
    for name in (
            'cooldownSeconds', 'fireStartingChanceFactor', 'bonusValue',
            'crewLevelIncrease', 'enginePowerFactor',
            'turretRotationSpeedFactor', 'engineHpLossPerSecond',
            'autoReactionSeconds'):
        if isinstance(contract.get(name), bool):
            raise ValueError('equipment contract number is invalid')
        try:
            raw_number = float(contract.get(name))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('equipment contract number is invalid')
        if math.isnan(raw_number) or math.isinf(raw_number):
            raise ValueError('equipment contract number is invalid')
    if result['tags'] != sorted(result['tags']):
        raise ValueError('equipment contract tags are not canonical')
    return result


def bot_consumable_contracts(descriptor, snapshot=None):
    """Read and validate the fixed three-item bot loadout.

    Wire validators may recover immutable contracts from canonical equipment
    snapshots when they do not have a native VehicleDescr projection.
    """
    raw = _value(descriptor, 'botConsumables')
    if raw is None and isinstance(snapshot, (list, tuple)):
        if not snapshot:
            return ()
        raw = [value.get('equipment') for value in snapshot
               if isinstance(value, dict)]
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or len(raw) != len(
            DEFAULT_BOT_CONSUMABLE_NAMES):
        raise ValueError('bot consumable contract count is invalid')
    result = tuple(_validate_contract(dict(value)) for value in raw)
    if tuple(value['name'] for value in result) != \
            DEFAULT_BOT_CONSUMABLE_NAMES:
        raise ValueError('bot consumable contract order is invalid')
    if tuple(value['kind'] for value in result) != (
            'extinguisher', 'medkit', 'repairkit'):
        raise ValueError('bot consumable contract kinds are invalid')
    if (not result[0]['autoactivate'] or
            not result[1]['repairAll'] or
            not result[2]['repairAll']):
        raise ValueError('bot consumable effect policy is invalid')
    return result


def participant_equipment_contracts(descriptor, snapshots=None):
    """Return the immutable regular-consumable contracts for one player.

    A visible client donates these once with its mounted descriptor.  Mutable
    quantities, cooldowns and trigger state are deliberately absent: the
    server creates and owns those values for the whole round.  ``snapshots``
    is accepted only as a recovery surface for a replica consuming a server
    snapshot; it never changes the donated identity/order contract.
    """
    mounted = _value(descriptor, 'mounted_loadout', {}) or {}
    raw = _value(mounted, 'equipment_contracts')
    if raw is None and isinstance(snapshots, (list, tuple)):
        raw = [value.get('equipment') for value in snapshots
               if isinstance(value, dict)]
    if raw is None:
        return ()
    if (not isinstance(raw, (list, tuple)) or len(raw) > 3 or
            any(not isinstance(value, dict) for value in raw)):
        raise ValueError('participant equipment contract count is invalid')
    result = tuple(_validate_contract(dict(value)) for value in raw)
    identities = tuple((value['id'], value['compactDescr'])
                       for value in result)
    if len(set(identities)) != len(identities):
        raise ValueError('participant equipment contract is duplicated')
    names = _value(mounted, 'equipments')
    if names is not None:
        if (not isinstance(names, (list, tuple)) or
                tuple(str(name) for name in names) !=
                tuple(value['name'] for value in result)):
            raise ValueError(
                'participant equipment contracts do not match loadout')
    return result


def restore_equipment_states(snapshots, contracts=None, now=0.0):
    """Validate and restore a complete server-owned equipment snapshot."""
    if not isinstance(snapshots, (list, tuple)):
        raise ValueError('equipment snapshots are invalid')
    if contracts is None:
        contracts = participant_equipment_contracts(None, snapshots)
    else:
        contracts = tuple(_validate_contract(dict(value))
                          for value in contracts)
    if len(snapshots) != len(contracts):
        raise ValueError('equipment snapshot count changed')
    states = []
    for contract, snapshot in zip(contracts, snapshots):
        state = EquipmentState(contract, now)
        state.restore(snapshot, now)
        states.append(state)
    return states


def _projection(value):
    if isinstance(value, EquipmentState):
        return value.contract
    return value


def passive_effects(equipments):
    """Fold mounted or active equipment into explicit stat policy values."""
    result = {
        'fireStartingChanceFactor': 1.0,
        'repairkitBonusValue': 0.0,
        'medkitBonusValue': 0.0,
        'crewLevelIncrease': 0.0,
        'enginePowerFactor': 1.0,
        'turretRotationSpeedFactor': 1.0,
        'engineHpLossPerSecond': 0.0,
    }
    for raw in (equipments or ()):
        state = raw if isinstance(raw, EquipmentState) else None
        value = _projection(raw)
        kind = str(_value(value, 'kind', '') or '')
        if kind == 'extinguisher':
            result['fireStartingChanceFactor'] *= max(
                0.0, _number(_value(
                    value, 'fireStartingChanceFactor'), 1.0))
        elif kind == 'repairkit':
            result['repairkitBonusValue'] += _number(
                _value(value, 'bonusValue'), 0.0)
        elif kind == 'medkit':
            result['medkitBonusValue'] += _number(
                _value(value, 'bonusValue'), 0.0)
        result['crewLevelIncrease'] += _number(
            _value(value, 'crewLevelIncrease'), 0.0)
        if kind == 'fuel' or (
                kind == 'rpm_limiter' and state is not None and state.active):
            result['enginePowerFactor'] *= max(
                0.0, _number(_value(value, 'enginePowerFactor'), 1.0))
        if kind == 'fuel':
            result['turretRotationSpeedFactor'] *= max(
                0.0, _number(_value(
                    value, 'turretRotationSpeedFactor'), 1.0))
        if kind == 'rpm_limiter' and state is not None and state.active:
            result['engineHpLossPerSecond'] += max(
                0.0, _number(_value(
                    value, 'engineHpLossPerSecond'), 0.0))
    return result


def _validated_snapshot_state(snapshot, contract):
    """Validate one mutable equipment row against one canonical contract."""
    if (not isinstance(snapshot, dict) or
            set(snapshot) != set(EQUIPMENT_SNAPSHOT_FIELDS)):
        raise ValueError('equipment wire snapshot is incomplete')
    raw_uses = snapshot.get('usesLeft')
    uses_left = _integer(raw_uses, -2)
    reuse_count = _integer(contract.get('reuseCount'), 0)
    maximum = -1 if reuse_count < 0 else reuse_count + 1
    if (isinstance(raw_uses, bool) or
            (maximum < 0 and uses_left != -1) or
            (maximum >= 0 and not 0 <= uses_left <= maximum)):
        raise ValueError('equipment wire quantity is invalid')
    raw_cooldown = snapshot.get('cooldownTimeLeft')
    cooldown = _number(raw_cooldown, -1.0)
    cooldown_limit = max(
        0.0, _number(contract.get('cooldownSeconds'), 0.0))
    if (isinstance(raw_cooldown, bool) or cooldown < 0.0 or
            cooldown > cooldown_limit + 1.0e-6):
        raise ValueError('equipment wire cooldown is invalid')
    active = snapshot.get('active')
    if (not isinstance(active, bool) or
            (active and contract.get('kind') != 'rpm_limiter')):
        raise ValueError('equipment wire active state is invalid')

    pending = []
    for name in ('autoPendingElapsed', 'aiPendingElapsed'):
        value = snapshot.get(name)
        if value is None:
            pending.append(None)
            continue
        if isinstance(value, bool):
            raise ValueError('equipment wire pending clock is invalid')
        value = _number(value, -1.0)
        if value < 0.0 or value > 3600.0:
            raise ValueError('equipment wire pending clock is invalid')
        pending.append(value)
    if (pending[0] is not None and
            not contract.get('autoactivate', False)):
        raise ValueError('equipment wire auto clock is invalid')
    if (pending[1] is not None and
            contract.get('kind') not in ('repairkit', 'medkit')):
        raise ValueError('equipment wire AI clock is invalid')
    if uses_left == 0 and any(value is not None for value in pending):
        raise ValueError('exhausted equipment has a pending activation')
    return uses_left, cooldown, active, pending


def canonical_bot_equipment_states(snapshots):
    """Return one validated Bot ledger without constructing mutable state."""
    contracts = bot_consumable_contracts(None, snapshot=snapshots)
    if len(snapshots) != len(contracts):
        raise ValueError('equipment snapshot count changed')
    result = []
    for contract, snapshot in zip(contracts, snapshots):
        uses_left, cooldown, active, pending = _validated_snapshot_state(
            snapshot, contract)
        result.append((contract, uses_left, cooldown, active, tuple(pending)))
    return tuple(result)


def validate_bot_equipment_states(snapshots):
    """Validate one complete Bot ledger without constructing throwaway state."""
    canonical_bot_equipment_states(snapshots)
    return True


def effect_policy(equipment, critical=None, selected=None,
                  requested_active=None, active=False, stunned=False):
    """Return the exact battle mutation requested by one valid activation."""
    value = _projection(equipment)
    kind = str(_value(value, 'kind', '') or '')
    critical = critical if isinstance(critical, dict) else {}
    if kind == 'extinguisher':
        if not bool(critical.get('fire', False)):
            return None
        return {'action': 'extinguish_fire'}
    if kind == 'repairkit':
        damaged = set(str(record.get('name'))
                      for record in (critical.get('devices') or ())
                      if (isinstance(record, dict) and record.get('name') and
                          str(record.get('state') or '') in
                          ('critical', 'destroyed')))
        damaged.update(str(name) for name in
                       (critical.get('destroyed') or ()))
        repair_all = bool(_value(value, 'repairAll', False))
        if not damaged or (not repair_all and str(selected) not in damaged):
            return None
        return {
            'action': 'repair_devices',
            'repairAll': repair_all,
            'selected': None if repair_all else str(selected),
            'bonusValue': _number(_value(value, 'bonusValue'), 0.0),
        }
    if kind == 'medkit':
        knocked_out = set(str(name) for name in
                          (critical.get('crew_ko') or ()))
        repair_all = bool(_value(value, 'repairAll', False))
        if (not knocked_out and not stunned) or (
                not repair_all and str(selected) not in knocked_out and
                not stunned):
            return None
        return {
            'action': 'restore_crew',
            'repairAll': repair_all,
            'selected': None if repair_all else str(selected),
            'bonusValue': _number(_value(value, 'bonusValue'), 0.0),
            'clearStun': bool(stunned),
        }
    if kind == 'rpm_limiter':
        requested_active = bool(requested_active)
        if requested_active == bool(active):
            return None
        return {
            'action': 'set_rpm_limiter',
            'active': requested_active,
            'enginePowerFactor': _number(
                _value(value, 'enginePowerFactor'), 1.0),
            'engineHpLossPerSecond': _number(
                _value(value, 'engineHpLossPerSecond'), 0.0),
        }
    return None


class EquipmentState(object):
    """Mutable cooldown/quantity state around one immutable projection."""

    __slots__ = (
        'contract', 'uses_left', 'ready_at', 'active',
        '_auto_pending_since', '_ai_pending_since')

    def __init__(self, contract, now=0.0):
        self.contract = _validate_contract(contract)
        reuse_count = _integer(self.contract.get('reuseCount'), 0)
        # reuseCount counts uses after the initial charge; -1 is unlimited.
        self.uses_left = -1 if reuse_count < 0 else reuse_count + 1
        self.ready_at = max(0.0, _number(now, 0.0))
        self.active = False
        self._auto_pending_since = None
        self._ai_pending_since = None

    def ready(self, now):
        return (self.uses_left != 0 and
                _number(now, 0.0) >= self.ready_at)

    def activate(self, now, critical=None, selected=None,
                 requested_active=None, stunned=False):
        now = _number(now, 0.0)
        if not self.ready(now):
            return None
        effect = effect_policy(
            self, critical, selected, requested_active, self.active,
            stunned)
        if effect is None:
            return None
        if effect['action'] == 'set_rpm_limiter':
            self.active = bool(effect['active'])
        else:
            if self.uses_left > 0:
                self.uses_left -= 1
            self.ready_at = now + max(
                0.0, _number(
                    self.contract.get('cooldownSeconds'), 0.0))
        self._auto_pending_since = None
        self._ai_pending_since = None
        return effect

    def poll_auto(self, now, critical=None):
        """Activate only after a continuously observed eligible condition."""
        if not bool(self.contract.get('autoactivate', False)):
            return None
        candidate = effect_policy(self, critical)
        now = _number(now, 0.0)
        if candidate is None or not self.ready(now):
            self._auto_pending_since = None
            return None
        delay = max(0.0, _number(
            self.contract.get('autoReactionSeconds'),
            0.0))
        if self._auto_pending_since is None:
            self._auto_pending_since = now
            if delay > 0.0:
                return None
        if now - self._auto_pending_since + 1.0e-9 < delay:
            return None
        return self.activate(now, critical)

    def poll_bot(self, now, critical=None, stunned=False):
        """Apply deterministic bot policy without same-frame kit reactions."""
        kind = str(self.contract.get('kind') or '')
        if kind == 'extinguisher':
            return self.poll_auto(now, critical)
        if kind not in ('repairkit', 'medkit'):
            self._ai_pending_since = None
            return None
        now = _number(now, 0.0)
        candidate = effect_policy(self, critical, stunned=stunned)
        if candidate is None or not self.ready(now):
            self._ai_pending_since = None
            return None
        if self._ai_pending_since is None:
            self._ai_pending_since = now
            return None
        # Even a zero-length simulator step may not consume a kit in the same
        # observation that first noticed the damage.
        if now <= self._ai_pending_since + 1.0e-9:
            return None
        return self.activate(now, critical, stunned=stunned)

    @staticmethod
    def _elapsed(now, started):
        if started is None:
            return None
        return max(0.0, _number(now, 0.0) - _number(started, 0.0))

    def snapshot(self, now=0.0):
        now = max(0.0, _number(now, 0.0))
        contract = dict(self.contract)
        contract['tags'] = list(self.contract.get('tags') or ())
        return {
            'equipment': contract,
            'usesLeft': int(self.uses_left),
            'cooldownTimeLeft': max(0.0, float(self.ready_at) - now),
            'active': bool(self.active),
            'autoPendingElapsed': self._elapsed(
                now, self._auto_pending_since),
            'aiPendingElapsed': self._elapsed(
                now, self._ai_pending_since),
        }

    def restore(self, snapshot, now=0.0):
        """Load a relative wire snapshot, rejecting partial state."""
        if (not isinstance(snapshot, dict) or
                set(snapshot) != set(EQUIPMENT_SNAPSHOT_FIELDS)):
            raise ValueError('equipment wire snapshot is incomplete')
        contract = _validate_contract(snapshot.get('equipment'))
        if contract != self.contract:
            raise ValueError('equipment wire contract changed')
        uses_left, cooldown, active, pending = _validated_snapshot_state(
            snapshot, self.contract)

        now = max(0.0, _number(now, 0.0))
        self.uses_left = uses_left
        self.ready_at = now + cooldown
        self.active = active
        self._auto_pending_since = (
            None if pending[0] is None else now - pending[0])
        self._ai_pending_since = (
            None if pending[1] is None else now - pending[1])
        return True
