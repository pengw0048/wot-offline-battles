//! Deterministic server-owned bot vehicle lineup planning.
//!
//! The visible clients and the hidden native oracle must load the same vehicle
//! for every bot slot.  This module derives that canonical assignment once
//! from the descriptor donor's pinned #1513 catalog, the waiting-room roster,
//! and the host-selected tier preset.  Native clients receive the result in
//! the battle manifest and do not independently elect a lineup.

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

use thiserror::Error;

use crate::config::BotLineupEntry;
use crate::descriptor_exchange::{CatalogVehicle, DescriptorCatalog};
use crate::room::{BotTierMode, RoomConfig, RoundParticipant, Team};

const UNUSABLE_VEHICLE_0922: &str = "germany:G138_VK168_02_Mauerbrecher";
const LEGACY_T23: &str = "usa:T23";

pub type BotSlot = (u8, u8);
pub type BotVehicleAssignments = BTreeMap<BotSlot, String>;

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum LineupError {
    #[error("the canonical player anchor is missing from the descriptor catalog")]
    MissingAnchor,
    #[error("a waiting-room player vehicle is missing from the descriptor catalog")]
    MissingPlayerVehicle,
    #[error("the descriptor catalog has no usable vehicle for the selected bot tier mode")]
    NoEligibleVehicle,
    #[error("exact bot vehicle {0:?} is absent or unusable in the pinned descriptor catalog")]
    InvalidExactVehicle(String),
    #[error("the deterministic lineup did not fill every active bot slot")]
    Incomplete,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum MatchClass {
    Heavy,
    Medium,
    TankDestroyer,
    Light,
    Spg,
}

impl MatchClass {
    const REGULAR: [Self; 4] = [Self::Heavy, Self::Medium, Self::TankDestroyer, Self::Light];
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct VehicleProfile {
    name: String,
    level: u8,
    class: MatchClass,
}

impl VehicleProfile {
    fn from_catalog(vehicle: &CatalogVehicle) -> Self {
        Self {
            name: vehicle.name.clone(),
            level: vehicle.level,
            class: match_class(&vehicle.tags),
        }
    }
}

/// Reject exact launcher pins before the room leaves the waiting phase.
pub fn validate_exact_lineup(
    catalog: &DescriptorCatalog,
    exact: &[BotLineupEntry],
) -> Result<(), LineupError> {
    for entry in exact {
        let Some(vehicle) = catalog.get(&entry.vehicle) else {
            return Err(LineupError::InvalidExactVehicle(entry.vehicle.clone()));
        };
        if !vehicle_is_usable(vehicle) {
            return Err(LineupError::InvalidExactVehicle(entry.vehicle.clone()));
        }
    }
    Ok(())
}

/// Build one canonical assignment for all active bot slots.
pub fn plan_bot_lineup(
    catalog: &DescriptorCatalog,
    participants: &[RoundParticipant],
    config: &RoomConfig,
    tier_mode: BotTierMode,
    exact: &[BotLineupEntry],
    round_id: u64,
    map: &str,
) -> Result<BotVehicleAssignments, LineupError> {
    validate_exact_lineup(catalog, exact)?;

    let mut humans: Vec<_> = participants.iter().collect();
    humans.sort_by_key(|participant| participant.player_id);
    let anchor = humans
        .first()
        .and_then(|participant| catalog.get(&participant.vehicle))
        .ok_or(LineupError::MissingAnchor)?;

    let mut human_profiles = BTreeMap::new();
    for participant in &humans {
        let vehicle = catalog
            .get(&participant.vehicle)
            .ok_or(LineupError::MissingPlayerVehicle)?;
        human_profiles.insert(participant.player_id, VehicleProfile::from_catalog(vehicle));
    }

    let mut candidates: Vec<_> = catalog
        .vehicles()
        .filter(|vehicle| {
            vehicle_is_usable(vehicle) && tier_mode.admits_tier(anchor.level, vehicle.level)
        })
        .map(VehicleProfile::from_catalog)
        .collect();
    candidates.sort_by(profile_order);
    if candidates.is_empty() {
        return Err(LineupError::NoEligibleVehicle);
    }

    let active_slots = active_bot_slots(participants, config);
    if active_slots.is_empty() {
        return Ok(BTreeMap::new());
    }

    let seed = lineup_seed(round_id, map, &humans, tier_mode);
    let mut random = DeterministicRandom::new(seed);
    let allowed_tiers = select_match_tiers(anchor.level, tier_mode, &candidates, &mut random);
    let required = shared_human_requirements(participants, &human_profiles);
    let team_size = config.team_capacities.into_iter().max().unwrap_or(1);
    let template = build_match_template(
        &candidates,
        team_size,
        &VehicleProfile::from_catalog(anchor),
        &allowed_tiers,
        &required,
        &mut random,
    );

    let mut assignments = BTreeMap::new();
    for team in Team::ALL {
        let mut team_slots: Vec<_> = active_slots
            .iter()
            .copied()
            .filter(|slot| slot.0 == team.number())
            .collect();
        team_slots.sort_unstable();
        if team_slots.is_empty() {
            continue;
        }
        let team_humans: Vec<_> = humans
            .iter()
            .filter(|participant| participant.team == team)
            .filter_map(|participant| human_profiles.get(&participant.player_id))
            .cloned()
            .collect();
        let mut picked = remaining_template(&template, &team_humans);
        fill_shortfall(&mut picked, team_slots.len(), &candidates, &mut random);
        random.shuffle(&mut picked);
        picked.sort_by_key(|profile| profile.class);
        for (slot, profile) in team_slots.into_iter().zip(picked.into_iter()) {
            assignments.insert(slot, profile.name);
        }
    }

    for entry in exact {
        let slot = (entry.team, entry.slot);
        if assignments.contains_key(&slot) {
            assignments.insert(slot, entry.vehicle.clone());
        }
    }
    if assignments.len() != active_slots.len()
        || active_slots
            .iter()
            .any(|slot| !assignments.contains_key(slot))
    {
        return Err(LineupError::Incomplete);
    }
    Ok(assignments)
}

fn active_bot_slots(participants: &[RoundParticipant], config: &RoomConfig) -> Vec<BotSlot> {
    let occupied: BTreeSet<_> = participants
        .iter()
        .map(|participant| (participant.team.number(), participant.slot as u8))
        .collect();
    let mut result = Vec::new();
    for team in Team::ALL {
        for slot in 0..config.team_capacity(team) {
            let key = (team.number(), slot as u8);
            if !occupied.contains(&key) {
                result.push(key);
            }
        }
    }
    result
}

fn shared_human_requirements(
    participants: &[RoundParticipant],
    profiles: &BTreeMap<u32, VehicleProfile>,
) -> Vec<VehicleProfile> {
    let mut required_counts: BTreeMap<(u8, MatchClass), usize> = BTreeMap::new();
    let mut representatives = BTreeMap::new();
    for team in Team::ALL {
        let mut team_counts = BTreeMap::new();
        for participant in participants.iter().filter(|value| value.team == team) {
            let Some(profile) = profiles.get(&participant.player_id) else {
                continue;
            };
            let key = (profile.level, profile.class);
            *team_counts.entry(key).or_insert(0) += 1;
            representatives
                .entry(key)
                .or_insert_with(|| profile.clone());
        }
        for (key, count) in team_counts {
            required_counts
                .entry(key)
                .and_modify(|current| *current = (*current).max(count))
                .or_insert(count);
        }
    }
    let mut result = Vec::new();
    for (key, count) in required_counts {
        if let Some(profile) = representatives.get(&key) {
            result.extend(std::iter::repeat_n(profile.clone(), count));
        }
    }
    result
}

fn select_match_tiers(
    anchor_tier: u8,
    mode: BotTierMode,
    candidates: &[VehicleProfile],
    random: &mut DeterministicRandom,
) -> Vec<u8> {
    let available: BTreeSet<_> = candidates.iter().map(|vehicle| vehicle.level).collect();
    if mode != BotTierMode::Random {
        return available.into_iter().collect();
    }
    let lower = anchor_tier
        .checked_sub(1)
        .filter(|tier| available.contains(tier));
    let upper = anchor_tier
        .checked_add(1)
        .filter(|tier| available.contains(tier));
    let mode_roll = random.unit_f64();
    let side_roll = random.unit_f64();
    if mode_roll < 0.28 || (lower.is_none() && upper.is_none()) {
        return vec![anchor_tier];
    }
    if mode_roll < 0.72 || lower.is_none() || upper.is_none() {
        let other = match (lower, upper) {
            (Some(lower), Some(upper)) => {
                if side_roll < 0.5 {
                    lower
                } else {
                    upper
                }
            }
            (Some(lower), None) => lower,
            (None, Some(upper)) => upper,
            (None, None) => anchor_tier,
        };
        let mut result = vec![anchor_tier, other];
        result.sort_unstable();
        result.dedup();
        return result;
    }
    [lower, Some(anchor_tier), upper]
        .into_iter()
        .flatten()
        .collect()
}

fn build_match_template(
    candidates: &[VehicleProfile],
    team_size: usize,
    anchor: &VehicleProfile,
    allowed_tiers: &[u8],
    required: &[VehicleProfile],
    random: &mut DeterministicRandom,
) -> Vec<VehicleProfile> {
    let team_size = team_size.max(1);
    let allowed: BTreeSet<_> = allowed_tiers.iter().copied().collect();
    let mut usable: Vec<_> = candidates
        .iter()
        .filter(|candidate| allowed.contains(&candidate.level))
        .cloned()
        .collect();
    if usable.is_empty() {
        usable.push(anchor.clone());
    }

    let mut tier_slots = repeated_tiers(allowed_tiers, anchor.level, team_size);
    random.shuffle(&mut tier_slots);
    let mut class_slots: Vec<_> = MatchClass::REGULAR
        .into_iter()
        .cycle()
        .take(team_size)
        .collect();
    let required_spgs = required
        .iter()
        .filter(|profile| profile.class == MatchClass::Spg)
        .count();
    if usable
        .iter()
        .any(|profile| profile.class == MatchClass::Spg)
        && (required_spgs > 0 || random.unit_f64() < 0.65)
    {
        if let Some(last) = class_slots.last_mut() {
            *last = MatchClass::Spg;
        }
    }
    random.shuffle(&mut class_slots);

    let mut result = Vec::with_capacity(team_size);
    let mut usage: BTreeMap<String, usize> = BTreeMap::new();
    let mut spg_count = 0;
    for profile in required.iter().take(team_size) {
        let candidate = usable
            .iter()
            .find(|candidate| candidate.level == profile.level && candidate.class == profile.class)
            .cloned()
            .unwrap_or_else(|| profile.clone());
        record_pick(&mut result, &mut usage, &mut spg_count, candidate);
        remove_first(&mut tier_slots, profile.level);
        remove_first(&mut class_slots, profile.class);
    }

    while result.len() < team_size {
        let desired_tier = tier_slots.pop().unwrap_or(anchor.level);
        let desired_class = class_slots.pop().unwrap_or(anchor.class);
        let mut choices: Vec<_> = usable
            .iter()
            .filter(|candidate| candidate.level == desired_tier && candidate.class == desired_class)
            .cloned()
            .collect();
        if choices.is_empty() {
            choices = usable
                .iter()
                .filter(|candidate| candidate.level == desired_tier)
                .cloned()
                .collect();
        }
        if choices.is_empty() {
            choices.clone_from(&usable);
        }
        if spg_count >= 1 {
            let regular: Vec<_> = choices
                .iter()
                .filter(|candidate| candidate.class != MatchClass::Spg)
                .cloned()
                .collect();
            if !regular.is_empty() {
                choices = regular;
            }
        }
        random.shuffle(&mut choices);
        let candidate = choices
            .into_iter()
            .min_by_key(|candidate| usage.get(&candidate.name).copied().unwrap_or(0))
            .expect("the usable vehicle pool is non-empty");
        record_pick(&mut result, &mut usage, &mut spg_count, candidate);
    }
    result
}

fn remaining_template(
    template: &[VehicleProfile],
    humans: &[VehicleProfile],
) -> Vec<VehicleProfile> {
    let mut remaining = template.to_vec();
    for human in humans {
        let Some((best_index, _)) = remaining
            .iter()
            .enumerate()
            .map(|(index, candidate)| {
                let tier_distance = candidate.level.abs_diff(human.level) as usize;
                let class_penalty = usize::from(candidate.class != human.class) * 8;
                (index, tier_distance * 3 + class_penalty)
            })
            .min_by_key(|(_, score)| *score)
        else {
            break;
        };
        remaining.remove(best_index);
    }
    remaining
}

fn fill_shortfall(
    picked: &mut Vec<VehicleProfile>,
    needed: usize,
    candidates: &[VehicleProfile],
    random: &mut DeterministicRandom,
) {
    if picked.len() > needed {
        picked.truncate(needed);
    }
    let mut index = random.index(candidates.len());
    while picked.len() < needed {
        let spgs = picked
            .iter()
            .filter(|profile| profile.class == MatchClass::Spg)
            .count();
        let mut candidate = &candidates[index % candidates.len()];
        if spgs >= 1 && candidate.class == MatchClass::Spg {
            if let Some(regular) = candidates
                .iter()
                .cycle()
                .skip(index)
                .take(candidates.len())
                .find(|profile| profile.class != MatchClass::Spg)
            {
                candidate = regular;
            }
        }
        picked.push(candidate.clone());
        index = index.wrapping_add(1);
    }
}

fn repeated_tiers(allowed: &[u8], anchor: u8, length: usize) -> Vec<u8> {
    let levels = if allowed.is_empty() {
        vec![anchor]
    } else {
        allowed.to_vec()
    };
    levels.into_iter().cycle().take(length).collect()
}

fn remove_first<T: PartialEq>(values: &mut Vec<T>, expected: T) {
    if let Some(index) = values.iter().position(|value| value == &expected) {
        values.remove(index);
    } else {
        values.pop();
    }
}

fn record_pick(
    result: &mut Vec<VehicleProfile>,
    usage: &mut BTreeMap<String, usize>,
    spg_count: &mut usize,
    candidate: VehicleProfile,
) {
    *usage.entry(candidate.name.clone()).or_insert(0) += 1;
    if candidate.class == MatchClass::Spg {
        *spg_count += 1;
    }
    result.push(candidate);
}

fn profile_order(left: &VehicleProfile, right: &VehicleProfile) -> Ordering {
    (left.level, left.class, left.name.as_str()).cmp(&(
        right.level,
        right.class,
        right.name.as_str(),
    ))
}

fn match_class(tags: &[String]) -> MatchClass {
    for (tag, class) in [
        ("heavyTank", MatchClass::Heavy),
        ("mediumTank", MatchClass::Medium),
        ("AT-SPG", MatchClass::TankDestroyer),
        ("lightTank", MatchClass::Light),
        ("SPG", MatchClass::Spg),
    ] {
        if tags.iter().any(|value| value == tag) {
            return class;
        }
    }
    MatchClass::Medium
}

fn vehicle_is_usable(vehicle: &CatalogVehicle) -> bool {
    const EXCLUDED_TAGS: [&str; 4] = ["event_battles", "premiumIGR", "observer", "secret"];
    vehicle.name != UNUSABLE_VEHICLE_0922
        && vehicle.name != LEGACY_T23
        && !vehicle
            .tags
            .iter()
            .any(|tag| EXCLUDED_TAGS.contains(&tag.as_str()))
}

fn lineup_seed(
    round_id: u64,
    map: &str,
    participants: &[&RoundParticipant],
    tier_mode: BotTierMode,
) -> u64 {
    let mut hash = StableHasher::new();
    hash.add(b"battle-lineup-v2");
    hash.add(&round_id.to_le_bytes());
    hash.add(map.as_bytes());
    hash.add(tier_mode.as_str().as_bytes());
    for participant in participants {
        hash.add(&participant.player_id.to_le_bytes());
        hash.add(&[participant.team.number()]);
        hash.add(&(participant.slot as u64).to_le_bytes());
        hash.add(participant.vehicle.as_bytes());
    }
    hash.finish()
}

struct StableHasher(u64);

impl StableHasher {
    fn new() -> Self {
        Self(0xcbf2_9ce4_8422_2325)
    }

    fn add(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.0 ^= u64::from(*byte);
            self.0 = self.0.wrapping_mul(0x0000_0100_0000_01b3);
        }
        self.0 ^= 0xff;
        self.0 = self.0.wrapping_mul(0x0000_0100_0000_01b3);
    }

    fn finish(self) -> u64 {
        self.0
    }
}

struct DeterministicRandom(u64);

impl DeterministicRandom {
    fn new(seed: u64) -> Self {
        Self(seed ^ 0x9e37_79b9_7f4a_7c15)
    }

    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut value = self.0;
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        value ^ (value >> 31)
    }

    fn unit_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1_u64 << 53) as f64
    }

    fn index(&mut self, length: usize) -> usize {
        if length <= 1 {
            0
        } else {
            (self.next_u64() % length as u64) as usize
        }
    }

    fn shuffle<T>(&mut self, values: &mut [T]) {
        for upper in (1..values.len()).rev() {
            let index = self.index(upper + 1);
            values.swap(index, upper);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn profile(name: &str, level: u8, class: MatchClass) -> VehicleProfile {
        VehicleProfile {
            name: name.to_owned(),
            level,
            class,
        }
    }

    fn catalog_vehicle(name: &str, level: u8, tags: &[&str]) -> CatalogVehicle {
        CatalogVehicle {
            name: name.to_owned(),
            level,
            tags: tags.iter().map(|tag| (*tag).to_owned()).collect(),
        }
    }

    fn participant(player_id: u32, team: Team, slot: usize, vehicle: &str) -> RoundParticipant {
        RoundParticipant {
            player_id,
            account_key: format!("account-{player_id}"),
            name: format!("player-{player_id}"),
            vehicle: vehicle.to_owned(),
            max_health: 1_000,
            team,
            slot,
            vehicle_configuration: json!({}),
        }
    }

    #[test]
    fn mirrored_template_reserves_human_tier_and_class_requirements() {
        let candidates = vec![
            profile("heavy-5", 5, MatchClass::Heavy),
            profile("medium-5", 5, MatchClass::Medium),
            profile("td-6", 6, MatchClass::TankDestroyer),
            profile("light-6", 6, MatchClass::Light),
            profile("spg-6", 6, MatchClass::Spg),
        ];
        let required = vec![
            profile("human-heavy", 5, MatchClass::Heavy),
            profile("human-td", 6, MatchClass::TankDestroyer),
        ];
        let mut random = DeterministicRandom::new(1513);
        let template = build_match_template(
            &candidates,
            5,
            &candidates[1],
            &[5, 6],
            &required,
            &mut random,
        );
        assert_eq!(template.len(), 5);
        assert!(template
            .iter()
            .any(|value| value.level == 5 && value.class == MatchClass::Heavy));
        assert!(template
            .iter()
            .any(|value| { value.level == 6 && value.class == MatchClass::TankDestroyer }));
        assert!(
            template
                .iter()
                .filter(|value| value.class == MatchClass::Spg)
                .count()
                <= 1
        );
    }

    #[test]
    fn removing_humans_picks_the_closest_mirrored_slots() {
        let template = vec![
            profile("heavy-5", 5, MatchClass::Heavy),
            profile("medium-5", 5, MatchClass::Medium),
            profile("td-6", 6, MatchClass::TankDestroyer),
        ];
        let remaining =
            remaining_template(&template, &[profile("human-heavy", 5, MatchClass::Heavy)]);
        assert_eq!(
            remaining
                .iter()
                .map(|value| value.name.as_str())
                .collect::<Vec<_>>(),
            vec!["medium-5", "td-6"]
        );
    }

    #[test]
    fn deterministic_shuffle_and_seed_are_repeatable() {
        let mut first = DeterministicRandom::new(0x1513);
        let mut second = DeterministicRandom::new(0x1513);
        let mut left = vec![1, 2, 3, 4, 5, 6];
        let mut right = left.clone();
        first.shuffle(&mut left);
        second.shuffle(&mut right);
        assert_eq!(left, right);
        assert_ne!(left, vec![1, 2, 3, 4, 5, 6]);
    }

    #[test]
    fn fill_shortfall_preserves_the_automatic_spg_cap() {
        let candidates = vec![
            profile("spg", 5, MatchClass::Spg),
            profile("medium", 5, MatchClass::Medium),
        ];
        let mut picked = vec![candidates[0].clone()];
        fill_shortfall(
            &mut picked,
            5,
            &candidates,
            &mut DeterministicRandom::new(3),
        );
        assert_eq!(picked.len(), 5);
        assert_eq!(
            picked
                .iter()
                .filter(|profile| profile.class == MatchClass::Spg)
                .count(),
            1
        );
    }

    #[test]
    fn canonical_plan_fills_active_slots_and_applies_exact_pins() {
        let catalog = DescriptorCatalog::from_test_vehicles([
            catalog_vehicle("ussr:heavy_5", 5, &["heavyTank"]),
            catalog_vehicle("germany:medium_5", 5, &["mediumTank"]),
            catalog_vehicle("usa:td_5", 5, &["AT-SPG"]),
            catalog_vehicle("france:light_5", 5, &["lightTank"]),
            catalog_vehicle("ussr:spg_5", 5, &["SPG"]),
        ]);
        let participants = vec![
            participant(1, Team::One, 0, "ussr:heavy_5"),
            participant(2, Team::Two, 0, "germany:medium_5"),
        ];
        let config = RoomConfig::new(6, 3, 3, "ussr:heavy_5", "lineup-test").unwrap();
        let exact = vec![BotLineupEntry {
            team: 1,
            slot: 2,
            vehicle: "usa:td_5".to_owned(),
        }];
        let first = plan_bot_lineup(
            &catalog,
            &participants,
            &config,
            BotTierMode::Same,
            &exact,
            7,
            "01_karelia",
        )
        .unwrap();
        let second = plan_bot_lineup(
            &catalog,
            &participants,
            &config,
            BotTierMode::Same,
            &exact,
            7,
            "01_karelia",
        )
        .unwrap();
        assert_eq!(first, second);
        assert_eq!(first.len(), 4);
        assert_eq!(first.get(&(1, 2)).map(String::as_str), Some("usa:td_5"));
        assert!(!first.contains_key(&(1, 0)));
        assert!(!first.contains_key(&(2, 0)));
    }

    #[test]
    fn exact_pins_reject_nonstandard_and_missing_resource_vehicles() {
        let catalog = DescriptorCatalog::from_test_vehicles([
            catalog_vehicle("ussr:secret", 5, &["mediumTank", "secret"]),
            catalog_vehicle("ussr:event", 5, &["mediumTank", "event_battles"]),
            catalog_vehicle("germany:igr", 5, &["heavyTank", "premiumIGR"]),
            catalog_vehicle("ussr:observer", 1, &["lightTank", "observer"]),
            catalog_vehicle(UNUSABLE_VEHICLE_0922, 8, &["heavyTank", "premium"]),
        ]);
        for name in [
            "ussr:secret",
            "ussr:event",
            "germany:igr",
            "ussr:observer",
            UNUSABLE_VEHICLE_0922,
            "usa:missing",
        ] {
            assert_eq!(
                validate_exact_lineup(
                    &catalog,
                    &[BotLineupEntry {
                        team: 1,
                        slot: 1,
                        vehicle: name.to_owned(),
                    }],
                ),
                Err(LineupError::InvalidExactVehicle(name.to_owned()))
            );
        }
    }
}
