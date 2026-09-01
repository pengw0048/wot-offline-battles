"""Callback tests for the advanced vehicle editor window."""

import unittest

import vehicle_editor_ui


class _Widget(object):
    def __init__(self, master=None, **options):
        self.options = dict(options)
        self.children = []
        if master is not None and hasattr(master, "children"):
            master.children.append(self)

    def pack(self, **unused):
        pass

    def grid(self, **unused):
        pass

    def grid_columnconfigure(self, *unused, **unused_options):
        pass

    def bind(self, event, callback):
        self.options.setdefault("bindings", {})[event] = callback

    def config(self, **options):
        self.options.update(options)

    def cget(self, name):
        return self.options.get(name)

    def current(self, index=None):
        if index is None:
            return self.options.get("current", -1)
        values = self.options.get("values", ())
        self.options["current"] = index
        variable = self.options.get("textvariable")
        if variable is not None and 0 <= index < len(values):
            variable.set(values[index])


class _Root(_Widget):
    def title(self, title):
        self.options["title"] = title


class _StringVar(object):
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeTk(object):
    Toplevel = _Root
    Frame = _Widget
    Label = _Widget
    Entry = _Widget
    Button = _Widget
    StringVar = _StringVar


class _FakeTtk(object):
    Combobox = _Widget


class _MessageBox(object):
    def __init__(self, answer=True):
        self.answer = answer
        self.calls = []

    def askyesno(self, *args, **options):
        self.calls.append((args, options))
        return self.answer


class _ArmorViewer(object):
    instances = []

    def __init__(self, parent, tk_module, on_select, language, game_root):
        self.parent = parent
        self.on_select = on_select
        self.language = language
        self.game_root = game_root
        self.loaded = []
        self.focused = []
        self.updated = []
        self.reset_count = 0
        self.__class__.instances.append(self)

    def grid(self, *args, **options):
        self.grid_call = (args, options)

    def load_vehicle(self, member, records, focus_record=None):
        self.loaded.append((member, list(records), focus_record))

    def focus_field(self, record):
        self.focused.append(record)

    def update_field(self, member, field_path, value):
        self.updated.append((member, field_path, value))

    def reset_values(self):
        self.reset_count += 1


class _Service(object):
    class VehicleOverlayError(Exception):
        pass

    def __init__(self):
        self.inspect_calls = []
        self.catalog_calls = []
        self.topology_calls = []
        self.apply_calls = []
        self.restore_calls = []
        self.inspect_error = None
        self.apply_error = None
        self.restore_error = None
        self.conflict = ""
        self.current = "32"
        self.extra_fields = []
        self.choices = [
            {"nation": "ussr", "vehicle": "R11_MS-1",
             "label": "MS-1",
             "member": vehicle_editor_ui.DEFAULT_MEMBER},
            {"nation": "ussr", "vehicle": "R12_Test",
             "label": "Test",
             "member": "scripts/item_defs/vehicles/ussr/R12_Test.xml"},
            {"nation": "usa", "vehicle": "A01_T1_Cunningham",
             "label": "T1_Cunningham",
             "member": (
                 "scripts/item_defs/vehicles/usa/A01_T1_Cunningham.xml")},
        ]

    def list_vehicle_choices(self, game_root):
        self.catalog_calls.append(game_root)
        return list(self.choices)

    def list_vehicle_field_choices(self, game_root, member):
        self.topology_calls.append((game_root, member))
        if member.endswith("R12_Test.xml"):
            return [self._field(
                member, "speedLimits/forward", "Vehicle",
                "Speed limits / Forward speed", False, ("R12_Test",))] + list(
                    self.extra_fields)
        return [
            self._field(
                member, vehicle_editor_ui.DEFAULT_FIELD, "Vehicle",
                "Speed limits / Forward speed", False, ("R11_MS-1",)),
            self._field(
                "scripts/item_defs/vehicles/ussr/components/guns.xml",
                "shared/Gun-A/reloadTime", "Gun",
                "Gun-A / Reload time", True,
                ("R11_MS-1", "R12_Test"), original="2.5"),
        ] + list(self.extra_fields)

    @staticmethod
    def _field(member, field_path, category, field_label, shared, affected,
               original="32"):
        def display_id(vehicle):
            prefix, separator, remainder = vehicle.partition("_")
            if (separator and any(character.isdigit()
                                  for character in prefix)):
                return remainder
            return vehicle

        affected_labels = tuple(display_id(vehicle) for vehicle in affected)
        scope = ("Shared component; affects %s" % ", ".join(affected_labels)
                 if shared else "Affects this vehicle only.")
        return {
                "member": member,
                "fieldPath": field_path,
                "categoryLabel": category,
                "category": {
                    "Vehicle": "vehicle", "Chassis": "chassis",
                    "Turret": "turret", "Gun": "guns",
                }.get(category, category.lower()),
                "fieldLabel": field_label,
                "scope": scope,
                "shared": shared,
                "affectedVehicles": affected,
                "affectedVehicleLabels": affected_labels,
                "displayVehicle": display_id(affected[0]),
                "originalValue": original,
                "packedType": (
                    "string" if field_path.endswith("reloadTime")
                    else "integer"),
                "constraint": "positive",
            }

    def _result(self, member, field_path):
        return {
            "member": member,
            "fieldPath": field_path,
            "originalValue": "32",
            "currentValue": self.current,
            "packedType": "integer",
            "constraint": "stock parser requires a positive number",
            "overlayPath": "C:/WoT/mods/configs/offline_lan_0922/"
                           "vehicle_profiles.json",
            "conflict": self.conflict,
        }

    def inspect_profile_field(self, game_root, profile_name, member,
                              field_path):
        self.inspect_calls.append(
            (game_root, profile_name, member, field_path))
        if self.inspect_error:
            raise self.VehicleOverlayError(self.inspect_error)
        return self._result(member, field_path)

    def apply_profile_edit(self, game_root, profile_name, member, field_path,
                           replacement):
        self.apply_calls.append(
            (game_root, profile_name, member, field_path, replacement))
        if self.apply_error:
            raise self.VehicleOverlayError(self.apply_error)
        self.current = replacement
        return self._result(member, field_path)

    def clear_vehicle_profile(self, game_root, profile_name):
        self.restore_calls.append((game_root, profile_name))
        if self.restore_error:
            raise self.VehicleOverlayError(self.restore_error)
        self.current = "32"
        return 2


class VehicleEditorWindowTest(unittest.TestCase):
    def setUp(self):
        self.parent = _Root()
        self.service = _Service()
        self.messagebox = _MessageBox()
        self.log = []
        self.window = vehicle_editor_ui.VehicleEditorWindow(
            self.parent, "C:/WoT", "Fast MS-1", _FakeTk, _FakeTtk,
            self.messagebox,
            log=self.log.append, service=self.service)

    def test_opening_inspects_and_shows_the_original_contract(self):
        self.assertEqual(["C:/WoT"], self.service.catalog_calls)
        self.assertEqual(
            [("C:/WoT", vehicle_editor_ui.DEFAULT_MEMBER)],
            self.service.topology_calls)
        self.assertEqual(
            [("C:/WoT", "Fast MS-1", vehicle_editor_ui.DEFAULT_MEMBER,
              vehicle_editor_ui.DEFAULT_FIELD)],
            self.service.inspect_calls)
        self.assertEqual("32", self.window.original.get())
        self.assertEqual("32", self.window.replacement.get())
        self.assertEqual("integer", self.window.packed_type.get())
        self.assertIn("positive", self.window.constraint.get())
        self.assertIn("vehicle_profiles.json",
                      self.window.overlay_path.get())
        self.assertEqual(("usa", "ussr"),
                         self.window.nation_box.cget("values"))
        self.assertEqual(("MS-1", "Test"),
                         self.window.vehicle_box.cget("values"))
        self.assertEqual("normal", self.window.vehicle_box.cget("state"))
        self.assertEqual("readonly", self.window.nation_box.cget("state"))
        self.assertEqual("readonly", self.window.category_box.cget("state"))
        self.assertEqual("readonly", self.window.field_box.cget("state"))
        self.assertFalse(hasattr(self.window, "inspect_button"))
        self.assertEqual(("Vehicle", "Gun"),
                         self.window.category_box.cget("values"))
        self.assertEqual(
            ("Speed limits / Forward speed",),
            self.window.field_box.cget("values"))

    def test_selecting_a_category_loads_its_discovered_safe_fields(self):
        self.window.category.set("Gun")

        self.assertTrue(self.window.refresh_fields())

        self.assertEqual("Gun-A / Reload time", self.window.field.get())
        self.assertEqual(
            ("Gun-A / Reload time",),
            self.window.field_box.cget("values"))
        self.assertIn("MS-1, Test", self.window.scope.get())
        self.assertEqual(
            "scripts/item_defs/vehicles/ussr/components/guns.xml",
            self.window.member.get())
        self.assertEqual("shared/Gun-A/reloadTime",
                         self.window.field_path.get())

    def test_armor_viewer_click_selects_exact_field_and_saved_value_recolors(self):
        service = _Service()
        turret_path = "turrets0/T-18_mod/armor/armor_1"
        service.extra_fields = [
            service._field(
                vehicle_editor_ui.DEFAULT_MEMBER,
                "hull/armor/armor_1", "Vehicle",
                "Hull / Armor thickness (armor_1)", False,
                ("R11_MS-1",), original="16"),
            service._field(
                vehicle_editor_ui.DEFAULT_MEMBER,
                turret_path, "Turret",
                "T-18_mod / Armor thickness (armor_1)", False,
                ("R11_MS-1",), original="35"),
        ]
        window = vehicle_editor_ui.VehicleEditorWindow(
            self.parent, "C:/WoT", "Fast MS-1", _FakeTk, _FakeTtk,
            self.messagebox, service=service,
            armor_viewer_factory=_ArmorViewer)
        armor_viewer = _ArmorViewer.instances[-1]

        self.assertEqual(vehicle_editor_ui.DEFAULT_MEMBER,
                         armor_viewer.loaded[-1][0])
        self.assertTrue(window.select_field_from_viewer(
            (vehicle_editor_ui.DEFAULT_MEMBER, turret_path)))
        self.assertEqual("Turret", window.category.get())
        self.assertEqual(
            "T-18_mod / Armor thickness (armor_1)", window.field.get())
        self.assertEqual(turret_path, window.field_path.get())

        window.replacement.set("75")
        self.assertTrue(window.apply())
        self.assertEqual(
            (vehicle_editor_ui.DEFAULT_MEMBER, turret_path, "75"),
            armor_viewer.updated[-1])

        self.assertTrue(window.restore_defaults())
        self.assertEqual(1, armor_viewer.reset_count)

    def test_selecting_a_nation_filters_the_vehicle_list(self):
        self.window.nation.set("usa")

        self.assertTrue(self.window.refresh_vehicles())

        self.assertEqual(("T1_Cunningham",),
                         self.window.vehicle_box.cget("values"))
        self.assertEqual("T1_Cunningham", self.window.vehicle.get())

    def test_typing_filters_without_loading_until_enter_is_pressed(self):
        topology_count = len(self.service.topology_calls)
        self.window.vehicle.set("tes")

        self.assertTrue(self.window.filter_vehicles())

        self.assertEqual(("Test",), self.window.vehicle_box.cget("values"))
        self.assertEqual(topology_count, len(self.service.topology_calls))
        self.assertEqual("disabled", self.window.apply_button.cget("state"))

        self.assertTrue(self.window.commit_vehicle_search())

        self.assertEqual("Test", self.window.vehicle.get())
        self.assertEqual(
            ("C:/WoT",
             "scripts/item_defs/vehicles/ussr/R12_Test.xml"),
            self.service.topology_calls[-1])
        self.assertEqual(("MS-1", "Test"),
                         self.window.vehicle_box.cget("values"))

    def test_no_search_match_can_restore_the_loaded_vehicle(self):
        self.window.vehicle.set("not a tank")

        self.assertFalse(self.window.filter_vehicles())

        self.assertEqual((), self.window.vehicle_box.cget("values"))
        self.assertIn("No vehicles match", self.window.status.get())

        self.assertTrue(self.window.restore_vehicle_selection())

        self.assertEqual("MS-1", self.window.vehicle.get())
        self.assertEqual(("MS-1", "Test"),
                         self.window.vehicle_box.cget("values"))
        self.assertEqual("normal", self.window.apply_button.cget("state"))

    def test_search_uses_hidden_internal_name_but_does_not_display_it(self):
        service = _Service()
        service.choices[1]["label"] = "Experimental tank (Test)"
        window = vehicle_editor_ui.VehicleEditorWindow(
            self.parent, "C:/WoT", "Fast MS-1", _FakeTk, _FakeTtk,
            self.messagebox, service=service)

        self.assertEqual(("Experimental tank", "MS-1"),
                         window.vehicle_box.cget("values"))
        window.vehicle.set("R12_Test")

        self.assertTrue(window.filter_vehicles())
        self.assertEqual(("Experimental tank",),
                         window.vehicle_box.cget("values"))
        self.assertTrue(window.commit_vehicle_search())
        self.assertEqual("Experimental tank", window.vehicle.get())

    def test_dropdown_selects_the_exact_duplicate_label_by_index(self):
        service = _Service()
        service.choices[1]["label"] = "Duplicate"
        service.choices.append({
            "nation": "ussr",
            "vehicle": "R13_Duplicate",
            "label": "Duplicate",
            "member": (
                "scripts/item_defs/vehicles/ussr/R13_Duplicate.xml"),
        })
        window = vehicle_editor_ui.VehicleEditorWindow(
            self.parent, "C:/WoT", "Fast MS-1", _FakeTk, _FakeTtk,
            self.messagebox, service=service)
        window.vehicle.set("dup")
        self.assertTrue(window.filter_vehicles())
        self.assertEqual(("Duplicate", "Duplicate"),
                         window.vehicle_box.cget("values"))

        window.vehicle_box.current(1)
        self.assertTrue(window.select_vehicle_from_dropdown())

        self.assertEqual(
            ("C:/WoT",
             "scripts/item_defs/vehicles/ussr/R13_Duplicate.xml"),
            service.topology_calls[-1])

    def test_localized_vehicle_label_selects_the_exact_internal_vehicle(self):
        service = _Service()
        service.choices[0]["label"] = "MS-1"

        window = vehicle_editor_ui.VehicleEditorWindow(
            self.parent, "C:/WoT", "Fast MS-1", _FakeTk, _FakeTtk,
            self.messagebox, service=service)

        self.assertEqual(
            ("MS-1", "Test"),
            window.vehicle_box.cget("values"))
        self.assertEqual("MS-1", window.vehicle.get())
        self.assertEqual(
            ("C:/WoT", vehicle_editor_ui.DEFAULT_MEMBER),
            service.topology_calls[-1])

    def test_inspect_disables_apply_and_shows_a_conflict(self):
        self.service.conflict = (
            "Conflict: another tool owns this complete member.")

        self.assertTrue(self.window.inspect())

        self.assertEqual("disabled", self.window.apply_button.cget("state"))
        self.assertEqual(self.service.conflict, self.window.status.get())

    def test_invalid_field_shows_the_validation_error(self):
        self.service.inspect_error = "This field is not in the allowlist."

        self.assertFalse(self.window.inspect())

        self.assertIn("Validation error", self.window.status.get())
        self.assertEqual("disabled", self.window.apply_button.cget("state"))

    def test_apply_passes_the_visible_selection_and_reports_success(self):
        self.window.replacement.set("40")

        self.assertTrue(self.window.apply())

        self.assertEqual(
            [("C:/WoT", "Fast MS-1", vehicle_editor_ui.DEFAULT_MEMBER,
              vehicle_editor_ui.DEFAULT_FIELD, "40")],
            self.service.apply_calls)
        self.assertEqual("40", self.window.current.get())
        self.assertIn("reparsed successfully", self.window.status.get())
        self.assertIn("reparsed successfully", self.log[-1])

    def test_apply_reports_the_game_running_refusal(self):
        self.service.apply_error = (
            "Close World of Tanks before changing vehicle data.")

        self.assertFalse(self.window.apply())

        self.assertIn("Close World of Tanks", self.window.status.get())

    def test_restore_requires_confirmation(self):
        self.messagebox.answer = False

        self.assertFalse(self.window.restore_defaults())

        self.assertEqual([], self.service.restore_calls)
        self.assertIn("cancelled", self.window.status.get())

    def test_restore_removes_only_owned_members_and_reinspects(self):
        self.service.current = "40"

        self.assertTrue(self.window.restore_defaults())

        self.assertEqual(
            [("C:/WoT", "Fast MS-1")], self.service.restore_calls)
        self.assertEqual("32", self.window.current.get())
        self.assertIn("Fast MS-1", self.log[-1])
        self.assertEqual(2, len(self.service.inspect_calls))

    def test_chinese_language_localizes_the_editor_and_discovered_fields(self):
        service = _Service()
        messagebox = _MessageBox(answer=False)
        window = vehicle_editor_ui.VehicleEditorWindow(
            self.parent, "C:/WoT", "Fast MS-1", _FakeTk, _FakeTtk,
            messagebox, service=service, language="zh")

        self.assertEqual(
            "0.9.22 车辆属性方案：Fast MS-1",
            window.root.cget("title"))
        self.assertFalse(hasattr(window, "inspect_button"))
        self.assertEqual(("车辆", "火炮"),
                         window.category_box.cget("values"))
        self.assertEqual(("速度限制 / 前进速度",),
                         window.field_box.cget("values"))
        self.assertEqual("整数", window.packed_type.get())
        self.assertIn("正数", window.constraint.get())
        self.assertIn("只影响该车", window.scope.get())
        self.assertIn("可以安全修改", window.status.get())
        self.assertEqual(
            "Shell-A / HE 溅射范围",
            window._field_label("Shell-A / Explosion radius"))
        self.assertEqual(
            "Shell-A / Explosion radius",
            self.window._field_label("Shell-A / Explosion radius"))
        self.assertEqual(
            "Shell-A / 伤害 / 模块伤害",
            window._field_label("Shell-A / Damage / Module damage"))
        self.assertEqual(
            "Shell-A / Damage / Module damage",
            self.window._field_label("Shell-A / Damage / Module damage"))
        self.assertEqual(
            "弹夹短装填射速（越高越短）",
            window._field_label(
                "Magazine firing rate (higher is a shorter reload)"))
        self.assertEqual(
            "基础精度", window._field_label("Base accuracy"))
        self.assertEqual(
            "履带地形阻力（硬地 / 中地 / 软地，越低越好）",
            window._field_label(
                "Ground resistance (hard, medium, soft; lower is better)"))
        self.assertEqual(
            "车体转向速度（度/秒）",
            window._field_label("Hull traverse speed (deg/s)"))
        self.assertEqual(
            "弹夹 / 弹夹容量（发）",
            window._field_label("Magazine / Rounds per magazine"))

        self.assertFalse(window.restore_defaults())
        self.assertIn("清除", messagebox.calls[-1][0][0])
        self.assertIn("已取消", window.status.get())


if __name__ == "__main__":
    unittest.main()
