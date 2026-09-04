from __future__ import print_function

"""Battle-feedback helpers behind explicit #1513 adapters.

The offline runtime owns visibility detection.  This module deliberately does
not inspect Account, CurrentVehicle, or GUI globals: the caller supplies the
skill, alive, battle-period, and generation predicates it actually owns.
That keeps a delayed notification from escaping into a later battle or the
hangar while the #1513 lifecycle remains under BattleRuntime control.
"""


# Match the deterministic no-skill visibility hold.  A repeated authority
# observation inside this window is one continuous spotting episode, not a
# second Sixth Sense notification.
OBSERVATION_SECONDS = 10.0
SIXTH_SENSE_DELAY_SECONDS = 3.0


class VehicleStatePresenter(object):
    """Thin adapter for the confirmed #1513 vehicle-state presentation path."""

    def __init__(self, session_provider, vehicle_view_state):
        if session_provider is None or vehicle_view_state is None:
            raise ValueError('session provider and VEHICLE_VIEW_STATE are required')
        self._session_provider = session_provider
        self._vehicle_view_state = vehicle_view_state

    def notify_observed_by_enemy(self, value):
        """Use the stock #1513 vehicle-state event; do not call legacy GUI APIs."""
        self._session_provider.shared.vehicleState.notifyStateChanged(
            self._vehicle_view_state.OBSERVED_BY_ENEMY, bool(value))


class SixthSenseController(object):
    """Coalesce enemy observation into the delayed Sixth Sense indicator.

    ``schedule`` and ``cancel`` are normally ``BigWorld.callback`` and
    ``BigWorld.cancelCallback``. ``generation`` must return the current battle
    generation.  Supplying all predicates explicitly is intentional: #1513
    crew/account shapes differ from 0.8.2, and no vehicle is assumed to own
    Sixth Sense merely because it entered an offline battle.
    """

    def __init__(self, schedule, cancel, generation, has_sixth_sense,
                 is_alive, is_battle, presenter):
        required = (schedule, cancel, generation, has_sixth_sense,
                    is_alive, is_battle)
        if not all(callable(value) for value in required):
            raise ValueError('Sixth Sense requires explicit lifecycle predicates')
        if presenter is None or not callable(
                getattr(presenter, 'notify_observed_by_enemy', None)):
            raise ValueError('Sixth Sense requires a vehicle-state presenter')
        self._schedule = schedule
        self._cancel = cancel
        self._generation = generation
        self._has_sixth_sense = has_sixth_sense
        self._is_alive = is_alive
        self._is_battle = is_battle
        self._presenter = presenter
        self._observed_until = 0.0
        self._pending_callback = None

    def reset(self):
        """Cancel delayed work before the owning battle generation is retired."""
        callback = self._pending_callback
        self._pending_callback = None
        self._observed_until = 0.0
        if callback is not None:
            self._cancel(callback)

    def observe(self, visible_to_enemy, now):
        """Record one observation edge and schedule native presentation once."""
        try:
            now = float(now)
        except (TypeError, ValueError):
            return False
        if not visible_to_enemy:
            return False
        was_observed = now < self._observed_until
        self._observed_until = now + OBSERVATION_SECONDS
        if was_observed or not self._has_sixth_sense():
            return False
        expected_generation = self._generation()
        holder = [None]

        def _deliver():
            callback = holder[0]
            if callback == self._pending_callback:
                self._pending_callback = None
            if self._generation() != expected_generation:
                return
            if not self._is_alive() or not self._is_battle():
                return
            self._presenter.notify_observed_by_enemy(True)

        callback = self._schedule(SIXTH_SENSE_DELAY_SECONDS, _deliver)
        holder[0] = callback
        self._pending_callback = callback
        return True
