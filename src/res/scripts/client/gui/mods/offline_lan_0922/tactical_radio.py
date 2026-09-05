"""Bridge the exact #1513 fixed battle commands to the LAN protocol.

The stock client owns command selection, cooldowns, chat formatting, sounds,
vehicle markers, and minimap feedback.  This adapter only translates the
reviewed command payload at the missing base-mailbox boundary and sends
accepted server results back through ``Avatar.messenger_onActionByServer_chat2``.
"""

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)


RESPONSE_SUCCESS_ACTION_ID = 0
RESPONSE_FAILURE_ACTION_ID = 1
GENERIC_ERROR_ID = 1
MAX_STOCK_REQUEST_ID = 32766

TARGET_NONE = None
TARGET_ALLY = 'ally'
TARGET_ENEMY = 'enemy'
TARGET_CELL = 'cell'

# ``BATTLE_CHAT_COMMANDS`` action IDs from the reviewed local #1513 archive.
# Reload/SPG status commands are intentionally outside the tactical order
# surface: their payloads describe transient gun state rather than Bot intent.
COMMAND_SPECS = {
    23: ('HELPME', TARGET_NONE),
    24: ('FOLLOWME', TARGET_ALLY),
    25: ('ATTACK', TARGET_NONE),
    26: ('BACKTOBASE', TARGET_NONE),
    27: ('POSITIVE', TARGET_NONE),
    28: ('NEGATIVE', TARGET_NONE),
    29: ('ATTENTIONTOCELL', TARGET_CELL),
    31: ('ATTACKENEMY', TARGET_ENEMY),
    32: ('TURNBACK', TARGET_ALLY),
    33: ('HELPMEEX', TARGET_ALLY),
    34: ('SUPPORTMEWITHFIRE', TARGET_ENEMY),
    36: ('STOP', TARGET_ALLY),
}
COMMAND_IDS_BY_NAME = dict(
    (spec[0], action_id) for action_id, spec in COMMAND_SPECS.items())

_MESSAGE_ARG_NAMES = (
    'int32Arg1', 'int64Arg1', 'floatArg1', 'strArg1', 'strArg2')


def _exact_int(value, minimum=None, maximum=None, allow_integral_float=False):
    if isinstance(value, bool):
        return None
    if isinstance(value, _INTEGER_TYPES):
        result = int(value)
    elif allow_integral_float and isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            return None
        result = int(value)
        if float(result) != value:
            return None
    else:
        return None
    if minimum is not None and result < minimum:
        return None
    if maximum is not None and result > maximum:
        return None
    return result


def _message_args(int32_arg=0, int64_arg=0, float_arg=0,
                  str_arg1='', str_arg2=''):
    return {
        'int32Arg1': int32_arg,
        'int64Arg1': int64_arg,
        'floatArg1': float_arg,
        'strArg1': str_arg1,
        'strArg2': str_arg2,
    }


class BattleRadioAdapter(object):
    """Own one Avatar's fixed-command translation and response lifecycle."""

    def __init__(self, avatar, lan_sender, player_getter=None):
        self._avatar = avatar
        self._lan_sender = lan_sender
        self._player_getter = player_getter
        self._pending = {}
        self._closed = False

    def close(self):
        if self._closed:
            return False
        self._closed = True
        self._pending = {}
        self._avatar = None
        self._lan_sender = None
        self._player_getter = None
        return True

    def handle_client_action(self, action_id, request_id, args):
        """Translate one stock mailbox call into one reliable LAN request."""
        if not self._is_current_avatar():
            return False
        action_id = _exact_int(action_id)
        # ``SequenceIDGenerator`` normally yields a positive id but resets to
        # its zero low bound at the exact high-bound wrap.  The stock response
        # dictionary simply ignores that untracked zero id.
        request_id = _exact_int(request_id, 0, MAX_STOCK_REQUEST_ID)
        spec = COMMAND_SPECS.get(action_id)
        if spec is None or request_id is None or not isinstance(args, dict):
            return False
        if set(args) != set(_MESSAGE_ARG_NAMES):
            return False

        command, target_kind = spec
        request = {
            'command': command,
            'stock_action_id': action_id,
            'stock_request_id': request_id,
        }
        if target_kind in (TARGET_ALLY, TARGET_ENEMY):
            target_id = _exact_int(args.get('int32Arg1'), 1)
            if target_id is None or not self._valid_target(
                    target_id, target_kind):
                return False
            # The stock id is a BigWorld entity id.  ``_LANInputSender`` owns
            # the current BattleRuntime record map and must translate it to a
            # wire ``target_kind`` + network ``target_id`` pair.
            request['target_relation'] = target_kind
            request['stock_target_id'] = target_id
        elif target_kind == TARGET_CELL:
            # ``makeCellIndex`` uses a float dimension and therefore returns
            # values such as 23.0 before the retail INT32 mailbox coerces it.
            cell_index = _exact_int(
                args.get('int32Arg1'), 0, 99, allow_integral_float=True)
            if cell_index is None:
                return False
            request['cell_index'] = cell_index

        sender = getattr(self._lan_sender, 'send_team_command', None)
        if not callable(sender):
            raise AttributeError('LAN sender.send_team_command')
        command_seq = _exact_int(sender(dict(request)), 1)
        if command_seq is None:
            self._publish_response(request_id, False)
            return False
        if command_seq in self._pending:
            raise RuntimeError('duplicate team command sequence')
        self._pending[command_seq] = request
        return True

    def receive_ack(self, command_seq, accepted,
                    responder_account_dbids=None):
        """Complete a sent request and render one deterministic Bot reply."""
        if not self._is_current_avatar() or not isinstance(accepted, bool):
            return False
        command_seq = _exact_int(command_seq, 1)
        request = self._pending.pop(command_seq, None)
        if request is None:
            return False
        self._publish_response(request['stock_request_id'], accepted)
        if not accepted:
            return True

        # The reliable same-team broadcast owns the stock command echo.  The
        # acknowledgement only closes the request and presents one assigned
        # Bot's positive response, so delivery order cannot duplicate the
        # issuer's command in chat or on the minimap.
        for account_dbid in responder_account_dbids or ():
            account_dbid = _exact_int(account_dbid, 1)
            if (account_dbid is not None and
                    self._valid_sender(account_dbid)):
                self.receive_command('POSITIVE', account_dbid)
                break
        return True

    def receive_command(self, command, sender_account_dbid, target_id=None,
                        cell_index=None):
        """Publish one validated team command through the stock receive path."""
        if not self._is_current_avatar() or command not in COMMAND_IDS_BY_NAME:
            return False
        sender_account_dbid = _exact_int(sender_account_dbid, 1)
        if (sender_account_dbid is None or
                not self._valid_sender(sender_account_dbid)):
            return False
        action_id = COMMAND_IDS_BY_NAME[command]
        unused_name, target_kind = COMMAND_SPECS[action_id]
        int32_arg = 0
        if target_kind in (TARGET_ALLY, TARGET_ENEMY):
            target_id = _exact_int(target_id, 1)
            if target_id is None or not self._valid_target(
                    target_id, target_kind):
                return False
            int32_arg = target_id
        elif target_kind == TARGET_CELL:
            cell_index = _exact_int(
                cell_index, 0, 99, allow_integral_float=True)
            if cell_index is None:
                return False
            int32_arg = cell_index
        elif target_id is not None or cell_index is not None:
            return False

        callback = getattr(
            self._avatar, 'messenger_onActionByServer_chat2', None)
        if not callable(callback):
            raise AttributeError(
                'Avatar.messenger_onActionByServer_chat2')
        callback(action_id, 0, _message_args(
            int32_arg=int32_arg, int64_arg=sender_account_dbid))
        return True

    def _publish_response(self, request_id, accepted):
        callback = getattr(
            self._avatar, 'messenger_onActionByServer_chat2', None)
        if not callable(callback):
            raise AttributeError(
                'Avatar.messenger_onActionByServer_chat2')
        action_id = (RESPONSE_SUCCESS_ACTION_ID if accepted else
                     RESPONSE_FAILURE_ACTION_ID)
        error_id = 0 if accepted else GENERIC_ERROR_ID
        callback(action_id, request_id, _message_args(int32_arg=error_id))

    def _is_current_avatar(self):
        if self._closed or self._avatar is None:
            return False
        if self._player_getter is None:
            return True
        try:
            return self._player_getter() is self._avatar
        except (AttributeError, ReferenceError):
            return False

    def _player_vehicle_id(self):
        return _exact_int(getattr(self._avatar, 'playerVehicleID', None), 1)

    def _vehicle_info(self, vehicle_id):
        arena = getattr(self._avatar, 'arena', None)
        vehicles = getattr(arena, 'vehicles', None)
        if not isinstance(vehicles, dict):
            return None
        info = vehicles.get(vehicle_id)
        return info if isinstance(info, dict) else None

    def _valid_sender(self, account_dbid):
        info = None
        arena = getattr(self._avatar, 'arena', None)
        vehicles = getattr(arena, 'vehicles', None)
        if isinstance(vehicles, dict):
            for candidate in vehicles.values():
                if (isinstance(candidate, dict) and
                        _exact_int(candidate.get('accountDBID'), 1) ==
                        account_dbid):
                    info = candidate
                    break
        own_team = _exact_int(getattr(self._avatar, 'team', None), 1, 2)
        return bool(info is not None and own_team is not None and
                    _exact_int(info.get('team'), 1, 2) == own_team)

    def _valid_target(self, vehicle_id, target_kind):
        info = self._vehicle_info(vehicle_id)
        own_team = _exact_int(getattr(self._avatar, 'team', None), 1, 2)
        target_team = (_exact_int(info.get('team'), 1, 2)
                       if info is not None else None)
        if (own_team is None or target_team is None or
                not info.get('isAlive', True)):
            return False
        if target_kind == TARGET_ALLY:
            return (target_team == own_team and
                    vehicle_id != self._player_vehicle_id())
        if target_kind == TARGET_ENEMY:
            return target_team != own_team
        return False
