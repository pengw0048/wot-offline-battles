#!/usr/bin/env python3
"""Bake a derived interior layout for every #1513 vehicle that has none.

``internal_layout_profiles`` holds authored interiors for 251 vehicle
identities built from only 58 distinct module-zone archetypes, and
``internal_layout_twins`` extends them to the second catalogue identity of the
same physical tank.  That still leaves most of the pinned client's 680 listed
vehicles -- the Czech, Swedish and Japanese trees, the Chinese tank
destroyers, the modern French line and the post-0.8.2 reworks -- with no
interior geometry at all, so their ammunition rack, engine, fuel tank and crew
cannot be hit.

No World of Tanks PC client of any version ships interior module geometry: the
surfaces live in the ``collision/`` server models, which are referenced by
every ``hitTester`` and shipped in no package.  This tool therefore does not
claim exact geometry.  It assigns each uncovered vehicle the archetype of the
#1513 vehicle it most resembles, chosen deterministically by discriminators
read from the exact client, and records the result as derived so no consumer
can mistake it for extracted data.

Every discriminator is exact #1513 client data:

* the crew roster -- roles per slot, from ``<crew>``;
* the fighting-compartment architecture -- ``AT-SPG``/``SPG`` tags together
  with the widest ``turretYawLimits`` span across the installable guns;
* the vehicle class and tier, from ``list.xml``;
* whether any turret is ``ceilless`` -- open-topped;
* the number of installable turrets;
* where the turret ring sits along the hull, from
  ``hull/turretPositions`` measured against the chassis footprint in
  ``topRightCarryingPoint``.

Donors are ranked lexicographically on that vector, nearest first, with the
donor's name as the final tiebreak so the bake is reproducible.  The chosen
donor's ``module_zones`` transfer unchanged: they are normalized fractions of
a parent component, and ``internal_geometry.fit_target`` re-anchors and clips
them inside this vehicle's own collision volume before use, so the container
is exact even where the placement is archetypal.

Crew zones follow the recipient's own roster, never the donor's, because
``build_layout`` validates the roster against the live descriptor.  When the
rosters agree the donor's zones transfer slot for slot.  Otherwise each slot
takes the donor zone of a slot with the same primary role, falling back
through ``ROLE_FALLBACK``; a donor zone that a previous slot already claimed
carries a reuse ordinal, and the runtime offsets that crewman laterally so no
two crewmen share one box.

    python3 tools/bake_internal_layout_derived_0922.py "$WOT_0922_CLIENT"
"""

import base64
import os
import re
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packed_xml import read_packed_xml, TYPE_COMPRESSED_STRING, TYPE_ELEMENT

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import internal_hit_layouts
from gui.mods.offline_lan_0922 import vehicle_blacklist


TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
SCRIPTS_PACKAGE = 'res/packages/scripts.pkg'
COLLISION_SEGMENT = '/collision_client/'
# internal_hit_layouts._NEAR_FULL_TURRET_YAW_SPAN in degrees: the same
# threshold the runtime uses to call a fighting compartment fixed.
NEAR_FULL_YAW_SPAN = 270.0
# Vehicle classes that are not playable tanks and get no interior.
SKIPPED_CLASSES = ('observer',)
ROLE_FALLBACK = {
    'commander': ('gunner', 'loader', 'radioman', 'driver'),
    'gunner': ('commander', 'loader', 'radioman', 'driver'),
    'loader': ('radioman', 'gunner', 'commander', 'driver'),
    'radioman': ('loader', 'gunner', 'commander', 'driver'),
    'driver': ('commander', 'gunner', 'loader', 'radioman'),
}
OUTPUT_PATH = (ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
               'offline_lan_0922' / 'internal_layout_derived.py')

HEADER = '''# -*- coding: utf-8 -*-
"""Generated derived interior layouts for #1513 vehicles with none authored.

Do not edit by hand.  Run
``tools/bake_internal_layout_derived_0922.py "$WOT_0922_CLIENT"`` to
regenerate it against the pinned client.

These layouts are DERIVED, not extracted.  No World of Tanks PC client ships
interior module geometry -- it lives in the ``collision/`` server models,
which no package contains -- so each entry carries the archetype of the #1513
vehicle this one most resembles, chosen by discriminators read from the exact
client: crew roster, fighting-compartment architecture, class, tier,
open-topped turret, turret count and where the turret ring sits along the
hull.  The donor's normalized zones are re-anchored and clipped inside this
vehicle's own collision volume at runtime, so the container is exact and only
the placement within it is archetypal.  Every record reports
``confidence = 'derived-low'``.

Each value is ``(donor profile key, vehicle class, tier, crew roster, crew
slot sources)``.  A crew slot source is ``(donor crew zone index, reuse
ordinal)``; ordinal 0 takes the donor zone as authored, and a higher ordinal
means an earlier slot already claimed it so the runtime offsets this crewman
laterally from the hull centreline instead.
"""

CLIENT_VERSION = %(version)r
CLIENT_BUILD = %(build)r
CATALOGUE_SIZE = %(catalogue_size)d
RESOLVED_BEFORE_DERIVATION = %(resolved)d
DERIVED_COUNT = %(derived)d
DERIVED_CONFIDENCE = 'derived-low'

DERIVED_LAYOUTS_0922 = {
'''

FOOTER = '''}
'''


def _client_identity(client_root):
    text = (client_root / 'version.xml').read_text(encoding='utf-8')
    match = re.search(r'v\.([^\s]+)\s+#(\d+)', text)
    if not match:
        raise SystemExit('unrecognized version.xml value')
    if match.group(1) != TARGET_VERSION or match.group(2) != TARGET_BUILD:
        raise SystemExit('client is not %s #%s' % (TARGET_VERSION, TARGET_BUILD))
    return match.group(1), match.group(2)


def _text(value):
    if value.value_type == TYPE_COMPRESSED_STRING:
        return base64.b64encode(value.value).decode('ascii')
    return value.value.decode('ascii')


def _section(node, name):
    for key, value in node.children:
        if key == name:
            return value
    return None


def _floats(value, count):
    raw = value.value
    if isinstance(raw, bytes) and len(raw) == 4 * count:
        return struct.unpack('<%df' % count, raw)
    if isinstance(raw, bytes):
        parts = raw.split()
        if len(parts) != count:
            return None
        return tuple(float(part) for part in parts)
    return None


def _turret_geometry(root, tags):
    """Architecture, open-top flag, turret count and yaw span from the client."""
    turrets = _section(root, b'turrets0')
    spans = []
    ceilless = False
    count = 0
    if turrets is not None and turrets.value_type == TYPE_ELEMENT:
        for unused_name, turret in turrets.value.children:
            if turret.value_type != TYPE_ELEMENT:
                continue
            count += 1
            if _section(turret.value, b'ceilless') is not None:
                ceilless = True
            guns = _section(turret.value, b'guns')
            if guns is None or guns.value_type != TYPE_ELEMENT:
                continue
            for unused_gun_name, gun in guns.value.children:
                if gun.value_type != TYPE_ELEMENT:
                    continue
                limits = _section(gun.value, b'turretYawLimits')
                if limits is None:
                    spans.append(NEAR_FULL_YAW_SPAN * 2.0)
                    continue
                pair = _floats(limits, 2)
                spans.append(NEAR_FULL_YAW_SPAN * 2.0 if pair is None
                             else abs(pair[1] - pair[0]))
    casemate = bool({'AT-SPG', 'SPG'} & set(tags))
    fixed = bool(casemate and spans and max(spans) < NEAR_FULL_YAW_SPAN)
    return fixed, ceilless, count


def _ring_fraction(root):
    """Where the turret ring sits along the hull, 0 at the rear, 1 at the front.

    ``turretPositions`` is hull-relative and ``topRightCarryingPoint`` gives
    the chassis half-length, so the ratio is comparable across vehicles of
    different size.  Returns None when either is absent.
    """
    hull = _section(root, b'hull')
    chassis = _section(root, b'chassis')
    if hull is None or chassis is None:
        return None
    positions = _section(hull.value, b'turretPositions')
    if positions is None or not positions.value.children:
        return None
    position = _floats(positions.value.children[0][1], 3)
    first_chassis = chassis.value.children[0][1]
    if first_chassis.value_type != TYPE_ELEMENT:
        return None
    carrying = _section(first_chassis.value, b'topRightCarryingPoint')
    if position is None or carrying is None:
        return None
    footprint = _floats(carrying, 2)
    if footprint is None or footprint[1] <= 0.0:
        return None
    fraction = 0.5 + position[2] / (2.0 * footprint[1])
    return max(0.0, min(1.0, fraction))


def _collision_directories(definition):
    """Every directory holding a client collision model this vehicle mounts."""
    directories = set()

    def walk(node):
        for key, value in node.children:
            if value.value_type == TYPE_ELEMENT:
                walk(value.value)
            elif key == b'collisionModelClient':
                path = _text(value)
                if COLLISION_SEGMENT not in path:
                    raise SystemExit('unexpected collision path: ' + path)
                directories.add(path.split(COLLISION_SEGMENT, 1)[0])
    walk(definition)
    return frozenset(directories)


def _crew_roster(definition, vehicle):
    crew = _section(definition, b'crew')
    if crew is None or crew.value_type != TYPE_ELEMENT:
        raise SystemExit('invalid crew section: ' + vehicle)
    return tuple((key.decode('ascii'),) + tuple(_text(value).split())
                 for key, value in crew.value.children)


def scan(client_root):
    vehicles = {}
    with zipfile.ZipFile(str(client_root / SCRIPTS_PACKAGE)) as archive:
        listings = sorted(name for name in archive.namelist()
                          if name.startswith('scripts/item_defs/vehicles/')
                          and name.endswith('/list.xml')
                          and name.count('/') == 4)
        for listing in listings:
            nation = listing.split('/')[-2]
            directory = listing.rsplit('/', 1)[0]
            catalog = read_packed_xml(archive.read(listing))
            for raw_name, entry in catalog.children:
                name = raw_name.decode('ascii')
                if ':' in name or entry.value_type != TYPE_ELEMENT:
                    continue
                vehicle = nation + ':' + name
                tags = _text(_section(entry.value, b'tags')).split()
                definition = read_packed_xml(
                    archive.read(directory + '/' + name + '.xml'))
                fixed, ceilless, turret_count = _turret_geometry(
                    definition, tags)
                vehicles[vehicle] = {
                    'class': tags[0] if tags else '',
                    'tier': _section(entry.value, b'level').value,
                    'crew': _crew_roster(definition, vehicle),
                    'fixed': fixed,
                    'ceilless': ceilless,
                    'turrets': turret_count,
                    'ring': _ring_fraction(definition),
                    'dirs': _collision_directories(definition),
                }
    return vehicles


def _resolved_profile(vehicle):
    """The layout the port already resolves for this vehicle, or None.

    Reads the authored table, the alias table and the twin table, and
    deliberately not the derived table this tool generates, so a re-bake with
    the output installed produces the same result.
    """
    key = internal_hit_layouts._profile_key(vehicle)
    if key is None:
        return None, None
    resolved_key, profile = internal_hit_layouts._profile_for_key(key)
    if profile is None:
        donor = internal_hit_layouts.twin_donor_key(vehicle)
        if donor is not None:
            resolved_key, profile = internal_hit_layouts._profile_for_key(donor)
    if profile is None:
        return None, None
    return resolved_key, internal_hit_layouts._profile_record(profile)


def _score(candidate, target):
    """Lexicographic distance from a donor to the vehicle being derived.

    Class and fighting-compartment architecture come first because they decide
    where the engine, ammunition and fuel compartments are at all: an SPG's
    interior is not a heavy tank's whatever the crew roster says.  Roster
    equality ranks below them because a differing roster only means the crew
    zones are remapped by role instead of transferring slot for slot.
    """
    ring_gap = 1.0
    if candidate['ring'] is not None and target['ring'] is not None:
        ring_gap = abs(candidate['ring'] - target['ring'])
    return (
        0 if candidate['class'] == target['class'] else 1,
        0 if candidate['fixed'] == target['fixed'] else 1,
        # Quantized to a tenth of the hull so that which end of the vehicle
        # carries the fighting compartment outranks the remaining
        # discriminators -- a front-compartment SPG must not inherit a
        # rear-compartment SPG's interior -- while leaving them to decide
        # between donors that agree to within a tenth.
        int(ring_gap / 0.1),
        0 if candidate['ceilless'] == target['ceilless'] else 1,
        0 if candidate['turrets'] == target['turrets'] else 1,
        0 if candidate['crew'] == target['crew'] else 1,
        ring_gap,
        abs(int(candidate['tier']) - int(target['tier'])),
    )


def _crew_slot_sources(donor_roster, roster):
    """Map each of this vehicle's crew slots onto a donor crew zone.

    Returns ``(donor crew zone index, reuse ordinal)`` per slot.  A reuse
    ordinal of 0 takes the donor zone as authored; a higher ordinal means an
    earlier slot already claimed that zone, and the runtime lays the crewman
    out laterally from it so no two crewmen share one box.  A recipient with
    more crew slots than the donor is why reuse happens at all.
    """
    if donor_roster == roster:
        return tuple((index, 0) for index in range(len(roster)))
    by_role = {}
    for index, slot in enumerate(donor_roster):
        by_role.setdefault(slot[0], []).append(index)
    claimed = set()
    reuse = {}
    sources = []
    for slot in roster:
        role = slot[0]
        order = (role,) + ROLE_FALLBACK.get(role, ())
        chosen = None
        for candidate_role in order:
            for index in by_role.get(candidate_role, ()):
                if index not in claimed:
                    chosen = index
                    break
            if chosen is not None:
                break
        if chosen is not None:
            claimed.add(chosen)
            sources.append((chosen, 0))
            continue
        # Every donor zone for this role and its fallbacks is taken.  Reuse
        # the nearest one by role and let the runtime offset it.
        for candidate_role in order:
            if by_role.get(candidate_role):
                chosen = by_role[candidate_role][0]
                break
        if chosen is None:
            chosen = 0
        reuse[chosen] = reuse.get(chosen, 0) + 1
        sources.append((chosen, reuse[chosen]))
    return tuple(sources)


def derive(vehicles):
    donors = {}
    derived = {}
    skipped = {}
    for vehicle, info in vehicles.items():
        resolved_key, record = _resolved_profile(vehicle)
        if record is not None and record['crew_roles'] == info['crew']:
            donors[vehicle] = (resolved_key, record, info)
    # One physical tank can appear under several catalogue identities that all
    # need deriving.  Choose the donor once per tank -- the collision-model
    # directories plus the crew roster -- so an _IGR or _bot entry never ends
    # up with a different interior from the tank it is a copy of.
    groups = {}
    for vehicle in sorted(vehicles):
        if vehicle in donors:
            continue
        info = vehicles[vehicle]
        if info['class'] in SKIPPED_CLASSES:
            skipped[vehicle] = 'class=' + info['class']
            continue
        if vehicle in vehicle_blacklist.UNUSABLE_VEHICLES:
            skipped[vehicle] = 'unusable resources'
            continue
        groups.setdefault((info['dirs'], info['crew']), []).append(vehicle)

    for members in groups.values():
        representative = vehicles[members[0]]
        best = min(sorted(donors),
                   key=lambda name: _score(donors[name][2], representative)
                   + (name,))
        donor_key, donor_record, unused_info = donors[best]
        sources = _crew_slot_sources(donor_record['crew_roles'],
                                     representative['crew'])
        score = _score(donors[best][2], representative)
        for vehicle in members:
            info = vehicles[vehicle]
            derived[vehicle] = (
                donor_key, info['class'], int(info['tier']), info['crew'],
                sources, best, score)
    return donors, derived, skipped


def verify(derived, donors_by_key):
    for vehicle in sorted(derived):
        donor_key, unused_class, unused_tier, roster, sources = \
            derived[vehicle][:5]
        record = donors_by_key.get(donor_key)
        if record is None:
            raise SystemExit('donor key resolves to no layout: %s -> %r'
                             % (vehicle, donor_key))
        if len(sources) != len(roster):
            raise SystemExit('crew slot count differs: ' + vehicle)
        placements = set()
        for index, ordinal in sources:
            if not 0 <= index < len(record['crew_zones']):
                raise SystemExit('crew zone index out of range: ' + vehicle)
            if (index, ordinal) in placements:
                raise SystemExit('two crew slots share one zone: ' + vehicle)
            placements.add((index, ordinal))
        if _resolved_profile(vehicle)[1] is not None:
            raise SystemExit('vehicle already has a layout: ' + vehicle)


def render(version, build, catalogue_size, resolved, derived):
    text = HEADER % {'version': version, 'build': build,
                     'catalogue_size': catalogue_size, 'resolved': resolved,
                     'derived': len(derived)}
    for vehicle in sorted(derived):
        donor_key, vehicle_class, tier, roster, sources = derived[vehicle][:5]
        text += '    %r: (\n' % (internal_hit_layouts._profile_key(vehicle),)
        text += '        %r,\n' % (donor_key,)
        text += '        %r,\n' % vehicle_class
        text += '        %d,\n' % tier
        text += '        %r,\n' % (roster,)
        text += '        %r,\n' % (sources,)
        text += '    ),\n'
    return text + FOOTER


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit('usage: bake_internal_layout_derived_0922.py '
                         '<client root> [output path]')
    client_root = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else OUTPUT_PATH
    version, build = _client_identity(client_root)
    vehicles = scan(client_root)
    donors, derived, skipped = derive(vehicles)
    donors_by_key = {}
    for unused_vehicle, (key, record, unused_info) in donors.items():
        donors_by_key[key] = record
    verify(derived, donors_by_key)
    output.write_text(
        render(version, build, len(vehicles), len(donors), derived),
        encoding='utf-8')
    print('catalogue: %d vehicles' % len(vehicles))
    print('already resolved: %d' % len(donors))
    print('derived: %d' % len(derived))
    print('skipped: %d' % len(skipped))
    for vehicle in sorted(skipped):
        print('  %s (%s)' % (vehicle, skipped[vehicle]))
    scores = [value[6] for value in derived.values()]
    print('donor shares the vehicle class: %d'
          % sum(1 for score in scores if score[0] == 0))
    print('  ... and the fighting-compartment architecture: %d'
          % sum(1 for score in scores if score[:2] == (0, 0)))
    print('  ... and the turret ring to within a tenth of the hull: %d'
          % sum(1 for score in scores if score[:3] == (0, 0, 0)))
    print('  ... and the open-topped flag and turret count: %d'
          % sum(1 for score in scores if score[:5] == (0, 0, 0, 0, 0)))
    print('  ... and the exact crew roster: %d'
          % sum(1 for score in scores if score[:6] == (0, 0, 0, 0, 0, 0)))
    print('donor shares the exact crew roster at all: %d'
          % sum(1 for score in scores if score[5] == 0))
    print('turret-ring offset from the donor: median %.3f, worst %.3f'
          % (sorted(score[6] for score in scores)[len(scores) // 2],
             max(score[6] for score in scores)))
    print('coverage after derivation: %d of %d'
          % (len(donors) + len(derived), len(vehicles)))
    print('written: %s' % output)


if __name__ == '__main__':
    main()
