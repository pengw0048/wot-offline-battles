# -*- coding: utf-8 -*-
"""Ballistic law for the #1513 ammo-bay turret detachment presentation.

Exact #1513 already owns every piece of the *presentation*.  ``Vehicle``
encodes turret detachment in the vehicle's own health value: the client's
``constants.SPECIAL_VEHICLE_HEALTH.TURRET_DETACHED`` (-13) makes
``Vehicle.isTurretMarkedForDetachment`` true, and once
``confirmTurretDetachment`` has run ``CompoundAppearance`` reassembles the
wreck without its turret while ``vehicle_damage_state`` selects the
``exploded`` model chain.  The flying half is the stock ``DetachedTurret``
client entity, which assembles ``turret.models.exploded`` plus
``gun.models.exploded`` and plays the shipped ``turret_flying_*`` /
``turret_touchdown_*`` effect chains from the turret's
``turretDetachmentEffects`` descriptor.

What #1513 does *not* ship is the flight itself.  In retail the arc is cell
(server) physics: ``DetachedTurret``'s ``velocity``, ``angularVelocity`` and
``applyForceToCOM`` are ``CELL_PRIVATE``/``CELL_PUBLIC`` and the client only
interpolates the replicated pose through a native ``BigWorld.WGTurretFilter``
that Python cannot feed.  ``Vehicle.showAmmoBayEffect`` even receives a
``projectedTurretSpeed`` argument and discards it.

This module therefore owns the one thing that has to be invented: a
deterministic ballistic arc.  It is pure arithmetic with no BigWorld
dependency so both runtimes and the test suite can evaluate it, and it is
deterministic in the detachment seed so every client in the room draws the
same arc without a new wire field.

The launch impulse below is a **product number**, not a retail constant.  Two
shipped #1513 values bound it, and the chosen speed sits inside them:

* ``DetachedTurret._TurretDetachmentEffects._MAX_COLLISION_ENERGY`` is
  ``98.1`` in specific-energy units (``0.5 * v ** 2``), i.e. exactly the
  energy of a 10 m free fall under ``9.81`` m/s^2.  That is the loudest drop
  the shipped touchdown effect is calibrated for.
* ``DetachedTurret._MIN_COLLISION_SPEED`` is ``3.5`` m/s, below which stock
  plays no ground impact at all.

``LAUNCH_VERTICAL_SPEED`` is picked so the landing sits inside that window
for a real tank rather than saturating it.  It apexes ``v ** 2 / (2 * g)`` =
5.6 m above the turret ring, and a turret thrown off a ring 2.2 m up onto
level ground lands with a specific energy of 69 to 79 -- 0.7 to 0.8 of the
shipped range once the drift is counted.  A turret thrown off a cliff still
saturates the ceiling, exactly as stock's own clamp intends.  Retail
calibration of the real impulse still needs a replay measurement.
"""

import math


GRAVITY = 9.81

# Product numbers.  See the module docstring for the shipped #1513 envelope
# they are chosen inside.
LAUNCH_VERTICAL_SPEED = 10.5
LAUNCH_DRIFT_MIN_SPEED = 1.5
LAUNCH_DRIFT_MAX_SPEED = 4.5
LAUNCH_SPIN_MIN_RATE = 1.1
LAUNCH_SPIN_MAX_RATE = 2.6

# The arc is walked with a world segment query per step.  0.04 s keeps each
# step under 0.5 m at the launch speed, so a wall or a roof edge cannot be
# stepped over, and the whole search stays near sixty queries.  A native
# segment query costs about 7 us in this client, so one detachment spends
# well under half a millisecond on the frame that resolves it.
FLIGHT_STEP_SECONDS = 0.04
MAX_FLIGHT_SECONDS = 8.0

# ``wg_collideSegment`` reports a point, not a surface normal, so the arc
# classifies a contact by its own motion instead: a turret that is still
# rising, or descending slower than this, has met a wall face rather than the
# ground.  Landing there would park it inside a building facade and report a
# ground material for a vertical surface.  Such a contact keeps the fall and
# drops the sideways motion -- the containment a missing normal allows, not an
# invented restitution coefficient -- and the search resumes from the last
# free point so a coplanar restart cannot stall it.
WALL_DESCENT_LIMIT = 0.5
MAX_WALL_DEFLECTIONS = 3

# A landed turret keeps whichever half turn it was tumbling through, so it
# rests either upright or inverted the way a retail one does.
_HALF_TURN = math.pi


def _finite(value):
    number = float(value)
    if number != number or number in (float('inf'), float('-inf')):
        raise ValueError('expected a finite number')
    return number


def _vector3(value):
    return (_finite(value[0]), _finite(value[1]), _finite(value[2]))


class _Draws(object):
    """Small explicit LCG so both runtimes draw the identical sequence.

    ``random`` is deliberately not used here: its seeding and its float
    generation are not contracted to be identical between the client's
    CPython 2.7.7 and the Python 3 test interpreter, and this sequence has to
    reproduce exactly in both.
    """

    __slots__ = ('_state',)

    def __init__(self, seed):
        self._state = (int(seed) & 0x7FFFFFFF) or 1

    def unit(self):
        # Numerical Recipes' ranqd1 multiplier/increment, taken modulo 2**31
        # so the whole sequence stays inside a CPython 2.7 small int.
        self._state = (self._state * 1664525 + 1013904223) & 0x7FFFFFFF
        return self._state / 2147483648.0

    def between(self, low, high):
        return low + (high - low) * self.unit()


def launch_impulse(seed):
    """Return the frozen launch velocity and tumble rates for one detachment.

    ``seed`` must be the same integer on every client in the room; the
    runtime derives it from the round id and the engine id with
    ``ai.planner.stable_seed``, both of which are already replicated.  The
    arc therefore needs no wire field of its own and still looks the same on
    every screen.

    The vertical component is the whole throw; the horizontal drift only
    decides where the turret comes down.  The impulse is world-vertical
    rather than hull-vertical: the cell-side rule that would tilt it with the
    hull is not in the client package, and inventing one would put a
    fabricated coefficient in front of a visible result.
    """
    draws = _Draws(seed)
    heading = draws.between(-math.pi, math.pi)
    drift = draws.between(LAUNCH_DRIFT_MIN_SPEED, LAUNCH_DRIFT_MAX_SPEED)
    velocity = (
        drift * math.sin(heading),
        LAUNCH_VERTICAL_SPEED,
        drift * math.cos(heading))
    spin = (
        draws.between(-LAUNCH_SPIN_MAX_RATE, LAUNCH_SPIN_MAX_RATE),
        draws.between(LAUNCH_SPIN_MIN_RATE, LAUNCH_SPIN_MAX_RATE) *
        (1.0 if draws.unit() < 0.5 else -1.0),
        draws.between(LAUNCH_SPIN_MIN_RATE, LAUNCH_SPIN_MAX_RATE) *
        (1.0 if draws.unit() < 0.5 else -1.0))
    return {'velocity': velocity, 'spin': spin}


def flight_position(origin, velocity, elapsed):
    """Return the free-fall position of the turret's own origin."""
    origin = _vector3(origin)
    velocity = _vector3(velocity)
    elapsed = max(0.0, _finite(elapsed))
    return (
        origin[0] + velocity[0] * elapsed,
        origin[1] + velocity[1] * elapsed - 0.5 * GRAVITY * elapsed * elapsed,
        origin[2] + velocity[2] * elapsed)


def flight_velocity(velocity, elapsed):
    velocity = _vector3(velocity)
    elapsed = max(0.0, _finite(elapsed))
    return (velocity[0], velocity[1] - GRAVITY * elapsed, velocity[2])


def impact_energy(velocity):
    """Return the specific energy #1513's touchdown effect is scaled by.

    ``_TurretDetachmentEffects.__normalizeEnergy`` clamps this into
    ``[0.5 * 3.5 ** 2, 98.1]`` and lerps the ``RTPC_ext_drop_energy`` sound
    parameter across it, so the value handed to ``onStaticCollision`` has to
    be ``0.5 * speed ** 2`` and nothing else.
    """
    velocity = _vector3(velocity)
    return 0.5 * (velocity[0] ** 2 + velocity[1] ** 2 + velocity[2] ** 2)


def _segment(origin, velocity, start, duration):
    return {'origin': origin, 'velocity': velocity,
            'start': start, 'duration': duration}


def resolve_flight(origin, velocity, collide, clearance=0.0,
                   step=FLIGHT_STEP_SECONDS, limit=MAX_FLIGHT_SECONDS,
                   deflections=MAX_WALL_DEFLECTIONS):
    """Walk the arc until the ground stops it and freeze the whole result.

    ``collide(start, end)`` is the caller's world segment query; it returns
    the contact point or ``None``.  ``clearance`` lifts the queried segment to
    the turret's underside so a resting turret sits on the surface instead of
    sinking to its own origin.  A wall contact splits the arc into another
    free-fall segment rather than ending it; see ``WALL_DESCENT_LIMIT``.

    The complete outcome is frozen once, here, exactly like an accepted fire
    intent: a presentation that is advanced per frame from a frozen arc cannot
    drift, cannot be re-decided by a later frame, and always has a terminal
    pose even if the client stalls through the whole flight.
    """
    origin = _vector3(origin)
    velocity = _vector3(velocity)
    clearance = max(0.0, _finite(clearance))
    step = _finite(step)
    if step <= 0.0:
        raise ValueError('flight step must be positive')
    limit = max(step, _finite(limit))
    deflections_left = max(0, int(deflections))
    segments = []
    part_origin = origin
    part_velocity = velocity
    part_time = 0.0
    elapsed_before = 0.0
    current = origin
    while elapsed_before + part_time < limit:
        following = min(limit - elapsed_before, part_time + step)
        nxt = flight_position(part_origin, part_velocity, following)
        contact = collide(
            (current[0], current[1] - clearance, current[2]),
            (nxt[0], nxt[1] - clearance, nxt[2]))
        if contact is None:
            current = nxt
            part_time = following
            continue
        point = _vector3(contact)
        travelled = _segment_fraction(current, nxt, point)
        hit_time = part_time + (following - part_time) * travelled
        impact = flight_velocity(part_velocity, hit_time)
        if impact[1] > -WALL_DESCENT_LIMIT and deflections_left > 0:
            deflections_left -= 1
            segments.append(_segment(
                part_origin, part_velocity, elapsed_before, part_time))
            elapsed_before += part_time
            part_origin = current
            part_velocity = (
                0.0, flight_velocity(part_velocity, part_time)[1], 0.0)
            part_time = 0.0
            continue
        segments.append(_segment(
            part_origin, part_velocity, elapsed_before, hit_time))
        return {
            'origin': origin,
            'velocity': velocity,
            'segments': segments,
            'duration': elapsed_before + hit_time,
            'contact': point,
            'rest': (point[0], point[1] + clearance, point[2]),
            'impact_velocity': impact,
            'energy': impact_energy(impact),
            'landed': True,
        }
    # No ground was found inside the search window.  Publish the terminal
    # state anyway: the turret stops where the search ran out rather than
    # falling for ever or leaving the presentation without an end.
    segments.append(_segment(
        part_origin, part_velocity, elapsed_before, part_time))
    return {
        'origin': origin,
        'velocity': velocity,
        'segments': segments,
        'duration': elapsed_before + part_time,
        'contact': None,
        'rest': flight_position(part_origin, part_velocity, part_time),
        'impact_velocity': flight_velocity(part_velocity, part_time),
        'energy': impact_energy(flight_velocity(part_velocity, part_time)),
        'landed': False,
    }


def _segment_fraction(start, end, point):
    """Return where along ``start``..``end`` the contact point lies."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    length_sq = dx * dx + dy * dy + dz * dz
    if length_sq <= 0.0:
        return 0.0
    fraction = ((point[0] - start[0]) * dx +
                (point[1] - start[1]) * dy +
                (point[2] - start[2]) * dz) / length_sq
    return max(0.0, min(1.0, fraction))


def rest_attitude(launch_attitude, spin, duration):
    """Return the settled yaw/pitch/roll of a turret that has stopped.

    The tumble is kept but rounded to the nearest half turn in pitch and
    roll, which is how a real turret ends up: flat on its roof or flat on its
    ring, never frozen on a corner.
    """
    yaw, pitch, roll = (_finite(value) for value in launch_attitude)
    spin = _vector3(spin)
    duration = max(0.0, _finite(duration))
    return (
        yaw + spin[0] * duration,
        _snap_half_turn(pitch + spin[1] * duration),
        _snap_half_turn(roll + spin[2] * duration))


def _snap_half_turn(angle):
    turns = int(round(angle / _HALF_TURN))
    return turns * _HALF_TURN


def pose_at(flight, launch_attitude, spin, elapsed):
    """Return the pose to write this frame, flight or rest.

    ``elapsed`` past the frozen duration always yields the identical rest
    pose, so a late frame, a hitch, or a repeated call cannot move a turret
    that has already stopped.
    """
    elapsed = max(0.0, _finite(elapsed))
    duration = float(flight['duration'])
    if elapsed >= duration:
        return (
            tuple(flight['rest']),
            rest_attitude(launch_attitude, spin, duration))
    yaw, pitch, roll = (_finite(value) for value in launch_attitude)
    spin = _vector3(spin)
    segment = flight['segments'][0]
    for candidate in flight['segments']:
        if elapsed >= candidate['start']:
            segment = candidate
    return (
        flight_position(segment['origin'], segment['velocity'],
                        elapsed - segment['start']),
        (yaw + spin[0] * elapsed,
         pitch + spin[1] * elapsed,
         roll + spin[2] * elapsed))
