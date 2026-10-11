from __future__ import division

"""Canonical RAM evidence, independent of replaceable replica histories.

A RAM transaction names both samples actually used to display its target.
The server validates those identities against its own accepted pose archive
and pins the interpolated body to that transaction at admission. The worker
must not choose new interpolation neighbours from its differently coalesced
snapshot stream. No timeout, invented pose, damage override or motion occurs.
"""

import math
from collections import OrderedDict

MAX_REVISIONS = 512
MAX_BRACKET_REVISION_SPAN = 255
POSE_FIELDS = ('id', 'x', 'y', 'z', 'yaw', 'pitch', 'roll', 'aim_yaw',
               'gun_pitch', 'alive', 'team', 'vehicle')
ANGLES = ('yaw', 'aim_yaw', 'pitch', 'roll')
try:
    INTEGER_TYPES = (int, long)
except NameError:
    INTEGER_TYPES = (int,)


def normalize_bracket(value, revision, stamp):
    """Validate only identities; the server later proves their actual data."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, INTEGER_TYPES) for v in value):
        return None
    lr, lt, rr, rt = value
    if not (0 <= lr <= rr == revision <= 2147483647 and
            rr-lr <= MAX_BRACKET_REVISION_SPAN and
            0 <= lt <= stamp <= rt <= 9007199254740991):
        return None
    if (lr == rr) != (lt == rt):
        return None
    return [lr, lt, rr, rt]


def presentation_bracket(left, right):
    """Export the identities of two confirmed SnapshotSync samples."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    values = [left.get('revision'), left.get('time_us'),
              right.get('revision'), right.get('time_us')]
    if any(v is None for v in values):
        return None
    return normalize_bracket(values, values[2], values[1])


def interpolate_pose(left, right, lt, rt, stamp):
    """Exactly mirror SnapshotSync position and shortest-angle interpolation."""
    if (not isinstance(left, dict) or not isinstance(right, dict) or
            left.get('id') != right.get('id') or not lt <= stamp <= rt):
        return None
    result = dict(left)
    if lt == rt:
        return result
    progress = (stamp-lt)/float(rt-lt)
    for key in ('x', 'y', 'z', 'gun_pitch'):
        if key in left and key in right:
            result[key] = left[key] + (right[key]-left[key])*progress
    for key in ANGLES:
        if key in left and key in right:
            delta = (right[key]-left[key]+math.pi) % (2.0*math.pi) - math.pi
            result[key] = left[key] + delta*progress
    if progress >= 1.0:
        result['alive'] = bool(right.get('alive', True))
    return result


class RamPoseArchive(object):
    """A bounded source archive; admitted contacts own their separate copies.

    Admission permits a right revision at most 255 behind the current one and
    an interpolation span at most 255 revisions. 512 retained source frames
    therefore contain every in-contract pair. Advancing or evicting this
    archive after admission cannot invalidate the pinned transaction.
    """
    def __init__(self, limit=MAX_REVISIONS):
        self.limit = int(limit)
        self.samples = OrderedDict()

    def remember(self, revision, stamp, states):
        if revision in self.samples:
            # Death/HP can change without a new pose revision. Never reinterpret
            # the already published historical pose as the latest live dict.
            return False
        frozen = {}
        for bot_id, state in states.items():
            if isinstance(state, dict):
                frozen[int(bot_id)] = dict((k, state[k]) for k in POSE_FIELDS
                                          if k in state)
        self.samples[int(revision)] = (int(stamp), frozen)
        while len(self.samples) > self.limit:
            self.samples.popitem(last=False)
        return True

    def pin(self, bot_id, revision, stamp, bracket):
        pair = normalize_bracket(bracket, revision, stamp)
        if pair is None:
            return None, 'invalid_history_bracket'
        lr, lt, rr, rt = pair
        left = self.samples.get(lr)
        right = self.samples.get(rr)
        if left is None or right is None:
            return None, 'canonical_history_missing'
        if left[0] != lt or right[0] != rt:
            return None, 'canonical_history_time_mismatch'
        state = interpolate_pose(left[1].get(bot_id), right[1].get(bot_id),
                                 lt, rt, stamp)
        if state is None:
            return None, 'canonical_history_body_missing'
        return state, None
