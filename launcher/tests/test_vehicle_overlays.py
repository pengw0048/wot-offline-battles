import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

import core
import vehicle_overlays

packed = vehicle_overlays.packed_xml


def scalar(value_type, value):
    return packed.PackedValue(value_type, value)


def element(children):
    return packed.PackedValue(
        packed.TYPE_ELEMENT, packed.PackedElement(children=children))


def valued_element(value_type, value, children):
    return packed.PackedValue(
        packed.TYPE_ELEMENT,
        packed.PackedElement(value=scalar(value_type, value),
                             children=children))


def child(parent, name):
    encoded = name.encode("utf-8")
    return next(value for current, value in parent.children
                if current == encoded)


class VehicleOverlayTest(unittest.TestCase):
    LIST = "scripts/item_defs/vehicles/ussr/list.xml"
    VEHICLE = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml"
    VEHICLE_TWO = "scripts/item_defs/vehicles/ussr/R12_Test.xml"
    SIEGE_VEHICLE = "scripts/item_defs/vehicles/ussr/R13_Siege.xml"
    SIEGE_MODE = (
        "scripts/item_defs/vehicles/ussr/R13_Siege_siege_mode.xml")
    OBSERVER = "scripts/item_defs/vehicles/ussr/Observer.xml"
    ENGINES = "scripts/item_defs/vehicles/ussr/components/engines.xml"
    GUNS = "scripts/item_defs/vehicles/ussr/components/guns.xml"
    RADIOS = "scripts/item_defs/vehicles/ussr/components/radios.xml"
    SHELLS = "scripts/item_defs/vehicles/ussr/components/shells.xml"

    def setUp(self):
        environment = mock.patch.dict(os.environ, {"APPDATA": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.game, True)
        self._write(core.GAME_EXECUTABLE, b"")
        self._write(
            "version.xml", b"<version> v.0.9.22.0.1 #1513 </version>")
        self.members = self._members()
        self._write_package()

    def _write(self, relative_path, data):
        path = os.path.join(self.game, *relative_path.split("/"))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "wb") as stream:
            stream.write(data)
        return path

    def _write_package(self):
        path = os.path.join(
            self.game, *vehicle_overlays.SOURCE_PACKAGE.split("/"))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in sorted(self.members.items()):
                archive.writestr(name, data)
        return path

    def _members(self):
        speed_limits = packed.PackedElement(children=[
            (b"forward", scalar(packed.TYPE_INTEGER, 32)),
            (b"backward", scalar(packed.TYPE_INTEGER, 8)),
        ])
        chassis_record = packed.PackedElement(children=[
            (b"weight", scalar(packed.TYPE_INTEGER, 1200)),
            (b"maxLoad", scalar(packed.TYPE_INTEGER, 6500)),
            (b"terrainResistance", scalar(
                packed.TYPE_STRING, b"1 1.2 2.1")),
            (b"rotationSpeed", scalar(packed.TYPE_INTEGER, 38)),
            (b"shotDispersionFactors", element([
                (b"vehicleMovement", scalar(
                    packed.TYPE_STRING, b"0.12")),
                (b"vehicleRotation", scalar(
                    packed.TYPE_STRING, b"0.08")),
            ])),
            (b"armor", element([
                (b"leftTrack", scalar(packed.TYPE_INTEGER, 15)),
                (b"rightTrack", scalar(packed.TYPE_INTEGER, 15)),
            ])),
            (b"maxHealth", scalar(packed.TYPE_INTEGER, 50)),
            (b"maxRegenHealth", scalar(packed.TYPE_INTEGER, 40)),
            (b"resource", scalar(
                packed.TYPE_STRING, b"vehicles/forbidden.model")),
        ])
        vehicle = packed.PackedElement(children=[
            (b"speedLimits", element(speed_limits.children)),
            (b"invisibility", element([
                (b"moving", scalar(packed.TYPE_STRING, b"0.16")),
                (b"still", scalar(packed.TYPE_STRING, b"0.22")),
            ])),
            (b"chassis", element([
                (b"T-18Bis", element(chassis_record.children)),
            ])),
            (b"turrets0", element([
                (b"T-18_mod", element([
                    (b"weight", scalar(packed.TYPE_INTEGER, 700)),
                    (b"rotationSpeed", scalar(packed.TYPE_INTEGER, 44)),
                    (b"circularVisionRadius", scalar(
                        packed.TYPE_INTEGER, 320)),
                    (b"armor", element([
                        (b"armor_1", scalar(packed.TYPE_INTEGER, 35)),
                        (b"armor_2", element([
                            (b"vehicleDamageFactor", scalar(
                                packed.TYPE_STRING, b"0.0")),
                        ])),
                    ])),
                    (b"guns", element([
                        (b"Gun-A", element([])),
                    ])),
                ])),
            ])),
            (b"engines", element([
                (b"GAZ-M1", scalar(packed.TYPE_STRING, b"shared")),
            ])),
        ])

        engine_record = packed.PackedElement(children=[
            (b"power", scalar(packed.TYPE_INTEGER, 90)),
            (b"weight", scalar(packed.TYPE_INTEGER, 300)),
            (b"maxHealth", scalar(packed.TYPE_INTEGER, 40)),
            (b"maxRegenHealth", scalar(packed.TYPE_INTEGER, 20)),
            (b"tags", scalar(packed.TYPE_COMPRESSED_STRING, b"\x81\x01")),
        ])
        engines = packed.PackedElement(children=[
            (b"ids", element([
                (b"GAZ-M1", scalar(packed.TYPE_INTEGER, 15)),
            ])),
            (b"shared", element([
                (b"GAZ-M1", element(engine_record.children)),
            ])),
        ])

        shot = packed.PackedElement(children=[
            (b"speed", scalar(packed.TYPE_INTEGER, 825)),
            (b"gravity", scalar(packed.TYPE_STRING, b"9.81")),
            (b"maxDistance", scalar(packed.TYPE_INTEGER, 400)),
            (b"piercingPower", scalar(packed.TYPE_STRING, b"22 18")),
        ])
        gun = packed.PackedElement(children=[
            (b"rotationSpeed", scalar(packed.TYPE_STRING, b"35.5")),
            (b"pitchLimits", element([
                (b"minPitch", scalar(
                    packed.TYPE_STRING, b"0 -10 1 -10")),
                (b"maxPitch", scalar(
                    packed.TYPE_STRING, b"0 20 1 20")),
            ])),
            (b"turretYawLimits", scalar(
                packed.TYPE_STRING, b"-180 180")),
            (b"armor", element([
                (b"gun", scalar(packed.TYPE_INTEGER, 20)),
            ])),
            (b"reloadTime", scalar(packed.TYPE_STRING, b"2.5")),
            (b"clip", element([
                (b"count", scalar(packed.TYPE_INTEGER, 4)),
                (b"rate", scalar(packed.TYPE_STRING, b"30")),
            ])),
            (b"burst", element([
                (b"count", scalar(packed.TYPE_INTEGER, 2)),
                (b"rate", scalar(packed.TYPE_STRING, b"600")),
            ])),
            (b"aimingTime", scalar(packed.TYPE_STRING, b"1.9")),
            (b"shotDispersionRadius", scalar(
                packed.TYPE_STRING, b"0.42")),
            (b"shotDispersionFactors", element([
                (b"turretRotation", scalar(
                    packed.TYPE_STRING, b"0.09")),
                (b"afterShot", scalar(packed.TYPE_STRING, b"2.4")),
            ])),
            (b"invisibilityFactorAtShot", scalar(
                packed.TYPE_STRING, b"0.35")),
            (b"weight", scalar(packed.TYPE_INTEGER, 200)),
            (b"maxAmmo", scalar(packed.TYPE_INTEGER, 45)),
            (b"shots", element([
                (b"Shell-A", element(shot.children)),
            ])),
        ])
        guns = packed.PackedElement(children=[
            (b"shared", element([
                (b"Gun-A", element(gun.children)),
            ])),
        ])

        shell_record = packed.PackedElement(children=[
            (b"id", scalar(packed.TYPE_INTEGER, 1)),
            (b"caliber", scalar(packed.TYPE_INTEGER, 20)),
            (b"explosionRadius", scalar(packed.TYPE_STRING, b"2.42")),
            (b"damage", element([
                (b"armor", scalar(packed.TYPE_INTEGER, 10)),
                (b"devices", scalar(packed.TYPE_INTEGER, 27)),
            ])),
            (b"effects", scalar(
                packed.TYPE_COMPRESSED_STRING, b"\x81\x99\x02")),
        ])
        shells = packed.PackedElement(children=[
            (b"Shell-A", element(shell_record.children)),
        ])
        roster = packed.PackedElement(children=[
            (b"xmlns:xmlref", scalar(
                packed.TYPE_STRING, b"http://www.w3.org/2001/XInclude")),
            (b"R11_MS-1", element([
                (b"userString", scalar(
                    packed.TYPE_STRING, b"#ussr_vehicles:R11_MS-1")),
                (b"shortUserString", scalar(
                    packed.TYPE_STRING, b"#ussr_vehicles:R11_MS-1_short")),
                (b"tags", scalar(packed.TYPE_STRING, b"lightTank")),
            ])),
            (b"R12_Test", element([
                (b"tags", scalar(
                    packed.TYPE_STRING, b"secret lightTank")),
            ])),
            (b"Observer", element([
                (b"tags", scalar(
                    packed.TYPE_STRING, b"secret observer lightTank")),
            ])),
        ])
        return {
            self.LIST: packed.write_packed_xml(roster),
            self.VEHICLE: packed.write_packed_xml(vehicle),
            self.VEHICLE_TWO: packed.write_packed_xml(vehicle),
            self.OBSERVER: packed.write_packed_xml(vehicle),
            self.ENGINES: packed.write_packed_xml(engines),
            self.GUNS: packed.write_packed_xml(guns),
            self.SHELLS: packed.write_packed_xml(shells),
        }

    def _install_siege_pair(self):
        normal = packed.read_packed_xml(self.members[self.VEHICLE])
        normal.children.append((b"hull", element([
            (b"weight", scalar(packed.TYPE_INTEGER, 2000)),
            (b"armor", element([
                (b"armor_1", scalar(packed.TYPE_INTEGER, 40)),
            ])),
        ])))
        normal_gun = child(child(child(
            child(normal, "turrets0").value,
            "T-18_mod").value, "guns").value, "Gun-A")
        normal_gun.value.children.extend((
            (b"pitchLimits", element([
                (b"minPitch", scalar(
                    packed.TYPE_STRING, b"0 -1 1 -1")),
                (b"maxPitch", scalar(
                    packed.TYPE_STRING, b"0 -1 1 -1")),
            ])),
            (b"turretYawLimits", scalar(
                packed.TYPE_STRING, b"-3 3")),
        ))
        siege = packed.read_packed_xml(packed.write_packed_xml(normal))
        child(child(siege, "speedLimits").value, "forward").value = 8
        child(child(siege, "speedLimits").value, "backward").value = 8
        siege_gun = child(child(child(
            child(siege, "turrets0").value,
            "T-18_mod").value, "guns").value, "Gun-A")
        pitch = child(siege_gun.value, "pitchLimits").value
        child(pitch, "minPitch").value = b"0 -4 1 -4"
        child(pitch, "maxPitch").value = b"0 2 1 2"

        roster = packed.read_packed_xml(self.members[self.LIST])
        roster.children.append((b"R13_Siege", element([
            (b"tags", scalar(
                packed.TYPE_STRING, b"AT-SPG siegeMode")),
        ])))
        self.members[self.LIST] = packed.write_packed_xml(roster)
        self.members[self.SIEGE_VEHICLE] = packed.write_packed_xml(normal)
        self.members[self.SIEGE_MODE] = packed.write_packed_xml(siege)
        self._write_package()

    def _overlay(self, member):
        return os.path.join(
            self.game, *vehicle_overlays.OVERLAY_ROOT.split("/"),
            *member.split("/"))

    def _root(self, member):
        with open(self._overlay(member), "rb") as stream:
            return packed.read_packed_xml(stream.read())

    def _value(self, member, field_path):
        return vehicle_overlays._find_value(
            self._root(member), field_path).value

    def test_inspect_shows_exact_path_original_type_and_constraint(self):
        result = vehicle_overlays.inspect_vehicle_field(
            self.game, self.VEHICLE, "speedLimits/forward")

        self.assertEqual("32", result["originalValue"])
        self.assertEqual("32", result["currentValue"])
        self.assertEqual("integer", result["packedType"])
        self.assertIn("positive", result["constraint"])
        self.assertEqual(self._overlay(self.VEHICLE), result["overlayPath"])
        self.assertEqual("", result["conflict"])

    def test_apply_writes_only_res_mods_and_records_complete_member_ownership(self):
        package_path = self._write_package()
        with open(package_path, "rb") as stream:
            package_before = stream.read()

        result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)

        self.assertEqual("40", result["currentValue"])
        self.assertEqual(40, self._value(
            self.VEHICLE, "speedLimits/forward"))
        self.assertEqual(packed.TYPE_INTEGER, vehicle_overlays._find_value(
            self._root(self.VEHICLE), "speedLimits/forward").value_type)
        with open(package_path, "rb") as stream:
            self.assertEqual(package_before, stream.read())
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            manifest = json.load(stream)
        self.assertEqual(1, manifest["schema"])
        self.assertEqual(self.VEHICLE,
                         manifest["members"][0]["sourceMember"])
        self.assertEqual(self.VEHICLE,
                         manifest["members"][0]["overlayRelativePath"])
        edit = manifest["members"][0]["edits"][0]
        self.assertEqual("integer", edit["originalPackedType"])
        self.assertEqual(32, edit["originalValue"])
        self.assertEqual(40, edit["replacementValue"])

    def test_later_edits_rebuild_the_member_from_original_and_merge_all_edits(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/backward", "12",
            is_running=lambda: False)

        self.assertEqual(40, self._value(
            self.VEHICLE, "speedLimits/forward"))
        self.assertEqual(12, self._value(
            self.VEHICLE, "speedLimits/backward"))
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            manifest = json.load(stream)
        self.assertEqual(2, len(manifest["members"][0]["edits"]))

    def test_rebuilding_one_member_preserves_unedited_compressed_strings(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.ENGINES, "shared/GAZ-M1/power", "100",
            is_running=lambda: False)

        tags = vehicle_overlays._find_value(
            self._root(self.ENGINES), "shared/GAZ-M1/tags")
        self.assertEqual(packed.TYPE_COMPRESSED_STRING, tags.value_type)
        self.assertEqual(b"\x81\x01", tags.value)

    def test_existing_overlay_without_this_manifest_is_a_conflict(self):
        self._write(
            "/".join((vehicle_overlays.OVERLAY_ROOT, self.VEHICLE)),
            b"another tool")

        result = vehicle_overlays.inspect_vehicle_field(
            self.game, self.VEHICLE, "speedLimits/forward")
        self.assertIn("Conflict", result["conflict"])
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "not owned"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/forward", "40",
                is_running=lambda: False)
        with open(self._overlay(self.VEHICLE), "rb") as stream:
            self.assertEqual(b"another tool", stream.read())

    def test_member_and_field_browsers_expose_only_existing_safe_contracts(self):
        self.assertEqual(
            sorted(self.members),
            vehicle_overlays.list_vehicle_members(self.game))

        gun_fields = vehicle_overlays.list_editable_fields(
            self.game, self.GUNS)
        paths = [record["fieldPath"] for record in gun_fields]
        self.assertIn("shared/Gun-A/reloadTime", paths)
        self.assertIn(
            "shared/Gun-A/shots/Shell-A/piercingPower", paths)
        self.assertIn("shared/Gun-A/pitchLimits/minPitch", paths)
        self.assertIn("shared/Gun-A/pitchLimits/maxPitch", paths)
        self.assertIn("shared/Gun-A/turretYawLimits", paths)
        self.assertIn("shared/Gun-A/armor/gun", paths)
        penetration = next(
            record for record in gun_fields
            if record["fieldPath"].endswith("piercingPower"))
        self.assertEqual("22 18", penetration["originalValue"])
        self.assertEqual("string", penetration["packedType"])
        self.assertIn("exactly two", penetration["constraint"])

        shell_fields = vehicle_overlays.list_editable_fields(
            self.game, self.SHELLS)
        explosion_radius = next(
            record for record in shell_fields
            if record["fieldPath"] == "Shell-A/explosionRadius")
        module_damage = next(
            record for record in shell_fields
            if record["fieldPath"] == "Shell-A/damage/devices")
        self.assertEqual("2.42", explosion_radius["originalValue"])
        self.assertIn("positive", explosion_radius["constraint"])
        self.assertEqual("27", module_damage["originalValue"])
        self.assertIn("positive", module_damage["constraint"])

        engine_paths = [
            record["fieldPath"] for record in
            vehicle_overlays.list_editable_fields(self.game, self.ENGINES)]
        self.assertNotIn("shared/GAZ-M1/tags", engine_paths)
        self.assertNotIn("ids/GAZ-M1", engine_paths)

    def test_fire_control_vision_and_camouflage_fields_are_editable(self):
        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)
        records = dict((record["fieldPath"], record) for record in fields)
        expected = {
            "invisibility/moving": "Vehicle",
            "invisibility/still": "Vehicle",
            "chassis/T-18Bis/shotDispersionFactors/vehicleMovement": "Chassis",
            "chassis/T-18Bis/shotDispersionFactors/vehicleRotation": "Chassis",
            "chassis/T-18Bis/terrainResistance": "Chassis",
            "chassis/T-18Bis/rotationSpeed": "Chassis",
            "turrets0/T-18_mod/circularVisionRadius": "Turret",
            "turrets0/T-18_mod/rotationSpeed": "Turret",
            "shared/Gun-A/clip/count": "Gun",
            "shared/Gun-A/clip/rate": "Gun",
            "shared/Gun-A/shotDispersionRadius": "Gun",
            "shared/Gun-A/shotDispersionFactors/turretRotation": "Gun",
            "shared/Gun-A/shotDispersionFactors/afterShot": "Gun",
            "shared/Gun-A/invisibilityFactorAtShot": "Gun",
        }
        self.assertEqual(set(expected), set(records) & set(expected))
        for field_path, category in expected.items():
            self.assertEqual(category, records[field_path]["categoryLabel"])
        self.assertEqual("30", records["shared/Gun-A/clip/rate"]["originalValue"])
        self.assertEqual(
            "4", records["shared/Gun-A/clip/count"]["originalValue"])
        self.assertEqual(
            "integer", records["shared/Gun-A/clip/count"]["packedType"])
        self.assertEqual(
            "1 1.2 2.1",
            records["chassis/T-18Bis/terrainResistance"]["originalValue"])
        self.assertIn(
            "hard, medium, soft",
            records["chassis/T-18Bis/terrainResistance"]["fieldLabel"])
        self.assertIn(
            "Hull traverse speed",
            records["chassis/T-18Bis/rotationSpeed"]["fieldLabel"])
        self.assertIn(
            "Turret traverse speed",
            records["turrets0/T-18_mod/rotationSpeed"]["fieldLabel"])
        self.assertIn(
            "Gun elevation speed",
            records["shared/Gun-A/rotationSpeed"]["fieldLabel"])
        self.assertIn(
            "Elevation curve",
            records["shared/Gun-A/pitchLimits/minPitch"]["fieldLabel"])
        self.assertIn(
            "Depression curve",
            records["shared/Gun-A/pitchLimits/maxPitch"]["fieldLabel"])
        self.assertIn(
            "shorter reload",
            records["shared/Gun-A/clip/rate"]["fieldLabel"])

        vehicle_overlays.apply_vehicle_edit(
            self.game, self.GUNS, "shared/Gun-A/clip/rate", "45",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE,
            "chassis/T-18Bis/shotDispersionFactors/vehicleMovement", "0.2",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE,
            "chassis/T-18Bis/terrainResistance", "0.9 1.1 1.8",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE,
            "chassis/T-18Bis/rotationSpeed", "42",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.GUNS, "shared/Gun-A/clip/count", "6",
            is_running=lambda: False)
        self.assertEqual(
            b"45", self._value(self.GUNS, "shared/Gun-A/clip/rate"))
        self.assertEqual(
            b"0.2", self._value(
                self.VEHICLE,
                "chassis/T-18Bis/shotDispersionFactors/vehicleMovement"))
        self.assertEqual(
            b"0.9 1.1 1.8", self._value(
                self.VEHICLE,
                "chassis/T-18Bis/terrainResistance"))
        self.assertEqual(
            42, self._value(
                self.VEHICLE, "chassis/T-18Bis/rotationSpeed"))
        self.assertEqual(
            6, self._value(self.GUNS, "shared/Gun-A/clip/count"))

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "positive"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.GUNS, "shared/Gun-A/clip/rate", "0",
                is_running=lambda: False)
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "non-negative"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE,
                "chassis/T-18Bis/shotDispersionFactors/vehicleRotation", "-0.1",
                is_running=lambda: False)

        for replacement in ("1 2", "1 2 3 4", "1 0 2", "1 nan 2"):
            with self.assertRaises(vehicle_overlays.VehicleOverlayError):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, self.VEHICLE,
                    "chassis/T-18Bis/terrainResistance", replacement,
                    is_running=lambda: False)
        decimal_result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE,
            "chassis/T-18Bis/rotationSpeed", "40.5",
            is_running=lambda: False)
        decimal_rotation = vehicle_overlays._find_value(
            self._root(self.VEHICLE),
            "chassis/T-18Bis/rotationSpeed")
        self.assertEqual(packed.TYPE_STRING, decimal_rotation.value_type)
        self.assertEqual(b"40.5", decimal_rotation.value)
        self.assertEqual("40.5", decimal_result["currentValue"])
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.GUNS, "shared/Gun-A/reloadTime", "3",
            is_running=lambda: False)
        rebuilt_rotation = vehicle_overlays._find_value(
            self._root(self.VEHICLE),
            "chassis/T-18Bis/rotationSpeed")
        self.assertEqual(packed.TYPE_STRING, rebuilt_rotation.value_type)
        self.assertEqual(b"40.5", rebuilt_rotation.value)
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "burst/count"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.GUNS, "shared/Gun-A/clip/count", "1",
                is_running=lambda: False)

    def test_armor_angles_and_component_weights_preserve_stock_types(self):
        vehicle_root = packed.read_packed_xml(self.members[self.VEHICLE])
        vehicle_root.children.extend((
            (b"hull", element([
                (b"weight", scalar(packed.TYPE_STRING, b"1650.5")),
                (b"armor", element([
                    (b"armor_1", scalar(packed.TYPE_STRING, b"16.5")),
                ])),
            ])),
            (b"hull_aiming", element([
                (b"pitch", element([
                    (b"wheelsCorrectionAngles", element([
                        (b"pitchMin", scalar(packed.TYPE_INTEGER, -11)),
                        (b"pitchMax", scalar(packed.TYPE_INTEGER, 11)),
                    ])),
                ])),
            ])),
        ))
        self.members[self.VEHICLE] = packed.write_packed_xml(vehicle_root)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)
        paths = {record["fieldPath"]: record for record in fields}
        expected = (
            "hull/weight", "hull/armor/armor_1",
            "chassis/T-18Bis/armor/leftTrack",
            "turrets0/T-18_mod/weight",
            "turrets0/T-18_mod/armor/armor_1",
            "shared/Gun-A/armor/gun",
            "shared/Gun-A/pitchLimits/minPitch",
            "shared/Gun-A/pitchLimits/maxPitch",
            "shared/Gun-A/turretYawLimits",
            "hull_aiming/pitch/wheelsCorrectionAngles/pitchMin",
            "hull_aiming/pitch/wheelsCorrectionAngles/pitchMax",
        )
        for field_path in expected:
            self.assertIn(field_path, paths)
        self.assertNotIn(
            "turrets0/T-18_mod/armor/armor_2/vehicleDamageFactor", paths)
        self.assertIn(
            "Armor thickness (armor_1)",
            paths["hull/armor/armor_1"]["fieldLabel"])

        result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.GUNS,
            "shared/Gun-A/pitchLimits/minPitch", "-12.5",
            is_running=lambda: False)
        self.assertEqual("0 -12.5 1 -12.5", result["currentValue"])
        value = vehicle_overlays._find_value(
            self._root(self.GUNS),
            "shared/Gun-A/pitchLimits/minPitch")
        self.assertEqual(packed.TYPE_STRING, value.value_type)
        self.assertEqual(b"0 -12.5 1 -12.5", value.value)

        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "hull/armor/armor_1", "22.25",
            is_running=lambda: False)
        armor = vehicle_overlays._find_value(
            self._root(self.VEHICLE), "hull/armor/armor_1")
        self.assertEqual(packed.TYPE_STRING, armor.value_type)
        self.assertEqual(b"22.25", armor.value)

    def test_armor_element_self_value_is_editable_without_losing_children(self):
        vehicle_root = packed.read_packed_xml(self.members[self.VEHICLE])
        turret = child(child(vehicle_root, "turrets0").value, "T-18_mod")
        armor = child(turret.value, "armor")
        armor.value.children[1] = (
            b"armor_2",
            valued_element(
                packed.TYPE_INTEGER, 45,
                [(b"vehicleDamageFactor",
                  scalar(packed.TYPE_STRING, b"0.0"))]))
        self.members[self.VEHICLE] = packed.write_packed_xml(vehicle_root)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)
        paths = {record["fieldPath"]: record for record in fields}
        field_path = "turrets0/T-18_mod/armor/armor_2"
        self.assertEqual("45", paths[field_path]["originalValue"])

        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, field_path, "52.5",
            is_running=lambda: False)
        rebuilt = self._root(self.VEHICLE)
        value = vehicle_overlays._find_value(rebuilt, field_path)
        self.assertEqual(packed.TYPE_STRING, value.value_type)
        self.assertEqual(b"52.5", value.value)
        factor = vehicle_overlays._find_value(
            rebuilt, field_path + "/vehicleDamageFactor")
        self.assertEqual(b"0.0", factor.value)

    def test_angle_relations_and_ranges_are_rejected_atomically(self):
        refused = (
            ("shared/Gun-A/pitchLimits/minPitch", "0 -10 0.5 -8"),
            ("shared/Gun-A/pitchLimits/minPitch", "-91"),
            ("shared/Gun-A/pitchLimits/minPitch", "25"),
            ("shared/Gun-A/turretYawLimits", "20 -20"),
            ("shared/Gun-A/turretYawLimits", "-181 20"),
        )
        for field_path, replacement in refused:
            with self.assertRaises(vehicle_overlays.VehicleOverlayError,
                                   msg=(field_path, replacement)):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, self.GUNS, field_path, replacement,
                    is_running=lambda: False)
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

    def test_siege_pair_exposes_mode_specific_angles_and_syncs_invariants(self):
        self._install_siege_pair()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.SIEGE_VEHICLE)
        hull_weight = next(
            record for record in fields
            if record["fieldPath"] == "hull/weight")
        travel_pitch = next(
            record for record in fields
            if record["fieldPath"].endswith("pitchLimits/minPitch") and
            record["mode"] == "travel")
        siege_pitch = next(
            record for record in fields
            if record["fieldPath"].endswith("pitchLimits/minPitch") and
            record["mode"] == "siege")

        self.assertEqual("all", hull_weight["mode"])
        self.assertEqual(self.SIEGE_MODE, hull_weight["pairedMember"])
        self.assertIn("both the travel-mode and Siege-mode", hull_weight["scope"])
        self.assertTrue(travel_pitch["fieldLabel"].startswith("Travel mode / "))
        self.assertEqual("0 -1 1 -1", travel_pitch["originalValue"])
        self.assertEqual(self.SIEGE_MODE, siege_pitch["member"])
        self.assertTrue(siege_pitch["fieldLabel"].startswith("Siege mode / "))
        self.assertEqual("0 -4 1 -4", siege_pitch["originalValue"])

        vehicle_overlays.create_vehicle_profile(self.game, "Siege tuning")
        vehicle_overlays.apply_profile_edit(
            self.game, "Siege tuning", self.SIEGE_VEHICLE,
            "hull/weight", "2500", is_running=lambda: False)
        vehicle_overlays.apply_profile_edit(
            self.game, "Siege tuning", self.SIEGE_MODE,
            siege_pitch["fieldPath"], "-8", is_running=lambda: False)
        self.assertFalse(os.path.exists(self._overlay(self.SIEGE_VEHICLE)))

        self.assertEqual(2, vehicle_overlays.activate_vehicle_profile(
            self.game, "Siege tuning", is_running=lambda: False))
        self.assertEqual(2500, self._value(
            self.SIEGE_VEHICLE, "hull/weight"))
        self.assertEqual(2500, self._value(
            self.SIEGE_MODE, "hull/weight"))
        self.assertEqual(
            b"0 -1 1 -1", self._value(
                self.SIEGE_VEHICLE, travel_pitch["fieldPath"]))
        self.assertEqual(
            b"0 -8 1 -8", self._value(
                self.SIEGE_MODE, siege_pitch["fieldPath"]))

    def test_vehicle_browser_resolves_shared_topology_and_impact(self):
        choices = vehicle_overlays.list_vehicle_choices(self.game)
        self.assertEqual(
            [("ussr", "R11_MS-1"), ("ussr", "R12_Test")],
            [(choice["nation"], choice["vehicle"])
             for choice in choices])
        self.assertFalse(any(choice["vehicle"] in ("Observer", "list")
                             for choice in choices))

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)
        direct = next(record for record in fields
                      if record["fieldPath"] == "speedLimits/forward")
        engine = next(record for record in fields
                      if record["fieldPath"] == "shared/GAZ-M1/power")
        gun = next(record for record in fields
                   if record["fieldPath"] == "shared/Gun-A/reloadTime")
        shell = next(record for record in fields
                     if record["fieldPath"] == "Shell-A/damage/armor")
        explosion_radius = next(
            record for record in fields
            if record["fieldPath"] == "Shell-A/explosionRadius")

        self.assertEqual("Vehicle", direct["categoryLabel"])
        self.assertFalse(direct["shared"])
        self.assertEqual(("R11_MS-1",), direct["affectedVehicles"])
        for record, category in ((engine, "Engine"), (gun, "Gun"),
                                 (shell, "Shell")):
            self.assertEqual(category, record["categoryLabel"])
            self.assertTrue(record["shared"])
            self.assertEqual(
                ("Observer", "R11_MS-1", "R12_Test"),
                record["affectedVehicles"])
            self.assertIn(
                "Observer, MS-1, Test", record["scope"])

        self.assertEqual(self.ENGINES, engine["member"])
        self.assertEqual(self.GUNS, gun["member"])
        self.assertEqual(self.SHELLS, shell["member"])
        self.assertEqual("Shell-A / Explosion radius",
                         explosion_radius["fieldLabel"])
        self.assertEqual(self.SHELLS, explosion_radius["member"])

    def test_vehicle_browser_shows_the_stock_name_and_exact_resource_id(self):
        class _Translations(object):
            @staticmethod
            def gettext(key):
                return "MS-1" if key == "R11_MS-1_short" else key

        with mock.patch.object(
                vehicle_overlays, "_vehicle_translations",
                return_value=_Translations()):
            choices = vehicle_overlays.list_vehicle_choices(self.game)

        ms1 = next(choice for choice in choices
                   if choice["vehicle"] == "R11_MS-1")
        self.assertEqual("MS-1", ms1["label"])
        self.assertEqual("R11_MS-1", ms1["vehicle"])
        self.assertEqual(self.VEHICLE, ms1["member"])
        self.assertIn("lightTank", ms1["tags"])

    def test_type_5_heavy_is_read_from_the_japanese_roster(self):
        class _Translations(object):
            @staticmethod
            def gettext(key):
                return "五式重战" if key == "J20_Type_2605_short" else key

        list_member = "scripts/item_defs/vehicles/japan/list.xml"
        vehicle_member = (
            "scripts/item_defs/vehicles/japan/J20_Type_2605.xml")
        roster = packed.PackedElement(children=[
            (b"xmlns:xmlref", scalar(
                packed.TYPE_STRING, b"http://www.w3.org/2001/XInclude")),
            (b"J20_Type_2605", element([
                (b"userString", scalar(
                    packed.TYPE_STRING,
                    b"#japan_vehicles:J20_Type_2605")),
                (b"shortUserString", scalar(
                    packed.TYPE_STRING,
                    b"#japan_vehicles:J20_Type_2605_short")),
                (b"tags", scalar(packed.TYPE_STRING, b"heavyTank")),
            ])),
        ])
        self.members[list_member] = packed.write_packed_xml(roster)
        self.members[vehicle_member] = self.members[self.VEHICLE]
        self._write_package()

        with mock.patch.object(
                vehicle_overlays, "_vehicle_translations",
                side_effect=lambda unused_root, nation: (
                    _Translations() if nation == "japan" else None)):
            choices = vehicle_overlays.list_vehicle_choices(self.game)

        type5 = next(choice for choice in choices
                     if choice["vehicle"] == "J20_Type_2605")
        self.assertEqual("五式重战", type5["label"])
        self.assertEqual(vehicle_member, type5["member"])

    def test_catalog_prefix_is_hidden_only_in_human_facing_fields(self):
        record = vehicle_overlays._choice_record(
            "ussr", "R11_MS-1", "vehicle", self.VEHICLE,
            {"fieldPath": "speedLimits/forward"}, False,
            "R11_MS-1", ("R11_MS-1", "R12_Test"))

        self.assertEqual("R11_MS-1", record["vehicle"])
        self.assertEqual(("R11_MS-1", "R12_Test"),
                         record["affectedVehicles"])
        self.assertEqual("MS-1", record["displayVehicle"])
        self.assertEqual(("MS-1", "Test"),
                         record["affectedVehicleLabels"])
        self.assertNotIn("R11_", record["scope"])

    def test_untranslated_vehicle_label_uses_the_prefix_free_id(self):
        self.assertEqual(
            "Type_2605",
            vehicle_overlays._vehicle_label({
                "vehicle": "J20_Type_2605",
                "shortUserString": "#japan_vehicles:J20_Type_2605_short",
            }, None))
        self.assertEqual(
            "Prototype",
            vehicle_overlays._vehicle_display_id("X99_Prototype"))

    def test_vehicle_local_gun_values_win_and_hull_health_is_editable(self):
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        root.children.append((b"hull", element([
            (b"maxHealth", scalar(packed.TYPE_INTEGER, 80)),
        ])))
        turret = child(child(root, "turrets0").value, "T-18_mod").value
        gun = child(child(turret, "guns").value, "Gun-A").value
        gun.children.extend((
            (b"reloadTime", scalar(packed.TYPE_STRING, b"3.2")),
            (b"maxAmmo", scalar(packed.TYPE_INTEGER, 60)),
        ))
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)

        local_reload = next(
            record for record in fields
            if record["fieldPath"] ==
            "turrets0/T-18_mod/guns/Gun-A/reloadTime")
        hull_health = next(
            record for record in fields
            if record["fieldPath"] == "hull/maxHealth")
        self.assertEqual(self.VEHICLE, local_reload["member"])
        self.assertEqual("Gun", local_reload["categoryLabel"])
        self.assertFalse(local_reload["shared"])
        self.assertEqual(("R11_MS-1",), local_reload["affectedVehicles"])
        self.assertFalse(any(
            record["fieldPath"] == "shared/Gun-A/reloadTime"
            for record in fields))
        self.assertTrue(any(
            record["fieldPath"] == "shared/Gun-A/aimingTime"
            for record in fields))
        self.assertIn("Effective battle HP", hull_health["scope"])

        vehicle_overlays.create_vehicle_profile(self.game, "Local gun")
        vehicle_overlays.apply_profile_edit(
            self.game, "Local gun", self.VEHICLE,
            local_reload["fieldPath"], "2.5", is_running=lambda: False)
        vehicle_overlays.activate_vehicle_profile(
            self.game, "Local gun", is_running=lambda: False)
        self.assertEqual(
            b"2.5", vehicle_overlays._find_value(
                self._root(self.VEHICLE),
                local_reload["fieldPath"]).value)

    def test_vehicle_browser_does_not_infer_unlisted_component_links(self):
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        engine = child(root, "engines").value
        child(engine, "GAZ-M1").value = b"vehicle-local"
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)

        self.assertFalse(any(
            record["member"] == self.ENGINES for record in fields))

    def test_vehicle_element_reference_keeps_shared_engine_fields(self):
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        engines = child(root, "engines").value
        engines.children = [
            (name, element([
                (b"unlocks", element([])),
            ]) if name == b"GAZ-M1" else value)
            for name, value in engines.children]
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)

        engine_paths = set(
            record["fieldPath"] for record in fields
            if record["member"] == self.ENGINES)
        self.assertIn("shared/GAZ-M1/power", engine_paths)
        self.assertIn("shared/GAZ-M1/maxHealth", engine_paths)

    def test_vehicle_element_reference_keeps_shared_radio_fields(self):
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        root.children.append((b"radios", element([
            (b"10R", element([
                (b"unlocks", element([])),
            ])),
        ])))
        radios = packed.PackedElement(children=[
            (b"shared", element([
                (b"10R", element([
                    (b"weight", scalar(packed.TYPE_INTEGER, 100)),
                    (b"maxHealth", scalar(packed.TYPE_INTEGER, 30)),
                    (b"maxRegenHealth", scalar(packed.TYPE_INTEGER, 15)),
                ])),
            ])),
        ])
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self.members[self.RADIOS] = packed.write_packed_xml(radios)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)

        radio_paths = set(
            record["fieldPath"] for record in fields
            if record["member"] == self.RADIOS)
        self.assertEqual({
            "shared/10R/maxHealth",
            "shared/10R/maxRegenHealth",
            "shared/10R/weight",
        }, radio_paths)

    def test_vehicle_local_component_leaf_wins_over_shared_value(self):
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        engines = child(root, "engines").value
        engines.children = [
            (name, element([
                (b"power", scalar(packed.TYPE_INTEGER, 95)),
                (b"unlocks", element([])),
            ]) if name == b"GAZ-M1" else value)
            for name, value in engines.children]
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self._write_package()

        fields = vehicle_overlays.list_vehicle_field_choices(
            self.game, self.VEHICLE)

        self.assertFalse(any(
            record["fieldPath"] == "shared/GAZ-M1/power"
            for record in fields))
        self.assertTrue(any(
            record["fieldPath"] == "shared/GAZ-M1/maxHealth"
            for record in fields))

    def test_existing_two_value_string_penetration_is_safely_editable(self):
        field_path = "shared/Gun-A/shots/Shell-A/piercingPower"

        result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.GUNS, field_path, "30.0  24",
            is_running=lambda: False)

        value = vehicle_overlays._find_value(self._root(self.GUNS), field_path)
        self.assertEqual(packed.TYPE_STRING, value.value_type)
        self.assertEqual(b"30 24", value.value)
        self.assertEqual("22 18", result["originalValue"])
        self.assertEqual("30 24", result["currentValue"])

    def test_existing_shell_explosion_radius_is_safely_editable(self):
        field_path = "Shell-A/explosionRadius"

        result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.SHELLS, field_path, "3.5",
            is_running=lambda: False)

        value = vehicle_overlays._find_value(
            self._root(self.SHELLS), field_path)
        self.assertEqual(packed.TYPE_STRING, value.value_type)
        self.assertEqual(b"3.5", value.value)
        self.assertEqual("2.42", result["originalValue"])
        self.assertEqual("3.5", result["currentValue"])

    def test_existing_shell_module_damage_is_safely_editable(self):
        field_path = "Shell-A/damage/devices"

        result = vehicle_overlays.apply_vehicle_edit(
            self.game, self.SHELLS, field_path, "30",
            is_running=lambda: False)

        value = vehicle_overlays._find_value(
            self._root(self.SHELLS), field_path)
        self.assertEqual(packed.TYPE_INTEGER, value.value_type)
        self.assertEqual(30, value.value)
        self.assertEqual("27", result["originalValue"])
        self.assertEqual("30", result["currentValue"])

    def test_ids_resources_compressed_strings_and_missing_children_are_refused(self):
        refused = (
            (self.ENGINES, "ids/GAZ-M1"),
            (self.VEHICLE, "chassis/T-18Bis/resource"),
            (self.ENGINES, "shared/GAZ-M1/tags"),
            (self.VEHICLE, "speedLimits/newChild"),
        )
        for member, field_path in refused:
            with self.assertRaises(vehicle_overlays.VehicleOverlayError,
                                   msg=field_path):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, member, field_path, "1",
                    is_running=lambda: False)

    def test_parser_and_storage_constraints_fail_before_writing(self):
        refused = (
            (self.VEHICLE, "speedLimits/forward", "0"),
            (self.GUNS, "shared/Gun-A/reloadTime", "nan"),
            (self.GUNS, "shared/Gun-A/reloadTime", "inf"),
            (self.GUNS, "shared/Gun-A/maxAmmo", str(1 << 63)),
            (self.SHELLS, "Shell-A/damage/armor", "-1"),
            (self.SHELLS, "Shell-A/explosionRadius", "0"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "30"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "30 20 10"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "30 nan"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "0 0"),
            (self.GUNS,
             "shared/Gun-A/shots/Shell-A/piercingPower", "20 30"),
        )
        for member, field_path, replacement in refused:
            with self.assertRaises(vehicle_overlays.VehicleOverlayError,
                                   msg=(field_path, replacement)):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, member, field_path, replacement,
                    is_running=lambda: False)
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

    def test_penetration_refuses_a_non_string_stock_type(self):
        field_path = "shared/Gun-A/shots/Shell-A/piercingPower"
        root = packed.read_packed_xml(self.members[self.GUNS])
        value = vehicle_overlays._find_value(root, field_path)
        value.value_type = packed.TYPE_INTEGER
        value.value = 22
        self.members[self.GUNS] = packed.write_packed_xml(root)
        self._write_package()

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "Packed string type"):
            vehicle_overlays.inspect_vehicle_field(
                self.game, self.GUNS, field_path)

    def test_health_relation_is_validated_after_all_logical_edits(self):
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "maxHealth"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.ENGINES,
                "shared/GAZ-M1/maxRegenHealth", "50",
                is_running=lambda: False)

        self.assertFalse(os.path.exists(self._overlay(self.ENGINES)))

    def test_running_game_refuses_apply_and_restore(self):
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "Close World of Tanks"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/forward", "40",
                is_running=lambda: True)
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "Close World of Tanks"):
            vehicle_overlays.restore_vehicle_defaults(
                self.game, is_running=lambda: True)

    def test_failed_transaction_restores_previous_overlay_and_manifest(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        with open(self._overlay(self.VEHICLE), "rb") as stream:
            overlay_before = stream.read()
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            manifest_before = stream.read()
        original_replace = os.replace

        def fail_manifest_install(source, target):
            if (".wot-vehicle-overlay-" in source and
                    os.path.basename(source).startswith("new-") and
                    target.endswith(vehicle_overlays.MANIFEST_NAME)):
                raise OSError("synthetic manifest failure")
            return original_replace(source, target)

        with mock.patch(
                "vehicle_overlays.os.replace",
                side_effect=fail_manifest_install):
            with self.assertRaisesRegex(
                    vehicle_overlays.VehicleOverlayError, "rolled back"):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, self.VEHICLE, "speedLimits/forward", "41",
                    is_running=lambda: False)

        with open(self._overlay(self.VEHICLE), "rb") as stream:
            self.assertEqual(overlay_before, stream.read())
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            self.assertEqual(manifest_before, stream.read())

    def test_incomplete_rollback_keeps_mapped_recovery_files(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        with open(self._overlay(self.VEHICLE), "rb") as stream:
            overlay_before = stream.read()
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            manifest_before = stream.read()
        original_replace = os.replace

        def fail_install_and_rollback(source, target):
            if (".wot-vehicle-overlay-" in source and
                    os.path.basename(source).startswith("new-") and
                    target.endswith(vehicle_overlays.MANIFEST_NAME)):
                raise OSError("synthetic install failure")
            if (".wot-vehicle-overlay-" in source and
                    os.path.basename(source) == "backup-0" and
                    target == self._overlay(self.VEHICLE)):
                raise OSError("synthetic rollback failure")
            return original_replace(source, target)

        with mock.patch(
                "vehicle_overlays.os.replace",
                side_effect=fail_install_and_rollback):
            with self.assertRaisesRegex(
                    vehicle_overlays.VehicleOverlayError,
                    "Recovery files were kept"):
                vehicle_overlays.apply_vehicle_edit(
                    self.game, self.VEHICLE, "speedLimits/forward", "41",
                    is_running=lambda: False)

        recovery_roots = [
            os.path.join(self.game, name) for name in os.listdir(self.game)
            if name.startswith(".wot-vehicle-overlay-")]
        self.assertEqual(1, len(recovery_roots))
        with open(os.path.join(
                recovery_roots[0], "recovery.json"), "rb") as stream:
            recovery = json.load(stream)
        self.assertEqual("apply", recovery["operation"])
        self.assertEqual(
            os.path.relpath(self._overlay(self.VEHICLE), self.game),
            recovery["targets"][0]["target"].replace("/", os.sep))
        self.assertTrue(os.path.isfile(os.path.join(
            recovery_roots[0], "backup-0")))

        self.assertEqual(1, vehicle_overlays.recover_vehicle_profile_transactions(
            self.game, is_running=lambda: False))
        with open(self._overlay(self.VEHICLE), "rb") as stream:
            self.assertEqual(overlay_before, stream.read())
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            self.assertEqual(manifest_before, stream.read())
        self.assertFalse(os.path.exists(recovery_roots[0]))

    def test_failed_default_restore_puts_every_owned_file_back(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.ENGINES, "shared/GAZ-M1/power", "100",
            is_running=lambda: False)
        targets = (
            self._overlay(self.VEHICLE),
            self._overlay(self.ENGINES),
            vehicle_overlays.manifest_path(self.game),
        )
        before = {}
        for path in targets:
            with open(path, "rb") as stream:
                before[path] = stream.read()
        original_replace = os.replace

        def fail_second_move(source, target):
            if (source == self._overlay(self.ENGINES) and
                    ".wot-vehicle-restore-" in target):
                raise OSError("synthetic restore failure")
            return original_replace(source, target)

        with mock.patch(
                "vehicle_overlays.os.replace", side_effect=fail_second_move):
            with self.assertRaisesRegex(
                    vehicle_overlays.VehicleOverlayError, "rolled back"):
                vehicle_overlays.restore_vehicle_defaults(
                    self.game, is_running=lambda: False)

        for path in targets:
            with open(path, "rb") as stream:
                self.assertEqual(before[path], stream.read())
        self.assertFalse(any(
            name.startswith(".wot-vehicle-restore-")
            for name in os.listdir(self.game)))

    def test_restore_removes_only_owned_members_and_keeps_other_mods(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        other = self._write(
            vehicle_overlays.OVERLAY_ROOT + "/other-author/mod.xml", b"keep")

        count = vehicle_overlays.restore_vehicle_defaults(
            self.game, is_running=lambda: False)

        self.assertEqual(1, count)
        self.assertFalse(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))
        with open(other, "rb") as stream:
            self.assertEqual(b"keep", stream.read())

    def test_restore_removes_a_manifest_owned_drifted_materialization(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        self._write(
            "/".join((vehicle_overlays.OVERLAY_ROOT, self.VEHICLE)),
            b"changed externally")

        self.assertEqual(1, vehicle_overlays.restore_vehicle_defaults(
            self.game, is_running=lambda: False))

        self.assertFalse(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

    def test_apply_rebuilds_a_manifest_owned_drifted_materialization(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        self._write(
            "/".join((vehicle_overlays.OVERLAY_ROOT, self.VEHICLE)),
            b"changed externally")

        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/backward", "12",
            is_running=lambda: False)

        self.assertEqual(
            40, self._value(self.VEHICLE, "speedLimits/forward"))
        self.assertEqual(
            12, self._value(self.VEHICLE, "speedLimits/backward"))

    def test_apply_normalizes_stale_manifest_target_metadata(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        path = vehicle_overlays.manifest_path(self.game)
        with open(path, "rb") as stream:
            manifest = json.load(stream)
        manifest["targetVersion"] = "0.9.22.legacy"
        manifest["targetBuild"] = "older"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream)

        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/backward", "12",
            is_running=lambda: False)

        with open(path, "rb") as stream:
            normalized = json.load(stream)
        self.assertEqual(
            vehicle_overlays.TARGET_VERSION, normalized["targetVersion"])
        self.assertEqual(
            vehicle_overlays.TARGET_BUILD, normalized["targetBuild"])

    def test_invalid_manifest_fails_closed(self):
        self._write(
            "/".join((vehicle_overlays.OVERLAY_ROOT,
                      vehicle_overlays.MANIFEST_NAME)),
            b'{"schema":999,"members":[]}')

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "does not belong"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/forward", "40",
                is_running=lambda: False)

    def test_manifest_accepts_the_full_supported_member_count(self):
        manifest = vehicle_overlays._empty_manifest()
        manifest["members"] = []
        for index in range(vehicle_overlays.MAX_OVERLAY_MEMBERS):
            member = (
                "scripts/item_defs/vehicles/ussr/Capacity_%04d.xml" %
                index)
            manifest["members"].append({
                "sourceMember": member,
                "sourcePackage": vehicle_overlays.SOURCE_PACKAGE,
                "overlayRelativePath": member,
                "overlaySha256": "0" * 64,
                "edits": [{
                    "fieldPath": "speedLimits/forward",
                    "originalPackedType": "integer",
                    "originalValue": 32,
                    "replacementValue": 40,
                }],
            })

        validated = vehicle_overlays._validate_manifest(manifest)

        self.assertEqual(
            vehicle_overlays.MAX_OVERLAY_MEMBERS,
            len(validated["members"]))

    def test_manifest_rejects_one_member_over_the_supported_count(self):
        manifest = vehicle_overlays._empty_manifest()
        entry = {
            "sourceMember": self.VEHICLE,
            "sourcePackage": vehicle_overlays.SOURCE_PACKAGE,
            "overlayRelativePath": self.VEHICLE,
            "overlaySha256": "0" * 64,
            "edits": [{
                "fieldPath": "speedLimits/forward",
                "originalPackedType": "integer",
                "originalValue": 32,
                "replacementValue": 40,
            }],
        }
        manifest["members"] = [entry] * (
            vehicle_overlays.MAX_OVERLAY_MEMBERS + 1)

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError,
                "more than 1024 members"):
            vehicle_overlays._validate_manifest(manifest)

    def test_manifest_size_error_is_distinct_from_member_count(self):
        manifest = vehicle_overlays._empty_manifest()

        class OversizedPayload(object):
            def __len__(self):
                return vehicle_overlays.MAX_OVERLAY_MANIFEST_BYTES + 1

        with mock.patch.object(
                vehicle_overlays, "_manifest_bytes",
                return_value=OversizedPayload()), self.assertRaisesRegex(
                    vehicle_overlays.VehicleOverlayError,
                    "larger than 32 MiB"):
            vehicle_overlays._validate_manifest(manifest)

    def test_integer_required_manifest_value_cannot_become_text(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.GUNS, "shared/Gun-A/maxAmmo", "46",
            is_running=lambda: False)
        path = vehicle_overlays.manifest_path(self.game)
        with open(path, "rb") as stream:
            manifest = json.load(stream)
        manifest["members"][0]["edits"][0]["replacementValue"] = "47"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream)

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError,
                "invalid replacement value"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.GUNS, "shared/Gun-A/reloadTime", "3",
                is_running=lambda: False)

    def test_changed_original_package_contract_refuses_a_saved_edit(self):
        vehicle_overlays.apply_vehicle_edit(
            self.game, self.VEHICLE, "speedLimits/forward", "40",
            is_running=lambda: False)
        root = packed.read_packed_xml(self.members[self.VEHICLE])
        vehicle_overlays._find_value(
            root, "speedLimits/forward").value = 33
        self.members[self.VEHICLE] = packed.write_packed_xml(root)
        self._write_package()

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "original scripts.pkg"):
            vehicle_overlays.apply_vehicle_edit(
                self.game, self.VEHICLE, "speedLimits/backward", "12",
                is_running=lambda: False)

    def test_named_profile_edits_stay_out_of_res_mods_until_activation(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast MS-1")

        result = vehicle_overlays.apply_profile_edit(
            self.game, "Fast MS-1", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)

        self.assertEqual("40", result["currentValue"])
        self.assertEqual("Fast MS-1", result["profileName"])
        self.assertFalse(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))
        self.assertTrue(os.path.isfile(
            vehicle_overlays.profile_store_path(self.game)))

    def test_profile_field_choices_include_all_current_values_in_one_snapshot(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast MS-1")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast MS-1", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)

        fields = vehicle_overlays.list_vehicle_profile_field_choices(
            self.game, "Fast MS-1", self.VEHICLE)
        values = dict((record["fieldPath"], record["currentValue"])
                      for record in fields if record["member"] == self.VEHICLE)

        self.assertEqual("40", values["speedLimits/forward"])
        self.assertEqual("8", values["speedLimits/backward"])

    def test_profiles_use_appdata_without_creating_a_game_recovery_journal(self):
        appdata = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, appdata, True)
        with mock.patch.dict(os.environ, {"APPDATA": appdata}):
            vehicle_overlays.create_vehicle_profile(self.game, "Fast MS-1")
            path = vehicle_overlays.profile_store_path(self.game)

        self.assertEqual(
            os.path.join(
                appdata, *vehicle_overlays.PROFILE_STORE_APPDATA_PARTS,
                vehicle_overlays.PROFILE_STORE_NAME),
            path)
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(os.path.exists(
            vehicle_overlays.legacy_profile_store_path(self.game)))
        self.assertFalse(any(
            name.startswith(".wot-vehicle-")
            for name in os.listdir(self.game)))

    def test_legacy_profile_store_is_copied_to_appdata_and_retained(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast MS-1")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast MS-1", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)
        legacy_path = vehicle_overlays.legacy_profile_store_path(self.game)
        with open(legacy_path, "rb") as stream:
            legacy_payload = stream.read()
        appdata = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, appdata, True)

        with mock.patch.dict(os.environ, {"APPDATA": appdata}):
            self.assertEqual(
                ["Fast MS-1"],
                vehicle_overlays.list_vehicle_profiles(self.game))
            external_path = vehicle_overlays.profile_store_path(self.game)
            current = vehicle_overlays.inspect_profile_field(
                self.game, "Fast MS-1", self.VEHICLE,
                "speedLimits/forward")

        self.assertEqual("40", current["currentValue"])
        with open(external_path, "rb") as stream:
            self.assertEqual(legacy_payload, stream.read())
        with open(legacy_path, "rb") as stream:
            self.assertEqual(legacy_payload, stream.read())

    def test_failed_appdata_migration_keeps_the_legacy_store_readable(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast MS-1")
        legacy_path = vehicle_overlays.legacy_profile_store_path(self.game)
        appdata = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, appdata, True)

        with mock.patch.dict(os.environ, {"APPDATA": appdata}), mock.patch(
                "vehicle_overlays._atomic_profile_store_write",
                side_effect=vehicle_overlays.VehicleOverlayError(
                    "APPDATA is read-only")):
            self.assertEqual(
                ["Fast MS-1"],
                vehicle_overlays.list_vehicle_profiles(self.game))
            external_path = vehicle_overlays.profile_store_path(self.game)

        self.assertTrue(os.path.isfile(legacy_path))
        self.assertFalse(os.path.exists(external_path))

    def test_overlay_parent_symlink_cannot_redirect_activation_outside_game(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        overlay_root = os.path.join(
            self.game, *vehicle_overlays.OVERLAY_ROOT.split("/"))
        os.makedirs(overlay_root)
        redirected = os.path.join(overlay_root, "scripts")
        try:
            os.symlink(outside, redirected, target_is_directory=True)
        except (AttributeError, NotImplementedError, OSError) as error:
            self.skipTest("directory symlinks are unavailable: %s" % error)

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError,
                "symlink, or junction"):
            vehicle_overlays.activate_vehicle_profile(
                self.game, "Fast", is_running=lambda: False)

        escaped = os.path.join(
            outside, "item_defs", "vehicles", "ussr", "R11_MS-1.xml")
        self.assertFalse(os.path.exists(escaped))
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

    @unittest.skipUnless(os.name == "nt", "Windows junction check")
    def test_overlay_parent_junction_cannot_redirect_activation_outside_game(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        overlay_root = os.path.join(
            self.game, *vehicle_overlays.OVERLAY_ROOT.split("/"))
        os.makedirs(overlay_root)
        redirected = os.path.join(overlay_root, "scripts")
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", redirected, outside],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode != 0:
            self.skipTest(
                "directory junctions are unavailable: %s" %
                result.stdout.decode("utf-8", "replace"))

        def remove_junction():
            if os.path.lexists(redirected):
                os.rmdir(redirected)

        self.addCleanup(remove_junction)
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError,
                "symlink, or junction"):
            vehicle_overlays.activate_vehicle_profile(
                self.game, "Fast", is_running=lambda: False)

        escaped = os.path.join(
            outside, "item_defs", "vehicles", "ussr", "R11_MS-1.xml")
        self.assertFalse(os.path.exists(escaped))

    def test_restore_refuses_to_follow_an_owned_overlay_outside_game(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)
        vehicle_overlays.activate_vehicle_profile(
            self.game, "Fast", is_running=lambda: False)
        overlay_root = os.path.join(
            self.game, *vehicle_overlays.OVERLAY_ROOT.split("/"))
        scripts_root = os.path.join(overlay_root, "scripts")
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        outside_scripts = os.path.join(outside, "scripts")
        os.replace(scripts_root, outside_scripts)
        try:
            os.symlink(
                outside_scripts, scripts_root, target_is_directory=True)
        except (AttributeError, NotImplementedError, OSError) as error:
            os.replace(outside_scripts, scripts_root)
            self.skipTest("directory symlinks are unavailable: %s" % error)
        escaped = os.path.join(
            outside_scripts, "item_defs", "vehicles", "ussr",
            "R11_MS-1.xml")

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError,
                "symlink, or junction"):
            vehicle_overlays.restore_vehicle_defaults(
                self.game, is_running=lambda: False)

        self.assertTrue(os.path.isfile(escaped))
        self.assertTrue(os.path.isfile(
            vehicle_overlays.manifest_path(self.game)))

    def test_recovery_refuses_a_target_redirected_outside_game(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        overlay_root = os.path.join(
            self.game, *vehicle_overlays.OVERLAY_ROOT.split("/"))
        os.makedirs(overlay_root)
        try:
            os.symlink(
                outside, os.path.join(overlay_root, "scripts"),
                target_is_directory=True)
        except (AttributeError, NotImplementedError, OSError) as error:
            self.skipTest("directory symlinks are unavailable: %s" % error)
        transaction = os.path.join(
            self.game, ".wot-vehicle-overlay-synthetic")
        os.makedirs(transaction)
        with open(os.path.join(transaction, "recovery.json"),
                  "w", encoding="utf-8") as stream:
            json.dump({
                "operation": "apply",
                "targets": [
                    {"backup": "backup-0", "hadTarget": False,
                     "target": "%s/%s" % (
                         vehicle_overlays.OVERLAY_ROOT, self.VEHICLE)},
                    {"backup": "backup-1", "hadTarget": False,
                     "target": "%s/%s" % (
                         vehicle_overlays.OVERLAY_ROOT,
                         vehicle_overlays.MANIFEST_NAME)},
                ],
            }, stream)

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError,
                "symlink, or junction"):
            vehicle_overlays.recover_vehicle_profile_transactions(
                self.game, is_running=lambda: False)

        self.assertTrue(os.path.isfile(
            os.path.join(transaction, "recovery.json")))

    def test_profiles_keep_independent_values(self):
        for name, speed in (("Fast", "40"), ("Very fast", "55")):
            vehicle_overlays.create_vehicle_profile(self.game, name)
            vehicle_overlays.apply_profile_edit(
                self.game, name, self.VEHICLE,
                "speedLimits/forward", speed, is_running=lambda: False)

        fast = vehicle_overlays.inspect_profile_field(
            self.game, "Fast", self.VEHICLE, "speedLimits/forward")
        very_fast = vehicle_overlays.inspect_profile_field(
            self.game, "Very fast", self.VEHICLE,
            "speedLimits/forward")

        self.assertEqual("40", fast["currentValue"])
        self.assertEqual("55", very_fast["currentValue"])
        self.assertEqual(["Fast", "Very fast"],
                         vehicle_overlays.list_vehicle_profiles(self.game))

    def test_switching_profiles_removes_members_owned_only_by_the_previous_one(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)
        vehicle_overlays.create_vehicle_profile(self.game, "Strong engine")
        vehicle_overlays.apply_profile_edit(
            self.game, "Strong engine", self.ENGINES,
            "shared/GAZ-M1/power", "120", is_running=lambda: False)
        vehicle_overlays.activate_vehicle_profile(
            self.game, "Fast", is_running=lambda: False)
        self.assertTrue(os.path.isfile(self._overlay(self.VEHICLE)))

        vehicle_overlays.activate_vehicle_profile(
            self.game, "Strong engine", is_running=lambda: False)

        self.assertFalse(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertTrue(os.path.isfile(self._overlay(self.ENGINES)))
        self.assertEqual(120, self._value(
            self.ENGINES, "shared/GAZ-M1/power"))

    def test_activation_materializes_then_original_mode_removes_profile(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)

        prepared = vehicle_overlays.prepare_vehicle_profile(
            self.game, "Fast", is_running=lambda: False)

        self.assertEqual(1, prepared["installedMembers"])
        self.assertEqual(40, self._value(
            self.VEHICLE, "speedLimits/forward"))
        with open(vehicle_overlays.manifest_path(self.game), "rb") as stream:
            self.assertEqual("Fast", json.load(stream)["activeProfile"])

        original = vehicle_overlays.prepare_vehicle_profile(
            self.game, None, is_running=lambda: False)

        self.assertEqual(1, original["removedMembers"])
        self.assertFalse(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))
        self.assertEqual(["Fast"],
                         vehicle_overlays.list_vehicle_profiles(self.game))

    def test_original_mode_leaves_foreign_vehicle_overrides_unchanged(self):
        foreign = "/".join((
            vehicle_overlays.OVERLAY_ROOT, self.VEHICLE))
        self._write(foreign, b"another tool")

        prepared = vehicle_overlays.prepare_vehicle_profile(
            self.game, None, is_running=lambda: False)

        self.assertIsNone(prepared["profile"])
        self.assertTrue(os.path.isfile(self._overlay(self.VEHICLE)))
        with open(self._overlay(self.VEHICLE), "rb") as stream:
            self.assertEqual(b"another tool", stream.read())

    def test_clear_and_delete_change_only_the_named_profile(self):
        for name in ("Fast", "Heavy"):
            vehicle_overlays.create_vehicle_profile(self.game, name)
            vehicle_overlays.apply_profile_edit(
                self.game, name, self.VEHICLE,
                "speedLimits/forward", "40", is_running=lambda: False)

        self.assertEqual(1, vehicle_overlays.clear_vehicle_profile(
            self.game, "Fast", is_running=lambda: False))
        self.assertEqual("32", vehicle_overlays.inspect_profile_field(
            self.game, "Fast", self.VEHICLE,
            "speedLimits/forward")["currentValue"])
        self.assertEqual("40", vehicle_overlays.inspect_profile_field(
            self.game, "Heavy", self.VEHICLE,
            "speedLimits/forward")["currentValue"])

        self.assertEqual("Fast", vehicle_overlays.delete_vehicle_profile(
            self.game, "Fast", is_running=lambda: False))
        self.assertEqual(["Heavy"],
                         vehicle_overlays.list_vehicle_profiles(self.game))

    def test_profile_names_are_trimmed_case_unique_and_reserve_original(self):
        self.assertEqual("Fast", vehicle_overlays.create_vehicle_profile(
            self.game, "  Fast  "))
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "already exists"):
            vehicle_overlays.create_vehicle_profile(self.game, "fast")
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "reserved"):
            vehicle_overlays.create_vehicle_profile(
                self.game, vehicle_overlays.ORIGINAL_PROFILE_LABEL)

    def test_invalid_profile_store_fails_closed_without_writing_res_mods(self):
        self._write(
            vehicle_overlays.PROFILE_STORE_RELATIVE,
            b'{"schema":999,"profiles":[]}')

        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "does not belong"):
            vehicle_overlays.list_vehicle_profiles(self.game)
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "does not belong"):
            vehicle_overlays.activate_vehicle_profile(
                self.game, "Fast", is_running=lambda: False)

        self.assertFalse(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

    def test_packaged_launcher_analysis_includes_the_shared_packed_xml_parser(self):
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "build_launcher.ps1")
        with open(script, "r", encoding="utf-8") as stream:
            content = stream.read()
        self.assertIn('0.9.22\\tools', content)
        self.assertIn('--paths', content)
        self.assertIn('--hidden-import packed_xml', content)
        self.assertIn('Launcher build dependency is missing', content)


class FetchedOverlayTest(VehicleOverlayTest):
    """A room host's overlay can be read, installed, and restored."""

    def _activated_payload(self):
        vehicle_overlays.create_vehicle_profile(self.game, "Fast MS-1")
        vehicle_overlays.apply_profile_edit(
            self.game, "Fast MS-1", self.VEHICLE,
            "speedLimits/forward", "40", is_running=lambda: False)
        activated = vehicle_overlays.activate_vehicle_profile(
            self.game, "Fast MS-1", is_running=lambda: False)
        self.assertEqual(1, activated)
        manifest, payload, digest = vehicle_overlays.active_vehicle_overlay(
            self.game, is_running=lambda: False)
        self.assertIsNotNone(manifest)
        self.assertEqual(1, len(payload))
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        return manifest, payload, digest

    def test_digest_is_empty_without_an_overlay(self):
        self.assertEqual(
            "", vehicle_overlays.vehicle_overlay_digest(
                self.game, is_running=lambda: False))

    def test_install_round_trip_matches_the_activated_overlay(self):
        manifest, payload, digest = self._activated_payload()
        self.assertTrue(os.path.exists(self._overlay(self.VEHICLE)))
        self.assertTrue(os.path.exists(
            vehicle_overlays.manifest_path(self.game)))

        removed = vehicle_overlays.ensure_original_vehicle_data(
            self.game, is_running=lambda: False)
        self.assertEqual(1, removed)
        self.assertEqual(
            "", vehicle_overlays.vehicle_overlay_digest(
                self.game, is_running=lambda: False))

        installed = vehicle_overlays.install_vehicle_overlay(
            self.game, manifest, payload, is_running=lambda: False)
        self.assertEqual(1, installed)
        again, again_payload, again_digest = (
            vehicle_overlays.active_vehicle_overlay(
                self.game, is_running=lambda: False))
        self.assertEqual(manifest, again)
        self.assertEqual(payload, again_payload)
        self.assertEqual(digest, again_digest)

        # The fetched install is removed by the same stock-restore path.
        removed = vehicle_overlays.ensure_original_vehicle_data(
            self.game, is_running=lambda: False)
        self.assertEqual(1, removed)

    def test_install_rejects_a_tampered_member(self):
        manifest, payload, digest = self._activated_payload()
        vehicle_overlays.ensure_original_vehicle_data(
            self.game, is_running=lambda: False)
        payload = dict(payload)
        payload[self.VEHICLE] = payload[self.VEHICLE] + b"tampered"
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "checksum"):
            vehicle_overlays.install_vehicle_overlay(
                self.game, manifest, payload, is_running=lambda: False)

    def test_install_rejects_a_manifest_member_mismatch(self):
        manifest, payload, digest = self._activated_payload()
        vehicle_overlays.ensure_original_vehicle_data(
            self.game, is_running=lambda: False)
        payload = dict(payload)
        del payload[self.VEHICLE]
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError,
                "members do not match"):
            vehicle_overlays.install_vehicle_overlay(
                self.game, manifest, payload, is_running=lambda: False)

    def test_install_rejects_a_foreign_manifest(self):
        manifest, payload, digest = self._activated_payload()
        vehicle_overlays.ensure_original_vehicle_data(
            self.game, is_running=lambda: False)
        manifest = copy.deepcopy(manifest)
        manifest["members"][0]["edits"] = []
        with self.assertRaisesRegex(
                vehicle_overlays.VehicleOverlayError, "logical edits"):
            vehicle_overlays.install_vehicle_overlay(
                self.game, manifest, payload, is_running=lambda: False)


if __name__ == "__main__":
    unittest.main()
