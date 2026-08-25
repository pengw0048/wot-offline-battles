//! Protocol-v5 public result and durable receipt construction.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};
use thiserror::Error;

use crate::combat::{VehicleKey, VehicleKind};
use crate::rewards::{compute_offline_rewards, RewardStatistics};
use crate::room::{BattleResult, BattleWinner, RoundParticipant, Team};
use crate::statistics::{InteractionStatistics, StatisticsLedger, VehicleStatistics};
use crate::wire::LAN_PROTOCOL_VERSION;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResultActor {
    pub key: VehicleKey,
    pub name: String,
    pub vehicle: String,
    pub team: u8,
    pub health: u32,
    pub alive: bool,
    pub death_reason: i32,
    pub killer: Option<VehicleKey>,
    pub team_killer: bool,
    pub vehicle_tier: u8,
}

#[derive(Clone, Debug)]
pub struct ReceiptBuildContext<'a> {
    pub receipt_namespace: &'a str,
    pub arena_unique_id: u64,
    pub round_id: u64,
    pub map: &'a str,
    pub duration_ticks: u64,
    pub result: &'a BattleResult,
    pub participants: &'a [RoundParticipant],
    pub actors: &'a [ResultActor],
    pub active_player_ids: &'a BTreeSet<u32>,
    pub statistics: &'a StatisticsLedger,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ResultBuildError {
    #[error("result actor roster must contain between one and thirty unique actors")]
    InvalidActorRoster,
    #[error("round participant {0} has no matching public result actor")]
    MissingParticipant(u32),
    #[error("result identity text is invalid")]
    InvalidText,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct RemainingBotState {
    pub team: Team,
    pub health: u32,
    pub max_health: u32,
    pub alive: bool,
}

/// Adjudicate an abandoned round from the last canonical Bot ledger.
///
/// This preserves the former LAN server law: surviving vehicle count wins
/// first, followed by remaining-health ratio and absolute health. An exact
/// tie is broken by round parity so early departures do not manufacture a
/// draw and block the single-room server indefinitely.
pub(crate) fn remaining_bot_winner(
    round_id: u64,
    states: impl IntoIterator<Item = RemainingBotState>,
) -> BattleWinner {
    #[derive(Clone, Copy, Default)]
    struct Total {
        alive: u64,
        health: u64,
        maximum: u64,
    }

    let mut totals = [Total::default(), Total::default()];
    for state in states {
        let total = &mut totals[usize::from(state.team.number() - 1)];
        let maximum = u64::from(state.max_health.max(1));
        let health = u64::from(state.health).min(maximum);
        if state.alive && health > 0 {
            total.alive += 1;
            total.health += health;
        }
        total.maximum += maximum;
    }

    let winner = if totals[0].alive != totals[1].alive {
        if totals[0].alive > totals[1].alive {
            Team::One
        } else {
            Team::Two
        }
    } else {
        let first_ratio = u128::from(totals[0].health) * u128::from(totals[1].maximum);
        let second_ratio = u128::from(totals[1].health) * u128::from(totals[0].maximum);
        if first_ratio != second_ratio {
            if first_ratio > second_ratio {
                Team::One
            } else {
                Team::Two
            }
        } else if totals[0].health != totals[1].health {
            if totals[0].health > totals[1].health {
                Team::One
            } else {
                Team::Two
            }
        } else if round_id % 2 == 0 {
            Team::One
        } else {
            Team::Two
        }
    };
    BattleWinner::Team(winner)
}

pub fn build_receipt_payloads(
    context: ReceiptBuildContext<'_>,
) -> Result<BTreeMap<u32, Value>, ResultBuildError> {
    validate_text(context.receipt_namespace, 64)?;
    validate_text(context.map, 96)?;
    if context.round_id == 0 || !(1..=30).contains(&context.actors.len()) {
        return Err(ResultBuildError::InvalidActorRoster);
    }
    let mut identities = BTreeSet::new();
    if context.actors.iter().any(|actor| {
        actor.key.id == 0
            || actor.key.id > u64::from(u32::MAX)
            || !matches!(actor.team, 1 | 2)
            || !identities.insert(actor.key)
            || validate_text(&actor.name, 32).is_err()
            || validate_text(&actor.vehicle, 96).is_err()
    }) {
        return Err(ResultBuildError::InvalidActorRoster);
    }

    let winner = context.result.winner.number();
    let mut public_results = Vec::with_capacity(context.actors.len());
    for actor in context.actors {
        let statistics = statistics_value(context.statistics.row(actor.key));
        let reward_statistics = reward_statistics(context.statistics.row(actor.key));
        let rewards = compute_offline_rewards(
            reward_statistics,
            winner == actor.team,
            true,
            actor.vehicle_tier,
        );
        public_results.push(json!({
            "actor_kind": kind_name(actor.key.kind),
            "actor_id": actor.key.id,
            "name": actor.name,
            "vehicle": actor.vehicle,
            "team": actor.team,
            "health": actor.health,
            "death_reason": if actor.alive { -1 } else { actor.death_reason.max(0) },
            "killer_kind": actor.killer.map(|key| kind_name(key.kind)).unwrap_or(""),
            "killer_id": actor.killer.map(|key| key.id).unwrap_or(0),
            "is_team_killer": actor.team_killer,
            "xp": rewards.xp,
            "stats": statistics,
        }));
    }

    let by_player: BTreeMap<_, _> = context
        .actors
        .iter()
        .filter(|actor| actor.key.kind == VehicleKind::Player)
        .map(|actor| (actor.key.id as u32, actor))
        .collect();
    let mut payloads = BTreeMap::new();
    for participant in context.participants {
        let actor = by_player
            .get(&participant.player_id)
            .copied()
            .ok_or(ResultBuildError::MissingParticipant(participant.player_id))?;
        let stats = statistics_value(context.statistics.row(actor.key));
        let rewards = compute_offline_rewards(
            reward_statistics(context.statistics.row(actor.key)),
            winner == actor.team,
            true,
            actor.vehicle_tier,
        );
        let receipt_id = format!(
            "{}:{}:{}",
            context.receipt_namespace, context.round_id, participant.player_id
        );
        payloads.insert(
            participant.player_id,
            json!({
                "type": "battle_receipt",
                "protocol": LAN_PROTOCOL_VERSION,
                "receipt_id": receipt_id,
                "arena_unique_id": context.arena_unique_id,
                "round_id": context.round_id,
                "player_id": participant.player_id,
                "account_key": participant.account_key,
                "player_name": participant.name,
                "vehicle": participant.vehicle,
                "team": participant.team.number(),
                "winner": winner,
                "map": context.map,
                "finish_reason": finish_reason(context.result),
                "death_reason": if actor.alive { -1 } else { actor.death_reason.max(0) },
                "duration": context.duration_ticks / 30,
                "premature_leave": !context.active_player_ids.contains(&participant.player_id),
                "stats": stats,
                "rewards": rewards,
                "public_results": public_results.clone(),
                "interactions": interaction_values(context.statistics, actor.key, context.actors),
            }),
        );
    }
    Ok(payloads)
}

fn statistics_value(row: Option<&VehicleStatistics>) -> Value {
    let row = row.cloned().unwrap_or_default();
    json!({
        "shots": row.shots_fired,
        "direct_hits": row.shots_hit,
        "piercings": row.shots_penetrated,
        "damage": row.damage_dealt,
        "damage_received": row.damage_received,
        "damage_blocked": row.damage_blocked,
        "assist_track": row.damage_assisted_track,
        "assist_radio": row.damage_assisted_radio,
        "assist_stun": row.damage_assisted_stun,
        "kills": row.kills,
        "spotted": row.spotted,
        "capture_points": row.capture_points,
        "dropped_capture_points": row.dropped_capture_points,
    })
}

fn reward_statistics(row: Option<&VehicleStatistics>) -> RewardStatistics {
    let row = row.cloned().unwrap_or_default();
    RewardStatistics {
        damage_dealt: row.damage_dealt,
        damage_assisted_track: row.damage_assisted_track,
        damage_assisted_radio: row.damage_assisted_radio,
        damage_assisted_stun: row.damage_assisted_stun,
        kills: u64::from(row.kills),
        spotted: u64::from(row.spotted),
        capture_points: u64::from(row.capture_points),
        dropped_capture_points: u64::from(row.dropped_capture_points),
    }
}

fn interaction_values(
    statistics: &StatisticsLedger,
    actor: VehicleKey,
    actors: &[ResultActor],
) -> Vec<Value> {
    let teams: BTreeMap<_, _> = actors.iter().map(|actor| (actor.key, actor.team)).collect();
    let actor_team = teams.get(&actor).copied().unwrap_or(0);
    statistics
        .interactions(actor)
        .filter(|row| {
            row.target != actor
                && teams
                    .get(&row.target)
                    .is_some_and(|target_team| *target_team != actor_team)
        })
        .map(interaction_value)
        .collect()
}

fn interaction_value(row: &InteractionStatistics) -> Value {
    json!({
        "target_kind": kind_name(row.target.kind),
        "target_id": row.target.id,
        "spotted": row.spotted,
        "death_reason": row.death_reason,
        "direct_hits": row.direct_hits,
        "explosion_hits": row.explosion_hits,
        "piercings": row.piercings,
        "damage": row.damage,
        "assist_track": row.assist_track,
        "assist_radio": row.assist_radio,
        "assist_stun": row.assist_stun,
        "crits": row.crits,
        "fire": row.fire,
        "stun_num": row.stun_num,
        "stun_duration": row.stun_duration,
        "damage_blocked": row.damage_blocked,
        "damage_received": row.damage_received,
        "ricochets_received": row.ricochets_received,
        "no_damage_direct_hits_received": row.no_damage_direct_hits_received,
        "target_kills": row.target_kills,
    })
}

fn finish_reason(result: &BattleResult) -> u8 {
    match result.reason.as_str() {
        "team_eliminated" | "elimination" => 1,
        "base captured" => 2,
        "battle_timeout" => 3,
        "all_players_left" => 4,
        _ => 5,
    }
}

fn kind_name(kind: VehicleKind) -> &'static str {
    match kind {
        VehicleKind::Player => "player",
        VehicleKind::Bot => "bot",
    }
}

fn validate_text(value: &str, maximum: usize) -> Result<(), ResultBuildError> {
    if value.is_empty() || value.len() > maximum {
        Err(ResultBuildError::InvalidText)
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::receipt_store::ReceiptStore;
    use crate::room::{BattleWinner, Team};
    use std::fs;

    fn player(id: u64) -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Player,
            id,
        }
    }

    fn bot(id: u64) -> VehicleKey {
        VehicleKey {
            kind: VehicleKind::Bot,
            id,
        }
    }

    #[test]
    fn payload_is_accepted_by_the_durable_python_compatible_store() {
        let participant = RoundParticipant {
            player_id: 1,
            account_key: "account-1".to_owned(),
            name: "Alice".to_owned(),
            vehicle: "ussr:R11_MS-1".to_owned(),
            max_health: 90,
            team: Team::One,
            slot: 0,
            vehicle_configuration: json!({}),
        };
        let actors = vec![
            ResultActor {
                key: player(1),
                name: "Alice".to_owned(),
                vehicle: "ussr:R11_MS-1".to_owned(),
                team: 1,
                health: 80,
                alive: true,
                death_reason: -1,
                killer: None,
                team_killer: false,
                vehicle_tier: 1,
            },
            ResultActor {
                key: bot(16),
                name: "BOT-2-01".to_owned(),
                vehicle: "germany:G12_Ltraktor".to_owned(),
                team: 2,
                health: 0,
                alive: false,
                death_reason: 0,
                killer: Some(player(1)),
                team_killer: false,
                vehicle_tier: 1,
            },
        ];
        let result = BattleResult {
            winner: BattleWinner::Team(Team::One),
            reason: "team_eliminated".to_owned(),
            base_team: None,
        };
        let payloads = build_receipt_payloads(ReceiptBuildContext {
            receipt_namespace: "server",
            arena_unique_id: 7,
            round_id: 3,
            map: "01_karelia",
            duration_ticks: 900,
            result: &result,
            participants: std::slice::from_ref(&participant),
            actors: &actors,
            active_player_ids: &BTreeSet::from([1]),
            statistics: &StatisticsLedger::new(),
        })
        .unwrap();
        let receipt = crate::room::ResultReceipt {
            receipt_id: "server:3:1".to_owned(),
            round_id: 3,
            player_id: 1,
            account_key: "account-1".to_owned(),
            result,
            payload: payloads[&1].clone(),
        };
        let path = std::env::temp_dir().join(format!(
            "offline-rust-result-{}-{}.json",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        let _ = fs::remove_file(&path);
        let mut store = ReceiptStore::open(&path).unwrap();
        store.append(receipt).unwrap();
        assert_eq!(store.len(), 1);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn missing_participant_actor_fails_closed() {
        let participant = RoundParticipant {
            player_id: 1,
            account_key: "account-1".to_owned(),
            name: "Alice".to_owned(),
            vehicle: "ussr:R11_MS-1".to_owned(),
            max_health: 90,
            team: Team::One,
            slot: 0,
            vehicle_configuration: json!({}),
        };
        let result = BattleResult {
            winner: BattleWinner::Draw,
            reason: "battle_timeout".to_owned(),
            base_team: None,
        };
        assert_eq!(
            build_receipt_payloads(ReceiptBuildContext {
                receipt_namespace: "server",
                arena_unique_id: 1,
                round_id: 1,
                map: "01_karelia",
                duration_ticks: 0,
                result: &result,
                participants: std::slice::from_ref(&participant),
                actors: &[ResultActor {
                    key: bot(16),
                    name: "BOT".to_owned(),
                    vehicle: "ussr:R11_MS-1".to_owned(),
                    team: 2,
                    health: 1,
                    alive: true,
                    death_reason: -1,
                    killer: None,
                    team_killer: false,
                    vehicle_tier: 1,
                }],
                active_player_ids: &BTreeSet::new(),
                statistics: &StatisticsLedger::new(),
            }),
            Err(ResultBuildError::MissingParticipant(1))
        );
    }

    #[test]
    fn abandoned_bot_adjudication_preserves_the_python_server_ordering() {
        let state = |team, health, max_health| RemainingBotState {
            team,
            health,
            max_health,
            alive: health > 0,
        };

        assert_eq!(
            remaining_bot_winner(
                1,
                [
                    state(Team::One, 10, 100),
                    state(Team::One, 10, 100),
                    state(Team::Two, 100, 100),
                    state(Team::Two, 0, 100),
                ],
            ),
            BattleWinner::Team(Team::One)
        );
        assert_eq!(
            remaining_bot_winner(1, [state(Team::One, 40, 100), state(Team::Two, 50, 200),],),
            BattleWinner::Team(Team::One)
        );
        assert_eq!(
            remaining_bot_winner(1, [state(Team::One, 50, 100), state(Team::Two, 100, 200),],),
            BattleWinner::Team(Team::Two)
        );
        assert_eq!(
            remaining_bot_winner(1, [state(Team::One, 50, 100), state(Team::Two, 50, 100),],),
            BattleWinner::Team(Team::Two)
        );
        assert_eq!(
            remaining_bot_winner(2, [state(Team::One, 50, 100), state(Team::Two, 50, 100),],),
            BattleWinner::Team(Team::One)
        );
    }
}
