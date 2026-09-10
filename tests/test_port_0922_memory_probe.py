"""The per-round address-space line the two 2026-09-09 worker crashes needed.

Both `hidden-worker.dmp` files in that batch showed ~1.6 GB of a 2 GB user
address space committed on the seventh round, which is why a Scaleform
allocation returned NULL and the stock null branch faulted.  Nothing in either
log said so.  These tests pin the numbers the probe reports and, more
importantly, that it never raises into the caller.
"""

import ctypes
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import memory_probe


def _regions():
    return [
        (0x00000000, 0x00010000, memory_probe.MEM_FREE, 0),
        (0x00010000, 0x00100000, memory_probe.MEM_COMMIT,
         memory_probe.MEM_PRIVATE),
        (0x00110000, 0x00100000, memory_probe.MEM_COMMIT,
         memory_probe.MEM_PRIVATE),
        (0x00210000, 0x00200000, memory_probe.MEM_COMMIT,
         memory_probe.MEM_IMAGE),
        (0x00410000, 0x00080000, memory_probe.MEM_RESERVE,
         memory_probe.MEM_PRIVATE),
        (0x00490000, 0x7FB70000, memory_probe.MEM_FREE, 0),
    ]


class _FakeKernel(object):
    """Answer VirtualQuery from a fixed region table, as Windows would."""

    def __init__(self, regions, load=42, status=True):
        self.regions = regions
        self.load = load
        self.status = status
        self.queries = 0

    def VirtualQuery(self, address, buffer_ref, size):
        self.queries += 1
        base = address.value or 0
        for start, length, state, kind in self.regions:
            if start <= base < start + length:
                info = ctypes.cast(
                    buffer_ref,
                    ctypes.POINTER(memory_probe._MemoryBasicInformation)
                ).contents
                info.BaseAddress = start
                info.AllocationBase = start
                info.RegionSize = start + length - base
                info.State = state
                info.Protect = 4
                info.Type = kind
                return size
        return 0

    def GlobalMemoryStatusEx(self, reference):
        if not self.status:
            return 0
        ctypes.cast(
            reference, ctypes.POINTER(memory_probe._MemoryStatusEx)
        ).contents.dwMemoryLoad = self.load
        return 1


class MemoryProbeTest(unittest.TestCase):
    def setUp(self):
        self._kernel32 = memory_probe._kernel32
        self.addCleanup(setattr, memory_probe, '_kernel32', self._kernel32)

    def _install(self, kernel):
        memory_probe._kernel32 = lambda: kernel

    def test_the_walk_separates_private_image_reserved_and_free(self):
        self._install(_FakeKernel(_regions()))
        state = memory_probe.snapshot()
        self.assertEqual(2 * 1024 * 1024, state['private'])
        self.assertEqual(2 * 1024 * 1024, state['image'])
        self.assertEqual(0, state['mapped'])
        self.assertEqual(512 * 1024, state['reserved'])
        self.assertEqual(0x7FB70000, state['largest_free'])
        self.assertEqual(6, state['regions'])
        self.assertEqual(42, state['system_load'])
        self.assertEqual(0, state['truncated'])

    def test_one_mib_private_allocations_are_counted_on_their_own(self):
        # 570 and 648 of these held most of the address space in the two
        # crashed workers, so the count is the number worth trending.
        self._install(_FakeKernel(_regions()))
        self.assertEqual(2, memory_probe.snapshot()['pool_blocks'])

    def test_a_pathological_process_truncates_instead_of_stalling(self):
        regions = [(index * 0x10000, 0x10000, memory_probe.MEM_COMMIT,
                    memory_probe.MEM_PRIVATE)
                   for index in range(memory_probe.MAX_REGIONS + 50)]
        kernel = _FakeKernel(regions)
        self._install(kernel)
        state = memory_probe.snapshot()
        self.assertEqual(1, state['truncated'])
        self.assertLessEqual(kernel.queries, memory_probe.MAX_REGIONS + 1)

    def test_a_system_status_failure_still_reports_the_walk(self):
        self._install(_FakeKernel(_regions(), status=False))
        self.assertEqual(-1, memory_probe.snapshot()['system_load'])

    def test_the_line_names_the_phase_and_round(self):
        self._install(_FakeKernel(_regions()))
        line = memory_probe.format_line('round_end', 7)
        self.assertIn('MEMORY phase=round_end round=7', line)
        self.assertIn('private_mb=2.0', line)
        self.assertIn('pool_1mib=2', line)

    def test_a_client_without_the_windows_api_reports_nothing(self):
        self._install(None)
        self.assertIsNone(memory_probe.snapshot())
        self.assertIsNone(memory_probe.format_line('round_start', 1))

    def test_logging_never_raises_into_the_round(self):
        class _Broken(object):
            def VirtualQuery(self, *unused):
                raise OSError('no')

            def GlobalMemoryStatusEx(self, *unused):
                raise OSError('no')

        self._install(_Broken())
        self.assertIsNone(memory_probe.snapshot())
        memory_probe.log('round_start', 1)


if __name__ == '__main__':
    unittest.main()
