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

* geometry: WOTInspector's Armor Inspector data archive.  Each vehicle takes
  the first package, in order of release distance from this client, that
  yields a complete layout registering against the PC bounds -- so
  ``console4.3`` (2018-02-07, six days after this build) wherever it exists,
  and a later or earlier package only where it does not.  The package used is
  recorded per vehicle;
* the reference frame: package ``pc9.22.0`` (2018-02-06), member
  ``pc/vehicles/<nation>/<code>/collision_client/<Part>.visual_processed``,
  whose ``<boundingBox>`` is the same bound the running client's hit tester
  reports, so a zone's fractions are exact rather than approximate;
* vehicle identity, crew roster and component structure: the pinned client.

The archived ``pc9.22.0`` catalogue is roster-identical to the pinned client --
all 680 entries, no difference either way -- which is what ties the archive to
this build.

Hull registration is measured; every other component is checked for frame
containment.  Both platforms carry the same vehicle's hull shell as ``armor_*``
surfaces, so the tool compares that shell against the PC hull bounding box:
the footprint extents must agree, and the shell floor must sit on the box
floor, both inside one tolerance.  The shell's height extent is deliberately
not gated -- see ``floor_offset``.  Separately, for every component, the
decoded surfaces must sit inside the PC bounding box the client reports for
it; a surface reaching outside is declined rather than costing the vehicle its
other modules.  Every figure is recorded per vehicle in the report.

Only the hull's shells are comparable directly: a gun component's plates cover
the breech while its bounding box spans the barrel, and a turret proxy is not
built to the same silhouette.  So containment is what is known about those
components -- it bounds the frame they sit in, not the accuracy of the
interior inside it.

    python3 tools/bake_internal_layout_console_0922.py "$WOT_0922_CLIENT" \
        --cache ~/console-collision-cache
"""

import argparse
import base64
import collections
import hashlib
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
from bw_primitives_geometry import (UnsupportedPrimitives,
                                    module_surfaces as primitive_surfaces)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import internal_hit_layouts

TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
SCRIPTS_PACKAGE = 'res/packages/scripts.pkg'
ARCHIVE = ('https://wotinspector-archive.s3.eu-central-003.backblazeb2.com'
           '/ai/%s/collision-%d.data')
# Every archived Console package, ordered by how close its release is to this
# client's 2018-02-01 build.  A vehicle takes the first package that yields a
# complete layout registering against the PC bounds, so the era-closest data
# wins wherever it exists and the later packages only fill genuine gaps --
# a vehicle absent from console4.3, or one whose traverse mechanism that
# package does not model.  The package actually used is recorded per vehicle.
CONSOLE_PACKAGES = (
    'console4.3',      # 2018-02-07, six days after this client
    'console4.1',      # 2017-11-07
    'console4.4',      # 2018-04-17
    'console3.9',      # 2017-07-30
    'console4.5',      # 2018-07-15
    'console3.6',      # 2017-05-18
    'console4.6',      # 2018-09-04
    'console3.5',      # 2017-03-02
    'console4.7',      # 2018-11-11
    'console3.4',      # 2016-12-14
    'console4.8',      # 2019-02-13
    'console3.3',      # 2016-10-05
    'console4.9',      # 2019-04-04
    'console4.10-1',   # 2019-07-06
    'console4.11',     # 2019-10-16
    'console4.10',     # 2019-11-20
    'console4.12',     # 2020-02-21
    'console4.13',     # 2020-07-16
)
# The reference frame must stay era-close, because it defines what the baked
# fractions are fractions of.  pc9.22.0 is this client's own build.
PC_PACKAGES = ('pc9.22.0', 'pc9.21.0', 'pc9.20.1.1', 'pc9.20.1', 'pc1.0.0')
PC_PACKAGE = PC_PACKAGES[0]
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
# Catalogue entries that are the same physical tank as another entry, where
# neither the model-path rule nor the content-hash rule sees it because the
# variant was published with its own model directory and its own resources.
# Each pair is reviewed, not guessed: both entries share the item_defs
# per-vehicle code slot -- Ch01_Type59 and Ch01_Type59_Gold are one vehicle
# published twice -- and the same vehicle class, and the tool additionally
# requires the donor to be decoded and the two crew rosters to be a
# permutation of one another before it will inherit.  A suffix is never
# matched on its own; that is how an old profile gets attached to an
# unrelated vehicle which happened to reuse a name.
# Vehicles the archive files under an id our client's formula does not
# produce, found by sweeping console4.13's whole id space and reading the
# vehicle code out of each package's member paths.  So the computed id is a
# first guess, not a guarantee: Console filed the Turan III prototype under
# czech rather than germany, keeps the ISU-130 at two ids, and serves the
# M48A1's model at the id computed for the M48A5.  Each candidate is still
# verified against the member paths before it is used, so a wrong id cannot
# slip through -- see _package_is_for.
ALTERNATE_ARCHIVE_IDS = {
    'france:F75_Char_de_25t': (62529,),
    'germany:G116_Turan_III_prot': (61809,),
    'ussr:R111_ISU130': (31745, 33537),
    'usa:A120_M48A5': (14113,),
}
SAME_TANK_ALIASES = {
    # The premium Type 59, identical hull and identical crew roster.
    'china:Ch01_Type59_Gold': 'china:Ch01_Type59',
    # The 113's Beijing Opera livery.  Its roster lists the driver and gunner
    # in the other order, so the crew zones are remapped by role below.
    'china:Ch22_113_Beijing_Opera': 'china:Ch22_113',
    # VK 45.02 (P) Ausf. B's second catalogue identity, identical roster.
    'germany:G58_VK4502P7': 'germany:G58_VK4502P',
    # Object 907A, identical roster to the Object 907.
    'ussr:R95_Object_907A': 'ussr:R95_Object_907',
}


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


def _open_package(body, password):
    """A reader for one archive package, AES-aware when a password is set.

    Packages released from 2018-07 (``console4.5``) onward encrypt their
    members with WinZip AES; earlier ones are plain.  The password is supplied
    at run time and never stored in this repository -- see ``read_password``.
    """
    if password is None:
        return zipfile.ZipFile(io.BytesIO(body))
    import pyzipper
    archive = pyzipper.AESZipFile(io.BytesIO(body))
    archive.setpassword(password.encode('utf-8'))
    return archive


# Console shipped its collision proxies as Havok packfiles up to console4.9
# and as BigWorld .primitives from console4.10 on, so both containers have to
# be read to reach the whole roster.  The two decoders were written
# independently and cross-check exactly: on the AT 7, whose hull is in both
# console4.3 (Havok) and console4.13 (primitives), all 24 shared surface
# centres agree to 0.000 m.
CONTAINERS = (
    ('.hkx', module_surfaces, UnsupportedPackfile),
    ('.primitives', primitive_surfaces, UnsupportedPrimitives),
)


def console_parts(body, password=None):
    """{port parent: decoded surfaces} for one Console collision package."""
    parts = {}
    with _open_package(body, password) as archive:
        for info in archive.infolist():
            if info.flag_bits & 0x1 and password is None:
                raise UnsupportedPackfile('encrypted member: '
                                          + info.filename)
            leaf = info.filename.rsplit('/', 1)[-1].lower()
            for extension, decode, unsupported in CONTAINERS:
                matched = None
                for suffix, parent in PART_PARENTS:
                    if leaf.endswith('_%s_proxy%s' % (suffix, extension)) or \
                            leaf.endswith('%s_proxy%s' % (suffix, extension)):
                        matched = parent
                        break
                if matched is None:
                    continue
                try:
                    parts[matched] = decode(archive.read(info))
                except unsupported:
                    pass
                except RuntimeError as error:
                    raise UnsupportedPackfile('member unreadable: %s'
                                              % error)
                break
    return parts


def read_password(args):
    """The archive password, from a file or the environment, or None.

    Deliberately never a literal in this file and never written to the
    generated module or the report: it is a credential, supplied by whoever
    runs the tool.  Without it the tool behaves exactly as before and reports
    an encrypted package as an unusable source.
    """
    if getattr(args, 'password_file', None):
        return Path(args.password_file).read_text(
            encoding='utf-8').rstrip('\r\n')
    return os.environ.get('WOT_AI_ARCHIVE_PASSWORD') or None


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


def pc_primitive_hashes(cache, entry):
    """Content fingerprint of a vehicle's PC collision primitives.

    Two catalogue entries whose collision primitives are byte-identical are
    the same mesh in the same frame, so one's decoded interior is the other's
    by identity.  Returns a hashable key, or None when no PC package is
    cached for the vehicle.
    """
    for package in PC_PACKAGES:
        path = cache / ('%s_collision-%d.data' % (package,
                                                  entry['archive_id']))
        if not path.exists():
            continue
        body = path.read_bytes()
        if not body:
            continue
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile:
            continue
        parts = {}
        for info in archive.infolist():
            leaf = info.filename.rsplit('/', 1)[-1].lower()
            if not leaf.endswith('.primitives_processed'):
                continue
            try:
                data = archive.read(info)
            except (RuntimeError, NotImplementedError):
                continue
            parts[leaf] = hashlib.sha1(data).hexdigest()
        return tuple(sorted(parts.items())) or None
    return None


def floor_offset(surfaces, bound):
    """How far the Console shell's lowest plate sits off the PC box floor.

    ``residual`` compares extents, and an extent disagreement is not by
    itself a registration error: ``fractions`` places a Console metre
    coordinate into the PC box by anchoring it at the box minimum, and
    ``fit_target`` reconstructs that against the live bbox of the same
    build, so a zone's position is preserved exactly.  What has to hold is
    that the two datasets share an origin on the axis.

    On x and z, matching extents plus containment force that: a shell as wide
    as the box, inside the box, can only be aligned with it.  On y the
    extents often disagree because the armour shell of an open-topped
    vehicle has no roof while the collision box encloses the compartment, and
    then containment is weak -- a shell 0.8 m shorter fits inside the box at
    any height.  So measure the vertical origin directly: the belly plate on
    the box floor, with the whole shortfall at the ceiling, is that
    open-topped signature and the anchoring holds.
    """
    shell = outer_shell(surfaces)
    if shell is None or bound is None:
        return None
    return shell[0][1] - bound[0][1]


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


def _vehicle_slot(code):
    """The item_defs per-vehicle prefix, e.g. Ch01_Type59_Gold -> ch01."""
    match = re.match(r'^([A-Za-z]+[0-9]+)', code)
    return match.group(1).lower() if match else None


def _package_is_for(body, code, password=None):
    """Whether this package's members really describe this vehicle.

    The archive is keyed by an id computed from our client, and that id is
    not always the one the archive used, so a fetch can succeed and hand back
    a different tank.  Accept a package only when its member paths name the
    vehicle's code or at least its item_defs slot: the archive's own Console
    naming differs cosmetically from the client's in a dozen cases -- R43_T-70
    against R43_T70, R71_IS_2B against R71_IS_2_Berlin -- but the slot is the
    per-vehicle identity and it always agrees.  Without this the M48A5 decodes
    the M48A1's interior.
    """
    try:
        with _open_package(body, password) as archive:
            names = [name.lower() for name in archive.namelist()]
    except Exception:                                     # noqa: BLE001
        return False
    lowered = code.lower()
    if any(lowered in name for name in names):
        return True
    slot = _vehicle_slot(code)
    if slot is None:
        return False
    return any(('/%s_' % slot) in name or ('/%s/' % slot) in name
               for name in names)


def _remap_crew(crew_zones, donor_roster, target_roster):
    """The donor's crew zones in the target's roster order, or None.

    Two catalogue identities of one tank can list the same crew in a
    different order -- the 113 Beijing Opera has the driver and gunner
    swapped against the 113 -- and build_layout indexes crew zones by the
    live descriptor's slot.  Copying positionally would seat the driver in the
    gunner's station, so match the slots by role and refuse the alias if the
    rosters are not a permutation of each other.
    """
    if len(donor_roster) != len(target_roster):
        return None
    if len(crew_zones) != len(donor_roster):
        return None
    available = list(range(len(donor_roster)))
    order = []
    for slot in target_roster:
        match = None
        for index in available:
            if donor_roster[index] == slot:
                match = index
                break
        if match is None:
            return None
        available.remove(match)
        order.append(match)
    # Keep each zone's own id aligned with the slot it now occupies.
    return tuple(
        (crew_zones[source][0], 'crew_%02d' % target_index)
        + tuple(crew_zones[source][2:])
        for target_index, source in enumerate(order))


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
        floor = floor_offset(surfaces, bound) if parent == 'hull' else None
        if gaps is not None:
            record['shell'] = [round(value, 4) for value in gaps]
        if floor is not None:
            record['floor'] = round(floor, 4)
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
        # The hull registers when the two datasets describe the same
        # footprint and share a vertical origin, both inside one tolerance.
        # The height extent is deliberately not gated: see floor_offset.
        if gaps is not None and max(gaps[0], gaps[2]) > max_residual:
            continue
        if floor is not None and abs(floor) > max_residual:
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
    unmodelled = []
    for entity in REQUIRED_ENTITIES:
        if entity in located:
            continue
        # Deliberately no archetype fill.  Console models no traverse
        # mechanism for a fixed superstructure and often no separate optic, in
        # every package it ships -- opening the encrypted 2018-2020 packages
        # raised the fill from 96 records to 101 rather than removing it, so
        # it is a property of Console's modelling, not of which package is
        # read.  Borrowing those two zones from the hand-authored archetypes
        # would put a second, reconstructed kind of source inside a record
        # whose every other number is decoded, and the reader could only tell
        # them apart by the provenance string.  So a decoded record now takes
        # every zone from one source, and a module that source does not model
        # is a named hole.  The retained archetypes stay as a whole-vehicle
        # fallback for vehicles with no decoded geometry at all, where they
        # are labelled reconstructed_archetype and mix with nothing.
        #
        # No decoded surface and no archetype either.  Declare the entity
        # explicitly unavailable rather than discarding the vehicle: the port
        # already has that contract for the tracks it scores natively, and
        # `validate_layout` accepts a target whose source says so.  The
        # alternative is what this tool used to do -- throw away a complete
        # decoded ammunition rack, engine, fuel tank, radio and crew because
        # Console models no traverse mechanism for a fixed superstructure --
        # which leaves the vehicle with no interior at all.  Nothing is
        # invented for it and the record names it, so a reader can tell an
        # unhittable module from a placed one.
        unmodelled.append(entity)
    if not module_zones:
        return None, 'no_module_surfaces', residuals
    return ((tuple(module_zones), tuple(crew_zones),
             tuple(sorted(unmodelled))), None, residuals)


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

Hull registration is measured; every other component is checked for frame
containment.  Both platforms carry the same vehicle's outer shell as
``armor_*`` surfaces, so the hull's two shells are directly comparable: a
vehicle whose footprint extents disagreed by more than
MAX_HULL_REGISTRATION_M, or whose shell floor sat that far off the PC box
floor, was rejected rather than used.  The shell's *height* extent is not
gated, because the armour shell of an open-topped vehicle has no roof while
the collision box encloses the compartment; positions are anchored at the box
minimum rather than scaled to its span, so that difference does not move a
zone.  A turret or gun shell is not built to the same silhouette as its
bounding box, so those components are checked only by containment -- which
bounds the frame, not the interior.
"""

CLIENT_VERSION = %(version)r
CLIENT_BUILD = %(build)r
GEOMETRY_SOURCES = %(sources)r
REFERENCE_FRAME_SOURCE = %(pc_package)r
MAX_HULL_REGISTRATION_M = %(max_residual)r
CATALOGUE_SIZE = %(catalogue)d
DECODED_COUNT = %(decoded)d
CONFIDENCE = 'decoded'

# vehicle key -> (vehicle class, tier, crew roster, module targets the source
# resources do not model at all -- explicitly unavailable, never invented and
# never borrowed from a reconstruction -- module zones, crew zones)
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
         unmodelled) = decoded[key]
        # Key on the same normalized (nation, name) the runtime derives from
        # a descriptor, so the lookup needs no translation.
        text += ('    %r: (\n        %r,\n        %d,\n        %r,\n'
                 '        %r,\n        (\n'
                 % (internal_hit_layouts._profile_key(key), vehicle_class,
                    tier, roster, unmodelled))
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
    parser.add_argument('--password-file', default=None,
                        help='file holding the archive password; packages '
                             'released from 2018-07 encrypt their members. '
                             'Falls back to WOT_AI_ARCHIVE_PASSWORD. Without '
                             'either, an encrypted package is reported as an '
                             'unusable source, as before.')
    parser.add_argument('--limit', type=int, default=0,
                        help='stop after this many vehicles, for a probe')
    args = parser.parse_args()

    client_root = Path(args.client_root).resolve()
    password = read_password(args)
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
        pc = {}
        pc_used = None
        for package in PC_PACKAGES:
            body = fetch(cache, package, vehicle['archive_id'], requests)
            if body is None:
                continue
            bounds = pc_bounds(body)
            if 'hull' in bounds:
                pc, pc_used = bounds, package
                break
        entry = {'reference_frame': pc_used}
        best = None
        attempts = {}
        candidates = ((vehicle['archive_id'],)
                      + ALTERNATE_ARCHIVE_IDS.get(key, ()))
        for package in CONSOLE_PACKAGES:
            body = None
            for candidate in candidates:
                found = fetch(cache, package, candidate, requests)
                if found is None:
                    continue
                if not _package_is_for(found, vehicle['code'], password):
                    attempts[package] = 'id %d holds another vehicle' % (
                        candidate,)
                    continue
                body = found
                if candidate != vehicle['archive_id']:
                    entry['archive_id_used'] = candidate
                break
            if body is None:
                continue
            try:
                parts = console_parts(body, password)
            except UnsupportedPackfile as error:
                attempts[package] = str(error)
                continue
            if not parts:
                attempts[package] = 'no named surfaces'
                continue
            built, reason, residuals = build_vehicle(
                key, vehicle, parts, pc, args.max_residual,
                args.max_overshoot)
            if built is not None:
                best = (package, parts, built, residuals)
                break
            attempts[package] = reason
        if best is None:
            entry['attempts'] = attempts
            # Report why the era-closest package that had this vehicle at all
            # failed, not whichever name sorts first: a later package being
            # encrypted says nothing about the vehicle.
            informative = [attempts[package] for package in CONSOLE_PACKAGES
                           if package in attempts
                           and 'encrypted' not in attempts[package]
                           and attempts[package] != 'no named surfaces']
            rejected[key] = (informative[0] if informative
                             else 'no_console_source')
            entry['reason'] = rejected[key]
            audit[key] = entry
            continue
        package, parts, built, residuals = best
        sources.add(package)
        entry.update({
            'package': package,
            'residuals': residuals,
            'surfaces': dict((parent, sorted(surfaces))
                             for parent, surfaces in parts.items()),
            'module_zones': len(built[0]),
            'crew_zones': len(built[1]),
            'entities_unmodelled': list(built[2]),
        })
        if attempts:
            entry['earlier_attempts'] = attempts
        audit[key] = entry
        decoded[key] = ((vehicle['vehicle_class'], vehicle['tier'],
                         vehicle['crew']) + built)
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

    # A variant published under its own model directory still has the same
    # geometry when its PC collision primitives are byte-identical to another
    # vehicle's.  Identical bytes are the same mesh in the same frame, so this
    # is identity as much as the path rule above is -- it just recognizes it
    # from content rather than from a name.  Only exact whole-set matches
    # count; a shared hull alone would not fix the turret.
    fingerprints = {}
    for key in sorted(vehicles):
        parts = pc_primitive_hashes(cache, vehicles[key])
        if parts:
            fingerprints[key] = parts
    by_content = collections.defaultdict(list)
    for key in decoded:
        if key in fingerprints:
            by_content[fingerprints[key]].append(key)
    by_content_hull = 0
    for key in sorted(rejected):
        if key in inherited or key not in fingerprints:
            continue
        candidates = [name for name in by_content.get(fingerprints[key], [])
                      if vehicles[name]['crew'] == vehicles[key]['crew']]
        if len(set(candidates)) == 1:
            inherited[key] = candidates[0]
            by_content_hull += 1

    for key, donor in inherited.items():
        decoded[key] = decoded[donor]
        audit.setdefault(key, {})['inherited_from'] = donor
        audit[key]['reason'] = 'inherited_identical_tank'
        del rejected[key]
    print('inherited by identical catalogue twin: %d (%d of them matched on '
          'byte-identical PC collision primitives)'
          % (len(inherited), by_content_hull))

    aliased = 0
    for key, donor in sorted(SAME_TANK_ALIASES.items()):
        if key in decoded:
            continue
        if key not in vehicles:
            print('  alias %s declined: not in this client' % key)
            continue
        if donor not in decoded:
            print('  alias %s declined: donor %s is not decoded'
                  % (key, donor))
            continue
        if vehicles[key]['vehicle_class'] != vehicles[donor]['vehicle_class']:
            print('  alias %s declined: class differs from %s' % (key, donor))
            continue
        # decoded holds (class, tier, crew, module_zones, crew_zones,
        # unmodelled); only render() reorders it for the generated table.
        unused_class, unused_tier, unused_crew, modules, crew_zones, holes = (
            decoded[donor])
        remapped = _remap_crew(crew_zones, vehicles[donor]['crew'],
                               vehicles[key]['crew'])
        if remapped is None:
            print('  alias %s declined: roster is not a permutation of %s'
                  % (key, donor))
            continue
        decoded[key] = (vehicles[key]['vehicle_class'],
                        vehicles[key]['tier'], vehicles[key]['crew'],
                        modules, remapped, holes)
        audit.setdefault(key, {})['inherited_from'] = donor
        audit[key]['reason'] = 'same_tank_alias'
        rejected.pop(key, None)
        aliased += 1
    print('inherited through a reviewed same-tank alias: %d' % aliased)

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
