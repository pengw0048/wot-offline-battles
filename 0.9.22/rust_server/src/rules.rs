//! Server-owned standard battle rules.

use crate::room::Team;
use std::collections::{BTreeMap, BTreeSet};

pub const CAPTURE_RADIUS_METRES: f64 = 50.0;
pub const CAPTURE_TICK_HZ: u64 = 30;
pub const CAPTURE_LIMIT: u16 = 100;
pub const MAX_CAPTURE_RATE: usize = 3;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MapPoint {
    pub x: f64,
    pub z: f64,
}

impl MapPoint {
    pub fn new(x: f64, z: f64) -> Self {
        Self { x, z }
    }

    fn contains(self, vehicle: &VehicleForRules) -> bool {
        let dx = vehicle.x - self.x;
        let dz = vehicle.z - self.z;
        dx * dx + dz * dz <= CAPTURE_RADIUS_METRES * CAPTURE_RADIUS_METRES
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum VehicleKey {
    Human(u64),
    Bot(u64),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VehicleForRules {
    pub key: VehicleKey,
    pub team: Team,
    pub alive: bool,
    pub world_pose: bool,
    pub x: f64,
    pub z: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct BaseCaptureState {
    pub points: u16,
    pub time_left_seconds: f64,
    pub invaders: usize,
    pub stopped: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ThreatenedBase {
    pub base_team: Team,
    pub index: usize,
    pub point: MapPoint,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct CaptureUpdate {
    pub changed: bool,
    pub captured: Option<CapturedBase>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CapturedBase {
    pub base_team: Team,
    pub winner: Team,
}

#[derive(Clone, Debug)]
struct TeamCapture {
    bases: Vec<MapPoint>,
    state: BaseCaptureState,
    contributors: BTreeMap<VehicleKey, u16>,
    cursor: usize,
    threats: Vec<ThreatenedBase>,
}

impl TeamCapture {
    fn new(bases: Vec<MapPoint>) -> Self {
        Self {
            bases,
            state: BaseCaptureState::default(),
            contributors: BTreeMap::new(),
            cursor: 0,
            threats: Vec::new(),
        }
    }
}

/// Modern 0.9.22 standard-mode capture state.
#[derive(Clone, Debug)]
pub struct StandardRules {
    teams: [TeamCapture; 2],
}

impl StandardRules {
    pub fn new(team_one_bases: Vec<MapPoint>, team_two_bases: Vec<MapPoint>) -> Self {
        Self {
            teams: [
                TeamCapture::new(team_one_bases),
                TeamCapture::new(team_two_bases),
            ],
        }
    }

    pub fn state(&self, base_team: Team) -> BaseCaptureState {
        self.teams[team_index(base_team)].state
    }

    pub fn bases(&self, base_team: Team) -> &[MapPoint] {
        &self.teams[team_index(base_team)].bases
    }

    pub fn contributors(&self, base_team: Team) -> impl Iterator<Item = (VehicleKey, u16)> + '_ {
        self.teams[team_index(base_team)]
            .contributors
            .iter()
            .map(|(&key, &points)| (key, points))
    }

    pub fn threatened_bases(&self, base_team: Team) -> impl Iterator<Item = &ThreatenedBase> {
        self.teams[team_index(base_team)].threats.iter()
    }

    /// Drop only one vehicle's accumulated points. Callers use this after
    /// hull damage or a new module/crew damage transition, never for repair.
    pub fn drop_contribution(&mut self, vehicle: VehicleKey) -> u16 {
        let mut dropped = 0u16;
        for team in &mut self.teams {
            dropped = dropped.saturating_add(team.contributors.remove(&vehicle).unwrap_or(0));
            refresh_state(team);
            if team.contributors.is_empty() {
                team.cursor = 0;
            }
        }
        dropped
    }

    /// Apply the 1 Hz, 50 metre, maximum-three-points-per-second rule.
    pub fn update(
        &mut self,
        server_tick: u64,
        combat_live: bool,
        vehicles: &[VehicleForRules],
    ) -> CaptureUpdate {
        if !combat_live || server_tick % CAPTURE_TICK_HZ != 0 {
            return CaptureUpdate::default();
        }

        let live: Vec<_> = vehicles
            .iter()
            .filter(|vehicle| vehicle.alive && vehicle.world_pose)
            .collect();
        let mut update = CaptureUpdate::default();
        for base_team in [Team::One, Team::Two] {
            let invading_team = opposing_team(base_team);
            let team = &mut self.teams[team_index(base_team)];
            if team.bases.is_empty() {
                continue;
            }
            let previous = team.state;
            team.threats = team
                .bases
                .iter()
                .enumerate()
                .filter(|(_, base)| {
                    live.iter()
                        .any(|vehicle| vehicle.team == invading_team && base.contains(vehicle))
                })
                .map(|(index, &point)| ThreatenedBase {
                    base_team,
                    index,
                    point,
                })
                .collect();

            let invaders: BTreeSet<_> = live
                .iter()
                .filter(|vehicle| {
                    vehicle.team == invading_team
                        && team.bases.iter().any(|base| base.contains(vehicle))
                })
                .map(|vehicle| vehicle.key)
                .collect();
            team.contributors
                .retain(|vehicle, _| invaders.contains(vehicle));
            for &vehicle in &invaders {
                team.contributors.entry(vehicle).or_insert(0);
            }

            let points = contributor_total(&team.contributors);
            if !invaders.is_empty() && points < CAPTURE_LIMIT {
                let ordered: Vec<_> = invaders.into_iter().collect();
                let cursor = team.cursor % ordered.len();
                let budget = MAX_CAPTURE_RATE
                    .min(ordered.len())
                    .min(usize::from(CAPTURE_LIMIT - points));
                for offset in 0..budget {
                    let vehicle = ordered[(cursor + offset) % ordered.len()];
                    let points = team.contributors.entry(vehicle).or_insert(0);
                    *points = points.saturating_add(1);
                }
                team.cursor = (cursor + budget) % ordered.len();
            } else if invaders.is_empty() {
                team.cursor = 0;
            }
            refresh_state(team);
            update.changed |= team.state != previous;
            if team.state.points >= CAPTURE_LIMIT {
                update.captured = Some(CapturedBase {
                    base_team,
                    winner: invading_team,
                });
                break;
            }
        }
        update
    }
}

fn opposing_team(team: Team) -> Team {
    match team {
        Team::One => Team::Two,
        Team::Two => Team::One,
    }
}

fn team_index(team: Team) -> usize {
    match team {
        Team::One => 0,
        Team::Two => 1,
    }
}

fn contributor_total(contributors: &BTreeMap<VehicleKey, u16>) -> u16 {
    contributors
        .values()
        .copied()
        .fold(0u16, u16::saturating_add)
        .min(CAPTURE_LIMIT)
}

fn refresh_state(team: &mut TeamCapture) {
    team.state.points = contributor_total(&team.contributors);
    team.state.invaders = team.contributors.len();
    team.state.stopped = false;
    let rate = team.state.invaders.min(MAX_CAPTURE_RATE);
    team.state.time_left_seconds = if rate == 0 {
        0.0
    } else {
        f64::from(CAPTURE_LIMIT - team.state.points) / rate as f64
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    fn human(id: u64, team: Team, x: f64, z: f64) -> VehicleForRules {
        VehicleForRules {
            key: VehicleKey::Human(id),
            team,
            alive: true,
            world_pose: true,
            x,
            z,
        }
    }

    fn rules() -> StandardRules {
        StandardRules::new(
            vec![MapPoint::new(0.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        )
    }

    #[test]
    fn capture_uses_the_inclusive_fifty_metre_boundary() {
        let mut inside = rules();
        inside.update(0, true, &[human(2, Team::Two, 50.0, 0.0)]);
        assert_eq!(inside.state(Team::One).points, 1);

        let mut outside = rules();
        outside.update(0, true, &[human(2, Team::Two, 50.001, 0.0)]);
        assert_eq!(outside.state(Team::One).points, 0);
    }

    #[test]
    fn placeholder_world_poses_never_capture() {
        let mut vehicle = human(2, Team::Two, 0.0, 0.0);
        vehicle.world_pose = false;
        let mut rules = rules();
        assert!(!rules.update(0, true, &[vehicle]).changed);
        assert_eq!(rules.state(Team::One).invaders, 0);
    }

    #[test]
    fn own_team_presence_does_not_stop_standard_capture() {
        let mut rules = rules();
        let vehicles = [human(1, Team::One, 0.0, 0.0), human(2, Team::Two, 0.0, 0.0)];
        rules.update(0, true, &vehicles);
        rules.update(30, true, &vehicles);
        assert_eq!(
            rules.state(Team::One),
            BaseCaptureState {
                points: 2,
                time_left_seconds: 98.0,
                invaders: 1,
                stopped: false,
            }
        );
    }

    #[test]
    fn leaving_drops_only_that_vehicles_points() {
        let mut rules = rules();
        let mut vehicles = [human(2, Team::Two, 0.0, 0.0), human(3, Team::Two, 1.0, 0.0)];
        rules.update(0, true, &vehicles);
        rules.update(30, true, &vehicles);
        assert_eq!(rules.state(Team::One).points, 4);

        vehicles[0].x = 60.0;
        rules.update(60, true, &vehicles);
        assert_eq!(rules.state(Team::One).points, 3);
        assert_eq!(
            rules.contributors(Team::One).collect::<Vec<_>>(),
            vec![(VehicleKey::Human(3), 3)]
        );
    }

    #[test]
    fn damage_drops_only_the_damaged_contribution() {
        let mut rules = rules();
        let vehicles = [human(2, Team::Two, 0.0, 0.0), human(3, Team::Two, 1.0, 0.0)];
        for tick in [0, 30, 60, 90] {
            rules.update(tick, true, &vehicles);
        }
        assert_eq!(rules.state(Team::One).points, 8);
        assert_eq!(rules.drop_contribution(VehicleKey::Human(2)), 4);
        assert_eq!(rules.state(Team::One).points, 4);
    }

    #[test]
    fn points_are_distributed_round_robin_at_three_per_second() {
        let mut rules = rules();
        let vehicles = [
            human(2, Team::Two, 0.0, 0.0),
            human(3, Team::Two, 0.0, 1.0),
            human(4, Team::Two, 1.0, 0.0),
            human(5, Team::Two, 1.0, 1.0),
        ];
        rules.update(0, true, &vehicles);
        rules.update(30, true, &vehicles);
        let points: Vec<_> = rules.contributors(Team::One).collect();
        assert_eq!(points.iter().map(|(_, value)| *value).sum::<u16>(), 6);
        assert_eq!(
            points,
            vec![
                (VehicleKey::Human(2), 2),
                (VehicleKey::Human(3), 2),
                (VehicleKey::Human(4), 1),
                (VehicleKey::Human(5), 1),
            ]
        );
    }

    #[test]
    fn threatened_context_names_only_the_live_circle() {
        let mut rules = StandardRules::new(
            vec![MapPoint::new(0.0, 0.0), MapPoint::new(400.0, 0.0)],
            vec![MapPoint::new(500.0, 0.0)],
        );
        rules.update(0, true, &[human(2, Team::Two, 400.0, 0.0)]);
        let threats: Vec<_> = rules.threatened_bases(Team::One).cloned().collect();
        assert_eq!(threats.len(), 1);
        assert_eq!(threats[0].index, 1);
        assert_eq!(threats[0].point, MapPoint::new(400.0, 0.0));
    }

    #[test]
    fn reaching_one_hundred_finishes_for_the_invading_team() {
        let mut rules = rules();
        let vehicles = [
            human(2, Team::Two, 0.0, 0.0),
            human(3, Team::Two, 1.0, 0.0),
            human(4, Team::Two, 2.0, 0.0),
        ];
        let mut captured = None;
        for second in 0..34 {
            captured = rules
                .update(second * CAPTURE_TICK_HZ, true, &vehicles)
                .captured;
        }
        assert_eq!(rules.state(Team::One).points, 100);
        assert_eq!(
            captured,
            Some(CapturedBase {
                base_team: Team::One,
                winner: Team::Two,
            })
        );
    }
}
