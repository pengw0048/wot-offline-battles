"""Apply only the reviewed radio integration, never later driving changes."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = 'c041bdddd02d7a6ca6240475d29b2fc86501ba51'
HIST_RADIO = 'ce44acf5a2aaafc405e1d66e556efe94380cf198'
BOT = 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py'
SERVER = 'server/server_bot_ai.py'
BEFORE = {BOT: '759bfc37f19e6b749a6e920031d8f5d40fedec14e3eb16caddd3ccca045a4731', SERVER: 'b7d3297a3ca19746c399f0b9f69a93ac3eca804af39f8d8fea8b3dd53aac3409'}
AFTER = {BOT: '1e66a7965c8e8fdb1fcd2847c17c4c899a5cf7c7505d9a455005306b42a55d23', SERVER: '3fc17dfff672570b7bfc37b47131846b9966f559179b55c86ce9f231ff4725e3'}

def git(*args):
    return subprocess.check_output(['git'] + list(args), cwd=str(ROOT))

def digest(data):
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()

def methods(data, name):
    tree = ast.parse(data)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    return {n.name: ast.dump(n, include_attributes=False)
            for n in cls.body if isinstance(n, ast.FunctionDef)}

subprocess.check_call(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=str(ROOT))
for path, expected in BEFORE.items():
    assert digest(git('show', BASE + ':' + path)) == expected, path
    assert digest((ROOT / path).read_bytes()) in (expected, AFTER[path]), path
if digest((ROOT / BOT).read_bytes()) == BEFORE[BOT]:
    original = git('show', HIST_RADIO, '--', BOT).decode('utf8')
    header = original[original.index('diff --git'):original.index('@@ ')]
    hunks = re.split(r'(?=^@@ )', original[original.index('@@ '):], flags=re.M)
    selected = [h for h in hunks if h and not any(n in h.splitlines()[0]
                for n in ('-11491,', '-11689,', '-11758,'))]
    assert len(selected) == 14
    # Excluded hunks introduce the second traffic brake and motion recovery.
    subprocess.run(['patch', '-p1', '--fuzz=0'], cwd=str(ROOT),
                   input=(header + ''.join(selected)).encode('utf8'), check=True)
    backup = ROOT / (BOT + '.orig')
    if backup.exists():
        backup.unlink()
if digest((ROOT / SERVER).read_bytes()) == BEFORE[SERVER]:
    p = ROOT / SERVER
    text = p.read_text()
    old = '                    accepted_visibility.append(accepted_contact)\n'
    new = '''                    # Preserve the validated recipient schema even with the
                    # historical tactical policy. Do not turn radio leases
                    # back into a team-wide or self-spot-only presentation.
                    if "radio_recipients" in raw:
                        accepted_contact["radio_recipients"] = [
                            dict(row) for row in raw["radio_recipients"]]
                    accepted_visibility.append(accepted_contact)
'''
    assert text.count(old) == 1
    text = text.replace(old, new)
    old = '''                        "threatened_bot_ids": [],
                    })
        return accepted
'''
    new = '''                        "threatened_bot_ids": [],
                    })
                    if "radio_recipients" in raw:
                        accepted_visibility[-1]["radio_recipients"] = []
        return accepted
'''
    assert text.count(old) == 1
    p.write_text(text.replace(old, new))
for path, expected in AFTER.items():
    assert digest((ROOT / path).read_bytes()) == expected, path

# Every other existing BotRuntime method must retain the tested baseline AST.
a = methods(git('show', BASE + ':' + BOT), 'BotRuntime')
b = methods((ROOT / BOT).read_bytes(), 'BotRuntime')
allowed = {'__init__', 'battle_start', '_append_human_observations',
           '_contacts_for', '_pack_observations', '_update_once'}
assert {n for n in a if a[n] != b.get(n)} == allowed
assert set(b) - set(a) == {'_radio_identity', '_source_radio_range',
                         '_configure_radio', '_renew_observer_spot', '_recipient_contact'}
a = methods(git('show', BASE + ':' + SERVER), 'BotPlanner')
b = methods((ROOT / SERVER).read_bytes(), 'BotPlanner')
assert {n for n in a if a[n] != b.get(n)} == {'report_contacts'}
assert set(a) == set(b)
subprocess.check_call(['git', 'diff', '--exit-code', BASE, '--',
    'navgraphs', 'foliage', 'destructibles',
    'src/res/scripts/client/gui/mods/offline_lan_0922/ai',
    'src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py'], cwd=str(ROOT))

p = ROOT / 'docs/testing/full-bot080-proof.json'
proof = json.loads(p.read_text())
for path in (BOT, SERVER):
    old_hash = proof['exact_historical_sha256'].pop(path, BEFORE[path])
    assert old_hash == BEFORE[path]
proof['runtime_sha256_lf'][BOT] = AFTER[BOT]
proof['radio_visibility_fix'] = {
    'baseline': BASE, 'scope': 'Restore radio observation production and relay; retain old Bot driving, tactical policy and original maps.',
    'intentional_exceptions': {path: {'historical_sha256_lf': BEFORE[path],
        'fixed_sha256_lf': AFTER[path]} for path in (BOT, SERVER)}}
p.write_text(json.dumps(proof, sort_keys=True, indent=2) + '\n')
# Keep the integrity gate explicit: modified historical files are exceptions,
# not relabelled as byte-identical v0.8.0 files.
p = ROOT / 'tools/full_bot080_build.py'
text = p.read_text()
anchor = "    assert runtime_hashes() == proof['runtime_sha256_lf']\n"
extra = '''    for n, record in proof.get('radio_visibility_fix', {}).get('intentional_exceptions', {}).items():
        assert sha(read(os.path.join(ROOT, n)).replace(b'\\r\\n', b'\\n')) == record['fixed_sha256_lf'], n
'''
if extra not in text:
    assert text.count(anchor) == 1
    p.write_text(text.replace(anchor, anchor + extra))
print('PASS pinned radio-only changes; other Bot methods, AI directory and old maps unchanged.')
