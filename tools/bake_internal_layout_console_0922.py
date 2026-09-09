#!/usr/bin/env python3
"""Bake Console indexed interiors against the exact PC 9.22 component frames.

The pinned PC client's reviewed archives provide exterior collision models,
not internal module meshes. Console HKX and BigWorld resources provide named
internal surfaces. Keep their vertices, triangles, separated pieces and
component-local metres; do not construct bounding-box substitutes.

Select the era-closest registering package with the fewest unavailable
module/crew targets across installed component variants. Retain archive-id
and member identity checks, explicit same-tank aliases, crew-role remapping,
hull footprint/floor registration and per-surface containment checks. Only
pc9.22.0 supplies reference frames. An unsupported component or surface is
reported, never silently rescaled or replaced.

The report records selected packages/ids, every rejected resource and frame,
all mesh/crew holes, and open surface-only pieces. A complete client is
inspected before baking; --scripts-package is a lower-tier resource-only
mode that checks pinned script contracts and explicitly cannot verify a
complete client installation or Windows gameplay.

    python3 tools/bake_internal_layout_console_0922.py "$WOT_0922_CLIENT" \
        --cache DIR --password-file PRIVATE_FILE --output OUT --report REPORT
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
from gui.mods.offline_lan_0922 import internal_hit_layouts, internal_mesh

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
PC_PACKAGES = ('pc9.22.0',)
PC_PACKAGE = PC_PACKAGES[0]
# The nation order the client uses to build a vehicle's compact descriptor,
# which is also the id the archive keys its packages on:
# (list.xml id << 8) | (nation index << 4) | ITEM_TYPE_VEHICLE.
NATIONS = ('ussr', 'germany', 'usa', 'china', 'france', 'uk', 'japan',
           'czech', 'sweden', 'poland')
ITEM_TYPE_VEHICLE = 1
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
# Vehicles the archive also files under an id our client's formula does not
# produce.  Console renumbered extensively, so the computed id is a first
# guess and not a guarantee: 122 of the 680 catalogue entries appear in
# console4.13 under some other id as well, and for a few -- the Char de 25t
# and the Lorraine 40 t have theirs swapped, the M48A5's holds the M48A1's
# model -- the computed id is simply the wrong file.
#
# Derived mechanically rather than hand-picked, by sweeping a package's whole
# id space (255 list ids x 10 nations) and reading the vehicle code out of
# each package's member paths, then recording every id whose code matches a
# client entry's and differs from the computed one.  Exact code matches only:
# pairing a vehicle with a *variant* of itself is what SAME_TANK_ALIASES is
# for, and the two are kept separate on purpose.
#
# The computed id is still tried first, so this only ever rescues a vehicle
# whose own id failed, and every candidate must still satisfy
# _package_is_for, so a wrong id cannot slip through.
ALTERNATE_ARCHIVE_IDS = {
    'china:Ch01_Type59': (33073,),
    'china:Ch01_Type59_Gold': (32817,),
    'china:Ch02_Type62': (32049, 32305),
    'china:Ch06_Renault_NC31': (31281,),
    'china:Ch14_T34_3': (31537,),
    'china:Ch21_T34': (34305,),
    'china:Ch26_59_Patton': (31793,),
    'china:Ch39_WZ120_1G_FT': (32561,),
    'czech:Cz01_Skoda_T40': (61553,),
    'czech:Cz05_T34_100': (34305, 62065),
    'france:F01_RenaultFT': (31297,),
    'france:F05_BDR_G1B': (31041, 31553),
    'france:F16_AMX_13_75': (36177, 63809),
    'france:F19_Lorraine40t': (5697,),
    'france:F64_AMX_50Fosh_155': (27713, 61761),
    'france:F65_FCM_50t': (25921,),
    'france:F68_AMX_Chasseur_de_char_46': (26433,),
    'france:F69_AMX13_57_100': (26177, 26945, 63297),
    'france:F73_M4A1_Revalorise': (26689,),
    'france:F74_AMX_M4_1949': (27201, 62017),
    'france:F75_Char_de_25t': (62529,),
    'france:F84_Somua_SM': (27457,),
    'france:F88_AMX_13_105': (27969,),
    'france:F89_Canon_dassaut_de_105': (27713,),
    'france:F97_ELC_EVEN_90': (28225,),
    'germany:G03_PzV_Panther': (32865, 38417),
    'germany:G04_PzVI_Tiger_I': (31761, 37121, 43585),
    'germany:G05_StuG_40_AusfG': (33297,),
    'germany:G109_Steyr_WT': (36881,),
    'germany:G112_KanonenJagdPanzer_105': (64529,),
    'germany:G114_Skorpian': (50193,),
    'germany:G115_Typ_205_4_Jun': (19729,),
    'germany:G116_Turan_III_prot': (61809,),
    'germany:G117_Toldi_III': (31617,),
    'germany:G118_VK4503': (35089,),
    'germany:G119_Panzer58': (36113, 36369),
    'germany:G120_M41_90': (34577, 34833),
    'germany:G12_Ltraktor': (31505,),
    'germany:G15_VK3601H': (37649,),
    'germany:G16_PzVIB_Tiger_II': (34321, 39201),
    'germany:G18_JagdPanther': (37137,),
    'germany:G36_PzII_J': (30993,),
    'germany:G37_Ferdinand': (35601,),
    'germany:G51_Lowe': (32529,),
    'germany:G78_Panther_M10': (33553,),
    'germany:G81_Pz_IV_AusfH': (31249, 35345),
    'germany:G92_VK7201': (19473,),
    'germany:G99_RhB_Waffentrager': (36625,),
    'japan:J01_NC27': (31329,),
    'japan:J02_Te_Ke': (32609,),
    'japan:J18_STA_2_3': (31841, 32097),
    'japan:J19_Tiger_I_Jpn': (33041, 35857),
    'japan:J24_Mi_To_130_tons': (31585, 32353),
    'poland:Pl03_PzV_Poland': (51345,),
    'sweden:S15_L_60': (31361,),
    'sweden:S22_Strv_S1': (31105,),
    'sweden:S23_Strv_81': (32385,),
    'uk:GB01_Medium_Mark_I': (31313,),
    'uk:GB04_Valentine': (38225,),
    'uk:GB07_Matilda': (34385,),
    'uk:GB09_Churchill_VII': (33105,),
    'uk:GB19_Sherman_Firefly': (32337,),
    'uk:GB21_Cromwell': (31057, 31825, 37969),
    'uk:GB22_Comet': (35409,),
    'uk:GB23_Centurion': (34129, 35665, 37201),
    'uk:GB24_Centurion_Mk3': (32385, 35665),
    'uk:GB33_Sentinel_AC_I': (33873,),
    'uk:GB35_Sentinel_AC_IV': (33361,),
    'uk:GB52_A45': (32849,),
    'uk:GB70_N_FV4202_105': (14929,),
    'uk:GB76_Mk_VIC': (35921,),
    'uk:GB77_FV304': (33617,),
    'uk:GB78_Sexton_I': (54049,),
    'uk:GB80_Charioteer': (34641,),
    'uk:GB87_Chieftain_T95_turret': (32593,),
    'uk:GB93_Caernarvon_AX': (38737, 40017),
    'usa:A01_T1_Cunningham': (31777,),
    'usa:A05_M4_Sherman': (35329, 35361),
    'usa:A103_T71E1': (33825, 38945),
    'usa:A111_T25_Pilot': (34849,),
    'usa:A115_Chrysler_K': (39713,),
    'usa:A11_T29': (39457,),
    'usa:A12_T32': (40993,),
    'usa:A13_T34_hvy': (34337,),
    'usa:A21_T14': (31265,),
    'usa:A33_MTLS-1G14': (34593,),
    'usa:A34_M24_Chaffee': (41249, 63777, 63809),
    'usa:A41_M18_Hellcat': (37153, 41761),
    'usa:A43_M22_Locust': (31521,),
    'usa:A45_M6A2E1': (40481, 40737),
    'usa:A57_M8A1': (39969,),
    'usa:A63_M46_Patton': (40225,),
    'usa:A74_T1_E6': (31009, 34081),
    'usa:A80_T26_E4_SuperPershing': (65057,),
    'usa:A93_T7_Combat_Car': (32033, 32289, 32545, 32801, 33057, 33569),
    'usa:A97_M41_Bulldog': (38177,),
    'usa:A99_T92_LT': (41505,),
    'ussr:R07_T-34-85': (31489, 58625),
    'ussr:R105_BT_7A': (33025,),
    'ussr:R108_T34_85M': (35585,),
    'ussr:R111_ISU130': (31745, 33537),
    'ussr:R112_T54_45': (32001,),
    'ussr:R113_Object_730': (34049,),
    'ussr:R118_T28_F30': (33793,),
    'ussr:R11_MS-1': (30977,),
    'ussr:R123_Kirovets_1': (35841,),
    'ussr:R125_T_45': (38401,),
    'ussr:R128_KV4_Kreslavskiy': (35073,),
    'ussr:R133_KV_122': (36609,),
    'ussr:R135_T_103': (36353,),
    'ussr:R143_T_29': (41217,),
    'ussr:R18_SU-152': (36097,),
    'ussr:R38_KV-220': (32769,),
    'ussr:R52_Object_261': (63489,),
    'ussr:R54_KV-5': (32257, 34561, 41473),
    'ussr:R61_Object252': (32513, 41729),
    'ussr:R77_KV2': (37633,),
    'ussr:R80_KV1': (32273,),
    'ussr:R86_LTP': (36865,),
    'ussr:R93_Object263': (14337,),
    'ussr:R96_Object_430': (17153,),
    'ussr:R99_T44_122': (42241,),
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
    # The premium T-44-100.  This pair does not share the item_defs slot, so
    # it is admitted on the other evidence _alias_evidence accepts: the two
    # hulls' PC collision bounds are identical to four decimals and the crew
    # rosters match.  The client exports a separate collision mesh per
    # variant, so the bytes differ while the hull does not.
    'ussr:R127_T44_100_P': 'ussr:R122_T44_100',
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
    package_path = client_root if client_root.is_file() else client_root / SCRIPTS_PACKAGE
    with zipfile.ZipFile(str(package_path)) as archive:
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
    url = ARCHIVE % (package, archive_id)
    if path.exists():
        body = path.read_bytes()
        log.append({'url': url, 'status': 200 if body else 404,
                    'bytes': len(body), 'cached': True})
        return body or None
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


def _part_parent(part):
    if part in ('hull', 'chassis'):
        return part
    match = re.match(r'^(turret|gun)_\d+$', part)
    return match.group(1) if match else None


def console_parts(body, password=None, package='', rejected=None):
    """Decode each exact component variant; never overwrite another turret."""
    parts = {}
    ambiguous = set()
    with _open_package(body, password) as archive:
        for info in archive.infolist():
            if info.flag_bits & 0x1 and password is None:
                raise UnsupportedPackfile('encrypted member: ' + info.filename)
            leaf = info.filename.rsplit('/', 1)[-1].lower()
            match = re.search(r'(hull|chassis|turret_\d+|gun_\d+)_proxy\.(hkx|primitives)$', leaf)
            if match is None:
                continue
            part, extension = match.groups()
            decode = module_surfaces if extension == 'hkx' else primitive_surfaces
            try:
                surfaces = decode(archive.read(info))
                if part in parts or part in ambiguous:
                    parts.pop(part, None)
                    ambiguous.add(part)
                    raise UnsupportedPackfile('ambiguous duplicate component: ' + part)
                for surface in surfaces.values():
                    surface['source_member'] = info.filename
                    surface['source_package'] = package
                parts[part] = surfaces
            except (UnsupportedPackfile, UnsupportedPrimitives) as error:
                if rejected is not None:
                    rejected[info.filename] = str(error)
            except RuntimeError as error:
                raise UnsupportedPackfile('member unreadable: %s' % error)
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
            if '/collision_client/' not in info.filename.lower() or not info.filename.endswith('.visual_processed'):
                continue
            leaf = info.filename.rsplit('/', 1)[-1].lower()
            stem = leaf[:-len('.visual_processed')]
            if _part_parent(stem) is None:
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
                bounds[stem] = (values['min'], values['max'])
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
    """The Console shell floor relative to the exact PC component origin.

    Open-topped armour shells may be shorter than the exterior visual box;
    matching footprint and floor are therefore the useful hull checks.
    Decoded vertices stay in metres and are never normalized to either box.
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


def _pc_hull_bound(cache, vehicle, key):
    """The PC hull bounding box for one vehicle, or None."""
    ids = (vehicle['archive_id'],) + ALTERNATE_ARCHIVE_IDS.get(key, ())
    for package in PC_PACKAGES:
        for archive_id in ids:
            path = cache / ('%s_collision-%d.data' % (package, archive_id))
            if not path.exists():
                continue
            body = path.read_bytes()
            if not body:
                continue
            try:
                names = zipfile.ZipFile(io.BytesIO(body)).namelist()
            except zipfile.BadZipFile:
                continue
            if not any(vehicle['code'].lower() in member.lower()
                       for member in names):
                continue
            if not _package_is_for(body, vehicle['code']):
                continue
            bounds = pc_bounds(body)
            if 'hull' in bounds:
                return bounds['hull']
    return None


def _alias_evidence(cache, vehicles, key, donor):
    """Why these two catalogue entries are one tank, or None to refuse.

    Two kinds are accepted, both checked here rather than asserted in the
    table.  ``slot`` is the item_defs per-vehicle code: Ch01_Type59 and
    Ch01_Type59_Gold are one vehicle published twice.  ``hull_bounds`` is a
    measurement, for a pair that does not share the slot -- the T-44-100 (P)
    against the T-44-100 -- where the two hulls' PC collision bounds are
    identical to four decimals and the crew rosters match.  The client exports
    a separate collision mesh per variant, so byte identity is too strict to
    recognize that; the bound is not.

    A bare name suffix is never evidence on its own, which is how an old
    profile gets attached to an unrelated vehicle that reused a name.
    """
    if vehicles[key]['vehicle_class'] != vehicles[donor]['vehicle_class']:
        return None
    if _vehicle_slot(vehicles[key]['code']) == _vehicle_slot(
            vehicles[donor]['code']):
        return 'slot'
    if vehicles[key]['crew'] != vehicles[donor]['crew']:
        return None
    mine = _pc_hull_bound(cache, vehicles[key], key)
    theirs = _pc_hull_bound(cache, vehicles[donor], donor)
    if mine is None or theirs is None:
        return None
    for side in range(2):
        for axis in range(3):
            if round(mine[side][axis], 4) != round(theirs[side][axis], 4):
                return None
    return 'hull_bounds'


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
    # Each crew slot may have one source surface per installed component.
    return tuple(None if crew_zones[source] is None else tuple(
        (zone[0], 'crew_%02d' % target_index, zone[2])
        for zone in crew_zones[source])
        for target_index, source in enumerate(order))


def _geometry_record(surface, bound, part):
    vertices, triangles = surface.get('vertices', ()), surface.get('triangles', ())
    pieces = internal_mesh.prepare(vertices, triangles)
    return {
        'part': part,
        'reference_bounds': bound,
        'payload': internal_mesh.encode(vertices, triangles),
        'source_package': surface.get('source_package', ''),
        'source_member': surface.get('source_member', ''),
        'vertices': len(vertices), 'triangles': len(triangles),
        'closed_pieces': sum(piece['closed'] for piece in pieces),
        'open_pieces': sum(not piece['closed'] for piece in pieces),
    }


def build_vehicle(key, vehicle, console, pc, max_residual, max_overshoot):
    """Indexed surfaces in their matching PC component frame, without fitting."""
    modules, crew, residuals, usable = [], [], {}, {}
    for part, surfaces in sorted(console.items()):
        bound = pc.get(part)
        if bound is None:
            residuals[part] = {'rejected': 'no_exact_pc_component_bounds'}
            continue
        parent = _part_parent(part)
        record = {}
        gaps = residual(surfaces, bound) if parent == 'hull' else None
        floor = floor_offset(surfaces, bound) if parent == 'hull' else None
        if gaps is not None:
            record['shell'] = [round(value, 6) for value in gaps]
        if floor is not None:
            record['floor'] = round(floor, 6)
        residuals[part] = record
        if gaps is None and parent == 'hull':
            record['rejected'] = 'no_console_outer_shell'
            continue
        if gaps is not None and max(gaps[0], gaps[2]) > max_residual:
            record['rejected'] = 'hull_footprint_mismatch'
            continue
        if floor is not None and abs(floor) > max_residual:
            record['rejected'] = 'hull_floor_mismatch'
            continue
        kept, dropped = {}, {}
        for name, item in sorted(surfaces.items()):
            if name not in MODULE_ENTITIES and name not in CREW_SURFACES:
                continue
            reach = max([0.0] + [value for axis in range(3) for value in (
                bound[0][axis] - item['minimum'][axis],
                item['maximum'][axis] - bound[1][axis])])
            if reach > max_overshoot:
                dropped[name] = 'outside_component:%.6f_m' % reach
                continue
            try:
                kept[name] = _geometry_record(item, bound, part)
            except ValueError as error:
                dropped[name] = 'invalid_mesh:' + str(error)
        record['dropped_surfaces'] = dropped
        if kept:
            usable[part] = kept
    if 'hull' not in usable:
        return None, 'hull_unregistered_or_no_interior_mesh', residuals
    for part, surfaces in sorted(usable.items()):
        for name, geometry in sorted(surfaces.items()):
            if name in MODULE_ENTITIES:
                modules.append((MODULE_ENTITIES[name], _part_parent(part),
                                name, geometry))
    for index, surface in enumerate(crew_surface_names(vehicle['crew'])):
        alternatives = tuple((_part_parent(part), 'crew_%02d' % index, surfaces[surface])
                             for part, surfaces in sorted(usable.items())
                             if surface in surfaces)
        crew.append(alternatives or None)
    located = set(zone[0] for zone in modules)
    unmodelled = tuple(entity for entity in REQUIRED_ENTITIES if entity not in located)
    if not modules:
        return None, 'no_module_meshes', residuals
    return ((tuple(modules), tuple(crew), tuple(sorted(unmodelled))), None, residuals)


def _coverage_holes(built, pc):
    """Account for every installed turret variant, not just the first one."""
    modules, crew, unused_unmodelled = built
    turrets = sorted(part for part in pc if _part_parent(part) == 'turret') or [None]
    holes = 0
    for turret in turrets:
        active = set(('hull', 'chassis', turret))
        located = set(zone[0] for zone in modules if zone[3]['part'] in active)
        holes += len(set(REQUIRED_ENTITIES) - located)
        holes += sum(not any(zone[2]['part'] in active for zone in (alternatives or ()))
                     for alternatives in crew)
    return holes


HEADER = '''# -*- coding: utf-8 -*-
"""Generated Console indexed interiors, registered to exact PC 9.22 components.

Regenerate with tools/bake_internal_layout_console_0922.py; do not edit.
Every mesh retains component-local decoded vertices and indexed triangles,
losslessly compressed as IM01. Reference bounds identify the PC component;
they never scale, cap, recenter or enclose the geometry for final collision.
Crew slots contain alternative meshes for their actual component variants.
Missing slots/modules are explicit. Open pieces have surface-only collision.
This is Console geometry, not original PC server geometry or Windows acceptance.
"""

MESH_SCHEMA = 1
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
    parser.add_argument('client_root', nargs='?')
    parser.add_argument('--scripts-package', help='resource-only bake from an extracted scripts.pkg; does not verify a complete client')
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

    if bool(args.client_root) == bool(args.scripts_package):
        parser.error('provide client_root or --scripts-package, exclusively')
    client_root = Path(args.client_root or args.scripts_package).resolve()
    password = read_password(args)
    if args.scripts_package:
        # An extracted resource archive is sufficient for roster/crew data.
        # Check the repository's pinned contracts and state this lower evidence
        # tier explicitly; never manufacture version.xml or a complete client.
        from inspect_client import PINNED_ENTITY_DEFINITION_SHA256, PROBE_MEMBERS
        with zipfile.ZipFile(client_root) as archive:
            for name, expected in PINNED_ENTITY_DEFINITION_SHA256.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise SystemExit('pinned scripts contract mismatch: ' + name)
            for name in PROBE_MEMBERS:
                if archive.read(name)[:4] != b'\x03\xf3\r\n':
                    raise SystemExit('Python 2.7 bytecode contract mismatch: ' + name)
        version, build = TARGET_VERSION, TARGET_BUILD
        client_evidence = 'extracted_scripts_pinned_contracts_only'
    else:
        from inspect_client import inspect_client
        inspect_client(client_root)
        version, build = _client_identity(client_root)
        client_evidence = 'inspect_client'

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
            if not _package_is_for(body, vehicle['code']):
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
            archive_id_used = None
            for candidate in candidates:
                found = fetch(cache, package, candidate, requests)
                if found is None:
                    continue
                if not _package_is_for(found, vehicle['code'], password):
                    attempts[package] = 'id %d holds another vehicle' % (
                        candidate,)
                    continue
                body = found
                archive_id_used = candidate
                break
            if body is None:
                continue
            try:
                decode_rejections = {}
                parts = console_parts(body, password, package, decode_rejections)
                if decode_rejections:
                    entry.setdefault('decode_rejections', {})[package] = decode_rejections
            except UnsupportedPackfile as error:
                attempts[package] = str(error)
                continue
            if not parts:
                attempts[package] = 'no named surfaces'
                continue
            built, reason, residuals = build_vehicle(
                key, vehicle, parts, pc, args.max_residual,
                args.max_overshoot)
            if built is None:
                entry.setdefault('registration_rejections', {})[package] = residuals
                attempts[package] = reason
                continue
            # A layout may carry holes -- a module or a crew station the
            # resources do not model.  Era-closeness decides between equally
            # complete layouts, but a hole is a real loss, so keep looking and
            # let a later package that models more win.  Without this the
            # era-closest package is taken whatever it is missing, and the
            # E 100 ships four of its six crew unhittable because console4.3
            # does not model them while a later package does.
            holes = _coverage_holes(built, pc)
            if best is None or holes < best[0]:
                best = (holes, package, parts, built, residuals, archive_id_used)
                if holes == 0:
                    break
            attempts[package] = '%s (%d unmodelled)' % (
                reason or 'usable', holes)
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
        unused_holes, package, parts, built, residuals, archive_id_used = best
        sources.add(package)
        entry.update({
            'package': package,
            'archive_id_used': archive_id_used,
            'residuals': residuals,
            'surfaces': dict((parent, sorted(surfaces))
                             for parent, surfaces in parts.items()),
            'module_zones': len(built[0]),
            'crew_zones': sum(len(z or ()) for z in built[1]),
            'crew_slots_missing': [i for i, z in enumerate(built[1]) if z is None],
            'configuration_holes': _coverage_holes(built, pc),
            'pc_components': sorted(pc),
            'entities_unmodelled': list(built[2]),
        })
        entry['meshes'] = [dict({'entity': entity, 'parent': parent, 'zone_id': zone_id},
            **dict((k, v) for k, v in geometry.items() if k != 'payload'))
            for entity, parent, zone_id, geometry in built[0]]
        entry['crew_meshes'] = [dict({'crew_index': i, 'parent': parent, 'zone_id': zone_id},
            **dict((k, v) for k, v in geometry.items() if k != 'payload'))
            for i, alternatives in enumerate(built[1])
            for parent, zone_id, geometry in alternatives or ()]
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
        evidence = _alias_evidence(cache, vehicles, key, donor)
        if evidence is None:
            print('  alias %s declined: no evidence it is %s' % (key, donor))
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
        audit[key]['reason'] = 'same_tank_alias:' + evidence
        rejected.pop(key, None)
        aliased += 1
    print('inherited through a reviewed same-tank alias: %d' % aliased)

    output.write_text(render(version, build, len(vehicles), decoded,
                             args.max_residual, sorted(sources)),
                      encoding='utf-8')
    report_path = (Path(args.report).resolve() if args.report else
                   cache / 'console_layout_report.json')
    report_path.write_text(json.dumps(
        {'client': {'version': version, 'build': build, 'evidence': client_evidence},
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
