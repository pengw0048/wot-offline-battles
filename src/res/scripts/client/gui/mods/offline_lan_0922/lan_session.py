from __future__ import print_function

"""Coordinator between LAN protocol, stock map picker and battle runtime."""

import base64
import math
import sys
import time

from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922 import queue_screen
from gui.mods.offline_lan_0922 import queue_ui
from gui.mods.offline_lan_0922 import vehicle_configuration
from gui.mods.offline_lan_0922 import waiting_room_ui


RECONNECT_DELAY = 2.0
POSTBATTLE_RETRY_DELAY = 0.10
# The server returns an abandoned round to its waiting room five seconds after
# the last participant leaves.  Rejoin if that roster never arrives.
ROUND_END_TIMEOUT = 12.0
VEHICLE_SELECTION_WARNING = (
    'Select a valid vehicle in the garage, then click Battle! again.')


class _VehicleSelectionError(Exception):
    pass


try:
    text_type = unicode
except NameError:
    text_type = str


def _show_status(message):
    """Show one native lobby notification without owning mouse focus."""
    try:
        from gui import SystemMessages
        SystemMessages.pushMessage(
            message, type=SystemMessages.SM_TYPE.Warning)
    except Exception:
        # A notification must never become a second connection failure.
        pass


def _load_client():
    from gui.mods.offline_lan_0922.lan_client import LANClient
    return LANClient


def _start_adisp_request(request_results, context, completed):
    """Advance one stock ``@async @process`` request to completion."""
    from adisp import process

    @process
    def fetch():
        success = yield request_results(context)
        completed(success)

    fetch()


def _load_battle_runtime():
    from gui.mods.offline_lan_0922.battle_runtime import g_battle_runtime
    return g_battle_runtime


def _load_donation_runtime():
    """Return the exact #1513 modules the descriptor donation reads."""
    import nations
    from items import vehicles

    class Runtime(object):
        pass

    runtime = Runtime()
    runtime.nations = nations
    runtime.vehicles = vehicles
    return runtime


def _selected_vehicle_details():
    """Return the canonical type name and HP of the selected #1513 vehicle."""
    from CurrentVehicle import g_currentVehicle

    item = g_currentVehicle.item
    if item is None:
        raise ValueError('the current garage vehicle is not available')
    descriptor = item.descriptor
    type_name = descriptor.type.name
    max_health = int(descriptor.maxHealth)
    if (not vehicle_configuration.is_standard_battle_vehicle(
            descriptor.type) or max_health < 1):
        raise ValueError('the current garage vehicle descriptor is invalid')
    return type_name, max_health


def _selected_vehicle_outfits():
    """Return stock compact outfits for each arena season, base64 on JSON."""
    try:
        from CurrentVehicle import g_currentVehicle
    except ImportError:
        return {}
    item = g_currentVehicle.item
    if item is None:
        return {}
    result = {}
    for season in (1, 2, 4):
        outfit = item.getOutfit(season)
        if outfit is None:
            continue
        compact = getattr(outfit, 'strCompactDescr', None)
        if compact is None:
            maker = getattr(outfit, 'makeCompDescr', None)
            compact = maker() if callable(maker) else None
        if not isinstance(compact, bytes) or not compact:
            continue
        if len(compact) > 64 * 1024:
            raise ValueError('selected vehicle outfit is too large')
        result[str(season)] = base64.b64encode(compact).decode('ascii')
    return result


def _selected_vehicle_compact_descr():
    """Return the selected mounted vehicle descriptor as canonical base64."""
    from CurrentVehicle import g_currentVehicle

    item = g_currentVehicle.item
    descriptor = None if item is None else item.descriptor
    maker = getattr(descriptor, 'makeCompactDescr', None)
    compact = maker() if callable(maker) else None
    if not isinstance(compact, bytes) or not compact:
        raise ValueError('selected vehicle compact descriptor is unavailable')
    if len(compact) > 64 * 1024:
        raise ValueError('selected vehicle compact descriptor is too large')
    return base64.b64encode(compact).decode('ascii')


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _ordered_crew_members(crew):
    members = list(crew or ())
    if members and all(isinstance(member, tuple) and len(member) == 2
                       for member in members):
        members = [member[1] for member in
                   sorted(members, key=lambda entry: entry[0])]
    return tuple(members)


def _project_crew_skill(skill):
    from gui.mods.offline_lan_0922 import loadout

    projected = loadout.project_skill_state(skill)
    if projected is None:
        raise ValueError('a mounted crew skill state is invalid')
    return projected


def _selected_vehicle_effective_params():
    """Build the immutable effective-parameter snapshot from #1513 garage.

    The worker must consume these final values instead of attempting to
    recreate the player's mounted crew, equipment and ammunition from the
    vehicle descriptor alone.
    """
    from CurrentVehicle import g_currentVehicle
    from gui.mods.offline_lan_0922 import descriptor_donation
    from gui.mods.offline_lan_0922 import effective_params
    from gui.mods.offline_lan_0922 import equipment_mechanics
    from gui.mods.offline_lan_0922 import loadout
    from gui.mods.offline_lan_0922 import tank_collision
    from gui.mods.offline_lan_0922 import player_critical_mechanics
    from gui.mods.offline_lan_0922 import vehicle_physics
    from items import vehicles

    item = g_currentVehicle.item
    if item is None:
        raise ValueError('the current garage vehicle is not available')
    descriptor = item.descriptor
    crew = tuple(getattr(item, 'crew', None) or ())
    consumables = getattr(
        getattr(item, 'equipment', None), 'regularConsumables', None)
    mounted_equipments = (() if consumables is None else
                          tuple(consumables.getInstalledItems()))
    equipments = []
    for mounted in mounted_equipments:
        equipment = loadout._artefact(mounted, vehicles)
        if equipment is None:
            raise ValueError(
                'a mounted garage equipment descriptor is unavailable')
        equipments.append(equipment)
    equipments = tuple(equipments)
    # A Removed RPM Limiter is trigger-only.  Supplying it to the passive
    # attribute-factor chain would claim it is permanently enabled.
    factor_equipments = tuple(
        equipment for equipment in equipments
        if not any('removedrpmlimiter' in name for name in
                   loadout.equipment_names((equipment,))))
    factors = loadout.attribute_factors(
        descriptor, crew or None, factor_equipments)
    if factors is None:
        raise ValueError('the exact client attribute factors are unavailable')
    crew_skills = loadout.crew_skill_names(crew) if crew else None
    loadout_values = loadout.modifiers(
        descriptor, equipments, crew_skills, factors=factors)
    # Discrete perks require exact mounted-role eligibility and completion.
    # The name-only fallback bundle cannot prove either condition.
    loadout_values['has_sixth_sense'] = bool(
        loadout.finished_skill_count(crew, 'commander_sixthsense'))
    spotting_values = loadout.spotting_profile(
        descriptor, crew or None,
        level_increase=loadout.crew_level_increase(
            descriptor, equipments, crew_skills),
        factors=factors)
    physics_values = vehicle_physics.derive_params(descriptor, factors)
    ramming_values = tank_collision.descriptor_ram_profile(
        descriptor, loadout.ramming_bonus(crew))

    ammo = []
    for shell in (getattr(item, 'shells', None) or ()):
        try:
            ammo.append([int(shell.intCD), max(0, int(shell.count))])
        except (AttributeError, TypeError, ValueError):
            raise ValueError('the selected vehicle ammunition is invalid')
    ammo.sort(key=lambda entry: entry[0])

    camouflage_id = None
    reader = getattr(item, 'getBonusCamo', None)
    if callable(reader):
        camouflage = reader()
        if camouflage is not None:
            camouflage_id = getattr(camouflage, 'id', None)
            if camouflage_id is not None:
                camouflage_id = int(camouflage_id)
    calculator = getattr(descriptor, 'computeBaseInvisibility', None)
    if not callable(calculator):
        raise ValueError('the exact client camouflage calculator is unavailable')
    base_invisibility = calculator(
        spotting_values['camouflage_factor'], camouflage_id)
    if (not isinstance(base_invisibility, (list, tuple)) or
            len(base_invisibility) < 2):
        raise ValueError('the exact client camouflage values are invalid')
    gun = _field(descriptor, 'gun', {})
    shot_factor = float(_field(gun, 'invisibilityFactorAtShot', 1.0))
    deadeye = bool(loadout.finished_skill_count(crew, 'gunner_sniper'))
    try:
        clip_size = int(_field(gun, 'clip')[0])
    except (IndexError, TypeError, ValueError):
        raise ValueError('the selected vehicle gun clip is invalid')
    shots = []
    for shot in (_field(gun, 'shots', ()) or ()):
        try:
            compact_descr = int(_field(_field(shot, 'shell'), 'compactDescr'))
        except (AttributeError, TypeError, ValueError):
            raise ValueError('the selected vehicle gun shot is invalid')
        shots.append({
            'compact_descr': compact_descr,
            'source_shot': descriptor_donation.project_shot(
                shot, deadeye=deadeye),
        })

    equipment_contracts = [
        equipment_mechanics.project_equipment(equipment)
        for equipment in equipments]
    critical_profile = player_critical_mechanics.project_profile(descriptor)
    members = _ordered_crew_members(crew)
    roles = tuple(getattr(
        getattr(descriptor, 'type', None), 'crewRoles', ()) or ())
    roster = tuple(critical_profile.get('crew_roster') or ())
    if (len(members) != len(roles) or len(roster) != len(roles) or
            not members or len(members) > 6 or
            any(member is None for member in members)):
        raise ValueError('the selected vehicle physical crew is incomplete')
    projected_members = []
    for index, member in enumerate(members):
        member_roles = roles[index]
        if not isinstance(member_roles, (list, tuple)) or not member_roles:
            raise ValueError('the selected vehicle crew roles are invalid')
        raw_skills = _field(member, 'skills', None)
        if raw_skills is None:
            raw_skills = _field(_field(member, 'descriptor', {}), 'skills', ())
        projected_skills = []
        for skill in raw_skills or ():
            projected = _project_crew_skill(skill)
            required_role = effective_params.skill_required_role(
                projected['name'])
            if (required_role is not None and
                    required_role not in member_roles):
                raise ValueError(
                    'a mounted crew skill does not match its slot roles')
            projected_skills.append(projected)
        projected_skills.sort(key=lambda entry: entry['name'])
        projected_members.append({
            'instance': roster[index],
            'roles': [str(role) for role in member_roles],
            'skills': projected_skills,
        })

    dynamic_states = {}
    for mask in range(1 << len(roster)):
        activity = [not bool(mask & (1 << index))
                    for index in range(len(roster))]
        for fire in (False, True):
            dynamic = loadout.attribute_factors(
                descriptor, crew or None, factor_equipments,
                activity_flags=activity, is_fire=fire)
            if dynamic is None:
                raise ValueError(
                    'the exact dynamic crew factors are unavailable')
            ratios = loadout.dynamic_spotting_ratios(factors, dynamic)
            dynamic_profile = loadout.spotting_profile(
                descriptor, crew or None,
                level_increase=loadout.crew_level_increase(
                    descriptor, equipments, crew_skills),
                factors=dynamic)
            dynamic_base = calculator(
                dynamic_profile['camouflage_factor'], camouflage_id)
            if (not isinstance(dynamic_base, (list, tuple)) or
                    len(dynamic_base) < 2):
                raise ValueError(
                    'the exact dynamic camouflage values are invalid')
            row = {
                'vision': float(ratios['vision']),
                'signal': float(ratios['signal']),
                'camouflage': float(ratios['camouflage']),
                'base_moving': float(dynamic_base[0]),
                'base_still': float(dynamic_base[1]),
                'invisibility_moving': list(
                    loadout.invisibility_pair(dynamic, False)),
                'invisibility_still': list(
                    loadout.invisibility_pair(dynamic, True)),
            }
            if any(math.isnan(float(value)) or math.isinf(float(value))
                   for value in (
                       row['vision'], row['signal'], row['camouflage'],
                       row['base_moving'], row['base_still'])):
                raise ValueError('a dynamic crew factor is not finite')
            dynamic_states['%d:%d' % (mask, int(fire))] = row
    crew_projection = {
        'members': projected_members,
        'dynamic_spotting': {
            'crew': list(roster),
            'states': dynamic_states,
        },
    }
    healthy_skills = effective_params.skill_summary(crew_projection)
    if effective_params._canonical_equipment(equipment_contracts) is None:
        raise ValueError('the selected vehicle equipment projection is invalid')
    if effective_params._canonical_critical(critical_profile) is None:
        raise ValueError('the selected vehicle critical profile is invalid')
    result = effective_params.canonical({
        'version': effective_params.SCHEMA_VERSION,
        'loadout': loadout_values,
        'physics': physics_values,
        'spotting': spotting_values,
        'ramming': ramming_values,
        'ammo': ammo,
        'camouflage': {
            'camouflage_id': camouflage_id,
            'base_moving': float(base_invisibility[0]),
            'base_still': float(base_invisibility[1]),
            'shot_factor': shot_factor,
        },
        'skills': healthy_skills,
        'crew': crew_projection,
        'gun': {
            'clip_size': clip_size,
            'shots': shots,
        },
        'equipment': equipment_contracts,
        'critical': critical_profile,
    })
    if result is None:
        raise ValueError('the selected vehicle effective parameters are invalid')
    return result


def _message_value(message, name, default=None):
    if isinstance(message, dict):
        return message.get(name, default)
    return default


def _spawn(value):
    """Normalize trusted server spawn data for the battle runtime."""
    if isinstance(value, dict):
        value = (value.get('x'), value.get('y'), value.get('z'))
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None


class LANSession(object):
    """Owns one LAN connection and its reversible lobby/battle transitions."""

    def __init__(self, config, client_factory=None, queue_factory=None,
                 picker_opener=None, join_factory=None, battle_runtime=None,
                 on_snapshot=None, on_event=None, lobby_ready=None,
                 callback=None, cancel_callback=None, status_notifier=None,
                 vehicle_provider=None, room_factory=None,
                 queue_screen_factory=None, postbattle_store=None,
                 vehicle_compact_provider=None,
                 effective_params_provider=None):
        self._config = dict(config or {})
        self._client_factory = client_factory or _load_client
        self._queue_factory = queue_factory or queue_ui.QueueUI
        self._room_factory = (waiting_room_ui.WaitingRoomUI
                              if room_factory is None else room_factory)
        self._queue_screen_factory = (queue_screen.QueueScreenUI
                                      if queue_screen_factory is None
                                      else queue_screen_factory)
        self._picker_opener = picker_opener or queue_ui.open_picker
        self._join_factory = join_factory or queue_ui.JoinButtonUI
        self._battle_runtime = battle_runtime
        self._on_snapshot = on_snapshot
        self._on_event_callback = on_event
        self._lobby_ready = lobby_ready or (lambda: True)
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._status_notifier = status_notifier or _show_status
        self._vehicle_provider = vehicle_provider or _selected_vehicle_details
        self._outfit_provider = _selected_vehicle_outfits
        self._vehicle_compact_provider = (
            vehicle_compact_provider or _selected_vehicle_compact_descr)
        # The production client always reads the exact garage snapshot.
        # Embedders that replace the whole transport factory must explicitly
        # provide the matching snapshot boundary as well.
        self._effective_params_provider = effective_params_provider
        if self._effective_params_provider is None and client_factory is None:
            self._effective_params_provider = \
                _selected_vehicle_effective_params
        self._postbattle_store = postbattle_store
        self._room_preferences = port_config.load_waiting_room_state()
        self._restored_team_generation = None
        self._restored_team_sizes_generation = None
        self._published_progress_battles = (
            -1 if postbattle_store is None else
            int(postbattle_store.progress().get('battles', 0)))
        self._requested_results = set()
        self._completed_results = set()
        self._notified_results = set()
        self._archived_result_replayed = False
        self.client = None
        self.snapshot = None
        self.state = 'idle'
        # None means the server policy is not known yet.  Only the elected
        # room host opens the stock picker after the welcome/roster barrier.
        self._map_pool = None
        self._queue = None
        self._queue_screen = None
        self._join_ui = None
        self._picker_open = False
        self._picker_dismissed = False
        # Only an explicit Battle click may raise the room over the garage.
        self._picker_requested = False
        self._picker_callback_id = None
        self._picker_close_callback_id = None
        self._picker_cleanup_pending = False
        self._battle_start_callback_id = None
        self._retry_callback_id = None
        self._retry_token = None
        self._postbattle_callback_id = None
        self._postbattle_token = None
        self._postbattle_request_token = None
        self._postbattle_service_error_reported = False
        self._postbattle_result_retry_attempted = set()
        self._postbattle_notification_retry_attempted = set()
        self._postbattle_notification_error_reported = set()
        self._round_end_callback_id = None
        self._round_end_token = None
        self._pending_battle_start = None
        self._pending_map = None
        self._start_requested = False
        self._starting_round_id = None
        self._active_round_id = None
        self._departed_round_id = None
        self._battle_started = False
        self._stopped = False
        self._connection_error_notified = False
        self._client_generation = 0
        self._host_player_id = None
        self._waiting_notice_host_id = None

    def _new_client(self):
        # Advancing before resolution also retires callbacks from an old
        # socket when the garage selection becomes unavailable during retry.
        self._client_generation += 1
        generation = self._client_generation
        try:
            vehicle, max_health = self._vehicle_provider()
            max_health = int(max_health)
            if not vehicle or max_health < 1:
                raise ValueError('selected vehicle details are invalid')
        except Exception:
            raise _VehicleSelectionError()

        factory = self._client_factory
        if factory is _load_client:
            factory = factory()

        def on_event(kind, message):
            # A cancelled BigWorld poll can still already be dispatching.  Do
            # not let a retired socket mutate the replacement LAN session.
            if (not self._stopped and
                    generation == self._client_generation):
                self._on_event(kind, message)

        client = factory(
            self._config.get('host', '127.0.0.1'),
            self._config.get('port', 28782),
            self._config.get('name', 'Player'),
            vehicle,
            max_health=max_health,
            on_event=on_event)
        # Keep custom test/embedding factories source-compatible: the team
        # preference is ordinary client state consumed when hello is built.
        saved_team = int(self._room_preferences.get('team', 0) or 0)
        client.requested_team = (saved_team or
                                 port_config.preferred_team(self._config))
        if self._postbattle_store is not None:
            client.account_key = self._postbattle_store.account_key
        client.outfits = self._outfit_provider()
        try:
            client.vehicle_compact_descr = self._vehicle_compact_provider()
        except Exception:
            client.vehicle_compact_descr = ''
        if self._effective_params_provider is not None:
            try:
                client.effective_params = self._effective_params_provider()
            except Exception as error:
                sys.stdout.write(
                    '[Offline LAN 0.9.22] selected vehicle projection '
                    'failed: %s\n' % error)
                raise _VehicleSelectionError()
        return client

    def _publish_postbattle_results(self):
        """Let the stock service request and cache each durable pending row."""
        store = self._postbattle_store
        if store is None:
            return False
        arenas = list(store.pending_arenas())
        archived_arena = None
        if not self._archived_result_replayed:
            latest_archived = getattr(store, 'latest_archived_arena', None)
            if callable(latest_archived):
                archived_arena = latest_archived()
                if (archived_arena is not None and
                        archived_arena not in arenas):
                    arenas.append(archived_arena)
        arenas = [arena for arena in arenas
                  if (arena not in self._requested_results and
                      arena not in self._completed_results)]
        notifications = sorted(
            self._completed_results.difference(self._notified_results))
        if not arenas and not notifications:
            return False
        if not self._lobby_ready():
            self._schedule_postbattle_publish()
            return False
        published = self._publish_completed_battle_notifications(
            notifications)
        if not arenas:
            return published
        try:
            from gui.battle_results.context import RequestResultsContext
            from gui.shared.personality import ServicesLocator
            service = ServicesLocator.battleResults
            request_results = getattr(service, 'requestResults', None)
            if not callable(RequestResultsContext) or not callable(
                    request_results):
                raise TypeError(
                    '#1513 battle-results service boundary is invalid')
        except Exception as error:
            self._report_postbattle_service_error(error)
            return published
        self._postbattle_service_error_reported = False
        for arena_unique_id in arenas:
            self._requested_results.add(arena_unique_id)
            request_token = object()
            self._postbattle_request_token = request_token
            generation = self._client_generation
            try:
                is_archived = arena_unique_id == archived_arena
                show_immediately = not is_archived
                show_policy = getattr(
                    store, 'should_show_immediately', None)
                if show_immediately and callable(show_policy):
                    show_immediately = bool(show_policy(arena_unique_id))
                def completed(success, arena=arena_unique_id,
                              archived=is_archived,
                              token=request_token,
                              client_generation=generation):
                    if (self._postbattle_request_token is not token or
                            self._stopped or
                            client_generation != self._client_generation):
                        return
                    self._requested_results.discard(arena)
                    self._postbattle_request_token = None
                    if not success:
                        self._retry_postbattle_result(arena)
                        return
                    self._postbattle_result_retry_attempted.discard(arena)
                    if archived:
                        self._archived_result_replayed = True
                        # The native 1501 ACK already made this a historical
                        # result.  Rehydrate the process-local cache without
                        # publishing the same service message again.
                        self._notified_results.add(arena)
                    self._completed_results.add(arena)
                    # #1513 BattleResultsCache accepts one request at a time.
                    # Its callback runs after releasing that wait gate, so
                    # publish this notification and drain the next durable
                    # result serially.
                    self._publish_postbattle_results()
                context = RequestResultsContext(
                    arena_unique_id, show_immediately, False, True)
                _start_adisp_request(request_results, context, completed)
            except Exception as error:
                self._requested_results.discard(arena_unique_id)
                if self._postbattle_request_token is request_token:
                    self._postbattle_request_token = None
                if isinstance(error, (AttributeError, TypeError)):
                    self._report_postbattle_service_error(error)
                else:
                    sys.stdout.write(
                        '[Offline LAN 0.9.22] battle result %s could not be '
                        'requested: %s\n' % (arena_unique_id, error))
                    self._retry_postbattle_result(arena_unique_id)
                return published
            return True
        return published

    def _report_postbattle_service_error(self, error):
        """Report a permanent #1513 service/ABI mismatch only once."""
        if self._postbattle_service_error_reported:
            return
        self._postbattle_service_error_reported = True
        sys.stdout.write(
            '[Offline LAN 0.9.22] native battle-results service is '
            'unavailable: %s\n' % error)

    def _publish_completed_battle_notifications(self, arenas):
        """Publish cached results without fetching them from Account again."""
        store = self._postbattle_store
        published = False
        for arena_unique_id in arenas:
            message_data = store.service_message_data(arena_unique_id)
            if message_data is None:
                self._report_postbattle_notification_error(
                    arena_unique_id,
                    'native service-message data is unavailable')
                if arena_unique_id not in \
                        self._postbattle_notification_retry_attempted:
                    self._postbattle_notification_retry_attempted.add(
                        arena_unique_id)
                    self._schedule_postbattle_publish()
                continue
            if self._publish_battle_service_message(
                    arena_unique_id, message_data):
                self._notified_results.add(arena_unique_id)
                self._postbattle_notification_retry_attempted.discard(
                    arena_unique_id)
                published = True
            elif arena_unique_id not in \
                    self._postbattle_notification_retry_attempted:
                self._postbattle_notification_retry_attempted.add(
                    arena_unique_id)
                self._schedule_postbattle_publish()
        return published

    def _retry_postbattle_result(self, arena_unique_id):
        if arena_unique_id in self._postbattle_result_retry_attempted:
            return False
        self._postbattle_result_retry_attempted.add(arena_unique_id)
        return self._schedule_postbattle_publish()

    def _report_postbattle_notification_error(self, arena_unique_id, error):
        if arena_unique_id in self._postbattle_notification_error_reported:
            return
        self._postbattle_notification_error_reported.add(arena_unique_id)
        sys.stdout.write(
            '[Offline LAN 0.9.22] battle result %s notification could '
            'not be published: %s\n' % (arena_unique_id, error))

    def _cancel_postbattle_callback(self):
        callback_id = self._postbattle_callback_id
        self._postbattle_callback_id = None
        self._postbattle_token = None
        self._postbattle_request_token = None
        self._requested_results.clear()
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _schedule_postbattle_publish(self):
        """Wait for the rebuilt lobby, then use its native result service."""
        if (self._stopped or self._postbattle_callback_id is not None or
                not callable(self._callback)):
            return False
        token = object()
        self._postbattle_token = token

        def retry():
            if self._postbattle_token is not token:
                return
            self._postbattle_callback_id = None
            self._postbattle_token = None
            if self._stopped:
                return
            self._publish_postbattle_progress()
            self._publish_postbattle_results()

        callback_id = self._callback(POSTBATTLE_RETRY_DELAY, retry)
        if self._postbattle_token is token:
            self._postbattle_callback_id = callback_id
        return True

    def _publish_battle_service_message(self, arena_unique_id, result_data):
        """Inject one exact #1513 clickable service-channel result message."""
        if arena_unique_id in self._notified_results:
            return False
        try:
            from chat_shared import SYS_MESSAGE_IMPORTANCE, SYS_MESSAGE_TYPE
            from messenger import MessengerEntry
            timestamp = int(time.time())
            chat_action = {
                'sentTime': timestamp,
                'data': {
                    'messageID': int(arena_unique_id),
                    'user_id': 0,
                    'type': SYS_MESSAGE_TYPE.battleResults.index(),
                    'importance': SYS_MESSAGE_IMPORTANCE.normal.index(),
                    'active': True,
                    'started_at': timestamp,
                    'finished_at': None,
                    'created_at': timestamp,
                    'data': dict(result_data),
                },
            }
            MessengerEntry.g_instance.protos.BW.serviceChannel.onReceiveSysMessage(
                chat_action)
        except Exception as error:
            self._report_postbattle_notification_error(
                arena_unique_id, error)
            return False
        self._notified_results.add(arena_unique_id)
        return True

    def _publish_postbattle_progress(self):
        """Refresh the current Account resources after a durable receipt."""
        store = self._postbattle_store
        if store is None or not self._lobby_ready():
            return False
        battles = int(store.progress().get('battles', 0))
        if battles == self._published_progress_battles:
            return False
        try:
            import BigWorld
            publisher = getattr(
                getattr(BigWorld.player(), 'fakeServer', None),
                'publish_postbattle_progress', None)
            if not callable(publisher) or not publisher():
                return False
        except Exception as error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] postbattle resources could not be '
                'published: %s\n' % error)
            return False
        self._published_progress_battles = battles
        return True

    def on_lobby_view_loaded(self):
        """Drain a completed battle as soon as the rebuilt lobby can do so.

        The receipt itself is already durable while the battle space is
        leaving.  Waiting only for the periodic retry leaves a small window in
        which the player can open the LAN waiting room before the stock
        Account update and result request begin.
        """
        if self._stopped:
            return False
        progress_published = self._publish_postbattle_progress()
        results_published = self._publish_postbattle_results()
        return bool(progress_published or results_published)

    def _publish_selected_vehicle(self):
        """Send the current garage tank so the next round uses it."""
        client = self.client
        select = getattr(client, 'select_vehicle', None)
        if client is None or not callable(select):
            return False
        try:
            vehicle, max_health = self._vehicle_provider()
            max_health = int(max_health)
            outfits = self._outfit_provider()
            vehicle_compact_descr = self._vehicle_compact_provider()
            effective_params = (None if self._effective_params_provider is None
                                else self._effective_params_provider())
        except Exception:
            # A lobby transition can hide the garage selection.  Keep the
            # vehicle the server already holds for this player.
            return False
        if not vehicle or max_health < 1:
            return False
        try:
            if effective_params is None:
                return bool(select(
                    vehicle, max_health, outfits, vehicle_compact_descr))
            return bool(select(
                vehicle, max_health, outfits, vehicle_compact_descr,
                effective_params))
        except TypeError:
            # Test doubles and the first protocol-v5 client accepted only the
            # original vehicle and max-health arguments.
            return bool(select(vehicle, max_health))

    def _return_to_join_after_vehicle_selection_error(self):
        self.client = None
        self.state = 'ready_to_join'
        self._host_player_id = None
        self._waiting_notice_host_id = None
        self._map_pool = None
        self._pending_map = None
        self._connection_error_notified = False
        if self._picker_open:
            self._close_picker_after_event()
        self._status_notifier(VEHICLE_SELECTION_WARNING)
        return False

    def start(self):
        if self._stopped or self.client is not None:
            return False
        try:
            self.client = self._new_client()
        except _VehicleSelectionError:
            return self._return_to_join_after_vehicle_selection_error()
        self.state = 'connecting'
        try:
            started = bool(self.client.start())
        except Exception as error:
            self._retry_initial_connection({'message': str(error)})
            return False
        if not started:
            self._retry_initial_connection(
                {'message': 'LAN client could not start'})
        return started

    def install(self):
        """Own the native Battle button without joining until the user clicks."""
        if self._stopped:
            return False
        if self._join_ui is None:
            self._join_ui = self._join_factory(self.join)
            self._join_ui.install()
        self.state = 'ready_to_join'
        # A previous process may have durably accepted a receipt before the
        # stock result service became available. Drain it as soon as this
        # lobby finishes loading, without requiring another LAN join.
        self._publish_postbattle_results()
        return True

    def revive(self):
        """Return a stopped or parked session to a clickable Battle button."""
        self._stopped = False
        self._cancel_retry_callback()
        self._cancel_postbattle_callback()
        self._cancel_picker_close_callback()
        self._cancel_round_end_watchdog()
        self._clear_pending_battle_start()
        client = self.client
        self.client = None
        self._client_generation += 1
        if client is not None:
            try:
                client.on_event = None
                client.stop()
            except Exception:
                pass
        self._battle_started = False
        self._starting_round_id = None
        self._active_round_id = None
        self._departed_round_id = None
        self.snapshot = None
        self._start_requested = False
        self._pending_map = None
        self._map_pool = None
        self._host_player_id = None
        self._waiting_notice_host_id = None
        self._picker_open = False
        self._picker_dismissed = False
        # Only an explicit Battle click may raise the room over the garage.
        self._picker_requested = False
        self._connection_error_notified = False
        self._postbattle_service_error_reported = False
        self._postbattle_result_retry_attempted.clear()
        self._postbattle_notification_retry_attempted.clear()
        self._postbattle_notification_error_reported.clear()
        self.state = 'ready_to_join'
        if self._join_ui is None:
            self._join_ui = self._join_factory(self.join)
        self._join_ui.install()
        return True

    def _cancel_round_end_watchdog(self):
        callback_id = self._round_end_callback_id
        self._round_end_callback_id = None
        self._round_end_token = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _schedule_round_end_watchdog(self):
        """Rejoin the room if the server never closes the departed round.

        The local player has already left the battle, so the only state that
        can still arrive is the waiting roster.  Without this the Battle
        button stays parked in ``awaiting_round_end`` for the whole round.
        """
        if not callable(self._callback):
            return False
        self._cancel_round_end_watchdog()
        token = object()
        self._round_end_token = token

        def expire():
            if self._round_end_token is not token:
                return
            self._round_end_callback_id = None
            self._round_end_token = None
            if self._stopped or self.state != 'awaiting_round_end':
                return
            sys.stdout.write(
                '[Offline LAN 0.9.22] LAN round %r was not closed by the '
                'server; rejoining the room\n' % (self._departed_round_id,))
            self._rejoin_room(user_requested=False)

        callback_id = self._callback(ROUND_END_TIMEOUT, expire)
        if self._round_end_token is token:
            self._round_end_callback_id = callback_id
        return True

    def _enter_awaiting_round_end(self):
        self.state = 'awaiting_round_end'
        self._schedule_round_end_watchdog()
        return True

    def _rejoin_room(self, user_requested=True):
        """Drop a parked socket and reconnect so the server resynchronises.

        An automatic rejoin keeps the room dismissed and silent: the player is
        in the garage and never asked for it.  ``revive()`` disarms the room,
        so a Battle click has to ask for it again here or the first click
        after a round produces nothing.
        """
        self.revive()
        self._picker_dismissed = not user_requested
        self._picker_requested = bool(user_requested)
        sys.stdout.write(
            '[Offline LAN 0.9.22] LAN rejoin requested: %s (user=%r)\n' %
            (self._endpoint_value(), bool(user_requested)))
        if not self.start():
            if self.state not in ('ready_to_join', 'retrying'):
                self._status_notifier('The LAN room could not be rejoined.')
            return False
        if user_requested:
            self._status_notifier(
                'Joining LAN room at %s...' % self._endpoint_value())
        return True

    def join(self, unused_map_id=None, unused_action_name=None):
        """Handle the stock lobby Battle button as one LAN-room join action.

        Every state answers the click.  A click that produced neither a room
        nor a message is what makes the button look dead after a round.
        """
        if self._stopped or self.state in ('error', 'stopped'):
            sys.stdout.write(
                '[Offline LAN 0.9.22] LAN session was %s; rebuilding it\n' %
                (self.state,))
            return bool(self._rejoin_room())
        if self.state in ('awaiting_round_end', 'awaiting_lobby_for_battle'):
            return bool(self._rejoin_room())
        if self.state == 'awaiting_battle_start':
            self._status_notifier(
                'The LAN round is starting. Wait for the battle to load.')
            return True
        if self.client is None:
            self._picker_dismissed = False
            self._picker_requested = True
            sys.stdout.write(
                '[Offline LAN 0.9.22] LAN join requested: %s\n' %
                self._endpoint_value())
            if not self.start():
                if self.state not in ('ready_to_join', 'retrying'):
                    self._status_notifier(
                        'The LAN room could not be joined.')
            else:
                self._status_notifier(
                    'Joining LAN room at %s...' % self._endpoint_value())
            return True
        if self.state in ('connecting', 'retrying'):
            self._status_notifier(
                'Still connecting to LAN room at %s. '
                'Opening server settings.' %
                self._endpoint_value())
            self._open_connection_picker()
        elif self.state == 'waiting':
            # An explicit Battle click is the user's request to reopen a room
            # they previously dismissed.
            self._picker_dismissed = False
            self._picker_requested = True
            self._publish_selected_vehicle()
            if not self._is_local_host():
                self._show_waiting_notice(force=True)
            self._open_waiting_picker()
        else:
            return bool(self._rejoin_room())
        return True

    def leave_room(self):
        """Leave the LAN room and return to the garage."""
        self._picker_open = False
        self._picker_dismissed = True
        self._cancel_retry_callback()
        self._cancel_round_end_watchdog()
        self._cancel_picker_callback()
        self._leave_queue_screen()
        client = self.client
        self.client = None
        if client is not None:
            client.on_event = None
            client.stop()
        self.state = 'ready_to_join'
        self._host_player_id = None
        self._waiting_notice_host_id = None
        self._map_pool = None
        self._pending_map = None
        self._start_requested = False
        self._connection_error_notified = False
        self._status_notifier(
            'You left the LAN room. Click Battle! to join again.')
        return True

    def _endpoint_value(self):
        return port_config.format_endpoint(
            self._config.get('host', '127.0.0.1'),
            self._config.get('port', 28782))

    @staticmethod
    def _player_label(player):
        if not isinstance(player, dict):
            return None
        value = player.get('name')
        if not value:
            value = player.get('id', player.get('player_id'))
        if value is None:
            return None
        try:
            label = text_type(value)
        except Exception:
            return None
        label = u' '.join(label.split())
        return label[:16] or None

    def _picker_description(self):
        """Describe the live room inside the stock editable comment field."""
        players = list(getattr(self.client, 'roster', ()) or ())
        labels = []
        for player in players:
            label = self._player_label(player)
            if label is not None:
                labels.append(label)
        shown = labels[:3]
        if len(labels) > len(shown):
            shown.append(u'+%d more' % (len(labels) - len(shown)))
        if shown:
            roster = u', '.join(shown)
        else:
            roster = u'waiting for roster'
        lines = [
            text_type(self._endpoint_value()),
            u'PLAYERS (%d): %s' % (len(players), roster),
        ]
        if self._is_local_host():
            lines.extend((
                u'SELECT A MAP, THEN CLICK CREATE TO START',
                u'OTHER PLAYERS JOIN WITH THE BATTLE BUTTON',
            ))
        elif self.state == 'waiting':
            lines.extend((
                u'WAITING FOR %s TO START THE BATTLE' %
                text_type(self._host_name()),
                u'NO ACTION NEEDED; THE BATTLE OPENS AUTOMATICALLY',
            ))
        else:
            lines.extend((
                u'EDIT THE FIRST LINE TO CHANGE THE SERVER',
                u'THEN CLICK CREATE TO CONNECT',
            ))
        return u'\n'.join(lines)

    def _cancel_retry_callback(self):
        callback_id = self._retry_callback_id
        self._retry_callback_id = None
        self._retry_token = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _schedule_connection_retry(self):
        if self._retry_callback_id is not None or not callable(self._callback):
            return False
        token = object()
        source_client = self.client
        self._retry_token = token

        def retry():
            if (self._retry_token is not token or
                    self.client is not source_client):
                return
            self._retry_callback_id = None
            self._retry_token = None
            if self._stopped:
                return
            old_client = self.client
            if old_client is not None:
                old_client.on_event = None
                old_client.stop()
            self.client = None
            try:
                self.client = self._new_client()
            except _VehicleSelectionError:
                self._return_to_join_after_vehicle_selection_error()
                return
            self.state = 'connecting'
            if not self.client.start():
                self.state = 'retrying'
                self._schedule_connection_retry()

        callback_id = self._callback(RECONNECT_DELAY, retry)
        # BigWorld callbacks are asynchronous, but retaining this guard makes
        # the owner correct under a synchronous test scheduler as well.
        if self._retry_token is token:
            self._retry_callback_id = callback_id
        return True

    def _retry_initial_connection(self, message):
        self.state = 'retrying'
        if not self._connection_error_notified:
            error = _message_value(message, 'message', 'connection failed')
            host = self._config.get('host', '127.0.0.1')
            port = self._config.get('port', 28782)
            self._status_notifier(
                'LAN server %s:%s is unavailable (%s). Retrying and opening '
                'server settings.' %
                (host, port, error))
            sys.stdout.write(
                '[Offline LAN 0.9.22] LAN connection failed: %s:%s (%s)\n' %
                (host, port, error))
            self._connection_error_notified = True
            try:
                self._open_connection_picker()
            except Exception:
                # Endpoint editing is a convenience surface.  It must never
                # replace a recoverable socket failure with a lobby crash.
                pass
        if self._schedule_connection_retry():
            return True
        return False

    def _map_pool_value(self):
        if self._map_pool is None:
            return None
        return list(self._map_pool)

    def _server_supports_random_map(self):
        supported = getattr(self.client, 'has_random_map', None)
        return bool(callable(supported) and supported())

    def _server_supports_team_selection(self):
        supported = getattr(self.client, 'has_team_selection', None)
        return bool(callable(supported) and supported())

    def _server_supports_team_size_selection(self):
        supported = getattr(self.client, 'has_team_size_selection', None)
        return bool(callable(supported) and supported())

    def _team_status(self):
        sizes = dict(getattr(self.client, 'team_sizes', {}) or {})
        counts = {1: 0, 2: 0}
        for player in list(getattr(self.client, 'roster', ()) or ()):
            if not isinstance(player, dict):
                continue
            team = player.get('team')
            if team in counts:
                counts[team] += 1
        return {
            'team': getattr(self.client, 'team', None),
            'sizes': sizes,
            'counts': counts,
            'supported': self._server_supports_team_selection(),
            'size_supported': self._server_supports_team_size_selection(),
        }

    def _bot_tier_status(self):
        return {
            'mode': getattr(self.client, 'bot_tier_mode', 'random'),
            'supported': callable(getattr(
                self.client, 'set_bot_tier_mode', None)),
        }

    def select_team(self, team):
        """Ask the server to move this waiting-room player."""
        if self._stopped or self.client is None or self.state != 'waiting':
            return False
        selector = getattr(self.client, 'select_team', None)
        if not callable(selector) or not selector(team):
            self._status_notifier(
                'The LAN server did not accept that team selection.')
            return False
        self._remember_team(team)
        self._status_notifier('Requesting Team %d...' % int(team))
        return True

    def set_team_size(self, team, size):
        """Ask the server to change one capacity without restarting."""
        if (self._stopped or self.client is None or self.state != 'waiting' or
                not self._is_local_host()):
            return False
        setter = getattr(self.client, 'set_team_size', None)
        if not callable(setter) or not setter(team, size):
            self._status_notifier(
                'The LAN server did not accept that team size.')
            return False
        self._remember_team_size(team, size)
        self._status_notifier(
            'Requesting Team %d size %d...' % (int(team), int(size)))
        return True

    def set_bot_tier_mode(self, mode):
        """Ask the elected host server to use one Bot-tier preset."""
        if (self._stopped or self.client is None or self.state != 'waiting' or
                not self._is_local_host()):
            return False
        setter = getattr(self.client, 'set_bot_tier_mode', None)
        if not callable(setter) or not setter(mode):
            self._status_notifier(
                'The LAN server did not accept that Bot tier preset.')
            return False
        self._status_notifier('Requesting Bot tier preset...')
        return True

    def _save_room_preferences(self):
        if port_config.save_waiting_room_state(self._room_preferences):
            return True
        self._status_notifier(
            'Could not save the LAN waiting room choices. Check that the '
            'user data directory is writable.')
        return False

    def _remember_map(self, map_name):
        if not map_name:
            return False
        if self._room_preferences.get('map') == map_name:
            return True
        self._room_preferences['map'] = map_name
        return self._save_room_preferences()

    def _remember_team(self, team):
        try:
            team = int(team)
        except (TypeError, ValueError, OverflowError):
            return False
        if team not in (0, 1, 2):
            return False
        if self._room_preferences.get('team') == team:
            return True
        self._room_preferences['team'] = team
        return self._save_room_preferences()

    def _remember_team_size(self, team, size):
        try:
            team = int(team)
            size = int(size)
        except (TypeError, ValueError, OverflowError):
            return False
        if team not in (1, 2) or size < 1 or size > 15:
            return False
        sizes = self._room_preferences.setdefault('team_sizes', {})
        if sizes.get(team, sizes.get(str(team))) == size:
            return True
        sizes[team] = size
        return self._save_room_preferences()

    def _restore_waiting_preferences(self):
        """Apply saved capacities and team once after each connection."""
        generation = self._client_generation
        if (self._restored_team_sizes_generation != generation and
                self._is_local_host() and
                self._server_supports_team_size_selection()):
            setter = getattr(self.client, 'set_team_size', None)
            current_sizes = dict(
                getattr(self.client, 'team_sizes', {}) or {})
            restored = callable(setter)
            if callable(setter):
                for team, size in sorted(
                        self._room_preferences.get('team_sizes', {}).items()):
                    current = current_sizes.get(
                        team, current_sizes.get(str(team)))
                    if current != size and not setter(team, size):
                        restored = False
            if restored:
                self._restored_team_sizes_generation = generation
        if (self._restored_team_generation != generation and
                self._server_supports_team_selection()):
            team = int(self._room_preferences.get('team', 0) or 0)
            selector = getattr(self.client, 'select_team', None)
            restored = True
            if team in (1, 2) and getattr(self.client, 'team', None) != team:
                restored = callable(selector) and bool(selector(team))
            if restored:
                self._restored_team_generation = generation

    def _ensure_queue(self):
        if self._queue is None:
            surface = self._new_room()
            if surface is None:
                surface = self._queue_factory(
                    self.request_start, self._map_pool_value,
                    endpoint=self._picker_description,
                    on_close=self._on_picker_closed)
                surface.install()
            self._queue = surface
        return self._queue

    def _ensure_queue_screen(self):
        if self._queue_screen is None and self._queue_screen_factory is not None:
            try:
                screen = self._queue_screen_factory(self._on_queue_screen_exit)
                screen.install()
            except Exception as error:
                sys.stdout.write(
                    '[Offline LAN 0.9.22] the stock queue screen is '
                    'unavailable, keeping the room over the hangar: %s\n' %
                    error)
                self._queue_screen_factory = None
                return None
            self._queue_screen = screen
        return self._queue_screen

    def _leave_queue_screen(self):
        if self._queue_screen is None:
            return False
        return bool(self._queue_screen.leave())

    def _on_queue_screen_exit(self):
        """Leave the LAN room when the stock queue exit control dequeues."""
        if self._stopped or self.client is None:
            return False
        if self.state not in ('waiting', 'awaiting_battle_start'):
            return False
        self._close_picker()
        return self.leave_room()

    def _new_room(self):
        """Build the self-drawn room, or report that this client cannot."""
        if self._room_factory is None:
            return None
        try:
            options = {
                'status': self._room_status,
                'on_close': self.leave_room,
                'host': self._is_local_host,
                'random_supported': self._server_supports_random_map,
            }
            if self._room_factory is waiting_room_ui.WaitingRoomUI:
                options.update({
                    'request_team': self.select_team,
                    'team_status': self._team_status,
                    'request_team_size': self.set_team_size,
                    'bot_tier_status': self._bot_tier_status,
                    'request_bot_tier_mode': self.set_bot_tier_mode,
                    'initial_map': self._room_preferences.get('map'),
                    'on_map_selected': self._remember_map,
                })
            room = self._room_factory(
                self.request_start, self._map_pool_value, **options)
            room.install()
        except Exception as error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] the native waiting room is unavailable, '
                'using the stock map window: %s\n' % error)
            return None
        return room

    def _room_status(self):
        """Describe the live room for the self-drawn waiting room."""
        players = list(getattr(self.client, 'roster', ()) or ())
        labels = []
        for player in players:
            label = self._player_label(player)
            if label is not None:
                labels.append(label)
        shown = labels[:6]
        if len(labels) > len(shown):
            shown.append(u'+%d more' % (len(labels) - len(shown)))
        lines = [
            text_type(self._endpoint_value()),
            u'PLAYERS (%d): %s' % (len(players),
                                   u', '.join(shown) or u'waiting for roster'),
        ]
        if not self._is_local_host():
            lines.append(u'WAITING FOR %s TO START THE BATTLE' %
                         text_type(self._host_name()))
        return u'\n'.join(lines)

    def _open_surface(self, surface):
        opener = getattr(surface, 'open', None)
        if callable(opener):
            return opener()
        return self._picker_opener()

    def _guest_surface(self):
        return bool(getattr(self._queue, 'guest_view', False))

    def _refresh_surface(self):
        refresh = getattr(self._queue, 'refresh', None)
        if callable(refresh):
            return refresh()
        return False

    def _on_picker_closed(self):
        self._picker_open = False
        self._picker_cleanup_pending = False
        # A close disarms the room: it took a host election after a refused
        # start to raise it over the garage again.  Only the Battle button
        # rearms it.
        self._picker_requested = False
        if (not self._stopped and self.state == 'waiting' and
                (self._is_local_host() or self._guest_surface())):
            self._picker_dismissed = True

    def _cancel_picker_callback(self):
        callback_id = self._picker_callback_id
        self._picker_callback_id = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _cancel_picker_close_callback(self):
        callback_id = self._picker_close_callback_id
        self._picker_close_callback_id = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _close_picker_after_event(self):
        """Close the Scaleform picker after its current event stack returns."""
        if self._picker_close_callback_id is not None:
            return True
        if not callable(self._callback):
            # Never fall back to a synchronous native view teardown.  The
            # battle_start poll event can safely close an otherwise-retained
            # picker after the Scaleform update callback has returned.
            return False

        def close_picker():
            self._picker_close_callback_id = None
            if not self._stopped:
                self._close_picker()

        self._picker_close_callback_id = self._callback(0.0, close_picker)
        return True

    def _schedule_picker_when_lobby_ready(self):
        if self._picker_callback_id is not None or not callable(self._callback):
            return False

        def retry():
            self._picker_callback_id = None
            if (not self._stopped and self.state == 'waiting' and
                    not self._picker_dismissed):
                self._open_waiting_picker('lobby ready')

        self._picker_callback_id = self._callback(0.10, retry)
        return True

    def _cancel_battle_start_callback(self):
        callback_id = self._battle_start_callback_id
        self._battle_start_callback_id = None
        if callback_id is not None and callable(self._cancel_callback):
            self._cancel_callback(callback_id)

    def _clear_pending_battle_start(self):
        self._cancel_battle_start_callback()
        self._pending_battle_start = None

    def _schedule_battle_when_lobby_ready(self):
        if (self._battle_start_callback_id is not None or
                not callable(self._callback)):
            return False

        def retry():
            self._battle_start_callback_id = None
            if self._stopped or self._pending_battle_start is None:
                return
            if not self._lobby_ready():
                self._schedule_battle_when_lobby_ready()
                return
            message = self._pending_battle_start
            self._pending_battle_start = None
            self._start_battle(message)

        self._battle_start_callback_id = self._callback(0.10, retry)
        return True

    def _defer_battle_until_lobby_ready(self, message):
        self._pending_battle_start = dict(message or {})
        self.state = 'awaiting_lobby_for_battle'
        self._cancel_picker_close_callback()
        self._cancel_picker_callback()
        if self._picker_open:
            self._close_picker()
        self._schedule_battle_when_lobby_ready()
        return False

    def _open_waiting_picker(self, reason='battle click'):
        """Raise the room. Only an explicit Battle click may ask for this.

        Every other caller reached this while the player was in the garage,
        which is how the room kept appearing over an open maintenance dialog.
        """
        if (self._stopped or self._picker_open or
                self._picker_cleanup_pending or self._picker_dismissed):
            return False
        if not self._picker_requested:
            return False
        if not self._lobby_ready():
            self._schedule_picker_when_lobby_ready()
            return False
        self._cancel_picker_callback()
        surface = self._ensure_queue()
        if not self._is_local_host() and not self._guest_surface():
            # The stock map window can only present the elected room host.
            return False
        # As in 0.8.2, the stock queue screen loads under the room.
        screen = self._ensure_queue_screen()
        if screen is not None:
            screen.open()
        self._picker_open = bool(self._open_surface(surface))
        if self._picker_open:
            sys.stdout.write(
                '[Offline LAN 0.9.22] LAN room opened: %s\n' % reason)
        return self._picker_open

    def _open_connection_picker(self):
        """Open the native form explicitly to edit an unreachable endpoint."""
        if (self._stopped or self._picker_open or
                self.state not in ('connecting', 'retrying') or
                bool(getattr(self.client, 'ready', False))):
            return False
        if not self._lobby_ready():
            return False
        # Before welcome, map_pool is None and QueueUI shows the local standard
        # maps.  The selection is provisional: the server later decides both
        # the valid pool and whether this client is the room host.
        surface = self._ensure_queue()
        if getattr(surface, 'guest_view', False):
            # The self-drawn room never edits the server address. The launcher
            # owns that address before the client starts.
            return False
        self._picker_open = bool(self._open_surface(surface))
        return self._picker_open

    def _is_local_host(self):
        player_id = getattr(self.client, 'player_id', None)
        host_player_id = self._host_player_id
        if host_player_id is None:
            host_player_id = getattr(self.client, 'host_player_id', None)
        return player_id is not None and player_id == host_player_id

    def _host_name(self):
        host_player_id = self._host_player_id
        for player in getattr(self.client, 'roster', ()) or ():
            if (isinstance(player, dict) and
                    player.get('id') == host_player_id):
                return player.get('name') or str(host_player_id)
        return str(host_player_id or 'unknown')

    def _show_waiting_notice(self, force=False):
        if (not force and
                self._waiting_notice_host_id == self._host_player_id):
            return
        self._waiting_notice_host_id = self._host_player_id
        self._status_notifier(
            'Joined LAN room. Waiting for host %s to choose the map.' %
            self._host_name())

    def _sync_waiting_surface(self, previous_host_player_id=None):
        if self._is_local_host():
            self._waiting_notice_host_id = None
            if (previous_host_player_id is not None and
                    previous_host_player_id != self._host_player_id):
                self._status_notifier(
                    'You are now the LAN room host. Choose a map to start.')
            self._open_waiting_picker('host election')
            return
        self._show_waiting_notice(
            force=previous_host_player_id != self._host_player_id)
        if self._picker_open:
            if self._guest_surface():
                self._refresh_surface()
            else:
                self._close_picker()
            return
        # The self-drawn room also presents guests, but a roster update may
        # never raise it over the garage on its own.
        self._open_waiting_picker('guest roster')

    def _close_picker(self):
        self._cancel_picker_callback()
        queue = self._queue
        if queue is None:
            self._picker_open = False
            self._picker_cleanup_pending = False
            return True
        if not self._picker_open and not self._picker_cleanup_pending:
            return True
        close = getattr(queue, 'close', None)
        try:
            closed = True if not callable(close) else close()
        except Exception:
            self._picker_cleanup_pending = True
            raise
        if closed is False:
            self._picker_cleanup_pending = True
            sys.stdout.write(
                '[Offline LAN 0.9.22] waiting-room native close remains '
                'pending; retaining its owner\n')
            return False
        self._picker_open = False
        self._picker_cleanup_pending = False
        return True

    def _save_endpoint(self, value):
        # The stock training description is also the only editable text area
        # available without shipping a custom SWF.  Room status occupies the
        # following lines, while the first non-empty line remains the endpoint
        # accepted by older packages.
        try:
            lines = value.splitlines()
        except AttributeError:
            lines = ()
        for line in lines:
            if line.strip():
                value = line.strip()
                break
        try:
            host, port = port_config.parse_endpoint(
                value, self._config.get('port', 28782))
        except ValueError as error:
            self._status_notifier(str(error))
            return False
        changed = (host != self._config.get('host') or
                   port != self._config.get('port'))
        if not changed:
            return True
        if not port_config.save_endpoint(host, port):
            self._status_notifier(
                'Could not save the LAN server address. Check that the user '
                'data directory is writable.')
            return False
        self._config['host'] = host
        self._config['port'] = port
        self._connection_error_notified = False
        self._cancel_retry_callback()
        old_client = self.client
        if old_client is not None:
            old_client.on_event = None
            old_client.stop()
        self.client = None
        try:
            self.client = self._new_client()
        except _VehicleSelectionError:
            self._return_to_join_after_vehicle_selection_error()
            return False
        self.state = 'connecting'
        if not self.client.start():
            self.state = 'retrying'
            self._schedule_connection_retry()
        return True

    def request_start(self, map_name, endpoint=None):
        if self._stopped:
            return False
        # The native button can deliver more than one event before the
        # next-tick picker close runs.  One accepted selection owns the
        # transition until the server accepts or denies it.
        if self._start_requested:
            return False
        if endpoint is not None and not self._save_endpoint(endpoint):
            return False
        if self._pending_map is not None:
            return False
        if self.client is None:
            return False
        if self.state not in ('waiting', 'connecting', 'retrying'):
            return False
        self._remember_map(map_name)
        if not bool(getattr(self.client, 'ready', False)):
            self._pending_map = map_name
            self._status_notifier(
                'Connecting to %s. The selected map will start automatically.' %
                self._endpoint_value())
            self._close_picker_after_event()
            return True
        accepted = bool(self.client.request_start(map_name))
        if accepted:
            self._start_requested = True
            self._pending_map = None
            self.state = 'awaiting_battle_start'
            self._close_picker_after_event()
        return accepted

    def _stop_active_round(self):
        try:
            if self._battle_runtime is not None:
                self._battle_runtime.stop(show_login=False)
        finally:
            # Runtime cleanup can fail while reconstructing the Account.  The
            # old round is still gone, so never retain ownership flags that
            # route later snapshots into a stopped runtime.
            self._battle_started = False
            self._starting_round_id = None
            self._active_round_id = None
            self.snapshot = None
            self._start_requested = False

    def _waiting_event(self, message):
        self._cancel_retry_callback()
        self._cancel_round_end_watchdog()
        recovered_connection = self._connection_error_notified
        previous_host_player_id = self._host_player_id
        self._host_player_id = _message_value(
            message, 'host_player_id',
            getattr(self.client, 'host_player_id', None))
        self._connection_error_notified = False
        if recovered_connection:
            self._status_notifier('LAN server connected.')
        self._map_pool = list(_message_value(
            message, 'map_pool', getattr(self.client, 'map_pool', [])) or [])
        phase = _message_value(message, 'phase', getattr(self.client, 'phase', None))
        round_id = _message_value(
            message, 'round_id', getattr(self.client, 'round_id', None))
        if (phase in ('loading', 'battle') and self._battle_started and
                round_id == self._active_round_id and
                self._battle_runtime is not None):
            on_roster = getattr(self._battle_runtime, 'on_roster', None)
            if callable(on_roster):
                on_roster(message)
        if phase == 'waiting':
            returning_from_round = (
                self._battle_started or self._active_round_id is not None or
                self._departed_round_id is not None)
            self._clear_pending_battle_start()
            self._starting_round_id = None
            if self._battle_started:
                sys.stdout.write(
                    '[Offline LAN 0.9.22] LAN round %r ended: the server '
                    'returned the room to waiting\n' % (self._active_round_id,))
                try:
                    self._stop_active_round()
                except Exception:
                    self.state = 'error'
                    try:
                        self.stop(show_login=True)
                    except Exception:
                        pass
                    raise
            # A waiting roster is the server-owned barrier that makes a
            # locally departed player eligible for another battle.
            self._departed_round_id = None
            if returning_from_round:
                # Coming back from a round returns the player to the GARAGE.
                # Treat the room as dismissed so the waiting roster cannot
                # reopen it over the garage; only a Battle click reopens it.
                self._picker_dismissed = True
                self._picker_requested = False
            if self._start_requested:
                self.state = 'awaiting_battle_start'
                return
            self.state = 'waiting'
            self._restore_waiting_preferences()
            self._publish_postbattle_progress()
            self._publish_postbattle_results()
            self._publish_selected_vehicle()
            if self._picker_open and self._queue is not None:
                self._refresh_surface()
            if self._pending_map is not None:
                pending_map = self._pending_map
                self._pending_map = None
                if not self._is_local_host():
                    # A connection-settings form can be used before the
                    # server has elected a host.  Guests never turn that
                    # provisional map choice into a start request.
                    self._sync_waiting_surface(previous_host_player_id)
                    return
                if (pending_map not in self._map_pool and not (
                        pending_map == waiting_room_ui.RANDOM_MAP_OPTION and
                        self._server_supports_random_map())):
                    self._status_notifier(
                        'The LAN server does not offer the selected map.')
                    self._sync_waiting_surface(previous_host_player_id)
                    return
                if self.client.request_start(pending_map):
                    self._start_requested = True
                    self.state = 'awaiting_battle_start'
                    self._close_picker()
                    return
                self._status_notifier(
                    'The LAN server did not accept the start request.')
            self._sync_waiting_surface(previous_host_player_id)
        elif phase == 'battle':
            # Disconnect/failover roster updates are broadcast during a live
            # round.  They update membership but must not demote the active
            # local battle back to an awaiting state.
            if (self._departed_round_id is not None and
                    round_id == self._departed_round_id):
                self._enter_awaiting_round_end()
            else:
                self.state = ('battle' if self._battle_started
                              else 'awaiting_battle_start')

    def _player_details(self, message):
        player_id = getattr(self.client, 'player_id', None)
        spawn = _message_value(message, 'spawn')
        vehicle = _message_value(message, 'vehicle')
        roster = (_message_value(message, 'players') or
                  _message_value(message, 'roster', []) or [])
        for player in roster:
            if not isinstance(player, dict):
                continue
            identifier = player.get('id', player.get('player_id'))
            if player_id is not None and identifier != player_id:
                continue
            spawn = player.get('spawn', spawn)
            if spawn is None and all(axis in player for axis in ('x', 'y', 'z')):
                spawn = {'x': player['x'], 'y': player['y'], 'z': player['z']}
            vehicle = player.get('vehicle', vehicle)
            break
        if spawn is None:
            spawn = getattr(self.client, 'spawn', None)
        if vehicle is None:
            vehicle = getattr(self.client, 'vehicle', None)
        return (_spawn(spawn), vehicle)

    def _start_battle(self, message):
        round_id = _message_value(message, 'round_id',
                                  getattr(self.client, 'round_id', None))
        if self._starting_round_id is not None:
            return False
        if round_id == self._departed_round_id:
            return False
        if (self._departed_round_id is not None and
                round_id != self._departed_round_id):
            self._departed_round_id = None
        if self._battle_started and round_id == self._active_round_id:
            return False
        if self._battle_started:
            try:
                self._stop_active_round()
            except Exception:
                self.state = 'error'
                try:
                    self.stop(show_login=True)
                except Exception:
                    pass
                raise
        # LAN messages are dispatched from a BigWorld poll callback, after the
        # Scaleform method that sent the request has returned.  If the next-tick
        # picker close has not run yet, it is safe to finish it on this stack
        # before any native Account/Avatar transition begins.
        self._cancel_picker_close_callback()
        self._cancel_picker_callback()
        if ((self._picker_open or self._picker_cleanup_pending) and
                not self._close_picker()):
            return self._defer_battle_until_lobby_ready(message)
        if not self._lobby_ready():
            return self._defer_battle_until_lobby_ready(message)
        self._clear_pending_battle_start()
        map_name = _message_value(message, 'map',
                                  _message_value(message, 'map_name',
                                                 getattr(self.client, 'map_name', None)))
        spawn, vehicle = self._player_details(message)
        if not map_name or spawn is None or not vehicle:
            self.state = 'error'
            return False
        if self._battle_runtime is None:
            self._battle_runtime = _load_battle_runtime()
        config = dict(self._config)
        config.update({'map': map_name, 'spawn': spawn, 'vehicle': vehicle})
        if 'startupTimeoutSeconds' not in config:
            config['startupTimeoutSeconds'] = 30.0
        # The runtime can report a synchronous native failure from inside
        # start().  Record ownership before entering it, then only commit the
        # active round if that ownership token survived the callback.
        self._starting_round_id = round_id
        try:
            started = bool(self._battle_runtime.start(
                config, message=message, lan_client=self.client,
                on_local_leave=self._on_local_battle_leave))
        except Exception:
            if self._starting_round_id == round_id:
                self._starting_round_id = None
            raise
        owns_start = self._starting_round_id == round_id
        restore_pending = False
        pending_check = getattr(
            self._battle_runtime, 'lobby_restore_pending', None)
        if owns_start and not started and callable(pending_check):
            restore_pending = bool(pending_check())
        if owns_start and not restore_pending:
            self._starting_round_id = None
        elif restore_pending:
            sys.stdout.write(
                '[Offline LAN 0.9.22] battle start failed; retaining round '
                '%r until deferred lobby recovery completes\n' % round_id)
        if started and owns_start and not self._stopped:
            self._battle_started = True
            self._active_round_id = round_id
            self._start_requested = False
            self._pending_map = None
            self.state = 'battle'
            return True
        return False

    def _on_local_battle_leave(self):
        """Retire one local round while retaining the waiting-room socket."""
        if self._stopped or not self._battle_started:
            return False
        sys.stdout.write(
            '[Offline LAN 0.9.22] local player left LAN round %r\n' %
            (self._active_round_id,))
        self._departed_round_id = self._active_round_id
        errors = []
        try:
            leave = getattr(self.client, 'leave_battle', None)
            if not callable(leave) or not leave():
                raise RuntimeError('LAN server did not accept battle leave')
        except Exception as error:
            errors.append(error)
        try:
            self._stop_active_round()
        except Exception as error:
            errors.append(error)
        if errors:
            self.state = 'error'
            try:
                # _stop_active_round already retired (or attempted to retire)
                # the native runtime.  Finish the remaining owners without a
                # duplicate stop call through the same failed boundary.
                self.stop(show_login=True, stop_runtime=False)
            except Exception:
                pass
            raise errors[0]
        self._enter_awaiting_round_end()
        return True

    def _on_battle_failed(self, message):
        """Handle a local native-runtime failure without faking socket loss."""
        owned_round_id = self._starting_round_id
        if owned_round_id is None and self._battle_started:
            owned_round_id = self._active_round_id
        round_id = _message_value(message, 'round_id', owned_round_id)
        # A duplicate failure for a locally-departed round, or a delayed
        # callback from an older round, must not tear down a newer Avatar or
        # send a second leave_battle request.
        if owned_round_id is None or round_id != owned_round_id:
            return False
        self._clear_pending_battle_start()
        self._starting_round_id = None
        self._battle_started = False
        self._active_round_id = None
        self._departed_round_id = round_id
        self.snapshot = None
        self._start_requested = False
        self._pending_map = None
        reason = _message_value(message, 'message', 'unknown error')
        sys.stdout.write(
            '[Offline LAN 0.9.22] battle aborted for round %r: %s '
            '(lobby_restored=%r)\n' %
            (round_id, reason,
             bool(_message_value(message, 'lobby_restored', False))))

        if bool(_message_value(message, 'lobby_restored', False)):
            leave = getattr(self.client, 'leave_battle', None)
            try:
                if not callable(leave) or not leave():
                    raise RuntimeError(
                        'LAN server did not accept failed battle leave')
            except Exception:
                self.state = 'error'
                self._status_notifier(
                    'Battle could not start (%s). LAN session stopped.' %
                    reason)
                try:
                    # BattleRuntime already owns cleanup and Account restore.
                    # Close only the LAN/picker owners on this failure path.
                    self.stop(show_login=True, restore_account=False,
                              stop_runtime=False)
                except Exception:
                    pass
                return False
            self._enter_awaiting_round_end()
            self._status_notifier(
                'Battle could not start (%s). Returning to the map picker.' %
                reason)
            return True

        self.state = 'error'
        self._status_notifier(
            'Battle could not start and the lobby was not restored (%s).' %
            reason)
        try:
            # Runtime recovery already failed.  Do not recurse through its
            # cleanup or Account reconstruction a second time.
            self.stop(show_login=True, restore_account=False,
                      stop_runtime=False)
        except Exception:
            pass
        return False

    def _return_to_join_after_waiting_disconnect(self, message):
        """Retire one lost waiting-room socket without leaving the lobby."""
        reason = _message_value(message, 'message',
                                _message_value(message, 'reason',
                                               'connection lost'))
        old_client = self.client

        # Move out of waiting before closing the native picker. Its close
        # callback must not reopen a surface owned by the retired connection.
        self.state = 'ready_to_join'
        self._client_generation += 1
        self.client = None
        self._host_player_id = None
        self._waiting_notice_host_id = None
        self._map_pool = None
        self.snapshot = None
        self._starting_round_id = None
        self._active_round_id = None
        self._departed_round_id = None
        self._battle_started = False
        self._start_requested = False
        self._pending_map = None
        self._connection_error_notified = False

        try:
            self._cancel_retry_callback()
        except Exception:
            pass
        try:
            self._cancel_picker_close_callback()
        except Exception:
            pass
        try:
            self._clear_pending_battle_start()
        except Exception:
            pass
        try:
            self._close_picker()
        except Exception:
            pass
        try:
            self._leave_queue_screen()
        except Exception:
            pass
        if old_client is not None:
            try:
                old_client.on_event = None
            except Exception:
                pass
            try:
                old_client.stop()
            except Exception:
                pass

        self._status_notifier(
            'LAN room connection lost (%s). Click Battle! to rejoin.' %
            reason)
        return True

    def _donation_runtime(self):
        try:
            return _load_donation_runtime()
        except Exception as error:
            print('[Offline LAN 0.9.22] descriptor donation modules are '
                  'unavailable: %s' % error)
            return None

    def _send_vehicle_catalog(self):
        """Donate the eligible-vehicle catalog for server-side lineups."""
        runtime = self._donation_runtime()
        if runtime is None:
            print('[Offline LAN 0.9.22] vehicle catalog donation skipped: '
                  'the exact vehicle list is unavailable')
            return
        try:
            from gui.mods.offline_lan_0922 import descriptor_donation
            rows = descriptor_donation.vehicle_catalog(runtime)
        except Exception as error:
            print('[Offline LAN 0.9.22] vehicle catalog donation '
                  'failed: %s' % error)
            return
        if not rows:
            print('[Offline LAN 0.9.22] vehicle catalog donation skipped: '
                  'the runtime vehicle list is empty')
            return
        if not self.client.send_descriptor_catalog(rows):
            print('[Offline LAN 0.9.22] vehicle catalog donation was not '
                  'accepted by the transport (%d rows)' % len(rows))

    def _on_event(self, kind, message):
        if self._stopped:
            return
        if kind in ('welcome', 'roster'):
            if kind == 'welcome':
                self._send_vehicle_catalog()
            self._waiting_event(message)
        elif kind == 'start_denied':
            # Concurrent start requests can produce an accepted battle_start
            # and a denial for this client's losing request in either order.
            # Once an accepted start is active or retained behind the lobby
            # gate, the denial must not cancel that server-owned transition.
            if (self._battle_started or
                    self._pending_battle_start is not None):
                return
            self._clear_pending_battle_start()
            self._start_requested = False
            self.state = 'waiting'
            # The player asked for this start, so its refusal may bring the
            # room back.  _picker_requested still gates the garage case.
            self._picker_dismissed = False
            self._host_player_id = _message_value(
                message, 'host_player_id',
                getattr(self.client, 'host_player_id',
                        self._host_player_id))
            if _message_value(message, 'code') == 'host_only':
                self._status_notifier(
                    'Only the LAN room host can choose the map and start.')
            else:
                self._status_notifier(
                    'The LAN server refused the battle start (%s).' %
                    (_message_value(message, 'code') or 'unknown'))
            self._sync_waiting_surface()
        elif kind == 'team_denied':
            code = _message_value(message, 'code')
            team = _message_value(message, 'team')
            actual_team = getattr(self.client, 'team', None)
            if actual_team in (1, 2):
                self._remember_team(actual_team)
            if code == 'team_full':
                self._status_notifier(
                    'Team %s is full. Choose the other team or wait for a '
                    'slot.' % team)
            else:
                self._status_notifier(
                    'The LAN server refused the team selection (%s).' %
                    (code or 'unknown'))
            self._refresh_surface()
        elif kind == 'team_size_denied':
            code = _message_value(message, 'code')
            team = _message_value(message, 'team')
            actual_sizes = dict(
                getattr(self.client, 'team_sizes', {}) or {})
            try:
                actual_size = actual_sizes.get(
                    int(team), actual_sizes.get(str(team)))
            except (TypeError, ValueError, OverflowError):
                actual_size = None
            if actual_size is not None:
                self._remember_team_size(team, actual_size)
            if code == 'host_only':
                notice = 'Only the LAN room host can change team sizes.'
            elif code == 'team_occupied':
                notice = ('Team %s already has too many players for that '
                          'size.' % team)
            else:
                notice = ('The LAN server refused the team size (%s).' %
                          (code or 'unknown'))
            self._status_notifier(notice)
            reject = getattr(self._queue, 'reject_team_size', None)
            if callable(reject):
                reject(team, notice)
            else:
                self._refresh_surface()
        elif kind == 'bot_tier_mode_denied':
            code = _message_value(message, 'code')
            if code == 'host_only':
                notice = ('Only the LAN room host can change the Bot tier '
                          'preset.')
            else:
                notice = ('The LAN server refused the Bot tier preset (%s).' %
                          (code or 'unknown'))
            self._status_notifier(notice)
            reject = getattr(self._queue, 'reject_bot_tier_mode', None)
            if callable(reject):
                reject(_message_value(message, 'bot_tier_mode'), notice)
            else:
                self._refresh_surface()
        elif kind == 'battle_start':
            self._start_battle(message)
        elif kind == 'battle_live':
            round_id = _message_value(message, 'round_id')
            if (not self._battle_started or
                    round_id != self._active_round_id or
                    self._battle_runtime is None):
                return
            self._battle_runtime.on_battle_live(message)
        elif kind == 'bot_observation':
            round_id = _message_value(message, 'round_id')
            if (not self._battle_started or
                    round_id != self._active_round_id or
                    self._battle_runtime is None):
                return
            self._battle_runtime.on_bot_observation(message)
        elif kind == 'battle_failed':
            self._on_battle_failed(message)
        elif kind == 'battle_receipt':
            store = self._postbattle_store
            if store is None:
                return
            try:
                accepted = store.accept(message)
            except Exception as error:
                sys.stdout.write(
                    '[Offline LAN 0.9.22] battle receipt was rejected: %s\n'
                    % error)
                return
            # Store.accept() returns only after its atomic JSON replacement.
            # Ack duplicates too: they already exist in durable local state,
            # and the server may be retrying because an earlier ACK was lost.
            acknowledge = getattr(
                self.client, 'acknowledge_battle_receipt', None)
            if callable(acknowledge):
                acknowledge(_message_value(message, 'receipt_id'))
            if accepted:
                self._publish_postbattle_progress()
            self._publish_postbattle_results()
        elif kind == 'snapshot':
            round_id = _message_value(message, 'round_id')
            if (not self._battle_started or
                    (round_id is not None and
                     round_id != self._active_round_id)):
                return
            self.snapshot = message
            if self._battle_runtime is not None:
                self._battle_runtime.on_snapshot(message)
            if self._on_snapshot is not None:
                self._on_snapshot(message)
        elif kind == 'events':
            round_id = _message_value(message, 'round_id')
            if (not self._battle_started or
                    (round_id is not None and
                     round_id != self._active_round_id)):
                return
            if self._battle_runtime is not None:
                self._battle_runtime.on_events(message)
        elif kind == 'fire_intent_result':
            round_id = _message_value(message, 'round_id')
            if (self._battle_started and
                    round_id == self._active_round_id and
                    self._battle_runtime is not None):
                self._battle_runtime.on_fire_intent_result(message)
        elif kind == 'player_destructible_contact_result':
            round_id = _message_value(message, 'round_id')
            if (self._battle_started and
                    round_id == self._active_round_id and
                    self._battle_runtime is not None):
                self._battle_runtime.on_player_destructible_contact_result(
                    message)
        elif kind in ('disconnected', 'connection_lost', 'error'):
            if (not self._battle_started and
                    bool(getattr(self.client, 'ready', False)) and
                    getattr(self.client, 'phase', None) == 'waiting'):
                self._return_to_join_after_waiting_disconnect(message)
            elif (kind == 'error' and not self._battle_started and
                    not bool(getattr(self.client, 'ready', False)) and
                    _message_value(message, 'code') in
                    ('team_full', 'invalid_team')):
                code = _message_value(message, 'code')
                if code == 'team_full':
                    self._status_notifier(
                        'The selected LAN team is full. Choose the other '
                        'team or Automatic in the launcher.')
                else:
                    self._status_notifier(
                        'The launcher team selection is invalid.')
                self.stop(show_login=True)
            elif (kind == 'error' and not self._battle_started and
                    not bool(getattr(self.client, 'ready', False)) and
                    self._retry_initial_connection(message)):
                if self._on_event_callback is not None:
                    self._on_event_callback(kind, message)
                return
            else:
                # The stock window owns cursor capture. Closing it before
                # uninstalling our wrappers lets its normal close path release
                # it on active-battle and unrecoverable failure paths.
                if self._battle_started:
                    reason = _message_value(
                        message, 'message',
                        _message_value(message, 'reason', 'connection lost'))
                    reason = str(reason)[:160]
                    sys.stdout.write(
                        '[Offline LAN 0.9.22] active LAN transport failed '
                        'kind=%s round=%r: %s\n' %
                        (kind, self._active_round_id, reason))
                    self._status_notifier(
                        'LAN battle connection lost (%s). Returning to the '
                        'garage.' % reason)
                self.stop(show_login=True)
        if self._on_event_callback is not None:
            self._on_event_callback(kind, message)

    def stop(self, show_login=True, restore_account=True,
             stop_runtime=True, release_join=False):
        """Retire this session.

        ``release_join`` restores the retail Battle button and belongs to mod
        shutdown only.  An error path keeps our button installed, otherwise the
        next click reaches retail matchmaking and the LAN room is unreachable
        until the client restarts.
        """
        if self._stopped:
            return
        self._stopped = True
        errors = []
        try:
            self._cancel_retry_callback()
        except Exception as error:
            errors.append(error)
        try:
            self._cancel_postbattle_callback()
        except Exception as error:
            errors.append(error)
        try:
            self._cancel_round_end_watchdog()
        except Exception as error:
            errors.append(error)
        try:
            self._cancel_picker_close_callback()
        except Exception as error:
            errors.append(error)
        try:
            self._clear_pending_battle_start()
        except Exception as error:
            errors.append(error)
        picker_closed = False
        try:
            picker_closed = self._close_picker()
        except Exception as error:
            errors.append(error)
        # ``close() is False`` means native roots/cursor ownership remains;
        # never uninstall and drop those Python owners in that state.
        if picker_closed and self._queue is not None:
            try:
                if self._queue.uninstall() is False:
                    raise RuntimeError(
                        'waiting-room uninstall remains pending')
            except Exception as error:
                errors.append(error)
        if picker_closed and self._queue_screen is not None:
            try:
                if self._queue_screen.uninstall() is False:
                    raise RuntimeError(
                        'queue-screen uninstall remains pending')
            except Exception as error:
                errors.append(error)
        if self._join_ui is not None and release_join:
            try:
                self._join_ui.uninstall()
            except Exception as error:
                errors.append(error)
        if self.client is not None:
            try:
                self.client.on_event = None
            except Exception as error:
                errors.append(error)
            try:
                self.client.stop()
            except Exception as error:
                errors.append(error)
        if self._battle_runtime is not None and stop_runtime:
            try:
                self._battle_runtime.stop(
                    show_login=show_login,
                    restore_account=restore_account)
            except Exception as error:
                errors.append(error)
        self._battle_started = False
        self._starting_round_id = None
        self._active_round_id = None
        self._departed_round_id = None
        self.snapshot = None
        self._start_requested = False
        self._pending_map = None
        self.state = 'stopped'
        if errors:
            raise errors[0]

    def fini(self, show_login=True, restore_account=True, stop_runtime=True):
        self.stop(show_login=show_login, restore_account=restore_account,
                  stop_runtime=stop_runtime, release_join=True)
