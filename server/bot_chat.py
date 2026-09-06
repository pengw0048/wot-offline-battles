"""Decide which Bot answers a team-chat line, when, and with what text.

The stock #1513 client owns text entry, formatting, rendering, and sounds.
This module owns only the conversation: who is being addressed, whether anyone
answers at all, how many answer, whether one Bot answers another, and how fast
the team may talk.  It hands plain text back to the LAN server, which publishes
it through the same team-chat path a human line already uses.

The director is pure.  It never reads a clock, opens a socket, or touches
server state: the caller passes the current tick and one plain snapshot, and
the director returns the lines to publish.  One round therefore replays
identically in tests, which is what keeps the conversation rules provable
while the line text itself stays replaceable.

A line backend writes the text.  Without one -- which is what an install
with no optional model downloaded looks like -- the director schedules
nothing and every Bot stays quiet.  There is deliberately no second line
source: a canned stand-in would only make a broken model look healthy.
"""

import re
import unicodedata


# Conversation pacing, in seconds.  The director converts these with the
# caller's tick rate so the server stays the single owner of TICK_HZ.
#
# A teammate reads the channel, decides to answer, and types a line of
# Chinese while driving.  Ten seconds is ordinary, and an instant answer is
# the thing that reads as a machine.  The generous window is also what gives
# a small local model room to finish without anybody waiting on it.
REPLY_DELAY_MIN_SECONDS = 1.5
REPLY_DELAY_MAX_SECONDS = 9.0
HOP_DELAY_MIN_SECONDS = 2.0
HOP_DELAY_MAX_SECONDS = 11.0
THREAD_SILENCE_SECONDS = 20.0
BOT_COOLDOWN_SECONDS = 9.0

# A team may publish this many Bot lines inside one rolling window.  The
# budget, not a per-event speaker cap, is what keeps the channel readable:
# a squad call legitimately answers with more than one voice.
TEAM_BUDGET_LINES = 6
TEAM_BUDGET_SECONDS = 20.0

# A Bot answering another Bot is the most alive-feeling part of the feature
# and also the only way the conversation can fail to terminate.  Depth is
# capped and each further hop is less likely than the last.
MAX_CONVERSATION_HOPS = 2
HOP_PROBABILITY = (0.55, 0.3)

# A generating backend is asked when the line is scheduled and states how
# long it wants.  Ordinary reply pacing already covers a small local model,
# so this only matters for a slow one, and the cap bounds how long a hung
# generator can hold a scheduled line before it is abandoned.
MAX_PREFETCH_WAIT_SECONDS = 15.0

# Peng's product choice: most lines get an answer, and an interesting line may
# get more than one.  Silence stays legal, it is simply not the default.
ADDRESSED_REPLY_PROBABILITY = 0.97
OPEN_REPLY_PROBABILITY = 0.72
SECOND_SPEAKER_PROBABILITY = 0.35
SQUAD_MAX_SPEAKERS = 2
EVENT_REPLY_PROBABILITY = 0.4

RECENT_LINE_MEMORY = 12
MAX_CHAT_UTF16_UNITS = 140

PERSONA_TACTICAL = "tactical"
PERSONA_SLACKER = "slacker"
PERSONA_MECHANIC = "mechanic"
PERSONA_SCOUT = "scout"
PERSONA_POETIC = "poetic"
PERSONA_PLAIN = "plain"

PERSONAS = (PERSONA_TACTICAL, PERSONA_SLACKER, PERSONA_MECHANIC,
            PERSONA_SCOUT, PERSONA_POETIC, PERSONA_PLAIN)

# The shipped callsigns already carry personality.  Reading it out of the name
# costs nothing and keeps one Bot sounding like itself for the whole round.
# Order matters: the poetic keys are single characters that also appear inside
# tactical and scout names, so they are matched last.
_PERSONA_KEYWORDS = (
    (PERSONA_MECHANIC, (
        "履带", "修理", "维修", "保养", "螺丝", "弹药架", "炮塔", "装填",
        "充值", "车库", "备用", "刚修好")),
    (PERSONA_SCOUT, (
        "观察", "侦察", "探", "小地图", "亮", "眼睛", "草丛", "巡逻",
        "守门", "看一眼", "前方", "侦查", "麻雀", "海鸥", "信号")),
    (PERSONA_SLACKER, (
        "不加班", "喝茶", "别催", "别急", "随缘", "缩圈", "手感", "一般",
        "汽水", "半糖", "热茶", "常驻", "再来一局", "慢慢", "慢速", "慢行",
        "汤圆", "苏打", "气泡", "柠檬", "橘子")),
    (PERSONA_TACTICAL, (
        "猎手", "穿杨", "孤狼", "之刃", "彗星", "残云", "洪流", "闪电",
        "全开", "单骑", "纵横", "入魂", "幽灵", "亮剑", "破浪", "压路机",
        "头铁", "卖头", "赤色", "钢铁", "铁骑", "打两炮", "这炮能中")),
    (PERSONA_POETIC, (
        "风", "雨", "月", "星", "雪", "云", "雾", "山", "江", "海", "夜",
        "晨", "夏", "冬", "春", "秋", "长安", "南枝", "未央", "归舟",
        "远岚", "拾光", "知夏", "青禾", "白榆", "岭南", "塞北", "江南")),
)

# Chinese class words a player actually types, mapped to the #1513 class tags
# the server already stores on every catalogued vehicle.
CLASS_KEYWORDS = (
    ("lightTank", ("轻坦", "轻型", "快车", "小车", "light")),
    ("mediumTank", ("中坦", "中型", "medium")),
    ("heavyTank", ("重坦", "重型", "heavy")),
    ("SPG", ("火炮", "自行火炮", "自走炮", "spg", "arty")),
    ("AT-SPG", ("反坦克", "坦歼", "歼击", "td", "atspg")),
)

# ``fold_text`` has already removed the separators, and a word
# boundary does not hold between a digit and a CJK character, so the
# reference is bounded against Latin runs instead.
_CELL_PATTERN = re.compile(r"(?<![a-z0-9])([a-j])(10|[1-9])(?![0-9])")
_VEHICLE_PREFIX = re.compile(r"^[A-Za-z]{1,3}\d{1,3}_")
_FOLD_STRIP = re.compile(r"[\s\-_.·、,，。!！?？~～]+")

ADDRESS_NONE = "none"
ADDRESS_THREAD = "thread"
ADDRESS_CALLSIGN = "callsign"
ADDRESS_VEHICLE = "vehicle"
ADDRESS_CLASS = "class"
ADDRESS_CELL = "cell"

TRIGGER_REPLY = "reply"
TRIGGER_HOP = "hop"
TRIGGER_KILL = "kill"
TRIGGER_DOWN = "down"
TRIGGER_ALLY_DOWN = "ally_down"
TRIGGER_LOW_HEALTH = "low_health"


def fold_text(value):
    """Return one comparable form of player text or a vehicle token."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return _FOLD_STRIP.sub("", text).lower()


def vehicle_token(vehicle):
    """Return the model designation players actually say for one vehicle.

    ``ussr:R04_T-34`` becomes ``t34``: the nation prefix and the internal
    ``R04_`` index are not part of any name a player types.
    """
    text = str(vehicle or "")
    if ":" in text:
        text = text.split(":", 1)[1]
    return fold_text(_VEHICLE_PREFIX.sub("", text))


def _is_latin_alnum(character):
    """Return whether one character would extend a Latin model designation.

    CJK characters answer ``True`` to ``str.isalnum``, so a naive boundary
    test rejects the most common real phrasing: ``那个T-34``.  Only Latin
    letters and digits can actually continue a designation like ``T-34``.
    """
    return character.isascii() and character.isalnum()


def _token_present(token, folded):
    """Return whether a folded model token stands alone inside folded text."""
    if not token or len(token) < 2:
        return False
    start = 0
    while True:
        index = folded.find(token, start)
        if index < 0:
            return False
        after = index + len(token)
        before_ok = index == 0 or not _is_latin_alnum(folded[index - 1])
        after_ok = after >= len(folded) or not _is_latin_alnum(folded[after])
        if before_ok and after_ok:
            return True
        start = index + 1


def _callsign_aliases(name):
    """Return the short forms a player types instead of a full callsign."""
    text = str(name or "")
    aliases = set()
    if len(text) >= 2:
        aliases.add(text)
    if len(text) >= 4:
        aliases.add(text[-2:])
        aliases.add(text[:2])
        aliases.add(text[-3:])
    return aliases


def persona_for_callsign(name):
    """Return one stable persona for a shipped callsign."""
    text = str(name or "")
    for persona, keywords in _PERSONA_KEYWORDS:
        for keyword in keywords:
            if keyword in text:
                return persona
    if not text:
        return PERSONA_PLAIN
    # A name that matches nothing still needs a stable voice for the round.
    return PERSONAS[sum(ord(character) for character in text) % len(PERSONAS)]


def clamp_chat_text(value):
    """Return publishable stock text, or None when nothing survives.

    The server validates again before publication.  This keeps a backend from
    handing the transport a line that was only ever going to be rejected.
    """
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value)
    text = " ".join(text.split())
    if not text:
        return None
    try:
        utf16 = text.encode("utf-16-le")
        text.encode("utf-8")
    except UnicodeError:
        return None
    if len(utf16) // 2 > MAX_CHAT_UTF16_UNITS:
        text = utf16[:MAX_CHAT_UTF16_UNITS * 2].decode("utf-16-le", "ignore")
        text = text.strip()
    return text or None


class BotChatDirector(object):
    """Own the team-chat conversation for every Bot in one round."""

    def __init__(self, rng, tick_hz=30.0, backend=None):
        self._rng = rng
        self._tick_hz = float(tick_hz)
        self._backend = backend
        self._round_id = None
        self._personas = {}
        self._pending = []
        self._threads = {}
        self._recent = {}
        self._budget = {}
        self._bot_last_line = {}
        self._team_last_line = {}
        self._request_seq = 0

    # -- lifecycle ------------------------------------------------------

    def reset_round(self, round_id):
        """Fence every conversation at a round boundary."""
        self._round_id = round_id
        self._personas = {}
        self._pending = []
        self._threads = {}
        self._recent = {}
        self._budget = {}
        self._bot_last_line = {}
        self._team_last_line = {}
        self._request_seq = 0

    def set_backend(self, backend):
        """Swap the line source without disturbing an active conversation."""
        self._backend = backend

    def enabled(self):
        """Return whether any backend can write a line at all."""
        return self._backend is not None

    def _ticks(self, seconds):
        return max(1, int(round(float(seconds) * self._tick_hz)))

    def persona(self, bot_id, name):
        """Return this Bot's stable persona for the round."""
        bot_id = int(bot_id)
        persona = self._personas.get(bot_id)
        if persona is None:
            persona = persona_for_callsign(name)
            self._personas[bot_id] = persona
        return persona

    # -- addressing -----------------------------------------------------

    def resolve_address(self, text, team, snapshot):
        """Return the addressed Bots and how they were addressed.

        Every branch here is deterministic string and geometry work over the
        roster that is actually in this battle.  A model is not required to
        read a name, and in this release none is consulted: an unresolvable
        reference stays unaddressed rather than becoming a guess.
        """
        folded = fold_text(text)
        raw = unicodedata.normalize("NFKC", str(text or ""))
        bots = self._team_bots(team, snapshot)
        if not bots:
            return {"kind": ADDRESS_NONE, "bot_ids": []}

        named = [bot for bot in bots
                 if any(alias and alias in raw
                        for alias in _callsign_aliases(bot.get("name")))]
        if named:
            return {"kind": ADDRESS_CALLSIGN,
                    "bot_ids": [bot["id"] for bot in named[:SQUAD_MAX_SPEAKERS]]}

        matched = [bot for bot in bots
                   if _token_present(vehicle_token(bot.get("vehicle")), folded)]
        if matched:
            chosen = self._disambiguate(matched, snapshot)
            return {"kind": ADDRESS_VEHICLE, "bot_ids": chosen}

        for class_tag, keywords in CLASS_KEYWORDS:
            if not any(keyword in folded for keyword in keywords):
                continue
            squad = [bot for bot in bots
                     if str(bot.get("vehicle_class") or "") == class_tag]
            if squad:
                squad = self._by_relevance(squad, snapshot)
                return {"kind": ADDRESS_CLASS,
                        "bot_ids": [bot["id"]
                                    for bot in squad[:SQUAD_MAX_SPEAKERS]]}

        cell = _CELL_PATTERN.search(folded)
        if cell is not None:
            index = self._cell_index(cell.group(1), cell.group(2), snapshot)
            if index is not None:
                near = [bot for bot in bots
                        if self._bot_cell_index(bot, snapshot) == index]
                if near:
                    near = self._by_relevance(near, snapshot)
                    return {"kind": ADDRESS_CELL, "bot_ids": [near[0]["id"]]}

        thread = self._threads.get(team)
        if thread:
            # The player is still talking to whoever they addressed, even
            # after other Bots have chimed in on top of that answer.
            for candidate in ([thread.get("anchor")] +
                              list(reversed(thread.get("participants") or ()))):
                if candidate is None:
                    continue
                if any(bot["id"] == candidate for bot in bots):
                    return {"kind": ADDRESS_THREAD, "bot_ids": [candidate]}
        return {"kind": ADDRESS_NONE, "bot_ids": []}

    def _disambiguate(self, matched, snapshot):
        """Pick who ``那个 T-34`` meant when the team fields more than one."""
        if len(matched) == 1:
            return [matched[0]["id"]]
        speaker = snapshot.get("speaker") or {}
        spotted = set(speaker.get("spotted") or ())
        # "那个" implies the player can see it, and the server already knows
        # what this player has spotted.
        visible = [bot for bot in matched if bot["id"] in spotted]
        pool = visible or matched
        pool = self._by_relevance(pool, snapshot)
        return [pool[0]["id"]]

    def _by_relevance(self, bots, snapshot):
        speaker = snapshot.get("speaker") or {}
        spotted = set(speaker.get("spotted") or ())
        origin = (speaker.get("x"), speaker.get("z"))

        def key(bot):
            seen = 0 if bot["id"] in spotted else 1
            if origin[0] is None or bot.get("x") is None:
                distance = float("inf")
            else:
                distance = ((float(bot["x"]) - float(origin[0])) ** 2 +
                            (float(bot["z"]) - float(origin[1])) ** 2)
            return (seen, distance, bot["id"])

        return sorted(bots, key=key)

    @staticmethod
    def _cell_index(letter, number, snapshot):
        bounds = snapshot.get("arena_bounds")
        if not bounds:
            return None
        column = ord(letter) - ord("a")
        row = int(number) - 1
        if not 0 <= column <= 9 or not 0 <= row <= 9:
            return None
        return row * 10 + column

    @staticmethod
    def _bot_cell_index(bot, snapshot):
        bounds = snapshot.get("arena_bounds")
        if not bounds or bot.get("x") is None:
            return None
        min_x, min_z, max_x, max_z = bounds
        width = float(max_x) - float(min_x)
        height = float(max_z) - float(min_z)
        if width <= 0.0 or height <= 0.0:
            return None
        column = int((float(bot["x"]) - float(min_x)) / width * 10.0)
        row = int((float(max_z) - float(bot["z"])) / height * 10.0)
        if not 0 <= column <= 9 or not 0 <= row <= 9:
            return None
        return row * 10 + column

    @staticmethod
    def _team_bots(team, snapshot):
        result = []
        for bot in (snapshot.get("bots") or ()):
            if int(bot.get("team", 0)) == int(team) and bot.get("alive"):
                result.append(bot)
        return result

    # -- admission ------------------------------------------------------

    def observe_player_line(self, tick, team, text, snapshot):
        """Schedule the answers one human line earns."""
        team = int(team)
        if self._backend is None:
            return {"kind": ADDRESS_NONE, "bot_ids": []}
        address = self.resolve_address(text, team, snapshot)
        self._remember(team, snapshot.get("speaker", {}).get("name"), text)
        if address["kind"] in (ADDRESS_CALLSIGN, ADDRESS_VEHICLE,
                               ADDRESS_CLASS, ADDRESS_CELL):
            self._anchor_thread(team, address["bot_ids"][0], tick)
        elif address["kind"] == ADDRESS_THREAD:
            self._thread(team, tick)["last_tick"] = tick
        bots = self._team_bots(team, snapshot)
        if not bots:
            return address
        addressed = [bot_id for bot_id in address["bot_ids"]
                     if self._can_speak(tick, bot_id)]
        if addressed:
            for index, bot_id in enumerate(addressed):
                probability = (ADDRESSED_REPLY_PROBABILITY if index == 0
                               else SECOND_SPEAKER_PROBABILITY)
                if self._rng.random() <= probability:
                    self._schedule(tick, bot_id, team, TRIGGER_REPLY,
                                   address["kind"], snapshot)
            return address
        # Nobody was named.  Peng's choice is that an open remark usually
        # still gets an answer, and an interesting one may get two.
        if self._rng.random() > self._open_probability(text):
            return address
        pool = [bot for bot in self._by_relevance(bots, snapshot)
                if self._can_speak(tick, bot["id"])]
        if not pool:
            return address
        self._schedule(tick, pool[0]["id"], team, TRIGGER_REPLY,
                       ADDRESS_NONE, snapshot)
        if len(pool) > 1 and self._rng.random() < SECOND_SPEAKER_PROBABILITY:
            self._schedule(tick, pool[1]["id"], team, TRIGGER_REPLY,
                           ADDRESS_NONE, snapshot)
        return address

    @staticmethod
    def _open_probability(text):
        """Answer an interesting remark more often than a grunt."""
        raw = str(text or "")
        probability = OPEN_REPLY_PROBABILITY
        if any(mark in raw for mark in ("?", "？")):
            probability = min(0.95, probability + 0.2)
        if len(raw) >= 8:
            probability = min(0.95, probability + 0.1)
        elif len(raw) <= 2:
            probability = max(0.2, probability - 0.35)
        return probability

    def observe_event(self, tick, team, trigger, bot_id, snapshot):
        """Let a battle event start a line without any human speaking."""
        if self._backend is None or trigger not in (
                TRIGGER_KILL, TRIGGER_DOWN, TRIGGER_ALLY_DOWN,
                TRIGGER_LOW_HEALTH):
            return False
        team = int(team)
        if bot_id is None:
            return False
        bot_id = int(bot_id)
        # A destroyed Bot may still speak its own last line: ``_emit`` is what
        # decides whether the snapshot must still show it alive.
        if not self._can_speak(tick, bot_id):
            return False
        if self._rng.random() > EVENT_REPLY_PROBABILITY:
            return False
        self._schedule(tick, bot_id, team, trigger, ADDRESS_NONE, snapshot)
        return True

    def _build_request(self, bot, team, trigger, address_kind, snapshot):
        """Describe one line completely enough for any backend to write it."""
        self._request_seq += 1
        return {
            "request_id": self._request_seq,
            "trigger": trigger,
            "persona": self.persona(bot["id"], bot.get("name")),
            "address_kind": address_kind,
            "address_prefix": self._address_prefix(address_kind, snapshot),
            "speaker": dict(snapshot.get("speaker") or {}),
            "bot": bot,
            "recent_texts": [record["text"]
                             for record in self._recent.get(team, ())],
            "recent": list(self._recent.get(team, ())),
            "rng": self._rng,
        }

    def _schedule(self, tick, bot_id, team, trigger, address_kind, snapshot,
                  hop=0):
        bot = self._find_bot(bot_id, snapshot)
        if bot is None:
            return
        delay_min, delay_max = ((HOP_DELAY_MIN_SECONDS, HOP_DELAY_MAX_SECONDS)
                                if hop else
                                (REPLY_DELAY_MIN_SECONDS,
                                 REPLY_DELAY_MAX_SECONDS))
        delay = self._rng.uniform(delay_min, delay_max)
        request = self._build_request(bot, team, trigger, address_kind,
                                      snapshot)
        # Hand a generating backend the work now, and hold the line back long
        # enough for it to finish.  ``rng`` is deliberately not shared across
        # a thread boundary: a backend copies what it needs.
        prefetch = getattr(self._backend, "prefetch", None)
        if prefetch is not None:
            try:
                prefetch(request)
            except Exception:
                pass
            delay = max(delay, min(
                MAX_PREFETCH_WAIT_SECONDS,
                float(getattr(self._backend, "latency_hint_seconds", 0.0))))
        self._pending.append({
            "due_tick": int(tick) + self._ticks(delay),
            "bot_id": int(bot_id),
            "team": int(team),
            "trigger": trigger,
            "address_kind": address_kind,
            "hop": int(hop),
            "request": request,
        })
        # Reserve the slot now.  Without this, one burst of admissions would
        # schedule every Bot before any of them has spoken.
        self._bot_last_line[int(bot_id)] = int(tick)

    # -- publication ----------------------------------------------------

    def tick(self, tick, snapshot):
        """Return the Bot lines due at this tick."""
        tick = int(tick)
        if not self._pending:
            self._expire_threads(tick)
            return []
        due = [entry for entry in self._pending if entry["due_tick"] <= tick]
        if not due:
            self._expire_threads(tick)
            return []
        self._pending = [entry for entry in self._pending
                         if entry["due_tick"] > tick]
        published = []
        for entry in due:
            line = self._emit(tick, entry, snapshot)
            if line is not None:
                published.append(line)
        self._expire_threads(tick)
        return published

    def _emit(self, tick, entry, snapshot):
        team = entry["team"]
        bot = self._find_bot(entry["bot_id"], snapshot)
        if bot is None:
            return None
        if entry["trigger"] != TRIGGER_DOWN and not bot.get("alive"):
            return None
        if self._backend is None or not self._budget_available(tick, team):
            return None
        # The request was frozen when the line was scheduled so a generating
        # backend could start early.  Only the facts that move are refreshed.
        request = entry["request"]
        request["bot"] = bot
        request["recent_texts"] = [record["text"]
                                   for record in self._recent.get(team, ())]
        request["recent"] = list(self._recent.get(team, ()))
        try:
            text = self._backend.compose(request)
        except Exception:
            # A backend failure is contained to this one line.  It must never
            # silence the team for the rest of the round or fault the tick.
            return None
        text = clamp_chat_text(text)
        if text is None:
            return None
        if any(text == record["text"]
               for record in list(self._recent.get(team, ()))[-3:]):
            return None
        self._spend_budget(tick, team)
        self._remember(team, bot.get("name"), text)
        self._bot_last_line[bot["id"]] = tick
        self._team_last_line[team] = tick
        self._advance_thread(team, bot["id"], tick)
        self._maybe_hop(tick, entry, snapshot)
        return {"bot_id": bot["id"], "team": team, "text": text}

    def _address_prefix(self, address_kind, snapshot):
        """Name the human back only when they named a Bot first."""
        if address_kind not in (ADDRESS_CALLSIGN, ADDRESS_VEHICLE):
            return None
        speaker = snapshot.get("speaker") or {}
        name = speaker.get("name")
        return str(name) if name else None

    def _maybe_hop(self, tick, entry, snapshot):
        """Let one Bot answer another, with a hard depth cap."""
        hop = entry["hop"]
        if hop >= MAX_CONVERSATION_HOPS:
            return
        if self._rng.random() >= HOP_PROBABILITY[hop]:
            return
        team = entry["team"]
        candidates = [bot for bot in self._team_bots(team, snapshot)
                      if bot["id"] != entry["bot_id"] and
                      self._can_speak(tick, bot["id"])]
        if not candidates:
            return
        speaker = self._rng.choice(candidates)
        self._schedule(tick, speaker["id"], team, TRIGGER_HOP,
                       ADDRESS_NONE, snapshot, hop=hop + 1)

    # -- budgets and threads --------------------------------------------

    def _can_speak(self, tick, bot_id):
        """Return whether one Bot is off its personal cooldown."""
        last = self._bot_last_line.get(int(bot_id))
        return (last is None or
                tick - last >= self._ticks(BOT_COOLDOWN_SECONDS))

    def _budget_available(self, tick, team):
        """Return whether this team may still publish inside the window."""
        window = self._ticks(TEAM_BUDGET_SECONDS)
        spent = [stamp for stamp in self._budget.get(team, ())
                 if tick - stamp < window]
        self._budget[team] = spent
        return len(spent) < TEAM_BUDGET_LINES

    def _spend_budget(self, tick, team):
        """Charge one published line against the team's window."""
        self._budget.setdefault(team, []).append(tick)

    def _thread(self, team, tick):
        thread = self._threads.get(team)
        if thread is None:
            thread = {"anchor": None, "participants": [], "last_tick": tick}
            self._threads[team] = thread
        return thread

    def _anchor_thread(self, team, bot_id, tick):
        """Remember which teammate the player is actually talking to."""
        thread = self._thread(team, tick)
        thread["anchor"] = None if bot_id is None else int(bot_id)
        thread["last_tick"] = tick

    def _advance_thread(self, team, bot_id, tick):
        thread = self._thread(team, tick)
        participants = thread["participants"]
        if bot_id in participants:
            participants.remove(bot_id)
        participants.append(bot_id)
        del participants[:-SQUAD_MAX_SPEAKERS]
        thread["last_tick"] = tick

    def _expire_threads(self, tick):
        window = self._ticks(THREAD_SILENCE_SECONDS)
        for team in list(self._threads):
            if tick - self._threads[team]["last_tick"] >= window:
                del self._threads[team]

    def _remember(self, team, name, text):
        record = {"name": str(name or ""), "text": str(text or "")}
        history = self._recent.setdefault(int(team), [])
        history.append(record)
        del history[:-RECENT_LINE_MEMORY]

    def recent_lines(self, team):
        """Return the rolling transcript one team has heard."""
        return list(self._recent.get(int(team), ()))

    @staticmethod
    def _find_bot(bot_id, snapshot):
        for bot in (snapshot.get("bots") or ()):
            if int(bot.get("id", 0)) == int(bot_id):
                return bot
        return None
