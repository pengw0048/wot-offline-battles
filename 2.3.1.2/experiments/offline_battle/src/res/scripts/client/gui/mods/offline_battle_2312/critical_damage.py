"""Critical damage and crew injury law, taken from the 0.9.22 port.

The law is unchanged. Version differences belong in the
adapters in this package, never in this file.
"""
from __future__ import absolute_import
from __future__ import print_function
"""Generated 0.8.2 critical-damage law with thin 2.3.1.2 state adapters.

Do not edit copied functions in this file.  Run
``0.9.22/tools/generate_critical_damage.py`` and let the source audit
compare every copied body with ``offline_battle.py``.
"""

import random

from gui.mods.offline_battle_2312 import device_damage as _device_damage


def _descriptor_value(value, name, default=None):
    """Read 0.8.2 mappings or native 2.3.1.2 item component attributes."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def LOG_DEBUG(*unused_args):
    # The user explicitly requested no trace-heavy battle output.
    return None


_OFFH_VOICE_BURST = [None]
loaded_models = {}

_OFFH_DEATH_DEVICES = ('engineHealth', 'ammoBayHealth', 'fuelTankHealth',
                       'radioHealth', 'gunHealth',
                       'turretRotatorHealth', 'surveyingDeviceHealth',
                       'leftTrackHealth', 'rightTrackHealth')


def _push_device_ui(*unused_args, **unused_kwargs):
    # 2.3.1.2 presentation is applied after the authoritative payload arrives.
    return None


def _offh_play_crit_voice(*unused_args, **unused_kwargs):
    # PlayerAvatar.showVehicleDamageInfo owns the 2.3.1.2 sound/UI mapping.
    return None


def _sync_crashed_track(*unused_args, **unused_kwargs):
    # Stock 2.3.1.2 Vehicle appearance owns damaged-track presentation.
    return None


class _SynthDeviceExtra(object):
    '''Stand-in for a vehicle-type extra, carrying the one field the crit loop
    reads off it.'''
    __slots__ = ('name',)

    def __init__(self, name):
        self.name = name


class _SynthMaterial(object):
    '''Stand-in for the MaterialInfo of an INTERIOR device hit.

    0.8.2 ships no collision geometry for the interior: all 1975 collision meshes
    in this client carry only armor_N, gun, the two tracks, surveyingDevice and
    gunBreech (the interior kinds survive on two leftover models and no crewman
    kind exists at all). WG resolved those hits server-side against a model that
    was never distributed, so the crit loop is handed one of these instead. The
    values are the era common/vehicle.xml entry for a device material: armor 0,
    damageKind 1 (device), vehicleDamageFactor 0 (the hull damage is already
    accounted for by the penetrating shot) and the real hit chances, which is
    what device_damage.saving_throw reads.'''
    __slots__ = ('extra', 'armor', 'damageKind', 'vehicleDamageFactor',
                 'chanceToHitByProjectile', 'chanceToHitByExplosion')

    def __init__(self, name):
        from gui.mods.offline_battle_2312 import device_damage as _dd
        self.extra = _SynthDeviceExtra(name)
        self.armor = 0
        self.damageKind = 1
        self.vehicleDamageFactor = 0.0
        self.chanceToHitByProjectile = _dd.fallback_chance(name, False)
        # Crew is the one group where the two differ: 0.33 by shell, 0.15 by blast.
        self.chanceToHitByExplosion = _dd.fallback_chance(name, True)


def _offh_interior_zone(target_mock, all_hits, start_pos, end_pos=None, td=None):
    '''Which interior compartment the shell entered: 'turret', 'hullFront',
    'hullRear' or 'hullSide'.

    The turret case is read straight off the component the shell crossed - that
    geometry IS in the collision model. The hull case is placed from the real
    entry point: the distance of the first structural plate along the shot
    segment gives a world point, the tank's inverse matrix turns it into hull
    coordinates, and device_damage.interior_zone splits it against THIS tank's
    own turret-ring position and half width (both on the descriptor). So "behind
    the ring" means behind that tank's actual ring, not a fixed fraction.

    If any of that is unavailable the bearing to the shooter decides, which is
    coarse but never wrong about a shot coming from directly astern.'''
    import Math
    _comp = None
    _dist = None
    try:
        for _h in sorted(all_hits, key=lambda _x: _x[0]):
            _m = _h[2]
            if _m is None:
                continue
            # The plate that stopped or admitted the round: structural, with thickness.
            if getattr(_m, 'vehicleDamageFactor', 1.0) != 0.0 and float(getattr(_m, 'armor', 0.0) or 0.0) > 0.0:
                _comp = _h[3]
                _dist = _h[0]
                break
    except Exception:
        _comp = None
    try:
        if _comp is not None:
            if str(_descriptor_value(_comp, 'itemTypeName', '')) in ('vehicleTurret', 'vehicleGun'):
                return 'turret'
    except Exception:
        pass
    # Entry point in the tank's own frame, compared against its own geometry.
    try:
        if td is None:
            td = getattr(target_mock, 'typeDescriptor', None)
        if td is not None and _dist is not None and end_pos is not None:
            _dx = float(end_pos.x) - float(start_pos.x)
            _dy = float(end_pos.y) - float(start_pos.y)
            _dz = float(end_pos.z) - float(start_pos.z)
            _dl = (_dx * _dx + _dy * _dy + _dz * _dz) ** 0.5
            if _dl > 0.001:
                _s = float(_dist) / _dl
                _wp = Math.Vector3(float(start_pos.x) + _dx * _s,
                                   float(start_pos.y) + _dy * _s,
                                   float(start_pos.z) + _dz * _s)
                _inv = Math.Matrix(target_mock.matrix)
                _inv.invert()
                _lp = _inv.applyPoint(_wp)
                # Vehicle origin sits on the chassis; the hull model is offset from it.
                _hp = _descriptor_value(td.chassis, 'hullPosition')
                _ring = _descriptor_value(td.hull, 'turretPositions')[0]
                _bb = _descriptor_value(td.hull, 'hitTester').bbox
                _hw = max(abs(float(_bb[0].x)), abs(float(_bb[1].x)))
                from gui.mods.offline_battle_2312 import device_damage as _DDz
                return _DDz.interior_zone(float(_lp.x) - float(_hp.x),
                                          float(_lp.z) - float(_hp.z),
                                          float(_ring.z), _hw)
    except Exception as _ze:
        LOG_DEBUG('interior zone from geometry failed, using bearing:', str(_ze))
    try:
        _pos = target_mock.position
        _fwd = Math.Matrix(target_mock.matrix).applyVector(Math.Vector3(0.0, 0.0, 1.0))
        _fx, _fz = float(_fwd.x), float(_fwd.z)
        _fl = (_fx * _fx + _fz * _fz) ** 0.5
        _tx = float(start_pos.x) - float(_pos.x)
        _tz = float(start_pos.z) - float(_pos.z)
        _tl = (_tx * _tx + _tz * _tz) ** 0.5
        if _fl > 0.001 and _tl > 0.001:
            _cos = (_fx * _tx + _fz * _tz) / (_fl * _tl)
            if _cos >= 0.5:
                return 'hullFront'      # within 60 deg of the nose
            if _cos <= -0.5:
                return 'hullRear'
    except Exception:
        pass
    return 'hullSide'


def _offh_voice_burst_pick(pending):
    '''The one line worth saying out of a single strike: a destroyed module or a
    downed crewman outranks a mere scratch, ties go to the first report.'''
    best = None
    best_rank = -1
    for snd in pending:
        rank = 2 if (snd.endswith('_destroyed') or snd.endswith('_killed')) else 1
        if rank > best_rank:
            best_rank = rank
            best = snd
    return best


def _offh_ignite(target_mock, is_player_target, reason):
    '''Set a vehicle alight and tell the damage panel about it.'''
    target_mock.is_on_fire = True
    try:
        import BigWorld as _bwig
        target_mock._fire_started = _bwig.time()
    except Exception:
        target_mock._fire_started = None
    LOG_DEBUG('FIRE IGNITED ON: %s (%s)' % (getattr(target_mock, 'id', 'PLAYER'), reason))
    if (not is_player_target or getattr(target_mock, '_offline_proposal_only', False)):
        return
    try:
        import gui.WindowsManager
        bw = gui.WindowsManager.g_windowsManager.battleWindow
        if bw is not None and hasattr(bw, 'damagePanel'):
            bw.damagePanel.onFireInVehicle(True)
    except Exception as _fe:
        LOG_DEBUG('FIRE UI UPDATE ERR:', str(_fe))


def _offh_extinguish(target_mock, is_player_target, reason):
    '''End a fire and bring the fuel tank back to its regen cap.

    The fuel tank has no repair bar in the game - a destroyed one is red for as
    long as the tank burns and turns orange the moment the fire is out, whether
    the crew smothered it or an extinguisher did. That step is here rather than
    in the repair tick, because it is the FIRE ending that restores it, not time
    spent repairing.'''
    if not getattr(target_mock, 'is_on_fire', False):
        return
    target_mock.is_on_fire = False
    target_mock._fire_started = None
    LOG_DEBUG('FIRE OUT ON: %s (%s)' % (getattr(target_mock, 'id', 'PLAYER'), reason))
    if (is_player_target and not getattr(target_mock, '_offline_proposal_only', False)):
        try:
            import gui.WindowsManager
            bw = gui.WindowsManager.g_windowsManager.battleWindow
            if bw is not None and hasattr(bw, 'damagePanel'):
                bw.damagePanel.onFireInVehicle(False)
        except Exception as _xe:
            LOG_DEBUG('FIRE UI CLEAR ERR:', str(_xe))
    # Fuel tank: destroyed -> back at the regen cap, which reads as 'repaired'.
    try:
        from gui.mods.offline_battle_2312 import device_damage as _DDx
        td = _device_td(target_mock)
        hp_map = getattr(target_mock, 'devices_hp', None)
        if hp_map is None or td is None:
            return
        name = 'fuelTankHealth'
        cap = _DDx.device_regen_hp(td, name)
        if cap is None or hp_map.get(name, cap) >= cap:
            return
        hp_map[name] = cap
        destroyed = getattr(target_mock, '_destroyed_devices', None)
        if destroyed is not None:
            destroyed.discard(name)
        states = getattr(target_mock, '_module_states', None)
        max_hp = _DDx.device_max_hp(td, name)
        new_state = _DDx.device_state(cap, max_hp)
        if states is not None:
            states[name] = new_state
        _push_device_ui(target_mock, is_player_target, name, cap, max_hp, state='repaired')
    except Exception as _fx:
        LOG_DEBUG('fuel tank restore after fire failed:', str(_fx))


def _offh_knock_out_everything(mock, is_player):
    '''A destroyed tank has everything destroyed: every module at 0 HP and every
    crewman down. Used for drowning AND for an ordinary kill - previously only
    drowning called it, so a normal death left most module icons untouched.'''
    try:
        if getattr(mock, 'devices_hp', None) is None:
            mock.devices_hp = {}
        for _n in _OFFH_DEATH_DEVICES:
            mock.devices_hp[_n] = 0
        # The repair tick and the module GUI read the destroyed-SET, not raw HP.
        _ds = getattr(mock, '_destroyed_devices', None)
        if _ds is None:
            _ds = set()
            mock._destroyed_devices = _ds
        for _n in _OFFH_DEATH_DEVICES:
            _ds.add(_n)
    except Exception:
        pass
    # Crew: use the tank's REAL roster ('gunner1', 'loader1', ...). The old generic
    # role names never matched the panel entries, so crew never turned red.
    _roster = []
    try:
        _roster = _crew_roster(_device_td(mock))
        _ko = getattr(mock, '_crew_ko', None)
        if _ko is None:
            _ko = set()
            mock._crew_ko = _ko
        for _c in _roster:
            _ko.add(_c)
        _recompute_crew_impaired(mock)
    except Exception:
        pass
    try:
        _refresh_mobility_flags(mock)
    except Exception:
        pass
    LOG_DEBUG('KNOCKOUT called: is_player=%s devices=%d crew=%d' % (is_player, len(_OFFH_DEATH_DEVICES), len(_roster)))
    if not is_player:
        return
    try:
        import BigWorld
        from gui import WindowsManager as _wmko
        _p = BigWorld.player()
        _bw = getattr(_wmko.g_windowsManager, 'battleWindow', None)
        _dp = getattr(_bw, 'damagePanel', None) if _bw is not None else None
        _ui = [_module_ui_name(_n) for _n in _OFFH_DEATH_DEVICES] + list(_roster)
        LOG_DEBUG('KNOCKOUT: is_player=%s panel=%s names=%s' % (is_player, _dp is not None, _ui))
        for _n in _ui:
            try: _p.guiSessionProvider.invalidateVehicleState(2, _p.playerVehicleID, _n, 'destroyed')
            except Exception: pass
            if _dp is not None:
                try: _dp.updateState(_n, 'destroyed')
                except Exception: pass
        # Retail greys the panel and deactivates the crew from DamagePanel._updateOther
        # the moment the vehicle reads dead. That tick needs a real BigWorld entity, so
        # offline it never runs and the crew icons stayed lit on a destroyed tank.
        if _dp is not None:
            try: _dp.onVehicleDestroyed()
            except Exception: pass
            try: _dp.onCrewDeactivated()
            except Exception: pass
        # onVehicleDestroyed greys the WHOLE panel out, wiping the red module icons we
        # just set, and it can also fire again later. Push them again on the next
        # frames so the destroyed state is what stays visible.
        def _reassert(_names=list(_ui), _panel=_dp, _hp=_offh_hp_display(mock)):
            if _panel is None:
                return
            for _m in _names:
                try: _panel.updateState(_m, 'destroyed')
                except Exception: pass
            # A drowned tank keeps its last HP; anything else is already 0 here.
            try: _panel.updateHealth(_hp)
            except Exception: pass
        try:
            import BigWorld as _bwr
            # Spread over the whole post-mortem: WG's DamagePanel._updateSelf ticks every
            # 30 ms and calls onVehicleDestroyed() the moment the vehicle reads as dead,
            # which greys the panel. Re-push past that point so the red module icons are
            # what remains on screen.
            _bwr.callback(0.1, _reassert)
            _bwr.callback(0.5, _reassert)
            _bwr.callback(1.5, _reassert)
            _bwr.callback(3.0, _reassert)
        except Exception:
            pass
    except Exception:
        pass


def _offh_module_test_mode():
    '''config module_test_mode: a bench for the module model.

    Bot shells still roll every module and crew crit exactly as they normally
    would - same era saving throws, same HP pools, same repair - but they take
    no hull HP off the player, an ammo rack does not detonate the tank, and fire
    does not drain. So a crit can be watched, repaired, re-broken and listened to
    without the run ending after four shells. Nothing about the crit model
    itself is altered; only the consequences that would end the test.'''
    try:
        from _constants import CONFIG_OPTIONS as _TCFG
        return bool(_TCFG.get('module_test_mode', False))
    except Exception:
        return False


def _offh_internal_layout(td):
    '''Per-vehicle interior layout from the adopted profile data, or None.

    None means "fall back to the measured zone model": the feature is switched
    off, the adopted modules are absent, or this tank has no profile. Their
    build_layout() keeps its own cache keyed by type + configuration, so calling
    it per shot is cheap after the first hit on a given tank.'''
    if td is None:
        return None
    try:
        from _constants import CONFIG_OPTIONS as _LCFG
        if not bool(_LCFG.get('internal_layout_profiles', True)):
            return None
    except Exception:
        pass
    try:
        from gui.mods.offline_battle_2312 import internal_hit_layouts as _IHL
    except Exception as _le:
        if not globals().get('_offh_layout_import_logged'):
            globals()['_offh_layout_import_logged'] = True
            LOG_DEBUG('internal_hit_layouts unavailable, using zone model:', str(_le))
        return None
    try:
        return _IHL.build_layout(td, log_build=False)
    except Exception as _be:
        LOG_DEBUG('build_layout failed:', str(_be))
        return None


def _offh_internal_ray_hits(target_mock, td, start_pos, end_pos, covered=()):
    '''Interior modules and crew the shell REALLY passed through.

    Returns [(entry_distance, extraName)] sorted front to back, or None when no
    layout is available. The profile boxes live in their parent component's own
    space, so the segment goes through exactly the two transforms
    Vehicle.getComponents applies: world -> vehicle -> component, which also
    accounts for the current turret yaw and gun pitch.

    `covered` lists extra names the real collision model already produced for
    this shot. Their layout drops any entity it finds in the per-vehicle
    material table, but surveyingDevice is only in the GLOBAL table, so without
    this the optics would be scored twice - once from geometry, once from the
    profile.'''
    layout = _offh_internal_layout(td)
    if not layout:
        return None
    targets = layout.get('targets') or ()
    if not targets:
        return None
    import Math
    from gui.mods.offline_battle_2312 import internal_geometry as _IG
    inv = Math.Matrix(target_mock.matrix)
    inv.invert()
    _vs = inv.applyPoint(Math.Vector3(start_pos.x, start_pos.y, start_pos.z))
    _ve = inv.applyPoint(Math.Vector3(end_pos.x, end_pos.y, end_pos.z))
    local = {}
    for compDescr, compMatrix in target_mock.getComponents():
        name = None
        for candidate in ('chassis', 'hull', 'turret', 'gun'):
            if compDescr is getattr(td, candidate, None):
                name = candidate
                break
        if name is None:
            continue
        _ls = compMatrix.applyPoint(_vs)
        _le = compMatrix.applyPoint(_ve)
        local[name] = ((_ls.x, _ls.y, _ls.z), (_le.x, _le.y, _le.z))
    hits = []
    for target in targets:
        seg = local.get(target.get('parent'))
        if seg is None:
            continue
        entity = target.get('entity')
        if not entity:
            continue
        name = str(entity) + 'Health'
        if name in covered:
            continue
        interval = _IG.target_interval(seg[0], seg[1], target)
        if interval is None:
            continue
        hits.append((float(interval[0]), name))
    hits.sort()
    # ONE roll per device, not per box. The profiles model a module as several
    # boxes - an ammo rack is typically three (hull floor left, hull floor right,
    # turret ready rack) - and a shell through the fighting compartment crosses
    # two of them. Scoring both would give that module twice the saving throw WG
    # gives it. The log showed exactly that: 'ammoBayHealth@0.04,
    # ammoBayHealth@0.04' from a single strike. Keep the nearest box per device.
    seen = set()
    unique = []
    for dist, name in hits:
        if name in seen:
            continue
        seen.add(name)
        unique.append((dist, name))
    return unique


def _device_td(mock):
    import BigWorld
    return getattr(mock, 'typeDescriptor', getattr(BigWorld.player(), 'vehicleTypeDescriptor', None))


def _crew_roster(td):
    # Crew instance names ('commander','driver','gunner1',...) - the crew health
    # extra names minus 'Health'.
    names = []
    try:
        enumRoles = {'gunner': 1, 'loader': 1, 'radioman': 1}
        for roles in getattr(getattr(td, 'type', None), 'crewRoles', []):
            mainRole = roles[0]
            if mainRole in enumRoles:
                names.append(mainRole + str(enumRoles[mainRole]))
                enumRoles[mainRole] += 1
            else:
                names.append(mainRole)
    except Exception:
        pass
    if not names:
        names = ['commander', 'driver', 'gunner1', 'loader1', 'radioman1']
    return names


def _recompute_crew_impaired(mock):
    # Cache the impaired BASE roles. A small crew has men covering SEVERAL roles,
    # so one casualty can impair more than one - read that from td.type.crewRoles
    # rather than assuming one role per man.
    from gui.mods.offline_battle_2312 import device_damage as _DDc
    ko = getattr(mock, '_crew_ko', None)
    if not ko:
        mock._crew_impaired = frozenset()
        return
    td = _device_td(mock)
    roster = _crew_roster(td)
    try:
        crewRoles = list(getattr(getattr(td, 'type', None), 'crewRoles', []))
    except Exception:
        crewRoles = []
    roles = set()
    for i, inst in enumerate(roster):
        if inst in ko:
            if i < len(crewRoles):
                for r in crewRoles[i]:
                    roles.add(r)
            else:
                roles.add(_DDc.crew_role_base(inst))
    mock._crew_impaired = frozenset(roles)


def _crew_factor(mock, stat):
    # Stat multiplier from this mock's knocked-out crew (1.0 when all fit).
    imp = getattr(mock, '_crew_impaired', None)
    if not imp:
        return 1.0
    try:
        from gui.mods.offline_battle_2312 import device_damage as _DDc
        return _DDc.crew_stat_factor(imp, stat)
    except Exception:
        return 1.0


def _module_factor(mock, stat):
    # Stat multiplier from this mock's MODULE state (1.0 when everything is
    # whole), the counterpart of _crew_factor. The client never had these -
    # avatar.py only gates input on a destroyed engine/track/gun - so the
    # numbers are reconstructed in device_damage.DAMAGED_MODULE_EFFICIENCY.
    if mock is None:
        return 1.0
    try:
        from gui.mods.offline_battle_2312 import device_damage as _DDm
        return _DDm.module_stat_factor(getattr(mock, 'devices_hp', None),
                                      getattr(mock, '_destroyed_devices', None),
                                      _device_td(mock), stat)
    except Exception:
        return 1.0


def _knock_out_crew(mock, crew_name, is_player_target):
    # Binary knock-out (a med kit revives). True when newly downed.
    ko = getattr(mock, '_crew_ko', None)
    if ko is None:
        ko = set()
        mock._crew_ko = ko
    if crew_name in ko:
        return False
    ko.add(crew_name)
    _recompute_crew_impaired(mock)
    LOG_DEBUG('CREW KO:', getattr(mock, 'id', 'PLAYER'), crew_name)
    if (is_player_target and not getattr(mock, '_offline_proposal_only', False)):
        try:
            import gui.WindowsManager
            bw = gui.WindowsManager.g_windowsManager.battleWindow
            if bw is not None and hasattr(bw, 'damagePanel'):
                try: bw.damagePanel.updateState(crew_name, 'destroyed')
                except Exception as _cse: LOG_DEBUG('crew updateState err:', crew_name, str(_cse))
            _tdk = getattr(BigWorld.player(), 'vehicleTypeDescriptor', None)
            _exk = _tdk.extrasDict.get(crew_name + 'Health') if (_tdk is not None and hasattr(_tdk, 'extrasDict')) else None
            _sndk = getattr(_exk, 'sounds', {}).get('destroyed') if _exk is not None else None
            _offh_play_crit_voice(_sndk)
        except Exception as _ke:
            LOG_DEBUG('crew KO ui err:', str(_ke))
    return True


def _dev_destroyed_set(mock):
    s = getattr(mock, '_destroyed_devices', None)
    if s is None:
        s = set()
        mock._destroyed_devices = s
    return s


def _module_ui_name(name):
    '''Damage-panel device name = extra name minus 'Health'; tracks keep their side.

    The battle scope defines its own and publishes it over this one. This module-level
    copy exists because _offh_knock_out_everything is module-level too: without it the
    name lookup raised NameError and took the whole panel block down with it.'''
    return name[:-6] if name.endswith('Health') else name


def _refresh_mobility_flags(mock):
    # A destroyed track/engine is functional again only once auto-repair reaches
    # ~50% (the repair tick drops it from the set), so gameplay keys off the
    # destroyed-set rather than raw HP.
    s = _dev_destroyed_set(mock)
    mock.is_tracked = ('leftTrackHealth' in s) or ('rightTrackHealth' in s)
    mock.is_engine_dead = ('engineHealth' in s)
    mock.is_gun_destroyed = ('gunHealth' in s)
    mock.is_turret_locked = ('turretRotatorHealth' in s)


def _apply_module_damage(target_mock, all_hits, start_pos, end_pos, dmg, _shell, attacker_id, penetrated=None, by_explosion=False):
    '''Roll module and crew crits for one strike.

    penetrated: True the shell got through, False it did not, None unknown (the
    bot call sites, which already sit behind their own penetration branch).
    False restricts the roll to devices IN FRONT of the plate that stopped the
    round - see the _stop_d block below.

    by_explosion: this is HE splash rather than a solid hit, so every saving throw
    reads the material's chanceToHitByExplosion. Blast reaches externally mounted
    gear far more readily than it reaches anything behind a plate, which is what
    the two separate XML values encode.'''
    import BigWorld, Math, random
    from gui.mods.offline_battle_2312 import device_damage as _device_damage
    try:
        from _constants import CONFIG_OPTIONS as _MDCFG
    except Exception:
        _MDCFG = {}
    if not bool(_MDCFG.get('module_damage', True)):
        return dmg
    _crew_on = bool(_MDCFG.get('crew_damage', True))
    _crew_hit = False
    _pvid = getattr(BigWorld.player(), 'playerVehicleID', -1)
    is_player_target = (getattr(target_mock, 'id', -1) == _pvid)
    if is_player_target and not bool(_MDCFG.get('player_module_damage', True)):
        return dmg
    if getattr(target_mock, 'devices_hp', None) is None:
        target_mock.devices_hp = {}
    # 0.8.2 shells carry damage as (armor, devices); there is no 'deviceDamage' key.
    _shell_dmg = _device_damage.module_damage_roll(_shell)
    if _shell_dmg is None:
        _shell_dmg = dmg
    is_player_attacker = (attacker_id == _pvid)
    target_mock.last_sound = 'armor_pierced_by_player' if is_player_attacker else 'armor_pierced'
    td = _device_td(target_mock)
    # A shell that did NOT get through can only crit what sits IN FRONT of the plate
    # that stopped it. The player's shot path calls this on every strike (so a pure
    # track hit can still break a track, since tracks deal 0 structure damage), and
    # without this window it also rolled the engine, fuel tank, crew and ammo bay
    # deep inside the hull on a shot that visibly bounced. A destroyed ammo bay is
    # an instant kill, so that read as a random ammo rack on a ricochet.
    # Where the shell LEAVES the hull. The ray runs far past the tank, so the
    # hit list also contains the far-side track on the way out - and a track
    # material has chanceToHitByProjectile 1.0, so every penetrating hull hit
    # broke a track that the shell never really reached. That is the "tracked
    # out of nowhere when shooting the hull" report. A round is spent once it
    # has crossed the far wall, so stop scoring after the SECOND structural
    # plate: entry, interior, exit.
    _exit_d = None
    try:
        _walls = 0
        for _h1 in sorted(all_hits, key=lambda _x: _x[0]):
            _m1 = _h1[2]
            if _m1 is None:
                continue
            if getattr(_m1, 'vehicleDamageFactor', 1.0) != 0.0 and float(getattr(_m1, 'armor', 0.0) or 0.0) > 0.0:
                _walls += 1
                if _walls >= 2:
                    _exit_d = _h1[0]
                    break
    except Exception:
        _exit_d = None
    _stop_d = None
    if penetrated is False:
        _stop_d = 1e9
        for _h0 in all_hits:
            try:
                _hd0, _hm0 = _h0[0], _h0[2]
            except Exception:
                continue
            if _hm0 is None:
                continue
            # structural = deals hull damage AND has thickness; that is the plate the
            # penetration test was run against.
            if getattr(_hm0, 'vehicleDamageFactor', 1.0) != 0.0 and float(getattr(_hm0, 'armor', 0.0) or 0.0) > 0.0:
                if _hd0 < _stop_d:
                    _stop_d = _hd0
    # Interior devices have no collision geometry in this client: all 1975
    # collision meshes carry armor_N, gun, both tracks, surveyingDevice and
    # gunBreech and nothing else. WG resolved engine / ammo bay / fuel tank /
    # radio / turret ring / crew hits server-side against a model that was never
    # shipped, so no ray can ever reach them. A penetrating strike therefore gets
    # ONE reconstructed interior roll, aimed at the compartment the shell entered.
    # It is appended as a synthetic hit at distance 0 and runs through the SAME
    # scoring loop below, so HP, panel, voice, fire and ammo-rack detonation all
    # behave exactly as they do for a hit that came out of the collision model.
    _scored = all_hits
    if penetrated is not False and bool(_MDCFG.get('internal_module_damage', True)):
        try:
            # Preferred path: the adopted per-tank profiles give every interior
            # module and crewman a real box, so the shell either crosses one or
            # it does not - no zone guess involved. Each crossed box gets its own
            # saving throw, which is how a round through the engine bay can take
            # the engine AND a fuel tank.
            _covered = set()
            for _h2 in all_hits:
                _m2 = _h2[2]
                _x2 = getattr(_m2, 'extra', None) if _m2 is not None else None
                if _x2 is not None:
                    _covered.add(str(getattr(_x2, 'name', '')))
            _rost = _crew_roster(td)
            _real = _offh_internal_ray_hits(target_mock, td, start_pos, end_pos, _covered)
            if _real is not None:
                if not _crew_on:
                    _real = [_r for _r in _real if _r[1][:-6] not in _rost]
                if _real:
                    LOG_DEBUG('INTERIOR GEOMETRY: %s' % ', '.join(
                        ['%s@%.2f' % (_n2, _d2) for _d2, _n2 in _real]))
                    _scored = list(all_hits)
                    for _d2, _n2 in _real:
                        _scored.append((0.0, 1.0, _SynthMaterial(_n2), None))
                else:
                    LOG_DEBUG('INTERIOR GEOMETRY: shell path crossed no interior box')
            else:
                # Fallback: no profile for this tank (or the feature is off).
                # One reconstructed roll against the compartment the shell entered.
                _zone = _offh_interior_zone(target_mock, all_hits, start_pos, end_pos, td)
                _cands = _device_damage.interior_candidates(_zone, _rost, td)
                if not _crew_on:
                    # Crew candidates are exactly the roster instances plus 'Health'.
                    _cands = [_c for _c in _cands if _c[0][:-6] not in _rost]
                _pick = _device_damage.pick_interior(_cands)
                if _pick is not None:
                    LOG_DEBUG('INTERIOR ROLL: zone=%s pick=%s (%d candidates)' % (_zone, _pick, len(_cands)))
                    _scored = list(all_hits)
                    _scored.append((0.0, 1.0, _SynthMaterial(_pick), None))
        except Exception as _ie:
            LOG_DEBUG('interior roll err:', str(_ie))
    # Re-entrant: HE splash scores other vehicles through this same function, so
    # only the outermost strike owns the collector.
    _own_burst = _OFFH_VOICE_BURST[0] is None
    if _own_burst:
        _OFFH_VOICE_BURST[0] = []
    try:
        _blocked = 0
        for h in _scored:
            h_dist, h_angle, h_mat, h_comp = h
            if h_mat is None:
                continue
            _extra = getattr(h_mat, 'extra', None)
            if _extra is None:
                continue          # plain armour plate, nothing to crit
            # NO vehicleDamageFactor filter here. It used to drop every device material
            # whose plate ALSO damages the hull, on the theory that those were armour
            # rather than gear. The shipped data says otherwise: engine, ammo bay, fuel
            # tank, radio, turret ring and gunBreech all carry vehicleDamageFactor 1.0
            # and WG crits every one of them. Of those, only gunBreech has geometry in
            # this client (37 vehicles), so the filter's real effect was that a shell
            # through the breech could not damage the gun. vehicleDamageFactor governs
            # how much of the round goes into the HULL, which the penetration path
            # already owns; it says nothing about whether a crit may happen.
            _name = getattr(_extra, 'name', 'Unknown')
            if _stop_d is not None and h_dist > _stop_d:
                _blocked += 1
                continue          # behind the plate that stopped the shell
            if _exit_d is not None and h_dist > _exit_d:
                _blocked += 1
                continue          # the shell has left the tank - exit-side track, not a hit
            # Chance source, so the log answers whether the era fallback table is ever
            # reached. MaterialInfo always carries chanceToHitByProjectile (vehicles.py
            # _readArmor copies it from g_cache.commonConfig), so 'FALLBACK' here means
            # the material object itself is not what we think it is.
            _live_c = getattr(h_mat, 'chanceToHitByExplosion' if by_explosion else 'chanceToHitByProjectile', None)
            LOG_DEBUG('CRIT ROLL: %s chance=%s src=%s%s' % (_name, _live_c, 'mat' if _live_c is not None else 'FALLBACK', ' splash' if by_explosion else ''))
            if _name in _device_damage.CREW_HEALTH_NAMES:
                if _crew_on and random.random() < _device_damage.saving_throw(h_mat, _name, by_explosion):
                    if _knock_out_crew(target_mock, _name[:-6], is_player_target):
                        _crew_hit = True
                        target_mock.last_sound = 'armor_pierced_crit_by_player' if is_player_attacker else 'armor_pierced_crit'
                continue
            # INCLUSION list: only real, modelled devices are scored. The old exclusion
            # list ('everything except tracks and gun') both credited unmodelled extras
            # AND made track/gun crits impossible.
            if _name not in _device_damage._DEVICE_HP_SPEC:
                continue
            if random.random() >= _device_damage.saving_throw(h_mat, _name, by_explosion):
                continue   # saving throw failed: no crit on this device
            max_hp = _device_damage.device_max_hp(td, _name)
            if max_hp is None:
                max_hp = 100
            current_hp = target_mock.devices_hp.get(_name, max_hp)
            current_hp -= _shell_dmg
            # Clamp at 0 so auto-repair does not have to climb out of a deficit.
            if current_hp < 0:
                current_hp = 0
            target_mock.devices_hp[_name] = current_hp
            target_mock.last_sound = 'armor_pierced_crit_by_player' if is_player_attacker else 'armor_pierced_crit'
            _push_device_ui(target_mock, is_player_target, _name, current_hp, max_hp)
            if 'ammo' in _name.lower() and current_hp <= 0 and is_player_target and _offh_module_test_mode():
                # Test bench: the rack still reads destroyed on the panel and can be
                # repaired, it just does not end the run.
                LOG_DEBUG('MODULE TEST: ammo rack detonation on the player suppressed')
            elif 'ammo' in _name.lower() and current_hp <= 0:
                # A detonated ammo rack destroys the tank outright - the era rule, not a roll.
                LOG_DEBUG('AMMO RACK DETONATION: target=%s penetrated=%s hp_was=%s shell_dev_dmg=%.0f' % (
                    getattr(target_mock, 'id', '?'), penetrated, max_hp, _shell_dmg))
                dmg = target_mock.health + 10
                target_mock._is_killed = True
                target_mock._ammo_rack_death = True   # picks the 'explosion' death effect
                target_mock.last_sound = 'enemy_killed_by_player' if is_player_attacker else 'enemy_killed'
                try:
                    if not getattr(target_mock, '_offline_proposal_only', False):
                        BigWorld.player().arena.onVehicleKilled(target_mock.id, attacker_id, 1)
                except Exception:
                    pass
                break
            if current_hp <= 0:
                _dev_destroyed_set(target_mock).add(_name)
                _refresh_mobility_flags(target_mock)
                # Opening frame at 0%, so the bar appears the instant the module breaks
                # instead of only on the next repair tick - but not when this very shot is
                # killing the tank, or the panel starts a repair on a wreck.
                if (is_player_target and not getattr(target_mock, '_offline_proposal_only', False) and not getattr(target_mock, '_is_killed', False)) and (getattr(target_mock, 'health', 0) or 0) > 0:
                    try:
                        import gui.WindowsManager as _WMrb
                        _bwrb = getattr(_WMrb.g_windowsManager, 'battleWindow', None)
                        if _bwrb is not None and hasattr(_bwrb, 'damagePanel'):
                            from gui.mods.offline_battle_2312 import device_damage as _DDrb
                            _secs0 = _DDrb.repair_seconds(_name, td)
                            _bwrb.damagePanel.updateModuleRepair(_module_ui_name(_name), 0, _secs0)
                    except Exception: pass
                if ('engine' in _name.lower() or 'fuel' in _name.lower()) and not getattr(target_mock, 'is_on_fire', False):
                    # Fuel tank always ignites; an engine only rolls for it, and the hit must
                    # first clear miscParams/minFireStartingDamage (21).
                    _ignite = ('fuel' in _name.lower())
                    if not _ignite and 'engine' in _name.lower():
                        _fsc = 0.15
                        try:
                            _eng = getattr(td, 'engine', None)
                            if _eng is not None:
                                _fsc = float(_descriptor_value(_eng, 'fireStartingChance', 0.15))
                        except Exception:
                            pass
                        _ignite = (_shell_dmg >= _device_damage.MIN_FIRE_STARTING_DAMAGE) and (random.random() < _fsc)
                    if _ignite:
                        _offh_ignite(target_mock, is_player_target, _name + ' destroyed')
            elif ('fuel' in _name.lower() and current_hp > 0
                    and not getattr(target_mock, 'is_on_fire', False)
                    and _shell_dmg >= _device_damage.MIN_FIRE_STARTING_DAMAGE):
                # A fuel tank that is merely HOLED can already set the tank alight - that is
                # the whole reason a hit in the tank is feared. The shipped data gives the
                # fuel tank no fire parameter of its own; only the engine carries
                # fireStartingChance (0.12 on the diesel V-2-54), so the roll borrows that
                # behind the same minFireStartingDamage gate. RECONSTRUCTED - destruction
                # still ignites unconditionally above.
                _fsc2 = 0.15
                try:
                    _eng2 = getattr(td, 'engine', None)
                    if _eng2 is not None:
                        _fsc2 = float(_descriptor_value(_eng2, 'fireStartingChance', 0.15))
                except Exception:
                    pass
                if random.random() < _fsc2:
                    _offh_ignite(target_mock, is_player_target, _name + ' holed')
        if _blocked:
            LOG_DEBUG('CRIT GATE: %d device hit(s) behind the stopping plate ignored (no penetration)' % _blocked)
    finally:
        if _own_burst:
            _pending_voice = _OFFH_VOICE_BURST[0] or []
            _OFFH_VOICE_BURST[0] = None
            if len(_pending_voice) > 1:
                LOG_DEBUG('CREWVOICE burst: %d reports from one strike, announcing the worst of %s'
                    % (len(_pending_voice), _pending_voice))
            if _pending_voice:
                _offh_play_crit_voice(_offh_voice_burst_pick(_pending_voice))
    return dmg

def _state(vehicle):
    devices = dict(getattr(vehicle, 'devices_hp', None) or {})
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    crew_ko = set(getattr(vehicle, '_crew_ko', None) or ())
    return {
        'devices': devices,
        'destroyed': destroyed,
        'crew_ko': crew_ko,
        'fire': bool(getattr(vehicle, 'is_on_fire', False)),
        'ammo_rack_death': bool(
            getattr(vehicle, '_ammo_rack_death', False)),
    }


def _device_record(name, hp, descriptor, destroyed):
    max_hp = _device_damage.device_max_hp(descriptor, name)
    if max_hp is None:
        max_hp = max(1, int(round(float(hp or 0.0))))
    return {
        'name': str(name),
        'hp': max(0.0, float(hp)),
        'max_hp': max(1.0, float(max_hp)),
        'state': ('destroyed' if name in destroyed else
                  _device_damage.device_state(float(hp), float(max_hp))),
    }


def _payload(before, after, descriptor, cause=None):
    names = sorted(set(before['devices']) | set(after['devices']))
    device_records = [
        _device_record(name, after['devices'].get(
            name, before['devices'].get(name, 0.0)), descriptor,
            after['destroyed']) for name in names]
    events = []
    for record in device_records:
        name = record['name']
        old_hp = before['devices'].get(name)
        old_max = _device_damage.device_max_hp(descriptor, name)
        if old_hp is None:
            old_state = 'normal'
        elif name in before['destroyed']:
            old_state = 'destroyed'
        else:
            old_state = _device_damage.device_state(
                old_hp, old_max if old_max is not None else record['max_hp'])
        if old_state != record['state']:
            event = {'kind': 'device', 'name': name,
                     'old_state': old_state,
                     'state': record['state']}
            if cause:
                event['cause'] = cause
            events.append(event)
    for name in sorted(after['crew_ko'] - before['crew_ko']):
        event = {'kind': 'crew', 'name': str(name),
                 'state': 'destroyed'}
        if cause:
            event['cause'] = cause
        events.append(event)
    for name in sorted(before['crew_ko'] - after['crew_ko']):
        event = {'kind': 'crew', 'name': str(name), 'state': 'normal'}
        if cause:
            event['cause'] = cause
        events.append(event)
    if before['fire'] != after['fire']:
        event = {'kind': 'fire', 'state': bool(after['fire'])}
        if cause:
            event['cause'] = cause
        events.append(event)
    if (not before['ammo_rack_death'] and
            after['ammo_rack_death']):
        event = {'kind': 'ammo_rack', 'state': 'destroyed'}
        if cause:
            event['cause'] = cause
        events.append(event)
    changed = (events or before['devices'] != after['devices'] or
               before['destroyed'] != after['destroyed'] or
               before['crew_ko'] != after['crew_ko'] or
               before['ammo_rack_death'] != after['ammo_rack_death'])
    if not changed:
        return None
    return {
        'devices': device_records,
        'destroyed': sorted(str(name) for name in after['destroyed']),
        'crew_ko': sorted(str(name) for name in after['crew_ko']),
        'fire': bool(after['fire']),
        'ammo_rack_death': bool(after['ammo_rack_death']),
        'events': events,
    }


def apply_direct(vehicle, collisions, start_pos, end_pos, hull_damage,
                 shell, attacker_id, penetrated=None, by_explosion=False):
    """Run the copied 0.8.2 crit loop and return its authoritative delta."""
    if getattr(vehicle, 'devices_hp', None) is None:
        vehicle.devices_hp = {}
    if getattr(vehicle, '_destroyed_devices', None) is None:
        vehicle._destroyed_devices = set()
    if getattr(vehicle, '_crew_ko', None) is None:
        vehicle._crew_ko = set()
    if not hasattr(vehicle, 'is_on_fire'):
        vehicle.is_on_fire = False
    before = _state(vehicle)
    damage = _apply_module_damage(
        vehicle, collisions, start_pos, end_pos, hull_damage, shell,
        attacker_id, penetrated, by_explosion)
    after = _state(vehicle)
    return damage, _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None),
        'explosion' if by_explosion else 'shot')


class _CriticalProposalVehicle(object):
    """Detached state used while a firing client proposes a critical hit.

    The descriptor, pose and component matrices are immutable inputs to the
    copied 0.8.2 law.  Every mutable battle field is copied explicitly so a
    proposal cannot alter the native Vehicle before the server accepts it.
    """
    __slots__ = (
        'id', 'health', 'typeDescriptor', 'position', 'matrix',
        'devices_hp', '_destroyed_devices', '_crew_ko', '_crew_impaired',
        'is_on_fire', '_ammo_rack_death', '_fire_started', '_fire_timer',
        '_is_killed', 'last_sound', 'is_tracked', 'is_engine_dead',
        'is_gun_destroyed', 'is_turret_locked', '_offline_proposal_only',
        '_components')

    def __init__(self, source):
        self.id = source.id
        self.health = source.health
        self.typeDescriptor = source.typeDescriptor
        self.position = source.position
        self.matrix = source.matrix
        self.devices_hp = dict(
            getattr(source, 'devices_hp', None) or {})
        self._destroyed_devices = set(
            getattr(source, '_destroyed_devices', None) or ())
        self._crew_ko = set(getattr(source, '_crew_ko', None) or ())
        self._crew_impaired = frozenset(
            getattr(source, '_crew_impaired', None) or ())
        self.is_on_fire = bool(getattr(source, 'is_on_fire', False))
        self._ammo_rack_death = bool(
            getattr(source, '_ammo_rack_death', False))
        self._fire_started = getattr(source, '_fire_started', None)
        self._fire_timer = float(
            getattr(source, '_fire_timer', 0.0) or 0.0)
        self._is_killed = bool(getattr(source, '_is_killed', False))
        self.last_sound = getattr(source, 'last_sound', None)
        self.is_tracked = bool(getattr(source, 'is_tracked', False))
        self.is_engine_dead = bool(
            getattr(source, 'is_engine_dead', False))
        self.is_gun_destroyed = bool(
            getattr(source, 'is_gun_destroyed', False))
        self.is_turret_locked = bool(
            getattr(source, 'is_turret_locked', False))
        self._offline_proposal_only = True
        self._components = tuple(source.getComponents())

    def getComponents(self):
        return self._components


def propose_direct(vehicle, collisions, start_pos, end_pos, hull_damage,
                   shell, attacker_id, penetrated=None, by_explosion=False):
    """Return a critical-hit proposal without mutating the live Vehicle."""
    if vehicle is None:
        raise ValueError('critical proposal requires a vehicle')
    shadow = _CriticalProposalVehicle(vehicle)
    return apply_direct(
        shadow, collisions, start_pos, end_pos, hull_damage, shell,
        attacker_id, penetrated, by_explosion)


def apply_payload(vehicle, payload):
    """Install one server-relayed state without re-rolling any damage law."""
    if not isinstance(payload, dict):
        return ()
    before = _state(vehicle)
    was_on_fire = bool(getattr(vehicle, 'is_on_fire', False))
    devices = {}
    for record in payload.get('devices') or ():
        if not isinstance(record, dict):
            continue
        name = record.get('name')
        if name:
            devices[str(name)] = max(0.0, float(record.get('hp', 0.0)))
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = set(
        str(name) for name in payload.get('destroyed') or ())
    vehicle._crew_ko = set(
        str(name) for name in payload.get('crew_ko') or ())
    is_on_fire = bool(payload.get('fire', False))
    if is_on_fire and not was_on_fire:
        _offh_ignite(vehicle, False, 'network')
    elif was_on_fire and not is_on_fire:
        _offh_extinguish(vehicle, False, 'network')
    else:
        vehicle.is_on_fire = is_on_fire
    vehicle._ammo_rack_death = bool(
        payload.get('ammo_rack_death', False))
    _recompute_crew_impaired(vehicle)
    _refresh_mobility_flags(vehicle)
    events = tuple(payload.get('events') or ())
    if events:
        return events
    derived = _payload(
        before, _state(vehicle), getattr(vehicle, 'typeDescriptor', None),
        'network')
    if derived is None:
        return ()
    normalized = []
    for source in derived.get('events') or ():
        event = dict(source)
        if (event.get('state') == 'normal' or
                (event.get('kind') == 'device' and
                 event.get('old_state') == 'destroyed' and
                 event.get('state') == 'critical')):
            event['cause'] = 'repair'
        else:
            event['cause'] = 'shot'
        normalized.append(event)
    return tuple(normalized)


def tick_repair(vehicle, dt, repair_skill=100.0, has_big_kit=False):
    """Advance copied 0.8.2 repair law; transport/presentation stay outside."""
    if vehicle is None or dt is None or dt <= 0.0:
        return None
    if float(getattr(vehicle, 'health', 0.0) or 0.0) <= 0.0:
        return None
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    before = _state(vehicle)
    devices = getattr(vehicle, 'devices_hp', None) or {}
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    for name in list(devices):
        # Only a destroyed device auto-repairs, and only up to critical;
        # a damaged one keeps its damage until a repair kit.
        if name not in destroyed:
            continue
        cap = _device_damage.device_regen_hp(descriptor, name)
        if cap is None or devices[name] >= cap:
            continue
        if (name in _device_damage.NO_REPAIR_PROGRESS_DEVICES and
                bool(getattr(vehicle, 'is_on_fire', False))):
            continue
        devices[name] = _device_damage.repair_step_hp(
            devices[name], name, descriptor, dt, repair_skill, has_big_kit)
        if name in destroyed and devices[name] >= cap:
            destroyed.discard(name)
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = destroyed
    _refresh_mobility_flags(vehicle)
    after = _state(vehicle)
    return _payload(before, after, descriptor, 'repair')


def _restore_fuel_regen_cap(vehicle):
    """Keep the copied fire-out law available to engine-free authorities."""
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    devices = getattr(vehicle, 'devices_hp', None)
    if devices is None:
        return False
    name = 'fuelTankHealth'
    cap = _device_damage.device_regen_hp(descriptor, name)
    if cap is None or devices.get(name, cap) >= cap:
        return False
    devices[name] = cap
    destroyed = getattr(vehicle, '_destroyed_devices', None)
    if destroyed is not None:
        destroyed.discard(name)
    return True


def tick_fire(vehicle, dt, now=None, module_test_mode=False):
    """Advance the copied 0.8.2 fire duration and one-second HP tick."""
    if vehicle is None or dt is None or dt <= 0.0:
        return 0, None
    if (not bool(getattr(vehicle, 'is_on_fire', False)) or
            float(getattr(vehicle, 'health', 0.0) or 0.0) <= 0.0):
        return 0, None
    before = _state(vehicle)
    if now is None:
        try:
            import BigWorld
            now = float(BigWorld.time())
        except Exception:
            now = None
    started = getattr(vehicle, '_fire_started', None)
    if started is None and now is not None:
        started = float(now)
        vehicle._fire_started = started
    if (started is not None and now is not None and
            float(now) - float(started) >=
            _device_damage.FIRE_DURATION_SECONDS):
        # Keep the source ordering: the frame that extinguishes may also
        # complete the final one-second burn tick below.
        _offh_extinguish(vehicle, False, 'burnt out')
        # ``_offh_extinguish`` is a copied presentation helper and imports
        # BigWorld before resolving the descriptor.  The authority simulator is
        # intentionally engine-free, so complete the same fuel-tank transition
        # through the pure descriptor seam as part of this public tick contract.
        _restore_fuel_regen_cap(vehicle)
    timer = float(getattr(vehicle, '_fire_timer', 0.0) or 0.0) + float(dt)
    damage = 0
    if timer >= 1.0:
        timer -= 1.0
        if not module_test_mode:
            damage = max(1, int(
                float(getattr(vehicle, 'maxHealth', 0.0) or 0.0) *
                _device_damage.FIRE_DAMAGE_FRACTION_PER_SEC))
    vehicle._fire_timer = timer
    after = _state(vehicle)
    return damage, _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None), 'repair')


def apply_drowning(vehicle):
    """Apply the copied all-module/all-crew drowning knockout law."""
    if vehicle is None:
        return None
    before = _state(vehicle)
    _offh_knock_out_everything(vehicle, False)
    after = _state(vehicle)
    return _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None), 'drowning')


def apply_death(vehicle, cause='shot'):
    """Apply the copied ordinary-death module/crew/fire terminal state."""
    if vehicle is None:
        return None
    before = _state(vehicle)
    if bool(getattr(vehicle, 'is_on_fire', False)):
        _offh_extinguish(vehicle, False, cause)
    _offh_knock_out_everything(vehicle, False)
    after = _state(vehicle)
    return _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None), cause)


def use_extinguisher(vehicle):
    if vehicle is None or not bool(getattr(vehicle, 'is_on_fire', False)):
        return None
    before = _state(vehicle)
    _offh_extinguish(vehicle, False, 'extinguisher')
    return _payload(
        before, _state(vehicle), getattr(vehicle, 'typeDescriptor', None),
        'repair')


def repair_device(vehicle, name=None, repair_all=False):
    if vehicle is None:
        return None
    before = _state(vehicle)
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    devices = getattr(vehicle, 'devices_hp', None) or {}
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    names = sorted(set(devices) | destroyed)
    if not repair_all:
        if name:
            name = str(name)
            if not name.endswith('Health'):
                name += 'Health'
        if name not in names:
            return None
        names = [name]
    changed = False
    for device_name in names:
        maximum = _device_damage.device_max_hp(descriptor, device_name)
        if maximum is None:
            continue
        if (device_name in destroyed or
                float(devices.get(device_name, maximum)) < float(maximum)):
            devices[device_name] = float(maximum)
            destroyed.discard(device_name)
            changed = True
    if not changed:
        return None
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = destroyed
    _refresh_mobility_flags(vehicle)
    return _payload(before, _state(vehicle), descriptor, 'repair')


def restore_crew(vehicle, name=None, restore_all=False):
    if vehicle is None:
        return None
    before = _state(vehicle)
    crew_ko = set(getattr(vehicle, '_crew_ko', None) or ())
    if restore_all:
        if not crew_ko:
            return None
        crew_ko.clear()
    else:
        name = str(name or '')
        if name not in crew_ko:
            return None
        crew_ko.discard(name)
    vehicle._crew_ko = crew_ko
    _recompute_crew_impaired(vehicle)
    return _payload(
        before, _state(vehicle), getattr(vehicle, 'typeDescriptor', None),
        'repair')


def stat_factor(vehicle, stat):
    if vehicle is None:
        return 1.0
    crew = _crew_factor(vehicle, stat)
    modules = _module_factor(vehicle, stat)
    return crew * modules
