"""Public-engine lifecycle glue; scene and battle ownership are native."""

import BigWorld


def init(*args):
    pass


def start():
    pass


def fini():
    pass


def onChangeEnvironments(inside):
    pass


def handleKeyEvent(event):
    if event.key in tuple(BigWorld.stringToKey(key)
                          for key in ("W", "S", "A", "D", "SPACE")):
        # The native vehicle scene samples the engine's current key state.
        return True
    if event.key == BigWorld.stringToKey("LEFTMOUSE"):
        if event.isKeyDown() and not event.isRepeatedEvent():
            BigWorld.standaloneFire()
        return True
    return BigWorld.camera().handleKeyEvent(event)


def handleMouseEvent(event):
    return BigWorld.camera().handleMouseEvent(event)


def handleAxisEvent(event):
    return BigWorld.camera().handleAxisEvent(event)
