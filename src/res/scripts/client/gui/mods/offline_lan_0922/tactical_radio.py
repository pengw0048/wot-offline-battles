"""Bridge the reviewed #1513 team chat and commands to the LAN protocol.

The stock client owns text entry, normalization, cooldowns, chat formatting,
sounds, vehicle markers, and minimap feedback.  This adapter only translates
the reviewed payloads at the missing base-mailbox boundary and sends accepted
server results back through ``Avatar.messenger_onActionByServer_chat2``.
"""

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle
import unicodedata
import zlib
import struct

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)
try:
    _UNICODE_TYPE = unicode
    _BYTE_STRING_TYPE = str
    _BINARY_TYPES = (str, bytearray)
except NameError:
    _UNICODE_TYPE = str
    _BYTE_STRING_TYPE = bytes
    _BINARY_TYPES = (bytes, bytearray)


RESPONSE_SUCCESS_ACTION_ID = 0
RESPONSE_FAILURE_ACTION_ID = 1
GENERIC_ERROR_ID = 1
MAX_STOCK_REQUEST_ID = 32766

TEAM_CHAT_INIT_ACTION_ID = 19
TEAM_CHAT_DEINIT_ACTION_ID = 20
TEAM_CHAT_SEND_ACTION_ID = 21
TEAM_CHAT_RECEIVE_ACTION_ID = 22
MAX_TEAM_CHAT_LENGTH = 140
MAX_TEAM_CHAT_UTF16_UNITS = MAX_TEAM_CHAT_LENGTH

TARGET_NONE = None
TARGET_ALLY = 'ally'
TARGET_ENEMY = 'enemy'
TARGET_CELL = 'cell'
TARGET_AIM_AREA = 'aim_area'

MAX_COMMAND_RELOAD_TIME = 3600.0
MAX_COMMAND_QUANTITY = 255
COMMAND_AIM_POINT_LOW = (-5000.0, -5000.0, -5000.0)
COMMAND_AIM_POINT_HIGH = (5000.0, 5000.0, 5000.0)

# ``BATTLE_CHAT_COMMANDS`` action IDs from the reviewed local #1513 archive.
COMMAND_SPECS = {
    23: ('HELPME', TARGET_NONE),
    24: ('FOLLOWME', TARGET_ALLY),
    25: ('ATTACK', TARGET_NONE),
    26: ('BACKTOBASE', TARGET_NONE),
    27: ('POSITIVE', TARGET_NONE),
    28: ('NEGATIVE', TARGET_NONE),
    29: ('ATTENTIONTOCELL', TARGET_CELL),
    30: ('SPG_AIM_AREA', TARGET_AIM_AREA),
    31: ('ATTACKENEMY', TARGET_ENEMY),
    32: ('TURNBACK', TARGET_ALLY),
    33: ('HELPMEEX', TARGET_ALLY),
    34: ('SUPPORTMEWITHFIRE', TARGET_ENEMY),
    35: ('RELOADINGGUN', TARGET_NONE),
    36: ('STOP', TARGET_ALLY),
    37: ('RELOADING_CASSETE', TARGET_NONE),
    38: ('RELOADING_READY', TARGET_NONE),
    39: ('RELOADING_READY_CASSETE', TARGET_NONE),
    40: ('RELOADING_UNAVAILABLE', TARGET_NONE),
}
COMMAND_IDS_BY_NAME = dict(
    (spec[0], action_id) for action_id, spec in COMMAND_SPECS.items())

# Value shape is ``(required fields, optional fields)``.  Cell and entity
# targets remain top-level adapter fields because they already have dedicated
# LAN projection paths.
COMMAND_DETAIL_FIELDS = {
    'SPG_AIM_AREA': (('aim_point', 'reload_time'), ()),
    'ATTACKENEMY': ((), ('reload_time',)),
    'RELOADINGGUN': (('reload_time',), ()),
    'RELOADING_CASSETE': (('reload_time', 'quantity'), ()),
    'RELOADING_READY_CASSETE': (('quantity',), ()),
}

_MESSAGE_ARG_NAMES = (
    'int32Arg1', 'int64Arg1', 'floatArg1', 'strArg1', 'strArg2')


def _unicode_text(value):
    if isinstance(value, _UNICODE_TYPE):
        return value
    if isinstance(value, _BINARY_TYPES):
        try:
            return value.decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            return None
    return None


def normalize_team_chat_text(value):
    """Return the stock outgoing filter's canonical JSON Unicode text."""
    if isinstance(value, _UNICODE_TYPE):
        stripped = value.strip()
    elif isinstance(value, _BINARY_TYPES):
        stripped = value.strip()
    else:
        return None
    text = _unicode_text(stripped)
    if text is None:
        return None
    try:
        text = unicodedata.normalize('NFKC', text)
        # The supported 32-bit Windows CPython 2.7 build uses narrow Unicode,
        # so the stock slice counts UTF-16 code units.  Reproduce that limit
        # on wide-Unicode audit and server interpreters as well.
        utf16 = text.encode('utf-16-le')
        utf16 = utf16[:MAX_TEAM_CHAT_UTF16_UNITS * 2]
        text = utf16.decode('utf-16-le')
        # The exact Python 2 filter encodes after truncation, then collapses
        # whitespace on the UTF-8 byte string.
        encoded = text.encode('utf-8')
        encoded = b' '.join(encoded.split())
        text = encoded.decode('utf-8')
    except (TypeError, UnicodeDecodeError, UnicodeEncodeError):
        return None
    return text if text else None


def is_valid_team_chat_text(value):
    """Validate plaintext without re-normalizing it under the host UCD."""
    if not isinstance(value, _UNICODE_TYPE):
        return False
    try:
        utf16 = value.encode('utf-16-le')
        value.encode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return False
    units = len(utf16) // 2
    return bool(value.strip()) and 0 < units <= MAX_TEAM_CHAT_UTF16_UNITS


def _bounded_number(value, minimum, maximum, inclusive_minimum=True):
    if isinstance(value, bool) or not isinstance(
            value, _INTEGER_TYPES + (float,)):
        return None
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if result != result or result in (float('inf'), float('-inf')):
        return None
    if ((inclusive_minimum and result < minimum) or
            (not inclusive_minimum and result <= minimum) or
            result > maximum):
        return None
    return result


def validate_command_details(command, details):
    """Validate optional LAN fields derived from exact stock command args."""
    if command not in COMMAND_IDS_BY_NAME:
        return False
    fields = COMMAND_DETAIL_FIELDS.get(command)
    if fields is None:
        return details is None or details == {}
    required, optional = fields
    if details is None:
        details = {}
    if not isinstance(details, dict):
        return False
    keys = set(details)
    if not set(required).issubset(keys) or not keys.issubset(
            set(required + optional)):
        return False
    if 'reload_time' in details:
        inclusive = command in ('SPG_AIM_AREA', 'ATTACKENEMY')
        if _bounded_number(
                details['reload_time'], 0.0, MAX_COMMAND_RELOAD_TIME,
                inclusive_minimum=inclusive) is None:
            return False
    if 'quantity' in details:
        if _exact_int(
                details['quantity'], 1, MAX_COMMAND_QUANTITY) is None:
            return False
    if 'aim_point' in details:
        point = details['aim_point']
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            return False
        for value, minimum, maximum in zip(
                point, COMMAND_AIM_POINT_LOW, COMMAND_AIM_POINT_HIGH):
            if _bounded_number(value, minimum, maximum) is None:
                return False
    return True


def _empty_arena_history():
    # ``ArenaHistoryIterator`` calls z_loads whenever strArg1 exists.  The
    # stock five-key mapping therefore needs a real compressed empty list;
    # its ordinary empty-string default would produce a non-iterable None.
    return zlib.compress(_pickle.dumps([], 2), 1)


def _notify_stock_ignore_lists_ready():
    """Release the stock arena-message queue after offline chat startup."""
    from messenger.m_constants import USER_TAG
    from messenger.proto.events import g_messengerEvents

    # ArenaChatHandler only needs completion of the two ignore rosters.  This
    # event preserves cached user entities and their existing tags; it does not
    # claim that unrelated friends or muted rosters were refreshed.
    g_messengerEvents.users.onUsersListReceived(set((
        USER_TAG.IGNORED, USER_TAG.IGNORED_TMP)))


def _schedule_next_frame(delay, callback):
    import BigWorld
    return BigWorld.callback(delay, callback)


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


def _stock_args_are_default(args, allowed=()):
    allowed = set(allowed)
    if ('int32Arg1' not in allowed and
            _exact_int(args.get('int32Arg1')) != 0):
        return False
    if ('int64Arg1' not in allowed and
            _exact_int(args.get('int64Arg1')) != 0):
        return False
    if ('floatArg1' not in allowed and
            _bounded_number(args.get('floatArg1'), 0.0, 0.0) is None):
        return False
    for name in ('strArg1', 'strArg2'):
        if name not in allowed and args.get(name) not in ('', u'', b''):
            return False
    return True


def _stock_command_details(action_id, args):
    """Return ``(details, packed_cell)`` or ``None`` for invalid stock args."""
    command, relation = COMMAND_SPECS[action_id]
    allowed = []
    if relation in (TARGET_ALLY, TARGET_ENEMY, TARGET_CELL):
        allowed.append('int32Arg1')
    if action_id in (31, 35, 37):
        allowed.append('floatArg1')
    if action_id in (37, 39):
        allowed.append('int32Arg1')
    if action_id == 30:
        allowed.append('strArg1')
    if not _stock_args_are_default(args, allowed):
        return None

    details = {}
    packed_cell = None
    if action_id == 30:
        record = args.get('strArg1')
        if not isinstance(record, _BYTE_STRING_TYPE):
            return None
        try:
            x, y, z, packed_cell, reload_time = struct.unpack(
                '<fffif', record)
        except (TypeError, ValueError, struct.error):
            return None
        packed_cell = _exact_int(packed_cell, 0, 99)
        details = {
            'aim_point': [float(x), float(y), float(z)],
            'reload_time': float(reload_time),
        }
    elif action_id == 31:
        reload_time = _bounded_number(
            args.get('floatArg1'), 0.0, MAX_COMMAND_RELOAD_TIME)
        if reload_time is None:
            return None
        if reload_time > 0.0:
            details['reload_time'] = reload_time
    elif action_id in (35, 37):
        reload_time = _bounded_number(
            args.get('floatArg1'), 0.0, MAX_COMMAND_RELOAD_TIME,
            inclusive_minimum=False)
        if reload_time is None:
            return None
        details['reload_time'] = reload_time
        if action_id == 37:
            quantity = _exact_int(
                args.get('int32Arg1'), 1, MAX_COMMAND_QUANTITY)
            if quantity is None:
                return None
            details['quantity'] = quantity
    elif action_id == 39:
        quantity = _exact_int(
            args.get('int32Arg1'), 1, MAX_COMMAND_QUANTITY)
        if quantity is None:
            return None
        details['quantity'] = quantity
    if packed_cell is None and action_id == 30:
        return None
    if not validate_command_details(command, details):
        return None
    return details, packed_cell


class BattleRadioAdapter(object):
    """Own one Avatar's fixed-command translation and response lifecycle."""

    def __init__(self, avatar, lan_sender, player_getter=None,
                 users_ready_notifier=None, callback_scheduler=None):
        self._avatar = avatar
        self._lan_sender = lan_sender
        self._player_getter = player_getter
        self._pending = {}
        self._pending_chat = {}
        self._team_chat_started = False
        self._users_ready_notifier = (
            users_ready_notifier or _notify_stock_ignore_lists_ready)
        self._callback_scheduler = (
            callback_scheduler or _schedule_next_frame)
        self._callback_generation = object()
        self._closed = False

    def start_team_chat(self):
        """Create the stock TEAM and COMMON arena channels exactly once."""
        if not self._is_current_avatar() or self._team_chat_started:
            return False
        self._team_chat_started = True
        try:
            self._publish_action(
                TEAM_CHAT_INIT_ACTION_ID, 0,
                _message_args(str_arg1=_empty_arena_history()))
            # Retail receives this event from XMPP contacts cache/sequence
            # startup or its retry fallback.  The offline account has none of
            # those producers; without it action 22 stays queued forever.
            self._users_ready_notifier()
        except Exception:
            # ArenaChatHandler marks itself initialized before creating its
            # two channels.  Retain teardown ownership if the second channel
            # or its synchronous controller event raises.
            raise
        return True

    def close_team_chat(self):
        """Remove the stock arena channels before Avatar GUI teardown."""
        # This is the runtime's actual presentation-retirement boundary.
        # Fence queued rejection callbacks and forget requests before action 20
        # can synchronously run stock channel-destroy listeners.
        self._callback_generation = object()
        self._pending = {}
        self._pending_chat = {}
        if not self._team_chat_started:
            return False
        if not self._is_current_avatar():
            self._team_chat_started = False
            return False
        try:
            self._publish_action(
                TEAM_CHAT_DEINIT_ACTION_ID, 0, _message_args())
        except Exception:
            # ``leave`` removes TEAM and COMMON sequentially.  A retry is
            # required when a synchronous destroy listener fails in between.
            raise
        self._team_chat_started = False
        return True

    def close(self):
        if self._closed:
            return False
        try:
            self.close_team_chat()
        except Exception:
            # Channel presentation is already retiring.  A late stock GUI
            # failure must not retain the Avatar or its LAN request identities.
            pass
        self._closed = True
        # Native cancellation is unnecessary: rotating this token makes every
        # already-scheduled response harmless after teardown.
        self._callback_generation = object()
        self._pending = {}
        self._pending_chat = {}
        self._avatar = None
        self._lan_sender = None
        self._player_getter = None
        self._users_ready_notifier = None
        self._callback_scheduler = None
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
        is_supported = (action_id == TEAM_CHAT_SEND_ACTION_ID or
                        action_id in COMMAND_SPECS)
        if request_id is None:
            return False
        if not isinstance(args, dict) or set(args) != set(_MESSAGE_ARG_NAMES):
            if is_supported:
                self._defer_response(request_id, False)
            return False
        if action_id == TEAM_CHAT_SEND_ACTION_ID:
            return self._handle_team_chat(request_id, args)
        spec = COMMAND_SPECS.get(action_id)
        if spec is None:
            return False

        command, target_kind = spec
        parsed_details = _stock_command_details(action_id, args)
        if parsed_details is None:
            self._defer_response(request_id, False)
            return False
        details, packed_cell = parsed_details
        request = {
            'command': command,
            'stock_action_id': action_id,
            'stock_request_id': request_id,
        }
        if details:
            request['details'] = details
        if target_kind in (TARGET_ALLY, TARGET_ENEMY):
            target_id = _exact_int(args.get('int32Arg1'), 1)
            if target_id is None or not self._valid_target(
                    target_id, target_kind):
                self._defer_response(request_id, False)
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
                self._defer_response(request_id, False)
                return False
            request['cell_index'] = cell_index
        elif target_kind == TARGET_AIM_AREA:
            request['cell_index'] = packed_cell

        sender = getattr(self._lan_sender, 'send_team_command', None)
        if not callable(sender):
            raise AttributeError('LAN sender.send_team_command')
        command_seq = _exact_int(sender(dict(request)), 1)
        if command_seq is None:
            self._defer_response(request_id, False)
            return False
        if command_seq in self._pending:
            raise RuntimeError('duplicate team command sequence')
        self._pending[command_seq] = request
        return True

    def _handle_team_chat(self, request_id, args):
        if (not self._team_chat_started or
                _exact_int(args.get('int32Arg1')) != 0 or
                _exact_int(args.get('int64Arg1')) != 0 or
                args.get('floatArg1') not in (0, 0.0) or
                args.get('strArg2') not in ('', u'')):
            self._defer_response(request_id, False)
            return False
        text = normalize_team_chat_text(args.get('strArg1'))
        if text is None:
            self._defer_response(request_id, False)
            return False
        sender = getattr(self._lan_sender, 'send_team_chat', None)
        if not callable(sender):
            raise AttributeError('LAN sender.send_team_chat')
        chat_seq = _exact_int(sender({'text': text}), 1)
        if chat_seq is None:
            self._defer_response(request_id, False)
            return False
        if chat_seq in self._pending_chat:
            raise RuntimeError('duplicate team chat sequence')
        self._pending_chat[chat_seq] = request_id
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

    def receive_chat_ack(self, chat_seq, accepted):
        """Complete one stock text request without duplicating its relay."""
        if not self._is_current_avatar() or not isinstance(accepted, bool):
            return False
        chat_seq = _exact_int(chat_seq, 1)
        request_id = self._pending_chat.pop(chat_seq, None)
        if request_id is None:
            return False
        self._publish_response(request_id, accepted)
        return True

    def receive_team_chat(self, text, sender_account_dbid):
        """Publish one valid same-team plaintext message through stock UI."""
        if (not self._is_current_avatar() or
                not self._team_chat_started or
                not is_valid_team_chat_text(text)):
            return False
        sender_account_dbid = _exact_int(sender_account_dbid, 1)
        if (sender_account_dbid is None or
                not self._valid_sender(sender_account_dbid)):
            return False
        self._publish_action(
            TEAM_CHAT_RECEIVE_ACTION_ID, 0,
            _message_args(int32_arg=0, int64_arg=sender_account_dbid,
                          str_arg1=text))
        return True

    def receive_command(self, command, sender_account_dbid, target_id=None,
                        cell_index=None, details=None):
        """Publish one validated team command through the stock receive path."""
        if (not self._is_current_avatar() or
                not validate_command_details(command, details)):
            return False
        details = details or {}
        sender_account_dbid = _exact_int(sender_account_dbid, 1)
        if (sender_account_dbid is None or
                not self._valid_sender(sender_account_dbid)):
            return False
        action_id = COMMAND_IDS_BY_NAME[command]
        unused_name, target_kind = COMMAND_SPECS[action_id]
        int32_arg = 0
        float_arg = 0.0
        str_arg1 = ''
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
        elif target_kind == TARGET_AIM_AREA:
            cell_index = _exact_int(
                cell_index, 0, 99, allow_integral_float=True)
            if cell_index is None or target_id is not None:
                return False
            point = details['aim_point']
            str_arg1 = struct.pack(
                '<fffif', float(point[0]), float(point[1]), float(point[2]),
                cell_index, float(details['reload_time']))
        elif target_id is not None or cell_index is not None:
            return False

        if 'reload_time' in details and action_id != 30:
            float_arg = float(details['reload_time'])
        if 'quantity' in details:
            int32_arg = details['quantity']

        self._publish_action(action_id, 0, _message_args(
            int32_arg=int32_arg, int64_arg=sender_account_dbid,
            float_arg=float_arg, str_arg1=str_arg1))
        return True

    def _publish_response(self, request_id, accepted):
        action_id = (RESPONSE_SUCCESS_ACTION_ID if accepted else
                     RESPONSE_FAILURE_ACTION_ID)
        error_id = 0 if accepted else GENERIC_ERROR_ID
        self._publish_action(
            action_id, request_id, _message_args(int32_arg=error_id))

    def _defer_response(self, request_id, accepted):
        """Respond after stock has registered the synchronous request ID."""
        generation = self._callback_generation

        def publish():
            if (generation is self._callback_generation and
                    self._is_current_avatar()):
                self._publish_response(request_id, accepted)

        self._callback_scheduler(0.0, publish)

    def _publish_action(self, action_id, request_id, args):
        callback = getattr(
            self._avatar, 'messenger_onActionByServer_chat2', None)
        if not callable(callback):
            raise AttributeError(
                'Avatar.messenger_onActionByServer_chat2')
        callback(action_id, request_id, args)

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
