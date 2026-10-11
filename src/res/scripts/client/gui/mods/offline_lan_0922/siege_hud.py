"""Battle-scoped presentation for the stock Siege mode indicator."""

from gui.mods.offline_lan_0922.siege_mechanics import DISABLED, ENABLED


class PersistentSiegeHints(object):
    """Present the offline mode timer and preserve the native key hint.

    The native indicator still chooses the text, key binding, animation and
    visibility for transitions, destruction, observers and postmortem.  Its
    finite tutorial quota is bypassed only for this battle provider, and a
    stable mode displays the next switch duration from its native table.
    AccountSettings retains its original value when the indicator disposes.
    """

    _HINT_METHOD = '_SiegeModeIndicator__updateHintView'
    _STATE_METHOD = '_SiegeModeIndicator__updateSiegeState'
    _VIEW_METHOD = '_SiegeModeIndicator__updateIndicatorView'

    def __init__(self, indicator_type, session_provider):
        self._indicator_type = indicator_type
        self._session_provider = session_provider
        self._patches = []
        self._installed = False

    def install(self):
        if self._installed:
            return False
        indicator_type = self._indicator_type
        originals = [(name, indicator_type.__dict__[name]) for name in (
            self._HINT_METHOD, self._STATE_METHOD, self._VIEW_METHOD)]
        for name, original in originals:
            if not callable(original):
                raise RuntimeError('stock Siege hint callback is unavailable')
        owner = self

        def wrap(original, allow_hint):
            def persistent_hint(indicator, *args, **kwargs):
                if (not owner._installed or
                        indicator.sessionProvider is not owner._session_provider):
                    return original(indicator, *args, **kwargs)
                hints_left = indicator._hintsLeft
                try:
                    if allow_hint:
                        indicator._hintsLeft = 1
                    return original(indicator, *args, **kwargs)
                finally:
                    indicator._hintsLeft = hints_left
            return persistent_hint

        def wrap_timer(original):
            def mode_timer(indicator, *args, **kwargs):
                if (not owner._installed or
                        indicator.sessionProvider is not owner._session_provider or
                        indicator._siegeState not in (DISABLED, ENABLED)):
                    return original(indicator, *args, **kwargs)
                # Stable LAN states carry zero remaining transition time.
                # Flash uses its second time argument for the visible number
                # even when no countdown is running, and renders zero as
                # "- -". The native indicator already maps each stable mode
                # to the NEXT switch duration from the vehicle descriptor,
                # including its current engine state. Adapt that display
                # argument only; switching states keep their server countdown.
                switch_time = indicator._switchTime
                try:
                    indicator._switchTime = indicator._switchTimeTable[
                        indicator._siegeState][indicator._devices['engine']]
                    return original(indicator, *args, **kwargs)
                finally:
                    indicator._switchTime = switch_time
            return mode_timer

        for name, original in originals:
            replacement = (wrap_timer(original) if name == self._VIEW_METHOD
                           else wrap(original, name == self._HINT_METHOD))
            setattr(indicator_type, name, replacement)
            self._patches.append((name, original, replacement))
        self._installed = True
        return True

    def close(self):
        self._installed = False
        for name, original, replacement in reversed(self._patches):
            # A later mod owns its own hook; never overwrite it during exit.
            if self._indicator_type.__dict__.get(name) is replacement:
                setattr(self._indicator_type, name, original)
        self._patches = []
        self._session_provider = None
