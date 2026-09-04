import base64
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'launcher'))

from vehicle_overlay_store import (
    MAX_OVERLAY_MANIFEST_BYTES, MAX_OVERLAY_MEMBER_BYTES,
    MAX_OVERLAY_MEMBERS, VehicleOverlayStore,
    VehicleOverlayStoreError)
from lan_battle_server import (
    CLIENT_BUILD_0922, ClientHandler, PROTOCOL_VERSION,
    ThreadedTCPServer, VEHICLE_OVERLAY_CAPABILITY)
import core as launcher_core
import vehicle_overlays as launcher_vehicle_overlays


MEMBER = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml"
MEMBER_DATA = b"\x01packed-xml-member-data\x02"


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _manifest_bytes(members, profile="Fast MS-1"):
    return json.dumps({
        "schema": 1,
        "targetVersion": "0.9.22.0.1",
        "targetBuild": "1513",
        "sourcePackage": "res/packages/scripts.pkg",
        "createdAt": "2026-08-23T12:00:00Z",
        "updatedAt": "2026-08-23T12:00:00Z",
        "activeProfile": profile,
        "members": members,
    }, sort_keys=True).encode("utf-8")


def _member_entry(member=MEMBER, data=MEMBER_DATA):
    return {
        "sourceMember": member,
        "sourcePackage": "res/packages/scripts.pkg",
        "overlayRelativePath": member,
        "overlaySha256": _sha256(data),
        "edits": [{
            "fieldPath": "speedLimits/forward",
            "originalPackedType": "integer",
            "originalValue": 32,
            "replacementValue": 40,
        }],
    }


def _capacity_entries(count):
    return [_member_entry(
        member=("scripts/item_defs/vehicles/ussr/Capacity_%04d.xml" %
                index)) for index in range(count)]


class VehicleOverlayStoreTest(unittest.TestCase):
    def setUp(self):
        self.game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.game, True)
        self.root = os.path.join(
            self.game, "res_mods", "0.9.22.0.1")
        os.makedirs(self.root)

    def _write_member(self, member=MEMBER, data=MEMBER_DATA):
        path = os.path.join(self.root, *member.split("/"))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "wb") as stream:
            stream.write(data)
        return path

    def _write_manifest(self, members):
        path = os.path.join(self.root, "vehicle_overlays.json")
        with open(path, "wb") as stream:
            stream.write(_manifest_bytes(members))
        return path

    def test_an_empty_root_serves_stock_data(self):
        store = VehicleOverlayStore(self.game)

        self.assertFalse(store.present)
        self.assertEqual("", store.digest)
        self.assertEqual("", store.profile)
        payload = store.manifest_payload()
        self.assertFalse(payload["present"])
        self.assertEqual([], payload["members"])
        self.assertIsNone(store.member_payload(MEMBER))

    def test_launcher_and_server_capacity_limits_match(self):
        self.assertEqual(
            MAX_OVERLAY_MEMBERS,
            launcher_core.MAX_OVERLAY_MEMBERS)
        self.assertEqual(
            MAX_OVERLAY_MEMBERS,
            launcher_vehicle_overlays.MAX_OVERLAY_MEMBERS)
        self.assertEqual(
            MAX_OVERLAY_MANIFEST_BYTES,
            launcher_core.MAX_OVERLAY_MANIFEST_BYTES)
        self.assertEqual(
            MAX_OVERLAY_MANIFEST_BYTES,
            launcher_vehicle_overlays.MAX_OVERLAY_MANIFEST_BYTES)

    def test_a_store_without_a_root_is_empty(self):
        store = VehicleOverlayStore()
        self.assertFalse(store.present)

    def test_load_verifies_members_and_digest(self):
        self._write_member()
        manifest_path = self._write_manifest([_member_entry()])

        store = VehicleOverlayStore(self.game)

        self.assertTrue(store.present)
        self.assertEqual("Fast MS-1", store.profile)
        with open(manifest_path, "rb") as stream:
            self.assertEqual(_sha256(stream.read()), store.digest)
        self.assertEqual(1, store.member_count)

        payload = store.manifest_payload()
        self.assertTrue(payload["present"])
        self.assertEqual(1, len(payload["members"]))
        self.assertEqual(MEMBER, payload["members"][0]["sourceMember"])
        self.assertEqual(len(MEMBER_DATA),
                         payload["members"][0]["size"])

        member = store.member_payload(MEMBER)
        self.assertIsNotNone(member)
        self.assertEqual(MEMBER, member["sourceMember"])
        self.assertEqual(_sha256(MEMBER_DATA), member["sha256"])
        self.assertEqual(MEMBER_DATA, base64.b64decode(
            member["data_b64"].encode("ascii")))
        self.assertIsNone(store.member_payload("unknown/member.xml"))

    def test_a_tampered_member_fails_closed(self):
        self._write_member(data=MEMBER_DATA + b"tampered")
        self._write_manifest([_member_entry()])

        with self.assertRaisesRegex(
                VehicleOverlayStoreError, "checksum"):
            VehicleOverlayStore(self.game)

    def test_a_missing_member_fails_closed(self):
        self._write_manifest([_member_entry()])

        with self.assertRaisesRegex(
                VehicleOverlayStoreError, "missing"):
            VehicleOverlayStore(self.game)

    def test_an_unsafe_member_path_fails_closed(self):
        self._write_manifest([_member_entry(
            member="../escape.xml", data=MEMBER_DATA)])

        with self.assertRaisesRegex(
                VehicleOverlayStoreError, "unsafe"):
            VehicleOverlayStore(self.game)

    def test_an_invalid_manifest_fails_closed(self):
        path = os.path.join(self.root, "vehicle_overlays.json")
        with open(path, "wb") as stream:
            stream.write(b"{not json")

        with self.assertRaisesRegex(
                VehicleOverlayStoreError, "invalid"):
            VehicleOverlayStore(self.game)

    def test_an_oversized_member_fails_closed(self):
        oversized = b"x" * (MAX_OVERLAY_MEMBER_BYTES + 1)
        self._write_member(data=oversized)
        entry = _member_entry(data=oversized)
        self._write_manifest([entry])

        with self.assertRaisesRegex(
                VehicleOverlayStoreError, "size"):
            VehicleOverlayStore(self.game)

    def test_store_accepts_the_full_supported_member_count(self):
        raw = _manifest_bytes(_capacity_entries(MAX_OVERLAY_MEMBERS))

        def read_file(path):
            if path.endswith("vehicle_overlays.json"):
                return raw
            return MEMBER_DATA

        with mock.patch(
                "vehicle_overlay_store.os.path.isfile", return_value=True):
            store = VehicleOverlayStore(self.game, read_file=read_file)

        self.assertEqual(MAX_OVERLAY_MEMBERS, store.member_count)
        self.assertLess(len(raw), MAX_OVERLAY_MANIFEST_BYTES)

    def test_store_rejects_one_member_over_the_supported_count(self):
        raw = _manifest_bytes(_capacity_entries(MAX_OVERLAY_MEMBERS + 1))

        with mock.patch(
                "vehicle_overlay_store.os.path.isfile", return_value=True), \
                self.assertRaisesRegex(
                    VehicleOverlayStoreError, "more than 1024 members"):
            VehicleOverlayStore(
                self.game,
                read_file=lambda unused_path: raw)

    def test_store_rejects_a_manifest_above_the_byte_limit(self):
        raw = _manifest_bytes([_member_entry()])

        with mock.patch(
                "vehicle_overlay_store.os.path.isfile", return_value=True), \
                mock.patch(
                    "vehicle_overlay_store.MAX_OVERLAY_MANIFEST_BYTES",
                    len(raw) - 1), self.assertRaisesRegex(
                        VehicleOverlayStoreError,
                        "vehicle_overlays.json is larger"):
            VehicleOverlayStore(
                self.game,
                read_file=lambda unused_path: raw)


class VehicleOverlayProbeExchangeTest(unittest.TestCase):
    """The probe handler serves the pinned overlay over the LAN protocol."""

    def _room(self, store):
        room = ThreadedTCPServer(("127.0.0.1", 0), ClientHandler)
        room.game_server = types.SimpleNamespace(
            state=None, vehicle_overlay=store)
        thread = threading.Thread(
            target=room.serve_forever, name="overlay-test-room",
            daemon=True)
        thread.start()
        self.addCleanup(room.shutdown)
        self.addCleanup(room.server_close)
        return room

    @staticmethod
    def _read_line(connection):
        payload = b""
        while b"\n" not in payload:
            chunk = connection.recv(4096)
            if not chunk:
                raise AssertionError("server closed the connection")
            payload += chunk
        line, separator, unused = payload.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def _probe_hello(self):
        capabilities = [
            "projectile_ledger_v2", "destructible_catalog_v5",
            "ram_contact_ledger_v2", "human_ram_timeline_v1",
            "player_fire_intent_v5", "player_environment_v2",
            "effective_params_v1", "ricochet_continuation_v1",
        ]
        return {
            "type": "hello",
            "protocol": PROTOCOL_VERSION,
            "client_build": CLIENT_BUILD_0922,
            "name": "Launcher-Probe",
            "vehicle": "ussr:R11_MS-1",
            "max_health": 1,
            "role": "probe",
            "vehicle_compact_descr": "AA==",
            "capabilities": capabilities,
        }

    def _exchange(self, store):
        """Run one full probe + overlay fetch against the live handler."""
        room = self._room(store)
        host, port = room.server_address
        connection = socket.create_connection((host, port), timeout=5.0)
        connection.settimeout(5.0)
        self.addCleanup(connection.close)
        try:
            connection.sendall((
                json.dumps(self._probe_hello(), separators=(",", ":")) +
                "\n").encode("utf-8"))
            welcome = self._read_line(connection)
            self.assertEqual("welcome", welcome["type"])
            self.assertIn(
                VEHICLE_OVERLAY_CAPABILITY,
                welcome.get("server_capabilities") or ())
            connection.sendall(b'{"type":"vehicle_overlay_query"}\n')
            manifest = self._read_line(connection)
            return welcome, manifest, connection
        except Exception:
            connection.close()
            raise

    def _member(self, connection, member):
        connection.sendall((json.dumps(
            {"type": "vehicle_overlay_member", "sourceMember": member},
            separators=(",", ":")) + "\n").encode("utf-8"))
        return self._read_line(connection)

    def _game_root(self):
        game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game, True)
        root = os.path.join(game, "res_mods", "0.9.22.0.1")
        os.makedirs(root)
        member_path = os.path.join(root, *MEMBER.split("/"))
        os.makedirs(os.path.dirname(member_path))
        with open(member_path, "wb") as stream:
            stream.write(MEMBER_DATA)
        with open(os.path.join(root, "vehicle_overlays.json"), "wb") as stream:
            stream.write(_manifest_bytes([_member_entry()]))
        return game

    def test_probe_fetches_the_pinned_overlay(self):
        store = VehicleOverlayStore(self._game_root())
        welcome, manifest, connection = self._exchange(store)

        self.assertTrue(manifest["present"])
        self.assertEqual(1, len(manifest["members"]))
        self.assertEqual(MEMBER, manifest["members"][0]["sourceMember"])

        member = self._member(connection, MEMBER)
        self.assertEqual("vehicle_overlay_member_data", member["type"])
        self.assertEqual(MEMBER_DATA, base64.b64decode(
            member["data_b64"].encode("ascii")))
        connection.sendall(b'{"type":"leave"}\n')

    def test_probe_learns_a_room_without_an_overlay(self):
        store = VehicleOverlayStore()
        welcome, manifest, connection = self._exchange(store)

        self.assertFalse(manifest["present"])
        self.assertEqual("", manifest["digest"])
        self.assertEqual([], manifest["members"])
        connection.sendall(b'{"type":"leave"}\n')

    def test_probe_rejects_an_unknown_member(self):
        store = VehicleOverlayStore(self._game_root())
        welcome, manifest, connection = self._exchange(store)

        member = self._member(connection, "scripts/unknown.xml")
        self.assertEqual("error", member["type"])
        self.assertEqual("unknown_member", member["code"])
        connection.sendall(b'{"type":"leave"}\n')


class LauncherFetchIntegrationTest(unittest.TestCase):
    """The launcher's fetch speaks the live server's overlay exchange."""

    def _room(self, store):
        room = ThreadedTCPServer(("127.0.0.1", 0), ClientHandler)
        room.game_server = types.SimpleNamespace(
            state=None, vehicle_overlay=store)
        thread = threading.Thread(
            target=room.serve_forever, name="overlay-integration-room",
            daemon=True)
        thread.start()
        self.addCleanup(room.shutdown)
        self.addCleanup(room.server_close)
        return room

    def _game_root(self):
        game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game, True)
        root = os.path.join(game, "res_mods", "0.9.22.0.1")
        os.makedirs(root)
        member_path = os.path.join(root, *MEMBER.split("/"))
        os.makedirs(os.path.dirname(member_path))
        with open(member_path, "wb") as stream:
            stream.write(MEMBER_DATA)
        with open(os.path.join(root, "vehicle_overlays.json"), "wb") as stream:
            stream.write(_manifest_bytes([_member_entry()]))
        return game

    def test_launcher_fetch_reads_the_live_server_overlay(self):
        store = VehicleOverlayStore(self._game_root())
        room = self._room(store)
        host, port = room.server_address

        result = launcher_core.fetch_vehicle_overlay(host, port)

        self.assertTrue(result["supported"])
        self.assertTrue(result["present"])
        self.assertEqual(store.digest, result["digest"])
        self.assertEqual("Fast MS-1", result["profile"])
        self.assertEqual({MEMBER: MEMBER_DATA}, result["payload"])
        self.assertEqual(
            MEMBER, result["manifest"]["members"][0]["sourceMember"])

    def test_launcher_probe_accepts_the_live_room(self):
        store = VehicleOverlayStore(self._game_root())
        room = self._room(store)
        host, port = room.server_address

        self.assertTrue(launcher_core.probe_server_protocol(
            launcher_core.PORT_0_9_22, host, port))
        # The probe connection closes cleanly; a later fetch still works.
        result = launcher_core.fetch_vehicle_overlay(host, port)
        self.assertTrue(result["present"])

    def test_launcher_fetch_accepts_a_room_without_an_overlay(self):
        store = VehicleOverlayStore()
        room = self._room(store)
        host, port = room.server_address

        result = launcher_core.fetch_vehicle_overlay(host, port)

        self.assertTrue(result["supported"])
        self.assertFalse(result["present"])
        self.assertEqual("", result["digest"])
        self.assertEqual({}, result["payload"])


if __name__ == "__main__":
    unittest.main()
