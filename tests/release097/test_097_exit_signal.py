from __future__ import print_function
import os,sys,types,imp,tempfile,shutil,struct,unittest
W=os.path.dirname(os.path.abspath(__file__))
ROOT=os.environ.get('WOT097_PYC',os.path.join(os.path.dirname(os.path.dirname(W)),'src','res','scripts','client','gui','mods','offline_lan_0922'))
pkg=types.ModuleType('exit_test');pkg.__path__=[ROOT];sys.modules['exit_test']=pkg
presentation=imp.load_source('exit_test.worker_presentation',os.path.join(ROOT,'worker_presentation.py'))
diag=imp.load_source('exit_test.runtime_diagnostics',os.path.join(ROOT,'runtime_diagnostics.py'))
class ExitSignal(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.mkdtemp(prefix='wot097-',dir=W);self.path=os.path.join(self.dir,'finish')
        self.env={presentation.PLAYER_FINISHED_MARKER_ENV:self.path,'OFFLINE_LAN_0922_CLIENT_MODE':'player'}
    def tearDown(self):
        diag.remove_exit_trace();sys.modules.pop('exit_test.offline_replay',None);shutil.rmtree(self.dir)
    def test_success_pid_and_atomic_cleanup(self):
        self.assertTrue(presentation.signal_player_finished(self.env))
        self.assertEqual(struct.unpack('<II',open(self.path,'rb').read()),(os.getpid(),0))
        self.assertFalse(os.path.exists(self.path+'.tmp'))
    def test_repeat_replaces_marker(self):
        presentation.signal_player_finished(self.env);presentation.signal_player_finished(self.env)
        self.assertEqual(len(open(self.path,'rb').read()),8)
    def test_worker_cannot_publish(self):
        self.env['OFFLINE_LAN_0922_CLIENT_MODE']='simulation_worker'
        self.assertFalse(presentation.signal_player_finished(self.env));self.assertFalse(os.path.exists(self.path))
    def test_missing_launch_marker_noop(self):
        self.assertFalse(presentation.signal_player_finished({}))
    def test_pending_recording_extends_budget(self):
        recorder=types.ModuleType('recorder');recorder.done=False
        replay=types.ModuleType('exit_test.offline_replay');replay._FINISHING=[recorder]
        sys.modules[replay.__name__]=replay
        presentation.signal_player_finished(self.env)
        self.assertEqual(struct.unpack('<II',open(self.path,'rb').read())[1],1)
        recorder.done=True;presentation.signal_player_finished(self.env)
        self.assertEqual(struct.unpack('<II',open(self.path,'rb').read())[1],0)
    def test_notification_is_after_successful_original_return(self):
        order=[];owner=types.ModuleType('owner')
        def original(value):order.append(value);return 73
        owner.fini=original
        self.assertTrue(diag._wrap(owner,'fini','game.fini',lambda:order.append('signal')))
        self.assertEqual(owner.fini('saved'),73);self.assertEqual(order,['saved','signal'])
        self.assertFalse(diag._wrap(owner,'fini','game.fini',lambda:None))
    def test_exception_not_announced_as_complete(self):
        calls=[];owner=types.ModuleType('owner')
        def original():raise ValueError('cleanup failed')
        owner.fini=original;diag._wrap(owner,'fini','game.fini',lambda:calls.append(1))
        self.assertRaises(ValueError,owner.fini);self.assertEqual(calls,[])
    def test_failed_notification_preserves_original_result(self):
        owner=types.ModuleType('owner');owner.fini=lambda:17
        def failed():raise IOError('read only')
        diag._wrap(owner,'fini','game.fini',failed);self.assertEqual(owner.fini(),17)
    def test_install_only_signals_game_fini(self):
        calls=[];oldgame=sys.modules.get('game');oldbw=sys.modules.get('BigWorld')
        game=types.ModuleType('game');game.fini=lambda:1
        bw=types.ModuleType('BigWorld');bw.quit=lambda:2
        sys.modules['game']=game;sys.modules['BigWorld']=bw
        oldsignal=presentation.signal_player_finished;presentation.signal_player_finished=lambda:calls.append('finished')
        try:
            diag.install_exit_trace();bw.quit();self.assertEqual(calls,[])
            game.fini();self.assertEqual(calls,['finished'])
        finally:
            presentation.signal_player_finished=oldsignal
            for name,value in [('game',oldgame),('BigWorld',oldbw)]:
                if value is None:sys.modules.pop(name,None)
                else:sys.modules[name]=value
if __name__=='__main__':unittest.main(verbosity=2)
