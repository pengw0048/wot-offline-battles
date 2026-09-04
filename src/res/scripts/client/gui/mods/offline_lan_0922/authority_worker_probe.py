from __future__ import print_function

"""Reversible exact-client probe for a future bot authority worker.

This is deliberately not a worker implementation and it changes no LAN
protocol.  When explicitly enabled by the user, one current bot-authority
client measures whether the existing BigWorld callback loop and bot
publication path keep advancing with world drawing disabled and with the game
window briefly hidden.  The client rolls back world drawing itself; a separate
supervisor owns hiding and restoration so the process being tested is never
responsible for recovering its own window.
"""

import json
import os
import sys
import time


PROBE_SCHEMA = 1
STAGE_NAMES = ('draw_on', 'draw_off', 'window_hidden')
DEFAULT_REPORT_PATH = os.path.join(
    '.', 'mods', 'configs', 'offline_lan_0922',
    'authority_worker_probe.jsonl')


def _wall_clock():
    monotonic = getattr(time, 'monotonic', None)
    if callable(monotonic):
        return float(monotonic())
    # Python 2 time.clock() is a high-resolution wall clock on Windows.
    if os.name == 'nt':
        return float(time.clock())
    return float(time.time())


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result or result in (float('inf'), float('-inf')):
        return None
    return result


def _delta(start, end):
    start = _number(start)
    end = _number(end)
    if start is None or end is None:
        return None
    return end - start


def _integer_delta(start, end):
    try:
        return int(end) - int(start)
    except (TypeError, ValueError, OverflowError):
        return None


def _probe_delta(start, end):
    start = start if isinstance(start, dict) else {}
    end = end if isinstance(end, dict) else {}
    result = {}
    for name in sorted(set(start) | set(end)):
        difference = _integer_delta(start.get(name, 0), end.get(name, 0))
        if difference is not None:
            result[name] = difference
    return result


def write_probe_record(record, path=DEFAULT_REPORT_PATH):
    """Append one compact JSON record without ever affecting gameplay."""
    payload = json.dumps(record, sort_keys=True, separators=(',', ':'))
    wrote = False
    try:
        sys.stdout.write('[AUTHORITY_WORKER_PROBE] %s\n' % payload)
        wrote = True
    except Exception:
        pass
    try:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            try:
                os.makedirs(directory)
            except OSError:
                if not os.path.isdir(directory):
                    raise
        stream = open(path, 'ab')
        try:
            line = payload + '\n'
            if not isinstance(line, bytes):
                line = line.encode('utf-8')
            stream.write(line)
        finally:
            stream.close()
        wrote = True
    except Exception:
        pass
    return wrote


class AuthorityWorkerProbe(object):
    """Run one three-stage measurement without becoming gameplay authority."""

    def __init__(self, bigworld, sample, stage_seconds=15.0, writer=None,
                 clock=None, context=None):
        if not callable(sample):
            raise ValueError('probe sample provider is required')
        self._bigworld = bigworld
        self._sample_provider = sample
        self._stage_seconds = max(0.1, float(stage_seconds))
        self._writer = writer or write_probe_record
        self._clock = clock or _wall_clock
        self._context = dict(context or {})
        self._context.setdefault('process_id', os.getpid())
        self._context.setdefault(
            'run_id', '%s-%s-%s' % (
                os.getpid(), int(time.time() * 1000), id(self)))
        self._active = False
        self._finished = False
        self._callback_count = 0
        self._stage_index = -1
        self._stage_name = None
        self._stage_started = 0.0
        self._stage_next_heartbeat = 0.0
        self._measurement_started = 0.0
        self._measurement_sample = None
        self._warmup_seconds = (
            5.0 if self._stage_seconds >= 15.0 else 0.0)
        self._last_callback_wall = None
        self._callback_gap_max = 0.0
        self._stage_sample = None
        self._space_min = None
        self._space_max = None
        self._draw_disabled = False
        self._draw_original = None
        self._restore_failed = False
        self._results = []

    @property
    def active(self):
        return self._active

    @property
    def finished(self):
        return self._finished

    @property
    def results(self):
        return tuple(dict(value) for value in self._results)

    def _write(self, event, **values):
        record = {
            'schema': PROBE_SCHEMA,
            'probe': 'authority_worker',
            'event': event,
            'wall_time_epoch': time.time(),
        }
        record.update(self._context)
        record.update(values)
        try:
            self._writer(record)
        except Exception:
            # Diagnostics are never allowed to fail or alter the battle.
            pass

    def _sample(self):
        try:
            supplied = self._sample_provider()
            supplied = supplied if isinstance(supplied, dict) else {}
        except Exception as error:
            supplied = {'sample_error': str(error)}
        sample = dict(supplied)
        sample['callbacks'] = self._callback_count
        try:
            sample['bigworld_time'] = _number(self._bigworld.time())
        except Exception as error:
            sample['bigworld_time'] = None
            sample['bigworld_time_error'] = str(error)
        try:
            sample['space_load_status'] = _number(
                self._bigworld.spaceLoadStatus())
        except Exception as error:
            sample['space_load_status'] = None
            sample['space_load_error'] = str(error)
        try:
            sample['world_draw_enabled'] = self._read_draw_state()
        except Exception as error:
            sample['world_draw_enabled'] = None
            sample['world_draw_error'] = str(error)
        return sample

    def start(self):
        if self._active or self._finished:
            return False
        try:
            self._draw_original = self._read_draw_state()
        except Exception as error:
            self._finished = True
            self._write('probe_complete', reason='draw_state_unavailable',
                        error=str(error), completed_stages=0)
            return False
        self._active = True
        self._write('probe_start', stages=list(STAGE_NAMES),
                    stage_seconds=self._stage_seconds)
        self._start_next_stage()
        return self._active

    def tick(self):
        if not self._active:
            return False
        self._callback_count += 1
        callback_wall = self._clock()
        if self._last_callback_wall is not None:
            self._callback_gap_max = max(
                self._callback_gap_max,
                max(0.0, callback_wall - self._last_callback_wall))
        self._last_callback_wall = callback_wall
        elapsed = callback_wall - self._stage_started
        if elapsed + 1e-9 >= self._stage_next_heartbeat:
            sample = self._sample()
            self._observe_space(sample)
            if (self._warmup_seconds > 0.0 and
                    self._measurement_started == self._stage_started and
                    elapsed + 1e-9 >= self._warmup_seconds):
                self._measurement_started = callback_wall
                self._measurement_sample = sample
                self._space_min = sample.get('space_load_status')
                self._space_max = sample.get('space_load_status')
                self._callback_gap_max = 0.0
                self._last_callback_wall = callback_wall
                self._write(
                    'measurement_start', stage=self._stage_name,
                    discarded_seconds=max(0.0, elapsed), sample=sample)
            self._write('stage_heartbeat', stage=self._stage_name,
                        elapsed_seconds=max(0.0, elapsed), sample=sample)
            while self._stage_next_heartbeat <= elapsed + 1e-9:
                self._stage_next_heartbeat += 1.0
        if elapsed + 1e-9 < self._stage_seconds:
            return True
        result = self._complete_stage('completed')
        if result is not None and result.get('restore_error'):
            self.stop('restore_failed')
            return False
        self._start_next_stage()
        return self._active

    def stop(self, reason='stopped'):
        if self._finished:
            return False
        if self._active and self._stage_name is not None:
            self._complete_stage('interrupted', reason=reason)
        restore_error = self._restore_mutations()
        self._active = False
        self._finished = True
        self._write('probe_complete', reason=reason,
                    restore_error=restore_error,
                    completed_stages=len(self._results))
        return True

    def _start_next_stage(self):
        if not self._active:
            return
        self._stage_index += 1
        if self._stage_index >= len(STAGE_NAMES):
            self._active = False
            self._finished = True
            restore_error = self._restore_mutations()
            self._write('probe_complete', reason='completed',
                        restore_error=restore_error,
                        completed_stages=len(self._results))
            return
        self._stage_name = STAGE_NAMES[self._stage_index]
        self._stage_started = self._clock()
        self._measurement_started = self._stage_started
        self._stage_next_heartbeat = 1.0
        self._last_callback_wall = self._stage_started
        self._callback_gap_max = 0.0
        self._stage_sample = self._sample()
        self._measurement_sample = self._stage_sample
        space = self._stage_sample.get('space_load_status')
        self._space_min = space
        self._space_max = space
        try:
            self._apply_stage(self._stage_name)
        except Exception as error:
            # Complete and roll back this unsupported stage, then continue so
            # the other independent measurement can still produce evidence.
            result = self._complete_stage('skipped', reason=str(error))
            if result is not None and result.get('restore_error'):
                self.stop('restore_failed')
                return
            self._start_next_stage()
            return
        self._write(
            'stage_start', stage=self._stage_name,
            stage_seconds=self._stage_seconds,
            world_draw_enabled=self._read_draw_state(),
            external_supervisor_required=(
                self._stage_name == 'window_hidden'),
            sample=self._stage_sample)

    def _apply_stage(self, stage):
        if stage == 'draw_on':
            self._set_draw_state(True)
            return
        if stage == 'draw_off':
            self._set_draw_state(False)
            return
        if stage == 'window_hidden':
            # The in-client Python thread must never hide its own native
            # window: the same condition under test can stop callbacks and
            # prevent that process from restoring itself.  The standalone
            # supervisor watches the stage_start record, owns the target PID,
            # rejects fullscreen, hides it and restores it in a finally block.
            self._set_draw_state(False)
            return
        raise RuntimeError('unknown probe stage %s' % stage)

    def _complete_stage(self, status, reason=None, end_sample=None):
        if self._stage_name is None:
            return None
        end_sample = end_sample or self._sample()
        self._observe_space(end_sample)
        restore_error = self._restore_mutations()
        start = self._measurement_sample or self._stage_sample or {}
        completed_at = self._clock()
        result = {
            'stage': self._stage_name,
            'status': status,
            'reason': reason,
            'stage_wall_seconds': max(
                0.0, completed_at - self._stage_started),
            'discarded_seconds': max(
                0.0, self._measurement_started - self._stage_started),
            'wall_seconds': max(
                0.0, completed_at - self._measurement_started),
            'callback_delta': _integer_delta(
                start.get('callbacks'), end_sample.get('callbacks')),
            'callback_gap_max_ms': self._callback_gap_max * 1000.0,
            'bigworld_time_start': start.get('bigworld_time'),
            'bigworld_time_end': end_sample.get('bigworld_time'),
            'bigworld_time_delta': _delta(
                start.get('bigworld_time'), end_sample.get('bigworld_time')),
            'space_load_start': start.get('space_load_status'),
            'space_load_end': end_sample.get('space_load_status'),
            'space_load_min': self._space_min,
            'space_load_max': self._space_max,
            'world_draw_start': start.get('world_draw_enabled'),
            'world_draw_end': end_sample.get('world_draw_enabled'),
            'bot_state_generated_delta': _integer_delta(
                start.get('bot_state_generated', 0),
                end_sample.get('bot_state_generated', 0)),
            'bot_state_enqueued_delta': _integer_delta(
                start.get('bot_state_enqueued', 0),
                end_sample.get('bot_state_enqueued', 0)),
            'bot_state_send_failed_delta': _integer_delta(
                start.get('bot_state_send_failed', 0),
                end_sample.get('bot_state_send_failed', 0)),
            'bot_state_revision_start': start.get('bot_state_revision'),
            'bot_state_revision_end': end_sample.get('bot_state_revision'),
            'bot_state_revision_delta': _integer_delta(
                start.get('bot_state_revision'),
                end_sample.get('bot_state_revision')),
            'bot_probe_delta': _probe_delta(
                start.get('bot_probes'), end_sample.get('bot_probes')),
            'bot_count_start': start.get('bot_count'),
            'bot_count_end': end_sample.get('bot_count'),
            'authority_callback_delta': _integer_delta(
                start.get('authority_callbacks', 0),
                end_sample.get('authority_callbacks', 0)),
            'simulation_cap_delta': _integer_delta(
                start.get('simulation_caps', 0),
                end_sample.get('simulation_caps', 0)),
            'alive_bot_tick_delta': _integer_delta(
                start.get('alive_bot_ticks', 0),
                end_sample.get('alive_bot_ticks', 0)),
            'restore_error': restore_error,
            'external_supervisor_required': (
                self._stage_name == 'window_hidden'),
        }
        result.update(self._assessment(result))
        self._results.append(result)
        self._write('stage_result', **result)
        self._stage_name = None
        self._stage_sample = None
        return result

    def _restore_mutations(self):
        errors = []
        if self._draw_disabled:
            try:
                setter = getattr(self._bigworld, 'worldDrawEnabled', None)
                if not callable(setter):
                    raise RuntimeError(
                        'BigWorld.worldDrawEnabled is unavailable')
                setter(self._draw_original)
                observed = self._read_draw_state()
                if observed is not self._draw_original:
                    raise RuntimeError(
                        'world draw restore readback mismatch')
                self._draw_disabled = False
            except Exception as error:
                errors.append('world draw: %s' % error)
        self._restore_failed = bool(errors)
        return '; '.join(errors) if errors else None

    def _read_draw_state(self):
        boundary = getattr(self._bigworld, 'worldDrawEnabled', None)
        if not callable(boundary):
            raise RuntimeError('BigWorld.worldDrawEnabled is unavailable')
        value = boundary()
        if value not in (False, True, 0, 1):
            raise RuntimeError('world draw getter returned an invalid state')
        return bool(value)

    def _set_draw_state(self, enabled):
        setter = getattr(self._bigworld, 'worldDrawEnabled', None)
        if not callable(setter):
            raise RuntimeError('BigWorld.worldDrawEnabled is unavailable')
        self._draw_disabled = True
        setter(bool(enabled))
        observed = self._read_draw_state()
        if observed is not bool(enabled):
            raise RuntimeError('world draw setter readback mismatch')

    def _observe_space(self, sample):
        space = sample.get('space_load_status')
        if space is None:
            return
        self._space_min = (space if self._space_min is None else
                           min(self._space_min, space))
        self._space_max = (space if self._space_max is None else
                           max(self._space_max, space))

    @staticmethod
    def _assessment(result):
        if result.get('stage') == 'window_hidden':
            return {
                'assessment': 'RAW_ONLY_EXTERNAL_WINDOW_EVIDENCE_REQUIRED'}
        wall = _number(result.get('wall_seconds')) or 0.0
        callbacks = result.get('authority_callback_delta')
        generated = result.get('bot_state_generated_delta')
        enqueued = result.get('bot_state_enqueued_delta')
        failed = result.get('bot_state_send_failed_delta')
        revision = result.get('bot_state_revision_delta')
        bigworld_delta = _number(result.get('bigworld_time_delta'))
        caps = result.get('simulation_cap_delta')
        alive_ticks = result.get('alive_bot_tick_delta')
        ground = (result.get('bot_probe_delta') or {}).get('ground')
        bot_counts = (
            result.get('bot_count_start'), result.get('bot_count_end'))
        if wall <= 0.0:
            return {'assessment': 'FAIL_STALLED',
                    'assessment_reason': 'empty measurement window'}
        callback_hz = float(callbacks or 0) / wall
        publication_hz = float(enqueued or 0) / wall
        expected = min(callback_hz, 30.0) * wall
        lower = max(0.0, expected * 0.90 - 2.0)
        upper = expected * 1.05 + 2.0
        metrics = {
            'callback_hz': callback_hz,
            'bot_publication_hz': publication_hz,
            'expected_publications': expected,
        }
        failures = []
        if callbacks is None or callbacks <= 1:
            failures.append('callback did not advance')
        if bigworld_delta is None or bigworld_delta <= 0.0:
            failures.append('BigWorld.time did not advance')
        if generated is None or generated <= 0:
            failures.append('bot publication did not advance')
        if enqueued is None or enqueued <= 0:
            failures.append('bot publication was not enqueued')
        if failed not in (None, 0):
            failures.append('bot publication send failed')
        if revision is None or revision <= 0:
            failures.append('server bot_state_revision did not advance')
        numeric_bot_counts = [
            int(value) for value in bot_counts
            if isinstance(value, (int, float))]
        if not numeric_bot_counts or max(numeric_bot_counts) <= 0:
            failures.append('no bots were measured')
        if generated is not None and not lower <= generated <= upper:
            failures.append('bot publication cadence drifted')
        if (result.get('callback_delta') is not None and
                callbacks is not None and
                result.get('callback_delta') != callbacks):
            failures.append('authority callback ownership changed')
        if alive_ticks is None or alive_ticks <= 0:
            failures.append('live bot tick metric did not advance')
        if ground is None:
            failures.append('ground probe metric is unavailable')
        elif alive_ticks is not None and ground < alive_ticks:
            failures.append('ground probes missed live bot ticks')
        if failures:
            metrics.update({
                'assessment': 'FAIL_STALLED',
                'assessment_reason': '; '.join(failures),
            })
        elif callback_hz < 24.0 or caps not in (None, 0):
            reason = ('simulation dt was capped at low callback rate'
                      if caps not in (None, 0) else
                      'authority callback rate below 24 Hz')
            metrics.update({
                'assessment': 'DEGRADED_LOW_FPS',
                'assessment_reason': reason,
            })
        else:
            metrics.update({
                'assessment': 'PASS_OPERATIONAL',
                'assessment_reason': 'all conservative progression checks passed',
            })
        return metrics
