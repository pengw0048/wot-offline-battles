use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

pub const MAX_COMBAT_ID: u64 = 2_147_483_647;
pub const FIRE_INTENT_LIFETIME_MS: u64 = 5_000;
pub const FIRE_INTENT_HISTORY: usize = 64;
pub const MAX_PENDING_FIRE_INTENTS: usize = 1;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum VehicleKind {
    Player,
    Bot,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub struct VehicleKey {
    pub kind: VehicleKind,
    pub id: u64,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
pub struct BodyPose {
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
    pub pitch: f64,
    pub roll: f64,
    pub speed: f64,
    pub aim_yaw: f64,
    pub gun_pitch: f64,
}

impl BodyPose {
    fn finite(self) -> bool {
        [
            self.x,
            self.y,
            self.z,
            self.yaw,
            self.pitch,
            self.roll,
            self.speed,
            self.aim_yaw,
            self.gun_pitch,
        ]
        .into_iter()
        .all(f64::is_finite)
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FireIntentRequest {
    pub intent_seq: u64,
    pub input_seq: u64,
    pub shell_index: u8,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FireContext {
    pub player_id: u64,
    pub current_input_seq: u64,
    pub current_shell_index: u8,
    pub last_fire_seq: u64,
    pub pose_time_us: Option<u64>,
    pub pose: BodyPose,
    pub server_time_ms: u64,
    pub alive: bool,
    pub combat_accepting: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct FireIntentBinding {
    pub player_id: u64,
    pub intent_seq: u64,
    pub shot_seq: u64,
    pub input_seq: u64,
    pub pose_time_us: u64,
    pub shell_index: u8,
    pub pose: BodyPose,
    pub deadline_server_time_ms: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FireIntentTerminal {
    Launched { projectile_id: String },
    Rejected { reason: String },
}

#[derive(Clone, Debug, PartialEq)]
pub enum FireIntentAdmission {
    New(FireIntentBinding),
    ExactRetry,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum FireIntentError {
    #[error("fire intent fields are outside protocol bounds")]
    InvalidShape,
    #[error("player cannot fire in the current state")]
    NotAccepting,
    #[error("intent sequence {received} does not follow {last}")]
    SequenceGap { last: u64, received: u64 },
    #[error("intent sequence {sequence} was reused with different content")]
    ConflictingRetry { sequence: u64 },
    #[error("fire intent is not bound to the current input and shell")]
    InputBinding,
    #[error("the player has no valid timed pose")]
    MissingPose,
    #[error("the player already has a pending fire intent")]
    PendingLimit,
    #[error("unknown fire intent {sequence}")]
    Unknown { sequence: u64 },
    #[error("fire intent {sequence} already has a conflicting terminal result")]
    TerminalConflict { sequence: u64 },
    #[error("projectile launch does not match its frozen fire intent")]
    LaunchBinding,
}

#[derive(Clone, Debug, Default)]
pub struct FireIntentLedger {
    last_intent_seq: u64,
    fingerprints: BTreeMap<u64, String>,
    pending: BTreeMap<u64, FireIntentBinding>,
    terminal: BTreeMap<u64, FireIntentTerminal>,
}

impl FireIntentLedger {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn last_intent_seq(&self) -> u64 {
        self.last_intent_seq
    }

    pub fn pending(&self, sequence: u64) -> Option<&FireIntentBinding> {
        self.pending.get(&sequence)
    }

    pub fn has_pending(&self) -> bool {
        !self.pending.is_empty()
    }

    pub fn terminal(&self, sequence: u64) -> Option<&FireIntentTerminal> {
        self.terminal.get(&sequence)
    }

    pub fn submit(
        &mut self,
        request: FireIntentRequest,
        context: FireContext,
    ) -> Result<FireIntentAdmission, FireIntentError> {
        if request.intent_seq == 0
            || request.intent_seq > MAX_COMBAT_ID
            || request.input_seq == 0
            || request.input_seq > MAX_COMBAT_ID
            || request.shell_index > 9
            || context.player_id == 0
            || context.player_id > MAX_COMBAT_ID
            || context.last_fire_seq >= MAX_COMBAT_ID
            || !context.pose.finite()
        {
            return Err(FireIntentError::InvalidShape);
        }
        let fingerprint =
            serde_json::to_string(&request).expect("serializing a typed fire intent cannot fail");
        if let Some(previous) = self.fingerprints.get(&request.intent_seq) {
            return if previous == &fingerprint {
                Ok(FireIntentAdmission::ExactRetry)
            } else {
                Err(FireIntentError::ConflictingRetry {
                    sequence: request.intent_seq,
                })
            };
        }
        if request.intent_seq != self.last_intent_seq.saturating_add(1) {
            return Err(FireIntentError::SequenceGap {
                last: self.last_intent_seq,
                received: request.intent_seq,
            });
        }
        if !context.alive || !context.combat_accepting {
            return Err(FireIntentError::NotAccepting);
        }
        if request.input_seq != context.current_input_seq
            || request.shell_index != context.current_shell_index
        {
            return Err(FireIntentError::InputBinding);
        }
        let pose_time_us = context.pose_time_us.ok_or(FireIntentError::MissingPose)?;
        if self.pending.len() >= MAX_PENDING_FIRE_INTENTS {
            return Err(FireIntentError::PendingLimit);
        }

        let binding = FireIntentBinding {
            player_id: context.player_id,
            intent_seq: request.intent_seq,
            shot_seq: context.last_fire_seq + 1,
            input_seq: request.input_seq,
            pose_time_us,
            shell_index: request.shell_index,
            pose: context.pose,
            deadline_server_time_ms: context
                .server_time_ms
                .saturating_add(FIRE_INTENT_LIFETIME_MS),
        };
        self.last_intent_seq = request.intent_seq;
        self.fingerprints.insert(request.intent_seq, fingerprint);
        self.pending.insert(request.intent_seq, binding.clone());
        trim_oldest(&mut self.fingerprints, FIRE_INTENT_HISTORY);
        Ok(FireIntentAdmission::New(binding))
    }

    pub fn reject(&mut self, sequence: u64, reason: &str) -> Result<(), FireIntentError> {
        let terminal = FireIntentTerminal::Rejected {
            reason: bounded_reason(reason),
        };
        if let Some(previous) = self.terminal.get(&sequence) {
            return if previous == &terminal {
                Ok(())
            } else {
                Err(FireIntentError::TerminalConflict { sequence })
            };
        }
        if self.pending.remove(&sequence).is_none() {
            return Err(FireIntentError::Unknown { sequence });
        }
        self.terminal.insert(sequence, terminal);
        trim_oldest(&mut self.terminal, FIRE_INTENT_HISTORY);
        Ok(())
    }

    pub fn commit_launch(
        &mut self,
        sequence: u64,
        shot_seq: u64,
        input_seq: u64,
        shell_index: u8,
        origin: [f64; 3],
        _server_time_ms: u64,
        projectile_id: &str,
    ) -> Result<(), FireIntentError> {
        let terminal = FireIntentTerminal::Launched {
            projectile_id: projectile_id.to_owned(),
        };
        if let Some(previous) = self.terminal.get(&sequence) {
            return if previous == &terminal {
                Ok(())
            } else {
                Err(FireIntentError::TerminalConflict { sequence })
            };
        }
        let binding = self
            .pending
            .get(&sequence)
            .ok_or(FireIntentError::Unknown { sequence })?;
        let distance = ((origin[0] - binding.pose.x).powi(2)
            + (origin[1] - binding.pose.y).powi(2)
            + (origin[2] - binding.pose.z).powi(2))
        .sqrt();
        if origin.into_iter().any(|value| !value.is_finite())
            || shot_seq != binding.shot_seq
            || input_seq != binding.input_seq
            || shell_index != binding.shell_index
            || distance > 25.0
            || projectile_id.is_empty()
            || projectile_id.len() > 96
        {
            return Err(FireIntentError::LaunchBinding);
        }

        self.pending.remove(&sequence);
        self.terminal.insert(sequence, terminal);
        trim_oldest(&mut self.terminal, FIRE_INTENT_HISTORY);
        Ok(())
    }
}

fn bounded_reason(reason: &str) -> String {
    let reason = if reason.is_empty() {
        "rejected"
    } else {
        reason
    };
    reason.chars().take(64).collect()
}

fn trim_oldest<T>(values: &mut BTreeMap<u64, T>, limit: usize) {
    while values.len() > limit {
        if let Some(oldest) = values.keys().next().copied() {
            values.remove(&oldest);
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VehicleCombatState {
    pub team: u8,
    pub health: u32,
    pub max_health: u32,
    pub alive: bool,
    pub frags: i32,
    pub team_killer: bool,
    pub death_attacker: Option<VehicleKey>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DamageSource {
    Shot,
    Fire,
    Ram,
    Environment,
    PlayerLeft,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DamageProposal {
    pub attacker: Option<VehicleKey>,
    pub target: VehicleKey,
    pub amount: u32,
    pub source: DamageSource,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DamageCommit {
    pub attacker: Option<VehicleKey>,
    pub target: VehicleKey,
    pub requested: u32,
    pub applied: u32,
    pub health: u32,
    pub dead: bool,
    pub source: DamageSource,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum DamageError {
    #[error("unknown target {target:?}")]
    UnknownTarget { target: VehicleKey },
    #[error("damage batch contains target {target:?} more than once")]
    DuplicateTarget { target: VehicleKey },
    #[error("damage {amount} exceeds the per-effect limit")]
    DamageLimit { amount: u32 },
    #[error("only a player vehicle may be retired from a round")]
    InvalidRetirement,
}

#[derive(Clone, Debug, Default)]
pub struct CombatLedger {
    vehicles: BTreeMap<VehicleKey, VehicleCombatState>,
}

impl CombatLedger {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&mut self, key: VehicleKey, state: VehicleCombatState) {
        self.vehicles.insert(key, state);
    }

    pub fn get(&self, key: VehicleKey) -> Option<&VehicleCombatState> {
        self.vehicles.get(&key)
    }

    pub fn retire_player(&mut self, key: VehicleKey) -> Result<DamageCommit, DamageError> {
        if key.kind != VehicleKind::Player {
            return Err(DamageError::InvalidRetirement);
        }
        let target = self
            .vehicles
            .get_mut(&key)
            .ok_or(DamageError::UnknownTarget { target: key })?;
        let requested = target.health;
        let applied = if target.alive { target.health } else { 0 };
        target.health = 0;
        target.alive = false;
        target.death_attacker = None;
        Ok(DamageCommit {
            attacker: None,
            target: key,
            requested,
            applied,
            health: 0,
            dead: true,
            source: DamageSource::PlayerLeft,
        })
    }

    /// Mark a living vehicle as defeated by complete crew loss while keeping
    /// its remaining hull HP. Returns whether this call created the death.
    pub fn knock_out_crew(
        &mut self,
        key: VehicleKey,
        attacker: Option<VehicleKey>,
    ) -> Result<bool, DamageError> {
        let target = self
            .vehicles
            .get_mut(&key)
            .ok_or(DamageError::UnknownTarget { target: key })?;
        if !target.alive {
            return Ok(false);
        }
        let target_team = target.team;
        target.alive = false;
        target.death_attacker = attacker;
        if let Some(attacker) = attacker.filter(|attacker| *attacker != key) {
            if let Some(attacker_state) = self.vehicles.get_mut(&attacker) {
                if attacker_state.team == target_team {
                    attacker_state.frags = attacker_state.frags.saturating_sub(1);
                    if attacker.kind == VehicleKind::Player {
                        attacker_state.team_killer = true;
                    }
                } else {
                    attacker_state.frags = attacker_state.frags.saturating_add(1);
                }
            }
        }
        Ok(true)
    }

    /// Validates the entire effect batch before committing any HP change.
    pub fn apply_atomic(
        &mut self,
        proposals: &[DamageProposal],
    ) -> Result<Vec<DamageCommit>, DamageError> {
        let mut targets = BTreeMap::new();
        for proposal in proposals {
            if proposal.amount > 5_000 && proposal.source != DamageSource::Ram {
                return Err(DamageError::DamageLimit {
                    amount: proposal.amount,
                });
            }
            if !self.vehicles.contains_key(&proposal.target) {
                return Err(DamageError::UnknownTarget {
                    target: proposal.target,
                });
            }
            if targets.insert(proposal.target, ()).is_some() {
                return Err(DamageError::DuplicateTarget {
                    target: proposal.target,
                });
            }
        }

        let mut commits = Vec::with_capacity(proposals.len());
        for proposal in proposals {
            let target = self
                .vehicles
                .get_mut(&proposal.target)
                .expect("targets were validated above");
            let was_alive = target.alive;
            let target_team = target.team;
            let applied = if was_alive {
                proposal.amount.min(target.health)
            } else {
                0
            };
            target.health -= applied;
            target.alive = target.health > 0;
            let died = was_alive && !target.alive;
            if died {
                target.death_attacker = proposal.attacker;
            }
            commits.push(DamageCommit {
                attacker: proposal.attacker,
                target: proposal.target,
                requested: proposal.amount,
                applied,
                health: target.health,
                dead: !target.alive,
                source: proposal.source,
            });
            if died {
                if let Some(attacker) = proposal.attacker {
                    if attacker != proposal.target {
                        if let Some(attacker_state) = self.vehicles.get_mut(&attacker) {
                            if attacker_state.team == target_team {
                                attacker_state.frags = attacker_state.frags.saturating_sub(1);
                                if attacker.kind == VehicleKind::Player {
                                    attacker_state.team_killer = true;
                                }
                            } else {
                                attacker_state.frags = attacker_state.frags.saturating_add(1);
                            }
                        }
                    }
                }
            }
        }
        Ok(commits)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn player(id: u64) -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Player,
            id,
        }
    }

    fn context() -> FireContext {
        FireContext {
            player_id: 1,
            current_input_seq: 4,
            current_shell_index: 2,
            last_fire_seq: 7,
            pose_time_us: Some(500_000),
            pose: BodyPose {
                x: 10.0,
                y: 2.0,
                z: 20.0,
                yaw: 0.0,
                pitch: 0.0,
                roll: 0.0,
                speed: 0.0,
                aim_yaw: 0.0,
                gun_pitch: 0.0,
            },
            server_time_ms: 1_000,
            alive: true,
            combat_accepting: true,
        }
    }

    #[test]
    fn fire_intent_freezes_input_pose_and_deadline() {
        let mut ledger = FireIntentLedger::new();
        let request = FireIntentRequest {
            intent_seq: 1,
            input_seq: 4,
            shell_index: 2,
        };
        let FireIntentAdmission::New(binding) = ledger.submit(request, context()).unwrap() else {
            panic!("expected a new binding");
        };
        assert_eq!(binding.shot_seq, 8);
        assert_eq!(binding.pose_time_us, 500_000);
        assert_eq!(binding.deadline_server_time_ms, 6_000);
        assert_eq!(
            ledger.submit(request, context()),
            Ok(FireIntentAdmission::ExactRetry)
        );
    }

    #[test]
    fn fire_intent_requires_current_input_and_one_pending_operation() {
        let mut ledger = FireIntentLedger::new();
        let mut wrong = context();
        wrong.current_input_seq = 3;
        assert_eq!(
            ledger.submit(
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 4,
                    shell_index: 2,
                },
                wrong,
            ),
            Err(FireIntentError::InputBinding)
        );
        ledger
            .submit(
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 4,
                    shell_index: 2,
                },
                context(),
            )
            .unwrap();
        assert_eq!(
            ledger.submit(
                FireIntentRequest {
                    intent_seq: 2,
                    input_seq: 4,
                    shell_index: 2,
                },
                context(),
            ),
            Err(FireIntentError::PendingLimit)
        );
    }

    #[test]
    fn launch_is_bound_and_terminal_retries_are_idempotent() {
        let mut ledger = FireIntentLedger::new();
        ledger
            .submit(
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 4,
                    shell_index: 2,
                },
                context(),
            )
            .unwrap();
        ledger
            .commit_launch(1, 8, 4, 2, [10.0, 2.0, 20.0], 2_000, "1:p:1:8")
            .unwrap();
        ledger
            .commit_launch(1, 8, 4, 2, [10.0, 2.0, 20.0], 2_000, "1:p:1:8")
            .unwrap();
        assert!(matches!(
            ledger.terminal(1),
            Some(FireIntentTerminal::Launched { .. })
        ));
        assert_eq!(
            ledger.reject(1, "late"),
            Err(FireIntentError::TerminalConflict { sequence: 1 })
        );
    }

    #[test]
    fn admitted_fire_intent_survives_a_worker_stall_beyond_five_seconds() {
        let mut ledger = FireIntentLedger::new();
        ledger
            .submit(
                FireIntentRequest {
                    intent_seq: 1,
                    input_seq: 4,
                    shell_index: 2,
                },
                context(),
            )
            .unwrap();

        ledger
            .commit_launch(1, 8, 4, 2, [10.0, 2.0, 20.0], 7_500, "1:p:1:8")
            .unwrap();
        assert!(matches!(
            ledger.terminal(1),
            Some(FireIntentTerminal::Launched { .. })
        ));
    }

    #[test]
    fn atomic_damage_rejects_the_whole_batch_before_mutation() {
        let mut ledger = CombatLedger::new();
        ledger.insert(
            player(1),
            VehicleCombatState {
                team: 1,
                health: 100,
                max_health: 100,
                alive: true,
                frags: 0,
                team_killer: false,
                death_attacker: None,
            },
        );
        let error = ledger
            .apply_atomic(&[
                DamageProposal {
                    attacker: None,
                    target: player(1),
                    amount: 10,
                    source: DamageSource::Ram,
                },
                DamageProposal {
                    attacker: None,
                    target: player(99),
                    amount: 10,
                    source: DamageSource::Ram,
                },
            ])
            .unwrap_err();
        assert_eq!(error, DamageError::UnknownTarget { target: player(99) });
        assert_eq!(ledger.get(player(1)).unwrap().health, 100);
    }

    #[test]
    fn atomic_ram_can_kill_both_sides_and_attribute_both_frags() {
        let mut ledger = CombatLedger::new();
        for id in [1, 2] {
            ledger.insert(
                player(id),
                VehicleCombatState {
                    team: id as u8,
                    health: 50,
                    max_health: 50,
                    alive: true,
                    frags: 0,
                    team_killer: false,
                    death_attacker: None,
                },
            );
        }
        let commits = ledger
            .apply_atomic(&[
                DamageProposal {
                    attacker: Some(player(2)),
                    target: player(1),
                    amount: 50,
                    source: DamageSource::Ram,
                },
                DamageProposal {
                    attacker: Some(player(1)),
                    target: player(2),
                    amount: 50,
                    source: DamageSource::Ram,
                },
            ])
            .unwrap();
        assert!(commits.iter().all(|commit| commit.dead));
        assert_eq!(ledger.get(player(1)).unwrap().frags, 1);
        assert_eq!(ledger.get(player(2)).unwrap().frags, 1);
    }

    #[test]
    fn allied_frag_is_negative_and_marks_only_a_human_team_killer() {
        let mut ledger = CombatLedger::new();
        for id in [1, 2] {
            ledger.insert(
                player(id),
                VehicleCombatState {
                    team: 1,
                    health: 50,
                    max_health: 50,
                    alive: true,
                    frags: 0,
                    team_killer: false,
                    death_attacker: None,
                },
            );
        }
        ledger
            .apply_atomic(&[DamageProposal {
                attacker: Some(player(1)),
                target: player(2),
                amount: 50,
                source: DamageSource::Shot,
            }])
            .unwrap();
        assert_eq!(ledger.get(player(1)).unwrap().frags, -1);
        assert!(ledger.get(player(1)).unwrap().team_killer);
    }
}
