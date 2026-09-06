import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
import zipfile


LAUNCHER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAUNCHER_ROOT))

import bot_chat_install as install  # noqa: E402
import bot_chat_ui as ui  # noqa: E402


MODEL_PAYLOAD = b"model" * 400
RUNTIME_MEMBERS = (("llama-server.exe", b"MZ server"),
                   ("ggml-base.dll", b"MZ ggml"))


def _zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, payload in members:
            bundle.writestr(name, payload)
    return buffer.getvalue()


RUNTIME_PAYLOAD = _zip(RUNTIME_MEMBERS)


def _entry(name, payload):
    return {"file": name, "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()}


class _Catalogue(object):
    MODELSCOPE = "modelscope"
    HUGGINGFACE = "huggingface"
    GITHUB = "github"
    SOURCES = (MODELSCOPE, HUGGINGFACE)
    DEFAULT_TIER_KEY = "small"
    MODEL_TIERS = (
        dict(_entry("small.gguf", MODEL_PAYLOAD), key="small",
             parameters="0.6B", quantization="Q8_0", license="Apache-2.0"),
        dict(_entry("big.gguf", MODEL_PAYLOAD + b"!"), key="big",
             parameters="1.7B", quantization="Q8_0", license="Apache-2.0"),
    )

    def __init__(self, arch_supported=True):
        self.arch_supported = arch_supported

    def tier(self, key):
        for entry in self.MODEL_TIERS:
            if entry["key"] == key:
                return dict(entry)
        return None

    def default_tier(self):
        return self.tier(self.DEFAULT_TIER_KEY)

    def model_url(self, key, source):
        entry = self.tier(key)
        return None if entry is None else "https://%s/%s" % (
            source, entry["file"])

    def runtime_asset(self, arch):
        if arch != "x64" or not self.arch_supported:
            return None
        return dict(_entry("runtime.zip", RUNTIME_PAYLOAD))

    def runtime_sources(self, arch):
        return (self.MODELSCOPE, self.GITHUB) if self.runtime_asset(
            arch) else ()

    def runtime_url(self, arch, source=None):
        if self.runtime_asset(arch) is None:
            return None
        return "https://%s/runtime.zip" % (source or self.MODELSCOPE)

    @staticmethod
    def runtime_arch(machine):
        return "x64" if str(machine).lower() == "amd64" else None


class _Response(object):
    def __init__(self, payload):
        self._stream = io.BytesIO(payload)
        self.status = 200

    def read(self, size=-1):
        return self._stream.read(size)

    def close(self):
        pass


def _opener(model_payload=MODEL_PAYLOAD, fail_urls=()):
    def opener(request, timeout=None):
        url = request.full_url
        if any(token in url for token in fail_urls):
            raise OSError("unreachable")
        if "runtime.zip" in url:
            return _Response(RUNTIME_PAYLOAD)
        if "big.gguf" in url:
            return _Response(MODEL_PAYLOAD + b"!")
        return _Response(model_payload)
    return opener


class _Var(object):
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Widget(object):
    def __init__(self, *unused_args, **options):
        self.options = dict(options)
        self.gridded = None

    def grid(self, **options):
        self.gridded = options

    def config(self, **options):
        self.options.update(options)

    def bind(self, *unused):
        pass


class _Tk(object):
    BooleanVar = staticmethod(lambda value=False: _Var(bool(value)))
    StringVar = staticmethod(lambda value="": _Var(value))
    Label = _Widget
    Entry = _Widget
    Button = _Widget
    Checkbutton = _Widget
    Frame = _Widget


class _Ttk(object):
    Combobox = _Widget
    Progressbar = _Widget


class _Parent(_Widget):
    def grid_columnconfigure(self, *unused, **unused_options):
        pass


def _panel(base, catalogue=None, settings=None, machine="AMD64"):
    return ui.BotChatPanel(
        _Parent(), catalogue or _Catalogue(), _Tk, _Ttk,
        settings if settings is not None else {},
        base_dir=base, machine=machine)


def _await(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _Temp(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="bot-chat-ui-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)


class LabelTest(unittest.TestCase):
    def test_every_model_is_offered_with_its_download_size(self):
        labels = ui.tier_labels(_Catalogue())
        self.assertEqual(["small", "big"], [key for key, unused in labels])
        self.assertIn("0.6B", labels[0][1])
        self.assertIn("KB", labels[0][1])

    def test_a_label_resolves_back_to_its_key(self):
        catalogue = _Catalogue()
        for key, text in ui.tier_labels(catalogue):
            self.assertEqual(key, ui.key_for_label(catalogue, text))

    def test_an_unknown_label_falls_back_to_the_default(self):
        self.assertEqual("small", ui.key_for_label(_Catalogue(), "nonsense"))


class PlanTest(_Temp):
    def test_nothing_installed_needs_both_downloads(self):
        catalogue = _Catalogue()
        plan = ui.install_plan(catalogue, "small", "x64", self.base)
        self.assertEqual(ui.STATE_ABSENT, plan["status"])
        self.assertEqual(len(MODEL_PAYLOAD) + len(RUNTIME_PAYLOAD),
                         plan["pending_bytes"])

    def test_a_machine_with_no_runtime_is_reported_unsupported(self):
        plan = ui.install_plan(_Catalogue(arch_supported=False), "small",
                               None, self.base)
        self.assertEqual(ui.STATE_UNSUPPORTED, plan["status"])

    def test_an_existing_generator_satisfies_the_runtime(self):
        existing = os.path.join(self.base, "llama-server.exe")
        with open(existing, "wb") as stream:
            stream.write(b"MZ")
        plan = ui.install_plan(_Catalogue(), "small", "x64", self.base,
                               runtime_override=existing)
        self.assertTrue(plan["runtime_present"])
        self.assertEqual(existing, plan["runtime"])
        self.assertEqual(len(MODEL_PAYLOAD), plan["pending_bytes"])

    def test_an_override_naming_nothing_is_ignored(self):
        plan = ui.install_plan(_Catalogue(), "small", "x64", self.base,
                               runtime_override="/nowhere/llama-server.exe")
        self.assertFalse(plan["runtime_present"])


class PanelTest(_Temp):
    def test_a_fresh_panel_reports_nothing_installed(self):
        panel = _panel(self.base)
        self.assertEqual(ui.STATE_ABSENT, panel.state())
        self.assertIsNone(panel.paths())

    def test_installing_both_halves_makes_the_panel_ready(self):
        panel = _panel(self.base)
        self.assertTrue(panel.start_install(opener=_opener()))
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertIsNone(panel.last_error())
        self.assertEqual(ui.STATE_READY, panel.state())

    def test_a_ready_panel_names_both_paths_only_when_enabled(self):
        settings = {}
        panel = _panel(self.base, settings=settings)
        panel.start_install(opener=_opener())
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertIsNone(panel.paths())
        panel.enabled.set(True)
        paths = panel.paths()
        self.assertTrue(os.path.isfile(paths["runtime"]))
        self.assertTrue(os.path.isfile(paths["model"]))

    def test_the_enable_choice_is_remembered(self):
        settings = {}
        panel = _panel(self.base, settings=settings)
        panel.enabled.set(True)
        panel._enabled_changed()
        self.assertTrue(settings[ui.ENABLED_SETTING])

    def test_the_model_choice_is_remembered(self):
        settings = {}
        panel = _panel(self.base, settings=settings)
        panel.model_choice.set(ui.tier_labels(_Catalogue())[1][1])
        panel._model_changed()
        self.assertEqual("big", settings[ui.TIER_SETTING])
        self.assertEqual("big", panel.tier_key())

    def test_a_remembered_model_is_selected_on_the_next_launch(self):
        panel = _panel(self.base, settings={ui.TIER_SETTING: "big"})
        self.assertEqual("big", panel.tier_key())

    def test_a_failed_download_is_reported_and_installs_nothing(self):
        panel = _panel(self.base)
        panel.start_install(opener=_opener(fail_urls=("modelscope",
                                                      "github",
                                                      "huggingface")))
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertIsNotNone(panel.last_error())
        self.assertEqual(ui.STATE_ABSENT, panel.state())

    def test_a_second_install_is_refused_while_one_runs(self):
        panel = _panel(self.base)
        started = threading_gate()
        panel.start_install(opener=started.opener)
        self.assertFalse(panel.start_install(opener=_opener()))
        started.release()
        self.assertTrue(_await(lambda: not panel.busy()))

    def test_stopping_keeps_what_was_downloaded(self):
        panel = _panel(self.base)
        gate = threading_gate(cancel_panel=panel)
        panel.start_install(opener=gate.opener)
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertTrue(panel.was_cancelled())
        self.assertIsNone(panel.last_error())

    def test_removing_deletes_the_download(self):
        panel = _panel(self.base)
        panel.start_install(opener=_opener())
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertTrue(panel.remove())
        self.assertEqual(ui.STATE_ABSENT, panel.state())

    def test_an_unsupported_machine_never_starts_a_download(self):
        panel = _panel(self.base, catalogue=_Catalogue(arch_supported=False),
                       machine="x86")
        self.assertFalse(panel.start_install(opener=_opener()))
        self.assertEqual(ui.STATE_UNSUPPORTED, panel.state())

    def test_progress_is_reportable_while_working(self):
        panel = _panel(self.base)
        panel.start_install(opener=_opener())
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertEqual(1.0, panel.progress_fraction())
        self.assertIn("/", panel.progress_text())

    def test_buttons_follow_the_working_state(self):
        panel = _panel(self.base)
        panel._set_working()
        self.assertEqual("disabled", panel.install_button.options["state"])
        self.assertEqual("normal", panel.stop_button.options["state"])
        panel._set_status(ui.STATE_ABSENT)
        self.assertEqual("normal", panel.install_button.options["state"])
        self.assertEqual("disabled", panel.stop_button.options["state"])


class _Gate(object):
    """Hold one download open so a concurrent state can be observed."""

    def __init__(self, cancel_panel=None):
        self._release = False
        self._cancel_panel = cancel_panel

    def opener(self, request, timeout=None):
        if self._cancel_panel is not None:
            self._cancel_panel.stop()
        while not self._release and self._cancel_panel is None:
            time.sleep(0.005)
        return _opener()(request, timeout)

    def release(self):
        self._release = True


def threading_gate(cancel_panel=None):
    return _Gate(cancel_panel)


class ProgressReportingTest(_Temp):
    """The bar and the stage exist so a long download does not look hung."""

    def test_the_bar_starts_empty(self):
        panel = _panel(self.base)
        panel.apply_progress()
        self.assertEqual(0, panel.progress.options["value"])

    def test_the_bar_follows_the_byte_count(self):
        panel = _panel(self.base)
        panel._progress(50, 200)
        panel.apply_progress()
        self.assertEqual(ui.PROGRESS_STEPS // 4,
                         panel.progress.options["value"])

    def test_the_bar_fills_completely(self):
        panel = _panel(self.base)
        panel._progress(200, 200)
        panel.apply_progress()
        self.assertEqual(ui.PROGRESS_STEPS, panel.progress.options["value"])

    def test_each_stage_reports_its_own_progress(self):
        panel = _panel(self.base)
        panel._progress(10, 200)
        panel._stage("model")
        # A new stage restarts the count rather than inheriting the last.
        self.assertEqual("model", panel.stage())
        self.assertEqual(0.0, panel.progress_fraction())

    def test_the_window_is_told_when_a_download_starts(self):
        started = []
        panel = ui.BotChatPanel(
            _Parent(), _Catalogue(), _Tk, _Ttk, {}, base_dir=self.base,
            machine="AMD64", on_start=lambda: started.append(1))
        self.assertTrue(panel.start_install(opener=_opener()))
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertEqual(1, len(started))

    def test_a_refused_start_tells_the_window_nothing(self):
        started = []
        panel = ui.BotChatPanel(
            _Parent(), _Catalogue(arch_supported=False), _Tk, _Ttk, {},
            base_dir=self.base, machine="x86",
            on_start=lambda: started.append(1))
        self.assertFalse(panel.start_install(opener=_opener()))
        self.assertEqual([], started)

    def test_the_install_location_is_shown(self):
        panel = _panel(self.base)
        self.assertEqual(ui.bot_chat_install.install_root(self.base),
                         panel.location.get())

    def test_removing_clears_the_last_failure(self):
        panel = _panel(self.base)
        panel.start_install(opener=_opener(fail_urls=("modelscope", "github",
                                                      "huggingface")))
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertIsNotNone(panel.last_error())
        panel.remove()
        self.assertIsNone(panel.last_error())
        self.assertIsNone(panel.stage())


class _FakeSupervisor(object):
    def __init__(self, started=True, ready=True, tail=""):
        self.started = started
        self.ready = ready
        self.tail = tail
        self.stopped = False
        self.endpoint = "http://127.0.0.1:1"

    def output_tail(self, lines=12):
        return self.tail

    def start(self):
        return self.started

    def wait_ready(self, timeout=None):
        return self.ready

    def stop(self):
        self.stopped = True


class _FakeBackend(object):
    def __init__(self, line="收到"):
        self.line = line
        self.stopped = False

    def start(self):
        pass

    def prefetch(self, request):
        pass

    def compose(self, request):
        return self.line

    def stop(self):
        self.stopped = True


class _FakeRuntime(object):
    def __init__(self, supervisor=None, backend=None):
        self.supervisor = supervisor or _FakeSupervisor()
        self.backend = backend if backend is not None else _FakeBackend()

    def LlamaServerSupervisor(self, executable, model, log=None):
        return self.supervisor

    def LlamaChatBackend(self, endpoint, log=None):
        return self.backend


class SelfTestTest(_Temp):
    """Installed files are not proof; a machine has to produce a line."""

    def _installed(self):
        panel = _panel(self.base)
        panel.start_install(opener=_opener())
        self.assertTrue(_await(lambda: not panel.busy()))
        panel.enabled.set(True)
        return panel

    def test_nothing_installed_cannot_be_tested(self):
        panel = _panel(self.base)
        self.assertFalse(panel.start_check(runtime=_FakeRuntime()))
        self.assertEqual(ui.CHECK_UNKNOWN, panel.check_state())

    def test_a_working_machine_reports_the_line_it_produced(self):
        panel = self._installed()
        runtime = _FakeRuntime()
        self.assertTrue(panel.start_check(runtime=runtime))
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertEqual(ui.CHECK_PASSED, panel.check_state())
        self.assertEqual("收到", panel.check_line())
        self.assertTrue(runtime.supervisor.stopped)
        self.assertTrue(runtime.backend.stopped)

    def test_a_runtime_that_will_not_start_is_reported(self):
        panel = self._installed()
        runtime = _FakeRuntime(supervisor=_FakeSupervisor(started=False))
        panel.start_check(runtime=runtime)
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertEqual(ui.CHECK_FAILED, panel.check_state())
        self.assertIn("did not start", panel.check_error())

    def test_a_model_that_never_loads_is_reported(self):
        panel = self._installed()
        runtime = _FakeRuntime(supervisor=_FakeSupervisor(ready=False))
        panel.start_check(runtime=runtime)
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertEqual(ui.CHECK_FAILED, panel.check_state())
        self.assertIn("finish loading", panel.check_error())

    def test_a_model_that_writes_nothing_is_reported(self):
        panel = self._installed()
        runtime = _FakeRuntime(backend=_FakeBackend(line=None))
        panel.check_timeout = 0.5
        panel.start_check(runtime=runtime)
        self.assertTrue(_await(lambda: not panel.busy(), timeout=30.0))
        self.assertEqual(ui.CHECK_FAILED, panel.check_state())

    def test_the_generator_is_always_stopped_afterwards(self):
        panel = self._installed()
        runtime = _FakeRuntime(supervisor=_FakeSupervisor(started=False))
        panel.start_check(runtime=runtime)
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertTrue(runtime.supervisor.stopped)

    def test_a_failed_load_quotes_what_the_generator_said(self):
        panel = self._installed()
        runtime = _FakeRuntime(supervisor=_FakeSupervisor(
            ready=False, tail='error: invalid argument: --reasoning-budget'))
        panel.start_check(runtime=runtime)
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertIn('--reasoning-budget', panel.check_error())

    def test_a_silent_generator_still_names_the_step_that_failed(self):
        panel = self._installed()
        runtime = _FakeRuntime(supervisor=_FakeSupervisor(ready=False))
        panel.start_check(runtime=runtime)
        self.assertTrue(_await(lambda: not panel.busy()))
        self.assertIn('finish loading', panel.check_error())

    def test_removing_clears_a_test_result(self):
        panel = self._installed()
        panel.start_check(runtime=_FakeRuntime())
        self.assertTrue(_await(lambda: not panel.busy()))
        panel.remove()
        self.assertEqual(ui.CHECK_UNKNOWN, panel.check_state())
        self.assertIsNone(panel.check_line())

    def test_a_switched_off_install_is_not_a_working_feature(self):
        panel = self._installed()
        panel.enabled.set(False)
        self.assertFalse(panel.enabled_and_ready())
        panel.enabled.set(True)
        self.assertTrue(panel.enabled_and_ready())


class ResolvedPathTest(_Temp):
    def test_the_catalogue_model_is_named_for_the_selected_tier(self):
        catalogue = _Catalogue()
        paths = ui.resolved_paths(catalogue, "big", self.base)
        self.assertTrue(paths["model"].endswith("big.gguf"))

    def test_an_unknown_tier_falls_back_to_the_default_model(self):
        paths = ui.resolved_paths(_Catalogue(), "gone", self.base)
        self.assertTrue(paths["model"].endswith("small.gguf"))

    def test_an_existing_generator_is_preferred_over_a_download(self):
        existing = os.path.join(self.base, "mine.exe")
        with open(existing, "wb") as stream:
            stream.write(b"MZ")
        paths = ui.resolved_paths(_Catalogue(), "small", self.base, existing)
        self.assertEqual(existing, paths["runtime"])


if __name__ == "__main__":
    unittest.main()
