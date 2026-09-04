import math


try:
	from gui.mods.offline_lan_0922 import internal_geometry as _internal_geometry
except Exception:
	try:
		import internal_geometry as _internal_geometry
	except Exception:
		_internal_geometry = None


try:
	from gui.mods.offline_lan_0922 import internal_layout_store as _layout_store
except Exception:
	try:
		import internal_layout_store as _layout_store
	except Exception:
		_layout_store = None

try:
	from gui.mods.offline_lan_0922 import internal_layout_profiles as _layout_profiles
except Exception:
	try:
		import internal_layout_profiles as _layout_profiles
	except Exception:
		_layout_profiles = None
if _layout_profiles is None:
	try:
		import os
		import sys
		import types
		_profile_module_name = 'gui.mods.offline_lan_0922.internal_layout_profiles'
		_layout_profiles = sys.modules.get(_profile_module_name)
		if _layout_profiles is None:
			_profile_path = os.path.join(os.path.dirname(
				os.path.abspath(__file__)), 'internal_layout_profiles.py')
			_layout_profiles = types.ModuleType(_profile_module_name)
			_layout_profiles.__file__ = _profile_path
			_layout_profiles.__package__ = 'gui.mods.offline_lan_0922'
			sys.modules[_profile_module_name] = _layout_profiles
			try:
				execfile(_profile_path, _layout_profiles.__dict__)
			except Exception:
				del sys.modules[_profile_module_name]
				raise
	except Exception:
		_layout_profiles = None


try:
	from gui.mods.offline_lan_0922.logging import LOG_EVENT, LOG_EXCEPTION, TRACE_CALL
except Exception:
	def LOG_EVENT(category, event, **fields):
		return None

	def LOG_EXCEPTION(category='python', event='exception', *args, **fields):
		return None

	def TRACE_CALL(category, name=None):
		def _decorate(function):
			return function
		return _decorate


LAYOUT_KEY = 14
_LAYOUT_MODE = 'profile'


_NEAR_FULL_TURRET_YAW_SPAN = math.radians(270.0)

MODULE_TARGETS = (
	'engine', 'ammoBay', 'gun', 'turretRotator', 'leftTrack', 'rightTrack',
	'surveyingDevice', 'radio', 'fuelTank')

# Tracks are scored from the native external collision extras when those are
# available.  A donated server descriptor deliberately carries component
# bounds, not the client's MaterialInfo table, so absence of the two track
# extras must not invalidate otherwise complete interior profile geometry.
# They remain explicitly unavailable rather than being replaced with guessed
# boxes.
OPTIONAL_NATIVE_GEOMETRY_TARGETS = frozenset(('leftTrack', 'rightTrack'))

_MODULE_EXTRA_NAMES = {
	'engine': 'engineHealth',
	'ammoBay': 'ammoBayHealth',
	'gun': 'gunHealth',
	'turretRotator': 'turretRotatorHealth',
	'leftTrack': 'leftTrackHealth',
	'rightTrack': 'rightTrackHealth',
	'surveyingDevice': 'surveyingDeviceHealth',
	'radio': 'radioHealth',
	'fuelTank': 'fuelTankHealth',
}

WOT_082_MODULE_DAMAGE_CHANCE = {
	'leftTrack': 1.00,
	'rightTrack': 1.00,
	'surveyingDevice': 0.45,
	'fuelTank': 0.45,
	'turretRotator': 0.45,
	'radio': 0.45,
	'engine': 0.45,
	'gun': 0.33,
	'ammoBay': 0.27,
}
WOT_082_CREW_PROJECTILE_DAMAGE_CHANCE = 0.33
WOT_082_CREW_EXPLOSION_DAMAGE_CHANCE = 0.10
WOT_082_SAVING_THROW_RULE = 'uniform_roll_0_to_1_success_if_roll_less_than_chance'

SUPPORTED_PARENTS = ('hull', 'turret', 'gun', 'chassis')

_LAYOUT_CACHE = {}
_LAYOUT_CACHE_LIMIT = 128
_RUNTIME_VERIFICATION = {}

_RUNTIME_REQUIRED_PHASES = ('hit', 'hp_state', 'effect', 'repair')
_RUNTIME_PLAYER_REQUIRED_PHASES = _RUNTIME_REQUIRED_PHASES + ('ui',)


def _value(value, key, default=None):
	if value is None:
		return default
	if isinstance(value, dict):
		return value.get(key, default)
	return getattr(value, key, default)


def _number(value, default=0.0):
	try:
		return float(value)
	except Exception:
		return float(default)


def _vector_tuple(value):
	try:
		return (float(value.x), float(value.y), float(value.z))
	except Exception:
		pass
	try:
		return (float(value[0]), float(value[1]), float(value[2]))
	except Exception:
		return None


def _bbox_for_component(vehicle_descriptor, parent_name):
	try:
		component = getattr(vehicle_descriptor, parent_name)
		hit_tester = _value(component, 'hitTester')
		bbox = getattr(hit_tester, 'bbox', None)
		if bbox is None:
			return None, 'missing_hit_tester_bbox'
		minimum = _vector_tuple(bbox[0])
		maximum = _vector_tuple(bbox[1])
		if minimum is None or maximum is None:
			return None, 'invalid_hit_tester_bbox'
		for axis in range(3):
			if maximum[axis] - minimum[axis] <= 0.0001:
				return None, 'degenerate_hit_tester_bbox'
		return (minimum, maximum), 'component.hitTester.bbox'
	except Exception:
		return None, 'component_bbox_access_failed'


def _component_identity(component):
	return '%s|%s|%s' % (
		str(_value(component, 'name', '')),
		str(_value(component, 'id', '')),
		str(_value(component, 'compactDescr', '')))


def vehicle_type_name(vehicle_descriptor):
	try:
		return str(vehicle_descriptor.type.name)
	except Exception:
		return ''


def configuration_fingerprint(vehicle_descriptor):
	parts = [vehicle_type_name(vehicle_descriptor)]
	for component_name in ('chassis', 'hull', 'turret', 'gun', 'engine',
			'fuelTank', 'radio'):
		try:
			parts.append(component_name + '=' + _component_identity(
				getattr(vehicle_descriptor, component_name)))
		except Exception:
			parts.append(component_name + '=missing')
	return ';'.join(parts)


def _normal_name(value):
	return ''.join(character.lower() for character in str(value)
		if character.isalnum())


def _profile_key(vehicle_name):
	parts = str(vehicle_name or '').split(':', 1)
	if len(parts) != 2:
		return None
	nation_aliases = {
		'russian': 'ussr',
		'german': 'germany',
		'american': 'usa',
		'french': 'france',
		'british': 'uk',
		'chinese': 'china',
	}
	nation = _normal_name(parts[0])
	nation = nation_aliases.get(nation, nation)
	return nation, _normal_name(parts[1])


def _compiled_profile(vehicle_name):
	if _layout_profiles is None:
		return None, None
	key = _profile_key(vehicle_name)
	if key is None:
		return None, None
	profile = _layout_profiles.PROFILES.get(key)
	if profile is not None:
		return key, profile
	# The retained geometry was authored for 0.8.2 names.  Resolve renamed
	# #1513 vehicles only through the reviewed full-name table: suffix matching
	# can attach an old profile to an unrelated vehicle which reused the name.
	alias = getattr(_layout_profiles, 'PROFILE_ALIASES_0922', {}).get(key)
	if alias is not None:
		profile = _layout_profiles.PROFILES.get(alias)
		if profile is not None:
			return alias, profile
	return key, None


def _profile_record(profile):
	if profile is None:
		return None
	return {
		'source_id': profile[0],
		'vehicle_class': profile[1],
		'tier': profile[2],
		'confidence': profile[3],
		'crew_roles': tuple(profile[4]),
		'module_zones': tuple(profile[5]),
		'crew_zones': tuple(profile[6]),
	}


def crew_entities(vehicle_descriptor):
	try:
		crew_roles = vehicle_descriptor.type.crewRoles
	except Exception:
		return (), ('missing_vehicle_type_crew_roles',)
	next_index = {'gunner': 1, 'loader': 1, 'radioman': 1}
	result = []
	errors = []
	for crew_index, roles in enumerate(crew_roles):
		try:
			main_role = str(roles[0])
		except Exception:
			errors.append('invalid_crew_role_%s' % crew_index)
			continue
		if main_role in next_index:
			entity_name = main_role + str(next_index[main_role])
			next_index[main_role] += 1
		else:
			entity_name = main_role
		result.append({
			'entity': entity_name,
			'roles': tuple(str(role) for role in roles),
			'crew_index': crew_index,
		})
	return tuple(result), tuple(errors)


def _official_geometry_bindings(vehicle_descriptor, crew):
	expected = dict((_MODULE_EXTRA_NAMES[entity], ('module', entity))
		for entity in MODULE_TARGETS)
	for role_data in crew:
		expected[_expected_extra_name(role_data['entity'], 'crew')] = (
			'crew', role_data['entity'])
	bindings = {}
	for parent_name in SUPPORTED_PARENTS:
		component = getattr(vehicle_descriptor, parent_name, None)
		for material_kind, material in _iter_materials(
				_value(component, 'materials', None)):
			extra_name = _material_extra_name(material)
			target = expected.get(extra_name)
			if target is None:
				continue
			kind, entity = target
			bindings.setdefault(entity, []).append({
				'entity': entity,
				'kind': kind,
				'parent': parent_name,
				'material_kind': str(material_kind),
				'extra': extra_name,
				'source': ('VehicleDescr.%s.materials+'
					'Vehicle.collideSegment') % parent_name,
				'geometry_classification': 'EXACT_OFFICIAL_RESOURCE_DATA',
			})
	return dict((entity, tuple(records))
		for entity, records in bindings.items())


def _fraction_point(bounds, fractions):
	minimum, maximum = bounds
	return tuple(minimum[index] + (maximum[index] - minimum[index]) *
		float(fractions[index]) for index in range(3))


def _fraction_half_extents(bounds, fractions, minimum_size=0.035):
	minimum, maximum = bounds
	return tuple(max(float(minimum_size),
		(maximum[index] - minimum[index]) * float(fractions[index]))
		for index in range(3))


def _clamp_volume(bounds, center, half_extents):
	minimum, maximum = bounds
	clamped_center = []
	clamped_half = []
	for axis in range(3):
		span = maximum[axis] - minimum[axis]
		half = min(max(0.01, half_extents[axis]), span * 0.45)
		low = minimum[axis] + half
		high = maximum[axis] - half
		value = min(high, max(low, center[axis]))
		clamped_center.append(value)
		clamped_half.append(half)
	return tuple(clamped_center), tuple(clamped_half)


def _validation_ray(center, half_extents):
	axis = 0
	if half_extents[1] < half_extents[axis]:
		axis = 1
	if half_extents[2] < half_extents[axis]:
		axis = 2
	start = list(center)
	end = list(center)
	end[axis] += half_extents[axis] * 0.5
	return tuple(start), tuple(end)


def _material_extra_name(material):
	try:
		return str(getattr(getattr(material, 'extra', None), 'name', '') or '')
	except Exception:
		return ''


def _iter_materials(materials):
	if materials is None:
		return ()
	try:
		return tuple(materials.iteritems())
	except Exception:
		pass
	try:
		return tuple(materials.items())
	except Exception:
		pass
	try:
		return tuple(enumerate(materials))
	except Exception:
		return ()


def _expected_extra_name(entity, kind):
	if kind == 'module':
		return _MODULE_EXTRA_NAMES.get(entity, '')
	return str(entity) + 'Health'


def _material_hit_chance(vehicle_descriptor, entity, kind,
		chance_attribute):


	expected_extra = _expected_extra_name(entity, kind)
	candidates = []
	for parent_name in SUPPORTED_PARENTS:
		component = getattr(vehicle_descriptor, parent_name, None)
		for material_kind, material in _iter_materials(
				_value(component, 'materials', None)):
			if _material_extra_name(material) != expected_extra:
				continue
			chance = _value(material, chance_attribute, None)
			if chance is None:
				continue
			candidates.append({
				'chance': max(0.0, min(1.0, _number(chance, 0.0))),
				'material_kind': str(material_kind),
				'extra': expected_extra,
				'source': ('VehicleDescr.%s.materials:%s' % (
					parent_name, chance_attribute)),
			})
	if not candidates:
		try:
			from items import vehicles as vehicles_module
			common_materials = vehicles_module.g_cache.commonConfig.get(
				'materials', {})
		except Exception:
			common_materials = {}
		for material_kind, material in _iter_materials(common_materials):
			if _material_extra_name(material) != expected_extra:
				continue
			chance = _value(material, chance_attribute, None)
			if chance is None:
				continue
			candidates.append({
				'chance': max(0.0, min(1.0, _number(chance, 0.0))),
				'material_kind': str(material_kind),
				'extra': expected_extra,
				'source': ('items.vehicles.g_cache.commonConfig.materials:%s' %
					chance_attribute),
			})
	if not candidates:
		return None


	selected = min(candidates, key=lambda item: (
		item['chance'], item['source'], item['material_kind']))
	return {
		'chance': selected['chance'],
		'source': selected['source'],
		'extra': expected_extra,
		'material_kind': selected['material_kind'],
		'candidates': tuple(candidates),
		'selection_rule': ('exact_extra_single_candidate' if len(candidates) == 1
			else 'exact_extra_conservative_minimum'),
	}


def _saving_throw_chance(vehicle_descriptor, entity, kind,
		damage_mode):
	mode = ('explosion' if str(damage_mode) == 'explosion' else 'projectile')
	if str(kind) == 'crew':
		chance = (WOT_082_CREW_EXPLOSION_DAMAGE_CHANCE
			if mode == 'explosion' else
			WOT_082_CREW_PROJECTILE_DAMAGE_CHANCE)
	else:
		chance = WOT_082_MODULE_DAMAGE_CHANCE.get(str(entity))
	if chance is None:
		attribute = ('chanceToHitByExplosion' if mode == 'explosion' else
			'chanceToHitByProjectile')
		return _material_hit_chance(vehicle_descriptor, entity, kind, attribute)

	attribute = ('chanceToHitByExplosion' if mode == 'explosion' else
		'chanceToHitByProjectile')
	descriptor = _material_hit_chance(
		vehicle_descriptor, entity, kind, attribute)
	result = {
		'chance': float(chance),
		'base_chance': float(chance),
		'base_chance_percent': float(chance) * 100.0,
		'source': 'table',
		'extra': _expected_extra_name(entity, kind),
		'material_kind': (descriptor or {}).get('material_kind', ''),
		'candidates': tuple((descriptor or {}).get('candidates', ())),
		'selection_rule': 'table',
		'saving_throw_rule': WOT_082_SAVING_THROW_RULE,
		'success_roll_min': 0.0,
		'success_roll_max_exclusive': float(chance),
	}
	if descriptor is not None:
		result['descriptor_chance'] = descriptor.get('chance')
		result['descriptor_source'] = descriptor.get('source')
	return result


def _projectile_hit_chance(vehicle_descriptor, entity, kind):
	return _saving_throw_chance(
		vehicle_descriptor, entity, kind, 'projectile')


def projectile_hit_chance(vehicle_descriptor, entity, kind):
	return _projectile_hit_chance(vehicle_descriptor, entity, kind)


def explosion_hit_chance(vehicle_descriptor, entity, kind):
	return _saving_throw_chance(
		vehicle_descriptor, entity, kind, 'explosion')


def _profile_shape_hint(profile_key, entity, kind, parent, zone_id,
		half_fractions=None):
	if _layout_profiles is None:
		return 'crew_capsule_head' if kind == 'crew' else 'box'
	method = getattr(_layout_profiles, 'zone_shape_hint', None)
	if not callable(method):
		return 'crew_capsule_head' if kind == 'crew' else 'box'
	try:
		return str(method(profile_key, entity, kind, parent, zone_id,
			half_fractions))
	except Exception:
		return 'crew_capsule_head' if kind == 'crew' else 'box'


def _target(vehicle_descriptor, entity, kind, parent, bounds,
		center_fractions, half_fractions, descriptor_source, hit_chance_info,
		role_data=None, zone_id='', profile_id='', profile_confidence='',
		shape_hint=None):
	if _internal_geometry is None:
		center = _fraction_point(bounds, center_fractions)
		half_extents = _fraction_half_extents(bounds, half_fractions)
		center, half_extents = _clamp_volume(bounds, center, half_extents)
		minimum = tuple(center[i] - half_extents[i] for i in range(3))
		maximum = tuple(center[i] + half_extents[i] for i in range(3))
		fit = {
			'center': center,
			'half_extents': half_extents,
			'minimum': minimum,
			'maximum': maximum,
			'shape': 'compound',
			'primitives': ({
				'shape': 'aabb', 'primitive_id': zone_id + ':fallback',
				'center': center, 'half_extents': half_extents,
				'minimum': minimum, 'maximum': maximum,
			},),
			'fit_mode': 'bbox_only_runtime_fallback',
			'fit_source': descriptor_source,
			'model_signature': None,
			'geometry_mode': 'BBOX_FALLBACK',
			'resistance': {
				'penetration_resistance_mm_per_meter': 20.0,
				'resistance_source': 'conservative_runtime_fallback',
			},
		}
	else:
		fit = _internal_geometry.fit_target(
			vehicle_descriptor, parent, entity, kind, bounds,
			center_fractions, half_fractions, zone_id, role_data,
			shape_hint)
	center = tuple(fit['center'])
	half_extents = tuple(fit['half_extents'])
	minimum = tuple(fit['minimum'])
	maximum = tuple(fit['maximum'])
	test_start, test_end = _validation_ray(center, half_extents)
	resistance = dict(fit.get('resistance', {}))
	result = {
		'entity': entity,
		'kind': kind,
		'parent': parent,
		'center': center,
		'half_extents': half_extents,
		'minimum': minimum,
		'maximum': maximum,
		'shape': fit.get('shape', 'compound'),
		'primitives': tuple(fit.get('primitives', ())),
		'local_transform': {'translation': center},
		'zone_id': zone_id,
		'profile_id': profile_id,
		'profile_confidence': profile_confidence,
		'seed_center_fractions': tuple(center_fractions),
		'seed_half_fractions': tuple(half_fractions),
		'calibration_status': 'profile_seed_unverified',
		'geometry_classification': 'profile',
		'descriptor_source': descriptor_source,
		'fit_mode': fit.get('fit_mode'),
		'fit_source': fit.get('fit_source'),
		'model_signature': fit.get('model_signature'),
		'geometry_mode': fit.get('geometry_mode'),
		'size_policy': fit.get('size_policy'),
		'profile_half_extents_m': fit.get('profile_half_extents_m'),
		'physical_half_cap_m': fit.get('physical_half_cap_m'),
		'final_half_extents_m': fit.get('final_half_extents_m'),
		'physical_cap_size_corrected': bool(fit.get(
			'physical_cap_size_corrected', False)),
		'collision_fit_size_corrected': bool(fit.get(
			'collision_fit_size_corrected', False)),
		'size_correction_applied': bool(fit.get(
			'size_correction_applied', False)),
		'size_correction_reasons': tuple(fit.get(
			'size_correction_reasons', ())),
		'fixed_fighting_compartment': bool(fit.get(
			'fixed_fighting_compartment', False)),
		'primitive_policy': fit.get('primitive_policy'),
		'shape_hint': fit.get('shape_hint', shape_hint or 'box'),
		'shape_source': fit.get('shape_source', 'profile'),
		'validation_ray': (test_start, test_end),
		'hit_chance': hit_chance_info['chance'],
		'hit_chance_source': hit_chance_info['source'],
		'hit_chance_extra': hit_chance_info['extra'],
		'hit_chance_material_kind': hit_chance_info['material_kind'],
		'hit_chance_candidates': hit_chance_info['candidates'],
		'hit_chance_selection_rule': hit_chance_info['selection_rule'],
		'hit_chance_rule': hit_chance_info.get(
			'saving_throw_rule', WOT_082_SAVING_THROW_RULE),
		'hit_chance_success_roll_min': hit_chance_info.get(
			'success_roll_min', 0.0),
		'hit_chance_success_roll_max_exclusive': hit_chance_info.get(
			'success_roll_max_exclusive', hit_chance_info['chance']),
		'hit_chance_application': 'table',
		'penetration_resistance_mm_per_meter': float(resistance.get(
			'penetration_resistance_mm_per_meter', 20.0)),
		'penetration_resistance_source': resistance.get(
			'resistance_source', 'unknown'),
		'geometry_mass_kg': resistance.get('weight_kg', 0.0),
		'geometry_mass_source': resistance.get('weight_source', 'unknown'),
		'geometry_volume_m3': resistance.get('volume_m3', 0.0),
		'geometry_effective_density_kg_m3': resistance.get(
			'effective_density_kg_m3', 0.0),
	}
	if role_data is not None:
		result['roles'] = tuple(role_data.get('roles', ()))
		result['crew_index'] = role_data.get('crew_index')
	return result

def _rotating_turret_architecture(vehicle_descriptor):
	try:
		tags = set(vehicle_descriptor.type.tags or ())
	except Exception:
		tags = set()
	try:
		yaw_limits = _value(vehicle_descriptor.turret, 'yawLimits')
	except Exception:
		yaw_limits = None
	if 'AT-SPG' not in tags and 'SPG' not in tags:
		return True, ('vehicle.tags+installed_turret.yawLimits:'
			'non_casemate_vehicle_class')
	if yaw_limits is None:
		return True, ('items.vehicles._readTurret:yawLimits=None:'
			'full_rotation')
	try:
		left = float(yaw_limits[0])
		right = float(yaw_limits[1])
	except Exception:
		try:
			left = float(yaw_limits.x)
			right = float(yaw_limits.y)
		except Exception:
			return False, ('installed_turret.yawLimits:unreadable:'
				'local_emulation_conservative_fixed')
	yaw_span = max(0.0, right - left)
	if yaw_span >= _NEAR_FULL_TURRET_YAW_SPAN:
		return True, ('installed_turret.yawLimits:span_deg=%.3f:'
			'local_emulation_near_full_rotation') % math.degrees(yaw_span)
	return False, ('installed_turret.yawLimits:span_deg=%.3f:'
		'limited_fighting_compartment') % math.degrees(yaw_span)


@TRACE_CALL('modules', 'build_internal_hit_layout')
def build_layout(vehicle_descriptor, log_build=True):
	vehicle_name = vehicle_type_name(vehicle_descriptor)
	fingerprint = configuration_fingerprint(vehicle_descriptor)
	cache_key = (vehicle_name, fingerprint, LAYOUT_KEY)
	if cache_key in _LAYOUT_CACHE:
		return _LAYOUT_CACHE[cache_key]

	bounds = {}
	bounds_sources = {}
	errors = []
	for parent_name in SUPPORTED_PARENTS:
		parent_bounds, source = _bbox_for_component(
			vehicle_descriptor, parent_name)
		if parent_bounds is not None:
			bounds[parent_name] = parent_bounds
			bounds_sources[parent_name] = source

	crew, crew_errors = crew_entities(vehicle_descriptor)
	errors.extend(crew_errors)
	rotating_turret, architecture_source = _rotating_turret_architecture(
		vehicle_descriptor)
	profile_key, compiled_profile = _compiled_profile(vehicle_name)
	profile = _profile_record(compiled_profile)
	if _layout_profiles is None:
		errors.append('compiled_profile_module_unavailable')
	elif (getattr(_layout_profiles, 'PROFILE_COUNT', 0) != 251 or
			len(getattr(_layout_profiles, 'PROFILES', {})) != 251):
		errors.append('compiled_profile_count_invalid')
	elif getattr(_layout_profiles, 'LAYOUT_PROFILE_KEY', 0) not in (1, 2, 3, 4):
		errors.append('compiled_profile_invalid')
	if profile is None:
		errors.append('compiled_vehicle_profile_missing:%s' % (
			str(profile_key or vehicle_name)))

	active_crew_roles = tuple(tuple(item.get('roles', ())) for item in crew)
	if profile is not None and profile['crew_roles'] != active_crew_roles:
		errors.append('crew_roles_profile_mismatch:expected=%s:actual=%s' % (
			profile['crew_roles'], active_crew_roles))

	official_geometry = _official_geometry_bindings(
		vehicle_descriptor, crew)
	candidate_specs = []
	if profile is not None:
		for module_zone in profile['module_zones']:
			entity, parent, zone_id, center_fractions, half_fractions = (
				module_zone)
			if entity in official_geometry:
				continue
			candidate_specs.append({
				'entity': entity,
				'kind': 'module',
				'parent': parent,
				'zone_id': zone_id,
				'center_fractions': center_fractions,
				'half_fractions': half_fractions,
				'role_data': None,
				'shape_hint': _profile_shape_hint(profile_key, entity,
					'module', parent, zone_id, half_fractions),
			})
		if 'gun' not in official_geometry and 'gun' in bounds:
			candidate_specs.append({
				'entity': 'gun',
				'kind': 'module',
				'parent': 'gun',
				'zone_id': 'single_installed_gun_model',
				'center_fractions': (0.5, 0.5, 0.5),
				'half_fractions': (0.40, 0.40, 0.40),
				'role_data': None,
				'shape_hint': _profile_shape_hint(profile_key, 'gun',
					'module', 'gun', 'single_installed_gun_model',
					(0.40, 0.40, 0.40)),
			})
		for crew_index, role_data in enumerate(crew):
			if crew_index >= len(profile['crew_zones']):
				errors.append('compiled_crew_zone_missing:%s' % crew_index)
				continue
			if role_data['entity'] in official_geometry:
				continue
			crew_zone = profile['crew_zones'][crew_index]
			parent, zone_id, center_fractions, half_fractions = crew_zone
			candidate_specs.append({
				'entity': role_data['entity'],
				'kind': 'crew',
				'parent': parent,
				'zone_id': zone_id,
				'center_fractions': center_fractions,
				'half_fractions': half_fractions,
				'role_data': role_data,
				'shape_hint': _profile_shape_hint(profile_key,
					role_data['entity'], 'crew', parent, zone_id,
					half_fractions),
			})

	candidate_entities = set(item['entity'] for item in candidate_specs)
	logical_entity_sources = {}
	expected_entities = list(MODULE_TARGETS)
	expected_entities.extend(item['entity'] for item in crew)
	for entity in expected_entities:
		if entity in official_geometry:
			logical_entity_sources[entity] = {
				'mode': 'EXACT_OFFICIAL_RESOURCE_DATA',
				'bindings': official_geometry[entity],
			}
		elif entity in candidate_entities:
			logical_entity_sources[entity] = {
				'mode': _LAYOUT_MODE,
				'profile_key': profile_key,
				'profile_source_id': (profile['source_id']
					if profile is not None else None),
			}
		else:
			if entity in OPTIONAL_NATIVE_GEOMETRY_TARGETS:
				logical_entity_sources[entity] = {
					'mode': 'OPTIONAL_NATIVE_COLLISION_GEOMETRY',
				}
			else:
				logical_entity_sources[entity] = {
					'mode': 'MISSING',
				}
				errors.append('geometry_source_missing:%s' % entity)

	required_parents = set(item['parent'] for item in candidate_specs)
	for parent_name in sorted(required_parents):
		if parent_name not in bounds:
			errors.append('%s:missing_hit_tester_bbox' % parent_name)
	hit_chances = {}
	for candidate in candidate_specs:
		entity_name = candidate['entity']
		kind = candidate['kind']
		if (kind, entity_name) in hit_chances:
			continue
		chance_info = _projectile_hit_chance(
			vehicle_descriptor, entity_name, kind)
		if chance_info is None:
			errors.append('projectile_hit_chance_missing:%s' % entity_name)
		else:
			hit_chances[(kind, entity_name)] = chance_info

	parent_transforms = {}
	for parent_name in sorted(required_parents):
		if parent_name not in bounds:
			continue
		try:
			component = getattr(vehicle_descriptor, parent_name)
			parent_transforms[parent_name] = {
				'parent': parent_name,
				'component_identity': _component_identity(component),
				'runtime_source': 'Vehicle.getComponents',
				'runtime_method': ('res/scripts/client/Vehicle.py:'
					'Vehicle.getComponents'),
				'current_transform_required': True,
			}
		except Exception:
			errors.append('parent_transform_binding_failed:' + parent_name)

	targets = []
	for candidate in candidate_specs:
		parent = candidate['parent']
		chance_info = hit_chances.get((
			candidate['kind'], candidate['entity']))
		if parent not in bounds or chance_info is None:
			continue
		target = _target(
			vehicle_descriptor, candidate['entity'], candidate['kind'], parent, bounds[parent],
			candidate['center_fractions'], candidate['half_fractions'],
			('internal_layout_profiles.PROFILES+%s+'
				'VehicleDescr.%s.hitTester.bbox') % (
					profile_key, parent),
			chance_info, candidate['role_data'], candidate['zone_id'],
			(profile['source_id'] if profile is not None else ''),
			(profile['confidence'] if profile is not None else ''),
			candidate.get('shape_hint'))
		if _layout_store is not None:
			try:
				_layout_store.apply_target_override(
					fingerprint, target, bounds[parent])
			except Exception:
				errors.append('calibration_override_failed:%s' %
					candidate['zone_id'])
		targets.append(target)
	if _layout_store is not None:
		try:
			targets.extend(_layout_store.append_custom_targets(
				fingerprint, targets, bounds))
		except Exception:
			errors.append('custom_calibration_zones_failed')
	if _internal_geometry is not None:
		try:
			_internal_geometry.normalize_entity_resistance(
				vehicle_descriptor, targets)
		except Exception:
			errors.append('entity_resistance_normalization_failed')

	layout = {
		'vehicle_type': vehicle_name,
		'configuration_fingerprint': fingerprint,
		'layout_key': LAYOUT_KEY,
		'layout_mode': _LAYOUT_MODE,
		'profile_key': profile_key,
		'profile_source_id': (profile['source_id']
			if profile is not None else None),
		'profile_confidence': (profile['confidence']
			if profile is not None else None),
		'profile_vehicle_class': (profile['vehicle_class']
			if profile is not None else None),
		'profile_tier': (profile['tier'] if profile is not None else None),
		'parents': bounds,
		'parent_transforms': parent_transforms,
		'required_parents': tuple(sorted(required_parents)),
		'vehicle_architecture': ('rotating_turret' if rotating_turret else
			'fixed_fighting_compartment'),
		'architecture_source': architecture_source,
		'runtime_value_owner': ('module_runtime.VehicleDescr+'
			'item_defs.current_installed_components'),
		'official_geometry': official_geometry,
		'official_geometry_entities': tuple(sorted(official_geometry.keys())),
		'logical_entity_sources': logical_entity_sources,
		'expected_module_entities': MODULE_TARGETS,
		'expected_crew_entities': tuple(item['entity'] for item in crew),
		'expected_runtime_entities': tuple(expected_entities),
		'targets': tuple(targets),
		'calibration_store': ('offhangar_user/internal_layout_overrides.json'
			if _layout_store is not None else None),
		'calibration_statuses': tuple(sorted(set(
			target.get('calibration_status', 'unknown') for target in targets))),
		'errors': tuple(errors),
		'valid': False,
	}
	validation = validate_layout(layout)
	layout['validation'] = validation
	layout['valid'] = bool(validation['complete'])
	if len(_LAYOUT_CACHE) >= _LAYOUT_CACHE_LIMIT:
		_LAYOUT_CACHE.clear()
	_LAYOUT_CACHE[cache_key] = layout
	if log_build:
		LOG_EVENT('modules', 'internal_layout_built',
			vehicle_type=vehicle_name, configuration=fingerprint,
			mode=_LAYOUT_MODE,
			vehicle_architecture=layout['vehicle_architecture'],
			profile_key=profile_key,
			profile_source_id=layout['profile_source_id'],
			profile_confidence=layout['profile_confidence'],
			required_parents=layout['required_parents'],
			validation_scope=validation['validation_scope'],
			original_geometry_found=layout['official_geometry_entities'],
			profile_zones=len(targets),
			projectile_probability_sources=sorted(set(target.get(
				'hit_chance_source', '') for target in targets)),
			projectile_probability_extras=sorted(set(target.get(
				'hit_chance_extra', '') for target in targets)),
			targets=len(targets), expected_targets=len(expected_entities),
			crew=len(crew), valid=bool(layout['valid']),
			missing=validation['missing'], errors=layout['errors'])
	return layout


def _segment_aabb(start, end, minimum, maximum):
	t_min = 0.0
	t_max = 1.0
	for axis in range(3):
		origin = float(start[axis])
		direction = float(end[axis]) - origin
		if abs(direction) <= 0.0000001:
			if origin < minimum[axis] or origin > maximum[axis]:
				return None
			continue
		inverse = 1.0 / direction
		t1 = (minimum[axis] - origin) * inverse
		t2 = (maximum[axis] - origin) * inverse
		if t1 > t2:
			t1, t2 = t2, t1
		t_min = max(t_min, t1)
		t_max = min(t_max, t2)
		if t_min > t_max:
			return None
	return t_min


def _segment_aabb_interval(start, end, minimum, maximum):
	t_min = 0.0
	t_max = 1.0
	for axis in range(3):
		origin = float(start[axis])
		direction = float(end[axis]) - origin
		if abs(direction) <= 0.0000001:
			if origin < minimum[axis] or origin > maximum[axis]:
				return None
			continue
		inverse = 1.0 / direction
		t1 = (minimum[axis] - origin) * inverse
		t2 = (maximum[axis] - origin) * inverse
		if t1 > t2:
			t1, t2 = t2, t1
		t_min = max(t_min, t1)
		t_max = min(t_max, t2)
		if t_min > t_max:
			return None
	return float(t_min), float(t_max)


def _target_intervals(start, end, target):
	if _internal_geometry is not None and target.get('primitives'):
		try:
			return _internal_geometry.target_intervals(start, end, target)
		except Exception:
			pass
	interval = _segment_aabb_interval(start, end, target['minimum'],
		target['maximum'])
	if interval is None:
		return ()
	return ((interval[0], interval[1], None),)


def _target_interval(start, end, target):
	intervals = _target_intervals(start, end, target)
	return intervals[0] if intervals else None


def _point_aabb_distance_squared(point, minimum, maximum):
	distance_squared = 0.0
	for axis in range(3):
		value = float(point[axis])
		if value < minimum[axis]:
			delta = minimum[axis] - value
		elif value > maximum[axis]:
			delta = value - maximum[axis]
		else:
			delta = 0.0
		distance_squared += delta * delta
	return distance_squared


def _segment_aabb_nearest(start, end, minimum, maximum):
	intersection = _segment_aabb(start, end, minimum, maximum)
	if intersection is not None:
		point = tuple(start[axis] + (end[axis] - start[axis]) * intersection
			for axis in range(3))
		return 0.0, float(intersection), point


	low = 0.0
	high = 1.0
	for unused_index in range(36):
		left = low + (high - low) / 3.0
		right = high - (high - low) / 3.0
		left_point = tuple(start[axis] + (end[axis] - start[axis]) * left
			for axis in range(3))
		right_point = tuple(start[axis] + (end[axis] - start[axis]) * right
			for axis in range(3))
		if (_point_aabb_distance_squared(left_point, minimum, maximum) <=
				_point_aabb_distance_squared(right_point, minimum, maximum)):
			high = right
		else:
			low = left
	t_value = (low + high) * 0.5
	point = tuple(start[axis] + (end[axis] - start[axis]) * t_value
		for axis in range(3))
	return (math.sqrt(_point_aabb_distance_squared(point, minimum, maximum)),
		float(t_value), point)


def diagnose_segment(layout, parent_segments, excluded_entities=None):
	excluded = set(excluded_entities or ())
	parents = []
	for parent_name in SUPPORTED_PARENTS:
		segment = parent_segments.get(parent_name)
		if segment is None:
			parents.append({'parent': parent_name, 'available': False})
		else:
			parents.append({
				'parent': parent_name,
				'available': True,
				'local_start': tuple(segment[0]),
				'local_end': tuple(segment[1]),
			})
	candidates = []
	for target in (layout or {}).get('targets', ()):
		segment = parent_segments.get(target['parent'])
		record = {
			'entity': target['entity'],
			'kind': target['kind'],
			'parent': target['parent'],
			'minimum': tuple(target['minimum']),
			'maximum': tuple(target['maximum']),
			'excluded': bool(target['entity'] in excluded),
			'parent_available': bool(segment is not None),
		}
		if segment is not None:
			interval = _target_interval(segment[0], segment[1], target)
			t_value = (None if interval is None else interval[0])
			distance, nearest_t, nearest_point = _segment_aabb_nearest(
				segment[0], segment[1], target['minimum'], target['maximum'])
			record.update({
				'intersects': bool(t_value is not None),
				'segment_t': (None if t_value is None else float(t_value)),
				'closest_distance': float(distance),
				'closest_segment_t': float(nearest_t),
				'closest_point': tuple(nearest_point),
			})
		else:
			record.update({
				'intersects': False,
				'segment_t': None,
				'closest_distance': None,
				'closest_segment_t': None,
				'closest_point': None,
			})
		candidates.append(record)
	return {'parents': tuple(parents), 'candidates': tuple(candidates)}


def _directional_validation_rays(parent_bounds, target):
	parent_minimum, parent_maximum = parent_bounds
	center = target['center']
	rays = []
	for axis in range(3):
		span = parent_maximum[axis] - parent_minimum[axis]
		margin = max(0.001, span * 0.001)
		for origin in (
				parent_minimum[axis] - margin,
				parent_maximum[axis] + margin):
			start = list(center)
			start[axis] = origin
			rays.append((tuple(start), tuple(center)))
	return tuple(rays)


def validate_layout(layout):
	targets = layout.get('targets', ())
	profile_entities = set(target.get('entity') for target in targets)
	expected_modules = tuple(layout.get(
		'expected_module_entities', MODULE_TARGETS))
	expected_crew = tuple(layout.get('expected_crew_entities', ()))
	expected_entities = expected_modules + expected_crew
	logical_sources = layout.get('logical_entity_sources', {})
	official_geometry = layout.get('official_geometry', {})
	missing = []
	for entity in expected_entities:
		source = logical_sources.get(entity, {})
		mode = source.get('mode')
		if mode == 'EXACT_OFFICIAL_RESOURCE_DATA':
			bindings = official_geometry.get(entity, ())
			if not bindings:
				missing.append('official_geometry_binding:' + entity)
				continue
			for binding in bindings:
				if (binding.get('parent') not in SUPPORTED_PARENTS or
						not binding.get('extra') or
						not binding.get('source')):
					missing.append('official_geometry_invalid:' + entity)
					break
		elif mode == _LAYOUT_MODE:
			if entity not in profile_entities:
				missing.append('geometry:' + entity)
		elif mode == 'OPTIONAL_NATIVE_COLLISION_GEOMETRY':
			# Honest server boundary: no native MaterialInfo was donated, and no
			# synthetic track box participates in the interior resolver.
			continue
		else:
			missing.append('geometry_source:' + entity)
	parent_transforms = layout.get('parent_transforms', {})
	required_parents = tuple(layout.get('required_parents', ()))
	for parent_name in required_parents:
		binding = parent_transforms.get(parent_name)
		if not binding:
			missing.append('parent_transform:' + parent_name)
		elif (binding.get('runtime_source') != 'Vehicle.getComponents' or
				not binding.get('current_transform_required', False) or
				not binding.get('component_identity')):
			missing.append('parent_transform_invalid:' + parent_name)
	validated = []
	validated_entities = set()
	for target in targets:
		parent = target.get('parent')
		if parent not in SUPPORTED_PARENTS or parent not in layout.get('parents', {}):
			missing.append('parent:%s:%s' % (target.get('entity'), parent))
			continue
		if (not target.get('hit_chance_source') or
				_number(target.get('hit_chance', -1.0), -1.0) < 0.0 or
				_number(target.get('hit_chance', 2.0), 2.0) > 1.0):
			missing.append('hit_chance:%s' % target.get('entity'))
			continue
		if not target.get('primitives'):
			missing.append('primitives:%s' % target.get('entity'))
			continue
		if (not target.get('model_signature') and
				target.get('fit_mode') != 'bbox_only_runtime_fallback'):
			missing.append('model_signature:%s' % target.get('entity'))
			continue
		resolved = False
		for ray in _directional_validation_rays(
				layout['parents'][parent], target):
			if _target_interval(ray[0], ray[1], target) is not None:
				resolved = True
				break
		if not resolved:
			missing.append('ray_misses_target:%s:%s' % (
				target.get('entity'), target.get('zone_id')))
			continue
		validated.append(target.get('entity'))
		validated_entities.add(target.get('entity'))
	for entity in profile_entities:
		if entity not in validated_entities:
			missing.append('unvalidated_entity:' + entity)
	unique_missing = []
	for item in missing:
		if item not in unique_missing:
			unique_missing.append(item)
	return {
		'complete': (not bool(layout.get('errors')) and
			not bool(unique_missing)),
		'coverage_kind': 'hybrid',
		'validation_scope': ('per_model_fitted_compound_primitives_direct_'
			'intersection+official_collision_binding'),
		'physical_wg_geometry_claimed': bool(official_geometry),
		'missing': tuple(unique_missing),
		'validated_targets': tuple(validated),
		'module_count': len(expected_modules),
		'expected_target_count': len(expected_entities),
		'zone_count': len(targets),
		'entity_count': len(profile_entities),
		'official_geometry_entity_count': len(official_geometry),
		'official_geometry_entities': tuple(sorted(official_geometry.keys())),
		'runtime_value_owner': layout.get('runtime_value_owner'),
		'crew_count': len(expected_crew),
		'parent_count': len(layout.get('parents', {})),
		'parent_transform_count': len(parent_transforms),
		'required_parent_count': len(required_parents),
		'expected_crew_count': len(expected_crew),
	}


def _installed_component_choices(vehicle_type):
	try:
		turrets = vehicle_type.turrets[0]
	except Exception:
		turrets = ()
	return {
		'chassis': tuple(getattr(vehicle_type, 'chassis', ()) or ()),
		'engines': tuple(getattr(vehicle_type, 'engines', ()) or ()),
		'fuel_tanks': tuple(getattr(vehicle_type, 'fuelTanks', ()) or ()),
		'radios': tuple(getattr(vehicle_type, 'radios', ()) or ()),
		'turrets': tuple(turrets or ()),
	}


def _component_compact_descriptor(component):
	return _value(component, 'compactDescr', 0) or 0


def _configuration_compatible(vehicle_descriptor, selections):
	checks = ()
	try:
		checks = (
			vehicle_descriptor.mayInstallComponent(
				_component_compact_descriptor(selections['chassis'])),
			vehicle_descriptor.mayInstallComponent(
				_component_compact_descriptor(selections['engine'])),
			vehicle_descriptor.mayInstallComponent(
				_component_compact_descriptor(selections['fuel_tank'])),
			vehicle_descriptor.mayInstallComponent(
				_component_compact_descriptor(selections['radio'])),
			vehicle_descriptor.mayInstallTurret(
				_component_compact_descriptor(selections['turret']),
				_component_compact_descriptor(selections['gun']), 0),
		)
	except Exception as error:
		return False, 'compatibility_api_exception:%s' % error
	for accepted, reason in checks:
		if not accepted:
			return False, 'client_rejected:%s' % reason
	return True, 'VehicleDescr.mayInstallComponent/mayInstallTurret'


def _assemble_configuration(type_id, selections, require_complete=True):
	from items import vehicles
	descriptor = vehicles.VehicleDescr(typeID=type_id)
	for selection_name in ('chassis', 'engine', 'fuel_tank', 'radio'):
		component = selections.get(selection_name)
		if component is not None:
			descriptor.installComponent(
				_component_compact_descriptor(component))
	if selections.get('turret') is not None and selections.get('gun') is not None:
		descriptor.installTurret(
			_component_compact_descriptor(selections['turret']),
			_component_compact_descriptor(selections['gun']), 0)
	elif require_complete:
		raise ValueError('incomplete_turret_gun_selection')
	return descriptor


def _selection_options(choices, selection_name):
	if selection_name == 'chassis':
		return choices['chassis']
	if selection_name == 'engine':
		return choices['engines']
	if selection_name == 'fuel_tank':
		return choices['fuel_tanks']
	if selection_name == 'radio':
		return choices['radios']
	return ()


def _iter_compatible_selections(type_id, choices):
	selection_order = ('chassis', 'engine', 'fuel_tank', 'radio')

	def _walk(selection_index, selections):
		if selection_index >= len(selection_order):
			try:
				partial_descriptor = _assemble_configuration(
					type_id, selections, require_complete=False)
			except Exception:
				return
			for turret in choices['turrets']:
				for gun in tuple(_value(turret, 'guns', ()) or ()):
					try:
						accepted, unused_reason = partial_descriptor.mayInstallTurret(
							_component_compact_descriptor(turret),
							_component_compact_descriptor(gun), 0)
					except Exception:
						continue
					if not accepted:
						continue
					complete = dict(selections)
					complete['turret'] = turret
					complete['gun'] = gun
					yield complete
			return
		selection_name = selection_order[selection_index]
		try:
			partial_descriptor = _assemble_configuration(
				type_id, selections, require_complete=False)
		except Exception:
			return
		for component in _selection_options(choices, selection_name):
			try:
				accepted, unused_reason = partial_descriptor.mayInstallComponent(
					_component_compact_descriptor(component))
			except Exception:
				continue
			if not accepted:
				continue
			next_selections = dict(selections)
			next_selections[selection_name] = component
			for result in _walk(selection_index + 1, next_selections):
				yield result

	for result in _walk(0, {}):
		yield result


def iter_vehicle_configurations():
	from items import vehicles
	try:
		import nations
		nation_count = len(nations.NAMES)
	except Exception:
		nation_count = 5
	for nation_id in xrange(nation_count):
		vehicle_rows = vehicles.g_list.getList(nation_id)
		for vehicle_id in sorted(vehicle_rows.keys()):
			type_id = (nation_id, vehicle_id)
			try:
				base = vehicles.VehicleDescr(typeID=type_id)
				vehicle_name = vehicle_type_name(base)
				choices = _installed_component_choices(base.type)
			except Exception as error:
				yield {
					'record_kind': 'inventory_error',
					'vehicle_type': '%s:%s' % type_id,
					'descriptor': None,
					'error': 'vehicle_descriptor_failed:%s' % error,
				}
				continue


			vehicle_root = (_profile_key(vehicle_name) or ('', ''))[1]
			if vehicle_root in ('ch01type59gold', 't23', 'observer'):
				yield {
					'record_kind': 'vehicle_type_excluded',
					'vehicle_type': vehicle_name,
					'type_id': type_id,
					'reason': 'excluded_from_supplied_251_profile_database',
					'source': 'internal_layout_profiles.EXCLUDED',
				}
				continue
			yield {
				'record_kind': 'vehicle_type',
				'vehicle_type': vehicle_name,
				'type_id': type_id,
			}
			if not all(choices.values()):
				yield {
					'record_kind': 'inventory_error',
					'vehicle_type': vehicle_name,
					'descriptor': None,
					'error': 'missing_installable_component_group',
				}
				continue
			seen = set()
			accepted_count = 0
			for selections in _iter_compatible_selections(type_id, choices):
				try:
					descriptor = _assemble_configuration(type_id, selections)
					fingerprint = configuration_fingerprint(descriptor)
					if fingerprint in seen:
						continue
					seen.add(fingerprint)
					compatible, source = _configuration_compatible(
						descriptor, selections)
					if not compatible:
						continue
					accepted_count += 1
					yield {
						'record_kind': 'configuration',
						'vehicle_type': vehicle_name,
						'descriptor': descriptor,
						'configuration': fingerprint,
						'compatibility_source': source,
						'candidate_source': (
							'VehicleDescr compatibility graph traversal+'
							'concrete turret.guns'),
					}
				except Exception:
					continue
			if accepted_count <= 0:
				yield {
					'record_kind': 'inventory_error',
					'vehicle_type': vehicle_name,
					'descriptor': None,
					'error': 'no_client_compatible_configuration',
				}


def new_coverage_state():
	return {
		'by_type': {}, 'configuration_total': 0,
		'configuration_covered': 0, 'missing_configurations': [],
		'configuration_index': [], 'inventory_errors': [],
		'excluded_vehicle_types': [],
	}


def register_vehicle_type(state, vehicle_type):
	if not vehicle_type:
		vehicle_type = 'unknown'
	return state['by_type'].setdefault(vehicle_type,
		{'total': 0, 'covered': 0, 'inventory_errors': 0})


def add_inventory_error(state, vehicle_type, error):
	entry = register_vehicle_type(state, vehicle_type)
	entry['inventory_errors'] += 1
	state['inventory_errors'].append({
		'vehicle_type': vehicle_type or 'unknown',
		'error': error or 'inventory_error',
	})


def add_vehicle_type_exclusion(state, vehicle_type, reason, source):
	state['excluded_vehicle_types'].append({
		'vehicle_type': vehicle_type or 'unknown',
		'reason': reason or 'excluded',
		'source': source or 'unknown',
	})


def add_coverage_result(state, layout=None, vehicle_type='', error=None,
		configuration='', compatibility_source='', candidate_source=''):

	state['configuration_total'] += 1
	vehicle_name = (layout.get('vehicle_type', '') if layout else vehicle_type)
	entry = register_vehicle_type(state, vehicle_name)
	entry['total'] += 1
	fingerprint = (layout.get('configuration_fingerprint', '')
		if layout else configuration)
	validation = (layout.get('validation', {}) if layout else {})
	targets = (tuple(layout.get('targets', ())) if layout else ())
	expected_runtime = (tuple(layout.get('expected_runtime_entities', ()))
		if layout else ())
	state['configuration_index'].append({
		'vehicle_type': vehicle_name,
		'configuration': fingerprint,
		'structurally_valid': bool(layout is not None and layout.get('valid')),
		'layout_mode': (layout.get('layout_mode') if layout else None),
		'profile_key': (layout.get('profile_key') if layout else None),
		'profile_source_id': (layout.get('profile_source_id')
			if layout else None),
		'profile_confidence': (layout.get('profile_confidence')
			if layout else None),
		'physical_wg_geometry_claimed': bool(
			layout and layout.get('official_geometry')),
		'original_geometry_found': (tuple(layout.get(
			'official_geometry_entities', ())) if layout else ()),
		'zone_count': len(targets),
		'client_compatibility_source': compatibility_source,
		'candidate_source': candidate_source,
		'architecture': (layout.get('vehicle_architecture') if layout else None),
		'required_parents': (tuple(layout.get('required_parents', ()))
			if layout else ()),
		'parent_transform_count': (len(layout.get('parent_transforms', {}))
			if layout else 0),
		'expected_modules': (tuple(layout.get(
			'expected_module_entities', ())) if layout else ()),
		'expected_crew': (tuple(layout.get(
			'expected_crew_entities', ())) if layout else ()),
		'expected_targets': expected_runtime,
		'logical_entity_sources': (layout.get('logical_entity_sources', {})
			if layout else {}),
		'validation_ray_count': len(tuple(validation.get(
			'validated_targets', ()))),
		'validated_targets': tuple(validation.get('validated_targets', ())),
		'validation_scope': validation.get('validation_scope'),
	})
	if layout is not None and layout.get('valid'):
		state['configuration_covered'] += 1
		entry['covered'] += 1
	else:
		state['missing_configurations'].append({
			'vehicle_type': vehicle_name,
			'configuration': fingerprint,
			'missing': (layout.get('validation', {}).get('missing', ())
				if layout else (error or 'configuration_unavailable',)),
			'errors': (layout.get('errors', ()) if layout else (error,)),
		})


def finalize_coverage_state(state):
	type_total = len(state['by_type'])
	type_covered = len([name for name, value in state['by_type'].items()
		if (value['total'] > 0 and value['covered'] == value['total'] and
			value.get('inventory_errors', 0) == 0)])
	configuration_index = tuple(state.get('configuration_index', ()))
	report = {
		'coverage_kind': 'hybrid',
		'layout_mode': _LAYOUT_MODE,
		'geometry_classification': (
			'exact_official_collision_where_present_plus_'
			'profile'),
		'physical_wg_geometry_claimed': bool(any(record.get(
			'physical_wg_geometry_claimed') for record in configuration_index)),
		'historical_wg_geometry_verified': False,
		'configuration_compatibility_mode': (
			'client_descriptor_graph_runtime_api_validated'),
		'vehicle_types': {'covered': type_covered, 'total': type_total,
			'coverage_kind': 'hybrid'},
		'vehicle_configurations': {
			'covered': state['configuration_covered'],
			'total': state['configuration_total'],
			'coverage_kind': 'hybrid',
		},
		'missing_configurations': tuple(state['missing_configurations']),
		'inventory_errors': tuple(state.get('inventory_errors', ())),
		'excluded_vehicle_types': tuple(state.get(
			'excluded_vehicle_types', ())),
		'by_type': state['by_type'],
		'configuration_index': configuration_index,
	}
	report['runtime_verified'] = runtime_verification_report(
		configuration_index, state['by_type'])
	return report


def write_coverage_report(report, path):
	import json
	import os
	absolute_path = os.path.abspath(path)
	temporary_path = absolute_path + '.tmp'
	handle = None
	try:
		handle = open(temporary_path, 'wb')
		payload = json.dumps(report, sort_keys=True, indent=2)
		handle.write(payload.encode('utf-8'))
		handle.close()
		handle = None
		if os.path.exists(absolute_path):
			os.remove(absolute_path)
		os.rename(temporary_path, absolute_path)
		return absolute_path
	finally:
		if handle is not None:
			try:
				handle.close()
			except Exception:
				pass


def record_runtime_evidence(layout, owner, entity_name, phases, source):
	if not layout or not layout.get('valid'):
		return False
	owner = 'player' if owner == 'player' else 'bot'
	fingerprint = layout.get('configuration_fingerprint', '')
	vehicle_name = layout.get('vehicle_type', '')
	if not fingerprint or not vehicle_name:
		return False
	expected = tuple(layout.get('expected_runtime_entities', ()))
	entry = _RUNTIME_VERIFICATION.setdefault(fingerprint, {
		'vehicle_type': vehicle_name,
		'expected_targets': expected,
		'owners': {'player': {}, 'bot': {}},
	})
	target = entry['owners'][owner].setdefault(entity_name, {
		'phases': set(), 'sources': set(),
	})
	changed = False
	for phase in tuple(phases or ()):
		if phase not in target['phases']:
			target['phases'].add(phase)
			changed = True
	if source and source not in target['sources']:
		target['sources'].add(str(source))
		changed = True
	return changed


def _runtime_configuration_complete(entry):
	expected = tuple(entry.get('expected_targets', ()))
	if not expected:
		return False
	for owner in ('player', 'bot'):
		required = (_RUNTIME_PLAYER_REQUIRED_PHASES if owner == 'player' else
			_RUNTIME_REQUIRED_PHASES)
		owner_targets = entry.get('owners', {}).get(owner, {})
		for entity_name in expected:
			target = owner_targets.get(entity_name)
			if target is None or not set(required).issubset(target.get('phases', set())):
				return False
	return True


def runtime_verification_report(configuration_index, vehicle_type_index=None,
		missing_preview_limit=100):

	index = tuple(configuration_index or ())
	indexed_fingerprints = set(record.get('configuration') for record in index
		if record.get('configuration'))
	verified_configurations = set()
	for fingerprint, entry in _RUNTIME_VERIFICATION.items():
		if (fingerprint in indexed_fingerprints and
				_runtime_configuration_complete(entry)):
			verified_configurations.add(fingerprint)
	by_type = dict((name, {'total': 0, 'verified': 0})
		for name in (vehicle_type_index or {}).keys())
	for record in index:
		vehicle_name = record.get('vehicle_type', '')
		entry = by_type.setdefault(vehicle_name, {'total': 0, 'verified': 0})
		entry['total'] += 1
		if record.get('configuration') in verified_configurations:
			entry['verified'] += 1
	verified_types = len([name for name, value in by_type.items()
		if (value['total'] > 0 and value['verified'] == value['total'] and
			(vehicle_type_index or {}).get(name, {}).get(
				'inventory_errors', 0) == 0)])
	missing = []
	observed_configurations = []
	missing_count = 0
	for record in index:
		fingerprint = record.get('configuration', '')
		verification = _RUNTIME_VERIFICATION.get(fingerprint, {})
		observed_owners = {}
		for owner in ('player', 'bot'):
			owner_records = verification.get('owners', {}).get(owner, {})
			serialized_targets = {}
			for entity_name, target_record in owner_records.items():
				serialized_targets[entity_name] = {
					'phases': tuple(sorted(target_record.get('phases', set()))),
					'sources': tuple(sorted(target_record.get('sources', set()))),
				}
			if serialized_targets:
				observed_owners[owner] = serialized_targets
		if observed_owners:
			observed_configurations.append({
				'vehicle_type': record.get('vehicle_type', ''),
				'configuration': fingerprint,
				'owners': observed_owners,
			})
		missing_targets = []
		for owner in ('player', 'bot'):
			required = (_RUNTIME_PLAYER_REQUIRED_PHASES if owner == 'player' else
				_RUNTIME_REQUIRED_PHASES)
			observed = verification.get('owners', {}).get(owner, {})
			for entity_name in tuple(record.get('expected_targets', ())):
				phases = observed.get(entity_name, {}).get('phases', set())
				missing_phases = tuple(phase for phase in required
					if phase not in phases)
				if missing_phases:
					missing_targets.append({
						'owner': owner, 'entity': entity_name,
						'missing_phases': missing_phases,
					})
		if missing_targets:
			missing_count += 1
			if len(missing) < max(0, int(missing_preview_limit)):
				missing.append({
					'vehicle_type': record.get('vehicle_type', ''),
					'configuration': fingerprint,
					'missing_targets': tuple(missing_targets),
				})
	return {
		'coverage_kind': 'runtime',
		'layout_mode': _LAYOUT_MODE,
		'geometry_classification': (
			'exact_official_collision_where_present_plus_'
			'profile'),
		'physical_wg_geometry_claimed': bool(any(record.get(
			'physical_wg_geometry_claimed') for record in index)),
		'historical_wg_geometry_verified': False,
		'vehicle_types': {'covered': verified_types, 'total': len(by_type)},
		'vehicle_configurations': {
			'covered': len(verified_configurations), 'total': len(index)},
		'missing_configuration_count': missing_count,
		'missing_configurations_preview': tuple(missing),
		'missing_preview_limit': max(0, int(missing_preview_limit)),
		'observed_configurations': tuple(observed_configurations),
		'required_player_phases': _RUNTIME_PLAYER_REQUIRED_PHASES,
		'required_bot_phases': _RUNTIME_REQUIRED_PHASES,
		'note': ('Runtime data required.'),
	}


@TRACE_CALL('modules', 'resolve_internal_segments')
def resolve_segments(layout, parent_segments, remaining_penetration=None,
		excluded_entities=None):
	if not layout or not layout.get('valid'):
		return ()
	excluded = set(excluded_entities or ())
	candidates = []
	for target in layout.get('targets', ()):
		if target['entity'] in excluded:
			continue
		segment = parent_segments.get(target['parent'])
		if not segment:
			continue
		segment_length = math.sqrt(sum(
			(float(segment[1][axis]) - float(segment[0][axis])) ** 2
			for axis in range(3)))
		for interval_index, interval in enumerate(_target_intervals(
				segment[0], segment[1], target)):
			entry_t, exit_t, primitive = interval
			path_length = max(0.0, (float(exit_t) - float(entry_t)) *
				segment_length)
			record = dict(target)
			record['segment_t'] = float(entry_t)
			record['segment_exit_t'] = float(exit_t)
			record['path_length_m'] = path_length
			record['primitive_id'] = (primitive.get('primitive_id')
				if primitive else None)
			record['physical_interval_index'] = int(interval_index)
			record['hit_point'] = tuple(segment[0][axis] +
				(segment[1][axis] - segment[0][axis]) * entry_t
				for axis in range(3))
			candidates.append(record)
	candidates.sort(key=lambda item: (
		item['segment_t'], item.get('parent', ''), item.get('zone_id', ''),
		item.get('physical_interval_index', 0)))
	budget = (None if remaining_penetration is None else
		max(0.0, float(remaining_penetration)))
	result = []
	damaged_entities = set()
	for candidate in candidates:
		entity = candidate['entity']
		candidate['damage_eligible'] = bool(entity not in damaged_entities)
		if candidate['damage_eligible']:
			damaged_entities.add(entity)
		candidate['remaining_penetration_before'] = budget
		candidate['penetration_resistance_mm'] = 0.0
		candidate['penetration_resistance_source'] = (
			'wot_0.8.2_internal_modules_do_not_consume_penetration')
		candidate['remaining_penetration_after'] = budget
		candidate['projectile_stopped'] = False
		result.append(candidate)
	return tuple(result)


def _closest_point_aabb(point, minimum, maximum):
	return tuple(max(float(minimum[axis]), min(float(maximum[axis]),
		float(point[axis]))) for axis in range(3))


def _vector_length(vector):
	return math.sqrt(sum(float(value) * float(value) for value in vector))


def _normalised(vector):
	length = _vector_length(vector)
	if length <= 0.000001:
		return (0.0, 0.0, 1.0)
	return tuple(float(value) / length for value in vector)


def _rotate_point_about_y(point, center, degrees):
	angle = math.radians(float(degrees or 0.0))
	if abs(angle) <= 0.0000001:
		return tuple(float(value) for value in point)
	dx = float(point[0]) - float(center[0])
	dz = float(point[2]) - float(center[2])
	cosine = math.cos(angle)
	sine = math.sin(angle)
	return (float(center[0]) + dx * cosine - dz * sine,
		float(point[1]), float(center[2]) + dx * sine + dz * cosine)


def _ellipsoid_point_distance(point, center, radii, yaw_degrees):
	local_world = _rotate_point_about_y(point, center, -yaw_degrees)
	local = tuple(float(local_world[axis]) - float(center[axis])
		for axis in range(3))
	radii = tuple(max(0.0001, float(value)) for value in radii)
	normalised = sum((local[axis] / radii[axis]) ** 2
		for axis in range(3))
	if normalised <= 1.0:
		return 0.0, tuple(point)
	absolute = tuple(abs(value) for value in local)
	def equation(value):
		return sum(((radii[axis] * absolute[axis]) /
			(value + radii[axis] * radii[axis])) ** 2
			for axis in range(3)) - 1.0
	low = 0.0
	high = max(radii) * max(_vector_length(absolute), max(radii))
	while equation(high) > 0.0:
		high *= 2.0
		if high > 1000000.0:
			break
	for unused_index in range(36):
		midpoint = (low + high) * 0.5
		if equation(midpoint) > 0.0:
			low = midpoint
		else:
			high = midpoint
	lam = (low + high) * 0.5
	closest_local = []
	for axis in range(3):
		radius_sq = radii[axis] * radii[axis]
		value = radius_sq * absolute[axis] / (lam + radius_sq)
		closest_local.append(value if local[axis] >= 0.0 else -value)
	closest_unrotated = tuple(float(center[axis]) + closest_local[axis]
		for axis in range(3))
	closest = _rotate_point_about_y(closest_unrotated, center, yaw_degrees)
	delta = tuple(float(point[axis]) - float(closest[axis])
		for axis in range(3))
	return _vector_length(delta), closest


def _capsule_point_distance(point, center, radius, half_length, axis,
		yaw_degrees):
	local_world = _rotate_point_about_y(point, center, -yaw_degrees)
	local = tuple(float(local_world[index]) - float(center[index])
		for index in range(3))
	axis = str(axis or 'y').lower()
	axis_index = {'x': 0, 'y': 1, 'z': 2}.get(axis, 1)
	axis_value = max(-float(half_length), min(float(half_length),
		local[axis_index]))
	axis_point = [0.0, 0.0, 0.0]
	axis_point[axis_index] = axis_value
	delta = tuple(local[index] - axis_point[index] for index in range(3))
	length = _vector_length(delta)
	radius = max(0.0001, float(radius))
	if length <= radius:
		return 0.0, tuple(point)
	factor = radius / length
	closest_local = tuple(axis_point[index] + delta[index] * factor
		for index in range(3))
	closest_unrotated = tuple(float(center[index]) + closest_local[index]
		for index in range(3))
	closest = _rotate_point_about_y(closest_unrotated, center, yaw_degrees)
	return length - radius, closest


def _primitive_distance_record(point, target, primitive):
	shape = str(primitive.get('shape', 'aabb') or 'aabb').lower()
	center = tuple(float(value) for value in primitive.get(
		'center', target.get('center', (0.0, 0.0, 0.0))))
	if shape == 'sphere':
		radius = max(0.0, float(primitive.get('radius', 0.0) or 0.0))
		delta = tuple(float(point[axis]) - center[axis] for axis in range(3))
		distance_to_center = _vector_length(delta)
		if distance_to_center <= radius or distance_to_center <= 0.000001:
			return 0.0, tuple(point)
		scale = radius / distance_to_center
		closest = tuple(center[axis] + delta[axis] * scale for axis in range(3))
		return distance_to_center - radius, closest
	if shape == 'ellipsoid':
		return _ellipsoid_point_distance(point, center,
			primitive.get('radii', primitive.get('half_extents',
				target.get('half_extents', (0.1, 0.1, 0.1)))),
			primitive.get('rotation_yaw_degrees',
				target.get('rotation_yaw_degrees', 0.0)))
	if shape == 'capsule':
		return _capsule_point_distance(point, center,
			primitive.get('radius', 0.1), primitive.get('half_length', 0.0),
			primitive.get('axis', 'y'), primitive.get(
				'rotation_yaw_degrees', target.get(
					'rotation_yaw_degrees', 0.0)))
	half = primitive.get('half_extents', target.get('half_extents'))
	yaw_degrees = float(primitive.get('rotation_yaw_degrees',
		target.get('rotation_yaw_degrees', 0.0)) or 0.0)
	if half is not None and abs(yaw_degrees) > 0.0001:
		half = tuple(max(0.0, float(value)) for value in half)
		radians = math.radians(-yaw_degrees)
		cosine = math.cos(radians)
		sine = math.sin(radians)
		dx = float(point[0]) - center[0]
		dz = float(point[2]) - center[2]
		local = (dx * cosine - dz * sine, float(point[1]) - center[1],
			dx * sine + dz * cosine)
		clamped = tuple(max(-half[axis], min(half[axis], local[axis]))
			for axis in range(3))
		forward = math.radians(yaw_degrees)
		cosine = math.cos(forward)
		sine = math.sin(forward)
		closest = (center[0] + clamped[0] * cosine - clamped[2] * sine,
			center[1] + clamped[1],
			center[2] + clamped[0] * sine + clamped[2] * cosine)
		delta = tuple(float(closest[axis]) - float(point[axis])
			for axis in range(3))
		return _vector_length(delta), closest
	minimum = primitive.get('minimum', target.get('minimum'))
	maximum = primitive.get('maximum', target.get('maximum'))
	closest = _closest_point_aabb(point, minimum, maximum)
	delta = tuple(float(closest[axis]) - float(point[axis])
		for axis in range(3))
	return _vector_length(delta), closest


def _vector_add(first, second):
	return tuple(float(first[index]) + float(second[index])
		for index in range(3))


def _vector_subtract(first, second):
	return tuple(float(first[index]) - float(second[index])
		for index in range(3))


def _vector_scale(vector, factor):
	return tuple(float(value) * float(factor) for value in vector)


def _vector_dot(first, second):
	return sum(float(first[index]) * float(second[index])
		for index in range(3))


def _vector_cross(first, second):
	return (
		float(first[1]) * float(second[2]) -
			float(first[2]) * float(second[1]),
		float(first[2]) * float(second[0]) -
			float(first[0]) * float(second[2]),
		float(first[0]) * float(second[1]) -
			float(first[1]) * float(second[0]),
	)


def _vector_near_zero(vector, epsilon=0.00000001):
	return _vector_dot(vector, vector) <= float(epsilon) * float(epsilon)


def _triple_product(first, second, third):
	return _vector_cross(_vector_cross(first, second), third)


def _primitive_center(target, primitive):
	center = primitive.get('center', target.get('center'))
	if center is not None:
		return tuple(float(value) for value in center)
	minimum = primitive.get('minimum', target.get('minimum'))
	maximum = primitive.get('maximum', target.get('maximum'))
	return tuple((float(minimum[index]) + float(maximum[index])) * 0.5
		for index in range(3))


def _yaw_vector(vector, degrees):
	angle = math.radians(float(degrees or 0.0))
	cosine = math.cos(angle)
	sine = math.sin(angle)
	return (
		float(vector[0]) * cosine - float(vector[2]) * sine,
		float(vector[1]),
		float(vector[0]) * sine + float(vector[2]) * cosine,
	)


def _primitive_support(target, primitive, direction):
	'''Farthest point of one convex internal primitive in ``direction``.'''
	shape = str(primitive.get('shape', 'aabb') or 'aabb').lower()
	center = _primitive_center(target, primitive)
	direction = tuple(float(value) for value in direction)
	direction_length = _vector_length(direction)
	unit = ((1.0, 0.0, 0.0) if direction_length <= 0.00000001 else
		tuple(value / direction_length for value in direction))
	if shape == 'sphere':
		radius = max(0.0, float(primitive.get('radius', 0.0) or 0.0))
		return _vector_add(center, _vector_scale(unit, radius))
	if shape == 'ellipsoid':
		radii = tuple(max(0.0001, float(value)) for value in primitive.get(
			'radii', primitive.get('half_extents', target.get(
				'half_extents', (0.1, 0.1, 0.1)))))
		yaw = float(primitive.get('rotation_yaw_degrees', target.get(
			'rotation_yaw_degrees', 0.0)) or 0.0)
		local_direction = _yaw_vector(direction, -yaw)
		denominator = math.sqrt(sum(
			(radii[index] * local_direction[index]) ** 2
			for index in range(3)))
		if denominator <= 0.00000001:
			return center
		local_support = tuple(
			(radii[index] * radii[index] * local_direction[index]) /
			denominator for index in range(3))
		return _vector_add(center, _yaw_vector(local_support, yaw))
	if shape == 'capsule':
		radius = max(0.0, float(primitive.get('radius', 0.1) or 0.0))
		half_length = max(0.0, float(primitive.get(
			'half_length', 0.0) or 0.0))
		axis_name = str(primitive.get('axis', 'y') or 'y').lower()
		axis = {'x': (1.0, 0.0, 0.0), 'y': (0.0, 1.0, 0.0),
			'z': (0.0, 0.0, 1.0)}.get(axis_name, (0.0, 1.0, 0.0))
		axis = _yaw_vector(axis, primitive.get('rotation_yaw_degrees',
			target.get('rotation_yaw_degrees', 0.0)))
		end = _vector_scale(axis, half_length if _vector_dot(
			direction, axis) >= 0.0 else -half_length)
		return _vector_add(center, _vector_add(
			end, _vector_scale(unit, radius)))
	half = primitive.get('half_extents', target.get('half_extents'))
	if half is None:
		minimum = primitive.get('minimum', target.get('minimum'))
		maximum = primitive.get('maximum', target.get('maximum'))
		half = tuple((float(maximum[index]) - float(minimum[index])) * 0.5
			for index in range(3))
	half = tuple(max(0.0, float(value)) for value in half)
	yaw = float(primitive.get('rotation_yaw_degrees', target.get(
		'rotation_yaw_degrees', 0.0)) or 0.0)
	local_direction = _yaw_vector(direction, -yaw)
	local_support = tuple(half[index] if local_direction[index] >= 0.0
		else -half[index] for index in range(3))
	return _vector_add(center, _yaw_vector(local_support, yaw))


def _cone_support(apex, axis, depth, tangent, direction):
	'''Farthest point of a capped right circular cone in ``direction``.'''
	direction = tuple(float(value) for value in direction)
	axial = _vector_dot(direction, axis)
	perpendicular = _vector_subtract(direction, _vector_scale(axis, axial))
	perpendicular_length = _vector_length(perpendicular)
	coefficient = axial + float(tangent) * perpendicular_length
	if depth <= 0.0 or coefficient <= 0.0:
		return tuple(apex)
	base = _vector_add(apex, _vector_scale(axis, depth))
	if perpendicular_length <= 0.00000001:
		return base
	rim_direction = _vector_scale(perpendicular, 1.0 / perpendicular_length)
	return _vector_add(base, _vector_scale(
		rim_direction, float(depth) * float(tangent)))


def _gjk_line(simplex):
	a = simplex[-1]
	b = simplex[-2]
	ab = _vector_subtract(b, a)
	ao = _vector_scale(a, -1.0)
	if _vector_dot(ab, ao) <= 0.0:
		simplex[:] = [a]
		return False, ao
	direction = _triple_product(ab, ao, ab)
	if not _vector_near_zero(direction):
		return False, direction
	denominator = _vector_dot(ab, ab)
	projection = (_vector_dot(ao, ab) / denominator
		if denominator > 0.000000000001 else 0.0)
	if projection <= 1.0 + 0.0000001:
		return True, (0.0, 0.0, 0.0)
	simplex[:] = [b]
	return False, _vector_scale(b, -1.0)


def _gjk_triangle(simplex):
	a = simplex[-1]
	b = simplex[-2]
	c = simplex[-3]
	ab = _vector_subtract(b, a)
	ac = _vector_subtract(c, a)
	ao = _vector_scale(a, -1.0)
	abc = _vector_cross(ab, ac)
	if _vector_dot(_vector_cross(abc, ac), ao) > 0.0:
		if _vector_dot(ac, ao) > 0.0:
			simplex[:] = [c, a]
			return _gjk_line(simplex)
		simplex[:] = [b, a]
		return _gjk_line(simplex)
	if _vector_dot(_vector_cross(ab, abc), ao) > 0.0:
		simplex[:] = [b, a]
		return _gjk_line(simplex)
	if _vector_dot(abc, ao) > 0.0:
		return False, abc
	simplex[:] = [b, c, a]
	return False, _vector_scale(abc, -1.0)


def _gjk_tetrahedron(simplex):
	a = simplex[-1]
	b = simplex[-2]
	c = simplex[-3]
	d = simplex[-4]
	ao = _vector_scale(a, -1.0)
	ab = _vector_subtract(b, a)
	ac = _vector_subtract(c, a)
	ad = _vector_subtract(d, a)
	faces = (
		([c, b, a], _vector_cross(ab, ac), ad),
		([d, c, a], _vector_cross(ac, ad), ab),
		([b, d, a], _vector_cross(ad, ab), ac),
	)
	for face, normal, opposite in faces:
		if _vector_dot(normal, opposite) > 0.0:
			normal = _vector_scale(normal, -1.0)
		if _vector_dot(normal, ao) > 0.0:
			simplex[:] = face
			return _gjk_triangle(simplex)
	return True, (0.0, 0.0, 0.0)


def _gjk_intersects(support, initial_direction):
	direction = tuple(float(value) for value in initial_direction)
	if _vector_near_zero(direction):
		direction = (1.0, 0.0, 0.0)
	first = support(direction)
	simplex = [first]
	direction = _vector_scale(first, -1.0)
	if _vector_near_zero(direction):
		return True
	for unused_index in range(48):
		point = support(direction)
		if _vector_dot(point, direction) < -0.0000001:
			return False
		if any(_vector_length(_vector_subtract(point, existing)) <=
				0.00000001 for existing in simplex):
			return False
		simplex.append(point)
		if len(simplex) == 2:
			contains, direction = _gjk_line(simplex)
		elif len(simplex) == 3:
			contains, direction = _gjk_triangle(simplex)
		else:
			contains, direction = _gjk_tetrahedron(simplex)
		if contains or _vector_near_zero(direction):
			return True
	return False


def _primitive_intersects_cone(target, primitive, apex, axis, depth,
		tangent):
	center = _primitive_center(target, primitive)
	cone_center = _vector_add(apex, _vector_scale(axis, depth * 0.5))
	initial = _vector_subtract(center, cone_center)
	def support(direction):
		return _vector_subtract(
			_primitive_support(target, primitive, direction),
			_cone_support(apex, axis, depth, tangent,
				_vector_scale(direction, -1.0)))
	return _gjk_intersects(support, initial)


def _primitive_cone_entry(target, primitive, apex, axis, depth, tangent):
	'''First axial depth at which the growing finite cone touches a primitive.'''
	if not _primitive_intersects_cone(
			target, primitive, apex, axis, depth, tangent):
		return None
	distance, closest = _primitive_distance_record(
		apex, target, primitive)
	if distance <= 0.0000001:
		return 0.0
	# Preserve an exact entry for points/volumes whose apex-nearest point is
	# already inside the cone. The binary convex-intersection search below is
	# needed only for the edge-straddling case that the old closest-point-only
	# test missed.
	delta = _vector_subtract(closest, apex)
	axial = _vector_dot(delta, axis)
	radial = _vector_length(_vector_subtract(
		delta, _vector_scale(axis, axial)))
	if (axial >= -0.0000001 and axial <= float(depth) + 0.0000001 and
			radial <= max(0.0, axial) * float(tangent) + 0.0000001):
		return max(0.0, axial)
	low = 0.0
	high = float(depth)
	for unused_index in range(14):
		middle = (low + high) * 0.5
		if _primitive_intersects_cone(
				target, primitive, apex, axis, middle, tangent):
			high = middle
		else:
			low = middle
	return high


@TRACE_CALL('modules', 'resolve_internal_explosion')
def resolve_explosion(layout, parent_contexts, radius_m, mode='sphere',
		cone_cos=0.92387953, excluded_entities=None, cone_depth_m=None):
	if not layout or not layout.get('valid'):
		return ()
	radius = max(0.0, float(radius_m or 0.0))
	if radius <= 0.0001:
		return ()
	excluded = set(excluded_entities or ())
	cone_cosine = max(0.0001, min(1.0, float(cone_cos)))
	cone_tangent = (math.sqrt(max(0.0, 1.0 - cone_cosine * cone_cosine)) /
		cone_cosine)
	cone_depth = (max(0.0, float(cone_depth_m))
		if cone_depth_m is not None else radius * cone_cosine)
	candidates = []
	for target in layout.get('targets', ()):
		if target.get('entity') in excluded:
			continue
		context = parent_contexts.get(target.get('parent'))
		if not context:
			continue
		point = context.get('point')
		direction = _normalised(context.get('direction', (0.0, 0.0, 1.0)))
		primitives = tuple(target.get('primitives') or ({
			'primitive_id': target.get('zone_id'),
			'minimum': target.get('minimum'),
			'maximum': target.get('maximum'),
		},))
		for primitive_index, primitive in enumerate(primitives):
			distance, closest = _primitive_distance_record(
				point, target, primitive)
			if distance > radius + 0.0001:
				continue
			to_target = tuple(float(closest[axis]) - float(point[axis])
				for axis in range(3))
			to_length = _vector_length(to_target)
			alignment = 1.0
			if to_length > 0.0001:
				alignment = sum(direction[axis] * to_target[axis]
					for axis in range(3)) / to_length
			cone_entry = None
			if mode == 'cone':
				cone_entry = _primitive_cone_entry(
					target, primitive, point, direction, cone_depth,
					cone_tangent)
				if cone_entry is None:
					continue
			record = dict(target)
			record['primitive_id'] = primitive.get('primitive_id')
			record['physical_interval_index'] = primitive_index
			record['hit_point'] = closest
			record['explosion_distance_m'] = distance
			record['explosion_radius_m'] = radius
			record['explosion_scale'] = max(0.0, 1.0 - distance / radius)
			record['explosion_mode'] = mode
			record['explosion_alignment'] = alignment
			if cone_entry is not None:
				record['cone_entry_axial_m'] = float(cone_entry)
				record['cone_depth_m'] = float(cone_depth)
			record['path_length_m'] = 0.0
			record['penetration_resistance_mm'] = 0.0
			record['penetration_resistance_source'] = (
				'wot_0.8.2_explosion_volume')
			record['remaining_penetration_before'] = None
			record['remaining_penetration_after'] = None
			record['projectile_stopped'] = False
			candidates.append(record)
	candidates.sort(key=lambda item: (
		float(item.get('cone_entry_axial_m', item.get(
			'explosion_distance_m', 0.0))),
		item.get('parent', ''), item.get('zone_id', ''),
		item.get('physical_interval_index', 0)))
	result = []
	damaged_entities = set()
	for candidate in candidates:
		entity = candidate.get('entity')
		candidate['damage_eligible'] = bool(entity not in damaged_entities)
		if candidate['damage_eligible']:
			damaged_entities.add(entity)
		result.append(candidate)
	return tuple(result)


@TRACE_CALL('modules', 'resolve_internal_segment')
def resolve_segment(layout, parent_segments, excluded_entities=None):
	resolved = resolve_segments(layout, parent_segments, None,
		excluded_entities)
	return resolved[0] if resolved else None

def coverage_report(layouts):
	state = new_coverage_state()
	for layout in layouts:
		add_coverage_result(state, layout)
	return finalize_coverage_state(state)


def clear_cache(reload_overrides=False):
	_LAYOUT_CACHE.clear()
	if reload_overrides and _layout_store is not None:
		try:
			_layout_store.clear_cache()
		except Exception:
			pass


def clear_runtime_evidence():
	_RUNTIME_VERIFICATION.clear()


def target_by_identity(layout, entity=None, parent=None, zone_id=None,
        primitive_id=None):
    if not layout or not layout.get('valid'):
        return None
    for target in layout.get('targets', ()):
        if entity is not None and target.get('entity') != entity:
            continue
        if parent is not None and target.get('parent') != parent:
            continue
        if zone_id is not None and target.get('zone_id') != zone_id:
            continue
        if primitive_id is None:
            return target
        for primitive in target.get('primitives', ()):
            if primitive.get('primitive_id') == primitive_id:
                result = dict(target)
                result['selected_primitive'] = primitive
                return result
    return None


def geometry_signature(target, primitive_id=None):
    if target is None:
        return None
    primitive = None
    for item in target.get('primitives', ()):
        if primitive_id is None or item.get('primitive_id') == primitive_id:
            primitive = item
            if primitive_id is not None:
                break
    if primitive is None:
        primitive = {
            'primitive_id': target.get('zone_id'),
            'shape': target.get('shape', 'box'),
            'center': target.get('center'),
            'half_extents': target.get('half_extents'),
            'minimum': target.get('minimum'),
            'maximum': target.get('maximum'),
        }
    return {
        'entity': target.get('entity'),
        'kind': target.get('kind'),
        'parent': target.get('parent'),
        'zone_id': target.get('zone_id'),
        'primitive_id': primitive.get('primitive_id'),
        'shape': primitive.get('shape', 'box'),
        'rotation_yaw_degrees': float(primitive.get('rotation_yaw_degrees',
            target.get('rotation_yaw_degrees', 0.0)) or 0.0),
        'center': tuple(primitive.get('center', target.get('center', ()))),
        'half_extents': tuple(primitive.get('half_extents',
            target.get('half_extents', ()))),
        'minimum': tuple(primitive.get('minimum', target.get('minimum', ()))),
        'maximum': tuple(primitive.get('maximum', target.get('maximum', ()))),
        'model_signature': target.get('model_signature'),
        'layout_key': target.get('layout_key'),
        'calibration_status': target.get('calibration_status'),
    }


def compare_geometry_signatures(expected, current, tolerance=0.0005):
    if not expected or not current:
        return {'synchronized': False, 'reason': 'missing_geometry_signature'}
    differences = []
    for key in ('entity', 'kind', 'parent', 'zone_id', 'primitive_id', 'shape'):
        if expected.get(key) != current.get(key):
            differences.append('%s:%s!=%s' % (key, expected.get(key),
                current.get(key)))
    if abs(float(expected.get('rotation_yaw_degrees', 0.0) or 0.0) -
            float(current.get('rotation_yaw_degrees', 0.0) or 0.0)) > 0.001:
        differences.append('rotation_yaw_degrees:delta')
    for key in ('center', 'half_extents', 'minimum', 'maximum'):
        first = expected.get(key)
        second = current.get(key)
        if first is None or second is None or len(first) != len(second):
            differences.append('%s:missing' % key)
            continue
        if any(abs(float(first[index]) - float(second[index])) > tolerance
                for index in range(len(first))):
            differences.append('%s:delta' % key)
    return {
        'synchronized': not differences,
        'reason': ('MATCH' if not differences else ';'.join(differences)),
        'tolerance_m': float(tolerance),
        'expected': expected,
        'current': current,
    }


def validate_layout_geometry(layout):
    warnings = []
    if not layout or not layout.get('valid'):
        return ({'severity': 'error', 'code': 'INVALID_LAYOUT',
            'message': str((layout or {}).get('reason', 'invalid layout'))},)
    seen = {}
    entities = set()
    for target in layout.get('targets', ()):
        entity = str(target.get('entity', ''))
        parent = str(target.get('parent', ''))
        zone_id = str(target.get('zone_id', ''))
        entities.add(entity)
        half = tuple(target.get('half_extents', (0.0, 0.0, 0.0)))
        center = tuple(target.get('center', (0.0, 0.0, 0.0)))
        if len(half) != 3 or min([float(value) for value in half] or [0.0]) <= 0.0:
            warnings.append({'severity': 'error', 'code': 'ZERO_VOLUME',
                'entity': entity, 'zone_id': zone_id,
                'message': 'zone has a zero/invalid half extent'})
        cap = target.get('physical_half_cap_m')
        if cap is not None and len(cap) == 3 and any(float(half[index]) >
                float(cap[index]) * 1.151 for index in range(3)):
            warnings.append({'severity': 'warning', 'code': 'PHYSICAL_CAP',
                'entity': entity, 'zone_id': zone_id,
                'message': 'zone exceeds per-entity physical size cap'})
        bounds = layout.get('bounds', {}).get(parent)
        if bounds and len(bounds) == 2:
            minimum, maximum = bounds
            target_min = target.get('minimum')
            target_max = target.get('maximum')
            if target_min is not None and target_max is not None:
                outside = 0
                for axis in range(3):
                    if (float(target_min[axis]) < float(minimum[axis]) - 0.02 or
                            float(target_max[axis]) > float(maximum[axis]) + 0.02):
                        outside += 1
                if outside:
                    warnings.append({'severity': 'warning',
                        'code': 'OUTSIDE_PARENT', 'entity': entity,
                        'zone_id': zone_id,
                        'message': 'zone extends outside %s bounds' % parent})
        key = (parent, tuple(round(float(value), 3) for value in center),
            tuple(round(float(value), 3) for value in half))
        previous = seen.get(key)
        if previous is not None and previous != (entity, zone_id):
            warnings.append({'severity': 'warning', 'code': 'DUPLICATE_ZONE',
                'entity': entity, 'zone_id': zone_id,
                'message': 'zone overlaps exactly with %s/%s' % previous})
        else:
            seen[key] = (entity, zone_id)
    for required in ('engine', 'ammoBay', 'fuelTank'):
        if required not in entities:
            warnings.append({'severity': 'info', 'code': 'MISSING_' + required,
                'entity': required, 'zone_id': '',
                'message': 'layout contains no %s zone' % required})
    return tuple(warnings)
