"""#1513 AccountCommands.pyc values used by the minimal offline RPC surface."""

RES_FAILURE = -1
RES_SUCCESS = 0
RES_STREAM = 1

# #1513 derives these from streamIDs.STREAM_ID_ACCOUNT_CMDS_MIN (200), not from
# zero: REQUEST_ID_NO_RESPONSE is 202 and REQUEST_ID_UNRESERVED_MIN is 220.
# Account.__getRequestID only ever allocates ids at or above the unreserved
# minimum, so a reserved id can never match a response callback.
REQUEST_ID_NO_RESPONSE = 202
REQUEST_ID_UNRESERVED_MIN = 220

CMD_SYNC_DATA = 100
# Fitting surface, verified against this build's AccountCommands.pyc.
CMD_EQUIP = 101
CMD_EQUIP_OPTDEV = 102
CMD_EQUIP_SHELLS = 103
CMD_EQUIP_EQS = 104
CMD_VEH_SETTINGS = 107
CMD_SET_AND_FILL_LAYOUTS = 108
# Customization 2.0, verified against #1513 AccountCommands.pyc/Shop.pyc.
CMD_VEH_APPLY_STYLE = 116
CMD_SELL_C11N_ITEMS = 117
CMD_BUY_C11N_ITEMS = 118
CMD_VEH_APPLY_OUTFIT = 119
CMD_TMAN_ADD_SKILL = 151
CMD_TMAN_DROP_SKILLS = 152
CMD_TRAINING_TMAN = 155
CMD_SYNC_SHOP = 300
CMD_BUY_ITEM = 302
CMD_BUY_AND_EQUIP_ITEM = 308
CMD_REQ_SERVER_STATS = 501
CMD_SYNC_DOSSIERS = 600
CMD_ENQUEUE_RANDOM = 700
CMD_DEQUEUE_RANDOM = 701
CMD_SET_LANGUAGE = 1000
CMD_COMPLETE_TUTORIAL = 1150
# #1513 BattleResultsCache.get/stream-save acknowledgement.
CMD_REQ_BATTLE_RESULTS = 1500
CMD_BATTLE_RESULTS_RECEIVED = 1501
CMD_ADD_INT_USER_SETTINGS = 1600
CMD_DEL_INT_USER_SETTINGS = 1601

# constants.pyc QUEUE_TYPE.RANDOMS, consumed by Account.onEnqueued/onDequeued.
QUEUE_TYPE_RANDOMS = 1
