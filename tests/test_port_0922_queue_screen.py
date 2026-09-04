import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    full_name = 'gui.mods.offline_lan_0922.queue_screen'
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(
        full_name, PACKAGE_ROOT / 'queue_screen.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _Event(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler()


class _PlayerEvents(object):
    def __init__(self):
        self.onEnqueuedRandom = _Event()
        self.onDequeuedRandom = _Event()


class _Account(object):
    def __init__(self):
        self.isInRandomQueue = False


class _PrbAction(object):
    def __init__(self, actionName, mapID=None, accountsToInvite=None):
        self.actionName = actionName
        self.mapID = mapID
        self.accountsToInvite = accountsToInvite


class _RandomEntity(object):
    """Mirrors the verified #1513 pre-queue toggle and async completion."""

    def __init__(self, account, events, pending):
        self._account = account
        self._events = events
        self._pending = pending
        self.queue_calls = 0
        self.dequeue_calls = 0

    def getQueueType(self):
        return 1

    def isInQueue(self):
        return self._account.isInRandomQueue

    def doAction(self, action=None):
        # PreQueueEntity.doAction toggles by isInQueue(); the enqueue and
        # dequeue effects land on a later server callback.
        if not self.isInQueue():
            self.queue_calls += 1

            def enqueue():
                self._account.isInRandomQueue = True
                self._events.onEnqueuedRandom.fire()

            self._pending.append(enqueue)
        else:
            self.dequeue_calls += 1

            def dequeue():
                self._account.isInRandomQueue = False
                self._events.onDequeuedRandom.fire()

            self._pending.append(dequeue)
        return True

    def exitFromQueue(self):
        self.doAction()


class _Dispatcher(object):
    def __init__(self, entity, vehicle_present=True):
        self.entity = entity
        self.vehicle_present = vehicle_present
        self.actions = []

    def getEntity(self):
        return self.entity

    def doAction(self, action):
        # _PreBattleDispatcher.doAction refuses without a garage vehicle.
        if not self.vehicle_present:
            return False
        self.actions.append(action)
        return self.entity.doAction(action)


class _Runtime(object):
    def __init__(self, dispatcher, account, events):
        self.player_events = events
        self.prb_action_type = _PrbAction
        self._dispatcher = dispatcher
        self._account = account

        class _Loader(object):
            @staticmethod
            def getDispatcher():
                return self._dispatcher

        class _BigWorld(object):
            @staticmethod
            def player():
                return self._account

        self.prb_loader = _Loader()
        self.bigworld = _BigWorld()


class QueueScreenTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.pending = []
        self.account = _Account()
        self.events = _PlayerEvents()
        self.entity = _RandomEntity(self.account, self.events, self.pending)
        self.dispatcher = _Dispatcher(self.entity)
        self.runtime = _Runtime(self.dispatcher, self.account, self.events)
        self.exits = []
        self.screen = self.module.QueueScreenUI(
            lambda: self.exits.append(True), runtime=self.runtime)
        self.screen.install()

    def pump(self):
        while self.pending:
            self.pending.pop(0)()

    def test_open_enqueues_once_and_is_idempotent_while_queued(self):
        self.assertTrue(self.screen.open())
        self.assertEqual(1, self.entity.queue_calls)
        self.assertEqual(0, self.dispatcher.actions[0].mapID)
        self.pump()
        self.assertTrue(self.account.isInRandomQueue)
        self.assertTrue(self.screen.open())
        self.assertEqual(1, self.entity.queue_calls)

    def test_open_fails_closed_without_a_dispatcher(self):
        self.runtime._dispatcher = None
        self.assertFalse(self.screen.open())
        self.assertEqual(0, self.entity.queue_calls)

    def test_open_fails_closed_outside_the_random_pre_queue(self):
        self.entity.getQueueType = lambda: 17
        self.assertFalse(self.screen.open())
        self.assertEqual(0, self.entity.queue_calls)

    def test_open_fails_closed_when_the_dispatcher_refuses_the_action(self):
        self.dispatcher.vehicle_present = False
        self.assertFalse(self.screen.open())
        self.assertEqual(0, self.entity.queue_calls)
        self.events.onDequeuedRandom.fire()
        self.assertEqual([], self.exits)

    def test_leave_dequeues_without_reporting_a_room_exit(self):
        self.assertTrue(self.screen.open())
        self.pump()
        self.assertTrue(self.screen.leave())
        self.assertEqual(1, self.entity.dequeue_calls)
        self.pump()
        self.assertFalse(self.account.isInRandomQueue)
        self.assertEqual([], self.exits)

    def test_leave_never_toggles_the_queue_while_not_queued(self):
        # exitFromQueue enqueues when the player is not queued, so a leave
        # outside the queue must not reach it.
        self.assertFalse(self.screen.leave())
        self.assertEqual(0, self.entity.queue_calls)
        self.assertEqual(0, self.entity.dequeue_calls)

    def test_stock_queue_exit_reports_one_room_exit(self):
        self.assertTrue(self.screen.open())
        self.pump()
        self.entity.exitFromQueue()
        self.pump()
        self.assertEqual([True], self.exits)
        self.events.onDequeuedRandom.fire()
        self.assertEqual([True], self.exits)

    def test_uninstall_dequeues_and_stops_listening(self):
        self.assertTrue(self.screen.open())
        self.pump()
        self.assertTrue(self.screen.uninstall())
        self.assertEqual(1, self.entity.dequeue_calls)
        self.pump()
        self.assertEqual([], self.exits)
        self.assertEqual([], self.events.onDequeuedRandom.handlers)


if __name__ == '__main__':
    unittest.main()
