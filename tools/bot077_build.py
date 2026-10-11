from __future__ import print_function
"""Pinned upstream 0.7.7 Bot rollback, with an explicit no-yield comparison.

Radio production/relay and the current non-Bot boundary are retained. The
no-yield variant intentionally differs from upstream; it is never labelled an
exact upstream Bot. No new driving or recovery strategy is introduced.
"""
import ast
import contextlib
import difflib
import hashlib
import inspect
import json
import os
import subprocess
import sys
import types
import unittest

import full_bot080_build as common
ROOT = common.ROOT
HIST = '54603d6e68d345c45c9aaa5e6a2cd45200c5436b'
OLD = '5a7c20a124ab8ad76dc40db3012a31436ae6905b'
BASE = '889fd4076053fc4825a5a98c2ae6d7c38c1629fb'
PREFIX = common.PREFIX
BOT = PREFIX + 'bot_runtime.py'
SERVER = 'server/server_bot_ai.py'
PROOF = 'docs/testing/bot077-proof.json'
ROOTS = (PREFIX+'ai/', 'navgraphs/', 'foliage/', 'destructibles/')
FILES = (BOT, PREFIX+'bot_gunnery.py', PREFIX+'bot_state_codec.py',
         PREFIX+'prebaked_navigation.py', SERVER,
         'launcher/bot_lineup_profiles.py', 'launcher/bot_lineup_ui.py')
RADIO_METHODS = {'__init__', 'battle_start', '_append_human_observations',
                 '_contacts_for', '_pack_observations', '_update_once'}
RADIO_ADDED = {'_radio_identity', '_source_radio_range', '_configure_radio',
               '_renew_observer_spot', '_recipient_contact'}

def git(*args):
    return subprocess.check_output(['git']+list(args), cwd=ROOT)

def read(path):
    return common.read(os.path.join(ROOT,path))

def write(path,data):
    common.write(os.path.join(ROOT,path),data)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def methods(data):
    cls=next(n for n in ast.parse(data).body if isinstance(n,ast.ClassDef) and n.name=='BotRuntime')
    return {n.name:ast.dump(n,include_attributes=False) for n in cls.body if isinstance(n,ast.FunctionDef)}

def remove_yield(data):
    text=data.decode('utf8')
    cuts=[
        ('from gui.mods.offline_lan_0922.ai.traffic import TrafficCoordinator\n',1),
        ('        self._traffic_coordinator = TrafficCoordinator()\n',1),
        ('            self._traffic_coordinator = TrafficCoordinator()\n',2),
        ('        self._traffic_coordinator.forget(bot_id)\n',1),
        ("            and not driver_state.get('traffic_waiting', False)\n",1),
        ('        if owns_contacted_bot_ids and step is not None:\n            for bot_id in sorted(contacted_bot_ids):\n                self._record_traffic_wait_contact(bot_id, step)\n',1),
        ('        for bot_id in sorted(contacted_bot_ids):\n            self._record_traffic_wait_contact(bot_id, step)\n',1),
        ('                # Deliver once per render callback, after every bounded\n                # physical slice has contributed only its own contact time.\n                self._apply_traffic_wait_lease()\n',1),
        ("                command = timed_call(\n                    self._combat_diagnostics, 'bot.traffic',\n                    self._traffic_coordinator.adjust,\n                    state['id'], traffic_bodies[state['id']], command,\n                    decision_state['neighbours'], now, sample_clear)\n",1)]
    for fragment,count in cuts:
        lines=text.splitlines(True);part=fragment.splitlines(True)
        hits=[i for i in range(len(lines)-len(part)+1) if lines[i:i+len(part)]==part]
        assert len(hits)==count,(fragment,len(hits))
        for i in reversed(hits):del lines[i:i+len(part)]
        text=''.join(lines)
    cls=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name=='BotRuntime')
    lines=text.splitlines(True)
    removed=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('_record_traffic_wait_contact','_apply_traffic_wait_lease')]
    assert len(removed)==2
    for node in sorted(removed,key=lambda n:n.lineno,reverse=True):del lines[node.lineno-1:node.end_lineno]
    text=''.join(line for line in lines if line.strip()!='self._contact_lease_elapsed = {}')
    text=text.replace("        driver = getattr(self.adapter, 'driver', None)\n        driver_state = getattr(driver, 'states', {}).get(int(bot_id), {})\n        movement_intent = bool(\n","        movement_intent = bool(\n")
    text=text.replace("# Keep this decision until the next scheduled refresh. Friendly\n            # following uses contact physics; crossing yields have a fixed\n            # deadline and never renew LocalDriver's stuck timer.","# Keep the original driver decision until its scheduled refresh.\n            # No traffic coordinator changes it; physical contacts remain active.")
    for term in ('TrafficCoordinator','_traffic_coordinator','_record_traffic_wait_contact','_apply_traffic_wait_lease','_contact_lease_elapsed','traffic_waiting'):
        assert term not in text,term
    ast.parse(text)
    return text.encode('utf8')

def integrate(variant):
    assert variant in ('original','noyield')
    git('merge-base','--is-ancestor',BASE,'HEAD')
    names=git('ls-tree','-r','--name-only',HIST).decode().splitlines()
    selected=sorted(n for n in names if n in FILES or n.startswith(ROOTS))
    present=git('ls-tree','-r','--name-only','HEAD').decode().splitlines()
    for name in present:
        if name.startswith(ROOTS) and name not in selected:
            os.remove(os.path.join(ROOT,name))
    for name in selected:write(name,git('show',HIST+':'+name))
    before=git('show',OLD+':'+BOT).decode('utf8')
    after=git('show',BASE+':'+BOT).decode('utf8')
    patch=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+BOT,tofile='b/'+BOT))
    process=subprocess.Popen(['patch','-p1','--fuzz=0','--batch'],cwd=ROOT,stdin=subprocess.PIPE)
    assert process.communicate(patch.encode('utf8'))[0] is None
    assert process.returncode==0,'radio patch failed'
    backup=os.path.join(ROOT,BOT+'.orig')
    if os.path.isfile(backup):os.remove(backup)
    left=methods(git('show',HIST+':'+BOT));right=methods(read(BOT))
    assert set(n for n in left if left[n]!=right.get(n))==RADIO_METHODS
    assert set(right)-set(left)==RADIO_ADDED
    assert git('show',HIST+':'+SERVER)==git('show',OLD+':'+SERVER)
    write(SERVER,git('show',BASE+':'+SERVER))
    if variant=='noyield':
        write(BOT,remove_yield(read(BOT)))
        os.remove(os.path.join(ROOT,PREFIX+'ai/traffic.py'))
    exact={};exceptions={};removed=[]
    for name in selected:
        expected=sha(git('show',HIST+':'+name))
        if not os.path.exists(os.path.join(ROOT,name)):
            removed.append(name);continue
        actual=sha(read(name))
        if actual==expected:exact[name]=actual
        else:exceptions[name]={'historical_sha256':expected,'actual_sha256':actual}
    changed=[]
    preserved={}
    for name, digest in common.runtime_hashes().items():
        old=git('show',BASE+':'+name).replace(b'\r\n',b'\n')
        if sha(old)!=digest:changed.append(name)
        else:preserved[name]=digest
    proof={'historical_source':HIST,'base':BASE,'variant':variant,
           'exact_historical_sha256':exact,'intentional_exceptions':exceptions,
           'removed_paths':removed,'changed_runtime':sorted(changed),
           'runtime_sha256_lf':common.runtime_hashes(),'preserved_runtime_sha256_lf':preserved,
           'native_gameplay_tested':False,
           'scope':'Upstream 0.7.7 Bot and original maps; current radio and non-Bot boundary retained. noyield also removes proactive traffic and post-contact waiting ownership; geometric clearance and physical contacts remain.'}
    common.save(os.path.join(ROOT,PROOF),proof)
    subprocess.check_call(['git','add','-A','--']+list(ROOTS)+list(FILES)+[PROOF],cwd=ROOT)
    audit()

def audit():
    proof=json.loads(read(PROOF))
    assert proof['historical_source']==HIST and proof['base']==BASE
    for name,digest in proof['exact_historical_sha256'].items():assert sha(read(name))==digest,name
    for name,record in proof['intentional_exceptions'].items():assert sha(read(name))==record['actual_sha256'],name
    for name in proof['removed_paths']:assert not os.path.exists(os.path.join(ROOT,name)),name
    assert common.runtime_hashes()==proof['runtime_sha256_lf']
    source=read(BOT)
    if proof['variant']=='noyield':
        for term in (b'TrafficCoordinator',b'_apply_traffic_wait_lease',b'_record_traffic_wait_contact'):
            assert term not in source,term
    # Retained caller must accept the real historical constructor and roster API.
    cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='BotRuntime')
    init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
    args=set(getattr(n,'arg',getattr(n,'id','')) for n in init.args.args)
    tree=ast.parse(read(PREFIX+'battle_runtime.py'))
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='BotRuntime']
    assert len(calls)==1 and set(n.arg for n in calls[0].keywords).issubset(args)
    print('PASS upstream077 identity, variant=%s, all runtime hashes, exact maps and constructor.'%proof['variant'])
    return proof

def source_tests():
    proof=audit()
    sys.path[:0]=[os.path.join(ROOT,p) for p in ('tests','server','tools')]
    def load(name):
        path='tests/'+name+'.py';module=types.ModuleType('upstream077_'+name)
        module.__file__=os.path.join(ROOT,path)
        exec(compile(git('show',HIST+':'+path),module.__file__,'exec'),module.__dict__)
        return module
    ai=load('test_port_0922_ai');runtime=load('test_port_0922_bot_runtime')
    fixture=runtime._effective_params_snapshot
    def snapshot(*args,**kwargs):
        row=fixture(*args,**kwargs);row['physics']['rotationIsAroundCenter']=True;return row
    runtime._effective_params_snapshot=snapshot
    tree=ast.parse(inspect.getsource(common.source_tests))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='names' for t in n.targets))
    names=ast.literal_eval(node.value)
    # These two assert the policies intentionally removed only in noyield.
    excluded={'test_high_fps_contact_lease_pays_the_consumed_physical_time',
              'test_runtime_caches_one_traffic_decision_with_the_planner_command'} if proof['variant']=='noyield' else set()
    print('Intentionally removed-policy tests excluded:',sorted(excluded))
    ai_suite=unittest.defaultTestLoader.loadTestsFromModule(ai)
    if proof['variant']=='noyield':
        def flatten(suite):
            for test in suite:
                if isinstance(test,unittest.TestSuite):
                    for item in flatten(test):yield item
                else:yield test
        removed_ai='test_runtime_hold_and_traffic_wait_pause_macro_progress_lease'
        ai_suite=unittest.TestSuite(t for t in flatten(ai_suite) if t._testMethodName!=removed_ai)
        print('Replaced mixed hold/traffic test:',removed_ai)
    suite=unittest.TestSuite([ai_suite,
        unittest.TestSuite(runtime.BotRuntimeTests(n) for n in names if n not in excluded)])
    if proof['variant']=='original':suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(load('test_port_0922_traffic')))
    else:
        class NoYieldProductionTests(runtime.BotRuntimeTests):
            def test_driver_command_is_not_rewritten_by_traffic(self):
                self.runtime.battle_start(self.start)
                self.assertFalse(hasattr(self.runtime,'_traffic_coordinator'))
                self.assertFalse(hasattr(self.runtime,'_apply_traffic_wait_lease'))
                self.runtime.update(.04,1.0)
                self.runtime.update(.04,1.04)
                cached=self.runtime._decision_cache[11][3]
                self.assertNotIn('traffic_mode',cached)
                self.assertEqual(1.0,cached['throttle'])
                self.assertEqual(1,len(self.adapters[0].calls))
                self.runtime.battle_start(dict(self.start,round_id=2))
                self.assertFalse(hasattr(self.runtime,'_traffic_coordinator'))
        suite.addTest(NoYieldProductionTests('test_driver_command_is_not_rewritten_by_traffic'))
        class NoYieldIntentTests(ai.BotAiPortTests):
            def test_tactical_hold_remains_but_stale_traffic_cannot_freeze_progress(self):
                from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
                from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
                from gui.mods.offline_lan_0922.ai.navigation import TerrainNavigator
                rt=BotRuntime(1);rt.adapter=BotAdapter('04_himmelsdorf',1)
                rt.navigator=TerrainNavigator(lambda *unused:None,baked_graph=self._baked_graph(7,7))
                rt.states={i:{'id':i,'team':1} for i in (42,43,44)}
                current=(14.,0.,32.);goal=(26.,0.,32.);near=(15.,0.,32.)
                hold={'combat_mode':'engage','target_id':92,'throttle_override':0.}
                move={'combat_mode':'engage','target_id':93}
                rt.adapter.driver._state(43,1,current)['traffic_waiting']=True
                for step in range(64):
                    now=1.+step*.25
                    rt._navigation_target(42,current,goal,hold,{'now':now,'speed':0.})
                    rt._navigation_target(43,current,goal,move,{'now':now,'speed':0.})
                    arrived=rt._navigation_target(44,current,near,move,{'now':now,'speed':0.})
                self.assertEqual(0,rt.navigator.bot_direct_progress[42]['replans'])
                self.assertGreater(rt.navigator.bot_direct_progress[43]['replans'],0)
                self.assertEqual(near,arrived)
                self.assertEqual(0,rt.navigator.bot_direct_progress[44]['replans'])
        suite.addTest(NoYieldIntentTests('test_tactical_hold_remains_but_stale_traffic_cannot_freeze_progress'))
    with open(os.devnull,'w') as noise,contextlib.redirect_stdout(noise):
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():raise SystemExit(1)

def bind_common():
    common.HIST=HIST;common.BASE=BASE;common.PROOF=PROOF;common.audit=audit

if __name__=='__main__':
    action=sys.argv[1];bind_common()
    if action=='integrate':integrate(sys.argv[2])
    elif action=='audit':audit()
    elif action=='source-tests':source_tests()
    elif action=='bytecode':common.bytecode()
    elif action=='distribution':common.distribution(sys.argv[2])
    else:raise SystemExit('Unknown action')
