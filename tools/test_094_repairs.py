"""Targeted contracts; deliberately not a full-current-suite pass claim."""
import ast
import contextlib
import importlib
import inspect
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in ('tests', 'server', 'tools'):
    sys.path.insert(0, str(ROOT / directory))

MODULES = (
 'test_port_0922_094_repair', 'test_port_0922_bot_tier_modes',
 'test_port_0922_bot_lineup_integration',
 'test_bot080_radio_integration', 'test_port_0922_radio_integration',
 'test_port_0922_radio_server', 'test_port_0922_radio_routes',
 'test_port_0922_tactical_radio', 'test_port_0922_spotting',
 'test_port_0922_spotting_radio', 'test_port_0922_server_bot_ai',
 'test_port_0922_artillery_arc_queue', 'test_port_0922_artillery_controller',
 'test_port_0922_tank_collision', 'test_port_0922_tank_contact_ledger',
 'test_port_0922_rotation_contact_runtime', 'test_port_0922_bot_state_codec',
 'test_port_0922_account_rpc', 'test_port_0922_economy',
 'test_port_0922_economy_payloads', 'test_port_0922_economy_regressions',
 'test_port_0922_garage', 'test_port_0922_offline_services',
 'test_port_0922_offline_services_ui', 'test_port_0922_effective_params',
 'test_port_0922_world_collision', 'test_port_0922_vehicle_physics',
 'test_port_0922', 'test_bot080_pinned_startup')


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main():
    suite = unittest.TestSuite()
    excluded = ('test_port_0922_bot_lineup_integration.BotLineupIntegrationTests.'
                'test_launcher_and_server_share_the_mod_exclusion_rule')
    print('Known baseline failure excluded, unchanged by this repair:', excluded,
          '(expects a removed retired_vehicles launcher alias)', flush=True)
    for name in MODULES:
        suite.addTests(t for t in flatten(unittest.defaultTestLoader.loadTestsFromName(name))
                       if t.id() != excluded)
    import test_port_0922_bot_runtime as runtime
    names = {name for name in dir(runtime.BotRuntimeTests)
             if name.startswith('test_') and any(part in name for part in (
                'spg_', 'artillery_', 'current_human_contact', 'human_contact_momentum',
                'exactly_coincident', 'overlapping_bots', 'contact_broadphase', 'bot_push_decay'))}
    import full_bot080_build as old
    tree = ast.parse(inspect.getsource(old.source_tests))
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
                      any(isinstance(t, ast.Name) and t.id == 'names' for t in n.targets))
    names.update(ast.literal_eval(assignment.value))
    # Current test expects the later deferred-probe policy, whereas the pinned
    # original077 runtime intentionally holds its prior command. Reproduced
    # against the clean baseline; do not restore unrelated motion behavior.
    names.discard('test_unavailable_motion_probe_holds_last_drive_command')
    print('Also excluded unchanged baseline deferred-probe expectation; '
          'original077 hold-command policy retained.', flush=True)
    suite.addTests(runtime.BotRuntimeTests(name) for name in sorted(names))
    from test_port_0922_contact_dynamics import GroundContactTests
    for name in ('test_turning_a_pinned_hull_spends_mass_and_engine_power_for_either_owner',
                 'test_turn_reaction_is_reciprocal_and_cannot_send_multiple_full_torque_budgets',
                 'test_clear_recovery_arc_is_pruned_without_losing_an_interior_contact'):
        suite.addTest(GroundContactTests(name))
    # The remaining contact_dynamics cases assert the later AI escape policy,
    # which the maintainer intentionally reverted. They are not physics tests.
    with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())
