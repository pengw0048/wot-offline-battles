"""Replay native engine lifecycle events when an audible owner is replaced.

DetailedEngineState outlives the audition while a vehicle is unspotted. A late
startEngineWithDelay request is not a state transfer to a new sound owner.
Keep the exact native callback arguments (no guessed C++ arity or fake RPM),
then seed only the new audition after its normal model/water setup.
"""
from __future__ import print_function
import sys
import time
import weakref
from gui.mods.offline_lan_0922 import engine_audio_probe as _probe

_RELAYS = weakref.WeakValueDictionary()
_HOOKS = None
_VOLUME_OWNER = None
_VOLUME_HISTORY = {}


def _log(text):
    try:
        sys.stdout.write('[Offline LAN 0.9.22] ENGINE_AUDIO_V2 ' + text + '\n')
    except (IOError, ValueError, AttributeError):
        # Diagnostic output must never retire engine audio.
        return


def _primitive(value):
    # Diagnostics only, never transform arguments forwarded to native code.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return '<%s>' % type(value).__name__


class EngineRelay(object):
    def __init__(self, appearance, detailed):
        self.appearance = weakref.ref(appearance)
        self.state_id = id(detailed)
        self.audible = True
        self.last_start = None
        self.last_state = None
        self.sink_id = None
        self.start_count = self.state_count = self.start_sent = self.state_sent = 0
        self.hydrated = 0
        self.closed = False
        self.next_report = 0.0
        self.start_callback = self.on_start
        self.state_callback = self.on_state
        _RELAYS[self.state_id] = self

    def current(self):
        a = self.appearance()
        if self.closed or a is None:
            return None
        try:
            d = getattr(a, 'detailedEngineState', None)
        except (AttributeError, ReferenceError):
            return None
        return a if d is not None and id(d) == self.state_id else None

    def _sink(self):
        a = self.current()
        if a is None or not self.audible:
            return None
        return getattr(a, 'engineAudition', None)

    def on_start(self, *args, **kwargs):
        self.last_start = (args, kwargs)
        self.start_count += 1
        sink = self._sink()
        if sink is not None:
            sink.onEngineStart(*args, **kwargs)
            self.start_sent += 1
        a = self.current()
        if _probe.enabled() and a is not None:
            _probe.note(a, 'start', args=_probe.primitive(args), sent=sink is not None)
        self.report('start', force=self.start_count <= 2)

    def on_state(self, *args, **kwargs):
        self.last_state = (args, kwargs)
        self.state_count += 1
        sink = self._sink()
        if sink is not None:
            sink.onEngineStateChanged(*args, **kwargs)
            self.state_sent += 1
        a = self.current()
        if _probe.enabled() and a is not None:
            _probe.note(a, 'state', args=_probe.primitive(args), sent=sink is not None)
        self.report('state')

    def suspend(self):
        # Retain the DetailedEngineState callback observer, NOT the old native
        # audition wrapper. ComponentDescriptor destroys that wrapper on removal.
        self.audible = False
        self.sink_id = None
        a = self.current()
        if _probe.enabled() and a is not None:
            a._engineDiagEverMuted = True
            a._engineDiagDirect = False
            _probe.note(a, 'suspend', hidden_sink_will_be_removed=True)
        self.report('muted', force=True)

    def bind(self, audition, detailed):
        a = self.current()
        if a is None or id(detailed) != self.state_id:
            raise RuntimeError('engine lifecycle belongs to another appearance')
        old_sink_id = self.sink_id
        self.audible = getattr(a, '_offlineLANMutedEngine', None) is None
        self.sink_id = id(audition) if audition is not None else None
        direct = audition is not None and self.audible and _probe.direct_initial(a)
        if direct:
            # B only: reselect the exact native callbacks after EVERY reveal.
            # No Python event forwarding wrapper lies on this direct path.
            detailed.onEngineStart = audition.onEngineStart
            detailed.onStateChanged = audition.onEngineStateChanged
        else:
            detailed.onEngineStart = self.start_callback
            detailed.onStateChanged = self.state_callback
        if _probe.enabled():
            a._engineDiagDirect = direct
            _probe.note(a, 'subscribe', direct=direct, sink= _probe.ident(audition),
                        state= _probe.ident(detailed), role=_probe.role(a))
        motion = getattr(a, '_offlineEngineMotion', None)
        if motion is not None and audition is not None:
            vehicle_filter = getattr(a, 'filter', None)
            if vehicle_filter is not None:
                audition.setSpeedInfo(motion[1], lambda: vehicle_filter.strafeSpeed)
        if self.audible and audition is not None and old_sink_id != self.sink_id:
            # Do not ask an already running engine to start again. Initialize
            # the newly attached sound receiver from actual native events.
            if self.last_start is not None:
                args, kwargs = self.last_start
                audition.onEngineStart(*args, **kwargs)
                self.start_sent += 1
                self.hydrated += 1
            if self.last_state is not None:
                args, kwargs = self.last_state
                audition.onEngineStateChanged(*args, **kwargs)
                self.state_sent += 1
        if direct and old_sink_id != self.sink_id:
            _probe.note(a, 'direct_rebound', captured_start_available=self.last_start is not None,
                        captured_state_available=self.last_state is not None,
                        no_fabricated_native_events=True, sink=_probe.ident(audition))
        self.report('bound', force=True)

    def report(self, phase, force=False):
        try:
            self._report(phase, force)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            # Native wrappers may already be retiring during model changes.
            # This catches diagnostics only, never the audio event delivery.
            return

    def _report(self, phase, force=False):
        now = time.time()
        if not force and now < self.next_report:
            return
        self.next_report = now + 10.0
        a = self.current()
        if a is None:
            return
        d = a.detailedEngineState
        sink = getattr(a, 'engineAudition', None)
        sound_groups = sys.modules.get('SoundGroups')
        sounds = getattr(sound_groups, 'g_instance', None)
        volumes = {}
        if sounds is not None:
            for key in ('vehicles', 'effects', 'gui'):
                try:
                    volumes[key] = sounds.getVolume(key)
                except (AttributeError, KeyError, ReferenceError):
                    volumes[key] = None
        descr = getattr(a, 'typeDescriptor', None)
        engine = getattr(descr, 'engine', None)
        names = getattr(engine, 'sounds', None)
        event = None
        try:
            entity = getattr(a, '_CompoundAppearance__vehicle', None)
            event = names.getWWPlayerSound(bool(getattr(entity, 'isPlayerVehicle', False)))
        except (AttributeError, ReferenceError):
            pass
        _log('phase=%s id=%s audible=%s owner=%s starts=%s/%s states=%s/%s hydrate=%s mode=%s rpm=%s event=%r volumes=%r start_args=%r' %
             (phase, getattr(a, 'id', '?'), self.audible, sink is not None,
              self.start_count, self.start_sent, self.state_count, self.state_sent,
              self.hydrated, getattr(d, 'mode', None), getattr(d, 'rpm', None), event,
              volumes, None if self.last_start is None else tuple(_primitive(x) for x in self.last_start[0])))
        _log('context id=%s active=%s engine_tracks_enabled=%r changed_volumes=%r' %
             (getattr(a, 'id', '?'), getattr(a, 'activated', None),
              getattr(sound_groups, 'ENABLE_ENGINE_N_TRACKS', None), dict(_VOLUME_HISTORY)))

    def close(self):
        a = self.current()
        if a is not None:
            d = a.detailedEngineState
            if _probe.enabled() and getattr(a, '_engineDiagDirect', False):
                sink = getattr(a, 'engineAudition', None)
                for field in ('onEngineStart', 'onStateChanged'):
                    native_cb = getattr(d, field, None)
                    native_target = getattr(native_cb, '__self__', getattr(native_cb, 'im_self', None))
                    if sink is not None and native_target is sink:
                        setattr(d, field, None)
                a._engineDiagDirect = False
            _probe.note(a, 'release', state_id=self.state_id)
            for name, cb in (('onEngineStart', self.start_callback), ('onStateChanged', self.state_callback)):
                if getattr(d, name, None) is cb:
                    setattr(d, name, None)
        self.closed = True
        self.audible = False
        self.last_start = self.last_state = None
        self.sink_id = None
        if _RELAYS.get(self.state_id) is self:
            _RELAYS.pop(self.state_id, None)


def register(appearance):
    detailed = getattr(appearance, 'detailedEngineState', None)
    if detailed is None:
        return None
    owner = getattr(appearance, '_offlineEngineRelay', None)
    if owner is None or owner.closed or owner.state_id != id(detailed):
        if owner is not None:
            owner.close()
        owner = EngineRelay(appearance, detailed)
        appearance._offlineEngineRelay = owner
    return owner


def suspend(appearance):
    owner = register(appearance)
    if owner is not None:
        # Hook normally installed before assembly; also make the current native
        # callback ownership explicit before ComponentDescriptor removes audio.
        detailed = appearance.detailedEngineState
        detailed.onEngineStart = owner.start_callback
        detailed.onStateChanged = owner.state_callback
        owner.suspend()
    return owner


def bind_motion(appearance, speed, rotation, is_player=False):
    """Complete the native motion readers after the copied pose is attached."""
    detailed = getattr(appearance, 'detailedEngineState', None)
    if detailed is None:
        return False
    detailed.vehicleSpeedLink = speed
    detailed.rotationSpeedLink = rotation
    model = getattr(appearance, 'compoundModel', None)
    if model is not None:
        # This input was captured by native assembly BEFORE copied LAN poses.
        detailed.vehicleMatrixLink = model.root
    audition = getattr(appearance, 'engineAudition', None)
    if audition is not None:
        # Native model setup attached audio before the LAN model switched
        # matrix providers. Reattach via the same stock model-refresh API.
        if model is not None and not _probe.skip_extra_attach():
            audition.attachToModel(model)
        # Preserve the native lateral-speed reader; only angular speed has
        # a canonical copied-motion producer in this port.
        vehicle_filter = getattr(appearance, 'filter', None)
        if vehicle_filter is not None:
            audition.setSpeedInfo(rotation, lambda: vehicle_filter.strafeSpeed)
    appearance._offlineEngineMotion = (speed, rotation)
    _probe.note(appearance, 'motion', extra_attach_omitted=_probe.skip_extra_attach())
    owner = register(appearance)
    if owner is not None:
        owner.report('motion')
    return True


def _volume_changed(category, value):
    # Passive bus observation; never alter the user's audio preferences.
    if category in ('vehicles', 'effects', 'gui', 'ambient'):
        _VOLUME_HISTORY[category] = value


def install(assembler=None):
    global _HOOKS, _VOLUME_OWNER
    if _HOOKS is not None:
        return False
    _probe.start()
    if assembler is None:
        from vehicle_systems import model_assembler as assembler
    original_assemble = assembler.assembleVehicleAudition
    original_subscribe = assembler.subscribeEngineAuditionToEngineState

    def assemble(is_player, appearance):
        result = original_assemble(is_player, appearance)
        _probe.track(appearance, is_player)
        if _probe.enabled():
            appearance._engineDiagPlayerHint = bool(is_player)
        register(appearance)
        return result

    def subscribe(audition, detailed):
        result = original_subscribe(audition, detailed)
        relay = _RELAYS.get(id(detailed))
        if relay is not None:
            relay.bind(audition, detailed)
        return result

    _HOOKS = (assembler, original_assemble, original_subscribe, assemble, subscribe)
    assembler.assembleVehicleAudition = assemble
    assembler.subscribeEngineAuditionToEngineState = subscribe
    sounds = getattr(sys.modules.get('SoundGroups'), 'g_instance', None)
    event = getattr(sounds, 'onVolumeChanged', None)
    if event is not None:
        event += _volume_changed
        _VOLUME_OWNER = event
    return True


def release(appearance):
    owner = getattr(appearance, '_offlineEngineRelay', None)
    if owner is not None:
        owner.close()
        appearance._offlineEngineRelay = None
    if appearance is not None:
        appearance._offlineEngineMotion = None


def uninstall():
    global _HOOKS, _VOLUME_OWNER
    if _VOLUME_OWNER is not None:
        try:
            _VOLUME_OWNER -= _volume_changed
        except (KeyError, ValueError, ReferenceError):
            # SoundGroups may already have cleared its Event during fini.
            _log('volume_observer_already_retired=True')
        _VOLUME_OWNER = None
    _VOLUME_HISTORY.clear()
    for owner in list(_RELAYS.values()):
        owner.close()
    if _HOOKS is not None:
        module, old_a, old_s, new_a, new_s = _HOOKS
        if module.assembleVehicleAudition is new_a:
            module.assembleVehicleAudition = old_a
        if module.subscribeEngineAuditionToEngineState is new_s:
            module.subscribeEngineAuditionToEngineState = old_s
        _HOOKS = None
    _probe.stop()
