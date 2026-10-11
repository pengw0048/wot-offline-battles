# -*- coding: utf-8 -*-
"""Run complete navigation proofs on the current engine-owning thread."""
import operator
import sys

from .worker_diagnostics import current, observed
from .collision_flags import VEHICLE_SKIP_FLAGS

try:
    _LONG_TYPE = long
except NameError:
    _LONG_TYPE = int


_reported_active = False
_reported_failure = False


def _report_failure(error):
    global _reported_failure
    if not _reported_failure:
        _reported_failure = True
        try:
            sys.stdout.write('[Offline LAN 0.9.22] native navigation oracle '
                             'unavailable for an operation: %s\n' % error)
        except Exception:
            pass


class Oracle(object):
    @classmethod
    def create(cls, owner):
        try:
            result = cls(owner)
            return result if result._capabilities is not None else None
        except Exception as error:
            _report_failure(error)
            return None

    def __init__(self, owner, backend=None):
        if backend is None:
            from . import native_math
            backend = native_math._load()
        self.backend = backend
        self.owner = owner
        self._avatar = owner._avatar
        self._bots = getattr(owner, '_bots', None)
        self.calls = 0
        self.failures = 0
        self.rays = 0
        self.waters = 0
        self._capabilities = None
        if (backend is None or not hasattr(backend, 'nav_query_run') or
                not hasattr(backend, 'nav_query_filter')):
            _report_failure('native nav_query_run/nav_query_filter are missing')
            return
        from . import destructibles_sensor
        water = getattr(owner._runtime.bigworld, 'wg_collideWater', None)
        vector = owner._runtime.math.Vector3
        if callable(water):
            # Existing water failures mean unknown/no water (-1), not an
            # exception that rejects otherwise supported navigation ground.
            def safe_water(start, end, flag):
                try:
                    return water(vector(*start), vector(*end), flag)
                except Exception:
                    return None
        else:
            safe_water = None
        self._capabilities = (
            owner._runtime.bigworld.wg_collideSegment, safe_water,
            owner._runtime.math.Vector3, operator.attrgetter('y'),
            backend.nav_query_filter, owner._avatar.spaceID,
            VEHICLE_SKIP_FLAGS, False,
            destructibles_sensor.prepare_navigation_collision_filter(None, None),
            _LONG_TYPE)
        self._xyz = operator.attrgetter('x', 'y', 'z')

    @observed('nav.query_stage')
    def run(self, start, end, cell_size, max_grade_up, max_grade_down):
        global _reported_active
        if self._capabilities is None:
            return None
        if (self.owner._avatar is not self._avatar or
                getattr(self.owner, '_bots', None) is not self._bots or
                self.owner._avatar.spaceID != self._capabilities[5]):
            return (-1, 0, 0)
        from .bot_runtime import BOT_WATER_AVOID_DEPTH
        snapshot = tuple(start) + tuple(end) + (
            cell_size, max_grade_up, max_grade_down, BOT_WATER_AVOID_DEPTH)
        diagnostic = current()
        holder = [None]
        hooks = ((diagnostic.start, diagnostic.stop, diagnostic.geometry,
                  self._xyz, holder) if diagnostic is not None else None)
        try:
            result = self.backend.nav_query_run(snapshot, self._capabilities, hooks)
        except Exception as error:
            # The raw engine callback has already been attempted. Never replay
            # it through the Python oracle, and unwind its diagnostic child.
            if diagnostic is not None and holder[0] is not None:
                diagnostic.stop('native.navigation.ray', holder[0])
                holder[0] = None
            self.failures += 1
            _report_failure(error)
            return (-1, -1, -1)
        if result is None:
            _report_failure('unsupported navigation input before engine dispatch')
            return None  # unsupported BEFORE the first engine call
        if not _reported_active:
            _reported_active = True
            try:
                sys.stdout.write('[Offline LAN 0.9.22] native computation '
                                 'active: nav_query_run\n')
            except Exception:
                pass
        self.calls += 1
        self.rays += max(0, result[1])
        self.waters += max(0, result[2])
        return result

    def snapshot(self):
        return dict(calls=self.calls, failures=self.failures, rays=self.rays,
                    waters=self.waters, available=self._capabilities is not None)
