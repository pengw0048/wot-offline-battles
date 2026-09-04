"""Small, explicit entity lifecycle adapter.

This module deliberately has no BigWorld imports.  A future #1513 runtime
binding must supply the exact Vehicle.def property names and the calls which
perform each stage.  Until that binding is verified, this adapter refuses to
invent entity properties.

The property boundary follows the observable Vehicle creation contract in
``AvatarServer.py`` at
https://github.com/the-tuxedo-cat/WoT-Offline-Server/tree/c0bc550c46deac980194b7b860ee8781d53ec97b
(Boost Software License 1.0; no source code is copied here).  The exact #1513
binding remains authority for property names and payload encoding; asynchronous
entity lifecycle ownership lives only in ``BattleRuntime``.
"""

from __future__ import print_function


class EntityStageError(RuntimeError):
    pass


class EntityPropertyBuilder(object):
    """Validate an exact, caller-supplied Vehicle property contract.

    The 0.9.22 Vehicle.def has not been accepted as a safe assignment schema
    yet.  ``required_names`` is therefore a capability result supplied by the
    eventual #1513 binding, not a list of guessed defaults.
    """

    def __init__(self, required_names):
        names = tuple(required_names or ())
        if not names or len(set(names)) != len(names):
            raise ValueError('required_names must be a non-empty unique sequence')
        self._required_names = names

    @property
    def required_names(self):
        return self._required_names

    def build(self, snapshot):
        if not isinstance(snapshot, dict):
            raise EntityStageError('vehicle snapshot must be a dict')
        properties = snapshot.get('properties')
        if not isinstance(properties, dict):
            raise EntityStageError('vehicle properties must be a dict')
        missing = [name for name in self._required_names
                   if name not in properties]
        if missing:
            raise EntityStageError('missing exact Vehicle properties: %s' %
                                   ', '.join(missing))
        extras = [name for name in properties if name not in self._required_names]
        if extras:
            raise EntityStageError('unverified Vehicle properties: %s' %
                                   ', '.join(sorted(extras)))
        return dict((name, properties[name]) for name in self._required_names)
