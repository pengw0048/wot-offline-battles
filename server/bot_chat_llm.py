"""Write Bot chat lines with an optional local language model.

The model is a download a player chooses, never a requirement.  With no
model installed the whole feature is off and every Bot stays quiet.  With one
installed, the hard rule is that nothing here may block the caller: a slow,
unhealthy or absent generator costs one unsaid line, never a stalled battle.

The generator runs as a separate ``llama-server`` child process on loopback.
That keeps a native inference crash out of the LAN server, keeps the model's
memory out of the game process, and lets one worker thread bound how much CPU
the feature can take from a 32-bit game client and the hidden worker.

``BotChatDirector`` freezes a line's request the moment the line is scheduled
and hands it here immediately, so generation runs during the seconds a human
teammate would spend reading and typing.  A line whose generation has not
finished, failed, or produced nothing publishable is simply not said: there
is no canned stand-in, because one would only make a broken model look
healthy.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from bot_chat import (
    PERSONA_MECHANIC, PERSONA_PLAIN, PERSONA_POETIC, PERSONA_SCOUT,
    PERSONA_SLACKER, PERSONA_TACTICAL, TRIGGER_ALLY_DOWN, TRIGGER_DOWN,
    TRIGGER_HOP, TRIGGER_KILL, TRIGGER_LOW_HEALTH, TRIGGER_REPLY,
    clamp_chat_text,
)


READY_TIMEOUT_SECONDS = 90.0
REQUEST_TIMEOUT_SECONDS = 12.0
HEALTH_INTERVAL_SECONDS = 0.5
# One line is at most 140 UTF-16 units and the prompt asks for far less, so a
# short cap is a latency control rather than a quality limit.
MAX_TOKENS = 48
CONTEXT_TOKENS = 2048
TEMPERATURE = 0.9
TOP_P = 0.9
# Qwen closes a turn with ``<|im_end|>``.  A newline ends the line whatever
# the model intended: this channel carries exactly one sentence.
STOP_SEQUENCES = ("<|im_end|>", "<|endoftext|>", "\n")
PENDING_LIMIT = 24
RESULT_LIMIT = 48

PERSONA_STYLE = {
    PERSONA_TACTICAL: "干脆、果断，像个老车长，说话短促",
    PERSONA_SLACKER: "懒散、爱抱怨、有点摸鱼，语气松散",
    PERSONA_MECHANIC: "满脑子车况：装填、履带、弹药架",
    PERSONA_SCOUT: "谨慎、爱报点、先看再动",
    PERSONA_POETIC: "话少，偶尔带一点文气，但不掉书袋",
    PERSONA_PLAIN: "普通队友，平实直接",
}

TRIGGER_TASK = {
    TRIGGER_REPLY: "回应队伍频道里最后那句话",
    TRIGGER_HOP: "接住队友刚才那句话，不要重复他说过的内容",
    TRIGGER_KILL: "你刚刚打掉了一个敌人",
    TRIGGER_DOWN: "你刚刚被打死了",
    TRIGGER_ALLY_DOWN: "你的一个队友刚刚阵亡",
    TRIGGER_LOW_HEALTH: "你的血量已经很低了",
}


def free_loopback_port():
    """Reserve and release one loopback port for a child process to bind."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def inference_threads(cpu_count=None):
    """Return how many threads inference may take.

    The game client, the hidden worker and this server all want the same
    cores.  Leaving two behind keeps a reply from costing frames.
    """
    total = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
    return max(1, int(total) - 2)


def _no_window_flags():
    """Return creation flags that keep a console off the player's screen."""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LlamaServerSupervisor(object):
    """Own one hidden ``llama-server`` child process."""

    def __init__(self, executable, model_path, port=None, threads=None,
                 context_tokens=CONTEXT_TOKENS, log=None):
        self.executable = str(executable)
        self.model_path = str(model_path)
        self.port = int(port) if port else None
        self.threads = (int(threads) if threads else inference_threads())
        self.context_tokens = int(context_tokens)
        self._log = log or (lambda message: None)
        self._process = None
        self._ready = False

    @property
    def endpoint(self):
        if not self.port:
            return None
        return "http://127.0.0.1:%d" % self.port

    def available(self):
        """Return whether both halves of the optional download are present."""
        return (os.path.isfile(self.executable) and
                os.path.isfile(self.model_path))

    def start(self):
        """Launch the generator, or report why it cannot run."""
        if self._process is not None:
            return True
        if not self.available():
            self._log("BOT CHAT model or runtime is not installed")
            return False
        if not self.port:
            self.port = free_loopback_port()
        command = [
            self.executable,
            "-m", self.model_path,
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-c", str(self.context_tokens),
            "-t", str(self.threads),
        ]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_no_window_flags())
        except OSError as error:
            self._log("BOT CHAT runtime failed to start: %s" % (error,))
            self._process = None
            return False
        self._log("BOT CHAT runtime started on port %d with %d threads"
                  % (self.port, self.threads))
        return True

    def poll(self):
        """Return the child's exit code, or None while it is running."""
        return None if self._process is None else self._process.poll()

    def wait_ready(self, timeout=READY_TIMEOUT_SECONDS, clock=time.monotonic,
                   sleep=time.sleep):
        """Block until the generator answers, or give up.

        Only the caller that owns startup waits here.  A model load takes
        seconds, and the battle must never be one of the things waiting.
        """
        if self._ready:
            return True
        deadline = clock() + float(timeout)
        while clock() < deadline:
            if self._process is not None and self._process.poll() is not None:
                self._log("BOT CHAT runtime exited during startup")
                return False
            if self._health():
                self._ready = True
                return True
            sleep(HEALTH_INTERVAL_SECONDS)
        self._log("BOT CHAT runtime did not become ready")
        return False

    def is_ready(self):
        return self._ready

    def _health(self):
        endpoint = self.endpoint
        if endpoint is None:
            return False
        try:
            with urllib.request.urlopen(
                    endpoint + "/health", timeout=2.0) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            # An unready port refuses the connection rather than hanging.
            return False

    def stop(self):
        """Terminate the child so no orphan holds the model in memory."""
        process = self._process
        self._process = None
        self._ready = False
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        except OSError as error:
            self._log("BOT CHAT runtime failed to stop: %s" % (error,))


def build_messages(request):
    """Turn one frozen line request into a Qwen chat exchange."""
    bot = request.get("bot") or {}
    persona = request.get("persona") or PERSONA_PLAIN
    name = str(bot.get("name") or "队友")
    vehicle = str(bot.get("vehicle") or "")
    if ":" in vehicle:
        vehicle = vehicle.split(":", 1)[1]
    system = "\n".join((
        "你在玩《坦克世界》，是玩家的AI队友，正在队伍聊天频道里说话。",
        "你的ID是“%s”，开的车是 %s。" % (name, vehicle),
        "你的说话风格：%s。" % PERSONA_STYLE.get(persona, PERSONA_STYLE[
            PERSONA_PLAIN]),
        "规则：",
        "1. 只输出一句话，不超过25个字。",
        "2. 不要加引号，不要写自己的ID，不要解释，不要换行。",
        "3. 玩家说中文你就说中文，玩家说英文你就说英文。",
        "4. 像队友在打字，可以口语、可以不完整。",
    ))

    lines = []
    health = bot.get("hp")
    maximum = bot.get("max_hp")
    if health is not None and maximum:
        lines.append("你的血量：%s/%s。" % (health, maximum))
    transcript = [record for record in (request.get("recent") or ())
                  if record.get("text")]
    if transcript:
        lines.append("队伍频道最近的话：")
        for record in transcript[-6:]:
            speaker = record.get("name") or "队友"
            lines.append("%s：%s" % (speaker, record["text"]))
    task = TRIGGER_TASK.get(request.get("trigger"))
    if task:
        lines.append("现在轮到你说话：%s。" % task)
    prefix = request.get("address_prefix")
    if prefix:
        lines.append("刚才是 %s 在叫你。" % prefix)
    lines.append("只回一句话：")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines)},
    ]


class LlamaChatBackend(object):
    """Compose Bot lines against a local ``llama-server``, never blocking.

    ``prefetch`` starts one generation; ``compose`` collects whichever result
    is already finished.  A line whose generation is still running, failed, or
    produced nothing publishable simply has no result, and the caller falls
    back to the shipped templates.
    """

    def __init__(self, endpoint, latency_hint_seconds=3.0,
                 timeout=REQUEST_TIMEOUT_SECONDS, opener=None, log=None):
        self.endpoint = str(endpoint).rstrip("/")
        self.latency_hint_seconds = float(latency_hint_seconds)
        self._timeout = float(timeout)
        self._opener = opener or self._post
        self._log = log or (lambda message: None)
        self._lock = threading.Lock()
        self._results = {}
        self._order = []
        self._pending = set()
        self._thread = None
        self._queue = []
        self._wake = threading.Condition(self._lock)
        self._stopped = False

    # -- lifecycle ------------------------------------------------------

    def start(self):
        """Start the single generation worker."""
        with self._lock:
            if self._thread is not None:
                return
            self._stopped = False
            self._thread = threading.Thread(
                target=self._run, name="bot-chat-llm")
            self._thread.daemon = True
            self._thread.start()

    def stop(self):
        """Stop the worker without waiting on an in-flight request."""
        with self._lock:
            self._stopped = True
            self._queue = []
            self._wake.notify_all()
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)

    # -- backend contract -----------------------------------------------

    def prefetch(self, request):
        """Queue one line for generation."""
        request_id = request.get("request_id")
        if request_id is None:
            return
        try:
            messages = build_messages(request)
        except Exception as error:
            self._log("BOT CHAT prompt failed: %s" % (error,))
            return
        with self._lock:
            if self._stopped or request_id in self._pending:
                return
            if len(self._queue) >= PENDING_LIMIT:
                # The battle outran the generator.  Dropping the oldest keeps
                # the queue answering the current conversation rather than one
                # that has already moved on.
                dropped = self._queue.pop(0)
                self._pending.discard(dropped[0])
            self._queue.append((request_id, messages))
            self._pending.add(request_id)
            self._wake.notify()

    def compose(self, request):
        """Return this line's finished text, or None to fall back."""
        request_id = request.get("request_id")
        if request_id is None:
            return None
        with self._lock:
            return self._results.pop(request_id, None)

    # -- worker ---------------------------------------------------------

    def _run(self):
        while True:
            with self._lock:
                while not self._queue and not self._stopped:
                    self._wake.wait(0.5)
                if self._stopped:
                    return
                request_id, messages = self._queue.pop(0)
            text = None
            try:
                text = self._generate(messages)
            except Exception as error:
                self._log("BOT CHAT generation failed: %s" % (error,))
            with self._lock:
                self._pending.discard(request_id)
                if self._stopped:
                    return
                if text:
                    self._results[request_id] = text
                    self._order.append(request_id)
                    while len(self._order) > RESULT_LIMIT:
                        self._results.pop(self._order.pop(0), None)

    def _generate(self, messages):
        payload = {
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "stop": list(STOP_SEQUENCES),
            "stream": False,
        }
        body = self._opener(self.endpoint + "/v1/chat/completions", payload)
        return sanitize_line(extract_content(body))

    def _post(self, url, payload):
        data = json.dumps(payload).encode("utf-8")
        appeal = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(appeal, timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def extract_content(body):
    """Return the assistant text from one chat completion, or None."""
    if not isinstance(body, dict):
        return None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def sanitize_line(text):
    """Reduce model output to one publishable stock chat line, or None.

    The model is untrusted input like any other.  This strips the shapes a
    small instruct model actually produces -- a leading callsign, wrapping
    quotes, a reasoning block, several lines at once -- before the stock
    length and encoding rules get the final say.
    """
    if not isinstance(text, str):
        return None
    cleaned = text
    # Qwen2.5 has no thinking mode, but a mis-picked model would leak one.
    while "<think>" in cleaned and "</think>" in cleaned:
        head, rest = cleaned.split("<think>", 1)
        cleaned = head + rest.split("</think>", 1)[1]
    for marker in ("<|im_end|>", "<|endoftext|>", "<|im_start|>"):
        cleaned = cleaned.replace(marker, " ")
    # Only the first line is a chat message; the rest is the model continuing
    # a conversation with itself.
    cleaned = cleaned.replace("\r", "\n").split("\n")[0].strip()
    for quote in ('"', "'", "“", "”", "「", "」", "『", "』"):
        cleaned = cleaned.strip(quote)
    cleaned = cleaned.strip()
    # ``名字：内容`` is the single most common instruct-model slip here.
    for separator in ("：", ":"):
        head, sep, tail = cleaned.partition(separator)
        if sep and tail.strip() and len(head) <= 12 and "，" not in head:
            cleaned = tail.strip()
            break
    return clamp_chat_text(cleaned)
