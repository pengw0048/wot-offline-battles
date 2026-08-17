"""Show and clear the crashed track models the cell normally commands."""
from __future__ import absolute_import

TRACKS = (('leftTrackHealth', True), ('rightTrackHealth', False))


def refresh(vehicle):
    appearance = getattr(vehicle, 'appearance', None)
    if appearance is None:
        return
    destroyed = getattr(vehicle, '_destroyed_devices', None) or ()
    shown = getattr(vehicle, '_offh_crashed_tracks', None)
    if shown is None:
        shown = set()
        vehicle._offh_crashed_tracks = shown
    for name, is_left in TRACKS:
        broken = name in destroyed
        if broken and name not in shown:
            try:
                appearance.addCrashedTrack(is_left, 0, 0)
            except Exception:
                continue
            shown.add(name)
        elif not broken and name in shown:
            try:
                appearance.delCrashedTrack(is_left, 0)
            except Exception:
                continue
            shown.discard(name)
