"""Feed copied contact velocity to #1513's existing collision presentation.

The LAN pose owner never feeds WGVehicleFilter, whose velocity is therefore
not the integrated tank velocity. Only this synchronous presentation call sees
the read-only forwarding views below. Native entities and filters stay owned
by their existing lifecycle; the callback's downstream entity operations are
forwarded to the real vehicle.
"""


class _FilterView(object):
    def __init__(self, original, velocity):
        self._original = original
        self.velocity = velocity

    def __getattr__(self, name):
        return getattr(self._original, name)


class _VehicleView(object):
    def __init__(self, entity, velocity):
        self._entity = entity
        self.filter = _FilterView(entity.filter, velocity)

    def __getattr__(self, name):
        return getattr(self._entity, name)


class CollisionFeedback(object):
    # The audited Avatar handler uses this same contact time gate.
    INTERVAL = 0.2

    def __init__(self):
        self._last = {}

    def observed(self, first, second, now):
        if not hasattr(first, 'id') or not hasattr(second, 'id'):
            return
        try:
            if (first.filter.velocity-second.filter.velocity).length <= 0.0:
                return
        except (AttributeError, TypeError):
            return
        self._last[tuple(sorted((int(first.id), int(second.id))))] = float(now)

    def present(self, avatar, callback, first, second, first_velocity,
                second_velocity, point, now, vector):
        if (not callable(callback) or not getattr(first, 'isStarted', False) or
                not getattr(second, 'isStarted', False)):
            return False
        key = tuple(sorted((int(first.id), int(second.id))))
        if float(now)-self._last.get(key, -1e30) < self.INTERVAL:
            return False
        if all(a == b for a, b in zip(first_velocity, second_velocity)):
            return False
        self._last[key] = float(now)
        callback(avatar, _VehicleView(first, vector(first_velocity)),
                 _VehicleView(second, vector(second_velocity)), vector(point), float(now))
        return True

    def clear(self):
        self._last.clear()
