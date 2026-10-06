# -*- coding: utf-8 -*-
"""Frozen main-thread engine capabilities for ordered native ray frontiers.

The native owner keeps row iteration, candidate sets and bounded recast work.
These callbacks retain the real Vector3 operations, live destruction evidence
and Python exception boundary. No capability is submitted to another thread.
"""
from __future__ import print_function

import functools
import operator
import threading

from . import destructibles_sensor as sensor
from .worker_diagnostics import observed_ray
from .collision_flags import VEHICLE_SKIP_FLAGS


class EngineQuery(object):
    def __init__(self, owner, backend, ray_label='native.motion.ray',
                 skip_flags=VEHICLE_SKIP_FLAGS):
        self.owner = owner
        self._avatar = owner._avatar
        self._runtime = owner._runtime
        self._space = self._avatar.spaceID
        self._generation = getattr(owner, '_generation', None)
        self._bots = getattr(owner, '_bots', None)
        self._round = getattr(self._bots, 'round_id', None)
        self._thread = threading.current_thread().ident
        self._native_ray = self._runtime.bigworld.wg_collideSegment
        self._ray_label = ray_label
        self._skip_flags = skip_flags
        self._error = None
        self.failed = object()
        self.unavailable = object()
        self._vector = self._runtime.math.Vector3
        self._xyz = operator.attrgetter('x', 'y', 'z')
        safe = lambda function: functools.partial(self._invoke, function)
        # This tuple is borrowed only by one synchronous native Session.
        # Its indices are documented by engine_query::Capability.
        self.capabilities = (
            safe(self._vector), safe(self._xyz), safe(operator.attrgetter('length')),
            safe(operator.add), safe(operator.sub), safe(self._scale),
            safe(self._normalise), self._ray, self._skin, safe(self._alias),
            safe(self._filter), safe(operator.eq), safe(self._skipped),
            backend.engine_query_filter, self.failed, self.unavailable,
            safe(self._can_recast),
            (sensor._SOFT_STATIC_MAX_SKIPS, sensor._SHOT_RAY_EPSILON),
            self._recover_ground)

    def _live(self):
        if (threading.current_thread().ident != self._thread or
                self.owner._avatar is not self._avatar or
                self.owner._runtime is not self._runtime or
                self._avatar.spaceID != self._space or
                getattr(self.owner, '_generation', None) != self._generation or
                getattr(self.owner, '_bots', None) is not self._bots or
                getattr(self._bots, 'round_id', None) != self._round):
            raise RuntimeError('Native engine query owner changed')

    def _remember(self, error):
        if self._error is None:
            self._error = error
        return self.failed

    def _invoke(self, function, *args):
        try:
            return function(*args)
        except Exception as error:
            return self._remember(error)

    def raise_failure(self):
        error = self._error
        self._error = None
        if error is not None:
            raise error

    def _recover_ground(self):
        # The maintained ground wrapper contains these failures across the
        # complete recast helper, including an error inside its filter/math.
        if isinstance(self._error, (AttributeError, IndexError, TypeError, ValueError)):
            self._error = None
            return True
        return False

    def _ray(self, start, end, collision_filter, ground):
        try:
            self._live()
            args = (self._space, start, end, self._skip_flags)
            if collision_filter is not None:
                args += (collision_filter,)
            label = 'native.motion.ground' if ground else self._ray_label
            result = observed_ray(label, self._native_ray, *args)
            self._live()
            return result
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            return self.unavailable if ground else self._remember(error)
        except Exception as error:
            return self._remember(error)

    def _skin(self, hit, start, end, surfaces, ground):
        try:
            self._live()
            result = sensor._compiled_motion_skin_1513(
                hit[0], start, end, set(surfaces), hit[1])
            self._live()
            if result is None:
                return None
            distance, excluded = result
            return distance, tuple(excluded)
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            return self.unavailable if ground else self._remember(error)
        except Exception as error:
            return self._remember(error)

    @staticmethod
    def _scale(vector, factor):
        return vector.scale(factor)

    @staticmethod
    def _normalise(vector):
        vector.normalise()
        return vector

    @staticmethod
    def _filter(collision_filter, surface):
        return bool(collision_filter(*surface))

    @staticmethod
    def _alias(surface):
        return sensor._anonymous_original_surface_1513(surface)

    @staticmethod
    def _can_recast():
        return sensor._destructible_catalog is not None

    @staticmethod
    def _skipped():
        sensor.g_offh_destr_ground_skips = getattr(
            sensor, 'g_offh_destr_ground_skips', 0) + 1
