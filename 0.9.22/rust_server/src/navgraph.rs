//! Immutable #1513 navigation graphs and bounded local route finding.
//!
//! Strategic planning continues to choose a map lane. This module owns the
//! lower-level question of how a bot reaches the next sparse lane anchor
//! without driving a straight chord through baked water, cliffs, or missing
//! terrain. The graph is loaded once per round from the same launcher-managed
//! overlay used by the pinned client.

use std::cmp::Ordering;
use std::collections::{BTreeMap, BinaryHeap};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde::Deserialize;
use serde_json::Value;
use thiserror::Error;

use crate::bot_sim::Vec3;

pub const NAVGRAPH_FORMAT: &str = "offline-lan-0922-navgraph";
pub const NAVGRAPH_MANIFEST_FORMAT: &str = "offline-lan-0922-navgraph-manifest";
pub const NAVGRAPH_VERSION: u64 = 2;
pub const NAVGRAPH_GAME_VERSION: &str = "0.9.22.0.1-cn-1513";
pub const FATAL_HAZARDS: u8 = 1 | 2;
pub const SHALLOW_WATER: u8 = 4;
pub const SHALLOW_WATER_PENALTY: f64 = 4.0;
pub const MAX_ASTAR_EXPANSIONS: usize = 4_096;
pub const MAX_LEGACY_TACTICAL_ALIGNMENT_DIAGONAL_RATIO: f64 = 0.60;

const SQRT_TWO: f64 = std::f64::consts::SQRT_2;
const HEURISTIC_WEIGHT: f64 = 1.70;
const MAX_GRAPH_CELLS: usize = 1_000_000;
const NEIGHBOURS: [(i32, i32, f64); 8] = [
    (-1, -1, SQRT_TWO),
    (0, -1, 1.0),
    (1, -1, SQRT_TWO),
    (-1, 0, 1.0),
    (1, 0, 1.0),
    (-1, 1, SQRT_TWO),
    (0, 1, 1.0),
    (1, 1, SQRT_TWO),
];

#[derive(Debug, Error)]
pub enum NavGraphError {
    #[error("navigation graph {path} is unavailable: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("navigation graph {path} is not valid JSON: {source}")]
    Json {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("navigation graph contract is invalid: {0}")]
    Invalid(&'static str),
    #[error("navigation graph map {received:?} does not match {expected:?}")]
    MapMismatch { expected: String, received: String },
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NavPose {
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NavWaypoint {
    pub x: f64,
    pub z: f64,
    pub hold: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct NavRoute {
    pub id: String,
    pub capacity: usize,
    pub risk: f64,
    pub waypoints: Vec<NavWaypoint>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NavTarget {
    pub point: Vec3,
    /// True only when the next adjacent edge chosen by A* enters a passable
    /// shallow-water cell. Reactive shortcuts never set this flag.
    pub controlled_shallow: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct NavGraph {
    map: String,
    width: usize,
    height: usize,
    origin: [f64; 2],
    bounds: [f64; 4],
    cell_size: f64,
    max_grade: f64,
    heights_mm: Vec<Option<i32>>,
    links: Vec<u8>,
    hazards: Vec<u8>,
    spawn_anchors: [[f64; 2]; 2],
    objective_bases: [[f64; 2]; 2],
    spawn_formations: [Vec<NavPose>; 2],
    routes: [Vec<NavRoute>; 2],
}

#[derive(Debug, Deserialize)]
struct RawGraph {
    format: String,
    version: u64,
    game_version: String,
    map: String,
    width: usize,
    height: usize,
    origin: Vec<f64>,
    bounds: Vec<f64>,
    cell_size: f64,
    heights_mm: Vec<Option<i32>>,
    links: Vec<u8>,
    hazards: Vec<u8>,
    spawn_anchors: Vec<Vec<f64>>,
    objective_bases: Vec<Vec<f64>>,
    spawn_formations: BTreeMap<String, Vec<Vec<f64>>>,
    routes: BTreeMap<String, Vec<RawRoute>>,
    bake: Value,
}

#[derive(Debug, Deserialize)]
struct RawRoute {
    id: String,
    capacity: Option<usize>,
    risk: Option<f64>,
    waypoints: Vec<Vec<Value>>,
}

#[derive(Debug, Deserialize)]
struct RawManifest {
    format: String,
    version: u64,
    game_version: String,
    maps: Vec<RawManifestEntry>,
}

#[derive(Debug, Deserialize)]
struct RawManifestEntry {
    map: String,
    file: String,
}

impl NavGraph {
    /// Resolve the launcher-installed graph directory from the game root.
    pub fn directory_for_game_root(game_root: &Path) -> PathBuf {
        game_root
            .join("mods")
            .join("configs")
            .join("offline_lan_0922")
            .join("navgraphs")
    }

    pub fn load_from_game_root(game_root: &Path, map_name: &str) -> Result<Self, NavGraphError> {
        Self::load_from_directory(&Self::directory_for_game_root(game_root), map_name)
    }

    pub fn load_from_directory(directory: &Path, map_name: &str) -> Result<Self, NavGraphError> {
        let expected = short_map_name(map_name)?;
        validate_manifest(directory, &expected)?;
        let path = directory.join(format!("{expected}.json"));
        let bytes = fs::read(&path).map_err(|source| NavGraphError::Io {
            path: path.clone(),
            source,
        })?;
        let raw: RawGraph =
            serde_json::from_slice(&bytes).map_err(|source| NavGraphError::Json {
                path: path.clone(),
                source,
            })?;
        Self::from_raw(raw, &expected)
    }

    fn from_raw(raw: RawGraph, expected: &str) -> Result<Self, NavGraphError> {
        if raw.format != NAVGRAPH_FORMAT {
            return Err(NavGraphError::Invalid("unsupported format"));
        }
        if raw.version != NAVGRAPH_VERSION {
            return Err(NavGraphError::Invalid("unsupported version"));
        }
        if raw.game_version != NAVGRAPH_GAME_VERSION {
            return Err(NavGraphError::Invalid("unsupported game version"));
        }
        let received = short_map_name(&raw.map)?;
        if received != expected {
            return Err(NavGraphError::MapMismatch {
                expected: expected.to_owned(),
                received,
            });
        }
        let cell_count = raw
            .width
            .checked_mul(raw.height)
            .filter(|count| raw.width > 0 && raw.height > 0 && *count <= MAX_GRAPH_CELLS)
            .ok_or(NavGraphError::Invalid("dimensions are outside bounds"))?;
        if raw.heights_mm.len() != cell_count
            || raw.links.len() != cell_count
            || raw.hazards.len() != cell_count
        {
            return Err(NavGraphError::Invalid("cell arrays are incomplete"));
        }
        if raw.hazards.iter().any(|value| value & !7 != 0) {
            return Err(NavGraphError::Invalid("hazard mask is unsupported"));
        }
        let origin = exact_finite_pair(&raw.origin, "origin")?;
        let bounds = exact_finite_quad(&raw.bounds, "bounds")?;
        if !raw.cell_size.is_finite()
            || !(1.0..=64.0).contains(&raw.cell_size)
            || bounds[0] >= bounds[2]
            || bounds[1] >= bounds[3]
        {
            return Err(NavGraphError::Invalid("geometry is invalid"));
        }
        let max_grade = raw
            .bake
            .as_object()
            .and_then(|value| value.get("max_grade"))
            .and_then(Value::as_f64)
            .filter(|value| value.is_finite() && (0.05..=1.0).contains(value))
            .ok_or(NavGraphError::Invalid("bake.max_grade is invalid"))?;
        if raw
            .bake
            .as_object()
            .and_then(|value| value.get("shallow_water_cost_multiplier"))
            .and_then(Value::as_f64)
            .is_some_and(|value| (value - SHALLOW_WATER_PENALTY).abs() > 1.0e-9)
        {
            return Err(NavGraphError::Invalid(
                "shallow-water cost multiplier is incompatible",
            ));
        }
        let spawn_anchors = exact_team_points(&raw.spawn_anchors, "spawn anchors")?;
        let objective_bases = exact_team_points(&raw.objective_bases, "objective bases")?;
        let spawn_formations = parse_spawn_formations(&raw.spawn_formations)?;
        let routes = parse_routes(&raw.routes)?;
        let contains = |x: f64, z: f64| {
            (bounds[0]..=bounds[2]).contains(&x) && (bounds[1]..=bounds[3]).contains(&z)
        };
        if spawn_anchors
            .iter()
            .chain(objective_bases.iter())
            .any(|point| !contains(point[0], point[1]))
            || spawn_formations
                .iter()
                .flatten()
                .any(|pose| !contains(pose.x, pose.z))
            || routes
                .iter()
                .flatten()
                .flat_map(|route| route.waypoints.iter())
                .any(|waypoint| !contains(waypoint.x, waypoint.z))
        {
            return Err(NavGraphError::Invalid(
                "navigation spatial metadata is outside bounds",
            ));
        }
        if routes.iter().enumerate().any(|(team, team_routes)| {
            team_routes.iter().any(|route| {
                let first = route
                    .waypoints
                    .first()
                    .expect("strict route parsing rejects empty routes");
                (first.x - spawn_anchors[team][0]).hypot(first.z - spawn_anchors[team][1]) > 1.0e-6
            })
        }) {
            return Err(NavGraphError::Invalid(
                "navigation route does not start at its team spawn anchor",
            ));
        }
        Ok(Self {
            map: received,
            width: raw.width,
            height: raw.height,
            origin,
            bounds,
            cell_size: raw.cell_size,
            max_grade,
            heights_mm: raw.heights_mm,
            links: raw.links,
            hazards: raw.hazards,
            spawn_anchors,
            objective_bases,
            spawn_formations,
            routes,
        })
    }

    pub fn map_name(&self) -> &str {
        &self.map
    }

    pub fn dimensions(&self) -> (usize, usize) {
        (self.width, self.height)
    }

    pub fn cell_size(&self) -> f64 {
        self.cell_size
    }

    pub fn bounds(&self) -> [f64; 4] {
        self.bounds
    }

    pub fn spawn_anchor(&self, team: u8) -> Option<[f64; 2]> {
        team_index(team).map(|index| self.spawn_anchors[index])
    }

    /// Resolve an external team convention against the graph's two spawn
    /// sides. Some #1513 arena exports number sides opposite to the LAN room;
    /// comparing canonical lane origins avoids hard-coding that inversion.
    pub fn nearest_spawn_team(&self, x: f64, z: f64) -> Option<u8> {
        if !x.is_finite() || !z.is_finite() {
            return None;
        }
        let first = (x - self.spawn_anchors[0][0]).hypot(z - self.spawn_anchors[0][1]);
        let second = (x - self.spawn_anchors[1][0]).hypot(z - self.spawn_anchors[1][1]);
        Some(if first <= second { 1 } else { 2 })
    }

    /// Map LAN teams to stock graph sides by evaluating both complete 2x2
    /// permutations. A close score or a distant winner is rejected rather
    /// than silently choosing a side from one approximate route annotation.
    pub fn resolve_team_mapping(
        &self,
        tactical_homes: [[f64; 2]; 2],
    ) -> Result<[u8; 2], NavGraphError> {
        if tactical_homes
            .into_iter()
            .flatten()
            .any(|coordinate| !coordinate.is_finite())
        {
            return Err(NavGraphError::Invalid("tactical home is invalid"));
        }
        if tactical_homes.iter().any(|home| {
            !(self.bounds[0]..=self.bounds[2]).contains(&home[0])
                || !(self.bounds[1]..=self.bounds[3]).contains(&home[1])
        }) {
            return Err(NavGraphError::Invalid("tactical home is outside bounds"));
        }
        let distance =
            |home: [f64; 2], point: [f64; 2]| (home[0] - point[0]).hypot(home[1] - point[1]);
        let score = |mapping: [usize; 2]| {
            mapping
                .into_iter()
                .enumerate()
                .map(|(team, graph_team)| {
                    distance(tactical_homes[team], self.objective_bases[graph_team])
                })
                .sum::<f64>()
        };
        let identity = score([0, 1]);
        let swapped = score([1, 0]);
        if (identity - swapped).abs() <= 1.0 {
            return Err(NavGraphError::Invalid("team mapping is ambiguous"));
        }
        let mapping = if identity < swapped { [0, 1] } else { [1, 0] };
        let alignment_limit = (self.bounds[2] - self.bounds[0])
            .hypot(self.bounds[3] - self.bounds[1])
            * MAX_LEGACY_TACTICAL_ALIGNMENT_DIAGONAL_RATIO;
        for (team, graph_team) in mapping.into_iter().enumerate() {
            if distance(tactical_homes[team], self.objective_bases[graph_team]) > alignment_limit
                || distance(tactical_homes[team], self.spawn_anchors[graph_team]) > alignment_limit
                || distance(
                    self.objective_bases[graph_team],
                    self.spawn_anchors[graph_team],
                ) > alignment_limit
            {
                return Err(NavGraphError::Invalid(
                    "legacy tactical catalog alignment exceeds map-relative tolerance",
                ));
            }
        }
        Ok([mapping[0] as u8 + 1, mapping[1] as u8 + 1])
    }

    pub fn objective_base(&self, team: u8) -> Option<[f64; 2]> {
        team_index(team).map(|index| self.objective_bases[index])
    }

    pub fn spawn_pose(&self, team: u8, slot: usize) -> Option<NavPose> {
        self.spawn_formations
            .get(team_index(team)?)?
            .get(slot)
            .copied()
    }

    pub fn routes(&self, team: u8) -> Option<&[NavRoute]> {
        self.routes.get(team_index(team)?).map(Vec::as_slice)
    }

    pub fn route(&self, team: u8, route_id: &str) -> Option<&NavRoute> {
        self.routes(team)?.iter().find(|route| route.id == route_id)
    }

    pub fn router(self) -> NavRouter {
        NavRouter::new(self)
    }

    fn index(&self, cell: Cell) -> Option<usize> {
        if cell.x < 0 || cell.z < 0 || cell.x >= self.width as i32 || cell.z >= self.height as i32 {
            return None;
        }
        Some(cell.z as usize * self.width + cell.x as usize)
    }

    fn cell_height(&self, cell: Cell) -> Option<f64> {
        self.heights_mm
            .get(self.index(cell)?)?
            .map(|value| f64::from(value) / 1_000.0)
    }

    fn hazard(&self, cell: Cell) -> Option<u8> {
        self.hazards.get(self.index(cell)?).copied()
    }

    fn cell_for(&self, point: Vec3) -> Cell {
        Cell {
            x: ((point.x - self.origin[0]) / self.cell_size + 0.5).floor() as i32,
            z: ((point.z - self.origin[1]) / self.cell_size + 0.5).floor() as i32,
        }
    }

    fn point_for(&self, cell: Cell) -> Option<Vec3> {
        Some(Vec3::new(
            self.origin[0] + f64::from(cell.x) * self.cell_size,
            self.cell_height(cell)?,
            self.origin[1] + f64::from(cell.z) * self.cell_size,
        ))
    }

    fn traversable(&self, cell: Cell) -> bool {
        self.cell_height(cell).is_some()
            && self
                .hazard(cell)
                .is_some_and(|hazard| hazard & FATAL_HAZARDS == 0)
    }

    fn nearest_traversable(&self, cell: Cell, max_radius: i32) -> Option<Cell> {
        if self.traversable(cell) {
            return Some(cell);
        }
        let mut best = None;
        let mut best_distance = i64::MAX;
        for radius in 1..=max_radius.max(0) {
            for z in cell.z - radius..=cell.z + radius {
                for x in cell.x - radius..=cell.x + radius {
                    if (x - cell.x).abs().max((z - cell.z).abs()) != radius {
                        continue;
                    }
                    let candidate = Cell { x, z };
                    if !self.traversable(candidate) {
                        continue;
                    }
                    let distance = i64::from(x - cell.x).pow(2) + i64::from(z - cell.z).pow(2);
                    if distance < best_distance {
                        best = Some(candidate);
                        best_distance = distance;
                    }
                }
            }
            if best.is_some() {
                return best;
            }
        }
        None
    }

    fn edge_height(&self, cell: Cell, next: Cell) -> Option<f64> {
        let dx = next.x - cell.x;
        let dz = next.z - cell.z;
        let direction = NEIGHBOURS
            .iter()
            .position(|(candidate_x, candidate_z, _)| *candidate_x == dx && *candidate_z == dz)?;
        let index = self.index(cell)?;
        if self.links[index] & (1 << direction) == 0 || !self.traversable(next) {
            return None;
        }
        let current_height = self.cell_height(cell)?;
        let next_height = self.cell_height(next)?;
        let run = self.cell_size * if dx != 0 && dz != 0 { SQRT_TWO } else { 1.0 };
        ((next_height - current_height).abs() / run.max(0.1) <= self.max_grade + 1.0e-9)
            .then_some(next_height)
    }

    fn segment_cells(&self, start: Vec3, end: Vec3) -> Option<Vec<Cell>> {
        let mut current = self.nearest_traversable(self.cell_for(start), 2)?;
        let target = self.cell_for(end);
        if !self.traversable(target) {
            return None;
        }
        let mut result = vec![current];
        let dx = (target.x - current.x).abs();
        let dz = (target.z - current.z).abs();
        let step_x = if current.x < target.x { 1 } else { -1 };
        let step_z = if current.z < target.z { 1 } else { -1 };
        let mut error = dx - dz;
        while current != target {
            let double_error = error * 2;
            if double_error > -dz {
                error -= dz;
                current.x += step_x;
            }
            if double_error < dx {
                error += dx;
                current.z += step_z;
            }
            result.push(current);
        }
        Some(result)
    }

    /// Prove a straight graph chord. The start cell is excluded from the
    /// hazard check so a vehicle already standing in a shallow ford can leave.
    pub fn segment_clear(&self, start: Vec3, end: Vec3, avoid_shallow: bool) -> bool {
        let Some(cells) = self.segment_cells(start, end) else {
            return false;
        };
        for cell in cells.iter().skip(1) {
            let Some(hazard) = self.hazard(*cell) else {
                return false;
            };
            if hazard & FATAL_HAZARDS != 0 || (avoid_shallow && hazard & SHALLOW_WATER != 0) {
                return false;
            }
        }
        cells
            .windows(2)
            .all(|pair| self.edge_height(pair[0], pair[1]).is_some())
    }

    /// Admit the exact segment a fixed simulation tick will commit.
    ///
    /// A native motion receipt proves the pre-turn travel heading only. The
    /// post-turn segment therefore still has to satisfy the baked navigation
    /// policy. A controlled ford exception is valid only for the adjacent A*
    /// edge that was selected by the router and only while the committed
    /// heading remains aligned with that edge.
    pub fn committed_segment_clear(
        &self,
        start: Vec3,
        end: Vec3,
        target: Option<NavTarget>,
    ) -> bool {
        if self.segment_clear(start, end, true) {
            return true;
        }
        let Some(target) = target.filter(|target| target.controlled_shallow) else {
            return false;
        };
        if !self.controlled_shallow_target_valid(start, target) {
            return false;
        }
        let planned_dx = target.point.x - start.x;
        let planned_dz = target.point.z - start.z;
        let committed_dx = end.x - start.x;
        let committed_dz = end.z - start.z;
        let planned_distance = planned_dx.hypot(planned_dz);
        let committed_distance = committed_dx.hypot(committed_dz);
        if planned_distance <= 1.0e-6
            || committed_distance <= 1.0e-6
            || committed_distance > planned_distance + 1.0e-6
        {
            return false;
        }
        let planned_yaw = planned_dx.atan2(planned_dz);
        let committed_yaw = committed_dx.atan2(committed_dz);
        let heading_error = ((committed_yaw - planned_yaw + std::f64::consts::PI)
            .rem_euclid(std::f64::consts::TAU))
            - std::f64::consts::PI;
        heading_error.abs() <= 0.20 && self.segment_clear(start, end, false)
    }

    /// Validate that a typed controlled-shallow target is the adjacent,
    /// passable A* edge entering a shallow cell from the current pose.
    pub fn controlled_shallow_target_valid(&self, start: Vec3, target: NavTarget) -> bool {
        if !target.controlled_shallow {
            return false;
        }
        self.segment_cells(start, target.point)
            .is_some_and(|cells| {
                cells.len() == 2
                    && self
                        .hazard(cells[1])
                        .is_some_and(|hazard| hazard & SHALLOW_WATER != 0)
                    && self.segment_clear(start, target.point, false)
            })
    }

    fn segment_has_hazard(&self, start: Vec3, end: Vec3, hazard_mask: u8) -> bool {
        self.segment_cells(start, end).is_some_and(|cells| {
            cells.iter().skip(1).any(|cell| {
                self.hazard(*cell)
                    .is_some_and(|hazard| hazard & hazard_mask != 0)
            })
        })
    }

    fn planned_next_segment_clear(&self, current: Vec3, path: &[Vec3], index: usize) -> bool {
        let Some(target) = path.get(index + 1).copied() else {
            return false;
        };
        if !self.segment_clear(current, target, false) {
            return false;
        }
        if !self.segment_has_hazard(current, target, SHALLOW_WATER) {
            return true;
        }
        self.segment_has_hazard(path[index], target, SHALLOW_WATER)
    }

    fn plan(&self, start: Vec3, goal: Vec3) -> Option<PlannedPath> {
        let start_cell = self.nearest_traversable(self.cell_for(start), 3)?;
        let goal_cell = self.nearest_traversable(self.cell_for(goal), 3)?;
        let mut frontier = BinaryHeap::new();
        frontier.push(HeapEntry::new(0.0, 0, start_cell, 0.0));
        let mut sequence = 0_u64;
        let mut came_from = BTreeMap::new();
        let mut costs = BTreeMap::from([(start_cell, 0.0)]);
        let mut reached = None;
        let mut closest = start_cell;
        let mut closest_distance = start_cell.distance(goal_cell);
        let mut expansions = 0_usize;
        while let Some(entry) = frontier.pop() {
            if entry.cost != costs.get(&entry.cell).copied().unwrap_or(f64::INFINITY) {
                continue;
            }
            expansions += 1;
            let distance = entry.cell.distance(goal_cell);
            if distance < closest_distance {
                closest = entry.cell;
                closest_distance = distance;
            }
            if entry.cell == goal_cell {
                reached = Some(entry.cell);
                break;
            }
            if expansions >= MAX_ASTAR_EXPANSIONS {
                break;
            }
            let current_height = self.cell_height(entry.cell)?;
            for (direction, (dx, dz, length_scale)) in NEIGHBOURS.iter().enumerate() {
                let next = Cell {
                    x: entry.cell.x + dx,
                    z: entry.cell.z + dz,
                };
                let Some(next_height) = self.edge_height(entry.cell, next) else {
                    continue;
                };
                let run = self.cell_size * length_scale;
                let delta = next_height - current_height;
                let slope_ratio = (delta.abs() / run.max(0.1)) / self.max_grade.max(0.05);
                let mut slope_cost = run * slope_ratio * slope_ratio * 6.0;
                if delta < 0.0 {
                    slope_cost *= 1.25;
                }
                let shallow_cost = if self
                    .hazard(next)
                    .is_some_and(|hazard| hazard & SHALLOW_WATER != 0)
                {
                    self.cell_size * SHALLOW_WATER_PENALTY
                } else {
                    0.0
                };
                let new_cost = entry.cost + run + slope_cost + shallow_cost;
                if costs.get(&next).is_none_or(|known| new_cost < *known) {
                    costs.insert(next, new_cost);
                    came_from.insert(next, entry.cell);
                    let heuristic = next.distance(goal_cell) * self.cell_size * HEURISTIC_WEIGHT;
                    sequence = sequence.saturating_add(1);
                    frontier.push(HeapEntry::new(
                        new_cost + heuristic + direction as f64 * 1.0e-12,
                        sequence,
                        next,
                        new_cost,
                    ));
                }
            }
        }
        let (end, complete) = match reached {
            Some(cell) => (cell, true),
            None if closest != start_cell => (closest, false),
            None => return None,
        };
        let mut cells = vec![end];
        while cells.last().copied()? != start_cell {
            cells.push(*came_from.get(cells.last()?)?);
        }
        cells.reverse();
        let mut points: Vec<_> = cells
            .into_iter()
            .map(|cell| self.point_for(cell))
            .collect::<Option<_>>()?;
        if complete && self.segment_clear(*points.last()?, goal, true) {
            points.push(goal);
        }
        let points = self.smooth(&points);
        Some(PlannedPath { points, complete })
    }

    fn smooth(&self, path: &[Vec3]) -> Vec<Vec3> {
        if path.len() < 3 {
            return path.to_vec();
        }
        let mut result = vec![path[0]];
        let mut index = 0;
        while index + 1 < path.len() {
            let mut furthest = (index + 6).min(path.len() - 1);
            while furthest > index + 1
                && (!shortcut_preserves_climb_approach(path, index, furthest)
                    || !self.segment_clear(path[index], path[furthest], true))
            {
                furthest -= 1;
            }
            result.push(path[furthest]);
            index = furthest;
        }
        result
    }

    fn safe_local_target(&self, current: Vec3, goal: Vec3, side: f64) -> Option<Vec3> {
        let start = self.nearest_traversable(self.cell_for(current), 2)?;
        let mut candidates = Vec::new();
        for radius in [1_i32, 2] {
            for (offset_x, offset_z, _) in NEIGHBOURS {
                let cell = Cell {
                    x: start.x + offset_x * radius,
                    z: start.z + offset_z * radius,
                };
                let Some(point) = self.point_for(cell) else {
                    continue;
                };
                if !self.segment_clear(current, point, true) {
                    continue;
                }
                let dx = point.x - current.x;
                let dz = point.z - current.z;
                let goal_dx = goal.x - current.x;
                let goal_dz = goal.z - current.z;
                let cross = goal_dx * dz - goal_dz * dx;
                let side_penalty = if cross * side < 0.0 { 0.25 } else { 0.0 };
                candidates.push((point.horizontal_distance(goal) + side_penalty, point));
            }
        }
        candidates.sort_by(|left, right| left.0.total_cmp(&right.0));
        candidates.first().map(|value| value.1)
    }
}

#[derive(Clone, Debug)]
pub struct NavRouter {
    graph: Arc<NavGraph>,
    bots: BTreeMap<u32, BotPathState>,
}

impl NavRouter {
    pub fn new(graph: NavGraph) -> Self {
        Self {
            graph: Arc::new(graph),
            bots: BTreeMap::new(),
        }
    }

    pub fn graph(&self) -> &NavGraph {
        &self.graph
    }

    pub fn next_target(
        &mut self,
        bot_id: u32,
        current: Vec3,
        goal: Vec3,
        route_id: &str,
        route_index: usize,
    ) -> NavTarget {
        if current.horizontal_distance(goal) <= 1.5 {
            self.bots.remove(&bot_id);
            return NavTarget {
                point: goal,
                controlled_shallow: false,
            };
        }
        let goal_cell = self.graph.cell_for(goal);
        let key = PathKey {
            route_id: route_id.to_owned(),
            route_index,
            goal_cell,
        };
        let stale = self.bots.get(&bot_id).is_none_or(|state| state.key != key);
        if stale {
            let planned = if self.graph.segment_clear(current, goal, true) {
                PlannedPath {
                    points: vec![current, goal],
                    complete: true,
                }
            } else {
                self.graph
                    .plan(current, goal)
                    .unwrap_or_else(|| PlannedPath {
                        points: Vec::new(),
                        complete: false,
                    })
            };
            self.bots.insert(
                bot_id,
                BotPathState {
                    key: key.clone(),
                    points: planned.points,
                    index: 0,
                    complete: planned.complete,
                },
            );
        }
        let reach_radius = (self.graph.cell_size * 0.55).clamp(1.5, 10.0);
        let mut retry_path = false;
        if let Some(state) = self.bots.get_mut(&bot_id) {
            if !state.points.is_empty() {
                while state.index + 1 < state.points.len()
                    && current.horizontal_distance(state.points[state.index]) < reach_radius
                    && self
                        .graph
                        .planned_next_segment_clear(current, &state.points, state.index)
                {
                    state.index += 1;
                }
                let mut lookahead = state.index;
                for candidate in state.index + 1..(state.index + 3).min(state.points.len()) {
                    if self
                        .graph
                        .segment_clear(current, state.points[candidate], true)
                    {
                        lookahead = candidate;
                    } else {
                        break;
                    }
                }
                state.index = lookahead;
                let selected = state.points[lookahead];
                let controlled_shallow =
                    self.graph
                        .segment_has_hazard(current, selected, SHALLOW_WATER);
                retry_path |= !state.complete
                    && lookahead + 1 == state.points.len()
                    && current.horizontal_distance(selected) < reach_radius
                    && current.horizontal_distance(goal) > reach_radius;
                if !retry_path {
                    return NavTarget {
                        point: selected,
                        controlled_shallow,
                    };
                }
            }
        }
        if retry_path {
            self.bots.remove(&bot_id);
            return self.next_target(bot_id, current, goal, route_id, route_index);
        }
        NavTarget {
            point: self
                .graph
                .safe_local_target(current, goal, if bot_id % 2 == 0 { -1.0 } else { 1.0 })
                .unwrap_or(current),
            controlled_shallow: false,
        }
    }

    pub fn retain_bots(&mut self, mut keep: impl FnMut(u32) -> bool) {
        self.bots.retain(|bot_id, _| keep(*bot_id));
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Cell {
    x: i32,
    z: i32,
}

impl Cell {
    fn distance(self, other: Self) -> f64 {
        f64::from(self.x - other.x).hypot(f64::from(self.z - other.z))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct PathKey {
    route_id: String,
    route_index: usize,
    goal_cell: Cell,
}

#[derive(Clone, Debug)]
struct BotPathState {
    key: PathKey,
    points: Vec<Vec3>,
    index: usize,
    complete: bool,
}

#[derive(Clone, Debug)]
struct PlannedPath {
    points: Vec<Vec3>,
    complete: bool,
}

#[derive(Clone, Copy, Debug)]
struct HeapEntry {
    priority: f64,
    sequence: u64,
    cell: Cell,
    cost: f64,
}

impl HeapEntry {
    fn new(priority: f64, sequence: u64, cell: Cell, cost: f64) -> Self {
        Self {
            priority,
            sequence,
            cell,
            cost,
        }
    }
}

impl PartialEq for HeapEntry {
    fn eq(&self, other: &Self) -> bool {
        self.priority == other.priority && self.sequence == other.sequence
    }
}

impl Eq for HeapEntry {}

impl PartialOrd for HeapEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for HeapEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .priority
            .total_cmp(&self.priority)
            .then_with(|| other.sequence.cmp(&self.sequence))
    }
}

fn validate_manifest(directory: &Path, map_name: &str) -> Result<(), NavGraphError> {
    let path = directory.join("manifest.json");
    let bytes = fs::read(&path).map_err(|source| NavGraphError::Io {
        path: path.clone(),
        source,
    })?;
    let manifest: RawManifest =
        serde_json::from_slice(&bytes).map_err(|source| NavGraphError::Json {
            path: path.clone(),
            source,
        })?;
    if manifest.format != NAVGRAPH_MANIFEST_FORMAT
        || manifest.version != NAVGRAPH_VERSION
        || manifest.game_version != NAVGRAPH_GAME_VERSION
    {
        return Err(NavGraphError::Invalid("manifest is incompatible"));
    }
    let mut selected = None;
    for entry in manifest.maps {
        if short_map_name(&entry.map)? == map_name {
            if selected.is_some() {
                return Err(NavGraphError::Invalid("manifest map is duplicated"));
            }
            selected = Some(entry);
        }
    }
    let selected = selected.ok_or(NavGraphError::Invalid("manifest map is missing"))?;
    if selected.file != format!("{map_name}.json")
        || Path::new(&selected.file)
            .file_name()
            .and_then(|value| value.to_str())
            != Some(selected.file.as_str())
    {
        return Err(NavGraphError::Invalid("manifest filename is invalid"));
    }
    Ok(())
}

fn short_map_name(value: &str) -> Result<String, NavGraphError> {
    let short = value
        .rsplit(|character| matches!(character, '/' | '\\'))
        .next()
        .unwrap_or("")
        .strip_suffix(".xml")
        .unwrap_or_else(|| {
            value
                .rsplit(|character| matches!(character, '/' | '\\'))
                .next()
                .unwrap_or("")
        });
    if short.is_empty()
        || short.len() > 64
        || !short.is_ascii()
        || short
            .chars()
            .any(|character| !(character.is_ascii_alphanumeric() || character == '_'))
    {
        return Err(NavGraphError::Invalid("map name is invalid"));
    }
    Ok(short.to_owned())
}

fn exact_finite_pair(values: &[f64], label: &'static str) -> Result<[f64; 2], NavGraphError> {
    if values.len() != 2 || !values.iter().all(|value| value.is_finite()) {
        return Err(NavGraphError::Invalid(label));
    }
    Ok([values[0], values[1]])
}

fn exact_finite_quad(values: &[f64], label: &'static str) -> Result<[f64; 4], NavGraphError> {
    if values.len() != 4 || !values.iter().all(|value| value.is_finite()) {
        return Err(NavGraphError::Invalid(label));
    }
    Ok([values[0], values[1], values[2], values[3]])
}

fn exact_team_points(
    values: &[Vec<f64>],
    label: &'static str,
) -> Result<[[f64; 2]; 2], NavGraphError> {
    if values.len() != 2 {
        return Err(NavGraphError::Invalid(label));
    }
    Ok([
        exact_finite_pair(&values[0], label)?,
        exact_finite_pair(&values[1], label)?,
    ])
}

fn parse_spawn_formations(
    raw: &BTreeMap<String, Vec<Vec<f64>>>,
) -> Result<[Vec<NavPose>; 2], NavGraphError> {
    if raw.len() != 2 {
        return Err(NavGraphError::Invalid("spawn formations are incomplete"));
    }
    let mut result = [Vec::new(), Vec::new()];
    for (index, key) in ["1", "2"].into_iter().enumerate() {
        let values = raw
            .get(key)
            .filter(|values| values.len() == 15)
            .ok_or(NavGraphError::Invalid("spawn formation has invalid size"))?;
        for value in values {
            if value.len() != 4 || !value.iter().all(|coordinate| coordinate.is_finite()) {
                return Err(NavGraphError::Invalid("spawn pose is invalid"));
            }
            result[index].push(NavPose {
                x: value[0],
                y: value[1],
                z: value[2],
                yaw: value[3],
            });
        }
    }
    for first_team in 0..2 {
        for first_slot in 0..15 {
            let first = result[first_team][first_slot];
            for second_team in first_team..2 {
                let start_slot = if second_team == first_team {
                    first_slot + 1
                } else {
                    0
                };
                for second_slot in start_slot..15 {
                    let second = result[second_team][second_slot];
                    if (first.x - second.x).hypot(first.z - second.z) < 9.0 {
                        return Err(NavGraphError::Invalid("spawn poses overlap"));
                    }
                }
            }
        }
    }
    Ok(result)
}

fn parse_routes(
    raw: &BTreeMap<String, Vec<RawRoute>>,
) -> Result<[Vec<NavRoute>; 2], NavGraphError> {
    if raw.len() != 2 {
        return Err(NavGraphError::Invalid("routes are incomplete"));
    }
    let mut result = [Vec::new(), Vec::new()];
    for (index, key) in ["1", "2"].into_iter().enumerate() {
        let values = raw
            .get(key)
            .filter(|values| !values.is_empty())
            .ok_or(NavGraphError::Invalid("team routes are missing"))?;
        let mut seen = BTreeMap::new();
        for value in values {
            if value.id.is_empty()
                || value.id.len() > 64
                || !value.id.is_ascii()
                || seen.insert(value.id.as_str(), ()).is_some()
                || !(2..=16).contains(&value.waypoints.len())
                || value
                    .capacity
                    .is_some_and(|capacity| !(1..=15).contains(&capacity))
                || value
                    .risk
                    .is_some_and(|risk| !risk.is_finite() || !(0.0..=1.0).contains(&risk))
            {
                return Err(NavGraphError::Invalid("route metadata is invalid"));
            }
            let mut waypoints = Vec::with_capacity(value.waypoints.len());
            for waypoint in &value.waypoints {
                if !(2..=3).contains(&waypoint.len()) {
                    return Err(NavGraphError::Invalid("route waypoint is invalid"));
                }
                let x = waypoint[0]
                    .as_f64()
                    .filter(|value| value.is_finite())
                    .ok_or(NavGraphError::Invalid("route waypoint is invalid"))?;
                let z = waypoint[1]
                    .as_f64()
                    .filter(|value| value.is_finite())
                    .ok_or(NavGraphError::Invalid("route waypoint is invalid"))?;
                let hold = match waypoint.get(2) {
                    None => false,
                    Some(Value::Bool(value)) => *value,
                    _ => return Err(NavGraphError::Invalid("route waypoint is invalid")),
                };
                waypoints.push(NavWaypoint { x, z, hold });
            }
            result[index].push(NavRoute {
                id: value.id.clone(),
                capacity: value.capacity.unwrap_or(1),
                risk: value.risk.unwrap_or(0.5),
                waypoints,
            });
        }
    }
    Ok(result)
}

fn shortcut_preserves_climb_approach(path: &[Vec3], start: usize, end: usize) -> bool {
    if end.saturating_sub(start) < 2 {
        return true;
    }
    for index in start + 1..end {
        let before = path[index - 1];
        let pivot = path[index];
        let after = path[index + 1];
        let out_dx = after.x - pivot.x;
        let out_dz = after.z - pivot.z;
        let run = out_dx.hypot(out_dz);
        if run <= 0.1 || (after.y - pivot.y) / run <= 0.10 {
            continue;
        }
        let in_dx = pivot.x - before.x;
        let in_dz = pivot.z - before.z;
        if in_dx.abs() + in_dz.abs() <= 0.1 {
            continue;
        }
        let turn = ((out_dx.atan2(out_dz) - in_dx.atan2(in_dz) + std::f64::consts::PI)
            .rem_euclid(std::f64::consts::TAU))
            - std::f64::consts::PI;
        if turn.abs() > 0.30 {
            return false;
        }
    }
    true
}

fn team_index(team: u8) -> Option<usize> {
    match team {
        1 => Some(0),
        2 => Some(1),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tactical_maps::tactical_map;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn linked_graph(width: usize, height: usize) -> NavGraph {
        let mut links = vec![0_u8; width * height];
        for z in 0..height as i32 {
            for x in 0..width as i32 {
                for (direction, (dx, dz, _)) in NEIGHBOURS.iter().enumerate() {
                    let nx = x + dx;
                    let nz = z + dz;
                    if nx >= 0 && nz >= 0 && nx < width as i32 && nz < height as i32 {
                        links[z as usize * width + x as usize] |= 1 << direction;
                    }
                }
            }
        }
        let poses = (0..15)
            .map(|slot| NavPose {
                x: slot as f64 * 10.0,
                y: 0.0,
                z: 0.0,
                yaw: 0.0,
            })
            .collect::<Vec<_>>();
        NavGraph {
            map: "test_map".to_owned(),
            width,
            height,
            origin: [10.0, 20.0],
            bounds: [8.0, 18.0, 40.0, 50.0],
            cell_size: 4.0,
            max_grade: 0.38,
            heights_mm: vec![Some(0); width * height],
            links,
            hazards: vec![0; width * height],
            spawn_anchors: [[10.0, 20.0], [30.0, 40.0]],
            objective_bases: [[30.0, 40.0], [10.0, 20.0]],
            spawn_formations: [
                poses.clone(),
                poses
                    .iter()
                    .map(|pose| NavPose { z: 100.0, ..*pose })
                    .collect(),
            ],
            routes: [
                vec![NavRoute {
                    id: "lane".to_owned(),
                    capacity: 15,
                    risk: 0.5,
                    waypoints: vec![
                        NavWaypoint {
                            x: 10.0,
                            z: 20.0,
                            hold: false,
                        },
                        NavWaypoint {
                            x: 30.0,
                            z: 40.0,
                            hold: false,
                        },
                    ],
                }],
                vec![NavRoute {
                    id: "lane".to_owned(),
                    capacity: 15,
                    risk: 0.5,
                    waypoints: vec![
                        NavWaypoint {
                            x: 30.0,
                            z: 40.0,
                            hold: false,
                        },
                        NavWaypoint {
                            x: 10.0,
                            z: 20.0,
                            hold: false,
                        },
                    ],
                }],
            ],
        }
    }

    #[test]
    fn astar_prefers_dry_route_over_short_shallow_crossing() {
        let mut graph = linked_graph(5, 3);
        graph.hazards[1..4].fill(SHALLOW_WATER);
        let planned = graph
            .plan(Vec3::new(10.0, 0.0, 20.0), Vec3::new(26.0, 0.0, 20.0))
            .unwrap();
        assert!(planned.complete);
        assert!(planned.points.iter().any(|point| point.z > 20.0));
        assert!(planned
            .points
            .windows(2)
            .all(|pair| graph.segment_clear(pair[0], pair[1], true)));
    }

    #[test]
    fn shallow_ford_remains_a_fallback_when_no_dry_route_exists() {
        let mut graph = linked_graph(5, 1);
        graph.hazards[2] = SHALLOW_WATER;
        let planned = graph
            .plan(Vec3::new(10.0, 0.0, 20.0), Vec3::new(26.0, 0.0, 20.0))
            .unwrap();
        assert!(planned.complete);
        assert!(planned
            .points
            .iter()
            .any(|point| graph.hazard(graph.cell_for(*point)) == Some(SHALLOW_WATER)));
    }

    #[test]
    fn router_marks_only_the_adjacent_astar_ford_step_as_controlled_shallow() {
        let mut graph = linked_graph(5, 1);
        graph.hazards[2] = SHALLOW_WATER;
        let mut router = graph.router();
        let goal = Vec3::new(26.0, 0.0, 20.0);

        let dry_approach = router.next_target(1, Vec3::new(10.0, 0.0, 20.0), goal, "lane", 0);
        assert!(!dry_approach.controlled_shallow);
        assert_eq!(dry_approach.point, Vec3::new(14.0, 0.0, 20.0));

        let ford = router.next_target(1, dry_approach.point, goal, "lane", 0);
        assert!(ford.controlled_shallow);
        assert_eq!(ford.point, Vec3::new(18.0, 0.0, 20.0));

        let leave = router.next_target(1, ford.point, goal, "lane", 0);
        assert!(!leave.controlled_shallow);
        assert_eq!(leave.point, goal);
    }

    #[test]
    fn post_turn_travel_yaw_cannot_enter_unplanned_shallow() {
        let mut graph = linked_graph(5, 1);
        graph.hazards[2] = SHALLOW_WATER;
        let start = Vec3::new(14.0, 0.0, 20.0);
        let committed = Vec3::new(18.0, 0.0, 21.0);
        let controlled = NavTarget {
            point: Vec3::new(18.0, 0.0, 20.0),
            controlled_shallow: true,
        };

        assert!(!graph.committed_segment_clear(start, committed, None));
        assert!(!graph.committed_segment_clear(start, committed, Some(controlled)));
    }

    #[test]
    fn post_turn_travel_yaw_allows_only_its_controlled_shallow_step() {
        let mut graph = linked_graph(5, 1);
        graph.hazards[2] = SHALLOW_WATER;
        let start = Vec3::new(14.0, 0.0, 20.0);
        let committed = Vec3::new(16.5, 0.0, 20.0);
        let controlled = NavTarget {
            point: Vec3::new(18.0, 0.0, 20.0),
            controlled_shallow: true,
        };

        assert!(!graph.committed_segment_clear(start, committed, None));
        assert!(graph.committed_segment_clear(start, committed, Some(controlled)));
        assert!(!graph.committed_segment_clear(
            start,
            Vec3::new(22.0, 0.0, 20.0),
            Some(controlled),
        ));
        assert!(!graph.committed_segment_clear(
            start,
            committed,
            Some(NavTarget {
                point: Vec3::new(14.0, 0.0, 20.0),
                controlled_shallow: true,
            }),
        ));
    }

    #[test]
    fn direct_and_local_guards_allow_leaving_but_never_enter_shallow_water() {
        let mut graph = linked_graph(5, 3);
        graph.hazards[0] = SHALLOW_WATER;
        assert!(graph.segment_clear(Vec3::new(10.0, 0.0, 20.0), Vec3::new(14.0, 0.0, 20.0), true));
        graph.hazards[1] = SHALLOW_WATER;
        assert!(!graph.segment_clear(Vec3::new(10.0, 0.0, 20.0), Vec3::new(18.0, 0.0, 20.0), true));
        let local = graph
            .safe_local_target(Vec3::new(10.0, 0.0, 20.0), Vec3::new(26.0, 0.0, 20.0), 1.0)
            .unwrap();
        assert!(graph.segment_clear(Vec3::new(10.0, 0.0, 20.0), local, true));
    }

    #[test]
    fn smoothing_does_not_reintroduce_a_shallow_chord() {
        let mut graph = linked_graph(5, 3);
        graph.hazards[1..4].fill(SHALLOW_WATER);
        let original = [
            Vec3::new(10.0, 0.0, 20.0),
            Vec3::new(10.0, 0.0, 24.0),
            Vec3::new(26.0, 0.0, 24.0),
            Vec3::new(26.0, 0.0, 20.0),
        ];
        let smoothed = graph.smooth(&original);
        assert!(smoothed.len() >= 3);
        assert!(smoothed
            .windows(2)
            .all(|pair| graph.segment_clear(pair[0], pair[1], true)));
    }

    #[test]
    fn invalid_graph_fails_closed() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "offline-rust-navgraph-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&directory).unwrap();
        fs::write(
            directory.join("manifest.json"),
            r#"{"format":"offline-lan-0922-navgraph-manifest","version":2,"game_version":"0.9.22.0.1-cn-1513","maps":[{"map":"01_karelia","file":"01_karelia.json"}]}"#,
        )
        .unwrap();
        fs::write(directory.join("01_karelia.json"), r#"{"format":"wrong"}"#).unwrap();
        assert!(NavGraph::load_from_directory(&directory, "01_karelia").is_err());
        fs::remove_file(directory.join("01_karelia.json")).unwrap();
        fs::remove_file(directory.join("manifest.json")).unwrap();
        fs::remove_dir(directory).unwrap();
    }

    #[test]
    fn shipped_karelia_graph_loads_and_routes_between_baked_cells() {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR")).join("../navgraphs");
        let graph = NavGraph::load_from_directory(&directory, "01_karelia").unwrap();
        assert_eq!(graph.map_name(), "01_karelia");
        assert_eq!(graph.dimensions(), (251, 250));
        assert_eq!(graph.spawn_pose(1, 0).unwrap().x, 382.0);
        let route = graph.route(1, "middle_road").unwrap();
        let start = route.waypoints[0];
        let goal = route.waypoints[1];
        let path = graph
            .plan(
                Vec3::new(start.x, 0.0, start.z),
                Vec3::new(goal.x, 0.0, goal.z),
            )
            .unwrap();
        assert!(!path.points.is_empty());
    }

    #[test]
    fn every_shipped_manifest_graph_passes_the_rust_contract() {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR")).join("../navgraphs");
        let manifest: RawManifest =
            serde_json::from_slice(&fs::read(directory.join("manifest.json")).unwrap()).unwrap();
        assert_eq!(manifest.maps.len(), 41);
        let mut worst_alignment_ratio = 0.0_f64;
        let mut worst_alignment_map = String::new();
        let mut minimum_score_margin = f64::INFINITY;
        let mut minimum_margin_map = String::new();
        for entry in manifest.maps {
            let graph = NavGraph::load_from_directory(&directory, &entry.map).unwrap();
            let tactical = tactical_map(&entry.map).unwrap();
            let homes = [
                [tactical.bases[0].x, tactical.bases[0].z],
                [tactical.bases[1].x, tactical.bases[1].z],
            ];
            let mapping = graph.resolve_team_mapping(homes).unwrap();
            assert_ne!(mapping[0], mapping[1]);
            let score = |candidate: [u8; 2]| {
                candidate
                    .into_iter()
                    .enumerate()
                    .map(|(server_index, graph_team)| {
                        let objective = graph.objective_base(graph_team).unwrap();
                        (homes[server_index][0] - objective[0])
                            .hypot(homes[server_index][1] - objective[1])
                    })
                    .sum::<f64>()
            };
            let margin = (score([1, 2]) - score([2, 1])).abs();
            if margin < minimum_score_margin {
                minimum_score_margin = margin;
                minimum_margin_map = graph.map_name().to_owned();
            }
            let bounds = graph.bounds();
            let diagonal = (bounds[2] - bounds[0]).hypot(bounds[3] - bounds[1]);
            for server_team in [1_u8, 2] {
                let graph_team = mapping[usize::from(server_team - 1)];
                let own = graph.spawn_anchor(graph_team).unwrap();
                let objective = graph.objective_base(graph_team).unwrap();
                for distance in [
                    (homes[usize::from(server_team - 1)][0] - objective[0])
                        .hypot(homes[usize::from(server_team - 1)][1] - objective[1]),
                    (homes[usize::from(server_team - 1)][0] - own[0])
                        .hypot(homes[usize::from(server_team - 1)][1] - own[1]),
                    (objective[0] - own[0]).hypot(objective[1] - own[1]),
                ] {
                    let ratio = distance / diagonal;
                    if ratio > worst_alignment_ratio {
                        worst_alignment_ratio = ratio;
                        worst_alignment_map = graph.map_name().to_owned();
                    }
                }
            }
        }
        eprintln!(
            "shipped navgraph team mapping: worst alignment ratio={worst_alignment_ratio:.6} ({worst_alignment_map}), minimum score margin={minimum_score_margin:.3}m ({minimum_margin_map})"
        );
        assert!(
            worst_alignment_ratio <= MAX_LEGACY_TACTICAL_ALIGNMENT_DIAGONAL_RATIO,
            "worst legacy tactical alignment ratio {worst_alignment_ratio} exceeds the map-relative tolerance",
        );
        assert!(
            minimum_score_margin > 1.0,
            "minimum team-mapping score margin {minimum_score_margin} is ambiguous",
        );
    }
}
