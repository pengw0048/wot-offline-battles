"""Fly every live shell against the live poses, both directions.

The copied projectile_manager owns trajectory time and distance. This
adapter owns the collision probes: each chord is tested against the
terrain and against the vehicles as they stand NOW, so a moving hull is
hit where it is, or dodged.
"""
from __future__ import absolute_import

from gui.mods.offline_battle_2312 import damage
from gui.mods.offline_battle_2312 import projectile_manager
from gui.mods.offline_battle_2312 import projectiles


class FlightDeck(object):
    """One shared in-flight manager, ticked every frame."""

    def __init__(self, space_id, log):
        self._space_id = space_id
        self._log = log
        self._manager = None
        self._meta = {}
        self._hits = {}
        self._stopped = False
        self._callback_id = None

    def start(self):
        import BigWorld
        self._manager = projectile_manager.InFlightProjectiles(
            initial_time=BigWorld.time())
        self._tick()

    def stop(self):
        import BigWorld
        self._stopped = True
        if self._callback_id is not None:
            try:
                BigWorld.cancelCallback(self._callback_id)
            except Exception:
                pass
            self._callback_id = None

    def launch(self, key, start, velocity, gravity, max_distance, targets,
               on_terminal):
        """targets() lists the vehicles this shell may hit;
        on_terminal(hit, reason) takes the Impact and the manager reason."""
        import BigWorld
        if self._manager is None:
            return False
        gravity = abs(float(gravity))
        max_time = projectiles.flight_seconds(velocity, gravity,
                                              max_distance)
        accepted = self._manager.launch(
            key, tuple(start), tuple(velocity), (0.0, -gravity, 0.0),
            BigWorld.time(), max(0.01, max_time), float(max_distance))
        if accepted:
            self._meta[key] = (targets, on_terminal)
        else:
            self._log('flight_rejected key=%s' % (key,))
        return bool(accepted)

    def _tick(self):
        import BigWorld
        if self._stopped:
            return
        self._callback_id = BigWorld.callback(0.0, self._tick)
        # Advance even while empty: launch() rejects a shell whose launch
        # time is ahead of the manager clock, so the clock must track now.
        self._manager.advance(BigWorld.time(), self._chord, self._terminal)

    def _chord(self, state, start, end, absolute_start, absolute_end):
        import BigWorld
        import Math
        key = state['key']
        meta = self._meta.get(key)
        if meta is None:
            return {'reason': 'orphan', 'fraction': 0.0}
        head = Math.Vector3(*start)
        tail = Math.Vector3(*end)
        chord = (tail - head).length
        if chord <= 0.0001:
            return None
        terrain = BigWorld.wg_collideSegment(self._space_id, head, tail,
                                             projectiles.COLLISION_MASK)
        terrain_fraction = None
        if terrain is not None:
            terrain_fraction = (terrain.closestPoint - head).length / chord
        targets = meta[0]() if meta[0] is not None else ()
        target = damage.nearest_vehicle(targets, head, tail)
        if target is not None:
            vehicle, reach, collisions = target
            fraction = reach / chord
            if fraction <= 1.0 and (terrain_fraction is None or
                                    fraction <= terrain_fraction):
                point = Math.Vector3(
                    head.x + (tail.x - head.x) * fraction,
                    head.y + (tail.y - head.y) * fraction,
                    head.z + (tail.z - head.z) * fraction)
                self._hits[key] = projectiles.Impact(
                    point, absolute_end - state['launch_time'],
                    state['distance'] + chord * fraction,
                    vehicle=vehicle, collisions=collisions,
                    segment_start=head, segment_end=tail)
                return {'reason': 'vehicle', 'fraction': fraction}
        if terrain_fraction is not None and terrain_fraction <= 1.0:
            self._hits[key] = projectiles.Impact(
                Math.Vector3(terrain.closestPoint),
                absolute_end - state['launch_time'],
                state['distance'] + chord * terrain_fraction,
                mat_kind=getattr(terrain, 'matKind', 0),
                segment_start=head, segment_end=tail)
            return {'reason': 'terrain', 'fraction': terrain_fraction}
        return None

    def _terminal(self, state, result):
        import Math
        key = state['key']
        meta = self._meta.pop(key, None)
        hit = self._hits.pop(key, None)
        if meta is None:
            return
        if hit is None:
            hit = projectiles.Impact(
                Math.Vector3(*state['position']), state['elapsed'],
                state['distance'])
        reason = str(result.get('reason') or '')
        if reason not in ('vehicle', 'terrain', 'max_time', 'max_distance'):
            self._log('flight_odd_terminal key=%s reason=%s' % (key, reason))
        try:
            meta[1](hit, reason)
        except Exception as err:
            self._log('flight_terminal_error key=%s err=%r' % (key, err))
