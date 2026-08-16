"""Breadcrumbs across the stock steps that run between vehicle visual
start and client-ready.

A native access violation cannot be caught in Python, so the last line in
python.log is the only evidence of where it happened. These wrappers log
one line per stock step and one line per event delegate.
"""
from __future__ import absolute_import

_patches = []
_log = None


def _describe(delegate):
    owner = getattr(delegate, 'im_class', None)
    if owner is None:
        owner = type(getattr(delegate, 'im_self', None))
    name = getattr(delegate, '__name__', None)
    if name is None:
        return repr(delegate)[:80]
    return '%s.%s' % (getattr(owner, '__name__', '?'), name)


def _traced_event_class():
    import Event

    class TracedEvent(Event.Event):

        def __call__(self, *args, **kwargs):
            for delegate in self[:]:
                _log('event_delegate event=onVehicleEnterWorld target=%s'
                     % (_describe(delegate),))
                delegate(*args, **kwargs)

            _log('event_done event=onVehicleEnterWorld')

    return TracedEvent


def trace_vehicle_enter_world(log, avatar):
    global _log
    _log = log
    original = avatar.onVehicleEnterWorld
    traced = _traced_event_class()()
    traced.extend(original)
    avatar.onVehicleEnterWorld = traced
    log('event_trace_installed event=onVehicleEnterWorld delegates=%s'
        % (len(traced),))


def _subject(args):
    """Name the entity a traced step runs on, so a fault points at one."""
    if not args:
        return ''
    identity = getattr(args[0], 'id', None)
    return '' if identity is None else ' id=%s' % (identity,)


def _wrap(owner, name, label):
    original = getattr(owner, name)

    def wrapper(*args, **kwargs):
        subject = _subject(args)
        _log('step_enter name=%s%s' % (label, subject))
        result = original(*args, **kwargs)
        _log('step_exit name=%s%s' % (label, subject))
        return result

    setattr(owner, name, wrapper)
    _patches.append((owner, name, original, wrapper))


def log_vehicle_state(log, vehicle, phase):
    """Report the native ownership that offline has to establish."""
    import BigWorld
    entity_filter = getattr(vehicle, 'filter', None)
    appearance = getattr(vehicle, 'appearance', None)
    log('vehicle_state phase=%s id=%s started=%s is_player=%s filter=%s '
        'is_wg_filter=%s has_set_physics=%s appearance=%s '
        'appearance_filter=%s'
        % (phase, vehicle.id, getattr(vehicle, 'isStarted', None),
           getattr(vehicle, 'isPlayerVehicle', None),
           type(entity_filter).__name__,
           isinstance(entity_filter, BigWorld.WGVehicleFilter),
           hasattr(entity_filter, 'setVehiclePhysics'),
           type(appearance).__name__,
           type(getattr(appearance, 'filter', None)).__name__))


def log_appearance_state(log, vehicle, phase):
    """Report why the CGF activate reaction has not matched yet.

    CommonTankAppearanceActivateSystem only reacts once the appearance
    game object is active and carries VehicleAppearanceComponent,
    BigWorld.CollisionComponent and Vehicular.LodCalculator."""
    import BigWorld
    import Vehicular
    appearance = getattr(vehicle, 'appearance', None)
    game_object = getattr(appearance, 'gameObject', None)
    entity_object = getattr(vehicle, 'entityGameObject', None)

    def present(component):
        try:
            return game_object.findRead(component) is not None
        except Exception as error:
            return type(error).__name__

    log('appearance_state phase=%s state=%s constructed=%s '
        'components_created=%s go_valid=%s entity_go_valid=%s '
        'collision=%s lod=%s'
        % (phase, getattr(appearance, '_state', None),
           getattr(appearance, 'isConstructed', None),
           getattr(appearance, 'isComponentsCreated', None),
           getattr(game_object, 'valid', None),
           getattr(entity_object, 'valid', None),
           present(BigWorld.CollisionComponent),
           present(Vehicular.LodCalculator)))


_input_trace_budget = [40]


def _install_input_trace(log):
    """Follow one key press from the engine down to the avatar."""
    import game
    import AvatarInputHandler
    from Avatar import PlayerAvatar

    original_game_key = game.handleKeyEvent

    def game_handle_key(event):
        result = original_game_key(event)
        if _input_trace_budget[0] > 0:
            _input_trace_budget[0] -= 1
            _log('input_trace stage=game key=%s down=%s consumed=%s'
                 % (getattr(event, 'key', None), event.isKeyDown(), result))
        return result

    original_handler_key = AvatarInputHandler.AvatarInputHandler.handleKeyEvent

    def handler_handle_key(handler, event):
        result = original_handler_key(handler, event)
        if _input_trace_budget[0] > 0:
            _input_trace_budget[0] -= 1
            _log('input_trace stage=input_handler key=%s down=%s '
                 'consumed=%s started=%s detached=%s mode=%s'
                 % (getattr(event, 'key', None), event.isKeyDown(), result,
                    getattr(handler, '_AvatarInputHandler__isStarted', None),
                    getattr(handler, '_AvatarInputHandler__isDetached', None),
                    getattr(handler, '_AvatarInputHandler__ctrlModeName',
                            None)))
        return result

    original_avatar_key = PlayerAvatar.handleKey

    def avatar_handle_key(avatar, isDown, key, mods):
        result = original_avatar_key(avatar, isDown, key, mods)
        if _input_trace_budget[0] > 0:
            _input_trace_budget[0] -= 1
            import CommandMapping
            mapping = CommandMapping.g_instance
            _log('input_trace stage=avatar key=%s down=%s consumed=%s '
                 'forward_fired=%s forward_active=%s'
                 % (key, isDown, result,
                    mapping.isFired(CommandMapping.CMD_MOVE_FORWARD, key),
                    mapping.isActive(CommandMapping.CMD_MOVE_FORWARD)))
        return result

    game.handleKeyEvent = game_handle_key
    AvatarInputHandler.AvatarInputHandler.handleKeyEvent = handler_handle_key
    PlayerAvatar.handleKey = avatar_handle_key
    _patches.append((game, 'handleKeyEvent', original_game_key,
                     game_handle_key))
    _patches.append((AvatarInputHandler.AvatarInputHandler, 'handleKeyEvent',
                     original_handler_key, handler_handle_key))
    _patches.append((PlayerAvatar, 'handleKey', original_avatar_key,
                     avatar_handle_key))
    log('input_trace_installed')


def _install_vehicle_state_trace(log):
    """Name whoever puts the crew panel into a stun state.

    The panel shows a stunned crew with a zero timer, and this build has
    no stun mechanic at all."""
    from gui.battle_control.battle_session import BattleSessionProvider
    from gui.battle_control.battle_constants import VEHICLE_VIEW_STATE
    original = BattleSessionProvider.invalidateVehicleState
    watched = (VEHICLE_VIEW_STATE.STUN,)

    def invalidate_vehicle_state(provider, state_id, value=None,
                                 vehicle_id=0):
        if state_id in watched:
            log('vehicle_state_invalidated state=%s value=%s vehicle=%s'
                % (state_id, value, vehicle_id))
        return original(provider, state_id, value, vehicle_id)

    BattleSessionProvider.invalidateVehicleState = invalidate_vehicle_state
    _patches.append((BattleSessionProvider, 'invalidateVehicleState',
                     original, invalidate_vehicle_state))
    from gui.battle_control.controllers.feedback_adaptor import (
        BattleFeedbackAdaptor)
    original_stun = BattleFeedbackAdaptor.invalidateStun

    def invalidate_stun(adaptor, vehicle_id, duration):
        log('stun_invalidated vehicle=%s duration=%s'
            % (vehicle_id, duration))
        return original_stun(adaptor, vehicle_id, duration)

    BattleFeedbackAdaptor.invalidateStun = invalidate_stun
    _patches.append((BattleFeedbackAdaptor, 'invalidateStun', original_stun,
                     invalidate_stun))


def install(log):
    global _log
    _log = log
    if _patches:
        return
    import AvatarPositionControl
    import Vehicle
    from Avatar import PlayerAvatar
    from gui.battle_control.battle_session import BattleSessionProvider
    _wrap(Vehicle.Vehicle, '_Vehicle__onActivateAppearance',
          'Vehicle.onActivateAppearance')
    _wrap(Vehicle.Vehicle, '_Vehicle__startWGPhysics',
          'Vehicle.startWGPhysics')
    _wrap(Vehicle.Vehicle, 'startVisual', 'Vehicle.startVisual')
    _wrap(PlayerAvatar, '_PlayerAvatar__startVehicleVisual',
          'Avatar.startVehicleVisual')
    _wrap(PlayerAvatar, 'setClientReady', 'Avatar.setClientReady')
    _wrap(AvatarPositionControl.ConsistentMatrices, 'notifyVehicleLoaded',
          'ConsistentMatrices.notifyVehicleLoaded')
    _wrap(BattleSessionProvider, 'setPlayerVehicle',
          'BattleSession.setPlayerVehicle')
    _wrap(Vehicle.Vehicle, 'resetProperties', 'Vehicle.resetProperties')
    _wrap(Vehicle.Vehicle, 'set_dotEffect', 'Vehicle.setDotEffect')
    _wrap(Vehicle.Vehicle, 'set_burnoutLevel', 'Vehicle.setBurnoutLevel')
    _wrap(Vehicle.Vehicle, 'set_engineMode', 'Vehicle.setEngineMode')
    _wrap(Vehicle.Vehicle, 'set_gunAnglesPacked', 'Vehicle.setGunAngles')
    _wrap(Vehicle.Vehicle, 'onHealthChanged', 'Vehicle.onHealthChanged')
    _wrap(Vehicle.Vehicle, 'set_isCrewActive', 'Vehicle.setIsCrewActive')
    _wrap(Vehicle.Vehicle, '_Vehicle__onVehicleDeath', 'Vehicle.onDeath')
    import BigWorld
    import ClientArena
    _install_input_trace(log)
    _install_vehicle_state_trace(log)
    _wrap(BigWorld, 'notifyBattleTime', 'BigWorld.notifyBattleTime')
    _wrap(ClientArena.ClientArena, 'startVsePlans', 'Arena.startVsePlans')
    _wrap(ClientArena.ClientArena, 'invalidateVehiclesPosition',
          'Arena.invalidateVehiclesPosition')
    _wrap(ClientArena.ClientArena, 'updateVehicleIsAlive',
          'Arena.updateVehicleIsAlive')
    log('step_trace_installed steps=%s' % (len(_patches),))


def uninstall(log):
    while _patches:
        owner, name, original, wrapper = _patches.pop()
        if getattr(owner, name) is wrapper:
            setattr(owner, name, original)
    log('step_trace_removed')
