"""Optional coarse CPU attribution for the isolated workload, Python 2/3.

Exclusive time subtracts only other selected scopes, not every called
function. This observer is separate from the uninstrumented primary timing.
"""
import importlib
import time

CLOCK = time.process_time if hasattr(time, 'process_time') else time.clock


class Recorder(object):
    def __init__(self, backend=None, runtime=None):
        self.rows = {}
        self.stack = []
        self.patches = []
        prefix = 'gui.mods.offline_lan_0922.'
        bot = importlib.import_module(prefix + 'bot_runtime')
        nav = importlib.import_module(prefix + 'ai.navigation')
        driver = importlib.import_module(prefix + 'ai.adapter')
        battle = importlib.import_module(prefix + 'battle_runtime')
        world = importlib.import_module(prefix + 'world_collision')
        specs = [
            (bot.BotRuntime, '_update_once', 'bot.slice'),
            (bot.BotRuntime, '_contacts_for', 'contacts'),
            (nav.TerrainNavigator, '_advance_searches', 'astar.batch'),
            (nav.TerrainGrid, 'begin_plan', 'astar.request'),
            # The adapter already holds a bound navigation callback. Measure
            # its containing driver call, including route work outside A*.
            (driver.BotAdapter, 'decide_with_order', 'driving.and.route'),
            (battle.BattleRuntime, '_resolve_bot_motion', 'motion.collision'),
            (world, '_check_horizontal_collision', 'motion.world'),
            (bot.BotRuntime, '_cadenced_ballistic_solution', 'ballistics'),
            (bot.BotRuntime, '_update_gun_aim', 'gun.aim'),
            (bot.BotRuntime, '_service_shot_lane_work', 'shot.lanes'),
            (bot.bot_state_codec, 'encode_row', 'publication.encoding'),
            (bot.vehicle_physics, 'longitudinal_step', 'motion.longitudinal'),
            (bot.vehicle_physics, 'traverse_step', 'motion.traverse'),
        ]
        if backend is not None:
            specs.append((backend.module, 'dispatch', 'native.dispatch'))
        for owner, name, label in specs:
            if (owner is bot.BotRuntime and runtime is not None and
                    name in runtime.__dict__):
                owner = runtime
            self.wrap(owner, name, label)

    def wrap(self, owner, name, label):
        original = owner.__dict__[name]
        row = self.rows.setdefault(label, {
            'calls': 0, 'inclusive_cpu_seconds': 0.0,
            'exclusive_cpu_seconds': 0.0})
        recorder = self

        def measured(*args, **kwargs):
            item = [CLOCK(), 0.0]
            recorder.stack.append(item)
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = CLOCK() - item[0]
                recorder.stack.pop()
                row['calls'] += 1
                row['inclusive_cpu_seconds'] += elapsed
                row['exclusive_cpu_seconds'] += elapsed - item[1]
                if recorder.stack:
                    recorder.stack[-1][1] += elapsed

        setattr(owner, name, measured)
        self.patches.append((owner, name, original, measured))

    def close(self):
        for owner, name, original, measured in reversed(self.patches):
            if owner.__dict__[name] is measured:
                setattr(owner, name, original)
        self.patches = []
