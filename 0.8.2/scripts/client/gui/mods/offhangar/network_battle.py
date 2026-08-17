# -*- coding: utf-8 -*-
"""LAN transport for the offhangar network MVP.

The 0.8.2 client embeds Python 2, so this module intentionally uses only
Python-2-compatible syntax and the standard library.  Socket I/O happens on a
worker thread; BigWorld objects are touched only from the main-thread poll
callback.  The original garage and offline battle path remain untouched until
``network_mode`` is enabled.
"""

import json
import math
import socket
import threading
import time

import BigWorld

from gui.mods.offhangar.logging import LOG_DEBUG, LOG_ERROR, LOG_NOTE
from gui.mods.offhangar import vehicle_pose


PROTOCOL_VERSION = 8
CLIENT_BUILD = '1.8.59-native-experimental-20260815'
POLL_INTERVAL = 1.0 / 60.0
INPUT_INTERVAL = 1.0 / 30.0
BOT_STATE_INTERVAL = 1.0 / 30.0
PING_INTERVAL = 1.0
MAX_MESSAGE_BYTES = 256 * 1024

try:
	_TEXT_TYPES = (basestring,)
except NameError:
	_TEXT_TYPES = (str,)


def _system_message(message, level='information'):
	"""Send a visible stock lower-right notification from the game thread."""
	try:
		from gui.SystemMessages import SM_TYPE, pushMessage
		if level == 'error':
			message_type = SM_TYPE.Error
		elif level == 'warning':
			message_type = SM_TYPE.Warning
		else:
			message_type = SM_TYPE.Information
		text = str(message)
		try:
			text = text.encode('utf-8')
		except Exception:
			pass
		pushMessage(text, message_type)
		return True
	except Exception:
		return False


class _NetworkSpawnEvent(object):
	"""Small BigWorld input-event stand-in for the existing P-key spawn path."""

	def __init__(self):
		import Keys
		self.key = Keys.KEY_P

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


def _finite_float(value, fallback=0.0):
	try:
		value = float(value)
	except (TypeError, ValueError):
		return float(fallback)
	try:
		if math.isnan(value) or math.isinf(value):
			return float(fallback)
	except Exception:
		pass
	return value


_MOBILITY_DEVICE_NAMES = (
	'engineHealth', 'leftTrackHealth', 'rightTrackHealth')


def _mobility_report(mock, now=None):
	"""Return disabled state and bounded repair time for authority handoff."""
	if now is None:
		now = time.time()
	destroyed = set(getattr(mock, '_destroyed_devices', None) or ())
	disabled_names = set(name for name in _MOBILITY_DEVICE_NAMES
		if name in destroyed)
	if getattr(mock, 'is_engine_dead', False):
		disabled_names.add('engineHealth')
	if getattr(mock, 'is_tracked', False) and not disabled_names.intersection(
			('leftTrackHealth', 'rightTrackHealth')):
		disabled_names.add('leftTrackHealth')
	devices_hp = getattr(mock, 'devices_hp', None) or {}
	local_disabled = bool(disabled_names)
	remaining = 0.0
	if local_disabled:
		try:
			from gui.mods.offhangar import device_damage
			descriptor = getattr(mock, 'typeDescriptor', None)
			for name in disabled_names:
				cap = device_damage.device_regen_hp(descriptor, name)
				if cap is None or float(cap) <= 0.0:
					continue
				hp = max(0.0, min(float(cap), _finite_float(
					devices_hp.get(name), 0.0)))
				seconds = device_damage.repair_seconds(
					name, descriptor, repair_skill_pct=100.0)
				remaining = max(remaining,
					float(seconds) * (1.0 - hp / float(cap)))
		except Exception:
			remaining = 0.0
		if remaining <= 0.0:
			remaining = 9.0 if 'engineHealth' in disabled_names else 5.0
	carry_until = _finite_float(
		getattr(mock, '_network_mobility_carry_until', 0.0), 0.0)
	carry_remaining = max(0.0, carry_until - float(now))
	if carry_until > 0.0 and carry_remaining <= 0.0:
		mock._network_mobility_carry_until = 0.0
		mock._network_mobility_disabled = False
		mock._network_mobility_repair_seconds = 0.0
	remaining = max(remaining, carry_remaining)
	return bool(local_disabled or carry_remaining > 0.0), max(
		0.0, min(30.0, remaining))


def _apply_mobility_snapshot(mock, state, force_authority_pose=False,
		now=None):
	"""Carry canonical immobilisation across a promoted-authority handoff."""
	if now is None:
		now = time.time()
	disabled = bool(state.get('mobility_disabled', False))
	remaining = max(0.0, min(30.0, _finite_float(
		state.get('mobility_repair_seconds'), 0.0)))
	if not force_authority_pose:
		mock._network_mobility_disabled = disabled
		mock._network_mobility_repair_seconds = remaining
		return disabled
	existing = _finite_float(
		getattr(mock, '_network_mobility_carry_until', 0.0), 0.0)
	# A full handoff can repeat while another bot's model is still loading. Seed
	# this bot's repair deadline only once per authority tenure, even if that
	# deadline expires before the complete lineup is ready.
	if bool(getattr(mock, '_network_mobility_handoff_seeded', False)):
		carry_remaining = max(0.0, existing - float(now))
		mock._network_mobility_disabled = carry_remaining > 0.0
		mock._network_mobility_repair_seconds = carry_remaining
		return carry_remaining > 0.0
	mock._network_mobility_handoff_seeded = True
	mock._network_mobility_disabled = disabled
	mock._network_mobility_repair_seconds = remaining
	if not disabled:
		mock._network_mobility_carry_until = 0.0
		return False
	if remaining <= 0.0:
		remaining = 9.0
	candidate = float(now) + remaining
	mock._network_mobility_carry_until = candidate
	return True


def _reset_mobility_handoff_carry():
	"""Clear one authority tenure's conservative mobility carry state."""
	try:
		for mock in (_offline_mocks() or {}).values():
			for name in ('_network_mobility_handoff_seeded',
					'_network_mobility_carry_until',
					'_network_mobility_disabled',
					'_network_mobility_repair_seconds'):
				try:
					delattr(mock, name)
				except Exception:
					pass
	except Exception:
		pass


def _network_perf_clock():
	try:
		return time.clock()
	except Exception:
		return time.time()


def _safe_text(value, limit=80):
	try:
		if not isinstance(value, _TEXT_TYPES):
			value = str(value)
		return value[:limit]
	except Exception:
		return ''


def _safe_position(value):
	try:
		if isinstance(value, dict):
			return (_finite_float(value.get('x')), _finite_float(value.get('y')),
					_finite_float(value.get('z')))
		if isinstance(value, (tuple, list)) and len(value) >= 3:
			return (_finite_float(value[0]), _finite_float(value[1]),
					_finite_float(value[2]))
	except Exception:
		pass
	return None


def _protocol_bool(value, default=False):
	if value is True or (value == 1 and not isinstance(value, _TEXT_TYPES)):
		return True
	if value is False or (value == 0 and not isinstance(value, _TEXT_TYPES)):
		return False
	return bool(default) if value is None else False


def _protocol_position(value):
	if isinstance(value, dict):
		if not all(key in value for key in ('x', 'y', 'z')):
			return None
		values = (value.get('x'), value.get('y'), value.get('z'))
	elif isinstance(value, (tuple, list)) and len(value) >= 3:
		values = (value[0], value[1], value[2])
	else:
		return None
	result = []
	for item in values:
		try:
			number = float(item)
		except (TypeError, ValueError):
			return None
		try:
			if math.isnan(number) or math.isinf(number):
				return None
		except Exception:
			pass
		result.append(number)
	return tuple(result)


class LANClient(object):
	def __init__(self, player, host, port, name, vehicle, max_health=1000):
		self.player = player
		self.host = str(host or '127.0.0.1')
		self.port = int(port or 28782)
		self.name = str(name or 'Player')
		self.vehicle = str(vehicle or 'ussr:MS-1')
		self.max_health = max(1, int(max_health or 1000))
		self.sock = None
		self.thread = None
		self.running = False
		self.connected = False
		self.ready = False
		self.player_id = None
		self.team = None
		self.slot = 0
		self.map_name = None
		self.available_maps = []
		self.spawn = None
		self.phase = 'connecting'
		self.round_id = None
		self.waiting_count = 0
		self.start_requested = False
		self.battle_started = False
		self.combat_deadline = None
		self.combat_end_deadline = None
		self.combat_duration = 900.0
		self._send_lock = threading.Lock()
		# Wire encoding and sendall used to run on BigWorld's render thread.
		# A full 30-bot snapshot can take several milliseconds to JSON-encode and
		# occasionally much longer when the socket back-pressures, directly turning
		# keyboard polling into a 10 FPS affair on the authority client.  Keep a
		# tiny ordered/coalescing queue here and let a dedicated sender own that work.
		self._outbound_lock = threading.Lock()
		self._outbound_event = threading.Event()
		self._outbound_reliable = []
		self._outbound_latest = {}
		self._outbound_seq = 0
		self._sender_thread = None
		self._pending_lock = threading.Lock()
		self._pending = []
		self._recv_buffer = ''
		self._last_input = 0.0
		self._last_bot_state = 0.0
		self._last_bot_observation = 0.0
		self._fire_seq = 0
		self._ping_seq = 0
		self._last_ping = 0.0
		self._last_receive = 0.0
		self._last_snapshot = 0.0
		self.rtt_ms = None
		self._diag_window_start = time.time()
		self._diag_chunks = 0
		self._diag_messages = 0
		self._diag_snapshots = 0
		self._diag_bot_updates = 0
		self._diag_last_chunk = 0.0
		self._diag_last_snapshot = 0.0
		self._diag_last_bot_update = 0.0
		self._diag_last_bot_revision = -1
		self._diag_max_socket_gap = 0.0
		self._diag_max_snapshot_gap = 0.0
		self._diag_max_bot_update_gap = 0.0
		self._diag_max_queue_age = 0.0
		self._diag_max_pending = 0
		self._diag_poll_seconds = 0.0
		self._diag_poll_calls = 0
		self._diag_snapshot_apply_seconds = 0.0
		self._diag_snapshot_apply_calls = 0
		self.bot_authority_id = None
		self.bot_order_revision = 0
		self.bot_orders = {}
		self._last_order_resync = 0.0
		self._manifest_nonce_seq = 0
		self._last_error = None
		self._error_notified = False
		self._stop_requested = False
		self._poll_scheduled = False

	def start(self):
		if self.running:
			return True
		self.running = True
		self.thread = threading.Thread(target=self._worker, name='offhangar-lan-client')
		self.thread.setDaemon(True)
		self.thread.start()
		self._schedule_poll()
		return True

	def _worker(self):
		try:
			LOG_NOTE('LAN connecting to %s:%s' % (self.host, self.port))
			sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			sock.settimeout(3.0)
			sock.connect((self.host, self.port))
			LOG_NOTE('LAN TCP connected to %s:%s' % (self.host, self.port))
			try:
				sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
			except Exception:
				pass
			sock.settimeout(0.5)
			self.sock = sock
			# The server requires hello to be the first wire message.  Do not expose
			# the socket to the main-thread poller before that message is sent: its
			# initial ping could otherwise win the race and be rejected as a protocol
			# mismatch.
			hello = {
				'type': 'hello',
				'protocol': PROTOCOL_VERSION,
				'client_build': CLIENT_BUILD,
				'name': self.name,
				'vehicle': self.vehicle,
				'max_health': self.max_health,
			}
			payload = (json.dumps(hello, separators=(',', ':')) + '\n').encode('utf-8')
			with self._send_lock:
				sock.sendall(payload)
			self.connected = True
			self._sender_thread = threading.Thread(
				target=self._sender_worker, name='offhangar-lan-sender')
			self._sender_thread.setDaemon(True)
			self._sender_thread.start()
			LOG_NOTE('LAN hello sent (protocol %s, build %s)' % (
				PROTOCOL_VERSION, CLIENT_BUILD))
			while self.running:
				try:
					chunk = sock.recv(8192)
				except socket.timeout:
					continue
				if not chunk:
					if self.connected and not self._stop_requested:
						self._last_error = 'server closed the connection'
					break
				received_time = time.time()
				self._last_receive = received_time
				with self._pending_lock:
					self._diag_chunks += 1
					previous_receive = self._diag_last_chunk
					if previous_receive > 0.0:
						self._diag_max_socket_gap = max(
							self._diag_max_socket_gap,
							received_time - previous_receive)
					self._diag_last_chunk = received_time
				try:
					self._recv_buffer += chunk.decode('utf-8')
				except UnicodeError:
					continue
				if len(self._recv_buffer) > 512 * 1024:
					self._last_error = 'server message buffer exceeded limit'
					break
				while '\n' in self._recv_buffer:
					line, self._recv_buffer = self._recv_buffer.split('\n', 1)
					if not line:
						continue
					try:
						message = json.loads(line)
					except (TypeError, ValueError):
						continue
					if isinstance(message, dict):
						# This private timestamp lets RTT and diagnostics distinguish actual
						# socket delay from a busy BigWorld game thread draining the queue late.
						message['_client_received_time'] = received_time
					with self._pending_lock:
						self._pending.append(message)
						self._diag_messages += 1
						if isinstance(message, dict) and message.get('type') == 'snapshot':
							if self._diag_last_snapshot > 0.0:
								self._diag_max_snapshot_gap = max(
									self._diag_max_snapshot_gap,
									received_time - self._diag_last_snapshot)
							self._diag_last_snapshot = received_time
							self._diag_snapshots += 1
							try:
								bot_revision = int(message.get('bot_state_revision', -1))
							except (TypeError, ValueError):
								bot_revision = -1
							if (bot_revision >= 0 and
									bot_revision != self._diag_last_bot_revision):
								if self._diag_last_bot_update > 0.0:
									self._diag_max_bot_update_gap = max(
										self._diag_max_bot_update_gap,
										received_time - self._diag_last_bot_update)
								self._diag_last_bot_update = received_time
								self._diag_last_bot_revision = bot_revision
								self._diag_bot_updates += 1
						self._diag_max_pending = max(
							self._diag_max_pending, len(self._pending))
		except Exception as error:
			if not self._stop_requested:
				self._last_error = str(error)
				LOG_ERROR('LAN connection failed to %s:%s: %s' % (self.host, self.port, self._last_error))
		finally:
			self.connected = False
			self.running = False
			self._outbound_event.set()
			try:
				if self.sock is not None:
					self.sock.close()
			except Exception:
				pass

	def _send_wire(self, message):
		"""Encode and write one message from the sender thread."""
		if not self.connected or self.sock is None:
			return False
		try:
			payload = (json.dumps(message, separators=(',', ':')) + '\n').encode('utf-8')
			if len(payload) > MAX_MESSAGE_BYTES:
				self._last_error = 'client message exceeded wire limit'
				LOG_ERROR('LAN outbound message dropped: type=%s bytes=%d limit=%d' % (
					str(message.get('type', '?')), len(payload), MAX_MESSAGE_BYTES))
				return None
			with self._send_lock:
				self.sock.sendall(payload)
			return True
		except Exception as error:
			self._last_error = str(error)
			return False

	def _dequeue_outbound(self):
		"""Pop the oldest logical message while preserving coalesced ordering."""
		with self._outbound_lock:
			reliable = self._outbound_reliable[0] if self._outbound_reliable else None
			latest_key = None
			latest = None
			for key, item in self._outbound_latest.items():
				if latest is None or item[0] < latest[0]:
					latest_key, latest = key, item
			if reliable is None and latest is None:
				return None
			if latest is None or (reliable is not None and reliable[0] <= latest[0]):
				return self._outbound_reliable.pop(0)[1]
			del self._outbound_latest[latest_key]
			return latest[1]

	def _sender_worker(self):
		while self.running or self._outbound_reliable or self._outbound_latest:
			message = self._dequeue_outbound()
			if message is None:
				self._outbound_event.clear()
				# Close the enqueue/clear race: an enqueue between dequeue and clear
				# must wake us instead of sleeping until the next unrelated packet.
				with self._outbound_lock:
					pending = bool(self._outbound_reliable or self._outbound_latest)
				if pending:
					self._outbound_event.set()
					continue
				self._outbound_event.wait(0.1)
				continue
			if self._send_wire(message) is False:
				if not self._stop_requested:
					self.running = False
				break

	def _send(self, message, coalesce_key=None):
		"""Queue a message without blocking BigWorld's render/input thread.

		Level-triggered input, bot-state and observation packets replace an older
		unsent packet of the same kind.  Sequence numbers keep that replacement in
		the right order relative to reliable fire/hit/control messages.
		"""
		if not self.connected or self.sock is None:
			return False
		try:
			# Preserve the old synchronous rejection for obviously impossible text
			# messages without serializing every 30 Hz state packet on BigWorld's
			# render thread. Production packet builders bound all nested collections.
			for value in message.values():
				if isinstance(value, _TEXT_TYPES) and len(value) >= MAX_MESSAGE_BYTES:
					return False
			with self._outbound_lock:
				self._outbound_seq += 1
				item = (self._outbound_seq, message)
				if coalesce_key is None:
					self._outbound_reliable.append(item)
				else:
					self._outbound_latest[coalesce_key] = item
			self._outbound_event.set()
			return True
		except Exception as error:
			self._last_error = str(error)
			return False

	def send_input(self, forward, turn, aim_yaw, gun_pitch, position=None, yaw=None,
			reported_health=None):
		now = time.time()
		if now - self._last_input < INPUT_INTERVAL:
			return
		self._last_input = now
		message = {
			'type': 'input',
			'forward': max(-1.0, min(1.0, _finite_float(forward))),
			'turn': max(-1.0, min(1.0, _finite_float(turn))),
			'aim_yaw': _finite_float(aim_yaw),
			'gun_pitch': _finite_float(gun_pitch),
			'fire_seq': self._fire_seq,
		}
		if position is not None:
			message['x'] = _finite_float(position[0])
			message['y'] = _finite_float(position[1])
			message['z'] = _finite_float(position[2])
			message['yaw'] = _finite_float(yaw)
		if reported_health is not None:
			message['reported_health'] = max(0, int(reported_health))
		self._send(message, 'input')

	def send_fire(self, shell_index=0, position=None, yaw=None, aim_yaw=None,
			gun_pitch=None):
		self._fire_seq += 1
		message = {
			'type': 'input',
			'fire_seq': self._fire_seq,
			'shell_index': max(0, min(int(shell_index or 0), 9)),
		}
		if position is not None:
			message['x'] = _finite_float(position[0])
			message['y'] = _finite_float(position[1])
			message['z'] = _finite_float(position[2])
			message['yaw'] = _finite_float(yaw)
		if aim_yaw is not None:
			message['aim_yaw'] = _finite_float(aim_yaw)
		if gun_pitch is not None:
			message['gun_pitch'] = _finite_float(gun_pitch)
		if not self._send(message):
			return None
		return self._fire_seq

	def send_hit(self, target_id, shot_seq, damage, shot_result, shell_index,
			impact_position=None, critical=False):
		message = {
			'type': 'hit_report',
			'target': int(target_id),
			'shot_seq': int(shot_seq),
			'damage': max(0, int(damage or 0)),
			'shot_result': max(0, min(int(shot_result or 0), 2)),
			'shell_index': max(0, min(int(shell_index or 0), 9)),
			'critical': bool(critical),
		}
		if impact_position is not None:
			message['x'] = _finite_float(impact_position[0])
			message['y'] = _finite_float(impact_position[1])
			message['z'] = _finite_float(impact_position[2])
		return self._send(message)

	def send_bot_manifest(self, bots, map_frame=None, manifest_nonce=None,
			round_id=None):
		message = {
			'type': 'bot_manifest', 'bots': bots[:30],
			'manifest_nonce': str(manifest_nonce or ''),
			'round_id': round_id,
		}
		if map_frame is not None:
			message['map_frame'] = map_frame
		return self._send(message)

	def send_bot_states(self, bots):
		now = time.time()
		if now - self._last_bot_state < BOT_STATE_INTERVAL:
			return False
		self._last_bot_state = now
		return self._send({'type': 'bot_state', 'bots': bots[:30]}, 'bot_state')

	def bot_states_due(self):
		"""Return whether building the next authoritative snapshot is useful."""
		return time.time() - self._last_bot_state >= BOT_STATE_INTERVAL

	def send_bot_observation(self, contacts, affordances=None, navigation=None):
		now = time.time()
		if now - self._last_bot_observation < 0.45:
			return False
		message = {
			'type': 'bot_observation',
			'contacts': contacts[:64],
			'affordances': (affordances or ())[:16],
		}
		if navigation is not None:
			message['navigation'] = navigation
		sent = self._send(message)
		if sent:
			self._last_bot_observation = now
		return sent

	def bot_observation_due(self):
		"""Avoid rebuilding a full telemetry payload while its send is throttled."""
		return time.time() - self._last_bot_observation >= 0.45

	def request_bot_orders(self):
		"""Rate-limited application-level recovery for a missing bot order."""
		now = time.time()
		if now - self._last_order_resync < 0.5:
			return False
		self._last_order_resync = now
		return self._send({
			'type': 'bot_order_resync',
			'revision': int(self.bot_order_revision or 0),
			'loaded': len(self.bot_orders or {}),
		})

	def send_bot_hit(self, target_id, shot_seq, damage, shot_result,
			impact_position=None, critical=False):
		message = {
			'type': 'bot_hit_report',
			'target': int(target_id),
			'shot_seq': int(shot_seq),
			'damage': max(0, int(damage or 0)),
			'shot_result': max(0, min(int(shot_result or 0), 2)),
			'critical': bool(critical),
		}
		if impact_position is not None:
			message['x'] = _finite_float(impact_position[0])
			message['y'] = _finite_float(impact_position[1])
			message['z'] = _finite_float(impact_position[2])
		return self._send(message)

	def send_bot_human_hit(self, bot_id, target_id, shot_seq, damage,
			shot_result, impact_position=None, critical=False):
		message = {
			'type': 'bot_human_hit',
			'attacker_bot': int(bot_id),
			'target': int(target_id),
			'shot_seq': int(shot_seq or 0),
			'damage': max(0, int(damage or 0)),
			'shot_result': max(0, min(int(shot_result or 0), 2)),
			'critical': bool(critical),
		}
		if impact_position is not None:
			message['x'] = _finite_float(impact_position[0])
			message['y'] = _finite_float(impact_position[1])
			message['z'] = _finite_float(impact_position[2])
		return self._send(message)

	def send_rules(self, rules):
		return self._send({'type': 'rules_state', 'rules': rules})

	def send_battle_result(self, winner, reason, base_team=0):
		return self._send({
			'type': 'battle_result',
			'winner': int(winner),
			'reason': str(reason or 'battle finished'),
			'base_team': int(base_team or 0),
		})

	def _set_authority(self, authority_id):
		try:
			authority_id = int(authority_id) if authority_id is not None else None
		except (TypeError, ValueError):
			authority_id = None
		previous_authority_id = self.bot_authority_id
		demotion_pending = bool(getattr(
			self.player,
			'_offhangar_network_authority_demotion_pending', False))
		was_authority = bool(getattr(
			self.player, '_offhangar_network_is_authority', False)) or bool(
				demotion_pending and previous_authority_id == self.player_id)
		will_be_authority = (
			authority_id is not None and authority_id == self.player_id)
		if was_authority and not will_be_authority:
			# A demotion snapshot is applied in this same message. Release the local
			# native filter/physics owner before that snapshot writes relay pose.
			# Mark the transition first: a partial release must not leave this process
			# publishing or rebuilding the bots which were already stopped.
			self.player._offhangar_network_authority_demotion_pending = True
			self.player._offhangar_network_is_authority = False
			try:
				from gui.mods.offhangar.offline_battle import release_native_bots_for_replica
				if not release_native_bots_for_replica(self.player):
					LOG_ERROR('LAN native authority demotion could not release bot bodies')
					return False
			except Exception:
				LOG_ERROR('LAN native authority demotion failed before snapshot apply')
				return False
			self.player._offhangar_network_authority_demotion_pending = False
			# Drop relay interpolation targets which were sampled before this client
			# became authority. Otherwise the frame between the authority event and its
			# first canonical replica snapshot can smooth the lineup backwards once.
			try:
				import sys
				offline = sys.modules.get('gui.mods.offhangar.offline_battle')
				mocks = getattr(offline, 'G_MOCK_VEHICLES', {}) if offline else {}
				for mock in (mocks or {}).values():
					if getattr(mock, '_network_shared_bot', False):
						for name in (
								'_network_target_position', '_network_target_velocity',
								'_network_target_time', '_network_smoothing_ready'):
							try:
								delattr(mock, name)
							except Exception:
								pass
			except Exception:
				pass
		changed = authority_id != previous_authority_id
		if changed:
			_reset_mobility_handoff_carry()
			self.player._offhangar_network_bot_manifest_pending = None
		self.bot_authority_id = authority_id
		self.player._offhangar_network_authority_id = authority_id
		self.player._offhangar_network_is_authority = will_be_authority
		self.player._offhangar_network_authority_demotion_pending = False
		if self.player._offhangar_network_is_authority:
			if changed:
				if (previous_authority_id is not None and
						bool(getattr(
							self.player, '_offhangar_network_bot_manifest', None))):
					# The next canonical snapshot must be applied once before this client
					# begins simulating. Otherwise a relay promotes its interpolated pose
					# and zero local speed instead of the server's final authority state.
					self.player._offhangar_network_authority_handoff_pending = True
				else:
					# The initial authority owns the only canonical state; there is no
					# relay pose to hand off before it starts publishing. The same is true
					# when a replacement is elected before any manifest was published.
					self.player._offhangar_network_authority_handoff_pending = False
			# An unchanged authority id must preserve a pending handoff. Only a
			# successfully applied complete snapshot may open that ownership fence.
		elif not self.player._offhangar_network_is_authority:
			self.player._offhangar_network_authority_handoff_pending = False
		if changed and self.phase == 'battle':
			role = 'simulation authority' if self.player._offhangar_network_is_authority else 'relay client'
			LOG_NOTE('LAN bot authority=%s; local role=%s' % (authority_id, role))
			_system_message('LAN battle authority: player %s (%s).' % (authority_id, role))
		return True

	def _load_bot_orders(self, message):
		"""Apply a complete revision body and acknowledge game-thread delivery."""
		if not isinstance(message, dict) or 'bot_orders' not in message:
			return False
		try:
			revision = int(message.get('bot_order_revision', 0) or 0)
		except (TypeError, ValueError):
			return False
		if revision < self.bot_order_revision:
			return False
		if (revision > self.bot_order_revision or
				(revision == 0 and not self.bot_orders)):
			orders = {}
			for order in message.get('bot_orders') or ():
				try:
					orders[int(order.get('id'))] = order
				except Exception:
					continue
			self.bot_order_revision = revision
			self.bot_orders = orders
			self.player._offhangar_network_bot_order_revision = revision
			self.player._offhangar_network_bot_orders = orders
		self._send({'type': 'bot_order_ack', 'revision': revision})
		return True

	def request_start(self, map_name=None):
		if not self.ready or self.phase != 'waiting':
			return False
		self.start_requested = True
		message = {'type': 'start_battle'}
		if map_name:
			message['map'] = str(map_name)
		if not self._send(message):
			LOG_ERROR('LAN could not send battle start request')
			return False
		LOG_NOTE('LAN battle start requested map=%s; waiting for server broadcast' % (
			str(map_name or self.map_name)))
		return True

	def stop(self):
		self._stop_requested = True
		# Best effort, synchronously, before stopping the sender and closing the
		# socket.  Enqueuing after running=False can legitimately lose the leave.
		try:
			self._send_wire({'type': 'leave'})
		except Exception:
			pass
		self.running = False
		self._outbound_event.set()
		try:
			if self.sock is not None:
				self.sock.close()
		except Exception:
			pass

	def _schedule_poll(self):
		if self._poll_scheduled:
			return
		self._poll_scheduled = True
		try:
			BigWorld.callback(POLL_INTERVAL, self._poll)
		except Exception:
			self._poll_scheduled = False

	def _reset_transport_diagnostics(self, now=None):
		now = time.time() if now is None else float(now)
		with self._pending_lock:
			self._diag_window_start = now
			self._diag_chunks = 0
			self._diag_messages = 0
			self._diag_snapshots = 0
			self._diag_bot_updates = 0
			self._diag_last_chunk = 0.0
			self._diag_last_snapshot = 0.0
			self._diag_last_bot_update = 0.0
			self._diag_last_bot_revision = -1
			self._diag_max_socket_gap = 0.0
			self._diag_max_snapshot_gap = 0.0
			self._diag_max_bot_update_gap = 0.0
			self._diag_max_queue_age = 0.0
			self._diag_max_pending = 0
			self._diag_poll_seconds = 0.0
			self._diag_poll_calls = 0
			self._diag_snapshot_apply_seconds = 0.0
			self._diag_snapshot_apply_calls = 0

	def _transport_diagnostic_snapshot(self, now=None, minimum_window=5.0):
		"""Return one bounded transport summary and reset its rolling counters."""
		now = time.time() if now is None else float(now)
		with self._pending_lock:
			window = max(0.0, now - self._diag_window_start)
			if window < float(minimum_window):
				return None
			result = {
				'window': window,
				'chunks': self._diag_chunks,
				'messages': self._diag_messages,
				'snapshots': self._diag_snapshots,
				'bot_updates': self._diag_bot_updates,
				'max_socket_gap': self._diag_max_socket_gap,
				'max_snapshot_gap': self._diag_max_snapshot_gap,
				'max_bot_update_gap': self._diag_max_bot_update_gap,
				'max_queue_age': self._diag_max_queue_age,
				'max_pending': self._diag_max_pending,
				'poll_seconds': self._diag_poll_seconds,
				'poll_calls': self._diag_poll_calls,
				'snapshot_apply_seconds': self._diag_snapshot_apply_seconds,
				'snapshot_apply_calls': self._diag_snapshot_apply_calls,
			}
			self._diag_window_start = now
			self._diag_chunks = 0
			self._diag_messages = 0
			self._diag_snapshots = 0
			self._diag_bot_updates = 0
			self._diag_max_socket_gap = 0.0
			self._diag_max_snapshot_gap = 0.0
			self._diag_max_bot_update_gap = 0.0
			self._diag_max_queue_age = 0.0
			self._diag_max_pending = len(self._pending)
			self._diag_poll_seconds = 0.0
			self._diag_poll_calls = 0
			self._diag_snapshot_apply_seconds = 0.0
			self._diag_snapshot_apply_calls = 0
			return result

	def _poll(self):
		poll_started = _network_perf_clock()
		self._poll_scheduled = False
		now = time.time()
		if self.connected and now - self._last_ping >= PING_INTERVAL:
			self._last_ping = now
			self._ping_seq += 1
			self._send({'type': 'ping', 'seq': self._ping_seq, 'client_time': now})
		messages = []
		with self._pending_lock:
			if self._pending:
				messages = self._pending
				self._pending = []
			for message in messages:
				if isinstance(message, dict):
					received_time = _finite_float(
						message.get('_client_received_time'), now)
					self._diag_max_queue_age = max(
						self._diag_max_queue_age, now - received_time)
		# Snapshots are level-triggered.  If the game thread was busy loading a
		# remote tank, applying every stale 30 Hz snapshot would create an
		# unbounded queue and make the visual state increasingly lag behind.
		latest_snapshot = None
		order_payloads = {}
		coalesced = []
		for message in messages:
			if isinstance(message, dict) and message.get('type') == 'snapshot':
				if 'bot_orders' in message:
					try:
						order_revision = int(message.get('bot_order_revision', 0) or 0)
					except (TypeError, ValueError):
						order_revision = 0
					order_key = (message.get('round_id'), order_revision)
					order_payloads[order_key] = message.get('bot_orders') or []
				latest_snapshot = message
			else:
				coalesced.append(message)
		if latest_snapshot is not None:
			# The server sends an order body only once per revision, while snapshots
			# are coalesced to the newest state on the game thread. Preserve the body
			# from an earlier snapshot of the same revision; otherwise a busy frame
			# keeps the new revision number but silently clears every bot order.
			try:
				latest_revision = int(
					latest_snapshot.get('bot_order_revision', 0) or 0)
			except (TypeError, ValueError):
				latest_revision = 0
			latest_order_key = (
				latest_snapshot.get('round_id'), latest_revision)
			if ('bot_orders' not in latest_snapshot and
					latest_order_key in order_payloads):
				latest_snapshot = dict(latest_snapshot)
				latest_snapshot['bot_orders'] = order_payloads[latest_order_key]
			coalesced.append(latest_snapshot)
		messages = coalesced
		for message in messages:
			message_kind = (message.get('type')
			                if isinstance(message, dict) else None)
			message_started = (_network_perf_clock()
			                   if message_kind == 'snapshot' else None)
			try:
				self._handle_message(message)
			except Exception:
				LOG_ERROR('LAN client message error:', repr(message))
			if message_started is not None:
				with self._pending_lock:
					self._diag_snapshot_apply_seconds += max(
						0.0, _network_perf_clock() - message_started)
					self._diag_snapshot_apply_calls += 1
		with self._pending_lock:
			self._diag_poll_seconds += max(
				0.0, _network_perf_clock() - poll_started)
			self._diag_poll_calls += 1
		if self.phase == 'battle':
			diagnostic = self._transport_diagnostic_snapshot(now)
			if diagnostic is not None:
				window = max(0.001, diagnostic['window'])
				LOG_NOTE(
					'LAN NET window=%.1fs chunks=%.1f/s messages=%.1f/s snapshots=%.1f/s '
					'bot_updates=%.1f/s max_socket_gap=%dms max_snapshot_gap=%dms '
					'max_bot_gap=%dms max_queue_age=%dms '
					'max_pending=%d poll=%.2fms/%dc snapshot_apply=%.2fms/%dc rtt=%s' % (
						diagnostic['window'], diagnostic['chunks'] / window,
						diagnostic['messages'] / window,
						diagnostic['snapshots'] / window,
						diagnostic['bot_updates'] / window,
						int(round(diagnostic['max_socket_gap'] * 1000.0)),
						int(round(diagnostic['max_snapshot_gap'] * 1000.0)),
						int(round(diagnostic['max_bot_update_gap'] * 1000.0)),
						int(round(diagnostic['max_queue_age'] * 1000.0)),
						diagnostic['max_pending'],
						(1000.0 * diagnostic['poll_seconds'] /
						 max(1, diagnostic['poll_calls'])),
						diagnostic['poll_calls'],
						(1000.0 * diagnostic['snapshot_apply_seconds'] /
						 max(1, diagnostic['snapshot_apply_calls'])),
						diagnostic['snapshot_apply_calls'],
						'pending' if self.rtt_ms is None else '%dms' % int(round(self.rtt_ms))))
		if self._last_error and not self._error_notified:
			self._error_notified = True
			_system_message('LAN connection error: %s' % self._last_error, 'error')
		if self.running:
			self._schedule_poll()

	def _load_server_timing(self, message):
		"""Project server-relative battle timing onto this client's receive clock."""
		timing = message.get('timing') if isinstance(message, dict) else None
		if not isinstance(timing, dict):
			return False
		received = _finite_float(message.get('_client_received_time'), time.time())
		# Relative server time avoids requiring synchronized Windows/macOS clocks.
		# Half the smoothed RTT approximates the packet's one-way transit time.
		one_way = 0.0
		if self.rtt_ms is not None:
			one_way = max(0.0, min(0.25, float(self.rtt_ms) / 2000.0))
		phase = str(timing.get('phase') or 'loading')
		start_in = max(0.0, _finite_float(timing.get('start_in_ms'), 0.0) / 1000.0)
		remaining = max(0.0, _finite_float(timing.get('remaining_ms'), 0.0) / 1000.0)
		duration = max(1.0, _finite_float(timing.get('duration_ms'), 900000.0) / 1000.0)
		if phase == 'prebattle':
			projected_start = received + start_in - one_way
			if self.combat_deadline is None or abs(self.combat_deadline - projected_start) > 0.25:
				self.combat_deadline = projected_start
			else:
				self.combat_deadline = self.combat_deadline * 0.8 + projected_start * 0.2
			projected_end = self.combat_deadline + duration
		elif phase == 'battle':
			if self.combat_deadline is None:
				self.combat_deadline = received - one_way
			projected_end = received + remaining - one_way
		else:
			projected_end = received - one_way
		self.combat_duration = duration
		if self.combat_end_deadline is None or abs(self.combat_end_deadline - projected_end) > 0.25:
			self.combat_end_deadline = projected_end
		else:
			self.combat_end_deadline = self.combat_end_deadline * 0.8 + projected_end * 0.2
		self.player._offhangar_network_combat_phase = phase
		self.player._offhangar_network_combat_deadline = self.combat_deadline
		self.player._offhangar_network_combat_end_deadline = self.combat_end_deadline
		self.player._offhangar_network_combat_duration = self.combat_duration
		return True

	def _handle_message(self, message):
		kind = message.get('type') if isinstance(message, dict) else None
		if kind == 'welcome':
			self.ready = True
			self.player_id = message.get('player_id')
			self.name = str(message.get('name') or self.name)
			self.vehicle = str(message.get('vehicle') or self.vehicle)
			self.team = message.get('team')
			self.slot = int(message.get('slot', 0) or 0)
			self.max_health = int(message.get('max_health', self.max_health) or self.max_health)
			self.map_name = message.get('map')
			self.available_maps = list(message.get('map_pool') or self.available_maps)
			self.spawn = message.get('spawn') or {}
			self.phase = message.get('phase') or 'waiting'
			self.round_id = message.get('round_id')
			self._set_authority(message.get('bot_authority_id'))
			self.player._offhangar_network_id = self.player_id
			self.player._offhangar_network_name = self.name
			self.player._offhangar_network_vehicle = self.vehicle
			self.player._offhangar_network_team = self.team
			self.player._offhangar_network_slot = self.slot
			self.player._offhangar_network_spawn = self.spawn
			self.player._offhangar_network_map_name = self.map_name
			self.player._offhangar_network_ready = True
			LOG_NOTE('LAN welcome id=%s name=%s vehicle=%s team=%s slot=%s map=%s phase=%s' % (
				self.player_id, self.name, self.vehicle, self.team, self.slot,
				self.map_name, self.phase))
			_system_message('Connected to LAN server as %s (team %s).' % (
				self.name, self.team))
			if self.phase == 'waiting':
				try:
					from gui.mods.offhangar.offline_battle import show_network_waiting_queue_from_server
					show_network_waiting_queue_from_server(self.player)
					from gui.mods.offhangar.lan_waiting_room import open as open_waiting_room
					open_waiting_room(self.player)
					# loadPrebattle creates its event listeners asynchronously. Repeat the
					# roster update once after the Flash page has populated.
					BigWorld.callback(0.25, lambda: _publish_queue_count(
						self.player, self.waiting_count) if self.phase == 'waiting' else None)
				except Exception:
					LOG_ERROR('LAN could not open the queue screen after welcome')
		elif kind == 'roster':
			players = message.get('players') or []
			count = len(players)
			self.phase = message.get('phase') or self.phase
			self.map_name = message.get('map') or self.map_name
			self.available_maps = list(message.get('map_pool') or self.available_maps)
			self.waiting_count = count
			self.player._offhangar_network_roster = players
			_publish_queue_count(self.player, count)
			try:
				from gui.mods.offhangar.lan_waiting_room import update as update_waiting_room
				update_waiting_room(self.player)
			except Exception:
				pass
			if count != getattr(self.player, '_offhangar_network_roster_count', -1):
				self.player._offhangar_network_roster_count = count
				LOG_NOTE('LAN waiting room: %d player(s); choose a map and click START BATTLE' % count)
		elif kind == 'battle_start':
			if not _fence_local_health_round(
					self.player, message.get('round_id')):
				return
			self._load_bot_orders(message)
			self._load_server_timing(message)
			if self.battle_started:
				return
			self.battle_started = True
			self.phase = 'battle'
			self._reset_transport_diagnostics()
			self.map_name = message.get('map') or self.map_name
			self.round_id = message.get('round_id', self.round_id)
			self.player._offhangar_network_health_round_id = self.round_id
			self.player._offhangar_network_server_health = None
			self.player._offhangar_network_map_name = self.map_name
			self.player._offhangar_network_roster = message.get('players') or []
			self.player._offhangar_network_bot_roster = message.get('bots') or []
			self.player._offhangar_network_bot_manifest = message.get('bot_manifest') or []
			self.player._offhangar_network_bot_manifest_pending = None
			try:
				from gui.mods.offhangar.lan_waiting_room import close as close_waiting_room
				close_waiting_room()
			except Exception:
				pass
			self._set_authority(message.get('bot_authority_id'))
			delay = max(0.0, min(5.0, _finite_float(message.get('delay'), 0.0)))
			LOG_NOTE('LAN BATTLE START received: map=%s players=%d delay=%.2f' % (
				self.map_name, len(message.get('players') or []), delay))
			_system_message('LAN battle starting: %s, %d player(s).' % (
				self.map_name, len(message.get('players') or [])))

			def _start_from_server():
				try:
					from gui.mods.offhangar.offline_battle import start_network_battle_from_server
					start_network_battle_from_server(self.player, self.map_name, self.team)
				except Exception:
					LOG_ERROR('LAN failed to enter battle after server start')

			BigWorld.callback(delay, _start_from_server)
		elif kind == 'start_denied':
			self.start_requested = False
			LOG_NOTE('LAN start denied: %s (players=%s)' % (
				message.get('code'), message.get('players')))
			_system_message('LAN battle could not start: %s.' % (
				message.get('code') or 'request denied'), 'warning')
			try:
				from gui.mods.offhangar.lan_waiting_room import set_status
				set_status('Start denied: %s' % (message.get('code') or 'request denied'))
			except Exception:
				pass
		elif kind == 'bot_manifest_result':
			pending = getattr(
				self.player, '_offhangar_network_bot_manifest_pending', None)
			if not isinstance(pending, dict):
				return
			try:
				result_round = int(message.get('round_id', -1))
				pending_round = int(pending.get('round_id', -2))
			except (TypeError, ValueError):
				return
			try:
				current_round = int(self.round_id)
			except (TypeError, ValueError):
				return
			if (result_round != current_round or
					result_round != pending_round or
					str(message.get('manifest_nonce') or '') !=
					str(pending.get('nonce') or '')):
				return
			if not network_is_authority(self.player):
				return
			try:
				result_ids = sorted(int(value) for value in
					(message.get('bot_ids') or ()))
			except (TypeError, ValueError):
				return
			installed = message.get('bots') or ()
			try:
				installed_ids = sorted(int(entry.get('id')) for entry in installed)
			except (AttributeError, TypeError, ValueError):
				installed_ids = []
			if (bool(message.get('accepted')) and
					result_ids == list(pending.get('bot_ids') or ()) and
					installed_ids == result_ids):
				pending['state'] = 'accepted'
				self.player._offhangar_network_bot_manifest = list(installed)
				LOG_NOTE('LAN bot manifest accepted: %d bot(s)' % len(result_ids))
			else:
				pending['state'] = 'rejected'
				pending['code'] = str(message.get('code') or 'rejected')
				LOG_ERROR('LAN bot manifest rejected:', pending['code'])
				_system_message(
					'LAN server rejected the canonical bot lineup.', 'error')
		elif kind == 'snapshot':
			if not _fence_local_health_round(
					self.player, message.get('round_id')):
				return
			self._last_snapshot = time.time()
			self._load_server_timing(message)
			if self._set_authority(message.get('bot_authority_id')) is False:
				return
			self._load_bot_orders(message)
			self.player._offhangar_network_snapshot = message
			_apply_snapshot(self.player, message)
		elif kind == 'events':
			if not _fence_local_health_round(
					self.player, message.get('round_id')):
				return
			self.player._offhangar_network_events = message.get('events') or []
			_handle_events(self.player, message.get('events') or [])
		elif kind == 'pong':
			client_time = _finite_float(message.get('client_time'), 0.0)
			if client_time > 0.0:
				received_time = _finite_float(
					message.get('_client_received_time'), time.time())
				sample = max(0.0, (received_time - client_time) * 1000.0)
				self.rtt_ms = sample if self.rtt_ms is None else self.rtt_ms * 0.75 + sample * 0.25
		elif kind == 'error':
			self._last_error = message.get('message') or message.get('code') or 'server error'
			LOG_ERROR('LAN server error:', self._last_error)
			if not self._error_notified:
				self._error_notified = True
				_system_message('LAN server error: %s' % self._last_error, 'error')


def _network_config():
	try:
		from gui.mods.offhangar._constants import CONFIG_OPTIONS
		return CONFIG_OPTIONS
	except Exception:
		return {}


def queue_info_for_player(player):
	"""Build the exact 0.8.2 Prebattle queue payload from the LAN roster."""
	cfg = _network_config()
	if not bool(cfg.get('network_mode', False)):
		return None
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	count = int(getattr(client, 'waiting_count', 0) or 0) if client is not None else 0
	count = max(0, min(count, 999))
	roster = list(getattr(player, '_offhangar_network_roster', None) or []) if player is not None else []
	if not roster and count > 0:
		# Welcome can reach Flash before the first roster broadcast. Keep the
		# displayed total truthful using the selected vehicle until that roster
		# replaces these temporary entries.
		vehicle_name = getattr(client, 'vehicle', 'ussr:MS-1') if client is not None else 'ussr:MS-1'
		roster = [{'vehicle': vehicle_name} for unused in range(count)]
	classes = [0, 0, 0, 0, 0]
	levels = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
	try:
		import constants
		from items import vehicles
		class_indices = constants.VEHICLE_CLASS_INDICES
		max_level = int(getattr(constants, 'MAX_VEHICLE_LEVEL', 10) or 10)
		for entry in roster[:999]:
			try:
				vehicle_name = str(entry.get('vehicle') or getattr(client, 'vehicle', 'ussr:MS-1'))
				descriptor = vehicles.VehicleDescr(typeName=vehicle_name)
				vehicle_type = descriptor.type
				class_index = None
				for tag in getattr(vehicle_type, 'tags', ()):
					if tag in class_indices:
						class_index = int(class_indices[tag])
						break
				level = max(1, min(int(getattr(vehicle_type, 'level', 1) or 1), max_level, 10))
				if class_index is None or class_index < 0 or class_index >= len(classes):
					raise ValueError('vehicle class is missing for %s' % vehicle_name)
				classes[class_index] += 1
				levels[level] += 1
			except Exception as error:
				LOG_ERROR('LAN queue vehicle classification failed:', str(error))
				classes[0] += 1
				levels[1] += 1
	except Exception as error:
		# A malformed/custom vehicle must not make the queue page fail to open.
		# Preserve the true total when the descriptor subsystem itself is absent.
		LOG_ERROR('LAN queue vehicle classification failed:', str(error))
		classes = [0, 0, 0, 0, 0]
		levels = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
		fallback_count = min(len(roster), 999)
		classes[0] = fallback_count
		levels[1] = fallback_count
	# Prebattle.onQueueInfoReceived displays sum(levels) as the player count.
	# It reverses levels and drops original element zero; indices 1..10 are tiers.
	return {
		'classes': classes,
		'levels': levels,
	}


def _publish_queue_count(player, count):
	if player is None or not hasattr(player, 'receiveQueueInfo'):
		return False
	qinfo = queue_info_for_player(player)
	if qinfo is None:
		return False
	try:
		player.receiveQueueInfo(qinfo, {})
		LOG_NOTE('LAN queue UI updated: %d connected player(s)' % int(count))
		return True
	except Exception:
		LOG_ERROR('LAN queue UI update failed')
		return False


def _descriptor_details(descriptor):
	if descriptor is None:
		return None
	type_name = getattr(descriptor, 'typeName', None)
	if not type_name:
		type_name = getattr(getattr(descriptor, 'type', None), 'name', None)
	if not type_name:
		return None
	return str(type_name), int(getattr(descriptor, 'maxHealth', 1000) or 1000)


def _selected_vehicle_details(player):
	"""Resolve the selected 0.8.2 garage vehicle and its real maximum HP."""
	descriptor = None
	compact_descr = 0
	try:
		from CurrentVehicle import g_currentVehicle
		# CurrentVehicle.py in the 0.8.2 client exposes the selected garage
		# Vehicle through .vehicle.  Its descriptor.type.name is the canonical
		# value accepted by vehicles.VehicleDescr(typeName=...).
		garage_vehicle = getattr(g_currentVehicle, 'vehicle', None)
		descriptor = getattr(garage_vehicle, 'descriptor', None)
		details = _descriptor_details(descriptor)
		if details is not None:
			return details

		# .item is populated asynchronously by ItemsRequester. Keep it as a
		# second exact source because the offline inventory shim already uses it.
		item = getattr(g_currentVehicle, 'item', None)
		descriptor = getattr(item, 'descriptor', None) if item is not None else None
		details = _descriptor_details(descriptor)
		if details is not None:
			return details

		for source in (garage_vehicle, item):
			if source is None:
				continue
			source_descriptor = getattr(source, 'descriptor', None)
			try:
				compact_descr = source_descriptor.makeCompactDescr()
			except Exception:
				compact_descr = 0
			if not compact_descr:
				compact_descr = (getattr(source, 'intCD', 0) or
					getattr(source, 'typeCompDescr', 0) or
					getattr(getattr(source_descriptor, 'type', None), 'compactDescr', 0) or
					getattr(source_descriptor, 'typeCompDescr', 0) or 0)
			if compact_descr:
				break
	except Exception as exc:
		LOG_DEBUG('LAN CurrentVehicle resolution failed:', str(exc))
	if not compact_descr:
		try:
			selected_id = getattr(player, '_offhangar_network_pending_veh_id', 0) or 0
			cache = getattr(getattr(player, 'inventory', None), '_Inventory__cache', None) or {}
			vehicle_data = cache.get('inventory', {}).get(1, {})
			comp_descrs = vehicle_data.get('compDescr', {})
			compact_descr = (comp_descrs.get(selected_id, 0) or 0)
			if not compact_descr and comp_descrs:
				try:
					compact_descr = comp_descrs.values()[0] or 0
				except Exception:
					compact_descr = 0
		except Exception as exc:
			LOG_DEBUG('LAN inventory vehicle resolution failed:', str(exc))
			compact_descr = 0
	if compact_descr:
		try:
			from items import vehicles
			descriptor = vehicles.VehicleDescr(compactDescr=compact_descr)
			details = _descriptor_details(descriptor)
			if details is not None:
				return details
		except Exception as exc:
			LOG_DEBUG('LAN compact descriptor resolution failed:', str(exc))
	try:
		descriptor = getattr(player, 'vehicleTypeDescriptor', None)
		details = _descriptor_details(descriptor)
		if details is not None:
			return details
	except Exception:
		pass
	LOG_ERROR('LAN selected vehicle could not be resolved; using MS-1 fallback')
	_system_message('LAN could not read the selected tank; using MS-1 with 100 HP.', 'error')
	return 'ussr:MS-1', 100


def start_for_player(player):
	if player is None:
		return None
	old = getattr(player, '_offhangar_network_client', None)
	if old is not None and old.running:
		return old
	cfg = _network_config()
	host = cfg.get('network_server_host', '127.0.0.1')
	port = cfg.get('network_server_port', 28782)
	name = cfg.get('nickname', 'Player')
	vehicle, max_health = _selected_vehicle_details(player)
	LOG_NOTE('LAN selected vehicle resolved: %s (%s HP)' % (vehicle, max_health))
	_system_message('Connecting to LAN server %s:%s with %s...' % (host, port, vehicle))
	client = LANClient(player, host, port, name, vehicle, max_health)
	player._offhangar_network_client = client
	player._offhangar_network_ready = False
	player._offhangar_network_snapshot = None
	player._offhangar_network_events = []
	player._offhangar_network_pending_remote_ids = {}
	player._offhangar_network_server_health = None
	player._offhangar_network_health_round_id = None
	player._offhangar_network_authority_id = None
	player._offhangar_network_is_authority = False
	player._offhangar_network_authority_handoff_pending = False
	player._offhangar_network_authority_demotion_pending = False
	player._offhangar_network_announced_authority_id = None
	player._offhangar_network_bot_manifest = []
	player._offhangar_network_bot_manifest_pending = None
	player._offhangar_network_bot_snapshots_deferred = False
	player._offhangar_network_bot_defer_logged = False
	player._offhangar_network_server_navigation_at = 0.0
	player._offhangar_network_server_navigation_complete = False
	player._offhangar_network_server_navigation_logged = False
	player._offhangar_network_result_applied = False
	player._offhangar_network_combat_phase = 'loading'
	player._offhangar_network_combat_deadline = None
	player._offhangar_network_combat_end_deadline = None
	player._offhangar_network_combat_duration = 900.0
	client.start()
	return client


def request_battle_start(player, map_name=None):
	if player is None:
		return False
	cfg = _network_config()
	if not bool(cfg.get('network_mode', False)):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	if client is None or not client.running:
		LOG_NOTE('LAN start button ignored: click Battle! and wait for JOIN first')
		_system_message('Click Battle! and wait for the LAN JOIN before starting.', 'warning')
		return True
	if not client.ready:
		LOG_NOTE('LAN start button ignored while still connecting')
		_system_message('Still connecting to the LAN server.', 'warning')
		return True
	if client.phase == 'waiting':
		client.request_start(map_name)
		return True
	if client.phase == 'battle':
		return True
	return True


def stop_for_player(player):
	try:
		from gui.mods.offhangar.lan_waiting_room import close as close_waiting_room
		close_waiting_room()
	except Exception:
		pass
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is not None:
		client.stop()
	if player is not None:
		player._offhangar_network_client = None
		player._offhangar_network_ready = False
		player._offhangar_network_arena_starting = False
		player._offhangar_network_snapshot = None
		player._offhangar_network_events = []
		player._offhangar_network_pending_remote_ids = {}
		player._offhangar_network_server_health = None
		player._offhangar_network_health_round_id = None
		player._offhangar_network_authority_id = None
		player._offhangar_network_is_authority = False
		player._offhangar_network_authority_handoff_pending = False
		player._offhangar_network_authority_demotion_pending = False
		player._offhangar_network_announced_authority_id = None
		player._offhangar_network_bot_manifest = []
		player._offhangar_network_bot_manifest_pending = None
		player._offhangar_network_bot_snapshots_deferred = False
		player._offhangar_network_bot_defer_logged = False
		player._offhangar_network_server_navigation_at = 0.0
		player._offhangar_network_server_navigation_complete = False
		player._offhangar_network_server_navigation_logged = False
		player._offhangar_network_combat_phase = None
		player._offhangar_network_combat_deadline = None
		player._offhangar_network_combat_end_deadline = None
		player._offhangar_network_bot_orders = {}
		player._offhangar_network_bot_order_revision = 0
		player._offhangar_network_result_applied = False
		# These are per-battle closures installed by offline_battle. Keeping them
		# on the persistent account pins the finished battle's models and mocks.
		player._offhangar_apply_network_rules_state = None
		player._offhangar_apply_network_battle_result = None
		player._offhangar_network_spawn_remote = None
		player._offhangar_network_formation = None
		player._offhangar_network_world_frame_cache = None
	globals().pop('_g_network_mock_indexes', None)


def _server_pose_frame(player):
	"""Return the fixed world-to-server axes for the current battle."""
	try:
		frame = _network_world_frame(player)
		if frame is None:
			return None
		return frame[1:8]
	except Exception:
		return None


def _server_pose_with_frame(frame, world_x, world_y, world_z, world_yaw):
	"""Convert one pose using axes shared by every snapshot body."""
	try:
		if frame is None:
			return (world_x, world_y, world_z), world_yaw
		b1x, b1z, axis_x, axis_z, right_x, right_z, axis_yaw = frame
		world_dx = float(world_x) - b1x
		world_dz = float(world_z) - b1z
		travel = world_dx * axis_x + world_dz * axis_z
		lateral = world_dx * right_x + world_dz * right_z
		return ((lateral, _finite_float(world_y), travel),
			_finite_float(world_yaw) - axis_yaw)
	except Exception:
		return None, _finite_float(world_yaw)


def _server_pose_from_world(player, world_x, world_y, world_z, world_yaw):
	"""Convert the loaded map coordinates back into the shared server frame."""
	return _server_pose_with_frame(
		_server_pose_frame(player), world_x, world_y, world_z, world_yaw)


def send_local_input(player, forward, turn, aim_yaw, gun_pitch,
		world_x=None, world_y=None, world_z=None, hull_yaw=None):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is not None and client.ready:
		position = None
		server_hull_yaw = hull_yaw
		server_aim_yaw = aim_yaw
		if world_x is not None and world_z is not None and hull_yaw is not None:
			position, server_hull_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, hull_yaw)
			_, server_aim_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, aim_yaw)
		reported_health = None
		try:
			mock = _local_mock(player)
			if mock is not None:
				local_health = int(getattr(mock, 'health', client.max_health) or 0)
				server_health = getattr(
					player, '_offhangar_network_server_health', None)
				if server_health is None:
					server_health = int(client.max_health)
				else:
					server_health = int(server_health)
				# Canonical hit/snapshot updates already belong to the server. Only
				# report a strictly lower local value caused by fire, drowning or a
				# collision that the standalone server cannot simulate itself.
				if local_health < server_health:
					reported_health = local_health
		except Exception:
			pass
		client.send_input(forward, turn, server_aim_yaw, gun_pitch,
			position=position, yaw=server_hull_yaw, reported_health=reported_health)


def send_local_fire(player, shell_index=0, aim_yaw=None, gun_pitch=None,
		world_x=None, world_y=None, world_z=None, hull_yaw=None):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is not None and client.ready:
		position = None
		server_hull_yaw = hull_yaw
		server_aim_yaw = aim_yaw
		if world_x is not None and world_z is not None and hull_yaw is not None:
			position, server_hull_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, hull_yaw)
			_, server_aim_yaw = _server_pose_from_world(
				player, world_x, world_y or 0.0, world_z, aim_yaw)
		shot_seq = client.send_fire(shell_index, position, server_hull_yaw,
			server_aim_yaw, gun_pitch)
		if shot_seq is not None:
			player._offhangar_network_last_fire_seq = shot_seq
			player._offhangar_network_last_shell_index = int(shell_index or 0)
		return shot_seq
	return None


def send_local_hit(player, target_id, shot_seq, damage, shot_result,
		shell_index=0, impact_position=None, critical=False):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is None or not client.ready or shot_seq is None or target_id is None:
		return False
	server_impact = None
	if impact_position is not None:
		try:
			server_impact, unused_yaw = _server_pose_from_world(player,
				impact_position.x, impact_position.y, impact_position.z, 0.0)
		except Exception:
			server_impact = None
	return client.send_hit(target_id, shot_seq, damage, shot_result,
		shell_index, server_impact, critical)


def network_is_authority(player):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	return bool(client is not None and getattr(client, 'running', True) and
		getattr(client, 'connected', True) and client.ready and
		client.phase == 'battle' and
		getattr(player, '_offhangar_network_is_authority', False) and
		not getattr(player,
			'_offhangar_network_authority_handoff_pending', False) and
		not getattr(player,
			'_offhangar_network_authority_demotion_pending', False))


def _replica_lineup_loading(player):
	"""Return whether this relay is still constructing the shared bot lineup."""
	if player is None or network_is_authority(player):
		return False
	try:
		expected = int(getattr(player, '_offh_auto_spawn_expected', 0) or 0)
		completed = int(getattr(player, '_offh_auto_spawn_completed', 0) or 0)
		return expected > 0 and completed < expected
	except (TypeError, ValueError):
		return False


def publish_bot_manifest(player, jobs):
	"""Publish the authority-selected lineup before any client creates bots."""
	if not network_is_authority(player):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	if client is None:
		return False
	pending = getattr(
		player, '_offhangar_network_bot_manifest_pending', None)
	if isinstance(pending, dict):
		try:
			if int(pending.get('round_id', -1)) != int(client.round_id or -1):
				return False
		except (TypeError, ValueError):
			return False
		if pending.get('state') == 'accepted':
			return bool(getattr(
				player, '_offhangar_network_bot_manifest', None))
		if pending.get('state') in ('rejected', 'send_failed'):
			return False
		# Freeze the first payload while waiting. A retry may arrive after the
		# matchmaker shuffled a new local candidate, but only this nonce/body may
		# be accepted for the round.
		now = time.time()
		if now - float(pending.get('sent_at', 0.0) or 0.0) >= 0.5:
			if client.send_bot_manifest(
					pending.get('manifest') or (), pending.get('map_frame'),
					pending.get('nonce'), pending.get('round_id')):
				pending['sent_at'] = now
		return False
	frame = _server_pose_frame(player)
	if frame is None:
		return False
	manifest = []
	for job in jobs[:30]:
		try:
			bot_id, team, slot, vehicle, name, max_health, world_x, world_y, world_z, world_yaw = job
			profile = {}
			route_payload = {}
			try:
				from items import vehicles
				from gui.mods.offhangar.bot_ai import build_vehicle_profile
				descriptor = vehicles.VehicleDescr(typeName=str(vehicle))
				profile = build_vehicle_profile(descriptor)
			except Exception:
				profile = {}
			try:
				import sys
				offline = sys.modules.get('gui.mods.offhangar.offline_battle')
				director_getter = getattr(offline, '_offh_ai_director', None)
				director = director_getter(player) if callable(director_getter) else None
				if director is not None:
					import Math
					agent = director.register_profile(bot_id, team, profile, name)
					route = agent.get('route') or {}
					waypoints = []
					for point in route.get('waypoints', ()):
						grounded = _ground_world_point(
							Math.Vector3(float(point[0]), 0.0, float(point[1])))
						waypoint_world_y = float(grounded.y) if grounded is not None else 0.0
						shared, unused_yaw = _server_pose_from_world(
							player, point[0], waypoint_world_y, point[1], 0.0)
						waypoints.append({
							'x': shared[0], 'y': shared[1], 'z': shared[2],
							'hold': bool(point[2]) if len(point) > 2 else False,
						})
					route_payload = {
						'id': route.get('id', 'server_route'),
						'waypoints': waypoints,
					}
			except Exception:
				route_payload = {}
			server_pos, server_yaw = _server_pose_from_world(
				player, world_x, world_y, world_z, world_yaw)
			manifest.append({
				'id': int(bot_id), 'team': int(team), 'slot': int(slot),
				'vehicle': str(vehicle), 'name': str(name),
				'max_health': max(1, int(max_health)),
				'health': max(1, int(max_health)),
				'world_pose': True,
				'x': server_pos[0], 'y': server_pos[1], 'z': server_pos[2],
				'yaw': server_yaw, 'aim_yaw': server_yaw,
				'profile': profile,
				'route': route_payload,
			})
		except Exception:
			continue
	if not manifest:
		return False
	map_frame = None
	try:
		map_frame = {
			'origin': [round(float(frame[0]), 4), round(float(frame[1]), 4)],
			'axis': [round(float(frame[2]), 7), round(float(frame[3]), 7)],
		}
	except Exception:
		return False
	bot_ids = sorted(int(entry['id']) for entry in manifest)
	try:
		client._manifest_nonce_seq = int(
			getattr(client, '_manifest_nonce_seq', 0) or 0) + 1
		nonce = '%s-%s-%s' % (
			int(client.round_id or 0), int(client.player_id or 0),
			client._manifest_nonce_seq)
	except Exception:
		return False
	pending = {
		'nonce': nonce,
		'round_id': int(client.round_id or 0),
		'bot_ids': bot_ids,
		'manifest': manifest,
		'map_frame': map_frame,
		'state': 'pending',
		'sent_at': time.time(),
	}
	player._offhangar_network_bot_manifest_pending = pending
	if not client.send_bot_manifest(
			manifest, map_frame, nonce, pending['round_id']):
		pending['state'] = 'send_failed'
		return False
	return False


def publish_bot_observation(player, contacts, affordances=None, navigation=None):
	"""Send the authority's team-visibility report to the global planner."""
	if not network_is_authority(player):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	if client is None or not client.ready:
		return False
	payload = []
	for raw in (contacts or ())[:64]:
		try:
			point = _protocol_position(raw.get('position'))
			if point is None:
				continue
			server_pos, unused_yaw = _server_pose_from_world(
				player, point[0], point[1], point[2], 0.0)
			if server_pos is None:
				continue
			item = {
				'observing_team': int(raw.get('observing_team')),
				'target_id': int(raw.get('target_id')),
				'target_kind': _safe_text(raw.get('target_kind'), 16),
				'target_team': int(raw.get('target_team')),
				'x': server_pos[0], 'y': server_pos[1], 'z': server_pos[2],
				'health': max(0, int(raw.get('health', 0))),
				'max_health': max(1, int(raw.get('max_health', 1))),
				'class_tag': _safe_text(raw.get('class_tag'), 24),
				'armor': max(0.0, _finite_float(raw.get('armor'))),
				'visible': _protocol_bool(raw.get('visible'), True),
			}
			# Team spotting and local firing lanes are separate facts. Always send
			# the current client's bounded list, including [] when no bot can shoot;
			# omission is reserved for genuinely older protocol-v5 packages.
			shootable = []
			seen_bot_ids = set()
			for raw_bot_id in (raw.get('shootable_by_bot_ids') or ())[:64]:
				try:
					bot_id = int(raw_bot_id)
				except Exception:
					continue
				if bot_id <= 0 or bot_id in seen_bot_ids:
					continue
				seen_bot_ids.add(bot_id)
				shootable.append(bot_id)
			item['shootable_by_bot_ids'] = shootable
			payload.append(item)
		except Exception:
			continue
	shared_affordances = []
	for raw in (affordances or ())[:16]:
		try:
			item = {
				'bot_id': int(raw.get('bot_id')),
				'target_id': int(raw.get('target_id')),
				'target_kind': str(raw.get('target_kind') or ''),
				'candidates': [],
			}
			for candidate in (raw.get('candidates') or ())[:12]:
				position = _protocol_position(candidate.get('position'))
				if position is None:
					continue
				server_position, unused_yaw = _server_pose_from_world(
					player, position[0], position[1], position[2], 0.0)
				if server_position is None:
					continue
				value = {
					'id': _safe_text(candidate.get('id'), 80),
					'position': {
					'x': server_position[0], 'y': server_position[1],
					'z': server_position[2],
					},
					'travel_distance': max(0.0, _finite_float(candidate.get('travel_distance'))),
					'route_alignment': max(0.0, min(1.0, _finite_float(candidate.get('route_alignment')))),
					'enemy_occlusion': max(0.0, min(1.0, _finite_float(candidate.get('enemy_occlusion')))),
					'exposure': max(0.0, min(1.0, _finite_float(candidate.get('exposure'), 1.0))),
					'slope': max(0.0, _finite_float(candidate.get('slope'))),
					'water': max(0.0, min(1.0, _finite_float(candidate.get('water')))),
					'ally_congestion': max(0.0, min(1.0, _finite_float(candidate.get('ally_congestion')))),
					'peek_feasible': _protocol_bool(candidate.get('peek_feasible')),
					'escape_feasible': _protocol_bool(candidate.get('escape_feasible')),
				}
				peek = _protocol_position(candidate.get('peek_position'))
				if peek is not None:
					server_peek, unused_yaw = _server_pose_from_world(
						player, peek[0], peek[1], peek[2], 0.0)
					if server_peek is not None:
						value['peek_position'] = {
							'x': server_peek[0], 'y': server_peek[1],
							'z': server_peek[2],
						}
					else:
						value['peek_feasible'] = False
				else:
					value['peek_feasible'] = False
				item['candidates'].append(value)
			if item['candidates']:
				shared_affordances.append(item)
		except Exception:
			continue
	shared_navigation = None
	if isinstance(navigation, dict):
		shared_navigation = {
			'graph': {'source': 'none', 'cell_mm': 0, 'nodes': 0},
			'total': {}, 'active': {}, 'recovered': 0, 'search': {}}
		raw_graph = navigation.get('graph')
		if isinstance(raw_graph, dict):
			source = str(raw_graph.get('source') or 'none')
			if source not in ('baked', 'runtime'):
				source = 'none'
			shared_navigation['graph']['source'] = source
			for name, maximum in (('cell_mm', 100000), ('nodes', 100000)):
				try:
					value = int(raw_graph.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['graph'][name] = max(0, min(value, maximum))
		for group in ('total', 'active'):
			raw_group = navigation.get(group)
			if not isinstance(raw_group, dict):
				continue
			for name in ('safe_direct', 'safe_local', 'reactive'):
				try:
					value = int(raw_group.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation[group][name] = max(0, min(value, 100000))
		try:
			value = int(navigation.get('recovered', 0) or 0)
		except Exception:
			value = 0
		shared_navigation['recovered'] = max(0, min(value, 100000))
		raw_search = navigation.get('search')
		if isinstance(raw_search, dict):
			for name in ('pending', 'completed', 'failed', 'oldest_ms',
					'tick_age_ms'):
				try:
					value = int(raw_search.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['search'][name] = max(0, min(value, 3600000))
		raw_orders = navigation.get('orders')
		if isinstance(raw_orders, dict):
			shared_navigation['orders'] = {}
			for name, maximum in (('revision', 1000000000), ('loaded', 30)):
				try:
					value = int(raw_orders.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['orders'][name] = max(0, min(value, maximum))
		raw_aim = navigation.get('aim')
		if isinstance(raw_aim, dict):
			shared_navigation['aim'] = {}
			for name in ('alive', 'targeted', 'aligned', 'traversing', 'limited'):
				try:
					value = int(raw_aim.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['aim'][name] = max(0, min(value, 30))
		raw_driver = navigation.get('driver')
		if isinstance(raw_driver, dict):
			shared_navigation['driver'] = {}
			for name in ('moving', 'drive', 'avoid', 'blocked', 'recovery', 'arrived',
					'server_wait', 'traffic_wait', 'water_guard', 'full', 'cruise',
					'slow'):
				try:
					value = int(raw_driver.get(name, 0) or 0)
				except Exception:
					value = 0
				shared_navigation['driver'][name] = max(0, min(value, 30))
			try:
				value = int(raw_driver.get('speed_pct', 0) or 0)
			except Exception:
				value = 0
			shared_navigation['driver']['speed_pct'] = max(0, min(value, 200))
		raw_safety = navigation.get('safety')
		if isinstance(raw_safety, dict):
			shared_navigation['safety'] = {}
			for name in ('water_guard_total', 'water_guard_active',
					'edge_guard_total', 'edge_guard_active', 'veto_water',
					'veto_terrain', 'veto_obstacle', 'veto_error'):
				try:
					value = int(raw_safety.get(name, 0) or 0)
				except Exception:
					value = 0
				maximum = 100000 if name.endswith('_total') else 30
				shared_navigation['safety'][name] = max(0, min(value, maximum))
	return client.send_bot_observation(
		payload, shared_affordances, shared_navigation)


def _moving_target_velocity(target):
	"""Return the freshest world-space target velocity available locally."""
	try:
		if getattr(target, '_network_remote', False):
			value = getattr(target, '_network_target_velocity', None)
			if value is not None:
				return (float(value[0]), float(value[1]), float(value[2]))
	except Exception:
		pass
	try:
		speed = _finite_float(getattr(target, '_veh_velocity', 0.0))
		yaw = _finite_float(getattr(target, 'yaw', 0.0))
		return (math.sin(yaw) * speed, 0.0, math.cos(yaw) * speed)
	except Exception:
		return (0.0, 0.0, 0.0)


def _bot_projectile_speed(mock):
	"""Read the installed shell speed without assuming descriptor wrappers."""
	try:
		descriptor = getattr(mock, 'typeDescriptor', None)
		gun = getattr(descriptor, 'gun', None)
		shots = gun.get('shots', ()) if hasattr(gun, 'get') else gun['shots']
		if not shots:
			return 0.0
		index = max(0, min(int(getattr(
			mock, '_network_bot_shell_index', 0) or 0), len(shots) - 1))
		shot = shots[index]
		speed = shot.get('speed', 0.0) if hasattr(shot, 'get') else shot['speed']
		return max(0.0, float(speed))
	except Exception:
		return 0.0


def authoritative_bot_order(player, mock):
	"""Return one server order converted from shared to loaded-map coordinates."""
	if not network_is_authority(player) or mock is None:
		return None
	bot_id = getattr(mock, '_network_bot_id', None)
	if bot_id is None:
		return None
	client = getattr(player, '_offhangar_network_client', None)
	if client is None:
		return None
	raw = client.bot_orders.get(int(bot_id))
	if raw is None:
		try:
			client.request_bot_orders()
		except Exception:
			pass
		return None
	# Strategic orders are immutable for one server revision. Converting four
	# coordinates for every bot at 10 Hz cost several milliseconds on this 2012
	# Python client, even when the server had sent no new order. Cache only the
	# frame conversion; the live target pose below remains refreshed on every call.
	revision = int(getattr(client, 'bot_order_revision', 0) or 0)
	cache_key = (revision, id(raw))
	cached = getattr(mock, '_offh_network_world_order_cache', None)
	if (isinstance(cached, tuple) and len(cached) == 2 and
			cached[0] == cache_key):
		base_order = cached[1]
	else:
		base_order = dict(raw)
		for key in ('aim_position', 'face_position', 'move_position', 'route_anchor'):
			point = raw.get(key)
			if not isinstance(point, dict):
				continue
			world = _world_from_server(player, dict(point, world_pose=True))
			if world is not None:
				base_order[key] = (
					float(world.x), float(world.y), float(world.z))
		mock._offh_network_world_order_cache = (cache_key, base_order)
	order = dict(base_order)
	# The server selects who the bot may engage; the authority client owns the
	# rendered simulation and therefore has the freshest exact pose. Aim a
	# currently visible target at its live local mock, as the original offline AI
	# did, instead of at the last 2 Hz contact-report coordinate. Last-known
	# investigate orders retain the server coordinate and cannot see through fog.
	if bool(raw.get('fire_allowed')) and raw.get('target_id') is not None:
		target = None
		try:
			target_id = int(raw.get('target_id'))
			if raw.get('target_kind') == 'human':
				if target_id == getattr(player, '_offhangar_network_id', None):
					target = _local_mock(player)
				else:
					target = _find_mock(player, target_id)
			elif raw.get('target_kind') == 'bot':
				target = _find_bot(target_id)
		except Exception:
			target = None
		if (target is not None and getattr(target, 'isAlive', True) and
				getattr(target, 'health', 1) > 0):
			try:
				position = target.position
				live_position = (
					float(position.x), float(position.y), float(position.z))
				aim_position = live_position
				target_velocity = _moving_target_velocity(target)
				try:
					if str(order.get('combat_mode') or '') != 'artillery_fire':
						from gui.mods.offhangar import bot_ai_driver
						shooter = mock.position
						aim_position = bot_ai_driver.intercept_point(
							(float(shooter.x), float(shooter.y), float(shooter.z)),
							live_position, target_velocity,
							_bot_projectile_speed(mock), 1.5)
				except Exception:
					aim_position = live_position
				order['aim_position'] = aim_position
				order['face_position'] = live_position
				order['target_velocity'] = target_velocity
				if order.get('combat_mode') == 'advance_contact':
					order['move_position'] = live_position
			except Exception:
				order['fire_allowed'] = False
		else:
			# A visible order is permission to attempt a shot, but only the authority
			# client can prove the rendered target still exists at a live pose.
			order['fire_allowed'] = False
	return order


def publish_authoritative_bots(player, mocks):
	"""Send canonical bot pose, gun, shot and HP state at 30 Hz."""
	if not network_is_authority(player):
		return False
	# The server treats the first accepted bot-state report as proof that a newly
	# promoted authority consumed its complete canonical handoff snapshot. Do not
	# let an early local frame clear that fence before the full pose is applied.
	if bool(getattr(
			player, '_offhangar_network_authority_handoff_pending', False)):
		return False
	client = getattr(player, '_offhangar_network_client', None)
	if client is None or not client.bot_states_due():
		# Avoid walking every mock and converting every pose on render frames which
		# the 30 Hz transport will reject anyway.
		return False
	server_frame = _server_pose_frame(player)
	entity_to_bot = {}
	for candidate in (mocks or {}).values():
		candidate_bot_id = getattr(candidate, '_network_bot_id', None)
		candidate_entity_id = getattr(candidate, 'id', None)
		if candidate_bot_id is not None and candidate_entity_id is not None:
			entity_to_bot[candidate_entity_id] = int(candidate_bot_id)
	states = []
	for mock in (mocks or {}).values():
		bot_id = getattr(mock, '_network_bot_id', None)
		if bot_id is None:
			continue
		try:
			pos = mock.position
			killer_bot_id = entity_to_bot.get(
				getattr(mock, 'last_killer_id', None), 0)
			yaw = _finite_float(getattr(mock, 'yaw', 0.0))
			server_pos, server_yaw = _server_pose_with_frame(
				server_frame, pos.x, pos.y, pos.z, yaw)
			# Position and hull yaw already established the server-frame rotation.
			# Relative turret yaw is frame invariant, so a second formation lookup and
			# coordinate conversion per bot is unnecessary.
			server_aim_yaw = server_yaw + _finite_float(
				getattr(mock, '_turret_yaw', 0.0))
			mobility_disabled, mobility_repair_seconds = _mobility_report(mock)
			states.append({
				'id': int(bot_id),
				'x': server_pos[0], 'y': server_pos[1], 'z': server_pos[2],
				'yaw': server_yaw, 'aim_yaw': server_aim_yaw,
				'gun_pitch': _finite_float(getattr(mock, '_gun_pitch', 0.0)),
				'speed': _finite_float(getattr(mock, '_veh_velocity', 0.0)),
				'turn_velocity': _finite_float(
					getattr(mock, '_veh_turn_velocity', 0.0)),
				'fire_seq': int(getattr(mock, '_network_bot_fire_seq', 0) or 0),
				'shell_index': int(getattr(mock, '_network_bot_shell_index', 0) or 0),
				'health': max(0, int(getattr(mock, 'health', 0) or 0)),
				'killer_bot_id': int(killer_bot_id or 0),
				'killer_kind': 'bot' if killer_bot_id else '',
				'killer_id': int(killer_bot_id or 0),
				'alive': bool(getattr(mock, 'isAlive', False)) and int(getattr(mock, 'health', 0) or 0) > 0,
				'mobility_disabled': mobility_disabled,
				'mobility_repair_seconds': round(
					mobility_repair_seconds, 3),
			})
		except Exception:
			continue
	return client.send_bot_states(states) if states else False


def send_local_bot_hit(player, bot_id, shot_seq, damage, shot_result,
		impact_position=None, critical=False):
	client = getattr(player, '_offhangar_network_client', None) if player is not None else None
	if client is None or not client.ready or bot_id is None or shot_seq is None:
		return False
	server_impact = None
	if impact_position is not None:
		try:
			server_impact, unused_yaw = _server_pose_from_world(player,
				impact_position.x, impact_position.y, impact_position.z, 0.0)
		except Exception:
			pass
	return client.send_bot_hit(
		bot_id, shot_seq, damage, shot_result, server_impact, critical)


def send_authoritative_bot_human_hit(player, bot_id, target_id, shot_seq,
		damage, shot_result, impact_position=None, critical=False):
	if not network_is_authority(player) or target_id is None:
		return False
	server_impact = None
	if impact_position is not None:
		try:
			server_impact, unused_yaw = _server_pose_from_world(player,
				impact_position.x, impact_position.y, impact_position.z, 0.0)
		except Exception:
			pass
	return player._offhangar_network_client.send_bot_human_hit(
		bot_id, target_id, shot_seq, damage, shot_result, server_impact, critical)


def send_authoritative_rules(player, bases):
	if not network_is_authority(player):
		return False
	rules = {'bases': {}}
	for team in (1, 2):
		state = bases.get(team, {}) if bases is not None else {}
		contributors = {}
		for vehicle_id, points in (state.get('contributors') or {}).items():
			try:
				points = max(0, min(int(points or 0), 100))
			except Exception:
				continue
			if points:
				contributors[str(vehicle_id)[:64]] = points
		active_contributors = []
		for vehicle_id in state.get('active_contributors') or ():
			vehicle_id = str(vehicle_id)[:64]
			if (vehicle_id.startswith('human:') or
					vehicle_id.startswith('bot:')):
				active_contributors.append(vehicle_id)
		active_contributors = sorted(set(active_contributors))[:30]
		rules['bases'][str(team)] = {
			'points': max(0, min(int(state.get('points', 0) or 0), 100)),
			'stopped': bool(state.get('stopped', False)),
			'contributors': contributors,
			'active_contributors': active_contributors,
			'invaders': len(active_contributors),
			'cursor': max(0, int(state.get('cursor', 0) or 0)),
		}
	return player._offhangar_network_client.send_rules(rules)


def send_authoritative_result(player, winner, reason, base_team=0):
	if not network_is_authority(player):
		return False
	return player._offhangar_network_client.send_battle_result(
		winner, reason, base_team)


def install_network_hud_metrics():
	"""Replace only ping/lag arguments of the stock 0.8.2 debug panel."""
	try:
		import gui.Scaleform.Battle as battle_module
		stats_class = getattr(battle_module, '_PerformanceStats', None)
		if stats_class is None or getattr(stats_class, '_offhangar_network_metrics', False):
			return stats_class is not None
		original = stats_class.updateDebugInfo
		def _network_update(stats, fps, ping, lag, recorded_fps):
			try:
				player = BigWorld.player()
				client = getattr(player, '_offhangar_network_client', None) if player is not None else None
				if client is not None and client.phase == 'battle':
					now = time.time()
					ping = 999 if client.rtt_ms is None else max(0, min(int(round(client.rtt_ms)), 999))
					lag = (not client.connected or client._last_snapshot <= 0.0 or
						now - client._last_snapshot > 2.5 or now - client._last_receive > 2.5)
			except Exception:
				pass
			return original(stats, fps, ping, lag, recorded_fps)
		stats_class.updateDebugInfo = _network_update
		stats_class._offhangar_network_metrics = True
		LOG_NOTE('LAN native ping/lag HUD metrics installed')
		return True
	except Exception as error:
		LOG_ERROR('LAN native ping/lag HUD metrics install failed:', str(error))
		return False


def _network_world_frame(player):
	"""Cache the immutable server-to-map basis for one battle formation."""
	formation = getattr(player, '_offhangar_network_formation', None)
	if formation is None:
		return None
	cache_key = id(formation)
	cached = getattr(player, '_offhangar_network_world_frame_cache', None)
	if isinstance(cached, tuple) and len(cached) == 9 and cached[0] == cache_key:
		return cached
	base1 = formation(1, 0)
	base2 = formation(2, 0)
	b1x, b1z = float(base1[0]), float(base1[1])
	b2x, b2z = float(base2[0]), float(base2[1])
	dx, dz = b2x - b1x, b2z - b1z
	length = math.sqrt(dx * dx + dz * dz) or 1.0
	axis_x, axis_z = dx / length, dz / length
	right_x, right_z = axis_z, -axis_x
	frame = (cache_key, b1x, b1z, axis_x, axis_z,
	         right_x, right_z, math.atan2(dx, dz), formation)
	player._offhangar_network_world_frame_cache = frame
	return frame


def _world_from_server(player, state):
	"""Map canonical server coordinates onto the locally loaded WoT map.

	The elected authority supplies the map-frame basis used by the server-side
	static navigator.  Every client applies that same canonical x/z frame to its
	local map coordinates here; BigWorld terrain and entity queries remain local.
	"""
	try:
		import Math
		frame = _network_world_frame(player)
		if frame is None:
			return Math.Vector3(_finite_float(state.get('x')), _finite_float(state.get('y')), _finite_float(state.get('z')))
		unused_key, b1x, b1z, axis_x, axis_z, right_x, right_z, unused_yaw, formation = frame
		if bool(state.get('world_pose', False)):
			x = b1x + axis_x * _finite_float(state.get('z')) + right_x * _finite_float(state.get('x'))
			z = b1z + axis_z * _finite_float(state.get('z')) + right_z * _finite_float(state.get('x'))
			return Math.Vector3(x, _finite_float(state.get('y')), z)
		team = int(state.get('team', 1) or 1)
		slot = int(state.get('slot', 0) or 0)
		spawn_x = _finite_float(state.get('spawn_x', slot * 12.0))
		spawn_z = _finite_float(state.get('spawn_z', -35.0 if team == 1 else 35.0))
		base = formation(team, slot)
		base_x, base_z = float(base[0]), float(base[1])
		travel = _finite_float(state.get('z')) - spawn_z
		lateral = _finite_float(state.get('x')) - spawn_x
		x = base_x + axis_x * travel + right_x * lateral
		z = base_z + axis_z * travel + right_z * lateral
		y = _finite_float(state.get('y'))
		return Math.Vector3(x, y, z)
	except Exception:
		return None


def _ground_world_point(point):
	"""Resolve terrain height in the battle space, walking below roofs."""
	if point is None:
		return None
	try:
		import Math
		import sys
		module = sys.modules.get('gui.mods.offhangar.offline_battle')
		space_getter = getattr(module, '_offh_bspace', None) if module is not None else None
		if not callable(space_getter):
			return point
		ground_y = None
		from_y = 1000.0
		for unused in range(4):
			hit = BigWorld.wg_collideSegment(space_getter(),
				Math.Vector3(point.x, from_y, point.z),
				Math.Vector3(point.x, -1000.0, point.z), 128)
			if hit is None:
				break
			ground_y = hit[0].y
			below = BigWorld.wg_collideSegment(space_getter(),
				Math.Vector3(point.x, ground_y - 0.4, point.z),
				Math.Vector3(point.x, -1000.0, point.z), 128)
			if below is None or (ground_y - below[0].y) < 2.5:
				break
			from_y = ground_y - 0.4
		if ground_y is not None:
			point.y = ground_y
	except Exception:
		pass
	return point


def _network_mock_indexes():
	"""Build O(1) server-id indexes once for the current mock collection."""
	try:
		import sys
		module = sys.modules.get('gui.mods.offhangar.offline_battle')
		mocks = getattr(module, 'G_MOCK_VEHICLES', {}) if module is not None else {}
		generation = int(getattr(module, 'g_offh_battle_gen', 0) or 0)
		key = (generation, id(mocks), len(mocks or {}))
		cached = globals().get('_g_network_mock_indexes')
		if isinstance(cached, tuple) and len(cached) == 3 and cached[0] == key:
			return cached[1], cached[2]
		players = {}
		bots = {}
		for mock in (mocks or {}).values():
			server_id = getattr(mock, '_network_server_id', None)
			bot_id = getattr(mock, '_network_bot_id', None)
			if server_id is not None:
				players[server_id] = mock
			if bot_id is not None:
				try:
					bots[int(bot_id)] = mock
				except (TypeError, ValueError):
					pass
		globals()['_g_network_mock_indexes'] = (key, players, bots)
		return players, bots
	except Exception:
		return {}, {}


def _find_mock(player, server_id):
	mock = _network_mock_indexes()[0].get(server_id)
	if mock is not None:
		try:
			getattr(player, '_offhangar_network_pending_remote_ids', {}).pop(
				server_id, None)
		except Exception:
			pass
	return mock


def _find_bot(bot_id):
	try:
		return _network_mock_indexes()[1].get(int(bot_id))
	except (TypeError, ValueError):
		return None


def _world_yaw_from_server(player, state):
	"""Convert the server's synthetic yaw into the loaded map's yaw frame."""
	yaw = _finite_float(state.get('yaw'))
	try:
		frame = _network_world_frame(player)
	except Exception:
		frame = None
	if not bool(state.get('world_pose', False)):
		try:
			formation = getattr(player, '_offhangar_network_formation', None)
			if callable(formation):
				team = int(state.get('team', 1) or 1)
				slot = int(state.get('slot', 0) or 0)
				base = formation(team, slot)
				canonical_yaw = 0.0 if team == 1 else math.pi
				return float(base[2]) + yaw - canonical_yaw
		except Exception:
			pass
	if frame is None:
		return yaw
	return yaw + frame[7]


def _offline_mocks():
	try:
		import sys
		module = sys.modules.get('gui.mods.offhangar.offline_battle')
		return getattr(module, 'G_MOCK_VEHICLES', {}) if module is not None else {}
	except Exception:
		return {}


def _local_mock(player):
	try:
		return (_offline_mocks() or {}).get(getattr(player, 'playerVehicleID', -1))
	except Exception:
		return None


def _local_entity_id_for_server(player, server_id):
	if server_id == getattr(player, '_offhangar_network_id', None):
		return getattr(player, 'playerVehicleID', -1)
	mock = _find_mock(player, server_id)
	return getattr(mock, 'id', -1) if mock is not None else -1


def _local_killer_id_from_state(player, state):
	"""Resolve the server's stable killer identity into this client's entity id."""
	kind = str(state.get('killer_kind') or '')
	try:
		killer_id = int(state.get('killer_id', 0) or 0)
	except (TypeError, ValueError):
		killer_id = 0
	if kind == 'human' and killer_id:
		return _local_entity_id_for_server(player, killer_id)
	if kind == 'bot' and killer_id:
		mock = _find_bot(killer_id)
		return getattr(mock, 'id', -1) if mock is not None else -1
	# Protocol-5 compatibility with servers that only relay bot killers.
	legacy_bot_id = state.get('killer_bot_id')
	if legacy_bot_id not in (None, 0, '0'):
		mock = _find_bot(legacy_bot_id)
		return getattr(mock, 'id', -1) if mock is not None else -1
	return -1


def _set_remote_spot_visibility(player, mock, visible):
	"""Keep a remote human's model, marker and minimap state in lockstep."""
	if mock is None:
		return False
	visible = bool(visible)
	previous = bool(getattr(mock, '_spot_visible', False))
	if (previous == visible and
			getattr(mock, '_network_visibility_committed', False)):
		return visible
	mock._spot_visible = visible
	model = getattr(mock, '_chassis_model', None) or getattr(mock, 'model', None)
	if model is not None and getattr(mock, 'health', 0) > 0:
		try:
			model.visible = visible
			model.visibleAttachments = visible
		except Exception:
			pass
	try:
		from gui import WindowsManager
		battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
		markers = getattr(battle, 'vMarkersManager', None) if battle is not None else None
		marker = getattr(mock, 'marker', None)
		if markers is not None:
			if visible and marker in (None, -1) and getattr(mock, 'proxy', None) is not None:
				mock.marker = markers.createMarker(mock.proxy)
			elif not visible and marker not in (None, -1):
				markers.destroyMarker(marker)
				mock.marker = None
		if previous != visible:
			minimap = getattr(battle, 'minimap', None) if battle is not None else None
			if minimap is not None:
				if visible:
					minimap.notifyVehicleStart(mock.id)
				else:
					minimap.notifyVehicleStop(mock.id)
	except Exception:
		pass
	if previous != visible and getattr(mock, '_network_remote', False):
		try:
			local = _local_mock(player)
			distance = -1.0
			if local is not None:
				dx = float(mock.position.x) - float(local.position.x)
				dz = float(mock.position.z) - float(local.position.z)
				distance = math.sqrt(dx * dx + dz * dz)
			LOG_NOTE('LAN remote human visibility server_id=%s visible=%s distance=%.1fm' % (
				str(getattr(mock, '_network_server_id', '?')),
				str(bool(visible)), distance))
		except Exception:
			pass
	mock._network_visibility_committed = True
	return visible


def _remote_proximity_visibility(player, mock, now):
	"""Preserve the stock 50 m proximity rule if the shared adapter is unavailable."""
	local = _local_mock(player)
	if local is None or getattr(local, 'position', None) is None:
		return False
	dx = float(mock.position.x) - float(local.position.x)
	dz = float(mock.position.z) - float(local.position.z)
	visible = dx * dx + dz * dz <= 2500.0
	if visible:
		mock._spot_until = max(
			float(getattr(mock, '_spot_until', 0.0) or 0.0), float(now) + 5.0)
	return visible


def update_remote_spotting(player, mock, force=False):
	"""Apply the shared 0.8.2 spotting result to one LAN vehicle."""
	if player is None or mock is None:
		return False
	alive = bool(getattr(mock, 'isAlive', True)) and int(getattr(mock, 'health', 0) or 0) > 0
	if not alive:
		return bool(getattr(mock, '_spot_visible', False))
	player_team = int(getattr(player, '_offhangar_team',
		getattr(player, '_offhangar_network_team', 1)) or 1)
	remote_team = int(getattr(mock, '_bot_team',
		(getattr(mock, 'publicInfo', None) or {}).get('team', 2)) or 2)
	if remote_team == player_team or not bool(_network_config().get('spotting_enabled', True)):
		return _set_remote_spot_visibility(player, mock, True)
	try:
		now = float(BigWorld.time())
	except Exception:
		now = time.time()
	next_check = float(getattr(mock, '_network_spot_next', 0.0) or 0.0)
	if not force and now < next_check:
		visible = now < float(getattr(mock, '_spot_until', 0.0) or 0.0)
		if not getattr(mock, '_network_visibility_committed', False):
			return _set_remote_spot_visibility(player, mock, visible)
		return visible
	mock._network_spot_next = now + 0.5
	visible = False
	try:
		import sys
		offline = sys.modules.get('gui.mods.offhangar.offline_battle')
		evaluate = getattr(offline, '_offh_spot_visible_for_player', None)
		if callable(evaluate):
			visible = bool(evaluate(player, mock, now))
		else:
			# Source-loader safety net: proximity spotting must still work if the
			# shared battle adapter failed to load. Normal installs never use this.
			visible = _remote_proximity_visibility(player, mock, now)
	except Exception as error:
		# A spotting data bug must not make a nearby LAN player permanently
		# invisible. Log the first failure with its actual cause and retain only the
		# native 50 m proximity guarantee until the next successful shared pass.
		if not getattr(mock, '_network_spot_error_logged', False):
			mock._network_spot_error_logged = True
			LOG_ERROR('LAN remote spotting failed server_id=%s: %s' % (
				str(getattr(mock, '_network_server_id', '?')), str(error)))
		visible = _remote_proximity_visibility(player, mock, now)
	return _set_remote_spot_visibility(player, mock, visible)


def _notify_network_death(player, mock, killer_id=-1):
	if mock is None or getattr(mock, '_network_death_notified', False):
		return False
	if killer_id in (None, -1):
		killer_id = getattr(mock, 'last_killer_id', -1)
	if killer_id is None:
		killer_id = -1
	mock._network_death_pending = False
	mock._network_death_notified = True
	try:
		player.arena.onVehicleKilled(mock.id, killer_id, 0)
	except Exception:
		try:
			mock.isAlive = False
		except Exception:
			pass
	return True


def _push_mock_health(player, mock, health, max_health, alive, killer_id=-1, is_local=False):
	if mock is None:
		return
	old_health = int(getattr(mock, 'health', health) or 0)
	old_max_health = int(getattr(mock, 'maxHealth', max_health) or 1)
	old_alive = bool(getattr(mock, 'isAlive', old_health > 0))
	health = max(0, int(health or 0))
	max_health = max(1, int(max_health or getattr(mock, 'maxHealth', 1) or 1))
	if health > max_health:
		health = max_health
	alive = bool(alive) and health > 0
	if (old_health == health and old_max_health == max_health and
			old_alive == alive and killer_id in (None, -1)):
		return
	mock.health = health
	mock.maxHealth = max_health
	if killer_id not in (None, -1):
		try:
			mock.last_killer_id = killer_id
		except Exception:
			pass
	if getattr(mock, 'publicInfo', None) is not None:
		mock.publicInfo['isAlive'] = alive
	try:
		from gui import WindowsManager
		battle = getattr(WindowsManager.g_windowsManager, 'battleWindow', None)
		if is_local:
			try:
				if getattr(player, 'vehicle', None) is not None:
					player.vehicle.health = health
			except Exception:
				pass
			damage_panel = getattr(battle, 'damagePanel', None) if battle is not None else None
			if damage_panel is not None and old_health != health:
				damage_panel.updateHealth(health)
			try:
				player.guiSessionProvider.invalidateVehicleState(
					1, player.playerVehicleID, health, health)
			except Exception:
				pass
		else:
			markers = getattr(battle, 'vMarkersManager', None) if battle is not None else None
			marker = getattr(mock, 'marker', None)
			if markers is not None and marker not in (None, -1) and old_health != health:
				markers.onVehicleHealthChanged(marker, health, killer_id, 0)
	except Exception:
		pass
	if not alive or health <= 0:
		mock.health = 0
		if not getattr(mock, '_network_death_notified', False):
			if killer_id not in (None, -1):
				_notify_network_death(player, mock, killer_id)
			elif not getattr(mock, '_network_death_pending', False):
				# Snapshots and combat events are separate messages. The server sends the
				# snapshot first, so leave one short window for the following event to
				# supply its killer instead of permanently posting "lost in battle".
				mock._network_death_pending = True
				try:
					BigWorld.callback(0.5, lambda: _notify_network_death(player, mock, -1))
				except Exception:
					_notify_network_death(player, mock, -1)
	elif not old_alive:
		try:
			mock.isAlive = True
		except Exception:
			pass


def _remote_pose_space_id():
	try:
		import sys
		offline = sys.modules.get('gui.mods.offhangar.offline_battle')
		space_getter = (getattr(offline, '_offh_bspace', None)
		                if offline is not None else None)
		if callable(space_getter):
			return space_getter()
	except Exception:
		pass
	return None


def _apply_remote_transform(player, mock, world, yaw, sync_filter=True,
		timestamp=None, space_id=None):
	"""Commit one remote pose through the shared native-facing adapter."""
	if mock is None or world is None:
		return False
	if getattr(mock, '_offh_native_model_root_ready', False) is not True:
		# Entity.model already installed BigWorld's default motor, but the retail
		# handoff has not yet removed it. Do not attach a relay Servo beside it.
		return False
	pitch = float(getattr(mock, 'pitch', 0.0) or 0.0)
	roll = float(getattr(mock, 'roll', 0.0) or 0.0)
	if sync_filter and space_id is None:
		space_id = _remote_pose_space_id()
	if sync_filter and timestamp is None:
		timestamp = BigWorld.time()
	needs_servo = not bool(getattr(mock, '_servo_added', False))
	try:
		committed = vehicle_pose.commit_pose(
			mock, world, yaw, pitch, roll, space_id=space_id,
			timestamp=timestamp, sync_filter=sync_filter,
			attach_servo=needs_servo, prime_model=needs_servo)
	except Exception:
		return False
	if not committed:
		return False
	pose_servo = getattr(mock, '_pose_servo', None)
	if needs_servo and (not getattr(mock, '_servo_added', False) or
			pose_servo is None):
		return False
	if getattr(mock, '_servo_added', False):
		try:
			motors = list((getattr(mock, '_chassis_model', None) or
				getattr(mock, 'model', None)).motors)
		except Exception:
			return False
		if len(motors) != 1 or motors[0] is not pose_servo:
			return False
	return True


def _queue_network_transform(player, mock, world, hull_yaw, aim_yaw,
		gun_pitch, snap=False, longitudinal_speed=None, turn_velocity=None,
		sample_time=None):
	"""Queue a 30 Hz pose; the battle frame loop renders between packets."""
	if mock is None or world is None:
		return False
	now = time.time() if sample_time is None else float(sample_time)
	previous_target = getattr(mock, '_network_target_position', None)
	previous_time = float(getattr(mock, '_network_target_time', 0.0) or 0.0)
	if longitudinal_speed is not None:
		speed = max(-80.0, min(80.0, _finite_float(longitudinal_speed, 0.0)))
		mock._network_target_velocity = (
			math.sin(hull_yaw) * speed, 0.0, math.cos(hull_yaw) * speed)
	elif previous_target is not None and previous_time > 0.0 and now > previous_time:
		delta = max(0.01, min(now - previous_time, 0.25))
		vx = (world.x - previous_target.x) / delta
		vy = (world.y - previous_target.y) / delta
		vz = (world.z - previous_target.z) / delta
		speed = math.sqrt(vx * vx + vy * vy + vz * vz)
		if speed > 80.0:
			scale = 80.0 / speed
			vx *= scale
			vy *= scale
			vz *= scale
		mock._network_target_velocity = (vx, vy, vz)
	else:
		mock._network_target_velocity = (0.0, 0.0, 0.0)
	mock._network_target_position = world
	mock._network_target_yaw = hull_yaw
	mock._network_target_aim_yaw = aim_yaw
	mock._network_target_gun_pitch = gun_pitch
	if turn_velocity is not None:
		mock._network_target_turn_velocity = max(
			-10.0, min(10.0, _finite_float(turn_velocity, 0.0)))
	mock._network_target_time = now
	if snap or not getattr(mock, '_network_smoothing_ready', False):
		if not _apply_remote_transform(player, mock, world, hull_yaw):
			return False
		mock._turret_yaw = aim_yaw - hull_yaw
		mock._gun_pitch = gun_pitch
		try:
			mock._t_mat.setRotateYPR((mock._turret_yaw, 0, 0))
		except Exception:
			pass
		try:
			mock._g_mat.setRotateYPR((0, mock._gun_pitch, 0))
		except Exception:
			pass
		mock._network_smoothing_ready = True
	return True


def _short_angle_delta(target, current):
	delta = target - current
	while delta > math.pi:
		delta -= math.pi * 2.0
	while delta < -math.pi:
		delta += math.pi * 2.0
	return delta


def advance_network_smoothing(player, mocks, frame_dt):
	"""Interpolate and briefly predict remote humans/bots every render frame."""
	try:
		import Math
		dt = max(0.001, min(_finite_float(frame_dt, 0.016), 0.1))
		alpha = 1.0 - math.exp(-20.0 * dt)
		now = time.time()
		is_authority = network_is_authority(player)
		lineup_loading = _replica_lineup_loading(player)
		space_id = _remote_pose_space_id()
		pose_timestamp = BigWorld.time()
		pose_safe = None
		try:
			import sys
			offline = sys.modules.get('gui.mods.offhangar.offline_battle')
			pose_safe = (getattr(offline, '_offh_ai_baked_pose_safe', None)
			             if offline is not None else None)
		except Exception:
			pass
		for mock in (mocks or {}).values():
			is_human = bool(getattr(mock, '_network_remote', False))
			is_bot = bool(getattr(mock, '_network_shared_bot', False))
			if not is_human and not (is_bot and not is_authority):
				continue
			if is_bot and lineup_loading:
				# Entity creation owns the model until the complete lineup exists. Applying
				# 30 Hz transforms to each partially constructed relay model made native
				# model submission and the next spawn callback starve one another.
				continue
			if getattr(mock, '_network_death_notified', False) or not getattr(mock, 'isAlive', True):
				continue
			target = getattr(mock, '_network_target_position', None)
			current = getattr(mock, 'position', None)
			if target is None or current is None:
				continue
			vx, vy, vz = getattr(mock, '_network_target_velocity', (0.0, 0.0, 0.0))
			# Explicit authority velocity makes prediction useful even when the
			# authority renders below 30 FPS. Keep the LAN horizon bounded so a lost
			# packet cannot make a tank coast indefinitely.
			predict = max(0.0, min(
				now - float(getattr(mock, '_network_target_time', now)), 0.12))
			px = target.x + vx * predict
			py = target.y + vy * predict
			pz = target.z + vz * predict
			if is_bot and predict > 0.0 and callable(pose_safe):
				# Prediction is presentation only. Never extrapolate a shared bot from
				# its last authoritative safe pose across a baked water/cliff cell; the
				# next zero-speed guard packet would otherwise make it rubber-band back.
				# If the authority itself has already fallen, its target pose still wins
				# and the physical consequence remains visible on every client.
				try:
					if not pose_safe((px, py, pz)):
						px, py, pz = target.x, target.y, target.z
				except Exception:
					pass
			dx, dy, dz = px - current.x, py - current.y, pz - current.z
			distance_sq = dx * dx + dy * dy + dz * dz
			target_yaw = _finite_float(getattr(mock, '_network_target_yaw', mock.yaw))
			if distance_sq > 625.0:
				world = Math.Vector3(px, py, pz)
				yaw = target_yaw
			elif distance_sq <= 0.0004:
				# Finish the exponential tail once it is below two centimetres. Without
				# this, stationary tanks still rewrote three native matrices forever.
				world = Math.Vector3(px, py, pz)
				yaw = target_yaw
			else:
				world = Math.Vector3(current.x + dx * alpha,
					current.y + dy * alpha, current.z + dz * alpha)
				yaw = mock.yaw + _short_angle_delta(target_yaw, mock.yaw) * alpha
			pose_changed = (
				distance_sq > 0.000001 or
				abs(_short_angle_delta(yaw, mock.yaw)) > 0.00001)
			filter_due = now >= float(getattr(
				mock, '_network_filter_sync_at', 0.0) or 0.0)
			if filter_due:
				mock._network_filter_sync_at = now + (1.0 / 30.0)
			if pose_changed or filter_due:
				_apply_remote_transform(
					player, mock, world, yaw, filter_due,
					pose_timestamp, space_id)
			target_aim = _finite_float(getattr(mock, '_network_target_aim_yaw', yaw))
			desired_turret = _short_angle_delta(target_aim, yaw)
			current_turret = _finite_float(getattr(mock, '_turret_yaw', desired_turret))
			new_turret = current_turret + _short_angle_delta(
				desired_turret, current_turret) * alpha
			current_pitch = _finite_float(getattr(mock, '_gun_pitch', 0.0))
			target_pitch = _finite_float(getattr(mock, '_network_target_gun_pitch', current_pitch))
			new_pitch = current_pitch + (target_pitch - current_pitch) * alpha
			if abs(new_turret - current_turret) > 0.00001:
				mock._turret_yaw = new_turret
				try:
					mock._t_mat.setRotateYPR((new_turret, 0, 0))
				except Exception:
					pass
			if abs(new_pitch - current_pitch) > 0.00001:
				mock._gun_pitch = new_pitch
				try:
					mock._g_mat.setRotateYPR((0, new_pitch, 0))
				except Exception:
					pass
		return True
	except Exception:
		return False


def _advance_local_server_health(player, server_health):
	"""Advance the round's server HP baseline without accepting stale increases."""
	server_health = max(0, int(server_health))
	previous = getattr(player, '_offhangar_network_server_health', None)
	if previous is None:
		player._offhangar_network_server_health = server_health
		return None
	previous = max(0, int(previous))
	if server_health >= previous:
		return 0
	player._offhangar_network_server_health = server_health
	return previous - server_health


def _fence_local_health_round(player, round_id):
	"""Reset newer-round HP accounting and reject stale-round snapshots."""
	if round_id is None:
		return True
	try:
		round_id = int(round_id)
	except Exception:
		return True
	current = getattr(player, '_offhangar_network_health_round_id', None)
	if current is not None and round_id < int(current):
		return False
	if current is None or round_id > int(current):
		player._offhangar_network_health_round_id = round_id
		player._offhangar_network_server_health = None
	return True


def _apply_local_state(player, state):
	mock = _local_mock(player)
	if mock is None:
		return
	server_health = int(state.get('health', getattr(mock, 'health', 0)) or 0)
	delta = _advance_local_server_health(player, server_health)
	if delta is None:
		target_health = min(
			int(getattr(mock, 'health', server_health) or 0), server_health)
	else:
		target_health = max(0,
			int(getattr(mock, 'health', server_health) or 0) - delta)
	if not bool(state.get('alive', True)):
		target_health = 0
	_push_mock_health(player, mock, target_health,
		state.get('max_health', getattr(mock, 'maxHealth', 1)),
		bool(state.get('alive', True)), _local_killer_id_from_state(player, state), True)


def _apply_remote_state(player, state):
	server_id = state.get('id')
	if server_id is None or server_id == getattr(player, '_offhangar_network_id', None):
		return
	mock = _find_mock(player, server_id)
	if mock is None:
		pending = getattr(player, '_offhangar_network_pending_remote_ids', None)
		if pending is None:
			pending = {}
			player._offhangar_network_pending_remote_ids = pending
		pending_since = pending.get(server_id)
		if pending_since is not None and time.time() - pending_since < 30.0:
			return
		pending[server_id] = time.time()
		spawn = getattr(player, '_offhangar_network_spawn_remote', None)
		formation = getattr(player, '_offhangar_network_formation', None)
		if callable(spawn) and callable(formation):
			team = int(state.get('team', 2) or 2)
			slot = int(state.get('slot', 0) or 0)
			sx, sz, syaw = formation(team, slot)
			try:
				import Math
				# Map the server's synthetic coordinates onto this client's real
				# arena.  y is resolved by the existing ground probe in the spawn
				# helper, so a stale/missing terrain height cannot float the tank.
				point = _world_from_server(player, state)
				if point is None:
					point = Math.Vector3(float(sx), 0.0, float(sz))
				point = _ground_world_point(point)
			except Exception:
				point = None
			if point is not None:
				player._offhangar_network_forced_id = server_id
				player._offhangar_network_forced_state = state
				player._offhangar_network_forced_name = state.get('name') or ('Remote_%s' % server_id)
				player._forced_spawn_pos = (point.x, point.y, point.z)
				player._forced_spawn_team = team
				player._forced_spawn_yaw = _world_yaw_from_server(player, state)
				player._forced_spawn_vehname = state.get('vehicle') or 'ussr:MS-1'
				try:
					spawn_result = spawn(_NetworkSpawnEvent())
					if spawn_result is False:
						pending.pop(server_id, None)
				except Exception:
					pending.pop(server_id, None)
					LOG_ERROR('LAN remote spawn failed:', server_id)
				finally:
					player._forced_spawn_pos = None
					player._forced_spawn_team = None
					player._forced_spawn_yaw = None
					player._forced_spawn_vehname = None
					player._offhangar_network_forced_id = None
					player._offhangar_network_forced_name = None
					player._offhangar_network_forced_state = None
			return
		pending.pop(server_id, None)
		return
	try:
		death_locked = bool(getattr(mock, '_network_death_notified', False))
		target_alive = bool(state.get('alive', True))
		world_yaw = _world_yaw_from_server(player, state)
		world_aim_yaw = _world_yaw_from_server(player, dict(state, yaw=state.get('aim_yaw')))
		world = _world_from_server(player, state)
		if world is not None and abs(_finite_float(state.get('y'))) < 0.001:
			# The server has no map terrain. Retry the local ground probe instead of
			# preserving a failed sky-high async spawn forever.
			world = _ground_world_point(world)
		# Apply the final death snapshot once, then freeze both mock and model.
		# The input sender can run for a few frames after death; moving only the
		# marker proxy while the destroyed model stays put split them apart.
		if not death_locked:
			_queue_network_transform(player, mock, world, world_yaw, world_aim_yaw,
				_finite_float(state.get('gun_pitch'), getattr(mock, '_gun_pitch', 0.0)),
				not target_alive)
		_push_mock_health(player, mock, state.get('health', mock.health),
			state.get('max_health', mock.maxHealth), target_alive,
			_local_killer_id_from_state(player, state))
		# Remote-human visibility used to depend on the separate 2 Hz bot/contact
		# pass. If model loading stalled that pass during countdown, this entity
		# could retain its initial hidden state for the rest of the battle. Every
		# authoritative player snapshot now refreshes the shared spotting adapter;
		# its own 0.5 s gate keeps this cheap at the 30 Hz transport rate.
		force_spot = not bool(getattr(mock, '_network_spot_initialized', False))
		update_remote_spotting(player, mock, force_spot)
		mock._network_spot_initialized = True
	except Exception:
		pass


def _apply_bot_state(player, state, force_authority_pose=False, mock=None,
		sample_time=None):
	if mock is None:
		mock = _find_bot(state.get('id'))
	if mock is None:
		return False
	try:
		is_authority = network_is_authority(player)
		elected_authority = bool(getattr(
			player, '_offhangar_network_is_authority', False))
		authority_fast_path = is_authority and not force_authority_pose
		# Static A* is resolved by the Python 3 server over the shipped graph.  This
		# short waypoint is advisory: the authority's LocalDriver still performs the
		# exact BigWorld corridor, destructible-object, water and tank checks before
		# committing motion.  Replica clients retain it only for a possible failover.
		nav_source = str(state.get('nav_source') or '')
		if nav_source in ('server_baked', 'server_hold'):
			nav_revision = int(state.get('nav_order_revision', 0) or 0)
			nav_key = (
				nav_source, nav_revision, state.get('nav_x'),
				state.get('nav_y'), state.get('nav_z'))
			nav_cache = getattr(mock, '_network_authority_navigation_cache', None)
			nav_reused = bool(
				authority_fast_path and isinstance(nav_cache, tuple) and
				len(nav_cache) == 2 and nav_cache[0] == nav_key and
				getattr(mock, '_network_navigation_source', None) == nav_source and
				getattr(mock, '_network_navigation_revision', None) == nav_revision and
				getattr(mock, '_network_navigation_target', None) == nav_cache[1])
			if nav_reused:
				# Navigation freshness is level-triggered even when the immutable
				# server waypoint needs no second coordinate conversion.
				mock._network_navigation_time = float(sample_time or time.time())
				player._offhangar_network_server_navigation_at = float(
					sample_time or time.time())
			else:
				nav_state = {
					'world_pose': True,
					'x': state.get('nav_x'), 'y': state.get('nav_y'),
					'z': state.get('nav_z'),
				}
				nav_world = _world_from_server(player, nav_state)
				if nav_world is not None:
					nav_target = (
						float(nav_world.x), float(nav_world.y), float(nav_world.z))
					mock._network_navigation_target = nav_target
					mock._network_navigation_source = nav_source
					mock._network_navigation_revision = nav_revision
					mock._network_navigation_time = float(sample_time or time.time())
					player._offhangar_network_server_navigation_at = float(
						sample_time or time.time())
					if is_authority:
						mock._network_authority_navigation_cache = (
							nav_key, nav_target)
					if not getattr(player, '_offhangar_network_server_navigation_logged', False):
						player._offhangar_network_server_navigation_logged = True
						LOG_NOTE('LAN server-baked navigation waypoints active')
		elif nav_source == 'client_fallback':
			nav_revision = int(state.get('nav_order_revision', 0) or 0)
			mock._network_navigation_target = None
			mock._network_navigation_source = nav_source
			mock._network_navigation_revision = nav_revision
			mock._network_navigation_time = float(sample_time or time.time())
		old_health = max(0, int(getattr(mock, 'health', 0) or 0))
		_apply_mobility_snapshot(
			mock, state, force_authority_pose, sample_time or time.time())
		target_alive = bool(state.get('alive', True))
		if not is_authority or force_authority_pose:
			previous_fire = int(getattr(mock, '_network_seen_fire_seq', 0) or 0)
			fire_seq = int(state.get('fire_seq', previous_fire) or 0)
			death_locked = bool(getattr(mock, '_network_death_notified', False))
			if (force_authority_pose and target_alive and
					(death_locked or
					 not bool(getattr(mock, 'isAlive', True)) or
					 int(getattr(mock, 'health', 0) or 0) <= 0)):
				# Server bot HP is monotonic. A canonical handoff cannot legitimately
				# resurrect a locally settled death without also rolling back its frag,
				# score, kill feed and wreck transaction. Keep the ownership fence closed
				# instead of creating a half-revived bot.
				LOG_ERROR('LAN authority handoff rejected bot resurrection:',
				          state.get('id'))
				return False
			world = _world_from_server(player, state)
			world_yaw = _world_yaw_from_server(player, state)
			world_aim_yaw = _world_yaw_from_server(player,
				dict(state, yaw=state.get('aim_yaw')))
			transform_applied = True
			if not death_locked:
				transform_applied = _queue_network_transform(
					player, mock, world, world_yaw, world_aim_yaw,
					_finite_float(state.get('gun_pitch'), getattr(mock, '_gun_pitch', 0.0)),
					force_authority_pose or not target_alive,
					state.get('speed'), state.get('turn_velocity'), sample_time)
			if force_authority_pose and not transform_applied:
				return False
			if force_authority_pose:
				mock._veh_velocity = max(-80.0, min(
					80.0, _finite_float(state.get('speed'), 0.0)))
				mock._veh_turn_velocity = max(-10.0, min(
					10.0, _finite_float(state.get('turn_velocity'), 0.0)))
			if (not elected_authority and not force_authority_pose and
					fire_seq > previous_fire):
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_remote_shot', None) if offline is not None else None
					if callable(present) and world is not None:
						present(mock, world, world_aim_yaw, mock._gun_pitch,
							state.get('shell_index', 0))
				except Exception:
					LOG_ERROR('LAN bot shot presentation failed:', state.get('id'))
			mock._network_seen_fire_seq = fire_seq
			mock._network_bot_fire_seq = max(
				int(getattr(mock, '_network_bot_fire_seq', 0) or 0), fire_seq)
			mock._network_bot_shell_index = int(state.get('shell_index', 0) or 0)
		if authority_fast_path:
			incoming_health = max(0, int(state.get('health', mock.health) or 0))
			incoming_max_health = max(1, int(
				state.get('max_health', mock.maxHealth) or
				getattr(mock, 'maxHealth', 1) or 1))
			if incoming_health > incoming_max_health:
				incoming_health = incoming_max_health
			# The authority applies combat locally before its next state reaches the
			# server. A steady-state echo is therefore allowed to confirm damage, but
			# never to heal or resurrect the locally authoritative bot. Exact server
			# state is accepted only by the force_authority_pose handoff path below.
			current_health = max(0, int(getattr(mock, 'health', 0) or 0))
			current_alive = bool(getattr(mock, 'isAlive', current_health > 0))
			incoming_health = min(current_health, incoming_health)
			incoming_alive = (
				current_alive and target_alive and incoming_health > 0)
			current_health_matches = (
				int(getattr(mock, 'health', incoming_health) or 0) ==
					incoming_health and
				int(getattr(mock, 'maxHealth', incoming_max_health) or 1) ==
					incoming_max_health and
				bool(getattr(mock, 'isAlive', incoming_health > 0)) == incoming_alive)
			has_killer = bool(
				state.get('killer_kind') or
				state.get('killer_id') not in (None, 0, '0') or
				state.get('killer_bot_id') not in (None, 0, '0'))
			# _push_mock_health is already idempotent for an unchanged state without a
			# killer. Avoid its repeated identity lookup and UI imports on the authority,
			# while every death/killer payload keeps the original complete path.
			if not (current_health_matches and not has_killer):
				killer_id = _local_killer_id_from_state(player, state)
				_push_mock_health(player, mock, incoming_health,
					incoming_max_health, incoming_alive, killer_id)
		else:
			killer_id = _local_killer_id_from_state(player, state)
			_push_mock_health(player, mock, state.get('health', mock.health),
				state.get('max_health', mock.maxHealth), target_alive, killer_id)
		if not elected_authority and not force_authority_pose:
			new_health = max(0, int(getattr(mock, 'health', 0) or 0))
			if old_health > new_health:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					record_assist = getattr(offline, 'record_network_spot_assist', None) if offline is not None else None
					if callable(record_assist):
						record_assist(player, mock, old_health - new_health, not target_alive)
				except Exception:
					LOG_ERROR('LAN spotting-assist statistics failed:', state.get('id'))
			update_remote_spotting(player, mock)
		return True
	except Exception:
		LOG_ERROR('LAN bot state apply failed:', state.get('id'))
		return False


def _apply_snapshot(player, message):
	if not _fence_local_health_round(player, message.get('round_id')):
		return
	snapshot_time = time.time()
	for state in message.get('players') or []:
		if state.get('id') == getattr(player, '_offhangar_network_id', None):
			_apply_local_state(player, state)
		else:
			_apply_remote_state(player, state)
	handoff_pending = bool(getattr(
		player, '_offhangar_network_authority_handoff_pending', False))
	# A promoted authority must consume one complete canonical relay pose before
	# local simulation takes ownership. Compact steady-state authority snapshots
	# deliberately omit pose, so they may refresh HP/navigation but cannot satisfy
	# this ownership fence. Missing mode is the legacy full-snapshot contract.
	snapshot_mode = str(message.get('bot_snapshot_mode') or 'full')
	handoff = handoff_pending and snapshot_mode == 'full'
	rules_apply_ok = not handoff
	rules = message.get('rules')
	if rules is not None:
		callback = getattr(player, '_offhangar_apply_network_rules_state', None)
		if callable(callback):
			try:
				# A promoted authority is still fenced here, so it consumes the
				# canonical rules paired with the full pose handoff before it may
				# publish a newer state. Steady authorities remain authoritative.
				rules_result = callback(rules)
				if handoff:
					rules_apply_ok = rules_result is True
			except Exception:
				if handoff:
					rules_apply_ok = False
				LOG_ERROR('LAN rules state apply failed')
	bot_states = message.get('bots') or []
	# One failed server path must reactivate the client navigator for that bot.
	# Using only the latest successful waypoint timestamp made 28 successful bots
	# mask one ``client_fallback`` forever.
	alive_bot_states = [state for state in bot_states
		if bool(state.get('alive', True))]
	player._offhangar_network_server_navigation_complete = bool(
		alive_bot_states and all(str(state.get('nav_source') or '') in
			('server_baked', 'server_hold') for state in alive_bot_states))
	defer_bot_states = bool(bot_states and _replica_lineup_loading(player))
	if defer_bot_states:
		# Snapshots are already level-triggered and coalesced by LANClient._poll.
		# Player state, timer and rules continue below, while bot transforms wait for
		# the complete native lineup. The first 30 Hz snapshot after completion is the
		# canonical newest state, so replaying stale intermediate poses is unnecessary.
		player._offhangar_network_bot_snapshots_deferred = True
		if not getattr(player, '_offhangar_network_bot_defer_logged', False):
			player._offhangar_network_bot_defer_logged = True
			LOG_NOTE('LAN replica bot snapshots deferred until lineup is ready')
	else:
		if getattr(player, '_offhangar_network_bot_snapshots_deferred', False):
			player._offhangar_network_bot_snapshots_deferred = False
			LOG_NOTE('LAN replica bot snapshots resumed with complete lineup')
		bot_index = _network_mock_indexes()[1]
		handoff_applied = set()
		for state in bot_states:
			try:
				state_bot_id = int(state.get('id'))
				indexed_mock = bot_index.get(state_bot_id)
			except (TypeError, ValueError):
				state_bot_id = None
				indexed_mock = None
			if _apply_bot_state(
					player, state, handoff, indexed_mock, snapshot_time):
				if handoff and state_bot_id is not None:
					handoff_applied.add(state_bot_id)
		if handoff:
			expected_handoff = set()
			for entry in (getattr(
					player, '_offhangar_network_bot_manifest', None) or []):
				try:
					expected_handoff.add(int(entry.get('id')))
				except (AttributeError, TypeError, ValueError):
					pass
			# The server full snapshot contains every canonical bot. Do not acquire
			# native ownership until the complete manifest is mapped/applied and this
			# newly promoted client has proved live terrain collision for its tenure.
			_handoff_complete = bool(
				rules_apply_ok and (
					(not expected_handoff and not bot_states) or
					(expected_handoff and handoff_applied == expected_handoff and
						all(bot_id in bot_index for bot_id in expected_handoff))))
			if _handoff_complete:
				_streaming_ready = False
				_streaming_callback = getattr(
					player, '_offhangar_prepare_native_authority_streaming', None)
				if callable(_streaming_callback):
					try:
						_streaming_ready = _streaming_callback() is True
					except Exception:
						LOG_ERROR('LAN authority streaming gate failed')
				if _streaming_ready:
					player._offhangar_network_authority_handoff_pending = False
	result = message.get('battle_result')
	if result is not None and not getattr(player, '_offhangar_network_result_applied', False):
		callback = getattr(player, '_offhangar_apply_network_battle_result', None)
		if callable(callback):
			player._offhangar_network_result_applied = True
			callback(result)


def _apply_capture_reset_event(player, target_mock, event, attacker=None):
	if target_mock is None:
		return
	damage = max(0, int(event.get('damage', 0) or 0))
	critical = bool(event.get('critical', False))
	if not bool(event.get('capture_reset', damage > 0 or critical)):
		return
	try:
		import sys
		offline = sys.modules.get('gui.mods.offhangar.offline_battle')
		callback = getattr(offline, 'apply_network_capture_damage', None) if offline is not None else None
		if callable(callback):
			callback(player, target_mock, attacker, damage, critical,
				'LAN %s' % str(event.get('kind') or 'hit'))
	except Exception:
		LOG_ERROR('LAN capture reset event failed:', event.get('kind'))


def _handle_events(player, events):
	for event in events:
		kind = event.get('kind')
		if kind == 'authority':
			# The paired snapshot is the role source of truth. The game-thread poller
			# coalesces snapshots but preserves reliable events, so applying this event
			# directly could let a newer event run before an older coalesced snapshot
			# and flip native ownership twice in one poll.
			try:
				player._offhangar_network_announced_authority_id = int(
					event.get('player_id'))
			except (TypeError, ValueError):
				player._offhangar_network_announced_authority_id = None
		elif kind == 'bot_manifest':
			player._offhangar_network_bot_manifest = event.get('bots') or []
			LOG_NOTE('LAN bot manifest received: %d bot(s)' % len(
				player._offhangar_network_bot_manifest))
		elif kind == 'battle_result':
			if not getattr(player, '_offhangar_network_result_applied', False):
				callback = getattr(player, '_offhangar_apply_network_battle_result', None)
				if callable(callback):
					player._offhangar_network_result_applied = True
					callback(event)
		elif kind == 'shot':
			attacker_server_id = event.get('attacker')
			if attacker_server_id == getattr(player, '_offhangar_network_id', None):
				continue
			attacker_mock = _find_mock(player, attacker_server_id)
			if attacker_mock is None:
				continue
			try:
				import sys
				offline = sys.modules.get('gui.mods.offhangar.offline_battle')
				present = getattr(offline, 'play_network_remote_shot', None) if offline is not None else None
				start = _world_from_server(player, event)
				aim_yaw = _world_yaw_from_server(player, dict(event, yaw=event.get('aim_yaw')))
				if callable(present) and start is not None:
					present(attacker_mock, start, aim_yaw,
						_finite_float(event.get('gun_pitch')), event.get('shell_index', 0))
			except Exception:
				LOG_ERROR('LAN remote shot presentation failed')
		elif kind == 'hit':
			LOG_DEBUG('LAN hit attacker=%s target=%s damage=%s' % (
				event.get('attacker'), event.get('target'), event.get('damage')))
			target_id = event.get('target')
			attacker_server_id = event.get('attacker')
			attacker_id = _local_entity_id_for_server(player, event.get('attacker'))
			if target_id == getattr(player, '_offhangar_network_id', None):
				mock = _local_mock(player)
				is_local = True
			else:
				mock = _find_mock(player, target_id)
				is_local = False
			if mock is not None:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_hit_feedback', None) if offline is not None else None
					attacker_mock = (_local_mock(player) if attacker_server_id == getattr(
						player, '_offhangar_network_id', None) else _find_mock(player, attacker_server_id))
					hit_pos = _world_from_server(player, event)
					if callable(present):
						present(player, attacker_mock, mock, hit_pos,
							event.get('shot_result', 2), event.get('damage', 0),
							event.get('shell_index', 0), is_local,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN hit presentation failed')
				try:
					record_stats = getattr(offline, 'record_network_combat_stats', None) if offline is not None else None
					if callable(record_stats):
						record_stats(player,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							is_local, mock, event.get('damage', 0),
							event.get('shot_result', 2), bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN hit statistics failed')
				server_health = int(event.get('health', getattr(mock, 'health', 0)) or 0)
				if is_local:
					delta = _advance_local_server_health(player, server_health)
					if delta is None:
						health = max(0, int(getattr(mock, 'health', 0) or 0) - int(event.get('damage', 0) or 0))
					else:
						health = max(0,
							int(getattr(mock, 'health', 0) or 0) - delta)
				else:
					health = server_health
				_push_mock_health(player, mock, health,
					getattr(mock, 'maxHealth', max(1, health)),
					not bool(event.get('dead', False)), attacker_id, is_local)
				_apply_capture_reset_event(
					player, mock, event, attacker_server_id)
		elif kind == 'health':
			target_id = event.get('target')
			server_health = int(event.get('health', 0) or 0)
			if target_id == getattr(player, '_offhangar_network_id', None):
				# The local simulation already applied this damage and its effects.
				_advance_local_server_health(player, server_health)
				canonical_health = int(getattr(
					player, '_offhangar_network_server_health', server_health) or 0)
				mock = _local_mock(player)
				if mock is not None:
					_push_mock_health(player, mock,
						min(int(getattr(mock, 'health', canonical_health) or 0),
							canonical_health),
						getattr(mock, 'maxHealth', max(1, server_health)),
						not bool(event.get('dead', False)), -1, True)
					_apply_capture_reset_event(player, mock, event)
			else:
				mock = _find_mock(player, target_id)
				if mock is not None:
					_push_mock_health(player, mock, server_health,
						getattr(mock, 'maxHealth', max(1, server_health)),
						not bool(event.get('dead', False)), -1, False)
					_apply_capture_reset_event(player, mock, event)
		elif kind == 'bot_hit':
			mock = _find_bot(event.get('target_bot'))
			attacker_server_id = event.get('attacker')
			attacker_mock = (_local_mock(player) if attacker_server_id == getattr(
				player, '_offhangar_network_id', None) else _find_mock(player, attacker_server_id))
			if mock is not None:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_hit_feedback', None) if offline is not None else None
					if callable(present):
						present(player, attacker_mock, mock, _world_from_server(player, event),
							event.get('shot_result', 2), event.get('damage', 0),
							event.get('shell_index', 0), False,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot hit presentation failed')
				try:
					record_stats = getattr(offline, 'record_network_combat_stats', None) if offline is not None else None
					if callable(record_stats):
						record_stats(player,
							attacker_server_id == getattr(player, '_offhangar_network_id', None),
							False, mock, event.get('damage', 0),
							event.get('shot_result', 2), bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot-hit statistics failed')
				_push_mock_health(player, mock, event.get('health', mock.health),
					mock.maxHealth, not bool(event.get('dead', False)),
					_local_entity_id_for_server(player, attacker_server_id), False)
				_apply_capture_reset_event(
					player, mock, event, attacker_server_id)
		elif kind == 'bot_human_hit':
			target_id = event.get('target')
			is_local = target_id == getattr(player, '_offhangar_network_id', None)
			authority_simulated = network_is_authority(player)
			target_mock = _local_mock(player) if is_local else _find_mock(player, target_id)
			attacker_mock = _find_bot(event.get('attacker_bot'))
			if target_mock is not None:
				try:
					import sys
					offline = sys.modules.get('gui.mods.offhangar.offline_battle')
					present = getattr(offline, 'play_network_hit_feedback', None) if offline is not None else None
					# The authority already rendered the projectile impact while resolving
					# the hit. Other clients replay it from the canonical server event.
					if callable(present) and not authority_simulated:
						present(player, attacker_mock, target_mock, _world_from_server(player, event),
							event.get('shot_result', 2), event.get('damage', 0), 0,
							is_local, False, bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot-human hit presentation failed')
				try:
					record_stats = getattr(offline, 'record_network_combat_stats', None) if offline is not None else None
					if callable(record_stats):
						record_stats(player, False, is_local, target_mock,
							event.get('damage', 0), event.get('shot_result', 2),
							bool(event.get('dead', False)))
				except Exception:
					LOG_ERROR('LAN bot-human statistics failed')
				server_health = int(event.get(
					'health', getattr(target_mock, 'health', 0)) or 0)
				if is_local:
					delta = _advance_local_server_health(player, server_health)
					if delta is None:
						health = max(0,
							int(getattr(target_mock, 'health', 0) or 0) -
							int(event.get('damage', 0) or 0))
					else:
						health = max(0,
							int(getattr(target_mock, 'health', server_health) or 0) -
							delta)
				else:
					health = server_health
				_push_mock_health(player, target_mock, health,
					target_mock.maxHealth, not bool(event.get('dead', False)),
					getattr(attacker_mock, 'id', -1), is_local)
				_apply_capture_reset_event(
					player, target_mock, event, attacker_mock)
