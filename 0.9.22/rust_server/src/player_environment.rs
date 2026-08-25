//! Deterministic player environment-damage authority.
//!
//! Player controls still provide the canonical body pose, while the hidden
//! #1513 client provides only fenced, read-only ground and water evidence.
//! This ledger owns every timer and HP proposal; a missing oracle sample never
//! clears or advances an existing state.

use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::Value;

use crate::combat::BodyPose;
use crate::protocol::SimulationScope;
use crate::sim::delta_us_for_tick;
use crate::wire::WireObject;
use thiserror::Error;

pub const DROWNING_DEPTH_METRES: f64 = 1.6;
pub const DROWNING_DURATION_US: u64 = 10_000_000;
pub const OVERTURN_IGNORE_US: u64 = 100_000;
pub const OVERTURN_DURATION_US: u64 = 30_000_000;
pub const OVERTURN_WARNING_COSINE: f64 = 0.342_020_143_325_668_7;
pub const OVERTURN_ONBOARD_COSINE: f64 = 0.173_648_177_666_930_41;
pub const AIRBORNE_CLEARANCE_METRES: f64 = 0.8;
pub const FALL_SAFE_SPEED_METRES_PER_SECOND: f64 = 10.0;
pub const FALL_DAMAGE_PER_METRE_PER_SECOND: f64 = 0.03;
pub const PLAYER_LANDING_MAX_IMPACT_SPEED: f64 = 200.0;
pub const PLAYER_LANDING_HISTORY: usize = 64;
pub const PLAYER_LANDING_MAX_SEQUENCE: u64 = 2_147_483_647;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LandingObservationRequest {
    pub round_id: u64,
    pub authority_epoch: u64,
    pub observation_seq: u64,
    pub input_seq: u64,
    pub impact_speed: f64,
}

impl LandingObservationRequest {
    pub fn parse(message: &WireObject) -> Result<Self, LandingObservationParseError> {
        const FIELDS: [&str; 6] = [
            "type",
            "round_id",
            "authority_epoch",
            "observation_seq",
            "input_seq",
            "impact_speed",
        ];
        if message.kind() != "landing_observation"
            || message.fields().len() != FIELDS.len()
            || FIELDS.iter().any(|field| message.get(field).is_none())
        {
            return Err(LandingObservationParseError::InvalidShape);
        }
        let round_id = exact_integer(message.get("round_id"), 1, PLAYER_LANDING_MAX_SEQUENCE)
            .ok_or(LandingObservationParseError::InvalidShape)?;
        let authority_epoch = exact_integer(
            message.get("authority_epoch"),
            0,
            PLAYER_LANDING_MAX_SEQUENCE,
        )
        .ok_or(LandingObservationParseError::InvalidShape)?;
        let observation_seq = exact_integer(
            message.get("observation_seq"),
            1,
            PLAYER_LANDING_MAX_SEQUENCE,
        )
        .ok_or(LandingObservationParseError::InvalidShape)?;
        let input_seq = exact_integer(message.get("input_seq"), 1, PLAYER_LANDING_MAX_SEQUENCE)
            .ok_or(LandingObservationParseError::InvalidShape)?;
        let impact_speed = match message.get("impact_speed") {
            Some(Value::Number(number)) => number.as_f64(),
            _ => None,
        }
        .filter(|value| {
            value.is_finite() && (0.0..=PLAYER_LANDING_MAX_IMPACT_SPEED).contains(value)
        })
        .ok_or(LandingObservationParseError::InvalidShape)?;
        Ok(Self {
            round_id,
            authority_epoch,
            observation_seq,
            input_seq,
            impact_speed: round_six_places(impact_speed),
        })
    }

    pub const fn scope(self) -> SimulationScope {
        SimulationScope {
            round_id: self.round_id,
            epoch: self.authority_epoch,
        }
    }
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum LandingObservationParseError {
    #[error("landing observation has an invalid protocol shape")]
    InvalidShape,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct LandingObservationResult {
    #[serde(rename = "type")]
    pub message_type: &'static str,
    pub round_id: u64,
    pub authority_epoch: u64,
    pub observation_seq: u64,
    pub input_seq: u64,
    pub committed_seq: u64,
    pub accepted: bool,
    pub reason: &'static str,
}

impl LandingObservationResult {
    pub fn rejected(
        request: LandingObservationRequest,
        scope: SimulationScope,
        committed_seq: u64,
        reason: &'static str,
    ) -> Self {
        Self {
            message_type: "landing_observation_result",
            round_id: scope.round_id,
            authority_epoch: scope.epoch,
            observation_seq: request.observation_seq,
            input_seq: request.input_seq,
            committed_seq,
            accepted: false,
            reason,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct LandingObservationIdentity {
    authority_epoch: u64,
    observation_seq: u64,
    input_seq: u64,
    impact_speed_bits: u64,
}

impl From<LandingObservationRequest> for LandingObservationIdentity {
    fn from(request: LandingObservationRequest) -> Self {
        Self {
            authority_epoch: request.authority_epoch,
            observation_seq: request.observation_seq,
            input_seq: request.input_seq,
            impact_speed_bits: request.impact_speed.to_bits(),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LandingObservationContext {
    pub scope: SimulationScope,
    pub combat_active: bool,
    pub alive: bool,
    pub health: u32,
    pub max_health: u32,
    pub input_is_known: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LandingObservationAdmission {
    pub result: LandingObservationResult,
    pub damage: u32,
    pub newly_committed: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerEnvironmentCause {
    WorldCollision,
    Drowning,
    Overturn,
}

impl PlayerEnvironmentCause {
    pub const fn death_reason(self) -> u8 {
        match self {
            Self::WorldCollision => 3,
            Self::Drowning => 5,
            Self::Overturn => 7,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerEnvironmentDecision {
    pub player_id: u64,
    pub amount: u32,
    pub cause: PlayerEnvironmentCause,
}

/// One successful native ground query. `height == None` is a proven miss;
/// the outer `Option` on [`PlayerEnvironmentEvidence::ground`] distinguishes
/// that from an unavailable or timed-out query.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerGroundEvidence {
    pub height: Option<f64>,
    pub supported: bool,
}

/// Ground and water evidence frozen when the query was issued and released
/// only at its exact T+3 boundary.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerEnvironmentEvidence {
    pub issued_tick: u64,
    pub apply_tick: u64,
    pub pose: BodyPose,
    pub ground: Option<PlayerGroundEvidence>,
    /// `Some(0.0)` proves there is no water at the frozen root. `None` means
    /// the water query was unavailable or timed out and therefore pauses the
    /// drowning state instead of clearing it.
    pub water_depth: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PlayerEnvironmentTick {
    pub tick: u64,
    pub combat_live: bool,
    pub alive: bool,
    pub health: u32,
    pub max_health: u32,
    pub world_pose: bool,
    pub pose: BodyPose,
    pub up_cosine: f64,
    pub evidence: Option<PlayerEnvironmentEvidence>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct PlayerEnvironmentSnapshot {
    pub tick: u64,
    pub deep_water_us: u64,
    pub drowning: bool,
    pub overturn_danger_us: u64,
    pub overturned: bool,
    pub airborne: bool,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum PlayerEnvironmentError {
    #[error("player id must be positive")]
    InvalidPlayerId,
    #[error("environment tick {received} does not follow {last}")]
    TickSequence { last: u64, received: u64 },
    #[error("player environment input is invalid")]
    InvalidInput,
    #[error("native environment evidence is not exact T+3")]
    InvalidEvidenceLineage,
}

#[derive(Clone, Debug)]
pub struct PlayerEnvironmentLedger {
    player_id: u64,
    tick: u64,
    deep_water_us: u64,
    drowning: bool,
    tilt_us: u64,
    overturn_danger_us: u64,
    overturned: bool,
    airborne: bool,
    landing_observation_seq: u64,
    landing_observation_input_seq: u64,
    landing_observation_fingerprints: BTreeMap<u64, LandingObservationIdentity>,
}

impl PlayerEnvironmentLedger {
    pub fn new(player_id: u64, start_tick: u64) -> Result<Self, PlayerEnvironmentError> {
        if player_id == 0 {
            return Err(PlayerEnvironmentError::InvalidPlayerId);
        }
        Ok(Self {
            player_id,
            tick: start_tick,
            deep_water_us: 0,
            drowning: false,
            tilt_us: 0,
            overturn_danger_us: 0,
            overturned: false,
            airborne: false,
            landing_observation_seq: 0,
            landing_observation_input_seq: 0,
            landing_observation_fingerprints: BTreeMap::new(),
        })
    }

    pub fn player_id(&self) -> u64 {
        self.player_id
    }

    pub fn tick(&self) -> u64 {
        self.tick
    }

    pub fn landing_observation_seq(&self) -> u64 {
        self.landing_observation_seq
    }

    pub fn admit_landing_observation(
        &mut self,
        request: LandingObservationRequest,
        context: LandingObservationContext,
    ) -> LandingObservationAdmission {
        if request.round_id != context.scope.round_id {
            return self.rejected_landing(request, context.scope, "stale_authority");
        }
        let identity = LandingObservationIdentity::from(request);
        if let Some(previous) = self
            .landing_observation_fingerprints
            .get(&request.observation_seq)
        {
            if previous != &identity {
                return self.rejected_landing(request, context.scope, "identity_conflict");
            }
            return LandingObservationAdmission {
                result: self.landing_result(request, context.scope, true, ""),
                damage: 0,
                newly_committed: false,
            };
        }
        if request.scope() != context.scope {
            return self.rejected_landing(request, context.scope, "stale_authority");
        }
        if request.observation_seq != self.landing_observation_seq.saturating_add(1) {
            return self.rejected_landing(request, context.scope, "sequence_gap");
        }
        if !context.combat_active {
            return self.rejected_landing(request, context.scope, "not_active");
        }
        if !context.alive || context.health == 0 {
            return self.rejected_landing(request, context.scope, "player_dead");
        }
        if !context.input_is_known || request.input_seq <= self.landing_observation_input_seq {
            return self.rejected_landing(request, context.scope, "stale_input");
        }

        let damage =
            landing_fall_damage(context.max_health, request.impact_speed).min(context.health);
        self.landing_observation_seq = request.observation_seq;
        self.landing_observation_input_seq = request.input_seq;
        self.landing_observation_fingerprints
            .insert(request.observation_seq, identity);
        while self.landing_observation_fingerprints.len() > PLAYER_LANDING_HISTORY {
            if let Some(oldest) = self.landing_observation_fingerprints.keys().next().copied() {
                self.landing_observation_fingerprints.remove(&oldest);
            }
        }
        LandingObservationAdmission {
            result: self.landing_result(request, context.scope, true, ""),
            damage,
            newly_committed: true,
        }
    }

    fn landing_result(
        &self,
        request: LandingObservationRequest,
        scope: SimulationScope,
        accepted: bool,
        reason: &'static str,
    ) -> LandingObservationResult {
        LandingObservationResult {
            message_type: "landing_observation_result",
            round_id: scope.round_id,
            authority_epoch: scope.epoch,
            observation_seq: request.observation_seq,
            input_seq: request.input_seq,
            committed_seq: if accepted {
                request.observation_seq
            } else {
                self.landing_observation_seq
            },
            accepted,
            reason,
        }
    }

    fn rejected_landing(
        &self,
        request: LandingObservationRequest,
        scope: SimulationScope,
        reason: &'static str,
    ) -> LandingObservationAdmission {
        LandingObservationAdmission {
            result: self.landing_result(request, scope, false, reason),
            damage: 0,
            newly_committed: false,
        }
    }

    pub fn snapshot(&self) -> PlayerEnvironmentSnapshot {
        PlayerEnvironmentSnapshot {
            tick: self.tick,
            deep_water_us: self.deep_water_us,
            drowning: self.drowning,
            overturn_danger_us: self.overturn_danger_us,
            overturned: self.overturned,
            airborne: self.airborne,
        }
    }

    /// Advance one fixed tick and return at most one canonical HP proposal.
    /// Drowning wins a same-tick tie, matching the copied client update order.
    pub fn advance(
        &mut self,
        input: PlayerEnvironmentTick,
    ) -> Result<Option<PlayerEnvironmentDecision>, PlayerEnvironmentError> {
        let expected = self
            .tick
            .checked_add(1)
            .ok_or(PlayerEnvironmentError::TickSequence {
                last: self.tick,
                received: input.tick,
            })?;
        if input.tick != expected {
            return Err(PlayerEnvironmentError::TickSequence {
                last: self.tick,
                received: input.tick,
            });
        }
        if input.health > input.max_health
            || input.max_health == 0
            || !finite_pose(input.pose)
            || !input.up_cosine.is_finite()
            || !(-1.0..=1.0).contains(&input.up_cosine)
            || (input.alive && input.health == 0)
        {
            return Err(PlayerEnvironmentError::InvalidInput);
        }
        if let Some(evidence) = input.evidence {
            validate_evidence(evidence, input.tick)?;
        }

        let dt_us = delta_us_for_tick(input.tick);
        self.tick = input.tick;
        if !input.combat_live {
            self.clear_terminal_state();
            return Ok(None);
        }
        if !input.alive || !input.world_pose {
            if !input.alive {
                self.clear_terminal_state();
            }
            return Ok(None);
        }

        self.advance_overturn(input.up_cosine, dt_us);
        if let Some(evidence) = input.evidence {
            if let Some(water_depth) = evidence.water_depth {
                self.advance_drowning(water_depth, dt_us);
            }
            if let Some(ground) = evidence.ground {
                self.observe_airborne(evidence.pose, ground);
            }
        }

        if self.drowning && self.deep_water_us > DROWNING_DURATION_US {
            return Ok(Some(PlayerEnvironmentDecision {
                player_id: self.player_id,
                amount: input.health,
                cause: PlayerEnvironmentCause::Drowning,
            }));
        }
        if self.overturned && self.overturn_danger_us >= OVERTURN_DURATION_US {
            return Ok(Some(PlayerEnvironmentDecision {
                player_id: self.player_id,
                amount: input.health,
                cause: PlayerEnvironmentCause::Overturn,
            }));
        }
        Ok(None)
    }

    fn advance_drowning(&mut self, water_depth: f64, dt_us: u64) {
        if water_depth > DROWNING_DEPTH_METRES {
            self.drowning = true;
            self.deep_water_us = self.deep_water_us.saturating_add(dt_us);
        } else {
            self.drowning = false;
            self.deep_water_us = 0;
        }
    }

    fn advance_overturn(&mut self, up_cosine: f64, dt_us: u64) {
        if up_cosine > OVERTURN_WARNING_COSINE {
            self.tilt_us = 0;
            self.overturn_danger_us = 0;
            self.overturned = false;
            return;
        }
        self.tilt_us = self.tilt_us.saturating_add(dt_us);
        if self.tilt_us < OVERTURN_IGNORE_US {
            return;
        }
        if up_cosine <= OVERTURN_ONBOARD_COSINE {
            self.overturned = true;
            self.overturn_danger_us = self.overturn_danger_us.saturating_add(dt_us);
        } else {
            self.overturned = false;
            self.overturn_danger_us = 0;
        }
    }

    fn observe_airborne(&mut self, pose: BodyPose, ground: PlayerGroundEvidence) {
        let clearance = ground.height.map(|height| pose.y - height);
        let airborne_now =
            !ground.supported || clearance.map_or(true, |value| value > AIRBORNE_CLEARANCE_METRES);
        self.airborne = airborne_now;
    }

    fn clear_terminal_state(&mut self) {
        self.deep_water_us = 0;
        self.drowning = false;
        self.tilt_us = 0;
        self.overturn_danger_us = 0;
        self.overturned = false;
        self.airborne = false;
    }
}

pub fn landing_fall_damage(max_health: u32, impact_speed: f64) -> u32 {
    if impact_speed <= FALL_SAFE_SPEED_METRES_PER_SECOND {
        return 0;
    }
    let raw = f64::from(max_health)
        * (impact_speed - FALL_SAFE_SPEED_METRES_PER_SECOND)
        * FALL_DAMAGE_PER_METRE_PER_SECOND;
    raw.floor().clamp(0.0, f64::from(u32::MAX)) as u32
}

fn exact_integer(value: Option<&Value>, minimum: u64, maximum: u64) -> Option<u64> {
    match value {
        Some(Value::Number(number)) => number
            .as_u64()
            .filter(|value| (minimum..=maximum).contains(value)),
        _ => None,
    }
}

fn round_six_places(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

fn validate_evidence(
    evidence: PlayerEnvironmentEvidence,
    current_tick: u64,
) -> Result<(), PlayerEnvironmentError> {
    if evidence.apply_tick != current_tick
        || evidence.issued_tick.checked_add(3) != Some(evidence.apply_tick)
    {
        return Err(PlayerEnvironmentError::InvalidEvidenceLineage);
    }
    if !finite_pose(evidence.pose) || (evidence.ground.is_none() && evidence.water_depth.is_none())
    {
        return Err(PlayerEnvironmentError::InvalidInput);
    }
    if let Some(ground) = evidence.ground {
        if (ground.supported && ground.height.is_none())
            || ground
                .height
                .is_some_and(|height| !height.is_finite() || height.abs() > 5_000.0)
        {
            return Err(PlayerEnvironmentError::InvalidInput);
        }
    }
    if evidence
        .water_depth
        .is_some_and(|depth| !depth.is_finite() || !(0.0..=100.0).contains(&depth))
    {
        return Err(PlayerEnvironmentError::InvalidInput);
    }
    Ok(())
}

fn finite_pose(pose: BodyPose) -> bool {
    [
        pose.x,
        pose.y,
        pose.z,
        pose.yaw,
        pose.pitch,
        pose.roll,
        pose.speed,
        pose.aim_yaw,
        pose.gun_pitch,
    ]
    .into_iter()
    .all(f64::is_finite)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn pose(y: f64, pitch: f64, roll: f64, speed: f64) -> BodyPose {
        BodyPose {
            x: 0.0,
            y,
            z: 0.0,
            yaw: 0.0,
            pitch,
            roll,
            speed,
            aim_yaw: 0.0,
            gun_pitch: 0.0,
        }
    }

    fn tick(
        tick: u64,
        pose: BodyPose,
        evidence: Option<(f64, f64, bool)>,
    ) -> PlayerEnvironmentTick {
        PlayerEnvironmentTick {
            tick,
            combat_live: true,
            alive: true,
            health: 1_000,
            max_health: 1_000,
            world_pose: true,
            pose,
            up_cosine: (pose.pitch.cos() * pose.roll.cos()).clamp(-1.0, 1.0),
            evidence: evidence.map(|(ground_height, water_depth, supported)| {
                PlayerEnvironmentEvidence {
                    issued_tick: tick.saturating_sub(3),
                    apply_tick: tick,
                    pose,
                    ground: Some(PlayerGroundEvidence {
                        height: Some(ground_height),
                        supported,
                    }),
                    water_depth: Some(water_depth),
                }
            }),
        }
    }

    #[test]
    fn drowning_requires_more_than_ten_seconds_of_successful_deep_samples() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        for current in 1..=300 {
            assert_eq!(
                ledger
                    .advance(tick(
                        current,
                        pose(0.0, 0.0, 0.0, 0.0),
                        (current >= 4).then_some((0.0, 2.0, true)),
                    ))
                    .unwrap(),
                None,
            );
        }
        let decision = ledger
            .advance(tick(301, pose(0.0, 0.0, 0.0, 0.0), Some((0.0, 2.0, true))))
            .unwrap();
        assert_eq!(decision, None);
        let mut decision = None;
        for current in 302..=307 {
            decision = ledger
                .advance(tick(
                    current,
                    pose(0.0, 0.0, 0.0, 0.0),
                    Some((0.0, 2.0, true)),
                ))
                .unwrap();
            if decision.is_some() {
                break;
            }
        }
        assert_eq!(decision.unwrap().cause, PlayerEnvironmentCause::Drowning);
    }

    #[test]
    fn missing_sample_neither_advances_nor_clears_drowning() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        for current in 1..=6 {
            ledger
                .advance(tick(
                    current,
                    pose(0.0, 0.0, 0.0, 0.0),
                    (current >= 4).then_some((0.0, 2.0, true)),
                ))
                .unwrap();
        }
        let before = ledger.snapshot();
        ledger
            .advance(tick(7, pose(0.0, 0.0, 0.0, 0.0), None))
            .unwrap();
        let after = ledger.snapshot();
        assert_eq!(after.deep_water_us, before.deep_water_us);
        assert!(after.drowning);
        ledger
            .advance(tick(8, pose(0.0, 0.0, 0.0, 0.0), Some((0.0, 0.5, true))))
            .unwrap();
        assert_eq!(ledger.snapshot().deep_water_us, 0);
    }

    #[test]
    fn independently_unavailable_ground_does_not_block_water_authority() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        for current in 1..=4 {
            let mut input = tick(current, pose(0.0, 0.0, 0.0, 0.0), None);
            if current == 4 {
                input.evidence = Some(PlayerEnvironmentEvidence {
                    issued_tick: 1,
                    apply_tick: 4,
                    pose: input.pose,
                    ground: None,
                    water_depth: Some(2.0),
                });
            }
            ledger.advance(input).unwrap();
        }
        assert!(ledger.snapshot().drowning);
        assert_eq!(ledger.snapshot().deep_water_us, delta_us_for_tick(4));
        assert!(!ledger.snapshot().airborne);
    }

    #[test]
    fn prebattle_and_retired_players_advance_sequence_without_damage() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        for current in 1..=4 {
            ledger
                .advance(tick(
                    current,
                    pose(0.0, 0.0, 0.0, 0.0),
                    (current == 4).then_some((0.0, 2.0, true)),
                ))
                .unwrap();
        }
        assert!(ledger.snapshot().drowning);
        let mut prebattle = tick(5, pose(0.0, 0.0, 0.0, 0.0), None);
        prebattle.combat_live = false;
        assert_eq!(ledger.advance(prebattle).unwrap(), None);
        assert!(!ledger.snapshot().drowning);

        let mut retired = tick(6, pose(0.0, 0.0, 0.0, 0.0), None);
        retired.alive = false;
        retired.health = 1_000;
        assert_eq!(ledger.advance(retired).unwrap(), None);
        assert_eq!(ledger.tick(), 6);
    }

    #[test]
    fn overturned_pose_kills_only_after_thirty_seconds() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        let upside_down = pose(0.0, 0.0, std::f64::consts::PI, 0.0);
        let mut decision = None;
        for current in 1..=905 {
            decision = ledger.advance(tick(current, upside_down, None)).unwrap();
            if decision.is_some() {
                break;
            }
        }
        assert_eq!(decision.unwrap().cause, PlayerEnvironmentCause::Overturn);
    }

    #[test]
    fn native_surface_up_cosine_overrides_stabilised_hull_angles() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        let level_pose = pose(0.0, 0.0, 0.0, 0.0);
        let mut decision = None;
        for current in 1..=905 {
            let mut input = tick(current, level_pose, None);
            input.up_cosine = -1.0;
            decision = ledger.advance(input).unwrap();
            if decision.is_some() {
                break;
            }
        }
        assert_eq!(decision.unwrap().cause, PlayerEnvironmentCause::Overturn);
    }

    #[test]
    fn native_ground_only_tracks_airborne_state_without_inferring_fall_damage() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        for current in 1..=3 {
            ledger
                .advance(tick(current, pose(0.0, 0.0, 0.0, 0.0), None))
                .unwrap();
        }
        assert_eq!(
            ledger
                .advance(tick(4, pose(10.0, 0.0, 0.0, 0.0), Some((0.0, 0.0, false)),))
                .unwrap(),
            None,
        );
        assert!(ledger.snapshot().airborne);
        assert_eq!(
            ledger
                .advance(tick(5, pose(0.0, 0.0, 0.0, 0.0), Some((0.0, 0.0, true)),))
                .unwrap(),
            None,
        );
        assert!(!ledger.snapshot().airborne);
    }

    fn landing_request(
        observation_seq: u64,
        input_seq: u64,
        speed: f64,
    ) -> LandingObservationRequest {
        LandingObservationRequest {
            round_id: 4,
            authority_epoch: 2,
            observation_seq,
            input_seq,
            impact_speed: speed,
        }
    }

    fn landing_context() -> LandingObservationContext {
        LandingObservationContext {
            scope: SimulationScope {
                round_id: 4,
                epoch: 2,
            },
            combat_active: true,
            alive: true,
            health: 90,
            max_health: 90,
            input_is_known: true,
        }
    }

    #[test]
    fn landing_wire_shape_is_exact_and_numbers_do_not_coerce() {
        let valid = WireObject::try_from(json!({
            "type": "landing_observation",
            "round_id": 4,
            "authority_epoch": 2,
            "observation_seq": 1,
            "input_seq": 1,
            "impact_speed": 20,
        }))
        .unwrap();
        assert_eq!(
            LandingObservationRequest::parse(&valid).unwrap(),
            landing_request(1, 1, 20.0),
        );

        for invalid in [
            json!({
                "type":"landing_observation", "round_id":4.0,
                "authority_epoch":2, "observation_seq":1,
                "input_seq":1, "impact_speed":20.0,
            }),
            json!({
                "type":"landing_observation", "round_id":4,
                "authority_epoch":2, "observation_seq":true,
                "input_seq":1, "impact_speed":20.0,
            }),
            json!({
                "type":"landing_observation", "round_id":4,
                "authority_epoch":"2", "observation_seq":1,
                "input_seq":1, "impact_speed":20.0,
            }),
            json!({
                "type":"landing_observation", "round_id":4,
                "authority_epoch":2, "observation_seq":1,
                "input_seq":"1", "impact_speed":20.0,
            }),
            json!({
                "type":"landing_observation", "round_id":4,
                "authority_epoch":2, "observation_seq":1,
                "input_seq":1, "impact_speed":true,
            }),
            json!({
                "type":"landing_observation", "round_id":4,
                "authority_epoch":2, "observation_seq":1,
                "input_seq":1, "impact_speed":200.1,
            }),
            json!({
                "type":"landing_observation", "round_id":4,
                "authority_epoch":2, "observation_seq":1,
                "input_seq":1, "impact_speed":20.0, "extra":0,
            }),
        ] {
            let message = WireObject::try_from(invalid).unwrap();
            assert_eq!(
                LandingObservationRequest::parse(&message),
                Err(LandingObservationParseError::InvalidShape),
            );
        }
    }

    #[test]
    fn sequenced_landing_admission_replays_exactly_and_rejects_identity_changes() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        let request = landing_request(1, 1, 20.0);
        let accepted = ledger.admit_landing_observation(request, landing_context());
        assert!(accepted.result.accepted);
        assert_eq!(accepted.result.reason, "");
        assert_eq!(accepted.result.committed_seq, 1);
        assert_eq!(accepted.damage, 27);
        assert!(accepted.newly_committed);

        let retry = ledger.admit_landing_observation(
            request,
            LandingObservationContext {
                combat_active: false,
                alive: false,
                ..landing_context()
            },
        );
        assert!(retry.result.accepted);
        assert_eq!(retry.damage, 0);
        assert!(!retry.newly_committed);

        let second =
            ledger.admit_landing_observation(landing_request(2, 2, 12.0), landing_context());
        assert!(second.result.accepted);
        assert_eq!(second.result.committed_seq, 2);
        let old_retry = ledger.admit_landing_observation(request, landing_context());
        assert!(old_retry.result.accepted);
        assert_eq!(old_retry.result.committed_seq, 1);

        let conflict =
            ledger.admit_landing_observation(landing_request(1, 1, 20.000_001), landing_context());
        assert!(!conflict.result.accepted);
        assert_eq!(conflict.result.reason, "identity_conflict");
        assert_eq!(conflict.result.committed_seq, 2);
    }

    #[test]
    fn landing_rejection_order_matches_the_python_authority_contract() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        let gap = ledger.admit_landing_observation(landing_request(2, 1, 20.0), landing_context());
        assert_eq!(gap.result.reason, "sequence_gap");

        let mut stale_authority = landing_request(1, 1, 20.0);
        stale_authority.authority_epoch = 1;
        assert_eq!(
            ledger
                .admit_landing_observation(stale_authority, landing_context())
                .result
                .reason,
            "stale_authority",
        );

        let mut inactive = landing_context();
        inactive.combat_active = false;
        assert_eq!(
            ledger
                .admit_landing_observation(landing_request(1, 1, 20.0), inactive)
                .result
                .reason,
            "not_active",
        );

        let mut dead = landing_context();
        dead.alive = false;
        dead.health = 0;
        assert_eq!(
            ledger
                .admit_landing_observation(landing_request(1, 1, 20.0), dead)
                .result
                .reason,
            "player_dead",
        );

        let mut stale_input = landing_context();
        stale_input.input_is_known = false;
        assert_eq!(
            ledger
                .admit_landing_observation(landing_request(1, 1, 20.0), stale_input)
                .result
                .reason,
            "stale_input",
        );
        assert_eq!(ledger.landing_observation_seq(), 0);
    }

    #[test]
    fn copied_landing_damage_law_uses_max_health_and_floors() {
        assert_eq!(landing_fall_damage(1_000, 10.0), 0);
        assert_eq!(landing_fall_damage(1_000, 10.999_999), 29);
        assert_eq!(landing_fall_damage(1_000, 20.0), 300);
    }

    #[test]
    fn evidence_must_be_exact_t_plus_three() {
        let mut ledger = PlayerEnvironmentLedger::new(7, 0).unwrap();
        let mut input = tick(1, pose(0.0, 0.0, 0.0, 0.0), Some((0.0, 0.0, true)));
        input.evidence.as_mut().unwrap().issued_tick = 1;
        assert_eq!(
            ledger.advance(input),
            Err(PlayerEnvironmentError::InvalidEvidenceLineage),
        );
        assert_eq!(ledger.tick(), 0);
    }
}
