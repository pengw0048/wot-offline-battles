# -*- coding: utf-8 -*-
import time
import utils
import cPickle
from debug_utils import LOG_DEBUG, LOG_CURRENT_EXCEPTION

_g_destr_authority = None


def _offh_native_mode_enabled():
	"""Keep this experimental build on one native-only movement contract."""
	generation = int(globals().get('g_offh_battle_gen', 0) or 0)
	latch = globals().get('g_offh_native_mode_latch')
	if (isinstance(latch, tuple) and len(latch) == 2 and
			int(latch[0]) == generation):
		return True
	# Older user configs may still contain the former opt-out. Ignoring it is
	# deliberate: this package must expose a native failure instead of silently
	# selecting a second Python movement implementation.
	globals()['g_offh_native_mode_latch'] = (generation, True)
	return True


def _offh_network_bot_role(player):
	"""Return a fail-closed role for shared bot simulation ownership."""
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		network_enabled = bool(CONFIG_OPTIONS.get('network_mode', False))
	except Exception:
		network_enabled = getattr(
			player, '_offhangar_network_client', None) is not None
	if (not network_enabled or bool(getattr(
			player, '_offhangar_network_fallback_local', False))):
		return 'local'
	client = getattr(player, '_offhangar_network_client', None)
	if (client is None or not getattr(client, 'running', False) or
			not getattr(client, 'connected', False) or
			not getattr(client, 'ready', False) or
			getattr(client, 'phase', None) != 'battle'):
		return 'unknown'
	if bool(getattr(
			player, '_offhangar_network_authority_demotion_pending', False)):
		return 'unknown'
	try:
		authority_id = int(client.bot_authority_id)
		player_id = int(client.player_id)
	except Exception:
		return 'unknown'
	if authority_id != player_id:
		return 'replica'
	if bool(getattr(
			player, '_offhangar_network_authority_handoff_pending', False)):
		return 'handoff'
	return 'authority'


def _offh_native_eligible(mock):
	return (mock is not None and not (
		bool(getattr(mock, '_network_remote', False)) and
		not bool(getattr(mock, '_network_shared_bot', False))))


def _offh_native_movement_required(player, mock, role=None):
	"""Latch strict native movement only after this process owns the bot."""
	if mock is None:
		return False
	if bool(getattr(mock, '_offh_native_movement_required', False)):
		return True
	if not _offh_native_mode_enabled() or not _offh_native_eligible(mock):
		return False
	if role is None:
		role = _offh_network_bot_role(player)
	if role not in ('local', 'authority'):
		return False
	position = getattr(mock, 'position', None)
	try:
		fail_pose = (
			float(position.x), float(position.y), float(position.z),
			float(getattr(mock, 'yaw', 0.0) or 0.0),
			float(getattr(mock, 'pitch', 0.0) or 0.0),
			float(getattr(mock, 'roll', 0.0) or 0.0))
		setattr(mock, '_offh_native_fail_pose', fail_pose)
	except Exception:
		fail_pose = None
	# A relay proxy may have been created before this client was promoted. Remove
	# its legacy static shape before attaching the native rigid body.
	try:
		mock._collision_obstacle = None
	except Exception:
		pass
	setattr(mock, '_offh_native_movement_required', True)
	return True


def _offh_python_movement_allowed(player, mock, role=None):
	"""The native experiment never authorizes legacy bot kinematics."""
	return False


def release_native_bots_for_replica(player):
	"""Synchronously release native owners before one demotion snapshot applies."""
	try:
		from gui.mods.offhangar import native_bot_physics as manager
	except Exception:
		return False
	try:
		mocks = globals().get('G_MOCK_VEHICLES', {}) or {}
		# Release every claimed body before refreshing any relay presentation.
		# A partial pass must not re-fashion early bots and then fail on a later
		# native owner while the authority role is still unchanged.
		targets = list(mocks.values())
		try:
			targets.sort(key=lambda item: int(getattr(item, 'id', 0) or 0))
		except Exception:
			pass
		for mock in targets:
			if not getattr(mock, '_network_shared_bot', False):
				continue
			claimed = bool(manager.claims_movement(mock))
			if claimed and manager.is_prepared(mock):
				mock._offh_native_replica_refresh_pending = True
			if claimed and not manager.stop_mock(mock, True):
				return False
		for mock in targets:
			if not bool(getattr(
					mock, '_offh_native_replica_refresh_pending', False)):
				continue
			refresh_fashion = getattr(mock, '_offh_attach_bot_fashion', None)
			if callable(refresh_fashion):
				refresh_fashion()
			mock._offh_native_replica_refresh_pending = False
		# The expanded range belongs to the simulation authority. Restore it only
		# after every native owner has been released; a partial demotion must retain
		# collision for the bodies that are still authoritative on this client.
		streaming_bootstrap = getattr(
			player, '_offh_spawn_streaming_bootstrap', None)
		if (streaming_bootstrap is not None and
				streaming_bootstrap.stop() is not True):
			return False
		player._offh_spawn_streaming_bootstrap = None
		player._offh_spawn_streaming_monitor_active = False
		player._offhangar_native_replica_released = True
		return True
	except Exception as error:
		try:
			from gui.mods.offhangar.logging import LOG_ERROR as _release_error
			_release_error('NATIVE_BOT_PHYSICS demotion failed: %s' % str(error))
		except Exception:
			pass
		return False


def _offh_release_native_for_wreck(mock):
	"""Prove a dead bot released its native owner before replacing its model."""
	if mock is None:
		return True
	required = bool(getattr(mock, '_offh_native_movement_required', False))
	try:
		from gui.mods.offhangar import native_bot_physics as manager
	except Exception:
		return not required
	try:
		if not manager.claims_movement(mock):
			return True
		return bool(manager.stop_mock(mock, False))
	except Exception:
		return False


def _offh_wreck_release_or_retry(mock, callback):
	"""Retry a native owner release without ever attaching a second wreck root."""
	if _offh_release_native_for_wreck(mock):
		try:
			mock._offh_wreck_release_attempts = 0
		except Exception:
			pass
		return True
	attempts = int(getattr(
		mock, '_offh_wreck_release_attempts', 0) or 0) + 1
	try:
		mock._offh_wreck_release_attempts = attempts
	except Exception:
		pass
	if attempts < 20:
		_offh_battle_callback(0.1, callback)
	elif not bool(getattr(mock, '_offh_wreck_release_error_logged', False)):
		try:
			mock._offh_wreck_release_error_logged = True
		except Exception:
			pass
		try:
			from gui.mods.offhangar.logging import LOG_ERROR as _wreck_error
			_wreck_error('NATIVE_BOT_PHYSICS wreck swap blocked id=%s '
				'reason=native owner release failed' % (
					getattr(mock, 'id', '?'),))
		except Exception:
			pass
	return False


def _stop_dead_native_bot(mock, restore_filter=False):
	"""Compatibility name for the central kill ownership barrier."""
	return _offh_release_native_for_wreck(mock)


def _offh_native_failed_pose(mock):
	"""Return an immobile current pose when the native manager is unavailable."""
	pose = getattr(mock, '_offh_native_fail_pose', None)
	if isinstance(pose, tuple) and len(pose) >= 6:
		return {
			'position': pose[:3], 'yaw': pose[3], 'pitch': pose[4],
			'roll': pose[5], 'velocity': 0.0, 'turn_velocity': 0.0,
			'failed': True,
		}
	position = getattr(mock, 'position', None)
	try:
		point = (float(position.x), float(position.y), float(position.z))
	except Exception:
		return None
	return {
		'position': point,
		'yaw': float(getattr(mock, 'yaw', 0.0) or 0.0),
		'pitch': float(getattr(mock, 'pitch', 0.0) or 0.0),
		'roll': float(getattr(mock, 'roll', 0.0) or 0.0),
		'velocity': 0.0,
		'turn_velocity': 0.0,
		'failed': True,
	}

# Temporary low-overhead battle profiler.  It samples one render callback in
# four, then writes one aggregate NOTE every five seconds.  Sampling matters on
# this Python 2.6 client: timing every tiny operation would become part of the
# performance problem we are trying to measure.
_OFFH_PERF_SAMPLE_EVERY = 4
_OFFH_PERF_REPORT_SECONDS = 5.0
_OFFH_AI_ORDER_REFRESHES_PER_FRAME = 10
_OFFH_AI_NAV_REFRESHES_PER_FRAME = 6
_OFFH_AI_DRIVER_REFRESHES_PER_FRAME = 6
_OFFH_AI_TREE_REFRESHES_PER_FRAME = 6
_OFFH_AI_CONTACT_TARGETS_PER_FRAME = 2
_OFFH_AI_COVER_CANDIDATES_PER_FRAME = 1
_OFFH_AI_ARTILLERY_CHORDS_PER_FRAME = 4
_OFFH_AI_CONTACT_FULL_INTERVAL = 3.0
_OFFH_AI_DIAGNOSTICS_INTERVAL = 3.0
_OFFH_AI_COVER_OFFSETS = (
	(0.0, 0.0), (14.0, 0.0),
	(10.0, 13.0), (10.0, -13.0),
)


def _offh_ai_cache_deadline(now, entity_id, interval, salt=0, stagger=False):
	"""Spread the first expiry, then preserve the legacy per-update interval."""
	interval = max(0.001, float(interval))
	deadline = float(now) + interval
	if not stagger:
		return deadline
	# LAN battles contain at most 29 bots. A prime multiplier distributes adjacent
	# entity ids over all slots instead of making the whole line-up expire together.
	# Only the initial cache receives this phase; adding it after every update would
	# lower each bot's decision frequency.
	phase = (((abs(int(entity_id)) * 17 + int(salt) * 11) % 29) /
	         29.0) * interval
	return deadline + phase


def _offh_ai_refresh_due(selected, cache_matches, cache_fresh,
		deadline, now, horizon):
	"""Hard-cap cache refreshes, including cold starts and changed keys."""
	if not selected:
		return False
	if not cache_matches:
		return True
	return (not cache_fresh or
	        float(deadline) - float(now) <= float(horizon))


def _offh_ai_budget_from_ordered(ordered, frame_index, quota, salt=0):
	"""Select from one already-normalised entity-id sequence."""
	count = len(ordered)
	quota = max(0, int(quota or 0))
	if not count or not quota:
		return set()
	if quota >= count:
		return set(ordered)
	start = (int(frame_index) * quota + int(salt)) % count
	return set(ordered[(start + offset) % count] for offset in range(quota))


def _offh_ai_budget_ids(entity_ids, frame_index, quota, salt=0):
	"""Select one deterministic round-robin slice without starving any bot."""
	ordered = sorted(set(int(value) for value in (entity_ids or ())))
	return _offh_ai_budget_from_ordered(ordered, frame_index, quota, salt)


def _offh_ai_frame_budget_plan(entity_ids, frame_dt=(1.0 / 30.0)):
	"""Bound expensive decisions per rendered frame, independent of wall time.

	Wall-clock-only TTLs collapse when FPS drops: every cache expires before the
	next frame and all 29 bots refresh together.  This plan preserves the desired
	per-bot cadence at healthy FPS while guaranteeing a finite recovery workload
	on a slow frame. Physics and canonical pose commits are deliberately not
	budgeted here: every live local bot must move continuously on every render
	callback.
	"""
	generation = int(globals().get('g_offh_battle_gen', 0) or 0)
	state = globals().get('g_offh_ai_frame_budget')
	if state is None or int(state.get('generation', -1)) != generation:
		state = {'generation': generation, 'frame': -1}
		globals()['g_offh_ai_frame_budget'] = state
	state['frame'] = int(state.get('frame', -1)) + 1
	frame_index = state['frame']
	# Normalise once.  This function used to sort and allocate the same 29-id set
	# four times per render frame, despite every budget using identical members.
	ordered = sorted(set(int(value) for value in (entity_ids or ())))
	count = len(ordered)
	frame_dt = max(1.0 / 120.0, min(0.25, float(frame_dt or 0.0)))
	def _horizon(quota):
		quota = max(1, int(quota))
		frames = max(1, (count + quota - 1) // quota)
		return frame_dt * frames
	return {
		'order': _offh_ai_budget_from_ordered(
			ordered, frame_index, _OFFH_AI_ORDER_REFRESHES_PER_FRAME, 0),
		'nav': _offh_ai_budget_from_ordered(
			ordered, frame_index, _OFFH_AI_NAV_REFRESHES_PER_FRAME, 11),
		'driver': _offh_ai_budget_from_ordered(
			ordered, frame_index, _OFFH_AI_DRIVER_REFRESHES_PER_FRAME, 23),
		'tree': _offh_ai_budget_from_ordered(
			ordered, frame_index, _OFFH_AI_TREE_REFRESHES_PER_FRAME, 7),
		'order_horizon': _horizon(_OFFH_AI_ORDER_REFRESHES_PER_FRAME),
		'nav_horizon': _horizon(_OFFH_AI_NAV_REFRESHES_PER_FRAME),
		'driver_horizon': _horizon(_OFFH_AI_DRIVER_REFRESHES_PER_FRAME),
	}


def _offh_perf_clock():
	try:
		return time.clock()
	except Exception:
		try:
			return time.perf_counter()
		except Exception:
			return time.time()


def _offh_perf_state():
	state = globals().get('g_offh_perf_state')
	generation = int(globals().get('g_offh_battle_gen', 0) or 0)
	if state is None or int(state.get('generation', -1)) != generation:
		state = {
			'generation': generation,
			'wall_start': time.time(),
			'frames': 0,
			'frame_seconds': 0.0,
			'sample_frames': 0,
			'active': False,
			'times': {},
			'calls': {},
		}
		globals()['g_offh_perf_state'] = state
	return state


def _offh_perf_frame_begin(bot_count):
	state = _offh_perf_state()
	state['frames'] += 1
	state['bot_count'] = int(bot_count or 0)
	state['active'] = (state['frames'] % _OFFH_PERF_SAMPLE_EVERY) == 0
	if not state['active']:
		return None
	state['sample_frames'] += 1
	return _offh_perf_clock()


def _offh_perf_start():
	state = globals().get('g_offh_perf_state')
	if state is None or not state.get('active', False):
		return None
	return _offh_perf_clock()


def _offh_perf_stop(name, started, calls=1):
	if started is None:
		return
	state = globals().get('g_offh_perf_state')
	if state is None or not state.get('active', False):
		return
	elapsed = max(0.0, _offh_perf_clock() - started)
	times = state['times']
	counts = state['calls']
	times[name] = float(times.get(name, 0.0) or 0.0) + elapsed
	counts[name] = int(counts.get(name, 0) or 0) + int(calls or 0)


def _offh_perf_count(name, calls=1):
	"""Count sampled work items without adding another high-resolution clock."""
	state = globals().get('g_offh_perf_state')
	if state is None or not state.get('active', False):
		return
	counts = state['calls']
	counts[name] = int(counts.get(name, 0) or 0) + int(calls or 0)


def _offh_perf_call(name, callback, *args):
	started = _offh_perf_start()
	try:
		return callback(*args)
	finally:
		_offh_perf_stop(name, started)


def _offh_perf_role(player):
	try:
		client = getattr(player, '_offhangar_network_client', None)
		if client is None or not getattr(client, 'ready', False):
			return 'offline'
		from gui.mods.offhangar.network_battle import network_is_authority
		return 'authority' if network_is_authority(player) else 'replica'
	except Exception:
		return 'unknown'


def _offh_perf_frame_end(started, frame_dt, player):
	state = _offh_perf_state()
	try:
		state['frame_seconds'] += max(0.0, min(float(frame_dt), 0.5))
	except Exception:
		pass
	_offh_perf_stop('callback', started)
	state['active'] = False
	now = time.time()
	wall = max(0.001, now - float(state.get('wall_start', now)))
	if wall < _OFFH_PERF_REPORT_SECONDS:
		return
	samples = max(1, int(state.get('sample_frames', 0) or 0))
	frames = max(1, int(state.get('frames', 0) or 0))
	frame_ms = 1000.0 * float(state.get('frame_seconds', 0.0) or 0.0) / frames
	times = state.get('times', {}) or {}
	calls = state.get('calls', {}) or {}
	callback_ms = 1000.0 * float(times.get('callback', 0.0) or 0.0) / samples
	callback_share = 100.0 * callback_ms / max(0.1, frame_ms)
	ordered = ('player_loop', 'player_setup', 'player_physics', 'player_aim',
	           'player_pose', 'player_gun', 'player_gun_marker',
	           'marker_vehicle_candidates', 'player_effects',
	           'network_smoothing', 'ai_setup', 'contacts',
	           'contact_build', 'contact_targets', 'contact_foliage', 'contact_cover',
	           'contact_payload', 'contact_diagnostics', 'contact_publish',
	           'artillery_arc', 'artillery_rays',
	           'nav_tick', 'ai_order', 'order_refresh',
		           'order_deferred', 'nav_server', 'nav_target', 'nav_refresh', 'nav_deferred',
	           'bot_loop',
	           'driver', 'driver_refresh', 'driver_deferred',
		           'direction', 'direction_baked', 'direction_exact', 'physics',
		           'physics_state', 'native_simulation', 'native_physics',
		           'physics_motion', 'physics_ground',
	           'physics_safety', 'physics_rays',
	           'drive_pitch_reuse', 'drive_pitch_exact', 'tilt_support_reuse',
	           'bot_effects', 'kinematics', 'bot_audio',
	           'nav_paused', 'tactic_route', 'tactic_hold', 'tactic_manoeuvre',
	           'driver_drive', 'driver_avoid', 'driver_wait',
	           'driver_traffic_wait', 'driver_recovery',
	           'driver_arrived',
	           'traffic_snapshot', 'traffic_index', 'traffic_candidates',
	           'pose_water', 'terrain_support', 'terrain_tilt',
	           'tree_scan', 'tree_deferred',
	           'wall_collision', 'wall_fast', 'wall_exact',
	           'tank_collision', 'tank_collision_empty', 'collision_candidates',
	           'player_collision_error',
	           'pose_commit', 'visibility', 'los', 'network_publish', 'post_bot')
	parts = []
	for name in ordered:
		elapsed = float(times.get(name, 0.0) or 0.0)
		count = int(calls.get(name, 0) or 0)
		if elapsed <= 0.0 and count <= 0:
			continue
		parts.append('%s=%.2fms/%.1fc' % (
			name, 1000.0 * elapsed / samples, float(count) / samples))
	try:
		from gui.mods.offhangar.logging import LOG_NOTE as _perf_log
		_perf_log('PERF window=%.1fs role=%s bots=%d fps=%.1f frame=%.2fms '
		          'callback=%.2fms(%.0f%%) samples=%d %s' % (
			wall, _offh_perf_role(player), int(state.get('bot_count', 0) or 0),
			float(frames) / wall, frame_ms, callback_ms, callback_share,
			samples, ' '.join(parts)))
	except Exception:
		pass
	state['wall_start'] = now
	state['frames'] = 0
	state['frame_seconds'] = 0.0
	state['sample_frames'] = 0
	state['times'] = {}
	state['calls'] = {}


def _offh_record_spawn_timing(player, prepare_seconds, wait_seconds, build_seconds):
	"""Report compact aggregate spawn costs without logging every vehicle."""
	generation = int(globals().get('g_offh_battle_gen', 0) or 0)
	state = globals().get('g_offh_spawn_timing')
	if state is None or int(state.get('generation', -1)) != generation:
		state = {'generation': generation, 'count': 0, 'prepare': 0.0,
			'wait': 0.0, 'build': 0.0, 'max_prepare': 0.0,
			'max_wait': 0.0, 'max_build': 0.0}
		globals()['g_offh_spawn_timing'] = state
	state['count'] += 1
	state['prepare'] += max(0.0, float(prepare_seconds or 0.0))
	state['wait'] += max(0.0, float(wait_seconds or 0.0))
	state['build'] += max(0.0, float(build_seconds or 0.0))
	state['max_prepare'] = max(state['max_prepare'], float(prepare_seconds or 0.0))
	state['max_wait'] = max(state['max_wait'], float(wait_seconds or 0.0))
	state['max_build'] = max(state['max_build'], float(build_seconds or 0.0))
	expected = int(getattr(player, '_offh_auto_spawn_expected', 0) or 0)
	if state['count'] % 5 != 0 and (not expected or state['count'] < expected):
		return
	try:
		from gui.mods.offhangar.logging import LOG_NOTE as _spawn_log
		count = max(1, state['count'])
		_spawn_log('SPAWN PERF ready=%d/%s prepare=%.0fms(avg)/%.0fms(max) '
			'load_wait=%.0fms(avg)/%.0fms(max) '
			'build=%.0fms(avg)/%.0fms(max)' % (
			state['count'], expected or '?', 1000.0 * state['prepare'] / count,
			1000.0 * state['max_prepare'], 1000.0 * state['wait'] / count,
			1000.0 * state['max_wait'], 1000.0 * state['build'] / count,
			1000.0 * state['max_build']))
	except Exception:
		pass


def _offh_vehicle_model_paths(type_descriptor):
	"""Return the four undamaged component paths used by retail 0.8.2."""
	return (
		type_descriptor.chassis['models']['undamaged'],
		type_descriptor.hull['models']['undamaged'],
		type_descriptor.turret['models']['undamaged'],
		type_descriptor.gun['models']['undamaged'],
	)


def _offh_fetch_vehicle_models(type_descriptor, callback):
	"""Fetch one independent model set through the retail client path.

	VehicleAppearance.__fetchModels in the 0.8.2 client calls
	BigWorld.fetchModel once for each component.  loadResourceListBG is useful for
	bulk dependency warm-up, but calling it again per vehicle makes this DX9
	client clone warm models on the game thread and can stall for seconds.
	"""
	import BigWorld
	paths = _offh_vehicle_model_paths(type_descriptor)
	refs = {}
	remaining = [len(paths)]
	finished = [False]

	def _component_ready(path, model):
		if finished[0]:
			return
		refs[path] = model
		remaining[0] -= 1
		if remaining[0] <= 0:
			finished[0] = True
			callback(refs)

	for path in paths:
		try:
			BigWorld.fetchModel(path,
				lambda model, _path=path: _component_ready(_path, model))
		except Exception:
			# Complete the aggregate callback even when one resource is broken. The
			# normal spawn unpacker then reports the exact failed vehicle instead of
			# leaving the whole lineup permanently pending.
			_component_ready(path, None)

def _get_destr_authority():
	"""offhangar.destructibles_authority, with the same execfile fallback
	the package bootstrap uses (the module ships without a .pyc)."""
	global _g_destr_authority
	if _g_destr_authority is not None:
		return _g_destr_authority
	try:
		from gui.mods.offhangar import destructibles_authority as _da
		_g_destr_authority = _da
		return _da
	except Exception:
		pass
	import sys, os, types
	full_name = 'gui.mods.offhangar.destructibles_authority'
	if full_name in sys.modules:
		_g_destr_authority = sys.modules[full_name]
		return _g_destr_authority
	candidates = []
	try:
		candidates.append(os.path.dirname(os.path.abspath(__file__)))
	except Exception:
		pass
	candidates.append(os.path.join('res_mods', '0.8.2', 'scripts', 'client', 'gui', 'mods', 'offhangar'))
	for _dir in candidates:
		py_path = os.path.join(_dir, 'destructibles_authority.py')
		if os.path.exists(py_path):
			mod = types.ModuleType(full_name)
			mod.__file__ = py_path
			sys.modules[full_name] = mod
			try:
				execfile(py_path, mod.__dict__)
			except Exception:
				del sys.modules[full_name]
				raise
			_g_destr_authority = mod
			return mod
	raise ImportError('destructibles_authority not found')

g_offline_models = []
g_offline_enemies = []
def _add_model(m):
	global g_offline_models
	g_offline_models.append(m)
	import BigWorld
	BigWorld.addModel(m)


def _offh_assign_entity_model_root(entity, model, handoff=None):
	"""Mirror VehicleAppearance's single root-motor handoff."""
	if handoff is None:
		handoff = {}
	try:
		if not bool(handoff.get('assigned', False)):
			entity.model = model
			if getattr(entity, 'model', None) is not model:
				return False
			handoff['assigned'] = True
			motors = list(model.motors)
			try:
				handoff['default_motor'] = motors[0]
			except IndexError:
				handoff['complete'] = True
				return True
		default_motor = handoff.get('default_motor')
		if default_motor is None:
			return bool(handoff.get('complete', False))
		motors = list(model.motors)
		if not any(motor is default_motor for motor in motors):
			if motors:
				return False
			handoff['default_motor'] = None
			handoff['complete'] = True
			return True
		model.delMotor(default_motor)
		motors = list(model.motors)
		if (any(motor is default_motor for motor in motors) or motors):
			return False
		handoff['default_motor'] = None
		handoff['complete'] = True
		return True
	except Exception:
		return False


def _offh_cursor_shown():
	'''True while a modal GUI (ESC menu) owns the mouse: its clicks must not
	drive in-battle actions such as the post-mortem vehicle cycle.'''
	try:
		import GUI
		return bool(GUI.mcursor().visible)
	except Exception:
		return False


def _offh_is_ally(mock):
	'''True when this mock shares the player's team. Friendly fire IS possible
	(the shot loop does not filter by team), and sound_notifications.xml carries
	separate ally_* events - reporting a team-mate as an enemy kill is wrong.'''
	try:
		import BigWorld
		_pt = getattr(BigWorld.player(), '_offhangar_team', 1)
		_t = getattr(mock, '_bot_team', None)
		if _t is None:
			_pi = getattr(mock, 'publicInfo', None)
			_t = _pi.get('team', 2) if _pi else 2
		return _t == _pt
	except Exception:
		return False


def _offh_resolve_hull_hit(shot, dist_m, all_hits):
	'''Find the first STRUCTURAL plate behind any spaced armour.

	Returns (result, eff_armor, pierce, spaced_mm, angle_cos) where result is the
	_offh_penetration verdict for that plate, or None when the round never reaches
	structure - i.e. the track absorbed it.

	Tracks and external devices carry vehicleDamageFactor 0.0: they are not the
	hull, so they must not take hull damage. What they DO is cost penetration on
	the way through, which is why a shot that clips the track at a shallow angle
	(long path, thick effective plate) is swallowed while a square-on hit carries
	into the hull behind it.

	HEAT is a special case, as in the game: the shaped charge detonates on the
	first spaced plate it touches and the jet does not survive the standoff, so a
	track absorbs it outright regardless of the angle.'''
	import math
	if not all_hits:
		return None
	shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
	kind = shell.get('kind', 'ARMOR_PIERCING')
	spaced = 0.0
	try:
		_ordered = sorted(all_hits, key=lambda h: h[0])
	except Exception:
		_ordered = all_hits
	for _h in _ordered:
		try:
			_d, _ac, _mat = _h[0], _h[1], _h[2]
		except Exception:
			continue
		if _mat is None:
			continue
		_vdf = getattr(_mat, 'vehicleDamageFactor', 1.0)
		_arm = float(getattr(_mat, 'armor', 0.0) or 0.0)
		if _vdf == 0.0:
			# spaced: never structure. HEAT dies here; everything else pays armour.
			if kind == 'HOLLOW_CHARGE':
				return None
			_a = abs(float(_ac))
			if _a > 1.0: _a = 1.0
			if _a < 0.087: _a = 0.087
			spaced += _arm / _a
			continue
		if _arm <= 0.0:
			continue
		_res, _eff, _p = _offh_penetration(shot, dist_m, _arm, _ac, spaced)
		return (_res, _eff, _p, spaced, _ac)
	return None


def _offh_postmortem_grading():
	'''Retail's desaturated postmortem look, forced past the quality gate.

	g_postProcessing.enable('postmortem') on its own is not enough on this client.
	Two separate faults:

	* Every _Effect is gated by __isSupported, which needs __curQuality to be IN
	  the effect's qualityMask range - and _fromMaskToQualityRange only ever
	  produces 0/1/2. python.log reports "The quality = 4 was selected", so
	  NOTHING is supported: enable() pushes an empty chain and silently produces
	  no grading at all. That is why the grey look vanished after a graphics
	  settings change and why no error was ever logged.
	* enable() only APPENDS to __curEffects; WG relies on the OUTGOING control
	  mode's disable() to clear it. We switch to arcade first, so without an
	  explicit disable() the chain came out as arcade + postmortem mixed.

	The chains themselves are loaded at startup - WGPostProcessing.init() calls
	effect.create() for every mode regardless of quality - so when the supported
	path yields nothing, push the loaded chains straight through.'''
	try:
		import PostProcessing
		from post_processing import g_postProcessing as _pp
	except Exception as _ppi:
		LOG_DEBUG('postmortem grading: no post_processing (%s)' % str(_ppi))
		return
	try: _pp.disable()
	except Exception: pass
	try: _pp.enable('postmortem')
	except Exception as _ppe:
		LOG_DEBUG('postmortem grading enable err:', str(_ppe))
		return
	_cur = getattr(_pp, '_WGPostProcessing__curEffects', None) or []
	_set = getattr(_pp, '_WGPostProcessing__settings', None) or {}
	for _e in _cur:
		try:
			if _e._Effect__isSupported(_set):
				return         # quality gate passed - retail path already did the work
		except Exception:
			pass
	_chain = []
	for _e in _cur:
		# 'advanced' effects need MRT, which __isSupported hard-refuses in this
		# build; map-depended ones build their chain per arena in enable().
		if getattr(_e, '_Effect__isAdvanced', False) or getattr(_e, '_Effect__isMapDepended', False):
			continue
		_c = getattr(_e, '_Effect__chain', None)
		if not _c:
			continue
		_chain += list(_c)
		_ct = getattr(_e, '_Effect__ctrl', None)
		if _ct is not None:
			try: _ct.enable()
			except Exception: pass
	if _chain:
		try:
			PostProcessing.chain(_chain)
			LOG_DEBUG('postmortem grading forced past the quality gate: %d effects' % len(_chain))
		except Exception as _pce:
			LOG_DEBUG('postmortem grading chain err:', str(_pce))
	else:
		LOG_DEBUG('postmortem grading: no loaded chain to force')


def _module_ui_name(name):
	'''Damage-panel device name = extra name minus 'Health'; tracks keep their side.

	The battle scope defines its own and publishes it over this one. This module-level
	copy exists because _offh_knock_out_everything is module-level too: without it the
	name lookup raised NameError and took the whole panel block down with it.'''
	return name[:-6] if name.endswith('Health') else name


class _OffhAliveState(object):
	'''Alive flag that answers to BOTH `mock.isAlive()` and `if mock.isAlive:`.

	The mocks used to carry a method that always returned True, and every death
	path then overwrote it with a plain bool. From that moment WG's own code
	broke on the tank: gui/Scaleform/Battle.py DamagePanel._setup does
	`if not vehicle.isAlive():`, which on a bool raises

	    TypeError: 'bool' object is not callable

	and takes the whole panel setup with it - that traceback appears 13 times in
	one battle log, every time the panel binds to a dead mock (postmortem, and
	each spectator switch). Our own code reads the same attribute as a value in
	a dozen places, so it has to work both ways.'''
	__slots__ = ('value',)

	def __init__(self, value=True):
		self.value = bool(value)

	def set(self, value):
		self.value = bool(value)

	def __call__(self):
		return self.value

	def __nonzero__(self):      # Python 2 truth test
		return self.value

	__bool__ = __nonzero__      # and Python 3, for the desktop self-tests

	def __repr__(self):
		return 'alive' if self.value else 'dead'


def _offh_set_alive(mock, value):
	'''Set a mock's alive flag without ever turning it back into a plain bool.'''
	state = getattr(mock, 'isAlive', None)
	if isinstance(state, _OffhAliveState):
		state.set(value)
	else:
		try:
			mock.isAlive = _OffhAliveState(value)
		except Exception:
			pass


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
		from gui.mods.offhangar import device_damage as _dd
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
		if _comp is not None and hasattr(_comp, 'get'):
			if str(_comp.get('itemTypeName', '')) in ('vehicleTurret', 'vehicleGun'):
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
				_hp = td.chassis['hullPosition']
				_ring = td.hull['turretPositions'][0]
				_bb = td.hull['hitTester'].bbox
				_hw = max(abs(float(_bb[0].x)), abs(float(_bb[1].x)))
				from gui.mods.offhangar import device_damage as _DDz
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


_OFFH_VOICE_BURST = [None]


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
	if not is_player_target:
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
	if is_player_target:
		try:
			import gui.WindowsManager
			bw = gui.WindowsManager.g_windowsManager.battleWindow
			if bw is not None and hasattr(bw, 'damagePanel'):
				bw.damagePanel.onFireInVehicle(False)
		except Exception as _xe:
			LOG_DEBUG('FIRE UI CLEAR ERR:', str(_xe))
	# Fuel tank: destroyed -> back at the regen cap, which reads as 'repaired'.
	try:
		from gui.mods.offhangar import device_damage as _DDx
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
		from gui.mods.offhangar import internal_hit_layouts as _IHL
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
	from gui.mods.offhangar import internal_geometry as _IG
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


# Bumped on every change that ships. Logged once per battle as
#   'OfflineBattle BUILD <stamp>'
# so a log can be checked against the build that produced it instead of
# assuming the client picked the new .pyc up.
_OFFH_BUILD = '1.8.59-native-experimental (2026-08-15)'


def _offh_hit_sound(path, min_gap=0.10):
	'''Play a hit sound on ONE shared carrier model.

	The two bot-shoots-player sites used to build a fresh fake model per impact,
	add it to the world, hang a sound on it and hold both for 3 s. Under fire from
	several bots that is an unbounded rate of new models and events, all parked at
	the camera - which is why the trouble showed up around the tank and only when
	a lot was going on at once.

	One carrier is enough: it only positions the sound. Repeats of the SAME sound
	are rate-limited the way IngameSoundNotifications does it with
	minTimeBetweenEvents - several hits in one frame are one bang, not five.'''
	try:
		import BigWorld
		_now = BigWorld.time()
		_last = globals().setdefault('g_offh_hit_snd_t', {})
		if _now - (_last.get(path, 0.0) or 0.0) < min_gap:
			return
		_last[path] = _now
		_fm = globals().get('g_offh_hit_carrier')
		if _fm is None or not getattr(_fm, 'inWorld', False):
			_fm = BigWorld.player().newFakeModel()
			BigWorld.addModel(_fm)
			globals()['g_offh_hit_carrier'] = _fm
		_fm.position = BigWorld.camera().position
		_snd = _fm.getSound(path)
		if _snd:
			_snd.play()
	except Exception as _hse:
		LOG_DEBUG('hit sound err:', str(_hse))


def _offh_clamp_to_arena(pt):
	'''Pull an aim point back inside the arena bounding box - the red border.

	The strategic camera can be scrolled well past the edge of the map, and the
	strategic aim point followed it. A point out there is beyond any ballistic
	solution, and wg_getShotAngles answers an unreachable point with the maximum
	elevation angle - so the barrel swung up and the gun sat pointing at the sky.
	ArenaType.boundingBox is ((minX, minZ), (maxX, maxZ)).'''
	try:
		import BigWorld, Math
		_bb = BigWorld.player().arena.arenaType.boundingBox
		_x0, _z0 = float(_bb[0][0]), float(_bb[0][1])
		_x1, _z1 = float(_bb[1][0]), float(_bb[1][1])
	except Exception:
		return pt
	try:
		_x = pt.x
		_z = pt.z
		if _x < _x0: _x = _x0
		elif _x > _x1: _x = _x1
		if _z < _z0: _z = _z0
		elif _z > _z1: _z = _z1
		if _x == pt.x and _z == pt.z:
			return pt
		# Follow the terrain at the clamped spot, or the point would keep the height
		# it had off-map and the gun would aim at thin air just inside the border.
		_y = pt.y
		try:
			_c = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(_x, 1000.0, _z), Math.Vector3(_x, -250.0, _z), 128)
			if _c is not None:
				_y = _c[0].y
		except Exception:
			pass
		return Math.Vector3(_x, _y, _z)
	except Exception:
		return pt


def _offh_water_depth(x, y, z):
	'''Metres of water standing above the hull origin at (x, y, z); -1 when dry.

	ONE probe for the player and for every bot. The two used to carry separate
	copies of this call with their own state machines around it, which is exactly
	how their drowning behaviour drifted apart. Ray from 20 m above to 5 m below,
	the same window Avatar.updateVehicleDestroyTimer works in.'''
	try:
		import BigWorld, Math
		_w = BigWorld.wg_collideWater(Math.Vector3(x, y + 20.0, z),
		                              Math.Vector3(x, y - 5.0, z), False)
	except Exception:
		return -1.0
	if _w is None or _w < 0.0:
		return -1.0
	return 20.0 - _w


# Stock maps include shallow fords as intentional tank routes.  The prebaked
# graph strongly prefers dry ground and still rejects water deeper than this;
# local steering uses the same limit so it can follow a validated ford without
# fighting the rollback guard on every frame.
_OFFH_AI_WATER_AVOID_DEPTH = 0.90


def _offh_ai_probe_reject(vehicle, reason):
	'''Remember a short-lived probe reason for aggregate LAN diagnostics.'''
	try:
		import BigWorld
		vehicle._offh_ai_probe_reject = str(reason)
		vehicle._offh_ai_probe_reject_until = BigWorld.time() + 0.75
		# A realised collision/water rejection invalidates steering decisions made
		# before it.  Do not let either short performance cache replay that heading.
		_direction_cache = getattr(vehicle, '_offh_ai_direction_cache', None)
		if isinstance(_direction_cache, dict):
			_direction_cache.clear()
		else:
			vehicle._offh_ai_direction_cache = {}
		vehicle._offh_ai_driver_cache = None
	except Exception:
		pass
	return False


def _offh_native_hazard_escape_target(vehicle, distance=12.0):
	'''Return a probed-driver target through the most recent safe pose.

	The target extends past the safe anchor so LocalDriver cannot mistake a
	one-frame boundary crossing for an arrived waypoint.  It is still only an
	intent: the existing direction_clear callback owns terrain/water validation.
	'''
	try:
		import math
		cached = getattr(
			vehicle, '_offh_native_hazard_escape_endpoint', None)
		if isinstance(cached, (tuple, list)) and len(cached) >= 3:
			return (float(cached[0]), float(cached[1]), float(cached[2]))
		current_x = float(vehicle.position.x)
		current_y = float(vehicle.position.y)
		current_z = float(vehicle.position.z)
		anchor = getattr(vehicle, '_offh_native_hazard_anchor', None)
		dx = float(anchor[0]) - current_x if anchor is not None else 0.0
		dz = float(anchor[2]) - current_z if anchor is not None else 0.0
		length = math.sqrt(dx * dx + dz * dz)
		if length < 0.10:
			entry_yaw = float(getattr(
				vehicle, '_offh_native_hazard_entry_yaw', vehicle.yaw) or 0.0)
			dx = -math.sin(entry_yaw)
			dz = -math.cos(entry_yaw)
			length = 1.0
		# Cross the last safe root by at least one hull-scale margin. Cache the
		# endpoint for this recovery session: recomputing anchor-current after the
		# hull crosses the anchor reverses the direction and causes bank chatter.
		distance = max(6.0, float(distance), length + 6.0)
		endpoint = (current_x + dx / length * distance,
			float(anchor[1]) if anchor is not None else current_y,
			current_z + dz / length * distance)
		vehicle._offh_native_hazard_escape_endpoint = endpoint
		return endpoint
	except Exception:
		return None


def _offh_native_hazard_recovery_complete(vehicle, baked_safe,
		water_safe, now, safe_seconds=0.25, endpoint_tolerance=2.0):
	'''Require a baked-safe, dry pose at the fixed escape endpoint for a while.'''
	try:
		endpoint = getattr(
			vehicle, '_offh_native_hazard_escape_endpoint', None)
		if (not baked_safe or not water_safe or endpoint is None or
				len(endpoint) < 3):
			vehicle._offh_native_hazard_safe_since = None
			return False
		dx = float(vehicle.position.x) - float(endpoint[0])
		dz = float(vehicle.position.z) - float(endpoint[2])
		tolerance = max(0.25, float(endpoint_tolerance))
		if dx * dx + dz * dz > tolerance * tolerance:
			vehicle._offh_native_hazard_safe_since = None
			return False
		now = float(now)
		since = getattr(vehicle, '_offh_native_hazard_safe_since', None)
		if since is None or now < float(since):
			vehicle._offh_native_hazard_safe_since = now
			return False
		return now - float(since) >= max(0.0, float(safe_seconds))
	except Exception:
		try:
			vehicle._offh_native_hazard_safe_since = None
		except Exception:
			pass
		return False


def _offh_ai_pose_water_depth(vehicle, position=None, yaw=None):
	'''Maximum water depth below the centre and four corners of a bot hull.

	Direction feelers only describe commanded drive.  Tank impulses, lateral slope
	slide and ballistic drift can move a hull somewhere else, so the final realised
	pose needs an independent footprint check.  A fine pose cache keeps the five
	terrain + water probes off frames where a slow tank has barely moved.'''
	_perf_started = _offh_perf_start()
	try:
		import BigWorld, Math, math
		if position is None:
			position = vehicle.position
		if yaw is None:
			yaw = float(vehicle.yaw)
		px = float(position.x)
		py = float(position.y)
		pz = float(position.z)
		key = (int(math.floor(px * 5.0 + 0.5)),
		       int(math.floor(py * 2.0 + 0.5)),
		       int(math.floor(pz * 5.0 + 0.5)),
		       int(math.floor(float(yaw) * 16.0 + 0.5)))
		cached = getattr(vehicle, '_offh_ai_water_pose_cache', None)
		if cached is not None and cached[0] == key:
			return cached[1]
		half_length, half_width = _offh_ai_hull_dims(
			getattr(vehicle, 'typeDescriptor', None))
		half_length = max(1.5, float(half_length) + 0.25)
		half_width = max(0.8, float(half_width) + 0.20)
		forward_x = math.sin(float(yaw))
		forward_z = math.cos(float(yaw))
		side_x = math.cos(float(yaw))
		side_z = -math.sin(float(yaw))
		local_points = ((0.0, 0.0),
		                (-half_width, -half_length),
		                (half_width, -half_length),
		                (-half_width, half_length),
		                (half_width, half_length))
		maximum = -1.0
		for side, forward in local_points:
			x = px + side_x * side + forward_x * forward
			z = pz + side_z * side + forward_z * forward
			probe_top = py + 8.0
			ground_y = None
			for unused in range(3):
				hit = BigWorld.wg_collideSegment(
					_offh_bspace(), Math.Vector3(x, probe_top, z),
					Math.Vector3(x, py - 60.0, z), 128)
				if hit is None:
					break
				ground_y = float(hit[0].y)
				if ground_y <= py + 4.5:
					break
				probe_top = ground_y - 0.35
				ground_y = None
			if ground_y is None:
				continue
			depth = _offh_water_depth(x, ground_y, z)
			if depth > maximum:
				maximum = depth
		vehicle._offh_ai_water_pose_cache = (key, maximum)
		return maximum
	except Exception:
		_offh_ai_probe_reject(vehicle, 'error')
		return -1.0
	finally:
		_offh_perf_stop('pose_water', _perf_started)


def _offh_ai_baked_hazard_near(position, shoulder_cells=0):
	'''Return True/False from shipped hazard data, or None when unavailable.'''
	try:
		navigator = globals().get('g_offh_terrain_navigator')
		if (navigator is None or not navigator.grid.prebaked or
				not getattr(navigator.grid, '_baked_hazards', ())):
			return None
		return bool(navigator.grid.baked_hazard_near(
			(float(position[0]), float(position[1]), float(position[2])),
			max(0, int(shoulder_cells))))
	except Exception:
		return None


def _offh_ai_baked_pose_safe(position, shoulder_cells=0):
	'''Whether a realised pose avoids shipped water/cliff hazard cells.

	A missing navigation node may be an ordinary building footprint. Treating
	every such hole as a cliff made the final rollback fight bots beside city
	walls on every frame. The baked hazard mask keeps those meanings separate.
	The bake already rejects nodes without three- and six-metre hull clearance;
	adding another four-metre runtime shoulder would veto valid baked routes.
	'''
	near = _offh_ai_baked_hazard_near(position, shoulder_cells)
	# Existing water and local terrain probes remain fail-closed. Do not
	# immobilise every bot if only this optional shipped-graph guard breaks.
	return near is not True


def _offh_ai_baked_open_corridor(start, end):
	'''Return whether shipped data proves a wide static steering corridor.'''
	try:
		navigator = globals().get('g_offh_terrain_navigator')
		if navigator is None or not navigator.grid.prebaked:
			return False
		return bool(navigator.grid.baked_open_corridor(
			(float(start[0]), float(start[1]), float(start[2])),
			(float(end[0]), float(end[1]), float(end[2])), 1))
	except Exception:
		return False


def _offh_team_score(player):
	'''Return retail team frag totals as ``(our_score, enemy_score)``.'''
	vehicles = getattr(getattr(player, 'arena', None), 'vehicles', {}) or {}
	statistics = getattr(getattr(player, 'arena', None), 'statistics', {}) or {}
	player_team = int(getattr(player, '_offhangar_team',
	                          getattr(player, 'team', 1)) or 1)
	team_frags = {1: 0, 2: 0}
	for vehicle_id, info in vehicles.items():
		if not isinstance(info, dict):
			continue
		team = int(info.get('team', 0) or 0)
		if team not in (1, 2):
			continue
		stat = statistics.get(vehicle_id)
		if isinstance(stat, dict):
			frags = stat.get('frags', info.get('frags', 0))
		else:
			frags = info.get('frags', 0)
		try:
			team_frags[team] += int(frags or 0)
		except (TypeError, ValueError):
			pass
	return team_frags.get(player_team, 0), team_frags.get(3 - player_team, 0)


def _offh_refresh_team_score(player):
	'''Refresh the top HUD score from canonical individual frag totals.

	Retail 0.8.2 sums each team's statistics.frags.  A hostile kill adds one,
	a team kill subtracts one, and an uncredited death does not change this panel.
	'''
	try:
		our_score, enemy_score = _offh_team_score(player)
		from gui import WindowsManager
		battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
		correlation = getattr(battle, '_Battle__fragCorrelation', None)
		if correlation is not None:
			correlation.updateFrags(our_score, enemy_score)
		return our_score, enemy_score
	except Exception:
		return None


def _offh_hp_display(mock):
	'''HP to SHOW for a mock, which is not always its .health.

	Drowning is not damage: the crew drowns, the hull is untouched, so a drowned
	tank keeps the HP it had when it went under. Its internal health still goes to
	0 because isAlive, the team-wipe check, the repair gate and the wreck swap all
	key off that - only the DISPLAY differs, and every panel push has to read this
	rather than .health or the per-frame spectator push resets the bar to 0.'''
	_d = getattr(mock, '_hp_display', None)
	if _d is not None:
		return max(0, int(_d))
	return max(0, int(getattr(mock, 'health', 0) or 0))


_OFFH_DEATH_DEVICES = ('engineHealth', 'ammoBayHealth', 'fuelTankHealth', 'radioHealth',
                       'gunHealth', 'turretRotatorHealth', 'surveyingDeviceHealth',
                       'leftTrackHealth', 'rightTrackHealth')


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
			# Spread over the whole post-mortem: WG's DamagePanel._updateSelf ticks every
			# 30 ms and calls onVehicleDestroyed() the moment the vehicle reads as dead,
			# which greys the panel. Re-push past that point so the red module icons are
			# what remains on screen.
			_offh_battle_callback(0.1, _reassert)
			_offh_battle_callback(0.5, _reassert)
			_offh_battle_callback(1.5, _reassert)
			_offh_battle_callback(3.0, _reassert)
		except Exception:
			pass
	except Exception:
		pass


def _offh_player_add_model(m):
	'''player.addModel for the offline player. The offline player is the ACCOUNT
	entity, not an Avatar: Entity.addModel parents the model to THAT entity's
	transform/chunk, which sits wherever the account happens to be - not in the
	battle world - so anything routed through it (the ProjectileMover's shell
	tracers being the only user) was built, moved and lit correctly but never
	drawn. Use the global model API instead, exactly like the effects that DO
	work offline (StaticSceneBoundEffects.addNew: addModel + addAlwaysUpdateModel).
	addAlwaysUpdateModel matters for a shell: it crosses several chunks in a few
	frames, and without it the model (and the tracer pixie hanging off its
	'Scene Root' node) is only ticked while its spawn chunk is being drawn.'''
	import BigWorld
	_add_model(m)
	# Always-update ONLY for our own shells. This function is served to every
	# caller of player.addModel through Account.__getattribute__, and pinning all
	# of them (flock, mapactivities, camera and control-mode models) meant a
	# growing set of permanently animated models that are never released - the
	# client died natively during the first battle load. The flag is set around
	# our ProjectileMover.add() calls.
	if not globals().get('g_offh_adding_projectile'):
		return
	try:
		BigWorld.addAlwaysUpdateModel(m)
		# Tracked so the sweep can unregister it: a shell still in flight at battle
		# end is force-deleted without passing through _offh_player_del_model, and a
		# registration left on a deleted model crashes the next space load.
		globals().setdefault('g_offh_always_update_models', []).append(m)
	except Exception:
		pass


def _offh_player_del_model(m):
	'''Symmetric teardown. delAlwaysUpdateModel lives HERE and not in
	_offh_del_model: only projectile models are ever registered for always-update,
	and a failing BigWorld call can leave its C error PENDING - putting one in the
	shared teardown path risks that error surfacing inside an unrelated caller's
	cleanup loop. Same 1-item-loop absorber so it cannot escape this function.'''
	import BigWorld
	try:
		BigWorld.delAlwaysUpdateModel(m)
		for _ in [0]:
			pass
	except:
		pass
	try:
		_aul = globals().get('g_offh_always_update_models')
		if _aul:
			for _i in range(len(_aul) - 1, -1, -1):
				if _aul[_i] is m:
					del _aul[_i]
					break
	except Exception:
		pass
	_offh_del_model(m)


# ---- HE, 0.8.2 ----------------------------------------------------------
# The damage calculator that decides this online lives in the CELL scripts and
# is not shipped with the client, so unlike the penetration model below (which
# comes straight out of items/vehicles.py and physics_shared.py) the blast
# formula is WG's published model of the era, not a decompile:
#
#   damage = nominal * SPLASH_FRACTION * (1 - dist/explosionRadius)
#            - ARMOR_FACTOR * nominal_armour
#
# Both constants are overridable from config.json "physics_tuning"-style under
# "he_tuning", so the feel can be corrected without a recompile.
_OFFH_HE_SPLASH_FRACTION = 0.5
_OFFH_HE_ARMOR_FACTOR = 1.1


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


def _offh_he_hull_armor(td):
	'''Thinnest STRUCTURAL plate the hull carries, from the descriptor.

	Used when the blast ray finds no plate at all. Returning 0 there let the
	blast through untouched; the thinnest plate is the attacker-friendly but
	still bounded assumption - blast looks for the weak facing.'''
	best = None
	try:
		mats = (getattr(td, 'hull', None) or {}).get('materials') or {}
		for m in mats.values():
			if getattr(m, 'vehicleDamageFactor', 1.0) == 0.0:
				continue
			a = float(getattr(m, 'armor', 0.0) or 0.0)
			if a <= 0.0:
				continue
			if best is None or a < best:
				best = a
	except Exception:
		return 0.0
	return best or 0.0


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
	# No plate on the ray. Zero would hand the blast a free pass, so fall back to
	# the hull's thinnest structural plate when the descriptor is available.
	return _offh_he_hull_armor(td) if td is not None else 0.0


def _offh_he_damage(base_damage, armor_nominal, dist_frac=0.0):
	'''Damage an HE burst does to a hull it did NOT get through.

	dist_frac is 0.0 for the vehicle actually struck and rises to 1.0 at the edge
	of explosionRadius for everything else caught in the blast. Returns 0 when the
	plate eats the whole thing - the normal outcome against heavy armour, and the
	reason a derp gun rewards shooting thin plate.'''
	d = (float(base_damage) * _OFFH_HE_SPLASH_FRACTION * (1.0 - float(dist_frac))
	     - _OFFH_HE_ARMOR_FACTOR * float(armor_nominal or 0.0))
	return int(d) if d > 0.0 else 0


def _offh_he_apply_tuning(overrides):
	'''Overlay config.json "he_tuning" onto the two blast constants.'''
	g = globals()
	applied = []
	if isinstance(overrides, dict):
		for k, gname in (('splash_fraction', '_OFFH_HE_SPLASH_FRACTION'),
		                 ('armor_factor', '_OFFH_HE_ARMOR_FACTOR')):
			if k in overrides:
				try:
					g[gname] = float(overrides[k])
					applied.append('%s=%s' % (k, overrides[k]))
				except (TypeError, ValueError):
					pass
	return applied


def _offh_penetration(shot, dist_m, armor, hit_angle_cos, pierce_loss=0.0):
	'''Armour test shared by the player and by bot-vs-bot fire.

	Returns (result, eff_armor, pierce): 0 ricochet, 1 no penetration, 2 penetration.

	Fixes two faults of the old inline version:
	  * it classified shells with `'HE' not in shell['name']`, a substring test on the
	    NAME. Every HEAT round contains 'HE', so both the ricochet and the
	    no-penetration branch were skipped for it and it always went through.
	    items/vehicles.py stores a proper shell['kind'] - use that.
	  * piercingPower is a Vector2 (value at 100 m, value at maxDistance) and it only
	    ever read [0], so nothing lost penetration with range.
	Randomisation is WG's own g_cache.commonConfig piercingPowerRandomization = 0.25.
	'''
	import math, random
	shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
	kind = shell.get('kind', 'ARMOR_PIERCING')
	# ARMOR_PIERCING_HE (AP with HE filler) belongs in the AP family: same
	# normalisation and the same 70 deg ricochet rule. It was missing, so it fell
	# through to the HEAT branch - no normalisation, no ricochet, no overmatch.
	# 0.8.2 ships five kinds (vehicles.py _shellKinds): HOLLOW_CHARGE,
	# HIGH_EXPLOSIVE, ARMOR_PIERCING, ARMOR_PIERCING_HE, ARMOR_PIERCING_CR.
	is_ap = kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR')
	pp = shot.get('piercingPower', (100.0, 100.0))
	try:
		p100 = float(pp[0]); pfar = float(pp[1])
	except Exception:
		p100 = pfar = 100.0
	maxd = 0.0
	try: maxd = float(shot.get('maxDistance', 0.0) or 0.0)
	except Exception: maxd = 0.0
	if maxd <= 100.0: maxd = 400.0
	if dist_m <= 100.0:
		pierce = p100
	else:
		_t = (min(dist_m, maxd) - 100.0) / (maxd - 100.0)
		pierce = p100 + (pfar - p100) * _t
	pierce *= random.uniform(0.75, 1.25)
	# spaced armour already crossed (tracks, external devices) is subtracted here
	pierce -= float(pierce_loss or 0.0)
	if pierce < 0.0:
		pierce = 0.0
	armor = float(armor or 0.0)
	if armor <= 0.0:
		return (2, 0.0, pierce)
	_ac = abs(float(hit_angle_cos))
	if _ac > 1.0: _ac = 1.0
	if _ac < 0.0001: _ac = 0.0001
	ang = math.acos(_ac)                      # 0 = square on the plate
	caliber = float(shell.get('caliber', 100) or 100)
	# shell normalisation pulls the impact towards the normal: AP 5 deg, APCR 2 deg,
	# HEAT/HE none. A calibre over three times the plate overmatches it: normalisation
	# grows and the round can no longer ricochet.
	norm = math.radians(2.0) if kind == 'ARMOR_PIERCING_CR' else (math.radians(5.0) if is_ap else 0.0)
	overmatch = is_ap and caliber > armor * 3.0
	if overmatch:
		norm *= 1.4 * caliber / (armor * 3.0)
	elif is_ap and ang > math.radians(70.0):
		return (0, armor / max(0.087, _ac), pierce)
	ang_eff = ang - norm
	if ang_eff < 0.0: ang_eff = 0.0
	eff = armor / max(0.0001, math.cos(ang_eff))
	if kind == 'HIGH_EXPLOSIVE':
		# HE penetrates or it does not, like everything else - it just gets no
		# normalisation and cannot ricochet (both already handled above). This used
		# to be an unconditional 2, so every HE round dealt FULL damage through any
		# thickness. A non-penetration here is not a miss: the caller runs
		# _offh_he_damage() for the blast.
		return (2 if pierce >= eff else 1, eff, pierce)
	return (2 if pierce >= eff else 1, eff, pierce)


def _offh_del_model(m):
	# BigWorld.delModel can set its C error as PENDING while returning normally;
	# this client only RAISES it at the next list-iteration's exhaustion (the
	# FOR_ITER opcode checks PyErr and finds it). len('') does NOT trip it. So
	# absorb it HERE with our own 1-item loop: the loop's exhaustion FOR_ITER
	# raises the pending error INSIDE this try, where the bare except eats it -
	# it can never surface in the CALLER's cleanup loop (which logged
	# "CRITICAL ERROR IN K KEY: ... Not added as a global model" and skipped
	# the list clear, leaking the battle's models into the next battle).
	import BigWorld
	# Drop it from the sweep list FIRST so add/delete stay symmetric. _add_model
	# records EVERY model, including the ProjectileMover's shell models (they go
	# through the patched player.addModel). Once the projectile chain deletes one
	# mid-battle, a copy left in g_offline_models made the end-of-battle sweep
	# delete it a SECOND time -> dangling native model -> the next arena tripped
	# `MF_ASSERT_DEV FAILED: pSpace_` (chunk_embodiment.cpp:320) and the client died
	# on the second battle. Identity compare: BigWorld.Model equality is not reliable.
	try:
		_gm = g_offline_models
		for _i in range(len(_gm) - 1, -1, -1):
			if _gm[_i] is m:
				del _gm[_i]
				break
	except Exception:
		pass
	try:
		BigWorld.delModel(m)
		for _ in [0]:
			pass
	except:
		pass


def _offh_load_hit_testers(type_descriptor):
	"""Load and remember the unique BSP testers used by this battle.

	Retail Vehicle.onEnterWorld adds every loaded tester to PlayerAvatar.hitTesters,
	then PlayerAvatar.onLeaveWorld releases that set exactly once. Offline tanks are
	not Vehicle entities, so reproduce that ownership explicitly.
	"""
	loaded = globals().setdefault('g_offh_loaded_hit_testers', {})
	new_count = 0
	if type_descriptor is None:
		return new_count
	for hit_tester in type_descriptor.getHitTesters():
		if hit_tester is None:
			continue
		key = id(hit_tester)
		if key in loaded:
			continue
		try:
			hit_tester.loadBspModel()
		except Exception:
			continue
		# Match retail Vehicle.onEnterWorld: ownership begins only after the native
		# load succeeds, so a failed acquisition is never released on exit.
		loaded[key] = hit_tester
		new_count += 1
	return new_count


def _offh_release_hit_testers():
	"""Retail-equivalent release of the battle's unique vehicle BSP testers."""
	loaded = globals().pop('g_offh_loaded_hit_testers', {}) or {}
	released = 0
	failed = 0
	for hit_tester in list(loaded.values()):
		try:
			hit_tester.releaseBspModel()
			released += 1
		except Exception:
			failed += 1
	return released, failed


def _offh_detach_stickers(source, seen=None):
	"""Detach VehicleStickers before dropping their Python containers.

	The stock appearance calls detachStickers while the component nodes still
	exist. Clearing our lists/dicts first leaves the native WGStickerModel attached
	to a model that is about to be deleted.
	"""
	if seen is None:
		seen = {}
	if source is None:
		return 0
	if isinstance(source, dict):
		values = list(source.values())
	elif isinstance(source, (list, tuple, set)):
		values = list(source)
	else:
		values = [source]
	detached = 0
	for value in values:
		sticker = value
		if isinstance(value, (list, tuple)) and value:
			sticker = value[0]
		if sticker is None or not hasattr(sticker, 'detachStickers'):
			continue
		key = id(sticker)
		if key in seen:
			continue
		seen[key] = True
		try:
			sticker.detachStickers()
			detached += 1
		except Exception:
			pass
	return detached


def _offh_battle_entity_ids(*sources):
	"""Collect owned battle entities from every engine container, deduplicated."""
	entity_ids = {}
	for source in sources:
		if source is None:
			continue
		try:
			items = list(source.items())
		except Exception:
			continue
		for entity_id, entity in items:
			try:
				if entity.__class__.__name__ in ('OfflineEntity', 'AreaDestructibles'):
					entity_ids[entity_id] = True
			except Exception:
				pass
	return list(entity_ids.keys())


def _offh_clear_arena_events(arena):
	"""Drop every synthetic-arena event delegate, including legacy attributes.

	Older _OfflineArenaStub versions let ``arena.onFoo += handler`` materialise
	``onFoo`` in arena.__dict__.  Those events are outside _event_stubs and keep
	a whole battle window, its closures and all mock vehicles alive.  New stubs
	prevent that write, but the direct-attribute scan makes teardown complete and
	keeps this safe across an in-place mod update.
	"""
	if arena is None:
		return 0
	events = []
	registry = getattr(arena, '_event_stubs', None)
	if isinstance(registry, dict):
		events.extend(list(registry.values()))
	try:
		direct = list(arena.__dict__.items())
	except Exception:
		direct = []
	for name, event in direct:
		if name.startswith('on'):
			events.append(event)
			try:
				delattr(arena, name)
			except Exception:
				pass
	seen_delegate_lists = {}
	removed = 0
	for event in events:
		try:
			delegates = getattr(event, 'delegates', None)
		except Exception:
			delegates = None
		if not isinstance(delegates, list):
			continue
		key = id(delegates)
		if key in seen_delegate_lists:
			continue
		seen_delegate_lists[key] = True
		removed += len(delegates)
		del delegates[:]
	if isinstance(registry, dict):
		registry.clear()
	return removed


def _offh_battle_callback(delay, callback):
	"""Schedule a battle-owned callback that the sweep can cancel safely."""
	import BigWorld
	callbacks = globals().setdefault('g_offh_battle_callbacks', {})
	generation = globals().get('g_offh_battle_gen', 0)
	holder = [None]
	def _run():
		callback_id = holder[0]
		if callback_id is not None:
			callbacks.pop(callback_id, None)
		if globals().get('g_offh_battle_gen', 0) != generation:
			return
		return callback()
	callback_id = BigWorld.callback(delay, _run)
	holder[0] = callback_id
	callbacks[callback_id] = True
	return callback_id


def _offh_vec3_tuple(value):
	return (float(value[0]), float(value[1]), float(value[2]))


def _offh_live_projectile_position(state, elapsed):
	from gui.mods.offhangar import projectile_runtime as _projectiles
	return _projectiles.trajectory_position(
		state['start'], state['velocity'], state['gravity'], elapsed)


def _offh_live_projectile_target_positions(vehicles, shooter_id):
	positions = {}
	for entity_id, vehicle in (vehicles or {}).iteritems():
		try:
			if int(entity_id) == int(shooter_id):
				continue
			position = getattr(vehicle, 'position', None)
			if position is not None:
				positions[int(entity_id)] = _offh_vec3_tuple(position)
		except Exception:
			pass
	return positions


def _offh_player_gun_marker_vehicle_candidates(start, velocity, gravity,
		max_time, vehicles, shooter_id, profile_candidates=False):
	"""Return the visible vehicles inside the preview trajectory's XZ envelope.

	The direct-fire preview applies gravity only on Y, so its complete XZ path is
	a straight segment.  A vehicle farther than the existing 3-D broadphase
	radius from that segment cannot pass the per-chord broadphase either.  Keep a
	fail-open fallback for an unexpected lateral gravity vector or mapping type.
	"""
	try:
		from gui.mods.offhangar import projectile_runtime as _projectiles
		start_t = _offh_vec3_tuple(start)
		velocity_t = _offh_vec3_tuple(velocity)
		gravity_t = _offh_vec3_tuple(gravity)
		if abs(gravity_t[0]) > 0.000000001 or abs(gravity_t[2]) > 0.000000001:
			return vehicles
		end_x = start_t[0] + velocity_t[0] * float(max_time)
		end_z = start_t[2] + velocity_t[2] * float(max_time)
		span_x = end_x - start_t[0]
		span_z = end_z - start_t[2]
		span_sq = span_x * span_x + span_z * span_z
		broadphase_sq = _projectiles.PROJECTILE_BROADPHASE_RADIUS ** 2
		try:
			candidates = vehicles.__class__()
		except Exception:
			return vehicles
		shooter_id = int(shooter_id)
		for entity_id, vehicle in (vehicles or {}).iteritems():
			try:
				if int(entity_id) == shooter_id:
					continue
				if (not getattr(vehicle, 'isAlive', False) or
						(getattr(vehicle, 'health', 0) or 0) <= 0 or
						not getattr(vehicle, '_spot_visible', True)):
					continue
				position = getattr(vehicle, 'position', None)
				if position is None:
					continue
				offset_x = float(position[0]) - start_t[0]
				offset_z = float(position[2]) - start_t[2]
				fraction = 0.0
				if span_sq > 0.000000000001:
					fraction = max(0.0, min(1.0,
						(offset_x * span_x + offset_z * span_z) / span_sq))
				distance_x = offset_x - span_x * fraction
				distance_z = offset_z - span_z * fraction
				if (distance_x * distance_x + distance_z * distance_z <=
						broadphase_sq + 0.000001):
					candidates[entity_id] = vehicle
			except Exception:
				# A malformed mock cannot participate in the existing chord query.
				continue
		if profile_candidates:
			_offh_perf_count('marker_vehicle_candidates', len(candidates))
		return candidates
	except Exception:
		return vehicles


def _offh_live_projectile_world_hit(start, end):
	"""Return the nearest static/water hit and its local chord distance."""
	try:
		import BigWorld, Math
		start_v = Math.Vector3(start[0], start[1], start[2])
		end_v = Math.Vector3(end[0], end[1], end[2])
		direction = end_v - start_v
		length = direction.length
		if length <= 0.0001:
			return None, 999999.0
		direction.normalise()
		world_hit = BigWorld.wg_collideSegment(
			_offh_bspace(), start_v, end_v, 128)
		world_distance = ((world_hit[0] - start_v).length
		                  if world_hit is not None else 999999.0)
		try:
			water_distance = BigWorld.wg_collideWater(start_v, end_v)
			if water_distance >= 0.0 and water_distance < world_distance:
				water_point = start_v + direction.scale(water_distance)
				return (water_point, None), float(water_distance)
		except Exception:
			pass
		return world_hit, float(world_distance)
	except Exception:
		return None, 999999.0


def _offh_projectile_chord_impact(start, end, vehicles, shooter_id,
		previous_positions=None, current_positions=None,
		frame_start_fraction=0.0, frame_end_fraction=1.0,
		visible_only=False):
	"""Return the nearest static/water or vehicle hit on one shell chord.

	This is the collision contract shared by the live projectile and the gun
	marker preview.  The caller owns trajectory generation; this function owns
	only nearest-hit ordering.  The shooter is excluded from dynamic collision,
	but static and water collision start at the exact gun position, as in retail.
	"""
	try:
		import Math
		from gui.mods.offhangar import projectile_runtime as _projectiles
		start_v = Math.Vector3(start[0], start[1], start[2])
		end_v = Math.Vector3(end[0], end[1], end[2])
		segment = end_v - start_v
		segment_length = segment.length
		if segment_length <= 0.0001:
			return None
		direction = Math.Vector3(segment)
		direction.normalise()
		world_hit, world_local = _offh_live_projectile_world_hit(
			_offh_vec3_tuple(start_v), _offh_vec3_tuple(end_v))
		world_fraction = 999999.0
		if world_hit is not None:
			world_fraction = min(1.0, max(0.0, world_local / segment_length))

		previous_positions = previous_positions or {}
		current_positions = current_positions or {}
		broadphase_sq = _projectiles.PROJECTILE_BROADPHASE_RADIUS ** 2
		vehicle_fraction = 999999.0
		vehicle_hit = None
		for entity_id, vehicle in (vehicles or {}).iteritems():
			try:
				entity_id = int(entity_id)
				if entity_id == int(shooter_id):
					continue
				if (not getattr(vehicle, 'isAlive', False) or
						(getattr(vehicle, 'health', 0) or 0) <= 0):
					continue
				if visible_only and not getattr(vehicle, '_spot_visible', True):
					continue
				current_position = current_positions.get(entity_id)
				if current_position is None:
					position = getattr(vehicle, 'position', None)
					if position is None:
						continue
					current_position = _offh_vec3_tuple(position)
				previous_position = previous_positions.get(
					entity_id, current_position)
				adjusted_start, adjusted_end = (
					_projectiles.compensate_segment_for_moving_target(
						_offh_vec3_tuple(start_v), _offh_vec3_tuple(end_v),
						previous_position, current_position,
						frame_start_fraction, frame_end_fraction))
				if _projectiles.point_segment_distance_sq(
						current_position, adjusted_start,
						adjusted_end) > broadphase_sq:
					continue
				adjusted_start_v = Math.Vector3(*adjusted_start)
				adjusted_end_v = Math.Vector3(*adjusted_end)
				adjusted_length = (
					adjusted_end_v - adjusted_start_v).length
				if adjusted_length <= 0.0001:
					continue
				collision = vehicle.collideSegment(
					adjusted_start_v, adjusted_end_v)
				if collision is None:
					continue
				fraction = float(collision[0]) / adjusted_length
				if 0.0 <= fraction <= 1.0 and fraction < vehicle_fraction:
					vehicle_fraction = fraction
					vehicle_hit = (
						vehicle, collision, adjusted_start_v, adjusted_end_v)
			except Exception:
				continue

		if vehicle_hit is not None and vehicle_fraction <= world_fraction:
			return {
				'kind': 'vehicle', 'vehicle': vehicle_hit[0],
				'collision': vehicle_hit[1], 'world_hit': None,
				'impact_point': start_v + direction.scale(
					segment_length * vehicle_fraction),
				'segment_start': vehicle_hit[2],
				'segment_end': vehicle_hit[3],
				'direction': direction, 'fraction': vehicle_fraction,
				'local_distance': segment_length * vehicle_fraction,
			}
		if world_hit is not None and world_fraction <= 1.0:
			return {
				'kind': 'world', 'vehicle': None, 'collision': None,
				'world_hit': world_hit, 'impact_point': world_hit[0],
				'segment_start': start_v, 'segment_end': end_v,
				'direction': direction, 'fraction': world_fraction,
				'local_distance': segment_length * world_fraction,
			}
	except Exception:
		pass
	return None


def _offh_player_gun_marker_impact(start, velocity, gravity, vehicles,
		shooter_id, player_team, max_time, max_distance,
		profile_candidates=False):
	"""Preview the same gravity trajectory and first hit as the live shell."""
	try:
		import Math
		from gui.mods.offhangar import projectile_runtime as _projectiles
		start_t = _offh_vec3_tuple(start)
		velocity_t = _offh_vec3_tuple(velocity)
		gravity_t = _offh_vec3_tuple(gravity)
		max_time = max(0.05, min(20.0, float(max_time or 0.05)))
		max_distance = max(1.0, float(max_distance or 720.0))
		candidate_vehicles = _offh_player_gun_marker_vehicle_candidates(
			start_t, velocity_t, gravity_t, max_time, vehicles, shooter_id,
			profile_candidates)
		positions = _offh_live_projectile_target_positions(
			candidate_vehicles, shooter_id)
		origin = Math.Vector3(*start_t)
		travelled = 0.0
		last_point = Math.Vector3(origin)
		last_direction = Math.Vector3(*velocity_t)
		if last_direction.length > 0.0001:
			last_direction.normalise()
		else:
			last_direction = Math.Vector3(0.0, 0.0, 1.0)
		for chord_start_t, chord_end_t in _projectiles.substep_boundaries(
				0.0, max_time, _projectiles.PROJECTILE_MAX_SUBSTEP_SECONDS):
			first = _projectiles.trajectory_position(
				start_t, velocity_t, gravity_t, chord_start_t)
			second = _projectiles.trajectory_position(
				start_t, velocity_t, gravity_t, chord_end_t)
			first_v = Math.Vector3(*first)
			second_v = Math.Vector3(*second)
			segment = second_v - first_v
			segment_length = segment.length
			if segment_length <= 0.0001:
				continue
			impact = _offh_projectile_chord_impact(
				first, second, candidate_vehicles, shooter_id,
				positions, positions, 0.0, 1.0, True)
			last_point = second_v
			last_direction = Math.Vector3(second_v - first_v)
			last_direction.normalise()
			if impact is not None:
				coll_data = None
				if impact['kind'] == 'vehicle':
					from gui.mods.offhangar import pen_indicator as _peni
					coll_data = _peni.coll_data_from_collision(
						impact['vehicle'], impact['collision'],
						impact['impact_point'], player_team)
				return (
					impact['impact_point'], impact['direction'],
					travelled + impact['local_distance'], coll_data)
			travelled += segment_length
		# Retail checks every collision chord until the arena boundary, then
		# clamps only a no-hit endpoint to maxDistance. Clipping every chord at
		# that radius can hide a real first collision which the live shell sees.
		no_hit_offset = last_point - origin
		no_hit_distance = no_hit_offset.length
		if no_hit_distance > max_distance:
			no_hit_offset.normalise()
			last_direction = Math.Vector3(no_hit_offset)
			last_point = origin + no_hit_offset.scale(max_distance)
			no_hit_distance = max_distance
		return (
			last_point, last_direction, no_hit_distance, None)
	except Exception:
		return None


def _offh_stop_live_projectile(shot_id, impact_point):
	"""Stop the native tracer on a dynamic hit; tolerate near-zero X velocity."""
	try:
		mover = globals().get('g_projectile_mover')
		if mover is not None and shot_id is not None:
			mover.hide(shot_id, impact_point)
	except Exception:
		pass


def _offh_live_projectile_finish(runtime_id, state, kind, payload):
	shots = globals().setdefault('g_offh_live_projectiles', {})
	shots.pop(runtime_id, None)
	try:
		if kind == 'vehicle':
			_offh_stop_live_projectile(state.get('shot_id'), payload['impact_point'])
			state['on_vehicle_hit'](
				payload['vehicle'], payload['collision'], payload['impact_point'],
				payload['segment_start'], payload['segment_end'],
				payload['direction'], payload['travel_distance'],
				payload['flight_time'])
		else:
			state['on_world_hit'](
				payload['world_hit'], payload['impact_point'],
				payload['direction'], payload['travel_distance'],
				payload['flight_time'])
	except Exception:
		LOG_CURRENT_EXCEPTION()


def _offh_live_projectile_advance(runtime_id, state, elapsed):
	"""Advance one authoritative shell and resolve its first live collision."""
	try:
		import Math
		from gui.mods.offhangar import projectile_runtime as _projectiles
		last_t = float(state.get('last_t', 0.0) or 0.0)
		elapsed = max(last_t, min(float(elapsed), float(state['max_time'])))
		if elapsed <= last_t + 0.00001:
			return True

		vehicles = state.get('vehicles') or {}
		previous_positions = state.get('target_positions') or {}
		current_positions = _offh_live_projectile_target_positions(
			vehicles, state['shooter_id'])
		frame_span = max(0.00001, elapsed - last_t)
		travelled = float(state.get('travelled', 0.0) or 0.0)

		for chord_start_t, chord_end_t in _projectiles.substep_boundaries(
				last_t, elapsed, _projectiles.PROJECTILE_MAX_SUBSTEP_SECONDS):
			start = _offh_live_projectile_position(state, chord_start_t)
			end = _offh_live_projectile_position(state, chord_end_t)
			start_v = Math.Vector3(start[0], start[1], start[2])
			end_v = Math.Vector3(end[0], end[1], end[2])
			actual_segment = end_v - start_v
			actual_length = actual_segment.length
			if actual_length <= 0.0001:
				continue
			actual_direction = Math.Vector3(actual_segment)
			actual_direction.normalise()

			frame_start_fraction = (chord_start_t - last_t) / frame_span
			frame_end_fraction = (chord_end_t - last_t) / frame_span
			impact = _offh_projectile_chord_impact(
				start, end, vehicles, state['shooter_id'],
				previous_positions, current_positions,
				frame_start_fraction, frame_end_fraction, False)
			if impact is not None:
				impact_time = chord_start_t + (
					chord_end_t - chord_start_t) * impact['fraction']
				tangent = Math.Vector3(
					state['velocity'][0] + state['gravity'][0] * impact_time,
					state['velocity'][1] + state['gravity'][1] * impact_time,
					state['velocity'][2] + state['gravity'][2] * impact_time)
				if tangent.length > 0.0001:
					tangent.normalise()
				else:
					tangent = impact['direction']
				payload = {
					'impact_point': impact['impact_point'],
					'direction': tangent,
					'travel_distance': travelled + impact['local_distance'],
					'flight_time': impact_time,
				}
				if impact['kind'] == 'vehicle':
					payload.update({
						'vehicle': impact['vehicle'],
						'collision': impact['collision'],
						'segment_start': impact['segment_start'],
						'segment_end': impact['segment_end'],
					})
				else:
					payload['world_hit'] = impact['world_hit']
				_offh_live_projectile_finish(
					runtime_id, state, impact['kind'], payload)
				return False

			travelled += actual_length

		state['last_t'] = elapsed
		state['travelled'] = travelled
		state['target_positions'] = current_positions
		return elapsed + 0.00001 < float(state['max_time'])
	except Exception:
		LOG_CURRENT_EXCEPTION()
		globals().setdefault('g_offh_live_projectiles', {}).pop(runtime_id, None)
		return False


def _offh_live_projectile_tick():
	globals()['g_offh_live_projectile_callback_active'] = False
	shots = globals().setdefault('g_offh_live_projectiles', {})
	if not shots:
		return
	try:
		import BigWorld
		now = float(BigWorld.time())
		for runtime_id, state in list(shots.items()):
			if runtime_id not in shots:
				continue
			elapsed = max(0.0, now - float(state['start_time']))
			if not _offh_live_projectile_advance(runtime_id, state, elapsed):
				shots.pop(runtime_id, None)
	except Exception:
		LOG_CURRENT_EXCEPTION()
	if shots and not globals().get('g_offh_live_projectile_callback_active', False):
		from gui.mods.offhangar import projectile_runtime as _projectiles
		globals()['g_offh_live_projectile_callback_active'] = True
		_offh_battle_callback(
			_projectiles.PROJECTILE_CALLBACK_SECONDS,
			_offh_live_projectile_tick)


def _offh_launch_live_projectile(shot_id, start, velocity, gravity,
		vehicles, shooter_id, on_vehicle_hit, on_world_hit, max_time=None):
	"""Register a shell whose damage is resolved only when its tracer arrives."""
	try:
		import BigWorld
		visual = None
		mover = globals().get('g_projectile_mover')
		if mover is not None and shot_id is not None:
			visual = getattr(
				mover, '_ProjectileMover__projectiles', {}).get(shot_id)
		if visual is not None:
			start = visual.get('startPoint', start)
			velocity = visual.get('velocity', velocity)
			gravity = visual.get('gravity', gravity)
			start_time = float(visual.get('startTime', BigWorld.time()))
			max_time = float(visual.get('time', max_time or 20.0) or 20.0)
		else:
			start_time = float(BigWorld.time())
		max_time = max(0.05, min(20.0, float(max_time or 20.0)))
		serial = int(globals().get('g_offh_live_projectile_serial', 0) or 0) + 1
		globals()['g_offh_live_projectile_serial'] = serial
		state = {
			'shot_id': shot_id,
			'start': _offh_vec3_tuple(start),
			'velocity': _offh_vec3_tuple(velocity),
			'gravity': _offh_vec3_tuple(gravity),
			'start_time': start_time,
			'max_time': max_time,
			'last_t': 0.0,
			'travelled': 0.0,
			'vehicles': vehicles,
			'shooter_id': int(shooter_id),
			'target_positions': _offh_live_projectile_target_positions(
				vehicles, shooter_id),
			'on_vehicle_hit': on_vehicle_hit,
			'on_world_hit': on_world_hit,
		}
		globals().setdefault('g_offh_live_projectiles', {})[serial] = state
		if not globals().get('g_offh_live_projectile_callback_active', False):
			globals()['g_offh_live_projectile_callback_active'] = True
			_offh_battle_callback(0.0, _offh_live_projectile_tick)
		return serial
	except Exception:
		LOG_CURRENT_EXCEPTION()
		return None


_OFFH_PLAYER_BATTLE_ATTRS = (
	'vehicleTypeDescriptor', 'getVehicleAttached', 'getOwnVehicleMatrix',
	'getOwnVehiclePosition', 'handleKey', 'handleMouseEvent', 'leaveArena',
	'setGUIVisible', 'getAutorotation', 'enableOwnVehicleAutorotation',
	'positionControl', 'gunRotator', 'getOwnVehicleSpeeds', 'autoAim',
	'newFakeModel', 'inputHandler', 'onSpaceLoaded',
	'getOwnVehicleShotDispersionAngle', 'onEquipmentButtonPressed',
	'onDamageIconButtonPressed', 'shoot', 'terrainEffects',
	'_autoaim_target', '_outlined_bot',
	'_offh_vehicle_descriptors', '_offh_auto_spawn_expected',
	'_offh_auto_spawn_completed',
	'_offh_lineup_prefetch_refs', '_offh_lineup_prefetch_ready',
	'_offh_lineup_prefetch_started_at', '_offh_lineup_prefetch_wait_logged',
	'_offh_lineup_model_refs', '_offh_lineup_model_pending',
	'_offh_lineup_model_failed', '_offh_forced_model_refs',
	'_offh_sticker_warmup_queue', '_offh_sticker_warmup_active',
	'_offh_spawn_streaming_bootstrap',
	'_offh_spawn_streaming_monitor_active',
	'_offh_spawn_streaming_wait_logged',
	'_offhangar_prepare_native_authority_streaming',
)


def _offh_capture_player_battle_attrs(player):
	"""Snapshot instance attributes replaced by the synthetic PlayerAvatar."""
	if player is None or getattr(player, '_offh_player_attr_restore', None) is not None:
		return 0
	state = []
	try:
		instance_dict = player.__dict__
	except Exception:
		instance_dict = None
	for name in _OFFH_PLAYER_BATTLE_ATTRS:
		if isinstance(instance_dict, dict):
			existed = name in instance_dict
			value = instance_dict.get(name)
		else:
			try:
				value = getattr(player, name)
				existed = True
			except Exception:
				value = None
				existed = False
		state.append((name, existed, value))
	player._offh_player_attr_restore = state
	return len(state)


def _offh_restore_player_battle_attrs(player):
	"""Remove battle closures from the persistent account and restore originals."""
	if player is None:
		return 0, 0
	state = getattr(player, '_offh_player_attr_restore', None)
	if state is None:
		return 0, 0
	try:
		del player._offh_player_attr_restore
	except Exception:
		try: player._offh_player_attr_restore = None
		except Exception: pass
	restored = 0
	failed = 0
	for name, existed, value in state:
		try:
			if existed:
				setattr(player, name, value)
			else:
				try:
					current_dict = player.__dict__
				except Exception:
					current_dict = None
				if not isinstance(current_dict, dict) or name in current_dict:
					delattr(player, name)
			restored += 1
		except Exception:
			failed += 1
	return restored, failed


def _offh_proc_mem_mb():
	# DEBUG-ONLY memory probe (returns immediately unless debug_logging is on,
	# so it NEVER runs in the live/share build). ctypes is not functional in
	# this client (_ctypes.pyd missing) and tasklist gives only the working
	# set; wmic also exposes VirtualSize - the 32-bit ADDRESS-SPACE figure that
	# hits the ~2 GB fragmentation wall and OOM-crashes map/model loads (that
	# is the real limit; working set/RSS understates it). Returns
	# (rss_mb, virtual_mb, commit_mb) or (-1, -1, -1).
	try:
		from gui.mods.offhangar.logging import _DBG as _mp_dbg
		if not _mp_dbg[0]:
			return (-1, -1, -1)
	except Exception:
		return (-1, -1, -1)
	try:
		import os
		_pid = os.getpid()
		# /value -> one KEY=VALUE per line. VirtualSize/WorkingSetSize are bytes,
		# PageFileUsage (commit/private) is KB.
		_out = os.popen('wmic process where ProcessId=%d get VirtualSize,PageFileUsage,WorkingSetSize /value' % _pid).read()
		_v = {}
		for _ln in _out.splitlines():
			if '=' in _ln:
				_k, _, _val = _ln.partition('=')
				_val = _val.strip()
				if _val.isdigit():
					_v[_k.strip()] = int(_val)
		if _v:
			return (_v.get('WorkingSetSize', 0) // (1024 * 1024),
			        _v.get('VirtualSize', 0) // (1024 * 1024),
			        _v.get('PageFileUsage', 0) // 1024)
	except Exception:
		pass
	# WMIC is no longer installed by default on current Windows releases.  Use
	# the built-in PowerShell process object as the equivalent read-only probe.
	try:
		import os
		_pid = os.getpid()
		_ps = ('powershell -NoProfile -NonInteractive -Command '
		       '"$p=Get-Process -Id %d; '
		       "'WorkingSetSize='+$p.WorkingSet64; "
		       "'VirtualSize='+$p.VirtualMemorySize64; "
		       "'PrivateBytes='+$p.PrivateMemorySize64" + '"') % _pid
		_out = os.popen(_ps).read()
		_v = {}
		for _ln in _out.splitlines():
			if '=' in _ln:
				_k, _, _val = _ln.partition('=')
				_val = _val.strip()
				if _val.isdigit():
					_v[_k.strip()] = int(_val)
		if _v:
			return (_v.get('WorkingSetSize', 0) // (1024 * 1024),
			        _v.get('VirtualSize', 0) // (1024 * 1024),
			        _v.get('PrivateBytes', 0) // (1024 * 1024))
	except Exception:
		pass
	return (-1, -1, -1)


def _offh_gc_census_line(tag):
	"""DEBUG-ONLY per-battle object census. Forces a gc.collect first (so we
	count only SURVIVING = truly-retained objects, i.e. the leak), then walks
	gc.get_objects() and logs the top types by count PLUS the top GROWERS vs
	the previous battle's census. Also logs bw_entities + gc.garbage so we can
	tell a Python leak (total/grower type climbs) from a C++ residual
	(python total flat but commit still climbs = map textures / appearances,
	NOT freeable from Python). Returns immediately unless debug_logging is on,
	so it never runs in the live/share build."""
	try:
		from gui.mods.offhangar.logging import _DBG as _c_dbg, LOG_DEBUG as _c_log
		if not _c_dbg[0]:
			return
	except Exception:
		return
	try:
		import gc as _gc
		try:
			_gc.collect()
			_gc.collect(2)
		except Exception:
			pass
		_objs = _gc.get_objects()
		_total = len(_objs)
		_counts = {}
		for _o in _objs:
			try:
				_tn = type(_o).__name__
			except Exception:
				_tn = '?'
			_counts[_tn] = _counts.get(_tn, 0) + 1
		_objs = None
		# per-tag prev so quit->quit and start->start diff cleanly (interleaved
		# quit/start sweeps would otherwise make the grower delta meaningless).
		_prevkey = '_g_offh_prev_census_%s' % tag
		_prev = globals().get(_prevkey) or {}
		_top = sorted(_counts.items(), key=lambda _kv: _kv[1], reverse=True)[:12]
		_grow = []
		if _prev:
			for _k, _v in _counts.items():
				_d = _v - _prev.get(_k, 0)
				if _d:
					_grow.append((_k, _d))
			_grow.sort(key=lambda _kv: _kv[1], reverse=True)
			_grow = _grow[:12]
		globals()[_prevkey] = _counts
		try:
			_ng = len(_gc.garbage)
		except Exception:
			_ng = -1
		_ent = -1
		try:
			import BigWorld as _bw
			_ent = len(getattr(_bw, 'entities', []) or [])
		except Exception:
			pass
		_c_log('OfflineBattle.sweep(%s) census: total=%d garbage=%d bw_entities=%d | top=%s' % (tag, _total, _ng, _ent, _top))
		if _grow:
			_c_log('OfflineBattle.sweep(%s) census GROWERS(vs prev battle): %s' % (tag, _grow))
	except Exception:
		pass


def _offh_bspace():
	"""The space the battle MAP is in and the camera renders. In dedicated
	(full_space_release) mode this is a FRESH space, different from the
	read-only player.spaceID; in reuse mode it equals player.spaceID. Every
	battle collision / physics / destructible query must use THIS, else it
	hits the wrong (empty) space (tank falls through / no terrain)."""
	try:
		_s = globals().get('g_offh_battle_space', 0) or 0
		if _s:
			return _s
	except Exception:
		pass
	try:
		import BigWorld
		return BigWorld.player().spaceID
	except Exception:
		return 0


def _offh_set_render_space(sid):
	"""Make the engine RENDER space `sid` by pointing the camera at it. The
	HSPACE diagnostic proved rendering follows camera.spaceID /
	BigWorld.cameraSpaceID (the hangar renders its own space this way), NOT the
	read-only _offh_bspace(). Tries both; guarded."""
	import BigWorld
	try:
		if hasattr(BigWorld, 'cameraSpaceID'):
			BigWorld.cameraSpaceID(sid)
	except Exception:
		pass
	try:
		_c = BigWorld.camera()
		if _c is not None:
			_c.spaceID = sid
	except Exception:
		pass


def _offh_safe_purge():
	"""WG-style resource wipe in the loading-screen 'no man's land'. Called
	BETWEEN g_hangarSpace.destroy() and init() on battle exit: the battle is
	torn down and the hangar is DESTROYED (not re-inited yet), so NOTHING
	references the map/tank resources -> ResMgr.purge is safe here. Doing it
	while the hangar/tanks were still LIVE (e.g. at battle start) froze the
	engine. Draw is forced off first (as WG does). Gated by resmgr_purge; the
	START line is flushed to disk so a freeze is pinpointed in the log."""
	try:
		from _constants import CONFIG_OPTIONS as _CFG_PG
		_do_purge = bool(_CFG_PG.get('resmgr_purge', False))
		_do_reload = bool(_CFG_PG.get('reload_textures', False))
		_do_deepgc = bool(_CFG_PG.get('deep_gc', False))
	except Exception:
		_do_purge = False
		_do_reload = False
		_do_deepgc = False
	if not (_do_purge or _do_reload or _do_deepgc):
		return
	import BigWorld
	try:
		BigWorld.worldDrawEnabled(False)
	except Exception:
		pass
	try:
		from gui.mods.offhangar.logging import LOG_DEBUG as _pl
	except Exception:
		_pl = lambda *a: None
	# reloadTextures: reloads the GRAPHICS texture cache from LOCAL disk files.
	# ResMgr.purge is the wrong tool (map textures live in Moo, not DataSections)
	# and global purge freezes on the offline reload. reloadTextures is a local
	# graphics op - should free the dead map-texture residual without hanging.
	if _do_reload:
		try:
			if hasattr(BigWorld, 'reloadTextures'):
				_pl('OfflineBattle.reloadTextures START (if this is the LAST log line, it FROZE - set reload_textures:false)')
				try: BigWorld.flushPythonLog()
				except Exception: pass
				BigWorld.reloadTextures()
				_pl('OfflineBattle.reloadTextures done')
		except Exception:
			pass
	if _do_purge:
		try:
			import ResMgr as _rmg
			if hasattr(_rmg, 'purge'):
				_pl('OfflineBattle.safe_purge START (if this is the LAST log line, purge FROZE - set resmgr_purge:false)')
				try: BigWorld.flushPythonLog()
				except Exception: pass
				try:
					if hasattr(BigWorld, 'clearAllSpaces'):
						BigWorld.clearAllSpaces()
				except Exception:
					pass
				_pl('OfflineBattle.safe_purge clearAllSpaces done, now purging')
				try: BigWorld.flushPythonLog()
				except Exception: pass
				try:
					_rmg.purge()
				except TypeError:
					try: _rmg.purge('', True)
					except Exception: pass
				_pl('OfflineBattle.safe_purge done')
		except Exception:
			pass
		pass
	# deep_gc: bypass the broken C++ ResMgr entirely - clearAllSpaces (works)
	# + aggressive Python GC to drop loose model/mock/closure refs that pin
	# C++ objects. Won't touch the Moo texture cache (not Python), but trims
	# the Python overhang between matches. Safe (no freeze).
	if _do_deepgc:
		try:
			if hasattr(BigWorld, 'clearAllSpaces'):
				BigWorld.clearAllSpaces()
		except Exception:
			pass
		try:
			import sys as _sys
			if hasattr(_sys, 'exc_clear'):
				_sys.exc_clear()
		except Exception:
			pass
		try:
			_pl('OfflineBattle.deep_gc START')
		except Exception:
			pass
	try:
		import gc as _gp
		_gp.collect()
		try: _gp.collect(2)
		except Exception: _gp.collect()
	except Exception:
		pass
	# Re-enable drawing so the re-inited hangar renders (the ESC path does not
	# turn it back on itself).
	try:
		BigWorld.worldDrawEnabled(True)
	except Exception:
		pass


def _offh_dump_purge_apis():
	"""DEBUG-ONLY: dump the real docs/signatures of the resource + TEXTURE APIs
	so we can find the RIGHT texture-cache flush (ResMgr.purge is the wrong tool:
	map textures live in the graphics/Moo cache, not ResMgr DataSections, and
	global purge freezes on the offline reload). Grep the log for 'PURGEAPI:'."""
	try:
		from gui.mods.offhangar.logging import _DBG as _d
		if not _d[0]:
			return
	except Exception:
		return
	import BigWorld
	def _L(*a):
		try:
			from gui.mods.offhangar.logging import LOG_DEBUG as _ld
			_ld('PURGEAPI:', *a)
		except Exception:
			pass
	try:
		import ResMgr
		_L('ResMgr attrs', [n for n in dir(ResMgr) if not n.startswith('__')])
		_L('ResMgr.purge doc', repr(getattr(getattr(ResMgr, 'purge', None), '__doc__', None)))
	except Exception as e:
		_L('ResMgr err', e)
	# Every BigWorld attr whose name hints at texture/memory/cache/reload, with docs.
	try:
		_kw = ('texture', 'reload', 'cache', 'memory', 'flush', 'purge', 'release', 'stream', 'mip')
		for _n in dir(BigWorld):
			if any(k in _n.lower() for k in _kw):
				try:
					_f = getattr(BigWorld, _n, None)
					_L('BW.' + _n, repr(getattr(_f, '__doc__', None))[:220])
				except Exception:
					pass
	except Exception as e:
		_L('BW dir err', e)
	try:
		import Moo
		_L('Moo attrs', [n for n in dir(Moo) if not n.startswith('__')])
	except Exception:
		_L('Moo: not importable')


try:
	import BigWorld as _bw_pa
	_bw_pa.callback(14.0, _offh_dump_purge_apis)
except Exception:
	pass


def _offh_dump_hangar_render(_state=[0]):
	"""DEBUG-ONLY: reveal HOW ClientHangarSpace renders its space (the proven
	'render an arbitrary space' pattern) so we can replicate it for battle maps
	without touching read-only _offh_bspace(). Retries every 5s until in the
	hangar, then dumps once: g_hangarSpace, its inner space object, its spaceID
	vs _offh_bspace(), every BigWorld space/render/camera API, and the camera.
	Grep the log for 'HSPACE:'."""
	try:
		from gui.mods.offhangar.logging import _DBG as _d
		if not _d[0]:
			return
	except Exception:
		return
	import BigWorld
	def _L(*a):
		try:
			from gui.mods.offhangar.logging import LOG_DEBUG as _ld
			_ld('HSPACE:', *a)
		except Exception:
			pass
	# In the hangar the ClientHangarSpace inner space object exists. (player.arena
	# is NOT usable to detect hangar - the offline account returns an arena STUB
	# always, never None. That bug made this never fire.) Retry until the hangar
	# space is up.
	try:
		_pl = BigWorld.player()
	except Exception:
		_pl = None
	_hangar_up = False
	try:
		from gui.Scaleform.utils.HangarSpace import g_hangarSpace as _hs0
		_hangar_up = _hs0 is not None and getattr(_hs0, '_HangarSpace__space', None) is not None
	except Exception:
		_hangar_up = False
	if (_pl is None or not _hangar_up) and _state[0] < 20:
		_state[0] += 1
		try: BigWorld.callback(5.0, _offh_dump_hangar_render)
		except Exception: pass
		return
	# In hangar (or gave up waiting after ~100s) -> dump anyway for data.
	_L('hangar_detected', _hangar_up, 'retries', _state[0])
	_L('=== IN HANGAR - dumping render/space mechanism ===')
	try:
		_L('player', _pl.__class__.__name__, '_offh_bspace()', getattr(_pl, 'spaceID', None))
	except Exception as e:
		_L('player err', e)
	try:
		from gui.Scaleform.utils.HangarSpace import g_hangarSpace as _hs
		_L('g_hangarSpace type', _hs.__class__.__name__ if _hs else None)
		try:
			for _k, _v in _hs.__dict__.items():
				_L('  hs.'+str(_k), '=', repr(_v)[:140])
		except Exception as e:
			_L('hs dict err', e)
		_sp = getattr(_hs, '_HangarSpace__space', None)
		for _cand_attr in ('_HangarSpace__space', 'space', '_HangarSpace__spaceInited'):
			try: _L('  hs.'+_cand_attr, repr(getattr(_hs, _cand_attr, 'NONE'))[:140])
			except Exception: pass
		if _sp is not None:
			_L('inner space type', _sp.__class__.__name__)
			try:
				_L('  space dir', [n for n in dir(_sp) if not n.startswith('__')])
			except Exception: pass
			try:
				for _k, _v in _sp.__dict__.items():
					_L('  space.'+str(_k), '=', repr(_v)[:140])
			except Exception as e:
				_L('space dict err', e)
	except Exception as e:
		_L('g_hangarSpace err', e)
	try:
		_kw = ('space', 'world', 'render', 'active', 'camera', 'draw', 'scene', 'geometry')
		_L('BW space/render APIs', [n for n in dir(BigWorld) if any(k in n.lower() for k in _kw)])
	except Exception as e:
		_L('BW dir err', e)
	try:
		_cam = BigWorld.camera()
		_L('camera type', _cam.__class__.__name__ if _cam else None)
		if _cam is not None:
			_L('  camera space-ish attrs', [n for n in dir(_cam) if 'space' in n.lower()])
			for _a2 in ('spaceID', 'space'):
				try: _L('  camera.'+_a2, getattr(_cam, _a2, 'NONE'))
				except Exception: pass
	except Exception as e:
		_L('camera err', e)
	_L('=== dump done ===')


try:
	import BigWorld as _bw_hs_sched
	_bw_hs_sched.callback(8.0, _offh_dump_hangar_render)
except Exception:
	pass


def _offh_veh_excluded(v):
	"""Bots skip removed/hidden tanks: WG tags pulled vehicles 'secret' (e.g.
	usa:T23, removed from the 0.8.2 tree). Data-driven so any future removed
	tank drops out of the bot pool automatically."""
	try:
		_t = v['tags']
	except Exception:
		_t = ()
	if 'secret' in _t:
		return True
	try:
		if v['name'] == 'usa:T23':
			return True
	except Exception:
		pass
	return False


def _offh_ai_director(player):
	"""Create one deterministic planner for this battle on the sim authority."""
	client = getattr(player, '_offhangar_network_client', None)
	if client is not None:
		try:
			from gui.mods.offhangar.network_battle import network_is_authority
			if not network_is_authority(player):
				return None
		except Exception:
			return None
	director = globals().get('g_offh_bot_director')
	if director is not None:
		return director
	from gui.mods.offhangar.bot_ai import BattleDirector
	map_name = globals().get('g_offh_battle_mapname', '')
	if not map_name:
		try:
			map_name = player.arena.arenaType.geometryName
		except Exception:
			map_name = ''
	bases = {}
	for team, positions in (globals().get('g_offline_bases', {}) or {}).items():
		if positions:
			point = positions[0]
			bases[int(team)] = (float(point.x), float(point.z))
	seed = globals().get('g_offh_battle_gen', 0) or 0
	try:
		if client is not None and getattr(client, 'round_id', None) is not None:
			seed = client.round_id
	except Exception:
		pass
	baked_graph = globals().get('g_offh_baked_navigation_graph')
	if baked_graph is None:
		try:
			from gui.mods.offhangar.prebaked_navigation import load_graph
			baked_graph = load_graph(map_name)
			globals()['g_offh_baked_navigation_graph'] = baked_graph
		except Exception as error:
			_offh_ai_navigation_failure('load', error)
	director = BattleDirector(map_name, seed, bases,
	                          globals().get('g_offline_bounds'),
	                          (baked_graph or {}).get('routes'))
	globals()['g_offh_bot_director'] = director
	LOG_DEBUG('OfflineBattle.SMART_AI map=%s seed=%s tactical=%s' % (
		director.map_name, str(seed), str(director.map_data is not None)))
	return director


def _offh_ai_navigation_failure(stage, error):
	"""Report navigation failures loudly without disabling tactical bot AI."""
	key = 'g_offh_ai_navigation_error_' + str(stage)
	if globals().get(key, False):
		return
	globals()[key] = True
	try:
		import traceback
		detail = traceback.format_exc()
	except Exception:
		detail = '<traceback unavailable>'
	LOG_ERROR('OfflineBattle.SMART_AI navigation failure stage=%s error=%s' % (
		str(stage), str(error)))
	LOG_ERROR(detail)
	try:
		from gui.SystemMessages import SM_TYPE, pushMessage
		message = ('Baked navigation failed at %s; bots are using the safe fallback. '
		           'See python.log.' % str(stage))
		pushMessage(message.encode('utf-8'), SM_TYPE.Error)
	except Exception:
		pass


def _offh_ai_navigator(director):
	"""Create the shared terrain graph used below the strategic director."""
	navigator = globals().get('g_offh_terrain_navigator')
	if navigator is not None:
		return navigator
	if globals().get('g_offh_ai_navigation_disabled', False):
		return None
	import BigWorld, Math, math
	from gui.mods.offhangar.bot_ai_navigation import TerrainNavigator
	baked_graph = globals().get('g_offh_baked_navigation_graph')
	if baked_graph is None:
		try:
			from gui.mods.offhangar.prebaked_navigation import load_graph
			baked_graph = load_graph(getattr(director, 'map_name', ''))
			globals()['g_offh_baked_navigation_graph'] = baked_graph
		except Exception as error:
			_offh_ai_navigation_failure('load', error)

	def _ground_probe(x, z, hint_y):
		# Stay on the current terrain layer. A long top-down ray can select a
		# bridge or roof while the tank is driving underneath it.
		probe_top = float(hint_y) + 8.0
		probe_bottom = float(hint_y) - 18.0
		for _unused in range(3):
			hit = BigWorld.wg_collideSegment(
				_offh_bspace(), Math.Vector3(x, probe_top, z),
				Math.Vector3(x, probe_bottom, z), 128)
			if hit is None:
				return None
			height = float(hit[0].y)
			if height <= float(hint_y) + 4.5:
				# Water is not part of the autonomous navigation mesh. A conservative
				# dry-only graph is preferable to routing a tank into a river or harbour.
				if _offh_water_depth(x, height, z) > _OFFH_AI_WATER_AVOID_DEPTH:
					return None
				return height
			probe_top = height - 0.35
		return None

	def _obstacle_probe(start, end, half_width):
		dx = float(end[0]) - float(start[0])
		dz = float(end[2]) - float(start[2])
		length = math.sqrt(dx * dx + dz * dz)
		if length < 0.1:
			return False
		lateral_x = dz / length
		lateral_z = -dx / length
		# One hull-height, three-lane sweep is enough for the coarse graph;
		# the per-frame feelers below retain their dual-height fine check.
		for height in (0.9,):
			for offset in (-float(half_width), 0.0, float(half_width)):
				start_ray = Math.Vector3(
					float(start[0]) + lateral_x * offset,
					float(start[1]) + height,
					float(start[2]) + lateral_z * offset)
				end_ray = Math.Vector3(
					float(end[0]) + lateral_x * offset,
					float(end[1]) + height,
					float(end[2]) + lateral_z * offset)
				if BigWorld.wg_collideSegment(
						_offh_bspace(), start_ray, end_ray, 128) is not None:
					return True
		return False

	if baked_graph is not None:
		try:
			navigator = TerrainNavigator(_ground_probe, _obstacle_probe,
			                             getattr(director, 'bounds', None), 18.0,
			                             baked_graph=baked_graph)
		except Exception as error:
			_offh_ai_navigation_failure('construct_baked', error)
			navigator = None
	else:
		navigator = None
	if navigator is None:
		try:
			navigator = TerrainNavigator(_ground_probe, _obstacle_probe,
			                             getattr(director, 'bounds', None), 18.0)
		except Exception as error:
			_offh_ai_navigation_failure('construct_runtime', error)
			globals()['g_offh_ai_navigation_disabled'] = True
			return None
	globals()['g_offh_terrain_navigator'] = navigator
	globals()['g_offh_ai_water_guard_total'] = 0
	globals()['g_offh_ai_edge_guard_total'] = 0
	if baked_graph is not None and navigator.grid.prebaked:
		LOG_NOTE('OfflineBattle.SMART_AI using baked navigation map=%s cell=%.1fm nodes=%d' % (
			getattr(director, 'map_name', ''), navigator.grid.cell_size,
			sum(1 for value in baked_graph.get('heights_mm', ())
			    if value is not None)))
	else:
		LOG_NOTE('OfflineBattle.SMART_AI using runtime navigation map=%s cell=18m' %
		         getattr(director, 'map_name', ''))
	return navigator


def _offh_stats_for(player=None):
	try:
		if player is None:
			import BigWorld
			player = BigWorld.player()
		return getattr(player, '_offhangar_battle_stats', None)
	except Exception:
		return None


def _offh_capture_vehicle_key(vehicle, player=None):
	"""Return the authority-stable identity used by capture accounting."""
	if vehicle is None:
		return None
	try:
		if player is None:
			import BigWorld
			player = BigWorld.player()
	except Exception:
		player = None
	if vehicle is player:
		server_id = getattr(player, '_offhangar_network_id', None)
		if server_id is not None:
			return 'human:%s' % server_id
		return 'vehicle:%s' % getattr(player, 'playerVehicleID', -1)
	server_id = getattr(vehicle, '_network_server_id', None)
	if server_id is not None:
		return 'human:%s' % server_id
	bot_id = getattr(vehicle, '_network_bot_id', None)
	if bot_id is not None:
		return 'bot:%s' % bot_id
	vehicle_id = getattr(vehicle, 'id', vehicle)
	if player is not None and vehicle_id == getattr(player, 'playerVehicleID', None):
		server_id = getattr(player, '_offhangar_network_id', None)
		if server_id is not None:
			return 'human:%s' % server_id
	return 'vehicle:%s' % vehicle_id


def _offh_capture_is_authority(player):
	client = getattr(player, '_offhangar_network_client', None)
	if client is None or not getattr(client, 'ready', False) or getattr(client, 'phase', None) != 'battle':
		return True
	try:
		from gui.mods.offhangar.network_battle import network_is_authority
		return network_is_authority(player)
	except Exception:
		return False


def _offh_capture_attacker_is_local(player, attacker):
	if attacker is None:
		return False
	if attacker is player:
		return True
	values = (
		getattr(player, 'playerVehicleID', None),
		getattr(player, '_offhangar_network_id', None),
	)
	if attacker in values:
		return True
	return (getattr(attacker, 'id', None) in values or
		getattr(attacker, '_network_server_id', None) in values)


def _offh_drop_capture_for_vehicle(vehicle, attacker_id=None, reason='damage'):
	"""Drop only this capturer's contribution after real HP/module damage."""
	try:
		import BigWorld
		player = BigWorld.player()
		if player is None or not _offh_capture_is_authority(player):
			return 0
		states = globals().get('g_base_capture')
		if not isinstance(states, dict):
			return 0
		vehicle_key = _offh_capture_vehicle_key(vehicle, player)
		if vehicle_key is None:
			return 0
		from gui.mods.offhangar import capture_rules
		dropped_total = 0
		changed = []
		for base_team in (1, 2):
			state = states.get(base_team)
			dropped = capture_rules.drop_vehicle(state, vehicle_key)
			if dropped:
				dropped_total += dropped
				changed.append((base_team, state))
		for base_team, state in changed:
			try:
				player.arena.onTeamBasePointsUpdate(
					base_team, 0, int(state.get('points', 0) or 0),
					bool(state.get('stopped', False)))
			except Exception:
				pass
		if dropped_total <= 0:
			return 0
		if _offh_capture_attacker_is_local(player, attacker_id):
			try:
				from gui.mods.offhangar import battle_feedback
				battle_feedback.record_dropped_capture(
					_offh_stats_for(player), dropped_total)
			except Exception:
				pass
		try:
			from gui.mods.offhangar.network_battle import send_authoritative_rules
			send_authoritative_rules(player, states)
		except Exception:
			pass
		LOG_NOTE('CAPTURE RESET vehicle=%s dropped=%d reason=%s' % (
			vehicle_key, dropped_total, str(reason or 'damage')))
		return dropped_total
	except Exception as error:
		LOG_DEBUG('Capture reset failed: %s' % str(error))
		return 0


def apply_network_capture_damage(player, target_mock, attacker_id=None,
		damage=0, critical=False, reason='network hit'):
	"""Network event bridge used by the elected capture authority."""
	if max(0, int(damage or 0)) <= 0 and not bool(critical):
		return 0
	return _offh_drop_capture_for_vehicle(
		target_mock, attacker_id, reason)


def _offh_vehicle_message_label(player, vehicle_id, fallback='Unknown'):
	"""Return the retail battle label without mutating the arena roster."""
	try:
		info = getattr(getattr(player, 'arena', None), 'vehicles', {}).get(
			int(vehicle_id), {}) or {}
		try:
			from gui import BattleContext
			label = BattleContext.g_battleContext.getFullPlayerName(
				vData=info, showClan=False)
			if label:
				return label
		except Exception:
			pass
		name = info.get('name')
		if name:
			return name
	except Exception:
		pass
	return fallback


def _offh_scout_event(player, event_name, target_id):
	"""Use the stock 0.8.2 personal-message path for spotting feedback."""
	try:
		from constants import SCOUT_EVENT_TYPE
		event_type = getattr(SCOUT_EVENT_TYPE, str(event_name))
		handler = getattr(player, 'onScoutEvent', None)
		if callable(handler):
			handler(event_type, int(target_id))
			return True
		# Offline battles retain the Account entity rather than receiving a real
		# Avatar. Reproduce Avatar.onScoutEvent's stock 0.8.2 presentation path.
		message_types = {
			'SPOTTED': 'ENEMY_SPOTTED',
			'HIT_ASSIST': 'ENEMY_SPOTTED_HIT',
			'KILL_ASSIST': 'ENEMY_SPOTTED_KILLED',
		}
		message_type = message_types.get(str(event_name))
		if message_type is None:
			return False
		from gui import WindowsManager
		battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
		panel = getattr(battle, 'pMsgsPanel', None) if battle is not None else None
		if panel is None:
			return False
		name = _offh_vehicle_message_label(player, target_id, 'Enemy')
		panel.showMessage(message_type, {'entity': name}, (('entity', int(target_id)),))
		return True
	except Exception as error:
		LOG_DEBUG('Scout event presentation failed: %s' % str(error))
		return False


def _offh_record_direct_spot(player, target_mock, now):
	try:
		target_mock._offh_spotted_by_player_until = float(now) + 5.0
		from gui.mods.offhangar import battle_feedback
		if battle_feedback.record_spotted(
				_offh_stats_for(player), getattr(target_mock, 'id', -1)):
			_offh_scout_event(player, 'SPOTTED', getattr(target_mock, 'id', -1))
	except Exception as error:
		LOG_DEBUG('Direct spotting feedback failed: %s' % str(error))


def _offh_record_spot_assist(player, target_mock, damage, dead=False):
	try:
		import BigWorld
		if (float(getattr(target_mock, '_offh_spotted_by_player_until', 0.0) or 0.0) <
				BigWorld.time() or _offh_is_ally(target_mock)):
			return False
		damage = max(0, int(damage or 0))
		if damage <= 0:
			return False
		from gui.mods.offhangar import battle_feedback
		battle_feedback.record_assist(
			_offh_stats_for(player), getattr(target_mock, 'id', -1), damage, dead)
		_offh_scout_event(
			player, 'KILL_ASSIST' if dead else 'HIT_ASSIST',
			getattr(target_mock, 'id', -1))
		return True
	except Exception as error:
		LOG_DEBUG('Spotting assist feedback failed: %s' % str(error))
		return False


def record_network_combat_stats(player, attacker_is_local, target_is_local,
		target_mock, damage, shot_result=2, dead=False):
	"""Record one server-accepted LAN hit exactly once on the local client."""
	try:
		from gui.mods.offhangar import battle_feedback
		stats = _offh_stats_for(player)
		if attacker_is_local and target_mock is not None and not _offh_is_ally(target_mock):
			battle_feedback.record_outgoing_hit(
				stats, getattr(target_mock, 'id', -1), damage, shot_result, dead)
		elif target_is_local:
			battle_feedback.record_incoming_hit(stats, damage)
	except Exception as error:
		LOG_DEBUG('LAN combat statistics failed: %s' % str(error))


def record_network_spot_assist(player, target_mock, damage, dead=False):
	return _offh_record_spot_assist(player, target_mock, damage, dead)


def _offh_has_sixth_sense(player):
	cached = getattr(player, '_offhangar_has_sixth_sense', None)
	if cached is not None:
		return bool(cached)
	found = False
	try:
		from CurrentVehicle import g_currentVehicle
		item = getattr(g_currentVehicle, 'item', None)
		for entry in (getattr(item, 'crew', ()) or ()):
			tankman = entry[1] if isinstance(entry, tuple) and len(entry) == 2 else entry
			if tankman is None:
				continue
			skills = getattr(tankman, 'skills', None)
			if skills is None:
				skills = getattr(getattr(tankman, 'descriptor', None), 'skills', ())
			for skill in (skills or ()):
				name = str(getattr(skill, 'name', skill)).lower()
				if 'sixthsense' in name:
					found = True
					break
			if found:
				break
	except Exception:
		found = False
	player._offhangar_has_sixth_sense = bool(found)
	return bool(found)


def _offh_update_sixth_sense(player, visible_to_enemy, now):
	"""Schedule the native indicator on a new enemy observation."""
	try:
		import BigWorld
		now = float(now)
		was_observed = now < float(
			getattr(player, '_offhangar_observed_until', 0.0) or 0.0)
		if not visible_to_enemy:
			return
		player._offhangar_observed_until = now + 5.0
		if was_observed or not _offh_has_sixth_sense(player):
			return
		delay = 3.0
		try:
			from items import tankmen
			delay = float(tankmen.getSkillsConfig().get(
				'commander_sixthSense', {}).get('delay', delay) or delay)
		except Exception:
			pass
		generation = globals().get('g_offh_battle_gen', 0)
		def _show_sixth_sense(_player=player, _generation=generation):
			try:
				if (BigWorld.player() is not _player or
						globals().get('g_offh_battle_gen', 0) != _generation or
						getattr(getattr(_player, 'arena', None), 'period', 0) != 3 or
						getattr(_player, '_is_dead', False)):
					return
				from gui import WindowsManager
				battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
				if battle is not None and hasattr(battle, 'showSixthSenseIndicator'):
					battle.showSixthSenseIndicator(True)
			except Exception as error:
				LOG_DEBUG('Sixth Sense presentation failed: %s' % str(error))
		_offh_battle_callback(delay, _show_sixth_sense)
	except Exception as error:
		LOG_DEBUG('Sixth Sense scheduling failed: %s' % str(error))


def _offh_ai_driver():
	"""Return the per-battle local driver shared by every simulated bot."""
	driver = globals().get('g_offh_local_driver')
	if driver is not None:
		return driver
	from gui.mods.offhangar.bot_ai_driver import LocalDriver
	driver = LocalDriver()
	globals()['g_offh_local_driver'] = driver
	LOG_DEBUG('OfflineBattle.SMART_AI local driver enabled')
	return driver


def _offh_ai_hull_dims(descriptor):
	"""Return OBB half length/width from the native chassis collision body."""
	cache = globals().setdefault('g_offh_ai_hull_dims', {})
	key = id(descriptor)
	if key in cache:
		return cache[key]
	half_length = 3.5
	half_width = 1.7
	try:
		from gui.mods.offhangar.vehicle_collision import chassis_shape
		shape = chassis_shape(descriptor)
		half_width = float(shape[0])
		half_length = float(shape[1])
	except Exception:
		pass
	cache[key] = (half_length, half_width)
	return cache[key]


def _offh_mat_info_for_segment_hit(space_id, hit_point, surface_normal):
	"""Resolve one static contact with the retail point/normal material probe."""
	try:
		import BigWorld
		normal = surface_normal.scale(1.0)
		if normal.length <= 0.001:
			return None
		normal.normalise()
		segment_a = hit_point - normal.scale(3.0)
		segment_b = hit_point + normal.scale(2.0)
		return BigWorld.wg_getMatInfoNearPoint(
			space_id, segment_a, segment_b, hit_point, lambda *args: False)
	except Exception:
		return None


def _offh_destructible_mat_passable(mat_info, vehicle=None, vehicle_speed=0.0,
		space_id=None):
	"""Return True only for contacts this vehicle can actually crush."""
	if mat_info is None:
		return False
	try:
		import AreaDestructibles
		unused_hit, unused_normal, chunk_id, item_index, mat_kind, filename = mat_info
		if int(mat_kind) < 71 or int(mat_kind) > 130:
			return False
		desc = AreaDestructibles.g_cache.getDescByFilename(filename)
		if not desc:
			return False
		destructible_type = desc['type']
		if destructible_type in (
				AreaDestructibles.DESTR_TYPE_TREE,
				AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
			health = float(desc.get('health', 0) or 0)
			return 10.0 <= health <= 1000.0
		if destructible_type == AreaDestructibles.DESTR_TYPE_FRAGILE:
			return True
		if destructible_type == AreaDestructibles.DESTR_TYPE_STRUCTURE:
			if vehicle is None:
				return False
			if space_id is None:
				space_id = _offh_bspace()
			return bool(_get_destr_authority().can_crush(
				vehicle, space_id, int(chunk_id), int(item_index),
				int(mat_kind), filename, abs(float(vehicle_speed))))
		return False
	except Exception:
		return False


def _offh_ai_corridor_segment_clear(space_id, start, end, vehicle=None,
		vehicle_speed=0.0):
	"""Treat crushable scenery as road while retaining solid BSP blockers."""
	try:
		import BigWorld
		hit = BigWorld.wg_collideSegment(space_id, start, end, 128)
		if hit is None:
			return True
		return _offh_destructible_mat_passable(
			_offh_mat_info_for_segment_hit(space_id, hit[0], hit[1]),
			vehicle, vehicle_speed, space_id)
	except Exception:
		return False


def _offh_ai_direction_clear(vehicle, absolute_yaw, now=None, space_id=None):
	"""Probe one hull-width movement corridor for the engine-free driver."""
	_perf_started = _offh_perf_start()
	try:
		import BigWorld, Math, math
		velocity = float(getattr(vehicle, '_veh_velocity', 0.0) or 0.0)
		speed = abs(velocity)
		_now = float(BigWorld.time() if now is None else now)
		_space_id = _offh_bspace() if space_id is None else space_id
		_cache_key = (
			int(math.floor(float(vehicle.position.x) * 2.0 + 0.5)),
			int(math.floor(float(vehicle.position.y) * 2.0 + 0.5)),
			int(math.floor(float(vehicle.position.z) * 2.0 + 0.5)),
			int(math.floor(float(absolute_yaw) * 24.0 + 0.5)),
			int(math.floor(speed * 0.5)), -1 if velocity < -0.05 else 1)
		_cache = getattr(vehicle, '_offh_ai_direction_cache', None)
		if not isinstance(_cache, dict):
			_cache = {}
			vehicle._offh_ai_direction_cache = _cache
		_cached = _cache.get(_cache_key)
		if _cached is not None and _now < float(_cached[0]):
			return bool(_cached[1])
		def _cached_result(value):
			value = bool(value)
			_cache[_cache_key] = (
				_now + (0.10 if value else 0.05), value)
			if len(_cache) > 24:
				for _old_key, _old_value in list(_cache.items()):
					if _now >= float(_old_value[0]):
						_cache.pop(_old_key, None)
				if len(_cache) > 24:
					_cache.clear()
					_cache[_cache_key] = (
						_now + (0.10 if value else 0.05), value)
			return value
		# A city turn needs only a local driving corridor.  The former 18-38 m
		# straight wall sweep looked through the next bend into a building and
		# rejected every candidate, even though the road immediately ahead was open.
		lookahead = max(8.0, min(20.0, 7.0 + speed * 1.2))
		# Water/drop detection remains long-range and follows the realised momentum
		# corridor below, so shortening the wall sweep does not shorten braking room.
		hazard_lookahead = max(14.0, min(38.0, 14.0 + speed * 2.2))
		ground_step = 3.0
		try:
			_unused_length, hull_half_width = _offh_ai_hull_dims(
				getattr(vehicle, 'typeDescriptor', None))
		except Exception:
			hull_half_width = 1.7
		corridor_half_width = max(1.4, min(2.0, float(hull_half_width) + 0.15))
		previous_y = float(vehicle.position.y)
		sine = math.sin(float(absolute_yaw))
		cosine = math.cos(float(absolute_yaw))
		start_position = (
			float(vehicle.position.x), previous_y, float(vehicle.position.z))
		baked_end = (
			start_position[0] + sine * lookahead, previous_y,
			start_position[2] + cosine * lookahead)
		_baked_drive_clear = _offh_ai_baked_open_corridor(
			start_position, baked_end)
		_baked_motion_clear = True
		if _baked_drive_clear and speed > 1.25:
			motion_heading = float(vehicle.yaw)
			if velocity < -0.05:
				motion_heading += math.pi
			delta_yaw = float(absolute_yaw) - motion_heading
			while delta_yaw > math.pi: delta_yaw -= math.pi * 2.0
			while delta_yaw < -math.pi: delta_yaw += math.pi * 2.0
			motion_yaw = motion_heading + delta_yaw * 0.45
			motion_distance = min(hazard_lookahead, max(9.0, speed * 2.0))
			baked_motion_end = (
				start_position[0] + math.sin(motion_yaw) * motion_distance,
				previous_y,
				start_position[2] + math.cos(motion_yaw) * motion_distance)
			_baked_motion_clear = _offh_ai_baked_open_corridor(
				start_position, baked_motion_end)
		if _baked_drive_clear and _baked_motion_clear:
			# The four-metre bake already tested grade, deep water and a 2.15 m
			# obstacle margin. Requiring one complete neighbouring-cell halo makes
			# this stricter than an ordinary A* edge and lets open ground avoid the
			# legacy client's many synchronous BSP rays. Narrow or ambiguous places
			# still continue through the exact probe path below.
			_offh_perf_count('direction_baked')
			return _cached_result(True)
		_offh_perf_count('direction_exact')
		current_water = _offh_water_depth(
			float(vehicle.position.x), previous_y, float(vehicle.position.z))
		wet_escape = current_water > _OFFH_AI_WATER_AVOID_DEPTH
		last_water = current_water
		lateral_x = cosine
		lateral_z = -sine
		ground_points = []
		distance = ground_step
		while distance < lookahead:
			ground_points.append(distance)
			distance += ground_step
		ground_points.append(lookahead)
		previous_distance = 0.0
		ground_profile = [(0.0, float(vehicle.position.y))]
		for distance in ground_points:
			x = float(vehicle.position.x) + sine * distance
			z = float(vehicle.position.z) + cosine * distance
			run = distance - previous_distance
			probe_up = max(4.5, run * 0.52)
			probe_down = max(5.0, run * 0.45)
			ground = BigWorld.wg_collideSegment(
				_space_id, Math.Vector3(x, previous_y + probe_up, z),
				Math.Vector3(x, previous_y - probe_down, z), 128)
			if ground is None:
				return _cached_result(_offh_ai_probe_reject(vehicle, 'terrain'))
			y = float(ground[0].y)
			water_depth = _offh_water_depth(x, y, z)
			if wet_escape:
				# A tank already touching water may take only a route that never gets
				# deeper and finishes measurably closer to dry ground.
				if water_depth > current_water + 0.10:
					return _cached_result(_offh_ai_probe_reject(vehicle, 'water'))
			else:
				if water_depth > _OFFH_AI_WATER_AVOID_DEPTH:
					return _cached_result(_offh_ai_probe_reject(vehicle, 'water'))
			last_water = water_depth
			delta = y - previous_y
			if delta > run * 0.48 or delta < -run * 0.38:
				return _cached_result(_offh_ai_probe_reject(vehicle, 'terrain'))
			previous_y = y
			previous_distance = distance
			ground_profile.append((distance, y))
		if wet_escape and last_water > max(
				_OFFH_AI_WATER_AVOID_DEPTH, current_water - 0.15):
			return _cached_result(_offh_ai_probe_reject(vehicle, 'water'))
		# Turning tanks initially continue along a blend of the old and requested
		# headings. Probe that momentum corridor too; a safe straight ray on the far
		# side of a turn must not hide water directly under the actual arc.
		if not wet_escape and speed > 1.25:
			motion_heading = float(vehicle.yaw)
			if velocity < -0.05:
				motion_heading += math.pi
			delta_yaw = float(absolute_yaw) - motion_heading
			while delta_yaw > math.pi: delta_yaw -= math.pi * 2.0
			while delta_yaw < -math.pi: delta_yaw += math.pi * 2.0
			motion_yaw = motion_heading + delta_yaw * 0.45
			motion_distance = min(hazard_lookahead, max(9.0, speed * 2.0))
			motion_step = 4.0
			probe_distance = motion_step
			motion_y = float(vehicle.position.y)
			while probe_distance <= motion_distance + 0.01:
				mx = float(vehicle.position.x) + math.sin(motion_yaw) * probe_distance
				mz = float(vehicle.position.z) + math.cos(motion_yaw) * probe_distance
				motion_ground = BigWorld.wg_collideSegment(
					_space_id, Math.Vector3(mx, motion_y + 6.0, mz),
					Math.Vector3(mx, motion_y - 8.0, mz), 128)
				if motion_ground is None:
					return _cached_result(_offh_ai_probe_reject(vehicle, 'terrain'))
				motion_y = float(motion_ground[0].y)
				if _offh_water_depth(mx, motion_y, mz) > _OFFH_AI_WATER_AVOID_DEPTH:
					return _cached_result(_offh_ai_probe_reject(vehicle, 'water'))
				probe_distance += motion_step
		# Check the two outer tracks at the selected local endpoint.  The final-pose
		# guard below catches all realised motion, while this cheaper early veto lets
		# LocalDriver choose a dry candidate before a track hangs over the bank.
		for offset in (-corridor_half_width, corridor_half_width):
			x = (float(vehicle.position.x) + sine * lookahead +
			     lateral_x * offset)
			z = (float(vehicle.position.z) + cosine * lookahead +
			     lateral_z * offset)
			ground = BigWorld.wg_collideSegment(
				_space_id, Math.Vector3(x, previous_y + 6.0, z),
				Math.Vector3(x, previous_y - 12.0, z), 128)
			if ground is None:
				return _cached_result(_offh_ai_probe_reject(vehicle, 'terrain'))
			if _offh_water_depth(x, float(ground[0].y), z) > _OFFH_AI_WATER_AVOID_DEPTH:
				return _cached_result(_offh_ai_probe_reject(vehicle, 'water'))
		# Sweep the complete supported corridor at two hull heights. A single long
		# chord can cut through the crest of a perfectly drivable convex hill, so a
		# hit is verified against short terrain-following pieces before it becomes an
		# obstacle veto. Fragiles remain locally passable; structures require the
		# same retail mass/speed/module proof used by the physical contact path.
		final_x = float(vehicle.position.x) + sine * lookahead
		final_z = float(vehicle.position.z) + cosine * lookahead
		for height in (0.9, 1.5):
			for offset in (-corridor_half_width, 0.0, corridor_half_width):
				start = Math.Vector3(
					float(vehicle.position.x) + lateral_x * offset,
					float(vehicle.position.y) + height,
					float(vehicle.position.z) + lateral_z * offset)
				end = Math.Vector3(
					final_x + lateral_x * offset, previous_y + height,
					final_z + lateral_z * offset)
				if _offh_ai_corridor_segment_clear(
						_space_id, start, end, vehicle, speed):
					continue
				piece_clear = True
				last_distance, last_y = ground_profile[0]
				for piece_distance, piece_y in ground_profile[1:]:
					piece_start = Math.Vector3(
						float(vehicle.position.x) + sine * last_distance + lateral_x * offset,
						last_y + height,
						float(vehicle.position.z) + cosine * last_distance + lateral_z * offset)
					piece_end = Math.Vector3(
						float(vehicle.position.x) + sine * piece_distance + lateral_x * offset,
						piece_y + height,
						float(vehicle.position.z) + cosine * piece_distance + lateral_z * offset)
					if not _offh_ai_corridor_segment_clear(
							_space_id, piece_start, piece_end, vehicle, speed):
						piece_clear = False
						break
					last_distance, last_y = piece_distance, piece_y
				if not piece_clear:
					return _cached_result(_offh_ai_probe_reject(vehicle, 'obstacle'))
		return _cached_result(True)
	except Exception:
		return _offh_ai_probe_reject(vehicle, 'error')
	finally:
		_offh_perf_stop('direction', _perf_started)


def _offh_ai_class_tag(mock, descriptor):
	if mock is not None:
		cached = getattr(mock, '_offh_ai_class_tag', None)
		if cached:
			return cached
	try:
		class_tag = _offh_ai_vehicle_profile(
			mock, descriptor).get('class_tag', 'mediumTank')
	except Exception:
		class_tag = 'mediumTank'
	if mock is not None:
		try:
			mock._offh_ai_class_tag = class_tag
		except Exception:
			pass
	return class_tag


def _offh_ai_vehicle_profile(mock, descriptor):
	"""Cache immutable tactical descriptor data on the battle vehicle."""
	if mock is not None:
		cached = getattr(mock, '_offh_ai_vehicle_profile', None)
		if isinstance(cached, dict):
			return cached
	from gui.mods.offhangar.bot_ai import build_vehicle_profile
	profile = build_vehicle_profile(descriptor)
	if mock is not None:
		try:
			mock._offh_ai_vehicle_profile = profile
		except Exception:
			pass
	return profile


def _offh_spot_get(container, key, default=None):
	try:
		if hasattr(container, 'get'):
			return container.get(key, default)
		return getattr(container, key, default)
	except Exception:
		return default


def _offh_spot_component_name(id_map, component_id):
	try:
		for name, value in id_map.iteritems():
			if int(value) == int(component_id):
				return str(name)
	except Exception:
		pass
	return None


def _offh_spot_resource_profile(descriptor):
	"""Read the 0.8.2 fields retained in resources and legacy data.

	The release client strips the server-owned base invisibility values.  Prefer
	an unstripped development resource when present, then the per-vehicle legacy
	dataset.  A missing vehicle is a data error: silently substituting a class
	average would make two different tanks share fabricated concealment values.
	"""
	from gui.mods.offhangar import spotting, vehicle_camouflage
	from gui.mods.offhangar.logging import LOG_NOTE as _spot_log_note
	from gui.mods.offhangar.logging import LOG_ERROR as _spot_log_error
	type_name = str(_offh_spot_get(
		getattr(descriptor, 'type', None), 'name', 'unknown:unknown'))
	turret_id = _offh_spot_get(getattr(descriptor, 'turret', None), 'id', (0, 0))
	gun_id = _offh_spot_get(getattr(descriptor, 'gun', None), 'id', (0, 0))
	camouflages = getattr(descriptor, 'camouflages', ()) or ()
	key = (type_name, tuple(turret_id or (0, 0)), tuple(gun_id or (0, 0)),
	       repr(camouflages))
	cache = globals().setdefault('g_offh_spot_resource_profiles', {})
	if key in cache:
		return cache[key]
	moving = None
	still = None
	base_source = None
	legacy_values = vehicle_camouflage.camouflage_for_vehicle(type_name)
	if legacy_values is not None:
		moving, still = legacy_values
		base_source = 'tanks.gg-v09171'
	exact_base = False
	turret_factor = float(_offh_spot_get(
		getattr(descriptor, 'turret', None), 'invisibilityFactor', 1.0) or 1.0)
	shot_factor = float(_offh_spot_get(
		getattr(descriptor, 'gun', None), 'invisibilityFactorAtShot', 0.25) or 0.25)
	try:
		import ResMgr, nations
		from items import vehicles
		nation_id = int(getattr(descriptor.type, 'id', (0, 0))[0])
		nation_name = nations.AVAILABLE_NAMES[nation_id]
		vehicle_name = type_name.split(':', 1)[-1].lower()
		vehicle_path = '%s%s/%s.xml' % (
			vehicles._VEHICLE_TYPE_XML_PATH, nation_name, vehicle_name)
		section = ResMgr.openSection(vehicle_path)
		if section is not None and section.has_key('invisibility'):
			moving = float(section.readFloat(
				'invisibility/moving', float(moving or 0.0)))
			still = float(section.readFloat(
				'invisibility/still', float(still or 0.0)))
			exact_base = True
			base_source = 'resource'
		turret_name = _offh_spot_component_name(
			vehicles.g_cache.turretIDs(nation_id), turret_id[1])
		gun_name = _offh_spot_component_name(
			vehicles.g_cache.gunIDs(nation_id), gun_id[1])
		turret_section = None
		if section is not None and turret_name:
			turret_section = section['turrets0/%s' % turret_name]
			if (turret_section is not None and
					turret_section.has_key('invisibilityFactor')):
				turret_factor = float(turret_section.readFloat(
					'invisibilityFactor', turret_factor))
		local_gun = None
		if turret_section is not None and gun_name:
			local_gun = turret_section['guns/%s' % gun_name]
		if local_gun is not None and local_gun.has_key('invisibilityFactorAtShot'):
			shot_factor = float(local_gun.readFloat(
				'invisibilityFactorAtShot', shot_factor))
		elif gun_name:
			guns = ResMgr.openSection('%s%s/components/guns.xml' % (
				vehicles._VEHICLE_TYPE_XML_PATH, nation_name))
			shared_gun = guns['shared/%s' % gun_name] if guns is not None else None
			if (shared_gun is not None and
					shared_gun.has_key('invisibilityFactorAtShot')):
				shot_factor = float(shared_gun.readFloat(
					'invisibilityFactorAtShot', shot_factor))
	except Exception:
		pass
	paint_factor = 1.0
	try:
		from items import vehicles
		customization = vehicles.g_cache.customization(descriptor.type.id[0])
		for camo in camouflages:
			if camo is None or camo[0] is None:
				continue
			camo_descr = customization['camouflages'].get(camo[0])
			if camo_descr is not None:
				paint_factor = max(paint_factor, float(
					camo_descr.get('invisibilityFactor', 1.0) or 1.0))
	except Exception:
		pass
	if moving is None or still is None:
		message = ('SPOTTING: missing per-vehicle camouflage data for %s; '
		           'battle visibility cannot be simulated truthfully' % type_name)
		_spot_log_error(message)
		raise ValueError(message)
	profile = {
		'moving': moving,
		'still': still,
		'exact_base': exact_base,
		'base_source': base_source,
		'turret_factor': max(0.0, turret_factor),
		'shot_factor': max(0.0, min(1.0, shot_factor)),
		'paint_factor': max(1.0, paint_factor),
	}
	if (base_source == 'tanks.gg-v09171' and
			not globals().get('g_offh_spot_dataset_logged', False)):
		globals()['g_offh_spot_dataset_logged'] = True
		_spot_log_note('SPOTTING: per-vehicle camouflage dataset active source=%s '
		         'version=%s coverage=%d/%d' % (
			vehicle_camouflage.DATA_SOURCE, vehicle_camouflage.DATA_VERSION,
			vehicle_camouflage.DATA_COVERED_VEHICLES,
			vehicle_camouflage.DATA_LOCAL_VEHICLES))
	if (str(type_name or '').lower() in vehicle_camouflage.APPROXIMATE_ALIASES):
		logged = globals().setdefault('g_offh_spot_aliases_logged', set())
		if type_name not in logged:
			logged.add(type_name)
			_spot_log_note('SPOTTING: using nearest surviving camouflage descriptor for %s' %
			         type_name)
	cache[key] = profile
	return profile


def _offh_spot_device_profile(descriptor):
	result = {'vision_factor': 1.0, 'binocular_factor': 1.0,
	          'binocular_delay': 3.0, 'camouflage_net_factor': 1.0,
	          'camouflage_net_delay': 3.0}
	try:
		result['vision_factor'] = float(
			descriptor.miscAttrs.get('circularVisionRadiusFactor', 1.0) or 1.0)
	except Exception:
		pass
	for device in (getattr(descriptor, 'optionalDevices', ()) or ()):
		if device is None:
			continue
		name = str(getattr(device, 'name', '') or '').lower()
		factor = float(getattr(device, 'factor', 1.0) or 1.0)
		if 'stereoscope' in name:
			result['binocular_factor'] = max(result['binocular_factor'], factor)
			result['binocular_delay'] = float(getattr(
				device, 'activateWhenStillSec', 3.0) or 3.0)
		elif 'camouflagenet' in name:
			result['camouflage_net_factor'] = max(
				result['camouflage_net_factor'], factor)
			result['camouflage_net_delay'] = float(getattr(
				device, 'activateWhenStillSec', 3.0) or 3.0)
	return result


def _offh_spot_skill(tankman, wanted):
	for skill in (getattr(tankman, 'skills', ()) or ()):
		name = str(getattr(skill, 'name', skill) or '').lower()
		if name != wanted.lower():
			continue
		if not bool(getattr(skill, 'isActive', True)):
			return 0.0
		return float(getattr(skill, 'level', 100.0) or 0.0)
	return 0.0


def _offh_spot_player_crew(player):
	generation = int(globals().get('g_offh_battle_gen', 0) or 0)
	cached = globals().get('g_offh_spot_player_crew')
	if cached is not None and cached.get('generation') == generation:
		return cached
	result = {'generation': generation, 'commander_level': 100.0,
	          'recon_level': 0.0, 'situational_level': 0.0,
	          'camouflage_level': 0.0}
	try:
		from CurrentVehicle import g_currentVehicle
		item = getattr(g_currentVehicle, 'item', None)
		crew = [entry[1] if isinstance(entry, tuple) and len(entry) == 2 else entry
		        for entry in (getattr(item, 'crew', ()) or ())]
		crew = [tankman for tankman in crew if tankman is not None]
		camo_levels = []
		for tankman in crew:
			role = str(getattr(getattr(tankman, 'descriptor', None), 'role', '') or '')
			if role == 'commander':
				try:
					result['commander_level'] = float(tankman.realRoleLevel[0])
				except Exception:
					result['commander_level'] = float(
						getattr(tankman, 'roleLevel', 100.0) or 100.0)
			# Combined-role crew members may carry either skill even when their
			# primary descriptor role is Commander rather than Radio Operator.
			result['recon_level'] = max(result['recon_level'], _offh_spot_skill(
				tankman, 'commander_eagleEye'))
			result['situational_level'] = max(
				result['situational_level'], _offh_spot_skill(
					tankman, 'radioman_finder'))
			camo_levels.append(_offh_spot_skill(tankman, 'camouflage'))
		if camo_levels:
			result['camouflage_level'] = sum(camo_levels) / float(len(camo_levels))
	except Exception:
		pass
	globals()['g_offh_spot_player_crew'] = result
	return result


def _offh_spot_is_local_player(player, vehicle):
	return (vehicle is not None and int(getattr(vehicle, 'id', -1)) ==
	        int(getattr(player, 'playerVehicleID', -2)))


def _offh_spot_loadout(player, vehicle, descriptor):
	result = _offh_spot_device_profile(descriptor)
	crew_device_bonus = 0.0
	try:
		crew_device_bonus = float(
			descriptor.miscAttrs.get('crewLevelIncrease', 0.0) or 0.0)
	except Exception:
		pass
	result.update({'commander_level': 100.0 + crew_device_bonus, 'recon_level': 0.0,
	               'situational_level': 0.0, 'camouflage_level': 0.0})
	if _offh_spot_is_local_player(player, vehicle):
		result.update(_offh_spot_player_crew(player))
		# 0.8.2 Tankman.realRoleLevel reports the ventilation bonus in its
		# breakdown but omits it from the returned level; the descriptor owns it.
		result['commander_level'] += crew_device_bonus
	return result


def _offh_spot_motion(vehicle, now):
	from gui.mods.offhangar import spotting
	if vehicle is None:
		return False, spotting.STILL_DEVICE_DELAY_SECONDS
	speed = abs(float(getattr(vehicle, '_veh_velocity', 0.0) or 0.0))
	moving = speed > spotting.MOVING_SPEED_EPSILON
	if moving:
		still_since = float(now)
		vehicle._offh_spot_still_since = still_since
	else:
		# _MockVeh declares this field as None before the live battle clock exists.
		# hasattr() therefore succeeds even though there is no usable timestamp yet,
		# and the first contact/spotting pass used to abort the complete SMART_AI
		# frame with float(None). Treat missing and invalid state identically.
		try:
			still_since = float(getattr(vehicle, '_offh_spot_still_since', None))
		except (TypeError, ValueError):
			still_since = float(now)
			vehicle._offh_spot_still_since = still_since
	still_for = 0.0 if moving else max(
		0.0, float(now) - still_since)
	return moving, still_for


def _offh_spot_damage_vision_factor(vehicle, descriptor):
	"""Read crew/module vision penalties without depending on battle closures."""
	if vehicle is None:
		return 1.0
	factor = 1.0
	try:
		from gui.mods.offhangar import device_damage
		impaired = getattr(vehicle, '_crew_impaired', None) or ()
		factor *= float(device_damage.crew_stat_factor(impaired, 'vision'))
		factor *= float(device_damage.module_stat_factor(
			getattr(vehicle, 'devices_hp', None),
			getattr(vehicle, '_destroyed_devices', None), descriptor, 'vision'))
		return float(device_damage.clamp_vision_factor(factor))
	except Exception:
		return max(0.5, min(1.0, factor))


def _offh_ai_view_range(descriptor, vehicle=None, player=None, now=None):
	from gui.mods.offhangar import spotting
	radius = float(_offh_spot_get(
		getattr(descriptor, 'turret', None), 'circularVisionRadius', 400.0) or 400.0)
	loadout = _offh_spot_loadout(player, vehicle, descriptor) if player is not None else {
		'commander_level': 100.0, 'vision_factor': 1.0,
		'recon_level': 0.0, 'situational_level': 0.0,
		'binocular_factor': 1.0, 'binocular_delay': 3.0}
	still_active = False
	if vehicle is not None and now is not None:
		_unused_moving, still_for = _offh_spot_motion(vehicle, now)
		still_active = still_for >= float(loadout.get(
			'binocular_delay', spotting.STILL_DEVICE_DELAY_SECONDS) or
			spotting.STILL_DEVICE_DELAY_SECONDS)
	vision_factor = float(loadout.get('vision_factor', 1.0) or 1.0)
	vision_factor *= _offh_spot_damage_vision_factor(vehicle, descriptor)
	return spotting.effective_view_range(
		radius, loadout.get('commander_level', 100.0), vision_factor,
		loadout.get('recon_level', 0.0),
		loadout.get('situational_level', 0.0),
		loadout.get('binocular_factor', 1.0), still_active)


def _offh_ai_cached_view_range(descriptor, vehicle, player, now):
	"""Retain the original 2 Hz vision cadence while contacts are frame-sliced."""
	cache = getattr(vehicle, '_offh_spot_view_range_cache', None)
	if (isinstance(cache, tuple) and len(cache) == 2 and
			float(now) < float(cache[0])):
		return float(cache[1])
	value = _offh_ai_view_range(descriptor, vehicle, player, now)
	if vehicle is not None:
		try:
			vehicle._offh_spot_view_range_cache = (float(now) + 0.45, value)
		except Exception:
			pass
	return value


def _offh_spot_camouflage(player, vehicle, descriptor, now):
	from gui.mods.offhangar import spotting
	profile = _offh_spot_resource_profile(descriptor)
	loadout = _offh_spot_loadout(player, vehicle, descriptor)
	moving, still_for = _offh_spot_motion(vehicle, now)
	fired_recently = (float(now) - float(
		getattr(vehicle, '_offh_spot_last_shot', -999.0) or -999.0) <
		spotting.SHOT_CAMOUFLAGE_SECONDS)
	return spotting.effective_camouflage(
		profile['moving'], profile['still'], moving,
		loadout.get('camouflage_level', 0.0), profile['turret_factor'],
		profile['paint_factor'], loadout.get('camouflage_net_factor', 1.0),
		still_for >= float(loadout.get(
			'camouflage_net_delay', spotting.STILL_DEVICE_DELAY_SECONDS) or
			spotting.STILL_DEVICE_DELAY_SECONDS),
		profile['shot_factor'], fired_recently, 0.0)


def _offh_spot_fired_recently(vehicle, now):
	from gui.mods.offhangar import spotting
	return (float(now) - float(
		getattr(vehicle, '_offh_spot_last_shot', -999.0) or -999.0) <
		spotting.SHOT_CAMOUFLAGE_SECONDS)


def _offh_spot_foliage(player):
	"""Load the current map's shipped foliage index once per battle."""
	if 'g_offh_spot_foliage' in globals():
		return globals().get('g_offh_spot_foliage')
	map_name = globals().get('g_offh_battle_mapname', '')
	if not map_name:
		try:
			map_name = player.arena.arenaType.geometryName
		except Exception:
			map_name = ''
	try:
		from gui.mods.offhangar.prebaked_foliage import load_foliage
		foliage_map = load_foliage(map_name)
		if foliage_map is None:
			raise ValueError('no prebaked foliage for map %s' % str(map_name))
		globals()['g_offh_spot_foliage'] = foliage_map
		LOG_NOTE('SPOTTING: prebaked foliage active map=%s volumes=%d cells=%d' % (
			str(map_name), len(foliage_map.instances), len(foliage_map.cells)))
		return foliage_map
	except Exception as error:
		globals()['g_offh_spot_foliage'] = None
		if not globals().get('g_offh_spot_foliage_error', False):
			globals()['g_offh_spot_foliage_error'] = True
			LOG_ERROR('SPOTTING: foliage load failed: %s' % str(error))
			try:
				from gui.SystemMessages import SM_TYPE, pushMessage
				pushMessage(('Foliage concealment data failed to load; see python.log.'
					).encode('utf-8'), SM_TYPE.Error)
			except Exception:
				pass
		return None


def _offh_spot_detection_range(player, observer, target, now):
	from gui.mods.offhangar import spotting
	view_range = observer.get('_spot_view_range')
	if view_range is None:
		view_range = _offh_ai_cached_view_range(
			observer['descriptor'], observer.get('vehicle'), player, now)
		observer['_spot_view_range'] = view_range
	camouflage = target.get('_spot_camouflage')
	if camouflage is None:
		camouflage = _offh_spot_camouflage(
			player, target.get('vehicle'), target['descriptor'], now)
	foliage_map = _offh_spot_foliage(player)
	if foliage_map is not None:
		vehicle = target.get('vehicle')
		foliage_bonus = foliage_map.camouflage_bonus(
			observer['position'], target['position'],
			_offh_spot_fired_recently(vehicle, now))
		camouflage = spotting.clamp(
			float(camouflage) + float(foliage_bonus), 0.0, 0.95)
	return spotting.detection_distance(view_range, camouflage)


def _offh_spot_visible_for_player(player, target_vehicle, now=None):
	"""Evaluate one target once and share the result with render/network code."""
	try:
		import BigWorld
		if now is None:
			now = float(BigWorld.time())
	except Exception:
		if now is None:
			now = time.time()
	now = float(now)
	if target_vehicle is None or getattr(target_vehicle, 'position', None) is None:
		return False
	player_team = int(getattr(player, '_offhangar_team',
		getattr(player, '_offhangar_network_team', 1)) or 1)
	return _offh_spot_visible_to_team(
		player, target_vehicle, player_team, now, True)


def _offh_spot_visible_to_team(player, target_vehicle, observing_team, now,
			record_player_spot=False):
	"""Evaluate whether one team currently observes a vehicle."""
	if target_vehicle is None or getattr(target_vehicle, 'position', None) is None:
		return False
	observing_team = int(observing_team or 1)
	target_info = getattr(target_vehicle, 'publicInfo', None) or {}
	target_team = int(getattr(
		target_vehicle, '_bot_team', target_info.get('team', 2)) or 2)
	if target_team == observing_team:
		return True
	if (now - float(getattr(target_vehicle, '_offh_spot_eval_time', -999.0) or -999.0)
			< 0.45 and int(getattr(
				target_vehicle, '_offh_spot_eval_team', 0) or 0) == observing_team):
		return now < float(getattr(target_vehicle, '_spot_until', 0.0) or 0.0)
	mocks = globals().get('G_MOCK_VEHICLES', {}) or {}
	player_id = int(getattr(player, 'playerVehicleID', -1))
	local = mocks.get(player_id)
	if local is None:
		return False
	try:
		local._veh_velocity = float(player.getOwnVehicleSpeeds()[0])
	except Exception:
		pass
	target_descriptor = getattr(target_vehicle, 'typeDescriptor', None)
	if target_descriptor is None:
		return False
	target = {
		'id': int(getattr(target_vehicle, 'id', -1)),
		'team': target_team,
		'position': (float(target_vehicle.position.x),
		             float(target_vehicle.position.y),
		             float(target_vehicle.position.z)),
		'descriptor': target_descriptor,
		'vehicle': target_vehicle,
	}
	target['_spot_camouflage'] = _offh_spot_camouflage(
		player, target_vehicle, target_descriptor, now)
	candidates = []
	for observer_vehicle in mocks.values():
		if (observer_vehicle is target_vehicle or
				not bool(getattr(observer_vehicle, 'isAlive', True)) or
				getattr(observer_vehicle, 'position', None) is None):
			continue
		observer_info = getattr(observer_vehicle, 'publicInfo', None) or {}
		observer_team = int(getattr(
			observer_vehicle, '_bot_team', observer_info.get('team', 2)) or 2)
		if observer_team != observing_team:
			continue
		descriptor = getattr(observer_vehicle, 'typeDescriptor', None)
		if descriptor is None:
			continue
		position = (float(observer_vehicle.position.x),
		            float(observer_vehicle.position.y),
		            float(observer_vehicle.position.z))
		observer = {
			'id': int(getattr(observer_vehicle, 'id', -1)),
			'team': observer_team,
			'position': position,
			'descriptor': descriptor,
			'vehicle': observer_vehicle,
		}
		observer['_spot_view_range'] = _offh_ai_view_range(
			descriptor, observer_vehicle, player, now)
		dx = target['position'][0] - position[0]
		dz = target['position'][2] - position[2]
		distance_sq = dx * dx + dz * dz
		if distance_sq > 250000.0:
			continue
		if distance_sq <= 2500.0:
			candidates.append((distance_sq, observer))
		else:
			from gui.mods.offhangar import spotting as _team_spotting
			_foliage_bound = _team_spotting.foliage_visibility_bound(
				distance_sq, observer['_spot_view_range'],
				target['_spot_camouflage'], 0.60)
			if _foliage_bound is True:
				candidates.append((distance_sq, observer))
			elif _foliage_bound is None:
				spot_range = _offh_spot_detection_range(
					player, observer, target, now)
				if distance_sq <= spot_range * spot_range:
					candidates.append((distance_sq, observer))
	candidates.sort(key=lambda item: item[0])
	seen = False
	for distance_sq, observer in candidates[:3]:
		if (distance_sq <= 2500.0 or
				_offh_ai_has_los(observer['position'], target['position'])):
			seen = True
			if record_player_spot and observer['id'] == player_id:
				_offh_record_direct_spot(player, target_vehicle, now)
			break
	target_vehicle._offh_spot_eval_time = now
	target_vehicle._offh_spot_eval_team = observing_team
	target_vehicle._offh_spot_eval_seen = seen
	if seen:
		from gui.mods.offhangar import spotting
		target_vehicle._spot_until = now + spotting.SPOT_MEMORY_SECONDS
	return now < float(getattr(target_vehicle, '_spot_until', 0.0) or 0.0)


def _offh_spot_refresh_sixth_sense(player, now):
	"""Run enemy observation on replicas where no local AI director exists."""
	now = float(now)
	if now < float(getattr(player, '_offhangar_sixth_check_next', 0.0) or 0.0):
		return
	player._offhangar_sixth_check_next = now + 0.5
	mocks = globals().get('G_MOCK_VEHICLES', {}) or {}
	local = mocks.get(int(getattr(player, 'playerVehicleID', -1)))
	if (local is None or not bool(getattr(local, 'isAlive', True)) or
			int(getattr(local, 'health', 0) or 0) <= 0):
		return
	player_team = int(getattr(player, '_offhangar_team',
		getattr(player, '_offhangar_network_team', 1)) or 1)
	enemy_team = 2 if player_team == 1 else 1
	visible = _offh_spot_visible_to_team(
		player, local, enemy_team, now, False)
	_offh_update_sixth_sense(player, bool(visible), now)


def _offh_ai_has_los(observer_position, target_position):
	"""Static LOS between hulls, excluding both vehicle collision volumes."""
	_perf_started = _offh_perf_start()
	_result = False
	try:
		import BigWorld, Math
		from gui.mods.offhangar.bot_ai import trimmed_sight_segment
		for height in (1.5, 2.2):
			segment = trimmed_sight_segment(
				observer_position, target_position, target_height=height)
			if segment is None:
				_result = True
				break
			if not segment:
				continue
			start = Math.Vector3(*segment[0])
			end = Math.Vector3(*segment[1])
			if BigWorld.wg_collideSegment(_offh_bspace(), start, end, 128) is None:
				_result = True
				break
	except Exception:
		pass
	finally:
		_offh_perf_stop('los', _perf_started)
	return _result


def _offh_ai_clear_shot(shooter_position, target_position):
	return _offh_ai_has_los(shooter_position, target_position)


def _offh_ai_artillery_shot(vehicle, shell_index=0):
	'''Return the installed SPG shell without assuming descriptor wrappers.'''
	try:
		descriptor = getattr(vehicle, 'typeDescriptor', None)
		gun = getattr(descriptor, 'gun', None)
		shots = gun.get('shots', ()) if hasattr(gun, 'get') else gun['shots']
		if not shots:
			return None
		index = max(0, min(int(shell_index or 0), len(shots) - 1))
		return shots[index]
	except Exception:
		return None


def _offh_ai_artillery_pitch_limits(vehicle, target_yaw=0.0):
	'''Resolve the real yaw-dependent gun elevation limits.'''
	minimum, maximum = -1.45, 0.35
	try:
		descriptor = getattr(vehicle, 'typeDescriptor', None)
		pitch_desc = descriptor.gun.get('pitchLimits', None)
		if pitch_desc is not None:
			try:
				from gun_rotation_shared import calcPitchLimitsFromDesc
				limits = calcPitchLimitsFromDesc(float(target_yaw), pitch_desc)
			except Exception:
				limits = (pitch_desc.get('absolute', pitch_desc)
				          if hasattr(pitch_desc, 'get') else pitch_desc)
			minimum, maximum = float(limits[0]), float(limits[1])
	except Exception:
		pass
	return minimum, maximum


def _offh_ai_artillery_target_velocity(entry):
	import math
	vehicle = entry.get('vehicle') if isinstance(entry, dict) else None
	try:
		if getattr(vehicle, '_network_remote', False):
			value = getattr(vehicle, '_network_target_velocity', None)
			if value is not None:
				return (float(value[0]), float(value[1]), float(value[2]))
	except Exception:
		pass
	try:
		speed = float(getattr(vehicle, '_veh_velocity', 0.0) or 0.0)
		yaw = float(getattr(vehicle, 'yaw', 0.0) or 0.0)
		return (math.sin(yaw) * speed, 0.0, math.cos(yaw) * speed)
	except Exception:
		return (0.0, 0.0, 0.0)


def _offh_ai_gun_fire_position(vehicle):
	'''Return the rendered muzzle position, with a safe hull-relative fallback.'''
	try:
		gun_model = getattr(vehicle, '_gun_model', None)
		if gun_model is not None:
			position = Math.Matrix(gun_model.node('HP_gunFire')).translation
			return (float(position.x), float(position.y), float(position.z))
	except Exception:
		pass
	return (float(vehicle.position.x), float(vehicle.position.y) + 1.5,
	        float(vehicle.position.z))


def _offh_ai_artillery_world_clear(path, target_position):
	'''Check every chord of a real shell parabola against static world BSP.'''
	if not path or len(path) < 2:
		return False
	try:
		import Math
		target = Math.Vector3(float(target_position[0]),
		                      float(target_position[1]),
		                      float(target_position[2]))
		for first, second in zip(path, path[1:]):
			start = Math.Vector3(float(first[0]), float(first[1]), float(first[2]))
			end = Math.Vector3(float(second[0]), float(second[1]), float(second[2]))
			hit = BigWorld.wg_collideSegment(_offh_bspace(), start, end, 128)
			if hit is None:
				continue
			# The last chord legitimately ends inside the target or terrain under it.
			# Only an earlier mountain, roof or wall blocks the artillery lane.
			if (hit[0] - target).length <= 7.0:
				return True
			return False
		return True
	except Exception:
		return False


def _offh_ai_artillery_candidates(vehicle, target_position,
		target_velocity=(0.0, 0.0, 0.0), shell_index=0):
	'''Return the low/high real-shell trajectories without probing world BSP.'''
	import math
	shot = _offh_ai_artillery_shot(vehicle, shell_index)
	if shot is None:
		return ()
	try:
		speed = float(shot.get('speed', 0.0) if hasattr(shot, 'get') else shot['speed'])
		gravity = abs(float(shot.get('gravity', 0.0)
		                    if hasattr(shot, 'get') else shot['gravity']))
		start = _offh_ai_gun_fire_position(vehicle)
		target = (float(target_position[0]), float(target_position[1]) + 1.0,
		          float(target_position[2]))
		yaw = math.atan2(target[0] - start[0], target[2] - start[2])
		minimum, maximum = _offh_ai_artillery_pitch_limits(
			vehicle, yaw - float(getattr(vehicle, 'yaw', 0.0) or 0.0))
		from gui.mods.offhangar import bot_ai_driver
		result = []
		for prefer_high in (False, True):
			solution = bot_ai_driver.ballistic_intercept(
				start, target, target_velocity, speed, gravity,
				minimum, maximum, prefer_high, 12.0)
			if solution is None:
				continue
			aim, pitch, flight_time = solution
			yaw = math.atan2(aim[0] - start[0], aim[2] - start[2])
			path = bot_ai_driver.ballistic_path(
				start, yaw, pitch, speed, gravity, flight_time, 0.14)
			candidate = {
				'aim_position': aim, 'pitch': pitch,
				'flight_time': flight_time, 'yaw': yaw,
				'speed': speed, 'gravity': gravity, 'path': path,
			}
			# Near-identical low/high roots occur at the range limit. Avoid probing
			# the same trajectory twice merely because both solver branches exist.
			if (not result or abs(float(result[-1]['pitch']) - float(pitch)) > 0.002):
				result.append(candidate)
		return tuple(result)
	except Exception:
		return ()


def _offh_ai_artillery_solution(vehicle, target_position,
		target_velocity=(0.0, 0.0, 0.0), shell_index=0,
		require_clear=True):
	'''Solve and optionally world-probe the same parabola the tracer will fly.'''
	for candidate in _offh_ai_artillery_candidates(
			vehicle, target_position, target_velocity, shell_index):
		if (not require_clear or _offh_ai_artillery_world_clear(
				candidate['path'], candidate['aim_position'])):
			return candidate
	return None


def _offh_ai_artillery_arc_queue():
	'''Return one generation-scoped deferred BSP probe queue.'''
	generation = int(globals().get('g_offh_battle_gen', 0) or 0)
	state = globals().get('g_offh_artillery_arc_queue')
	if state is None or int(state.get('generation', -1)) != generation:
		from gui.mods.offhangar.artillery_arc_queue import ArcProbeQueue
		state = {'generation': generation, 'queue': ArcProbeQueue()}
		globals()['g_offh_artillery_arc_queue'] = state
	return state['queue']


def _offh_ai_artillery_probe_chord(first, second):
	'''Return a static-world hit position for one parabola chord, or None.'''
	try:
		import Math
		start = Math.Vector3(float(first[0]), float(first[1]), float(first[2]))
		end = Math.Vector3(float(second[0]), float(second[1]), float(second[2]))
		hit = BigWorld.wg_collideSegment(_offh_bspace(), start, end, 128)
		if hit is not None:
			return (float(hit[0].x), float(hit[0].y), float(hit[0].z))
	except Exception:
		# Match the synchronous fail-closed behaviour: a probe error blocks this
		# candidate instead of granting an unchecked firing lane.
		return (99999.0, 99999.0, 99999.0)
	return None


def _offh_ai_advance_artillery_arcs(now):
	started = _offh_perf_start()
	used = _offh_ai_artillery_arc_queue().advance(
		float(now), _OFFH_AI_ARTILLERY_CHORDS_PER_FRAME,
		_offh_ai_artillery_probe_chord)
	_offh_perf_stop('artillery_arc', started)
	_offh_perf_count('artillery_rays', used)
	return used


def _offh_ai_direct_fire_solution(vehicle, target_position,
		target_velocity=(0.0, 0.0, 0.0), shell_index=0):
	'''Lead an ordinary gun with the same speed and gravity its tracer uses.'''
	import math
	shot = _offh_ai_artillery_shot(vehicle, shell_index)
	if shot is None:
		return None
	try:
		speed = float(shot.get('speed', 0.0) if hasattr(shot, 'get') else shot['speed'])
		gravity = abs(float(shot.get('gravity', 0.0)
		                    if hasattr(shot, 'get') else shot['gravity']))
		start = _offh_ai_gun_fire_position(vehicle)
		target = (float(target_position[0]), float(target_position[1]) + 1.0,
		          float(target_position[2]))
		yaw = math.atan2(target[0] - start[0], target[2] - start[2])
		minimum, maximum = _offh_ai_artillery_pitch_limits(
			vehicle, yaw - float(getattr(vehicle, 'yaw', 0.0) or 0.0))
		from gui.mods.offhangar import bot_ai_driver
		solution = bot_ai_driver.ballistic_intercept(
			start, target, target_velocity, speed, gravity,
			minimum, maximum, False, 4.0)
		if solution is None:
			return None
		aim, pitch, flight_time = solution
		return {
			'aim_position': aim, 'pitch': pitch,
			'flight_time': flight_time,
			'yaw': math.atan2(aim[0] - start[0], aim[2] - start[2]),
			'speed': speed, 'gravity': gravity,
		}
	except Exception:
		return None


def _offh_ai_ballistic_collision(path, mock_vehicles, shooter_id,
		living_only=False):
	'''Return the first static or vehicle impact along sampled trajectory chords.'''
	if not path or len(path) < 2:
		return None
	try:
		import Math
		travelled = 0.0
		for first, second in zip(path, path[1:]):
			start = Math.Vector3(float(first[0]), float(first[1]), float(first[2]))
			end = Math.Vector3(float(second[0]), float(second[1]), float(second[2]))
			direction = end - start
			segment_length = direction.length
			if segment_length <= 0.001:
				continue
			direction.normalise()
			world_hit = BigWorld.wg_collideSegment(
				_offh_bspace(), start, end, 128)
			world_local = ((world_hit[0] - start).length
			               if world_hit is not None else 9999.0)
			try:
				water_local = BigWorld.wg_collideWater(start, end)
				if water_local >= 0.0 and water_local < world_local:
					water_point = start + direction.scale(water_local)
					world_hit = (water_point, None)
					world_local = float(water_local)
			except Exception:
				pass
			vehicle = None
			vehicle_hit = None
			vehicle_local = 9999.0
			for entity_id, candidate in (mock_vehicles or {}).iteritems():
				if int(entity_id) == int(shooter_id):
					continue
				if living_only and (not getattr(candidate, 'isAlive', False) or
						(getattr(candidate, 'health', 0) or 0) <= 0):
					continue
				collision = candidate.collideSegment(start, end)
				if collision is not None and float(collision[0]) < vehicle_local:
					vehicle = candidate
					vehicle_hit = collision
					vehicle_local = float(collision[0])
			if vehicle is not None and vehicle_local < world_local:
				return {
					'vehicle': vehicle, 'vehicle_hit': vehicle_hit,
					'vehicle_distance': travelled + vehicle_local,
					'world_hit': None, 'world_distance': 9999.0,
					'segment_start': start, 'segment_end': end,
					'direction': direction,
				}
			if world_hit is not None:
				return {
					'vehicle': None, 'vehicle_hit': None,
					'vehicle_distance': 9999.0,
					'world_hit': world_hit,
					'world_distance': travelled + world_local,
					'segment_start': start, 'segment_end': end,
					'direction': direction,
				}
			travelled += segment_length
	except Exception:
		return None
	return None


def _offh_ai_ground_point(x, z, hint_y):
	"""Return nearby drivable ground without jumping onto roofs or bridges."""
	try:
		import BigWorld, Math
		probe_top = float(hint_y) + 7.0
		probe_bottom = float(hint_y) - 14.0
		for _unused in range(3):
			hit = BigWorld.wg_collideSegment(
				_offh_bspace(), Math.Vector3(float(x), probe_top, float(z)),
				Math.Vector3(float(x), probe_bottom, float(z)), 128)
			if hit is None:
				return None
			height = float(hit[0].y)
			if height <= float(hint_y) + 4.0:
				return (float(x), height, float(z))
			probe_top = height - 0.35
	except Exception:
		pass
	return None


def _offh_ai_candidate_slope(point):
	"""Estimate the steepest local grade in degrees around one candidate."""
	try:
		import math
		maximum = 0.0
		for offset_x, offset_z in ((2.5, 0.0), (-2.5, 0.0),
		                             (0.0, 2.5), (0.0, -2.5)):
			other = _offh_ai_ground_point(
				point[0] + offset_x, point[2] + offset_z, point[1])
			if other is None:
				return 90.0
			grade = math.degrees(math.atan2(abs(other[1] - point[1]), 2.5))
			maximum = max(maximum, grade)
		return maximum
	except Exception:
		return 90.0


def _offh_ai_sample_cover(director, bot_id, vehicle, target_position,
							   route_position, ally_positions, offset_index=None):
	"""Probe a small cover fan; never queries an unobserved enemy position."""
	try:
		import math
		from gui.mods.offhangar.bot_ai_cover import score_candidates
		current = (float(vehicle.position.x), float(vehicle.position.y),
		           float(vehicle.position.z))
		dx = current[0] - float(target_position[0])
		dz = current[2] - float(target_position[2])
		length = math.sqrt(dx * dx + dz * dz)
		if length < 2.0:
			return ()
		away_x = dx / length
		away_z = dz / length
		right_x = away_z
		right_z = -away_x
		offsets = _OFFH_AI_COVER_OFFSETS
		if offset_index is None:
			indexed_offsets = tuple(enumerate(offsets))
		else:
			index = int(offset_index) % len(offsets)
			indexed_offsets = ((index, offsets[index]),)
		route_dx = float(route_position[0]) - current[0]
		route_dz = float(route_position[2]) - current[2]
		route_length = math.sqrt(route_dx * route_dx + route_dz * route_dz)
		navigator = _offh_ai_navigator(director)
		candidates = []
		for index, (away, lateral) in indexed_offsets:
			x = current[0] + away_x * away + right_x * lateral
			z = current[2] + away_z * away + right_z * lateral
			point = _offh_ai_ground_point(x, z, current[1])
			if point is None:
				continue
			travel = math.sqrt((point[0] - current[0]) ** 2 +
			                   (point[2] - current[2]) ** 2)
			water_depth = _offh_water_depth(point[0], point[1], point[2])
			if water_depth > _OFFH_AI_WATER_AVOID_DEPTH:
				continue
			try:
				escape = bool(navigator.grid.segment_clear(current, point))
			except Exception:
				escape = False
			if not escape:
				continue
			occluded = not _offh_ai_has_los(point, target_position)
			if not occluded:
				continue
			slope = _offh_ai_candidate_slope(point)
			if slope > 24.0:
				continue
			peek = None
			to_target_x = -away_x
			to_target_z = -away_z
			for side in (-1.0, 1.0):
				peek_point = _offh_ai_ground_point(
					point[0] + right_x * side * 6.5 + to_target_x * 2.0,
					point[2] + right_z * side * 6.5 + to_target_z * 2.0,
					point[1])
				if peek_point is None or _offh_water_depth(
						peek_point[0], peek_point[1], peek_point[2]) > _OFFH_AI_WATER_AVOID_DEPTH:
					continue
				try:
					peek_clear = navigator.grid.segment_clear(point, peek_point)
				except Exception:
					peek_clear = False
				if peek_clear and _offh_ai_has_los(peek_point, target_position):
					peek = peek_point
					break
			move_dx = point[0] - current[0]
			move_dz = point[2] - current[2]
			move_length = math.sqrt(move_dx * move_dx + move_dz * move_dz)
			alignment = 0.5
			if move_length > 0.1 and route_length > 0.1:
				dot = ((move_dx / move_length) * (route_dx / route_length) +
				       (move_dz / move_length) * (route_dz / route_length))
				alignment = max(0.0, min(1.0, (dot + 1.0) * 0.5))
			nearby = 0
			for ally in ally_positions or ():
				ally_distance = math.sqrt((point[0] - ally[0]) ** 2 +
				                          (point[2] - ally[2]) ** 2)
				if ally_distance < 13.0 and ally_distance > 0.5:
					nearby += 1
			candidate = {
				'id': '%s:%d:%d' % (
					str(bot_id), int(round(point[0] / 4.0)),
					int(round(point[2] / 4.0))),
				'position': point,
				'travel_distance': travel,
				'route_alignment': alignment,
				'enemy_occlusion': 1.0 if occluded else 0.0,
				'exposure': 0.12 if occluded else 1.0,
				'slope': slope,
				'water': max(0.0, min(1.0, water_depth)),
				'ally_congestion': max(0.0, min(1.0, nearby / 3.0)),
				'peek_feasible': peek is not None,
				'escape_feasible': escape,
			}
			if peek is not None:
				candidate['peek_position'] = peek
			candidates.append(candidate)
		ranked = score_candidates(candidates)
		for candidate in ranked:
			candidate.pop('breakdown', None)
			candidate.pop('reasons', None)
			candidate.pop('rank', None)
			candidate.pop('score', None)
		return tuple(ranked)
	except Exception:
		return ()


def _offh_ai_apply_local_cover(bot_id, position, order, now):
	"""Apply the same cover/peek cycle when LAN mode is disabled."""
	cache = globals().get('g_offh_ai_local_covers', {}).get(int(bot_id))
	if (cache is None or float(now) > float(cache.get('expires', 0.0)) or
			cache.get('target_id') != order.get('target_id')):
		return order
	candidate = cache.get('candidate') or {}
	cover = candidate.get('position')
	peek = candidate.get('peek_position')
	if cover is None or peek is None:
		return order
	try:
		import math
		cover_distance = math.sqrt((cover['x'] - position[0]) ** 2 +
		                           (cover['z'] - position[2]) ** 2)
		peek_distance = math.sqrt((peek['x'] - position[0]) ** 2 +
		                          (peek['z'] - position[2]) ** 2)
	except Exception:
		return order
	phase = cache.get('phase', 'approach')
	if phase in ('approach', 'return') and cover_distance <= 4.5:
		phase = 'hold'
		cache['phase'] = phase
		personality = order.get('personality') or {}
		cache['phase_until'] = (float(now) + 0.65 +
			float(personality.get('patience', 0.5)) * 1.35)
	elif phase == 'hold' and float(now) >= float(cache.get('phase_until', 0.0)):
		phase = 'peek'
		cache['phase'] = phase
		cache['phase_until'] = 0.0
	elif phase == 'peek' and peek_distance <= 4.5:
		if float(cache.get('phase_until', 0.0)) <= 0.0:
			personality = order.get('personality') or {}
			cache['phase_until'] = (float(now) + 1.0 +
				float(personality.get('aggression', 0.5)) * 1.8)
		elif float(now) >= float(cache.get('phase_until', 0.0)):
			phase = 'return'
			cache['phase'] = phase
			cache['phase_until'] = 0.0
	result = dict(order)
	result['cover_id'] = candidate.get('id')
	# Permission remains live throughout the cover cycle. The firing loop below
	# still requires current LOS, turret alignment and reload completion.
	result['fire_allowed'] = bool(order.get('fire_allowed'))
	if phase == 'approach':
		result['combat_mode'] = 'take_cover'
		result['move_position'] = (cover['x'], cover['y'], cover['z'])
		result['throttle_override'] = None
	elif phase == 'hold':
		result['combat_mode'] = 'cover_hold'
		result['move_position'] = (cover['x'], cover['y'], cover['z'])
		result['throttle_override'] = 0.0
	elif phase == 'peek':
		result['combat_mode'] = 'cover_peek'
		result['move_position'] = (peek['x'], peek['y'], peek['z'])
		result['throttle_override'] = None if peek_distance > 4.5 else 0.0
		result['fire_allowed'] = bool(order.get('fire_allowed'))
	else:
		result['combat_mode'] = 'cover_return'
		result['move_position'] = (cover['x'], cover['y'], cover['z'])
		result['throttle_override'] = None
	return result


def _offh_ai_refresh_contacts(director, player, mock_vehicles, veh_pos,
		                           player_descriptor, now):
	"""Refresh one bounded slice of the team blackboards each rendered frame.

	A complete 30-target sweep still takes roughly 0.5 seconds at 30 FPS, but its
	LOS and artillery probes no longer land in one 100+ ms render callback.
	"""
	last = globals().get('g_offh_ai_contacts_t', -999.0)
	if float(now) - float(last) < 0.02:
		return
	globals()['g_offh_ai_contacts_t'] = float(now)
	# Static SPG obstruction checks retain the real curved shell path, but only
	# four native BSP chords may run in one render callback. Completed low/high
	# solutions are cached by the queue and consumed below on a later target pass.
	_offh_ai_advance_artillery_arcs(now)
	_perf_contact_build = _offh_perf_start()
	entries = {}
	player_id = getattr(player, 'playerVehicleID', -1)
	player_mock = mock_vehicles.get(player_id)
	try:
		player_mock._veh_velocity = float(player.getOwnVehicleSpeeds()[0])
	except Exception:
		pass
	player_health = getattr(player_mock, 'health', getattr(player, 'health', 1))
	player_max_health = getattr(player_mock, 'maxHealth', max(1, player_health))
	player_profile = _offh_ai_vehicle_profile(player_mock, player_descriptor)
	entries[player_id] = {
		'id': player_id,
		'team': int(getattr(player, '_offhangar_team', 1) or 1),
		'position': (float(veh_pos[0]), float(veh_pos[1]), float(veh_pos[2])),
		'health': float(player_health or 0),
		'max_health': float(player_max_health or 1),
		'class_tag': player_profile.get('class_tag', 'mediumTank'),
		'armor': player_profile.get('armor', 0.0),
		'speed': player_profile.get('speed', 0.0),
		'server_id': getattr(player, '_offhangar_network_id', None),
		'target_kind': 'human',
		'descriptor': player_descriptor,
		'vehicle': player_mock,
		'alive': bool((player_health or 0) > 0 and not getattr(player, '_is_dead', False)),
	}
	for entity_id, mock in mock_vehicles.iteritems():
		if entity_id == player_id:
			continue
		info = getattr(mock, 'publicInfo', None)
		team = getattr(mock, '_bot_team', info.get('team', 2) if info else 2)
		descriptor = getattr(mock, 'typeDescriptor', None) or player_descriptor
		profile = _offh_ai_vehicle_profile(mock, descriptor)
		health = getattr(mock, 'health', 0) or 0
		server_id = getattr(mock, '_network_server_id', None)
		target_kind = 'human'
		if server_id is None:
			server_id = getattr(mock, '_network_bot_id', None)
			target_kind = 'bot'
		entries[entity_id] = {
			'id': entity_id,
			'team': int(team or 2),
			'position': (float(mock.position.x), float(mock.position.y),
			             float(mock.position.z)),
			'health': float(health),
			'max_health': float(getattr(mock, 'maxHealth', 1) or 1),
			'class_tag': profile.get('class_tag', 'mediumTank'),
			'armor': profile.get('armor', 0.0),
			'speed': profile.get('speed', 0.0),
			'server_id': server_id,
			'target_kind': target_kind,
			'descriptor': descriptor,
			'vehicle': mock,
			'alive': bool(getattr(mock, 'isAlive', False) and health > 0),
		}
	living = [entry for entry in entries.values() if entry['alive']]
	# View range and target camouflage are evaluated lazily below. Only two
	# targets are sliced into a frame, and a 50 m proximity spot needs neither;
	# pre-touching every vehicle here performed 30 cache reads for no result.
	generation = int(globals().get('g_offh_battle_gen', 0) or 0)
	contact_cache = globals().get('g_offh_ai_network_contacts')
	if (contact_cache is None or
			int(contact_cache.get('generation', -1)) != generation):
		contact_cache = {
			'generation': generation, 'contacts': {}, 'dirty': set(),
			'last_full': -999.0,
		}
		globals()['g_offh_ai_network_contacts'] = contact_cache
	contact_cache.setdefault('dirty', set())
	contact_cache.setdefault('last_full', -999.0)
	network_contact_cache = contact_cache['contacts']
	network_contact_dirty = contact_cache['dirty']
	artillery_by_team = {1: [], 2: []}
	for entry in living:
		if entry.get('class_tag') == 'SPG':
			artillery_by_team.setdefault(int(entry['team']), []).append(entry)
	_offh_perf_stop('contact_build', _perf_contact_build)
	targets = sorted(entries.values(), key=lambda value: int(value.get('id', 0)))
	if targets:
		cursor = int(globals().get('g_offh_ai_artillery_cursor', 0) or 0) % len(targets)
		targets = targets[cursor:] + targets[:cursor]
		globals()['g_offh_ai_artillery_cursor'] = (
			cursor + _OFFH_AI_CONTACT_TARGETS_PER_FRAME) % len(targets)
		targets = targets[:_OFFH_AI_CONTACT_TARGETS_PER_FRAME]
	_perf_contact_targets = _offh_perf_start()
	for target in targets:
		observing_team = 2 if target['team'] == 1 else 1
		visible = False
		shootable_by_bot_ids = []
		shootable_by_entity_ids = []
		if target['alive']:
			target['_spot_camouflage'] = _offh_spot_camouflage(
				player, target.get('vehicle'), target['descriptor'], now)
			observer_distances = []
			for observer in living:
				if observer['team'] != observing_team:
					continue
				dx = target['position'][0] - observer['position'][0]
				dz = target['position'][2] - observer['position'][2]
				distance_sq = dx * dx + dz * dz
				if distance_sq > 250000.0:
					continue
				observer_distances.append((distance_sq, observer))
			observer_distances.sort(key=lambda item: item[0])
			candidates = []
			from gui.mods.offhangar import spotting as _contact_spotting
			for distance_sq, observer in observer_distances:
				if distance_sq <= 2500.0:
					candidates.append((distance_sq, observer))
				else:
					# Foliage can only increase camouflage, so the foliage-free range is
					# an exact upper bound.  Sort first and run the expensive prebaked
					# foliage query only for observers that can enter the closest three.
					_view_range = observer.get('_spot_view_range')
					if _view_range is None:
						_view_range = _offh_ai_cached_view_range(
							observer['descriptor'], observer.get('vehicle'), player, now)
						observer['_spot_view_range'] = _view_range
					_foliage_bound = _contact_spotting.foliage_visibility_bound(
						distance_sq, _view_range,
						target.get('_spot_camouflage', 0.0), 0.60)
					if _foliage_bound is False:
						continue
					if _foliage_bound is True:
						candidates.append((distance_sq, observer))
					else:
						_perf_contact_foliage = _offh_perf_start()
						try:
							spot_range = _offh_spot_detection_range(
								player, observer, target, now)
						finally:
							_offh_perf_stop(
								'contact_foliage', _perf_contact_foliage)
						if distance_sq <= spot_range * spot_range:
							candidates.append((distance_sq, observer))
				if len(candidates) >= 3:
					break
			for distance_sq, observer in candidates:
				proximity_visible = distance_sq <= 2500.0
				has_los = _offh_ai_has_los(
					observer['position'], target['position'])
				if proximity_visible or has_los:
					visible = True
					if (observer['id'] == player_id and
							target['team'] != observer['team'] and
							target.get('vehicle') is not None):
						_offh_record_direct_spot(
							player, target['vehicle'], now)
					# Proximity spotting may reveal a tank through a building, but it
					# is not a firing lane. Only a real collision-free sight segment
					# may grant this observer a target assignment.
					if not has_los:
						continue
					if observer.get('target_kind') == 'bot':
						# A straight sight segment proves visibility, not a usable SPG
						# trajectory. Artillery is granted below only after its sampled
						# ballistic arc has completed the world-probe queue.
						if observer.get('class_tag') == 'SPG':
							continue
						observer_entity_id = int(observer['id'])
						if observer_entity_id not in shootable_by_entity_ids:
							shootable_by_entity_ids.append(observer_entity_id)
						# Team spotting updates the shared blackboard, but only this
						# observer has proved a local firing lane. Send network bot
						# ids so the server cannot turn every hull toward one red dot.
						if (observer.get('target_kind') == 'bot' and
								observer.get('server_id') is not None):
							observer_id = int(observer['server_id'])
							if observer_id not in shootable_by_bot_ids:
								shootable_by_bot_ids.append(observer_id)
			if visible:
				for observer in artillery_by_team.get(observing_team, ()):
					vehicle = observer.get('vehicle')
					if vehicle is None:
						continue
					shell_index = max(0, int(getattr(
						vehicle, '_network_bot_shell_index', 0) or 0))
					cache_key = (
						int(observer['id']),
						int(target['id']), shell_index,
						int(round(observer['position'][0] / 6.0)),
						int(round(observer['position'][2] / 6.0)),
						int(round(target['position'][0] / 10.0)),
						int(round(target['position'][2] / 10.0)))
					arc_queue = _offh_ai_artillery_arc_queue()
					ready, solution = arc_queue.request_lazy(
						cache_key,
						lambda: _offh_ai_artillery_candidates(
							vehicle, target['position'],
							_offh_ai_artillery_target_velocity(target),
							shell_index),
						target['position'], now)
					if solution is None:
						continue
					observer_entity_id = int(observer['id'])
					if observer_entity_id not in shootable_by_entity_ids:
						shootable_by_entity_ids.append(observer_entity_id)
					if observer.get('server_id') is not None:
						observer_id = int(observer['server_id'])
						if observer_id not in shootable_by_bot_ids:
							shootable_by_bot_ids.append(observer_id)
		if target['id'] == player_id:
			_offh_update_sixth_sense(player, bool(visible), now)
		target_vehicle = target.get('vehicle')
		if target_vehicle is not None:
			try:
				target_vehicle._offh_spot_eval_time = float(now)
				target_vehicle._offh_spot_eval_team = int(observing_team)
				target_vehicle._offh_spot_eval_seen = bool(visible)
				if visible:
					from gui.mods.offhangar import spotting
					target_vehicle._spot_until = (
						float(now) + spotting.SPOT_MEMORY_SECONDS)
			except Exception:
				pass
		# A confirmed destruction is shared immediately; an unseen living target
		# only changes an existing contact to last-known state.
		director.update_contact(
			observing_team, target['id'], target['team'], target['position'],
			target['health'], target['max_health'], target['class_tag'],
			visible or not target['alive'], now,
			target.get('armor', 0.0), target.get('speed', 0.0),
			shootable_by_entity_ids)
		if target.get('server_id') is not None:
			_network_contact = {
				'observing_team': observing_team,
				'target_id': int(target['server_id']),
				'target_kind': target.get('target_kind', 'human'),
				'target_team': int(target['team']),
				'position': target['position'],
				'health': int(target['health']),
				'max_health': int(target['max_health']),
				'class_tag': target['class_tag'],
				'armor': float(target.get('armor', 0.0)),
				'visible': bool(visible or not target['alive']),
				'shootable_by_bot_ids': shootable_by_bot_ids,
			}
			_contact_key = (
				int(observing_team), str(target.get('target_kind', 'human')),
				int(target['server_id']))
			network_contact_cache[_contact_key] = _network_contact
			network_contact_dirty.add(_contact_key)
	_offh_perf_stop('contact_targets', _perf_contact_targets, len(targets))
	cover_cache = globals().get('g_offh_ai_cover_reports')
	if (cover_cache is None or
			int(cover_cache.get('generation', -1)) != generation):
		cover_cache = {
			'generation': generation, 'reports': {}, 'offsets': {}}
		globals()['g_offh_ai_cover_reports'] = cover_cache
	cover_reports = list(cover_cache['reports'].values())
	try:
		cover_jobs = []
		last_cover = float(globals().get('g_offh_ai_cover_t', -999.0) or -999.0)
		cover_due = float(now) - last_cover >= 0.10
		if cover_due:
			globals()['g_offh_ai_cover_t'] = float(now)
		shared_entries = {}
		for value in entries.values():
			if value.get('server_id') is not None:
				shared_entries[(value.get('target_kind'),
				                int(value.get('server_id')))] = value
		for entity_id, entry in entries.items():
			if not cover_due:
				break
			if entity_id == player_id or not entry.get('alive'):
				continue
			agent = director.agents.get(int(entity_id))
			order = agent.get('last_order') if agent is not None else None
			vehicle = mock_vehicles.get(entity_id)
			# The main bot loop already materialises the authoritative order and keeps
			# it live at 75-160 ms cadence. Reuse that exact object for the 10 Hz cover
			# scan instead of converting the same server coordinates a second time for
			# every bot. A cold cache still falls back to the canonical reader.
			cached_order = getattr(vehicle, '_offh_ai_order_cache', None)
			if (isinstance(cached_order, tuple) and len(cached_order) == 3 and
					isinstance(cached_order[2], dict)):
				order = cached_order[2]
			else:
				try:
					from gui.mods.offhangar.network_battle import authoritative_bot_order
					network_order = authoritative_bot_order(player, vehicle)
					if network_order is not None:
						order = network_order
				except Exception:
					pass
			mode = order.get('combat_mode') if order else None
			cover_modes = ('take_cover', 'cover_hold', 'cover_peek', 'cover_return')
			if (not order or
					(mode not in cover_modes and
					 (not order.get('fire_allowed') or
					  mode not in ('engage', 'advance_contact',
					               'jiggle_forward', 'jiggle_back')))):
				continue
			target_kind = order.get('target_kind')
			if target_kind and order.get('target_id') is not None:
				target = shared_entries.get((target_kind, int(order.get('target_id'))))
			else:
				target = entries.get(order.get('target_id'))
			if target is None or vehicle is None or not target.get('alive'):
				continue
			cover_jobs.append((entity_id, entry, target, vehicle, order))
		cover_jobs.sort(key=lambda value: value[0])
		cursor = int(globals().get('g_offh_ai_cover_cursor', 0) or 0)
		if cover_jobs:
			cursor %= len(cover_jobs)
			ordered_jobs = cover_jobs[cursor:] + cover_jobs[:cursor]
			globals()['g_offh_ai_cover_cursor'] = (cursor + 1) % len(cover_jobs)
		else:
			ordered_jobs = ()
		for entity_id, entry, target, vehicle, order in ordered_jobs[
				: _OFFH_AI_COVER_CANDIDATES_PER_FRAME]:
			allies = [value['position'] for value in living
			          if value['team'] == entry['team']]
			offset_index = int(cover_cache['offsets'].get(int(entity_id), 0) or 0)
			cover_cache['offsets'][int(entity_id)] = (
				offset_index + 1) % len(_OFFH_AI_COVER_OFFSETS)
			candidates = _offh_perf_call('contact_cover', _offh_ai_sample_cover,
				director, entity_id, vehicle, target['position'],
				order.get('route_anchor') or order.get('move_position'), allies,
				offset_index)
			usable = [candidate for candidate in candidates
			          if candidate.get('water', 1.0) < 0.5 and
			          candidate.get('slope', 90.0) <= 24.0 and
			          candidate.get('enemy_occlusion', 0.0) >= 0.45 and
			          candidate.get('peek_feasible') and candidate.get('escape_feasible')]
			if not usable:
				continue
			bot_server_id = getattr(vehicle, '_network_bot_id', None)
			if bot_server_id is not None and target.get('server_id') is not None:
				cover_cache['reports'][int(bot_server_id)] = {
					'bot_id': int(bot_server_id),
					'target_id': int(target['server_id']),
					'target_kind': target.get('target_kind', 'human'),
					'candidates': usable,
				}
				cover_reports = list(cover_cache['reports'].values())
			else:
				local_covers = globals().setdefault('g_offh_ai_local_covers', {})
				old = local_covers.get(int(entity_id))
				selected = usable[0]
				if (old is not None and old.get('target_id') == target['id'] and
						old.get('candidate', {}).get('id') == selected.get('id')):
					old['candidate'] = selected
					old['expires'] = float(now) + 8.0
				else:
					local_covers[int(entity_id)] = {
						'target_id': target['id'], 'candidate': selected,
						'expires': float(now) + 8.0, 'phase': 'approach',
						'phase_until': 0.0,
					}
	except Exception:
		cover_reports = list(cover_cache['reports'].values())
	_network_client = getattr(player, '_offhangar_network_client', None)
	if (_network_client is None or
			not getattr(_network_client, 'ready', False)):
		return
	try:
		if (hasattr(_network_client, 'bot_observation_due') and
				not _network_client.bot_observation_due()):
			return
	except Exception:
		pass
	# Observation transport is throttled to roughly 2 Hz. Send newly evaluated
	# contacts as a delta, with a complete refresh well inside the server's 8 s
	# contact TTL. The server already stores contacts by identity, so converting
	# and serialising all 30 stale cache entries every 450 ms added no gameplay
	# information and starved this legacy client's render/network callback.
	_perf_contact_payload = _offh_perf_start()
	_full_contact_refresh = (
		float(now) - float(contact_cache.get('last_full', -999.0)) >=
		_OFFH_AI_CONTACT_FULL_INTERVAL)
	if _full_contact_refresh:
		_contact_keys = sorted(network_contact_cache.keys())
	else:
		_contact_keys = sorted(
			key for key in network_contact_dirty if key in network_contact_cache)
	network_contacts = [network_contact_cache[key] for key in _contact_keys]
	cover_reports.sort(key=lambda value: int(value.get('bot_id', 0)))
	_offh_perf_stop(
		'contact_payload', _perf_contact_payload, len(network_contacts))
	try:
		from gui.mods.offhangar.network_battle import publish_bot_observation
		_diagnostics_due = (
			float(now) - float(globals().get(
				'g_offh_ai_diagnostics_t', -999.0) or -999.0) >=
			_OFFH_AI_DIAGNOSTICS_INTERVAL)
		_navigator = (globals().get('g_offh_terrain_navigator')
		              if _diagnostics_due else None)
		_perf_contact_diagnostics = _offh_perf_start()
		_active_bot_ids = [entry['id'] for entry in living
		                   if entry['id'] != player_id]
		_navigation = (_navigator.fallback_diagnostics(_active_bot_ids, now)
		               if _navigator is not None else None)
		if _navigation is not None:
			_network_client = getattr(player, '_offhangar_network_client', None)
			_navigation['orders'] = {
				'revision': int(getattr(_network_client, 'bot_order_revision', 0) or 0),
				'loaded': len(getattr(_network_client, 'bot_orders', {}) or {}),
			}
			_aim = {'alive': 0, 'targeted': 0, 'aligned': 0,
			        'traversing': 0, 'limited': 0}
			_driver = {'moving': 0, 'drive': 0, 'avoid': 0, 'blocked': 0,
			           'recovery': 0, 'arrived': 0, 'server_wait': 0,
			           'traffic_wait': 0,
			           'water_guard': 0, 'full': 0, 'cruise': 0,
			           'speed_pct': 0, 'slow': 0}
			_driver_speed_pct_total = 0.0
			_safety = {'water_guard_total': int(
				globals().get('g_offh_ai_water_guard_total', 0) or 0),
				'water_guard_active': 0, 'edge_guard_total': int(
				globals().get('g_offh_ai_edge_guard_total', 0) or 0),
				'edge_guard_active': 0, 'veto_water': 0,
				'veto_terrain': 0, 'veto_obstacle': 0, 'veto_error': 0}
			_diag_now = BigWorld.time()
			for _aim_vehicle in (mock_vehicles or {}).values():
				if (getattr(_aim_vehicle, '_network_bot_id', None) is None or
						not getattr(_aim_vehicle, 'isAlive', False)):
					continue
				_aim['alive'] += 1
				_targeted = bool(getattr(_aim_vehicle, '_offh_ai_targeted', False))
				for _aim_name in ('targeted', 'aligned', 'traversing', 'limited'):
					if (getattr(_aim_vehicle, '_offh_ai_' + _aim_name, False) and
							(_aim_name in ('targeted', 'limited') or _targeted)):
						_aim[_aim_name] += 1
				if abs(float(getattr(_aim_vehicle, '_veh_velocity', 0.0) or 0.0)) > 0.5:
					_driver['moving'] += 1
				_driver_mode = getattr(_aim_vehicle, '_offh_ai_driver_mode', '')
				if _driver_mode in ('reverse_turn', 'pivot_recovery'):
					_driver_mode = 'recovery'
				if _driver_mode in _driver:
					_driver[_driver_mode] += 1
				_throttle = abs(float(getattr(
					_aim_vehicle, '_offh_ai_throttle', 0.0) or 0.0))
				if _throttle >= 0.99:
					_driver['full'] += 1
					if float(getattr(
							_aim_vehicle, '_offh_ai_full_throttle_seconds',
							0.0) or 0.0) >= 3.0:
						_params = getattr(_aim_vehicle, '_phys_params', None) or {}
						_limit = max(0.1, float(
							_params.get('speedFwd', 0.1) or 0.1))
						_ratio = min(2.0, abs(float(getattr(
							_aim_vehicle, '_veh_velocity', 0.0) or 0.0)) / _limit)
						_driver['cruise'] += 1
						_driver_speed_pct_total += _ratio * 100.0
						if (_ratio < 0.35 and
								_driver_mode in ('drive', 'avoid')):
							_driver['slow'] += 1
				if float(getattr(_aim_vehicle, '_offh_ai_water_guard_until', 0.0) or 0.0) > _diag_now:
					_safety['water_guard_active'] += 1
				if float(getattr(_aim_vehicle, '_offh_ai_edge_guard_until', 0.0) or 0.0) > _diag_now:
					_safety['edge_guard_active'] += 1
				if float(getattr(_aim_vehicle, '_offh_ai_probe_reject_until', 0.0) or 0.0) > _diag_now:
					_reason = getattr(_aim_vehicle, '_offh_ai_probe_reject', '')
					_reason_key = 'veto_' + str(_reason)
					if _reason_key in _safety:
						_safety[_reason_key] += 1
			_navigation['aim'] = _aim
			if _driver['cruise']:
				_driver['speed_pct'] = int(round(
					_driver_speed_pct_total / float(_driver['cruise'])))
			_navigation['driver'] = _driver
			_navigation['safety'] = _safety
		_offh_perf_stop('contact_diagnostics', _perf_contact_diagnostics)
		_perf_contact_publish = _offh_perf_start()
		_published = publish_bot_observation(
			player, network_contacts, cover_reports, _navigation)
		_offh_perf_stop('contact_publish', _perf_contact_publish)
		if _published:
			for _contact_key in _contact_keys:
				network_contact_dirty.discard(_contact_key)
			if _full_contact_refresh:
				contact_cache['last_full'] = float(now)
			if _diagnostics_due:
				globals()['g_offh_ai_diagnostics_t'] = float(now)
	except Exception as error:
		# This telemetry also carries the server's contact/driver diagnostics.
		# Silencing its failure left the server reporting all-zero AI state even
		# while local bots were moving, which hid the actual integration fault.
		_observation_gen = globals().get('g_offh_battle_gen', 0)
		if globals().get('g_offh_observation_error_gen') != _observation_gen:
			globals()['g_offh_observation_error_gen'] = _observation_gen
			try:
				import traceback
				from gui.mods.offhangar.logging import LOG_ERROR as _OBS_ERROR
				_OBS_ERROR('LAN bot observation publish failed: %s\n%s' % (
					str(error), traceback.format_exc()))
			except Exception:
				pass


def _offh_battle_sweep(tag='exit'):
	# Full post-battle cleanup. Without it every battle leaves wrecks,
	# global models, FMOD events and the mapped battle space behind;
	# after a few battles the 32-bit client dies out-of-memory while
	# loading a map or the hangar (malloc NULL -> native write@0).
	# v2: staged + ALWAYS logs one line, failures log stage+traceback.
	import BigWorld
	global g_offline_models, g_offline_enemies
	# Invalidate asynchronous visual callbacks immediately. Waiting for the next
	# battle to bump this value lets a late resource callback recreate a model in
	# the hangar after the sweep has already deleted everything.
	globals()['g_offh_battle_gen'] = (
		(globals().get('g_offh_battle_gen', 0) or 0) + 1)
	try:
		import gui.mods.offhangar.logging as _swlog
	except Exception:
		_swlog = None
	# Adopted layout caches: one entry per vehicle type and configuration, plus a
	# geometry probe cache. They are keyed by type, not by battle, so on a client
	# with ~2 GB of address space they must not ride along from map to map.
	try:
		import sys as _swsys
		_ihl = _swsys.modules.get('gui.mods.offhangar.internal_hit_layouts')
		if _ihl is not None and hasattr(_ihl, '_LAYOUT_CACHE'):
			_ihl._LAYOUT_CACHE.clear()
		_ig = _swsys.modules.get('gui.mods.offhangar.internal_geometry')
		if _ig is not None and hasattr(_ig, 'clear_cache'):
			_ig.clear_cache()
	except Exception:
		pass
	_stage = 'init'
	_n_models = 0
	_n_mocks = 0
	_n_stickers = 0
	_n_hit_testers = 0
	_n_hit_tester_failures = 0
	_n_callbacks = 0
	_n_entities = 0
	_n_entity_candidates = 0
	_n_player_attrs = 0
	_n_player_attr_failures = 0
	_n_arena_delegates = 0
	_fail = None
	_mem_before = _offh_proc_mem_mb()
	try:
		_n_models = len(g_offline_models or [])
		_mvd = globals().get('G_MOCK_VEHICLES', {}) or {}
		_n_mocks = len(_mvd)
		_stage = 'network'
		if tag != 'start':
			try:
				from gui.mods.offhangar.network_battle import stop_for_player as _stop_network_battle
				_stop_network_battle(BigWorld.player())
			except Exception:
				pass
		try:
			from gui.mods.offhangar.native_vehicle_physics_probe import cancel as _cancel_native_physics_probe
			_cancel_native_physics_probe(BigWorld.player())
		except Exception:
			pass
		_stage = 'music'
		try:
			import MusicController as _sweep_music
			_music_controller = getattr(_sweep_music, 'g_musicController', None)
			if (_music_controller is not None and
					getattr(_music_controller, '_offh_arena_lifecycle', False)):
				_music_controller.onLeaveArena()
				_music_controller._offh_arena_lifecycle = False
				_music_controller.stop()
		except Exception:
			pass
		globals().pop('g_offh_arena_snd', None)
		_stage = 'callbacks'
		for _callback_key in ('g_offh_aih_callback_id',
				'g_offh_capture_callback_id', 'g_offh_auto_spawn_callback_id'):
			_callback_id = globals().pop(_callback_key, None)
			if _callback_id is not None:
				try:
					BigWorld.cancelCallback(_callback_id)
					_n_callbacks += 1
				except Exception: pass
		_battle_callbacks = globals().pop('g_offh_battle_callbacks', {}) or {}
		for _callback_id in list(_battle_callbacks.keys()):
			try:
				BigWorld.cancelCallback(_callback_id)
				_n_callbacks += 1
			except Exception: pass
		_stage = 'targets'
		try:
			_target_player = BigWorld.player()
			_outlined_bot = getattr(_target_player, '_outlined_bot', None)
			if (_outlined_bot is not None and
					getattr(_outlined_bot, 'bw_entity', None) is not None):
				try: BigWorld.wgDelEdgeDetectEntity(_outlined_bot.bw_entity)
				except Exception: pass
			# Drop direct mock references before their native models/entities are
			# detached below. The original values (normally absent) are restored
			# by _offh_restore_player_battle_attrs later in the sweep.
			_target_player._outlined_bot = None
			_target_player._autoaim_target = None
		except Exception:
			pass
		_stage = 'mocks'
		_sticker_seen = {}
		_battle_window = None
		try:
			from gui import WindowsManager as _swwm
			_battle_window = getattr(_swwm.g_windowsManager, 'battleWindow', None)
		except Exception:
			pass
		try:
			from gui.mods.offhangar.native_bot_physics import stop_all as _stop_native_bot_physics
			_native_stopped = _stop_native_bot_physics(_mvd)
			if _native_stopped < 0:
				# A silent/no-op engine setter can leave one native owner attached.
				# Never continue by destroying its chassis/entity. The caller owns the
				# continuation and retries only after every reference is still intact.
				LOG_ERROR('NATIVE_BOT_PHYSICS battle sweep deferred '
					'reason=owner release incomplete')
				return False
		except Exception:
			LOG_CURRENT_EXCEPTION()
			return False
		# Native owners must release before the far-plane hold can be restored.
		# Otherwise remote terrain may unload while a C++ body still references it.
		# A rejected/silent restore is retryable and keeps every mock/space intact.
		_stage = 'streaming'
		try:
			_streaming_player = BigWorld.player()
			_streaming_bootstrap = getattr(
				_streaming_player, '_offh_spawn_streaming_bootstrap', None)
			if (_streaming_bootstrap is not None and
					_streaming_bootstrap.stop() is not True):
				LOG_ERROR('SPAWN_STREAMING battle sweep deferred '
					'reason=projection restore incomplete')
				return False
		except Exception:
			LOG_CURRENT_EXCEPTION()
			return False
		_stage = 'mocks'
		for _m in list(_mvd.values()):
			try:
				# Vehicle.stopVisual parity: remove GUI ownership and detach every
				# native visual child while the vehicle models still exist.
				try:
					_marker = getattr(_m, 'marker', None)
					_marker_manager = getattr(_battle_window, 'vMarkersManager', None)
					if _marker is not None and _marker != -1 and _marker_manager is not None:
						_marker_manager.destroyMarker(_marker)
						_m.marker = -1
				except Exception:
					pass
				try:
					_minimap = getattr(_battle_window, 'minimap', None)
					if _minimap is not None:
						_minimap.notifyVehicleStop(getattr(_m, 'id', 0))
				except Exception:
					pass
				try:
					_n_stickers += _offh_detach_stickers(
						getattr(_m, '_sticker_map', None), _sticker_seen)
					_m._sticker_map = {}
				except Exception:
					pass
				try: _stop_fire_effect(_m, died=True)
				except Exception: pass
				# Detach the engine-exhaust Pixie systems (native particles):
				# unreleased they leak past the battle into the hangar.
				try: _stop_engine_exhaust(_m)
				except Exception: pass
				for _sa in ('_snd_engine', '_snd_tracks'):
					try:
						_s = getattr(_m, _sa, None)
						if _s is not None:
							_s.stop()
						setattr(_m, _sa, None)
					except Exception:
						pass
				try:
					_filter = getattr(_m, 'filter', None)
					if _filter is not None:
						try: _filter.vehicleCollisionCallback = None
						except Exception: pass
						try: _filter.isLaggingStateChangedCallback = None
						except Exception: pass
				except Exception:
					pass
				try:
					_appearance = getattr(_m, 'appearance', None)
					_on_model_changed = getattr(_appearance, 'onModelChanged', None)
					if _on_model_changed is not None and hasattr(_on_model_changed, 'clear'):
						_on_model_changed.clear()
					_m.appearance = None
				except Exception:
					pass
				try:
					if getattr(_m, 'bw_entity', None) is not None:
						_m.bw_entity.model = None
						_m.bw_entity = None
				except Exception:
					pass
				try: _m._collision_obstacle = None
				except Exception: pass
				try: _m._offh_install_collision_obstacle = None
				except Exception: pass
				for _visual_attr in ('_gun_recoil', '_swinging', '_fashion',
						'_crashed_track_fashion', '_hull_model', '_turret_model',
						'_gun_model'):
					try: setattr(_m, _visual_attr, None)
					except Exception: pass
				try:
					# entity-owned chassis: ent.model=None above already released it;
					# delModel on it always raised (pending!) 'Not added as a global
					# model' - the very bomb this sweep kept tripping over.
					_m.model = None
					_m._chassis_model = None
				except Exception:
					pass
			except Exception:
				pass
		try:
			_pl_visual = BigWorld.player()
			if _pl_visual is not None:
				_n_stickers += _offh_detach_stickers(
					getattr(_pl_visual, '_offhangar_stickers', None), _sticker_seen)
				_n_stickers += _offh_detach_stickers(
					getattr(_pl_visual, '_offhangar_sticker_map', None), _sticker_seen)
				_pl_visual._offhangar_stickers = []
				_pl_visual._offhangar_sticker_map = {}
				_pl_visual._offhangar_gun_recoil = None
		except Exception:
			pass
		_stage = 'input'
		try:
			_input_player = BigWorld.player()
			_input_handler = getattr(_input_player, 'inputHandler', None)
			if _input_handler is not None:
				try: _input_handler.stop()
				except Exception:
					try:
						_input_handler._AvatarInputHandler__isStarted = False
						for _control in getattr(
								_input_handler, '_AvatarInputHandler__ctrls', {}).values():
							try: _control.destroy()
							except Exception: pass
					except Exception: pass
				try:
					import game as _input_game
					_resetter = getattr(
						_input_handler, '_AvatarInputHandler__onRecreateDevice', None)
					if _resetter is not None and _resetter in _input_game.g_guiResetters:
						_input_game.g_guiResetters.remove(_resetter)
				except Exception: pass
				try: _input_player.inputHandler = None
				except Exception: pass
		except Exception:
			pass
		_stage = 'mockdict'
		globals()['G_MOCK_VEHICLES'] = {}
		globals()['g_offh_exhaust_owners'] = []
		globals()['g_capture_tick_ref'] = None
		globals()['g_aih_tick_ref'] = None
		globals().pop('g_offline_formation_slot', None)
		globals().pop('g_offline_formation_pose', None)
		globals().pop('g_offh_bot_director', None)
		globals().pop('g_offh_terrain_navigator', None)
		globals().pop('g_offh_baked_navigation_graph', None)
		globals().pop('g_offh_spot_foliage', None)
		globals().pop('g_offh_spot_foliage_error', None)
		globals().pop('g_offh_local_driver', None)
		globals().pop('g_offh_ai_hull_dims', None)
		globals().pop('g_offh_ai_local_covers', None)
		globals().pop('g_offh_ai_cover_cursor', None)
		globals().pop('g_offh_ai_cover_t', None)
		globals().pop('g_offh_ai_cover_reports', None)
		globals().pop('g_offh_ai_artillery_cursor', None)
		globals().pop('g_offh_ai_network_contacts', None)
		globals().pop('g_offh_ai_frame_budget', None)
		globals().pop('g_offh_ai_contacts_t', None)
		globals().pop('g_offh_ai_diagnostics_t', None)
		globals().pop('g_offh_spot_resource_profiles', None)
		globals().pop('g_offh_spot_player_crew', None)
		globals().pop('g_offh_spot_fallback_logged', None)
		globals().pop('g_offh_ai_init_error_logged', None)
		globals().pop('g_offh_ai_navigation_disabled', None)
		for _nav_error_key in [value for value in globals()
		                       if value.startswith('g_offh_ai_navigation_error_')]:
			globals().pop(_nav_error_key, None)
		_stage = 'models'
		try:
			# Unregister always-update FIRST. The list is drained again later, but by
			# then these models are gone - calling delAlwaysUpdateModel on a deleted
			# model is exactly the dangling-reference case that crashes the next load.
			for _aum0 in list(globals().get('g_offh_always_update_models', []) or []):
				try:
					BigWorld.delAlwaysUpdateModel(_aum0)
				except Exception:
					pass
			globals()['g_offh_always_update_models'] = []
			# Clear the list BEFORE the loop: BigWorld.delModel leaves a PENDING
			# C error that this build only raises at the loop's EXHAUSTION (the
			# final FOR_ITER checks PyErr and finds it). If the clear sits AFTER
			# the loop it gets skipped, and the battle's models - including the
			# player's WRECK - leak into the next battle.
			_gm_list = list(g_offline_models or [])
			g_offline_models = []
			for _gm in _gm_list:
				_offh_del_model(_gm)
		except:
			pass
		_stage = 'hit_testers'
		try:
			_n_hit_testers, _n_hit_tester_failures = _offh_release_hit_testers()
		except Exception:
			pass
		try:
			# PlayerAvatar.onLeaveWorld performs this after releasing hit testers.
			# It is a no-op in the shipped 0.8.2 Cache implementation, but retain
			# the call so the offline lifecycle matches the original contract.
			from items import vehicles as _sweep_vehicles
			_sweep_vehicles.g_cache.clearPrereqs()
		except Exception:
			pass
		_stage = 'enemies'
		try:
			g_offline_enemies = []
		except Exception:
			pass
		_stage = 'sounds'
		_es = globals().get('g_offh_engine_state')
		if _es is not None:
			for _k in ('snd1', 'snd2'):
				try:
					if _es.get(_k) is not None:
						_es[_k].stop()
					_es[_k] = None
				except Exception:
					pass
		globals()['g_offh_engine_state'] = None
		_stage = 'voicenotif'
		# Crew-voice engine: destroy per battle, on EVERY exit path. The
		# instances live on persistent objects (account / module-global AIH);
		# left alive, a voice line active at exit keeps talking into the
		# hangar, and its never-ending 'voice' queue entry mutes all crew
		# voices for the rest of the session.
		try:
			_pl = BigWorld.player()
			_sn = getattr(_pl, 'soundNotifications', None) if _pl is not None else None
			if _sn is not None:
				try:
					_sn.destroy()
				except Exception:
					pass
				try:
					del _pl.soundNotifications
				except Exception:
					pass
		except Exception:
			pass
		try:
			_ga = globals().get('g_offline_aih')
			_sn2 = getattr(_ga, '_snd_notif', None) if _ga is not None else None
			if _sn2 is not None:
				try:
					_sn2.destroy()
				except Exception:
					pass
				try:
					del _ga._snd_notif
				except Exception:
					pass
		except Exception:
			pass
		_stage = 'projectile'
		try:
			# Drop impact closures before models/mocks disappear.  The callback itself
			# is battle-owned and was cancelled in the callback stage above.
			globals()['g_offh_live_projectiles'] = {}
			globals()['g_offh_live_projectile_callback_active'] = False
			_pm = globals().get('g_projectile_mover')
			if _pm is not None:
				try:
					_pm.destroy()
				except Exception:
					pass
				globals()['g_projectile_mover'] = None
		except Exception:
			pass
		_stage = 'destr'
		try:
			import AreaDestructibles
			# Stop the falling-body animator FIRST: a tree mid-fall at battle exit
			# leaves g_destructiblesAnimator's __updateCallback scheduled; it then
			# fires in the HANGAR against the released battle space ->
			# __launchFallEffect -> getDestructibleDesc(self.__spaceID=dead, ...) ->
			# native "argument 1 must be set to an int". clear() -> __stopUpdate()
			# cancels the BigWorld.callback. Manager.clear() alone did NOT (the
			# callback lives on the animator, a SEPARATE global) - that was the
			# "trees stop falling after a certain number of battles" report.
			_an = getattr(AreaDestructibles, 'g_destructiblesAnimator', None)
			if _an is not None and hasattr(_an, 'clear'):
				_an.clear()
			if getattr(AreaDestructibles, 'g_destructiblesManager', None) is not None:
				AreaDestructibles.g_destructiblesManager.clear()
		except Exception:
			pass
		_stage = 'effects'
		try:
			_pl = BigWorld.player()
			if _pl is not None and getattr(_pl, 'terrainEffects', None) is not None:
				try:
					_pl.terrainEffects.destroy()
				except Exception:
					pass
				try:
					_pl.terrainEffects = None
				except Exception:
					pass
		except Exception:
			pass
		_stage = 'muzzle'
		try:
			_pl = BigWorld.player()
			if _pl is not None:
				# Drop the battle descriptor so the hangar's vehicleTypeDescriptor
				# override falls back to its stub instead of the last battle's tank
				# (set at spawn for the native penetration marker).
				try: _pl._offhangar_td = None
				except Exception: pass
				# The swinging fashion is the SAME object as the chassis' wg_fashion, which
				# the sweep deletes with the model. Parking it on the persistent account and
				# leaving it there meant the hangar loaded on top of a dangling native
				# handle: 0xC0000005 Read@0x8 during loadHangarSpaceVehicle, right after a
				# battle that had itself run fine. Drop the reference here.
				try: _pl._offhangar_swinging = None
				except Exception: pass
				# The muzzle EffectsListPlayer lives on the persistent account:
				# unreleased it survives into the hangar and battle 2 would
				# replay battle 1's gun effects.
				_mzp = getattr(_pl, '_offhangar_muzzle_player', None)
				if _mzp is not None:
					try:
						_mzp.stop()
					except Exception:
						pass
					_pl._offhangar_muzzle_player = None
				try:
					_smap = getattr(_pl, '_offhangar_sticker_map', None)
					if _smap:
						_smap.clear()
				except Exception:
					pass
			# Always-update models are pinned by the ENGINE (strong native ref
			# + per-frame animation): without delAlwaysUpdateModel one gun
			# model per battle stays animated forever, hangar included.
			for _aum in list(globals().get('g_offh_always_update_models', []) or []):
				try:
					BigWorld.delAlwaysUpdateModel(_aum)
				except Exception:
					pass
			globals()['g_offh_always_update_models'] = []
		except Exception:
			pass
		_stage = 'snipercam'
		try:
			# Restore the original __cameraUpdate: the per-battle patch closure
			# pins mock_veh/loaded_models (a full tank model set) through the
			# hangar; battle start re-patches with fresh refs anyway.
			import AvatarInputHandler.cameras as _swcams
			_swo = getattr(_swcams.SniperCamera, '_orig_cam_update', None)
			if _swo is not None:
				_swcams.SniperCamera._SniperCamera__cameraUpdate = _swo
		except Exception:
			pass
		try:
			# SniperCamera.disable() is what normally restores the zoomed FOV
			# (cameras.py: __applyFOV(self.__fov)). Our exit path nulls inputHandler and
			# destroys the control modes, so it never runs - and the GARAGE then rendered
			# with the zoomed projection still applied. Put the captured FOV back.
			_fov0 = globals().get('g_offh_base_fov')
			if _fov0:
				BigWorld.projection().fov = _fov0
			# Drop the postmortem colour grading too, or the GARAGE renders desaturated -
			# exactly the mistake the zoomed-FOV bug above already made once.
			try:
				from post_processing import g_postProcessing as _offh_pp2
				_offh_pp2.disable()
			except Exception:
				pass
		except Exception:
			pass
		_stage = 'decals'
		try:
			# shell holes / track marks accumulate in native decal buffers
			if hasattr(BigWorld, 'wg_clearDecals'):
				BigWorld.wg_clearDecals()
		except Exception:
			pass
		# ANTI-FRAGMENTATION: with reuse_map_space on (default) KEEP the battle
		# map mapped between battles - the next same-map battle reuses it instead
		# of freeing + re-allocating ~700 MB, which fragmented the 32-bit address
		# space into an OOM crash. Dynamics are cleared by the 'models' +
		# 'entcache' stages regardless; the mapping is dropped lazily at battle
		# start only when a DIFFERENT map loads. reuse_map_space=false restores
		# the old per-battle unmap (frees memory, but fragments).
		_stage = 'space'
		try:
			from _constants import CONFIG_OPTIONS as _CFG_RM2
			_reuse_sp = bool(_CFG_RM2.get('reuse_map_space', True))
			_full_rel = bool(_CFG_RM2.get('full_space_release', False))
		except Exception:
			_reuse_sp = True
			_full_rel = False
		try:
			if _full_rel:
				# Full teardown: unmap + clearSpace + RELEASESPACE (frees the
				# chunk/terrain RAM; clearSpace only clears contents) + forced gc.
				_sid = globals().get('g_offh_battle_space', 0) or 0
				if _sid:
					# Stop the per-frame render pin so the tick stops forcing the
					# camera onto this space. Do NOT move the camera here (setting
					# it to the empty account space crashed on hangar load); the
					# hangar restore points the camera at its own space, and the
					# space is released only LATER (deferred callback) once it is
					# fully orphaned.
					globals()['g_offh_full_release'] = False
					_mh = globals().get('g_offh_battle_mapping')
					if _mh is not None:
						try:
							BigWorld.delSpaceGeometryMapping(_sid, _mh)
							len('')
						except:
							pass
					try:
						BigWorld.clearSpace(_sid)
					except:
						pass
					globals()['g_offh_battle_mapping'] = None
					globals()['g_offh_mapped_handle'] = None
					globals()['g_offh_mapped_name'] = None
					globals()['g_offh_mapped_space'] = 0
					globals()['g_offh_battle_space'] = 0
					# Defer releaseSpace to AFTER entcache destroys this space's
					# OfflineEntity bots + AreaDestructibles: releaseSpace on a
					# space that still holds entities does NOT fully free its
					# chunk/terrain RAM (measured: freed 0 in-sweep, baseline
					# still crept +700 MB/battle).
					globals()['g_offh_pending_release'] = _sid
			elif not _reuse_sp:
				_sid = globals().get('g_offh_battle_space', 0) or 0
				if _sid:
					# clearSpace alone never returns the chunk/terrain resources
					# (+600 MB); the mapping must be deleted explicitly.
					_mh = globals().get('g_offh_battle_mapping')
					if _mh is not None:
						try:
							BigWorld.delSpaceGeometryMapping(_sid, _mh)
							len('')
						except:
							pass
						globals()['g_offh_battle_mapping'] = None
						globals()['g_offh_mapped_handle'] = None
						globals()['g_offh_mapped_name'] = None
						globals()['g_offh_mapped_space'] = 0
					BigWorld.clearSpace(_sid)
					globals()['g_offh_battle_space'] = 0
		except Exception:
			pass
		# NOTE: ResMgr.purge(mapPath) was tried here and MEASURED freeing ~0 MB
		# across battles - it only drops DataSection descriptors (KB), not the
		# loaded chunk textures/geometry (MB) that the async chunk ejection from
		# clearSpace above owns. Removed as dead weight. Earlier measurements
		# attributed the residual climb to a process-wide vehicle texture cache,
		# but those runs also omitted retail's hit-tester release and visual-child
		# teardown. Re-measure after this lifecycle-complete sweep before imposing
		# any artificial vehicle-name limit.
		# clearSpace PARKS leaving entities in an engine-side cache instead
		# of destroying them: 30 OfflineEntity + the AreaDestructibles chunk
		# entities pile up there EVERY battle, pinning their resources.
		_stage = 'entcache'
		try:
			# OfflineEntity bots + AreaDestructibles live in BigWorld.entities,
			# NOT cachedEntities() (empty offline: logged "destroyed 0" EVERY
			# battle = the leak, bw_entities climbed 2->270 and never freed).
			# Iterate the real dict (the wrapper delegates .items() to it and
			# excludes injected mocks); the class filter below keeps hangar/
			# account ghosts safe.
			_cached_entities = getattr(BigWorld, 'cachedEntities', None)
			if callable(_cached_entities):
				_cached_entities = _cached_entities()
			_world_entities = getattr(BigWorld, 'entities', None)
			_pid = 0
			try:
				_pid = getattr(BigWorld.player(), 'id', 0) or 0
			except:
				pass
			# cachedEntities and entities are separate stores in this client. Scan both:
			# a single unrelated cached entity must not hide live OfflineEntity bots.
			_cids = _offh_battle_entity_ids(_cached_entities, _world_entities)
			_ndest = 0
			_n_entity_candidates = len(_cids)
			for _cid in _cids:
				if not _cid or _cid == _pid:
					continue
				try:
					BigWorld.destroyEntity(_cid)
					len('')
					_ndest += 1
				except:
					pass
			_n_entities = _ndest
			if _swlog is not None:
				try:
					_swlog.LOG_DEBUG('sweep: destroyed %d/%d battle entities (OfflineEntity+AreaDestructibles)' % (_ndest, len(_cids)))
				except:
					pass
		except:
			pass
		# Deferred full_space_release: entcache just destroyed this space's
		# OfflineEntity bots + AreaDestructibles, so it is now EMPTY and
		# releaseSpace can actually return its chunk/terrain RAM (it no-oped
		# earlier when entities were still parked in it).
		# Deferred full_space_release: the emptied battle space is released at
		# the START of the NEXT battle (stable context, space fully orphaned);
		# releasing in-sweep crashed on hangar load and a transition callback
		# never fired. g_offh_pending_release (set in the space stage) holds it.
		_stage = 'release'
		_stage = 'dastate'
		try:
			import gui.mods.offhangar.destructibles_authority as _dam
			# reset() keeps the dict SHAPE (spaceID/chunks/entities keys);
			# _state.clear() removed them and every destructible call the
			# next battle then KeyError'd, killing all destruction.
			_reset = getattr(_dam, 'reset', None)
			if callable(_reset):
				_reset()
			else:
				_dst = getattr(_dam, '_state', None)
				if isinstance(_dst, dict):
						_dst['spaceID'] = None
						_dst['chunks'] = {}
						_dst['entities'] = set()
		except:
			pass
		_stage = 'arena'
		try:
			_arena_player = BigWorld.player()
			_arena = getattr(_arena_player, '_offhangar_arena', None)
			if _arena is not None:
				_n_arena_delegates += _offh_clear_arena_events(_arena)
				try: delattr(_arena, 'collideWithSpaceBB')
				except Exception: pass
				try: _arena._offh_kill_wrapped = False
				except Exception: pass
				try: _arena.statistics.clear()
				except Exception: pass
				if tag != 'start':
					try: _arena.vehicles.clear()
					except Exception: pass
					try: _arena.extraData = {}
					except Exception: pass
			if tag != 'start' and _arena_player is not None:
				for _battle_attr in ('_offhangar_battle_ctx', '_offhangar_battle_stats',
						'_offhangar_mock_veh', '_offh_spec_mp', '_offh_spec_want'):
					try: setattr(_arena_player, _battle_attr, None)
					except Exception: pass
			if _arena_player is not None:
				for _battle_closure in ('_offhangar_apply_network_rules_state',
						'_offhangar_apply_network_battle_result',
						'_offhangar_prepare_native_authority_streaming',
						'_offhangar_network_spawn_remote',
						'_offhangar_network_formation'):
					try: setattr(_arena_player, _battle_closure, None)
					except Exception: pass
				try:
					_original_stats = getattr(_arena_player, '_offhangar_orig_stats', None)
					if _original_stats is not None:
						_arena_player.stats = _original_stats
				except Exception: pass
		except Exception:
			pass
		_stage = 'player_attrs'
		try:
			_n_player_attrs, _n_player_attr_failures = (
				_offh_restore_player_battle_attrs(BigWorld.player()))
		except Exception:
			pass
		# ResMgr.purge is UNSAFE here: DataSections are PROCESS-wide shared
		# (items.vehicles g_cache holds refs for the whole session); purging
		# them under its feet froze the engine on the next tank load.
		_stage = 'resmgr_skipped'
		# The dead battle scopes are reference CYCLES (closure<->cell<->
		# function); collect twice to drain cascades that the first pass
		# only unpins.
		_stage = 'gc'
		try:
			import gc as _gc
			_gc.collect()
			_gc.collect()
		except:
			pass
		# Engine memory report into the log (debug only): if anything still
		# holds megabytes after this sweep, the next log names it.
		_stage = 'memstats'
		try:
			if _swlog is not None and getattr(_swlog, '_DBG', [False])[0]:
				BigWorld.outputMemoryStats()
		except:
			pass
		if not globals().get('g_offh_apis_logged'):
			globals()['g_offh_apis_logged'] = True
			try:
				import ResMgr as _rmx
				_kw = ('flush', 'purge', 'cache', 'clear', 'release', 'texture', 'memory', 'reuse')
				_names = [_n for _n in dir(BigWorld) if any((_k in _n.lower()) for _k in _kw)]
				if _swlog is not None:
					_swlog.LOG_DEBUG('BW-APIs: %s' % (_names,))
					_swlog.LOG_DEBUG('ResMgr-APIs: %s' % (dir(_rmx),))
			except:
				pass
		_stage = 'done'
		# bare: BigWorld/FMOD can raise old-style exceptions that do NOT
		# inherit from Exception - 'except Exception' misses them.
	except:
		try:
			import traceback as _swtb
			_fail = _swtb.format_exc()
		except Exception:
			_fail = 'trace unavailable'
	_mem_after = _offh_proc_mem_mb()
	_residual_models = len(globals().get('g_offline_models', []) or [])
	_residual_mocks = len(globals().get('G_MOCK_VEHICLES', {}) or {})
	_residual_hit_testers = len(globals().get('g_offh_loaded_hit_testers', {}) or {})
	_residual_callbacks = len(globals().get('g_offh_battle_callbacks', {}) or {})
	_residual_player_attrs = 0
	_residual_arena_delegates = 0
	try:
		_residual_player = BigWorld.player()
		_residual_player_attrs = len(
			getattr(_residual_player, '_offh_player_attr_restore', None) or [])
		_residual_arena = getattr(_residual_player, '_offhangar_arena', None)
		for _residual_event in list(
				(getattr(_residual_arena, '_event_stubs', {}) or {}).values()):
			_residual_arena_delegates += len(
				getattr(_residual_event, 'delegates', None) or [])
	except Exception:
		_residual_player_attrs = -1
		_residual_arena_delegates = -1
	_pending_space = int(globals().get('g_offh_pending_release', 0) or 0)
	_mapped_space = int(globals().get('g_offh_mapped_space', 0) or 0)
	_residual_entities = 0
	try:
		_residual_source = getattr(BigWorld, 'entities', None)
		for _residual_id, _residual_entity in list(_residual_source.items()):
			try:
				if _residual_entity.__class__.__name__ in ('OfflineEntity', 'AreaDestructibles'):
					_residual_entities += 1
			except Exception:
				pass
	except Exception:
		_residual_entities = -1
	if _swlog is not None:
		try:
			if _fail is not None:
				_swlog.LOG_DEBUG('OfflineBattle.sweep(%s) FAILED at stage %s: %s' % (tag, _stage, _fail))
			_swlog.LOG_DEBUG('OfflineBattle.sweep(%s): freed models=%d mocks=%d stickers=%d hitBSP=%d hitBSP_fail=%d callbacks=%d entities=%d/%d playerAttrs=%d attr_fail=%d arenaDelegates=%d stage=%s | residual models=%d mocks=%d hitBSP=%d callbacks=%d entities=%d playerAttrs=%d arenaDelegates=%d pendingSpace=%d mappedSpace=%d | rss %d->%d virt %d->%d commit %d->%d MB (freed rss %d virt %d) [virt = 32-bit ~2GB wall]' % (
				tag, _n_models, _n_mocks, _n_stickers, _n_hit_testers,
				_n_hit_tester_failures, _n_callbacks, _n_entities,
				_n_entity_candidates, _n_player_attrs, _n_player_attr_failures,
				_n_arena_delegates, _stage, _residual_models, _residual_mocks,
				_residual_hit_testers, _residual_callbacks, _residual_entities,
				_residual_player_attrs, _residual_arena_delegates, _pending_space,
				_mapped_space,
				_mem_before[0], _mem_after[0], _mem_before[1], _mem_after[1], _mem_before[2], _mem_after[2],
				_mem_before[0] - _mem_after[0], _mem_before[1] - _mem_after[1]))
		except Exception:
			pass
		try:
			_offh_gc_census_line(tag)
		except Exception:
			pass
	return True


def _offh_sweep_or_retry(tag, continuation):
	"""Run the ownership barrier before an exit/start continuation.

	The normal battle callback registry is deliberately cancelled by the sweep.
	A failed native release therefore retries through a plain BigWorld callback;
	the caller resumes only after stop_all has proven every native owner detached.
	"""
	import BigWorld
	try:
		if _offh_battle_sweep(tag):
			(globals().get('g_offh_sweep_retry_pending', {}) or {}).pop(tag, None)
			return True
	except Exception:
		LOG_CURRENT_EXCEPTION()
	pending = globals().setdefault('g_offh_sweep_retry_pending', {})
	if pending.get(tag):
		return False
	pending[tag] = True
	def _retry():
		pending.pop(tag, None)
		return continuation()
	try:
		BigWorld.callback(0.1, _retry)
	except Exception:
		pending.pop(tag, None)
		LOG_CURRENT_EXCEPTION()
	return False

import BigWorld
try:
	from projectilemover import ProjectileMover
	def _safe_calc(self, r0, v0, gravity, isOwnShoot, tracerCameraPos):
		import BigWorld, Math, constants
		from projectile_trajectory import computeProjectileTrajectory
		_speed = v0.length
		if _speed <= 0.0001:
			return (r0 + Math.Vector3(0.0, 0.0, 100.0), 0.1)
		# The stock 0.8.2 implementation follows this exact parabola but calls
		# arena.collideWithSpaceBB, which the offline Arena adapter does not own.
		# Keep the original trajectory subdivision and static/water collision while
		# omitting only that unavailable arena-boundary helper.
		_tick = float(getattr(constants, 'SERVER_TICK_LENGTH', 0.1) or 0.1)
		_epsilon = float(getattr(
			constants, 'SHELL_TRAJECTORY_EPSILON_CLIENT', 0.03) or 0.03)
		_max_time = max(4.0, min(20.0, 2500.0 / _speed + 4.0))
		_elapsed = 0.0
		_prev_pos = r0
		_prev_velocity = v0
		_first_ray = True
		while _elapsed < _max_time:
			_points = computeProjectileTrajectory(
				_prev_pos, _prev_velocity, gravity, _tick, _epsilon)
			_old = _prev_pos
			_chord_total = 0.0
			_chord_cursor = _prev_pos
			for _chord_point in _points:
				_chord_total += (_chord_point - _chord_cursor).length
				_chord_cursor = _chord_point
			_chord_total = _chord_total or 1.0
			_chord_seen = 0.0
			for _point in _points:
				_ray_start = _old
				if _first_ray:
					_ray_start = r0 + v0.scale(3.0 / _speed)
					_first_ray = False
				_segment = (_point - _old).length
				_hit = BigWorld.wg_collideSegment(
					_offh_bspace(), _ray_start, _point, 128)
				_static_distance = ((_hit[0] - _ray_start).length
				                    if _hit is not None else 999999.0)
				_water_distance = -1.0
				try:
					_water_distance = BigWorld.wg_collideWater(
							_ray_start, _point)
				except Exception:
					pass
				if _hit is not None or _water_distance >= 0.0:
					if _water_distance >= 0.0 and _water_distance < _static_distance:
						_direction = _point - _ray_start
						_direction.normalise()
						_hit_point = _ray_start + _direction.scale(_water_distance)
						_local = _water_distance
					else:
						_hit_point = _hit[0]
						_local = _static_distance
					_fraction = min(1.0, max(0.0,
						(_chord_seen + _local) / _chord_total))
					_time = _elapsed + _tick * _fraction
					if (_hit_point - r0).length > 0.01 and _time > 0.001:
						return (_hit_point, _time)
				_chord_seen += _segment
				_old = _point
			_elapsed += _tick
			_prev_pos = r0 + v0.scale(_elapsed) + gravity.scale(
				_elapsed * _elapsed * 0.5)
			_prev_velocity = v0 + gravity.scale(_elapsed)
		return (_prev_pos, _max_time)
	def _safe_stop_plane(self, point, r0, v0, gravity):
		"""Build the impact plane from a stable trajectory-time component.

		The retail implementation divides by ``v0.x`` unconditionally.  A shot
		fired almost exactly north/south therefore cannot be stopped at a moving
		vehicle discovered after launch.  Use the strongest horizontal component
		and retain the same tangent-plane contract.
		"""
		from ClientArena import Plane
		x_speed = abs(float(v0[0]))
		z_speed = abs(float(v0[2]))
		if x_speed >= z_speed and x_speed > 0.00001:
			t = (float(point[0]) - float(r0[0])) / float(v0[0])
		elif z_speed > 0.00001:
			t = (float(point[2]) - float(r0[2])) / float(v0[2])
		else:
			t = 0.0
		t = max(0.0, float(t))
		velocity = v0 + gravity.scale(t)
		if velocity.length <= 0.00001:
			velocity = Math.Vector3(0.0, 0.0, 1.0)
		else:
			velocity.normalise()
		return Plane(velocity, velocity.dot(point))
	ProjectileMover._ProjectileMover__calcTrajectory = _safe_calc
	ProjectileMover._ProjectileMover__getStopPlane = _safe_stop_plane
	# Online the shell is hidden for the first 50 ms because the server's showTracer
	# arrives late and it would otherwise pop out of the barrel. Offline it spawns at
	# the muzzle immediately, so those 50 ms are pure loss - at ~1000 m/s that is the
	# first ~50 m, i.e. most of a bot engagement. Show it from the first tick.
	ProjectileMover._ProjectileMover__PROJECTILE_HIDING_TIME = 0.0
	g_projectile_mover = ProjectileMover()
except Exception as e:
	LOG_DEBUG('Could not init ProjectileMover:', e)
	g_projectile_mover = None

# Decal crash guard. Some effect lists reference decal groups/textures this
# 0.8.2 client never registers ('slow' group, 'explosion' texture). Native
# wg_addDecal RAISES on the unknown group, aborting EffectsList.attachTo
# halfway - the half-built effect state then kills the client with a native
# access violation (crash logs: "addDecal - invalid <groupName>" immediately
# before EXCEPTION_ACCESS_VIOLATION). Unknown textures resolve to index -1.
# Skip such decals silently; every other effect in the list still plays.
try:
	from helpers import EffectsList as _EL0
	from helpers import DecalMap as _DM0
	if not getattr(_EL0._DecalEffectDesc, '_offh_safe_create', False):
		_orig_decal_create = _EL0._DecalEffectDesc.create
		def _offh_safe_decal_create(self, model, list, args, _orig=_orig_decal_create):
			try:
				_texmap = getattr(_DM0.g_instance, '_DecalMap__texMap', None)
				if _texmap is not None and self._texName not in _texmap:
					return  # unknown texture: skip without LOG_ERROR spam
				return _orig(self, model, list, args)
			except Exception:
				return  # unknown decal group: native addDecal rejected it
		_EL0._DecalEffectDesc.create = _offh_safe_decal_create
		_EL0._DecalEffectDesc._offh_safe_create = True
	# Same protection for particle systems: a Pixie can embed a DECAL
	# RENDERER bound to a decal group ('slow' scorch marks). Attaching one
	# validates the group natively and RAISES when it is not registered on
	# this client (crash logs: _PixieEffectDesc.create -> _findTargetNode ->
	# 'addDecal - invalid <groupName>'), aborting attachTo halfway. Skip
	# just that pixie; the rest of the effect list still plays.
	if not getattr(_EL0._PixieEffectDesc, '_offh_safe_create', False):
		_orig_pixie_create = _EL0._PixieEffectDesc.create
		def _offh_safe_pixie_create(self, model, list, args, _orig=_orig_pixie_create):
			try:
				return _orig(self, model, list, args)
			except Exception:
				return
		_EL0._PixieEffectDesc.create = _offh_safe_pixie_create
		_EL0._PixieEffectDesc._offh_safe_create = True
except Exception:
	LOG_DEBUG('Decal guard install failed')


# shotResult -> shotEffects group, exactly as Vehicle.showDamageFromShot maps
# it: 0 ricochet, 1 non-penetration, 2/3 penetration, 4 critical.
_HIT_EFFECT_GROUPS = ('armorRicochet', 'armorResisted', 'armorHit', 'armorHit', 'armorCriticalHit')


def _terrain_hit_material(spaceID, hit_point, dir_vec):
	"""Effect-material name at a terrain/wall impact - one of
	'ground'/'stone'/'wood'/'metal'/'snow'/'sand' - mirroring
	Vehicle.showDamageFromShot: read the surface matKind with
	wg_getMatInfoNearPoint and map it via VehicleAppearance
	.calcEffectMaterialIndex. Falls back to 'ground' (water is handled
	inside ProjectileMover.explode itself). calcEffectMaterialIndex returns
	-1 for untagged terrain offline - the player is an account, not a
	PlayerAvatar, so its arena-default branch is skipped - hence the clamp."""
	try:
		import BigWorld
		from material_kinds import EFFECT_MATERIALS
		seg_start = hit_point - dir_vec.scale(0.5)
		seg_end = hit_point + dir_vec.scale(0.5)
		matInfo = BigWorld.wg_getMatInfoNearPoint(spaceID, seg_start, seg_end, hit_point, lambda *a: False)
		matKind = matInfo[4] if (matInfo is not None and len(matInfo) > 4) else 0
		from VehicleAppearance import VehicleAppearance as _VA
		effIdx = _VA.calcEffectMaterialIndex(matKind)
		if effIdx is None or effIdx < 0 or effIdx >= len(EFFECT_MATERIALS):
			return 'ground'
		return EFFECT_MATERIALS[effIdx]
	except Exception:
		return 'ground'


def _stop_engine_exhaust(mock):
	"""Detach + drop the engine-exhaust pixies from a tank (death / cleanup)."""
	try:
		pixies = getattr(mock, '_offhangar_exhaust', None)
		if not pixies:
			return
		for node, pixie in pixies:
			try:
				if node is not None:
					node.detach(pixie)
			except Exception:
				pass
		mock._offhangar_exhaust = None
		mock._offhangar_exhaust_rate_index = None
	except Exception:
		pass


def _sync_engine_exhaust(mock, hull_model, td, speed=0.0):
	"""Engine-exhaust smoke for ANY tank (player or bot), reusing the stock
	Pixie exhaust from hull['exhaust'] (VehicleAppearance.__createExhaust):
	one particle system per exhaust node, attached to the hull, with the
	emission rate driven off speed (idle vs moving) like __changeExhaust.
	Created once per tank and cached on mock._offhangar_exhaust. Owners are
	tracked in g_offh_exhaust_owners so the battle sweep detaches every one
	(else the native Pixie systems leak past the battle)."""
	try:
		import Pixie
		if mock is None or hull_model is None or td is None:
			return
		if getattr(mock, 'health', 1) <= 0:
			_stop_engine_exhaust(mock)
			return
		hull = getattr(td, 'hull', None)
		exhaust = hull.get('exhaust') if isinstance(hull, dict) else None
		if not exhaust:
			return
		nodes = exhaust.get('nodes') or ()
		rates = exhaust.get('rates') or ()
		if not nodes or not rates:
			return
		pixies = getattr(mock, '_offhangar_exhaust', None)
		if pixies is None:
			# Resolve the pixie by engine tag, exactly like the stock appearance.
			engine = getattr(td, 'engine', None)
			etags = (engine.get('tags') if isinstance(engine, dict) else getattr(engine, 'tags', None)) or ()
			pixie_name = None
			for tag in etags:
				pixie_name = exhaust.get('pixie/' + tag, pixie_name)
			if not pixie_name:
				return
			pixies = []
			for i in xrange(len(nodes)):
				try:
					pixie = Pixie.create(pixie_name)
					pixie.drawOrder = 50 + i
					node = hull_model.node(nodes[i])
					node.attach(pixie)
					pixies.append((node, pixie))
				except Exception:
					pass
			mock._offhangar_exhaust = pixies
			# Tracked so the battle sweep can detach every tank's pixies.
			globals().setdefault('g_offh_exhaust_owners', []).append(mock)
		# Emission rate: idle when stopped, higher when moving; clamp to table.
		idx = 1 if abs(speed) < 0.5 else 2
		last = len(rates) - 1
		if idx > last:
			idx = last
		if idx < 0:
			idx = 0
		# The rate table has only idle/moving states. Rewriting every native
		# particle-system action every render frame adds no visual information.
		if getattr(mock, '_offhangar_exhaust_rate_index', None) == idx:
			return
		rate = rates[idx]
		for node, pixie in pixies:
			try:
				for si in xrange(pixie.nSystems()):
					pixie.system(si).action(1).rate = rate
			except Exception:
				pass
		mock._offhangar_exhaust_rate_index = idx
	except Exception:
		LOG_CURRENT_EXCEPTION()


def _sync_bot_motion_sounds(mock, td, listener_position, speed_fwd,
		throttle, dt):
	"""Update bot engine/track events at 10 Hz, with native-value deduping.

	FMOD interpolates parameter changes itself. Writing two native parameters for
	every bot on every rendered frame only burns the old client's main thread;
	range gates and a 100 ms control rate preserve the same audible state.
	"""
	if mock is None or td is None or listener_position is None:
		return
	interval = 0.10
	accumulator = (getattr(mock, '_offh_sound_sync_acc', interval) or 0.0)
	accumulator += max(0.0, min(float(dt or 0.0), 0.25))
	if accumulator < interval:
		mock._offh_sound_sync_acc = accumulator
		return
	mock._offh_sound_sync_acc = accumulator % interval

	dx = float(mock.position.x) - float(listener_position[0])
	dz = float(mock.position.z) - float(listener_position[2])
	distance_sq = dx * dx + dz * dz
	if distance_sq > 16900.0:
		if (getattr(mock, '_snd_engine', None) is not None or
				getattr(mock, '_snd_tracks', None) is not None):
			for sound_name in ('_snd_engine', '_snd_tracks'):
				sound = getattr(mock, sound_name, None)
				if sound is not None:
					try:
						sound.stop()
					except Exception:
						pass
				setattr(mock, sound_name, None)
			mock._p_load = None
			mock._p_spd = None
			mock._offh_last_sound_load = None
			mock._offh_last_sound_speed = None
		mock._snd_init = False
		return

	if (not getattr(mock, '_snd_init', False) and distance_sq < 13225.0 and
			getattr(mock, 'isAlive', False)):
		engine = getattr(td, 'engine', None)
		chassis = getattr(td, 'chassis', None)
		model = getattr(mock, '_chassis_model', None)
		if engine and model is not None and getattr(model, 'inWorld', False):
			mock._snd_engine = model.playSound(engine['sound'])
		if chassis and model is not None and getattr(model, 'inWorld', False):
			mock._snd_tracks = model.playSound(chassis['sound'])
		if model is not None and getattr(model, 'inWorld', False):
			mock._snd_init = True
		if getattr(mock, '_snd_tracks', None):
			for name in ('ground', 'stone', 'wood', 'snow', 'sand', 'water',
					'hardness', 'friction', 'roughness', 'flying'):
				try:
					param = mock._snd_tracks.param(name)
					if param is not None:
						param.value = 0.0
				except Exception:
					pass

	current_speed = abs(float(getattr(mock, '_veh_velocity', 0.0) or 0.0))
	speed_limit = max(0.1, float(speed_fwd or 0.0))
	power_fraction = min(1.0, current_speed / speed_limit + abs(float(throttle)) * 0.3)
	load = 1.0 + power_fraction * 2.0
	speed_ratio = current_speed / speed_limit
	if getattr(mock, '_snd_engine', None):
		param = getattr(mock, '_p_load', None)
		if param is None:
			param = mock._snd_engine.param('load')
			mock._p_load = param
		last = getattr(mock, '_offh_last_sound_load', None)
		if param and (last is None or abs(float(last) - load) >= 0.02):
			param.value = load
			mock._offh_last_sound_load = load
	if getattr(mock, '_snd_tracks', None):
		param = getattr(mock, '_p_spd', None)
		if param is None:
			param = mock._snd_tracks.param('speed')
			mock._p_spd = param
		last = getattr(mock, '_offh_last_sound_speed', None)
		if param and (last is None or abs(float(last) - speed_ratio) >= 0.02):
			param.value = speed_ratio
			mock._offh_last_sound_speed = speed_ratio


def _play_vehicle_hit_effect(shell, hit_pos, hit_dir, shot_result, is_player_target=False, target_mock=None):
	"""Play the armor hit / bounce / ricochet effect at a mock vehicle hit
	point. Mock tanks are not real collision geometry, so the ProjectileMover
	(which only collides against static geometry) never shows an impact on
	them. Replay the same shotEffects group the real Vehicle uses, in world
	space via player.terrainEffects (the same channel destructibles use).

	The fullscreen shockwave/flashbang default to ON in EffectsListPlayer, so
	they are only enabled when the PLAYER's own tank is hit - otherwise every
	bot-on-bot / player-on-bot hit would red-flash the whole screen."""
	try:
		import BigWorld, Math
		from items import vehicles
		player = BigWorld.player()
		te = getattr(player, 'terrainEffects', None)
		if te is None or shell is None or hit_pos is None:
			return
		idx = shell.get('effectsIndex') if isinstance(shell, dict) else getattr(shell, 'effectsIndex', None)
		if idx is None:
			return
		effectsDescr = vehicles.g_cache.shotEffects[idx]
		key = _HIT_EFFECT_GROUPS[max(0, min(int(shot_result), len(_HIT_EFFECT_GROUPS) - 1))]
		stages, effects, _ = effectsDescr[key]
		d = Math.Vector3(hit_dir)
		try:
			d.normalise()
		except Exception:
			d = Math.Vector3(0.0, 1.0, 0.0)
		te.addNew(hit_pos, effects, stages, None, dir=d, start=hit_pos - d, end=hit_pos + d,
		          showShockWave=is_player_target, showFlashBang=is_player_target)
		# Rock the target hull like Vehicle.showDamageFromShot does
		try:
			fashion = getattr(target_mock, '_swinging', None)
			if fashion is None and is_player_target:
				fashion = getattr(player, '_offhangar_swinging', None)
			_trigger_shot_impulse(fashion, d, effectsDescr['targetImpulse'])
		except Exception:
			pass
	except Exception:
		LOG_CURRENT_EXCEPTION()


_DS_NAMES_LOGGED = [False]


def _pick_damage_sticker(shot_result=2):
	"""Return a damage-sticker descr chosen by shot outcome: a penetration
	hole for pens (result >= 2), a scratch/ricochet mark otherwise. Selection
	is deterministic (not random) so a pen never shows a scuff and vice versa.
	Logs the available sticker names once for tuning."""
	try:
		from items import vehicles
		ds = vehicles.g_cache.damageStickers
		descrs = ds.get('descrs') if isinstance(ds, dict) else None
		if not descrs:
			return None
		ids = ds.get('ids', {}) if isinstance(ds, dict) else {}
		if not _DS_NAMES_LOGGED[0]:
			_DS_NAMES_LOGGED[0] = True
			LOG_DEBUG('DecalDBG: damage sticker names:', list(ids.keys()))
		pen = int(shot_result) >= 2
		pen_kw = ('pierc', 'penetr', 'hole', 'through', 'shot')
		nonpen_kw = ('scratch', 'ricochet', 'splash', 'nopen', 'no_pen', 'scuff', 'blast')
		want = pen_kw if pen else nonpen_kw
		# exact name match first, then substring
		for name, sid in ids.iteritems():
			low = str(name).lower()
			if any(k in low for k in want):
				return descrs[sid]
		# deterministic fallback: highest-priority sticker, else the first
		try:
			return sorted(descrs, key=lambda d: -int(d.get('priority', 0)))[0]
		except Exception:
			return descrs[0]
	except Exception:
		return None


def _comp_name_from_hits(td, all_hits):
	"""Map the first hit component descriptor to its name (hull/turret/gun/
	chassis) so the decal lands on the right component model."""
	try:
		for _h in (all_hits or []):
			_hc = _h[3] if len(_h) > 3 else None
			if _hc is None:
				continue
			if _hc is getattr(td, 'turret', None):
				return 'turret'
			if _hc is getattr(td, 'gun', None):
				return 'gun'
			if _hc is getattr(td, 'chassis', None):
				return 'chassis'
			if _hc is getattr(td, 'hull', None):
				return 'hull'
	except Exception:
		pass
	return 'hull'


def _setup_gun_recoil(gun_model, td):
	"""Create a WGGunRecoil fashion on the gun model (node 'G'), configured
	from the gun's recoil descriptor. Returns the recoil object to trigger
	per shot, or None."""
	try:
		import BigWorld
		if gun_model is None or td is None:
			return None
		gun = getattr(td, 'gun', None)
		rd = gun.get('recoil') if isinstance(gun, dict) else getattr(gun, 'recoil', None)
		if not rd:
			return None
		recoil = BigWorld.WGGunRecoil('G')
		try:
			recoil.setLod(rd['lodDist'])
		except Exception:
			pass
		recoil.setDuration(rd['backoffTime'], rd['returnTime'])
		recoil.setDepth(rd['amplitude'])
		gun_model.wg_gunRecoil = recoil
		return recoil
	except Exception:
		LOG_CURRENT_EXCEPTION()
		return None


def _trigger_gun_recoil(recoil):
	"""Play the barrel recoil animation for one shot."""
	try:
		if recoil is not None:
			recoil.recoil()
	except Exception:
		pass


def _setup_swinging(chassis_model, td):
	"""DISABLED: attaching this minimal WGVehicleFashion to a mock chassis
	crashes the engine natively on battle start (EXCEPTION_ACCESS_VIOLATION,
	near-null read) - the C++ fashion update expects the track/wheel/filter
	setup VehicleAppearance always provides. Re-enabling needs the full
	setup (setTracks with the chassis track materials, wheel groups, and a
	movementInfo provider). Kept as a stub so the _trigger_shot_impulse
	call sites stay wired; they no-op on a None fashion.

	Original intent: pitch/roll swinging + shot impulse on root node 'V',
	configured from the hull's swinging descriptor like
	VehicleAppearance._setupVehicleFashion. The
	full fashion also animates tracks/wheels, but that needs the vehicle
	filter's movementInfo which mock vehicles lack - so track/wheel/trace
	LODs are set to 0 (disabled) and only the swinging is active."""
	return None
	try:
		import BigWorld
		if chassis_model is None or td is None:
			return None
		swingingCfg = td.hull['swinging']
		fashion = BigWorld.WGVehicleFashion()
		try:
			fashion.maxMovement = td.physics['speedLimits'][0]
		except Exception:
			pass
		# same pitch modifiers VehicleAppearance applies
		_mods = (0.9, 1.88, 0.3, 4.0, 1.0, 1.0)
		pp = tuple(p * m for (p, m) in zip(swingingCfg['pitchParams'], _mods))
		fashion.setPitchSwinging('V', *pp)
		fashion.setRollSwinging('V', *swingingCfg['rollParams'])
		fashion.setShotSwinging('V', swingingCfg['sensitivityToImpulse'])
		fashion.setLods(0.0, 0.0, 0.0, swingingCfg['lodDist'])
		chassis_model.wg_fashion = fashion
		return fashion
	except Exception:
		LOG_CURRENT_EXCEPTION()
		return None


def _trigger_shot_impulse(fashion, d, impulse):
	"""Rock the hull: on firing (backward along the barrel, gun[impulse])
	and on getting hit (hit direction, shotEffects targetImpulse)."""
	try:
		if fashion is not None and d is not None and impulse:
			fashion.receiveShotImpulse(d, impulse)
	except Exception:
		pass


def _play_death_effect(td, position, is_player=False, ammo_rack=False):
	"""Vehicle destruction effect like VehicleAppearance.__playEffect:
	'explosion' for ammo-rack kills, 'destruction' otherwise, played in
	world space via terrainEffects (the wreck model swap detaches the live
	hull model, so attaching to it would cut the effect short). Fullscreen
	shockwave/flashbang only for the player's own tank, like the game."""
	try:
		import random, BigWorld, Math
		player = BigWorld.player()
		te = getattr(player, 'terrainEffects', None)
		if te is None or td is None or position is None:
			return
		kind = 'explosion' if ammo_rack else 'destruction'
		effs = td.type.effects.get(kind) or td.type.effects.get('destruction')
		if not effs:
			return
		stages, effects, _unused = random.choice(effs)
		pos = Math.Vector3(position)
		te.addNew(pos, effects, stages, None,
		          start=pos + Math.Vector3(0.0, -1.0, 0.0),
		          end=pos + Math.Vector3(0.0, 1.0, 0.0),
		          showShockWave=is_player, showFlashBang=is_player)
	except Exception:
		LOG_CURRENT_EXCEPTION()


def _start_fire_effect(mock, hull_model, td):
	"""Burning-tank flames, mirroring the Fire extra: attach the 'fire'
	stage of a random 'flaming' effects list to the hull model."""
	try:
		import random
		if mock is None or hull_model is None or td is None:
			return
		effs = td.type.effects.get('flaming')
		if not effs:
			return
		stages, effects, _unused = random.choice(effs)
		if len(stages) != 2 or stages[0][0] != 'fire' or stages[1][0] != 'noEmission':
			return
		data = {}
		effects.attachTo(hull_model, data, 'fire')
		mock._fire_fx = {'effects': effects, 'data': data, 'hull': hull_model, 'noEmissionTime': stages[1][1]}
	except Exception:
		LOG_CURRENT_EXCEPTION()


def _stop_fire_effect(mock, died=False):
	"""Stop the flames: on extinguish let the smoke (noEmission stage) fade
	out like the Fire extra does; on death cut everything immediately."""
	try:
		import BigWorld
		fx = getattr(mock, '_fire_fx', None)
		if not fx:
			return
		mock._fire_fx = None
		effects, data, hull = fx['effects'], fx['data'], fx['hull']
		if died:
			effects.detachAllFrom(data)
			return
		effects.detachFrom(data, 'fire')
		effects.attachTo(hull, data, 'noEmission')
		_offh_battle_callback(
			fx['noEmissionTime'], lambda: effects.detachAllFrom(data))
	except Exception:
		pass


def _sync_burn_and_death(mock, hull_model, td):
	"""Per-tick visual sync for ANY tank (player or bot), regardless of what
	killed or ignited it (shells, fire, ammo rack): flames while burning, a
	one-shot destruction/ammo-rack explosion on death."""
	try:
		import BigWorld
		if mock is None:
			return
		alive = getattr(mock, 'health', 1) > 0
		burning = bool(getattr(mock, 'is_on_fire', False)) and alive
		if burning and getattr(mock, '_fire_fx', None) is None:
			_start_fire_effect(mock, hull_model, td)
		elif not burning and getattr(mock, '_fire_fx', None) is not None:
			_stop_fire_effect(mock, died=not alive)
		if not alive and not getattr(mock, '_death_fx_played', False):
			mock._death_fx_played = True
			# One place that catches EVERY death, player and bot, whatever killed them:
			# put every module and every crewman out. Only the drowning path used to.
			try:
				import BigWorld as _bwd
				_is_p = (getattr(mock, 'id', -1) == getattr(_bwd.player(), 'playerVehicleID', -1))
				_offh_knock_out_everything(mock, _is_p)
			except Exception as _kae:
				LOG_DEBUG('death knockout err:', str(_kae))
			# Tracks stop dead. WGVehicleFashion.movementInfo is a SPEED, not a frame:
			# the native scroll keeps running on the last value forever. That never
			# showed before because the wreck swap threw the fashion away with the old
			# models - a drowned tank keeps its intact models, so its tracks rolled on.
			try:
				import Math as _Md
				_fa_d = getattr(mock, '_fashion', None)
				if _fa_d is not None:
					_fa_d.movementInfo = _Md.Vector4(0.0, 0.0, 0.0, 0.0)
			except Exception:
				pass
			mock._veh_velocity = 0.0
			mock._veh_turn_velocity = 0.0
			# Dead engines are silent: the real game stops the engine and
			# movement sounds when the vehicle is destroyed.
			for _sname in ('_snd_engine', '_snd_tracks'):
				_snd = getattr(mock, _sname, None)
				if _snd is not None:
					try:
						_snd.stop()
					except Exception:
						pass
					setattr(mock, _sname, None)
			# Param handles of the stopped events must not outlive them
			for _pname in ('_p_load', '_p_spd'):
				try:
					setattr(mock, _pname, None)
				except Exception:
					pass
			try:
				is_pl = getattr(BigWorld.player(), 'playerVehicleID', -2) == getattr(mock, 'id', -1)
			except Exception:
				is_pl = False
			# drowned tanks sank, they did not explode - skip the destruction effect
			if not getattr(mock, '_drowned', False):
				_play_death_effect(td, getattr(mock, 'position', None), is_player=is_pl,
				                   ammo_rack=bool(getattr(mock, '_ammo_rack_death', False)))
	except Exception:
		pass


def _offh_set_battle_gui_mode(visible):
	"""Single owner of the battle pause-menu input state (cursor visibility +
	AvatarInputHandler input gate). Driven from BOTH the ESC key hook and the
	patched Battle.cursorVisibility flash callback, so every way of opening/
	closing the menu leaves the two consistent (idempotent, not a toggle)."""
	try:
		import BigWorld, GUI
		p = BigWorld.player()
		if p is None:
			return
		p._offhangar_gui_visible = bool(visible)
		aih = getattr(p, 'inputHandler', None)
		if visible:
			BigWorld.setCursor(GUI.mcursor())
			GUI.mcursor().visible = True
			if aih is not None:
				aih._AvatarInputHandler__isStarted = False
		else:
			if aih is not None:
				aih._AvatarInputHandler__isStarted = True
			GUI.mcursor().visible = False
			BigWorld.setCursor(getattr(GUI, 'ccursor', GUI.mcursor)())
	except Exception:
		pass


def _fallback_gun_sound(td, model):
	"""Generic caliber-bucket shot sound. Used ONLY when the gun's own
	effects list could not play: the effects carry the real per-gun sound
	(EffectsList 'sound' element), exactly like the live game, so forcing
	a bucket sound on top doubles it with a generic (often wrong) one."""
	try:
		if model is None:
			return
		caliber = 75
		try:
			gun = getattr(td, 'gun', None)
			if isinstance(gun, dict) and 'shots' in gun:
				caliber = gun['shots'][0]['shell']['caliber']
		except Exception:
			pass
		if caliber > 120:
			sound_event = '/tanks/guns/gun_huge/gun_huge_152mm'
		elif caliber > 100:
			sound_event = '/tanks/guns/gun_large/gun_large_115-152mm'
		elif caliber > 75:
			sound_event = '/tanks/guns/gun_main/gun_main_85-107mm'
		elif caliber > 45:
			sound_event = '/tanks/guns/gun_medium/gun_medium_50-75mm'
		else:
			sound_event = '/tanks/guns/gun_small/gun_small_20-45mm'
		model.playSound(sound_event)
	except Exception:
		pass


_INPUT_DBG = {'total': 0, 'pre_eaten': 0, 'aih': 0, 'started': '?', 'detach': '?', 'installed': False}


def _install_input_chain_debug():
	"""Temporary diagnostic for the intermittent camera aimlock: counts where
	mouse events die in the chain game.handleMouseEvent ->
	AvatarInputHandler.handleMouseEvent -> control mode, and logs a state
	snapshot every 2s while in battle. Remove once the lock is understood."""
	if _INPUT_DBG['installed']:
		return
	# Debug-only: with logging off (player build) do NOT wrap the mouse-event
	# chain nor start the 2s dump loop - pure overhead for players.
	try:
		from gui.mods.offhangar.logging import _DBG as _in_dbg
		if not _in_dbg[0]:
			return
	except Exception:
		return
	_INPUT_DBG['installed'] = True
	try:
		import game, BigWorld
		import AvatarInputHandler as _AIH_mod

		_orig_game_hme = game.handleMouseEvent
		def _dbg_game_hme(event):
			_INPUT_DBG['total'] += 1
			before = _INPUT_DBG['aih']
			result = _orig_game_hme(event)
			if _INPUT_DBG['aih'] == before:
				_INPUT_DBG['pre_eaten'] += 1
			return result
		game.handleMouseEvent = _dbg_game_hme

		_orig_aih_hme = _AIH_mod.AvatarInputHandler.handleMouseEvent
		def _dbg_aih_hme(self, dx, dy, dz):
			_INPUT_DBG['aih'] += 1
			_INPUT_DBG['started'] = getattr(self, '_AvatarInputHandler__isStarted', '?')
			_INPUT_DBG['detach'] = getattr(self, '_AvatarInputHandler__detachCount', '?')
			return _orig_aih_hme(self, dx, dy, dz)
		_AIH_mod.AvatarInputHandler.handleMouseEvent = _dbg_aih_hme

		def _dump():
			try:
				BigWorld.callback(2.0, _dump)
				p = BigWorld.player()
				if p is None or getattr(p, 'arena', None) is None:
					return
				if not _INPUT_DBG['total']:
					return
				try:
					ih = getattr(p, 'inputHandler', None)
					ctrl = getattr(ih, 'ctrl', None) if ih is not None else None
					ctrl_name = ctrl.__class__.__name__ if ctrl is not None else 'None'
					enabled = '?'
					if ctrl is not None:
						for k, v in ctrl.__dict__.items():
							if k.endswith('__isEnabled'):
								enabled = v
								break
				except Exception:
					ctrl_name, enabled = '?', '?'
				try:
					cam = BigWorld.camera()
					cam_name = cam.__class__.__name__ if cam is not None else 'None'
				except Exception:
					cam_name = '?'
				try:
					import GUI
					cursor_on = GUI.mcursor().visible
				except Exception:
					cursor_on = '?'
				LOG_DEBUG('InputDBG: mouse=%s eaten_before_aih=%s reached_aih=%s isStarted=%s detach=%s ctrl=%s enabled=%s cam=%s mcursor=%s period=%s' % (
					_INPUT_DBG['total'], _INPUT_DBG['pre_eaten'], _INPUT_DBG['aih'],
					_INPUT_DBG['started'], _INPUT_DBG['detach'], ctrl_name, enabled,
					cam_name, cursor_on, getattr(getattr(p, 'arena', None), 'period', '?')))
				_INPUT_DBG['total'] = 0
				_INPUT_DBG['pre_eaten'] = 0
				_INPUT_DBG['aih'] = 0
			except Exception:
				pass
		BigWorld.callback(2.0, _dump)
		LOG_DEBUG('InputDBG: input chain diagnostics installed')
	except Exception:
		LOG_CURRENT_EXCEPTION()


def _play_ground_wave(gun_model, td):
	"""Dust kicked up off the ground under the barrel when firing, mirroring
	vehicle_extras.ShowShooting.__doGroundWaveEffect."""
	try:
		import BigWorld, Math
		if gun_model is None or td is None:
			return
		gun = getattr(td, 'gun', None)
		gwv = gun.get('groundWave') if isinstance(gun, dict) else getattr(gun, 'groundWave', None)
		if not gwv:
			return
		player = BigWorld.player()
		if player is None:
			return
		node = gun_model.node('HP_gunFire')
		gunPos = Math.Matrix(node).translation
		testRes = BigWorld.wg_collideSegment(_offh_bspace(), gunPos + Math.Vector3(0, 0.5, 0), gunPos - Math.Vector3(0, 4.0, 0), 128)
		if testRes is None:
			return
		position = testRes[0]
		stages, effects = gwv[0], gwv[1]
		player.terrainEffects.addNew(position, effects, stages, None, dir=testRes[1], start=position + Math.Vector3(0, 0.5, 0), end=position - Math.Vector3(0, 0.5, 0))
	except Exception:
		pass


def _play_muzzle_flash(owner, gun_model, td, is_player=False):
	"""Play the gun muzzle flash (+ ground dust) for one shot, uniformly for
	the player and bots. Reuses a single EffectsListPlayer per gun, stopped
	before replay, exactly like vehicle_extras.ShowShooting."""
	try:
		import BigWorld
		if gun_model is None or td is None or owner is None:
			return
		gun = getattr(td, 'gun', None)
		ge = gun.get('effects') if isinstance(gun, dict) else getattr(gun, 'effects', None)
		if not isinstance(ge, (tuple, list)) or len(ge) < 2:
			return
		stages, effects = ge[0], ge[1]
		# Keep the gun model animating every frame so recoil + flash play out
		# even when the camera is still (matches the game's isPlayer path).
		if is_player and not getattr(gun_model, '_offhangar_always_update', False):
			try:
				BigWorld.addAlwaysUpdateModel(gun_model)
				gun_model._offhangar_always_update = True
				# engine holds a strong ref + animates it every frame:
				# tracked so the battle sweep can delAlwaysUpdateModel it
				globals().setdefault('g_offh_always_update_models', []).append(gun_model)
			except Exception:
				pass
		# Shooter's models are HIDDEN (player zoomed into sniper / unspotted
		# bot): skip the flash visuals entirely - particles spawned on a hidden
		# model do not animate, so they reappeared FROZEN in the air when the
		# model was shown again ('muzzle flash visible after leaving sniper').
		# Play only the shot sound, using the gun's own EffectsList sound
		# element (the same one attachTo would have played).
		_offh_hidden = (is_player and not getattr(gun_model, 'visible', True)) or \
			((not is_player) and not getattr(owner, '_spot_visible', True))
		if _offh_hidden:
			try:
				for _sdesc in (getattr(effects, '_EffectsList__effectDescList', None) or []):
					_snm = getattr(_sdesc, '_soundName', None)
					if _snm:
						_snd = gun_model.getSound(_snm)
						if _snd is not None:
							_snd.play()
							return True
						break
			except Exception:
				pass
			return None  # caller falls back to the caliber-bucket sound
		from helpers import EffectsList
		mzp = getattr(owner, '_offhangar_muzzle_player', None)
		if mzp is None:
			mzp = EffectsList.EffectsListPlayer(effects, stages)
			owner._offhangar_muzzle_player = mzp
		else:
			try:
				mzp.stop()
			except Exception:
				pass
		mzp.play(gun_model)
		_play_ground_wave(gun_model, td)
		return True
	except Exception:
		pass


def play_network_remote_shot(attacker_mock, start_pos, aim_yaw, gun_pitch, shell_index=0):
	"""Render one server-relayed human shot with the existing bot effects."""
	try:
		import BigWorld, Math, math, random
		from items import vehicles
		if attacker_mock is None:
			return False
		attacker_mock._offh_spot_last_shot = float(BigWorld.time())
		td = getattr(attacker_mock, 'typeDescriptor', None)
		gun_model = getattr(attacker_mock, '_gun_model', None)
		if td is None or gun_model is None:
			return False
		shots = td.gun.get('shots', []) if isinstance(td.gun, dict) else []
		if not shots:
			return False
		idx = max(0, min(int(shell_index or 0), len(shots) - 1))
		shot = shots[idx]
		pitch = float(gun_pitch or 0.0)
		direction = Math.Vector3(
			math.sin(float(aim_yaw)) * math.cos(pitch),
			-math.sin(pitch),
			math.cos(float(aim_yaw)) * math.cos(pitch))
		direction.normalise()
		muzzle = None
		try:
			muzzle = Math.Matrix(gun_model.node('HP_gunFire')).translation
		except Exception:
			pass
		if muzzle is None:
			muzzle = Math.Vector3(start_pos)
			muzzle.y += 1.5

		mover = globals().get('g_projectile_mover')
		if mover is not None:
			effects_descr = vehicles.g_cache.shotEffects[shot['shell']['effectsIndex']]
			velocity = direction.scale(shot['speed'])
			camera_pos = BigWorld.camera().position if BigWorld.camera() else muzzle
			shot_id = random.randint(10000, 99999)
			globals()['g_offh_adding_projectile'] = True
			try:
				mover.add(shot_id, effects_descr, shot['gravity'], muzzle,
					velocity, muzzle, True, camera_pos)
			finally:
				globals()['g_offh_adding_projectile'] = False
			try:
				projectile = getattr(mover, '_ProjectileMover__projectiles', {}).get(shot_id)
				if projectile is not None:
					projectile['fireMissedTrigger'] = False
			except Exception:
				pass

		_trigger_gun_recoil(getattr(attacker_mock, '_gun_recoil', None))
		played = _play_muzzle_flash(attacker_mock, gun_model, td, is_player=False)
		if not played:
			_fallback_gun_sound(td, getattr(attacker_mock, '_chassis_model', None))
		return True
	except Exception:
		LOG_CURRENT_EXCEPTION()
		return False


def play_network_hit_feedback(player, attacker_mock, target_mock, hit_pos,
		shot_result, damage, shell_index=0, local_target=False, local_attacker=False,
		dead=False):
	"""Present a relayed hit to the victim, shooter, or a third observer."""
	try:
		import Math, math
		damage = max(0, int(damage or 0))
		shot_result = max(0, min(int(shot_result or 0), 2))
		td = getattr(attacker_mock, 'typeDescriptor', None)
		if td is None and local_attacker:
			td = globals().get('loaded_models', {}).get('td')
		shots = td.gun.get('shots', []) if td is not None and isinstance(td.gun, dict) else []
		idx = max(0, min(int(shell_index or 0), len(shots) - 1)) if shots else 0
		shell = shots[idx].get('shell') if shots else None

		direction = Math.Vector3(0.0, 0.0, 1.0)
		try:
			attacker_pos = getattr(attacker_mock, 'position', None)
			target_pos = getattr(target_mock, 'position', None)
			if attacker_pos is not None and target_pos is not None:
				direction = Math.Vector3(target_pos.x - attacker_pos.x,
					target_pos.y - attacker_pos.y, target_pos.z - attacker_pos.z)
				direction.normalise()
		except Exception:
			pass

		# The firing client already played its exact collision effect locally.
		# Victims and spectators need the relayed impact reconstructed here.
		if not local_attacker and shell is not None and hit_pos is not None:
			_play_vehicle_hit_effect(shell, hit_pos, direction, shot_result,
				is_player_target=local_target, target_mock=target_mock)

		if local_target:
			try:
				px = getattr(target_mock, 'position', hit_pos)
				ap = getattr(attacker_mock, 'position', None)
				if px is not None and ap is not None:
					hit_yaw = math.atan2(-(ap.x - px.x), -(ap.z - px.z))
					aim = getattr(getattr(player, 'inputHandler', None), 'aim', None)
					if aim is not None and hasattr(aim, 'showHit'):
						aim.showHit(hit_yaw, damage > 0)
			except Exception:
				pass
			_offh_hit_sound('/hits/hits/tank_hit_armor_crit' if damage > 0 else
				'/hits/hits/tank_hit_armor_ricochet')
			try:
				ctrl = getattr(getattr(player, 'inputHandler', None), 'ctrl', None)
				camera = getattr(ctrl, 'camera', None) if ctrl is not None else None
				impulse = Math.Vector3(direction.x, 0.0, direction.z)
				impulse.normalise()
				if camera is not None and hasattr(camera, 'applyImpulse'):
					camera.applyImpulse(impulse, 1.0 if damage > 0 else 0.5)
			except Exception:
				pass
		elif local_attacker:
			try:
				if dead:
					sound_name = 'enemy_killed_by_player'
				elif damage > 0:
					sound_name = 'armor_pierced_by_player'
				elif shot_result == 0:
					sound_name = 'armor_ricochet_by_player'
				else:
					sound_name = 'armor_not_pierced_by_player'
				notifications = getattr(player, 'soundNotifications', None)
				if notifications is not None:
					notifications.play(sound_name)
			except Exception:
				pass
			if damage > 0 and target_mock is not None:
				try:
					from gui import WindowsManager
					battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
					markers = getattr(battle, 'vMarkersManager', None) if battle is not None else None
					marker = getattr(target_mock, 'marker', None)
					if markers is not None and marker not in (None, -1):
						markers.showVehicleDamageInfo(marker, damage, 0, 0, 1)
				except Exception:
					pass
		return True
	except Exception:
		LOG_CURRENT_EXCEPTION()
		return False


def _offh_prepare_sticker_component(target_mock, component_name):
	"""Create one native damage-sticker attachment for a completed bot model."""
	if target_mock is None or getattr(target_mock, 'typeDescriptor', None) is None:
		return False
	sticker_map = getattr(target_mock, '_sticker_map', None)
	if not isinstance(sticker_map, dict):
		sticker_map = {}
		target_mock._sticker_map = sticker_map
	if component_name in sticker_map:
		return True
	try:
		if component_name == 'hull':
			component_model = getattr(target_mock, '_hull_model', None)
			chassis_model = getattr(target_mock, '_chassis_model', None)
			component_node = chassis_model.node('V') if chassis_model is not None else None
		elif component_name == 'turret':
			component_model = getattr(target_mock, '_turret_model', None)
			component_node = getattr(target_mock, '_t_node', None)
		elif component_name == 'gun':
			component_model = getattr(target_mock, '_gun_model', None)
			component_node = getattr(target_mock, '_g_node', None)
		else:
			return False
		if component_model is None or component_node is None:
			return False
		import VehicleStickers
		stickers = VehicleStickers.VehicleStickers(
			target_mock.typeDescriptor, [], component_name == 'hull', None)
		stickers.attachStickers(component_model, component_node, False)
		sticker_map[component_name] = (stickers, component_model, component_node)
		return True
	except Exception as error:
		LOG_DEBUG('Bot sticker component setup error:', component_name, str(error))
		return False


def _offh_queue_sticker_warmup(player, target_mock):
	"""Spread native sticker creation across loading/countdown frames."""
	if player is None or target_mock is None:
		return False
	if getattr(target_mock, '_sticker_warmup_queued', False):
		return True
	target_mock._sticker_warmup_queued = True
	queue = getattr(player, '_offh_sticker_warmup_queue', None)
	if queue is None:
		queue = []
		player._offh_sticker_warmup_queue = queue
	for component_name in ('hull', 'turret', 'gun'):
		queue.append((target_mock, component_name))
	if getattr(player, '_offh_sticker_warmup_active', False):
		return True
	player._offh_sticker_warmup_active = True

	def _drain_one():
		items = getattr(player, '_offh_sticker_warmup_queue', None) or []
		if items:
			target, component = items.pop(0)
			_offh_prepare_sticker_component(target, component)
			if not any(item[0] is target for item in items):
				prepared = getattr(target, '_sticker_map', None) or {}
				target._sticker_setup_done = all(
					name in prepared for name in ('hull', 'turret', 'gun'))
		if items:
			# One native object per callback avoids the old multi-second main-thread
			# cliff while still finishing well before a normal countdown expires.
			_offh_battle_callback(0.03, _drain_one)
			return
		player._offh_sticker_warmup_active = False

	_offh_battle_callback(0.0, _drain_one)
	return True


def _target_sticker_map(target_mock, component_name=None):
	"""Resolve the per-component VehicleStickers map for ANY hit target,
	uniformly for bots and the player, so decals work the same everywhere."""
	m = getattr(target_mock, '_sticker_map', None)
	if m and (component_name is None or component_name in m):
		return m
	# Normal battles create these incrementally during loading/countdown. Keep a
	# one-component fallback for manually spawned bots or an unusually slow load.
	if (target_mock is not None and
			not getattr(target_mock, '_sticker_setup_done', False) and
			getattr(target_mock, 'typeDescriptor', None) is not None):
		components = (component_name,) if component_name else ('hull', 'turret', 'gun')
		for fallback_component in components:
			_offh_prepare_sticker_component(target_mock, fallback_component)
		m = getattr(target_mock, '_sticker_map', None)
		if m:
			return m
	# The player's own tank keeps its sticker map on the player object.
	try:
		import BigWorld
		p = BigWorld.player()
		if p is not None and getattr(p, 'playerVehicleID', -2) == getattr(target_mock, 'id', -1):
			return getattr(p, '_offhangar_sticker_map', None)
	except Exception:
		pass
	return None


def _add_impact_decal(sticker_map, comp_name, world_hit_pos, world_dir, shot_result=2):
	"""Add a persistent shell-hole decal to a mock tank at the hit point.
	sticker_map maps component name -> (VehicleStickers, componentModel).
	The sticker projects along the shot segment onto the component surface,
	so the segment must be expressed in that model's LOCAL space."""
	try:
		import Math, math
		if not sticker_map:
			return
		entry = sticker_map.get(comp_name) or sticker_map.get('hull')
		if not entry:
			return
		stickers = entry[0]
		model = entry[1]
		node = entry[2] if len(entry) > 2 else None
		if stickers is None or model is None:
			return
		descr = _pick_damage_sticker(shot_result)
		if descr is None:
			return
		# Component models are attached to nodes and report a local (identity)
		# matrix; the node carries the real world transform. Use it to map the
		# world hit point into the model's local space the decal expects.
		w2m = Math.Matrix(node) if node is not None else Math.Matrix(model.matrix)
		w2m.invert()
		d = Math.Vector3(world_dir)
		try:
			d.normalise()
		except Exception:
			d = Math.Vector3(0.0, -1.0, 0.0)
		# segStart just outside the surface, segEnd just inside, so the
		# projector places the decal on the outer skin at the hit point.
		local_start = w2m.applyPoint(world_hit_pos - d.scale(0.4))
		local_end = w2m.applyPoint(world_hit_pos + d.scale(0.4))
		import random
		ang = random.random() * math.pi * 2.0
		up = Math.Vector3(math.sin(ang), math.cos(ang), 0.0)
		sz = descr.get('modelSizes', (0.3, 0.3))
		sizes = Math.Vector2(sz[0], sz[1])
		stickers.addDamageSticker(descr['texName'], descr.get('bumpTexName', ''), local_start, local_end, sizes, up)
	except Exception:
		LOG_CURRENT_EXCEPTION()

from gui.mods.offhangar.logging import LOG_DEBUG, LOG_ERROR, LOG_NOTE
from gui.mods.offhangar.offline_battle_stack import build_offline_battle_context

# NOTE level is intentional: this must be present before entering a battle so
# mixed/stale client folders can be identified from any python.log.
LOG_NOTE('OfflineBattle BUILD %s' % _OFFH_BUILD)

_BATTLE_BOOT_DEBOUNCE_SEC = 1.5
OFFLINE_BATTLE_ENABLED = True



def _offh_arena_type_matches(arena_type, map_name, gameplay_name):
	"""Require both geometry and gameplay; one geometry can own several modes."""
	if arena_type is None:
		return False
	try:
		geometry = str(getattr(arena_type, 'geometryName', '') or '')
		wanted = str(map_name or '')
		geometry = geometry.replace('\\', '/').split('/')[-1]
		wanted = wanted.replace('\\', '/').split('/')[-1]
		geometry_matches = (
			geometry == wanted or
			(bool(geometry) and wanted.endswith('_' + geometry)) or
			(bool(wanted) and geometry.endswith('_' + wanted)))
		return (geometry_matches and
		        str(getattr(arena_type, 'gameplayName', '') or '') ==
		        str(gameplay_name or ''))
	except Exception:
		return False


def _offh_arena_cache_get(cache, key):
	for getter in (
		lambda: cache.get(key),
		lambda: cache[key],
		lambda: cache.getArenaType(key) if hasattr(cache, 'getArenaType') else None,
		lambda: cache.getByID(key) if hasattr(cache, 'getByID') else None,
		lambda: cache.getById(key) if hasattr(cache, 'getById') else None,
	):
		try:
			arena_type = getter()
			if arena_type is not None:
				return arena_type
		except Exception:
			continue
	return None


def _offh_arena_cache_values(cache):
	try:
		if hasattr(cache, 'itervalues'):
			return list(cache.itervalues())
		if hasattr(cache, 'values'):
			return list(cache.values())
	except Exception:
		pass
	return []


def _resolve_real_arena_type(map_id, map_name, gameplay_name):
	"""Resolve the exact geometry/gameplay pair from the retail ArenaType cache."""
	try:
		try:
			import ArenaType as ArenaTypeModule
		except ImportError:
			# 0.8.2 ships it as ``common/arenatype.pyc``.
			try:
				from common import arenatype as ArenaTypeModule
			except ImportError:
				import arenatype as ArenaTypeModule
		cache = getattr(ArenaTypeModule, 'g_cache', None)
		# A fresh client can expose the empty cache before the account flow calls init.
		if not cache:
			init_fn = getattr(ArenaTypeModule, 'init', None)
			if callable(init_fn):
				init_fn()
				cache = getattr(ArenaTypeModule, 'g_cache', None)
		if cache is None:
			LOG_DEBUG('OfflineBattle.arenaType.cacheMissing', map_name,
			          'module', getattr(ArenaTypeModule, '__name__', '?'))
			return None

		gameplay_id = int(ArenaTypeModule.getGameplayIDForName(gameplay_name))
		# Retail 0.8.2 keys g_cache by (gameplayID << 16) | geometryID.
		arena_type_id = (gameplay_id << 16) | (int(map_id) & 0xffff)
		arena_type = _offh_arena_cache_get(cache, arena_type_id)
		if _offh_arena_type_matches(arena_type, map_name, gameplay_name):
			return arena_type

		# Keep compatibility with cache wrappers, but never accept the first mode
		# that merely shares this geometry. Mutating that object into "ctf" leaves
		# its assault/encounter bases and control point intact on the minimap.
		for fn_name in ('getArenaType', 'getByGeometryName', 'getByName',
		                'getArenaTypeByName'):
			fn = getattr(ArenaTypeModule, fn_name, None)
			if not callable(fn):
				continue
			for key in (arena_type_id, map_name, map_id):
				try:
					arena_type = fn(key)
				except Exception:
					continue
				if _offh_arena_type_matches(
						arena_type, map_name, gameplay_name):
					return arena_type

		for arena_type in _offh_arena_cache_values(cache):
			if _offh_arena_type_matches(
					arena_type, map_name, gameplay_name):
				return arena_type

		# Diagnostics: log cache shape so we can implement the correct lookup for 0.8.2.
		try:
			cache_type = type(cache).__name__
			attrs = [a for a in dir(cache) if 'get' in a.lower() or 'arena' in a.lower() or 'type' in a.lower()]
			if isinstance(cache, dict):
				keys = cache.keys()
				key_types = {}
				for kk in keys[:50]:
					kt = type(kk).__name__
					key_types[kt] = key_types.get(kt, 0) + 1
				# also sample a few geometry names to confirm value shape
				sample_geom = []
				for vv in cache.values()[:10]:
					try:
						g = getattr(vv, 'geometryName', None)
						if g:
							sample_geom.append(g)
					except Exception:
						continue
				LOG_DEBUG(
					'OfflineBattle.arenaType.cacheNoHit',
					map_name, 'mapID', map_id,
					'cacheType', cache_type,
					'keyTypes', key_types,
					'sampleGeom', sample_geom[:5],
					'attrs', attrs[:20]
				)
			else:
				LOG_DEBUG('OfflineBattle.arenaType.cacheNoHit', map_name, 'mapID', map_id, 'cacheType', cache_type, 'attrs', attrs[:25])
		except Exception:
			LOG_CURRENT_EXCEPTION()
	except Exception:
		LOG_CURRENT_EXCEPTION()
	return None


def _offh_apply_space_visibility_mask(space_id, arena_type, gameplay_name):
	"""Select the map UDO visibility bit normally supplied by the server."""
	try:
		import BigWorld
		try:
			import ArenaType as ArenaTypeModule
		except ImportError:
			try:
				from common import arenatype as ArenaTypeModule
			except ImportError:
				import arenatype as ArenaTypeModule
		setter = getattr(BigWorld, 'wg_setSpaceItemsVisibilityMask', None)
		if not callable(setter):
			raise RuntimeError('wg_setSpaceItemsVisibilityMask is unavailable')
		gameplay_id = getattr(arena_type, 'gameplayID', None)
		if gameplay_id is None:
			gameplay_id = ArenaTypeModule.getGameplayIDForName(gameplay_name)
		gameplay_id = int(gameplay_id)
		visibility_mask = int(ArenaTypeModule.getVisibilityMask(gameplay_id))
		setter(int(space_id), visibility_mask)
		LOG_NOTE('LAN space visibility gameplay=%s id=%d mask=0x%x' % (
			gameplay_name, gameplay_id, visibility_mask))
		return True
	except Exception as error:
		LOG_ERROR('LAN space visibility setup failed: %s' % str(error))
		return False


def _queue_type_randoms():
	try:
		from constants import QUEUE_TYPE
		return QUEUE_TYPE.RANDOMS
	except Exception:
		# Very old builds: keep a sane default; onEnqueued may still accept an int.
		return 1


def _resolve_vehicle_inv_id(player, int1):
	if int1:
		return int1
	try:
		from CurrentVehicle import g_currentVehicle
		if g_currentVehicle is not None:
			item = getattr(g_currentVehicle, 'item', None)
			if item is not None:
				vid = getattr(item, 'invID', None)
				if vid:
					return vid
	except ImportError:
		pass
	except Exception:
		LOG_CURRENT_EXCEPTION()
	inv = getattr(player, 'inventory', None)
	if inv is None:
		return 0
	for methodName in (
		'getCurrVehicleInvID',
		'getCurrentVehInvID',
		'getVehicleInvID',
		'getCurrentInvID',
	):
		fn = getattr(inv, methodName, None)
		if callable(fn):
			try:
				v = fn()
				if v:
					return v
			except Exception:
				LOG_CURRENT_EXCEPTION()
	for methodName in ('getCurrentVehicle', 'getCurrVehicle'):
		fn = getattr(inv, methodName, None)
		if callable(fn):
			try:
				veh = fn()
				if veh is not None:
					vid = getattr(veh, 'invID', None)
					if vid:
						return vid
			except Exception:
				LOG_CURRENT_EXCEPTION()
	return 0


def _enable_offline_battle_transition(player):
	# Hangar hardening hooks in mod_offhangar must relax while loading an arena.
	player._offhangar_allow_world_clear = True
	# Allow become-non-player only after avatar spawn attempt.
	player._offline_allow_become_non_player = False


def _try_spawn_battle_avatar_stub(player, cmdName):
	import BigWorld
	if player is None or not getattr(player, 'isOffline', False):
		return
	if not _offh_sweep_or_retry('start', lambda _cmd=cmdName:
			_try_spawn_battle_avatar_stub(BigWorld.player(), _cmd)):
		return
	try:
		# One battle = one generation. Establish it before any asynchronous model,
		# camera or spawn callback is scheduled so every callback can reject a
		# completed battle instead of recreating visuals in the hangar.
		globals()['g_offh_battle_gen'] = (
			(globals().get('g_offh_battle_gen', 0) or 0) + 1)
		_offh_my_gen = [globals()['g_offh_battle_gen']]
		# Freeze the movement contract for this battle. Later config/import or LAN
		# role failures may stop native simulation, but may never enable Python.
		_offh_native_mode_enabled()
		_offh_capture_player_battle_attrs(player)
		# The sweep destroys the projectile mover on battle exit (it owns
		# the shell models) - recreate it per battle or tracers are gone
		# from the second battle on. The __calcTrajectory patch lives on
		# the class, so a fresh instance keeps it.
		try:
			if globals().get('g_projectile_mover') is None:
				from projectilemover import ProjectileMover as _OffhPM
				globals()['g_projectile_mover'] = _OffhPM()
		except:
			pass
		# Map name from the (matchmaker-rolled) arena.
		map_name = player.arena.arenaType.geometryName
		if not map_name.startswith('spaces/'):
			map_name = 'spaces/' + map_name

		try:
			from _constants import CONFIG_OPTIONS as _CFG_SP
			_full_release = bool(_CFG_SP.get('full_space_release', False))
			_reuse_space = bool(_CFG_SP.get('reuse_map_space', True))
		except Exception:
			_full_release = False
			_reuse_space = True
		globals()['g_offh_full_release'] = _full_release

		if _full_release:
			# Dedicated FRESH space per battle. Rendering follows camera.spaceID
			# (the diagnostic proved it; the hangar renders its own space that
			# way), so we point the render camera at this space. The PREVIOUS
			# battle's space is releaseSpace'd HERE (start = stable context, it's
			# fully orphaned) rather than in the exit sweep, which crashed on the
			# hangar load -> map RAM truly returned -> variety WITHOUT fragmentation.
			_prev = globals().get('g_offh_pending_release', 0) or 0
			if _prev:
				# That space still has its geometry MAPPED at this point: the mapped_*
				# globals are only overwritten after the new space exists, two lines
				# below. Releasing a space whose mapping is still registered is what
				# leaves the engine reading freed chunk memory one call later. Unmap
				# first, then drop the globals so no stale handle can survive into a
				# REUSED space id - the engine hands the freed id straight back out.
				try:
					_ph = globals().get('g_offh_mapped_handle')
					if _ph is not None and (globals().get('g_offh_mapped_space', 0) or 0) == _prev:
						BigWorld.delSpaceGeometryMapping(_prev, _ph)
						globals()['g_offh_mapped_handle'] = None
						globals()['g_offh_mapped_space'] = 0
						globals()['g_offh_mapped_name'] = None
						LOG_DEBUG('OfflineBattle.unmapped prev space %s' % _prev)
				except Exception, _e_um:
					LOG_DEBUG('OfflineBattle.unmap prev FAILED %s' % _e_um)
				try:
					if hasattr(BigWorld, 'releaseSpace'):
						BigWorld.releaseSpace(_prev)
						globals()['g_offh_pending_release'] = 0
						LOG_DEBUG('OfflineBattle.released prev space %s' % _prev)
					else:
						LOG_DEBUG('OfflineBattle.release prev unavailable space=%s' % _prev)
				except Exception, _e_release:
					# Keep the id pending so a later battle can retry and the sweep
					# summary cannot falsely report that this native space is gone.
					LOG_DEBUG('OfflineBattle.release prev FAILED space=%s error=%s' %
						(_prev, _e_release))
				try:
					import gc as _gcp
					_gcp.collect(); _gcp.collect()
				except Exception:
					pass
			# The log ends right here on the second battle - 'released prev space'
			# prints, 'dedicated space' never does - so the crash is in one of the
			# next three calls, not in the release. One line each to name which.
			space_id = BigWorld.createSpace()
			LOG_DEBUG('OfflineBattle.createSpace -> %s (prev %s, reusedID=%s)' % (space_id, _prev, space_id == _prev))
			_offh_mh = BigWorld.addSpaceGeometryMapping(space_id, None, map_name)
			LOG_DEBUG('OfflineBattle.addSpaceGeometryMapping ok')
			globals()['g_offh_mapped_handle'] = _offh_mh
			globals()['g_offh_mapped_space'] = space_id
			globals()['g_offh_mapped_name'] = map_name
			_offh_set_render_space(space_id)
			LOG_DEBUG('OfflineBattle.dedicated space', space_id, 'camera render ->', space_id, map_name)
		else:
			space_id = getattr(player, 'spaceID', 0)
			if space_id == 0:
				space_id = BigWorld.createSpace()
			# ANTI-FRAGMENTATION: reuse the already-mapped space when the map is
			# UNCHANGED (reuse_map_space); else unmap old + map new.
			_mapped_name = globals().get('g_offh_mapped_name')
			_mapped_space = globals().get('g_offh_mapped_space', 0) or 0
			_mapped_handle = globals().get('g_offh_mapped_handle')
			if _reuse_space and _mapped_handle is not None and _mapped_space == space_id and _mapped_name == map_name:
				_offh_mh = _mapped_handle
				LOG_DEBUG('OfflineBattle.mappedGeometry REUSED (no realloc)', map_name, 'space', space_id)
			else:
				if _mapped_handle is not None and _mapped_space:
					try: BigWorld.delSpaceGeometryMapping(_mapped_space, _mapped_handle)
					except Exception: pass
				try: BigWorld.clearSpace(space_id)
				except Exception: pass
				_offh_mh = BigWorld.addSpaceGeometryMapping(space_id, None, map_name)
				globals()['g_offh_mapped_handle'] = _offh_mh
				globals()['g_offh_mapped_space'] = space_id
				globals()['g_offh_mapped_name'] = map_name
				LOG_DEBUG('OfflineBattle.mappedGeometry', map_name, 'space', space_id)
		# Chunk UDOs carry one visibility bit per gameplay mode. The retail server
		# selects the active bit immediately after mapping the arena. Without it,
		# Malinovka (and every multi-mode map) renders CTF, assault and encounter
		# flags/circles together even when the battle itself is standard CTF.
		_offh_apply_space_visibility_mask(
			space_id, getattr(getattr(player, 'arena', None), 'arenaType', None),
			'ctf')
		globals()['g_offh_battle_space'] = space_id
		globals()['g_offh_battle_mapping'] = _offh_mh
		globals()['g_offh_battle_mapname'] = map_name
		# The spaceID is reused between battles, so the per-space reset
		# heuristics in the destructibles ledger / tree registry never fire
		# on their own: reset explicitly or the dedup sets grow forever and
		# suppress destruction of fresh objects in later battles.
		try:
			_get_destr_authority().reset()
		except Exception:
			pass
		for _k in ('g_offh_tree_state', 'g_offh_destr_ordered', 'g_offh_destr_chunks', 'g_offh_destr_seen', 'g_offh_ram_cd'):
			globals().pop(_k, None)
		# Start the destructibles manager BEFORE the chunks stream in, so it
		# receives onChunkLoad for every chunk (fences/houses become breakable)
		try:
			import AreaDestructibles
			if getattr(AreaDestructibles, 'g_destructiblesManager', None) is not None:
				AreaDestructibles.g_destructiblesManager.startSpace(space_id)
				LOG_DEBUG('OfflineBattle: destructibles manager started for space', space_id)
		except Exception:
			LOG_CURRENT_EXCEPTION()
	except Exception:
		LOG_CURRENT_EXCEPTION()

	try:
		LOG_DEBUG('OfflineBattle.starting camera manually in space', space_id)
		import AvatarInputHandler
		import Math, ResMgr
		global g_offline_aih

		# Determine spawn position from arena XML
		spawn_pos = Math.Vector3(0, 100.0, 0)
		spawn_dir = Math.Vector3(0, 0, 3.1415926535)
		try:
			at = player.arena.arenaType
			if True:
				xml_path = 'scripts/arena_defs/%s.xml' % at.geometryName.split('/')[-1]
				section = ResMgr.openSection(xml_path)
				LOG_DEBUG('OfflineBattle.XML_LOAD:', xml_path, section is not None)
				if section is not None:
					import debug_utils
					debug_utils.LOG_DEBUG('DUMP ARENA DEFS:', section.keys(), section['gameplayTypes/ctf'].keys() if section.has_key('gameplayTypes/ctf') else 'no_ctf')
					if section.has_key('gameplayTypes/ctf'):
						ctf = section['gameplayTypes/ctf']
						for t in ['team1', 'team2']:
							if ctf.has_key('teamSpawnPoints/%s' % t):
								debug_utils.LOG_DEBUG('SPAWN POINTS %s:' % t, ctf['teamSpawnPoints/%s' % t].keys())
								for k, v in ctf['teamSpawnPoints/%s' % t].items():
									debug_utils.LOG_DEBUG(' - ', k, type(v), v.asVector2)
					gp = section['gameplayTypes/ctf']
					if section is not None:
						try:
							with open('C:\\Games\\World_of_Tanks_0.08.02.00.00_EU_0543_SD\\arena_dump_root.txt', 'w') as f_out:
								f_out.write('ROOT keys: ' + str(section.keys()) + '\n')
								for k, v in section.items():
									if k in ['teamSpawnPoints', 'teamBasePositions'] or 'team' in k:
										f_out.write(' - ' + k + ' : ' + str(type(v)) + '\n')
										if hasattr(v, 'keys'):
											f_out.write('    keys: ' + str(v.keys()) + '\n')
						except Exception as e:
							pass
					if gp is not None:
						try:
							with open('C:\\Games\\World_of_Tanks_0.08.02.00.00_EU_0543_SD\\arena_dump_gp.txt', 'w') as f_out:
								f_out.write('ctf keys: ' + str(gp.keys()) + '\n')
								for k, v in gp.items():
									f_out.write(' - ' + k + ' : ' + str(type(v)) + '\n')
									if hasattr(v, 'keys'):
										f_out.write('    keys: ' + str(v.keys()) + '\n')
										for k2, v2 in v.items():
											f_out.write('    - ' + k2 + ' : ' + str(type(v2)) + '\n')
											if hasattr(v2, 'keys'):
												f_out.write('       keys: ' + str(v2.keys()) + '\n')
												for k3, v3 in v2.items():
													f_out.write('       - ' + k3 + ' asVec2:' + str(getattr(v3, 'asVector2', 'none')) + ' asStr:' + str(getattr(v3, 'asString', 'none')) + '\n')
						except Exception as e:
							import debug_utils
							debug_utils.LOG_DEBUG('DUMP ERROR:', e)

						global g_offline_bases
						g_offline_bases = {1: [], 2: []}
						def _add_base(_t, _x, _z, _src):
							'''Accept a base position only if it really is one.

							hasattr(section, 'asVector2') is ALWAYS true on a BigWorld DataSection -
							those accessors exist whatever the node holds, so it is not a type test.
							A child that is not a vector read as (0, 0) and became a base at the MAP
							ORIGIN. Driving through the middle of the map then started a capture with
							no circle anywhere near - the reported symptom. No WoT map places a base
							at the origin, so that value is always the bug.'''
							try:
								_x = float(_x); _z = float(_z)
							except Exception:
								return
							if abs(_x) < 1.0 and abs(_z) < 1.0:
								LOG_DEBUG('BASE REJECTED (origin, not a vector node): team=%s src=%s' % (_t, _src))
								return
							g_offline_bases[_t].append(Math.Vector3(_x, 0.0, _z))
							LOG_DEBUG('BASE team=%s at (%.1f, %.1f) src=%s' % (_t, _x, _z, _src))
						import debug_utils
						try:
							bp_node_all = gp['teamBasePositions']
							if bp_node_all is not None:
								debug_utils.LOG_DEBUG('teamBasePositions EXISTS! keys:', bp_node_all.keys())
								for k, v in bp_node_all.items():
									debug_utils.LOG_DEBUG(' - child:', k, v.keys())
						except Exception as e:
							debug_utils.LOG_DEBUG('teamBasePositions error:', e)


						for t_id in (1, 2):
							bp_node = gp['teamBasePositions/team%d' % t_id]
							if bp_node is not None:
								items = bp_node.items()
								if items:
									for k, v in items:
										import debug_utils
										debug_utils.LOG_DEBUG('Base node child', t_id, k)
										if v is not None and hasattr(v, 'asVector2'):
											_add_base(t_id, v.asVector2.x, v.asVector2.y, 'child:%s' % k)
										elif v is not None and hasattr(v, 'asVector3'):
											_add_base(t_id, v.asVector3.x, v.asVector3.z, 'child3:%s' % k)
								else:
									import gui.mods.offhangar.logging as __offlog
									__offlog.LOG_DEBUG('LOUD: Base node DIRECT', t_id)
									if hasattr(bp_node, 'asVector2'):
										_add_base(t_id, bp_node.asVector2.x, bp_node.asVector2.y, 'direct2')
									elif hasattr(bp_node, 'asVector3'):
										_add_base(t_id, bp_node.asVector3.x, bp_node.asVector3.z, 'direct3')
									elif hasattr(bp_node, 'asString'):
										try:
											parts = bp_node.asString.split()
											_add_base(t_id, parts[0], parts[1], 'string')
										except Exception as e:
											pass
							import gui.mods.offhangar.logging as __offlog
							__offlog.LOG_DEBUG('LOUD: g_offline_bases is now:', g_offline_bases)

						import debug_utils
						debug_utils.LOG_DEBUG('Parsed bases:', g_offline_bases)
						# The packed 0.8.2 arena XML is the primary authority.  If its
						# DataSection shape cannot be decoded on a particular client build,
						# use the shipped tactical coordinates rather than silently disabling
						# capture and formation placement for that team.
						_base_sources = {1: 'arena_xml', 2: 'arena_xml'}
						try:
							from gui.mods.offhangar.bot_ai_maps import get_tactical_map
							_tactical_bases = (get_tactical_map(map_name) or {}).get('bases', {})
							for _base_team in (1, 2):
								if not g_offline_bases.get(_base_team):
									_fallback_base = _tactical_bases.get(_base_team)
									if _fallback_base is not None:
										_add_base(_base_team, _fallback_base[0],
										          _fallback_base[1], 'tactical_fallback')
										_base_sources[_base_team] = 'tactical_fallback'
						except Exception as _base_fallback_error:
							LOG_DEBUG('CTF base fallback failed:', str(_base_fallback_error))
						try:
							from gui.mods.offhangar.logging import LOG_NOTE as _BASE_NOTE
							_base_parts = []
							for _base_team in (1, 2):
								_base_list = g_offline_bases.get(_base_team, []) or []
								if _base_list:
									_base_parts.append('t%d=(%.1f,%.1f):%s' % (
										_base_team, _base_list[0].x, _base_list[0].z,
										_base_sources[_base_team]))
								else:
									_base_parts.append('t%d=MISSING' % _base_team)
							_BASE_NOTE('LAN CTF bases map=%s %s' % (
								map_name, ' '.join(_base_parts)))
						except Exception:
							pass

						# Collect the original spawn points of BOTH teams (for the 15v15 auto-spawn)
						global g_offline_spawns
						g_offline_spawns = {1: [], 2: []}
						for _sp_t in (1, 2):
							try:
								_sp_node = gp['teamSpawnPoints/team%d' % _sp_t]
								if _sp_node is not None:
									for _sp_k, _sp_v in _sp_node.items():
										_sp_v2 = getattr(_sp_v, 'asVector2', None)
										if _sp_v2 is None:
											try:
												_sp_parts = _sp_v.asString.split()
												_sp_v2 = Math.Vector2(float(_sp_parts[0]), float(_sp_parts[1]))
											except Exception:
												_sp_v2 = None
										if _sp_v2 is not None:
											g_offline_spawns[_sp_t].append((float(_sp_v2.x), float(_sp_v2.y)))
							except Exception:
								pass
						LOG_DEBUG('OfflineBattle.spawnPoints (ctf xml):', g_offline_spawns)
						# Only spawn points nested under the selected ctf gameplay type are
						# safe to use. ArenaType.teamSpawnPoints can expose a different mode's
						# points (Himmelsdorf exposes domination here), which put standard-battle
						# vehicles on roofs at the opposite edge. Most stock ctf definitions have
						# no explicit spawn list, so the formation around the ctf base is expected.
						LOG_DEBUG('OfflineBattle.spawnPoints (final):', g_offline_spawns)
						# Map bounds (arena_defs boundingBox: bottomLeft/upperRight as Vector2) - used to
						# reject off-map spawn candidates (the 'spawned left outside the map' bug).
						global g_offline_bounds
						g_offline_bounds = None
						try:
							_bb = getattr(at, 'boundingBox', None)
							if _bb is not None and len(_bb) >= 2:
								g_offline_bounds = (float(_bb[0].x), float(_bb[0].y), float(_bb[1].x), float(_bb[1].y))
								LOG_DEBUG('OfflineBattle.mapBounds:', g_offline_bounds)
						except Exception:
							g_offline_bounds = None

						# The player can be either absolute arena team in LAN mode.  The
						# old hard-coded team1 candidate placed every team2 client at the
						# opposite base until a later correction happened to succeed.
						_player_spawn_team = int(getattr(player, '_offhangar_team', 1) or 1)
						sp = gp['teamSpawnPoints/team%d' % _player_spawn_team]
						bp = gp['teamBasePositions/team%d' % _player_spawn_team]

						# Validate ALL spawn candidates with a roof/ledge check instead of
						# blindly taking the first one. Real spawn points first (like the
						# original game), base flag positions as fallback.
						_found_spawn = False
						import BigWorld

						def _read_vec2(val):
							v2 = getattr(val, 'asVector2', None)
							if v2 is None:
								try:
									parts = val.asString.split()
									v2 = Math.Vector2(float(parts[0]), float(parts[1]))
								except: pass
							return v2

						# Collect candidates: real ArenaType spawn points first (original game order),
						# then the XML hand-parse, then the base flag as the last resort.
						_spawn_cands = []
						for _gx, _gz in ((globals().get('g_offline_spawns', {}) or {}).get(_player_spawn_team, []) or []):
							try: _spawn_cands.append(Math.Vector2(float(_gx), float(_gz)))
							except Exception: pass
						if sp is not None:
							for key, val in sp.items():
								vec2 = _read_vec2(val)
								if vec2 is not None:
									_spawn_cands.append(vec2)
						if bp is not None:
							for key, val in bp.items():
								if 'position' in key or key.isdigit():
									vec2 = _read_vec2(val)
									if vec2 is not None:
										_spawn_cands.append(vec2)
						# The robust parser above already accepted direct/vector child
						# base nodes. Keep those as the final fallback for either team;
						# some packed DataSections expose no iterable children here.
						if not _spawn_cands:
							for _base_candidate in (g_offline_bases.get(_player_spawn_team, []) or []):
								_spawn_cands.append(Math.Vector2(_base_candidate.x, _base_candidate.z))
						LOG_DEBUG('OfflineBattle.spawn candidates:', len(_spawn_cands), map_name)

						def _spawn_ground(x, z):
							# Returns (y, ok): ground height + roof/ledge check
							# (neighbouring ground must exist and be at a similar height)
							y = None
							try:
								hit = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(x, 1000.0, z), Math.Vector3(x, -1000.0, z), 128)
								if hit is not None:
									y = hit[0].y
							except: pass
							if y is None:
								return (None, False)
							for _dx, _dz in ((4.0, 0.0), (-4.0, 0.0), (0.0, 4.0), (0.0, -4.0)):
								try:
									c = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(x + _dx, y + 3.0, z + _dz), Math.Vector3(x + _dx, y - 150.0, z + _dz), 128)
								except:
									continue
								if c is None or abs(c[0].y - y) > 3.0:
									return (y, False)
							return (y, True)

						for vec2 in _spawn_cands:
							_sy, _sok = _spawn_ground(vec2.x, vec2.y)
							if _sy is not None and _sok:
								spawn_pos = Math.Vector3(vec2.x, _sy, vec2.y)
								LOG_DEBUG('OfflineBattle.spawn validated pos:', spawn_pos)
								_found_spawn = True
								break
						if not _found_spawn and _spawn_cands:
							# Fallback WITHOUT the roof check, but still bounds- and ground-checked. The old
							# code took candidate[0] blind and used Y=100.0 when the ground probe found
							# nothing - a probe from y=1000 to y=-1000 only misses when the point is off the
							# map, so that is exactly the 'spawned outside the map, hanging in the air' bug
							# (python.log: 'spawn fallback pos: (-126.887, 100, -305.909)').
							_bnd = globals().get('g_offline_bounds', None)
							_picked = None
							for _cand in _spawn_cands:
								# 60 m of slack: arena_defs sometimes place valid points just outside their own
								# declared boundingBox (Himmelsdorf: box ends at -300, a point sits at -306.4).
								if _bnd is not None and not (_bnd[0] - 60.0 <= _cand.x <= _bnd[2] + 60.0 and _bnd[1] - 60.0 <= _cand.y <= _bnd[3] + 60.0):
									LOG_DEBUG('OfflineBattle.spawn candidate off-map, skipped:', _cand.x, _cand.y)
									continue
								_cy, _cok = _spawn_ground(_cand.x, _cand.y)
								if _cy is not None:
									_picked = (_cand, _cy)
									break
							if _picked is None:
								# Nothing resolved: search outward from the first in-bounds candidate for real
								# ground rather than dropping the hull at a made-up altitude.
								_seed = None
								for _cand in _spawn_cands:
									if _bnd is None or (_bnd[0] - 60.0 <= _cand.x <= _bnd[2] + 60.0 and _bnd[1] - 60.0 <= _cand.y <= _bnd[3] + 60.0):
										_seed = _cand
										break
								if _seed is None: _seed = _spawn_cands[0]
								for _r in (10.0, 25.0, 50.0, 100.0):
									for _ox, _oz in ((_r, 0.0), (-_r, 0.0), (0.0, _r), (0.0, -_r)):
										_tx, _tz = _seed.x + _ox, _seed.y + _oz
										if _bnd is not None and not (_bnd[0] <= _tx <= _bnd[2] and _bnd[1] <= _tz <= _bnd[3]):
											continue
										_cy, _cok = _spawn_ground(_tx, _tz)
										if _cy is not None:
											_picked = (Math.Vector2(_tx, _tz), _cy)
											break
									if _picked is not None: break
							if _picked is not None:
								spawn_pos = Math.Vector3(_picked[0].x, _picked[1], _picked[0].y)
							else:
								vec2 = _spawn_cands[0]
								spawn_pos = Math.Vector3(vec2.x, 100.0, vec2.y)
								LOG_DEBUG('OfflineBattle.spawn WARNING: no ground found for any candidate')
							LOG_DEBUG('OfflineBattle.spawn fallback pos:', spawn_pos)

						# Face the enemy base instead of a fixed 180 degrees
						try:
							import math
							_eb_list = g_offline_bases.get(2 if _player_spawn_team == 1 else 1, [])
							if _eb_list:
								_ddx = _eb_list[0].x - spawn_pos.x
								_ddz = _eb_list[0].z - spawn_pos.z
								if _ddx * _ddx + _ddz * _ddz > 25.0:
									spawn_dir = Math.Vector3(0, 0, math.atan2(_ddx, _ddz))
						except Exception:
							pass

						# Hardcoded spawn hack removed: the candidates above are validated against
						# roofs/ledges, so the real arena_def spawn points are safe to use directly.
						import math
		except Exception as e:
			LOG_DEBUG('OfflineBattle.XML_ERROR:', str(e))

		# Use a MatrixProduct as the live vehicle matrix provider.
		# Math.Matrix is a STATIC snapshot - WGTranslationOnlyMP.source needs a C++ live provider.
		# MatrixProduct(a=identity, b=identity) acts as a live provider and can be .set()-like via its parts.
		veh_matrix_static = Math.Matrix()
		veh_matrix_static.setRotateY(spawn_dir.z)
		veh_matrix_static.translation = spawn_pos
		veh_matrix = Math.MatrixProduct()
		veh_matrix.a = veh_matrix_static
		veh_matrix.b = Math.Matrix()  # identity

		# Chassis matrix: includes yaw + position, driven by Servo
		# so hull/turret/gun chain stays perfectly in sync
		chassis_m = Math.Matrix()
		chassis_m.setRotateY(spawn_dir.z)
		chassis_m.translation = spawn_pos
		chassis_mp = Math.MatrixProduct()
		chassis_mp.a = chassis_m
		chassis_mp.b = Math.Matrix()  # identity

		class _MockFilter(object): pass
		mf = _MockFilter()
		mf.position = Math.Vector3(spawn_pos)
		mf.yaw = spawn_dir.z
		mf.pitch = 0.0
		mf.matrix = veh_matrix

		class _Appearance(object):
			def changeVisibility(self, part, visible, lod=True): pass
			def showStickers(self, visible): pass
			def isUnderwater(self): return False
			def __getattr__(self, name):
				if 'turretMatrix' in name:
					return turret_matrix_local
				if 'gunMatrix' in name:
					if self.compoundModel is not None:
						try:
							return self.compoundModel.node('HP_gunJoint')
						except Exception:
							pass
					return turret_matrix
				if 'hullMatrix' in name:
					if self.compoundModel is not None:
						try:
							return self.compoundModel.node('V')
						except Exception:
							pass
					return turret_matrix
				if 'Matrix' in name or 'Prov' in name:
					if self.compoundModel is not None:
						return self.compoundModel.matrix
					return turret_matrix
				if 'Bounds' in name:
					import Math
					return (Math.Vector3(-1,-1,-1), Math.Vector3(1,1,1))
				if name.startswith(('is','on','set','get','update','show','hide','add','remove','play','stop','start')) or name == 'refresh':
					return lambda *a, **k: None
				import Math
				return Math.Matrix()

		ma = _Appearance()

		td = None
		try:
			if hasattr(player, '_offhangar_battle_ctx'):
				ctx = player._offhangar_battle_ctx
				vdict = ctx.get('vehicles', {})
				vid = player.playerVehicleID
				vinfo = vdict.get(vid)
				if not vinfo and vdict:
					vinfo = list(vdict.values())[0]
				if vinfo:
					td = vinfo.get('vehicleType')

			from items import vehicles
			if type(td) is int:
				nationID = (td >> 4) & 15
				vehicleID = td >> 8
				td = vehicles.VehicleDescr(typeID=(nationID, vehicleID))
				LOG_DEBUG('PHYSICS_DUMP:', td.physics)
			elif td is None:
				td = vehicles.VehicleDescr(typeName='ussr:MS-1')
			elif type(td).__name__ == 'FakeDesc':
				# If offline_battle_stack gave us FakeDesc, fallback to MS-1 so we don't crash
				td = vehicles.VehicleDescr(typeName='ussr:MS-1')
		except Exception as e:
			LOG_DEBUG('OfflineBattle.td error', str(e))

		LOG_DEBUG('OfflineBattle.td resolved:', td, type(td).__name__ if td else None)
		if td is not None:
			LOG_DEBUG('OfflineBattle.td types:', type(td.chassis), type(td.hull), type(td.turret))
			if hasattr(td.chassis, 'keys'):
				LOG_DEBUG('OfflineBattle.td keys:', td.chassis.keys())

		# Inject into player so the GUI finds it!
		player.vehicleTypeDescriptor = td
		# Publish the battle descriptor via _offhangar_td so the account's
		# vehicleTypeDescriptor getattribute override returns THIS tank (not a
		# fresh tier-1 MS-1); the native penetration marker, tracer ballistics
		# and maxHealth then read the real tank being driven. Cleared in the
		# sweep's 'muzzle' stage so the hangar falls back to its stub.
		player._offhangar_td = td

		loaded_models = {'chassis': None, 'hull': None, 'turret': None, 'gun': None, 'td': td}
		loaded_models['chassis_mp'] = chassis_mp
		if td is not None:
			for part_name in ('chassis', 'hull', 'turret', 'gun'):
				try:
					part_desc = getattr(td, part_name, None)
					if part_desc is not None and 'models' in part_desc and 'undamaged' in part_desc['models']:
						modelName = part_desc['models']['undamaged']
						m = BigWorld.Model(modelName)
						loaded_models[part_name] = m
						LOG_DEBUG('OfflineBattle.model loaded:', part_name, modelName)
				except Exception as e:
					LOG_DEBUG('OfflineBattle load model error:', part_name, str(e))

			# BigWorld.Model() is async - the model isn't ready immediately.
			# Use a callback to add them after they've loaded.
			_models_to_add = dict((k, v) for k, v in loaded_models.items() if v is not None)
			_add_attempts = [0]


			def _add_models_when_ready(_model_gen=_offh_my_gen[0]):
				if globals().get('g_offh_battle_gen', 0) != _model_gen:
					return
				_add_attempts[0] += 1
				try:
					chassis = _models_to_add.get('chassis')
					hull    = _models_to_add.get('hull')
					turret  = _models_to_add.get('turret')
					gun     = _models_to_add.get('gun')

					if chassis is not None:
						chassis.position = Math.Vector3(spawn_pos)
						chassis.yaw = 0.0
						_add_model(chassis)
						try:
							chassis.addMotor(BigWorld.Servo(chassis_mp))
							LOG_DEBUG('OfflineBattle.chassis Servo attached')
						except Exception as e:
							LOG_DEBUG('OfflineBattle.chassis Servo error:', str(e))

						if hull is not None:
							try:
								chassis.node('V').attach(hull)
								LOG_DEBUG('OfflineBattle: hull attached to chassis.V')
							except Exception as e:
								LOG_DEBUG('OfflineBattle.attach hull error:', str(e))
								hull.position = Math.Vector3(spawn_pos)
								_add_model(hull)

							# Attach turret to hull node 'HP_turretJoint'
							if turret is not None:
								try:
									turret_mat = Math.Matrix()
									turret_mat.setIdentity()
									loaded_models['turret_mat'] = turret_mat
									hull.node('HP_turretJoint', turret_mat).attach(turret)
									LOG_DEBUG('OfflineBattle: turret attached to hull.HP_turretJoint')
								except Exception as e:
									LOG_DEBUG('OfflineBattle.attach turret error:', str(e))

								# Apply Camouflage and Emblems
								try:
									import items.vehicles as iv
									cust = iv.g_cache.customization(td.type.id[0])
									camo_kind = getattr(player.arena.arenaType, 'vehicleCamouflageKind', 0) if hasattr(player, 'arena') and hasattr(player.arena, 'arenaType') else 0
									camo_params = td.camouflages[camo_kind] if hasattr(td, 'camouflages') and len(td.camouflages) > camo_kind else None
									LOG_DEBUG('OfflineBattle.customization:', 'kind', camo_kind, 'params', camo_params)
									# Offline QoL: if the map-season slot is empty, fall back to any season
									# the player has painted - otherwise the bought camo never shows here.
									if (camo_params is None or camo_params[0] is None) and hasattr(td, 'camouflages'):
										for _ck in range(len(td.camouflages)):
											if td.camouflages[_ck] is not None and td.camouflages[_ck][0] is not None:
												camo_params = td.camouflages[_ck]
												LOG_DEBUG('OfflineBattle.customization: season fallback ->', _ck, camo_params)
												break
									if camo_params is not None and camo_params[0] is not None:
										camo = cust['camouflages'].get(camo_params[0]) if cust else None
										if camo is not None:
											tex = camo['texture']
											colors = camo['colors']
											defaultTiling = camo['tiling'].get(td.type.compactDescr)
											weights = Math.Vector4((colors[0]>>24)/255.0, (colors[1]>>24)/255.0, (colors[2]>>24)/255.0, (colors[3]>>24)/255.0)
											for p_name, p_mdl in [('chassis', chassis), ('hull', hull), ('turret', turret), ('gun', gun)]:
												if p_mdl is not None:
													excl = td.type.camouflageExclusionMask
													tiling = defaultTiling
													if tiling is None: tiling = td.type.camouflageTiling
													p_desc = getattr(td, p_name, None)
													if p_desc is not None:
														coeff = p_desc.get('camouflageTiling')
														if coeff is not None and tiling is not None:
															tiling = (tiling[0]*coeff[0], tiling[1]*coeff[1], tiling[2]*coeff[2], tiling[3]*coeff[3])
														if 'camouflageExclusionMask' in p_desc:
															excl = p_desc['camouflageExclusionMask']
													if excl != '' and tex != '':
														if p_name == 'chassis':
															# Chassis camo must go THROUGH the track fashion (wg_fashion), like the
															# original __updateCamouflage: a second WGBaseFashion on the chassis
															# detaches the scrolling track material (wheels spin, tracks freeze).
															loaded_models['_camo_args'] = (tex, excl, tiling, colors[0], colors[1], colors[2], colors[3], weights)
														else:
															fashion = getattr(p_mdl, 'wg_baseFashion', None)
															if fashion is None: fashion = p_mdl.wg_baseFashion = BigWorld.WGBaseFashion()
															fashion.setCamouflage(tex, excl, tiling, colors[0], colors[1], colors[2], colors[3], weights)

								except Exception as e:
									import traceback
									LOG_DEBUG('OfflineBattle.customization error:', str(e), traceback.format_exc())

								# Attach gun to turret node 'HP_gunJoint'
								if gun is not None:
									try:
										gun_mat = Math.Matrix()
										gun_mat.setIdentity()
										loaded_models['gun_mat'] = gun_mat
										turret.node('HP_gunJoint', gun_mat).attach(gun)
										LOG_DEBUG('OfflineBattle: gun attached to turret.HP_gunJoint')
										# Barrel recoil animation on the player's own gun
										player._offhangar_gun_recoil = _setup_gun_recoil(gun, td)
										# Hull rocking (shot impulse / pitch-roll swinging) on the chassis
										player._offhangar_swinging = _setup_swinging(chassis, td)
									except Exception as e:
										LOG_DEBUG('OfflineBattle.attach gun error:', str(e))

								try:
									import VehicleStickers
									_nodes = loaded_models['sticker_nodes'] = {
										'hull': chassis.node('V') if chassis else hull.node(''),
										'turret': hull.node('HP_turretJoint', turret_mat) if hull else turret.node(''),
										'gun': turret.node('HP_gunJoint', gun_mat) if turret else gun.node('')
									}
									_emblemPositions = (
										('hull', hull, td.hull['emblemSlots']),
										('gun' if td.turret['showEmblemsOnGun'] else 'turret', gun if td.turret['showEmblemsOnGun'] else turret, td.turret['emblemSlots']),
										('turret' if td.turret['showEmblemsOnGun'] else 'gun', turret if td.turret['showEmblemsOnGun'] else gun, [])
									)
									if not hasattr(player, '_offhangar_stickers'): player._offhangar_stickers = []
									if not hasattr(player, '_offhangar_sticker_map'): player._offhangar_sticker_map = {}
									for cName, p_mdl, slots in _emblemPositions:
										if p_mdl is not None:
											stickers = VehicleStickers.VehicleStickers(td, slots, cName == 'hull', None)
											p_node = _nodes.get(cName)
											if p_node is not None:
												stickers.attachStickers(p_mdl, p_node, False)
												player._offhangar_stickers.append(stickers)
												# Map by component so shell-hole decals can target the hit
												# part. Store the NODE too: attached component models report
												# a local (identity) matrix, so the node gives the world
												# transform needed to place the decal correctly.
												player._offhangar_sticker_map[cName] = (stickers, p_mdl, p_node)
								except Exception as e:
									import traceback
									LOG_DEBUG('OfflineBattle.stickers error:', str(e), traceback.format_exc())
					elif hull is not None:
						hull.position = Math.Vector3(spawn_pos)
						_add_model(hull)
						LOG_DEBUG('OfflineBattle.addModel OK: hull (no chassis)')

					root_model = chassis or hull
					ma.models = [root_model]
					ma.compoundModel = root_model
					LOG_DEBUG('OfflineBattle.compoundModel set, attempt:', _add_attempts[0])


					# Engine sounds are now initialized in _step_offline_physics

				except Exception as e:
					import traceback
					LOG_DEBUG('OfflineBattle._add_models_when_ready ERROR:', traceback.format_exc())
					if _add_attempts[0] < 10:
						_offh_battle_callback(0.3, _add_models_when_ready)

			_offh_battle_callback(0.2, _add_models_when_ready)

			# Set temporary compoundModel so camera logic doesn't fail
			root_model = loaded_models['chassis'] if loaded_models['chassis'] is not None else loaded_models['hull']
			ma.models = [root_model]
			ma.compoundModel = root_model

		try:
			_offh_load_hit_testers(td)
		except Exception as e:
			LOG_DEBUG("Error loading hitTesters for player:", str(e))

		class _MockVeh(object):
			def __init__(self):
				self.damage_from_player = 0
				self.damage_from_bots = 0
				self.hits_from_player = 0
				self.matrix = Math.Matrix()
				self.matrix.setRotateY(spawn_dir.z)
				self.matrix.translation = spawn_pos
				self.position = Math.Vector3(spawn_pos)
				self.yaw = spawn_dir.z
				self.pitch = 0.0
				self.roll = 0.0
				self.filter = mf
				self.appearance = ma
				self.isPlayer = True
				self.typeDescriptor = td
				self.health = getattr(td, 'maxHealth', 400)
				self.maxHealth = getattr(td, 'maxHealth', 400)
				self.isStarted = True
				# Callable AND truthy - see _OffhAliveState.
				self.isAlive = _OffhAliveState(True)
				self.id = getattr(player, 'playerVehicleID', 0)
				self.model = getattr(self.appearance, 'compoundModel', None)

				class _ModelsDesc(object):
					def __getitem__(self, key):
						if key in loaded_models and loaded_models.get(key) is not None:
							return {'model': loaded_models[key]}
						# Return None model so SniperCamera falls through
						# to the MatrixProduct branch (which uses getOwnVehicleMatrix)
						return {'model': None}
				self.appearance.modelsDesc = _ModelsDesc()
			def getAutorotation(self): return False
			def __getattr__(self, name): return None

			def getComponents(self):
				import Math
				res = []
				m = Math.Matrix()
				m.setIdentity()
				res.append((self.typeDescriptor.chassis, m))

				hullOffset = self.typeDescriptor.chassis['hullPosition']
				m = Math.Matrix()
				m.setTranslate(-hullOffset)
				res.append((self.typeDescriptor.hull, m))

				if getattr(self, 'isPlayer', False):
					tYaw = turret_matrix_local.yaw
					gPitch = gun_matrix.pitch if 'gun_matrix' in globals() else 0.0
				else:
					tYaw = getattr(self, '_t_mat', m).yaw
					gPitch = getattr(self, '_g_mat', m).pitch

				turretMatrix = Math.Matrix()
				turretMatrix.setTranslate(-hullOffset - self.typeDescriptor.hull['turretPositions'][0])
				m = Math.Matrix()
				m.setRotateY(-tYaw)
				turretMatrix.postMultiply(m)
				res.append((self.typeDescriptor.turret, turretMatrix))

				gunMatrix = Math.Matrix()
				gunMatrix.setTranslate(-self.typeDescriptor.turret['gunPosition'])
				m = Math.Matrix()
				m.setRotateX(-gPitch)
				gunMatrix.postMultiply(m)
				gunMatrix.preMultiply(turretMatrix)
				res.append((self.typeDescriptor.gun, gunMatrix))

				return res

			def collideSegment(self, startPoint, endPoint, skipGun=False):
				import Math
				worldToVehMatrix = Math.Matrix(self.matrix)
				worldToVehMatrix.invert()
				startPoint = worldToVehMatrix.applyPoint(startPoint)
				endPoint = worldToVehMatrix.applyPoint(endPoint)
				res_closest = None
				all_hits = []
				for (compDescr, compMatrix) in self.getComponents():
					if skipGun and compDescr.get('itemTypeName') == 'vehicleGun':
						continue
					if not hasattr(compDescr.get('hitTester'), 'localHitTest'):
						continue
					collisions = compDescr['hitTester'].localHitTest(compMatrix.applyPoint(startPoint), compMatrix.applyPoint(endPoint))
					if collisions is None:
						continue
					for (dist, _, hitAngleCos, matKind) in collisions:
						matInfo = compDescr.get('materials', {}).get(matKind)
						if matInfo is None:
							# The mesh DOES carry device geometry the vehicle XML never assigns.
							# Scanned all 1975 collision meshes in the game: surveyingDevice appears
							# on 218 of 252 vehicles and gunBreech on 37, yet _readArmor only ever
							# builds the per-vehicle table from the <armor> section, which names
							# armor_N, the two tracks and the gun. Every optics hit was therefore
							# thrown away. common/vehicle.xml defines those kinds globally, with the
							# right extra and hit chances - fall back to it.
							try:
								from items import vehicles as _vgm
								matInfo = (_vgm.g_cache.commonConfig.get('materials') or {}).get(matKind)
								if matInfo is not None:
									_gm_seen = globals().setdefault('g_offh_global_mat', set())
									if matKind not in _gm_seen:
										_gm_seen.add(matKind)
										LOG_DEBUG('MATKIND from global table: id=%s extra=%s' % (matKind, getattr(getattr(matInfo, 'extra', None), 'name', None)))
							except Exception:
								matInfo = None
						# Does the COLLISION MESH carry material kinds the vehicle XML never
						# defines? The per-vehicle materials dict is built by _readArmor from the
						# <armor> section alone, and that section only ever names armor_N, the two
						# tracks and the gun. But common/vehicle.xml DOES define engine, ammoBay,
						# fuelTank, radio and every crewman as material kinds with their extras and
						# hit chances. If the BSP has triangles tagged with those kinds, the geometry
						# is present and only the per-vehicle LOOKUP is missing - which would be
						# fixable from the global table, no external tool needed. Report each unknown
						# kind once so this is decided by data instead of assumption.
						if matInfo is None:
							try:
								_seen_mk = globals().setdefault('g_offh_unknown_matkinds', set())
								if matKind not in _seen_mk:
									_seen_mk.add(matKind)
									_mkn = matKind
									try:
										import material_kinds as _MK
										for _n, _i in _MK.IDS_BY_NAMES.items():
											if _i == matKind:
												_mkn = _n
												break
									except Exception:
										pass
									LOG_DEBUG('MATKIND unresolved: id=%s name=%s comp=%s' % (matKind, _mkn, compDescr.get('itemTypeName', '?')))
							except Exception:
								pass
						all_hits.append((dist, hitAngleCos, matInfo, compDescr))
						if res_closest is None or res_closest[0] >= dist:
							res_closest = (dist, hitAngleCos, getattr(matInfo, 'armor', 0) if matInfo is not None else 0)
				if res_closest is not None:
					return (res_closest[0], res_closest[1], res_closest[2], all_hits)
				return None

		# Clear persistent data from previous offline battles, BUT keep the player!
		try:
			global G_OFFHANGAR_SHOTS_FIRED
			G_OFFHANGAR_SHOTS_FIRED = 0
			player = BigWorld.player()
			from gui.mods.offhangar import battle_feedback
			player._offhangar_battle_stats = battle_feedback.new_stats(BigWorld.time())
			player._offhangar_shots_fired = 0
			player._offhangar_has_sixth_sense = None
			player._offhangar_observed_until = 0.0
			player._offhangar_sixth_check_next = 0.0
			if hasattr(player, 'arena') and player.arena is not None:
				p_id = getattr(player, 'playerVehicleID', -1)
				if hasattr(player.arena, 'vehicles') and type(player.arena.vehicles) is dict:
					p_veh = player.arena.vehicles.get(p_id, None)
					player.arena.vehicles.clear()
					if p_veh is not None:
						player.arena.vehicles[p_id] = p_veh
				if hasattr(player.arena, 'statistics') and type(player.arena.statistics) is dict:
					p_stat = player.arena.statistics.get(p_id, None)
					player.arena.statistics.clear()
					if p_stat is not None:
						# Reset frags to 0 for the new battle!
						if 'frags' in p_stat: p_stat['frags'] = 0
						player.arena.statistics[p_id] = p_stat
		except: pass

		mock_veh = _MockVeh()

		mock_vehicles = {getattr(BigWorld.player(), 'playerVehicleID', -1): mock_veh}
		global G_MOCK_VEHICLES
		G_MOCK_VEHICLES = mock_vehicles

		# --- X-ray overlay (adopted). OFF by default and not even imported then:
		# it draws interior modules and crew straight through the armour, which is
		# a debug view here but exactly the kind of mod that gets accounts banned
		# on a live server - and this res_mods tree sits in a client that can log
		# in. config internal_xray_overlay=true is the only way it loads, and only
		# then are F8/F9/F10 bound.
		globals()['g_offh_internal_xray'] = None
		try:
			from _constants import CONFIG_OPTIONS as _XCFG
			if bool(_XCFG.get('internal_xray_overlay', False)):
				from gui.mods.offhangar import internal_layout_debug as _ILD
				from gui.mods.offhangar import internal_hit_layouts as _IHL2
				globals()['g_offh_internal_xray'] = _ILD.InternalLayoutDebugController(
					lambda: globals().get('G_MOCK_VEHICLES') or {},
					lambda: BigWorld.player(),
					_IHL2)
				LOG_DEBUG('X-ray overlay armed: F8 toggle, F9 view mode, F10 labels')
		except Exception as _xe:
			globals()['g_offh_internal_xray'] = None
			LOG_DEBUG('X-ray overlay unavailable:', str(_xe))
		# Belt for an interrupted prior sweep: detach native sticker models before
		# resetting the persistent account containers.
		try:
			_offh_detach_stickers(getattr(player, '_offhangar_stickers', None))
			_offh_detach_stickers(getattr(player, '_offhangar_sticker_map', None))
			player._offhangar_stickers = []
			player._offhangar_sticker_map = {}
		except Exception:
			pass

		# Wrap once and resolve the mock registry at call time: re-wrapping every
		# battle nested the previous wrapper in _orig_entity, so each battle's
		# whole mock_vehicles dict (bots, descriptors, model refs) stayed
		# reachable forever and the lookup chain grew battle by battle.
		if not getattr(BigWorld, '_offh_entity_wrapped', False):
			_orig_entity = BigWorld.entity
			def _mock_entity(eid):
				_mv = globals().get('G_MOCK_VEHICLES', {}) or {}
				if eid == getattr(BigWorld.player(), 'playerVehicleID', -1) and eid in _mv:
					return _mv[eid]
				orig_e = _orig_entity(eid)
				if orig_e is None and eid in _mv:
					return _mv[eid]
				return orig_e
			BigWorld.entity = _mock_entity
			BigWorld._offh_entity_wrapped = True
		# Minimap & friends read BigWorld.entities[id] directly (minimap.pyc:548
		# matrix = BigWorld.entities[id].matrix). Mock bots are not real engine
		# entities -> KeyError on every notifyVehicleStart ('GUI Add error') and
		# no bot ever reached the minimap. Wrap the dict: real entities first,
		# then the mock registry; enumeration stays original-only (engine-safe).
		if not getattr(BigWorld, '_offh_entities_wrapped', False):
			try:
				from gui.mods.offhangar.bot_ai import entity_visible_to_minimap as _offh_minimap_visible
			except Exception:
				_offh_minimap_visible = lambda entity: getattr(entity, '_spot_visible', True)
			class _OffhEntities(object):
				def __init__(self, orig):
					self._o = orig
				def __getitem__(self, k):
					try:
						return self._o[k]
					except KeyError:
						_mv = globals().get('G_MOCK_VEHICLES', {}) or {}
						if k in _mv:
							return _mv[k]
						raise
				def get(self, k, d=None):
					try:
						value = self[k]
						# Stock minimap.__detectLocation uses entities.get(). Hiding an
						# unspotted mock here prevents arena.onVehicleAdded from creating
						# an enemy icon. notifyVehicleStart uses __getitem__, so the icon
						# still appears normally on the first real spot.
						if not _offh_minimap_visible(value):
							return d
						return value
					except KeyError:
						return d
				def __contains__(self, k):
					if k in self._o:
						return True
					return k in (globals().get('G_MOCK_VEHICLES', {}) or {})
				def keys(self):
					return self._o.keys()
				def values(self):
					return self._o.values()
				def items(self):
					return self._o.items()
				def iteritems(self):
					return self._o.iteritems()
				def itervalues(self):
					return self._o.itervalues()
				def __iter__(self):
					return iter(self._o)
				def __len__(self):
					return len(self._o)
				def __getattr__(self, n):
					return getattr(self._o, n)
			BigWorld.entities = _OffhEntities(BigWorld.entities)
			BigWorld._offh_entities_wrapped = True

		player.getVehicleAttached = lambda: mock_veh
		player.getOwnVehicleMatrix = lambda: veh_matrix
		player.getOwnVehiclePosition = lambda: mock_veh.position
		player._offhangar_gui_visible = False
		def _mock_handleKey(key, isDown, mods=0):
			aih = getattr(player, 'inputHandler', None)
			if aih is not None and hasattr(aih, 'handleKeyEvent'):
				try: return aih.handleKeyEvent(key, isDown, mods)
				except: pass
			return False
		player.handleKey = _mock_handleKey

		def _offh_mouse_delta(args):
			try:
				if len(args) == 1 and hasattr(args[0], 'dx'):
					return (float(args[0].dx), float(args[0].dy), float(args[0].dz))
				if len(args) >= 3:
					return (float(args[0]), float(args[1]), float(args[2]))
			except Exception:
				pass
			return (0.0, 0.0, 0.0)

		def _offh_zoom_wheel_delta(dz):
			"""Preserve the stock 0.8.2 wheel sign for every camera path.

			The native camera handlers already interpret wheel-up as zooming in.
			Inverting here made physical mouse wheels behave backwards even though
			the synthetic fallback looked correct in isolation.
			"""
			try:
				return float(dz)
			except Exception:
				return 0.0

		def _offh_center_arcade_aim(ctrl):
			'''Keep the arcade sight and its world ray on the screen centre.

			Retail 0.8.2 deliberately offsets the arcade sight upward by 0.15 screen
			units. Offline aiming uses that same sight ray, so changing only the Flash
			marker would make the picture and the shot disagree. Centre both owners and
			leave sniper/strategic modes on their stock contracts.
			'''
			try:
				if ctrl is None or ctrl.__class__.__name__ != 'ArcadeControlMode':
					return False
				aim = ctrl.getAim()
				camera = ctrl.camera
				aim.offset((0.0, 0.0))
				camera.cursorOffset((0.0, 0.0))
				return True
			except Exception:
				return False

		def _offh_apply_zoom_attrs(ctrl, dz):
			# Ported: some 0.8.2 camera handlers silently reject wheel input in the
			# offline shell. Touch camera-only zoom fields (never aiming/ballistics).
			try:
				dz = float(dz)
				if abs(dz) <= 0.0001:
					return False
			except Exception:
				return False
			try:
				candidates = []
				cam = getattr(ctrl, 'camera', None)
				for obj in (cam, BigWorld.camera()):
					if obj is not None and obj not in candidates:
						candidates.append(obj)
				for obj in list(candidates):
					for name in ('_ArcadeCamera__cam', '_SniperCamera__cam', '_StrategicCamera__cam', '_camera', 'camera', 'cam'):
						try:
							sub = getattr(obj, name, None)
							if sub is not None and sub not in candidates:
								candidates.append(sub)
						except Exception:
							pass
				handled = False
				scale = 1.0 - max(-3.0, min(3.0, dz)) * 0.12
				if scale < 0.35:
					scale = 0.35
				if scale > 2.25:
					scale = 2.25
				# NEVER touch SniperCamera/StrategicCamera zoom state here: those
				# modes render their own camera offline and apply dz natively.
				# SniperCamera.__setupZoom walks the discrete zooms list and exits
				# to arcade only on the EXACT check zoom == zooms[0]; a scaled
				# float (2.0 -> 1.76) breaks both the levels and the scroll-out.
				attrs = ('distance', 'dist', 'height', 'curHeight', 'curDistance', '_distance', '_dist', '_height', '_curHeight', '_curDistance', '_ArcadeCamera__distance', '_ArcadeCamera__dist', '_ArcadeCamera__curDist')
				for obj in candidates:
					for name in attrs:
						try:
							old = getattr(obj, name)
						except Exception:
							continue
						try:
							if isinstance(old, (int, long, float)):
								new = float(old) * scale
								if new < 1.0:
									new = 1.0
								if new > 1200.0:
									new = 1200.0
								setattr(obj, name, new)
								handled = True
						except Exception:
							pass
				return handled
			except Exception:
				return False

		def _offh_wrap_aih_mouse_delivery(aih, wheel_player):
			"""Count wheel delivery at the actual stock game.py input entry."""
			if aih is None or getattr(aih, '_offh_wheel_delivery_wrapped', False):
				return aih
			original = aih.handleMouseEvent
			def _counted(dx, dy, dz):
				try:
					if abs(float(dz)) > 0.0001:
						wheel_player._offh_wheel_delivery_serial = int(getattr(
							wheel_player, '_offh_wheel_delivery_serial', 0) or 0) + 1
				except Exception:
					pass
				return original(dx, dy, dz)
			aih.handleMouseEvent = _counted
			aih._offh_wheel_delivery_wrapped = True
			return aih

		def _mock_handleMouse(*args):
			# Ported arty-camera fix: without this hook the wheel never reaches the
			# strategic camera offline, so the SPG view height could not be changed.
			try:
				aih = getattr(player, 'inputHandler', None)
				if aih is not None and hasattr(aih, 'handleMouseEvent'):
					dx, dy, raw_dz = _offh_mouse_delta(args)
					dz = raw_dz
					dz = _offh_zoom_wheel_delta(dz)
					normalized_args = (dx, dy, dz)
					try:
						return aih.handleMouseEvent(*normalized_args)
					except Exception:
						pass
			except Exception:
				pass
			return False
		player.handleMouseEvent = _mock_handleMouse

		import game
		if not getattr(game, '_offhangar_hooked', False):
			game._offhangar_hooked = True
			# game.handleMouseEvent resolves this module global at call time.  Patch
			# the conversion point so the stock arcade/sniper/strategic control modes
			# all receive the same conventional wheel direction.  GUI still receives
			# the original event object, so menu scrolling is untouched.
			orig_game_convertMouseEvent = game.convertMouseEvent
			def _offh_convertMouseEvent(event):
				dx, dy, dz, cursor_pos = orig_game_convertMouseEvent(event)
				try:
					p = BigWorld.player()
					if (p is not None and getattr(p, 'isOffline', False) and
							globals().get('g_offh_battle_space', 0)):
						dz = _offh_zoom_wheel_delta(dz)
				except Exception:
					pass
				return (dx, dy, dz, cursor_pos)
			game.convertMouseEvent = _offh_convertMouseEvent
			orig_game_handleKeyEvent = game.handleKeyEvent
			def _mock_game_handleKeyEvent(event):
				# NO ESC handling here! The flash menu handles ESC itself (on key
				# DOWN) and fires Battle.cursorVisibility on BOTH open and close -
				# proven by the CursorDBG log - and the patched cursorVisibility
				# below drives the input gate from it. The old key-UP toggle here
				# double-acted on the same press, always leaving the state inverted
				# from the actual menu (aim stuck with cursor shown after closing,
				# or menu open with no cursor).
				return orig_game_handleKeyEvent(event)
			game.handleKeyEvent = _mock_game_handleKeyEvent
			# Wheel: offline the flash GUI often EATS wheel events before they
			# reach the AIH (InputDBG: in sniper the fullscreen binoculars ate
			# 19 of 21 events), so the native zoom path never sees them. The old
			# unconditional fallback masked that but also ran when the native
			# path DID handle the wheel - double zoom in arcade, and its direct
			# _SniperCamera__zoom writes broke the discrete 2/4/8 walk and the
			# scroll-out exit (exact zoom == zooms[0] check in __setupZoom).
			# Correct rule: record whether this exact event reached the actual
			# AvatarInputHandler entry and, only if not, re-deliver dz through
			# AvatarInputHandler. Camera distance is not a valid delivery signal:
			# it legitimately remains unchanged at either zoom limit.
			# ArcadeControlMode must own this call: its TargetPointCalculator saves
			# the world point under the reticle, changes camera distance, then calls
			# focusOnPos() so wheel-only zoom does not move the aim point. Calling
			# ArcadeCamera.update() directly skips that fixed-point correction.
			# This fallback replaces only a wheel event consumed by Flash. Do not
			# replay SniperCamera's stored dx/dy: those belong to an earlier event
			# (or keyboard auto-update) and would rotate the view a second time.
			orig_game_handleMouseEvent = game.handleMouseEvent
			def _mock_game_handleMouseEvent(event):
				# no zoom while dead/spectating - postmortem must not sniper-zoom into the wreck.
				# ONLY while a battle is actually on screen: this handler is installed on
				# game.handleMouseEvent globally, so it runs in the hangar and every menu too,
				# and _is_dead / _offh_spectating are cleared at battle START, not on exit.
				# After the first battle you died in, both stayed True all the way back to the
				# hangar and this swallowed every mouse wheel event - scrolling in the menus
				# died out of nowhere and only came back once the next battle started.
				try:
					_pz = BigWorld.player()
					if _pz is not None and (getattr(_pz, '_is_dead', False) or getattr(_pz, '_offh_spectating', False)):
						_in_battle = False
						try:
							from gui import WindowsManager as _wmz
							_in_battle = getattr(_wmz.g_windowsManager, 'battleWindow', None) is not None
						except Exception:
							_in_battle = False
						if _in_battle and abs(float(getattr(event, 'dz', 0.0))) > 0.0001:
							return True
				except Exception:
					pass
				pre = None
				try:
					dz = _offh_zoom_wheel_delta(getattr(event, 'dz', 0.0))
					if abs(dz) > 0.0001:
						p = BigWorld.player()
						if p is not None and getattr(p, 'isOffline', False):
							aih = getattr(p, 'inputHandler', None)
							ctrl = getattr(aih, 'ctrl', None)
							cam = getattr(ctrl, 'camera', None)
							# gated input (pause menu open / cursor detached):
							# eaten on purpose, do not zoom
							if cam is not None and getattr(aih, '_AvatarInputHandler__isStarted', False) and getattr(aih, '_AvatarInputHandler__detachCount', 0) >= 0:
								pre = (aih, ctrl, cam, p, int(getattr(
									p, '_offh_wheel_delivery_serial', 0) or 0), dz)
				except Exception:
					pre = None
				result = orig_game_handleMouseEvent(event)
				if pre is not None:
					try:
						aih, ctrl, cam, wheel_player, serial_before, dz = pre
						if (getattr(aih, 'ctrl', None) is ctrl and
								int(getattr(wheel_player,
									'_offh_wheel_delivery_serial', 0) or 0) == serial_before):
							dzc = max(-1.0, min(1.0, dz))
							aih.handleMouseEvent(0.0, 0.0, dzc)
					except Exception:
						pass
				return result
			game.handleMouseEvent = _mock_game_handleMouseEvent
			# Route the flash cursor callback through the same state-setter so
			# menu-button closes restore aiming (fixes the stuck-aim repro).
			try:
				from gui.Scaleform.Battle import Battle as _BattleWnd
				if not getattr(_BattleWnd, '_offh_cursor_patched', False):
					_orig_cv = _BattleWnd.cursorVisibility
					def _offh_cursorVisibility(self, callbackId, visible, x=None, y=None, customCall=False, enableAiming=True):
						_orig_cv(self, callbackId, visible, x, y, customCall, enableAiming)
						try:
							import BigWorld
							p = BigWorld.player()
							if p is not None and getattr(p, 'isOffline', False):
								LOG_DEBUG('CursorDBG: flash cursorVisibility ->', visible)
								_offh_set_battle_gui_mode(visible)
						except Exception:
							pass
					_BattleWnd.cursorVisibility = _offh_cursorVisibility
					_BattleWnd._offh_cursor_patched = True
			except Exception:
				LOG_CURRENT_EXCEPTION()
			# Alt-tab fix: offline the aim object can be None during a device recreate;
			# the exception aborted game.onRecreateDevice mid-way so the GUI resetters
			# never ran and HUD/input came back broken after tabbing back in.
			try:
				import AvatarInputHandler as _AIHmod
				if not getattr(_AIHmod.AvatarInputHandler, '_offh_rc_wrapped', False):
					_orig_rc = _AIHmod.AvatarInputHandler._AvatarInputHandler__onRecreateDevice
					def _offh_safe_rc(self, *a, **kw):
						try:
							return _orig_rc(self, *a, **kw)
						except Exception:
							pass
					_AIHmod.AvatarInputHandler._AvatarInputHandler__onRecreateDevice = _offh_safe_rc
					_AIHmod.AvatarInputHandler._offh_rc_wrapped = True
			except Exception:
				pass

		def _leaveArena():
			if _exit_done[0]:
				return
			if not _offh_sweep_or_retry('quit', _leaveArena):
				return
			_battle_finished[0] = True
			_exit_done[0] = True
			# Leaving the synthetic arena also leaves the LAN room.  Once the last
			# client disconnects, the server can reset and accept the next round.
			try:
				from gui.mods.offhangar.network_battle import stop_for_player
				stop_for_player(player)
			except Exception:
				LOG_CURRENT_EXCEPTION()
			# Tear the overlay down before the space goes: it holds GUI components
			# and a repeating callback, and both outlive the arena otherwise.
			_xr_stop = globals().get('g_offh_internal_xray')
			if _xr_stop is not None:
				try:
					_xr_stop.stop()
				except Exception:
					pass
				globals()['g_offh_internal_xray'] = None
			# Exactly ONE exit path may tear the battle down. Player death
			# schedules _exit_battle(+3s) AND triggers battle results ->
			# _leaveArena: both ran the full teardown, so the hangar was
			# destroyed + re-inited TWICE, the second init racing the first
			# one's async load -> broken garage return and a leaked
			# half-loaded hangar space per occurrence.
			# The ownership sweep above is the continuation gate: no hangar or model
			# teardown below may run while a native Servo/filter still owns a bot.
			try:
				_offh_detach_stickers(getattr(player, '_offhangar_stickers', None))
				player._offhangar_stickers = []
				player._offhangar_sticker_map = {}
			except Exception: pass
			try:
				import SoundGroups as _SG
				if getattr(_SG, 'g_instance', None) is not None:
					_SG.g_instance.enableArenaSounds(False)
					_SG.g_instance.enableLobbySounds(True)
			except Exception: pass
			try:
				_aih = getattr(player, 'inputHandler', None)
				if _aih is not None:
					try: _aih._AvatarInputHandler__isStarted = False
					except: pass
					for _cm in getattr(_aih, '_AvatarInputHandler__ctrls', {}).values():
						try: _cm.destroy()
						except: pass
					# Parity with the death-exit path: every battle's AIH registers
					# __onRecreateDevice in game.g_guiResetters at construction; left
					# in, one dead resetter per battle piles up and a later device
					# recreate (alt-tab, res change) runs them all against torn-down
					# controls - the GUI never comes back from that.
					try:
						import game
						if hasattr(_aih, '_AvatarInputHandler__onRecreateDevice'):
							game.g_guiResetters.remove(_aih._AvatarInputHandler__onRecreateDevice)
					except: pass
					player.inputHandler = None
			except Exception: pass

			try:
				from gui import WindowsManager
				if hasattr(WindowsManager.g_windowsManager, 'destroyBattle'):
					WindowsManager.g_windowsManager.destroyBattle()
				else:
					WindowsManager.g_windowsManager.hideAll()
				if hasattr(WindowsManager.g_windowsManager, 'showLobby'):
					WindowsManager.g_windowsManager.showLobby()
			except Exception: pass

			try:
				import BigWorld
				BigWorld.camera(None)
				BigWorld.worldDrawEnabled(True)
			except: pass

			try:
				from gui.Scaleform.utils.HangarSpace import g_hangarSpace
				if g_hangarSpace is not None:
					try: g_hangarSpace.destroy()
					except Exception: pass

					# Prevent showLobby from destroying the space
					def _mock_refreshSpace(self, isPremium):
						pass
					g_hangarSpace.__class__.refreshSpace = _mock_refreshSpace

					# Force premium
					def _mock_getSpacePath(self, isPremium):
						return self._HangarSpace__space.getDefSpacePath(True)
					g_hangarSpace.__class__._HangarSpace__getSpacePath = _mock_getSpacePath

					# Init manually
					# WG 'no man's land' purge: hangar destroyed, nothing active, before re-init.
					_offh_safe_purge()
					g_hangarSpace.init(True)
			except Exception: pass


			try:
				global g_offline_models
				# Clear FIRST (see sweep 'models' stage): delModel's pending error
				# raises at loop exhaustion and would skip a post-loop clear,
				# leaking the dead battle's models into the next one.
				_gm_list = list(g_offline_models)
				g_offline_models = []
				for m in _gm_list:
					_offh_del_model(m)
			except Exception: pass
			try:
				import gui.mods.offhangar._constants as _c
				for _e in BigWorld.entities.values():
					if _e.__class__.__name__ in ('PlayerAccount', 'Account'):
						_e._offline_allow_become_non_player = True
						if hasattr(_e, '_offhangar_orig_stats') and _e._offhangar_orig_stats is not None:
							_e.stats = _e._offhangar_orig_stats
						try: _e.showGUI(_c.OFFLINE_GUI_CTX)
						except Exception: pass
			except Exception: pass

		player.leaveArena = _leaveArena

		def _setGUIVisible(visible):
			aih = getattr(player, 'inputHandler', None)
			if aih is not None:
				try: aih._AvatarInputHandler__isGUIVisible = visible
				except: pass
				if hasattr(aih, 'setGUIVisible'):
					try: aih.setGUIVisible(visible)
					except: pass
		player.setGUIVisible = _setGUIVisible

		player.getAutorotation = lambda: False
		player.enableOwnVehicleAutorotation = lambda val: None

		class FakePositionControl(object):
			def bindToVehicle(self, *a, **k): pass
			def followCamera(self, *a, **k): pass
			def moveTo(self, *a, **k): pass
		player.positionControl = FakePositionControl()

		class FakeStats(object):
			def getCache(self, cb): cb(1, {})
			def __getattr__(self, name): return lambda *a, **k: None

		if not hasattr(player, '_offhangar_orig_stats'):
			player._offhangar_orig_stats = getattr(player, 'stats', None)
		player.stats = FakeStats()

		class FakeGunRotator(object):
			def __init__(self):
				import Math
				self.markerInfo = (Math.Vector3(0.0, 0.0, 0.0), Math.Vector3(0.0, 1.0, 0.0), 1.0)
				self.dispersionAngle = 0.1
			def getShotParams(self, targetPos, *a, **kw):
				import BigWorld, Math
				try:
					from projectile_trajectory import getShotAngles
					descr = BigWorld.player().vehicleTypeDescriptor
					# VehicleTypeDescriptor owns the stock active-shell switch. Keep it in
					# step with the offline ammo panel before asking the native ballistic
					# solver, otherwise an alternate shell can draw the default shell's arc.
					try:
						descr.activeGunShotIndex = _gun_state.get('shot_index', 0)
					except Exception:
						pass
					speed = descr.shot['speed']
					gravity = descr.shot['gravity']
					mat = BigWorld.player().getOwnVehicleMatrix()

					# Get exact required gun elevation angle to hit targetPos
					try:
						(shotTurretYaw, shotGunPitch) = getShotAngles(descr, mat, (0, 0), targetPos)
					except Exception:
						shotTurretYaw, shotGunPitch = getattr(self, '_turret_yaw', 0.0), getattr(self, '_gun_pitch', 0.0)

					# Clamp to limits so trajectory doesn't draw where gun can't reach
					import math
					try:
						pl = descr.gun['pitchLimits']
						from gun_rotation_shared import calcPitchLimitsFromDesc
						limits = calcPitchLimitsFromDesc(shotTurretYaw, pl)
						if shotGunPitch < limits[0]: shotGunPitch = limits[0]
						elif shotGunPitch > limits[1]: shotGunPitch = limits[1]
					except: pass

					try:
						yl = descr.gun.get('turretYawLimits', None)
						if yl is None and descr.turret is not None:
							yl = descr.turret.get('yawLimits', None)
						if yl is not None:
							min_yaw = float(yl[0])
							max_yaw = float(yl[1])
							if abs(min_yaw) > 10.0:
								min_yaw = math.radians(min_yaw)
								max_yaw = math.radians(max_yaw)
							if shotTurretYaw < min_yaw: shotTurretYaw = min_yaw
							elif shotTurretYaw > max_yaw: shotTurretYaw = max_yaw
					except: pass

					# Calculate actual world space gun position and velocity vector
					turretOffs = descr.hull['turretPositions'][0] + descr.chassis['hullPosition']
					gunOffs = descr.turret['gunPosition']
					turretWorldMatrix = Math.Matrix()
					turretWorldMatrix.setRotateY(shotTurretYaw)
					turretWorldMatrix.translation = turretOffs
					turretWorldMatrix.postMultiply(mat)
					position = turretWorldMatrix.applyPoint(gunOffs)
					gunWorldMatrix = Math.Matrix()
					gunWorldMatrix.setRotateX(shotGunPitch)
					gunWorldMatrix.postMultiply(turretWorldMatrix)
					vector = gunWorldMatrix.applyVector(Math.Vector3(0, 0, speed))

					return (position, vector, Math.Vector3(0, -gravity, 0))
				except Exception as e:
					LOG_DEBUG('OfflineBattle getShotParams ERROR:', str(e))
					# fallback
					try:
						speed = BigWorld.player().vehicleTypeDescriptor.shot['speed']
						gravity = BigWorld.player().vehicleTypeDescriptor.shot['gravity']
					except:
						speed, gravity = 250.0, 9.81
					if hasattr(self, '_gun_pos') and hasattr(self, '_gun_dir'):
						return (self._gun_pos, self._gun_dir.scale(speed), Math.Vector3(0, -gravity, 0))
					startPos = BigWorld.player().getOwnVehiclePosition()
					startPos.y += 2.0
					v0 = BigWorld.camera().direction
					return (startPos, v0.scale(speed), Math.Vector3(0, -gravity, 0))
			def _VehicleGunRotator__getCurShotPosition(self):
				import BigWorld, Math
				try:
					speed = BigWorld.player().vehicleTypeDescriptor.shot['speed']
				except:
					speed = 250.0
				if hasattr(self, '_gun_pos') and hasattr(self, '_gun_dir'):
					return (self._gun_pos, self._gun_dir.scale(speed))
				startPos = BigWorld.player().getOwnVehiclePosition()
				startPos.y += 2.0
				v0 = BigWorld.camera().direction
				return (startPos, v0.scale(speed))
		player.gunRotator = FakeGunRotator()

		# Report real simulated speeds so dispersion reacts to movement
		# (_veh_velocity/_veh_turn_velocity are defined below; resolved at call time)
		player.getOwnVehicleSpeeds = lambda: (_veh_velocity[0], _veh_turn_velocity[0])
		player.autoAim = lambda val: None

		if hasattr(player, 'arena') and player.arena is not None:
			if not hasattr(player.arena, 'collideWithSpaceBB') or not callable(getattr(player.arena, 'collideWithSpaceBB', None)):
				player.arena.collideWithSpaceBB = lambda *a, **kw: None

		veh_yaw     = [spawn_dir.z]
		turret_yaw  = [0.0]   # relative to hull
		gun_pitch   = [0.0]   # gun elevation
		veh_pos = [spawn_pos.x, spawn_pos.y, spawn_pos.z]
		try:
			from gui.mods.offhangar import battle_feedback as _offh_feedback_start
			_offh_feedback_start.record_position(
				_offh_stats_for(player), (veh_pos[0], veh_pos[1], veh_pos[2]))
		except Exception:
			pass
		turret_matrix = Math.Matrix()
		turret_matrix.setTranslate(Math.Vector3(spawn_pos.x, spawn_pos.y + 2.0, spawn_pos.z))
		turret_matrix_local = Math.Matrix()

		# Read turret/gun rotation limits from vehicle descriptor
		_turret_rot_speed = 1.5  # rad/s default
		_gun_min_pitch    = -0.35  # ~-20 deg (ELEVATION - UP) default
		_gun_max_pitch    =  0.15  # ~+8.6 deg (DEPRESSION - DOWN) default
		_gun_pitch_desc   = None   # full 0.8.2 pitchLimits descriptor (yaw-dependent)
		_gun_pitch_speed  = 0.75   # rad/s fallback vertical aim speed
		_gun_min_yaw      = -3.14159
		_gun_max_yaw      =  3.14159
		try:
			if td is not None:
				rot = td.turret.get('rotationSpeed', None)
				if rot is not None:
					_turret_rot_speed = float(rot)  # descriptor stores rad/s
				pl = td.gun.get('pitchLimits', None)
				if pl is not None:
					try:
						if isinstance(pl, dict):
							# 0.8.2 descriptor: {'basic': (minRad, maxRad), 'absolute': (...),
							# optional 'front'/'back'/'transition'} - values ALREADY in radians.
							# (The old minPitch/minAngle keys never existed in this format, so
							# every tank silently fell back to the hardcoded default limits.)
							_gun_pitch_desc = pl
							lim = pl.get('basic') or pl.get('absolute')
							if lim:
								_gun_min_pitch = float(lim[0])
								_gun_max_pitch = float(lim[1])
						elif isinstance(pl, (list, tuple)) and len(pl) >= 2:
							_gun_min_pitch = float(pl[0])
							_gun_max_pitch = float(pl[1])
					except Exception as pe:
						LOG_DEBUG('OfflineBattle pitch parsing error:', str(pe))
				# Vertical aim speed from the descriptor (radians/s) instead of the
				# old hardcoded 2.5 rad/s (~143 deg/s - several times too fast).
				try:
					_gs = td.gun.get('rotationSpeed', None)
					if _gs:
						_gun_pitch_speed = float(_gs)
				except Exception:
					pass
				try:
					import math as _math
					LOG_DEBUG('PitchDBG: elevation=%.1f deg (up), depression=%.1f deg (down), yawDependent=%s, aimSpeed=%.1f deg/s' % (
						-_math.degrees(_gun_min_pitch), _math.degrees(_gun_max_pitch),
						_gun_pitch_desc is not None and (('front' in _gun_pitch_desc) or ('back' in _gun_pitch_desc)),
						_math.degrees(_gun_pitch_speed)))
				except Exception:
					pass
				yl = td.gun.get('turretYawLimits', None)
				if yl is None and td.turret is not None:
					yl = td.turret.get('yawLimits', None)
				if yl is not None:
					import math as _math
					_gun_min_yaw = float(yl[0])
					_gun_max_yaw = float(yl[1])
					if abs(_gun_min_yaw) > 10.0 or abs(_gun_max_yaw) > 10.0:
						_gun_min_yaw = _math.radians(_gun_min_yaw)
						_gun_max_yaw = _math.radians(_gun_max_yaw)
		except Exception as e:
			LOG_DEBUG('OfflineBattle.limits error:', str(e))

		_tick_counter = [0]

		# Engine and track sound state
		_sound_state = {
			'engine_sound': None,
			'tread_sound': None,
			'last_engine_event': '',
			'last_tread_event': '',
		}

		# Determine tank class for sound events
		_tank_class = 'medium'
		try:
			if td is not None:
				tags = td.type.tags if hasattr(td, 'type') and hasattr(td.type, 'tags') else set()
				if 'lightTank' in tags: _tank_class = 'light'
				elif 'heavyTank' in tags: _tank_class = 'heavy'
				elif 'SPG' in tags or 'AT-SPG' in tags: _tank_class = 'SAU'
				else: _tank_class = 'medium'
			LOG_DEBUG('OfflineBattle.tank_class:', _tank_class)
		except Exception as e:
			LOG_DEBUG('OfflineBattle.tank_class error:', str(e))

		# Map tank class to FMOD event prefix
		_engine_idle_event = '/tanks/%s/%s/%s' % (
			{'light': 'light', 'heavy': 'heavy', 'medium': 'medium', 'SAU': 'medium'}.get(_tank_class, 'medium'),
			{'light': 'MC-1', 'heavy': 'IS_2', 'medium': 'tiger', 'SAU': 'tiger'}.get(_tank_class, 'tiger'),
			{'light': 'idle', 'heavy': 'IS_2_stand', 'medium': 'tiger_idle', 'SAU': 'tiger_idle'}.get(_tank_class, 'tiger_idle'),
		)
		_engine_run_event = '/tanks/%s/%s/%s' % (
			{'light': 'light', 'heavy': 'heavy', 'medium': 'medium', 'SAU': 'medium'}.get(_tank_class, 'medium'),
			{'light': 'MC-1', 'heavy': 'IS_2', 'medium': 'tiger', 'SAU': 'tiger'}.get(_tank_class, 'tiger'),
			{'light': 'run', 'heavy': 'heavy_tank_run_state2', 'medium': 'medium_tank_state2', 'SAU': 'medium_tank_state2'}.get(_tank_class, 'medium_tank_state2'),
		)
		_tread_prefix = '/tanks/tanks_treads/%s_tank' % ({'SAU': 'SAU'}.get(_tank_class, _tank_class))

# --- GUN MECHANICS STATE ---
		_gun_state = {
			'base_dispersion': 0.1,
			'after_shot': 1.5,
			'aim_time': 2.0,
			'clip_size': 1,
			'clip_reload': 2.0,
			'reload': 5.0,
			'ammo': 100,
			'clip': 1,
			'reloadTime': 0.0,
			'dispersion': 0.1,
			'initialized': False,
			'shot_index': 0,
			'prebattle_marker_seeded': False,
			'marker_in_prebattle': False,
			# Stock 0.8.2 cruise modes: -2/-1/0/1/2/3 represent reverse
			# 100/50, off, and forward 25/50/100 percent.
			'cruise_mode': 0,
			'cruise_last_key': None,
			'cruise_last_time': -1.0,
			'cruise_press_count': 0,
			# The 2012 client can expose a stale global isKeyDown value when the
			# render thread is saturated by authority-side bot simulation.  Once a
			# real movement event reaches the AIH, its down/up edge is canonical.
			'manual_input_events': False,
			'manual_forward_down': False,
			'manual_backward_down': False,
			'manual_left_down': False,
			'manual_right_down': False
		}

		_engine_state = {'init': False, 'snd1': None, 'snd2': None}
		globals()['g_offh_engine_state'] = _engine_state

		_veh_velocity = [0.0]        # m/s, forward speed
		_veh_turn_velocity = [0.0]   # rad/s, current hull rotation speed
		_last_tick_time = [BigWorld.time()]
		_veh_vert_vel = [0.0]        # m/s, vertical (falling) speed
		_veh_airborne = [False]      # True while the hull has left the ground
		_veh_fall_armed = [False]    # fall damage arms only after the FIRST real ground
		                             # contact: the spawn drop (collide-miss fallback puts
		                             # the hull well above ground) must land free, or the
		                             # tank spawns damaged/dead (WZ-111 report)

		# === WoT-style physics parameters ===
		import math
		# ONE source of physics laws + parameters for player AND bots:
		# gui.mods.offhangar.physics (see its module docstring for units).
		from gui.mods.offhangar import physics as _PHY
		# Keep this binding at battle scope. Importing the same name inside
		# _aih_tick makes every earlier nested collision helper capture an unbound
		# local cell: vehicle contacts silently disappear and slope contacts fail
		# closed as walls before the later import can execute.
		from gui.mods.offhangar import vehicle_collision as _VC
		from gui.mods.offhangar import vehicle_pose as _VP
		# Live tuning: config.json "physics_tuning" overrides the WG constants
		# (cohesion, power, brake, slide thresholds...) - restart, no recompile.
		# MUST run before derive_params so the new values reach the params.
		try:
			from _constants import CONFIG_OPTIONS as _CFG_PHY
			_applied_tuning = _PHY.apply_tuning(_CFG_PHY.get('physics_tuning'))
			if _applied_tuning:
				LOG_DEBUG('OfflineBattle.PHYSICS tuning: ' + ', '.join(_applied_tuning))
			# Same idea for the two HE blast constants, under "he_tuning". Those are a
			# reconstruction (the damage calculator is cell-side and is not shipped), so
			# dialling them without a recompile matters more here than for the physics.
			_applied_he = _offh_he_apply_tuning(_CFG_PHY.get('he_tuning'))
			if _applied_he:
				LOG_DEBUG('OfflineBattle.HE tuning: ' + ', '.join(_applied_he))
			_offh_phys_debug = [bool(_CFG_PHY.get('physics_debug', False))]
		except Exception:
			_offh_phys_debug = [False]
		if _offh_phys_debug[0]:
			try:
				import gui.mods.offhangar.physics_monitor as _offh_mon
				_offh_mon.reset()
				LOG_DEBUG('OfflineBattle.PHYSICS telemetry ON -> offhangar_user/physics_telemetry.csv')
			except Exception:
				_offh_phys_debug[0] = False
		_pparams = _PHY.derive_params(td)
		# Local aliases: the tick code below and several helpers (tank_resolve,
		# sounds, scroll caps) read these names.
		_phys_mass           = _pparams['mass']
		_phys_enginePowerW   = _pparams['powerW']
		_phys_speedFwd       = _pparams['speedFwd']
		_phys_speedBwd       = _pparams['speedBwd']
		_phys_chassisRotSpd  = _pparams['rotSpd']
		_phys_terrainResist  = _pparams['terrainResist']
		_phys_specificFriction = _pparams['specificFriction']
		_phys_terrainCoeff   = _pparams['terrainResist'][0]
		_phys_gravity        = _PHY.GRAVITY
		_phys_brakeDecel     = _pparams['brakeDecel']
		_phys_trackCenter    = _pparams['trackCenter']
		# Collision contacts and reciprocal responses live for one rendered frame.
		# A pair may be encountered first from either vehicle, but is solved once.
		_tank_pair_seen = {}
		_tank_pair_pending = {}
		# One immutable broad-phase snapshot per rendered frame. The physics
		# resolver still reads each candidate's live pose before the narrow OBB
		# test; this index only removes distant all-pairs Python object walks.
		_traffic_spatial = [None]
		# Collision needs a much smaller neighbourhood than the 24 m local driver.
		# Keep a second index instead of making every OBB pass walk the driver's
		# 72 x 72 m query square. Its cell size is derived from the largest chassis
		# in the line-up below, so the surrounding nine cells cannot miss a pair.
		_collision_spatial = [None]
		_collision_frame = [None]
		_collision_candidates = [None]
		LOG_DEBUG('OfflineBattle.PHYSICS: mass=%.0f, power=%.0fW, fwd=%.1f m/s, bwd=%.1f m/s, rot=%.1f deg/s, terrain=(%.2f,%.2f,%.2f), friction=%.4f, brake=%.2f m/s2, halfGauge=%.2f' % (
			_phys_mass, _phys_enginePowerW, _phys_speedFwd, _phys_speedBwd,
			math.degrees(_phys_chassisRotSpd), _phys_terrainResist[0], _phys_terrainResist[1], _phys_terrainResist[2],
			_phys_specificFriction, _phys_brakeDecel, _phys_trackCenter))
		_battle_finished = [False]
		_exit_done = [False]  # once-guard shared by ALL exit paths (leaveArena / death / K)
		# The generation was established before model loading. Per-frame loops
		# capture the same value and stop when the next battle bumps it.
		globals().pop('g_offh_bot_director', None)
		globals().pop('g_offh_terrain_navigator', None)
		globals().pop('g_offh_baked_navigation_graph', None)
		globals().pop('g_offh_spot_foliage', None)
		globals().pop('g_offh_spot_foliage_error', None)
		globals().pop('g_offh_local_driver', None)
		globals().pop('g_offh_ai_hull_dims', None)
		globals().pop('g_offh_ai_local_covers', None)
		globals().pop('g_offh_ai_cover_cursor', None)
		globals().pop('g_offh_ai_cover_t', None)
		globals().pop('g_offh_ai_cover_reports', None)
		globals().pop('g_offh_ai_artillery_cursor', None)
		globals().pop('g_offh_ai_network_contacts', None)
		globals().pop('g_offh_ai_frame_budget', None)
		globals().pop('g_offh_ai_contacts_t', None)
		globals().pop('g_offh_ai_diagnostics_t', None)
		globals().pop('g_offh_spot_resource_profiles', None)
		globals().pop('g_offh_spot_player_crew', None)
		globals().pop('g_offh_spot_fallback_logged', None)
		globals().pop('g_offh_ai_init_error_logged', None)
		globals().pop('g_offh_ai_navigation_disabled', None)
		for _nav_error_key in [value for value in globals()
		                       if value.startswith('g_offh_ai_navigation_error_')]:
			globals().pop(_nav_error_key, None)
		_offh_seen_arena = [False]
		_offh_seen_bw = [False]

		global g_base_capture
		from gui.mods.offhangar import capture_rules as _capture_rules_init
		g_base_capture = {
			1: _capture_rules_init.new_state(),
			2: _capture_rules_init.new_state(),
		}
		globals().pop('G_OFFH_FORCED_WINNER', None)  # stale capture-win flag from a crashed exit
		globals().pop('g_offh_capture_won', None)
		globals().pop('g_offh_battle_over', None)
		globals().pop('g_offh_result_requested', None)
		globals().pop('_offh_kill_msgs', None)
		globals().pop('_offh_settled_deaths', None)
		globals().pop('_offh_canonical_frags', None)
		# Drop the hit-sound carrier reference. It is added with BigWorld.addModel,
		# NOT through _add_model, so the end-of-battle sweep never saw it - carrying
		# a model reference across a space teardown is the dangling-reference case
		# that has crashed the next space load before.
		globals().pop('g_offh_hit_carrier', None)
		globals().pop('g_offh_hit_snd_t', None)
		# New battle, new queue - the old one belongs to the finished arena.
		globals().pop('g_offh_crew_notif', None)
		globals().pop('g_offh_roster_ready', None)

		global g_capture_tick_ref
		def trigger_battle_results(winnerTeam=1):
			import BigWorld
			player = BigWorld.player()
			if player is None: return
			try:
				from gui.SystemMessages import SM_TYPE, pushMessage
				pushMessage('Offline battle finished. Returning to Hangar...'.encode('utf-8'), SM_TYPE.Information)
			except Exception as e: pass

			try:
				import MusicController
				if hasattr(MusicController, 'g_musicController') and MusicController.g_musicController:
					_mc = MusicController.g_musicController
					try: _mc.stop()
					except: pass
					evt = None
					p_team = getattr(player, 'team', 1)
					if winnerTeam == p_team:
						evt = getattr(MusicController, 'MUSIC_EVENT_COMBAT_VICTORY', getattr(MusicController, 'MUSIC_EVENT_VICTORY', 'music_victory'))
					elif winnerTeam != 0:
						evt = getattr(MusicController, 'MUSIC_EVENT_COMBAT_LOSE', getattr(MusicController, 'MUSIC_EVENT_LOSE', 'music_lose'))
					else:
						evt = getattr(MusicController, 'MUSIC_EVENT_COMBAT_DRAW', getattr(MusicController, 'MUSIC_EVENT_DRAW', 'music_draw'))
					try: _mc.play(evt)
					except: pass
			except Exception as e: pass

			try:
				import battle_results_shared
				mock_arena_id = 999

				v_id = getattr(player, 'playerVehicleID', 1)
				p_max_health = getattr(getattr(player, 'vehicleTypeDescriptor', None), 'maxHealth', 1000)
				p_health = getattr(getattr(player, 'vehicle', None), 'health', p_max_health)

				_player_mock = globals().get('G_MOCK_VEHICLES', {}).get(getattr(player, 'playerVehicleID', -1))
				_p_killer_id = getattr(_player_mock, 'last_killer_id', 255) if p_health <= 0 else 0

				p_team = getattr(player, 'team', 1)
				p_dbid = getattr(player, 'databaseID', 1)
				p_name = getattr(player, 'name', 'Player')
				p_cd = getattr(getattr(getattr(player, 'vehicleTypeDescriptor', None), 'type', None), 'compactDescr', 0)

				players_dict = {p_dbid: {'name': p_name, 'clanDBID': 0, 'clanAbbrev': '', 'prebattleID': 0, 'team': p_team, 'igrType': 0}}
				vehicles_dict = {v_id: {'health': p_health, 'credits': 10000, 'xp': 1000, 'shots': 10, 'hits': 8, 'he_hits': 0, 'pierced': 8, 'damageDealt': 0, 'damageAssisted': 0, 'damageReceived': max(0, p_max_health - p_health), 'shotsReceived': 0, 'spotted': 0, 'damaged': 0, 'kills': 0, 'tdamageDealt': 0, 'tkills': 0, 'isTeamKiller': False, 'capturePoints': 0, 'droppedCapturePoints': 0, 'mileage': 100, 'lifeTime': 300, 'killerID': _p_killer_id, 'achievements': [], 'repair': 0, 'freeXP': 50, 'details': {}, 'accountDBID': p_dbid, 'team': p_team, 'typeCompDescr': p_cd, 'gold': 0}}

				for vid, vinfo in getattr(player.arena, 'vehicles', {}).items():
					if vid == v_id: continue
					bot_team = vinfo.get('team', 2)
					bot_name = vinfo.get('name', 'Bot')
					bot_dbid = vid
					td = vinfo.get('vehicleType', None)
					td_type = getattr(td, 'type', None)
					bot_cd = getattr(td_type, 'compactDescr', 0)

					players_dict[bot_dbid] = {'name': bot_name, 'clanDBID': 0, 'clanAbbrev': '', 'prebattleID': 0, 'team': bot_team, 'igrType': 0}

					is_killed = not vinfo.get('isAlive', True)
					bot_hp = getattr(td, 'maxHealth', 1000)
					if is_killed: bot_hp = 0

					vehicles_dict[vid] = {'health': bot_hp, 'credits': 0, 'xp': 0, 'shots': 0, 'hits': 0, 'he_hits': 0, 'pierced': 0, 'damageDealt': 0, 'damageAssisted': 0, 'damageReceived': getattr(td, 'maxHealth', 1000) - bot_hp, 'shotsReceived': 0, 'spotted': 0, 'damaged': 0, 'kills': 0, 'tdamageDealt': 0, 'tkills': 0, 'isTeamKiller': False, 'capturePoints': 0, 'droppedCapturePoints': 0, 'mileage': 10, 'lifeTime': 300, 'killerID': v_id if is_killed else 0, 'achievements': [], 'repair': 0, 'freeXP': 0, 'details': {}, 'accountDBID': bot_dbid, 'team': bot_team, 'typeCompDescr': bot_cd, 'gold': 0}

				mock_res = {
					'arenaUniqueID': mock_arena_id,
					'personal': {'health': p_health, 'credits': 10000, 'xp': 1000, 'shots': 10, 'hits': 8, 'he_hits': 0, 'pierced': 8, 'damageDealt': 0, 'damageAssisted': 0, 'damageReceived': 0, 'shotsReceived': 0, 'spotted': 0, 'damaged': 0, 'kills': 0, 'tdamageDealt': 0, 'tkills': 0, 'isTeamKiller': False, 'capturePoints': 0, 'droppedCapturePoints': 0, 'mileage': 100, 'lifeTime': 300, 'killerID': _p_killer_id, 'achievements': [], 'repair': 0, 'freeXP': 50, 'details': {}, 'accountDBID': p_dbid, 'team': p_team, 'typeCompDescr': p_cd, 'gold': 0, 'xpPenalty': 0, 'creditsPenalty': 0, 'creditsContributionIn': 0, 'creditsContributionOut': 0, 'tmenXP': 0, 'eventCredits': 0, 'eventGold': 0, 'eventXP': 0, 'eventFreeXP': 0, 'eventTMenXP': 0, 'autoRepairCost': 0, 'autoLoadCost': (0, 0), 'autoEquipCost': (0, 0), 'isPremium': True, 'premiumXPFactor10': 15, 'premiumCreditsFactor10': 15, 'dailyXPFactor10': 10, 'aogasFactor10': 10, 'markOfMastery': 0, 'dossierPopUps': []},
					'common': {'arenaTypeID': getattr(player.arena, 'arenaTypeID', 1), 'arenaCreateTime': __import__('time').time(), 'winnerTeam': winnerTeam, 'finishReason': 1, 'duration': 300, 'bonusType': 1, 'guiType': 1, 'vehLockMode': 0},
					'players': players_dict,
					'vehicles': vehicles_dict
				}



				try:
					from gui import WindowsManager
					if hasattr(WindowsManager.g_windowsManager, 'showBattleResults'):
						WindowsManager.g_windowsManager.showBattleResults(mock_arena_id)
				except: pass

			except Exception as e:
				import traceback
				import gui.mods.offhangar.logging as __offlog
				__offlog.LOG_DEBUG('CRITICAL ERROR IN TRIGGER BATTLE RESULTS:', e)
				__offlog.LOG_DEBUG(traceback.format_exc())

			# Now clean up and leave arena!
			# Restore original stats object which was replaced with FakeStats
			if hasattr(player, '_offhangar_orig_stats') and player._offhangar_orig_stats is not None:
				player.stats = player._offhangar_orig_stats

			_leaveArena()
			player.onBecomeNonPlayer()

			# HACK: Because we triggered onBecomeNonPlayer manually but never call
			# onBecomePlayer to avoid crashing the offline mock state, we must manually
			# re-bind the requester modules and un-ignore them!
			for helper in ('syncData', 'inventory', 'stats', 'trader', 'shop', 'dossierCache', 'battleResultsCache', 'questProgress'):
				h = getattr(player, helper, None)
				if hasattr(h, 'setAccount'):
					try: h.setAccount(player)
					except: pass
				if hasattr(h, 'onAccountBecomePlayer'):
					try: h.onAccountBecomePlayer()
					except: pass

		try:
			from gui.mods.offhangar.network_battle import install_network_hud_metrics
			install_network_hud_metrics()
		except Exception:
			pass

		def _offh_finish_battle(winner, reason, from_network=False, base_team=0):
			'''End the battle through the one flow that is known to work: force the
			outcome, switch the arena to AFTERBATTLE, then replay a K keypress after the
			5 s window - the same route base capture already takes.'''
			import BigWorld
			if globals().get('g_offh_battle_over'):
				return
			try:
				from gui.mods.offhangar._constants import CONFIG_OPTIONS as _RESULT_CFG
				_network_result = bool(_RESULT_CFG.get('network_mode', False)) and not getattr(BigWorld.player(), '_offhangar_network_fallback_local', False)
			except Exception:
				_network_result = False
			if _network_result and not from_network:
				if globals().get('g_offh_result_requested'):
					return
				try:
					from gui.mods.offhangar.network_battle import send_authoritative_result
					if send_authoritative_result(BigWorld.player(), winner, reason, base_team):
						globals()['g_offh_result_requested'] = True
				except Exception:
					pass
				return
			globals()['g_offh_battle_over'] = True
			globals()['G_OFFH_FORCED_WINNER'] = winner
			LOG_DEBUG('BATTLE OVER: %s -> winnerTeam=%s' % (reason, winner))
			try:
				BigWorld.player().arena.onPeriodChange(4, BigWorld.serverTime() + 5.0, 5.0, {})
			except Exception:
				pass
			def _end_now():
				try:
					if _exit_done[0]:
						return
					import Keys as _EK
					class _EndKeyEvent(object):
						key = _EK.KEY_K
						def isKeyDown(self): return True
						def isRepeatedEvent(self): return False
						def isShiftDown(self): return False
						def isCtrlDown(self): return False
						def isAltDown(self): return False
					_mock_handleKeyEvent(_EndKeyEvent())
				except Exception:
					import traceback
					LOG_DEBUG('battle end error:', traceback.format_exc())
			_offh_battle_callback(5.0, _end_now)

		def _offh_check_battle_end():
			'''Team wipe and timer expiry. Base capture handles itself further down.'''
			import BigWorld
			if globals().get('g_offh_battle_over'):
				return
			player = BigWorld.player()
			if player is None or getattr(player, 'arena', None) is None:
				return
			try:
				from gui.mods.offhangar._constants import CONFIG_OPTIONS as _END_CFG
				if bool(_END_CFG.get('network_mode', False)) and not getattr(player, '_offhangar_network_fallback_local', False):
					from gui.mods.offhangar.network_battle import network_is_authority
					if not network_is_authority(player):
						return
			except Exception:
				pass
			# Only once the battle proper is running - period 3. Checking during the
			# countdown would call a wipe before the bots have even spawned.
			if getattr(player.arena, 'period', 0) != 3:
				return
			p_team = getattr(player, '_offhangar_team', 1)
			# --- timer expiry -> draw ---
			try:
				_end_t = getattr(player.arena, 'periodEndTime', 0) or 0
				if _end_t and BigWorld.serverTime() >= _end_t:
					_offh_finish_battle(0, 'battle timer expired')
					return
			except Exception:
				pass
			# --- team wipe ---
			try:
				_mv = globals().get('G_MOCK_VEHICLES', {}) or {}
				if not _mv:
					return
				_alive = {1: 0, 2: 0}
				for _vid, _m in _mv.items():
					if (getattr(_m, 'health', 0) or 0) <= 0:
						continue
					_t = getattr(_m, '_bot_team', None)
					if _t is None:
						if _vid == getattr(player, 'playerVehicleID', -1):
							_t = p_team
						else:
							_pi = getattr(_m, 'publicInfo', None)
							_t = _pi.get('team', 2) if _pi else 2
					if _t in _alive:
						_alive[_t] += 1
				# Both sides must have HAD vehicles, or a half-spawned line-up reads as a
				# wipe. Requires the roster to be complete on both sides first.
				if not globals().get('g_offh_roster_ready'):
					if _alive[1] > 0 and _alive[2] > 0:
						globals()['g_offh_roster_ready'] = True
					return
				if _alive[1] <= 0 and _alive[2] <= 0:
					_offh_finish_battle(0, 'both teams wiped out')
				elif _alive[2] <= 0:
					_offh_finish_battle(1, 'team 2 wiped out')
				elif _alive[1] <= 0:
					_offh_finish_battle(2, 'team 1 wiped out')
			except Exception as _bee:
				LOG_DEBUG('battle end check error:', str(_bee))

		def _apply_network_rules(_rules):
			try:
				_pl = BigWorld.player()
				try:
					from gui.mods.offhangar.network_battle import network_is_authority
					if network_is_authority(_pl):
						return True
				except Exception:
					pass
				for _team in (1, 2):
					_raw = (_rules.get('bases') or {}).get(str(_team), {}) or {}
					_points = max(0, min(int(_raw.get('points', 0) or 0), 100))
					_stopped = bool(_raw.get('stopped', False))
					_contributors = {}
					for _vehicle_key, _vehicle_points in (_raw.get('contributors') or {}).items():
						try:
							_vehicle_points = max(0, min(int(_vehicle_points or 0), 100))
						except Exception:
							continue
						if _vehicle_points:
							_contributors[str(_vehicle_key)] = _vehicle_points
					_old = int(g_base_capture[_team].get('points', 0) or 0)
					_old_stopped = bool(g_base_capture[_team].get('stopped', False))
					g_base_capture[_team]['points'] = _points
					g_base_capture[_team]['stopped'] = _stopped
					g_base_capture[_team]['contributors'] = _contributors
					_active_contributors = []
					for _vehicle_key in _raw.get('active_contributors') or ():
						_vehicle_key = str(_vehicle_key)[:64]
						if (_vehicle_key.startswith('human:') or
								_vehicle_key.startswith('bot:')):
							_active_contributors.append(_vehicle_key)
					g_base_capture[_team]['active_contributors'] = sorted(
						set(_active_contributors))[:30]
					g_base_capture[_team]['invaders'] = len(
						g_base_capture[_team]['active_contributors'])
					g_base_capture[_team]['cursor'] = max(
						0, int(_raw.get('cursor', 0) or 0))
					if _pl is not None and (_old != _points or _old_stopped != _stopped):
						_pl.arena.onTeamBasePointsUpdate(_team, 0, _points, _stopped)
				return True
			except Exception as _nre:
				LOG_DEBUG('LAN rules UI apply error:', str(_nre))
				return False

		def _prepare_native_authority_streaming():
			"""Hold a promoted authority behind live collision coverage."""
			try:
				_pl = BigWorld.player()
				if _pl is None or _offh_network_bot_role(_pl) != 'handoff':
					return False
				_bootstrap = getattr(
					_pl, '_offh_spawn_streaming_bootstrap', None)
				if _bootstrap is None:
					_manifest = list(getattr(
						_pl, '_offhangar_network_bot_manifest', None) or ())
					from gui.mods.offhangar.network_battle import (
						_world_from_server, _world_yaw_from_server)
					_jobs = []
					for _entry in _manifest:
						_point = _world_from_server(_pl, _entry)
						_jobs.append((
							int(_entry.get('team', 0) or 0),
							int(_entry.get('slot', 0) or 0),
							int(_entry.get('id')),
							float(_point.x), float(_point.y), float(_point.z),
							float(_world_yaw_from_server(_pl, _entry)),
							str(_entry.get('vehicle')), str(_entry.get('name'))))

					# A full handoff has already committed relay poses to the local
					# mocks. Probe those current alive poses rather than their original
					# spawn points when the complete runtime index is available.
					_mock_source = globals().get('G_MOCK_VEHICLES')
					if isinstance(_mock_source, dict) and _mock_source:
						_mock_by_bot = {}
						for _mock in _mock_source.values():
							_bot_id = getattr(_mock, '_network_bot_id', None)
							if _bot_id is not None:
								_mock_by_bot[int(_bot_id)] = _mock
						_current_jobs = []
						for _job in _jobs:
							_mock = _mock_by_bot.get(int(_job[2]))
							if _mock is None:
								return False
							if not bool(getattr(_mock, 'isAlive', True)):
								continue
							_pos = getattr(_mock, 'position', None)
							if _pos is None:
								return False
							_current_jobs.append((
								_job[0], _job[1], _job[2], float(_pos.x),
								float(_pos.y), float(_pos.z),
								float(getattr(_mock, 'yaw', _job[6]) or 0.0),
								_job[7], _job[8]))
						_jobs = _current_jobs

					from gui.mods.offhangar.spawn_streaming_bootstrap import (
						SpawnStreamingBootstrap, coverage_target_from_bounds)
					_graph = globals().get('g_offh_baked_navigation_graph')
					_coverage_target = None
					if isinstance(_graph, dict):
						_coverage_target = coverage_target_from_bounds(
							_graph.get('bounds'))
					def _probe_handoff_support(_probe_job):
						_hit = BigWorld.wg_collideSegment(
							_offh_bspace(),
							Math.Vector3(float(_probe_job[3]),
								float(_probe_job[4]) + 3.0,
								float(_probe_job[5])),
							Math.Vector3(float(_probe_job[3]),
								float(_probe_job[4]) - 12.0,
								float(_probe_job[5])), 128)
						return None if _hit is None else float(_hit[0].y)
					_origin = getattr(_pl, 'position', None)
					if _origin is None:
						_origin = (veh_pos[0], veh_pos[1], veh_pos[2])
					_bootstrap = SpawnStreamingBootstrap(
						BigWorld.projection(), _jobs, _origin,
						_probe_handoff_support, time.time(), 30.0,
						height_tolerance=3.0,
						coverage_target=_coverage_target)
					_pl._offh_spawn_streaming_bootstrap = _bootstrap
				_phase = _bootstrap.poll(time.time(), 0)
				return _phase in ('placement_ready', 'complete')
			except Exception as _streaming_error:
				LOG_ERROR('Native authority streaming gate failed: %s' %
					str(_streaming_error))
				return False

		def _apply_network_result(_result):
			try:
				_base_team = int(_result.get('base_team', 0) or 0)
				if _base_team in (1, 2):
					BigWorld.player().arena.onTeamBaseCaptured(1, _base_team)
				_offh_finish_battle(int(_result.get('winner', 0) or 0),
					str(_result.get('reason') or 'battle finished'), True, _base_team)
			except Exception as _nbe:
				LOG_DEBUG('LAN battle result apply error:', str(_nbe))

		try:
			BigWorld.player()._offhangar_apply_network_rules_state = _apply_network_rules
			BigWorld.player()._offhangar_apply_network_battle_result = _apply_network_result
			BigWorld.player()._offhangar_prepare_native_authority_streaming = _prepare_native_authority_streaming
			BigWorld.player()._offhangar_network_result_applied = False
		except Exception:
			pass

		def _capture_tick():
			try:
				if _battle_finished[0]: return
				import BigWorld
				player = BigWorld.player()
				if player is None or _battle_finished[0]:
					return
				if globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]:
					return  # a newer battle owns the globals - stop this stale loop
				if getattr(player, 'arena', None) is not None:
					_offh_seen_arena[0] = True
				elif _offh_seen_arena[0]:
					return  # battle left (hangar) - stop, let the battle graph free
				# The battle GUI dies on EVERY exit path (ESC quit has no hook of
				# its own). Once it existed and is gone: clean up NOW and stop -
				# ticking into the teardown/hangar load crashed the client, and
				# the leaked battle OOM-crashed the hangar load itself.
				try:
					from gui import WindowsManager as _gwm
					_bwref = getattr(_gwm.g_windowsManager, 'battleWindow', None)
				except Exception:
					_bwref = None
				if _bwref is not None:
					_offh_seen_bw[0] = True
				elif _offh_seen_bw[0]:
					_leaveArena()
					return

				# Period progression is local presentation, but battle rules below are
				# calculated only by the elected LAN authority.
				if getattr(player.arena, 'period', 0) == 2 and BigWorld.serverTime() >= getattr(player.arena, 'periodEndTime', 0):
					_remaining = _offh_server_battle_remaining(player, 900.0)
					player.arena.period = 3
					player.arena.periodLength = _remaining
					player.arena.periodEndTime = BigWorld.serverTime() + _remaining
					player.arena.onPeriodChange(3, player.arena.periodEndTime, _remaining, {})
					try:
						from gui.mods.offhangar import battle_feedback as _offh_feedback_live
						_offh_feedback_live.mark_started(
							_offh_stats_for(player), BigWorld.time())
					except Exception:
						pass
				try:
					from gui.mods.offhangar._constants import CONFIG_OPTIONS as _CAP_CFG
					if bool(_CAP_CFG.get('network_mode', False)) and not getattr(player, '_offhangar_network_fallback_local', False):
						from gui.mods.offhangar.network_battle import network_is_authority
						if not network_is_authority(player):
							return
				except Exception:
					pass

				# Get alive vehicles per team
				vehs_by_team = {1: [], 2: []}
				_mock_vehicles = globals().get('G_MOCK_VEHICLES', {})
				# The player has no isVehicleAlive offline (the account object
				# always answered True, so a DEAD player kept capturing); the
				# player's mock carries the real health.
				_pm = _mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
				_player_team = int(getattr(player, '_offhangar_team', 1) or 1)
				if _pm is None or getattr(_pm, 'health', 1) > 0:
					if _player_team in vehs_by_team:
						vehs_by_team[_player_team].append(player)

				def _capture_xz(entity):
					# The offline player is an Account with a separately simulated
					# vehicle.  Its native Account position is not authoritative; use the
					# same canonical mock pose consumed by combat and networking.
					if entity is player:
						position = getattr(_pm, 'position', None)
						if position is None:
							return (float(veh_pos[0]), float(veh_pos[2]))
					else:
						position = getattr(entity, 'position', None)
					if position is None:
						return None
					return (float(position.x), float(position.z))

				for e_mock in _mock_vehicles.values():
					if e_mock is _pm:
						continue
					# Bots carry _bot_team/isAlive; the old check probed the
					# nonexistent _team (mock __getattr__ -> None), so no bot was
					# ever counted and bases could only be captured by the player.
					_bt = getattr(e_mock, '_bot_team', None)
					if _bt in vehs_by_team and getattr(e_mock, 'isAlive', False):
						vehs_by_team[_bt].append(e_mock)

				# Check base distances
				for base_team, bases in g_offline_bases.items():
					if not bases: continue

					invading_team = 2 if base_team == 1 else 1

					invader_keys = []
					_player_capture_key = _offh_capture_vehicle_key(player, player)
					for invader in vehs_by_team[invading_team]:
						_invader_xz = _capture_xz(invader)
						if _invader_xz is None:
							continue
						for base_pos in bases:
							inv_x, inv_z = _invader_xz
							dx = inv_x - base_pos.x
							dz = inv_z - base_pos.z
							if dx*dx + dz*dz <= 2500.0: # 50m radius
								_invader_key = _offh_capture_vehicle_key(invader, player)
								if _invader_key is not None:
									invader_keys.append(_invader_key)
								break
					invaders_count = len(invader_keys)

					defenders_count = 0
					for defender in vehs_by_team[base_team]:
						_defender_xz = _capture_xz(defender)
						if _defender_xz is None:
							continue
						for base_pos in bases:
							def_x, def_z = _defender_xz
							dx = def_x - base_pos.x
							dz = def_z - base_pos.z
							if dx*dx + dz*dz <= 2500.0:
								defenders_count += 1
								break

					state = g_base_capture[base_team]
					old_points = int(state.get('points', 0) or 0)
					old_stopped = bool(state.get('stopped', False))
					if base_team != _player_team:
						_player_xz = _capture_xz(player)
						if _player_xz is not None:
							_nearest_sq = min(((_player_xz[0] - _bp.x) ** 2 +
							                   (_player_xz[1] - _bp.z) ** 2)
							                  for _bp in bases)
							_diag_key = (_offh_my_gen[0], int(base_team))
							_diag_seen = globals().setdefault(
								'g_offh_capture_near_logged', set())
							if _nearest_sq <= 4900.0 and _diag_key not in _diag_seen:
								_diag_seen.add(_diag_key)
								try:
									from gui.mods.offhangar.logging import LOG_NOTE as _CAPTURE_NOTE
									_CAPTURE_NOTE('LAN capture check base_team=%d player_team=%d distance=%.1fm invaders=%d defenders=%d' % (
										base_team, _player_team, _nearest_sq ** 0.5,
										invaders_count, defenders_count))
								except Exception:
									pass

					# Handle transition from PREBATTLE to BATTLE
					if getattr(player.arena, 'period', 0) == 2 and BigWorld.serverTime() >= getattr(player.arena, 'periodEndTime', 0):
						import gui.mods.offhangar.logging as __offlog
						__offlog.LOG_DEBUG('LOUD: TRANSITION TO BATTLE PERIOD')
						_remaining = _offh_server_battle_remaining(player, 900.0)
						player.arena.period = 3
						player.arena.periodLength = _remaining
						player.arena.periodEndTime = BigWorld.serverTime() + _remaining
						player.arena.onPeriodChange(3, player.arena.periodEndTime, _remaining, {}) # dict, not int: UI handlers call has_key() on it


					import debug_utils
					if state['points'] != old_points or invaders_count > 0:
						debug_utils.LOG_DEBUG('Capture tick: team', base_team, 'invaders:', invaders_count, 'defenders:', defenders_count, 'points:', state['points'], 'serverTime:', BigWorld.serverTime())

					from gui.mods.offhangar import capture_rules as _capture_rules_tick
					_capture_result = _capture_rules_tick.advance(
						state, invader_keys, defenders_count > 0)
					_player_gain = int(_capture_result.get('gained', {}).get(
						_player_capture_key, 0) or 0)
					if _player_gain > 0:
						try:
							from gui.mods.offhangar import battle_feedback as _offh_feedback_capture
							_offh_feedback_capture.record_capture(
								_offh_stats_for(player), _player_gain)
						except Exception:
							pass

					# Removed old hack

					if (state['points'] != old_points or
							bool(state.get('stopped', False)) != old_stopped or
							invaders_count > 0):
						import gui.mods.offhangar.logging as __offlog
						__offlog.LOG_DEBUG('LOUD: PERIOD:', getattr(player.arena, 'period', None), 'SERVERTIME:', BigWorld.serverTime(), 'PERIODENDTIME:', getattr(player.arena, 'periodEndTime', None))
						__offlog.LOG_DEBUG('Capture UI updating points! base:', base_team, 'points:', state['points'], 'invaders:', invaders_count)
						try:
							import gui.Scaleform.Battle
							if not hasattr(gui.Scaleform.Battle.TeamBasesPanel, '_patched_update'):
								orig = gui.Scaleform.Battle.TeamBasesPanel._TeamBasesPanel__onTeamBasePointsUpdate
								def _hook(self, team, baseID, points, capturingStopped):
									import gui.mods.offhangar.logging as __offlog
									__offlog.LOG_DEBUG('LOUD: UI HOOK! team', team, 'base', baseID, 'pts', points, 'stop', capturingStopped)
									try:
										orig(self, team, baseID, points, capturingStopped)
										__offlog.LOG_DEBUG('LOUD: UI HOOK orig executed successfully!')
									except Exception as e:
										__offlog.LOG_DEBUG('LOUD: UI HOOK EXCEPTION:', e)
								gui.Scaleform.Battle.TeamBasesPanel._TeamBasesPanel__onTeamBasePointsUpdate = _hook
								gui.Scaleform.Battle.TeamBasesPanel._patched_update = True
						except Exception as e:
							__offlog.LOG_DEBUG('LOUD: UI HOOK INIT ERROR:', e)
						try:
							player.arena.onTeamBasePointsUpdate(
								base_team, 0, state['points'],
								bool(state.get('stopped', False)))
						except Exception as e:
							__offlog.LOG_DEBUG('LOUD: Capture UI Error:', e)

					if state['points'] >= 100 and not globals().get('g_offh_capture_won'):
						globals()['g_offh_capture_won'] = True
						try:
							from gui.mods.offhangar.network_battle import send_authoritative_rules
							send_authoritative_rules(player, g_base_capture)
						except Exception:
							pass
						_offh_finish_battle(3 - base_team, 'base captured', False, base_team)

				try:
					from gui.mods.offhangar.network_battle import send_authoritative_rules
					send_authoritative_rules(player, g_base_capture)
				except Exception:
					pass
				# Team wipe / timer expiry, checked on the same 1 s cadence.
				_offh_check_battle_end()
			except Exception as e:
				# Capture is authoritative state. A release-build LOG_DEBUG is silent,
				# which previously made a broken tick look like a valid 0-point base.
				if globals().get('g_offh_capture_error_gen') != _offh_my_gen[0]:
					globals()['g_offh_capture_error_gen'] = _offh_my_gen[0]
					try:
						import traceback
						from gui.mods.offhangar.logging import LOG_ERROR as _CAPTURE_ERROR
						_CAPTURE_ERROR('LAN capture tick failed: %s\n%s' % (
							str(e), traceback.format_exc()))
					except Exception:
						pass
			finally:
				# Reschedule ONLY while this battle is alive and owns the globals;
				# unconditional rescheduling kept whole old battles in memory.
				try:
					import BigWorld as _cbw
					_cpl = _cbw.player()
					_cok = (not _battle_finished[0]) and globals().get('g_offh_battle_gen', 0) == _offh_my_gen[0]
					if _cok and _offh_seen_arena[0] and (_cpl is None or getattr(_cpl, 'arena', None) is None):
						_cok = False
					if _cok:
						globals()['g_offh_capture_callback_id'] = _cbw.callback(
							1.0, _capture_tick)
				except Exception:
					pass

		g_capture_tick_ref = _capture_tick
		globals()['g_offh_capture_callback_id'] = BigWorld.callback(
			5.0, _capture_tick)

		global g_aih_tick_ref
		def _aih_tick():
			# A cancelled callback can already be executing. Reject stale work before
			# touching BigWorld.player(), and never let its exception path resurrect
			# this zero-delay loop after the battle generation changes.
			if (_battle_finished[0] or
					globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]):
				return
			try:
				import BigWorld, Math, Keys, math
				player = BigWorld.player()

				# Stop the loop if battle is over
				if _battle_finished[0] or player is None:
					return
				# Stale-loop guard: each battle start bumps the generation. A stale
				# per-frame loop pins its whole battle (models/mocks) in the 32-bit
				# client - three battles piled up = OOM crash while loading #3.
				if globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]:
					return
				if getattr(player, 'arena', None) is not None:
					_offh_seen_arena[0] = True
				elif _offh_seen_arena[0]:
					return  # back in the hangar - stop and release the battle
				# The battle GUI dies on EVERY exit path (ESC quit has no hook of
				# its own). Once it existed and is gone: clean up NOW and stop -
				# ticking into the teardown/hangar load crashed the client, and
				# the leaked battle OOM-crashed the hangar load itself.
				try:
					from gui import WindowsManager as _gwm
					_bwref = getattr(_gwm.g_windowsManager, 'battleWindow', None)
				except Exception:
					_bwref = None
				if _bwref is not None:
					_offh_seen_bw[0] = True
				elif _offh_seen_bw[0]:
					_leaveArena()
					return
				current_time = BigWorld.time()
				dt = current_time - _last_tick_time[0]
				_last_tick_time[0] = current_time
				# full_space_release: pin the render camera to the dedicated
				# battle space each frame (camera-mode switches recreate cameras
				# that would revert rendering to the empty account space -> black).
				if globals().get('g_offh_full_release', False):
					_offh_set_render_space(_offh_bspace())
				if dt <= 0.0 or dt > 0.5:
					dt = 0.016 # fallback to 60fps
				_frame_dt = dt # real per-frame delta (dt is reused by the bot section below)
				_perf_frame_started = _offh_perf_frame_begin(len(mock_vehicles or {}))
				_perf_player_loop = _offh_perf_start()
				_perf_player_setup = _offh_perf_start()
				_tank_pair_seen.clear()
				_tank_pair_pending.clear()

				# --- One-time spawn correction once the terrain has streamed in ---
				# The initial spawn runs before the space is loaded (all ground rays
				# miss -> y=100 fallback, sometimes onto/inside buildings). As soon
				# as the ground answers, snap the player onto formation slot 0 at his
				# team base, facing the enemy base - the original line-up position.
				if not getattr(player, '_offh_spawn_fixed', False):
					try:
						_bases_fix = globals().get('g_offline_bases', {}) or {}
						_my_bl = _bases_fix.get(getattr(player, '_offhangar_team', 1) or 1) or []
						_my_b = _my_bl[0] if _my_bl else None
						if _my_b is not None:
							_slot = 0
							try:
								from gui.mods.offhangar._constants import CONFIG_OPTIONS as _NET_SLOT_CFG
								if bool(_NET_SLOT_CFG.get('network_mode', False)):
									_slot = int(getattr(player, '_offhangar_network_slot', 0) or 0)
							except Exception:
								_slot = 0
							_fp = globals().get('g_offline_formation_pose')
							if _fp is not None:
								_sx, _expected_y, _sz, _syaw = _fp(
									getattr(player, '_offhangar_team', 1) or 1, _slot)
							else:
								_sx, _expected_y, _sz, _syaw = (
									_my_b.x, None, _my_b.z, 0.0)
							_gy = None
							if _expected_y is not None:
								# The stock graph owns the terrain layer. Wait until that
								# exact layer is streamed; never re-project from a roof above.
								_c1 = BigWorld.wg_collideSegment(
									_offh_bspace(),
									Math.Vector3(_sx, float(_expected_y) + 3.0, _sz),
									Math.Vector3(_sx, float(_expected_y) - 3.0, _sz), 128)
								if (_c1 is not None and
										abs(float(_c1[0].y) - float(_expected_y)) <= 0.35):
									_gy = float(_c1[0].y)
							else:
								# Compatibility path for custom maps without a baked pose.
								_c1 = BigWorld.wg_collideSegment(
									_offh_bspace(), Math.Vector3(_sx, 800.0, _sz),
									Math.Vector3(_sx, -500.0, _sz), 128)
								if _c1 is not None:
									_gy = float(_c1[0].y)
							if _gy is not None:
								player._offh_spawn_fixed = True
								veh_pos[0] = _sx
								veh_pos[1] = _gy
								veh_pos[2] = _sz
								veh_yaw[0] = _syaw
								_veh_velocity[0] = 0.0
								_veh_turn_velocity[0] = 0.0
								_veh_vert_vel[0] = 0.0
								_veh_airborne[0] = False
								_veh_fall_armed[0] = False  # teleported: next touchdown is free
								LOG_DEBUG('OfflineBattle: spawn corrected to line-up slot:', _sx, _gy, _sz)
					except Exception as _sce:
						LOG_DEBUG('Spawn correction error:', str(_sce))

				import debug_utils
				if not hasattr(player, '_debug_dump_done_6'):
					player._debug_dump_done_6 = True
					debug_utils.LOG_DEBUG('AIH_TICK DUMP AT START!')
					_mock_vehicles = globals().get('G_MOCK_VEHICLES', {})
					debug_utils.LOG_DEBUG('AIH_TICK keys:', _mock_vehicles.keys())

				def _get_terrain_ypr(spaceID, pos, yaw, length=5.0, width=3.0,
						support=None, support_span=None):
					import math, BigWorld, Math
					cos_y = math.cos(yaw)
					sin_y = math.sin(yaw)

					hl = length / 2.0
					hw = width / 2.0

					# 4 body na podvozku
					fx = pos.x + sin_y * hl
					fz = pos.z + cos_y * hl
					bx = pos.x - sin_y * hl
					bz = pos.z - cos_y * hl

					rx = pos.x + cos_y * hw
					rz = pos.z - sin_y * hw
					lx = pos.x - cos_y * hw
					lz = pos.z + sin_y * hw

					def get_y(x, z):
						try:
							_offh_perf_count('physics_rays')
							# Ray from well above: the old +1.5 start sat BELOW steep uphill
							# ground so the hull stayed flat. Accept ground within a hull-height
							# window; reject walls/roofs far above and holes/cliffs far below.
							c = BigWorld.wg_collideSegment(spaceID, Math.Vector3(x, pos.y + 8.0, z), Math.Vector3(x, pos.y - 30.0, z), 128)
							if c is not None and -14.0 < (c[0].y - pos.y) < 6.0:
								return c[0].y
						except: pass
						return pos.y

					# Terrain support already sampled the same fore/aft footprint this
					# frame. Reuse those exact hits for pitch and spend engine rays only
					# on the left/right footprint. Invalid or differently scaled support
					# fails closed to the original four-ray path.
					fy = None
					by = None
					_fore_span = float(length)
					try:
						_sf = support[2]
						_sb = support[3]
						_ss = max(0.5, float(support_span))
						if (_sf is not None and _sb is not None and
								-14.0 < (_sf - pos.y) < 6.0 and
								-14.0 < (_sb - pos.y) < 6.0):
							fy = _sf
							by = _sb
							_fore_span = _ss * 2.0
							_offh_perf_count('tilt_support_reuse')
					except Exception:
						pass
					if fy is None or by is None:
						fy = get_y(fx, fz)
						by = get_y(bx, bz)
						_fore_span = float(length)
					ry = get_y(rx, rz)
					ly = get_y(lx, lz)

					pitch = -math.atan2(fy - by, _fore_span)
					roll = math.atan2(ry - ly, width)

					# Suspension + tip guard. The hull tilts toward the true downhill but
					# the TOTAL lean is capped as one magnitude - clamping pitch and roll
					# INDEPENDENTLY let a diagonal slope combine them into a ~44 deg
					# tip-over (tank looked laid on its side). A single magnitude clamp
					# keeps the tip DIRECTION honest and caps how far it leans, so the hull
					# lies flush on real 30-35 deg slopes without floating a side, yet never
					# tips over on a steep diagonal. Light damp mimics suspension give; the
					# per-tick blend (dt*8) already absorbs 1-frame spikes.
					pitch *= 0.9
					roll *= 0.9
					_tilt = math.sqrt(pitch * pitch + roll * roll)
					_max_tilt = 0.61                       # ~35 deg total hull lean
					if _tilt > _max_tilt:
						_s = _max_tilt / _tilt
						pitch *= _s
						roll *= _s

					# --- Slope gradient: unit downhill dir + magnitude (caller integrates slide) ---
					slide_x = 0.0
					slide_z = 0.0
					slope = 0.0
					try:
						grad_f = (by - fy) / _fore_span   # + = downhill toward hull front
						grad_l = (ly - ry) / width    # + = downhill toward hull right
						slope = math.sqrt(grad_f * grad_f + grad_l * grad_l)
						if slope > 0.001:
							dh_x = grad_f * sin_y + grad_l * cos_y
							dh_z = grad_f * cos_y - grad_l * sin_y
							dl = math.sqrt(dh_x * dh_x + dh_z * dh_z)
							if dl > 0.001:
								slide_x = dh_x / dl
								slide_z = dh_z / dl
					except: pass
					# telemetry: height spread of the 4 footprint samples = how edgy/uneven
					# the ground under the hull is (a sharp edge/step reads high here).
					_spread = max(fy, by, ry, ly) - min(fy, by, ry, ly)
					return (yaw, pitch, roll, slide_x, slide_z, slope, _spread)

				def _terrain_support(spaceID, px, py, pz, yaw, hl=2.5,
						maximum_y=None):
					# Returns (supportMax, centreY, frontY, backY):
					#   supportMax = HIGHEST ground under the fore-aft track footprint
					#     (front/centre/back). A grounded tracked hull rests on the
					#     highest ground it touches, belly hanging - use this for the
					#     rest height so climbing a bank and cresting a ridge stay smooth
					#     (nose does not clip in, hull does not dive early).
					#   centreY = ground directly under the hull centre - the centre of
					#     mass. Drives the airborne trigger and the landing height: once
					#     the CoM clears the ledge the hull tips and FALLS, even if the
					#     tail still overhangs the crest (supportMax would hang it there).
					# Either is None when that probe finds no ground (map edge / void).
					import BigWorld, Math
					_sy = math.sin(yaw); _cy = math.cos(yaw)
					best = None
					centre = None
					front = None
					back = None
					for _d in (hl, 0.0, -hl):
						_x = px + _sy * _d
						_z = pz + _cy * _d
						_ray_start = Math.Vector3(_x, py + 2.0, _z)
						_ray_end = Math.Vector3(_x, py - 1000.0, _z)
						_yv = None
						for _layer in range(4):
							_offh_perf_count('physics_rays')
							try:
								_c = BigWorld.wg_collideSegment(
									spaceID, _ray_start, _ray_end, 128)
							except Exception:
								_c = None
							if _c is None:
								break
							_yv = _c[0].y
							_destroyed = False
							_above_limit = False
							_ground_facing = False
							try:
								_ground_facing = float(_c[1].y) > 0.5
							except Exception:
								_ground_facing = False
							try:
								_above_limit = (maximum_y is not None and
									float(_yv) > float(maximum_y))
							except Exception:
								_above_limit = False
							try:
								_mi = _offh_mat_info_for_segment_hit(
									spaceID, _c[0], _c[1])
								if _mi is not None:
									_destroyed = bool(_get_destr_authority().is_destroyed(
										_mi[2], _mi[3], _mi[4]))
							except Exception:
								_destroyed = False
							if (not _destroyed and not _above_limit and
									_ground_facing):
								break
							# Retail keeps a destroyed object's old BSP skin for about
							# 0.2 s. A vertical ray can likewise hit an intact wagon or
							# low roof above the maximum climbable support. Downward or
							# vertical faces are not ground either. Continue below every
							# rejected layer instead of inventing support.
							_next_y = float(_yv) - 0.05
							_yv = None
							if _next_y <= _ray_end.y + 0.01:
								_yv = None
								break
							_ray_start = Math.Vector3(_x, _next_y, _z)
						if _yv is not None:
							if best is None or _yv > best:
								best = _yv
							if _d == 0.0:
								centre = _yv
							elif _d > 0.0:
								front = _yv
							else:
								back = _yv
					return (best, centre, front, back)

				def _try_destroy_destructible(crush_vehicle, spaceID, matInfo, yaw, vel):
					import AreaDestructibles, BigWorld, constants
					try:
						if not hasattr(AreaDestructibles, 'g_destructiblesManager') or not AreaDestructibles.g_destructiblesManager:
							return False

						hitPt, surfNormal, chunkID, itemIndex, matKind, fname = matInfo
						_dseen = globals().setdefault('g_offh_destr_seen', set())
						_dkey = (matKind, fname)
						if _dkey not in _dseen:
							_dseen.add(_dkey); LOG_DEBUG('Destr hit: matKind=', matKind, 'fname=', repr(fname), 'chunk=', chunkID, 'idx=', itemIndex)
						# Widened band: the strict 71-100 range rejected spawn barriers/props at
						# matKind 102. getDescByFilename below is the real filter, so a wider band
						# only lets more candidates reach the authoritative desc check.
						if matKind < 71 or matKind > 130:
							return False
						desc = AreaDestructibles.g_cache.getDescByFilename(fname)
						if not desc:
							_dnd = globals().setdefault('g_offh_destr_nodesc', set())
							if _dkey not in _dnd:
								_dnd.add(_dkey); LOG_DEBUG('Destr no desc: matKind=', matKind, 'fname=', repr(fname), 'chunk=', chunkID, 'idx=', itemIndex)
							return False

						# Data-driven vegetation gate: soft vegetation (bush/shrub/fern)
						# ships with health <= 5; real fallable trees start at 10.
						if desc['type'] in (AreaDestructibles.DESTR_TYPE_TREE, AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
							_hp_gate = desc.get('health', 0)
							if _hp_gate < 10 or _hp_gate > 1000:
								return False
						# All bookkeeping (chunk bootstrap, dedup, encoding) lives in
						# the authority - this path is now just a contact sensor.
						_auth = _get_destr_authority()

						typ = desc['type']
						# STRUCTURE (buildings) now falls through to the module-destroy
						# path: online, small buildings crumble module by module as the
						# tank pushes through. Requires the working effects pipeline
						# (terrainEffects + real fake_model), else it raises mid-destroy.
						if _auth.is_destroyed(chunkID, itemIndex, matKind):
							LOG_DEBUG('Destr: already broken')
							return True
						if not _auth.can_crush(
								crush_vehicle, spaceID, chunkID, itemIndex, matKind,
								fname, vel):
							return False

						if typ == AreaDestructibles.DESTR_TYPE_TREE:
							_destr_ok = _auth.destroy_tree(spaceID, chunkID, itemIndex, yaw, vel, hitPt)
						elif typ == AreaDestructibles.DESTR_TYPE_FALLING_ATOM:
							_destr_ok = _auth.destroy_column(spaceID, chunkID, itemIndex, yaw, vel, hitPt)
						elif typ == AreaDestructibles.DESTR_TYPE_FRAGILE:
							_destr_ok = _auth.destroy_fragile(spaceID, chunkID, itemIndex, hitPt)
						else:
							# STRUCTURE: buildings crumble module by module
							_destr_ok = _auth.destroy_module(spaceID, chunkID, itemIndex, matKind, hitPt, False)

						if _destr_ok:
							LOG_DEBUG('Destr SUCCESS!', typ)
						return bool(_destr_ok)
					except Exception as e:
						LOG_DEBUG('Destr Exception:', str(e))
					return False

				# The destructible effect pipeline (fall dust, decay effects) calls
				# player.terrainEffects.addNew(); only the real battle Avatar has it.
				# Without it __launchFallEffect raises and trees never start falling.
				try:
					from helpers import bound_effects
					if getattr(player, 'terrainEffects', None) is None:
						player.terrainEffects = bound_effects.StaticSceneBoundEffects()
					# Effects attach to player.newFakeModel(); the offline stub
					# returned BigWorld.Model('') and Model.node() rejects blank
					# models. Use the real fake model like Avatar does.
					def _offh_new_fake_model():
						try:
							return BigWorld.Model('objects/fake_model.model')
						except Exception:
							return BigWorld.Model('')
					player.newFakeModel = _offh_new_fake_model
				except Exception:
					LOG_CURRENT_EXCEPTION()

				# Export for _mock_shoot (different function scope); resolved at call time
				loaded_models['_destr_fn'] = _try_destroy_destructible

				def _fell_trees_near(crush_vehicle, spaceID, pos, yaw, vel, td=None):
					# Offline tree/pole felling. Online the SERVER detected tank-vs-tree
					# contact; the client-side collision probes never return tree/column
					# materials, so trees could never fall offline. Instead: enumerate
					# each chunk's destructibles once (filename + world matrix), then
					# fell TREE / FALLING_ATOM items that intersect the moving hull.
					import math
					import AreaDestructibles
					try:
						if abs(vel) < 1.0:
							return
						mgr = getattr(AreaDestructibles, 'g_destructiblesManager', None)
						if not mgr:
							return
						if mgr.getSpaceID() is None:
							mgr.startSpace(spaceID)
						_st = globals().setdefault('g_offh_tree_state', {'chunks': {}, 'felled': set(), 'spaceID': None})
						if _st.get('spaceID') != spaceID:
							# New battle/space: chunk IDs collide between maps and the
							# dedup sets would suppress destruction of fresh objects.
							_st['chunks'] = {}
							_st['felled'] = set()
							_st['spaceID'] = spaceID
							globals()['g_offh_destr_ordered'] = set()
							globals()['g_offh_destr_chunks'] = set()
							globals()['g_offh_destr_seen'] = set()
						cos_y = math.cos(yaw); sin_y = math.sin(yaw)
						cids = set()
						for _pf in (0.0, 6.0 if vel >= 0 else -6.0):
							try:
								cids.add(AreaDestructibles.chunkIDFromPosition(Math.Vector3(pos.x + sin_y * _pf, pos.y, pos.z + cos_y * _pf)))
							except Exception:
								pass
						hw = 1.6; hl_f = 3.6; hl_b = 3.6
						try:
							if td is not None and hasattr(td, 'hull') and 'hitTester' in td.hull:
								bbox = td.hull['hitTester'].bbox
								hw = max(abs(bbox[0][0]), abs(bbox[1][0]))
								hl_b = abs(bbox[0][2])
								hl_f = abs(bbox[1][2])
						except Exception:
							pass
						for cid in cids:
							trees = _st['chunks'].get(cid)
							if trees is None:
								_dfn = None
								try:
									_dfn = BigWorld.wg_getChunkDestrFilenames(spaceID, cid)
								except Exception:
									pass
								if _dfn is None:
									continue # chunk not streamed in yet; retry next tick
								trees = []
								_cm_t = None
								try:
									_cm_t = BigWorld.wg_getChunkMatrix(spaceID, cid).translation
								except Exception:
									pass
								if _cm_t is None:
									continue
								for _ti in xrange(len(_dfn)):
									try:
										desc = AreaDestructibles.g_cache.getDescByFilename(_dfn[_ti])
										if desc is None:
											continue
										if desc['type'] not in (AreaDestructibles.DESTR_TYPE_TREE, AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
											continue
										# Data-driven vegetation gate: destructibles.xml gives
										# soft vegetation (bushes/shrubs/ferns/weeds) health<=5
										# (or -2); real fallable trees start at health 10.
										# ChristmasTree sentinels use 40000 = unrammable.
										_hp_gate = desc.get('health', 0)
										if _hp_gate < 10 or _hp_gate > 1000:
											continue
										# Destructible matrices are CHUNK-LOCAL: world pos =
										# chunk translation + destructible translation
										# (see AreaDestructibles.__launchEffect)
										_m = Math.Matrix(BigWorld.wg_getDestructibleMatrix(spaceID, cid, _ti))
										trees.append((_ti, _cm_t.x + _m.translation.x, _cm_t.z + _m.translation.z, desc['type'], _dfn[_ti], desc.get('health', 0), desc.get('mass', 0)))
									except Exception:
										continue
								_st['chunks'][cid] = trees
								LOG_DEBUG('DestrTree: chunk registry', cid, len(trees), 'trees/poles')
								if trees:
									LOG_DEBUG('DestrTree: sample world pos', trees[0][1], trees[0][2], 'tank at', pos.x, pos.z)
							if not trees:
								continue
							reach_f = hl_f + 0.8 + min(abs(vel) * 0.25, 1.2)
							for (_ti, _tx, _tz, _ttyp, _tfn, _thp, _tmass) in trees:
								dx = _tx - pos.x; dz = _tz - pos.z
								if dx * dx + dz * dz > 64.0:
									continue
								fwd = dx * sin_y + dz * cos_y
								lat = dx * cos_y - dz * sin_y
								if vel < 0:
									in_reach = -(hl_b + 0.8) <= fwd <= hl_f
								else:
									in_reach = -hl_b <= fwd <= reach_f
								if abs(lat) > hw + 0.5 or not in_reach:
									continue
								_key = (cid, _ti)
								if _key in _st['felled']:
									continue
								fall_yaw = yaw if vel >= 0 else (yaw + math.pi)
								_auth = _get_destr_authority()
								if not _auth.can_crush(
										crush_vehicle, spaceID, cid, _ti, 0, _tfn, vel):
									continue
								if _ttyp == AreaDestructibles.DESTR_TYPE_TREE:
									_ok = _auth.destroy_tree(spaceID, cid, _ti, fall_yaw, vel, pos)
								else:
									_ok = _auth.destroy_column(spaceID, cid, _ti, fall_yaw, vel, pos)
								if _ok:
									_st['felled'].add(_key)
									LOG_DEBUG('DestrTree: FELLED', cid, _ti, 'type', _ttyp, 'hp', _thp, 'mass', _tmass, _tfn)
					except Exception:
						import traceback
						LOG_DEBUG('DestrTree error:', traceback.format_exc())

				def _try_destroy_solid_hit(crush_vehicle, spaceID, hit_pt, surface_normal, yaw, vel):
					# wg_collideSegment returns no material info: probe the hit point for a
					# destructible along the authored contact normal before treating it solid.
					try:
						_mi = _offh_mat_info_for_segment_hit(
							spaceID, hit_pt, surface_normal)
						if _mi is not None:
							return _try_destroy_destructible(
								crush_vehicle, spaceID, _mi, yaw, vel)
					except Exception:
						pass
					return False

				def _collision_damage(victim, dmg, attacker_id):
					# Ported ram-damage sink: HP, damage panel / marker feedback, kill.
					if dmg <= 0 or victim is None:
						return
					try:
						if getattr(victim, 'health', 0) <= 0:
							return
						_hp_before_collision = max(0, int(getattr(victim, 'health', 0) or 0))
						victim.health = max(0, _hp_before_collision - int(dmg))
						if victim.health < _hp_before_collision:
							_offh_drop_capture_for_vehicle(
								victim, attacker_id, 'collision damage')
						victim.last_killer_id = attacker_id
						v_id = getattr(victim, 'id', -1)
						is_player_victim = (v_id == getattr(player, 'playerVehicleID', -1))
						if is_player_victim:
							try:
								if hasattr(player, 'vehicle') and player.vehicle:
									player.vehicle.health = victim.health
							except Exception:
								pass
							try:
								from gui import WindowsManager as _rwm
								bw = getattr(_rwm.g_windowsManager, 'battleWindow', None)
								if bw and hasattr(bw, 'damagePanel'):
									bw.damagePanel.updateHealth(victim.health)
							except Exception:
								pass
						else:
							try:
								if hasattr(player.arena, 'onVehicleStatisticsUpdate'):
									player.arena.onVehicleStatisticsUpdate(v_id)
								from gui import WindowsManager as _rwm
								bw = getattr(_rwm.g_windowsManager, 'battleWindow', None)
								if bw and hasattr(bw, 'vMarkersManager'):
									marker = getattr(victim, 'marker', None)
									if marker is not None:
										# max(0, ...) is not cosmetic. VehicleMarkersManager.swf, VehicleMarker.updateHealth
										# (curHealth, flag, damageType) starts with:  if (curHealth < 0) damageType = 'explosion'
										# and VehicleMarkerFlags.ALLOW_ATTACK_REASONS = ['fire','explosion'] is exactly the set
										# that makes hitExplosion.setFlag() draw the red blow-up symbol next to the damage
										# number. A killing shot drives health negative, so EVERY kill lit that icon up. Retail
										# never sends a negative value here.
										bw.vMarkersManager.onVehicleHealthChanged(marker, max(0, victim.health), attacker_id, 0)
										try:
											bw.vMarkersManager.showVehicleDamageInfo(marker, dmg, 0, 0, 0)
										except Exception:
											pass
							except Exception:
								pass
						if victim.health <= 0 and not is_player_victim:
							_offh_set_alive(victim, False)
							try:
								if v_id in player.arena.vehicles:
									player.arena.vehicles[v_id]['isAlive'] = False
								if hasattr(player.arena, 'onVehicleKilled'):
									player.arena.onVehicleKilled(v_id, attacker_id, 3)  # reason 3 = ram (wrapper swaps wreck)
							except Exception:
								pass
					except Exception as e:
						LOG_DEBUG('Collision damage error:', str(e))

				def _tank_resolve(self_id, x, z, yaw, td, inv_self, svx, svz, y=None):
					# Chassis OBB contact + inelastic impulse. Retail 0.8.2 sizes the
					# rigid body from chassis['hitTester'], while the old reconstruction
					# used the narrower hull and a chain of circles. That let tracks and
					# corners visibly overlap. Each unordered pair is solved once per
					# frame; the reciprocal response is queued for the other local body.
					import BigWorld, math
					_mv = globals().get('G_MOCK_VEHICLES', {}) or {}
					_plobj = BigWorld.player()
					_pid = getattr(_plobj, 'playerVehicleID', -1)
					_pending = _tank_pair_pending.pop(self_id, None)
					if _pending is None:
						corr_x = 0.0; corr_z = 0.0; dvx = 0.0; dvz = 0.0
					else:
						corr_x, corr_z, dvx, dvz = _pending
					_collision_bodies = _collision_frame[0] or {}
					_my_body = _collision_bodies.get(self_id)
					if _my_body is not None:
						my_shape = _my_body['shape']
						my_radius = _my_body['radius']
					else:
						my_shape = _VC.chassis_shape(td)
						my_radius = math.sqrt(
							my_shape[0] * my_shape[0] + my_shape[1] * my_shape[1])
					_candidate_ids = None
					_candidate_map = _collision_candidates[0]
					if _candidate_map is not None:
						_candidate_ids = _candidate_map.get(self_id)
					if _candidate_ids is None:
						_candidate_ids = (_VC.nearby_ids(
							_collision_spatial[0], x, z)
							if _collision_spatial[0] else _mv.keys())
					_offh_perf_count('collision_candidates', len(_candidate_ids))
					for oid in _candidate_ids:
						ov = _mv.get(oid)
						if oid == self_id or ov is None:
							continue
						_pair = (min(self_id, oid), max(self_id, oid))
						if _pair in _tank_pair_seen:
							continue
						# Each unordered broad-phase pair needs at most one narrow-phase
						# test per frame. A non-overlap used to be tested again when the
						# other bot reached its loop iteration, nearly doubling SAT work in
						# dense formations. A pair that closes later is caught next frame.
						_tank_pair_seen[_pair] = True
						_o_body = _collision_bodies.get(oid)
						if oid == _pid:
							ox = veh_pos[0]; oz = veh_pos[2]; oyaw = veh_yaw[0]; otd = loaded_models.get('td'); mass_o = max(_phys_mass, 1.0); inv_o = 1.0 / mass_o
							oy = veh_pos[1]
							ovx = math.sin(oyaw) * _veh_velocity[0] + (getattr(_plobj, '_push_x', 0.0) or 0.0)
							ovz = math.cos(oyaw) * _veh_velocity[0] + (getattr(_plobj, '_push_z', 0.0) or 0.0)
						else:
							op = getattr(ov, 'position', None)
							if op is None:
								continue
							ox = op.x; oz = op.z; oyaw = getattr(ov, 'yaw', 0.0); otd = getattr(ov, 'typeDescriptor', None)
							if _o_body is not None:
								inv_o = _o_body['inv_mass']
								mass_o = 1.0 / max(inv_o, 1e-09)
							else:
								_oparams = getattr(ov, '_phys_params', None)
								if _oparams is None:
									_oparams = _PHY.derive_params(otd)
									ov._phys_params = _oparams
								mass_o = max(float(_oparams.get('mass', 25000.0)), 1.0)
								inv_o = 1.0 / mass_o
							oy = op.y
							_ovv = getattr(ov, '_veh_velocity', 0.0) or 0.0
							ovx = math.sin(oyaw) * _ovv + (getattr(ov, '_push_x', 0.0) or 0.0)
							ovz = math.cos(oyaw) * _ovv + (getattr(ov, '_push_z', 0.0) or 0.0)
						# Network replicas are observations, not locally simulated bodies.
						# Correct the local vehicle against them without moving a snapshot
						# that the smoother would immediately put back (visible jitter).
						if getattr(ov, '_network_remote', False):
							inv_o = 0.0
						elif getattr(ov, '_network_shared_bot', False):
							try:
								from gui.mods.offhangar.network_battle import network_is_authority
								if not network_is_authority(_plobj):
									inv_o = 0.0
							except Exception:
								inv_o = 0.0
						if (_o_body is not None and
								_o_body.get('native_owner', False)):
							# Python collision cannot teleport an attached WGVehiclePhysics2
							# body. Resolve the complete hybrid contact on the Python-owned
							# participant instead of queuing a reciprocal fake correction.
							inv_o = 0.0
						o_shape = (_o_body['shape'] if _o_body is not None else
						           _VC.chassis_shape(otd))
						if not _VC.vertical_overlap(y, my_shape, oy, o_shape):
							continue
						dcx = x - ox; dcz = z - oz
						o_radius = (_o_body['radius'] if _o_body is not None else
						            math.sqrt(o_shape[0] * o_shape[0] +
						                      o_shape[1] * o_shape[1]))
						_max_dist = my_radius + o_radius + 0.25
						if dcx * dcx + dcz * dcz > _max_dist * _max_dist:
							continue
						_contact = _VC.obb_contact(x, z, yaw, my_shape, ox, oz, oyaw, o_shape)
						if _contact is None:
							continue
						_response = _VC.pair_response(
							_contact, inv_self, inv_o, (svx, svz), (ovx, ovz))
						corr_x += _response[0]; corr_z += _response[1]
						dvx += _response[2]; dvz += _response[3]
						if inv_o > 0.0:
							_old_pending = _tank_pair_pending.get(oid)
							if _old_pending is None:
								_tank_pair_pending[oid] = _response[4:8]
							else:
								_tank_pair_pending[oid] = tuple(
									_old_pending[_pi] + _response[4 + _pi] for _pi in range(4))
						_vn = ((svx - ovx) * _contact[0] +
						       (svz - ovz) * _contact[1])
						if _vn < 0.0:
							# Ram damage (ported): approach speed beyond 3.5 m/s hurts both hulls
							if _vn < -3.5:
								_now = BigWorld.time()
								_rcd = globals().setdefault('g_offh_ram_cd', {})
								_rkey = (min(self_id, oid), max(self_id, oid))
								if _now - _rcd.get(_rkey, 0.0) > 0.75:
									_rcd[_rkey] = _now
									_rel = -_vn
									# physics.ram_damage uses real descriptor masses even when the
									# other body is a locally static network snapshot.
									_dmo, _dms = _PHY.ram_damage(_rel, 1.0 / max(inv_self, 1e-09), mass_o)
									_rsv = _mv.get(self_id)
									if _dmo > 0:
										_collision_damage(ov, _dmo, self_id)
									if _dms > 0 and _rsv is not None:
										_collision_damage(_rsv, _dms, oid)
									LOG_DEBUG('RAM:', self_id, '<->', oid, 'rel=%.1f' % _rel, 'dmg', _dmo, _dms)
					return (corr_x, corr_z, dvx, dvz)

				def _support_drive_pitch(y, support, half_span):
					# Convert terrain_support's fore/aft samples to the exact pitch law
					# used by _drive_pitch. None means the support cannot safely replace
					# the original bridge-aware probes.
					import math
					try:
						fy = support[2]
						by = support[3]
						L = max(0.5, float(half_span))
					except Exception:
						return None
					if fy is None or by is None:
						return None
					_WALL_RISE = L * 1.43
					_fd = fy - y
					if _fd > _WALL_RISE: _fd = _WALL_RISE
					elif _fd < -_WALL_RISE: _fd = -_WALL_RISE
					_bd = by - y
					if _bd > _WALL_RISE: _bd = _WALL_RISE
					elif _bd < -_WALL_RISE: _bd = -_WALL_RISE
					p = -math.atan2(_fd - _bd, 2.0 * L)
					if p > 0.96: p = 0.96
					if p < -0.96: p = -0.96
					return p

				def _drive_pitch(spaceID, x, z, yaw, y):
					# Fore/aft GROUND slope under the hull (nose-up = negative, BigWorld
					# convention) for the drive/slide physics. Sampled close to the hull
					# (L = track half-length) so a WALL a few metres ahead is not read as
					# a 69 deg 'slope' that injects phantom gravity. A sample that rises
					# more than a drivable step over L is a wall/cliff face, not ground -
					# it is clamped to the drivable ceiling (the collision code, not
					# gravity, handles walls). Final pitch clamped to +/-40 deg: steeper
					# than that no tank drives, so it must never drive the engine/hold maths.
					import math, BigWorld, Math
					fx = math.sin(yaw); fz = math.cos(yaw)
					L = 2.0
					def _gy(px, pz):
						# Skip geometry ABOVE the hull. The probe starts 15 m up so an uphill sample
						# ahead is still caught, but that also made it hit a BRIDGE DECK when driving
						# underneath: front sample = bridge, back sample = road, which reads as a
						# near-vertical rise, gets clamped to the wall ceiling and cuts the engine -
						# the tank simply stopped at the underpass. Anything more than 3.5 m above the
						# hull cannot be ground we drive on (the drivable band over L is 2.86 m), so
						# drop below it and probe again.
						_from = y + 15.0
						for _ in range(4):
							try:
								_offh_perf_count('physics_rays')
								c = BigWorld.wg_collideSegment(spaceID, Math.Vector3(px, _from, pz), Math.Vector3(px, y - 60.0, pz), 128)
							except:
								return None
							if c is None:
								return None
							_yv = c[0].y
							if _yv > y + 3.5:
								_from = _yv - 0.5
								continue
							# A low fence, pole or tree in front of the hull is an obstacle,
							# not the terrain grade.  Continue below its authored BSP skin;
							# the horizontal sweep remains responsible for the retail crush
							# decision, so this never makes an intact object passable.
							try:
								_mat_info = _offh_mat_info_for_segment_hit(
									spaceID, c[0], c[1])
								if _offh_destructible_mat_passable(_mat_info):
									_from = _yv - 0.05
									continue
							except Exception:
								pass
							return _yv
						return None
					fy = _gy(x + fx * L, z + fz * L)
					by = _gy(x - fx * L, z - fz * L)
					p = _support_drive_pitch(y, (None, None, fy, by), L)
					return 0.0 if p is None else p

				def _check_horizontal_collision(crush_vehicle, spaceID, pos, yaw, vel,
						td=None, airborne=False, dt=0.04, sweep_direction=None):
					import math, BigWorld, Math
					try:
						_direction = vel if sweep_direction is None else sweep_direction
						_forward = _direction > 0.0
						hw = 1.5
						hl_front = 3.5
						hl_back = 3.5

						if td and hasattr(td, 'hull') and 'hitTester' in td.hull:
							try:
								bbox = td.hull['hitTester'].bbox
								hw = max(abs(bbox[0][0]), abs(bbox[1][0])) - 0.1
								hl_back = abs(bbox[0][2])
								hl_front = abs(bbox[1][2])
							except: pass

						# Look-ahead beyond the hull. The old flat +2.0 m made an invisible
						# wall 2 m before every obstacle, and DURING A FALL it saw the cliff
						# face below-ahead and zeroed the speed mid-air - the tank then hugged
						# the wall and trickled down instead of flying a ballistic arc.
						# Grounded: just enough to not tunnel at speed. Airborne: only the
						# distance actually travelled this tick - contact stops, proximity not.
						# Sweep through the complete movement integrated this frame.  A fixed
						# 1.2 m cap was only safe at high FPS and could tunnel through thin
						# geometry after a slow frame.
						_ahead = max(0.4, abs(vel) * max(0.0, dt) + 0.2)
						back_margin = -0.5 if _forward else 0.5
						front_margin = (hl_front + _ahead) if _forward else -(hl_back + _ahead)

						cos_y = math.cos(yaw)
						sin_y = math.sin(yaw)

						# Always retain the exact lower-hull swept test. It spans the previous
						# pose to the complete candidate motion, so reducing expensive follow-up
						# work cannot let a fast hull tunnel through a thin wall.
						_bottom_hits = []
						_target_len = (abs(back_margin) +
							(hl_front if _forward else hl_back) + _ahead)
						for offset_x in (-hw, 0, hw):
							sx = pos.x + cos_y * offset_x
							sz = pos.z - sin_y * offset_x

							x1 = sx + sin_y * back_margin
							z1 = sz + cos_y * back_margin
							x2 = sx + sin_y * front_margin
							z2 = sz + cos_y * front_margin

							start_bot = Math.Vector3(x1, pos.y + 0.6, z1)
							end_bot = Math.Vector3(x2, pos.y + 0.6, z2)
							_offh_perf_count('physics_rays')
							col_bot = BigWorld.wg_collideSegment(spaceID, start_bot, end_bot, 128)
							if col_bot is not None:
								d_bot = (col_bot[0] - start_bot).length
								if d_bot < _target_len:
									_bottom_hits.append((
										offset_x, sx, sz, x2, z2, start_bot, end_bot,
										col_bot, d_bot))

						if not _bottom_hits:
							_offh_perf_count('wall_fast')
							return False
						_offh_perf_count('wall_exact')

						# A lower sweep also intersects ordinary slopes and rounded crests.
						# Use the stock 0.8.2 terrain callback for this exception. Re-probing an
						# upward contact vertically is not enough: an inclined rock simply hits
						# itself again and can self-certify as ground. Flat ground is not a terrain
						# profile here and must never hide a genuine wall at the same height.
						try:
							def _terrain_triangle(_mat_kind, _coll_flags,
									_item_id, _chunk_id):
								return _coll_flags & 8

							def _terrain_hit(_segment_start, _segment_end):
								_offh_perf_count('physics_rays')
								return BigWorld.wg_collideSegment(
									spaceID, _segment_start, _segment_end, 128,
									_terrain_triangle)

							def _contact_matches_ground(
									_segment_start, _segment_end, _contact):
								try:
									_normal_y = float(_contact[1].y)
									if _normal_y <= 0.5:
										return False, None, None, False, False, False

									def _terrain_profile_overlimit(_terrain_contact):
										# Match the seven-sample profile's longitudinal grade.
										# A steep cross-slope is handled by the existing lateral
										# slide law and must not become an invisible forward wall.
										_terrain_normal_y = float(_terrain_contact[1].y)
										if _terrain_normal_y <= 0.5:
											return True
										_normal_along_sweep = abs(
											float(_terrain_contact[1].x) * sin_y +
											float(_terrain_contact[1].z) * cos_y)
										return (_normal_along_sweep >
											_VC.TERRAIN_PROFILE_MAXIMUM_GRADIENT *
											_terrain_normal_y)
									# Stock TargetPointCalculator uses mask 136 for the
									# non-terrain subset of the same mask-128 segment.
									# This catches a coincident sloped rock and a thin wall
									# immediately behind terrain before the 3 cm rescan step.
									_offh_perf_count('physics_rays')
									_solid = BigWorld.wg_collideSegment(
										spaceID, _segment_start, _segment_end, 136)
									if _solid is None:
										# Mask 136 is the complete non-terrain subset of this
										# remaining segment. No solid can be hidden behind the
										# first terrain triangle, so repeated mask-128 terrain
										# contacts must not consume the bounded solid budget.
										_overlimit_terrain = _terrain_profile_overlimit(_contact)
										return (not _overlimit_terrain, None, None, True,
											True, _overlimit_terrain)
									_contact_distance = (
										_contact[0] - _segment_start).length
									_solid_distance = (
										_solid[0] - _segment_start).length
									if _solid_distance > _contact_distance + 0.05:
										_overlimit_terrain = _terrain_profile_overlimit(_contact)
										return (True, None, None, True, False,
											_overlimit_terrain)
									# Near/coincident upward geometry is ambiguous from its
									# normal alone. Query the stock terrain subset explicitly:
									# a prop mounted on steep terrain must not erase that slope
									# proof after its own crush delivery succeeds.
									_terrain = _terrain_hit(_segment_start, _segment_end)
									if _terrain is None:
										return (False, _solid, _solid_distance, False,
											False, False)
									_terrain_distance = (
										_terrain[0] - _segment_start).length
									_overlimit_terrain = _terrain_profile_overlimit(_terrain)
									if _solid_distance > _terrain_distance + 0.03:
										return (True, None, None, True, False,
											_overlimit_terrain)
									return (False, _solid, _solid_distance, True,
										False, _overlimit_terrain)
								except Exception:
									raise

							# Walk every occupied lower lane to its exact endpoint. A
							# successful crush only clears that one contact; it must not
							# hide a second intact object farther inside the same movement
							# sweep. Six contacts is a bounded fail-closed budget.
							_saw_terrain_contact = False
							_saw_overlimit_terrain = False
							_terrain_profile_lanes = []
							for _bottom_hit in _bottom_hits:
								(offset_x, sx, sz, x2, z2, start_bot, end_bot,
									col_bot, d_bot) = _bottom_hit
								_span_x = end_bot.x - start_bot.x
								_span_z = end_bot.z - start_bot.z
								_span_len = math.sqrt(
									_span_x * _span_x + _span_z * _span_z)
								_scan_start = start_bot
								_contact = col_bot
								_scan_finished = False
								_lane_saw_terrain = False
								for _scan_index in range(6):
									(_matches_ground, _near_solid,
										_near_solid_distance,
										_contact_saw_terrain,
										_remaining_nonterrain_clear,
										_contact_overlimit_terrain) = _contact_matches_ground(
										_scan_start, end_bot, _contact)
									_progress_hit = _contact
									if _contact_saw_terrain:
										_saw_terrain_contact = True
										_lane_saw_terrain = True
									if _contact_overlimit_terrain:
										_saw_overlimit_terrain = True
									if _remaining_nonterrain_clear:
										_scan_finished = True
										break
									if _matches_ground:
										pass
									else:
										if _near_solid is not None:
											_progress_hit = _near_solid
										if not _try_destroy_solid_hit(
												crush_vehicle, spaceID, _progress_hit[0],
												_progress_hit[1], yaw, vel):
											return True
									_contact_distance = (
										_progress_hit[0] - start_bot).length
									# Terrain triangles need a small step past the crossing to
									# avoid returning the same surface. After an accepted solid,
									# use only a 1 mm numeric epsilon so a second object mounted
									# immediately behind a fragile fence cannot be skipped.
									_advance = 0.03 if _matches_ground else 0.001
									_next_distance = _contact_distance + _advance
									if _next_distance >= _span_len - 0.001:
										_scan_finished = True
										break
									_ratio = _next_distance / _span_len
									_scan_start = Math.Vector3(
										start_bot.x + _span_x * _ratio,
										start_bot.y,
										start_bot.z + _span_z * _ratio)
									_offh_perf_count('physics_rays')
									_contact = BigWorld.wg_collideSegment(
										spaceID, _scan_start, end_bot, 128)
									if _contact is None:
										_scan_finished = True
										break
									if ((_contact[0] - start_bot).length + 0.001 <
											_next_distance):
										return True
								if not _scan_finished:
									return True
								if _lane_saw_terrain:
									_terrain_profile_lanes.append((sx, sz))
							if _saw_overlimit_terrain:
								return True
							if not _saw_terrain_contact:
								return False
							_fw = 1.0 if _forward else -1.0
							_look = (hl_front if _forward else hl_back) + _ahead
							_seg_n = 6
							_seg = _look / _seg_n
							# Profile the exact lane(s) whose lower sweep proved terrain.
							# Sampling the chassis centre line here made a shallow cross-slope
							# feel like a wall whenever the centre happened to cross a narrow
							# seam, and could also hide a step seen only by one track.
							for _lane_sx, _lane_sz in _terrain_profile_lanes:
								_ground_heights = []
								for _si in range(_seg_n + 1):
									_dd = _seg * _si
									_px = _lane_sx + sin_y * _dd * _fw
									_pz = _lane_sz + cos_y * _dd * _fw
									_gg = _terrain_hit(
										Math.Vector3(_px, pos.y + 12.0, _pz),
										Math.Vector3(_px, pos.y - 5.0, _pz))
									if _gg is None:
										_ground_heights = []
										break
									_ground_heights.append(_gg[0].y)
								if (not _ground_heights or
										not _VC.drivable_rising_profile(
											_ground_heights, _seg, allow_flat=True)):
									return True
							return False
						except Exception:
							# Once an exact lower contact exists, any uncertainty in
							# terrain classification must stay solid. Falling back to
							# the older top/mid heuristic can hide a wall behind a slope.
							return True

					except Exception:
						# Collision uncertainty must stay solid. Returning False here used
						# to turn any engine/material error into a wall pass-through.
						return True
					return False

				def _offh_land_impact(vy):
					# 0.8.x landing: fall damage from the COMBINED slam speed (vertical
					# fall + carried lateral drift - a hull that slid sideways off a
					# slope hits harder). The residual lateral becomes ground-slide speed
					# so the tank skids on after touchdown, then the air-lateral clears.
					try:
						_alx = getattr(player, '_air_lat_vx', 0.0) or 0.0
						_alz = getattr(player, '_air_lat_vz', 0.0) or 0.0
						_lat = math.sqrt(_alx * _alx + _alz * _alz)
						if _lat > 0.01:
							player._slide_spd = max(getattr(player, '_slide_spd', 0.0) or 0.0, _lat)
						player._air_lat_vx = 0.0
						player._air_lat_vz = 0.0
						if getattr(mock_veh, 'health', 0) <= 0:
							return
						_iv = math.sqrt(vy * vy + _lat * _lat)   # combined slam speed
						# physics.fall_damage: free below ~4 m-equivalent, then linear
						_dmg = _PHY.fall_damage(getattr(mock_veh, 'maxHealth', 400), _iv)
						if _dmg <= 0:
							return
						mock_veh.health -= _dmg
						LOG_DEBUG('OfflineBattle: landing impact %.1f m/s (lat %.1f) -> %d damage' % (_iv, _lat, _dmg))
						try:
							import gui.WindowsManager
							_bwli = gui.WindowsManager.g_windowsManager.battleWindow
							if _bwli and hasattr(_bwli, 'damagePanel'):
								_bwli.damagePanel.updateHealth(max(0, mock_veh.health))
						except Exception:
							pass
						if mock_veh.health <= 0:
							mock_veh.health = 0
							mock_veh.last_killer_id = -1
							try:
								player.arena.onVehicleKilled(getattr(mock_veh, 'id', player.playerVehicleID), -1, 2)
							except Exception:
								pass
						try:
							if hasattr(player, 'vehicle') and player.vehicle:
								player.vehicle.health = mock_veh.health
								player.guiSessionProvider.invalidateVehicleState(1, player.playerVehicleID, mock_veh.health, mock_veh.health)
						except Exception:
							pass
					except Exception:
						pass

				if not _engine_state['init']:
					try:
						td = loaded_models.get('td')
						root_model = loaded_models.get('chassis') or loaded_models.get('hull') or loaded_models.get('turret') or loaded_models.get('gun')
						engine_dict = getattr(td, 'engine', None)
						chassis_dict = getattr(td, 'chassis', None)
						if td and engine_dict and chassis_dict and root_model is not None and root_model.inWorld:
							_engine_state['snd1'] = root_model.playSound(engine_dict['sound'])
							_engine_state['snd2'] = root_model.playSound(chassis_dict['sound'])
							_engine_state['init'] = True
							# Refs on the mock so _sync_burn_and_death can stop them on death
							mock_veh._snd_engine = _engine_state['snd1']
							mock_veh._snd_tracks = _engine_state['snd2']
							LOG_DEBUG('OfflineBattle: Engine sounds attached!', engine_dict['sound'], chassis_dict['sound'])
					except Exception as e:
						LOG_DEBUG('OfflineBattle: Engine sounds failed:', str(e))

				# --- Track animation (one-time): WGVehicleFashion drives the scrolling
				# track materials and wheels, exactly like the original VehicleAppearance ---
				if not loaded_models.get('_fashion_done'):
					try:
						_f_ch = loaded_models.get('chassis')
						_f_td = loaded_models.get('td')
						if _f_ch is not None and _f_td is not None and getattr(_f_ch, 'inWorld', False):
							loaded_models['_fashion_done'] = True
							_fash = BigWorld.WGVehicleFashion()
							try:
								_fash.maxMovement = _f_td.physics['speedLimits'][0]
							except Exception:
								pass
							# Swinging setup is mandatory: without a swinging node ('V' =
							# vehicle root) the fashion refuses to attach / stays inert
							try:
								_f_sw = _f_td.hull['swinging']
								# VehicleAppearance scales pitchParams by _PITCH_SWINGING_MODIFIERS before
								# handing them over; feeding the raw descriptor values gave the hull a
								# noticeably different pitch response from retail (the 2nd term is x1.88).
								_f_pp = tuple(_p * _m for (_p, _m) in zip(_f_sw['pitchParams'], (0.9, 1.88, 0.3, 4.0, 1.0, 1.0)))
								_fash.setPitchSwinging('V', *_f_pp)
								_fash.setRollSwinging('V', *_f_sw['rollParams'])
								_fash.setShotSwinging('V', _f_sw['sensitivityToImpulse'])
							except Exception as _swe:
								LOG_DEBUG('Fashion swinging setup failed:', str(_swe))
							_f_tr = _f_td.chassis['tracks']
							try:
								_fash.setLods(_f_td.chassis['traces']['lodDist'], _f_td.chassis['wheels']['lodDist'], _f_tr['lodDist'], _f_td.hull['swinging']['lodDist'])
							except Exception:
								pass
							_fash.setTracks(_f_tr['leftMaterial'], _f_tr['rightMaterial'], _f_tr['textureScale'])
							# (setTrackTraces intentionally NOT called - see crash note in the sweep)
							# Road wheels spin with movement: replicate _setupVehicleFashion's
							# addWheelGroup/addWheel. nodes = '<template><i>' from chassis['wheels'].
							try:
								_wcfg = _f_td.chassis['wheels']
								for _grp in _wcfg['groups']:
									_wnodes = ['%s%d' % (_grp[1], _wi) for _wi in range(_grp[3], _grp[3] + _grp[2])]
									_fash.addWheelGroup(_grp[0], _grp[4], _wnodes)
								for _wh in _wcfg['wheels']:
									_fash.addWheel(_wh[0], _wh[2], _wh[1])
								LOG_DEBUG('Fashion road wheels added')
							except Exception as _we:
								LOG_DEBUG('Fashion road wheels failed:', str(_we))
							# EXPERIMENT: real game sources fashion.movementInfo from WGVehicleFilter2;
							# the kinematic mock has none, so attach a settable Vector4 provider and
							# drive it per frame from speed. Log the available classes for diagnosis.
							try:
								LOG_DEBUG('Math Vector4 classes:', [ _n for _n in dir(Math) if 'Vector4' in _n ])
								_fash.movementInfo = Math.Vector4(0.0, 0.0, 0.0, 0.0)
								loaded_models['_fashion_mv'] = _fash
								LOG_DEBUG('Fashion movementInfo set Vector4; readback=', repr(_fash.movementInfo))
							except Exception as _mve:
								LOG_DEBUG('Fashion movementInfo attach failed:', str(_mve))
							_f_ch.wg_fashion = _fash
							# Chassis camo goes through THIS fashion (stashed at model setup);
							# a separate WGBaseFashion would detach the scrolling track material.
							try:
								_ca = loaded_models.get('_camo_args')
								if _ca is not None:
									_fash.setCamouflage(_ca[0], _ca[1], _ca[2], _ca[3], _ca[4], _ca[5], _ca[6], _ca[7])
									LOG_DEBUG('Chassis camo applied via track fashion')
							except Exception as _cae:
								LOG_DEBUG('Chassis camo via fashion failed:', str(_cae))
							loaded_models['_fashion'] = _fash
							# DO NOT point this at the real fashion. Doing so makes _trigger_shot_impulse
							# actually call fashion.receiveShotImpulse(), and the client then died with
							# EXCEPTION_ACCESS_VIOLATION 0xC0000005 Read@0x8 at loadHangarSpaceVehicle -
							# twice, same address. WG feeds a fashion placingCompensationMatrix and
							# physicsInfo from WGVehicleFilter2 (VehicleAppearance._setupVehicleFashion);
							# a mock has no such filter, so the shot-swinging path dereferences null.
							# Hull rocking needs those two set first - until then it stays a no-op.
							LOG_DEBUG('OfflineBattle: track fashion attached (player)')
							try:
								LOG_DEBUG('WGVehicleFashion dir:', [ _n for _n in dir(_fash) if not _n.startswith('__') ])
							except Exception:
								pass
					except Exception as _fe:
						loaded_models['_fashion_done'] = True
						LOG_DEBUG('Track fashion failed:', str(_fe))

				_offh_perf_stop('player_setup', _perf_player_setup)
				_perf_player_physics = _offh_perf_start()
				# --- WoT-style Hull Physics ---
				# Determine input direction
				throttle = 0
				steer = 0

				# Allow WASD to move the tank even in Arty Mode, because offline edge-panning is broken
				# and the user needs to be able to rotate the hull to bring targets into the gun arc!
				if getattr(player, '_is_dead', False) is True:
					throttle = 0
					steer = 0
					if int(_gun_state.get('cruise_mode', 0) or 0) != 0:
						_set_cruise_mode(0)
				else:
					# Honor Controls->Movement rebinds from the settings screen: the
					# raw W/A/S/D polls ignored CommandMapping, so rebinding movement
					# keys saved fine but changed nothing in the offline battle.
					try:
						import CommandMapping as _CMap
						_cmg = _CMap.g_instance
						_k_fwd = _cmg.get('CMD_MOVE_FORWARD') or Keys.KEY_W
						_k_bwd = _cmg.get('CMD_MOVE_BACKWARD') or Keys.KEY_S
						_k_lft = _cmg.get('CMD_ROTATE_LEFT') or Keys.KEY_A
						_k_rgt = _cmg.get('CMD_ROTATE_RIGHT') or Keys.KEY_D
					except Exception:
						_k_fwd, _k_bwd, _k_lft, _k_rgt = Keys.KEY_W, Keys.KEY_S, Keys.KEY_A, Keys.KEY_D
					if _gun_state.get('manual_input_events', False):
						_manual_forward = bool(_gun_state.get('manual_forward_down', False))
						_manual_backward = bool(_gun_state.get('manual_backward_down', False))
						_manual_left = bool(_gun_state.get('manual_left_down', False))
						_manual_right = bool(_gun_state.get('manual_right_down', False))
					else:
						# Compatibility fallback until the first real movement event. This
						# keeps programmatic controls and unusual input wrappers working.
						_manual_forward = bool(BigWorld.isKeyDown(_k_fwd))
						_manual_backward = bool(BigWorld.isKeyDown(_k_bwd))
						_manual_left = bool(BigWorld.isKeyDown(_k_lft))
						_manual_right = bool(BigWorld.isKeyDown(_k_rgt))
					# A manual movement key-down cancels cruise in _handle_cruise_key.
					# Do not clear it again every physics tick: pressing R/F while W/S
					# remains held must arm cruise for the moment the manual key is released.
					if _manual_forward:
						throttle = 1
					elif _manual_backward:
						throttle = -1
					else:
						throttle = {
							1: 0.25, 2: 0.50, 3: 1.0,
							-1: -0.50, -2: -1.0,
						}.get(int(_gun_state.get('cruise_mode', 0) or 0), 0.0)

					if _manual_left: steer = -1
					elif _manual_right: steer = 1

					# Auto-hull rotation if aiming outside limits
					# Only auto-rotate if not manually steering
					if steer == 0:
						steer = _gun_state.get('auto_steer', 0)

				# Freeze tank movement if battle hasn't started yet (Prebattle Countdown)
				arena = getattr(BigWorld.player(), 'arena', None)
				if arena is not None and getattr(arena, 'period', 3) < 3:
					throttle = 0
					steer = 0

				# LAN MVP: the client keeps using the existing movement/aim code for
				# responsive presentation, while the same input is sent to the
				# authoritative server tick for remote replication and server-side
				# coarse fire checks.
				try:
					from gui.mods.offhangar._constants import CONFIG_OPTIONS as _NET_INPUT_CFG
					if bool(_NET_INPUT_CFG.get('network_mode', False)) and not getattr(player, '_offhangar_network_fallback_local', False):
						from gui.mods.offhangar.network_battle import send_local_input
						send_local_input(player, throttle, steer, veh_yaw[0] + turret_yaw[0], gun_pitch[0],
							veh_pos[0], veh_pos[1], veh_pos[2], veh_yaw[0])
				except Exception:
					pass

				cur_vel = _veh_velocity[0]
				# Longitudinal law (engine / rolling resist / brake / slope / clamps):
				# physics.longitudinal_step - the SAME function every bot integrates
				# with, parameterized by this tank's real descriptor values.
				_slope_p = 0.0
				# Probe ground slope on EVERY grounded tick, incl. parked/idle: a hull
				# parked on a slope must feel gravity along the hull (standstill slide),
				# so longitudinal_step needs the real pitch, not 0.0. Old gate on motion
				# fed 0.0 at rest and killed the documented stand-still slide.
				if not _veh_airborne[0]:
					_raw_sp = _drive_pitch(_offh_bspace(), veh_pos[0], veh_pos[2], veh_yaw[0], veh_pos[1])
					# The ground probe throws isolated garbage (LOD seams produced single-frame
					# 78 deg spikes), but the old cure was worse than the disease: rate-limiting
					# the TRACKED pitch to 0.3 * 1.2 * dt rad capped it at 20.6 deg/s at ANY
					# framerate. Driving onto a hill at 13 m/s the physics then needed ~1.5 s to
					# perceive a 30 deg slope - by which time the hull had already covered ~19 m
					# of it. THAT lag, not a weak slip drag, is what let momentum carry a tank up
					# slopes the engine flatly refuses; the slip term was fed a far too shallow
					# grade the whole way. A median rejects the spikes outright (they are isolated
					# by nature, and 3 of 5 samples would have to be bad to shift it) while a
					# genuine slope now registers within a couple of frames.
					_hist = getattr(player, '_offh_pitch_hist', None)
					if _hist is None:
						_hist = [_raw_sp] * 5
						player._offh_pitch_hist = _hist
					_hist.append(_raw_sp)
					del _hist[:-5]
					_med = sorted(_hist)[2]
					_prev_sp = getattr(player, '_offh_smooth_pitch', _med)
					_slope_p = _prev_sp + (_med - _prev_sp) * 0.5
					player._offh_smooth_pitch = _slope_p
					player._offh_last_pitch = _slope_p  # launch seed for ramp jumps (see airborne start)
				else:
					player._offh_smooth_pitch = 0.0
				# Handbrake (SPACE): locks the tracks. Held down it overrides the throttle,
				# so it also works as an emergency stop, not just a parking brake.
				_hb_on = False
				try:
					import Keys as _hbK
					_hb_on = bool(BigWorld.isKeyDown(_hbK.KEY_SPACE)) and not _offh_cursor_shown()
				except Exception:
					_hb_on = False
				player._offh_handbrake = _hb_on
				# Crew + module effects on mobility: a downed driver halves the throttle, and
				# a destroyed engine or track stops the hull outright (the repair tick clears
				# those flags again once the module is functional).
				_p_locked = False
				try:
					_pm_mob = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
					if _pm_mob is not None:
						# A thrown track is a LOCKED track, not a released throttle: cutting the
						# throttle alone left the hull coasting another 10-15 m after it was
						# immobilised. Feed it through the handbrake branch, which is grip-limited
						# like every other brake here, so a cliff still slides the hull off.
						# A dead ENGINE is different and stays a coast - the hull still rolls.
						_p_locked = bool(getattr(_pm_mob, 'is_tracked', False))
						if _p_locked or getattr(_pm_mob, 'is_engine_dead', False):
							throttle = 0
						elif throttle != 0:
							# A downed driver and a DAMAGED engine both cost throttle; a destroyed
							# engine is the hard gate above (that is all avatar.py ever knew about).
							_mf = _crew_factor(_pm_mob, 'mobility') * _module_factor(_pm_mob, 'mobility')
							if _mf < 1.0:
								throttle = throttle * _mf
				except Exception: pass
				_veh_velocity[0] = _PHY.longitudinal_step(_pparams, cur_vel, throttle, steer != 0, _slope_p, dt, _veh_airborne[0], 0, _hb_on or _p_locked)
				# --- offhangar slope diagnostic (physics_debug): steepest grade the PLAYER
				# climbs. Resets on flat so each hill reports its own peak. Drive up Drachenpass
				# then the Serene mountain and read the two 'SLOPE grade=' peaks from the log.
				try:
					from _constants import CONFIG_OPTIONS as _CFG_SLP
					if _CFG_SLP.get('physics_debug', False):
						_sp_deg = math.degrees(abs(_slope_p))
						if _sp_deg < 3.0:
							player._offh_climb_max = 0.0
						elif cur_vel > 0.5 and throttle > 0:
							if _sp_deg > getattr(player, '_offh_climb_max', 0.0) + 0.5:
								player._offh_climb_max = _sp_deg
								LOG_DEBUG('SLOPE grade=%.1f deg %s  v=%.1f m/s' % (_sp_deg, 'UP' if _slope_p < 0 else 'DOWN', cur_vel))
				except Exception:
					pass

				# Track scroll: feed movementInfo from current speed so WGVehicleFashion
				# scrolls the track texture (experimental; real game uses WGVehicleFilter2).
				_mvp = loaded_models.get('_fashion_mv')
				if _mvp is not None:
					try:
						# physics.track_scroll: v -/+ omega*halfGauge, clamped strictly
						# below fashion.maxMovement (native scroll wraps to zero at the
						# exact boundary and the tracks freeze at top speed).
						if (getattr(mock_veh, 'health', 1) or 0) <= 0:
							# Dead: hold the tracks still. movementInfo is a speed the native
							# scroll keeps applying, so the last value would roll forever.
							_tls = _trs = 0.0
						else:
							_tls, _trs = _PHY.track_scroll(_pparams, _veh_velocity[0], _veh_turn_velocity[0])
						_mvp.movementInfo = Math.Vector4(0.0, _tls, _trs, 0.0)
					except Exception:
						pass
				# Update engine sounds
				try:
					cur_speed = abs(_veh_velocity[0])
					max_speed = _phys_speedFwd
					# Continuous load blend: discrete 1/2/3 mode values flip rapidly around
					# their thresholds and retrigger the FMOD engine loop (audible resets).
					power_fraction = min(1.0, (cur_speed / max_speed) + (abs(throttle) * 0.3))
					load = 1.0 + (power_fraction * 2.0)
					if _engine_state['snd1']:
						p = _engine_state.get('p_load')
						if p is None:
							p = _engine_state['snd1'].param('load')  # resolve once, not every frame
							_engine_state['p_load'] = p
						if p: p.value = load
					if _engine_state['snd2']:
						p = _engine_state.get('p_speed')
						if p is None:
							p = _engine_state['snd2'].param('speed')
							_engine_state['p_speed'] = p
						if p: p.value = cur_speed / max_speed
				except:
					pass
				# Apply position.  Preserve a direction-only sweep while drive input is
				# held at zero speed.  A false slope used to zero the speed and then
				# suppress the only exact obstacle probe, permanently pinning the hull.
				# Direction and impact speed are deliberately separate.  Even a tiny
				# invented speed can break an extreme mass-scaled destructible, so the
				# retail crush gate must always receive the exact realised velocity.
				_sweep_direction = _veh_velocity[0]
				if abs(_sweep_direction) <= 0.0001 and abs(throttle) > 0.01:
					_sweep_direction = 1.0 if throttle > 0.0 else -1.0
				if _sweep_direction != 0.0:
					_p_td = loaded_models.get('td')
					_fell_trees_near(mock_veh, _offh_bspace(), Math.Vector3(veh_pos[0], veh_pos[1], veh_pos[2]), veh_yaw[0], _veh_velocity[0], _p_td)
					if _check_horizontal_collision(mock_veh, _offh_bspace(), Math.Vector3(veh_pos[0], veh_pos[1], veh_pos[2]), veh_yaw[0], _veh_velocity[0], _p_td, _veh_airborne[0], dt, _sweep_direction):
						# A WALL only pushes laterally - it must NOT brake a fall. Airborne:
						# keep momentum, just don't advance into it (slide down the face).
						if not _veh_airborne[0]:
							# WALL-SLIDE: instead of dead-sticking (the '2.6 s pinned against
							# a rock until you steer' bug), probe angled directions and grind
							# along the obstacle at reduced speed. Only a true head-on into a
							# flat wall / inside corner (every angle blocked) stops the hull.
							_deflected = False
							for _da in (0.55, -0.55, 1.0, -1.0):   # ~32 then ~57 deg, both sides
								_ty = veh_yaw[0] + _da
								if not _check_horizontal_collision(mock_veh, _offh_bspace(), Math.Vector3(veh_pos[0], veh_pos[1], veh_pos[2]), _ty, _veh_velocity[0], _p_td, False, dt, _sweep_direction):
									# First tick of wall contact loses the into-wall velocity component
									# (~40%); continuing to grind only applies friction. Storing the realized
									# slide speed back into v stops the model booking phantom forward progress
									# while the hull actually moves along _ty (fps-independent).
									if getattr(player, '_offh_grind', 0) <= 0:
										_veh_velocity[0] *= 0.6
									_gs = _veh_velocity[0] * (0.85 ** (dt * 60.0))
									veh_pos[0] += math.sin(_ty) * _gs * dt
									veh_pos[2] += math.cos(_ty) * _gs * dt
									_veh_velocity[0] = _gs
									player._offh_grind = 4   # in-contact latch; survives 1-tick convex-wall separations
									_deflected = True
									break
							if not _deflected:
								# Head-on into a flat wall / inside corner (no angle clears): bleed the
								# speed out over ~0.1 s (fps-independent) instead of a 1-frame hard stop -
								# no velocity discontinuity. The wall already blocks the advance (no
								# veh_pos update here), so the hull holds against it.
								_veh_velocity[0] *= 0.35 ** (dt * 60.0)
								if abs(_veh_velocity[0]) < 0.05:
									_veh_velocity[0] = 0.0
								player._offh_grind = 4
							player._offh_deflected = _deflected
					else:
						player._offh_deflected = False
						player._offh_grind = max(0, getattr(player, '_offh_grind', 0) - 1)
						veh_pos[0] += math.sin(veh_yaw[0]) * _veh_velocity[0] * dt
						veh_pos[2] += math.cos(veh_yaw[0]) * _veh_velocity[0] * dt
				# Tank-vs-tank: velocity-relative impulse (e=0) + Baumgarte push-apart.
				# Never blocks movement -> no deadlock; heavier tank shoves lighter aside.
				try:
					_psvx = math.sin(veh_yaw[0]) * _veh_velocity[0] + (getattr(player, '_push_x', 0.0) or 0.0)
					_psvz = math.cos(veh_yaw[0]) * _veh_velocity[0] + (getattr(player, '_push_z', 0.0) or 0.0)
					_ptr = _tank_resolve(getattr(player, 'playerVehicleID', -1), veh_pos[0], veh_pos[2], veh_yaw[0], loaded_models.get('td'), 1.0 / max(_phys_mass, 1.0), _psvx, _psvz, veh_pos[1])
					# e=0: the FORWARD share of the impulse must hit the real drive
					# speed - a ram stops the hull. Before, it went only into the
					# 0.90-decay push velocity, so the tracks kept feeding into the
					# other tank until the centres crossed and the hull popped out the
					# far side ('glitched through a tank after the cliff drop').
					_fimp = _ptr[2] * math.sin(veh_yaw[0]) + _ptr[3] * math.cos(veh_yaw[0])
					_fabs = 0.0
					if _fimp * _veh_velocity[0] < 0.0:
						_fabs = -_veh_velocity[0] if abs(_fimp) >= abs(_veh_velocity[0]) else _fimp
						_veh_velocity[0] += _fabs
					player._push_x = (getattr(player, '_push_x', 0.0) or 0.0) + _ptr[2] - _fabs * math.sin(veh_yaw[0])
					player._push_z = (getattr(player, '_push_z', 0.0) or 0.0) + _ptr[3] - _fabs * math.cos(veh_yaw[0])
					veh_pos[0] += _ptr[0] + player._push_x * dt
					veh_pos[2] += _ptr[1] + player._push_z * dt
					player._push_x *= 0.90
					player._push_z *= 0.90
				except Exception as _player_collision_error:
					# Player/native contacts are the only collision bridge between the
					# Python-owned player and native bot bodies. Never let this subsystem
					# fail silently: one hidden exception disables vehicle volume for the
					# whole battle. Count sampled failures and report the first one in each
					# battle generation without flooding the 32-bit client log.
					_offh_perf_count('player_collision_error')
					_player_collision_gen = int(
						globals().get('g_offh_battle_gen', 0) or 0)
					if (globals().get('g_offh_player_collision_error_gen') !=
							_player_collision_gen):
						globals()['g_offh_player_collision_error_gen'] = _player_collision_gen
						LOG_ERROR('OfflineBattle player/native collision failed: %s' %
							str(_player_collision_error))

				# --- Hull Rotation: physics.traverse_step (same law as the bots) ---
				turn_dir = steer
				# A destroyed track (or engine) stops the hull turning as well. Gating only the
				# throttle left a tracked tank free to pivot on the spot.
				try:
					_pm_trn = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
					if _pm_trn is not None and (getattr(_pm_trn, 'is_tracked', False) or getattr(_pm_trn, 'is_engine_dead', False)):
						turn_dir = 0
						_veh_turn_velocity[0] = 0.0
				except Exception: pass
				_veh_turn_velocity[0] = _PHY.traverse_step(
					_pparams, _veh_turn_velocity[0], turn_dir,
					_veh_velocity[0], dt, drive_intent=throttle)
				# A damaged (not thrown) track slows the hull traverse. Scaling the rate the
				# step returns caps it at that fraction of the tank's own traverse speed.
				try:
					_ptf = _module_factor(mock_veh, 'traverse')
					if _ptf < 1.0:
						_veh_turn_velocity[0] = _veh_turn_velocity[0] * _ptf
				except Exception: pass

				if _veh_turn_velocity[0] != 0.0:
					veh_yaw[0] += _veh_turn_velocity[0] * dt
					while veh_yaw[0] > math.pi: veh_yaw[0] -= 2*math.pi
					while veh_yaw[0] < -math.pi: veh_yaw[0] += 2*math.pi

				# --- Ground contact: stick to terrain on slopes, fall with gravity off ledges ---
				# Ray starts just above the hull (not +100) so bridges/roofs overhead are ignored
				try:
					# Rest on the HIGHEST ground under the fore-aft track footprint
					# (front/centre/back), not a lone centre ray - couples the vertical
					# follow to the pitch probe so climbs and crests are smooth.
					_hl_sup = 2.5
					try:
						_tdc = loaded_models.get('td')
						if _tdc is not None and hasattr(_tdc, 'hull') and 'hitTester' in _tdc.hull:
							_bbs = _tdc.hull['hitTester'].bbox
							_hl_sup = max(1.5, abs(_bbs[1][2]))
					except Exception:
						pass
					_sup = _terrain_support(_offh_bspace(), veh_pos[0], veh_pos[1], veh_pos[2], veh_yaw[0], _hl_sup)
					_centre_y = _sup[1]     # ground under the hull CENTRE (chassis origin sits here)
					player._y_snap = None   # telemetry: tag which path sets veh_pos.y this tick
					# Rest on the CENTRE ground, not the highest footprint point: the latter
					# lifted the hull by the half-slope rise, so every tank floated. Fall back
					# to the footprint max only when the centre probe missed (centre over a gap).
					ground_y = _centre_y if _centre_y is not None else _sup[0]
					if ground_y is not None:
						snap_gap = max(0.8, min(2.5, abs(_veh_velocity[0]) * dt * 2.0 + 0.6))
						max_climb = max(0.6, abs(_veh_velocity[0]) * dt * 2.5)
						try:
							if _VC.support_rise_is_obstacle(
									veh_pos[1], ground_y, max_climb):
								# The upper hit is an obstacle, not support. Probe below
								# the exact climb limit and preserve traction only when a
								# real lower collision surface proves the floor. A void
								# must still transition into the normal airborne path.
								_max_support_y = (float(veh_pos[1]) +
									min(max(0.0, float(max_climb)), 0.85) + 0.02)
								_sup = _terrain_support(
									_offh_bspace(), veh_pos[0], veh_pos[1], veh_pos[2],
									veh_yaw[0], _hl_sup, maximum_y=_max_support_y)
								_centre_y = _sup[1]
								ground_y = _centre_y if _centre_y is not None else _sup[0]
						except Exception:
							pass
					if ground_y is not None:
						# CoM has left the ground when the CENTRE probe drops away (or finds
						# nothing): THEN the hull tips and falls, even if the tail still
						# overhangs the crest. Landing height is that same centre ground.
						_com_gap = snap_gap if _centre_y is None else (veh_pos[1] - _centre_y)
						_land_y = ground_y if _centre_y is None else _centre_y
						if not _veh_fall_armed[0]:
							# First ground acquisition after spawn: the hull starts at the y=100
							# fallback far above terrain. SNAP straight down instead of a ~100 m
							# ballistic plummet (telemetry reached -46 m/s). Spawn touchdown is
							# free and instant - never a fall.
							player._offh_buried = 0
							veh_pos[1] = _land_y if _land_y is not None else ground_y
							_veh_vert_vel[0] = 0.0
							_veh_airborne[0] = False
							_veh_fall_armed[0] = True
						elif _centre_y is not None and veh_pos[1] < _centre_y and (_centre_y - veh_pos[1]) > max_climb:
							# Buried deeper than any climbable step: a diagonal slip past the
							# wall probes left the hull INSIDE the slope, where it stuck
							# forever. Two consecutive buried ticks = terrain, never a fence
							# (fences fit inside max_climb) -> pop back to the surface.
							player._offh_buried = getattr(player, '_offh_buried', 0) + 1
							if player._offh_buried >= 2 and (_centre_y - veh_pos[1]) > 0.5:
								veh_pos[1] = _centre_y
								player._offh_buried = 0
						elif veh_pos[1] <= ground_y or (_com_gap <= snap_gap and not _veh_airborne[0]):
							player._offh_buried = 0
							# Soft ground-follow: below the surface snaps up hard (never sink,
							# tracks stay planted); above eases down (no teleport) but is capped
							# 0.12 m over ground so the hull never visibly floats on a downhill.
							if veh_pos[1] < ground_y:
								_rise = ground_y - veh_pos[1]
								veh_pos[1] += _rise if _rise <= max_climb else max_climb
							else:
								veh_pos[1] += (ground_y - veh_pos[1]) * min(1.0, dt * 15.0)
								if veh_pos[1] > ground_y + 0.12:
									veh_pos[1] = ground_y + 0.12
							_veh_vert_vel[0] = 0.0
							_veh_airborne[0] = False
							_veh_fall_armed[0] = True
						else:
							# Ledge/cliff: ballistic fall, substepped so a fast drop can't clip/tunnel
							player._offh_buried = 0
							if not _veh_airborne[0]:
								# Launch: leaving a ramp/crest inherits the vertical component
								# of the ground slope (v*sin(-pitch); nose-up pitch is
								# negative). Starting every jump at v_y=0 made ramps feel
								# dead - the hull dropped like a brick the moment the ray
								# lost the ground.
								try:
									# Upward launches only (nose-up pitch, i.e. ramps/crests).
									# Seeding DOWNWARD on steep descents inflated the landing
									# speed and charged phantom fall damage for ordinary
									# downhill driving.
									_lp = getattr(player, '_offh_last_pitch', 0.0) or 0.0
									_veh_vert_vel[0] = _veh_velocity[0] * math.sin(-_lp) if _lp < 0.0 else 0.0
								except Exception:
									_veh_vert_vel[0] = 0.0
								LOG_DEBUG('OfflineBattle: player airborne, %.1fm to ground' % (veh_pos[1] - ground_y))
							_veh_airborne[0] = True
							_fall_n = 1
							if abs(_veh_vert_vel[0] * dt) > 0.5:
								_fall_n = min(8, int(abs(_veh_vert_vel[0] * dt) / 0.5) + 1)
							_fall_sdt = dt / _fall_n
							_fall_i = 0
							while _fall_i < _fall_n:
								_veh_vert_vel[0] -= _phys_gravity * _fall_sdt
								veh_pos[1] += _veh_vert_vel[0] * _fall_sdt
								if _land_y is not None and veh_pos[1] <= _land_y:
									veh_pos[1] = _land_y
									# Fall damage only once armed (first spawn touchdown is
									# free) and only in the running battle - never during the
									# countdown while the spawn drop settles.
									if _veh_fall_armed[0] and getattr(getattr(player, 'arena', None), 'period', 3) == 3:
										_offh_land_impact(_veh_vert_vel[0])
									_veh_fall_armed[0] = True
									_veh_vert_vel[0] = 0.0   # kill vertical only; horizontal momentum kept
									_veh_airborne[0] = False
									break
								_fall_i += 1
					else:
						# No terrain below. Told apart by whether we have EVER been grounded
						# (fall_armed): at SPAWN the space may simply not be streamed in yet -
						# HOLD instead of plummeting off the y=100 fallback (the ~4 s, -46 m/s
						# spawn drop). Once a ray hits, the spawn snap above sets the hull on
						# the ground. A genuine map-edge void AFTER driving is a real free fall.
						if not _veh_fall_armed[0]:
							_veh_vert_vel[0] = 0.0
							_veh_airborne[0] = False
						else:
							if not _veh_airborne[0]:
								LOG_DEBUG('OfflineBattle: player off map-edge, free fall')
							_veh_airborne[0] = True
							_veh_vert_vel[0] -= _phys_gravity * dt
							veh_pos[1] += _veh_vert_vel[0] * dt
				except Exception:
					pass

				# --- Drowning: WG 1:1 (Avatar.updateVehicleDestroyTimer). Three DROWN_WARNING_LEVEL
				# states: SAFE=hide, CAUTION='warning' (standing in water), DANGER='critical' countdown.
				# Whole seconds only - WG's flash floors the value, no sub-seconds. ---
				try:
					player._offh_dchk = getattr(player, '_offh_dchk', 0.0) + dt
					if player._offh_dchk >= 0.3:
						_dcel = min(player._offh_dchk, 0.5)
						player._offh_dchk = 0.0
						_depth = _offh_water_depth(veh_pos[0], veh_pos[1], veh_pos[2])
						# level: 0=SAFE(dry) 1=CAUTION(in water) 2=DANGER(drowning countdown)
						if _depth > 1.6:
							player._offh_drown_t = getattr(player, '_offh_drown_t', 0.0) + _dcel
							_rem = int(round(max(0.0, 10.0 - player._offh_drown_t)))
							_lvl = 2
						elif _depth > 0.5:
							player._offh_drown_t = 0.0
							_rem = 0
							_lvl = 1
						else:
							player._offh_drown_t = 0.0
							_rem = 0
							_lvl = 0
						if (getattr(mock_veh, 'health', 1) or 0) <= 0:
							_lvl = 0
							_rem = 0
						# Submerged: the crew is fighting the water, not the gun. Read by the shoot
						# gate and by the turret traverse below.
						player._offh_drowning = (_lvl == 2)
						# push ONCE per level change: flash then animates the countdown ring itself.
						# re-pushing every second restarts that animation -> stutter (WG pushes on level change only)
						if getattr(player, '_offh_drown_state', None) != _lvl:
							player._offh_drown_state = _lvl
							try:
								from gui import WindowsManager as _dwm
								_dbw = getattr(_dwm.g_windowsManager, 'battleWindow', None)
								if _dbw is not None:
									try:
										import constants as _dcst
										_dcode = _dcst.VEHICLE_MISC_STATUS.VEHICLE_DROWN_WARNING
									except Exception:
										_dcode = 4
									# mirror Avatar.updateVehicleDestroyTimer exactly
									if _lvl == 2:
										try: _dbw.showVehicleTimer(_dcode, _rem, 'critical')
										except TypeError: _dbw.showVehicleTimer(_dcode, _rem)
									elif _lvl == 1:
										try: _dbw.showVehicleTimer(_dcode, 0, 'warning')
										except TypeError: _dbw.showVehicleTimer(_dcode, 0)
									else:
										try: _dbw.hideVehicleTimer(_dcode)
										except TypeError: _dbw.hideVehicleTimer()
							except Exception:
								pass
						# death after 10 s fully submerged
						if _depth > 1.6 and player._offh_drown_t > 10.0 and (getattr(mock_veh, 'health', 1) or 0) > 0:
							# Drowning is not damage: the crew drowns, the hull is untouched. Keep the HP
							# the tank had when it went under for the DISPLAY, while the internal health
							# still goes to 0 - everything else (isAlive, the wipe check, the repair gate)
							# keys off that to treat the tank as dead.
							_hp_at_drown = getattr(mock_veh, 'health', 0) or 0
							mock_veh._hp_display = _hp_at_drown
							mock_veh.health = 0
							mock_veh._drowned = True
							mock_veh.last_killer_id = -1
							# the crew drowns with the tank - every module and every crew member is out
							_offh_knock_out_everything(mock_veh, True)
							try:
								_sn_d = getattr(player, 'soundNotifications', None)
								# crew_deactivated sets shouldBindToPlayer, so play() would bind it to the
								# player - who just drowned. __playFirstFromQueue discards a line bound to a
								# dead vehicle, so it was silently dropped exactly when it should sound.
								# Bind to a living ally instead, like the death path already does.
								_alive_d = None
								try:
									for _adv, _adi in player.arena.vehicles.iteritems():
										if _adi.get('isAlive') and _adv != player.playerVehicleID:
											_alive_d = _adv
											break
								except Exception:
									pass
								if _sn_d is not None: _sn_d.play('crew_deactivated', _alive_d)
							except Exception: pass
							# reason 5 = drowning attackReasonID; wrapper greys panel + dead marker
							try:
								player.arena.onVehicleKilled(getattr(mock_veh, 'id', player.playerVehicleID), -1, 5)
							except Exception:
								pass
							try:
								if hasattr(player, 'vehicle') and player.vehicle:
									player.vehicle.health = _hp_at_drown
									player.guiSessionProvider.invalidateVehicleState(1, player.playerVehicleID, _hp_at_drown, _hp_at_drown)
							except Exception:
								pass
							LOG_DEBUG('OfflineBattle: player drowned')
				except Exception:
					pass

				# --- Dead-state sync: grey out EVERY destroyed tank in the players panel,
				# no matter which path killed it (player shot, bot, fire, ram, fall) ---
				try:
					_greyed = globals().setdefault('_offh_greyed_ids', set())
					_pl2 = BigWorld.player()
					_arena_v = getattr(getattr(_pl2, 'arena', None), 'vehicles', None)
					# perf: sweep runs 4x/s, not every frame
					_ds = globals().get('g_offh_ds_acc', 0.0) + dt
					globals()['g_offh_ds_acc'] = 0.0 if _ds >= 0.25 else _ds
					_fresh = []
					for _kid, _kv in ((globals().get('G_MOCK_VEHICLES', {}) or {}).items() if _ds >= 0.25 else ()):
						if _kid in _greyed:
							continue
						if _kid == getattr(_pl2, 'playerVehicleID', -1):
							_dead = getattr(_pl2, '_is_dead', False)
						else:
							_dead = (not getattr(_kv, 'isAlive', True)) or ((getattr(_kv, 'health', 1) or 0) <= 0)
						if _dead:
							_greyed.add(_kid)
							_fresh.append(_kid)
							if _arena_v is not None and _kid in _arena_v:
								try:
									_arena_v[_kid]['isAlive'] = False
									_arena_v[_kid]['isAvatarReady'] = False  # else panel keeps it 'alive' (vState=2)
								except Exception: pass
					if _fresh:
						try:
							from gui import WindowsManager
							_bw2 = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
							if _bw2 is not None:
								if hasattr(_bw2, '_Battle__updatePlayers'):
									_bw2._Battle__updatePlayers()
								if getattr(_bw2, 'minimap', None):
									for _dk in _fresh:
										try: _bw2.minimap.notifyVehicleStop(_dk)
										except Exception: pass
						except Exception: pass
				except Exception: pass

				_offh_perf_stop('player_physics', _perf_player_physics)
				_perf_player_aim = _offh_perf_start()
				# --- Turret & Gun Mouse Aiming ---
				# Safe default: the aiming code below only assigns 'mat' on its happy
				# path; without this the gun-marker block crashes every frame.
				mat = Math.Matrix(mock_veh.matrix)
				try:
					is_sniper = False
					is_arty = False
					aih = getattr(BigWorld.player(), 'inputHandler', None)
					if aih and getattr(aih, '_AvatarInputHandler__isStarted', False):
						ctrl = getattr(aih, 'ctrl', None)
						if ctrl is not None:
							name = ctrl.__class__.__name__
							if name == 'SniperControlMode': is_sniper = True
							if name == 'StrategicControlMode': is_arty = True
					# SPG strategic (bird's-eye) view support for offline battles:
					# (A) StrategicCamera.enable seeds its pan anchor (__totalMove) from the map
					#     origin when no server position is available, so recentre it on the tank
					#     for a few frames after the strategic mode is entered.
					# (B) the strategic trajectory drawer (the green/red shot line) is driven by
					#     AvatarInputHandler from getDesiredShotPoint, which returns None offline,
					#     so it never updates. Feed it here: R = ground under the camera look-at,
					#     r0/v0 = gun muzzle params from the gun rotator.
					try:
						_aih_c = getattr(BigWorld.player(), 'inputHandler', None)
						_ctrl_c = getattr(_aih_c, 'ctrl', None) if _aih_c is not None else None
						_plr = BigWorld.player()
						_cur = _ctrl_c.__class__.__name__ if _ctrl_c is not None else None
						_last = getattr(_plr, '_offh_last_ctrl', None)
						_plr._offh_last_ctrl = _cur
						if _cur == 'StrategicControlMode':
							_sc2 = getattr(_ctrl_c, 'camera', None)
							if _last != 'StrategicControlMode':
								_plr._offh_arty_seedn = 12
							_sn = getattr(_plr, '_offh_arty_seedn', 0)
							if _sn > 0:
								_plr._offh_arty_seedn = _sn - 1
								try:
									_tm = getattr(_sc2, '_StrategicCamera__totalMove', None)
									if _tm is not None:
										_tm[0] = veh_pos[0]
										_tm[2] = veh_pos[2]
										try: _sc2.update(0.0, 0.0, 0.0)
										except Exception: pass
								except Exception: pass
							_tn = globals().get('_offh_traj_n', 0) + 1
							globals()['_offh_traj_n'] = _tn
							if _tn % 3 == 0:
								try:
									_tm2 = getattr(_sc2, '_StrategicCamera__totalMove', None)
									_tdrw = getattr(_ctrl_c, '_StrategicControlMode__trajectoryDrawer', None)
									if _tm2 is not None and _tdrw is not None:
										_ry = float(veh_pos[1])
										try:
											_rc = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(_tm2[0], 1000.0, _tm2[2]), Math.Vector3(_tm2[0], -250.0, _tm2[2]), 3)
											if _rc is not None: _ry = _rc[0][1]
										except Exception: pass
										_R = Math.Vector3(_tm2[0], _ry, _tm2[2])
										_r0, _v0, _g0 = BigWorld.player().gunRotator.getShotParams(_R)
										_tdrw.update(_R, _r0, _v0, 0.1)
								except Exception:
									pass
						else:
							_plr._offh_arty_seedn = 0
					except Exception:
						pass
					# 1. First compute previous exact gun position
					try:
						td = loaded_models.get('td')
						turretOffs = td.hull['turretPositions'][0] + td.chassis['hullPosition']
						gunOffs = td.turret['gunPosition']
					except:
						turretOffs = Math.Vector3(0, 1.5, 0)
						gunOffs = Math.Vector3(0, 0.4, 1.0)

					turretWorldMatrix = Math.Matrix()
					turretWorldMatrix.setRotateY(turret_yaw[0])
					turretWorldMatrix.translation = turretOffs
					turretWorldMatrix.postMultiply(mock_veh.matrix)
					last_true_gun_pos = turretWorldMatrix.applyPoint(gunOffs)

					# 2. Get exact 3D point the crosshair is looking at
					shot_point = None
					try:
						if aih and getattr(aih, '_AvatarInputHandler__isStarted', False):
							shot_point = aih.getDesiredShotPoint()
					except Exception as e:
						pass
					# (POS-CHECK debug removed: ran every frame and referenced an undefined var)

					if shot_point is None:
						cam_mat = Math.Matrix(BigWorld.camera().matrix)
						cam_pos = cam_mat.translation
						cam_dir = cam_mat.applyToAxis(2)
						cam_dir.normalise()
						end_pos = cam_pos + cam_dir.scale(1000.0)
						col = BigWorld.wg_collideSegment(_offh_bspace(), cam_pos, end_pos, 128)
						if col is not None:
							shot_point = col[0]
						else:
							shot_point = end_pos
							# Strategic/SPG looks straight down: if the engine ray misses for a
							# frame, intersect the view ray with the last aim height instead of
							# letting the reticle jump to the ray end (ported arty-view fix).
							if is_arty and abs(cam_dir.y) > 0.0001:
								try:
									_ply = float(_gun_state.get('last_aim_y', 0.0) or 0.0)
									_pt = (_ply - cam_pos.y) / cam_dir.y
									if 0.0 < _pt <= 2000.0:
										shot_point = cam_pos + cam_dir.scale(_pt)
								except Exception:
									pass
						try:
							_gun_state['last_aim_y'] = float(shot_point.y)
						except Exception:
							pass

				# 3. Calculate target yaw and pitch
					# Vector from mathematical gun to the target
					dx = shot_point.x - last_true_gun_pos.x
					dy = shot_point.y - last_true_gun_pos.y
					dz = shot_point.z - last_true_gun_pos.z
					dist = math.sqrt(dx*dx + dz*dz)

					try:
						if _gun_state.get('rmb_down', False) and not getattr(player, '_autoaim_target', None) and 'locked_local_yaw' in _gun_state:
							local_target_yaw = _gun_state['locked_local_yaw']
							target_pitch = _gun_state['locked_local_pitch']
							target_yaw = veh_yaw[0] + local_target_yaw
						else:
							_aat = getattr(player, '_autoaim_target', None)
							if _aat is not None and not getattr(_aat, '_spot_visible', True):
								# Target lost from view -> release the lock (like online):
								# the barrel silently tracking an invisible tank both
								# reveals it and looks broken.
								_set_autoaim_target(None, 'target_lost')
								_aat = None
							elif _aat is not None and getattr(_aat, 'health', 0) <= 0:
								# Retail silently clears a lock when the locked vehicle dies.
								_set_autoaim_target(None, '')
								_aat = None
							if _aat is not None and getattr(_aat, 'health', 0) > 0:
								t_pos = Math.Vector3(_aat.position)
								t_pos.y += 1.0
								shot_point = t_pos
							from projectile_trajectory import getShotAngles
							mat = BigWorld.player().getOwnVehicleMatrix()
							tYaw, gPitch = getShotAngles(td, mat, (turret_yaw[0], gun_pitch[0]), shot_point)
							local_target_yaw = tYaw
							target_pitch = gPitch
							target_yaw = veh_yaw[0] + local_target_yaw
					except Exception as e:
						# Fallback k jednoduche trigonometrii (nepresne)
						target_yaw = math.atan2(dx, dz)
						local_target_yaw = target_yaw - veh_yaw[0]

						if is_arty:
							try:
								shots = td.gun['shots'] if isinstance(td.gun, dict) else getattr(td.gun, 'shots')
								shot = shots[0]
								v = shot['speed'] if isinstance(shot, dict) else getattr(shot, 'speed')
								g = shot['gravity'] if isinstance(shot, dict) else getattr(shot, 'gravity', 9.81)
								g = abs(g)
								if g < 0.1: g = 9.81
								root = v**4 - g * (g * dist**2 + 2 * dy * v**2)
								if root > 0:
									target_pitch = -math.atan((v**2 - math.sqrt(root)) / (g * dist))
								else:
									# No ballistic solution: hold the gun at ITS OWN maximum elevation rather
									# than a hardcoded 45 degrees, which was the sky for anything else.
									target_pitch = _gun_min_pitch
							except Exception as ex:
								target_pitch = math.atan2(-dy, dist) # direct fire fallback
						else:
							target_pitch = math.atan2(-dy, dist)

					# Normalize angleses
					while local_target_yaw > math.pi: local_target_yaw -= 2*math.pi
					while local_target_yaw < -math.pi: local_target_yaw += 2*math.pi
					while turret_yaw[0] > math.pi: turret_yaw[0] -= 2*math.pi
					while turret_yaw[0] < -math.pi: turret_yaw[0] += 2*math.pi

					_gun_state['auto_steer'] = 0
					if not BigWorld.isKeyDown(Keys.KEY_RIGHTMOUSE):
						if _gun_min_yaw is not None and _gun_max_yaw is not None:
							# Check if aiming outside bounds
							if local_target_yaw < _gun_min_yaw - 0.02: _gun_state['auto_steer'] = -1
							elif local_target_yaw > _gun_max_yaw + 0.02: _gun_state['auto_steer'] = 1

					# Clamp to max traverse limits (for SPGs and TDs)
					local_target_yaw = max(_gun_min_yaw, min(_gun_max_yaw, local_target_yaw))

					diff_yaw = local_target_yaw - turret_yaw[0]
					if diff_yaw > math.pi: diff_yaw -= 2*math.pi
					if diff_yaw < -math.pi: diff_yaw += 2*math.pi

					# Actual turret angular speed this tick (rad/s), for the real
					# turretRotation dispersion term (was: remaining-yaw-delta hack).
					_gun_state['turret_speed'] = min(abs(diff_yaw) / max(dt, 0.001), _turret_rot_speed)

					# perf: outline scan ~8x/s (was every frame over all bots)
					player._outl_acc = (getattr(player, '_outl_acc', 9.0) or 9.0) + dt
					try:
						gun_dir = shot_point - last_true_gun_pos
						if player._outl_acc >= 0.12 and gun_dir.length > 0.001:
							gun_dir.normalise()
							_mock_vehicles = globals().get('G_MOCK_VEHICLES', {})

							import debug_utils
							if not hasattr(player, '_debug_dump_done_5'):
								player._debug_dump_done_5 = True
								debug_utils.LOG_DEBUG('AIH_TICK DUMP keys:', _mock_vehicles.keys())
								for _k, _v in _mock_vehicles.items():
									debug_utils.LOG_DEBUG(' - Veh', _k, getattr(_v, '_bot_team', 'N/A'))

							player._outl_acc = 0.0
							closest_bot = None
							min_dist = 9999.0
							for eid, m_veh in _mock_vehicles.iteritems():
								if eid == getattr(player, 'playerVehicleID', -1): continue
								if getattr(m_veh, 'health', 0) <= 0: continue
								# Unspotted (hidden) bots must not be outline/autoaim targets
								if not getattr(m_veh, '_spot_visible', True): continue
								b_pos = Math.Vector3(m_veh.position)
								b_vec = b_pos - last_true_gun_pos
								proj_len = b_vec.dot(gun_dir)
								# (per-frame log removed: file I/O for every bot every frame)
								if proj_len > 0:
									proj_pt = last_true_gun_pos + gun_dir.scale(proj_len)
									dist_to_ray = (b_pos - proj_pt).length
									# (per-frame log removed)
									if dist_to_ray < 2.5:
										if proj_len < min_dist:
											min_dist = proj_len
											closest_bot = m_veh
							# No outline through terrain/buildings: the picker only projects
							# onto the gun ray, so tanks BEHIND rocks/walls got the border
							# (and could be autoaim-locked). Two sample points (mid-hull +
							# turret top): a hull-down tank whose centre ray grazes the
							# crest still gets its silhouette off the turret ray - only a
							# tank with BOTH points behind statics loses the outline.
							if closest_bot is not None:
								try:
									_ob_base = closest_bot.position
									_blocked = True
									for _oby in (1.5, 2.2):
										_ob_pos = Math.Vector3(_ob_base.x, _ob_base.y + _oby, _ob_base.z)
										_oc = BigWorld.wg_collideSegment(_offh_bspace(), last_true_gun_pos, _ob_pos, 128)
										if _oc is None or ((_oc[0] - last_true_gun_pos).length + 2.0) >= (_ob_pos - last_true_gun_pos).length:
											_blocked = False
											break
									if _blocked:
										closest_bot = None
								except Exception:
									pass
							prev_bot = getattr(player, '_outlined_bot', None)
							if prev_bot and prev_bot != closest_bot:
								try:
									if hasattr(prev_bot, 'bw_entity') and prev_bot.bw_entity:
										BigWorld.wgDelEdgeDetectEntity(prev_bot.bw_entity)
								except Exception as e:
									pass
								player._outlined_bot = None
							if closest_bot and prev_bot != closest_bot:
								color = 2 if getattr(closest_bot, '_bot_team', 2) == getattr(player, '_offhangar_team', 1) else 1
								try:
									if hasattr(closest_bot, 'bw_entity') and closest_bot.bw_entity:
										BigWorld.wgAddEdgeDetectEntity(closest_bot.bw_entity, color)
										LOG_DEBUG('REAL_RAYCAST OUTLINE APPLIED TO BOT', closest_bot.bw_entity.id)
									else:
										LOG_DEBUG('REAL_RAYCAST bot has no bw_entity!')
								except Exception as e:
									LOG_DEBUG('Outline dummy err:', str(e))
								player._outlined_bot = closest_bot
					except Exception as e:
						import debug_utils
						debug_utils.LOG_DEBUG('Outline error:', str(e))

					# Countdown: turret + gun frozen until the prebattle timer hits 0 (period 3)
					if getattr(getattr(BigWorld.player(), 'arena', None), 'period', 3) < 3:
						local_target_yaw = turret_yaw[0]; diff_yaw = 0.0; target_pitch = gun_pitch[0]
					_t_step = _turret_rot_speed * dt  # rad this frame (framerate independent)
					_pm_tr = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
					_turret_locked = _pm_tr is not None and getattr(_pm_tr, 'is_turret_locked', False)
					# A DAMAGED turret ring still traverses, only slowly. Destroyed is the freeze
					# below (_turret_locked), which is the state the real client knew about.
					try:
						_tsf = _module_factor(_pm_tr, 'turret_speed')
						if _tsf < 1.0:
							_t_step = _t_step * _tsf
					except Exception: pass
					if getattr(player, '_offh_drowning', False) or getattr(player, '_is_dead', False) or _turret_locked:
						# Frozen while submerged AND once dead. The damage-panel tank indicator is
						# bound to this turret matrix, so a dead tank kept turning its turret there.
						_t_step = 0.0
						diff_yaw = 0.0
					if abs(diff_yaw) < _t_step:
						turret_yaw[0] = local_target_yaw
					else:
						turret_yaw[0] += _t_step * (1 if diff_yaw > 0 else -1)

					# Update pitch
					# Yaw-dependent pitch limits (extraPitchLimits tanks have e.g. less
					# depression over the engine deck), same as the real VehicleGunRotator
					try:
						if _gun_pitch_desc is not None:
							from gun_rotation_shared import calcPitchLimitsFromDesc as _cpl
							_lim_now = _cpl(turret_yaw[0], _gun_pitch_desc)
							target_pitch = max(_lim_now[0], min(_lim_now[1], target_pitch))
						else:
							target_pitch = max(_gun_min_pitch, min(_gun_max_pitch, target_pitch))
					except Exception:
						target_pitch = max(_gun_min_pitch, min(_gun_max_pitch, target_pitch))

					diff_pitch = target_pitch - gun_pitch[0]
					_p_step = _gun_pitch_speed * dt  # vertical aim speed from gun descriptor (rad/s)
					if abs(diff_pitch) < _p_step:
						gun_pitch[0] = target_pitch
					else:
						gun_pitch[0] += _p_step * (1 if diff_pitch > 0 else -1)

					player = BigWorld.player()
					# Force the mod model funcs (once): the native ProjectileMover draws shot
					# tracers via player.addModel/delModel; route them through _add_model so the
					# shell models land in the BATTLE space. An account-native addModel targets
					# the empty read-only space and left tracers invisible (the old not-hasattr
					# guard never replaced it).
					# NOTE: do NOT install addModel/delModel by assignment. PlayerAccount is a real
					# BigWorld.Entity and its method slots are READ-ONLY - the assignment raises
					# "Sorry, that method attribute in PlayerAccount is read-only", so this never
					# took effect and every shell model went to the account's own chunk instead of
					# the battle world. Served from Account.__getattribute__ in mod_offhangar.py.
					# (delModel is served the same way, see _offh_player_del_model.)

					# Mock appearance for SniperCamera to find HP_gunJoint
					if 'gun_node_matrix' not in loaded_models:
						loaded_models['gun_node_matrix'] = Math.Matrix()
					if not hasattr(mock_veh, 'appearance'):
						class FakeAppearance(object):
							def __init__(self):
								class FakeCompound(object):
									def node(self, name):
										if name == 'HP_gunJoint': return loaded_models['gun_node_matrix']
										if name == 'HP_turretJoint': return loaded_models.get('hull').node(name) if loaded_models.get('hull') else None
										return mock_veh.model.node(name)
									@property
									def position(self): return mock_veh.position
									@property
									def matrix(self): return mock_veh.matrix
								self.compoundModel = FakeCompound()
								self.modelsDesc = {'gun': {'model': loaded_models.get('gun')}}
							def changeVisibility(self, modelName, modelVisible, attachmentsVisible):
								is_sniper = not modelVisible
								c_mdl = loaded_models.get('chassis')
								h_mdl = loaded_models.get('hull')
								t_mdl = loaded_models.get('turret')
								g_mdl = loaded_models.get('gun')
								if hasattr(c_mdl, 'visible'): c_mdl.visible = not is_sniper
								if hasattr(h_mdl, 'visible'): h_mdl.visible = not is_sniper
								if hasattr(t_mdl, 'visible'): t_mdl.visible = not is_sniper
								if hasattr(g_mdl, 'visible'): g_mdl.visible = not is_sniper
							def hideIfExistFor(self, vehicle):
								pass
						mock_veh.appearance = FakeAppearance()

					# Debug log every 50 ticks (1 sec)
					_tick_counter[0] += 1
					if _tick_counter[0] % 50 == 0:
						try:
							cur_cam = Math.Matrix(BigWorld.camera().matrix)
							c_ptc = -cur_cam.pitch
						except:
							c_ptc = 0.0
						LOG_DEBUG('OfflineBattle.aim: cam_yaw=%.2f, veh_yaw=%.2f, loc_tgt=%.2f, tur_yaw=%.2f, cam_ptc=%.2f, gun_ptc=%.2f' % (
							target_yaw, veh_yaw[0], local_target_yaw, turret_yaw[0], c_ptc, gun_pitch[0]))

				except Exception as e:
					LOG_DEBUG('OfflineBattle.aim error:', str(e))


				_offh_perf_stop('player_aim', _perf_player_aim)
				_perf_player_pose = _offh_perf_start()
				# --- Update mock vehicle and camera matrix ---
				mock_veh.position = Math.Vector3(veh_pos[0], veh_pos[1], veh_pos[2])
				mock_veh.yaw   = veh_yaw[0]

				# DEBUG CHASSIS KEYS
				try:
					if getattr(mock_veh, '_dbg_keys_logged', None) is None:
						_td_dbg = loaded_models.get('td')
						if _td_dbg and hasattr(_td_dbg, 'chassis'):
							try: LOG_DEBUG('CHASSIS BBOX:', _td_dbg.chassis['hitTester'].bbox)
							except: pass
						import AreaDestructibles, inspect, constants
						if hasattr(AreaDestructibles, 'g_destructiblesManager'):
							if getattr(AreaDestructibles.g_destructiblesManager, 'getSpaceID', lambda: -1)() != _offh_bspace():
								AreaDestructibles.g_destructiblesManager.startSpace(_offh_bspace())
							try: LOG_DEBUG('DESTRUCTIBLE_MATKIND MIN/MAX:', constants.DESTRUCTIBLE_MATKIND.MIN, constants.DESTRUCTIBLE_MATKIND.MAX)
							except: pass
							try: LOG_DEBUG('BW collide doc:', BigWorld.collide.__doc__)
							except: pass
							try:
								# Log DestructiblesController methods!
								import AreaDestructibles
								if getattr(AreaDestructibles.g_destructiblesManager, 'getSpaceID', lambda: -1)() != _offh_bspace():
									AreaDestructibles.g_destructiblesManager.startSpace(_offh_bspace())
								chunkID = AreaDestructibles.chunkIDFromPosition(BigWorld.player().position)
								ctrl = AreaDestructibles.g_destructiblesManager.getController(chunkID)
								if ctrl:
									LOG_DEBUG('DestructiblesController dir:', dir(ctrl))
								else:
									LOG_DEBUG('DestructiblesController ctrl is NONE')
							except Exception as e:
								LOG_DEBUG('DestructiblesController EXCEPTION:', str(e))
							try: LOG_DEBUG('encodeDestructibleModule argspec:', inspect.getargspec(AreaDestructibles.encodeDestructibleModule))
							except: pass
							try: LOG_DEBUG('encodeFallenTree argspec:', inspect.getargspec(AreaDestructibles.encodeFallenTree))
							except: pass
							try: LOG_DEBUG('encodeFallenColumn argspec:', inspect.getargspec(AreaDestructibles.encodeFallenColumn))
							except: pass
							try: LOG_DEBUG('wg_getMatInfoNearPoint doc:', BigWorld.wg_getMatInfoNearPoint.__doc__)
							except: pass
							try: LOG_DEBUG('onChunkLoad argspec:', inspect.getargspec(AreaDestructibles.g_destructiblesManager.onChunkLoad))
							except: pass
						mock_veh._dbg_keys_logged = True
				except: pass

				# Vypočítat náklon tanku hráče podle terénu
				_p_ypr = _get_terrain_ypr(_offh_bspace(), mock_veh.position, veh_yaw[0])
				# --- Slope slide: physics.slope_slide_speed (WG track-cohesion hold). The
				# hull holds on any drivable hill; past the grip limit it slips down the
				# fall line. Only the CROSS-heading component is applied here - the along-
				# heading part is already in longitudinal_step (engine vs slope gravity),
				# so climbing/stalling and lateral slip never double-count. ---
				_pss = getattr(player, '_slide_spd', 0.0) or 0.0
				if _veh_airborne[0]:
					_pss = 0.0   # no fresh ground slide while flying (carried drift below)
				else:
					_pss = _PHY.slope_slide_speed(_pss, _p_ypr[5], dt)
				player._slide_spd = _pss
				# Physics telemetry (~5 Hz) when config physics_debug is on: one CSV row
				# per sample to offhangar_user/physics_telemetry.csv for tuning vs original.
				if _offh_phys_debug[0]:
					player._offh_tel_acc = (getattr(player, '_offh_tel_acc', 0.0) or 0.0) + dt
					if player._offh_tel_acc >= 0.2:
						player._offh_tel_acc = 0.0
						try:
							import gui.mods.offhangar.physics_monitor as _offh_mon
							# observable extras the force numbers miss:
							_now = BigWorld.time()
							_pel = _now - (getattr(player, '_tel_pt', _now) or _now)
							_gkm = 0.0; _dyv = 0.0
							if _pel > 0.01:
								_ddx = veh_pos[0] - (getattr(player, '_tel_px', veh_pos[0]) or veh_pos[0])
								_ddz = veh_pos[2] - (getattr(player, '_tel_pz', veh_pos[2]) or veh_pos[2])
								_gkm = (math.sqrt(_ddx*_ddx + _ddz*_ddz) / _pel) * 3.6
								_dyv = veh_pos[1] - (getattr(player, '_tel_py', veh_pos[1]) or veh_pos[1])
							player._tel_px = veh_pos[0]; player._tel_pz = veh_pos[2]; player._tel_py = veh_pos[1]; player._tel_pt = _now
							_offh_mon.log(_PHY.snapshot(_pparams, _veh_velocity[0], _veh_turn_velocity[0], throttle, _slope_p, _veh_airborne[0], _pss, 'player', ground_kmh=_gkm, pitch_deg=math.degrees(getattr(mock_veh, 'pitch', 0.0) or 0.0), roll_deg=math.degrees(getattr(mock_veh, 'roll', 0.0) or 0.0), vert_ms=_veh_vert_vel[0], deflect=getattr(player, '_offh_deflected', False), dy=_dyv, slide_slope=_p_ypr[5], dy_tick=(getattr(player, '_dy_tick_max', 0.0) or 0.0), y_src=getattr(player, '_dy_tick_src', None), terr_spread=(_p_ypr[6] if len(_p_ypr) > 6 else None)), _now)
							player._dy_tick_max = 0.0
						except Exception:
							pass
				# fall line projected onto the cross-heading axis (perp to hull yaw)
				_cross_x = math.cos(veh_yaw[0]); _cross_z = -math.sin(veh_yaw[0])
				_slide_dot = _p_ypr[3] * _cross_x + _p_ypr[4] * _cross_z
				_slide_dx = _cross_x * _slide_dot
				_slide_dz = _cross_z * _slide_dot
				try:
					player._slide_dbg_t = getattr(player, '_slide_dbg_t', 0.0) + dt
					if _p_ypr[5] > 0.35 and player._slide_dbg_t > 1.0:
						player._slide_dbg_t = 0.0
						LOG_DEBUG('SLIDE dbg slope=%.2f deg=%.0f pss=%.2f air=%s vvel=%.2f' % (_p_ypr[5], math.degrees(math.atan(_p_ypr[5])), _pss, _veh_airborne[0], _veh_velocity[0]))
				except: pass
				if _veh_airborne[0]:
					# Carry the lateral drift frozen at take-off through the fall: no ground
					# contact = no friction, so sideways momentum is conserved (light air
					# drag). Longitudinal v and the vertical fall are integrated elsewhere.
					_alx = getattr(player, '_air_lat_vx', 0.0) or 0.0
					_alz = getattr(player, '_air_lat_vz', 0.0) or 0.0
					if abs(_alx) > 1e-04 or abs(_alz) > 1e-04:
						veh_pos[0] += _alx * dt
						veh_pos[2] += _alz * dt
						player._air_lat_vx = _alx * 0.995
						player._air_lat_vz = _alz * 0.995
						mock_veh.position = Math.Vector3(veh_pos[0], veh_pos[1], veh_pos[2])
				else:
					# Grounded: freeze the CURRENT world lateral velocity so a take-off next
					# frame conserves it (zero when not sliding).
					player._air_lat_vx = _slide_dx * _pss
					player._air_lat_vz = _slide_dz * _pss
				if not _veh_airborne[0] and _pss > 0.01 and (abs(_slide_dx) > 1e-04 or abs(_slide_dz) > 1e-04):
					_slp_x = veh_pos[0] + _slide_dx * _pss * dt
					_slp_z = veh_pos[2] + _slide_dz * _pss * dt
					try:
						_slp_c = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(_slp_x, veh_pos[1] + 8.0, _slp_z), Math.Vector3(_slp_x, veh_pos[1] - 30.0, _slp_z), 128)
					except Exception:
						_slp_c = None
					if _slp_c is not None and (veh_pos[1] - _slp_c[0].y) < 4.0:
						veh_pos[0] = _slp_x
						veh_pos[2] = _slp_z
						_sdy = _slp_c[0].y - veh_pos[1]
						if _sdy > 0.35:
							_sdy = 0.35
						elif _sdy < -0.35:
							_sdy = -0.35   # ease off/onto an edge under the slide; don't teleport the
							               # hull (a sharp spot below the slid position was a 3.5 m pop).
						veh_pos[1] += _sdy
						player._y_snap = 'slide'
						# Anti-penetration: lift so the uphill hull edge clears the rising bank
						try:
							_up_x = _slp_x - _p_ypr[3] * 3.0
							_up_z = _slp_z - _p_ypr[4] * 3.0
							_upc = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(_up_x, veh_pos[1] + 8.0, _up_z), Math.Vector3(_up_x, veh_pos[1] - 30.0, _up_z), 128)
							if _upc is not None:
								_pexp = veh_pos[1] + 3.0 * _p_ypr[5]
								# Only lift for a GENUINE rising bank/step: the uphill ground must sit a
								# clear margin ABOVE the linear slope. The gentle concave base of a hill
								# sits only just above it - lifting the whole hull there made the tank
								# ride high driving onto a slope. Lift only the excess over the margin,
								# small cap, sharing the 0.35 budget with the slide-snap (_sdy).
								if _upc[0].y > _pexp + 0.30:
									_lift = _upc[0].y - _pexp - 0.30
									_lift_cap = 0.20 - (_sdy if _sdy > 0.0 else 0.0)
									if _lift_cap < 0.0:
										_lift_cap = 0.0
									if _lift > _lift_cap:
										_lift = _lift_cap
									veh_pos[1] += _lift
									player._y_snap = 'antipen'
						except Exception:
							pass
						_veh_vert_vel[0] = 0.0
						_veh_airborne[0] = False
						mock_veh.position = Math.Vector3(veh_pos[0], veh_pos[1], veh_pos[2])
				try:
					from gui.mods.offhangar import battle_feedback as _offh_feedback_move
					_offh_feedback_move.record_position(
						_offh_stats_for(player), (veh_pos[0], veh_pos[1], veh_pos[2]))
				except Exception:
					pass
			# Smooth pitch/roll so bumps and landings don't snap the hull instantly
				_pr_blend = min(1.0, dt * 8.0)
				_pr_p0 = getattr(mock_veh, 'pitch', 0.0)
				_pr_r0 = getattr(mock_veh, 'roll', 0.0)
				_p_ypr = (_p_ypr[0], _pr_p0 + (_p_ypr[1] - _pr_p0) * _pr_blend, _pr_r0 + (_p_ypr[2] - _pr_r0) * _pr_blend)
				mock_veh.pitch = _p_ypr[1]
				mock_veh.roll = _p_ypr[2]
				# edge-pop telemetry: track the largest single physics-tick height jump
				# across the 5 Hz window (the 0.2 s dy smears sub-sample pops at edges).
				if _offh_phys_debug[0]:
					_cy = veh_pos[1]
					_tdy = _cy - (getattr(player, '_prev_ty', _cy) or _cy)
					player._prev_ty = _cy
					if abs(_tdy) > abs(getattr(player, '_dy_tick_max', 0.0) or 0.0):
						player._dy_tick_max = _tdy
						player._dy_tick_src = getattr(player, '_y_snap', None) or ('air' if _veh_airborne[0] else 'follow')

				# Update base matrix IN PLACE so AvatarInputHandler doesn't lose the reference
				mock_veh.matrix.setRotateYPR(_p_ypr)
				mock_veh.matrix.translation = mock_veh.position

				if hasattr(mock_veh, 'filter'):
					mock_veh.filter.position = mock_veh.position
					mock_veh.filter.yaw = veh_yaw[0]

				# Update camera matrix (needs both translation AND yaw for SniperCamera offsets to work)
				# (Arcade camera strips yaw using WGTranslationOnlyMP later)
				new_m = Math.Matrix()
				new_m.setRotateYPR(_p_ypr)
				new_m.translation = mock_veh.position
				veh_matrix.a = new_m

				# Update chassis matrix (position + yaw) - Servo drives the model
				# Always update the chassis matrix, INCLUDING sniper mode. Sniper
				# hiding is done via model.visible=False nowadays (not the old
				# push-underground trick this skip was written for), and freezing
				# the matrix left the chassis/hull/gun models - and everything
				# attached to them: engine sound, gun shot sound, muzzle flash -
				# stuck at the position where the player scoped in, so audio and
				# shot effects played from the wrong place after driving scoped.
				chassis_new = Math.Matrix()
				chassis_new.setRotateYPR(_p_ypr)
				chassis_new.translation = mock_veh.position
				chassis_mp.a = chassis_new

				# Engine sounds are handled in _step_offline_physics





				_offh_perf_stop('player_pose', _perf_player_pose)
				_perf_player_gun = _offh_perf_start()
				# --- Update Gun Mechanics (Dispersion & Reload) ---
				if not _gun_state['initialized']:
					td = loaded_models.get('td')
					if td is not None and hasattr(td, 'gun'):
						try:
							_gun_state['base_dispersion'] = td.gun.get('shotDispersionAngle', 0.1) if isinstance(td.gun, dict) else getattr(td.gun, 'shotDispersionAngle', 0.1)
							if 'shotDispersionFactors' in td.gun if isinstance(td.gun, dict) else hasattr(td.gun, 'shotDispersionFactors'):
								_gun_state['after_shot'] = td.gun['shotDispersionFactors'].get('afterShot', 1.5) if isinstance(td.gun, dict) else td.gun.shotDispersionFactors.get('afterShot', 1.5)
							_gun_state['aim_time'] = td.gun.get('aimingTime', 2.0) if isinstance(td.gun, dict) else getattr(td.gun, 'aimingTime', 2.0)
							if 'clip' in td.gun if isinstance(td.gun, dict) else hasattr(td.gun, 'clip'):
								_clip = td.gun['clip'] if isinstance(td.gun, dict) else td.gun.clip
								_gun_state['clip_size'] = _clip[0]
								_gun_state['clip_reload'] = _clip[1]
							_gun_state['reload'] = td.gun.get('reloadTime', 5.0) if isinstance(td.gun, dict) else getattr(td.gun, 'reloadTime', 5.0)

							_gun_state['ammo'] = 45
							if hasattr(td, 'maxAmmo'): _gun_state['ammo'] = td.maxAmmo
							elif isinstance(td.gun, dict) and 'maxAmmo' in td.gun: _gun_state['ammo'] = td.gun['maxAmmo']
							elif hasattr(td.gun, 'maxAmmo'): _gun_state['ammo'] = td.gun.maxAmmo
							elif hasattr(td, 'turret') and hasattr(td.turret, 'maxAmmo'): _gun_state['ammo'] = td.turret.maxAmmo

							# Equipment & Crew Modifiers
							has_rammer, has_egld, has_vents, has_vstab, has_rations = False, False, False, False, False
							has_bia, has_snapshot, has_smooth_ride = True, False, False
							has_sixth_sense = False

							# Hardcode consumables if none found or to guarantee they exist in offline mode
							_gun_state['consumables'] = [
								{'slot': 3, 'tag': 'repairkit', 'name': 'smallrepairkit', 'icon': '../maps/icons/artefact/smallRepairkit.png', 'used': False},
								{'slot': 4, 'tag': 'medkit', 'name': 'smallmedkit', 'icon': '../maps/icons/artefact/smallMedkit.png', 'used': False},
								{'slot': 5, 'tag': 'extinguisher', 'name': 'handextinguishers', 'icon': '../maps/icons/artefact/handExtinguishers.png', 'used': False}
							]

							try:
								from CurrentVehicle import g_currentVehicle
								if g_currentVehicle and hasattr(g_currentVehicle, 'item') and g_currentVehicle.item:
									v_item = g_currentVehicle.item

									try:
										import debug_utils
										debug_utils.LOG_DEBUG('DEBUG STATS COMP: td.gun.aimingTime=', getattr(td.gun, 'aimingTime', None), 'v_item.descriptor.gun.aimingTime=', getattr(v_item.descriptor.gun, 'aimingTime', None))
									except: pass

									# Parse Equipment
									for dev in getattr(v_item, 'optDevices', []):
										if not dev: continue
										name = getattr(dev, 'name', '') or getattr(getattr(dev, 'descriptor', None), 'name', '') or str(dev)
										name = str(name).lower()
										import debug_utils
										debug_utils.LOG_DEBUG('Parsed Equipment Name:', name)
										if 'rammer' in name: has_rammer = True
										if 'aimdrives' in name: has_egld = True
										if 'ventilation' in name: has_vents = True
										if 'stabilizer' in name: has_vstab = True
									# Parse Consumables from g_currentVehicle if available
									# (We already hardcoded them above, but we can override if needed)

									_eqs_list = list(getattr(v_item, 'eqs', []))
									if any(_eqs_list):
										_gun_state['consumables'] = []

									for idx, eq in enumerate(_eqs_list):
										if not eq: continue
										name = getattr(eq, 'name', '') or getattr(getattr(eq, 'descriptor', None), 'name', '') or str(eq)
										name = str(name).lower()
										if any(x in name for x in ('ration', 'chocolate', 'cola', 'coffee', 'pudding')): has_rations = True
										icon = getattr(eq, 'icon', None) or getattr(getattr(eq, 'descriptor', None), 'icon', None)
										icon_path = icon[0] if icon and isinstance(icon, tuple) else ''
										if not icon_path:
											# Every variant has its OWN icon shipped in res (smallRepairkit/largeRepairkit,
											# smallMedkit/largeMedkit, handExtinguishers/autoExtinguishers). The old fallback
											# matched only 'medkit'/'repair'/'extinguisher' and always handed back the small
											# one, so a large kit sat in the slot wearing the small kit's picture.
											_big = ('large' in name) or ('big' in name)
											if 'medkit' in name:
												icon_path = '../maps/icons/artefact/%sMedkit.png' % ('large' if _big else 'small')
											elif 'repair' in name:
												icon_path = '../maps/icons/artefact/%sRepairkit.png' % ('large' if _big else 'small')
											elif 'extinguisher' in name:
												# automatic extinguishers are the 'auto' variant, the hand ones the default
												icon_path = '../maps/icons/artefact/%sExtinguishers.png' % ('auto' if 'auto' in name else 'hand')

										import debug_utils
										debug_utils.LOG_DEBUG('DUMP CONSUMABLE:', name, icon, icon_path)
										tag_name = 'extinguisher' if 'extinguisher' in name else ('medkit' if 'medkit' in name else ('repairkit' if 'repair' in name else ''))
										if tag_name:
											_gun_state['consumables'].append({
												'slot': idx + 3,
												'tag': tag_name,
												'name': name,
												'icon': icon_path,
												'used': False
											})

									# Parse Crew Perks
									crew = getattr(v_item, 'crew', [])
									import debug_utils
									debug_utils.LOG_DEBUG('CREW OBJECT IS:', len(crew), crew)
									if not crew: has_bia = False
									for idx, item in enumerate(crew):
										try:
											tman = item[1] if isinstance(item, tuple) and len(item) == 2 else item

											if tman is None:
												has_bia = False
												continue

											tman_skills = []
											if hasattr(tman, 'skills'):
												for sk in tman.skills:
													name = getattr(sk, 'name', '') or str(sk)
													tman_skills.append(str(name).lower())
											elif hasattr(tman, 'descriptor') and hasattr(tman.descriptor, 'skills'):
												tman_skills = [str(sk).lower() for sk in tman.descriptor.skills]

											if 'brotherhood' not in tman_skills: has_bia = False
											if 'smoothturret' in tman_skills or 'snapshot' in tman_skills: has_snapshot = True
											if 'smoothdriving' in tman_skills or 'smoothride' in tman_skills: has_smooth_ride = True
											if any('sixthsense' in _skill for _skill in tman_skills): has_sixth_sense = True
										except Exception as ce:
											import debug_utils
											debug_utils.LOG_DEBUG('Crew member parsing error:', str(ce))
											has_bia = False
							except Exception as e:
								import debug_utils
								debug_utils.LOG_DEBUG('Equipment/Crew parsing error:', str(e))
								has_bia = False
							player._offhangar_has_sixth_sense = bool(has_sixth_sense)

							# Calculate crew multiplier (Base 100% crew + Commander 10% bonus)
							crew_skill, commander_skill = 100.0, 100.0
							if has_vents:
								crew_skill += 5.0
								commander_skill += 5.0
							if has_bia:
								crew_skill += 5.0
								commander_skill += 5.0
							if has_rations:
								crew_skill += 10.0
								commander_skill += 10.0
							effective_skill = crew_skill + (commander_skill * 0.1)
							crew_mult = 1.0 / (0.5 + 0.005 * effective_skill)

							_gun_state['base_dispersion'] *= crew_mult
							_gun_state['aim_time'] *= crew_mult
							_gun_state['reload'] *= crew_mult
							_gun_state['clip_reload'] *= crew_mult

							if has_rammer:
								_gun_state['reload'] *= 0.9
								_gun_state['clip_reload'] *= 0.9
							if has_egld:
								_gun_state['aim_time'] /= 1.1
							_gun_state['has_vstab'] = has_vstab
							_gun_state['has_snapshot'] = has_snapshot
							_gun_state['has_smooth_ride'] = has_smooth_ride

						except Exception as e:
							LOG_DEBUG('OfflineBattle: Gun State Init ERROR:', str(e))
						# Empty gun at battle start, like the original: nothing is chambered and no
						# magazine is in. The reload tick below refuses to run until the arena reaches
						# period 3, so the first round only starts going in when the countdown ends.
						_gun_state['clip'] = 0
						_gun_state['reloadTime'] = _gun_state['reload']
						_gun_state['load_started'] = False
						# A knocked-out gunner widens the aiming circle (commander a bit more).
						try:
							_pm_cd = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
							_gun_state['dispersion'] = _gun_state['base_dispersion'] * (_crew_factor(_pm_cd, 'dispersion') if _pm_cd is not None else 1.0)
						except Exception:
							_gun_state['dispersion'] = _gun_state['base_dispersion']
						_gun_state['initialized'] = True
						LOG_DEBUG('OfflineBattle: Gun State initialized from TD: dispersion=%.3f, aim_time=%.2f, reload=%.2f, clip_size=%d' % (
							_gun_state['base_dispersion'], _gun_state['aim_time'], _gun_state['reload'], _gun_state['clip_size']))

				if _gun_state['initialized']:
					try:
						# 1. Dispersion shrinkage

						if 'GUI_INIT' not in _gun_state:
							try:
								from gui import WindowsManager
								panel = getattr(WindowsManager.g_windowsManager.battleWindow, 'consumablesPanel', None) if getattr(WindowsManager.g_windowsManager, 'battleWindow', None) else None
								if panel:
									try:
										td = loaded_models.get('td')
										shots = td.gun['shots'] if isinstance(td.gun, dict) else getattr(td.gun, 'shots', [])

										# Distribute maxAmmo across available shells
										ammo_pool = _gun_state['ammo']
										try:
											from CurrentVehicle import g_currentVehicle
											v_shells = []
											if g_currentVehicle and g_currentVehicle.item:
												shells = getattr(g_currentVehicle.item, 'shells', [])
												for sh in shells:
													if hasattr(sh, 'count'): v_shells.append(sh.count)
													elif isinstance(sh, tuple) and len(sh) >= 2: v_shells.append(sh[1])
										except:
											v_shells = []

										for i, shot in enumerate(shots):
											try: shell = shot['shell']
											except: shell = getattr(shot, 'shell', None)
											try: piercing_val = shot['piercingPower']
											except: piercing_val = getattr(shot, 'piercingPower', 100)
											if isinstance(piercing_val, (tuple, list)): piercing_val = piercing_val[0]

											if v_shells and i < len(v_shells):
												qty = v_shells[i]
											else:
												qty = int(ammo_pool * 0.6) if i == 0 else (int(ammo_pool * 0.3) if i == 1 else int(ammo_pool * 0.1))
												if qty == 0 and ammo_pool > 0: qty = 1

											_gun_state['ammo_%d' % i] = qty
											panel.addShellSlot(i, qty, _gun_state['clip_size'], _gun_state['clip_size'], shell, piercing_val)

										# Find first shell with > 0 ammo
										first_active = 0
										for i in xrange(len(shots)):
											if _gun_state.get('ammo_%d' % i, 0) > 0:
												first_active = i
												break
										_gun_state['shot_index'] = first_active

										# Select the first shell as active to show clip UI
										panel.setCurrentShell(first_active)
										panel.setShellQuantityInSlot(first_active, _gun_state['ammo_%d' % first_active], _gun_state['clip'])
									except Exception as ex: LOG_DEBUG('SHELL SLOT FAIL:', str(ex))

									try:
										import AvatarInputHandler.aims as aim
										aim.setClipParams(_gun_state['clip_size'], 1)
										aim.setAmmoStock(_gun_state['ammo_%d' % first_active], _gun_state['clip'], False)

										# Vynutit reset ukazatele zdraví v GUI!
										from gui import WindowsManager
										bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
										if bw is not None:
											_mh = getattr(td, 'maxHealth', 400)
											if hasattr(bw, 'damagePanel'):
												try: bw.damagePanel._DamagePanel__callFlash('setMaxHealth', [_mh])
												except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())
												bw.damagePanel.updateHealth(_mh)
											if hasattr(bw, 'vMarkersManager'):
												pass # bw.vMarkersManager.updateVehicleHealth(player.playerVehicleID, _mh, 1, 0)
									except Exception as e: pass

									# Add Consumables to UI
									if not _gun_state.get('consumables_added_to_ui'):
										_gun_state['consumables_added_to_ui'] = True
										import debug_utils
										debug_utils.LOG_DEBUG('ADDING CONSUMABLES TO UI:', _gun_state.get('consumables', []))

										class FakeEqDescr(object):
											def __init__(self, tag, icon, name):
												self.tags = set([tag])
												self.icon = [icon]
												self.userString = name
												self.description = ''

										for cons in _gun_state.get('consumables', []):
											idx = cons['slot']
											tag = cons['tag']
											icon = cons['icon']
											name = cons['name']
											try:
												panel.addEquipmentSlot(idx, 1, FakeEqDescr(tag, icon, name))
											except Exception as e:
												import debug_utils
												debug_utils.LOG_DEBUG('Failed to addEquipmentSlot:', str(e))

									# Route Flash slot clicks and damage-panel icon clicks into the offline
									# equipment activation: small kit -> selector, large -> repair all, and a
									# click on a damaged module icon repairs exactly that module.
									try:
										player.onEquipmentButtonPressed = (lambda _idx, deviceName=None: _offh_activate_equipment(_idx, deviceName))
										player.onDamageIconButtonPressed = (lambda _tag, _dev=None: _offh_damage_icon(_tag, _dev))
									except Exception as _wire_e:
										LOG_DEBUG('wire equipment methods err:', str(_wire_e))
									_gun_state['GUI_INIT'] = True
									LOG_DEBUG('OfflineBattle: GUI panel initialized!')
							except Exception as e:
								LOG_DEBUG('OfflineBattle GUI Init Error:', str(e))
						cur_time = BigWorld.time()
						if 'last_time' not in _gun_state: _gun_state['last_time'] = cur_time
						dt = cur_time - _gun_state['last_time']
						_gun_state['last_time'] = cur_time
						_period_g = getattr(getattr(player, 'arena', None), 'period', 3)
						# Retail stops its gun rotator during countdown. Seed the fully
						# aimed marker once there, then resume per-frame updates in battle.
						_in_prebattle_g = _period_g < 3
						if _period_g == 3 or (_in_prebattle_g and not _gun_state.get('marker_in_prebattle', False)):
							_gun_state['prebattle_marker_seeded'] = False
						_gun_state['marker_in_prebattle'] = _in_prebattle_g

						# Real dispersion model (Avatar.getOwnVehicleShotDispersionAngle):
						#   ideal = base * sqrt(1 + (v*chassisMove)^2 + (vR*chassisRot)^2
						#                          + (wTurret*gunTurretRotation)^2)
						# with PER-TANK chassis/gun factors (already unit-converted to m/s and
						# rad/s by the descriptor parser). The old code called the Avatar
						# method, which never exists on the offline Account, so it ALWAYS fell
						# back to a generic linear 0.015/unit penalty identical for every tank.
						target_disp = _gun_state['base_dispersion']
						try:
							import math
							_d_td = loaded_models.get('td')
							_cm, _cr = _d_td.chassis['shotDispersionFactors']
							_gdf = _d_td.gun['shotDispersionFactors'] if isinstance(_d_td.gun, dict) else _d_td.gun.shotDispersionFactors
							_gt = _gdf['turretRotation']
							# Retail pre-battle controls let the player look around while the
							# vehicle and gun remain fully aimed. Camera-driven turret motion
							# must not bloom the reticle before period 3 begins.
							if _period_g == 3:
								v_speed, r_speed = player.getOwnVehicleSpeeds()
								_mv = v_speed * _cm
								_rv = r_speed * _cr
								_tv = _gun_state.get('turret_speed', 0.0) * _gt
							else:
								_mv = 0.0
								_rv = 0.0
								_tv = 0.0
							# Equipment: vStab dampens all movement bloom, snap shot the turret
							# term, smooth ride the hull-movement term.
							if _gun_state.get('has_vstab', False):
								_mv *= 0.8; _rv *= 0.8; _tv *= 0.8
							if _gun_state.get('has_snapshot', False):
								_tv *= 0.925
							if _gun_state.get('has_smooth_ride', False):
								_mv *= 0.96
							target_disp = _gun_state['base_dispersion'] * math.sqrt(1.0 + _mv * _mv + _rv * _rv + _tv * _tv)
						except Exception:
							if _period_g == 3:
								try:
									v_speed, r_speed = player.getOwnVehicleSpeeds()
									target_disp += abs(v_speed) * 0.015 + abs(r_speed) * 0.015
								except Exception:
									pass

						# Crew and module maluses belong on the TARGET circle, not on the current
						# one. They used to be applied once, to _gun_state['dispersion'], at gun
						# init - and the convergence below then pulled that straight back down to
						# target_disp within a second, so a gunner knocked out at minute three
						# never widened anything. A damaged gun widens it the same way.
						_aim_time = _gun_state['aim_time']
						try:
							_pm_ds = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
							if _pm_ds is not None:
								target_disp = target_disp * _crew_factor(_pm_ds, 'dispersion') * _module_factor(_pm_ds, 'dispersion')
								_aim_time = _aim_time * _module_factor(_pm_ds, 'aim_time')
						except Exception:
							pass
						if _gun_state['dispersion'] > target_disp:
							import math
							# Real decay: aimingFactor = start * exp((start-now)/aimingTime) - the
							# aim_time IS the time constant; the old 2.5x made aiming 2.5x too fast.
							factor = math.exp(-dt / max(_aim_time, 0.1))
							_gun_state['dispersion'] = target_disp + (_gun_state['dispersion'] - target_disp) * factor
						else:
							_gun_state['dispersion'] = min(_gun_state['dispersion'] + (target_disp - _gun_state['dispersion']) * 0.2, 5.0)

						# 2. Reload logic
						# The crew does not load during the countdown - the gun is empty until the
						# battle actually starts. On the frame period turns 3 the pending reload is
						# announced to the UI so the bar animates from full instead of appearing
						# half-way through.
						if _period_g != 3:
							_gun_state['load_started'] = False
						elif not _gun_state.get('load_started'):
							_gun_state['load_started'] = True
							if _gun_state['reloadTime'] > 0:
								try:
									_si_g = _gun_state.get('shot_index', 0)
									from gui import WindowsManager as _wmg
									_bwg = getattr(_wmg.g_windowsManager, 'battleWindow', None)
									_pg = getattr(_bwg, 'consumablesPanel', None) if _bwg is not None else None
									if _pg is not None:
										_pg.setCoolDownTime(_si_g, _gun_state['reloadTime'])
									_aimg = getattr(g_offline_aih, 'aim', None)
									if _aimg is not None:
										_aimg.setReloading(_gun_state['reloadTime'], None)
										_aimg.setAmmoStock(_gun_state['ammo_%d' % _si_g], 0, False)
								except Exception as _lse:
									LOG_DEBUG('initial load UI err:', str(_lse))
						if _gun_state['reloadTime'] > 0 and _period_g == 3:
							_gun_state['reloadTime'] -= dt
							if _gun_state['reloadTime'] <= 0:
								_gun_state['reloadTime'] = 0.0
								if _gun_state['clip'] == 0:
									# Never more rounds than are actually carried - a near-empty ammo type
									# used to refill to the full magazine size.
									_si_r = _gun_state.get('shot_index', 0)
									_gun_state['clip'] = min(_gun_state['clip_size'], _gun_state.get('ammo_%d' % _si_r, 0) or 0)

								# Reset UI cooldown and refresh ammo count when reload finishes
								try:
									from gui import WindowsManager
									panel = WindowsManager.g_windowsManager.battleWindow.consumablesPanel
									if panel:
										shot_idx = _gun_state.get('shot_index', 0)
										panel.setShellQuantityInSlot(shot_idx, _gun_state['ammo_%d' % shot_idx], _gun_state['clip'])
										panel.setCoolDownTime(shot_idx, 0.0)
									aim = getattr(g_offline_aih, 'aim', None)
									if aim:
										aim.setReloading(0.0, None)
										shot_idx = _gun_state.get('shot_index', 0)
										aim.setAmmoStock(_gun_state['ammo_%d' % shot_idx], _gun_state['clip'], True if _gun_state['clip'] == _gun_state['clip_size'] else False)

									try:
										if not hasattr(BigWorld.player(), 'soundNotifications'):
											import gui.IngameSoundNotifications as IngameSoundNotifications
											BigWorld.player().soundNotifications = IngameSoundNotifications.IngameSoundNotifications()
											BigWorld.player().soundNotifications.start()
										BigWorld.player().soundNotifications.play('gun_reloaded')
									except: pass
								except Exception:
									pass
					except Exception as e:
						LOG_DEBUG('OfflineBattle dispersion error:', str(e))

					# 3. Update Crosshair + AIH
					try:
						# Let the engine update the aim crosshair
						# Compute where the gun is actually pointing (offset start pos by 4.0m to avoid hitting our own tank hull!)
						try:
							td = loaded_models.get('td')
							turretOffs = td.hull['turretPositions'][0] + td.chassis['hullPosition']
							gunOffs = td.turret['gunPosition']
						except:
							turretOffs = Math.Vector3(0, 1.5, 0)
							gunOffs = Math.Vector3(0, 0.4, 1.0)

						turretWorldMatrix = Math.Matrix()
						turretWorldMatrix.setRotateY(turret_yaw[0])
						turretWorldMatrix.translation = turretOffs
						turretWorldMatrix.postMultiply(mock_veh.matrix)

						true_gun_pos = turretWorldMatrix.applyPoint(gunOffs)

						gunWorldMatrix = Math.Matrix()
						gunWorldMatrix.setRotateX(gun_pitch[0])
						gunWorldMatrix.translation = gunOffs
						gunWorldMatrix.postMultiply(turretWorldMatrix)

						gun_dir = gunWorldMatrix.applyToAxis(2)
						gun_dir.normalise()

						if 'gun_node_matrix' in loaded_models:
							# Store ONLY the true_gun_pos (pivot). NO rotation.
							# SniperCamera applies its own pitch/yaw from mouse input,
							# and then automatically applies the tank's configured pivotPos.
							_cam_m = Math.Matrix()  # identity = no rotation
							_cam_m.translation = true_gun_pos
							loaded_models['gun_node_matrix'].set(_cam_m)

						# Pass gun pos to rotator for Arty/Arcade raycasts
						if hasattr(player, 'gunRotator'):
							player.gunRotator._gun_pos = true_gun_pos
							player.gunRotator._gun_dir = gun_dir

						is_arty = False
						try: is_arty = 'SPG' in td.type.tags
						except: pass
						# The final marker is resolved from the gravity trajectory below. Keep
						# the desired world point only as a fail-closed fallback; projecting the
						# elevated barrel axis puts the marker above the shell's real impact.
						gun_target_pos = Math.Vector3(shot_point)
						gun_marker_dir = Math.Vector3(gun_dir)
						_pen_coll = None

						if _tick_counter[0] % 50 == 0:
							LOG_DEBUG('OfflineBattle.gun: target_pos=', gun_target_pos, 'dir=', gun_dir, 'pos=', true_gun_pos)

						# Hide vehicle in sniper mode using model.visible
						if hasattr(g_offline_aih, 'ctrl'):
							is_sniper = g_offline_aih.ctrl.__class__.__name__ == 'SniperControlMode'
							was_sniper = getattr(g_offline_aih, '_was_sniper', None)
							if is_sniper != was_sniper:
								g_offline_aih._was_sniper = is_sniper
								# (removed) This used to _offhangar_muzzle_player.stop() to kill a
								# muzzle flash left frozen when the hidden models reappeared - but
								# the gun's EffectsList also carries the SHOT SOUND, so zooming
								# right after firing (either direction) cut the bang off mid-play.
								# The freeze itself is gone: _play_muzzle_flash puts the player's
								# gun model on BigWorld.addAlwaysUpdateModel, so the flash animates
								# out even while the models are hidden/unrendered.
								for _part in ('chassis', 'hull', 'turret', 'gun'):
									_mdl = loaded_models.get(_part)
									if _mdl is not None:
										try: _mdl.visible = not is_sniper
										except: pass
								# Tank is hidden via .visible=False, so no need to push underground.
								# Keeping it at real position ensures 3D sounds (engine, gun) remain audible!

						# Calculate perfectly synchronous math_gun_world for raycast
						math_turret_pos = td.chassis['hullPosition'] + td.hull['turretPositions'][0]
						math_gun_world = Math.Matrix(mat).applyPoint(math_turret_pos)
						yaw_mat = Math.Matrix()
						yaw_mat.setRotateY(turret_yaw[0])
						math_gun_world += Math.Matrix(mat).applyVector(yaw_mat.applyVector(td.turret['gunPosition']))

						# Stock VehicleGunRotator walks the shell parabola chord by chord and
						# places the marker at the first dynamic/static hit. The gun axis is
						# intentionally elevated to cancel drop, so a straight ray is not the
						# impact path and appears high in arcade view.
						_marker_distance_origin = math_gun_world
						_marker_preview_fresh = False
						_marker_preview_allowed = (_period_g == 3 or not
							_gun_state.get('prebattle_marker_seeded', False))
						try:
							_marker_index = int(_gun_state.get('shot_index', 0) or 0)
							try:
								player.vehicleTypeDescriptor.activeGunShotIndex = _marker_index
							except Exception:
								pass
							_marker_start = math_gun_world
							_marker_velocity = None
							try:
								_marker_start, _marker_velocity = (
									player.gunRotator._VehicleGunRotator__getCurShotPosition())
							except Exception:
								pass
							_marker_distance_origin = Math.Vector3(_marker_start)
							_marker_shots = td.gun.get('shots', [])
							_marker_shot = (_marker_shots[_marker_index]
								if 0 <= _marker_index < len(_marker_shots) else None)
							_marker_speed = float(_marker_shot.get('speed', 0.0)
								if _marker_shot is not None else 0.0)
							if _marker_velocity is None:
								_marker_velocity = gun_dir.scale(_marker_speed)
							else:
								_marker_velocity = Math.Vector3(_marker_velocity)
							if _marker_speed <= 0.0001:
								_marker_speed = _marker_velocity.length
							_marker_gravity_value = abs(float(
								_marker_shot.get('gravity', 0.0)
								if _marker_shot is not None else 0.0))
							_marker_gravity = Math.Vector3(
								0.0, -_marker_gravity_value, 0.0)
							_marker_max_distance = float(
								_marker_shot.get('maxDistance', 720.0)
								if _marker_shot is not None else 720.0)
							if _marker_max_distance <= 1.0:
								_marker_max_distance = 720.0
							# The desired camera point is not a trajectory boundary. If the gun
							# cannot depress to it, stopping at its estimated flight time leaves
							# the marker suspended above the shell's later first impact. The
							# preview helper owns the Euclidean maxDistance boundary; this is
							# only a hard runaway guard matching the live projectile runtime.
							_marker_max_time = max(4.0, min(
								20.0, 2500.0 / max(1.0, _marker_speed) + 4.0))
							_marker_now = float(BigWorld.time())
							_marker_preview_cached = (
								_gun_state.get('marker_preview_index') == _marker_index and
								_marker_now - float(_gun_state.get(
									'marker_preview_at', -999.0)) < 0.1)
							_marker_preview = None
							if _marker_preview_cached:
								_marker_preview = _gun_state.get('marker_preview')
							if _marker_preview_allowed and not _marker_preview_cached:
								_marker_perf = _offh_perf_start()
								try:
									_marker_preview = _offh_player_gun_marker_impact(
										_marker_start, _marker_velocity, _marker_gravity,
										globals().get('G_MOCK_VEHICLES', {}) or {},
										getattr(player, 'playerVehicleID', -1),
										getattr(player, 'team', getattr(
											player, '_offhangar_team', 1)),
										_marker_max_time, _marker_max_distance,
										profile_candidates=(_marker_perf is not None))
								finally:
									_offh_perf_stop('player_gun_marker', _marker_perf)
								_gun_state['marker_preview'] = _marker_preview
								_gun_state['marker_preview_at'] = _marker_now
								_gun_state['marker_preview_index'] = _marker_index
								_marker_preview_fresh = _marker_preview is not None
								if _marker_preview is None:
									_last_marker_error = float(_gun_state.get(
										'marker_preview_error_at', -999.0))
									if _marker_now - _last_marker_error >= 1.0:
										_gun_state['marker_preview_error_at'] = _marker_now
										LOG_ERROR('OfflineBattle ballistic marker preview failed')
							if _marker_preview is not None:
								gun_target_pos = _marker_preview[0]
								gun_marker_dir = _marker_preview[1]
								_pen_coll = _marker_preview[3]
						except Exception as _marker_error:
							LOG_DEBUG('OfflineBattle ballistic marker error:',
								str(_marker_error))
							gun_target_pos = Math.Vector3(shot_point)
							gun_marker_dir = gun_target_pos - math_gun_world
							if gun_marker_dir.length > 0.0001:
								gun_marker_dir.normalise()
							else:
								gun_marker_dir = Math.Vector3(gun_dir)
							_pen_coll = None

						# UPDATE CROSSHAIR
						# dead/spectating -> skip the dynamic gun-marker (dispersion reticle) refresh;
						# leaving it on re-shows it every frame + fights the post-mortem hide below.
						_refresh_gun_marker = (_marker_preview_fresh and
							(_period_g == 3 or not _gun_state.get(
								'prebattle_marker_seeded', False)))
						if hasattr(g_offline_aih, 'ctrl') and not getattr(player, '_is_dead', False):
							try:
								if hasattr(player, 'gunRotator'):
									player.gunRotator.dispersionAngle = _gun_state['dispersion']

								dist_m = (gun_target_pos - _marker_distance_origin).length
								size_m = _gun_state['dispersion'] * dist_m * 2.0
								if _refresh_gun_marker and hasattr(player, 'gunRotator'):
									player.gunRotator.markerInfo = (
										gun_target_pos, gun_marker_dir, size_m)

								if _refresh_gun_marker:
									g_offline_aih.updateGunMarker(
										gun_target_pos, gun_marker_dir, size_m,
										0.1, _pen_coll)
							except Exception as e:
								LOG_DEBUG('OfflineBattle updateGunMarker error:', str(e), 'pos:', true_gun_pos, 'dir:', gun_dir)
							if _refresh_gun_marker:
								try:
									g_offline_aih.updateGunMarker2(
										gun_target_pos, gun_marker_dir, size_m,
										0.1, _pen_coll)
								except Exception as e:
									pass
							if _period_g != 3:
								_gun_state['prebattle_marker_seeded'] = True

							if _gun_state.get('tick_counter', 0) % 60 == 0:
								import debug_utils
								try:
									cam_m_debug = Math.Matrix(BigWorld.camera().matrix)
									debug_utils.LOG_DEBUG("DEBUG DIR", "cam_pos:", cam_m_debug.translation, "gun_pos:", true_gun_pos)
									debug_utils.LOG_DEBUG("DEBUG DIR", "cam_dir:", cam_m_debug.applyToAxis(2), "gun_dir:", gun_dir)
									debug_utils.LOG_DEBUG("DEBUG DIR", "tYaw:", tYaw, "gPitch:", gPitch)
								except: pass
							_gun_state['tick_counter'] = _gun_state.get('tick_counter', 0) + 1

							# Synchronize ammo UI when switching control modes
							aim = getattr(g_offline_aih, 'aim', None)
							if aim and aim != _gun_state.get('last_aim'):
								_gun_state['last_aim'] = aim
								try:
									if hasattr(aim, 'setClipParams'): aim.setClipParams(_gun_state['clip_size'], 1)
									if hasattr(aim, 'setAmmoStock'): aim.setAmmoStock(_gun_state['ammo_%d' % _gun_state.get('shot_index', 0)], _gun_state['clip'], False)
									# setReloading hands Flash a DURATION and Flash animates it locally, so
									# pushing the pending reload here started the bar running during the
									# countdown - the crew is not loading yet. Only announce it once the
									# battle is live; the period-3 handler above starts the bar on that frame.
									if hasattr(aim, 'setReloading'):
										if _gun_state['reloadTime'] > 0 and getattr(getattr(player, 'arena', None), 'period', 3) == 3:
											aim.setReloading(_gun_state['reloadTime'], None)
										else:
											aim.setReloading(0.0, None)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())
					except Exception as e:
						import traceback
						LOG_DEBUG('OfflineBattle fatal gun error:', traceback.format_exc())

				# Update turret rotation (via node matrix)
				turret_mat = loaded_models.get('turret_mat')
				if turret_mat is not None:
					turret_mat.setRotateYPR((turret_yaw[0], 0, 0))

				# Update gun pitch (via node matrix)
				gun_mat = loaded_models.get('gun_mat')
				if gun_mat is not None:
					gun_mat.setRotateYPR((0, gun_pitch[0], 0))



				# --- Update turret_matrix for camera/AIH ---
				tm = Math.Matrix()
				tm.setRotateYPR((veh_yaw[0] + turret_yaw[0], gun_pitch[0], 0))
				try:
					td = loaded_models.get('td')
					turret_offs = td.hull['turretPositions'][0] + td.chassis['hullPosition']
					tm.translation = mock_veh.matrix.applyPoint(turret_offs)
				except:
					tm.translation = Math.Vector3(veh_pos[0], veh_pos[1] + 2.0, veh_pos[2])
				turret_matrix.set(tm)

				tm_local = Math.Matrix()
				tm_local.setRotateYPR((turret_yaw[0], gun_pitch[0], 0))
				turret_matrix_local.set(tm_local)

				_offh_perf_stop('player_gun', _perf_player_gun)
				_perf_player_effects = _offh_perf_start()
				# --- PLAYER FIRE LOGIC ---
				try:
					_player_mock = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
					_sync_burn_and_death(_player_mock, loaded_models.get('hull'), loaded_models.get('td'))
					# Crew auto-repair: destroyed modules climb back to functional over the
					# repair time, damaged ones regen to the same cap. Without this the player
					# would stay crippled for the rest of the battle after a single crit.
					try:
						if _player_mock is not None and getattr(_player_mock, 'health', 0) > 0:
							_tick_module_repair(_player_mock, loaded_models.get('td'), dt, True)
					except Exception as _mre:
						LOG_DEBUG('player module repair err:', str(_mre))
					_sync_engine_exhaust(_player_mock, loaded_models.get('hull'), loaded_models.get('td'), _veh_velocity[0])
					if _player_mock and getattr(_player_mock, 'is_on_fire', False) and getattr(_player_mock, 'health', 0) > 0:
						# Fires burn out on their own. device_damage.FIRE_DURATION_SECONDS.
						try:
							import BigWorld as _bwf
							from gui.mods.offhangar import device_damage as _DDf
							_fs = getattr(_player_mock, '_fire_started', None)
							if _fs is not None and (_bwf.time() - _fs) >= _DDf.FIRE_DURATION_SECONDS:
								_offh_extinguish(_player_mock, True, 'burnt out')
						except Exception:
							pass
						cur_timer = getattr(_player_mock, '_fire_timer', 0.0)
						if cur_timer is None: cur_timer = 0.0
						_player_mock._fire_timer = float(cur_timer) + 0.02
						if _player_mock._fire_timer >= 1.0:
							_player_mock._fire_timer -= 1.0
							fire_dmg = max(1, int(_player_mock.maxHealth * 0.05))
							# The flames, the panel and the extinguisher all still work in
							# test mode; only the HP drain is held back.
							if not _offh_module_test_mode():
								_player_mock.health -= fire_dmg
								_offh_drop_capture_for_vehicle(
									_player_mock,
									getattr(_player_mock, 'last_killer_id', None),
									'fire damage')

							try:
								import gui.WindowsManager
								bw = gui.WindowsManager.g_windowsManager.battleWindow
								import debug_utils
								debug_utils.LOG_DEBUG("PLAYER_FIRE_TICK! bw: ", bw)
								if bw:
									debug_utils.LOG_DEBUG("BW_DIR: ", dir(bw))
									if hasattr(bw, 'damagePanel'):
										debug_utils.LOG_DEBUG("DAMAGE_PANEL_DIR: ", dir(bw.damagePanel))
										bw.damagePanel.updateHealth(_player_mock.health)
							except: pass

							if _player_mock.health <= 0:
								_player_mock.health = 0
								player.arena.onVehicleKilled(getattr(_player_mock, 'id', player.playerVehicleID), getattr(_player_mock, 'last_killer_id', -1), 2)
								_player_mock.is_on_fire = False
								try:
									import gui.WindowsManager
									bw = gui.WindowsManager.g_windowsManager.battleWindow
									if hasattr(bw, 'damagePanel'):
										bw.damagePanel._DamagePanel__callFlash('onFireInVehicle', [False])
								except: pass

							if hasattr(player, 'vehicle') and player.vehicle:
								player.vehicle.health = _player_mock.health
								try: player.guiSessionProvider.invalidateVehicleState(1, player.playerVehicleID, _player_mock.health, _player_mock.health)
								except: pass
				except: pass

				_offh_perf_stop('player_effects', _perf_player_effects)
				_offh_perf_stop('player_loop', _perf_player_loop)
				# --- BOT AI (Advanced Physics) ---
				import math, random
				dt = _frame_dt # real frame delta: bot speed/reload no longer depends on FPS
				_network_bot_role = _offh_network_bot_role(player)
				_network_simulation_authority = (
					_network_bot_role in ('local', 'authority'))
				# Unknown ownership and promotion handoff are neither authority nor relay.
				# Hold the shared lineup until a complete canonical role is known.
				_is_network_replica = not _network_simulation_authority
				_replica_native_manager = None
				if _network_bot_role == 'replica':
					release_native_bots_for_replica(player)
				if _network_bot_role in ('local', 'authority', 'replica'):
					try:
						from gui.mods.offhangar.network_battle import advance_network_smoothing
						_offh_perf_call('network_smoothing', advance_network_smoothing,
						                player, mock_vehicles, dt)
					except Exception:
						pass
				# Use one timestamp for the whole rendered frame. TerrainNavigator's
				# scheduler is explicitly once-per-frame; calling BigWorld.time() again
				# for every bot made tiny microsecond differences bypass that guard and
				# advanced the A* budget up to 30 times in one frame.
				_ai_now = BigWorld.time()
				_ai_space_id = _offh_bspace()
				_ai_driver = _offh_ai_driver()
				_player_vehicle_id = getattr(player, 'playerVehicleID', -1)
				_player_team = int(getattr(player, '_offhangar_team', 1) or 1)
				_battle_active = (
					getattr(getattr(player, 'arena', None), 'period', 3) == 3)
				_ai_driver.set_battle_active(_battle_active)
				# F6 runs one invisible, isolated retail-physics capability probe.
				# It never replaces a production bot; the result is used only to decide
				# whether a later build may safely move bot integration into C++.
				if _battle_active and _network_simulation_authority:
					try:
						from gui.mods.offhangar import native_vehicle_physics_probe as _native_physics_probe
						if _native_physics_probe.is_requested():
							_native_physics_probe.maybe_run(
								player, loaded_models.get('td'),
								Math.Vector3(veh_pos[0], veh_pos[1], veh_pos[2]),
								veh_yaw[0], _ai_space_id, _offh_my_gen[0])
					except Exception as _native_probe_tick_error:
						if not globals().get('g_offh_native_probe_tick_error', False):
							globals()['g_offh_native_probe_tick_error'] = True
							LOG_ERROR(
								'NATIVE_PHYSICS_PROBE tick failed: %s' %
								str(_native_probe_tick_error))
				# Validate and materialise the shipped foliage index during the loading /
				# countdown period. The old lazy path loaded it only after the first
				# observer-target pair happened to need a >50 m concealment calculation,
				# which made a healthy battle log look as if foliage were disabled.
				if 'g_offh_spot_foliage' not in globals():
					_offh_spot_foliage(player)
				_ai_director = None
				try:
					if _network_simulation_authority:
						_ai_director = _offh_ai_director(player)
					if _ai_director is not None:
						_ai_navigator = _offh_ai_navigator(_ai_director)
						_server_navigation_fresh = (
							bool(getattr(player,
								'_offhangar_network_server_navigation_complete', False)) and
							time.time() - float(getattr(
								player, '_offhangar_network_server_navigation_at',
								0.0) or 0.0) < 1.5)
						if _ai_navigator is not None and not _server_navigation_fresh:
							try:
								_offh_perf_call('nav_tick', _ai_navigator.tick, _ai_now)
							except Exception as _ai_nav_tick_error:
								_offh_ai_navigation_failure('tick', _ai_nav_tick_error)
								globals().pop('g_offh_terrain_navigator', None)
								globals()['g_offh_ai_navigation_disabled'] = True
						_offh_perf_call('contacts', _offh_ai_refresh_contacts,
						                _ai_director, player, mock_vehicles, veh_pos,
						                loaded_models.get('td'), _ai_now)
						# Registration order affects route capacity. Sort by entity id so
						# an authority failover reconstructs the same assignments.
						_perf_ai_setup = _offh_perf_start()
						for _ai_eid in sorted(mock_vehicles.keys()):
							_ai_mock = mock_vehicles.get(_ai_eid)
							if (_ai_eid == _player_vehicle_id or
							        _ai_mock is None or
							        not getattr(_ai_mock, 'isAlive', False) or
							        getattr(_ai_mock, '_network_remote', False)):
								continue
							if int(_ai_eid) in _ai_director.agents:
								continue
							_ai_info = getattr(_ai_mock, 'publicInfo', None) or {}
							_ai_team = getattr(_ai_mock, '_bot_team',
							                        _ai_info.get('team', 2))
							_ai_td = getattr(_ai_mock, 'typeDescriptor', None) or loaded_models.get('td')
							_ai_director.register(
								_ai_eid, _ai_team, _ai_td,
								_ai_info.get('name', 'Bot %s' % _ai_eid))
						_offh_perf_stop('ai_setup', _perf_ai_setup)
				except Exception as _ai_init_error:
					if not globals().get('g_offh_ai_init_error_logged', False):
						globals()['g_offh_ai_init_error_logged'] = True
						try:
							import traceback
							from gui.mods.offhangar.logging import LOG_ERROR as _AI_INIT_ERROR
							_AI_INIT_ERROR('OfflineBattle SMART_AI initialization failed: %s\n%s' % (
								str(_ai_init_error), traceback.format_exc()))
						except Exception:
							pass
					_ai_director = None
				if _ai_director is None:
					try:
						_offh_perf_call('spotting_player',
						                _offh_spot_refresh_sixth_sense,
						                player, _ai_now)
					except Exception:
						pass
				# Capture moving bodies once per rendered frame. The old inner loops read
				# every model, descriptor and velocity again for every bot (roughly 29x29
				# Python object walks) before doing the same 24 m distance filter.
				_perf_traffic = _offh_perf_start()
				_driver_frame = {}
				_nav_frame = {}
				_collision_bodies = {}
				_collision_max_radius = 4.0
				_local_ai_ids = []
				_python_collision_ids = []
				_native_body_manager_frame = None
				if _network_simulation_authority:
					try:
						from gui.mods.offhangar import native_bot_physics as _native_body_manager_frame
					except Exception:
						_native_body_manager_frame = None
				for _frame_eid, _frame_vehicle in mock_vehicles.iteritems():
					if (_frame_vehicle is None or
					        not getattr(_frame_vehicle, 'isAlive', False)):
						continue
					try:
						_frame_position = (
							float(_frame_vehicle.position.x),
							float(_frame_vehicle.position.y),
							float(_frame_vehicle.position.z))
						_frame_yaw = float(_frame_vehicle.yaw)
						_frame_speed = float(
							getattr(_frame_vehicle, '_veh_velocity', 0.0) or 0.0)
						_frame_collision_cache = getattr(
							_frame_vehicle, '_offh_collision_frame_cache', None)
						if _frame_collision_cache is None:
							_frame_td = getattr(_frame_vehicle, 'typeDescriptor', None)
							_frame_shape = _VC.chassis_shape(_frame_td)
							_frame_half_width = float(_frame_shape[0])
							_frame_half_length = float(_frame_shape[1])
							_frame_radius = math.sqrt(
								_frame_half_width * _frame_half_width +
								_frame_half_length * _frame_half_length)
							_frame_params = getattr(_frame_vehicle, '_phys_params', None)
							if _frame_params is None:
								_frame_params = _PHY.derive_params(_frame_td)
								_frame_vehicle._phys_params = _frame_params
							_frame_inv_mass = 1.0 / max(
								float(_frame_params.get('mass', 25000.0)), 1.0)
							_frame_collision_cache = (
								_frame_shape, _frame_half_width, _frame_half_length,
								_frame_radius, _frame_inv_mass)
							_frame_vehicle._offh_collision_frame_cache = _frame_collision_cache
						else:
							(_frame_shape, _frame_half_width, _frame_half_length,
							 _frame_radius, _frame_inv_mass) = _frame_collision_cache
						if _network_simulation_authority:
							_frame_velocity = (
								math.sin(_frame_yaw) * _frame_speed, 0.0,
								math.cos(_frame_yaw) * _frame_speed)
							_frame_traffic_id = _ai_driver.traffic_identity(
								_frame_eid, getattr(_frame_vehicle, '_network_bot_id', None))
							_driver_body = getattr(
								_frame_vehicle, '_offh_driver_frame_body', None)
							if _driver_body is None:
								_driver_body = {
									'id': _frame_traffic_id,
									'team': int(_player_team if _frame_eid == _player_vehicle_id else
										(getattr(_frame_vehicle, '_bot_team', None) or
										 (getattr(_frame_vehicle, 'publicInfo', None) or {}).get('team', 0) or 0)),
									'speed': _frame_speed,
									'is_human': bool(_frame_eid == _player_vehicle_id or
										getattr(_frame_vehicle, '_network_remote', False)),
									'position': _frame_position,
									'yaw': _frame_yaw,
									'velocity': _frame_velocity,
									'half_length': _frame_half_length,
									'half_width': _frame_half_width,
								}
								_frame_vehicle._offh_driver_frame_body = _driver_body
							else:
								_driver_body['id'] = _frame_traffic_id
								_driver_body['team'] = int(
									_player_team if _frame_eid == _player_vehicle_id else
									(getattr(_frame_vehicle, '_bot_team', None) or
									 (getattr(_frame_vehicle, 'publicInfo', None) or {}).get('team', 0) or 0))
								_driver_body['speed'] = _frame_speed
								_driver_body['is_human'] = bool(
									_frame_eid == _player_vehicle_id or
									getattr(_frame_vehicle, '_network_remote', False))
								_driver_body['position'] = _frame_position
								_driver_body['yaw'] = _frame_yaw
								_driver_body['velocity'] = _frame_velocity
							_driver_frame[_frame_eid] = _driver_body
							if (_frame_eid != _player_vehicle_id and
									not getattr(_frame_vehicle, '_network_remote', False)):
								_local_ai_ids.append(int(_frame_eid))
								_native_frame_required = _offh_native_movement_required(
									player, _frame_vehicle, _network_bot_role)
								_python_frame_allowed = _offh_python_movement_allowed(
									player, _frame_vehicle, _network_bot_role)
								if (_python_frame_allowed and
									not _native_frame_required and
									(_native_body_manager_frame is None or
									not _native_body_manager_frame.claims_movement(
										_frame_vehicle))):
									_python_collision_ids.append(int(_frame_eid))
						_collision_body = getattr(
							_frame_vehicle, '_offh_collision_frame_body', None)
						_native_collision_owner = bool(
							_frame_eid != _player_vehicle_id and (
							_offh_native_movement_required(
								player, _frame_vehicle, _network_bot_role) or
							(_native_body_manager_frame is not None and
							_native_body_manager_frame.claims_movement(_frame_vehicle))))
						if _collision_body is None:
							_collision_body = {
								'position': _frame_position,
								'shape': _frame_shape,
								'radius': _frame_radius,
								'inv_mass': _frame_inv_mass,
								'native_owner': _native_collision_owner,
							}
							_frame_vehicle._offh_collision_frame_body = _collision_body
						else:
							_collision_body['position'] = _frame_position
							_collision_body['native_owner'] = _native_collision_owner
						_collision_bodies[_frame_eid] = _collision_body
						_collision_max_radius = max(
							_collision_max_radius, _frame_radius)
						if _network_simulation_authority:
							_nav_frame[_frame_eid] = _frame_position
					except Exception:
						continue
				_perf_traffic_index = _offh_perf_start()
				_traffic_spatial[0] = _VC.build_spatial_index(_driver_frame)
				# Two maximum hull radii plus four metres of per-frame motion slop
				# guarantees that a colliding pair lies in the same or an adjacent cell.
				_collision_cell_size = _collision_max_radius * 2.0 + 4.0
				_collision_frame[0] = _collision_bodies
				_collision_spatial[0] = _VC.build_spatial_index(
					_collision_bodies, _collision_cell_size)
				_offh_perf_stop('traffic_index', _perf_traffic_index)
				_perf_traffic_candidates = _offh_perf_start()
				_collision_candidates[0] = _VC.unique_candidate_map(
					_collision_spatial[0], _collision_bodies,
					_python_collision_ids)
				_offh_perf_stop('traffic_candidates', _perf_traffic_candidates)
				_offh_perf_stop('traffic_snapshot', _perf_traffic)
				_ai_frame_budget = _offh_ai_frame_budget_plan(_local_ai_ids, dt)
				_order_refresh_ids = _ai_frame_budget['order']
				_nav_refresh_ids = _ai_frame_budget['nav']
				_driver_refresh_ids = _ai_frame_budget['driver']
				_tree_refresh_ids = _ai_frame_budget['tree']
				_order_refresh_horizon = _ai_frame_budget['order_horizon']
				_nav_refresh_horizon = _ai_frame_budget['nav_horizon']
				_driver_refresh_horizon = _ai_frame_budget['driver_horizon']
				if _native_body_manager_frame is not None:
					_offh_perf_call(
						'native_simulation',
						_native_body_manager_frame.simulate_frame,
						mock_vehicles, dt, _ai_now)
				_perf_bot_loop = _offh_perf_start()
				# Stable entity order also makes every bot-bot collision pair flow from
				# the lower id to the higher id, whose queued reciprocal correction is
				# therefore consumed later in this same frame.
				for eid in sorted(mock_vehicles):
					m_veh = mock_vehicles[eid]
					if eid != _player_vehicle_id and getattr(m_veh, 'isAlive', False):
						try:
							if ((not _network_simulation_authority) and
									(getattr(m_veh, '_network_remote', False) or
									 getattr(m_veh, '_network_shared_bot', False))):
								continue
							if getattr(m_veh, '_network_remote', False):
								# Remote vehicles are advanced by network_battle's
								# server snapshots, never by this client's bot AI. They
								# still pass through local spotting like every enemy NPC.
								try:
									from gui.mods.offhangar.network_battle import update_remote_spotting
									update_remote_spotting(player, m_veh)
								except Exception:
									pass
								continue
							if getattr(m_veh, '_network_shared_bot', False):
								if _network_bot_role != 'authority':
									if _network_bot_role == 'replica':
										try:
											from gui.mods.offhangar.network_battle import update_remote_spotting
											update_remote_spotting(player, m_veh)
										except Exception:
											pass
									continue
							my_team = getattr(m_veh, '_bot_team', m_veh.publicInfo.get('team', 2) if getattr(m_veh, 'publicInfo', None) is not None else 2)
							_td = getattr(m_veh, 'typeDescriptor', None) or loaded_models.get('td')
							target_pos = None
							drive_pos = None
							face_pos = None
							_ai_order = None
							_ai_fire_allowed = False
							_ai_fire_range = 150.0
							_ai_target_id = None
							_ai_throttle_override = None
							_ai_shell_index = 0
							_tactical_mode = 'server_wait'
							_artillery_solution = None
							_direct_fire_solution = None
							_nav_paused = False
							_ai_hull_aiming = False
							_ai_server_wait = False
							_native_hazard_recovery_pre = bool(
								_battle_active and getattr(
									m_veh, '_offh_native_hazard_recovering', False))
							# INIT BOT STATES
							if getattr(m_veh, '_veh_velocity', None) is None: m_veh._veh_velocity = 0.0
							if getattr(m_veh, '_veh_turn_velocity', None) is None: m_veh._veh_turn_velocity = 0.0

							if _ai_director is not None:
								_perf_ai_order = _offh_perf_start()
								try:
									_public_info = getattr(m_veh, 'publicInfo', None) or {}
									_display_name = _public_info.get('name', 'Bot %s' % eid)
									_network_ai = getattr(player, '_offhangar_network_client', None)
									_order_source = ('network' if _network_ai is not None and
									                 getattr(_network_ai, 'ready', False) else 'local')
									_order_cache = getattr(m_veh, '_offh_ai_order_cache', None)
									_order_cache_matches = (
										isinstance(_order_cache, tuple) and len(_order_cache) == 3 and
										_order_cache[0] == _order_source)
									_order_cache_fresh = (
										_order_cache_matches and
										float(_ai_now) < float(_order_cache[1]))
									_order_refresh_now = _offh_ai_refresh_due(
										eid in _order_refresh_ids, _order_cache_matches,
										_order_cache_fresh,
										_order_cache[1] if _order_cache_matches else 0.0,
										_ai_now, _order_refresh_horizon)
									if _order_cache_matches and not _order_refresh_now:
										_ai_order = _order_cache[2]
										if not _order_cache_fresh:
											_offh_perf_count('order_deferred')
									elif not _order_refresh_now:
										# A cold start or authority-source change waits at most one
										# round-robin horizon. Holding is safer than letting all 29
										# bots synchronously parse a fresh order on the same frame.
										_offh_perf_count('order_deferred')
									else:
										_offh_perf_count('order_refresh')
										if _order_source == 'network':
											from gui.mods.offhangar.network_battle import authoritative_bot_order
											_ai_order = authoritative_bot_order(player, m_veh)
										else:
											_ai_order = _ai_director.order_for(
												eid,
												(m_veh.position.x, m_veh.position.y, m_veh.position.z),
												m_veh.yaw, getattr(m_veh, 'health', 1),
												getattr(m_veh, 'maxHealth', 1), _ai_now)
											_ai_order = _offh_ai_apply_local_cover(
												eid,
												(m_veh.position.x, m_veh.position.y, m_veh.position.z),
												_ai_order, _ai_now)
										if _ai_order is not None:
											# Route assignments change at strategic cadence. Keep
											# point-blank/aim orders responsive, but do not rebuild an
											# unchanged route dictionary for 29 bots every render frame.
											_order_mode = str(
												_ai_order.get('combat_mode', 'route'))
											_order_is_combat = bool(
												_ai_order.get('target_id') is not None or
												_ai_order.get('fire_allowed') or
												_order_mode not in ('route', 'advance'))
											_order_interval = (0.075 if _order_is_combat
											                   else 0.160)
											m_veh._offh_ai_order_cache = (
												_order_source, _offh_ai_cache_deadline(
													_ai_now, eid, _order_interval, 1,
													_order_cache is None),
												_ai_order)
									if _ai_order is None:
										_hold = (m_veh.position.x, m_veh.position.y, m_veh.position.z)
										_ai_order = {
											'aim_position': _hold, 'move_position': _hold,
											'face_position': _hold, 'fire_allowed': False,
											'fire_range': 0.0, 'target_id': None,
											'throttle_override': None, 'shell_index': 0,
											'combat_mode': 'server_wait', 'route_id': 'server_wait',
											'route_index': 0, 'route_anchor': _hold,
										}
										_ai_server_wait = True
									target_pos = _ai_order.get('aim_position')
									drive_pos = _ai_order.get('move_position')
									face_pos = _ai_order.get('face_position', target_pos)
									_ai_fire_allowed = bool(_ai_order.get('fire_allowed', False))
									_ai_fire_range = float(_ai_order.get('fire_range', 150.0))
									_ai_target_id = _ai_order.get('target_id')
									_ai_throttle_override = _ai_order.get('throttle_override')
									_ai_shell_index = max(0, int(_ai_order.get('shell_index', 0) or 0))
									_tactical_mode = str(_ai_order.get('combat_mode', 'route'))
									if _tactical_mode in ('route', 'advance'):
										_offh_perf_count('tactic_route')
									elif _tactical_mode in ('engage', 'cover_hold'):
										_offh_perf_count('tactic_hold')
									else:
										_offh_perf_count('tactic_manoeuvre')
								except Exception as _ai_bot_error:
									if not getattr(m_veh, '_offh_ai_error_logged', False):
										m_veh._offh_ai_error_logged = True
										LOG_DEBUG('OfflineBattle.SMART_AI bot decision error id=%s: %s' % (
											eid, str(_ai_bot_error)))
									_ai_order = None
								_offh_perf_stop('ai_order', _perf_ai_order)

							if _ai_order is None:
								# A planner error must never restore the removed omniscient chase.
								# Hold position with fire disabled until the next valid order.
								target_pos = (m_veh.position.x, m_veh.position.y, m_veh.position.z)
								drive_pos = target_pos
								face_pos = target_pos
								_ai_server_wait = True

							_current_bot_pos = (
								m_veh.position.x, m_veh.position.y, m_veh.position.z)
							target_pos, drive_pos, face_pos, _stop_without_route = (
									_ai_driver.resolve_order_positions(
									_current_bot_pos, target_pos, drive_pos, face_pos))
							if _stop_without_route:
								# Only an order without both aim and movement is an idle hold.
								m_veh._veh_velocity = max(0.0, m_veh._veh_velocity - 20.0 * dt)
								m_veh._veh_turn_velocity = 0.0
							# Hierarchical navigation: strategic routes choose the battle lane;
							# a shared lazy A* graph connects their sparse anchors without
							# crossing cliffs, water gaps or solid geometry. Nearby tanks remain
							# the responsibility of the fast per-frame separation/feeler layer.
							if _ai_director is not None and _ai_order is not None:
								try:
									_requested_drive_pos = drive_pos
									_nav_dx = drive_pos[0] - m_veh.position.x
									_nav_dz = drive_pos[2] - m_veh.position.z
									_nav_distance = math.sqrt(_nav_dx*_nav_dx + _nav_dz*_nav_dz)
									if _nav_distance > 15.0:
										_nav_mode = _ai_order.get('combat_mode', 'route')
										_nav_index = int(_ai_order.get('route_index', 0))
										# LAN server route orders used the name "advance" while the
										# local planner used "route". Both are the same strategic state.
										if _nav_mode == 'base_defense':
											_nav_key = ('local', int(eid), _nav_mode,
												_ai_order.get('defense_base_id'))
											_nav_anchor = None
										elif (_nav_mode in ('route', 'advance') and
										        _ai_target_id is None):
											_nav_key = ('route', int(my_team),
											            _ai_order.get('route_id', 'direct'), _nav_index)
											_nav_anchor = (_ai_order.get('route_anchor')
											               if _nav_index > 0 else None)
										else:
											_nav_key = ('local', int(eid), _nav_mode,
											            _ai_order.get('target_id'))
											_nav_anchor = None
										_navigator = _offh_ai_navigator(_ai_director)
										_server_nav_target = getattr(
											m_veh, '_network_navigation_target', None)
										_server_nav_source = str(getattr(
											m_veh, '_network_navigation_source', '') or '')
										_server_nav_revision = int(getattr(
											m_veh, '_network_navigation_revision', -1) or 0)
										_server_nav_time = float(getattr(
											m_veh, '_network_navigation_time', 0.0) or 0.0)
										try:
											_current_order_revision = int(getattr(
												_network_client, 'bot_order_revision', 0) or 0)
										except Exception:
											_current_order_revision = -1
										_server_nav_ready = (
											_server_nav_target is not None and
											_server_nav_source in ('server_baked', 'server_hold') and
											_server_nav_revision == _current_order_revision and
											time.time() - _server_nav_time < 1.5)
										if _server_nav_ready:
											drive_pos = tuple(_server_nav_target)
											_nav_paused = False
											_offh_perf_count('nav_server')
										elif _navigator is not None:
											_current_nav_pos = (
												m_veh.position.x, m_veh.position.y, m_veh.position.z)
											_nav_cache_key = (
												tuple(_nav_key), _navigator.grid.cell_for(drive_pos))
											_nav_cache = getattr(m_veh, '_offh_nav_target_cache', None)
											_use_nav_cache = False
											_pending = False
											_nav_cache_matches = (
												isinstance(_nav_cache, tuple) and len(_nav_cache) == 3 and
												_nav_cache[0] == _nav_cache_key)
											_nav_cache_fresh = (
												_nav_cache_matches and
												float(_ai_now) < float(_nav_cache[1]))
											_nav_refresh_now = _offh_ai_refresh_due(
												eid in _nav_refresh_ids, _nav_cache_matches,
												_nav_cache_fresh,
												_nav_cache[1] if _nav_cache_matches else 0.0,
												_ai_now, _nav_refresh_horizon)
											if isinstance(_nav_cache, tuple) and len(_nav_cache) == 3:
												_cached_dx = float(_nav_cache[2][0]) - float(m_veh.position.x)
												_cached_dz = float(_nav_cache[2][2]) - float(m_veh.position.z)
												_cached_target_ahead = (
													_cached_dx * _cached_dx + _cached_dz * _cached_dz > 4.0)
												_use_nav_cache = (
													_cached_target_ahead and
													not _nav_refresh_now)
											if _use_nav_cache:
												drive_pos = _nav_cache[2]
												if not _nav_cache_fresh or not _nav_cache_matches:
													_offh_perf_count('nav_deferred')
											elif not _nav_refresh_now:
												# No safe stale waypoint exists yet. Wait for this bot's
												# bounded A* slot instead of bypassing navigation directly.
												drive_pos = _current_nav_pos
												_pending = True
												_offh_perf_count('nav_deferred')
											else:
												_offh_perf_count('nav_refresh')
												_nearby_ids = _VC.nearby_ids(
													_traffic_spatial[0], m_veh.position.x,
													m_veh.position.z)
												_avoid_points = [
													_nav_frame[_nav_eid] for _nav_eid in _nearby_ids
													if _nav_eid != eid and _nav_eid in _nav_frame]
												drive_pos = _offh_perf_call(
													'nav_target', _navigator.next_target, eid,
													_current_nav_pos, drive_pos, _nav_key, _ai_now,
													_nav_anchor, _avoid_points)
												_pending = _navigator.navigation_paused(
													_current_nav_pos, _requested_drive_pos, drive_pos)
												_cache_interval = 0.04 if _pending else 0.1125
												m_veh._offh_nav_target_cache = (
													_nav_cache_key, _offh_ai_cache_deadline(
														_ai_now, eid, _cache_interval, 2,
														_nav_cache is None),
													tuple(drive_pos))
											_nav_paused = _navigator.navigation_paused(
												_current_nav_pos,
												_requested_drive_pos, drive_pos)
								except Exception as _nav_error:
									# Fall back to the old reactive steering intent. LocalDriver still
									# probes every candidate corridor and fails closed on probe errors.
									drive_pos = _requested_drive_pos
									_nav_paused = False
									if not getattr(m_veh, '_offh_nav_error_logged', False):
										m_veh._offh_nav_error_logged = True
										LOG_DEBUG('OfflineBattle.SMART_AI navigation error id=%s: %s' % (
											str(eid), str(_nav_error)))
							# Safety recovery outranks a tactical hold or a pending A* job. The
							# target points back through the last proven-safe pose, but LocalDriver
							# still validates every candidate through the ordinary terrain callback.
							if _native_hazard_recovery_pre:
								_native_escape_target = _offh_native_hazard_escape_target(m_veh)
								if _native_escape_target is not None:
									drive_pos = _native_escape_target
									_nav_paused = False
									_ai_throttle_override = None
									_ai_server_wait = False
							_is_artillery_order = (_tactical_mode == 'artillery_fire')
							if _is_artillery_order and target_pos is not None:
								try:
									_artillery_velocity = _ai_order.get('target_velocity')
									if _artillery_velocity is None:
										_artillery_target = mock_vehicles.get(_ai_target_id)
										if _artillery_target is not None:
											_artillery_velocity = _offh_ai_artillery_target_velocity(
												{'vehicle': _artillery_target})
										else:
											_artillery_velocity = (0.0, 0.0, 0.0)
									_artillery_key = (
										_ai_target_id, _ai_shell_index,
										int(round(float(target_pos[0]) / 3.0)),
										int(round(float(target_pos[2]) / 3.0)),
										int(round(float(m_veh.position.x) / 3.0)),
										int(round(float(m_veh.position.z) / 3.0)))
									_artillery_cache = getattr(
										m_veh, '_offh_artillery_aim_cache', None)
									if (isinstance(_artillery_cache, tuple) and
											len(_artillery_cache) == 3 and
											_artillery_cache[0] == _artillery_key and
											float(_artillery_cache[1]) > float(_ai_now)):
										_artillery_solution = _artillery_cache[2]
									else:
										_artillery_solution = _offh_ai_artillery_solution(
											m_veh, target_pos, _artillery_velocity,
											_ai_shell_index, False)
										m_veh._offh_artillery_aim_cache = (
											_artillery_key, float(_ai_now) + 0.25,
											_artillery_solution)
									if _artillery_solution is not None:
										target_pos = _artillery_solution['aim_position']
										face_pos = target_pos
									else:
										_ai_fire_allowed = False
								except Exception:
									_artillery_solution = None
									_ai_fire_allowed = False
							elif _ai_target_id is not None and target_pos is not None:
								try:
									# face_position is the target's current pose.  LAN orders may
									# already contain an older constant-speed lead in aim_position;
									# starting from face_position avoids applying that lead twice.
									_direct_target = face_pos if face_pos is not None else target_pos
									_direct_velocity = _ai_order.get('target_velocity')
									if _direct_velocity is None:
										_direct_vehicle = mock_vehicles.get(_ai_target_id)
										_direct_velocity = _offh_ai_artillery_target_velocity(
											{'vehicle': _direct_vehicle})
									_direct_key = (
										_ai_target_id, _ai_shell_index,
										int(round(float(_direct_target[0]) * 0.5)),
										int(round(float(_direct_target[1]) * 0.5)),
										int(round(float(_direct_target[2]) * 0.5)),
										int(round(float(m_veh.position.x) * 0.5)),
										int(round(float(m_veh.position.z) * 0.5)),
										int(round(float(_direct_velocity[0]))),
										int(round(float(_direct_velocity[2]))))
									_direct_cache = getattr(
										m_veh, '_offh_direct_fire_aim_cache', None)
									if (isinstance(_direct_cache, tuple) and
											len(_direct_cache) == 3 and
											_direct_cache[0] == _direct_key and
											float(_direct_cache[1]) > float(_ai_now)):
										_direct_fire_solution = _direct_cache[2]
									else:
										_direct_fire_solution = _offh_ai_direct_fire_solution(
											m_veh, _direct_target, _direct_velocity,
											_ai_shell_index)
										m_veh._offh_direct_fire_aim_cache = (
											_direct_key, float(_ai_now) + 0.10,
											_direct_fire_solution)
									if _direct_fire_solution is not None:
										target_pos = _direct_fire_solution['aim_position']
										face_pos = target_pos
								except Exception:
									_direct_fire_solution = None
							dx = drive_pos[0] - m_veh.position.x
							dz = drive_pos[2] - m_veh.position.z
							dist = math.sqrt(dx*dx + dz*dz)
							_aim_dx = target_pos[0] - m_veh.position.x
							_aim_dz = target_pos[2] - m_veh.position.z
							_enemy_dist = math.sqrt(_aim_dx*_aim_dx + _aim_dz*_aim_dz)
							_aim_target_yaw = math.atan2(_aim_dx, _aim_dz) if _enemy_dist > 0.1 else m_veh.yaw
							_face_dx = face_pos[0] - m_veh.position.x
							_face_dz = face_pos[2] - m_veh.position.z
							_face_dist = math.sqrt(_face_dx*_face_dx + _face_dz*_face_dz)

							# PHYSICS PARAMS: same law module as the player, derived ONCE
							# per bot from its real descriptor (the old inline block
							# re-read td.physics for every bot on every tick).
							_bphys = getattr(m_veh, '_phys_params', None)
							if _bphys is None:
								_bphys = _PHY.derive_params(_td)
								m_veh._phys_params = _bphys
							bot_mass = _bphys['mass']
							bot_speedFwd = _bphys['speedFwd']
							_bot_gun_yaw_limits = getattr(
								m_veh, '_offh_ai_gun_yaw_limits', None)
							if _bot_gun_yaw_limits is None:
								_bot_gun_yaw_limits = _ai_driver.gun_yaw_limits(_td)
								m_veh._offh_ai_gun_yaw_limits = _bot_gun_yaw_limits
							_bot_gun_min_yaw, _bot_gun_max_yaw, _has_limited_traverse = (
								_bot_gun_yaw_limits)
							m_veh._offh_ai_targeted = _ai_target_id is not None
							m_veh._offh_ai_aligned = False
							m_veh._offh_ai_traversing = False
							m_veh._offh_ai_limited = bool(_has_limited_traverse)
							_desired_gun_pitch = float(
								getattr(m_veh, '_gun_pitch', 0.0) or 0.0)
							m_veh._offh_desired_gun_pitch = _desired_gun_pitch

							# VIRTUAL DRIVER
							throttle = 0.0
							turn_dir = 0

							# Preliminary yaw to target (needed by feelers before blending)
							# While stopped, face the combat order (including stable armour
							# angling); while moving, local avoidance follows the route point.
							if dist <= 15.0 and _face_dist > 0.1:
								_raw_target_yaw = math.atan2(_face_dx, _face_dz)
							else:
								_raw_target_yaw = math.atan2(dx, dz) if dist > 0.1 else m_veh.yaw
							_raw_diff_yaw = _raw_target_yaw - m_veh.yaw
							while _raw_diff_yaw > math.pi:  _raw_diff_yaw -= 2*math.pi
							while _raw_diff_yaw < -math.pi: _raw_diff_yaw += 2*math.pi

							# Pure local driver: the engine supplies terrain/collision probes;
							# timing, traffic separation, steering hysteresis and alternating
							# recovery live in one testable state machine.
							_driver_intent = bool(_battle_active) and (
								_native_hazard_recovery_pre or not (
									_ai_throttle_override is not None and
									float(_ai_throttle_override) <= 0.0))
							_driver_key = (
								int(math.floor(float(drive_pos[0]) * 0.25 + 0.5)),
								int(math.floor(float(drive_pos[2]) * 0.25 + 0.5)),
								bool(_driver_intent), bool(_nav_paused))
							_driver_cache = getattr(m_veh, '_offh_ai_driver_cache', None)
							_driver_cache_matches = (
								isinstance(_driver_cache, tuple) and len(_driver_cache) == 4 and
								_driver_cache[0] == _driver_key)
							_driver_cache_fresh = (
								_driver_cache_matches and
								float(_ai_now) < float(_driver_cache[1]))
							_driver_refresh_now = (
								_native_hazard_recovery_pre or _offh_ai_refresh_due(
									eid in _driver_refresh_ids, _driver_cache_matches,
									_driver_cache_fresh,
									_driver_cache[1] if _driver_cache_matches else 0.0,
									_ai_now, _driver_refresh_horizon))
							_driver_stale_reusable = (
								isinstance(_driver_cache, tuple) and
								len(_driver_cache) == 4 and
								(_driver_cache_matches or str(
									_driver_cache[3].get('recovery_mode', 'drive')) == 'drive'))
							if not _driver_intent:
								# A stop/cover order is authoritative immediately; never replay
								# a stale full-throttle answer while waiting for a driver slot.
								_driver_order = {
									'throttle': 0.0, 'turn': 0.0,
									'target_yaw': float(m_veh.yaw),
									'recovery_mode': 'arrived'}
							elif _nav_paused:
								# Path jobs progress in the shared navigator above. Do not spend
								# nine terrain corridors proving how to move during a hard wait.
								_driver_order = {
									'throttle': 0.0, 'turn': 0.0,
									'target_yaw': float(m_veh.yaw),
									'recovery_mode': 'nav_wait'}
							elif not _driver_refresh_now and _driver_stale_reusable:
								_driver_order = _driver_cache[3]
								if not _driver_cache_fresh or not _driver_cache_matches:
									_offh_perf_count('driver_deferred')
							elif not _driver_refresh_now:
								# A newly spawned bot waits only until its deterministic
								# driver slice; this keeps cold-cache work below the same hard
								# six-bot budget used during the rest of the battle.
								_driver_order = {
									'throttle': 0.0, 'turn': 0.0,
									'target_yaw': float(m_veh.yaw),
									'recovery_mode': 'budget_wait'}
								_offh_perf_count('driver_deferred')
							else:
								_offh_perf_count('driver_refresh')
								_driver_neighbours = []
								_nearby_ids = _VC.nearby_ids(
									_traffic_spatial[0], m_veh.position.x, m_veh.position.z)
								for _driver_eid in _nearby_ids:
									if _driver_eid == eid:
										continue
									_driver_body = _driver_frame.get(_driver_eid)
									if _driver_body is None:
										continue
									_driver_position = _driver_body['position']
									_driver_dx = _driver_position[0] - float(m_veh.position.x)
									_driver_dy = _driver_position[1] - float(m_veh.position.y)
									_driver_dz = _driver_position[2] - float(m_veh.position.z)
									# Local OBB prediction has no value for distant or vertically
									# separated vehicles. Filter before descriptor and dict work.
									if (abs(_driver_dy) > 5.0 or
											_driver_dx * _driver_dx + _driver_dz * _driver_dz > 576.0):
										continue
									_driver_neighbours.append(_driver_body)
								_own_half_length, _own_half_width = _offh_ai_hull_dims(_td)
								_own_velocity = (
									math.sin(float(m_veh.yaw)) * float(m_veh._veh_velocity),
									0.0,
									math.cos(float(m_veh.yaw)) * float(m_veh._veh_velocity))
								_driver_dt = float(dt)
								if isinstance(_driver_cache, tuple) and len(_driver_cache) == 4:
									_driver_dt = max(float(dt), min(
										0.35, float(_ai_now) - float(_driver_cache[2])))
								_driver_order = _offh_perf_call(
									'driver', _ai_driver.drive, eid,
									(float(m_veh.position.x), float(m_veh.position.y),
									 float(m_veh.position.z)),
									float(m_veh.yaw), float(m_veh._veh_velocity), _driver_dt,
									(float(drive_pos[0]), float(drive_pos[1]), float(drive_pos[2])),
									_driver_neighbours,
									lambda _driver_yaw: _offh_ai_direction_clear(
										m_veh, _driver_yaw, _ai_now, _ai_space_id),
									_own_velocity, _own_half_length, _own_half_width,
									_driver_intent)
								# A clear-road steering answer remains valid longer than an
								# avoidance/recovery answer.  Physics and wall/tank collision
								# still run every rendered frame; this only avoids repeating the
								# same nine native corridor probes for a bot that is driving
								# straight with no nearby traffic.
								_driver_mode_for_cache = str(
									_driver_order.get('recovery_mode', 'drive'))
								if (_driver_mode_for_cache == 'drive' and
										not _driver_neighbours):
									_driver_interval = 0.145
								elif _driver_mode_for_cache == 'drive':
									_driver_interval = 0.095
								else:
									# Avoidance and recovery need to react promptly while hulls
									# are close or a static corridor has just failed.
									_driver_interval = 0.060
								m_veh._offh_ai_driver_cache = (
									_driver_key, _offh_ai_cache_deadline(
										_ai_now, eid, _driver_interval, 3,
										_driver_cache is None),
									float(_ai_now), _driver_order)
							throttle = float(_driver_order.get('throttle', 0.0))
							turn_dir = float(_driver_order.get('turn', 0.0))
							target_yaw = float(_driver_order.get('target_yaw', _raw_target_yaw))
							diff_yaw = target_yaw - m_veh.yaw
							while diff_yaw > math.pi: diff_yaw -= 2 * math.pi
							while diff_yaw < -math.pi: diff_yaw += 2 * math.pi
							_driver_mode = _driver_order.get('recovery_mode', 'drive')
							if _driver_mode in ('drive', 'avoid'):
								# Corridor planning is deliberately budgeted across the lineup, but
								# its cached turn value is not feedback-safe. Recompute the cheap
								# heading error from this frame's exact native root yaw so a stale
								# full-track command cannot overshoot and reverse for several frames.
								turn_dir = _ai_driver.steering_turn(target_yaw, m_veh.yaw)
							if _ai_server_wait:
								_driver_mode = 'server_wait'
							m_veh._offh_ai_driver_mode = _driver_mode
							if _nav_paused:
								_offh_perf_count('nav_paused')
							if _driver_mode == 'drive':
								_offh_perf_count('driver_drive')
							elif _driver_mode == 'avoid':
								_offh_perf_count('driver_avoid')
							elif _driver_mode == 'arrived':
								_offh_perf_count('driver_arrived')
							elif _driver_mode in ('blocked', 'nav_wait', 'server_wait',
									'budget_wait'):
								_offh_perf_count('driver_wait')
							else:
								_offh_perf_count('driver_recovery')
							_feeler_steer_yaw = target_yaw if _driver_mode == 'avoid' else None
							if _nav_paused:
								# A* returns the current point while a safe path is pending or
								# unavailable. This is a hard safety stop and must outrank the
								# local driver's normal full-throttle request.
								throttle = 0.0
								turn_dir = 0.0
								if m_veh._veh_velocity > 0.0:
									m_veh._veh_velocity = max(0.0, m_veh._veh_velocity - 20.0 * dt)
								elif m_veh._veh_velocity < 0.0:
									m_veh._veh_velocity = min(0.0, m_veh._veh_velocity + 20.0 * dt)
							elif (_ai_throttle_override is not None and
								        _driver_mode in ('drive', 'arrived') and
								        abs(diff_yaw) < 0.65):
								throttle = float(_ai_throttle_override)
							if _battle_active:
								turn_dir, throttle, _ai_hull_aiming = (
									_ai_driver.combat_hull_aim(
										m_veh.yaw, _aim_target_yaw,
										_bot_gun_min_yaw, _bot_gun_max_yaw,
										turn_dir, throttle, _driver_mode,
										_ai_target_id is not None and
										not _native_hazard_recovery_pre))
							_traffic_neighbours = []
							_waiting_for_traffic = False
							_traffic_source = _driver_frame.get(eid)
							if _traffic_source is not None and throttle > 0.01:
								for _traffic_eid in _VC.nearby_ids(
										_traffic_spatial[0], m_veh.position.x, m_veh.position.z):
									if _traffic_eid == eid:
										continue
									_traffic_body = _driver_frame.get(_traffic_eid)
									if _traffic_body is None:
										continue
									_traffic_position = _traffic_body['position']
									_traffic_dx = _traffic_position[0] - float(m_veh.position.x)
									_traffic_dy = _traffic_position[1] - float(m_veh.position.y)
									_traffic_dz = _traffic_position[2] - float(m_veh.position.z)
									if (abs(_traffic_dy) <= 5.0 and
											_traffic_dx * _traffic_dx + _traffic_dz * _traffic_dz <= 576.0):
										_traffic_neighbours.append(_traffic_body)
								throttle, _waiting_for_traffic = (
									_ai_driver.friendly_traffic_throttle(
										_traffic_source, {
											'throttle': throttle,
											'target_yaw': target_yaw,
										}, _traffic_neighbours))
								if _waiting_for_traffic:
									_ai_driver.wait_for_traffic(
										eid, dt, throttle <= 0.01)
									# Record the final arbitration result, not the cached driver
									# mode that existed before deterministic right-of-way ran.
									m_veh._offh_ai_driver_mode = 'traffic_wait'
									_offh_perf_count('driver_traffic_wait')
							if not _waiting_for_traffic:
								_ai_driver.clear_traffic_wait(eid)

							_perf_physics = _offh_perf_start()
							_perf_physics_state = _offh_perf_start()
							# IMMOBILIZATION CHECK
							_dev_hp = getattr(m_veh, 'devices_hp', None)
							# is_tracked = locked tracks (handbrake below), a dead engine only coasts.
							_network_mobility_until = float(getattr(
								m_veh, '_network_mobility_carry_until', 0.0) or 0.0)
							_network_mobility_locked = _network_mobility_until > time.time()
							if (_network_mobility_until > 0.0 and
									not _network_mobility_locked):
								m_veh._network_mobility_carry_until = 0.0
								m_veh._network_mobility_disabled = False
							_b_locked = bool(getattr(m_veh, 'is_tracked', False))
							if (_b_locked or _network_mobility_locked or
									(_dev_hp is not None and _dev_hp.get('engineHealth', 1) <= 0)):
								throttle = 0.0
								turn_dir = 0.0
								# The player path zeroed this; the bot path did not, so a tracked bot kept
								# pivoting on its residual angular velocity.
								m_veh._veh_turn_velocity = 0.0
							elif throttle:
								# Same cost the player pays: a downed driver and a DAMAGED engine
								# both eat throttle (destruction is the hard gate above).
								_bmf = _crew_factor(m_veh, 'mobility') * _module_factor(m_veh, 'mobility')
								if _bmf < 1.0:
									throttle = throttle * _bmf

							# Repair and fire state advance independently of drive input. Keeping
							# this under ``elif throttle`` made a destroyed track permanent.
							_perf_effects = _offh_perf_start()
							_sync_burn_and_death(m_veh, getattr(m_veh, '_hull_model', None), getattr(m_veh, 'typeDescriptor', None))
							try:
								_tick_module_repair(m_veh, getattr(m_veh, 'typeDescriptor', None), dt, False)
							except Exception: pass
							_sync_engine_exhaust(m_veh, getattr(m_veh, '_hull_model', None), getattr(m_veh, 'typeDescriptor', None), getattr(m_veh, '_veh_velocity', 0.0) or 0.0)
							_offh_perf_stop('bot_effects', _perf_effects)
							if getattr(m_veh, 'is_on_fire', False) and m_veh.health > 0:
								try:
									from gui.mods.offhangar import device_damage as _DDf2
									_fs2 = getattr(m_veh, '_fire_started', None)
									if _fs2 is not None and (_ai_now - _fs2) >= _DDf2.FIRE_DURATION_SECONDS:
										_offh_extinguish(m_veh, False, 'burnt out')
								except Exception:
									pass
								cur_timer = getattr(m_veh, '_fire_timer', 0.0)
								if cur_timer is None: cur_timer = 0.0
								m_veh._fire_timer = float(cur_timer) + float(dt if dt is not None else 0.02)
								if m_veh._fire_timer >= 1.0: # Tick every 1 second
									m_veh._fire_timer -= 1.0
									fire_dmg = max(1, int(m_veh.maxHealth * 0.05)) # 5% max HP per sec
									m_veh.health -= fire_dmg
									_offh_drop_capture_for_vehicle(
										m_veh, getattr(m_veh, 'last_killer_id', None),
										'fire damage')

									try:
										import BigWorld
										from gui import WindowsManager
										bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)

										if m_veh.health <= 0:
											m_veh.health = 0
											BigWorld.player().arena.onVehicleKilled(m_veh.id, (getattr(m_veh, 'last_killer_id', None) or -1), 2)
										elif bw and hasattr(bw, 'vMarkersManager'):
											player_id = getattr(BigWorld.player(), 'playerVehicleID', -1)
											if m_veh.id == player_id:
												player = BigWorld.player()
												if hasattr(player, 'vehicle') and player.vehicle:
													player.vehicle.health = m_veh.health
													try: player.guiSessionProvider.invalidateVehicleState(1, player_id, m_veh.health, m_veh.health)
													except: pass
											else:
												marker = getattr(m_veh, 'marker', None)
												if marker is not None:
													bw.vMarkersManager.onVehicleHealthChanged(marker, max(0, m_veh.health), (getattr(m_veh, 'last_killer_id', -1) or -1), 0)
													try:
														bw.vMarkersManager.showVehicleDamageInfo(marker, fire_dmg, 0, 0, 1)
													except:
														pass
													LOG_DEBUG('Fire HP updated via marker, HP=%d' % m_veh.health)
									except: pass

							# Outside the active battle, the line-up holds its current pose.
							if not _battle_active:
								throttle = 0.0
								turn_dir = 0
								m_veh._veh_velocity = 0.0
								m_veh._veh_turn_velocity = 0.0
							m_veh._offh_ai_throttle = float(throttle)
							if abs(float(throttle)) >= 0.99:
								m_veh._offh_ai_full_throttle_seconds = float(getattr(
									m_veh, '_offh_ai_full_throttle_seconds', 0.0) or
									0.0) + float(dt)
							else:
								m_veh._offh_ai_full_throttle_seconds = 0.0

							_offh_perf_stop('physics_state', _perf_physics_state)
							_native_previous_pose = (
								float(m_veh.position.x), float(m_veh.position.y),
								float(m_veh.position.z), float(m_veh.yaw),
								float(getattr(m_veh, 'pitch', 0.0) or 0.0),
								float(getattr(m_veh, 'roll', 0.0) or 0.0))
							_native_body_pose = None
							_native_filter_owned = False
							_native_movement_required = (
								_offh_native_movement_required(
									player, m_veh, _network_bot_role))
							_python_movement_allowed = _offh_python_movement_allowed(
								player, m_veh, _network_bot_role)
							_native_body_manager = _native_body_manager_frame
							try:
								if _native_body_manager is not None:
									_native_movement_required = (
										_native_movement_required or
										_native_body_manager.requires_native(player, m_veh))
									_native_body_pose = _offh_perf_call(
										'native_physics', _native_body_manager.step,
										player, m_veh, _td, throttle, turn_dir,
										_ai_space_id, _ai_now, _battle_active)
									_native_filter_owned = _native_body_manager.owns_filter(m_veh)
							except Exception as _native_body_error:
								if not getattr(m_veh, '_offh_native_error_logged', False):
									m_veh._offh_native_error_logged = True
									LOG_ERROR('NATIVE_BOT_PHYSICS tick error id=%s error=%s' % (
										eid, str(_native_body_error)))
							if _native_body_pose is None and _native_movement_required:
								if _native_body_manager is not None:
									_native_body_pose = (
										_native_body_manager.fail_closed_result(m_veh))
								if _native_body_pose is None:
									_native_body_pose = _offh_native_failed_pose(m_veh)
								if not getattr(m_veh, '_offh_native_manager_error_logged', False):
									m_veh._offh_native_manager_error_logged = True
									LOG_ERROR('NATIVE_BOT_PHYSICS fail-closed id=%s '
										'reason=native manager returned no pose' % eid)
								if _native_body_pose is None:
									# An invalid presentation pose is still not permission to run a
									# second movement implementation. Hold this bot for the frame.
									m_veh._veh_velocity = 0.0
									m_veh._veh_turn_velocity = 0.0
									continue

							if _native_body_pose is not None:
								# Strategy, pathing and combat stay unchanged.  Only the retail
								# rigid body supplies the canonical hull pose and speeds.
								_native_position = _native_body_pose['position']
								m_veh.position = Math.Vector3(
									_native_position[0], _native_position[1],
									_native_position[2])
								m_veh.yaw = float(_native_body_pose['yaw'])
								m_veh.pitch = float(_native_body_pose['pitch'])
								m_veh.roll = float(_native_body_pose['roll'])
								m_veh._veh_velocity = float(_native_body_pose['velocity'])
								m_veh._veh_turn_velocity = float(
									_native_body_pose['turn_velocity'])
								m_veh._airborne = False
								m_veh._vert_vel = 0.0
								m_veh._slide_spd = 0.0
								m_veh._push_x = 0.0
								m_veh._push_z = 0.0
								_b_ypr = (m_veh.yaw, m_veh.pitch, m_veh.roll,
									0.0, 0.0, 0.0)

								# Native contact resolves terrain, but it does not own tactical
								# water/cliff avoidance. A realised hazard is a driver-recovery
								# condition, not a native wiring failure: stop input once, invalidate
								# the cached heading, then leave the active rigid body available for
								# the existing wet-escape/reverse/pivot controller.
								_native_final_hazard = _offh_ai_baked_hazard_near((
									m_veh.position.x, m_veh.position.y, m_veh.position.z), 1)
								_native_water = (-1.0 if _native_final_hazard is False else
									_offh_ai_pose_water_depth(m_veh))
								_native_was_safe = _offh_ai_baked_pose_safe(
									_native_previous_pose[:3])
								_native_now_safe = (
									True if _native_final_hazard is False else
									_offh_ai_baked_pose_safe((m_veh.position.x,
										m_veh.position.y, m_veh.position.z)))
								_native_recovery_baked_safe = _native_now_safe
								_native_recovery_water_safe = (
									_native_water <= _OFFH_AI_WATER_AVOID_DEPTH)
								_native_hazard_reason = None
								if _native_water > _OFFH_AI_WATER_AVOID_DEPTH:
									_native_hazard_reason = 'water'
								elif not _native_now_safe:
									_native_hazard_reason = 'terrain'
								_native_hazard_recovering = bool(getattr(
									m_veh, '_offh_native_hazard_recovering', False))
								_native_hazard_recovery_done = False
								if _native_hazard_recovering:
									_native_hazard_recovery_done = (
										_offh_native_hazard_recovery_complete(
											m_veh, _native_recovery_baked_safe,
											_native_recovery_water_safe, _ai_now))
								if _native_hazard_reason is None:
									if (_native_hazard_recovering and
											_native_hazard_recovery_done):
										m_veh._offh_native_hazard_recovering = False
										m_veh._offh_native_hazard_anchor = None
										m_veh._offh_native_hazard_entry_yaw = None
										m_veh._offh_native_hazard_escape_endpoint = None
										m_veh._offh_native_hazard_safe_since = None
										_native_hazard_recovering = False
									if not _native_hazard_recovering:
										m_veh._offh_native_last_safe_pose = (
											float(m_veh.position.x), float(m_veh.position.y),
											float(m_veh.position.z))
								elif not _native_hazard_recovering:
									_native_safe_anchor = getattr(
										m_veh, '_offh_native_last_safe_pose', None)
									if _native_safe_anchor is None and _native_was_safe:
										_native_safe_anchor = _native_previous_pose[:3]
									m_veh._offh_native_hazard_anchor = _native_safe_anchor
									m_veh._offh_native_hazard_entry_yaw = float(target_yaw)
									m_veh._offh_native_hazard_escape_endpoint = None
									m_veh._offh_native_hazard_safe_since = None
									_native_hold_ok = False
									if _native_body_manager is not None:
										try:
											_native_hold_ok = bool(
												_native_body_manager.hold(m_veh))
										except Exception:
											_native_hold_ok = False
									m_veh._offh_native_hazard_recovering = True
									m_veh._veh_velocity = 0.0
									m_veh._veh_turn_velocity = 0.0
									m_veh._offh_ai_driver_mode = 'native_guard'
									_b_ypr = (m_veh.yaw, m_veh.pitch, m_veh.roll,
										0.0, 0.0, 0.0)
									_offh_ai_probe_reject(
										m_veh, _native_hazard_reason)
									try:
										_ai_driver.remember_failure(eid, target_yaw, 5.0)
									except Exception:
										pass
									LOG_NOTE('NATIVE_BOT_PHYSICS hazard_recovery id=%s '
										'reason=%s water=%.3f hold=%s' % (
											eid, _native_hazard_reason, _native_water,
											_native_hold_ok))

								# Native destructible health and damage callbacks are the sole
								# collision authority for this body. The legacy proximity scan
								# remains below only for Python/player movement; running both would
								# destroy the same tree or structure through two unrelated ledgers.
								try:
									_offh_perf_call(
										'bot_audio', _sync_bot_motion_sounds, m_veh, _td,
										(veh_pos[0], veh_pos[1], veh_pos[2]), bot_speedFwd,
										throttle, dt)
								except Exception:
									pass
							elif _python_movement_allowed:
								# Kept as unreachable rollback code for source-level comparison.
								# This native-only package never authorizes this branch.
								_perf_physics_motion = _offh_perf_start()
								bot_gravity = _PHY.GRAVITY
								# Seed the rollback point before any commanded movement, tank impulse,
								# airborne drift or slope slide can move this hull during the tick.
								# The shipped graph already separates dry road cells from water and
								# cliff shoulders.  Five exact engine probes are still mandatory near a
								# hazard (and when data is unavailable), but add no safety on inland cells.
								_initial_hazard = _offh_ai_baked_hazard_near((
									m_veh.position.x, m_veh.position.y, m_veh.position.z), 1)
								_initial_water = (-1.0 if _initial_hazard is False else
								                  _offh_ai_pose_water_depth(m_veh))
								if _initial_water <= _OFFH_AI_WATER_AVOID_DEPTH:
									# Transaction start: this exact dry pose is restored before the
									# matrix/network state is committed if any later motion becomes wet.
									m_veh._offh_ai_tick_dry_pose = (
										float(m_veh.position.x), float(m_veh.position.y),
										float(m_veh.position.z))
									# A clear one-cell halo necessarily proves the centre cell safe.
									# Avoid a second lookup for the common inland case.
									m_veh._offh_ai_tick_nav_safe = (
										True if _initial_hazard is False else
										_offh_ai_baked_pose_safe(m_veh._offh_ai_tick_dry_pose))
								else:
									m_veh._offh_ai_tick_dry_pose = None
									m_veh._offh_ai_tick_nav_safe = False
								cur_vel = m_veh._veh_velocity
								if not getattr(m_veh, '_airborne', False) and (throttle != 0 or abs(cur_vel) > 0.01):
									m_veh._dp_acc = (getattr(m_veh, '_dp_acc', 9.0) or 9.0) + dt
									if m_veh._dp_acc >= 0.15:
										m_veh._dp_acc = 0.0
										# The previous frame's terrain-support footprint was sampled at
										# this same canonical pose. Reuse it when no later slide, rollback
										# or collision displaced the hull; otherwise retain the original
										# bridge-aware probes. This removes duplicate engine rays without
										# changing the pitch cadence or accepting stale terrain.
										_braw = None
										_bsp = getattr(m_veh, '_offh_drive_support', None)
										if _bsp is not None:
											_bdx = float(m_veh.position.x) - float(_bsp[0])
											_bdy = float(m_veh.position.y) - float(_bsp[1])
											_bdz = float(m_veh.position.z) - float(_bsp[2])
											_bda = float(m_veh.yaw) - float(_bsp[3])
											while _bda > math.pi: _bda -= 2.0 * math.pi
											while _bda < -math.pi: _bda += 2.0 * math.pi
											if (_bdx * _bdx + _bdz * _bdz <= 0.16 and
													abs(_bdy) <= 0.40 and abs(_bda) <= 0.10):
												_braw = float(_bsp[4])
												_offh_perf_count('drive_pitch_reuse')
										if _braw is None:
											_braw = _drive_pitch(
												_ai_space_id, m_veh.position.x, m_veh.position.z,
												m_veh.yaw, m_veh.position.y)
											_offh_perf_count('drive_pitch_exact')
										# smooth probe spikes (same reason as the player)
										_bprev = getattr(m_veh, '_dp_v', _braw) or 0.0
										_bd = _braw - _bprev
										if _bd > 0.35: _bd = 0.35
										elif _bd < -0.35: _bd = -0.35
										m_veh._dp_v = _bprev + _bd * 0.6
								m_veh._veh_velocity = _offh_perf_call(
									'kinematics', _PHY.longitudinal_step, _bphys, cur_vel,
									throttle, turn_dir != 0,
									getattr(m_veh, '_dp_v', 0.0) or 0.0, dt,
									getattr(m_veh, '_airborne', False), 0, _b_locked)

								try:
									_offh_perf_call(
										'bot_audio', _sync_bot_motion_sounds, m_veh, _td,
										(veh_pos[0], veh_pos[1], veh_pos[2]), bot_speedFwd,
										throttle, dt)
								except Exception:
									pass

								# Static tanks still participate: a moving neighbour may have queued
								# their reciprocal correction earlier in this frame. Wall/tree probes
								# below remain speed-gated, so this adds only the cheap OBB pass.
								if getattr(m_veh, 'isAlive', False):
									_hit_wall = False
									# Tree/fence enumeration is presentation-side contact work, not
									# motion integration.  At the fastest 0.8.2 tanks a 150 ms scan
									# interval advances less than the probe's 6 m look-ahead, so no
									# contact can be skipped while avoiding 20-29 chunk walks/frame.
									_tree_scan_due = float(_ai_now) >= float(getattr(
										m_veh, '_offh_next_tree_scan', 0.0) or 0.0)
									if (abs(m_veh._veh_velocity) > 0.5 and _tree_scan_due and
											eid in _tree_refresh_ids):
										m_veh._offh_next_tree_scan = _offh_ai_cache_deadline(
											_ai_now, eid, 0.150, 5,
											getattr(m_veh, '_offh_next_tree_scan', None) is None)
										try:
											_offh_perf_call('tree_scan', _fell_trees_near,
												m_veh, _ai_space_id, m_veh.position, m_veh.yaw,
												m_veh._veh_velocity, _td)
										except: pass
									elif abs(m_veh._veh_velocity) > 0.5 and _tree_scan_due:
										_offh_perf_count('tree_deferred')
									# The three-ray swept broad phase is mandatory every movement frame.
									# Expensive slope/material classification runs only after those rays hit.
									if abs(m_veh._veh_velocity) > 0.5:
										try:
											_hit_wall = _offh_perf_call(
												'wall_collision', _check_horizontal_collision,
												m_veh, _ai_space_id, m_veh.position, m_veh.yaw,
												m_veh._veh_velocity, _td,
												getattr(m_veh, '_airborne', False), dt)
										except: pass
									_bnx = m_veh.position.x + math.sin(m_veh.yaw) * m_veh._veh_velocity * dt
									_bnz = m_veh.position.z + math.cos(m_veh.yaw) * m_veh._veh_velocity * dt
									if _hit_wall:
										# Airborne: a wall must not brake the fall - keep momentum,
										# just don't advance into it. Grounded: bleed forward drive.
										if not getattr(m_veh, '_airborne', False):
											m_veh._veh_velocity *= 0.2
											# A realised wall hit is stronger evidence than the speculative
											# corridor probe. Feed it back immediately instead of waiting for
											# the generic stuck timer while the hull grinds the obstacle.
											m_veh._offh_ai_driver_mode = 'blocked'
											_offh_ai_probe_reject(m_veh, 'obstacle')
											_ai_driver.remember_failure(
												eid, target_yaw, 5.0)
									else:
										m_veh.position = Math.Vector3(_bnx, m_veh.position.y, _bnz)
									# Tank-vs-tank: velocity-relative impulse (e=0) + Baumgarte push-apart
									try:
										_bsvx = math.sin(m_veh.yaw) * m_veh._veh_velocity + (getattr(m_veh, '_push_x', 0.0) or 0.0)
										_bsvz = math.cos(m_veh.yaw) * m_veh._veh_velocity + (getattr(m_veh, '_push_z', 0.0) or 0.0)
										_bot_collision_ids = None
										if _collision_candidates[0] is not None:
											_bot_collision_ids = _collision_candidates[0].get(eid)
										# The broad phase proves that most bots have no nearby body.
										# Preserve a queued reciprocal correction even when this bot
										# owns no pair itself; otherwise skip the complete resolver.
										if (_bot_collision_ids == () and
												eid not in _tank_pair_pending):
											_offh_perf_count('tank_collision_empty')
											_btr = (0.0, 0.0, 0.0, 0.0)
										else:
											_btr = _offh_perf_call(
												'tank_collision', _tank_resolve, eid,
												m_veh.position.x, m_veh.position.z, m_veh.yaw, _td,
												1.0 / max(bot_mass, 1.0), _bsvx, _bsvz,
												m_veh.position.y)
										if abs(_btr[0]) + abs(_btr[1]) > 0.01:
											# Re-evaluate immediately after another hull displaced this bot.
											# A short failed-heading memory gives touching bots deterministic,
											# opposite escape sides without treating traffic as a static wall.
											m_veh._offh_ai_driver_cache = None
											m_veh._offh_ai_driver_mode = 'avoid'
											_ai_driver.remember_failure(
												eid, target_yaw, 0.8)
										# Forward impulse share hits the bot's drive speed too (see player)
										_bfimp = _btr[2] * math.sin(m_veh.yaw) + _btr[3] * math.cos(m_veh.yaw)
										_bfabs = 0.0
										if _bfimp * m_veh._veh_velocity < 0.0:
											_bfabs = -m_veh._veh_velocity if abs(_bfimp) >= abs(m_veh._veh_velocity) else _bfimp
											m_veh._veh_velocity += _bfabs
										_bpx = (getattr(m_veh, '_push_x', 0.0) or 0.0) + _btr[2] - _bfabs * math.sin(m_veh.yaw)
										_bpz = (getattr(m_veh, '_push_z', 0.0) or 0.0) + _btr[3] - _bfabs * math.cos(m_veh.yaw)
										m_veh.position = Math.Vector3(m_veh.position.x + _btr[0] + _bpx * dt, m_veh.position.y, m_veh.position.z + _btr[1] + _bpz * dt)
										m_veh._push_x = _bpx * 0.90
										m_veh._push_z = _bpz * 0.90
									except: pass

								# ROTATION: physics.traverse_step (same law as the player)
								m_veh._veh_turn_velocity = _offh_perf_call(
									'kinematics', _PHY.traverse_step, _bphys,
									m_veh._veh_turn_velocity, turn_dir,
									m_veh._veh_velocity, dt, 0, throttle)
								try:
									_btf = _module_factor(m_veh, 'traverse')
									if _btf < 1.0:
										m_veh._veh_turn_velocity = m_veh._veh_turn_velocity * _btf
								except Exception: pass

								if m_veh._veh_turn_velocity != 0.0:
									m_veh.yaw += m_veh._veh_turn_velocity * dt
									while m_veh.yaw > math.pi: m_veh.yaw -= 2*math.pi
									while m_veh.yaw < -math.pi: m_veh.yaw += 2*math.pi

								_offh_perf_stop('physics_motion', _perf_physics_motion)
								_perf_physics_ground = _offh_perf_start()
								# TERRAIN SNAP (ray starts just above the hull so bridges overhead are ignored)
								_bsup = None
								_bsup_rejected = False
								try:
									# Highest ground under the fore-aft footprint (same law as the player)
									_bhl = 2.5
									try:
										if _td is not None and hasattr(_td, 'hull') and 'hitTester' in _td.hull:
											_bhl = max(1.5, abs(_td.hull['hitTester'].bbox[1][2]))
									except Exception:
										pass
									_bsup = _offh_perf_call(
										'terrain_support', _terrain_support, _ai_space_id,
										m_veh.position.x, m_veh.position.y, m_veh.position.z,
										m_veh.yaw, _bhl)
									_bc_y = _bsup[1]        # ground under the hull centre (chassis origin)
									_bg_y = _bc_y if _bc_y is not None else _bsup[0]  # rest on centre, not float
									if _bg_y is not None:
										_b_snap = max(0.8, min(2.5, abs(m_veh._veh_velocity) * dt * 2.0 + 0.6))
										_b_climb = max(0.6, abs(m_veh._veh_velocity) * dt * 2.5)
										_bcom_gap = _b_snap if _bc_y is None else (m_veh.position.y - _bc_y)
										_bland_y = _bg_y if _bc_y is None else _bc_y
										if _VC.support_rise_is_obstacle(
												m_veh.position.y, _bc_y, _b_climb):
											_bsup_rejected = True
											# The centre support ray hit the top of a wagon, roof or large
											# prop after horizontal integration moved the hull partly inside
											# it.  Never pop the tank vertically onto that surface.  Restore
											# only this frame's pose and invalidate the selected heading so
											# LocalDriver performs its normal reverse/turn recovery.
											_rise_anchor = getattr(m_veh, '_offh_ai_tick_dry_pose', None)
											if _rise_anchor is not None:
												m_veh.position = Math.Vector3(
													_rise_anchor[0], _rise_anchor[1], _rise_anchor[2])
											m_veh._veh_velocity = 0.0
											m_veh._veh_turn_velocity = 0.0
											m_veh._push_x = 0.0
											m_veh._push_z = 0.0
											m_veh._vert_vel = 0.0
											m_veh._airborne = False
											m_veh._offh_ai_driver_mode = 'obstacle_rise'
											_offh_ai_probe_reject(m_veh, 'obstacle')
											try:
												_ai_driver.remember_failure(eid, target_yaw, 5.0)
											except Exception:
												pass
										elif m_veh.position.y <= _bg_y or (_bcom_gap <= _b_snap and not getattr(m_veh, '_airborne', False)):
											# Soft ground-follow: below snaps up hard, above eases down (cap 0.12 m)
											if m_veh.position.y < _bg_y:
												_brise = _bg_y - m_veh.position.y
												_bfy = m_veh.position.y + (_brise if _brise <= _b_climb else _b_climb)
											else:
												_bfy = m_veh.position.y + (_bg_y - m_veh.position.y) * min(1.0, dt * 15.0)
												if _bfy > _bg_y + 0.12:
													_bfy = _bg_y + 0.12
											m_veh.position = Math.Vector3(m_veh.position.x, _bfy, m_veh.position.z)
											m_veh._vert_vel = 0.0
											m_veh._airborne = False
										else:
											m_veh._airborne = True
											_bvv = (getattr(m_veh, '_vert_vel', 0.0) or 0.0)
											_bfall_n = 1
											if abs(_bvv * dt) > 0.5:
												_bfall_n = min(8, int(abs(_bvv * dt) / 0.5) + 1)
											_bfall_sdt = dt / _bfall_n
											_by = m_veh.position.y
											_bfall_i = 0
											while _bfall_i < _bfall_n:
												_bvv -= bot_gravity * _bfall_sdt
												_by += _bvv * _bfall_sdt
												if _bland_y is not None and _by <= _bland_y:
													_by = _bland_y
													_bvv = 0.0
													m_veh._airborne = False
													break
												_bfall_i += 1
											m_veh._vert_vel = _bvv
											m_veh.position = Math.Vector3(m_veh.position.x, _by, m_veh.position.z)
								except:
									_bsup = None
								# Cache only support that still belongs to the realised grounded
								# pose. A later slide/rollback is detected by the pose fence above.
								if (_bsup is not None and not _bsup_rejected and
										not getattr(m_veh, '_airborne', False)):
									_bsupport_pitch = _support_drive_pitch(
										m_veh.position.y, _bsup, _bhl)
									if _bsupport_pitch is not None:
										m_veh._offh_drive_support = (
											float(m_veh.position.x), float(m_veh.position.y),
											float(m_veh.position.z), float(m_veh.yaw),
											float(_bsupport_pitch))
									else:
										m_veh._offh_drive_support = None
								else:
									m_veh._offh_drive_support = None

								# Tilt sampling alternates frames per bot; fore/aft support from
								# this frame removes two of its four engine rays when valid.
								# the pitch/roll smoothing below hides the halved sample rate
								m_veh._ypr_fc = (getattr(m_veh, '_ypr_fc', 0) or 0) + 1
								if getattr(m_veh, '_ypr_c', None) is None or ((m_veh._ypr_fc + eid) & (1 if getattr(m_veh, '_spot_visible', True) else 3)) == 0:
										m_veh._ypr_c = _offh_perf_call(
										'terrain_tilt', _get_terrain_ypr, _ai_space_id,
											m_veh.position, m_veh.yaw, 5.0, 3.0,
											_bsup if not _bsup_rejected else None, _bhl)
								_b_ypr = (m_veh.yaw, m_veh._ypr_c[1], m_veh._ypr_c[2], m_veh._ypr_c[3], m_veh._ypr_c[4], m_veh._ypr_c[5])
								# --- Slope slide (bot): same WG law + cross-heading projection as player ---
								_bss = getattr(m_veh, '_slide_spd', 0.0) or 0.0
								if getattr(m_veh, '_airborne', False):
									_bss = 0.0   # airborne = pure ballistic fall, no slide
								else:
									_bss = _PHY.slope_slide_speed(_bss, _b_ypr[5], dt)
								m_veh._slide_spd = _bss
								_bcross_x = math.cos(m_veh.yaw); _bcross_z = -math.sin(m_veh.yaw)
								_bsl_dot = _b_ypr[3] * _bcross_x + _b_ypr[4] * _bcross_z
								_bsl_dx = _bcross_x * _bsl_dot; _bsl_dz = _bcross_z * _bsl_dot
								if getattr(m_veh, '_airborne', False):
									# carry the frozen lateral drift through the fall (see player)
									_balx = getattr(m_veh, '_air_lat_vx', 0.0) or 0.0
									_balz = getattr(m_veh, '_air_lat_vz', 0.0) or 0.0
									if abs(_balx) > 1e-04 or abs(_balz) > 1e-04:
										m_veh.position = Math.Vector3(m_veh.position.x + _balx * dt, m_veh.position.y, m_veh.position.z + _balz * dt)
										m_veh._air_lat_vx = _balx * 0.995
										m_veh._air_lat_vz = _balz * 0.995
								else:
									m_veh._air_lat_vx = _bsl_dx * _bss
									m_veh._air_lat_vz = _bsl_dz * _bss
									if not getattr(m_veh, '_airborne', False) and _bss > 0.01 and (abs(_bsl_dx) > 1e-04 or abs(_bsl_dz) > 1e-04):
										_slb_len = math.sqrt(_bsl_dx * _bsl_dx + _bsl_dz * _bsl_dz)
										_slide_blocked_by_water = False
										if _slb_len > 1e-04:
											# Look ahead along the gravity-driven path, not the commanded
											# heading. This catches a tank sliding sideways toward a one-way
											# shoreline lip before the current frame actually crosses it.
											_slide_forecast = max(3.0, min(8.0, _bss * _slb_len * 1.5))
											_slide_probe = Math.Vector3(
												m_veh.position.x + _bsl_dx / _slb_len * _slide_forecast,
												m_veh.position.y,
												m_veh.position.z + _bsl_dz / _slb_len * _slide_forecast)
											if _offh_ai_pose_water_depth(
													m_veh, _slide_probe, m_veh.yaw) > _OFFH_AI_WATER_AVOID_DEPTH:
												_slide_blocked_by_water = True
												m_veh._slide_spd = 0.0
												m_veh._air_lat_vx = 0.0
												m_veh._air_lat_vz = 0.0
												_offh_ai_probe_reject(m_veh, 'water')
												try:
													_ai_driver.remember_failure(eid, target_yaw, 5.0)
												except Exception:
													pass
											if not _slide_blocked_by_water:
												try:
													_offh_perf_count('physics_rays')
													_forecast_hit = BigWorld.wg_collideSegment(
														_ai_space_id,
														Math.Vector3(_slide_probe.x,
														             m_veh.position.y + 8.0,
														             _slide_probe.z),
														Math.Vector3(_slide_probe.x,
														             m_veh.position.y - 30.0,
														             _slide_probe.z), 128)
												except Exception:
													_forecast_hit = None
												if (_forecast_hit is None or
														m_veh.position.y - _forecast_hit[0].y >
														_slide_forecast * 0.38):
													_slide_blocked_by_water = True
													m_veh._slide_spd = 0.0
													m_veh._air_lat_vx = 0.0
													m_veh._air_lat_vz = 0.0
													_offh_ai_probe_reject(m_veh, 'terrain')
											_slb_x = m_veh.position.x + _bsl_dx * _bss * dt
											_slb_z = m_veh.position.z + _bsl_dz * _bss * dt
											try:
												_offh_perf_count('physics_rays')
												_slb_c = BigWorld.wg_collideSegment(_ai_space_id, Math.Vector3(_slb_x, m_veh.position.y + 8.0, _slb_z), Math.Vector3(_slb_x, m_veh.position.y - 30.0, _slb_z), 128)
											except Exception:
												_slb_c = None
											if (not _slide_blocked_by_water and _slb_c is not None and
													(m_veh.position.y - _slb_c[0].y) < 4.0):
												m_veh.position = Math.Vector3(_slb_x, _slb_c[0].y, _slb_z)
												m_veh._vert_vel = 0.0
												m_veh._airborne = False
								_offh_perf_stop('physics_ground', _perf_physics_ground)
								_perf_physics_safety = _offh_perf_start()
								# Final realised-pose water guard.  This is intentionally after all
								# horizontal drive, vehicle impulses, vertical falling and lateral slope
								# slide: none of those paths may push an autonomous hull over a wet bank.
								_final_hazard = _offh_ai_baked_hazard_near((
									m_veh.position.x, m_veh.position.y, m_veh.position.z), 1)
								_pose_water = (-1.0 if _final_hazard is False else
								               _offh_ai_pose_water_depth(m_veh))
								if _pose_water > _OFFH_AI_WATER_AVOID_DEPTH:
									# Cancel only motion performed during THIS simulation tick, before it
									# is rendered or published. Never rewind to an older dry-history pose:
									# that made a tank visibly teleport several metres back uphill after
									# it had already crossed a one-way bank.
									_dry_anchor = getattr(m_veh, '_offh_ai_tick_dry_pose', None)
									if _dry_anchor is not None:
										m_veh.position = Math.Vector3(
											_dry_anchor[0], _dry_anchor[1], _dry_anchor[2])
										m_veh._veh_velocity = 0.0
										m_veh._veh_turn_velocity = 0.0
										m_veh._slide_spd = 0.0
										m_veh._air_lat_vx = 0.0
										m_veh._air_lat_vz = 0.0
										m_veh._vert_vel = 0.0
										m_veh._airborne = False
										m_veh._push_x = 0.0
										m_veh._push_z = 0.0
										m_veh._offh_ai_driver_mode = 'water_guard'
										m_veh._offh_ai_water_guard_until = _ai_now + 1.0
										globals()['g_offh_ai_water_guard_total'] = int(
											globals().get('g_offh_ai_water_guard_total', 0) or 0) + 1
										try:
											_ai_driver.remember_failure(
												eid, target_yaw, 5.0)
										except Exception:
											pass
										m_veh._ypr_c = _offh_perf_call(
											'terrain_tilt', _get_terrain_ypr,
											_ai_space_id, m_veh.position, m_veh.yaw)
										_b_ypr = (m_veh.yaw, m_veh._ypr_c[1],
										          m_veh._ypr_c[2], m_veh._ypr_c[3],
										          m_veh._ypr_c[4], m_veh._ypr_c[5])
								# The baked hazard mask marks water and cliff shoulders separately from
								# ordinary obstacle holes. Local avoidance, impulses and lateral slide
								# may enter a true hazard, but driving beside a building must not trigger
								# this final rollback on every frame.
								if (getattr(m_veh, '_offh_ai_tick_nav_safe', False) and
										_final_hazard is not False and
										not _offh_ai_baked_pose_safe((m_veh.position.x,
											m_veh.position.y, m_veh.position.z))):
									_edge_anchor = getattr(m_veh, '_offh_ai_tick_dry_pose', None)
									if _edge_anchor is not None:
										m_veh.position = Math.Vector3(
											_edge_anchor[0], _edge_anchor[1], _edge_anchor[2])
										m_veh._veh_velocity = 0.0
										m_veh._veh_turn_velocity = 0.0
										m_veh._slide_spd = 0.0
										m_veh._air_lat_vx = 0.0
										m_veh._air_lat_vz = 0.0
										m_veh._vert_vel = 0.0
										m_veh._airborne = False
										m_veh._push_x = 0.0
										m_veh._push_z = 0.0
										m_veh._offh_ai_driver_mode = 'edge_guard'
										m_veh._offh_ai_edge_guard_until = _ai_now + 1.0
										globals()['g_offh_ai_edge_guard_total'] = int(
											globals().get('g_offh_ai_edge_guard_total', 0) or 0) + 1
										try:
											_ai_driver.remember_failure(eid, target_yaw, 5.0)
										except Exception:
											pass
										m_veh._ypr_c = _offh_perf_call(
											'terrain_tilt', _get_terrain_ypr,
											_ai_space_id, m_veh.position, m_veh.yaw)
										_b_ypr = (m_veh.yaw, m_veh._ypr_c[1],
										          m_veh._ypr_c[2], m_veh._ypr_c[3],
										          m_veh._ypr_c[4], m_veh._ypr_c[5])
								_offh_perf_stop('physics_safety', _perf_physics_safety)
							else:
								# Replica, handoff and unknown roles never receive local legacy
								# kinematics. Their presentation is snapshot-owned or frozen.
								m_veh._veh_velocity = 0.0
								m_veh._veh_turn_velocity = 0.0
								_b_ypr = (m_veh.yaw, m_veh.pitch, m_veh.roll,
									0.0, 0.0, 0.0)
							# Native pose is already the canonical rigid-body orientation. Legacy
							# terrain smoothing here would split the visible/native chassis from the
							# mock matrix used by hit tests.
							if _native_body_pose is not None:
								m_veh.pitch = float(_b_ypr[1])
								m_veh.roll = float(_b_ypr[2])
							else:
								_b_blend = min(1.0, dt * 8.0)
								_b_p0 = getattr(m_veh, 'pitch', 0.0) or 0.0
								_b_r0 = getattr(m_veh, 'roll', 0.0) or 0.0
								m_veh.pitch = _b_p0 + (_b_ypr[1] - _b_p0) * _b_blend
								m_veh.roll = _b_r0 + (_b_ypr[2] - _b_r0) * _b_blend
							_b_ypr = (_b_ypr[0], m_veh.pitch, m_veh.roll)

							_offh_perf_call(
								'pose_commit', _VP.commit_pose, m_veh,
								m_veh.position, m_veh.yaw, m_veh.pitch, m_veh.roll,
								_ai_space_id, _ai_now,
								(bool(getattr(m_veh, '_spot_visible', True)) and
								 not _native_filter_owned and
								 not _native_movement_required), True, False)
							if (_native_body_pose is not None and
									_native_body_manager is not None):
								try:
									_native_body_manager.observe_presentation(
										m_veh, _ai_now)
								except Exception:
									pass
							_offh_perf_stop('physics', _perf_physics)
							_perf_visibility = _offh_perf_start()
							# --- Spotting: unspotted ENEMY tanks are hidden like the real game.
							# Simulation keeps running; only rendering/markers/minimap are culled.
							try:
								_sen = globals().get('g_offh_spotting')
								if _sen is None:
									try:
										from _constants import CONFIG_OPTIONS as _SCFG
										_sen = bool(_SCFG.get('spotting_enabled', True))
									except Exception:
										_sen = True
									globals()['g_offh_spotting'] = _sen
								if _sen and getattr(m_veh, 'isAlive', True) and getattr(m_veh, '_bot_team', 2) != _player_team:
									m_veh._spot_chk = (getattr(m_veh, '_spot_chk', 9.0) or 9.0) + dt
									if m_veh._spot_chk >= 0.5:
										m_veh._spot_chk = (eid % 10) * 0.05  # stagger re-checks across bots
										# Re-apply the model state on every check (idempotent): a
										# show that failed or raced the async model load left the
										# bot invisible-while-spotted FOREVER (the change-only flip
										# below never re-fires once the flag matches) - the
										# 'invisible tank keeps firing until destroyed' report.
										_schk = getattr(m_veh, '_chassis_model', None)
										if _schk is not None:
											# spot memory alone kept DEAD bots marked and shown - gate on alive too
											_svchk = _ai_now < (getattr(m_veh, '_spot_until', 0.0) or 0.0) and (getattr(m_veh, 'health', 0) or 0) > 0
											try:
												_schk.visible = _svchk
												_schk.visibleAttachments = _svchk
											except Exception:
												pass
											# The MARKER has to follow the model. Hiding an unspotted bot but leaving its
											# marker up is what the 'invisible tank' screenshots actually show: name, HP
											# bar and direction arrow floating over empty ground. Retail shows nothing at
											# all for an unspotted vehicle, because the marker only exists while it is
											# spotted. Create and destroy it alongside the model - both branches are
											# idempotent, so this cannot churn on every check.
											try:
												from gui import WindowsManager as _spwm
												_spbw = getattr(_spwm.g_windowsManager, 'battleWindow', None)
												_spvm = getattr(_spbw, 'vMarkersManager', None) if _spbw is not None else None
												_spmk = getattr(m_veh, 'marker', None)
												if _spvm is not None:
													if _svchk and _spmk in (None, -1) and getattr(m_veh, 'proxy', None) is not None:
														m_veh.marker = _spvm.createMarker(m_veh.proxy)
													elif (not _svchk) and _spmk not in (None, -1):
														_spvm.destroyMarker(_spmk)
														m_veh.marker = None
											except Exception as _spe:
												LOG_DEBUG('spot marker sync err:', str(_spe))
											# The flags are provably right - 137 flips in one battle, not a single
											# want/got mismatch - and tanks still vanish. A model can carry
											# visible=True and draw nothing in two ways this probe never covered: it
											# is not in the world at all, or it sits somewhere other than the mock the
											# marker follows. Measure both; put it back when it fell out.
											if _svchk:
												try:
													if not getattr(_schk, 'inWorld', True):
														# READ-ONLY on purpose. Putting the model back crashed the client twice:
														# through _add_model (1.2.8) and through the entity (1.2.9), the latter
														# at the next onArenaCreated while the previous space was released. The
														# engine took this model out of the world and owns that decision; a
														# re-attach leaves a dangling reference that kills the space teardown.
														# Ask WHY it went instead, once per vehicle, and never write anything.
														if not getattr(m_veh, '_dbg_oow', False):
															m_veh._dbg_oow = True
															_ent_rw = getattr(m_veh, 'bw_entity', None)
															_emd_same = '?'
															try:
																if _ent_rw is not None:
																	_emd_same = getattr(_ent_rw, 'model', None) is _schk
															except Exception:
																pass
															LOG_DEBUG('VIS OUTOFWORLD id=%s ent=%s entHoldsIt=%s hp=%s wreck=%s dead=%s' % (
																eid, _ent_rw is not None, _emd_same, getattr(m_veh, 'health', '?'),
																getattr(m_veh, '_wreck_done', False), not getattr(m_veh, 'isAlive', True)))
													else:
														_mp = _schk.position
														_dxz = ((_mp.x - m_veh.position.x) ** 2 + (_mp.z - m_veh.position.z) ** 2) ** 0.5
														if _dxz > 25.0 and not getattr(m_veh, '_dbg_drift', False):
															m_veh._dbg_drift = True
															LOG_DEBUG('VIS DRIFT id=%s dist=%.1f model=(%.0f,%.0f) mock=(%.0f,%.0f)' % (
																eid, _dxz, _mp.x, _mp.z, m_veh.position.x, m_veh.position.z))
												except Exception:
													pass
											# The MARKER needs the same idempotent treatment as the model above. Its
											# state was only ever touched on a CHANGE of _spot_visible, so one failed
											# createMarker/destroyMarker left the two permanently out of step - and the
											# flag was already flipped, so nothing ever retried. An icon with no tank is
											# exactly that: the model went hidden while destroyMarker did not take. Same
											# bug the comment above describes for the model, one level up.
											try:
												from gui import WindowsManager as _WMk
												_bwk = getattr(_WMk.g_windowsManager, 'battleWindow', None)
												_vmk = getattr(_bwk, 'vMarkersManager', None) if _bwk is not None else None
												if _vmk is not None:
													_mk_now = getattr(m_veh, 'marker', None)
													_mk_has = _mk_now not in (None, -1)
													if _svchk and not _mk_has:
														try: m_veh.marker = _vmk.createMarker(m_veh.proxy)
														except Exception: m_veh.marker = None
													elif (not _svchk) and _mk_has:
														try: _vmk.destroyMarker(_mk_now)
														except Exception: pass
														m_veh.marker = None
											except Exception:
												pass
											# Report every visibility FLIP with the full state, so an invisible tank
											# can be traced instead of guessed at: is it the spot timer, the model, or
											# the marker that disagrees? Also reads back what the engine actually
											# stored - a write that silently did not take shows up as a mismatch.
											try:
												_vprev = getattr(m_veh, '_dbg_vis', None)
												if _vprev != _svchk:
													m_veh._dbg_vis = _svchk
													LOG_DEBUG('VIS id=%s want=%s got=%s spotUntil=%.1f now=%.1f hp=%s marker=%s' % (
														eid, _svchk, getattr(_schk, "visible", "?"),
															(getattr(m_veh, '_spot_until', 0.0) or 0.0), _ai_now,
														getattr(m_veh, 'health', '?'), getattr(m_veh, 'marker', None)))
											except Exception:
												pass
									_svis = _ai_now < ((getattr(m_veh, '_spot_until', 0.0) or 0.0)) and (getattr(m_veh, 'health', 0) or 0) > 0
									if _svis != getattr(m_veh, '_spot_visible', True):
										m_veh._spot_visible = _svis
										_sch = getattr(m_veh, '_chassis_model', None)
										if _sch is not None:
											try:
												_sch.visible = _svis
												_sch.visibleAttachments = _svis
											except Exception:
												pass
										try:
											from gui import WindowsManager as _WMs
											_bws = getattr(_WMs.g_windowsManager, 'battleWindow', None)
											if _bws is not None:
												_vmm = getattr(_bws, 'vMarkersManager', None)
												if _vmm is not None:
													if _svis:
														if getattr(m_veh, 'marker', None) in (None, -1):
															m_veh.marker = _vmm.createMarker(m_veh.proxy)
													else:
														if getattr(m_veh, 'marker', None) not in (None, -1):
															try:
																_vmm.destroyMarker(m_veh.marker)
															except Exception:
																pass
															m_veh.marker = None
												_smm = getattr(_bws, 'minimap', None)
												if _smm is not None:
													if _svis:
														_smm.notifyVehicleStart(eid)
													else:
														_smm.notifyVehicleStop(eid)
										except Exception:
											pass
							except Exception:
								pass
							_offh_perf_stop('visibility', _perf_visibility)
							# Track scroll (bot): y=left, z=right, traverse via turn rate
							try:
								_bfa = getattr(m_veh, '_fashion', None)
								if (_bfa is not None and not _native_filter_owned and
										not _native_movement_required):
									# physics.track_scroll: same law + clamp as the player feed
									_btls, _btrs = _PHY.track_scroll(_bphys, m_veh._veh_velocity, m_veh._veh_turn_velocity)
									_bfa.movementInfo = Math.Vector4(0.0, _btls, _btrs, 0.0)
							except Exception: pass
							# Allies never pass through the spotting block below - they count as always
							# visible, so their model is set once at spawn and never touched again. That
							# leaves them without the idempotent re-apply enemies get every tick: an ally
							# whose model ends up hidden for any reason stays hidden for the whole battle,
							# which matches the reports - single tanks, permanently, no trigger. Enemies
							# are left alone here; the spotting block owns them.
							try:
								if (getattr(m_veh, '_bot_team', 2) or 2) == _player_team:
									_avm = getattr(m_veh, '_chassis_model', None)
									_aal = (getattr(m_veh, 'health', 0) or 0) > 0
									if _avm is not None and _aal and not getattr(_avm, "visible", True):
										_avm.visible = True
										_avm.visibleAttachments = True
										LOG_DEBUG('VIS ALLY RESTORED id=%s' % eid)
							except Exception:
								pass
							# Drowning: same rules as the player - 1.6 m of water over the hull, 10 s,
							# probed ~3x/s per bot for perf. While submerged the bot is _offh_drowning,
							# which freezes its turret and stops it shooting further down, exactly as the
							# player's crew stops working the gun.
							try:
								# NOT getattr(..., 0.0) + dt. _MockVeh defines __getattr__ returning None for
								# every unknown attribute, so it never raises AttributeError and getattr NEVER
								# falls back to the default - it hands back None. None + dt raised TypeError on
								# the FIRST line of this block, every tick, for every bot, straight into the
								# bare except below. That is why no bot ever drowned and why not even the
								# diagnostics printed. The player path is unaffected: PlayerAccount raises
								# properly, so its identical-looking line works.
								m_veh._dwn_chk = (getattr(m_veh, '_dwn_chk', 0.0) or 0.0) + dt
								if m_veh._dwn_chk >= 0.3:
									_bdel = min(m_veh._dwn_chk, 0.5)
									m_veh._dwn_chk = 0.0
									_bdepth = _offh_water_depth(m_veh.position.x, m_veh.position.y, m_veh.position.z)
									_bwd = (20.0 - _bdepth) if _bdepth >= 0.0 else -1.0   # kept for the log below
									m_veh._offh_drowning = (_bdepth > 1.6)
									# Diagnostic: report the FIRST time each bot touches water at all, plus how
									# deep. Drowning needs 10 s continuously past 1.6 m (same as the player and
									# as retail), so a bot merely fording a river never dies - this tells us
									# whether they reach water in the first place.
									# Across a full Slough round not one bot ever logged BOT IN WATER, so before
									# blaming the AI for staying dry, report what wg_collideWater returns for the
									# bot NEAREST the player, once a second. A steady None/-1 means the probe
									# itself is the problem, not where the bots drive.
									try:
										_wt = globals().get('g_offh_water_dbg_t', 0.0) or 0.0
										_wnow = BigWorld.time()
										if _wnow - _wt > 1.0:
											globals()['g_offh_water_dbg_t'] = _wnow
											LOG_DEBUG('WATER PROBE: bot=%s y=%.1f raw=%s depth=%s' % (eid, m_veh.position.y, _bwd, ('%.2f' % (20.0 - _bwd)) if (_bwd is not None and _bwd >= 0.0) else 'n/a'))
									except Exception:
										pass
									if _bwd is not None and _bwd >= 0.0 and (20.0 - _bwd) > 0.2:
										if not getattr(m_veh, '_wet_logged', False):
											m_veh._wet_logged = True
											LOG_DEBUG('BOT IN WATER: id=%s depth=%.2f m' % (eid, 20.0 - _bwd))
									if _bdepth > 1.6:
										m_veh._drown_t = (getattr(m_veh, '_drown_t', 0.0) or 0.0) + _bdel
										if int(m_veh._drown_t) != int(m_veh._drown_t - _bdel):
											LOG_DEBUG('BOT DROWNING: id=%s t=%.1f/10 s depth=%.2f' % (eid, m_veh._drown_t, _bdepth))
										if m_veh._drown_t > 10.0 and (getattr(m_veh, 'health', 1) or 0) > 0:
											# The hull is untouched, the crew drowns - so the bot keeps the HP it had
											# when it went under for display purposes, like the player does.
											m_veh._hp_display = getattr(m_veh, 'health', 0) or 0
											m_veh.health = 0
											m_veh._drowned = True
											_offh_set_alive(m_veh, False)
											m_veh._offh_drowning = False
											_offh_knock_out_everything(m_veh, False)
											LOG_DEBUG('BOT DROWNED: id=%s' % eid)
											try: player.arena.onVehicleKilled(eid, -1, 5)
											except Exception: pass
									else:
										m_veh._drown_t = 0.0
							except Exception: pass

								# Turret and gun stay in their spawn pose until the countdown ends.
							if _battle_active and hasattr(m_veh, '_t_mat'):
								# Věž by měla vždy mířit na hráče (cíl), nezávisle na tom, kam se vyhýbá trup
								t_yaw = _aim_target_yaw - m_veh.yaw
								while t_yaw > math.pi: t_yaw -= 2*math.pi
								while t_yaw < -math.pi: t_yaw += 2*math.pi
								# Omezit věž na limity vždy
								if _has_limited_traverse:
									t_yaw = max(_bot_gun_min_yaw, min(_bot_gun_max_yaw, t_yaw))

								if getattr(m_veh, '_turret_yaw', None) is None: m_veh._turret_yaw = 0.0
								t_diff = t_yaw - m_veh._turret_yaw
								m_veh._offh_ai_traversing = bool(
									_ai_target_id is not None and
									(_ai_hull_aiming or abs(t_diff) > 0.04))
								rot_speed = 0.5
								try:
									if _td: rot_speed = _td.turret['rotationSpeed']
								except: pass
								rot_step = rot_speed * dt
								try:
									_btsf = _module_factor(m_veh, 'turret_speed')
									if _btsf < 1.0:
										rot_step = rot_step * _btsf
								except Exception: pass
								# Frozen while submerged or with the turret rotator destroyed - the same two
								# conditions that freeze the player's turret. On a turretless tank this is
								# the gun lock: its aim is clamped to the hull-mounted yaw limits above, so
								# a frozen traverse leaves the gun pointing wherever the hull points.
								if getattr(m_veh, '_offh_drowning', False) or getattr(m_veh, 'is_turret_locked', False):
									rot_step = 0.0
									t_diff = 0.0

								if t_diff > rot_step: m_veh._turret_yaw += rot_step
								elif t_diff < -rot_step: m_veh._turret_yaw -= rot_step
								else: m_veh._turret_yaw = t_yaw

								m_veh._t_mat.setRotateYPR((m_veh._turret_yaw, 0, 0))
								# Barrel elevation toward the same target, slewed at the gun's own speed and
								# clamped to its real pitchLimits. Without this bots held the gun dead level
								# and every wreck ended up in the identical pose.
								if hasattr(m_veh, '_g_mat'):
									try:
										_bp_want = 0.0
										if (_is_artillery_order and
												_artillery_solution is not None):
											# The rendered barrel follows the same ballistic solution used
											# by the arc-clearance and impact tests below.
											_bp_want = float(_artillery_solution['pitch'])
										elif _direct_fire_solution is not None:
											# Ordinary guns need both horizontal lead and gravity drop;
											# firing along a straight sight line now genuinely misses low.
											_bp_want = float(_direct_fire_solution['pitch'])
										elif target_pos is not None:
											_bp_dx = target_pos[0] - m_veh.position.x
											_bp_dz = target_pos[2] - m_veh.position.z
											_bp_flat = math.sqrt(_bp_dx * _bp_dx + _bp_dz * _bp_dz)
											if _bp_flat > 0.5:
												# BigWorld convention: nose-up is NEGATIVE pitch
												_bp_want = -math.atan2((target_pos[1] + 1.0) - (m_veh.position.y + 1.5), _bp_flat)
										_bp_min, _bp_max = -0.35, 0.15
										try:
											_bp_lim = _td.gun.get('pitchLimits', None) if (_td and isinstance(_td.gun, dict)) else None
											if _bp_lim is not None:
												_bp_l = _bp_lim.get('absolute', _bp_lim) if hasattr(_bp_lim, 'get') else _bp_lim
												_bp_min = float(_bp_l[0]); _bp_max = float(_bp_l[1])
										except Exception:
											pass
										if _bp_want < _bp_min: _bp_want = _bp_min
										elif _bp_want > _bp_max: _bp_want = _bp_max
										_desired_gun_pitch = _bp_want
										m_veh._offh_desired_gun_pitch = _bp_want
										if getattr(m_veh, '_gun_pitch', None) is None: m_veh._gun_pitch = 0.0
										_bp_speed = 0.35
										try:
											if _td: _bp_speed = float(_td.gun.get('rotationSpeed', 0.35))
										except Exception:
											pass
										_bp_step = _bp_speed * dt
										_bp_diff = _bp_want - m_veh._gun_pitch
										if _bp_diff > _bp_step: m_veh._gun_pitch += _bp_step
										elif _bp_diff < -_bp_step: m_veh._gun_pitch -= _bp_step
										else: m_veh._gun_pitch = _bp_want
										m_veh._g_mat.setRotateYPR((0, m_veh._gun_pitch, 0))
									except Exception:
										pass

							# Strelba bota na hrace
							if getattr(getattr(player, 'arena', None), 'period', 3) != 3:
								continue # no shooting in prebattle countdown OR afterbattle (capture won)
							# Same gates the player's _mock_shoot applies to itself: a submerged crew is
							# fighting the water, and a destroyed gun does not fire at all.
							if getattr(m_veh, '_offh_drowning', False):
								continue
							if getattr(m_veh, 'is_gun_destroyed', False):
								continue
							if getattr(m_veh, '_ai_shoot_timer', None) is None:
								m_veh._ai_shoot_timer = 0
								m_veh._ai_clip_size = 1
								m_veh._ai_clip = 1
								m_veh._ai_reload_intra = 0.0
								m_veh._ai_reload_full = 3.0
								try:
									_g = getattr(_td, 'gun', {}) if _td else {}
									if isinstance(_g, dict):
										if 'reloadTime' in _g: m_veh._ai_reload_full = float(_g['reloadTime'])
										if 'clip' in _g and len(_g['clip']) == 2:
											m_veh._ai_clip_size = int(_g['clip'][0])
											m_veh._ai_reload_intra = float(_g['clip'][1])
											m_veh._ai_clip = m_veh._ai_clip_size
								except: pass

							m_veh._ai_shoot_timer += dt

							# Zjistit absolutní úhel, kam míří dělo
							abs_gun_yaw = m_veh.yaw + getattr(m_veh, '_turret_yaw', 0.0)
							# Gate on the bearing to the TARGET, not target_yaw: that one is
							# the steering direction (separation/feeler blended), so a
							# limited-traverse TD whose hull lined up with its own driving
							# direction fired at a player sitting 90 deg off to the side.
							_ai_gun_aligned = _offh_ai_driver().gun_aligned(
								_aim_target_yaw, m_veh.yaw,
								getattr(m_veh, '_turret_yaw', 0.0),
								getattr(m_veh, '_offh_desired_gun_pitch',
								        _desired_gun_pitch),
								getattr(m_veh, '_gun_pitch', 0.0))
							m_veh._offh_ai_aligned = bool(
								_ai_target_id is not None and _ai_gun_aligned)

							bot_reload = m_veh._ai_reload_intra if (m_veh._ai_clip_size > 1 and m_veh._ai_clip > 0 and m_veh._ai_clip < m_veh._ai_clip_size) else m_veh._ai_reload_full
							# A downed loader drags the reload out for a bot exactly as it does for the
							# player (a knocked-out commander adds his smaller malus on top), and a
							# damaged ammo bay on top of that. crew_stat_factor returns a TIME
							# multiplier - >1 is worse - so it multiplies. The old code divided by it
							# and only when it was below 1.0, a combination that can never be true:
							# the bot reload malus has never once fired.
							try:
								_brf = _crew_factor(m_veh, 'reload') * _module_factor(m_veh, 'reload')
								if _brf and _brf > 1.0:
									bot_reload = bot_reload * _brf
							except Exception:
								pass

							# Smart AI requires a current team spot and an unobstructed static
							# firing lane.
							_ai_ready_to_fire = (
								m_veh._ai_shoot_timer > bot_reload and _ai_fire_allowed and
								1.0 < _enemy_dist < _ai_fire_range and _ai_gun_aligned)
							_ai_shot_clear = False
							if _ai_ready_to_fire:
								_ai_los_now = BigWorld.time()
								_ai_los_target = getattr(m_veh, '_offh_ai_los_target', None)
								_ai_los_time = getattr(m_veh, '_offh_ai_los_time', -999.0)
								if (_ai_los_target != _ai_target_id or
								        _ai_los_now - _ai_los_time >= 0.20):
									m_veh._offh_ai_los_target = _ai_target_id
									m_veh._offh_ai_los_time = _ai_los_now
									if (_is_artillery_order and
											_artillery_solution is not None):
										m_veh._offh_ai_los_clear = _offh_ai_artillery_world_clear(
											_artillery_solution['path'],
											_artillery_solution['aim_position'])
									else:
										m_veh._offh_ai_los_clear = _offh_ai_clear_shot(
											(m_veh.position.x, m_veh.position.y, m_veh.position.z),
											target_pos)
								_ai_shot_clear = bool(getattr(m_veh, '_offh_ai_los_clear', False))
							if _ai_ready_to_fire and _ai_shot_clear:
								m_veh._ai_shoot_timer = 0
								m_veh._offh_spot_last_shot = float(BigWorld.time())
								m_veh._network_bot_fire_seq = int(getattr(m_veh, '_network_bot_fire_seq', 0) or 0) + 1
								m_veh._network_bot_shell_index = _ai_shell_index
								if m_veh._ai_clip_size > 1:
									m_veh._ai_clip -= 1
									if m_veh._ai_clip <= 0:
										m_veh._ai_clip = m_veh._ai_clip_size
								try:
									if g_projectile_mover and _td:
										from items import vehicles
										_shots = _td.gun['shots'] if hasattr(_td, 'gun') and 'shots' in _td.gun else []
										if not _shots and isinstance(_td.gun, dict): _shots = _td.gun.get('shots', [])
										if _shots:
											_ai_shell_index = min(_ai_shell_index, len(_shots) - 1)
											m_veh._network_bot_shell_index = _ai_shell_index
											_shot = _shots[_ai_shell_index]
											_effectsDescr = vehicles.g_cache.shotEffects[_shot['shell']['effectsIndex']]
											_gravity = _shot['gravity']
											_speed = _shot['speed']

											# Spawn the shell along the rendered barrel in both yaw and
											# pitch. It must never home vertically toward target_pos while
											# the visible gun is still elevating.
											_barrel_dir = _offh_ai_driver().barrel_direction(
												abs_gun_yaw, getattr(m_veh, '_gun_pitch', 0.0))
											dir_v = Math.Vector3(
												_barrel_dir[0], _barrel_dir[1], _barrel_dir[2])
											# Fire from the installed gun's fully-aimed dispersion.  The old
											# hard-coded 0.03 rad circle was roughly an order of magnitude
											# wider than many real 0.8.2 guns, so even a correctly led shot
											# missed a tank-sized target at ordinary engagement range.
											_bot_dispersion = 0.03
											try:
												_bot_gun = getattr(_td, 'gun', {}) if _td else {}
												_bot_dispersion = float(
													_bot_gun.get('shotDispersionAngle', 0.03)
													if hasattr(_bot_gun, 'get') else
													getattr(_bot_gun, 'shotDispersionAngle', 0.03))
												_bot_dispersion *= (
													_crew_factor(m_veh, 'dispersion') *
													_module_factor(m_veh, 'dispersion'))
											except Exception:
												_bot_dispersion = 0.03
											sigma = max(0.0, _bot_dispersion) / 3.0
											dir_v.x += random.gauss(0, sigma)
											dir_v.y += random.gauss(0, sigma)
											dir_v.z += random.gauss(0, sigma)
											dir_v.normalise()

											_vel = dir_v.scale(_speed)

											_muzzle = _offh_ai_gun_fire_position(m_veh)
											start_p = Math.Vector3(
												_muzzle[0], _muzzle[1], _muzzle[2])
											_cam_pos = BigWorld.camera().position if BigWorld.camera() else start_p
											# keep the shot id: explode() needs it to detonate this very tracer
											_b_sid = random.randint(10000, 99999)
											globals()['g_offh_adding_projectile'] = True
											try:
												g_projectile_mover.add(_b_sid, _effectsDescr, _gravity, start_p, _vel, start_p, True, _cam_pos)
											finally:
												globals()['g_offh_adding_projectile'] = False
											try:
												_pjb = getattr(g_projectile_mover, '_ProjectileMover__projectiles', {}).get(_b_sid)
												if _pjb is not None: _pjb['fireMissedTrigger'] = False
											except Exception: pass
											# Barrel recoil animation on the firing bot's gun
											_trigger_gun_recoil(getattr(m_veh, '_gun_recoil', None))
											try:
												_b_td2 = getattr(m_veh, 'typeDescriptor', None)
												_trigger_shot_impulse(getattr(m_veh, '_swinging', None), Math.Vector3(-dir_v.x, -dir_v.y, -dir_v.z), _b_td2.gun['impulse'] if _b_td2 else 0.0)
											except Exception:
												pass
											_mflash_played = _play_muzzle_flash(m_veh, getattr(m_veh, '_gun_model', None), getattr(m_veh, 'typeDescriptor', None), is_player=False)
											if not _mflash_played:
												# The old inline lookup checked gun['effects']['shotSound'], but
												# gun['effects'] is a (stages, effects, _) tuple in this build, so
												# it always fell through to the 20-45mm sound for every bot.
												_fallback_gun_sound(getattr(m_veh, 'typeDescriptor', None), getattr(m_veh, '_chassis_model', None))

											# All bot shells use the same in-flight collision runtime as the player.
											_fired_bot = m_veh
											_fired_bot_id = eid
											_fired_bot_shot = _shot
											_fired_bot_seq = int(getattr(m_veh, '_network_bot_fire_seq', 0) or 0)
											_fired_bot_velocity = _vel
											_fired_bot_gravity = Math.Vector3(0.0, -float(_gravity), 0.0)
											_fired_bot_time = max(4.0, min(
												20.0, 2500.0 / max(1.0, float(_speed)) + 4.0))
											def _bot_vehicle_impact(_target, _collision, _point,
													_segment_start, _segment_end, _direction,
													_travel_distance, _flight_time,
													_attacker=_fired_bot, _attacker_id=_fired_bot_id,
													_fired_shot=_fired_bot_shot, _fire_seq=_fired_bot_seq):
												_resolve_bot_projectile_hit(
													_attacker, _attacker_id, _target, _collision, _point,
													_segment_start, _segment_end, _direction,
													_travel_distance, _fired_shot, _fire_seq)
											def _bot_world_impact(_world_hit, _point, _direction,
													_travel_distance, _flight_time,
													_attacker=_fired_bot, _fired_shot=_fired_bot_shot,
													_shot_id=_b_sid, _effects=_effectsDescr):
												try:
													_material = _terrain_hit_material(
														_offh_bspace(), _point, _direction)
													if (_material + 'Hit') not in _effects:
														_material = 'ground'
													g_projectile_mover.explode(
														_shot_id, _effects, _material, _point, _direction)
												except Exception as _bot_ground_error:
													LOG_DEBUG('Bot ground impact error:', str(_bot_ground_error))
												try:
													if _offh_is_he(_fired_shot):
														_offh_he_splash(
															_point, _fired_shot, _attacker.id, None)
												except Exception as _bot_ground_splash_error:
													LOG_DEBUG('Bot HE ground splash error:', str(_bot_ground_splash_error))
											_offh_launch_live_projectile(
												_b_sid, start_p, _fired_bot_velocity, _fired_bot_gravity,
												mock_vehicles, _fired_bot_id, _bot_vehicle_impact,
												_bot_world_impact, _fired_bot_time)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())
						except Exception as e:
							import traceback
							LOG_DEBUG('Bot AI Exception:', traceback.format_exc())

				_offh_perf_stop('bot_loop', _perf_bot_loop)
				try:
					from gui.mods.offhangar.network_battle import publish_authoritative_bots
					_offh_perf_call('network_publish', publish_authoritative_bots,
					                player, mock_vehicles)
				except Exception:
					pass
				_perf_post_bot = _offh_perf_start()

				# PLAYER DEATH CHECK
				try:
					player_mock = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
					if player_mock and player_mock.health <= 0 and getattr(player, '_is_dead', False) is not True:
						player._is_dead = True
						player._offh_spectating = True
						# A destroyed tank has every module and every crewman out - not just when it
						# drowned, which was the only path that used to do this.
						try: _offh_knock_out_everything(player_mock, True)
						except Exception as _koe: LOG_DEBUG('death knockout err:', str(_koe))
						# leave sniper/strategic so the postmortem cam is a stable follow, not stuck zoomed
						try: g_offline_aih.onControlModeChanged('arcade')
						except Exception: pass
						# Retail's desaturated postmortem look: PostMortemControlMode.enable() runs
						# g_postProcessing.enable('postmortem'), a chain of HSV saturation cut plus
						# the postmortem_correction LUT (system/post_processing/chains/wg_postmortem).
						# Must come AFTER the arcade switch, whose own enable() sets the arcade preset.
						_offh_postmortem_grading()
						# The arcade switch above is not the only thing that can clobber the chain
						# (control-mode enable() calls g_postProcessing.enable for its own preset),
						# so re-assert once the postmortem camera has settled.
						try: _offh_battle_callback(0.5, _offh_postmortem_grading)
						except Exception: pass
						player._offh_spec_idx = 0
						# dead -> hide the aim crosshair / gun marker
						try:
							_dh_ctrl = getattr(getattr(player, 'inputHandler', None), 'ctrl', None)
							if _dh_ctrl is not None:
								try: _dh_ctrl.showGunMarker(False)
								except Exception: pass
								try: _dh_ctrl.showGunMarker2(False)
								except Exception: pass
						except Exception: pass
						# crew death voice like live (Avatar plays soundNotifications 'vehicle_destroyed')
						try:
							_sn = getattr(player, 'soundNotifications', None)
							if _sn is None:
								import gui.IngameSoundNotifications as _ISN
								player.soundNotifications = _ISN.IngameSoundNotifications()
								player.soundNotifications.start()
								_sn = player.soundNotifications
							# Bind to a LIVING vehicle: __playFirstFromQueue discards any sound whose
							# bound vehicle is not alive, and this one binds to the player by default -
							# who is already dead here, so the crew death voice was always dropped.
							_alive_id = None
							try:
								for _avid, _avi in player.arena.vehicles.iteritems():
									if _avi.get('isAlive'):
										_alive_id = _avid
										break
							except Exception:
								pass
							if _sn is not None: _sn.play('vehicle_destroyed', _alive_id)
						except Exception: pass
						LOG_DEBUG('Player is dead. Spawning destroyed model and ending battle.')
						try:
							killer_id = getattr(player_mock, 'last_killer_id', -1)
							p_id = player.playerVehicleID
							if hasattr(player.arena, 'onVehicleKilled'):
								player.arena.onVehicleKilled(p_id, killer_id, 0)
						except Exception as _e:
							LOG_DEBUG('Player death event error:', _e)

						# Swap model - hide live models, show the destroyed ones. The block below
						# already carries the live turret bearing and gun pitch across (_dead_tyaw /
						# _dead_gpitch), so the pose survives either way. What must NOT survive a
						# drowning is the burnt-out look: the tank sank where it stood, it did not
						# blow up, so it keeps its intact models - same swap, undamaged variants.
						try:
							_dtd = getattr(player_mock, 'typeDescriptor', None) or loaded_models.get('td')
							_dkey = 'undamaged' if getattr(player_mock, '_drowned', False) else 'destroyed'
							_d_ch = BigWorld.Model(_dtd.chassis['models'][_dkey])
							_d_hu = BigWorld.Model(_dtd.hull['models'][_dkey])
							_d_tu = BigWorld.Model(_dtd.turret['models'][_dkey])
							_d_gu = BigWorld.Model(_dtd.gun['models'][_dkey])

							try: _dead_tyaw = turret_yaw[0]
							except Exception: _dead_tyaw = 0.0
							try: _dead_gpitch = gun_pitch[0]
							except Exception: _dead_gpitch = 0.0
							def _swap_player_destroyed(_d_ch=_d_ch, _d_hu=_d_hu, _d_tu=_d_tu, _d_gu=_d_gu, _tyaw=_dead_tyaw, _gpitch=_dead_gpitch):
								try:
									# Force load
									_add_model(_d_ch)
									_add_model(_d_hu)
									_add_model(_d_tu)
									_add_model(_d_gu)

									def _attach_when_ready():
										if not getattr(_d_ch, 'loaded', True) or not getattr(_d_hu, 'loaded', True) or not getattr(_d_tu, 'loaded', True) or not getattr(_d_gu, 'loaded', True):
											_offh_battle_callback(0.1, _attach_when_ready)
											return
										try: BigWorld.delModel(_d_hu)
										except: pass
										try: BigWorld.delModel(_d_tu)
										except: pass
										try: BigWorld.delModel(_d_gu)
										except: pass

										_live_chassis = loaded_models.get('chassis') or loaded_models.get('hull')
										if _live_chassis is not None:
											try:
												for _mot in list(_live_chassis.motors):
													_live_chassis.delMotor(_mot)
											except: pass
											try: _live_chassis.visible = False
											except: pass
											try: BigWorld.delModel(_live_chassis)
											except: pass

										try: _d_ch.node('V').attach(_d_hu)
										except: pass
										# freeze turret/gun at the last aimed direction (not snapped forward)
										try:
											_t_mat = Math.Matrix(); _t_mat.setRotateYPR((_tyaw, 0, 0))
											mock_veh._wreck_t_mat = _t_mat   # hold a ref: a GC'd matrix drops the node back to identity
											_d_hu.node('HP_turretJoint', _t_mat).attach(_d_tu)
										except: pass
										try:
											_g_mat = Math.Matrix(); _g_mat.setRotateYPR((0, _gpitch, 0))
											mock_veh._wreck_g_mat = _g_mat   # hold a ref: a GC'd matrix drops the node back to identity
											_d_tu.node('HP_gunJoint', _g_mat).attach(_d_gu)
										except: pass

										_d_ch.position = Math.Vector3(mock_veh.position)
										try: _d_ch.addMotor(BigWorld.Servo(chassis_mp))
										except: pass
										try:
											mock_veh._collision_obstacle = BigWorld.PyModelObstacle(
												_ptd.hull['models']['destroyed'],
												_ptd.turret['models']['destroyed'],
												chassis_mp,
												False
											)
										except: pass
										LOG_DEBUG('Player destroyed model placed OK')
									_attach_when_ready()
								except Exception as _e:
									import traceback
									LOG_DEBUG('Player model swap failed:', traceback.format_exc())

							_offh_battle_callback(0.1, _swap_player_destroyed)
						except Exception as _e: LOG_DEBUG('Player death model err:', str(_e))

						# Exit battle in 5 seconds - use game.fini() which is the proper hook
						def _exit_battle():
							try:
								if _exit_done[0]:
									LOG_DEBUG('Player death exit skipped: another exit path already ran')
									return
								if not _offh_sweep_or_retry('exit', _exit_battle):
									return
								_exit_done[0] = True
								LOG_DEBUG('Player death: triggering exit to hangar')
								_battle_finished[0] = True
								try:
									import SoundGroups as _SG
									if getattr(_SG, 'g_instance', None) is not None:
										_SG.g_instance.enableArenaSounds(False)
										_SG.g_instance.enableLobbySounds(True)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								# Kill the crew-voice engine BEFORE the hangar loads. This used
								# to CREATE+start a fresh IngameSoundNotifications here (merge
								# artifact): the instance lived on the persistent account, its
								# active voice event never got an end-callback once arena sounds
								# died, and the jammed 'voice' queue silenced ALL crew voices in
								# every later battle. destroy() stops active lines and lets the
								# next battle lazily build a clean instance.
								try:
									_sn = getattr(player, 'soundNotifications', None)
									if _sn is not None:
										try: _sn.destroy()
										except Exception: pass
									try: del player.soundNotifications
									except Exception: pass
								except: pass

								try:
									_aih = getattr(player, 'inputHandler', None)
									if _aih is not None:
										try: _aih._AvatarInputHandler__isStarted = False
										except: pass
										for _cm in getattr(_aih, '_AvatarInputHandler__ctrls', {}).values():
											try: _cm.destroy()
											except: pass
										try:
											import game
											if hasattr(_aih, '_AvatarInputHandler__onRecreateDevice'):
												game.g_guiResetters.remove(_aih._AvatarInputHandler__onRecreateDevice)
										except: pass
										try: player.inputHandler = None
										except: pass
								except Exception as e:
									import traceback
									LOG_DEBUG('Failed to stop AIH:', traceback.format_exc())

								import gui.mods.offhangar._constants as _c
								from gui import WindowsManager

								try:
									if hasattr(WindowsManager.g_windowsManager, 'destroyBattle'):
										WindowsManager.g_windowsManager.destroyBattle()
									else:
										WindowsManager.g_windowsManager.hideAll()
								except Exception:
									pass

								try:
									global g_offline_models
									# Clear FIRST: delModel's pending error raises at loop
									# exhaustion; a post-loop clear was being skipped, so the
									# player's WRECK models leaked into the next battle (the
									# 'thrown back into the same round with my dead tank' bug).
									_gm_list = list(g_offline_models)
									g_offline_models = []
									for m in _gm_list:
										_offh_del_model(m)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								# The ownership sweep ran before any teardown in this continuation.
								try:
									global g_projectile_mover
									if g_projectile_mover is not None:
										g_projectile_mover.destroy()
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								try:
									BigWorld.camera(None)
									BigWorld.worldDrawEnabled(False)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								try:
									import gui.ClientHangarSpace
									LOG_DEBUG('ClientHangarSpace module dir:', dir(gui.ClientHangarSpace))
									LOG_DEBUG('ClientHangarSpace class dir:', dir(gui.ClientHangarSpace.ClientHangarSpace))
								except Exception as e:
									LOG_DEBUG('ClientHangarSpace error:', e)

								try:
									BigWorld.worldDrawEnabled(True)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								try:
									from gui import WindowsManager

									if hasattr(WindowsManager.g_windowsManager, 'showLobby'):
										WindowsManager.g_windowsManager.showLobby()
										LOG_DEBUG('Triggered showLobby() for full UI and camera reload!')

									from gui.Scaleform.utils.HangarSpace import g_hangarSpace
									if g_hangarSpace is not None:
										try:
											g_hangarSpace.destroy()
										except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())
										# WG 'no man's land' purge: hangar destroyed, nothing active, before re-init.
										_offh_safe_purge()
										g_hangarSpace.init(True)
										g_hangarSpace.refreshVehicle()
										LOG_DEBUG('Restored HangarSpace via global instance!')
									else:
										LOG_DEBUG('Global g_hangarSpace is None!')

								except Exception as e:
									import traceback
									LOG_DEBUG('HangarSpace restore error:', traceback.format_exc())

								# The battle-exit resync can leave a stale 'download/...'
								# entry in the global Waiting overlay (its completion
								# callback is lost in the lobby transition). The overlay
								# then resurfaces over the next opened view - e.g. the
								# Research screen - as an infinite spinner, although the
								# view underneath loaded fine. Flush once things settle.
								def _flush_stale_waiting():
									try:
										from gui.Scaleform.Waiting import Waiting
										Waiting.close()
										LOG_DEBUG('OfflineBattle: flushed stale Waiting overlay')
									except Exception:
										pass
								try:
									BigWorld.callback(3.0, _flush_stale_waiting)
								except Exception:
									pass

								try:
									BigWorld.worldDrawEnabled(True)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								# Set the allow flag and trigger native exit
								for _e in BigWorld.entities.values():
									if _e.__class__.__name__ in ('PlayerAccount', 'Account'):
										_e._offline_allow_become_non_player = True
										if hasattr(_e, '_offhangar_orig_stats') and _e._offhangar_orig_stats is not None:
											_e.stats = _e._offhangar_orig_stats
										try: _e.showGUI(_c.OFFLINE_GUI_CTX)
										except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())
							except Exception as e:
								import traceback
								LOG_DEBUG('Player exit battle err:', traceback.format_exc())

						# Post-death: enter ally spectator instead of auto-exiting. The tick follows
						# a living ally; K/ESC still leaves via the normal exit path.
						LOG_DEBUG('OfflineBattle: player dead -> ally spectator (K to exit)')
						# native post-death GUI overlay (post-mortem tips panel)
						try:
							from gui import WindowsManager as _pmwm
							_pmwm.g_windowsManager.showPostMortem()
						except Exception: pass
				except Exception as e: LOG_DEBUG('Player death check err:', str(e))
				# Post-death spectator. Target 0 = the dead tank itself (stay on it first);
				# left-click then cycles to living team-mates. Drive WGs native post-death
				# panels via onPostmortemVehicleChanged (offline mocks are not real entities so
				# PostMortemControlMode cannot; we point the camera + panels directly).
				try:
					_pl_s = BigWorld.player()
					if getattr(_pl_s, '_offh_spectating', False):
						# dead -> hide the aim reticle (incl. reload/ammo timer) + gun marker
						try:
							_saim = getattr(getattr(_pl_s, 'inputHandler', None), 'aim', None)
							if _saim is not None:
								try: _saim.setVisible(False)
								except Exception: pass
								try: _saim.component.visible = False
								except Exception: pass
							_sctrl = getattr(getattr(_pl_s, 'inputHandler', None), 'ctrl', None)
							if _sctrl is not None:
								try: _sctrl.showGunMarker(False)
								except Exception: pass
								try: _sctrl.showGunMarker2(False)
								except Exception: pass
								# showGunMarker asserts __isEnabled; if the ctrl is disabled the assert is
								# swallowed and the Zielkreis stays. Hide the _SuperGunMarker directly.
								for _gmn in ('_ArcadeControlMode__gunMarker', '_SniperControlMode__gunMarker', '_ArtyControlMode__gunMarker', '_StrategicControlMode__gunMarker'):
									_gm = getattr(_sctrl, _gmn, None)
									if _gm is not None:
										# hide the _SuperGunMarker's two flash markers DIRECTLY. Never call show2(False):
										# in retail it runs show(not flag)=show(True), re-showing the marker we just hid.
										for _gmi in ('_SuperGunMarker__gm1', '_SuperGunMarker__gm2'):
											_gmf = getattr(_gm, _gmi, None)
											if _gmf is not None:
												try: _gmf.show(False)
												except Exception: pass
							# hide the bottom-left tank indicator (rotating silhouette): offline mocks are
							# never _setup so it keeps the player's live turret matrix and keeps spinning.
							try:
								from gui import WindowsManager as _wmti
								_bwti = getattr(_wmti.g_windowsManager, 'battleWindow', None)
								_dpti = getattr(_bwti, 'damagePanel', None) if _bwti is not None else None
								_uiti = getattr(_dpti, '_DamagePanel__ui', None) if _dpti is not None else None
								_compti = getattr(_uiti, 'component', None) if _uiti is not None else None
								_mcti = getattr(_compti, 'tankIndicator', None) if _compti is not None else None
								if _mcti is not None: _mcti.visible = False
							except Exception: pass
						except Exception: pass
						_mvs = globals().get('G_MOCK_VEHICLES', {}) or {}
						_pteam = getattr(_pl_s, '_offhangar_team', 1)
						_pvid = getattr(_pl_s, 'playerVehicleID', -1)
						_targets = []
						_pmk = _mvs.get(_pvid)
						if _pmk is not None: _targets.append((_pvid, _pmk))
						for _mk, _mv2 in _mvs.items():
							if _mk == _pvid: continue
							if (getattr(_mv2, 'health', 0) or 0) <= 0 or not getattr(_mv2, 'isAlive', False): continue
							_vt = getattr(_mv2, '_bot_team', None)
							if _vt is None:
								_pi = getattr(_mv2, 'publicInfo', None)
								_vt = _pi.get('team', 2) if _pi is not None else 2
							if _vt == _pteam: _targets.append((_mk, _mv2))
						if _targets:
							# A click on a name in the players panel arrives as _offh_spec_want
							# (Account.selectPlayer). Consume it here: the list is rebuilt every
							# frame, so a vehicle id is stable where an index is not.
							_want = getattr(_pl_s, '_offh_spec_want', None)
							if _want is not None:
								_pl_s._offh_spec_want = None
								for _wi in range(len(_targets)):
									if _targets[_wi][0] == _want:
										_pl_s._offh_spec_idx = _wi
										break
							_si = getattr(_pl_s, '_offh_spec_idx', 0) % len(_targets)
							_pl_s._offh_spec_idx = _si
							_aid, _amock = _targets[_si]
							_scam = BigWorld.camera()
							if getattr(_pl_s, '_offh_spec_cur', None) != _aid:
								_pl_s._offh_spec_cur = _aid
								# Bind the camera to a LIVE translation provider ONCE per target change.
								# The old code built a fresh Math.Matrix and re-assigned cam.target EVERY
								# frame: ~60 new matrix objects/s (the churn that crashed this client before)
								# carrying a one-frame-stale position, and the constant re-assign fought WG's
								# own camera update - that is the postmortem judder. The mock's .matrix is a
								# persistent Math.Matrix mutated in place each tick, so the provider tracks it
								# for free (same pattern as _force_camera_to_model).
								try:
									_amx = getattr(_amock, 'matrix', None)
									if _scam is not None and hasattr(_scam, 'target') and _amx is not None:
										_smp = Math.WGTranslationOnlyMP()
										_smp.source = _amx
										_scam.target = _smp
										_pl_s._offh_spec_mp = _smp   # keep a ref alive; a GC'd provider drops the camera
								except Exception as _sce:
									LOG_DEBUG('Spectator camera bind error:', str(_sce))
								try:
									from gui import WindowsManager as _pmwm2
									_bw2 = getattr(_pmwm2.g_windowsManager, 'battleWindow', None)
									if _bw2 is not None:
										if hasattr(_bw2, 'onPostmortemVehicleChanged'):
											_bw2.onPostmortemVehicleChanged(_aid)
										# Stock 0.8.2 moves both the player and camera minimap
										# markers in Minimap.__resetCamera('postmortem'). That
										# method requires a real BigWorld entity; use the same
										# matrices directly for offline mock vehicles.
										try:
											from gui.mods.offhangar.spectator_minimap import follow_mock_vehicle
											follow_mock_vehicle(
												getattr(_bw2, 'minimap', None),
												getattr(_pl_s, 'playerVehicleID', -1), _aid,
												getattr(_amock, 'matrix', None),
												_pl_s.getOwnVehicleMatrix(),
												getattr(BigWorld.camera(), 'invViewMatrix', None), Math)
										except Exception as _sme:
											LOG_DEBUG('Spectator minimap bind error:', str(_sme))
										# switchToVehicle() waits for a real BigWorld.entity (offline mocks never
										# are) so it resets HP to 0 forever - feed the mock's max HP straight in.
										_dp = getattr(_bw2, 'damagePanel', None)
										if _dp is not None:
											_amh = getattr(getattr(_amock, 'typeDescriptor', None), 'maxHealth', None) or getattr(_amock, 'maxHealth', None) or int(getattr(_amock, 'health', 1000) or 1000)
											try: _dp._DamagePanel__callFlash('setMaxHealth', [int(_amh)])
											except Exception: pass
								except Exception: pass
							# keep the spectated tank's HP bar live each frame (it takes damage as it fights)
							try:
								from gui import WindowsManager as _pmwm3
								_bw3 = getattr(_pmwm3.g_windowsManager, 'battleWindow', None)
								_dpf = getattr(_bw3, 'damagePanel', None) if _bw3 is not None else None
								if _dpf is not None:
									# Target 0 of the spectator list is the player's OWN wreck, so this push
									# owns the bar right after death - reading .health straight put a drowned
									# tank back to 0 every frame, undoing the drown block's display value.
									_dpf.updateHealth(_offh_hp_display(_amock))
							except Exception: pass
				except Exception:
					pass
				_offh_perf_stop('post_bot', _perf_post_bot)
				_offh_perf_frame_end(_perf_frame_started, _frame_dt, player)
				if (not _battle_finished[0] and
						globals().get('g_offh_battle_gen', 0) == _offh_my_gen[0]):
					globals()['g_offh_aih_callback_id'] = BigWorld.callback(
						0.0, _aih_tick)
			except Exception as e:
				import traceback
				LOG_DEBUG('AIH_TICK CRASH:', traceback.format_exc())
				if (not _battle_finished[0] and
						globals().get('g_offh_battle_gen', 0) == _offh_my_gen[0]):
					globals()['g_offh_aih_callback_id'] = BigWorld.callback(
						0.0, _aih_tick)
			return
		globals()['g_offh_aih_callback_id'] = BigWorld.callback(
			0.0, _aih_tick)

		# Patch SniperCamera.__cameraUpdate to sync camera source position every frame
		try:
			import AvatarInputHandler.cameras as _cams
			_orig_cam_update = getattr(_cams.SniperCamera, '_orig_cam_update', None)
			if not _orig_cam_update:
				_orig_cam_update = _cams.SniperCamera._SniperCamera__cameraUpdate
				_cams.SniperCamera._orig_cam_update = _orig_cam_update
			_mv_ref = mock_veh
			_vm_ref = veh_matrix
			def _patched_cam_update(cam_self, *a, **kw):
				_orig_cam_update(cam_self, *a, **kw)
				try:
					cam = getattr(cam_self, '_SniperCamera__cam', None)
					if cam is not None and hasattr(cam, 'source'):
						if 'gun_node_matrix' in loaded_models:
							cam.source = loaded_models['gun_node_matrix']
						else:
							mp = Math.WGTranslationOnlyMP()
							mp.source = _vm_ref
							cam.source = mp
				except Exception:
					pass
			_cams.SniperCamera._SniperCamera__cameraUpdate = _patched_cam_update
			_cams.SniperCamera._offhangar_patched = True
			LOG_DEBUG('OfflineBattle.SniperCamera.__cameraUpdate patched')
		except Exception:
			LOG_CURRENT_EXCEPTION()

		# Patch control_modes and cameras ticks to stop gracefully after player is gone
		try:
			import AvatarInputHandler.control_modes as _ctrl
			import AvatarInputHandler.cameras as _cams2

			if hasattr(_ctrl.ArcadeControlMode, '_ArcadeControlMode__tick') and not hasattr(_ctrl.ArcadeControlMode, '_offhangar_patched'):
				# Patch ArcadeControlMode.__tick
				_orig_ctrl_tick = getattr(_ctrl.ArcadeControlMode, '_ArcadeControlMode__tick')
				def _safe_ctrl_tick(self_cm, *a, **kw):
					if BigWorld.player() is None:
						return  # Stop ticking after battle ends
					return _orig_ctrl_tick(self_cm, *a, **kw)
				_ctrl.ArcadeControlMode._ArcadeControlMode__tick = _safe_ctrl_tick
				_ctrl.ArcadeControlMode._offhangar_patched = True

			if hasattr(_cams2, 'ArcadeCamera') and hasattr(_cams2.ArcadeCamera, '_ArcadeCamera__cameraUpdate') and not hasattr(_cams2.ArcadeCamera, '_offhangar_patched'):
				# Patch ArcadeCamera.__cameraUpdate
				_orig_arc_cam = getattr(_cams2.ArcadeCamera, '_ArcadeCamera__cameraUpdate')
				def _safe_arc_cam(self_ac, *a, **kw):
					if BigWorld.player() is None:
						# The original reschedules itself as its FIRST statement, so a
						# plain early return here kills camera pivot updates permanently
						# after one transient None-player tick. Keep the chain alive.
						try:
							self_ac._ArcadeCamera__cameraUpdateCallbackId = BigWorld.callback(0.5, lambda: _safe_arc_cam(self_ac))
						except Exception:
							pass
						return
					return _orig_arc_cam(self_ac, *a, **kw)
				_cams2.ArcadeCamera._ArcadeCamera__cameraUpdate = _safe_arc_cam
				_cams2.ArcadeCamera._offhangar_patched = True

			LOG_DEBUG('OfflineBattle.control_modes/cameras ticks patched for safe exit')
		except Exception:
			LOG_CURRENT_EXCEPTION()

		_install_input_chain_debug()
		g_offline_aih = AvatarInputHandler.AvatarInputHandler()
		# game.handleMouseEvent calls player.inputHandler.handleMouseEvent directly;
		# it does not route through player.handleMouseEvent. Count delivery at this
		# exact instance entry so the GUI-eaten fallback cannot double-apply a wheel
		# event which the normal game path already delivered. Increment before the
		# stock gate: reaching a stopped/detached AIH is an intentional rejection,
		# not permission to bypass it with a direct camera mutation.
		_offh_wrap_aih_mouse_delivery(g_offline_aih, player)
		player.inputHandler = g_offline_aih
		try:
			g_offline_aih.start()
		except Exception as e:
			import traceback
			LOG_DEBUG('AvatarInputHandler.start ERROR:', traceback.format_exc())

		# After AIH.start(), forcibly redirect camera to our spawn position.
		# AIH may set cam.target to (0,0,0) from a defaulted entity matrix.
		# We override it directly using CursorCamera.
		def _force_camera_to_model():
			try:
				import BigWorld, Math
				cam = BigWorld.camera()
				if cam is not None and hasattr(cam, 'target'):
					# Set cam.target to a translation-only provider tracking veh_matrix.
					# This prevents the camera from turning when the tank hull turns.
					mp = Math.WGTranslationOnlyMP()
					mp.source = veh_matrix
					cam.target = mp
					LOG_DEBUG('OfflineBattle.force_camera: set target to', veh_pos[0], veh_pos[1], veh_pos[2])
				else:
					LOG_DEBUG('OfflineBattle.force_camera: cam=', cam, 'has target=', hasattr(cam, 'target') if cam else False)
			except Exception as e:
				import traceback
				LOG_DEBUG('OfflineBattle.force_camera ERROR:', traceback.format_exc())
		_offh_battle_callback(0.1, _force_camera_to_model)
		_offh_battle_callback(0.5, _force_camera_to_model)
		_offh_battle_callback(1.0, _force_camera_to_model)


		from gui import WindowsManager
		from gui.Scaleform.Waiting import Waiting
		try:
			player = BigWorld.player()

			import gui.Scaleform.Battle
			import Avatar
			class _FakeAvatarMod(object):
				PlayerAvatar = type(player)

			if hasattr(gui.Scaleform.Battle, 'Avatar'):
				gui.Scaleform.Battle.orig_Avatar = gui.Scaleform.Battle.Avatar
			gui.Scaleform.Battle.Avatar = _FakeAvatarMod

			if hasattr(Avatar, 'PlayerAvatar'):
				Avatar.orig_PlayerAvatar = Avatar.PlayerAvatar
			Avatar.PlayerAvatar = type(player)

			if not hasattr(player, 'denunciationsLeft'):
				player.denunciationsLeft = 0

			if not hasattr(player, 'onSpaceLoaded'):
				class _DummyEvent(object):
					def __iadd__(self, *a, **k): return self
					def __isub__(self, *a, **k): return self
					def __call__(self, *a, **k): return True
					def isActive(self): return True
				player.onSpaceLoaded = _DummyEvent()

			if not hasattr(player, 'playerVehicleID'):
				player.playerVehicleID = 0

			import types
			if hasattr(player, 'getOwnVehicleShotDispersionAngle'):
				# Rebind EVERY battle: the old name-based guard kept the FIRST
				# battle's _gun_state closure forever, so later tanks fired with
				# battle 1's dispersion. Keep the true original on the player.
				_orig_get_disp = getattr(player, '_offh_orig_get_disp', None)
				if _orig_get_disp is None:
					_orig_get_disp = player.getOwnVehicleShotDispersionAngle
					player._offh_orig_get_disp = _orig_get_disp
				def _mock_getOwnVehicleShotDispersionAngle(self, turretRotationSpeed, withShot=0):
					orig = _orig_get_disp(turretRotationSpeed, withShot)
					return (_gun_state.get('dispersion', orig[0]), orig[1])
				player.getOwnVehicleShotDispersionAngle = types.MethodType(_mock_getOwnVehicleShotDispersionAngle, player)

			# VŽDY resetuj životní funkce při nové bitvě
			player.isVehicleAlive = True
			# Crew voices need this object to exist. Retail builds it in
			# Avatar.__startGUI; offline it was only ever created lazily, on the first
			# reload or on death. Until then every voice line hit
			#     getattr(player, 'soundNotifications', None)
			# on a PlayerAccount, which RAISES AttributeError, so getattr answered None
			# and the line was dropped without a word. python.log shows the same gap from
			# the other side: "PlayerAccount object has no attribute soundNotifications".
			# Build it once, here, before anything can want it.
			try:
				if getattr(player, 'soundNotifications', None) is None:
					import gui.IngameSoundNotifications as _ISNb
					player.soundNotifications = _ISNb.IngameSoundNotifications()
					player.soundNotifications.start()
					LOG_DEBUG('OfflineBattle: soundNotifications created at battle start')
			except Exception as _isne:
				LOG_DEBUG('soundNotifications init err:', str(_isne))
			player._is_dead = False
			player._offh_spectating = False
			player._offh_spec_cur = None
			player._offh_spec_idx = 0
			# Belt and braces: if a previous battle exited on a path that skipped the
			# sweep, the postmortem grading would still be on. The control mode sets its
			# own preset a moment later anyway, but start from a clean slate.
			try:
				from post_processing import g_postProcessing as _offh_pp3
				_offh_pp3.disable()
			except Exception:
				pass
			player._crosshair_init_done = False
			if hasattr(player, 'vehicle') and player.vehicle is not None:
				try: player.vehicle.typeDescriptor = td
				except Exception: pass
				player.vehicle.health = getattr(td, 'maxHealth', 400)
				# This one is the REAL BigWorld vehicle, and Battle.py binds the damage
				# panel to it first: DamagePanel._setup calls vehicle.isAlive(). A plain
				# True shadows the entity's own method and the call raises
				# TypeError: 'bool' object is not callable, killing the panel setup on
				# every retry of its 0.05 s waiting loop.
				_offh_set_alive(player.vehicle, True)

			if not hasattr(player, 'name'):
				player.name = 'Player'
			if not hasattr(player, 'team'):
				player.team = 1






			# ---- consumables / equipment (ported) ----
			def _offh_player_mock():
				import BigWorld
				_mv = globals().get('G_MOCK_VEHICLES', {}) or {}
				return _mv.get(getattr(BigWorld.player(), 'playerVehicleID', -1))

			def _offh_device_ui_state(mock, td, ui_name):
				from gui.mods.offhangar import device_damage as _DDs
				healths = _REPAIR_UI_TO_HEALTH.get(ui_name, (ui_name + 'Health',))
				destroyed = getattr(mock, '_destroyed_devices', None) or set()
				dh = getattr(mock, 'devices_hp', None) or {}
				st = None
				for h in healths:
					if h in destroyed:
						return 'destroyed'
					if h in dh:
						mx = _DDs.device_max_hp(td, h)
						if mx is not None and dh[h] < mx:
							st = 'critical'
				return st

			def _offh_repair_device(mock, td, ui_name):
				from gui.mods.offhangar import device_damage as _DDs
				healths = _REPAIR_UI_TO_HEALTH.get(ui_name, (ui_name + 'Health',))
				dh = getattr(mock, 'devices_hp', None)
				if dh is None:
					dh = {}
					mock.devices_hp = dh
				destroyed = _dev_destroyed_set(mock)
				repaired = False
				for h in healths:
					mx = _DDs.device_max_hp(td, h)
					if (h in destroyed) or (h in dh and (mx is None or dh[h] < mx)):
						repaired = True
					if mx is not None:
						dh[h] = mx
					destroyed.discard(h)
					if getattr(mock, '_module_states', None):
						mock._module_states.pop(h, None)
				_refresh_mobility_flags(mock)
				try:
					import gui.WindowsManager
					bw = gui.WindowsManager.g_windowsManager.battleWindow
					if bw is not None and hasattr(bw, 'damagePanel'):
						for h in healths:
							try: bw.damagePanel.updateState(_module_ui_name(h), 'normal')
							except Exception: pass
				except Exception:
					pass
				return repaired

			def _offh_activate_equipment(idx, deviceName=None):
				import BigWorld, random
				from gui.mods.offhangar import device_damage as _DDs
				mock = _offh_player_mock()
				if mock is None:
					return
				cons = None
				for c in _gun_state.get('consumables', []):
					if c.get('slot') == idx:
						cons = c
						break
				if cons is None or cons.get('used'):
					return
				tag = cons.get('tag')
				name = str(cons.get('name', '')).lower()
				td = _device_td(mock)
				try:
					import gui.WindowsManager
					bw = gui.WindowsManager.g_windowsManager.battleWindow
				except Exception:
					bw = None
				panel = getattr(bw, 'consumablesPanel', None) if bw is not None else None
				def _consume():
					cons['used'] = True
					if panel is not None:
						try: panel.setItemQuantityInSlot(idx, 0)
						except Exception: pass
						try: panel.setCoolDownTime(idx, -1)
						except Exception: pass
				def _err(msg):
					try:
						if bw is not None and hasattr(bw, 'vErrorsPanel'):
							bw.vErrorsPanel.showMessage(msg)
					except Exception:
						pass
				if tag == 'extinguisher':
					if getattr(mock, 'is_on_fire', False):
						_offh_extinguish(mock, mock is _offh_player_mock(), 'extinguisher')
						_consume()
					else:
						_err('extinguisherDoesNotActivated')
					return
				is_big = ('large' in name)
				if tag == 'repairkit':
					if deviceName is not None:
						if _offh_repair_device(mock, td, str(deviceName)):
							_consume()
						if panel is not None:
							try: panel.collapseEquipmentSlot(idx)
							except Exception: pass
							# collapseEquipmentSlot only animates Flash; the private expand index is
							# otherwise cleared by a callback we cannot rely on offline.
							try: panel._ConsumablesPanel__removeExpandEquipment(idx)
							except Exception: pass
						return
					_damaged = bool(getattr(mock, '_destroyed_devices', None))
					if not _damaged:
						dh = getattr(mock, 'devices_hp', None) or {}
						for _h, _hp in dh.items():
							_mx = _DDs.device_max_hp(td, _h)
							if _mx is not None and _hp < _mx:
								_damaged = True
								break
					if not _damaged:
						_err('repairkitAllDevicesAreNotDamaged')
						return
					if is_big:
						dh = getattr(mock, 'devices_hp', None) or {}
						for _h in list(dh.keys()):
							_mx = _DDs.device_max_hp(td, _h)
							if _mx is not None:
								dh[_h] = _mx
						if getattr(mock, '_destroyed_devices', None):
							mock._destroyed_devices.clear()
						_refresh_mobility_flags(mock)
						if getattr(mock, '_module_states', None):
							mock._module_states.clear()
						if bw is not None and hasattr(bw, 'damagePanel'):
							for _dn in ('engineHealth', 'ammoBayHealth', 'fuelTankHealth', 'radioHealth', 'leftTrackHealth', 'rightTrackHealth', 'gunHealth', 'turretRotatorHealth', 'surveyingDeviceHealth'):
								try: bw.damagePanel.updateState(_module_ui_name(_dn), 'normal')
								except Exception: pass
						_consume()
					else:
						entityStates = {}
						devs = getattr(getattr(td, 'type', None), 'devices', None)
						if devs:
							for d in devs:
								dn = getattr(d, 'name', '')
								if dn.endswith('Health'):
									ui = dn[:-6]
									entityStates[ui] = _offh_device_ui_state(mock, td, ui)
						else:
							for ui in ('engine', 'ammoBay', 'gun', 'turretRotator', 'leftTrack', 'rightTrack', 'surveyingDevice', 'radio', 'fuelTank'):
								entityStates[ui] = _offh_device_ui_state(mock, td, ui)
						if panel is not None:
							try: panel.expandEquipmentSlot(idx, 'repairkit', entityStates)
							except Exception as _ee: LOG_DEBUG('expandEquipmentSlot(repairkit) err:', str(_ee))
						else:
							# No panel to pick from (offline the Flash slot does not always expand):
							# a SMALL kit still repairs exactly ONE module, never the whole tank.
							# Destroyed first, otherwise the worst damaged one.
							_one = None
							_dhk = getattr(mock, 'devices_hp', None) or {}
							_dead = sorted(getattr(mock, '_destroyed_devices', None) or ())
							if _dead:
								_one = _dead[0]
							else:
								_worst = None
								for _h2, _hp2 in sorted(_dhk.items()):
									_mx2 = _DDs.device_max_hp(td, _h2)
									if _mx2 and _hp2 < _mx2:
										_frac = float(_hp2) / float(_mx2)
										if _worst is None or _frac < _worst[0]:
											_worst = (_frac, _h2)
								if _worst is not None:
									_one = _worst[1]
							if _one is not None and _offh_repair_device(mock, td, _module_ui_name(_one)):
								LOG_DEBUG('SMALL REPAIR KIT: repaired %s only (no selection panel)' % _one)
								_consume()
					return
				if tag == 'medkit':
					ko = getattr(mock, '_crew_ko', None) or set()
					if deviceName is not None:
						if deviceName in ko:
							ko.discard(deviceName)
							_recompute_crew_impaired(mock)
							if bw is not None and hasattr(bw, 'damagePanel'):
								try: bw.damagePanel.updateState(str(deviceName), 'normal')
								except Exception: pass
							_consume()
						if panel is not None:
							try: panel.collapseEquipmentSlot(idx)
							except Exception: pass
							try: panel._ConsumablesPanel__removeExpandEquipment(idx)
							except Exception: pass
						return
					if not ko:
						_err('medkitAllTankmenAreSafe')
						return
					if is_big:
						for _cn in list(ko):
							if bw is not None and hasattr(bw, 'damagePanel'):
								try: bw.damagePanel.updateState(_cn, 'normal')
								except Exception: pass
						ko.clear()
						_recompute_crew_impaired(mock)
						_consume()
					else:
						entityStates = {}
						for _cn in _crew_roster(td):
							entityStates[_cn] = 'destroyed' if _cn in ko else None
						if panel is not None:
							try: panel.expandEquipmentSlot(idx, 'medkit', entityStates)
							except Exception as _me: LOG_DEBUG('expandEquipmentSlot(medkit) err:', str(_me))
					return

			def _offh_damage_icon(tag, deviceName=None):
				for c in _gun_state.get('consumables', []):
					if c.get('tag') == tag and not c.get('used'):
						_offh_activate_equipment(c.get('slot'), deviceName)
						return

			# ---- broken-track visual (ported) ----
			def _clear_crashed_track(mock):
				'''Detach and drop a mock's crashed-track overlay, reset the cached state.'''
				cm = getattr(mock, '_crashed_track_model', None)
				parent = getattr(mock, '_crashed_track_parent', None)
				if cm is not None and parent is not None:
					try:
						parent.root.detach(cm)
					except Exception as _de:
						LOG_DEBUG('crashed model detach err:', str(_de))
				mock._crashed_track_model = None
				mock._crashed_track_fashion = None
				mock._crashed_track_parent = None

			def _sync_crashed_track(mock, chassis_model, fashion, td):
				'''Broken-track visual, like the game's _CrashedTrackController. Idempotent
				through a cached (left, right) state; every native call is guarded because a
				fashion on an offline mock is delicate.'''
				if mock is None:
					return
				destroyed = getattr(mock, '_destroyed_devices', None) or set()
				left = 'leftTrackHealth' in destroyed
				right = 'rightTrackHealth' in destroyed
				state = (left, right)
				_dead = (getattr(mock, 'health', 1) <= 0) or getattr(mock, '_is_killed', False)
				# Hot path (every frame, every bot): alive and unchanged -> out before the
				# config read, so this stays cheap.
				if not _dead and getattr(mock, '_crashed_tracks_state', None) == state:
					return
				try:
					from _constants import CONFIG_OPTIONS as _CTV
					if not bool(_CTV.get('crashed_track_visual', True)):
						return
				except Exception:
					pass
				# A dead tank already shows its full destroyed model with broken tracks baked
				# in, so drop the overlay instead of driving it.
				if _dead:
					if getattr(mock, '_crashed_track_model', None) is not None:
						_clear_crashed_track(mock)
					mock._crashed_tracks_state = None
					return
				any_broken = left or right
				crashed_model = getattr(mock, '_crashed_track_model', None)
				crashed_fashion = getattr(mock, '_crashed_track_fashion', None)
				# The live fashion attaches a moment after spawn; until it exists we cannot
				# hide the intact track, so retry next frame rather than cache a half state.
				if fashion is None and (any_broken or crashed_model is not None):
					return
				mock._crashed_tracks_state = state
				# 1) live chassis: hide the intact scrolling track on the broken side(s)
				if fashion is not None:
					try:
						fashion.hideTracks(bool(left), bool(right))
					except Exception as _he:
						LOG_DEBUG('hideTracks(main) err:', str(_he))
				if any_broken:
					# 2) attach the destroyed chassis model + its own fashion, once
					if crashed_model is None and chassis_model is not None and td is not None:
						try:
							crashed_model = BigWorld.Model(td.chassis['models']['destroyed'])
							try:
								crashed_fashion = BigWorld.WGVehicleFashion(True)
							except Exception:
								crashed_fashion = BigWorld.WGVehicleFashion()
							try:
								crashed_fashion.maxMovement = td.physics['speedLimits'][0]
								_sw = td.hull['swinging']
								crashed_fashion.setPitchSwinging('V', *_sw['pitchParams'])
								crashed_fashion.setRollSwinging('V', *_sw['rollParams'])
								crashed_fashion.setShotSwinging('V', _sw['sensitivityToImpulse'])
								_tr = td.chassis['tracks']
								crashed_fashion.setLods(td.chassis['traces']['lodDist'], td.chassis['wheels']['lodDist'], _tr['lodDist'], _sw['lodDist'])
								crashed_fashion.setTracks(_tr['leftMaterial'], _tr['rightMaterial'], _tr['textureScale'])
								crashed_fashion.movementInfo = Math.Vector4(0.0, 0.0, 0.0, 0.0)
							except Exception as _cfe:
								LOG_DEBUG('crashed fashion setup err:', str(_cfe))
							try:
								crashed_model.wg_fashion = crashed_fashion
							except Exception:
								pass
							try:
								chassis_model.root.attach(crashed_model)
								mock._crashed_track_model = crashed_model
								mock._crashed_track_fashion = crashed_fashion
								mock._crashed_track_parent = chassis_model
							except Exception as _ae:
								LOG_DEBUG('crashed model attach err:', str(_ae))
								crashed_model = None
								crashed_fashion = None
						except Exception as _cme:
							LOG_DEBUG('crashed model build err:', str(_cme))
					# show ONLY the broken side(s) on the overlay
					if crashed_fashion is not None:
						try:
							crashed_fashion.hideTracks(not left, not right)
						except Exception as _che:
							LOG_DEBUG('hideTracks(crashed) err:', str(_che))
				else:
					# both tracks functional again: drop the overlay, restore the live tracks
					if crashed_model is not None:
						_clear_crashed_track(mock)

			# ---- crew injuries (ported) ----
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
				from gui.mods.offhangar import device_damage as _DDc
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
					from gui.mods.offhangar import device_damage as _DDc
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
					devices_hp = getattr(mock, 'devices_hp', None)
					destroyed_devices = getattr(mock, '_destroyed_devices', None)
					if not devices_hp and not destroyed_devices:
						return 1.0
					from gui.mods.offhangar import device_damage as _DDm
					return _DDm.module_stat_factor(devices_hp, destroyed_devices,
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
				if is_player_target:
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

			# ---- module damage / repair support (ported from the shared build) ----
			_REPAIR_UI_TO_HEALTH = {
				'engine': ('engineHealth',), 'ammoBay': ('ammoBayHealth',),
				'gun': ('gunHealth',), 'turretRotator': ('turretRotatorHealth',),
				'surveyingDevice': ('surveyingDeviceHealth',), 'radio': ('radioHealth',),
				'fuelTank': ('fuelTankHealth',),
				'chassis': ('leftTrackHealth', 'rightTrackHealth'),
				'track': ('leftTrackHealth', 'rightTrackHealth'),
				'leftTrack': ('leftTrackHealth',), 'rightTrack': ('rightTrackHealth',),
			}

			def _dev_destroyed_set(mock):
				s = getattr(mock, '_destroyed_devices', None)
				if s is None:
					s = set()
					mock._destroyed_devices = s
				return s

			def _module_ui_name(name):
				# Damage-panel device name = extra name minus 'Health'; tracks keep their
				# side, which is what the real 0.8.2 Avatar sends.
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

			# These live in the battle scope, but _offh_knock_out_everything is module-level
			# and is what every death path calls. Reaching them from there raised NameError
			# into a bare except, so a destroyed tank pushed NOTHING to the damage panel -
			# 'KNOCKOUT called: ... crew=0' in the log with no follow-up line was exactly
			# that (_crew_roster has a 5-man fallback and cannot return an empty list).
			for _hn, _hf in (('_device_td', _device_td),
				('_crew_roster', _crew_roster),
				('_recompute_crew_impaired', _recompute_crew_impaired),
				('_dev_destroyed_set', _dev_destroyed_set),
				('_module_ui_name', _module_ui_name),
				('_refresh_mobility_flags', _refresh_mobility_flags)):
				globals()[_hn] = _hf

			def _offh_play_crit_voice(snd):
				'''Queue one module or crew voice line for the player's own tank.

				No throttling here, deliberately. gui/sound_notifications.xml gives every
				module and crew line playRules 3 - append to the voice queue and wait its
				turn - and the engine already rate-limits repeats of the SAME line through
				minTimeBetweenEvents. Only playRules 1 wipes the queue, and that is reserved
				for the kill lines. An earlier version of this function held a single global
				0.7 s gate that dropped any line following any other one whatever they were,
				so a shell that broke a track AND downed the driver reported whichever won
				the race and silently swallowed the other.

				The binding is explicit because every one of these events sets
				shouldBindToPlayer, which makes play() resolve it through
				BigWorld.player().vehicle.id - a stub offline. __playFirstFromQueue drops any
				queued line whose bound vehicle is not in arena.vehicles or not alive, with no
				error, which is the other half of why these lines came and went.'''
				import BigWorld
				# One strike, one report. A single shell now routinely crits two or three
				# things at once (34 such strikes in one battle log), and every line is
				# ~2.5 s while WG drops anything that has waited longer than its 3 s
				# timeout - so in a busy fight the extra lines were not being heard, they
				# were being binned. While a strike is scoring, calls land in this list and
				# the worst one is spoken at the end of it.
				if _OFFH_VOICE_BURST[0] is not None:
					if snd:
						_OFFH_VOICE_BURST[0].append(snd)
					return
				if not snd:
					return
				try:
					_p = BigWorld.player()
					# OWN queue for crew and module lines.
					#
					# IngameSoundNotifications keeps ONE active event per category, and in
					# sound_notifications.xml practically everything is category 'voice':
					#   module/crew lines   playRules 3  - queue up and wait
					#   armor_pierced_*     playRules 2  - jump the queue
					#   *_killed_*          playRules 1  - WIPE the queue, stop what is talking
					#
					# Sharing one queue means the polite lines never get a turn in a close
					# fight, and get cut mid-word when they do - which reads as "crew sounds
					# break when enemies are near". Measured: it is NOT channel starvation,
					# the log shows 12 live events, 4 shot effects a second and zero failures.
					# A second instance gives them a queue that hit and kill reports cannot
					# reach. Same class, same rules - only the contention is gone.
					_sn = globals().get('g_offh_crew_notif')
					if _sn is None:
						try:
							import gui.IngameSoundNotifications as _ISNc
							_sn = _ISNc.IngameSoundNotifications()
							_sn.start()
							globals()['g_offh_crew_notif'] = _sn
							LOG_DEBUG('OfflineBattle: crew voice queue created (separate from hit/kill reports)')
							# DIAGNOSTIC SHIM. Only three things can stop a line that is already
							# speaking: a playRules 1 event, enable(False) and enableCategory - and none
							# of them should ever reach THIS instance. Wrap the two methods that do the
							# stopping so the caller has to identify itself. The wrapper only logs and
							# forwards; behaviour is unchanged. Remove once the cutter is found.
							try:
								import traceback as _tbv
								def _crew_shim(_inst, _attr, _label):
									_orig = getattr(_inst, _attr, None)
									if _orig is None:
										return
									def _wrapped(*_a, **_kw):
										try:
											LOG_DEBUG('CREWVOICE CUT: %s%s' % (_label, _a and (' ' + repr(_a)) or ''))
											for _ln in _tbv.format_stack()[-7:-1]:
												LOG_DEBUG('   ' + _ln.strip().replace(chr(10), ' | '))
										except Exception:
											pass
										return _orig(*_a, **_kw)
									setattr(_inst, _attr, _wrapped)
								_crew_shim(_sn, '_IngameSoundNotifications__clearQueue', 'clearQueue')
								_crew_shim(_sn, 'cancel', 'cancel')
								_crew_shim(_sn, 'enable', 'enable')
								_crew_shim(_sn, 'enableCategory', 'enableCategory')
							except Exception as _she:
								LOG_DEBUG('crew voice shim err:', str(_she))
						except Exception as _cne:
							LOG_DEBUG('crew voice queue err:', str(_cne))
							_sn = getattr(_p, 'soundNotifications', None)
					if _sn is None:
						return
					# NEVER bind these to a vehicle. __playFirstFromQueue re-checks the
					# binding at PLAY time against arena.vehicles - a fake arena here - and a
					# miss makes it `continue` with no error at all. Binding buys nothing
					# either: these are the player's own crew lines, and an unbound line
					# always plays. One silent-drop path closed for free.
					_vid = None
					# Verify the instance instead of trusting it. destroy() leaves an object
					# that looks fine but has __soundQueues = None and __isEnabled False, and
					# it then swallows every line for the rest of the battle without a word.
					try:
						_qs = getattr(_sn, '_IngameSoundNotifications__soundQueues', None)
						_en = getattr(_sn, '_IngameSoundNotifications__isEnabled', False)
						if _qs is None or not _en:
							import gui.IngameSoundNotifications as _ISNr
							_sn = _ISNr.IngameSoundNotifications()
							_sn.start()
							globals()['g_offh_crew_notif'] = _sn
							LOG_DEBUG('CREWVOICE instance REBUILT (was dead: queues=%s enabled=%s)' % (_qs is not None, _en))
					except Exception, _e_rb:
						LOG_DEBUG('CREWVOICE rebuild failed: %s' % _e_rb)
					# Which instance actually speaks? The last log had no 'queue created' line
					# and still played, which would mean these lines go into WG's instance -
					# where every playRules 1 kill report stops whatever is speaking.
					try:
						if not globals().get('_offh_crew_inst_logged'):
							globals()['_offh_crew_inst_logged'] = True
							LOG_DEBUG('CREWVOICE instance: ours=%s wg=%s' % (
								_sn is globals().get('g_offh_crew_notif'),
								_sn is getattr(_p, 'soundNotifications', None)))
					except Exception:
						pass
					# Never let a backlog build. playRules 3 means APPEND, and the mod feeds this
					# queue far more often than the server ever would: one line per device state
					# change, so a single shell that crits three modules queues three, auto-repair
					# adds a *_functional line per module as it crosses back, and a destroyed tank
					# knocks out nine modules plus the crew at once. Each line then plays in full,
					# one after another, seconds after the event that caused it - that is the
					# lagging and stuttering. Retail never has a backlog because the server sends
					# one or two crits per shot, not a state machine.
					#
					# Rule: at most ONE line waiting. A newer crit is worth more than an older one
					# still queued, so the fresh line replaces the stale one instead of lining up
					# behind it. What is currently SPEAKING is never cut.
					try:
						_q = getattr(_sn, '_IngameSoundNotifications__soundQueues', None)
						_vq = _q.get('voice') if _q else None
						if _vq is not None and len(_vq) >= 1:
							del _vq[:]
					except Exception:
						pass
					# NO burst rule here any more, and it must not come back.
					#
					# It used to cancel the previous line whenever a new one arrived within
					# 2 s, on the theory that the newest report is the truest. What that
					# actually did was cut the line that was still speaking - which IS the
					# "crew sounds get cut off" report. The log caught it red-handed:
					#   CREWVOICE play: driver_killed
					#   CREWVOICE play: radio_damaged
					#   CREWVOICE CUT: cancel ('driver_killed', False)
					# One shell that downs the driver and knocks the radio about produces two
					# crits milliseconds apart, and the second one silenced the first mid-word.
					# Interior crits make that combination the normal case rather than a rare
					# one, so the rule went from occasionally rude to constantly wrong.
					#
					# The problem it was aimed at - a queued line landing long after the moment
					# it describes - is already solved by WG: play() stamps every queue item
					# with `time + soundDesc['timeout']`, timeout defaults to 3.0 s, and
					# __playFirstFromQueue silently drops any item whose stamp has passed. A
					# report that cannot be spoken within 3 s is discarded on its own, without
					# anyone having to interrupt the line in progress.
					# A destroyed line makes the damaged one obsolete. Appending it (playRules
					# 3) means the crew keeps reporting the track as merely damaged for another
					# ~2.5 s while it is already gone and repairs may be running - the 'lagging
					# behind' the tester described. So the weaker line for the SAME device is
					# dropped from the WAITING queue.
					#
					# Queue only. cancel() would also stop the weaker line if it happened to be
					# the one speaking, and cutting a line in progress is the very complaint
					# this build set out to fix. Deleting queue entries is safe: it is exactly
					# what cancel() does to the queue, and it never touches __activeEvents.
					#
					# Do NOT generalise this by stopping the active sound directly and clearing
					# activeEvents by hand - 1.3.6 did that and the crew went silent for the
					# whole battle: __onSoundEnd still fires for the stopped sound, finds the
					# slot already empty, and the queue pump WG drives from there never runs
					# again.
					try:
						if snd.endswith('_destroyed'):
							_weak = snd[:-len('_destroyed')] + '_damaged'
							_evs = getattr(_sn, '_IngameSoundNotifications__events', None) or {}
							_wpath = ((_evs.get(_weak) or {}).get('voice') or {}).get('sound')
							_qv = (getattr(_sn, '_IngameSoundNotifications__soundQueues', None) or {}).get('voice')
							if _wpath and _qv:
								for _qi in range(len(_qv) - 1, -1, -1):
									if _qv[_qi][0] == _wpath:
										del _qv[_qi]
										LOG_DEBUG('CREWVOICE dropped queued %s, superseded by %s' % (_weak, snd))
					except Exception:
						pass
					LOG_DEBUG('CREWVOICE play: %s bind=%s' % (snd, _vid))
					# Own try/except around the call. The outer one is bare, so every
					# exception play() raised so far was discarded - and play() CAN raise
					# here: it re-binds unbound lines via BigWorld.player().vehicle.id, and
					# our player is a mock. Report it instead of guessing at it.
					try:
						_sn.play(snd, _vid)
					except Exception, _e_pl:
						LOG_DEBUG('CREWVOICE play RAISED: %s: %s' % (type(_e_pl).__name__, _e_pl))
					# What did the call actually do? An empty queue with nothing active means
					# it never enqueued (unknown event name, or minTimeBetweenEvents). A
					# non-empty queue with something already active means the pump is stuck:
					# __playFirstFromQueue only runs `if activeEvents[category] is None`, so a
					# sound that never reports finishing mutes the whole rest of the battle.
					try:
						_qd = getattr(_sn, '_IngameSoundNotifications__soundQueues', None)
						_ad = getattr(_sn, '_IngameSoundNotifications__activeEvents', None)
						_av = (_ad or {}).get('voice')
						LOG_DEBUG('CREWVOICE after play: queued=%s active=%s' % (
							len((_qd or {}).get('voice') or []), _av and _av.get('soundPath')))
					except Exception:
						pass
					# An EARLY-END probe used to sit here. It is gone because it measured the
					# wrong thing: `duration` reports the LONGEST variant of a random FMOD
					# container, so a shorter variant ending normally looked like a cut. Its
					# verdict, before it was believed too far: two lines ended with WG's voice
					# slot empty the whole time and one ended while WG's line had already run
					# 1.44 s ALONGSIDE ours - no exclusive channel, nothing to steal.
				except Exception:
					pass

			def _push_device_ui(target_mock, is_player_target, name, current_hp, max_hp, state=None):
				# Entry probe. CREWVOICE play sits at the END of this chain, so a zero there
				# cannot tell "no module was ever damaged" apart from "the chain broke on the
				# way". This fires for EVERY module state push, bots included, before any of
				# the guards below.
				try:
					LOG_DEBUG('DEVUI %s hp=%s/%s state=%s player=%s' % (name, current_hp, max_hp, state, is_player_target))
				except Exception:
					pass
				# The damage panel only ever shows the PLAYER's own modules.
				if not is_player_target:
					return
				try:
					from gui.mods.offhangar import device_damage as _DDui
					import gui.WindowsManager
					bw = gui.WindowsManager.g_windowsManager.battleWindow
					if bw is None or not hasattr(bw, 'damagePanel'):
						return
					dev_state = state if state is not None else _DDui.device_state(current_hp, max_hp)
					ui_name = _module_ui_name(name)
					# updateState is the real 0.8.2 method (Battle.py:1491). The old code called
					# updateDeviceState, which does not exist - it raised into a bare except and
					# the panel never showed a single module hit.
					try: bw.damagePanel.updateState(ui_name, dev_state)
					except Exception as _e2: LOG_DEBUG('updateState error:', ui_name, dev_state, str(_e2))
					# Speak only when a module gets WORSE.
					#
					# This fired on every state change, and crew auto-repair produces a stream of
					# them: a destroyed module climbing back to its regen cap crosses into
					# 'critical', which announced "engine damaged" for a module that had just
					# gotten BETTER. Several modules repairing at once meant a constant feed into
					# a queue whose lines are ~2 s each - lines arriving late, on top of each
					# other, cut short. Retail has no such stream: the server reports a crit when
					# one happens and says nothing while the crew patches things up.
					try:
						# 'repaired' ranks with 'critical': the module is back in service but
						# still damaged, so it must not re-arm the announcement latch either.
						_rank = {'normal': 0, 'repaired': 1, 'critical': 1, 'destroyed': 2}
						_vs = getattr(target_mock, '_voice_states', None)
						if _vs is None:
							_vs = {}
							target_mock._voice_states = _vs
						# _vs holds what was ANNOUNCED, never merely what is current. Writing the
						# current state here was the overload: auto-repair lifts a destroyed
						# module into 'critical', which lowered the latch, and the next hit back
						# to 'destroyed' counted as a fresh worsening and spoke again. Repairs
						# make that boundary oscillate, so the same track got reported over and
						# over.
						#
						# A partial recovery must NOT re-arm anything. Only a finished repair -
						# back to 'normal' - does. One announcement per destruction, silence
						# however often it is hit while it lies there, and it may speak again
						# only after it has been repaired and destroyed anew. Same for every
						# module, which is what keeps a busy fight from turning into a stream.
						_was = _rank.get(_vs.get(name, 'normal'), 0)
						_now_r = _rank.get(dev_state, 0)
						if dev_state == 'normal':
							_vs[name] = 'normal'
						# Recovery lines are not announcements of damage, so they bypass the
						# worsening latch - retail plays one every time a module comes back. The
						# extra's sound keys are NOT the panel's state names (avatar.py maps
						# them): 'fixed' on a full repair, 'functional' when a destroyed module
						# is back in service, and 'functionalCanMove' for a track when the other
						# one is still under the tank.
						_snd_key = None
						if dev_state == 'normal':
							_snd_key = 'fixed'
						elif dev_state == 'repaired':
							_snd_key = 'functional'
							if name in ('leftTrackHealth', 'rightTrackHealth'):
								_other = 'rightTrackHealth' if name == 'leftTrackHealth' else 'leftTrackHealth'
								if _other not in (getattr(target_mock, '_destroyed_devices', None) or ()):
									_snd_key = 'functionalCanMove'
						elif _now_r > _was:
							_vs[name] = dev_state
							_snd_key = dev_state
						if _snd_key is not None:
							_tdu = getattr(BigWorld.player(), 'vehicleTypeDescriptor', None)
							_ex = _tdu.extrasDict.get(name) if (_tdu is not None and hasattr(_tdu, 'extrasDict')) else None
							_snd = getattr(_ex, 'sounds', {}).get(_snd_key) if _ex is not None else None
							_offh_play_crit_voice(_snd)
					except Exception:
						pass
				except Exception as _e:
					LOG_DEBUG('DAMAGE_PANEL_UI_ERR:', str(_e))

			# _offh_extinguish is module level (every fire path calls it) and needs this
			# one; publish it the same way the other battle-scope helpers are published.
			globals()['_push_device_ui'] = _push_device_ui

			def _tick_module_repair(mock, td, dt, is_player_target, repair_skill=100.0, has_big_kit=False):
				'''Crew auto-repair: destroyed modules climb back to functional (~50%) over
				repair_seconds (scaled by crew skill, toolbox, large kit); damaged modules
				regen toward the same cap. Drives the panel repair bar and state icons, and
				clears the mobility flags when tracks/engine come back.'''
				if mock is None or dt is None or dt <= 0:
					return
				# A destroyed vehicle repairs nothing - its crew is gone. Only the PLAYER call
				# site checked this, so dead bots kept repairing, and on the frame the player
				# died the panel still showed a repair running on a wreck.
				if (getattr(mock, 'health', 0) or 0) <= 0 or getattr(mock, '_is_killed', False):
					return
				dh = getattr(mock, 'devices_hp', None)
				if not dh:
					return
				from gui.mods.offhangar import device_damage as _DDr
				destroyed = _dev_destroyed_set(mock)
				states = getattr(mock, '_module_states', None)
				if states is None:
					states = {}
					mock._module_states = states
				bw = None
				if is_player_target:
					try:
						import gui.WindowsManager
						bw = gui.WindowsManager.g_windowsManager.battleWindow
					except Exception:
						bw = None
				for _name in list(dh.keys()):
					max_hp = _DDr.device_max_hp(td, _name)
					if max_hp is None:
						continue
					cap = _DDr.device_regen_hp(td, _name)
					if not cap:
						cap = int(max_hp * _DDr.CRITICAL_HP_FRACTION)
					hp = dh[_name]
					# The fuel tank is not patched up while it burns - the fire ending is what
					# restores it (_offh_extinguish), so it stays red until then.
					_burning_tank = (_name in _DDr.NO_REPAIR_PROGRESS_DEVICES
						and bool(getattr(mock, 'is_on_fire', False)))
					if hp < cap and not _burning_tank:
						hp = _DDr.repair_step_hp(hp, _name, td, dt, repair_skill, has_big_kit)
						dh[_name] = hp
					was_destroyed = _name in destroyed
					functional = hp >= cap
					_repair_done = False
					if was_destroyed and functional:
						destroyed.discard(_name)
						# Repair finished. Do NOT close the bar with (100, 0) - retail never sends
						# 100 at all. The server streams DESTROYED_DEVICE_IS_REPAIRING while the
						# repair runs and then simply stops; what clears the bar is the DEVICE STATE
						# leaving destroyed. Pushing 100 left the bar drawn full at the end of the
						# sequence even though the track was long since fixed. The clear happens
						# below, AFTER the state change, so the panel sees them in retail order.
						_repair_done = True
						_rui = getattr(mock, '_repair_ui_pct', None)
						if _rui is not None:
							_rui.pop(_name, None)
					if _name in destroyed:
						new_state = 'destroyed'
					else:
						new_state = _DDr.device_state(hp, max_hp)
					if is_player_target and bw is not None and hasattr(bw, 'damagePanel') and _name in destroyed and cap > 0 and _name not in _DDr.NO_REPAIR_PROGRESS_DEVICES:
						frac = hp / float(cap)
						if frac < 0.0: frac = 0.0
						elif frac > 1.0: frac = 1.0
						pct = int(round(100.0 * frac))
						secs = _DDr.repair_seconds(_name, td, repair_skill, has_big_kit)
						secs_left = max(0.0, secs * (1.0 - frac))
						# push only when the integer percent changes, so the Flash bar animates
						# smoothly instead of being re-sent every frame
						_rl = getattr(mock, '_repair_ui_pct', None)
						if _rl is None:
							_rl = {}
							mock._repair_ui_pct = _rl
						if _rl.get(_name) != pct:
							_rl[_name] = pct
							try: bw.damagePanel.updateModuleRepair(_module_ui_name(_name), pct, secs_left)
							except Exception as _mre: LOG_DEBUG('updateModuleRepair err:', _module_ui_name(_name), str(_mre))
					if _repair_done and is_player_target and bw is not None and hasattr(bw, 'damagePanel') and _name not in _DDr.NO_REPAIR_PROGRESS_DEVICES:
						# 0 percent / 0 s = nothing in progress. The opening frame starts a bar with
						# (0, seconds), so the zero SECONDS is what marks it finished rather than running.
						try: bw.damagePanel.updateModuleRepair(_module_ui_name(_name), 0, 0.0)
						except Exception: pass
					if _repair_done:
						# Retail order (avatar.py, DEVICE_REPAIRED_TO_CRITICAL): the bar stops,
						# then the device goes to 'repaired' - not straight to 'critical'. That
						# is also what selects the 'functional' / 'functionalCanMove' voice line.
						# Bookkeeping keeps the real state so the next tick pushes nothing.
						_push_device_ui(mock, is_player_target, _name, hp, max_hp, state='repaired')
						states[_name] = new_state
					if states.get(_name) != new_state:
						_push_device_ui(mock, is_player_target, _name, hp, max_hp, state=new_state)
						states[_name] = new_state
				# Unconditional. mobility_dirty only fires on the single frame a module crosses
				# back to functional, so any path that touched the destroyed-set without going
				# through this loop left is_tracked/is_engine_dead latched True - an
				# immobilised tank that never moved again. It is four set lookups.
				_refresh_mobility_flags(mock)
				# Broken-track visual follows the same destroyed-set this tick maintains.
				try:
					if is_player_target:
						_ch_m = loaded_models.get('chassis')
						_fa_m = loaded_models.get('_fashion')
					else:
						_ch_m = getattr(mock, '_chassis_model', None)
						_fa_m = getattr(mock, '_fashion', None)
					_sync_crashed_track(mock, _ch_m, _fa_m, td)
				except Exception as _cte:
					LOG_DEBUG('crashed track sync err:', str(_cte))

			def _offh_he_splash(burst_pos, _shot, attacker_id, direct_id):
				'''HE blast on every OTHER vehicle within explosionRadius.

				The vehicle actually struck is skipped: it is the dist_frac 0 case and its
				damage is applied by the shot path that called us, so counting it here too
				would double it. For each victim a ray is run from the burst point into the
				hull, which yields the real plate facing the blast and the device hitboxes
				behind it out of the vehicle's OWN collision model - the same source a direct
				hit uses, so a tank turned side-on takes the blast on its side armour.'''
				import BigWorld, Math, random
				_R = _offh_he_radius(_shot)
				if _R <= 0.0 or burst_pos is None:
					return
				_shell_s = (_shot.get('shell') or {}) if hasattr(_shot, 'get') else {}
				try:
					_base = float(_shell_s['damage'][0])
				except Exception:
					return
				_pl = BigWorld.player()
				if _pl is None:
					return
				_pvid_s = getattr(_pl, 'playerVehicleID', -1)
				_hit_any = 0
				for _sid2, _sm in ((globals().get('G_MOCK_VEHICLES', {}) or {}).items()):
					if _sid2 == direct_id:
						continue
					if (getattr(_sm, 'health', 0) or 0) <= 0 or not getattr(_sm, 'isAlive', False):
						continue
					_sp = getattr(_sm, 'position', None)
					if _sp is None:
						continue
					_dx = _sp.x - burst_pos.x
					_dy = _sp.y - burst_pos.y
					_dz = _sp.z - burst_pos.z
					_dd = (_dx * _dx + _dy * _dy + _dz * _dz) ** 0.5
					if _dd > _R:
						continue
					_hits_s = []
					_nom_s = 0.0
					try:
						# Aim a metre above the mock position: that is the CHASSIS origin, at track
						# height, so a low burst beside the hull crossed only track material (spaced,
						# skipped) and reported no armour at all.
						_aim_s = Math.Vector3(_sp.x, _sp.y + 1.0, _sp.z)
						_col_s = _sm.collideSegment(burst_pos, _aim_s)
						if _col_s is not None:
							_hits_s = _col_s[3] if len(_col_s) > 3 else []
						_nom_s = _offh_he_nominal_armor(_hits_s, getattr(_sm, 'typeDescriptor', None))
					except Exception:
						_nom_s = _offh_he_hull_armor(getattr(_sm, 'typeDescriptor', None))
					# Same +/-25% spread the direct hit gets (shell damageRandomization).
					_sd = _offh_he_damage(_base * random.uniform(0.75, 1.25), _nom_s, _dd / _R)
					if _sd <= 0:
						continue
					_hit_any += 1
					# A player HE burst may catch a LAN human without directly
					# striking that tank. Reuse the same server-owned HP report as
					# a direct hit, and do not mutate a private copy of remote HP.
					if getattr(_sm, '_network_remote', False) and attacker_id == _pvid_s:
						try:
							from gui.mods.offhangar.network_battle import send_local_hit
							send_local_hit(_pl, getattr(_sm, '_network_server_id', None),
								getattr(_pl, '_offhangar_network_last_fire_seq', None),
								int(_sd), 2,
								getattr(_pl, '_offhangar_network_last_shell_index', 0),
								burst_pos)
							LOG_DEBUG('LAN HE splash reported: target=%s damage=%s' % (
								getattr(_sm, '_network_server_id', None), int(_sd)))
						except Exception:
							LOG_CURRENT_EXCEPTION()
						continue
					if getattr(_sm, '_network_shared_bot', False) and attacker_id == _pvid_s:
						try:
							from gui.mods.offhangar.network_battle import send_local_bot_hit
							send_local_bot_hit(_pl, getattr(_sm, '_network_bot_id', None),
								getattr(_pl, '_offhangar_network_last_fire_seq', None),
								int(_sd), 2, burst_pos)
						except Exception:
							LOG_CURRENT_EXCEPTION()
						continue
					if getattr(_sm, '_network_remote', False) and attacker_id != _pvid_s:
						try:
							_attacker_bot = (globals().get('G_MOCK_VEHICLES', {}) or {}).get(attacker_id)
							from gui.mods.offhangar.network_battle import send_authoritative_bot_human_hit
							send_authoritative_bot_human_hit(_pl,
								getattr(_attacker_bot, '_network_bot_id', None),
								getattr(_sm, '_network_server_id', None),
								getattr(_attacker_bot, '_network_bot_fire_seq', 0),
								int(_sd), 2, burst_pos)
						except Exception:
							LOG_CURRENT_EXCEPTION()
						continue
					# Module and crew crits from the blast. penetrated=False keeps the roll to
					# what sits in front of the plate - splash reaches tracks and external gear,
					# not the ammo bay through 100 mm of hull.
					try:
						_apply_module_damage(_sm, _hits_s, burst_pos, _sp, _sd, _shell_s, attacker_id, False, True)
					except Exception as _hme:
						LOG_DEBUG('HE splash module damage err:', str(_hme))
					_was = getattr(_sm, 'health', 0) or 0
					_act = _sd if _sd < _was else _was
					_sm.health = _was - _sd
					if _act > 0:
						_offh_drop_capture_for_vehicle(
							_sm, attacker_id, 'HE splash damage')
					if attacker_id == _pvid_s:
						_sm.damage_from_player = (getattr(_sm, 'damage_from_player', 0) or 0) + _act
						_sm.hits_from_player = (getattr(_sm, 'hits_from_player', 0) or 0) + 1
						try:
							if not _offh_is_ally(_sm):
								from gui.mods.offhangar import battle_feedback as _offh_feedback_he
								_offh_feedback_he.record_outgoing_hit(
									_offh_stats_for(_pl), _sid2, _act, 2,
									_sm.health <= 0, False, True)
						except Exception:
							pass
					else:
						_sm.damage_from_bots = (getattr(_sm, 'damage_from_bots', 0) or 0) + _act
						try:
							_attacker_mock = (globals().get('G_MOCK_VEHICLES', {}) or {}).get(attacker_id)
							if (_attacker_mock is not None and _offh_is_ally(_attacker_mock) and
									not _offh_is_ally(_sm)):
								_offh_record_spot_assist(_pl, _sm, _act, _sm.health <= 0)
						except Exception:
							pass
					if _sid2 == _pvid_s:
						try:
							from gui.mods.offhangar import battle_feedback as _offh_feedback_received_he
							_offh_feedback_received_he.record_incoming_hit(
								_offh_stats_for(_pl), _act)
						except Exception:
							pass
					_sm.last_killer_id = attacker_id
					LOG_DEBUG('HE SPLASH: target=%s dist=%.1fm/%.1fm armor=%.0f dmg=%d hp=%d' % (
						_sid2, _dd, _R, _nom_s, _sd, max(0, _sm.health)))
					try:
						from gui import WindowsManager as _hewm
						_hebw = getattr(_hewm.g_windowsManager, 'battleWindow', None)
					except Exception:
						_hebw = None
					if _sid2 == _pvid_s:
						# The player caught in his own or a bot's blast: same HP plumbing the
						# direct-hit path uses, or the bar simply would not move.
						try:
							if getattr(_pl, 'vehicle', None):
								_pl.vehicle.health = max(0, _sm.health)
								_pl.guiSessionProvider.invalidateVehicleState(1, _pvid_s, max(0, _sm.health), max(0, _sm.health))
							if _hebw is not None and hasattr(_hebw, 'damagePanel'):
								_hebw.damagePanel.updateHealth(max(0, _sm.health))
						except Exception:
							pass
					else:
						try:
							_mk = getattr(_sm, 'marker', None)
							if _hebw is not None and getattr(_hebw, 'vMarkersManager', None) and _mk not in (None, -1):
								_hebw.vMarkersManager.onVehicleHealthChanged(_mk, max(0, _sm.health), attacker_id, 0)
								_hebw.vMarkersManager.showVehicleDamageInfo(_mk, _sd, 0, 0, 1)
						except Exception:
							pass
					if _sm.health <= 0:
						_sm.health = 0
						try:
							_pl.arena.onVehicleKilled(_sm.id, attacker_id, 0)
						except Exception:
							pass
				if _hit_any:
					LOG_DEBUG('HE BURST: %d vehicle(s) caught in a %.1f m blast' % (_hit_any, _R))

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
				from gui.mods.offhangar import device_damage as _device_damage
				_critical_applied = False
				try:
					target_mock._offh_last_strike_critical = False
				except Exception:
					pass
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
									_critical_applied = True
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
						_previous_device_hp = current_hp
						current_hp -= _shell_dmg
						# Clamp at 0 so auto-repair does not have to climb out of a deficit.
						if current_hp < 0:
							current_hp = 0
						target_mock.devices_hp[_name] = current_hp
						if current_hp < _previous_device_hp:
							_critical_applied = True
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
							if is_player_target and not getattr(target_mock, '_is_killed', False) and (getattr(target_mock, 'health', 0) or 0) > 0:
								try:
									import gui.WindowsManager as _WMrb
									_bwrb = getattr(_WMrb.g_windowsManager, 'battleWindow', None)
									if _bwrb is not None and hasattr(_bwrb, 'damagePanel'):
										from gui.mods.offhangar import device_damage as _DDrb
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
										if _eng is not None and hasattr(_eng, 'get'):
											_fsc = float(_eng.get('fireStartingChance', 0.15))
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
								if _eng2 is not None and hasattr(_eng2, 'get'):
									_fsc2 = float(_eng2.get('fireStartingChance', 0.15))
							except Exception:
								pass
							if random.random() < _fsc2:
								_offh_ignite(target_mock, is_player_target, _name + ' holed')
					if _blocked:
						LOG_DEBUG('CRIT GATE: %d device hit(s) behind the stopping plate ignored (no penetration)' % _blocked)
				finally:
					if _critical_applied:
						try:
							target_mock._offh_last_strike_critical = True
							_offh_drop_capture_for_vehicle(
								target_mock, attacker_id, 'module or crew damage')
						except Exception:
							pass
					if _own_burst:
						_pending_voice = _OFFH_VOICE_BURST[0] or []
						_OFFH_VOICE_BURST[0] = None
						if len(_pending_voice) > 1:
							LOG_DEBUG('CREWVOICE burst: %d reports from one strike, announcing the worst of %s'
								% (len(_pending_voice), _pending_voice))
						if _pending_voice:
							_offh_play_crit_voice(_offh_voice_burst_pick(_pending_voice))
				return dmg
			def _resolve_bot_projectile_hit(_attacker, _attacker_id,
					hit_veh, hit_col, _impact_point, _damage_start_p,
					_damage_end_p, _damage_dir, _penetration_distance,
					_shot, _fire_seq):
				import BigWorld, Math, math, random
				# Projectile arrival is asynchronous: the bot loop that launched this
				# shell has already returned, so resolve the current player vehicle in
				# this callback instead of relying on a frame-local name.
				player_mock = mock_vehicles.get(
					getattr(player, 'playerVehicleID', -1))
				if (hit_veh is None or hit_col is None or
						not getattr(hit_veh, 'isAlive', False) or
						(getattr(hit_veh, 'health', 0) or 0) <= 0):
					return
				my_team = _attacker.publicInfo.get('team', 2) if getattr(_attacker, 'publicInfo', None) is not None else 2
				player_team = getattr(player, '_offhangar_team', 1)
				if hit_veh == player_mock and getattr(player_mock, 'health', 0) > 0 and my_team != player_team:
					_dist, _hitAngleCos, _armor = hit_col[:3]
					# shared model - this path still carried the old piercingPower[0] +
					# "'HE' in shell name" test, so shots at the player never bounced either
					_pen_b, eff_armor, pierce_rng = _offh_penetration(
						_shot, float(_penetration_distance), _armor,
						_hitAngleCos)
					angle_cos = max(0.087, abs(_hitAngleCos))

					LOG_DEBUG('BOT HIT PLAYER! base=%.1f eff=%.1f pierce=%.1f' % (_armor, eff_armor, pierce_rng))

					auto_bounce = (_pen_b == 0)

					# Visible impact effect on the player's tank (sparks/bounce/ricochet)
					try:
						_hit_res = _pen_b
						_wpos = _impact_point
						_play_vehicle_hit_effect(
							_shot['shell'], _wpos, _damage_dir,
							_hit_res, is_player_target=True)
						# Persistent shell-hole decal on the player's tank
						_p_td = loaded_models.get('td')
						_cn = _comp_name_from_hits(_p_td, hit_col[3] if len(hit_col) > 3 else [])
						_add_impact_decal(
							_target_sticker_map(player_mock, _cn), _cn,
							_wpos, _damage_dir, _hit_res)
					except Exception:
						pass

					dmg = 0
					# DIRECTION AND FLASH FOR ALL HITS
					try:
						px = player_mock.position
						import math
						import BigWorld

						# Left/Right is now CORRECT, but Front/Back is inverted.
						# Keep X inverted, and INVERT Z as well.
						dx = -(_attacker.position[0] - px[0])
						dz = -(_attacker.position[2] - px[2])
						hitDirYaw = math.atan2(dx, dz)

						if hasattr(player, 'inputHandler') and player.inputHandler:
							_aim = getattr(player.inputHandler, 'aim', None)
							if _aim and hasattr(_aim, 'showHit'):
								# shell['kind'], never the NAME: every HEAT shell contains the letters 'HE'
								# too, so the old substring test let a bot's failed HEAT round count as a hit.
								isDamage = not auto_bounce and (pierce_rng >= eff_armor or _offh_is_he(_shot))
								_aim.showHit(hitDirYaw, isDamage)

						if isDamage:
							fba = Math.Vector4Animation()
							fba.keyframes = [(0.0, Math.Vector4(1.0, 0.0, 0.0, 0.7)), (0.3, Math.Vector4(1.0, 0.0, 0.0, 0.7)), (1.5, Math.Vector4(1.0, 0.0, 0.0, 0.0))]
							fba.duration = 1.5
							BigWorld.flashBangAnimation(fba)
							def remove_fba(f=fba):
								try: BigWorld.removeFlashBangAnimation(f)
								except: pass
							_offh_battle_callback(1.4, remove_fba)
					except Exception as e:
						LOG_DEBUG('HitDir calc err:', e)

					_he_bp = _offh_is_he(_shot)
					_pen_bp = (not auto_bounce) and pierce_rng >= eff_armor
					_player_hp_before = max(0, int(getattr(player_mock, 'health', 0) or 0))
					if auto_bounce or not (_pen_bp or _he_bp):
						LOG_DEBUG('BOT RICOCHET!')
						try:
							_offh_hit_sound('/hits/hits/tank_hit_armor_ricochet')
						except Exception as ex:
							LOG_DEBUG('Ricochet FM err:', ex)
						try:
							if hasattr(player.inputHandler, 'ctrl') and player.inputHandler.ctrl:
								cam = getattr(player.inputHandler.ctrl, 'camera', None)
								_dir = Math.Vector3(dx, 0, dz)
								_dir.normalise()
								if cam and hasattr(cam, 'applyImpulse'):
									cam.applyImpulse(_dir, 0.5)
								elif cam and hasattr(cam, 'impulseOscillator') and cam.impulseOscillator:
									cam.impulseOscillator.applyImpulse(_dir * 0.5)
						except: pass
					else:
						_dmg_base = _shot['shell']['damage'][0]
						dmg = _dmg_base * random.uniform(0.75, 1.25)
						_he_thru_bp = _he_bp and not _pen_bp
						if _he_thru_bp:
							# Burst on the plate: half the nominal, minus 1.1x its nominal thickness.
							dmg = _offh_he_damage(dmg, _offh_he_nominal_armor(hit_col[3], getattr(player_mock, 'typeDescriptor', None)), 0.0)
							LOG_DEBUG('BOT HE NO PENETRATION -> %d damage' % dmg)
						try:
							# Blast also reaches whoever else is standing around the player.
							if _he_bp:
								_offh_he_splash(
									_impact_point, _shot, _attacker.id,
									getattr(player, 'playerVehicleID', -1))
						except Exception as _hsp:
							LOG_DEBUG('HE splash err (bot->player):', str(_hsp))
						try:
							# start_p/end_p, not the two tank positions: hit_col's distances
							# are measured along THAT segment, and the interior zone needs
							# the real entry point.
							dmg = _apply_module_damage(
								player_mock, hit_col[3], _damage_start_p,
								_damage_end_p, dmg, _shot['shell'], _attacker.id,
								(not _he_thru_bp), _he_thru_bp)
						except Exception as ex:
							import traceback
							LOG_DEBUG("PLAYER MODULE DAMAGE ERROR:", traceback.format_exc() if 'traceback' in globals() else str(ex))
						# Module test bench: the crits above already happened, the
					# hull damage is what would end the run.
					if _offh_module_test_mode():
						if int(dmg) > 0:
							LOG_DEBUG('MODULE TEST: bot shell dealt %d hull damage, suppressed' % int(dmg))
					else:
						# LAN HP is server-owned.  The authority renders this impact
						# immediately, then waits for the canonical event instead of
						# privately subtracting HP that other clients cannot observe.
						_network_damage_deferred = False
						try:
							from gui.mods.offhangar.network_battle import send_authoritative_bot_human_hit
							_hit_world = _impact_point
							_network_damage_deferred = send_authoritative_bot_human_hit(
								player, getattr(_attacker, '_network_bot_id', None),
								getattr(player, '_offhangar_network_id', None),
								_fire_seq,
								int(dmg), 2, _hit_world,
								bool(getattr(
									player_mock, '_offh_last_strike_critical', False)))
							if _network_damage_deferred:
								LOG_DEBUG('LAN bot-human hit reported: bot=%s target=%s damage=%s' % (
									getattr(_attacker, '_network_bot_id', None),
									getattr(player, '_offhangar_network_id', None), int(dmg)))
						except Exception:
							LOG_CURRENT_EXCEPTION()
						if not _network_damage_deferred:
							try:
								from gui.mods.offhangar import battle_feedback as _offh_feedback_received
								_offh_feedback_received.record_incoming_hit(
									_offh_stats_for(player), min(_player_hp_before, max(0, int(dmg or 0))))
							except Exception:
								pass
							_player_hp_before_apply = max(0, int(player_mock.health or 0))
							player_mock.health = max(0, player_mock.health - int(dmg))
							if player_mock.health < _player_hp_before_apply:
								_offh_drop_capture_for_vehicle(
									player_mock, _attacker.id, 'shell damage')
							if hasattr(player, 'vehicle') and player.vehicle:
								player.vehicle.health = player_mock.health
							try:
								import gui.WindowsManager
								bw = gui.WindowsManager.g_windowsManager.battleWindow
								if hasattr(bw, 'damagePanel'):
									bw.damagePanel.updateHealth(player_mock.health)
							except Exception:
								pass
						try:
							_offh_hit_sound('/hits/hits/tank_hit_armor_crit')
						except Exception as ex:
							LOG_DEBUG('Pierce FM err:', ex)
						try:
							if hasattr(player.inputHandler, 'ctrl') and player.inputHandler.ctrl:
								cam = getattr(player.inputHandler.ctrl, 'camera', None)
								_dir = Math.Vector3(dx, 0, dz)
								_dir.normalise()
								if cam and hasattr(cam, 'applyImpulse'):
									cam.applyImpulse(_dir, 1.0)
								elif cam and hasattr(cam, 'impulseOscillator') and cam.impulseOscillator:
									cam.impulseOscillator.applyImpulse(_dir * 1.0)
						except: pass
				else:
					my_team = _attacker.publicInfo.get('team', 2) if getattr(_attacker, 'publicInfo', None) is not None else 2
					target_team = hit_veh.publicInfo.get('team', 2) if getattr(hit_veh, 'publicInfo', None) is not None else (getattr(player, '_offhangar_team', 1) if getattr(player, 'playerVehicleID', -1) == hit_veh.id else 2)
					if getattr(hit_veh, 'health', 0) > 0 and my_team != target_team:
						# ARMOR PENETRATION LOGIC FOR BOT vs BOT
						_dmg_base = _shot['shell']['damage'][0]
						_dist, _hitAngleCos, _armor = hit_col[:3]
						_pen_res, eff_armor, pierce_rng = _offh_penetration(
							_shot, float(_penetration_distance), _armor,
							_hitAngleCos)
						auto_bounce = (_pen_res == 0)

						is_damage = (_pen_res == 2)
						# HE that failed to get through is not a miss - it bursts on the plate. Force
						# the damage branch and let the blast formula decide how much survives.
						_he_bb = _offh_is_he(_shot)
						_he_thru_bb = _he_bb and not is_damage
						if _he_thru_bb:
							is_damage = True

						# Visible impact effect + shell-hole decal on the hit bot
						try:
							_hit_res = 0 if auto_bounce else (2 if is_damage else 1)
							_wpos = _impact_point
							_play_vehicle_hit_effect(
								_shot['shell'], _wpos, _damage_dir,
								_hit_res, target_mock=hit_veh)
							_cn = _comp_name_from_hits(getattr(hit_veh, 'typeDescriptor', None), hit_col[3] if len(hit_col) > 3 else [])
							_add_impact_decal(
								_target_sticker_map(hit_veh, _cn), _cn,
								_wpos, _damage_dir, _hit_res)
						except Exception:
							pass

						if is_damage:
							LOG_DEBUG('BOT HIT ENEMY BOT: %s' % ('HE BURST' if _he_thru_bb else 'PENETRATION!'))
							_dmg = int(_dmg_base * random.uniform(0.75, 1.25))
							if _he_thru_bb:
								_dmg = _offh_he_damage(_dmg, _offh_he_nominal_armor(hit_col[3], getattr(hit_veh, 'typeDescriptor', None)), 0.0)
							if getattr(hit_veh, '_network_remote', False):
								try:
									from gui.mods.offhangar.network_battle import send_authoritative_bot_human_hit
									_hit_world = _impact_point
									send_authoritative_bot_human_hit(player,
										getattr(_attacker, '_network_bot_id', None),
										getattr(hit_veh, '_network_server_id', None),
										_fire_seq,
										_dmg, 2, _hit_world)
									LOG_DEBUG('LAN bot-human hit reported: bot=%s target=%s damage=%s' % (
										getattr(_attacker, '_network_bot_id', None),
										getattr(hit_veh, '_network_server_id', None), _dmg))
								except Exception:
									LOG_CURRENT_EXCEPTION()
								return
							try:
								if _he_bb:
									_offh_he_splash(
										_impact_point, _shot, _attacker.id,
										getattr(hit_veh, 'id', -1))
							except Exception as _hsb:
								LOG_DEBUG('HE splash err (bot->bot):', str(_hsb))
							try:
								_dmg = int(_apply_module_damage(
									hit_veh, hit_col[3], _damage_start_p,
									_damage_end_p, _dmg, _shot['shell'], _attacker.id,
									(not _he_thru_bb), _he_thru_bb))
							except Exception as ex:
								import traceback
								LOG_DEBUG("BOT MODULE DAMAGE ERROR:", traceback.format_exc() if 'traceback' in globals() else str(ex))
							_bot_hp_before = max(0, int(getattr(hit_veh, 'health', 0) or 0))
							hit_veh.health -= _dmg
							_bot_actual_damage = min(_bot_hp_before, max(0, int(_dmg or 0)))
							if _bot_actual_damage > 0:
								_offh_drop_capture_for_vehicle(
									hit_veh, _attacker.id, 'shell damage')
							hit_veh.damage_from_bots = (getattr(hit_veh, 'damage_from_bots', 0) or 0) + _dmg
							hit_veh.last_killer_id = _attacker.id
							try:
								if (_offh_is_ally(_attacker) and not _offh_is_ally(hit_veh)):
									_offh_record_spot_assist(player, hit_veh,
										_bot_actual_damage, hit_veh.health <= 0)
							except Exception:
								pass
							try:
								player.arena.onVehicleStatisticsUpdate(hit_veh.id)
								from gui import WindowsManager
								bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
								if bw and hasattr(bw, 'vMarkersManager'):
									marker = getattr(hit_veh, 'marker', None)
									if marker is not None:
										bw.vMarkersManager.onVehicleHealthChanged(marker, max(0, hit_veh.health), _attacker.id, 0)
										try:
											bw.vMarkersManager.showVehicleDamageInfo(marker, _dmg, 0, 0, 0)
										except:
											pass
									try: bw.minimap.notifyVehicleStop(hit_veh.id) if hit_veh.health <= 0 else None
									except: pass
							except: pass
						else:
							LOG_DEBUG('BOT HIT ENEMY BOT: RICOCHET/NON-PEN!')
						if hit_veh.health <= 0:
							_offh_set_alive(hit_veh, False)
							try:
								from gui import WindowsManager
								bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
								if bw and hasattr(bw, '_Battle__arena'):
									bw._Battle__arena.vehicles[hit_veh.id]['isAlive'] = False
									bw._Battle__updatePlayers()
							except: pass
							LOG_DEBUG('BOT KILLED ENEMY BOT!')
							# Wreck ownership is centralized in _KillEventWrapper below.  Per-weapon
							# swaps used to race the native Servo/filter release and could replace a
							# chassis which the rigid-body graph still owned.
							# The canonical event owns native release/wreck scheduling. Fire it before
							# optional statistics or GUI work so a broken panel cannot strand a live
							# rigid body after the bot is already dead.
							try:
								player.arena.onVehicleKilled(hit_veh.id, _attacker_id, 0)
							except Exception:
								pass
							pass  # kill feed is posted centrally in _KillEventWrapper
						else:
							LOG_DEBUG(
								'BOT SHELL HIT NON-ENEMY at travelled distance %.1f' %
								float(_penetration_distance))

			def _resolve_player_projectile_hit(enemy_mock, enemy_hit_info,
					_impact_point, _damage_start_pos, _damage_end_pos,
					_damage_dir_vec, _penetration_distance, _shot, _sidx,
					_network_shot_seq):
				import BigWorld, Math, random
				# The projectile may arrive after another shell has already killed it.
				if (enemy_mock is None or enemy_hit_info is None or
						not getattr(enemy_mock, 'isAlive', False) or
						(getattr(enemy_mock, 'health', 0) or 0) <= 0):
					return
				# Calculate real damage from gun.shots[i].shell descriptor
				# Pre-bind: _apply_module_damage below runs OUTSIDE this try and reads both. On
				# the fallback path (shell has no 'damage' key, or the try dies early) they stayed
				# unbound -> UnboundLocalError, silently swallowed, and module crits (tracks/engine/
				# crew) never applied - it only logged 'MODULE DAMAGE ERROR'.
				all_hits = []
				_shell = None
				_hit_res = 2   # pre-bound: the miss/bounce sound branch reads it
				_he_snd_override = None
				try:
					_td = loaded_models.get('td')
					_gun = _td.gun
					_shots = _gun.get('shots', [])
					_sidx = max(0, min(int(_sidx), len(_shots) - 1)) if _shots else 0
					_shot = _shots[_sidx] if _shots else _shot
					_shell = _shot.get('shell') if _shot else None

					dmg = 0
					# Bound before the branches below so the module call can never hit an
					# UnboundLocalError and skip every crit. None = verdict unknown.
					_offh_penetrated = None
					if _shell and 'damage' in _shell:
						_dmg_data = _shell['damage']
						if hasattr(_dmg_data, '__len__') and len(_dmg_data) >= 1: avg = float(_dmg_data[0])
						else: avg = float(_dmg_data)
						dmg = int(random.uniform(avg * 0.75, avg * 1.25))

						# ARMOR PENETRATION LOGIC (Real HitBox) - shared model, see _offh_penetration
						_dist, _hitAngleCos, _armor = enemy_hit_info[:3]
						all_hits = enemy_hit_info[3] if len(enemy_hit_info) > 3 else []
						# Resolve against the first STRUCTURAL plate, not the nearest hit. The nearest
						# hit is often a track (vehicleDamageFactor 0), and testing the round against
						# the track and then subtracting full hull damage is what made tracks deal
						# structure damage. Spaced plates only cost penetration; HEAT dies on them.
						_spaced_mm = 0.0
						_res_hull = _offh_resolve_hull_hit(
							_shots[_sidx], float(_penetration_distance), all_hits)
						if _res_hull is None:
							# never reached structure - the track swallowed it
							_pen_res, eff_armor, pierce_rng = 1, 0.0, 0.0
							_hitAngleCos_s = _hitAngleCos
							LOG_DEBUG('TRACK ABSORBED the shell - no hull damage')
						else:
							_pen_res, eff_armor, pierce_rng, _spaced_mm, _hitAngleCos_s = _res_hull
							if _spaced_mm > 0.0:
								LOG_DEBUG('SPACED ARMOUR: %.0f mm eaten before the hull plate' % _spaced_mm)
						angle_cos = max(0.087, abs(_hitAngleCos_s))

						LOG_DEBUG('REAL ARMOR: base=%.1f eff=%.1f pierce=%.1f angle_cos=%.2f' % (_armor, eff_armor, pierce_rng, angle_cos))

						auto_bounce = (_pen_res == 0)
						# Gate for the module roll below. This is the ONLY call site that runs on a
						# bounce as well (so track hits still register), so it is the only one that
						# has to tell _apply_module_damage what actually happened.
						_offh_penetrated = (_pen_res == 2)

						_hit_res = 2  # penetration by default (pre-bound: the sound below reads it)
						_he_shot = _offh_is_he(_shots[_sidx])
						if auto_bounce:
							dmg = 0
							_hit_res = 0  # ricochet
							LOG_DEBUG('REAL RICOCHET (Auto-Bounce >70 deg)!')
						elif _pen_res == 1:
							dmg = 0
							_hit_res = 1  # non-penetration
							LOG_DEBUG('REAL RICOCHET / NON-PENETRATION!')
						if _he_shot and _pen_res != 2:
							# A high-explosive round that does not get through is not a zero. It
							# detonates on the plate and pushes what is left through it: half the
							# nominal, minus 1.1x the plate's NOMINAL thickness. Against heavy armour
							# that lands on 0 by itself, which is why a derp gun wants thin plate.
							_he_nom = _offh_he_nominal_armor(all_hits, getattr(enemy_mock, 'typeDescriptor', None))
							dmg = _offh_he_damage(dmg if dmg > 0 else int(avg), _he_nom, 0.0)
							_hit_res = 2 if dmg > 0 else _hit_res
							# Blast through armour it did not pierce is not a penetration, and 0.8.2
							# has its own crew line for it.
							if dmg > 0:
								_he_snd_override = 'damage_by_near_explosion_by_player'
							LOG_DEBUG('HE NO PENETRATION: armor=%.0f -> %d damage' % (_he_nom, dmg))
						# Visible impact effect + shell-hole decal on the target -
						# the ProjectileMover only shows impacts on static geometry,
						# never on the mock tanks.
						try:
							_wpos = _impact_point
							_play_vehicle_hit_effect(
								_shell, _wpos, _damage_dir_vec, _hit_res,
								target_mock=enemy_mock)
							_cn = _comp_name_from_hits(getattr(enemy_mock, 'typeDescriptor', None), enemy_hit_info[3] if len(enemy_hit_info) > 3 else [])
							_add_impact_decal(
								_target_sticker_map(enemy_mock, _cn), _cn,
								_wpos, _damage_dir_vec, _hit_res)
						except Exception:
							pass
					else:
						# No invented damage. Every 0.8.2 shell has damage=(armor, devices); if we
						# land here the descriptor lookup itself is broken, and rolling 250-450
						# would just paper over it with a number the module system then trusts.
						dmg = 0
						_offh_penetrated = None
						LOG_DEBUG('SHELL DESCRIPTOR HAS NO damage FIELD - no damage dealt')
				except Exception as e:
					import traceback
					LOG_DEBUG('Damage calc error:', traceback.format_exc())
					# Deal nothing rather than a random number: a silent 250-450 hid the real
					# fault and fed the module/crew system fabricated input.
					dmg = 0
					_offh_penetrated = None

				# LAN human tanks are server-authoritative. The local collision pass above
				# supplies the exact map collision, armor result and impact point. Report that
				# verdict to the server, which owns shared HP and relays it to every client.
				if getattr(enemy_mock, '_network_remote', False):
					try:
						from gui.mods.offhangar.network_battle import send_local_hit
						_network_hit_pos = _impact_point
						send_local_hit(player, getattr(enemy_mock, '_network_server_id', None),
							_network_shot_seq, max(0, int(dmg or 0)), _hit_res,
							_sidx, _network_hit_pos)
						LOG_DEBUG('LAN human hit reported: target=%s seq=%s damage=%s result=%s' % (
							getattr(enemy_mock, '_network_server_id', None), _network_shot_seq,
							max(0, int(dmg or 0)), _hit_res))
					except Exception:
						LOG_CURRENT_EXCEPTION()
					return
				if getattr(enemy_mock, '_network_shared_bot', False):
					try:
						from gui.mods.offhangar.network_battle import send_local_bot_hit
						_network_hit_pos = _impact_point
						send_local_bot_hit(player, getattr(enemy_mock, '_network_bot_id', None),
							_network_shot_seq, max(0, int(dmg or 0)), _hit_res,
							_network_hit_pos)
						LOG_DEBUG('LAN bot hit reported: target=%s seq=%s damage=%s result=%s' % (
							getattr(enemy_mock, '_network_bot_id', None), _network_shot_seq,
							max(0, int(dmg or 0)), _hit_res))
					except Exception:
						LOG_CURRENT_EXCEPTION()
					return

				# Modules and crew take their hits on ANY strike that reached the tank, not
				# only when hull damage resulted. Since tracks became spaced armour a pure
				# track hit deals 0 structure damage, so gating this on dmg > 0 meant a track
				# could never be broken at all.
				# HE also reaches whatever else is standing near the impact. The tank
				# actually struck is excluded - it is handled right above as the dist 0 case.
				try:
					if _offh_is_he(_shots[_sidx]):
							_offh_he_splash(_impact_point, _shots[_sidx],
								getattr(player, 'playerVehicleID', -1), getattr(enemy_mock, 'id', -1))
				except Exception as _hse:
					LOG_DEBUG('HE splash err:', str(_hse))
				try:
						dmg = _apply_module_damage(
							enemy_mock, all_hits, _damage_start_pos,
							_damage_end_pos, dmg, _shell,
							getattr(player, 'playerVehicleID', -1),
							_offh_penetrated)
					# (the hit line is chosen below, from last_sound or the HE override)
				except Exception as ex:
					import traceback
					LOG_DEBUG("MODULE DAMAGE ERROR:", traceback.format_exc())
				try:
					if not _offh_is_ally(enemy_mock):
						from gui.mods.offhangar import battle_feedback as _offh_feedback_hit
						_tracked_damage = min(
							max(0, int(dmg or 0)), max(0, int(enemy_mock.health or 0)))
						_offh_feedback_hit.record_outgoing_hit(
							_offh_stats_for(player), enemy_mock.id, _tracked_damage,
							_hit_res, _tracked_damage >= int(enemy_mock.health or 0),
							True, bool(_shell is not None and _offh_is_he(_shots[_sidx])))
				except Exception as _stats_error:
					LOG_DEBUG('Local hit statistics failed:', str(_stats_error))
				if dmg > 0:

					actual_dmg = min(dmg, max(0, enemy_mock.health))
					enemy_mock.health -= dmg
					if actual_dmg > 0:
						_offh_drop_capture_for_vehicle(
							enemy_mock, getattr(player, 'playerVehicleID', -1),
							'shell damage')
					enemy_mock.damage_from_player = (getattr(enemy_mock, 'damage_from_player', 0) or 0) + actual_dmg
					enemy_mock.hits_from_player = (getattr(enemy_mock, 'hits_from_player', 0) or 0) + 1
					LOG_DEBUG('HIT! Damage:', dmg, 'Enemy HP:', enemy_mock.health)

					try:
						_is_ally = _offh_is_ally(enemy_mock)
						if enemy_mock.health <= 0:
							sound_str = 'ally_killed_by_player' if _is_ally else 'enemy_killed_by_player'
						elif _is_ally:
							# 0.8.2 ships no ally HIT line - only ally_killed. Announcing a penetration on
							# a team-mate with the ENEMY line was simply wrong, so say nothing at all.
							sound_str = None
						else:
							sound_str = _he_snd_override or getattr(enemy_mock, 'last_sound', 'armor_pierced_by_player')
						if sound_str and hasattr(player, 'soundNotifications') and player.soundNotifications is not None:
							player.soundNotifications.play(sound_str)
						elif sound_str:
							# elif, not else. sound_str is deliberately None for a hit on a team-mate
							# (0.8.2 ships no ally-hit line), and this branch then played None -
							# IngameSoundNotifications answered with "Couldn't find None event" in the
							# log. The first condition failed for the missing sound, not for a missing
							# notifications object, so the fallback fired on the wrong reason.
							if not hasattr(g_offline_aih, '_snd_notif'):
								try:
									from gui.IngameSoundNotifications import IngameSoundNotifications
									g_offline_aih._snd_notif = IngameSoundNotifications()
									g_offline_aih._snd_notif.start()
								except: pass
							if hasattr(g_offline_aih, '_snd_notif'):
								g_offline_aih._snd_notif.play(sound_str)
					except Exception as e:
						LOG_DEBUG('Hit sound error:', str(e))
				else:
					# dmg == 0 -> bounced or failed to pierce. Retail still reports both.
					try:
						_nz = 'armor_ricochet_by_player' if _hit_res == 0 else 'armor_not_pierced_by_player'
						if _offh_is_ally(enemy_mock):
							_nz = None   # no ally bounce / no-pen line exists either
						_sn_h = getattr(player, 'soundNotifications', None)
						if _sn_h is None:
							# lazily build it exactly like the penetration branch does, otherwise the
							# first shot of a battle stays silent whenever it bounces
							if not hasattr(g_offline_aih, '_snd_notif'):
								try:
									from gui.IngameSoundNotifications import IngameSoundNotifications
									g_offline_aih._snd_notif = IngameSoundNotifications()
									g_offline_aih._snd_notif.start()
								except Exception: pass
							_sn_h = getattr(g_offline_aih, '_snd_notif', None)
						if _sn_h is not None and _nz:
							_sn_h.play(_nz, getattr(enemy_mock, 'id', None))
					except Exception as _nze:
						LOG_DEBUG('No-pen sound error:', str(_nze))

				# Update vehicle marker health
				try:
					hp_percent = max(0, int((float(enemy_mock.health) / float(enemy_mock.maxHealth)) * 100.0))
					player.arena.onVehicleStatisticsUpdate(enemy_mock.id)
					from gui import WindowsManager
					bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
					if bw and hasattr(bw, 'vMarkersManager'):
						marker = getattr(enemy_mock, 'marker', None)
						if marker is not None:
							bw.vMarkersManager.onVehicleHealthChanged(marker, max(0, enemy_mock.health), getattr(player, 'playerVehicleID', -1), 0)
							try:
								bw.vMarkersManager.showVehicleDamageInfo(marker, dmg, 0, 0, 1)
							except:
								pass
							LOG_DEBUG('HP updated via marker, HP=%d' % enemy_mock.health)
						else:
							LOG_DEBUG('No marker on enemy_mock!')
					if bw and hasattr(bw, 'minimap'):
						try: bw.minimap.notifyVehicleStop(enemy_mock.id) if enemy_mock.health <= 0 else None
						except: pass
					try:
						player.showVehicleDamageInfo(enemy_mock.id, 0, 0, dmg)
					except:
						pass
				except Exception as e:
					LOG_DEBUG('Hit GUI error:', str(e))

				if enemy_mock.health <= 0:
					_offh_set_alive(enemy_mock, False)
					try:
						from gui import WindowsManager
						bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
						if bw and hasattr(bw, '_Battle__arena'):
							bw._Battle__arena.vehicles[enemy_mock.id]['isAlive'] = False
							bw._Battle__updatePlayers()
					except: pass
					LOG_DEBUG('ENEMY DESTROYED!')
					try:
						p_id = getattr(player, 'playerVehicleID', -1)
						if p_id != -1 and p_id in player.arena.vehicles and hasattr(player.arena, 'onVehicleKilled'):
							player.arena.onVehicleKilled(enemy_mock.id, p_id, 0)
					except Exception as _e:
						LOG_DEBUG('Player kill event error:', _e)

					# The central kill wrapper owns the only native-release/wreck transaction.


			def _mock_shoot():
				import BigWorld, Math, math, random
				_network_shot_seq = None
				if getattr(BigWorld.player(), '_is_dead', False) is True: return
				# Submerged crew cannot work the gun.
				if getattr(BigWorld.player(), '_offh_drowning', False): return
				# A destroyed gun cannot fire at all. The flag was computed but never read.
				try:
					_pm_gun = mock_vehicles.get(getattr(BigWorld.player(), 'playerVehicleID', -1))
					if _pm_gun is not None and getattr(_pm_gun, 'is_gun_destroyed', False): return
				except Exception: pass
				# No shooting during the pre-battle countdown (like the original)
				try:
					if getattr(BigWorld.player().arena, 'period', 3) != 3: return  # prebattle AND afterbattle (capture won)
				except Exception:
					pass
				try:
					# --- RELOAD LOGIC ---
					if not _gun_state['initialized']: return
					if _gun_state['reloadTime'] > 0: return
					idx = _gun_state.get('shot_index', 0)
					ammo_key = 'ammo_%d' % idx
					if _gun_state.get(ammo_key, 1) <= 0: return

					_gun_state[ammo_key] -= 1
					_gun_state['clip'] -= 1
					import math
					jump = _gun_state['base_dispersion'] * _gun_state['after_shot']
					_gun_state['dispersion'] = math.sqrt(_gun_state['dispersion']**2 + jump**2)
					max_disp = _gun_state['base_dispersion'] * 15.0
					if _gun_state['dispersion'] > max_disp:
						_gun_state['dispersion'] = max_disp

					if _gun_state['clip'] > 0:
						_gun_state['reloadTime'] = _gun_state['clip_reload']
					else:
						# A knocked-out loader drags the reload out; a knocked-out commander adds a
						# smaller malus on top (device_damage.crew_stat_factor).
						try:
							_pm_cr = mock_vehicles.get(getattr(player, 'playerVehicleID', -1))
							# A damaged ammo bay drags it out on top of that (destroyed detonates).
							_gun_state['reloadTime'] = _gun_state['reload'] * (_crew_factor(_pm_cr, 'reload') * _module_factor(_pm_cr, 'reload') if _pm_cr is not None else 1.0)
						except Exception:
							_gun_state['reloadTime'] = _gun_state['reload']

					if hasattr(BigWorld.player(), 'gunRotator'):
						BigWorld.player().gunRotator.dispersionAngle = _gun_state['dispersion']

					player = BigWorld.player()
					try:
						_player_mock._offh_spot_last_shot = float(BigWorld.time())
					except Exception:
						pass
					player._offhangar_shots_fired = getattr(player, '_offhangar_shots_fired', 0) + 1
					globals()['G_OFFHANGAR_SHOTS_FIRED'] = int(
						globals().get('G_OFFHANGAR_SHOTS_FIRED', 0) or 0) + 1
					try:
						from gui.mods.offhangar import battle_feedback as _offh_feedback_shot
						_offh_feedback_shot.record_shot(_offh_stats_for(player))
					except Exception:
						pass
					try:
						from gui.mods.offhangar._constants import CONFIG_OPTIONS as _NET_FIRE_CFG
						if bool(_NET_FIRE_CFG.get('network_mode', False)) and not getattr(player, '_offhangar_network_fallback_local', False):
							from gui.mods.offhangar.network_battle import send_local_fire
							_network_shot_seq = send_local_fire(player, idx,
								veh_yaw[0] + turret_yaw[0], gun_pitch[0],
								veh_pos[0], veh_pos[1], veh_pos[2], veh_yaw[0])
					except Exception:
						pass

					# UPDATE RELOAD UI
					try:
						from gui import WindowsManager
						panel = WindowsManager.g_windowsManager.battleWindow.consumablesPanel
						if panel:
							shot_idx = _gun_state.get('shot_index', 0)
							panel.setShellQuantityInSlot(shot_idx, _gun_state['ammo_%d' % shot_idx], _gun_state['clip'])
							try: panel.setCoolDownTime(shot_idx, 0.0)
							except Exception as e: LOG_DEBUG('setCoolDownTime reset error:', str(e))
							try: panel.setCoolDownTime(shot_idx, _gun_state['reloadTime'])
							except Exception as e: LOG_DEBUG('setCoolDownTime error:', str(e))
						aim = getattr(g_offline_aih, 'aim', None)
						if aim:
							try: aim.setReloading(0.0, None)
							except: pass
							try: aim.setReloading(_gun_state['reloadTime'], None)
							except Exception as e: LOG_DEBUG('setReloading error:', str(e))
							shot_idx = _gun_state.get('shot_index', 0)
							aim.setAmmoStock(_gun_state['ammo_%d' % shot_idx], _gun_state['clip'], False)
					except Exception as e:
						LOG_DEBUG('Normal shoot UI error:', str(e))

					# Auto-load the next stocked shell type when this one just ran out
					# (was: the gun 'reloaded' an empty shell and refused to fire while
					# other types were still in the rack). Deferred one frame because the
					# in-flight shot below re-reads shot_index for its ballistics.
					if _gun_state.get(ammo_key, 0) <= 0:
						def _offh_auto_next_shell():
							try:
								if _gun_state.get('shot_index', 0) != idx: return  # user switched already
								if _gun_state.get(ammo_key, 0) > 0: return
								_next = None
								for _off in range(1, 10):
									_i = (idx + _off) % 10
									if _i != idx and _gun_state.get('ammo_%d' % _i, 0) > 0:
										_next = _i
										break
								if _next is None: return  # completely dry
								_gun_state['shot_index'] = _next
								_gun_state['clip'] = min(_gun_state['clip_size'], _gun_state['ammo_%d' % _next])
								if _gun_state['reloadTime'] < _gun_state['reload']:
									_gun_state['reloadTime'] = _gun_state['reload']  # type change = full reload
								try:
									from gui import WindowsManager
									_bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
									_pnl = getattr(_bw, 'consumablesPanel', None) if _bw else None
									if _pnl:
										_pnl.setCurrentShell(_next)
										_pnl.setShellQuantityInSlot(_next, _gun_state['ammo_%d' % _next], _gun_state['clip'])
										try: _pnl.setCoolDownTime(_next, 0.0)
										except Exception: pass
										try: _pnl.setCoolDownTime(_next, _gun_state['reloadTime'])
										except Exception: pass
									_aim = getattr(g_offline_aih, 'aim', None)
									if _aim:
										try: _aim.setReloading(0.0, None)
										except: pass
										try: _aim.setReloading(_gun_state['reloadTime'], None)
										except Exception: pass
										_aim.setAmmoStock(_gun_state['ammo_%d' % _next], _gun_state['clip'], False)
								except Exception as _ase:
									LOG_DEBUG('Auto shell switch UI error:', str(_ase))
							except Exception as _ase:
								LOG_DEBUG('Auto shell switch error:', str(_ase))
						_offh_battle_callback(0.0, _offh_auto_next_shell)

					try:
						player._Avatar__shotWaitingTimerID = None
					except: pass

					# --- RAYCAST HIT DETECTION ---
					start_pos, dir_vec = player.gunRotator._VehicleGunRotator__getCurShotPosition()
					dir_vec.normalise()

					# Apply Player Dispersion based on actual aiming circle
					# (config.json "perfect_accuracy": true disables all scatter - testing aid)
					disp_angle = getattr(player.gunRotator, 'dispersionAngle', _gun_state.get('dispersion', 0.02))
					from _constants import CONFIG_OPTIONS as _CFG_ACC
					sigma = 0.0 if _CFG_ACC.get('perfect_accuracy', False) else disp_angle / 3.0
					dir_vec.x += random.gauss(0, sigma)
					dir_vec.y += random.gauss(0, sigma)
					dir_vec.z += random.gauss(0, sigma)
					dir_vec.normalise()

					# Resolve the selected shell independently of the visual tracer. Collision
					# and damage must continue to work if ProjectileMover is unavailable.
					_sid = None
					_effectsDescr = None
					_w_col = None
					_our_td = loaded_models.get('td')
					_our_shots = _our_td.gun.get('shots', []) if _our_td else []
					_si = _gun_state.get('shot_index', 0)
					_si = min(_si, len(_our_shots) - 1) if _our_shots else 0
					_shot = _our_shots[_si] if _our_shots else None
					_projectile_duration = None
					# --- TRACER ---
					try:
						if g_projectile_mover and _shot:
							from items import vehicles
							_effectsDescr = vehicles.g_cache.shotEffects[_shot['shell']['effectsIndex']]
							_gravity = _shot['gravity']
							_speed = _shot['speed']
							_vel = dir_vec.scale(_speed)
							import random
							_sid = random.randint(10000, 99999)
							_cam_pos = BigWorld.camera().position if BigWorld.camera() else start_pos
							# isOwnShoot=True picks projModelOwnShotName - the BRIGHT own-shot tracer model -
							# and enables autoscale. It also sets fireMissedTrigger, whose only consumer is
							# TriggersManager.g_manager.fireTrigger; that singleton is never set up offline,
							# so clear the flag right after. The visual is already decided at construction.
							globals()['g_offh_adding_projectile'] = True
							try:
								g_projectile_mover.add(_sid, _effectsDescr, _gravity, start_pos, _vel, start_pos, True, _cam_pos)
							finally:
								globals()['g_offh_adding_projectile'] = False
							try:
								_pj = getattr(g_projectile_mover, '_ProjectileMover__projectiles', {}).get(_sid)
								if _pj is not None:
									_pj['fireMissedTrigger'] = False
									_projectile_duration = float(_pj.get('time', 0.0) or 0.0)
							except Exception: pass
					except Exception as e:
						import traceback
						LOG_DEBUG('Tracer spawn error:', traceback.format_exc())

					# Dynamic collision is resolved while the shell is actually in flight.
					# Do not preselect a target here: an un-led shot must pass behind a
					# moving tank, while a correctly led shot meets its later pose.
					if _shot is not None:
						_fired_shot = _shot
						_fired_index = _si
						_fired_seq = _network_shot_seq
						_fired_velocity = dir_vec.scale(float(_shot['speed']))
						_fired_gravity = Math.Vector3(0.0, -float(_shot['gravity']), 0.0)
						_fallback_time = max(4.0, min(
							20.0, 2500.0 / max(1.0, float(_shot['speed'])) + 4.0))
						def _player_vehicle_impact(_target, _collision, _point,
								_segment_start, _segment_end, _direction,
								_travel_distance, _flight_time):
							_resolve_player_projectile_hit(
								_target, _collision, _point, _segment_start,
								_segment_end, _direction, _travel_distance,
								_fired_shot, _fired_index, _fired_seq)
						def _player_world_impact(_world_hit, _point, _direction,
								_travel_distance, _flight_time):
							try:
								if (g_projectile_mover is not None and _sid is not None and
										_effectsDescr is not None):
									_gmat = _terrain_hit_material(
										_offh_bspace(), _point, _direction)
									if (_gmat + 'Hit') not in _effectsDescr:
										_gmat = 'ground'
									g_projectile_mover.explode(
										_sid, _effectsDescr, _gmat, _point, _direction)
							except Exception as _ground_effect_error:
								LOG_DEBUG('Ground impact effect error:', str(_ground_effect_error))
							try:
								if _offh_is_he(_fired_shot):
									_offh_he_splash(_point, _fired_shot,
										getattr(player, 'playerVehicleID', -1), None)
							except Exception as _ground_splash_error:
								LOG_DEBUG('HE ground splash err:', str(_ground_splash_error))
						_offh_launch_live_projectile(
							_sid, start_pos, _fired_velocity, _fired_gravity,
							mock_vehicles, player.playerVehicleID,
							_player_vehicle_impact, _player_world_impact,
							_projectile_duration or _fallback_time)
					# --- GUNSHOT SOUND & EFFECTS ---
					try:
						if not hasattr(BigWorld.player(), 'soundNotifications'):
							import gui.IngameSoundNotifications as IngameSoundNotifications
							BigWorld.player().soundNotifications = IngameSoundNotifications.IngameSoundNotifications()
							BigWorld.player().soundNotifications.start()

						td = loaded_models.get('td')
						# Barrel recoil animation on the player's gun
						_trigger_gun_recoil(getattr(BigWorld.player(), '_offhangar_gun_recoil', None))
						# Hull rock-back: impulse backward along the shot direction
						try:
							_trigger_shot_impulse(getattr(BigWorld.player(), '_offhangar_swinging', None), Math.Vector3(-dir_vec.x, -dir_vec.y, -dir_vec.z), td.gun['impulse'] if td else 0.0)
						except Exception:
							pass
						_mflash_played = False
						if td is not None:
							_mflash_played = _play_muzzle_flash(BigWorld.player(), loaded_models.get('gun'), td, is_player=True)
						# The gun's effects list plays the real per-gun shot sound (like the
						# live game); the forced caliber-bucket sound doubled it with a
						# generic one. Bucket kept only as a fallback.
						if not _mflash_played:
							_fallback_gun_sound(td, loaded_models.get('chassis') or loaded_models.get('hull') or loaded_models.get('turret') or loaded_models.get('gun'))
					except Exception as e: pass

					LOG_DEBUG('OfflineBattle: PROJECTILE LAUNCHED')
				except Exception as e:
					import traceback
					LOG_DEBUG('Shoot ERROR:', traceback.format_exc())
				return


			# --- ENEMY CLONE SPAWNER (Key O) ---
			def _find_safe_spawn(want_pos):
				# Find a free, flat ground spot near want_pos:
				# not inside the player/other tanks, not against walls, not on roofs/steep slopes
				import math as _m
				import BigWorld, Math
				_pl = BigWorld.player()
				_sid = _offh_bspace()  # battle space, not empty player.spaceID (dedicated mode)

				def _ground_at(x, z, y_hint):
					# Probe ground just above the expected height (not from +1000: avoids roofs)
					try:
						c = BigWorld.wg_collideSegment(_sid, Math.Vector3(x, y_hint + 3.0, z), Math.Vector3(x, y_hint - 150.0, z), 128)
						if c is not None:
							return c[0].y
					except Exception:
						pass
					return None

				def _is_free(x, y, z):
					# 1) Keep distance: >=10 m to the player, >=8 m to other tanks/wrecks
					try:
						if (x - veh_pos[0]) ** 2 + (z - veh_pos[2]) ** 2 < 100.0:
							return False
					except Exception:
						pass
					try:
						for _sv in mock_vehicles.values():
							_svp = getattr(_sv, 'position', None)
							if _svp is not None and (x - _svp.x) ** 2 + (z - _svp.z) ** 2 < 64.0:
								return False
					except Exception:
						pass
					# 2) Clearance: 8 horizontal rays at hull height (no walls right next to us)
					for _i in range(8):
						_a = _i * _m.pi / 4.0
						try:
							if BigWorld.wg_collideSegment(_sid, Math.Vector3(x, y + 1.2, z), Math.Vector3(x + _m.sin(_a) * 3.5, y + 1.2, z + _m.cos(_a) * 3.5), 128) is not None:
								return False
						except Exception:
							pass
					# 3) No steep slope / roof edge: ground probes 2.5 m around
					for _dx, _dz in ((2.5, 0.0), (-2.5, 0.0), (0.0, 2.5), (0.0, -2.5)):
						_gy = _ground_at(x + _dx, z + _dz, y)
						if _gy is None or abs(_gy - y) > 1.5:
							return False
					return True

				# Candidates: the desired point itself, then rings (8 directions) up to 30 m
				_cands = [(want_pos.x, want_pos.z)]
				for _r in (4.0, 8.0, 13.0, 20.0, 30.0):
					for _i in range(8):
						_a = _i * _m.pi / 4.0
						_cands.append((want_pos.x + _m.sin(_a) * _r, want_pos.z + _m.cos(_a) * _r))
				for _cx, _cz in _cands:
					_gy = _ground_at(_cx, _cz, want_pos.y)
					if _gy is None:
						continue
					if _is_free(_cx, _gy, _cz):
						return Math.Vector3(_cx, _gy, _cz)
				# Fallback 1: force the desired point down to the ground (long ray)
				try:
					c = BigWorld.wg_collideSegment(_sid, Math.Vector3(want_pos.x, want_pos.y + 300.0, want_pos.z), Math.Vector3(want_pos.x, want_pos.y - 1000.0, want_pos.z), 128)
					if c is not None:
						return Math.Vector3(want_pos.x, c[0].y, want_pos.z)
				except Exception:
					pass
				# Fallback 2: 15 m in front of the player
				try:
					_fx = veh_pos[0] + _m.sin(veh_yaw[0]) * 15.0
					_fz = veh_pos[2] + _m.cos(veh_yaw[0]) * 15.0
					_gy = _ground_at(_fx, _fz, veh_pos[1])
					if _gy is not None:
						return Math.Vector3(_fx, _gy, _fz)
				except Exception:
					pass
				# Last resort: desired x/z at the PLAYER's ground height. Never return
				# want_pos unchanged - its y is the sky-high probe start (~300), exactly
				# what made bots spawn in the air and fall.
				try:
					return Math.Vector3(want_pos.x, float(veh_pos[1]), want_pos.z)
				except Exception:
					return Math.Vector3(want_pos)
			def _set_cruise_mode(mode):
				'''Apply the stock 0.8.2 cruise value and refresh its HUD arrows.'''
				try:
					mode = max(-2, min(3, int(mode)))
				except Exception:
					mode = 0
				_gun_state['cruise_mode'] = mode
				try:
					from gui import WindowsManager as _CruiseWM
					_battle = getattr(_CruiseWM.g_windowsManager, 'battleWindow', None)
					_panel = getattr(_battle, 'damagePanel', None) if _battle is not None else None
					if _panel is not None:
						_panel.setCruiseMode(mode)
				except Exception as _cruise_ui_error:
					LOG_DEBUG('Cruise panel update failed:', str(_cruise_ui_error))

			def _record_manual_movement_key(event):
				'''Keep authoritative key edges independent of a starved render poll.'''
				try:
					import CommandMapping as _MoveMapping
					_mapping = _MoveMapping.g_instance
					_key = event.key
					_down = bool(event.isKeyDown())
					_fields = (
						(_MoveMapping.CMD_MOVE_FORWARD, 'manual_forward_down'),
						(getattr(_MoveMapping, 'CMD_MOVE_FORWARD_SPEC', -1), 'manual_forward_down'),
						(_MoveMapping.CMD_MOVE_BACKWARD, 'manual_backward_down'),
						(_MoveMapping.CMD_ROTATE_LEFT, 'manual_left_down'),
						(_MoveMapping.CMD_ROTATE_RIGHT, 'manual_right_down'),
					)
					_changed = False
					for _command, _field in _fields:
						if _command != -1 and _mapping.isFired(_command, _key):
							_gun_state[_field] = _down
							_changed = True
					if _changed:
						_gun_state['manual_input_events'] = True
					return _changed
				except Exception as _movement_key_error:
					LOG_DEBUG('Movement key-state handling failed:', str(_movement_key_error))
					return False

			def _handle_cruise_key(event):
				'''Mirror PlayerAvatar.handleKey's R/F cruise state machine.'''
				try:
					import CommandMapping as _CruiseMapping
					_mapping = _CruiseMapping.g_instance
					_key = event.key
					_is_forward = _mapping.isFired(
						_CruiseMapping.CMD_INCREMENT_CRUISE_MODE, _key)
					_is_backward = _mapping.isFired(
						_CruiseMapping.CMD_DECREMENT_CRUISE_MODE, _key)
					_is_manual = _mapping.isFiredList((
						_CruiseMapping.CMD_MOVE_FORWARD,
						getattr(_CruiseMapping, 'CMD_MOVE_FORWARD_SPEC', -1),
						_CruiseMapping.CMD_MOVE_BACKWARD), _key)
					if _is_manual and event.isKeyDown():
						if int(_gun_state.get('cruise_mode', 0) or 0) != 0:
							_set_cruise_mode(0)
						return False
					if not _is_forward and not _is_backward:
						return False
					# Consume both edges. Only key-down changes the mode.
					if not event.isKeyDown() or getattr(player, '_is_dead', False):
						return True
					_now = BigWorld.time()
					_last_key = _gun_state.get('cruise_last_key')
					_last_time = float(_gun_state.get('cruise_last_time', -1.0))
					if _key == _last_key and _last_time >= 0.0 and _now - _last_time < 0.35:
						_count = int(_gun_state.get('cruise_press_count', 0) or 0) + 1
					else:
						_count = 1
					_gun_state['cruise_last_key'] = _key
					_gun_state['cruise_last_time'] = _now
					_gun_state['cruise_press_count'] = _count
					_double_press = (_count == 2)
					_mode = int(_gun_state.get('cruise_mode', 0) or 0)
					if _is_forward:
						_mode = 3 if _double_press else min(3, _mode + 1)
					else:
						_mode = -2 if _double_press else max(-2, _mode - 1)
					_set_cruise_mode(_mode)
					return True
				except Exception as _cruise_key_error:
					LOG_DEBUG('Cruise key handling failed:', str(_cruise_key_error))
					return False

			def _play_autoaim_sound(event_name):
				'''Use a live per-battle notification queue, rebuilding a swept one.'''
				try:
					_notifications = getattr(player, 'soundNotifications', None)
					_queues = (getattr(
						_notifications, '_IngameSoundNotifications__soundQueues', None)
						if _notifications is not None else None)
					if _notifications is None or _queues is None:
						from gui.IngameSoundNotifications import IngameSoundNotifications
						_notifications = IngameSoundNotifications()
						_notifications.start()
						player.soundNotifications = _notifications
					_notifications.play(event_name)
					return True
				except Exception as _autoaim_sound_error:
					LOG_DEBUG('Autoaim sound error:', str(_autoaim_sound_error))
					return False

			def _set_autoaim_target(target, sound_name=None):
				'''Apply the same aiming-mode and sound transitions as PlayerAvatar.'''
				previous = getattr(player, '_autoaim_target', None)
				if previous is target:
					return False
				player._autoaim_target = target
				try:
					from constants import AIMING_MODE as _AutoAimMode
					g_offline_aih.setAimingMode(
						target is not None, _AutoAimMode.TARGET_LOCK)
				except Exception as _autoaim_mode_error:
					LOG_DEBUG('Autoaim aiming-mode update failed:', str(_autoaim_mode_error))
				try:
					player.gunRotator.clientMode = (target is None)
				except Exception:
					pass
				if sound_name is None:
					sound_name = 'target_captured' if target is not None else 'target_unlocked'
				if sound_name:
					_play_autoaim_sound(sound_name)
				LOG_DEBUG('Autoaim state changed:', previous, '->', target)
				return True

			_orig_handleKeyEvent = g_offline_aih.handleKeyEvent
			_spawn_count = [0]
			def _mock_handleKeyEvent(event):
				import BigWorld, Keys, Math
				player = BigWorld.player()
				# Diagnostic-only native physics capability probe. F6 is otherwise
				# untouched by the offline battle implementation.
				if (event.isKeyDown() and
						event.key == getattr(Keys, 'KEY_F6', 64)):
					try:
						from gui.mods.offhangar.native_vehicle_physics_probe import request as _request_native_physics_probe
						_request_native_physics_probe()
						return True
					except Exception as _native_probe_key_error:
						LOG_ERROR(
							'NATIVE_PHYSICS_PROBE request failed: %s' %
							str(_native_probe_key_error))
				# X-ray overlay first, and only when it was actually armed by config.
				# It claims F8/F9/F10 and returns True for those, so nothing else in
				# this handler sees them; every other key falls straight through.
				_xr = globals().get('g_offh_internal_xray')
				if _xr is not None:
					try:
						if _xr.handle_key_event(event):
							return True
					except Exception as _xke:
						LOG_DEBUG('X-ray key handling failed, disabling overlay:', str(_xke))
						try: _xr.stop()
						except Exception: pass
						globals()['g_offh_internal_xray'] = None
				# Post-death spectator: left-click cycles to the next living ally, right-click
				# the previous one - but NOT while the ESC menu is up. Its clicks are meant for
				# the menu, and they were also switching the spectated tank underneath it.
				if getattr(player, '_offh_spectating', False) and not _offh_cursor_shown():
					try:
						if event.isKeyDown() and event.key == Keys.KEY_LEFTMOUSE:
							player._offh_spec_idx = getattr(player, '_offh_spec_idx', 0) + 1
							return
						if event.isKeyDown() and event.key == Keys.KEY_RIGHTMOUSE:
							# right-click = previous target; negative index wraps (Python % maps it)
							player._offh_spec_idx = getattr(player, '_offh_spec_idx', 0) - 1
							return
					except Exception:
						pass

				_record_manual_movement_key(event)
				if _handle_cruise_key(event):
					return True

				if event.key == Keys.KEY_RIGHTMOUSE:
					if event.isKeyDown():
						_gun_state['rmb_down'] = True
						bot = getattr(player, '_outlined_bot', None)
						prev_target = getattr(player, '_autoaim_target', None)
						curr_target = None
						if bot is not None:
							team = getattr(bot, '_bot_team', 2)
							player_team = getattr(player, '_offhangar_team', 1)
							if team != player_team and getattr(bot, 'health', 0) > 0:
								if prev_target == bot:
									curr_target = None
								else:
									curr_target = bot
						if prev_target != curr_target:
							_set_autoaim_target(curr_target)

						if getattr(player, '_autoaim_target', None) is None:
							_gun_state['locked_local_yaw'] = turret_yaw[0]
							_gun_state['locked_local_pitch'] = gun_pitch[0]
					else:
						_gun_state['rmb_down'] = False

				# An OPEN equipment fly-out owns the number keys while it is up. Route them to
				# the panel, but only when a fly-out is actually expanded and the key is bound
				# to one of its entities - a stale fly-out must not eat the shell keys forever.
				if event.isKeyDown() and event.key in (Keys.KEY_1, Keys.KEY_2, Keys.KEY_3, Keys.KEY_4, Keys.KEY_5, Keys.KEY_6):
					try:
						import gui.WindowsManager as _WMfk
						_bwfk = getattr(_WMfk.g_windowsManager, 'battleWindow', None)
						_panelfk = getattr(_bwfk, 'consumablesPanel', None) if _bwfk is not None else None
						if _panelfk is not None and getattr(_panelfk, '_ConsumablesPanel__expandEquipmentIdx', None) is not None:
							_kcmap = getattr(_panelfk, '_ConsumablesPanel__entitiesKCMap', None) or {}
							if event.key in _kcmap:
								_panelfk.handleKey(event.key)
								return
					except Exception as _fke:
						LOG_DEBUG('flyout key route err:', str(_fke))

				# Shell slots honor Controls->Equipment rebinds (CMD_AMMO_CHOICE_1..3)
				_ammo_bind = [Keys.KEY_1, Keys.KEY_2, Keys.KEY_3]
				try:
					import CommandMapping as _CMap
					_ammo_bind = [(_CMap.g_instance.get('CMD_AMMO_CHOICE_%d' % (_n + 1)) or _ammo_bind[_n]) for _n in range(3)]
				except Exception:
					pass
				if event.isKeyDown() and event.key in _ammo_bind:
					try:
						idx = _ammo_bind.index(event.key)
						from gui import WindowsManager
						bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
						panel = getattr(bw, 'consumablesPanel', None) if bw else None
						if panel and ('ammo_%d' % idx) in _gun_state:
							# Empty type: retail refuses the switch outright. Switching anyway set
							# clip = min(clip_size, 0) = 0 AND started a full reload animation for
							# ammunition that does not exist.
							if (_gun_state.get('ammo_%d' % idx, 0) or 0) <= 0:
								LOG_DEBUG('Ammo switch refused: slot %d is empty' % idx)
							elif _gun_state.get('shot_index', 0) != idx:
								_gun_state['shot_index'] = idx
								# The magazine is EMPTY while the type is being changed. That reload is the
								# gun being cleared and refilled with the other shell, so leaving the clip
								# full meant the green rounds sat there through the whole animation. The
								# reload-complete handler puts them back.
								_gun_state['clip'] = 0
								_gun_state['reloadTime'] = _gun_state['reload'] # Full reload on switch
								panel.setCurrentShell(idx)
								panel.setShellQuantityInSlot(idx, _gun_state['ammo_%d' % idx], _gun_state['clip'])
								try: panel.setCoolDownTime(idx, 0.0)
								except Exception as e:
									import debug_utils; debug_utils.LOG_DEBUG('setCoolDownTime reset error switch:', str(e))
								try: panel.setCoolDownTime(idx, _gun_state['reloadTime'])
								except Exception as e:
									import debug_utils; debug_utils.LOG_DEBUG('setCoolDownTime error switch:', str(e))
								try:
									aim = getattr(g_offline_aih, 'aim', None)
									if aim:
										try: aim.setReloading(0.0, None)
										except: pass
										aim.setReloading(_gun_state['reloadTime'], None)
										aim.setAmmoStock(_gun_state['ammo_%d' % idx], _gun_state['clip'], False)
								except Exception as e:
									import debug_utils; debug_utils.LOG_DEBUG('aim error switch:', str(e))
					except Exception as e:
						import debug_utils
						debug_utils.LOG_DEBUG('Key ammo switch error:', str(e))

				# Consumable slots honor Controls->Equipment rebinds (CMD_AMMO_CHOICE_4..6)
				_cons_bind = [Keys.KEY_4, Keys.KEY_5, Keys.KEY_6]
				try:
					import CommandMapping as _CMap
					_cons_bind = [(_CMap.g_instance.get('CMD_AMMO_CHOICE_%d' % (_n + 4)) or _cons_bind[_n]) for _n in range(3)]
				except Exception:
					pass
				if event.isKeyDown() and event.key in _cons_bind:
					# One entry point for every consumable. The old inline handler repaired
					# EVERYTHING from any kit and offered no module/crew choice at all.
					try:
						_offh_activate_equipment(_cons_bind.index(event.key) + 3)
					except Exception as _eqe:
						import debug_utils
						debug_utils.LOG_DEBUG('Consumable hotkey error:', str(_eqe))
					return
				if event.isKeyDown() and event.key == Keys.KEY_K:
					try:
						import BigWorld
						player = BigWorld.player()
						if hasattr(player, 'arena'):
							p_team = getattr(player, '_offhangar_team', getattr(player, 'team', 1))
							p_name = getattr(player, 'name', 'Player')
							p_dbid = getattr(player, 'databaseID', 1)
							_td = None
							try: _td = loaded_models.get('td')
							except: pass
							if not _td: _td = getattr(player, 'vehicleTypeDescriptor', None)

							p_cd = getattr(getattr(_td, 'type', None), 'compactDescr', 0)

							LOG_DEBUG('BATTLE RESULTS LOCAL P_CD IS:', p_cd)

							import debug_utils
							debug_utils.LOG_DEBUG('BATTLE RESULTS P_CD IS:', p_cd)

							if p_cd == 0 and hasattr(player, 'arena') and player.playerVehicleID in player.arena.vehicles:
								_vinfo = player.arena.vehicles[player.playerVehicleID]
								_vtype = _vinfo.get('vehicleType', None)
								if _vtype:
									p_cd = getattr(getattr(_vtype, 'type', None), 'compactDescr', 0)
									debug_utils.LOG_DEBUG('BATTLE RESULTS FALLBACK P_CD IS:', p_cd)

							# Match the live HUD's canonical per-team frag totals. Explicit
							# capture/wipe outcomes override this display-derived fallback below.
							allied, enemy = _offh_team_score(player)
							# A capture win ends the battle through this same K flow and
							# forces the outcome instead of the frag comparison below.
							_forced_w = globals().pop('G_OFFH_FORCED_WINNER', None)
							if _forced_w is not None:
								if _forced_w == 0:
									# Draw: winnerTeam is derived from these two, and only EQUAL counts
									# map to 0. Forcing 0 straight through read as a defeat.
									allied = enemy = 0
								else:
									allied, enemy = (1, 0) if _forced_w == p_team else (0, 1)

							def _show_res():
								try:
									from gui.SystemMessages import SM_TYPE, pushMessage
									pushMessage('Offline battle finished. Returning to Hangar...'.encode('utf-8'), SM_TYPE.Information)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								try:
									import MusicController
									if hasattr(MusicController, 'g_musicController') and MusicController.g_musicController:
										_mc = MusicController.g_musicController
										try: _mc.stop()
										except: pass
										evt = None
										if allied > enemy:
											evt = getattr(MusicController, 'MUSIC_EVENT_COMBAT_VICTORY', getattr(MusicController, 'MUSIC_EVENT_VICTORY', 'music_victory'))
										elif allied < enemy:
											evt = getattr(MusicController, 'MUSIC_EVENT_COMBAT_LOSE', getattr(MusicController, 'MUSIC_EVENT_LOSE', 'music_lose'))
										else:
											evt = getattr(MusicController, 'MUSIC_EVENT_COMBAT_DRAW', getattr(MusicController, 'MUSIC_EVENT_DRAW', 'music_draw'))
										try: _mc.play(evt)
										except: pass
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY MUSIC:', e); import traceback; LOG_DEBUG(traceback.format_exc())

								try:
									import battle_results_shared
									mock_arena_id = 999

									v_id = getattr(player, 'playerVehicleID', 1)
									p_max_health = getattr(getattr(player, 'vehicleTypeDescriptor', None), 'maxHealth', 1000)
									p_health = getattr(getattr(player, 'vehicle', None), 'health', p_max_health)

									_player_mock = globals().get('G_MOCK_VEHICLES', {}).get(getattr(player, 'playerVehicleID', -1))
									if _player_mock is not None:
										p_health = max(0, int(getattr(_player_mock, 'health', p_health) or 0))
									_p_killer_id = getattr(_player_mock, 'last_killer_id', 255) if p_health <= 0 else 0

									total_dmg_dealt = 0
									total_frags = 0
									total_hits = 0
									players_dict = {p_dbid: {'name': p_name, 'clanDBID': 0, 'clanAbbrev': '', 'prebattleID': 0, 'team': p_team, 'igrType': 0}}
									vehicles_dict = {v_id: {'health': p_health, 'credits': 10000, 'xp': 1000, 'shots': 10, 'hits': 8, 'he_hits': 0, 'pierced': 8, 'damageDealt': 0, 'damageAssisted': 0, 'damageReceived': max(0, p_max_health - p_health), 'shotsReceived': 0, 'spotted': 0, 'damaged': 0, 'kills': 0, 'tdamageDealt': 0, 'tkills': 0, 'isTeamKiller': False, 'capturePoints': 0, 'droppedCapturePoints': 0, 'mileage': 100, 'lifeTime': 300, 'killerID': _p_killer_id, 'achievements': [], 'repair': 0, 'freeXP': 50, 'details': {}, 'accountDBID': p_dbid, 'team': p_team, 'typeCompDescr': p_cd, 'gold': 0}}
									personal_details = {}

									for vid, vinfo in getattr(player.arena, 'vehicles', {}).items():
										if vid == v_id: continue
										bot_team = vinfo.get('team', 2)

										_mock_vehicles = globals().get('G_MOCK_VEHICLES', {})
										if vid in _mock_vehicles:
											bot_team = getattr(_mock_vehicles[vid], '_bot_team', bot_team)
										bot_name = vinfo.get('name', 'Bot')
										# Force bot DBID to be its vehicle ID so it never overlaps the player's DBID!
										bot_dbid = vid
										td = vinfo.get('vehicleType', None)

										_mock_vehicles = globals().get('G_MOCK_VEHICLES', {})
										if vid in _mock_vehicles:
											_true_td = getattr(_mock_vehicles[vid], 'typeDescriptor', None)
											if _true_td: td = _true_td

										td_type = getattr(td, 'type', None)
										bot_cd = getattr(td_type, 'compactDescr', 0)

										players_dict[bot_dbid] = {'name': bot_name, 'clanDBID': 0, 'clanAbbrev': '', 'prebattleID': 0, 'team': bot_team, 'igrType': 0}

										is_killed = not vinfo.get('isAlive', True)
										bot_hp = getattr(td, 'maxHealth', 1000)
										bot_max_hp = bot_hp

										_mock_vehicles = globals().get('G_MOCK_VEHICLES', {})
										if vid in _mock_vehicles:
											bot_hp = max(0, getattr(_mock_vehicles[vid], 'health', 0))
											bot_max_hp = getattr(_mock_vehicles[vid], 'maxHealth', bot_max_hp)
											if bot_hp <= 0: is_killed = True

										if not 'mock_vehicles' in locals() and not '_mock_vehicles' in locals():
											bot_hp = 0 if is_killed else bot_max_hp

										# Retrieve damage tracking from mock_vehicles
										_dmg_from_player = 0
										_dmg_from_bots = 0
										_hits_from_player = 0
										_mock_vehicles = globals().get('G_MOCK_VEHICLES', {})
										if vid in _mock_vehicles:
											_dmg_from_player = getattr(_mock_vehicles[vid], 'damage_from_player', 0)
											_dmg_from_bots = getattr(_mock_vehicles[vid], 'damage_from_bots', 0)
											_hits_from_player = getattr(_mock_vehicles[vid], 'hits_from_player', 0)

										# Removed dangerous fallback! Only explicitly tracked damage counts.
										dmg_received = bot_max_hp - bot_hp

										player_killed_this = is_killed and _dmg_from_player > 0 and _dmg_from_player >= (dmg_received / 2.0)
										if player_killed_this and bot_team == p_team: player_killed_this = False

										total_dmg_dealt += _dmg_from_player
										total_hits += _hits_from_player
										if player_killed_this: total_frags += 1

										killer_id = v_id if player_killed_this else (getattr(_mock_vehicles.get(vid, None), 'last_killer_id', 255) if is_killed else 0)

										# Simulate some random shots and hits if the bot dealt damage
										_bot_shots = max(1, int(_dmg_from_bots / 200.0)) if _dmg_from_bots > 0 else 1
										vehicles_dict[vid] = {'health': bot_hp, 'credits': 100, 'xp': 100, 'shots': _bot_shots, 'hits': _bot_shots, 'he_hits': 0, 'pierced': _bot_shots, 'damageDealt': _dmg_from_bots, 'damageAssisted': 0, 'damageReceived': dmg_received, 'shotsReceived': max(1, int(dmg_received / 300.0)) if dmg_received > 0 else 0, 'spotted': 0, 'damaged': 0, 'kills': 0, 'tdamageDealt': 0, 'tkills': 0, 'isTeamKiller': False, 'capturePoints': 0, 'droppedCapturePoints': 0, 'mileage': 100, 'lifeTime': 300, 'killerID': killer_id, 'achievements': [], 'repair': 0, 'freeXP': 5, 'details': {}, 'accountDBID': bot_dbid, 'team': bot_team, 'typeCompDescr': bot_cd, 'gold': 0}

										if _dmg_from_player > 0 or bot_team != p_team:
											personal_details[vid] = {'spotted': 1 if bot_team != p_team else 0, 'killed': 1 if player_killed_this else 0, 'hits': _hits_from_player, 'he_hits': 0, 'pierced': _hits_from_player, 'damageDealt': _dmg_from_player, 'damageAssisted': 0, 'crits': 1 if player_killed_this else 0, 'fire': 0}

									_feedback_values = None
									try:
										from gui.mods.offhangar import battle_feedback as _offh_feedback_results
										if _offh_stats_for(player) is not None:
											_feedback_values = _offh_feedback_results.result_values(
												_offh_stats_for(player), BigWorld.time())
									except Exception as _feedback_error:
										LOG_DEBUG('Battle feedback result build failed:', str(_feedback_error))
									if _feedback_values is not None:
										total_dmg_dealt = _feedback_values['damageDealt']
										total_hits = _feedback_values['hits']
										total_frags = _feedback_values['kills']
										personal_details = _feedback_values['details']

									vehicles_dict[v_id]['damageDealt'] = total_dmg_dealt
									vehicles_dict[v_id]['kills'] = total_frags
									vehicles_dict[v_id]['hits'] = total_hits
									vehicles_dict[v_id]['pierced'] = (_feedback_values['pierced'] if _feedback_values is not None else total_hits)
									vehicles_dict[v_id]['shots'] = (_feedback_values['shots'] if _feedback_values is not None else globals().get('G_OFFHANGAR_SHOTS_FIRED', total_hits))
									vehicles_dict[v_id]['he_hits'] = (_feedback_values['he_hits'] if _feedback_values is not None else 0)
									vehicles_dict[v_id]['damageAssisted'] = (_feedback_values['damageAssisted'] if _feedback_values is not None else 0)
									vehicles_dict[v_id]['damageReceived'] = (_feedback_values['damageReceived'] if _feedback_values is not None else max(0, p_max_health - p_health))
									vehicles_dict[v_id]['shotsReceived'] = (_feedback_values['shotsReceived'] if _feedback_values is not None else 0)
									vehicles_dict[v_id]['spotted'] = (_feedback_values['spotted'] if _feedback_values is not None else len(personal_details))
									vehicles_dict[v_id]['damaged'] = (_feedback_values['damaged'] if _feedback_values is not None else len(personal_details))
									vehicles_dict[v_id]['capturePoints'] = (_feedback_values['capturePoints'] if _feedback_values is not None else 0)
									vehicles_dict[v_id]['mileage'] = (_feedback_values['mileage'] if _feedback_values is not None else 0)
									vehicles_dict[v_id]['lifeTime'] = (_feedback_values['lifeTime'] if _feedback_values is not None else 0)

									for v_iter_id, v_iter_data in vehicles_dict.items():
										k_id = v_iter_data.get('killerID', 0)
										if k_id and k_id in vehicles_dict and k_id != v_iter_id:
											vehicles_dict[k_id]['kills'] = vehicles_dict[k_id].get('kills', 0) + 1
									if _feedback_values is not None:
										vehicles_dict[v_id]['kills'] = total_frags

									mock_res = {
										'arenaUniqueID': mock_arena_id,
										'personal': {'health': p_health, 'credits': 10000, 'xp': 1000, 'shots': (_feedback_values['shots'] if _feedback_values is not None else globals().get('G_OFFHANGAR_SHOTS_FIRED', max(0, total_hits))), 'hits': total_hits, 'he_hits': (_feedback_values['he_hits'] if _feedback_values is not None else 0), 'pierced': (_feedback_values['pierced'] if _feedback_values is not None else total_hits), 'damageDealt': total_dmg_dealt, 'damageAssisted': (_feedback_values['damageAssisted'] if _feedback_values is not None else 0), 'damageReceived': (_feedback_values['damageReceived'] if _feedback_values is not None else max(0, p_max_health - p_health)), 'shotsReceived': (_feedback_values['shotsReceived'] if _feedback_values is not None else 0), 'spotted': (_feedback_values['spotted'] if _feedback_values is not None else len(personal_details)), 'damaged': (_feedback_values['damaged'] if _feedback_values is not None else len(personal_details)), 'kills': total_frags, 'tdamageDealt': 0, 'tkills': 0, 'isTeamKiller': False, 'capturePoints': (_feedback_values['capturePoints'] if _feedback_values is not None else 0), 'droppedCapturePoints': (_feedback_values['droppedCapturePoints'] if _feedback_values is not None else 0), 'mileage': (_feedback_values['mileage'] if _feedback_values is not None else 0), 'lifeTime': (_feedback_values['lifeTime'] if _feedback_values is not None else 0), 'killerID': _p_killer_id, 'achievements': [], 'repair': 0, 'freeXP': 50, 'details': personal_details, 'accountDBID': p_dbid, 'team': p_team, 'typeCompDescr': p_cd, 'gold': 0, 'xpPenalty': 0, 'creditsPenalty': 0, 'creditsContributionIn': 0, 'creditsContributionOut': 0, 'tmenXP': 0, 'eventCredits': 0, 'eventGold': 0, 'eventXP': 0, 'eventFreeXP': 0, 'eventTMenXP': 0, 'autoRepairCost': 0, 'autoLoadCost': (0, 0), 'autoEquipCost': (0, 0), 'isPremium': True, 'premiumXPFactor10': 15, 'premiumCreditsFactor10': 15, 'dailyXPFactor10': 10, 'aogasFactor10': 10, 'markOfMastery': 0, 'dossierPopUps': []},
										'common': {'arenaTypeID': getattr(player.arena, 'arenaTypeID', 1), 'arenaCreateTime': __import__('time').time() - (_feedback_values['lifeTime'] if _feedback_values is not None else 0), 'winnerTeam': p_team if allied > enemy else (0 if allied==enemy else (3-p_team)), 'finishReason': 1, 'duration': (_feedback_values['lifeTime'] if _feedback_values is not None else 0), 'bonusType': 1, 'guiType': 1, 'vehLockMode': 0},
										'players': players_dict,
										'vehicles': vehicles_dict
									}

									if hasattr(battle_results_shared, 'VEH_FULL_RESULTS'):
										for k in battle_results_shared.VEH_FULL_RESULTS:
											if k not in mock_res['personal']: mock_res['personal'][k] = [] if 'list' in k or k == 'achievements' else (0 if k != 'details' else {})
									if hasattr(battle_results_shared, 'VEH_BASE_RESULTS'):
										for k in battle_results_shared.VEH_BASE_RESULTS:
											for v in mock_res['vehicles']:
												if k not in mock_res['vehicles'][v]: mock_res['vehicles'][v][k] = [] if 'list' in k or k == 'achievements' else (0 if k != 'details' else {})

									def _mock_get(arenaUniqueID, callback):
										import BigWorld
										BigWorld.callback(0.1, lambda: callback(1, mock_res))

									player_brc = getattr(player, 'battleResultsCache', None)
									if player_brc:
										orig_br_get = player_brc.get
										player_brc.get = _mock_get

									from gui import WindowsManager
									window = getattr(WindowsManager.g_windowsManager, 'window', None)
									if hasattr(window, 'onBattleResultsReceived'): window.onBattleResultsReceived(True, mock_arena_id)
									elif hasattr(window, 'battleResults') and hasattr(window.battleResults, 'show'): window.battleResults.show(mock_arena_id)
									elif hasattr(window, 'battleResults') and hasattr(window.battleResults, '_BattleResultsManager__showBattleResults'): window.battleResults._BattleResultsManager__showBattleResults(mock_arena_id)
								except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

							BigWorld.callback(4.0, _show_res)
							player.leaveArena()
					except Exception: pass

				if event.isKeyDown() and event.key in (Keys.KEY_O, Keys.KEY_P, Keys.KEY_L):
					try:
						_spawn_entered_at = time.time()
						player = BigWorld.player()
						start_pos, dir_vec = player.gunRotator._VehicleGunRotator__getCurShotPosition()
						dir_vec.normalise()
						hit = BigWorld.wg_collideSegment(_offh_bspace(), start_pos, start_pos + dir_vec.scale(500.0), 128)
						target_pos = hit[0] if hit else start_pos + dir_vec.scale(50.0)

						# Auto-spawner forces a spawn location (original map spawn points)
						_forced_pos = getattr(player, '_forced_spawn_pos', None)
						if _forced_pos is not None:
							target_pos = Math.Vector3(
								_forced_pos[0], _forced_pos[1], _forced_pos[2])
						else:
							# Manual O/P/L spawns need local safety search. Server and
							# auto-formation positions are already grounded and reserved;
							# changing those from each client's different local occupancy
							# made identical LAN coordinates spawn in different places.
							target_pos = _find_safe_spawn(target_pos)

						td = None
						bot_name = 'Bot ' + str(_spawn_count[0])
						bot_team = 1 if event.key == Keys.KEY_L else 2
						# Auto-spawner overrides team/facing. Read them synchronously here:
						# the model-load callback below runs async and must not race the next spawn.
						_forced_team = getattr(player, '_forced_spawn_team', None)
						if _forced_team in (1, 2): bot_team = _forced_team
						_forced_yaw_local = getattr(player, '_forced_spawn_yaw', None)
						if bot_team == (getattr(player, '_offhangar_team', 1) or 1):
							bot_name = 'Ally ' + str(_spawn_count[0])
						if event.key == Keys.KEY_O:
							td = loaded_models.get('td')
							bot_name = 'Clone ' + str(_spawn_count[0])
						elif event.key in (Keys.KEY_P, Keys.KEY_L):
							try:
								import random
								from items import vehicles
								_fv = getattr(player, '_forced_spawn_vehname', None)
								if _fv:
									chosen = _fv
								else:
									import nations
									from gui.mods.offhangar.bot_ai import vehicle_in_battle_tier_band
									cur_tier = loaded_models['td'].type.level
									candidates = []
									for nation in nations.AVAILABLE_NAMES:
										nationID = nations.INDICES[nation]
										for v in vehicles.g_list.getList(nationID).itervalues():
											if vehicle_in_battle_tier_band(cur_tier, v['level']) and not _offh_veh_excluded(v):
												candidates.append(v['name'])
									LOG_DEBUG('KEY P pressed! cur_tier=%d candidates=%d' % (cur_tier, len(candidates)))
									chosen = random.choice(candidates) if candidates else None
								if chosen:
									descriptors = getattr(player, '_offh_vehicle_descriptors', None)
									if descriptors is None:
										descriptors = {}
										player._offh_vehicle_descriptors = descriptors
									td = descriptors.get(chosen)
									if td is None:
										td = vehicles.VehicleDescr(typeName=chosen)
										descriptors[chosen] = td
									bot_name = ('Ally ' if bot_team == (getattr(player, '_offhangar_team', 1) or 1) else 'Enemy ') + chosen.split(':')[-1] + ' ' + str(_spawn_count[0])
							except Exception as e:
								import traceback
								LOG_DEBUG('Random spawn error:', str(e), traceback.format_exc())
								td = loaded_models.get('td')

						if not td: return True

						# OOM guard: each bot is a full tank (models + textures +
						# per-component VehicleStickers + recoil). Spawning far past a
						# normal battle exhausts the 32-bit client and it crashes
						# NATIVELY mid-spawn (no Python traceback). Cap the live count
						# (player + bots); raise max_total_bots in config.json to allow more.
						try:
							from _constants import CONFIG_OPTIONS as _CFG_CAP
							_bot_cap = int(_CFG_CAP.get('max_total_bots', 50))
						except Exception:
							_bot_cap = 50
						if _bot_cap > 0 and len(globals().get('G_MOCK_VEHICLES', {}) or {}) >= _bot_cap:
							LOG_DEBUG('Bot spawn capped at %d live (raise max_total_bots in config.json)' % _bot_cap)
							return True

						try:
							_offh_load_hit_testers(td)
						except Exception as e:
							LOG_DEBUG("Error loading hitTesters for bot:", str(e))

						e_id = 1000 + _spawn_count[0]
						_spawn_count[0] += 1
						_network_server_id = getattr(player, '_offhangar_network_forced_id', None)
						_network_server_name = getattr(player, '_offhangar_network_forced_name', None)
						_network_server_state = getattr(player, '_offhangar_network_forced_state', None) or {}
						_network_bot_id = getattr(player, '_forced_spawn_bot_id', None)
						_network_bot_slot = getattr(player, '_forced_spawn_bot_slot', None)
						_forced_display_name = getattr(player, '_forced_spawn_name', None)
						_bot_display_name = str(_network_server_name or _forced_display_name or bot_name)

						# Load visual models

						_spawn_requested_at = time.time()
						def _on_bot_models_loaded(resourceRefs, bot_display_name=_bot_display_name,
								_bot_gen=_offh_my_gen[0]):
							if globals().get('g_offh_battle_gen', 0) != _bot_gen:
								return
							_spawn_build_started = time.time()
							def _network_spawn_complete():
								if _network_server_id is not None:
									try:
										getattr(player, '_offhangar_network_pending_remote_ids', {}).pop(_network_server_id, None)
									except Exception:
										pass
								if _network_bot_slot is not None:
									try:
										player._offh_auto_spawn_completed = int(getattr(
											player, '_offh_auto_spawn_completed', 0) or 0) + 1
										if player._offh_auto_spawn_completed >= int(getattr(
											player, '_offh_auto_spawn_expected', 0) or 0):
											# Every live bot now owns its component instances. Release any
											# unused batch bookkeeping instead of retaining a second lineup.
											player._offh_lineup_prefetch_refs = None
											player._offh_lineup_model_refs = None
											player._offh_lineup_model_pending = None
											_spawn_completed_at = time.time()
											try:
												from gui.mods.offhangar.logging import LOG_NOTE as _SPAWN_NOTE
												_SPAWN_NOTE('LAN bot lineup ready: bots=%d elapsed_ms=%d' % (
													player._offh_auto_spawn_completed,
													int((_spawn_completed_at - float(getattr(player,
														'_offh_spawn_batch_started_at', _spawn_completed_at))) * 1000.0)))
											except Exception:
												pass
											def _refresh_complete_lineup():
												try:
													from gui import WindowsManager as _spawn_wm
													battle = getattr(_spawn_wm.g_windowsManager,
														'battleWindow', None)
													if battle is not None and hasattr(
														battle, '_Battle__updatePlayers'):
														battle._Battle__updatePlayers()
												except Exception:
													pass
												try:
													from gui.mods.offhangar.logging import LOG_NOTE as _SETTLE_NOTE
													_SETTLE_NOTE('LAN bot lineup next-frame delay: %dms' % int(
														(time.time() - _spawn_completed_at) * 1000.0))
												except Exception:
													pass
											_offh_battle_callback(0.0, _refresh_complete_lineup)
									except Exception:
										pass
							try:
								ch = resourceRefs[td.chassis['models']['undamaged']]
								hu = resourceRefs[td.hull['models']['undamaged']]
								tu = resourceRefs[td.turret['models']['undamaged']]
								gu = resourceRefs[td.gun['models']['undamaged']]
								if ch is None or hu is None or tu is None or gu is None:
									raise ValueError('BigWorld.fetchModel returned None')
							except Exception as e:
								# NOT debug_utils.LOG_DEBUG - that one writes nothing in the release
								# client, so a bot whose models failed to load vanished without a trace.
								LOG_DEBUG('Bot model unpack error (bot will not spawn):', str(e))
								_network_spawn_complete()
								return
							# Loaded component models exist at the world origin until they are
							# attached. Hide all four immediately so a staggered spawn cannot flash
							# a complete tank at the map centre for one rendered frame.
							for _loaded_component in (ch, hu, tu, gu):
								try:
									_loaded_component.visible = False
									_loaded_component.visibleAttachments = False
								except Exception:
									pass
							e_mock = _MockVeh()
							e_mock._offh_native_model_root_ready = False
							e_mock.id = e_id
							e_mock.position = target_pos
							# Face the player
							import math
							e_mock.yaw = math.atan2(start_pos.x - target_pos.x, start_pos.z - target_pos.z)
							if _forced_yaw_local is not None:
								e_mock.yaw = _forced_yaw_local
							# _MockVeh starts with the player's pose. Commit the bot pose before
							# minimap, markers and PyModelObstacle can read that stale matrix.
							_VP.commit_pose(e_mock, e_mock.position, e_mock.yaw,
							                0.0, 0.0, sync_filter=False,
							                attach_servo=False, prime_model=False)
							e_mock.maxHealth = int(_network_server_state.get('max_health', getattr(td, 'maxHealth', 1000)) or getattr(td, 'maxHealth', 1000))
							e_mock.health = int(_network_server_state.get('health', e_mock.maxHealth) or 0)
							_offh_set_alive(e_mock, bool(_network_server_state.get('alive', True)) and e_mock.health > 0)
							e_mock.isStarted = True
							e_mock._bot_team = bot_team
							try:
								from _constants import CONFIG_OPTIONS as _BOT_VIS_CFG
								_bot_spotting = bool(_BOT_VIS_CFG.get('spotting_enabled', True))
							except Exception:
								_bot_spotting = True
							from gui.mods.offhangar.bot_ai import bot_initially_visible
							e_mock._spot_visible = bot_initially_visible(
								bot_team, getattr(player, '_offhangar_team', 1) or 1,
								_bot_spotting)
							e_mock._spot_until = 0.0
							e_mock._network_server_id = _network_server_id
							e_mock._network_remote = _network_server_id is not None
							e_mock._network_bot_id = _network_bot_id
							e_mock._network_bot_slot = _network_bot_slot
							e_mock._network_shared_bot = _network_bot_id is not None
							e_mock._network_bot_fire_seq = 0
							e_mock._network_bot_shell_index = 0
							LOG_DEBUG('SPAWN BOT: bot_team=%s bot_name=%s player_team=%s' % (bot_team, bot_display_name, getattr(player, '_offhangar_team', -99)))
							e_mock.publicInfo = {
								'vehicleType': td,
								'name': bot_display_name,
								'team': bot_team,
								'isAlive': bool(_network_server_state.get('alive', True)) and e_mock.health > 0,
								'isAvatarReady': True,
								'isTeamKiller': False,
								'accountDBID': 0,
								'clanAbbrev': '',
								'clanDBID': 0,
								'prebattleID': 0,
								'isPrebattleCreator': False,
							'events': {}
							}
							try:
								ch.visible = bool(e_mock._spot_visible)
								ch.visibleAttachments = bool(e_mock._spot_visible)
							except Exception:
								pass

							def _install_live_collision_obstacle(_mock=e_mock, _descriptor=td):
								"""Install the legacy static proxy only for Python-owned bodies."""
								if getattr(_mock, '_collision_obstacle', None) is not None:
									return True
								try:
									_mock._collision_obstacle = BigWorld.PyModelObstacle(
										_descriptor.hull['models']['undamaged'],
										_descriptor.turret['models']['undamaged'],
										_mock.matrix,
										True)
									return True
								except Exception as _obstacle_error:
									LOG_DEBUG('OfflineBattle PyModelObstacle Error:', _obstacle_error)
									return False
							e_mock._offh_install_collision_obstacle = (
								_install_live_collision_obstacle)
							# Establish the movement contract before touching the entity/model
							# root. A failed retail motor handoff must remain a visible native
							# failure, never an undeclared Python owner.
							_native_body_required = _offh_native_movement_required(
								player, e_mock, _offh_network_bot_role(player))

							_eid = BigWorld.createEntity('OfflineEntity', _offh_bspace(), 0, e_mock.position, (0, 0, e_mock.yaw), dict())
							e_mock.bw_entity = None
							_model_root_handoff = {}
							def _assign_model_when_ready(eid, model_to_add, retries=10,
									_e_mock=e_mock, _root_handoff=_model_root_handoff):
								if globals().get('g_offh_battle_gen', 0) != _bot_gen:
									return
								if not getattr(_e_mock, 'isAlive', True) or getattr(_e_mock, '_wreck_done', False):
									return  # bot died meanwhile: never re-add the intact model over the wreck
								ent = BigWorld.entity(eid)
								if ent:
									_e_mock.bw_entity = ent
									# Entity assignment installs its own default root motor. Retail
									# VehicleAppearance removes it before adding Servo(vehicle.matrix);
									# keeping both makes the chassis visibly twist between two owners.
									if not _offh_assign_entity_model_root(
											ent, model_to_add, _root_handoff):
										_e_mock._offh_native_model_root_ready = False
										if retries > 0:
											_offh_battle_callback(0.1, lambda: _assign_model_when_ready(
												eid, model_to_add, retries - 1, _e_mock, _root_handoff))
										elif not getattr(
												_e_mock, '_offh_native_model_root_error_logged', False):
											_e_mock._offh_native_model_root_error_logged = True
											LOG_ERROR('NATIVE_BOT_PHYSICS model root handoff failed id=%s' % (
												getattr(_e_mock, 'id', '?'),))
										return
									_e_mock._offh_native_model_root_ready = True
									try:
										_model_visible = bool(getattr(_e_mock, '_spot_visible', True))
										model_to_add.visible = _model_visible
										model_to_add.visibleAttachments = _model_visible
									except Exception:
										pass
									_native_body_prepared = False
									if _native_body_required:
										try:
											from gui.mods.offhangar.native_bot_physics import prepare as _prepare_native_bot_physics
											_native_body_prepared = _prepare_native_bot_physics(
												player, _e_mock, td, _offh_bspace(), BigWorld.time())
										except Exception as _native_prepare_error:
											LOG_DEBUG('Native bot physics prepare failed:', str(_native_prepare_error))
									if not _native_body_prepared and not _native_body_required:
										try:
											ent.filter = BigWorld.AvatarFilter()
											_e_mock.filter = ent.filter
										except: pass
									_VP.commit_pose(
										_e_mock, _e_mock.position, _e_mock.yaw,
										getattr(_e_mock, 'pitch', 0.0) or 0.0,
										getattr(_e_mock, 'roll', 0.0) or 0.0,
										space_id=_offh_bspace(), timestamp=BigWorld.time(),
										sync_filter=(not _native_body_prepared and
											not _native_body_required),
										attach_servo=True, prime_model=True)
									if not _native_body_prepared and not _native_body_required:
										_install_live_collision_obstacle()
									else:
										# PyModelObstacle is a second static collision shape. Keeping
										# it beside WGVehiclePhysics2 makes the native body collide with
										# its own legacy proxy.
										_e_mock._collision_obstacle = None
								elif retries > 0:
									_offh_battle_callback(0.1, lambda: _assign_model_when_ready(eid, model_to_add, retries - 1, _e_mock))
								else:
									try:
										_model_visible = bool(getattr(_e_mock, '_spot_visible', True))
										model_to_add.visible = _model_visible
										model_to_add.visibleAttachments = _model_visible
									except Exception:
										pass
									_add_model(model_to_add)
							# world-add moved BELOW mock registration: a failure in between
							# must not leave a ghost model in the world
							h_mat = Math.Matrix(); h_mat.setIdentity()
							t_mat = Math.Matrix(); t_mat.setIdentity()
							g_mat = Math.Matrix(); g_mat.setIdentity()
							ch.node('V').attach(hu)
							e_mock._t_node = hu.node('HP_turretJoint', t_mat)
							e_mock._t_node.attach(tu)
							e_mock._g_node = tu.node('HP_gunJoint', g_mat)
							e_mock._g_node.attach(gu)
							# Children are now safely parented under the hidden chassis. Their own
							# visibility can stay enabled; spotting toggles the chassis tree.
							for _attached_component in (hu, tu, gu):
								try:
									_attached_component.visible = True
									_attached_component.visibleAttachments = True
								except Exception:
									pass
							e_mock._gun_recoil = _setup_gun_recoil(gu, td)
							e_mock._swinging = _setup_swinging(ch, td)
							e_mock.model = ch
							e_mock.typeDescriptor = td
							e_mock._chassis_model = ch
							# Prime the hidden async model through the same adapter that later
							# owns its filter and Servo. No other live-bot code writes the root.
							_VP.commit_pose(
								e_mock, e_mock.position, e_mock.yaw,
								getattr(e_mock, 'pitch', 0.0) or 0.0,
								getattr(e_mock, 'roll', 0.0) or 0.0,
								sync_filter=False, attach_servo=False, prime_model=True)
							e_mock._hull_model = hu
							e_mock._turret_model = tu
							e_mock._gun_model = gu
							e_mock._t_mat = t_mat
							# was a local only, so the barrel could never be elevated
							e_mock._g_mat = g_mat
							# Build native damage-sticker objects one component per loading/countdown
							# callback. First combat impact should only add a decal, not initialize
							# three native attachments and visibly stall the frame.
							e_mock._sticker_map = {}
							e_mock._sticker_setup_done = False
							_offh_queue_sticker_warmup(player, e_mock)
							# Scrolling-track animation for the bot (original fashion system);
							# attached slightly delayed so the model is in the world first
							def _attach_bot_fashion(_bch=ch, _btd=td, _bm=e_mock):
								if globals().get('g_offh_battle_gen', 0) != _bot_gen:
									return
								# The ghost-fix delays the world-add (entity retries); a fashion
								# attached to a not-yet-inWorld model stays inert -> static tracks.
								# Wait for inWorld like the player path does.
								if not getattr(_bch, 'inWorld', False):
									_bm._fash_tries = (getattr(_bm, '_fash_tries', 0) or 0) + 1
									if _bm._fash_tries < 20 and getattr(_bm, 'isAlive', True):
										_offh_battle_callback(0.5, lambda: _attach_bot_fashion(_bch, _btd, _bm))
									return
								try:
									_bf = BigWorld.WGVehicleFashion()
									try:
										_bf.maxMovement = _btd.physics['speedLimits'][0]
									except Exception:
										pass
									# Swinging node 'V' is mandatory for attaching the fashion
									try:
										_b_sw = _btd.hull['swinging']
										_b_pp = tuple(_p * _m for (_p, _m) in zip(_b_sw['pitchParams'], (0.9, 1.88, 0.3, 4.0, 1.0, 1.0)))
										_bf.setPitchSwinging('V', *_b_pp)
										_bf.setRollSwinging('V', *_b_sw['rollParams'])
										_bf.setShotSwinging('V', _b_sw['sensitivityToImpulse'])
									except Exception:
										pass
									_bt = _btd.chassis['tracks']
									try:
										_bf.setLods(_btd.chassis['traces']['lodDist'], _btd.chassis['wheels']['lodDist'], _bt['lodDist'], _btd.hull['swinging']['lodDist'])
									except Exception:
										pass
									_bf.setTracks(_bt['leftMaterial'], _bt['rightMaterial'], _bt['textureScale'])
									# Road wheels + scroll source, same as the player fashion
									try:
										_bwcfg = _btd.chassis['wheels']
										for _bg in _bwcfg['groups']:
											_bn = ['%s%d' % (_bg[1], _bi) for _bi in range(_bg[3], _bg[3] + _bg[2])]
											_bf.addWheelGroup(_bg[0], _bg[4], _bn)
										for _bwh in _bwcfg['wheels']:
											_bf.addWheel(_bwh[0], _bwh[2], _bwh[1])
									except Exception:
										pass
									try:
										_bf.movementInfo = Math.Vector4(0.0, 0.0, 0.0, 0.0)
									except Exception:
										pass
									_bch.wg_fashion = _bf
									_bm._fashion = _bf
									try:
										from gui.mods.offhangar.native_bot_physics import bind_fashion as _bind_native_bot_fashion
										_bind_native_bot_fashion(_bm, _bf)
									except Exception:
										pass
									# see the note on the player fashion: wiring this to _trigger_shot_impulse
									# crashes the hangar load on a filter-less mock fashion.
									# Real half track gauge for the turn scroll split (see player feed)
									try:
										_btco = abs(float(_btd.physics.get('trackCenterOffset', 1.5)))
										_bm._tco = _btco if 0.3 <= _btco <= 3.0 else 1.5
									except Exception:
										_bm._tco = 1.5
								except Exception as _bfe:
									LOG_DEBUG('Bot track fashion failed:', str(_bfe))
							e_mock._offh_attach_bot_fashion = _attach_bot_fashion
							_offh_battle_callback(1.5, _attach_bot_fashion)
							class FakeEnemyAppearance(object):
								def __init__(self, tmat=None):
									from Event import Event
									self.onModelChanged = Event()
									# WG's TankIndicator._setup reads appearance.turretMatrix for EVERY
									# vehicle it follows and feeds it to wg_turretMatProv. Without it the
									# setup raises inside __update -> _waiting -> _setup the moment the GUI
									# follows a bot (postmortem), taking the rest of that update pass with
									# it. Reference the turret matrix the bot mutates in place each tick -
									# never a per-frame copy - so the needle tracks the real turret.
									if tmat is None:
										tmat = Math.Matrix()
										tmat.setIdentity()
									self.turretMatrix = tmat
								def changeVisibility(self, *a, **kw): pass
								def showDamageFromShot(self, *a, **kw): pass
								def showDamageFromExplosion(self, *a, **kw): pass
							e_mock.appearance = FakeEnemyAppearance(getattr(e_mock, '_t_mat', None))
							mock_vehicles[e_id] = e_mock
							if getattr(e_mock, '_network_remote', False):
								try:
									from gui.mods.offhangar.network_battle import update_remote_spotting
									update_remote_spotting(player, e_mock, True)
									e_mock._network_spot_initialized = True
									from gui.mods.offhangar.logging import LOG_NOTE as _REMOTE_NOTE
									_REMOTE_NOTE('LAN remote human ready server_id=%s entity_id=%s team=%s vehicle=%s visible=%s' % (
										str(_network_server_id), str(e_id), str(bot_team),
										str(getattr(getattr(td, 'type', None), 'name', '?')),
										str(bool(getattr(e_mock, '_spot_visible', False)))))
								except Exception as _remote_ready_error:
									from gui.mods.offhangar.logging import LOG_ERROR as _REMOTE_ERROR
									_REMOTE_ERROR('LAN remote human registration failed: %s' % str(
										_remote_ready_error))
							_network_spawn_complete()
							try:
								_assign_model_when_ready(_eid, ch)
							except Exception:
								# Once entity.model was assigned, adding the same chassis globally
								# creates a second world owner. Leave the native failure visible.
								if not _model_root_handoff.get('assigned', False):
									_add_model(ch)
							# Safety net for the invisible bot. _assign_model_when_ready can leave the
							# chassis out of the world entirely - its liveness guard returns without
							# scheduling a retry, and the entity path can silently not take. Everything
							# else about the bot works in that case (it drives, aims and shoots), which
							# is exactly what an invisible tank looks like. 2 s is well past the 1 s
							# retry budget, so a normal spawn never reaches this.
							# Safety net for the invisible bot: ONE shot, 2 s after spawn, for the case
							# where _assign_model_when_ready left the chassis out of the world entirely.
							#
							# It stays a one-shot on purpose. A periodic version (1.7.3) looked obvious and
							# was wrong: models added through _add_model report inWorld False in this client
							# even while they render perfectly, so the watchdog fired on 16 healthy bots in a
							# single battle and called _add_model on models that were already claimed - the
							# double-owner crash, dressed up as a fix. inWorld is only trustworthy as a
							# NEGATIVE signal at spawn time, before anything has been added.
							def _verify_bot_visible(_vch=ch, _vm=e_mock, _vid=e_id):
								try:
									if globals().get('g_offh_battle_gen', 0) != _bot_gen:
										return
									if not getattr(_vm, 'isAlive', False) or getattr(_vm, '_wreck_done', False):
										return
									if _model_root_handoff.get('assigned', False):
										return
									if getattr(_vch, 'inWorld', False):
										return
									LOG_DEBUG('BOT INVISIBLE: id=%s never reached the world - re-adding' % _vid)
									_add_model(_vch)
								except Exception as _vbe:
									LOG_DEBUG('bot visibility check err:', str(_vbe))
							try:
								_offh_battle_callback(2.0, _verify_bot_visible)
							except Exception:
								pass
							import weakref
							e_mock.proxy = weakref.proxy(e_mock)

							from gui import WindowsManager
							player.arena.vehicles[e_id] = e_mock.publicInfo
							try:
								player.arena.onVehicleAdded(e_id)
							except: pass
							try:
								if (getattr(e_mock, '_spot_visible', True) and
										hasattr(WindowsManager.g_windowsManager.battleWindow, 'vMarkersManager')):
									e_mock.marker = WindowsManager.g_windowsManager.battleWindow.vMarkersManager.createMarker(e_mock.proxy)

								minimap = WindowsManager.g_windowsManager.battleWindow.minimap
								if minimap and getattr(e_mock, '_spot_visible', True):
									minimap.notifyVehicleStart(e_mock.id)
							except Exception as e:
								LOG_DEBUG('GUI Add error:', str(e))
							if _network_bot_slot is not None:
								_offh_record_spawn_timing(player,
									_spawn_requested_at - _spawn_entered_at,
									_spawn_build_started - _spawn_requested_at,
									time.time() - _spawn_build_started)
							LOG_DEBUG('Enemy Clone Spawned at:', target_pos)

						_prefetched_models = getattr(
							player, '_offh_forced_model_refs', None)
						if isinstance(_prefetched_models, dict):
							_on_bot_models_loaded(_prefetched_models)
						else:
							_offh_fetch_vehicle_models(td, _on_bot_models_loaded)
						return True
					except Exception as e:
						import traceback
						LOG_DEBUG('Clone spawn error:', traceback.format_exc())
				return _orig_handleKeyEvent(event)
			g_offline_aih.handleKeyEvent = _mock_handleKeyEvent
			try:
				from gui.mods.offhangar._constants import CONFIG_OPTIONS as _NET_SPAWN_CFG
				if bool(_NET_SPAWN_CFG.get('network_mode', False)) and not getattr(player, '_offhangar_network_fallback_local', False):
					# network_battle invokes the exact existing model/resource spawn
					# path for remote clients instead of duplicating it.
					player._offhangar_network_spawn_remote = _mock_handleKeyEvent
			except Exception:
				pass

			# --- 15v15 AUTO-SPAWN like the original game ---
			# In 0.8.2 ctf the teams spawn AT the teamBasePositions (the arena_defs
			# only carry teamSpawnPoints for the domination mode). The line-up is a
			# 5x3 grid behind the flag, 9 m spacing, facing the enemy base, with the
			# heavies up front and artillery at the back. bots_per_team in config.json.
			def _formation_slot(t_id, slot):
				# Returns (x, z, yaw) for a line-up slot of the given team.
				# Slot 0 = front row centre (the player's own slot in his team).
				import math
				try:
					from gui.mods.offhangar.prebaked_navigation import load_graph, spawn_pose
					_spawn_graph = globals().get('g_offh_baked_navigation_graph')
					if _spawn_graph is None:
						_spawn_graph = load_graph(globals().get('g_offh_battle_mapname', ''))
						globals()['g_offh_baked_navigation_graph'] = _spawn_graph
					_baked_pose = spawn_pose(_spawn_graph, t_id, slot)
					if _baked_pose is not None:
						return (_baked_pose[0], _baked_pose[2], _baked_pose[3])
					from gui.mods.offhangar.prebaked_navigation import STOCK_MAPS
					if globals().get('g_offh_battle_mapname', '') in STOCK_MAPS:
						raise ValueError('stock map has no validated spawn slot')
				except Exception as _spawn_graph_error:
					try:
						from gui.mods.offhangar.prebaked_navigation import STOCK_MAPS
						if globals().get('g_offh_battle_mapname', '') in STOCK_MAPS:
							raise _spawn_graph_error
					except ImportError:
						pass
				_sp = globals().get('g_offline_spawns', {}) or {}
				_bs = globals().get('g_offline_bases', {}) or {}
				pts = list(_sp.get(t_id, []) or [])
				# True when these are the arena's REAL spawn points (not the base-flag fallback):
				# the original game puts each vehicle ON its own spawn point, so the grid offset
				# below must not be applied while a distinct real point is still free.
				_real_pts = bool(pts)
				if not pts:
					pts = [(_b.x, _b.z) for _b in (_bs.get(t_id, []) or [])]
				if not pts:
					return (0.0, 0.0, 0.0)
				ax, az = pts[slot % len(pts)]
				_fb = globals().get('g_offline_bounds', None)
				# Face the enemy base (fallback: map centre)
				try:
					eb = _bs.get(2 if t_id == 1 else 1, [])
					yaw = math.atan2(eb[0].x - ax, eb[0].z - az) if eb else math.atan2(-ax, -az)
				except Exception:
					yaw = 0.0
				k = slot // len(pts)
				# Real spawn point still unshared on this pass -> stand exactly on it, like retail.
				if _real_pts and k == 0:
					return (ax, az, yaw)
				# Wide + shallow, like a retail spawn line. The old 5-wide/9 m grid packed 16
				# vehicles into a ~36 m x 27 m block right behind the flag - they visibly
				# clumped and clipped. 9 columns at 14 m spans ~112 m and needs only 2 rows.
				cols = (0, -1, 1, -2, 2, -3, 3, -4, 4)   # centre first, then fan out
				col = cols[k % len(cols)]
				row = k // len(cols)
				# Step rows TOWARD the enemy, i.e. into the map. Base flags commonly sit ON the
				# arena edge (Himmelsdorf team1: z=-302.6 while boundingBox stops at -300), so
				# the old 'behind the flag' offset pushed the whole line-up off the map - which
				# is why hulls ended up on roofs, inside edge buildings and stacked on each other.
				# Vehicles DO start inside their own base circle, as in retail - the line-up
				# only needs to stand off the flag itself and, crucially, in FRONT of it:
				# base flags sit at the arena edge (Himmelsdorf team1 z=-302.6 vs a -300
				# boundary), so there is no ground behind them to line up on.
				fwd = 20.0 + row * 12.0
				sx = ax + math.sin(yaw) * fwd + math.cos(yaw) * col * 14.0
				sz = az + math.cos(yaw) * fwd - math.sin(yaw) * col * 14.0
				# Safety net only (the anchor is already inside): a tight margin here so a
				# wide lateral slot cannot leave the arena, without flattening the rows.
				if _fb is not None:
					if sx < _fb[0] + 8.0: sx = _fb[0] + 8.0
					elif sx > _fb[2] - 8.0: sx = _fb[2] - 8.0
					if sz < _fb[1] + 8.0: sz = _fb[1] + 8.0
					elif sz > _fb[3] - 8.0: sz = _fb[3] - 8.0
				return (sx, sz, yaw)
			def _formation_pose(t_id, slot):
				try:
					from gui.mods.offhangar.prebaked_navigation import load_graph, spawn_pose
					_spawn_graph = globals().get('g_offh_baked_navigation_graph')
					if _spawn_graph is None:
						_spawn_graph = load_graph(globals().get('g_offh_battle_mapname', ''))
						globals()['g_offh_baked_navigation_graph'] = _spawn_graph
					_baked_pose = spawn_pose(_spawn_graph, t_id, slot)
					if _baked_pose is not None:
						return _baked_pose
					from gui.mods.offhangar.prebaked_navigation import STOCK_MAPS
					if globals().get('g_offh_battle_mapname', '') in STOCK_MAPS:
						raise ValueError('stock map has no validated spawn slot')
				except Exception as _spawn_graph_error:
					try:
						from gui.mods.offhangar.prebaked_navigation import STOCK_MAPS
						if globals().get('g_offh_battle_mapname', '') in STOCK_MAPS:
							raise _spawn_graph_error
					except ImportError:
						pass
				_x, _z, _yaw = _formation_slot(t_id, slot)
				return (_x, None, _z, _yaw)
			# Shared via globals: _aih_tick uses it for the player's spawn correction
			globals()['g_offline_formation_slot'] = _formation_slot
			globals()['g_offline_formation_pose'] = _formation_pose
			try:
				from gui.mods.offhangar._constants import CONFIG_OPTIONS as _NET_FORMATION_CFG
				if bool(_NET_FORMATION_CFG.get('network_mode', False)) and not getattr(player, '_offhangar_network_fallback_local', False):
					player._offhangar_network_formation = _formation_slot
			except Exception:
				pass

			try:
				from _constants import CONFIG_OPTIONS as _CFG_AS_EARLY
				_auto_spawn_delay = max(0.0, float(
					_CFG_AS_EARLY.get('auto_spawn_delay_seconds', 10.0)))
			except Exception:
				_auto_spawn_delay = 10.0
			_auto_spawn_not_before = time.time() + _auto_spawn_delay

			def _auto_spawn_teams(_spawn_gen=_offh_my_gen[0],
					_spawn_not_before=_auto_spawn_not_before):
				if globals().get('g_offh_battle_gen', 0) != _spawn_gen:
					return
				import BigWorld, Keys, Math, math
				try:
					_pl = BigWorld.player()
					if _pl is None or _battle_finished[0]:
						return
					from _constants import CONFIG_OPTIONS as _CFG
					_n_per_team = int(_CFG.get('bots_per_team', 15))
					if _n_per_team <= 0:
						return
					# Replicas can be called before the authority has published the shared
					# lineup. Retry cheaply instead of rebuilding a throw-away local match.
					try:
						from gui.mods.offhangar.network_battle import network_is_authority
						if (bool(_CFG.get('network_mode', False)) and
								not network_is_authority(_pl) and
								not (getattr(_pl, '_offhangar_network_bot_manifest', None) or [])):
							_offh_battle_callback(0.25, _auto_spawn_teams)
							return
					except Exception:
						pass
					_spawns = dict(globals().get('g_offline_spawns', {}) or {})
					_bases = globals().get('g_offline_bases', {}) or {}
					_p_team = getattr(_pl, '_offhangar_team', 1) or 1
					_bot_name_by_slot = {}
					_bot_identity_by_slot = {}
					try:
						for _bot_identity in (getattr(_pl, '_offhangar_network_bot_roster', None) or []):
							_key = (int(_bot_identity.get('team', 0) or 0), int(_bot_identity.get('slot', 0) or 0))
							_bot_name_by_slot[_key] = str(_bot_identity.get('name') or '')
							_bot_identity_by_slot[_key] = _bot_identity
					except Exception:
						_bot_name_by_slot = {}
						_bot_identity_by_slot = {}
					_local_bot_names = set()
					_local_callsigns = ('Atlas', 'Badger', 'Comet', 'Echo', 'Falcon', 'Frost',
						'Hawk', 'Jade', 'Kestrel', 'Lynx', 'Meteor', 'Nomad', 'Orion', 'Raven',
						'Rook', 'Saber', 'Scout', 'Talon', 'Viper', 'Wolf')
					def _bot_name(t_id, slot):
						_shared = _bot_name_by_slot.get((t_id, slot))
						if _shared:
							return _shared
						import random as _name_random
						while True:
							_name = '%s-%02d' % (_name_random.choice(_local_callsigns), _name_random.randint(10, 99))
							if _name not in _local_bot_names:
								_local_bot_names.add(_name)
								return _name

					class _FakeSpawnEvent(object):
						def __init__(self, key):
							self.key = key
						def isKeyDown(self):
							return True
						def isRepeatedEvent(self):
							return False
						def isShiftDown(self):
							return False
						def isCtrlDown(self):
							return False
						def isAltDown(self):
							return False

					def _anchors(t_id):
						pts = list(_spawns.get(t_id, []) or [])
						if not pts:
							# Fall back to the team base flag if the map has no spawn points
							for _b in (_bases.get(t_id, []) or []):
								pts.append((_b.x, _b.z))
						return pts

					def _face_yaw(t_id, x, z):
						# Line the team up facing the enemy base (like the real line-up)
						try:
							_eb = _bases.get(2 if t_id == 1 else 1, [])
							if _eb:
								return math.atan2(_eb[0].x - x, _eb[0].z - z)
						except Exception:
							pass
						return math.atan2(-x, -z)

					# Build one public matchmaking template before either team is filled.
					# Humans remove their closest exact slot; bots fill every remaining
					# slot, so both aggregate tier and vehicle-class distributions match.
					_balanced_bot_templates = {}
					try:
						import random as _match_random
						from items import vehicles as _match_vehicles
						import nations as _match_nations
						from gui.mods.offhangar.bot_ai import (build_match_template,
							choose_match_tiers, remaining_match_template,
							shared_human_requirements, vehicle_in_battle_tier_band,
							vehicle_match_class)
						_player_type = loaded_models['td'].type
						_player_profile = {
							'name': str(_player_type.name),
							'level': int(_player_type.level),
							'tags': _player_type.tags,
						}
						_human_profiles = {1: [], 2: []}
						for _human in (getattr(_pl, '_offhangar_network_roster', None) or []):
							try:
								_human_team = int(_human.get('team', 0) or 0)
								if _human_team not in (1, 2):
									continue
								_human_td = _match_vehicles.VehicleDescr(
									typeName=str(_human.get('vehicle')))
								_human_profiles[_human_team].append({
									'name': str(_human_td.type.name),
									'level': int(_human_td.type.level),
									'tags': _human_td.type.tags,
								})
							except Exception:
								pass
						if not _human_profiles[1] and not _human_profiles[2]:
							_human_profiles[_p_team].append(_player_profile)

						_band_candidates = []
						for _match_nation in _match_nations.AVAILABLE_NAMES:
							_match_nid = _match_nations.INDICES[_match_nation]
							for _match_vehicle in _match_vehicles.g_list.getList(_match_nid).itervalues():
								if (vehicle_in_battle_tier_band(_player_profile['level'],
										_match_vehicle['level']) and
										not _offh_veh_excluded(_match_vehicle)):
									_band_candidates.append(_match_vehicle)
						_available_tiers = sorted(set(int(_candidate['level'])
							for _candidate in _band_candidates))
						_match_tiers = list(choose_match_tiers(
							_player_profile['level'], _match_random.random(),
							_match_random.random(), _available_tiers))
						for _profiles in _human_profiles.values():
							for _profile in _profiles:
								if int(_profile['level']) not in _match_tiers:
									_match_tiers.append(int(_profile['level']))
						_match_tiers = tuple(sorted(set(_match_tiers)))
						_match_pool = list(_band_candidates)
						# A LAN player outside the authority's normal three-tier band is
						# still a legal selected vehicle. Make that profile available as a
						# compensating bot on the opposite team rather than hiding the skew.
						for _profiles in _human_profiles.values():
							for _profile in _profiles:
								if not any(int(_candidate.get('level', 0) or 0) == int(_profile['level']) and
										vehicle_match_class(_candidate) == vehicle_match_class(_profile)
										for _candidate in _match_pool):
									_match_pool.append(_profile)
						_requirements = shared_human_requirements(_human_profiles)
						_match_template = build_match_template(
							_match_pool, _n_per_team, _player_profile, _match_tiers,
							_match_random, _requirements)
						for _match_team in (1, 2):
							_balanced_bot_templates[_match_team] = remaining_match_template(
								_match_template, _human_profiles[_match_team])
						_tier_text = ','.join(str(_value) for _value in _match_tiers)
						_class_counts = {}
						for _candidate in _match_template:
							_class_tag = vehicle_match_class(_candidate)
							_class_counts[_class_tag] = _class_counts.get(_class_tag, 0) + 1
						LOG_DEBUG('MATCHMAKER: tiers=%s template=%s team1_bots=%d team2_bots=%d' % (
							_tier_text, repr(_class_counts),
							len(_balanced_bot_templates[1]),
							len(_balanced_bot_templates[2])))
					except Exception:
						import traceback
						LOG_DEBUG('MATCHMAKER template failed; using legacy lineup:',
							traceback.format_exc())

					_jobs = []
					for _t in (1, 2):
						_pts = _anchors(_t)
						if not _pts:
							LOG_DEBUG('AUTO-SPAWN: no spawn points for team', _t)
							continue
						# Reserve every LAN human slot before filling the remaining line-up
						# with local bots. Without this, player 3/4 shared slot 1 with a bot.
						_reserved_slots = set()
						try:
							if bool(_CFG.get('network_mode', False)):
								for _human in (getattr(_pl, '_offhangar_network_roster', None) or []):
									if int(_human.get('team', 0) or 0) == _t:
										_reserved_slots.add(int(_human.get('slot', 0) or 0))
						except Exception:
							_reserved_slots = set()
						if not _reserved_slots and _t == _p_team:
							_own_slot = int(getattr(_pl, '_offhangar_network_slot', 0) or 0) if bool(_CFG.get('network_mode', False)) else 0
							_reserved_slots.add(_own_slot)
						_count = max(0, _n_per_team - len(_reserved_slots))
						_bot_slots = []
						_next_slot = 0
						while len(_bot_slots) < _count:
							if _next_slot not in _reserved_slots:
								_bot_slots.append(_next_slot)
							_next_slot += 1
						# Pick the bots' vehicles up front and sort heavy -> arty so the
						# front rows hold the heavies and artillery sits at the back
						_veh_names = []
						try:
							import random as _rnd
							from items import vehicles as _veh_items
							import nations as _nations
							from gui.mods.offhangar.bot_ai import (vehicle_in_battle_tier_band,
								select_bot_lineup)
							_tier = loaded_models['td'].type.level
							_cand = []
							for _nat in _nations.AVAILABLE_NAMES:
								_nid = _nations.INDICES[_nat]
								for _v in _veh_items.g_list.getList(_nid).itervalues():
									if vehicle_in_battle_tier_band(_tier, _v['level']) and not _offh_veh_excluded(_v):
										_cand.append(_v)
							def _class_key(_v):
								try:
									_tg = _v['tags']
								except Exception:
									return 1
								if 'heavyTank' in _tg: return 0
								if 'mediumTank' in _tg: return 1
								if 'AT-SPG' in _tg: return 2
								if 'lightTank' in _tg: return 3
								if 'SPG' in _tg: return 4
								return 1
							_picked = list(_balanced_bot_templates.get(_t, ()) or ())
							if len(_picked) != _count and _cand:
								_pool = _cand
								# One artillery slot per team, including LAN humans. AT-SPG is
								# an exact, separate tank-destroyer tag and does not consume it.
								_human_spgs = 0
								_human_team_seen = False
								for _human in (getattr(_pl, '_offhangar_network_roster', None) or []):
									if int(_human.get('team', 0) or 0) != _t:
										continue
									_human_team_seen = True
									try:
										_human_td = _veh_items.VehicleDescr(
											typeName=str(_human.get('vehicle')))
										if 'SPG' in _human_td.type.tags:
											_human_spgs += 1
									except Exception:
										pass
								if not _human_team_seen and _t == _p_team:
									try:
										if 'SPG' in loaded_models['td'].type.tags:
											_human_spgs = 1
									except Exception:
										pass
								_picked = select_bot_lineup(
									_pool, _count, max(0, 1 - _human_spgs), _cand)
							if len(_picked) > _count:
								_picked = _picked[:_count]
							if _picked:
								_rnd.shuffle(_picked)
								_picked.sort(key=_class_key)
								_veh_names = [_p['name'] for _p in _picked]
						except Exception:
							import traceback
							LOG_DEBUG('AUTO-SPAWN vehicle pick failed:', traceback.format_exc())
						for _i in range(_count):
							_sx, _sy, _sz, _yw = _formation_pose(_t, _bot_slots[_i])
							_vn = _veh_names[_i] if _i < len(_veh_names) else None
							_identity = _bot_identity_by_slot.get((_t, _bot_slots[_i]), {})
							_bid = _identity.get('id')
							_jobs.append((_t, _bot_slots[_i], _bid, _sx, _sy, _sz, _yw, _vn, _bot_name(_t, _bot_slots[_i])))

					# The elected client chooses the exact lineup once. Every other client
					# waits for and recreates that manifest, so bot id, tank and slot match.
					try:
						from gui.mods.offhangar.network_battle import network_is_authority, publish_bot_manifest
						_is_network = bool(_CFG.get('network_mode', False))
						if _is_network and network_is_authority(_pl):
							_manifest_jobs = []
							from items import vehicles as _manifest_vehicles
							_manifest_descriptors = {}
							for _jt, _jslot, _jbid, _jx, _jy, _jz, _jyw, _jvn, _jname in _jobs:
								if _jbid is None or not _jvn:
									continue
								_jtd = _manifest_descriptors.get(_jvn)
								if _jtd is None:
									_jtd = _manifest_vehicles.VehicleDescr(typeName=_jvn)
									_manifest_descriptors[_jvn] = _jtd
								_manifest_jobs.append((_jbid, _jt, _jslot, _jvn, _jname,
									_jtd.maxHealth, _jx, _jy, _jz, _jyw))
							_pl._offh_vehicle_descriptors = _manifest_descriptors
							if not publish_bot_manifest(_pl, _manifest_jobs):
								_pending_manifest = getattr(
									_pl, '_offhangar_network_bot_manifest_pending', None)
								if (isinstance(_pending_manifest, dict) and
										_pending_manifest.get('state') == 'rejected'):
									LOG_ERROR('LAN canonical bot manifest rejected; '
										'spawn remains fail-closed')
									return
								_offh_battle_callback(0.25, _auto_spawn_teams)
								return
							_manifest = getattr(
								_pl, '_offhangar_network_bot_manifest', None) or []
							_jobs = []
							from gui.mods.offhangar.network_battle import (
								_world_from_server, _world_yaw_from_server)
							for _entry in _manifest:
								_jpoint = _world_from_server(_pl, _entry)
								_jobs.append((int(_entry.get('team', 0) or 0),
									int(_entry.get('slot', 0) or 0), int(_entry.get('id')),
									float(_jpoint.x), float(_jpoint.y), float(_jpoint.z),
									_world_yaw_from_server(_pl, _entry),
									str(_entry.get('vehicle')), str(_entry.get('name'))))
						elif _is_network:
							_manifest = getattr(_pl, '_offhangar_network_bot_manifest', None) or []
							if not _manifest:
								_offh_battle_callback(0.25, _auto_spawn_teams)
								return
							_jobs = []
							from gui.mods.offhangar.network_battle import (_world_from_server,
								_world_yaw_from_server)
							for _entry in _manifest:
								_jt = int(_entry.get('team', 0) or 0)
								_jslot = int(_entry.get('slot', 0) or 0)
								_jpoint = _world_from_server(_pl, _entry)
								_jyw = _world_yaw_from_server(_pl, _entry)
								_jobs.append((_jt, _jslot, int(_entry.get('id')),
									float(_jpoint.x), float(_jpoint.y), float(_jpoint.z), _jyw,
									str(_entry.get('vehicle')), str(_entry.get('name'))))
					except Exception:
						import traceback
						LOG_DEBUG('LAN bot manifest setup failed:', traceback.format_exc())

					# Parse the exact shared lineup and acquire collision resources first. Visual
					# loading is two-stage below: one deduplicated dependency warm-up, followed by
					# the same per-component fetchModel calls used by retail VehicleAppearance.
					# Keeping the fetched instances per spawn job means entity construction never
					# starts another native model request during the visible countdown.
					_pl._offh_lineup_prefetch_refs = None
					_pl._offh_lineup_prefetch_ready = False
					_pl._offh_lineup_prefetch_started_at = time.time()
					_pl._offh_lineup_prefetch_wait_logged = False
					_pl._offh_lineup_model_refs = {}
					_pl._offh_lineup_model_pending = {}
					_pl._offh_lineup_model_failed = {}
					_lineup_model_paths = []
					try:
						from items import vehicles as _lineup_vehicles
						_lineup_started = time.time()
						_lineup_descriptors = getattr(_pl, '_offh_vehicle_descriptors', None) or {}
						_hit_tester_count = 0
						_lineup_vehicle_names = [_lineup_job[7] for _lineup_job in _jobs]
						for _lineup_human in (getattr(_pl, '_offhangar_network_roster', None) or []):
							_lineup_vehicle_names.append(_lineup_human.get('vehicle'))
						for _lineup_vehicle in _lineup_vehicle_names:
							if not _lineup_vehicle:
								continue
							_lineup_vehicle = str(_lineup_vehicle)
							_lineup_td = _lineup_descriptors.get(_lineup_vehicle)
							if _lineup_td is None:
								_lineup_td = _lineup_vehicles.VehicleDescr(typeName=_lineup_vehicle)
								_lineup_descriptors[_lineup_vehicle] = _lineup_td
							_hit_tester_count += _offh_load_hit_testers(_lineup_td)
							for _lineup_path in _offh_vehicle_model_paths(_lineup_td):
								if _lineup_path not in _lineup_model_paths:
									_lineup_model_paths.append(_lineup_path)
						_pl._offh_vehicle_descriptors = _lineup_descriptors
						from gui.mods.offhangar.logging import LOG_NOTE as _LINEUP_NOTE
						_LINEUP_NOTE('LAN lineup prepared: bots=%d unique_types=%d hit_testers=%d elapsed_ms=%d' % (
							len(_jobs), len(_lineup_descriptors), _hit_tester_count,
							int((time.time() - _lineup_started) * 1000.0)))
					except Exception:
						import traceback
						LOG_DEBUG('LAN lineup preparation failed:', traceback.format_exc())

					# Interleave the teams so both sides build up evenly
					_t1 = [_j for _j in _jobs if _j[0] == 1]
					_t2 = [_j for _j in _jobs if _j[0] == 2]
					_jobs = []
					for _k in range(max(len(_t1), len(_t2))):
						if _k < len(_t1): _jobs.append(_t1[_k])
						if _k < len(_t2): _jobs.append(_t2[_k])
					try:
						_pl._offh_auto_spawn_expected = len(_jobs)
						_pl._offh_auto_spawn_completed = 0
					except Exception:
						pass

					# The authority's collision space is camera-streamed. On large maps a
					# remote spawn chunk can therefore be absent even though the loading
					# screen reports complete. Temporarily widen the engine load radius and
					# require a live narrow collision hit for every frozen canonical pose.
					# Replicas never own native bot movement and must not change projection.
					_spawn_stream_role = _offh_network_bot_role(_pl)
					if _spawn_stream_role in ('local', 'authority'):
						try:
							_previous_bootstrap = getattr(
								_pl, '_offh_spawn_streaming_bootstrap', None)
							if _previous_bootstrap is not None:
								_previous_bootstrap.stop()
							from gui.mods.offhangar.spawn_streaming_bootstrap import (
								SpawnStreamingBootstrap, coverage_target_from_bounds)
							_spawn_coverage_target = None
							_spawn_graph = globals().get('g_offh_baked_navigation_graph')
							if isinstance(_spawn_graph, dict):
								_spawn_coverage_target = coverage_target_from_bounds(
									_spawn_graph.get('bounds'))
							def _probe_canonical_spawn(_probe_job):
								_probe_hit = BigWorld.wg_collideSegment(
									_offh_bspace(),
									Math.Vector3(float(_probe_job[3]),
										float(_probe_job[4]) + 3.0, float(_probe_job[5])),
									Math.Vector3(float(_probe_job[3]),
										float(_probe_job[4]) - 3.0, float(_probe_job[5])), 128)
								return None if _probe_hit is None else float(_probe_hit[0].y)
							_pl._offh_spawn_streaming_bootstrap = SpawnStreamingBootstrap(
								BigWorld.projection(), _jobs,
								(veh_pos[0], veh_pos[1], veh_pos[2]),
								_probe_canonical_spawn, time.time(),
								max(30.0, _auto_spawn_delay + 30.0),
								coverage_target=_spawn_coverage_target)
							_pl._offh_spawn_streaming_monitor_active = False
							_pl._offh_spawn_streaming_wait_logged = 0.0
						except Exception:
							import traceback
							LOG_ERROR('Native spawn streaming bootstrap failed: %s' %
								traceback.format_exc())
							return
					elif _spawn_stream_role == 'replica':
						_pl._offh_spawn_streaming_bootstrap = None
					else:
						LOG_ERROR('Native spawn streaming ownership unavailable: role=%s' %
							str(_spawn_stream_role))
						return

					def _lineup_job_key(_job):
						return (int(_job[0]), int(_job[1]), _job[2])

					_lineup_fetch_started = [0.0]
					def _lineup_model_ready(_key, _refs):
						if globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]:
							return
						_pending = getattr(_pl, '_offh_lineup_model_pending', {})
						_failed_paths = [
							_path for _path, _model in (_refs or {}).iteritems()
							if _model is None]
						if _failed_paths:
							getattr(_pl, '_offh_lineup_model_failed', {})[_key] = _failed_paths
						else:
							getattr(_pl, '_offh_lineup_model_refs', {})[_key] = _refs
						_pending.pop(_key, None)
						if not _pending:
							_pl._offh_lineup_prefetch_ready = True
							# fetchModel has produced one independent component set for every
							# spawn job, so the dependency-only instances can be released now.
							_pl._offh_lineup_prefetch_refs = None
							try:
								from gui.mods.offhangar.logging import LOG_NOTE as _FETCH_READY_NOTE
								_FETCH_READY_NOTE(
									'LAN lineup model fetch ready: bots=%d failed=%d elapsed_ms=%d' % (
									len(getattr(_pl, '_offh_lineup_model_refs', {}) or {}),
									len(getattr(_pl, '_offh_lineup_model_failed', {}) or {}),
									int((time.time() - _lineup_fetch_started[0]) * 1000.0)))
							except Exception:
								pass

					def _start_lineup_model_fetches():
						if globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]:
							return
						_lineup_fetch_started[0] = time.time()
						_pending = {}
						for _fetch_job in _jobs:
							_pending[_lineup_job_key(_fetch_job)] = True
						_pl._offh_lineup_model_pending = _pending
						_submitted = 0
						for _fetch_job in _jobs:
							_fetch_key = _lineup_job_key(_fetch_job)
							try:
								_fetch_td = _pl._offh_vehicle_descriptors[str(_fetch_job[7])]
								_offh_fetch_vehicle_models(
									_fetch_td,
									lambda _refs, _key=_fetch_key: _lineup_model_ready(
										_key, _refs))
								_submitted += 1
							except Exception as _fetch_error:
								_pl._offh_lineup_model_failed[_fetch_key] = [str(_fetch_error)]
								_pending.pop(_fetch_key, None)
						if not _pending:
							_pl._offh_lineup_prefetch_ready = True
							_pl._offh_lineup_prefetch_refs = None
						try:
							from gui.mods.offhangar.logging import LOG_NOTE as _FETCH_SUBMIT_NOTE
							_FETCH_SUBMIT_NOTE(
								'LAN lineup native fetch submitted: bots=%d components=%d elapsed_ms=%d' % (
								_submitted, _submitted * 4,
								int((time.time() - _lineup_fetch_started[0]) * 1000.0)))
						except Exception:
							pass

					def _lineup_dependencies_ready(_resource_refs):
						if globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]:
							return
						# The returned PyModels only warm shared geometry/textures. fetchModel
						# acquires the independent instances below, so retaining this extra set
						# would waste precious address space in the 32-bit client.
						_pl._offh_lineup_prefetch_refs = None
						try:
							from gui.mods.offhangar.logging import LOG_NOTE as _WARM_NOTE
							_WARM_NOTE('LAN lineup dependencies warm: models=%d elapsed_ms=%d' % (
								len(_lineup_model_paths), int((time.time() - float(
									getattr(_pl, '_offh_lineup_prefetch_started_at', time.time()))) * 1000.0)))
						except Exception:
							pass
						_start_lineup_model_fetches()

					try:
						if _lineup_model_paths:
							BigWorld.loadResourceListBG(
								tuple(_lineup_model_paths), _lineup_dependencies_ready)
						else:
							_start_lineup_model_fetches()
					except Exception as _warm_error:
						LOG_DEBUG('LAN lineup dependency warm-up failed:', str(_warm_error))
						_start_lineup_model_fetches()

					def _poll_spawn_streaming_activation():
						if globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]:
							return
						_stream_player = BigWorld.player()
						_bootstrap = getattr(
							_stream_player, '_offh_spawn_streaming_bootstrap', None)
						if _bootstrap is None:
							_stream_player._offh_spawn_streaming_monitor_active = False
							return
						_active_count = 0
						try:
							from gui.mods.offhangar import native_bot_physics as _STREAM_NBP
							_stream_job_keys = set()
							for _stream_job in _bootstrap.jobs:
								_stream_job_id = _stream_job[2]
								if _stream_job_id is not None:
									_stream_job_id = int(_stream_job_id)
								_stream_job_keys.add((int(_stream_job[0]),
									int(_stream_job[1]), _stream_job_id))
							for _stream_mock in list((globals().get(
									'G_MOCK_VEHICLES', {}) or {}).values()):
								try:
									_stream_mock_team = getattr(
										_stream_mock, '_bot_team', None)
									_stream_mock_slot = getattr(
										_stream_mock, '_network_bot_slot', None)
									# G_MOCK_VEHICLES also contains the player proxy. Its
									# permissive __getattr__ returns None for bot identity;
									# it must not erase the count for every valid native bot.
									if (_stream_mock_team is None or
											_stream_mock_slot is None):
										continue
									_stream_mock_id = getattr(
										_stream_mock, '_network_bot_id', None)
									if _stream_mock_id is not None:
										_stream_mock_id = int(_stream_mock_id)
									_stream_mock_key = (int(_stream_mock_team),
										int(_stream_mock_slot), _stream_mock_id)
									if (_stream_mock_key in _stream_job_keys and
											_STREAM_NBP.is_active(_stream_mock)):
										_active_count += 1
								except Exception:
									continue
						except Exception:
							_active_count = 0
						try:
							_stream_phase = _bootstrap.poll(time.time(), _active_count)
						except Exception:
							_stream_phase = 'failed'
						if _stream_phase == 'placement_ready':
							_offh_battle_callback(0.10, _poll_spawn_streaming_activation)
							return
						_stream_player._offh_spawn_streaming_monitor_active = False
						if _stream_phase == 'complete':
							try:
								from gui.mods.offhangar.logging import LOG_NOTE as _STREAM_READY_NOTE
								_STREAM_READY_NOTE(
									'Native spawn streaming retained: active=%d/%d' % (
										_active_count, len(_bootstrap.jobs)))
							except Exception:
								pass
						elif _stream_phase == 'failed':
							LOG_ERROR('Native spawn streaming failed after placement: '
								'reason=%s active=%d/%d' % (
									str(getattr(_bootstrap, 'failure_reason', 'unknown')),
									_active_count, len(_bootstrap.jobs)))

					def _spawn_next(_rest):
						if globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0]:
							return
						if _battle_finished[0] or not _rest:
							return
						_spawn_bootstrap = getattr(
							BigWorld.player(), '_offh_spawn_streaming_bootstrap', None)
						if (_spawn_bootstrap is not None and
								getattr(_spawn_bootstrap, 'failure_reason', None) is not None):
							return
						_t, _slot, _bid, _x, _y, _z, _yw, _vn, _bn = _rest[0]
						try:
							_p2 = BigWorld.player()
							# Bot ground height. Native owners require an exact live collision hit
							# in the BATTLE space. A replica may seed presentation from the server's
							# canonical pose because it never treats that height as local physics.
							# Keep this slot clear of already-placed hulls: the grid spaces slots, but a
							# roof-corrected drop can still land on top of a neighbour.
							_taken = getattr(_p2, '_offh_spawn_taken', None)
							if _taken is None:
								_taken = []
								_p2._offh_spawn_taken = _taken
							for _nudge in range(4 if _y is None else 0):
								_clash = False
								for _tx, _tz in _taken:
									if (_x - _tx) ** 2 + (_z - _tz) ** 2 < 81.0:   # < 9 m apart
										_clash = True
										break
								if not _clash: break
								import math as _mnu
								# Nudge FORWARD (toward the enemy, i.e. into the map). Pushing backwards ran
								# straight at the arena edge behind the base and its buildings.
								_x += _mnu.sin(_yw) * 11.0
								_z += _mnu.cos(_yw) * 11.0
							# The prebaked graph identifies the drivable terrain layer at this X/Z.
							# Probe narrowly around that height to retain the collision surface without
							# ever selecting a roof above it or a cellar below it.
							_gy = None
							if _y is not None:
								if _offh_network_bot_role(_p2) == 'replica':
									# A replica never owns this pose as physics; the server's
									# canonical manifest height is only its presentation seed.
									_gy = float(_y)
								else:
									try:
										_gc = BigWorld.wg_collideSegment(
											_offh_bspace(), Math.Vector3(_x, float(_y) + 3.0, _z),
											Math.Vector3(_x, float(_y) - 3.0, _z), 128)
										if (_gc is not None and
												abs(float(_gc[0].y) - float(_y)) <= 0.35):
											_gy = float(_gc[0].y)
									except Exception:
										_gy = None
								if _gy is None:
									LOG_DEBUG('AUTO-SPAWN: canonical support obstructed team=%d slot=%d' % (
										int(_t), int(_slot)))
									_deferred = (_rest[1:] + [_rest[0]]) if len(_rest) > 1 else _rest
									_offh_battle_callback(0.25,
										lambda _waiting=_deferred: _spawn_next(_waiting))
									return
							try:
								from gui.mods.offhangar.prebaked_navigation import load_graph, nearest_ground_point
								_spawn_graph = globals().get('g_offh_baked_navigation_graph')
								if _spawn_graph is None:
									_spawn_graph = load_graph(globals().get('g_offh_battle_mapname', ''))
								_ground_hint = None if _y is not None else nearest_ground_point(_spawn_graph, _x, _z, 3)
								if _ground_hint is not None:
									_baked_y = float(_ground_hint[1])
									_gc = BigWorld.wg_collideSegment(
										_offh_bspace(), Math.Vector3(_x, _baked_y + 3.0, _z),
										Math.Vector3(_x, _baked_y - 3.0, _z), 128)
									# The graph supplies only a placement-height hint. Native staging
									# independently waits for a live static-collision hit before attach.
									_gy = _gc[0].y if _gc is not None else _baked_y
							except Exception:
								pass
							# Developer/custom maps may not ship a graph. Preserve the old collision
							# walk as a compatibility fallback, but stock maps never need to guess.
							if _gy is None:
								try:
									_from_y = 1000.0
									for _ri in range(4):
										_gc = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(_x, _from_y, _z), Math.Vector3(_x, -1000.0, _z), 128)
										if _gc is None: break
										_gy = _gc[0].y
										_gc2 = BigWorld.wg_collideSegment(_offh_bspace(), Math.Vector3(_x, _gy - 0.4, _z), Math.Vector3(_x, -1000.0, _z), 128)
										if _gc2 is None or (_gy - _gc2[0].y) < 2.5: break
										_from_y = _gy - 0.4
								except Exception:
									pass
							if _gy is None:
								try:
									_gy = float(veh_pos[1])
								except Exception:
									_gy = 100.0
							_taken.append((_x, _z))
							_p2._forced_spawn_pos = (_x, _gy, _z)
							_p2._forced_spawn_team = _t
							_p2._forced_spawn_yaw = _yw
							_p2._forced_spawn_vehname = _vn
							_p2._forced_spawn_name = _bn
							_p2._forced_spawn_bot_id = _bid
							_p2._forced_spawn_bot_slot = _slot
							_p2._offh_forced_model_refs = (getattr(
								_p2, '_offh_lineup_model_refs', {}) or {}).pop(
								(int(_t), int(_slot), _bid), None)
							try:
								_mock_handleKeyEvent(_FakeSpawnEvent(Keys.KEY_P))
							finally:
								_p2._forced_spawn_pos = None
								_p2._forced_spawn_team = None
								_p2._forced_spawn_yaw = None
								_p2._forced_spawn_vehname = None
								_p2._forced_spawn_name = None
								_p2._forced_spawn_bot_id = None
								_p2._forced_spawn_bot_slot = None
								_p2._offh_forced_model_refs = None
						except Exception as _se:
							LOG_DEBUG('AUTO-SPAWN error:', str(_se))
						# Models are already fetched. Build one entity per rendered frame so the
						# remaining 5-10 ms attachment/GUI work cannot form one large hitch.
						if len(_rest) > 1:
							_offh_battle_callback(0.0,
								lambda _remaining=_rest[1:]: _spawn_next(_remaining))
						else:
							try:
								_p2._offh_spawn_batch_submitted_at = time.time()
								from gui.mods.offhangar.logging import LOG_NOTE as _SUBMIT_NOTE
								_SUBMIT_NOTE('LAN bot entities assembled: bots=%d elapsed_ms=%d' % (
									int(getattr(_p2, '_offh_auto_spawn_expected', 0) or 0),
									int((_p2._offh_spawn_batch_submitted_at - float(getattr(_p2,
										'_offh_spawn_batch_started_at', _p2._offh_spawn_batch_submitted_at))) * 1000.0)))
							except Exception:
								pass

					def _begin_bot_placement(_prepared_jobs):
						if (globals().get('g_offh_battle_gen', 0) != _offh_my_gen[0] or
								_battle_finished[0]):
							return
						_prep_player = BigWorld.player()
						_streaming_bootstrap = getattr(
							_prep_player, '_offh_spawn_streaming_bootstrap', None)
						if _streaming_bootstrap is not None:
							try:
								_streaming_phase = _streaming_bootstrap.poll(
									time.time(), 0)
							except Exception:
								_streaming_phase = 'failed'
							if _streaming_phase == 'waiting_support':
								_now = time.time()
								_last_stream_log = float(getattr(
									_prep_player, '_offh_spawn_streaming_wait_logged', 0.0) or 0.0)
								if _now - _last_stream_log >= 2.0:
									_prep_player._offh_spawn_streaming_wait_logged = _now
									try:
										from gui.mods.offhangar.logging import LOG_NOTE as _STREAM_WAIT_NOTE
										_STREAM_WAIT_NOTE(
											'Native spawn streaming waiting for live support')
									except Exception:
										pass
								_offh_battle_callback(0.10,
									lambda _waiting_jobs=_prepared_jobs: _begin_bot_placement(
										_waiting_jobs))
								return
							if _streaming_phase == 'failed':
								LOG_ERROR('Native spawn streaming failed before placement: '
									'reason=%s' % str(getattr(
										_streaming_bootstrap, 'failure_reason', 'unknown')))
								return
						if not bool(getattr(_prep_player, '_offh_lineup_prefetch_ready', False)):
							_now = time.time()
							_last_wait_log = float(getattr(
								_prep_player, '_offh_lineup_prefetch_wait_logged', 0.0) or 0.0)
							if _now - _last_wait_log >= 2.0:
								_prep_player._offh_lineup_prefetch_wait_logged = _now
								try:
									from gui.mods.offhangar.logging import LOG_NOTE as _WAIT_NOTE
									_WAIT_NOTE('LAN lineup model fetch waiting: ready=%d/%d elapsed_ms=%d' % (
										len(getattr(_prep_player, '_offh_lineup_model_refs', {}) or {}),
										len(_prepared_jobs), int((_now - float(getattr(
											_prep_player, '_offh_lineup_prefetch_started_at', _now))) * 1000.0)))
								except Exception:
									pass
							_offh_battle_callback(0.10,
								lambda _waiting_jobs=_prepared_jobs: _begin_bot_placement(
									_waiting_jobs))
							return
						LOG_DEBUG('AUTO-SPAWN: placing %d bots (%d per team incl. player)' % (
							len(_prepared_jobs), _n_per_team))
						# Fresh occupancy list per battle - a stale one would nudge every new spawn.
						try: BigWorld.player()._offh_spawn_taken = []
						except Exception: pass
						try: BigWorld.player()._offh_spawn_batch_started_at = time.time()
						except Exception: pass
						_spawn_next(_prepared_jobs)
						if (_streaming_bootstrap is not None and not bool(getattr(
								_prep_player, '_offh_spawn_streaming_monitor_active', False))):
							_prep_player._offh_spawn_streaming_monitor_active = True
							_offh_battle_callback(0.10, _poll_spawn_streaming_activation)

					# Descriptor and collision-resource preparation runs immediately behind
					# the normal loading page. Native entities still wait for the original
					# terrain-streaming deadline and are then staged during the countdown.
					_place_delay = max(0.0, _spawn_not_before - time.time())
					if _place_delay > 0.01:
						LOG_DEBUG('AUTO-SPAWN: lineup ready; model placement starts in %.1fs' % _place_delay)
						_offh_battle_callback(_place_delay,
							lambda _prepared_jobs=list(_jobs): _begin_bot_placement(_prepared_jobs))
					else:
						_begin_bot_placement(_jobs)
				except Exception:
					import traceback
					LOG_DEBUG('AUTO-SPAWN failed:', traceback.format_exc())

			try:
				from _constants import CONFIG_OPTIONS as _CFG_AS
				if int(_CFG_AS.get('bots_per_team', 15)) > 0:
					# Choose and parse the exact lineup while the normal loading page is up.
					# _auto_spawn_teams keeps actual entity placement behind the configured
					# terrain-streaming delay.
					globals()['g_offh_auto_spawn_callback_id'] = BigWorld.callback(
						0.25, _auto_spawn_teams)
			except Exception:
				import traceback
				LOG_DEBUG('AUTO-SPAWN schedule failed:', traceback.format_exc())
			# -------------------------------------------

			player.shoot = _mock_shoot

			# --- Central kill handling: keep players-panel/minimap icons in sync ---
			# Every kill path fires arena.onVehicleKilled(...). The bare event does not
			# flip arena.vehicles[id]['isAlive'], so panel icons stayed 'alive'. Wrap it
			# once so ALL kill paths (shots, fire, ramming) update the UI consistently.
			try:
				if hasattr(player, 'arena') and player.arena is not None and not getattr(player.arena, '_offh_kill_wrapped', False):
					class _KillEventWrapper(object):
						def __init__(self, orig):
							self._orig = orig
						def __iadd__(self, handler):
							try:
								self._orig += handler
							except Exception:
								pass
							return self
						def __isub__(self, handler):
							try:
								self._orig -= handler
							except Exception:
								pass
							return self
						def __getattr__(self, name):
							return getattr(self._orig, name)
						def __call__(self, victimID, killerID=-1, reason=0):
							import BigWorld
							_pl = BigWorld.player()
							_mv = globals().get('G_MOCK_VEHICLES', {}).get(victimID)
							# One victim has one death transaction. Ammo-rack, hull-damage and
							# delayed LAN snapshots can all report the same terminal transition;
							# only the first report may credit a frag, notify retail UI or replace
							# the native-owned chassis.
							_settled = globals().setdefault('_offh_settled_deaths', set())
							_first_death = victimID not in _settled
							if _first_death:
								_settled.add(victimID)
							if _mv is not None:
								try:
									_mv._network_death_pending = False
									_mv._network_death_notified = True
								except Exception:
									pass
							if not _first_death:
								return
							try:
								if _pl is not None and victimID in getattr(_pl.arena, 'vehicles', {}):
									_pl.arena.vehicles[victimID]['isAlive'] = False
							except Exception:
								pass
							try:
								if _mv is not None:
									_offh_set_alive(_mv, False)
									if (getattr(_mv, 'health', None) or 0) > 0:
										# Drowning is not damage, so remember what it had before zeroing the
										# internal value that everything else treats as 'dead'.
										if getattr(_mv, '_drowned', False) and getattr(_mv, '_hp_display', None) is None:
											_mv._hp_display = _mv.health
										_mv.health = 0
									# Original behaviour (Vehicle.__onVehicleDeath): the marker is NOT
									# destroyed on death - it switches to the grey 'dead' state. Wrecks
									# are visible to everyone, so create the marker first if the victim
									# died unspotted. Central here for EVERY kill path.
									try:
										from gui import WindowsManager as _zwm
										_zbw = getattr(_zwm.g_windowsManager, 'battleWindow', None)
										_zvm = getattr(_zbw, 'vMarkersManager', None) if _zbw is not None else None
										if _zvm is not None:
											_zfresh = getattr(_mv, 'marker', None) in (None, -1)
											if _zfresh:
												try:
													_mv.marker = _zvm.createMarker(_mv.proxy)
												except Exception:
													_mv.marker = None
											if getattr(_mv, 'marker', None) not in (None, -1):
												try:
													# NOT a hard 0: a drowned hull still shows the HP it went under with.
													_zvm.onVehicleHealthChanged(_mv.marker, max(0, _offh_hp_display(_mv)), killerID, 0)
												except Exception:
													pass
												_zvm.updateMarkerState(_mv.marker, 'dead', not _zfresh)
									except Exception:
										pass
							except Exception:
								pass
							try:
								if self._orig is not None:
									self._orig(victimID, killerID, reason)
							except Exception:
								pass
							# Individual frags are derived exactly once from this same death
							# transaction. Assign the canonical count after the stock callback so
							# legacy handlers cannot increment it a second time.
							try:
								_vehicles = getattr(getattr(_pl, 'arena', None), 'vehicles', {}) or {}
								_victim_info = _vehicles.get(victimID) or {}
								_killer_info = _vehicles.get(killerID) or {}
								_valid_killer = (
									killerID is not None and killerID != victimID and
									killerID in _vehicles)
								if _valid_killer:
									_killer_team = _killer_info.get('team')
									_victim_team = _victim_info.get('team')
									_delta = (-1 if _killer_team in (1, 2) and
									          _killer_team == _victim_team else 1)
									_counts = globals().setdefault('_offh_canonical_frags', {})
									_counts[killerID] = int(_counts.get(killerID, 0)) + _delta
									_killer_info['frags'] = _counts[killerID]
									_statistics = getattr(_pl.arena, 'statistics', None)
									if isinstance(_statistics, dict):
										_stat = _statistics.setdefault(killerID, {})
										_stat['frags'] = _counts[killerID]
									if _delta < 0:
										_killer_info['isTeamKiller'] = True
										if killerID == getattr(_pl, 'playerVehicleID', -1):
											_pl.isTeamKiller = True
										try: _pl.arena.onTeamKiller(killerID)
										except Exception: pass
									try: _pl.arena.onVehicleStatisticsUpdate(killerID)
									except Exception: pass
							except Exception as _frag_error:
								LOG_DEBUG('Canonical frag update error:', str(_frag_error))
							# One canonical score path covers shells, fire, ramming, drowning and
							# LAN deaths. Repeat after the current event stack so the Flash panel
							# observes the final alive flags even when its stock handler updates late.
							try:
								_offh_refresh_team_score(_pl)
								_offh_battle_callback(0.0, lambda _score_player=_pl:
									_offh_refresh_team_score(_score_player))
							except Exception:
								pass
							# Kill feed, ONCE per victim. Four separate sites used to post this, one of
							# them twice in a row, all with the key 'PlayerKilled' - which the panel does
							# not define, so Flash fell back to a default icon (the ammo rack) and printed
							# the key as the text. The real keys and their %(...)s arguments come from
							# gui/player_messages_panel.xml and ingame_gui player_messages/*.
							try:
								_seen_k = globals().setdefault('_offh_kill_msgs', set())
								if victimID not in _seen_k:
									_seen_k.add(victimID)
									from gui import WindowsManager as _kwm
									_kbw = getattr(_kwm.g_windowsManager, 'battleWindow', None)
									_kp = getattr(_kbw, '_Battle__pMsgsPanel', None) if _kbw is not None else None
									_pvid_early = getattr(_pl, 'playerVehicleID', -1)
									# Retail posts NOTHING to this panel when the player himself is the
									# victim (Avatar.__onArenaVehicleKilled returns early) - the post-mortem
									# already tells you. Match it.
									if _kp is not None and victimID != _pvid_early:
										_av = getattr(_pl.arena, 'vehicles', {}) or {}
										_vinfo = _av.get(victimID) or {}
										_kinfo = _av.get(killerID) or {}
										_vname = _offh_vehicle_message_label(_pl, victimID)
										_kname = _offh_vehicle_message_label(_pl, killerID)
										_pteam_k = getattr(_pl, '_offhangar_team', 1)
										_vteam = _vinfo.get('team', _pteam_k)
										_kteam = _kinfo.get('team', None)
										_pvid_k = getattr(_pl, 'playerVehicleID', -1)
										if killerID is None or killerID < 0 or killerID == victimID or not _kinfo:
											# Drowned, burned out, fell, rammed a rock: nobody gets the frag.
											_key = 'ally_suicide' if _vteam == _pteam_k else 'enemy_suicide'
											_args = {'entity': _vname}
										else:
											_ff = (_kteam == _vteam)
											if killerID == _pvid_k:
												_key = 'player_friendly_fire_frag' if _ff else 'player_frag'
												_args = {'target': _vname}
											elif _kteam == _pteam_k:
												_key = 'ally_friendly_fire_frag' if _ff else 'ally_frag'
												_args = {'attacker': _kname, 'target': _vname}
											else:
												_key = 'enemy_friendly_fire_frag' if _ff else 'enemy_frag'
												_args = {'attacker': _kname, 'target': _vname}
										LOG_DEBUG('KILL FEED: key=%s args=%s victim=%s killer=%s' % (_key, _args, victimID, killerID))
										_kp.showMessage(_key, _args)
							except Exception as _kfe:
								LOG_DEBUG('kill feed error:', str(_kfe))
							# Grey out the players-panel icon + drop the minimap marker
							try:
								from gui import WindowsManager
								_bw = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
								if _bw is not None:
									try:
										if hasattr(_bw, '_Battle__updatePlayers'):
											_bw._Battle__updatePlayers()
									except Exception:
										pass
									try:
										if getattr(_bw, 'minimap', None):
											_bw.minimap.notifyVehicleStop(victimID)
									except Exception:
										pass
									try:
										pass  # marker health + 'dead' state already handled centrally above
									except Exception:
										pass
							except Exception:
								pass
							# The actual `_stop_dead_native_bot(_mv, False)` ownership proof is
							# performed by _fire_wreck_swap before it allocates any wreck model.
							# Fire deaths (reason 2) have no wreck-swap path of their own:
							# swap burnt-out bots to their destroyed models here
							try:
								# A drowned tank sank where it stood - it did not blow up. Swapping in the
								# crash models would reset the turret to its default bearing and level the
								# hull, throwing away exactly the last state that should be preserved.
								if (_mv is not None and _pl is not None and
										victimID != getattr(_pl, 'playerVehicleID', -1) and
										not getattr(_mv, '_wreck_done', False) and
										getattr(_mv, '_chassis_model', None) is not None):
									if getattr(_mv, '_drowned', False):
										def _drowned_native_release(_mv=_mv):
											if not _offh_wreck_release_or_retry(
													_mv, _drowned_native_release):
												return
											# Keep the last live chassis and its exact submerged pose, but
											# only after the native physics/filter/Servo graph is detached.
											_mv._wreck_done = True
										_drowned_native_release()
										return
									_dtd = getattr(_mv, 'typeDescriptor', None)
									if _dtd is not None:
										if getattr(_mv, '_network_remote', False) or getattr(_mv, '_network_shared_bot', False):
											_play_death_effect(_dtd, getattr(_mv, 'position', None), False,
												getattr(_mv, '_ammo_rack_death', False))
										_d_ch_path = _dtd.chassis['models']['destroyed']
										_d_hu_path = _dtd.hull['models']['destroyed']
										_d_tu_path = _dtd.turret['models']['destroyed']
										_d_gu_path = _dtd.gun['models']['destroyed']
										_old_ch = _mv._chassis_model
										_old_pos = _old_ch.position
										_old_yaw = _old_ch.yaw
										# pitch/roll as well: a wreck used to snap dead level on any slope
										try: _old_pitch = _old_ch.pitch
										except Exception: _old_pitch = 0.0
										try: _old_roll = _old_ch.roll
										except Exception: _old_roll = 0.0
										_fire_wreck_models = [None]
										def _fire_wreck_swap(_d_ch_path=_d_ch_path, _d_hu_path=_d_hu_path, _d_tu_path=_d_tu_path, _d_gu_path=_d_gu_path, _old_ch=_old_ch, _old_pos=_old_pos, _old_yaw=_old_yaw, _mv=_mv, _models=_fire_wreck_models):
											import BigWorld, Math
											if _models[0] is None:
												if not _offh_wreck_release_or_retry(
														_mv, _fire_wreck_swap):
													return
												if getattr(_mv, '_wreck_done', False):
													return
												_mv._wreck_done = True
												_models[0] = (
													BigWorld.Model(_d_ch_path),
													BigWorld.Model(_d_hu_path),
													BigWorld.Model(_d_tu_path),
													BigWorld.Model(_d_gu_path))
												_offh_battle_callback(0.1, _fire_wreck_swap)
												return
											_d_ch, _d_hu, _d_tu, _d_gu = _models[0]
											if not getattr(_d_ch, 'loaded', True) or not getattr(_d_hu, 'loaded', True) or not getattr(_d_tu, 'loaded', True) or not getattr(_d_gu, 'loaded', True):
												_offh_battle_callback(0.1, _fire_wreck_swap)
												return
											try: _old_ch.visible = False
											except Exception: pass
											try: _old_ch.visibleAttachments = False
											except Exception: pass
											try:
												if getattr(_mv, 'bw_entity', None) is not None:
													_mv.bw_entity.model = None  # chassis is entity-owned: delModel alone fails
											except Exception: pass
											try: BigWorld.delModel(_old_ch)
											except Exception: pass
											# Wreck must rest on the ground (mid-air kill would leave a floating
											# wreck). _wpos: NEVER rebind _old_pos - in the player-kill path this
											# code sits in a nested function where _old_pos is only a closure var;
											# assigning it made it local -> UnboundLocalError -> vanishing wrecks.
											_wpos = _old_pos
											try:
												import BigWorld as _bwx, Math as _mx
												_gw = _bwx.wg_collideSegment(_offh_bspace(), _mx.Vector3(_wpos.x, _wpos.y + 2.0, _wpos.z), _mx.Vector3(_wpos.x, _wpos.y - 500.0, _wpos.z), 128)
												if _gw is not None and _wpos.y > _gw[0].y + 0.5:
													_wpos = _mx.Vector3(_wpos.x, _gw[0].y, _wpos.z)
											except Exception:
												pass
											_d_ch.position = _wpos
											_d_ch.yaw = _old_yaw
											# Vehicle markers follow the mock, not the render model. Move
											# that proxy to the grounded wreck and replace its model root.
											try:
												_mv.position = Math.Vector3(_wpos)
												_mv.yaw = _old_yaw
												_mv.matrix.setRotateYPR((_old_yaw, _old_pitch, _old_roll))
												_mv.matrix.translation = _wpos
											except Exception:
												pass
											# Whole orientation in one go. Model.pitch/.roll assigned separately after
											# .yaw do NOT compose - each setter rebuilds the transform, which left the
											# wreck mis-oriented (turretless hulls like the Foch 155 worst of all).
											# A Servo on a prepared matrix is what the live chassis already uses.
											try:
												_wr_mat = Math.Matrix()
												_wr_mat.setRotateYPR((_old_yaw, _old_pitch, _old_roll))
												_wr_mat.translation = _wpos
												_d_ch.addMotor(BigWorld.Servo(_wr_mat))
												_mv._wreck_mat = _wr_mat   # hold a ref: a GC'd matrix drops the wreck
											except Exception as _wme:
												LOG_DEBUG('Wreck orientation failed:', str(_wme))
											try: _d_ch.node('V').attach(_d_hu)
											except Exception: pass
											try:
												# last aimed pose, like every other wreck path - identity snapped the turret forward,
												# which on TDs and arty twisted the casemate into an impossible default facing
												_tm = Math.Matrix(); _tm.setRotateYPR((float(getattr(_mv, '_turret_yaw', 0.0) or 0.0), 0, 0))
												_mv._wreck_t_mat = _tm   # hold a ref: a GC'd matrix drops the node back to identity
												_mv._d_t_node = _d_hu.node('HP_turretJoint', _tm)
												_mv._d_t_node.attach(_d_tu)
											except Exception: pass
											try:
												_gm = Math.Matrix(); _gm.setRotateYPR((0, float(getattr(_mv, '_gun_pitch', 0.0) or 0.0), 0))
												_mv._wreck_g_mat = _gm   # hold a ref: a GC'd matrix drops the node back to identity
												_mv._d_g_node = _d_tu.node('HP_gunJoint', _gm)
												_mv._d_g_node.attach(_d_gu)
											except Exception: pass
											try: _add_model(_d_ch)
											except Exception: pass
											try:
												_mv.model = _d_ch
												_mv._chassis_model = _d_ch
											except Exception:
												pass
										_fire_wreck_swap()
							except Exception:
								pass
					player.arena.onVehicleKilled = _KillEventWrapper(getattr(player.arena, 'onVehicleKilled', None))
					player.arena._offh_kill_wrapped = True
					LOG_DEBUG('OfflineBattle: kill-event wrapper installed')
					LOG_DEBUG('OfflineBattle BUILD %s' % _OFFH_BUILD)
			except Exception:
				import traceback
				LOG_DEBUG('OfflineBattle: kill wrapper failed:', traceback.format_exc())

			from Account import Account
			# shoot is a per-battle closure and is already installed on the player
			# instance above. Never put it on the persistent Account class: doing so
			# pins the first battle's gun state, mocks and models for the process.
			if not hasattr(Account, 'autoAim'):
				Account.autoAim = lambda self, targetID: None
			if not hasattr(Account, 'isGuiVisible'):
				Account.isGuiVisible = True

			if hasattr(player, 'arena'):
				if player.arena.vehicles:
					player.playerVehicleID = player.arena.vehicles.keys()[0]

			Waiting.close()

			# ---- ZVUK: okamžitě zastavit garážové audio, spustit loading hudbu ----
			try:
				import MusicController as _MC


				_mc = _MC.g_musicController
				try:
					import SoundGroups as _SG
					if getattr(_SG, 'g_instance', None) is not None:
						# Preserve the user's sliders exactly as retail Avatar startup does.
						_SG.g_instance.applyPreferences()
				except Exception: pass

				# Stop the hangar events through the controller, which also clears its
				# event ids and pending result callback. Directly stopping the private
				# FMOD handles first duplicated work and left stale controller state.
				_mc.stop()

				# The stock resolver rejects the synthetic Account because it is not a
				# PlayerAvatar. Keep the stock event ids and replace only that type gate.
				def _mock_mc_getArenaSoundEvent(self, eventId):
					from debug_utils import LOG_DEBUG
					import BigWorld
					player = BigWorld.player()
					if hasattr(player, 'arena') and hasattr(player.arena, 'arenaType'):
						sound_name = ''
						if eventId == _MC.MUSIC_EVENT_COMBAT:
							# Do NOT return None on a repeat call. MusicController.play does
							#     if prevSoundEvent == soundEvent: return
							#     if prevSoundEvent is not None: prevSoundEvent.stop()
							#     if soundEvent is not None: soundEvent.play()
							# so a None answer STOPS whatever is playing and starts nothing. The old
							# one-shot guard here did exactly that: the loading track was cut the
							# moment the bots finished loading and no combat music ever followed.
							# WG already prevents a restart through that equality check - it just
							# needs the SAME object back every time, hence the cache below.
							sound_name = getattr(player.arena.arenaType, 'music', '')
						elif eventId == _MC.MUSIC_EVENT_COMBAT_LOADING:
							sound_name = getattr(player.arena.arenaType, 'loadingMusic', '')
						elif eventId == _MC.AMBIENT_EVENT_COMBAT:
							sound_name = getattr(player.arena.arenaType, 'ambientSound', '')
						LOG_DEBUG('OfflineBattle.mock_getArenaSoundEvent DIRECT', eventId, sound_name)
						if sound_name:
							import FMOD
							# Cache per event id: FMOD.getSound hands back a NEW object each call,
							# and play()'s 'same event -> do nothing' check compares objects. Without
							# the cache every repeat call would restart the track from the top.
							_cache = globals().setdefault('g_offh_arena_snd', {})
							_snd = _cache.get(eventId)
							if _snd is None:
								_snd = FMOD.getSound(sound_name)
								_cache[eventId] = _snd
							return _snd
					return _MC.MusicController._MusicController__getArenaSoundEvent(self, eventId)

				import types
				_mc._MusicController__getArenaSoundEvent = types.MethodType(_mock_mc_getArenaSoundEvent, _mc)

				# Match Avatar startup: loading music first, then subscribe to the arena.
				globals()['g_offh_combat_music_done'] = False
				# New battle: drop the cached sound objects, the arena (and its tracks) changed.
				globals()['g_offh_arena_snd'] = {}
				_mc.play(_MC.MUSIC_EVENT_COMBAT_LOADING)
				# Match Avatar.__onInitStepCompleted: subscribe once so BATTLE starts
				# both the arena combat event and its map ambience. The synthetic player
				# is an Account, so the private sound resolver above supplies the same
				# arena events that MusicController normally gets from PlayerAvatar.
				if not getattr(_mc, '_offh_arena_lifecycle', False):
					_mc.onEnterArena()
					_mc._offh_arena_lifecycle = True
					LOG_DEBUG('OfflineBattle.music: original arena lifecycle active')
				LOG_DEBUG('OfflineBattle.sounds.battle_start', 'COMBAT_LOADING OK')
			except Exception as _se:
				LOG_DEBUG('OfflineBattle.sounds.battle_start error', _se)
			# ---- konec zvuk ----

			WindowsManager.g_windowsManager.startBattle()
			WindowsManager.g_windowsManager.showBattleLoading()

			if hasattr(player, 'arena'):
				if hasattr(player.arena, 'onVehicleAdded'):
					for vID in player.arena.vehicles.keys():
						player.arena.onVehicleAdded(vID)

				def _finish_battle_load():
					try:
						try:
							import SoundGroups as _SG
							if getattr(_SG, 'g_instance', None) is not None:
								_SG.g_instance.enableLobbySounds(False)
								_SG.g_instance.enableArenaSounds(True)
							# Do not start MUSIC_EVENT_COMBAT here. Retail starts it from
							# MusicController.__onArenaStateChanged when period becomes BATTLE;
							# starting it during model/UI completion cut the countdown intro early.
						except Exception as e: pass

						Waiting.close()
						WindowsManager.g_windowsManager.showBattle()
						BigWorld.worldDrawEnabled(True)

						import AvatarInputHandler.cameras
						AvatarInputHandler.cameras.SniperCamera._USE_SWINGING = False
						BigWorld.wg_isSniperModeSwingingEnabled = lambda *a, **kw: False

						if not hasattr(BigWorld, '_orig_serverTime'):
							BigWorld._orig_serverTime = BigWorld.serverTime
							BigWorld._offline_start_time = __import__('time').time()
							def _mock_serverTime():
								return __import__('time').time() - BigWorld._offline_start_time
							BigWorld.serverTime = _mock_serverTime

						def _do():
							try:
								from gui import WindowsManager
								from account_helpers.AccountSettings import AccountSettings
								_orig_getSettings = AccountSettings.getSettings
								# Unwrap first: re-wrapping every battle chained a new closure
								# over the previous one (leak + ever-longer call path).
								if getattr(_orig_getSettings, '_offh_wrapped', False):
									_orig_getSettings = _orig_getSettings._offh_orig
								def _mock_getSettings(name, *a, **kw):
									res = _orig_getSettings(name, *a, **kw)
									if name == 'sniper' or name == 'arcade':
										if res is None: res = {}
										if isinstance(res, dict):
											defaults = {
												'snpCentralTag': {'alpha': 100, 'type': 0},
												'snpNet': {'alpha': 100, 'type': 0},
												'snpReloader': {'alpha': 100, 'type': 0},
												'snpCondition': {'alpha': 100, 'type': 0},
												'snpCassette': {'alpha': 100, 'type': 0},
												'snpGunTag': {'alpha': 100, 'type': 0},
												'snpMixing': {'alpha': 100, 'type': 0},
												'centralTag': {'alpha': 100, 'type': 0},
												'net': {'alpha': 100, 'type': 0},
												'reloader': {'alpha': 100, 'type': 0},
												'condition': {'alpha': 100, 'type': 0},
												'cassette': {'alpha': 100, 'type': 0},
												'gunTag': {'alpha': 100, 'type': 0},
												'mixing': {'alpha': 100, 'type': 0}
											}
											for k, v in defaults.items():
												if k not in res:
													res[k] = v
									return res
								_mock_getSettings._offh_wrapped = True
								_mock_getSettings._offh_orig = _orig_getSettings
								AccountSettings.getSettings = staticmethod(_mock_getSettings)

								if hasattr(player.arena, 'onPeriodChange'):
									_battle_duration = 900

									# LAN clients join the one server countdown at whatever value remains
									# after their own loading time. Offline mode keeps the configured local
									# countdown. Movement/shooting remain gated by period < 3.
									from _constants import CONFIG_OPTIONS as _CFG_PB
									_pb_len = float(_CFG_PB.get('prebattle_countdown_seconds', 30.0))
									_server_deadline = getattr(player, '_offhangar_network_combat_deadline', None)
									if _network_mode_enabled() and _server_deadline is not None:
										_pb_len = max(0.0, float(_server_deadline) - time.time())
										from gui.mods.offhangar.logging import LOG_NOTE as _TIMING_NOTE
										_TIMING_NOTE('LAN server countdown joined with %.1f second(s) remaining' % _pb_len)
									if _pb_len > 0.05:
										player.arena.period = 2
										player.arena.periodLength = _pb_len
										player.arena.periodEndTime = BigWorld.serverTime() + _pb_len
										player.arena.onPeriodChange(2, player.arena.periodEndTime, _pb_len, {})
									else:
										_battle_duration = _offh_server_battle_remaining(player, _battle_duration)
										player.arena.period = 3
										player.arena.periodLength = _battle_duration
										player.arena.periodEndTime = BigWorld.serverTime() + _battle_duration
										player.arena.onPeriodChange(3, player.arena.periodEndTime, _battle_duration, {})
								if hasattr(player.arena, 'onNewVehicleListReceived'):
									player.arena.onNewVehicleListReceived()
								if hasattr(player.arena, 'onVehicleAdded'):
									for vID in player.arena.vehicles.keys():
										player.arena.onVehicleAdded(vID)
								if hasattr(player.arena, 'onVehicleStatisticsUpdate'):
									for vID in player.arena.vehicles.keys():
										player.arena.onVehicleStatisticsUpdate(vID)
								if hasattr(WindowsManager.g_windowsManager.battleWindow, '_Battle__populateData'):
									WindowsManager.g_windowsManager.battleWindow._Battle__populateData()

								if not getattr(player, '_crosshair_init_done', False):
									player._crosshair_init_done = True
									# Remember the hangar FOV before any sniper zoom touches it, so the sweep
									# can put it back (see the snipercam stage).
									try:
										if globals().get('g_offh_base_fov') is None:
											globals()['g_offh_base_fov'] = BigWorld.projection().fov
									except Exception: pass
									try:
										import AvatarInputHandler.aims
										try:
											AvatarInputHandler.aims.clearState()
											hs = AvatarInputHandler.aims._g_aimState.get('health')
											if hs is not None:
												hs['cur'] = getattr(player.vehicleTypeDescriptor, 'maxHealth', 400)
												hs['max'] = getattr(player.vehicleTypeDescriptor, 'maxHealth', 400)
										except Exception as e:
											LOG_DEBUG('OfflineBattle aims init error:', str(e))

										# Mock the startup to avoid crashing on missing Vehicle entity
										g_offline_aih._AvatarInputHandler__isStarted = True
										g_offline_aih._AvatarInputHandler__isGUIVisible = True
										g_offline_aih._AvatarInputHandler__isArenaStarted = True
										for control in g_offline_aih._AvatarInputHandler__ctrls.itervalues():
											try:
												control.create()
												_offh_center_arcade_aim(control)
											except Exception as e: LOG_DEBUG('Control create error:', e)

											# Pre-warm the gunMarker state so dumpState() doesn't throw KeyError: 'startTime'
											try: control.setReloading(0.0, 0.0)
											except Exception as e: LOG_DEBUG('CRITICAL ERROR IN K KEY:', e); import traceback; LOG_DEBUG(traceback.format_exc())

										try:
											g_offline_aih._AvatarInputHandler__isSPG = 'SPG' in td.type.tags
										except Exception:
											g_offline_aih._AvatarInputHandler__isSPG = False

										g_offline_aih.onControlModeChanged('arcade')
										g_offline_aih.setGUIVisible(True)
										if hasattr(g_offline_aih, 'ctrl'):
											pass
										try:
											import AvatarInputHandler.aims as aims
											if getattr(aims, '_g_aimState', None) is not None:
												aims._g_aimState['reload'] = {'isReloading': False, 'duration': 0.0, 'startTime': None, 'correction': None}
										except Exception:
											pass
										g_offline_aih.ctrl.showGunMarker(True)
										g_offline_aih.ctrl.showGunMarker2(True)
										_force_camera_to_model()
										LOG_DEBUG('OfflineBattle AIH enable SUCCESS')
									except Exception as e:
										import traceback
										LOG_DEBUG('OfflineBattle AIH enable ERROR:', traceback.format_exc())
							except Exception:
								import traceback
								LOG_DEBUG('Do error:', traceback.format_exc())
							return

						_offh_battle_callback(0.1, _do)

					except Exception:
						LOG_CURRENT_EXCEPTION()
				from _constants import CONFIG_OPTIONS
				loading_time = float(CONFIG_OPTIONS.get('loading_screen_time_seconds', 5.0))
				_offh_battle_callback(loading_time, _finish_battle_load)

		except Exception:
			LOG_CURRENT_EXCEPTION()
			WindowsManager.g_windowsManager.hideAll()
		LOG_DEBUG('OfflineBattle.camera started')
	except Exception:
		LOG_CURRENT_EXCEPTION()
	player._offline_allow_become_non_player = False
	LOG_DEBUG('OfflineBattle.spawnAvatar.done', cmdName)
	return

def _network_mode_enabled():
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		return bool(CONFIG_OPTIONS.get('network_mode', False))
	except Exception:
		return False


def _offh_server_battle_remaining(player, fallback=900.0):
	"""Use the server's projected end deadline when LAN timing is available."""
	try:
		if _network_mode_enabled():
			deadline = getattr(player, '_offhangar_network_combat_end_deadline', None)
			if deadline is not None:
				return max(0.1, float(deadline) - time.time())
	except Exception:
		pass
	return max(0.1, float(fallback))


def _show_waiting_queue(player):
	queueType = _queue_type_randoms()
	onEnqueued = getattr(player, 'onEnqueued', None)
	if callable(onEnqueued):
		onEnqueued(queueType)
	else:
		onEnqueuedRandom = getattr(player, 'onEnqueuedRandom', None)
		if callable(onEnqueuedRandom):
			onEnqueuedRandom()
	if hasattr(player, 'isInRandomQueue'):
		player.isInRandomQueue = True


def _join_network_waiting_room(player, vehInvID, cmdName):
	"""Connect now, but never start a local arena before battle_start."""
	try:
		_enable_offline_battle_transition(player)
		player._offhangar_network_fallback_local = False
		player._offhangar_network_map_name = None
		player._offhangar_network_team = 1
		player._offhangar_network_pending_veh_id = vehInvID
		player._offhangar_network_pending_cmd = cmdName
		from gui.mods.offhangar.network_battle import start_for_player
		client = start_for_player(player)
		if client is None:
			raise RuntimeError('LAN client was not created')
		from gui.mods.offhangar.logging import LOG_NOTE
		LOG_NOTE('LAN connection requested; queue screen waits for server welcome')
		return True
	except Exception:
		from gui.mods.offhangar.logging import LOG_ERROR
		LOG_ERROR('LAN waiting room connection setup failed')
		LOG_CURRENT_EXCEPTION()
		return False


def show_network_waiting_queue_from_server(player):
	"""Show Prebattle only after the LAN server accepted this client."""
	if player is None or getattr(player, '_offhangar_queue_cancelled', False):
		return False
	if not getattr(player, 'isInRandomQueue', False):
		_show_waiting_queue(player)
	from gui.mods.offhangar.logging import LOG_NOTE
	LOG_NOTE('LAN JOIN confirmed; queue screen is now server-backed')
	return True


def _step_on_enqueued(player, vehInvID, cmdName):
	try:
		_enable_offline_battle_transition(player)
		_net_client = None
		if _network_mode_enabled():
			_net_client = getattr(player, '_offhangar_network_client', None)
			if _net_client is None or not _net_client.ready or _net_client.phase != 'battle':
				from gui.mods.offhangar.logging import LOG_ERROR
				LOG_ERROR('LAN arena start blocked: battle_start was not received')
				return False
			player._offhangar_network_team = _net_client.team
			player._offhangar_network_map_name = _net_client.map_name
			try:
				from gui.mods.offhangar.lan_settings import close as _close_lan_settings
				_close_lan_settings()
			except Exception:
				pass
		ctx = build_offline_battle_context(player, vehInvID, cmdName)
		player._offhangar_battle_ctx = ctx
		player._offhangar_player_vehicle_id = ctx.get('playerVehicleID', vehInvID)
		player._offhangar_team = getattr(player, '_offhangar_network_team', 1) if _net_client is not None else 1
		arena = getattr(player, '_offhangar_arena', None)
		if arena is not None:
			arena.vehicles = ctx.get('vehicles', {})
			arena.guiType = 0
			arena.bonusType = 0
			arena.extraData = {'mapName': ctx.get('mapName'), 'mapID': ctx.get('mapID')}
			arena.period = 1
			arena.periodLength = 600
			arena.periodEndTime = BigWorld.serverTime() + 600
			map_name = ctx.get('mapName', '') or ''
			map_id = ctx.get('mapID', 0) or 0
			gameplay = 'ctf'
			real_arena_type = _resolve_real_arena_type(map_id, map_name, gameplay)
			if real_arena_type is not None:
				arena.arenaType = real_arena_type
				arena.arenaTypeID = map_id
				try:
					import ArenaType
					if hasattr(ArenaType, 'g_cache') and isinstance(ArenaType.g_cache, dict):
						for k, v in ArenaType.g_cache.iteritems():
							if v is real_arena_type:
								arena.arenaTypeID = k
								break
				except Exception: pass
				LOG_DEBUG('OfflineBattle.arenaType.real', map_name, 'arenaTypeID', arena.arenaTypeID, 'geomName', getattr(real_arena_type, 'geometryName', ''), 'minimap', hasattr(real_arena_type, 'minimap'))
			elif getattr(arena, 'arenaType', None) is not None:
				# Fallback: keep stub, but ensure required attrs exist.
				arena.arenaTypeID = map_id
				arena.arenaType.geometryName = map_name
				arena.arenaType.gameplayName = gameplay
				if not hasattr(arena.arenaType, 'minimap'):
					arena.arenaType.minimap = None
				LOG_DEBUG('OfflineBattle.arenaType.stub', map_name)
		queueType = _queue_type_randoms()
		LOG_DEBUG('OfflineBattle.onEnqueued', cmdName, 'queueType', queueType, 'vehInvID', vehInvID)
		onEnqueued = getattr(player, 'onEnqueued', None)
		if callable(onEnqueued):
			onEnqueued(queueType)
		else:
			onEnqueuedRandom = getattr(player, 'onEnqueuedRandom', None)
			if callable(onEnqueuedRandom):
				onEnqueuedRandom()
		if hasattr(player, 'isInRandomQueue'):
			player.isInRandomQueue = True
		return True
	except Exception:
		LOG_CURRENT_EXCEPTION()
		return False


def start_network_battle_from_server(player, map_name, team):
	"""Only the server's battle_start message may call this LAN transition."""
	if player is None or getattr(player, '_offhangar_queue_cancelled', False):
		return False
	if getattr(player, '_offhangar_network_arena_starting', False):
		return True
	player._offhangar_network_arena_starting = True
	player._offhangar_network_map_name = str(map_name)
	player._offhangar_network_team = int(team or 1)
	vehInvID = getattr(player, '_offhangar_network_pending_veh_id', 0) or _resolve_vehicle_inv_id(player, 0)
	cmdName = getattr(player, '_offhangar_network_pending_cmd', 'lan.battle_start')
	if not vehInvID or not _step_on_enqueued(player, vehInvID, cmdName):
		player._offhangar_network_arena_starting = False
		return False
	from gui.mods.offhangar.logging import LOG_NOTE
	LOG_NOTE('LAN entering server battle map=%s team=%s' % (map_name, team))
	BigWorld.callback(0.05, lambda: _step_on_arena_created(BigWorld.player(), cmdName))
	return True


def _step_on_arena_created(player, cmdName):
	try:
		if player is None:
			return
		if getattr(player, '_offhangar_arena_created_once', False):
			LOG_DEBUG('OfflineBattle.onArenaCreated skip duplicate', cmdName)
			return
		player._offhangar_arena_created_once = True
		LOG_DEBUG('OfflineBattle.onArenaCreated', cmdName)
		onArenaCreated = getattr(player, 'onArenaCreated', None)
		if callable(onArenaCreated):
			onArenaCreated()
		BigWorld.callback(0.05, lambda: _try_spawn_battle_avatar_stub(BigWorld.player(), cmdName))
	except Exception:
		LOG_CURRENT_EXCEPTION()


def _schedule_arena_created_resilient(cmdName, player, queue_generation):
	def _fire():
		if queue_generation != getattr(player, '_offhangar_queue_generation', 0):
			LOG_DEBUG('OfflineBattle.arenaCreated skipped: stale queue', cmdName)
			return
		# Cancel button pressed while the fake matchmaker was counting down
		# (Account.dequeueRandom sets this). Without the check the battle booted
		# anyway a few seconds after the player had left the queue.
		if getattr(player, '_offhangar_queue_cancelled', False):
			LOG_DEBUG('OfflineBattle.arenaCreated skipped: queue was cancelled')
			return
		if not getattr(player, '_offhangar_arena_created_once', False):
			_step_on_arena_created(player, cmdName)

	from _constants import CONFIG_OPTIONS
	queue_time = float(CONFIG_OPTIONS.get('queue_wait_time_seconds', 4.0))

	import BigWorld
	BigWorld.callback(queue_time, _fire)
	BigWorld.callback(queue_time + 0.03, _fire)
	BigWorld.callback(queue_time + 0.10, _fire)


def begin_offline_battle_queue(player, vehInvID, cmdName, cmd=0, args=()):
	"""Start one local or LAN queue flow; all enqueue paths use this gate."""
	if not OFFLINE_BATTLE_ENABLED:
		LOG_DEBUG('OfflineBattle.disabled queue', cmdName, cmd, args)
		return False
	if player is None or not getattr(player, 'isOffline', False):
		return False
	if not vehInvID:
		vehInvID = _resolve_vehicle_inv_id(player, 0)
	if not vehInvID:
		LOG_DEBUG('OfflineBattle.skip no vehInvID', cmdName, cmd, args)
		return False
	try:
		from gui import WindowsManager as _WMg
		if getattr(_WMg.g_windowsManager, 'battleWindow', None) is not None:
			LOG_DEBUG('OfflineBattle.queue skip active battle window', cmdName)
			return False
	except Exception:
		pass
	try:
		input_handler = getattr(player, 'inputHandler', None)
		if input_handler is not None and hasattr(input_handler, 'ctrls'):
			LOG_DEBUG('OfflineBattle.queue skip active battle input', cmdName)
			return False
	except Exception:
		pass
	now = time.time()
	if now - getattr(player, '_offhangar_battle_last_boot', 0.0) < _BATTLE_BOOT_DEBOUNCE_SEC:
		LOG_DEBUG('OfflineBattle.queue debounce skip', cmdName, vehInvID)
		return False
	player._offhangar_battle_last_boot = now
	if getattr(player, '_offhangar_queue_pending', False):
		LOG_DEBUG('OfflineBattle.queue skip duplicate', cmdName, vehInvID)
		return False
	player._offhangar_queue_pending = True
	player._offhangar_queue_cancelled = False
	player._offhangar_arena_created_once = False
	queue_generation = getattr(player, '_offhangar_queue_generation', 0) + 1
	player._offhangar_queue_generation = queue_generation

	def _run():
		current = BigWorld.player()
		if current is not player or queue_generation != getattr(player, '_offhangar_queue_generation', 0):
			return
		player._offhangar_queue_pending = False
		if getattr(player, '_offhangar_queue_cancelled', False):
			return
		if _network_mode_enabled():
			_join_network_waiting_room(player, vehInvID, cmdName)
		else:
			_step_on_enqueued(player, vehInvID, cmdName)
			_schedule_arena_created_resilient(cmdName, player, queue_generation)

	# Run after onCmdResponse so native queue state is visible before transition.
	BigWorld.callback(0.05, _run)
	return True


def schedule_random_battle_flow_after_enqueue(cmd, cmdName, args):
	"""Compatibility adapter for command-router enqueue handlers."""
	int1 = args[0] if args else 0
	# One numeric command id aliases stats in some 0.8.x indexes.
	if cmdName and ('SERVER_STATS' in cmdName or 'REQ_SERVER_STATS' in cmdName):
		if int1 == 0 and (len(args) < 2 or args[1] == 0) and (len(args) < 3 or args[2] == 0):
			LOG_DEBUG('OfflineBattle.skip stats-shaped packet', cmdName, cmd, args)
			return False
	return begin_offline_battle_queue(BigWorld.player(), int1, cmdName, cmd, args)


def start_offline_random_from_hangar(player, vehInvID):
	return begin_offline_battle_queue(player, vehInvID, 'offline.enqueueRandom')

try:
	import gui.Scaleform.battledispatcherinterface as bdi
	if hasattr(bdi, 'BattleDispatcherInterface'):
		orig_updateFightButton = bdi.BattleDispatcherInterface.updateFightButton
		def _new_updateFightButton(self):
			orig_updateFightButton(self)

			fightTypes = getattr(self, '_offhangar_fightTypes_temp', None)
			if fightTypes is None:
				# In case we can't capture it easily, we just call self.call again!
				pass

		# Better approach: monkey-patch self.call in BattleDispatcherInterface
		orig_call = bdi.BattleDispatcherInterface.call
		def _new_call(self, methodName, args=None):
			from gui.mods.offhangar.logging import LOG_DEBUG
			LOG_DEBUG("FLASH CALL:", methodName, args)
			if methodName == 'common.setFightButton' and isinstance(args, list):
				args.append('Bootcamp')
				args.append('tutorial')
				args.append(False)
				args.append('')
			return orig_call(self, methodName, args)
		bdi.BattleDispatcherInterface.call = _new_call

		orig_onFightButtonClick = bdi.BattleDispatcherInterface.onFightButtonClick
		def _new_onFightButtonClick(self, callbackId, mapId=None, queueType=0, confirm=False):
			import BigWorld
			p = BigWorld.player()
			from gui.mods.offhangar.logging import LOG_DEBUG
			LOG_DEBUG("FIGHT BUTTON CLICKED", "mapId:", mapId, "type:", type(mapId), "queueType:", queueType, "type:", type(queueType))

			if queueType == 'tutorial':
				if hasattr(p, 'enqueueTutorial'):
					p.enqueueTutorial()
				return

			if queueType == 'demonstrator':
				if mapId is not None:
					setattr(p, '_offhangar_selected_mapId', mapId)
				if hasattr(self, 'respond'):
					try: self.respond(callbackId, True)
					except: pass
				start_offline_random_from_hangar(p, 0)
				return

			# If it's a regular random battle, ensure we clear any demonstrator map override!
			if hasattr(p, '_offhangar_selected_mapId'):
				delattr(p, '_offhangar_selected_mapId')

			return orig_onFightButtonClick(self, callbackId, mapId, queueType, confirm)
		bdi.BattleDispatcherInterface.onFightButtonClick = _new_onFightButtonClick
except Exception:
	import traceback
	LOG_DEBUG('Failed to hook UI')
	LOG_DEBUG(traceback.format_exc())
