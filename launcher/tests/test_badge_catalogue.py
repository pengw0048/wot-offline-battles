"""Exercise the badge editor through the real package reader, not its mock."""

import os
import struct
import tempfile
import unittest
import warnings
import zipfile
from unittest import mock

import core
import save_ledger
import save_personal_missions as missions
import wot_launcher
from test_launcher_window import _FakeTk, _FakeTtk, _Root, _StringVar


packed = missions.vehicle_overlays.packed_xml
MEMBER = "scripts/item_defs/badges.xml"


def _text(value):
    return packed.PackedValue(packed.TYPE_STRING, value.encode("utf-8"))


def _element(children, value=None):
    return packed.PackedValue(packed.TYPE_ELEMENT,
                             packed.PackedElement(value, children))


def _catalogue(rows):
    badges = []
    for badge_id, name, weight in rows:
        fields = [(b"item", _element([(b"name", _text("id")),
                   (b"type", _text("int"))], _text(str(badge_id))))]
        if weight is not None:
            fields.append((b"item", _element([(b"name", _text("weight")),
                           (b"type", _text("float"))], _text(str(weight)))))
        badges.append((b"badge", _element([
            (b"name", _text(name)), (b"type", _text("dict")),
            (b"value", _element(fields))])))
    return packed.write_packed_xml(packed.PackedElement(children=[
        (b"badges", _element(badges))]))


def _translations(messages):
    """Make a small valid GNU MO catalogue for the installed-client fixture."""
    messages = {"": "Content-Type: text/plain; charset=UTF-8\n", **messages}
    pairs = sorted((key.encode("utf-8"), value.encode("utf-8"))
                   for key, value in messages.items())
    count = len(pairs)
    offset = 28 + count * 16
    originals = b"".join(key + b"\0" for key, unused in pairs)
    originals_index, translations_index = [], []
    for key, unused in pairs:
        originals_index.append(struct.pack("<II", len(key), offset))
        offset += len(key) + 1
    for unused, value in pairs:
        translations_index.append(struct.pack("<II", len(value), offset))
        offset += len(value) + 1
    return (struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + count * 8, 0, 0)
            + b"".join(originals_index + translations_index) + originals
            + b"".join(value + b"\0" for unused, value in pairs))


class BadgeCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = self.temp.name
        patcher = mock.patch.dict(os.environ, {"APPDATA": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self._write(core.GAME_EXECUTABLE, b"")
        self._write("version.xml", b"<version> v.0.9.22.0.1 #1513 </version>")
        self.package = os.path.join(self.game, "res", "packages", "scripts.pkg")
        os.makedirs(os.path.dirname(self.package))
        self.rows = [(29, "ranked_s1_gold", 5.1),
                     (10, "personal_missions_stug_iv", 1.1)]
        self._package([(MEMBER, _catalogue(self.rows))])

    def _write(self, relative, data):
        path = os.path.join(self.game, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(data)

    def _package(self, members):
        with zipfile.ZipFile(self.package, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)

    def _owner(self):
        owner = object.__new__(wot_launcher.LauncherWindow)
        owner._tk, owner._ttk = _FakeTk, _FakeTtk
        owner._t = lambda text: text
        owner._save_slot_id = "career"
        owner.game_root = _StringVar(self.game)
        owner.save_dialog = _Root()
        owner.save_dialog_feedback = _FakeTk.Label(owner.save_dialog)
        owner._busy = owner._maintenance_busy = False
        owner.messages = []
        owner._log = owner.messages.append
        return owner

    def test_real_package_opens_badges_and_saves_ownership_without_vehicle_edits(self):
        with open(self.package, "rb") as stream:
            original = stream.read()
        owner = self._owner()
        with mock.patch.object(core, "game_is_running", return_value=False):
            self.assertTrue(owner._open_account_badges(), owner.messages)
            dialog = owner._personal_editor
            self.assertTrue(dialog.window.options["grabbed"])
            self.assertEqual(("personal_missions_stug_iv", "ranked_s1_gold"), dialog.labels)
            dialog.achieved.set(True)
            dialog.changed()
            self.assertTrue(dialog.save())
            self.assertEqual({"10"}, set(missions.read_account_fields(
                "career", self.game)["badges"]))
            dialog.close()
            self.assertTrue(owner.save_dialog.options["grabbed"])
            self.assertTrue(owner._open_account_badges())
            self.assertTrue(owner._personal_editor.achieved.get())
        with open(self.package, "rb") as stream:
            self.assertEqual(original, stream.read())
        self.assertFalse(os.path.exists(os.path.join(self.game, "res_mods")))
        with self.assertRaisesRegex(missions.vehicle_overlays.VehicleOverlayError,
                                    "Only 0.9.22 vehicle definitions"):
            missions.vehicle_overlays._read_source_member(self.package, MEMBER)

    def test_native_badge_names_use_installed_translation_catalogue(self):
        self._write("res/text/LC_MESSAGES/badge.mo", _translations({
            "badge_10": "IV号突击炮I级勋章", "badge_29": "第1赛季黄金勋章"}))
        rows = missions.badge_catalogue(self.game)
        self.assertEqual([10, 29], [row["id"] for row in rows])
        self.assertEqual(["IV号突击炮I级勋章", "第1赛季黄金勋章"],
                         [row["label"] for row in rows])

    def test_badge_without_weight_remains_available_to_edit(self):
        self._package([(MEMBER, _catalogue(self.rows + [
            (99, "badge_without_weight", None)]))])
        rows = missions.badge_catalogue(self.game)
        self.assertEqual([99, 10, 29], [row["id"] for row in rows])
        self.assertEqual(-1.0, rows[0]["weight"])

    def test_missing_duplicate_and_corrupt_members_fail_visibly_before_dialog(self):
        for members in ([], [(MEMBER, b"bad data")],
                        [(MEMBER, _catalogue(self.rows))] * 2):
            with self.subTest(members=len(members)), warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                self._package(members)
                owner = self._owner()
                self.assertFalse(owner._open_account_badges())
                self.assertFalse(hasattr(owner, "_personal_editor"))
                self.assertIn("badge catalogue", owner.messages[-1])

    def test_invalid_catalogue_schema_fails_as_displayable_save_error(self):
        for rows in ([], self.rows + [self.rows[0]], [("bad id", "broken", None)]):
            with self.subTest(rows=rows):
                self._package([(MEMBER, _catalogue(rows))])
                with self.assertRaisesRegex(save_ledger.SaveLedgerError, "expected format"):
                    missions.badge_catalogue(self.game)

    def test_target_version_is_checked_before_reading_catalogue(self):
        self._write("version.xml", b"<version> v.1.0.0 #100 </version>")
        with self.assertRaisesRegex(missions.vehicle_overlays.VehicleOverlayError,
                                    "exact supported 0.9.22 client"):
            missions.badge_catalogue(self.game)


if __name__ == "__main__":
    unittest.main()
