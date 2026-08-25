//! Pinned launcher-owned vehicle-data overlay served to LAN joiners.
//!
//! The launcher materializes one profile below the host game root before the
//! server starts. Rust reads and verifies that immutable snapshot once; the
//! hidden native oracle and every visible client then load the same files.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use thiserror::Error;

pub const VEHICLE_OVERLAY_CAPABILITY: &str = "vehicle_overlay_v1";
pub const MAX_OVERLAY_MEMBERS: usize = 1_024;
pub const MAX_OVERLAY_MANIFEST_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_OVERLAY_MEMBER_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_OVERLAY_TOTAL_BYTES: usize = 64 * 1024 * 1024;
/// One member is returned as base64 inside one compact JSON line.
pub const MAX_OVERLAY_LINE_BYTES: usize = MAX_OVERLAY_MEMBER_BYTES * 4 / 3 + 1024 * 1024;

const VERSION_DIRECTORY: &str = "0.9.22.0.1";
const MANIFEST_NAME: &str = "vehicle_overlays.json";

#[derive(Debug, Error)]
pub enum VehicleOverlayError {
    #[error("vehicle_overlays.json is unreadable: {0}")]
    ManifestIo(#[source] io::Error),
    #[error("vehicle_overlays.json is larger than 32 MiB")]
    ManifestTooLarge,
    #[error("vehicle_overlays.json is invalid: {0}")]
    InvalidManifest(String),
    #[error("overlay member path is unsafe: {0:?}")]
    UnsafeMember(String),
    #[error("overlay manifest repeats member {0}")]
    DuplicateMember(String),
    #[error("overlay member is missing or not a regular file: {0}")]
    MissingMember(String),
    #[error("overlay member is unreadable: {member}: {source}")]
    MemberIo {
        member: String,
        #[source]
        source: io::Error,
    },
    #[error("overlay member size is invalid: {0}")]
    InvalidMemberSize(String),
    #[error("overlay is larger than 64 MiB")]
    OverlayTooLarge,
    #[error("overlay member failed its checksum: {0}")]
    ChecksumMismatch(String),
}

#[derive(Clone, Debug)]
pub struct VehicleOverlayStore {
    present: bool,
    digest: String,
    profile: String,
    manifest: Option<Value>,
    entries: Vec<Value>,
    members: BTreeMap<String, Vec<u8>>,
}

impl VehicleOverlayStore {
    pub fn empty() -> Self {
        Self {
            present: false,
            digest: String::new(),
            profile: String::new(),
            manifest: None,
            entries: Vec::new(),
            members: BTreeMap::new(),
        }
    }

    pub fn load(game_root: Option<&Path>) -> Result<Self, VehicleOverlayError> {
        let Some(game_root) = game_root else {
            return Ok(Self::empty());
        };
        let overlay_root = game_root.join("res_mods").join(VERSION_DIRECTORY);
        let manifest_path = overlay_root.join(MANIFEST_NAME);
        if !manifest_path.is_file() {
            return Ok(Self::empty());
        }
        let raw_manifest = read_bounded(&manifest_path, MAX_OVERLAY_MANIFEST_BYTES).map_err(
            |error| match error {
                BoundedReadError::Io(error) => VehicleOverlayError::ManifestIo(error),
                BoundedReadError::TooLarge => VehicleOverlayError::ManifestTooLarge,
            },
        )?;
        let manifest: Value = serde_json::from_slice(&raw_manifest)
            .map_err(|error| VehicleOverlayError::InvalidManifest(error.to_string()))?;
        let object = manifest.as_object().ok_or_else(|| {
            VehicleOverlayError::InvalidManifest("manifest must be an object".to_owned())
        })?;
        let rows = object
            .get("members")
            .and_then(Value::as_array)
            .filter(|rows| rows.len() <= MAX_OVERLAY_MEMBERS)
            .ok_or_else(|| {
                VehicleOverlayError::InvalidManifest(
                    "member list is missing, invalid, or too large".to_owned(),
                )
            })?;

        let mut members = BTreeMap::new();
        let mut entries = Vec::with_capacity(rows.len());
        let mut seen = BTreeSet::new();
        let mut total_bytes = 0usize;
        for row in rows {
            let entry = row.as_object().ok_or_else(|| {
                VehicleOverlayError::InvalidManifest("member entry is not an object".to_owned())
            })?;
            let member = entry
                .get("sourceMember")
                .and_then(Value::as_str)
                .ok_or_else(|| {
                    VehicleOverlayError::InvalidManifest(
                        "member entry has no sourceMember".to_owned(),
                    )
                })?;
            validate_member(member)?;
            if !seen.insert(member.to_owned()) {
                return Err(VehicleOverlayError::DuplicateMember(member.to_owned()));
            }
            let expected = entry
                .get("overlaySha256")
                .and_then(Value::as_str)
                .filter(|value| valid_digest(value))
                .ok_or_else(|| {
                    VehicleOverlayError::InvalidManifest(format!(
                        "member {member} has an invalid checksum"
                    ))
                })?;
            let path = member_path(&overlay_root, member);
            let metadata = fs::symlink_metadata(&path)
                .map_err(|_| VehicleOverlayError::MissingMember(member.to_owned()))?;
            if metadata.file_type().is_symlink() || !metadata.is_file() {
                return Err(VehicleOverlayError::MissingMember(member.to_owned()));
            }
            let data =
                read_bounded(&path, MAX_OVERLAY_MEMBER_BYTES).map_err(|error| match error {
                    BoundedReadError::Io(source) => VehicleOverlayError::MemberIo {
                        member: member.to_owned(),
                        source,
                    },
                    BoundedReadError::TooLarge => {
                        VehicleOverlayError::InvalidMemberSize(member.to_owned())
                    }
                })?;
            if data.is_empty() {
                return Err(VehicleOverlayError::InvalidMemberSize(member.to_owned()));
            }
            total_bytes = total_bytes
                .checked_add(data.len())
                .ok_or(VehicleOverlayError::OverlayTooLarge)?;
            if total_bytes > MAX_OVERLAY_TOTAL_BYTES {
                return Err(VehicleOverlayError::OverlayTooLarge);
            }
            let actual = sha256(&data);
            if actual != expected {
                return Err(VehicleOverlayError::ChecksumMismatch(member.to_owned()));
            }
            entries.push(json!({
                "sourceMember": member,
                "overlaySha256": expected,
                "size": data.len(),
            }));
            members.insert(member.to_owned(), data);
        }

        Ok(Self {
            present: true,
            digest: sha256(&raw_manifest),
            profile: object
                .get("activeProfile")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            manifest: Some(manifest),
            entries,
            members,
        })
    }

    pub fn present(&self) -> bool {
        self.present
    }

    pub fn digest(&self) -> &str {
        &self.digest
    }

    pub fn profile(&self) -> &str {
        &self.profile
    }

    pub fn member_count(&self) -> usize {
        self.members.len()
    }

    pub fn manifest_payload(&self) -> Value {
        json!({
            "type": "vehicle_overlay_manifest",
            "present": self.present,
            "digest": self.digest,
            "profile": self.profile,
            "manifest": self.manifest,
            "members": self.entries,
        })
    }

    pub fn member_payload(&self, member: &str) -> Option<Value> {
        let data = self.members.get(member)?;
        Some(json!({
            "type": "vehicle_overlay_member_data",
            "sourceMember": member,
            "size": data.len(),
            "sha256": sha256(data),
            "data_b64": BASE64_STANDARD.encode(data),
        }))
    }
}

#[derive(Debug)]
enum BoundedReadError {
    Io(io::Error),
    TooLarge,
}

fn read_bounded(path: &Path, maximum: usize) -> Result<Vec<u8>, BoundedReadError> {
    let mut file = File::open(path).map_err(BoundedReadError::Io)?;
    let take_limit = u64::try_from(maximum).unwrap_or(u64::MAX).saturating_add(1);
    let mut data = Vec::new();
    file.by_ref()
        .take(take_limit)
        .read_to_end(&mut data)
        .map_err(BoundedReadError::Io)?;
    if data.len() > maximum {
        return Err(BoundedReadError::TooLarge);
    }
    Ok(data)
}

fn member_path(root: &Path, member: &str) -> PathBuf {
    member
        .split('/')
        .fold(root.to_path_buf(), |path, component| path.join(component))
}

fn validate_member(member: &str) -> Result<(), VehicleOverlayError> {
    let valid = !member.is_empty()
        && member.split('/').all(|component| {
            let mut bytes = component.bytes();
            bytes
                .next()
                .is_some_and(|byte| byte.is_ascii_alphanumeric())
                && bytes
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
        });
    if valid {
        Ok(())
    } else {
        Err(VehicleOverlayError::UnsafeMember(member.to_owned()))
    }
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn sha256(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(1);

    struct Fixture {
        root: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let sequence = NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed);
            let root = std::env::temp_dir().join(format!(
                "offline-rust-overlay-test-{}-{sequence}",
                std::process::id()
            ));
            fs::create_dir_all(&root).unwrap();
            Self { root }
        }

        fn overlay_root(&self) -> PathBuf {
            self.root.join("res_mods").join(VERSION_DIRECTORY)
        }

        fn install(&self, member: &str, data: &[u8], checksum: &str) {
            let overlay_root = self.overlay_root();
            let member_path = member_path(&overlay_root, member);
            fs::create_dir_all(member_path.parent().unwrap()).unwrap();
            fs::write(&member_path, data).unwrap();
            let manifest = json!({
                "schema": 1,
                "activeProfile": "Fast MS-1",
                "members": [{
                    "sourceMember": member,
                    "overlaySha256": checksum,
                }],
            });
            fs::write(
                overlay_root.join(MANIFEST_NAME),
                serde_json::to_vec(&manifest).unwrap(),
            )
            .unwrap();
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    fn absent_manifest_is_stock_data() {
        let fixture = Fixture::new();
        let store = VehicleOverlayStore::load(Some(&fixture.root)).unwrap();
        assert!(!store.present());
        assert_eq!(store.member_count(), 0);
        assert_eq!(store.manifest_payload()["present"], false);
    }

    #[test]
    fn pinned_overlay_verifies_and_serves_exact_member() {
        let fixture = Fixture::new();
        let member = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml";
        let data = b"packed-vehicle-data";
        fixture.install(member, data, &sha256(data));

        let store = VehicleOverlayStore::load(Some(&fixture.root)).unwrap();
        assert!(store.present());
        assert_eq!(store.profile(), "Fast MS-1");
        assert_eq!(store.member_count(), 1);
        let payload = store.member_payload(member).unwrap();
        assert_eq!(payload["size"], data.len());
        assert_eq!(
            BASE64_STANDARD
                .decode(payload["data_b64"].as_str().unwrap())
                .unwrap(),
            data
        );
    }

    #[test]
    fn checksum_mismatch_fails_the_room_startup() {
        let fixture = Fixture::new();
        let member = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml";
        fixture.install(member, b"data", &"0".repeat(64));

        assert!(matches!(
            VehicleOverlayStore::load(Some(&fixture.root)),
            Err(VehicleOverlayError::ChecksumMismatch(value)) if value == member
        ));
    }

    #[test]
    fn unsafe_member_never_escapes_the_overlay_root() {
        let fixture = Fixture::new();
        fixture.install("../outside.xml", b"data", &sha256(b"data"));

        assert!(matches!(
            VehicleOverlayStore::load(Some(&fixture.root)),
            Err(VehicleOverlayError::UnsafeMember(value)) if value == "../outside.xml"
        ));
    }
}
