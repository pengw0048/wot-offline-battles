"""What quality preset the hidden worker is actually running.

Both 2026-09-09/10 worker crashes logged `MemoryCriticalController` lowering
quality minutes before dying, and neither log said what the preset was before
or after.  The tuple shape asserted here is the one #1513's own
`MemoryCriticalController.__call__` and `GraphicsPresets.setSelectedOption`
index: name, selected option index, option list.
"""

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import graphics_probe


def _entries():
    # Shaped exactly as the stock client indexes it: t[0] name, t[1] selected
    # index, t[2] the option list.  Real option rows are
    # [label, supported, requiresDeferred]; only their count is read here.
    return [
        ('TEXTURE_QUALITY', 1, [['HIGH', True, False], ['LOW', True, False],
                                ['OFF', True, False]]),
        ('TERRAIN_QUALITY', 3, [['a', True, False], ['b', True, False],
                                ['c', True, False], ['d', True, False]]),
        ('FLORA_QUALITY', 0, [['MAX', True, False], ['MIN', True, False]]),
        ('RENDER_PIPELINE', 0, [['DEFERRED', True, False],
                                ['FORWARD', True, False]]),
    ]


class GraphicsProbeTest(unittest.TestCase):
    def setUp(self):
        self._reader = graphics_probe._bigworld
        self.addCleanup(setattr, graphics_probe, '_bigworld', self._reader)

    def _install(self, entries):
        class _BigWorld(object):
            @staticmethod
            def graphicsSettings():
                if isinstance(entries, Exception):
                    raise entries
                return entries

        graphics_probe._bigworld = lambda: _BigWorld

    def test_each_setting_reports_its_index_and_its_lowest(self):
        self._install(_entries())
        state = graphics_probe.snapshot()
        self.assertEqual((1, 2), state['TEXTURE_QUALITY'])
        self.assertEqual((3, 3), state['TERRAIN_QUALITY'])
        self.assertEqual((0, 1), state['FLORA_QUALITY'])

    def test_the_line_leads_with_the_three_quality_settings(self):
        # These are the three MemoryCriticalController actually lowers, and
        # terrain is the one this port's collision reads.
        self._install(_entries())
        line = graphics_probe.format_line('round_start', 7)
        self.assertIn('GRAPHICS phase=round_start round=7 settings=4', line)
        self.assertIn('TEXTURE_QUALITY=1/2', line)
        self.assertIn('TERRAIN_QUALITY=3/3', line)
        self.assertIn('FLORA_QUALITY=0/1', line)
        self.assertLess(line.index('TEXTURE_QUALITY'),
                        line.index('RENDER_PIPELINE'))

    def test_a_terrain_quality_already_at_minimum_is_visible_as_such(self):
        # 3/3 is the bottom of the scale: higher index is lower quality, which
        # is how MemoryCriticalController computes len(options) - 1 as MIN.
        self._install(_entries())
        line = graphics_probe.format_line('round_end', 7)
        self.assertIn('TERRAIN_QUALITY=3/3', line)

    def test_an_entry_without_an_option_list_still_reports_its_index(self):
        self._install([('OBJECT_LOD', 2)])
        self.assertEqual((2, -1), graphics_probe.snapshot()['OBJECT_LOD'])
        self.assertIn('OBJECT_LOD=2',
                      graphics_probe.format_line('round_start', 1))

    def test_unknown_settings_are_reported_but_bounded(self):
        entries = [('SETTING_%02d' % index, 0, [['x', True, False]])
                   for index in range(graphics_probe.MAX_REPORTED + 20)]
        self._install(entries)
        line = graphics_probe.format_line('round_start', 1)
        self.assertIn('settings=%d' % len(entries), line)
        self.assertEqual(graphics_probe.MAX_REPORTED,
                         line.count('SETTING_'))

    def test_a_client_without_the_api_reports_nothing(self):
        graphics_probe._bigworld = lambda: None
        self.assertIsNone(graphics_probe.snapshot())
        self.assertIsNone(graphics_probe.format_line('round_start', 1))

    def test_an_empty_registry_reports_nothing(self):
        self._install([])
        self.assertIsNone(graphics_probe.snapshot())
        self.assertIsNone(graphics_probe.format_line('round_start', 1))

    def test_logging_never_raises_into_the_round(self):
        self._install(RuntimeError('native settings unavailable'))
        self.assertIsNone(graphics_probe.snapshot())
        graphics_probe.log('round_start', 1)


if __name__ == '__main__':
    unittest.main()
