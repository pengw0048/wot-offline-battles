"""A replay is a disposable visible-client session, never a reusable LAN room.

Only this process is asked to quit. ReplayClient.stop owns its decoder process;
the launcher's existing child supervision owns any paired worker. Do not kill
all game processes or an unrelated LAN room by executable name.
"""
from __future__ import print_function
import sys


class ReplayProcessExit(object):
    def __init__(self, session, bigworld, menu_type=None):
        self.session = session
        self.bigworld = bigworld
        self.menu_type = menu_type
        self.old_click = None
        self.wrapper = None
        self.old_labels = None
        self.labels_wrapper = None
        self.callback_id = None
        self.reason = None
        self.requested = False
        self.finished = False
        if menu_type is not None:
            self.old_click = menu_type.__dict__.get('quitBattleClick', menu_type.quitBattleClick)
            owner = self
            def click(menu):
                if getattr(owner.session.client, 'is_offline_replay', False):
                    return owner.request('esc')
                return owner.old_click(menu)
            self.wrapper = click
            menu_type.quitBattleClick = click
            self.old_labels = menu_type.__dict__.get('_IngameMenu__setMenuButtonsLabels')
            if self.old_labels is not None:
                def labels(menu):
                    if not getattr(owner.session.client, 'is_offline_replay', False):
                        return owner.old_labels(menu)
                    # Same native labels contract, only the quit action text
                    # identifies this session as replay; no fake native replay flag.
                    from gui.Scaleform.locale.MENU import MENU
                    menu.as_setMenuButtonsLabelsS(
                        MENU.INGAME_MENU_BUTTONS_HELP,
                        MENU.INGAME_MENU_BUTTONS_SETTINGS,
                        MENU.INGAME_MENU_BUTTONS_BACK,
                        MENU.INGAME_MENU_BUTTONS_REPLAYEXIT, '', '')
                self.labels_wrapper = labels
                menu_type._IngameMenu__setMenuButtonsLabels = labels

    def request(self, reason, error=None):
        if self.requested:
            return False
        self.requested = True
        self.reason = reason
        self.session._replay_exit_requested = True
        self.session._postbattle_return = None
        sys.stdout.write('[Offline LAN 0.9.22] REPLAY_EXIT requested reason=%s error=%s\n' %
                         (reason, error or '-'))
        # Finish the Flash menu / network / file-reader callback before
        # releasing its Avatar. Never tear an entity down on its own RPC stack.
        self.callback_id = self.bigworld.callback(0.0, self._finish)
        return True

    def _finish(self):
        self.callback_id = None
        if self.finished:
            return
        self.finished = True
        try:
            self.session.stop(show_login=False, restore_account=False,
                              release_join=True,
                              stop_runtime=self.reason != 'runtime_error')
        except Exception as error:
            # Continue the orderly engine quit even if a Python cleanup failed.
            # The failure remains explicit rather than returning to a fake lobby.
            sys.stdout.write('[Offline LAN 0.9.22] REPLAY_EXIT cleanup_error=%s\n' % error)
        finally:
            self.restore_menu()
        sys.stdout.write('[Offline LAN 0.9.22] REPLAY_EXIT quit_current_client restore_account=False\n')
        self.bigworld.quit()

    def restore_menu(self):
        if (self.menu_type is not None and
                self.menu_type.__dict__.get('quitBattleClick') is self.wrapper):
            self.menu_type.quitBattleClick = self.old_click
        if (self.menu_type is not None and self.labels_wrapper is not None and
                self.menu_type.__dict__.get('_IngameMenu__setMenuButtonsLabels') is self.labels_wrapper):
            self.menu_type._IngameMenu__setMenuButtonsLabels = self.old_labels


def install(session):
    controller = getattr(session, '_replay_exit_controller', None)
    if controller is not None:
        return controller
    if not getattr(session.client, 'is_offline_replay', False):
        raise RuntimeError('replay exit cannot own a live LAN session')
    import BigWorld
    from gui.Scaleform.daapi.view.battle.shared.ingame_menu import IngameMenu
    controller = ReplayProcessExit(session, BigWorld, IngameMenu)
    session._replay_exit_controller = controller
    return controller


def request(session, reason, error=None):
    return install(session).request(reason, error)
