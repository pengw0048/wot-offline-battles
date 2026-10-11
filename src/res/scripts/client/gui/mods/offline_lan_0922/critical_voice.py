"""A per-call voice priority, independent of penetration flags and HP damage."""
from __future__ import print_function
import sys

CRITICAL_EVENT = 'enemy_no_hp_damage_at_no_attempt_by_player'
# Only the direct-projectile result chosen by stock showShotResults is remapped.
# Kill and ignition sounds, ally messages, HE groups and HUD flags stay native.
_DIRECT_RESULTS = frozenset((
    'enemy_hp_damaged_by_projectile_by_player',
    'enemy_hp_damaged_by_projectile_and_gun_damaged_by_player',
    'enemy_hp_damaged_by_projectile_and_chassis_damaged_by_player',
    'enemy_no_hp_damage_at_attempt_by_player',
    'enemy_no_hp_damage_at_attempt_and_gun_damaged_by_player',
    'enemy_no_hp_damage_at_attempt_and_chassis_damaged_by_player',
    'enemy_no_hp_damage_at_no_attempt_by_player',
    'enemy_no_hp_damage_at_no_attempt_and_gun_damaged_by_player',
    'enemy_no_hp_damage_at_no_attempt_and_chassis_damaged_by_player',
    'enemy_no_piercing_by_player', 'enemy_ricochet_by_player'))


class _PriorityVoice(object):
    def __init__(self, original):
        self.original = original
        self.remapped = False

    def __getattr__(self, name):
        return getattr(self.original, name)

    def play(self, name, *args, **kwargs):
        if name in _DIRECT_RESULTS:
            name = CRITICAL_EVENT
            self.remapped = True
        return self.original.play(name, *args, **kwargs)


def present(avatar, callback, results):
    """Do not falsify armour penetration to request the critical-hit voice."""
    original = avatar.soundNotifications
    proxy = _PriorityVoice(original)
    avatar.soundNotifications = proxy
    try:
        return callback(results)
    finally:
        if avatar.soundNotifications is proxy:
            avatar.soundNotifications = original
        if proxy.remapped:
            count = getattr(avatar, '_offlineCriticalVoiceCount', 0) + 1
            avatar._offlineCriticalVoiceCount = count
            if count <= 3 or count % 50 == 0:
                sys.stdout.write('[Offline LAN 0.9.22] FEEDBACK critical_voice=%s count=%s flags_preserved=True\n' %
                                 (CRITICAL_EVENT, count))
