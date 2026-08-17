"""Server-side path resolution over the shipped static navigation graphs.

The 0.8.2 client must keep BigWorld collision and presentation work on its
game thread.  Static A* does not need the engine, however, so the LAN server
can turn a tactical destination into a short, terrain-safe waypoint and send
that waypoint with every bot snapshot.  The authority client still performs
the final local corridor, destructible-object, water and tank checks.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import sys
import time


_ROOT = os.path.dirname(os.path.abspath(__file__))
_RELEASE_CLIENT_ROOT = os.path.join(_ROOT, "0.8.2")
if os.path.isdir(os.path.join(_RELEASE_CLIENT_ROOT, "scripts")):
    if _RELEASE_CLIENT_ROOT not in sys.path:
        sys.path.insert(0, _RELEASE_CLIENT_ROOT)

from scripts.client.gui.mods.offhangar.bot_ai_navigation import (  # noqa: E402
    BAKED_SHALLOW_WATER,
    TerrainGrid,
)


MAX_PATH_CACHE = 512
MAX_EXPANSIONS = 8192
REPLAN_INTERVAL_SECONDS = 0.35
GOAL_REUSE_DISTANCE = 7.5
PATH_DEVIATION_DISTANCE = 20.0
SEARCH_EXPANSIONS_PER_TICK = 2048
SEARCH_EXPANSIONS_PER_PATH = 256
SEARCH_TIME_BUDGET_SECONDS = 0.006
PATH_COMPLETE_CELLS = 3.25
GRAPH_FORMAT = "offhangar-navgraph"
GRAPH_VERSION = 1
GRAPH_GAME_VERSION = "0.8.2"
MANIFEST_FORMAT = GRAPH_FORMAT + "-manifest"


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) else float(default)


def _point(raw):
    raw = raw if isinstance(raw, dict) else {}
    return (_number(raw.get("x")), _number(raw.get("y")),
            _number(raw.get("z")))


def _distance(first, second):
    return math.hypot(float(first[0]) - float(second[0]),
                      float(first[2]) - float(second[2]))


def _graph_directories():
    return (
        os.path.join(_ROOT, "scripts", "client", "gui", "mods", "offhangar",
                     "navgraphs"),
        os.path.join(_RELEASE_CLIENT_ROOT, "scripts", "client", "gui", "mods",
                     "offhangar", "navgraphs"),
    )


def _sha256(path):
    """Hash JSON payloads without treating CRLF/LF as different content."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        digest.update(source.read().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _validate_graph(graph, map_name):
    if not isinstance(graph, dict):
        raise ValueError("navigation graph root is not an object")
    if graph.get("format") != GRAPH_FORMAT:
        raise ValueError("unsupported navigation graph format")
    if int(graph.get("version", -1)) != GRAPH_VERSION:
        raise ValueError("unsupported navigation graph version")
    if str(graph.get("game_version") or "") != GRAPH_GAME_VERSION:
        raise ValueError("navigation graph belongs to a different client version")
    if str(graph.get("map") or "") != str(map_name):
        raise ValueError("navigation graph map mismatch")
    width = int(graph.get("width", 0) or 0)
    height = int(graph.get("height", 0) or 0)
    count = width * height
    if width <= 0 or height <= 0:
        raise ValueError("navigation graph dimensions are invalid")
    if len(graph.get("heights_mm") or ()) != count:
        raise ValueError("navigation graph height array is incomplete")
    if len(graph.get("links") or ()) != count:
        raise ValueError("navigation graph link array is incomplete")
    hazards = graph.get("hazards")
    if hazards is not None and len(hazards) != count:
        raise ValueError("navigation graph hazard array is incomplete")
    if len(graph.get("origin") or ()) != 2:
        raise ValueError("navigation graph origin is invalid")
    if _number(graph.get("cell_size"), -1.0) <= 0.0:
        raise ValueError("navigation graph cell size is invalid")
    validation = graph.get("validation") or {}
    if not bool(validation.get("route_terminal_obb_clearance")):
        raise ValueError(
            "navigation graph lacks route terminal OBB clearance proof")
    return graph


def _manifest_record(directory, map_name):
    path = os.path.join(directory, "manifest.json")
    if not os.path.isfile(path):
        raise IOError("navigation graph manifest not found")
    with open(path, "r", encoding="utf-8") as source:
        manifest = json.load(source)
    if (not isinstance(manifest, dict) or
            manifest.get("format") != MANIFEST_FORMAT or
            int(manifest.get("version", -1)) != GRAPH_VERSION or
            str(manifest.get("game_version") or "") != GRAPH_GAME_VERSION):
        raise ValueError("navigation graph manifest is incompatible")
    records = manifest.get("maps") or ()
    selected = None
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("navigation graph manifest record is invalid")
        name = str(record.get("map") or "")
        filename = str(record.get("file") or "")
        digest = str(record.get("sha256") or "")
        if (not name or name in seen or filename != name + ".json" or
                len(digest) != 64):
            raise ValueError("navigation graph manifest record is invalid")
        seen.add(name)
        if not os.path.isfile(os.path.join(directory, filename)):
            raise ValueError("navigation graph batch is incomplete")
        if name == str(map_name):
            selected = record
    if selected is None:
        raise ValueError("navigation graph is absent from manifest")
    return selected


def _load_graph(map_name):
    filename = "%s.json" % str(map_name)
    for directory in _graph_directories():
        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            continue
        _manifest_record(directory, map_name)
        digest = _sha256(path)
        with open(path, "r", encoding="utf-8") as source:
            graph = json.load(source)
        return _validate_graph(graph, map_name), path, digest
    raise IOError("navigation graph not found for %s" % map_name)


def _frame_from_graph(graph):
    bases = graph.get("bases") or ()
    if len(bases) < 2 or len(bases[0]) < 2 or len(bases[1]) < 2:
        raise ValueError("navigation graph has no team-base frame")
    first = bases[0]
    second = bases[1]
    dx = _number(second[0]) - _number(first[0])
    dz = _number(second[1]) - _number(first[1])
    length = math.hypot(dx, dz)
    if length < 1.0:
        raise ValueError("navigation graph has a degenerate team-base frame")
    axis_x, axis_z = dx / length, dz / length
    return (_number(first[0]), _number(first[1]), axis_x, axis_z,
            axis_z, -axis_x)


def _frame_from_message(raw):
    if not isinstance(raw, dict):
        raise ValueError("authority map frame is missing")
    origin = raw.get("origin") or ()
    axis = raw.get("axis") or ()
    if len(origin) != 2 or len(axis) != 2:
        raise ValueError("authority map frame is invalid")
    try:
        origin_x = float(origin[0])
        origin_z = float(origin[1])
        axis_x = float(axis[0])
        axis_z = float(axis[1])
    except (TypeError, ValueError):
        raise ValueError("authority map frame is invalid")
    if not all(math.isfinite(value) for value in
               (origin_x, origin_z, axis_x, axis_z)):
        raise ValueError("authority map frame contains a non-finite value")
    if abs(origin_x) > 2000.0 or abs(origin_z) > 2000.0:
        raise ValueError("authority map frame origin is out of range")
    length = math.hypot(axis_x, axis_z)
    if length < 0.5:
        raise ValueError("authority map frame axis is degenerate")
    axis_x, axis_z = axis_x / length, axis_z / length
    return (origin_x, origin_z, axis_x, axis_z, axis_z, -axis_x)


class BotPathResolver(object):
    """Resolve strategic server orders into short baked-graph waypoints."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.map_name = None
        self.graph_path = None
        self.graph_sha256 = None
        self.context_id = None
        self.grid = None
        self.frame = None
        self.base_points = None
        self._frame_signature = None
        self._bot_paths = {}
        self._path_cache = {}
        self._path_times = {}
        self._path_complete = {}
        self._searches = {}
        self._search_times = {}
        self._search_next_key = None
        self._search_tick_time = None
        self._revision = 0
        self._plans = 0
        self._cache_hits = 0
        self._failures = 0
        self._direct = 0
        self._completed = 0
        self._partials = 0
        self._search_steps = 0
        self._search_budget_ms = 0.0
        self._search_budget_ms_max = 0.0
        self._plan_ms_total = 0.0
        self._plan_ms_max = 0.0

    @staticmethod
    def sanitize_frame(raw):
        """Return a small JSON-safe frame payload or ``None``."""
        if not isinstance(raw, dict):
            return None
        origin = raw.get("origin") or ()
        axis = raw.get("axis") or ()
        if len(origin) != 2 or len(axis) != 2:
            return None
        try:
            origin_x = float(origin[0])
            origin_z = float(origin[1])
            axis_x = float(axis[0])
            axis_z = float(axis[1])
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in
                   (origin_x, origin_z, axis_x, axis_z)):
            return None
        if abs(origin_x) > 2000.0 or abs(origin_z) > 2000.0:
            return None
        length = math.hypot(axis_x, axis_z)
        if length < 0.5:
            return None
        return {
            "origin": [round(origin_x, 4), round(origin_z, 4)],
            "axis": [round(axis_x / length, 7), round(axis_z / length, 7)],
        }

    def configure(self, map_name, raw_frame=None):
        graph, path, digest = _load_graph(map_name)
        sanitized = self.sanitize_frame(raw_frame)
        frame = _frame_from_message(sanitized)
        signature = tuple(round(value, 6) for value in frame)
        if (self.grid is not None and self.map_name == str(map_name) and
                self._frame_signature == signature and
                self.graph_sha256 == digest):
            return False
        started = time.perf_counter()
        grid = TerrainGrid(lambda unused_x, unused_z, unused_y: None,
                           baked_graph=graph)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.map_name = str(map_name)
        self.graph_path = path
        self.graph_sha256 = digest
        self.grid = grid
        self.frame = frame
        base_points = []
        for value in (graph.get("bases") or ())[:2]:
            world = (float(value[0]), 0.0, float(value[1]))
            height = grid._baked_cell_height(grid.cell_for(world))
            base_points.append(self._shared(
                (world[0], float(height if height is not None else 0.0),
                 world[2])))
        self.base_points = tuple(base_points)
        self._frame_signature = signature
        context = "%s|%s|%s" % (
            self.map_name, digest, ",".join("%.6f" % value for value in signature))
        self.context_id = hashlib.sha256(context.encode("ascii")).hexdigest()[:16]
        self._bot_paths = {}
        self._path_cache = {}
        self._path_times = {}
        self._path_complete = {}
        self._searches = {}
        self._search_times = {}
        self._search_next_key = None
        self._search_tick_time = None
        self._revision = 0
        self._plans = 0
        self._cache_hits = 0
        self._failures = 0
        self._direct = 0
        self._completed = 0
        self._partials = 0
        self._search_steps = 0
        self._search_budget_ms = 0.0
        self._search_budget_ms_max = 0.0
        self._plan_ms_total = 0.0
        self._plan_ms_max = 0.0
        self.install_ms = elapsed_ms
        return True

    @property
    def active(self):
        return self.grid is not None and self.frame is not None

    def _world(self, shared):
        origin_x, origin_z, axis_x, axis_z, right_x, right_z = self.frame
        lateral, y, travel = shared
        return (origin_x + axis_x * travel + right_x * lateral,
                float(y),
                origin_z + axis_z * travel + right_z * lateral)

    def _shared(self, world):
        origin_x, origin_z, axis_x, axis_z, right_x, right_z = self.frame
        dx = float(world[0]) - origin_x
        dz = float(world[2]) - origin_z
        return (dx * right_x + dz * right_z,
                float(world[1]),
                dx * axis_x + dz * axis_z)

    def _path_is_complete(self, path, goal):
        return bool(path and _distance(path[-1], goal) <=
                    self.grid.cell_size * PATH_COMPLETE_CELLS)

    def _trim_cache(self):
        if len(self._path_cache) <= MAX_PATH_CACHE:
            return
        stale = sorted(self._path_times.items(), key=lambda item: item[1])
        for old_key, unused_time in stale[:len(self._path_cache) - MAX_PATH_CACHE]:
            self._path_cache.pop(old_key, None)
            self._path_times.pop(old_key, None)
            self._path_complete.pop(old_key, None)

    def _store_path(self, key, path, goal, now, elapsed_ms):
        path = tuple(path or ())
        complete = self._path_is_complete(path, goal)
        if not path:
            self._failures += 1
        elif complete:
            self._completed += 1
        else:
            self._partials += 1
        self._path_cache[key] = path
        self._path_times[key] = float(now)
        self._path_complete[key] = complete
        self._plan_ms_total += float(elapsed_ms)
        self._plan_ms_max = max(self._plan_ms_max, float(elapsed_ms))
        self._trim_cache()
        return path, complete

    def _finish_search(self, key, job, now):
        self._searches.pop(key, None)
        self._search_times.pop(key, None)
        return self._store_path(
            key, job["search"].result, job["goal"], now, job["cpu_ms"])

    def _advance_searches(self, now):
        """Advance every cold A* fairly within one bounded server-tick slice."""
        if (self._search_tick_time is not None and
                abs(float(now) - float(self._search_tick_time)) < 0.000001):
            return
        self._search_tick_time = float(now)
        keys = sorted(self._searches, key=lambda value: repr(value))
        if not keys:
            self._search_next_key = None
            self._search_budget_ms = 0.0
            return
        if self._search_next_key in keys:
            start_index = keys.index(self._search_next_key)
            queue = keys[start_index:] + keys[:start_index]
        else:
            queue = keys
        per_path = {}
        budget = max(1, int(SEARCH_EXPANSIONS_PER_TICK))
        minimum_round = min(len(queue), budget)
        processed = 0
        started = time.perf_counter()
        while queue and budget > 0:
            key = queue.pop(0)
            job = self._searches.get(key)
            if job is None:
                continue
            step_started = time.perf_counter()
            job["search"].step(1)
            job["cpu_ms"] += (time.perf_counter() - step_started) * 1000.0
            processed += 1
            budget -= 1
            self._search_steps += 1
            per_path[key] = per_path.get(key, 0) + 1
            if job["search"].done:
                self._finish_search(key, job, now)
            elif per_path[key] < SEARCH_EXPANSIONS_PER_PATH:
                queue.append(key)
            if (processed >= minimum_round and
                    time.perf_counter() - started >= SEARCH_TIME_BUDGET_SECONDS):
                break
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._search_budget_ms = elapsed_ms
        self._search_budget_ms_max = max(self._search_budget_ms_max, elapsed_ms)
        self._search_next_key = queue[0] if queue else None

    def _request_path(self, key, start, goal, now):
        """Return ``(status, path, complete)`` without blocking on cold A*."""
        cached = self._path_cache.get(key)
        if cached is not None:
            # A failed lookup is a short negative cache, not a permanent route
            # verdict.  The authority client may have moved the bot back onto a
            # supported cell since the previous snapshot.
            if cached:
                self._cache_hits += 1
                self._path_times[key] = float(now)
                return "ready", cached, bool(
                    self._path_complete.get(key, False))
            if (float(now) - float(self._path_times.get(key, 0.0)) <
                    REPLAN_INTERVAL_SECONDS):
                # Do not renew a negative cache hit. A bot that asks on every
                # 30 Hz tick must still get a fresh search opportunity after
                # the short retry interval expires.
                self._cache_hits += 1
                return "failed", cached, False
            self._path_cache.pop(key, None)
            self._path_times.pop(key, None)
            self._path_complete.pop(key, None)
        if key in self._searches:
            return "pending", None, False
        started = time.perf_counter()
        if (self.grid.segment_clear(start, goal) and
                not self.grid.segment_has_baked_hazard(
                    start, goal, BAKED_SHALLOW_WATER)):
            path = (tuple(start), tuple(goal))
            self._direct += 1
            self._plans += 1
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            path, complete = self._store_path(
                key, path, goal, now, elapsed_ms)
            return "ready", path, complete
        else:
            search = self.grid.begin_plan(
                start, goal, max_expansions=MAX_EXPANSIONS, now=now)
            self._searches[key] = {
                "search": search,
                "goal": tuple(goal),
                "cpu_ms": (time.perf_counter() - started) * 1000.0,
            }
            self._search_times[key] = float(now)
        self._plans += 1
        return "pending", None, False

    def _path_identity(self, order, goal):
        mode = str(order.get("combat_mode") or "route")
        route_mode = mode in ("route", "advance") and order.get("target_id") is None
        if mode == "base_defense":
            prefix = ("bot", int(order.get("id", 0) or 0), mode,
                      str(order.get("defense_base_id") or "own_base"))
        elif route_mode:
            prefix = ("route", int(order.get("team", 0) or 0),
                      str(order.get("route_id") or "direct"),
                      int(order.get("route_index", 0) or 0))
        else:
            prefix = ("bot", int(order.get("id", 0) or 0), mode,
                      str(order.get("target_kind") or ""),
                      int(order.get("target_id", 0) or 0))
        # The bot's current cell is deliberately not part of the tactical
        # identity.  Otherwise a moving bot would discard its path and run A*
        # again every time it crossed a grid boundary.  The starting cell still
        # belongs in the shared path-cache key below.
        return prefix + (self.grid.cell_for(goal),)

    def _path_cache_key(self, identity, start):
        return ("path",) + tuple(identity) + (self.grid.cell_for(start),)

    def _activate_bot_path(self, bot_id, identity, path, goal, complete,
                           current, now):
        closest = min(range(len(path)), key=lambda index:
                      _distance(current, path[index]))
        self._revision += 1
        bot_path = {
            "identity": identity,
            "path": path,
            "index": closest,
            "goal": goal,
            "complete": bool(complete),
            "planned_at": float(now),
            "revision": self._revision,
        }
        self._bot_paths[bot_id] = bot_path
        return bot_path

    def _resolve_one(self, order, state, order_revision, now):
        bot_id = int(order.get("id", 0) or 0)
        current_shared = _point(state)
        raw_goal = order.get("move_position")
        if not isinstance(raw_goal, dict):
            return None, "client_fallback"
        goal_shared = _point(raw_goal)
        current = self._world(current_shared)
        goal = self._world(goal_shared)
        distance = _distance(current, goal)
        mode = str(order.get("combat_mode") or "route")
        if (order.get("throttle_override") == 0.0 or
                mode in ("server_wait", "engage", "cover_hold",
                         "artillery_hold", "artillery_fire")):
            return goal_shared, "server_hold"
        if (distance <= 15.0 and self.grid.segment_clear(current, goal) and
                not self.grid.segment_has_baked_hazard(
                    current, goal, BAKED_SHALLOW_WATER)):
            return goal_shared, "server_hold"

        route_mode = mode in ("route", "advance") and order.get("target_id") is None
        anchor = order.get("route_anchor") if route_mode else None
        start = self._world(_point(anchor)) if isinstance(anchor, dict) else current
        identity = self._path_identity(order, goal)
        bot_path = self._bot_paths.get(bot_id)
        replan = bot_path is None or bot_path.get("identity") != identity
        if not replan and bot_path:
            old_goal = bot_path.get("goal", goal)
            if (_distance(old_goal, goal) > GOAL_REUSE_DISTANCE and
                    float(now) - float(bot_path.get("planned_at", 0.0)) >=
                    REPLAN_INTERVAL_SECONDS):
                replan = True
        if replan:
            status, path, complete = self._request_path(
                self._path_cache_key(identity, start), start, goal, now)
            if status == "failed" and _distance(start, current) > 1.0:
                # Strategic route anchors are sparse hand-authored points.  If
                # one lies just outside the baked support, join from the bot's
                # authoritative current pose before falling back to client A*.
                status, path, complete = self._request_path(
                    ("anchor_join", bot_id, self.grid.cell_for(current),
                     self.grid.cell_for(goal), mode),
                    current, goal, now)
            if status == "pending":
                return current_shared, "server_hold"
            if status == "failed" or not path:
                self._bot_paths.pop(bot_id, None)
                return None, "client_fallback"
            bot_path = self._activate_bot_path(
                bot_id, identity, path, goal, complete, current, now)
        path = bot_path["path"]
        closest = min(range(len(path)), key=lambda index:
                      _distance(current, path[index]))
        if (_distance(current, path[closest]) > PATH_DEVIATION_DISTANCE and
                float(now) - float(bot_path.get("planned_at", 0.0)) >=
                REPLAN_INTERVAL_SECONDS):
            join_key = ("join", bot_id, self.grid.cell_for(current),
                        self.grid.cell_for(goal), mode)
            status, path, complete = self._request_path(
                join_key, current, goal, now)
            if status == "pending":
                return current_shared, "server_hold"
            if status == "failed" or not path:
                self._bot_paths.pop(bot_id, None)
                return None, "client_fallback"
            bot_path = self._activate_bot_path(
                bot_id, identity, path, goal, complete, current, now)
            closest = int(bot_path["index"])
        reach = min(10.0, max(2.5, self.grid.cell_size * 0.65))
        if (not bool(bot_path.get("complete", False)) and
                _distance(current, path[-1]) < reach):
            # A bounded A* result is safe progress, not proof that the strategic
            # destination was reached. Continue from the realised hull pose as soon
            # as the bot consumes the partial path.
            continue_key = (
                "continue", bot_id, self.grid.cell_for(current),
                self.grid.cell_for(goal), mode)
            status, path, complete = self._request_path(
                continue_key, current, goal, now)
            if status == "pending":
                return current_shared, "server_hold"
            if (status == "failed" or not path or
                    (not complete and _distance(current, path[-1]) < 1.0)):
                self._bot_paths.pop(bot_id, None)
                return None, "client_fallback"
            bot_path = self._activate_bot_path(
                bot_id, identity, path, goal, complete, current, now)
            closest = int(bot_path["index"])
        index = max(int(bot_path.get("index", 0)), closest)
        while index + 1 < len(path) and _distance(current, path[index]) < reach:
            index += 1
        lookahead = index
        for candidate in range(index + 1, min(len(path), index + 3)):
            if self.grid.segment_clear(current, path[candidate]):
                lookahead = candidate
            else:
                break
        bot_path["index"] = lookahead
        return self._shared(path[lookahead]), "server_baked"

    def resolve(self, orders, bot_states, order_revision, now):
        """Return snapshot navigation fields keyed by stable bot id."""
        states = {}
        for raw in bot_states or ():
            try:
                states[int(raw.get("id"))] = raw
            except (AttributeError, TypeError, ValueError):
                continue
        result = {}
        live_ids = set()
        if not self.active:
            for bot_id, state in states.items():
                if bool(state.get("alive", True)):
                    result[bot_id] = {
                        "nav_source": "client_fallback",
                        "nav_order_revision": int(order_revision),
                    }
            return result
        # Search work is server-side and bounded per 30 Hz tick. Cold routes are
        # queued by one resolve call and make fair resumable progress from the next;
        # clients receive a safe hold instead of running the same A* in render time.
        self._advance_searches(float(now))
        for order in orders or ():
            try:
                bot_id = int(order.get("id"))
            except (AttributeError, TypeError, ValueError):
                continue
            state = states.get(bot_id)
            if state is None or not bool(state.get("alive", True)):
                continue
            live_ids.add(bot_id)
            target, source = self._resolve_one(
                order, state, int(order_revision), float(now))
            payload = {
                "nav_source": source,
                "nav_order_revision": int(order_revision),
                "nav_context_id": self.context_id,
            }
            if target is not None:
                payload.update({
                    "nav_x": round(float(target[0]), 4),
                    "nav_y": round(float(target[1]), 4),
                    "nav_z": round(float(target[2]), 4),
                })
            result[bot_id] = payload
        # Always make fallback explicit.  Otherwise a missing order leaves the
        # client using the previous server waypoint until its freshness timer
        # happens to expire.
        for bot_id, state in states.items():
            if bool(state.get("alive", True)) and bot_id not in result:
                live_ids.add(bot_id)
                result[bot_id] = {
                    "nav_source": "client_fallback",
                    "nav_order_revision": int(order_revision),
                    "nav_context_id": self.context_id,
                }
        for bot_id in list(self._bot_paths):
            if bot_id not in live_ids:
                self._bot_paths.pop(bot_id, None)
        return result

    def diagnostics(self):
        nodes = 0
        if self.grid is not None:
            nodes = sum(1 for value in self.grid._baked_heights
                        if value is not None)
        return {
            "active": self.active,
            "map": self.map_name or "none",
            "context": self.context_id or "none",
            "sha256": self.graph_sha256 or "none",
            "nodes": nodes,
            "install_ms": round(_number(getattr(self, "install_ms", 0.0)), 2),
            "plans": self._plans,
            "direct": self._direct,
            "cache_hits": self._cache_hits,
            "failures": self._failures,
            "completed": self._completed,
            "partials": self._partials,
            "pending": len(self._searches),
            "search_steps": self._search_steps,
            "budget_ms": round(self._search_budget_ms, 3),
            "max_budget_ms": round(self._search_budget_ms_max, 3),
            "oldest_ms": round(max(
                (float(self._search_tick_time or 0.0) - value) * 1000.0
                for value in self._search_times.values()), 1)
                if self._search_times else 0.0,
            "avg_plan_ms": round(
                self._plan_ms_total / max(1, self._plans), 3),
            "max_plan_ms": round(self._plan_ms_max, 3),
            "paths": len(self._path_cache),
        }
