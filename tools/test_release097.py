"""Run the isolated 0.9.7 regressions without sharing native test doubles."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922'
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
           RECORDER_SOURCE=str(MODULE),
           RECORDER_HELPER=str(ROOT / 'tools/replay/replay_writer_process.py'),
           REPLAY_READER_HELPER=str(ROOT / 'tools/replay/replay_reader_process.py'),
           PY3_EXE=sys.executable)
paths = [ROOT / 'tests/release097', ROOT / 'tests',
         ROOT / 'src/res/scripts/client', ROOT / 'server']
modules = [
    'test_port_0922_he_feedback',
    'test_096_contact_steering5', 'test_096_mobility_recovery',
    'test_096_ram_evidence6', 'test_096_features7.CrewServiceTests',
    'test_096_features7.EnrollmentWireTests', 'test_096_loading_transfer8',
    'test_recorder_process', 'test_096_replay_r4.PoseTests',
    'test_096_replay_r4.ReloadTests', 'test_096_replay_r4.ProfileTests',
    'test_replay_contract_r5', 'test_097_low_support', 'test_097_bridge_visibility',
]
failed = []
for name in modules:
    code = ('import sys,unittest;sys.path[:0]=%r;'
            'r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName(%r));'
            'sys.exit(not r.wasSuccessful())') % ([str(p) for p in paths], name)
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env)
    if result.returncode:
        failed.append(name)
if failed:
    raise SystemExit('Failed groups: ' + ', '.join(failed))
print('All isolated 0.9.7 regression groups passed.')
