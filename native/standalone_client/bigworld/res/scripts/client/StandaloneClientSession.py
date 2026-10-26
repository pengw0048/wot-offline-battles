"""Network boundary and lifecycle observation for the original #1513 entities.

Avatar, Vehicle, InputHandler, Appearance and ProjectileMover remain the stock
modules. The caller supplies strict server mailboxes and real arena publication;
this adapter never substitutes readiness flags or a gameplay implementation.
"""

import BigWorld


_ACTIVE_SESSION = None
_MISSING = object()


class ClientSession(object):
    def __init__(self):
        self.stage = 'idle'
        self.error = ''
        self._space_id = None
        self._avatar_id = None
        self._vehicle_id = None
        self._avatar_server = None
        self._vehicle_server = None
        self._hooks = []
        self._creating_avatar = False
        self._avatar_class = None
        self._vehicle_class = None
        self._avatar_seen = False
        self._vehicle_seen = False

    @property
    def avatar(self):
        return (BigWorld.entity(self._avatar_id)
                if self._avatar_id is not None else None)

    @property
    def vehicle(self):
        return (BigWorld.entity(self._vehicle_id)
                if self._vehicle_id is not None else None)

    def _failed(self, error):
        if not self.error:
            self.error = '%s: %s' % (self.stage, error)
        self.stage = 'failed'

    def _hook(self, cls, name, getter):
        previous = cls.__dict__.get(name, _MISSING)
        installed = property(getter)
        setattr(cls, name, installed)
        self._hooks.append((cls, name, previous, installed))

    def _mailbox(self, entity, vehicle):
        entity_id = int(entity.id)
        if vehicle:
            if entity_id != self._vehicle_id or self._vehicle_server is None:
                raise RuntimeError('Vehicle mailbox has no reserved session identity')
            return self._vehicle_server
        if self._avatar_id is None and self._creating_avatar:
            # onBecomePlayer may synchronously request its mailbox while the
            # native player factory is constructing this very Avatar.
            self._avatar_id = entity_id
        if entity_id != self._avatar_id or self._avatar_server is None:
            raise RuntimeError('Avatar mailbox has no reserved session identity')
        return self._avatar_server

    def begin(self, space_id, properties, position, direction, server):
        global _ACTIVE_SESSION
        if self.stage != 'idle' or _ACTIVE_SESSION is not None:
            raise RuntimeError('A stock client session already owns the player')
        if not isinstance(properties, dict) or server is None:
            raise TypeError('Original Avatar properties and a strict server are required')
        self.error = ''
        self.stage = 'avatar_loading'
        try:
            import Avatar
            import Vehicle
            import AvatarInputHandler
            import ProjectileMover
            from vehicle_systems import CompoundAppearance
            self._avatar_class = Avatar.PlayerAvatar
            self._vehicle_class = Vehicle.Vehicle
            if not issubclass(self._avatar_class, BigWorld.Entity):
                raise TypeError('Avatar.PlayerAvatar is not the native stock entity')
            if not issubclass(self._vehicle_class, BigWorld.Entity):
                raise TypeError('Vehicle.Vehicle is not the native stock entity')
            # These names are direct #1513 consumers in Vehicle.startVisual,
            # model_assembler and CompoundAppearance. Missing native behavior
            # is an explicit blocker, never an empty successful substitute.
            required = ('WGVehicleFilter', 'WGVehiclePhysics', 'CompoundAssembler',
                        'PyTrackScroll', 'PyBallisticsSimulator')
            missing = [name for name in required
                       if not callable(getattr(BigWorld, name, None))]
            if missing:
                raise RuntimeError('Missing stock native interfaces: ' + ', '.join(missing))
            self._space_id = int(space_id)
            self._avatar_server = server
            _ACTIVE_SESSION = self
            self._hook(self._avatar_class, 'base',
                       lambda entity: self._mailbox(entity, False))
            self._hook(self._avatar_class, 'cell',
                       lambda entity: self._mailbox(entity, False))
            if Avatar.Avatar is not self._avatar_class:
                self._hook(Avatar.Avatar, 'base',
                           lambda entity: self._mailbox(entity, False))
                self._hook(Avatar.Avatar, 'cell',
                           lambda entity: self._mailbox(entity, False))
            self._hook(self._vehicle_class, 'cell',
                       lambda entity: self._mailbox(entity, True))
            self._creating_avatar = True
            try:
                created = BigWorld.standaloneCreateSessionAvatar(
                    self._space_id, position, direction, properties)
            finally:
                self._creating_avatar = False
            if self._avatar_id is not None and self._avatar_id != created:
                raise RuntimeError('Native player factory returned a different Avatar identity')
            self._avatar_id = int(created)
            return self._avatar_id
        except Exception as error:
            self._failed(error)
            raise

    def spawnOwnVehicle(self, properties, position, direction, server,
                        select_vehicle):
        if self.stage != 'avatar_loading' or self._vehicle_id is not None:
            raise RuntimeError('Own Vehicle cannot be created in this session phase')
        avatar = self.avatar
        if avatar is None or BigWorld.player() is not avatar or not avatar.inWorld:
            raise RuntimeError('Original Avatar has not entered its native player world')
        if not isinstance(properties, dict) or server is None or not callable(select_vehicle):
            raise TypeError('Vehicle properties, strict cell server and arena selector are required')
        self.stage = 'vehicle_loading'
        self._vehicle_server = server

        def allocated(entity_id):
            if self._vehicle_id is not None:
                raise RuntimeError('Own Vehicle identity was allocated twice')
            self._vehicle_id = int(entity_id)
            # The engine calls this after allocating the real id and before
            # constructing Vehicle. This is safe even with hot resource caches.
            select_vehicle(self._vehicle_id)
            if avatar.playerVehicleID != self._vehicle_id:
                raise RuntimeError('Arena selector did not select the real own Vehicle')
            if self._vehicle_id not in avatar.arena.vehicles:
                raise RuntimeError('Own Vehicle roster was not published before construction')
            return True

        try:
            created = BigWorld.standaloneCreateSessionVehicle(
                self._space_id, position, direction, properties, allocated)
            if created != self._vehicle_id:
                raise RuntimeError('Native Vehicle factory returned a different identity')
            self.advance()
            return self._vehicle_id
        except Exception as error:
            self._failed(error)
            raise

    def advance(self):
        if self.stage in ('idle', 'failed', 'stopping'):
            return self.stage
        try:
            avatar = self.avatar
            if avatar is None or not avatar.inWorld:
                if self._avatar_seen:
                    raise RuntimeError('Original Avatar disappeared outside session teardown')
                return self.stage
            self._avatar_seen = True
            if BigWorld.player() is not avatar or avatar.spaceID != self._space_id:
                raise RuntimeError('Stock player or space identity changed outside the session')
            if self._vehicle_id is None:
                return self.stage
            vehicle = self.vehicle
            if vehicle is None or not vehicle.inWorld:
                if self._vehicle_seen:
                    raise RuntimeError('Own Vehicle disappeared outside session teardown')
                return self.stage
            self._vehicle_seen = True
            if vehicle.spaceID != self._space_id or avatar.playerVehicleID != vehicle.id:
                raise RuntimeError('Own Vehicle left its selected native identity')
            if vehicle._Vehicle__isEnteringWorld or not vehicle.isStarted:
                return self.stage
            self.stage = 'battle_loading'
            # Observe the original completion and input gates. Do not invoke
            # startVisual/activate/start or write any of these private flags.
            import Avatar
            completed = Avatar._INIT_STEPS.INIT_COMPLETED
            if not (avatar._PlayerAvatar__initProgress & completed):
                return self.stage
            if vehicle.appearance is None or avatar.inputHandler is None:
                return self.stage
            if not avatar.inputHandler._AvatarInputHandler__isStarted:
                return self.stage
            if not avatar._PlayerAvatar__isOnArena:
                return self.stage
            self.stage = 'active'
            return self.stage
        except Exception as error:
            self._failed(error)
            raise

    def stop(self):
        global _ACTIVE_SESSION
        self.stage = 'stopping'
        errors = []
        # Vehicle.onLeaveWorld needs the live original Avatar to stop its
        # visual through avatar.vehicle_onLeaveWorld. Destroy it first.
        if self._vehicle_id is not None:
            try:
                BigWorld.standaloneDestroySessionVehicle(self._space_id,
                                                        self._vehicle_id)
                self._vehicle_id = None
            except Exception as error:
                errors.append('Vehicle teardown: %s' % error)
        if self._avatar_id is not None:
            try:
                # Original BWNullConnection base reset clears pending/active
                # Vehicles before demoting Avatar, including a partial startup.
                BigWorld.standaloneDestroySessionAvatar(self._space_id,
                                                       self._avatar_id)
                self._avatar_id = None
                self._vehicle_id = None
            except Exception as error:
                errors.append('Avatar teardown: %s' % error)
        if self._avatar_id is None and self._vehicle_id is None:
            for cls, name, previous, installed in reversed(self._hooks):
                try:
                    if cls.__dict__.get(name, _MISSING) is not installed:
                        raise RuntimeError('Stock mailbox ownership changed outside the session')
                    if previous is _MISSING:
                        delattr(cls, name)
                    else:
                        setattr(cls, name, previous)
                except Exception as error:
                    errors.append('Mailbox teardown: %s' % error)
            self._hooks = []
            if _ACTIVE_SESSION is self:
                _ACTIVE_SESSION = None
            self._avatar_server = self._vehicle_server = None
            self._avatar_seen = self._vehicle_seen = False
            self.stage = 'idle'
        if errors:
            error = RuntimeError('; '.join(errors))
            self._failed(error)
            raise error
        return True
