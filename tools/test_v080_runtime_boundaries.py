from pathlib import Path
import contextlib, os, subprocess, sys, types, unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'server'),str(ROOT/'tools')]
if len(sys.argv)>1 and sys.argv[1]=='historical':
    source=subprocess.check_output(['git','show','5a7c20a124ab8ad76dc40db3012a31436ae6905b:tests/test_port_0922_ai.py'],cwd=str(ROOT))
    module=types.ModuleType('v080_ai_tests');module.__file__=str(ROOT/'tests/test_port_0922_ai.py')
    exec(compile(source,module.__file__,'exec'),module.__dict__)
    suite=unittest.defaultTestLoader.loadTestsFromModule(module)
else:
    names=['test_decision_and_copied_motion_reuse_installed_physics', 'test_direction_probe_receives_speed_and_descriptor_contract', 'test_baked_planner_clear_cannot_bypass_native_selected_wall', 'test_reverse_final_world_receipt_receives_exact_travel_heading', 'test_hard_final_world_receipt_blocks_the_selected_motion', 'test_bot_soft_motion_contact_preserves_speed_without_moving', 'test_realised_hard_contact_invalidates_cached_command_and_probe', 'test_bot_cap_crush_keeps_real_speed_then_moves_next_tick', 'test_bot_drowning_requires_ten_continuous_seconds_and_publishes_death', 'test_bot_water_uses_hull_top_pose_and_recovery', 'test_bot_overturn_matches_ignore_recovery_and_death_law', 'test_bot_drive_uses_contacted_plane_instead_of_corridor_grade', 'test_bot_bridges_a_trench_narrower_than_its_chassis', 'test_bot_still_falls_off_a_cliff_edge_with_one_supported_end', 'test_hydraulic_bot_height_and_attitude_share_uneven_contact_plane', 'test_initial_and_restored_manifests_share_physics_installation', 'test_injected_baked_graph_replaces_runtime_grid_and_passes_routes', 'test_worker_stall_refreshes_control_once_and_consumes_all_elapsed', 'test_worker_low_fps_reuses_valid_drive_and_moves_continuously', 'test_worker_four_fps_keeps_turning_drive_active_between_plans', 'test_reverse_recovery_uses_driver_turn_sign_not_target_bearing', 'test_driver_proportional_turn_is_not_collapsed_to_keyboard_sign', 'test_limited_traverse_tank_turns_hull_before_advancing_or_firing', 'test_target_solution_cache_invalidates_and_burst_forces_freshness', 'test_the_manifest_publishes_each_bot_gunnery_tier', 'test_bot_physics_uses_plain_default_crew_factors', 'test_high_fps_contact_lease_pays_the_consumed_physical_time', 'test_baked_planner_ranking_never_replaces_selected_native_gate', 'test_traffic_wait_does_not_enter_reverse_recovery']
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName('test_port_0922_bot_runtime.BotRuntimeTests.'+name) for name in names)
with open(os.devnull,'w') as noise,contextlib.redirect_stdout(noise):
    result=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
