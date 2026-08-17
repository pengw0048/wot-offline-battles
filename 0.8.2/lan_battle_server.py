#!/usr/bin/env python3
"""Small LAN battle server for the 0.8.2 offhangar client.

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
import ctypes
import hashlib
import json
import math
import os
import random
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from server_bot_ai import BotPlanner
from server_bot_navigation import BotPathResolver


PROTOCOL_VERSION = 8
CLIENT_BUILD = "1.8.59-native-experimental-20260815"
TICK_HZ = 30.0
SERVER_PERF_LOG_SECONDS = 5.0
PREBATTLE_COUNTDOWN_SECONDS = 30.0
BATTLE_DURATION_SECONDS = 900.0
MAX_LINE_BYTES = 256 * 1024
SERVER_IO_TIMEOUT_SECONDS = 15.0
SERVER_SEND_BUFFER_BYTES = 128 * 1024
BOT_ORDER_WIRE_FIELDS = (
    "id", "target_id", "target_kind", "aim_position", "face_position",
    "move_position", "fire_allowed", "combat_mode", "throttle_override",
    "fire_range", "route_id", "route_index", "route_anchor", "shell_index",
    "defense_base_id",
)
BOT_AUTHORITY_SNAPSHOT_FIELDS = (
    "id", "health", "alive", "nav_source", "nav_order_revision",
    "nav_x", "nav_y", "nav_z",
)
BOT_REPLICA_SNAPSHOT_FIELDS = (
    "id", "world_pose", "x", "y", "z", "yaw", "aim_yaw", "gun_pitch",
    "speed", "turn_velocity", "fire_seq", "shell_index", "health", "alive",
)
BOT_KILLER_SNAPSHOT_FIELDS = (
    "killer_bot_id", "killer_kind", "killer_id",
)
DEFAULT_MAP = "server_random"
MAP_POOL = (
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
BOT_CALLSIGNS = (
    "Atlas", "Badger", "Bison", "Cedar", "Comet", "Condor", "Coyote", "Dagger",
    "Echo", "Falcon", "Frost", "Golem", "Harbor", "Hawk", "Ibis", "Jade",
    "Kestrel", "Lancer", "Lynx", "Mantis", "Maple", "Meteor", "Nomad", "Onyx",
    "Orion", "Otter", "Panda", "Quartz", "Raven", "Rook", "Saber", "Scout",
    "Shark", "Sparrow", "Talon", "Tiger", "Viper", "Wolf", "Yak", "Zephyr",
)
WINDOWS_FIREWALL_RULE_PREFIX = "WoT 0.8.2 LAN Server"
WINDOWS_FIREWALL_REMOTE_IP = "any"


def _server_log(message):
    stamp = time.strftime("%H:%M:%S")
    print("[%s] %s" % (stamp, message), flush=True)


def _is_frozen_windows_executable():
    """Return whether this process is the packaged Windows server executable."""
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _windows_firewall_rule_name(
        executable_path, port, remote_ip=WINDOWS_FIREWALL_REMOTE_IP):
    """Build a stable rule name tied to the executable, port, and scope."""
    normalized_path = str(executable_path).replace("/", "\\").casefold()
    normalized_remote_ip = str(remote_ip).strip().casefold()
    identity = "%s|%d|%s" % (
        normalized_path, int(port), normalized_remote_ip)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return "%s TCP %d [%s]" % (
        WINDOWS_FIREWALL_RULE_PREFIX, int(port), digest)


def _powershell_single_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _windows_firewall_rule_exists(rule_name, runner=None):
    """Check our deterministic inbound rule without requesting elevation."""
    if runner is None:
        runner = subprocess.run
    script = (
        "$rule = Get-NetFirewallRule -DisplayName %s -Direction Inbound "
        "-Enabled True -Action Allow -ErrorAction SilentlyContinue | "
        "Select-Object -First 1; "
        "if ($null -eq $rule) { exit 1 }; exit 0"
    ) % _powershell_single_quote(rule_name)
    result = runner(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def _request_windows_firewall_rule(rule_name, executable_path, port,
                                   shell_execute=None):
    """Open one UAC prompt that runs the narrowly scoped netsh command."""
    arguments = subprocess.list2cmdline([
        "advfirewall", "firewall", "add", "rule",
        "name=" + rule_name,
        "dir=in",
        "action=allow",
        "enable=yes",
        "profile=any",
        "program=" + executable_path,
        "protocol=TCP",
        "localport=%d" % int(port),
        "remoteip=" + WINDOWS_FIREWALL_REMOTE_IP,
    ])
    if shell_execute is None:
        shell_execute = ctypes.windll.shell32.ShellExecuteW
        shell_execute.restype = ctypes.c_void_p
    result = shell_execute(
        None, "runas", "netsh.exe", arguments, None, 1)
    return int(result or 0) > 32


def _ensure_windows_firewall_rule(port):
    """Request a local-subnet inbound rule only for the frozen Windows EXE."""
    if not _is_frozen_windows_executable():
        return False

    executable_path = os.path.abspath(sys.executable)
    rule_name = _windows_firewall_rule_name(executable_path, port)
    try:
        if _windows_firewall_rule_exists(rule_name):
            return True
        _server_log(
            "Windows Firewall access needs approval for LAN clients; "
            "opening one UAC prompt")
        if _request_windows_firewall_rule(
                rule_name, executable_path, port):
            _server_log(
                "Windows Firewall rule request launched for TCP %d "
                "(all remote addresses)" % int(port))
            return True
        _server_log(
            "Windows Firewall rule was not requested; remote LAN clients "
            "may remain blocked")
    except Exception as error:
        _server_log(
            "Windows Firewall rule setup failed (%s); server will continue" %
            error)
    return False


def _finite_float(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(value):
        return float(default)
    return value


def _clamp(value, low, high):
    return max(low, min(high, value))


def _safe_name(value, fallback):
    value = str(value or fallback).strip()
    value = "".join(ch for ch in value if ch.isalnum() or ch in " _-")
    return value[:24] or fallback


def _safe_vehicle(value, fallback):
    value = str(value or fallback).strip()
    value = "".join(ch for ch in value if ch.isalnum() or ch in ":_-")
    return value[:64] or fallback


def _percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(math.ceil((len(ordered) - 1) * float(fraction)))
    return float(ordered[max(0, min(index, len(ordered) - 1))])


def _wire_bot_order(order):
    """Project one planner order onto fields executed by the authority client."""
    return dict((key, order[key]) for key in BOT_ORDER_WIRE_FIELDS if key in order)


def _wire_bot_state(state, fields):
    """Project rich canonical state onto one recipient's steady-state needs."""
    result = dict((key, state[key]) for key in fields if key in state)
    if any(state.get(key) not in (None, "", 0, "0")
           for key in BOT_KILLER_SNAPSHOT_FIELDS):
        for key in BOT_KILLER_SNAPSHOT_FIELDS:
            if key in state:
                result[key] = state[key]
    return result


class _ServerPerfWindow:
    """Low-frequency process and battle-tick diagnostics."""

    STAGES = (
        "movement", "planner", "navigation", "snapshot", "diagnostics",
        "recipients", "events", "encode", "socket",
    )

    def __init__(self, started_at=None, process_started_at=None):
        self.reset(started_at, process_started_at)

    def reset(self, started_at=None, process_started_at=None):
        self.started_at = time.monotonic() if started_at is None else float(started_at)
        self.process_started_at = (
            time.process_time() if process_started_at is None else float(process_started_at))
        self.tick_ms = []
        self.late_ms = []
        self.overruns = 0
        self.messages = 0
        self.bytes = 0
        self.snapshot_messages = 0
        self.snapshot_base_bytes = 0
        self.snapshot_order_bytes = 0
        self.order_attachments = 0
        self.stage_seconds = dict((name, 0.0) for name in self.STAGES)

    def add(self, metrics, elapsed_seconds, late_seconds, interval_seconds):
        if metrics is None:
            return
        self.tick_ms.append(max(0.0, float(elapsed_seconds)) * 1000.0)
        self.late_ms.append(max(0.0, float(late_seconds)) * 1000.0)
        if elapsed_seconds > interval_seconds:
            self.overruns += 1
        for name in self.STAGES:
            self.stage_seconds[name] += float(metrics.get(name + "_seconds", 0.0))
        self.messages += int(metrics.get("messages", 0))
        self.bytes += int(metrics.get("bytes", 0))
        self.snapshot_messages += int(metrics.get("snapshot_messages", 0))
        self.snapshot_base_bytes += int(metrics.get("snapshot_base_bytes", 0))
        self.snapshot_order_bytes += int(metrics.get("snapshot_order_bytes", 0))
        self.order_attachments += int(metrics.get("order_attachments", 0))

    def ready(self, now):
        return bool(self.tick_ms and now - self.started_at >= SERVER_PERF_LOG_SECONDS)

    def summary(self, now=None, process_now=None):
        now = time.monotonic() if now is None else float(now)
        process_now = time.process_time() if process_now is None else float(process_now)
        wall_seconds = max(0.001, now - self.started_at)
        ticks = len(self.tick_ms)
        cpu_percent = max(
            0.0, (process_now - self.process_started_at) * 100.0 / wall_seconds)
        stage_ms = dict(
            (name, self.stage_seconds[name] * 1000.0 / max(1, ticks))
            for name in self.STAGES)
        return {
            "wall_seconds": wall_seconds,
            "ticks": ticks,
            "tick_hz": ticks / wall_seconds,
            "cpu_percent": cpu_percent,
            "tick_avg_ms": sum(self.tick_ms) / max(1, ticks),
            "tick_p95_ms": _percentile(self.tick_ms, 0.95),
            "tick_max_ms": max(self.tick_ms or [0.0]),
            "late_max_ms": max(self.late_ms or [0.0]),
            "overruns": self.overruns,
            "messages_per_second": self.messages / wall_seconds,
            "kilobytes_per_second": self.bytes / wall_seconds / 1024.0,
            "snapshot_messages": self.snapshot_messages,
            "snapshot_base_bytes": (
                self.snapshot_base_bytes / max(1, self.snapshot_messages)),
            "snapshot_order_bytes": (
                self.snapshot_order_bytes / max(1, self.order_attachments)),
            "order_attachments": self.order_attachments,
            "stage_ms": stage_ms,
        }


@dataclass
class Player:
    player_id: int
    conn: socket.socket
    address: Tuple[str, int]
    name: str = "Player"
    vehicle: str = "ussr:MS-1"
    team: int = 1
    slot: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    aim_yaw: float = 0.0
    gun_pitch: float = 0.0
    forward: float = 0.0
    turn: float = 0.0
    fire_seq: int = 0
    shell_index: int = 0
    reported_hits: set = field(default_factory=set, repr=False)
    health: int = 1000
    max_health: int = 1000
    alive: bool = True
    killer_kind: str = ""
    killer_id: int = 0
    client_position: bool = False
    connected: bool = True
    bot_order_revision_sent: int = -1
    bot_order_revision_ack: int = -1
    bot_order_sent_at: float = 0.0
    bot_snapshot_full_pending: bool = False
    last_send_error: str = ""
    send_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    outbound_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    outbound_event: threading.Event = field(default_factory=threading.Event, repr=False)
    outbound_reliable: list = field(default_factory=list, repr=False)
    outbound_latest: dict = field(default_factory=dict, repr=False)
    outbound_seq: int = field(default=0, init=False, repr=False)
    outbound_coalesced: int = field(default=0, init=False, repr=False)
    outbound_inflight_type: str = field(default="", init=False, repr=False)
    outbound_inflight_started: float = field(default=0.0, init=False, repr=False)
    outbound_send_max_seconds: float = field(default=0.0, init=False, repr=False)
    outbound_completed_messages: dict = field(default_factory=dict, init=False, repr=False)
    outbound_completed_bytes: dict = field(default_factory=dict, init=False, repr=False)
    sender_thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    sender_started: bool = field(default=False, init=False, repr=False)

    def start_sender(self):
        """Move socket writes off the simulation tick and coalesce snapshots."""
        if self.sender_started:
            return
        self.sender_started = True
        self.sender_thread = threading.Thread(
            target=self._sender_worker,
            name="wot-lan-sender-%d" % self.player_id,
            daemon=True,
        )
        self.sender_thread.start()

    def stop_sender(self):
        self.connected = False
        with self.outbound_lock:
            self.outbound_reliable[:] = []
            self.outbound_latest.clear()
        self.outbound_event.set()

    def _record_order_sent(self, revision):
        if revision is None:
            return
        self.bot_order_revision_sent = revision
        self.bot_order_sent_at = time.monotonic()

    def _record_completed_send(self, message_type, payload_size):
        with self.outbound_lock:
            self.outbound_completed_messages[message_type] = (
                self.outbound_completed_messages.get(message_type, 0) + 1)
            self.outbound_completed_bytes[message_type] = (
                self.outbound_completed_bytes.get(message_type, 0) +
                int(payload_size))

    def _dequeue_outbound(self):
        with self.outbound_lock:
            reliable = self.outbound_reliable[0] if self.outbound_reliable else None
            latest_key = None
            latest = None
            for key, item in self.outbound_latest.items():
                if latest is None or item[0] < latest[0]:
                    latest_key, latest = key, item
            if reliable is None and latest is None:
                return None
            if latest is None or (reliable is not None and reliable[0] <= latest[0]):
                return self.outbound_reliable.pop(0)
            del self.outbound_latest[latest_key]
            return latest

    def _sender_worker(self):
        while self.connected:
            item = self._dequeue_outbound()
            if item is None:
                self.outbound_event.clear()
                with self.outbound_lock:
                    pending = bool(self.outbound_reliable or self.outbound_latest)
                if pending:
                    self.outbound_event.set()
                    continue
                self.outbound_event.wait(0.1)
                continue
            unused_seq, payload, order_revision, message_type = item
            send_started = time.monotonic()
            with self.outbound_lock:
                self.outbound_inflight_type = message_type
                self.outbound_inflight_started = send_started
            try:
                with self.send_lock:
                    self.conn.sendall(payload)
                self._record_completed_send(message_type, len(payload))
                self._record_order_sent(order_revision)
            except (BrokenPipeError, ConnectionError, OSError) as error:
                self.last_send_error = "%s: %s" % (type(error).__name__, error)
                self.connected = False
                break
            finally:
                elapsed = time.monotonic() - send_started
                with self.outbound_lock:
                    self.outbound_send_max_seconds = max(
                        self.outbound_send_max_seconds, elapsed)
                    self.outbound_inflight_type = ""
                    self.outbound_inflight_started = 0.0
        with self.outbound_lock:
            self.outbound_reliable[:] = []
            self.outbound_latest.clear()
        self.outbound_event.set()

    def outbound_diagnostics(self):
        """Return a cheap, consistent snapshot for the five-second perf log."""
        with self.outbound_lock:
            now = time.monotonic()
            result = {
                "pending_reliable": len(self.outbound_reliable),
                "pending_latest": len(self.outbound_latest),
                "coalesced": self.outbound_coalesced,
                "inflight_type": self.outbound_inflight_type,
                "inflight_age_ms": (
                    max(0.0, (now - self.outbound_inflight_started) * 1000.0)
                    if self.outbound_inflight_started > 0.0 else 0.0),
                "send_max_ms": self.outbound_send_max_seconds * 1000.0,
                "completed_messages": dict(self.outbound_completed_messages),
                "completed_bytes": dict(self.outbound_completed_bytes),
            }
            self.outbound_send_max_seconds = 0.0
            self.outbound_completed_messages.clear()
            self.outbound_completed_bytes.clear()
            return result

    def send(self, message, perf=None, order_payload_bytes=0):
        if not self.connected:
            return False
        encode_started = time.perf_counter()
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        message_type = str(message.get("type") or "unknown")
        if message_type == "snapshot" and "bot_orders" in message:
            message_type = "snapshot_orders"
        if perf is not None:
            perf["encode_seconds"] += time.perf_counter() - encode_started
            perf["messages"] += 1
            perf["bytes"] += len(payload)
            if message.get("type") == "snapshot":
                order_payload_bytes = max(
                    0, min(int(order_payload_bytes), len(payload)))
                perf["snapshot_messages"] = (
                    perf.get("snapshot_messages", 0) + 1)
                perf["snapshot_base_bytes"] = (
                    perf.get("snapshot_base_bytes", 0) +
                    len(payload) - order_payload_bytes)
                perf["snapshot_order_bytes"] = (
                    perf.get("snapshot_order_bytes", 0) + order_payload_bytes)
                if order_payload_bytes:
                    perf["order_attachments"] = (
                        perf.get("order_attachments", 0) + 1)
        if len(payload) > MAX_LINE_BYTES:
            self.last_send_error = "outbound message exceeds %d bytes" % MAX_LINE_BYTES
            return False
        order_revision = None
        if "bot_orders" in message:
            try:
                order_revision = int(message.get("bot_order_revision", -1))
            except (TypeError, ValueError):
                pass
        if self.sender_started:
            with self.outbound_lock:
                self.outbound_seq += 1
                item = (
                    self.outbound_seq, payload, order_revision,
                    message_type,
                )
                if message.get("type") == "snapshot":
                    if "snapshot" in self.outbound_latest:
                        self.outbound_coalesced += 1
                    self.outbound_latest["snapshot"] = item
                else:
                    self.outbound_reliable.append(item)
            self.outbound_event.set()
            return True
        try:
            socket_started = time.perf_counter()
            with self.send_lock:
                self.conn.sendall(payload)
            self._record_completed_send(message_type, len(payload))
            if perf is not None:
                perf["socket_seconds"] += time.perf_counter() - socket_started
            self._record_order_sent(order_revision)
            return True
        except (BrokenPipeError, ConnectionError, OSError) as error:
            if perf is not None:
                perf["socket_seconds"] += time.perf_counter() - socket_started
            self.last_send_error = "%s: %s" % (type(error).__name__, error)
            self.connected = False
            return False


class BattleState:
    def __init__(self, map_name=DEFAULT_MAP, max_players=30):
        self.map_option = map_name
        self.map_name = self._choose_map()
        self.max_players = max(1, min(int(max_players), 64))
        self.players: Dict[int, Player] = {}
        self.next_id = 1
        # Keep teams balanced, but do not permanently pin the room creator to
        # team 1 (and therefore to the same physical spawn on every map).
        self.next_balanced_team = random.choice((1, 2))
        self.tick = 0
        self.lock = threading.RLock()
        self.running = True
        self.phase = "waiting"
        self.round_id = 1
        self.combat_start_at = None
        self.combat_end_at = None
        self.bot_roster = self._new_bot_roster()
        self.bot_authority_id = None
        self.bot_manifest = []
        self.bot_states = {}
        self.bot_state_revision = 0
        self.bot_planner = BotPlanner()
        self.bot_navigation = BotPathResolver()
        self.bot_navigation_targets = {}
        self.bot_navigation_frame = None
        self.bot_orders = {"revision": 0, "orders": []}
        self.bot_order_wire_revision = -1
        self.bot_order_wire_body = []
        self.bot_order_wire_bytes = 0
        self.bot_reported_hits = set()
        self.bot_observation_stats = {1: 0, 2: 0, "accepted": 0}
        self.bot_navigation_stats = {
            "graph": {"source": "none", "cell_mm": 0, "nodes": 0},
            "total": {"safe_direct": 0, "safe_local": 0, "reactive": 0},
            "active": {"safe_direct": 0, "safe_local": 0, "reactive": 0},
            "recovered": 0,
            "search": {"pending": 0, "completed": 0, "failed": 0,
                       "oldest_ms": 0, "tick_age_ms": 0},
            "orders": {"revision": 0, "loaded": 0},
            "aim": {"alive": 0, "targeted": 0, "aligned": 0,
                    "traversing": 0, "limited": 0},
            "driver": {"moving": 0, "drive": 0, "avoid": 0,
                       "blocked": 0, "recovery": 0, "arrived": 0,
                       "server_wait": 0, "traffic_wait": 0,
                       "water_guard": 0, "full": 0,
                       "cruise": 0, "speed_pct": 0, "slow": 0},
            "safety": {"water_guard_total": 0, "water_guard_active": 0,
                       "edge_guard_total": 0, "edge_guard_active": 0,
                       "veto_water": 0, "veto_terrain": 0,
                       "veto_obstacle": 0, "veto_error": 0},
        }
        self.next_bot_ai_log = 0.0
        self.rules_state = {"bases": {
            "1": {"points": 0, "stopped": False, "contributors": {},
                  "active_contributors": [], "invaders": 0, "cursor": 0},
            "2": {"points": 0, "stopped": False, "contributors": {},
                  "active_contributors": [], "invaders": 0, "cursor": 0},
        }}
        self.battle_result = None
        self.pending_events = []

    def _choose_map(self):
        if self.map_option in (None, "", "random", DEFAULT_MAP):
            return random.choice(MAP_POOL)
        return str(self.map_option)

    @staticmethod
    def _new_bot_roster():
        roster = []
        used = set()
        bot_id = 1
        for team in (1, 2):
            for slot in range(15):
                while True:
                    name = "%s-%02d" % (random.choice(BOT_CALLSIGNS), random.randint(10, 99))
                    if name.lower() not in used:
                        used.add(name.lower())
                        break
                roster.append({"id": bot_id, "team": team, "slot": slot, "name": name})
                bot_id += 1
        return roster

    def _elect_bot_authority(self):
        connected = sorted(p.player_id for p in self.players.values() if p.connected)
        old = self.bot_authority_id
        self.bot_authority_id = connected[0] if connected else None
        if old != self.bot_authority_id and self.phase == "battle":
            authority = self.players.get(self.bot_authority_id)
            if authority is not None:
                # Replicas do not receive executable orders.  A promoted client
                # may retain an ACK from an earlier authority tenure, so force
                # delivery of the latest complete revision before it drives.
                authority.bot_order_revision_ack = -1
                authority.bot_order_revision_sent = -1
                authority.bot_order_sent_at = 0.0
                # A relay promoted to authority must apply one complete canonical
                # pose before it starts publishing locally simulated bot state.
                # Its first accepted bot-state report proves that handoff happened.
                # A previous connection alone does not imply that canonical bot
                # state exists.  If the first authority disconnects before it
                # publishes the manifest, the replacement must be allowed to
                # publish that initial manifest instead of waiting forever for an
                # empty handoff snapshot.
                authority.bot_snapshot_full_pending = bool(self.bot_manifest)
            self.bot_planner.clear_observations()
            self.bot_navigation_stats = {
                "graph": {"source": "none", "cell_mm": 0, "nodes": 0},
                "total": {"safe_direct": 0, "safe_local": 0, "reactive": 0},
                "active": {"safe_direct": 0, "safe_local": 0, "reactive": 0},
                "recovered": 0,
                "search": {"pending": 0, "completed": 0, "failed": 0,
                           "oldest_ms": 0, "tick_age_ms": 0},
                "orders": {"revision": 0, "loaded": 0},
                "aim": {"alive": 0, "targeted": 0, "aligned": 0,
                        "traversing": 0, "limited": 0},
                "driver": {"moving": 0, "drive": 0, "avoid": 0,
                           "blocked": 0, "recovery": 0, "arrived": 0,
                           "server_wait": 0, "traffic_wait": 0,
                           "water_guard": 0, "full": 0,
                           "cruise": 0, "speed_pct": 0, "slow": 0},
                "safety": {"water_guard_total": 0, "water_guard_active": 0,
                           "edge_guard_total": 0, "edge_guard_active": 0,
                           "veto_water": 0, "veto_terrain": 0,
                           "veto_obstacle": 0, "veto_error": 0},
            }
            self.pending_events.append({
                "kind": "authority",
                "player_id": self.bot_authority_id,
            })
        return old, self.bot_authority_id

    def _spawn_for(self, slot, team):
        # Coordinates are intentionally simple and are also sent to clients.
        # The client maps these onto the same local battle space.
        # Keep the synthetic arena small; clients map it onto the loaded map.
        return self._spawn_x_for(slot), self._spawn_z_for(team), (0.0 if team == 1 else math.pi)

    def _assign_team_and_slot(self):
        counts = {1: 0, 2: 0}
        for player in self.players.values():
            if player.connected and player.team in counts:
                counts[player.team] += 1
        if counts[1] < counts[2]:
            team = 1
        elif counts[2] < counts[1]:
            team = 2
        else:
            team = self.next_balanced_team
            self.next_balanced_team = 1 if team == 2 else 2
        return team, counts[team]

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
            if len(self.players) >= self.max_players:
                return None, "full", None
            if self.phase == "battle":
                # A frozen manifest occupies every non-human slot selected at
                # battle start. Rejecting from the phase boundary also closes the
                # race while the authority is publishing that first manifest.
                return None, "battle_in_progress", None
            player_id = self.next_id
            self.next_id += 1
            team, slot = self._assign_team_and_slot()
            x, z, yaw = self._spawn_for(slot, team)
            player = Player(
                player_id=player_id,
                conn=conn,
                address=address,
                name=self._unique_name(hello.get("name"), address, player_id),
                vehicle=_safe_vehicle(hello.get("vehicle"), "ussr:MS-1"),
                team=team,
                slot=slot,
                x=x,
                z=z,
                yaw=yaw,
                aim_yaw=yaw,
                health=max(1, min(int(_finite_float(hello.get("max_health"), 1000)), 100000)),
                max_health=max(1, min(int(_finite_float(hello.get("max_health"), 1000)), 100000)),
            )
            self.players[player_id] = player
            player.start_sender()
            welcome = {
                "type": "welcome",
                "protocol": PROTOCOL_VERSION,
                "client_build": CLIENT_BUILD,
                "player_id": player.player_id,
                "name": player.name,
                "vehicle": player.vehicle,
                "team": player.team,
                "slot": player.slot,
                "max_health": player.max_health,
                "map": self.map_name,
                "map_pool": list(MAP_POOL),
                "phase": self.phase,
                "round_id": self.round_id,
                "spawn": {
                    "x": player.x, "y": player.y,
                    "z": player.z, "yaw": player.yaw,
                },
                "bot_authority_id": self.bot_authority_id,
            }
            if not player.send(welcome):
                self.remove_player(player.player_id, expected_player=player)
                return None, "send_failed", None
            # Publish the exact room and current-battle state before releasing
            # the lock. A tick or concurrent start can only enqueue after this
            # connection's welcome/roster/battle bootstrap sequence.
            self.broadcast_lobby()
            if self.players.get(player.player_id) is not player:
                return None, "send_failed", None
            current_battle = self.current_battle_message()
            if current_battle is not None and not player.send(current_battle):
                self.remove_player(player.player_id, expected_player=player)
                self.broadcast_lobby()
                return None, "send_failed", None
            return player, None, current_battle

    def remove_player(self, player_id, expected_player=None):
        with self.lock:
            player = self.players.get(player_id)
            # Player ids restart at one after a room reset. A delayed sender or
            # handler from the previous round must never remove the new object
            # that reused its numeric id.
            if expected_player is not None and player is not expected_player:
                return None, False
            player = self.players.pop(player_id, None)
            if player is not None:
                player.stop_sender()
            if player_id == self.bot_authority_id:
                self._elect_bot_authority()
            reset = False
            if not self.players and self.phase == "battle":
                self.phase = "waiting"
                self.round_id += 1
                self.combat_start_at = None
                self.combat_end_at = None
                self.next_id = 1
                self.next_balanced_team = random.choice((1, 2))
                self.tick = 0
                self.map_name = self._choose_map()
                self.bot_roster = self._new_bot_roster()
                self.bot_authority_id = None
                self.bot_manifest = []
                self.bot_states = {}
                self.bot_state_revision = 0
                self.bot_planner.reset()
                self.bot_navigation.reset()
                self.bot_navigation_targets = {}
                self.bot_navigation_frame = None
                self.bot_orders = {"revision": 0, "orders": []}
                self.bot_order_wire_revision = -1
                self.bot_order_wire_body = []
                self.bot_order_wire_bytes = 0
                self.bot_reported_hits = set()
                self.bot_observation_stats = {1: 0, 2: 0, "accepted": 0}
                self.bot_navigation_stats = {
                    "graph": {"source": "none", "cell_mm": 0, "nodes": 0},
                    "total": {"safe_direct": 0, "safe_local": 0, "reactive": 0},
                    "active": {"safe_direct": 0, "safe_local": 0, "reactive": 0},
                    "recovered": 0,
                    "search": {"pending": 0, "completed": 0, "failed": 0,
                               "oldest_ms": 0, "tick_age_ms": 0},
                    "orders": {"revision": 0, "loaded": 0},
                    "aim": {"alive": 0, "targeted": 0, "aligned": 0,
                            "traversing": 0, "limited": 0},
                    "driver": {"moving": 0, "drive": 0, "avoid": 0,
                               "blocked": 0, "recovery": 0, "arrived": 0,
                               "server_wait": 0, "traffic_wait": 0,
                               "water_guard": 0, "full": 0,
                               "cruise": 0, "speed_pct": 0, "slow": 0},
                    "safety": {"water_guard_total": 0, "water_guard_active": 0,
                               "edge_guard_total": 0, "edge_guard_active": 0,
                               "veto_water": 0, "veto_terrain": 0,
                               "veto_obstacle": 0, "veto_error": 0},
                }
                self.next_bot_ai_log = 0.0
                self.rules_state = {"bases": {
                    "1": {"points": 0, "stopped": False, "contributors": {},
                          "active_contributors": [], "invaders": 0, "cursor": 0},
                    "2": {"points": 0, "stopped": False, "contributors": {},
                          "active_contributors": [], "invaders": 0, "cursor": 0},
                }}
                self.battle_result = None
                self.pending_events = []
                reset = True
            return player, reset

    def lobby_message(self):
        with self.lock:
            return {
                "type": "roster",
                "protocol": PROTOCOL_VERSION,
                "phase": self.phase,
                "round_id": self.round_id,
                "map": self.map_name,
                "map_pool": list(MAP_POOL),
                "bot_authority_id": self.bot_authority_id,
                "players": [self._public_player(p) for p in self.players.values() if p.connected],
            }

    def broadcast_lobby(self):
        """Queue one internally consistent roster for the current players."""
        with self.lock:
            while True:
                message = self.lobby_message()
                players = list(self.players.values())
                failed = []
                for player in players:
                    if not player.send(message):
                        failed.append(player)
                if not failed:
                    return
                removed_any = False
                for player in failed:
                    removed, unused_reset = self.remove_player(
                        player.player_id, expected_player=player)
                    removed_any = removed_any or removed is not None
                if not removed_any:
                    return

    def request_start(self, player_id, requested_map=None):
        with self.lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return None, "player_not_found"
            if self.phase != "waiting":
                return None, "already_started"
            if requested_map not in (None, ""):
                requested_map = str(requested_map)
                if requested_map not in MAP_POOL:
                    return None, "invalid_map"
                self.map_name = requested_map
            connected = [p for p in self.players.values() if p.connected]
            self.phase = "battle"
            timing_now = time.monotonic()
            self.combat_start_at = timing_now + PREBATTLE_COUNTDOWN_SECONDS
            self.combat_end_at = self.combat_start_at + BATTLE_DURATION_SECONDS
            self._elect_bot_authority()
            return {
                "type": "battle_start",
                "protocol": PROTOCOL_VERSION,
                "round_id": self.round_id,
                "map": self.map_name,
                "requested_by": player_id,
                "delay": 0.75,
                "timing": self._timing_payload(timing_now),
                "players": [self._public_player(p) for p in connected],
                "bots": list(self.bot_roster),
                "bot_authority_id": self.bot_authority_id,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
            }, None

    def current_battle_message(self):
        with self.lock:
            if self.phase != "battle":
                return None
            connected = [p for p in self.players.values() if p.connected]
            return {
                "type": "battle_start",
                "protocol": PROTOCOL_VERSION,
                "round_id": self.round_id,
                "map": self.map_name,
                "requested_by": 0,
                "delay": 0.75,
                "late_join": True,
                "timing": self._timing_payload(),
                "players": [self._public_player(p) for p in connected],
                "bots": list(self.bot_roster),
                "bot_authority_id": self.bot_authority_id,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
            }

    def _timing_payload(self, now=None):
        """Return server-authoritative phase time as relative milliseconds."""
        if now is None:
            now = time.monotonic()
        if self.combat_start_at is None or self.combat_end_at is None:
            return {
                "phase": "loading",
                "start_in_ms": 0,
                "remaining_ms": int(BATTLE_DURATION_SECONDS * 1000.0),
                "duration_ms": int(BATTLE_DURATION_SECONDS * 1000.0),
            }
        if self.battle_result is not None or now >= self.combat_end_at:
            phase = "finished"
        elif now < self.combat_start_at:
            phase = "prebattle"
        else:
            phase = "battle"
        return {
            "phase": phase,
            "start_in_ms": max(0, int((self.combat_start_at - now) * 1000.0)),
            "remaining_ms": max(0, int((self.combat_end_at - max(
                now, self.combat_start_at)) * 1000.0)),
            "duration_ms": int(BATTLE_DURATION_SECONDS * 1000.0),
        }

    def _wire_order_dispatch(self):
        """Return one immutable, compact authority order body per revision."""
        revision = int(self.bot_orders.get("revision", 0))
        if revision != self.bot_order_wire_revision:
            body = [
                _wire_bot_order(order)
                for order in self.bot_orders.get("orders", ())
            ]
            encoded_body = json.dumps(
                body, separators=(",", ":")).encode("utf-8")
            self.bot_order_wire_revision = revision
            self.bot_order_wire_body = body
            # Adding a final compact-JSON member replaces only the original
            # closing brace, so this is the exact snapshot byte increment.
            self.bot_order_wire_bytes = (
                len(b',"bot_orders":') + len(encoded_body))
        return (
            self.bot_order_wire_revision,
            self.bot_order_wire_body,
            self.bot_order_wire_bytes,
        )

    def acknowledge_bot_orders(self, player_id, message):
        """Record application-level delivery, not merely a successful TCP write."""
        with self.lock:
            player = self.players.get(player_id)
            if self.phase != "battle" or player is None or not player.connected:
                return False
            try:
                revision = int(message.get("revision", -1))
            except (TypeError, ValueError):
                return False
            current = int(self.bot_orders.get("revision", 0))
            if revision < 0 or revision > current:
                return False
            player.bot_order_revision_ack = max(
                int(player.bot_order_revision_ack), revision)
            return True

    def request_bot_order_resync(self, player_id):
        """Force the next snapshot to carry the current executable order body."""
        with self.lock:
            player = self.players.get(player_id)
            if self.phase != "battle" or player is None or not player.connected:
                return False
            player.bot_order_revision_ack = -1
            player.bot_order_revision_sent = -1
            player.bot_order_sent_at = 0.0
            return True

    def update_bot_manifest(self, player_id, message):
        """Accept the canonical bot lineup from the elected simulation client."""
        with self.lock:
            if self.phase != "battle" or player_id != self.bot_authority_id:
                return False
            try:
                if int(message.get("round_id", -1)) != int(self.round_id):
                    return False
            except (TypeError, ValueError):
                return False
            manifest_nonce = message.get("manifest_nonce")
            if (not isinstance(manifest_nonce, str) or
                    not manifest_nonce or len(manifest_nonce) > 96):
                return False
            incoming = message.get("bots") or []
            if not isinstance(incoming, (list, tuple)):
                return False
            navigation_frame = BotPathResolver.sanitize_frame(
                message.get("map_frame"))
            if navigation_frame is None:
                return False
            roster = {entry["id"]: entry for entry in self.bot_roster}
            if self.bot_manifest:
                # A late join or authority handoff must preserve the bot identities
                # frozen when the battle began. Recomputing from the current humans
                # could silently add or remove a bot during the same round.
                expected_ids = set(
                    int(entry["id"]) for entry in self.bot_manifest)
            else:
                occupied_slots = set(
                    (int(player.team), int(player.slot))
                    for player in self.players.values()
                    if player.connected and int(player.team) in (1, 2))
                expected_ids = set(
                    bot_id for bot_id, entry in roster.items()
                    if (int(entry["team"]), int(entry["slot"])) not in occupied_slots
                ) if occupied_slots else set(roster)
            manifest = []
            states = {}
            seen = set()
            for raw in incoming[:30]:
                if not isinstance(raw, dict):
                    continue
                if raw.get("world_pose") is not True or any(
                        key not in raw for key in ("x", "y", "z", "yaw")):
                    continue
                try:
                    if any(not math.isfinite(float(raw[key])) for key in
                           ("x", "y", "z", "yaw")):
                        continue
                except (TypeError, ValueError):
                    continue
                try:
                    bot_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    continue
                identity = roster.get(bot_id)
                if identity is None or bot_id in seen:
                    continue
                if int(raw.get("team", identity["team"])) != identity["team"]:
                    continue
                if int(raw.get("slot", identity["slot"])) != identity["slot"]:
                    continue
                seen.add(bot_id)
                max_health = max(1, min(int(_finite_float(raw.get("max_health"), 1000)), 100000))
                health = max(0, min(int(_finite_float(raw.get("health"), max_health)), max_health))
                entry = {
                    "id": bot_id,
                    "team": identity["team"],
                    "slot": identity["slot"],
                    "name": identity["name"],
                    "vehicle": _safe_vehicle(raw.get("vehicle"), "ussr:MS-1"),
                    "max_health": max_health,
                    "health": health,
                    "profile": self._sanitize_bot_profile(raw.get("profile")),
                    "route": self._sanitize_bot_route(raw.get("route")),
                }
                state = self._sanitize_bot_state(raw, entry, None)
                entry.update({
                    "world_pose": True,
                    "x": state["x"], "y": state["y"], "z": state["z"],
                    "yaw": state["yaw"], "aim_yaw": state["aim_yaw"],
                })
                manifest.append(entry)
                states[bot_id] = state
            if not manifest or seen != expected_ids:
                return False
            # The first accepted manifest is immutable for the round. Replays of
            # the identical nonce/body are idempotent; the same IDs with changed
            # tanks, poses, routes or coordinate frame are not a revision protocol.
            if self.bot_manifest:
                return bool(
                    manifest == self.bot_manifest and
                    navigation_frame == self.bot_navigation_frame)
            try:
                changed = self.bot_navigation.configure(
                    self.map_name, navigation_frame)
                if changed:
                    details = self.bot_navigation.diagnostics()
                    _server_log(
                        "BOT NAV loaded map=%s nodes=%d install=%.1fms context=%s source=%s" % (
                            details["map"], details["nodes"],
                            details["install_ms"], details["context"],
                            self.bot_navigation.graph_path))
            except Exception as error:
                self.bot_navigation.reset()
                self.bot_navigation_targets = {}
                _server_log("BOT NAV unavailable map=%s error=%s" % (
                    self.map_name, error))
                return False
            self.bot_manifest = manifest
            if not self.bot_states:
                self.bot_states = states
            self.bot_navigation_frame = navigation_frame
            self.pending_events.append({
                "kind": "bot_manifest", "bots": list(manifest)})
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
        waypoints = raw.get("waypoints") or []
        if not isinstance(waypoints, (list, tuple)):
            waypoints = []
        for point in waypoints[:16]:
            if not isinstance(point, dict):
                continue
            route["waypoints"].append({
                "x": round(_clamp(_finite_float(point.get("x")), -2000.0, 2000.0), 3),
                "y": round(_clamp(_finite_float(point.get("y")), -1000.0, 1000.0), 3),
                "z": round(_clamp(_finite_float(point.get("z")), -2000.0, 2000.0), 3),
                "hold": bool(point.get("hold", False)),
            })
        return route

    def update_bot_observation(self, player_id, message):
        """Accept authority observations; never derive contacts from snapshots."""
        with self.lock:
            if self.phase != "battle" or player_id != self.bot_authority_id:
                return False
            players = [self._public_player(p) for p in self.players.values() if p.connected]
            known_targets = self.bot_planner.known_targets(list(self.bot_states.values()), players)
            now = time.monotonic()
            accepted_contacts = self.bot_planner.report_contacts(
                message.get("contacts"), known_targets, now)
            known_bots = self.bot_planner.known_bots(
                self.bot_manifest, list(self.bot_states.values()))
            accepted_affordances = self.bot_planner.report_affordances(
                message.get("affordances"), known_bots, known_targets, now)
            visible_reports = {1: 0, 2: 0}
            raw_contacts = message.get("contacts")
            if isinstance(raw_contacts, (list, tuple)):
                for raw in raw_contacts:
                    if not isinstance(raw, dict) or not bool(raw.get("visible")):
                        continue
                    team = int(_finite_float(raw.get("observing_team"), 0))
                    if team in visible_reports:
                        visible_reports[team] += 1
            self.bot_observation_stats = {
                1: visible_reports[1], 2: visible_reports[2],
                "accepted": accepted_contacts,
            }
            raw_navigation = message.get("navigation")
            if isinstance(raw_navigation, dict):
                navigation = {"graph": {}, "total": {}, "active": {}, "search": {}}
                raw_graph = raw_navigation.get("graph")
                if not isinstance(raw_graph, dict):
                    raw_graph = {}
                graph_source = str(raw_graph.get("source") or "none")
                if graph_source not in ("baked", "runtime"):
                    graph_source = "none"
                navigation["graph"] = {
                    "source": graph_source,
                    "cell_mm": max(0, min(int(_finite_float(
                        raw_graph.get("cell_mm"), 0)), 100000)),
                    "nodes": max(0, min(int(_finite_float(
                        raw_graph.get("nodes"), 0)), 100000)),
                }
                for group in ("total", "active"):
                    raw_group = raw_navigation.get(group)
                    if not isinstance(raw_group, dict):
                        raw_group = {}
                    for name in ("safe_direct", "safe_local", "reactive"):
                        navigation[group][name] = max(0, min(
                            int(_finite_float(raw_group.get(name), 0)), 100000))
                navigation["recovered"] = max(0, min(
                    int(_finite_float(raw_navigation.get("recovered"), 0)), 100000))
                raw_search = raw_navigation.get("search")
                if not isinstance(raw_search, dict):
                    raw_search = {}
                for name in ("pending", "completed", "failed", "oldest_ms",
                             "tick_age_ms"):
                    navigation["search"][name] = max(0, min(
                        int(_finite_float(raw_search.get(name), 0)), 3600000))
                raw_orders = raw_navigation.get("orders")
                if not isinstance(raw_orders, dict):
                    raw_orders = {}
                navigation["orders"] = {
                    "revision": max(0, min(int(_finite_float(
                        raw_orders.get("revision"), 0)), 1000000000)),
                    "loaded": max(0, min(int(_finite_float(
                        raw_orders.get("loaded"), 0)), 30)),
                }
                raw_aim = raw_navigation.get("aim")
                if not isinstance(raw_aim, dict):
                    raw_aim = {}
                navigation["aim"] = {}
                for name in ("alive", "targeted", "aligned", "traversing", "limited"):
                    navigation["aim"][name] = max(0, min(
                        int(_finite_float(raw_aim.get(name), 0)), 30))
                raw_driver = raw_navigation.get("driver")
                if not isinstance(raw_driver, dict):
                    raw_driver = {}
                navigation["driver"] = {}
                for name in ("moving", "drive", "avoid", "blocked", "recovery",
                             "arrived", "server_wait", "traffic_wait",
                             "water_guard", "full", "cruise", "slow"):
                    navigation["driver"][name] = max(0, min(
                        int(_finite_float(raw_driver.get(name), 0)), 30))
                navigation["driver"]["speed_pct"] = max(0, min(
                    int(_finite_float(raw_driver.get("speed_pct"), 0)), 200))
                raw_safety = raw_navigation.get("safety")
                if not isinstance(raw_safety, dict):
                    raw_safety = {}
                navigation["safety"] = {}
                for name in ("water_guard_total", "water_guard_active",
                             "edge_guard_total", "edge_guard_active", "veto_water",
                             "veto_terrain", "veto_obstacle", "veto_error"):
                    maximum = 100000 if name.endswith("_total") else 30
                    navigation["safety"][name] = max(0, min(
                        int(_finite_float(raw_safety.get(name), 0)), maximum))
                self.bot_navigation_stats = navigation
            return accepted_contacts > 0 or accepted_affordances > 0

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
        try:
            killer_bot_id = max(0, min(int(raw.get("killer_bot_id", 0)), 30))
        except (TypeError, ValueError):
            killer_bot_id = 0
        if previous is not None and not killer_bot_id:
            killer_bot_id = int(previous.get("killer_bot_id", 0) or 0)
        killer_kind = str(raw.get("killer_kind") or "")
        try:
            killer_id = max(0, int(raw.get("killer_id", 0) or 0))
        except (TypeError, ValueError):
            killer_id = 0
        if killer_kind not in ("bot", "human") or not killer_id:
            if killer_bot_id:
                killer_kind = "bot"
                killer_id = killer_bot_id
            else:
                killer_kind = ""
                killer_id = 0
        if previous is not None and not killer_id:
            previous_kind = str(previous.get("killer_kind") or "")
            previous_id = int(previous.get("killer_id", 0) or 0)
            if previous_kind in ("bot", "human") and previous_id:
                killer_kind = previous_kind
                killer_id = previous_id
        yaw = _finite_float(raw.get("yaw"), 0.0)
        return {
            "id": int(identity["id"]),
            "team": int(identity["team"]),
            "slot": int(identity["slot"]),
            "name": identity["name"],
            "vehicle": identity.get("vehicle", "ussr:MS-1"),
            "world_pose": True,
            "x": round(_clamp(_finite_float(raw.get("x")), -2000.0, 2000.0), 4),
            "y": round(_clamp(_finite_float(raw.get("y")), -1000.0, 1000.0), 4),
            "z": round(_clamp(_finite_float(raw.get("z")), -2000.0, 2000.0), 4),
            "yaw": round(yaw, 5),
            "aim_yaw": round(_finite_float(raw.get("aim_yaw"), yaw), 5),
            "gun_pitch": round(_clamp(_finite_float(raw.get("gun_pitch")), -1.2, 1.2), 5),
            "speed": round(_clamp(_finite_float(raw.get("speed")), -80.0, 80.0), 4),
            "turn_velocity": round(_clamp(
                _finite_float(raw.get("turn_velocity")), -10.0, 10.0), 5),
            "fire_seq": fire_seq,
            "shell_index": max(0, min(int(_finite_float(raw.get("shell_index"), 0)), 9)),
            "health": reported_health,
            "max_health": max_health,
            "killer_bot_id": killer_bot_id,
            "killer_kind": killer_kind,
            "killer_id": killer_id,
            "alive": bool(raw.get("alive", reported_health > 0)) and reported_health > 0,
            "mobility_disabled": bool(raw.get("mobility_disabled", False)),
            "mobility_repair_seconds": round(_clamp(_finite_float(
                raw.get("mobility_repair_seconds"), 0.0), 0.0, 30.0), 3)
                if bool(raw.get("mobility_disabled", False)) else 0.0,
        }

    def update_bot_states(self, player_id, message):
        with self.lock:
            if self.phase != "battle" or player_id != self.bot_authority_id or not self.bot_manifest:
                return False
            identities = {entry["id"]: entry for entry in self.bot_manifest}
            accepted = False
            incoming = message.get("bots") or []
            if not isinstance(incoming, (list, tuple)):
                return False
            for raw in incoming[:30]:
                if not isinstance(raw, dict):
                    continue
                try:
                    bot_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    continue
                identity = identities.get(bot_id)
                if identity is None:
                    continue
                previous = self.bot_states.get(bot_id)
                updated = self._sanitize_bot_state(raw, identity, previous)
                self.bot_states[bot_id] = updated
                accepted = True
                if (previous is not None and
                        int(updated.get("fire_seq", 0)) > int(previous.get("fire_seq", 0))):
                    self.pending_events.append({
                        "kind": "bot_shot", "attacker_bot": bot_id,
                        "team": int(identity["team"]),
                        "shot_seq": int(updated["fire_seq"]),
                        "shell_index": int(updated.get("shell_index", 0)),
                    })
            if accepted:
                self.bot_state_revision += 1
                authority = self.players.get(player_id)
                if authority is not None:
                    authority.bot_snapshot_full_pending = False
            return accepted

    def report_bot_hit(self, player_id, message):
        """Apply a human shot against a server-owned bot HP record."""
        with self.lock:
            attacker = self.players.get(player_id)
            # A shell fired while alive remains authoritative after its shooter
            # is destroyed.  fire_seq still proves it was emitted before death.
            if self.phase != "battle" or attacker is None or not attacker.connected:
                return False
            try:
                shot_seq = int(message.get("shot_seq", 0))
                bot_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return False
            hit_key = ("bot", shot_seq, bot_id)
            state = self.bot_states.get(bot_id)
            if (state is None or not state.get("alive") or state.get("team") == attacker.team or
                    shot_seq <= 0 or shot_seq > attacker.fire_seq or hit_key in attacker.reported_hits):
                return False
            attacker.reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            applied = min(damage, int(state.get("health", 0)))
            critical = bool(message.get("critical", False))
            state["health"] -= applied
            state["alive"] = state["health"] > 0
            if not state["alive"]:
                state["killer_kind"] = "human"
                state["killer_id"] = player_id
                state["killer_bot_id"] = 0
            self.pending_events.append({
                "kind": "bot_hit", "attacker": player_id, "target_bot": bot_id,
                "shot_seq": shot_seq, "shell_index": attacker.shell_index,
                "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": state["health"], "dead": not state["alive"],
                "critical": critical, "capture_reset": bool(applied > 0 or critical),
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), state["x"]), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), state["y"] + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), state["z"]), -2000.0, 2000.0), 4),
            })
            return True

    def report_bot_human_hit(self, player_id, message):
        """Apply an authority-resolved bot shot against shared human HP."""
        with self.lock:
            if self.phase != "battle" or player_id != self.bot_authority_id:
                return False
            try:
                bot_id = int(message.get("attacker_bot", 0))
                target_id = int(message.get("target", 0))
                shot_seq = int(message.get("shot_seq", 0))
            except (TypeError, ValueError):
                return False
            bot = self.bot_states.get(bot_id)
            target = self.players.get(target_id)
            # The bot can die while an earlier shell is still travelling.  Its
            # published fire_seq is the immutable launch proof for that shell.
            if bot is None or target is None:
                return False
            if bot.get("team") == target.team:
                return False
            hit_key = (bot_id, shot_seq, target_id)
            if (shot_seq <= 0 or shot_seq > int(bot.get("fire_seq", 0)) or
                    hit_key in self.bot_reported_hits):
                return False
            if not target.alive and target.killer_id:
                return False
            self.bot_reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            applied = min(damage, target.health)
            critical = bool(message.get("critical", False))
            target.health -= applied
            target.alive = target.health > 0
            if not target.alive:
                target.killer_kind = "bot"
                target.killer_id = bot_id
            self.pending_events.append({
                "kind": "bot_human_hit", "attacker_bot": bot_id, "target": target_id,
                "shot_seq": shot_seq, "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": target.health, "dead": not target.alive,
                "critical": critical, "capture_reset": bool(applied > 0 or critical),
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            })
            return True

    def update_rules(self, player_id, message):
        with self.lock:
            if self.phase != "battle" or player_id != self.bot_authority_id or self.battle_result is not None:
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
                contributors = {}
                incoming_contributors = raw.get("contributors") or {}
                if isinstance(incoming_contributors, dict):
                    for vehicle_id, points in list(incoming_contributors.items())[:64]:
                        try:
                            points = max(0, min(int(_finite_float(points, 0)), 100))
                        except (TypeError, ValueError):
                            continue
                        if points:
                            contributors[str(vehicle_id)[:64]] = points
                active_contributors = []
                raw_active = raw.get("active_contributors") or ()
                if not isinstance(raw_active, (list, tuple)):
                    raw_active = ()
                for vehicle_id in raw_active[:30]:
                    value = str(vehicle_id)[:64]
                    parts = value.split(":", 1)
                    if len(parts) != 2 or parts[0] not in ("human", "bot"):
                        continue
                    try:
                        identity_id = int(parts[1])
                    except (TypeError, ValueError):
                        continue
                    if identity_id <= 0:
                        continue
                    if parts[0] == "human":
                        identity = self.players.get(identity_id)
                        if (identity is None or not identity.connected or
                                not identity.alive or identity.team == team):
                            continue
                    else:
                        identity = next((entry for entry in self.bot_roster
                                         if entry["id"] == identity_id), None)
                        state = self.bot_states.get(identity_id)
                        if (identity is None or identity["team"] == team or
                                (state is not None and not state.get("alive", True))):
                            continue
                    active_contributors.append("%s:%d" %
                                               (parts[0], identity_id))
                active_contributors = sorted(set(active_contributors))
                bases[str(team)] = {
                    "points": max(0, min(int(_finite_float(raw.get("points"), 0)), 100)),
                    "stopped": bool(raw.get("stopped", False)),
                    "contributors": contributors,
                    "active_contributors": active_contributors,
                    "invaders": len(active_contributors),
                    "cursor": max(0, min(int(_finite_float(raw.get("cursor"), 0)), 100000)),
                }
            self.rules_state = {"bases": bases}
            return True

    def _bot_defense_context(self):
        """Build planner input from authority capture state and baked bases."""
        base_points = self.bot_navigation.base_points
        if not base_points or len(base_points) < 2:
            return None
        bases = {}
        states = {}
        contributors = {}
        rules_bases = self.rules_state.get("bases", {})
        for team in (1, 2):
            point = base_points[team - 1]
            bases[str(team)] = [{
                "id": "%d:0" % team,
                "x": float(point[0]),
                "y": float(point[1]),
                "z": float(point[2]),
            }]
            raw = rules_bases.get(str(team), {})
            invaders = max(0, min(int(_finite_float(
                raw.get("invaders"), 0)), 3))
            points = max(0, min(int(_finite_float(
                raw.get("points"), 0)), 100))
            states[str(team)] = {
                "points": points,
                "stopped": bool(raw.get("stopped", False)),
                "invaders": invaders,
                "time_left": ((100.0 - points) / float(invaders)
                              if invaders else 0.0),
            }
            active = []
            for value in raw.get("active_contributors") or ():
                parts = str(value).split(":", 1)
                if len(parts) != 2 or parts[0] not in ("human", "bot"):
                    continue
                try:
                    identity_id = int(parts[1])
                except (TypeError, ValueError):
                    continue
                if identity_id > 0:
                    active.append({"kind": parts[0], "id": identity_id})
            contributors[str(team)] = active
        return {"bases": bases, "states": states,
                "contributors": contributors}

    def report_battle_result(self, player_id, message):
        with self.lock:
            if self.phase != "battle" or player_id != self.bot_authority_id or self.battle_result is not None:
                return False
            winner = max(0, min(int(_finite_float(message.get("winner"), 0)), 2))
            base_team = max(0, min(int(_finite_float(message.get("base_team"), 0)), 2))
            self.battle_result = {
                "winner": winner,
                "reason": _safe_name(message.get("reason"), "battle finished"),
                "base_team": base_team,
            }
            self.pending_events.append(dict(self.battle_result, kind="battle_result"))
            return True

    def update_input(self, player_id, message):
        with self.lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return
            if player.alive:
                if "forward" in message:
                    player.forward = _clamp(_finite_float(message.get("forward")), -1.0, 1.0)
                if "turn" in message:
                    player.turn = _clamp(_finite_float(message.get("turn")), -1.0, 1.0)
                if "aim_yaw" in message:
                    player.aim_yaw = _finite_float(message.get("aim_yaw"), player.aim_yaw)
                if "gun_pitch" in message:
                    player.gun_pitch = _clamp(_finite_float(message.get("gun_pitch")), -1.2, 1.2)
                if "x" in message and "z" in message:
                    player.x = _clamp(_finite_float(message.get("x"), player.x), -2000.0, 2000.0)
                    player.y = _clamp(_finite_float(message.get("y"), player.y), -1000.0, 1000.0)
                    player.z = _clamp(_finite_float(message.get("z"), player.z), -2000.0, 2000.0)
                    player.yaw = _finite_float(message.get("yaw"), player.yaw)
                    player.client_position = True
            try:
                fire_seq = int(message.get("fire_seq", player.fire_seq))
            except (TypeError, ValueError):
                fire_seq = player.fire_seq
            try:
                player.shell_index = max(0, min(int(message.get("shell_index", player.shell_index)), 9))
            except (TypeError, ValueError):
                pass
            if fire_seq > player.fire_seq and self.phase == "battle" and player.alive:
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
            if "reported_health" in message and self.phase == "battle":
                self._apply_reported_health(player, message.get("reported_health"))
            if not player.alive:
                # Late packets from the dead client's still-running input loop
                # must not drag its marker away from the server-owned wreck.
                player.forward = 0.0
                player.turn = 0.0

    def _apply_reported_health(self, player, reported_health):
        """Relay damage caused by local bots, fire, drowning or collisions.

        Human-versus-human damage uses report_hit() below.  The victim client
        remains authoritative for local simulation damage that the standalone
        server cannot reproduce without BigWorld collision and vehicle state.
        Health reports may only move downward during a round.
        """
        health = max(0, min(int(_finite_float(reported_health, player.health)), player.max_health))
        if health >= player.health:
            return
        damage = player.health - health
        player.health = health
        if health == 0:
            player.alive = False
        self.pending_events.append({
            "kind": "health",
            "target": player.player_id,
            "damage": damage,
            "health": player.health,
            "dead": not player.alive,
            "capture_reset": damage > 0,
            "source": "client_simulation",
        })

    def report_hit(self, player_id, message):
        """Apply a map/armor hit resolved by the firing 0.8.2 client.

        The server validates identity, team, range and one report per shot, then
        owns the shared HP result.  This reuses the existing client armor and
        shell collision logic instead of the old fixed 100-HP cone test.
        """
        with self.lock:
            attacker = self.players.get(player_id)
            # Do not cancel a legitimate in-flight round when its shooter dies.
            # update_input cannot advance fire_seq for a dead player, so the
            # existing sequence bound remains the anti-forgery gate.
            if self.phase != "battle" or attacker is None or not attacker.connected:
                return False
            try:
                shot_seq = int(message.get("shot_seq", 0))
                target_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return False
            hit_key = (shot_seq, target_id)
            if shot_seq <= 0 or shot_seq > attacker.fire_seq or hit_key in attacker.reported_hits:
                return False
            target = self.players.get(target_id)
            if target is None or not target.connected or not target.alive:
                return False
            if target.player_id == attacker.player_id or target.team == attacker.team:
                return False
            distance = math.hypot(target.x - attacker.x, target.z - attacker.z)
            if distance > 2200.0:
                return False
            attacker.reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            applied_damage = min(damage, target.health)
            target.health -= applied_damage
            if target.health == 0:
                target.alive = False
                target.killer_kind = "human"
                target.killer_id = attacker.player_id
            try:
                shot_result = max(0, min(int(message.get("shot_result", 2)), 2))
            except (TypeError, ValueError):
                shot_result = 2
            critical = bool(message.get("critical", False))
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
                "critical": critical,
                "capture_reset": bool(applied_damage > 0 or critical),
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            }
            self.pending_events.append(event)
            return True

    def _apply_movement(self, player, dt):
        if not player.alive:
            return
        if not player.client_position:
            player.yaw += player.turn * 0.85 * dt
            speed = 14.0 * player.forward
            player.x += math.sin(player.yaw) * speed * dt
            player.z += math.cos(player.yaw) * speed * dt
            player.x = _clamp(player.x, -220.0, 220.0)
            player.z = _clamp(player.z, -220.0, 220.0)

    def tick_once(self, dt):
        metrics = dict((name + "_seconds", 0.0) for name in _ServerPerfWindow.STAGES)
        metrics["messages"] = 0
        metrics["bytes"] = 0
        metrics["snapshot_messages"] = 0
        metrics["snapshot_base_bytes"] = 0
        metrics["snapshot_order_bytes"] = 0
        metrics["order_attachments"] = 0
        with self.lock:
            if self.phase != "battle":
                return
            self.tick += 1
            stage_started = time.perf_counter()
            for player in list(self.players.values()):
                self._apply_movement(player, dt)
            metrics["movement_seconds"] = time.perf_counter() - stage_started
            now = time.monotonic()
            stage_started = time.perf_counter()
            self.bot_orders = self.bot_planner.build_orders(
                self.bot_manifest, list(self.bot_states.values()),
                [self._public_player(p) for p in self.players.values() if p.connected],
                now, self._bot_defense_context())
            metrics["planner_seconds"] = time.perf_counter() - stage_started
            stage_started = time.perf_counter()
            self.bot_navigation_targets = self.bot_navigation.resolve(
                self.bot_orders.get("orders"), list(self.bot_states.values()),
                self.bot_orders.get("revision", 0), now)
            metrics["navigation_seconds"] = time.perf_counter() - stage_started
            stage_started = time.perf_counter()
            ai_debug = None
            if self.bot_manifest and now >= self.next_bot_ai_log:
                self.next_bot_ai_log = now + 3.0
                ai_debug = self.bot_planner.debug_summary(now)
                ai_debug["reported"] = dict(self.bot_observation_stats)
                ai_debug["navigation"] = dict(self.bot_navigation_stats)
                ai_debug["server_navigation"] = self.bot_navigation.diagnostics()
            metrics["diagnostics_seconds"] = time.perf_counter() - stage_started
            stage_started = time.perf_counter()
            events = self.pending_events
            self.pending_events = []
            snapshot_bots = []
            for key in sorted(self.bot_states):
                state = self.bot_states[key]
                navigation_target = self.bot_navigation_targets.get(key)
                if navigation_target:
                    state = dict(state)
                    state.update(navigation_target)
                snapshot_bots.append(state)
            snapshot = {
                "type": "snapshot",
                "protocol": PROTOCOL_VERSION,
                "round_id": self.round_id,
                "server_tick": self.tick,
                "map": self.map_name,
                "bot_authority_id": self.bot_authority_id,
                "players": [self._public_player(p) for p in self.players.values() if p.connected],
                "bots": snapshot_bots,
                "bot_state_revision": self.bot_state_revision,
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "timing": self._timing_payload(now),
            }
            authority_snapshot_bots = [
                _wire_bot_state(state, BOT_AUTHORITY_SNAPSHOT_FIELDS)
                for state in snapshot_bots
            ]
            replica_snapshot_bots = [
                _wire_bot_state(state, BOT_REPLICA_SNAPSHOT_FIELDS)
                for state in snapshot_bots
            ]
            recipients = list(self.players.values())
            authority = self.players.get(self.bot_authority_id)
            authority_order_ack = (
                int(authority.bot_order_revision_ack) if authority is not None else -1)
            dispatch_authority = authority
            dispatch_round_id = self.round_id
            dispatch_server_tick = self.tick
            (dispatch_order_revision, dispatch_order_body,
             dispatch_order_bytes) = self._wire_order_dispatch()
            metrics["snapshot_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        if ai_debug is not None:
            teams = ai_debug["teams"]
            reports = ai_debug["reported"]
            navigation = ai_debug["navigation"]
            server_navigation = ai_debug["server_navigation"]
            mode_counts = {}
            for team in (1, 2):
                for mode, count in teams[team]["modes"].items():
                    mode_counts[mode] = mode_counts.get(mode, 0) + count
            modes = ",".join("%s:%s" % item for item in sorted(mode_counts.items()))
            _server_log(
                "BOT AI reports=t1:%d,t2:%d accepted=%d "
                "contacts=t1:%d/%d,t2:%d/%d targets=t1:%d,t2:%d "
                "fire=t1:%d,t2:%d modes=%s "
                "nav=%s,cell:%dmm,nodes:%d "
                "nav_total=direct:%d,local:%d,reactive:%d recovered:%d "
                "nav_active=direct:%d,local:%d,reactive:%d "
                "astar=pending:%d,oldest:%dms,tick_age:%dms,done:%d,failed:%d "
                "orders=server:%d,client:%d,loaded:%d,acked:%d "
                "aim=targeted:%d,aligned:%d,traversing:%d,limited:%d,alive:%d "
                "driver=moving:%d,drive:%d,avoid:%d,blocked:%d,recovery:%d,arrived:%d,wait:%d,traffic:%d,full:%d,cruise:%d,speed:%d%%,slow:%d "
                "safety=water:%d/%d,edge:%d/%d,veto:w%d,t%d,o%d,e%d" % (
                    reports.get(1, 0), reports.get(2, 0), reports.get("accepted", 0),
                    teams[1]["visible"], teams[1]["contacts"],
                    teams[2]["visible"], teams[2]["contacts"],
                    teams[1]["targeted"], teams[2]["targeted"],
                    teams[1]["fire"], teams[2]["fire"], modes or "none",
                    navigation.get("graph", {}).get("source", "none"),
                    navigation.get("graph", {}).get("cell_mm", 0),
                    navigation.get("graph", {}).get("nodes", 0),
                    navigation["total"].get("safe_direct", 0),
                    navigation["total"].get("safe_local", 0),
                    navigation["total"].get("reactive", 0),
                    navigation.get("recovered", 0),
                    navigation["active"].get("safe_direct", 0),
                    navigation["active"].get("safe_local", 0),
                    navigation["active"].get("reactive", 0),
                    navigation.get("search", {}).get("pending", 0),
                    navigation.get("search", {}).get("oldest_ms", 0),
                    navigation.get("search", {}).get("tick_age_ms", 0),
                    navigation.get("search", {}).get("completed", 0),
                    navigation.get("search", {}).get("failed", 0),
                    self.bot_orders.get("revision", 0),
                    navigation.get("orders", {}).get("revision", 0),
                    navigation.get("orders", {}).get("loaded", 0),
                    authority_order_ack,
                    navigation.get("aim", {}).get("targeted", 0),
                    navigation.get("aim", {}).get("aligned", 0),
                    navigation.get("aim", {}).get("traversing", 0),
                    navigation.get("aim", {}).get("limited", 0),
                    navigation.get("aim", {}).get("alive", 0),
                    navigation.get("driver", {}).get("moving", 0),
                    navigation.get("driver", {}).get("drive", 0),
                    navigation.get("driver", {}).get("avoid", 0),
                    navigation.get("driver", {}).get("blocked", 0),
                    navigation.get("driver", {}).get("recovery", 0),
                    navigation.get("driver", {}).get("arrived", 0),
                    navigation.get("driver", {}).get("server_wait", 0),
                    navigation.get("driver", {}).get("traffic_wait", 0),
                    navigation.get("driver", {}).get("full", 0),
                    navigation.get("driver", {}).get("cruise", 0),
                    navigation.get("driver", {}).get("speed_pct", 0),
                    navigation.get("driver", {}).get("slow", 0),
                    navigation.get("safety", {}).get("water_guard_total", 0),
                    navigation.get("safety", {}).get("water_guard_active", 0),
                    navigation.get("safety", {}).get("edge_guard_total", 0),
                    navigation.get("safety", {}).get("edge_guard_active", 0),
                    navigation.get("safety", {}).get("veto_water", 0),
                    navigation.get("safety", {}).get("veto_terrain", 0),
                    navigation.get("safety", {}).get("veto_obstacle", 0),
                    navigation.get("safety", {}).get("veto_error", 0)))
            _server_log(
                "BOT NAV active=%s map=%s nodes=%d plans=%d direct=%d cache=%d "
                "complete=%d partial=%d pending=%d failed=%d budget=%.3f/%.3fms "
                "oldest=%.0fms avg=%.3fms max=%.3fms paths=%d" % (
                    server_navigation.get("active", False),
                    server_navigation.get("map", "none"),
                    server_navigation.get("nodes", 0),
                    server_navigation.get("plans", 0),
                    server_navigation.get("direct", 0),
                    server_navigation.get("cache_hits", 0),
                    server_navigation.get("completed", 0),
                    server_navigation.get("partials", 0),
                    server_navigation.get("pending", 0),
                    server_navigation.get("failures", 0),
                    server_navigation.get("budget_ms", 0.0),
                    server_navigation.get("max_budget_ms", 0.0),
                    server_navigation.get("oldest_ms", 0.0),
                    server_navigation.get("avg_plan_ms", 0.0),
                    server_navigation.get("max_plan_ms", 0.0),
                    server_navigation.get("paths", 0)))
        metrics["diagnostics_seconds"] += time.perf_counter() - stage_started
        # Reliable events are the causal acknowledgement for health and combat
        # changes already reflected in this tick's level-triggered snapshot. Queue
        # them first so a receive poll can never apply the snapshot delta before a
        # client-simulated health acknowledgement and subtract the same loss twice.
        stage_started = time.perf_counter()
        failed_recipients = []
        if events:
            for event in events:
                if event.get("kind") == "shot":
                    _server_log("SHOT attacker=%s seq=%s shell=%s" % (
                        event.get("attacker"), event.get("shot_seq"), event.get("shell_index")))
                elif event.get("kind") == "hit":
                    _server_log("HIT attacker=%s target=%s damage=%s health=%s dead=%s" % (
                        event.get("attacker"), event.get("target"), event.get("damage"),
                        event.get("health"), event.get("dead")))
                elif event.get("kind") == "health":
                    _server_log("HEALTH target=%s damage=%s health=%s dead=%s source=%s" % (
                        event.get("target"), event.get("damage"), event.get("health"),
                        event.get("dead"), event.get("source")))
                elif event.get("kind") == "bot_shot":
                    _server_log("BOT FIRE attacker=%s team=%s seq=%s shell=%s" % (
                        event.get("attacker_bot"), event.get("team"),
                        event.get("shot_seq"), event.get("shell_index")))
                elif event.get("kind") in ("bot_hit", "bot_human_hit"):
                    _server_log("BOT COMBAT kind=%s attacker=%s target=%s damage=%s health=%s dead=%s" % (
                        event.get("kind"), event.get("attacker", event.get("attacker_bot")),
                        event.get("target", event.get("target_bot")), event.get("damage"),
                        event.get("health"), event.get("dead")))
                elif event.get("kind") == "authority":
                    _server_log("BOT AUTHORITY player_id=%s" % event.get("player_id"))
                elif event.get("kind") == "battle_result":
                    _server_log("BATTLE RESULT winner=%s reason=%s base_team=%s" % (
                        event.get("winner"), event.get("reason"), event.get("base_team")))
            event_message = {
                "type": "events", "round_id": dispatch_round_id,
                "server_tick": dispatch_server_tick, "events": events,
            }
            for player in recipients:
                if not player.send(event_message, metrics):
                    failed_recipients.append(player)
        metrics["events_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        failed_objects = set(id(player) for player in failed_recipients)
        for player in recipients:
            if id(player) in failed_objects:
                continue
            outgoing = dict(snapshot)
            if player is dispatch_authority:
                if player.bot_snapshot_full_pending:
                    outgoing["bot_snapshot_mode"] = "full"
                else:
                    outgoing["bot_snapshot_mode"] = "authority"
                    outgoing["bots"] = authority_snapshot_bots
            else:
                outgoing["bot_snapshot_mode"] = "replica"
                outgoing["bots"] = replica_snapshot_bots
            revision = dispatch_order_revision
            needs_order_body = (
                player is dispatch_authority and
                player.bot_order_revision_ack != revision and
                (player.bot_order_revision_sent != revision or
                 now - player.bot_order_sent_at >= 0.25))
            if needs_order_body:
                outgoing = dict(outgoing)
                outgoing["bot_orders"] = dispatch_order_body
            if not player.send(
                    outgoing, metrics,
                    dispatch_order_bytes if needs_order_body else 0):
                failed_recipients.append(player)
        for player in failed_recipients:
            removed, reset = self.remove_player(
                player.player_id, expected_player=player)
            if removed is not None:
                _server_log(
                    "SEND DROP id=%d name=%s remaining=%d error=%s" % (
                        removed.player_id, removed.name, len(self.players),
                        removed.last_send_error or "unknown send failure"))
            if reset:
                _server_log("ROOM RESET round=%d map=%s after send failure" % (
                    self.round_id, self.map_name))
        if failed_recipients:
            self.broadcast_lobby()
        metrics["recipients_seconds"] = time.perf_counter() - stage_started
        return metrics

    @staticmethod
    def _public_player(player):
        return {
            "id": player.player_id,
            "name": player.name,
            "vehicle": player.vehicle,
            "team": player.team,
            "slot": player.slot,
            "world_pose": player.client_position,
            "spawn_x": BattleState._spawn_x_for(player.slot),
            "spawn_z": BattleState._spawn_z_for(player.team),
            "x": round(player.x, 4),
            "y": round(player.y, 4),
            "z": round(player.z, 4),
            "yaw": round(player.yaw, 5),
            "aim_yaw": round(player.aim_yaw, 5),
            "gun_pitch": round(player.gun_pitch, 5),
            "fire_seq": player.fire_seq,
            "shell_index": player.shell_index,
            "health": player.health,
            "max_health": player.max_health,
            "alive": player.alive,
            "killer_kind": player.killer_kind,
            "killer_id": player.killer_id,
        }

    def broadcast(self, message, perf=None):
        with self.lock:
            players = list(self.players.values())
        for player in players:
            if not player.send(message, perf):
                self.remove_player(player.player_id, expected_player=player)


class ClientHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server.game_server
        conn = self.request
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            # Bound kernel head-of-line backlog for reliable pong/combat messages.
            # During a client loading pause the sender may still block on one
            # in-flight write, while the application queue keeps only the newest
            # snapshot until the receive thread resumes.
            conn.setsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF, SERVER_SEND_BUFFER_BYTES)
        except OSError:
            pass
        try:
            actual_send_buffer = int(conn.getsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF))
        except (AttributeError, OSError, TypeError, ValueError):
            actual_send_buffer = None
        conn.settimeout(10.0)
        player = None
        buffer = b""
        _server_log(
            "TCP connection from %s:%d sndbuf=%s requested=%dB" % (
                self.client_address[0], self.client_address[1],
                "%dB" % actual_send_buffer if actual_send_buffer is not None else "unknown",
                SERVER_SEND_BUFFER_BYTES))
        try:
            while b"\n" not in buffer and len(buffer) < MAX_LINE_BYTES:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            line, _, buffer = buffer.partition(b"\n")
            hello = json.loads(line.decode("utf-8"))
            if (not isinstance(hello, dict) or hello.get("type") != "hello" or
                    int(hello.get("protocol", -1)) != PROTOCOL_VERSION):
                self._send_raw(conn, {"type": "error", "code": "protocol", "message": "protocol mismatch"})
                received_type = hello.get("type") if isinstance(hello, dict) else type(hello).__name__
                received_protocol = hello.get("protocol") if isinstance(hello, dict) else None
                _server_log(
                    "Rejected %s:%d: protocol mismatch type=%r protocol=%r expected=%d" % (
                        self.client_address[0], self.client_address[1], received_type,
                        received_protocol, PROTOCOL_VERSION))
                return
            received_build = str(hello.get("client_build", ""))
            if received_build != CLIENT_BUILD:
                self._send_raw(conn, {
                    "type": "error",
                    "code": "build",
                    "message": "client build mismatch; install %s on every PC" % CLIENT_BUILD,
                })
                _server_log(
                    "Rejected %s:%d: client build mismatch received=%r expected=%r" % (
                        self.client_address[0], self.client_address[1],
                        received_build, CLIENT_BUILD))
                return
            player, join_error, current_battle = server.state.add_player(
                conn, self.client_address, hello)
            if player is None:
                if join_error == "full":
                    message = "server is full"
                elif join_error == "battle_in_progress":
                    message = "battle already has a frozen bot lineup"
                else:
                    message = "connection bootstrap failed"
                self._send_raw(conn, {"type": "error", "code": join_error, "message": message})
                _server_log("Rejected %s:%d: %s" % (self.client_address[0], self.client_address[1], message))
                return
            _server_log("JOIN id=%d name=%s vehicle=%s max_hp=%d team=%d address=%s:%d phase=%s players=%d build=%s" % (
                player.player_id,
                player.name,
                player.vehicle,
                player.max_health,
                player.team,
                self.client_address[0],
                self.client_address[1],
                server.state.phase,
                len(server.state.players),
                received_build,
            ))
            if current_battle is not None:
                _server_log("LATE JOIN id=%d round=%d map=%s" % (
                    player.player_id,
                    current_battle["round_id"],
                    current_battle["map"],
                ))
            # This timeout applies to both recv and the dedicated sender thread.
            # Snapshot coalescing bounds application memory and avoids stale-state
            # buildup; the longer deadline only protects a healthy client during
            # its observed 4-6 second synchronous model-loading pause.
            conn.settimeout(SERVER_IO_TIMEOUT_SECONDS)
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    if player is not None and not player.connected:
                        break
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
                        return
                    message = json.loads(line.decode("utf-8"))
                    if not isinstance(message, dict):
                        continue
                    if not self._dispatch_player_message(server, player, message):
                        return
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            _server_log("Invalid message from %s:%d: %s" % (self.client_address[0], self.client_address[1], error))
        except (ConnectionError, OSError) as error:
            _server_log("Connection error from %s:%d: %s" % (self.client_address[0], self.client_address[1], error))
        finally:
            if player is not None:
                removed, reset = server.state.remove_player(
                    player.player_id, expected_player=player)
                if removed is not None:
                    _server_log("LEAVE id=%d name=%s remaining=%d" % (
                        removed.player_id, removed.name, len(server.state.players)))
                if reset:
                    _server_log("ROOM RESET round=%d map=%s" % (server.state.round_id, server.state.map_name))
                if removed is not None:
                    server.state.broadcast_lobby()
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _dispatch_player_message(server, player, message):
        """Dispatch only while this exact connection owns its numeric id."""
        state = server.state
        with state.lock:
            if state.players.get(player.player_id) is not player:
                return False
            message_type = message.get("type")
            if message_type == "input":
                state.update_input(player.player_id, message)
            elif message_type == "hit_report":
                if not state.report_hit(player.player_id, message):
                    _server_log("HIT REPORT rejected attacker=%d target=%s seq=%s" % (
                        player.player_id, message.get("target"), message.get("shot_seq")))
            elif message_type == "bot_manifest":
                accepted = state.update_bot_manifest(player.player_id, message)
                player.send({
                    "type": "bot_manifest_result",
                    "round_id": state.round_id,
                    "manifest_nonce": str(
                        message.get("manifest_nonce") or "")[:96],
                    "accepted": bool(accepted),
                    "bot_ids": (sorted(
                        int(entry["id"]) for entry in state.bot_manifest)
                        if accepted else []),
                    "bots": (list(state.bot_manifest) if accepted else []),
                    "code": "accepted" if accepted else "rejected",
                })
                if accepted:
                    routed = sum(1 for entry in state.bot_manifest
                                 if entry.get("route", {}).get("waypoints"))
                    lanes = {}
                    for entry in state.bot_manifest:
                        route_id = str(entry.get("route", {}).get("id") or "none")
                        key = "t%d:%s" % (int(entry.get("team", 0)), route_id)
                        lanes[key] = lanes.get(key, 0) + 1
                    lane_text = ",".join("%s=%d" % item
                                             for item in sorted(lanes.items()))
                    _server_log("BOT MANIFEST authority=%d bots=%d routes=%d lanes=%s" % (
                        player.player_id, len(state.bot_manifest), routed,
                        lane_text or "none"))
                else:
                    _server_log("BOT MANIFEST rejected sender=%d" % player.player_id)
            elif message_type == "bot_state":
                state.update_bot_states(player.player_id, message)
            elif message_type == "bot_observation":
                state.update_bot_observation(player.player_id, message)
            elif message_type == "bot_order_ack":
                state.acknowledge_bot_orders(player.player_id, message)
            elif message_type == "bot_order_resync":
                if state.request_bot_order_resync(player.player_id):
                    _server_log("BOT ORDERS RESYNC player_id=%d" % player.player_id)
            elif message_type == "bot_hit_report":
                if not state.report_bot_hit(player.player_id, message):
                    _server_log("BOT HIT rejected attacker=%d target=%s seq=%s" % (
                        player.player_id, message.get("target"), message.get("shot_seq")))
            elif message_type == "bot_human_hit":
                if not state.report_bot_human_hit(player.player_id, message):
                    _server_log("BOT HUMAN HIT rejected authority=%d target=%s" % (
                        player.player_id, message.get("target")))
            elif message_type == "rules_state":
                state.update_rules(player.player_id, message)
            elif message_type == "battle_result":
                if not state.report_battle_result(player.player_id, message):
                    _server_log("BATTLE RESULT rejected sender=%d" % player.player_id)
            elif message_type == "start_battle":
                start_message, start_error = state.request_start(
                    player.player_id, message.get("map"))
                if start_message is None:
                    player.send({
                        "type": "start_denied",
                        "code": start_error,
                        "players": len(state.players),
                    })
                    _server_log("START denied for id=%d: %s" % (
                        player.player_id, start_error))
                else:
                    _server_log("BATTLE START round=%d map=%s players=%d requested_by=%s countdown=%.0fs duration=%.0fs" % (
                        start_message["round_id"],
                        start_message["map"],
                        len(start_message["players"]),
                        player.name,
                        PREBATTLE_COUNTDOWN_SECONDS,
                        BATTLE_DURATION_SECONDS,
                    ))
                    state.broadcast(start_message)
            elif message_type == "ping":
                player.send({
                    "type": "pong",
                    "seq": message.get("seq"),
                    "client_time": message.get("client_time"),
                    "server_time": time.time(),
                })
            elif message_type == "leave":
                return False
            return True

    @staticmethod
    def _send_raw(conn, message):
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        conn.sendall(payload)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(host, port, map_name, max_players):
    state = BattleState(map_name=map_name, max_players=max_players)
    tcp_server = ThreadedTCPServer((host, port), ClientHandler)
    tcp_server.game_server = type("GameServer", (), {"state": state})()

    def tick_loop():
        interval = 1.0 / TICK_HZ
        next_tick = time.monotonic()
        next_error_log = 0.0
        perf_window = _ServerPerfWindow()
        perf_active = False
        while state.running:
            next_tick += interval
            tick_started = time.monotonic()
            process_started = time.process_time()
            metrics = None
            try:
                metrics = state.tick_once(min(interval, 0.1))
            except Exception:
                now = time.monotonic()
                if now >= next_error_log:
                    next_error_log = now + 2.0
                    _server_log("BATTLE TICK ERROR (server remains running):\n%s" % (
                        traceback.format_exc().rstrip()))
            tick_finished = time.monotonic()
            if metrics is None:
                if perf_active:
                    perf_window.reset(tick_finished, time.process_time())
                perf_active = False
            else:
                if not perf_active:
                    perf_window.reset(tick_started, process_started)
                    perf_active = True
                perf_window.add(
                    metrics, tick_finished - tick_started,
                    max(0.0, tick_finished - next_tick), interval)
                if perf_window.ready(tick_finished):
                    process_finished = time.process_time()
                    summary = perf_window.summary(tick_finished, process_finished)
                    stages = summary["stage_ms"]
                    with state.lock:
                        player_stats = [
                            player.outbound_diagnostics()
                            for player in state.players.values()
                        ]
                    pending_reliable = sum(
                        item["pending_reliable"] for item in player_stats)
                    pending_latest = sum(
                        item["pending_latest"] for item in player_stats)
                    coalesced = sum(item["coalesced"] for item in player_stats)
                    inflight = sum(
                        1 for item in player_stats if item["inflight_type"])
                    inflight_age_max = max(
                        [item["inflight_age_ms"] for item in player_stats] or [0.0])
                    send_max = max(
                        [item["send_max_ms"] for item in player_stats] or [0.0])
                    completed_messages = {}
                    completed_bytes = {}
                    for item in player_stats:
                        for message_type, count in item["completed_messages"].items():
                            completed_messages[message_type] = (
                                completed_messages.get(message_type, 0) + count)
                        for message_type, byte_count in item["completed_bytes"].items():
                            completed_bytes[message_type] = (
                                completed_bytes.get(message_type, 0) + byte_count)
                    actual_text = ",".join(
                        "%s:%.1f/s/%.1fKiB/s" % (
                            message_type,
                            completed_messages[message_type] /
                            summary["wall_seconds"],
                            completed_bytes.get(message_type, 0) /
                            summary["wall_seconds"] / 1024.0,
                        )
                        for message_type in sorted(completed_messages)
                    ) or "none"
                    _server_log(
                        "SERVER PERF cpu_core=%.1f%% tick=%.1fHz avg=%.2fms p95=%.2fms "
                        "max=%.2fms overruns=%d late_max=%.2fms "
                        "stage=move:%.2f,plan:%.2f,nav:%.2f,snapshot:%.2f,diag:%.2f,dispatch:%.2f,events:%.2fms "
                        "wire=encode:%.2f,socket:%.2fms messages=%.1f/s data=%.1fKiB/s "
                        "snapshot=base:%.0fB,orders:%.0fB,attach:%d/%d "
                        "outbound=reliable:%d,latest:%d,coalesced:%d,"
                        "inflight:%d,age_max:%.0fms,send_max:%.0fms sent=%s" % (
                            summary["cpu_percent"], summary["tick_hz"],
                            summary["tick_avg_ms"], summary["tick_p95_ms"],
                            summary["tick_max_ms"], summary["overruns"],
                            summary["late_max_ms"], stages["movement"],
                            stages["planner"], stages["navigation"],
                            stages["snapshot"], stages["diagnostics"],
                            stages["recipients"], stages["events"],
                            stages["encode"], stages["socket"],
                            summary["messages_per_second"],
                            summary["kilobytes_per_second"],
                            summary["snapshot_base_bytes"],
                            summary["snapshot_order_bytes"],
                            summary["order_attachments"],
                            summary["snapshot_messages"],
                            pending_reliable, pending_latest, coalesced,
                            inflight, inflight_age_max, send_max, actual_text))
                    perf_window.reset(tick_finished, process_finished)
            delay = next_tick - tick_finished
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()

    thread = threading.Thread(target=tick_loop, name="battle-tick", daemon=True)
    thread.start()
    _server_log("LAN battle server listening on %s:%d (map=%s, max_players=%d, protocol=%d, build=%s)" % (
        host, port, state.map_name, max_players, PROTOCOL_VERSION, CLIENT_BUILD))
    _server_log("Ready: clients click Battle! to join, choose a map, then click START BATTLE")
    try:
        tcp_server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping server", flush=True)
    finally:
        state.running = False
        tcp_server.shutdown()
        tcp_server.server_close()


def main():
    parser = argparse.ArgumentParser(description="LAN server for the offhangar network MVP")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=28782, help="TCP port (default: 28782)")
    parser.add_argument("--map", dest="map_name", default=DEFAULT_MAP, help="map name, or server_random (default: server chooses one)")
    parser.add_argument("--max-players", type=int, default=30, help="maximum connected clients")
    args = parser.parse_args()
    _ensure_windows_firewall_rule(args.port)
    run_server(args.host, args.port, args.map_name, args.max_players)


if __name__ == "__main__":
    main()
