"""Asynchronous Account base proxy matching #1513 response/stream callbacks."""

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle
import zlib

from gui.mods.offline_lan_0922.account_rpc import commands, data, requests


def _refresh_garage_views(diff, after_refresh=None):
    """Complete the stock refresh chain for a locally applied inventory diff.

    ``gui/shared/personality.onClientUpdate`` runs
    ``itemsCache.update(CLIENT_UPDATE, diff)`` and only then
    ``g_clientUpdateManager.update(diff)``.  The order matters: the first call
    drops ``ItemsRequester``'s memoised ``Vehicle`` and re-reads the inventory
    behind it, and the second is what reaches
    ``_CurrentVehicle.onInventoryUpdate`` -> ``onChanged`` ->
    ``Hangar.__updateParams``.  Running the second alone would recompute the
    parameters panel from the pre-mount descriptor.

    This is a fallback: when the stock listener already completes the chain,
    both calls are idempotent.  Any failure here is presentation only.
    ``after_refresh`` is called exactly once after the chain finishes or can
    no longer be started.
    """
    completed = [False]

    def complete():
        if completed[0]:
            return
        completed[0] = True
        if callable(after_refresh):
            after_refresh()

    try:
        import adisp
        from gui.ClientUpdateManager import g_clientUpdateManager
        from gui.shared.items_cache import CACHE_SYNC_REASON
        from gui.shared.personality import ServicesLocator
    except ImportError:
        complete()
        return False

    @adisp.process
    def refresh():
        try:
            yield ServicesLocator.itemsCache.update(
                CACHE_SYNC_REASON.CLIENT_UPDATE, diff)
            g_clientUpdateManager.update(diff)
        except Exception as error:
            print('[Offline LAN 0.9.22] the garage views did not refresh: '
                  '%s' % error)
        finally:
            complete()

    try:
        refresh()
    except Exception as error:
        print('[Offline LAN 0.9.22] the garage refresh could not start: %s'
              % error)
        complete()
        return False
    return True


def pack_stream(value):
    payload = zlib.compress(_pickle.dumps(value, _pickle.HIGHEST_PROTOCOL))
    crc = zlib.crc32(payload) & 0xffffffff
    return (False, len(payload), len(payload), crc, crc), payload


class FakeServer(object):
    """Base/cell stand-in with registered command dispatch and async callbacks.

    ``callback`` must behave as ``BigWorld.callback(0.0, fn)``.  In production
    it defaults to BigWorld.callback; tests can inject a deterministic queue.
    """
    def __init__(self, player_getter, callback=None, context=None):
        self._player_getter = player_getter
        self._context = dict(context or {})
        if self._context.get('account_state') is None:
            from gui.mods.offline_lan_0922.account_rpc.state import AccountState
            self._context['account_state'] = AccountState(path=None)
        if self._context.get('postbattle_store') is None:
            account_state = self._context.get('account_state')
            postbattle = getattr(account_state, 'postbattle_store', None)
            if postbattle is not None:
                self._context['postbattle_store'] = postbattle
        if callback is None:
            import BigWorld
            callback = BigWorld.callback
        self._callback = callback
        self._context.setdefault('push_update', self._push_update)
        self._context.setdefault(
            'push_update_and_wait', self._push_update)

    def _push_update(self, diff, after_publish=None):
        """Publish one account diff through the exact #1513 entity method.

        ``PlayerAccount.update`` unpickles its argument and forwards it to
        ``_update(True, diff)``.  Two details of that method matter:

        - ``isFullSync`` is ``diff.get('prevRev') is None``, and a full sync
          makes ``Inventory.synchronize`` clear the account cache before
          applying the diff.  We publish a complete inventory, so either mode
          is consistent, but stamping the revision pair keeps
          ``syncData.revision`` advancing instead of resetting to zero.
        - the only event it raises for an inventory diff is
          ``onClientUpdated``.  The garage parameters panel is refreshed
          further down that chain, by ``_CurrentVehicle.onInventoryUpdate``
          reading ``diff['inventory'][1]['compDescr'][vehInvID]``, which the
          published inventory already carries.
        """
        player = self._player()
        if player is None:
            return False
        diff = dict(diff)
        revision = 0
        try:
            revision = int(getattr(player.syncData, 'revision', 0) or 0)
        except (AttributeError, TypeError, ValueError):
            revision = 0
        diff.setdefault('potapovQuests', data.personal_missions())
        diff.setdefault('prevRev', revision)
        diff.setdefault('rev', revision + 1)
        payload = _pickle.dumps(diff, _pickle.HIGHEST_PROTOCOL)

        def publish():
            if self._player() is not player:
                return
            player.update(payload)

            def finish():
                if (self._player() is player and
                        callable(after_publish)):
                    after_publish(player)

            # The fallback exists for fitting changes whose inventory payload
            # must be re-read by the current-vehicle cache.  Reapplying a
            # stats-only update needlessly runs that expensive inventory path
            # a second time.  An accepted fitting command chains its success
            # response through ``after_publish``, so finish only after the
            # CurrentVehicle listener can read the new descriptor.
            if 'inventory' in diff:
                _refresh_garage_views(diff, after_refresh=finish)
            else:
                finish()

        self._callback(0.0, publish)
        return True

    def _player(self):
        try:
            return self._player_getter()
        except ReferenceError:
            return None

    def publish_postbattle_progress(self):
        """Push earned resources through the normal revisioned Account diff."""
        store = self._context.get('postbattle_store')
        if store is None:
            return False
        snapshot = data.stats(
            self._context.get('selected_vehicle'), store.progress())
        # PlayerAccount._update treats every descriptor carried by an
        # incremental ``eliteVehicles`` field as a newly elite vehicle and
        # emits one modal notification for each.  A post-battle update changes
        # only these accumulated values; the static unlocked/elite snapshot
        # belongs exclusively to the initial full sync.
        stats = snapshot['stats']
        diff = {'stats': dict((name, stats[name]) for name in (
            'credits', 'freeXP', 'vehTypeXP'))}
        touched = self._context.get('postbattle_touched_vehicles')
        touched = set(touched or ())
        if touched:
            diff.update(data.inventory(
                self._context.get('selected_vehicle'), validate=False,
                only_vehicles=touched, only_items={}))

        def resync_dossiers(player):
            shared_touched = self._context.get(
                'postbattle_touched_vehicles')
            if shared_touched is not None:
                shared_touched.difference_update(touched)
            resync = getattr(player, 'resyncDossiers', None)
            if callable(resync):
                resync()

        return self._push_update(diff, after_publish=resync_dossiers)

    def _respond(self, request_id, command, args):
        result = requests.dispatch(command, self._context, args)

        def before_response(on_complete):
            if callable(result.before_response):
                if result.wait_for_before_response:
                    result.before_response(on_complete)
                    return
                result.before_response()
            on_complete()

        if result.result_id == commands.RES_STREAM:
            desc, payload = pack_stream(result.stream)

            def respond_then_stream():
                player = self._player()
                if player is None:
                    return

                def respond():
                    if self._player() is not player:
                        return
                    player.onCmdResponse(
                        request_id, result.result_id, result.error)

                    def stream():
                        if self._player() is player:
                            player.onStreamComplete(
                                request_id, desc, payload)

                    self._callback(0.0, stream)

                before_response(respond)

            self._callback(0.0, respond_then_stream)
        elif result.ext is not None:
            ext = _pickle.dumps(result.ext, _pickle.HIGHEST_PROTOCOL)

            def respond_ext():
                player = self._player()
                if player is None:
                    return

                def respond():
                    if self._player() is player:
                        player.onCmdResponseExt(
                            request_id, result.result_id, result.error, ext)

                before_response(respond)

            self._callback(0.0, respond_ext)
        else:
            def respond():
                player = self._player()
                if player is None:
                    return

                def complete():
                    if self._player() is player:
                        player.onCmdResponse(
                            request_id, result.result_id, result.error)

                before_response(complete)

            self._callback(0.0, respond)
        return request_id

    def doCmdStr(self, request_id, command, value):
        return self._respond(request_id, command, (value,))

    def doCmdInt3(self, request_id, command, first, second, third):
        return self._respond(request_id, command, (first, second, third))

    def doCmdIntArr(self, request_id, command, values):
        return self._respond(request_id, command, (values,))

    def doCmdInt2Str(self, request_id, command, first, second, value):
        return self._respond(request_id, command, (first, second, value))

    def doCmdIntStr(self, request_id, command, first, value):
        return self._respond(request_id, command, (first, value))

    def doCmdInt4(self, request_id, command, first, second, third, fourth):
        return self._respond(request_id, command,
                             (first, second, third, fourth))

    def doCmdIntStrArr(self, request_id, command, first, values):
        return self._respond(request_id, command, (first, values))

    def doCmdIntArrStrArr(self, request_id, command, ints, strings):
        return self._respond(request_id, command, (ints, strings))

    def chatCommandFromClient(self, request_id, action, channel_id,
                              int64_arg, int16_arg, string_arg1,
                              string_arg2):
        # This is a one-way mailbox.  Empty offline chat has no server action
        # to publish.  In particular, ``action`` is a CHAT_COMMANDS index, not
        # a CHAT_ACTIONS index; echoing it into ClientChat.onChatAction causes
        # its exact #1513 processors to index malformed data before dispatch.
        return True

    def messenger_onActionByClient_chat2(self, action_id, request_id, args):
        """Accept the enabled #1513 BW_CHAT2 provider's one-way mailbox."""
        return True

    def update_context(self, values):
        if isinstance(values, dict):
            self._context.update(values)
