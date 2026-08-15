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


def install(log):
    global _installed
    import Account
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
