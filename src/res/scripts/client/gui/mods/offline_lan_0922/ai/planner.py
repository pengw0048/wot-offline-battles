# -*- coding: utf-8 -*-
"""Deterministic strategic AI for offline/LAN bots.

This module is deliberately engine-free so its decisions can be tested on a
modern Python interpreter while the shipped client still imports it on its
Python 2 runtime. It owns stable personalities, vehicle-role scoring, shared
team contact memory, route assignment and target selection. Rendering,
physics, collision and shell resolution remain in ``offline_battle.py``.
"""

import hashlib
import math
import random

from gui.mods.offline_lan_0922.ai import maps as bot_ai_maps


CONTACT_MEMORY_SECONDS = 7.0
TARGET_HYSTERESIS_BONUS = 18.0
DISCOVERED_ARTILLERY_PRIORITY_BONUS = 48.0
LOCAL_FORCE_RADIUS = 185.0
BATTLE_TIER_RADIUS = 1
MATCH_CLASSES = ('heavyTank', 'mediumTank', 'AT-SPG', 'lightTank', 'SPG')
CLASS_ROUTE_AFFINITY_WEIGHT = 42.0
ARTILLERY_ROUTE_REPEAT_PENALTY = 120.0

BOT_TIER_MODE_RANDOM = 'random'
BOT_TIER_MODE_SAME = 'same'
BOT_TIER_MODE_MINUS1_0 = 'minus1_0'
BOT_TIER_MODE_0_PLUS1 = '0_plus1'
BOT_TIER_MODE_MINUS1_PLUS2 = 'minus1_plus2'
BOT_TIER_MODES = (
	BOT_TIER_MODE_RANDOM, BOT_TIER_MODE_SAME, BOT_TIER_MODE_MINUS1_0,
	BOT_TIER_MODE_0_PLUS1, BOT_TIER_MODE_MINUS1_PLUS2)


def _number(value, default=0.0):
	try:
		result = float(value)
		if math.isinf(result) or math.isnan(result):
			return float(default)
		return result
	except Exception:
		return float(default)


def vehicle_in_battle_tier_band(player_tier, candidate_tier):
	"""Keep one battle within a three-tier band, e.g. VI-VIII for tier VII."""
	try:
		return abs(int(candidate_tier) - int(player_tier)) <= BATTLE_TIER_RADIUS
	except Exception:
		return False


def select_bot_lineup(pool, count, spg_limit=1, fallback_candidates=()):
	"""Fill a team while enforcing an exact SPG cap.

	``AT-SPG`` is a tank destroyer in the legacy tags and does not consume the
	artillery slot.  Human artillery is accounted for by the caller through a
	reduced ``spg_limit``.
	"""
	count = max(0, int(count))
	spg_limit = max(0, int(spg_limit))
	pool = list(pool or ())
	if not pool or count <= 0:
		return []
	regular = []
	seen = set()
	for candidate in pool + list(fallback_candidates or ()):
		try:
			tags = candidate['tags']
			name = candidate['name']
		except Exception:
			continue
		if 'SPG' in tags or name in seen:
			continue
		seen.add(name)
		regular.append(candidate)
	result = []
	spg_count = 0
	regular_index = 0
	for index in range(count):
		candidate = pool[index % len(pool)]
		try:
			is_spg = 'SPG' in candidate['tags']
		except Exception:
			is_spg = False
		if is_spg and spg_count >= spg_limit:
			if not regular:
				continue
			candidate = regular[regular_index % len(regular)]
			regular_index += 1
			is_spg = False
		if is_spg:
			spg_count += 1
		result.append(candidate)
	return result


def vehicle_match_class(candidate):
	"""Return the exact legacy matchmaking class for a vehicle record."""
	try:
		tags = candidate['tags']
	except Exception:
		tags = ()
	for class_tag in MATCH_CLASSES:
		if class_tag in tags:
			return class_tag
	return 'mediumTank'


def choose_match_tiers(player_tier, mode_roll, side_roll=0.5,
		available_tiers=()):
	"""Choose a one-, two-, or three-tier battle that includes the player."""
	try:
		player_tier = max(1, min(10, int(player_tier)))
	except Exception:
		player_tier = 1
	available = set()
	for value in (available_tiers or range(1, 11)):
		try:
			value = int(value)
		except Exception:
			continue
		if 1 <= value <= 10:
			available.add(value)
	available.add(player_tier)
	lower = player_tier - 1 if player_tier - 1 in available else None
	upper = player_tier + 1 if player_tier + 1 in available else None
	try:
		mode_roll = float(mode_roll)
		side_roll = float(side_roll)
	except Exception:
		mode_roll = side_roll = 0.5
	if mode_roll < 0.28 or (lower is None and upper is None):
		return (player_tier,)
	if mode_roll < 0.72 or lower is None or upper is None:
		if lower is not None and upper is not None:
			other = lower if side_roll < 0.5 else upper
		elif lower is not None:
			other = lower
		else:
			other = upper
		return tuple(sorted((player_tier, other)))
	return (lower, player_tier, upper)


def normalize_bot_tier_mode(value):
	"""Return one supported waiting-room Bot tier preset."""
	return value if value in BOT_TIER_MODES else BOT_TIER_MODE_RANDOM


def bot_match_tiers(player_tier, mode, mode_roll=0.5, side_roll=0.5,
					available_tiers=()):
	"""Resolve a host-selected tier preset against the available catalog."""
	try:
		player_tier = max(1, min(10, int(player_tier)))
	except Exception:
		player_tier = 1
	available = set()
	for value in (available_tiers or range(1, 11)):
		try:
			value = int(value)
		except Exception:
			continue
		if 1 <= value <= 10:
			available.add(value)
	available.add(player_tier)
	mode = normalize_bot_tier_mode(mode)
	if mode == BOT_TIER_MODE_RANDOM:
		return choose_match_tiers(
			player_tier, mode_roll, side_roll, available)
	desired = {
		BOT_TIER_MODE_SAME: (player_tier,),
		BOT_TIER_MODE_MINUS1_0: (player_tier - 1, player_tier),
		BOT_TIER_MODE_0_PLUS1: (player_tier, player_tier + 1),
		BOT_TIER_MODE_MINUS1_PLUS2: (
			player_tier - 1, player_tier, player_tier + 1, player_tier + 2),
	}[mode]
	return tuple(value for value in desired if value in available)


def vehicle_in_bot_tier_mode(player_tier, candidate_tier, mode):
	"""Whether a vehicle can fill the selected preset before template fill."""
	try:
		candidate_tier = int(candidate_tier)
	except Exception:
		return False
	if normalize_bot_tier_mode(mode) == BOT_TIER_MODE_RANDOM:
		return vehicle_in_battle_tier_band(player_tier, candidate_tier)
	return candidate_tier in bot_match_tiers(
		player_tier, mode, available_tiers=range(1, 11))


def select_vehicle_variety_pool(candidates, player_tier, max_unique,
		rng=None):
	"""Build a small texture-safe pool while preserving tiers and classes."""
	rng = rng or random
	max_unique = max(0, int(max_unique))
	if max_unique <= 0:
		return list(candidates or ())
	unique = []
	seen = set()
	for candidate in (candidates or ()):
		try:
			name = str(candidate['name'])
		except Exception:
			continue
		if name in seen:
			continue
		seen.add(name)
		unique.append(candidate)
	try:
		rng.shuffle(unique)
	except Exception:
		random.shuffle(unique)
	selected = []
	selected_names = set()

	def _take(options):
		for candidate in options:
			name = str(candidate['name'])
			if name not in selected_names:
				selected.append(candidate)
				selected_names.add(name)
				return True
		return False

	# Prefer one of each role at the selected tier, then fill any missing role
	# from a neighbouring tier. This keeps single-tier matches tactically varied.
	for class_tag in MATCH_CLASSES:
		if len(selected) >= max_unique:
			break
		exact = [candidate for candidate in unique
			if int(candidate.get('level', 0) or 0) == int(player_tier) and
			vehicle_match_class(candidate) == class_tag]
		if not _take(exact):
			_take([candidate for candidate in unique
				if vehicle_match_class(candidate) == class_tag])
	# Make every available tier representable before using the remaining slots.
	levels = sorted(set(int(candidate.get('level', 0) or 0)
		for candidate in unique))
	for level in levels:
		if len(selected) >= max_unique:
			break
		if any(int(candidate.get('level', 0) or 0) == level
				for candidate in selected):
			continue
		_take([candidate for candidate in unique
			if int(candidate.get('level', 0) or 0) == level])
	for candidate in unique:
		if len(selected) >= max_unique:
			break
		_take((candidate,))
	return selected


def shared_human_requirements(team_profiles):
	"""Return the per-profile maxima that a mirrored team template must hold."""
	representatives = {}
	required_counts = {}
	for profiles in (team_profiles or {}).values():
		team_counts = {}
		for profile in (profiles or ()):
			try:
				key = (int(profile.get('level', 0) or 0),
					vehicle_match_class(profile))
			except Exception:
				continue
			team_counts[key] = team_counts.get(key, 0) + 1
			representatives.setdefault(key, profile)
		for key, count in team_counts.items():
			required_counts[key] = max(required_counts.get(key, 0), count)
	result = []
	for key in sorted(required_counts):
		for unused in range(required_counts[key]):
			result.append(representatives[key])
	return result


def build_match_template(pool, team_size, player_candidate, allowed_tiers,
		rng=None, required_profiles=()):
	"""Return one tier/class template that both teams can independently fill."""
	rng = rng or random
	team_size = max(1, int(team_size))
	allowed = tuple(sorted(set(int(value) for value in allowed_tiers)))
	if not allowed:
		allowed = (int(player_candidate.get('level', 1) or 1),)
	player_tier = int(player_candidate.get('level', allowed[0]) or allowed[0])
	player_class = vehicle_match_class(player_candidate)
	usable = [candidate for candidate in (pool or ())
		if int(candidate.get('level', 0) or 0) in allowed]
	if not usable:
		usable = [player_candidate]
	required = list(required_profiles or ())
	if not required:
		required = [player_candidate]
	required = required[:team_size]

	tier_slots = []
	while len(tier_slots) < team_size:
		for level in allowed:
			if len(tier_slots) >= team_size:
				break
			tier_slots.append(level)
	try:
		rng.shuffle(tier_slots)
	except Exception:
		random.shuffle(tier_slots)

	regular_classes = ('heavyTank', 'mediumTank', 'AT-SPG', 'lightTank')
	class_slots = []
	while len(class_slots) < team_size:
		for class_tag in regular_classes:
			if len(class_slots) >= team_size:
				break
			class_slots.append(class_tag)
	has_spg = any(vehicle_match_class(candidate) == 'SPG' for candidate in usable)
	required_spgs = sum(vehicle_match_class(profile) == 'SPG'
		for profile in required)
	try:
		include_spg = has_spg and (required_spgs > 0 or rng.random() < 0.65)
	except Exception:
		include_spg = required_spgs > 0
	if include_spg:
		class_slots[-1] = 'SPG'
	try:
		rng.shuffle(class_slots)
	except Exception:
		random.shuffle(class_slots)

	result = []
	usage = {}
	spg_count = 0
	# Reserve an exact tier/class slot for every human profile needed by either
	# team. If one side lacks that human, its bot lineup fills the same slot.
	for profile in required:
		desired_tier = int(profile.get('level', player_tier) or player_tier)
		desired_class = vehicle_match_class(profile)
		choices = [candidate for candidate in usable
			if int(candidate.get('level', 0) or 0) == desired_tier and
			vehicle_match_class(candidate) == desired_class]
		candidate = choices[0] if choices else profile
		result.append(candidate)
		name = str(candidate.get('name', ''))
		usage[name] = usage.get(name, 0) + 1
		if desired_class == 'SPG':
			spg_count += 1
		if desired_tier in tier_slots:
			tier_slots.remove(desired_tier)
		elif tier_slots:
			tier_slots.pop()
		if desired_class in class_slots:
			class_slots.remove(desired_class)
		elif class_slots:
			class_slots.pop()

	for index in range(len(result), team_size):
		desired_tier = tier_slots.pop() if tier_slots else player_tier
		desired_class = class_slots.pop() if class_slots else player_class
		choices = [candidate for candidate in usable
			if int(candidate.get('level', 0) or 0) == desired_tier and
			vehicle_match_class(candidate) == desired_class]
		if not choices:
			choices = [candidate for candidate in usable
				if int(candidate.get('level', 0) or 0) == desired_tier]
		if not choices:
			choices = list(usable)
		if spg_count >= 1:
			regular = [candidate for candidate in choices
				if vehicle_match_class(candidate) != 'SPG']
			if regular:
				choices = regular
		try:
			rng.shuffle(choices)
		except Exception:
			random.shuffle(choices)
		candidate = min(choices, key=lambda value:
			usage.get(str(value.get('name', '')), 0))
		name = str(candidate.get('name', ''))
		usage[name] = usage.get(name, 0) + 1
		if vehicle_match_class(candidate) == 'SPG':
			spg_count += 1
		result.append(candidate)
	return result


def remaining_match_template(template, human_profiles):
	"""Remove the closest template slot for each human already on a team."""
	remaining = list(template or ())
	for human in (human_profiles or ()):
		if not remaining:
			break
		human_tier = int(human.get('level', 0) or 0)
		human_class = vehicle_match_class(human)
		best_index = 0
		best_score = None
		for index, candidate in enumerate(remaining):
			candidate_tier = int(candidate.get('level', 0) or 0)
			candidate_class = vehicle_match_class(candidate)
			score = abs(candidate_tier - human_tier) * 3
			if candidate_class != human_class:
				score += 8
			if best_score is None or score < best_score:
				best_score = score
				best_index = index
		remaining.pop(best_index)
	return remaining


def bot_initially_visible(bot_team, player_team, spotting_enabled):
	"""Hide an enemy model before its first spotting update can run."""
	try:
		return (not bool(spotting_enabled) or
			int(bot_team) == int(player_team))
	except Exception:
		return not bool(spotting_enabled)


def entity_visible_to_minimap(entity):
	"""Expose a mock entity to the minimap only after its first spot."""
	return bool(getattr(entity, '_spot_visible', True))


def trimmed_sight_segment(observer, target, observer_height=2.5,
		target_height=1.5, start_clearance=4.0, end_clearance=4.0):
	"""Return a world ray that excludes the two vehicle hull volumes.

	The legacy battle creates a ``PyModelObstacle`` for every mock vehicle.  A
	world ray from centre to centre can therefore hit its shooter immediately or
	the intended target at the far end and report a blocked lane.  Static-world
	LOS only needs the part *between* the vehicles.  Very close vehicles have no
	meaningful middle segment and are considered mutually exposed by returning
	``None``.
	"""
	try:
		ox = float(observer[0]); oy = float(observer[1]); oz = float(observer[2])
		tx = float(target[0]); ty = float(target[1]); tz = float(target[2])
		dx = tx - ox
		dz = tz - oz
		distance = math.sqrt(dx * dx + dz * dz)
		start_clearance = max(0.0, float(start_clearance))
		end_clearance = max(0.0, float(end_clearance))
		if distance <= start_clearance + end_clearance + 0.5:
			return None
		unit_x = dx / distance
		unit_z = dz / distance
		return (
			(ox + unit_x * start_clearance, oy + float(observer_height),
			 oz + unit_z * start_clearance),
			(tx - unit_x * end_clearance, ty + float(target_height),
			 tz - unit_z * end_clearance),
		)
	except Exception:
		return ()


def route_toward_enemy(route, team, bases):
	"""Orient a multi-point route from the own flag to the enemy flag."""
	result = dict(route or {})
	result['role_weights'] = dict(result.get('role_weights', {}) or {})
	points = list(result.get('waypoints', ()) or ())
	if len(points) < 2:
		result['waypoints'] = tuple(points)
		return result
	try:
		own = bases.get(int(team))
		enemy = bases.get(3 - int(team))
		if own is None or enemy is None:
			result['waypoints'] = tuple(points)
			return result
		def _distance(point, base):
			return ((float(point[0]) - float(base[0])) ** 2 +
				(float(point[1]) - float(base[1])) ** 2)
		forward = _distance(points[0], own) + _distance(points[-1], enemy)
		reverse = _distance(points[-1], own) + _distance(points[0], enemy)
		if reverse < forward:
			points.reverse()
		own_point = (float(own[0]), float(own[1]), 0)
		enemy_point = (float(enemy[0]), float(enemy[1]), 0)
		if _distance(points[0], own) > 1.0:
			points.insert(0, own_point)
		else:
			points[0] = own_point
		if _distance(points[-1], enemy) > 1.0:
			points.append(enemy_point)
		else:
			points[-1] = enemy_point
	except Exception:
		pass
	result['waypoints'] = tuple(points)
	return result


def _mapping_get(value, key, default=None):
	if not isinstance(value, dict):
		raise TypeError('Expected a plain mapping')
	return value.get(key, default)


def _attribute_or_key(value, key, default=None):
	if isinstance(value, dict):
		return value.get(key, default)
	return getattr(value, key, default)


def stable_seed(*parts):
	"""Return the same positive integer on Python 2 and Python 3."""
	text_parts = []
	for part in parts:
		try:
			text_parts.append(str(part))
		except Exception:
			text_parts.append('?')
	payload = '|'.join(text_parts).encode('utf-8')
	return int(hashlib.sha1(payload).hexdigest()[:8], 16) & 0x7fffffff


def make_personality(seed):
	"""Create coherent per-battle preferences; this is not a skill rating."""
	rng = random.Random(int(seed))
	traits = {}
	for name in ('aggression', 'caution', 'teamwork', 'patience',
	             'initiative', 'adaptability', 'jiggle'):
		# Keep extreme personalities rare while preserving visible variation.
		traits[name] = 0.18 + rng.random() * 0.64
	traits['aggression'] = max(0.05, min(0.95,
		traits['aggression'] + (0.5 - traits['caution']) * 0.22))
	traits['caution'] = max(0.05, min(0.95,
		traits['caution'] + (0.5 - traits['aggression']) * 0.12))
	traits['route_jitter'] = rng.uniform(-4.0, 4.0)
	traits['hold_jitter'] = rng.uniform(-1.5, 1.5)
	return traits


def _tags_from_descriptor(descriptor):
	type_info = _attribute_or_key(descriptor, 'type', None)
	tags = _attribute_or_key(type_info, 'tags', ()) or ()
	try:
		return tuple(tags)
	except Exception:
		return ()


def _forward_speed(descriptor):
	physics = _attribute_or_key(descriptor, 'physics', {}) or {}
	limits = _mapping_get(physics, 'speedLimits', None)
	try:
		return abs(float(limits[0]))
	except Exception:
		return 0.0


def _primary_armor(component):
	if component is None:
		component = {}
	armor = _attribute_or_key(component, 'primaryArmor', 0.0)
	if isinstance(armor, (tuple, list)):
		return max([_number(item, 0.0) for item in armor] or [0.0])
	return _number(armor, 0.0)


def _middle_value(value, default=0.0):
	"""Return a representative value for scalar or min/max vehicle data."""
	if isinstance(value, (tuple, list)):
		values = [_number(item, default) for item in value]
		if values:
			return sum(values) / float(len(values))
	return _number(value, default)


def _shell_profiles(descriptor):
	"""Extract the small shell summary needed by the tactical planner."""
	gun = _attribute_or_key(descriptor, 'gun', {})
	if gun is None:
		gun = {}
	shots = _attribute_or_key(gun, 'shots', ()) or ()
	result = []
	try:
		iterator = enumerate(shots)
	except Exception:
		iterator = ()
	for index, shot in iterator:
		shell = _attribute_or_key(shot, 'shell', {})
		if shell is None:
			shell = {}
		kind = _attribute_or_key(shell, 'kind', '') or ''
		result.append({
			'index': int(index),
			'kind': str(kind),
			'penetration': _middle_value(
				_attribute_or_key(shell, 'piercingPower', 0.0), 0.0),
			'damage': _middle_value(
				_attribute_or_key(shell, 'damage', 0.0), 0.0),
			'speed': _number(
				_attribute_or_key(shot, 'speed', 0.0), 0.0),
		})
	return tuple(result)


def build_vehicle_profile(descriptor):
	"""Derive tactical roles from class tags plus available vehicle stats."""
	tags = _tags_from_descriptor(descriptor)
	class_tag = 'mediumTank'
	for candidate in ('heavyTank', 'mediumTank', 'lightTank', 'AT-SPG', 'SPG'):
		if candidate in tags:
			class_tag = candidate
			break

	role_defaults = {
		'heavyTank': {'brawler': 0.92, 'support': 0.55, 'flanker': 0.18,
		              'sniper': 0.20, 'scout': 0.05, 'artillery': 0.00},
		'mediumTank': {'brawler': 0.46, 'support': 0.76, 'flanker': 0.86,
		               'sniper': 0.48, 'scout': 0.42, 'artillery': 0.00},
		'lightTank': {'brawler': 0.10, 'support': 0.45, 'flanker': 0.72,
		              'sniper': 0.28, 'scout': 0.96, 'artillery': 0.00},
		'AT-SPG': {'brawler': 0.32, 'support': 0.78, 'flanker': 0.18,
		           'sniper': 0.92, 'scout': 0.08, 'artillery': 0.00},
		'SPG': {'brawler': 0.00, 'support': 0.10, 'flanker': 0.00,
		        'sniper': 0.16, 'scout': 0.00, 'artillery': 1.00},
	}
	roles = dict(role_defaults[class_tag])
	speed = _forward_speed(descriptor)
	hull = _attribute_or_key(descriptor, 'hull', {})
	turret = _attribute_or_key(descriptor, 'turret', {})
	armor = max(_primary_armor(hull), _primary_armor(turret))

	if speed >= 15.0:
		roles['flanker'] = min(1.0, roles['flanker'] + 0.12)
		roles['scout'] = min(1.0, roles['scout'] + 0.08)
	elif speed and speed < 9.0:
		roles['flanker'] = max(0.0, roles['flanker'] - 0.16)
	if armor >= 120.0:
		roles['brawler'] = min(1.0, roles['brawler'] + 0.18)
		roles['sniper'] = max(0.0, roles['sniper'] - 0.08)

	desired_ranges = {
		'heavyTank': (72.0, 260.0),
		'mediumTank': (135.0, 340.0),
		'lightTank': (175.0, 320.0),
		'AT-SPG': (255.0, 450.0),
		# The client proves a real shell trajectory before an SPG is reported
		# shootable.  These ranges therefore describe its battery role rather
		# than the old temporary 120 m direct-fire fallback.
		'SPG': (650.0, 1250.0),
	}
	desired_range, fire_range = desired_ranges[class_tag]
	if armor >= 120.0 and class_tag == 'AT-SPG':
		desired_range = 115.0
		fire_range = 320.0

	dominant = 'support'
	dominant_score = -1.0
	for role_name, role_score in roles.items():
		if role_score > dominant_score:
			dominant = role_name
			dominant_score = role_score

	type_info = _attribute_or_key(descriptor, 'type', None)
	vehicle_name = _attribute_or_key(type_info, 'name', class_tag)
	return {
		'class_tag': class_tag,
		'vehicle_name': str(vehicle_name or class_tag),
		'roles': roles,
		'dominant_role': dominant,
		'desired_range': desired_range,
		'fire_range': fire_range,
		'speed': speed,
		'armor': armor,
		'shells': _shell_profiles(descriptor),
	}


def select_shell_index(profile, target, personality):
	"""Plan standard AP first, HE for soft targets and premium for hard ones."""
	shells = profile.get('shells', ()) or ()
	if not shells:
		return 0
	target_armor = max(0.0, _number(target.get('armor', 0.0), 0.0))
	target_health = max(0.0, _number(target.get('health', 0.0), 0.0))
	non_he = []
	high_explosive = []
	for shell in shells:
		kind = str(shell.get('kind', '')).lower()
		is_he = ('high_explosive' in kind or
		         ('explosive' in kind and 'armor_piercing' not in kind))
		(high_explosive if is_he else non_he).append(shell)
	baseline = min(non_he, key=lambda value: int(value.get('index', 0))) \
		if non_he else None
	baseline_penetration = max(0.0, _number(
		(baseline or {}).get('penetration', 0.0), 0.0))
	standard = []
	premium = []
	for shell in non_he:
		penetration = max(0.0, _number(
			shell.get('penetration', 0.0), 0.0))
		if (shell is not baseline and baseline_penetration > 0.0 and
				penetration >= baseline_penetration * 1.03):
			premium.append(shell)
		else:
			standard.append(shell)
	best_standard = max(standard, key=lambda value: (
		_number(value.get('penetration', 0.0), 0.0),
		_number(value.get('damage', 0.0), 0.0),
		-int(value.get('index', 0)))) if standard else None
	best_premium = max(premium, key=lambda value: (
		_number(value.get('penetration', 0.0), 0.0),
		_number(value.get('damage', 0.0), 0.0),
		-int(value.get('index', 0)))) if premium else None
	best_he = max(high_explosive, key=lambda value: (
		_number(value.get('damage', 0.0), 0.0),
		_number(value.get('penetration', 0.0), 0.0),
		-int(value.get('index', 0)))) if high_explosive else None
	if target_armor <= 0.0:
		if best_standard is not None:
			return max(0, int(best_standard.get('index', 0)))
		if best_premium is not None:
			return max(0, int(best_premium.get('index', 0)))
		return (max(0, int(best_he.get('index', 0)))
			if best_he is not None else 0)
	if best_he is not None:
		he_penetration = max(0.0, _number(
			best_he.get('penetration', 0.0), 0.0))
		he_damage = max(0.0, _number(best_he.get('damage', 0.0), 0.0))
		if (target_armor <= he_penetration * 0.90 or
				(target_health > 0.0 and
				 target_health <= he_damage * (
					0.72 + personality['aggression'] * 0.18) and
				 target_armor <= he_penetration * 1.10)):
			return max(0, int(best_he.get('index', 0)))
	if best_standard is not None:
		standard_penetration = max(0.0, _number(
			best_standard.get('penetration', 0.0), 0.0))
		if (best_premium is not None and
				standard_penetration < target_armor * 1.05):
			return max(0, int(best_premium.get('index', 0)))
		return max(0, int(best_standard.get('index', 0)))
	if best_premium is not None:
		return max(0, int(best_premium.get('index', 0)))
	if best_he is not None:
		return max(0, int(best_he.get('index', 0)))
	return max(0, int(shells[0].get('index', 0)))


def _distance_2d(first, second):
	dx = _number(first[0]) - _number(second[0])
	dz = _number(first[2]) - _number(second[2])
	return math.sqrt(dx * dx + dz * dz)


def _angle_delta(target, current):
	delta = target - current
	while delta > math.pi:
		delta -= math.pi * 2.0
	while delta < -math.pi:
		delta += math.pi * 2.0
	return delta


def _map_data_with_baked_routes(map_data, baked_routes):
	"""Combine current strategy with graph-validated locomotion waypoints."""
	if map_data is None or not isinstance(baked_routes, dict):
		return map_data
	result = dict(map_data)
	routes = {}
	for team in (1, 2):
		converted = []
		strategy_routes = dict(
			(route.get('id'), route) for route in
			map_data.get('routes', {}).get(team, ()) or ())
		values = baked_routes.get(str(team), baked_routes.get(team, ())) or ()
		for raw in values:
			if not isinstance(raw, dict):
				continue
			waypoints = []
			for point in raw.get('waypoints', ()) or ():
				try:
					waypoints.append((float(point[0]), float(point[1]),
					                  bool(point[2]) if len(point) > 2 else False))
				except Exception:
					continue
			if not waypoints:
				continue
			route = dict(raw)
			# Navigation graphs prove geometry. Strategy remains source data so a
			# map-guide update cannot be masked by metadata copied into an older
			# bake. Unknown custom route ids retain their baked fallback metadata.
			strategy = strategy_routes.get(raw.get('id'))
			if strategy is not None:
				for key in ('capacity', 'risk'):
					if key in strategy:
						route[key] = strategy[key]
				for key in ('role_weights', 'class_weights'):
					if key in strategy:
						route[key] = dict(strategy.get(key, {}) or {})
			route['role_weights'] = dict(
				route.get('role_weights', {}) or {})
			if 'class_weights' in route:
				route['class_weights'] = dict(
					route.get('class_weights', {}) or {})
			route['waypoints'] = tuple(waypoints)
			converted.append(route)
		routes[team] = tuple(converted)
	if not routes.get(1) or not routes.get(2):
		return map_data
	result['routes'] = routes
	# Baked routes already carry their team orientation and graph-validated
	# endpoints.  They must not be passed through the legacy static-base
	# orientation helper, which may insert two unvalidated points and exceed the
	# 16-waypoint LAN protocol boundary.
	result['_routes_are_baked'] = True
	return result


class BattleDirector(object):
	"""Shared per-battle planner for both teams."""

	def __init__(self, map_name, battle_seed, bases=None, bounds=None,
	             baked_routes=None):
		self.map_name = bot_ai_maps.normalize_map_name(map_name)
		self.battle_seed = stable_seed(battle_seed, self.map_name)
		self.map_data = _map_data_with_baked_routes(
			bot_ai_maps.get_tactical_map(self.map_name), baked_routes)
		self._routes_are_baked = bool(
			self.map_data is not None and
			self.map_data.get('_routes_are_baked', False))
		self.bases = {}
		self.bounds = bounds
		if self.map_data is not None:
			self.bases.update(self.map_data.get('bases', {}))
			self.bounds = self.map_data.get('bounds', self.bounds)
		# The live arena definition is authoritative. Static tactical data only
		# supplies a fallback for tests or incomplete legacy DataSections.
		self.bases.update(dict(bases or {}))
		self.agents = {}
		self.contacts = {1: {}, 2: {}}
		self.route_usage = {}
		self.artillery_route_usage = {}

	def register(self, bot_id, team, descriptor, display_name='Bot'):
		return self.register_profile(
			bot_id, team, build_vehicle_profile(descriptor), display_name)

	def register_profile(self, bot_id, team, profile, display_name='Bot'):
		"""Register serialized profile data on either client or LAN server."""
		bot_id = int(bot_id)
		agent = self.agents.get(bot_id)
		if agent is not None:
			return agent
		profile = dict(profile or {})
		profile['roles'] = dict(profile.get('roles', {}) or {})
		profile['shells'] = tuple(profile.get('shells', ()) or ())
		seed = stable_seed(self.battle_seed, bot_id, display_name,
		                   profile.get('vehicle_name'))
		agent = {
			'id': bot_id,
			'team': int(team),
			'profile': profile,
			'personality': make_personality(seed),
			'seed': seed,
			'route': None,
			'waypoint_index': 0,
			'target_id': None,
			'last_order': None,
			'position': None,
			'health_fraction': 1.0,
		}
		agent['route'] = self._assign_route(agent)
		self.agents[bot_id] = agent
		return agent

	def _routes_for(self, team):
		if self.map_data is None:
			return ()
		return self.map_data.get('routes', {}).get(int(team), ()) or ()

	def _assign_route(self, agent):
		routes = self._routes_for(agent['team'])
		if not routes:
			return None
		profile = agent['profile']
		is_artillery = profile.get('class_tag') == 'SPG'
		# Capacities are a lane distribution contract, not a soft preference.
		# Without this gate a strongly specialised lineup can all choose the same
		# corridor even though the map advertises several viable approaches. SPGs
		# stage on a rear route anchor and therefore do not consume a front-line
		# lane slot.
		if not is_artillery:
			open_routes = []
			for route in routes:
				key = (agent['team'], route.get('id'))
				capacity = max(1, int(route.get('capacity', 1)))
				if int(self.route_usage.get(key, 0)) < capacity:
					open_routes.append(route)
			if open_routes:
				routes = tuple(open_routes)
		personality = agent['personality']
		best = None
		best_score = -1e18
		for route in routes:
			role_weights = route.get('role_weights', {})
			risk = _number(route.get('risk', 0.5), 0.5)
			if is_artillery:
				# Prefer an explicit battery route, then the least exposed early
				# segment. The server chooses the exact graph-validated rear anchor.
				key = (agent['team'], route.get('id'))
				score = (_number(role_weights.get('artillery', 0.0)) *
				         100.0 - risk * 24.0 -
				         int(self.artillery_route_usage.get(key, 0)) *
				         ARTILLERY_ROUTE_REPEAT_PENALTY)
			else:
				score = 0.0
				for role_name, vehicle_score in profile['roles'].items():
					score += (vehicle_score *
					          _number(role_weights.get(role_name, 0.0)) * 18.0)
				class_weights = route.get('class_weights', {}) or {}
				score += (_number(class_weights.get(
					profile.get('class_tag'), 0.0)) *
					CLASS_ROUTE_AFFINITY_WEIGHT)
				score += risk * personality['aggression'] * 16.0
				score -= risk * personality['caution'] * 13.0
				score += personality['initiative'] * risk * 5.0
				score += personality['route_jitter']
				key = (agent['team'], route.get('id'))
				used = int(self.route_usage.get(key, 0))
				capacity = max(1, int(route.get('capacity', 1)))
				score -= (float(used) / float(capacity)) * 28.0
				if used >= capacity:
					score -= 34.0
			if score > best_score:
				best_score = score
				best = route
		if best is not None:
			if is_artillery:
				key = (agent['team'], best.get('id'))
				self.artillery_route_usage[key] = int(
					self.artillery_route_usage.get(key, 0)) + 1
			else:
				key = (agent['team'], best.get('id'))
				self.route_usage[key] = int(
					self.route_usage.get(key, 0)) + 1
			if self._routes_are_baked:
				result = dict(best)
				result['role_weights'] = dict(
					best.get('role_weights', {}) or {})
				if 'class_weights' in best:
					result['class_weights'] = dict(
						best.get('class_weights', {}) or {})
				result['waypoints'] = tuple(
					tuple(point) for point in
					best.get('waypoints', ()) or ())
				return result
			return route_toward_enemy(best, agent['team'], self.bases)
		return None

	def update_contact(self, observing_team, target_id, target_team, position,
	                   health, max_health, class_tag, visible, now,
	                   armor=0.0, speed=0.0, shootable_by_ids=None):
		observing_team = int(observing_team)
		if observing_team == int(target_team):
			return
		team_contacts = self.contacts.setdefault(observing_team, {})
		contact = team_contacts.get(target_id)
		if visible:
			team_contacts[target_id] = {
				'id': target_id,
				'team': int(target_team),
				'position': tuple(position),
				'health': max(0.0, _number(health, 1.0)),
				'max_health': max(1.0, _number(max_health, 1.0)),
				'class_tag': str(class_tag or 'mediumTank'),
				'armor': max(0.0, _number(armor, 0.0)),
				'speed': max(0.0, _number(speed, 0.0)),
				'visible': True,
				'last_seen': _number(now),
				'shootable_by_ids': (tuple(int(value) for value in shootable_by_ids)
				                     if shootable_by_ids is not None else None),
			}
		elif contact is not None:
			contact['visible'] = False
			contact['shootable_by_ids'] = ()

	def _known_contacts(self, team, now):
		known = []
		stale = []
		for target_id, contact in self.contacts.get(int(team), {}).items():
			age = _number(now) - _number(contact.get('last_seen'))
			if age > CONTACT_MEMORY_SECONDS or contact.get('health', 0.0) <= 0.0:
				stale.append(target_id)
			else:
				known.append(contact)
		for target_id in stale:
			try:
				del self.contacts[int(team)][target_id]
			except Exception:
				pass
		return known

	def _focus_count(self, team, target_id):
		count = 0
		for agent in self.agents.values():
			if agent.get('team') == team and agent.get('target_id') == target_id:
				count += 1
		return count

	def _desired_focus(self, contact):
		"""Reserve extra guns for durable threats without dog-piling wrecks."""
		remaining = max(0.0, _number(contact.get('health', 0.0), 0.0))
		count = 1
		if remaining >= 900.0 or contact.get('class_tag') in ('heavyTank', 'AT-SPG'):
			count = 2
		if remaining >= 1800.0:
			count = 3
		return count

	def _local_force_balance(self, agent, position, target_position, now):
		allies = 1
		for other in self.agents.values():
			if other.get('id') == agent.get('id') or other.get('team') != agent.get('team'):
				continue
			other_position = other.get('position')
			if (other_position is not None and
			        _distance_2d(other_position, position) <= LOCAL_FORCE_RADIUS):
				allies += max(0.25, _number(other.get('health_fraction', 1.0), 1.0))
		enemies = 0.0
		for contact in self._known_contacts(agent['team'], now):
			if _distance_2d(contact['position'], target_position) <= LOCAL_FORCE_RADIUS:
				enemies += max(0.3, contact['health'] / max(contact['max_health'], 1.0))
		return allies - enemies

	def _flank_position(self, agent, position, target_position):
		"""Return a deterministic lateral pressure point around the target."""
		dx = position[0] - target_position[0]
		dz = position[2] - target_position[2]
		length = math.sqrt(dx * dx + dz * dz)
		if length < 0.1:
			return tuple(position)
		dx /= length
		dz /= length
		side = -1.0 if (agent['seed'] & 1) else 1.0
		forward = agent['profile']['desired_range'] * 0.72
		lateral = min(95.0, agent['profile']['desired_range'] * 0.38)
		return (target_position[0] + dx * forward + dz * lateral * side,
		        position[1],
		        target_position[2] + dz * forward - dx * lateral * side)

	def _choose_contact(self, agent, position, hull_yaw, now):
		contacts = self._known_contacts(agent['team'], now)
		if not contacts:
			agent['target_id'] = None
			return None
		profile = agent['profile']
		personality = agent['personality']
		best = None
		best_score = -1e18
		for contact in contacts:
			distance = _distance_2d(position, contact['position'])
			age = max(0.0, _number(now) - _number(contact.get('last_seen')))
			visible = bool(contact.get('visible'))
			shootable_by_ids = contact.get('shootable_by_ids')
			if (visible and shootable_by_ids is not None and
					int(agent['id']) not in shootable_by_ids):
				continue
			focus = self._focus_count(agent['team'], contact['id'])
			desired_focus = self._desired_focus(contact)
			if (focus >= desired_focus and contact['id'] != agent.get('target_id')):
				continue
			roles = profile.get('roles', {})
			mobility = max(_number(roles.get('scout')), _number(roles.get('flanker')))
			engagement_range = max(340.0, min(
				560.0, profile['desired_range'] * 2.0 + mobility * 300.0))
			if distance > engagement_range:
				continue
			health_fraction = contact['health'] / max(contact['max_health'], 1.0)
			dx = contact['position'][0] - position[0]
			dz = contact['position'][2] - position[2]
			bearing = math.atan2(dx, dz)
			turn_cost = abs(_angle_delta(bearing, hull_yaw)) / math.pi
			range_error = abs(distance - profile['desired_range']) / max(profile['desired_range'], 50.0)
			score = 90.0 if visible else max(4.0, 42.0 - age * 6.0)
			score += (1.0 - health_fraction) * 38.0
			score -= range_error * (14.0 - personality['aggression'] * 5.0)
			score -= turn_cost * 12.0
			if focus < desired_focus:
				score += focus * personality['teamwork'] * 4.0
			else:
				score -= (focus - desired_focus + 1) * (
					10.0 + (1.0 - personality['teamwork']) * 7.0)
			if contact['id'] == agent.get('target_id'):
				score += TARGET_HYSTERESIS_BONUS
			if contact.get('class_tag') in ('lightTank', 'SPG'):
				score += 4.0 * personality['initiative']
			if visible and contact.get('class_tag') == 'SPG':
				# A discovered battery is a high-value target, but the normal
				# shootability and focus reservations above still prevent blind
				# pursuit or the whole team dog-piling one artillery vehicle.
				score += DISCOVERED_ARTILLERY_PRIORITY_BONUS
			if score > best_score:
				best_score = score
				best = contact
		agent['target_id'] = best.get('id') if best is not None else None
		return best

	def _route_position(self, agent, position, now, hull_yaw=None):
		route = agent.get('route')
		if route is None:
			enemy_base = self.bases.get(2 if agent['team'] == 1 else 1)
			if enemy_base is None:
				return tuple(position)
			return (enemy_base[0], position[1], enemy_base[1])
		waypoints = route.get('waypoints', ())
		if not waypoints:
			return tuple(position)
		if not agent.get('route_started', False):
			nearest = min(range(len(waypoints)), key=lambda value:
				_distance_2d(position, (float(waypoints[value][0]),
					position[1], float(waypoints[value][1]))))
			# Point zero is the own flag. The formation is deployed in front of
			# it, so entering battle must continue to the first tactical point.
			if nearest == 0 and len(waypoints) > 1:
				nearest = 1
			# Baked routes may contain one or two short connectors around the flag.
			# A formation slot can already be beyond them, making the nearest connector
			# sit behind the hull. Let A* join the first meaningful route anchor.
			while (nearest + 1 < len(waypoints) and
					_distance_2d(position, (float(waypoints[nearest][0]),
					position[1], float(waypoints[nearest][1]))) < 30.0):
				nearest += 1
			# A deployed formation can be closer to a connector that it has already
			# passed than to the next lane point. Joining that connector makes every
			# hull turn back toward its own flag before it may leave spawn. Only skip
			# a genuinely rear-facing point and preserve lateral lane openings.
			if hull_yaw is not None:
				while nearest + 1 < len(waypoints):
					waypoint = waypoints[nearest]
					bearing = math.atan2(
						float(waypoint[0]) - float(position[0]),
						float(waypoint[1]) - float(position[2]))
					if abs(_angle_delta(bearing, float(hull_yaw))) <= 1.75:
						break
					nearest += 1
			agent['waypoint_index'] = nearest
			agent['route_started'] = True
			agent['route_join_anchor'] = tuple(position)
			agent['route_join_index'] = nearest
		index = min(int(agent.get('waypoint_index', 0)), len(waypoints) - 1)
		waypoint = waypoints[index]
		world = (float(waypoint[0]), float(position[1]), float(waypoint[1]))
		if _distance_2d(position, world) <= 13.0:
			if index + 1 < len(waypoints):
				agent['waypoint_index'] = index + 1
				waypoint = waypoints[index + 1]
				world = (float(waypoint[0]), float(position[1]), float(waypoint[1]))
		return world

	def _fallback_position(self, agent, position):
		"""Return the previous route anchor, usually behind the current corner."""
		route = agent.get('route')
		waypoints = route.get('waypoints', ()) if route is not None else ()
		if not waypoints:
			return tuple(position)
		index = max(0, min(int(agent.get('waypoint_index', 0)) - 1,
		                   len(waypoints) - 1))
		waypoint = waypoints[index]
		return (float(waypoint[0]), float(position[1]), float(waypoint[1]))

	def _route_anchor(self, agent, position):
		"""Return the strategic anchor immediately before the current waypoint."""
		route = agent.get('route')
		waypoints = route.get('waypoints', ()) if route is not None else ()
		if not waypoints:
			return tuple(position)
		index = max(0, min(int(agent.get('waypoint_index', 0)),
		                   len(waypoints) - 1))
		if int(agent.get('route_join_index', -1)) == index:
			anchor = agent.get('route_join_anchor')
			if anchor is not None:
				return tuple(anchor)
		index = max(0, min(index - 1,
		                   len(waypoints) - 1))
		waypoint = waypoints[index]
		return (float(waypoint[0]), float(position[1]), float(waypoint[1]))

	def _angled_face_position(self, agent, position, target_position):
		"""Give armoured turreted tanks a stable 12-30 degree hull angle."""
		angle_degrees = self._hull_angle_degrees(agent)
		if angle_degrees is None:
			return target_position
		dx = target_position[0] - position[0]
		dz = target_position[2] - position[2]
		length = math.sqrt(dx * dx + dz * dz)
		if length < 0.1:
			return target_position
		angle = math.radians(angle_degrees)
		cosine = math.cos(angle)
		sine = math.sin(angle)
		angled_x = dx * cosine - dz * sine
		angled_z = dx * sine + dz * cosine
		return (position[0] + angled_x, target_position[1],
		        position[2] + angled_z)

	def _hull_angle_degrees(self, agent):
		profile = agent['profile']
		roles = profile.get('roles', {}) or {}
		if profile['class_tag'] in ('AT-SPG', 'SPG'):
			return None
		if profile['dominant_role'] not in ('brawler', 'support'):
			return None
		if (_number(profile.get('armor')) < 60.0 and
				_number(roles.get('brawler')) < 0.55):
			return None
		angle = 12.0 + agent['personality']['caution'] * 18.0
		return -angle if (agent['seed'] & 1) == 0 else angle

	def order_for(self, bot_id, position, hull_yaw, health, max_health, now):
		agent = self.agents[int(bot_id)]
		agent['position'] = tuple(position)
		agent['health_fraction'] = (
			_number(health, 1.0) / max(_number(max_health, 1.0), 1.0))
		contact = self._choose_contact(agent, position, hull_yaw, now)
		route_position = self._route_position(agent, position, now, hull_yaw)
		profile = agent['profile']
		personality = agent['personality']
		order = {
			'target_id': None,
			'aim_position': route_position,
			'face_position': route_position,
			'move_position': route_position,
			'fire_allowed': False,
			'combat_mode': 'route',
			'throttle_override': None,
			'desired_range': profile['desired_range'],
			'fire_range': profile['fire_range'],
			'route_id': agent['route'].get('id') if agent.get('route') else 'direct',
			'route_index': int(agent.get('waypoint_index', 0)),
			'route_anchor': self._route_anchor(agent, position),
			'route_join': (
				int(agent.get('route_join_index', -1)) ==
				int(agent.get('waypoint_index', 0))),
			'personality': personality,
			'profile': profile,
			'shell_index': 0,
			'force_balance': 0.0,
		}
		if contact is not None:
			distance = _distance_2d(position, contact['position'])
			contact['distance'] = distance
			force_balance = self._local_force_balance(
				agent, position, contact['position'], now)
			order['force_balance'] = force_balance
			order['target_id'] = contact['id']
			order['aim_position'] = contact['position']
			order['face_position'] = self._angled_face_position(
				agent, position, contact['position'])
			hull_angle = self._hull_angle_degrees(agent)
			if hull_angle is not None:
				order['hull_angle_degrees'] = hull_angle
			order['fire_allowed'] = bool(contact.get('visible'))
			order['shell_index'] = select_shell_index(profile, contact, personality)
			if contact.get('visible'):
				order['combat_mode'] = 'engage'
				close_ratio = 0.52 + personality['aggression'] * 0.12
				far_ratio = 1.02 + personality['caution'] * 0.28
				if (force_balance < -0.65 and
				        personality['caution'] + 0.18 > personality['aggression'] and
				        profile['dominant_role'] != 'brawler'):
					order['move_position'] = self._fallback_position(agent, position)
					order['combat_mode'] = 'withdraw'
				elif distance > profile['desired_range'] * far_ratio:
					order['move_position'] = contact['position']
					order['combat_mode'] = 'advance_contact'
				elif distance < profile['desired_range'] * close_ratio:
					# Use the route as a known-safe fallback instead of reversing into
					# arbitrary geometry. Brawlers with high aggression are less eager.
					if profile['dominant_role'] != 'brawler' or personality['caution'] > 0.62:
						order['move_position'] = self._fallback_position(agent, position)
						order['combat_mode'] = 'withdraw'
					else:
						order['move_position'] = tuple(position)
				elif (profile['roles'].get('flanker', 0.0) >= 0.68 and
				      distance < profile['fire_range'] * 1.15 and
				      force_balance >= -0.35 and personality['initiative'] > 0.38):
					order['move_position'] = self._flank_position(
						agent, position, contact['position'])
					order['combat_mode'] = 'flank'
				else:
					order['move_position'] = tuple(position)
				# Do not manufacture a periodic forward/back manoeuvre in open
				# ground. Peeking belongs to the geometry-backed cover adapter;
				# without confirmed cover this order holds, aims and fires.
			else:
				# Last-known positions inform movement but never authorize a shot.
				order['combat_mode'] = 'investigate'
				if personality['initiative'] + personality['aggression'] > 1.05:
					order['move_position'] = contact['position']
		current_fraction = _number(health, 1.0) / max(_number(max_health, 1.0), 1.0)
		if current_fraction < 0.20 + personality['caution'] * 0.18:
			order['move_position'] = self._fallback_position(agent, position)
			order['throttle_override'] = None
			order['combat_mode'] = 'withdraw'
		agent['last_order'] = order
		return order


def route_summary(director, bot_id):
	agent = director.agents.get(int(bot_id))
	if agent is None:
		return 'unregistered'
	profile = agent['profile']
	personality = agent['personality']
	route = agent.get('route')
	return ('route=%s role=%s aggression=%.2f caution=%.2f teamwork=%.2f '
	        'patience=%.2f initiative=%.2f jiggle=%.2f' % (
			route.get('id') if route else 'direct', profile['dominant_role'],
			personality['aggression'], personality['caution'],
			personality['teamwork'], personality['patience'],
			personality['initiative'], personality['jiggle']))
