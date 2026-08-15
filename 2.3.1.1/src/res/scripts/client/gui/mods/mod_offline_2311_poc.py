from gui.mods.offline_2311_poc import lifecycle


def init():
    lifecycle.init()


def fini():
    lifecycle.fini()


def onConnected(*args, **kwargs):
    lifecycle.record_online_lifecycle('onConnected', args, kwargs)


def onDisconnected(*args, **kwargs):
    lifecycle.record_online_lifecycle('onDisconnected', args, kwargs)


def onAccountBecomePlayer(*args, **kwargs):
    lifecycle.record_online_lifecycle('onAccountBecomePlayer', args, kwargs)


def onAccountBecomeNonPlayer(*args, **kwargs):
    lifecycle.record_online_lifecycle('onAccountBecomeNonPlayer', args, kwargs)


def onAvatarBecomePlayer(*args, **kwargs):
    lifecycle.record_online_lifecycle('onAvatarBecomePlayer', args, kwargs)


def onAccountShowGUI(*args, **kwargs):
    lifecycle.record_online_lifecycle('onAccountShowGUI', args, kwargs)
