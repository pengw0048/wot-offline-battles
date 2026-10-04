"""Read-only, forward-time replacement of the LAN transport for saved battles.

A replay feeds the original presentation decoder after its native load barrier.
No connection, simulation worker, input request, damage authority or settlement
is created. The dedicated launcher entry starts only the visible client.
"""
from __future__ import print_function
import time
from collections import deque

from gui.mods.offline_lan_0922.lan_client import LANClient
from gui.mods.offline_lan_0922 import offline_replay, replay_reader, replay_resources


class ReplayClient(LANClient):
    is_offline_replay = True

    def __init__(self, *args, **kwargs):
        path = kwargs.pop('replay_path', None) or offline_replay.replay_request()
        self._replay_clock = kwargs.pop('replay_clock', None)
        reader_factory = kwargs.pop('replay_reader_factory', replay_reader.ProcessReader)
        LANClient.__init__(self, *args, **kwargs)
        self.reader = reader_factory(path)
        root = __import__('os').getcwdu() if hasattr(__import__('os'), 'getcwdu') else __import__('os').getcwd()
        try:
            verified = replay_resources.verify(self.reader.header.get('overlay_signature'), root)
        except Exception:
            self.reader.close()
            raise
        offline_replay.log('play_profile bound=%s legacy_requires_original_profile=%s' % (verified, not verified))
        self.replay_config = dict(self.reader.header['config'])
        self._replay_callback = None
        self._replay_ready = False
        self._replay_started = None
        self._replay_next = None
        self._replay_initial = False
        self._replay_live_seeded = False
        self._validate_initial_lineup()
        self._replay_booted = False
        self._replay_open = True
        self._replay_local_last = None
        self._replay_rows = 0
        self._replay_queue = deque()
        self._replay_local_future = None
        self._replay_present_limit = 0.0
        self._replay_eof_read = False
        self._replay_reader_logged = False

    def _validate_initial_lineup(self):
        # A reservation-only battle_start cannot create Bot entities. The
        # canonical snapshot is part of loading, NOT part of advancing time.
        h = self.reader.header
        start, snapshot = h['start'], h['snapshot']
        if snapshot.get('round_id') != start.get('round_id'):
            self.reader.close()
            raise ValueError('replay initial snapshot belongs to another round')
        for name in ('players', 'bots'):
            expected = set(row.get('id') for row in start.get(name, ())
                           if isinstance(row, dict))
            actual = set(row.get('id') for row in snapshot.get(name, ())
                         if isinstance(row, dict))
            if expected != actual or None in expected:
                self.reader.close()
                raise ValueError('replay initial %s lineup is incomplete' % name)

    def _clock(self):
        if self._replay_clock is not None:
            return float(self._replay_clock())
        if self.bigworld is not None and hasattr(self.bigworld, 'time'):
            return float(self.bigworld.time())
        return time.time()

    def _schedule_replay(self):
        if self.running and self.bigworld is not None:
            self._replay_callback = self.bigworld.callback(0.01, self.pump_replay)

    def start(self):
        if self.running or not self._replay_open:
            return False
        if self.bigworld is None:
            try:
                import BigWorld
                self.bigworld = BigWorld
            except ImportError:
                pass
        self.running = self.connected = True
        self.phase = 'connecting'
        # A delayed start avoids re-entering LANSession before it owns us.
        self._schedule_replay()
        offline_replay.log('play_open map=%s records_streamed=True' %
                           self.reader.header['start'].get('map'))
        return True

    def _feed(self, message):
        # Header seeds remain immutable; later rows are owned by this consumer
        # and are delivered once. Do not deepcopy a complete dynamic snapshot.
        header_seed = any(message is self.reader.header.get(k) for k in ('welcome','start','snapshot','live'))
        value = offline_replay.clean_message(message) if header_seed else message
        value.pop('_client_received_time', None)
        value.pop('_client_dispatch_delay', None)
        if value.get('type') == 'battle_receipt':
            raise ValueError('reward receipts may not be replayed')
        old_drops = self._snapshot_drop_streak
        LANClient._handle_message(self, value)
        if value.get('type') == 'snapshot' and self._snapshot_drop_streak > old_drops:
            raise ValueError('recorded snapshot rejected: %s' % self._snapshot_drop_reason)
        if not self.running:
            raise ValueError(self.last_error or 'recorded message failed its schema')

    def send_battle_ready(self, bases=None):
        if not self.running or self._replay_ready:
            return self._replay_ready
        if not self._replay_initial:
            return False
        self._replay_ready = True
        self._replay_started = self._clock()
        offline_replay.log('play_ready round=%s' % self.round_id)
        return True

    def presentation_time(self):
        if self._replay_started is None:
            return 0.0
        return min(max(0.0, self._clock()-self._replay_started), self._replay_present_limit)

    def _prefetch(self, elapsed):
        # Only read enough for a future local endpoint. Future discrete events
        # stay queued, so lookahead cannot fire shots/deaths/visibility early.
        if self._replay_eof_read:
            return
        if any(row['type']=='local' and row['t']>elapsed for row in self._replay_queue):
            return
        for unused in range(128-len(self._replay_queue)):
            try:
                row = self.reader.next()
            except replay_reader.Pending:
                break
            if row is None:
                raise ValueError('replay ended without terminal record')
            self._replay_queue.append(row)
            if row['type']=='end':
                self._replay_eof_read=True
                break
            if row['type']=='local' and row['t']>elapsed:
                break

    def pump_replay(self):
        # Observability only: do not change eligibility, sample order, playback
        # clock, frame budget, or replay scheduling without a captured stall.
        from gui.mods.offline_lan_0922 import runtime_diagnostics
        return runtime_diagnostics.observe_replay_pump(self, self._pump_replay)

    def _pump_replay(self):
        self._replay_callback = None
        if not self.running:
            return
        try:
            if not self._replay_booted:
                self._replay_booted = True
                self._feed(self.reader.header['welcome'])
                self._feed(self.reader.header['start'])
                snapshot = self.reader.header['snapshot']
                self._replay_initial = True
                self._feed(snapshot)
                offline_replay.log('play_initial_lineup players=%s bots=%s round=%s timeline_paused=True' % (
                    len(snapshot.get('players') or ()),len(snapshot.get('bots') or ()),self.round_id))
            if not self._replay_ready:
                self._prefetch(0.0)
                self._schedule_replay()
                return
            if not self._replay_live_seeded:
                self._replay_live_seeded=True
                live=self.reader.header.get('live')
                if isinstance(live,dict): self._feed(live)
            elapsed=max(0.0,self._clock()-self._replay_started)
            self._prefetch(elapsed)
            # Deliver ordered original records. Main-thread decode has already
            # happened in the reader process; native application remains here.
            for unused in range(64):
                if not self._replay_queue:
                    self._prefetch(elapsed)
                if not self._replay_queue or self._replay_queue[0]['t']>elapsed:
                    break
                row=self._replay_queue.popleft()
                self._replay_present_limit=row['t']
                if row['type']=='end':
                    self._end_replay(None)
                    return
                if row['type']=='wire':
                    self._feed(row['data'])
                elif row['type']=='local':
                    data=offline_replay.validate_local(dict(row['data'],_replay_t=row['t']))
                    self._replay_local_last=data
                    self._notify('replay_local',data)
                self._replay_rows+=1
            self._prefetch(elapsed)
            self._replay_local_future=None
            for row in self._replay_queue:
                if row['type']=='local':
                    self._replay_local_future=offline_replay.validate_local(dict(row['data'],_replay_t=row['t']))
                    break
            if self._replay_queue:
                self._replay_present_limit=self._replay_queue[0]['t']
            if not self._replay_reader_logged and getattr(self.reader,'pid',None):
                self._replay_reader_logged=True
                offline_replay.log('play_reader pid=%s isolated=True interpolation=True' % self.reader.pid)
        except Exception as error:
            self._end_replay(str(error))
            return
        self._schedule_replay()

    def _end_replay(self, error):
        offline_replay.log('play_end rows=%s error=%s' % (self._replay_rows, error or '-'))
        self.stop()
        self._notify('replay_error' if error else 'replay_finished',
                     {'error': error, 'round_id': self.round_id})

    def stop(self):
        self.running = self.connected = self.ready = False
        self.phase = 'disconnected'
        if self._replay_callback is not None and self.bigworld is not None:
            try:
                self.bigworld.cancelCallback(self._replay_callback)
            except Exception:
                pass
        self._replay_callback = None
        if self._replay_open:
            self._replay_open = False
            self.reader.close()

    def _ignore_runtime_payload(self, kind, reason, message):
        # The file contains only messages the live decoder had accepted. A
        # replay decode failure is corrupt/incompatible data, not packet loss
        # that can be ignored while claiming faithful playback.
        raise ValueError('recorded %s rejected: %s' % (kind, reason))

    def _send(self, message, **unused_options):
        # Cosmetic callers can finish without a transport failure. There is
        # intentionally no socket, outbound thread or persistent account owner.
        return bool(self.running)

    def send_fire_intent(self, *args, **kwargs):
        return None

    def send_equipment_intent(self, *args, **kwargs):
        return None

    def send_descriptor_catalog(self, *args, **kwargs):
        return False

    def start_battle(self, *args, **kwargs):
        return False

    def leave_battle(self, voluntary=True):
        # Match the shared session contract without issuing a network request.
        # Runtime failures are handled by the replay-specific teardown path.
        return True

    def is_bot_authority(self):
        return False
