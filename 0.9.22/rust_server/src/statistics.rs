//! Deterministic per-vehicle and per-target battle statistics.

use crate::combat::VehicleKey;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct VehicleStatistics {
    pub team: u8,
    pub shots_fired: u32,
    pub shots_hit: u32,
    pub shots_penetrated: u32,
    pub damage_dealt: u64,
    pub damage_received: u64,
    pub damage_blocked: u64,
    pub damage_assisted_track: u64,
    pub damage_assisted_radio: u64,
    pub damage_assisted_stun: u64,
    pub kills: u32,
    pub spotted: u32,
    pub capture_points: u32,
    pub dropped_capture_points: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InteractionStatistics {
    pub target: VehicleKey,
    pub spotted: u8,
    pub death_reason: i8,
    pub direct_hits: u16,
    pub explosion_hits: u16,
    pub piercings: u16,
    pub damage: u16,
    pub assist_track: u16,
    pub assist_radio: u16,
    pub assist_stun: u16,
    pub crits: u32,
    pub fire: u16,
    pub stun_num: u16,
    pub stun_duration: u16,
    pub damage_blocked: u32,
    pub damage_received: u16,
    pub ricochets_received: u16,
    pub no_damage_direct_hits_received: u16,
    pub target_kills: u8,
}

impl InteractionStatistics {
    fn new(target: VehicleKey) -> Self {
        Self {
            target,
            spotted: 0,
            death_reason: -1,
            direct_hits: 0,
            explosion_hits: 0,
            piercings: 0,
            damage: 0,
            assist_track: 0,
            assist_radio: 0,
            assist_stun: 0,
            crits: 0,
            fire: 0,
            stun_num: 0,
            stun_duration: 0,
            damage_blocked: 0,
            damage_received: 0,
            ricochets_received: 0,
            no_damage_direct_hits_received: 0,
            target_kills: 0,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct StatisticsLedger {
    rows: BTreeMap<VehicleKey, VehicleStatistics>,
    interactions: BTreeMap<VehicleKey, BTreeMap<VehicleKey, InteractionStatistics>>,
    spotted: BTreeMap<VehicleKey, BTreeSet<VehicleKey>>,
    track_immobilisers: BTreeMap<VehicleKey, VehicleKey>,
}

impl StatisticsLedger {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, actor: VehicleKey, team: u8) {
        self.rows.entry(actor).or_default().team = team;
    }

    pub fn row(&self, actor: VehicleKey) -> Option<&VehicleStatistics> {
        self.rows.get(&actor)
    }

    pub fn rows(&self) -> impl Iterator<Item = (VehicleKey, &VehicleStatistics)> {
        self.rows.iter().map(|(&key, value)| (key, value))
    }

    pub fn interactions(&self, actor: VehicleKey) -> impl Iterator<Item = &InteractionStatistics> {
        self.interactions
            .get(&actor)
            .into_iter()
            .flat_map(|values| values.values())
    }

    pub fn record_shot(&mut self, actor: VehicleKey) {
        self.ensure(actor).shots_fired = self.ensure(actor).shots_fired.saturating_add(1);
    }

    pub fn record_impact(
        &mut self,
        actor: VehicleKey,
        target: VehicleKey,
        direct: bool,
        explosion: bool,
        penetrated: bool,
        blocked: u32,
    ) {
        {
            let row = self.ensure(actor);
            if direct {
                row.shots_hit = row.shots_hit.saturating_add(1);
            }
            if penetrated {
                row.shots_penetrated = row.shots_penetrated.saturating_add(1);
            }
        }
        {
            let interaction = self.interaction_mut(actor, target);
            if direct {
                interaction.direct_hits = interaction.direct_hits.saturating_add(1);
            }
            if explosion {
                interaction.explosion_hits = interaction.explosion_hits.saturating_add(1);
            }
            if penetrated {
                interaction.piercings = interaction.piercings.saturating_add(1);
            }
        }
        if blocked > 0 {
            let victim = self.ensure(target);
            victim.damage_blocked = victim.damage_blocked.saturating_add(u64::from(blocked));
            let received = self.interaction_mut(target, actor);
            received.damage_blocked = received.damage_blocked.saturating_add(blocked);
        }
    }

    pub fn report_spotted(&mut self, reporter: VehicleKey, targets: &[VehicleKey]) {
        let previous = self.spotted.entry(reporter).or_default().clone();
        let next: BTreeSet<_> = targets.iter().copied().collect();
        for &target in next.difference(&previous) {
            let interaction = self.interaction_mut(reporter, target);
            if interaction.spotted == 0 {
                interaction.spotted = 1;
                self.ensure(reporter).spotted = self.ensure(reporter).spotted.saturating_add(1);
            }
        }
        self.spotted.insert(reporter, next);
    }

    pub fn radio_reporters(&self, target: VehicleKey) -> BTreeSet<VehicleKey> {
        self.spotted
            .iter()
            .filter_map(|(&reporter, targets)| targets.contains(&target).then_some(reporter))
            .collect()
    }

    pub fn clear_spotting_actor(&mut self, actor: VehicleKey) {
        self.spotted.remove(&actor);
        for targets in self.spotted.values_mut() {
            targets.remove(&actor);
        }
    }

    pub fn mark_track_immobilised(&mut self, target: VehicleKey, holder: VehicleKey) {
        self.track_immobilisers.insert(target, holder);
    }

    pub fn clear_track_immobilised(&mut self, target: VehicleKey) {
        self.track_immobilisers.remove(&target);
    }

    /// Record applied hull damage and return each awarded assist.
    pub fn record_damage(
        &mut self,
        attacker: Option<VehicleKey>,
        target: VehicleKey,
        damage: u32,
        target_tracks_destroyed: bool,
        active_stun_assister: Option<VehicleKey>,
        eligible_radio_reporters: &BTreeSet<VehicleKey>,
    ) -> Vec<AssistAward> {
        if damage == 0 {
            return Vec::new();
        }
        self.ensure(target).damage_received = self
            .ensure(target)
            .damage_received
            .saturating_add(u64::from(damage));
        let target_team = self.team(target);
        let Some(attacker) = attacker else {
            return Vec::new();
        };
        if self.team(attacker) == target_team {
            return Vec::new();
        }
        self.ensure(attacker).damage_dealt = self
            .ensure(attacker)
            .damage_dealt
            .saturating_add(u64::from(damage));
        self.interaction_mut(attacker, target).damage = self
            .interaction_mut(attacker, target)
            .damage
            .saturating_add(damage.min(u32::from(u16::MAX)) as u16);
        self.interaction_mut(target, attacker).damage_received = self
            .interaction_mut(target, attacker)
            .damage_received
            .saturating_add(damage.min(u32::from(u16::MAX)) as u16);

        let mut awards = Vec::new();
        if target_tracks_destroyed {
            if let Some(&holder) = self.track_immobilisers.get(&target) {
                if holder != attacker && self.team(holder) != target_team {
                    awards.push(AssistAward {
                        category: AssistCategory::Track,
                        assister: holder,
                        attacker,
                        target,
                        damage,
                    });
                }
            }
        }
        if let Some(holder) = active_stun_assister {
            if holder != attacker && self.team(holder) != target_team {
                awards.push(AssistAward {
                    category: AssistCategory::Stun,
                    assister: holder,
                    attacker,
                    target,
                    damage,
                });
            }
        }
        for &reporter in eligible_radio_reporters {
            if reporter != attacker
                && self.team(reporter) != target_team
                && self
                    .spotted
                    .get(&reporter)
                    .is_some_and(|targets| targets.contains(&target))
            {
                awards.push(AssistAward {
                    category: AssistCategory::Radio,
                    assister: reporter,
                    attacker,
                    target,
                    damage,
                });
            }
        }
        for award in &awards {
            let value = u64::from(award.damage);
            match award.category {
                AssistCategory::Track => {
                    self.ensure(award.assister).damage_assisted_track = self
                        .ensure(award.assister)
                        .damage_assisted_track
                        .saturating_add(value);
                    let interaction = self.interaction_mut(award.assister, award.target);
                    interaction.assist_track = interaction
                        .assist_track
                        .saturating_add(award.damage.min(u32::from(u16::MAX)) as u16);
                }
                AssistCategory::Radio => {
                    self.ensure(award.assister).damage_assisted_radio = self
                        .ensure(award.assister)
                        .damage_assisted_radio
                        .saturating_add(value);
                    let interaction = self.interaction_mut(award.assister, award.target);
                    interaction.assist_radio = interaction
                        .assist_radio
                        .saturating_add(award.damage.min(u32::from(u16::MAX)) as u16);
                }
                AssistCategory::Stun => {
                    self.ensure(award.assister).damage_assisted_stun = self
                        .ensure(award.assister)
                        .damage_assisted_stun
                        .saturating_add(value);
                    let interaction = self.interaction_mut(award.assister, award.target);
                    interaction.assist_stun = interaction
                        .assist_stun
                        .saturating_add(award.damage.min(u32::from(u16::MAX)) as u16);
                }
            }
        }
        awards
    }

    pub fn record_enemy_frag(
        &mut self,
        attacker: VehicleKey,
        target: VehicleKey,
        death_reason: i8,
    ) {
        if self.team(attacker) == self.team(target) || attacker == target {
            return;
        }
        self.ensure(attacker).kills = self.ensure(attacker).kills.saturating_add(1);
        let interaction = self.interaction_mut(attacker, target);
        interaction.target_kills = interaction.target_kills.saturating_add(1);
        interaction.death_reason = death_reason.clamp(-1, 10);
    }

    pub fn record_capture(&mut self, actor: VehicleKey, gained: u32, dropped: u32) {
        let row = self.ensure(actor);
        row.capture_points = row.capture_points.saturating_add(gained);
        row.dropped_capture_points = row.dropped_capture_points.saturating_add(dropped);
    }

    fn team(&self, actor: VehicleKey) -> u8 {
        self.rows.get(&actor).map(|row| row.team).unwrap_or(0)
    }

    fn ensure(&mut self, actor: VehicleKey) -> &mut VehicleStatistics {
        self.rows.entry(actor).or_default()
    }

    fn interaction_mut(
        &mut self,
        actor: VehicleKey,
        target: VehicleKey,
    ) -> &mut InteractionStatistics {
        self.ensure(actor);
        self.interactions
            .entry(actor)
            .or_default()
            .entry(target)
            .or_insert_with(|| InteractionStatistics::new(target))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AssistCategory {
    Track,
    Radio,
    Stun,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AssistAward {
    pub category: AssistCategory,
    pub assister: VehicleKey,
    pub attacker: VehicleKey,
    pub target: VehicleKey,
    pub damage: u32,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::combat::VehicleKind;

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

    fn ledger() -> StatisticsLedger {
        let mut ledger = StatisticsLedger::new();
        ledger.register(player(1), 1);
        ledger.register(player(2), 1);
        ledger.register(bot(11), 2);
        ledger
    }

    #[test]
    fn shot_and_impact_keep_bounded_per_target_rows() {
        let mut ledger = ledger();
        ledger.record_shot(player(1));
        ledger.record_impact(player(1), bot(11), true, false, true, 42);
        assert_eq!(ledger.row(player(1)).unwrap().shots_fired, 1);
        assert_eq!(ledger.row(player(1)).unwrap().shots_hit, 1);
        let interaction = ledger.interactions(player(1)).next().unwrap();
        assert_eq!(interaction.direct_hits, 1);
        assert_eq!(interaction.piercings, 1);
        assert_eq!(interaction.damage_blocked, 0);
        assert_eq!(ledger.row(bot(11)).unwrap().damage_blocked, 42);
        let received = ledger.interactions(bot(11)).next().unwrap();
        assert_eq!(received.damage_blocked, 42);
    }

    #[test]
    fn track_and_radio_assists_are_both_attributed() {
        let mut ledger = ledger();
        ledger.report_spotted(player(2), &[bot(11)]);
        ledger.mark_track_immobilised(bot(11), player(2));
        let awards = ledger.record_damage(
            Some(player(1)),
            bot(11),
            120,
            true,
            None,
            &BTreeSet::from([player(2)]),
        );
        assert_eq!(awards.len(), 2);
        assert_eq!(ledger.row(player(1)).unwrap().damage_dealt, 120);
        assert_eq!(ledger.row(bot(11)).unwrap().damage_received, 120);
        assert_eq!(ledger.row(player(2)).unwrap().damage_assisted_track, 120);
        assert_eq!(ledger.row(player(2)).unwrap().damage_assisted_radio, 120);
    }

    #[test]
    fn canonical_stun_assist_does_not_invent_duration_statistics() {
        let mut ledger = ledger();
        let awards = ledger.record_damage(
            Some(player(1)),
            bot(11),
            120,
            false,
            Some(player(2)),
            &BTreeSet::new(),
        );

        assert_eq!(awards.len(), 1);
        assert_eq!(awards[0].category, AssistCategory::Stun);
        assert_eq!(ledger.row(player(2)).unwrap().damage_assisted_stun, 120);
        let interaction = ledger.interactions(player(2)).next().unwrap();
        assert_eq!(interaction.assist_stun, 120);
        assert_eq!(interaction.stun_num, 0);
        assert_eq!(interaction.stun_duration, 0);
    }

    #[test]
    fn friendly_damage_never_earns_damage_or_assist_credit() {
        let mut ledger = ledger();
        let awards = ledger.record_damage(
            Some(player(1)),
            player(2),
            100,
            false,
            None,
            &BTreeSet::new(),
        );
        assert!(awards.is_empty());
        assert_eq!(ledger.row(player(1)).unwrap().damage_dealt, 0);
        assert_eq!(ledger.row(player(2)).unwrap().damage_received, 100);
    }

    #[test]
    fn spotted_and_kill_counts_are_idempotent_or_enemy_only() {
        let mut ledger = ledger();
        ledger.report_spotted(player(1), &[bot(11)]);
        ledger.report_spotted(player(1), &[bot(11)]);
        ledger.record_enemy_frag(player(1), bot(11), 2);
        ledger.record_enemy_frag(player(1), player(2), 2);
        assert_eq!(ledger.row(player(1)).unwrap().spotted, 1);
        assert_eq!(ledger.row(player(1)).unwrap().kills, 1);
        assert_eq!(
            ledger.interactions(player(1)).next().unwrap().target_kills,
            1
        );
    }

    #[test]
    fn current_radio_reporters_follow_direct_visibility_edges() {
        let mut ledger = ledger();
        ledger.report_spotted(player(1), &[bot(11)]);
        ledger.report_spotted(player(2), &[bot(11)]);
        assert_eq!(
            ledger.radio_reporters(bot(11)),
            BTreeSet::from([player(1), player(2)])
        );

        ledger.report_spotted(player(1), &[]);
        assert_eq!(ledger.radio_reporters(bot(11)), BTreeSet::from([player(2)]));
        assert_eq!(ledger.row(player(1)).unwrap().spotted, 1);
    }

    #[test]
    fn retiring_an_actor_clears_both_sides_of_spotting_state() {
        let mut ledger = ledger();
        ledger.report_spotted(player(1), &[bot(11)]);
        ledger.report_spotted(player(2), &[bot(11)]);

        ledger.clear_spotting_actor(player(1));
        assert_eq!(ledger.radio_reporters(bot(11)), BTreeSet::from([player(2)]));

        ledger.clear_spotting_actor(bot(11));
        assert!(ledger.radio_reporters(bot(11)).is_empty());
    }
}
