"""Physical state and ordered receipts across the private player-driver link.

The visible process retains room input sequencing, ammunition, gun and
projectile ownership. Only the hidden driver integrates the player's body.
"""

import collections
import math
import sys

from gui.mods.offline_lan_0922 import (
    equipment_mechanics, player_driver, tank_contact_ledger,
    turret_contact_ledger)

try:
    _INTS = (int, long)
except NameError:
    _INTS = (int,)

_STATE_FIELDS = (
    'yaw', 'pitch', 'roll', 'speed', 'turn_speed', 'drive_throttle',
    'drive_turn', 'vertical_speed', 'siege_aim_pitch', 'siege_aim_center_z')
_CRITICAL_SETS = ('_crew_ko', '_crew_impaired', '_destroyed_devices',
                  '_critical_devices')
_CRITICAL_FLAGS = ('is_tracked', 'is_engine_dead', 'is_gun_destroyed',
                   'is_turret_locked')
_MAX_EVENTS = 128


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, _INTS + (float,)):
        raise ValueError('driver value is not numeric')
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError('driver value is not finite')
    return value


def _sequence(value, minimum=0):
    if (isinstance(value, bool) or not isinstance(value, _INTS) or
            not minimum <= value <= 2 ** 63 - 1):
        raise ValueError('invalid driver sequence')
    return value


def _triple(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('invalid driver vector')
    return tuple(_finite(item) for item in value)


def _boolean(value):
    if not isinstance(value, bool):
        raise ValueError('invalid driver boolean')
    return value


def _plain(value, depth=0):
    if depth > 12:
        raise ValueError('driver payload is too deep')
    if value is None or isinstance(value, (bool, type(''), type(u''))):
        return
    if isinstance(value, _INTS + (float,)):
        _finite(value)
        return
    if isinstance(value, (list, tuple)) and len(value) <= 1024:
        for item in value:
            _plain(item, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 128:
        for key, item in value.items():
            if not isinstance(key, (type(''), type(u''))):
                raise ValueError('invalid driver object key')
            _plain(item, depth + 1)
        return
    raise ValueError('invalid driver payload')


class DriverRuntimeMixin(object):
    def _reset_player_driver(self):
        self._player_driver = None
        self._driver_error = None
        self._driver_sample_seq = 0
        self._driver_pose_time_us = None
        self._driver_received_seq = 0
        self._driver_published_seq = 0
        self._driver_published_input_seq = 0
        self._driver_siege_sample_seq = 0
        self._driver_autorotation = False
        self._driver_native_gun_angles = None
        self._driver_control_seq = 0
        self._driver_integrated_control_seq = 0
        self._driver_receipt_time_us = None
        self._driver_landing_seq = 0
        self._driver_landing_ack = 0
        self._driver_landing_events = collections.OrderedDict()
        self._driver_feedback_seq = 0
        self._driver_feedback_ack = 0
        self._driver_feedback_events = collections.OrderedDict()
        self._driver_prediction_seq = 0
        self._driver_prediction_ack = 0
        self._driver_prediction_events = collections.OrderedDict()
        self._driver_last_feedback = {}
        self._driver_integration_time_us = None
        self._driver_receipts_pending = []
        self._driver_published_time_us = None
        self._driver_integrating_step = False
        self._driver_receipt_due = False
        self._driver_deferred_siege = None
        self._driver_consuming_receipts = False
        self._driver_presentation = None
        self._driver_display_clock = None
        self._driver_display_input = None
        self._driver_display_allow = False
        self._driver_display_control_seq = 0
        self._driver_display_guard_reported = False

    def _attach_player_driver(self):
        self._player_driver = player_driver.attach(self)
        return self._player_driver

    def _forward_driver_message(self, message):
        frontend = self._player_driver
        return bool(frontend is not None and frontend.forward_message(message))

    def _close_player_driver(self, reason):
        frontend = self._player_driver
        self._player_driver = None
        if frontend is not None:
            frontend.close(reason)

    def _driver_local_failure(self, reason):
        """Freeze this body's private path without tearing down the LAN room."""
        if self._driver_error is None:
            self._driver_error = str(reason)
            sys.stdout.write('[Offline LAN 0.9.22] PLAYER DRIVER failed: %s\n' %
                             self._driver_error)
            warn = getattr(self, '_warn_optional_failure', None)
            if callable(warn):
                warn('player driver', RuntimeError(self._driver_error),
                     disable=False)
            present = getattr(self, '_present_driver_failure', None)
            if not self._player_driver_mode and callable(present):
                try:
                    present()
                except Exception as error:
                    sys.stdout.write(
                        '[Offline LAN 0.9.22] PLAYER DRIVER failure UI: %s\n' %
                        str(error))
        return False

    def _driver_entity(self):
        server = getattr(self, '_server', None)
        return (self._server_entity(server.vehicle_id)
                if server is not None else None)

    def _send_driver_control(self, siege_request=None):
        if self._driver_consuming_receipts and siege_request is None:
            # Receipt admission publishes every physical sample to the room.
            # Its intermediate acknowledgements have no new player input;
            # send the final acknowledgement once after this frame's drain.
            return True
        frontend = self._player_driver
        sender = getattr(self, '_sender', None)
        if frontend is None or sender is None or self._driver_error:
            return False
        if frontend.error:
            # Consume receipts queued before EOF before freezing this path.
            return False
        if not frontend.ready:
            return False
        entity = self._driver_entity()
        rotator = getattr(self._avatar, 'gunRotator', None)
        if entity is None or rotator is None:
            return False
        now = self._clock()
        ack = (self._driver_published_seq, self._driver_published_input_seq,
               self._driver_landing_ack, self._driver_feedback_ack,
               self._driver_prediction_ack)
        try:
            critical = {name: sorted(getattr(entity, name, None) or ())
                        for name in _CRITICAL_SETS}
            critical.update({name: bool(getattr(entity, name, False))
                             for name in _CRITICAL_FLAGS})
            critical.update(
                devices_hp=dict(getattr(entity, 'devices_hp', None) or {}),
                stun=dict(getattr(entity, '_offlineStunFactors', None) or {}),
                health=_finite(entity.health),
                crew_active=bool(entity.isCrewActive))
            handler = getattr(self._avatar, 'inputHandler', None)
            autorotation = getattr(handler, 'getAutorotation', None)
            payload = {
                'forward': _finite(sender.forward),
                'turn': _finite(sender.turn),
                'handbrake': bool(sender.handbrake),
                'aim_yaw': _finite(sender.aim_yaw),
                'aim_pitch': _finite(sender.aim_pitch),
                'aim_point': (None if sender.aim_point is None else
                              list(_triple(sender.aim_point))),
                'gun_pitch': _finite(rotator.gunPitch),
                'turret_yaw': _finite(rotator.turretYaw),
                'dispersion_angle': _finite(rotator.dispersionAngle),
                'autorotation': bool(callable(autorotation) and autorotation()),
                'critical': critical,
                'equipment': [item.snapshot(now) for item in
                              (self._equipment_state or ())],
                'published_sample_seq': ack[0], 'published_input_seq': ack[1],
                'landing_ack': ack[2], 'feedback_ack': ack[3],
                'prediction_ack': ack[4],
            }
            if siege_request is not None:
                payload['siege_request'] = bool(siege_request)
            _plain(payload)
            if payload['dispersion_angle'] <= 0:
                raise ValueError('invalid driver dispersion angle')
            if not frontend.send_control(payload):
                if frontend.error:
                    self._driver_local_failure(frontend.error)
                return False
        except (AttributeError, TypeError, ValueError) as error:
            return self._driver_local_failure(error)
        return True

    def apply_driver_control(self, payload):
        if not self._player_driver_mode or self._driver_error:
            return False
        try:
            _plain(payload)
            seq = _sequence(payload['control_seq'], 1)
            if seq <= self._driver_control_seq:
                return False
            numbers = {name: _finite(payload[name]) for name in (
                'forward', 'turn', 'aim_yaw', 'aim_pitch', 'gun_pitch', 'turret_yaw',
                'dispersion_angle')}
            if (abs(numbers['forward']) > 1.0 or abs(numbers['turn']) > 1.0
                    or numbers['dispersion_angle'] <= 0.0):
                raise ValueError('invalid driver control range')
            handbrake = _boolean(payload['handbrake'])
            autorotation = _boolean(payload['autorotation'])
            aim_point = (None if payload['aim_point'] is None else
                         _triple(payload['aim_point']))
            critical = payload['critical']
            if not isinstance(critical, dict):
                raise ValueError('invalid driver critical projection')
            health = _finite(critical['health'])
            active = _boolean(critical['crew_active'])
            flags = {name: _boolean(critical[name]) for name in _CRITICAL_FLAGS}
            sets = {}
            for name in _CRITICAL_SETS:
                values = critical[name]
                if (not isinstance(values, list) or len(values) > 64 or
                        any(not isinstance(value, (type(''), type(u'')))
                            for value in values)):
                    raise ValueError('invalid driver critical set')
                sets[name] = set(values)
            maps = {}
            for name in ('devices_hp', 'stun'):
                values = critical[name]
                if not isinstance(values, dict):
                    raise ValueError('invalid driver critical map')
                maps[name] = {key: _finite(value)
                              for key, value in values.items()}
            now = self._clock()
            equipment = []
            if not isinstance(payload['equipment'], list) or len(
                    payload['equipment']) > 16:
                raise ValueError('invalid driver equipment projection')
            for row in payload['equipment']:
                item = equipment_mechanics.EquipmentState(row['equipment'], now)
                item.restore(row, now)
                equipment.append(item)
            published = _sequence(payload['published_sample_seq'])
            input_seq = _sequence(payload['published_input_seq'])
            if published > self._driver_sample_seq or bool(published) != bool(input_seq):
                raise ValueError('invalid driver pose acknowledgement')
            acks = {}
            for kind in ('landing', 'feedback', 'prediction'):
                value = _sequence(payload[kind + '_ack'])
                if value > getattr(self, '_driver_' + kind + '_seq'):
                    raise ValueError('invalid driver event acknowledgement')
                acks[kind] = value
            siege = payload.get('siege_request')
            if siege is not None:
                _boolean(siege)
            entity = self._driver_entity()
            if entity is None or self._sender is None:
                return False
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            return self._driver_local_failure(error)
        self._driver_control_seq = seq
        self._sender.forward = numbers['forward']
        self._sender.turn = numbers['turn']
        self._sender.handbrake = handbrake
        self._sender.aim_yaw = numbers['aim_yaw']
        self._sender.aim_pitch = numbers['aim_pitch']
        self._sender.aim_point = aim_point
        self._sender.gun_pitch = numbers['gun_pitch']
        self._driver_autorotation = autorotation
        self._driver_native_gun_angles = (numbers['turret_yaw'], numbers['gun_pitch'])
        entity.health = health
        entity.isCrewActive = active
        for name, value in list(flags.items()) + list(sets.items()):
            setattr(entity, name, value)
        entity.devices_hp = maps['devices_hp']
        entity._offlineStunFactors = maps['stun']
        self._equipment_state = equipment
        apply_gun = getattr(self, '_apply_mirrored_gun_pose', None)
        if callable(apply_gun):
            try:
                apply_gun(numbers)
            except Exception as error:
                return self._driver_local_failure(error)
        for kind, value in acks.items():
            ledger = getattr(self, '_driver_' + kind + '_events')
            for key in list(ledger):
                if key <= value:
                    del ledger[key]
        if (self._local_siege_pending is not None and
                self._driver_siege_sample_seq and
                published >= self._driver_siege_sample_seq):
            enabled, unused_old_seq = self._local_siege_pending
            self._local_siege_pending = (enabled, input_seq)
            self._driver_siege_sample_seq = 0
        if siege is not None:
            settings = self._runtime.constants.VEHICLE_SETTING
            return self.change_vehicle_setting(settings.SIEGE_MODE_ENABLED,
                                               int(siege))
        return True

    def _driver_queue_event(self, kind, value):
        ledger = getattr(self, '_driver_' + kind + '_events')
        if len(ledger) >= _MAX_EVENTS:
            self._driver_local_failure('player driver event queue overflow')
            return False
        attr = '_driver_' + kind + '_seq'
        sequence = getattr(self, attr) + 1
        value = dict(value, seq=sequence)
        ledger[sequence] = value
        setattr(self, attr, sequence)
        return True

    def _queue_driver_collision(self, own, other, point, now):
        remote = other.get('_vehicle')
        key = next((key for key, row in self._records.items()
                    if row.get('engine_id') == getattr(remote, 'id', None)), None)
        if key is None:
            return False
        # Match the stock feedback boundary's pair debounce before transport.
        previous = self._driver_last_feedback.get(key)
        if previous is not None and float(now) - previous < 0.2:
            return False
        result = self._driver_queue_event('feedback', {
            'actor': key, 'point': list(point),
            'own_velocity': [own['vx'], own.get('vy', 0.0), own['vz']],
            'other_velocity': [other['vx'], other.get('vy', 0.0), other['vz']],
        })
        if result:
            self._driver_last_feedback[key] = float(now)
        return result

    def _queue_driver_destructible_prediction(
            self, detail, start_position, start_yaw, end_position, end_yaw,
            speed, dt, catalog_speed=None):
        return self._driver_queue_event('prediction', {
            'tree_token': detail.get('_tree_token'),
            'catalog_token': detail.get('_catalog_token'),
            'start': list(start_position), 'start_yaw': float(start_yaw),
            'end': list(end_position), 'end_yaw': float(end_yaw),
            'speed': float(speed), 'dt': float(dt),
            'catalog_speed': float(speed if catalog_speed is None else catalog_speed),
        })

    def _publish_driver_state(self, siege_enabled=None):
        if not self._player_driver_mode or self._driver_error:
            return False
        if self._driver_integrating_step:
            self._driver_receipt_due = True
            if siege_enabled is not None:
                self._driver_deferred_siege = bool(siege_enabled)
            return True
        if siege_enabled is None:
            siege_enabled = self._driver_deferred_siege
        now = self._clock()
        motion_time = self._driver_integration_time_us
        if motion_time is None:
            motion_time = self._estimated_motion_time_us(now)
        if motion_time is None or self._local_position is None:
            return False
        while self._pending_landing_impacts:
            if not self._driver_queue_event('landing', {
                    'impact_speed': float(self._pending_landing_impacts[0])}):
                return False
            del self._pending_landing_impacts[0]
        ram = list(self._local_ram_receipts.values())
        destructible = list(self._local_destructible_contacts.values())
        state = {name: float(getattr(self, '_local_' + name, 0.0))
                 for name in _STATE_FIELDS}
        state.update(position=list(self._local_position),
                     airborne=bool(self._local_airborne),
                     surface_up_cosine=self._local_surface_up_cosine,
                     grind=int(self._local_grind))
        environment = {}
        server_now = self._server_clock()
        for kind in ('drown', 'overturn'):
            started = getattr(self, '_' + kind + '_started')
            environment[kind] = {
                'level': int(getattr(self, '_' + kind + '_level')),
                'time': float(getattr(self, '_' + kind + '_time')),
                'elapsed': (None if started is None else
                            max(0.0, server_now - float(started))),
            }
        receipt = {
            'sample_seq': self._driver_sample_seq + 1,
            'integrated_control_seq': self._driver_integrated_control_seq,
            'pose_time_us': int(motion_time), 'state': state,
            'ram_contacts': ram, 'destructible_contacts': destructible,
            'tank_pushes': list(self._local_contact_pushes.values()),
            'turret_pushes': list(self._local_turret_pushes.values()),
            'landing': list(self._driver_landing_events.values()),
            'feedback': list(self._driver_feedback_events.values()),
            'prediction': list(self._driver_prediction_events.values()),
            'environment': environment,
        }
        if siege_enabled is not None:
            receipt['siege_enabled'] = bool(siege_enabled)
        try:
            _plain(receipt)
            if not self.client.publish_driver_state(receipt):
                return self._driver_local_failure('player driver receipt send failed')
        except Exception as error:
            return self._driver_local_failure(error)
        self._driver_sample_seq += 1
        if siege_enabled is not None:
            # A request emitted inside integration is frozen only after the
            # physical slice completes. Bind its latch to that actual receipt.
            self._driver_siege_sample_seq = self._driver_sample_seq
        self._driver_receipt_due = False
        self._driver_deferred_siege = None
        return True

    def _driver_pose_publication(self):
        if (self._driver_received_seq <= self._driver_published_seq or
                self._driver_pose_time_us is None or
                (self._driver_published_time_us is not None and
                 self._driver_pose_time_us <= self._driver_published_time_us)):
            return None, None
        return self._local_position, self._driver_pose_time_us

    def _driver_pose_published(self):
        if self._driver_received_seq <= self._driver_published_seq:
            return
        self._driver_published_seq = self._driver_received_seq
        self._driver_published_input_seq = self.client._input_seq
        if self._driver_pose_time_us is not None:
            self._driver_published_time_us = max(
                self._driver_published_time_us or 0, self._driver_pose_time_us)
        self._send_driver_control()

    def _driver_validate_receipt(self, receipt):
        if not isinstance(receipt, dict):
            raise ValueError('invalid driver receipt')
        # This optional diagnostic acknowledgement never admits physical state.
        # Missing/malformed metadata only suppresses its latency measurement.
        _plain(dict((key, value) for key, value in receipt.items()
                    if key != 'integrated_control_seq'))
        _sequence(receipt['sample_seq'], 1)
        _sequence(receipt['pose_time_us'])
        state = receipt['state']
        _triple(state['position'])
        for name in _STATE_FIELDS:
            _finite(state[name])
        _boolean(state['airborne'])
        _sequence(state['grind'])
        if state['surface_up_cosine'] is not None:
            if abs(_finite(state['surface_up_cosine'])) > 1.0:
                raise ValueError('invalid driver surface orientation')
        for kind in ('drown', 'overturn'):
            row = receipt['environment'][kind]
            if _sequence(row['level']) > 2 or _finite(row['time']) < 0:
                raise ValueError('invalid driver environment warning')
            if row['elapsed'] is not None and _finite(row['elapsed']) < 0:
                raise ValueError('invalid driver warning duration')
        if 'siege_enabled' in receipt:
            _boolean(receipt['siege_enabled'])
        for name in ('ram_contacts', 'destructible_contacts',
                     'landing', 'feedback', 'prediction'):
            rows = receipt[name]
            if not isinstance(rows, list) or len(rows) > _MAX_EVENTS:
                raise ValueError('invalid driver event list')
            previous = 0
            for row in rows:
                sequence = _sequence(row['seq'], 1)
                if sequence <= previous:
                    raise ValueError('unordered driver event list')
                previous = sequence
                if name == 'destructible_contacts':
                    for key in ('x', 'y', 'z', 'yaw', 'speed', 'dt',
                                'end_x', 'end_y', 'end_z', 'end_yaw'):
                        _finite(row[key])
                    if self._destructible_contact_token(row['token']) is None:
                        raise ValueError('invalid driver destructible identity')
                elif name == 'landing':
                    if _finite(row['impact_speed']) < 0.0:
                        raise ValueError('invalid driver landing impact')
                elif name == 'feedback':
                    turret_contact_ledger.actor_key(row['actor'])
                    _triple(row['point'])
                    _triple(row['own_velocity'])
                    _triple(row['other_velocity'])
                elif name == 'prediction':
                    _triple(row['start'])
                    _triple(row['end'])
                    for key in ('start_yaw', 'end_yaw', 'speed', 'dt',
                                'catalog_speed'):
                        _finite(row[key])
                    for key in ('tree_token', 'catalog_token'):
                        if row[key] is not None and (
                                self._destructible_contact_token(row[key]) is None):
                            raise ValueError('invalid driver prediction identity')
        tank_contact_ledger.normalize(receipt['tank_pushes'])
        turret_contact_ledger.normalize(receipt['turret_pushes'])

    def _driver_apply_prediction(self, row, entity, now):
        if row['seq'] <= self._driver_prediction_ack:
            return True
        sensor = self._destructibles
        if sensor is None or self._avatar is None:
            return False
        tree = self._destructible_contact_token(row['tree_token'])
        catalog = self._destructible_contact_token(row['catalog_token'])
        if tree:
            result = sensor.commit_local_tree_prediction(
                self._avatar.spaceID, tree, self._vector(row['start']),
                row['start_yaw'], self._vector(row['end']), row['end_yaw'],
                row['speed'], entity.typeDescriptor, now,
                dt=row['dt'], publish=False)
            if not isinstance(result, dict) or result.get('status') == 'pending':
                return False
            token = self._destructible_contact_token(result.get('token'))
            if result.get('status') != 'crushed' or set(token or ()) != set(tree):
                raise ValueError('driver tree prediction was not applied')
        if catalog and not sensor.commit_local_prediction(
                self._avatar.spaceID, catalog, self._vector(row['start']),
                row['start_yaw'], row['catalog_speed']):
            return False
        self._driver_prediction_ack = row['seq']
        return True

    def _driver_apply_feedback(self, row, entity, now):
        if row['seq'] <= self._driver_feedback_ack:
            return
        record = self._records.get(row['actor'])
        remote = (self._server_entity(record['engine_id'])
                  if record is not None else None)
        hook = self._native_ram_contact_hook
        # An already-despawned counterpart has no presentation left to play.
        if remote is not None and hook is not None and self._avatar is not None:
            self._collision_feedback.present(
                self._avatar, hook[2], entity, remote,
                row['own_velocity'], row['other_velocity'], row['point'],
                now, self._vector)
        self._driver_feedback_ack = row['seq']

    def _driver_note_performance(self, method, *args):
        try:
            callback = getattr(self._player_driver, method, None)
            if callable(callback):
                callback(*args)
        except Exception:
            pass

    def _report_player_driver_performance(self):
        self._driver_note_performance('report_performance')

    def _driver_apply_receipt(self, receipt):
        sequence = receipt['sample_seq']
        if sequence < self._driver_received_seq:
            return True
        entity = self._driver_entity()
        if entity is None or self._sender is None:
            return False
        now = self._clock()
        for row in receipt['prediction']:
            try:
                if not self._driver_apply_prediction(row, entity, now):
                    # This is an early visual hint, not the canonical event.
                    # Missing native chunks must not retain hints until their
                    # bounded queue stops physical motion. The reliable room
                    # destruction event will converge presentation later.
                    self._driver_prediction_ack = row['seq']
            except Exception as error:
                warn = getattr(self, '_warn_optional_failure', None)
                if callable(warn):
                    warn('player driver destructible presentation', error,
                         disable=False)
                # The canonical destruction event remains its final owner.
                self._driver_prediction_ack = row['seq']
        if sequence > self._driver_received_seq:
            advance = getattr(self, '_advance_local_gun_to', None)
            if callable(advance):
                # Account elapsed gun time using the previous physical state.
                advance(entity, now)
            state = receipt['state']
            self._local_position = tuple(state['position'])
            for name in _STATE_FIELDS:
                setattr(self, '_local_' + name, float(state[name]))
            self._local_airborne = state['airborne']
            self._local_surface_up_cosine = state['surface_up_cosine']
            self._local_grind = state['grind']
            self._driver_received_seq = sequence
            self._driver_pose_time_us = receipt['pose_time_us']
            for row in receipt['ram_contacts']:
                if row['seq'] > self._local_ram_admitted_seq:
                    self._local_ram_receipts[row['seq']] = dict(row)
                    self._local_ram_seq = max(self._local_ram_seq, row['seq'])
                    self._local_ram_receipt = dict(row)
            for row in receipt['destructible_contacts']:
                if row['seq'] > self._local_destructible_admitted_seq:
                    self._local_destructible_contacts[row['seq']] = dict(row)
                    self._local_destructible_contact_seq = max(
                        self._local_destructible_contact_seq, row['seq'])
                    self._local_destructible_safe_poses[row['seq']] = (
                        (row['x'], row['y'], row['z']), row['yaw'])
            self._local_contact_pushes = tank_contact_ledger.normalize(
                receipt['tank_pushes'])
            self._local_turret_pushes = turret_contact_ledger.normalize(
                receipt['turret_pushes'])
            server_now = self._server_clock()
            for kind in ('drown', 'overturn'):
                row = receipt['environment'][kind]
                previous = getattr(self, '_' + kind + '_level')
                setattr(self, '_' + kind + '_level', row['level'])
                setattr(self, '_' + kind + '_time', row['time'])
                setattr(self, '_' + kind + '_started',
                        None if row['elapsed'] is None else
                        server_now - row['elapsed'])
                flag = '_offh_drowning' if kind == 'drown' else '_offh_overturned'
                setattr(entity, flag, row['level'] == 2)
                setattr(self._avatar, flag, row['level'] == 2)
                if row['level'] != previous:
                    if kind == 'drown':
                        self._present_drowning_level(row['level'], now)
                    else:
                        self._present_overturn_level(row['level'])
            elapsed = (0.0 if self._driver_receipt_time_us is None else
                       max(0.0, (receipt['pose_time_us'] -
                                 self._driver_receipt_time_us) / 1000000.0))
            self._driver_receipt_time_us = receipt['pose_time_us']
            try:
                accept_display = getattr(
                    self, '_accept_driver_display_receipt', None)
                if callable(accept_display):
                    accept_display(receipt, now)
            except Exception:
                # Optional display continuation cannot reject canonical state.
                pass
            position = self._update_local_presentation(entity, elapsed)
            callback = getattr(self._avatar, 'updateOwnVehiclePosition', None)
            if callable(callback):
                callback(position, self._vector((0.0, 0.0, self._local_yaw)),
                         self._local_speed, self._local_turn_speed)
            self._publish_rpm(now)
            self._driver_note_performance('note_receipt_applied', receipt)
        for row in receipt['feedback']:
            self._driver_apply_feedback(row, entity, now)
        if not self._sender.send_current(
                siege_enabled=receipt.get('siege_enabled')):
            return False
        for row in receipt['landing']:
            if row['seq'] <= self._driver_landing_ack:
                continue
            if not self.client.send_landing_observation(row['impact_speed']):
                # Keep the receipt until its ordered observation is admitted.
                return False
            self._driver_landing_ack = row['seq']
        self._send_driver_control()
        return True

    def _consume_driver_receipts(self):
        frontend = self._player_driver
        if frontend is None or self._driver_error:
            return False
        try:
            self._driver_consuming_receipts = True
            rows = frontend.drain()
            if len(self._driver_receipts_pending) + len(rows) > _MAX_EVENTS:
                return self._driver_local_failure('player driver receipt queue overflow')
            self._driver_receipts_pending.extend(rows)
            self._driver_note_performance(
                'note_receipt_batch', len(rows), len(self._driver_receipts_pending))
            while self._driver_receipts_pending:
                receipt = self._driver_receipts_pending[0]
                self._driver_validate_receipt(receipt)
                if not self._driver_apply_receipt(receipt):
                    return False
                del self._driver_receipts_pending[0]
        except Exception as error:
            return self._driver_local_failure(error)
        finally:
            self._driver_consuming_receipts = False
            if not self._driver_error and not frontend.error:
                self._send_driver_control()
        if frontend.error:
            return self._driver_local_failure(frontend.error)
        return not self._driver_error
