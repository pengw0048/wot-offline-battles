import json
import os
import tempfile
import unittest

import save_slots


class SaveSlotsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = os.path.join(directory.name, "saves")

    def _names(self):
        return [row["name"] for row in save_slots.list_slots(root=self.root)]

    def test_the_default_save_is_listable_before_it_exists_on_disk(self):
        rows = save_slots.list_slots(root=self.root)

        self.assertEqual([save_slots.DEFAULT_SLOT_ID],
                         [row["id"] for row in rows])
        self.assertFalse(rows[0]["has_state"])
        self.assertFalse(os.path.isdir(self.root))

    def test_new_saves_cannot_take_the_reserved_default_id(self):
        for name in ('default', 'Default', 'DEFAULT'):
            row = save_slots.create_slot(
                name, save_slots.MODE_NEW_ACCOUNT, root=self.root)
            self.assertNotEqual('default', row['id'].lower())
            self.assertEqual(name, row['name'])
            self.assertFalse(row['has_state'])
        self.assertFalse(os.path.exists(os.path.join(self.root, 'default')))

    def test_a_created_save_owns_an_empty_directory_and_a_record(self):
        record = save_slots.create_slot(
            "Career", save_slots.MODE_UNLOCKED, root=self.root, now=1700)

        self.assertEqual("Career", record["name"])
        self.assertEqual(save_slots.MODE_UNLOCKED, record["mode"])
        self.assertEqual(1700, record["created"])
        self.assertFalse(record["has_state"])
        self.assertEqual(
            [save_slots.METADATA_NAME], os.listdir(record["path"]))
        with open(os.path.join(record["path"], save_slots.METADATA_NAME),
                  "rb") as stream:
            self.assertEqual("Career", json.load(stream)["name"])

    def test_a_new_save_earns_at_the_ordinary_rate(self):
        record = save_slots.create_slot(
            "Career", save_slots.MODE_UNLOCKED, root=self.root)

        self.assertEqual(100, record["earnings_percent"])

    def test_a_saves_earnings_multiplier_is_kept_and_read_back(self):
        """The client reads this number and scales what every battle pays."""
        record = save_slots.create_slot(
            "Rich", save_slots.MODE_NEW_ACCOUNT, root=self.root,
            earnings_percent=250)

        self.assertEqual(250, record["earnings_percent"])
        with open(os.path.join(record["path"], save_slots.METADATA_NAME),
                  "rb") as stream:
            self.assertEqual(250, json.load(stream)["earnings_percent"])
        self.assertEqual(
            250, save_slots.read_slot(record["id"], root=self.root)[
                "earnings_percent"])

    def test_the_multiplier_can_be_changed_before_the_save_is_ever_played(self):
        """It is the launcher's own record, not the client's state."""
        record = save_slots.set_earnings_percent(
            save_slots.DEFAULT_SLOT_ID, 300, root=self.root)

        self.assertEqual(300, record["earnings_percent"])
        self.assertEqual(
            300, save_slots.read_slot(
                save_slots.DEFAULT_SLOT_ID, root=self.root)[
                    "earnings_percent"])

    def test_an_impossible_multiplier_is_clamped_rather_than_stored(self):
        for wanted, expected in ((0, 1), (-5, 1), (99999, 10000),
                                 ("lots", 100), (None, 100)):
            record = save_slots.set_earnings_percent(
                save_slots.DEFAULT_SLOT_ID, wanted, root=self.root)
            self.assertEqual(expected, record["earnings_percent"], wanted)

    def test_renaming_a_save_keeps_its_multiplier(self):
        created = save_slots.create_slot(
            "Rich", save_slots.MODE_UNLOCKED, root=self.root,
            earnings_percent=175)

        renamed = save_slots.rename_slot(
            created["id"], "Richer", root=self.root)

        self.assertEqual(175, renamed["earnings_percent"])

    def test_a_save_written_before_the_multiplier_earns_ordinarily(self):
        record = save_slots.create_slot(
            "Old", save_slots.MODE_UNLOCKED, root=self.root)
        path = os.path.join(record["path"], save_slots.METADATA_NAME)
        with open(path, "rb") as stream:
            metadata = json.load(stream)
        del metadata["earnings_percent"]
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(metadata, stream)

        self.assertEqual(
            100, save_slots.read_slot(record["id"], root=self.root)[
                "earnings_percent"])

    def test_a_non_ascii_name_still_produces_a_usable_directory_name(self):
        record = save_slots.create_slot(
            "生涯存档", save_slots.MODE_UNLOCKED, root=self.root)

        self.assertTrue(save_slots.valid_slot_id(record["id"]))
        self.assertEqual("生涯存档", record["name"])
        self.assertTrue(os.path.isdir(record["path"]))

    def test_two_saves_with_the_same_name_keep_separate_directories(self):
        first = save_slots.create_slot(
            "Career", save_slots.MODE_UNLOCKED, root=self.root)
        second = save_slots.create_slot(
            "Career", save_slots.MODE_UNLOCKED, root=self.root)

        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(["default", "Career", "Career"], self._names())

    def test_a_save_name_is_normalized_and_bounded(self):
        record = save_slots.create_slot(
            "  spaced   out  ", save_slots.MODE_UNLOCKED, root=self.root)
        self.assertEqual("spaced out", record["name"])

        for bad in ("", "   ", "x" * (save_slots.MAX_SLOT_NAME_LENGTH + 1),
                    None, 5):
            with self.assertRaises(save_slots.SaveSlotError):
                save_slots.create_slot(
                    bad, save_slots.MODE_UNLOCKED, root=self.root)

    def test_an_unknown_save_type_is_refused(self):
        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.create_slot("Career", "everything", root=self.root)

    def test_a_save_id_may_not_escape_the_saves_directory(self):
        for value in ("", ".", "..", "a/b", "a\\b", "-lead", "x" * 65, None):
            self.assertFalse(save_slots.valid_slot_id(value), repr(value))
            with self.assertRaises(save_slots.SaveSlotError):
                save_slots.slot_dir(value, root=self.root)

    def test_renaming_keeps_the_directory_and_everything_earned_in_it(self):
        record = save_slots.create_slot(
            "Career", save_slots.MODE_UNLOCKED, root=self.root, now=1700)
        state = os.path.join(record["path"], "garage_state.json")
        with open(state, "w", encoding="utf-8") as stream:
            stream.write("{}")

        renamed = save_slots.rename_slot(
            record["id"], "Second career", root=self.root)

        self.assertEqual(record["id"], renamed["id"])
        self.assertEqual("Second career", renamed["name"])
        self.assertEqual(1700, renamed["created"])
        self.assertTrue(renamed["has_state"])
        self.assertTrue(os.path.isfile(state))

    def test_a_save_with_an_unreadable_record_is_still_reported(self):
        record = save_slots.create_slot(
            "Career", save_slots.MODE_UNLOCKED, root=self.root)
        with open(os.path.join(record["path"], save_slots.METADATA_NAME),
                  "w", encoding="utf-8") as stream:
            stream.write("not json")
        with open(os.path.join(record["path"], "garage_state.json"),
                  "w", encoding="utf-8") as stream:
            stream.write("{}")

        rows = save_slots.list_slots(root=self.root)

        damaged = [row for row in rows if row["id"] == record["id"]]
        self.assertEqual(1, len(damaged))
        self.assertEqual(record["id"], damaged[0]["name"])
        self.assertTrue(damaged[0]["has_state"])

    def test_deleting_a_save_removes_its_directory(self):
        record = save_slots.create_slot(
            "Career", save_slots.MODE_UNLOCKED, root=self.root)

        save_slots.delete_slot(record["id"], root=self.root)

        self.assertFalse(os.path.exists(record["path"]))
        self.assertEqual(["default"], self._names())

    def test_the_default_save_cannot_be_deleted(self):
        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.delete_slot(
                save_slots.DEFAULT_SLOT_ID, root=self.root)

    def test_a_missing_save_is_refused_rather_than_recreated(self):
        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.read_slot("gone", root=self.root)
        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.rename_slot("gone", "Career", root=self.root)

    def test_a_stray_file_in_the_saves_directory_is_not_a_save(self):
        os.makedirs(self.root)
        with open(os.path.join(self.root, "notes.txt"), "w",
                  encoding="utf-8") as stream:
            stream.write("hello")
        os.makedirs(os.path.join(self.root, "not a slot id"))

        self.assertEqual(["default"], self._names())

    def test_a_linked_slot_cannot_read_or_write_outside_the_saves_root(self):
        os.makedirs(self.root)
        outside = os.path.join(os.path.dirname(self.root), "outside")
        os.makedirs(outside)
        linked = os.path.join(self.root, "linked")
        try:
            os.symlink(outside, linked, target_is_directory=True)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("directory symlinks are unavailable")

        self.assertEqual(["default"], self._names())
        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.read_slot("linked", root=self.root)
        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.rename_slot("linked", "Outside", root=self.root)
        self.assertFalse(os.path.exists(os.path.join(outside, "save.json")))

    def test_the_saves_root_follows_appdata_and_falls_back_to_the_game(self):
        appdata = save_slots.saves_root(
            environment={"APPDATA": os.path.join("C:\\", "Users", "p",
                                                 "AppData", "Roaming")})
        self.assertTrue(appdata.endswith(
            os.path.join("offline_lan_0922", "saves")))

        fallback = save_slots.saves_root(
            game_root=os.path.join("D:\\", "WoT"), environment={})
        self.assertTrue(fallback.endswith(
            os.path.join("offline_lan_0922", "saves")))
        self.assertIn("mods", fallback)

        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.saves_root(environment={})


if __name__ == "__main__":
    unittest.main()


class SaveBackupTests(unittest.TestCase):
    """A player-owned copy of one save, and a restore that keeps evidence."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = directory.name
        self.root = os.path.join(directory.name, "saves")

    def _slot_with_state(self, name="Career", **files):
        record = save_slots.create_slot(
            name, save_slots.MODE_NEW_ACCOUNT, root=self.root)
        payload = {
            "garage_state.json": {"schema": 7, "vehicles": {"50001": {}}},
            "postbattle_state.json": {"schema": 1, "progress": {"battles": 9}},
        }
        payload.update(files)
        for file_name, value in payload.items():
            with open(os.path.join(record["path"], file_name), "w",
                      encoding="utf-8") as stream:
                json.dump(value, stream)
        return record

    def _archive(self, name="backup.zip"):
        return os.path.join(self.base, name)

    def test_a_backup_holds_the_saves_own_files_and_names_its_origin(self):
        record = self._slot_with_state()

        result = save_slots.backup_slot(
            record["id"], self._archive(), root=self.root)

        self.assertEqual(
            ["garage_state.json", "postbattle_state.json", "save.json"],
            sorted(result["files"]))
        described = save_slots.read_backup(result["path"])
        self.assertEqual(record["id"], described["id"])
        self.assertEqual("Career", described["name"])

    def test_a_backup_keeps_the_copies_the_client_left_behind(self):
        """The evidence is part of the save a player wants back.

        The client keeps ``.backup1`` and ``.rejected-`` copies of a state
        file beside the live one, and those are exactly what a player
        recovering a broken career needs.
        """
        record = self._slot_with_state(**{
            "garage_state.backup1.json": {"schema": 7, "vehicles": {}},
            "garage_state.rejected-20260908-000000-000.json": {"schema": 7},
        })

        result = save_slots.backup_slot(
            record["id"], self._archive(), root=self.root)

        self.assertIn("garage_state.backup1.json", result["files"])
        self.assertIn(
            "garage_state.rejected-20260908-000000-000.json",
            result["files"])

    def test_an_empty_save_has_nothing_to_back_up(self):
        record = save_slots.create_slot(
            "Fresh", save_slots.MODE_NEW_ACCOUNT, root=self.root)
        os.unlink(os.path.join(record["path"], save_slots.METADATA_NAME))

        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.backup_slot(
                record["id"], self._archive(), root=self.root)

    def test_a_restore_replaces_the_state_and_keeps_what_it_replaced(self):
        source = self._slot_with_state("Career")
        save_slots.backup_slot(
            source["id"], self._archive(), root=self.root)
        target = self._slot_with_state("Renamed career", **{
            "garage_state.json": {"schema": 7, "vehicles": {"50009": {}}},
        })

        result = save_slots.restore_slot(
            target["id"], self._archive(), root=self.root,
            is_running=lambda: False)

        with open(os.path.join(target["path"], "garage_state.json"),
                  encoding="utf-8") as stream:
            self.assertEqual(["50001"], list(json.load(stream)["vehicles"]))
        # The save keeps its own name and the replaced files stay recoverable.
        self.assertEqual(
            "Renamed career",
            save_slots.read_slot(target["id"], root=self.root)["name"])
        self.assertEqual("Career", result["from_name"])
        with open(os.path.join(result["replaced"], "garage_state.json"),
                  encoding="utf-8") as stream:
            self.assertEqual(["50009"], list(json.load(stream)["vehicles"]))

    def test_failed_rollback_keeps_the_original_files_for_recovery(self):
        from unittest import mock
        record = self._slot_with_state()
        save_slots.backup_slot(record["id"], self._archive(), root=self.root)
        real_replace = os.replace

        def replace(source, destination):
            if "pre-restore-" in source:
                raise OSError("restore target is locked")
            return real_replace(source, destination)

        backup = save_slots.read_backup(self._archive())
        with mock.patch.object(save_slots, "read_backup", return_value=backup), \
                mock.patch.object(save_slots.os, "replace", side_effect=replace), \
                mock.patch("zipfile.ZipFile.open", side_effect=OSError("read failed")):
            with self.assertRaises(save_slots.SaveSlotError):
                save_slots.restore_slot(
                    record["id"], self._archive(), root=self.root,
                    is_running=lambda: False)
        kept = [name for name in os.listdir(record["path"])
                if name.startswith("pre-restore-")]
        self.assertEqual(1, len(kept))
        with open(os.path.join(record["path"], kept[0], "garage_state.json")) as stream:
            self.assertIn("50001", json.load(stream)["vehicles"])

    def test_a_restore_refuses_while_the_game_may_be_writing_the_save(self):
        record = self._slot_with_state()
        save_slots.backup_slot(
            record["id"], self._archive(), root=self.root)

        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.restore_slot(
                record["id"], self._archive(), root=self.root,
                is_running=lambda: True)

    def test_a_file_that_is_not_a_save_backup_is_refused_unchanged(self):
        record = self._slot_with_state()
        stranger = self._archive("stranger.zip")
        import zipfile

        with zipfile.ZipFile(stranger, "w") as archive:
            archive.writestr("notes.txt", "hello")

        with self.assertRaises(save_slots.SaveSlotError):
            save_slots.restore_slot(
                record["id"], stranger, root=self.root,
                is_running=lambda: False)
        with open(os.path.join(record["path"], "garage_state.json"),
                  encoding="utf-8") as stream:
            self.assertEqual(["50001"], list(json.load(stream)["vehicles"]))

    def test_a_member_with_a_path_never_leaves_the_save_folder(self):
        record = self._slot_with_state()
        hostile = self._archive("hostile.zip")
        import zipfile

        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr(
                "../garage_state.json", '{"schema": 7, "vehicles": {}}')
            archive.writestr("garage_state.json", '{"schema": 7}')

        result = save_slots.restore_slot(
            record["id"], hostile, root=self.root, is_running=lambda: False)

        self.assertEqual(["garage_state.json"], result["files"])
        self.assertFalse(os.path.isfile(
            os.path.join(os.path.dirname(record["path"]),
                         "garage_state.json")))

    def test_opening_a_save_folder_creates_it_and_asks_explorer_for_it(self):
        record = save_slots.create_slot(
            "Career", save_slots.MODE_NEW_ACCOUNT, root=self.root)
        asked = []

        directory = save_slots.open_slot_folder(
            record["id"], root=self.root,
            runner=lambda command, **unused: asked.append(command))

        self.assertEqual(record["path"], directory)
        self.assertEqual(1, len(asked))
        self.assertEqual("explorer.exe", asked[0][0])
        self.assertTrue(os.path.isdir(directory))
