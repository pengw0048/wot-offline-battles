from __future__ import print_function
"""Apply the reviewed v0.9.4 ramming/contact follow-up on one pinned source."""
from pathlib import Path

BASE = "10e08f800d093d0df1eee16344737defcde38fcd"
ROOT = Path(__file__).resolve().parents[1]

def replace_once(path, old, new):
    p = ROOT / path
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit("%s: expected one replacement, found %d" % (path, count))
    p.write_text(text.replace(old, new))

def insert_before(path, marker, block):
    p = ROOT / path
    text = p.read_text()
    if block.strip() in text:
        return
    count = text.count(marker)
    if count != 1:
        raise SystemExit("%s: marker count %d" % (path, count))
    p.write_text(text.replace(marker, block + marker))

def apply():
    tank = "src/res/scripts/client/gui/mods/offline_lan_0922/tank_collision.py"
    battle = "src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py"
    bot = "src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py"
    if ("def ram_contact_sample_heights(" in (ROOT / tank).read_text() and
            "def _native_ram_contact_plate_pair(" in (ROOT / battle).read_text() and
            "post_contact_velocity_bodies(" in (ROOT / bot).read_text()):
        print("Ramming/contact repair already applied.")
        return

    insert_before(tank, "def _contact_ram_inputs(tank, contact_armor=None):\n", r'''def ram_contact_sample_heights(hit_y, span):
    """Return stable structural-probe heights inside one real contact span.

    #1513 ramming damage is applied at a point inside the total contact area.
    A synthetic OBB overlap has no native Y coordinate, so its single chassis
    midpoint can land on track-only or empty material.  Keep the observed Y
    first, then test nearby interior heights; callers still require both real
    native hit testers to return structural armour at the same height.
    """
    try:
        hit_y = float(hit_y)
    except (TypeError, ValueError, OverflowError):
        return ()
    if not _finite(hit_y):
        return ()
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return (hit_y,)
    try:
        low, high = float(span[0]), float(span[1])
    except (TypeError, ValueError, OverflowError):
        return (hit_y,)
    if not (_finite(low) and _finite(high)) or high <= low:
        return (hit_y,)
    observed = max(low, min(high, hit_y))
    raw = [observed]
    for fraction in (0.5, 1.0 / 3.0, 2.0 / 3.0, 0.2, 0.8):
        raw.append(low + (high - low) * fraction)
    raw[1:] = sorted(raw[1:], key=lambda value: abs(value - observed))
    result = []
    for value in raw:
        if not result or all(abs(value - old) > 1.0e-4 for old in result):
            result.append(value)
    return tuple(result)


''')

    insert_before(tank, "def traverse_impulses(tanks, dt, anchor=None):\n", r'''def post_contact_velocity_bodies(tanks, results):
    """Return frozen bodies after the already-solved normal contact impulse.

    Traverse torque is a second constraint in the same physics slice.  Feeding
    it pre-contact velocities makes the same closing speed available twice and
    lets a small steering twitch add a second collision impulse.  Only velocity
    is carried forward here; geometric separation remains owned by the normal
    solver and its world-collision gate.
    """
    updated = []
    results = results or {}
    for tank in tanks or ():
        body = dict(tank)
        result = results.get(body.get('id'), {}) or {}
        delta = result.get('delta_velocity', (0.0, 0.0))
        try:
            body['vx'] = float(body.get('vx', 0.0)) + float(delta[0])
            body['vz'] = float(body.get('vz', 0.0)) + float(delta[1])
        except (TypeError, ValueError, IndexError, OverflowError):
            raise RuntimeError('invalid solved contact velocity')
        updated.append(body)
    return updated


''')

    insert_before(battle, "    def _ram_contact_armor_status(self, first, second, contact):\n", r'''    def _native_ram_contact_plate_pair(self, proof):
        """Find one shared structural damage height inside the frozen contact.

        Never substitutes primaryArmor or mixes plates sampled at different
        heights.  The first candidate for which both native #1513 hit testers
        expose structure owns the receipt.
        """
        contact_normal = proof.get('contact_normal')
        if contact_normal is None:
            return None, None, None
        hit = proof['hit_point']
        heights = tank_collision.ram_contact_sample_heights(
            hit[1], proof.get('contact_y_span'))
        seen_player = None
        seen_bot = None
        for sample_y in heights:
            sample = self._vector((hit[0], sample_y, hit[2]))
            player_plate = self._native_ram_vehicle_armor(
                proof['local_vehicle'], proof['local_matrix'], sample,
                contact_normal)
            bot_plate = self._native_ram_vehicle_armor(
                proof['bot_vehicle'], proof['bot_matrix'], sample,
                (-contact_normal[0], -contact_normal[1]))
            if player_plate is not None:
                seen_player = player_plate
            if bot_plate is not None:
                seen_bot = bot_plate
            if player_plate is not None and bot_plate is not None:
                return (player_plate, bot_plate, float(sample_y)), seen_player, seen_bot
        return None, seen_player, seen_bot

''')

    replace_once(battle,
'''    def _queue_ram_contact_proof(self, record, local_vehicle, bot_vehicle,
                                 hit_point, player_velocity, bot_velocity,
                                 contact_time_us, own_pose=None,
                                 bot_pose=None, player_ram_profile=None,
                                 contact_normal=None):
''',
'''    def _queue_ram_contact_proof(self, record, local_vehicle, bot_vehicle,
                                 hit_point, player_velocity, bot_velocity,
                                 contact_time_us, own_pose=None,
                                 bot_pose=None, player_ram_profile=None,
                                 contact_normal=None, contact_y_span=None):
''')

    replace_once(battle,
'''        bot_pose = tuple(bot_pose[:3]) + vehicle_physics.canonical_body_rotation(
            bot_pose[3], bot_pose[4], bot_pose[5])[:3]
        if player_ram_profile is None:
''',
'''        bot_pose = tuple(bot_pose[:3]) + vehicle_physics.canonical_body_rotation(
            bot_pose[3], bot_pose[4], bot_pose[5])[:3]
        if contact_y_span is None:
            try:
                player_shape = self._collision_shape(
                    local_vehicle.typeDescriptor)
                bot_shape = self._collision_shape(bot_vehicle.typeDescriptor)
                player_low, player_high = tank_collision.vertical_interval(
                    own_pose[1], player_shape, own_pose[4], own_pose[5])
                bot_low, bot_high = tank_collision.vertical_interval(
                    bot_pose[1], bot_shape, bot_pose[4], bot_pose[5])
                contact_low = max(player_low, bot_low)
                contact_high = min(player_high, bot_high)
                contact_y_span = (
                    (float(contact_low), float(contact_high))
                    if contact_high > contact_low else None)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                contact_y_span = None
        if player_ram_profile is None:
''')

    replace_once(battle,
'''            'contact_normal': contact_normal,
            'contact_spall_player': player_spall,
''',
'''            'contact_normal': contact_normal,
            'contact_y_span': contact_y_span,
            'contact_spall_player': player_spall,
''')

    replace_once(battle,
'''        contact_normal = proof.get('contact_normal')
        if contact_normal is None:
            player_plate = bot_plate = None
        else:
            player_plate = self._native_ram_vehicle_armor(
                proof['local_vehicle'], local_matrix, proof['hit_point'],
                contact_normal)
            bot_plate = self._native_ram_vehicle_armor(
                proof['bot_vehicle'], bot_matrix, proof['hit_point'],
                (-contact_normal[0], -contact_normal[1]))
        if (revision is None or presentation_time_us is None or
                player_plate is None or bot_plate is None):
''',
'''        contact_normal = proof.get('contact_normal')
        matched = None
        if contact_normal is None:
            player_plate = bot_plate = None
        else:
            matched, player_plate, bot_plate = (
                self._native_ram_contact_plate_pair(proof))
            if matched is not None:
                player_plate, bot_plate, unused_sample_y = matched
        if (revision is None or presentation_time_us is None or
                matched is None):
''')

    replace_once(battle,
'''        self._local_ram_seq += 1
        hit = proof['hit_point']
        player_armor = player_plate['armor']
''',
'''        self._local_ram_seq += 1
        sample_y = matched[2]
        hit = (proof['hit_point'][0], sample_y, proof['hit_point'][2])
        player_armor = player_plate['armor']
''')

    replace_once(battle,
'''            low = max(
                float(own['y']) + float(own['shape'][2]),
                float(other['y']) + float(other['shape'][2]))
            high = min(
                float(own['y']) + float(own['shape'][3]),
                float(other['y']) + float(other['shape'][3]))
''',
'''            # Preserve the historical presentation midpoint as the first
            # observed damage height.  Separately derive a pitched/rolled
            # shared vertical span for structural fallbacks.
            low = max(
                float(own['y']) + float(own['shape'][2]),
                float(other['y']) + float(other['shape'][2]))
            high = min(
                float(own['y']) + float(own['shape'][3]),
                float(other['y']) + float(other['shape'][3]))
            own_contact_low, own_contact_high = tank_collision.vertical_interval(
                own['y'], own['shape'],
                own.get('pitch', 0.0), own.get('roll', 0.0))
            other_contact_low, other_contact_high = tank_collision.vertical_interval(
                other['y'], other['shape'],
                other.get('pitch', 0.0), other.get('roll', 0.0))
            contact_low = max(own_contact_low, other_contact_low)
            contact_high = min(own_contact_high, other_contact_high)
''')

    replace_once(battle,
'''                player_ram_profile=own['ram_profile'],
                contact_normal=impact_contact[:2])
''',
'''                player_ram_profile=own['ram_profile'],
                contact_normal=impact_contact[:2],
                contact_y_span=(contact_low, contact_high))
''')

    replace_once(battle,
'''        contact = tank_collision.resolve_tank(
            own, physical_others, now=now,
            ram_cooldowns=self._local_ram_cooldowns,
            active_ram_contacts=self._local_ram_contacts, dt=dt)
        angular = tank_collision.traverse_impulses([own]+physical_others, dt, anchor=own['id'])
        contact['delta_velocity'] = tuple(contact['delta_velocity'][i]+angular[own['id']][i]
                                          for i in range(2))
        responses = dict(contact.get('responses', ()))
''',
'''        contact = tank_collision.resolve_tank(
            own, physical_others, now=now,
            ram_cooldowns=self._local_ram_cooldowns,
            active_ram_contacts=self._local_ram_contacts, dt=dt)
        responses = dict(contact.get('responses', ()))
        solved = {
            own['id']: {'delta_velocity': contact['delta_velocity']}}
        for other in physical_others:
            solved[other['id']] = {
                'delta_velocity': responses.get(other['id'], (0.0, 0.0))}
        traverse_bodies = tank_collision.post_contact_velocity_bodies(
            [own] + physical_others, solved)
        angular = tank_collision.traverse_impulses(
            traverse_bodies, dt, anchor=own['id'])
        contact['delta_velocity'] = tuple(
            contact['delta_velocity'][i] + angular[own['id']][i]
            for i in range(2))
''')

    replace_once(bot,
'''        physical_results = tank_collision.resolve_pairs(tanks, step)
        for actor, delta in tank_collision.traverse_impulses(tanks, step).items():
''',
'''        physical_results = tank_collision.resolve_pairs(tanks, step)
        traverse_bodies = tank_collision.post_contact_velocity_bodies(
            tanks, physical_results)
        for actor, delta in tank_collision.traverse_impulses(
                traverse_bodies, step).items():
''')

    print("Applied ramming/contact follow-up to", tank, battle, bot)

if __name__ == "__main__":
    apply()
