#!/usr/bin/env python3
"""Small LAN battle server for the supported offline LAN clients.

This is deliberately a separate, dependency-free process.  It is the first
network slice, not an implementation of the original BigWorld server
protocol.  Clients connect with the companion network mod and exchange
newline-delimited JSON messages.

The server owns the shared player state, relayed positions, team assignment
and validated hit/health events.  The existing client remains responsible for
rendering the original map/tank assets, resolving map/armor collisions and the
local garage.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import random
import re
import os
import sys
import socket
import socketserver
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_PORT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_CLIENT_SCRIPT_ROOT = os.path.join(
    _PORT_ROOT, 'src', 'res', 'scripts', 'client')
if _CLIENT_SCRIPT_ROOT not in sys.path:
    sys.path.insert(0, _CLIENT_SCRIPT_ROOT)

from server_bot_ai import BotPlanner
from offline_rewards import compute_offline_rewards
from vehicle_overlay_store import (
    MAX_OVERLAY_MEMBER_BYTES,
    VehicleOverlayStore,
    VehicleOverlayStoreError,
)
from gui.mods.offline_lan_0922 import tank_collision
from gui.mods.offline_lan_0922 import burst_mechanics
from gui.mods.offline_lan_0922 import effective_params as effective_params_wire
from gui.mods.offline_lan_0922 import equipment_mechanics
from gui.mods.offline_lan_0922 import device_damage
from gui.mods.offline_lan_0922 import player_critical_mechanics
from gui.mods.offline_lan_0922 import siege_mechanics
from gui.mods.offline_lan_0922 import spotting
from gui.mods.offline_lan_0922 import vehicle_physics
from gui.mods.offline_lan_0922.ai import planner as bot_planner
from gui.mods.offline_lan_0922.ai.maps import get_tactical_map
from gui.mods.offline_lan_0922.ai.maps_0922_extra import (
    TACTICAL_MAPS_0922_EXTRA as _MAPS_0922_DATA,
)
from gui.mods.offline_lan_0922.navigation_graph_schema import (
    SUPPORTED_MAPS as _SUPPORTED_MAPS_0922,
)


PROTOCOL_VERSION = 5
TICK_HZ = 30.0
# #1513 ingests observations and damage immediately, but only needs one
# full-team tactical order synthesis per second.
BOT_PLANNER_INTERVAL_TICKS = max(1, int(round(TICK_HZ)))
REPLICA_SNAPSHOT_HZ = 15.0
REPLICA_SNAPSHOT_TICKS = max(
    1, int(round(TICK_HZ / REPLICA_SNAPSHOT_HZ)))
RESULT_RESET_SECONDS = 5.0
MAX_TICK_FAILURE_DIAGNOSTIC_CHARS = 512
MAX_CONSECUTIVE_TICK_FAILURES = 2
PREBATTLE_SECONDS = 15.0
BATTLE_DURATION_SECONDS = 900.0
PLAYER_DROWNING_SECONDS = 10.0
PLAYER_OVERTURN_IGNORE_SECONDS = 0.10
PLAYER_OVERTURN_DEATH_SECONDS = 30.0
PLAYER_ENVIRONMENT_STALE_TICKS = int(round(TICK_HZ))
PLAYER_LANDING_MAX_IMPACT_SPEED = 200.0
PLAYER_LANDING_HISTORY = 64
BOT_FIRE_DURATION_SECONDS = 10.0
BOT_FIRE_TICK_SECONDS = 1.0
MAX_LINE_BYTES = 256 * 1024
# Overlay member payloads travel as one base64 JSON line; the store bounds
# member size, so the line cap is the base64 expansion plus framing slack.
MAX_OVERLAY_LINE_BYTES = MAX_OVERLAY_MEMBER_BYTES * 4 // 3 + 1024 * 1024
MAX_RELIABLE_OUTBOUND_MESSAGES = 64
MAX_RELIABLE_OUTBOUND_BYTES = 4 * 1024 * 1024
OUTBOUND_SYNC_TIMEOUT_SECONDS = 2.0
OUTBOUND_STALL_TIMEOUT_SECONDS = 5.0
MAX_PENDING_RAM_CONTACTS = 32
MAX_PENDING_PLAYER_DESTRUCTIBLE_CONTACTS = 16
MAX_PLAYER_DESTRUCTIBLE_REJECTIONS = 16
MAX_PLAYER_DESTRUCTIBLE_CONTACT_TOKEN = 64
MAX_PLAYER_DESTRUCTIBLE_INFLIGHT = 64
# A 0.1-second copied-physics step can legitimately rotate a fast light tank
# by several tenths of a radian.  This envelope is deliberately wider than
# every #1513 descriptor while still bounding an untrusted swept-pose payload.
MAX_PLAYER_DESTRUCTIBLE_ANGULAR_SPEED = 8.0
MAX_PLAYER_DESTRUCTIBLE_VERTICAL_TRAVEL = 0.25
MAX_PLAYER_DESTRUCTIBLE_LINEAR_SLOP = 0.25
MAX_PLAYER_INPUT_FINGERPRINTS = 128
MAX_PLAYER_INPUT_DECISIONS = 128
MAX_PLAYER_INPUT_REJECT_REASONS = 32
MAX_PLAYER_EQUIPMENT_FINGERPRINTS = 64
MAX_CRITICAL_DEVICE_HP = effective_params_wire.MAX_CRITICAL_DEVICE_HP
HUMAN_POSE_HISTORY_SECONDS = 1.5
HUMAN_RAM_MAX_SUBSTEP_US = 100000
HUMAN_POSE_CLOCK_LEEWAY_US = 250000
MAX_HUMAN_RAM_PROBES = 64
MAX_HUMAN_RAM_PROBE_HISTORY = 256
MAX_RESULT_RECEIPTS = 256
RESULT_RECEIPT_STATE_SCHEMA = 1
RESULT_RECEIPT_STATE_FILE = "unacked_battle_receipts.json"
RESULT_INTERACTION_LIMITS = {
    "spotted": (0, 1),
    "death_reason": (-1, 10),
    "direct_hits": (0, 65535),
    "explosion_hits": (0, 65535),
    "piercings": (0, 65535),
    "damage": (0, 65535),
    "assist_track": (0, 65535),
    "assist_radio": (0, 65535),
    "assist_stun": (0, 65535),
    "crits": (0, 4294967295),
    "fire": (0, 65535),
    "stun_num": (0, 65535),
    "stun_duration": (0, 65535),
    "damage_blocked": (0, 4294967295),
    "damage_received": (0, 65535),
    "ricochets_received": (0, 65535),
    "no_damage_direct_hits_received": (0, 65535),
    "target_kills": (0, 255),
}
DEFAULT_MAP = "server_random"
CLIENT_BUILD_082 = "wot-0.8.2"
CLIENT_BUILD_0922 = "wot-0.9.22.0.1-cn-1513"
MAP_POOL_082 = (
    "01_karelia",
    "02_malinovka",
    "03_campania",
    "04_himmelsdorf",
    "05_prohorovka",
    "06_ensk",
    "07_lakeville",
    "08_ruinberg",
    "10_hills",
    "11_murovanka",
    "13_erlenberg",
    "14_siegfried_line",
    "15_komarin",
    "17_munchen",
    "18_cliff",
    "19_monastery",
    "22_slough",
    "23_westfeld",
    "28_desert",
    "29_el_hallouf",
    "31_airfield",
    "33_fjord",
    "34_redshire",
    "35_steppes",
    "36_fishing_bay",
    "37_caucasus",
    "38_mannerheim_line",
    "39_crimea",
    "42_north_america",
    "44_north_america",
    "45_north_america",
    "47_canada_a",
    "51_asia",
)
MAP_POOL_0922 = tuple(_SUPPORTED_MAPS_0922)
MAP_POOL = MAP_POOL_0922
CLIENT_MAP_POOLS = {
    CLIENT_BUILD_082: MAP_POOL_082,
    CLIENT_BUILD_0922: MAP_POOL_0922,
}
CLIENT_DEFAULT_VEHICLES = {
    CLIENT_BUILD_082: "ussr:MS-1",
    CLIENT_BUILD_0922: "ussr:R11_MS-1",
}
ALL_MAP_POOL = tuple(sorted(set(MAP_POOL_082 + MAP_POOL_0922)))
BOT_CALLSIGNS = (
    "Atlas", "Badger", "Bison", "Cedar", "Comet", "Condor", "Coyote", "Dagger",
    "Echo", "Falcon", "Frost", "Golem", "Harbor", "Hawk", "Ibis", "Jade",
    "Kestrel", "Lancer", "Lynx", "Mantis", "Maple", "Meteor", "Nomad", "Onyx",
    "Orion", "Otter", "Panda", "Quartz", "Raven", "Rook", "Saber", "Scout",
    "Shark", "Sparrow", "Talon", "Tiger", "Viper", "Wolf", "Yak", "Zephyr",
)
BOT_CALLSIGNS_0922 = (
    # Keep some familiar period names, but draw most rosters from a broader
    # mixture of tactical, everyday, regional, poetic, and playful identities.
    # These are complete display names; #1513 rosters select them without the
    # former mandatory ``-NN`` suffix.
    "暗夜猎手", "百步穿杨", "北方孤狼", "苍穹之刃", "乘风破浪", "赤色彗星",
    "此路不通", "东风破", "风卷残云", "钢铁洪流", "黑色闪电", "火力全开",
    "落叶随风", "千里走单骑", "铁骑纵横", "一炮入魂", "战场幽灵", "亮剑",
    "山海之间", "晚风过境", "雨打芭蕉", "星河入梦", "月落长安", "云起无声",
    "雾里看山", "松间明月", "江畔听风", "北纬三十度", "夏夜微凉",
    "冬日余温", "南山有雾", "海边拾光", "风停在七月", "萤火未眠",
    "青石小巷", "长街听雪", "天边一朵云", "山谷有回声", "清晨第一班",
    "今天不加班", "装填中别催", "履带又掉了", "草丛观察员", "开局先喝茶",
    "路过打两炮", "别撞我谢谢", "炮弹已充值", "维修费好贵", "我先探个路",
    "等我缩圈", "这炮能中", "再来一局", "慢慢开别急", "看见我请鸣笛",
    "一颗备用螺丝", "车库常驻", "随缘开炮", "今天手感一般", "先瞄五秒钟",
    "不是故意空炮", "小地图看一眼", "这里不能停车", "刚修好的履带",
    "我在等装填", "前方可能有车", "先让我亮一下", "别急马上到",
    "卖头不卖队友", "坡后观察员", "城区慢车", "山口守门员", "南线巡逻车",
    "北线压路机", "中路看风景", "履带修理工", "炮塔保养员", "弹药架管理员",
    "车长在路上", "侧甲有点薄", "头铁但谨慎", "老车也能跑", "轻坦不迷路",
    "重坦慢慢来", "火炮搬运工", "反坦克盆栽", "草丛里的眼睛",
    "最后一发留着", "别催正在转向", "坡顶先别上", "队友正在赶来",
    "岭南夜雨", "塞北孤烟", "江南旧梦", "川西来客", "齐鲁小队长",
    "关中老车长", "东北暖气足", "海西观潮", "燕山脚下", "天府慢行",
    "黄河拐个弯", "珠江晚风", "太湖边上", "昆仑看雪", "长白山下",
    "松花江畔", "橘子汽水", "半糖乌龙", "芝麻汤圆", "盐焗小星球",
    "一只纸飞机", "小熊不冬眠", "猫在看小地图", "企鹅修履带",
    "河豚正在装填", "海盐苏打", "薄荷气泡", "柠檬不太酸", "栗子开坦克",
    "土豆观察员", "松鼠搬炮弹", "熊猫慢速前进", "麻雀侦察队", "海鸥绕着飞",
    "山雀", "白榆", "青禾", "知夏", "归舟", "远岚", "南枝", "拾光",
    "未央", "长风", "星野", "凌晨四点", "七号车组", "三号观察位",
    "四零四号履带", "八十八号停车位", "半格信号", "两杯热茶",
)
ROUND_SCOPED_MESSAGE_TYPES = frozenset((
    "start_battle", "input", "hit_report", "bot_manifest", "bot_state",
    "bot_observation", "bot_hit_report", "bot_human_hit", "bot_ram_report",
    "spotted_report", "fire_intent", "fire_intent_result",
    "projectile_launch", "projectile_progress", "projectile_ricochet",
    "projectile_resolve",
    "rules_state", "destructible",
    "player_destructible_contact_result",
    "battle_result", "leave_battle", "battle_ready", "simulation_progress",
    "player_environment", "landing_observation",
    "track_repair",
    "equipment_intent",
))
MODERN_VISIBLE_MESSAGE_TYPES = frozenset((
    "input", "fire_intent", "landing_observation", "start_battle",
    "battle_ready", "leave_battle",
    "battle_receipt_ack", "descriptor_catalog", "select_vehicle",
    "select_team", "set_team_size", "set_bot_tier_mode",
    "ping", "leave",
    "track_repair",
    "equipment_intent",
))
# The elected #1513 authority uses the same bounded in-process manager.  The
# server must never admit more durable launches than a takeover client can
# restore and simulate.
PROJECTILE_MAX_ACTIVE = 128
PROJECTILE_MAX_PER_SHOOTER = 32
PROJECTILE_MAX_ID = 2147483647
PROJECTILE_MAX_DAMAGE_STICKER = (1 << 64) - 1
PROJECTILE_MAX_PROGRESS_BATCH = 30
PROJECTILE_MAX_SPLASH_TARGETS = 30
PROJECTILE_MAX_DESTRUCTIBLES = 64
PROJECTILE_MAX_LIFETIME_MS = 20000
PLAYER_FIRE_INTENT_MAX_PENDING = 1
PLAYER_FIRE_INTENT_HISTORY = 64
PLAYER_FIRE_ORIGIN_RADIUS = 25.0
MAX_PLAYER_DISPERSION_ANGLE = 0.5
MAX_PLAYER_CLIP_SIZE = 255
PROJECTILE_MAX_GRAVITY = 500.0
PROJECTILE_MAX_VELOCITY = 3000.0
PROJECTILE_MAX_DISTANCE = 10000.0
PROJECTILE_MAX_SPLASH_RADIUS = 100.0
PROJECTILE_CLOCK_LEEWAY_MS = 250
PROJECTILE_TOLERANCE = 0.001
PROJECTILE_SHELL_KINDS = frozenset((
    "HOLLOW_CHARGE", "HIGH_EXPLOSIVE", "ARMOR_PIERCING",
    "ARMOR_PIERCING_HE", "ARMOR_PIERCING_CR"))
PROJECTILE_CAPABILITY = "projectile_ledger_v2"
RICOCHET_CONTINUATION_CAPABILITY = "ricochet_continuation_v1"
PROJECTILE_HIT_VEHICLE_CAPABILITY = "projectile_hit_vehicle_v1"
PROJECTILE_WRECK_HIT_CAPABILITY = "projectile_wreck_hit_v1"
RANDOM_MAP_CAPABILITY = "random_map_v1"
DESTRUCTIBLE_CATALOG_V5_CAPABILITY = "destructible_catalog_v5"
LEAN_SNAPSHOT_MANIFEST_CAPABILITY = "lean_snapshot_manifest_v1"
RAM_CONTACT_LEDGER_CAPABILITY = "ram_contact_ledger_v2"
HUMAN_RAM_TIMELINE_CAPABILITY = "human_ram_timeline_v1"
PLAYER_FIRE_INTENT_CAPABILITY = "player_fire_intent_v4"
PLAYER_ENVIRONMENT_CAPABILITY = "player_environment_v2"
EFFECTIVE_PARAMS_CAPABILITY = effective_params_wire.CAPABILITY
VEHICLE_OVERLAY_CAPABILITY = "vehicle_overlay_v1"
TEAM_SIZE_SELECTION_CAPABILITY = "team_size_selection_v1"
MODERN_INPUT_FIELDS = frozenset((
    "type", "round_id", "input_seq",
    "forward", "turn", "speed", "aim_yaw", "gun_pitch",
    "x", "y", "z", "yaw", "pitch", "roll", "pose_time_us",
    "fire_seq", "shell_index", "next_shell_index",
    "shell_change_pending", "gun_checkpoint", "ram_contacts",
    "ram_contact", "destructible_contacts", "siege_enabled",
    "up_cosine",
))
MODERN_INPUT_REQUIRED_FIELDS = frozenset(("round_id",))
HUMAN_RAM_CONTACT_FIELDS = frozenset((
    "seq", "bot_id", "bot_state_revision", "presentation_time_us",
    "native_contact_time_us", "contact_x", "contact_y", "contact_z",
    "contact_normal_x", "contact_normal_z", "contact_armor_player",
    "contact_armor_bot", "contact_screened_player",
    "contact_screened_bot", "contact_spall_player",
    "contact_bonus_player", "x", "y", "z", "yaw", "pitch", "roll",
    "vx", "vy", "vz", "bot_vx", "bot_vy", "bot_vz",
))
SERVER_CAPABILITIES = (
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    LEAN_SNAPSHOT_MANIFEST_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY,
    PLAYER_FIRE_INTENT_CAPABILITY,
    PLAYER_ENVIRONMENT_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    RICOCHET_CONTINUATION_CAPABILITY,
    PROJECTILE_HIT_VEHICLE_CAPABILITY,
    PROJECTILE_WRECK_HIT_CAPABILITY,
    RANDOM_MAP_CAPABILITY,
    "team_selection_v1",
    TEAM_SIZE_SELECTION_CAPABILITY,
    VEHICLE_OVERLAY_CAPABILITY,
)
SIEGE_DISABLED = 0
SIEGE_SWITCHING_ON = 1
SIEGE_ENABLED = 2
SIEGE_SWITCHING_OFF = 3
# Exact Chinese HD #1513 values from each stock vehicle definition and its
# paired ``*_siege_mode.xml`` descriptor.  The standalone server does not load
# client packages, so this small version-locked table owns only transition
# time, damaged-engine coefficient and the final-mode movement ceiling.
SIEGE_VEHICLE_PARAMS = {
    "sweden:S10_Strv_103_0_Series": (2.0, 1.3, 10.0 / 3.6, 2.0),
    "sweden:S11_Strv_103B": (2.0, 1.3, 10.0 / 3.6, 2.0),
    "sweden:S21_UDES_03": (2.0, 2.0, 5.0 / 3.6, 2.0),
    "sweden:S22_Strv_S1": (2.0, 1.3, 8.0 / 3.6, 2.0),
}
MAX_MOTION_TIME_US = 10000000000000000
# A source clock may span publications that were dropped or rejected, so its
# delta can exceed one BotRuntime step. It may not, however, advance more than
# one maximum 0.2-second integration step ahead of real receipt time.
MAX_BOT_SAMPLE_LEAD_US = 200000
SIMULATION_WORKER_CAPABILITY = "simulation_worker_v1"
SIMULATION_WORKER_ROLE = "simulation_worker"
MODERN_CLIENT_REQUIRED_CAPABILITIES = (
    PROJECTILE_CAPABILITY,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY,
    PLAYER_FIRE_INTENT_CAPABILITY,
    PLAYER_ENVIRONMENT_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    RICOCHET_CONTINUATION_CAPABILITY,
)
SIMULATION_WORKER_REQUIRED_CAPABILITIES = (
    MODERN_CLIENT_REQUIRED_CAPABILITIES +
    (SIMULATION_WORKER_CAPABILITY,))
# Negative ids are never legal player or bot ids.  Keep the external native
# worker distinct from the in-process server authority, whose wire id is zero.
SIMULATION_WORKER_AUTHORITY_ID = -1
SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS = 5.0
SIMULATION_WORKER_LOADING_TIMEOUT_SECONDS = 30.0
SIMULATION_WORKER_ADVANCEMENT_TYPES = frozenset((
    "simulation_progress", "bot_state", "bot_observation",
    "bot_hit_report", "bot_human_hit", "bot_ram_report", "rules_state",
    "destructible", "projectile_launch", "projectile_progress",
    "projectile_ricochet", "projectile_resolve", "fire_intent_result",
    "player_destructible_contact_result",
    "player_environment",
))
FATAL_BOT_STATE_REJECT_CODES = frozenset((
    "round", "authority", "manifest_authority", "manifest_missing",
))
AUTHORITY_DESCRIPTOR_TIMEOUT_SECONDS = 30.0
AUTHORITY_DESTRUCTIBLE_TIMEOUT_SECONDS = 120.0
DESTRUCTIBLE_KINDS = frozenset(("tree", "column", "fragile", "module"))
COMBAT_EVENT_KINDS = frozenset((
    "health", "hit", "bot_hit", "bot_human_hit", "bot_bot_hit",
))
COMBAT_SOURCE_KINDS = {
    "shot": frozenset((
        "hit", "bot_hit", "bot_human_hit", "bot_bot_hit")),
    "fire": frozenset((
        "hit", "bot_hit", "bot_human_hit", "bot_bot_hit")),
    "ram": frozenset((
        "hit", "bot_hit", "bot_human_hit", "bot_bot_hit")),
    "client_simulation": frozenset(("health",)),
    "player_left": frozenset(("health",)),
    "environment": frozenset(("health",)),
}
CRITICAL_DEVICE_NAMES = frozenset((
    "engineHealth", "ammoBayHealth", "fuelTankHealth", "radioHealth",
    "leftTrackHealth", "rightTrackHealth", "gunHealth",
    "turretRotatorHealth", "surveyingDeviceHealth",
))
CRITICAL_CREW_NAMES = frozenset((
    "commander", "driver", "gunner1", "gunner2", "loader1",
    "loader2", "radioman1", "radioman2",
))
CRITICAL_STATES = frozenset(("normal", "critical", "destroyed"))
CRITICAL_CAUSES = frozenset((
    "shot", "explosion", "repair", "fire", "drowning", "ramming"))
TRACK_DEVICE_NAMES = frozenset(("leftTrackHealth", "rightTrackHealth"))
OUTFIT_SEASONS = frozenset((1, 2, 4))
MAX_OUTFIT_BYTES = 64 * 1024
MAX_VEHICLE_COMPACT_BYTES = 64 * 1024


def _validated_outfits(value):
    """Return canonical base64 outfit rows or raise ValueError."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > len(OUTFIT_SEASONS):
        raise ValueError("invalid outfit catalogue")
    result = {}
    total = 0
    for raw_season, encoded in value.items():
        try:
            season = int(raw_season)
        except (TypeError, ValueError):
            raise ValueError("invalid outfit season")
        if season not in OUTFIT_SEASONS or not isinstance(encoded, str):
            raise ValueError("invalid outfit row")
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except Exception:
            raise ValueError("invalid outfit descriptor")
        if not raw or len(raw) > MAX_OUTFIT_BYTES:
            raise ValueError("invalid outfit descriptor size")
        total += len(raw)
        if total > MAX_OUTFIT_BYTES * len(OUTFIT_SEASONS):
            raise ValueError("outfit catalogue is too large")
        result[str(season)] = base64.b64encode(raw).decode("ascii")
    return result


def _validated_vehicle_compact_descr(value):
    """Return one canonical base64 mounted vehicle descriptor."""
    if not isinstance(value, str) or not value:
        raise ValueError("invalid vehicle compact descriptor")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception:
        raise ValueError("invalid vehicle compact descriptor")
    if not raw or len(raw) > MAX_VEHICLE_COMPACT_BYTES:
        raise ValueError("invalid vehicle compact descriptor size")
    canonical = base64.b64encode(raw).decode("ascii")
    if canonical != value:
        raise ValueError("non-canonical vehicle compact descriptor")
    return canonical


def _validated_effective_params(value):
    """Return a detached canonical #1513 effective-parameter snapshot."""
    canonical = effective_params_wire.canonical(value)
    if canonical is None:
        raise ValueError("invalid effective vehicle parameters")
    return canonical


def _server_log(message):
    stamp = time.strftime("%H:%M:%S")
    sys.stdout.write("[%s] %s\n" % (stamp, message))
    sys.stdout.flush()


_SERVER_LOG_LAST = {}


def _server_log_limited(key, message, interval=5.0):
    """Keep repeated protocol failures useful without flooding the log."""
    now = time.monotonic()
    previous = _SERVER_LOG_LAST.get(key)
    if previous is not None and now - previous < float(interval):
        return False
    _SERVER_LOG_LAST[key] = now
    _server_log(message)
    return True


def _tick_failure_diagnostic(error):
    """Return one bounded single-line diagnostic for a tick failure."""
    try:
        error_type = type(error).__name__
    except Exception:
        error_type = "Exception"
    try:
        detail = str(error)
    except Exception:
        detail = "<unprintable>"
    diagnostic = "%s: %s" % (
        error_type, detail.replace("\r", " ").replace("\n", " "))
    return diagnostic[:MAX_TICK_FAILURE_DIAGNOSTIC_CHARS]


def _valid_capability_subset(value, required):
    """Validate a bounded capability list containing the understood subset."""
    return bool(
        isinstance(value, list) and
        len(value) <= 32 and
        all(isinstance(item, str) and item and len(item) <= 64
            for item in value) and
        len(set(value)) == len(value) and
        all(item in value for item in required))


def _compatible_hello_protocol(value, capabilities):
    """Negotiate newer/older JSON peers by capability, not version equality."""
    if isinstance(value, bool):
        return False
    try:
        protocol = int(value)
    except (TypeError, ValueError, OverflowError):
        return False
    if protocol == PROTOCOL_VERSION:
        return True
    return bool(
        protocol > 0 and
        _valid_capability_subset(
            capabilities, MODERN_CLIENT_REQUIRED_CAPABILITIES))


def _peer_closed_socket(error):
    """Classify ordinary peer shutdown/reset separately from server faults."""
    code = getattr(error, "errno", None)
    if code is None:
        args = getattr(error, "args", ())
        if args and isinstance(args[0], int):
            code = args[0]
    return code in frozenset((32, 54, 104, 107, 10053, 10054, 10058))


def _bot_combat_log_message(event, players, bot_states):
    """Format one bot combat line with its real cause and both teams."""
    attacker_id = event.get("attacker", event.get("attacker_bot"))
    target_id = event.get("target", event.get("target_bot"))
    attacker = (players.get(attacker_id) if "attacker" in event else
                bot_states.get(attacker_id))
    target = (players.get(target_id) if "target" in event else
              bot_states.get(target_id))
    attacker_team = (attacker.team if hasattr(attacker, "team") else
                     attacker.get("team") if isinstance(attacker, dict) else
                     None)
    target_team = (target.team if hasattr(target, "team") else
                   target.get("team") if isinstance(target, dict) else None)
    return (
        "BOT COMBAT kind=%s source=%s attacker=%s attacker_team=%s "
        "target=%s target_team=%s damage=%s health=%s dead=%s" % (
            event.get("kind"), event.get("source"), attacker_id,
            attacker_team, target_id, target_team, event.get("damage"),
            event.get("health"), event.get("dead")))


def _server_event_log_message(event, players, bot_states):
    """Format only battle events that help diagnose a reported failure."""
    kind = event.get("kind")
    if kind == "shot":
        origin = event.get("origin") or (0.0, 0.0, 0.0)
        velocity = event.get("velocity") or (0.0, 0.0, 0.0)
        speed = math.sqrt(sum(float(value) ** 2 for value in velocity))
        direction = tuple(
            float(value) / speed for value in velocity) if speed > 0.0 else (
                0.0, 0.0, 0.0)
        return (
            "SHOT attacker=%s seq=%s shell=%s input=%s "
            "origin=(%.2f,%.2f,%.2f) direction=(%.5f,%.5f,%.5f)" % (
                event.get("attacker"), event.get("shot_seq"),
                event.get("shell_index"), event.get("fire_input_seq"),
                float(origin[0]), float(origin[1]), float(origin[2]),
                direction[0], direction[1], direction[2]))
    if kind == "hit":
        return "HIT attacker=%s target=%s damage=%s health=%s dead=%s" % (
            event.get("attacker"), event.get("target"),
            event.get("damage"), event.get("health"), event.get("dead"))
    if kind == "health":
        if (event.get("damage") == 0 and event.get("dead") is False and
                event.get("source") == "client_simulation"):
            return None
        return "HEALTH target=%s damage=%s health=%s dead=%s source=%s" % (
            event.get("target"), event.get("damage"), event.get("health"),
            event.get("dead"), event.get("source"))
    if kind in ("bot_hit", "bot_human_hit"):
        return _bot_combat_log_message(event, players, bot_states)
    if kind == "bot_bot_hit":
        attacker = bot_states.get(event.get("attacker_bot"))
        target = bot_states.get(event.get("target_bot"))
        attacker_team = (attacker.get("team")
                         if isinstance(attacker, dict) else None)
        target_team = (target.get("team")
                       if isinstance(target, dict) else None)
        if (event.get("source") == "shot" and not event.get("dead") and
                attacker_team in (1, 2) and target_team in (1, 2) and
                attacker_team != target_team):
            return None
        return _bot_combat_log_message(event, players, bot_states)
    if kind == "authority":
        return "BOT AUTHORITY player_id=%s" % event.get("player_id")
    if kind == "battle_result":
        return "BATTLE RESULT winner=%s reason=%s base_team=%s" % (
            event.get("winner"), event.get("reason"),
            event.get("base_team"))
    if (kind == "projectile_impact" and
            event.get("shooter_kind") == "player"):
        return "PROJECTILE TERMINAL id=%s outcome=%s elapsed_ms=%s" % (
            event.get("projectile_id"), event.get("outcome"),
            event.get("resolved_time_ms"))
    return None


def _finite_float(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(value):
        return float(default)
    return value


def _has_finite_fields(value, names):
    if not isinstance(value, dict):
        return False
    for name in names:
        if name not in value:
            return False
        try:
            if not math.isfinite(float(value[name])):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _validated_bot_reload_progress(raw, required=False):
    """Return one exact finite reload clock or raise on a partial contract."""
    has_time = isinstance(raw, dict) and "reload_time" in raw
    has_duration = isinstance(raw, dict) and "reload_duration" in raw
    if not has_time and not has_duration and not required:
        return None
    if not has_time or not has_duration:
        raise ValueError("bot reload progress is incomplete")
    if (isinstance(raw.get("reload_time"), bool) or
            isinstance(raw.get("reload_duration"), bool)):
        raise ValueError("bot reload progress is invalid")
    try:
        remaining = float(raw["reload_time"])
        duration = float(raw["reload_duration"])
    except (TypeError, ValueError, OverflowError):
        raise ValueError("bot reload progress is invalid")
    if (not math.isfinite(remaining) or not math.isfinite(duration) or
            duration <= 0.0 or remaining < 0.0 or remaining > duration):
        raise ValueError("bot reload progress is invalid")
    return remaining, duration


INPUT_OUTCOME_APPLIED = "applied"
INPUT_OUTCOME_INACTIVE = "inactive"
INPUT_OUTCOME_REJECTED = "rejected"
PLAYER_INPUT_FAULT_ENV = "WOT_0922_INPUT_FAULT"
# Deterministic acceptance hook.  Each entry rewrites exactly one field of one
# ordered input frame into a value the shipping client never emits, so the
# frame then fails the *production* pre-admission validator.  Nothing here
# bypasses validation, and the hook stays inert unless the environment
# variable names a class.
PLAYER_INPUT_FAULT_CLASSES = {
    "aim_yaw": ("aim_yaw", 7.0),
    "gun_pitch": ("gun_pitch", 2.5),
    "vehicle_yaw": ("yaw", 9.0),
    "position": ("x", 4000.0),
    "up_cosine": ("up_cosine", 1.5),
    "pose_clock": ("pose_time_us", 1.5),
    "fire_seq": ("fire_seq", True),
    "shell_pair": ("next_shell_index", 10),
    "gun_checkpoint": ("gun_checkpoint", {"reload_time": 0.0}),
    "siege_state": ("siege_enabled", 1),
    "extra_field": ("damage", 1000),
    "missing_field": ("round_id", None),
}


def _player_input_fault_class():
    """Return the armed recoverable input-fault class, or an empty string."""
    selected = str(os.environ.get(PLAYER_INPUT_FAULT_ENV, "") or "").strip()
    return selected if selected in PLAYER_INPUT_FAULT_CLASSES else ""


def _injected_player_input_fault(message, fault_class):
    """Return one copy of ``message`` broken in exactly one field."""
    name, value = PLAYER_INPUT_FAULT_CLASSES[fault_class]
    faulted = dict(message)
    if value is None:
        faulted.pop(name, None)
    else:
        faulted[name] = value
    return faulted


def _canonical_human_gun_checkpoint(raw):
    """Validate one final visible-client gun state at an input sequence."""
    if not isinstance(raw, dict) or set(raw) != {
            "reload_time", "reload_duration", "clip", "clip_size",
            "dispersion"}:
        raise ValueError("player gun checkpoint has an invalid shape")
    reload_time = _bounded_float(
        raw.get("reload_time"), 0.0, 3600.0)
    reload_duration = _bounded_float(
        raw.get("reload_duration"), 0.0, 3600.0, False)
    dispersion = _bounded_float(
        raw.get("dispersion"), 0.0, MAX_PLAYER_DISPERSION_ANGLE)
    clip = _exact_int(raw.get("clip"), 0, MAX_PLAYER_CLIP_SIZE)
    clip_size = _exact_int(
        raw.get("clip_size"), 1, MAX_PLAYER_CLIP_SIZE)
    if reload_time > reload_duration or clip > clip_size:
        raise ValueError("player gun checkpoint is inconsistent")
    return {
        "reload_time": reload_time,
        "reload_duration": reload_duration,
        "clip": clip,
        "clip_size": clip_size,
        "dispersion": dispersion,
    }


def _critical_proposal_admission(message, expected_base_revision,
                                 expected_ack_seq):
    """Validate one modern firing-client critical compare-and-swap token."""
    if (isinstance(expected_base_revision, bool) or
            not isinstance(expected_base_revision, int) or
            expected_base_revision < 0 or
            isinstance(expected_ack_seq, bool) or
            not isinstance(expected_ack_seq, int) or
            expected_ack_seq < 0):
        raise ValueError("invalid canonical critical target token")
    values = []
    for name in ("critical_target_base_revision",
                 "critical_target_ack_seq", "hull_damage"):
        value = message.get(name)
        if (isinstance(value, bool) or not isinstance(value, int) or
                value < 0):
            raise ValueError("invalid modern critical proposal")
        values.append(value)
    base_revision, ack_seq, hull_damage = values
    if hull_damage > 5000:
        raise ValueError("invalid modern critical hull damage")
    accepted = (base_revision == expected_base_revision and
                ack_seq == expected_ack_seq)
    return hull_damage, accepted


def _clamp(value, low, high):
    return max(low, min(high, value))


def _exact_int(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    if low is not None and value < low:
        raise ValueError("integer below lower bound")
    if high is not None and value > high:
        raise ValueError("integer above upper bound")
    return value


def _team_capacity(value, name):
    """Return one exact 1-15 team capacity without accepting booleans."""
    if isinstance(value, bool):
        raise ValueError("%s must be 1-15" % name)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("%s must be 1-15" % name)
    if isinstance(value, float) and value != parsed:
        raise ValueError("%s must be 1-15" % name)
    if not 1 <= parsed <= 15:
        raise ValueError("%s must be 1-15" % name)
    return parsed


def _requested_team(value):
    """Normalize an optional player team preference; zero means automatic."""
    if value in (None, "", "auto", 0, "0"):
        return 0
    if isinstance(value, bool):
        raise ValueError("requested_team must be auto, 1, or 2")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("requested_team must be auto, 1, or 2")
    if parsed not in (1, 2) or str(value).strip() not in ("1", "2"):
        raise ValueError("requested_team must be auto, 1, or 2")
    return parsed


def _bounded_float(value, low, high, inclusive_low=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected finite number")
    value = float(value)
    if (not math.isfinite(value) or value > high or
            (value < low if inclusive_low else value <= low)):
        raise ValueError("number outside bounds")
    return value


def _bot_lineup_allowed_names(catalog):
    """Return selectable catalog identities without hidden test vehicles."""
    excluded_tags = {
        "event_battles", "premiumIGR", "observer", "secret",
    }
    return set(
        row.get("name") for row in (catalog or ())
        if isinstance(row, dict) and row.get("name") and
        not excluded_tags.intersection(row.get("tags") or ()) and
        row.get("name") != "usa:T23")


def _bounded_vector(value, lows, highs):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("expected three-vector")
    return [round(_bounded_float(component, lows[index], highs[index]), 6)
            for index, component in enumerate(value)]


def _bounded_bot_launch_pose(value):
    if not isinstance(value, list) or len(value) != 6:
        raise ValueError("expected bot launch pose")
    position = _bounded_vector(
        value[:3], (-5000.0, -1000.0, -5000.0),
        (5000.0, 3000.0, 5000.0))
    angles = [round(_bounded_float(
        value[index], -math.pi * 2.0, math.pi * 2.0), 6)
        for index in range(3, 6)]
    return position + angles


def _projectile_source_shot(value):
    """Return one exact mounted-gun projectile law or reject the wire."""
    if not isinstance(value, dict) or set(value) != {
            "speed", "gravity", "maxDistance", "piercingPower", "deadeye",
            "shell"}:
        raise ValueError("invalid source shot shape")
    shell = value.get("shell")
    shell_fields = set(shell) if isinstance(shell, dict) else set()
    base_shell_fields = {"kind", "caliber", "damage", "explosionRadius"}
    he_factor_fields = {
        "explosionDamageFactor", "explosionDamageAbsorptionFactor",
        "explosionEdgeDamageFactor"}
    if (not isinstance(shell, dict) or
            shell_fields not in (
                base_shell_fields, base_shell_fields | he_factor_fields)):
        raise ValueError("invalid source shell shape")
    kind = shell.get("kind")
    piercing = value.get("piercingPower")
    damage = shell.get("damage")
    deadeye = value.get("deadeye")
    if (not isinstance(kind, str) or kind not in PROJECTILE_SHELL_KINDS or
            not isinstance(deadeye, bool) or
            not isinstance(piercing, list) or len(piercing) != 2 or
            not isinstance(damage, list) or len(damage) != 2):
        raise ValueError("invalid source shell data")
    result = {
        "speed": round(_bounded_float(
            value.get("speed"), 0.000001, PROJECTILE_MAX_VELOCITY), 6),
        "gravity": round(_bounded_float(
            value.get("gravity"), 0.000001, PROJECTILE_MAX_GRAVITY), 6),
        "maxDistance": round(_bounded_float(
            value.get("maxDistance"), 0.000001,
            PROJECTILE_MAX_DISTANCE), 6),
        "piercingPower": [round(_bounded_float(
            component, 0.0, 10000.0), 6) for component in piercing],
        "deadeye": deadeye,
        "shell": {
            "kind": kind,
            "caliber": round(_bounded_float(
                shell.get("caliber"), 0.000001, 1000.0), 6),
            "damage": [
                round(_bounded_float(
                    damage[0], 0.000001, 10000.0), 6),
                round(_bounded_float(
                    damage[1], 0.0, MAX_CRITICAL_DEVICE_HP), 6),
            ],
            "explosionRadius": round(_bounded_float(
                shell.get("explosionRadius"), 0.0,
                PROJECTILE_MAX_SPLASH_RADIUS), 6),
        },
    }
    if he_factor_fields.issubset(shell_fields):
        result["shell"].update({
            "explosionDamageFactor": round(_bounded_float(
                shell.get("explosionDamageFactor"), 0.000001,
                10000.0), 6),
            "explosionDamageAbsorptionFactor": round(_bounded_float(
                shell.get("explosionDamageAbsorptionFactor"),
                0.000001, 10000.0), 6),
            "explosionEdgeDamageFactor": round(_bounded_float(
                shell.get("explosionEdgeDamageFactor"),
                0.000001, 1.0), 6),
        })
    return result


def _message_fingerprint(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _safe_name(value, fallback):
    value = str(value or fallback).strip()
    value = "".join(ch for ch in value if ch.isalnum() or ch in " _-")
    return value[:24] or fallback


def _safe_vehicle(value, fallback):
    value = str(value or fallback).strip()
    value = "".join(ch for ch in value if ch.isalnum() or ch in ":_-")
    return value[:64] or fallback


def _default_result_receipt_state_path(port=28782):
    """Keep unacknowledged results beside the portable server executable."""
    if getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.abspath(sys.executable))
    else:
        root = os.path.dirname(os.path.abspath(__file__))
    stem, extension = os.path.splitext(RESULT_RECEIPT_STATE_FILE)
    return os.path.join(root, "%s-%d%s" % (
        stem, int(port), extension))


def _write_json_atomic(path, value):
    """Replace one JSON file only after its complete contents reach disk."""
    path = os.path.abspath(path)
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    temporary = "%s.tmp-%s" % (path, uuid.uuid4().hex)
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.isfile(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def _persisted_result_receipt(value):
    """Return a plain bounded receipt loaded from disk, or raise ValueError."""
    if (not isinstance(value, dict) or
            value.get("type") != "battle_receipt" or
            value.get("protocol") != PROTOCOL_VERSION):
        raise ValueError("invalid persisted battle receipt envelope")
    required_text = {
        "receipt_id": 96, "account_key": 64, "player_name": 32,
        "vehicle": 96, "map": 96,
    }
    for name, limit in required_text.items():
        field = value.get(name)
        if not isinstance(field, str) or not field or len(field) > limit:
            raise ValueError("invalid persisted battle receipt identity")
    if any(character not in
           "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"
           for character in value["account_key"]):
        raise ValueError("invalid persisted battle receipt account")
    for name, low, high in (
            ("arena_unique_id", 0, None), ("round_id", 1, None),
            ("player_id", 1, None), ("team", 1, 2),
            ("winner", 0, 2), ("finish_reason", 0, 255),
            ("death_reason", -1, 255), ("duration", 0, None)):
        parsed = value.get(name)
        if (isinstance(parsed, bool) or not isinstance(parsed, int) or
                parsed < low or (high is not None and parsed > high)):
            raise ValueError("invalid persisted battle receipt number")
    if not isinstance(value.get("premature_leave"), bool):
        raise ValueError("invalid persisted battle receipt leave state")
    stats = value.get("stats")
    rewards = value.get("rewards")
    stat_names = (
        "shots", "direct_hits", "piercings", "damage", "damage_received",
        "damage_blocked", "assist_track", "assist_radio", "assist_stun",
        "kills", "spotted", "capture_points", "dropped_capture_points",
    )
    reward_names = ("credits", "xp", "free_xp", "repair_cost", "ammo_cost")
    if not isinstance(stats, dict) or not isinstance(rewards, dict):
        raise ValueError("invalid persisted battle receipt summary")
    for mapping, names in ((stats, stat_names), (rewards, reward_names)):
        if any(isinstance(mapping.get(name), bool) or
               not isinstance(mapping.get(name), int) or
               mapping.get(name) < 0 for name in names):
            raise ValueError("invalid persisted battle receipt statistic")
    if rewards["repair_cost"] or rewards["ammo_cost"]:
        raise ValueError("offline service costs must be zero")
    public_rows = value.get("public_results")
    if not isinstance(public_rows, list) or not 1 <= len(public_rows) <= 30:
        raise ValueError("invalid persisted public result roster")
    seen = set()
    row_teams = {}
    personal = None
    for row in public_rows:
        if not isinstance(row, dict):
            raise ValueError("invalid persisted public result row")
        actor_kind = row.get("actor_kind")
        actor_id = row.get("actor_id")
        identity = (actor_kind, actor_id)
        if (actor_kind not in ("player", "bot") or
                isinstance(actor_id, bool) or not isinstance(actor_id, int) or
                actor_id < 1 or identity in seen or
                not isinstance(row.get("name"), str) or
                not 1 <= len(row["name"]) <= 32 or
                not isinstance(row.get("vehicle"), str) or
                not 1 <= len(row["vehicle"]) <= 96 or
                isinstance(row.get("team"), bool) or
                row.get("team") not in (1, 2) or
                isinstance(row.get("health"), bool) or
                not isinstance(row.get("health"), int) or
                row.get("health") < 0 or
                isinstance(row.get("death_reason"), bool) or
                not isinstance(row.get("death_reason"), int) or
                not -1 <= row.get("death_reason") <= 255 or
                isinstance(row.get("xp"), bool) or
                not isinstance(row.get("xp"), int) or row.get("xp") < 0 or
                not isinstance(row.get("is_team_killer"), bool) or
                not isinstance(row.get("stats"), dict)):
            raise ValueError("invalid persisted public result row")
        if any(isinstance(row["stats"].get(name), bool) or
               not isinstance(row["stats"].get(name), int) or
               row["stats"].get(name) < 0 for name in stat_names):
            raise ValueError("invalid persisted public result statistic")
        killer_kind = row.get("killer_kind", "")
        killer_id = row.get("killer_id", 0)
        if (killer_kind not in ("", "player", "bot") or
                isinstance(killer_id, bool) or not isinstance(killer_id, int) or
                killer_id < 0 or bool(killer_kind) != bool(killer_id)):
            raise ValueError("invalid persisted public result killer")
        seen.add(identity)
        row_teams[identity] = row["team"]
        if identity == ("player", value["player_id"]):
            personal = row
    if (personal is None or personal["name"] != value["player_name"] or
            personal["vehicle"] != value["vehicle"] or
            personal["team"] != value["team"] or
            personal["death_reason"] != value["death_reason"] or
            personal["xp"] != rewards["xp"] or
            personal["stats"] != stats):
        raise ValueError("inconsistent persisted personal result row")
    interactions = value.get("interactions", [])
    if (not isinstance(interactions, list) or
            len(interactions) > len(public_rows)):
        raise ValueError("invalid persisted interaction details")
    interaction_fields = set(RESULT_INTERACTION_LIMITS)
    interaction_keys = interaction_fields | {"target_kind", "target_id"}
    interaction_targets = set()
    for interaction in interactions:
        if (not isinstance(interaction, dict) or
                set(interaction) != interaction_keys):
            raise ValueError("invalid persisted interaction row")
        target = (interaction.get("target_kind"),
                  interaction.get("target_id"))
        if (target not in seen or target in interaction_targets or
                target == ("player", value["player_id"]) or
                row_teams[target] == value["team"]):
            raise ValueError("invalid persisted interaction target")
        for name, (minimum, maximum) in RESULT_INTERACTION_LIMITS.items():
            field = interaction.get(name)
            if (isinstance(field, bool) or not isinstance(field, int) or
                    field < minimum or field > maximum):
                raise ValueError("invalid persisted interaction value")
        interaction_targets.add(target)
    value["interactions"] = interactions
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) + 1 > MAX_LINE_BYTES:
        raise ValueError("persisted battle receipt exceeds wire limit")
    return json.loads(encoded)


def _critical_payload(value):
    """Validate one client-resolved 0.8.2 critical-state transition.

    The firing authority owns collision/material rolls; the server only
    bounds and relays their resulting state.  This mirrors the existing
    server-owned hull-HP boundary without inventing a second module law.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("critical payload must be an object")
    devices = []
    seen = set()
    for record in list(value.get("devices") or ())[:16]:
        if not isinstance(record, dict):
            raise ValueError("critical device must be an object")
        name = str(record.get("name", ""))
        state = str(record.get("state", ""))
        if (name not in CRITICAL_DEVICE_NAMES or name in seen or
                state not in CRITICAL_STATES or
                not _has_finite_fields(record, ("hp", "max_hp"))):
            raise ValueError("invalid critical device")
        hp = _clamp(
            _finite_float(record.get("hp")), 0.0,
            MAX_CRITICAL_DEVICE_HP)
        maximum = _clamp(
            _finite_float(record.get("max_hp")), 1.0,
            MAX_CRITICAL_DEVICE_HP)
        devices.append({"name": name, "hp": round(min(hp, maximum), 3),
                        "max_hp": round(maximum, 3), "state": state})
        seen.add(name)
    destroyed = sorted(set(
        str(name) for name in value.get("destroyed") or ()
        if str(name) in CRITICAL_DEVICE_NAMES))
    destroyed_states = set(
        record["name"] for record in devices
        if record["state"] == "destroyed")
    if destroyed_states != set(destroyed):
        raise ValueError("critical destroyed state disagrees with devices")
    crew_ko = sorted(set(
        str(name) for name in value.get("crew_ko") or ()
        if str(name) in CRITICAL_CREW_NAMES))
    raw_roster = value.get("crew_roster")
    crew_roster = []
    if raw_roster is not None:
        if (not isinstance(raw_roster, (list, tuple)) or
                not 1 <= len(raw_roster) <= len(CRITICAL_CREW_NAMES)):
            raise ValueError("invalid critical crew roster")
        for raw_name in raw_roster:
            name = str(raw_name)
            if name not in CRITICAL_CREW_NAMES or name in crew_roster:
                raise ValueError("invalid critical crew roster")
            crew_roster.append(name)
        if not set(crew_ko).issubset(crew_roster):
            raise ValueError("critical crew knockout is outside roster")
    events = []
    for raw in list(value.get("events") or ())[:24]:
        if not isinstance(raw, dict):
            raise ValueError("critical event must be an object")
        kind = str(raw.get("kind", ""))
        state = raw.get("state")
        cause = str(raw.get("cause", "shot"))
        if kind == "device":
            name = str(raw.get("name", ""))
            if name not in CRITICAL_DEVICE_NAMES or state not in CRITICAL_STATES:
                raise ValueError("invalid critical device event")
            event = {"kind": kind, "name": name, "state": state}
            old_state = raw.get("old_state")
            if old_state in CRITICAL_STATES:
                event["old_state"] = old_state
        elif kind == "crew":
            name = str(raw.get("name", ""))
            if name not in CRITICAL_CREW_NAMES or state not in (
                    "normal", "destroyed"):
                raise ValueError("invalid critical crew event")
            event = {"kind": kind, "name": name, "state": state}
        elif kind == "fire":
            event = {"kind": kind, "state": bool(state)}
        elif kind == "ammo_rack" and state == "destroyed":
            event = {"kind": kind, "state": state}
        else:
            raise ValueError("invalid critical event kind")
        event["cause"] = cause if cause in CRITICAL_CAUSES else "shot"
        events.append(event)
    result = {
        "devices": devices,
        "destroyed": destroyed,
        "crew_ko": crew_ko,
        "fire": bool(value.get("fire", False)),
        "ammo_rack_death": bool(value.get("ammo_rack_death", False)),
        "events": events,
    }
    if crew_roster:
        result["crew_roster"] = crew_roster
    return result


def _critical_damage_delta(value):
    """Validate one monotonic native critical-damage proposal delta."""
    if not isinstance(value, dict) or set(value) != {
            "devices", "crew_ko", "ignite"}:
        raise ValueError("critical delta has an invalid shape")
    raw_devices = value.get("devices")
    raw_crew = value.get("crew_ko")
    if (not isinstance(raw_devices, (list, tuple)) or
            len(raw_devices) > len(CRITICAL_DEVICE_NAMES) or
            not isinstance(raw_crew, (list, tuple)) or
            len(raw_crew) > len(CRITICAL_CREW_NAMES) or
            not isinstance(value.get("ignite"), bool)):
        raise ValueError("critical delta has invalid fields")
    devices = []
    seen = set()
    for raw in raw_devices:
        if not isinstance(raw, dict) or set(raw) != {"name", "hp_loss"}:
            raise ValueError("critical device delta has an invalid shape")
        name = str(raw.get("name", ""))
        if (name not in CRITICAL_DEVICE_NAMES or name in seen or
                isinstance(raw.get("hp_loss"), bool) or
                not _has_finite_fields(raw, ("hp_loss",))):
            raise ValueError("invalid critical device delta")
        hp_loss = _finite_float(raw.get("hp_loss"), -1.0)
        if hp_loss <= 0.0 or hp_loss > MAX_CRITICAL_DEVICE_HP:
            raise ValueError("invalid critical device HP loss")
        seen.add(name)
        devices.append({"name": name, "hp_loss": round(hp_loss, 3)})
    crew = []
    for raw in raw_crew:
        name = str(raw)
        if name not in CRITICAL_CREW_NAMES or name in crew:
            raise ValueError("invalid critical crew delta")
        crew.append(name)
    result = {
        "devices": devices,
        "crew_ko": sorted(crew),
        "ignite": bool(value["ignite"]),
    }
    return result


def _whole_crew_knocked_out(value):
    """Return whether a validated #1513 physical crew roster is all KO."""
    if not isinstance(value, dict):
        return False
    roster = set(value.get("crew_roster") or ())
    return bool(roster) and roster.issubset(set(value.get("crew_ko") or ()))


def _critical_state(value):
    """Store durable critical state without replaying transition events."""
    if value is None:
        return None
    result = dict(value)
    result["events"] = []
    return result


def _bot_terminal_critical(value):
    """Validate the worker's immutable full-wreck critical projection."""
    terminal = _critical_payload(value)
    if terminal is None or terminal.get("events"):
        raise ValueError("bot terminal critical must be durable state")
    devices = terminal.get("devices") or ()
    if (set(record["name"] for record in devices) !=
            set(CRITICAL_DEVICE_NAMES) or
            any(record["hp"] != 0.0 or
                record["state"] != "destroyed"
                for record in devices) or
            set(terminal.get("destroyed") or ()) !=
            set(CRITICAL_DEVICE_NAMES) or terminal.get("fire", False)):
        raise ValueError("bot terminal critical is not a complete wreck")
    roster = terminal.get("crew_roster") or ()
    if (not roster or
            set(terminal.get("crew_ko") or ()) != set(roster)):
        raise ValueError("bot terminal critical does not knock out its crew")
    return _critical_state(terminal)


def _critical_discrete_state(value):
    """Compare module/crew phases without per-frame repair HP progress."""
    if not isinstance(value, dict):
        return None
    devices = tuple(sorted(
        (str(record.get("name", "")), str(record.get("state", "")))
        for record in value.get("devices") or ()
        if isinstance(record, dict)))
    return (
        devices,
        tuple(sorted(str(name) for name in value.get("destroyed") or ())),
        tuple(sorted(str(name) for name in value.get("crew_ko") or ())),
        bool(value.get("fire", False)),
        bool(value.get("ammo_rack_death", False)),
    )


def _critical_damage_transition(previous, current):
    """Return whether a critical payload contains new module/crew damage."""
    if not isinstance(current, dict):
        return False
    for event in current.get("events") or ():
        if not isinstance(event, dict) or event.get("cause") == "repair":
            continue
        kind = event.get("kind")
        state = event.get("state")
        if ((kind == "device" and state in ("critical", "destroyed")) or
                (kind == "crew" and state == "destroyed") or
                (kind == "fire" and bool(state)) or
                (kind == "ammo_rack" and state == "destroyed")):
            return True

    previous = previous if isinstance(previous, dict) else {}
    old_devices = {
        str(record.get("name")): float(record.get("hp", 0.0))
        for record in previous.get("devices") or ()
        if isinstance(record, dict) and record.get("name") is not None
    }
    for record in current.get("devices") or ():
        if not isinstance(record, dict) or record.get("name") is None:
            continue
        name = str(record.get("name"))
        hp = _finite_float(record.get("hp"))
        old_hp = old_devices.get(name)
        if old_hp is not None and hp < old_hp - 0.0001:
            return True
        if (old_hp is None and hp + 0.0001 <
                _finite_float(record.get("max_hp"), hp)):
            return True
    if (set(current.get("destroyed") or ()) -
            set(previous.get("destroyed") or ())):
        return True
    if (set(current.get("crew_ko") or ()) -
            set(previous.get("crew_ko") or ())):
        return True
    return ((bool(current.get("fire")) and
             not bool(previous.get("fire"))) or
            (bool(current.get("ammo_rack_death")) and
             not bool(previous.get("ammo_rack_death"))))


def _track_repair_rows(value):
    """Validate one track-only repair checkpoint without widening authority."""
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 2:
        raise ValueError("invalid track repair rows")
    result = []
    seen = set()
    keys = {"name", "hp", "max_hp", "state"}
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != keys:
            raise ValueError("invalid track repair row")
        name = str(raw.get("name", ""))
        state = str(raw.get("state", ""))
        if (name not in TRACK_DEVICE_NAMES or name in seen or
                state not in ("destroyed", "critical") or
                not _has_finite_fields(raw, ("hp", "max_hp"))):
            raise ValueError("invalid track repair state")
        hp = _finite_float(raw.get("hp"), -1.0)
        maximum = _finite_float(raw.get("max_hp"), -1.0)
        if maximum <= 0.0 or hp < 0.0 or hp > maximum:
            raise ValueError("invalid track repair health")
        seen.add(name)
        result.append({
            "name": name,
            "hp": round(hp, 3),
            "max_hp": round(maximum, 3),
            "state": state,
        })
    return tuple(sorted(result, key=lambda row: row["name"]))


def _destroyed_tracks(critical):
    """Return the track devices one critical payload reports as destroyed."""
    if not isinstance(critical, dict):
        return frozenset()
    names = set(str(name) for name in critical.get("destroyed") or ()
                if str(name) in TRACK_DEVICE_NAMES)
    for record in critical.get("devices") or ():
        if (isinstance(record, dict) and
                str(record.get("name")) in TRACK_DEVICE_NAMES and
                record.get("state") == "destroyed"):
            names.add(str(record.get("name")))
    return frozenset(names)


def _monotonic_endpoint_server_time(endpoint, message):
    """Clamp concurrently-produced clock samples on one ordered stream."""
    if not isinstance(message, dict) or "server_time_ms" not in message:
        return message
    try:
        round_id = int(message.get("round_id"))
        server_time_ms = int(message.get("server_time_ms"))
    except (TypeError, ValueError, OverflowError):
        return message
    if endpoint.server_time_round_id != round_id:
        endpoint.server_time_round_id = round_id
        endpoint.last_server_time_ms = server_time_ms
        return message
    if server_time_ms >= endpoint.last_server_time_ms:
        endpoint.last_server_time_ms = server_time_ms
        return message
    outgoing = dict(message)
    outgoing["server_time_ms"] = endpoint.last_server_time_ms
    return outgoing


class _EndpointSendMixin:
    """Keep slow TCP writers off the simulation and handler threads."""

    def _initialize_outbox(self):
        self._outbox_condition = threading.Condition()
        self._outbox_reliable = deque()
        self._outbox_reliable_bytes = 0
        self._outbox_snapshot = None
        self._outbox_thread = None

    def __post_init__(self):
        self._initialize_outbox()

    def _uses_async_outbox(self):
        return bool(
            getattr(self, "_force_async_outbox", False) or
            isinstance(self.conn, socket.socket))

    def _ensure_outbox(self):
        condition = getattr(self, "_outbox_condition", None)
        if condition is not None:
            return condition
        # Compatibility for an endpoint restored without __post_init__.
        with self.send_lock:
            if getattr(self, "_outbox_condition", None) is None:
                self._initialize_outbox()
            return self._outbox_condition

    def _shutdown_transport(self):
        shutdown = getattr(self.conn, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown(socket.SHUT_RDWR)
            except (OSError, TypeError, ValueError):
                pass

    def disconnect(self):
        self._mark_disconnected()
        self._shutdown_transport()

    def _mark_disconnected(self):
        condition = self._ensure_outbox()
        with condition:
            self.connected = False
            condition.notify_all()

    def _serialize_message(self, message):
        outgoing = _monotonic_endpoint_server_time(self, message)
        payload = (json.dumps(
            outgoing, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > MAX_LINE_BYTES:
            return None
        return payload

    def _mark_message_sent(self, message):
        if "bot_orders" in message:
            try:
                self.bot_order_revision_sent = int(
                    message.get("bot_order_revision", -1))
            except (TypeError, ValueError):
                pass
        if "destructibles" in message:
            try:
                self.destructible_revision_sent = int(
                    message.get("destructible_revision", -1))
            except (TypeError, ValueError):
                pass

    def _send_direct(self, message):
        if not self.connected:
            return False
        try:
            with self.send_lock:
                payload = self._serialize_message(message)
                if payload is None:
                    return False
                self._write_payload(payload)
            self._mark_message_sent(message)
            return True
        except (BrokenPipeError, ConnectionError, OSError):
            self.connected = False
            return False

    def _write_payload(self, payload):
        """Finish one frame across short socket stalls without duplication."""
        sender = getattr(self.conn, "send", None)
        if not callable(sender):
            self.conn.sendall(payload)
            return
        offset = 0
        stalled_since = None
        while offset < len(payload):
            try:
                count = sender(payload[offset:])
                if count is None or int(count) <= 0:
                    raise ConnectionError("peer closed during send")
                offset += int(count)
                stalled_since = None
            except socket.timeout:
                now = time.monotonic()
                if stalled_since is None:
                    stalled_since = now
                elif (now - stalled_since >=
                      OUTBOUND_STALL_TIMEOUT_SECONDS):
                    raise socket.timeout(
                        "peer did not accept LAN state for %.0f seconds" %
                        OUTBOUND_STALL_TIMEOUT_SECONDS)

    def _start_outbox_locked(self):
        thread = self._outbox_thread
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=self._outbox_loop,
            name="lan-outbound-%s" % getattr(
                self, "player_id", getattr(self, "worker_id", "peer")),
            daemon=True)
        self._outbox_thread = thread
        thread.start()

    def _enqueue_reliable(self, message, wait):
        if not self.connected:
            return False
        condition = self._ensure_outbox()
        done = threading.Event() if wait else None
        result = [] if wait else None
        with condition:
            if not self.connected:
                return False
            payload = self._serialize_message(message)
            if payload is None:
                return False
            pending_snapshot = self._outbox_snapshot
            if (pending_snapshot is not None and
                    self._reliable_fences_snapshot(
                        message, pending_snapshot["message"])):
                self._outbox_snapshot = None
            if (len(self._outbox_reliable) >=
                    MAX_RELIABLE_OUTBOUND_MESSAGES or
                    self._outbox_reliable_bytes + len(payload) >
                    MAX_RELIABLE_OUTBOUND_BYTES):
                self._fail_outbox()
                self._shutdown_transport()
                return False
            self._outbox_reliable.append({
                "payload": payload,
                "message": dict(message),
                "done": done,
                "result": result,
            })
            self._outbox_reliable_bytes += len(payload)
            self._start_outbox_locked()
            condition.notify()
        if done is None:
            return True
        if not done.wait(OUTBOUND_SYNC_TIMEOUT_SECONDS):
            self._fail_outbox()
            self._shutdown_transport()
            return False
        return bool(result and result[0])

    @staticmethod
    def _reliable_fences_snapshot(message, snapshot):
        """Prevent an older replaceable snapshot crossing a state barrier."""
        if not isinstance(message, dict) or not isinstance(snapshot, dict):
            return False
        if message.get("type") not in (
                "battle_live", "battle_start", "events", "roster",
                "snapshot"):
            return False
        message_round = message.get("round_id")
        snapshot_round = snapshot.get("round_id")
        if message_round is None or snapshot_round is None:
            return False
        if message_round != snapshot_round:
            return True
        if message.get("type") in (
                "battle_live", "battle_start", "roster"):
            # These are lifecycle/state barriers even without a tick. Never
            # let an older same-round snapshot cross one and resurrect a
            # departed member or a prior phase.
            return True
        message_tick = message.get("server_tick")
        snapshot_tick = snapshot.get("server_tick")
        if message.get("type") == "snapshot" and message_tick is None:
            return True
        try:
            if (message_tick is not None and snapshot_tick is not None and
                    int(message_tick) >= int(snapshot_tick)):
                return True
        except (TypeError, ValueError):
            return True
        message_epoch = message.get("authority_epoch")
        snapshot_epoch = snapshot.get("authority_epoch")
        try:
            return bool(
                message_epoch is not None and snapshot_epoch is not None and
                int(message_epoch) > int(snapshot_epoch))
        except (TypeError, ValueError):
            return True

    def offer_reliable(self, message):
        """Queue one ordered message without blocking its producer."""
        if not self._uses_async_outbox():
            return self._send_direct(message)
        return self._enqueue_reliable(message, wait=False)

    def offer_snapshot(self, message):
        """Replace an unsent snapshot while preserving reliable messages."""
        if not self._uses_async_outbox():
            return self._send_direct(message)
        if not self.connected:
            return False
        condition = self._ensure_outbox()
        with condition:
            if not self.connected:
                return False
            payload = self._serialize_message(message)
            if payload is None:
                return False
            self._outbox_snapshot = {
                "payload": payload,
                "message": dict(message),
                "done": None,
                "result": None,
            }
            self._start_outbox_locked()
            condition.notify()
        return True

    def _fail_outbox(self, current=None):
        condition = self._ensure_outbox()
        waiting = []
        with condition:
            self.connected = False
            if current is not None:
                waiting.append(current)
            waiting.extend(self._outbox_reliable)
            self._outbox_reliable.clear()
            self._outbox_reliable_bytes = 0
            self._outbox_snapshot = None
            for item in waiting:
                if item["result"] is not None:
                    item["result"].append(False)
                if item["done"] is not None:
                    item["done"].set()
            condition.notify_all()

    def _outbox_loop(self):
        condition = self._ensure_outbox()
        while True:
            with condition:
                while (self.connected and not self._outbox_reliable and
                       self._outbox_snapshot is None):
                    condition.wait(0.5)
                if not self.connected:
                    self._fail_outbox()
                    return
                if self._outbox_reliable:
                    item = self._outbox_reliable.popleft()
                    self._outbox_reliable_bytes -= len(item["payload"])
                else:
                    item = self._outbox_snapshot
                    self._outbox_snapshot = None
            try:
                with self.send_lock:
                    self._write_payload(item["payload"])
            except (BrokenPipeError, ConnectionError, OSError):
                self._fail_outbox(item)
                self._shutdown_transport()
                return
            self._mark_message_sent(item["message"])
            if item["result"] is not None:
                item["result"].append(True)
            if item["done"] is not None:
                item["done"].set()

    def send(self, message):
        if not self._uses_async_outbox():
            return self._send_direct(message)
        return self._enqueue_reliable(message, wait=True)


@dataclass
class Player(_EndpointSendMixin):
    player_id: int
    conn: socket.socket
    address: Tuple[str, int]
    name: str = "Player"
    vehicle: str = "ussr:R11_MS-1"
    team: int = 1
    slot: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    up_cosine: float = 1.0
    aim_yaw: float = 0.0
    gun_pitch: float = 0.0
    forward: float = 0.0
    turn: float = 0.0
    speed: float = 0.0
    siege_state: int = SIEGE_DISABLED
    siege_transition_ticks: int = 0
    fire_seq: int = 0
    fire_intent_seq: int = 0
    fire_intent_fingerprints: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    pending_fire_intents: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    fire_intent_results: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    shell_index: int = 0
    next_shell_index: int = 0
    shell_change_pending: bool = False
    reported_hits: set = field(default_factory=set, repr=False)
    health: int = 1000
    max_health: int = 1000
    alive: bool = True
    critical: dict = field(default_factory=dict)
    critical_revision: int = 0
    critical_report_base_revision: int = 0
    critical_ack_seq: int = 0
    track_repair_fingerprints: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    equipment_states: list = field(default_factory=list, repr=False)
    equipment_clock: float = 0.0
    equipment_revision: int = 0
    equipment_intent_seq: int = 0
    equipment_intent_fingerprints: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    equipment_intent_result: dict = field(default_factory=lambda: {
        "intent_seq": 0, "accepted": False, "reason": ""})
    combat_fire_elapsed: float = 0.0
    combat_fire_timer: float = 0.0
    fire_attacker_kind: str = ""
    fire_attacker_id: int = 0
    death_reason: int = 0
    display_health: Optional[int] = None
    frags: int = 0
    team_killer: bool = False
    death_attacker_kind: str = ""
    death_attacker_id: int = 0
    stun_end_server_time_ms: int = 0
    stun_attacker_kind: str = ""
    stun_attacker_id: int = 0
    client_position: bool = False
    ram_contact_seq: int = 0
    ram_contact_resolved_seq: int = 0
    ram_contact: dict = field(default_factory=dict)
    ram_contacts: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    ram_contact_rejections: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    destructible_contact_seq: int = 0
    destructible_contact_resolved_seq: int = 0
    destructible_contacts: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    destructible_contact_resolutions: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    destructible_contact_rejections: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    # ``input_seq`` is the last *applied* ordered input: the frame
    # whose controls, pose, shell selection and gun checkpoint were
    # committed.  ``input_processed_seq`` is the contiguous terminal
    # frontier, which also advances over a frame that reached an
    # idempotent rejected or inactive decision without applying any
    # state.  Consumers that need the current pose/gun checkpoint
    # must keep using ``input_seq``.
    input_seq: int = 0
    input_fingerprints: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    input_processed_seq: int = 0
    input_decisions: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    last_input_reject: dict = field(default_factory=dict, repr=False)
    input_reject_counts: dict = field(
        default_factory=dict, repr=False)
    input_fault_round: int = 0
    landing_observation_seq: int = 0
    landing_observation_input_seq: int = 0
    landing_observation_fingerprints: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    landing_observation_results: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    gun_checkpoint_seq: int = 0
    gun_checkpoint: dict = field(default_factory=dict, repr=False)
    gun_checkpoints: OrderedDict = field(
        default_factory=OrderedDict, repr=False)
    pose_time_us: Optional[int] = None
    pose_history: deque = field(default_factory=deque, repr=False)
    connected: bool = True
    participating: bool = True
    bot_order_revision_sent: int = -1
    destructible_revision_sent: int = -1
    battle_ready_round: int = 0
    capabilities: Tuple[str, ...] = field(default_factory=tuple)
    account_key: str = ""
    outfits: dict = field(default_factory=dict)
    vehicle_compact_descr: str = ""
    effective_params: dict = field(default_factory=dict)
    delivered_receipt_id: str = ""
    server_time_round_id: Optional[int] = field(default=None, repr=False)
    last_server_time_ms: int = field(default=-1, repr=False)
    send_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    snapshot_round_id_sent: int = -1
    snapshot_tick_sent: int = -1
    bot_manifest_round_id_sent: int = -1
    bot_manifest_authority_epoch_sent: int = -1
    bot_manifest_revision_sent: int = -1


@dataclass
class SimulationWorker(_EndpointSendMixin):
    """One native simulation endpoint that never enters the player model."""

    conn: socket.socket
    address: Tuple[str, int]
    capabilities: Tuple[str, ...] = field(default_factory=tuple)
    worker_id: int = SIMULATION_WORKER_AUTHORITY_ID
    connected: bool = True
    bot_order_revision_sent: int = -1
    destructible_revision_sent: int = -1
    battle_ready_round: int = 0
    simulation_progress_round_id: int = 0
    simulation_progress_authority_epoch: int = -1
    simulation_progress_frame_seq: int = -1
    server_time_round_id: Optional[int] = field(default=None, repr=False)
    last_server_time_ms: int = field(default=-1, repr=False)
    send_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    snapshot_round_id_sent: int = -1
    snapshot_tick_sent: int = -1
    bot_manifest_round_id_sent: int = -1
    bot_manifest_authority_epoch_sent: int = -1
    bot_manifest_revision_sent: int = -1


class BattleState:
    def __init__(self, map_name=DEFAULT_MAP, max_players=30, clock=None,
                 team_size=15,
                 receipt_state_path=None, team1_size=None, team2_size=None,
                 bot_tier_mode='random', bot_lineup=None):
        self.map_option = map_name
        self.map_name = self._choose_map()
        self.client_build = None
        self.max_players = max(1, min(int(max_players), 30))
        legacy_team_size = _team_capacity(team_size, "team_size")
        team1_size = (legacy_team_size if team1_size is None else
                      _team_capacity(team1_size, "team1_size"))
        team2_size = (legacy_team_size if team2_size is None else
                      _team_capacity(team2_size, "team2_size"))
        self.team_sizes = {1: team1_size, 2: team2_size}
        self.bot_tier_mode = bot_planner.normalize_bot_tier_mode(
            bot_tier_mode)
        self.bot_lineup = self._normalize_bot_lineup(bot_lineup)
        # Keep the old scalar on the wire for older protocol-v5 consumers.
        # New consumers use team_sizes; max remains a safe roster upper bound.
        self.team_size = max(team1_size, team2_size)
        self.players: Dict[int, Player] = {}
        self.simulation_worker: Optional[SimulationWorker] = None
        self.worker_failure_reason = ""
        self.next_id = 1
        self.tick = 0
        self.lock = threading.RLock()
        self.running = True
        self.server_tick_failure_count = 0
        self.last_server_tick_failure = ""
        self.phase = "waiting"
        self.round_id = 1
        self.state_revision = 0
        self.host_player_id = None
        self.bot_roster = self._new_bot_roster()
        self.bot_authority_id = None
        self.authority_epoch = 0
        self.bot_manifest_authority_id = None
        self.bot_manifest = []
        self.bot_manifest_revision = 0
        self.bot_states = {}
        self.bot_terminal_criticals = {}
        self.bot_state_revision = 0
        self.bot_planner = BotPlanner()
        self.vehicle_catalogs = {}
        self._monotonic = clock or time.monotonic
        # Windows CPython implements monotonic() with GetTickCount64, whose
        # practical 15.6 ms resolution aliases both a 25-30 Hz bot producer
        # and the 30 Hz server cadence.  Motion timing needs QPC resolution;
        # lifecycle deadlines keep the ordinary monotonic clock above.
        self._motion_clock = clock or time.perf_counter
        # Bot poses are accepted between the fixed 30 Hz world ticks.  The
        # tick clock therefore cannot timestamp them: an 18 Hz authority
        # observed by a 30 Hz snapshot alternates between one- and two-tick
        # gaps even when its own cadence is perfectly steady.  Keep a separate
        # monotonic clock so replicas can recover the real pose sample period
        # and the time already spent waiting for the next server snapshot.
        self._motion_clock_origin = float(self._motion_clock())
        self.bot_state_time_us = 0
        self.bot_source_time_us = None
        self.bot_source_receipt_time_us = None
        self.bot_source_batch_horizon_us = None
        # Once a source-integrated pose runs ahead of its receipt clock, keep
        # the whole round's motion timeline ahead by the same amount. Letting
        # raw time catch up under max(raw, sample) would flatten successive
        # snapshot timestamps and recreate a presentation hold.
        self.motion_time_offset_us = 0
        self.bot_orders = {"revision": 0, "orders": []}
        self._next_bot_planner_tick = 0
        self.bot_reported_hits = set()
        self.bot_reported_rams = set()
        self.bot_reported_ram_fingerprints = {}
        self.human_collision_profiles = {}
        self.human_collision_profile_authority_id = None
        self.human_collision_manifest_fingerprint = None
        self.human_ram_cooldowns = {}
        self.human_ram_contacts = frozenset()
        self.human_ram_pair_frontiers = {}
        self.human_ram_episode_seq = {}
        self.human_ram_probe_seq = 0
        self.human_ram_probe_requests = {}
        self.human_ram_retired_probe_pairs = OrderedDict()
        self.human_ram_probe_fingerprints = OrderedDict()
        self.vehicle_statistics = {}
        self.vehicle_interactions = {}
        self.round_participants = {}
        self.track_immobilisers = {}
        self.player_spotted = {}
        self.bot_spotted = {}
        self.player_environment = {}
        self.player_environment_seq = -1
        self.player_environment_authority_epoch = -1
        self.player_drowning_seconds = {}
        self.player_overturn_state = {}
        self.rules_state = {"bases": {
            "1": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False},
            "2": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False}}}
        self.battle_result = None
        # Receipts outlive the round reset so a client that left the battle or
        # reconnects in the garage can still receive the same idempotent row.
        self.result_receipts = OrderedDict()
        self.receipt_state_path = receipt_state_path
        self._load_result_receipts()
        self.receipt_namespace = uuid.uuid4().hex
        self.receipt_arena_prefix = uuid.uuid4().int & 0xffffffff
        self.round_start_time = int(time.time())
        self.result_reset_tick = None
        self.roster_finalized = False
        self.pending_events = []
        self.pending_live_message = None
        self.capture_bases = {}
        self.capture_threat_bases = {1: [], 2: []}
        self.capture_contributors = {1: {}, 2: {}}
        self.capture_cursors = {1: 0, 2: 0}
        self.destructibles = {}
        self.destructible_revision = 0
        self.projectiles = {}
        self.projectile_tombstones = {}
        self.projectile_revision = 0
        self.bot_pending_projectile_launches = set()
        self.bot_pending_projectile_metadata = {}
        self.bot_launch_clock_offset_us = None
        self.bot_last_projectile_launch_time_us = {}
        self.last_bot_state_reject = ""
        self.last_bot_state_reject_code = ""
        self.last_bot_hit_reject = ""
        self.last_bot_hit_reject_code = ""
        self.last_bot_human_hit_reject = ""
        self.last_bot_human_hit_reject_code = ""
        self.last_bot_manifest_reject = ""
        self.last_bot_manifest_reject_code = ""
        self.last_projectile_launch_reject = ""
        self.last_projectile_launch_reject_code = ""
        self.last_projectile_progress_reject = ""
        self.last_projectile_progress_reject_code = ""
        self.last_projectile_ricochet_reject = ""
        self.last_projectile_ricochet_reject_code = ""
        self.last_projectile_resolve_reject = ""
        self.last_projectile_resolve_reject_code = ""
        self._logged_protocol_reject_codes = {}

    @staticmethod
    def _normalize_bot_lineup(value):
        """Accept only unique, fully qualified launcher-owned slot pins."""
        if value is None:
            return []
        if not isinstance(value, (list, tuple)) or len(value) > 30:
            raise ValueError("invalid Bot lineup")
        result = []
        seen = set()
        for raw in value:
            if not isinstance(raw, dict):
                raise ValueError("invalid Bot lineup entry")
            team = _exact_int(raw.get("team"), 1, 2)
            slot = _exact_int(raw.get("slot"), 0, 14)
            vehicle = raw.get("vehicle")
            if (not isinstance(vehicle, str) or len(vehicle) > 96 or
                    vehicle.count(":") != 1):
                raise ValueError("invalid Bot lineup vehicle")
            nation, vehicle_name = vehicle.split(":", 1)
            if (re.fullmatch(r"[a-z][a-z0-9_]*", nation) is None or
                    re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",
                                 vehicle_name) is None):
                raise ValueError("invalid Bot lineup vehicle")
            key = (team, slot)
            if key in seen:
                raise ValueError("duplicate Bot lineup slot")
            seen.add(key)
            result.append({
                "team": team, "slot": slot, "vehicle": vehicle,
            })
        return sorted(result, key=lambda item: (item["team"], item["slot"]))

    def _load_result_receipts(self):
        """Recover only bounded, unacknowledged per-account receipts."""
        path = self.receipt_state_path
        if path is None or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as stream:
                value = json.load(stream)
            if (not isinstance(value, dict) or
                    value.get("schema") != RESULT_RECEIPT_STATE_SCHEMA or
                    not isinstance(value.get("receipts"), list) or
                    len(value["receipts"]) > MAX_RESULT_RECEIPTS):
                raise ValueError("invalid result receipt state")
            recovered = OrderedDict()
            for raw in value["receipts"]:
                receipt = _persisted_result_receipt(raw)
                receipt_id = receipt["receipt_id"]
                if receipt_id in recovered:
                    raise ValueError("duplicate persisted receipt id")
                recovered[receipt_id] = receipt
            self.result_receipts = recovered
        except (OSError, UnicodeError, ValueError, TypeError,
                json.JSONDecodeError) as error:
            _server_log("RESULT RECEIPTS ignored invalid state: %s" % error)
            self.result_receipts = OrderedDict()

    def _persist_result_receipts(self, receipts=None):
        """Atomically persist the current unacknowledged receipt ledger."""
        if self.receipt_state_path is None:
            return
        receipts = self.result_receipts if receipts is None else receipts
        rows = [_persisted_result_receipt(receipt)
                for receipt in receipts.values()]
        _write_json_atomic(self.receipt_state_path, {
            "schema": RESULT_RECEIPT_STATE_SCHEMA,
            "receipts": rows[-MAX_RESULT_RECEIPTS:],
        })

    def _result_receipts_for_account(self, account_key):
        """Return this account's unacknowledged receipts oldest first."""
        return [receipt for receipt in self.result_receipts.values()
                if receipt.get("account_key") == account_key]

    def _choose_map(self, map_pool=None):
        map_pool = MAP_POOL if map_pool is None else tuple(map_pool)
        if self.map_option in (None, "", "random", DEFAULT_MAP):
            return random.choice(map_pool)
        selected = str(self.map_option)
        if selected not in ALL_MAP_POOL:
            raise ValueError("unsupported standard map: %s" % selected)
        return selected

    def _active_map_pool(self):
        return CLIENT_MAP_POOLS.get(self.client_build, MAP_POOL)

    def _team_sizes_wire(self):
        return {str(team): self.team_sizes[team] for team in (1, 2)}

    def _server_time_ms(self):
        return max(0, int(round(float(self.tick) * 1000.0 / TICK_HZ)))

    def _motion_time_us(self):
        return max(0, int(round(
            (float(self._motion_clock()) - self._motion_clock_origin) *
            1000000.0)))

    def _logical_motion_time_us(self, raw_time_us=None):
        if raw_time_us is None:
            raw_time_us = self._motion_time_us()
        return min(
            MAX_MOTION_TIME_US,
            int(raw_time_us) + int(self.motion_time_offset_us))

    def _projectile_wire(self, record):
        result = {
            "projectile_id": record["projectile_id"],
            "shooter_kind": record["shooter_kind"],
            "shooter_id": record["shooter_id"],
            "source_vehicle": record["source_vehicle"],
            "source_shot": _projectile_source_shot(
                record["source_shot"]),
            "shot_seq": record["shot_seq"],
            "burst_group_seq": record["burst_group_seq"],
            "burst_index": record["burst_index"],
            "burst_count": record["burst_count"],
            "shell_index": record["shell_index"],
            "team": record["team"],
            "origin": list(record["origin"]),
            "velocity": list(record["velocity"]),
            "range_origin": list(record["range_origin"]),
            "segment_origin": list(record["segment_origin"]),
            "segment_velocity": list(record["segment_velocity"]),
            "segment_start_time_ms": record["segment_start_time_ms"],
            "ricochet_count": record["ricochet_count"],
            "base_penetration_multiplier": record[
                "base_penetration_multiplier"],
            "gravity": record["gravity"],
            "max_distance": record["max_distance"],
            "max_time_ms": record["max_time_ms"],
            "is_he": record["is_he"],
            "splash_radius": record["splash_radius"],
            "penetration_factor": record["penetration_factor"],
            "launch_server_time_ms": record["launch_server_time_ms"],
            "checked_through_ms": record["checked_through_ms"],
            "checked_distance": record["checked_distance"],
            "piercing_loss": record["piercing_loss"],
            "authority_epoch": self.authority_epoch,
        }
        if record.get("fire_intent_seq") is not None:
            result["fire_intent_seq"] = int(record["fire_intent_seq"])
            result["fire_input_seq"] = int(record["fire_input_seq"])
        return result

    def _projectile_snapshot(self):
        return [self._projectile_wire(self.projectiles[projectile_id])
                for projectile_id in sorted(self.projectiles)]

    def _message_round_matches(self, message):
        """Fence round-aware clients while accepting 0.8.2 payloads."""
        if not isinstance(message, dict):
            return False
        if "round_id" not in message:
            return True
        try:
            raw_round = message.get("round_id")
            parsed_round = int(raw_round)
            return (not isinstance(raw_round, bool) and
                    float(raw_round) == parsed_round and
                    parsed_round == self.round_id)
        except (TypeError, ValueError, OverflowError):
            return False

    def _set_protocol_reject(self, kind, code, detail):
        """Record one exact rejection without changing protocol semantics."""
        setattr(self, "last_%s_reject_code" % kind, str(code))
        setattr(self, "last_%s_reject" % kind, str(detail))
        return False

    def _clear_protocol_reject(self, kind):
        setattr(self, "last_%s_reject_code" % kind, "")
        setattr(self, "last_%s_reject" % kind, "")

    def _set_protocol_exception(self, kind, error):
        """Record a stable low-volume code plus the exact validation error."""
        detail = str(error or "invalid payload")
        code = re.sub(r"[^a-z0-9]+", "_", detail.lower()).strip("_")
        return self._set_protocol_reject(
            kind, (code or "invalid_payload")[:64], detail)

    def should_log_protocol_reject(self, kind, accepted):
        """Log only the first rejection in one continuous reason cascade."""
        if accepted:
            self._logged_protocol_reject_codes.pop(kind, None)
            return False
        code = getattr(self, "last_%s_reject_code" % kind, "unknown")
        if self._logged_protocol_reject_codes.get(kind) == code:
            return False
        self._logged_protocol_reject_codes[kind] = code
        return True

    @staticmethod
    def _validate_combat_event_for_wire(event):
        """Reject incomplete cause metadata before any combat event ships."""
        kind = event.get("kind")
        if kind not in COMBAT_EVENT_KINDS:
            return True
        if "source" not in event:
            raise RuntimeError("combat event has no source")
        source = event["source"]
        if source not in COMBAT_SOURCE_KINDS:
            raise RuntimeError("combat event has invalid source: %s" % source)
        if kind not in COMBAT_SOURCE_KINDS[source]:
            raise RuntimeError(
                "combat source %s does not allow kind %s" % (source, kind))
        if "death_reason" not in event:
            raise RuntimeError("combat event has no death_reason")
        death_reason = event["death_reason"]
        if (isinstance(death_reason, bool) or
                not isinstance(death_reason, int) or death_reason < 0):
            raise RuntimeError("combat event has invalid death_reason")
        if not event.get("dead", False) and death_reason != 0:
            raise RuntimeError(
                "nonfatal combat event has nonzero death_reason")
        if "damage_sticker" in event:
            damage_sticker = event.get("damage_sticker")
            if (source != "shot" or bool(event.get("splash", False)) or
                    isinstance(damage_sticker, bool) or
                    not isinstance(damage_sticker, int) or
                    not 0 <= damage_sticker <=
                    PROJECTILE_MAX_DAMAGE_STICKER):
                raise RuntimeError(
                    "combat event has invalid damage_sticker")
        has_attacker = "attacker" in event or "attacker_bot" in event
        if "attacker" in event and "attacker_bot" in event:
            raise RuntimeError("combat event has ambiguous attacker")
        if source == "player_left":
            if (event.get("attack_reason", object()) is not None or
                    has_attacker):
                raise RuntimeError(
                    "player_left event must be an explicit non-attack cause")
            return True
        if "attack_reason" not in event:
            raise RuntimeError("combat event has no attack_reason")
        attack_reason = event["attack_reason"]
        if (isinstance(attack_reason, bool) or
                not isinstance(attack_reason, int) or attack_reason < 0):
            raise RuntimeError("combat event has invalid attack_reason")
        if source in ("shot", "fire", "ram"):
            expected = {"shot": 0, "fire": 1, "ram": 2}[source]
            if not has_attacker or attack_reason != expected:
                raise RuntimeError(
                    "combat event attacker/cause does not match source %s" %
                    source)
        elif source == "environment":
            dead = bool(event.get("dead", False))
            valid_reason = (
                (attack_reason == 3 and
                 death_reason == (3 if dead else 0)) or
                (attack_reason in (5, 7) and dead and
                 death_reason == attack_reason))
            if has_attacker or not valid_reason:
                raise RuntimeError(
                    "environment event has invalid attacker or cause")
        elif has_attacker:
            raise RuntimeError(
                "client_simulation event must not have an attacker")
        return True

    def _new_bot_roster(self, occupied_slots=None):
        occupied_slots = set(occupied_slots or ())
        roster = []
        used = set()
        callsigns = (BOT_CALLSIGNS_0922
                     if self.client_build == CLIENT_BUILD_0922
                     else BOT_CALLSIGNS)
        available_callsigns = (list(callsigns)
                               if self.client_build == CLIENT_BUILD_0922
                               else [])
        if available_callsigns:
            random.shuffle(available_callsigns)
        for team in (1, 2):
            for slot in range(self.team_sizes[team]):
                if (team, slot) in occupied_slots:
                    continue
                if available_callsigns:
                    name = available_callsigns.pop()
                else:
                    # The legacy port can request more names than its compact
                    # callsign pool. Preserve its bounded uniqueness fallback.
                    while True:
                        name = "%s-%02d" % (
                            random.choice(callsigns), random.randint(10, 99))
                        if name.lower() not in used:
                            break
                used.add(name.lower())
                # Preserve the canonical id for a team slot even when humans
                # occupy other slots.  This keeps bot identity deterministic
                # across different waiting-room sizes without slot collisions.
                bot_id = slot + 1 if team == 1 else slot + 16
                roster.append({"id": bot_id, "team": team, "slot": slot, "name": name})
        return roster

    def _elect_bot_authority(self):
        if self.client_build == CLIENT_BUILD_0922:
            if (self.simulation_worker is not None and
                    self.simulation_worker.connected):
                connected = [SIMULATION_WORKER_AUTHORITY_ID]
            else:
                # A visible #1513 client is never a simulation authority.
                # Missing infrastructure is an explicit round failure, not a
                # reason to move bot work back into one player's renderer.
                connected = []
        else:
            connected = sorted(
                p.player_id for p in self.players.values()
                if p.connected and
                (self.phase not in ("loading", "battle") or p.participating))
        old = self.bot_authority_id
        self.bot_authority_id = connected[0] if connected else None
        if old != self.bot_authority_id:
            # A new producer has a new source-clock origin. The logical motion
            # offset is round-scoped, however: dropping it here would make the
            # public snapshot clock stall until raw time caught up.
            self.bot_source_time_us = None
            self.bot_source_receipt_time_us = None
            self.bot_source_batch_horizon_us = None
            self.player_environment = {}
            self.player_environment_seq = -1
            self.player_environment_authority_epoch = -1
            self.player_drowning_seconds = {}
        if (old != self.bot_authority_id and
                self.client_build == CLIENT_BUILD_0922):
            self.authority_epoch += 1
            self.bot_pending_projectile_launches.clear()
            self.bot_pending_projectile_metadata.clear()
            self.bot_launch_clock_offset_us = None
            self.bot_last_projectile_launch_time_us.clear()
        if (old != self.bot_authority_id and
                self.phase in ("loading", "battle")):
            self.bot_manifest_authority_id = None
            self.bot_planner.clear_observations()
            event = {
                "kind": "authority",
                "player_id": self.bot_authority_id,
                "round_id": self.round_id,
            }
            if self.client_build == CLIENT_BUILD_0922:
                event["authority_epoch"] = self.authority_epoch
            self.pending_events.append(event)
        return old, self.bot_authority_id

    def _connected_endpoints(self, participating_only=False):
        endpoints = [
            player for player in self.players.values()
            if player.connected and
            (not participating_only or player.participating)]
        worker = self.simulation_worker
        if worker is not None and worker.connected:
            endpoints.append(worker)
        return endpoints

    def _endpoint_is_current(self, endpoint, participating_only=False):
        if isinstance(endpoint, SimulationWorker):
            return (self.simulation_worker is endpoint and
                    endpoint.connected)
        return (self.players.get(endpoint.player_id) is endpoint and
                endpoint.connected and
                (not participating_only or endpoint.participating))

    def _remove_endpoint(self, endpoint):
        if isinstance(endpoint, SimulationWorker):
            return self.remove_simulation_worker(endpoint)
        return self.remove_player(endpoint.player_id, expected=endpoint)

    def _elect_room_host(self):
        connected = sorted(
            p.player_id for p in self.players.values() if p.connected)
        self.host_player_id = connected[0] if connected else None
        return self.host_player_id

    def _spawn_for(self, slot, team):
        # Coordinates are intentionally simple and are also sent to clients.
        # The client maps these onto the same local battle space.
        # Keep the synthetic arena small; clients map it onto the loaded map.
        return self._spawn_x_for(slot), self._spawn_z_for(team), (0.0 if team == 1 else math.pi)

    @staticmethod
    def _spawn_x_for(slot):
        return float(int(slot) * 12.0)

    @staticmethod
    def _spawn_z_for(team):
        return -35.0 if team == 1 else 35.0

    def _unique_name(self, requested, address, player_id):
        fallback = "Player%d" % player_id
        base = _safe_name(requested, fallback)
        if base.lower() in ("defaultplayer", "player", "offline_player"):
            address_tail = str(address[0]).rsplit(".", 1)[-1]
            if not address_tail.isdigit():
                address_tail = str(player_id)
            base = "Player-%s" % address_tail
        existing = set(p.name.lower() for p in self.players.values() if p.connected)
        candidate = base
        suffix = 2
        while candidate.lower() in existing:
            suffix_text = "-%d" % suffix
            candidate = base[:max(1, 24 - len(suffix_text))] + suffix_text
            suffix += 1
        return candidate

    def add_player(self, conn, address, hello):
        with self.lock:
            if self.phase != "waiting":
                return None, "battle_in_progress"
            if hello.get("role", "player") != "player":
                return None, "unsupported_role"
            declared_client_build = hello.get(
                "client_build", CLIENT_BUILD_082)
            if (not isinstance(declared_client_build, str) or
                    not declared_client_build or
                    len(declared_client_build) > 128):
                return None, "unsupported_client_build"
            raw_capabilities = hello.get("capabilities", ())
            modern_capabilities = _valid_capability_subset(
                raw_capabilities, MODERN_CLIENT_REQUIRED_CAPABILITIES)
            # The build label is diagnostic only for capability-complete
            # #1513 peers.  Keep the known 0.8.2 path for truly legacy hellos,
            # then normalize every negotiated modern peer to the server's
            # internal build family so later room logic remains unchanged.
            client_build = (
                CLIENT_BUILD_082
                if (declared_client_build == CLIENT_BUILD_082 and
                    not modern_capabilities)
                else CLIENT_BUILD_0922)
            try:
                requested_team = _requested_team(
                    hello.get("requested_team"))
            except ValueError:
                return None, "invalid_team"
            capabilities = ()
            if client_build == CLIENT_BUILD_0922:
                if not modern_capabilities:
                    return None, "unsupported_capabilities"
                capabilities = tuple(raw_capabilities)
                account_key = hello.get("account_key")
                if account_key is None:
                    # Protocol-v5 clients released before durable receipts did
                    # not send this field.  Keep them joinable; current clients
                    # always send the persistent random key.
                    legacy = "".join(
                        character for character in str(
                            hello.get("name", "player"))
                        if character.isalnum())[:48]
                    account_key = "legacy_%s" % (legacy or "player")
                if (not isinstance(account_key, str) or
                        not 1 <= len(account_key) <= 64 or
                        any(character not in
                            "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"
                            for character in account_key)):
                    return None, "invalid_account_key"
                if any(participant.account_key == account_key
                       for participant in self.players.values()
                       if participant.connected):
                    # The account key owns durable receipts.  Letting two
                    # live players share it would collapse their frozen round
                    # rows and overwrite one player's result at battle end.
                    return None, "duplicate_account_key"
                try:
                    outfits = _validated_outfits(hello.get("outfits"))
                except ValueError:
                    return None, "invalid_outfits"
                try:
                    vehicle_compact_descr = \
                        _validated_vehicle_compact_descr(
                            hello.get("vehicle_compact_descr"))
                except ValueError:
                    return None, "invalid_vehicle_configuration"
                try:
                    effective_params = _validated_effective_params(
                        hello.get("effective_params"))
                except ValueError:
                    return None, "invalid_effective_params"
            else:
                account_key = ""
                outfits = {}
                vehicle_compact_descr = ""
                effective_params = {}
            if client_build == CLIENT_BUILD_0922:
                try:
                    max_health = _exact_int(
                        hello.get("max_health"), 1, 100000)
                except (TypeError, ValueError, OverflowError):
                    return None, "invalid_max_health"
            else:
                max_health = max(1, min(int(_finite_float(
                    hello.get("max_health"), 1000)), 100000))
            if (self.client_build is not None and
                    client_build != self.client_build):
                return None, "incompatible_client_build"
            if len(self.players) >= self.max_players:
                return None, "full"
            if self.client_build is None:
                map_pool = CLIENT_MAP_POOLS[client_build]
                if (self.map_option not in (None, "", "random", DEFAULT_MAP) and
                        str(self.map_option) not in map_pool):
                    return None, "map_not_available_for_client"
                self.client_build = client_build
                self.map_name = self._choose_map(map_pool)
            occupied = {
                team: {player.slot for player in self.players.values()
                       if player.connected and player.team == team}
                for team in (1, 2)}
            available = {
                team: [slot for slot in range(self.team_sizes[team])
                       if slot not in occupied[team]]
                for team in (1, 2)}
            candidates = ([requested_team] if requested_team in (1, 2)
                          else [team for team in (1, 2)
                                if available[team]])
            if (requested_team in (1, 2) and
                    not available[requested_team]):
                return None, "team_full"
            if not candidates:
                return None, "team_full"
            team = min(candidates, key=lambda value: (
                len(occupied[value]), value))
            slot = available[team][0]
            player_id = self.next_id
            self.next_id += 1
            x, z, yaw = self._spawn_for(slot, team)
            player = Player(
                player_id=player_id,
                conn=conn,
                address=address,
                name=self._unique_name(hello.get("name"), address, player_id),
                vehicle=_safe_vehicle(
                    hello.get("vehicle"), CLIENT_DEFAULT_VEHICLES[client_build]),
                team=team,
                slot=slot,
                x=x,
                z=z,
                yaw=yaw,
                aim_yaw=yaw,
                health=max_health,
                max_health=max_health,
                capabilities=capabilities,
                account_key=account_key,
                outfits=outfits,
                vehicle_compact_descr=vehicle_compact_descr,
                effective_params=effective_params,
            )
            self.players[player_id] = player
            if self.host_player_id is None:
                self.host_player_id = player_id
            self.state_revision += 1
            return player, None

    def select_team(self, player_id, requested_team):
        """Move one waiting player to a requested team under capacity lock."""
        with self.lock:
            player = self.players.get(player_id)
            if (player is None or not player.connected or
                    self.phase != "waiting"):
                return False, "not_waiting"
            try:
                team = _requested_team(requested_team)
            except ValueError:
                return False, "invalid_team"
            if team not in (1, 2):
                return False, "invalid_team"
            if team == player.team:
                return True, None
            occupied = {
                participant.slot for participant in self.players.values()
                if participant.connected and participant.team == team
            }
            slots = [slot for slot in range(self.team_sizes[team])
                     if slot not in occupied]
            if not slots:
                return False, "team_full"
            player.team = team
            player.slot = slots[0]
            player.x, player.z, player.yaw = self._spawn_for(
                player.slot, player.team)
            player.y = 0.0
            player.aim_yaw = player.yaw
            self.state_revision += 1
            return True, None

    def set_team_size(self, player_id, requested_team, requested_size):
        """Let the waiting-room host change one next-round team capacity."""
        with self.lock:
            player = self.players.get(player_id)
            if (player is None or not player.connected or
                    self.phase != "waiting"):
                return False, "not_waiting"
            if player_id != self.host_player_id:
                return False, "host_only"
            try:
                team = _exact_int(requested_team, 1, 2)
            except ValueError:
                return False, "invalid_team"
            try:
                size = _exact_int(requested_size, 1, 15)
            except ValueError:
                return False, "invalid_size"

            participants = sorted(
                (participant for participant in self.players.values()
                 if participant.connected and participant.team == team),
                key=lambda participant: (
                    participant.slot, participant.player_id))
            if len(participants) > size:
                return False, "team_occupied"
            if self.team_sizes[team] == size:
                return True, None

            # Leaving players may create slot gaps. Compact only the affected
            # waiting team before shrinking so every retained player remains
            # inside the new capacity and receives the matching spawn.
            for slot, participant in enumerate(participants):
                if participant.slot == slot:
                    continue
                participant.slot = slot
                participant.x, participant.z, participant.yaw = (
                    self._spawn_for(slot, team))
                participant.y = 0.0
                participant.aim_yaw = participant.yaw
            self.team_sizes[team] = size
            self.team_size = max(self.team_sizes.values())
            occupied_slots = {
                (participant.team, participant.slot)
                for participant in self.players.values()
                if participant.connected
            }
            self.bot_roster = self._new_bot_roster(occupied_slots)
            self.state_revision += 1
            return True, None

    def set_bot_tier_mode(self, player_id, requested_mode):
        """Let only the room host choose the next round's Bot tier preset."""
        with self.lock:
            player = self.players.get(player_id)
            if (player is None or not player.connected or
                    self.phase != "waiting"):
                return False, "not_waiting"
            if player_id != self.host_player_id:
                return False, "host_only"
            mode = bot_planner.normalize_bot_tier_mode(requested_mode)
            if mode != requested_mode:
                return False, "invalid_mode"
            if mode == self.bot_tier_mode:
                return True, None
            self.bot_tier_mode = mode
            self.state_revision += 1
            return True, None

    def add_simulation_worker(self, conn, address, hello):
        """Admit one #1513 native worker without allocating a player slot."""
        with self.lock:
            if self.phase != "waiting":
                return None, "battle_in_progress"
            if hello.get("role") != SIMULATION_WORKER_ROLE:
                return None, "unsupported_role"
            if (self.simulation_worker is not None and
                    self.simulation_worker.connected):
                return None, "worker_already_connected"
            declared_client_build = hello.get("client_build")
            if (not isinstance(declared_client_build, str) or
                    not declared_client_build or
                    len(declared_client_build) > 128):
                return None, "unsupported_client_build"
            raw_capabilities = hello.get("capabilities", ())
            if not _valid_capability_subset(
                    raw_capabilities,
                    SIMULATION_WORKER_REQUIRED_CAPABILITIES):
                return None, "unsupported_capabilities"
            capabilities = tuple(raw_capabilities)
            client_build = CLIENT_BUILD_0922
            if (self.client_build is not None and
                    self.client_build != client_build):
                return None, "incompatible_client_build"
            if self.client_build is None:
                map_pool = CLIENT_MAP_POOLS[client_build]
                if (self.map_option not in
                        (None, "", "random", DEFAULT_MAP) and
                        str(self.map_option) not in map_pool):
                    return None, "map_not_available_for_client"
                self.client_build = client_build
                self.map_name = self._choose_map(map_pool)
            worker = SimulationWorker(
                conn=conn, address=address, capabilities=capabilities)
            self.simulation_worker = worker
            self.worker_failure_reason = ""
            self._elect_bot_authority()
            self.state_revision += 1
            return worker, None

    def remove_simulation_worker(self, worker, failure_reason=None):
        """Fence a lost worker and terminate its active #1513 round."""
        with self.lock:
            if self.simulation_worker is not worker:
                return None, False
            was_round_active = self.phase in ("loading", "battle")
            # Fence the producer before changing any published lineage. The
            # socket shutdown stays outside the global state lock.
            worker._mark_disconnected()
            self.simulation_worker = None
            old_authority, unused_new_authority = self._elect_bot_authority()
            round_failed = bool(
                was_round_active and
                old_authority == SIMULATION_WORKER_AUTHORITY_ID)
            if round_failed:
                self.worker_failure_reason = str(
                    failure_reason or "worker_disconnected")
                self.pending_live_message = None
                # A loading client has already entered the native offline
                # arena. Publish the same explicit terminal result used in a
                # live battle so every replica can leave cleanly. This is an
                # infrastructure failure, so it must not mint result receipts.
                self.phase = "battle"
                self._finish_battle(
                    0, self.worker_failure_reason, 0,
                    record_receipts=False)
            else:
                self.worker_failure_reason = ""
            self.state_revision += 1
            if not self.players:
                if round_failed:
                    self._reset_round()
                self.client_build = None
                self.host_player_id = None
            if round_failed:
                _server_log(
                    "WORKER FAILURE round=%d reason=%s; round terminated" % (
                        self.round_id, self.worker_failure_reason))
        worker._shutdown_transport()
        return worker, round_failed

    def select_vehicle(self, player_id, message):
        """Apply one waiting-room garage change before the next round."""
        with self.lock:
            player = self.players.get(player_id)
            if (player is None or not player.connected or
                    self.phase != "waiting"):
                return False
            if self.client_build == CLIENT_BUILD_0922:
                try:
                    max_health = _exact_int(
                        message.get("max_health"), 1, 100000)
                except (TypeError, ValueError, OverflowError):
                    return False
            else:
                max_health = max(1, min(int(_finite_float(
                    message.get("max_health"), player.max_health)),
                    100000))
            vehicle = _safe_vehicle(message.get("vehicle"), player.vehicle)
            try:
                outfits = _validated_outfits(message.get("outfits"))
                vehicle_compact_descr = \
                    _validated_vehicle_compact_descr(
                        message.get("vehicle_compact_descr"))
                effective_params = _validated_effective_params(
                    message.get("effective_params"))
            except ValueError:
                return False
            if (vehicle == player.vehicle and max_health == player.max_health
                    and outfits == player.outfits and
                    vehicle_compact_descr ==
                    player.vehicle_compact_descr and
                    effective_params == player.effective_params):
                return False
            player.vehicle = vehicle
            player.max_health = max_health
            player.health = max_health
            player.siege_state = SIEGE_DISABLED
            player.siege_transition_ticks = 0
            player.outfits = outfits
            player.vehicle_compact_descr = vehicle_compact_descr
            player.effective_params = effective_params
            self.state_revision += 1
            return True

    def remove_player(self, player_id, expected=None):
        with self.lock:
            if (expected is not None and
                    self.players.get(player_id) is not expected):
                return None, False
            player = self.players.pop(player_id, None)
            if player is not None:
                player._mark_disconnected()
                participant = self.round_participants.get(
                    player.account_key)
                if participant is not None and player.participating:
                    participant['alive'] = bool(player.alive)
                    participant['health'] = int(player.health)
                    participant['death_reason'] = int(player.death_reason)
                    participant['death_attacker_kind'] = str(
                        player.death_attacker_kind or '')
                    participant['death_attacker_id'] = int(
                        player.death_attacker_id or 0)
                    participant['frags'] = int(player.frags)
                    participant['team_killer'] = bool(player.team_killer)
                self.state_revision += 1
            self.player_spotted.pop(player_id, None)
            self.player_environment.pop(player_id, None)
            self.player_drowning_seconds.pop(player_id, None)
            self.player_overturn_state.pop(player_id, None)
            self.vehicle_catalogs.pop(player_id, None)
            if player_id == self.host_player_id:
                self._elect_room_host()
            if player_id == self.bot_authority_id:
                self._elect_bot_authority()
            if self.phase == "loading":
                self._activate_battle_if_ready()
            reset = False
            if self.phase in ("loading", "battle"):
                if not self._finish_abandoned_battle():
                    self._maybe_finish_battle()
            if (not self.players and self.phase == "loading" and
                    self.battle_result is None):
                self._reset_round()
                reset = True
            if (not self.players and
                    (self.simulation_worker is None or
                     not self.simulation_worker.connected)):
                self.client_build = None
                self.host_player_id = None
        if player is not None:
            player._shutdown_transport()
        return player, reset

    def _finish_abandoned_battle(self):
        """Resolve a round once no human participant remains connected."""
        if (self.phase != "battle" or self.battle_result is not None or
                any(player.connected and player.participating
                    for player in self.players.values())):
            return False
        for base_team in (1, 2):
            base = self.rules_state.get("bases", {}).get(str(base_team), {})
            if int(_finite_float(base.get("points"), 0)) >= 100:
                return self._finish_battle(
                    3 - base_team, "base captured", base_team)
        if self._maybe_finish_battle():
            return True
        return self._finish_battle(
            self._remaining_bot_winner(), "team_eliminated", 0)

    def _remaining_bot_winner(self):
        """Adjudicate an unobserved remainder from canonical bot state."""
        totals = {
            1: {"alive": 0, "health": 0, "maximum": 0},
            2: {"alive": 0, "health": 0, "maximum": 0},
        }
        for identity in self.bot_manifest:
            team = int(identity.get("team", 0))
            if team not in totals:
                continue
            bot_id = int(identity.get("id", 0))
            state = self.bot_states.get(bot_id, identity)
            maximum = max(1, int(_finite_float(
                identity.get("max_health"), 1)))
            health = max(0, min(int(_finite_float(
                state.get("health"), 0)), maximum))
            alive = bool(state.get("alive", health > 0)) and health > 0
            totals[team]["alive"] += int(alive)
            totals[team]["health"] += health if alive else 0
            totals[team]["maximum"] += maximum
        if totals[1]["alive"] != totals[2]["alive"]:
            return (1 if totals[1]["alive"] > totals[2]["alive"] else 2)
        maximum_1 = totals[1]["maximum"]
        maximum_2 = totals[2]["maximum"]
        if maximum_1 > 0 and maximum_2 > 0:
            ratio_1 = totals[1]["health"] * maximum_2
            ratio_2 = totals[2]["health"] * maximum_1
            if ratio_1 != ratio_2:
                return 1 if ratio_1 > ratio_2 else 2
        if totals[1]["health"] != totals[2]["health"]:
            return (1 if totals[1]["health"] >
                    totals[2]["health"] else 2)
        # An abandoned battle is adjudicated immediately so the single-room
        # server can accept another match.  Break an exact tie deterministically
        # instead of manufacturing the same draw on every early departure.
        return 1 if int(self.round_id) % 2 == 0 else 2

    def _reset_round(self):
        """Return connected players to a clean waiting-room round."""
        self.phase = "waiting"
        self.round_id += 1
        self.tick = 0
        self.map_name = self._choose_map(self._active_map_pool())
        for player in self.players.values():
            player.health = player.max_health
            player.alive = True
            player.critical = {}
            player.critical_revision = 0
            player.critical_report_base_revision = 0
            player.critical_ack_seq = 0
            player.track_repair_fingerprints.clear()
            player.equipment_states = []
            player.equipment_clock = 0.0
            player.equipment_revision = 0
            player.equipment_intent_seq = 0
            player.equipment_intent_fingerprints.clear()
            player.equipment_intent_result = {
                "intent_seq": 0, "accepted": False, "reason": ""}
            player.combat_fire_elapsed = 0.0
            player.combat_fire_timer = 0.0
            player.fire_attacker_kind = ""
            player.fire_attacker_id = 0
            player.death_reason = 0
            player.display_health = None
            player.frags = 0
            player.team_killer = False
            player.death_attacker_kind = ""
            player.death_attacker_id = 0
            player.stun_end_server_time_ms = 0
            player.stun_attacker_kind = ""
            player.stun_attacker_id = 0
            player.participating = True
            player.forward = 0.0
            player.turn = 0.0
            player.speed = 0.0
            player.siege_state = SIEGE_DISABLED
            player.siege_transition_ticks = 0
            player.fire_seq = 0
            player.fire_intent_seq = 0
            player.fire_intent_fingerprints.clear()
            player.pending_fire_intents.clear()
            player.fire_intent_results.clear()
            player.shell_index = 0
            player.next_shell_index = 0
            player.shell_change_pending = False
            player.reported_hits.clear()
            player.client_position = False
            player.ram_contact_seq = 0
            player.ram_contact_resolved_seq = 0
            player.ram_contact = {}
            player.ram_contacts.clear()
            player.ram_contact_rejections.clear()
            player.destructible_contact_seq = 0
            player.destructible_contact_resolved_seq = 0
            player.destructible_contacts.clear()
            player.destructible_contact_resolutions.clear()
            player.destructible_contact_rejections.clear()
            player.input_seq = 0
            player.input_fingerprints.clear()
            player.input_processed_seq = 0
            player.input_decisions.clear()
            player.last_input_reject = {}
            player.input_reject_counts.clear()
            player.input_fault_round = 0
            player.landing_observation_seq = 0
            player.landing_observation_input_seq = 0
            player.landing_observation_fingerprints.clear()
            player.landing_observation_results.clear()
            player.gun_checkpoint_seq = 0
            player.gun_checkpoint = {}
            player.gun_checkpoints.clear()
            player.pose_time_us = None
            player.pose_history.clear()
            player.x, player.z, player.yaw = self._spawn_for(
                player.slot, player.team)
            player.y = 0.0
            player.aim_yaw = player.yaw
            player.pitch = 0.0
            player.roll = 0.0
            player.up_cosine = 1.0
            player.gun_pitch = 0.0
            player.bot_order_revision_sent = -1
            player.destructible_revision_sent = -1
            player.battle_ready_round = 0
        worker = self.simulation_worker
        if worker is not None and worker.connected:
            worker.bot_order_revision_sent = -1
            worker.destructible_revision_sent = -1
            worker.battle_ready_round = 0
            worker.simulation_progress_round_id = 0
            worker.simulation_progress_authority_epoch = -1
            worker.simulation_progress_frame_seq = -1
        self.worker_failure_reason = ""
        self.next_id = max([player.player_id for player in self.players.values()] or [0]) + 1
        occupied_slots = {
            (player.team, player.slot) for player in self.players.values()
            if player.connected}
        self.bot_roster = self._new_bot_roster(occupied_slots)
        self.bot_authority_id = None
        self.authority_epoch = 0
        self.bot_manifest_authority_id = None
        self.bot_manifest = []
        self.bot_manifest_revision = 0
        self.bot_states = {}
        self.bot_terminal_criticals = {}
        self.bot_state_revision = 0
        self.bot_state_time_us = 0
        self.bot_source_time_us = None
        self.bot_source_receipt_time_us = None
        self.bot_source_batch_horizon_us = None
        self.bot_launch_clock_offset_us = None
        self.bot_last_projectile_launch_time_us = {}
        self.motion_time_offset_us = 0
        self.bot_planner.reset()
        self.bot_orders = {"revision": 0, "orders": []}
        self._next_bot_planner_tick = 0
        self.bot_reported_hits = set()
        self.bot_reported_rams = set()
        self.bot_reported_ram_fingerprints = {}
        self.human_collision_profiles = {}
        self.human_collision_profile_authority_id = None
        self.human_collision_manifest_fingerprint = None
        self.human_ram_cooldowns = {}
        self.human_ram_contacts = frozenset()
        self.human_ram_pair_frontiers = {}
        self.human_ram_episode_seq = {}
        self.human_ram_probe_seq = 0
        self.human_ram_probe_requests = {}
        self.human_ram_retired_probe_pairs = OrderedDict()
        self.human_ram_probe_fingerprints = OrderedDict()
        self.vehicle_statistics = {}
        self.vehicle_interactions = {}
        self.round_participants = {}
        self.track_immobilisers = {}
        self.player_spotted = {}
        self.bot_spotted = {}
        self.player_environment = {}
        self.player_environment_seq = -1
        self.player_environment_authority_epoch = -1
        self.player_drowning_seconds = {}
        self.player_overturn_state = {}
        self.rules_state = {"bases": {
            "1": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False},
            "2": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False}}}
        self.battle_result = None
        self.result_reset_tick = None
        self.roster_finalized = False
        self.pending_events = []
        self.pending_live_message = None
        self.capture_bases = {}
        self.capture_threat_bases = {1: [], 2: []}
        self.capture_contributors = {1: {}, 2: {}}
        self.capture_cursors = {1: 0, 2: 0}
        self.destructibles = {}
        self.destructible_revision = 0
        self.projectiles = {}
        self.projectile_tombstones = {}
        self.projectile_revision = 0
        self.bot_pending_projectile_launches = set()
        self.bot_pending_projectile_metadata = {}
        self.last_bot_state_reject = ""
        self.last_bot_state_reject_code = ""
        self.last_bot_hit_reject = ""
        self.last_bot_hit_reject_code = ""
        self.last_bot_human_hit_reject = ""
        self.last_bot_human_hit_reject_code = ""
        self.last_bot_manifest_reject = ""
        self.last_bot_manifest_reject_code = ""
        self.last_projectile_launch_reject = ""
        self.last_projectile_launch_reject_code = ""
        self.last_projectile_progress_reject = ""
        self.last_projectile_progress_reject_code = ""
        self.last_projectile_ricochet_reject = ""
        self.last_projectile_ricochet_reject_code = ""
        self.last_projectile_resolve_reject = ""
        self.last_projectile_resolve_reject_code = ""
        self._logged_protocol_reject_codes = {}
        self._elect_room_host()
        if worker is not None and worker.connected:
            self._elect_bot_authority()
        self.state_revision += 1

    def _authority_fields(self):
        worker = self.simulation_worker
        if worker is not None and worker.connected:
            return {
                "worker_status": "connected",
                "worker_failure_reason": "",
            }
        if self.worker_failure_reason:
            return {
                "worker_status": "failed",
                "worker_failure_reason": self.worker_failure_reason,
            }
        return {"worker_status": "missing"}

    def lobby_message(self):
        with self.lock:
            message = {
                "type": "roster",
                "protocol": PROTOCOL_VERSION,
                "client_build": self.client_build,
                "phase": self.phase,
                "round_id": self.round_id,
                "state_revision": self.state_revision,
                "map": self.map_name,
                "map_pool": list(self._active_map_pool()),
                "host_player_id": self.host_player_id,
                "bot_authority_id": self.bot_authority_id,
                "team_size": self.team_size,
                "team_sizes": self._team_sizes_wire(),
                "bot_tier_mode": self.bot_tier_mode,
                "players": [self._public_player(p)
                            for p in self.players.values() if p.connected],
            }
            if self.client_build == CLIENT_BUILD_0922:
                message["authority_epoch"] = self.authority_epoch
                message.update(self._authority_fields())
            return message

    def request_start(self, player_id, requested_map=None):
        with self.lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return None, "player_not_found"
            if self.phase != "waiting":
                return None, "already_started"
            if (self.client_build == CLIENT_BUILD_0922 and
                    player_id != self.host_player_id):
                return None, "host_only"
            if (self.client_build == CLIENT_BUILD_0922 and
                    (self.simulation_worker is None or
                     not self.simulation_worker.connected)):
                return None, "simulation_worker_required"
            if (self.client_build == CLIENT_BUILD_0922 and any(
                    PROJECTILE_CAPABILITY not in participant.capabilities
                    for participant in self.players.values()
                    if participant.connected)):
                return None, "missing_projectile_capability"
            if (self.client_build == CLIENT_BUILD_0922 and
                    (any(HUMAN_RAM_TIMELINE_CAPABILITY not in
                         participant.capabilities
                         for participant in self.players.values()
                         if participant.connected) or
                     self.simulation_worker is None or
                     HUMAN_RAM_TIMELINE_CAPABILITY not in
                     self.simulation_worker.capabilities)):
                return None, "missing_human_ram_timeline_capability"
            if (self.client_build == CLIENT_BUILD_0922 and
                    (any(RAM_CONTACT_LEDGER_CAPABILITY not in
                         participant.capabilities
                         for participant in self.players.values()
                         if participant.connected) or
                     self.simulation_worker is None or
                     RAM_CONTACT_LEDGER_CAPABILITY not in
                     self.simulation_worker.capabilities)):
                return None, "missing_ram_contact_ledger_capability"
            if (self.client_build == CLIENT_BUILD_0922 and
                    (any(PLAYER_FIRE_INTENT_CAPABILITY not in
                         participant.capabilities
                         for participant in self.players.values()
                         if participant.connected) or
                     self.simulation_worker is None or
                     PLAYER_FIRE_INTENT_CAPABILITY not in
                     self.simulation_worker.capabilities)):
                return None, "missing_player_fire_intent_capability"
            if (self.client_build == CLIENT_BUILD_0922 and
                    (any(PLAYER_ENVIRONMENT_CAPABILITY not in
                         participant.capabilities
                         for participant in self.players.values()
                         if participant.connected) or
                     self.simulation_worker is None or
                     PLAYER_ENVIRONMENT_CAPABILITY not in
                     self.simulation_worker.capabilities)):
                return None, "missing_player_environment_capability"
            if (self.client_build == CLIENT_BUILD_0922 and
                    (any(EFFECTIVE_PARAMS_CAPABILITY not in
                         participant.capabilities
                         for participant in self.players.values()
                         if participant.connected) or
                     self.simulation_worker is None or
                     EFFECTIVE_PARAMS_CAPABILITY not in
                     self.simulation_worker.capabilities)):
                return None, "missing_effective_params_capability"
            if (self.client_build == CLIENT_BUILD_0922 and
                    (any(RICOCHET_CONTINUATION_CAPABILITY not in
                         participant.capabilities
                         for participant in self.players.values()
                         if participant.connected) or
                     self.simulation_worker is None or
                     RICOCHET_CONTINUATION_CAPABILITY not in
                     self.simulation_worker.capabilities)):
                return None, "missing_ricochet_continuation_capability"
            if (self.client_build == CLIENT_BUILD_0922 and any(
                    effective_params_wire.canonical(
                        participant.effective_params) is None
                    for participant in self.players.values()
                    if participant.connected)):
                return None, "invalid_effective_params"
            if self.bot_lineup:
                allowed_names = _bot_lineup_allowed_names(
                    self.vehicle_catalogs.get(self.host_player_id))
                if any(
                        raw["vehicle"] not in allowed_names
                        for raw in self.bot_lineup):
                    return None, "invalid_bot_lineup"
            if requested_map not in (None, ""):
                requested_map = str(requested_map)
                active_map_pool = tuple(self._active_map_pool())
                if requested_map == DEFAULT_MAP:
                    if not active_map_pool:
                        return None, "invalid_map"
                    self.map_name = random.choice(active_map_pool)
                else:
                    if requested_map not in active_map_pool:
                        return None, "invalid_map"
                    self.map_name = requested_map
            connected = [p for p in self.players.values() if p.connected]
            self.round_start_time = int(time.time())
            for participant in connected:
                participant.participating = True
                if not self._install_player_equipments(participant):
                    return None, "invalid_player_critical_profile"
            self._freeze_round_participants(connected)
            occupied_slots = {(p.team, p.slot) for p in connected}
            self.bot_roster = self._new_bot_roster(occupied_slots)
            self.roster_finalized = True
            self.phase = ("loading" if self.client_build == CLIENT_BUILD_0922
                          else "battle")
            self._elect_bot_authority()
            self.state_revision += 1
            start_message = {
                "type": "battle_start",
                "protocol": PROTOCOL_VERSION,
                "client_build": self.client_build,
                "round_id": self.round_id,
                "state_revision": self.state_revision,
                "map": self.map_name,
                "requested_by": player_id,
                "host_player_id": self.host_player_id,
                "phase": self.phase,
                "delay": 0.75,
                "players": [self._public_player(p) for p in connected],
                "bots": list(self.bot_roster),
                "team_size": self.team_size,
                "team_sizes": self._team_sizes_wire(),
                "bot_tier_mode": self.bot_tier_mode,
                "bot_lineup": list(self.bot_lineup),
                "bot_authority_id": self.bot_authority_id,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "bot_orders": list(self.bot_orders["orders"]),
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "destructibles": list(self.destructibles.values()),
            }
            if self.client_build == CLIENT_BUILD_0922:
                start_message.update({
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": self._server_time_ms(),
                })
            start_message["bot_authority_id"] = self.bot_authority_id
            if self.client_build == CLIENT_BUILD_0922:
                start_message["authority_epoch"] = self.authority_epoch
                start_message.update(self._authority_fields())
            return start_message, None

    def _install_player_equipments(self, player):
        """Create mutable round equipment state from immutable owner input."""
        params = effective_params_wire.canonical(player.effective_params)
        if params is None or params.get("critical") is None:
            return False
        try:
            contracts = tuple(params.get("equipment") or ())
            states = [
                equipment_mechanics.EquipmentState(contract, 0.0)
                for contract in contracts]
        except (TypeError, ValueError, OverflowError):
            return False
        player.effective_params = params
        player.equipment_states = states
        player.equipment_clock = 0.0
        player.equipment_revision = 1
        player.equipment_intent_seq = 0
        player.equipment_intent_fingerprints.clear()
        player.equipment_intent_result = {
            "intent_seq": 0, "accepted": False, "reason": ""}
        player.combat_fire_elapsed = 0.0
        player.combat_fire_timer = 0.0
        player.fire_attacker_kind = ""
        player.fire_attacker_id = 0
        return True

    def _freeze_round_participants(self, players):
        """Retain receipt identity after a participant disconnects."""
        frozen = {}
        for participant in players:
            tier = 1
            for row in self.vehicle_catalogs.get(
                    participant.player_id, ()):
                if row.get("name") == participant.vehicle:
                    tier = max(1, min(10, int(row.get("level", 1))))
                    break
            frozen[participant.account_key] = {
                "player_id": int(participant.player_id),
                "account_key": participant.account_key,
                "name": participant.name,
                "vehicle": participant.vehicle,
                "vehicle_tier": tier,
                "team": int(participant.team),
                "alive": bool(participant.alive),
                "health": int(participant.health),
                "death_reason": int(participant.death_reason),
                "death_attacker_kind": str(
                    participant.death_attacker_kind or ""),
                "death_attacker_id": int(
                    participant.death_attacker_id or 0),
                "frags": int(participant.frags),
                "team_killer": bool(participant.team_killer),
            }
        self.round_participants = frozen

    def store_vehicle_catalog(self, player_id, message):
        """Keep one connection's eligible-vehicle catalog for lineups."""
        with self.lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return False
            rows = message.get("vehicles")
            if not isinstance(rows, list) or not rows or len(rows) > 1024:
                _server_log(
                    "DESCRIPTOR CATALOG rejected id=%d rows=%s" % (
                        player_id, len(rows) if isinstance(rows, list)
                        else type(rows).__name__))
                return False
            catalog = []
            seen = set()
            for index, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    _server_log(
                        "DESCRIPTOR CATALOG rejected id=%d row=%d: "
                        "not an object" % (player_id, index))
                    return False
                name = _safe_vehicle(raw.get("name"), "")
                try:
                    level = int(raw.get("level"))
                except (TypeError, ValueError):
                    level = 0
                tags = raw.get("tags")
                if (not name or name in seen or not 1 <= level <= 10 or
                        not isinstance(tags, list) or len(tags) > 32):
                    _server_log(
                        "DESCRIPTOR CATALOG rejected id=%d row=%d: %r" % (
                            player_id, index, raw.get("name")))
                    return False
                seen.add(name)
                catalog.append({
                    "name": name, "level": level,
                    "tags": tuple(sorted(str(tag)[:32] for tag in tags)),
                })
            self.vehicle_catalogs[player_id] = tuple(catalog)
            _server_log("DESCRIPTOR CATALOG stored id=%d rows=%d" % (
                player_id, len(catalog)))
            return True

    def _activate_battle_if_ready(self):
        if self.phase != "loading":
            return None
        if (self.client_build == CLIENT_BUILD_0922 and
                (self.simulation_worker is None or
                 not self.simulation_worker.connected or
                 self.bot_authority_id !=
                 SIMULATION_WORKER_AUTHORITY_ID)):
            return None
        participants = [
            player for player in self.players.values()
            if player.connected and player.participating]
        worker = self.simulation_worker
        worker_required = (
            self.bot_authority_id == SIMULATION_WORKER_AUTHORITY_ID)
        human_profiles_required = self._human_ram_profiles_required()
        if (not participants or
                (self.bot_roster and
                 self.bot_manifest_authority_id != self.bot_authority_id) or
                (human_profiles_required and
                 (self.bot_manifest_authority_id != self.bot_authority_id or
                  self.human_collision_profile_authority_id !=
                  self.bot_authority_id)) or
                any(
                player.battle_ready_round != self.round_id
                for player in participants) or
                (worker_required and
                 (worker is None or not worker.connected or
                  worker.battle_ready_round != self.round_id))):
            return None
        self.phase = "battle"
        self.tick = 0
        self._next_bot_planner_tick = 0
        self.state_revision += 1
        live_message = {
            "type": "battle_live",
            "protocol": PROTOCOL_VERSION,
            "client_build": self.client_build,
            "round_id": self.round_id,
            "server_tick": self.tick,
            "state_revision": self.state_revision,
            "bot_authority_id": self.bot_authority_id,
            "authority_epoch": self.authority_epoch,
            "server_time_ms": self._server_time_ms(),
            "countdown_seconds": PREBATTLE_SECONDS,
            "battle_duration_seconds": BATTLE_DURATION_SECONDS,
            "timing": self._timing_payload(),
        }
        live_message.update(self._authority_fields())
        # The tick thread is the only publisher of this barrier.  It sends the
        # barrier before advancing tick zero or publishing the first snapshot,
        # so every TCP stream observes one ordered transition into PREBATTLE.
        self.pending_live_message = {
            "round_id": self.round_id,
            "recipients": tuple(
                participants + ([worker] if worker_required else [])),
            "message": live_message,
        }
        return live_message

    def _timing_payload(self):
        """Return server-authoritative phase time as relative milliseconds."""
        prebattle_ticks = int(round(PREBATTLE_SECONDS * TICK_HZ))
        battle_ticks = int(round(BATTLE_DURATION_SECONDS * TICK_HZ))
        total_ticks = prebattle_ticks + battle_ticks
        tick = max(0, int(self.tick))
        if self.phase == "loading":
            phase = "loading"
        elif self.battle_result is not None or tick >= total_ticks:
            phase = "finished"
        elif tick < prebattle_ticks:
            phase = "prebattle"
        else:
            phase = "battle"
        return {
            "phase": phase,
            "start_in_ms": max(
                0, int(round(1000.0 * (prebattle_ticks - tick) /
                             TICK_HZ))),
            "remaining_ms": max(
                0, int(round(1000.0 *
                             (total_ticks - max(tick, prebattle_ticks)) /
                             TICK_HZ))),
            "duration_ms": int(round(
                BATTLE_DURATION_SECONDS * 1000.0)),
        }

    def mark_battle_ready(self, player_id, message):
        """Open one shared countdown after every #1513 client loaded."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "loading"):
                return None
            if player_id == SIMULATION_WORKER_AUTHORITY_ID:
                player = self.simulation_worker
                if (player is None or not player.connected or
                        self.bot_authority_id !=
                        SIMULATION_WORKER_AUTHORITY_ID):
                    return None
            else:
                player = self.players.get(player_id)
                if (player is None or not player.connected or
                        not player.participating):
                    return None
            if player_id == self.bot_authority_id:
                bases = self._sanitize_capture_bases(message.get("bases"))
                if bases:
                    self.capture_bases = bases
            player.battle_ready_round = self.round_id
            return self._activate_battle_if_ready()

    def activate_battle_if_ready(self):
        """Re-evaluate the barrier when its final prerequisite arrives."""
        with self.lock:
            return self._activate_battle_if_ready()

    @staticmethod
    def _sanitize_capture_bases(raw):
        if not isinstance(raw, dict):
            return {}
        result = {}
        for team in (1, 2):
            values = raw.get(str(team), raw.get(team))
            if not isinstance(values, (list, tuple)):
                continue
            points = []
            for value in values[:4]:
                try:
                    if isinstance(value, dict):
                        x, z = value.get("x"), value.get("z")
                    else:
                        x, z = value[0], value[1]
                    x = _finite_float(x, float("nan"))
                    z = _finite_float(z, float("nan"))
                    if not math.isfinite(x) or not math.isfinite(z):
                        continue
                    points.append((round(_clamp(x, -2000.0, 2000.0), 3),
                                   round(_clamp(z, -2000.0, 2000.0), 3)))
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
            if points:
                result[team] = points
        return result if 1 in result and 2 in result else {}

    def loading_snapshot(self):
        """Publish the accepted canonical bot lineup before the load barrier."""
        with self.lock:
            if (self.phase != "loading" or
                    self.bot_manifest_authority_id != self.bot_authority_id):
                return None
            message = {
                "type": "snapshot",
                "protocol": PROTOCOL_VERSION,
                "client_build": self.client_build,
                "server_tick": 0,
                "round_id": self.round_id,
                "map": self.map_name,
                "bot_authority_id": self.bot_authority_id,
                "authority_epoch": self.authority_epoch,
                "server_time_ms": self._server_time_ms(),
                "motion_time_us": self._logical_motion_time_us(),
                "bot_state_time_us": self.bot_state_time_us,
                "players": [self._public_player(p) for p in self.players.values()
                            if p.connected and p.participating],
                "bots": [self.bot_states[key] for key in sorted(self.bot_states)],
                "bot_state_revision": self.bot_state_revision,
                "projectile_revision": self.projectile_revision,
                "projectiles": self._projectile_snapshot(),
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "destructibles": list(self.destructibles.values()),
            }
            message.update(self._authority_fields())
            return message

    @staticmethod
    def _sanitize_destructible(message):
        if not isinstance(message, dict):
            return None
        kind = str(message.get("destructible_kind", ""))
        if kind not in DESTRUCTIBLE_KINDS:
            return None
        try:
            chunk_id = int(message.get("chunk_id"))
            item_index = int(message.get("item_index"))
            if (isinstance(message.get("chunk_id"), bool) or
                    isinstance(message.get("item_index"), bool) or
                    float(message.get("chunk_id")) != chunk_id or
                    float(message.get("item_index")) != item_index or
                    not -2147483648 <= chunk_id <= 4294967295 or
                    not 0 <= item_index <= 1048575):
                return None
        except (TypeError, ValueError, OverflowError):
            return None
        mat_kind = message.get("mat_kind")
        if mat_kind is not None:
            try:
                parsed = int(mat_kind)
                if (isinstance(mat_kind, bool) or float(mat_kind) != parsed or
                        not 0 <= parsed <= 65535):
                    return None
                mat_kind = parsed
            except (TypeError, ValueError, OverflowError):
                return None
        if kind == "module" and mat_kind is None:
            return None
        is_shot = message.get("is_shot")
        if not isinstance(is_shot, bool):
            return None
        if not _has_finite_fields(
                message, ("x", "y", "z", "fall_yaw", "speed")):
            return None
        event = {
            "kind": "destructible",
            "destructible_kind": kind,
            "chunk_id": chunk_id,
            "item_index": item_index,
            "x": round(_clamp(_finite_float(message.get("x")),
                              -5000.0, 5000.0), 3),
            "y": round(_clamp(_finite_float(message.get("y")),
                              -1000.0, 3000.0), 3),
            "z": round(_clamp(_finite_float(message.get("z")),
                              -5000.0, 5000.0), 3),
            "fall_yaw": round(_clamp(
                _finite_float(message.get("fall_yaw")),
                -math.pi * 4.0, math.pi * 4.0), 6),
            "speed": round(_clamp(_finite_float(message.get("speed")),
                                  -200.0, 200.0), 3),
            "is_shot": is_shot,
        }
        if mat_kind is not None:
            event["mat_kind"] = mat_kind
        return event

    def report_destructible(self, player_id, message):
        """Admit one resolved map destruction into shared LAN state."""
        with self.lock:
            if not self._message_round_matches(message):
                return False
            dedicated_authority = (
                player_id == self.bot_authority_id and
                player_id == SIMULATION_WORKER_AUTHORITY_ID)
            if (self.client_build == CLIENT_BUILD_0922 and
                    not dedicated_authority):
                return False
            if not dedicated_authority:
                player = self.players.get(player_id)
                if (player is None or not player.connected or
                        not player.participating or not player.alive):
                    return False
            event = self._sanitize_destructible(message)
            if event is None:
                return False
            if self.battle_result is not None:
                # A native destruction callback may already be queued when
                # the round terminal overtakes it. Its validated current-round
                # receipt has no remaining state to change.
                return True
            if not self._combat_accepting():
                return False
            key = (event["destructible_kind"], event["chunk_id"],
                   event["item_index"], event.get("mat_kind"))
            if key in self.destructibles:
                if self.client_build != CLIENT_BUILD_0922:
                    return True
                previous = dict(self.destructibles[key])
                previous.pop("revision", None)
                previous.pop("reported_by", None)
                return previous == event
            self.destructible_revision += 1
            event["revision"] = self.destructible_revision
            event["reported_by"] = player_id
            self.destructibles[key] = event
            self.pending_events.append(dict(event))
            return True

    @staticmethod
    def _destructible_contact_result_token(raw_token):
        if (not isinstance(raw_token, list) or
                not 1 <= len(raw_token) <=
                MAX_PLAYER_DESTRUCTIBLE_CONTACT_TOKEN):
            return None
        token = set()
        for raw in raw_token:
            if not isinstance(raw, list) or len(raw) != 3:
                return None
            try:
                chunk_id = _exact_int(raw[0], 0, PROJECTILE_MAX_ID)
                item_index = _exact_int(raw[1], 0, PROJECTILE_MAX_ID)
                mat_kind = (None if raw[2] is None else
                            _exact_int(raw[2], 0, PROJECTILE_MAX_ID))
            except (TypeError, ValueError, OverflowError):
                return None
            if (chunk_id is None or item_index is None or
                    (raw[2] is not None and mat_kind is None)):
                return None
            token.add((chunk_id, item_index, mat_kind))
        if len(token) != len(raw_token):
            return None
        return tuple(sorted(token, key=lambda row: (
            row[0], row[1], -1 if row[2] is None else row[2])))

    @staticmethod
    def _player_destructible_contact_is_resolved(player, seq):
        return bool(
            seq <= player.destructible_contact_resolved_seq or
            seq in player.destructible_contact_resolutions)

    @staticmethod
    def _record_player_destructible_contact_resolution(
            player, seq):
        """Record one bounded terminal row and advance its contiguous prefix."""
        if BattleState._player_destructible_contact_is_resolved(player, seq):
            return
        player.destructible_contact_resolutions[int(seq)] = True
        next_seq = int(player.destructible_contact_resolved_seq) + 1
        while next_seq in player.destructible_contact_resolutions:
            player.destructible_contact_resolutions.pop(next_seq, None)
            player.destructible_contact_resolved_seq = next_seq
            next_seq += 1
        if (len(player.destructible_contact_resolutions) >
                MAX_PLAYER_DESTRUCTIBLE_INFLIGHT):
            raise RuntimeError(
                "destructible selective-ack window exceeded")

    def report_player_destructible_contact_result(self, player_id, message):
        """Consume one hidden-worker verdict for an admitted player sweep."""
        with self.lock:
            if (player_id != SIMULATION_WORKER_AUTHORITY_ID or
                    player_id != self.bot_authority_id or
                    not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    set(message) != {
                        "type", "round_id", "player_id", "contact_seq",
                        "accepted", "token"} or
                    not isinstance(message.get("accepted"), bool)):
                return False
            try:
                target_id = _exact_int(
                    message.get("player_id"), 1, PROJECTILE_MAX_ID)
                seq = _exact_int(
                    message.get("contact_seq"), 1, PROJECTILE_MAX_ID)
            except (TypeError, ValueError, OverflowError):
                return False
            target = self.players.get(target_id)
            token = self._destructible_contact_result_token(
                message.get("token"))
            if target is None or seq is None or token is None:
                return False
            if self._player_destructible_contact_is_resolved(target, seq):
                return True
            if seq not in target.destructible_contacts:
                return False
            pending = target.destructible_contacts[seq]
            expected = self._destructible_contact_result_token(
                pending.get("token"))
            if token != expected:
                return False
            if message["accepted"]:
                for chunk_id, item_index, mat_kind in token:
                    if mat_kind is None:
                        known = any(
                            (kind, chunk_id, item_index, None) in
                            self.destructibles
                            for kind in ("fragile", "column", "tree"))
                    else:
                        known = (
                            "module", chunk_id, item_index, mat_kind) in \
                            self.destructibles
                    if not known:
                        return False
            target.destructible_contacts.pop(seq, None)
            if message["accepted"]:
                self._record_player_destructible_contact_resolution(
                    target, seq)
            else:
                self._reject_player_destructible_contact(
                    target, seq, pending)
            return True

    def _reject_player_destructible_contact(
            self, player, seq, admitted_pose=None):
        """Publish one terminal rejection and retain a snapshot fallback."""
        self._record_player_destructible_contact_resolution(
            player, seq)
        player.destructible_contact_rejections[seq] = True
        while (len(player.destructible_contact_rejections) >
               MAX_PLAYER_DESTRUCTIBLE_REJECTIONS):
            player.destructible_contact_rejections.popitem(last=False)
        terminal = {
            "type": "player_destructible_contact_result",
            "round_id": self.round_id,
            "contact_seq": int(seq),
            "accepted": False,
        }
        if admitted_pose is not None:
            terminal.update({
                "x": float(admitted_pose["x"]),
                "y": float(admitted_pose["y"]),
                "z": float(admitted_pose["z"]),
                "yaw": float(admitted_pose["yaw"]),
            })
        # This ordered relay removes one snapshot interval from correction
        # latency.  The bounded public ledger below is the idempotent fallback
        # if the visible endpoint cannot accept this offer immediately.
        player.offer_reliable(terminal)

    @staticmethod
    def _destructible_key(event):
        return (event["destructible_kind"], event["chunk_id"],
                event["item_index"], event.get("mat_kind"))

    def _normalize_projectile_destructibles(self, receipts):
        """Validate one bounded embedded shot-destruction transaction."""
        if (not isinstance(receipts, list) or
                len(receipts) > PROJECTILE_MAX_DESTRUCTIBLES):
            raise ValueError("invalid projectile destructible batch")
        normalized = []
        seen = set()
        for raw in receipts:
            allowed = {
                "destructible_kind", "chunk_id", "item_index", "x", "y",
                "z", "fall_yaw", "speed", "is_shot", "mat_kind",
            }
            required = allowed - {"mat_kind"}
            if (not isinstance(raw, dict) or set(raw) - allowed or
                    not required.issubset(raw)):
                raise ValueError("invalid projectile destructible shape")
            event = self._sanitize_destructible(raw)
            if event is None or event.get("is_shot") is not True:
                raise ValueError("invalid projectile destructible receipt")
            key = self._destructible_key(event)
            if key in seen:
                raise ValueError("duplicate projectile destructible receipt")
            seen.add(key)
            normalized.append(event)
        return normalized

    def _commit_projectile_destructibles(self, player_id, receipts):
        """Commit only prevalidated receipts; this helper cannot reject."""
        changed = 0
        for event in receipts:
            key = self._destructible_key(event)
            if key in self.destructibles:
                continue
            self.destructible_revision += 1
            stored = dict(event)
            stored["revision"] = self.destructible_revision
            stored["reported_by"] = int(player_id)
            self.destructibles[key] = stored
            self.pending_events.append(dict(stored))
            changed += 1
        return changed

    def leave_battle(self, player_id, message):
        """Retire a client from one round while keeping its lobby socket."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase not in ("loading", "battle")):
                return False
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return False
            if not player.participating:
                return True
            was_alive = bool(player.alive)
            participant = self.round_participants.get(player.account_key)
            if participant is not None:
                # Preserve the final participant state for the result receipt
                # before the unobserved remainder is adjudicated from bot state.
                participant["alive"] = was_alive
                participant["health"] = int(player.health)
                participant["death_reason"] = int(player.death_reason)
                participant["death_attacker_kind"] = str(
                    player.death_attacker_kind or "")
                participant["death_attacker_id"] = int(
                    player.death_attacker_id or 0)
                participant["team_killer"] = bool(player.team_killer)
                participant["frags"] = int(player.frags)
            player.participating = False
            previous_health = player.health
            player.health = 0
            player.alive = False
            player.forward = 0.0
            player.turn = 0.0
            self.state_revision += 1
            if was_alive:
                self.pending_events.append({
                    "kind": "health",
                    "target": player.player_id,
                    "damage": previous_health,
                    "health": 0,
                    "dead": True,
                    "attack_reason": None,
                    "death_reason": 0,
                    "source": "player_left",
                })
            if player_id == self.bot_authority_id:
                self._elect_bot_authority()
            if self.phase == "loading":
                participants = [
                    value for value in self.players.values()
                    if value.connected and value.participating]
                if not participants:
                    # A graceful leave keeps every TCP connection alive.  If
                    # nobody remains in this load, return those same sockets
                    # to a fresh waiting-room round instead of leaving an
                    # impossible ready barrier behind forever.
                    self._reset_round()
                    return True
                # A not-yet-ready participant may be the only remaining
                # blocker.  Re-evaluate under the same state lock that retired
                # it so tick zero cannot observe a half-updated recipient set.
                self._activate_battle_if_ready()
                return True
            if not self._finish_abandoned_battle():
                self._maybe_finish_battle()
            return True

    def leave_battle_and_publish(self, player_id, message):
        """Apply a graceful loading leave and publish its membership atomically."""
        with self.lock:
            was_loading = self.phase == "loading"
            accepted = self.leave_battle(player_id, message)
            if accepted and was_loading:
                self._broadcast_current_roster_locked()
            return accepted

    def current_battle_message(self):
        with self.lock:
            if self.phase != "battle":
                return None
            connected = [
                p for p in self.players.values()
                if p.connected and p.participating]
            takeover_manifest = []
            for identity in self.bot_manifest:
                entry = dict(identity)
                state = self.bot_states.get(int(identity["id"]))
                if self.client_build == CLIENT_BUILD_0922:
                    if state is None:
                        raise RuntimeError(
                            "bot takeover manifest has no canonical state")
                    _validated_bot_reload_progress(state, required=True)
                    for name in (
                            "fire_seq", "reload_time", "reload_duration",
                            "equipment_states", "critical",
                            "combat_revision", "combat_base_revision",
                            "combat_ack_seq", "combat_fire_elapsed",
                            "combat_fire_timer", "stun_end_server_time_ms",
                            "stun_attacker_kind", "stun_attacker_id"):
                        entry[name] = state[name]
                takeover_manifest.append(entry)
            message = {
                "type": "battle_start",
                "protocol": PROTOCOL_VERSION,
                "round_id": self.round_id,
                "state_revision": self.state_revision,
                "map": self.map_name,
                "requested_by": 0,
                "host_player_id": self.host_player_id,
                "delay": 0.75,
                "late_join": True,
                "players": [self._public_player(p) for p in connected],
                "bots": list(self.bot_roster),
                "team_size": self.team_size,
                "team_sizes": self._team_sizes_wire(),
                "bot_tier_mode": self.bot_tier_mode,
                "bot_lineup": list(self.bot_lineup),
                "bot_authority_id": self.bot_authority_id,
                "bot_manifest": takeover_manifest,
                "bot_order_revision": self.bot_orders["revision"],
                "bot_orders": list(self.bot_orders["orders"]),
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "destructibles": list(self.destructibles.values()),
            }
            if self.client_build == CLIENT_BUILD_0922:
                message.update({
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": self._server_time_ms(),
                    "projectile_revision": self.projectile_revision,
                    "projectiles": self._projectile_snapshot(),
                })
                message.update(self._authority_fields())
            return message

    def _human_ram_profiles_required(self):
        """Require authority-derived human bodies for every modern round."""
        worker = self.simulation_worker
        participants = [
            player for player in self.players.values()
            if player.connected and player.participating]
        return bool(
            self.client_build == CLIENT_BUILD_0922 and participants and
            self.bot_authority_id == SIMULATION_WORKER_AUTHORITY_ID and
            worker is not None and worker.connected)

    def _sanitize_human_collision_profiles(self, raw_profiles):
        """Bind worker-donated collision bodies to this exact player roster."""
        if not isinstance(raw_profiles, (list, tuple)):
            return None
        active = {
            player.player_id: player for player in self.players.values()
            if player.connected and player.participating}
        frozen = {}
        for participant in self.round_participants.values():
            if not isinstance(participant, dict):
                continue
            try:
                frozen[int(participant["player_id"])] = participant
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
        profiles = {}
        seen = set()
        for raw in raw_profiles:
            if not isinstance(raw, dict):
                return None
            try:
                player_id = _exact_int(raw.get("id"), 1, PROJECTILE_MAX_ID)
                mass = float(raw.get("mass"))
                shape = raw.get("shape")
                ram_profile = raw.get("ram_profile")
            except (TypeError, ValueError, OverflowError):
                return None
            player = active.get(player_id)
            participant = player if player is not None else frozen.get(
                player_id)
            vehicle = _safe_vehicle(raw.get("vehicle"), "")
            expected_vehicle = (
                participant.vehicle if player is not None else
                participant.get("vehicle") if participant is not None else
                None)
            if (participant is None or player_id in seen or
                    vehicle != expected_vehicle or
                    not math.isfinite(mass) or not 100.0 <= mass <= 500000.0 or
                    not isinstance(shape, (list, tuple)) or len(shape) != 4):
                return None
            seen.add(player_id)
            try:
                shape = tuple(float(value) for value in shape)
            except (TypeError, ValueError, OverflowError):
                return None
            if (not all(math.isfinite(value) for value in shape) or
                    not 0.5 <= shape[0] <= 20.0 or
                    not 0.75 <= shape[1] <= 30.0 or
                    not -20.0 <= shape[2] < shape[3] <= 30.0):
                return None
            if not isinstance(ram_profile, dict):
                return None
            try:
                spall = float(ram_profile.get("spall_coefficient"))
                bonus = float(ram_profile.get("ramming_bonus"))
            except (TypeError, ValueError, OverflowError):
                return None
            if (not math.isfinite(spall) or not 1.0 <= spall <= 1.5 or
                    not math.isfinite(bonus) or not 0.0 <= bonus <= 0.15):
                return None
            # A worker starts from the frozen battle_start roster.  If one of
            # those players leaves during loading, its old native profile is a
            # legal extra, but only connected participants remain canonical.
            if player is not None:
                profiles[player_id] = {
                    "id": player_id,
                    "vehicle": vehicle,
                    "mass": round(mass, 3),
                    "shape": tuple(round(value, 4) for value in shape),
                    "ram_profile": {
                        "spall_coefficient": round(spall, 4),
                        "ramming_bonus": round(bonus, 6),
                    },
                }
        return profiles if set(profiles) == set(active) else None

    def update_bot_manifest(self, player_id, message):
        """Accept the canonical bot lineup from the elected simulation client."""
        received_raw_motion_time_us = self._motion_time_us()
        with self.lock:
            received_motion_time_us = self._logical_motion_time_us(
                received_raw_motion_time_us)
            if (not self._message_round_matches(message) or
                    self.phase not in ("loading", "battle") or
                    self.battle_result is not None or
                    player_id != self.bot_authority_id):
                return False
            self.last_bot_manifest_reject = ""
            self.last_bot_manifest_reject_code = ""
            human_profiles = None
            if self._human_ram_profiles_required():
                try:
                    human_manifest_fingerprint = json.dumps({
                        "bots": message.get("bots") or [],
                        "player_collision_profiles": message.get(
                            "player_collision_profiles"),
                    }, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True)
                except (TypeError, ValueError, OverflowError):
                    human_manifest_fingerprint = None
                if self.human_collision_manifest_fingerprint is not None:
                    if (human_manifest_fingerprint ==
                            self.human_collision_manifest_fingerprint):
                        return True
                    self.last_bot_manifest_reject_code = (
                        "human_collision_manifest_conflict")
                    self.last_bot_manifest_reject = (
                        "worker reused the round manifest with other data")
                    return False
                human_profiles = self._sanitize_human_collision_profiles(
                    message.get("player_collision_profiles"))
                if (human_manifest_fingerprint is None or
                        human_profiles is None):
                    self.last_bot_manifest_reject_code = (
                        "human_collision_profiles")
                    self.last_bot_manifest_reject = (
                        "worker human collision profiles do not match roster")
                    return False
            incoming = message.get("bots") or []
            if not isinstance(incoming, (list, tuple)):
                return False
            roster = {entry["id"]: entry for entry in self.bot_roster}
            if not roster:
                if incoming:
                    return False
                self.bot_manifest_authority_id = player_id
                self.bot_terminal_criticals = {}
                if human_profiles is not None:
                    self.human_collision_profiles = human_profiles
                    self.human_collision_profile_authority_id = player_id
                    self.human_collision_manifest_fingerprint = (
                        human_manifest_fingerprint)
                return True
            if len(incoming) != len(roster):
                return False
            manifest = []
            states = {}
            terminal_criticals = {}
            seen = set()
            required = ("id", "team", "slot", "vehicle", "health",
                        "max_health", "x", "y", "z", "yaw")
            for raw in incoming:
                if (not isinstance(raw, dict) or
                        not all(key in raw for key in required) or
                        not _has_finite_fields(
                            raw, ("id", "team", "slot", "health",
                                  "max_health", "x", "y", "z", "yaw"))):
                    return False
                try:
                    bot_id = int(raw.get("id"))
                    raw_team = int(raw.get("team", roster.get(bot_id, {}).get("team", 0)))
                    raw_slot = int(raw.get("slot", roster.get(bot_id, {}).get("slot", -1)))
                except (TypeError, ValueError):
                    return False
                identity = roster.get(bot_id)
                if identity is None or bot_id in seen:
                    return False
                if raw_team != identity["team"] or raw_slot != identity["slot"]:
                    return False
                seen.add(bot_id)
                max_health = max(1, min(int(_finite_float(raw.get("max_health"), 1000)), 100000))
                health = max(0, min(int(_finite_float(raw.get("health"), max_health)), max_health))
                try:
                    route = self._sanitize_bot_route(raw.get("route"))
                    _validated_bot_reload_progress(
                        raw, required=(
                            self.client_build == CLIENT_BUILD_0922))
                    if "terminal_critical" in raw:
                        terminal_criticals[bot_id] = (
                            _bot_terminal_critical(
                                raw.get("terminal_critical")))
                except (TypeError, ValueError):
                    return False
                entry = {
                    "id": bot_id,
                    "team": identity["team"],
                    "slot": identity["slot"],
                    "name": identity["name"],
                    "vehicle": _safe_vehicle(raw.get("vehicle"), "ussr:R11_MS-1"),
                    "max_health": max_health,
                    "health": health,
                    "profile": self._sanitize_bot_profile(raw.get("profile")),
                    "route": route,
                }
                manifest.append(entry)
                states[bot_id] = self._sanitize_bot_state(
                    raw, entry, self.bot_states.get(bot_id))
            if seen != set(roster):
                return False
            manifest.sort(key=lambda value: value["id"])
            self.bot_manifest = manifest
            self.bot_manifest_revision += 1
            self.bot_manifest_authority_id = player_id
            self.bot_terminal_criticals = terminal_criticals
            if human_profiles is not None:
                self.human_collision_profiles = human_profiles
                self.human_collision_profile_authority_id = player_id
                self.human_collision_manifest_fingerprint = (
                    human_manifest_fingerprint)
            if not self.bot_states:
                self.bot_states = states
                # The spawn poses are the first authoritative motion sample.
                # Timestamp their actual receipt so the first bot_state delta
                # is never measured from the server process/round origin.
                self.bot_state_time_us = received_motion_time_us
            self.pending_events.append({"kind": "bot_manifest", "bots": list(manifest)})
            return True

    @staticmethod
    def _sanitize_bot_profile(raw):
        raw = raw if isinstance(raw, dict) else {}
        profile = {}
        for key in ("class_tag", "dominant_role"):
            profile[key] = _safe_name(raw.get(key), "unknown")
        roles = raw.get("roles")
        profile["roles"] = {}
        if isinstance(roles, dict):
            for key, value in list(roles.items())[:8]:
                role = _safe_name(key, "unknown")
                profile["roles"][role] = round(
                    _clamp(_finite_float(value), 0.0, 1.0), 3)
        for key, default, maximum in (("desired_range", 180.0, 2000.0),
                                      ("fire_range", 500.0, 2500.0),
                                      ("speed", 0.0, 200.0),
                                      ("armor", 0.0, 10000.0)):
            profile[key] = round(_clamp(_finite_float(raw.get(key), default), 0.0, maximum), 3)
        profile["shells"] = []
        shells = raw.get("shells") or []
        if not isinstance(shells, (list, tuple)):
            shells = []
        for shell in shells[:5]:
            if not isinstance(shell, dict):
                continue
            profile["shells"].append({
                "index": max(0, min(int(_finite_float(shell.get("index"), 0)), 9)),
                "kind": _safe_name(shell.get("kind"), "unknown"),
                "penetration": round(_clamp(_finite_float(shell.get("penetration")), 0.0, 10000.0), 3),
                "damage": round(_clamp(_finite_float(shell.get("damage")), 0.0, 10000.0), 3),
                "speed": round(_clamp(_finite_float(shell.get("speed")), 0.0, 10000.0), 3),
            })
        return profile

    @staticmethod
    def _sanitize_bot_route(raw):
        raw = raw if isinstance(raw, dict) else {}
        route = {"id": _safe_name(raw.get("id"), "server_route"),
                 "waypoints": []}
        if "capacity" in raw:
            route["capacity"] = int(_clamp(
                _finite_float(raw.get("capacity"), 1.0), 1.0, 15.0))
        if "risk" in raw:
            route["risk"] = round(_clamp(
                _finite_float(raw.get("risk")), 0.0, 1.0), 3)
        for field in ("role_weights", "class_weights"):
            if field not in raw:
                continue
            weights = raw.get(field)
            sanitized = {}
            if isinstance(weights, dict):
                for key, value in list(weights.items())[:8]:
                    name = _safe_name(key, "unknown")
                    sanitized[name] = round(_clamp(
                        _finite_float(value), 0.0, 1.0), 3)
            route[field] = sanitized
        waypoints = raw.get("waypoints") or []
        if not isinstance(waypoints, (list, tuple)):
            waypoints = []
        if len(waypoints) > 16:
            raise ValueError("bot route exceeds the 16-waypoint protocol limit")
        for point in waypoints:
            if not isinstance(point, dict):
                continue
            route["waypoints"].append({
                "x": round(_clamp(_finite_float(point.get("x")), -2000.0, 2000.0), 3),
                "y": round(_clamp(_finite_float(point.get("y")), -1000.0, 1000.0), 3),
                "z": round(_clamp(_finite_float(point.get("z")), -2000.0, 2000.0), 3),
                "hold": bool(point.get("hold", False)),
            })
        return route

    @staticmethod
    def _sanitize_bot_ammo(raw, identity, previous):
        """Validate an optional atomic finite-ammunition snapshot."""
        has_inventory = "ammo_remaining" in raw
        has_next = "next_shell_index" in raw
        has_pending = "ammo_reload_pending" in raw
        if not has_inventory and not has_next and not has_pending:
            return {
                "remaining": list((previous or {}).get(
                    "ammo_remaining", [])),
                "next": int((previous or {}).get(
                    "next_shell_index", raw.get("shell_index", 0))),
                "pending": bool((previous or {}).get(
                    "ammo_reload_pending", False)),
            }
        if (not has_inventory or not has_next or not has_pending or
                "shell_index" not in raw):
            raise ValueError("bot ammunition snapshot is incomplete")
        shells = ((identity.get("profile") or {}).get("shells") or [])
        remaining = raw.get("ammo_remaining")
        if not isinstance(remaining, (list, tuple)):
            raise ValueError("bot ammunition inventory shape is invalid")
        # Production manifests carry descriptor shell summaries. Engine-free
        # harnesses and legacy adapters may omit them; the atomic inventory is
        # still self-sizing and bounded, so preserve it without inventing a
        # shell catalogue on the server.
        shell_count = len(shells) if shells else len(remaining)
        if (shell_count <= 0 or shell_count > 5 or
                len(remaining) != shell_count):
            raise ValueError("bot ammunition inventory shape is invalid")
        parsed = [_exact_int(value, 0, 1000) for value in remaining]
        if sum(parsed) > 1000:
            raise ValueError("bot ammunition inventory exceeds capacity")
        loaded = _exact_int(raw.get("shell_index"), 0, shell_count - 1)
        planned = _exact_int(
            raw.get("next_shell_index"), 0, shell_count - 1)
        pending = raw.get("ammo_reload_pending")
        if not isinstance(pending, bool):
            raise ValueError("bot ammunition reload state is invalid")
        total = sum(parsed)
        if total > 0 and parsed[planned] <= 0:
            raise ValueError("bot planned ammunition is exhausted")
        if total > 0 and not pending and parsed[loaded] <= 0:
            raise ValueError("bot loaded ammunition is exhausted")
        return {"remaining": parsed, "next": planned, "loaded": loaded,
                "pending": pending}

    @staticmethod
    def _sanitize_bot_clip(raw, previous):
        """Validate one optional exact Bot magazine checkpoint."""
        has_clip = "clip" in raw
        has_size = "clip_size" in raw
        if has_clip != has_size:
            raise ValueError("bot clip snapshot is incomplete")
        if not has_clip:
            return {
                "clip": int((previous or {}).get("clip", 1)),
                "clip_size": int((previous or {}).get("clip_size", 1)),
            }
        clip_size = _exact_int(
            raw.get("clip_size"), 1, MAX_PLAYER_CLIP_SIZE)
        clip = _exact_int(raw.get("clip"), 0, clip_size)
        previous_size = (previous or {}).get("clip_size")
        if previous_size is not None and int(previous_size) != clip_size:
            raise ValueError("bot clip size changed mid-round")
        return {"clip": clip, "clip_size": clip_size}

    @staticmethod
    def _sanitize_bot_siege(raw, identity, previous):
        """Validate one atomic Bot Siege publication."""
        fields = (
            "siege_state", "siege_time_left_ms",
            "siege_transition_total_ms")
        present = tuple(name in raw for name in fields)
        if any(present) and not all(present):
            raise ValueError("bot Siege snapshot is incomplete")
        if not any(present):
            return (
                int((previous or {}).get(
                    "siege_state", SIEGE_DISABLED)),
                int((previous or {}).get("siege_time_left_ms", 0)),
                int((previous or {}).get(
                    "siege_transition_total_ms", 0)),
            )
        state = _exact_int(raw.get("siege_state"), 0, 3)
        time_left_ms = _exact_int(
            raw.get("siege_time_left_ms"), 0, 4000)
        transition_total_ms = _exact_int(
            raw.get("siege_transition_total_ms"), 0, 4000)
        vehicle = str(identity.get("vehicle", ""))
        if not siege_mechanics.valid_wire_state(
                state, time_left_ms, vehicle, transition_total_ms):
            raise ValueError("bot Siege snapshot is invalid")
        # The simulation worker is room-owned and runs the same pinned client
        # code as the server package.  Keep the atomic wire-shape check above,
        # but do not independently re-derive transition duration or history
        # here.  Frame batching and wire rounding can legitimately skip or
        # slightly reshape those intermediate presentation states.
        return state, time_left_ms, transition_total_ms

    @staticmethod
    def _valid_hidden_observation(raw, known_targets):
        """Recognize a valid first hidden sample with no stored contact."""
        if (not isinstance(raw, dict) or raw.get("visible") is not False or
                not isinstance(raw.get("shootable_by_bot_ids"),
                               (list, tuple))):
            return False
        try:
            observing_team = _exact_int(raw.get("observing_team"), 1, 2)
            target_id = _exact_int(
                raw.get("target_id"), 1, PROJECTILE_MAX_ID)
            target_team = _exact_int(raw.get("target_team"), 1, 2)
        except ValueError:
            return False
        target_kind = raw.get("target_kind")
        if target_kind not in ("human", "bot"):
            return False
        target = known_targets.get((target_kind, target_id))
        return bool(
            target is not None and target.get("alive", True) and
            int(target.get("team", 0)) == target_team and
            target_team != observing_team)

    def update_bot_observation(self, player_id, message):
        """Accept authority observations; never derive contacts from snapshots."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    player_id != self.bot_authority_id or
                    player_id != self.bot_manifest_authority_id):
                return False
            if self.battle_result is not None:
                return True
            if not self._combat_accepting():
                return False
            if (not isinstance(message.get("contacts"), (list, tuple)) or
                    ("affordances" in message and
                     not isinstance(message.get("affordances"), (list, tuple)))):
                return False
            players = [
                self._public_player(p, include_outfits=False)
                for p in self.players.values()
                if p.connected and p.participating]
            known_targets = self.bot_planner.known_targets(list(self.bot_states.values()), players)
            known_players = dict(
                (int(player.player_id), player)
                for player in self.players.values()
                if player.connected and player.participating)
            known_bots = self.bot_planner.known_bots(
                self.bot_manifest, list(self.bot_states.values()))
            direct_bot_spots = dict(
                (bot_id, set()) for bot_id in known_bots)
            direct_player_spots = dict(
                (reporter_id, set()) for reporter_id in known_players)
            human_visible = set()
            stale_observation = False
            if self.client_build != CLIENT_BUILD_0922:
                for reporter_id, targets in self.player_spotted.items():
                    reporter = self.players.get(reporter_id)
                    if (reporter is None or not reporter.connected or
                            not reporter.participating or not reporter.alive):
                        continue
                    for target_kind, target_id in targets:
                        wire_kind = ("human" if target_kind == "player"
                                     else target_kind)
                        human_visible.add((
                            int(reporter.team), wire_kind, int(target_id)))
            contacts = []
            for raw in message.get("contacts"):
                contact = dict(raw) if isinstance(raw, dict) else raw
                if (not isinstance(contact, dict) or
                        not isinstance(contact.get("visible"), bool) or
                        not isinstance(contact.get("fresh"), bool) or
                        not isinstance(contact.get(
                            "visible_by_bot_ids"), (list, tuple)) or
                        not isinstance(contact.get(
                            "visible_by_player_ids"), (list, tuple)) or
                        not isinstance(contact.get(
                            "shootable_by_bot_ids"), (list, tuple)) or
                        len(contact.get("visible_by_bot_ids")) >
                        len(known_bots) or
                        len(contact.get("visible_by_player_ids")) >
                        len(known_players)):
                    return False
                try:
                    observing_team = _exact_int(
                        contact.get("observing_team"), 1, 2)
                    target_id = _exact_int(
                        contact.get("target_id"), 1, PROJECTILE_MAX_ID)
                    target_team = _exact_int(
                        contact.get("target_team"), 1, 2)
                    time_left = _bounded_float(
                        contact.get("time_left"), 0.0,
                        spotting.DESIGNATED_SPOT_MEMORY_SECONDS)
                except ValueError:
                    return False
                target_kind = contact.get("target_kind")
                target = known_targets.get((target_kind, target_id))
                if target is None:
                    known_retired = bool(
                        (target_kind == "human" and
                         target_id in self.players) or
                        (target_kind == "bot" and
                         (target_id in self.bot_states or any(
                             int(entry.get("id", 0)) == target_id
                             for entry in self.bot_manifest))))
                    if known_retired:
                        stale_observation = True
                        continue
                    return False
                if not bool(target.get("alive", True)):
                    stale_observation = True
                    continue
                if (target_kind not in ("human", "bot") or
                        int(target.get("team", 0)) == observing_team or
                        target_team != int(target.get("team", 0))):
                    return False
                bot_observer_ids = []
                stale_contact = False
                for raw_bot_id in contact.get("visible_by_bot_ids"):
                    try:
                        bot_id = _exact_int(
                            raw_bot_id, 1, PROJECTILE_MAX_ID)
                    except ValueError:
                        return False
                    bot = known_bots.get(bot_id)
                    if bot is not None and not bot.get("alive"):
                        stale_observation = True
                        stale_contact = True
                        continue
                    if (bot is None or
                            int(bot.get("team", 0)) != observing_team or
                            bot_id in bot_observer_ids):
                        return False
                    bot_observer_ids.append(bot_id)
                contact["visible_by_bot_ids"] = sorted(bot_observer_ids)

                observer_ids = []
                for raw_observer_id in contact.get(
                        "visible_by_player_ids"):
                    try:
                        observer_id = _exact_int(
                            raw_observer_id, 1, PROJECTILE_MAX_ID)
                    except ValueError:
                        return False
                    observer = known_players.get(observer_id)
                    if observer is not None and not observer.alive:
                        stale_observation = True
                        stale_contact = True
                        continue
                    if (observer is None or
                            int(observer.team) != observing_team or
                            observer_id in observer_ids):
                        return False
                    observer_ids.append(observer_id)
                if observer_ids and not contact["visible"]:
                    return False
                contact["visible_by_player_ids"] = sorted(observer_ids)
                shooter_ids = []
                for raw_bot_id in contact.get("shootable_by_bot_ids"):
                    try:
                        bot_id = _exact_int(
                            raw_bot_id, 1, PROJECTILE_MAX_ID)
                    except ValueError:
                        return False
                    bot = known_bots.get(bot_id)
                    if bot is not None and not bot.get("alive"):
                        stale_observation = True
                        stale_contact = True
                        continue
                    if (bot is None or
                            int(bot.get("team", 0)) != observing_team or
                            bot_id in shooter_ids):
                        return False
                    shooter_ids.append(bot_id)
                contact["shootable_by_bot_ids"] = sorted(shooter_ids)
                fresh = bool(bot_observer_ids or observer_ids)
                if stale_contact and not fresh:
                    continue
                if (contact["fresh"] != fresh or
                        contact["visible"] != (time_left > 0.0) or
                        (fresh and not contact["visible"]) or
                        (not fresh and shooter_ids)):
                    return False
                contact["time_left"] = time_left
                result_kind = ("player" if target_kind == "human"
                               else target_kind)
                for bot_id in bot_observer_ids:
                    direct_bot_spots[bot_id].add(
                        (result_kind, target_id))
                for observer_id in observer_ids:
                    direct_player_spots[observer_id].add(
                        (result_kind, target_id))
                if isinstance(contact, dict):
                    try:
                        key = (
                            int(contact.get("observing_team", 0)),
                            contact.get("target_kind"),
                            int(contact.get("target_id", 0)))
                    except (TypeError, ValueError):
                        key = None
                    if key in human_visible:
                        contact["visible"] = True
                contacts.append(contact)
            now = time.monotonic()
            accepted_visibility = []
            accepted_contacts = self.bot_planner.report_contacts(
                contacts, known_targets, now,
                accepted_visibility=accepted_visibility)
            valid_hidden = any(
                self._valid_hidden_observation(contact, known_targets)
                for contact in contacts)
            accepted_affordances = self.bot_planner.report_affordances(
                message.get("affordances"), known_bots, known_targets, now)
            self._replace_bot_spotted(direct_bot_spots)
            self._replace_player_spotted(direct_player_spots)
            if (self.client_build == CLIENT_BUILD_0922 and
                    accepted_visibility):
                return {
                    "type": "bot_observation",
                    "protocol": PROTOCOL_VERSION,
                    "round_id": self.round_id,
                    "contacts": accepted_visibility,
                }
            # A first ``visible=false`` sample is a valid complete observation
            # even though there is no prior contact to hide.  Keep that valid
            # no-op distinct from authorization or protocol rejection so the
            # worker dispatcher can advance its liveness fence without noisy
            # false rejection logs.
            return bool(
                accepted_contacts > 0 or accepted_affordances > 0 or
                valid_hidden or stale_observation)

    def _replace_bot_spotted(self, direct_spots):
        """Commit one complete validated Bot direct-spot batch."""
        for bot_id in tuple(self.bot_spotted):
            if bot_id not in direct_spots:
                self.bot_spotted.pop(bot_id, None)
        for bot_id in sorted(direct_spots):
            spotted = frozenset(direct_spots[bot_id])
            self.bot_spotted[int(bot_id)] = spotted
            reporter = ("bot", int(bot_id))
            for target in spotted:
                interaction = self._statistics_interaction(reporter, target)
                if interaction["spotted"]:
                    continue
                interaction["spotted"] = 1
                row = self._statistics_row(*reporter)
                row["spotted"] = int(row.get("spotted", 0)) + 1
        return True

    def _replace_player_spotted(self, direct_spots):
        """Commit a complete human direct-spot batch from the worker."""
        for reporter_id in tuple(self.player_spotted):
            if reporter_id not in direct_spots:
                self.player_spotted.pop(reporter_id, None)
        for player_id in sorted(direct_spots):
            spotted = frozenset(direct_spots[player_id])
            self.player_spotted[int(player_id)] = spotted
            reporter = ("player", int(player_id))
            for target in spotted:
                interaction = self._statistics_interaction(reporter, target)
                if interaction["spotted"]:
                    continue
                interaction["spotted"] = 1
                row = self._statistics_row(*reporter)
                row["spotted"] = int(row.get("spotted", 0)) + 1
        return True

    @staticmethod
    def _ordinary_bot_burst(fire_seq, shell_index):
        fire_seq = max(0, int(fire_seq))
        return {
            "burst_active": False,
            "burst_group_seq": fire_seq if fire_seq else 0,
            "burst_count": 1 if fire_seq else 0,
            "burst_next_index": 1 if fire_seq else 0,
            "burst_interval": 0.0,
            "burst_time_left": 0.0,
            "burst_shell_index": max(0, int(shell_index)),
        }

    @staticmethod
    def _sanitize_bot_burst(raw, fire_seq, shell_index):
        """Validate one complete physical-burst clock publication."""
        fields = burst_mechanics.BurstClock.WIRE_FIELDS
        present = tuple(name in raw for name in fields)
        if not any(present):
            return BattleState._ordinary_bot_burst(fire_seq, shell_index)
        if not all(present):
            raise ValueError("bot burst snapshot is incomplete")
        active = raw.get("burst_active")
        if not isinstance(active, bool):
            raise ValueError("bot burst active flag is invalid")
        try:
            group_seq = _exact_int(
                raw.get("burst_group_seq"), 0, PROJECTILE_MAX_ID)
            count = _exact_int(
                raw.get("burst_count"), 0,
                burst_mechanics.MAX_BURST_COUNT)
            next_index = _exact_int(
                raw.get("burst_next_index"), 0, count)
            interval = round(_bounded_float(
                raw.get("burst_interval"), 0.0,
                burst_mechanics.MAX_BURST_INTERVAL_SECONDS), 6)
            time_left = round(_bounded_float(
                raw.get("burst_time_left"), 0.0,
                burst_mechanics.MAX_BURST_INTERVAL_SECONDS), 6)
            burst_shell = _exact_int(
                raw.get("burst_shell_index"), 0, 9)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("bot burst snapshot is malformed")
        if ((count > 1 and interval <= 0.0) or
                (active and (count < 2 or next_index < 1 or
                             next_index >= count or
                             time_left > interval + 1.0e-9)) or
                (not active and time_left != 0.0) or
                (count == 0 and
                 (group_seq != 0 or next_index != 0)) or
                (count > 0 and
                 int(fire_seq) != group_seq + next_index - 1)):
            raise ValueError("bot burst snapshot is invalid")
        return {
            "burst_active": active,
            "burst_group_seq": group_seq,
            "burst_count": count,
            "burst_next_index": next_index,
            "burst_interval": interval,
            "burst_time_left": time_left,
            "burst_shell_index": burst_shell,
        }

    @staticmethod
    def _bot_burst_transition(previous, current):
        """Return every newly published physical projectile edge."""
        previous_fire = int((previous or {}).get("fire_seq", 0))
        current_fire = int(current.get("fire_seq", 0))
        fire_delta = current_fire - previous_fire
        if fire_delta < 0:
            raise ValueError("bot burst fire sequence moved backwards")
        previous_burst = dict(previous or {})
        if not all(name in previous_burst for name in
                   burst_mechanics.BurstClock.WIRE_FIELDS):
            previous_burst.update(BattleState._ordinary_bot_burst(
                previous_fire, previous_burst.get("shell_index", 0)))
        current_group = int(current["burst_group_seq"])
        current_count = int(current["burst_count"])
        current_next = int(current["burst_next_index"])
        current_shell = int(current["burst_shell_index"])
        previous_group = int(previous_burst["burst_group_seq"])
        previous_count = int(previous_burst["burst_count"])
        previous_next = int(previous_burst["burst_next_index"])
        previous_shell = int(previous_burst["burst_shell_index"])
        previous_active = bool(previous_burst["burst_active"])
        current_active = bool(current["burst_active"])

        if fire_delta == 0:
            if previous_active:
                same_active = (
                    current_active and current_group == previous_group and
                    current_count == previous_count and
                    current_next == previous_next and
                    current_shell == previous_shell and
                    float(current["burst_interval"]) ==
                    float(previous_burst["burst_interval"]) and
                    float(current["burst_time_left"]) <=
                    float(previous_burst["burst_time_left"]) + 1.0e-9)
                cancelled = (
                    not current_active and
                    current_group == previous_group and
                    current_count == previous_count and
                    current_next == previous_next and
                    current_shell == previous_shell and
                    float(current["burst_interval"]) ==
                    float(previous_burst["burst_interval"]))
                if not (same_active or cancelled):
                    raise ValueError("bot active burst state changed")
            elif current_active:
                raise ValueError("bot idle burst state changed")
            # Once both snapshots are idle, completed-group metadata has no
            # future firing authority.  A worker may normalize that history
            # while an ordinary reload edge changes the selected shell; the
            # ammunition validator below still proves the loaded/next shell,
            # clip and inventory transition independently.
            return ()

        if previous_active:
            if (current_group != previous_group or
                    current_count != previous_count or
                    current_shell != previous_shell or
                    float(current["burst_interval"]) !=
                    float(previous_burst["burst_interval"]) or
                    current_next != previous_next + fire_delta or
                    current_next > previous_count or
                    current_active != (current_next < current_count)):
                raise ValueError("bot burst continuation is invalid")
            first_index = previous_next
        else:
            if (current_group != previous_fire + 1 or
                    current_next != fire_delta or
                    current_count < fire_delta or current_count < 1 or
                    current_active != (current_next < current_count)):
                raise ValueError("bot burst start is invalid")
            if current_count == 1 and fire_delta != 1:
                raise ValueError("ordinary bot shot skipped a sequence")
            first_index = 0
        return tuple({
            "burst_group_seq": current_group,
            "burst_index": index,
            "burst_count": current_count,
            "shell_index": current_shell,
        } for index in range(first_index, current_next))

    @staticmethod
    def _bot_medkit_activated(previous, current):
        """Recognize one canonical large-medkit use from its wire ledgers."""
        if previous is None:
            return False
        old_snapshots = previous.get("equipment_states")
        new_snapshots = current.get("equipment_states")
        if (not isinstance(old_snapshots, list) or
                not isinstance(new_snapshots, list)):
            return False
        try:
            contracts = equipment_mechanics.bot_consumable_contracts(
                None, snapshot=old_snapshots)
            old_states = equipment_mechanics.restore_equipment_states(
                old_snapshots, contracts=contracts, now=0.0)
            new_states = equipment_mechanics.restore_equipment_states(
                new_snapshots, contracts=contracts, now=0.0)
        except (TypeError, ValueError):
            return False
        for old, new in zip(old_states, new_states):
            if str(old.contract.get("kind") or "") != "medkit":
                continue
            quantity_used = bool(
                old.uses_left > 0 and new.uses_left == old.uses_left - 1)
            unlimited_used = bool(
                old.uses_left == -1 and new.uses_left == -1 and
                new.ready_at > old.ready_at + 1.0e-6)
            if quantity_used or unlimited_used:
                return True
        return False

    @staticmethod
    def _sanitize_bot_state(raw, identity, previous):
        max_health = int(identity.get("max_health", 1000))
        reported_health = max(0, min(int(_finite_float(raw.get("health"), max_health)), max_health))
        if previous is not None:
            reported_health = min(reported_health, int(previous.get("health", max_health)))
        try:
            fire_seq = max(0, int(raw.get("fire_seq", 0)))
        except (TypeError, ValueError):
            fire_seq = 0
        if previous is not None:
            fire_seq = max(fire_seq, int(previous.get("fire_seq", 0)))
        yaw = _finite_float(raw.get("yaw"), 0.0)
        movement = _finite_float(raw.get("movement_dir"), 0.0)
        rotation = _finite_float(raw.get("rotation_dir"), 0.0)
        ammunition = BattleState._sanitize_bot_ammo(
            raw, identity, previous)
        burst = BattleState._sanitize_bot_burst(
            raw, fire_seq, ammunition.get("loaded", 0))
        gun_clip = BattleState._sanitize_bot_clip(raw, previous)
        reload_progress = _validated_bot_reload_progress(raw)
        (siege_state, siege_time_left_ms,
         siege_transition_total_ms) = BattleState._sanitize_bot_siege(
             raw, identity, previous)
        result = {
            "id": int(identity["id"]),
            "team": int(identity["team"]),
            "slot": int(identity["slot"]),
            "name": identity["name"],
            "vehicle": identity.get("vehicle", "ussr:R11_MS-1"),
            "world_pose": bool(raw.get(
                "world_pose", (previous or {}).get("world_pose", True))),
            "x": round(_clamp(_finite_float(raw.get("x")), -2000.0, 2000.0), 4),
            "y": round(_clamp(_finite_float(raw.get("y")), -1000.0, 1000.0), 4),
            "z": round(_clamp(_finite_float(raw.get("z")), -2000.0, 2000.0), 4),
            "yaw": round(yaw, 5),
            "pitch": round(_clamp(_finite_float(raw.get("pitch")), -0.61, 0.61), 5),
            "roll": round(_clamp(_finite_float(raw.get("roll")), -0.61, 0.61), 5),
            "aim_yaw": round(_finite_float(raw.get("aim_yaw"), yaw), 5),
            "gun_pitch": round(_clamp(_finite_float(raw.get("gun_pitch")), -1.2, 1.2), 5),
            "speed": round(_clamp(
                _finite_float(raw.get("speed")), -80.0, 80.0), 4),
            "movement_dir": (1 if movement > 0.01 else
                             (-1 if movement < -0.01 else 0)),
            "rotation_dir": (1 if rotation > 0.01 else
                             (-1 if rotation < -0.01 else 0)),
            "siege_state": siege_state,
            "siege_time_left_ms": siege_time_left_ms,
            "siege_transition_total_ms": siege_transition_total_ms,
            "fire_seq": fire_seq,
            "shell_index": ammunition.get(
                "loaded", max(0, min(int(_finite_float(
                    raw.get("shell_index"), 0)), 9))),
            "next_shell_index": ammunition["next"],
            "ammo_remaining": ammunition["remaining"],
            "ammo_reload_pending": ammunition["pending"],
            "clip": gun_clip["clip"],
            "clip_size": gun_clip["clip_size"],
            "health": reported_health,
            "max_health": max_health,
            "alive": bool(raw.get("alive", reported_health > 0)) and reported_health > 0,
            "frags": int((previous or {}).get("frags", 0)),
            "team_killer": bool((previous or {}).get(
                "team_killer", False)),
            "death_attacker_kind": str((previous or {}).get(
                "death_attacker_kind", "")),
            "death_attacker_id": int((previous or {}).get(
                "death_attacker_id", 0)),
            "combat_revision": int((previous or {}).get(
                "combat_revision", 0)),
            "combat_base_revision": int((previous or {}).get(
                "combat_base_revision", 0)),
            "combat_ack_seq": int((previous or {}).get(
                "combat_ack_seq", 0)),
            "combat_fire_elapsed": round(_finite_float(
                raw.get("combat_fire_elapsed"), (previous or {}).get(
                    "combat_fire_elapsed", 0.0)), 6),
            "combat_fire_timer": round(_finite_float(
                raw.get("combat_fire_timer"), (previous or {}).get(
                    "combat_fire_timer", 0.0)), 6),
            "fire_attacker_kind": str((previous or {}).get(
                "fire_attacker_kind", "")),
            "fire_attacker_id": int((previous or {}).get(
                "fire_attacker_id", 0)),
            # The hidden worker may resolve a canonical stun edge through the
            # projectile ledger, but its periodic Bot publication never owns
            # this durable state. Preserve only the server-admitted value.
            "stun_end_server_time_ms": int((previous or {}).get(
                "stun_end_server_time_ms", 0)),
            "stun_attacker_kind": str((previous or {}).get(
                "stun_attacker_kind", "")),
            "stun_attacker_id": int((previous or {}).get(
                "stun_attacker_id", 0)),
        }
        result.update(burst)
        if reload_progress is not None:
            result["reload_time"], result["reload_duration"] = \
                reload_progress
        critical = (previous or {}).get("critical")
        if "critical" in raw:
            critical = _critical_state(_critical_payload(raw.get("critical")))
        # Canonical snapshots always carry the complete modern combat
        # baseline.  Legacy 0.8.2 senders need not provide it, but an empty
        # state must not disappear before the #1513 loading snapshot.
        result["critical"] = critical or {}
        has_shot_yaw = "shot_yaw" in raw
        has_shot_pitch = "shot_pitch" in raw
        if has_shot_yaw != has_shot_pitch:
            raise ValueError("bot shot angles must be an atomic pair")
        raw_shot_yaw = (raw.get("shot_yaw") if has_shot_yaw else
                        (previous or {}).get("shot_yaw"))
        raw_shot_pitch = (raw.get("shot_pitch") if has_shot_pitch else
                          (previous or {}).get("shot_pitch"))
        if raw_shot_yaw is not None and raw_shot_pitch is not None:
            shot_yaw = _finite_float(raw_shot_yaw, float("nan"))
            shot_pitch = _finite_float(raw_shot_pitch, float("nan"))
            if not math.isfinite(shot_yaw) or not math.isfinite(shot_pitch):
                raise ValueError("bot shot angles must be finite")
            result["shot_yaw"] = round(
                ((shot_yaw + math.pi) % (2.0 * math.pi)) - math.pi, 5)
            result["shot_pitch"] = round(
                _clamp(shot_pitch, -1.2, 1.2), 5)
        result["death_reason"] = max(0, min(int(_finite_float(
            raw.get("death_reason"), (previous or {}).get(
                "death_reason", 0))), 255))
        result["display_health"] = max(0, min(int(_finite_float(
            raw.get("display_health"), reported_health)), max_health))
        snapshots = raw.get("equipment_states")
        previous_snapshots = (previous or {}).get("equipment_states")
        if snapshots is None:
            snapshots = previous_snapshots if previous_snapshots is not None \
                else []
        if not isinstance(snapshots, (list, tuple)):
            raise ValueError("bot equipment snapshot is invalid")
        contracts = None
        if previous_snapshots is not None:
            contracts = equipment_mechanics.bot_consumable_contracts(
                None, snapshot=previous_snapshots)
        else:
            contracts = equipment_mechanics.bot_consumable_contracts(
                None, snapshot=snapshots)
        equipments = equipment_mechanics.restore_equipment_states(
            snapshots, contracts=contracts, now=0.0)
        canonical_snapshots = [equipment.snapshot(0.0)
                               for equipment in equipments]
        if previous_snapshots is not None:
            previous_equipments = \
                equipment_mechanics.restore_equipment_states(
                    previous_snapshots, contracts=contracts, now=0.0)
            for old, new in zip(previous_equipments, equipments):
                if (old.uses_left >= 0 and
                        new.uses_left > old.uses_left):
                    raise ValueError("bot equipment inventory increased")
        result["equipment_states"] = canonical_snapshots
        raw_stun_end = raw.get(
            "stun_end_server_time_ms",
            (previous or {}).get("stun_end_server_time_ms", 0))
        try:
            proposed_stun_end = int(raw_stun_end)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("bot stun proposal is invalid")
        previous_stun_end = int((previous or {}).get(
            "stun_end_server_time_ms", 0))
        stale_combat_base = bool(
            previous is not None and
            int(_finite_float(raw.get("combat_base_revision"), -1)) <
            int(previous.get("combat_base_revision", 0)))
        if (isinstance(raw_stun_end, bool) or proposed_stun_end < 0 or
                proposed_stun_end > PROJECTILE_MAX_ID or
                float(raw_stun_end) != proposed_stun_end or
                (not stale_combat_base and
                 proposed_stun_end not in (0, previous_stun_end))):
            raise ValueError("bot stun proposal is invalid")
        if (previous_stun_end > 0 and proposed_stun_end == 0 and
                not stale_combat_base and
                not BattleState._bot_medkit_activated(previous, result)):
            raise ValueError("bot stun clear has no medkit activation")
        result["stun_end_server_time_ms"] = proposed_stun_end
        if proposed_stun_end == 0:
            result["stun_attacker_kind"] = ""
            result["stun_attacker_id"] = 0
        return result

    @staticmethod
    def _validate_bot_ammo_transition(previous, current):
        """Require conserved inventory and exact magazine boundaries."""
        if previous is None:
            return True
        before = previous.get("ammo_remaining") or []
        after = current.get("ammo_remaining") or []
        if not before and not after:
            return True
        if len(before) != len(after) or not before:
            raise ValueError("bot ammunition contract appeared mid-round")
        fire_delta = (int(current.get("fire_seq", 0)) -
                      int(previous.get("fire_seq", 0)))
        if fire_delta < 0 or fire_delta > burst_mechanics.MAX_BURST_COUNT:
            raise ValueError("bot ammunition fire delta is invalid")
        loaded = int(current.get("shell_index", 0))
        previous_loaded = int(previous.get("shell_index", 0))
        previous_next = int(previous.get(
            "next_shell_index", previous_loaded))
        next_shell = int(current.get("next_shell_index", loaded))
        previous_pending = bool(previous.get(
            "ammo_reload_pending", False))
        pending = bool(current.get("ammo_reload_pending", False))
        previous_reload_time = float(previous.get("reload_time", 0.0))
        reload_time = float(current.get("reload_time", 0.0))
        clip_size = int(current.get("clip_size", 1))
        previous_clip_size = int(previous.get("clip_size", 1))
        clip = int(current.get("clip", clip_size))
        previous_clip = int(previous.get(
            "clip", previous_clip_size))
        if clip_size != previous_clip_size:
            raise ValueError("bot clip size changed mid-round")
        if not 0 <= previous_clip <= clip_size or not 0 <= clip <= clip_size:
            raise ValueError("bot clip checkpoint is invalid")
        expected = list(before)
        if fire_delta:
            if not pending:
                raise ValueError("bot shot did not enter reload state")
            previous_active = bool((previous or {}).get(
                "burst_active", False))
            if previous_active:
                expected_loaded = previous_loaded
                expected_clip = previous_clip - fire_delta
            elif previous_pending and (
                    previous_clip == 0 or clip_size == 1):
                expected_loaded = previous_next
                # A full reload is capped by the planned shell's remaining
                # inventory.  If that reload completes and fires in this same
                # worker publication, consume one round from the partial
                # refill rather than inventing a full magazine first.
                expected_clip = min(
                    clip_size, before[previous_next]) - fire_delta
            else:
                expected_loaded = previous_loaded
                expected_clip = previous_clip - fire_delta
            if previous_clip <= 0 and not (
                    previous_pending and previous_clip == 0):
                raise ValueError("bot fired from an empty clip")
            burst_shell = int(current.get("burst_shell_index", loaded))
            if loaded != burst_shell or loaded != expected_loaded:
                raise ValueError("bot loaded shell changed while firing")
            if (expected_clip < 0 or loaded >= len(expected) or
                    expected[loaded] < fire_delta):
                raise ValueError("bot fired an exhausted shell")
            expected[loaded] -= fire_delta
            exhausted_clip_switch = (
                clip_size > 1 and expected_clip > 0 and
                expected[loaded] == 0 and sum(expected) > 0)
            if (clip_size > 1 and clip != expected_clip and
                    not (exhausted_clip_switch and clip == 0)):
                raise ValueError(
                    "bot clip did not consume one round (%d -> %d, "
                    "expected %d)" % (
                        previous_clip, clip, expected_clip))
            if next_shell != previous_next:
                # The worker must keep ``next`` usable in every atomic ammo
                # snapshot.  Consuming the last round of the planned shell
                # therefore selects its fallback in the same fire update,
                # before the reload edge.  That is the only legal planned
                # selection change outside a completed reload.
                planned_was_consumed = (
                    previous_next == loaded and
                    expected[previous_next] == 0)
                fallback_is_usable = (
                    (sum(expected) == 0 and next_shell == 0) or
                    (0 <= next_shell < len(expected) and
                     expected[next_shell] > 0))
                initial_reload_completed = (
                    not previous_active and previous_reload_time > 0.0)
                if (not fallback_is_usable or not (
                        planned_was_consumed or
                        initial_reload_completed)):
                    raise ValueError(
                        "bot planned shell changed outside reload")
        elif not previous_pending:
            if pending:
                raise ValueError("bot reload started without a shot")
            if loaded != previous_loaded:
                raise ValueError("bot loaded shell changed outside reload")
            if clip_size > 1 and clip != previous_clip:
                raise ValueError("bot clip changed outside reload")
            if next_shell != previous_next:
                planned_is_usable = (
                    (sum(expected) == 0 and next_shell == 0) or
                    (0 <= next_shell < len(expected) and
                     expected[next_shell] > 0))
                initial_reload_completed = (
                    previous_reload_time > 0.0 and reload_time == 0.0)
                if not initial_reload_completed or not planned_is_usable:
                    raise ValueError(
                        "bot planned shell changed outside reload")
        elif pending:
            if loaded != previous_loaded:
                raise ValueError("bot loaded shell changed before reload")
            if next_shell != previous_next:
                raise ValueError("bot planned shell changed before reload")
            if clip_size > 1 and clip != previous_clip:
                raise ValueError("bot clip changed before reload")
        elif previous_clip == 0 or clip_size == 1:
            if loaded != previous_next:
                raise ValueError(
                    "bot loaded shell skipped its planned boundary")
            refill = min(clip_size, after[loaded])
            if clip_size > 1 and clip != refill:
                raise ValueError("bot full reload did not refill its clip")
        else:
            if loaded != previous_loaded:
                raise ValueError(
                    "bot intra-clip reload changed its loaded shell")
            if clip_size > 1 and clip != previous_clip:
                raise ValueError(
                    "bot intra-clip reload changed its clip")
        if list(after) != expected:
            raise ValueError("bot ammunition inventory is not conserved")
        return True

    @staticmethod
    def _bot_combat_signature(state):
        critical = state.get("critical")
        if critical:
            critical = _critical_state(_critical_payload(critical))
            if _critical_discrete_state(critical) == (
                    (), (), (), False, False):
                critical = None
        else:
            critical = None
        return (int(state.get("health", 0)), bool(state.get("alive")),
                critical,
                round(float(state.get("combat_fire_elapsed", 0.0)), 6),
                round(float(state.get("combat_fire_timer", 0.0)), 6),
                int(state.get("stun_end_server_time_ms", 0)))

    @staticmethod
    def _copy_bot_combat(target, source):
        for key in ("health", "alive", "critical", "death_reason",
                    "display_health", "death_attacker_kind",
                    "death_attacker_id", "combat_revision",
                    "combat_base_revision", "combat_ack_seq",
                    "combat_fire_elapsed", "combat_fire_timer",
                    "fire_attacker_kind", "fire_attacker_id",
                    "stun_end_server_time_ms", "stun_attacker_kind",
                    "stun_attacker_id"):
            if key in source:
                value = source[key]
                target[key] = dict(value) if isinstance(value, dict) else value
            else:
                target.pop(key, None)

    @staticmethod
    def _commit_external_bot_combat(bot, before):
        """Open a new lineage for one server-admitted bot combat change."""
        before_fire = bool(before[2] and before[2].get("fire", False))
        after_critical = bot.get("critical") or {}
        after_fire = bool(after_critical.get("fire", False))
        if not (before_fire and after_fire):
            bot["combat_fire_elapsed"] = 0.0
            bot["combat_fire_timer"] = 0.0
        if not after_fire:
            bot["fire_attacker_kind"] = ""
            bot["fire_attacker_id"] = 0
        after = BattleState._bot_combat_signature(bot)
        if after == before:
            return False
        revision = int(bot.get("combat_revision", 0)) + 1
        bot["combat_revision"] = revision
        bot["combat_base_revision"] = revision
        # Publication sequence numbers are global within one round.  Keeping
        # the accepted prefix identifies whether an in-flight authority state
        # was incorporated before this external hit.
        bot["combat_ack_seq"] = int(bot.get("combat_ack_seq", 0))
        return True

    def _apply_bot_terminal_critical(self, bot):
        """Apply the worker-projected wreck without opening a revision."""
        terminal = self.bot_terminal_criticals.get(int(bot.get("id", 0)))
        if terminal is None:
            return None
        durable = json.loads(json.dumps(terminal))
        current = bot.get("critical")
        if (isinstance(current, dict) and
                current.get("ammo_rack_death", False)):
            # The manifest's generic wreck is built before this shot. Keep
            # the admitted ammo-rack terminal cause when completing the rest
            # of the destroyed module/crew projection.
            durable["ammo_rack_death"] = True
        bot["critical"] = durable
        bot["combat_fire_elapsed"] = 0.0
        bot["combat_fire_timer"] = 0.0
        return durable

    def _reconcile_modern_bot_combat(self, raw, previous, current):
        """Apply the strict #1513 bot publication/base/ack contract."""
        required = ("critical", "combat_base_revision", "combat_seq",
                    "combat_fire_elapsed", "combat_fire_timer",
                    "stun_end_server_time_ms")
        if not all(key in raw for key in required):
            raise ValueError("modern bot combat publication is incomplete")
        if not isinstance(raw["critical"], dict):
            raise ValueError("modern bot critical state is invalid")
        try:
            raw_base = int(raw["combat_base_revision"])
            raw_seq = int(raw["combat_seq"])
            fire_elapsed = float(raw["combat_fire_elapsed"])
            fire_timer = float(raw["combat_fire_timer"])
        except (TypeError, ValueError, OverflowError):
            raise ValueError("modern bot combat revision is invalid")
        if (isinstance(raw["combat_base_revision"], bool) or
                isinstance(raw["combat_seq"], bool) or
                isinstance(raw["combat_fire_elapsed"], bool) or
                isinstance(raw["combat_fire_timer"], bool) or
                not math.isfinite(fire_elapsed) or
                not math.isfinite(fire_timer) or raw_base < 0 or
                raw_seq < 0 or
                float(raw["combat_base_revision"]) != raw_base or
                float(raw["combat_seq"]) != raw_seq or
                fire_elapsed < 0.0 or
                fire_elapsed > BOT_FIRE_DURATION_SECONDS or
                fire_timer < 0.0 or
                fire_timer >= BOT_FIRE_TICK_SECONDS):
            raise ValueError("modern bot combat revision is invalid")
        current_fire = bool((current.get("critical") or {}).get(
            "fire", False))
        if (not current_fire and
                (fire_elapsed != 0.0 or fire_timer != 0.0)):
            raise ValueError("inactive bot fire has a non-zero clock")
        current["combat_fire_elapsed"] = round(fire_elapsed, 6)
        current["combat_fire_timer"] = round(fire_timer, 6)

        server_base = int(previous.get("combat_base_revision", 0))
        server_ack = int(previous.get("combat_ack_seq", 0))
        if raw_base > server_base:
            raise ValueError("bot combat publication is ahead of its base")
        if raw_base < server_base:
            # An external hit won the server ordering race.  Pose and shot
            # edges remain current, while the stale combat proposal is fenced.
            self._copy_bot_combat(current, previous)
            return
        if raw_seq < server_ack or raw_seq > server_ack + 1:
            raise ValueError("bot combat publication sequence is not contiguous")
        if raw_seq == server_ack:
            if self._bot_combat_signature(current) != \
                    self._bot_combat_signature(previous):
                raise ValueError("repeated bot combat publication changed state")
            self._copy_bot_combat(current, previous)
            return

        current["combat_revision"] = int(
            previous.get("combat_revision", 0)) + 1
        current["combat_base_revision"] = server_base
        current["combat_ack_seq"] = raw_seq

    def update_bot_states(self, player_id, message):
        received_raw_motion_time_us = self._motion_time_us()
        with self.lock:
            received_motion_time_us = self._logical_motion_time_us(
                received_raw_motion_time_us)
            self._clear_protocol_reject("bot_state")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    "bot_state", "round",
                    "round=%s server_round=%s" % (
                        message.get("round_id") if isinstance(message, dict)
                        else None, self.round_id))
            if self.battle_result is not None:
                # A checkpoint encoded before the terminal result can still be
                # waiting in the worker's reliable queue.  The result is
                # canonical, so converge that tail packet as a quiet no-op.
                return True
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    "bot_state", "combat_closed",
                    "phase=%s tick=%s" % (self.phase, self.tick))
            if player_id != self.bot_authority_id:
                return self._set_protocol_reject(
                    "bot_state", "authority",
                    "sender=%s authority=%s" % (
                        player_id, self.bot_authority_id))
            if player_id != self.bot_manifest_authority_id:
                return self._set_protocol_reject(
                    "bot_state", "manifest_authority",
                    "sender=%s manifest_authority=%s" % (
                        player_id, self.bot_manifest_authority_id))
            if not self.bot_manifest and self.bot_roster:
                return self._set_protocol_reject(
                    "bot_state", "manifest_missing", "manifest=empty")
            previous_source_time_us = self.bot_source_time_us
            source_time_us = None
            source_batch_horizon_us = None
            source_clock_rebase = False
            same_source_batch = False
            if "sample_time_us" in message:
                try:
                    source_time_us = _exact_int(
                        message.get("sample_time_us"), 0,
                        MAX_MOTION_TIME_US)
                    source_batch_horizon_us = _exact_int(
                        message.get("source_batch_horizon_us"), 0,
                        MAX_MOTION_TIME_US)
                except ValueError:
                    return self._set_protocol_reject(
                        "bot_state", "sample_time",
                        "sample_time_us=%s horizon_us=%s" % (
                            message.get("sample_time_us"),
                            message.get("source_batch_horizon_us")))
                if source_time_us > source_batch_horizon_us:
                    return self._set_protocol_reject(
                        "bot_state", "sample_horizon",
                        "sample_time_us=%s horizon_us=%s" % (
                            source_time_us, source_batch_horizon_us))
                if (self.bot_source_batch_horizon_us is not None and
                        source_batch_horizon_us <
                        self.bot_source_batch_horizon_us):
                    return self._set_protocol_reject(
                        "bot_state", "sample_horizon_order",
                        "horizon_us=%s previous=%s" % (
                            source_batch_horizon_us,
                            self.bot_source_batch_horizon_us))
                same_source_batch = bool(
                    self.bot_source_batch_horizon_us is not None and
                    source_batch_horizon_us ==
                    self.bot_source_batch_horizon_us)
                if (self.bot_source_time_us is not None and
                        source_time_us <= self.bot_source_time_us):
                    return self._set_protocol_reject(
                        "bot_state", "sample_time_order",
                        "sample_time_us=%s previous=%s" % (
                            source_time_us, self.bot_source_time_us))
                if self.bot_source_time_us is None:
                    next_bot_state_time_us = max(
                        received_motion_time_us,
                        self.bot_state_time_us + 1)
                else:
                    if self.bot_source_receipt_time_us is None:
                        return self._set_protocol_reject(
                            "bot_state", "sample_time_clock",
                            "accepted source receipt origin is missing")
                    source_delta_us = (
                        source_time_us - self.bot_source_time_us)
                    receipt_elapsed_us = max(
                        0, received_raw_motion_time_us -
                        self.bot_source_receipt_time_us)
                    if (not same_source_batch and
                            source_delta_us > (
                                receipt_elapsed_us +
                                MAX_BOT_SAMPLE_LEAD_US)):
                        # A complete, valid publication may re-anchor the
                        # trusted source clock after a render stall or a
                        # coalesced outbound backlog.  Defer that rebase until
                        # every row and side ledger has been validated so a
                        # malformed future packet cannot poison the accepted
                        # lineage.
                        source_clock_rebase = True
                        next_bot_state_time_us = max(
                            received_motion_time_us,
                            self.bot_state_time_us + 1)
                    else:
                        # One slow worker callback can publish ordered physical
                        # checkpoints which share its final source horizon.
                        # Their near-identical receipt times do not make the
                        # later checkpoint a source-clock jump; preserving the
                        # source delta also preserves every admitted shot edge.
                        next_bot_state_time_us = (
                            self.bot_state_time_us + source_delta_us)
            elif "source_batch_horizon_us" in message:
                return self._set_protocol_reject(
                    "bot_state", "sample_horizon_without_time",
                    "horizon_us=%s" % message.get(
                        "source_batch_horizon_us"))
            elif self.bot_source_time_us is not None:
                return self._set_protocol_reject(
                    "bot_state", "sample_time_missing",
                    "previous=%s" % self.bot_source_time_us)
            else:
                next_bot_state_time_us = max(
                    received_motion_time_us, self.bot_state_time_us + 1)
            if next_bot_state_time_us > MAX_MOTION_TIME_US:
                return self._set_protocol_reject(
                    "bot_state", "sample_time_range",
                    "mapped_sample_time_us=%s" %
                    next_bot_state_time_us)
            next_motion_time_offset_us = max(
                self.motion_time_offset_us,
                next_bot_state_time_us - received_raw_motion_time_us)
            next_launch_clock_offset_us = self.bot_launch_clock_offset_us
            if source_clock_rebase:
                next_launch_clock_offset_us = (
                    self._server_time_ms() * 1000 -
                    source_batch_horizon_us)
            elif (source_time_us is not None and
                    next_launch_clock_offset_us is None):
                next_launch_clock_offset_us = (
                    self._server_time_ms() * 1000 -
                    source_batch_horizon_us)
            identities = {entry["id"]: entry for entry in self.bot_manifest}
            incoming = message.get("bots") or []
            if (not isinstance(incoming, (list, tuple)) or
                    len(incoming) != len(identities)):
                return self._set_protocol_reject(
                    "bot_state", "batch_shape",
                    "incoming_type=%s incoming_count=%s expected_count=%s" % (
                        type(incoming).__name__,
                        len(incoming) if isinstance(
                            incoming, (list, tuple)) else None,
                        len(identities)))
            next_states = {}
            human_ram_armors = self._validated_human_ram_armors(
                message.get("human_ram_armors"))
            if human_ram_armors is None:
                return self._set_protocol_reject(
                    "bot_state", "human_ram_armors",
                    "worker human ram armor results are invalid")
            shot_events = []
            pending_projectile_launches = {}
            fire_deaths = []
            capture_resets = set()
            stun_clears = []
            seen = set()
            required = ("id", "x", "y", "z", "yaw", "health",
                        "alive", "fire_seq")
            if self.client_build == CLIENT_BUILD_0922:
                required += ("reload_time", "reload_duration")
            for raw in incoming:
                if (not isinstance(raw, dict) or
                        not all(key in raw for key in required) or
                        not _has_finite_fields(
                            raw, ("id", "x", "y", "z", "yaw",
                                  "health", "fire_seq")) or
                        not isinstance(raw.get("alive"), bool)):
                    return self._set_protocol_reject(
                        "bot_state", "bot_shape",
                        "bot=%s required_or_finite_fields_invalid" % (
                            raw.get("id") if isinstance(raw, dict)
                            else None))
                try:
                    bot_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    return self._set_protocol_reject(
                        "bot_state", "bot_id", "bot=%s" % raw.get("id"))
                identity = identities.get(bot_id)
                if identity is None or bot_id in seen:
                    return self._set_protocol_reject(
                        "bot_state", "bot_identity",
                        "bot=%s known=%s duplicate=%s" % (
                            bot_id, identity is not None, bot_id in seen))
                seen.add(bot_id)
                try:
                    fire_seq = int(raw.get("fire_seq"))
                except (TypeError, ValueError):
                    return self._set_protocol_reject(
                        "bot_state", "fire_seq",
                        "bot=%s client_fire=%s" % (
                            bot_id, raw.get("fire_seq")))
                if (fire_seq < 0 or float(raw.get("fire_seq")) != fire_seq or
                        bool(raw.get("alive")) !=
                        (int(float(raw.get("health"))) > 0)):
                    return self._set_protocol_reject(
                        "bot_state", "fire_or_alive",
                        "bot=%s client_fire=%s health=%s alive=%s" % (
                            bot_id, raw.get("fire_seq"), raw.get("health"),
                            raw.get("alive")))
                previous = self.bot_states.get(bot_id)
                try:
                    current = self._sanitize_bot_state(
                        raw, identity, previous)
                    _validated_bot_reload_progress(
                        raw, required=(
                            self.client_build == CLIENT_BUILD_0922))
                    if (previous is not None and
                            int(current.get("fire_seq", 0)) >
                            int(previous.get("fire_seq", 0)) and
                            current.get("siege_state") in (
                                SIEGE_SWITCHING_ON,
                                SIEGE_SWITCHING_OFF)):
                        raise ValueError(
                            "bot fired during a Siege transition")
                    if self.client_build == CLIENT_BUILD_0922:
                        self._reconcile_modern_bot_combat(
                            raw, previous, current)
                except ValueError as error:
                    return self._set_protocol_reject(
                        "bot_state", "combat_contract",
                        ("bot=%s client_fire=%s server_fire=%s "
                         "client_base=%s server_base=%s client_seq=%s "
                         "server_ack=%s reason=%s") % (
                            bot_id, raw.get("fire_seq"),
                            (previous or {}).get("fire_seq", 0),
                            raw.get("combat_base_revision"),
                            (previous or {}).get(
                                "combat_base_revision", 0),
                            raw.get("combat_seq"),
                            (previous or {}).get("combat_ack_seq", 0),
                            error))
                rebase_fire_gap = bool(
                    source_clock_rebase and previous is not None and
                    int(current.get("fire_seq", 0)) >
                    int(previous.get("fire_seq", 0)))
                if rebase_fire_gap:
                    # Publications coalesced behind a render stall can span
                    # more than one unobserved shot.  Their complete current
                    # inventory and burst clock form the new trusted baseline;
                    # never invent projectile edges for the missing interval.
                    burst_edges = ()
                else:
                    try:
                        burst_edges = self._bot_burst_transition(
                            previous, current)
                        self._validate_bot_ammo_transition(previous, current)
                    except ValueError as error:
                        return self._set_protocol_reject(
                            "bot_state", "ammo_contract",
                            "bot=%s reason=%s" % (bot_id, error))
                previous_fire = int((previous or {}).get("fire_seq", 0))
                if (previous is not None and
                        int(previous.get("stun_end_server_time_ms", 0)) > 0 and
                        int(current.get("stun_end_server_time_ms", 0)) == 0):
                    stun_clears.append(bot_id)
                next_states[bot_id] = current
                previous_fire_active = bool(
                    (previous or {}).get("critical") and
                    previous["critical"].get("fire", False))
                fire_tick_damage = max(1, int(
                    int(current.get("max_health", 0)) * 0.05))
                previous_health = int((previous or {}).get("health", 0))
                current_health = int(current.get("health", 0))
                if (previous is not None and
                        (current_health < previous_health or
                         _critical_damage_transition(
                             previous.get("critical"),
                             current.get("critical")))):
                    capture_resets.add(bot_id)
                if (previous is not None and previous.get("alive") and
                        not current.get("alive") and
                        previous_fire_active and
                        current_health == max(
                            0, previous_health - fire_tick_damage) and
                        current.get("fire_attacker_kind") in (
                            "player", "bot") and
                        int(current.get("fire_attacker_id", 0)) > 0):
                    current["death_reason"] = 1
                    current["death_attacker_kind"] = current[
                        "fire_attacker_kind"]
                    current["death_attacker_id"] = int(current[
                        "fire_attacker_id"])
                    fire_deaths.append((
                        current["fire_attacker_kind"],
                        current["fire_attacker_id"], current,
                        previous_health - current_health))
                if (not current.get("alive") or
                        not bool((current.get("critical") or {}).get(
                            "fire", False))):
                    current["fire_attacker_kind"] = ""
                    current["fire_attacker_id"] = 0
                if (not source_clock_rebase and current["alive"] and
                        (previous is None or previous.get("alive")) and
                        current["fire_seq"] > previous_fire):
                    if self.client_build == CLIENT_BUILD_0922:
                        for edge in burst_edges:
                            shot_seq = (int(edge["burst_group_seq"]) +
                                        int(edge["burst_index"]))
                            if (source_time_us is None or
                                    next_launch_clock_offset_us is None):
                                return self._set_protocol_reject(
                                    "bot_state", "launch_sample_time",
                                    "bot=%s shot_seq=%s" % (
                                        bot_id, shot_seq))
                            edge = dict(edge)
                            edge.update({
                                "sample_start_us": max(
                                    0, int(previous_source_time_us or 0)),
                                "sample_end_us": int(source_time_us),
                                "launch_clock_offset_us": int(
                                    next_launch_clock_offset_us),
                            })
                            pending_projectile_launches[(
                                bot_id, shot_seq)] = edge
                    else:
                        shot_event = {
                            "kind": "bot_shot", "attacker_bot": bot_id,
                            "shot_seq": current["fire_seq"],
                            "shell_index": current["shell_index"],
                        }
                        if ("shot_yaw" in current and
                                "shot_pitch" in current):
                            shot_event["shot_yaw"] = current["shot_yaw"]
                            shot_event["shot_pitch"] = current["shot_pitch"]
                        shot_events.append(shot_event)
            if seen != set(identities):
                return self._set_protocol_reject(
                    "bot_state", "batch_members",
                    "missing=%s" % sorted(set(identities) - seen))
            self._commit_human_ram_armors(human_ram_armors)
            self.bot_states = next_states
            for bot_id in stun_clears:
                self.pending_events.append({
                    "kind": "stun", "active": False,
                    "target_kind": "bot", "target_id": int(bot_id),
                    "stun_end_server_time_ms": 0,
                })
            self.bot_pending_projectile_launches.update(
                pending_projectile_launches)
            self.bot_pending_projectile_metadata.update(
                pending_projectile_launches)
            for bot_id in capture_resets:
                self._drop_capture_for_vehicle("bot", bot_id)
            for attacker_kind, attacker_id, victim, damage in fire_deaths:
                self._record_frag(
                    attacker_kind, attacker_id, victim["team"],
                    "bot", victim["id"])
                event = {
                    "kind": ("bot_bot_hit" if attacker_kind == "bot"
                             else "bot_hit"),
                    "target_bot": victim["id"],
                    "damage": damage, "health": 0, "dead": True,
                    "attack_reason": 1, "death_reason": 1,
                    "source": "fire",
                }
                event["attacker_bot" if attacker_kind == "bot"
                      else "attacker"] = int(attacker_id)
                self.pending_events.append(event)
            self.pending_events.extend(shot_events)
            self._maybe_finish_battle()
            # New authorities publish their actual integrated-time clock.
            # Mapping its deltas onto the server epoch keeps native probe and
            # serialization duration out of the pose velocity. Older clients
            # retain receipt-time stamping until they opt into this field.
            self.bot_state_time_us = next_bot_state_time_us
            self.motion_time_offset_us = next_motion_time_offset_us
            if source_time_us is not None:
                self.bot_source_time_us = source_time_us
                self.bot_source_receipt_time_us = (
                    received_raw_motion_time_us)
                self.bot_source_batch_horizon_us = (
                    source_batch_horizon_us)
                self.bot_launch_clock_offset_us = (
                    next_launch_clock_offset_us)
            self.bot_state_revision += 1
            return True

    def update_simulation_progress(self, worker, message):
        """Accept one strictly advancing native simulation frame marker."""
        with self.lock:
            if (not isinstance(message, dict) or
                    set(message) != {
                        "type", "round_id", "authority_epoch", "frame_seq"} or
                    message.get("type") != "simulation_progress" or
                    self.simulation_worker is not worker or
                    not worker.connected or
                    self.bot_authority_id != SIMULATION_WORKER_AUTHORITY_ID or
                    self.phase not in ("loading", "battle") or
                    self.battle_result is not None):
                return False
            try:
                round_id = _exact_int(
                    message.get("round_id"), 1, PROJECTILE_MAX_ID)
                authority_epoch = _exact_int(
                    message.get("authority_epoch"), 0, PROJECTILE_MAX_ID)
                frame_seq = _exact_int(
                    message.get("frame_seq"), 0, PROJECTILE_MAX_ID)
            except ValueError:
                return False
            if (round_id != self.round_id or
                    authority_epoch != self.authority_epoch):
                return False
            same_tenure = (
                worker.simulation_progress_round_id == round_id and
                worker.simulation_progress_authority_epoch == authority_epoch)
            if (same_tenure and
                    frame_seq <= worker.simulation_progress_frame_seq):
                return False
            worker.simulation_progress_round_id = round_id
            worker.simulation_progress_authority_epoch = authority_epoch
            worker.simulation_progress_frame_seq = frame_seq
            return True

    def update_player_environment(self, authority_id, message):
        """Accept only internal water observations; never a client verdict."""
        with self.lock:
            if (not isinstance(message, dict) or
                    set(message) != {
                        "type", "round_id", "authority_epoch",
                        "sample_seq", "observations"} or
                    message.get("type") != "player_environment" or
                    authority_id != self.bot_authority_id or
                    authority_id != SIMULATION_WORKER_AUTHORITY_ID):
                return False
            worker = self.simulation_worker
            if worker is None or not worker.connected:
                return False
            try:
                round_id = _exact_int(
                    message.get("round_id"), 1, PROJECTILE_MAX_ID)
                authority_epoch = _exact_int(
                    message.get("authority_epoch"), 0,
                    PROJECTILE_MAX_ID)
                sample_seq = _exact_int(
                    message.get("sample_seq"), 1,
                    PROJECTILE_MAX_ID)
            except ValueError:
                return False
            if (round_id != self.round_id or
                    authority_epoch != self.authority_epoch):
                return False
            terminal_noop = bool(
                not self._combat_accepting() or
                self.battle_result is not None)
            stale_noop = bool(
                self.player_environment_authority_epoch ==
                authority_epoch and
                sample_seq <= self.player_environment_seq)
            raw_observations = message.get("observations")
            if (not isinstance(raw_observations, list) or
                    len(raw_observations) > self.max_players):
                return False
            observations = {}
            seen_player_ids = set()
            for raw in raw_observations:
                if (not isinstance(raw, dict) or
                        set(raw) not in (
                            {"player_id", "input_seq", "level"},
                            {"player_id", "input_seq", "level",
                             "drowning_critical"})):
                    return False
                try:
                    player_id = _exact_int(
                        raw.get("player_id"), 1, PROJECTILE_MAX_ID)
                    input_seq = _exact_int(
                        raw.get("input_seq"), 0, PROJECTILE_MAX_ID)
                    level = _exact_int(raw.get("level"), 0, 2)
                except ValueError:
                    return False
                player = self.players.get(player_id)
                if player_id in seen_player_ids:
                    return False
                seen_player_ids.add(player_id)
                drowning_critical = None
                if "drowning_critical" in raw:
                    if level != 2:
                        return False
                    try:
                        drowning_critical = _critical_payload(
                            raw["drowning_critical"])
                    except ValueError:
                        return False
                # The worker may have sampled a player immediately before the
                # connection was removed from this room.  The row still has to
                # pass the complete trusted-worker envelope above, but it no
                # longer has canonical state to update and must not poison live
                # rows in the same batch.
                if player is None:
                    continue
                previous = self.player_environment.get(player_id)
                # A snapshot can be overtaken independently by player death,
                # leaving the battle, or a newer visible-client pose.  Skip
                # only that stale row so unrelated live observations in the
                # same trusted-worker batch continue to converge.
                if (not player.connected or not player.participating or
                        not player.alive or input_seq > player.input_seq or
                        (previous is not None and
                         input_seq < int(previous["input_seq"]))):
                    continue
                observation = {
                    "input_seq": input_seq,
                    "level": level,
                    "observed_tick": int(self.tick),
                }
                if drowning_critical is not None:
                    observation["drowning_critical"] = drowning_critical
                observations[player_id] = observation
            # The worker can finish an already-queued observation after the
            # player dies, a newer sample commits, or the round reaches its
            # terminal result. Validate the complete batch first, then retain
            # the last canonical water state as a successful no-op.
            if terminal_noop or stale_noop:
                return True
            self.player_environment = observations
            self.player_environment_seq = sample_seq
            self.player_environment_authority_epoch = authority_epoch
            return True

    def _tick_player_drowning(self, dt):
        """Own the continuous ten-second law and terminal HP transition."""
        if not self._combat_accepting() or self.battle_result is not None:
            return 0
        deaths = 0
        for player in list(self.players.values()):
            if (not player.connected or not player.participating or
                    not player.alive):
                self.player_drowning_seconds.pop(player.player_id, None)
                continue
            observation = self.player_environment.get(player.player_id)
            fresh = bool(
                observation is not None and
                self.tick - int(observation["observed_tick"]) <=
                PLAYER_ENVIRONMENT_STALE_TICKS)
            # Missing/stale telemetry is not evidence that the vehicle
            # surfaced.  Pause at the already-earned drowning time until a
            # fresh worker observation arrives; only a fresh non-drowning
            # sample resets the continuous timer.
            if not fresh:
                continue
            if int(observation["level"]) != 2:
                self.player_drowning_seconds.pop(player.player_id, None)
                continue
            elapsed = self.player_drowning_seconds.get(
                player.player_id, 0.0) + max(0.0, float(dt))
            self.player_drowning_seconds[player.player_id] = elapsed
            if elapsed <= PLAYER_DROWNING_SECONDS:
                continue
            critical = observation.get("drowning_critical")
            if critical is None:
                self.player_drowning_seconds[player.player_id] = (
                    PLAYER_DROWNING_SECONDS)
                continue
            try:
                critical = _critical_payload(critical)
            except ValueError:
                self.player_drowning_seconds[player.player_id] = (
                    PLAYER_DROWNING_SECONDS)
                continue
            critical_before = player.critical
            display_health = max(0, int(player.health))
            damage = display_health
            player.health = 0
            player.alive = False
            player.display_health = display_health
            player.death_reason = 5
            player.death_attacker_kind = ""
            player.death_attacker_id = 0
            player.forward = 0.0
            player.turn = 0.0
            player.speed = 0.0
            player.pending_fire_intents.clear()
            critical_commit = self._commit_external_player_critical(
                player, critical)
            self._record_damage(
                None, ("player", player.player_id), damage,
                critical_before)
            self._drop_capture_for_vehicle("player", player.player_id)
            event = {
                "kind": "health",
                "target": player.player_id,
                "damage": damage,
                "health": 0,
                "dead": True,
                "display_health": display_health,
                "attack_reason": 5,
                "death_reason": 5,
                "source": "environment",
                "critical": critical,
            }
            event.update(critical_commit)
            self.pending_events.append(event)
            self.player_environment.pop(player.player_id, None)
            self.player_drowning_seconds.pop(player.player_id, None)
            self.player_overturn_state.pop(player.player_id, None)
            deaths += 1
            if self._maybe_finish_battle():
                break
        return deaths

    def _commit_player_environment_damage(
            self, player, damage, reason, display_health=None):
        """Apply one server-decided fall or overturn HP delta atomically."""
        if player is None or not player.alive or player.health <= 0:
            return False
        damage = min(max(0, int(damage)), int(player.health))
        if damage <= 0:
            return False
        reason = int(reason)
        critical_before = player.critical
        health = max(0, int(player.health) - damage)
        dead = health <= 0
        player.health = health
        player.alive = not dead
        if dead:
            player.display_health = max(
                0, int(health if display_health is None else display_health))
            player.death_reason = reason
            player.death_attacker_kind = ""
            player.death_attacker_id = 0
            player.forward = 0.0
            player.turn = 0.0
            player.speed = 0.0
            player.pending_fire_intents.clear()
        else:
            player.display_health = int(player.health)
        self._record_damage(
            None, ("player", player.player_id), damage, critical_before)
        self._drop_capture_for_vehicle("player", player.player_id)
        self.pending_events.append({
            "kind": "health",
            "target": player.player_id,
            "damage": damage,
            "health": int(player.health),
            "dead": dead,
            "display_health": (
                int(player.display_health) if dead else int(player.health)),
            "attack_reason": reason,
            "death_reason": reason if dead else 0,
            "source": "environment",
        })
        return True

    def _player_overturn_danger(self, player_id):
        state = self.player_overturn_state.get(int(player_id))
        return bool(
            isinstance(state, dict) and int(state.get("level", 0)) == 2 and
            float(state.get("check", 0.0)) + 0.000001 >=
            PLAYER_OVERTURN_IGNORE_SECONDS)

    def _tick_player_overturn(self, dt):
        """Own the shared ignore gate and thirty-second terminal countdown."""
        if not self._combat_accepting() or self.battle_result is not None:
            return 0
        deaths = 0
        step = max(0.0, float(dt))
        for player in list(self.players.values()):
            if (not player.connected or not player.participating or
                    not player.alive):
                self.player_overturn_state.pop(player.player_id, None)
                continue
            level = vehicle_physics.overturn_level_from_up_cosine(
                float(player.up_cosine))
            if level == 0:
                self.player_overturn_state.pop(player.player_id, None)
                continue
            state = self.player_overturn_state.setdefault(
                player.player_id,
                {"check": 0.0, "time": 0.0, "level": 0})
            state["check"] = float(state.get("check", 0.0)) + step
            if (state["check"] + 0.000001 <
                    PLAYER_OVERTURN_IGNORE_SECONDS):
                continue
            if level != int(state.get("level", 0)):
                state["level"] = level
                state["time"] = 0.0
            if level != 2:
                state["time"] = 0.0
                continue
            player.forward = 0.0
            player.turn = 0.0
            player.speed = 0.0
            state["time"] = float(state.get("time", 0.0)) + step
            if (state["time"] + 0.000001 <
                    PLAYER_OVERTURN_DEATH_SECONDS):
                continue
            if self._commit_player_environment_damage(
                    player, int(player.health), 7, display_health=0):
                deaths += 1
            self.player_environment.pop(player.player_id, None)
            self.player_drowning_seconds.pop(player.player_id, None)
            self.player_overturn_state.pop(player.player_id, None)
            if self._maybe_finish_battle():
                break
        return deaths

    def _landing_observation_result(
            self, player, observation_seq, input_seq, accepted, reason):
        return {
            "type": "landing_observation_result",
            "round_id": int(self.round_id),
            "authority_epoch": int(self.authority_epoch),
            "observation_seq": int(observation_seq),
            "input_seq": int(input_seq),
            "committed_seq": int(player.landing_observation_seq),
            "accepted": bool(accepted),
            "reason": str(reason or "")[:32],
        }

    @staticmethod
    def _offer_landing_observation_result(player, result):
        return bool(player is not None and player.connected and
                    player.offer_reliable(dict(result)))

    def submit_landing_observation(self, player_id, message):
        """Apply fall damage from one sequenced physical impact sample."""
        with self.lock:
            player = self.players.get(player_id)
            if (self.client_build != CLIENT_BUILD_0922 or
                    player is None or not player.connected or
                    PLAYER_ENVIRONMENT_CAPABILITY not in
                    player.capabilities or
                    not isinstance(message, dict) or
                    set(message) != {
                        "type", "round_id", "authority_epoch",
                        "observation_seq", "input_seq", "impact_speed"} or
                    message.get("type") != "landing_observation" or
                    not self._message_round_matches(message)):
                return False
            try:
                authority_epoch = _exact_int(
                    message.get("authority_epoch"), 0, PROJECTILE_MAX_ID)
                observation_seq = _exact_int(
                    message.get("observation_seq"), 1, PROJECTILE_MAX_ID)
                input_seq = _exact_int(
                    message.get("input_seq"), 1, PROJECTILE_MAX_ID)
            except (TypeError, ValueError, OverflowError):
                return False
            raw_impact_speed = message.get("impact_speed")
            if (isinstance(raw_impact_speed, bool) or
                    not isinstance(raw_impact_speed, (int, float)) or
                    not math.isfinite(float(raw_impact_speed)) or
                    not 0.0 <= float(raw_impact_speed) <=
                    PLAYER_LANDING_MAX_IMPACT_SPEED):
                return False
            impact_speed = round(float(raw_impact_speed), 6)
            normalized = {
                "authority_epoch": authority_epoch,
                "observation_seq": observation_seq,
                "input_seq": input_seq,
                "impact_speed": impact_speed,
            }
            fingerprint = _message_fingerprint(normalized)
            previous = player.landing_observation_fingerprints.get(
                observation_seq)
            if previous is not None:
                if previous != fingerprint:
                    result = self._landing_observation_result(
                        player, observation_seq, input_seq, False,
                        "identity_conflict")
                    self._offer_landing_observation_result(player, result)
                    return False
                result = player.landing_observation_results.get(
                    observation_seq)
                if result is None:
                    return False
                replay = dict(result)
                replay["authority_epoch"] = int(self.authority_epoch)
                return self._offer_landing_observation_result(player, replay)
            if authority_epoch != self.authority_epoch:
                result = self._landing_observation_result(
                    player, observation_seq, input_seq, False,
                    "stale_authority")
                self._offer_landing_observation_result(player, result)
                return False
            if observation_seq != player.landing_observation_seq + 1:
                result = self._landing_observation_result(
                    player, observation_seq, input_seq, False,
                    "sequence_gap")
                self._offer_landing_observation_result(player, result)
                return False
            if (not self._combat_accepting() or
                    self.battle_result is not None or
                    not player.participating):
                result = self._landing_observation_result(
                    player, observation_seq, input_seq, False,
                    "not_active")
                self._offer_landing_observation_result(player, result)
                return False
            if not player.alive or player.health <= 0:
                result = self._landing_observation_result(
                    player, observation_seq, input_seq, False,
                    "player_dead")
                self._offer_landing_observation_result(player, result)
                return False
            known_input = bool(
                input_seq == player.input_seq or
                input_seq in player.input_fingerprints)
            if (not known_input or
                    input_seq <= player.landing_observation_input_seq):
                result = self._landing_observation_result(
                    player, observation_seq, input_seq, False,
                    "stale_input")
                self._offer_landing_observation_result(player, result)
                return False
            damage = vehicle_physics.fall_damage(
                int(player.max_health), impact_speed)
            self._commit_player_environment_damage(
                player, damage, 3, display_health=0)
            player.landing_observation_seq = observation_seq
            player.landing_observation_input_seq = input_seq
            player.landing_observation_fingerprints[
                observation_seq] = fingerprint
            result = self._landing_observation_result(
                player, observation_seq, input_seq, True, "")
            player.landing_observation_results[observation_seq] = result
            while (len(player.landing_observation_fingerprints) >
                   PLAYER_LANDING_HISTORY):
                old_seq, unused = \
                    player.landing_observation_fingerprints.popitem(
                        last=False)
                player.landing_observation_results.pop(old_seq, None)
            offered = self._offer_landing_observation_result(player, result)
            if not player.alive:
                self.player_environment.pop(player.player_id, None)
                self.player_drowning_seconds.pop(player.player_id, None)
                self.player_overturn_state.pop(player.player_id, None)
                self._maybe_finish_battle()
            return offered

    @staticmethod
    def _projectile_message_fits(message):
        try:
            payload = json.dumps(
                message, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True)
        except (TypeError, ValueError, OverflowError):
            return False
        return len(payload.encode("utf-8")) + 1 <= MAX_LINE_BYTES

    @staticmethod
    def _projectile_payload_is_finite(value):
        """Reject non-JSON or non-finite scalars at the worker boundary."""
        if value is None or isinstance(value, (bool, str)):
            return True
        if isinstance(value, (int, float)):
            try:
                return math.isfinite(float(value))
            except (OverflowError, ValueError):
                return False
        if isinstance(value, (list, tuple)):
            return all(BattleState._projectile_payload_is_finite(item)
                       for item in value)
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and
                BattleState._projectile_payload_is_finite(item)
                for key, item in value.items())
        return False

    @staticmethod
    def _projectile_id(round_id, shooter_kind, shooter_id, shot_seq):
        prefix = "p" if shooter_kind == "player" else "b"
        return "%d:%s:%d:%d" % (
            int(round_id), prefix, int(shooter_id), int(shot_seq))

    @staticmethod
    def _projectile_burst(message, shot_seq):
        fields = ("burst_group_seq", "burst_index", "burst_count")
        present = tuple(name in message for name in fields)
        if not any(present):
            return {
                "burst_group_seq": shot_seq,
                "burst_index": 0,
                "burst_count": 1,
            }
        if not all(present):
            raise ValueError("projectile burst metadata is incomplete")
        group_seq = _exact_int(
            message.get("burst_group_seq"), 1, PROJECTILE_MAX_ID)
        count = _exact_int(
            message.get("burst_count"), 1,
            burst_mechanics.MAX_BURST_COUNT)
        index = _exact_int(message.get("burst_index"), 0, count - 1)
        if shot_seq != group_seq + index:
            raise ValueError("projectile burst sequence is invalid")
        return {
            "burst_group_seq": group_seq,
            "burst_index": index,
            "burst_count": count,
        }

    def _projectile_authority_matches(self, player_id, message):
        try:
            epoch = _exact_int(
                message.get("authority_epoch"), 0, PROJECTILE_MAX_ID)
        except ValueError:
            return False
        return (player_id == self.bot_authority_id and
                epoch == self.authority_epoch)

    def _trusted_internal_projectile_authority(self, authority_id):
        """Recognize only the configured non-player simulation endpoint."""
        worker = self.simulation_worker
        return bool(
            authority_id == SIMULATION_WORKER_AUTHORITY_ID and
            self.bot_authority_id == SIMULATION_WORKER_AUTHORITY_ID and
            worker is not None and worker.connected)

    def _commit_fire_intent_rejection_locked(
            self, player, intent_seq, fingerprint, reason,
            already_admitted=False):
        """Consume one identified trigger and publish its terminal result."""
        reason = str(reason or "rejected")[:64]
        terminal = (False, reason)
        previous_fingerprint = player.fire_intent_fingerprints.get(
            intent_seq)
        if already_admitted:
            if (player.fire_intent_seq != intent_seq or
                    previous_fingerprint != fingerprint):
                return False
        else:
            if (previous_fingerprint is not None or
                    intent_seq != player.fire_intent_seq + 1):
                return False
            player.fire_intent_seq = intent_seq
            player.fire_intent_fingerprints[intent_seq] = fingerprint
            while (len(player.fire_intent_fingerprints) >
                   PLAYER_FIRE_INTENT_HISTORY):
                player.fire_intent_fingerprints.popitem(last=False)

        player.pending_fire_intents.pop(intent_seq, None)
        player.fire_intent_results[intent_seq] = terminal
        while len(player.fire_intent_results) > PLAYER_FIRE_INTENT_HISTORY:
            player.fire_intent_results.popitem(last=False)

        result_message = {
            "type": "fire_intent_result",
            "round_id": self.round_id,
            "intent_seq": intent_seq,
            "accepted": False,
            "reason": reason,
        }
        delivered = player.offer_reliable(result_message)
        if not delivered:
            self.remove_player(player.player_id, expected=player)
        return bool(delivered)

    def submit_fire_intent(self, player_id, message):
        """Consume one ordered trigger or relay it to the native authority."""
        with self.lock:
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    not isinstance(message, dict) or
                    set(message) != {
                        "type", "round_id", "intent_seq", "input_seq",
                        "shell_index", "shot_origin", "shot_direction",
                        "dispersion_angle"} or
                    message.get("type") != "fire_intent"):
                return False
            try:
                intent_seq = _exact_int(
                    message.get("intent_seq"), 1, PROJECTILE_MAX_ID)
                input_seq = _exact_int(
                    message.get("input_seq"), 1, PROJECTILE_MAX_ID)
                shell_index = _exact_int(message.get("shell_index"), 0, 9)
                shot_origin = _bounded_vector(
                    message.get("shot_origin"),
                    [-5000.0, -1000.0, -5000.0],
                    [5000.0, 3000.0, 5000.0])
                shot_direction = _bounded_vector(
                    message.get("shot_direction"),
                    [-1.0, -1.0, -1.0], [1.0, 1.0, 1.0])
                dispersion_angle = _bounded_float(
                    message.get("dispersion_angle"), 0.0,
                    MAX_PLAYER_DISPERSION_ANGLE)
            except (TypeError, ValueError, OverflowError):
                return False

            player = self.players.get(player_id)
            if player is None:
                return False
            fingerprint = _message_fingerprint({
                "player_id": int(player_id),
                "wire": message,
            })
            previous = player.fire_intent_fingerprints.get(intent_seq)
            if previous is not None:
                return previous == fingerprint
            if intent_seq != player.fire_intent_seq + 1:
                return False

            def reject(reason):
                return self._commit_fire_intent_rejection_locked(
                    player, intent_seq, fingerprint, reason)

            if self.battle_result is not None:
                return reject("battle_finished")
            if not self._combat_accepting():
                return reject("combat_not_accepting")
            if not player.connected:
                return reject("player_disconnected")
            if not player.participating:
                return reject("player_not_participating")
            if not player.alive:
                return reject("player_dead")
            if self._player_overturn_danger(player_id):
                return reject("player_overturned")

            direction_length = math.sqrt(sum(
                component * component for component in shot_direction))
            if not 0.999 <= direction_length <= 1.001:
                return reject("shot_direction_untrusted")
            if sum((shot_origin[index] - coordinate) ** 2
                   for index, coordinate in enumerate(
                       (player.x, player.y, player.z))) > \
                    PLAYER_FIRE_ORIGIN_RADIUS ** 2:
                return reject("shot_origin_untrusted")
            gun_checkpoint = player.gun_checkpoints.get(input_seq)
            if (not isinstance(gun_checkpoint, dict) or
                    player.gun_checkpoint_seq != input_seq):
                return reject("gun_checkpoint_unavailable")
            normalized = {
                "player_id": int(player_id),
                "intent_seq": intent_seq,
                "input_seq": input_seq,
                "shell_index": shell_index,
                "shot_origin": list(shot_origin),
                "shot_direction": [round(
                    component / direction_length, 8)
                    for component in shot_direction],
                "dispersion_angle": round(dispersion_angle, 8),
                "gun_checkpoint_seq": input_seq,
                "gun_checkpoint": dict(gun_checkpoint),
            }
            if input_seq != player.input_seq:
                return reject("input_checkpoint_stale")
            if shell_index != player.shell_index:
                return reject("shell_mismatch")
            if player.pose_time_us is None:
                return reject("pose_unavailable")
            if player.fire_seq >= PROJECTILE_MAX_ID:
                return reject("fire_sequence_exhausted")
            if len(player.pending_fire_intents) >= \
                    PLAYER_FIRE_INTENT_MAX_PENDING:
                return reject("fire_intent_pending")
            worker = self.simulation_worker
            worker_mode = self._trusted_internal_projectile_authority(
                SIMULATION_WORKER_AUTHORITY_ID)
            if not worker_mode:
                return reject("worker_unavailable")
            relay = {
                "type": "fire_intent",
                "round_id": self.round_id,
                "authority_epoch": self.authority_epoch,
                "player_id": int(player_id),
                "intent_seq": intent_seq,
                "shot_seq": int(player.fire_seq) + 1,
                "input_seq": input_seq,
                "pose_time_us": int(player.pose_time_us),
                "shell_index": shell_index,
                "next_shell_index": int(player.next_shell_index),
                "shell_change_pending": bool(
                    player.shell_change_pending),
                "gun_checkpoint_seq": int(
                    normalized["gun_checkpoint_seq"]),
                "gun_checkpoint": dict(normalized["gun_checkpoint"]),
                "shot_origin": list(normalized["shot_origin"]),
                "shot_direction": list(normalized["shot_direction"]),
                "dispersion_angle": normalized["dispersion_angle"],
                "aim_yaw": round(float(player.aim_yaw), 6),
                "gun_pitch": round(float(player.gun_pitch), 6),
                "x": round(float(player.x), 4),
                "y": round(float(player.y), 4),
                "z": round(float(player.z), 4),
                "yaw": round(float(player.yaw), 5),
                "pitch": round(float(player.pitch), 5),
                "roll": round(float(player.roll), 5),
                "speed": round(float(player.speed), 4),
            }
            player.fire_intent_seq = intent_seq
            player.fire_intent_fingerprints[intent_seq] = fingerprint
            player.pending_fire_intents[intent_seq] = dict(relay)
            while (len(player.fire_intent_fingerprints) >
                   PLAYER_FIRE_INTENT_HISTORY):
                player.fire_intent_fingerprints.popitem(last=False)
            if worker.offer_reliable(relay):
                return True
            delivered = self._commit_fire_intent_rejection_locked(
                player, intent_seq, fingerprint,
                "worker_send_stalled", already_admitted=True)
            self.remove_simulation_worker(worker, "worker_send_stalled")
            return delivered

    def resolve_fire_intent(self, player_id, message):
        """Commit one internal-authority rejection for a player trigger."""
        with self.lock:
            if (not self._trusted_internal_projectile_authority(player_id) or
                    not self._projectile_authority_matches(
                        player_id, message) or
                    not self._message_round_matches(message) or
                    set(message) != {
                        "type", "round_id", "authority_epoch", "player_id",
                        "intent_seq", "accepted", "reason"} or
                    message.get("accepted") is not False):
                return False
            try:
                shooter_id = _exact_int(
                    message.get("player_id"), 1, PROJECTILE_MAX_ID)
                intent_seq = _exact_int(
                    message.get("intent_seq"), 1, PROJECTILE_MAX_ID)
            except (TypeError, ValueError, OverflowError):
                return False
            reason = str(message.get("reason") or "rejected")[:64]
            shooter = self.players.get(shooter_id)
            if shooter is None:
                return False
            terminal = (False, reason)
            previous = shooter.fire_intent_results.get(intent_seq)
            if previous is not None:
                return previous == terminal
            if intent_seq not in shooter.pending_fire_intents:
                return False
            result_message = {
                "type": "fire_intent_result",
                "round_id": self.round_id,
                "intent_seq": intent_seq,
                "accepted": False,
                "reason": reason,
            }
            if not shooter.offer_reliable(result_message):
                self.remove_player(shooter_id)
                return False
            shooter.pending_fire_intents.pop(intent_seq, None)
            shooter.fire_intent_results[intent_seq] = terminal
            while len(shooter.fire_intent_results) > PLAYER_FIRE_INTENT_HISTORY:
                shooter.fire_intent_results.popitem(last=False)
            return True

    def reject_player_projectile_launch(
            self, player_id, message, reason="projectile_launch_rejected"):
        """Terminate one admitted trigger after its worker launch is refused."""
        with self.lock:
            if (not self._trusted_internal_projectile_authority(player_id) or
                    not self._message_round_matches(message) or
                    not self._projectile_authority_matches(
                        player_id, message) or
                    message.get("shooter_kind") != "player"):
                return False
            try:
                shooter_id = _exact_int(
                    message.get("shooter_id"), 1, PROJECTILE_MAX_ID)
                intent_seq = _exact_int(
                    message.get("fire_intent_seq"), 1,
                    PROJECTILE_MAX_ID)
                input_seq = _exact_int(
                    message.get("fire_input_seq"), 1,
                    PROJECTILE_MAX_ID)
                shot_seq = _exact_int(
                    message.get("shot_seq"), 1, PROJECTILE_MAX_ID)
                shell_index = _exact_int(
                    message.get("shell_index"), 0, 9)
            except (TypeError, ValueError, OverflowError):
                return False
            shooter = self.players.get(shooter_id)
            if shooter is None:
                return False
            terminal = (False, str(reason or "projectile_launch_rejected")[:64])
            previous = shooter.fire_intent_results.get(intent_seq)
            if previous is not None:
                return previous == terminal
            intent = shooter.pending_fire_intents.get(intent_seq)
            if (intent is None or
                    int(intent.get("player_id", 0)) != shooter_id or
                    int(intent.get("intent_seq", 0)) != intent_seq or
                    int(intent.get("input_seq", 0)) != input_seq or
                    int(intent.get("shot_seq", 0)) != shot_seq or
                    int(intent.get("shell_index", -1)) != shell_index):
                return False

            result_message = {
                "type": "fire_intent_result",
                "round_id": self.round_id,
                "player_id": shooter_id,
                "intent_seq": intent_seq,
                "accepted": False,
                "reason": terminal[1],
            }
            # Commit before offering the terminal message. If either endpoint
            # has already stalled, its transport is retired below; retaining
            # an unresolvable trigger would poison a later shot sequence.
            shooter.pending_fire_intents.pop(intent_seq, None)
            shooter.fire_intent_results[intent_seq] = terminal
            while len(shooter.fire_intent_results) > PLAYER_FIRE_INTENT_HISTORY:
                shooter.fire_intent_results.popitem(last=False)

            worker = self.simulation_worker
            player_delivered = shooter.offer_reliable(result_message)
            worker_delivered = bool(
                worker is not None and worker.connected and
                worker.offer_reliable(result_message))
            if not player_delivered:
                self.remove_player(shooter_id, expected=shooter)
            if not worker_delivered and worker is not None:
                self.remove_simulation_worker(
                    worker, "worker_result_send_stalled")
            return bool(player_delivered and worker_delivered)

    def launch_projectile(self, player_id, message):
        """Atomically admit one #1513 shot into the round projectile ledger."""
        with self.lock:
            reject_kind = "projectile_launch"
            self._clear_protocol_reject(reject_kind)
            if self.client_build != CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    reject_kind, "build", "unsupported client build")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    reject_kind, "round", "projectile round does not match")
            if not self._projectile_message_fits(message):
                return self._set_protocol_reject(
                    reject_kind, "size", "projectile message exceeds limits")
            if not self._projectile_payload_is_finite(message):
                return self._set_protocol_reject(
                    reject_kind, "finite",
                    "projectile message contains a non-finite value")
            if not self._projectile_authority_matches(player_id, message):
                return self._set_protocol_reject(
                    reject_kind, "authority",
                    "projectile authority does not match")
            if self.battle_result is not None:
                # A finite, same-round command from the room-owned worker may
                # arrive after the terminal battle event.  The result is
                # already canonical, so this is an idempotent late delivery.
                return True
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    reject_kind, "phase", "combat is not accepting commands")
            allowed = {
                "type", "round_id", "shooter_kind", "shooter_id",
                "shot_seq", "shell_index", "origin", "velocity",
                "gravity", "max_distance", "max_time_ms", "is_he",
                "splash_radius", "penetration_factor", "source_shot",
                "authority_epoch", "fire_intent_seq", "fire_input_seq",
                "burst_group_seq", "burst_index", "burst_count",
                "launch_time_us", "launch_pose",
            }
            if set(message) - allowed:
                return self._set_protocol_reject(
                    reject_kind, "shape", "projectile launch has extra fields")
            try:
                shooter_kind = str(message.get("shooter_kind"))
                if shooter_kind not in ("player", "bot"):
                    raise ValueError("invalid shooter kind")
                shooter_id = _exact_int(
                    message.get("shooter_id"), 1, PROJECTILE_MAX_ID)
                shot_seq = _exact_int(
                    message.get("shot_seq"), 1, PROJECTILE_MAX_ID)
                burst = self._projectile_burst(message, shot_seq)
                shell_index = _exact_int(message.get("shell_index"), 0, 9)
                origin = _bounded_vector(
                    message.get("origin"), (-5000.0, -1000.0, -5000.0),
                    (5000.0, 3000.0, 5000.0))
                velocity = _bounded_vector(
                    message.get("velocity"),
                    (-PROJECTILE_MAX_VELOCITY,) * 3,
                    (PROJECTILE_MAX_VELOCITY,) * 3)
                speed = math.sqrt(sum(component * component
                                      for component in velocity))
                if speed <= 0.001 or speed > PROJECTILE_MAX_VELOCITY:
                    raise ValueError("invalid launch speed")
                gravity = round(_bounded_float(
                    message.get("gravity"), 0.0,
                    PROJECTILE_MAX_GRAVITY, False), 6)
                max_distance = round(_bounded_float(
                    message.get("max_distance"), 0.0,
                    PROJECTILE_MAX_DISTANCE, False), 6)
                max_time_ms = _exact_int(
                    message.get("max_time_ms"), 1,
                    PROJECTILE_MAX_LIFETIME_MS)
                if not isinstance(message.get("is_he"), bool):
                    raise ValueError("invalid HE flag")
                is_he = message["is_he"]
                splash_radius = round(_bounded_float(
                    message.get("splash_radius"), 0.0,
                    PROJECTILE_MAX_SPLASH_RADIUS), 6)
                penetration_factor = round(_bounded_float(
                    message.get("penetration_factor"), 0.0, 100.0), 6)
                source_shot = _projectile_source_shot(
                    message.get("source_shot"))
                fire_intent_seq = (
                    _exact_int(message.get("fire_intent_seq"), 1,
                               PROJECTILE_MAX_ID)
                    if shooter_kind == "player" else None)
                fire_input_seq = (
                    _exact_int(message.get("fire_input_seq"), 1,
                               PROJECTILE_MAX_ID)
                    if shooter_kind == "player" else None)
                if (shooter_kind == "bot" and
                        ("fire_intent_seq" in message or
                         "fire_input_seq" in message)):
                    raise ValueError("bot launch has a player intent")
                launch_time_us = (
                    _exact_int(message.get("launch_time_us"), 0,
                               MAX_MOTION_TIME_US)
                    if shooter_kind == "bot" else None)
                launch_pose = (
                    _bounded_bot_launch_pose(message.get("launch_pose"))
                    if shooter_kind == "bot" else None)
                if (shooter_kind == "player" and
                        ("launch_time_us" in message or
                         "launch_pose" in message)):
                    raise ValueError("player launch has a bot logical pose")
                if not is_he and splash_radius != 0.0:
                    raise ValueError("AP projectile cannot have splash")
            except (TypeError, ValueError, OverflowError) as error:
                return self._set_protocol_exception(reject_kind, error)

            projectile_id = self._projectile_id(
                self.round_id, shooter_kind, shooter_id, shot_seq)
            normalized = {
                "shooter_kind": shooter_kind, "shooter_id": shooter_id,
                "shot_seq": shot_seq, "shell_index": shell_index,
                "origin": origin, "velocity": velocity,
                "gravity": gravity, "max_distance": max_distance,
                "max_time_ms": max_time_ms, "is_he": is_he,
                "splash_radius": splash_radius,
                "penetration_factor": penetration_factor,
                "source_shot": source_shot,
            }
            normalized.update(burst)
            if launch_time_us is not None:
                normalized["launch_time_us"] = launch_time_us
                normalized["launch_pose"] = launch_pose
            if fire_intent_seq is not None:
                normalized["fire_intent_seq"] = fire_intent_seq
                normalized["fire_input_seq"] = fire_input_seq
            launch_fingerprint = _message_fingerprint(normalized)
            if shooter_kind == "player":
                shooter = self.players.get(shooter_id)
                if (shooter is None or not shooter.connected or
                        not self._trusted_internal_projectile_authority(
                            player_id) or
                        not self._projectile_authority_matches(
                            player_id, message)):
                    return self._set_protocol_reject(
                        reject_kind, "authority",
                        "player projectile authority does not match")
            else:
                if not self._projectile_authority_matches(player_id, message):
                    return self._set_protocol_reject(
                        reject_kind, "authority",
                        "bot projectile authority does not match")
                shooter = self.bot_states.get(shooter_id)
            active = self.projectiles.get(projectile_id)
            if active is not None:
                if active["launch_fingerprint"] == launch_fingerprint:
                    return True
                return self._set_protocol_reject(
                    reject_kind, "order", "active launch retry changed")
            terminal = self.projectile_tombstones.get(projectile_id)
            if terminal is not None:
                if terminal["launch_fingerprint"] == launch_fingerprint:
                    return True
                return self._set_protocol_reject(
                    reject_kind, "order", "retired launch retry changed")

            if len(self.projectiles) >= PROJECTILE_MAX_ACTIVE:
                return self._set_protocol_reject(
                    reject_kind, "capacity", "active projectile limit reached")
            shooter_active = sum(
                1 for record in self.projectiles.values()
                if (record["shooter_kind"] == shooter_kind and
                    record["shooter_id"] == shooter_id))
            if shooter_active >= PROJECTILE_MAX_PER_SHOOTER:
                return self._set_protocol_reject(
                    reject_kind, "capacity", "shooter projectile limit reached")

            if shooter_kind == "player":
                intent = shooter.pending_fire_intents.get(fire_intent_seq)
                if intent is None:
                    return self._set_protocol_reject(
                        reject_kind, "order", "player fire intent is missing")
                if (not shooter.participating or
                        not shooter.alive or
                        shot_seq != shooter.fire_seq + 1 or
                        int(intent["shot_seq"]) != shot_seq or
                        int(intent["input_seq"]) != fire_input_seq):
                    return self._set_protocol_reject(
                        reject_kind, "order",
                        "player projectile sequence does not match intent")
                team = shooter.team
                source_vehicle = shooter.vehicle
                shooter_position = [
                    intent["x"], intent["y"], intent["z"]]
            else:
                launch_edge = (shooter_id, shot_seq)
                expected_edge = self.bot_pending_projectile_metadata.get(
                    launch_edge)
                if (expected_edge is None and launch_edge in
                        self.bot_pending_projectile_launches):
                    expected_edge = {
                        "burst_group_seq": shot_seq,
                        "burst_index": 0,
                        "burst_count": 1,
                        "shell_index": shell_index,
                    }
                if shooter is None:
                    return self._set_protocol_reject(
                        reject_kind, "identity", "bot shooter is unknown")
                if not shooter.get("alive"):
                    # Death can overtake a launch already queued by the
                    # worker.  No live edge remains to admit.
                    return True
                if expected_edge is None:
                    if shot_seq <= int(shooter.get("fire_seq", 0)):
                        # The canonical state already crossed this edge, but
                        # its pending metadata was consumed or retired before
                        # this retry arrived.
                        return True
                    return self._set_protocol_reject(
                        reject_kind, "launch_edge_pending",
                        "bot projectile is waiting for its launch edge")
                if any(pending_bot == shooter_id and
                       pending_seq < shot_seq
                       for pending_bot, pending_seq in
                       self.bot_pending_projectile_launches):
                    return self._set_protocol_reject(
                        reject_kind, "order",
                        "bot projectile launch edge is out of order")
                launch_clock_offset_us = expected_edge.get(
                    "launch_clock_offset_us", self.bot_launch_clock_offset_us)
                if launch_clock_offset_us is None:
                    launch_clock_offset_us = (
                        self._server_time_ms() * 1000 - launch_time_us)
                try:
                    mapped_launch_time_us = (
                        launch_time_us + int(launch_clock_offset_us))
                except (TypeError, ValueError, OverflowError) as error:
                    return self._set_protocol_exception(reject_kind, error)
                # The room-owned worker is the simulation clock authority.
                # Network scheduling can put the mapped edge a frame ahead of
                # server receipt; clamp that representational skew instead of
                # rejecting every otherwise valid bot shot once.
                mapped_launch_time_us = max(
                    0, min(mapped_launch_time_us,
                           self._server_time_ms() * 1000))
                team = int(shooter.get("team", 0))
                if team not in (1, 2):
                    return self._set_protocol_reject(
                        reject_kind, "identity", "bot team is invalid")
                source_vehicle = str(shooter.get("vehicle", ""))
                shooter_position = list(launch_pose[:3])
            if not source_vehicle or len(source_vehicle) > 128:
                return self._set_protocol_reject(
                    reject_kind, "identity", "source vehicle is invalid")
            try:
                range_origin = _bounded_vector(
                    shooter_position,
                    (-5000.0, -1000.0, -5000.0),
                    (5000.0, 3000.0, 5000.0))
            except (TypeError, ValueError, OverflowError) as error:
                return self._set_protocol_exception(reject_kind, error)

            launch_server_time_ms = (
                int(round(float(mapped_launch_time_us) / 1000.0))
                if shooter_kind == "bot" else self._server_time_ms())
            record = dict(normalized)
            record.update({
                "projectile_id": projectile_id,
                "source_vehicle": source_vehicle,
                "team": int(team),
                "range_origin": range_origin,
                "segment_origin": list(origin),
                "segment_velocity": list(velocity),
                "segment_start_time_ms": 0,
                "ricochet_count": 0,
                "base_penetration_multiplier": 1.0,
                "launch_server_time_ms": launch_server_time_ms,
                "checked_through_ms": 0,
                "checked_distance": 0.0,
                "piercing_loss": 0.0,
                "launch_fingerprint": launch_fingerprint,
                "last_progress_fingerprint": None,
                "last_progress_request_fingerprint": None,
            })
            self.projectiles[projectile_id] = record
            if shooter_kind == "player":
                shooter.fire_seq = shot_seq
                if bool(intent.get("shell_change_pending", False)):
                    shooter.shell_index = int(intent["next_shell_index"])
                else:
                    shooter.shell_index = shell_index
                shooter.next_shell_index = shooter.shell_index
                shooter.shell_change_pending = False
                shooter.pending_fire_intents.pop(fire_intent_seq, None)
                shooter.fire_intent_results[fire_intent_seq] = (
                    True, projectile_id)
                while (len(shooter.fire_intent_results) >
                       PLAYER_FIRE_INTENT_HISTORY):
                    shooter.fire_intent_results.popitem(last=False)
            else:
                self.bot_pending_projectile_launches.discard(
                    (shooter_id, shot_seq))
                self.bot_pending_projectile_metadata.pop(
                    (shooter_id, shot_seq), None)
                self.bot_last_projectile_launch_time_us[shooter_id] = (
                    launch_time_us)
            self._statistics_row(shooter_kind, shooter_id)["shots_fired"] += 1
            self.projectile_revision += 1

            horizontal = math.hypot(velocity[0], velocity[2])
            shot_yaw = math.atan2(velocity[0], velocity[2])
            # Canonical launch events publish physical vector elevation:
            # positive is upward.  Rendered gun pitch uses the opposite sign,
            # but RemoteVehicle explicitly adapts between the two contracts.
            shot_pitch = math.atan2(velocity[1], horizontal)
            event = self._projectile_wire(record)
            event.update({
                "kind": "shot" if shooter_kind == "player" else "bot_shot",
                "maxDistance": max_distance,
                "shot_yaw": round(
                    ((shot_yaw + math.pi) % (2.0 * math.pi)) - math.pi, 6),
                "shot_pitch": round(_clamp(shot_pitch, -math.pi, math.pi), 6),
            })
            event["attacker" if shooter_kind == "player"
                  else "attacker_bot"] = shooter_id
            if fire_intent_seq is not None:
                event["fire_intent_seq"] = fire_intent_seq
                event["fire_input_seq"] = fire_input_seq
            self.pending_events.append(event)
            return True

    def _normalize_projectile_cursor(self, raw, record):
        allowed = {
            "projectile_id", "base_checked_ms", "checked_through_ms",
            "checked_distance", "piercing_loss", "penetration_factor",
            "destructibles",
        }
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise ValueError("invalid cursor shape")
        projectile_id = raw.get("projectile_id")
        if (not isinstance(projectile_id, str) or not projectile_id or
                len(projectile_id) > 96 or
                projectile_id != record["projectile_id"]):
            raise ValueError("invalid projectile id")
        base_checked_ms = _exact_int(raw.get("base_checked_ms"), 0)
        checked_through_ms = _exact_int(
            raw.get("checked_through_ms"), base_checked_ms,
            record["max_time_ms"])
        # A progress message can be queued before a newer canonical snapshot
        # reaches the worker.  Validate the absolute physical envelope here;
        # convergence against the current cursor happens below.  Using the
        # canonical distance/loss as parser lower bounds made a harmless stale
        # retry reject the whole batch before it could be recognised as stale.
        checked_distance = round(_bounded_float(
            raw.get("checked_distance"), 0.0,
            record["max_distance"] + PROJECTILE_TOLERANCE), 6)
        piercing_loss = round(_bounded_float(
            raw.get("piercing_loss"), 0.0, 100000.0), 6)
        penetration_factor = round(_bounded_float(
            raw.get("penetration_factor"), 0.0, 100.0), 6)
        if penetration_factor != record["penetration_factor"]:
            raise ValueError("penetration factor changed")
        destructibles = self._normalize_projectile_destructibles(
            raw.get("destructibles"))
        return {
            "projectile_id": projectile_id,
            "base_checked_ms": base_checked_ms,
            "checked_through_ms": checked_through_ms,
            "checked_distance": checked_distance,
            "piercing_loss": piercing_loss,
            "penetration_factor": penetration_factor,
            "destructibles": destructibles,
        }

    def _validate_retired_projectile_cursor(self, raw, projectile_id):
        """Validate only the envelope of a cursor overtaken by its terminal."""
        allowed = {
            "projectile_id", "base_checked_ms", "checked_through_ms",
            "checked_distance", "piercing_loss", "penetration_factor",
            "destructibles",
        }
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise ValueError("invalid retired cursor shape")
        if raw.get("projectile_id") != projectile_id:
            raise ValueError("invalid retired projectile id")
        # Message-level byte and finite-value checks have already run.  Once
        # the terminal is canonical, the queued cursor's exact time, distance
        # and receipts are obsolete and cannot affect server state.

    @staticmethod
    def _validate_projectile_progress_envelope(message):
        """Validate shape while leaving terminal cursor values obsolete."""
        if (not isinstance(message, dict) or
                set(message) != {
                    "type", "round_id", "authority_epoch", "cursors"} or
                message.get("type") != "projectile_progress"):
            raise ValueError("projectile progress shape is invalid")
        cursors = message.get("cursors")
        if (not isinstance(cursors, list) or not cursors or
                len(cursors) > PROJECTILE_MAX_PROGRESS_BATCH):
            raise ValueError("projectile cursor batch is invalid")
        allowed = {
            "projectile_id", "base_checked_ms", "checked_through_ms",
            "checked_distance", "piercing_loss", "penetration_factor",
            "destructibles",
        }
        seen = set()
        for raw in cursors:
            if not isinstance(raw, dict) or set(raw) != allowed:
                raise ValueError("projectile cursor shape is invalid")
            projectile_id = raw.get("projectile_id")
            if (not isinstance(projectile_id, str) or not projectile_id or
                    len(projectile_id) > 96 or projectile_id in seen):
                raise ValueError("projectile cursor id is invalid")
            seen.add(projectile_id)
            destructibles = raw.get("destructibles")
            if (not isinstance(destructibles, list) or
                    len(destructibles) > PROJECTILE_MAX_DESTRUCTIBLES):
                raise ValueError(
                    "projectile cursor destructible batch is invalid")
        return cursors

    def progress_projectiles(self, player_id, message):
        """Advance an authority-owned batch with cursor compare-and-swap."""
        with self.lock:
            reject_kind = "projectile_progress"
            self._clear_protocol_reject(reject_kind)
            if self.client_build != CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    reject_kind, "build", "unsupported client build")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    reject_kind, "round", "projectile round does not match")
            if not self._projectile_message_fits(message):
                return self._set_protocol_reject(
                    reject_kind, "size", "projectile message exceeds limits")
            if not self._projectile_payload_is_finite(message):
                return self._set_protocol_reject(
                    reject_kind, "finite",
                    "projectile message contains a non-finite value")
            if not self._projectile_authority_matches(player_id, message):
                return self._set_protocol_reject(
                    reject_kind, "authority",
                    "projectile authority does not match")
            try:
                cursors = self._validate_projectile_progress_envelope(message)
            except (TypeError, ValueError, OverflowError) as error:
                return self._set_protocol_reject(
                    reject_kind, "shape", str(error))
            if self.battle_result is not None:
                return True
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    reject_kind, "phase", "combat is not accepting commands")
            proposals = []
            seen = set()
            receipt_count = 0
            try:
                for raw in cursors:
                    projectile_id = raw.get("projectile_id") \
                        if isinstance(raw, dict) else None
                    if projectile_id in seen:
                        raise ValueError("duplicate cursor")
                    seen.add(projectile_id)
                    record = self.projectiles.get(projectile_id)
                    if record is None:
                        tombstone = self.projectile_tombstones.get(
                            projectile_id)
                        if tombstone is not None:
                            self._validate_retired_projectile_cursor(
                                raw, projectile_id)
                            # A terminal may overtake any queued cursor for
                            # the same projectile.  The terminal is canonical;
                            # accepting the stale finite cursor as a no-op
                            # keeps unrelated active cursors in this batch.
                            proposals.append(
                                (None, None, None, None, True))
                            continue
                        raise ValueError("unknown projectile")
                    cursor = self._normalize_projectile_cursor(raw, record)
                    request_fingerprint = _message_fingerprint(raw)
                    receipt_count += len(cursor["destructibles"])
                    if receipt_count > PROJECTILE_MAX_DESTRUCTIBLES:
                        raise ValueError(
                            "too many projectile destructible receipts")
                    # Progress fields are cumulative trusted-worker facts.  A
                    # delayed, duplicated, reordered, post-snapshot, or
                    # skipped-intermediate cursor therefore converges by
                    # taking each monotonic frontier.  The trusted worker is
                    # the only projectile simulation authority; its base is
                    # advisory rather than a reason to poison the whole batch.
                    # Any stale component is a no-op, while newer components
                    # and idempotent destructible receipts are retained.
                    cursor["base_checked_ms"] = int(
                        record["checked_through_ms"])
                    cursor["checked_through_ms"] = max(
                        int(record["checked_through_ms"]),
                        int(cursor["checked_through_ms"]))
                    cursor["checked_distance"] = max(
                        float(record["checked_distance"]),
                        float(cursor["checked_distance"]))
                    cursor["piercing_loss"] = max(
                        float(record["piercing_loss"]),
                        float(cursor["piercing_loss"]))
                    fingerprint = _message_fingerprint(cursor)
                    changed = (
                        cursor["checked_through_ms"] !=
                        record["checked_through_ms"] or
                        cursor["checked_distance"] !=
                        record["checked_distance"] or
                        cursor["piercing_loss"] != record["piercing_loss"])
                    proposals.append((
                        record, cursor, fingerprint,
                        request_fingerprint, changed))
            except (AttributeError, TypeError, ValueError,
                    OverflowError) as error:
                return self._set_protocol_exception(reject_kind, error)

            changed = False
            destructibles = []
            for (record, cursor, fingerprint, request_fingerprint,
                 cursor_changed) in proposals:
                if record is None:
                    continue
                destructibles.extend(cursor["destructibles"])
                if not cursor_changed:
                    continue
                record["checked_through_ms"] = cursor["checked_through_ms"]
                record["checked_distance"] = cursor["checked_distance"]
                record["piercing_loss"] = cursor["piercing_loss"]
                record["last_progress_fingerprint"] = fingerprint
                record["last_progress_request_fingerprint"] = (
                    request_fingerprint)
                changed = True
            self._commit_projectile_destructibles(
                player_id, destructibles)
            if changed:
                self.projectile_revision += 1
            return True

    def _normalize_projectile_effect(
            self, raw, record, impact, splash, allow_stun=False,
            stun_now_ms=None):
        allowed = {
            "target_kind", "target_id", "damage", "shot_result",
            "x", "y", "z", "critical",
            "critical_target_base_revision", "critical_target_ack_seq",
            "hull_damage", "critical_delta", "potential_damage",
            "stun_end_server_time_ms",
            "target_x", "target_y", "target_z",
            "damage_sticker",
        }
        required = {
            "target_kind", "target_id", "damage", "shot_result",
            "x", "y", "z",
        }
        if (not isinstance(raw, dict) or set(raw) - allowed or
                not required.issubset(raw)):
            raise ValueError("invalid effect shape")
        target_kind = raw.get("target_kind")
        if target_kind not in ("player", "bot"):
            raise ValueError("invalid target kind")
        target_id = _exact_int(
            raw.get("target_id"), 1, PROJECTILE_MAX_ID)
        damage = _exact_int(raw.get("damage"), 0, 5000)
        potential_damage = (_exact_int(raw["potential_damage"], 0, 5000)
                            if "potential_damage" in raw else 0)
        shot_result = _exact_int(raw.get("shot_result"), 0, 2)
        pose = _bounded_vector(
            [raw.get("x"), raw.get("y"), raw.get("z")],
            (-5000.0, -1000.0, -5000.0),
            (5000.0, 3000.0, 5000.0))
        target = (self.players.get(target_id) if target_kind == "player"
                  else self.bot_states.get(target_id))
        retired_target = False
        if (target_kind == "player" and
                (target is None or not target.participating)):
            frozen_target = self._frozen_player_participant(target_id)
            if frozen_target is not None:
                retired_target = True
                target_team = int(frozen_target.get("team", 0))
                target_alive = False
            else:
                raise ValueError("unknown target")
        elif target is None:
            raise ValueError("unknown target")
        else:
            target_team = (target.team if target_kind == "player"
                           else int(target.get("team", 0)))
            target_alive = (target.alive if target_kind == "player"
                            else bool(target.get("alive")))
        if splash:
            if not {"target_x", "target_y", "target_z"}.issubset(raw):
                raise ValueError("splash target pose is missing")
            target_pose = _bounded_vector(
                [raw.get("target_x"), raw.get("target_y"),
                 raw.get("target_z")],
                (-5000.0, -1000.0, -5000.0),
                (5000.0, 3000.0, 5000.0))
            # The room-owned worker sampled both poses while resolving the
            # native explosion.  The server keeps their finite wire shape but
            # does not re-derive the gameplay radius from a later frame.
        elif set(raw) & {"target_x", "target_y", "target_z"}:
            raise ValueError("direct effect cannot carry splash target pose")
        elif (target_kind == record["shooter_kind"] and
              target_id == record["shooter_id"]):
            raise ValueError("direct self hit")

        damage_sticker = None
        if "damage_sticker" in raw:
            if splash:
                raise ValueError("splash effect cannot carry damage sticker")
            damage_sticker = _exact_int(
                raw.get("damage_sticker"), 0,
                PROJECTILE_MAX_DAMAGE_STICKER)

        critical = _critical_payload(raw.get("critical"))
        critical_delta = None
        critical_accepted = True
        hull_damage = None
        if critical is not None:
            if not {"critical_target_base_revision",
                    "critical_target_ack_seq", "hull_damage",
                    "critical_delta"}.issubset(raw):
                raise ValueError("critical tokens missing")
            critical_delta = _critical_damage_delta(
                raw.get("critical_delta"))
            if retired_target:
                # Validate the exact terminal shape, but a player who really
                # belonged to this round and has since disconnected no longer
                # has mutable critical CAS state.  The whole terminal remains
                # admissible as a no-op instead of wedging the projectile.
                expected_base = _exact_int(
                    raw.get("critical_target_base_revision"), 0)
                expected_ack = _exact_int(
                    raw.get("critical_target_ack_seq"), 0)
            else:
                expected_base = (
                    target.critical_report_base_revision
                    if target_kind == "player" else
                    int(target.get("combat_base_revision", 0)))
                expected_ack = (
                    target.critical_ack_seq if target_kind == "player" else
                    int(target.get("combat_ack_seq", 0)))
            hull_damage, critical_accepted = _critical_proposal_admission(
                raw, expected_base, expected_ack)
            if retired_target:
                critical_accepted = False
        elif set(raw) & {"critical_target_base_revision",
                         "critical_target_ack_seq", "hull_damage",
                         "critical_delta"}:
            raise ValueError("critical tokens without critical payload")
        stun_end_server_time_ms = 0
        if "stun_end_server_time_ms" in raw:
            if not allow_stun or stun_now_ms is None:
                raise ValueError("stun result needs internal authority")
            stun_end_server_time_ms = _exact_int(
                raw.get("stun_end_server_time_ms"),
                int(stun_now_ms) + 1,
                int(round((PREBATTLE_SECONDS + BATTLE_DURATION_SECONDS) *
                          1000.0)))
        return {
            "target_kind": target_kind, "target_id": target_id,
            "target": target, "target_team": target_team,
            "target_alive": target_alive, "damage": damage,
            "potential_damage": potential_damage,
            "shot_result": shot_result, "pose": pose,
            "critical": critical,
            "critical_delta": critical_delta,
            "critical_accepted": critical_accepted,
            "hull_damage": hull_damage, "splash": bool(splash),
            "retired_target": retired_target,
            "stun_end_server_time_ms": stun_end_server_time_ms,
            "damage_sticker": damage_sticker,
        }

    def ricochet_projectile(self, player_id, message):
        """Commit the first authority-resolved ricochet without retiring it."""
        with self.lock:
            reject_kind = "projectile_ricochet"
            self._clear_protocol_reject(reject_kind)
            if self.client_build != CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    reject_kind, "build", "unsupported client build")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    reject_kind, "round", "projectile round does not match")
            if not self._projectile_message_fits(message):
                return self._set_protocol_reject(
                    reject_kind, "size", "projectile message exceeds limits")
            if not self._projectile_payload_is_finite(message):
                return self._set_protocol_reject(
                    reject_kind, "finite",
                    "projectile message contains a non-finite value")
            if not self._projectile_authority_matches(player_id, message):
                return self._set_protocol_reject(
                    reject_kind, "authority",
                    "projectile authority does not match")
            if self.battle_result is not None:
                return True
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    reject_kind, "phase", "combat is not accepting commands")
            allowed = {
                "type", "round_id", "authority_epoch", "projectile_id",
                "base_checked_ms", "resolved_time_ms", "checked_distance",
                "piercing_loss", "penetration_factor", "impact",
                "segment_origin", "segment_velocity",
                "base_penetration_multiplier", "direct", "destructibles",
            }
            if set(message) != allowed or message.get("type") != (
                    "projectile_ricochet"):
                return self._set_protocol_reject(
                    reject_kind, "shape", "projectile ricochet shape is invalid")
            projectile_id = message.get("projectile_id")
            if (not isinstance(projectile_id, str) or not projectile_id or
                    len(projectile_id) > 96):
                return self._set_protocol_reject(
                    reject_kind, "identity", "projectile id is invalid")
            record = self.projectiles.get(projectile_id)
            if record is None:
                return self._set_protocol_reject(
                    reject_kind, "identity", "projectile is not active")
            request_fingerprint = _message_fingerprint(message)
            if record["ricochet_count"]:
                if record.get(
                        "last_ricochet_fingerprint") == request_fingerprint:
                    return True
                return self._set_protocol_reject(
                    reject_kind, "order", "ricochet retry changed")
            try:
                base_checked_ms = _exact_int(
                    message.get("base_checked_ms"), 0)
                if base_checked_ms != record["checked_through_ms"]:
                    raise ValueError("cursor compare-and-swap failed")
                resolved_time_ms = _exact_int(
                    message.get("resolved_time_ms"), base_checked_ms,
                    record["max_time_ms"])
                checked_distance = round(_bounded_float(
                    message.get("checked_distance"),
                    record["checked_distance"],
                    record["max_distance"] + PROJECTILE_TOLERANCE), 6)
                piercing_loss = round(_bounded_float(
                    message.get("piercing_loss"), record["piercing_loss"],
                    100000.0), 6)
                penetration_factor = round(_bounded_float(
                    message.get("penetration_factor"), 0.0, 100.0), 6)
                if penetration_factor != record["penetration_factor"]:
                    raise ValueError("penetration factor changed")
                impact = _bounded_vector(
                    message.get("impact"),
                    (-5000.0, -1000.0, -5000.0),
                    (5000.0, 3000.0, 5000.0))
                segment_origin = _bounded_vector(
                    message.get("segment_origin"),
                    (-5000.0, -1000.0, -5000.0),
                    (5000.0, 3000.0, 5000.0))
                segment_velocity = _bounded_vector(
                    message.get("segment_velocity"),
                    (-PROJECTILE_MAX_VELOCITY,) * 3,
                    (PROJECTILE_MAX_VELOCITY,) * 3)
                stored_segment_speed = math.sqrt(sum(
                    component * component for component in segment_velocity))
                if (stored_segment_speed <= 0.0 or
                        stored_segment_speed > PROJECTILE_MAX_VELOCITY):
                    raise ValueError("invalid ricochet speed")
                base_penetration_multiplier = _bounded_float(
                    message.get("base_penetration_multiplier"),
                    0.0, 1.0, False)
                raw_direct = message.get("direct")
                direct_fields = {
                    "target_kind", "target_id", "damage", "shot_result",
                    "x", "y", "z"}
                if (not isinstance(raw_direct, dict) or
                        set(raw_direct) not in (
                            direct_fields,
                            direct_fields | {"damage_sticker"})):
                    raise ValueError(
                        "ricochet direct effect has optional terminal fields")
                direct = self._normalize_projectile_effect(
                    raw_direct, record, impact, False)
                if (direct["damage"] != 0 or direct["shot_result"] != 0 or
                        direct["critical"] is not None):
                    raise ValueError("ricochet direct effect must be harmless")
                destructibles = self._normalize_projectile_destructibles(
                    message.get("destructibles"))
            except (TypeError, ValueError, OverflowError) as error:
                return self._set_protocol_exception(reject_kind, error)

            record["checked_through_ms"] = resolved_time_ms
            record["checked_distance"] = checked_distance
            record["piercing_loss"] = piercing_loss
            record["segment_origin"] = segment_origin
            record["segment_velocity"] = segment_velocity
            record["segment_start_time_ms"] = resolved_time_ms
            record["ricochet_count"] = 1
            record["base_penetration_multiplier"] = (
                base_penetration_multiplier)
            record["last_ricochet_fingerprint"] = request_fingerprint
            self._commit_projectile_destructibles(
                player_id, destructibles)
            ricochet_event = self._projectile_wire(record)
            ricochet_event.update({
                "kind": "projectile_ricochet",
                "resolved_time_ms": resolved_time_ms,
                "impact": list(impact),
                "direct": dict(message["direct"]),
            })
            self.pending_events.append(ricochet_event)
            self._apply_projectile_effect(record, direct)
            self.projectile_revision += 1
            return True

    def _apply_projectile_effect(self, record, proposal):
        target_kind = proposal["target_kind"]
        target_id = proposal["target_id"]
        target = proposal["target"]
        was_alive = proposal["target_alive"]
        if proposal.get("retired_target"):
            # The impact was legal when the worker sampled it, but this
            # participant left before the terminal arrived.  Commit the
            # projectile/destructible transaction without inventing a hit,
            # damage, assist, stun or statistic against an absent vehicle.
            return
        critical = proposal["critical"]
        critical_noop = bool(
            target_kind == "player" and critical is not None and was_alive and
            not proposal["critical_delta"]["devices"] and
            not proposal["critical_delta"]["crew_ko"] and
            not proposal["critical_delta"]["ignite"])
        if critical_noop:
            admitted_critical = None
        elif (target_kind == "player" and critical is not None and was_alive and
                not critical_noop):
            admitted_critical = self._merge_player_critical_damage(
                target, critical, proposal["critical_delta"])
        else:
            admitted_critical = (
                critical if proposal["critical_accepted"] and was_alive
                else None)
        event_critical = admitted_critical
        crew_knockout = bool(
            was_alive and _whole_crew_knocked_out(admitted_critical))
        ammo_rack_death = bool(
            was_alive and isinstance(admitted_critical, dict) and
            admitted_critical.get("ammo_rack_death", False))
        damage = (proposal["hull_damage"]
                  if target_kind == "player" and critical is not None
                  else proposal["damage"])
        if (target_kind != "player" and critical is not None and
                not proposal["critical_accepted"]):
            damage = proposal["hull_damage"]
        if not was_alive:
            damage = 0

        if target_kind == "player":
            critical_before = target.critical
            applied = min(damage, target.health)
            target.health -= applied
            if ammo_rack_death and target.health > 0:
                applied += target.health
                target.health = 0
            target.alive = (
                target.health > 0 and not crew_knockout and
                not ammo_rack_death)
            target.display_health = target.health
            if crew_knockout:
                # #1513 uses ATTACK_REASON.SHOT (index 0) for a crew-loss
                # death and preserves the hull's remaining health while
                # isCrewActive becomes false on the client.
                target.death_reason = 0
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical,
                (record["shooter_kind"], record["shooter_id"]))
            health = target.health
            alive = target.alive
        else:
            combat_before = self._bot_combat_signature(target)
            critical_before = combat_before[2]
            applied = min(damage, int(target.get("health", 0)))
            target["health"] = int(target.get("health", 0)) - applied
            if ammo_rack_death and target["health"] > 0:
                applied += target["health"]
                target["health"] = 0
            target["alive"] = (
                target["health"] > 0 and not crew_knockout and
                not ammo_rack_death)
            target["display_health"] = target["health"]
            if crew_knockout:
                target["death_reason"] = 0
            if admitted_critical is not None:
                target["critical"] = _critical_state(admitted_critical)
                before_fire = bool(
                    critical_before and critical_before.get("fire", False))
                after_fire = bool(target["critical"].get("fire", False))
                if not before_fire and after_fire:
                    target["fire_attacker_kind"] = record["shooter_kind"]
                    target["fire_attacker_id"] = record["shooter_id"]
            if was_alive and not target["alive"]:
                terminal = self._apply_bot_terminal_critical(target)
                if terminal is not None:
                    event_critical = dict(terminal)
                    # Full wreck completion is durable state, not a burst of
                    # fresh hit feedback. Preserve only admitted hit events.
                    event_critical["events"] = list(
                        (admitted_critical or {}).get("events") or ())
            self._commit_external_bot_combat(target, combat_before)
            critical_commit = ({
                "combat_revision": target.get("combat_revision", 0),
                "combat_base_revision": target.get(
                    "combat_base_revision", 0),
                "combat_ack_seq": target.get("combat_ack_seq", 0),
            } if event_critical is not None else None)
            health = int(target["health"])
            alive = bool(target["alive"])

        if (applied > 0 or _critical_damage_transition(
                critical_before, admitted_critical)):
            self._drop_capture_for_vehicle(target_kind, target_id)
        if record["shooter_kind"] == "player":
            event_kind = "hit" if target_kind == "player" else "bot_hit"
            attacker_key = "attacker"
        else:
            event_kind = ("bot_human_hit" if target_kind == "player"
                          else "bot_bot_hit")
            attacker_key = "attacker_bot"
        blocked_damage = 0
        if (not proposal["splash"] and was_alive and
                int(record["team"]) != int(proposal["target_team"]) and
                proposal["shot_result"] != 2):
            blocked_damage = max(
                0, proposal["potential_damage"] - damage)
        event = {
            "kind": event_kind,
            attacker_key: record["shooter_id"],
            "target" if target_kind == "player" else "target_bot": target_id,
            "projectile_id": record["projectile_id"],
            "shot_seq": record["shot_seq"],
            "shell_index": record["shell_index"],
            "shot_result": proposal["shot_result"],
            "blocked_damage": blocked_damage,
            "damage": applied, "health": health, "dead": not alive,
            "attack_reason": 0, "death_reason": 0,
            "source": "shot", "splash": proposal["splash"],
            "world_pose": True,
            "x": proposal["pose"][0], "y": proposal["pose"][1],
            "z": proposal["pose"][2],
        }
        if proposal.get("damage_sticker") is not None:
            event["damage_sticker"] = proposal["damage_sticker"]
        if critical is not None:
            event["critical_accepted"] = bool(
                (admitted_critical is not None or critical_noop) and
                was_alive)
            if admitted_critical is not None:
                event["critical"] = event_critical
                if critical_commit:
                    event.update(critical_commit)
            elif not critical_noop:
                event["critical_reject_reason"] = (
                    "target_destroyed" if not was_alive else
                    "stale_target_state")
                if critical_commit:
                    event.update(critical_commit)
        self.pending_events.append(event)
        shooter = (str(record["shooter_kind"]), int(record["shooter_id"]))
        victim = (str(target_kind), int(target_id))
        self._record_damage(
            shooter, victim, applied, critical_before,
            attacker_team=int(record["team"]))
        if _destroyed_tracks(admitted_critical) - _destroyed_tracks(
                critical_before):
            self.track_immobilisers[victim] = shooter
        enemy_hit = (
            int(record["team"]) != int(proposal["target_team"]))
        if enemy_hit and proposal["splash"]:
            self._increment_interaction(
                shooter, victim, "explosion_hits")
        if not proposal["splash"] and enemy_hit:
            self._increment_interaction(
                shooter, victim, "direct_hits")
            row = self._statistics_row(*shooter)
            row["shots_hit"] += 1
            if proposal["shot_result"] == 2:
                row["shots_penetrated"] += 1
                self._increment_interaction(
                    shooter, victim, "piercings")
            elif blocked_damage:
                self._statistics_row(*victim)[
                    "damage_blocked"] += blocked_damage
                self._increment_interaction(
                    victim, shooter, "damage_blocked", blocked_damage)
            if proposal["shot_result"] == 0:
                self._increment_interaction(
                    victim, shooter, "ricochets_received")
            elif proposal["shot_result"] == 1 and applied <= 0:
                self._increment_interaction(
                    victim, shooter,
                    "no_damage_direct_hits_received")
        if was_alive and not alive:
            if target_kind == "player":
                target.death_attacker_kind = record["shooter_kind"]
                target.death_attacker_id = record["shooter_id"]
            else:
                target["death_attacker_kind"] = record["shooter_kind"]
                target["death_attacker_id"] = record["shooter_id"]
            self._record_frag(
                record["shooter_kind"], record["shooter_id"],
                proposal["target_team"], target_kind, target_id,
                attacker_team=int(record["team"]))
            self._clear_vehicle_stun(victim)
        elif proposal["stun_end_server_time_ms"]:
            self._set_canonical_stun(
                shooter, victim, proposal["stun_end_server_time_ms"])

    def resolve_projectile(self, player_id, message):
        """Validate one whole terminal effect batch before applying any HP."""
        with self.lock:
            reject_kind = "projectile_resolve"
            self._clear_protocol_reject(reject_kind)
            if self.client_build != CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    reject_kind, "build", "unsupported client build")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    reject_kind, "round", "projectile round does not match")
            if not self._projectile_message_fits(message):
                return self._set_protocol_reject(
                    reject_kind, "size", "projectile message exceeds limits")
            if not self._projectile_payload_is_finite(message):
                return self._set_protocol_reject(
                    reject_kind, "finite",
                    "projectile message contains a non-finite value")
            if not self._projectile_authority_matches(player_id, message):
                return self._set_protocol_reject(
                    reject_kind, "authority",
                    "projectile authority does not match")
            if self.battle_result is not None:
                return True
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    reject_kind, "phase", "combat is not accepting commands")
            allowed = {
                "type", "round_id", "authority_epoch", "projectile_id",
                "base_checked_ms", "outcome", "resolved_time_ms",
                "checked_distance", "piercing_loss", "penetration_factor",
                "impact", "direct", "splash", "destructibles",
                "hit_vehicle", "wreck_hit",
            }
            required = allowed - {"hit_vehicle", "wreck_hit"}
            if set(message) - allowed or not required.issubset(message):
                return self._set_protocol_reject(
                    reject_kind, "shape", "projectile terminal shape is invalid")
            projectile_id = message.get("projectile_id")
            if (not isinstance(projectile_id, str) or not projectile_id or
                    len(projectile_id) > 96):
                return self._set_protocol_reject(
                    reject_kind, "identity", "projectile id is invalid")
            request_fingerprint = _message_fingerprint(message)
            terminal = self.projectile_tombstones.get(projectile_id)
            if terminal is not None:
                if terminal.get(
                        "request_fingerprint") == request_fingerprint:
                    return True
                return self._set_protocol_reject(
                    reject_kind, "order", "projectile terminal retry changed")
            record = self.projectiles.get(projectile_id)
            if record is None:
                return self._set_protocol_reject(
                    reject_kind, "identity", "projectile is not active")
            try:
                base_checked_ms = _exact_int(
                    message.get("base_checked_ms"), 0)
                if base_checked_ms != record["checked_through_ms"]:
                    raise ValueError("cursor compare-and-swap failed")
                outcome = message.get("outcome")
                if outcome not in ("impact", "miss", "expired"):
                    raise ValueError("invalid outcome")
                resolved_time_ms = _exact_int(
                    message.get("resolved_time_ms"), base_checked_ms,
                    record["max_time_ms"])
                resolution_server_time_ms = self._server_time_ms()
                checked_distance = round(_bounded_float(
                    message.get("checked_distance"),
                    record["checked_distance"],
                    record["max_distance"] + PROJECTILE_TOLERANCE), 6)
                piercing_loss = round(_bounded_float(
                    message.get("piercing_loss"), record["piercing_loss"],
                    100000.0), 6)
                penetration_factor = round(_bounded_float(
                    message.get("penetration_factor"), 0.0, 100.0), 6)
                if penetration_factor != record["penetration_factor"]:
                    raise ValueError("penetration factor changed")
                direct_raw = message.get("direct")
                splash_raw = message.get("splash")
                if not isinstance(splash_raw, list):
                    raise ValueError("splash must be a list")
                if len(splash_raw) > PROJECTILE_MAX_SPLASH_TARGETS:
                    raise ValueError("too many splash targets")
                impact = None
                if outcome == "impact":
                    impact = _bounded_vector(
                        message.get("impact"),
                        (-5000.0, -1000.0, -5000.0),
                        (5000.0, 3000.0, 5000.0))
                else:
                    if (message.get("impact") is not None or
                            direct_raw is not None or splash_raw):
                        raise ValueError("non-impact outcome has effects")
                    impact = None
                if direct_raw is not None and outcome != "impact":
                    raise ValueError("direct effect without impact")
                raw_hit_vehicle = message.get("hit_vehicle")
                if raw_hit_vehicle is None and "hit_vehicle" not in message:
                    hit_vehicle = direct_raw is not None
                elif isinstance(raw_hit_vehicle, bool):
                    hit_vehicle = raw_hit_vehicle
                else:
                    raise ValueError("invalid vehicle impact verdict")
                if outcome != "impact" and hit_vehicle:
                    raise ValueError("non-impact cannot hit a vehicle")
                if direct_raw is not None and not hit_vehicle:
                    raise ValueError("direct effect needs a vehicle impact")
                wreck_hit = None
                if "wreck_hit" in message:
                    wreck_raw = message.get("wreck_hit")
                    if (not isinstance(wreck_raw, dict) or
                            set(wreck_raw) != {"target_kind", "target_id"}):
                        raise ValueError("invalid wreck impact shape")
                    wreck_kind = wreck_raw.get("target_kind")
                    wreck_id = _exact_int(
                        wreck_raw.get("target_id"), 1, PROJECTILE_MAX_ID)
                    if wreck_kind not in ("player", "bot"):
                        raise ValueError("invalid wreck target kind")
                    wreck_target = (
                        self.players.get(wreck_id)
                        if wreck_kind == "player" else
                        self.bot_states.get(wreck_id))
                    retired_wreck = bool(
                        wreck_kind == "player" and
                        (wreck_target is None or
                         not wreck_target.participating) and
                        self._frozen_player_participant(wreck_id) is not None)
                    if wreck_target is None and not retired_wreck:
                        raise ValueError("unknown wreck target")
                    wreck_alive = (False if retired_wreck else
                        wreck_target.alive if wreck_kind == "player" else
                        bool(wreck_target.get("alive")))
                    if wreck_alive:
                        raise ValueError("wreck target is alive")
                    if (wreck_kind == record["shooter_kind"] and
                            wreck_id == record["shooter_id"]):
                        raise ValueError("projectile cannot hit its own wreck")
                    if not retired_wreck:
                        wreck_hit = {
                            "target_kind": wreck_kind,
                            "target_id": wreck_id,
                        }
                if (wreck_hit is not None and
                        (outcome != "impact" or not hit_vehicle or
                         direct_raw is not None)):
                    raise ValueError("wreck impact contract is inconsistent")
                allow_stun = self._trusted_internal_projectile_authority(
                    player_id)
                direct = (self._normalize_projectile_effect(
                    direct_raw, record, impact, False, allow_stun,
                    resolution_server_time_ms)
                          if direct_raw is not None else None)
                splash = [self._normalize_projectile_effect(
                    raw, record, impact, True, allow_stun,
                    resolution_server_time_ms)
                    for raw in splash_raw]
                target_keys = []
                if direct is not None:
                    target_keys.append((direct["target_kind"],
                                        direct["target_id"]))
                target_keys.extend((proposal["target_kind"],
                                    proposal["target_id"])
                                   for proposal in splash)
                if len(target_keys) != len(set(target_keys)):
                    raise ValueError("duplicate direct or splash target")
                destructibles = self._normalize_projectile_destructibles(
                    message.get("destructibles"))
            except (TypeError, ValueError, OverflowError) as error:
                return self._set_protocol_exception(reject_kind, error)

            impact_event = {
                "kind": "projectile_impact",
                "projectile_id": projectile_id,
                "outcome": outcome,
                "resolved_time_ms": resolved_time_ms,
                "checked_distance": checked_distance,
                "piercing_loss": piercing_loss,
                "penetration_factor": penetration_factor,
                "hit_vehicle": hit_vehicle,
                "shooter_kind": record["shooter_kind"],
                "shooter_id": record["shooter_id"],
                "shot_seq": record["shot_seq"],
            }
            if impact is not None:
                impact_event["impact"] = list(impact)
            if wreck_hit is not None:
                impact_event["wreck_hit"] = wreck_hit
            self._commit_projectile_destructibles(
                player_id, destructibles)
            self.pending_events.append(impact_event)
            if direct is not None:
                self._apply_projectile_effect(record, direct)
            for proposal in splash:
                self._apply_projectile_effect(record, proposal)
            self.projectiles.pop(projectile_id, None)
            self.projectile_tombstones[projectile_id] = {
                "projectile_id": projectile_id,
                "outcome": outcome,
                "launch_fingerprint": record["launch_fingerprint"],
                "request_fingerprint": request_fingerprint,
                "last_progress_request_fingerprint": record.get(
                    "last_progress_request_fingerprint"),
            }
            self.projectile_revision += 1
            self._maybe_finish_battle()
            return True

    def _expire_projectiles(self):
        if self.client_build != CLIENT_BUILD_0922:
            return 0
        if (self.bot_authority_id == SIMULATION_WORKER_AUTHORITY_ID and
                self.simulation_worker is not None and
                self.simulation_worker.connected):
            # The hidden native worker owns these terminals.  Server wall
            # time must not overtake a collision result queued on its render
            # thread; worker loss already terminates the active round.
            return 0
        now_ms = self._server_time_ms()
        expired = []
        for projectile_id, record in self.projectiles.items():
            if now_ms >= (record["launch_server_time_ms"] +
                          record["max_time_ms"]):
                expired.append((projectile_id, record))
        for projectile_id, record in expired:
            self.projectiles.pop(projectile_id, None)
            self.projectile_tombstones[projectile_id] = {
                "projectile_id": projectile_id,
                "outcome": "expired",
                "launch_fingerprint": record["launch_fingerprint"],
                "request_fingerprint": None,
                "last_progress_request_fingerprint": record.get(
                    "last_progress_request_fingerprint"),
            }
            self.pending_events.append({
                "kind": "projectile_impact",
                "projectile_id": projectile_id,
                "outcome": "expired",
                "resolved_time_ms": record["max_time_ms"],
                "checked_distance": record["checked_distance"],
                "piercing_loss": record["piercing_loss"],
                "penetration_factor": record["penetration_factor"],
                "hit_vehicle": False,
                "shooter_kind": record["shooter_kind"],
                "shooter_id": record["shooter_id"],
                "shot_seq": record["shot_seq"],
            })
        if expired:
            self.projectile_revision += 1
        return len(expired)

    def _prune_orphaned_bot_launch_edges(self):
        if not self.bot_pending_projectile_launches:
            return
        keep = set()
        for bot_id, shot_seq in self.bot_pending_projectile_launches:
            state = self.bot_states.get(bot_id)
            edge = self.bot_pending_projectile_metadata.get(
                (bot_id, shot_seq))
            if (state is not None and state.get("alive") and edge is not None and
                    int(edge.get("burst_group_seq", -1)) ==
                    int(state.get("burst_group_seq", -2)) and
                    int(edge.get("burst_count", -1)) ==
                    int(state.get("burst_count", -2)) and
                    int(edge.get("burst_index", -1)) <
                    int(state.get("burst_next_index", 0)) and
                    int(edge.get("shell_index", -1)) ==
                    int(state.get("burst_shell_index", -2))):
                keep.add((bot_id, shot_seq))
            elif (edge is None and state is not None and
                  state.get("alive") and
                  int(state.get("fire_seq", 0)) == int(shot_seq)):
                keep.add((bot_id, shot_seq))
        self.bot_pending_projectile_launches = keep
        self.bot_pending_projectile_metadata = dict(
            (key, value) for key, value in
            self.bot_pending_projectile_metadata.items() if key in keep)

    def report_bot_hit(self, player_id, message):
        """Apply a human or authority-owned bot shot to a bot HP record."""
        with self.lock:
            self._clear_protocol_reject("bot_hit")
            if self.client_build == CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    "bot_hit", "legacy_projectile_path",
                    "#1513 requires projectile_resolve")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    "bot_hit", "round",
                    "round=%s server_round=%s" % (
                        message.get("round_id") if isinstance(message, dict)
                        else None, self.round_id))
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    "bot_hit", "combat_closed",
                    "phase=%s tick=%s" % (self.phase, self.tick))
            if self.battle_result is not None:
                return self._set_protocol_reject(
                    "bot_hit", "battle_finished", "battle_result=set")
            if not all(key in message for key in
                       ("target", "shot_seq", "damage")):
                return self._set_protocol_reject(
                    "bot_hit", "message_shape",
                    "required=target,shot_seq,damage")
            if (not _has_finite_fields(
                    message, ("target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return self._set_protocol_reject(
                    "bot_hit", "message_values",
                    "target=%s seq=%s damage=%s" % (
                        message.get("target"), message.get("shot_seq"),
                        message.get("damage")))
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError as error:
                return self._set_protocol_reject(
                    "bot_hit", "critical_payload", "reason=%s" % error)
            try:
                shot_seq = int(message.get("shot_seq", 0))
                bot_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return self._set_protocol_reject(
                    "bot_hit", "identity",
                    "target=%s seq=%s" % (
                        message.get("target"), message.get("shot_seq")))
            state = self.bot_states.get(bot_id)
            if state is None or not state.get("alive"):
                return self._set_protocol_reject(
                    "bot_hit", "target_unavailable",
                    "target=%s known=%s alive=%s" % (
                        bot_id, state is not None,
                        bool(state and state.get("alive"))))
            combat_before = self._bot_combat_signature(state)
            attacker_bot_value = message.get("attacker_bot")
            if attacker_bot_value is not None:
                if player_id != self.bot_authority_id:
                    return self._set_protocol_reject(
                        "bot_hit", "authority",
                        "sender=%s authority=%s" % (
                            player_id, self.bot_authority_id))
                if player_id != self.bot_manifest_authority_id:
                    return self._set_protocol_reject(
                        "bot_hit", "manifest_authority",
                        "sender=%s manifest_authority=%s" % (
                            player_id, self.bot_manifest_authority_id))
                try:
                    attacker_bot_id = int(attacker_bot_value)
                except (TypeError, ValueError):
                    return self._set_protocol_reject(
                        "bot_hit", "attacker_id",
                        "attacker_bot=%s" % attacker_bot_value)
                attacker_bot = self.bot_states.get(attacker_bot_id)
                splash = bool(message.get("splash", False))
                hit_key = (("bot_shot", attacker_bot_id, shot_seq,
                            "bot", bot_id) if splash else
                           ("bot_shot", attacker_bot_id, shot_seq))
                if attacker_bot is None or not attacker_bot.get("alive"):
                    return self._set_protocol_reject(
                        "bot_hit", "attacker_unavailable",
                        "attacker_bot=%s known=%s alive=%s" % (
                            attacker_bot_id, attacker_bot is not None,
                            bool(attacker_bot and attacker_bot.get("alive"))))
                if attacker_bot_id == bot_id and not splash:
                    return self._set_protocol_reject(
                        "bot_hit", "self_hit",
                        "attacker_bot=%s target=%s splash=false" % (
                            attacker_bot_id, bot_id))
                server_fire_seq = int(attacker_bot.get("fire_seq", 0))
                if shot_seq <= 0 or shot_seq > server_fire_seq:
                    return self._set_protocol_reject(
                        "bot_hit", "shot_lineage",
                        ("attacker_bot=%s target=%s client_fire=%s "
                         "server_fire=%s client_target_base=%s "
                         "server_target_base=%s client_target_ack=%s "
                         "server_target_ack=%s") % (
                            attacker_bot_id, bot_id, shot_seq,
                            server_fire_seq,
                            message.get("critical_target_base_revision"),
                            state.get("combat_base_revision"),
                            message.get("critical_target_ack_seq"),
                            state.get("combat_ack_seq")))
                if hit_key in self.bot_reported_hits:
                    return self._set_protocol_reject(
                        "bot_hit", "duplicate",
                        "attacker_bot=%s target=%s seq=%s splash=%s" % (
                            attacker_bot_id, bot_id, shot_seq, splash))
                distance = math.hypot(
                    state["x"] - attacker_bot["x"],
                    state["z"] - attacker_bot["z"])
                if distance > 5000.0:
                    return self._set_protocol_reject(
                        "bot_hit", "distance",
                        "attacker_bot=%s target=%s distance=%.3f" % (
                            attacker_bot_id, bot_id, distance))
                reported_hits = self.bot_reported_hits
                attacker_id = attacker_bot_id
                shell_index = attacker_bot.get("shell_index", 0)
                event_kind = "bot_bot_hit"
            else:
                attacker = self.players.get(player_id)
                splash = bool(message.get("splash", False))
                hit_key = (("shot", shot_seq, "bot", bot_id)
                           if splash else ("shot", shot_seq))
                if attacker is None or not attacker.alive:
                    return self._set_protocol_reject(
                        "bot_hit", "attacker_unavailable",
                        "attacker=%s known=%s alive=%s" % (
                            player_id, attacker is not None,
                            bool(attacker and attacker.alive)))
                if shot_seq <= 0 or shot_seq > attacker.fire_seq:
                    return self._set_protocol_reject(
                        "bot_hit", "shot_lineage",
                        ("attacker=%s target=%s client_fire=%s "
                         "server_fire=%s client_target_base=%s "
                         "server_target_base=%s client_target_ack=%s "
                         "server_target_ack=%s") % (
                            player_id, bot_id, shot_seq, attacker.fire_seq,
                            message.get("critical_target_base_revision"),
                            state.get("combat_base_revision"),
                            message.get("critical_target_ack_seq"),
                            state.get("combat_ack_seq")))
                if hit_key in attacker.reported_hits:
                    return self._set_protocol_reject(
                        "bot_hit", "duplicate",
                        "attacker=%s target=%s seq=%s splash=%s" % (
                            player_id, bot_id, shot_seq, splash))
                reported_hits = attacker.reported_hits
                attacker_id = player_id
                shell_index = attacker.shell_index
                event_kind = "bot_hit"
            modern_proposal = (
                self.client_build == CLIENT_BUILD_0922 and
                critical is not None)
            critical_accepted = True
            hull_damage = None
            if modern_proposal:
                try:
                    hull_damage, critical_accepted = (
                        _critical_proposal_admission(
                            message, state.get("combat_base_revision"),
                            state.get("combat_ack_seq")))
                except ValueError as error:
                    return self._set_protocol_reject(
                        "bot_hit", "critical_contract",
                        ("target=%s client_base=%s server_base=%s "
                         "client_ack=%s server_ack=%s reason=%s") % (
                            bot_id,
                            message.get("critical_target_base_revision"),
                            state.get("combat_base_revision"),
                            message.get("critical_target_ack_seq"),
                            state.get("combat_ack_seq"), error))
            reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            if modern_proposal and not critical_accepted:
                damage = hull_damage
            applied = min(damage, int(state.get("health", 0)))
            state["health"] -= applied
            state["alive"] = state["health"] > 0
            state["display_health"] = state["health"]
            admitted_critical = (
                critical if not modern_proposal or critical_accepted else None)
            if admitted_critical is not None:
                state["critical"] = _critical_state(admitted_critical)
                before_fire = bool(
                    combat_before[2] and combat_before[2].get("fire", False))
                after_fire = bool(state["critical"].get("fire", False))
                if not before_fire and after_fire:
                    state["fire_attacker_kind"] = (
                        "bot" if event_kind == "bot_bot_hit" else "player")
                    state["fire_attacker_id"] = int(attacker_id)
            self._commit_external_bot_combat(state, combat_before)
            capture_reset = bool(
                applied > 0 or _critical_damage_transition(
                    combat_before[2], admitted_critical))
            if capture_reset:
                self._drop_capture_for_vehicle("bot", bot_id)
            event = {
                "kind": event_kind,
                "attacker_bot" if event_kind == "bot_bot_hit" else "attacker": attacker_id,
                "target_bot": bot_id,
                "shot_seq": shot_seq, "shell_index": shell_index,
                "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": state["health"], "dead": not state["alive"],
                "attack_reason": 0, "death_reason": 0,
                "source": "shot",
                "splash": splash,
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), state["x"]), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), state["y"] + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), state["z"]), -2000.0, 2000.0), 4),
            }
            if critical is not None:
                if modern_proposal:
                    event["critical_accepted"] = critical_accepted
                if admitted_critical is not None:
                    event["critical"] = admitted_critical
                elif modern_proposal:
                    event["critical_reject_reason"] = (
                        "stale_target_state")
                if modern_proposal:
                    event.update({
                        "combat_revision": state["combat_revision"],
                        "combat_base_revision":
                            state["combat_base_revision"],
                        "combat_ack_seq": state["combat_ack_seq"],
                    })
            self.pending_events.append(event)
            if not state["alive"]:
                state["death_attacker_kind"] = (
                    "bot" if event_kind == "bot_bot_hit" else "player")
                state["death_attacker_id"] = int(attacker_id)
                self._record_frag(
                    "bot" if event_kind == "bot_bot_hit" else "player",
                    attacker_id, int(state.get("team", 0)),
                    "bot", bot_id)
            self._maybe_finish_battle()
            return True

    def report_bot_human_hit(self, player_id, message):
        """Apply an authority-resolved bot shot against shared human HP."""
        with self.lock:
            self._clear_protocol_reject("bot_human_hit")
            if self.client_build == CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    "bot_human_hit", "legacy_projectile_path",
                    "#1513 requires projectile_resolve")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    "bot_human_hit", "round",
                    "round=%s server_round=%s" % (
                        message.get("round_id") if isinstance(message, dict)
                        else None, self.round_id))
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    "bot_human_hit", "combat_closed",
                    "phase=%s tick=%s" % (self.phase, self.tick))
            if self.battle_result is not None:
                return self._set_protocol_reject(
                    "bot_human_hit", "battle_finished",
                    "battle_result=set")
            if player_id != self.bot_authority_id:
                return self._set_protocol_reject(
                    "bot_human_hit", "authority",
                    "sender=%s authority=%s" % (
                        player_id, self.bot_authority_id))
            if player_id != self.bot_manifest_authority_id:
                return self._set_protocol_reject(
                    "bot_human_hit", "manifest_authority",
                    "sender=%s manifest_authority=%s" % (
                        player_id, self.bot_manifest_authority_id))
            if not all(key in message for key in
                       ("attacker_bot", "target", "shot_seq", "damage")):
                return self._set_protocol_reject(
                    "bot_human_hit", "message_shape",
                    "required=attacker_bot,target,shot_seq,damage")
            if (not _has_finite_fields(
                    message, ("attacker_bot", "target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return self._set_protocol_reject(
                    "bot_human_hit", "message_values",
                    "attacker_bot=%s target=%s seq=%s damage=%s" % (
                        message.get("attacker_bot"), message.get("target"),
                        message.get("shot_seq"), message.get("damage")))
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError as error:
                return self._set_protocol_reject(
                    "bot_human_hit", "critical_payload",
                    "reason=%s" % error)
            try:
                bot_id = int(message.get("attacker_bot", 0))
                target_id = int(message.get("target", 0))
                shot_seq = int(message.get("shot_seq", 0))
            except (TypeError, ValueError):
                return self._set_protocol_reject(
                    "bot_human_hit", "identity",
                    "attacker_bot=%s target=%s seq=%s" % (
                        message.get("attacker_bot"), message.get("target"),
                        message.get("shot_seq")))
            bot = self.bot_states.get(bot_id)
            target = self.players.get(target_id)
            if bot is None or not bot.get("alive") or target is None or not target.alive:
                return self._set_protocol_reject(
                    "bot_human_hit", "vehicle_unavailable",
                    ("attacker_bot=%s known=%s alive=%s target=%s "
                     "known=%s alive=%s") % (
                        bot_id, bot is not None,
                        bool(bot and bot.get("alive")), target_id,
                        target is not None,
                        bool(target and target.alive)))
            try:
                bot_fire_seq = int(bot.get("fire_seq", 0))
            except (TypeError, ValueError):
                bot_fire_seq = 0
            splash = bool(message.get("splash", False))
            hit_key = (("bot_shot", bot_id, shot_seq,
                        "player", target_id) if splash else
                       ("bot_shot", bot_id, shot_seq))
            if (shot_seq <= 0 or shot_seq > bot_fire_seq or
                    hit_key in self.bot_reported_hits):
                code = ("duplicate" if hit_key in self.bot_reported_hits
                        else "shot_lineage")
                return self._set_protocol_reject(
                    "bot_human_hit", code,
                    ("attacker_bot=%s target=%s client_fire=%s "
                     "server_fire=%s client_target_base=%s "
                     "server_target_base=%s client_target_ack=%s "
                     "server_target_ack=%s duplicate=%s") % (
                        bot_id, target_id, shot_seq, bot_fire_seq,
                        message.get("critical_target_base_revision"),
                        target.critical_report_base_revision,
                        message.get("critical_target_ack_seq"),
                        target.critical_ack_seq,
                        hit_key in self.bot_reported_hits))
            modern_proposal = (
                self.client_build == CLIENT_BUILD_0922 and
                critical is not None)
            critical_accepted = True
            hull_damage = None
            if modern_proposal:
                try:
                    hull_damage, critical_accepted = (
                        _critical_proposal_admission(
                            message,
                            target.critical_report_base_revision,
                            target.critical_ack_seq))
                except ValueError as error:
                    return self._set_protocol_reject(
                        "bot_human_hit", "critical_contract",
                        ("target=%s client_base=%s server_base=%s "
                         "client_ack=%s server_ack=%s reason=%s") % (
                            target_id,
                            message.get("critical_target_base_revision"),
                            target.critical_report_base_revision,
                            message.get("critical_target_ack_seq"),
                            target.critical_ack_seq, error))
            self.bot_reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            if modern_proposal and not critical_accepted:
                damage = hull_damage
            applied = min(damage, target.health)
            target.health -= applied
            target.alive = target.health > 0
            target.display_health = target.health
            admitted_critical = (
                critical if not modern_proposal or critical_accepted else None)
            critical_before = target.critical
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical, ("bot", bot_id))
            capture_reset = bool(
                applied > 0 or _critical_damage_transition(
                    critical_before, admitted_critical))
            if capture_reset:
                self._drop_capture_for_vehicle("player", target_id)
            event = {
                "kind": "bot_human_hit", "attacker_bot": bot_id, "target": target_id,
                "shot_seq": shot_seq,
                "shell_index": max(0, min(int(_finite_float(
                    bot.get("shell_index"), 0)), 9)),
                "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": target.health, "dead": not target.alive,
                "attack_reason": 0, "death_reason": 0,
                "source": "shot",
                "splash": splash,
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            }
            if critical is not None:
                if modern_proposal:
                    event["critical_accepted"] = critical_accepted
                if admitted_critical is not None:
                    event["critical"] = admitted_critical
                    event.update(critical_commit)
                elif modern_proposal:
                    event["critical_reject_reason"] = (
                        "stale_target_state")
                    event.update({
                        "critical_revision": target.critical_revision,
                        "critical_base_revision":
                            target.critical_report_base_revision,
                        "critical_ack_seq": target.critical_ack_seq,
                    })
            self.pending_events.append(event)
            if not target.alive:
                target.death_attacker_kind = "bot"
                target.death_attacker_id = int(bot_id)
                self._record_frag(
                    "bot", bot_id, target.team,
                    "player", target.player_id)
            self._maybe_finish_battle()
            return True

    @staticmethod
    def _advance_player_ram_resolved(player):
        """Advance only across contiguous terminal receipt decisions."""
        resolved = int(player.ram_contact_resolved_seq)
        admitted = int(player.ram_contact_seq)
        while resolved < admitted:
            candidate = resolved + 1
            if candidate in player.ram_contacts:
                break
            resolved = candidate
        player.ram_contact_resolved_seq = resolved

    @classmethod
    def _consume_player_ram_contact(cls, player, contact_seq):
        player.ram_contacts.pop(contact_seq, None)
        cls._advance_player_ram_resolved(player)
        if int(player.ram_contact.get("seq", 0) or 0) == contact_seq:
            player.ram_contact = (dict(next(reversed(
                player.ram_contacts.values())))
                if player.ram_contacts else {})

    @classmethod
    def _reject_player_ram_contact(cls, player, contact_seq, reason):
        """Record one fail-closed terminal input decision."""
        player.ram_contact_seq = int(contact_seq)
        player.ram_contact_rejections[int(contact_seq)] = str(reason)
        _server_log(
            "RAM CONTACT rejected player=%d seq=%d reason=%s" % (
                int(player.player_id), int(contact_seq), str(reason)))
        while len(player.ram_contact_rejections) > MAX_PENDING_RAM_CONTACTS:
            player.ram_contact_rejections.popitem(last=False)
        cls._advance_player_ram_resolved(player)

    def report_bot_ram(self, player_id, message):
        """Apply one receipt-owned authority tank collision atomically."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    player_id != self.bot_authority_id or
                    player_id != self.bot_manifest_authority_id):
                return False
            required = ("bot_id", "target_kind", "target_id", "ram_seq",
                        "damage_to_bot", "damage_to_target")
            if (not all(key in message for key in required) or
                    not _has_finite_fields(message, (
                        "bot_id", "target_id", "ram_seq",
                        "damage_to_bot", "damage_to_target"))):
                return False
            try:
                bot_id = int(message["bot_id"])
                target_id = int(message["target_id"])
                ram_seq = int(message["ram_seq"])
                target_kind = str(message["target_kind"])
            except (TypeError, ValueError):
                return False
            if target_kind not in ("bot", "human") or ram_seq <= 0:
                return False
            damage_to_bot = max(0, int(_finite_float(
                message["damage_to_bot"])))
            damage_to_target = max(0, int(_finite_float(
                message["damage_to_target"])))
            has_contact_player = "ram_contact_player_id" in message
            has_contact_seq = "ram_contact_seq" in message
            if has_contact_player != has_contact_seq:
                return False
            contact_player_id = None
            contact_seq = None
            if has_contact_player:
                try:
                    contact_player_id = _exact_int(
                        message.get("ram_contact_player_id"), 1,
                        PROJECTILE_MAX_ID)
                    contact_seq = _exact_int(
                        message.get("ram_contact_seq"), 1, 2147483647)
                except (TypeError, ValueError, OverflowError):
                    return False
            key = (("contact", contact_player_id, contact_seq)
                   if has_contact_player else
                   ("authority", self.authority_epoch, player_id, ram_seq))
            fingerprint = (
                bot_id, target_kind, target_id, damage_to_bot,
                damage_to_target, contact_player_id, contact_seq)
            previous_fingerprint = (
                self.bot_reported_ram_fingerprints.get(key))
            if previous_fingerprint is not None:
                # Exact retransmission is an idempotent success. Reusing one
                # operation identity for different damage is a conflict.
                return previous_fingerprint == fingerprint
            bot = self.bot_states.get(bot_id)
            target = (self.bot_states.get(target_id) if target_kind == "bot"
                      else self.players.get(target_id))
            if (bot is None or target is None or
                    (target_kind == "bot" and target_id == bot_id)):
                return False
            authority = (self.simulation_worker if
                         player_id == SIMULATION_WORKER_AUTHORITY_ID else
                         self.players.get(player_id))
            requires_contact = bool(
                target_kind == "human" and
                self.client_build == CLIENT_BUILD_0922)
            if requires_contact and not has_contact_player:
                # New authorities may physically separate a currently visible
                # human, but HP is owned only by the immutable player receipt.
                # This prevents a delayed receipt replaying a direct report.
                return False
            ram_contact = None
            if has_contact_player:
                if (target_kind != "human" or
                        contact_player_id != target_id or
                        contact_seq not in target.ram_contacts or
                        contact_seq != next(iter(target.ram_contacts), None) or
                        int(target.ram_contacts[contact_seq].get(
                            "bot_id", 0)) != bot_id):
                    return False
                ram_contact = target.ram_contacts[contact_seq]
            else:
                if (not bot.get("alive") or
                        not (target.get("alive") if target_kind == "bot"
                             else target.alive)):
                    return False
                target_x = (target.get("x") if target_kind == "bot"
                            else target.x)
                target_z = (target.get("z") if target_kind == "bot"
                            else target.z)
                if math.hypot(float(bot["x"]) - float(target_x),
                              float(bot["z"]) - float(target_z)) > 12.5:
                    return False
            if ram_contact is not None and (
                    not bot.get("alive") or not target.alive):
                # The contact was valid when admitted but combat advanced
                # before its delayed proof was resolved. It is terminal and
                # must not survive forever or damage an already dead tank.
                self.bot_reported_rams.add(key)
                self.bot_reported_ram_fingerprints[key] = fingerprint
                self._consume_player_ram_contact(target, contact_seq)
                return True
            bot_team = int(bot.get("team", 0))
            target_team = (int(target.get("team", 0))
                           if target_kind == "bot" else int(target.team))
            if bot_team in (1, 2) and bot_team == target_team:
                # Friendly hulls still collide in the vehicle physics paths,
                # but team contact is never an HP-producing operation.  Treat
                # an otherwise valid report as a terminal no-op so an older
                # client cannot strand a receipt or retry it forever.
                self.bot_reported_rams.add(key)
                self.bot_reported_ram_fingerprints[key] = fingerprint
                if ram_contact is not None:
                    self._consume_player_ram_contact(target, contact_seq)
                return True
            if (damage_to_bot <= 0 and damage_to_target <= 0 and
                    ram_contact is None):
                return False
            self.bot_reported_rams.add(key)
            self.bot_reported_ram_fingerprints[key] = fingerprint
            if ram_contact is not None:
                # Consume the proof only after its operation identity and
                # immutable fingerprint have reached a terminal result.
                self._consume_player_ram_contact(target, contact_seq)
            if damage_to_bot <= 0 and damage_to_target <= 0:
                return True
            reason = 2

            bot_combat_before = self._bot_combat_signature(bot)
            applied_bot = min(damage_to_bot, int(bot.get("health", 0)))
            bot["health"] -= applied_bot
            bot["alive"] = bot["health"] > 0
            bot["display_health"] = bot["health"]
            bot["death_reason"] = reason if not bot["alive"] else 0
            bot_terminal = None
            if not bot["alive"]:
                bot_terminal = self._apply_bot_terminal_critical(bot)
            self._commit_external_bot_combat(bot, bot_combat_before)
            if applied_bot > 0:
                self._drop_capture_for_vehicle("bot", bot_id)
            bot_event = {
                "kind": ("bot_bot_hit" if target_kind == "bot"
                         else "bot_hit"),
                "target_bot": bot_id, "damage": applied_bot,
                "health": bot["health"], "dead": not bot["alive"],
                "attack_reason": reason,
                "death_reason": bot["death_reason"], "source": "ram",
            }
            if bot_terminal is not None:
                bot_event.update({
                    "critical": bot_terminal,
                    "combat_revision": bot["combat_revision"],
                    "combat_base_revision": bot["combat_base_revision"],
                    "combat_ack_seq": bot["combat_ack_seq"],
                })
            if target_kind == "bot":
                bot_event["attacker_bot"] = target_id
            else:
                bot_event["attacker"] = target_id
            self.pending_events.append(bot_event)
            self._record_damage(
                ("bot" if target_kind == "bot" else "player", target_id),
                ("bot", bot_id), applied_bot, bot_combat_before[2])

            if target_kind == "bot":
                target_combat_before = self._bot_combat_signature(target)
                target_critical_before = target_combat_before[2]
                applied_target = min(
                    damage_to_target, int(target.get("health", 0)))
                target["health"] -= applied_target
                target["alive"] = target["health"] > 0
                target["display_health"] = target["health"]
                target["death_reason"] = reason if not target["alive"] else 0
                target_terminal = None
                if not target["alive"]:
                    target_terminal = self._apply_bot_terminal_critical(
                        target)
                self._commit_external_bot_combat(
                    target, target_combat_before)
                if applied_target > 0:
                    self._drop_capture_for_vehicle("bot", target_id)
                target_event = {
                    "kind": "bot_bot_hit", "attacker_bot": bot_id,
                    "target_bot": target_id, "damage": applied_target,
                    "health": target["health"],
                    "dead": not target["alive"],
                    "attack_reason": reason,
                    "death_reason": target["death_reason"], "source": "ram",
                }
                if target_terminal is not None:
                    target_event.update({
                        "critical": target_terminal,
                        "combat_revision": target["combat_revision"],
                        "combat_base_revision":
                            target["combat_base_revision"],
                        "combat_ack_seq": target["combat_ack_seq"],
                    })
                target_team = int(target.get("team", 0))
            else:
                target_critical_before = target.critical
                applied_target = min(damage_to_target, target.health)
                target.health -= applied_target
                target.alive = target.health > 0
                target.display_health = target.health
                target.death_reason = reason if not target.alive else 0
                if applied_target > 0:
                    self._drop_capture_for_vehicle("player", target_id)
                target_event = {
                    "kind": "bot_human_hit", "attacker_bot": bot_id,
                    "target": target_id, "damage": applied_target,
                    "health": target.health, "dead": not target.alive,
                    "attack_reason": reason,
                    "death_reason": target.death_reason, "source": "ram",
                }
                target_team = target.team
            self.pending_events.append(target_event)
            self._record_damage(
                ("bot", bot_id),
                ("bot" if target_kind == "bot" else "player", target_id),
                applied_target, target_critical_before)

            if not bot["alive"]:
                bot["death_attacker_kind"] = (
                    "bot" if target_kind == "bot" else "player")
                bot["death_attacker_id"] = target_id
                self._record_frag(
                    bot["death_attacker_kind"], target_id,
                    int(bot.get("team", 0)), "bot", bot_id)
            if not (target.get("alive") if target_kind == "bot"
                    else target.alive):
                if target_kind == "bot":
                    target["death_attacker_kind"] = "bot"
                    target["death_attacker_id"] = bot_id
                else:
                    target.death_attacker_kind = "bot"
                    target.death_attacker_id = bot_id
                self._record_frag(
                    "bot", bot_id, target_team,
                    "bot" if target_kind == "bot" else "player",
                    target_id)
            self._maybe_finish_battle()
            return True

    def update_rules(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    player_id != self.bot_authority_id or
                    self.battle_result is not None):
                return False
            bases = {}
            rules = message.get("rules") or {}
            if not isinstance(rules, dict):
                return False
            incoming = rules.get("bases") or {}
            if not isinstance(incoming, dict):
                return False
            for team in (1, 2):
                raw = incoming.get(str(team), incoming.get(team, {})) or {}
                if not isinstance(raw, dict):
                    raw = {}
                bases[str(team)] = {
                    "points": max(0, min(int(_finite_float(raw.get("points"), 0)), 100)),
                    "time_left": max(
                        0.0, _finite_float(raw.get("time_left"), 0.0)),
                    "invaders": max(
                        0, min(int(_finite_float(raw.get("invaders"), 0)), 30)),
                    "stopped": bool(raw.get("stopped", False)),
                }
            self.rules_state = {"bases": bases}
            return True

    def report_battle_result(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    player_id != self.bot_authority_id or
                    self.battle_result is not None):
                return False
            if "winner" not in message or "reason" not in message:
                return False
            try:
                winner = int(message.get("winner"))
                base_team = int(message.get("base_team", 0))
            except (TypeError, ValueError):
                return False
            reason = _safe_name(message.get("reason"), "")
            if winner not in (0, 1, 2) or base_team not in (0, 1, 2) or not reason:
                return False
            return self._finish_battle(
                winner, reason, base_team)

    @staticmethod
    def _receipt_statistics(row):
        """Project only statistics the battle server actually records."""
        return {
            "shots": max(0, int(row.get("shots_fired", 0))),
            "direct_hits": max(0, int(row.get("shots_hit", 0))),
            "piercings": max(0, int(row.get("shots_penetrated", 0))),
            "damage": max(0, int(row.get("damage_dealt", 0))),
            "damage_received": max(0, int(row.get("damage_received", 0))),
            "damage_blocked": max(0, int(row.get("damage_blocked", 0))),
            "assist_track": max(0, int(
                row.get("damage_assisted_track", 0))),
            "assist_radio": max(0, int(
                row.get("damage_assisted_radio", 0))),
            "assist_stun": max(0, int(
                row.get("damage_assisted_stun", 0))),
            "kills": max(0, int(row.get("kills", 0))),
            "spotted": max(0, int(row.get("spotted", 0))),
            "capture_points": max(0, int(
                row.get("capture_points", 0))),
            "dropped_capture_points": max(0, int(
                row.get("dropped_capture_points", 0))),
        }

    def _result_vehicle_tier(self, vehicle, preferred_player_id=None):
        catalog_ids = []
        if preferred_player_id is not None:
            catalog_ids.append(int(preferred_player_id))
        catalog_ids.extend(sorted(
            player_id for player_id in self.vehicle_catalogs
            if player_id not in catalog_ids))
        for player_id in catalog_ids:
            for entry in self.vehicle_catalogs.get(player_id, ()):
                if entry.get("name") == vehicle:
                    return max(1, min(10, int(entry.get("level", 1))))
        return 1

    def _public_result_roster(self, winner, participants):
        """Freeze complete human and bot team rows from authoritative state."""
        rows = []
        for participant in sorted(
                participants, key=lambda value: int(value["player_id"])):
            player_id = int(participant["player_id"])
            live = self.players.get(player_id)
            alive = bool(live.alive if live is not None else
                         participant.get("alive", True))
            statistics = self._receipt_statistics(
                self._statistics_row("player", player_id))
            tier = participant.get("vehicle_tier")
            if tier is None:
                tier = self._result_vehicle_tier(
                    participant["vehicle"], player_id)
            xp = compute_offline_rewards(
                {
                    "damage_dealt": statistics["damage"],
                    "damage_assisted_track": statistics["assist_track"],
                    "damage_assisted_radio": statistics["assist_radio"],
                    "damage_assisted_stun": statistics["assist_stun"],
                    "kills": statistics["kills"],
                    "spotted": statistics["spotted"],
                    "capture_points": statistics["capture_points"],
                    "dropped_capture_points":
                        statistics["dropped_capture_points"],
                },
                int(winner) == int(participant["team"]),
                participated=True, vehicle_tier=tier)["xp"]
            rows.append({
                "actor_kind": "player", "actor_id": player_id,
                "name": participant["name"],
                "vehicle": participant["vehicle"],
                "team": int(participant["team"]),
                "health": max(0, int(
                    live.health if live is not None else
                    participant.get("health", 0))),
                "death_reason": (-1 if alive else max(0, int(
                    live.death_reason if live is not None else
                    participant.get("death_reason", 0)))),
                "killer_kind": str(
                    live.death_attacker_kind if live is not None else
                    participant.get("death_attacker_kind", "") or ""),
                "killer_id": max(0, int(
                    live.death_attacker_id if live is not None else
                    participant.get("death_attacker_id", 0) or 0)),
                "is_team_killer": bool(
                    live.team_killer if live is not None else
                    participant.get("team_killer", False)),
                "xp": int(xp), "stats": statistics,
            })

        manifest = {int(entry["id"]): entry for entry in self.bot_manifest}
        for bot_id in sorted(manifest):
            identity = manifest[bot_id]
            state = self.bot_states.get(bot_id, identity)
            alive = bool(state.get("alive", int(state.get("health", 0)) > 0))
            statistics = self._receipt_statistics(
                self._statistics_row("bot", bot_id))
            xp = compute_offline_rewards(
                {
                    "damage_dealt": statistics["damage"],
                    "damage_assisted_track": statistics["assist_track"],
                    "damage_assisted_radio": statistics["assist_radio"],
                    "damage_assisted_stun": statistics["assist_stun"],
                    "kills": statistics["kills"],
                    "spotted": statistics["spotted"],
                    "capture_points": statistics["capture_points"],
                    "dropped_capture_points":
                        statistics["dropped_capture_points"],
                }, int(winner) == int(identity["team"]), participated=True,
                vehicle_tier=self._result_vehicle_tier(
                    identity["vehicle"]))["xp"]
            rows.append({
                "actor_kind": "bot", "actor_id": bot_id,
                "name": identity["name"], "vehicle": identity["vehicle"],
                "team": int(identity["team"]),
                "health": max(0, int(state.get("health", 0))),
                "death_reason": (-1 if alive else max(
                    0, int(state.get("death_reason", 0)))),
                "killer_kind": str(
                    state.get("death_attacker_kind", "") or ""),
                "killer_id": max(0, int(
                    state.get("death_attacker_id", 0) or 0)),
                "is_team_killer": False,
                "xp": int(xp), "stats": statistics,
            })
        return rows

    def _finish_battle(self, winner, reason, base_team=0,
                       record_receipts=True):
        """Durably store and announce a terminal result exactly once."""
        if self.battle_result is not None:
            return False
        winner = max(0, min(int(winner), 2))
        result = {
            "winner": winner,
            "reason": _safe_name(reason, "battle finished"),
            "base_team": max(0, min(int(base_team), 2)),
        }
        next_receipts = OrderedDict(self.result_receipts)
        if self.client_build == CLIENT_BUILD_0922 and record_receipts:
            participants = list(self.round_participants.values())
            if not participants:
                participants = [{
                    "player_id": int(player.player_id),
                    "account_key": player.account_key,
                    "name": player.name,
                    "vehicle": player.vehicle,
                    "vehicle_tier": self._result_vehicle_tier(
                        player.vehicle, player.player_id),
                    "team": int(player.team),
                    "alive": bool(player.alive),
                    "health": int(player.health),
                    "death_reason": int(player.death_reason),
                    "death_attacker_kind": str(
                        player.death_attacker_kind or ""),
                    "death_attacker_id": int(
                        player.death_attacker_id or 0),
                    "team_killer": bool(player.team_killer),
                } for player in self.players.values()]
            public_results = self._public_result_roster(winner, participants)
            public_by_player = dict(
                (row["actor_id"], row) for row in public_results
                if row["actor_kind"] == "player")
            result["vehicle_statistics"] = self._vehicle_statistics_payload()
            arena_unique_id = (
                (((self.receipt_arena_prefix + int(self.round_id)) &
                  0xffffffff) << 32) |
                (int(self.round_start_time) & 0xffffffff))
            finish_reason = {
                "team_eliminated": 1,
                "elimination": 1,
                "base captured": 2,
                "battle_timeout": 3,
                "all_players_left": 4,
            }.get(result["reason"], 5)
            for participant in participants:
                player_id = int(participant["player_id"])
                live_player = self.players.get(player_id)
                public_row = public_by_player[player_id]
                rewards = compute_offline_rewards(
                    self._statistics_row("player", player_id),
                    winner == int(participant["team"]), participated=True,
                    vehicle_tier=participant.get(
                        "vehicle_tier", self._result_vehicle_tier(
                            participant["vehicle"], player_id)))
                receipt = {
                    "type": "battle_receipt",
                    "protocol": PROTOCOL_VERSION,
                    "receipt_id": "%s:%d:%d" % (
                        self.receipt_namespace, self.round_id, player_id),
                    "arena_unique_id": arena_unique_id,
                    "round_id": int(self.round_id),
                    "player_id": player_id,
                    "account_key": participant["account_key"],
                    "player_name": participant["name"],
                    "vehicle": participant["vehicle"],
                    "team": int(participant["team"]),
                    "winner": winner,
                    "map": self.map_name,
                    "finish_reason": finish_reason,
                    "death_reason": public_row["death_reason"],
                    "duration": max(0, int(round(
                        float(self.tick) / TICK_HZ))),
                    "premature_leave": bool(
                        live_player is None or
                        not live_player.participating),
                    "stats": dict(public_row["stats"]),
                    "rewards": rewards,
                    "public_results": public_results,
                    "interactions": self._receipt_interactions(
                        ("player", player_id)),
                }
                receipt_id = receipt["receipt_id"]
                # One account may finish another arena before an earlier ACK
                # reaches the server. Keep both idempotent receipts; delivery
                # drains them oldest-first for that account.
                next_receipts[receipt_id] = receipt
                while len(next_receipts) > MAX_RESULT_RECEIPTS:
                    next_receipts.popitem(last=False)
            try:
                # Persist before the result can be broadcast or delivered.
                self._persist_result_receipts(next_receipts)
            except (OSError, ValueError, TypeError) as error:
                _server_log("BATTLE RESULT persistence failed: %s" % error)
                return False
            self.result_receipts = next_receipts
        self.battle_result = result
        self.result_reset_tick = self.tick + max(
            1, int(round(RESULT_RESET_SECONDS * TICK_HZ)))
        for player in self.players.values():
            player.forward = 0.0
            player.turn = 0.0
        if self.projectiles:
            for projectile_id, record in list(self.projectiles.items()):
                self.projectile_tombstones[projectile_id] = {
                    "projectile_id": projectile_id,
                    "outcome": "battle_finished",
                    "launch_fingerprint": record["launch_fingerprint"],
                    "request_fingerprint": None,
                    "last_progress_request_fingerprint": record.get(
                        "last_progress_request_fingerprint"),
                }
            self.projectiles.clear()
            self.projectile_revision += 1
        self.pending_events.append(dict(self.battle_result, kind="battle_result"))
        return True

    def handle_tick_failure(self, error):
        """Terminate one uncertain active round without minting settlement."""
        diagnostic = _tick_failure_diagnostic(error)
        with self.lock:
            self.server_tick_failure_count += 1
            self.last_server_tick_failure = diagnostic
            if self.phase not in ("loading", "battle"):
                return False

            # A tick may have failed after partially consuming a planner or
            # event batch. Do not retry that unknown transaction. Publish one
            # clean terminal barrier and let the ordinary result-reset path
            # return every connected endpoint to the waiting room.
            self.pending_live_message = None
            self.pending_events = []
            if self.battle_result is None:
                self.phase = "battle"
                if not self._finish_battle(
                        0, "server_tick_failure", 0,
                        record_receipts=False):
                    raise RuntimeError(
                        "server tick failure terminal was not committed")
                self.state_revision += 1
            else:
                # The exception may have happened after extracting an
                # existing terminal event but before every endpoint received
                # it. Requeue the canonical result rather than inventing a
                # second outcome.
                self.pending_events.append(dict(
                    self.battle_result, kind="battle_result"))
            return True

    def _maybe_finish_battle(self):
        """Finish a standard battle once one of the two teams is eliminated.

        Wait for the authority manifest before evaluating.  The stock roster is
        intentionally not counted as alive: it has no health state yet, while
        treating it as dead would end the round during the startup handshake.
        """
        if (not self._combat_accepting() or self.battle_result is not None or
                (not self.roster_finalized and not self.bot_manifest)):
            return False
        if self.bot_roster and not self.bot_manifest:
            return False
        if self.bot_roster:
            roster_ids = {int(entry.get("id", 0))
                          for entry in self.bot_roster}
            manifest_ids = {int(entry.get("id", 0))
                            for entry in self.bot_manifest}
            if not roster_ids.issubset(manifest_ids):
                return False
        participant_teams = {
            int(entry.get("team", 0)) for entry in self.bot_roster}
        participant_teams.update(
            player.team for player in self.players.values()
            if player.connected)
        if not {1, 2}.issubset(participant_teams):
            return False
        alive_teams = set()
        for player in self.players.values():
            if player.connected and player.alive and player.team in (1, 2):
                alive_teams.add(player.team)
        for state in self.bot_states.values():
            if state.get("alive") and state.get("team") in (1, 2):
                alive_teams.add(int(state["team"]))
        if alive_teams == {1, 2}:
            return False
        winner = next(iter(alive_teams)) if len(alive_teams) == 1 else 0
        return self._finish_battle(winner, "team_eliminated", 0)

    def _validated_ram_contact(self, player, raw_ram):
        """Compatibility surface for direct validators and older tests."""
        return self._validate_ram_contact(player, raw_ram)[0]

    def _normalize_ram_contact_envelope(self, player, raw_ram):
        """Validate one receipt without consulting mutable Bot progress."""
        if (not isinstance(raw_ram, dict) or
                set(raw_ram) != HUMAN_RAM_CONTACT_FIELDS):
            return None, "malformed_contact"
        try:
            seq = _exact_int(raw_ram.get("seq"), 1, 2147483647)
            bot_id = _exact_int(raw_ram.get("bot_id"), 1, 30)
            revision = _exact_int(
                raw_ram.get("bot_state_revision"), 0, 2147483647)
            presentation_time_us = _exact_int(
                raw_ram.get("presentation_time_us"), 0,
                MAX_MOTION_TIME_US)
            native_contact_time_us = _exact_int(
                raw_ram.get("native_contact_time_us"), 0,
                MAX_MOTION_TIME_US)
            player_armor = _bounded_float(
                raw_ram.get("contact_armor_player"), 0.0, 5000.0,
                False)
            bot_armor = _bounded_float(
                raw_ram.get("contact_armor_bot"), 0.0, 5000.0, False)
            player_spall = _bounded_float(
                raw_ram.get("contact_spall_player"), 1.0, 1.5)
            player_bonus = _bounded_float(
                raw_ram.get("contact_bonus_player"), 0.0, 0.15)
            contact_normal_x = _bounded_float(
                raw_ram.get("contact_normal_x"), -1.0, 1.0)
            contact_normal_z = _bounded_float(
                raw_ram.get("contact_normal_z"), -1.0, 1.0)
            center_x = _bounded_float(raw_ram.get("x"), -2000.0, 2000.0)
            center_y = _bounded_float(raw_ram.get("y"), -1000.0, 1000.0)
            center_z = _bounded_float(raw_ram.get("z"), -2000.0, 2000.0)
            yaw = _bounded_float(
                raw_ram.get("yaw"), -math.pi * 2.0, math.pi * 2.0)
            pitch = _bounded_float(raw_ram.get("pitch", 0.0), -0.61, 0.61)
            roll = _bounded_float(raw_ram.get("roll", 0.0), -0.61, 0.61)
            hit_x = _bounded_float(
                raw_ram.get("contact_x"), -2000.0, 2000.0)
            hit_y = _bounded_float(
                raw_ram.get("contact_y"), -1000.0, 1000.0)
            hit_z = _bounded_float(
                raw_ram.get("contact_z"), -2000.0, 2000.0)
            velocities = {
                name: _bounded_float(raw_ram.get(name), -200.0, 200.0)
                for name in (
                    "vx", "vy", "vz", "bot_vx", "bot_vy", "bot_vz")
            }
        except (TypeError, ValueError, OverflowError):
            return None, "malformed_contact"
        contact_normal_length = math.hypot(
            contact_normal_x, contact_normal_z)
        if (seq is None or bot_id is None or revision is None or
                presentation_time_us is None or
                native_contact_time_us is None or
                abs(native_contact_time_us - presentation_time_us) >
                int(HUMAN_POSE_HISTORY_SECONDS * 1000000.0) or
                not math.isfinite(contact_normal_length) or
                not 0.999 <= contact_normal_length <= 1.001 or
                not isinstance(raw_ram.get("contact_screened_player"), bool) or
                not isinstance(raw_ram.get("contact_screened_bot"), bool) or
                raw_ram.get("contact_screened_player") or
                raw_ram.get("contact_screened_bot")):
            return None, "invalid_contact_contract"
        profile = self.human_collision_profiles.get(player.player_id)
        if profile is None:
            return None, "missing_collision_profile"
        if abs(player_spall - float(
                profile["ram_profile"]["spall_coefficient"])) > 0.0001:
            return None, "ram_profile_mismatch"
        if not tank_collision.body_contains_point({
                "x": center_x, "y": center_y, "z": center_z,
                "yaw": yaw, "pitch": pitch, "roll": roll,
                "shape": profile["shape"],
        }, (hit_x, hit_y, hit_z),
                slop=tank_collision.RAM_CONTACT_POINT_SLOP):
            return None, "contact_outside_player_body"
        if (contact_normal_x * (center_x - hit_x) +
                contact_normal_z * (center_z - hit_z)) <= 0.000001:
            return None, "contact_normal_mismatch"
        return {
            "seq": seq,
            "bot_id": bot_id,
            "bot_state_revision": revision,
            "presentation_time_us": presentation_time_us,
            "native_contact_time_us": native_contact_time_us,
            "contact_x": round(hit_x, 4),
            "contact_y": round(hit_y, 4),
            "contact_z": round(hit_z, 4),
            "contact_normal_x": round(
                contact_normal_x / contact_normal_length, 6),
            "contact_normal_z": round(
                contact_normal_z / contact_normal_length, 6),
            "contact_armor_player": round(player_armor, 4),
            "contact_armor_bot": round(bot_armor, 4),
            "contact_screened_player": raw_ram["contact_screened_player"],
            "contact_screened_bot": raw_ram["contact_screened_bot"],
            "contact_spall_player": round(player_spall, 4),
            "contact_bonus_player": round(player_bonus, 6),
            "x": round(center_x, 4),
            "y": round(center_y, 4),
            "z": round(center_z, 4),
            "yaw": round(yaw, 5),
            "pitch": round(pitch, 5),
            "roll": round(roll, 5),
            "vx": round(velocities["vx"], 4),
            "vy": round(velocities["vy"], 4),
            "vz": round(velocities["vz"], 4),
            "bot_vx": round(velocities["bot_vx"], 4),
            "bot_vy": round(velocities["bot_vy"], 4),
            "bot_vz": round(velocities["bot_vz"], 4),
        }, None

    def _validate_ram_contact(self, player, raw_ram):
        """Return one admitted contact or a stable fail-closed reason."""
        contact, reason = self._normalize_ram_contact_envelope(
            player, raw_ram)
        if contact is None:
            return None, reason
        if (contact["bot_id"] not in self.bot_states or
                contact["bot_state_revision"] > self.bot_state_revision or
                contact["bot_state_revision"] + 255 <
                self.bot_state_revision or
                contact["presentation_time_us"] > self.bot_state_time_us):
            return None, "invalid_contact_contract"
        return contact, None

    @staticmethod
    def _validated_player_destructible_contact(raw_contact):
        """Validate one client proposal without trusting its map verdict."""
        if (not isinstance(raw_contact, dict) or
                set(raw_contact) != {
                    "seq", "x", "y", "z", "yaw", "speed", "dt",
                    "end_x", "end_y", "end_z", "end_yaw", "token"}):
            return None
        try:
            seq = _exact_int(raw_contact.get("seq"), 1, PROJECTILE_MAX_ID)
            x = _bounded_float(raw_contact.get("x"), -2000.0, 2000.0)
            y = _bounded_float(raw_contact.get("y"), -1000.0, 1000.0)
            z = _bounded_float(raw_contact.get("z"), -2000.0, 2000.0)
            yaw = _bounded_float(
                raw_contact.get("yaw"), -math.pi * 2.0, math.pi * 2.0)
            speed = _bounded_float(
                raw_contact.get("speed"), -200.0, 200.0)
            step = _bounded_float(
                raw_contact.get("dt"), 0.0, 0.1, False)
            end_x = _bounded_float(
                raw_contact.get("end_x"), -2000.0, 2000.0)
            end_y = _bounded_float(
                raw_contact.get("end_y"), -1000.0, 1000.0)
            end_z = _bounded_float(
                raw_contact.get("end_z"), -2000.0, 2000.0)
            end_yaw = _bounded_float(
                raw_contact.get("end_yaw"),
                -math.pi * 2.0, math.pi * 2.0)
        except (TypeError, ValueError, OverflowError):
            return None
        raw_token = raw_contact.get("token")
        if (seq is None or None in (end_x, end_y, end_z, end_yaw) or
                not isinstance(raw_token, list) or
                not 1 <= len(raw_token) <=
                MAX_PLAYER_DESTRUCTIBLE_CONTACT_TOKEN):
            return None
        move_x = end_x - x
        move_y = end_y - y
        move_z = end_z - z
        move_distance = math.hypot(move_x, move_z)
        yaw_delta = (end_yaw - yaw + math.pi) % (
            2.0 * math.pi) - math.pi
        if (abs(move_y) > MAX_PLAYER_DESTRUCTIBLE_VERTICAL_TRAVEL or
                move_distance > abs(speed) * step +
                MAX_PLAYER_DESTRUCTIBLE_LINEAR_SLOP or
                abs(yaw_delta) >
                MAX_PLAYER_DESTRUCTIBLE_ANGULAR_SPEED * step + 0.001 or
                (move_distance <= 0.0001 and abs(yaw_delta) <= 0.00001)):
            return None
        token = set()
        for raw in raw_token:
            if not isinstance(raw, list) or len(raw) != 3:
                return None
            try:
                chunk_id = _exact_int(raw[0], 0, PROJECTILE_MAX_ID)
                item_index = _exact_int(raw[1], 0, PROJECTILE_MAX_ID)
                mat_kind = (None if raw[2] is None else
                            _exact_int(raw[2], 0, PROJECTILE_MAX_ID))
            except (TypeError, ValueError, OverflowError):
                return None
            if (chunk_id is None or item_index is None or
                    (raw[2] is not None and mat_kind is None)):
                return None
            token.add((chunk_id, item_index, mat_kind))
        if len(token) != len(raw_token):
            return None
        ordered = sorted(token, key=lambda row: (
            row[0], row[1], -1 if row[2] is None else row[2]))
        return {
            "seq": seq,
            "x": round(x, 4),
            "y": round(y, 4),
            "z": round(z, 4),
            "yaw": round(yaw, 5),
            "speed": round(speed, 4),
            "dt": round(step, 6),
            "end_x": round(end_x, 4),
            "end_y": round(end_y, 4),
            "end_z": round(end_z, 4),
            "end_yaw": round(end_yaw, 5),
            "token": [list(row) for row in ordered],
        }

    @staticmethod
    def _player_input_identity(message):
        """Return the exact ordered sequence, raw payload and digest.

        The identity is always the raw wire payload, so an exact retry folds
        even when a diagnostic hook later rewrites a field of the frame.
        """
        sequence = _exact_int(
            message.get("input_seq"), 1, PROJECTILE_MAX_ID)
        payload = _message_fingerprint(message)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return sequence, payload, digest

    def _record_player_input_decision(self, player, sequence, fingerprint,
                                      outcome, reason="", field="",
                                      active=True):
        """Advance only the terminal frontier and retain its typed outcome."""
        player.input_processed_seq = int(sequence)
        player.input_decisions[int(sequence)] = {
            "fingerprint": str(fingerprint),
            "outcome": str(outcome),
            "reason": str(reason),
            "field": str(field),
            "active": bool(active),
        }
        while len(player.input_decisions) > MAX_PLAYER_INPUT_DECISIONS:
            player.input_decisions.popitem(last=False)
        return outcome

    def _note_player_input_rejection(self, player, sequence, reason,
                                     field="", consumed=False, active=True):
        """Keep one bounded typed first cause for the socket diagnostics."""
        counts = player.input_reject_counts
        if (reason not in counts and
                len(counts) >= MAX_PLAYER_INPUT_REJECT_REASONS):
            counts.clear()
        counts[reason] = counts.get(reason, 0) + 1
        player.last_input_reject = {
            "reason": str(reason),
            "field": str(field),
            "player_id": int(player.player_id),
            "round_id": int(self.round_id),
            "submitted_seq": (
                None if sequence is None else int(sequence)),
            "expected_seq": int(player.input_processed_seq) + 1,
            "processed_seq": int(player.input_processed_seq),
            "applied_seq": int(player.input_seq),
            "checkpoint_seq": int(player.gun_checkpoint_seq),
            "active": bool(active),
            "consumed": bool(consumed),
            "repeats": int(counts[reason]),
        }
        return False

    def _reject_player_input_frame(self, player, sequence, fingerprint,
                                   reason, field="", active=True):
        """End one expected frame without applying any part of its state."""
        # Record the diagnostic against the pre-decision frontiers, then
        # advance only the terminal frontier.  No control, pose, shell, gun
        # checkpoint or contact state may be written on this path.
        self._note_player_input_rejection(
            player, sequence, reason, field, consumed=True, active=active)
        self._record_player_input_decision(
            player, sequence, fingerprint, INPUT_OUTCOME_REJECTED, reason,
            field, active)
        return False

    def _admit_applied_player_input(self, player, sequence, payload, digest):
        """Reserve a validated frame on both ordered-input frontiers."""
        self._record_player_input_decision(
            player, sequence, digest, INPUT_OUTCOME_APPLIED, "")
        player.input_seq = int(sequence)
        player.input_fingerprints[int(sequence)] = payload
        while (len(player.input_fingerprints) >
               MAX_PLAYER_INPUT_FINGERPRINTS):
            player.input_fingerprints.popitem(last=False)
        return True

    def player_input_rejection_log(self, player_id):
        """Return one bounded typed first-cause line for the socket reader."""
        with self.lock:
            player = self.players.get(player_id)
            detail = (
                dict(player.last_input_reject) if player is not None else {})
        if not detail or detail.get("round_id") != self.round_id:
            # Nothing typed for this round: the message did not identify a
            # live player of the current round at all.
            return ("player-input:%d" % player_id,
                    "PLAYER INPUT rejected sender=%d round=%d "
                    "reason=unidentified" % (player_id, self.round_id))
        return (
            "player-input:%d:%s" % (player_id, detail["reason"]),
            "PLAYER INPUT rejected sender=%s round=%s reason=%s field=%s "
            "seq=%s expected=%s processed=%s applied=%s checkpoint=%s "
            "active=%s consumed=%s repeats=%s" % (
                detail["player_id"], detail["round_id"], detail["reason"],
                detail["field"] or "-", detail["submitted_seq"],
                detail["expected_seq"], detail["processed_seq"],
                detail["applied_seq"], detail["checkpoint_seq"],
                int(detail["active"]), int(detail["consumed"]),
                detail["repeats"]))

    def _record_player_pose_sample(self, player, message):
        """Store one source-timed pose without extrapolating a broken clock."""
        try:
            sample_time_us = _exact_int(
                message.get("pose_time_us"), 0, MAX_MOTION_TIME_US)
        except (TypeError, ValueError, OverflowError):
            sample_time_us = None
        receipt_time_us = self._logical_motion_time_us()
        if (sample_time_us is None or
                sample_time_us >
                receipt_time_us + HUMAN_POSE_CLOCK_LEEWAY_US or
                sample_time_us < receipt_time_us - int(
                    HUMAN_POSE_HISTORY_SECONDS * 2000000.0)):
            player.pose_history.clear()
            player.pose_time_us = None
            return False
        sample_time_us = min(sample_time_us, receipt_time_us)
        previous_time_us = player.pose_time_us
        if (previous_time_us is not None and
                sample_time_us <= previous_time_us):
            player.pose_history.clear()
        speed = float(player.speed) if player.alive else 0.0
        sample = {
            "input_seq": int(player.input_seq),
            "time_us": int(sample_time_us),
            "x": float(player.x), "y": float(player.y),
            "z": float(player.z), "yaw": float(player.yaw),
            "forward": float(player.forward), "turn": float(player.turn),
            "speed": speed,
            "vx": math.sin(float(player.yaw)) * speed,
            "vz": math.cos(float(player.yaw)) * speed,
            "pitch": float(player.pitch), "roll": float(player.roll),
        }
        player.pose_history.append(sample)
        player.pose_time_us = int(sample_time_us)
        oldest = sample_time_us - int(
            HUMAN_POSE_HISTORY_SECONDS * 1000000.0)
        while (len(player.pose_history) > 2 and
               player.pose_history[1]["time_us"] < oldest):
            player.pose_history.popleft()
        return True

    # Every entry is the exact static contract the shipping client
    # canonicalizes against.  Periodic angles are normalized by the client to
    # the principal interval; the wider period accepted here keeps an
    # already-legal orientation valid without clipping it.
    MODERN_INPUT_BOUNDED_FIELDS = (
        ("forward", -1.0, 1.0),
        ("turn", -1.0, 1.0),
        ("speed", -200.0, 200.0),
        ("aim_yaw", -math.pi * 2.0, math.pi * 2.0),
        ("gun_pitch", -1.2, 1.2),
        ("x", -2000.0, 2000.0),
        ("y", -1000.0, 1000.0),
        ("z", -2000.0, 2000.0),
        ("yaw", -math.pi * 2.0, math.pi * 2.0),
        ("pitch", -0.61, 0.61),
        ("roll", -0.61, 0.61),
    )

    def _modern_input_envelope_valid(self, player, message,
                                     validate_contacts=False):
        """Validate one input envelope without advancing any ledger."""
        return not self._modern_input_envelope_failure(
            player, message, validate_contacts)[0]

    def _modern_input_envelope_failure(self, player, message,
                                       validate_contacts=False):
        """Return one typed (reason, field) failure or ("", "")."""
        try:
            _exact_int(message.get("round_id"), 1, PROJECTILE_MAX_ID)
        except (TypeError, ValueError, OverflowError):
            return "envelope_round_id", "round_id"
        for name, low, high in self.MODERN_INPUT_BOUNDED_FIELDS:
            if name not in message:
                continue
            try:
                _bounded_float(message[name], low, high)
            except (TypeError, ValueError, OverflowError):
                return "envelope_numeric", name
        for name, low, high in (
                ("pose_time_us", 0, MAX_MOTION_TIME_US),
                ("fire_seq", 0, PROJECTILE_MAX_ID),
                ("input_seq", 1, PROJECTILE_MAX_ID)):
            if name not in message:
                continue
            try:
                _exact_int(message[name], low, high)
            except (TypeError, ValueError, OverflowError):
                return "envelope_integer", name
        if ("input_seq" not in message and
                HUMAN_RAM_TIMELINE_CAPABILITY in player.capabilities):
            return "envelope_integer", "input_seq"
        if ("siege_enabled" in message and
                not isinstance(message["siege_enabled"], bool)):
            return "envelope_siege", "siege_enabled"
        if not validate_contacts:
            # Active frames retain the established per-row rejection path.
            # A bad optional contact must not reject the ordered control frame
            # and leave every subsequent input stuck behind a sequence gap.
            return "", ""
        raw_ram_contacts = message.get("ram_contacts", [])
        if (not isinstance(raw_ram_contacts, list) or
                len(raw_ram_contacts) > 16):
            return "envelope_contacts", "ram_contacts"
        for raw_ram in raw_ram_contacts:
            if self._normalize_ram_contact_envelope(
                    player, raw_ram)[0] is None:
                return "envelope_contacts", "ram_contacts"
        if "ram_contact" in message and self._normalize_ram_contact_envelope(
                player, message["ram_contact"])[0] is None:
            return "envelope_contacts", "ram_contact"
        raw_destructible_contacts = message.get(
            "destructible_contacts", [])
        if (not isinstance(raw_destructible_contacts, list) or
                len(raw_destructible_contacts) >
                MAX_PENDING_PLAYER_DESTRUCTIBLE_CONTACTS):
            return "envelope_contacts", "destructible_contacts"
        for raw in raw_destructible_contacts:
            if self._validated_player_destructible_contact(raw) is None:
                return "envelope_contacts", "destructible_contacts"
        return "", ""

    def _player_input_frame_failure(self, player, message, inactive_modern):
        """Validate one whole frame before any frontier or state advances.

        Returns ``((reason, field), parsed)``.  ``reason`` is empty for a
        frame whose complete envelope is in contract, and ``parsed`` then
        carries the canonical shell selection, gun checkpoint and world-up
        the caller may commit.
        """
        parsed = {
            "shell_selection": None,
            "gun_checkpoint": None,
            "up_cosine": None,
        }
        if self.client_build == CLIENT_BUILD_0922:
            fields = set(message)
            if "type" in message and message.get("type") != "input":
                return ("field_whitelist", "type"), parsed
            missing = MODERN_INPUT_REQUIRED_FIELDS - fields
            if missing:
                return ("field_required", sorted(missing)[0]), parsed
            extra = fields - MODERN_INPUT_FIELDS
            if extra:
                return ("field_whitelist", sorted(extra)[0]), parsed
        has_next_shell = "next_shell_index" in message
        has_shell_pending = "shell_change_pending" in message
        if has_next_shell != has_shell_pending:
            return ("shell_pair_shape", (
                "next_shell_index" if has_next_shell
                else "shell_change_pending")), parsed
        if has_next_shell and "shell_index" not in message:
            return ("shell_pair_shape", "shell_index"), parsed
        if "shell_index" in message:
            try:
                loaded_shell = _exact_int(message.get("shell_index"), 0, 9)
            except (TypeError, ValueError, OverflowError):
                return ("shell_selection", "shell_index"), parsed
            try:
                next_shell = (_exact_int(
                    message.get("next_shell_index"), 0, 9)
                    if has_next_shell else loaded_shell)
            except (TypeError, ValueError, OverflowError):
                return ("shell_selection", "next_shell_index"), parsed
            pending_shell = (
                message.get("shell_change_pending")
                if has_shell_pending else False)
            if not isinstance(pending_shell, bool):
                return ("shell_selection", "shell_change_pending"), parsed
            if not pending_shell and next_shell != loaded_shell:
                return ("shell_selection", "next_shell_index"), parsed
            parsed["shell_selection"] = (
                loaded_shell, next_shell, pending_shell)
        checkpoint_required = bool(
            message.get("type") == "input" and
            PLAYER_FIRE_INTENT_CAPABILITY in player.capabilities)
        if checkpoint_required and "gun_checkpoint" not in message:
            return ("gun_checkpoint_missing", "gun_checkpoint"), parsed
        if "gun_checkpoint" in message:
            if (parsed["shell_selection"] is None or
                    HUMAN_RAM_TIMELINE_CAPABILITY not in
                    player.capabilities):
                return ("gun_checkpoint_context", "gun_checkpoint"), parsed
            try:
                parsed["gun_checkpoint"] = _canonical_human_gun_checkpoint(
                    message.get("gun_checkpoint"))
            except (TypeError, ValueError, OverflowError):
                return ("gun_checkpoint_shape", "gun_checkpoint"), parsed
        if (self.client_build == CLIENT_BUILD_0922 and
                "up_cosine" in message):
            raw_up_cosine = message.get("up_cosine")
            if (isinstance(raw_up_cosine, bool) or
                    not isinstance(raw_up_cosine, (int, float)) or
                    not math.isfinite(float(raw_up_cosine)) or
                    not -1.0 <= float(raw_up_cosine) <= 1.0):
                return ("world_up", "up_cosine"), parsed
            parsed["up_cosine"] = float(raw_up_cosine)
        if self.client_build == CLIENT_BUILD_0922:
            reason, field = self._modern_input_envelope_failure(
                player, message, validate_contacts=inactive_modern)
            if reason:
                return (reason, field), parsed
        return ("", ""), parsed

    @staticmethod
    def _player_pose_for_destructible_contact(player, contact):
        """Bind a proposal only to an already admitted player input sample."""
        for sample in reversed(player.pose_history):
            yaw_delta = (float(sample["yaw"]) - float(contact["yaw"]) +
                         math.pi) % (2.0 * math.pi) - math.pi
            if (abs(float(sample["x"]) - float(contact["x"])) > 0.02 or
                    abs(float(sample["y"]) - float(contact["y"])) > 0.05 or
                    abs(float(sample["z"]) - float(contact["z"])) > 0.02 or
                    abs(yaw_delta) > 0.002):
                continue
            return sample
        return None

    def update_input(self, player_id, message):
        """Reach one idempotent terminal decision for one ordered frame.

        The ordered ledger separates three concepts.  ``input_processed_seq``
        is the contiguous frontier of frames that reached a terminal decision,
        applied or not.  ``input_seq`` is the last frame whose gameplay state
        was actually committed.  ``input_decisions`` remembers the bounded
        fingerprint and typed outcome per sequence so an exact retry folds and
        a changed payload at the same sequence conflicts.

        A recoverable validation failure therefore ends that one operation
        without applying any field and without installing a gun checkpoint,
        while the next well-formed frame can still advance automatically.
        """
        with self.lock:
            player = self.players.get(player_id)
            if player is None:
                return False
            inactive_modern = bool(
                self.client_build == CLIENT_BUILD_0922 and (
                    self.phase != "battle" or
                    self.battle_result is not None or
                    not player.participating or not player.alive))
            submitted_sequence = None
            if isinstance(message, dict) and "input_seq" in message:
                try:
                    submitted_sequence = _exact_int(
                        message.get("input_seq"), 1, PROJECTILE_MAX_ID)
                except (TypeError, ValueError, OverflowError):
                    pass
            if not self._message_round_matches(message):
                return self._note_player_input_rejection(
                    player, submitted_sequence, "round_mismatch",
                    "round_id", active=not inactive_modern)
            if not player.connected:
                return self._note_player_input_rejection(
                    player, submitted_sequence, "player_disconnected",
                    active=False)
            ledger = bool(
                HUMAN_RAM_TIMELINE_CAPABILITY in player.capabilities)
            sequence = None
            payload = ""
            digest = ""
            if ledger:
                try:
                    sequence, payload, digest = (
                        self._player_input_identity(message))
                except (TypeError, ValueError, OverflowError):
                    # Without a usable exact sequence this message cannot be
                    # allowed to consume another operation's ordered slot.
                    return self._note_player_input_rejection(
                        player, None, "sequence_identity", "input_seq")
                decision = player.input_decisions.get(sequence)
                if decision is not None:
                    if decision["fingerprint"] != digest:
                        # A changed payload may never replace a decision that
                        # already became terminal at this sequence.
                        return self._note_player_input_rejection(
                            player, sequence, "identity_conflict",
                            "input_seq")
                    if decision["outcome"] == INPUT_OUTCOME_REJECTED:
                        return self._note_player_input_rejection(
                            player, sequence, decision["reason"],
                            decision.get("field", "input_seq"),
                            consumed=True,
                            active=decision.get("active", True))
                    return True
                if sequence <= player.input_processed_seq:
                    # Evicted from the bounded ledger: safely rejected, and it
                    # can never resurrect state.
                    return self._note_player_input_rejection(
                        player, sequence, "sequence_retired", "input_seq")
                if sequence != player.input_processed_seq + 1:
                    return self._note_player_input_rejection(
                        player, sequence, "sequence_gap", "input_seq")
                fault_class = _player_input_fault_class()
                if (fault_class and
                        player.input_fault_round != self.round_id):
                    # Deterministic acceptance hook: break exactly one frame
                    # per round and let the production validator reject it.
                    player.input_fault_round = int(self.round_id)
                    _server_log(
                        "PLAYER INPUT fault injected sender=%d round=%d "
                        "class=%s seq=%d" % (
                            player.player_id, self.round_id, fault_class,
                            sequence))
                    message = _injected_player_input_fault(
                        message, fault_class)
            (reason, field), parsed = self._player_input_frame_failure(
                player, message, inactive_modern)
            if reason:
                if not ledger:
                    # No ordered ledger to consume, but the socket reader
                    # still needs the typed first cause instead of a generic
                    # line.
                    return self._note_player_input_rejection(
                        player, None, reason, field,
                        active=not inactive_modern)
                return self._reject_player_input_frame(
                    player, sequence, digest, reason, field,
                    active=not inactive_modern)
            shell_selection = parsed["shell_selection"]
            gun_checkpoint = parsed["gun_checkpoint"]
            reported_up_cosine = parsed["up_cosine"]
            if self.client_build == CLIENT_BUILD_0922 and inactive_modern:
                # A complete input frame may already be queued when death,
                # leave, or the round result overtakes it. Its whole
                # non-mutating envelope is validated above; fold it as a
                # terminal no-op that advances only the processed frontier so
                # the frames queued behind it never wait on a sequence gap.
                if ledger:
                    self._record_player_input_decision(
                        player, sequence, digest,
                        INPUT_OUTCOME_INACTIVE, "inactive", active=False)
                return True
            if ledger:
                # Every client-controlled validation path has returned above.
                # Reserve the transport identity before projecting the
                # prevalidated fields so an unexpected internal handler fault
                # cannot recreate the original permanent sequence gap.  This
                # is fault containment, not a general rollback transaction.
                self._admit_applied_player_input(
                    player, sequence, payload, digest)
                if gun_checkpoint is not None:
                    checkpoint_seq = int(player.input_seq)
                    player.gun_checkpoint_seq = checkpoint_seq
                    player.gun_checkpoint = dict(gun_checkpoint)
                    player.gun_checkpoints[checkpoint_seq] = dict(
                        gun_checkpoint)
                    while (len(player.gun_checkpoints) >
                           MAX_PLAYER_INPUT_FINGERPRINTS):
                        player.gun_checkpoints.popitem(last=False)
            if (player.alive and self.phase == "battle" and
                    self.battle_result is None and
                    "siege_enabled" in message):
                self._request_siege_state(
                    player, message.get("siege_enabled"))
            if not self._combat_accepting() or self.battle_result is not None:
                player.forward = 0.0
                player.turn = 0.0
                return
            if player.alive:
                siege_switching = player.siege_state in (
                    SIEGE_SWITCHING_ON, SIEGE_SWITCHING_OFF)
                if siege_switching:
                    player.forward = 0.0
                    player.turn = 0.0
                    player.speed = 0.0
                else:
                    if "forward" in message:
                        player.forward = _clamp(
                            _finite_float(message.get("forward")),
                            -1.0, 1.0)
                    if "turn" in message:
                        player.turn = _clamp(
                            _finite_float(message.get("turn")),
                            -1.0, 1.0)
                    if "speed" in message:
                        speed_limit = self._siege_speed_limit(player)
                        player.speed = _clamp(
                            _finite_float(message.get("speed")),
                            -speed_limit, speed_limit)
                if "aim_yaw" in message:
                    player.aim_yaw = _finite_float(message.get("aim_yaw"), player.aim_yaw)
                if "gun_pitch" in message:
                    player.gun_pitch = _clamp(_finite_float(message.get("gun_pitch")), -1.2, 1.2)
                if "x" in message and "z" in message:
                    # Switching owns the drivetrain, not world contact. Keep
                    # accepting the client's gravity, slope and collision pose
                    # while forward/turn/speed remain authoritatively zero.
                    player.x = _clamp(_finite_float(message.get("x"), player.x), -2000.0, 2000.0)
                    player.y = _clamp(_finite_float(message.get("y"), player.y), -1000.0, 1000.0)
                    player.z = _clamp(_finite_float(message.get("z"), player.z), -2000.0, 2000.0)
                    player.yaw = _finite_float(message.get("yaw"), player.yaw)
                    if "pitch" in message:
                        player.pitch = _clamp(
                            _finite_float(message.get("pitch"), player.pitch),
                            -0.61, 0.61)
                    if "roll" in message:
                        player.roll = _clamp(
                            _finite_float(message.get("roll"), player.roll),
                            -0.61, 0.61)
                    if reported_up_cosine is not None:
                        player.up_cosine = round(reported_up_cosine, 6)
                    player.client_position = True
                    if (HUMAN_RAM_TIMELINE_CAPABILITY in
                            player.capabilities):
                        self._record_player_pose_sample(player, message)
                raw_contacts = None
                if (RAM_CONTACT_LEDGER_CAPABILITY in player.capabilities and
                        "ram_contacts" in message):
                    candidate = message.get("ram_contacts")
                    if (isinstance(candidate, list) and
                            len(candidate) <= 16):
                        raw_contacts = candidate
                elif isinstance(message.get("ram_contact"), dict):
                    # Protocol-v5 compatibility: older clients repeat one
                    # latest receipt. Current senders retain the same one-row
                    # fallback when no batch is available.
                    raw_contacts = [message.get("ram_contact")]
                if raw_contacts is not None:
                    authority = (self.simulation_worker if
                                 self.bot_authority_id ==
                                 SIMULATION_WORKER_AUTHORITY_ID else
                                 self.players.get(self.bot_authority_id))
                    ledger_authority = bool(
                        authority is not None and
                        RAM_CONTACT_LEDGER_CAPABILITY in
                        authority.capabilities)
                    pending_limit = (
                        MAX_PENDING_RAM_CONTACTS if ledger_authority else 1)
                    by_seq = {}
                    conflicting = set()
                    for raw_ram in raw_contacts:
                        try:
                            raw_seq = _exact_int(
                                raw_ram.get("seq"), 1, 2147483647)
                        except (AttributeError, TypeError, ValueError,
                                OverflowError):
                            raw_seq = None
                        if raw_seq is not None:
                            previous = by_seq.get(raw_seq)
                            if previous is None:
                                by_seq[raw_seq] = raw_ram
                            elif previous != raw_ram:
                                conflicting.add(raw_seq)
                    for seq in sorted(by_seq):
                        raw_ram = by_seq[seq]
                        if seq <= player.ram_contact_seq:
                            continue
                        if (RAM_CONTACT_LEDGER_CAPABILITY in
                                player.capabilities and
                                seq != player.ram_contact_seq + 1):
                            break
                        if len(player.ram_contacts) >= pending_limit:
                            break
                        if seq in conflicting:
                            contact = None
                            reject_reason = "conflicting_contact_payload"
                        else:
                            contact, reject_reason = (
                                self._validate_ram_contact(player, raw_ram))
                        if contact is None:
                            # A permanently invalid but identifiable head row
                            # still needs a terminal input decision. Otherwise
                            # it blocks every later collision indefinitely.
                            self._reject_player_ram_contact(
                                player, seq, reject_reason)
                            continue
                        if self.client_build == CLIENT_BUILD_0922:
                            # The contact body is sampled before local
                            # separation, while this input's ordinary pose is
                            # sampled after the reciprocal impulse/correction.
                            # Replacing the former with the latter therefore
                            # removes the overlap that the authority must
                            # independently re-check and turns every player-
                            # bot ram into a terminal zero-damage receipt.
                            # Bind the validated pre-separation body to this
                            # ordered input transaction without rewriting it;
                            # the worker still derives damage from the frozen
                            # bot history and owns the canonical HP commit.
                            contact["input_seq"] = int(player.input_seq)
                        player.ram_contact_seq = seq
                        player.ram_contacts[seq] = contact
                        player.ram_contact = dict(contact)
                raw_destructible_contacts = None
                if (HUMAN_RAM_TIMELINE_CAPABILITY in player.capabilities and
                        "destructible_contacts" in message):
                    candidate = message.get("destructible_contacts")
                    if (isinstance(candidate, list) and
                            len(candidate) <=
                            MAX_PENDING_PLAYER_DESTRUCTIBLE_CONTACTS):
                        raw_destructible_contacts = candidate
                if raw_destructible_contacts is not None:
                    by_seq = {}
                    conflicting = set()
                    for raw_contact in raw_destructible_contacts:
                        try:
                            raw_seq = _exact_int(
                                raw_contact.get("seq"), 1,
                                PROJECTILE_MAX_ID)
                        except (AttributeError, TypeError, ValueError,
                                OverflowError):
                            raw_seq = None
                        if raw_seq is None:
                            continue
                        previous = by_seq.get(raw_seq)
                        if previous is None:
                            by_seq[raw_seq] = raw_contact
                        elif previous != raw_contact:
                            conflicting.add(raw_seq)
                    for seq in sorted(by_seq):
                        if seq <= player.destructible_contact_seq:
                            continue
                        if seq != player.destructible_contact_seq + 1:
                            break
                        if (seq >
                                player.destructible_contact_resolved_seq +
                                MAX_PLAYER_DESTRUCTIBLE_INFLIGHT):
                            break
                        if (len(player.destructible_contacts) >=
                                MAX_PENDING_PLAYER_DESTRUCTIBLE_CONTACTS):
                            break
                        contact = (
                            None if seq in conflicting else
                            self._validated_player_destructible_contact(
                                by_seq[seq]))
                        sample = (None if contact is None else
                                  self._player_pose_for_destructible_contact(
                                      player, contact))
                        player.destructible_contact_seq = seq
                        if contact is None or sample is None:
                            # Invalid/stale rows are terminal so one bad head
                            # cannot keep every later exact contact blocked.
                            self._reject_player_destructible_contact(
                                player, seq)
                            continue
                        contact.update({
                            "input_seq": int(sample["input_seq"]),
                            "pose_time_us": int(sample["time_us"]),
                            "x": round(float(sample["x"]), 4),
                            "y": round(float(sample["y"]), 4),
                            "z": round(float(sample["z"]), 4),
                            "yaw": round(float(sample["yaw"]), 5),
                            "forward": round(float(sample["forward"]), 4),
                        })
                        player.destructible_contacts[seq] = contact
                        worker = self.simulation_worker
                        if (self.bot_authority_id ==
                                SIMULATION_WORKER_AUTHORITY_ID and
                                worker is not None and worker.connected):
                            # Do not wait for the next replica snapshot to
                            # carry a contact that has already been validated
                            # against this player's admitted pose.  The normal
                            # snapshot remains an idempotent retry if this
                            # latency-only relay cannot be queued.
                            worker.offer_reliable({
                                "type": "player_destructible_contact",
                                "protocol": PROTOCOL_VERSION,
                                "round_id": self.round_id,
                                "authority_epoch": self.authority_epoch,
                                "player": {
                                    "id": int(player.player_id),
                                    "vehicle": str(player.vehicle),
                                    "vehicle_compact_descr":
                                        player.vehicle_compact_descr,
                                    "destructible_contacts": [dict(contact)],
                                },
                            })
            if shell_selection is not None:
                player.shell_index = shell_selection[0]
                player.next_shell_index = shell_selection[1]
                player.shell_change_pending = shell_selection[2]
            if self.client_build != CLIENT_BUILD_0922:
                try:
                    fire_seq = int(message.get("fire_seq", player.fire_seq))
                except (TypeError, ValueError):
                    fire_seq = player.fire_seq
                if (fire_seq == player.fire_seq + 1 and
                        self._combat_accepting() and player.alive):
                    player.fire_seq = fire_seq
                    self.pending_events.append({
                        "kind": "shot",
                        "attacker": player.player_id,
                        "shot_seq": player.fire_seq,
                        "shell_index": player.shell_index,
                        "world_pose": player.client_position,
                        "x": round(player.x, 4),
                        "y": round(player.y, 4),
                        "z": round(player.z, 4),
                        "yaw": round(player.yaw, 5),
                        "aim_yaw": round(player.aim_yaw, 5),
                        "gun_pitch": round(player.gun_pitch, 5),
                    })
            if (("reported_health" in message or
                 "reported_critical" in message) and
                    self._combat_accepting() and player.alive):
                self._apply_reported_health(player, message)
            if not player.alive:
                # Late packets from the dead client's still-running input loop
                # must not drag its marker away from the server-owned wreck.
                player.forward = 0.0
                player.turn = 0.0
            return True

    @staticmethod
    def _siege_params(player):
        return SIEGE_VEHICLE_PARAMS.get(str(player.vehicle))

    @staticmethod
    def _engine_destroyed(player):
        critical = player.critical if isinstance(player.critical, dict) else {}
        if "engineHealth" in set(critical.get("destroyed") or ()):
            return True
        return any(
            isinstance(device, dict) and
            device.get("name") == "engineHealth" and
            device.get("state") == "destroyed"
            for device in critical.get("devices") or ())

    @staticmethod
    def _engine_damaged(player):
        critical = player.critical if isinstance(player.critical, dict) else {}
        return any(
            isinstance(device, dict) and
            device.get("name") == "engineHealth" and
            device.get("state") == "critical"
            for device in critical.get("devices") or ())

    def _request_siege_state(self, player, enabled):
        """Begin one #1513 four-state transition from an exact BOOL request."""
        params = self._siege_params(player)
        if (not player.participating or not isinstance(enabled, bool) or
                params is None or self._engine_destroyed(player) or
                player.siege_state in (
                    SIEGE_SWITCHING_ON, SIEGE_SWITCHING_OFF)):
            return False
        if enabled:
            if player.siege_state == SIEGE_ENABLED:
                return True
            next_state = SIEGE_SWITCHING_ON
            duration = params[0]
        else:
            if player.siege_state == SIEGE_DISABLED:
                return True
            next_state = SIEGE_SWITCHING_OFF
            duration = params[1]
        if self._engine_damaged(player):
            duration *= params[3]
        player.siege_state = next_state
        player.siege_transition_ticks = max(
            1, int(round(float(duration) * TICK_HZ)))
        player.forward = 0.0
        player.turn = 0.0
        player.speed = 0.0
        return True

    def _siege_speed_limit(self, player):
        params = self._siege_params(player)
        if player.siege_state in (
                SIEGE_SWITCHING_ON, SIEGE_SWITCHING_OFF):
            return 0.0
        if params is not None and player.siege_state == SIEGE_ENABLED:
            return float(params[2])
        return 200.0

    def _advance_siege_states(self):
        for player in self.players.values():
            if player.siege_state not in (
                    SIEGE_SWITCHING_ON, SIEGE_SWITCHING_OFF):
                player.siege_transition_ticks = 0
                continue
            player.siege_transition_ticks = max(
                0, int(player.siege_transition_ticks) - 1)
            if player.siege_transition_ticks != 0:
                continue
            player.siege_state = (
                SIEGE_ENABLED
                if player.siege_state == SIEGE_SWITCHING_ON
                else SIEGE_DISABLED)

    @staticmethod
    def _merge_player_critical_damage(player, proposal, delta):
        """Apply one monotonic worker delta over current canonical progress."""
        if proposal is None or delta is None:
            return None
        params = player.effective_params if isinstance(
            player.effective_params, dict) else {}
        profile = params.get("critical") if isinstance(params, dict) else None
        rows = profile.get("devices") if isinstance(profile, dict) else None
        if not isinstance(rows, list):
            raise ValueError("player critical profile is unavailable")
        maxima = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("player critical profile is invalid")
            name = str(row.get("name", ""))
            maximum = _finite_float(row.get("max_hp"), -1.0)
            if (name not in CRITICAL_DEVICE_NAMES or name in maxima or
                    maximum <= 0.0):
                raise ValueError("player critical profile is invalid")
            maxima[name] = maximum

        current = (copy.deepcopy(player.critical)
                   if isinstance(player.critical, dict) else {})
        current_devices = []
        by_name = {}
        for raw in current.get("devices") or ():
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            name = str(record.get("name", ""))
            if name in by_name:
                raise ValueError("canonical critical device is duplicated")
            by_name[name] = record
            current_devices.append(record)
        proposal_devices = {
            str(record.get("name")): record
            for record in proposal.get("devices") or ()
            if isinstance(record, dict)
        }
        events = []
        ammo_rack_destroyed = bool(
            current.get("ammo_rack_death", False))
        for change in delta["devices"]:
            name = change["name"]
            maximum = maxima.get(name)
            proposed = proposal_devices.get(name)
            if maximum is None or proposed is None:
                raise ValueError("critical delta is outside fitted profile")
            if abs(_finite_float(
                    proposed.get("max_hp"), -1.0) - maximum) > 0.001:
                raise ValueError("critical delta maximum disagrees")
            hp_loss = float(change["hp_loss"])
            if hp_loss > maximum + 0.001:
                raise ValueError("critical delta HP loss exceeds pool")
            record = by_name.get(name)
            if record is None:
                record = {
                    "name": name, "hp": maximum, "max_hp": maximum,
                    "state": "normal",
                }
                by_name[name] = record
                current_devices.append(record)
            if abs(_finite_float(
                    record.get("max_hp"), -1.0) - maximum) > 0.001:
                raise ValueError("canonical critical maximum disagrees")
            old_hp = _clamp(
                _finite_float(record.get("hp"), maximum), 0.0, maximum)
            new_hp = max(0.0, old_hp - hp_loss)
            old_state = str(record.get("state", "normal"))
            derived = device_damage.device_state(new_hp, maximum)
            state_rank = {"normal": 0, "critical": 1, "destroyed": 2}
            new_state = (old_state if state_rank.get(old_state, 0) >=
                         state_rank.get(derived, 0) else derived)
            record.update({
                "hp": round(new_hp, 3), "max_hp": round(maximum, 3),
                "state": new_state,
            })
            if old_state != new_state:
                events.append({
                    "kind": "device", "name": name,
                    "old_state": old_state, "state": new_state,
                    "cause": "shot",
                })
            if (name == "ammoBayHealth" and old_hp > 0.0 and
                    new_hp <= 0.0 and not ammo_rack_destroyed):
                ammo_rack_destroyed = True
                events.append({
                    "kind": "ammo_rack", "state": "destroyed",
                    "cause": "shot",
                })

        profile_roster = tuple(profile.get("crew_roster") or ())
        current_roster = tuple(current.get("crew_roster") or ())
        proposal_roster = tuple(proposal.get("crew_roster") or ())
        if ((current_roster and profile_roster and
             current_roster != profile_roster) or
                (proposal_roster and profile_roster and
                 proposal_roster != profile_roster) or
                (current_roster and proposal_roster and
                 current_roster != proposal_roster)):
            raise ValueError("critical crew roster changed mid-round")
        roster = profile_roster or current_roster or proposal_roster
        crew_ko = set(current.get("crew_ko") or ())
        proposal_crew = set(proposal.get("crew_ko") or ())
        for name in delta["crew_ko"]:
            if name not in proposal_crew or (roster and name not in roster):
                raise ValueError("critical crew delta disagrees")
            if name not in crew_ko:
                crew_ko.add(name)
                events.append({
                    "kind": "crew", "name": name,
                    "state": "destroyed", "cause": "shot",
                })
        burning = bool(current.get("fire", False))
        if delta["ignite"]:
            if not bool(proposal.get("fire", False)):
                raise ValueError("critical fire delta disagrees")
            if not burning:
                burning = True
                events.append({
                    "kind": "fire", "state": True, "cause": "shot",
                })
        candidate = {
            "devices": current_devices,
            "destroyed": sorted(
                record["name"] for record in current_devices
                if record.get("state") == "destroyed"),
            "crew_ko": sorted(crew_ko),
            "fire": burning,
            "ammo_rack_death": ammo_rack_destroyed,
            "events": events,
        }
        if roster:
            candidate["crew_roster"] = list(roster)
        return _critical_payload(candidate)

    @staticmethod
    def _commit_external_player_critical(player, critical, attacker=None):
        """Commit damage and open a new owner-report lineage.

        A later repair checkpoint may advance within this lineage, but a
        checkpoint computed before this damage cannot overwrite it.
        """
        if critical is None:
            return None
        candidate = _critical_state(critical)
        if (isinstance(player.critical, dict) and
                player.critical.get("crew_roster") and
                not candidate.get("crew_roster")):
            candidate["crew_roster"] = list(
                player.critical["crew_roster"])
        if candidate == player.critical:
            return {
                "critical_revision": player.critical_revision,
                "critical_base_revision":
                    player.critical_report_base_revision,
                "critical_ack_seq": player.critical_ack_seq,
            }
        was_burning = bool(
            isinstance(player.critical, dict) and
            player.critical.get("fire", False))
        player.critical = candidate
        burning = bool(player.critical.get("fire", False))
        if not was_burning and burning:
            player.combat_fire_elapsed = 0.0
            player.combat_fire_timer = 0.0
            if (isinstance(attacker, tuple) and len(attacker) == 2 and
                    attacker[0] in ("player", "bot") and
                    int(attacker[1]) > 0):
                player.fire_attacker_kind = str(attacker[0])
                player.fire_attacker_id = int(attacker[1])
        elif was_burning and not burning:
            player.combat_fire_elapsed = 0.0
            player.combat_fire_timer = 0.0
            player.fire_attacker_kind = ""
            player.fire_attacker_id = 0
        player.critical_revision += 1
        player.critical_report_base_revision = player.critical_revision
        player.critical_ack_seq = 0
        player.track_repair_fingerprints.clear()
        return {
            "critical_revision": player.critical_revision,
            "critical_base_revision":
                player.critical_report_base_revision,
            "critical_ack_seq": player.critical_ack_seq,
        }

    @staticmethod
    def _commit_player_critical_progress(player, critical):
        """Commit repair/equipment/fire progress within one damage lineage.

        Owner-timed track repair reports merge only their track rows into the
        current canonical payload.  Non-track progress therefore must retain
        the damage base and acknowledgement ledger instead of starving that
        independent owner CAS with a new lineage every server tick.
        """
        if critical is None:
            return None
        candidate = _critical_state(critical)
        if (isinstance(player.critical, dict) and
                player.critical.get("crew_roster") and
                not candidate.get("crew_roster")):
            candidate["crew_roster"] = list(
                player.critical["crew_roster"])
        if candidate == player.critical:
            return {
                "critical_revision": player.critical_revision,
                "critical_base_revision":
                    player.critical_report_base_revision,
                "critical_ack_seq": player.critical_ack_seq,
            }
        was_burning = bool(
            isinstance(player.critical, dict) and
            player.critical.get("fire", False))
        player.critical = candidate
        burning = bool(player.critical.get("fire", False))
        if was_burning and not burning:
            player.combat_fire_elapsed = 0.0
            player.combat_fire_timer = 0.0
            player.fire_attacker_kind = ""
            player.fire_attacker_id = 0
        player.critical_revision += 1
        return {
            "critical_revision": player.critical_revision,
            "critical_base_revision":
                player.critical_report_base_revision,
            "critical_ack_seq": player.critical_ack_seq,
        }

    @staticmethod
    def _commit_reported_player_critical(player, critical, message):
        """Accept one monotonic checkpoint from the owning client.

        Socket delivery is not an acknowledgement.  The accepted sequence is
        returned in events and snapshots; only that canonical echo permits the
        client to retire its pending report.
        """
        if critical is None:
            return None, False
        try:
            base_revision = int(message.get(
                "reported_critical_base_revision"))
            report_seq = int(message.get("reported_critical_seq"))
        except (TypeError, ValueError):
            return None, False
        if base_revision != player.critical_report_base_revision:
            return None, False
        if report_seq <= player.critical_ack_seq:
            return {
                "critical_revision": player.critical_revision,
                "critical_base_revision":
                    player.critical_report_base_revision,
                "critical_ack_seq": player.critical_ack_seq,
            }, True
        player.critical = _critical_state(critical)
        player.critical_revision += 1
        player.critical_ack_seq = report_seq
        return {
            "critical_revision": player.critical_revision,
            "critical_base_revision":
                player.critical_report_base_revision,
            "critical_ack_seq": player.critical_ack_seq,
        }, True

    def report_track_repair(self, player_id, message):
        """CAS one owner-timed repair or acknowledge canonical convergence."""
        with self.lock:
            player = self.players.get(player_id)
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or player is None or
                    not player.connected or not player.participating or
                    not player.alive):
                return False
            base_revision = _exact_int(
                message.get("critical_base_revision"), 0,
                PROJECTILE_MAX_ID)
            repair_seq = _exact_int(
                message.get("repair_seq"), 1, PROJECTILE_MAX_ID)
            try:
                rows = _track_repair_rows(message.get("tracks"))
            except ValueError:
                return False
            if (base_revision is None or repair_seq is None or
                    base_revision != player.critical_report_base_revision):
                return False
            fingerprint = tuple(
                (row["name"], row["hp"], row["max_hp"], row["state"])
                for row in rows)
            current = (player.critical
                       if isinstance(player.critical, dict) else {})
            devices = [dict(record)
                       for record in current.get("devices") or ()
                       if isinstance(record, dict)]
            by_name = {
                str(record.get("name")): (index, record)
                for index, record in enumerate(devices)
                if record.get("name") is not None
            }
            if any(row["name"] not in by_name for row in rows):
                return False
            if repair_seq <= player.critical_ack_seq:
                accepted = player.track_repair_fingerprints.get(repair_seq)
                return accepted is None or accepted == fingerprint

            destroyed = set(current.get("destroyed") or ())
            events = []
            improved = False
            converged = set()
            for row in rows:
                index, previous = by_name[row["name"]]
                old_hp = _finite_float(previous.get("hp"), -1.0)
                old_maximum = _finite_float(
                    previous.get("max_hp"), -1.0)
                if old_hp < 0.0 or old_maximum <= 0.0:
                    return False
                if (previous.get("state") in ("normal", "critical") and
                        row["name"] not in destroyed):
                    converged.add(row["name"])
                    continue
                if (previous.get("state") != "destroyed" or
                        row["name"] not in destroyed or
                        abs(old_maximum - row["max_hp"]) > 0.001 or
                        row["hp"] + 0.001 < old_hp):
                    return False
                if (row["hp"] > old_hp + 0.001 or
                        row["state"] == "critical"):
                    improved = True
                devices[index] = dict(row)
                if row["state"] == "critical":
                    destroyed.discard(row["name"])
                    events.append({
                        "kind": "device",
                        "name": row["name"],
                        "old_state": "destroyed",
                        "state": "critical",
                        "cause": "repair",
                    })
            if not improved and len(converged) != len(rows):
                return False
            if not improved:
                candidate = current
            else:
                candidate = dict(current)
                candidate["devices"] = devices
                candidate["destroyed"] = sorted(destroyed)
                candidate["events"] = events
            try:
                candidate = _critical_payload(candidate)
            except ValueError:
                return False
            commit, accepted = self._commit_reported_player_critical(
                player, candidate, {
                    "reported_critical_base_revision": base_revision,
                    "reported_critical_seq": repair_seq,
                })
            if not accepted or int(commit["critical_ack_seq"]) != repair_seq:
                return False
            player.track_repair_fingerprints[repair_seq] = fingerprint
            while len(player.track_repair_fingerprints) > 64:
                player.track_repair_fingerprints.popitem(last=False)
            return True

    @staticmethod
    def _finish_equipment_intent(player, intent_seq, accepted, reason):
        player.equipment_intent_result = {
            "intent_seq": int(intent_seq),
            "accepted": bool(accepted),
            "reason": str(reason or "")[:64],
        }
        return True

    def submit_equipment_intent(self, player_id, message):
        """Commit one visible trigger against the server-owned kit ledger."""
        with self.lock:
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    message.get("type") != "equipment_intent" or
                    set(message) != {
                        "type", "round_id", "intent_seq", "equipment_id",
                        "activation_code", "selected",
                        "requested_active"}):
                return False
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return False
            try:
                intent_seq = _exact_int(
                    message.get("intent_seq"), 1, PROJECTILE_MAX_ID)
                equipment_id = _exact_int(
                    message.get("equipment_id"), 1, 65535)
                activation_code = _exact_int(
                    message.get("activation_code"), 1,
                    PROJECTILE_MAX_ID)
            except (TypeError, ValueError, OverflowError):
                return False
            if (intent_seq is None or equipment_id is None or
                    activation_code is None or
                    activation_code & 65535 != equipment_id):
                return False
            selected = message.get("selected")
            if selected is not None:
                if (not isinstance(selected, str) or not selected or
                        len(selected) > 64 or
                        selected not in CRITICAL_DEVICE_NAMES and
                        selected not in CRITICAL_CREW_NAMES):
                    return False
            requested_active = message.get("requested_active")
            if (requested_active is not None and
                    not isinstance(requested_active, bool)):
                return False
            normalized = {
                "intent_seq": intent_seq,
                "equipment_id": equipment_id,
                "activation_code": activation_code,
                "selected": selected,
                "requested_active": requested_active,
            }
            fingerprint = _message_fingerprint(normalized)
            previous = player.equipment_intent_fingerprints.get(intent_seq)
            if previous is not None:
                return previous == fingerprint
            if intent_seq != player.equipment_intent_seq + 1:
                return False
            player.equipment_intent_seq = intent_seq
            player.equipment_intent_fingerprints[intent_seq] = fingerprint
            while (len(player.equipment_intent_fingerprints) >
                   MAX_PLAYER_EQUIPMENT_FINGERPRINTS):
                player.equipment_intent_fingerprints.popitem(last=False)
            if (not self._combat_accepting() or
                    self.battle_result is not None or
                    not player.participating or not player.alive):
                return self._finish_equipment_intent(
                    player, intent_seq, False, "vehicle_not_alive")
            equipment = next((
                value for value in player.equipment_states
                if int(value.contract.get("id", 0)) == equipment_id), None)
            if equipment is None:
                return self._finish_equipment_intent(
                    player, intent_seq, False, "equipment_not_mounted")
            kind = str(equipment.contract.get("kind") or "")
            repair_all = bool(equipment.contract.get("repairAll", False))
            extra_index = activation_code >> 16
            if ((kind == "rpm_limiter" and requested_active is None) or
                    (kind != "rpm_limiter" and
                     requested_active is not None)):
                return self._finish_equipment_intent(
                    player, intent_seq, False, "invalid_activation_mode")
            if (repair_all and
                    (kind not in ("repairkit", "medkit") or
                     extra_index != 1 or selected is not None)):
                return self._finish_equipment_intent(
                    player, intent_seq, False, "invalid_activation_code")
            if kind in ("repairkit", "medkit") and not repair_all:
                targets = {
                    int(row.get("index")): str(row.get("name"))
                    for row in ((player.effective_params.get("critical") or
                                 {}).get("activation_targets") or ())
                    if isinstance(row, dict)
                }
                if extra_index <= 0 or targets.get(extra_index) != selected:
                    return self._finish_equipment_intent(
                        player, intent_seq, False,
                        "invalid_activation_code")
            elif kind == "rpm_limiter":
                if (extra_index not in (0, 1) or
                        bool(extra_index) != requested_active):
                    return self._finish_equipment_intent(
                        player, intent_seq, False,
                        "invalid_activation_code")
            elif not repair_all and extra_index != 0:
                return self._finish_equipment_intent(
                    player, intent_seq, False, "invalid_activation_code")
            if ((kind == "repairkit" and
                 ((repair_all and selected is not None) or
                  (not repair_all and
                   selected not in CRITICAL_DEVICE_NAMES))) or
                    (kind == "medkit" and
                     ((repair_all and selected is not None) or
                      (not repair_all and
                       selected not in CRITICAL_CREW_NAMES))) or
                    (kind not in ("repairkit", "medkit") and
                     selected is not None)):
                return self._finish_equipment_intent(
                    player, intent_seq, False, "invalid_equipment_target")
            if (kind == "extinguisher" and
                    bool(equipment.contract.get("autoactivate", False))):
                return self._finish_equipment_intent(
                    player, intent_seq, False, "automatic_only")
            critical = (player.critical
                        if isinstance(player.critical, dict) else {})
            stun_state = self._vehicle_stun_state(("player", player_id))
            stunned = bool(
                stun_state is not None and
                int(stun_state.get("end", 0)) > self._server_time_ms())
            effect = equipment_mechanics.effect_policy(
                equipment, critical, selected=selected,
                requested_active=requested_active,
                active=equipment.active, stunned=stunned)
            now = float(self.tick) / TICK_HZ
            player.equipment_clock = now
            if effect is None or not equipment.ready(now):
                return self._finish_equipment_intent(
                    player, intent_seq, False, "equipment_ineligible")
            payload = None
            if effect.get("action") != "set_rpm_limiter":
                payload = player_critical_mechanics.apply_equipment(
                    player, effect, now)
                if payload is None and not effect.get("clearStun", False):
                    return self._finish_equipment_intent(
                        player, intent_seq, False, "equipment_no_effect")
            committed = equipment.activate(
                now, critical, selected=selected,
                requested_active=requested_active, stunned=stunned)
            if committed != effect:
                raise RuntimeError(
                    "canonical player equipment commit diverged")
            if payload is not None:
                self._commit_player_critical_progress(
                    player, _critical_payload(payload))
            if effect.get("clearStun", False):
                if not self._clear_vehicle_stun(("player", player_id)):
                    raise RuntimeError("canonical medkit stun clear diverged")
            player.equipment_revision += 1
            return self._finish_equipment_intent(
                player, intent_seq, True, "")

    def _tick_player_critical(self, dt):
        """Advance non-track repairs and automatic equipment canonically."""
        if not self._combat_accepting() or self.battle_result is not None:
            return 0
        changed = 0
        now = float(self.tick) / TICK_HZ
        for player in list(self.players.values()):
            if (not player.connected or not player.participating or
                    not player.alive):
                continue
            player.equipment_clock = now
            for equipment in player.equipment_states:
                critical = (player.critical if isinstance(
                    player.critical, dict) else {})
                effect = equipment.poll_auto(now, critical)
                if effect is None:
                    continue
                payload = player_critical_mechanics.apply_equipment(
                    player, effect, now)
                if payload is None:
                    raise RuntimeError(
                        "canonical automatic equipment had no effect")
                self._commit_player_critical_progress(
                    player, _critical_payload(payload))
                player.equipment_revision += 1
                changed += 1
            payload = player_critical_mechanics.advance_critical(
                player, max(0.0, float(dt)), now)
            if payload is not None:
                self._commit_player_critical_progress(
                    player, _critical_payload(payload))
                changed += 1
        return changed

    def _tick_player_fire(self, dt):
        """Advance participant fires against the canonical health ledger."""
        if not self._combat_accepting() or self.battle_result is not None:
            return 0
        changed = 0
        now = float(self.tick) / TICK_HZ
        for player in list(self.players.values()):
            burning = bool(
                isinstance(player.critical, dict) and
                player.critical.get("fire", False))
            if (not player.connected or not player.participating or
                    not player.alive or not burning):
                if not burning:
                    player.combat_fire_elapsed = 0.0
                    player.combat_fire_timer = 0.0
                    player.fire_attacker_kind = ""
                    player.fire_attacker_id = 0
                continue
            attacker_kind = str(player.fire_attacker_kind or "")
            attacker_id = int(player.fire_attacker_id or 0)
            attacker = ((attacker_kind, attacker_id)
                        if attacker_kind in ("player", "bot") and
                        attacker_id > 0 else None)
            if attacker is None:
                continue
            result = player_critical_mechanics.advance_fire(
                player, max(0.0, float(dt)), now)
            if not isinstance(result, dict):
                continue
            critical_before = player.critical
            critical = result.get("critical")
            damage = min(
                max(0, int(result.get("damage", 0))), player.health)
            player.combat_fire_elapsed = max(
                0.0, float(result.get("fire_elapsed", 0.0)))
            player.combat_fire_timer = max(
                0.0, float(result.get("fire_timer", 0.0)))
            critical_commit = self._commit_player_critical_progress(
                player, _critical_payload(critical)
                if critical is not None else None)
            if damage <= 0 and critical is None:
                continue
            player.health -= damage
            player.alive = player.health > 0
            player.display_health = player.health
            player.death_reason = 1 if not player.alive else 0
            if damage > 0:
                self._drop_capture_for_vehicle("player", player.player_id)
            self._record_damage(
                attacker, ("player", player.player_id), damage,
                critical_before)
            event = {
                "kind": ("hit" if attacker_kind == "player" else
                         "bot_human_hit"),
                "target": player.player_id,
                "damage": damage,
                "health": player.health,
                "dead": not player.alive,
                "attack_reason": 1,
                "death_reason": player.death_reason,
                "source": "fire",
            }
            if attacker_kind == "player":
                event["attacker"] = attacker_id
            elif attacker_kind == "bot":
                event["attacker_bot"] = attacker_id
            if critical is not None:
                event["critical"] = player.critical
                if critical_commit:
                    event.update(critical_commit)
            self.pending_events.append(event)
            changed += int(damage > 0)
            if not player.alive:
                player.forward = 0.0
                player.turn = 0.0
                player.speed = 0.0
                player.pending_fire_intents.clear()
                if attacker is not None:
                    player.death_attacker_kind = attacker_kind
                    player.death_attacker_id = attacker_id
                    self._record_frag(
                        attacker_kind, attacker_id, player.team,
                        "player", player.player_id)
                player.fire_attacker_kind = ""
                player.fire_attacker_id = 0
                if self._maybe_finish_battle():
                    break
            elif not bool(player.critical.get("fire", False)):
                player.fire_attacker_kind = ""
                player.fire_attacker_id = 0
        return changed

    def _apply_reported_health(self, player, message):
        """Relay legacy 0.8.2 client-simulated damage.

        The #1513 path rejects this boundary: visible modern clients are never
        canonical combat authorities. Health reports may only move downward
        during a legacy round.
        """
        if self.client_build == CLIENT_BUILD_0922 or not player.alive:
            return False
        try:
            critical = _critical_payload(message.get("reported_critical"))
        except ValueError:
            return False
        health = max(0, min(int(_finite_float(
            message.get("reported_health"), player.health)),
            player.max_health))
        health = min(health, player.health)
        stored_critical = _critical_state(critical)
        critical_before = player.critical
        old_discrete = _critical_discrete_state(player.critical)
        new_discrete = _critical_discrete_state(stored_critical)
        critical_damage = _critical_damage_transition(
            critical_before, critical)
        critical_event_changed = (
            stored_critical is not None and
            (new_discrete != old_discrete or bool(critical.get("events")) or
             critical_damage))
        critical_commit = None
        if stored_critical is not None:
            if self.client_build == CLIENT_BUILD_0922:
                critical_commit, accepted = (
                    self._commit_reported_player_critical(
                        player, critical, message))
                if not accepted:
                    return False
                if (int(critical_commit["critical_ack_seq"]) !=
                        int(message.get("reported_critical_seq"))):
                    return True
            else:
                # The completed 0.8.2 package predates revisioned repair
                # reports.  Rooms are build-homogeneous, so preserving its
                # protocol does not weaken the strict #1513 path.
                player.critical = stored_critical
        if health == player.health and not critical_event_changed:
            return stored_critical is not None
        was_alive = player.alive
        damage = player.health - health
        player.health = health
        # The cause is a client claim, so only the victim's loss is attributed.
        self._record_damage(
            None, ("player", player.player_id), damage, critical_before)
        capture_reset = bool(damage > 0 or critical_damage)
        if capture_reset:
            self._drop_capture_for_vehicle("player", player.player_id)
        try:
            reason = max(0, min(int(message.get("reported_reason", 0)), 255))
        except (TypeError, ValueError):
            reason = 0
        if health == 0:
            player.alive = False
            player.death_reason = reason
            display_health = max(0, min(int(_finite_float(
                message.get("reported_display_health"), health)),
                player.max_health))
            player.display_health = display_health
        event = {
            "kind": "health",
            "target": player.player_id,
            "damage": damage,
            "health": player.health,
            "dead": not player.alive,
            "source": "client_simulation",
            "attack_reason": reason,
            "death_reason": player.death_reason if not player.alive else 0,
            "display_health": (player.display_health
                               if not player.alive else player.health),
        }
        if critical is not None:
            event["critical"] = critical
            if critical_commit is not None:
                event.update(critical_commit)
        try:
            attacker_id = int(message.get("reported_attacker", 0))
        except (TypeError, ValueError):
            attacker_id = 0
        try:
            attacker_bot = int(message.get("reported_attacker_bot", 0))
        except (TypeError, ValueError):
            attacker_bot = 0
        reported_attacker_kind = None
        reported_attacker_id = 0
        if attacker_id in self.players:
            reported_attacker_kind = "player"
            reported_attacker_id = attacker_id
        elif attacker_bot in self.bot_states:
            reported_attacker_kind = "bot"
            reported_attacker_id = attacker_bot
        # The owner may retain the last attacker so a locally simulated fatal
        # tick can preserve the death ledger.  That attribution is ledger-only:
        # client_simulation is an explicit non-attack wire cause and must never
        # expose attacker fields to the server or client event validators.
        self.pending_events.append(event)
        if (was_alive and not player.alive and
                reported_attacker_kind is not None):
            player.death_attacker_kind = reported_attacker_kind
            player.death_attacker_id = int(reported_attacker_id)
            self._record_frag(
                reported_attacker_kind, reported_attacker_id, player.team,
                "player", player.player_id)
        self._maybe_finish_battle()
        return True

    def report_hit(self, player_id, message):
        """Apply a map/armor hit resolved by the firing 0.8.2 client.

        The server validates identity, team, range and one report per target
        per shot, then
        owns the shared HP result.  This reuses the existing client armor and
        shell collision logic instead of the old fixed 100-HP cone test.
        """
        with self.lock:
            if self.client_build == CLIENT_BUILD_0922:
                return False
            attacker = self.players.get(player_id)
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    attacker is None or not attacker.connected or not attacker.alive):
                return False
            if not all(key in message for key in
                       ("target", "shot_seq", "damage")):
                return False
            if (not _has_finite_fields(
                    message, ("target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return False
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError:
                return False
            try:
                shot_seq = int(message.get("shot_seq", 0))
                target_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return False
            splash = bool(message.get("splash", False))
            hit_key = (("shot", shot_seq, "player", target_id)
                       if splash else ("shot", shot_seq))
            if shot_seq <= 0 or shot_seq > attacker.fire_seq or hit_key in attacker.reported_hits:
                return False
            target = self.players.get(target_id)
            if target is None or not target.connected or not target.alive:
                return False
            if target.player_id == attacker.player_id and not splash:
                return False
            distance = math.hypot(target.x - attacker.x, target.z - attacker.z)
            if distance > 5000.0:
                return False
            modern_proposal = (
                self.client_build == CLIENT_BUILD_0922 and
                critical is not None)
            critical_accepted = True
            hull_damage = None
            if modern_proposal:
                try:
                    hull_damage, critical_accepted = (
                        _critical_proposal_admission(
                            message,
                            target.critical_report_base_revision,
                            target.critical_ack_seq))
                except ValueError:
                    return False
            attacker.reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            if modern_proposal and not critical_accepted:
                damage = hull_damage
            applied_damage = min(damage, target.health)
            target.health -= applied_damage
            if target.health == 0:
                target.alive = False
            target.display_health = target.health
            admitted_critical = (
                critical if not modern_proposal or critical_accepted else None)
            critical_before = target.critical
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical,
                ("player", attacker.player_id))
            capture_reset = bool(
                applied_damage > 0 or _critical_damage_transition(
                    critical_before, admitted_critical))
            if capture_reset:
                self._drop_capture_for_vehicle("player", target_id)
            try:
                shot_result = max(0, min(int(message.get("shot_result", 2)), 2))
            except (TypeError, ValueError):
                shot_result = 2
            event = {
                "kind": "hit",
                "attacker": attacker.player_id,
                "target": target.player_id,
                "shot_seq": shot_seq,
                "shell_index": attacker.shell_index,
                "shot_result": shot_result,
                "damage": applied_damage,
                "health": target.health,
                "dead": not target.alive,
                "attack_reason": 0,
                "death_reason": 0,
                "source": "shot",
                "splash": splash,
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            }
            if critical is not None:
                if modern_proposal:
                    event["critical_accepted"] = critical_accepted
                if admitted_critical is not None:
                    event["critical"] = admitted_critical
                    event.update(critical_commit)
                elif modern_proposal:
                    event["critical_reject_reason"] = (
                        "stale_target_state")
                    event.update({
                        "critical_revision": target.critical_revision,
                        "critical_base_revision":
                            target.critical_report_base_revision,
                        "critical_ack_seq": target.critical_ack_seq,
                    })
            self.pending_events.append(event)
            if not target.alive:
                target.death_attacker_kind = "player"
                target.death_attacker_id = int(attacker.player_id)
                self._record_frag(
                    "player", attacker.player_id, target.team,
                    "player", target.player_id)
            self._maybe_finish_battle()
            return True

    def _vehicle_team(self, kind, vehicle_id):
        if kind == "player":
            player = self.players.get(int(vehicle_id))
            if player is not None:
                return int(player.team)
            participant = self._frozen_player_participant(vehicle_id)
            return (int(participant.get("team", 0))
                    if participant is not None else 0)
        state = self.bot_states.get(int(vehicle_id))
        return int(state.get("team", 0)) if state is not None else 0

    def _vehicle_stun_state(self, target):
        kind, vehicle_id = str(target[0]), int(target[1])
        if kind == "player":
            vehicle = self.players.get(vehicle_id)
            if vehicle is None:
                return None
            return {
                "end": int(vehicle.stun_end_server_time_ms),
                "attacker_kind": str(vehicle.stun_attacker_kind),
                "attacker_id": int(vehicle.stun_attacker_id),
                "alive": bool(vehicle.alive),
            }
        if kind != "bot":
            return None
        vehicle = self.bot_states.get(vehicle_id)
        if vehicle is None:
            return None
        return {
            "end": int(vehicle.get("stun_end_server_time_ms", 0)),
            "attacker_kind": str(vehicle.get("stun_attacker_kind", "")),
            "attacker_id": int(vehicle.get("stun_attacker_id", 0)),
            "alive": bool(vehicle.get("alive")),
        }

    def _write_vehicle_stun(self, target, end, attacker):
        kind, vehicle_id = str(target[0]), int(target[1])
        attacker_kind = str(attacker[0]) if attacker is not None else ""
        attacker_id = int(attacker[1]) if attacker is not None else 0
        if kind == "player":
            vehicle = self.players.get(vehicle_id)
            if vehicle is None:
                return False
            vehicle.stun_end_server_time_ms = int(end)
            vehicle.stun_attacker_kind = attacker_kind
            vehicle.stun_attacker_id = attacker_id
            return True
        if kind != "bot":
            return False
        vehicle = self.bot_states.get(vehicle_id)
        if vehicle is None:
            return False
        vehicle["stun_end_server_time_ms"] = int(end)
        vehicle["stun_attacker_kind"] = attacker_kind
        vehicle["stun_attacker_id"] = attacker_id
        return True

    def _set_canonical_stun(self, attacker, target, end_server_time_ms):
        """Carry one internal projectile resolver's final stun state.

        The resolver owns duration and overlap semantics. This layer only
        stores the supplied round-relative end time and publishes it.
        """
        end_server_time_ms = _exact_int(
            end_server_time_ms, 1,
            int(round((PREBATTLE_SECONDS + BATTLE_DURATION_SECONDS) *
                      1000.0)))
        state = self._vehicle_stun_state(target)
        if state is None or not state["alive"]:
            return False
        attacker = (str(attacker[0]), int(attacker[1]))
        if self._vehicle_team(*attacker) not in (1, 2):
            return False
        bot = (self.bot_states.get(int(target[1]))
               if str(target[0]) == "bot" else None)
        combat_before = (self._bot_combat_signature(bot)
                         if bot is not None else None)
        if not self._write_vehicle_stun(target, end_server_time_ms, attacker):
            return False
        if bot is not None:
            self._commit_external_bot_combat(bot, combat_before)
        self.pending_events.append({
            "kind": "stun", "active": True,
            "target_kind": str(target[0]), "target_id": int(target[1]),
            "attacker_kind": attacker[0], "attacker_id": attacker[1],
            "stun_end_server_time_ms": end_server_time_ms,
        })
        return True

    def _clear_vehicle_stun(self, target):
        state = self._vehicle_stun_state(target)
        if state is None or not state["end"]:
            return False
        bot = (self.bot_states.get(int(target[1]))
               if str(target[0]) == "bot" else None)
        combat_before = (self._bot_combat_signature(bot)
                         if bot is not None else None)
        if not self._write_vehicle_stun(target, 0, None):
            return False
        if bot is not None:
            self._commit_external_bot_combat(bot, combat_before)
        self.pending_events.append({
            "kind": "stun", "active": False,
            "target_kind": str(target[0]), "target_id": int(target[1]),
            "stun_end_server_time_ms": 0,
        })
        return True

    def _expire_stuns(self, now_ms=None):
        now = (self._server_time_ms() if now_ms is None else
               _exact_int(now_ms, 0))
        targets = [("player", player_id)
                   for player_id in sorted(self.players)]
        targets.extend(("bot", bot_id) for bot_id in sorted(self.bot_states))
        expired = 0
        for target in targets:
            state = self._vehicle_stun_state(target)
            if (state is not None and state["end"] and
                    (not state["alive"] or state["end"] <= now) and
                    self._clear_vehicle_stun(target)):
                expired += 1
        return expired

    def _active_stun_assister(self, target):
        state = self._vehicle_stun_state(target)
        if (state is None or not state["end"] or
                state["end"] <= self._server_time_ms() or
                state["attacker_kind"] not in ("player", "bot") or
                state["attacker_id"] <= 0):
            return None
        return state["attacker_kind"], state["attacker_id"]

    def _statistics_row(self, kind, vehicle_id):
        """Return this round's mutable statistics row for one vehicle."""
        key = (str(kind), int(vehicle_id))
        row = self.vehicle_statistics.get(key)
        if row is None:
            row = {
                "actor_kind": key[0], "actor_id": key[1],
                "team": self._vehicle_team(*key),
                "shots_fired": 0, "shots_hit": 0, "shots_penetrated": 0,
                "damage_dealt": 0, "damage_received": 0,
                "damage_blocked": 0, "damage_assisted_track": 0,
                "damage_assisted_radio": 0, "damage_assisted_stun": 0,
                "kills": 0,
            }
            self.vehicle_statistics[key] = row
        elif not row["team"]:
            row["team"] = self._vehicle_team(*key)
        return row

    def _statistics_interaction(self, actor, target):
        """Return one bounded per-target row owned by ``actor``."""
        actor = (str(actor[0]), int(actor[1]))
        self._statistics_row(*actor)
        interactions = self.vehicle_interactions.setdefault(actor, {})
        target = (str(target[0]), int(target[1]))
        key = "%s:%d" % target
        interaction = interactions.get(key)
        if interaction is None:
            interaction = {
                "target_kind": target[0], "target_id": target[1],
            }
            for name, (minimum, unused_maximum) in (
                    RESULT_INTERACTION_LIMITS.items()):
                interaction[name] = minimum if name == "death_reason" else 0
            interactions[key] = interaction
        return interaction

    def _increment_interaction(self, actor, target, name, amount=1):
        minimum, maximum = RESULT_INTERACTION_LIMITS[name]
        interaction = self._statistics_interaction(actor, target)
        interaction[name] = max(minimum, min(
            maximum, int(interaction.get(name, 0)) + int(amount)))
        return interaction[name]

    def _receipt_interactions(self, actor):
        """Project mutable interaction maps into stable receipt rows."""
        actor = (str(actor[0]), int(actor[1]))
        interactions = self.vehicle_interactions.get(actor, {})
        if not isinstance(interactions, dict):
            return []
        return [dict(value) for value in sorted(
            interactions.values(), key=lambda value: (
                0 if value.get("target_kind") == "player" else 1,
                int(value.get("target_id", 0))))]

    def update_spotted_targets(self, player_id, message):
        """Store one player's own spotted set for assist accounting only.

        The claim never changes visibility, damage or any authority decision;
        it only decides who earns radio assist for another vehicle's damage.
        """
        with self.lock:
            if self.client_build == CLIENT_BUILD_0922:
                return False
            player = self.players.get(player_id)
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or player is None or
                    not player.connected or not player.participating):
                return False
            raw = message.get("targets")
            if (not isinstance(raw, list) or
                    len(raw) > len(self.players) + len(self.bot_states)):
                return False
            spotted = set()
            for entry in raw:
                if (not isinstance(entry, dict) or
                        set(entry) != {"target_kind", "target_id"}):
                    return False
                kind = entry.get("target_kind")
                try:
                    target_id = _exact_int(
                        entry.get("target_id"), 1, PROJECTILE_MAX_ID)
                except ValueError:
                    return False
                if kind == "player":
                    target = self.players.get(target_id)
                    team = target.team if target is not None else 0
                elif kind == "bot":
                    target = self.bot_states.get(target_id)
                    team = int(target.get("team", 0)) if target else 0
                else:
                    return False
                if target is None or team == player.team:
                    return False
                spotted.add((kind, target_id))
            self.player_spotted[player_id] = frozenset(spotted)
            reporter = ("player", int(player_id))
            for target in spotted:
                interaction = self._statistics_interaction(reporter, target)
                if not interaction["spotted"]:
                    interaction["spotted"] = 1
                    row = self._statistics_row(*reporter)
                    row["spotted"] = int(row.get("spotted", 0)) + 1
            return True

    def _radio_assisters(self, attacker, target, target_team):
        """Return live direct observers whose set contains this target."""
        result = []
        for reporter_id in sorted(self.player_spotted):
            reporter = self.players.get(reporter_id)
            if (reporter is None or not reporter.connected or
                    not reporter.alive or reporter.team == target_team or
                    ("player", reporter_id) == attacker or
                    target not in self.player_spotted[reporter_id]):
                continue
            result.append(("player", reporter_id))
        for bot_id in sorted(self.bot_spotted):
            bot = self.bot_states.get(bot_id)
            reporter = ("bot", int(bot_id))
            if (bot is None or not bot.get("alive") or
                    int(bot.get("team", 0)) == target_team or
                    reporter == attacker or
                    target not in self.bot_spotted[bot_id]):
                continue
            result.append(reporter)
        return result

    def _vehicle_statistics_payload(self):
        return [dict(self.vehicle_statistics[key])
                for key in sorted(self.vehicle_statistics)]

    def _record_damage(self, attacker, target, damage, target_critical,
                       attacker_team=None):
        """Attribute one applied damage amount and every assist it earned.

        ``attacker`` and ``target`` are ``(kind, id)`` pairs; ``attacker`` is
        None when the server does not own the cause.  ``target_critical`` is
        the target's critical state before this damage.
        """
        damage = int(damage)
        if damage <= 0:
            return
        self._statistics_row(*target)["damage_received"] += damage
        target_team = self._vehicle_team(*target)
        if attacker is None:
            return
        if attacker_team is None:
            attacker_team = self._vehicle_team(*attacker)
        else:
            attacker_team = int(attacker_team)
        if attacker_team == target_team:
            return
        if target[0] == "bot":
            self.bot_planner.report_damage(
                target[1], attacker[0], attacker[1], damage,
                self._monotonic())
        attacker_row = self._statistics_row(*attacker)
        if not attacker_row["team"] and attacker_team in (1, 2):
            attacker_row["team"] = attacker_team
        attacker_row["damage_dealt"] += damage
        self._increment_interaction(
            attacker, target, "damage", damage)
        self._increment_interaction(
            target, attacker, "damage_received", damage)
        credits = []
        holder = self.track_immobilisers.get(target)
        if (holder is not None and holder != attacker and
                self._vehicle_team(*holder) != target_team and
                _destroyed_tracks(target_critical)):
            credits.append(("track", holder))
        holder = self._active_stun_assister(target)
        if (holder is not None and holder != attacker and
                self._vehicle_team(*holder) != target_team):
            credits.append(("stun", holder))
        credits.extend(
            ("radio", assister) for assister in
            self._radio_assisters(attacker, target, target_team))
        for category, assister in credits:
            self._statistics_row(*assister)[
                "damage_assisted_%s" % category] += damage
            self._increment_interaction(
                assister, target, "assist_%s" % category, damage)
            self.pending_events.append({
                "kind": "assist",
                "category": category,
                "assister_kind": assister[0], "assister_id": assister[1],
                "attacker_kind": attacker[0], "attacker_id": attacker[1],
                "target_kind": target[0], "target_id": target[1],
                "damage": damage,
            })

    def _frozen_player_participant(self, player_id):
        """Return one immutable-round player identity after socket removal."""
        player_id = int(player_id)
        for participant in self.round_participants.values():
            if int(participant.get("player_id", 0)) == player_id:
                return participant
        return None

    def _record_frag(self, attacker_kind, attacker_id, victim_team,
                     victim_kind, victim_id, attacker_team=None):
        """Copy 0.8.2 +1 enemy / -1 ally frag and team-killer law."""
        if (attacker_kind == victim_kind and
                int(attacker_id) == int(victim_id)):
            return False
        if attacker_kind == "player":
            actor = self.players.get(int(attacker_id))
            participant = self._frozen_player_participant(attacker_id)
            if actor is None and participant is None:
                return False
            actor_team = int(
                actor.team if actor is not None else participant["team"])
            if (attacker_team is not None and
                    int(attacker_team) != actor_team):
                return False
            delta = -1 if actor_team == int(victim_team) else 1
            if actor is not None:
                actor.frags += delta
                if delta < 0:
                    actor.team_killer = True
                frags = actor.frags
                team_killer = actor.team_killer
            else:
                participant["frags"] = int(
                    participant.get("frags", 0)) + delta
                if delta < 0:
                    participant["team_killer"] = True
                frags = participant["frags"]
                team_killer = bool(participant.get("team_killer", False))
            if participant is not None:
                participant["frags"] = int(frags)
                participant["team_killer"] = bool(team_killer)
        elif attacker_kind == "bot":
            actor = self.bot_states.get(int(attacker_id))
            if actor is None:
                return False
            actor_team = int(actor.get("team", 0))
            delta = -1 if actor_team == int(victim_team) else 1
            actor["frags"] = int(actor.get("frags", 0)) + delta
            frags = actor["frags"]
            # The copied 0.8.2 bot path adjusts frags but only the human
            # player path publishes the blue/team-killer state.
            team_killer = False
        else:
            return False
        if delta > 0:
            row = self._statistics_row(attacker_kind, attacker_id)
            if not row["team"] and actor_team in (1, 2):
                row["team"] = actor_team
            row["kills"] += 1
            attacker = (str(attacker_kind), int(attacker_id))
            victim = (str(victim_kind), int(victim_id))
            interaction = self._statistics_interaction(attacker, victim)
            self._increment_interaction(
                attacker, victim, "target_kills")
            if victim_kind == "player":
                target = self.players.get(int(victim_id))
                reason = getattr(target, "death_reason", 0)
            else:
                target = self.bot_states.get(int(victim_id))
                reason = target.get("death_reason", 0) if target else 0
            minimum, maximum = RESULT_INTERACTION_LIMITS["death_reason"]
            interaction["death_reason"] = max(
                minimum, min(maximum, int(reason)))
        self.pending_events.append({
            "kind": "vehicle_statistics",
            "actor_kind": attacker_kind,
            "actor_id": int(attacker_id),
            "frags": int(frags),
            "team_killer": bool(team_killer),
        })
        return True

    def _apply_movement(self, player, dt):
        if not player.alive or self.battle_result is not None:
            return
        if player.siege_state in (SIEGE_SWITCHING_ON, SIEGE_SWITCHING_OFF):
            # Transition lock suppresses drivetrain output only. Client-owned
            # gravity, slope and contact resolution still arrives as a legal
            # world pose through update_input and must not be rewound here.
            player.forward = 0.0
            player.turn = 0.0
            player.speed = 0.0
            return
        if not player.client_position:
            player.yaw += player.turn * 0.85 * dt
            params = self._siege_params(player)
            speed_limit = (float(params[2])
                           if params is not None and
                           player.siege_state == SIEGE_ENABLED
                           else 14.0)
            speed = speed_limit * player.forward
            player.x += math.sin(player.yaw) * speed * dt
            player.z += math.cos(player.yaw) * speed * dt
            player.x = _clamp(player.x, -220.0, 220.0)
            player.z = _clamp(player.z, -220.0, 220.0)

    @staticmethod
    def _human_ram_probe_body(body):
        return {
            "id": int(body["id"]),
            "vehicle": str(body["vehicle"]),
            "x": round(float(body["x"]), 4),
            "y": round(float(body["y"]), 4),
            "z": round(float(body["z"]), 4),
            "yaw": round(float(body["yaw"]), 5),
            "pitch": round(float(body.get("pitch", 0.0)), 5),
            "roll": round(float(body.get("roll", 0.0)), 5),
            "shape": [round(float(value), 4)
                      for value in body["shape"]],
        }

    def _queue_human_ram_probe(
            self, pair, first, second, frontier_time_us, contact):
        request = self.human_ram_probe_requests.get(pair)
        if request is not None:
            return request
        if len(self.human_ram_probe_requests) >= MAX_HUMAN_RAM_PROBES:
            return None
        try:
            normal_x = float(contact[0])
            normal_z = float(contact[1])
            normal_length = math.hypot(normal_x, normal_z)
            center_delta_x = float(first["x"]) - float(second["x"])
            center_delta_z = float(first["z"]) - float(second["z"])
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(normal_length) or normal_length <= 0.000001:
            return None
        if (normal_x * center_delta_x +
                normal_z * center_delta_z) <= 0.000001:
            return None
        self.human_ram_probe_seq += 1
        request = {
            "seq": self.human_ram_probe_seq,
            "contact_normal": [
                round(normal_x / normal_length, 6),
                round(normal_z / normal_length, 6)],
            "first": self._human_ram_probe_body(first),
            "second": self._human_ram_probe_body(second),
            "frontier_time_us": int(frontier_time_us),
            "_first_body": copy.deepcopy(first),
            "_second_body": copy.deepcopy(second),
        }
        self.human_ram_probe_requests[pair] = request
        return request

    def _human_ram_probe_snapshot(self):
        return [{
            "seq": int(request["seq"]),
            "contact_normal": list(request["contact_normal"]),
            "first": copy.deepcopy(request["first"]),
            "second": copy.deepcopy(request["second"]),
        } for unused_pair, request in sorted(
            self.human_ram_probe_requests.items())]

    def _validated_human_ram_armors(self, raw_results):
        if raw_results is None:
            return ()
        if (not isinstance(raw_results, (list, tuple)) or
                len(raw_results) > MAX_HUMAN_RAM_PROBES):
            return None
        normalized = []
        seen = set()
        for raw in raw_results:
            if not isinstance(raw, dict):
                return None
            available = raw.get("available")
            allowed = {"seq", "first_id", "second_id", "available"}
            if available is True:
                allowed.update(("armor_first", "armor_second"))
            if set(raw) != allowed or not isinstance(available, bool):
                return None
            try:
                sequence = _exact_int(
                    raw.get("seq"), 1, PROJECTILE_MAX_ID)
                first_id = _exact_int(
                    raw.get("first_id"), 1, PROJECTILE_MAX_ID)
                second_id = _exact_int(
                    raw.get("second_id"), 1, PROJECTILE_MAX_ID)
            except ValueError:
                return None
            if (sequence in seen or first_id >= second_id or
                    first_id not in self.human_collision_profiles or
                    second_id not in self.human_collision_profiles):
                return None
            seen.add(sequence)
            result = {
                "seq": sequence, "first_id": first_id,
                "second_id": second_id, "available": available,
            }
            if available:
                try:
                    armor_first = float(raw.get("armor_first"))
                    armor_second = float(raw.get("armor_second"))
                except (TypeError, ValueError, OverflowError):
                    return None
                if (not math.isfinite(armor_first) or
                        not 0.0 < armor_first <= 5000.0 or
                        not math.isfinite(armor_second) or
                        not 0.0 < armor_second <= 5000.0):
                    return None
                result["armor_first"] = round(armor_first, 4)
                result["armor_second"] = round(armor_second, 4)
            fingerprint = json.dumps(
                result, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True)
            previous = self.human_ram_probe_fingerprints.get(sequence)
            if previous is not None:
                if previous != fingerprint:
                    return None
                continue
            pair = (first_id, second_id)
            request = self.human_ram_probe_requests.get(pair)
            if request is None:
                if self.human_ram_retired_probe_pairs.get(sequence) != pair:
                    return None
                normalized.append((pair, result, fingerprint, True))
                continue
            if (int(request["seq"]) != sequence or
                    int(request["first"]["id"]) != first_id or
                    int(request["second"]["id"]) != second_id):
                return None
            normalized.append((pair, result, fingerprint, False))
        return tuple(normalized)

    def _commit_human_ram_armors(self, results):
        for pair, result, fingerprint, retired in results:
            if retired:
                sequence = int(result["seq"])
                if self.human_ram_retired_probe_pairs.get(sequence) != pair:
                    raise RuntimeError(
                        "retired human ram armor request changed at commit")
                self.human_ram_retired_probe_pairs.pop(sequence, None)
            else:
                request = self.human_ram_probe_requests.get(pair)
                if (request is None or
                        int(request["seq"]) != int(result["seq"])):
                    raise RuntimeError(
                        "human ram armor request changed at commit")
                request["result"] = dict(result)
            self.human_ram_probe_fingerprints[int(result["seq"])] = fingerprint
            while (len(self.human_ram_probe_fingerprints) >
                   MAX_HUMAN_RAM_PROBE_HISTORY):
                self.human_ram_probe_fingerprints.popitem(last=False)

    def _retire_human_ram_probe(self, pair):
        request = self.human_ram_probe_requests.pop(pair, None)
        if request is None:
            return
        sequence = int(request["seq"])
        self.human_ram_retired_probe_pairs[sequence] = pair
        while (len(self.human_ram_retired_probe_pairs) >
               MAX_HUMAN_RAM_PROBE_HISTORY):
            self.human_ram_retired_probe_pairs.popitem(last=False)

    def _human_ram_contact_armors(
            self, pair, first, second, frontier_time_us, contact):
        request = self._queue_human_ram_probe(
            pair, first, second, frontier_time_us, contact)
        if request is None:
            return None, False
        result = request.get("result")
        if result is None:
            return None, False
        self.human_ram_probe_requests.pop(pair, None)
        if not result["available"]:
            return None, True
        return (float(result["armor_first"]),
                float(result["armor_second"])), True

    def _resolve_human_ram_substep(
            self, pair, first_body, second_body, frontier_time_us,
            active_contacts):
        probe_state = {"resolved": False}

        def contact_armor_probe(probe_first, probe_second, contact):
            armors, resolved = self._human_ram_contact_armors(
                pair, probe_first, probe_second, frontier_time_us, contact)
            probe_state["resolved"] = resolved
            return armors

        result = tank_collision.resolve_tank(
            first_body, (second_body,),
            # resolve_tank uses zero as the no-prior-contact sentinel. Keep the
            # round-relative source timeline positive.
            now=float(frontier_time_us) / 1000000.0 + 1.0,
            ram_cooldowns=self.human_ram_cooldowns,
            active_ram_contacts=active_contacts,
            contact_armor_probe=contact_armor_probe)
        armor_pending = bool(
            not probe_state["resolved"] and any(
                diagnostic.get("reason") == "contact_armor_unavailable"
                for diagnostic in result.get("ram_diagnostics", ())))
        return result, armor_pending

    def _apply_human_ram_substep(
            self, first, second, pair, frontier_time_us, result,
            active_contacts):
        self.human_ram_cooldowns = result["cooldowns"]
        if pair in result["contacts"]:
            active_contacts.add(pair)
        else:
            active_contacts.discard(pair)
        applied = 0
        for event in result["ram_events"]:
            if self._apply_human_ram_damage(
                    first, second, event, pair, frontier_time_us):
                applied += 1
        return applied

    @staticmethod
    def _human_pose_at(history, frontier_time_us):
        """Interpolate one canonical player pose; never extrapolate it."""
        samples = list(history or ())
        if len(samples) < 2:
            return None
        left = None
        right = None
        for sample in samples:
            sample_time = int(sample["time_us"])
            if sample_time <= frontier_time_us:
                left = sample
            if sample_time >= frontier_time_us:
                right = sample
                break
        if left is None or right is None:
            return None
        left_time = int(left["time_us"])
        right_time = int(right["time_us"])
        if right_time == left_time:
            ratio = 0.0
        else:
            ratio = ((float(frontier_time_us) - float(left_time)) /
                     float(right_time - left_time))
        yaw_delta = ((float(right["yaw"]) - float(left["yaw"]) +
                      math.pi) % (2.0 * math.pi)) - math.pi
        return {
            "x": float(left["x"]) +
                 (float(right["x"]) - float(left["x"])) * ratio,
            "y": float(left["y"]) +
                 (float(right["y"]) - float(left["y"])) * ratio,
            "z": float(left["z"]) +
                 (float(right["z"]) - float(left["z"])) * ratio,
            "yaw": float(left["yaw"]) + yaw_delta * ratio,
            "pitch": float(left.get("pitch", 0.0)) +
                     (float(right.get("pitch", 0.0)) -
                      float(left.get("pitch", 0.0))) * ratio,
            "roll": float(left.get("roll", 0.0)) +
                    (float(right.get("roll", 0.0)) -
                     float(left.get("roll", 0.0))) * ratio,
            "vx": float(left["vx"]) +
                  (float(right["vx"]) - float(left["vx"])) * ratio,
            "vz": float(left["vz"]) +
                  (float(right["vz"]) - float(left["vz"])) * ratio,
        }

    def _apply_human_ram_damage(self, first, second, event, pair,
                                frontier_time_us):
        """Commit both halves of one server-owned ram in one state lock."""
        damage_first = max(0, min(
            int(event.get("damage_to_self", 0)), int(first.health)))
        damage_second = max(0, min(
            int(event.get("damage_to_other", 0)), int(second.health)))
        if damage_first <= 0 and damage_second <= 0:
            return False
        first_critical_before = first.critical
        second_critical_before = second.critical
        first.health -= damage_first
        second.health -= damage_second
        first.alive = first.health > 0
        second.alive = second.health > 0
        first.display_health = first.health
        second.display_health = second.health
        first.death_reason = 2 if not first.alive else 0
        second.death_reason = 2 if not second.alive else 0
        if damage_first > 0:
            self._drop_capture_for_vehicle("player", first.player_id)
        if damage_second > 0:
            self._drop_capture_for_vehicle("player", second.player_id)
        episode = int(self.human_ram_episode_seq.get(pair, 0)) + 1
        self.human_ram_episode_seq[pair] = episode
        operation_id = "%d:%d:%d:%d" % (
            self.round_id, pair[0], pair[1], episode)
        reason = 2
        self.pending_events.extend(({
            "kind": "hit", "attacker": second.player_id,
            "target": first.player_id, "damage": damage_first,
            "health": first.health, "dead": not first.alive,
            "attack_reason": reason,
            "death_reason": first.death_reason, "source": "ram",
            "ram_operation_id": operation_id,
            "ram_frontier_time_us": int(frontier_time_us),
        }, {
            "kind": "hit", "attacker": first.player_id,
            "target": second.player_id, "damage": damage_second,
            "health": second.health, "dead": not second.alive,
            "attack_reason": reason,
            "death_reason": second.death_reason, "source": "ram",
            "ram_operation_id": operation_id,
            "ram_frontier_time_us": int(frontier_time_us),
        }))
        self._record_damage(
            ("player", second.player_id),
            ("player", first.player_id), damage_first,
            first_critical_before)
        self._record_damage(
            ("player", first.player_id),
            ("player", second.player_id), damage_second,
            second_critical_before)
        if not first.alive:
            first.death_attacker_kind = "player"
            first.death_attacker_id = second.player_id
            self._record_frag(
                "player", second.player_id, first.team,
                "player", first.player_id)
        if not second.alive:
            second.death_attacker_kind = "player"
            second.death_attacker_id = first.player_id
            self._record_frag(
                "player", first.player_id, second.team,
                "player", second.player_id)
        self._maybe_finish_battle()
        return True

    def _resolve_human_rams(self):
        """Resolve every due source-time segment for each human pair."""
        if (not self._combat_accepting() or self.battle_result is not None or
                not self._human_ram_profiles_required() or
                self.human_collision_profile_authority_id !=
                self.bot_authority_id):
            return 0
        now_us = self._logical_motion_time_us()
        players = sorted((
            player for player in self.players.values()
            if player.connected and player.participating and player.alive and
            player.player_id in self.human_collision_profiles),
            key=lambda value: value.player_id)
        live_player_ids = set(player.player_id for player in players)
        for pair in list(self.human_ram_probe_requests):
            if not set(pair).issubset(live_player_ids):
                self._retire_human_ram_probe(pair)
        active_contacts = set(self.human_ram_contacts)
        applied = 0
        for first_index, first in enumerate(players):
            for second in players[first_index + 1:]:
                if (self.battle_result is not None or
                        not first.alive or not second.alive):
                    continue
                pair = (first.player_id, second.player_id)
                if int(first.team) == int(second.team):
                    # Native clients retain solid friendly hulls.  The server
                    # only owns HP and therefore has no same-team ram work.
                    self._retire_human_ram_probe(pair)
                    active_contacts.discard(pair)
                    continue
                pending_request = self.human_ram_probe_requests.get(pair)
                if pending_request is not None:
                    # A native probe can outlive the bounded pose history while
                    # either real entity is still starting.  Replay the frozen
                    # source-time bodies before consulting newer samples so a
                    # delayed response cannot strand this pair frontier.
                    if pending_request.get("result") is None:
                        continue
                    pending_frontier = int(
                        pending_request["frontier_time_us"])
                    result, armor_pending = self._resolve_human_ram_substep(
                        pair,
                        copy.deepcopy(pending_request["_first_body"]),
                        copy.deepcopy(pending_request["_second_body"]),
                        pending_frontier, active_contacts)
                    if armor_pending:
                        raise RuntimeError(
                            "committed human ram armor was not consumed")
                    applied += self._apply_human_ram_substep(
                        first, second, pair, pending_frontier, result,
                        active_contacts)
                    self.human_ram_pair_frontiers[pair] = pending_frontier
                    if (self.battle_result is not None or
                            not first.alive or not second.alive):
                        continue
                if not first.pose_history or not second.pose_history:
                    continue
                frontier = min(
                    int(first.pose_history[-1]["time_us"]),
                    int(second.pose_history[-1]["time_us"]))
                previous_frontier = self.human_ram_pair_frontiers.get(pair)
                if ((previous_frontier is not None and
                     frontier <= int(previous_frontier)) or
                        now_us - frontier > int(
                            HUMAN_POSE_HISTORY_SECONDS * 1000000.0)):
                    continue

                # A source-time gap is still elapsed battle time. Build the
                # complete common interval before mutating HP/contact state,
                # then resolve it in bounded steps. Advancing only the newest
                # endpoint lets two tanks pass through each other during a
                # one-second callback stall without ever owning a contact.
                if previous_frontier is None:
                    cursor = max(
                        int(first.pose_history[0]["time_us"]),
                        int(second.pose_history[0]["time_us"]))
                    sample_frontiers = [cursor]
                else:
                    cursor = int(previous_frontier)
                    sample_frontiers = []
                while cursor < frontier:
                    cursor = min(
                        frontier, cursor + HUMAN_RAM_MAX_SUBSTEP_US)
                    sample_frontiers.append(cursor)

                timeline = []
                for sample_frontier in sample_frontiers:
                    first_pose = self._human_pose_at(
                        first.pose_history, sample_frontier)
                    second_pose = self._human_pose_at(
                        second.pose_history, sample_frontier)
                    if first_pose is None or second_pose is None:
                        timeline = []
                        break
                    timeline.append(
                        (sample_frontier, first_pose, second_pose))
                if not timeline:
                    continue
                first_profile = self.human_collision_profiles[first.player_id]
                second_profile = self.human_collision_profiles[
                    second.player_id]
                processed_frontier = previous_frontier
                for sample_frontier, first_pose, second_pose in timeline:
                    first_body = dict(first_pose, **{
                        "id": first.player_id, "alive": True,
                        "team": int(first.team),
                        "vehicle": first.vehicle,
                        "mass": first_profile["mass"],
                        "shape": first_profile["shape"],
                        "ram_profile": first_profile["ram_profile"],
                    })
                    second_body = dict(second_pose, **{
                        "id": second.player_id, "alive": True,
                        "team": int(second.team),
                        "vehicle": second.vehicle,
                        "mass": second_profile["mass"],
                        "shape": second_profile["shape"],
                        "ram_profile": second_profile["ram_profile"],
                    })
                    result, armor_pending = self._resolve_human_ram_substep(
                        pair, first_body, second_body, sample_frontier,
                        active_contacts)
                    if armor_pending:
                        # The hidden worker evaluates the exact native models at
                        # this source-time pose. Keep the pair frontier behind
                        # this substep so its result replays the same contact,
                        # rather than applying a later asynchronous pose.
                        break
                    applied += self._apply_human_ram_substep(
                        first, second, pair, sample_frontier, result,
                        active_contacts)
                    processed_frontier = sample_frontier
                    if (self.battle_result is not None or
                            not first.alive or not second.alive):
                        break
                # Publish the frontier only after every available source-time
                # substep has been applied in order. No later tick can skip an
                # unprocessed suffix of this interval.
                if processed_frontier is not None:
                    self.human_ram_pair_frontiers[pair] = processed_frontier
        self.human_ram_contacts = frozenset(active_contacts)
        return applied

    def _combat_accepting(self):
        """Fence #1513 combat until the shared countdown becomes live.

        The 0.8.2 client already owns its proven local PREBATTLE guard and the
        original v5 server accepted its packets as soon as the room entered
        ``battle``.  Keep that wire behavior unchanged; only #1513 uses the
        server-owned load barrier and countdown clock added by this port.
        """
        return (self.phase == "battle" and
                (self.client_build != CLIENT_BUILD_0922 or
                 self.tick >= int(round(PREBATTLE_SECONDS * TICK_HZ))))

    def _map_rule_data(self):
        return (get_tactical_map(self.map_name) or
                _MAPS_0922_DATA.get(self.map_name) or {})

    @staticmethod
    def _capture_vehicle_key(kind, vehicle_id):
        return "%s:%d" % ("human" if kind == "player" else "bot",
                           int(vehicle_id))

    def _drop_capture_for_vehicle(self, kind, vehicle_id):
        """Drop only one damaged vehicle's accumulated capture points."""
        if self.client_build != CLIENT_BUILD_0922:
            return 0
        key = self._capture_vehicle_key(kind, vehicle_id)
        dropped_total = 0
        for base_team in (1, 2):
            state = self.rules_state["bases"][str(base_team)]
            contributors = self.capture_contributors[base_team]
            dropped_total += max(0, int(contributors.pop(key, 0) or 0))
            state["points"] = min(100, sum(
                max(0, int(points or 0))
                for points in contributors.values()))
            if not contributors:
                self.capture_cursors[base_team] = 0
            rate = min(max(0, int(state.get("invaders", 0))), 3)
            state["time_left"] = (
                float(max(0, 100 - state["points"])) / float(rate)
                if rate > 0 else 0.0)
        return dropped_total

    def _update_capture(self):
        """Copy the 0.8.2 standard-mode 50 m, 1 Hz capture law."""
        if (not self._combat_accepting() or
                self.tick % max(1, int(round(TICK_HZ))) != 0 or
                self.battle_result is not None):
            return False
        # #1513 navigation graphs contain packed CTF objective positions.  Its
        # tactical-map ``bases`` are route annotations and can be hundreds of
        # metres from the retail capture circles, so never use them as a modern
        # protocol fallback.
        bases = (self.capture_bases if self.client_build == CLIENT_BUILD_0922
                 else self.capture_bases or
                 (self._map_rule_data().get('bases') or {}))
        if not bases:
            return False
        vehicles = {1: [], 2: []}
        for player in self.players.values():
            if (player.connected and player.participating and player.alive and
                    (self.client_build != CLIENT_BUILD_0922 or
                     player.client_position) and player.team in vehicles):
                vehicles[player.team].append((
                    self._capture_vehicle_key("player", player.player_id),
                    player.x, player.z))
        for state in self.bot_states.values():
            team = int(state.get('team', 0))
            if (state.get('alive') and
                    (self.client_build != CLIENT_BUILD_0922 or
                     state.get('world_pose')) and
                    team in vehicles):
                vehicles[team].append((
                    self._capture_vehicle_key("bot", state['id']),
                    state['x'], state['z']))
        changed = False
        self.capture_threat_bases = {1: [], 2: []}
        for base_team in (1, 2):
            raw_base = bases.get(base_team, bases.get(str(base_team)))
            if raw_base is None:
                continue
            if isinstance(raw_base, dict):
                base_positions = [(raw_base.get('x'), raw_base.get('z'))]
            elif (isinstance(raw_base, (list, tuple)) and len(raw_base) >= 2 and
                  not isinstance(raw_base[0], (list, tuple, dict))):
                base_positions = [(raw_base[0], raw_base[1])]
            else:
                base_positions = list(raw_base or ())
            normalized = []
            for point in base_positions:
                try:
                    if isinstance(point, dict):
                        normalized.append((float(point['x']), float(point['z'])))
                    else:
                        normalized.append((float(point[0]), float(point[1])))
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
            if not normalized:
                continue
            invading_team = 3 - base_team
            threatened = []
            for index, (bx, bz) in enumerate(normalized):
                if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                       for unused_key, x, z in vehicles[invading_team]):
                    threatened.append({
                        "id": "%d:%d" % (base_team, index),
                        "x": round(bx, 3), "y": 0.0,
                        "z": round(bz, 3),
                    })
            self.capture_threat_bases[base_team] = threatened
            invader_keys = sorted(set(
                key for key, x, z in vehicles[invading_team]
                if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                       for bx, bz in normalized)))
            defenders = sum(
                1 for unused_key, x, z in vehicles[base_team]
                if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                       for bx, bz in normalized))
            state = self.rules_state['bases'][str(base_team)]
            previous = dict(state)
            if self.client_build != CLIENT_BUILD_0922:
                if invader_keys and defenders == 0:
                    state['points'] = min(
                        100, int(state.get('points', 0)) +
                        min(len(invader_keys), 3))
                elif not invader_keys:
                    state['points'] = 0
                state['stopped'] = defenders > 0
            else:
                contributors = self.capture_contributors[base_team]
                active = set(invader_keys)
                for vehicle_id in list(contributors):
                    if vehicle_id not in active:
                        contributors.pop(vehicle_id, None)
                for vehicle_id in invader_keys:
                    contributors.setdefault(vehicle_id, 0)
                points = min(100, sum(
                    max(0, int(value or 0))
                    for value in contributors.values()))
                # Standard CTF bases do not stop capture merely because an
                # owner enters its own circle.  Only an invader leaving,
                # dying, or taking qualifying damage drops that vehicle's
                # contribution.
                state['stopped'] = False
                if invader_keys and points < 100:
                    cursor = (self.capture_cursors[base_team] %
                              len(invader_keys))
                    budget = min(3, len(invader_keys), 100 - points)
                    for offset in range(budget):
                        vehicle_id = invader_keys[
                            (cursor + offset) % len(invader_keys)]
                        contributors[vehicle_id] = int(
                            contributors.get(vehicle_id, 0) or 0) + 1
                    self.capture_cursors[base_team] = (
                        cursor + budget) % len(invader_keys)
                elif not invader_keys:
                    self.capture_cursors[base_team] = 0
                state['points'] = min(100, sum(
                    max(0, int(value or 0))
                    for value in contributors.values()))
            state['invaders'] = len(invader_keys)
            rate = min(len(invader_keys), 3)
            state['time_left'] = (
                float(max(0, 100 - state['points'])) / float(rate)
                if rate > 0 else 0.0)
            changed = changed or state != previous
            if state['points'] >= 100:
                self._finish_battle(
                    invading_team, 'base captured', base_team)
                break
        return changed

    def _bot_defense_context(self):
        """Return own-base pressure facts and authoritative capture targets."""
        contributors = {}
        for team in (1, 2):
            values = []
            for key in sorted(self.capture_contributors.get(team, {})):
                try:
                    kind, raw_id = str(key).split(":", 1)
                    vehicle_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if kind not in ("human", "bot") or vehicle_id <= 0:
                    continue
                values.append({"kind": kind, "id": vehicle_id})
            contributors[str(team)] = values
        capture_bases = {}
        for team in (1, 2):
            values = []
            for index, point in enumerate(
                    self.capture_bases.get(team,
                                           self.capture_bases.get(str(team), ())) or ()):
                try:
                    if isinstance(point, dict):
                        x = float(point['x'])
                        z = float(point['z'])
                    else:
                        x = float(point[0])
                        z = float(point[1])
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
                values.append({
                    "id": "%d:%d" % (team, index),
                    "x": round(x, 3), "y": 0.0, "z": round(z, 3),
                })
            capture_bases[str(team)] = values
        return {
            "bases": dict((str(team), [dict(point) for point in
                                        self.capture_threat_bases.get(
                                            team, ())])
                          for team in (1, 2)),
            "states": dict((str(team), dict(
                self.rules_state["bases"][str(team)]))
                for team in (1, 2)),
            "contributors": contributors,
            "capture_bases": capture_bases,
        }

    def tick_once(self, dt):
        reset_message = None
        had_pending_live = False
        failed_live_recipients = []
        failed_receipt_recipients = []
        failed_receipt_recipient_ids = set()
        current_receipt_recipients = set()
        failed_event_recipients = []
        authority_observation_relays = ()
        with self.lock:
            if self.pending_live_message is not None:
                pending = self.pending_live_message
                self.pending_live_message = None
                had_pending_live = True
                if not isinstance(pending, dict):
                    raise RuntimeError("pending battle_live barrier is invalid")
                barrier_round = pending.get("round_id")
                barrier_message = pending.get("message")
                barrier_recipients = pending.get("recipients")
                if (not isinstance(barrier_message, dict) or
                        barrier_message.get("type") != "battle_live" or
                        barrier_message.get("round_id") != barrier_round or
                        not isinstance(barrier_recipients, tuple)):
                    raise RuntimeError(
                        "pending battle_live barrier contract is invalid")
                if (barrier_round == self.round_id and
                        self.phase == "battle"):
                    message = dict(barrier_message)
                    # Authority can fail over after the final ready message
                    # queues this barrier but before the tick thread publishes
                    # it.  Refresh every authority/timing fence under the state
                    # lock so no client observes an older epoch after a newer
                    # roster and disconnects on the apparent regression.
                    message.update({
                        "state_revision": self.state_revision,
                        "bot_authority_id": self.bot_authority_id,
                        "authority_epoch": self.authority_epoch,
                        "server_time_ms": self._server_time_ms(),
                        "timing": self._timing_payload(),
                    })
                    message.update(self._authority_fields())
                    recipients = tuple(
                        endpoint for endpoint in barrier_recipients
                        if self._endpoint_is_current(
                            endpoint, participating_only=True))
                    # Keep the round/recipient check and each send in one
                    # state-lock critical section.  A result reset preserves
                    # Player objects, so merely binding object references is
                    # insufficient: without this lock, the same connection
                    # could enter the next round between validation and send.
                    for endpoint in recipients:
                        if not endpoint.offer_reliable(message):
                            failed_live_recipients.append(endpoint)
            if (self.phase == "battle" and self.battle_result is not None and
                    self.result_reset_tick is not None and
                    self.tick + 1 >= self.result_reset_tick):
                self._reset_round()
                reset_message = self.lobby_message()
        if reset_message is not None:
            self.broadcast(reset_message)
            return
        if had_pending_live:
            for endpoint in failed_live_recipients:
                self._remove_endpoint(endpoint)
            # A round reset can invalidate a barrier after it was queued.  It
            # still consumes this tick: a newly queued round must publish its
            # own tick-zero barrier before any snapshot can advance it.  A
            # valid barrier likewise remains its own ordered wire transition.
            return
        with self.lock:
            if self.phase != "battle":
                return
            self.tick += 1
            self._advance_siege_states()
            self._prune_orphaned_bot_launch_edges()
            if (self.battle_result is None and
                    self.tick >= int(round(
                        (PREBATTLE_SECONDS + BATTLE_DURATION_SECONDS) *
                        TICK_HZ))):
                self._finish_battle(0, "battle_timeout", 0)
            self._update_capture()
            for player in list(self.players.values()):
                self._apply_movement(player, dt)
            self._resolve_human_rams()
            self._tick_player_critical(dt)
            self._tick_player_fire(dt)
            self._tick_player_drowning(dt)
            self._tick_player_overturn(dt)
            self._expire_projectiles()
            # Observation and damage handlers update planner memory as their
            # messages arrive. Only the full-roster synthesis is throttled;
            # authoritative events and snapshots continue every tick below.
            if (self.battle_result is None and
                    self.tick >= self._next_bot_planner_tick):
                self.bot_orders = self.bot_planner.build_orders(
                    self.bot_manifest, list(self.bot_states.values()),
                    [self._public_player(p, include_outfits=False)
                     for p in self.players.values()
                     if p.connected and p.participating],
                    time.monotonic(), self._bot_defense_context())
                self._next_bot_planner_tick = (
                    self.tick +
                    (BOT_PLANNER_INTERVAL_TICKS
                     if self.client_build == CLIENT_BUILD_0922 else 1))
            tick_server_time_ms = None
            if self.client_build == CLIENT_BUILD_0922:
                # Freeze the one current clock sample shared by this tick's
                # durable state, ordered events and expiration edges.
                tick_server_time_ms = self._server_time_ms()
                self._expire_stuns(tick_server_time_ms)
            events = []
            for ordinal, pending in enumerate(self.pending_events):
                self._validate_combat_event_for_wire(pending)
                event = dict(pending)
                event["event_id"] = "%d:%d:%d" % (
                    self.round_id, self.tick, ordinal)
                events.append(event)
            self.pending_events = []
            if self.client_build == CLIENT_BUILD_0922:
                # Events and the snapshot published by one simulation tick
                # share the current clock sample frozen above. Reusing a prior
                # snapshot's time would make delayed projectile tracers start
                # at the wrong point on their authoritative trajectory.
                assert tick_server_time_ms is not None
            snapshot = {
                "type": "snapshot",
                "protocol": PROTOCOL_VERSION,
                "server_tick": self.tick,
                "round_id": self.round_id,
                "state_revision": self.state_revision,
                "map": self.map_name,
                "bot_authority_id": self.bot_authority_id,
                "players": [self._public_player(p, include_outfits=False)
                            for p in self.players.values()
                            if p.connected and p.participating],
                "bots": [self.bot_states[key] for key in sorted(self.bot_states)],
                "bot_state_revision": self.bot_state_revision,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "timing": self._timing_payload(),
            }
            if self.client_build == CLIENT_BUILD_0922:
                snapshot.update({
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": tick_server_time_ms,
                    "motion_time_us": self._logical_motion_time_us(),
                    "bot_state_time_us": self.bot_state_time_us,
                    "projectile_revision": self.projectile_revision,
                    "projectiles": self._projectile_snapshot(),
                    "human_ram_probes": self._human_ram_probe_snapshot(),
                })
                snapshot.update(self._authority_fields())
            # Freeze one exact wire image while holding the state lock. Bot,
            # rule, manifest and critical dictionaries are otherwise shared
            # mutable objects; serializing later per endpoint could make one
            # server_tick describe different state to different peers.
            snapshot = copy.deepcopy(snapshot)
            events_message = None
            if events:
                events_message = {
                    "type": "events",
                    "protocol": PROTOCOL_VERSION,
                    "round_id": self.round_id,
                    "server_tick": self.tick,
                    "events": events,
                }
                if self.client_build == CLIENT_BUILD_0922:
                    events_message.update({
                        "authority_epoch": self.authority_epoch,
                        "server_time_ms": tick_server_time_ms,
                    })
                    events_message.update(self._authority_fields())
                    if (self.simulation_worker is not None or
                            self.worker_failure_reason):
                        events_message["bot_authority_id"] = (
                            self.bot_authority_id)
            recipients = list(self.players.values())
            if self.simulation_worker is not None:
                recipients.append(self.simulation_worker)
            # Settlement is part of the terminal barrier, not a best-effort
            # follow-up to the state that tears the battle UI down. Prioritize
            # this round's durable receipt even when the account still owns an
            # older unacknowledged row; ordinary garage/reconnect delivery
            # below remains oldest-first.
            if self.battle_result is not None:
                for endpoint in recipients:
                    if not isinstance(endpoint, Player):
                        continue
                    receipt = self._result_receipt_for_delivery(
                        endpoint, round_id=self.round_id)
                    if receipt is None:
                        continue
                    current_receipt_recipients.add(id(endpoint))
                    if not self._deliver_result_receipt(
                            endpoint, round_id=self.round_id):
                        failed_receipt_recipients.append(endpoint)
                        failed_receipt_recipient_ids.add(id(endpoint))
            # Enqueue ordered causes in the same state transaction that assigns
            # their IDs and removes them from pending_events. A leave, worker
            # loss, or epoch change cannot invalidate this batch between
            # extraction and delivery; the reliable outbox fences its snapshot.
            if events_message is not None:
                for endpoint in recipients:
                    if id(endpoint) in failed_receipt_recipient_ids:
                        continue
                    if not endpoint.offer_reliable(events_message):
                        failed_event_recipients.append(endpoint)
            snapshot_client_build = self.client_build
            snapshot_round_id = self.round_id
            snapshot_tick = self.tick
            snapshot_state_revision = self.state_revision
            snapshot_bot_authority_id = self.bot_authority_id
            snapshot_authority_epoch = self.authority_epoch
            snapshot_manifest_revision = self.bot_manifest_revision
            snapshot_order_revision = self.bot_orders["revision"]
            snapshot_orders = copy.deepcopy(self.bot_orders["orders"])
            snapshot_destructible_revision = self.destructible_revision
            snapshot_destructibles = copy.deepcopy(
                list(self.destructibles.values()))
        for relay in authority_observation_relays:
            self.broadcast_bot_observation(relay)
        if events:
            for event in events:
                message = _server_event_log_message(
                    event, self.players, self.bot_states)
                if message is not None:
                    _server_log(message)
            for endpoint in failed_event_recipients:
                self._remove_endpoint(endpoint)
        for endpoint in failed_receipt_recipients:
            self._remove_endpoint(endpoint)
        # Ordered combat causes must reach the client before the durable state
        # they produced.  Otherwise #1513 observes the new HP/death first and
        # suppresses hit direction, attacker attribution and the fatal shot.
        for player in recipients:
            if id(player) in failed_receipt_recipient_ids:
                continue
            replica_limited = bool(
                snapshot_client_build == CLIENT_BUILD_0922 and
                isinstance(player, Player) and
                player.player_id != snapshot_bot_authority_id)
            snapshot_lineage_due = bool(
                player.bot_manifest_round_id_sent != snapshot_round_id or
                player.bot_manifest_revision_sent !=
                snapshot_manifest_revision or
                (snapshot_client_build == CLIENT_BUILD_0922 and
                 player.bot_manifest_authority_epoch_sent !=
                 snapshot_authority_epoch))
            supports_lean_manifest = bool(
                LEAN_SNAPSHOT_MANIFEST_CAPABILITY in player.capabilities)
            snapshot_due = bool(
                not replica_limited or
                player.snapshot_round_id_sent != snapshot_round_id or
                snapshot_tick - player.snapshot_tick_sent >=
                REPLICA_SNAPSHOT_TICKS or events or
                snapshot_lineage_due)
            if not snapshot_due:
                if (isinstance(player, Player) and
                        id(player) not in current_receipt_recipients and
                        not self._deliver_result_receipt(player)):
                    self.remove_player(player.player_id, expected=player)
                continue
            outgoing = snapshot
            needs_manifest = snapshot_lineage_due
            includes_manifest = bool(
                needs_manifest or not supports_lean_manifest)
            needs_orders = (
                player.bot_order_revision_sent !=
                snapshot_order_revision)
            needs_destructibles = (
                player.destructible_revision_sent !=
                snapshot_destructible_revision)
            if (not includes_manifest or needs_orders or
                    needs_destructibles):
                outgoing = dict(snapshot)
            if not includes_manifest:
                outgoing.pop("bot_manifest", None)
            if needs_orders:
                outgoing["bot_orders"] = snapshot_orders
            if needs_destructibles:
                outgoing["destructibles"] = snapshot_destructibles
            # A manifest-bearing snapshot is a rare lineage barrier and must
            # not be replaced. Steady snapshots occupy one latest-only slot.
            with self.lock:
                if (self.round_id != snapshot_round_id or
                        self.state_revision != snapshot_state_revision or
                        self.authority_epoch != snapshot_authority_epoch or
                        self.bot_manifest_revision !=
                        snapshot_manifest_revision or
                        not self._endpoint_is_current(player)):
                    continue
                offered = (player.offer_reliable(outgoing)
                           if needs_manifest else
                           player.offer_snapshot(outgoing))
                if offered:
                    player.snapshot_round_id_sent = snapshot_round_id
                    player.snapshot_tick_sent = snapshot_tick
                    if needs_manifest:
                        player.bot_manifest_round_id_sent = (
                            snapshot_round_id)
                        player.bot_manifest_authority_epoch_sent = (
                            snapshot_authority_epoch)
                        player.bot_manifest_revision_sent = (
                            snapshot_manifest_revision)
            if not offered:
                self._remove_endpoint(player)
                continue
            if (isinstance(player, Player) and
                    id(player) not in current_receipt_recipients and
                    not self._deliver_result_receipt(player)):
                self.remove_player(player.player_id, expected=player)

    @staticmethod
    def _public_player(player, include_outfits=True):
        result = {
            "id": player.player_id,
            "name": player.name,
            "vehicle": player.vehicle,
            "vehicle_compact_descr": player.vehicle_compact_descr,
            "team": player.team,
            "slot": player.slot,
            "participating": bool(player.participating),
            "world_pose": player.client_position,
            "spawn_x": BattleState._spawn_x_for(player.slot),
            "spawn_z": BattleState._spawn_z_for(player.team),
            "x": round(player.x, 4),
            "y": round(player.y, 4),
            "z": round(player.z, 4),
            "yaw": round(player.yaw, 5),
            "pitch": round(player.pitch, 5),
            "roll": round(player.roll, 5),
            "up_cosine": round(player.up_cosine, 6),
            "aim_yaw": round(player.aim_yaw, 5),
            "gun_pitch": round(player.gun_pitch, 5),
            "forward": round(player.forward, 4),
            "turn": round(player.turn, 4),
            "speed": round(player.speed, 4),
            "input_seq": int(player.input_seq),
            "landing_observation_seq": int(
                player.landing_observation_seq),
            # The terminal frontier lets a reconnecting or resynchronizing
            # client resume at the next eligible sequence instead of retrying
            # an identifier whose decision is already terminal.
            "input_processed_seq": int(player.input_processed_seq),
            "siege_state": int(player.siege_state),
            "siege_time_left_ms": int(math.ceil(
                max(0, int(player.siege_transition_ticks)) *
                1000.0 / TICK_HZ)),
            "fire_seq": player.fire_seq,
            "shell_index": player.shell_index,
            "next_shell_index": player.next_shell_index,
            "shell_change_pending": player.shell_change_pending,
            "health": player.health,
            "max_health": player.max_health,
            "alive": player.alive,
            "death_reason": player.death_reason,
            "display_health": (player.health if player.display_health is None
                               else player.display_health),
            "frags": player.frags,
            "team_killer": player.team_killer,
            "death_attacker_kind": player.death_attacker_kind,
            "death_attacker_id": player.death_attacker_id,
            "stun_end_server_time_ms":
                player.stun_end_server_time_ms,
            "stun_attacker_kind": player.stun_attacker_kind,
            "stun_attacker_id": player.stun_attacker_id,
            "critical_revision": player.critical_revision,
            "critical_base_revision":
                player.critical_report_base_revision,
            "critical_ack_seq": player.critical_ack_seq,
            "equipment_states": [
                equipment.snapshot(player.equipment_clock)
                for equipment in player.equipment_states],
            "equipment_revision": int(player.equipment_revision),
            "equipment_intent_seq": int(player.equipment_intent_seq),
            "equipment_intent_result": dict(
                player.equipment_intent_result),
            "ram_contact_admitted_seq": player.ram_contact_seq,
            "ram_contact_resolved_seq": player.ram_contact_resolved_seq,
            "destructible_contact_admitted_seq":
                player.destructible_contact_seq,
            "destructible_contact_resolved_seq":
                player.destructible_contact_resolved_seq,
        }
        if include_outfits:
            result["outfits"] = dict(player.outfits)
            result["effective_params"] = copy.deepcopy(
                player.effective_params)
        if player.gun_checkpoint_seq > 0:
            result["gun_checkpoint_seq"] = int(
                player.gun_checkpoint_seq)
            result["gun_checkpoint"] = dict(player.gun_checkpoint)
        if player.ram_contact:
            result["ram_contact"] = dict(player.ram_contact)
        if player.ram_contacts:
            result["ram_contacts"] = [
                dict(value) for value in player.ram_contacts.values()]
        if player.destructible_contacts:
            result["destructible_contacts"] = [
                dict(value)
                for value in player.destructible_contacts.values()]
        if player.destructible_contact_resolutions:
            result["destructible_contact_resolved_seqs"] = sorted(
                player.destructible_contact_resolutions)
        if player.destructible_contact_rejections:
            result["destructible_contact_rejected_seqs"] = list(
                player.destructible_contact_rejections)
        if player.critical:
            result["critical"] = player.critical
        return result

    def _result_receipt_for_delivery(self, player, round_id=None):
        """Select one durable receipt without changing ordinary FIFO order."""
        receipts = self._result_receipts_for_account(player.account_key)
        if round_id is None:
            return receipts[0] if receipts else None
        for receipt in receipts:
            if receipt.get("round_id") == round_id:
                return receipt
        return None

    def _deliver_result_receipt(self, player, round_id=None):
        """Deliver each unacknowledged result once per TCP connection."""
        receipt = self._result_receipt_for_delivery(player, round_id)
        if receipt is None:
            return True
        receipt_id = receipt.get("receipt_id")
        if receipt_id == player.delivered_receipt_id:
            return True
        if not player.offer_reliable(receipt):
            return False
        player.delivered_receipt_id = receipt_id
        return True

    def acknowledge_result_receipt(self, player_id, message):
        """Remove one receipt only after its owning client durably accepted it."""
        with self.lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return False
            receipt_id = message.get("receipt_id")
            if (not isinstance(receipt_id, str) or
                    not 1 <= len(receipt_id) <= 96):
                return False
            receipt = self.result_receipts.get(receipt_id)
            if (receipt is None or
                    receipt.get("account_key") != player.account_key):
                return False
            remaining = OrderedDict(self.result_receipts)
            del remaining[receipt_id]
            try:
                # Persist the removal first. A crash before this point must
                # recover and retry the still-unacknowledged receipt.
                self._persist_result_receipts(remaining)
            except (OSError, ValueError, TypeError) as error:
                _server_log("RESULT RECEIPT ack persistence failed: %s" % error)
                return False
            self.result_receipts = remaining
            # A player can own more than one durable result after an ACK was
            # delayed across rounds.  The waiting room has no snapshot tick,
            # so publish the next row on this same live connection instead of
            # requiring another battle or reconnect to drain the queue.
            self._deliver_result_receipt(player)
            return True

    def broadcast(self, message):
        with self.lock:
            endpoints = list(self.players.values())
            if self.simulation_worker is not None:
                endpoints.append(self.simulation_worker)
        for endpoint in endpoints:
            if not endpoint.offer_reliable(message):
                self._remove_endpoint(endpoint)

    def broadcast_reliable(self, message):
        """Publish an ordered message without waiting on any one peer."""
        with self.lock:
            endpoints = list(self.players.values())
            if self.simulation_worker is not None:
                endpoints.append(self.simulation_worker)
        for endpoint in endpoints:
            if not endpoint.offer_reliable(message):
                self._remove_endpoint(endpoint)

    def broadcast_bot_observation(self, message):
        """Relay one validated modern observation to active round members."""
        with self.lock:
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    message.get("type") != "bot_observation"):
                return False
            players = tuple(
                player for player in self.players.values()
                if player.connected and player.participating)
        for player in players:
            if not player.offer_reliable(message):
                self.remove_player(player.player_id, expected=player)
        return True

    def _broadcast_current_roster_locked(self):
        """Queue a roster that remains current after every enqueue failure."""
        while True:
            message = self.lobby_message()
            recipients = tuple(self._connected_endpoints())
            failed = []
            for endpoint in recipients:
                if not endpoint.offer_reliable(message):
                    failed.append(endpoint)
            if not failed:
                return message
            # A failed enqueue marks an unusable connection. Remove every
            # failed endpoint before rebuilding the next revision so the last
            # queued roster for every surviving socket is authoritative.
            for endpoint in failed:
                self._remove_endpoint(endpoint)

    def broadcast_current_roster(self):
        with self.lock:
            return self._broadcast_current_roster_locked()

    def broadcast_loading_transition(self, message):
        """Publish one #1513 loading transition with strict membership repair."""
        with self.lock:
            if self.client_build != CLIENT_BUILD_0922:
                raise RuntimeError(
                    "loading transition is only valid for the #1513 client")
            if not isinstance(message, dict):
                raise RuntimeError("loading transition must be an object")
            kind = message.get("type")
            if kind not in ("battle_start", "snapshot"):
                raise RuntimeError("unsupported loading transition: %s" % kind)

            # A different sender can discover a dead connection between the
            # state mutation and this publisher acquiring the lock.  Retire it
            # before rebuilding the transition rather than knowingly putting
            # stale authority or membership on another socket.
            disconnected = [
                player.player_id for player in self.players.values()
                if not player.connected]
            for player_id in disconnected:
                self.remove_player(player_id)

            if (self.phase != "loading" or
                    message.get("round_id") != self.round_id):
                self._broadcast_current_roster_locked()
                return False

            if kind == "battle_start":
                outgoing = dict(message)
                connected = [
                    player for player in self.players.values()
                    if player.connected and player.participating]
                outgoing.update({
                    "client_build": self.client_build,
                    "round_id": self.round_id,
                    "state_revision": self.state_revision,
                    "map": self.map_name,
                    "host_player_id": self.host_player_id,
                    "phase": self.phase,
                    "players": [self._public_player(player)
                                for player in connected],
                    "bots": list(self.bot_roster),
                    "team_size": self.team_size,
                    "team_sizes": self._team_sizes_wire(),
                    "bot_tier_mode": self.bot_tier_mode,
                    "bot_lineup": list(self.bot_lineup),
                    "bot_authority_id": self.bot_authority_id,
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": self._server_time_ms(),
                    "bot_manifest": list(self.bot_manifest),
                    "bot_order_revision": self.bot_orders["revision"],
                    "bot_orders": list(self.bot_orders["orders"]),
                    "rules": self.rules_state,
                    "battle_result": self.battle_result,
                    "destructible_revision": self.destructible_revision,
                    "destructibles": list(self.destructibles.values()),
                })
                outgoing.update(self._authority_fields())
            else:
                outgoing = self.loading_snapshot()
                if outgoing is None:
                    # Authority changed before publication.  Its old manifest
                    # is no longer canonical; publish the new authority and
                    # wait for that client to submit a fresh manifest.
                    self._broadcast_current_roster_locked()
                    return False

            recipients = tuple(
                self._connected_endpoints(participating_only=True))
            failed = []
            # Defer removals until the transition has been queued for every
            # surviving recipient. If an enqueue fails, a revisioned roster
            # repair below becomes the final membership message on every
            # surviving stream.
            for endpoint in recipients:
                if not endpoint.offer_reliable(outgoing):
                    failed.append(endpoint)
            for endpoint in failed:
                self._remove_endpoint(endpoint)
            if failed:
                self._broadcast_current_roster_locked()
            return True


class ClientHandler(socketserver.BaseRequestHandler):
    @staticmethod
    def _worker_welcome(state, worker):
        message = {
            "type": "welcome",
            "protocol": PROTOCOL_VERSION,
            "role": SIMULATION_WORKER_ROLE,
            "worker_id": worker.worker_id,
            "client_build": state.client_build,
            "capabilities": list(worker.capabilities),
            "server_capabilities": list(SERVER_CAPABILITIES),
            "map": state.map_name,
            "map_pool": list(state._active_map_pool()),
            "host_player_id": state.host_player_id,
            "phase": state.phase,
            "round_id": state.round_id,
            "state_revision": state.state_revision,
            "bot_authority_id": state.bot_authority_id,
            "authority_epoch": state.authority_epoch,
            "server_time_ms": state._server_time_ms(),
            "team_size": state.team_size,
            "team_sizes": state._team_sizes_wire(),
            "bot_tier_mode": state.bot_tier_mode,
        }
        message.update(state._authority_fields())
        return message

    def _dispatch_simulation_worker_message(
            self, server, worker, message):
        """Dispatch only authority-owned commands from a native worker."""
        with server.state.lock:
            if (server.state.simulation_worker is not worker or
                    not worker.connected):
                return "close"
        message_type = message.get("type")
        if (message_type in ROUND_SCOPED_MESSAGE_TYPES and
                not server.state._message_round_matches(message)):
            return False
        authority_id = SIMULATION_WORKER_AUTHORITY_ID
        if message_type == "simulation_progress":
            accepted = server.state.update_simulation_progress(worker, message)
        elif message_type == "player_environment":
            accepted = server.state.update_player_environment(
                authority_id, message)
        elif message_type == "fire_intent_result":
            accepted = server.state.resolve_fire_intent(
                authority_id, message)
        elif message_type == "projectile_launch":
            accepted = server.state.launch_projectile(authority_id, message)
            if not accepted and message.get("shooter_kind") == "player":
                server.state.reject_player_projectile_launch(
                    authority_id, message)
        elif message_type == "projectile_progress":
            accepted = server.state.progress_projectiles(
                authority_id, message)
        elif message_type == "projectile_ricochet":
            accepted = server.state.ricochet_projectile(
                authority_id, message)
        elif message_type == "projectile_resolve":
            accepted = server.state.resolve_projectile(authority_id, message)
        elif message_type == "bot_manifest":
            accepted = server.state.update_bot_manifest(
                authority_id, message)
            if (not accepted and
                    server.state.phase in ("loading", "battle") and
                    server.state.battle_result is None):
                reject_code = (
                    server.state.last_bot_manifest_reject_code or
                    "bot_manifest_rejected")
                _server_log(
                    "WORKER MANIFEST fatal code=%s reason=%s" % (
                        reject_code,
                        server.state.last_bot_manifest_reject))
                server.state.remove_simulation_worker(
                    worker, reject_code)
                return "close"
            if accepted:
                _server_log("BOT MANIFEST authority=%d bots=%d" % (
                    authority_id, len(server.state.bot_manifest)))
                loading_snapshot = server.state.loading_snapshot()
                if loading_snapshot is not None:
                    server.state.broadcast_loading_transition(
                        loading_snapshot)
                server.state.activate_battle_if_ready()
        elif message_type == "bot_state":
            accepted = server.state.update_bot_states(
                authority_id, message)
            reject_code = server.state.last_bot_state_reject_code
            if (not accepted and
                    reject_code not in FATAL_BOT_STATE_REJECT_CODES):
                # State publications are atomic.  A timing, precision or
                # model-contract mismatch can safely retain the last-good
                # checkpoint while the local trusted worker sends its next
                # full publication. Malformed soft drops do not prove that
                # authoritative simulation is progressing.
                if server.state.should_log_protocol_reject(
                        "bot_state", accepted):
                    _server_log(
                        "BOT STATE dropped authority=%d code=%s reason=%s" % (
                            authority_id, reject_code,
                            server.state.last_bot_state_reject))
                return False
            elif server.state.should_log_protocol_reject(
                    "bot_state", accepted):
                _server_log(
                    "BOT STATE rejected authority=%d code=%s reason=%s" % (
                        authority_id,
                        server.state.last_bot_state_reject_code,
                        server.state.last_bot_state_reject))
            if (not accepted and
                    server.state.phase in ("loading", "battle") and
                    server.state.battle_result is None):
                # Round and authority conflicts cannot be reconciled on this
                # transport. Publication-local identity/lineage mismatches
                # retain the last-good checkpoint and may recover next tick.
                server.state.remove_simulation_worker(
                    worker, reject_code or "bot_state_rejected")
                return "close"
        elif message_type == "bot_observation":
            relay = server.state.update_bot_observation(
                authority_id, message)
            accepted = relay is not False
            if isinstance(relay, dict):
                server.state.broadcast_bot_observation(relay)
        elif message_type == "bot_hit_report":
            accepted = server.state.report_bot_hit(authority_id, message)
        elif message_type == "bot_human_hit":
            accepted = server.state.report_bot_human_hit(
                authority_id, message)
        elif message_type == "bot_ram_report":
            accepted = server.state.report_bot_ram(authority_id, message)
        elif message_type == "rules_state":
            accepted = server.state.update_rules(authority_id, message)
        elif message_type == "battle_result":
            accepted = server.state.report_battle_result(
                authority_id, message)
        elif message_type == "destructible":
            accepted = server.state.report_destructible(
                authority_id, message)
        elif message_type == "player_destructible_contact_result":
            accepted = server.state.report_player_destructible_contact_result(
                authority_id, message)
        elif message_type == "battle_ready":
            accepted = server.state.mark_battle_ready(
                authority_id, message) is not None
            # The final ready call queues battle_live rather than returning a
            # transport acknowledgement.  A still-loading call is valid too.
            if (not accepted and server.state.phase == "loading" and
                    worker.battle_ready_round == server.state.round_id):
                accepted = True
        elif message_type == "ping":
            return worker.send({
                "type": "pong",
                "seq": message.get("seq"),
                "client_time": message.get("client_time"),
                "server_time": time.time(),
            })
        elif message_type == "leave":
            # This only closes the worker transport.  It never invokes the
            # player leave-battle lifecycle or mutates player statistics.
            return "close"
        else:
            _server_log_limited(
                "worker-command:%s" % message_type,
                "WORKER COMMAND rejected type=%s" % message_type)
            return False
        projectile_commands = (
            "projectile_launch", "projectile_progress",
            "projectile_ricochet", "projectile_resolve")
        if message_type in projectile_commands:
            reject_code = getattr(
                server.state, "last_%s_reject_code" % message_type,
                "unknown")
            if (reject_code != "launch_edge_pending" and
                    server.state.should_log_protocol_reject(
                        message_type, accepted)):
                _server_log(
                    "WORKER COMMAND rejected type=%s code=%s reason=%s" % (
                        message_type,
                        reject_code,
                        getattr(server.state,
                                "last_%s_reject" % message_type,
                                "unknown")))
        elif not accepted:
            _server_log_limited(
                "worker-command:%s" % message_type,
                "WORKER COMMAND rejected type=%s" % message_type)
        return bool(accepted)

    def _handle_simulation_worker(self, server, conn, buffer, hello):
        worker = None
        try:
            with server.state.lock:
                worker, join_error = server.state.add_simulation_worker(
                    conn, self.client_address, hello)
                welcomed = bool(
                    worker is not None and
                    worker.send(self._worker_welcome(server.state, worker)))
            if worker is None:
                messages = {
                    "battle_in_progress": "battle already in progress",
                    "worker_not_supported":
                        "simulation workers require client authority mode",
                    "worker_already_connected":
                        "a simulation worker is already connected",
                    "unsupported_client_build":
                        "simulation worker requires the #1513 client",
                    "incompatible_client_build":
                        "this room is using a different client build",
                    "map_not_available_for_client":
                        "the fixed server map is unavailable in this client build",
                    "unsupported_capabilities":
                        "required worker capabilities are missing",
                }
                self._send_raw(conn, {
                    "type": "error", "code": join_error,
                    "message": messages.get(join_error, "worker rejected"),
                })
                _server_log("WORKER rejected %s:%d code=%s" % (
                    self.client_address[0], self.client_address[1],
                    join_error))
                return
            _server_log("WORKER JOIN id=%d build=%s address=%s:%d" % (
                worker.worker_id, server.state.client_build,
                self.client_address[0], self.client_address[1]))
            if not welcomed:
                return
            server.state.broadcast(server.state.lobby_message())
            conn.settimeout(0.5)
            liveness_key = None
            last_activity = time.monotonic()
            while True:
                now = time.monotonic()
                with server.state.lock:
                    active_phase = (
                        server.state.simulation_worker is worker and
                        worker.connected and
                        (server.state.phase == "loading" or
                         (server.state.phase == "battle" and
                          server.state.battle_result is None)))
                    current_key = (
                        (server.state.round_id, server.state.phase)
                        if active_phase else None)
                if current_key != liveness_key:
                    liveness_key = current_key
                    last_activity = now
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    if not line:
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        _server_log_limited(
                            "worker-message-too-large",
                            "WORKER MESSAGE ignored oversized line")
                        continue
                    message_type = "invalid"
                    try:
                        message = json.loads(line.decode("utf-8"))
                        if not isinstance(message, dict):
                            continue
                        message_type = message.get("type")
                        dispatched = \
                            self._dispatch_simulation_worker_message(
                                server, worker, message)
                        if dispatched == "close":
                            return
                        if (dispatched and current_key is not None and
                                (current_key[1] == "loading" or
                                 message_type in
                                 SIMULATION_WORKER_ADVANCEMENT_TYPES)):
                            last_activity = time.monotonic()
                    except (ConnectionError, OSError):
                        raise
                    except Exception as error:
                        _server_log_limited(
                            "worker-message:%s" % message_type,
                            "WORKER MESSAGE ignored type=%s error=%s" % (
                                message_type, type(error).__name__))
                        continue
                now = time.monotonic()
                liveness_timeout = (
                    SIMULATION_WORKER_LOADING_TIMEOUT_SECONDS
                    if current_key is not None and
                    current_key[1] == "loading" else
                    SIMULATION_WORKER_LIVENESS_TIMEOUT_SECONDS)
                if (current_key is not None and
                        now - last_activity >= liveness_timeout):
                    _server_log(
                        "WORKER TIMEOUT round=%d phase=%s idle=%.2fs" % (
                            current_key[0], current_key[1],
                            now - last_activity))
                    return
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > MAX_LINE_BYTES * 4:
                    return
        finally:
            if worker is not None:
                removed, _failed_over = (
                    server.state.remove_simulation_worker(worker))
                if removed is not None:
                    _server_log("WORKER LEAVE id=%d players=%d" % (
                        removed.worker_id, len(server.state.players)))
                    server.state.broadcast(server.state.lobby_message())

    def handle(self):
        server = self.server.game_server
        conn = self.request
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(10.0)
        player = None
        buffer = b""
        _server_log("TCP connection from %s:%d" % self.client_address)
        try:
            while b"\n" not in buffer and len(buffer) < MAX_LINE_BYTES:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            line, _, buffer = buffer.partition(b"\n")
            hello = json.loads(line.decode("utf-8"))
            if (not isinstance(hello, dict) or hello.get("type") != "hello" or
                    not _compatible_hello_protocol(
                        hello.get("protocol"),
                        hello.get("capabilities", ()))):
                self._send_raw(conn, {"type": "error", "code": "protocol", "message": "protocol mismatch"})
                _server_log("Rejected %s:%d: protocol mismatch" % self.client_address)
                return
            role = hello.get("role", "player")
            if role == "probe":
                client_build = hello.get("client_build")
                raw_capabilities = hello.get("capabilities", ())
                valid_client_build = bool(
                    isinstance(client_build, str) and client_build and
                    len(client_build) <= 128)
                if (not valid_client_build or
                        not _valid_capability_subset(
                            raw_capabilities,
                            MODERN_CLIENT_REQUIRED_CAPABILITIES)):
                    self._send_raw(conn, {
                        "type": "error",
                        "code": "unsupported_capabilities",
                        "message": "launcher probe is incompatible",
                    })
                    _server_log("PROBE rejected %s:%d" %
                                self.client_address)
                    return
                self._send_raw(conn, {
                    "type": "welcome",
                    "protocol": PROTOCOL_VERSION,
                    "client_build": CLIENT_BUILD_0922,
                    "capabilities": list(
                        MODERN_CLIENT_REQUIRED_CAPABILITIES),
                    "server_capabilities": list(SERVER_CAPABILITIES),
                })
                _server_log("PROBE OK %s:%d" % self.client_address)
                overlay = getattr(server, "vehicle_overlay", None)
                if overlay is None:
                    return
                # A launcher probe may ask for the pinned vehicle-data overlay
                # before the game starts.  The exchange is bounded by a short
                # read timeout, so a probe that only checks compatibility
                # still closes quickly.
                conn.settimeout(5.0)
                while True:
                    while b"\n" not in buffer:
                        try:
                            chunk = conn.recv(4096)
                        except socket.timeout:
                            return
                        if not chunk:
                            return
                        buffer += chunk
                        if len(buffer) > MAX_OVERLAY_LINE_BYTES * 2:
                            return
                    line, _, buffer = buffer.partition(b"\n")
                    if not line:
                        continue
                    if len(line) > MAX_OVERLAY_LINE_BYTES:
                        return
                    try:
                        message = json.loads(line.decode("utf-8"))
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    message_type = message.get("type")
                    if message_type == "vehicle_overlay_query":
                        self._send_raw(conn, overlay.manifest_payload())
                    elif message_type == "vehicle_overlay_member":
                        payload = overlay.member_payload(
                            message.get("sourceMember"))
                        if payload is None:
                            self._send_raw(conn, {
                                "type": "error",
                                "code": "unknown_member",
                                "message": "unknown vehicle overlay member",
                            })
                        else:
                            self._send_raw(conn, payload)
                    elif message_type == "leave":
                        return
                return
            if role == SIMULATION_WORKER_ROLE:
                self._handle_simulation_worker(
                    server, conn, buffer, hello)
                return
            if role != "player":
                self._send_raw(conn, {
                    "type": "error", "code": "unsupported_role",
                    "message": "unsupported connection role",
                })
                _server_log("Rejected %s:%d: unsupported role" %
                            self.client_address)
                return
            # Publish membership and this connection's welcome atomically.
            # Otherwise an existing handler can start a battle after add_player
            # releases the state lock but before this handler sends welcome,
            # making battle_start the new client's first state message.
            welcomed = False
            with server.state.lock:
                player, join_error = server.state.add_player(
                    conn, self.client_address, hello)
                if player is not None:
                    welcome_message = {
                        "type": "welcome",
                        "protocol": PROTOCOL_VERSION,
                        "client_build": server.state.client_build,
                        "player_id": player.player_id,
                        "name": player.name,
                        "vehicle": player.vehicle,
                        "vehicle_compact_descr":
                            player.vehicle_compact_descr,
                        "outfits": dict(player.outfits),
                        "effective_params": copy.deepcopy(
                            player.effective_params),
                        "team": player.team,
                        "slot": player.slot,
                        "max_health": player.max_health,
                        "map": server.state.map_name,
                        "map_pool": list(server.state._active_map_pool()),
                        "host_player_id": server.state.host_player_id,
                        "phase": server.state.phase,
                        "round_id": server.state.round_id,
                        "state_revision": server.state.state_revision,
                        "spawn": {"x": player.x, "y": player.y, "z": player.z, "yaw": player.yaw},
                        "bot_authority_id": server.state.bot_authority_id,
                        "team_size": server.state.team_size,
                        "team_sizes": server.state._team_sizes_wire(),
                        "bot_tier_mode": server.state.bot_tier_mode,
                    }
                    if server.state.client_build == CLIENT_BUILD_0922:
                        welcome_message.update({
                            "authority_epoch": server.state.authority_epoch,
                            "capabilities": list(player.capabilities),
                            "server_capabilities": list(SERVER_CAPABILITIES),
                        })
                        welcome_message.update(
                            server.state._authority_fields())
                    welcomed = player.send(welcome_message)
                    if welcomed:
                        welcomed = server.state._deliver_result_receipt(player)
            if player is None:
                messages = {
                    "battle_in_progress": "battle already in progress",
                    "full": "server is full",
                    "team_full": "requested team is full",
                    "invalid_team": "team must be automatic, Team 1, or Team 2",
                    "unsupported_client_build": "unsupported or missing client build",
                    "incompatible_client_build": "this room is using a different client build",
                    "map_not_available_for_client": "the fixed server map is unavailable in this client build",
                    "unsupported_capabilities": "required client capabilities are missing",
                    "invalid_account_key": "invalid offline account identity",
                    "duplicate_account_key": "offline account identity is already connected",
                    "invalid_outfits": "invalid vehicle customization data",
                    "invalid_max_health": "invalid vehicle maximum health",
                    "invalid_effective_params":
                        "invalid effective vehicle parameters",
                }
                message = messages.get(join_error, "join rejected")
                self._send_raw(conn, {"type": "error", "code": join_error, "message": message})
                _server_log("Rejected %s:%d: %s" % (self.client_address[0], self.client_address[1], message))
                return
            _server_log("JOIN id=%d name=%s build=%s vehicle=%s max_hp=%d team=%d address=%s:%d phase=%s players=%d" % (
                player.player_id,
                player.name,
                server.state.client_build,
                player.vehicle,
                player.max_health,
                player.team,
                self.client_address[0],
                self.client_address[1],
                server.state.phase,
                len(server.state.players),
            ))
            if not welcomed:
                return
            server.state.broadcast(server.state.lobby_message())
            current_battle = server.state.current_battle_message()
            if current_battle is not None:
                player.send(current_battle)
                _server_log("LATE JOIN id=%d round=%d map=%s" % (
                    player.player_id,
                    current_battle["round_id"],
                    current_battle["map"],
                ))
            conn.settimeout(0.5)
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_LINE_BYTES * 4:
                    break
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    if not line:
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        _server_log_limited(
                            "visible-message-too-large:%d" %
                            player.player_id,
                            "VISIBLE MESSAGE ignored oversized line "
                            "sender=%d" % player.player_id)
                        continue
                    message_type = "invalid"
                    try:
                        message = json.loads(line.decode("utf-8"))
                        if not isinstance(message, dict):
                            continue
                        message_type = message.get("type")
                        if (message_type in ROUND_SCOPED_MESSAGE_TYPES and
                                not server.state._message_round_matches(message)):
                            continue
                        if (server.state.client_build == CLIENT_BUILD_0922 and
                                message_type not in MODERN_VISIBLE_MESSAGE_TYPES):
                            _server_log_limited(
                                "visible-command:%d:%s" % (
                                    player.player_id, message_type),
                                "VISIBLE COMMAND rejected sender=%d type=%s" % (
                                    player.player_id, message_type))
                            continue
                        if message_type == "input":
                            if server.state.update_input(
                                    player.player_id, message) is False:
                                # Rate-limit per player *and* typed reason so
                                # the first causal field survives any later
                                # cascade of ordering rejections.
                                _server_log_limited(
                                    *server.state.
                                    player_input_rejection_log(
                                        player.player_id))
                        elif message_type == "track_repair":
                            if not server.state.report_track_repair(
                                    player.player_id, message):
                                _server_log_limited(
                                    "track-repair:%d" % player.player_id,
                                    "TRACK REPAIR rejected sender=%d seq=%s" % (
                                        player.player_id,
                                        message.get("repair_seq")))
                        elif message_type == "fire_intent":
                            if not server.state.submit_fire_intent(
                                    player.player_id, message):
                                _server_log_limited(
                                    "fire-intent:%d" % player.player_id,
                                    "FIRE INTENT rejected sender=%d seq=%s" % (
                                        player.player_id,
                                        message.get("intent_seq")))
                        elif message_type == "equipment_intent":
                            if not server.state.submit_equipment_intent(
                                    player.player_id, message):
                                _server_log_limited(
                                    "equipment-intent:%d" % player.player_id,
                                    "EQUIPMENT INTENT rejected sender=%d seq=%s" % (
                                        player.player_id,
                                        message.get("intent_seq")))
                        elif message_type == "landing_observation":
                            if not server.state.submit_landing_observation(
                                    player.player_id, message):
                                _server_log_limited(
                                    "landing-observation:%d" % player.player_id,
                                    "LANDING OBSERVATION rejected sender=%d "
                                    "seq=%s" % (
                                        player.player_id,
                                        message.get("observation_seq")))
                        elif message_type == "hit_report":
                            if not server.state.report_hit(player.player_id, message):
                                _server_log("HIT REPORT rejected attacker=%d target=%s seq=%s" % (
                                    player.player_id, message.get("target"), message.get("shot_seq")))
                        elif message_type == "projectile_launch":
                            if not server.state.launch_projectile(
                                    player.player_id, message):
                                _server_log(
                                    "PROJECTILE LAUNCH rejected sender=%d shooter=%s:%s seq=%s" % (
                                        player.player_id,
                                        message.get("shooter_kind"),
                                        message.get("shooter_id"),
                                        message.get("shot_seq")))
                        elif message_type == "projectile_progress":
                            if not server.state.progress_projectiles(
                                    player.player_id, message):
                                _server_log(
                                    "PROJECTILE PROGRESS rejected sender=%d epoch=%s count=%s" % (
                                        player.player_id,
                                        message.get("authority_epoch"),
                                        len(message.get("cursors", ()))
                                        if isinstance(message.get("cursors"), list)
                                        else None))
                        elif message_type == "projectile_resolve":
                            if not server.state.resolve_projectile(
                                    player.player_id, message):
                                _server_log(
                                    "PROJECTILE RESOLVE rejected sender=%d projectile=%s outcome=%s" % (
                                        player.player_id,
                                        message.get("projectile_id"),
                                        message.get("outcome")))
                        elif message_type == "bot_manifest":
                            if server.state.update_bot_manifest(player.player_id, message):
                                _server_log("BOT MANIFEST authority=%d bots=%d" % (
                                    player.player_id, len(server.state.bot_manifest)))
                                loading_snapshot = server.state.loading_snapshot()
                                if loading_snapshot is not None:
                                    server.state.broadcast_loading_transition(
                                        loading_snapshot)
                                live = server.state.activate_battle_if_ready()
                                if live is not None:
                                    _server_log("BATTLE LIVE round=%d countdown=%.1fs players=%d" % (
                                        live["round_id"], live["countdown_seconds"],
                                        len(server.state.players)))
                            else:
                                _server_log("BOT MANIFEST rejected sender=%d" % player.player_id)
                        elif message_type == "bot_state":
                            accepted = server.state.update_bot_states(
                                player.player_id, message)
                            if server.state.should_log_protocol_reject(
                                    "bot_state", accepted):
                                _server_log(
                                    "BOT STATE rejected authority=%d code=%s reason=%s" % (
                                        player.player_id,
                                        server.state.last_bot_state_reject_code,
                                        server.state.last_bot_state_reject))
                        elif message_type == "bot_observation":
                            relay = server.state.update_bot_observation(
                                player.player_id, message)
                            if isinstance(relay, dict):
                                server.state.broadcast_bot_observation(relay)
                        elif message_type == "spotted_report":
                            if not server.state.update_spotted_targets(
                                    player.player_id, message):
                                _server_log(
                                    "SPOTTED REPORT rejected sender=%d count=%s"
                                    % (player.player_id,
                                       len(message.get("targets"))
                                       if isinstance(message.get("targets"), list)
                                       else None))
                        elif message_type == "bot_hit_report":
                            accepted = server.state.report_bot_hit(
                                player.player_id, message)
                            if server.state.should_log_protocol_reject(
                                    "bot_hit", accepted):
                                _server_log(
                                    ("BOT HIT rejected authority=%d attacker_bot=%s "
                                     "target=%s seq=%s code=%s reason=%s") % (
                                        player.player_id,
                                        message.get("attacker_bot"),
                                        message.get("target"),
                                        message.get("shot_seq"),
                                        server.state.last_bot_hit_reject_code,
                                        server.state.last_bot_hit_reject))
                        elif message_type == "bot_human_hit":
                            accepted = server.state.report_bot_human_hit(
                                player.player_id, message)
                            if server.state.should_log_protocol_reject(
                                    "bot_human_hit", accepted):
                                _server_log(
                                    ("BOT HUMAN HIT rejected authority=%d "
                                     "attacker_bot=%s target=%s seq=%s "
                                     "code=%s reason=%s") % (
                                        player.player_id,
                                        message.get("attacker_bot"),
                                        message.get("target"),
                                        message.get("shot_seq"),
                                        server.state.last_bot_human_hit_reject_code,
                                        server.state.last_bot_human_hit_reject))
                        elif message_type == "bot_ram_report":
                            if not server.state.report_bot_ram(
                                    player.player_id, message):
                                _server_log(
                                    "BOT RAM rejected authority=%d target=%s:%s" % (
                                        player.player_id,
                                        message.get("target_kind"),
                                        message.get("target_id")))
                        elif message_type == "rules_state":
                            server.state.update_rules(player.player_id, message)
                        elif message_type == "destructible":
                            if not server.state.report_destructible(
                                    player.player_id, message):
                                _server_log(
                                    "DESTRUCTIBLE rejected sender=%d chunk=%s item=%s" % (
                                        player.player_id,
                                        message.get("chunk_id"),
                                        message.get("item_index")))
                        elif message_type == "battle_result":
                            if not server.state.report_battle_result(player.player_id, message):
                                _server_log("BATTLE RESULT rejected sender=%d" % player.player_id)
                        elif message_type == "battle_receipt_ack":
                            if not server.state.acknowledge_result_receipt(
                                    player.player_id, message):
                                _server_log(
                                    "BATTLE RECEIPT ACK rejected sender=%d" %
                                    player.player_id)
                        elif message_type == "leave_battle":
                            if server.state.leave_battle_and_publish(
                                    player.player_id, message):
                                _server_log("BATTLE LEAVE id=%d round=%d" % (
                                    player.player_id, server.state.round_id))
                        elif message_type == "battle_ready":
                            live_message = server.state.mark_battle_ready(
                                player.player_id, message)
                            if live_message is not None:
                                _server_log(
                                    "BATTLE LIVE round=%d countdown=%ss players=%d" % (
                                        live_message["round_id"],
                                        live_message["countdown_seconds"],
                                        len(server.state.players)))
                        elif message_type == "start_battle":
                            start_message, start_error = server.state.request_start(
                                player.player_id, message.get("map"))
                            if start_message is None:
                                player.send({
                                    "type": "start_denied",
                                    "protocol": PROTOCOL_VERSION,
                                    "round_id": server.state.round_id,
                                    "state_revision": server.state.state_revision,
                                    "code": start_error,
                                    "players": len(server.state.players),
                                })
                                _server_log("START denied for id=%d: %s" % (player.player_id, start_error))
                            else:
                                _server_log("BATTLE LOADING round=%d map=%s players=%d requested_by=%s" % (
                                    start_message["round_id"],
                                    start_message["map"],
                                    len(start_message["players"]),
                                    player.name,
                                ))
                                if start_message.get("phase") == "loading":
                                    server.state.broadcast_loading_transition(
                                        start_message)
                                else:
                                    # Preserve the mature 0.8.2 immediate-battle
                                    # publisher exactly; only #1513 has a loading
                                    # membership barrier to repair.
                                    server.state.broadcast(start_message)
                        elif message_type == "descriptor_catalog":
                            server.state.store_vehicle_catalog(
                                player.player_id, message)
                        elif message_type == "select_vehicle":
                            if server.state.select_vehicle(
                                    player.player_id, message):
                                _server_log("VEHICLE id=%d vehicle=%s hp=%d" % (
                                    player.player_id, player.vehicle,
                                    player.max_health))
                                server.state.broadcast_current_roster()
                        elif message_type == "select_team":
                            accepted, team_error = server.state.select_team(
                                player.player_id, message.get("team"))
                            if accepted:
                                _server_log("TEAM id=%d team=%d slot=%d" % (
                                    player.player_id, player.team, player.slot))
                                server.state.broadcast_current_roster()
                            else:
                                player.send({
                                    "type": "team_denied",
                                    "protocol": PROTOCOL_VERSION,
                                    "round_id": server.state.round_id,
                                    "state_revision": server.state.state_revision,
                                    "code": team_error,
                                    "team": message.get("team"),
                                    "team_sizes": server.state._team_sizes_wire(),
                                })
                        elif message_type == "set_team_size":
                            accepted, size_error = server.state.set_team_size(
                                player.player_id, message.get("team"),
                                message.get("size"))
                            if accepted:
                                _server_log(
                                    "TEAM SIZE id=%d team=%s size=%s" % (
                                        player.player_id, message.get("team"),
                                        message.get("size")))
                                server.state.broadcast_current_roster()
                            else:
                                player.send({
                                    "type": "team_size_denied",
                                    "protocol": PROTOCOL_VERSION,
                                    "round_id": server.state.round_id,
                                    "state_revision": server.state.state_revision,
                                    "code": size_error,
                                    "team": message.get("team"),
                                    "size": message.get("size"),
                                    "team_sizes": server.state._team_sizes_wire(),
                                })
                        elif message_type == "set_bot_tier_mode":
                            accepted, mode_error = server.state.set_bot_tier_mode(
                                player.player_id, message.get("mode"))
                            if accepted:
                                _server_log(
                                    "BOT TIER MODE id=%d mode=%s" % (
                                        player.player_id,
                                        server.state.bot_tier_mode))
                                server.state.broadcast_current_roster()
                            else:
                                player.send({
                                    "type": "bot_tier_mode_denied",
                                    "protocol": PROTOCOL_VERSION,
                                    "round_id": server.state.round_id,
                                    "state_revision": server.state.state_revision,
                                    "code": mode_error,
                                    "mode": message.get("mode"),
                                    "bot_tier_mode": server.state.bot_tier_mode,
                                })
                        elif message_type == "ping":
                            player.send({
                                "type": "pong",
                                "seq": message.get("seq"),
                                "client_time": message.get("client_time"),
                                "server_time": time.time(),
                            })
                        elif message_type == "leave":
                            return
                    except (ConnectionError, OSError):
                        raise
                    except Exception as error:
                        _server_log_limited(
                            "visible-message:%d:%s" % (
                                player.player_id, message_type),
                            "VISIBLE MESSAGE ignored sender=%d type=%s "
                            "error=%s" % (
                                player.player_id, message_type,
                                type(error).__name__))
                        continue
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
            _server_log("Invalid message from %s:%d: %s" % (self.client_address[0], self.client_address[1], error))
        except (ConnectionError, OSError) as error:
            if _peer_closed_socket(error):
                _server_log_limited(
                    "peer-close:%s:%s" % self.client_address,
                    "Connection closed by %s:%d" % self.client_address)
            else:
                _server_log("Connection error from %s:%d: %s" % (
                    self.client_address[0], self.client_address[1], error))
        finally:
            if player is not None:
                removed, reset = server.state.remove_player(
                    player.player_id, expected=player)
                if removed is not None:
                    _server_log("LEAVE id=%d name=%s remaining=%d" % (
                        removed.player_id, removed.name, len(server.state.players)))
                if reset:
                    _server_log("ROOM RESET round=%d map=%s" % (server.state.round_id, server.state.map_name))
                server.state.broadcast(server.state.lobby_message())
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _send_raw(conn, message):
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        conn.sendall(payload)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _TCPShutdownController:
    """Request socket shutdown without blocking before serve_forever starts."""

    def __init__(self, server):
        self._server = server
        self._lock = threading.Lock()
        self._requested = False
        self._serving = False
        self._shutdown_thread = None

    def _start_shutdown_locked(self):
        if self._shutdown_thread is not None:
            return
        thread = threading.Thread(
            target=self._server.shutdown,
            name="lan-server-shutdown", daemon=True)
        self._shutdown_thread = thread
        thread.start()

    def request(self):
        """Record one idempotent request and never wait for serve_forever."""
        with self._lock:
            if self._requested:
                return False
            self._requested = True
            if self._serving:
                self._start_shutdown_locked()
            return True

    def serve_forever(self, poll_interval=0.5):
        with self._lock:
            if self._requested:
                return
            self._serving = True
        try:
            self._server.serve_forever(poll_interval=poll_interval)
        finally:
            with self._lock:
                self._serving = False

    def wait(self, timeout=None):
        with self._lock:
            thread = self._shutdown_thread
        if thread is not None:
            thread.join(timeout)


def _stop_failed_tick_loop(state, shutdown_callback, stage, error):
    """Stop scheduling and ask the owning TCP server to leave service."""
    state.running = False
    diagnostic = _tick_failure_diagnostic(error)
    _server_log_limited(
        "server-tick-stop:%s" % stage,
        "SERVER TICK stopped stage=%s diagnostic=%s" % (
            stage, diagnostic), interval=1.0)
    if shutdown_callback is None:
        return
    try:
        shutdown_callback()
    except Exception as shutdown_error:
        _server_log_limited(
            "server-tick-shutdown-callback",
            "SERVER TICK shutdown callback failed diagnostic=%s" %
            _tick_failure_diagnostic(shutdown_error), interval=1.0)


def _run_tick_loop(state, tick_clock=None, sleeper=None,
                   failure_handler=None, shutdown_callback=None):
    """Run every due fixed simulation step without dropping late ticks."""
    interval = 1.0 / TICK_HZ
    tick_clock = time.perf_counter if tick_clock is None else tick_clock
    sleeper = time.sleep if sleeper is None else sleeper
    if failure_handler is None:
        failure_handler = getattr(state, "handle_tick_failure", None)
    consecutive_failures = 0
    try:
        next_tick = tick_clock() + interval
        while state.running:
            now = tick_clock()
            delay = next_tick - now
            if delay > 0.0:
                sleeper(delay)
                continue
            # A one-second scheduler stall is thirty due rule steps. Consume
            # all thirty before waiting again; resetting next_tick here would
            # erase timeout, capture, drowning and movement time from battle.
            while state.running and now + 1e-9 >= next_tick:
                try:
                    state.tick_once(interval)
                except Exception as error:
                    consecutive_failures += 1
                    _server_log_limited(
                        "server-tick-failure:%d" % consecutive_failures,
                        "SERVER TICK failure consecutive=%d diagnostic=%s" % (
                            consecutive_failures,
                            _tick_failure_diagnostic(error)),
                        interval=1.0)
                    if (consecutive_failures >=
                            MAX_CONSECUTIVE_TICK_FAILURES):
                        _stop_failed_tick_loop(
                            state, shutdown_callback,
                            "consecutive_tick_failure", error)
                        return
                    if not callable(failure_handler):
                        _stop_failed_tick_loop(
                            state, shutdown_callback,
                            "missing_failure_handler", error)
                        return
                    try:
                        failure_handler(error)
                    except Exception as handler_error:
                        _stop_failed_tick_loop(
                            state, shutdown_callback,
                            "failure_handler", handler_error)
                        return
                else:
                    consecutive_failures = 0
                # A failed tick is consumed exactly once. Retrying the same
                # partially-mutated transaction would make the failure loop
                # unbounded; the handler publishes the terminal on a new tick.
                next_tick += interval
                now = tick_clock()
    except Exception as error:
        _stop_failed_tick_loop(
            state, shutdown_callback, "scheduler", error)


def run_server(host, port, map_name, max_players,
               team_size=15, receipt_state_path=None,
               team1_size=None, team2_size=None,
               bot_tier_mode="random", bot_lineup=None,
               vehicle_overlay_root=None):
    if receipt_state_path is None:
        receipt_state_path = _default_result_receipt_state_path(port)
    state = BattleState(map_name=map_name, max_players=max_players,
                        team_size=team_size,
                        receipt_state_path=receipt_state_path,
                        team1_size=team1_size, team2_size=team2_size,
                        bot_tier_mode=bot_tier_mode,
                        bot_lineup=bot_lineup)
    if vehicle_overlay_root:
        try:
            overlay = VehicleOverlayStore(vehicle_overlay_root)
        except VehicleOverlayStoreError as error:
            _server_log("VEHICLE OVERLAY refused: %s" % error)
            raise
        if overlay.present:
            _server_log(
                "VEHICLE OVERLAY pinned profile=%s digest=%s members=%d" % (
                    overlay.profile, overlay.digest, overlay.member_count))
        else:
            _server_log("VEHICLE OVERLAY none; the room runs stock data")
    else:
        overlay = VehicleOverlayStore()
    tcp_server = ThreadedTCPServer((host, port), ClientHandler)
    tcp_server.game_server = type("GameServer", (), {
        "state": state,
        "vehicle_overlay": overlay,
    })()
    shutdown_controller = _TCPShutdownController(tcp_server)

    thread = threading.Thread(
        target=_run_tick_loop,
        args=(state,),
        kwargs={"shutdown_callback": shutdown_controller.request},
        name="battle-tick", daemon=True)
    thread.start()
    _server_log(
        "LAN battle server listening on %s:%d "
        "(map=%s, max_players=%d, team_sizes=%d:%d)" % (
            host, port, state.map_name, state.max_players,
            state.team_sizes[1], state.team_sizes[2]))
    _server_log("Ready: clients click Battle! to join, choose a map, then click START BATTLE")
    try:
        shutdown_controller.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping server", flush=True)
    finally:
        state.running = False
        shutdown_controller.request()
        tcp_server.server_close()
        thread.join(2.0)
        shutdown_controller.wait(2.0)


def main():
    parser = argparse.ArgumentParser(description="LAN server for the offhangar network MVP")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=28782, help="TCP port (default: 28782)")
    parser.add_argument(
        "--map", dest="map_name", default=DEFAULT_MAP,
        choices=(DEFAULT_MAP,) + ALL_MAP_POOL,
        help="standard map name, or server_random")
    parser.add_argument("--max-players", type=int, default=30, help="maximum connected clients")
    parser.add_argument(
        "--team-size", type=int, choices=range(1, 16), default=15,
        help="legacy default for both team capacities (default: 15)")
    parser.add_argument(
        "--team-1-size", type=int, choices=range(1, 16), default=None,
        help="total Team 1 tanks, including players")
    parser.add_argument(
        "--team-2-size", type=int, choices=range(1, 16), default=None,
        help="total Team 2 tanks, including players")
    args = parser.parse_args()
    run_server(
        args.host, args.port, args.map_name, args.max_players,
        team_size=args.team_size,
        team1_size=args.team_1_size, team2_size=args.team_2_size)


if __name__ == "__main__":
    main()
