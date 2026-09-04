from __future__ import print_function

"""#1513 critical-damage law derived from the 0.8.2 reconstruction.

The active module now intentionally diverges from the legacy extractor for
#1513 penetration, internal geometry, HE and persistent device states.
``generate_critical_damage.py`` remains an audit-only baseline extractor and
must not overwrite this file.
"""

import math
import random

from gui.mods.offline_lan_0922 import device_damage as _device_damage
from gui.mods.offline_lan_0922 import track_damage as _track_damage


def _descriptor_value(value, name, default=None):
    """Read 0.8.2 mappings or native #1513 item component attributes."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _fire_starting_chance_factor(vehicle):
    """Return the target's mounted-equipment fire chance multiplier."""
    try:
        value = float(getattr(
            vehicle, '_fire_starting_chance_factor', 1.0))
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if value < 0.0 or value != value or value == float('inf'):
        return 1.0
    return value


def _medkit_bonus_value(vehicle):
    """Return the mounted medkit's raw #1513 descriptor bonus value."""
    try:
        value = float(getattr(vehicle, '_medkit_bonus_value', 0.0))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if value != value or value in (float('inf'), -float('inf')):
        return 0.0
    return max(0.0, min(2.0, value))


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
    # #1513 presentation is applied after the authoritative payload arrives.
    return None


def _offh_play_crit_voice(*unused_args, **unused_kwargs):
    # PlayerAvatar.showVehicleDamageInfo owns the #1513 sound/UI mapping.
    return None


def _sync_crashed_track(vehicle, before, after):
    """Mirror authoritative track destruction into stock #1513 visuals.

    The LAN critical payload is our authority record; a remote ``Vehicle``
    does not receive the retail server's track-break mailbox.  Its stock
    ``CompoundAppearance`` still exposes the exact presentation seam,
    ``addCrashedTrack``/``delCrashedTrack``.  Call it only on a confirmed
    destroyed-set edge, so a snapshot replay cannot restart the effect or
    invent a broken track from partial module HP.
    """
    appearance = getattr(vehicle, 'appearance', None)
    if appearance is None:
        return False
    previous = set((before or {}).get('destroyed') or ())
    current = set((after or {}).get('destroyed') or ())
    changed = False
    for name, is_left in (
            ('leftTrackHealth', True), ('rightTrackHealth', False)):
        was_destroyed = name in previous
        is_destroyed = name in current
        if was_destroyed == is_destroyed:
            continue
        method_name = ('addCrashedTrack' if is_destroyed else
                       'delCrashedTrack')
        callback = getattr(appearance, method_name, None)
        if not callable(callback):
            continue
        try:
            callback(is_left)
        except Exception as error:
            # This is optional native presentation; retain the authoritative
            # device state even if a streamed or retiring appearance declines
            # the visual update.
            LOG_DEBUG('crashed-track presentation failed:', str(error))
            continue
        changed = True
    return changed


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
		from gui.mods.offline_lan_0922 import device_damage as _dd
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
				from gui.mods.offline_lan_0922 import device_damage as _DDz
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
		from gui.mods.offline_lan_0922 import device_damage as _DDx
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
		critical = getattr(target_mock, '_critical_devices', None)
		if critical is None:
			critical = set()
			target_mock._critical_devices = critical
		critical.add(name)
		states = getattr(target_mock, '_module_states', None)
		max_hp = _DDx.device_max_hp(td, name)
		new_state = 'critical'
		if states is not None:
			states[name] = new_state
		_push_device_ui(target_mock, is_player_target, name, cap, max_hp, state='repaired')
	except Exception as _fx:
		LOG_DEBUG('fuel tank restore after fire failed:', str(_fx))


def _offh_knock_out_everything(mock):
	'''Put every module at 0 HP and every crewman down for a terminal state.

	The stock #1513 death path owns the terminal damage-panel presentation.'''
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
		_cs = getattr(mock, '_critical_devices', None)
		if _cs is not None:
			_cs.clear()
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
	LOG_DEBUG('KNOCKOUT called: devices=%d crew=%d' % (len(_OFFH_DEATH_DEVICES), len(_roster)))


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

	None means no reliable internal geometry: the feature is switched off, the
	adopted modules are absent, or this tank has no profile. Their build_layout()
	keeps its own cache keyed by type + configuration, so calling it per shot is
	cheap after the first hit on a given tank.'''
	if td is None:
		return None
	try:
		from _constants import CONFIG_OPTIONS as _LCFG
		if not bool(_LCFG.get('internal_layout_profiles', True)):
			return None
	except Exception:
		pass
	try:
		from gui.mods.offline_lan_0922 import internal_hit_layouts as _IHL
	except Exception as _le:
		if not globals().get('_offh_layout_import_logged'):
			globals()['_offh_layout_import_logged'] = True
			LOG_DEBUG('internal_hit_layouts unavailable, using zone model:', str(_le))
		return None
	try:
		layout = _IHL.build_layout(td, log_build=False)
		if not layout or not layout.get('valid'):
			return None
		return layout
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
	if not layout or not layout.get('valid'):
		return None
	targets = layout.get('targets') or ()
	if not targets:
		return None
	import Math
	from gui.mods.offline_lan_0922 import internal_geometry as _IG
	_dx = float(end_pos.x) - float(start_pos.x)
	_dy = float(end_pos.y) - float(start_pos.y)
	_dz = float(end_pos.z) - float(start_pos.z)
	_world_length = (_dx * _dx + _dy * _dy + _dz * _dz) ** 0.5
	if _world_length <= 0.0001:
		return []
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
		# target_interval is a normalized segment parameter. Native collision
		# records use metres from ray start, so keep both sources in one unit before
		# the stopping/exit-plate filters compare them.
		hits.append((float(interval[0]) * _world_length, name))
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


_OFFH_HE_CONE_COS = 0.7071067811865476  # cos(45 degrees)
_OFFH_HE_CONE_EDGE_FACTOR = 1.4142135623730951  # 1 / cos(45 degrees)


def _offh_xyz(value):
	try:
		return float(value.x), float(value.y), float(value.z)
	except Exception:
		return float(value[0]), float(value[1]), float(value[2])


def _offh_he_internal_depth(shell):
	'''0.9.22 HE interior-cone depth in metres: shell caliber / 100.'''
	try:
		caliber = float(_descriptor_value(shell, 'caliber', 0.0) or 0.0)
	except Exception:
		caliber = 0.0
	return max(0.0, caliber / 100.0)


def _offh_internal_cone_hits(target_mock, td, burst_pos, direction, shell,
		covered=()):
	'''Interior modules inside the 0.9.22 HE damage cone.

	The cone starts at the burst point, follows the shell's incoming direction,
	has a 45-degree half angle, and extends caliber / 100 metres along its axis.
	The adopted targets live in current component-local coordinates, so both the
	apex and a direction point use the same world -> vehicle -> component chain as
	the direct-ray resolver. Returns one nearest axial hit per logical device, or
	None when no validated per-vehicle layout exists.
	'''
	layout = _offh_internal_layout(td)
	if not layout or not layout.get('valid'):
		return None
	depth = _offh_he_internal_depth(shell)
	if depth <= 0.0001:
		return []
	try:
		bx, by, bz = _offh_xyz(burst_pos)
		dx, dy, dz = _offh_xyz(direction)
	except Exception:
		return []
	direction_length = (dx * dx + dy * dy + dz * dz) ** 0.5
	if direction_length <= 0.0001:
		return []
	dx /= direction_length
	dy /= direction_length
	dz /= direction_length
	import Math
	from gui.mods.offline_lan_0922 import internal_hit_layouts as _IHL
	inv = Math.Matrix(target_mock.matrix)
	inv.invert()
	world_burst = Math.Vector3(bx, by, bz)
	world_tip = Math.Vector3(
		bx + dx * depth, by + dy * depth, bz + dz * depth)
	vehicle_burst = inv.applyPoint(world_burst)
	vehicle_tip = inv.applyPoint(world_tip)
	contexts = {}
	for compDescr, compMatrix in target_mock.getComponents():
		name = None
		for candidate in ('chassis', 'hull', 'turret', 'gun'):
			if compDescr is getattr(td, candidate, None):
				name = candidate
				break
		if name is None:
			continue
		local_burst = compMatrix.applyPoint(vehicle_burst)
		local_tip = compMatrix.applyPoint(vehicle_tip)
		point = _offh_xyz(local_burst)
		tip = _offh_xyz(local_tip)
		contexts[name] = {
			'point': point,
			'direction': tuple(tip[index] - point[index]
				for index in range(3)),
		}
	_excluded = set()
	for item in covered or ():
		name = str(item)
		if name.endswith('Health'):
			name = name[:-6]
		if name:
			_excluded.add(name)
	# resolve_explosion's radius is radial distance from the apex. A 45-degree
	# finite cone whose AXIAL depth is `depth` reaches sqrt(2) * depth at its rim;
	# pass that enclosing radius, then enforce the exact axial boundary below.
	records = _IHL.resolve_explosion(
		layout, contexts, depth * _OFFH_HE_CONE_EDGE_FACTOR, mode='cone',
		cone_cos=_OFFH_HE_CONE_COS, excluded_entities=_excluded,
		cone_depth_m=depth)
	hits = []
	for record in records:
		if not record.get('damage_eligible', False):
			continue
		entity = record.get('entity')
		context = contexts.get(record.get('parent'))
		hit_point = record.get('hit_point')
		if not entity or not context or hit_point is None:
			continue
		axis = context.get('direction') or (0.0, 0.0, 0.0)
		axis_length = sum(float(value) * float(value)
			for value in axis) ** 0.5
		if axis_length <= 0.0001:
			continue
		axial = record.get('cone_entry_axial_m')
		if axial is None:
			delta = tuple(float(hit_point[index]) -
				float(context['point'][index]) for index in range(3))
			axial = sum(delta[index] * float(axis[index])
				for index in range(3)) / axis_length
		axial = float(axial)
		if axial < -0.0001 or axial > depth + 0.0001:
			continue
		hits.append((max(0.0, float(axial)), str(entity) + 'Health'))
	hits.sort()
	seen = set()
	unique = []
	for distance, name in hits:
		if name in seen:
			continue
		seen.add(name)
		unique.append((distance, name))
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
	from gui.mods.offline_lan_0922 import device_damage as _DDc
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
		from gui.mods.offline_lan_0922 import device_damage as _DDc
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
		from gui.mods.offline_lan_0922 import device_damage as _DDm
		return _DDm.module_stat_factor(getattr(mock, 'devices_hp', None),
		                              getattr(mock, '_destroyed_devices', None),
		                              _device_td(mock), stat,
		                              getattr(mock, '_critical_devices', None))
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


def _record_proposal_crew_damage(mock, crew_name):
	operations = getattr(mock, '_critical_damage_operations', None)
	if operations is not None:
		operations['crew_ko'].add(str(crew_name))


def _record_proposal_device_damage(mock, name, amount, maximum):
	operations = getattr(mock, '_critical_damage_operations', None)
	if operations is None:
		return
	try:
		maximum = max(1.0, float(maximum))
		amount = max(0.0, min(maximum, float(amount)))
	except (TypeError, ValueError, OverflowError):
		return
	if amount <= 0.0005:
		return
	previous = float(operations['devices'].get(name, 0.0) or 0.0)
	operations['devices'][str(name)] = min(maximum, previous + amount)


def _dev_destroyed_set(mock):
	s = getattr(mock, '_destroyed_devices', None)
	if s is None:
		s = set()
		mock._destroyed_devices = s
	return s


def _dev_critical_set(mock):
	s = getattr(mock, '_critical_devices', None)
	if s is None:
		s = set()
		mock._critical_devices = s
	return s


def _module_ui_name(name):
	'''Damage-panel device name = extra name minus 'Health'; tracks keep their side.

	The battle scope defines its own and publishes it over this one.'''
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


def _vehicle_identity(td):
	'''Short "vehicle/chassis" label used only by bounded diagnostics.'''
	name = _descriptor_value(td, 'name')
	if name is None:
		name = _descriptor_value(_descriptor_value(td, 'type'), 'name')
	chassis = _descriptor_value(_descriptor_value(td, 'chassis'), 'name')
	return '%s/%s' % (name, chassis)


def _track_contact_point(contacts, component, distance):
	'''Return the component-local contact recorded for one native hit.

	The match is exact rather than heuristic: a hit's local point is a pure
	function of its component-local ray and its native distance, so two
	collisions sharing a component and a distance necessarily share one point.
	Duplicate material identities, equal-distance boxes and an extended trace
	(same origin, longer end) therefore cannot attach the wrong contact.'''
	for record in contacts or ():
		try:
			record_component, record_distance, point = record
		except (TypeError, ValueError):
			continue
		if str(record_component) != str(component):
			continue
		try:
			if abs(float(record_distance) - float(distance)) > 1.0e-6:
				continue
		except (TypeError, ValueError, OverflowError):
			continue
		return point
	return None


def _track_hp_loss(td, name, material, component, distance, contacts,
		channel_roll, device_loss, shell):
	'''Return (hp_loss, decision) for one solid direct hit on a track.

	The live material's damageKind selects the shell damage channel - normal
	#1513 track materials carry nonzero armour, so vehicles.py resolves their
	"auto" kind to armour damage, index 0.  The chassis-local contact then
	selects the zone: the leading and rearmost driving wheels take the full
	roll, the ordinary middle run takes roll / bulkHealthFactor.

	Any missing, malformed or impossible input keeps the previous
	device-damage result for that one hit, reports at most one bounded
	diagnostic, and never raises.'''
	identity = _vehicle_identity(td)
	if str(component) != _track_damage.TRACK_COMPONENT_NAME:
		_track_damage.report(
			('component', identity, str(component)),
			'component=%s is not the chassis collision model; '
			'keeping device damage (vehicle=%s)' % (component, identity))
		return device_loss, None
	# Resolve every non-random input before drawing a roll, so a fallback can
	# never consume a shell-damage draw the old law would not have made.
	bounds = _track_damage.wheel_zone_bounds(_descriptor_value(td, 'chassis'))
	point = _track_contact_point(contacts, component, distance)
	if bounds is None or point is None:
		_track_damage.report(
			('geometry', identity, name),
			'driving-wheel geometry unavailable: vehicle=%s device=%s '
			'bounds=%s contact=%s; keeping device damage'
			% (identity, name, bounds, point is not None))
		return device_loss, None
	zone = _track_damage.classify_zone(
		point[_track_damage.FORWARD_AXIS], bounds)
	factor = None
	if zone == _track_damage.ZONE_MIDDLE:
		factor = _track_damage.bulk_health_factor(td)
	scale = _track_damage.zone_damage_scale(zone, factor)
	if scale is None:
		_track_damage.report(
			('scale', identity, name, zone),
			'track zone scale unavailable: vehicle=%s device=%s zone=%s '
			'bulk=%s; keeping device damage'
			% (identity, name, zone, factor))
		return device_loss, None
	index = _track_damage.material_damage_index(material)
	if index is None:
		_track_damage.report(
			('damagekind', identity, name),
			'material damageKind unavailable: vehicle=%s device=%s; '
			'keeping device damage' % (identity, name))
		return device_loss, None
	rolled = channel_roll(index)
	if rolled is None:
		_track_damage.report(
			('channel', identity, name, index),
			'shell damage channel %d unavailable: vehicle=%s device=%s; '
			'keeping device damage' % (index, identity, name))
		return device_loss, None
	return rolled * scale, {
		'identity': identity, 'zone': zone,
		'local_z': point[_track_damage.FORWARD_AXIS],
		'bounds': bounds, 'index': index, 'factor': factor,
		'base': _device_damage.shell_damage_base(shell, index)}


def _apply_module_damage(target_mock, all_hits, start_pos, end_pos, dmg, _shell,
		attacker_id, penetrated=None, by_explosion=False, internal_hits=None,
		distance_filters=True, deadeye=False, collision_contacts=None):
	'''Roll module and crew crits for one strike.

	penetrated: True the shell got through, False it did not, None unknown (the
	bot call sites, which already sit behind their own penetration branch).
	False restricts the roll to devices IN FRONT of the plate that stopped the
	round - see the _stop_d block below.

	by_explosion: this is HE splash rather than a solid hit, so every saving throw
	reads the material's chanceToHitByExplosion. Blast reaches externally mounted
	gear far more readily than it reaches anything behind a plate, which is what
	the two separate XML values encode.

	internal_hits: a precomputed HE cone. None selects the ordinary solid-shell
	ray; an empty tuple explicitly means the cone crossed no internal target.
	distance_filters is disabled for that cone because its distances start at the
	burst, while native collision distances start at the external trace origin.
	deadeye adds the official three percentage points only to AP/APCR/HEAT.

	collision_contacts: private (component, distance, local_point) evidence for
	this exact strike, produced beside the native collisions themselves. Only
	the track zone law reads it; without it a track hit keeps the previous
	device-damage behaviour.'''
	import BigWorld, Math, random
	from gui.mods.offline_lan_0922 import device_damage as _device_damage
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
	_critical_devices = _dev_critical_set(target_mock)
	# Shells carry damage as (armor, devices); there is no 'deviceDamage' key.
	# One roll per damage channel per strike: every ordinary device keeps
	# sharing the single devices roll it has always shared, while a track
	# material whose damageKind selects armour draws that channel once. The
	# resolved hull damage is never reused as a track roll - a pure track hit
	# legitimately deals zero hull damage but still needs a full track roll.
	_channel_rolls = {}

	def _channel_roll(index):
		if index not in _channel_rolls:
			_channel_rolls[index] = _device_damage.module_damage_roll(
				_shell, index)
		return _channel_rolls[index]

	_shell_dmg = _channel_roll(_track_damage.DEVICE_DAMAGE_INDEX)
	if _shell_dmg is None:
		_shell_dmg = dmg
	_shell_kind = str(_descriptor_value(_shell, 'kind', '') or '')
	_deadeye_bonus = (0.03 if deadeye and _shell_kind in (
		'ARMOR_PIERCING', 'ARMOR_PIERCING_CR', 'HOLLOW_CHARGE') else 0.0)
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
	if distance_filters:
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
	if distance_filters and penetrated is False:
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
	# gunBreech and nothing else. Adopted per-tank profiles provide the only
	# reliable interior boxes. Without one, fail closed instead of inventing a
	# compartment hit; native external device geometry still runs below.
	_scored = all_hits
	if (internal_hits is not None or penetrated is not False) and bool(
			_MDCFG.get('internal_module_damage', True)):
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
			if internal_hits is None:
				_real = _offh_internal_ray_hits(
					target_mock, td, start_pos, end_pos, _covered)
			else:
				_real = list(internal_hits)
			if _real is not None:
				if not _crew_on:
					_real = [_r for _r in _real if _r[1][:-6] not in _rost]
				if _real:
					LOG_DEBUG('INTERIOR %s GEOMETRY: %s' % (
						'EXPLOSION' if internal_hits is not None else 'RAY',
						', '.join(['%s@%.2f' % (_n2, _d2)
							for _d2, _n2 in _real])))
					_scored = list(all_hits)
					for _d2, _n2 in _real:
						_scored.append((_d2, 1.0, _SynthMaterial(_n2), None))
				else:
					LOG_DEBUG('INTERIOR GEOMETRY: shell path crossed no interior box')
			else:
				# No per-tank profile means no reliable interior geometry. Do not
				# manufacture a guaranteed compartment candidate and then apply a
				# second chance roll; external/native device boxes are still scored.
				LOG_DEBUG('INTERIOR GEOMETRY: unavailable; internal crit skipped')
		except Exception as _ie:
			LOG_DEBUG('interior roll err:', str(_ie))
	# Re-entrant: HE splash scores other vehicles through this same function, so
	# only the outermost strike owns the collector.
	_own_burst = _OFFH_VOICE_BURST[0] is None
	if _own_burst:
		_OFFH_VOICE_BURST[0] = []
	try:
		_blocked = 0
		_rolled_names = set()
		for h in sorted(_scored, key=lambda _entry: _entry[0]):
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
				if _name in _rolled_names:
					continue
				_rolled_names.add(_name)
				_chance = min(1.0, _device_damage.saving_throw(
					h_mat, _name, by_explosion) + _deadeye_bonus)
				# #1513 stores 0.30 for the advertised 15% large-medkit
				# protection. Apply the artefact value at the crew-hit roll.
				_chance *= max(
					0.0, 1.0 - 0.5 * _medkit_bonus_value(target_mock))
				if _crew_on and random.random() < _chance:
					_record_proposal_crew_damage(target_mock, _name[:-6])
					if _knock_out_crew(target_mock, _name[:-6], is_player_target):
						_crew_hit = True
						target_mock.last_sound = 'armor_pierced_crit_by_player' if is_player_attacker else 'armor_pierced_crit'
				continue
			# INCLUSION list: only real, modelled devices are scored. The old exclusion
			# list ('everything except tracks and gun') both credited unmodelled extras
			# AND made track/gun crits impossible.
			if _name not in _device_damage._DEVICE_HP_SPEC:
				continue
			if _name in _rolled_names:
				continue
			_rolled_names.add(_name)
			_chance = min(1.0, _device_damage.saving_throw(
				h_mat, _name, by_explosion) + _deadeye_bonus)
			if random.random() >= _chance:
				continue   # saving throw failed: no crit on this device
			max_hp = _device_damage.device_max_hp(td, _name)
			if max_hp is None:
				max_hp = 100
			# Update 6.4 split the track into leading/rearmost driving wheels
			# and an ordinary middle run. Solid direct hits only: HE direct and
			# splash stay on the previous device-damage law until the same
			# evidence covers them.
			_loss = _shell_dmg
			_track_decision = None
			if _name in _track_damage.TRACK_DEVICE_NAMES and not by_explosion:
				_loss, _track_decision = _track_hp_loss(
					td, _name, h_mat, h_comp, h_dist, collision_contacts,
					_channel_roll, _shell_dmg, _shell)
			_record_proposal_device_damage(
				target_mock, _name, _loss, max_hp)
			previous_hp = target_mock.devices_hp.get(_name, max_hp)
			current_hp = previous_hp - _loss
			# Clamp at 0 so auto-repair does not have to climb out of a deficit.
			if current_hp < 0:
				current_hp = 0
			target_mock.devices_hp[_name] = current_hp
			if _track_decision is not None:
				# Bounded to one line per target, side and zone, so a Windows
				# check can read all three zones without per-shot spam.
				_track_damage.report(
					('zone', getattr(target_mock, 'id', '?'), _name,
						_track_decision['zone']),
					'zone target=%s vehicle=%s device=%s z=%.3f '
					'front>=%.3f rear<=%.3f zone=%s damageKind=%d base=%s '
					'bulk=%s loss=%.1f hp=%.1f->%.1f' % (
						getattr(target_mock, 'id', '?'),
						_track_decision['identity'], _name,
						_track_decision['local_z'],
						_track_decision['bounds'][0],
						_track_decision['bounds'][1],
						_track_decision['zone'], _track_decision['index'],
						_track_decision['base'], _track_decision['factor'],
						_loss, previous_hp, current_hp))
			_destroyed_devices = _dev_destroyed_set(target_mock)
			if current_hp <= 0:
				_destroyed_devices.add(_name)
				_critical_devices.discard(_name)
			elif (_name in _critical_devices or
					_device_damage.device_state(current_hp, max_hp) == 'critical'):
				_critical_devices.add(_name)
			else:
				_critical_devices.discard(_name)
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
				_refresh_mobility_flags(target_mock)
				# Opening frame at 0%, so the bar appears the instant the module breaks
				# instead of only on the next repair tick - but not when this very shot is
				# killing the tank, or the panel starts a repair on a wreck.
				if (is_player_target and not getattr(target_mock, '_offline_proposal_only', False) and not getattr(target_mock, '_is_killed', False)) and (getattr(target_mock, 'health', 0) or 0) > 0:
					try:
						import gui.WindowsManager as _WMrb
						_bwrb = getattr(_WMrb.g_windowsManager, 'battleWindow', None)
						if _bwrb is not None and hasattr(_bwrb, 'damagePanel'):
							from gui.mods.offline_lan_0922 import device_damage as _DDrb
							_secs0 = _DDrb.repair_seconds(_name, td)
							_bwrb.damagePanel.updateModuleRepair(_module_ui_name(_name), 0, _secs0)
					except Exception: pass
			# The fuel tank starts a fire only when this successful hit reduces it to
			# zero. Engine fire is independent of the yellow/red state threshold, but
			# common/vehicle.xml still requires the rolled device damage to reach
			# miscParams/minFireStartingDamage before fireStartingChance is rolled.
			_hp_lost = current_hp < previous_hp
			if (_hp_lost and current_hp <= 0 and
					'fuel' in _name.lower() and
					not getattr(target_mock, 'is_on_fire', False)):
				_offh_ignite(target_mock, is_player_target, _name + ' destroyed')
			elif (_hp_lost and 'engine' in _name.lower() and
					not getattr(target_mock, 'is_on_fire', False)):
				_fsc = 0.15
				_min_fire_damage = _device_damage.MIN_FIRE_STARTING_DAMAGE
				try:
					_eng = getattr(td, 'engine', None)
					if _eng is not None:
						_fsc = float(_descriptor_value(
							_eng, 'fireStartingChance', 0.15))
						_min_fire_damage = float(_descriptor_value(
							_eng, 'minFireStartingDamage',
							_device_damage.MIN_FIRE_STARTING_DAMAGE))
				except Exception:
					pass
				_fsc *= _fire_starting_chance_factor(target_mock)
				if (_shell_dmg >= max(0.0, _min_fire_damage) and
						_fsc > 0.0 and random.random() < _fsc):
					_offh_ignite(target_mock, is_player_target,
						_name + ' damaged')
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
    critical = set(getattr(vehicle, '_critical_devices', None) or ())
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    for name, hp in devices.items():
        maximum = _device_damage.device_max_hp(descriptor, name)
        if (name not in destroyed and
                _device_damage.device_state(hp, maximum) == 'critical'):
            critical.add(name)
    critical.difference_update(destroyed)
    crew_ko = set(getattr(vehicle, '_crew_ko', None) or ())
    return {
        'devices': devices,
        'destroyed': destroyed,
        'critical': critical,
        'crew_ko': crew_ko,
        'fire': bool(getattr(vehicle, 'is_on_fire', False)),
        'ammo_rack_death': bool(
            getattr(vehicle, '_ammo_rack_death', False)),
    }


def _device_record(name, hp, descriptor, destroyed, critical):
    max_hp = _device_damage.device_max_hp(descriptor, name)
    if max_hp is None:
        max_hp = max(1, int(round(float(hp or 0.0))))
    return {
        'name': str(name),
        'hp': max(0.0, float(hp)),
        'max_hp': max(1.0, float(max_hp)),
        'state': ('destroyed' if name in destroyed else
                  'critical' if name in critical else
                  _device_damage.device_state(float(hp), float(max_hp))),
    }


def _payload(before, after, descriptor, cause=None, force=False):
    names = sorted(set(before['devices']) | set(after['devices']) |
                   set(before['critical']) | set(after['critical']) |
                   set(before['destroyed']) | set(after['destroyed']))
    device_records = [
        _device_record(name, after['devices'].get(
            name, before['devices'].get(name, 0.0)), descriptor,
            after['destroyed'], after['critical']) for name in names]
    events = []
    for record in device_records:
        name = record['name']
        old_hp = before['devices'].get(name)
        old_max = _device_damage.device_max_hp(descriptor, name)
        if old_hp is None:
            old_state = 'normal'
        elif name in before['destroyed']:
            old_state = 'destroyed'
        elif name in before['critical']:
            old_state = 'critical'
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
               before['critical'] != after['critical'] or
               before['crew_ko'] != after['crew_ko'] or
               before['ammo_rack_death'] != after['ammo_rack_death'])
    if not changed and not force:
        return None
    return {
        'devices': device_records,
        'destroyed': sorted(str(name) for name in after['destroyed']),
        'crew_ko': sorted(str(name) for name in after['crew_ko']),
        'fire': bool(after['fire']),
        'ammo_rack_death': bool(after['ammo_rack_death']),
        'events': events,
    }


def _damage_delta(before, after, descriptor, operations=None):
    """Describe irreversible operations introduced by one proposal.

    The full critical payload remains useful for presentation, but it cannot
    be installed after unrelated authority-owned repair/fire progress: doing so
    would restore the proposal's stale snapshot. Device and crew entries record
    the successful native operation before the detached target's stale HP/KO
    state can clamp it away. The fire bit deliberately retains its existing
    state-transition meaning until ignition receives its own conditional receipt.
    """
    devices = []
    crew_ko = []
    if isinstance(operations, dict):
        for name in sorted(operations.get('devices') or {}):
            hp_loss = float(operations['devices'][name])
            if hp_loss > 0.0005:
                devices.append({
                    'name': str(name),
                    'hp_loss': round(hp_loss, 3),
                })
        crew_ko = sorted(str(name) for name in
                         (operations.get('crew_ko') or ()))
    else:
        names = sorted(set(before['devices']) | set(after['devices']) |
                       set(before['critical']) | set(after['critical']) |
                       set(before['destroyed']) | set(after['destroyed']))
        for name in names:
            maximum = _device_damage.device_max_hp(descriptor, name)
            if maximum is None:
                continue
            maximum = max(1.0, float(maximum))
            before_hp = max(0.0, min(
                maximum, float(before['devices'].get(name, maximum))))
            after_hp = max(0.0, min(
                maximum, float(after['devices'].get(name, before_hp))))
            hp_loss = max(0.0, before_hp - after_hp)
            if hp_loss <= 0.0005:
                continue
            devices.append({
                'name': str(name),
                'hp_loss': round(hp_loss, 3),
            })
        crew_ko = sorted(str(name) for name in
                         (after['crew_ko'] - before['crew_ko']))
    result = {
        'devices': devices,
        'crew_ko': crew_ko,
        'ignite': bool(after['fire'] and not before['fire']),
    }
    return result


def apply_direct(vehicle, collisions, start_pos, end_pos, hull_damage,
                 shell, attacker_id, penetrated=None, by_explosion=False,
                 deadeye=False, _internal_hits=None,
                 _distance_filters=True, collision_contacts=None):
    """Run the copied 0.8.2 crit loop and return its authoritative delta."""
    if getattr(vehicle, 'devices_hp', None) is None:
        vehicle.devices_hp = {}
    if getattr(vehicle, '_destroyed_devices', None) is None:
        vehicle._destroyed_devices = set()
    if getattr(vehicle, '_critical_devices', None) is None:
        vehicle._critical_devices = set(_state(vehicle)['critical'])
    if getattr(vehicle, '_crew_ko', None) is None:
        vehicle._crew_ko = set()
    if not hasattr(vehicle, 'is_on_fire'):
        vehicle.is_on_fire = False
    before = _state(vehicle)
    damage = _apply_module_damage(
        vehicle, collisions, start_pos, end_pos, hull_damage, shell,
        attacker_id, penetrated, by_explosion,
        internal_hits=_internal_hits,
        distance_filters=_distance_filters, deadeye=deadeye,
        collision_contacts=collision_contacts)
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
        'devices_hp', '_destroyed_devices', '_critical_devices',
        '_crew_ko', '_crew_impaired',
        'is_on_fire', '_ammo_rack_death', '_fire_started', '_fire_timer',
        '_is_killed', 'last_sound', 'is_tracked', 'is_engine_dead',
        'is_gun_destroyed', 'is_turret_locked', '_offline_proposal_only',
        '_components', '_fire_starting_chance_factor',
        '_medkit_bonus_value', '_critical_damage_operations')

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
        self._critical_devices = set(_state(source)['critical'])
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
        self._fire_starting_chance_factor = \
            _fire_starting_chance_factor(source)
        self._medkit_bonus_value = _medkit_bonus_value(source)
        self._critical_damage_operations = {
            'devices': {}, 'crew_ko': set()}
        self._components = tuple(source.getComponents())

    def getComponents(self):
        return self._components


def propose_direct(vehicle, collisions, start_pos, end_pos, hull_damage,
                   shell, attacker_id, penetrated=None, by_explosion=False,
                   deadeye=False, with_delta=False, collision_contacts=None):
    """Return a critical-hit proposal without mutating the live Vehicle."""
    if vehicle is None:
        raise ValueError('critical proposal requires a vehicle')
    shadow = _CriticalProposalVehicle(vehicle)
    before = _state(shadow)
    damage, payload = apply_direct(
        shadow, collisions, start_pos, end_pos, hull_damage, shell,
        attacker_id, penetrated, by_explosion, deadeye,
        collision_contacts=collision_contacts)
    if with_delta:
        after = _state(shadow)
        delta = _damage_delta(
            before, after, shadow.typeDescriptor,
            shadow._critical_damage_operations)
        if (payload is None and
                (delta['devices'] or delta['crew_ko'])):
            payload = _payload(
                before, after, shadow.typeDescriptor,
                'explosion' if by_explosion else 'shot', force=True)
        return damage, payload, delta
    return damage, payload


def apply_explosion(vehicle, collisions, burst, direction, hull_damage,
                    shell, attacker_id, deadeye=False):
    """Apply one HE interior cone and return its authoritative delta.

    Penetrating HE, a non-penetrating direct hit, and remote splash all use this
    same entry point. Native collision materials still score exposed modules;
    adopted interior targets come only from the finite cone, never the solid
    projectile ray.
    """
    covered = set()
    for collision in collisions or ():
        try:
            material = collision[2]
            extra = getattr(material, 'extra', None)
            if extra is not None:
                covered.add(str(getattr(extra, 'name', '')))
        except Exception:
            continue
    try:
        hits = _offh_internal_cone_hits(
            vehicle, getattr(vehicle, 'typeDescriptor', None), burst,
            direction, shell, covered)
    except Exception as error:
        LOG_DEBUG('HE interior cone unavailable:', str(error))
        hits = None
    if hits is None:
        hits = ()
    return apply_direct(
        vehicle, tuple(collisions or ()), burst, burst, hull_damage,
        shell, attacker_id, penetrated=None, by_explosion=True,
        deadeye=deadeye, _internal_hits=tuple(hits),
        _distance_filters=False)


def propose_explosion(vehicle, collisions, burst, direction, hull_damage,
                      shell, attacker_id, deadeye=False, with_delta=False):
    """Return an HE-cone critical proposal without mutating the live Vehicle."""
    if vehicle is None:
        raise ValueError('critical proposal requires a vehicle')
    shadow = _CriticalProposalVehicle(vehicle)
    before = _state(shadow)
    damage, payload = apply_explosion(
        shadow, collisions, burst, direction, hull_damage, shell,
        attacker_id, deadeye)
    if with_delta:
        after = _state(shadow)
        delta = _damage_delta(
            before, after, shadow.typeDescriptor,
            shadow._critical_damage_operations)
        if (payload is None and
                (delta['devices'] or delta['crew_ko'])):
            payload = _payload(
                before, after, shadow.typeDescriptor,
                'explosion', force=True)
        return damage, payload, delta
    return damage, payload


def apply_payload(vehicle, payload):
    """Install one server-relayed state without re-rolling any damage law."""
    if not isinstance(payload, dict):
        return ()
    before = _state(vehicle)
    was_on_fire = bool(getattr(vehicle, 'is_on_fire', False))
    devices = {}
    critical = set()
    for record in payload.get('devices') or ():
        if not isinstance(record, dict):
            continue
        name = record.get('name')
        if name:
            name = str(name)
            devices[name] = max(0.0, float(record.get('hp', 0.0)))
            if record.get('state') == 'critical':
                critical.add(name)
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = set(
        str(name) for name in payload.get('destroyed') or ())
    critical.difference_update(vehicle._destroyed_devices)
    vehicle._critical_devices = critical
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
    _sync_crashed_track(vehicle, before, _state(vehicle))
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


def tick_repair(vehicle, dt, repair_skill=100.0, has_big_kit=False,
                repair_factor=None):
    """Advance copied 0.8.2 repair law; transport/presentation stay outside."""
    if vehicle is None or dt is None or dt <= 0.0:
        return None
    if float(getattr(vehicle, 'health', 0.0) or 0.0) <= 0.0:
        return None
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    before = _state(vehicle)
    devices = getattr(vehicle, 'devices_hp', None) or {}
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    critical = set(getattr(vehicle, '_critical_devices', None) or ())
    for name in list(devices):
        # Retail automatic repair starts only after a module is destroyed.
        # Hidden HP loss and a functional yellow module persist until a repair
        # kit or another explicit recovery event changes them.
        if name not in destroyed:
            continue
        cap = _device_damage.device_regen_hp(descriptor, name)
        if cap is None or devices[name] >= cap:
            continue
        if (name in _device_damage.NO_REPAIR_PROGRESS_DEVICES and
                bool(getattr(vehicle, 'is_on_fire', False))):
            continue
        devices[name] = _device_damage.repair_step_hp(
            devices[name], name, descriptor, dt, repair_skill, has_big_kit,
            repair_factor)
        was_destroyed = name in destroyed
        if was_destroyed and devices[name] >= cap:
            destroyed.discard(name)
            critical.add(name)
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = destroyed
    vehicle._critical_devices = critical
    _refresh_mobility_flags(vehicle)
    after = _state(vehicle)
    return _payload(before, after, descriptor, 'repair')


def damage_device_over_time(vehicle, name, amount, cause='equipment'):
    """Subtract deterministic HP from one module and return its state payload.

    This is the server-side part of effects such as Removed RPM Limiter.  It
    deliberately bypasses projectile saving throws: the equipment definition
    already specifies direct engine HP loss per second.
    """
    if vehicle is None:
        return None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    if amount <= 0.0 or float(
            getattr(vehicle, 'health', 0.0) or 0.0) <= 0.0:
        return None
    name = str(name or '')
    if not name.endswith('Health'):
        name += 'Health'
    descriptor = getattr(vehicle, 'typeDescriptor', None)
    maximum = _device_damage.device_max_hp(descriptor, name)
    if maximum is None:
        return None
    before = _state(vehicle)
    devices = dict(getattr(vehicle, 'devices_hp', None) or {})
    destroyed = set(getattr(vehicle, '_destroyed_devices', None) or ())
    critical = set(getattr(vehicle, '_critical_devices', None) or ())
    current = max(0.0, float(devices.get(name, maximum)))
    if current <= 0.0:
        return None
    devices[name] = max(0.0, current - amount)
    if devices[name] <= 0.0:
        destroyed.add(name)
        critical.discard(name)
    elif (name in critical or _device_damage.device_state(
            devices[name], maximum) == 'critical'):
        critical.add(name)
    else:
        critical.discard(name)
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = destroyed
    vehicle._critical_devices = critical
    _refresh_mobility_flags(vehicle)
    return _payload(before, _state(vehicle), descriptor, cause)


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
    critical = getattr(vehicle, '_critical_devices', None)
    if critical is None:
        critical = set()
        vehicle._critical_devices = critical
    critical.add(name)
    return True


def tick_fire(vehicle, dt, now=None, module_test_mode=False):
    """Advance every elapsed fire second without losing slow-frame time."""
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
    dt = float(dt)
    started = getattr(vehicle, '_fire_started', None)
    if started is None and now is not None:
        # An engine-free authority can receive an already-burning snapshot
        # without BigWorld.time(). The tank was burning throughout this rule
        # interval, so anchor the missing edge at its beginning, not its end.
        started = float(now) - dt
        vehicle._fire_started = started
    active_dt = dt
    burnt_out = False
    if started is not None and now is not None:
        now = float(now)
        started = float(started)
        fire_end = started + _device_damage.FIRE_DURATION_SECONDS
        active_start = max(now - dt, started)
        active_end = min(now, fire_end)
        active_dt = max(0.0, active_end - active_start)
        burnt_out = now >= fire_end
    timer = (float(getattr(vehicle, '_fire_timer', 0.0) or 0.0) +
             active_dt)
    damage = 0
    completed_ticks = int(math.floor(timer + 1e-9))
    if completed_ticks > 0:
        timer = max(0.0, timer - float(completed_ticks))
        if not module_test_mode:
            damage_per_tick = max(1, int(
                float(getattr(vehicle, 'maxHealth', 0.0) or 0.0) *
                _device_damage.FIRE_DAMAGE_FRACTION_PER_SEC))
            damage = damage_per_tick * completed_ticks
    vehicle._fire_timer = timer
    if burnt_out:
        # The interval may complete the final burn tick before the fire-out
        # transition. Only time after this exact boundary is excluded.
        _offh_extinguish(vehicle, False, 'burnt out')
        # ``_offh_extinguish`` is a copied presentation helper and imports
        # BigWorld before resolving the descriptor.  The authority simulator is
        # intentionally engine-free, so complete the same fuel-tank transition
        # through the pure descriptor seam as part of this public tick contract.
        _restore_fuel_regen_cap(vehicle)
    after = _state(vehicle)
    return damage, _payload(
        before, after, getattr(vehicle, 'typeDescriptor', None), 'repair')


def apply_drowning(vehicle):
	"""Apply the copied all-module/all-crew drowning knockout law."""
	if vehicle is None:
		return None
	before = _state(vehicle)
	_offh_knock_out_everything(vehicle)
	after = _state(vehicle)
	return _payload(
		before, after, getattr(vehicle, 'typeDescriptor', None), 'drowning')


def propose_drowning(vehicle):
	"""Return the drowning terminal state without mutating a live Vehicle."""
	if vehicle is None:
		raise ValueError('critical proposal requires a vehicle')
	return apply_drowning(_CriticalProposalVehicle(vehicle))


def apply_death(vehicle, cause='shot'):
    """Apply the copied ordinary-death module/crew/fire terminal state."""
    if vehicle is None:
        return None
    before = _state(vehicle)
    if bool(getattr(vehicle, 'is_on_fire', False)):
        _offh_extinguish(vehicle, False, cause)
    _offh_knock_out_everything(vehicle)
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
    critical = set(getattr(vehicle, '_critical_devices', None) or ())
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
            critical.discard(device_name)
            changed = True
    if not changed:
        return None
    vehicle.devices_hp = devices
    vehicle._destroyed_devices = destroyed
    vehicle._critical_devices = critical
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
