use crate::combat::{VehicleKey, VehicleKind, MAX_COMBAT_ID};
use crate::combat_rules::{CombatRuleError, HeTuning};
use crate::critical_damage::MAX_CRITICAL_DEVICE_HP;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

pub const MAX_ACTIVE_PROJECTILES: usize = 128;
pub const MAX_PROJECTILES_PER_SHOOTER: usize = 32;
pub const MAX_PROGRESS_BATCH: usize = 30;
pub const MAX_PROJECTILE_LIFETIME_MS: u64 = 20_000;
/// The round-relative clock ends after 15 seconds of countdown plus the
/// 15-minute battle timer. A canonical stun end beyond that boundary could
/// never expire inside the owning round.
pub const MAX_STUN_END_SERVER_TIME_MS: u64 = (15 + 15 * 60) * 1_000;
pub const PROJECTILE_CLOCK_LEEWAY_MS: u64 = 250;
pub const PROJECTILE_TOLERANCE: f64 = 0.001;
pub const RICOCHET_ORIGIN_OFFSET_M: f64 = 0.002;
pub const RICOCHET_ORIGIN_TOLERANCE_M: f64 = 0.1;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProjectileVec3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl ProjectileVec3 {
    pub fn magnitude(self) -> f64 {
        (self.x * self.x + self.y * self.y + self.z * self.z).sqrt()
    }

    fn finite(self) -> bool {
        [self.x, self.y, self.z].into_iter().all(f64::is_finite)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SourceShell {
    pub kind: String,
    pub caliber: f64,
    pub damage: [f64; 2],
    #[serde(rename = "explosionRadius")]
    pub explosion_radius: f64,
    #[serde(
        rename = "explosionDamageFactor",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    pub explosion_damage_factor: Option<f64>,
    #[serde(
        rename = "explosionDamageAbsorptionFactor",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    pub explosion_damage_absorption_factor: Option<f64>,
    #[serde(
        rename = "explosionEdgeDamageFactor",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    pub explosion_edge_damage_factor: Option<f64>,
}

impl SourceShell {
    /// Return the exact descriptor-owned HE law while rejecting partial HE
    /// projections and HE-only fields attached to any solid shell kind.
    pub fn he_tuning(&self) -> Result<Option<HeTuning>, CombatRuleError> {
        match (
            self.explosion_damage_factor,
            self.explosion_damage_absorption_factor,
            self.explosion_edge_damage_factor,
        ) {
            (Some(damage), Some(absorption), Some(edge)) if self.kind == "HIGH_EXPLOSIVE" => {
                HeTuning::new(damage, absorption, edge).map(Some)
            }
            (None, None, None) if self.kind != "HIGH_EXPLOSIVE" => Ok(None),
            _ => Err(CombatRuleError::InvalidHeTuning),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SourceShot {
    pub speed: f64,
    pub gravity: f64,
    #[serde(rename = "maxDistance")]
    pub max_distance: f64,
    #[serde(rename = "piercingPower")]
    pub piercing_power: [f64; 2],
    pub deadeye: bool,
    pub shell: SourceShell,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProjectileLaunch {
    pub round_id: u64,
    pub authority_epoch: u64,
    pub shooter: VehicleKey,
    pub shot_seq: u64,
    pub shell_index: u8,
    pub origin: ProjectileVec3,
    pub velocity: ProjectileVec3,
    pub gravity: f64,
    pub max_distance: f64,
    pub max_time_ms: u64,
    pub is_he: bool,
    pub splash_radius: f64,
    pub penetration_factor: f64,
    pub damage_factor: f64,
    pub source_shot: SourceShot,
    pub fire_intent_seq: Option<u64>,
    pub fire_input_seq: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LaunchContext {
    pub round_id: u64,
    pub authority_epoch: u64,
    pub shooter: VehicleKey,
    pub team: u8,
    pub source_vehicle: String,
    pub expected_shot_seq: u64,
    pub server_time_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ProjectileRecord {
    pub projectile_id: String,
    pub launch: ProjectileLaunch,
    pub team: u8,
    pub source_vehicle: String,
    pub launch_server_time_ms: u64,
    pub checked_through_ms: u64,
    pub checked_distance: f64,
    pub piercing_loss: f64,
    pub segment_origin: ProjectileVec3,
    pub segment_velocity: ProjectileVec3,
    pub segment_start_time_ms: u64,
    pub ricochet_count: u8,
    pub base_penetration_multiplier: f64,
    launch_fingerprint: String,
    last_progress_fingerprint: Option<String>,
    last_ricochet_fingerprint: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProjectileCursor {
    pub projectile_id: String,
    pub base_checked_ms: u64,
    pub checked_through_ms: u64,
    pub checked_distance: f64,
    pub piercing_loss: f64,
    pub penetration_factor: f64,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProjectileOutcome {
    Impact,
    Miss,
    Expired,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProjectileResolution {
    pub round_id: u64,
    pub authority_epoch: u64,
    pub projectile_id: String,
    pub base_checked_ms: u64,
    pub outcome: ProjectileOutcome,
    pub resolved_time_ms: u64,
    pub checked_distance: f64,
    pub piercing_loss: f64,
    pub penetration_factor: f64,
    pub impact: Option<ProjectileVec3>,
}

/// First-ricochet continuation geometry decided by Rust from one native
/// contact normal. Damage/destructible effects remain in the battle layer.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProjectileRicochet {
    pub round_id: u64,
    pub authority_epoch: u64,
    pub projectile_id: String,
    pub base_checked_ms: u64,
    pub resolved_time_ms: u64,
    pub checked_distance: f64,
    pub piercing_loss: f64,
    pub penetration_factor: f64,
    pub impact: ProjectileVec3,
    pub segment_origin: ProjectileVec3,
    pub segment_velocity: ProjectileVec3,
    pub base_penetration_multiplier: f64,
}

/// One Rust-resolved stun end time for a projectile target. Native code never
/// supplies this value; the future descriptor-backed stun formula feeds this
/// type after Rust has decided the duration and overlap semantics.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProjectileStunTarget {
    pub target: VehicleKey,
    pub end_server_time_ms: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProjectileStunState {
    pub target: VehicleKey,
    pub attacker: VehicleKey,
    pub end_server_time_ms: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProjectileStunBatchAdmission {
    pub applied: usize,
    pub exact_retries: usize,
    pub activated: Vec<ProjectileStunState>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProjectileStunClearAdmission {
    Cleared { previous: ProjectileStunState },
    ExactRetry,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ProjectileStunError {
    #[error("projectile stun scope is invalid")]
    InvalidScope,
    #[error("projectile stun clock is invalid")]
    InvalidClock,
    #[error("projectile stun tick {received} does not follow {current}")]
    TickSequence { current: u64, received: u64 },
    #[error("projectile stun batch must contain 1..=30 unique targets")]
    InvalidBatch,
    #[error("projectile stun target or attacker identity is invalid")]
    InvalidVehicle,
    #[error("projectile stun end time is not after the frozen resolution clock")]
    InvalidEndTime,
    #[error("projectile stun identity was reused with different content")]
    ConflictingRetry,
    #[error("medkit stun-clear identity is invalid")]
    InvalidClearIdentity,
    #[error("medkit stun clear lost its end-time compare-and-swap")]
    ClearCasConflict,
    #[error("projectile stun revision is exhausted")]
    RevisionExhausted,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ProjectileStunReceipt {
    target: ProjectileStunTarget,
    attacker: VehicleKey,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ProjectileStunClearReceipt {
    base_end_server_time_ms: u64,
}

/// Durable, fixed-tick stun state detached from battle-loop integration.
///
/// The ledger is cloneable so a projectile terminal can stage hull, critical,
/// stun, statistics, and replication mutations before publishing any of them.
#[derive(Clone, Debug)]
pub struct ProjectileStunLedger {
    round_id: u64,
    authority_epoch: u64,
    current_tick: u64,
    current_server_time_ms: u64,
    revision: u64,
    active: BTreeMap<VehicleKey, ProjectileStunState>,
    receipts: BTreeMap<String, Vec<ProjectileStunReceipt>>,
    clear_receipts: BTreeMap<(VehicleKey, u64), ProjectileStunClearReceipt>,
}

impl ProjectileStunLedger {
    pub fn new(
        round_id: u64,
        authority_epoch: u64,
        current_tick: u64,
        current_server_time_ms: u64,
    ) -> Result<Self, ProjectileStunError> {
        if round_id == 0 {
            return Err(ProjectileStunError::InvalidScope);
        }
        Ok(Self {
            round_id,
            authority_epoch,
            current_tick,
            current_server_time_ms,
            revision: 0,
            active: BTreeMap::new(),
            receipts: BTreeMap::new(),
            clear_receipts: BTreeMap::new(),
        })
    }

    pub fn current_tick(&self) -> u64 {
        self.current_tick
    }

    pub fn current_server_time_ms(&self) -> u64 {
        self.current_server_time_ms
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }

    pub fn state(&self, target: VehicleKey) -> Option<&ProjectileStunState> {
        self.active.get(&target)
    }

    pub fn active_assister(&self, target: VehicleKey) -> Option<VehicleKey> {
        self.active_assister_at(target, self.current_server_time_ms)
    }

    pub fn active_assister_at(
        &self,
        target: VehicleKey,
        server_time_ms: u64,
    ) -> Option<VehicleKey> {
        self.active
            .get(&target)
            .filter(|state| state.end_server_time_ms > server_time_ms)
            .map(|state| state.attacker)
    }

    /// Atomically apply every direct/splash stun decided for one projectile at
    /// the ledger's single frozen resolution clock.
    pub fn apply_projectile_batch(
        &mut self,
        record: &ProjectileRecord,
        targets: &[ProjectileStunTarget],
    ) -> Result<ProjectileStunBatchAdmission, ProjectileStunError> {
        if record.launch.round_id != self.round_id
            || record.launch.authority_epoch != self.authority_epoch
            || record.projectile_id.is_empty()
            || record.projectile_id.len() > 96
            || record.projectile_id
                != projectile_id(
                    record.launch.round_id,
                    record.launch.shooter,
                    record.launch.shot_seq,
                )
        {
            return Err(ProjectileStunError::InvalidScope);
        }
        validate_stun_vehicle(record.launch.shooter)?;
        if targets.is_empty() || targets.len() > MAX_PROGRESS_BATCH {
            return Err(ProjectileStunError::InvalidBatch);
        }

        let mut normalized = targets.to_vec();
        normalized.sort_by_key(|target| target.target);
        if normalized
            .windows(2)
            .any(|pair| pair[0].target == pair[1].target)
        {
            return Err(ProjectileStunError::InvalidBatch);
        }

        let receipt = normalized
            .iter()
            .copied()
            .map(|target| ProjectileStunReceipt {
                target,
                attacker: record.launch.shooter,
            })
            .collect::<Vec<_>>();
        if let Some(previous) = self.receipts.get(&record.projectile_id) {
            return if previous == &receipt {
                Ok(ProjectileStunBatchAdmission {
                    applied: 0,
                    exact_retries: receipt.len(),
                    activated: Vec::new(),
                })
            } else {
                Err(ProjectileStunError::ConflictingRetry)
            };
        }

        let mut staged = self.clone();
        let mut activated = Vec::with_capacity(normalized.len());
        for target in normalized {
            validate_stun_vehicle(target.target)?;
            if target.end_server_time_ms <= staged.current_server_time_ms
                || target.end_server_time_ms > MAX_STUN_END_SERVER_TIME_MS
            {
                return Err(ProjectileStunError::InvalidEndTime);
            }
            let state = ProjectileStunState {
                target: target.target,
                attacker: record.launch.shooter,
                end_server_time_ms: target.end_server_time_ms,
            };
            staged.active.insert(target.target, state.clone());
            activated.push(state);
        }
        staged
            .receipts
            .insert(record.projectile_id.clone(), receipt);
        staged.revision = staged
            .revision
            .checked_add(1)
            .ok_or(ProjectileStunError::RevisionExhausted)?;
        let applied = activated.len();
        *self = staged;
        Ok(ProjectileStunBatchAdmission {
            applied,
            exact_retries: 0,
            activated,
        })
    }

    /// Clear one stun only when the equipment intent still observes the exact
    /// end time it was admitted against.
    pub fn clear_medkit(
        &mut self,
        target: VehicleKey,
        equipment_intent_seq: u64,
        base_end_server_time_ms: u64,
    ) -> Result<ProjectileStunClearAdmission, ProjectileStunError> {
        validate_stun_vehicle(target)?;
        if equipment_intent_seq == 0
            || equipment_intent_seq > MAX_COMBAT_ID
            || base_end_server_time_ms == 0
        {
            return Err(ProjectileStunError::InvalidClearIdentity);
        }
        let key = (target, equipment_intent_seq);
        let receipt = ProjectileStunClearReceipt {
            base_end_server_time_ms,
        };
        if let Some(previous) = self.clear_receipts.get(&key) {
            return if previous == &receipt {
                Ok(ProjectileStunClearAdmission::ExactRetry)
            } else {
                Err(ProjectileStunError::ConflictingRetry)
            };
        }
        let previous = self
            .active
            .get(&target)
            .filter(|state| state.end_server_time_ms == base_end_server_time_ms)
            .cloned()
            .ok_or(ProjectileStunError::ClearCasConflict)?;
        let revision = self
            .revision
            .checked_add(1)
            .ok_or(ProjectileStunError::RevisionExhausted)?;
        self.active.remove(&target);
        self.clear_receipts.insert(key, receipt);
        self.revision = revision;
        Ok(ProjectileStunClearAdmission::Cleared { previous })
    }

    /// Remove stun from a vehicle that is no longer a live combat target.
    /// Death is already a canonical terminal edge, so it needs no separate
    /// retry identity; an absent state is an exact no-op.
    pub fn clear_terminal(
        &mut self,
        target: VehicleKey,
    ) -> Result<Option<ProjectileStunState>, ProjectileStunError> {
        validate_stun_vehicle(target)?;
        let Some(previous) = self.active.get(&target).cloned() else {
            return Ok(None);
        };
        let revision = self
            .revision
            .checked_add(1)
            .ok_or(ProjectileStunError::RevisionExhausted)?;
        self.active.remove(&target);
        self.revision = revision;
        Ok(Some(previous))
    }

    /// Advance exactly one simulation tick and return the prior states cleared
    /// at this boundary, in stable target order.
    pub fn advance_tick(
        &mut self,
        tick: u64,
        server_time_ms: u64,
    ) -> Result<Vec<ProjectileStunState>, ProjectileStunError> {
        let expected = self
            .current_tick
            .checked_add(1)
            .ok_or(ProjectileStunError::InvalidClock)?;
        if tick != expected {
            return Err(ProjectileStunError::TickSequence {
                current: self.current_tick,
                received: tick,
            });
        }
        if server_time_ms < self.current_server_time_ms {
            return Err(ProjectileStunError::InvalidClock);
        }
        let expired = self
            .active
            .values()
            .filter(|state| state.end_server_time_ms <= server_time_ms)
            .cloned()
            .collect::<Vec<_>>();
        let revision = if expired.is_empty() {
            self.revision
        } else {
            self.revision
                .checked_add(1)
                .ok_or(ProjectileStunError::RevisionExhausted)?
        };
        for state in &expired {
            self.active.remove(&state.target);
        }
        self.current_tick = tick;
        self.current_server_time_ms = server_time_ms;
        self.revision = revision;
        Ok(expired)
    }
}

fn validate_stun_vehicle(vehicle: VehicleKey) -> Result<(), ProjectileStunError> {
    if vehicle.id == 0 || vehicle.id > MAX_COMBAT_ID {
        Err(ProjectileStunError::InvalidVehicle)
    } else {
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum LaunchAdmission {
    New(ProjectileRecord),
    ExactRetry { projectile_id: String },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProgressAdmission {
    Applied { changed: usize },
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ProjectileTombstone {
    pub projectile_id: String,
    pub outcome: ProjectileOutcome,
    launch_fingerprint: String,
    request_fingerprint: String,
}

#[derive(Clone, Debug, PartialEq)]
pub enum ResolutionAdmission {
    Applied {
        record: ProjectileRecord,
        outcome: ProjectileOutcome,
        impact: Option<ProjectileVec3>,
    },
    ExactRetry,
}

#[derive(Clone, Debug, PartialEq)]
pub enum RicochetAdmission {
    Applied { record: ProjectileRecord },
    ExactRetry,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ProjectileError {
    #[error("projectile message belongs to the wrong round or authority epoch")]
    Lineage,
    #[error("projectile launch shape or physics is invalid")]
    InvalidLaunch,
    #[error("projectile launch is not bound to its canonical shooter state")]
    LaunchBinding,
    #[error("active projectile capacity is exhausted")]
    Capacity,
    #[error("projectile identity was reused with conflicting launch content")]
    ConflictingLaunch,
    #[error("projectile progress batch must contain 1..=30 unique cursors")]
    InvalidProgressBatch,
    #[error("unknown projectile {projectile_id}")]
    Unknown { projectile_id: String },
    #[error("projectile {projectile_id} cursor compare-and-swap failed")]
    CursorConflict { projectile_id: String },
    #[error("projectile {projectile_id} progress is invalid")]
    InvalidProgress { projectile_id: String },
    #[error("projectile terminal result is invalid")]
    InvalidResolution,
    #[error("projectile terminal identity was reused with conflicting content")]
    ConflictingResolution,
    #[error("projectile ricochet continuation is invalid")]
    InvalidRicochet,
    #[error("projectile already consumed its one permitted ricochet")]
    RicochetLimit,
    #[error("projectile ricochet identity was reused with conflicting content")]
    ConflictingRicochet,
}

#[derive(Clone, Debug, Default)]
pub struct ProjectileLedger {
    active: BTreeMap<String, ProjectileRecord>,
    tombstones: BTreeMap<String, ProjectileTombstone>,
    revision: u64,
}

impl ProjectileLedger {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }

    pub fn active(&self) -> &BTreeMap<String, ProjectileRecord> {
        &self.active
    }

    pub fn tombstone(&self, projectile_id: &str) -> Option<&ProjectileTombstone> {
        self.tombstones.get(projectile_id)
    }

    pub fn admit_launch(
        &mut self,
        mut launch: ProjectileLaunch,
        context: LaunchContext,
    ) -> Result<LaunchAdmission, ProjectileError> {
        if launch.round_id != context.round_id || launch.authority_epoch != context.authority_epoch
        {
            return Err(ProjectileError::Lineage);
        }
        normalize_launch(&mut launch)?;
        if launch.shooter != context.shooter
            || launch.shot_seq != context.expected_shot_seq
            || !matches!(context.team, 1 | 2)
            || context.source_vehicle.is_empty()
            || context.source_vehicle.len() > 128
        {
            return Err(ProjectileError::LaunchBinding);
        }
        match launch.shooter.kind {
            VehicleKind::Player => {
                if launch.fire_intent_seq.is_none() || launch.fire_input_seq.is_none() {
                    return Err(ProjectileError::LaunchBinding);
                }
            }
            VehicleKind::Bot => {
                if launch.fire_intent_seq.is_some() || launch.fire_input_seq.is_some() {
                    return Err(ProjectileError::LaunchBinding);
                }
            }
        }

        let projectile_id = projectile_id(launch.round_id, launch.shooter, launch.shot_seq);
        let launch_fingerprint = fingerprint(&launch);
        if let Some(active) = self.active.get(&projectile_id) {
            return if active.launch_fingerprint == launch_fingerprint {
                Ok(LaunchAdmission::ExactRetry { projectile_id })
            } else {
                Err(ProjectileError::ConflictingLaunch)
            };
        }
        if let Some(terminal) = self.tombstones.get(&projectile_id) {
            return if terminal.launch_fingerprint == launch_fingerprint {
                Ok(LaunchAdmission::ExactRetry { projectile_id })
            } else {
                Err(ProjectileError::ConflictingLaunch)
            };
        }
        if self.active.len() >= MAX_ACTIVE_PROJECTILES
            || self
                .active
                .values()
                .filter(|record| record.launch.shooter == launch.shooter)
                .count()
                >= MAX_PROJECTILES_PER_SHOOTER
        {
            return Err(ProjectileError::Capacity);
        }

        let segment_origin = launch.origin;
        let segment_velocity = launch.velocity;
        let record = ProjectileRecord {
            projectile_id: projectile_id.clone(),
            launch,
            team: context.team,
            source_vehicle: context.source_vehicle,
            launch_server_time_ms: context.server_time_ms,
            checked_through_ms: 0,
            checked_distance: 0.0,
            piercing_loss: 0.0,
            segment_origin,
            segment_velocity,
            segment_start_time_ms: 0,
            ricochet_count: 0,
            base_penetration_multiplier: 1.0,
            launch_fingerprint,
            last_progress_fingerprint: None,
            last_ricochet_fingerprint: None,
        };
        self.active.insert(projectile_id, record.clone());
        self.revision = self.revision.saturating_add(1);
        Ok(LaunchAdmission::New(record))
    }

    /// Applies a whole cursor batch with compare-and-swap semantics. Any bad
    /// cursor rejects the batch before a single active record is mutated.
    pub fn progress(
        &mut self,
        cursors: &[ProjectileCursor],
        server_time_ms: u64,
    ) -> Result<ProgressAdmission, ProjectileError> {
        if cursors.is_empty() || cursors.len() > MAX_PROGRESS_BATCH {
            return Err(ProjectileError::InvalidProgressBatch);
        }
        let mut seen = BTreeMap::new();
        let mut normalized = Vec::with_capacity(cursors.len());
        for raw in cursors {
            if raw.projectile_id.is_empty()
                || raw.projectile_id.len() > 96
                || seen.insert(raw.projectile_id.clone(), ()).is_some()
            {
                return Err(ProjectileError::InvalidProgressBatch);
            }
            let record =
                self.active
                    .get(&raw.projectile_id)
                    .ok_or_else(|| ProjectileError::Unknown {
                        projectile_id: raw.projectile_id.clone(),
                    })?;
            let mut cursor = raw.clone();
            normalize_cursor(&mut cursor, record, server_time_ms)?;
            let cursor_fingerprint = fingerprint(&cursor);
            let repeated = if cursor.base_checked_ms != record.checked_through_ms {
                if record.last_progress_fingerprint.as_ref() == Some(&cursor_fingerprint) {
                    true
                } else {
                    return Err(ProjectileError::CursorConflict {
                        projectile_id: cursor.projectile_id,
                    });
                }
            } else {
                false
            };
            normalized.push((cursor, cursor_fingerprint, repeated));
        }

        let mut changed = 0;
        for (cursor, cursor_fingerprint, repeated) in normalized {
            if repeated {
                continue;
            }
            let record = self
                .active
                .get_mut(&cursor.projectile_id)
                .expect("the batch was validated above");
            record.checked_through_ms = cursor.checked_through_ms;
            record.checked_distance = cursor.checked_distance;
            record.piercing_loss = cursor.piercing_loss;
            record.last_progress_fingerprint = Some(cursor_fingerprint);
            changed += 1;
        }
        if changed > 0 {
            self.revision = self.revision.saturating_add(1);
        }
        Ok(ProgressAdmission::Applied { changed })
    }

    /// Commit the one #1513 ricochet continuation without retiring the shell.
    ///
    /// The caller must first obtain a Rust `Ricochet` armour verdict and build
    /// the reflected segment from the frozen native contact normal with
    /// [`build_first_ricochet`]. This method owns cursor CAS, speed continuity,
    /// shell multiplier, and exact-retry identity.
    pub fn ricochet(
        &mut self,
        mut request: ProjectileRicochet,
        server_time_ms: u64,
    ) -> Result<RicochetAdmission, ProjectileError> {
        normalize_ricochet_shape(&mut request)?;
        let request_fingerprint = fingerprint(&request);
        let record =
            self.active
                .get(&request.projectile_id)
                .ok_or_else(|| ProjectileError::Unknown {
                    projectile_id: request.projectile_id.clone(),
                })?;
        if request.round_id != record.launch.round_id
            || request.authority_epoch != record.launch.authority_epoch
        {
            return Err(ProjectileError::Lineage);
        }
        if record.ricochet_count != 0 {
            return if record.last_ricochet_fingerprint.as_ref() == Some(&request_fingerprint) {
                Ok(RicochetAdmission::ExactRetry)
            } else {
                Err(ProjectileError::ConflictingRicochet)
            };
        }
        validate_ricochet_against_record(&request, record, server_time_ms)?;

        let record = self
            .active
            .get_mut(&request.projectile_id)
            .expect("the ricochet request was validated above");
        record.checked_through_ms = request.resolved_time_ms;
        record.checked_distance = request.checked_distance;
        record.piercing_loss = request.piercing_loss;
        record.segment_origin = request.segment_origin;
        record.segment_velocity = request.segment_velocity;
        record.segment_start_time_ms = request.resolved_time_ms;
        record.ricochet_count = 1;
        record.base_penetration_multiplier = request.base_penetration_multiplier;
        record.last_ricochet_fingerprint = Some(request_fingerprint);
        let record = record.clone();
        self.revision = self.revision.saturating_add(1);
        Ok(RicochetAdmission::Applied { record })
    }

    pub fn resolve(
        &mut self,
        mut request: ProjectileResolution,
        server_time_ms: u64,
    ) -> Result<ResolutionAdmission, ProjectileError> {
        normalize_resolution(&mut request)?;
        let request_fingerprint = fingerprint(&request);
        if let Some(terminal) = self.tombstones.get(&request.projectile_id) {
            return if terminal.request_fingerprint == request_fingerprint {
                Ok(ResolutionAdmission::ExactRetry)
            } else {
                Err(ProjectileError::ConflictingResolution)
            };
        }
        let record =
            self.active
                .get(&request.projectile_id)
                .ok_or_else(|| ProjectileError::Unknown {
                    projectile_id: request.projectile_id.clone(),
                })?;
        if request.round_id != record.launch.round_id
            || request.authority_epoch != record.launch.authority_epoch
        {
            return Err(ProjectileError::Lineage);
        }
        validate_resolution_against_record(&request, record, server_time_ms)?;

        let record = self
            .active
            .remove(&request.projectile_id)
            .expect("the resolution was validated above");
        self.tombstones.insert(
            request.projectile_id.clone(),
            ProjectileTombstone {
                projectile_id: request.projectile_id,
                outcome: request.outcome,
                launch_fingerprint: record.launch_fingerprint.clone(),
                request_fingerprint,
            },
        );
        self.revision = self.revision.saturating_add(1);
        Ok(ResolutionAdmission::Applied {
            record,
            outcome: request.outcome,
            impact: request.impact,
        })
    }
}

/// Build the exact first continuation segment from Rust-owned armour verdict
/// inputs and one frozen native contact normal.
pub fn build_first_ricochet(
    record: &ProjectileRecord,
    resolution: &ProjectileResolution,
    native_normal: ProjectileVec3,
) -> Result<ProjectileRicochet, ProjectileError> {
    if record.ricochet_count != 0
        || resolution.projectile_id != record.projectile_id
        || resolution.round_id != record.launch.round_id
        || resolution.authority_epoch != record.launch.authority_epoch
        || resolution.outcome != ProjectileOutcome::Impact
    {
        return Err(ProjectileError::RicochetLimit);
    }
    let impact = resolution.impact.ok_or(ProjectileError::InvalidRicochet)?;
    let normal_length = native_normal.magnitude();
    if !native_normal.finite() || !normal_length.is_finite() || normal_length <= 1.0e-9 {
        return Err(ProjectileError::InvalidRicochet);
    }
    let normal = ProjectileVec3 {
        x: native_normal.x / normal_length,
        y: native_normal.y / normal_length,
        z: native_normal.z / normal_length,
    };
    let incoming = incoming_velocity(record, resolution.resolved_time_ms)?;
    let dot = incoming.x * normal.x + incoming.y * normal.y + incoming.z * normal.z;
    let reflected = ProjectileVec3 {
        x: incoming.x - 2.0 * dot * normal.x,
        y: incoming.y - 2.0 * dot * normal.y,
        z: incoming.z - 2.0 * dot * normal.z,
    };
    let speed = reflected.magnitude();
    if !speed.is_finite() || !(0.001..=3_000.0).contains(&speed) {
        return Err(ProjectileError::InvalidRicochet);
    }
    let direction = ProjectileVec3 {
        x: reflected.x / speed,
        y: reflected.y / speed,
        z: reflected.z / speed,
    };
    let multiplier =
        first_ricochet_penetration_multiplier(record.launch.source_shot.shell.kind.as_str())
            .ok_or(ProjectileError::InvalidRicochet)?;
    Ok(ProjectileRicochet {
        round_id: resolution.round_id,
        authority_epoch: resolution.authority_epoch,
        projectile_id: resolution.projectile_id.clone(),
        base_checked_ms: resolution.base_checked_ms,
        resolved_time_ms: resolution.resolved_time_ms,
        checked_distance: resolution.checked_distance,
        piercing_loss: resolution.piercing_loss,
        penetration_factor: resolution.penetration_factor,
        impact,
        segment_origin: ProjectileVec3 {
            x: impact.x + direction.x * RICOCHET_ORIGIN_OFFSET_M,
            y: impact.y + direction.y * RICOCHET_ORIGIN_OFFSET_M,
            z: impact.z + direction.z * RICOCHET_ORIGIN_OFFSET_M,
        },
        segment_velocity: reflected,
        base_penetration_multiplier: multiplier,
    })
}

pub fn first_ricochet_penetration_multiplier(shell_kind: &str) -> Option<f64> {
    match shell_kind {
        "ARMOR_PIERCING" | "ARMOR_PIERCING_CR" => Some(0.75),
        "HOLLOW_CHARGE" => Some(1.0),
        _ => None,
    }
}

pub fn projectile_id(round_id: u64, shooter: VehicleKey, shot_seq: u64) -> String {
    let prefix = match shooter.kind {
        VehicleKind::Player => 'p',
        VehicleKind::Bot => 'b',
    };
    format!("{round_id}:{prefix}:{}:{shot_seq}", shooter.id)
}

fn normalize_launch(launch: &mut ProjectileLaunch) -> Result<(), ProjectileError> {
    let speed = launch.velocity.magnitude();
    let source = &mut launch.source_shot;
    if launch.round_id == 0
        || launch.shooter.id == 0
        || launch.shooter.id > MAX_COMBAT_ID
        || launch.shot_seq == 0
        || launch.shot_seq > MAX_COMBAT_ID
        || launch.shell_index > 9
        || !launch.origin.finite()
        || !launch.velocity.finite()
        || !within(launch.origin.x, -5_000.0, 5_000.0)
        || !within(launch.origin.y, -1_000.0, 3_000.0)
        || !within(launch.origin.z, -5_000.0, 5_000.0)
        || !(0.001..=3_000.0).contains(&speed)
        || !within_open_low(launch.gravity, 0.0, 500.0)
        || !within_open_low(launch.max_distance, 0.0, 10_000.0)
        || !(1..=MAX_PROJECTILE_LIFETIME_MS).contains(&launch.max_time_ms)
        || !within(launch.splash_radius, 0.0, 100.0)
        || !within(launch.penetration_factor, 0.75, 1.25)
        || !within(launch.damage_factor, 0.75, 1.25)
        || !valid_source_shot(source)
    {
        return Err(ProjectileError::InvalidLaunch);
    }
    launch.origin = rounded_vec(launch.origin);
    launch.velocity = rounded_vec(launch.velocity);
    launch.gravity = round6(launch.gravity);
    launch.max_distance = round6(launch.max_distance);
    launch.splash_radius = round6(launch.splash_radius);
    launch.penetration_factor = round6(launch.penetration_factor);
    launch.damage_factor = round6(launch.damage_factor);
    normalize_source_shot(source);

    let source_speed = source.speed;
    let source_is_he = source.shell.kind == "HIGH_EXPLOSIVE";
    if (!launch.is_he && launch.splash_radius != 0.0)
        || launch.is_he != source_is_he
        || !close(speed, source_speed)
        || !close(launch.gravity, source.gravity)
        || !close(launch.max_distance, source.max_distance)
        || !close(launch.splash_radius, source.shell.explosion_radius)
    {
        return Err(ProjectileError::InvalidLaunch);
    }
    Ok(())
}

fn valid_source_shot(source: &SourceShot) -> bool {
    matches!(
        source.shell.kind.as_str(),
        "HOLLOW_CHARGE"
            | "HIGH_EXPLOSIVE"
            | "ARMOR_PIERCING"
            | "ARMOR_PIERCING_HE"
            | "ARMOR_PIERCING_CR"
    ) && within_open_low(source.speed, 0.0, 3_000.0)
        && within_open_low(source.gravity, 0.0, 500.0)
        && within_open_low(source.max_distance, 0.0, 10_000.0)
        && source
            .piercing_power
            .into_iter()
            .all(|value| within(value, 0.0, 10_000.0))
        && within_open_low(source.shell.caliber, 0.0, 1_000.0)
        && within_open_low(source.shell.damage[0], 0.0, 10_000.0)
        && within(source.shell.damage[1], 0.0, MAX_CRITICAL_DEVICE_HP)
        && within(source.shell.explosion_radius, 0.0, 100.0)
        && source.shell.he_tuning().is_ok()
}

fn normalize_source_shot(source: &mut SourceShot) {
    source.speed = round6(source.speed);
    source.gravity = round6(source.gravity);
    source.max_distance = round6(source.max_distance);
    source.piercing_power = source.piercing_power.map(round6);
    source.shell.caliber = round6(source.shell.caliber);
    source.shell.damage = source.shell.damage.map(round6);
    source.shell.explosion_radius = round6(source.shell.explosion_radius);
    source.shell.explosion_damage_factor = source.shell.explosion_damage_factor.map(round6);
    source.shell.explosion_damage_absorption_factor =
        source.shell.explosion_damage_absorption_factor.map(round6);
    source.shell.explosion_edge_damage_factor =
        source.shell.explosion_edge_damage_factor.map(round6);
}

fn normalize_cursor(
    cursor: &mut ProjectileCursor,
    record: &ProjectileRecord,
    server_time_ms: u64,
) -> Result<(), ProjectileError> {
    cursor.checked_distance = round6(cursor.checked_distance);
    cursor.piercing_loss = round6(cursor.piercing_loss);
    cursor.penetration_factor = round6(cursor.penetration_factor);
    let elapsed = server_time_ms.saturating_sub(record.launch_server_time_ms);
    if cursor.base_checked_ms > cursor.checked_through_ms
        || cursor.checked_through_ms > record.launch.max_time_ms
        || cursor.checked_through_ms > elapsed.saturating_add(PROJECTILE_CLOCK_LEEWAY_MS)
        || !within(
            cursor.checked_distance,
            record.checked_distance,
            record.launch.max_distance + PROJECTILE_TOLERANCE,
        )
        || !within(cursor.piercing_loss, record.piercing_loss, 100_000.0)
        || cursor.penetration_factor != record.launch.penetration_factor
    {
        return Err(ProjectileError::InvalidProgress {
            projectile_id: cursor.projectile_id.clone(),
        });
    }
    Ok(())
}

fn normalize_resolution(request: &mut ProjectileResolution) -> Result<(), ProjectileError> {
    if request.projectile_id.is_empty() || request.projectile_id.len() > 96 {
        return Err(ProjectileError::InvalidResolution);
    }
    request.checked_distance = round6(request.checked_distance);
    request.piercing_loss = round6(request.piercing_loss);
    request.penetration_factor = round6(request.penetration_factor);
    if let Some(impact) = request.impact.as_mut() {
        if !impact.finite()
            || !within(impact.x, -5_000.0, 5_000.0)
            || !within(impact.y, -1_000.0, 3_000.0)
            || !within(impact.z, -5_000.0, 5_000.0)
        {
            return Err(ProjectileError::InvalidResolution);
        }
        *impact = rounded_vec(*impact);
    }
    if matches!(request.outcome, ProjectileOutcome::Impact) != request.impact.is_some() {
        return Err(ProjectileError::InvalidResolution);
    }
    Ok(())
}

fn validate_resolution_against_record(
    request: &ProjectileResolution,
    record: &ProjectileRecord,
    server_time_ms: u64,
) -> Result<(), ProjectileError> {
    let elapsed = server_time_ms.saturating_sub(record.launch_server_time_ms);
    if request.base_checked_ms != record.checked_through_ms
        || request.resolved_time_ms < request.base_checked_ms
        || request.resolved_time_ms > record.launch.max_time_ms
        || request.resolved_time_ms > elapsed.saturating_add(PROJECTILE_CLOCK_LEEWAY_MS)
        || !within(
            request.checked_distance,
            record.checked_distance,
            record.launch.max_distance + PROJECTILE_TOLERANCE,
        )
        || !within(request.piercing_loss, record.piercing_loss, 100_000.0)
        || request.penetration_factor != record.launch.penetration_factor
    {
        return Err(ProjectileError::InvalidResolution);
    }
    Ok(())
}

fn normalize_ricochet_shape(request: &mut ProjectileRicochet) -> Result<(), ProjectileError> {
    if request.projectile_id.is_empty()
        || request.projectile_id.len() > 96
        || !request.impact.finite()
        || !request.segment_origin.finite()
        || !request.segment_velocity.finite()
        || !within(request.impact.x, -5_000.0, 5_000.0)
        || !within(request.impact.y, -1_000.0, 3_000.0)
        || !within(request.impact.z, -5_000.0, 5_000.0)
        || !within(request.segment_origin.x, -5_000.0, 5_000.0)
        || !within(request.segment_origin.y, -1_000.0, 3_000.0)
        || !within(request.segment_origin.z, -5_000.0, 5_000.0)
        || !within_open_low(request.segment_velocity.magnitude(), 0.0, 3_000.0)
    {
        return Err(ProjectileError::InvalidRicochet);
    }
    request.checked_distance = round6(request.checked_distance);
    request.piercing_loss = round6(request.piercing_loss);
    request.penetration_factor = round6(request.penetration_factor);
    request.impact = rounded_vec(request.impact);
    request.segment_origin = rounded_vec(request.segment_origin);
    request.segment_velocity = rounded_vec(request.segment_velocity);
    request.base_penetration_multiplier = round6(request.base_penetration_multiplier);
    Ok(())
}

fn validate_ricochet_against_record(
    request: &ProjectileRicochet,
    record: &ProjectileRecord,
    server_time_ms: u64,
) -> Result<(), ProjectileError> {
    let elapsed = server_time_ms.saturating_sub(record.launch_server_time_ms);
    let incoming = incoming_velocity(record, request.resolved_time_ms)?;
    let incoming_speed = incoming.magnitude();
    let reflected_speed = request.segment_velocity.magnitude();
    let speed_tolerance = 0.25_f64.max(incoming_speed * 0.0001);
    let origin_gap = ProjectileVec3 {
        x: request.segment_origin.x - request.impact.x,
        y: request.segment_origin.y - request.impact.y,
        z: request.segment_origin.z - request.impact.z,
    }
    .magnitude();
    let expected_multiplier =
        first_ricochet_penetration_multiplier(record.launch.source_shot.shell.kind.as_str())
            .ok_or(ProjectileError::InvalidRicochet)?;
    if request.base_checked_ms != record.checked_through_ms
        || request.resolved_time_ms < request.base_checked_ms
        || request.resolved_time_ms >= record.launch.max_time_ms
        || request.resolved_time_ms > elapsed.saturating_add(PROJECTILE_CLOCK_LEEWAY_MS)
        || !within(
            request.checked_distance,
            record.checked_distance,
            record.launch.max_distance + PROJECTILE_TOLERANCE,
        )
        || request.checked_distance >= record.launch.max_distance
        || !within(request.piercing_loss, record.piercing_loss, 100_000.0)
        || request.penetration_factor != record.launch.penetration_factor
        || request.base_penetration_multiplier != expected_multiplier
        || !origin_gap.is_finite()
        || origin_gap > RICOCHET_ORIGIN_TOLERANCE_M
        || (reflected_speed - incoming_speed).abs() > speed_tolerance
    {
        return Err(ProjectileError::InvalidRicochet);
    }
    Ok(())
}

fn incoming_velocity(
    record: &ProjectileRecord,
    resolved_time_ms: u64,
) -> Result<ProjectileVec3, ProjectileError> {
    if resolved_time_ms < record.segment_start_time_ms {
        return Err(ProjectileError::InvalidRicochet);
    }
    let elapsed_seconds = (resolved_time_ms - record.segment_start_time_ms) as f64 / 1_000.0;
    let incoming = ProjectileVec3 {
        x: record.segment_velocity.x,
        y: record.segment_velocity.y - record.launch.gravity * elapsed_seconds,
        z: record.segment_velocity.z,
    };
    if !incoming.finite() || incoming.magnitude() <= 0.0 {
        return Err(ProjectileError::InvalidRicochet);
    }
    Ok(incoming)
}

fn rounded_vec(value: ProjectileVec3) -> ProjectileVec3 {
    ProjectileVec3 {
        x: round6(value.x),
        y: round6(value.y),
        z: round6(value.z),
    }
}

fn round6(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

fn within(value: f64, low: f64, high: f64) -> bool {
    value.is_finite() && value >= low && value <= high
}

fn within_open_low(value: f64, low: f64, high: f64) -> bool {
    value.is_finite() && value > low && value <= high
}

fn close(left: f64, right: f64) -> bool {
    (left - right).abs() <= PROJECTILE_TOLERANCE.max(right.abs() * 0.000_001)
}

fn fingerprint<T: Serialize>(value: &T) -> String {
    serde_json::to_string(value).expect("serializing a typed projectile value cannot fail")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shooter() -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Player,
            id: 1,
        }
    }

    fn launch() -> ProjectileLaunch {
        ProjectileLaunch {
            round_id: 7,
            authority_epoch: 3,
            shooter: shooter(),
            shot_seq: 1,
            shell_index: 0,
            origin: ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 0.0,
            },
            velocity: ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 100.0,
            },
            gravity: 9.81,
            max_distance: 720.0,
            max_time_ms: 10_000,
            is_he: false,
            splash_radius: 0.0,
            penetration_factor: 1.0,
            damage_factor: 1.0,
            source_shot: SourceShot {
                speed: 100.0,
                gravity: 9.81,
                max_distance: 720.0,
                piercing_power: [100.0, 80.0],
                deadeye: false,
                shell: SourceShell {
                    kind: "ARMOR_PIERCING".to_owned(),
                    caliber: 75.0,
                    damage: [100.0, 0.0],
                    explosion_radius: 0.0,
                    explosion_damage_factor: None,
                    explosion_damage_absorption_factor: None,
                    explosion_edge_damage_factor: None,
                },
            },
            fire_intent_seq: Some(1),
            fire_input_seq: Some(4),
        }
    }

    fn context() -> LaunchContext {
        LaunchContext {
            round_id: 7,
            authority_epoch: 3,
            shooter: shooter(),
            team: 1,
            source_vehicle: "ussr:R11_MS-1".to_owned(),
            expected_shot_seq: 1,
            server_time_ms: 1_000,
        }
    }

    fn he_launch(tuning: HeTuning) -> ProjectileLaunch {
        let mut value = launch();
        value.is_he = true;
        value.splash_radius = 10.0;
        value.source_shot.shell.kind = "HIGH_EXPLOSIVE".to_owned();
        value.source_shot.shell.explosion_radius = 10.0;
        value.source_shot.shell.explosion_damage_factor = Some(tuning.splash_fraction);
        value.source_shot.shell.explosion_damage_absorption_factor = Some(tuning.armor_factor);
        value.source_shot.shell.explosion_edge_damage_factor = Some(tuning.edge_factor);
        value
    }

    fn admitted_record(ledger: &mut ProjectileLedger) -> ProjectileRecord {
        match ledger.admit_launch(launch(), context()).unwrap() {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        }
    }

    fn impact_resolution(record: &ProjectileRecord) -> ProjectileResolution {
        ProjectileResolution {
            round_id: record.launch.round_id,
            authority_epoch: record.launch.authority_epoch,
            projectile_id: record.projectile_id.clone(),
            base_checked_ms: record.checked_through_ms,
            outcome: ProjectileOutcome::Impact,
            resolved_time_ms: 200,
            checked_distance: 20.0,
            piercing_loss: 3.0,
            penetration_factor: record.launch.penetration_factor,
            impact: Some(ProjectileVec3 {
                x: 0.0,
                y: 0.8038,
                z: 20.0,
            }),
        }
    }

    #[test]
    fn launch_and_exact_retry_share_one_projectile() {
        let mut ledger = ProjectileLedger::new();
        let first = ledger.admit_launch(launch(), context()).unwrap();
        assert!(matches!(first, LaunchAdmission::New(_)));
        assert_eq!(
            ledger.admit_launch(launch(), context()).unwrap(),
            LaunchAdmission::ExactRetry {
                projectile_id: "7:p:1:1".to_owned(),
            }
        );
        assert_eq!(ledger.active().len(), 1);
        assert_eq!(ledger.revision(), 1);
    }

    #[test]
    fn duplicated_launch_identity_with_new_physics_conflicts() {
        let mut ledger = ProjectileLedger::new();
        ledger.admit_launch(launch(), context()).unwrap();
        let mut conflict = launch();
        conflict.origin.x = 1.0;
        assert_eq!(
            ledger.admit_launch(conflict, context()),
            Err(ProjectileError::ConflictingLaunch)
        );
    }

    #[test]
    fn he_source_shell_wire_is_flat_camel_case_and_preserves_overrides() {
        let tuning = HeTuning::new(0.6, 1.0, 0.2).unwrap();
        let value = he_launch(tuning);
        let wire = serde_json::to_value(&value.source_shot.shell).unwrap();
        assert_eq!(wire["explosionDamageFactor"], 0.6);
        assert_eq!(wire["explosionDamageAbsorptionFactor"], 1.0);
        assert_eq!(wire["explosionEdgeDamageFactor"], 0.2);
        assert!(wire.get("he_tuning").is_none());
        let ap_wire = serde_json::to_value(&launch().source_shot.shell).unwrap();
        assert!(ap_wire.get("explosionDamageFactor").is_none());
        assert!(ap_wire.get("explosionDamageAbsorptionFactor").is_none());
        assert!(ap_wire.get("explosionEdgeDamageFactor").is_none());

        let mut ledger = ProjectileLedger::new();
        let record = match ledger.admit_launch(value, context()).unwrap() {
            LaunchAdmission::New(record) => record,
            LaunchAdmission::ExactRetry { .. } => unreachable!(),
        };
        assert_eq!(
            record.launch.source_shot.shell.he_tuning(),
            Ok(Some(tuning))
        );
    }

    #[test]
    fn he_source_shell_requires_all_three_factors() {
        let mut value = he_launch(HeTuning::default());
        value.source_shot.shell.explosion_edge_damage_factor = None;
        assert_eq!(
            ProjectileLedger::new().admit_launch(value, context()),
            Err(ProjectileError::InvalidLaunch)
        );
    }

    #[test]
    fn malformed_he_factor_and_ap_he_fields_are_rejected() {
        let mut malformed = he_launch(HeTuning::default());
        malformed.source_shot.shell.explosion_edge_damage_factor = Some(1.000_001);
        assert_eq!(
            ProjectileLedger::new().admit_launch(malformed, context()),
            Err(ProjectileError::InvalidLaunch)
        );

        let mut ap = launch();
        ap.source_shot.shell.explosion_damage_factor = Some(0.5);
        assert_eq!(
            ProjectileLedger::new().admit_launch(ap, context()),
            Err(ProjectileError::InvalidLaunch)
        );

        let malformed_wire = serde_json::json!({
            "kind": "HIGH_EXPLOSIVE",
            "caliber": 122.0,
            "damage": [400.0, 90.0],
            "explosionRadius": 10.0,
            "explosionDamageFactor": "0.5",
            "explosionDamageAbsorptionFactor": 1.3,
            "explosionEdgeDamageFactor": 0.15
        });
        assert!(serde_json::from_value::<SourceShell>(malformed_wire).is_err());
    }

    #[test]
    fn trusted_large_module_damage_survives_projectile_admission() {
        let mut value = launch();
        value.source_shot.shell.damage[1] = MAX_CRITICAL_DEVICE_HP;
        assert!(matches!(
            ProjectileLedger::new().admit_launch(value.clone(), context()),
            Ok(LaunchAdmission::New(_))
        ));

        value.source_shot.shell.damage[1] = MAX_CRITICAL_DEVICE_HP + 1.0;
        assert_eq!(
            ProjectileLedger::new().admit_launch(value, context()),
            Err(ProjectileError::InvalidLaunch)
        );
    }

    #[test]
    fn progress_batch_is_atomic_and_exact_retry_is_a_noop() {
        let mut ledger = ProjectileLedger::new();
        ledger.admit_launch(launch(), context()).unwrap();
        let cursor = ProjectileCursor {
            projectile_id: "7:p:1:1".to_owned(),
            base_checked_ms: 0,
            checked_through_ms: 100,
            checked_distance: 10.0,
            piercing_loss: 1.0,
            penetration_factor: 1.0,
        };
        assert_eq!(
            ledger.progress(&[cursor.clone()], 1_100).unwrap(),
            ProgressAdmission::Applied { changed: 1 }
        );
        assert_eq!(
            ledger.progress(&[cursor.clone()], 1_100).unwrap(),
            ProgressAdmission::Applied { changed: 0 }
        );
        let mut invalid = cursor.clone();
        invalid.projectile_id = "missing".to_owned();
        assert!(matches!(
            ledger.progress(&[cursor, invalid], 1_100),
            Err(ProjectileError::Unknown { .. })
        ));
        assert_eq!(ledger.active()["7:p:1:1"].checked_through_ms, 100);
    }

    #[test]
    fn resolution_is_terminal_and_idempotent() {
        let mut ledger = ProjectileLedger::new();
        ledger.admit_launch(launch(), context()).unwrap();
        let request = ProjectileResolution {
            round_id: 7,
            authority_epoch: 3,
            projectile_id: "7:p:1:1".to_owned(),
            base_checked_ms: 0,
            outcome: ProjectileOutcome::Impact,
            resolved_time_ms: 200,
            checked_distance: 20.0,
            piercing_loss: 0.0,
            penetration_factor: 1.0,
            impact: Some(ProjectileVec3 {
                x: 0.0,
                y: 1.0,
                z: 20.0,
            }),
        };
        assert!(matches!(
            ledger.resolve(request.clone(), 1_200).unwrap(),
            ResolutionAdmission::Applied { .. }
        ));
        assert_eq!(
            ledger.resolve(request, 1_200).unwrap(),
            ResolutionAdmission::ExactRetry
        );
        assert!(ledger.active().is_empty());
        assert!(ledger.tombstone("7:p:1:1").is_some());
    }

    #[test]
    fn resolution_cannot_jump_past_the_cursor_or_server_clock() {
        let mut ledger = ProjectileLedger::new();
        ledger.admit_launch(launch(), context()).unwrap();
        let request = ProjectileResolution {
            round_id: 7,
            authority_epoch: 3,
            projectile_id: "7:p:1:1".to_owned(),
            base_checked_ms: 1,
            outcome: ProjectileOutcome::Miss,
            resolved_time_ms: 5_000,
            checked_distance: 20.0,
            piercing_loss: 0.0,
            penetration_factor: 1.0,
            impact: None,
        };
        assert_eq!(
            ledger.resolve(request, 1_100),
            Err(ProjectileError::InvalidResolution)
        );
        assert_eq!(ledger.active().len(), 1);
    }

    #[test]
    fn first_ricochet_is_reflected_committed_and_exact_retry_safe() {
        let mut ledger = ProjectileLedger::new();
        let record = admitted_record(&mut ledger);
        let resolution = impact_resolution(&record);
        let request = build_first_ricochet(
            &record,
            &resolution,
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 1.0,
            },
        )
        .unwrap();
        assert_eq!(request.base_penetration_multiplier, 0.75);
        assert!(request.segment_velocity.z < 0.0);
        assert!((request.segment_velocity.magnitude() - 100.019_245).abs() < 0.001);

        let applied = match ledger.ricochet(request.clone(), 1_200).unwrap() {
            RicochetAdmission::Applied { record } => record,
            RicochetAdmission::ExactRetry => unreachable!(),
        };
        assert_eq!(applied.ricochet_count, 1);
        assert_eq!(applied.segment_start_time_ms, 200);
        let revision = ledger.revision();
        assert_eq!(
            ledger.ricochet(request.clone(), 1_200),
            Ok(RicochetAdmission::ExactRetry)
        );
        assert_eq!(ledger.revision(), revision);

        let mut conflict = request;
        conflict.segment_origin.x += 0.01;
        assert_eq!(
            ledger.ricochet(conflict, 1_200),
            Err(ProjectileError::ConflictingRicochet)
        );
        assert_eq!(
            build_first_ricochet(
                &applied,
                &resolution,
                ProjectileVec3 {
                    x: 0.0,
                    y: 0.0,
                    z: 1.0,
                },
            ),
            Err(ProjectileError::RicochetLimit)
        );
    }

    #[test]
    fn invalid_ricochet_geometry_does_not_mutate_projectile() {
        let mut ledger = ProjectileLedger::new();
        let record = admitted_record(&mut ledger);
        let mut request = build_first_ricochet(
            &record,
            &impact_resolution(&record),
            ProjectileVec3 {
                x: 0.0,
                y: 0.0,
                z: 1.0,
            },
        )
        .unwrap();
        request.base_penetration_multiplier = 1.0;
        let before = ledger.active()[&record.projectile_id].clone();
        assert_eq!(
            ledger.ricochet(request, 1_200),
            Err(ProjectileError::InvalidRicochet)
        );
        assert_eq!(ledger.active()[&record.projectile_id], before);
    }

    #[test]
    fn stun_batch_is_atomic_durable_and_uses_one_frozen_clock() {
        let mut projectile_ledger = ProjectileLedger::new();
        let record = admitted_record(&mut projectile_ledger);
        let mut stun = ProjectileStunLedger::new(7, 3, 10, 1_000).unwrap();
        let bot_two = VehicleKey {
            kind: VehicleKind::Bot,
            id: 2,
        };
        let bot_three = VehicleKey {
            kind: VehicleKind::Bot,
            id: 3,
        };
        let targets = [
            ProjectileStunTarget {
                target: bot_three,
                end_server_time_ms: 1_500,
            },
            ProjectileStunTarget {
                target: bot_two,
                end_server_time_ms: 1_500,
            },
        ];
        let admission = stun.apply_projectile_batch(&record, &targets).unwrap();
        assert_eq!(admission.applied, 2);
        assert_eq!(admission.exact_retries, 0);
        assert_eq!(
            admission
                .activated
                .iter()
                .map(|state| state.target)
                .collect::<Vec<_>>(),
            vec![bot_two, bot_three]
        );
        assert_eq!(stun.active_assister(bot_two), Some(shooter()));

        let snapshot = stun.state(bot_two).cloned();
        let mut invalid = targets;
        invalid[1].end_server_time_ms = 1_600;
        assert_eq!(
            stun.apply_projectile_batch(&record, &invalid),
            Err(ProjectileStunError::ConflictingRetry)
        );
        assert_eq!(
            stun.apply_projectile_batch(&record, &targets[..1]),
            Err(ProjectileStunError::ConflictingRetry)
        );
        assert_eq!(stun.state(bot_two).cloned(), snapshot);

        assert_eq!(stun.advance_tick(11, 1_499).unwrap(), Vec::new());
        let expired = stun.advance_tick(12, 1_500).unwrap();
        assert_eq!(expired.len(), 2);
        assert!(stun.state(bot_two).is_none());
        let retry = stun.apply_projectile_batch(&record, &targets).unwrap();
        assert_eq!((retry.applied, retry.exact_retries), (0, 2));
        assert!(retry.activated.is_empty());
    }

    #[test]
    fn stun_end_stays_inside_the_round_without_rejecting_a_late_clock() {
        let mut projectile_ledger = ProjectileLedger::new();
        let record = admitted_record(&mut projectile_ledger);
        let target = VehicleKey {
            kind: VehicleKind::Bot,
            id: 2,
        };
        let mut stun = ProjectileStunLedger::new(7, 3, 0, 1_000).unwrap();
        assert_eq!(
            stun.apply_projectile_batch(
                &record,
                &[ProjectileStunTarget {
                    target,
                    end_server_time_ms: MAX_STUN_END_SERVER_TIME_MS + 1,
                }],
            ),
            Err(ProjectileStunError::InvalidEndTime)
        );
        assert!(stun.state(target).is_none());
        assert_eq!(stun.revision(), 0);
        assert!(stun
            .advance_tick(1, MAX_STUN_END_SERVER_TIME_MS + 1)
            .unwrap()
            .is_empty());
        assert_eq!(stun.current_tick(), 1);
    }

    #[test]
    fn medkit_stun_clear_is_end_time_cas_and_exact_retry_safe() {
        let mut projectile_ledger = ProjectileLedger::new();
        let record = admitted_record(&mut projectile_ledger);
        let target = VehicleKey {
            kind: VehicleKind::Player,
            id: 2,
        };
        let mut stun = ProjectileStunLedger::new(7, 3, 0, 1_000).unwrap();
        stun.apply_projectile_batch(
            &record,
            &[ProjectileStunTarget {
                target,
                end_server_time_ms: 2_000,
            }],
        )
        .unwrap();
        assert_eq!(
            stun.clear_medkit(target, 1, 1_999),
            Err(ProjectileStunError::ClearCasConflict)
        );
        let previous = stun.state(target).unwrap().clone();
        assert_eq!(
            stun.clear_medkit(target, 1, 2_000),
            Ok(ProjectileStunClearAdmission::Cleared { previous })
        );
        assert_eq!(
            stun.clear_medkit(target, 1, 2_000),
            Ok(ProjectileStunClearAdmission::ExactRetry)
        );
        assert_eq!(
            stun.clear_medkit(target, 1, 2_001),
            Err(ProjectileStunError::ConflictingRetry)
        );
        assert_eq!(
            stun.advance_tick(2, 1_100),
            Err(ProjectileStunError::TickSequence {
                current: 0,
                received: 2,
            })
        );
        assert_eq!(stun.current_tick(), 0);
    }
}
