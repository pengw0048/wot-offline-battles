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
