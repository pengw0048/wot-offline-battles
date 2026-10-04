"""Replay entry preflight, mode isolation and true single-visible session plan."""
import copy
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import replay_launch
import core
import wot_launcher
from test_launcher_window import WindowTest


def write_replay(path, n=2, rows=None, header=None):
    h = header or dict(magic='WOT_OFFLINE_REPLAY', schema=1, client='0.9.22.0.1-cn-1513',
        welcome=dict(type='welcome', player_id=1), config=dict(vehicle='ussr:R11_MS-1'),
        start=dict(type='battle_start', round_id=1,map='01_karelia',
            players=[dict(id=1)],bots=[dict(id=i+2) for i in range(n)]),
        snapshot=dict(type='snapshot', round_id=1,players=[dict(id=1)],
            bots=[dict(id=i+2,alive=True,x=3.,y=0.,z=5.) for i in range(n)]))
    body=rows if rows is not None else [dict(type='local',t=.1,data={'position':[1.,2.,3.]}),
        dict(type='wire',t=.2,data={'type':'snapshot'}),dict(type='end',t=.3,records=2)]
    with gzip.open(str(path),'wb') as f:
        for row in [dict(type='header',data=h)]+body:
            f.write((json.dumps(row)+'\n').encode('utf8'))
    return str(path)

class PreflightTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        self.path=write_replay(Path(d.name)/'中文 replay.wotlanreplay',29)
    def test_complete_29_bot_chinese_path(self):
        r=replay_launch.validate(self.path)
        self.assertEqual(29,r['bots']);self.assertEqual(2,r['records']);self.assertEqual(.3,r['duration'])
    def test_missing_foot_rejected(self):
        write_replay(self.path,rows=[dict(type='local',t=1.,data={})])
        with self.assertRaises(ValueError):replay_launch.validate(self.path)
    def test_count_mismatch_rejected(self):
        write_replay(self.path,rows=[dict(type='end',t=1.,records=5)])
        with self.assertRaises(ValueError):replay_launch.validate(self.path)
    def test_timestamp_regression_rejected(self):
        write_replay(self.path,rows=[dict(type='local',t=2.,data={}),dict(type='local',t=1.,data={}),dict(type='end',t=2.,records=2)])
        with self.assertRaises(ValueError):replay_launch.validate(self.path)
    def test_native_extension_rejected(self):
        with self.assertRaises(ValueError):replay_launch.checked_path(self.path.replace('.wotlanreplay','.wotreplay'))
    def test_no_rewards_in_stream(self):
        write_replay(self.path,rows=[dict(type='wire',t=1.,data={'type':'battle_receipt'}),dict(type='end',t=2.,records=1)])
        with self.assertRaises(ValueError):replay_launch.validate(self.path)
    def test_corrupt_gzip(self):
        b=bytearray(Path(self.path).read_bytes());b[-8]^=1;Path(self.path).write_bytes(b)
        with self.assertRaises((ValueError, OSError, EOFError)):replay_launch.validate(self.path)
    def test_user_can_cancel_before_game_start(self):
        with self.assertRaisesRegex(ValueError,'cancelled'):replay_launch.validate(self.path,cancelled=lambda:True)
    def test_environment_does_not_leak_back_to_normal_play(self):
        original=dict(OTHER='kept',WOT_OFFLINE_REPLAY_FILE='old',WOT_OFFLINE_REPLAY_AUTOSTART='1')
        normal=replay_launch.child_environment(original)
        self.assertEqual({'OTHER':'kept'},normal)
        replay=replay_launch.child_environment(original,self.path)
        self.assertEqual(self.path,replay[replay_launch.FILE_ENV]);self.assertEqual('1',replay[replay_launch.AUTO_ENV])
        self.assertEqual('old',original[replay_launch.FILE_ENV])

class WindowReplayTests(unittest.TestCase):
    setUp=WindowTest.setUp
    _game=WindowTest._game
    _log_text=WindowTest._log_text
    def test_dedicated_tab_and_button(self):
        w=self.window
        self.assertEqual('Replay',w.battle_tabs.tab(w.replay_panel)['text'])
        self.assertEqual(w._start_replay,w.replay_start_button.cget('command'))
        w.select_replay('test.wotlanreplay')
        self.assertIs(w.replay_panel,w.battle_tabs.select())
        self.assertIs(w.start_button,w.replay_start_button)
    def test_button_uses_replay_plan_not_join_plan(self):
        game=self._game();path=write_replay(Path(self.settings_dir)/'test.wotlanreplay')
        w=self.window;w.mode.set(core.MODE_JOIN);w.select_replay(path)
        with mock.patch('wot_launcher.threading.Thread') as th:
            w._start_replay()
        session=th.call_args.kwargs['args'][1]
        self.assertEqual(path,session['replay_path']);self.assertFalse(session['needs_server'])
        self.assertEqual(core.MODE_SINGLE,session['mode']);self.assertEqual([],session['bot_lineup'])
        self.assertEqual(path,w._active_replay_path)
    def test_live_room_refused_before_install(self):
        w=self.window
        with mock.patch.object(w,'_server_is_running',return_value=True),mock.patch.object(w,'_start') as start:
            w._start_replay()
        start.assert_not_called();self.assertIn('close the running LAN room',self._log_text())
    def test_invalid_file_does_not_start_any_session(self):
        with mock.patch.object(self.window,'_start') as start:self.window._start_replay()
        start.assert_not_called()
    def test_session_runs_only_visible_game(self):
        w=self.window;game=self._game();path=write_replay(Path(self.settings_dir)/'test.wotlanreplay')
        session=core.plan_session(core.inspect_game_root(game),core.MODE_SINGLE,'')
        session.update(replay_path=path,needs_server=False)
        with mock.patch('core.install_client_mod',return_value=[]), \
             mock.patch('core.write_settings',return_value=[]), \
             mock.patch('core.ensure_0_9_22_preferences_isolation',return_value='prefs'), \
             mock.patch('wot_launcher.error_reports.begin_session',return_value=None), \
             mock.patch('wot_launcher.vehicle_overlays.prepare_vehicle_profile',return_value={'profile':None,'installedMembers':0}), \
             mock.patch.object(w,'_enable_crash_capture'), \
             mock.patch.object(w,'_restore_original_vehicle_data'), \
             mock.patch.object(w,'_start_server') as server, \
             mock.patch.object(w,'_start_worker') as worker, \
             mock.patch.object(w,'_run_game',return_value=False) as gamecall:
            w._run_session(game,session,'Player')
        server.assert_not_called();worker.assert_not_called()
        self.assertFalse(gamecall.call_args.kwargs['paired_worker'])
        self.assertEqual(path,gamecall.call_args.kwargs['replay_path'])
        self.assertIn('server=False worker=False',self._log_text())

if __name__=='__main__':unittest.main()
