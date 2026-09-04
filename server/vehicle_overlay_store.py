#!/usr/bin/env python3
"""Engine-free store of the host's vehicle-data overlay.

The room host's launcher materializes a vehicle profile as temporary
``res_mods/0.9.22.0.1`` overlay files plus a ``vehicle_overlays.json``
manifest.  This store loads exactly that installed overlay once when the
server starts, verifies every member against the manifest checksum, and serves
it to joining launchers so every client runs the same modified vehicle data.

The store is deliberately small and self-contained: the server process never
loads client packages, and the manifest format is owned by the launcher's
``vehicle_overlays`` module.  A corrupt or foreign manifest fails closed so a
room never distributes data whose identity is uncertain.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re

MANIFEST_RELATIVE = ("res_mods", "0.9.22.0.1", "vehicle_overlays.json")
OVERLAY_ROOT_RELATIVE = ("res_mods", "0.9.22.0.1")
MAX_OVERLAY_MEMBERS = 1024
MAX_OVERLAY_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_OVERLAY_MEMBER_BYTES = 8 * 1024 * 1024
MAX_OVERLAY_TOTAL_BYTES = 64 * 1024 * 1024

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)*$")


class VehicleOverlayStoreError(Exception):
    pass


def _validate_member(member):
    if not isinstance(member, str) or not _SAFE_MEMBER.fullmatch(member):
        raise VehicleOverlayStoreError(
            "an overlay member path is unsafe: %r" % (member,))


class VehicleOverlayStore(object):
    """One pinned vehicle-data overlay, or an empty store for stock data."""

    def __init__(self, game_root=None, read_file=None, sha256=None):
        self._game_root = game_root
        self.present = False
        self.digest = ""
        self.profile = ""
        self._manifest = None
        self._members = {}
        self._entries = []
        self._read_file = read_file or _read_file
        self._sha256 = sha256 or _sha256
        if game_root:
            self._load(os.path.abspath(game_root))

    def _load(self, game_root):
        manifest_path = os.path.join(game_root, *MANIFEST_RELATIVE)
        if not os.path.isfile(manifest_path):
            return
        try:
            raw = self._read_file(manifest_path)
        except (IOError, OSError) as error:
            raise VehicleOverlayStoreError(
                "vehicle_overlays.json is unreadable: %s" % error)
        if len(raw) > MAX_OVERLAY_MANIFEST_BYTES:
            raise VehicleOverlayStoreError(
                "vehicle_overlays.json is larger than %d MiB." %
                (MAX_OVERLAY_MANIFEST_BYTES // (1024 * 1024)))
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (TypeError, ValueError) as error:
            raise VehicleOverlayStoreError(
                "vehicle_overlays.json is invalid: %s" % error)
        if not isinstance(manifest, dict):
            raise VehicleOverlayStoreError(
                "vehicle_overlays.json must be an object.")
        members = manifest.get("members")
        if not isinstance(members, list):
            raise VehicleOverlayStoreError(
                "the overlay manifest member list is invalid.")
        if len(members) > MAX_OVERLAY_MEMBERS:
            raise VehicleOverlayStoreError(
                "the overlay manifest contains more than %d members." %
                MAX_OVERLAY_MEMBERS)
        root = os.path.join(game_root, *OVERLAY_ROOT_RELATIVE)
        loaded = {}
        entries = []
        total = 0
        for entry in members:
            member = entry.get("sourceMember") if isinstance(entry, dict) else None
            _validate_member(member)
            if member in loaded:
                raise VehicleOverlayStoreError(
                    "the overlay manifest repeats a member: %s" % member)
            checksum = entry.get("overlaySha256")
            if not _DIGEST.fullmatch(str(checksum or "")):
                raise VehicleOverlayStoreError(
                    "the overlay manifest checksum is invalid: %s" % member)
            path = os.path.join(root, *member.split("/"))
            if os.path.islink(path) or not os.path.isfile(path):
                raise VehicleOverlayStoreError(
                    "the overlay member is missing: %s" % member)
            try:
                data = self._read_file(path)
            except (IOError, OSError) as error:
                raise VehicleOverlayStoreError(
                    "the overlay member is unreadable: %s (%s)" % (member, error))
            if not data or len(data) > MAX_OVERLAY_MEMBER_BYTES:
                raise VehicleOverlayStoreError(
                    "the overlay member size is invalid: %s" % member)
            total += len(data)
            if total > MAX_OVERLAY_TOTAL_BYTES:
                raise VehicleOverlayStoreError(
                    "the overlay is too large to share.")
            if self._sha256(data) != checksum:
                raise VehicleOverlayStoreError(
                    "the overlay member failed its checksum: %s" % member)
            loaded[member] = data
            entries.append({
                "sourceMember": member,
                "overlaySha256": checksum,
                "size": len(data),
            })
        self.present = True
        self.digest = self._sha256(raw)
        self.profile = str(manifest.get("activeProfile") or "")
        self._manifest = manifest
        self._members = loaded
        self._entries = entries

    def manifest_payload(self):
        """One wire reply describing the pinned overlay (or its absence)."""
        return {
            "type": "vehicle_overlay_manifest",
            "present": self.present,
            "digest": self.digest,
            "profile": self.profile,
            "manifest": self._manifest,
            "members": list(self._entries),
        }

    @property
    def member_count(self):
        return len(self._members)

    def member_payload(self, member):
        """Return the wire payload for one member, or None when unknown."""
        if not isinstance(member, str):
            return None
        data = self._members.get(member)
        if data is None:
            return None
        return {
            "type": "vehicle_overlay_member_data",
            "sourceMember": member,
            "size": len(data),
            "sha256": self._sha256(data),
            "data_b64": base64.b64encode(data).decode("ascii"),
        }


def _read_file(path):
    with open(path, "rb") as stream:
        return stream.read()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()
