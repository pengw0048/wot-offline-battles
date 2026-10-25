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
    return BigWorld.camera().handleKeyEvent(event)


def handleMouseEvent(event):
    return BigWorld.camera().handleMouseEvent(event)


def handleAxisEvent(event):
    return BigWorld.camera().handleAxisEvent(event)
