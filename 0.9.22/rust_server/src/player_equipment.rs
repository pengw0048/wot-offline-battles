//! Deterministic player equipment and consumable authority.
//!
//! A visible #1513 client donates only the immutable mounted-equipment
//! contracts and sends ordered activation intents. This ledger owns charges,
//! cooldowns, trigger state, exact retries, automatic activation, and every
//! resulting critical-state proposal. Callers should clone the ledger and the
//! battle transaction, apply [`EquipmentApplication::critical_mutation`], and
//! publish both clones only after the whole command succeeds.

use crate::combat::MAX_COMBAT_ID;
use crate::critical_damage::{
    propose_device_damage_over_time, propose_repair_device, propose_restore_crew,
    propose_use_extinguisher, CrewName, CriticalCause, CriticalDamageError, CriticalMutation,
    CriticalProfile, CriticalState, DeviceCondition, DeviceName,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

pub const MAX_PLAYER_EQUIPMENT_SLOTS: usize = 3;
pub const MAX_PLAYER_EQUIPMENT_FINGERPRINTS: usize = 64;
pub const MAX_EQUIPMENT_ID: u64 = 65_535;
pub const MAX_EQUIPMENT_PENDING_SECONDS: f64 = 3_600.0;
pub const BOT_CONSUMABLE_NAMES: [&str; 3] = ["autoExtinguishers", "largeMedkit", "largeRepairkit"];

const ACTIVATION_ID_MASK: u64 = 65_535;
const CLOCK_EPSILON_SECONDS: f64 = 1.0e-9;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EquipmentKind {
    Medkit,
    Repairkit,
    Extinguisher,
    RpmLimiter,
    Stimulator,
    Fuel,
    Passive,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(untagged)]
pub enum EquipmentTarget {
    Device(DeviceName),
    Crew(CrewName),
}

impl EquipmentTarget {
    pub const fn device(self) -> Option<DeviceName> {
        match self {
            Self::Device(name) => Some(name),
            Self::Crew(_) => None,
        }
    }

    pub const fn crew(self) -> Option<CrewName> {
        match self {
            Self::Crew(name) => Some(name),
            Self::Device(_) => None,
        }
    }
}

/// Exact `equipment_mechanics.EQUIPMENT_CONTRACT_FIELDS` projection.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EquipmentContract {
    pub name: String,
    pub kind: EquipmentKind,
    pub id: u64,
    #[serde(rename = "compactDescr")]
    pub compact_descr: u64,
    pub tags: Vec<String>,
    #[serde(rename = "reuseCount")]
    pub reuse_count: i64,
    #[serde(rename = "cooldownSeconds")]
    pub cooldown_seconds: f64,
    pub autoactivate: bool,
    #[serde(rename = "fireStartingChanceFactor")]
    pub fire_starting_chance_factor: f64,
    #[serde(rename = "repairAll")]
    pub repair_all: bool,
    #[serde(rename = "bonusValue")]
    pub bonus_value: f64,
    #[serde(rename = "crewLevelIncrease")]
    pub crew_level_increase: f64,
    #[serde(rename = "enginePowerFactor")]
    pub engine_power_factor: f64,
    #[serde(rename = "turretRotationSpeedFactor")]
    pub turret_rotation_speed_factor: f64,
    #[serde(rename = "engineHpLossPerSecond")]
    pub engine_hp_loss_per_second: f64,
    #[serde(rename = "autoReactionSeconds")]
    pub auto_reaction_seconds: f64,
}

impl EquipmentContract {
    pub fn validate(&self) -> Result<(), PlayerEquipmentError> {
        if self.name.is_empty()
            || self.id == 0
            || self.compact_descr == 0
            || !self.cooldown_seconds.is_finite()
            || self.cooldown_seconds < 0.0
            || !self.fire_starting_chance_factor.is_finite()
            || self.fire_starting_chance_factor < 0.0
            || !self.bonus_value.is_finite()
            || !self.crew_level_increase.is_finite()
            || !self.engine_power_factor.is_finite()
            || self.engine_power_factor < 0.0
            || !self.turret_rotation_speed_factor.is_finite()
            || self.turret_rotation_speed_factor < 0.0
            || !self.engine_hp_loss_per_second.is_finite()
            || self.engine_hp_loss_per_second < 0.0
            || !self.auto_reaction_seconds.is_finite()
            || self.auto_reaction_seconds < 0.0
            || (self.reuse_count >= 0 && self.reuse_count.checked_add(1).is_none())
            || self.tags.iter().any(|tag| tag.to_lowercase() != *tag)
            || !self.tags.windows(2).all(|pair| pair[0] <= pair[1])
            || self.classified_kind() != self.kind
        {
            return Err(PlayerEquipmentError::InvalidContract);
        }
        Ok(())
    }

    fn classified_kind(&self) -> EquipmentKind {
        let name = self.name.to_lowercase();
        let has_tag = |expected: &str| self.tags.iter().any(|tag| tag == expected);
        if has_tag("medkit") || name.contains("medkit") {
            EquipmentKind::Medkit
        } else if has_tag("repairkit") || name.contains("repairkit") {
            EquipmentKind::Repairkit
        } else if name.contains("extinguisher")
            || self.tags.iter().any(|tag| tag.contains("extinguisher"))
        {
            EquipmentKind::Extinguisher
        } else if name.contains("removedrpmlimiter") || self.engine_hp_loss_per_second > 0.0 {
            EquipmentKind::RpmLimiter
        } else if self.crew_level_increase != 0.0 {
            EquipmentKind::Stimulator
        } else if self.engine_power_factor != 1.0 || self.turret_rotation_speed_factor != 1.0 {
            EquipmentKind::Fuel
        } else {
            EquipmentKind::Passive
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EquipmentActivationTarget {
    pub index: u64,
    pub name: EquipmentTarget,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EquipmentIntent {
    pub intent_seq: u64,
    pub equipment_id: u64,
    pub activation_code: u64,
    pub selected: Option<EquipmentTarget>,
    pub requested_active: Option<bool>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
enum EquipmentIntentMessageKind {
    #[serde(rename = "equipment_intent")]
    EquipmentIntent,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct EquipmentIntentMessage {
    #[serde(rename = "type")]
    kind: EquipmentIntentMessageKind,
    round_id: u64,
    intent_seq: u64,
    equipment_id: u64,
    activation_code: u64,
    selected: Option<EquipmentTarget>,
    requested_active: Option<bool>,
}

/// Decode the exact current-main visible-client message shape and fence it to
/// the active round before the sequence ledger sees it.
pub fn decode_equipment_intent(
    value: &Value,
    expected_round_id: u64,
) -> Result<EquipmentIntent, PlayerEquipmentError> {
    if !(1..=MAX_COMBAT_ID).contains(&expected_round_id) {
        return Err(PlayerEquipmentError::InvalidRound);
    }
    let fields = value
        .as_object()
        .ok_or_else(|| PlayerEquipmentError::InvalidWire("message is not an object".into()))?;
    let expected_fields = BTreeSet::from([
        "type",
        "round_id",
        "intent_seq",
        "equipment_id",
        "activation_code",
        "selected",
        "requested_active",
    ]);
    if fields.keys().map(String::as_str).collect::<BTreeSet<_>>() != expected_fields {
        return Err(PlayerEquipmentError::InvalidWire(
            "message fields are incomplete".into(),
        ));
    }
    let message: EquipmentIntentMessage = serde_json::from_value(value.clone())
        .map_err(|error| PlayerEquipmentError::InvalidWire(error.to_string()))?;
    if message.round_id != expected_round_id || !(1..=MAX_COMBAT_ID).contains(&message.round_id) {
        return Err(PlayerEquipmentError::InvalidRound);
    }
    let intent = EquipmentIntent {
        intent_seq: message.intent_seq,
        equipment_id: message.equipment_id,
        activation_code: message.activation_code,
        selected: message.selected,
        requested_active: message.requested_active,
    };
    validate_intent_identity(&intent)?;
    Ok(intent)
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct EquipmentCriticalView {
    pub damaged_devices: BTreeSet<DeviceName>,
    pub knocked_out_crew: BTreeSet<CrewName>,
    pub on_fire: bool,
    pub stunned: bool,
}

impl EquipmentCriticalView {
    pub fn from_state(state: &CriticalState, stunned: bool) -> Self {
        Self {
            damaged_devices: state
                .devices
                .iter()
                .filter_map(|(&name, device)| {
                    matches!(
                        device.condition,
                        DeviceCondition::Critical | DeviceCondition::Destroyed
                    )
                    .then_some(name)
                })
                .collect(),
            knocked_out_crew: state.crew_ko.clone(),
            on_fire: state.on_fire,
            stunned,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct PlayerEquipmentContext<'a> {
    pub now_seconds: f64,
    pub combat_accepting: bool,
    pub battle_result_committed: bool,
    pub participating: bool,
    pub alive: bool,
    pub stunned: bool,
    pub critical_profile: &'a CriticalProfile,
    pub critical_state: &'a CriticalState,
}

impl PlayerEquipmentContext<'_> {
    fn active(self) -> bool {
        self.combat_accepting && !self.battle_result_committed && self.participating && self.alive
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum EquipmentEffect {
    ExtinguishFire,
    RepairDevices {
        selected: Option<DeviceName>,
        repair_all: bool,
        bonus_value: f64,
    },
    RestoreCrew {
        selected: Option<CrewName>,
        restore_all: bool,
        bonus_value: f64,
        clear_stun: bool,
    },
    SetRpmLimiter {
        active: bool,
        engine_power_factor: f64,
        engine_hp_loss_per_second: f64,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub struct EquipmentApplication {
    pub equipment_id: u64,
    pub effect: EquipmentEffect,
    pub critical_mutation: Option<CriticalMutation>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EquipmentIntentDisposition {
    New,
    ExactRetry,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EquipmentIntentRejection {
    VehicleNotAlive,
    EquipmentNotMounted,
    InvalidActivationMode,
    InvalidActivationCode,
    InvalidEquipmentTarget,
    AutomaticOnly,
    EquipmentIneligible,
    EquipmentNoEffect,
}

impl EquipmentIntentRejection {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::VehicleNotAlive => "vehicle_not_alive",
            Self::EquipmentNotMounted => "equipment_not_mounted",
            Self::InvalidActivationMode => "invalid_activation_mode",
            Self::InvalidActivationCode => "invalid_activation_code",
            Self::InvalidEquipmentTarget => "invalid_equipment_target",
            Self::AutomaticOnly => "automatic_only",
            Self::EquipmentIneligible => "equipment_ineligible",
            Self::EquipmentNoEffect => "equipment_no_effect",
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EquipmentIntentResult {
    pub intent_seq: u64,
    pub accepted: bool,
    pub reason: String,
}

impl Default for EquipmentIntentResult {
    fn default() -> Self {
        Self {
            intent_seq: 0,
            accepted: false,
            reason: String::new(),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct EquipmentIntentOutcome {
    pub disposition: EquipmentIntentDisposition,
    /// The canonical current snapshot result. On an old exact retry this may
    /// describe a later already-committed sequence, matching Python's ledger.
    pub current_result: EquipmentIntentResult,
    pub rejection: Option<EquipmentIntentRejection>,
    pub application: Option<EquipmentApplication>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EquipmentStateSnapshot {
    pub equipment: EquipmentContract,
    #[serde(rename = "usesLeft")]
    pub uses_left: i64,
    #[serde(rename = "cooldownTimeLeft")]
    pub cooldown_time_left: f64,
    pub active: bool,
    #[serde(rename = "autoPendingElapsed")]
    pub auto_pending_elapsed: Option<f64>,
    #[serde(rename = "aiPendingElapsed")]
    pub ai_pending_elapsed: Option<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PlayerEquipmentSnapshot {
    pub equipment_states: Vec<EquipmentStateSnapshot>,
    pub equipment_revision: u64,
    pub equipment_intent_seq: u64,
    pub equipment_intent_result: EquipmentIntentResult,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotEquipmentApplication {
    pub application: EquipmentApplication,
    /// Exact end time observed while admitting a large-medkit stun clear.
    /// The battle ledger must compare-and-swap this value before committing
    /// the staged equipment ledger.
    pub stun_base_end_server_time_ms: Option<u64>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct EquipmentPassiveEffects {
    pub fire_starting_chance_factor: f64,
    pub repairkit_bonus_value: f64,
    pub medkit_bonus_value: f64,
    pub crew_level_increase: f64,
    pub engine_power_factor: f64,
    pub turret_rotation_speed_factor: f64,
    pub engine_hp_loss_per_second: f64,
}

impl Default for EquipmentPassiveEffects {
    fn default() -> Self {
        Self {
            fire_starting_chance_factor: 1.0,
            repairkit_bonus_value: 0.0,
            medkit_bonus_value: 0.0,
            crew_level_increase: 0.0,
            engine_power_factor: 1.0,
            turret_rotation_speed_factor: 1.0,
            engine_hp_loss_per_second: 0.0,
        }
    }
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum PlayerEquipmentError {
    #[error("player id must be in 1..=2147483647")]
    InvalidPlayerId,
    #[error("equipment contract is invalid")]
    InvalidContract,
    #[error("player equipment loadout exceeds three slots or duplicates an identity")]
    InvalidLoadout,
    #[error("bot equipment loadout is not the exact ordered #1513 default policy")]
    InvalidBotLoadout,
    #[error("equipment activation-target projection is invalid")]
    InvalidActivationTargets,
    #[error("effective_params equipment projection is invalid: {0}")]
    InvalidProjection(String),
    #[error("equipment intent wire shape is invalid: {0}")]
    InvalidWire(String),
    #[error("equipment intent round is invalid")]
    InvalidRound,
    #[error("equipment intent sequence is invalid")]
    InvalidIntentSequence,
    #[error("equipment intent identity is invalid")]
    InvalidIntentIdentity,
    #[error("equipment intent sequence {received} does not follow {last}")]
    IntentSequenceGap { last: u64, received: u64 },
    #[error("equipment intent sequence {sequence} fell outside the exact-retry window")]
    StaleIntentRetry { sequence: u64 },
    #[error("equipment intent sequence {sequence} was reused with different content")]
    IdentityConflict { sequence: u64 },
    #[error("equipment authority clock is invalid")]
    InvalidClock,
    #[error("equipment authority delta is invalid")]
    InvalidDelta,
    #[error("equipment revision is exhausted")]
    RevisionExhausted,
    #[error("automatic equipment produced no canonical mutation")]
    AutomaticNoEffect,
    #[error(transparent)]
    Critical(#[from] CriticalDamageError),
}

#[derive(Clone, Debug)]
struct EquipmentState {
    contract: EquipmentContract,
    uses_left: i64,
    ready_at: f64,
    active: bool,
    auto_pending_since: Option<f64>,
    ai_pending_since: Option<f64>,
}

impl EquipmentState {
    fn new(contract: EquipmentContract, now_seconds: f64) -> Result<Self, PlayerEquipmentError> {
        contract.validate()?;
        let uses_left = if contract.reuse_count < 0 {
            -1
        } else {
            contract
                .reuse_count
                .checked_add(1)
                .ok_or(PlayerEquipmentError::InvalidContract)?
        };
        Ok(Self {
            contract,
            uses_left,
            ready_at: now_seconds,
            active: false,
            auto_pending_since: None,
            ai_pending_since: None,
        })
    }

    fn ready(&self, now_seconds: f64) -> bool {
        self.uses_left != 0 && now_seconds >= self.ready_at
    }

    fn activate(&mut self, now_seconds: f64, effect: &EquipmentEffect) {
        if let EquipmentEffect::SetRpmLimiter { active, .. } = effect {
            self.active = *active;
        } else {
            if self.uses_left > 0 {
                self.uses_left -= 1;
            }
            self.ready_at = now_seconds + self.contract.cooldown_seconds;
        }
        self.auto_pending_since = None;
        self.ai_pending_since = None;
    }

    fn snapshot(&self, now_seconds: f64) -> EquipmentStateSnapshot {
        EquipmentStateSnapshot {
            equipment: self.contract.clone(),
            uses_left: self.uses_left,
            cooldown_time_left: (self.ready_at - now_seconds).max(0.0),
            active: self.active,
            auto_pending_elapsed: self
                .auto_pending_since
                .map(|started| (now_seconds - started).max(0.0)),
            ai_pending_elapsed: self
                .ai_pending_since
                .map(|started| (now_seconds - started).max(0.0)),
        }
    }
}

/// Server-owned mutable equipment state for one visible participant.
#[derive(Clone, Debug)]
pub struct PlayerEquipmentLedger {
    player_id: u64,
    clock_seconds: f64,
    revision: u64,
    intent_seq: u64,
    intent_result: EquipmentIntentResult,
    activation_targets: BTreeMap<u64, EquipmentTarget>,
    states: Vec<EquipmentState>,
    intent_receipts: BTreeMap<u64, EquipmentIntent>,
}

impl PlayerEquipmentLedger {
    pub fn new(
        player_id: u64,
        now_seconds: f64,
        contracts: Vec<EquipmentContract>,
        activation_targets: Vec<EquipmentActivationTarget>,
    ) -> Result<Self, PlayerEquipmentError> {
        if !(1..=MAX_COMBAT_ID).contains(&player_id) {
            return Err(PlayerEquipmentError::InvalidPlayerId);
        }
        validate_clock(now_seconds, now_seconds)?;
        if contracts.len() > MAX_PLAYER_EQUIPMENT_SLOTS {
            return Err(PlayerEquipmentError::InvalidLoadout);
        }
        let mut ids = BTreeSet::new();
        let mut compact_descriptors = BTreeSet::new();
        let mut states = Vec::with_capacity(contracts.len());
        for contract in contracts {
            if !ids.insert(contract.id) || !compact_descriptors.insert(contract.compact_descr) {
                return Err(PlayerEquipmentError::InvalidLoadout);
            }
            states.push(EquipmentState::new(contract, now_seconds)?);
        }

        let mut targets = BTreeMap::new();
        for target in activation_targets {
            if !(1..=MAX_EQUIPMENT_ID).contains(&target.index)
                || targets.insert(target.index, target.name).is_some()
            {
                return Err(PlayerEquipmentError::InvalidActivationTargets);
            }
        }
        Ok(Self {
            player_id,
            clock_seconds: now_seconds,
            // Python publishes revision one immediately after installing the
            // immutable round loadout, including an empty loadout.
            revision: 1,
            intent_seq: 0,
            intent_result: EquipmentIntentResult::default(),
            activation_targets: targets,
            states,
            intent_receipts: BTreeMap::new(),
        })
    }

    /// Read the two exact fields consumed from canonical effective_params.
    /// Full effective-parameter validation remains the descriptor boundary's
    /// responsibility; this function neither accepts nor invents alternatives.
    pub fn from_effective_params(
        player_id: u64,
        now_seconds: f64,
        effective_params: &Value,
    ) -> Result<Self, PlayerEquipmentError> {
        let root = effective_params.as_object().ok_or_else(|| {
            PlayerEquipmentError::InvalidProjection("effective_params is not an object".into())
        })?;
        let equipment = root.get("equipment").ok_or_else(|| {
            PlayerEquipmentError::InvalidProjection("equipment is missing".into())
        })?;
        let critical = root
            .get("critical")
            .and_then(Value::as_object)
            .ok_or_else(|| PlayerEquipmentError::InvalidProjection("critical is missing".into()))?;
        let targets = critical
            .get("activation_targets")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new()));
        let contracts: Vec<EquipmentContract> = serde_json::from_value(equipment.clone())
            .map_err(|error| PlayerEquipmentError::InvalidProjection(error.to_string()))?;
        let activation_targets: Vec<EquipmentActivationTarget> = serde_json::from_value(targets)
            .map_err(|error| PlayerEquipmentError::InvalidProjection(error.to_string()))?;
        Self::new(player_id, now_seconds, contracts, activation_targets)
    }

    pub fn player_id(&self) -> u64 {
        self.player_id
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }

    pub fn intent_seq(&self) -> u64 {
        self.intent_seq
    }

    pub fn intent_result(&self) -> &EquipmentIntentResult {
        &self.intent_result
    }

    pub fn clock_seconds(&self) -> f64 {
        self.clock_seconds
    }

    pub fn snapshot(&self) -> PlayerEquipmentSnapshot {
        PlayerEquipmentSnapshot {
            equipment_states: self
                .states
                .iter()
                .map(|state| state.snapshot(self.clock_seconds))
                .collect(),
            equipment_revision: self.revision,
            equipment_intent_seq: self.intent_seq,
            equipment_intent_result: self.intent_result.clone(),
        }
    }

    pub fn advance_clock(&mut self, now_seconds: f64) -> Result<(), PlayerEquipmentError> {
        validate_clock(self.clock_seconds, now_seconds)?;
        self.clock_seconds = now_seconds;
        Ok(())
    }

    pub fn passive_effects(&self) -> EquipmentPassiveEffects {
        equipment_passive_effects(&self.states)
    }

    /// Build the exact deterministic engine-module drain caused by active RPM
    /// limiter items. No hull-health healing or damage exists in this contract.
    pub fn propose_engine_damage(
        &self,
        profile: &CriticalProfile,
        state: &CriticalState,
        current_hull_health: u32,
        dt_seconds: f64,
    ) -> Result<Option<CriticalMutation>, PlayerEquipmentError> {
        if !dt_seconds.is_finite() || dt_seconds < 0.0 {
            return Err(PlayerEquipmentError::InvalidDelta);
        }
        let amount = self.passive_effects().engine_hp_loss_per_second * dt_seconds;
        if !amount.is_finite() {
            return Err(PlayerEquipmentError::InvalidDelta);
        }
        if amount <= 0.0 {
            return Ok(None);
        }
        Ok(Some(propose_device_damage_over_time(
            profile,
            state,
            current_hull_health,
            DeviceName::EngineHealth,
            amount,
            CriticalCause::Equipment,
        )?))
    }

    pub fn admit_intent(
        &mut self,
        intent: EquipmentIntent,
        context: PlayerEquipmentContext<'_>,
    ) -> Result<EquipmentIntentOutcome, PlayerEquipmentError> {
        validate_intent_identity(&intent)?;
        if let Some(previous) = self.intent_receipts.get(&intent.intent_seq) {
            return if previous == &intent {
                Ok(EquipmentIntentOutcome {
                    disposition: EquipmentIntentDisposition::ExactRetry,
                    current_result: self.intent_result.clone(),
                    rejection: None,
                    application: None,
                })
            } else {
                Err(PlayerEquipmentError::IdentityConflict {
                    sequence: intent.intent_seq,
                })
            };
        }
        if intent.intent_seq <= self.intent_seq {
            return Err(PlayerEquipmentError::StaleIntentRetry {
                sequence: intent.intent_seq,
            });
        }
        let expected = self
            .intent_seq
            .checked_add(1)
            .ok_or(PlayerEquipmentError::InvalidIntentSequence)?;
        if intent.intent_seq != expected {
            return Err(PlayerEquipmentError::IntentSequenceGap {
                last: self.intent_seq,
                received: intent.intent_seq,
            });
        }
        validate_clock(self.clock_seconds, context.now_seconds)?;

        let mut staged = self.clone();
        staged.clock_seconds = context.now_seconds;
        staged.intent_seq = intent.intent_seq;
        staged
            .intent_receipts
            .insert(intent.intent_seq, intent.clone());
        prune_receipts(&mut staged.intent_receipts);

        let (rejection, application) = staged.resolve_new_intent(&intent, context)?;
        staged.intent_result = EquipmentIntentResult {
            intent_seq: intent.intent_seq,
            accepted: rejection.is_none(),
            reason: rejection
                .map(EquipmentIntentRejection::as_str)
                .unwrap_or("")
                .to_owned(),
        };
        let outcome = EquipmentIntentOutcome {
            disposition: EquipmentIntentDisposition::New,
            current_result: staged.intent_result.clone(),
            rejection,
            application,
        };
        *self = staged;
        Ok(outcome)
    }

    /// Poll automatic items in mounted order at one canonical server clock.
    /// The returned mutations are chained over a staged critical state, so two
    /// items can never both consume the same fire or damaged target.
    pub fn advance_automatic(
        &mut self,
        now_seconds: f64,
        profile: &CriticalProfile,
        state: &CriticalState,
    ) -> Result<Vec<EquipmentApplication>, PlayerEquipmentError> {
        validate_clock(self.clock_seconds, now_seconds)?;
        let mut staged = self.clone();
        staged.clock_seconds = now_seconds;
        let mut staged_critical = state.clone();
        let mut applications = Vec::new();

        for index in 0..staged.states.len() {
            if !staged.states[index].contract.autoactivate {
                continue;
            }
            let view = EquipmentCriticalView::from_state(&staged_critical, false);
            let candidate = effect_policy(&staged.states[index], &view, None, None);
            if candidate.is_none() || !staged.states[index].ready(now_seconds) {
                staged.states[index].auto_pending_since = None;
                continue;
            }
            if staged.states[index].auto_pending_since.is_none() {
                staged.states[index].auto_pending_since = Some(now_seconds);
                if staged.states[index].contract.auto_reaction_seconds > 0.0 {
                    continue;
                }
            }
            let started = staged.states[index]
                .auto_pending_since
                .expect("eligible automatic equipment starts its timer");
            if now_seconds - started + CLOCK_EPSILON_SECONDS
                < staged.states[index].contract.auto_reaction_seconds
            {
                continue;
            }
            let effect = candidate.expect("eligible automatic effect was checked");
            let equipment_id = staged.states[index].contract.id;
            let application =
                build_application(equipment_id, effect.clone(), profile, &staged_critical)?
                    .ok_or(PlayerEquipmentError::AutomaticNoEffect)?;
            let revision = staged.next_revision()?;
            staged.states[index].activate(now_seconds, &effect);
            staged.revision = revision;
            if let Some(mutation) = &application.critical_mutation {
                staged_critical = mutation.state().clone();
            }
            applications.push(application);
        }
        *self = staged;
        Ok(applications)
    }

    fn resolve_new_intent(
        &mut self,
        intent: &EquipmentIntent,
        context: PlayerEquipmentContext<'_>,
    ) -> Result<
        (
            Option<EquipmentIntentRejection>,
            Option<EquipmentApplication>,
        ),
        PlayerEquipmentError,
    > {
        if !context.active() {
            return Ok((Some(EquipmentIntentRejection::VehicleNotAlive), None));
        }
        let Some(index) = self
            .states
            .iter()
            .position(|state| state.contract.id == intent.equipment_id)
        else {
            return Ok((Some(EquipmentIntentRejection::EquipmentNotMounted), None));
        };
        let kind = self.states[index].contract.kind;
        let repair_all = self.states[index].contract.repair_all;
        let extra_index = intent.activation_code >> 16;

        if (kind == EquipmentKind::RpmLimiter && intent.requested_active.is_none())
            || (kind != EquipmentKind::RpmLimiter && intent.requested_active.is_some())
        {
            return Ok((Some(EquipmentIntentRejection::InvalidActivationMode), None));
        }
        if repair_all
            && (!matches!(kind, EquipmentKind::Repairkit | EquipmentKind::Medkit)
                || extra_index != 1
                || intent.selected.is_some())
        {
            return Ok((Some(EquipmentIntentRejection::InvalidActivationCode), None));
        }
        if matches!(kind, EquipmentKind::Repairkit | EquipmentKind::Medkit) && !repair_all {
            if extra_index == 0
                || self.activation_targets.get(&extra_index).copied() != intent.selected
            {
                return Ok((Some(EquipmentIntentRejection::InvalidActivationCode), None));
            }
        } else if kind == EquipmentKind::RpmLimiter {
            if !matches!(extra_index, 0 | 1)
                || (extra_index == 1) != intent.requested_active.unwrap_or(false)
            {
                return Ok((Some(EquipmentIntentRejection::InvalidActivationCode), None));
            }
        } else if !repair_all && extra_index != 0 {
            return Ok((Some(EquipmentIntentRejection::InvalidActivationCode), None));
        }

        let valid_target = match kind {
            EquipmentKind::Repairkit => {
                repair_all && intent.selected.is_none()
                    || !repair_all && intent.selected.and_then(EquipmentTarget::device).is_some()
            }
            EquipmentKind::Medkit => {
                repair_all && intent.selected.is_none()
                    || !repair_all && intent.selected.and_then(EquipmentTarget::crew).is_some()
            }
            _ => intent.selected.is_none(),
        };
        if !valid_target {
            return Ok((Some(EquipmentIntentRejection::InvalidEquipmentTarget), None));
        }
        if kind == EquipmentKind::Extinguisher && self.states[index].contract.autoactivate {
            return Ok((Some(EquipmentIntentRejection::AutomaticOnly), None));
        }

        let view = EquipmentCriticalView::from_state(context.critical_state, context.stunned);
        let effect = effect_policy(
            &self.states[index],
            &view,
            intent.selected,
            intent.requested_active,
        );
        if effect.is_none() || !self.states[index].ready(context.now_seconds) {
            return Ok((Some(EquipmentIntentRejection::EquipmentIneligible), None));
        }
        let effect = effect.expect("eligible manual effect was checked");
        let application = build_application(
            intent.equipment_id,
            effect.clone(),
            context.critical_profile,
            context.critical_state,
        )?;
        let Some(application) = application else {
            return Ok((Some(EquipmentIntentRejection::EquipmentNoEffect), None));
        };
        let revision = self.next_revision()?;
        self.states[index].activate(context.now_seconds, &effect);
        self.revision = revision;
        Ok((None, Some(application)))
    }

    fn next_revision(&self) -> Result<u64, PlayerEquipmentError> {
        let revision = self
            .revision
            .checked_add(1)
            .ok_or(PlayerEquipmentError::RevisionExhausted)?;
        if revision > MAX_COMBAT_ID {
            return Err(PlayerEquipmentError::RevisionExhausted);
        }
        Ok(revision)
    }
}

/// Server-owned mutable equipment state for one Rust-simulated bot.
///
/// Every bot receives an independent ledger built from the one immutable
/// three-item contract donated by the hidden #1513 oracle. The policy mirrors
/// Python `EquipmentState.poll_bot`: automatic extinguishers follow their
/// donated reaction delay, while repair/med kits require one eligible
/// observation and a strictly later simulation clock before activation.
#[derive(Clone, Debug)]
pub struct BotEquipmentLedger {
    bot_id: u64,
    clock_seconds: f64,
    states: Vec<EquipmentState>,
}

impl BotEquipmentLedger {
    pub fn new(
        bot_id: u64,
        now_seconds: f64,
        contracts: Vec<EquipmentContract>,
    ) -> Result<Self, PlayerEquipmentError> {
        if !(1..=MAX_COMBAT_ID).contains(&bot_id) {
            return Err(PlayerEquipmentError::InvalidPlayerId);
        }
        validate_clock(now_seconds, now_seconds)?;
        validate_bot_consumable_contracts(&contracts)?;
        let states = contracts
            .into_iter()
            .map(|contract| EquipmentState::new(contract, now_seconds))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            bot_id,
            clock_seconds: now_seconds,
            states,
        })
    }

    pub fn bot_id(&self) -> u64 {
        self.bot_id
    }

    pub fn clock_seconds(&self) -> f64 {
        self.clock_seconds
    }

    pub fn snapshot(&self) -> Vec<EquipmentStateSnapshot> {
        self.states
            .iter()
            .map(|state| state.snapshot(self.clock_seconds))
            .collect()
    }

    pub fn passive_effects(&self) -> EquipmentPassiveEffects {
        equipment_passive_effects(&self.states)
    }

    pub fn advance_clock(&mut self, now_seconds: f64) -> Result<(), PlayerEquipmentError> {
        validate_clock(self.clock_seconds, now_seconds)?;
        self.clock_seconds = now_seconds;
        Ok(())
    }

    /// Stage all eligible mounted items in their donated order.
    ///
    /// The caller must itself operate on a clone and publish that clone only
    /// after the battle's critical/stun transaction commits. This method
    /// chains mutations locally so an extinguisher, medkit and repair kit can
    /// never race over one stale critical-state view.
    pub fn advance_policy(
        &mut self,
        now_seconds: f64,
        profile: &CriticalProfile,
        state: &CriticalState,
        stun_end_server_time_ms: Option<u64>,
    ) -> Result<Vec<BotEquipmentApplication>, PlayerEquipmentError> {
        validate_clock(self.clock_seconds, now_seconds)?;
        if stun_end_server_time_ms == Some(0) {
            return Err(PlayerEquipmentError::InvalidClock);
        }
        let mut staged = self.clone();
        staged.clock_seconds = now_seconds;
        let mut staged_critical = state.clone();
        let mut staged_stun_end = stun_end_server_time_ms;
        let mut applications = Vec::new();

        for index in 0..staged.states.len() {
            let view =
                EquipmentCriticalView::from_state(&staged_critical, staged_stun_end.is_some());
            let effect = match staged.states[index].contract.kind {
                EquipmentKind::Extinguisher => {
                    let candidate = effect_policy(&staged.states[index], &view, None, None);
                    if candidate.is_none() || !staged.states[index].ready(now_seconds) {
                        staged.states[index].auto_pending_since = None;
                        continue;
                    }
                    if staged.states[index].auto_pending_since.is_none() {
                        staged.states[index].auto_pending_since = Some(now_seconds);
                        if staged.states[index].contract.auto_reaction_seconds > 0.0 {
                            continue;
                        }
                    }
                    let started = staged.states[index]
                        .auto_pending_since
                        .expect("eligible automatic equipment starts its timer");
                    if now_seconds - started + CLOCK_EPSILON_SECONDS
                        < staged.states[index].contract.auto_reaction_seconds
                    {
                        continue;
                    }
                    candidate.expect("eligible automatic effect was checked")
                }
                EquipmentKind::Repairkit | EquipmentKind::Medkit => {
                    let candidate = effect_policy(&staged.states[index], &view, None, None);
                    if candidate.is_none() || !staged.states[index].ready(now_seconds) {
                        staged.states[index].ai_pending_since = None;
                        continue;
                    }
                    if staged.states[index].ai_pending_since.is_none() {
                        staged.states[index].ai_pending_since = Some(now_seconds);
                        continue;
                    }
                    let started = staged.states[index]
                        .ai_pending_since
                        .expect("eligible bot equipment starts its observation clock");
                    if now_seconds <= started + CLOCK_EPSILON_SECONDS {
                        continue;
                    }
                    candidate.expect("eligible bot effect was checked")
                }
                _ => return Err(PlayerEquipmentError::InvalidBotLoadout),
            };

            let equipment_id = staged.states[index].contract.id;
            let application =
                build_application(equipment_id, effect.clone(), profile, &staged_critical)?
                    .ok_or(PlayerEquipmentError::AutomaticNoEffect)?;
            let stun_base_end_server_time_ms = matches!(
                &application.effect,
                EquipmentEffect::RestoreCrew {
                    clear_stun: true,
                    ..
                }
            )
            .then_some(staged_stun_end)
            .flatten();
            staged.states[index].activate(now_seconds, &effect);
            if let Some(mutation) = &application.critical_mutation {
                staged_critical = mutation.state().clone();
            }
            if stun_base_end_server_time_ms.is_some() {
                staged_stun_end = None;
            }
            applications.push(BotEquipmentApplication {
                application,
                stun_base_end_server_time_ms,
            });
        }
        *self = staged;
        Ok(applications)
    }
}

pub fn validate_bot_consumable_contracts(
    contracts: &[EquipmentContract],
) -> Result<(), PlayerEquipmentError> {
    if contracts.len() != BOT_CONSUMABLE_NAMES.len() {
        return Err(PlayerEquipmentError::InvalidBotLoadout);
    }
    let expected_kinds = [
        EquipmentKind::Extinguisher,
        EquipmentKind::Medkit,
        EquipmentKind::Repairkit,
    ];
    let mut ids = BTreeSet::new();
    let mut compact_descriptors = BTreeSet::new();
    for (index, contract) in contracts.iter().enumerate() {
        contract.validate()?;
        if contract.name != BOT_CONSUMABLE_NAMES[index]
            || contract.kind != expected_kinds[index]
            || !ids.insert(contract.id)
            || !compact_descriptors.insert(contract.compact_descr)
        {
            return Err(PlayerEquipmentError::InvalidBotLoadout);
        }
    }
    if !contracts[0].autoactivate || !contracts[1].repair_all || !contracts[2].repair_all {
        return Err(PlayerEquipmentError::InvalidBotLoadout);
    }
    Ok(())
}

fn equipment_passive_effects(states: &[EquipmentState]) -> EquipmentPassiveEffects {
    let mut result = EquipmentPassiveEffects::default();
    for state in states {
        let contract = &state.contract;
        match contract.kind {
            EquipmentKind::Extinguisher => {
                result.fire_starting_chance_factor *= contract.fire_starting_chance_factor;
            }
            EquipmentKind::Repairkit => {
                result.repairkit_bonus_value += contract.bonus_value;
            }
            EquipmentKind::Medkit => {
                result.medkit_bonus_value += contract.bonus_value;
            }
            _ => {}
        }
        result.crew_level_increase += contract.crew_level_increase;
        if contract.kind == EquipmentKind::Fuel
            || (contract.kind == EquipmentKind::RpmLimiter && state.active)
        {
            result.engine_power_factor *= contract.engine_power_factor;
        }
        if contract.kind == EquipmentKind::Fuel {
            result.turret_rotation_speed_factor *= contract.turret_rotation_speed_factor;
        }
        if contract.kind == EquipmentKind::RpmLimiter && state.active {
            result.engine_hp_loss_per_second += contract.engine_hp_loss_per_second;
        }
    }
    result
}

fn validate_intent_identity(intent: &EquipmentIntent) -> Result<(), PlayerEquipmentError> {
    if !(1..=MAX_COMBAT_ID).contains(&intent.intent_seq) {
        return Err(PlayerEquipmentError::InvalidIntentSequence);
    }
    if !(1..=MAX_EQUIPMENT_ID).contains(&intent.equipment_id)
        || !(1..=MAX_COMBAT_ID).contains(&intent.activation_code)
        || intent.activation_code & ACTIVATION_ID_MASK != intent.equipment_id
    {
        return Err(PlayerEquipmentError::InvalidIntentIdentity);
    }
    Ok(())
}

fn validate_clock(previous: f64, now: f64) -> Result<(), PlayerEquipmentError> {
    if !previous.is_finite() || previous < 0.0 || !now.is_finite() || now < previous {
        return Err(PlayerEquipmentError::InvalidClock);
    }
    Ok(())
}

fn prune_receipts(receipts: &mut BTreeMap<u64, EquipmentIntent>) {
    while receipts.len() > MAX_PLAYER_EQUIPMENT_FINGERPRINTS {
        if let Some(sequence) = receipts.keys().next().copied() {
            receipts.remove(&sequence);
        }
    }
}

fn effect_policy(
    state: &EquipmentState,
    critical: &EquipmentCriticalView,
    selected: Option<EquipmentTarget>,
    requested_active: Option<bool>,
) -> Option<EquipmentEffect> {
    let contract = &state.contract;
    match contract.kind {
        EquipmentKind::Extinguisher => critical.on_fire.then_some(EquipmentEffect::ExtinguishFire),
        EquipmentKind::Repairkit => {
            let selected = selected.and_then(EquipmentTarget::device);
            if critical.damaged_devices.is_empty()
                || (!contract.repair_all
                    && selected.is_none_or(|name| !critical.damaged_devices.contains(&name)))
            {
                None
            } else {
                Some(EquipmentEffect::RepairDevices {
                    selected: (!contract.repair_all).then_some(selected).flatten(),
                    repair_all: contract.repair_all,
                    bonus_value: contract.bonus_value,
                })
            }
        }
        EquipmentKind::Medkit => {
            let selected = selected.and_then(EquipmentTarget::crew);
            if (critical.knocked_out_crew.is_empty() && !critical.stunned)
                || (!contract.repair_all
                    && selected.is_none_or(|name| {
                        !critical.knocked_out_crew.contains(&name) && !critical.stunned
                    }))
            {
                None
            } else {
                Some(EquipmentEffect::RestoreCrew {
                    selected: (!contract.repair_all).then_some(selected).flatten(),
                    restore_all: contract.repair_all,
                    bonus_value: contract.bonus_value,
                    clear_stun: critical.stunned,
                })
            }
        }
        EquipmentKind::RpmLimiter => {
            let active = requested_active.unwrap_or(false);
            (active != state.active).then_some(EquipmentEffect::SetRpmLimiter {
                active,
                engine_power_factor: contract.engine_power_factor,
                engine_hp_loss_per_second: contract.engine_hp_loss_per_second,
            })
        }
        EquipmentKind::Stimulator | EquipmentKind::Fuel | EquipmentKind::Passive => None,
    }
}

fn build_application(
    equipment_id: u64,
    effect: EquipmentEffect,
    profile: &CriticalProfile,
    state: &CriticalState,
) -> Result<Option<EquipmentApplication>, PlayerEquipmentError> {
    let mutation = match &effect {
        EquipmentEffect::ExtinguishFire => Some(propose_use_extinguisher(profile, state)?),
        EquipmentEffect::RepairDevices {
            selected,
            repair_all,
            ..
        } => Some(propose_repair_device(
            profile,
            state,
            *selected,
            *repair_all,
        )?),
        EquipmentEffect::RestoreCrew {
            selected,
            restore_all,
            ..
        } => Some(propose_restore_crew(
            profile,
            state,
            *selected,
            *restore_all,
        )?),
        EquipmentEffect::SetRpmLimiter { .. } => None,
    };
    let clear_stun = matches!(
        effect,
        EquipmentEffect::RestoreCrew {
            clear_stun: true,
            ..
        }
    );
    if mutation
        .as_ref()
        .is_some_and(|candidate| !candidate.changes_internal_state())
        && !clear_stun
    {
        return Ok(None);
    }
    Ok(Some(EquipmentApplication {
        equipment_id,
        effect,
        critical_mutation: mutation,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::critical_damage::{
        CrewMemberProfile, CrewRole, DeviceProfile, DeviceState, ALL_DEVICE_NAMES,
    };
    use serde_json::json;

    fn contract(name: &str, kind: EquipmentKind, id: u64) -> EquipmentContract {
        EquipmentContract {
            name: name.to_owned(),
            kind,
            id,
            compact_descr: 11_000 + id,
            tags: match kind {
                EquipmentKind::Medkit => vec!["medkit".to_owned()],
                EquipmentKind::Repairkit => vec!["repairkit".to_owned()],
                _ => Vec::new(),
            },
            reuse_count: 0,
            cooldown_seconds: 0.0,
            autoactivate: false,
            fire_starting_chance_factor: 1.0,
            repair_all: false,
            bonus_value: 0.0,
            crew_level_increase: 0.0,
            engine_power_factor: 1.0,
            turret_rotation_speed_factor: 1.0,
            engine_hp_loss_per_second: 0.0,
            auto_reaction_seconds: 0.0,
        }
    }

    fn bot_contracts() -> Vec<EquipmentContract> {
        let mut extinguisher = contract("autoExtinguishers", EquipmentKind::Extinguisher, 21);
        extinguisher.autoactivate = true;
        extinguisher.fire_starting_chance_factor = 0.9;
        let mut medkit = contract("largeMedkit", EquipmentKind::Medkit, 23);
        medkit.repair_all = true;
        let mut repairkit = contract("largeRepairkit", EquipmentKind::Repairkit, 25);
        repairkit.repair_all = true;
        vec![extinguisher, medkit, repairkit]
    }

    fn profile() -> CriticalProfile {
        let devices = ALL_DEVICE_NAMES
            .into_iter()
            .map(|name| {
                (
                    name,
                    DeviceProfile {
                        max_hp: 100.0,
                        regen_hp: 50.0,
                    },
                )
            })
            .collect();
        CriticalProfile {
            devices,
            crew: vec![
                CrewMemberProfile {
                    name: CrewName::Commander,
                    roles: BTreeSet::from([CrewRole::Commander]),
                },
                CrewMemberProfile {
                    name: CrewName::Driver,
                    roles: BTreeSet::from([CrewRole::Driver]),
                },
            ],
            engine_fire_starting_chance: 0.2,
            repair_speed_factor: 1.0,
        }
    }

    fn critical_state(
        engine: Option<(f64, DeviceCondition)>,
        crew_ko: &[CrewName],
        on_fire: bool,
    ) -> CriticalState {
        let mut state = CriticalState::default();
        if let Some((hp, condition)) = engine {
            state
                .devices
                .insert(DeviceName::EngineHealth, DeviceState { hp, condition });
        }
        state.crew_ko.extend(crew_ko.iter().copied());
        state.on_fire = on_fire;
        state
    }

    fn context<'a>(
        now_seconds: f64,
        profile: &'a CriticalProfile,
        state: &'a CriticalState,
    ) -> PlayerEquipmentContext<'a> {
        PlayerEquipmentContext {
            now_seconds,
            combat_accepting: true,
            battle_result_committed: false,
            participating: true,
            alive: true,
            stunned: false,
            critical_profile: profile,
            critical_state: state,
        }
    }

    fn target(index: u64, name: EquipmentTarget) -> EquipmentActivationTarget {
        EquipmentActivationTarget { index, name }
    }

    fn activation_code(extra_index: u64, equipment_id: u64) -> u64 {
        (extra_index << 16) | equipment_id
    }

    fn intent(
        intent_seq: u64,
        equipment_id: u64,
        extra_index: u64,
        selected: Option<EquipmentTarget>,
        requested_active: Option<bool>,
    ) -> EquipmentIntent {
        EquipmentIntent {
            intent_seq,
            equipment_id,
            activation_code: activation_code(extra_index, equipment_id),
            selected,
            requested_active,
        }
    }

    fn damaged_engine() -> CriticalState {
        critical_state(Some((0.0, DeviceCondition::Destroyed)), &[], false)
    }

    #[test]
    fn bot_loadout_freezes_semantic_identity_order_and_policy_only() {
        let mut contracts = bot_contracts();
        contracts[0].id = 6_001;
        contracts[0].compact_descr = 61_001;
        contracts[1].id = 7_003;
        contracts[1].compact_descr = 67_003;
        contracts[2].id = 8_005;
        contracts[2].compact_descr = 68_005;
        let ledger = BotEquipmentLedger::new(16, 0.0, contracts.clone()).unwrap();
        assert_eq!(
            ledger
                .snapshot()
                .into_iter()
                .map(|state| state.equipment.id)
                .collect::<Vec<_>>(),
            vec![6_001, 7_003, 8_005]
        );

        contracts.swap(1, 2);
        assert!(matches!(
            BotEquipmentLedger::new(16, 0.0, contracts),
            Err(PlayerEquipmentError::InvalidBotLoadout)
        ));
        let mut wrong_policy = bot_contracts();
        wrong_policy[1].repair_all = false;
        assert!(matches!(
            BotEquipmentLedger::new(16, 0.0, wrong_policy),
            Err(PlayerEquipmentError::InvalidBotLoadout)
        ));
    }

    #[test]
    fn bot_policy_observes_first_then_applies_mounted_order_on_later_clock() {
        let profile = profile();
        let mut critical = critical_state(
            Some((0.0, DeviceCondition::Destroyed)),
            &[CrewName::Driver],
            true,
        );
        let mut ledger = BotEquipmentLedger::new(16, 0.0, bot_contracts()).unwrap();

        let first = ledger
            .advance_policy(0.0, &profile, &critical, None)
            .unwrap();
        assert_eq!(first.len(), 1);
        assert!(matches!(
            &first[0].application.effect,
            EquipmentEffect::ExtinguishFire
        ));
        critical = first[0]
            .application
            .critical_mutation
            .as_ref()
            .unwrap()
            .state()
            .clone();
        let observed = ledger.snapshot();
        assert_eq!(
            observed
                .iter()
                .map(|state| state.uses_left)
                .collect::<Vec<_>>(),
            vec![0, 1, 1]
        );
        assert_eq!(observed[1].ai_pending_elapsed, Some(0.0));
        assert_eq!(observed[2].ai_pending_elapsed, Some(0.0));

        assert!(ledger
            .advance_policy(0.0, &profile, &critical, None)
            .unwrap()
            .is_empty());
        let later = ledger
            .advance_policy(0.01, &profile, &critical, None)
            .unwrap();
        assert_eq!(later.len(), 2);
        assert!(matches!(
            &later[0].application.effect,
            EquipmentEffect::RestoreCrew { .. }
        ));
        assert!(matches!(
            &later[1].application.effect,
            EquipmentEffect::RepairDevices { .. }
        ));
        assert_eq!(
            ledger
                .snapshot()
                .iter()
                .map(|state| state.uses_left)
                .collect::<Vec<_>>(),
            vec![0, 0, 0]
        );
    }

    #[test]
    fn bot_medkit_carries_the_observed_stun_end_without_inventing_duration() {
        let profile = profile();
        let critical = CriticalState::default();
        let mut ledger = BotEquipmentLedger::new(16, 0.0, bot_contracts()).unwrap();

        assert!(ledger
            .advance_policy(1.0, &profile, &critical, Some(5_000))
            .unwrap()
            .is_empty());
        let applications = ledger
            .advance_policy(1.01, &profile, &critical, Some(5_000))
            .unwrap();
        assert_eq!(applications.len(), 1);
        assert_eq!(applications[0].stun_base_end_server_time_ms, Some(5_000));
        assert!(matches!(
            &applications[0].application.effect,
            EquipmentEffect::RestoreCrew {
                clear_stun: true,
                ..
            }
        ));
    }

    #[test]
    fn contract_json_uses_exact_current_main_fields() {
        let mut limiter = contract("removedRpmLimiter", EquipmentKind::RpmLimiter, 12);
        limiter.reuse_count = -1;
        limiter.cooldown_seconds = 7.0;
        limiter.engine_power_factor = 1.1;
        limiter.engine_hp_loss_per_second = 1.5;
        limiter.validate().unwrap();

        let encoded = serde_json::to_value(&limiter).unwrap();
        assert_eq!(
            encoded
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect::<BTreeSet<_>>(),
            BTreeSet::from([
                "autoReactionSeconds".to_owned(),
                "autoactivate".to_owned(),
                "bonusValue".to_owned(),
                "compactDescr".to_owned(),
                "cooldownSeconds".to_owned(),
                "crewLevelIncrease".to_owned(),
                "engineHpLossPerSecond".to_owned(),
                "enginePowerFactor".to_owned(),
                "fireStartingChanceFactor".to_owned(),
                "id".to_owned(),
                "kind".to_owned(),
                "name".to_owned(),
                "repairAll".to_owned(),
                "reuseCount".to_owned(),
                "tags".to_owned(),
                "turretRotationSpeedFactor".to_owned(),
            ])
        );
        assert_eq!(
            serde_json::from_value::<EquipmentContract>(encoded).unwrap(),
            limiter
        );
    }

    #[test]
    fn contract_and_loadout_validation_fail_closed() {
        let mut wrong_kind = contract("smallRepairkit", EquipmentKind::Repairkit, 41);
        wrong_kind.kind = EquipmentKind::Passive;
        assert_eq!(
            wrong_kind.validate(),
            Err(PlayerEquipmentError::InvalidContract)
        );

        let mut uppercase_tag = contract("generic", EquipmentKind::Medkit, 43);
        uppercase_tag.tags = vec!["MEDKIT".to_owned()];
        assert_eq!(
            uppercase_tag.validate(),
            Err(PlayerEquipmentError::InvalidContract)
        );

        let mut non_finite = contract("passive", EquipmentKind::Passive, 1);
        non_finite.cooldown_seconds = f64::NAN;
        assert_eq!(
            non_finite.validate(),
            Err(PlayerEquipmentError::InvalidContract)
        );

        let first = contract("smallRepairkit", EquipmentKind::Repairkit, 41);
        let mut duplicate = contract("smallMedkit", EquipmentKind::Medkit, 41);
        duplicate.compact_descr += 1;
        assert!(matches!(
            PlayerEquipmentLedger::new(1, 0.0, vec![first, duplicate], vec![]),
            Err(PlayerEquipmentError::InvalidLoadout)
        ));
        assert!(matches!(
            PlayerEquipmentLedger::new(
                1,
                0.0,
                vec![
                    contract("one", EquipmentKind::Passive, 1),
                    contract("two", EquipmentKind::Passive, 2),
                    contract("three", EquipmentKind::Passive, 3),
                    contract("four", EquipmentKind::Passive, 4),
                ],
                vec![],
            ),
            Err(PlayerEquipmentError::InvalidLoadout)
        ));
        assert!(matches!(
            PlayerEquipmentLedger::new(
                1,
                0.0,
                vec![],
                vec![
                    target(1, EquipmentTarget::Crew(CrewName::Driver)),
                    target(1, EquipmentTarget::Crew(CrewName::Commander)),
                ],
            ),
            Err(PlayerEquipmentError::InvalidActivationTargets)
        ));
    }

    #[test]
    fn effective_params_parser_reads_only_canonical_equipment_inputs() {
        let repair = contract("smallRepairkit", EquipmentKind::Repairkit, 41);
        let params = json!({
            "version": 1,
            "equipment": [repair],
            "critical": {
                "devices": [],
                "activation_targets": [
                    {"index": 4, "name": "engineHealth"}
                ]
            },
            "unrelated_validated_fields": {"stay": "outside this module"}
        });
        let ledger = PlayerEquipmentLedger::from_effective_params(7, 15.0, &params).unwrap();
        let snapshot = ledger.snapshot();
        assert_eq!(7, ledger.player_id());
        assert_eq!(1, snapshot.equipment_revision);
        assert_eq!(1, snapshot.equipment_states.len());
        assert_eq!(41, snapshot.equipment_states[0].equipment.id);

        let mut malformed = params;
        malformed["equipment"][0]["reuseCount"] = json!(true);
        assert!(matches!(
            PlayerEquipmentLedger::from_effective_params(7, 15.0, &malformed),
            Err(PlayerEquipmentError::InvalidProjection(_))
        ));
    }

    #[test]
    fn strict_wire_decoder_rejects_scope_shape_types_and_identity() {
        let message = json!({
            "type": "equipment_intent",
            "round_id": 9,
            "intent_seq": 1,
            "equipment_id": 41,
            "activation_code": activation_code(4, 41),
            "selected": "engineHealth",
            "requested_active": null
        });
        assert_eq!(
            decode_equipment_intent(&message, 9).unwrap().selected,
            Some(EquipmentTarget::Device(DeviceName::EngineHealth))
        );

        let mut wrong_round = message.clone();
        wrong_round["round_id"] = json!(10);
        assert_eq!(
            decode_equipment_intent(&wrong_round, 9),
            Err(PlayerEquipmentError::InvalidRound)
        );
        let mut unknown = message.clone();
        unknown["verdict"] = json!(true);
        assert!(matches!(
            decode_equipment_intent(&unknown, 9),
            Err(PlayerEquipmentError::InvalidWire(_))
        ));
        for missing in ["selected", "requested_active"] {
            let mut incomplete = message.clone();
            incomplete.as_object_mut().unwrap().remove(missing);
            assert!(matches!(
                decode_equipment_intent(&incomplete, 9),
                Err(PlayerEquipmentError::InvalidWire(_))
            ));
        }
        let mut coerced = message.clone();
        coerced["intent_seq"] = json!("1");
        assert!(matches!(
            decode_equipment_intent(&coerced, 9),
            Err(PlayerEquipmentError::InvalidWire(_))
        ));
        let mut boolean = message.clone();
        boolean["equipment_id"] = json!(true);
        assert!(matches!(
            decode_equipment_intent(&boolean, 9),
            Err(PlayerEquipmentError::InvalidWire(_))
        ));
        let mut bad_target = message.clone();
        bad_target["selected"] = json!("not-a-critical-slot");
        assert!(matches!(
            decode_equipment_intent(&bad_target, 9),
            Err(PlayerEquipmentError::InvalidWire(_))
        ));
        let mut bad_low_bits = message;
        bad_low_bits["activation_code"] = json!(activation_code(4, 42));
        assert_eq!(
            decode_equipment_intent(&bad_low_bits, 9),
            Err(PlayerEquipmentError::InvalidIntentIdentity)
        );
    }

    #[test]
    fn inventory_cooldown_sequence_and_exact_retry_are_canonical() {
        let mut repair = contract("smallRepairkit", EquipmentKind::Repairkit, 41);
        repair.reuse_count = 1;
        repair.cooldown_seconds = 5.0;
        let mut ledger = PlayerEquipmentLedger::new(
            1,
            0.0,
            vec![repair],
            vec![target(4, EquipmentTarget::Device(DeviceName::EngineHealth))],
        )
        .unwrap();
        let profile = profile();
        let damaged = damaged_engine();
        let first = intent(
            1,
            41,
            4,
            Some(EquipmentTarget::Device(DeviceName::EngineHealth)),
            None,
        );

        let admitted = ledger
            .admit_intent(first.clone(), context(0.0, &profile, &damaged))
            .unwrap();
        assert_eq!(EquipmentIntentDisposition::New, admitted.disposition);
        assert!(admitted.current_result.accepted);
        let repaired = admitted
            .application
            .as_ref()
            .unwrap()
            .critical_mutation
            .as_ref()
            .unwrap()
            .state();
        assert_eq!(
            DeviceCondition::Normal,
            repaired
                .devices
                .get(&DeviceName::EngineHealth)
                .unwrap()
                .condition
        );
        assert_eq!(1, ledger.snapshot().equipment_states[0].uses_left);
        assert_eq!(2, ledger.revision());

        let retry = ledger
            .admit_intent(first.clone(), context(0.0, &profile, &damaged))
            .unwrap();
        assert_eq!(EquipmentIntentDisposition::ExactRetry, retry.disposition);
        assert!(retry.application.is_none());
        assert_eq!(2, ledger.revision());

        let mut conflict = first;
        conflict.selected = Some(EquipmentTarget::Device(DeviceName::LeftTrackHealth));
        assert_eq!(
            ledger.admit_intent(conflict, context(0.0, &profile, &damaged)),
            Err(PlayerEquipmentError::IdentityConflict { sequence: 1 })
        );
        assert!(matches!(
            ledger.admit_intent(
                intent(
                    3,
                    41,
                    4,
                    Some(EquipmentTarget::Device(DeviceName::EngineHealth)),
                    None,
                ),
                context(0.0, &profile, &damaged),
            ),
            Err(PlayerEquipmentError::IntentSequenceGap {
                last: 1,
                received: 3
            })
        ));

        let early = ledger
            .admit_intent(
                intent(
                    2,
                    41,
                    4,
                    Some(EquipmentTarget::Device(DeviceName::EngineHealth)),
                    None,
                ),
                context(4.0, &profile, &damaged),
            )
            .unwrap();
        assert_eq!(
            Some(EquipmentIntentRejection::EquipmentIneligible),
            early.rejection
        );
        assert_eq!(1, ledger.snapshot().equipment_states[0].uses_left);

        let second_use = ledger
            .admit_intent(
                intent(
                    3,
                    41,
                    4,
                    Some(EquipmentTarget::Device(DeviceName::EngineHealth)),
                    None,
                ),
                context(5.0, &profile, &damaged),
            )
            .unwrap();
        assert!(second_use.current_result.accepted);
        assert_eq!(0, ledger.snapshot().equipment_states[0].uses_left);
        assert_eq!(3, ledger.revision());
    }

    #[test]
    fn terminal_rejections_consume_only_well_formed_contiguous_intents() {
        let medkit = contract("smallMedkit", EquipmentKind::Medkit, 43);
        let mut automatic = contract("autoExtinguishers", EquipmentKind::Extinguisher, 21);
        automatic.autoactivate = true;
        let mut limiter = contract("removedRpmLimiter", EquipmentKind::RpmLimiter, 12);
        limiter.engine_power_factor = 1.1;
        limiter.engine_hp_loss_per_second = 1.5;
        let mut ledger = PlayerEquipmentLedger::new(
            1,
            0.0,
            vec![medkit, automatic, limiter],
            vec![
                target(1, EquipmentTarget::Crew(CrewName::Driver)),
                target(2, EquipmentTarget::Crew(CrewName::Commander)),
                target(4, EquipmentTarget::Device(DeviceName::EngineHealth)),
            ],
        )
        .unwrap();
        let profile = profile();
        let fit = critical_state(None, &[], false);

        let cases = [
            (
                intent(1, 99, 0, None, None),
                EquipmentIntentRejection::EquipmentNotMounted,
            ),
            (
                intent(
                    2,
                    43,
                    1,
                    Some(EquipmentTarget::Crew(CrewName::Driver)),
                    Some(true),
                ),
                EquipmentIntentRejection::InvalidActivationMode,
            ),
            (
                intent(
                    3,
                    43,
                    4,
                    Some(EquipmentTarget::Device(DeviceName::EngineHealth)),
                    None,
                ),
                EquipmentIntentRejection::InvalidEquipmentTarget,
            ),
            (
                intent(
                    4,
                    43,
                    0,
                    Some(EquipmentTarget::Crew(CrewName::Driver)),
                    None,
                ),
                EquipmentIntentRejection::InvalidActivationCode,
            ),
            (
                intent(5, 21, 0, None, None),
                EquipmentIntentRejection::AutomaticOnly,
            ),
            (
                intent(6, 12, 0, None, Some(true)),
                EquipmentIntentRejection::InvalidActivationCode,
            ),
            (
                intent(
                    7,
                    43,
                    2,
                    Some(EquipmentTarget::Crew(CrewName::Commander)),
                    None,
                ),
                EquipmentIntentRejection::EquipmentIneligible,
            ),
        ];
        for (command, expected) in cases {
            let outcome = ledger
                .admit_intent(command, context(0.0, &profile, &fit))
                .unwrap();
            assert_eq!(Some(expected), outcome.rejection);
            assert!(!outcome.current_result.accepted);
            assert_eq!(expected.as_str(), outcome.current_result.reason);
        }

        let mut dead = context(0.0, &profile, &fit);
        dead.alive = false;
        let dead_result = ledger
            .admit_intent(
                intent(
                    8,
                    43,
                    1,
                    Some(EquipmentTarget::Crew(CrewName::Driver)),
                    None,
                ),
                dead,
            )
            .unwrap();
        assert_eq!(
            Some(EquipmentIntentRejection::VehicleNotAlive),
            dead_result.rejection
        );
        assert_eq!(8, ledger.intent_seq());
        assert_eq!(1, ledger.revision());
    }

    #[test]
    fn malformed_identity_never_consumes_the_sequence() {
        let profile = profile();
        let fit = CriticalState::default();
        let mut ledger = PlayerEquipmentLedger::new(1, 0.0, vec![], vec![]).unwrap();
        for malformed in [
            EquipmentIntent {
                intent_seq: 0,
                equipment_id: 41,
                activation_code: 41,
                selected: None,
                requested_active: None,
            },
            EquipmentIntent {
                intent_seq: 1,
                equipment_id: 0,
                activation_code: 1,
                selected: None,
                requested_active: None,
            },
            EquipmentIntent {
                intent_seq: 1,
                equipment_id: 41,
                activation_code: 42,
                selected: None,
                requested_active: None,
            },
        ] {
            assert!(ledger
                .admit_intent(malformed, context(0.0, &profile, &fit))
                .is_err());
            assert_eq!(0, ledger.intent_seq());
        }
    }

    #[test]
    fn repair_medkit_and_extinguisher_produce_typed_critical_mutations() {
        let repair = {
            let mut value = contract("largeRepairkit", EquipmentKind::Repairkit, 41);
            value.repair_all = true;
            value.bonus_value = 0.1;
            value
        };
        let medkit = {
            let mut value = contract("largeMedkit", EquipmentKind::Medkit, 43);
            value.repair_all = true;
            value.bonus_value = 0.3;
            value
        };
        let extinguisher = contract("manualExtinguisher", EquipmentKind::Extinguisher, 21);
        let mut ledger =
            PlayerEquipmentLedger::new(1, 0.0, vec![extinguisher, medkit, repair], vec![]).unwrap();
        let profile = profile();
        let initial = critical_state(
            Some((0.0, DeviceCondition::Destroyed)),
            &[CrewName::Driver],
            true,
        );

        let extinguish = ledger
            .admit_intent(
                intent(1, 21, 0, None, None),
                context(0.0, &profile, &initial),
            )
            .unwrap()
            .application
            .unwrap();
        assert_eq!(EquipmentEffect::ExtinguishFire, extinguish.effect);
        let after_fire = extinguish.critical_mutation.unwrap().state().clone();
        assert!(!after_fire.on_fire);

        let mut med_context = context(0.0, &profile, &after_fire);
        med_context.stunned = true;
        let heal = ledger
            .admit_intent(intent(2, 43, 1, None, None), med_context)
            .unwrap()
            .application
            .unwrap();
        assert!(matches!(
            heal.effect,
            EquipmentEffect::RestoreCrew {
                restore_all: true,
                clear_stun: true,
                ..
            }
        ));
        let after_crew = heal.critical_mutation.unwrap().state().clone();
        assert!(after_crew.crew_ko.is_empty());

        let repaired = ledger
            .admit_intent(
                intent(3, 41, 1, None, None),
                context(0.0, &profile, &after_crew),
            )
            .unwrap()
            .application
            .unwrap();
        assert!(matches!(
            repaired.effect,
            EquipmentEffect::RepairDevices {
                repair_all: true,
                bonus_value: 0.1,
                ..
            }
        ));
        let after_repair = repaired.critical_mutation.unwrap();
        assert_eq!(
            DeviceCondition::Normal,
            after_repair
                .state()
                .devices
                .get(&DeviceName::EngineHealth)
                .unwrap()
                .condition
        );
        assert_eq!(
            vec![0, 0, 0],
            ledger
                .snapshot()
                .equipment_states
                .iter()
                .map(|item| item.uses_left)
                .collect::<Vec<_>>()
        );
        assert_eq!(4, ledger.revision());
    }

    #[test]
    fn small_medkit_can_clear_stun_without_inventing_hull_healing() {
        let medkit = contract("smallMedkit", EquipmentKind::Medkit, 43);
        let mut ledger = PlayerEquipmentLedger::new(
            1,
            0.0,
            vec![medkit],
            vec![target(1, EquipmentTarget::Crew(CrewName::Driver))],
        )
        .unwrap();
        let profile = profile();
        let fit = CriticalState::default();
        let mut input = context(0.0, &profile, &fit);
        input.stunned = true;
        let outcome = ledger
            .admit_intent(
                intent(
                    1,
                    43,
                    1,
                    Some(EquipmentTarget::Crew(CrewName::Driver)),
                    None,
                ),
                input,
            )
            .unwrap();
        assert!(outcome.current_result.accepted);
        let application = outcome.application.unwrap();
        assert!(matches!(
            application.effect,
            EquipmentEffect::RestoreCrew {
                selected: Some(CrewName::Driver),
                clear_stun: true,
                ..
            }
        ));
        assert!(!application
            .critical_mutation
            .unwrap()
            .changes_internal_state());
        assert_eq!(0, ledger.snapshot().equipment_states[0].uses_left);
    }

    #[test]
    fn automatic_extinguisher_uses_server_clock_and_chains_state() {
        let mut first = contract("autoExtinguishers", EquipmentKind::Extinguisher, 21);
        first.autoactivate = true;
        first.cooldown_seconds = 90.0;
        first.auto_reaction_seconds = 1.0;
        let mut second = first.clone();
        second.id = 22;
        second.compact_descr = 11_022;
        let mut ledger = PlayerEquipmentLedger::new(1, 0.0, vec![first, second], vec![]).unwrap();
        let profile = profile();
        let burning = critical_state(None, &[], true);

        assert!(ledger
            .advance_automatic(10.0, &profile, &burning)
            .unwrap()
            .is_empty());
        assert_eq!(
            Some(0.0),
            ledger.snapshot().equipment_states[0].auto_pending_elapsed
        );
        assert!(ledger
            .advance_automatic(10.999, &profile, &burning)
            .unwrap()
            .is_empty());
        let applications = ledger.advance_automatic(11.0, &profile, &burning).unwrap();
        assert_eq!(1, applications.len());
        assert_eq!(21, applications[0].equipment_id);
        assert!(
            !applications[0]
                .critical_mutation
                .as_ref()
                .unwrap()
                .state()
                .on_fire
        );
        let snapshots = ledger.snapshot().equipment_states;
        assert_eq!(0, snapshots[0].uses_left);
        assert_eq!(1, snapshots[1].uses_left);
        assert_eq!(90.0, snapshots[0].cooldown_time_left);
        assert_eq!(2, ledger.revision());
    }

    #[test]
    fn zero_reaction_automatic_item_activates_on_first_observation() {
        let mut automatic = contract("autoExtinguishers", EquipmentKind::Extinguisher, 21);
        automatic.autoactivate = true;
        let mut ledger = PlayerEquipmentLedger::new(1, 0.0, vec![automatic], vec![]).unwrap();
        let profile = profile();
        let burning = critical_state(None, &[], true);

        assert_eq!(
            1,
            ledger
                .advance_automatic(10.0, &profile, &burning)
                .unwrap()
                .len()
        );
        assert_eq!(0, ledger.snapshot().equipment_states[0].uses_left);
    }

    #[test]
    fn rpm_limiter_toggle_passives_and_engine_drain_are_server_owned() {
        let mut limiter = contract("removedRpmLimiter", EquipmentKind::RpmLimiter, 12);
        limiter.reuse_count = -1;
        limiter.engine_power_factor = 1.1;
        limiter.engine_hp_loss_per_second = 1.5;
        let mut ledger = PlayerEquipmentLedger::new(1, 0.0, vec![limiter], vec![]).unwrap();
        let profile = profile();
        let engine = critical_state(Some((100.0, DeviceCondition::Normal)), &[], false);

        let enabled = ledger
            .admit_intent(
                intent(1, 12, 1, None, Some(true)),
                context(0.0, &profile, &engine),
            )
            .unwrap();
        assert!(matches!(
            enabled.application.unwrap().effect,
            EquipmentEffect::SetRpmLimiter { active: true, .. }
        ));
        let passives = ledger.passive_effects();
        assert_eq!(1.1, passives.engine_power_factor);
        assert_eq!(1.5, passives.engine_hp_loss_per_second);
        let damage = ledger
            .propose_engine_damage(&profile, &engine, 1_000, 2.0)
            .unwrap()
            .unwrap();
        assert_eq!(
            97.0,
            damage
                .state()
                .devices
                .get(&DeviceName::EngineHealth)
                .unwrap()
                .hp
        );
        assert_eq!(-1, ledger.snapshot().equipment_states[0].uses_left);

        let disabled = ledger
            .admit_intent(
                intent(2, 12, 0, None, Some(false)),
                context(0.0, &profile, &engine),
            )
            .unwrap();
        assert!(disabled.current_result.accepted);
        assert_eq!(1.0, ledger.passive_effects().engine_power_factor);
        assert_eq!(
            None,
            ledger
                .propose_engine_damage(&profile, &engine, 1_000, 2.0)
                .unwrap()
        );
        let duplicate_off = ledger
            .admit_intent(
                intent(3, 12, 0, None, Some(false)),
                context(0.0, &profile, &engine),
            )
            .unwrap();
        assert_eq!(
            Some(EquipmentIntentRejection::EquipmentIneligible),
            duplicate_off.rejection
        );
    }

    #[test]
    fn passive_factors_keep_distinct_current_main_meanings() {
        let mut extinguisher = contract("manualExtinguisher", EquipmentKind::Extinguisher, 21);
        extinguisher.fire_starting_chance_factor = 0.9;
        let mut food = contract("chocolate", EquipmentKind::Stimulator, 9);
        food.crew_level_increase = 10.0;
        let mut fuel = contract("gasoline105", EquipmentKind::Fuel, 8);
        fuel.engine_power_factor = 1.1;
        fuel.turret_rotation_speed_factor = 1.1;
        let ledger =
            PlayerEquipmentLedger::new(1, 0.0, vec![extinguisher, food, fuel], vec![]).unwrap();

        let effects = ledger.passive_effects();
        assert_eq!(0.9, effects.fire_starting_chance_factor);
        assert_eq!(10.0, effects.crew_level_increase);
        assert_eq!(1.1, effects.engine_power_factor);
        assert_eq!(1.1, effects.turret_rotation_speed_factor);

        let mut repair = contract("largeRepairkit", EquipmentKind::Repairkit, 41);
        repair.bonus_value = 0.1;
        let mut medkit = contract("largeMedkit", EquipmentKind::Medkit, 43);
        medkit.bonus_value = 0.3;
        let bonuses = PlayerEquipmentLedger::new(2, 0.0, vec![repair, medkit], vec![])
            .unwrap()
            .passive_effects();
        assert_eq!(0.1, bonuses.repairkit_bonus_value);
        assert_eq!(0.3, bonuses.medkit_bonus_value);
    }

    #[test]
    fn snapshot_has_exact_relative_cooldown_and_result_shape() {
        let mut repair = contract("smallRepairkit", EquipmentKind::Repairkit, 41);
        repair.reuse_count = 1;
        repair.cooldown_seconds = 10.0;
        let mut ledger = PlayerEquipmentLedger::new(
            1,
            0.0,
            vec![repair],
            vec![target(4, EquipmentTarget::Device(DeviceName::EngineHealth))],
        )
        .unwrap();
        let profile = profile();
        let damaged = damaged_engine();
        ledger
            .admit_intent(
                intent(
                    1,
                    41,
                    4,
                    Some(EquipmentTarget::Device(DeviceName::EngineHealth)),
                    None,
                ),
                context(0.0, &profile, &damaged),
            )
            .unwrap();
        ledger.advance_clock(3.0).unwrap();
        let snapshot = ledger.snapshot();
        assert_eq!(7.0, snapshot.equipment_states[0].cooldown_time_left);
        assert_eq!(1, snapshot.equipment_intent_seq);
        assert_eq!(
            EquipmentIntentResult {
                intent_seq: 1,
                accepted: true,
                reason: String::new(),
            },
            snapshot.equipment_intent_result
        );
        let wire = serde_json::to_value(snapshot).unwrap();
        assert_eq!(
            BTreeSet::from([
                "active".to_owned(),
                "aiPendingElapsed".to_owned(),
                "autoPendingElapsed".to_owned(),
                "cooldownTimeLeft".to_owned(),
                "equipment".to_owned(),
                "usesLeft".to_owned(),
            ]),
            wire["equipment_states"][0]
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect()
        );
    }

    #[test]
    fn bounded_retry_window_and_clock_failures_are_transactional() {
        let profile = profile();
        let fit = CriticalState::default();
        let mut ledger = PlayerEquipmentLedger::new(1, 0.0, vec![], vec![]).unwrap();
        for sequence in 1..=65 {
            let outcome = ledger
                .admit_intent(
                    intent(sequence, 1, 0, None, None),
                    context(0.0, &profile, &fit),
                )
                .unwrap();
            assert_eq!(
                Some(EquipmentIntentRejection::EquipmentNotMounted),
                outcome.rejection
            );
        }
        assert_eq!(
            ledger.admit_intent(intent(1, 1, 0, None, None), context(0.0, &profile, &fit),),
            Err(PlayerEquipmentError::StaleIntentRetry { sequence: 1 })
        );

        ledger.advance_clock(10.0).unwrap();
        assert_eq!(
            ledger.admit_intent(intent(66, 1, 0, None, None), context(9.0, &profile, &fit),),
            Err(PlayerEquipmentError::InvalidClock)
        );
        assert_eq!(65, ledger.intent_seq());
        let retry = ledger
            .admit_intent(intent(65, 1, 0, None, None), context(9.0, &profile, &fit))
            .unwrap();
        assert_eq!(EquipmentIntentDisposition::ExactRetry, retry.disposition);
        assert_eq!(10.0, ledger.clock_seconds());
    }

    #[test]
    fn exact_retry_of_older_intent_keeps_the_latest_terminal_snapshot() {
        let profile = profile();
        let fit = CriticalState::default();
        let mut ledger = PlayerEquipmentLedger::new(1, 0.0, vec![], vec![]).unwrap();
        ledger
            .admit_intent(intent(1, 41, 0, None, None), context(0.0, &profile, &fit))
            .unwrap();
        ledger
            .admit_intent(intent(2, 42, 0, None, None), context(0.0, &profile, &fit))
            .unwrap();

        let retry = ledger
            .admit_intent(intent(1, 41, 0, None, None), context(0.0, &profile, &fit))
            .unwrap();
        assert_eq!(EquipmentIntentDisposition::ExactRetry, retry.disposition);
        assert_eq!(2, retry.current_result.intent_seq);
        assert_eq!(2, ledger.snapshot().equipment_intent_result.intent_seq);
    }
}
