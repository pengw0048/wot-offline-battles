from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
PORT_ROOT = ROOT
SOURCE = PORT_ROOT / 'native' / 'offline_worker_starter.c'
BINARY = PORT_ROOT / 'native' / 'offline_worker_starter.exe'
PLAYER_BATCH = PORT_ROOT / 'START_OFFLINE_0922.bat'
LAN_CLIENT_BATCH = PORT_ROOT / 'START_LAN_CLIENT_0922.bat'
WORKER_BATCH = PORT_ROOT / 'START_SIMULATION_WORKER_0922.bat'


class WorkerStarterTests(unittest.TestCase):
    def test_worker_uses_an_unswitched_private_desktop_and_original_client(self):
        source = SOURCE.read_text(encoding='utf-8')

        self.assertIn('CreateDesktopW(desktop_name', source)
        self.assertIn('startup.lpDesktop = full_desktop_name;', source)
        self.assertIn('L"WinSta0\\\\%s"', source)
        self.assertNotIn('SwitchDesktop(', source)
        self.assertNotIn('SetThreadDesktop(', source)
        self.assertIn('L"WorldOfTanks.exe"', source)
        self.assertIn(
            '--config engine_config.offline-worker.xml', source)
        self.assertNotIn('--preferences', source)
        self.assertIn('--logFilePrefix offline-worker-', source)
        self.assertLess(source.index('CREATE_SUSPENDED'),
                        source.index('AssignProcessToJobObject'))
        self.assertLess(source.index('AssignProcessToJobObject'),
                        source.index('ResumeThread'))
        self.assertIn('JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE', source)

    def test_worker_starts_only_in_worker_mode_and_waits_for_readiness(self):
        source = SOURCE.read_text(encoding='utf-8')

        self.assertIn('CreateMutexW(0, TRUE, WORKER_MUTEX_NAME)', source)
        self.assertIn('OFFLINE_LAN_0922_WORKER_READY_MARKER', source)
        self.assertIn('wait_for_worker_ready(process.hProcess, stop_event)',
                      source)
        wait_body = source.split(
            'static int wait_for_worker_ready', 1)[1].split(
                'static int launch_player', 1)[0]
        self.assertIn('worker_exited_before_ready', wait_body)
        self.assertIn('worker_process_exit_before_ready', source)
        self.assertNotIn(
            'failed worker no longer blocks a standalone player',
            wait_body.lower())
        self.assertIn(
            '--config engine_config.offline-player.xml', source)
        self.assertIn('--logFilePrefix offline-player-', source)
        self.assertIn(
            'SetEnvironmentVariableW(WORKER_MODE_ENV, PLAYER_MODE_VALUE)',
            source)
        self.assertIn('lstrcmpiW(command_line, PLAYER_MODE)', source)
        self.assertIn(
            'lstrcmpiW(command_line, PAIRED_PLAYER_MODE)', source)
        self.assertIn(
            'result = launch_player(game_path, TRUE, stop_event);', source)
        self.assertIn(
            'result = launch_player(game_path, FALSE, stop_event);', source)
        self.assertIn('TerminateJobObject(job, ERROR_PROCESS_ABORTED)', source)
        main = source.split('int WINAPI wWinMain', 1)[1]
        self.assertIn('wait_for_worker_ready(process.hProcess, stop_event)',
                      main)
        worker_path = main.split('if (!worker_only)', 1)[1]
        self.assertNotIn('launch_player(', worker_path)

    def test_worker_only_starter_never_creates_a_server(self):
        source = SOURCE.read_text(encoding='utf-8')
        main = source.split('int WINAPI wWinMain', 1)[1]

        self.assertIn('if (!worker_only)', main)
        self.assertIn('unsupported_mode', main)
        self.assertIn('CreateProcessW(game_path, child_command', main)
        self.assertNotIn('server_path', main)
        self.assertNotIn('server_process', main)
        self.assertIn('OFFLINE_LAN_0922_SERVER_HOST', source)
        self.assertIn('OFFLINE_LAN_0922_SERVER_PORT', source)
        self.assertNotIn('WOT_0922_SERVER_DATA', source)

    def test_lan_player_preserves_launcher_server_override(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]

        self.assertNotIn(
            'SetEnvironmentVariableW(SERVER_HOST_ENV, 0);', launch)
        self.assertNotIn(
            'SetEnvironmentVariableW(SERVER_PORT_ENV, 0);', launch)

    def test_visible_player_job_tracks_client_process_handoffs(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]

        self.assertIn('CreateJobObjectW(0, 0)', launch)
        self.assertIn(
            'AssignProcessToJobObject(player_job, process.hProcess)', launch)
        self.assertIn('JobObjectBasicAccountingInformation', launch)
        self.assertIn('accounting.ActiveProcesses == 0', launch)
        self.assertLess(launch.index('CREATE_SUSPENDED'),
                        launch.index('AssignProcessToJobObject'))
        self.assertLess(launch.index('AssignProcessToJobObject'),
                        launch.index('ResumeThread'))

    def test_optional_procdump_attaches_only_after_client_ready_boundaries(self):
        source = SOURCE.read_text(encoding='utf-8')
        capture = source.split(
            'static HANDLE start_procdump_configured', 1)[1].split(
                'static int starter_stop_event_name', 1)[0]
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]
        main = source.split('int WINAPI wWinMain', 1)[1]

        self.assertIn('WOT_OFFLINE_PROCDUMP_PATH', source)
        self.assertIn('WOT_OFFLINE_CRASH_DUMP_PATH', source)
        self.assertIn('WOT_OFFLINE_CRASH_DUMP_MODE', source)
        self.assertIn(
            '-accepteula %s -n 1 -e -t %lu', capture)
        self.assertIn('const WCHAR *dump_option = L"-mm"', capture)
        self.assertIn('dump_option = L"-ma"', capture)
        self.assertIn('CheckRemoteDebuggerPresent(', capture)
        self.assertIn('WaitForSingleObject(process.hProcess, 0)', capture)
        self.assertIn('procdump_exited_before_attach', capture)
        self.assertIn('procdump_attach_timeout', capture)

        player_assign = launch.index(
            'AssignProcessToJobObject(player_job, process.hProcess)')
        player_resume = launch.index(
            'ResumeThread(process.hThread)', player_assign)
        player_track = launch.index(
            'track_player_process(&tracker, process.dwProcessId, game_path)',
            player_resume)
        player_ready_attach = launch.index(
            'attach_ready_player_procdumps(', player_track)
        self.assertLess(player_assign, player_resume)
        self.assertLess(player_resume, player_track)
        self.assertLess(player_track, player_ready_attach)
        attach = source.split(
            'static int attach_ready_player_procdumps', 1)[1].split(
                'static int track_player_job_processes', 1)[0]
        self.assertLess(
            attach.index('GetFileAttributesW(ready_marker)'),
            attach.index('start_procdump_configured('))
        self.assertGreater(
            attach.index('remove_marker_path(ready_marker)'),
            attach.index('start_procdump_configured('))

        worker_assign = main.index(
            'AssignProcessToJobObject(job, process.hProcess)')
        worker_resume = main.index(
            'ResumeThread(process.hThread)', worker_assign)
        worker_ready = main.index(
            'ready_state = wait_for_worker_ready(', worker_resume)
        worker_capture = main.index(
            'procdump_process = start_procdump_configured(', worker_ready)
        worker_publish = main.index(
            'publish_ready_marker(g_ready_marker)', worker_capture)
        self.assertLess(worker_assign, worker_resume)
        self.assertLess(worker_resume, worker_ready)
        self.assertLess(worker_ready, worker_capture)
        self.assertLess(worker_capture, worker_publish)
        self.assertIn('OFFLINE_LAN_0922_WORKER_INTERNAL_READY_MARKER', source)
        self.assertIn('OFFLINE_LAN_0922_PLAYER_READY_MARKER', source)

    def test_starter_status_log_preserves_the_root_procdump_error(self):
        source = SOURCE.read_text(encoding='utf-8')
        log = source.split(
            'static void log_status', 1)[1].split(
                'static void clear_failure_log', 1)[0]

        self.assertIn('FILE_APPEND_DATA', log)
        self.assertIn('OPEN_ALWAYS', log)
        self.assertIn('FILE_SHARE_READ | FILE_SHARE_WRITE', log)
        self.assertNotIn('CREATE_ALWAYS', log)

    def test_starter_distinguishes_child_exits_from_win32_failures(self):
        source = SOURCE.read_text(encoding='utf-8')
        status_log = source.split(
            'static void log_status', 1)[1].split(
                'static void clear_failure_log', 1)[0]
        main = source.split('int WINAPI wWinMain', 1)[1]

        self.assertIn('"stage=%s %s=%lu\\r\\n"', status_log)
        self.assertIn(
            'log_status(stage, "win32_error", error_code);', status_log)
        self.assertIn(
            'log_status(stage, "exit_code", exit_code);', status_log)
        self.assertIn('log_process_exit(', main)
        self.assertIn('"worker_process_exit", child_exit_code', main)
        self.assertNotIn(
            'log_failure("worker_process_exit", child_exit_code)', main)

    def test_starter_bounds_procdump_completion_and_cancels_on_timeout(self):
        source = SOURCE.read_text(encoding='utf-8')
        wait = source.split(
            'static BOOL wait_for_procdump', 1)[1].split(
                'static void cancel_procdump_now', 1)[0]

        self.assertIn('PROCDUMP_FINISH_TIMEOUT_MS', wait)
        self.assertIn('start_procdump_cancel(', wait)
        self.assertIn('-accepteula -cancel %lu', source)
        self.assertIn('return !timed_out && completed;', wait)
        self.assertNotIn('INFINITE', wait)

    def test_procdump_processes_are_explicitly_hidden(self):
        source = SOURCE.read_text(encoding='utf-8')
        cancel = source.split(
            'static HANDLE start_procdump_cancel', 1)[1].split(
                'static BOOL wait_for_procdump', 1)[0]
        monitor = source.split(
            'static HANDLE start_procdump_configured', 1)[1].split(
                'static int starter_stop_event_name', 1)[0]

        for body in (cancel, monitor):
            self.assertIn('startup.dwFlags = STARTF_USESHOWWINDOW;', body)
            self.assertIn('startup.wShowWindow = SW_HIDE;', body)
            self.assertIn('CREATE_NO_WINDOW', body)

    def test_procdump_status_cannot_discard_a_complete_dump(self):
        source = SOURCE.read_text(encoding='utf-8')
        close = source.split(
            'static int close_finished_procdump', 1)[1].split(
                'static HANDLE start_procdump_cancel', 1)[0]
        finish = source.split(
            'static DWORD finish_player_tracker', 1)[1].split(
                'static int launch_player', 1)[0]

        self.assertIn('GetExitCodeProcess(', close)
        self.assertIn('*completed = TRUE;', close)
        self.assertNotIn('exit_code != 0', close)
        self.assertIn('complete_regular_dump_file(last->dump_path)', finish)
        self.assertIn('player_dump_missing', finish)

    def test_both_player_modes_track_only_their_job_handoffs(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]

        self.assertIn('PLAYER_HANDOFF_GRACE_MS', launch)
        self.assertIn('track_player_job_processes(', launch)
        self.assertIn('JobObjectBasicProcessIdList', source)
        self.assertIn('QueryFullProcessImageNameW(', source)
        self.assertNotIn('CreateToolhelp32Snapshot(', source)
        self.assertNotIn('track_external_player_processes(', source)
        self.assertNotIn('collect_game_processes(', source)
        self.assertNotIn('baseline_game_processes', source)
        self.assertIn(
            'result = launch_player(game_path, FALSE, stop_event);', source)
        self.assertIn(
            'result = launch_player(game_path, TRUE, stop_event);', source)

    def test_terminal_player_exit_controls_result_and_only_complete_dump_moves(self):
        source = SOURCE.read_text(encoding='utf-8')
        exits = source.split(
            'static int update_tracked_player_exits', 1)[1].split(
                'static DWORD active_tracked_player_count', 1)[0]
        finish = source.split(
            'static DWORD finish_player_tracker', 1)[1].split(
                'static int launch_player', 1)[0]

        self.assertIn('GetProcessTimes(', exits)
        self.assertIn('tracker->last_exit_index = (int)index;', exits)
        self.assertIn('result = last->exit_code;', finish)
        self.assertIn('last->dump_complete', finish)
        self.assertIn('complete_regular_dump_file(last->dump_path)', finish)
        self.assertIn('result != 0', finish)
        self.assertIn('MoveFileExW(', finish)
        self.assertIn('MOVEFILE_WRITE_THROUGH', finish)
        self.assertLess(
            finish.index('last->dump_complete'),
            finish.index('MoveFileExW('))

    def test_dump_monitors_use_only_fixed_slots_and_clean_them(self):
        source = SOURCE.read_text(encoding='utf-8')
        slots = source.split(
            'static int monitor_dump_path', 1)[1].split(
                'static int complete_regular_dump_file', 1)[0]

        self.assertIn('slot >= MAX_GAME_PROCESS_IDS', slots)
        self.assertIn('.monitor-%02lu.tmp.dmp', slots)
        self.assertIn(
            'slot = 0; slot < MAX_GAME_PROCESS_IDS; ++slot', slots)
        self.assertIn('cleanup_monitor_dump_slots(', source)
        self.assertNotIn('.%lu.tmp.dmp', slots)

    def test_normal_stop_cancels_monitors_before_terminating_targets(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]
        main = source.split('int WINAPI wWinMain', 1)[1]
        player_stop = launch.index('WaitForSingleObject(stop_event, 0)')
        player_cancel = launch.index(
            'cancel_player_procdumps(&tracker);', player_stop)
        player_terminate = launch.index(
            'TerminateJobObject(\n\t\t\t\t\tplayer_job, '
            'ERROR_PROCESS_ABORTED)',
            player_cancel)
        worker_stop = main.index('wait_state == WAIT_OBJECT_0 + 1')
        worker_recheck = main.index(
            'WaitForSingleObject(\n\t\t\t\t\tprocess.hProcess, 0)',
            worker_stop)
        worker_cancel = main.index(
            'cancel_procdump_now(&procdump_process,', worker_recheck)
        worker_terminate = main.index(
            'TerminateJobObject(job, ERROR_PROCESS_ABORTED);', worker_cancel)
        worker_cleanup = main.index('worker_cleanup:')
        cleanup_cancel = main.index(
            'cancel_procdump_now(&procdump_process,', worker_cleanup)
        cleanup_job_close = main.index('CloseHandle(job);', cleanup_cancel)

        self.assertIn('--stop-starter ', source)
        self.assertIn('OpenEventW(EVENT_MODIFY_STATE', source)
        self.assertIn('SetEvent(stop_event)', source)
        self.assertLess(player_cancel, player_terminate)
        self.assertLess(worker_recheck, worker_cancel)
        self.assertLess(worker_cancel, worker_terminate)
        self.assertLess(cleanup_cancel, cleanup_job_close)
        self.assertIn('return stopped ? 0 : result;', source)

    def test_player_stop_always_retires_job_and_preserves_observed_crash(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]
        stop = launch.split(
            'WaitForSingleObject(stop_event, 0) == WAIT_OBJECT_0', 1)[1]
        stop = stop.split('ZeroMemory(&accounting', 1)[0]

        self.assertIn(
            'preserved_crash_exit = latest_nonzero_player_exit(&tracker);',
            stop)
        self.assertIn('stopped = preserved_crash_exit < 0;', stop)
        self.assertIn(
            'TerminateJobObject(\n\t\t\t\t\tplayer_job, '
            'ERROR_PROCESS_ABORTED)', stop)
        self.assertIn('TerminateJobObject(player stop)', stop)
        self.assertNotIn('active_tracked_player_count', stop)
        self.assertIn(
            'if (stop_failed && preserved_crash_exit < 0)', launch)

    def test_duplicate_host_cannot_erase_the_live_worker_ready_marker(self):
        source = SOURCE.read_text(encoding='utf-8')
        main = source.split('int WINAPI wWinMain', 1)[1]

        mutex = main.index(
            'singleton = CreateMutexW(0, TRUE, WORKER_MUTEX_NAME);')
        duplicate = main.index(
            'if (GetLastError() == ERROR_ALREADY_EXISTS)', mutex)
        clear_marker = main.index('if (!remove_ready_markers())', duplicate)
        self.assertLess(mutex, duplicate)
        self.assertLess(duplicate, clear_marker)
        self.assertIn('goto worker_cleanup;', main[clear_marker:])

    def test_ready_marker_is_accepted_only_while_worker_is_alive(self):
        source = SOURCE.read_text(encoding='utf-8')
        wait_body = source.split(
            'static int wait_for_worker_ready', 1)[1].split(
                'static int launch_player', 1)[0]

        first_process_check = wait_body.index(
            'WaitForSingleObject(worker_process, 0)')
        marker_check = wait_body.index(
            'GetFileAttributesW(g_internal_ready_marker)')
        second_process_check = wait_body.index(
            'WaitForSingleObject(worker_process, 0)',
            first_process_check + 1)
        self.assertLess(first_process_check, marker_check)
        self.assertLess(marker_check, second_process_check)
        self.assertIn('worker_exited_after_ready', wait_body)
        self.assertNotIn('local_server_exited_before_worker_ready', wait_body)

    def test_lan_player_returns_before_any_worker_resource_is_created(self):
        source = SOURCE.read_text(encoding='utf-8')
        main = source.split('int WINAPI wWinMain', 1)[1]

        lan_dispatch = main.index(
            'if (lstrcmpiW(command_line, PLAYER_MODE) == 0)')
        lan_return = main.index('return result;', lan_dispatch)
        worker_mutex = main.index('CreateMutexW(', lan_return)
        worker_desktop = main.index('CreateDesktopW(', worker_mutex)
        worker_process = main.index(
            'CreateProcessW(game_path, child_command', worker_desktop)
        self.assertLess(lan_dispatch, lan_return)
        self.assertLess(lan_return, worker_mutex)
        self.assertLess(worker_mutex, worker_desktop)
        self.assertLess(worker_desktop, worker_process)

    def test_bat_files_only_dispatch_the_gui_starter(self):
        player = PLAYER_BATCH.read_text(encoding='utf-8')
        lan_client = LAN_CLIENT_BATCH.read_text(encoding='utf-8')
        worker = WORKER_BATCH.read_text(encoding='utf-8')

        player_starts = [line.strip() for line in player.splitlines()
                         if line.strip().startswith('start ""')]
        self.assertEqual([
            'start "" "%GAME_ROOT%offline_worker_starter.exe" --player',
        ], player_starts)
        self.assertIn(
            'start "" "%GAME_ROOT%offline_worker_starter.exe" --player',
            lan_client)
        self.assertIn(
            'start "" "%GAME_ROOT%offline_worker_starter.exe" --worker-only',
            worker)
        self.assertNotIn('powershell.exe', player.lower())
        self.assertNotIn('powershell.exe', lan_client.lower())
        self.assertNotIn('powershell.exe', worker.lower())
        self.assertNotIn('WorldOfTanks.exe --preferences', player)
        self.assertNotIn('WorldOfTanks.exe --preferences', worker)

    def test_built_starter_is_a_32_bit_windows_gui_binary(self):
        payload = BINARY.read_bytes()
        self.assertEqual(b'MZ', payload[:2])
        pe_offset = struct.unpack_from('<I', payload, 0x3c)[0]
        self.assertEqual(b'PE\0\0', payload[pe_offset:pe_offset + 4])
        self.assertEqual(0x14c, struct.unpack_from(
            '<H', payload, pe_offset + 4)[0])
        optional_offset = pe_offset + 24
        self.assertEqual(0x10b, struct.unpack_from(
            '<H', payload, optional_offset)[0])
        self.assertEqual(2, struct.unpack_from(
            '<H', payload, optional_offset + 68)[0])
        self.assertIn(b'CreateDesktopW', payload)
        self.assertIn(b'CreateProcessW', payload)
        self.assertIn(b'CheckRemoteDebuggerPresent', payload)
        self.assertNotIn(b'CreateToolhelp32Snapshot', payload)
        self.assertIn(b'QueryFullProcessImageNameW', payload)
        self.assertIn('--player'.encode('utf-16le'), payload)
        self.assertIn('--paired-player'.encode('utf-16le'), payload)
        self.assertIn('--worker-only'.encode('utf-16le'), payload)
        self.assertIn('--stop-starter '.encode('utf-16le'), payload)
        self.assertIn(
            'engine_config.offline-player.xml'.encode('utf-16le'), payload)
        self.assertIn(
            'engine_config.offline-worker.xml'.encode('utf-16le'), payload)
        self.assertNotIn('--preferences'.encode('utf-16le'), payload)
        self.assertIn('offline-worker.ready'.encode('utf-16le'), payload)
        self.assertIn(
            'offline-worker.internal-ready'.encode('utf-16le'), payload)
        self.assertIn(
            'offline-player-%lu.ready'.encode('utf-16le'), payload)
        self.assertNotIn(
            'WoT-0.9.22-LAN-Server.exe'.encode('utf-16le'), payload)
        self.assertNotIn(
            'WOT_0922_LOOPBACK_ONLY'.encode('utf-16le'), payload)
        self.assertNotIn('local_server_'.encode('utf-16le'), payload)
        self.assertNotIn('wait_for_local_server'.encode('utf-16le'), payload)
        self.assertNotIn('WOT_0922_SERVER_DATA'.encode('utf-16le'), payload)
        self.assertIn(b'player_mode', payload)
        self.assertIn(
            'WOT_OFFLINE_PROCDUMP_PATH'.encode('utf-16le'), payload)
        self.assertIn(
            'WOT_OFFLINE_CRASH_DUMP_PATH'.encode('utf-16le'), payload)
        self.assertIn(
            '.monitor-%02lu.tmp.dmp'.encode('utf-16le'), payload)
        self.assertIn('-cancel %lu'.encode('utf-16le'), payload)


if __name__ == '__main__':
    unittest.main()
