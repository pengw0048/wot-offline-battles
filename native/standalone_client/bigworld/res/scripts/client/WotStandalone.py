"""Public-engine lifecycle glue; scene and battle ownership are native."""


def init(*args):
    pass


def start():
    pass


def fini():
    pass


def onChangeEnvironments(inside):
    pass


def handleKeyEvent(event):
    return False


def handleMouseEvent(event):
    return False


def handleAxisEvent(event):
    return False
