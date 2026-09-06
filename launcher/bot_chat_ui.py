"""Tk panel for installing and enabling the optional Bot chat model.

The panel owns one long download and must stay honest about it: the work
runs on a worker thread, progress is reported continuously, and Stop is
answered within one chunk while keeping the partial file for a resume.

Every Tk module is injected so the panel can be driven headlessly by the
tests, and every catalogue fact comes from the server's pinned module rather
than being restated here.
"""

from __future__ import annotations

import os
import threading

try:
    from . import bot_chat_install
except ImportError:
    import bot_chat_install


ENABLED_SETTING = "bot_chat_enabled"
TIER_SETTING = "bot_chat_model"
RUNTIME_OVERRIDE_SETTING = "bot_chat_runtime_override"

STATE_ABSENT = "absent"
STATE_READY = "ready"
STATE_WORKING = "working"
STATE_UNSUPPORTED = "unsupported"

# The progress bar's own scale. A byte count would overflow a Tk integer on
# a large model, and a fraction of a thousand is finer than the eye.
PROGRESS_STEPS = 1000


def tier_labels(catalogue):
    """Return one selectable label per catalogued model, and its key."""
    labels = []
    for entry in catalogue.MODEL_TIERS:
        labels.append((entry["key"], "%s  %s  %s" % (
            entry["key"], entry["parameters"],
            bot_chat_install.format_bytes(entry["size"]))))
    return labels


def key_for_label(catalogue, label):
    """Resolve one displayed label back to its catalogue key."""
    for key, text in tier_labels(catalogue):
        if text == label:
            return key
    return catalogue.DEFAULT_TIER_KEY


def resolved_paths(catalogue, tier_key, base_dir=None, runtime_override=""):
    """Return the runtime and model the server should be given, if any.

    A player who already has ``llama-server.exe`` may point at it instead of
    downloading a second copy, so the override wins whenever it names a real
    file.
    """
    entry = catalogue.tier(tier_key) or catalogue.default_tier()
    model = bot_chat_install.model_path(entry, base_dir)
    runtime = str(runtime_override or "").strip()
    if not runtime or not os.path.isfile(runtime):
        runtime = bot_chat_install.runtime_executable(base_dir)
    return {"runtime": runtime, "model": model}


def install_plan(catalogue, tier_key, arch, base_dir=None,
                 runtime_override=""):
    """Describe what still has to be downloaded before a room can talk."""
    state = bot_chat_install.installation_state(
        catalogue, tier_key, arch, base_dir)
    override = str(runtime_override or "").strip()
    if override and os.path.isfile(override):
        state["runtime"] = override
        state["runtime_present"] = True
        state["ready"] = state["model_present"]
    if not state["runtime_available"] and not state["runtime_present"]:
        state["status"] = STATE_UNSUPPORTED
    elif state["ready"]:
        state["status"] = STATE_READY
    else:
        state["status"] = STATE_ABSENT
    entry = catalogue.tier(tier_key)
    pending = 0
    if entry is not None and not state["model_present"]:
        pending += int(entry["size"])
    if not state["runtime_present"]:
        asset = catalogue.runtime_asset(arch)
        pending += int(asset["size"]) if asset else 0
    state["pending_bytes"] = pending
    return state


class BotChatPanel(object):
    """Drive one install from the launcher window without blocking it."""

    def __init__(self, parent, catalogue, tk_module, ttk_module, settings,
                 on_change=None, log=None, base_dir=None, machine=None,
                 on_start=None):
        self._tk = tk_module
        self._ttk = ttk_module
        self._catalogue = catalogue
        self._settings = settings
        self._on_change = on_change or (lambda: None)
        # The window starts reading progress when a download starts, and
        # stops when it ends, rather than polling an idle panel forever.
        self._on_start = on_start or (lambda: None)
        self._log = log or (lambda unused: None)
        self._base_dir = base_dir
        self._arch = bot_chat_install.machine_architecture(catalogue, machine)
        self._thread = None
        self._working = False
        self._cancel = threading.Event()
        self._build(parent)
        self.refresh()

    # -- construction ---------------------------------------------------

    def _build(self, parent):
        tk = self._tk
        self.enabled = tk.BooleanVar(
            value=bool(self._settings.get(ENABLED_SETTING)))
        self.enable_check = tk.Checkbutton(
            parent, text="", variable=self.enabled,
            command=self._enabled_changed)
        self.enable_check.grid(row=0, column=0, columnspan=3, sticky="w")

        self.model_label = tk.Label(parent, text="")
        self.model_label.grid(row=1, column=0, sticky="w")
        labels = [text for unused, text in tier_labels(self._catalogue)]
        current = self._settings.get(
            TIER_SETTING, self._catalogue.DEFAULT_TIER_KEY)
        selected = labels[0]
        for key, text in tier_labels(self._catalogue):
            if key == current:
                selected = text
        self.model_choice = tk.StringVar(value=selected)
        self.model_box = self._ttk.Combobox(
            parent, textvariable=self.model_choice, values=tuple(labels),
            state="readonly", width=34)
        self.model_box.grid(row=1, column=1, sticky="we", padx=(6, 0))
        self.model_box.bind("<<ComboboxSelected>>",
                            lambda unused: self._model_changed())

        self.runtime_label = tk.Label(parent, text="")
        self.runtime_label.grid(row=2, column=0, sticky="w")
        self.runtime_override = tk.StringVar(
            value=self._settings.get(RUNTIME_OVERRIDE_SETTING, ""))
        self.runtime_entry = tk.Entry(
            parent, textvariable=self.runtime_override, width=34)
        self.runtime_entry.grid(row=2, column=1, sticky="we", padx=(6, 0))
        self.runtime_browse = tk.Button(
            parent, text="...", width=3, command=self._browse_runtime)
        self.runtime_browse.grid(row=2, column=2, sticky="w", padx=(6, 0))

        self.progress = self._ttk.Progressbar(
            parent, orient="horizontal", mode="determinate",
            maximum=PROGRESS_STEPS)
        self.progress.grid(row=3, column=0, columnspan=3, sticky="we",
                           pady=(8, 0))
        self.status = tk.StringVar(value="")
        self.status_label = tk.Label(parent, textvariable=self.status,
                                     anchor="w", justify="left")
        self.status_label.grid(row=4, column=0, columnspan=3, sticky="we")
        # A player about to spend hundreds of megabytes should be able to see
        # where they are going before deciding, and what Remove will delete.
        self.location = tk.StringVar(value=bot_chat_install.install_root(
            self._base_dir))
        self.location_entry = tk.Entry(
            parent, textvariable=self.location, state="readonly",
            relief="flat", borderwidth=0, highlightthickness=0)
        self.location_entry.grid(row=5, column=0, columnspan=3, sticky="we")

        self.install_button = tk.Button(parent, text="",
                                        command=self.start_install)
        self.install_button.grid(row=6, column=0, sticky="we", pady=(6, 0))
        self.stop_button = tk.Button(parent, text="", command=self.stop,
                                     state="disabled")
        self.stop_button.grid(row=6, column=1, sticky="we", pady=(6, 0),
                              padx=(6, 0))
        self.remove_button = tk.Button(parent, text="", command=self.remove)
        self.remove_button.grid(row=6, column=2, sticky="we", pady=(6, 0),
                                padx=(6, 0))
        parent.grid_columnconfigure(1, weight=1)

    # -- settings -------------------------------------------------------

    def _enabled_changed(self):
        self._settings[ENABLED_SETTING] = bool(self.enabled.get())
        self.refresh()
        self._on_change()

    def _model_changed(self):
        self._settings[TIER_SETTING] = key_for_label(
            self._catalogue, self.model_choice.get())
        self.refresh()
        self._on_change()

    def _browse_runtime(self):
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(
            title=bot_chat_install.RUNTIME_EXECUTABLE,
            filetypes=[("llama-server", bot_chat_install.RUNTIME_EXECUTABLE)])
        if chosen:
            self.runtime_override.set(chosen)
            self._settings[RUNTIME_OVERRIDE_SETTING] = chosen
            self.refresh()
            self._on_change()

    def tier_key(self):
        return key_for_label(self._catalogue, self.model_choice.get())

    def paths(self):
        """Return what the server should be told, or None when it is off."""
        if not self.enabled.get():
            return None
        plan = self.plan()
        if plan["status"] != STATE_READY:
            return None
        return resolved_paths(self._catalogue, self.tier_key(),
                              self._base_dir, self.runtime_override.get())

    def plan(self):
        return install_plan(self._catalogue, self.tier_key(), self._arch,
                            self._base_dir, self.runtime_override.get())

    # -- install --------------------------------------------------------

    def busy(self):
        """Return whether an install is in flight.

        This is an explicit flag rather than ``Thread.is_alive`` because the
        worker reports its own completion: at that moment its thread is
        still running, and asking the thread would leave the panel stuck
        reporting a download that has already finished.
        """
        return self._working

    def start_install(self, opener=None):
        """Download whatever is still missing, on a worker thread."""
        if self.busy():
            return False
        plan = self.plan()
        if plan["status"] == STATE_UNSUPPORTED:
            self._set_status(STATE_UNSUPPORTED)
            return False
        if plan["status"] == STATE_READY:
            self.refresh()
            return False
        self._cancel.clear()
        self._working = True
        self._set_working()
        self._thread = threading.Thread(
            target=self._install, args=(self.tier_key(), opener),
            name="bot-chat-install", daemon=True)
        self._thread.start()
        self._on_start()
        return True

    def stop(self):
        """Ask the running install to stop at the next chunk."""
        self._cancel.set()

    def _install(self, tier_key, opener=None):
        cancel = self._cancel.is_set
        try:
            if not self.plan()["runtime_present"]:
                self._stage("runtime")
                bot_chat_install.install_runtime(
                    self._catalogue, self._arch, self._base_dir,
                    progress=self._progress, cancel=cancel, opener=opener)
            self._stage("model")
            bot_chat_install.install_model(
                self._catalogue, tier_key, self._base_dir,
                progress=self._progress, cancel=cancel, opener=opener)
        except bot_chat_install.InstallCancelled:
            self._finish(None, cancelled=True)
            return
        except bot_chat_install.InstallError as error:
            self._finish(str(error))
            return
        except Exception as error:  # noqa: BLE001 - a worker must not vanish
            self._finish(str(error))
            return
        self._finish(None)

    # -- reporting ------------------------------------------------------

    def _stage(self, name):
        self._stage_name = name
        self._last_progress = (0, 0)
        self._log("BOT CHAT downloading %s" % name)

    def stage(self):
        """Return which half is downloading, for the status line."""
        return getattr(self, "_stage_name", None)

    def _progress(self, done, total):
        self._last_progress = (done, total)

    def apply_progress(self):
        """Push the worker's latest count onto the bar.

        The download runs on a worker thread, which must not touch Tk. The
        window polls this instead, which is also why the bar exists at all:
        without it a six hundred megabyte download looked like a frozen
        panel.
        """
        self.progress.config(
            value=int(round(self.progress_fraction() * PROGRESS_STEPS)))

    def progress_text(self):
        done, total = getattr(self, "_last_progress", (0, 0))
        if not total:
            return ""
        return "%s / %s" % (bot_chat_install.format_bytes(done),
                            bot_chat_install.format_bytes(total))

    def progress_fraction(self):
        done, total = getattr(self, "_last_progress", (0, 0))
        return (float(done) / float(total)) if total else 0.0

    def _set_working(self):
        self._state = STATE_WORKING
        self._apply_button_state()

    def _set_status(self, state):
        self._state = state
        self._apply_button_state()

    def _apply_button_state(self):
        working = self._state == STATE_WORKING
        self.install_button.config(state="disabled" if working else "normal")
        self.stop_button.config(state="normal" if working else "disabled")
        self.remove_button.config(state="disabled" if working else "normal")

    def _finish(self, error, cancelled=False):
        self._working = False
        self._error = error
        self._cancelled = cancelled
        if error:
            self._log("BOT CHAT install failed: %s" % error)
        elif cancelled:
            self._log("BOT CHAT install stopped; progress was kept")
        else:
            self._log("BOT CHAT install finished")
        self.refresh()
        self._on_change()

    def last_error(self):
        return getattr(self, "_error", None)

    def was_cancelled(self):
        return bool(getattr(self, "_cancelled", False))

    def refresh(self):
        """Recompute the panel state from what is actually on disk."""
        if self.busy():
            self._set_working()
            return
        plan = self.plan()
        self._set_status(plan["status"])

    def state(self):
        return getattr(self, "_state", STATE_ABSENT)

    def remove(self):
        """Delete the download so the disk space comes back."""
        if self.busy():
            return False
        removed = bot_chat_install.remove_installation(self._base_dir)
        self._last_progress = (0, 0)
        self._stage_name = None
        self._error = None
        self._cancelled = False
        self.refresh()
        self._on_change()
        return removed
