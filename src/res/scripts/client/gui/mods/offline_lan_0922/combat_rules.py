from __future__ import print_function

"""Pinned #1513 armour and HE laws behind native input adapters."""

import math
import random


# The hull resolver also accepts already-crossed destructible loss before the
# projectile enters the vehicle's ordered material layers.


_OFFH_RANGE_NEAR = 100.0
_OFFH_RANGE_FAR = 500.0
_OFFH_RANGE_SPAN = _OFFH_RANGE_FAR - _OFFH_RANGE_NEAR
_OFFH_MIN_HIT_ANGLE_COS = 1.0e-5
_OFFH_MISSING = object()


def _offh_range_piercing(shot, dist_m):
	'''Non-randomized P100/P500 interpolation with the shell range cutoff.'''
	pp = shot.get('piercingPower', (100.0, 100.0))
	try:
		p100 = float(pp[0]); p500 = float(pp[1])
	except Exception:
		p100 = p500 = 100.0
	try:
		distance = float(dist_m)
	except Exception:
		distance = 0.0
	try:
		maximum = float(shot.get('maxDistance', 0.0) or 0.0)
	except Exception:
		maximum = 0.0
	# maxDistance is a projectile lifetime boundary, not the interpolation
	# endpoint.  The descriptor's two piercing values are always P100/P500, and
	# their fixed slope continues beyond 500 m until the lifetime boundary.
	if distance <= _OFFH_RANGE_NEAR:
		return p100
	if maximum <= 0.0 or distance >= maximum:
		return 0.0
	t = (distance - _OFFH_RANGE_NEAR) / _OFFH_RANGE_SPAN
	return max(0.0, p100 + (p500 - p100) * t)


def _offh_material_value(material, name, default=_OFFH_MISSING):
	if material is None:
		return default
	if isinstance(material, dict):
		return material.get(name, default)
	return getattr(material, name, default)


def _offh_material_flag(material, name, default):
	if name == 'checkCaliberForRicochet':
		# Exact #1513 MaterialInfo exposes only WG's historical ``Richet`` typo.
		# Prefer that field when both spellings exist; the correctly-spelled name
		# remains a fallback for legacy synthetic material fixtures.
		value = _offh_material_value(
			material, 'checkCaliberForRichet', _OFFH_MISSING)
		if value is _OFFH_MISSING:
			value = _offh_material_value(material, name, _OFFH_MISSING)
	else:
		value = _offh_material_value(material, name, _OFFH_MISSING)
	if value is _OFFH_MISSING:
		return bool(default)
	return bool(value)


def _offh_resolve_armor_contact(shot, dist_m, all_hits,
		initial_pierce_loss=0.0, penetration_factor=None,
		base_penetration_multiplier=1.0):
	'''Resolve the terminal external or structural plate in projectile order.

	Returns a contact dictionary for the plate that stops external traversal or
	the first structural plate.  accumulated_loss is the penetration already
	spent before that returned plate.  None means no structural plate was reached
	and every external plate that was found was penetrated.

	Tracks and external devices carry vehicleDamageFactor 0.0: they are not the
	hull, so they never take hull damage.  They are nevertheless real plates: the
	shell must penetrate each one, then pays that plate's effective thickness.

	HEAT continues as a jet after penetrating an external layer.  It pays both
	the plate and 5 percent of its rolled penetration per 10 cm travelled before
	the next layer.  One shot-owned penetration factor is reused for every layer.'''
	if not all_hits:
		return None
	shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
	kind = shell.get('kind', 'ARMOR_PIERCING')
	spaced = float(initial_pierce_loss or 0.0)
	factor = penetration_factor
	base_pierce = None
	heat_last_plate_distance = None
	seen_once = set()
	try:
		_ordered = sorted(all_hits, key=lambda h: h[0])
	except Exception:
		_ordered = all_hits
	for _h in _ordered:
		try:
			_d, _ac, _mat = _h[0], _h[1], _h[2]
		except Exception:
			continue
		_comp = _h[3] if len(_h) > 3 else None
		if factor is None:
			factor = random.uniform(0.75, 1.25)
		if base_pierce is None:
			base_pierce = (_offh_range_piercing(shot, dist_m) *
				float(base_penetration_multiplier) * float(factor))
		if kind == 'HOLLOW_CHARGE' and heat_last_plate_distance is not None:
			try:
				_gap = max(0.0, float(_d) - heat_last_plate_distance)
			except Exception:
				_gap = 0.0
			# #1513 charges the current collision's jet gap before it decides
			# whether that material collision is ignored as a duplicate.
			_remaining = max(0.0, base_pierce - spaced)
			spaced += _remaining * min(1.0, 0.5 * _gap)
		if _mat is None:
			if base_pierce - spaced <= 0.0:
				break
			if kind == 'HOLLOW_CHARGE':
				heat_last_plate_distance = float(_d)
			continue
		_once_key = None
		if _offh_material_flag(_mat, 'collideOnceOnly', False):
			_mat_kind = _offh_material_value(_mat, 'kind', _OFFH_MISSING)
			_once_key = ((_comp, _mat_kind) if _mat_kind is not _OFFH_MISSING
				else (_comp, id(_mat)))
			if _once_key in seen_once:
				continue
		_vdf = _offh_material_value(_mat, 'vehicleDamageFactor', 1.0)
		_arm = float(_offh_material_value(_mat, 'armor', 0.0) or 0.0)
		if _arm < 0.0:
			continue
		_res, _eff, _p = _offh_penetration(
			shot, dist_m, _arm, _ac, spaced, factor, _mat,
			not (kind == 'HOLLOW_CHARGE' and
				heat_last_plate_distance is not None),
			base_penetration_multiplier)
		_contact = {
			'result': _res,
			'layer': ('structural' if bool(_vdf) else 'external'),
			'distance': _d,
			'material': _mat,
			'accumulated_loss': spaced,
			'effective_armor': _eff,
			'piercing': _p,
			'angle_cos': _ac,
			'component': _comp,
		}
		if not _vdf:
			if kind == 'HIGH_EXPLOSIVE':
				# In 0.9.22 an HE shell detonates after hitting spaced armour,
				# even when it penetrates that external layer.  It never carries
				# solid-shot penetration through to the underlying hull plate.
				_contact['result'] = 1
				return _contact
			if _res != 2:
				return _contact
			spaced += _eff
			if base_pierce - spaced <= 0.0:
				# The screen itself was penetrated, but no power remains to reach
				# structure.  #1513 terminates at this external contact.
				_contact['result'] = 1
				return _contact
			if kind == 'HOLLOW_CHARGE':
				# The native preview starts the air gap behind the nominal plate.
				heat_last_plate_distance = float(_d) + _arm * 0.001
			if _once_key is not None:
				seen_once.add(_once_key)
			continue
		return _contact
	return None


def _offh_resolve_hull_hit(shot, dist_m, all_hits, initial_pierce_loss=0.0,
		penetration_factor=None, base_penetration_multiplier=1.0):
	'''Keep the legacy structural-hit tuple contract unchanged.'''
	contact = _offh_resolve_armor_contact(
		shot, dist_m, all_hits, initial_pierce_loss, penetration_factor,
		base_penetration_multiplier)
	if contact is None or contact['layer'] != 'structural':
		return None
	return (contact['result'], contact['effective_armor'],
		contact['piercing'], contact['accumulated_loss'],
		contact['angle_cos'])


# Exact #1513 common vehicle.xml defaults. Individual HE shell types carry the
# same three fields and may override any of them. Damage falls linearly from
# damageFactor at the burst centre to edgeDamageFactor at explosionRadius,
# then nominal armour is subtracted through damageAbsorptionFactor.
_OFFH_HE_DAMAGE_FACTOR = 0.5
_OFFH_HE_DAMAGE_ABSORPTION_FACTOR = 1.3
_OFFH_HE_EDGE_DAMAGE_FACTOR = 0.15
_OFFH_HE_TUNING_OVERRIDES = {}


def _offh_is_he(shot):
	'''True for a high-explosive round. Reads shell['kind'] - never the name:
	every HEAT shell contains the letters 'HE' too, which is exactly the bug the
	shared penetration model was written to kill.'''
	shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
	return shell.get('kind') == 'HIGH_EXPLOSIVE'


def _offh_he_radius(shot):
	'''explosionRadius of this shot's shell, in metres. items/vehicles.py falls
	back to caliber^2 / 5555 when the shell XML omits it - mirror that rather
	than inventing a number.'''
	shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
	try:
		r = float(shell.get('explosionRadius', 0.0) or 0.0)
	except Exception:
		r = 0.0
	if r > 0.0:
		return r
	try:
		cal = float(shell.get('caliber', 0) or 0)
	except Exception:
		cal = 0.0
	return (cal * cal / 5555.0) if cal > 0.0 else 0.0


def _offh_he_nominal_armor(all_hits, td=None):
	'''Nominal thickness of the first STRUCTURAL plate on the ray.
	
	The HE reduction uses the plate's NOMINAL thickness, not the angled effective
	value: a sloped plate does not shrug off blast the way it deflects a solid
	shot. Spaced plates (vehicleDamageFactor 0 - tracks, external gear) are
	skipped; HE bursts on them and what has to hold is the hull behind.'''
	best = None
	for _h in (all_hits or []):
		try:
			_d, _mat = _h[0], _h[2]
		except Exception:
			continue
		if _mat is None or getattr(_mat, 'vehicleDamageFactor', 1.0) == 0.0:
			continue
		_a = float(getattr(_mat, 'armor', 0.0) or 0.0)
		if _a <= 0.0:
			continue
		if best is None or _d < best[0]:
			best = (_d, _a)
	if best is not None:
		return best[1]
	# An unavailable plate is not the descriptor's thinnest plate.
	return None


def _offh_he_factor(shell, name, default, maximum=None):
	value = shell.get(name, _OFFH_MISSING)
	if value is _OFFH_MISSING:
		value = default
	try:
		value = float(value)
	except (TypeError, ValueError, OverflowError):
		return float(default)
	if (value != value or abs(value) == float('inf') or value <= 0.0 or
			(maximum is not None and value > maximum)):
		return float(default)
	return value


def _offh_he_factors(shot):
	'''Return damage, absorption, and edge factors for one #1513 HE shell.'''
	shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
	damage_factor = _offh_he_factor(
		shell, 'explosionDamageFactor', _OFFH_HE_DAMAGE_FACTOR)
	absorption_factor = _offh_he_factor(
		shell, 'explosionDamageAbsorptionFactor',
		_OFFH_HE_DAMAGE_ABSORPTION_FACTOR)
	edge_factor = _offh_he_factor(
		shell, 'explosionEdgeDamageFactor', _OFFH_HE_EDGE_DAMAGE_FACTOR, 1.0)
	# Keep the existing local tuning hook as an explicit operator override.
	damage_factor = _OFFH_HE_TUNING_OVERRIDES.get(
		'splash_fraction', damage_factor)
	absorption_factor = _OFFH_HE_TUNING_OVERRIDES.get(
		'armor_factor', absorption_factor)
	return damage_factor, absorption_factor, edge_factor


def _offh_he_spall(spall_coefficient):
	'''Target Spall Liner multiplier on the armour absorption term.'''
	try:
		value = float(spall_coefficient)
	except (TypeError, ValueError, OverflowError):
		return 1.0
	if value != value or abs(value) == float('inf') or value < 1.0:
		return 1.0
	return value


def _offh_he_damage(shot, base_damage, armor_nominal, dist_frac=0.0,
                    spall_coefficient=1.0):
	'''Damage an HE burst does to a hull it did NOT get through.

	dist_frac is 0.0 for the vehicle actually struck and rises to 1.0 at the edge
	of explosionRadius for everything else caught in the blast. Returns 0 when the
	plate eats the whole thing - the normal outcome against heavy armour, and the
	reason a derp gun rewards shooting thin plate.'''
	damage_factor, absorption_factor, edge_factor = _offh_he_factors(shot)
	try:
		distance_fraction = float(dist_frac)
	except (TypeError, ValueError, OverflowError):
		distance_fraction = 0.0
	distance_fraction = max(0.0, min(1.0, distance_fraction))
	blast_factor = damage_factor + (
		edge_factor - damage_factor) * distance_fraction
	d = (float(base_damage) * blast_factor -
	     absorption_factor * float(armor_nominal or 0.0) *
	     _offh_he_spall(spall_coefficient))
	return int(d) if d > 0.0 else 0


def _offh_he_apply_tuning(overrides):
	'''Overlay config.json "he_tuning" onto descriptor-owned HE factors.'''
	_OFFH_HE_TUNING_OVERRIDES.clear()
	applied = []
	if isinstance(overrides, dict):
		for k in ('splash_fraction', 'armor_factor'):
			if k in overrides:
				try:
					value = float(overrides[k])
					if value != value or abs(value) == float('inf'):
						continue
					_OFFH_HE_TUNING_OVERRIDES[k] = value
					applied.append('%s=%s' % (k, overrides[k]))
				except (TypeError, ValueError, OverflowError):
					pass
	return applied


def _offh_penetration(shot, dist_m, armor, hit_angle_cos, pierce_loss=0.0,
		penetration_factor=None, material=None, allow_ricochet=True,
		base_penetration_multiplier=1.0):
	'''Armour test shared by the player and by bot-vs-bot fire.

	Returns (result, eff_armor, pierce): 0 ricochet, 1 no penetration, 2 penetration.

	Fixes two faults of the old inline version:
	  * it classified shells with `'HE' not in shell['name']`, a substring test on the
	    NAME. Every HEAT round contains 'HE', so both the ricochet and the
	    no-penetration branch were skipped for it and it always went through.
	    items/vehicles.py stores a proper shell['kind'] - use that.
	  * piercingPower is a Vector2 (P100, P500), not a maxDistance endpoint.
	Randomisation is WG's own g_cache.commonConfig piercingPowerRandomization = 0.25.
	'''
	import math
	shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
	kind = shell.get('kind', 'ARMOR_PIERCING')
	# Exact #1513 shellExtraData gives normalization/calibre ricochet checks only
	# to AP and APCR.  ARMOR_PIERCING_HE is a solid direct-damage shell, but it
	# has zero normalization and cannot ricochet.
	is_normalizing_ap = kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR')
	pierce = (_offh_range_piercing(shot, dist_m) *
		float(base_penetration_multiplier))
	if penetration_factor is None:
		penetration_factor = random.uniform(0.75, 1.25)
	pierce *= float(penetration_factor)
	# spaced armour already crossed (tracks, external devices) is subtracted here
	pierce -= float(pierce_loss or 0.0)
	if pierce < 0.0:
		pierce = 0.0
	armor = max(0.0, float(armor or 0.0))
	use_hit_angle = _offh_material_flag(material, 'useHitAngle', True)
	if use_hit_angle:
		_ac = float(hit_angle_cos)
	else:
		_ac = 1.0
	caliber = float(shell.get('caliber', 100) or 100)
	# AP normalizes by 5 degrees and APCR by 2.  Above two calibres the
	# normalization grows by WG's 1.4*C/(2*A) rule.  The three-calibre rule is
	# separate: strictly above three calibres suppresses ricochet, but does not
	# itself guarantee penetration.
	norm = (math.radians(2.0) if kind == 'ARMOR_PIERCING_CR' else
		(math.radians(5.0) if kind == 'ARMOR_PIERCING' else 0.0))
	if (use_hit_angle and is_normalizing_ap and
			_offh_material_flag(
				material, 'checkCaliberForHitAngleNorm', True) and
			caliber > armor * 2.0 and armor * 2.0 > 0.0):
		norm *= 1.4 * caliber / (armor * 2.0)
	shell_may_ricochet = (is_normalizing_ap or kind == 'HOLLOW_CHARGE')
	may_ricochet = (allow_ricochet and use_hit_angle and shell_may_ricochet and
		_offh_material_flag(material, 'mayRicochet', True))
	no_ap_ricochet = (is_normalizing_ap and
		_offh_material_flag(material, 'checkCaliberForRicochet', True) and
		caliber > armor * 3.0)
	if may_ricochet:
		if (is_normalizing_ap and not no_ap_ricochet and
				_ac <= math.cos(math.radians(70.0))):
			return (0, armor / max(_OFFH_MIN_HIT_ANGLE_COS, _ac), pierce)
		# HEAT does not use either calibre rule.  Its auto-ricochet threshold
		# is 85 degrees in the pinned client's shellExtraData.
		if (kind == 'HOLLOW_CHARGE' and
				_ac <= math.cos(math.radians(85.0))):
			return (0, armor / max(_OFFH_MIN_HIT_ANGLE_COS, _ac), pierce)
	# Mirror #1513's _computePenetrationArmor control flow.  In particular, the
	# native cosine is not made absolute or floored before normalization.
	_eff_ac = _ac
	if use_hit_angle and norm > 0.0 and _eff_ac < 1.0:
		ang_eff = math.acos(_eff_ac) - norm
		if ang_eff < 0.0:
			_eff_ac = 1.0
		else:
			maximum_angle = math.pi / 2.0 - _OFFH_MIN_HIT_ANGLE_COS
			if ang_eff > maximum_angle:
				ang_eff = maximum_angle
			_eff_ac = math.cos(ang_eff)
	if _eff_ac < _OFFH_MIN_HIT_ANGLE_COS:
		_eff_ac = _OFFH_MIN_HIT_ANGLE_COS
	eff = armor / _eff_ac
	penetrated = pierce > 0.0 and pierce >= eff
	if kind == 'HIGH_EXPLOSIVE':
		# HE penetrates or it does not, like everything else - it just gets no
		# normalisation and cannot ricochet (both already handled above). This used
		# to be an unconditional 2, so every HE round dealt FULL damage through any
		# thickness. A non-penetration here is not a miss: the caller runs
		# _offh_he_damage() for the blast.
		return (2 if penetrated else 1, eff, pierce)
	return (2 if penetrated else 1, eff, pierce)


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _legacy_shell(shell):
    if isinstance(shell, dict):
        return shell
    result = {}
    for name in ('kind', 'caliber', 'damage', 'explosionRadius',
                 'explosionDamageFactor',
                 'explosionDamageAbsorptionFactor',
                 'explosionEdgeDamageFactor', 'compactDescr', 'name'):
        value = getattr(shell, name, None)
        if value is None:
            value = getattr(getattr(shell, 'type', None), name, None)
        if value is not None:
            result[name] = value
    if 'kind' not in result:
        kind = getattr(getattr(shell, 'type', None), 'name', None)
        if kind is not None:
            result['kind'] = kind
    return result


def legacy_shot(shot):
    """Convert a #1513 GunShot object without changing the copied law."""
    if isinstance(shot, dict):
        return shot
    return {
        'shell': _legacy_shell(_field(shot, 'shell', {}) or {}),
        'piercingPower': _field(shot, 'piercingPower', (100.0, 100.0)),
        'maxDistance': _field(shot, 'maxDistance', 0.0),
    }


def collision_layers(collisions):
    result = []
    for collision in collisions or ():
        distance = getattr(collision, 'dist')
        angle = getattr(collision, 'hitAngleCos')
        material = getattr(collision, 'matInfo')
        component = getattr(collision, 'compName')
        try:
            result.append((float(distance), float(angle), material, component))
        except (TypeError, ValueError):
            raise TypeError('#1513 collision contains a non-numeric field')
    return sorted(result, key=lambda item: item[0])


def _offh_he_xyz(value):
	"""Read one finite three-coordinate point from a native vector or tuple."""
	try:
		result = tuple(float(value[index]) for index in range(3))
	except (AttributeError, IndexError, KeyError, TypeError, ValueError,
			OverflowError):
		return None
	if any(item != item or abs(item) == float('inf') for item in result):
		return None
	return result


def _offh_he_collision_values(collision):
	"""Extract distance and material while retaining the native collision."""
	try:
		distance = getattr(collision, 'dist')
		material = getattr(collision, 'matInfo')
	except (AttributeError, TypeError):
		try:
			distance = collision[0]
			material = collision[2]
		except (IndexError, KeyError, TypeError):
			return None
	try:
		distance = float(distance)
	except (TypeError, ValueError, OverflowError):
		return None
	if distance != distance or abs(distance) == float('inf'):
		return None
	return distance, material, collision


def _offh_he_material_is_structural(material):
	"""Only an explicit positive vehicle-damage factor is structure."""
	factor = _offh_material_value(
		material, 'vehicleDamageFactor', _OFFH_MISSING)
	if factor is _OFFH_MISSING:
		return False
	try:
		factor = float(factor)
	except (TypeError, ValueError, OverflowError):
		return False
	return factor == factor and abs(factor) != float('inf') and factor > 0.0


def he_blast_contact(shot, burst, start, end, collisions, rolled_damage,
					 spall_coefficient=1.0):
	"""Resolve one real structural HE contact from already-native ray evidence.

	``collisions`` remains native evidence: this routine only orders it, rebuilds
	the contact along ``start``/``end``, and applies the existing HE formula to
	the first structural material at or beyond the burst.  It never samples
	randomness and never substitutes a descriptor-wide armour fallback.
	"""
	converted = legacy_shot(shot)
	if not _offh_is_he(converted):
		return None
	burst = _offh_he_xyz(burst)
	start = _offh_he_xyz(start)
	end = _offh_he_xyz(end)
	radius = _offh_he_radius(converted)
	try:
		rolled_damage = float(rolled_damage)
	except (TypeError, ValueError, OverflowError):
		return None
	if (burst is None or start is None or end is None or radius <= 0.0 or
			rolled_damage != rolled_damage or abs(rolled_damage) == float('inf')):
		return None
	delta = tuple(end[index] - start[index] for index in range(3))
	length_squared = sum(item * item for item in delta)
	if length_squared <= 0.0:
		return None
	length = math.sqrt(length_squared)
	direction = tuple(item / length for item in delta)
	burst_along = sum((burst[index] - start[index]) * direction[index]
					for index in range(3))
	ordered = []
	for collision in collisions or ():
		values = _offh_he_collision_values(collision)
		if values is not None:
			ordered.append(values)
	ordered.sort(key=lambda item: item[0])
	through_contact = []
	for distance_along, material, collision in ordered:
		# The native distance must be on the queried segment; no extrapolated
		# evidence may create HE damage outside its blast ray.
		if distance_along < 0.0 or distance_along > length:
			continue
		if distance_along < burst_along - 1.0e-3:
			continue
		point = tuple(start[index] + direction[index] * distance_along
					  for index in range(3))
		distance = math.sqrt(sum(
			(point[index] - burst[index]) ** 2 for index in range(3)))
		structural = _offh_he_material_is_structural(material)
		if distance > radius:
			# A real structural plate in front of the query blocks a later one,
			# even when this ray reached it outside the blast sphere.
			if structural:
				return None
			continue
		through_contact.append(collision)
		if not structural:
			continue
		try:
			nominal_armor = float(
				_offh_material_value(material, 'armor', _OFFH_MISSING))
		except (TypeError, ValueError, OverflowError):
			return None
		if (nominal_armor != nominal_armor or
				abs(nominal_armor) == float('inf') or nominal_armor < 0.0):
			return None
		uses_liner = _offh_material_flag(
			material, 'useAntifragmentationLining', True)
		spall = spall_coefficient if uses_liner else 1.0
		return {
			'damage': _offh_he_damage(
				converted, rolled_damage, nominal_armor, distance / radius, spall),
			'nominal_armor': nominal_armor,
			'distance': distance,
			'point': point,
			'direction': direction,
			'collision': collision,
			'collisions': tuple(through_contact),
		}
	return None


def _call_with_uniform(function, uniform, *args):
    if uniform is None:
        return function(*args)
    original = random.uniform
    random.uniform = uniform
    try:
        return function(*args)
    finally:
        random.uniform = original


def penetration(shot, distance, armor, hit_angle_cos,
                pierce_loss=0.0, random_uniform=None,
                penetration_factor=None, material=None,
                base_penetration_multiplier=1.0):
    return _call_with_uniform(
        _offh_penetration, random_uniform, legacy_shot(shot),
        distance, armor, hit_angle_cos, pierce_loss, penetration_factor,
        material, True, base_penetration_multiplier)


def sample_penetration_factor(random_uniform=None):
    """Draw the one #1513 penetration random factor owned by a shell."""
    sampler = random.uniform if random_uniform is None else random_uniform
    return float(sampler(0.75, 1.25))


def range_piercing(shot, distance):
    """Return the non-randomized piercing mean at one travelled distance."""
    return _offh_range_piercing(legacy_shot(shot), distance)


def sampled_piercing(shot, distance, penetration_factor,
                     pierce_loss=0.0, base_penetration_multiplier=1.0):
    """Reuse one shell-owned factor at distance after external obstacles."""
    return max(0.0, range_piercing(shot, distance) *
               float(base_penetration_multiplier) *
               float(penetration_factor) - float(pierce_loss or 0.0))


def first_ricochet_penetration_multiplier(shell_kind):
    """Return the 9.3-0.9.22 retained penetration for a first ricochet."""
    if shell_kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR'):
        return 0.75
    if shell_kind == 'HOLLOW_CHARGE':
        return 1.0
    return None


def nominal_piercing_after_loss(shot, distance, pierce_loss=0.0):
    """Return the non-randomized #1513 range value after external obstacles."""
    return max(0.0, range_piercing(shot, distance) -
               float(pierce_loss or 0.0))


def resolve_armor_contact(shot, distance, collisions, random_uniform=None,
                          pierce_loss=0.0, penetration_factor=None,
                          base_penetration_multiplier=1.0):
    """Return the terminal external or structural armor contact."""
    return _call_with_uniform(
        _offh_resolve_armor_contact, random_uniform, legacy_shot(shot),
        distance, collision_layers(collisions), pierce_loss,
        penetration_factor, base_penetration_multiplier)


def resolve_hull_hit(shot, distance, collisions, random_uniform=None,
                     pierce_loss=0.0, penetration_factor=None,
                     base_penetration_multiplier=1.0):
    return _call_with_uniform(
        _offh_resolve_hull_hit, random_uniform, legacy_shot(shot),
        distance, collision_layers(collisions), pierce_loss,
        penetration_factor, base_penetration_multiplier)


def he_nominal_armor(collisions, descriptor=None):
    return _offh_he_nominal_armor(
        collision_layers(collisions), descriptor)


def damage(shot, result, nominal_armor, random_uniform=None,
           spall_coefficient=1.0):
    """Apply the legacy direct-damage formula to the resolved vehicle hit."""
    converted = legacy_shot(shot)
    shell = converted.get('shell') or {}
    raw = shell.get('damage')
    try:
        average = float(raw[0])
    except (TypeError, ValueError, IndexError):
        try:
            average = float(raw)
        except (TypeError, ValueError):
            return 0
    uniform = random_uniform or random.uniform
    rolled = int(uniform(average * 0.75, average * 1.25))
    if int(result) == 2:
        return rolled
    if _offh_is_he(converted):
        return _offh_he_damage(
            converted, rolled, nominal_armor, 0.0, spall_coefficient)
    return 0


def he_radius(shot):
    return _offh_he_radius(legacy_shot(shot))


def is_he(shot):
    return _offh_is_he(legacy_shot(shot))


def he_factors(shot):
    return _offh_he_factors(legacy_shot(shot))


def he_splash_damage(shot, nominal_armor, distance_fraction,
                     random_uniform=None, spall_coefficient=1.0):
    converted = legacy_shot(shot)
    shell = converted.get('shell') or {}
    raw = shell.get('damage')
    try:
        average = float(raw[0])
    except (TypeError, ValueError, IndexError):
        try:
            average = float(raw)
        except (TypeError, ValueError):
            return 0
    uniform = random_uniform or random.uniform
    rolled = uniform(average * 0.75, average * 1.25)
    return _offh_he_damage(
        converted, rolled, nominal_armor, distance_fraction,
        spall_coefficient)


def apply_he_tuning(overrides):
    return _offh_he_apply_tuning(overrides)
