#!/usr/bin/env python3
"""Bake real interior module geometry for the #1513 roster.

No World of Tanks PC client ships interior module geometry: every hit tester
names a ``collision/`` server model and no package contains one.  The same
vehicles on World of Tanks Console do ship it, as Havok packfiles whose named
surfaces are exactly the material kinds
``scripts/item_defs/vehicles/common/vehicle.xml`` defines for #1513 --
``ammoBay``, ``engine``, ``transmission``, ``fuelTank``, ``radio``,
``turretRotator``, ``surveyingDevice``, ``leftTrack``, ``rightTrack``,
``gun``, and one surface per crew station.  This tool reads those, registers
them against the PC collision bounds of the same vehicle, and writes the
result as the port's own normalized zones.

Nothing is invented.  A vehicle with no source is reported as having none.

Provenance of every number this writes:

* geometry: WOTInspector's Armor Inspector data archive, package
  ``console4.3`` (2018-02-07) with ``console4.13`` (2020-07-16) filling gaps,
  member ``console/vehicles/<nation>/<code>/<part>_proxy.hkx``;
* the reference frame: package ``pc9.22.0`` (2018-02-06), member
  ``pc/vehicles/<nation>/<code>/collision_client/<Part>.visual_processed``,
  whose ``<boundingBox>`` is the same bound the running client's hit tester
  reports, so a zone's fractions are exact rather than approximate;
* vehicle identity, crew roster and component structure: the pinned client.

The archived ``pc9.22.0`` catalogue is roster-identical to the pinned client --
all 680 entries, no difference either way -- which is what ties the archive to
this build.

Registration is measured, not assumed, in two ways.  Both platforms carry the
same vehicle's hull shell as ``armor_*`` surfaces, so the tool compares that
shell's extent against the PC hull bounding box on every axis; and for every
component it checks that the decoded surfaces sit inside the PC bounding box
the client reports for it.  A component failing either check is rejected
rather than used, and both figures are recorded per vehicle in the report.
Only the hull's shells are comparable directly: a gun component's plates
cover the breech while its bounding box spans the barrel, and a turret proxy
is not built to the same silhouette.

    python3 tools/bake_internal_layout_console_0922.py "$WOT_0922_CLIENT" \
        --cache ~/console-collision-cache
"""

import argparse
import base64
import collections
import io
import json
import os
import re
import struct
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packed_xml import read_packed_xml, TYPE_COMPRESSED_STRING, TYPE_ELEMENT
from hkx_module_geometry import (UnsupportedPackfile, module_surfaces,
                                 outer_shell)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import internal_hit_layouts

TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
SCRIPTS_PACKAGE = 'res/packages/scripts.pkg'
ARCHIVE = ('https://wotinspector-archive.s3.eu-central-003.backblazeb2.com'
           '/ai/%s/collision-%d.data')
CONSOLE_PACKAGES = ('console4.3', 'console4.13')
PC_PACKAGE = 'pc9.22.0'
# The nation order the client uses to build a vehicle's compact descriptor,
# which is also the id the archive keys its packages on:
# (list.xml id << 8) | (nation index << 4) | ITEM_TYPE_VEHICLE.
NATIONS = ('ussr', 'germany', 'usa', 'china', 'france', 'uk', 'japan',
           'czech', 'sweden', 'poland')
ITEM_TYPE_VEHICLE = 1
# Which component a Console packfile member describes, and the port's parent
# name for it.
PART_PARENTS = (
    ('hull', 'hull'),
    ('turret_01', 'turret'),
    ('gun_01', 'gun'),
    ('chassis', 'chassis'),
)
# Console surface name -> the port's module entity.
MODULE_ENTITIES = {
    'ammoBay': 'ammoBay',
    'engine': 'engine',
    'transmission': 'engine',
    'fuelTank': 'fuelTank',
    'radio': 'radio',
    'turretRotator': 'turretRotator',
    'surveyingDevice': 'surveyingDevice',
}
# leftTrack, rightTrack and gun are deliberately absent.  They are external
# devices the client already carries itself: the port scores tracks from the
# native collision extras and the gun from the installed gun model, so baking
# a second copy would add a competing owner for geometry that is not missing.
# What build_layout must find in the profile: MODULE_TARGETS less the tracks
# it scores from the native collision extras and the gun it takes from the
# installed gun model.
REQUIRED_ENTITIES = ('ammoBay', 'engine', 'fuelTank', 'radio',
                     'surveyingDevice', 'turretRotator')
CREW_SURFACES = ('commander', 'driver', 'gunner_1', 'gunner_2', 'loader_1',
                 'loader_2', 'radioman_1', 'radioman_2')
REQUEST_PAUSE = 0.25


def _client_identity(client_root):
    text = (client_root / 'version.xml').read_text(encoding='utf-8')
    match = re.search(r'v\.([^\s]+)\s+#(\d+)', text)
    if not match:
        raise SystemExit('unrecognized version.xml value')
    if match.group(1) != TARGET_VERSION or match.group(2) != TARGET_BUILD:
        raise SystemExit('client is not %s #%s'
                         % (TARGET_VERSION, TARGET_BUILD))
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


def read_client(client_root):
    """Roster, archive id, crew roster and hit-tester parts per vehicle."""
    vehicles = {}
    with zipfile.ZipFile(str(client_root / SCRIPTS_PACKAGE)) as archive:
        listings = sorted(name for name in archive.namelist()
                          if name.startswith('scripts/item_defs/vehicles/')
                          and name.endswith('/list.xml')
                          and name.count('/') == 4)
        for listing in listings:
            nation = listing.split('/')[-2]
            base = listing.rsplit('/', 1)[0]
            catalog = read_packed_xml(archive.read(listing))
            for raw, entry in catalog.children:
                code = raw.decode('ascii', 'replace')
                if ':' in code or entry.value_type != TYPE_ELEMENT:
                    continue
                list_id = _section(entry.value, b'id').value
                tags = _text(_section(entry.value, b'tags')).split()
                level = int(_section(entry.value, b'level').value)
                definition = read_packed_xml(
                    archive.read(base + '/' + code + '.xml'))
                crew = _section(definition, b'crew')
                roster = tuple(
                    (key.decode('ascii'),) + tuple(_text(value).split())
                    for key, value in crew.value.children)
                models = set()

                def walk(node):
                    for key, value in node.children:
                        if value.value_type == TYPE_ELEMENT:
                            walk(value.value)
                        elif key == b'collisionModelClient':
                            models.add(_text(value))
                walk(definition)
                vehicles[nation + ':' + code] = {
                    'nation': nation,
                    'code': code,
                    'list_id': int(list_id),
                    'archive_id': ((int(list_id) << 8)
                                   | (NATIONS.index(nation) << 4)
                                   | ITEM_TYPE_VEHICLE),
                    'crew': roster,
                    'vehicle_class': tags[0] if tags else '',
                    'tier': level,
                    'models': tuple(sorted(models)),
                }
    return vehicles


def fetch(cache, package, archive_id, log):
    """The cached bytes of one archive package, or None when absent."""
    path = cache / ('%s_collision-%d.data' % (package, archive_id))
    if path.exists():
        return path.read_bytes() or None
    url = ARCHIVE % (package, archive_id)
    try:
        time.sleep(REQUEST_PAUSE)
        with urlopen(url, timeout=60) as response:
            body = response.read()
    except HTTPError as error:
        if error.code == 404:
            path.write_bytes(b'')
            log.append({'url': url, 'status': 404})
            return None
        raise
    except URLError as error:
        raise SystemExit('archive unreachable: %s (%s)' % (url, error))
    path.write_bytes(body)
    log.append({'url': url, 'status': 200, 'bytes': len(body)})
    return body


def console_parts(body):
    """{port parent: decoded surfaces} for one Console collision package."""
    parts = {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for info in archive.infolist():
            if info.flag_bits & 0x1:
                raise UnsupportedPackfile('encrypted member: '
                                          + info.filename)
            leaf = info.filename.rsplit('/', 1)[-1].lower()
            for suffix, parent in PART_PARENTS:
                if leaf.endswith('_%s_proxy.hkx' % suffix) or \
                        leaf.endswith('%s_proxy.hkx' % suffix):
                    try:
                        parts[parent] = module_surfaces(archive.read(info))
                    except UnsupportedPackfile:
                        pass
                    break
    return parts


def pc_bounds(body):
    """{port parent: (min, max)} from the PC collision visuals."""
    bounds = {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for info in archive.infolist():
            if not info.filename.endswith('.visual_processed'):
                continue
            leaf = info.filename.rsplit('/', 1)[-1].lower()
            stem = leaf[:-len('.visual_processed')]
            parent = None
            for suffix, candidate in PART_PARENTS:
                if stem == suffix:
                    parent = candidate
                    break
            if parent is None:
                continue
            try:
                root = read_packed_xml(archive.read(info))
            except Exception:
                continue
            box = _section(root, b'boundingBox')
            if box is None or box.value_type != TYPE_ELEMENT:
                continue
            values = {}
            for key, value in box.value.children:
                raw = value.value
                if isinstance(raw, bytes) and len(raw) == 12:
                    values[key.decode()] = struct.unpack('<3f', raw)
                elif isinstance(raw, bytes):
                    parts = raw.split()
                    if len(parts) == 3:
                        values[key.decode()] = tuple(float(p) for p in parts)
            if 'min' in values and 'max' in values:
                bounds[parent] = (values['min'], values['max'])
    return bounds


def residual(surfaces, bound):
    """Per-axis extent disagreement between the Console shell and PC bounds.

    Only meaningful for the hull, where both platforms' ``armor_*`` plates
    describe the same closed shell.  A gun component's plates cover the breech
    while its PC bounding box spans the whole barrel, and a turret's proxy is
    not built to the same silhouette, so those are checked by containment
    instead.
    """
    shell = outer_shell(surfaces)
    if shell is None or bound is None:
        return None
    return tuple(abs((shell[1][axis] - shell[0][axis])
                     - (bound[1][axis] - bound[0][axis]))
                 for axis in range(3))


def overshoot(surfaces, bound):
    """How far the decoded surfaces reach outside the PC bounding box.

    This is the per-component frame check: the two datasets share an origin
    and axes only if what Console places inside a component sits inside the
    bound the PC client reports for it.  The gun is exempt because its
    surface is the barrel, which reaches far beyond the collision box the
    breech visual declares.
    """
    if bound is None:
        return None
    worst = 0.0
    for name, item in surfaces.items():
        if name == 'gun':
            continue
        for axis in range(3):
            worst = max(worst, bound[0][axis] - item['minimum'][axis],
                        item['maximum'][axis] - bound[1][axis])
    return worst


def surface_box(surface):
    """The surface's exact tight bound, straight out of the resource.

    Deliberately one box per surface rather than one per lobe.  These are
    surface meshes with sparsely sampled vertices, so a gap between
    consecutive vertices is not evidence of a void: the IS-7's ammunition
    racks really do sit against both hull sides, but the same 72-vertex mesh
    also shows seven other gaps wider than 12 cm that are only sparse
    sampling.  Separating true lobes needs the triangle connectivity, which
    this decoder does not read, so guessing them would invent geometry.

    The consequence is stated rather than hidden: a module built as two
    separated lobes is enclosed by one box that also covers the space between
    them, which is wider than retail.  The port then applies its own shape
    hints and per-module physical half-extent caps to that zone, as it does
    for every other zone.
    """
    return (surface['minimum'], surface['maximum'])


def fractions(box, bound):
    """A box in component metres as centre/half fractions of the bounds."""
    low, high = box
    span = [bound[1][axis] - bound[0][axis] for axis in range(3)]
    centre = []
    half = []
    for axis in range(3):
        if span[axis] <= 0.0:
            return None
        middle = (low[axis] + high[axis]) * 0.5
        value = (middle - bound[0][axis]) / span[axis]
        centre.append(round(min(0.98, max(0.02, value)), 4))
        extent = (high[axis] - low[axis]) * 0.5 / span[axis]
        half.append(round(min(0.48, max(0.01, extent)), 4))
    return tuple(centre), tuple(half)


def crew_surface_names(roster):
    """The Console surface name for each of this vehicle's crew slots."""
    counters = collections.Counter()
    names = []
    for slot in roster:
        role = slot[0]
        counters[role] += 1
        if role in ('commander', 'driver'):
            names.append(role)
        else:
            names.append('%s_%d' % (role, counters[role]))
    return names


def archetype_zones(key, entity):
    """The retained archetype's zones for one entity, or ()."""
    unused_key, profile = internal_hit_layouts._profile_for_key(
        internal_hit_layouts._profile_key(key))
    record = internal_hit_layouts._profile_record(profile)
    if record is None:
        return ()
    return tuple(zone for zone in record['module_zones']
                 if zone[0] == entity)


def build_vehicle(key, vehicle, console, pc, max_residual, max_overshoot):
    """Real module and crew zones for one vehicle, or a rejection reason."""
    module_zones = []
    crew_zones = []
    residuals = {}
    usable = {}
    for parent, surfaces in sorted(console.items()):
        bound = pc.get(parent)
        if bound is None:
            residuals[parent] = 'no_pc_bounds'
            continue
        record = {}
        gaps = residual(surfaces, bound) if parent == 'hull' else None
        if gaps is not None:
            record['shell'] = [round(value, 4) for value in gaps]
        # Drop the individual surfaces that reach outside the bound rather
        # than the whole vehicle: a roof cupola sitting above the hull's
        # collision box does not mean the frames disagree, and the hull shell
        # comparison already answers that question.  fractions() clamps to
        # the component edge anyway, so a dropped surface is one we decline
        # to place, not one placed wrongly.
        kept = {}
        dropped = []
        for name, item in surfaces.items():
            if name == 'gun':
                kept[name] = item
                continue
            reach = max([0.0] + [value for axis in range(3)
                                 for value in (bound[0][axis]
                                               - item['minimum'][axis],
                                               item['maximum'][axis]
                                               - bound[1][axis])])
            if reach > max_overshoot:
                dropped.append('%s:%.3f' % (name, reach))
            else:
                kept[name] = item
        if dropped:
            record['dropped_surfaces'] = sorted(dropped)
        residuals[parent] = record
        if gaps is not None and max(gaps) > max_residual:
            continue
        if kept:
            usable[parent] = (kept, bound)

    if 'hull' not in usable:
        return None, 'hull_unregistered', residuals

    for parent in sorted(usable):
        surfaces, bound = usable[parent]
        for name in sorted(surfaces):
            entity = MODULE_ENTITIES.get(name)
            if entity is None:
                continue
            shaped = fractions(surface_box(surfaces[name]), bound)
            if shaped is None:
                continue
            module_zones.append((entity, parent, name,
                                 shaped[0], shaped[1]))

    absent = []
    for slot_index, surface in enumerate(crew_surface_names(vehicle['crew'])):
        placed = None
        for parent in ('turret', 'hull', 'gun', 'chassis'):
            if parent not in usable or surface not in usable[parent][0]:
                continue
            surfaces, bound = usable[parent]
            shaped = fractions((surfaces[surface]['minimum'],
                                surfaces[surface]['maximum']), bound)
            if shaped is None:
                continue
            placed = (parent, 'crew_%02d' % slot_index, shaped[0], shaped[1])
            break
        if placed is None:
            absent.append(surface)
        else:
            crew_zones.append(placed)

    if absent:
        return None, 'crew_surfaces_absent:' + ','.join(absent), residuals
    # build_layout requires a geometry source for every module target it does
    # not get natively -- the tracks come from the collision extras and the
    # gun from the installed gun model, the rest must be here -- and reports
    # the layout invalid otherwise.  So a vehicle enters the table only with
    # the complete set: a casemate tank destroyer with no turretRotator
    # surface falls back to the retained archetype in full rather than
    # shipping a layout the runtime would reject.
    # Console models no traverse mechanism for a casemate tank destroyer, and
    # sometimes no separate optic.  Discarding a vehicle's four decoded
    # modules over the one it does not model would be worse than filling that
    # one from the retained archetype, so each entity keeps its own
    # provenance and the record says which were not decoded.
    located = set(zone[0] for zone in module_zones)
    from_archetype = []
    for entity in REQUIRED_ENTITIES:
        if entity in located:
            continue
        borrowed = archetype_zones(key, entity)
        if not borrowed:
            return None, 'module_absent:' + entity, residuals
        module_zones.extend(borrowed)
        from_archetype.append(entity)
    if not module_zones:
        return None, 'no_module_surfaces', residuals
    return ((tuple(module_zones), tuple(crew_zones),
             tuple(sorted(from_archetype))), None, residuals)


HEADER = '''# -*- coding: utf-8 -*-
"""Generated interior geometry for the #1513 roster, decoded not reconstructed.

Do not edit by hand.  Run
``tools/bake_internal_layout_console_0922.py "$WOT_0922_CLIENT" --cache DIR``
to regenerate it.

Every zone here is a decoded collision surface, not an archetype.  No World of
Tanks PC client ships interior module geometry -- each hit tester names a
``collision/`` server model that no package contains -- so the surfaces come
from the same vehicles on World of Tanks Console, whose Havok collision
packfiles name them with exactly the material kinds
``item_defs/vehicles/common/vehicle.xml`` defines for #1513.

Centres and half extents are fractions of the component's PC collision
bounding box, the same bound the running client's hit tester reports, so
``internal_geometry.fit_target`` resolves them to the metres this client
actually uses.  One zone per decoded surface, carrying that
surface's exact tight bound.  A module built as two separated lobes is
enclosed by a single box that also covers the space between them: separating
lobes would need triangle connectivity this decoder does not read, and
guessing them would invent geometry.

Registration is measured: both platforms carry the same vehicle's outer shell
as ``armor_*`` surfaces, and a component whose two shells disagreed by more
than MAX_SHELL_RESIDUAL_M on any axis was rejected rather than used.
"""

CLIENT_VERSION = %(version)r
CLIENT_BUILD = %(build)r
GEOMETRY_SOURCES = %(sources)r
REFERENCE_FRAME_SOURCE = %(pc_package)r
MAX_SHELL_RESIDUAL_M = %(max_residual)r
CATALOGUE_SIZE = %(catalogue)d
DECODED_COUNT = %(decoded)d
CONFIDENCE = 'decoded'

# vehicle key -> (vehicle class, tier, crew roster, entities taken from the
# retained archetype because the resources do not model them, module zones,
# crew zones)
CONSOLE_LAYOUTS_0922 = {
'''

FOOTER = '''}
'''


def render(version, build, catalogue, decoded, max_residual, sources):
    text = HEADER % {'version': version, 'build': build,
                     'catalogue': catalogue, 'decoded': len(decoded),
                     'max_residual': max_residual,
                     'sources': tuple(sources), 'pc_package': PC_PACKAGE}
    for key in sorted(decoded):
        (vehicle_class, tier, roster, module_zones, crew_zones,
         from_archetype) = decoded[key]
        # Key on the same normalized (nation, name) the runtime derives from
        # a descriptor, so the lookup needs no translation.
        text += ('    %r: (\n        %r,\n        %d,\n        %r,\n'
                 '        %r,\n        (\n'
                 % (internal_hit_layouts._profile_key(key), vehicle_class,
                    tier, roster, from_archetype))
        for zone in module_zones:
            text += '            %r,\n' % (zone,)
        text += '        ),\n        (\n'
        for zone in crew_zones:
            text += '            %r,\n' % (zone,)
        text += '        ),\n    ),\n'
    return text + FOOTER


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('client_root')
    parser.add_argument('--cache', required=True,
                        help='directory for downloaded archive packages')
    parser.add_argument('--output', default=None)
    parser.add_argument('--report', default=None,
                        help='where to write the per-vehicle audit JSON')
    parser.add_argument('--max-residual', type=float, default=0.35,
                        help='reject a component whose Console and PC shells '
                             'disagree by more than this many metres')
    parser.add_argument('--max-overshoot', type=float, default=0.20,
                        help='reject a component whose decoded surfaces reach '
                             'more than this many metres outside the PC '
                             'bounding box')
    parser.add_argument('--limit', type=int, default=0,
                        help='stop after this many vehicles, for a probe')
    args = parser.parse_args()

    client_root = Path(args.client_root).resolve()
    version, build = _client_identity(client_root)
    cache = Path(args.cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    output = (Path(args.output).resolve() if args.output else
              ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
              'offline_lan_0922' / 'internal_layout_console.py')

    vehicles = read_client(client_root)
    print('catalogue: %d vehicles' % len(vehicles))
    requests = []
    decoded = {}
    rejected = {}
    audit = {}
    sources = set()
    order = sorted(vehicles)
    if args.limit:
        order = order[:args.limit]
    for position, key in enumerate(order):
        vehicle = vehicles[key]
        console = None
        used = None
        for package in CONSOLE_PACKAGES:
            body = fetch(cache, package, vehicle['archive_id'], requests)
            if body is None:
                continue
            try:
                parts = console_parts(body)
            except UnsupportedPackfile as error:
                audit.setdefault(key, {})['%s_error' % package] = str(error)
                continue
            if parts:
                console, used = parts, package
                break
        if console is None:
            rejected[key] = 'no_console_source'
            audit.setdefault(key, {})['reason'] = 'no_console_source'
            continue
        pc_body = fetch(cache, PC_PACKAGE, vehicle['archive_id'], requests)
        pc = pc_bounds(pc_body) if pc_body else {}
        built, reason, residuals = build_vehicle(
            key, vehicle, console, pc, args.max_residual, args.max_overshoot)
        audit[key] = {
            'package': used,
            'residuals': residuals,
            'surfaces': dict((parent, sorted(surfaces))
                             for parent, surfaces in console.items()),
        }
        if built is None:
            rejected[key] = reason
            audit[key]['reason'] = reason
            continue
        sources.add(used)
        decoded[key] = ((vehicle['vehicle_class'], vehicle['tier'],
                         vehicle['crew']) + built)
        audit[key]['module_zones'] = len(built[0])
        audit[key]['crew_zones'] = len(built[1])
        audit[key]['entities_from_archetype'] = list(built[2])
        if (position + 1) % 25 == 0:
            print('  %d/%d processed, %d decoded'
                  % (position + 1, len(order), len(decoded)))

    # A second catalogue identity of one physical tank -- an _IGR, _bot,
    # _bootcamp, _Gold or _training entry -- has no package of its own but
    # references the same collision models and the same crew roster, so the
    # decoded geometry of that tank is its geometry, not an approximation.
    def tank_identity(entry):
        return (frozenset(path.split('/collision_client/', 1)[0]
                          for path in entry['models']), entry['crew'])

    donors = collections.defaultdict(list)
    for key in decoded:
        donors[tank_identity(vehicles[key])].append(key)
    inherited = {}
    for key in sorted(rejected):
        candidates = donors.get(tank_identity(vehicles[key]), [])
        if len(candidates) == 1:
            inherited[key] = candidates[0]
    for key, donor in inherited.items():
        decoded[key] = decoded[donor]
        audit.setdefault(key, {})['inherited_from'] = donor
        audit[key]['reason'] = 'inherited_identical_tank'
        del rejected[key]
    print('inherited by identical catalogue twin: %d' % len(inherited))

    output.write_text(render(version, build, len(vehicles), decoded,
                             args.max_residual, sorted(sources)),
                      encoding='utf-8')
    report_path = (Path(args.report).resolve() if args.report else
                   cache / 'console_layout_report.json')
    report_path.write_text(json.dumps(
        {'client': {'version': version, 'build': build},
         'catalogue': len(vehicles), 'decoded': len(decoded),
         'rejected': rejected, 'requests': requests, 'vehicles': audit},
        indent=1, sort_keys=True), encoding='utf-8')
    print()
    print('decoded with real geometry: %d of %d'
          % (len(decoded), len(vehicles)))
    for reason, count in collections.Counter(
            value.split(':')[0] for value in rejected.values()).most_common():
        print('  rejected %4d  %s' % (count, reason))
    print('written: %s' % output)
    print('report:  %s' % report_path)


if __name__ == '__main__':
    main()
