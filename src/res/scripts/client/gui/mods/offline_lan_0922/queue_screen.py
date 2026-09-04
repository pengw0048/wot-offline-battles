from __future__ import print_function

"""Stock #1513 battle-queue screen kept under the LAN waiting room.

Exact #1513 evidence (scripts.pkg members):

- ``gui/prb_control/dispatcher.pyc``: ``g_prbLoader.getDispatcher()`` is None
  outside the lobby; ``doAction`` checks only ``g_currentVehicle.isPresent()``
  and delegates to the current entity.
- ``gui/prb_control/factories/PreQueueFactory.pyc``: the default lobby entity
  is ``RandomEntity``.
- ``gui/prb_control/entities/base/pre_queue/entity.pyc`` and
  ``entities/random/pre_queue/entity.pyc``: ``doAction`` toggles by
  ``isInQueue()``; the queue screen's exit control calls ``exitFromQueue()``
  through the same toggle. ``onEnqueued`` loads ``VIEW_ALIAS.BATTLE_QUEUE``
  and ``onDequeued`` loads the hangar.
- ``Account.pyc``: ``enqueueRandom``/``dequeueRandom`` send
  ``base.doCmdInt3(REQUEST_ID_NO_RESPONSE, CMD_ENQUEUE_RANDOM/_DEQUEUE_RANDOM,
  ...)``; ``onEnqueued/onDequeued(QUEUE_TYPE.RANDOMS)`` toggle
  ``player.isInRandomQueue`` and fire ``g_playerEvents.onEnqueuedRandom`` /
  ``onDequeuedRandom``. The offline answers live in ``account_rpc``.
"""

import sys

# constants.pyc QUEUE_TYPE.RANDOMS.
QUEUE_TYPE_RANDOMS = 1


def _log(message):
    sys.stdout.write('[Offline LAN 0.9.22] %s\n' % message)


def _load_runtime():
    import BigWorld
    from PlayerEvents import g_playerEvents
    from gui.prb_control.dispatcher import g_prbLoader
    from gui.prb_control.entities.base.ctx import PrbAction

    class Runtime(object):
        pass

    runtime = Runtime()
    runtime.bigworld = BigWorld
    runtime.player_events = g_playerEvents
    runtime.prb_loader = g_prbLoader
    runtime.prb_action_type = PrbAction
    return runtime


class QueueScreenUI(object):
    """Presents the stock random pre-queue view below the LAN room."""

    def __init__(self, on_exit, runtime=None):
        self._on_exit = on_exit
        self._runtime = runtime
        self._installed = False
        self._engaged = False

    def install(self):
        if self._installed:
            return True
        if self._runtime is None:
            self._runtime = _load_runtime()
        self._runtime.player_events.onDequeuedRandom += \
            self._on_dequeued_random
        self._installed = True
        return True

    def _dispatcher(self):
        return self._runtime.prb_loader.getDispatcher()

    def _in_queue(self):
        player = self._runtime.bigworld.player()
        return bool(getattr(player, 'isInRandomQueue', False))

    def open(self):
        """Enter the stock random queue so its screen loads under the room."""
        if not self._installed:
            return False
        try:
            if self._in_queue():
                self._engaged = True
                return True
            dispatcher = self._dispatcher()
            if dispatcher is None:
                _log('LAN queue screen unavailable: no prebattle dispatcher')
                return False
            queue_type = getattr(dispatcher.getEntity(), 'getQueueType', None)
            if not callable(queue_type) or queue_type() != QUEUE_TYPE_RANDOMS:
                _log('LAN queue screen unavailable: the lobby is not in the '
                     'random pre-queue')
                return False
            accepted = dispatcher.doAction(
                self._runtime.prb_action_type('', mapID=0))
            if not accepted:
                _log('LAN queue screen unavailable: the queue action was '
                     'refused')
                return False
            self._engaged = True
            return True
        except Exception as error:
            _log('LAN queue screen could not open: %s' % error)
            return False

    def leave(self):
        """Leave the stock queue; its screen unloads back to the hangar."""
        if not self._installed:
            return False
        self._engaged = False
        try:
            # exitFromQueue toggles by queue state, so it must never run
            # while the player is not queued.
            if not self._in_queue():
                return False
            dispatcher = self._dispatcher()
            if dispatcher is None:
                return False
            exit_from_queue = getattr(
                dispatcher.getEntity(), 'exitFromQueue', None)
            if not callable(exit_from_queue):
                return False
            exit_from_queue()
            return True
        except Exception as error:
            _log('LAN queue screen could not leave the queue: %s' % error)
            return False

    def _on_dequeued_random(self):
        if not self._engaged:
            return
        self._engaged = False
        if callable(self._on_exit):
            self._on_exit()

    def uninstall(self):
        if not self._installed:
            return True
        self.leave()
        self._runtime.player_events.onDequeuedRandom -= \
            self._on_dequeued_random
        self._installed = False
        self._engaged = False
        return True
