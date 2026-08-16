"""Filter view that omits the two native syncs a client-only Vehicle cannot
submit.

A vehicle created by BigWorld.createEntity has no server-fed interpolation
filter behind it. syncGunAngles and syncStabilisedYPR submit their first
sample straight into that missing chain and fault. Everything else on the
filter, including the physics owner and the track updates, works and is
delegated unchanged.

This mirrors the proven 0.9.22 offline port, which suppresses the same two
calls through a scoped proxy.
"""
from __future__ import absolute_import


class OfflineFilterProxy(object):
    __slots__ = ('_filter',)

    def __init__(self, entity_filter):
        self._filter = entity_filter

    def __getattr__(self, name):
        return getattr(self._filter, name)

    def syncGunAngles(self, yaw, pitch):
        return None

    def syncStabilisedYPR(self, yaw, pitch, roll):
        return None
