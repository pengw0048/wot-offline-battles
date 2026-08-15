from __future__ import absolute_import

import logging
import sys
import time


LOG_PREFIX = '[OFFLINE_2311_POC]'
EXPECTED_CLIENT_VERSION = 'v.2.3.1.1 #916'
POLL_INTERVAL_SECONDS = 0.5
LOAD_TIMEOUT_SECONDS = 120.0

_logger = logging.getLogger(__name__)
_probe = None


def parse_offline_request(argv):
    """Mirror the stock OfflineMode command-line selection exactly."""
    try:
        argv = list(argv)
        index = argv.index('offline')
        return argv[index + 1]
    except (ValueError, IndexError):
        return None


class OfflineModeProbe(object):
    """Observe, but never replace or mutate, the stock OfflineMode lifecycle."""

    def __init__(self, bigworld, offline_mode, space_name, logger=None,
                 now=None, poll_interval=POLL_INTERVAL_SECONDS,
                 timeout=LOAD_TIMEOUT_SECONDS):
        self._bigworld = bigworld
        self._offline_mode = offline_mode
        self._space_name = space_name
        self._logger = logger or _logger
        self._now = now or time.time
        self._poll_interval = float(poll_interval)
        self._timeout = float(timeout)
        self._callback_id = None
        self._started_at = None
        self._stopped = False
        self._completed = False
        self._enabled_seen = False
        self._snapshot_incomplete_seen = False
        self._reported_errors = set()

    @property
    def completed(self):
        return self._completed

    @property
    def callback_id(self):
        return self._callback_id

    def start(self):
        if self._started_at is not None:
            return
        self._started_at = self._now()
        self._logger.info(
            '%s probe_start expected_version=%s space=%s timeout=%.1f',
            LOG_PREFIX, EXPECTED_CLIENT_VERSION, self._space_name,
            self._timeout)
        self._poll()

    def stop(self, reason='fini'):
        if self._stopped:
            return
        self._stopped = True
        callback_id = self._callback_id
        self._callback_id = None
        if callback_id is not None:
            try:
                self._bigworld.cancelCallback(callback_id)
            except Exception:
                self._report_exception_once(
                    'cancel_callback', 'callback_cancel_failed')
        self._logger.info('%s probe_stop reason=%s completed=%s',
                          LOG_PREFIX, reason, self._completed)

    def _report_exception_once(self, key, message):
        if key in self._reported_errors:
            return
        self._reported_errors.add(key)
        self._logger.exception('%s %s', LOG_PREFIX, message)

    def _read_offline_flag(self, name):
        try:
            return bool(getattr(self._offline_mode, name)())
        except Exception:
            self._report_exception_once(
                'offline_flag_' + name,
                'offline_mode_%s_failed' % name)
            return False

    def _read_load_status(self):
        try:
            return float(self._bigworld.spaceLoadStatus())
        except Exception:
            self._report_exception_once(
                'space_load_status', 'space_load_status_failed')
            return -1.0

    def _read_world_snapshot(self):
        try:
            player = self._bigworld.player()
        except Exception:
            self._report_exception_once('player', 'player_lookup_failed')
            player = None
            player_type = 'unavailable'
        else:
            player_type = 'None' if player is None else type(player).__name__

        try:
            camera = self._bigworld.camera()
            camera_type = (
                'None' if camera is None else type(camera).__name__)
        except Exception:
            self._report_exception_once('camera', 'camera_lookup_failed')
            camera_type = 'unavailable'

        try:
            space_ids = sorted(self._bigworld.spaces.keys())
        except Exception:
            self._report_exception_once('spaces', 'spaces_lookup_failed')
            space_ids = 'unavailable'

        return (player_type,
                None if player is None else getattr(player, 'spaceID', None),
                camera_type, space_ids)

    def _schedule(self):
        if self._stopped or self._completed or self._callback_id is not None:
            return
        try:
            self._callback_id = self._bigworld.callback(
                self._poll_interval, self._on_callback)
        except Exception:
            self._stopped = True
            self._report_exception_once(
                'schedule_callback', 'callback_schedule_failed')

    def _on_callback(self):
        self._callback_id = None
        self._poll()

    def _poll(self):
        if self._stopped or self._completed:
            return

        enabled = self._read_offline_flag('enabled')
        loaded = self._read_offline_flag('isSpaceLoaded')
        status = self._read_load_status()

        if enabled and not self._enabled_seen:
            self._enabled_seen = True
            self._logger.info(
                '%s offline_mode_entered space=%s status=%.6f',
                LOG_PREFIX, self._space_name, status)

        if loaded:
            player_type, space_id, camera_type, space_ids = (
                self._read_world_snapshot())
            snapshot_complete = (
                player_type == 'OfflineEntity' and
                camera_type == 'FreeCamera' and
                isinstance(space_ids, list) and
                space_id in space_ids)
            if snapshot_complete:
                self._completed = True
                self._logger.info(
                    '%s space_loaded space=%s status=%.6f player=%s '
                    'space_id=%s camera=%s spaces=%s',
                    LOG_PREFIX, self._space_name, status, player_type,
                    space_id, camera_type, space_ids)
                return
            if not self._snapshot_incomplete_seen:
                self._snapshot_incomplete_seen = True
                self._logger.error(
                    '%s space_loaded_snapshot_incomplete space=%s '
                    'player=%s space_id=%s camera=%s spaces=%s',
                    LOG_PREFIX, self._space_name, player_type, space_id,
                    camera_type, space_ids)

        elapsed = self._now() - self._started_at
        if elapsed >= self._timeout:
            self._stopped = True
            self._logger.error(
                '%s probe_timeout space=%s elapsed=%.1f enabled=%s '
                'status=%.6f',
                LOG_PREFIX, self._space_name, elapsed, enabled, status)
            return

        self._schedule()


def init(argv=None, bigworld=None, offline_mode=None, logger=None, now=None,
         poll_interval=POLL_INTERVAL_SECONDS, timeout=LOAD_TIMEOUT_SECONDS,
         get_client_version=None):
    global _probe
    if _probe is not None:
        return _probe

    argv = sys.argv if argv is None else argv
    space_name = parse_offline_request(argv)
    if space_name is None:
        return None

    try:
        if get_client_version is None:
            from helpers import getClientVersion as get_client_version
        actual_version = get_client_version().strip()
        if actual_version != EXPECTED_CLIENT_VERSION:
            (logger or _logger).error(
                '%s version_mismatch expected=%s actual=%s',
                LOG_PREFIX, EXPECTED_CLIENT_VERSION, actual_version)
            return None
        if bigworld is None:
            import BigWorld as bigworld
        if offline_mode is None:
            from helpers import OfflineMode as offline_mode
        _probe = OfflineModeProbe(
            bigworld, offline_mode, space_name, logger=logger, now=now,
            poll_interval=poll_interval, timeout=timeout)
        _probe.start()
        return _probe
    except Exception:
        (logger or _logger).exception('%s probe_bootstrap_failed', LOG_PREFIX)
        _probe = None
        return None


def record_online_lifecycle(event_name, args=(), kwargs=None):
    if _probe is None:
        return
    kwargs = {} if kwargs is None else kwargs
    _logger.error(
        '%s unexpected_online_lifecycle event=%s args=%d kwargs=%d',
        LOG_PREFIX, event_name, len(args), len(kwargs))


def fini():
    global _probe
    probe = _probe
    _probe = None
    if probe is not None:
        probe.stop()
