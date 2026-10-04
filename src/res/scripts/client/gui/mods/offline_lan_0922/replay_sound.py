"""Replay-only ownership of native reload sounds (no weapon-state mutation).

The stock HUD still receives its original time/count updates. Its reloadEffect
slot is replaced on this ONE AmmoController, not on a global class/descriptor.
Mechanical sounds have a single, bounded schedule on the replay clock. There
are no additional BigWorld callbacks, negative delays, or 50 simultaneous
one-shot requests when a custom magazine is compressed into a short reload.
"""
from __future__ import print_function
import math

_MIN_CUE_SPACING = 0.06  # Cosmetic floor only; never clamps weapon fire rate.
_MAX_CUES = 64
_LATE_CUE_GRACE = 0.15


def _finite(value, default=0.0):
    try:
        value = float(value)
        if not math.isnan(value) and not math.isinf(value):
            return value
    except (ValueError, TypeError, OverflowError):
        pass
    return float(default)


class NativeSoundBackend(object):
    """Use the same #1513 Wwise event names, switches and handle APIs."""
    def enabled(self):
        from helpers import gEffectsDisabled
        return not gEffectsDisabled()

    def event(self, name):
        import SoundGroups
        SoundGroups.g_instance.playSound2D(name)

    def handle(self, name):
        import SoundGroups
        return SoundGroups.g_instance.getSound2D(name)

    def caliber(self, name):
        if name:
            import SoundGroups
            SoundGroups.g_instance.setSwitch('SWITCH_ext_rld_automat_caliber', name)


def _retire_native(effect):
    """Also cancel the native nested timer, which stop() alone can miss."""
    if effect is None:
        return
    effect.stop()
    for owner in (effect, getattr(effect, '_BarrelReload__reloadSequence', None)):
        clear = getattr(owner, 'clearCallbacks', None)
        if callable(clear):
            clear()


class ReplayReloadSound(object):
    def __init__(self, original, settings, log=None, backend=None):
        self.original = original
        self.settings = settings
        self.desc = getattr(original, '_desc', None)
        self.backend = backend or NativeSoundBackend()
        self.log = log or (lambda text: None)
        self.controller = None
        self.bound_settings = None
        self.queue = []
        self.kind = None
        self.shell_index = None
        self.deadline = None
        self.last_edge = None
        self.last_gun = None
        self.closed = False
        self.loop_running = False
        self.lane = None
        self.last_tick = None
        self.short_played = False
        self.errors = set()
        self.counts = dict(cycles=0, corrections=0, native_starts_suppressed=0,
                           planned_cues=0, coalesced_cues=0, played_cues=0,
                           short_cycles=0, long_cycles=0, failed_sounds=0,
                           maximum_queue=0)

    def _report(self, phase, **fields):
        import json
        fields['phase'] = phase
        self.log('REPLAY_SOUND ' + json.dumps(fields, sort_keys=True, separators=(',', ':')))

    def bind(self, controller):
        current = controller.getGunSettings()
        if current is not self.settings:
            raise RuntimeError('replay reload sound: gun settings changed during binding')
        if getattr(controller, '_AmmoController__gunSettings', None) is not current:
            raise RuntimeError('replay reload sound: #1513 AmmoController owner is unavailable')
        replacement = current._replace(reloadEffect=self)
        _retire_native(self.original)
        controller._AmmoController__gunSettings = replacement
        self.controller, self.bound_settings = controller, replacement
        fields = dict(effect=type(self.original).__name__, clip=int(current.clip.size))
        for name in ('duration', 'shellDuration', 'shellDt', 'shellDtLast',
                     'soundEvent', 'startLong', 'startLoop', 'stopLoop',
                     'loopShell', 'loopShellLast', 'lastShellAlert', 'caliber'):
            value = getattr(self.desc, name, None)
            if value is not None:
                fields[name] = value
        self._report('bound', **fields)
        return self

    def owns(self, controller):
        return (not self.closed and controller is self.controller and
                controller.getGunSettings().reloadEffect is self)

    def start(self, *args):
        # AmmoController.triggerReloadEffect is a HUD side effect, NOT another
        # sound owner. Only the explicit recorded edge below creates a plan.
        self.counts['native_starts_suppressed'] += 1

    def _name(self, field):
        return getattr(self.desc, field, '') or ''

    def _control(self, field):
        name = self._name(field)
        if not name:
            return
        try:
            if self.backend.enabled():
                self.backend.event(name)
        except Exception as error:
            self._sound_error(name, error)

    def _sound_error(self, name, error):
        self.counts['failed_sounds'] += 1
        if name not in self.errors:
            self.errors.add(name)
            self._report('sound_error', event=name, error=str(error))

    def _stop_lane(self):
        sound, self.lane = self.lane, None
        if sound is not None:
            try:
                sound.stop()
            except Exception as error:
                self._sound_error('mechanical_lane_stop', error)

    def _mechanical(self, field, now):
        name = self._name(field)
        if not name:
            return
        self._stop_lane()
        try:
            if self.backend.enabled():
                self.lane = self.backend.handle(name)
                self.lane.play()
                self.counts['played_cues'] += 1
                self.last_tick = now
                if self.kind == 'short':
                    self.short_played = True
        except Exception as error:
            self._sound_error(name, error)

    def _stop_loop(self):
        if self.loop_running:
            self._control('stopLoop')
        self.loop_running = False

    def stop(self):
        # Called both by our lifecycle and by the original ammo clear().
        self.queue = []
        self.kind = None
        self.deadline = None
        self._stop_loop()
        self._stop_lane()

    def close(self):
        if self.closed:
            return
        self.stop()
        if (self.controller is not None and
                self.controller.getGunSettings().reloadEffect is self):
            # Retain any shell/gun changes made since binding; replace only
            # our own effect, never the entire old settings tuple.
            settings = self.controller.getGunSettings()
            self.controller._AmmoController__gunSettings = settings._replace(reloadEffect=self.original)
        self.closed = True
        self._report('summary', **self.counts)
        self.controller = None
        self.bound_settings = None

    def _add(self, when, action, field):
        self.queue.append((float(when), action, field))

    def on_edge(self, publication, gun, stamp, now):
        """An explicit recorded edge, independent of timeLeft == baseTime."""
        if self.closed or publication is None:
            return
        signature = (float(stamp), publication, int(gun.get('clip', 0)),
                     int(gun.get('shot_index', 0)), tuple(gun.get('ammo', ())))
        if signature == self.last_edge:
            return
        self.last_edge = signature
        left, base, reason = publication
        now, stamp = float(now), float(stamp)
        raw = max(0.0, _finite(gun.get('reload_time')))
        if reason == 'complete' or raw <= 0.0:
            # Never replay all expired clicks in one frame. Permit the latest
            # due cue only, then stop ambient loops; let its natural tail end.
            self.pump(now)
            self.queue = []
            self.kind = None
            self.deadline = None
            self._stop_loop()
            return
        barrel = hasattr(self.desc, 'shellDuration')
        clip = int(gun.get('clip', 0))
        kind = 'long' if barrel and clip == 0 else 'short'
        shell_index = int(gun.get('shot_index', 0))
        previous = self.last_gun
        ammo = tuple(int(v) for v in gun.get('ammo', ()))
        # A fast inter-shot interval may have no sampled zero between shots.
        # A recorded expenditure is a NEW cycle, not a correction to the old
        # sound. Damage/reload-factor changes without expenditure stay updates.
        spent = bool(previous is not None and (
            clip < previous[0] or
            (len(ammo) == len(previous[1]) and
             any(a < b for a, b in zip(ammo, previous[1])))))
        new = (reason == 'start' or self.kind != kind or
               self.shell_index != shell_index or spent)
        self.last_gun = (clip, ammo)
        self.queue = []
        if new:
            self._stop_loop()
            self._stop_lane()
            self.last_tick = None
            self.short_played = False
            self.counts['cycles'] += 1
            self.counts[kind + '_cycles'] += 1
        else:
            self.counts['corrections'] += 1
        self.kind, self.shell_index = kind, shell_index
        self.deadline = stamp + raw
        end = max(now, self.deadline)
        try:
            self.backend.caliber(self._name('caliber'))
        except Exception as error:
            self._sound_error('caliber', error)
        planned = 0
        requested = 1
        if kind == 'long':
            ammo = gun.get('ammo', ())
            available = max(0, int(ammo[shell_index])) if 0 <= shell_index < len(ammo) else 0
            requested = min(max(0, int(self.settings.clip.size)), available)
            # Preserve intro/loop/tail assets. The shell tick density is a
            # cosmetic time budget, not the number of rounds put in the gun.
            if new:
                self._add(now, 'control', 'startLong')
            duration = max(0.0, _finite(getattr(self.desc, 'duration', 0.0)))
            window_start = max(now, end - duration)
            tail = min(max(0.0, _finite(getattr(self.desc, 'shellDtLast', 0.0))), end-window_start)
            last = max(window_start, end-tail)
            spacing = max(_MIN_CUE_SPACING, _finite(getattr(self.desc, 'shellDt', 0.5), 0.5))
            room = max(0.0, last-window_start)
            planned = min(requested, _MAX_CUES, 1 + int(math.floor((room+1e-9)/spacing))) if requested else 0
            if not self.loop_running and requested:
                self._add(window_start, 'loop', 'startLoop')
            for i in range(planned):
                t = last if planned == 1 else window_start + room*i/(planned-1)
                if self.last_tick is not None and t < self.last_tick+spacing-1e-9:
                    continue
                self._add(t, 'mechanical', 'loopShellLast' if i == planned-1 else 'loopShell')
            self._add(end, 'stop', 'stopLoop')
            self.counts['coalesced_cues'] += max(0, requested-planned)
        elif not self.short_played:
            lead = max(0.0, _finite(getattr(self.desc, 'shellDuration' if barrel else 'duration', 0.0)))
            # Clamp the DUE TIME and its ownership together. No negative
            # native callback whose original timestamp is already expired.
            due = max(now, end-lead)
            if barrel and clip == 1 and new:
                self._add(now, 'control', 'lastShellAlert')
            self._add(due, 'mechanical', 'soundEvent')
            planned = 1
        self.queue.sort(key=lambda row: row[0])  # stable: loop before its tick
        self.counts['planned_cues'] += planned
        self.counts['maximum_queue'] = max(self.counts['maximum_queue'], len(self.queue))
        if kind == 'long' or self.counts['cycles'] <= 5 or self.counts['cycles'] % 50 == 0:
            self._report('plan', kind=kind, new_cycle=new, reason=reason,
                         stamp=stamp, now=now, raw=raw, hud_left=left,
                         base=base, clip=clip, requested_cues=requested,
                         planned_cues=planned, queue=len(self.queue))
        self.pump(now)

    def pump(self, now):
        if self.closed or not self.queue:
            return
        now = float(now)
        due = 0
        while due < len(self.queue) and self.queue[due][0] <= now + 1e-9:
            due += 1
        if not due:
            return
        batch, self.queue = self.queue[:due], self.queue[due:]
        mechanics = [row for row in batch if row[1] == 'mechanical']
        latest = mechanics[-1] if mechanics else None
        self.counts['coalesced_cues'] += max(0, len(mechanics)-1)
        for row in batch:
            when, action, field = row
            if action == 'stop':
                self._stop_loop()
            elif action == 'mechanical':
                if row is latest and now-when <= _LATE_CUE_GRACE:
                    self._mechanical(field, now)
            elif action == 'loop':
                if self.deadline is not None and now < self.deadline and not self.loop_running:
                    self._control(field)
                    self.loop_running = True
            elif now-when <= _LATE_CUE_GRACE:
                self._control(field)
