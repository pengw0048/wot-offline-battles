"""Bounded, observation-owned geometry for advisory Bot tactics."""

import math

from gui.mods.offline_lan_0922 import shot_geometry


class ProbeBudgetExhausted(Exception):
    pass


class RayBudget(object):
    """Never turn an untested ray into evidence of cover or exposure."""

    def __init__(self, probe, maximum=24):
        self.probe = probe
        self.remaining = maximum

    def clear(self, start, end):
        return bool(self.call(self.probe, start, end))

    def call(self, probe, *args):
        if self.remaining <= 0:
            raise ProbeBudgetExhausted()
        self.remaining -= 1
        return probe(*args)


def body_samples(descriptor):
    """Keep hull/turret centres and both hull sides, in descriptor frames."""
    samples = shot_geometry.vehicle_aim_samples(descriptor)
    centres = [value for value in samples[:2]]
    sides = [value for value in samples[2:] if value[0] == 'hull'][:2]
    return tuple(centres + sides)


def exposure(samples, pose, threats, clear):
    """Return the worst observed direction's sampled visible body fraction.

    This is a geometric score, not a probability of being hit. The caller
    supplies observed enemy gun origins; no live hidden entity pose is read.
    """
    if not samples or not threats:
        return None
    points = [shot_geometry.vehicle_aim_point(sample, pose)
              for sample in samples]
    return max(sum(1.0 for point in points if clear(origin, point)) /
               len(points) for origin in threats)


def search_offsets(width, length, phase):
    """Rotate a local search around the whole vehicle, including its flanks."""
    radius = max(width, length) * 2.0
    offsets = [(0.0, 0.0)]
    for scale in (1.0, 2.0):
        for index in range(8):
            angle = index * math.pi / 4.0
            offsets.append((math.cos(angle) * radius * scale,
                            math.sin(angle) * radius * scale))
    start = int(phase) % len(offsets)
    return tuple(offsets[start:] + offsets[:start])


def relevant_threats(source_position, focus, observed, maximum=2):
    """Prioritize the focus and a distinct nearby observed firing direction."""
    focus_key = (focus.get('kind'), focus.get('network_id', focus.get('id')))
    result = [focus]
    fx, unused_y, fz = focus['position']
    main = math.atan2(fx - source_position[0], fz - source_position[2])
    ranked = []
    for key, value in observed:
        if key == focus_key:
            continue
        x, unused_y, z = value['position']
        distance = math.hypot(x - source_position[0], z - source_position[2])
        bearing = math.atan2(x - source_position[0], z - source_position[2])
        separation = abs((bearing - main + math.pi) % (2.0 * math.pi) - math.pi)
        ranked.append((-(1.0 + separation) / max(1.0, distance), key, value))
    ranked.sort(key=lambda value: (value[0], value[1]))
    result.extend(value[2] for value in ranked[:max(0, maximum - 1)])
    return result
