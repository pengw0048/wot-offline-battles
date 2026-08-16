"""Install the stock client-side account repository for offline battles.

PlayerAvatar.__init__ reads Account.g_accountRepository for intUserSettings,
prebattleInvitations, spaFlags and dogTags. This module creates the real
Account._AccountRepository so that chain stays stock.
"""
from __future__ import absolute_import

ACCOUNT_NAME = 'OfflineBattle'
ACCOUNT_CLASS_NAME = 'PlayerAccount'
INITIAL_SERVER_SETTINGS = {'file_server': {}}

_installed = False
_conversion_patch = None


def _guard_settings_conversion(log):
    """Keep a lobby settings migration from aborting onBecomePlayer.

    AccountSettings.convert migrates preference sections written by
    earlier sessions and assumes keys an offline session never writes.
    The migration has no meaning for an offline battle, so a failure
    must not stop the avatar from becoming the player."""
    global _conversion_patch
    from account_helpers.AccountSettings import AccountSettings
    if _conversion_patch is not None:
        return
    original = AccountSettings.convert

    def convert():
        try:
            return original()
        except Exception as error:
            log('account_settings_conversion_failed error=%s detail=%r'
                % (type(error).__name__, error))
            return None

    AccountSettings.convert = staticmethod(convert)
    _conversion_patch = (AccountSettings, original, convert)


def _restore_settings_conversion():
    global _conversion_patch
    if _conversion_patch is None:
        return
    owner, original, installed = _conversion_patch
    owner.convert = staticmethod(original)
    _conversion_patch = None


def install(log):
    global _installed
    import Account
    try:
        _guard_settings_conversion(log)
    except Exception as error:
        log('account_settings_guard_failed error=%s' % (type(error).__name__,))
    if Account.g_accountRepository is not None:
        log('account_repository_present className=%s' %
            (getattr(Account.g_accountRepository, 'className', None),))
        return True
    repository_class = getattr(Account, '_AccountRepository', None)
    if repository_class is None:
        log('account_repository_class_missing')
        return False
    try:
        Account.g_accountRepository = repository_class(
            ACCOUNT_NAME, ACCOUNT_CLASS_NAME, dict(INITIAL_SERVER_SETTINGS))
    except Exception as error:
        log('account_repository_failed error=%s detail=%r' %
            (type(error).__name__, error))
        Account.g_accountRepository = None
        return False
    _installed = True
    log('account_repository_installed')
    return True


def uninstall(log):
    global _installed
    _restore_settings_conversion()
    if not _installed:
        return
    _installed = False
    import Account
    remover = (getattr(Account, 'delAccountRepository', None) or
               getattr(Account, '_delAccountRepository', None))
    try:
        if remover is not None:
            remover()
        else:
            Account.g_accountRepository = None
        log('account_repository_removed')
    except Exception as error:
        Account.g_accountRepository = None
        log('account_repository_remove_failed error=%s' %
            (type(error).__name__,))
