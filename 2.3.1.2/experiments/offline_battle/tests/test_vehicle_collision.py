import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

vehicle_collision = package_stub.load('vehicle_collision')


class FakeTester(object):
    def __init__(self):
        self.loaded = 0
        self.released = 0

    def loadBspModel(self):
        self.loaded += 1

    def releaseBspModel(self):
        self.released += 1


class FakeManager(object):
    def __init__(self):
        self.modelHitTester = FakeTester()
        self.crashedModelHitTester = FakeTester()
        self.load_calls = 0

    def loadHitTesters(self):
        self.load_calls += 1
        self.modelHitTester.loadBspModel()
        self.crashedModelHitTester.loadBspModel()


class FakeDescriptor(object):
    def __init__(self, managers):
        self._managers = managers

    def getHitTesterManagers(self):
        return self._managers


class PrepareTests(unittest.TestCase):
    def setUp(self):
        vehicle_collision.release_all()

    def tearDown(self):
        vehicle_collision.release_all()

    def test_prepare_loads_every_manager_once(self):
        managers = [FakeManager(), FakeManager()]
        descriptor = FakeDescriptor(managers)
        vehicle_collision.prepare(descriptor)
        vehicle_collision.prepare(descriptor)
        self.assertEqual([m.load_calls for m in managers], [1, 1])

    def test_a_missing_manager_is_skipped(self):
        manager = FakeManager()
        vehicle_collision.prepare(FakeDescriptor([None, manager]))
        self.assertEqual(manager.load_calls, 1)

    def test_release_all_releases_both_testers(self):
        manager = FakeManager()
        vehicle_collision.prepare(FakeDescriptor([manager]))
        vehicle_collision.release_all()
        self.assertEqual(manager.modelHitTester.released, 1)
        self.assertEqual(manager.crashedModelHitTester.released, 1)


if __name__ == '__main__':
    unittest.main()
