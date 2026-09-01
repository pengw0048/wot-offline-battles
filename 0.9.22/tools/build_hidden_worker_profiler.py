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
PROJECT_ROOT = os.path.abspath(os.path.join(PORT_ROOT, '..'))
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
        'status', '--porcelain', '--untracked-files=all', '--', '0.9.22'))
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
    return """WoT 0.9.22 #1513 hidden-worker profiler
================================================

Diagnostic build: {identity}

This is a reversible delta for an existing v0.6.2 launcher installation. Run
that same v0.6.2 launcher once and let it finish its normal installation before
using this ZIP. The ZIP does not contain the LAN server, baked map data, native
bridge, launcher, or any player-owned configuration. It replaces the one existing
org.peng.offline_lan_0922 WOTMOD and keeps a private backup for uninstall.

System baseline first
---------------------

Extract this ZIP before installing its payload. Start one ordinary 15-vs-15
battle with the currently installed v0.6.2 product and run
COLLECT_HIDDEN_WORKER_PROFILE.bat for 60 seconds. A missing diagnostic marker
is expected for this first report. Stop the room and every client, then install
the profiler and repeat the same map, lineup, visible-client count and
60-second collection.

The build marker records the diagnostic package's exact source revision. That
revision can include main-branch changes made after the launcher package you
currently have, even though both packages retain semantic version 0.6.2.
Therefore this before/after pair is a whole-product system baseline, not an
isolated estimate of profiler overhead. Within the diagnostic build, the light
phase is only a wrapper-only observational baseline: it measures the extra
clocks, aggregation and bounded trace work, but still includes wrapper dispatch
and is not an unmodified-client or causal comparison.

Install
-------

1. Close every visible client and hidden worker.
2. Extract this ZIP. Do not copy payload files by hand.
3. Run INSTALL_HIDDEN_WORKER_PROFILER.bat. If the ZIP is not extracted in the
   game folder, enter the exact folder that contains WorldOfTanks.exe, or pass
   it as the first argument.
4. Continue using the exact same launcher build. Do not request a forced
   repair/reinstall and do not switch to another launcher build while collecting
   evidence, because either action intentionally restores its bundled mod.

Normally the launcher starts the visible client and hidden worker from the
same selected client folder, so one install covers both. If a diagnostic setup
uses another physical #1513 client copy for the hidden worker, run the
installer again with that copy as its argument.

Collect
-------

While a 15-vs-15 battle is running, run COLLECT_HIDDEN_WORKER_PROFILE.bat.
The default capture is 60 seconds. Its optional arguments are the game folder
and capture seconds. The collector reads the PID from
authority_worker_status.json, samples CPU and working set, attempts Windows'
PID-scoped GPU Engine counter, and creates a ZIP beside these scripts. If the
GPU counter is unavailable or localised differently, the samples record it as
unavailable rather than estimating it. The report also copies the worker and
launcher logs when their fixed paths are available. These logs can contain
local paths and session addresses; inspect the report before sharing it.

Uninstall
---------

Close every client, then run UNINSTALL_HIDDEN_WORKER_PROFILER.bat against the
same game folder. It verifies the installed diagnostic package, removes it,
and restores the exact WOTMOD files and diagnostic marker that existed before
installation. Saved endpoint, garage, account and battle-result files are not
read, packaged, changed or deleted.
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
