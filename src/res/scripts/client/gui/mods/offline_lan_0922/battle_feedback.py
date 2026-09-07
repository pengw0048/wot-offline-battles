from __future__ import print_function

"""Battle-feedback helpers behind explicit #1513 adapters.

The offline runtime owns visibility detection.  This module deliberately does
not inspect Account, CurrentVehicle, or GUI globals: the caller supplies the
skill, alive, battle-period, and generation predicates it actually owns.
That keeps a delayed notification from escaping into a later battle or the
hangar while the #1513 lifecycle remains under BattleRuntime control.
"""

import sys


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


# ``vehicles._VEHICLE_TYPE_XML_PATH + nationName + '/components/shells.xml'``:
# the exact resource #1513 ``Cache.__readNation`` hands to ``_readShells``.
SHELLS_XML_PATH = 'scripts/item_defs/vehicles/%s/components/shells.xml'

# ``_readShells`` skips exactly these two top-level keys before it treats a
# subsection as a shell definition.
_NON_SHELL_SECTIONS = ('icons', 'xmlns:xmlref')

# Resolved once per nation.  ``shells.xml`` is immutable client content, so
# this never needs to follow a round, vehicle, or account change.
_gold_shell_names = {}

_reported_shell_price_failures = set()


def reset_shell_price_cache():
    """Drop resolved shell-price data (test isolation)."""
    _gold_shell_names.clear()
    _reported_shell_price_failures.clear()


def _report_shell_price_failure(nation, reason):
    """Write one bounded diagnostic per nation and failure reason."""
    key = (str(nation), str(reason))
    if key in _reported_shell_price_failures:
        return False
    _reported_shell_price_failures.add(key)
    try:
        sys.stdout.write(
            '[Offline LAN 0.9.22] FEEDBACK shell prices unresolved: '
            'nation=%s reason=%s\n' % (nation, reason))
    except Exception:
        return False
    return True


def _read_gold_shell_names(nation, res_mgr):
    """Collect one nation's gold-priced shell names from its raw resource.

    The exact client's own reader leaves ``Shell.isGold`` at its ``False``
    default (``_readShell`` guards the assignment with ``IS_CELLAPP``), so the
    value has to come from the raw item definitions.  ``_xml.readPrice``
    decides the currency by probing ``<subsection>/gold``; this reproduces
    that probe against the same sections ``_readShells`` iterates.
    """
    path = SHELLS_XML_PATH % (nation,)
    if res_mgr is None:
        import ResMgr
        res_mgr = ResMgr
    section = res_mgr.openSection(path)
    if section is None:
        return None
    try:
        names = set()
        for name, subsection in section.items():
            if name in _NON_SHELL_SECTIONS:
                continue
            if subsection['price/gold'] is not None:
                names.add(name)
    finally:
        purge = getattr(res_mgr, 'purge', None)
        if callable(purge):
            # Match the stock readers, which release the shell XML tree as
            # soon as they are done with it.
            try:
                purge(path, True)
            except Exception:
                pass
    return names


def gold_shell_names(nation, res_mgr=None):
    """Return one nation's gold-priced shell names, or None when unresolved.

    A failed or absent resource is contained here: the caller keeps the
    ``False`` that the exact client itself reports on this seam.
    """
    nation = str(nation)
    if nation in _gold_shell_names:
        return _gold_shell_names[nation]
    try:
        names = _read_gold_shell_names(nation, res_mgr)
        reason = None if names is not None else 'section_unavailable'
    except Exception as error:
        names = None
        reason = '%s: %s' % (error.__class__.__name__, error)
    _gold_shell_names[nation] = names
    if names is None:
        _report_shell_price_failure(nation, reason)
    return names


def is_gold_shell(nation, shell_name, res_mgr=None):
    """True when ``<nation>/components/shells.xml`` prices the shell in gold."""
    names = gold_shell_names(nation, res_mgr)
    if names is None:
        return False
    return str(shell_name) in names
