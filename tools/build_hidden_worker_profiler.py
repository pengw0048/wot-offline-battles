"""Build a reversible diagnostic delta for an existing #1513 install."""

from __future__ import print_function

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


TOOLS_ROOT = os.path.abspath(os.path.dirname(__file__))
PORT_ROOT = os.path.abspath(os.path.join(TOOLS_ROOT, '..'))
# Since v0.6.6 the #1513 port is the repository root itself.
PROJECT_ROOT = PORT_ROOT
if PORT_ROOT not in sys.path:
    sys.path.insert(0, PORT_ROOT)

import build_wotmod


DIAGNOSTIC_IDENTITY_ENV = 'WOT_OFFLINE_PROFILER_BUILD_IDENTITY'
DIAGNOSTIC_IDENTITY_PATTERN = re.compile(
    r'^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$')
MARKER_FILENAME = 'hidden_worker_profiler_build.json'
PACKAGE_PREFIX = 'WoT-0.9.22-Hidden-Worker-Profiler-'
INSTALLER_FILES = (
    'INSTALL_HIDDEN_WORKER_PROFILER.bat',
    'UNINSTALL_HIDDEN_WORKER_PROFILER.bat',
    'COLLECT_HIDDEN_WORKER_PROFILE.bat',
    'hidden_worker_profiler_package.ps1',
    'collect_hidden_worker_profile.ps1',
)


def diagnostic_build_identity(environ=None, now=None, random_hex=None):
    environ = os.environ if environ is None else environ
    explicit = str(environ.get(DIAGNOSTIC_IDENTITY_ENV, '') or '').strip()
    if explicit:
        if DIAGNOSTIC_IDENTITY_PATTERN.match(explicit) is None:
            raise SystemExit('%s is invalid' % DIAGNOSTIC_IDENTITY_ENV)
        return explicit
    now = time.time() if now is None else float(now)
    random_hex = uuid.uuid4().hex if random_hex is None else str(random_hex)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))
    return 'profiler-%s-%s' % (stamp, random_hex[:12])


def _git_value(arguments):
    with open(os.devnull, 'wb') as null_stream:
        try:
            return subprocess.check_output(
                ['git'] + list(arguments), cwd=PROJECT_ROOT,
                stderr=null_stream).decode('ascii', 'replace').strip()
        except (OSError, subprocess.CalledProcessError):
            return None


def source_identity():
    revision = _git_value(('rev-parse', 'HEAD'))
    if revision is None or re.match(r'^[0-9a-f]{40}$', revision) is None:
        revision = 'unknown'
    status = _git_value((
        'status', '--porcelain', '--untracked-files=all', '--',
        'src', 'tools', 'build_wotmod.py', 'meta.xml', 'LICENSE',
        'THIRD_PARTY_NOTICES.md', 'licenses'))
    # A source tree whose cleanliness cannot be established is not clean.
    dirty = True if status is None else bool(status)
    return revision, dirty


def build_marker(build_identity, package_name, package_digest,
                 source_revision, source_dirty):
    return {
        'schema': 1,
        'diagnostic': 'hidden_worker_profiler',
        'diagnosticBuildIdentity': str(build_identity),
        'baseModId': build_wotmod.MOD_ID,
        'baseSemanticVersion': build_wotmod.MOD_VERSION,
        'packageFile': str(package_name),
        'packageSha256': str(package_digest),
        'sourceRevision': str(source_revision),
        'sourceDirty': bool(source_dirty),
    }


def install_text(build_identity):
    return """WoT 0.9.22 #1513 hidden-worker Python performance experiment
====================================================================

Diagnostic build: {identity}

This reversible profiler overlay adds function-level Python profiling and
fire-latency instrumentation to the current hidden-worker Python workload.
It changes no gameplay rule, cadence, budget or wire message. It is built
for an existing v0.6.7 launcher installation. You
still start the game, LAN server, visible client and hidden
worker through that same launcher. This ZIP only avoids rebuilding or replacing
the launcher itself.

Run the launcher once and let it finish its normal v0.6.7 installation before
using this ZIP. The ZIP does not contain the LAN server, baked map data, native
bridge, launcher, or any player-owned configuration. It replaces the one
existing org.peng.offline_lan_0922 WOTMOD and keeps a private backup for
uninstall.

This build records one comprehensive profile of the hidden worker's Python
main thread with the interpreter's builtin _lsprof engine (the engine behind
cProfile). Three 30-second windows start 25 s, 85 s and 145 s after the battle
goes live; each writes offline-worker-lsprof-round<N>-w<K>.txt (functions by
self time, by cumulative time, and by module) plus a .pstats file next to
authority_worker_status.json. While a window is open the worker's frames are
slower than normal; that is the measurement cost, not a gameplay change. The
worker also logs one LSPROF platform line (CPU architecture, emulation marker,
processor count, Python build) and millisecond FIRE INTENT RECEIVED / FIRE
LAUNCH / FIRE COMMIT lines for every human shot. The visible client logs FIRE
TRIGGER / FIRE SHOWN / FIRE CURSOR / FIRE TRACER MOVING lines so the delay
between pressing fire, seeing the muzzle flash and seeing the shell move can be
read directly. Timing and counters are aggregated; the profiler itself does not
emit one log line per Bot operation.

This build also runs the staged native vehicle physics probe in the hidden
worker, starting 8 s after the battle goes live: it inventories the exe's
WGVehiclePhysics / WGDynamicsSimulator / WGPhysicalBody Python surface and
physics_shared, looks for a retail physics object on each Bot's native
WGVehicleFilter, mirrors physics_shared.updateCommonConf() once, then builds
standalone bodies from the Bots' own descriptors with the retail server recipe
(physics_shared.configurePhysics over g_defaultTankXPhysicsCfg, engine
smplEnginePower and the vehicle speed limits from the client descriptor) and
checks the native WGVehiclePhysics.configure(cfg) return value before reading a
single attribute. On a body that is never simulated it then tests the pose,
handbrake, cruise and staticMode setters, the simulation subscriptions, the
ground queries, an impulse, and whether the owner setter accepts the worker's
own avatar. Then, through explicit four-argument WGDynamicsSimulator.update
batches: body A is stepped once to see whether the solver honours the seeded
pose (falling back through the other pose setters if not), driven forward,
rotated, reversed and stopped with per-frame position, height above ground,
speed, yaw and freeze samples; body B is re-seeded 20 m ahead of A facing it and
both drive into each other for contact callbacks; up to 29 bodies are batched
idle, in staticMode and all driving for cost; finally A receives an impulse and
the ground queries. Every signal and staticMode is zeroed afterwards. Play at
least 60 s after the battle goes live. Standalone bodies have no presentation,
so nothing visible moves and only their read-back matrices are evidence. The
report records the exact cfg handed to configure(), its return value, every
attribute write, and per-stage callback counts with their first arguments.
Every native call is announced with an NPHYS step line and each
stage rewrites offline-worker-native-physics-probe-round<N>.json next to
authority_worker_status.json, so a native crash still leaves the earlier
stages on disk (collect the dump the launcher wrote). To skip stages or disable
either diagnostic, create
mods\configs\offline_lan_0922\worker_diagnostics.json, for example
{{"native_physics_probe": {{"enabled": false}}}} or
{{"native_physics_probe": {{"stages": ["inventory", "inspect_existing"],
"disable_lsprof": true}}}}.

Install
-------

1. Close the launcher, every visible client and hidden worker.
2. Extract this ZIP. Do not copy payload files by hand.
3. Run INSTALL_HIDDEN_WORKER_PROFILER.bat. If the ZIP is not extracted in the
   game folder, enter the exact folder that contains WorldOfTanks.exe, or pass
   it as the first argument. If an earlier profiler build is still installed,
   this updates it in place and preserves the original launcher WOTMOD backup.
4. Reopen and use the exact same launcher to start the battle normally. Do not
   request a forced repair/reinstall and do not switch to another launcher build
   while collecting evidence, because either action intentionally restores its
   bundled mod.

Normally the launcher starts the visible client and hidden worker from the
same selected client folder, so one install covers both. If a diagnostic setup
uses another physical #1513 client copy for the hidden worker, run the
installer again with that copy as its argument.

Collect
-------

Play a 15-vs-15 battle for at least three minutes and fire the gun a number
of times, including while many Bots are fighting. Then, while the battle is
still running, run COLLECT_HIDDEN_WORKER_PROFILE.bat. The
default capture is 90 seconds, so one report spans multiple detailed and
lightweight profiler windows. Its optional arguments are the game folder
and capture seconds. The
collector reads the PID from authority_worker_status.json, samples CPU and
working set, attempts Windows' PID-scoped GPU Engine counter, copies every
offline-worker-lsprof-*.txt/.pstats report written so far, and creates a ZIP
beside these scripts. If the GPU counter is unavailable or localised
differently, the samples record it as unavailable rather than estimating it.
The report also copies the worker, visible-client and launcher logs when their
fixed paths are available. These logs can contain local paths and session
addresses; inspect the report before sharing it.

Uninstall
---------

Close every client, then run UNINSTALL_HIDDEN_WORKER_PROFILER.bat against the
same game folder. It verifies the installed diagnostic package, removes it,
and restores the exact WOTMOD files and diagnostic marker that existed before
installation. Saved endpoint, garage, account and battle-result files are not
read, packaged, changed or deleted. The offline-worker-lsprof-* reports are
plain evidence files and may be deleted by hand once collected.
""".format(identity=build_identity)


def build(output_root):
    build_identity = diagnostic_build_identity()
    source_revision, source_dirty = source_identity()
    output_root = os.path.abspath(output_root)
    if not os.path.isdir(output_root):
        os.makedirs(output_root)
    work_root = tempfile.mkdtemp(prefix='hidden-worker-profiler-')
    try:
        compiled_root = os.path.join(work_root, 'compiled')
        package_path, package_digest = build_wotmod.build_wotmod_package(
            compiled_root)
        bundle_root = os.path.join(work_root, 'bundle')
        payload_mod_root = os.path.join(
            bundle_root, 'payload', 'mods', '0.9.22.0.1')
        os.makedirs(payload_mod_root)
        package_name = os.path.basename(package_path)
        shutil.copy2(package_path, os.path.join(payload_mod_root, package_name))

        marker = build_marker(
            build_identity, package_name, package_digest,
            source_revision, source_dirty)
        with open(os.path.join(bundle_root, MARKER_FILENAME), 'wb') as stream:
            payload = json.dumps(marker, indent=2, sort_keys=True) + '\n'
            stream.write(payload.encode('utf-8'))
        with open(os.path.join(
                bundle_root, 'INSTALL_HIDDEN_WORKER_PROFILER.txt'), 'wb') as stream:
            stream.write(install_text(build_identity).encode('utf-8'))
        for filename in INSTALLER_FILES:
            shutil.copy2(os.path.join(TOOLS_ROOT, filename), bundle_root)
        build_wotmod._copy_legal_files(bundle_root)

        archive_name = PACKAGE_PREFIX + build_identity + '.zip'
        archive_path = os.path.join(output_root, archive_name)
        build_wotmod._archive_tree(bundle_root, archive_path)
        print('diagnostic build identity=%s' % build_identity)
        print('source revision=%s dirty=%s' %
              (source_revision, 'yes' if source_dirty else 'no'))
        print(archive_path)
        return archive_path
    finally:
        shutil.rmtree(work_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--output', default=os.path.join(PORT_ROOT, 'dist'),
        help='directory that receives the diagnostic ZIP')
    arguments = parser.parse_args(argv)
    build(arguments.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
