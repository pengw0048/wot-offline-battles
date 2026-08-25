//! Pure-data tactical planner for the Rust LAN authority.
//!
//! This is the Rust authority's stateful tactical planner. It preserves the
//! applicable behavior of the retired `server_bot_ai.BotPlanner`; socket
//! handling, native collision queries, and client-side driving deliberately
//! stay outside this module. The existing planner manifest/order boundary is
//! retained while contacts, bots, routes, leases, and orders are strongly typed
//! internally.
//!
//! Coverage matrix:
//! - contact ingestion, identity validation, negative observations, and TTL;
//! - damage reactions, target leases, focus caps, and local firing-lane gates;
//! - map-aware opening routes, route progress, pressure rebalance, and SPG anchors;
//! - stable base-defense responders, bounded capture squads, and base screening;
//! - cover affordances and approach/hold/peek/return phases;
//! - shell selection, deterministic personality, order signatures, and revision.
//!
//! Server damage accounting and native live-pose overlay are integration-layer
//! responsibilities and are not part of the tactical planner contract.

use serde::Serialize;
use serde_json::{json, Map, Value};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

use crate::tactical_maps::{
    match_route, routes_for, tactical_map, MatchedTacticalRoute, RoleWeights,
};

pub const CONTACT_TTL_SECONDS: f64 = 8.0;
pub const MAX_CONTACTS_PER_TEAM: usize = 32;
pub const COVER_TTL_SECONDS: f64 = 8.0;
pub const MAX_COVER_REPORTS: usize = 16;
pub const MAX_COVER_CANDIDATES: usize = 12;
pub const TARGET_LEASE_SECONDS: f64 = 2.0;
pub const TARGET_SWITCH_MARGIN: f64 = 3.0;
pub const CLOSE_THREAT_DISTANCE: f64 = 50.0;
pub const CLOSE_THREAT_SCORE_BONUS: f64 = 100.0;
pub const CLOSE_THREAT_FOCUS_LIMIT: usize = 4;
pub const ROUTE_REBALANCE_SECONDS: f64 = 4.0;
pub const ROUTE_LEASE_SECONDS: f64 = 6.0;
pub const MAX_BASE_DEFENDERS: usize = 3;
pub const MAX_BASE_CAPTURERS: usize = 3;
pub const CAPTURE_STAGING_RADIUS: f64 = 30.0;
pub const MIN_ROUTE_CLASS_AFFINITY: f64 = 0.20;
pub const RECENT_HIT_SECONDS: f64 = 6.0;
pub const RECENT_ATTACKER_SCORE_BONUS: f64 = 140.0;
pub const DISCOVERED_ARTILLERY_PRIORITY_BONUS: f64 = 48.0;
pub const LOW_HEALTH_BASE_FRACTION: f64 = 0.18;
const CLASS_ROUTE_AFFINITY_WEIGHT: f64 = 42.0;
const ARTILLERY_ROUTE_REPEAT_PENALTY: f64 = 120.0;
const CROSSFIRE_MIN_ANGLE: f64 = 55.0_f64.to_radians();
const CROSSFIRE_MAX_DISTANCE: f64 = 360.0;

fn finite(value: f64, default: f64) -> f64 {
    if value.is_finite() {
        value
    } else {
        default
    }
}

fn number(value: Option<&Value>, default: f64) -> f64 {
    match value {
        Some(Value::Number(value)) => finite(value.as_f64().unwrap_or(default), default),
        Some(Value::String(value)) => value
            .parse::<f64>()
            .ok()
            .map(|value| finite(value, default))
            .unwrap_or(default),
        Some(Value::Bool(value)) => i32::from(*value) as f64,
        _ => default,
    }
}

fn integer(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(value)) => value
            .as_i64()
            .or_else(|| value.as_f64().map(|value| value as i64))
            .unwrap_or(default),
        Some(Value::String(value)) => value
            .parse::<i64>()
            .or_else(|_| value.parse::<f64>().map(|value| value as i64))
            .unwrap_or(default),
        Some(Value::Bool(value)) => i64::from(*value),
        _ => default,
    }
}

fn text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        Some(Value::Bool(value)) => value.to_string(),
        _ => String::new(),
    }
}

fn boolean(value: Option<&Value>, default: bool) -> bool {
    match value {
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_i64().map(|value| value != 0).unwrap_or(default),
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Null) | None => default,
        Some(_) => true,
    }
}

fn strict_bool(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_f64() == Some(1.0),
        _ => false,
    }
}

fn order_signature(order: &Value) -> Value {
    let mut signature = order.clone();
    if let Some(raw) = signature.as_object_mut() {
        if raw.get("target_id").is_some_and(|value| !value.is_null()) {
            // The Rust authority overlays the target's current pose after the
            // strategic order is parsed. A moving target is not a new plan,
            // including while an SPG is still waiting for permission to fire.
            raw.remove("aim_position");
            raw.remove("face_position");
            if raw.get("combat_mode").and_then(Value::as_str) == Some("advance_contact") {
                raw.remove("move_position");
            }
        }
    }
    signature
}

fn round_to(value: f64, digits: i32) -> f64 {
    let scale = 10_f64.powi(digits);
    (value * scale).round_ties_even() / scale
}

fn cmp_f64(left: f64, right: f64) -> Ordering {
    left.partial_cmp(&right).unwrap_or(Ordering::Equal)
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize)]
pub struct Point {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl Point {
    fn loose(value: Option<&Value>) -> Self {
        let raw = value.and_then(Value::as_object);
        Self {
            x: round_to(
                number(raw.and_then(|raw| raw.get("x")), 0.0).clamp(-2000.0, 2000.0),
                3,
            ),
            y: round_to(
                number(raw.and_then(|raw| raw.get("y")), 0.0).clamp(-1000.0, 1000.0),
                3,
            ),
            z: round_to(
                number(raw.and_then(|raw| raw.get("z")), 0.0).clamp(-2000.0, 2000.0),
                3,
            ),
        }
    }

    fn cover(value: Option<&Value>) -> Option<Self> {
        let values = match value? {
            Value::Array(values) if values.len() >= 3 => {
                [values.get(0), values.get(1), values.get(2)]
            }
            Value::Object(values)
                if values.contains_key("x")
                    && values.contains_key("y")
                    && values.contains_key("z") =>
            {
                [values.get("x"), values.get("y"), values.get("z")]
            }
            _ => return None,
        };
        let mut parsed = [0.0; 3];
        for (index, value) in values.into_iter().enumerate() {
            let value = match value {
                Some(Value::Number(value)) => value.as_f64()?,
                Some(Value::String(value)) => value.parse::<f64>().ok()?,
                _ => return None,
            };
            if !value.is_finite() {
                return None;
            }
            parsed[index] = round_to(value, 3);
        }
        Some(Self {
            x: parsed[0],
            y: parsed[1],
            z: parsed[2],
        })
    }

    fn distance_xz(self, other: Self) -> f64 {
        (self.x - other.x).hypot(self.z - other.z)
    }

    fn average(values: &[Self]) -> Self {
        let count = values.len().max(1) as f64;
        Self {
            x: round_to(values.iter().map(|value| value.x).sum::<f64>() / count, 3),
            y: round_to(values.iter().map(|value| value.y).sum::<f64>() / count, 3),
            z: round_to(values.iter().map(|value| value.z).sum::<f64>() / count, 3),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TargetKind {
    Human,
    Bot,
}

impl Ord for TargetKind {
    fn cmp(&self, other: &Self) -> Ordering {
        self.as_str().cmp(other.as_str())
    }
}

impl PartialOrd for TargetKind {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl TargetKind {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "human" | "player" => Some(Self::Human),
            "bot" => Some(Self::Bot),
            _ => None,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Human => "human",
            Self::Bot => "bot",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct TargetKey {
    pub kind: TargetKind,
    pub id: i64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct KnownTarget {
    pub kind: TargetKind,
    pub team: i64,
    pub alive: bool,
}

pub type KnownTargets = BTreeMap<TargetKey, KnownTarget>;

#[derive(Clone, Debug, PartialEq)]
pub struct KnownBot {
    pub team: i64,
    pub alive: bool,
    pub x: f64,
    pub z: f64,
}

pub type KnownBots = BTreeMap<i64, KnownBot>;

#[derive(Clone, Debug, PartialEq)]
struct Shell {
    source_order: usize,
    index: usize,
    kind: String,
    penetration: f64,
    damage: f64,
}

#[derive(Clone, Debug, PartialEq)]
struct Profile {
    raw: Value,
    class_tag: String,
    speed: f64,
    dominant_role: String,
    desired_range: f64,
    fire_range: f64,
    armor: f64,
    roles: BTreeMap<String, f64>,
    shells: Vec<Shell>,
}

impl Profile {
    fn parse(value: Option<&Value>) -> Self {
        let raw = value
            .filter(|value| value.is_object())
            .cloned()
            .unwrap_or_else(|| json!({}));
        let object = raw.as_object().expect("profile was normalized to object");
        let roles = object
            .get("roles")
            .and_then(Value::as_object)
            .map(|roles| {
                roles
                    .iter()
                    .map(|(key, value)| (key.clone(), number(Some(value), 0.0)))
                    .collect()
            })
            .unwrap_or_default();
        let shells = object
            .get("shells")
            .and_then(Value::as_array)
            .map(|shells| {
                shells
                    .iter()
                    .enumerate()
                    .filter_map(|(source_order, shell)| {
                        let shell = shell.as_object()?;
                        Some(Shell {
                            source_order,
                            index: integer(shell.get("index"), 0).max(0) as usize,
                            kind: text(shell.get("kind")).to_lowercase(),
                            penetration: number(shell.get("penetration"), 0.0).max(0.0),
                            damage: number(shell.get("damage"), 0.0).max(0.0),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Self {
            class_tag: text(object.get("class_tag")),
            speed: number(object.get("speed"), 0.0),
            dominant_role: {
                let value = text(object.get("dominant_role"));
                if value.is_empty() {
                    "support".to_owned()
                } else {
                    value
                }
            },
            desired_range: number(object.get("desired_range"), 180.0),
            fire_range: number(object.get("fire_range"), 500.0),
            armor: number(object.get("armor"), 0.0),
            raw,
            roles,
            shells,
        }
    }

    fn role(&self, name: &str) -> f64 {
        self.roles.get(name).copied().unwrap_or(0.0)
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
struct BotState {
    x: f64,
    y: f64,
    z: f64,
    yaw: f64,
    health: f64,
    max_health: f64,
    world_pose: bool,
    destroyed: BTreeSet<String>,
    shell_index: usize,
    ammo_remaining: Option<Vec<i64>>,
}

impl BotState {
    fn point(&self) -> Point {
        Point {
            x: round_to(self.x.clamp(-2000.0, 2000.0), 3),
            y: round_to(self.y.clamp(-1000.0, 1000.0), 3),
            z: round_to(self.z.clamp(-2000.0, 2000.0), 3),
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
struct Route {
    id: String,
    waypoints: Vec<Point>,
    capacity: Option<usize>,
    risk: f64,
    role_weights: BTreeMap<String, f64>,
    class_weights: BTreeMap<String, f64>,
    map_name: Option<&'static str>,
    capture_target: Option<Point>,
}

impl Route {
    fn parse(value: Option<&Value>, team: i64) -> Self {
        let Some(raw) = value.and_then(Value::as_object) else {
            return Self::default();
        };
        let waypoints = raw
            .get("waypoints")
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .map(|value| Point::loose(Some(value)))
                    .collect()
            })
            .unwrap_or_default();
        let mut result = Self {
            id: text(raw.get("id")),
            waypoints,
            capacity: raw
                .contains_key("capacity")
                .then(|| integer(raw.get("capacity"), 1).clamp(1, 15) as usize),
            risk: number(raw.get("risk"), 0.5).clamp(0.0, 1.0),
            role_weights: Self::parse_weights(raw.get("role_weights")),
            class_weights: Self::parse_weights(raw.get("class_weights")),
            map_name: None,
            capture_target: None,
        };
        let points: Vec<_> = result
            .waypoints
            .iter()
            .map(|point| (point.x, point.z))
            .collect();
        if let Some(context) = u8::try_from(team)
            .ok()
            .and_then(|team| match_route(team, &result.id, &points))
        {
            result.apply_tactical(context);
        }
        result
    }

    fn parse_weights(value: Option<&Value>) -> BTreeMap<String, f64> {
        value
            .and_then(Value::as_object)
            .map(|values| {
                values
                    .iter()
                    .take(8)
                    .map(|(name, value)| (name.clone(), number(Some(value), 0.0).clamp(0.0, 1.0)))
                    .collect()
            })
            .unwrap_or_default()
    }

    fn role_weights(weights: RoleWeights) -> BTreeMap<String, f64> {
        BTreeMap::from([
            ("brawler".to_owned(), weights.brawler),
            ("support".to_owned(), weights.support),
            ("flanker".to_owned(), weights.flanker),
            ("sniper".to_owned(), weights.sniper),
            ("scout".to_owned(), weights.scout),
            ("artillery".to_owned(), weights.artillery),
        ])
    }

    fn from_tactical(context: MatchedTacticalRoute) -> Self {
        let mut result = Self {
            id: context.route.id.to_owned(),
            waypoints: context
                .route
                .waypoints
                .iter()
                .map(|point| Point {
                    x: point.x,
                    y: 0.0,
                    z: point.z,
                })
                .collect(),
            ..Self::default()
        };
        result.apply_tactical(context);
        result
    }

    fn apply_tactical(&mut self, context: MatchedTacticalRoute) {
        self.capacity = Some(context.route.capacity);
        self.risk = context.route.risk;
        self.role_weights = Self::role_weights(context.route.role_weights);
        self.class_weights = ["heavyTank", "mediumTank", "lightTank", "AT-SPG", "SPG"]
            .into_iter()
            .filter_map(|class_tag| {
                context
                    .class_affinity(class_tag)
                    .map(|weight| (class_tag.to_owned(), weight))
            })
            .collect();
        self.map_name = Some(context.map.name);
        let base = context.enemy_base();
        self.capture_target = Some(Point {
            x: base.x,
            y: 0.0,
            z: base.z,
        });
    }

    fn role_affinity(&self, profile: &Profile) -> f64 {
        profile
            .roles
            .iter()
            .map(|(name, value)| value * self.role_weights.get(name).copied().unwrap_or(0.0))
            .sum()
    }

    fn class_affinity(&self, class_tag: &str) -> f64 {
        self.class_weights.get(class_tag).copied().unwrap_or(0.5)
    }
}

#[derive(Clone, Debug, PartialEq)]
struct Bot {
    id: i64,
    team: i64,
    slot: i64,
    profile: Profile,
    route: Route,
    state: BotState,
}

#[derive(Clone, Debug, PartialEq)]
struct Contact {
    id: i64,
    target_kind: TargetKind,
    team: i64,
    visible: bool,
    last_seen: f64,
    position: Point,
    health: i64,
    max_health: i64,
    class_tag: String,
    armor: f64,
    shootable_by_bot_ids: BTreeSet<i64>,
}

impl Contact {
    fn key(&self) -> TargetKey {
        TargetKey {
            kind: self.target_kind,
            id: self.id,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
struct CoverCandidate {
    id: String,
    position: Point,
    peek_position: Option<Point>,
    travel_distance: f64,
    route_alignment: f64,
    enemy_occlusion: f64,
    exposure: f64,
    slope: f64,
    water: f64,
    ally_congestion: f64,
    peek_feasible: bool,
    escape_feasible: bool,
}

impl CoverCandidate {
    fn normalize(value: &Value) -> Option<Self> {
        let raw = value.as_object()?;
        let id: String = text(raw.get("id"))
            .chars()
            .filter(char::is_ascii)
            .take(80)
            .collect();
        let position = Point::cover(raw.get("position"))?;
        let peek_position = Point::cover(raw.get("peek_position"));
        let peek_feasible = strict_bool(raw.get("peek_feasible")) && peek_position.is_some();
        Some(Self {
            id,
            position: Point {
                x: round_to(position.x.clamp(-2000.0, 2000.0), 3),
                y: round_to(position.y.clamp(-1000.0, 1000.0), 3),
                z: round_to(position.z.clamp(-2000.0, 2000.0), 3),
            },
            peek_position: peek_position.map(|point| Point {
                x: round_to(point.x.clamp(-2000.0, 2000.0), 3),
                y: round_to(point.y.clamp(-1000.0, 1000.0), 3),
                z: round_to(point.z.clamp(-2000.0, 2000.0), 3),
            }),
            travel_distance: round_to(number(raw.get("travel_distance"), 0.0).max(0.0), 3),
            route_alignment: round_to(number(raw.get("route_alignment"), 0.0).clamp(0.0, 1.0), 4),
            enemy_occlusion: round_to(number(raw.get("enemy_occlusion"), 0.0).clamp(0.0, 1.0), 4),
            exposure: round_to(number(raw.get("exposure"), 1.0).clamp(0.0, 1.0), 4),
            slope: round_to(number(raw.get("slope"), 0.0).max(0.0), 3),
            water: round_to(number(raw.get("water"), 0.0).clamp(0.0, 1.0), 4),
            ally_congestion: round_to(number(raw.get("ally_congestion"), 0.0).clamp(0.0, 1.0), 4),
            peek_feasible,
            escape_feasible: strict_bool(raw.get("escape_feasible")),
        })
    }

    fn score(&self, personality: Personality, urgent: bool) -> f64 {
        let mut enemy_occlusion = 26.0 + personality.caution * 18.0;
        let mut travel_distance = -0.035 - personality.caution * 0.035;
        let mut escape_feasible = 8.0 + personality.patience * 10.0;
        let peek_feasible = 6.0 + personality.aggression * 8.0;
        let exposure = if urgent { -48.0 } else { -28.0 };
        if urgent {
            enemy_occlusion += 22.0;
            travel_distance -= 0.045;
            escape_feasible += 10.0;
        }
        let contributions = [
            round_to(self.travel_distance * travel_distance, 3),
            round_to(self.route_alignment * 22.0, 3),
            round_to(self.enemy_occlusion * enemy_occlusion, 3),
            round_to(self.exposure * exposure, 3),
            round_to(self.slope * -1.4, 3),
            round_to(self.water * -55.0, 3),
            round_to(self.ally_congestion * -18.0, 3),
            if self.peek_feasible {
                peek_feasible
            } else {
                0.0
            },
            if self.escape_feasible {
                escape_feasible
            } else {
                0.0
            },
        ];
        round_to(contributions.into_iter().sum(), 3)
    }
}

#[derive(Clone, Debug)]
struct CoverReport {
    target: TargetKey,
    reported_at: f64,
    candidates: Vec<CoverCandidate>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CoverPhase {
    Approach,
    Hold,
    Peek,
    Return,
}

#[derive(Clone, Debug)]
struct CoverState {
    target: TargetKey,
    candidate_id: String,
    phase: CoverPhase,
    phase_until: f64,
    refresh_candidate: bool,
}

#[derive(Clone, Debug)]
struct RouteAssignment {
    route: Route,
    until: f64,
}

#[derive(Clone, Debug)]
struct RouteState {
    index: usize,
    route_id: String,
    join_index: usize,
    join_anchor: Point,
}

#[derive(Clone, Debug)]
struct TargetAssignment {
    target: TargetKey,
    until: f64,
}

#[derive(Clone, Debug)]
struct RecentHit {
    attacker: TargetKey,
    reported_at: f64,
    _damage: i64,
}

#[derive(Clone, Debug)]
struct DefenseResponder {
    base_id: String,
    point: Point,
}

#[derive(Clone, Debug, Default)]
struct DefenseIncident {
    responders: BTreeMap<i64, DefenseResponder>,
    clear_since: Option<f64>,
    need: usize,
}

#[derive(Clone, Debug, PartialEq)]
struct CaptureTarget {
    id: String,
    point: Point,
}

#[derive(Clone, Debug, Default, PartialEq)]
struct CaptureSquad {
    base_id: String,
    bot_ids: Vec<i64>,
}

#[derive(Clone, Debug)]
struct ArtilleryAnchor {
    route: Route,
    point: Point,
    face: Point,
    index: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct Personality {
    pub aggression: f64,
    pub caution: f64,
    pub teamwork: f64,
    pub patience: f64,
    pub initiative: f64,
    pub adaptability: f64,
    pub jiggle: f64,
}

#[derive(Clone, Debug)]
struct Order {
    id: i64,
    team: i64,
    target_id: Option<i64>,
    target_kind: Option<TargetKind>,
    aim_position: Option<Point>,
    face_position: Option<Point>,
    move_position: Point,
    fire_allowed: bool,
    combat_mode: String,
    throttle_override: Option<f64>,
    desired_range: f64,
    fire_range: f64,
    route_id: String,
    route_index: usize,
    route_anchor: Point,
    route_join: bool,
    personality: Personality,
    profile: Value,
    shell_index: usize,
    cover_id: Option<String>,
    defense_base_id: Option<String>,
    hull_angle_degrees: Option<f64>,
}

impl Order {
    fn to_value(&self) -> Value {
        let mut result = Map::new();
        result.insert("id".to_owned(), json!(self.id));
        result.insert("team".to_owned(), json!(self.team));
        result.insert("target_id".to_owned(), json!(self.target_id));
        if let Some(kind) = self.target_kind {
            result.insert("target_kind".to_owned(), json!(kind.as_str()));
        }
        result.insert("aim_position".to_owned(), json!(self.aim_position));
        result.insert("face_position".to_owned(), json!(self.face_position));
        result.insert("move_position".to_owned(), json!(self.move_position));
        result.insert("fire_allowed".to_owned(), json!(self.fire_allowed));
        result.insert("combat_mode".to_owned(), json!(self.combat_mode));
        result.insert(
            "throttle_override".to_owned(),
            json!(self.throttle_override),
        );
        result.insert("desired_range".to_owned(), json!(self.desired_range));
        result.insert("fire_range".to_owned(), json!(self.fire_range));
        result.insert("route_id".to_owned(), json!(self.route_id));
        result.insert("route_index".to_owned(), json!(self.route_index));
        result.insert("route_anchor".to_owned(), json!(self.route_anchor));
        result.insert("route_join".to_owned(), json!(self.route_join));
        result.insert("personality".to_owned(), json!(self.personality));
        result.insert("profile".to_owned(), self.profile.clone());
        result.insert("shell_index".to_owned(), json!(self.shell_index));
        if let Some(value) = &self.cover_id {
            result.insert("cover_id".to_owned(), json!(value));
        }
        if let Some(value) = &self.defense_base_id {
            result.insert("defense_base_id".to_owned(), json!(value));
        }
        if let Some(value) = self.hull_angle_degrees {
            result.insert("hull_angle_degrees".to_owned(), json!(value));
        }
        Value::Object(result)
    }
}

#[derive(Clone, Debug)]
pub struct BotPlanner {
    pub revision: u64,
    contacts: [BTreeMap<TargetKey, Contact>; 2],
    last_orders: Option<Value>,
    last_order_signature: Option<Value>,
    route_states: BTreeMap<i64, RouteState>,
    route_assignments: BTreeMap<i64, RouteAssignment>,
    opening_routes: BTreeMap<i64, Route>,
    next_route_rebalance: [f64; 2],
    engage_anchors: BTreeMap<i64, (TargetKey, Point)>,
    affordances: BTreeMap<i64, CoverReport>,
    cover_states: BTreeMap<i64, CoverState>,
    cover_reservations: BTreeSet<(i64, i64)>,
    target_assignments: BTreeMap<i64, TargetAssignment>,
    base_defense: [DefenseIncident; 2],
    base_capture: [CaptureSquad; 2],
    donated_capture_targets: [Option<CaptureTarget>; 2],
    artillery_anchors: BTreeMap<i64, ArtilleryAnchor>,
    recent_hits: BTreeMap<i64, RecentHit>,
}

impl Default for BotPlanner {
    fn default() -> Self {
        Self {
            revision: 0,
            contacts: [BTreeMap::new(), BTreeMap::new()],
            last_orders: None,
            last_order_signature: None,
            route_states: BTreeMap::new(),
            route_assignments: BTreeMap::new(),
            opening_routes: BTreeMap::new(),
            next_route_rebalance: [0.0, 0.0],
            engage_anchors: BTreeMap::new(),
            affordances: BTreeMap::new(),
            cover_states: BTreeMap::new(),
            cover_reservations: BTreeSet::new(),
            target_assignments: BTreeMap::new(),
            base_defense: [DefenseIncident::default(), DefenseIncident::default()],
            base_capture: [CaptureSquad::default(), CaptureSquad::default()],
            donated_capture_targets: [None, None],
            artillery_anchors: BTreeMap::new(),
            recent_hits: BTreeMap::new(),
        }
    }
}

impl BotPlanner {
    pub fn new() -> Self {
        Self::default()
    }

    /// Freeze the same class-aware opening lanes used by the live planner
    /// before `battle_start`, while each vehicle remains at its graph-authored
    /// formation pose and drives from that pose into the selected lane.
    pub fn opening_route_ids(manifest: &Value) -> Option<BTreeMap<u64, String>> {
        let rows = manifest.as_array()?;
        if rows.is_empty() || rows.len() > 30 {
            return None;
        }
        let mut states = Vec::with_capacity(rows.len());
        for raw in rows {
            let raw = raw.as_object()?;
            let id = raw.get("id")?.as_u64()?;
            let team = raw.get("team")?.as_u64()?;
            if id == 0 || !(1..=2).contains(&team) {
                return None;
            }
            let first = raw
                .get("route")?
                .as_object()?
                .get("waypoints")?
                .as_array()?
                .first()?
                .as_object()?;
            states.push(json!({
                "id": id,
                "team": team,
                "alive": true,
                "health": raw.get("health").and_then(Value::as_u64).unwrap_or(1),
                "max_health": raw.get("max_health").and_then(Value::as_u64).unwrap_or(1),
                "x": number(first.get("x"), 0.0),
                "y": number(first.get("y"), 0.0),
                "z": number(first.get("z"), 0.0),
                "yaw": 0.0,
                "world_pose": true,
            }));
        }
        let bots = Self::alive_bots(manifest, &Value::Array(states));
        if bots.len() != rows.len() {
            return None;
        }
        let mut planner = Self::new();
        for team in [1_i64, 2] {
            let mut team_bots: Vec<_> = bots
                .iter()
                .filter(|bot| bot.team == team)
                .cloned()
                .collect();
            team_bots.sort_by_key(|bot| bot.id);
            let catalog = Self::route_catalog(team, &team_bots);
            planner.ensure_opening_routes(&team_bots, &catalog);
        }
        let selected: BTreeMap<_, _> = planner
            .opening_routes
            .into_iter()
            .map(|(id, route)| (u64::try_from(id).ok(), route.id))
            .filter_map(|(id, route)| id.map(|id| (id, route)))
            .collect();
        (selected.len() == rows.len()).then_some(selected)
    }

    pub fn reset(&mut self) {
        *self = Self::default();
    }

    pub fn report_damage(
        &mut self,
        victim_bot_id: i64,
        attacker_kind: &str,
        attacker_id: i64,
        damage: i64,
        now: f64,
    ) -> bool {
        let Some(kind) = TargetKind::parse(attacker_kind) else {
            return false;
        };
        if victim_bot_id <= 0 || attacker_id <= 0 || damage <= 0 {
            return false;
        }
        self.recent_hits.insert(
            victim_bot_id,
            RecentHit {
                attacker: TargetKey {
                    kind,
                    id: attacker_id,
                },
                reported_at: finite(now, 0.0),
                _damage: damage,
            },
        );
        true
    }

    pub fn known_targets(bot_states: &Value, players: &Value) -> KnownTargets {
        let mut result = BTreeMap::new();
        for raw in players.as_array().into_iter().flatten() {
            let Some(raw) = raw.as_object() else { continue };
            let id = integer(raw.get("id"), 0);
            if id != 0 {
                let key = TargetKey {
                    kind: TargetKind::Human,
                    id,
                };
                result.insert(
                    key,
                    KnownTarget {
                        kind: TargetKind::Human,
                        team: integer(raw.get("team"), 0),
                        alive: boolean(raw.get("alive"), true),
                    },
                );
            }
        }
        for raw in bot_states.as_array().into_iter().flatten() {
            let Some(raw) = raw.as_object() else { continue };
            let id = integer(raw.get("id"), 0);
            if id != 0 {
                let key = TargetKey {
                    kind: TargetKind::Bot,
                    id,
                };
                result.insert(
                    key,
                    KnownTarget {
                        kind: TargetKind::Bot,
                        team: integer(raw.get("team"), 0),
                        alive: boolean(raw.get("alive"), true),
                    },
                );
            }
        }
        result
    }

    pub fn known_bots(manifest: &Value, bot_states: &Value) -> KnownBots {
        let states: BTreeMap<i64, &Map<String, Value>> = bot_states
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_object)
            .map(|state| (integer(state.get("id"), 0), state))
            .collect();
        let mut result = BTreeMap::new();
        for raw in manifest.as_array().into_iter().flatten() {
            let Some(raw) = raw.as_object() else { continue };
            let id = integer(raw.get("id"), 0);
            if id == 0 {
                continue;
            }
            let state = states.get(&id).copied();
            result.insert(
                id,
                KnownBot {
                    team: integer(raw.get("team"), 0),
                    alive: state
                        .map(|state| boolean(state.get("alive"), integer(raw.get("health"), 1) > 0))
                        .unwrap_or_else(|| integer(raw.get("health"), 1) > 0),
                    x: state
                        .map(|state| number(state.get("x"), number(raw.get("x"), 0.0)))
                        .unwrap_or_else(|| number(raw.get("x"), 0.0)),
                    z: state
                        .map(|state| number(state.get("z"), number(raw.get("z"), 0.0)))
                        .unwrap_or_else(|| number(raw.get("z"), 0.0)),
                },
            );
        }
        result
    }

    pub fn report_contacts(
        &mut self,
        contacts: &Value,
        known_targets: &KnownTargets,
        now: f64,
    ) -> usize {
        self.report_contacts_with_visibility(contacts, known_targets, now, None)
    }

    pub fn report_contacts_with_visibility(
        &mut self,
        contacts: &Value,
        known_targets: &KnownTargets,
        now: f64,
        mut accepted_visibility: Option<&mut Vec<Value>>,
    ) -> usize {
        let Some(values) = contacts.as_array() else {
            return 0;
        };
        let mut accepted = 0;
        for raw in values.iter().take(MAX_CONTACTS_PER_TEAM * 2) {
            let Some(raw) = raw.as_object() else { continue };
            if !raw.get("shootable_by_bot_ids").is_some_and(Value::is_array)
                || !raw.get("visible").is_some_and(Value::is_boolean)
            {
                continue;
            }
            let observing_team = integer(raw.get("observing_team"), 0);
            let Some(team_index) = team_index(observing_team) else {
                continue;
            };
            let target_id = integer(raw.get("target_id"), 0);
            let requested_kind = TargetKind::parse(&text(raw.get("target_kind")));
            let (key, target) = if let Some(kind) = requested_kind {
                let key = TargetKey {
                    kind,
                    id: target_id,
                };
                let Some(target) = known_targets.get(&key) else {
                    continue;
                };
                (key, target)
            } else {
                let matches: Vec<_> = known_targets
                    .iter()
                    .filter(|(key, _)| key.id == target_id)
                    .collect();
                if matches.len() != 1 {
                    continue;
                }
                (*matches[0].0, matches[0].1)
            };
            let target_team = integer(raw.get("target_team"), target.team);
            if target.team == observing_team || target_team != target.team || !target.alive {
                continue;
            }
            let visible = raw.get("visible").and_then(Value::as_bool).unwrap_or(false);
            if visible {
                let shootable_by_bot_ids = bot_id_set(raw.get("shootable_by_bot_ids"));
                self.contacts[team_index].insert(
                    key,
                    Contact {
                        id: target_id,
                        target_kind: key.kind,
                        team: target.team,
                        visible: true,
                        last_seen: finite(now, 0.0),
                        position: Point::loose(Some(&Value::Object(raw.clone()))),
                        health: integer(raw.get("health"), 1).max(0),
                        max_health: integer(raw.get("max_health"), 1).max(1),
                        class_tag: {
                            let value: String =
                                text(raw.get("class_tag")).chars().take(24).collect();
                            if value.is_empty() {
                                "unknown".to_owned()
                            } else {
                                value
                            }
                        },
                        armor: number(raw.get("armor"), 0.0).max(0.0),
                        shootable_by_bot_ids,
                    },
                );
                accepted += 1;
            } else if let Some(previous) = self.contacts[team_index].get_mut(&key) {
                previous.visible = false;
                previous.shootable_by_bot_ids.clear();
                accepted += 1;
            } else {
                continue;
            }
            if let Some(output) = accepted_visibility.as_deref_mut() {
                output.push(json!({
                    "observing_team": observing_team,
                    "target_kind": key.kind.as_str(),
                    "target_id": target_id,
                    "target_team": target.team,
                    "visible": visible,
                }));
            }
        }
        accepted
    }

    pub fn report_affordances(
        &mut self,
        reports: &Value,
        known_bots: &KnownBots,
        known_targets: &KnownTargets,
        now: f64,
    ) -> usize {
        let Some(reports) = reports.as_array() else {
            return 0;
        };
        let mut accepted = 0;
        for raw in reports.iter().take(MAX_COVER_REPORTS) {
            let Some(raw) = raw.as_object() else { continue };
            let bot_id = integer(raw.get("bot_id"), 0);
            let Some(bot) = known_bots.get(&bot_id) else {
                continue;
            };
            let Some(target_kind) = TargetKind::parse(&text(raw.get("target_kind"))) else {
                continue;
            };
            let target = TargetKey {
                kind: target_kind,
                id: integer(raw.get("target_id"), 0),
            };
            let Some(known_target) = known_targets.get(&target) else {
                continue;
            };
            if !bot.alive || bot.team == known_target.team {
                continue;
            }
            let Some(team_index) = team_index(bot.team) else {
                continue;
            };
            let Some(contact) = self.contacts[team_index].get(&target) else {
                continue;
            };
            if !contact.visible || finite(now, 0.0) - contact.last_seen > CONTACT_TTL_SECONDS {
                continue;
            }
            let Some(raw_candidates) = raw.get("candidates").and_then(Value::as_array) else {
                continue;
            };
            let bot_point = Point {
                x: bot.x,
                y: 0.0,
                z: bot.z,
            };
            let candidates: Vec<_> = raw_candidates
                .iter()
                .take(MAX_COVER_CANDIDATES)
                .filter_map(CoverCandidate::normalize)
                .filter(|candidate| {
                    candidate.travel_distance <= 180.0
                        && candidate.water < 0.5
                        && candidate.slope <= 28.0
                        && candidate.position.distance_xz(bot_point) <= 180.0
                        && candidate
                            .peek_position
                            .is_none_or(|point| point.distance_xz(bot_point) <= 200.0)
                })
                .collect();
            if candidates.is_empty() {
                continue;
            }
            self.affordances.insert(
                bot_id,
                CoverReport {
                    target,
                    reported_at: finite(now, 0.0),
                    candidates,
                },
            );
            accepted += 1;
        }
        accepted
    }
}

impl BotPlanner {
    pub fn build_orders(
        &mut self,
        manifest: &Value,
        bot_states: &Value,
        players: &Value,
        now: f64,
        defense: Option<&Value>,
    ) -> Value {
        let now = finite(now, 0.0);
        let known_targets = Self::known_targets(bot_states, players);
        let contacts = self.prune_contacts(&known_targets, now);
        let bots = Self::alive_bots(manifest, bot_states);
        self.prune_tactical_state(&bots, &known_targets, now);
        let defenders = self.update_base_defense(&bots, &contacts, defense, now);
        self.cover_reservations.clear();
        let team_axes = [
            Self::team_base_axis(1, &bots),
            Self::team_base_axis(2, &bots),
        ];
        let mut orders = Vec::new();
        for team in [1_i64, 2] {
            let mut team_bots: Vec<_> = bots
                .iter()
                .filter(|bot| bot.team == team)
                .cloned()
                .collect();
            team_bots.sort_by_key(|bot| bot.id);
            let team_index = team_index(team).expect("canonical team");
            let protected_ids: BTreeSet<_> = defenders[team_index].keys().copied().collect();
            self.rebalance_routes(team, &team_bots, &contacts[team_index], now, &protected_ids);
            let capture_target = self.capture_target(team, &team_bots);
            let capture_ids =
                self.update_base_capture(team, &team_bots, capture_target.as_ref(), &protected_ids);
            let assignments = self.assign_targets(&team_bots, &contacts[team_index], now);
            let assignments = self.prioritize_base_invaders(
                team,
                &team_bots,
                &contacts[team_index],
                assignments,
                &defenders[team_index],
                defense,
            );
            for (index, bot) in team_bots.iter().enumerate() {
                let focus = assignments.get(&bot.id);
                let travel_override = defenders[team_index].get(&bot.id);
                orders.push(self.order_for(
                    bot,
                    index,
                    team_bots.len(),
                    focus,
                    &contacts[team_index],
                    now,
                    travel_override,
                    team_axes[team_index],
                    &team_bots,
                    capture_target.as_ref(),
                    contacts[team_index].is_empty() && capture_ids.contains(&bot.id),
                ));
            }
        }
        orders.sort_by_key(|order| order.id);
        let order_values: Vec<_> = orders.iter().map(Order::to_value).collect();
        let signature_orders: Vec<_> = order_values.iter().map(order_signature).collect();
        let signature = json!({"orders": signature_orders});
        if self.last_order_signature.as_ref() != Some(&signature) {
            self.revision = self.revision.saturating_add(1);
            self.last_order_signature = Some(signature);
        }
        self.last_orders = Some(json!({"orders": order_values.clone()}));
        json!({"revision": self.revision, "orders": order_values})
    }

    pub fn debug_summary(&self, now: f64) -> Value {
        let now = finite(now, 0.0);
        let orders = self
            .last_orders
            .as_ref()
            .and_then(|value| value.get("orders"))
            .and_then(Value::as_array);
        let mut teams = Map::new();
        for team in [1_i64, 2] {
            let index = team_index(team).expect("canonical team");
            let current: Vec<_> = self.contacts[index]
                .values()
                .filter(|contact| now - contact.last_seen <= CONTACT_TTL_SECONDS)
                .collect();
            let team_orders: Vec<_> = orders
                .into_iter()
                .flatten()
                .filter(|order| integer(order.get("team"), 0) == team)
                .collect();
            let mut modes = BTreeMap::<String, usize>::new();
            for order in &team_orders {
                let mode = order
                    .get("combat_mode")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown")
                    .to_owned();
                *modes.entry(mode).or_default() += 1;
            }
            teams.insert(
                team.to_string(),
                json!({
                    "contacts": current.len(),
                    "visible": current.iter().filter(|contact| contact.visible).count(),
                    "orders": team_orders.len(),
                    "targeted": team_orders.iter().filter(|order| order.get("target_id").is_some_and(|value| !value.is_null())).count(),
                    "fire": team_orders.iter().filter(|order| order.get("fire_allowed").and_then(Value::as_bool) == Some(true)).count(),
                    "modes": modes,
                }),
            );
        }
        json!({"teams": teams})
    }

    pub fn clear_observations(&mut self) {
        self.contacts = [BTreeMap::new(), BTreeMap::new()];
        self.affordances.clear();
        self.cover_states.clear();
        self.cover_reservations.clear();
        self.engage_anchors.clear();
    }

    /// Prefer the native world's canonical base centres over static tactical
    /// annotations. `bases[0]` belongs to team 1 and `bases[1]` to team 2, so
    /// each attacking team receives the opposite entry as its capture target.
    pub fn install_capture_bases(&mut self, map_name: &str, bases: [[f64; 2]; 2]) -> bool {
        if map_name.is_empty()
            || map_name.len() > 64
            || !map_name.is_ascii()
            || bases
                .into_iter()
                .flatten()
                .any(|coordinate| !coordinate.is_finite() || coordinate.abs() > 2_000.0)
        {
            return false;
        }
        let next = [
            Some(CaptureTarget {
                id: format!("native:{map_name}:2"),
                point: Point {
                    x: bases[1][0],
                    y: 0.0,
                    z: bases[1][1],
                },
            }),
            Some(CaptureTarget {
                id: format!("native:{map_name}:1"),
                point: Point {
                    x: bases[0][0],
                    y: 0.0,
                    z: bases[0][1],
                },
            }),
        ];
        let changed = self.donated_capture_targets != next;
        self.donated_capture_targets = next;
        if changed {
            self.base_capture = [CaptureSquad::default(), CaptureSquad::default()];
        }
        true
    }

    fn alive_bots(manifest: &Value, bot_states: &Value) -> Vec<Bot> {
        let states: BTreeMap<i64, &Map<String, Value>> = bot_states
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_object)
            .map(|state| (integer(state.get("id"), 0), state))
            .collect();
        let mut result = Vec::new();
        for raw in manifest.as_array().into_iter().flatten() {
            let Some(raw) = raw.as_object() else { continue };
            let id = integer(raw.get("id"), 0);
            let state = states.get(&id).copied();
            let manifest_alive = integer(raw.get("health"), 1) > 0;
            let alive = state
                .map(|state| boolean(state.get("alive"), manifest_alive))
                .unwrap_or(manifest_alive);
            if id == 0 || !alive {
                continue;
            }
            let destroyed = state
                .and_then(|state| state.get("critical"))
                .and_then(Value::as_object)
                .and_then(|critical| critical.get("destroyed"))
                .and_then(Value::as_array)
                .map(|values| values.iter().map(|value| text(Some(value))).collect())
                .unwrap_or_default();
            let ammo_remaining = state
                .and_then(|state| state.get("ammo_remaining"))
                .and_then(Value::as_array)
                .map(|values| values.iter().map(|value| integer(Some(value), 0)).collect());
            let state_value = |key: &str| state.and_then(|state| state.get(key));
            result.push(Bot {
                id,
                team: integer(raw.get("team"), 0),
                slot: integer(raw.get("slot"), 0),
                profile: Profile::parse(raw.get("profile")),
                route: Route::parse(raw.get("route"), integer(raw.get("team"), 0)),
                state: BotState {
                    x: number(state_value("x"), 0.0),
                    y: number(state_value("y"), 0.0),
                    z: number(state_value("z"), 0.0),
                    yaw: number(state_value("yaw"), 0.0),
                    health: number(state_value("health"), 1.0).max(0.0),
                    max_health: number(state_value("max_health"), 1.0).max(1.0),
                    world_pose: boolean(state_value("world_pose"), true),
                    destroyed,
                    shell_index: integer(state_value("shell_index"), 0).max(0) as usize,
                    ammo_remaining,
                },
            });
        }
        result
    }

    fn prune_tactical_state(&mut self, bots: &[Bot], known_targets: &KnownTargets, now: f64) {
        let live_bots: BTreeMap<_, _> = bots.iter().map(|bot| (bot.id, bot)).collect();
        self.route_states.retain(|id, _| live_bots.contains_key(id));
        self.route_assignments
            .retain(|id, _| live_bots.contains_key(id));
        self.opening_routes
            .retain(|id, _| live_bots.contains_key(id));
        self.target_assignments
            .retain(|id, _| live_bots.contains_key(id));
        self.engage_anchors
            .retain(|id, _| live_bots.contains_key(id));
        self.artillery_anchors
            .retain(|id, _| live_bots.contains_key(id));
        self.recent_hits.retain(|id, hit| {
            live_bots.contains_key(id) && now - hit.reported_at <= RECENT_HIT_SECONDS
        });
        self.affordances.retain(|bot_id, report| {
            let Some(bot) = live_bots.get(bot_id) else {
                return false;
            };
            let Some(target) = known_targets.get(&report.target) else {
                return false;
            };
            let contact =
                team_index(bot.team).and_then(|index| self.contacts[index].get(&report.target));
            target.alive
                && contact.is_some_and(|contact| contact.visible)
                && now - report.reported_at <= COVER_TTL_SECONDS
        });
        self.cover_states.retain(|bot_id, state| {
            live_bots.contains_key(bot_id)
                && self
                    .affordances
                    .get(bot_id)
                    .is_some_and(|report| report.target == state.target)
        });
    }

    fn prune_contacts(&mut self, known_targets: &KnownTargets, now: f64) -> [Vec<Contact>; 2] {
        let mut result = [Vec::new(), Vec::new()];
        for index in 0..2 {
            self.contacts[index].retain(|key, contact| {
                known_targets.get(key).is_some_and(|target| target.alive)
                    && now - contact.last_seen <= CONTACT_TTL_SECONDS
            });
            result[index].extend(self.contacts[index].values().cloned());
        }
        result
    }

    fn base_defense_eligible(bot: &Bot) -> bool {
        bot.state.world_pose
            && !bot.state.destroyed.iter().any(|value| {
                matches!(
                    value.as_str(),
                    "engineHealth" | "leftTrackHealth" | "rightTrackHealth"
                )
            })
    }

    fn capture_target(&self, team: i64, bots: &[Bot]) -> Option<CaptureTarget> {
        if let Some(target) =
            team_index(team).and_then(|index| self.donated_capture_targets[index].clone())
        {
            return Some(target);
        }
        let route = bots
            .iter()
            .map(|bot| &bot.route)
            .find(|route| route.capture_target.is_some())?;
        Some(CaptureTarget {
            id: format!("{}:{}", route.map_name?, 3 - team),
            point: route.capture_target?,
        })
    }

    fn update_base_capture(
        &mut self,
        team: i64,
        bots: &[Bot],
        target: Option<&CaptureTarget>,
        protected_ids: &BTreeSet<i64>,
    ) -> BTreeSet<i64> {
        let Some(index) = team_index(team) else {
            return BTreeSet::new();
        };
        let Some(target) = target else {
            self.base_capture[index] = CaptureSquad::default();
            return BTreeSet::new();
        };
        if self.base_capture[index].base_id != target.id {
            self.base_capture[index] = CaptureSquad {
                base_id: target.id.clone(),
                bot_ids: Vec::new(),
            };
        }
        let candidates: Vec<_> = bots
            .iter()
            .filter(|bot| !protected_ids.contains(&bot.id) && Self::base_defense_eligible(bot))
            .collect();
        let regulars: Vec<_> = candidates
            .iter()
            .copied()
            .filter(|bot| bot.profile.class_tag != "SPG")
            .collect();
        let eligible = if regulars.is_empty() {
            candidates
        } else {
            regulars
        };
        let by_id: BTreeMap<_, _> = eligible.iter().map(|bot| (bot.id, *bot)).collect();
        let mut selected: Vec<_> = self.base_capture[index]
            .bot_ids
            .iter()
            .copied()
            .filter(|id| by_id.contains_key(id))
            .take(MAX_BASE_CAPTURERS)
            .collect();
        let desired = MAX_BASE_CAPTURERS.min(eligible.len());
        if selected.len() < desired {
            let mut available: Vec<_> = eligible
                .into_iter()
                .filter(|bot| !selected.contains(&bot.id))
                .collect();
            available.sort_by(|left, right| {
                let eta = |bot: &Bot| {
                    bot.state.point().distance_xz(target.point) / bot.profile.speed.clamp(4.0, 30.0)
                };
                cmp_f64(eta(left), eta(right))
                    .then_with(|| {
                        cmp_f64(
                            right.state.health / right.state.max_health.max(1.0),
                            left.state.health / left.state.max_health.max(1.0),
                        )
                    })
                    .then_with(|| left.id.cmp(&right.id))
            });
            selected.extend(
                available
                    .into_iter()
                    .take(desired - selected.len())
                    .map(|bot| bot.id),
            );
        }
        self.base_capture[index].bot_ids = selected;
        self.base_capture[index].bot_ids.iter().copied().collect()
    }

    fn defense_points(defense: &Value, team: i64) -> Vec<DefensePoint> {
        let Some(raw) = defense.as_object() else {
            return Vec::new();
        };
        let Some(values) = team_value(raw, team).and_then(Value::as_array) else {
            return Vec::new();
        };
        values
            .iter()
            .take(4)
            .enumerate()
            .filter_map(|(index, value)| {
                let raw = value.as_object()?;
                let mut id = text(raw.get("id"));
                if id.is_empty() {
                    id = format!("{team}:{index}");
                }
                Some(DefensePoint {
                    id,
                    point: Point::loose(Some(value)),
                })
            })
            .collect()
    }

    fn defense_eta(bot: &Bot, point: Point, contacts: &[Contact], deadline: f64) -> DefenseEta {
        let distance = bot.state.point().distance_xz(point);
        let cruise = (bot.profile.speed * 0.65).clamp(4.0, 22.0);
        let eta = 3.0 + distance * 1.30 / cruise;
        let mut diversion: f64 = 0.0;
        for contact in contacts {
            if !contact.visible || !contact.shootable_by_bot_ids.contains(&bot.id) {
                continue;
            }
            let contact_distance = bot.state.point().distance_xz(contact.position);
            if contact_distance <= 50.0 {
                diversion = diversion.max(12.0);
            } else if contact_distance <= 150.0 {
                diversion = diversion.max(4.0);
            }
        }
        diversion += 6.0 * (1.0 - (bot.state.health / bot.state.max_health).min(1.0));
        if bot.profile.class_tag == "SPG" {
            diversion += 8.0;
        } else if bot.profile.class_tag == "AT-SPG" {
            diversion += 3.0;
        }
        if eta <= deadline {
            DefenseEta(0, eta + diversion, eta, bot.id)
        } else {
            DefenseEta(1, eta - deadline, eta + diversion, bot.id)
        }
    }

    fn update_base_defense(
        &mut self,
        bots: &[Bot],
        contacts: &[Vec<Contact>; 2],
        defense: Option<&Value>,
        now: f64,
    ) -> [BTreeMap<i64, DefenseResponder>; 2] {
        let Some(defense) = defense.filter(|value| value.is_object()) else {
            self.base_defense = [DefenseIncident::default(), DefenseIncident::default()];
            return [BTreeMap::new(), BTreeMap::new()];
        };
        let defense_object = defense.as_object().expect("validated object");
        let states = defense_object.get("states").and_then(Value::as_object);
        let bases = defense_object.get("bases").unwrap_or(&Value::Null);
        let live_by_team: [Vec<_>; 2] = [1_i64, 2].map(|team| {
            bots.iter()
                .filter(|bot| bot.team == team && Self::base_defense_eligible(bot))
                .cloned()
                .collect()
        });
        let mut result = [BTreeMap::new(), BTreeMap::new()];
        for team in [1_i64, 2] {
            let index = team_index(team).expect("canonical team");
            let live: BTreeMap<_, _> = live_by_team[index]
                .iter()
                .map(|bot| (bot.id, bot))
                .collect();
            let mut incident = self.base_defense[index].clone();
            incident.responders.retain(|id, _| live.contains_key(id));
            let raw_state = states
                .and_then(|states| team_value(states, team))
                .and_then(Value::as_object);
            let invaders =
                integer(raw_state.and_then(|state| state.get("invaders")), 0).max(0) as usize;
            let points = Self::defense_points(bases, team);
            let reserve_limit = if live.len() == 1 {
                1
            } else {
                live.len().saturating_sub(1)
            };
            if incident.responders.len() > reserve_limit {
                let deadline = (number(raw_state.and_then(|state| state.get("time_left")), 0.0)
                    - 2.0)
                    .max(0.0);
                let mut ranked: Vec<_> = incident
                    .responders
                    .iter()
                    .filter_map(|(id, responder)| {
                        let bot = live.get(id)?;
                        Some((
                            Self::defense_eta(bot, responder.point, &contacts[index], deadline),
                            *id,
                        ))
                    })
                    .collect();
                ranked.sort_by(|left, right| left.0.cmp(&right.0));
                let keep: BTreeSet<_> = ranked
                    .into_iter()
                    .take(reserve_limit)
                    .map(|(_, id)| id)
                    .collect();
                incident.responders.retain(|id, _| keep.contains(id));
            }
            if invaders == 0 {
                if incident.responders.is_empty() {
                    incident.clear_since = None;
                    incident.need = 0;
                } else if let Some(clear_since) = incident.clear_since {
                    if now - clear_since >= 3.0 {
                        incident.responders.clear();
                        incident.clear_since = None;
                        incident.need = 0;
                    }
                } else {
                    incident.clear_since = Some(now);
                }
            } else {
                incident.clear_since = None;
                let mut desired = invaders.clamp(1, MAX_BASE_DEFENDERS);
                desired = if live.len() > 1 {
                    desired.min(live.len() - 1)
                } else if live.is_empty() {
                    0
                } else {
                    1
                };
                incident.need = incident.need.max(desired);
                let point_by_id: BTreeMap<_, _> = points
                    .iter()
                    .map(|point| (point.id.clone(), point.clone()))
                    .collect();
                let responder_ids: Vec<_> = incident.responders.keys().copied().collect();
                for bot_id in responder_ids {
                    let current_valid = incident
                        .responders
                        .get(&bot_id)
                        .is_some_and(|responder| point_by_id.contains_key(&responder.base_id));
                    if current_valid {
                        continue;
                    }
                    if points.is_empty() {
                        incident.responders.remove(&bot_id);
                        continue;
                    }
                    let bot = live[&bot_id];
                    let selected = nearest_defense_point(bot, &points);
                    incident.responders.insert(
                        bot_id,
                        DefenseResponder {
                            base_id: selected.id.clone(),
                            point: selected.point,
                        },
                    );
                }
                let missing = incident
                    .need
                    .min(reserve_limit)
                    .saturating_sub(incident.responders.len());
                if missing > 0 && !points.is_empty() {
                    let deadline =
                        (number(raw_state.and_then(|state| state.get("time_left")), 0.0) - 2.0)
                            .max(0.0);
                    let mut candidates: Vec<_> = live
                        .values()
                        .filter(|bot| !incident.responders.contains_key(&bot.id))
                        .map(|bot| {
                            let selected = nearest_defense_point(bot, &points).clone();
                            (
                                Self::defense_eta(bot, selected.point, &contacts[index], deadline),
                                bot.id,
                                selected,
                            )
                        })
                        .collect();
                    candidates.sort_by(|left, right| left.0.cmp(&right.0));
                    for (_, bot_id, selected) in candidates.into_iter().take(missing) {
                        incident.responders.insert(
                            bot_id,
                            DefenseResponder {
                                base_id: selected.id,
                                point: selected.point,
                            },
                        );
                    }
                }
            }
            result[index] = incident.responders.clone();
            self.base_defense[index] = incident;
        }
        result
    }
}

#[derive(Clone, Debug)]
struct DefensePoint {
    id: String,
    point: Point,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct DefenseEta(i32, f64, f64, i64);

impl Eq for DefenseEta {}

impl Ord for DefenseEta {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0
            .cmp(&other.0)
            .then_with(|| cmp_f64(self.1, other.1))
            .then_with(|| cmp_f64(self.2, other.2))
            .then_with(|| self.3.cmp(&other.3))
    }
}

impl PartialOrd for DefenseEta {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

fn nearest_defense_point<'a>(bot: &Bot, points: &'a [DefensePoint]) -> &'a DefensePoint {
    points
        .iter()
        .min_by(|left, right| {
            cmp_f64(
                bot.state.point().distance_xz(left.point),
                bot.state.point().distance_xz(right.point),
            )
            .then_with(|| left.id.cmp(&right.id))
        })
        .expect("callers require at least one defense point")
}

fn team_value(values: &Map<String, Value>, team: i64) -> Option<&Value> {
    values.get(&team.to_string())
}

impl BotPlanner {
    fn prioritize_base_invaders(
        &mut self,
        team: i64,
        bots: &[Bot],
        contacts: &[Contact],
        mut assignments: BTreeMap<i64, Contact>,
        defenders: &BTreeMap<i64, DefenseResponder>,
        defense: Option<&Value>,
    ) -> BTreeMap<i64, Contact> {
        if defenders.is_empty() {
            return assignments;
        }
        let Some(defense) = defense.and_then(Value::as_object) else {
            return assignments;
        };
        let contributor_map = defense.get("contributors").and_then(Value::as_object);
        let contributors = contributor_map
            .and_then(|values| team_value(values, team))
            .and_then(Value::as_array);
        let mut contributor_keys = BTreeSet::new();
        for value in contributors.into_iter().flatten() {
            let Some(raw) = value.as_object() else {
                continue;
            };
            let Some(kind) = TargetKind::parse(&text(raw.get("kind"))) else {
                continue;
            };
            let id = integer(raw.get("id"), 0);
            if id > 0 {
                contributor_keys.insert(TargetKey { kind, id });
            }
        }
        if contributor_keys.is_empty() {
            return assignments;
        }
        let bot_by_id: BTreeMap<_, _> = bots.iter().map(|bot| (bot.id, bot)).collect();
        let mut reservations = BTreeMap::<TargetKey, usize>::new();
        for bot_id in defenders.keys() {
            let Some(bot) = bot_by_id.get(bot_id) else {
                continue;
            };
            let mut choices: Vec<_> = contacts
                .iter()
                .filter(|contact| {
                    contributor_keys.contains(&contact.key())
                        && contact.visible
                        && contact.shootable_by_bot_ids.contains(bot_id)
                })
                .map(|contact| InvaderChoice {
                    reservations: reservations.get(&contact.key()).copied().unwrap_or(0),
                    health_fraction: contact.health as f64 / contact.max_health.max(1) as f64,
                    distance: bot.state.point().distance_xz(contact.position),
                    key: contact.key(),
                    contact: contact.clone(),
                })
                .collect();
            choices.sort();
            let Some(selected) = choices.into_iter().next() else {
                continue;
            };
            *reservations.entry(selected.key).or_default() += 1;
            assignments.insert(*bot_id, selected.contact);
            self.target_assignments.insert(
                *bot_id,
                TargetAssignment {
                    target: selected.key,
                    until: 0.0,
                },
            );
        }
        assignments
    }

    fn desired_focus(contact: &Contact) -> usize {
        if contact.health.max(0) >= 1800 {
            3
        } else if contact.health.max(0) >= 900
            || matches!(contact.class_tag.as_str(), "heavyTank" | "AT-SPG")
        {
            2
        } else {
            1
        }
    }

    fn focus_limit(contact: &Contact, distance: f64) -> usize {
        let desired = Self::desired_focus(contact);
        if distance <= CLOSE_THREAT_DISTANCE {
            desired.max(CLOSE_THREAT_FOCUS_LIMIT)
        } else {
            desired
        }
    }

    fn engagement_range(bot: &Bot, contact: &Contact) -> f64 {
        let desired = bot.profile.desired_range.max(40.0);
        if bot.profile.class_tag == "SPG" {
            return desired.max(number(bot.profile.raw.get("fire_range"), 1250.0).min(2500.0));
        }
        let mobility = bot.profile.role("scout").max(bot.profile.role("flanker"));
        if contact.visible {
            (desired * 2.0 + mobility * 300.0).clamp(340.0, 560.0)
        } else {
            (desired * 1.5 + mobility * 210.0).clamp(240.0, 420.0)
        }
    }

    fn recent_hit(&mut self, bot_id: i64, now: f64) -> Option<RecentHit> {
        let hit = self.recent_hits.get(&bot_id)?.clone();
        if now - hit.reported_at > RECENT_HIT_SECONDS {
            self.recent_hits.remove(&bot_id);
            None
        } else {
            Some(hit)
        }
    }

    fn recent_threat_contact(
        &mut self,
        bot: &Bot,
        contacts: &[Contact],
        now: f64,
    ) -> Option<Contact> {
        let hit = self.recent_hit(bot.id, now)?;
        contacts
            .iter()
            .find(|contact| contact.key() == hit.attacker)
            .cloned()
    }

    fn ally_support_score(bot: &Bot, team_bots: &[Bot], focus: &Contact) -> f64 {
        let mut score = 0.0;
        for ally in team_bots {
            if ally.id == bot.id {
                continue;
            }
            let health_fraction = ally.state.health / ally.state.max_health.max(1.0);
            let bot_distance = ally.state.point().distance_xz(bot.state.point());
            let target_distance = ally.state.point().distance_xz(focus.position);
            score += health_fraction * (1.0 - bot_distance / 130.0).max(0.0) * 0.55;
            score += health_fraction * (1.0 - target_distance / 220.0).max(0.0) * 0.35;
        }
        round_to(score.clamp(0.0, 1.0), 3)
    }

    fn crossfire_risk(bot: &Bot, contacts: &[Contact]) -> f64 {
        let mut bearings = Vec::new();
        for contact in contacts {
            if !contact.visible || !contact.shootable_by_bot_ids.contains(&bot.id) {
                continue;
            }
            let dx = contact.position.x - bot.state.x;
            let dz = contact.position.z - bot.state.z;
            let distance = dx.hypot(dz);
            if distance <= 1.0 || distance > CROSSFIRE_MAX_DISTANCE {
                continue;
            }
            bearings.push(dx.atan2(dz));
        }
        let mut best: f64 = 0.0;
        for (index, first) in bearings.iter().enumerate() {
            for second in bearings.iter().skip(index + 1) {
                let separation = ((*first - *second + std::f64::consts::PI)
                    .rem_euclid(std::f64::consts::TAU)
                    - std::f64::consts::PI)
                    .abs();
                if separation >= CROSSFIRE_MIN_ANGLE {
                    best = best.max(
                        ((separation - CROSSFIRE_MIN_ANGLE) / 90.0_f64.to_radians())
                            .clamp(0.0, 1.0),
                    );
                }
            }
        }
        round_to(best, 3)
    }

    fn assign_targets(
        &mut self,
        bots: &[Bot],
        contacts: &[Contact],
        now: f64,
    ) -> BTreeMap<i64, Contact> {
        if bots.is_empty() || contacts.is_empty() {
            return BTreeMap::new();
        }
        let mut all_candidates = Vec::new();
        let mut by_bot = BTreeMap::<i64, Vec<TargetCandidate>>::new();
        for bot in bots {
            for contact in contacts {
                if !contact.visible || !contact.shootable_by_bot_ids.contains(&bot.id) {
                    continue;
                }
                let distance = bot.state.point().distance_xz(contact.position);
                if distance > Self::engagement_range(bot, contact) {
                    continue;
                }
                let mut score = contact.health as f64 / contact.max_health.max(1) as f64 * 28.0
                    + distance * 0.018;
                if self.recent_hits.get(&bot.id).is_some_and(|hit| {
                    now - hit.reported_at <= RECENT_HIT_SECONDS && hit.attacker == contact.key()
                }) {
                    score -= RECENT_ATTACKER_SCORE_BONUS;
                }
                if distance <= CLOSE_THREAT_DISTANCE {
                    score -= CLOSE_THREAT_SCORE_BONUS;
                }
                if contact.class_tag == "SPG" {
                    score -= DISCOVERED_ARTILLERY_PRIORITY_BONUS;
                }
                let candidate = TargetCandidate {
                    score,
                    bot_id: bot.id,
                    contact: contact.clone(),
                    distance,
                };
                all_candidates.push(candidate.clone());
                by_bot.entry(bot.id).or_default().push(candidate);
            }
        }
        for candidates in by_bot.values_mut() {
            candidates.sort();
        }
        all_candidates.sort();
        let mut reservations = BTreeMap::<TargetKey, usize>::new();
        let mut assigned = BTreeMap::new();
        for bot in bots {
            let Some(bot_candidates) = by_bot.get(&bot.id) else {
                self.target_assignments.remove(&bot.id);
                continue;
            };
            let Some(previous) = self.target_assignments.get(&bot.id).cloned() else {
                continue;
            };
            let Some(previous_candidate) = bot_candidates
                .iter()
                .find(|candidate| candidate.contact.key() == previous.target)
            else {
                self.target_assignments.remove(&bot.id);
                continue;
            };
            let best = &bot_candidates[0];
            let lease_expired = now >= previous.until;
            let close_override = best.contact.key() != previous.target
                && best.distance <= CLOSE_THREAT_DISTANCE
                && previous_candidate.distance > CLOSE_THREAT_DISTANCE;
            let attacker_override = self.recent_hits.get(&bot.id).is_some_and(|hit| {
                now - hit.reported_at <= RECENT_HIT_SECONDS
                    && best.contact.key() == hit.attacker
                    && best.contact.key() != previous.target
            });
            if close_override || attacker_override {
                continue;
            }
            if lease_expired && previous_candidate.score > best.score + TARGET_SWITCH_MARGIN {
                continue;
            }
            let key = previous_candidate.contact.key();
            if reservations.get(&key).copied().unwrap_or(0)
                >= Self::focus_limit(&previous_candidate.contact, previous_candidate.distance)
            {
                continue;
            }
            *reservations.entry(key).or_default() += 1;
            assigned.insert(bot.id, previous_candidate.contact.clone());
            if lease_expired {
                if let Some(previous) = self.target_assignments.get_mut(&bot.id) {
                    previous.until = now + TARGET_LEASE_SECONDS;
                }
            }
        }
        for candidate in all_candidates {
            if assigned.contains_key(&candidate.bot_id) {
                continue;
            }
            let key = candidate.contact.key();
            if reservations.get(&key).copied().unwrap_or(0)
                >= Self::focus_limit(&candidate.contact, candidate.distance)
            {
                continue;
            }
            *reservations.entry(key).or_default() += 1;
            assigned.insert(candidate.bot_id, candidate.contact);
            self.target_assignments.insert(
                candidate.bot_id,
                TargetAssignment {
                    target: key,
                    until: now + TARGET_LEASE_SECONDS,
                },
            );
        }
        for bot in bots {
            if !assigned.contains_key(&bot.id) {
                self.target_assignments.remove(&bot.id);
            }
        }
        assigned
    }

    fn route_catalog(team: i64, bots: &[Bot]) -> BTreeMap<String, Route> {
        let mut result = BTreeMap::new();
        for bot in bots {
            if !bot.route.id.is_empty() && !bot.route.waypoints.is_empty() {
                result
                    .entry(bot.route.id.clone())
                    .or_insert_with(|| bot.route.clone());
            }
        }
        let map_names: BTreeSet<_> = bots.iter().filter_map(|bot| bot.route.map_name).collect();
        if let (Some(map_name), Ok(team)) = (map_names.iter().copied().next(), u8::try_from(team)) {
            if map_names.len() == 1 {
                if let (Some(map), Some(routes)) =
                    (tactical_map(map_name), routes_for(map_name, team))
                {
                    for route in routes {
                        result.insert(
                            route.id.to_owned(),
                            Route::from_tactical(MatchedTacticalRoute { map, team, route }),
                        );
                    }
                }
            }
        }
        result
    }

    fn opening_route_score(
        bot: &Bot,
        route: &Route,
        front_usage: &BTreeMap<String, usize>,
        artillery_usage: &BTreeMap<String, usize>,
    ) -> f64 {
        let personality = Self::personality(bot.id);
        if bot.profile.class_tag == "SPG" {
            return route.role_weights.get("artillery").copied().unwrap_or(0.0) * 100.0
                - route.risk * 24.0
                - artillery_usage.get(&route.id).copied().unwrap_or(0) as f64
                    * ARTILLERY_ROUTE_REPEAT_PENALTY;
        }
        let used = front_usage.get(&route.id).copied().unwrap_or(0);
        let capacity = route.capacity.unwrap_or(1).max(1);
        let mut score = route.role_affinity(&bot.profile) * 18.0
            + route.class_affinity(&bot.profile.class_tag) * CLASS_ROUTE_AFFINITY_WEIGHT
            + route.risk * personality.aggression * 16.0
            - route.risk * personality.caution * 13.0
            + personality.initiative * route.risk * 5.0
            - used as f64 / capacity as f64 * 28.0;
        if used >= capacity {
            score -= 34.0;
        }
        score
    }

    fn ensure_opening_routes(&mut self, bots: &[Bot], catalog: &BTreeMap<String, Route>) {
        let tactical = catalog.values().any(|route| route.map_name.is_some());
        let mut front_usage = BTreeMap::<String, usize>::new();
        let mut artillery_usage = BTreeMap::<String, usize>::new();
        for bot in bots {
            let existing = self
                .opening_routes
                .get(&bot.id)
                .filter(|route| {
                    catalog
                        .get(&route.id)
                        .is_some_and(|current| current == *route)
                })
                .cloned();
            let route = if let Some(route) = existing {
                route
            } else if tactical {
                let under_capacity: Vec<_> = catalog
                    .values()
                    .filter(|route| {
                        bot.profile.class_tag == "SPG"
                            || front_usage.get(&route.id).copied().unwrap_or(0)
                                < route.capacity.unwrap_or(1).max(1)
                    })
                    .collect();
                let candidates: Vec<_> = if under_capacity.is_empty() {
                    catalog.values().collect()
                } else {
                    under_capacity
                };
                let Some(route) = candidates.into_iter().max_by(|left, right| {
                    cmp_f64(
                        Self::opening_route_score(bot, left, &front_usage, &artillery_usage),
                        Self::opening_route_score(bot, right, &front_usage, &artillery_usage),
                    )
                    .then_with(|| right.id.cmp(&left.id))
                }) else {
                    continue;
                };
                route.clone()
            } else {
                bot.route.clone()
            };
            if bot.profile.class_tag == "SPG" {
                *artillery_usage.entry(route.id.clone()).or_default() += 1;
            } else {
                *front_usage.entry(route.id.clone()).or_default() += 1;
            }
            let changed = self
                .opening_routes
                .get(&bot.id)
                .is_none_or(|current| current != &route);
            self.opening_routes.insert(bot.id, route.clone());
            if changed {
                self.route_assignments
                    .insert(bot.id, RouteAssignment { route, until: 0.0 });
                self.route_states.remove(&bot.id);
            }
        }
    }

    fn team_base_axis(team: i64, bots: &[Bot]) -> Option<(Point, Point)> {
        let mut starts = [Vec::new(), Vec::new()];
        let mut own_ends = Vec::new();
        for bot in bots {
            if bot.route.waypoints.len() < 2 {
                continue;
            }
            let Some(index) = team_index(bot.team) else {
                continue;
            };
            starts[index].push(bot.route.waypoints[0]);
            if bot.team == team {
                own_ends.push(*bot.route.waypoints.last().expect("route has two points"));
            }
        }
        let own_starts = &starts[team_index(team)?];
        if own_starts.is_empty() || own_ends.is_empty() {
            return None;
        }
        let own = Point::average(own_starts);
        let opposing_starts = &starts[team_index(3 - team)?];
        let enemy_ends = if opposing_starts.is_empty() {
            let distances: Vec<_> = own_ends
                .iter()
                .map(|point| point.distance_xz(own))
                .collect();
            let farthest = distances.iter().copied().fold(0.0_f64, f64::max);
            let threshold = (farthest * 0.02).max(1.0);
            own_ends
                .into_iter()
                .zip(distances)
                .filter_map(|(point, distance)| (farthest - distance <= threshold).then_some(point))
                .collect::<Vec<_>>()
        } else {
            opposing_starts.clone()
        };
        let enemy = Point::average(&enemy_ends);
        (own.distance_xz(enemy) >= 1.0).then_some((own, enemy))
    }

    fn nearest_route(contact: &Contact, catalog: &BTreeMap<String, Route>) -> Option<String> {
        catalog
            .iter()
            .map(|(id, route)| {
                let distance = route
                    .waypoints
                    .iter()
                    .map(|point| {
                        let dx = point.x - contact.position.x;
                        let dz = point.z - contact.position.z;
                        dx * dx + dz * dz
                    })
                    .min_by(|left, right| cmp_f64(*left, *right))
                    .unwrap_or(1e18);
                (distance, id)
            })
            .min_by(|left, right| cmp_f64(left.0, right.0).then_with(|| left.1.cmp(right.1)))
            .map(|(_, id)| id.clone())
    }

    fn rebalance_routes(
        &mut self,
        team: i64,
        bots: &[Bot],
        contacts: &[Contact],
        now: f64,
        protected_ids: &BTreeSet<i64>,
    ) {
        let catalog = Self::route_catalog(team, bots);
        self.ensure_opening_routes(bots, &catalog);
        for bot in bots {
            let assigned = self.route_assignments.get(&bot.id);
            let invalid = assigned.is_none_or(|assigned| {
                !catalog
                    .get(&assigned.route.id)
                    .is_some_and(|route| route == &assigned.route)
                    || (assigned.until > 0.0 && assigned.until <= now)
            });
            if invalid {
                if let Some(route) = self
                    .opening_routes
                    .get(&bot.id)
                    .and_then(|route| catalog.get(&route.id))
                {
                    self.route_assignments.insert(
                        bot.id,
                        RouteAssignment {
                            route: route.clone(),
                            until: 0.0,
                        },
                    );
                } else {
                    self.route_assignments.remove(&bot.id);
                }
                self.route_states.remove(&bot.id);
            }
        }
        if catalog.len() < 2 {
            return;
        }
        let Some(team_index) = team_index(team) else {
            return;
        };
        if now < self.next_route_rebalance[team_index] {
            return;
        }
        self.next_route_rebalance[team_index] = now + ROUTE_REBALANCE_SECONDS;
        let mut pressure: BTreeMap<_, f64> = catalog.keys().map(|id| (id.clone(), 0.0)).collect();
        for contact in contacts {
            if let Some(route_id) = Self::nearest_route(contact, &catalog) {
                let fraction = contact.health as f64 / contact.max_health.max(1) as f64;
                *pressure.entry(route_id).or_default() += fraction.max(0.3);
            }
        }
        if pressure.values().copied().fold(0.0_f64, f64::max) <= 0.0 {
            return;
        }
        let mut counts: BTreeMap<_, usize> = catalog.keys().map(|id| (id.clone(), 0)).collect();
        for bot in bots {
            if bot.profile.class_tag == "SPG" {
                continue;
            }
            let route = self
                .route_assignments
                .get(&bot.id)
                .map(|assignment| &assignment.route)
                .unwrap_or(&bot.route);
            if let Some(count) = counts.get_mut(&route.id) {
                *count += 1;
            }
        }
        let mut target_route = None;
        let mut target_score = f64::NEG_INFINITY;
        for route_id in catalog.keys() {
            let score = pressure[route_id] - counts[route_id] as f64 * 0.45;
            if score > target_score {
                target_score = score;
                target_route = Some(route_id.clone());
            }
        }
        let Some(target_route) = target_route else {
            return;
        };
        if pressure[&target_route] - counts[&target_route] as f64 * 0.45 <= 0.0 {
            return;
        }
        for assignment in self.route_assignments.values_mut() {
            if assignment.route.id == target_route && assignment.until > 0.0 {
                assignment.until = now + ROUTE_LEASE_SECONDS;
            }
        }
        let target_record = &catalog[&target_route];
        if target_record
            .capacity
            .is_some_and(|capacity| counts[&target_route] >= capacity.max(1))
        {
            return;
        }
        let mut candidates = Vec::new();
        for bot in bots {
            if protected_ids.contains(&bot.id) || bot.profile.class_tag == "SPG" {
                continue;
            }
            let current = self
                .route_assignments
                .get(&bot.id)
                .map(|assignment| &assignment.route)
                .unwrap_or(&bot.route);
            if current.id == target_route || counts.get(&current.id).copied().unwrap_or(0) <= 1 {
                continue;
            }
            let class_affinity = target_record.class_affinity(&bot.profile.class_tag);
            if !target_record.class_weights.is_empty() && class_affinity < MIN_ROUTE_CLASS_AFFINITY
            {
                continue;
            }
            let mobility = bot
                .profile
                .role("support")
                .max(bot.profile.role("flanker"))
                .max(bot.profile.role("scout"));
            let personality = Self::personality(bot.id);
            let score = mobility * 2.0 + personality.adaptability
                - bot.profile.role("brawler") * 0.65
                - pressure.get(&current.id).copied().unwrap_or(0.0) * 0.7
                + class_affinity * 1.8
                + target_record.role_affinity(&bot.profile) * 1.2;
            candidates.push((score, bot.id));
        }
        candidates
            .sort_by(|left, right| cmp_f64(right.0, left.0).then_with(|| left.1.cmp(&right.1)));
        let Some((_, donor_id)) = candidates.first().copied() else {
            return;
        };
        self.route_assignments.insert(
            donor_id,
            RouteAssignment {
                route: catalog[&target_route].clone(),
                until: now + ROUTE_LEASE_SECONDS,
            },
        );
        self.route_states.remove(&donor_id);
    }
}

#[derive(Clone, Debug)]
struct InvaderChoice {
    reservations: usize,
    health_fraction: f64,
    distance: f64,
    key: TargetKey,
    contact: Contact,
}

impl Eq for InvaderChoice {}

impl PartialEq for InvaderChoice {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}

impl Ord for InvaderChoice {
    fn cmp(&self, other: &Self) -> Ordering {
        self.reservations
            .cmp(&other.reservations)
            .then_with(|| cmp_f64(self.health_fraction, other.health_fraction))
            .then_with(|| cmp_f64(self.distance, other.distance))
            .then_with(|| self.key.cmp(&other.key))
    }
}

impl PartialOrd for InvaderChoice {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Clone, Debug)]
struct TargetCandidate {
    score: f64,
    bot_id: i64,
    contact: Contact,
    distance: f64,
}

impl Eq for TargetCandidate {}

impl PartialEq for TargetCandidate {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}

impl Ord for TargetCandidate {
    fn cmp(&self, other: &Self) -> Ordering {
        cmp_f64(self.score, other.score)
            .then_with(|| self.bot_id.cmp(&other.bot_id))
            .then_with(|| self.contact.target_kind.cmp(&other.contact.target_kind))
            .then_with(|| self.contact.id.cmp(&other.contact.id))
            .then_with(|| cmp_f64(self.distance, other.distance))
    }
}

impl PartialOrd for TargetCandidate {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl BotPlanner {
    fn route_for(&mut self, bot: &Bot, stop_before_objective: bool) -> RoutePlan {
        let route = self
            .route_assignments
            .get(&bot.id)
            .map(|assignment| assignment.route.clone())
            .or_else(|| self.opening_routes.get(&bot.id).cloned())
            .unwrap_or_else(|| bot.route.clone());
        if route.waypoints.is_empty() {
            let route_ids = ["left_flank", "center_line", "right_flank"];
            let offset = (bot.slot + bot.id).rem_euclid(route_ids.len() as i64) as usize;
            let route_id = route_ids[offset].to_owned();
            let side = match route_id.as_str() {
                "left_flank" => -1.0,
                "right_flank" => 1.0,
                _ => 0.0,
            };
            let direction = if bot.team == 1 { 1.0 } else { -1.0 };
            let point = Point {
                x: round_to(side * 115.0, 3),
                y: 0.0,
                z: round_to(direction * 18.0, 3),
            };
            return RoutePlan {
                route_id,
                index: 0,
                point,
                anchor: point,
                route_join: false,
            };
        }
        let route_id = if route.id.is_empty() {
            "uploaded_route".to_owned()
        } else {
            route.id.clone()
        };
        let route_limit = if stop_before_objective && route.waypoints.len() > 1 {
            route.waypoints.len() - 2
        } else {
            route.waypoints.len() - 1
        };
        let initialize = self
            .route_states
            .get(&bot.id)
            .is_none_or(|state| state.route_id != route_id);
        if initialize {
            let position = bot.state.point();
            let nearest = route
                .waypoints
                .iter()
                .enumerate()
                .min_by(|left, right| {
                    cmp_f64(
                        position.distance_xz(*left.1),
                        position.distance_xz(*right.1),
                    )
                })
                .map(|(index, _)| index)
                .unwrap_or(0);
            let mut index = if nearest == 0 && route.waypoints.len() > 1 {
                1
            } else {
                nearest
            };
            index = index.min(route_limit);
            while index < route_limit && position.distance_xz(route.waypoints[index]) < 30.0 {
                index += 1;
            }
            while index < route_limit {
                let point = route.waypoints[index];
                let bearing = (point.x - position.x).atan2(point.z - position.z);
                let delta = (bearing - bot.state.yaw + std::f64::consts::PI)
                    .rem_euclid(std::f64::consts::TAU)
                    - std::f64::consts::PI;
                if delta.abs() <= 1.75 {
                    break;
                }
                index += 1;
            }
            self.route_states.insert(
                bot.id,
                RouteState {
                    index,
                    route_id: route_id.clone(),
                    join_index: index,
                    join_anchor: position,
                },
            );
        }
        let state = self
            .route_states
            .get_mut(&bot.id)
            .expect("route state initialized above");
        let mut index = state.index.min(route_limit);
        state.index = index;
        let position = bot.state.point();
        let mut point = route.waypoints[index];
        if position.distance_xz(point) <= 13.0 && index < route_limit {
            index += 1;
            state.index = index;
            point = route.waypoints[index];
        }
        let route_join = state.join_index == index;
        let anchor = if route_join {
            state.join_anchor
        } else {
            route.waypoints[index.saturating_sub(1)]
        };
        RoutePlan {
            route_id,
            index,
            point,
            anchor,
            route_join,
        }
    }

    fn retreat_point(&self, bot: &Bot, route_anchor: Point) -> Point {
        let route = self
            .route_assignments
            .get(&bot.id)
            .map(|assignment| &assignment.route)
            .or_else(|| self.opening_routes.get(&bot.id))
            .unwrap_or(&bot.route);
        let Some(state) = self.route_states.get(&bot.id) else {
            return route_anchor;
        };
        if route.waypoints.is_empty() {
            return route_anchor;
        }
        let index = state.index.min(route.waypoints.len() - 1);
        route.waypoints[index.saturating_sub(1)]
    }

    fn capture_staged(&self, bot: &Bot, route_index: usize) -> bool {
        let route = self
            .route_assignments
            .get(&bot.id)
            .map(|assignment| &assignment.route)
            .or_else(|| self.opening_routes.get(&bot.id))
            .unwrap_or(&bot.route);
        if route.waypoints.is_empty() {
            return true;
        }
        let staging_index = route.waypoints.len().saturating_sub(2);
        route_index >= staging_index
            && bot
                .state
                .point()
                .distance_xz(route.waypoints[staging_index])
                <= CAPTURE_STAGING_RADIUS
    }

    fn flank_point(bot: &Bot, contact: &Contact, desired_range: f64) -> Point {
        let dx = bot.state.x - contact.position.x;
        let dz = bot.state.z - contact.position.z;
        let length = dx.hypot(dz).max(1.0);
        let dx = dx / length;
        let dz = dz / length;
        let side = if bot.id % 2 != 0 { -1.0 } else { 1.0 };
        let lateral = (desired_range * 0.38).min(95.0) * side;
        Point::loose(Some(&json!({
            "x": contact.position.x + dx * desired_range * 0.72 + dz * lateral,
            "y": contact.position.y,
            "z": contact.position.z + dz * desired_range * 0.72 - dx * lateral,
        })))
    }

    fn artillery_anchor(
        &mut self,
        bot: &Bot,
        team_axis: Option<(Point, Point)>,
    ) -> ArtilleryAnchor {
        let route = self
            .route_assignments
            .get(&bot.id)
            .map(|assignment| assignment.route.clone())
            .or_else(|| self.opening_routes.get(&bot.id).cloned())
            .unwrap_or_else(|| bot.route.clone());
        if let Some(cached) = self.artillery_anchors.get(&bot.id) {
            if cached.route == route {
                return cached.clone();
            }
        }
        let result = if route.waypoints.is_empty() {
            let point = bot.state.point();
            ArtilleryAnchor {
                route,
                point,
                face: point,
                index: 0,
            }
        } else {
            let points = &route.waypoints;
            let (mut own, mut enemy) = team_axis.unwrap_or((points[0], *points.last().unwrap()));
            let mut axis_x = enemy.x - own.x;
            let mut axis_z = enemy.z - own.z;
            let mut axis_length = axis_x.hypot(axis_z);
            if axis_length < 1.0 {
                own = points[0];
                enemy = *points.last().unwrap();
                axis_x = enemy.x - own.x;
                axis_z = enemy.z - own.z;
                axis_length = axis_x.hypot(axis_z).max(1.0);
            }
            let axis_squared = axis_length * axis_length;
            let desired_radius = (axis_length * 0.16).clamp(35.0, 120.0);
            let (index, point) = points
                .iter()
                .copied()
                .enumerate()
                .min_by(|left, right| {
                    let score = |point: Point| {
                        let offset_x = point.x - own.x;
                        let offset_z = point.z - own.z;
                        let radius = offset_x.hypot(offset_z);
                        let progress = (offset_x * axis_x + offset_z * axis_z) / axis_squared;
                        let outside_rear = (progress - 0.30).max((-0.12 - progress).max(0.0));
                        let base_overlap = (desired_radius * 0.35 - radius).max(0.0);
                        outside_rear * axis_length * 8.0
                            + base_overlap * 5.0
                            + (radius - desired_radius).abs()
                            + (progress - 0.12).abs() * axis_length * 0.20
                    };
                    cmp_f64(score(left.1), score(right.1)).then_with(|| left.0.cmp(&right.0))
                })
                .unwrap();
            ArtilleryAnchor {
                route,
                point,
                face: enemy,
                index,
            }
        };
        self.artillery_anchors.insert(bot.id, result.clone());
        result
    }

    fn shell_index(
        profile: &Profile,
        contact: &Contact,
        personality: Personality,
        state: &BotState,
    ) -> usize {
        if profile.shells.is_empty() {
            return 0;
        }
        let available: Vec<_> = profile
            .shells
            .iter()
            .filter(|shell| {
                state.ammo_remaining.as_ref().is_none_or(|remaining| {
                    shell.index < remaining.len() && remaining[shell.index] > 0
                })
            })
            .collect();
        if available.is_empty() {
            return state.shell_index;
        }
        let is_he = |shell: &&Shell| {
            shell.kind.contains("high_explosive")
                || (shell.kind.contains("explosive") && !shell.kind.contains("armor_piercing"))
        };
        let non_he: Vec<_> = available
            .iter()
            .copied()
            .filter(|shell| !is_he(shell))
            .collect();
        let high_explosive: Vec<_> = available.iter().copied().filter(is_he).collect();
        let baseline = non_he.iter().copied().min_by_key(|shell| shell.index);
        let baseline_penetration = baseline.map(|shell| shell.penetration).unwrap_or(0.0);
        let mut standard = Vec::new();
        let mut premium = Vec::new();
        for shell in non_he {
            if baseline.is_some_and(|baseline| {
                shell.source_order != baseline.source_order
                    && baseline_penetration > 0.0
                    && shell.penetration >= baseline_penetration * 1.03
            }) {
                premium.push(shell);
            } else {
                standard.push(shell);
            }
        }
        let normal = best_penetration_shell(&standard);
        let gold = best_penetration_shell(&premium);
        let explosive = high_explosive.into_iter().max_by(|left, right| {
            cmp_f64(left.damage, right.damage)
                .then_with(|| cmp_f64(left.penetration, right.penetration))
                .then_with(|| right.index.cmp(&left.index))
        });
        if contact.armor <= 0.0 {
            return normal
                .or(gold)
                .or(explosive)
                .map(|shell| shell.index)
                .unwrap_or(0);
        }
        if let Some(explosive) = explosive {
            let fragile = contact.armor <= explosive.penetration * 0.90;
            let finisher = contact.health > 0
                && contact.health as f64
                    <= explosive.damage * (0.72 + personality.aggression * 0.18)
                && contact.armor <= explosive.penetration * 1.10;
            if fragile || finisher {
                return explosive.index;
            }
        }
        if let Some(normal) = normal {
            if let Some(gold) = gold {
                if normal.penetration < contact.armor * 1.05 {
                    return gold.index;
                }
            }
            return normal.index;
        }
        gold.or(explosive)
            .or_else(|| available.first().copied())
            .map(|shell| shell.index)
            .unwrap_or(0)
    }

    pub fn personality(bot_id: i64) -> Personality {
        let value = ((bot_id as i128 * 1_103_515_245_i128 + 12_345) & 0x7fff_ffff) as u64;
        Personality {
            aggression: round_to(0.35 + (value % 41) as f64 / 100.0, 3),
            caution: round_to(0.25 + ((value >> 8) % 41) as f64 / 100.0, 3),
            teamwork: round_to(0.30 + ((value >> 12) % 51) as f64 / 100.0, 3),
            patience: round_to(0.25 + ((value >> 16) % 56) as f64 / 100.0, 3),
            initiative: round_to(0.25 + ((value >> 20) % 61) as f64 / 100.0, 3),
            adaptability: round_to(0.30 + ((value >> 4) % 51) as f64 / 100.0, 3),
            jiggle: round_to(0.18 + ((value >> 6) % 65) as f64 / 100.0, 3),
        }
    }
}

#[derive(Clone, Debug)]
struct RoutePlan {
    route_id: String,
    index: usize,
    point: Point,
    anchor: Point,
    route_join: bool,
}

impl BotPlanner {
    fn cover_candidate(
        &mut self,
        bot: &Bot,
        focus: &Contact,
        personality: Personality,
        now: f64,
        urgent: bool,
    ) -> Option<(CoverCandidate, CoverState)> {
        let target = focus.key();
        let Some(report) = self.affordances.get(&bot.id).cloned() else {
            self.cover_states.remove(&bot.id);
            return None;
        };
        if report.target != target || now - report.reported_at > COVER_TTL_SECONDS {
            self.cover_states.remove(&bot.id);
            return None;
        }
        let mut usable: Vec<_> = report
            .candidates
            .into_iter()
            .filter(|candidate| {
                candidate.water < 0.5
                    && candidate.slope <= 24.0
                    && candidate.enemy_occlusion >= 0.45
                    && candidate.peek_feasible
                    && candidate.escape_feasible
                    && candidate.peek_position.is_some()
            })
            .collect();
        usable.sort_by(|left, right| {
            cmp_f64(
                right.score(personality, urgent),
                left.score(personality, urgent),
            )
            .then_with(|| cmp_f64(left.travel_distance, right.travel_distance))
            .then_with(|| left.id.cmp(&right.id))
        });
        if usable.is_empty() {
            self.cover_states.remove(&bot.id);
            return None;
        }
        let current = self.cover_states.get(&bot.id).cloned();
        let selected = current
            .as_ref()
            .filter(|state| state.target == target && !state.refresh_candidate)
            .and_then(|state| {
                usable
                    .iter()
                    .find(|candidate| candidate.id == state.candidate_id)
                    .cloned()
            });
        let selected = selected.unwrap_or_else(|| {
            usable
                .iter()
                .find(|candidate| {
                    let reservation = cover_reservation(candidate.position);
                    !self.cover_reservations.contains(&reservation)
                })
                .cloned()
                .unwrap_or_else(|| usable[0].clone())
        });
        let state = if current.as_ref().is_some_and(|state| {
            state.target == target && !state.refresh_candidate && state.candidate_id == selected.id
        }) {
            current.unwrap()
        } else {
            CoverState {
                target,
                candidate_id: selected.id.clone(),
                phase: CoverPhase::Approach,
                phase_until: 0.0,
                refresh_candidate: false,
            }
        };
        self.cover_reservations
            .insert(cover_reservation(selected.position));
        self.cover_states.insert(bot.id, state.clone());
        Some((selected, state))
    }

    fn apply_cover_order(
        &mut self,
        order: &mut Order,
        bot: &Bot,
        focus: &Contact,
        personality: Personality,
        now: f64,
        urgent: bool,
        hold_only: bool,
    ) -> bool {
        let Some((candidate, mut state)) =
            self.cover_candidate(bot, focus, personality, now, urgent || hold_only)
        else {
            return false;
        };
        let cover = candidate.position;
        let peek = candidate
            .peek_position
            .expect("usable candidates have peek points");
        let cover_distance = bot.state.point().distance_xz(cover);
        let peek_distance = bot.state.point().distance_xz(peek);
        if (urgent || hold_only) && state.phase == CoverPhase::Peek {
            state.phase = CoverPhase::Return;
            state.phase_until = 0.0;
        }
        if matches!(state.phase, CoverPhase::Approach | CoverPhase::Return) && cover_distance <= 4.5
        {
            let completed_return = state.phase == CoverPhase::Return;
            state.phase = CoverPhase::Hold;
            state.refresh_candidate = completed_return;
            state.phase_until = now + 0.65 + personality.patience * 1.35;
        } else if state.phase == CoverPhase::Hold
            && !urgent
            && !hold_only
            && now >= state.phase_until
        {
            state.phase = CoverPhase::Peek;
            state.phase_until = 0.0;
        } else if state.phase == CoverPhase::Peek && peek_distance <= 4.5 {
            if state.phase_until <= 0.0 {
                state.phase_until = now + 1.0 + personality.aggression * 1.8;
            } else if now >= state.phase_until {
                state.phase = CoverPhase::Return;
                state.phase_until = 0.0;
            }
        }
        order.cover_id = Some(candidate.id);
        match state.phase {
            CoverPhase::Approach => {
                order.combat_mode = "take_cover".to_owned();
                order.move_position = cover;
                order.throttle_override = Some(0.72);
            }
            CoverPhase::Hold => {
                order.combat_mode = "cover_hold".to_owned();
                order.move_position = cover;
                order.throttle_override = Some(0.0);
            }
            CoverPhase::Peek => {
                order.combat_mode = "cover_peek".to_owned();
                order.move_position = peek;
                order.throttle_override = Some(if peek_distance > 4.5 { 0.56 } else { 0.0 });
            }
            CoverPhase::Return => {
                order.combat_mode = "cover_return".to_owned();
                order.move_position = cover;
                order.throttle_override = None;
            }
        }
        self.cover_states.insert(bot.id, state);
        true
    }

    fn set_target(
        order: &mut Order,
        bot: &Bot,
        contact: &Contact,
        personality: Personality,
        fire_allowed: bool,
    ) {
        order.target_id = Some(contact.id);
        order.target_kind = Some(contact.target_kind);
        order.aim_position = Some(contact.position);
        order.face_position = Some(contact.position);
        order.fire_allowed = fire_allowed;
        order.shell_index = Self::shell_index(&bot.profile, contact, personality, &bot.state);
    }

    fn apply_base_defense_order(&mut self, order: &mut Order, bot: &Bot, base: &DefenseResponder) {
        self.engage_anchors.remove(&bot.id);
        self.cover_states.remove(&bot.id);
        order.combat_mode = "base_defense".to_owned();
        order.defense_base_id = Some(base.base_id.clone());
        order.move_position = base.point;
        order.throttle_override = None;
        order.route_join = false;
        if order.target_id.is_none() {
            order.face_position = Some(base.point);
        }
    }

    fn apply_artillery_order(
        &mut self,
        order: &mut Order,
        bot: &Bot,
        team_axis: Option<(Point, Point)>,
    ) {
        self.engage_anchors.remove(&bot.id);
        self.cover_states.remove(&bot.id);
        let anchor = self.artillery_anchor(bot, team_axis);
        let arrived = bot.state.point().distance_xz(anchor.point) <= 15.0;
        order.combat_mode = if arrived {
            "artillery_hold".to_owned()
        } else {
            "artillery_deploy".to_owned()
        };
        order.move_position = anchor.point;
        order.route_index = anchor.index;
        order.route_anchor = anchor.point;
        order.route_join = false;
        order.throttle_override = arrived.then_some(0.0);
        if order.target_id.is_none() {
            order.face_position = Some(anchor.face);
        }
    }

    fn angled_face_point(bot: &Bot, target: Point, personality: Personality) -> (Point, f64) {
        let dx = target.x - bot.state.x;
        let dz = target.z - bot.state.z;
        if dx.hypot(dz) <= 0.1 {
            return (target, 0.0);
        }
        let mut degrees = 12.0 + personality.caution * 18.0;
        if bot.id % 2 != 0 {
            degrees = -degrees;
        }
        let radians = degrees.to_radians();
        let cosine = radians.cos();
        let sine = radians.sin();
        (
            Point::loose(Some(&json!({
                "x": bot.state.x + dx * cosine - dz * sine,
                "y": bot.state.y,
                "z": bot.state.z + dx * sine + dz * cosine,
            }))),
            round_to(degrees, 3),
        )
    }

    fn may_angle_hull(profile: &Profile) -> bool {
        matches!(
            profile.class_tag.as_str(),
            "heavyTank" | "mediumTank" | "lightTank"
        ) && (profile.armor >= 60.0 || profile.role("brawler") >= 0.55)
    }

    fn apply_stationary_angling(order: &mut Order, bot: &Bot, personality: Personality) {
        if order.combat_mode != "engage"
            || order.throttle_override != Some(0.0)
            || !order.fire_allowed
            || !Self::may_angle_hull(&bot.profile)
        {
            return;
        }
        let Some(target) = order.aim_position else {
            return;
        };
        let (point, degrees) = Self::angled_face_point(bot, target, personality);
        if degrees.abs() > 0.01 {
            order.face_position = Some(point);
            order.hull_angle_degrees = Some(degrees);
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn order_for(
        &mut self,
        bot: &Bot,
        _index: usize,
        _count: usize,
        focus: Option<&Contact>,
        contacts: &[Contact],
        now: f64,
        travel_override: Option<&DefenseResponder>,
        team_axis: Option<(Point, Point)>,
        team_bots: &[Bot],
        capture_target: Option<&CaptureTarget>,
        capture_selected: bool,
    ) -> Order {
        let capture_screen = capture_target.is_some()
            && contacts.is_empty()
            && !capture_selected
            && bot.profile.class_tag != "SPG";
        let route = self.route_for(bot, capture_screen);
        let capture_staged = self.capture_staged(bot, route.index);
        let retreat_point = self.retreat_point(bot, route.anchor);
        let desired_range = bot.profile.desired_range.max(10.0);
        let fire_range = bot.profile.fire_range.max(desired_range);
        let personality = Self::personality(bot.id);
        let health_fraction = bot.state.health / bot.state.max_health.max(1.0);
        let low_health = health_fraction <= LOW_HEALTH_BASE_FRACTION + personality.caution * 0.18;
        let recent_hit = self.recent_hit(bot.id, now);
        let threat_contact = self.recent_threat_contact(bot, contacts, now);
        let mut order = Order {
            id: bot.id,
            team: bot.team,
            target_id: None,
            target_kind: None,
            aim_position: None,
            face_position: None,
            move_position: route.point,
            fire_allowed: false,
            combat_mode: "route".to_owned(),
            throttle_override: None,
            desired_range: round_to(desired_range, 3),
            fire_range: round_to(fire_range, 3),
            route_id: route.route_id,
            route_index: route.index,
            route_anchor: route.anchor,
            route_join: route.route_join,
            personality,
            profile: bot.profile.raw.clone(),
            shell_index: 0,
            cover_id: None,
            defense_base_id: None,
            hull_angle_degrees: None,
        };
        if let Some(base) = travel_override {
            if let Some(focus) = focus {
                Self::set_target(
                    &mut order,
                    bot,
                    focus,
                    personality,
                    focus.visible && focus.shootable_by_bot_ids.contains(&bot.id),
                );
            }
            self.apply_base_defense_order(&mut order, bot, base);
            return order;
        }
        if capture_selected && capture_staged {
            let target = capture_target.expect("selected capture squad has a target");
            self.engage_anchors.remove(&bot.id);
            self.cover_states.remove(&bot.id);
            order.aim_position = Some(target.point);
            order.face_position = Some(target.point);
            order.move_position = target.point;
            order.combat_mode = "route".to_owned();
            order.throttle_override = None;
            return order;
        }
        if bot.profile.class_tag == "SPG" {
            if let Some(focus) = focus {
                Self::set_target(
                    &mut order,
                    bot,
                    focus,
                    personality,
                    focus.visible && focus.shootable_by_bot_ids.contains(&bot.id),
                );
            }
            self.apply_artillery_order(&mut order, bot, team_axis);
            return order;
        }
        let Some(focus) = focus else {
            self.engage_anchors.remove(&bot.id);
            if capture_screen
                && capture_staged
                && bot.state.point().distance_xz(order.move_position) <= 15.0
            {
                order.combat_mode = "support_hold".to_owned();
                order.face_position = capture_target.map(|target| target.point);
                order.throttle_override = Some(0.0);
                return order;
            }
            if let Some(threat) = threat_contact.as_ref() {
                Self::set_target(
                    &mut order,
                    bot,
                    threat,
                    personality,
                    threat.visible && threat.shootable_by_bot_ids.contains(&bot.id),
                );
                if self.apply_cover_order(
                    &mut order,
                    bot,
                    threat,
                    personality,
                    now,
                    true,
                    low_health,
                ) {
                    return order;
                }
            }
            self.cover_states.remove(&bot.id);
            if low_health {
                order.combat_mode = "low_health_retreat".to_owned();
                order.move_position = retreat_point;
            } else if recent_hit.is_some() {
                order.combat_mode = "under_fire_withdraw".to_owned();
                order.move_position = retreat_point;
            }
            return order;
        };
        let locally_shootable = focus.visible && focus.shootable_by_bot_ids.contains(&bot.id);
        Self::set_target(&mut order, bot, focus, personality, locally_shootable);
        let distance = bot.state.point().distance_xz(focus.position);
        let far_limit = desired_range * (1.08 + personality.caution * 0.18);
        let close_limit = desired_range * (0.48 + personality.aggression * 0.10);
        let support_score = Self::ally_support_score(bot, team_bots, focus);
        let crossfire_risk = Self::crossfire_risk(bot, contacts);
        if low_health {
            self.engage_anchors.remove(&bot.id);
            if distance <= fire_range * 1.15
                && self.apply_cover_order(&mut order, bot, focus, personality, now, true, true)
            {
                return order;
            }
            self.cover_states.remove(&bot.id);
            order.combat_mode = "low_health_retreat".to_owned();
            order.move_position = retreat_point;
            order.throttle_override = None;
            return order;
        }
        if recent_hit.is_some() {
            let cover_focus = threat_contact.as_ref().unwrap_or(focus);
            Self::set_target(
                &mut order,
                bot,
                cover_focus,
                personality,
                cover_focus.visible && cover_focus.shootable_by_bot_ids.contains(&bot.id),
            );
            self.engage_anchors.remove(&bot.id);
            if self.apply_cover_order(&mut order, bot, cover_focus, personality, now, true, false) {
                return order;
            }
            self.cover_states.remove(&bot.id);
            order.combat_mode = "under_fire_withdraw".to_owned();
            order.move_position = retreat_point;
            order.throttle_override = None;
            return order;
        }
        if crossfire_risk >= 0.35 && support_score < 0.70 {
            self.engage_anchors.remove(&bot.id);
            if distance <= fire_range * 1.15
                && self.apply_cover_order(&mut order, bot, focus, personality, now, true, false)
            {
                return order;
            }
            self.cover_states.remove(&bot.id);
            order.combat_mode = "crossfire_withdraw".to_owned();
            order.move_position = retreat_point;
            order.throttle_override = None;
            return order;
        }
        if !locally_shootable {
            self.engage_anchors.remove(&bot.id);
            self.cover_states.remove(&bot.id);
            order.target_id = None;
            order.target_kind = None;
            order.aim_position = Some(route.point);
            order.face_position = Some(route.point);
            order.fire_allowed = false;
            order.combat_mode = "route".to_owned();
            order.move_position = route.point;
            order.throttle_override = None;
        } else if self.cover_states.contains_key(&bot.id)
            && distance <= fire_range * 1.15
            && self.apply_cover_order(&mut order, bot, focus, personality, now, false, false)
        {
            self.engage_anchors.remove(&bot.id);
        } else if distance > far_limit {
            self.engage_anchors.remove(&bot.id);
            self.cover_states.remove(&bot.id);
            let advance_score = personality.aggression * 0.85
                + personality.initiative * 0.30
                + support_score * (0.35 + personality.teamwork * 0.25)
                - personality.caution * 0.55;
            let threshold = if bot.profile.dominant_role == "brawler" {
                0.24
            } else {
                0.34
            };
            if advance_score >= threshold {
                order.combat_mode = "advance_contact".to_owned();
                order.move_position = focus.position;
                order.throttle_override = Some(0.72);
            } else {
                order.combat_mode = "support_hold".to_owned();
                order.move_position = bot.state.point();
                order.throttle_override = Some(0.0);
            }
        } else if distance <= fire_range * 1.15
            && self.apply_cover_order(&mut order, bot, focus, personality, now, false, false)
        {
            self.engage_anchors.remove(&bot.id);
        } else if distance < close_limit && bot.profile.dominant_role != "brawler" {
            self.engage_anchors.remove(&bot.id);
            self.cover_states.remove(&bot.id);
            order.combat_mode = "withdraw".to_owned();
            order.move_position = route.anchor;
            order.throttle_override = None;
        } else if distance <= fire_range.min((desired_range * 1.35).max(150.0)) {
            self.cover_states.remove(&bot.id);
            order.combat_mode = "engage".to_owned();
            let target = focus.key();
            let anchor = self
                .engage_anchors
                .entry(bot.id)
                .or_insert_with(|| (target, bot.state.point()));
            if anchor.0 != target {
                *anchor = (target, bot.state.point());
            }
            order.move_position = anchor.1;
            order.throttle_override = Some(0.0);
        } else if bot.profile.role("flanker") >= 0.68 && personality.initiative > 0.42 {
            self.engage_anchors.remove(&bot.id);
            order.combat_mode = "flank".to_owned();
            order.move_position = Self::flank_point(bot, focus, desired_range);
            order.throttle_override = Some(0.78);
        } else {
            order.combat_mode = "engage".to_owned();
            let target = focus.key();
            let anchor = self
                .engage_anchors
                .entry(bot.id)
                .or_insert_with(|| (target, bot.state.point()));
            if anchor.0 != target {
                *anchor = (target, bot.state.point());
            }
            order.move_position = anchor.1;
            order.throttle_override = Some(0.0);
        }
        Self::apply_stationary_angling(&mut order, bot, personality);
        order
    }
}

fn cover_reservation(point: Point) -> (i64, i64) {
    (
        round_half_even(point.x / 8.0),
        round_half_even(point.z / 8.0),
    )
}

fn round_half_even(value: f64) -> i64 {
    value.round_ties_even() as i64
}

fn best_penetration_shell<'a>(values: &[&'a Shell]) -> Option<&'a Shell> {
    values.iter().copied().max_by(|left, right| {
        cmp_f64(left.penetration, right.penetration)
            .then_with(|| cmp_f64(left.damage, right.damage))
            .then_with(|| right.index.cmp(&left.index))
    })
}

fn team_index(team: i64) -> Option<usize> {
    match team {
        1 => Some(0),
        2 => Some(1),
        _ => None,
    }
}

fn bot_id_set(value: Option<&Value>) -> BTreeSet<i64> {
    let mut result = BTreeSet::new();
    for value in value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .take(MAX_CONTACTS_PER_TEAM)
    {
        let id = integer(Some(value), 0);
        if id > 0 {
            result.insert(id);
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn route(id: &str, points: &[(f64, f64)]) -> Value {
        json!({
            "id": id,
            "waypoints": points.iter().map(|(x, z)| json!({
                "x": x, "y": 0.0, "z": z, "hold": false,
            })).collect::<Vec<_>>(),
        })
    }

    fn tactical_route_value(map_name: &str, team: u8, route_id: &str) -> Value {
        let tactical = routes_for(map_name, team)
            .unwrap()
            .iter()
            .find(|route| route.id == route_id)
            .unwrap();
        route(
            tactical.id,
            &tactical
                .waypoints
                .iter()
                .map(|point| (point.x, point.z))
                .collect::<Vec<_>>(),
        )
    }

    fn profile(class_tag: &str, roles: Value) -> Value {
        json!({
            "class_tag": class_tag,
            "speed": 12.0,
            "dominant_role": if class_tag == "SPG" { "artillery" } else { "support" },
            "desired_range": if class_tag == "SPG" { 650.0 } else { 180.0 },
            "fire_range": if class_tag == "SPG" { 1250.0 } else { 520.0 },
            "armor": 120.0,
            "roles": roles,
            "shells": [],
        })
    }

    fn bot(id: i64, team: i64, slot: i64, route: Value, class_tag: &str, roles: Value) -> Value {
        json!({
            "id": id,
            "team": team,
            "slot": slot,
            "health": 1000,
            "profile": profile(class_tag, roles),
            "route": route,
        })
    }

    fn state(id: i64, team: i64, x: f64, z: f64) -> Value {
        json!({
            "id": id,
            "team": team,
            "alive": true,
            "world_pose": true,
            "x": x,
            "y": 0.0,
            "z": z,
            "yaw": 0.0,
            "health": 1000,
            "max_health": 1000,
            "critical": {},
        })
    }

    fn contact(target_id: i64, x: f64, z: f64, observers: &[i64]) -> Value {
        json!({
            "observing_team": 1,
            "target_kind": "human",
            "target_id": target_id,
            "target_team": 2,
            "visible": true,
            "shootable_by_bot_ids": observers,
            "x": x,
            "y": 0.0,
            "z": z,
            "health": 1000,
            "max_health": 1000,
            "class_tag": "mediumTank",
        })
    }

    fn base_fixture() -> (Value, Value, Value) {
        (
            json!([bot(
                11,
                1,
                0,
                route("lane", &[(0.0, -100.0), (0.0, 100.0), (0.0, 500.0)]),
                "mediumTank",
                json!({"support": 1.0}),
            )]),
            json!([state(11, 1, 0.0, 0.0)]),
            json!([{"id": 2, "team": 2, "alive": true}]),
        )
    }

    fn order(payload: &Value, id: i64) -> &Value {
        payload["orders"]
            .as_array()
            .unwrap()
            .iter()
            .find(|order| integer(order.get("id"), 0) == id)
            .unwrap()
    }

    #[test]
    fn contact_identity_lane_evidence_and_ttl_are_authoritative() {
        let (manifest, states, players) = base_fixture();
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        let malformed = json!([{
            "observing_team": 1, "target_kind": "human",
            "target_id": 2, "target_team": 2, "visible": true,
            "x": 0.0, "y": 0.0, "z": 100.0,
        }]);
        assert_eq!(planner.report_contacts(&malformed, &known, 1.0), 0);
        let wrong_identity = json!([{
            "observing_team": 1, "target_kind": "human",
            "target_id": 2, "target_team": 1, "visible": true,
            "shootable_by_bot_ids": [11], "x": 0.0, "y": 0.0, "z": 100.0,
        }]);
        assert_eq!(planner.report_contacts(&wrong_identity, &known, 1.0), 0);
        assert_eq!(
            planner.report_contacts(&json!([contact(2, 0.0, 100.0, &[11])]), &known, 1.0),
            1
        );
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 1.0, None),
                11
            )["target_id"],
            2
        );
        assert!(order(
            &planner.build_orders(&manifest, &states, &players, 9.01, None),
            11
        )["target_id"]
            .is_null());
    }

    #[test]
    fn negative_observation_withdraws_lane_without_refreshing_last_seen() {
        let (manifest, states, players) = base_fixture();
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(&json!([contact(2, 0.0, 100.0, &[11])]), &known, 1.0);
        let hidden = json!([{
            "observing_team": 1, "target_kind": "human",
            "target_id": 2, "target_team": 2, "visible": false,
            "shootable_by_bot_ids": [],
        }]);
        assert_eq!(planner.report_contacts(&hidden, &known, 2.0), 1);
        let payload = planner.build_orders(&manifest, &states, &players, 2.0, None);
        assert!(order(&payload, 11)["target_id"].is_null());
        assert_eq!(
            planner.contacts[0][&TargetKey {
                kind: TargetKind::Human,
                id: 2
            }]
                .last_seen,
            1.0
        );
    }

    #[test]
    fn recent_attacker_preempts_target_lease_and_withdraws() {
        let (manifest, states, _) = base_fixture();
        let players = json!([
            {"id": 2, "team": 2, "alive": true},
            {"id": 3, "team": 2, "alive": true},
        ]);
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(
            &json!([contact(2, 0.0, 260.0, &[11]), contact(3, 0.0, 35.0, &[11])]),
            &known,
            1.0,
        );
        let first = planner.build_orders(&manifest, &states, &players, 1.0, None);
        assert_eq!(order(&first, 11)["target_id"], 3);
        assert!(planner.report_damage(11, "player", 2, 240, 1.1));
        let reaction = planner.build_orders(&manifest, &states, &players, 1.1, None);
        assert_eq!(order(&reaction, 11)["target_id"], 2);
        assert_eq!(order(&reaction, 11)["combat_mode"], "under_fire_withdraw");
        assert_eq!(
            order(&reaction, 11)["move_position"],
            json!({"x": 0.0, "y": 0.0, "z": -100.0})
        );
    }

    #[test]
    fn focus_limit_spreads_long_range_fire() {
        let lane = route("lane", &[(0.0, -100.0), (0.0, 100.0), (0.0, 500.0)]);
        let manifest = Value::Array(
            (11..16)
                .map(|id| {
                    bot(
                        id,
                        1,
                        id - 11,
                        lane.clone(),
                        "mediumTank",
                        json!({"support": 1.0}),
                    )
                })
                .collect(),
        );
        let states = Value::Array(
            (11..16)
                .map(|id| state(id, 1, (id - 11) as f64 * 2.0, 0.0))
                .collect(),
        );
        let players = json!([{"id": 2, "team": 2, "alive": true}]);
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(
            &json!([contact(2, 0.0, 100.0, &[11, 12, 13, 14, 15])]),
            &known,
            1.0,
        );
        let payload = planner.build_orders(&manifest, &states, &players, 1.0, None);
        assert_eq!(
            payload["orders"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|order| order["target_id"] == 2)
                .count(),
            2
        );
    }

    #[test]
    fn target_lease_absorbs_score_jitter_then_allows_a_real_switch() {
        let (manifest, states, _) = base_fixture();
        let players = json!([
            {"id": 2, "team": 2, "alive": true},
            {"id": 3, "team": 2, "alive": true},
        ]);
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(
            &json!([contact(2, 0.0, 200.0, &[11]), contact(3, 0.0, 201.0, &[11]),]),
            &known,
            1.0,
        );
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 1.0, None),
                11
            )["target_id"],
            2
        );
        let mut stronger = contact(3, 0.0, 190.0, &[11]);
        stronger["health"] = json!(1);
        planner.report_contacts(&json!([stronger]), &known, 1.5);
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 1.5, None),
                11
            )["target_id"],
            2
        );
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 3.1, None),
                11
            )["target_id"],
            3
        );
    }

    #[test]
    fn discovered_artillery_preempts_a_healthier_non_artillery_target() {
        let (manifest, states, _) = base_fixture();
        let players = json!([
            {"id": 2, "team": 2, "alive": true},
            {"id": 3, "team": 2, "alive": true},
        ]);
        let known = BotPlanner::known_targets(&states, &players);
        let mut heavy = contact(2, 0.0, 100.0, &[11]);
        heavy["class_tag"] = json!("heavyTank");
        heavy["health"] = json!(100);
        let mut artillery = contact(3, 0.0, 130.0, &[11]);
        artillery["class_tag"] = json!("SPG");
        let mut planner = BotPlanner::new();
        planner.report_contacts(&json!([heavy, artillery]), &known, 1.0);

        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 1.0, None),
                11
            )["target_id"],
            3
        );
    }

    #[test]
    fn live_target_pose_is_not_part_of_the_order_signature_before_fire_permission() {
        let first = json!({
            "id": 11,
            "target_id": 2,
            "fire_allowed": false,
            "combat_mode": "advance_contact",
            "aim_position": {"x": 1.0, "y": 0.0, "z": 2.0},
            "face_position": {"x": 1.0, "y": 0.0, "z": 2.0},
            "move_position": {"x": 1.0, "y": 0.0, "z": 2.0},
            "shell_index": 0,
        });
        let mut moved = first.clone();
        moved["aim_position"]["x"] = json!(11.0);
        moved["face_position"]["z"] = json!(22.0);
        moved["move_position"]["x"] = json!(11.0);
        assert_eq!(order_signature(&first), order_signature(&moved));

        moved["fire_allowed"] = json!(true);
        assert_ne!(order_signature(&first), order_signature(&moved));
        moved["fire_allowed"] = json!(false);
        moved["target_id"] = json!(3);
        assert_ne!(order_signature(&first), order_signature(&moved));
    }

    #[test]
    fn route_rebalance_never_uses_spg_as_donor() {
        let route_a = route("a", &[(-100.0, 0.0), (-100.0, 80.0), (-100.0, 500.0)]);
        let route_b = route("b", &[(100.0, 0.0), (100.0, 80.0), (100.0, 500.0)]);
        let manifest = json!([
            bot(
                11,
                1,
                0,
                route_a.clone(),
                "SPG",
                json!({"support": 1.0, "flanker": 1.0, "scout": 1.0})
            ),
            bot(
                12,
                1,
                1,
                route_a.clone(),
                "mediumTank",
                json!({"brawler": 1.0})
            ),
            bot(13, 1, 2, route_b, "mediumTank", json!({"support": 0.5})),
            bot(
                14,
                1,
                3,
                route_a,
                "mediumTank",
                json!({"support": 1.0, "flanker": 1.0})
            ),
        ]);
        let states = json!([
            state(11, 1, -100.0, 0.0),
            state(12, 1, -100.0, 0.0),
            state(13, 1, 100.0, 0.0),
            state(14, 1, -100.0, 0.0),
        ]);
        let players = json!([{"id": 2, "team": 2, "alive": true}]);
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(&json!([contact(2, 100.0, 250.0, &[])]), &known, 1.0);
        let payload = planner.build_orders(&manifest, &states, &players, 1.0, None);
        assert_eq!(order(&payload, 11)["route_id"], "a");
        assert_eq!(order(&payload, 13)["route_id"], "b");
        assert_eq!(
            [12, 14]
                .into_iter()
                .filter(|id| order(&payload, *id)["route_id"] == "b")
                .count(),
            1
        );
    }

    #[test]
    fn pressured_route_lease_renews_without_resetting_progress() {
        let route_a = route("a", &[(-100.0, 0.0), (-100.0, 80.0), (-100.0, 500.0)]);
        let mut route_b = route("b", &[(100.0, 0.0), (100.0, 80.0), (100.0, 500.0)]);
        route_b["capacity"] = json!(1);
        let manifest = json!([
            bot(11, 1, 0, route_b, "SPG", json!({"support": 1.0})),
            bot(
                12,
                1,
                1,
                route_a.clone(),
                "mediumTank",
                json!({"support": 1.0})
            ),
            bot(14, 1, 2, route_a, "mediumTank", json!({"support": 1.0})),
        ]);
        let states = json!([
            state(11, 1, 100.0, 0.0),
            state(12, 1, -100.0, 0.0),
            state(14, 1, -100.0, 0.0),
        ]);
        let players = json!([{"id": 2, "team": 2, "alive": true}]);
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(&json!([contact(2, 100.0, 250.0, &[])]), &known, 1.0);
        planner.build_orders(&manifest, &states, &players, 1.0, None);
        let donor_id = [12, 14]
            .into_iter()
            .find(|id| planner.route_assignments[id].route.id == "b")
            .unwrap();
        assert_eq!(planner.route_assignments[&donor_id].until, 7.0);
        let route_index = planner.route_states[&donor_id].index;
        planner.build_orders(&manifest, &states, &players, 5.0, None);
        assert_eq!(planner.route_assignments[&donor_id].route.id, "b");
        assert_eq!(planner.route_assignments[&donor_id].until, 11.0);
        assert_eq!(planner.route_states[&donor_id].index, route_index);
    }

    #[test]
    fn karelia_opening_routes_use_class_affinity_capacity_and_spg_spread() {
        let west = tactical_route_value("01_karelia", 1, "west_ridge");
        let manifest = json!([
            bot(11, 1, 0, west.clone(), "lightTank", json!({"scout": 1.0})),
            bot(12, 1, 1, west.clone(), "heavyTank", json!({"brawler": 1.0})),
            bot(13, 1, 2, west.clone(), "SPG", json!({"artillery": 1.0})),
            bot(14, 1, 3, west, "SPG", json!({"artillery": 1.0})),
        ]);
        let states = json!([
            state(11, 1, -392.0, -372.0),
            state(12, 1, -392.0, -372.0),
            state(13, 1, -392.0, -372.0),
            state(14, 1, -392.0, -372.0),
        ]);
        let mut planner = BotPlanner::new();
        planner.build_orders(&manifest, &states, &json!([]), 1.0, None);

        assert_eq!(planner.opening_routes[&11].id, "middle_road");
        assert_eq!(planner.opening_routes[&12].id, "east_shelf");
        assert_ne!(
            planner.opening_routes[&13].id,
            planner.opening_routes[&14].id
        );

        let manifest = Value::Array(
            (100..115)
                .map(|id| {
                    bot(
                        id,
                        1,
                        id - 100,
                        tactical_route_value("01_karelia", 1, "west_ridge"),
                        "mediumTank",
                        json!({"support": 1.0}),
                    )
                })
                .collect(),
        );
        let states = Value::Array((100..115).map(|id| state(id, 1, -392.0, -372.0)).collect());
        let mut planner = BotPlanner::new();
        planner.build_orders(&manifest, &states, &json!([]), 1.0, None);
        let mut counts = BTreeMap::<String, usize>::new();
        for route in planner.opening_routes.values() {
            *counts.entry(route.id.clone()).or_default() += 1;
        }
        assert_eq!(counts["west_ridge"], 5);
        assert_eq!(counts["middle_road"], 4);
        assert_eq!(counts["east_shelf"], 6);
    }

    #[test]
    fn pre_spawn_opening_selection_matches_live_class_aware_routes() {
        let west = tactical_route_value("01_karelia", 1, "west_ridge");
        let manifest = json!([
            bot(11, 1, 0, west.clone(), "lightTank", json!({"scout": 1.0})),
            bot(12, 1, 1, west.clone(), "heavyTank", json!({"brawler": 1.0})),
            bot(13, 1, 2, west.clone(), "SPG", json!({"artillery": 1.0})),
            bot(14, 1, 3, west, "SPG", json!({"artillery": 1.0})),
        ]);

        let selected = BotPlanner::opening_route_ids(&manifest).unwrap();
        assert_eq!(selected[&11], "middle_road");
        assert_eq!(selected[&12], "east_shelf");
        assert_ne!(selected[&13], selected[&14]);
    }

    #[test]
    fn bounded_capture_squad_stages_then_captures_while_others_screen() {
        let west = tactical_route_value("02_malinovka", 1, "west_lake_road");
        let manifest = json!([
            bot(
                11,
                1,
                0,
                west.clone(),
                "mediumTank",
                json!({"support": 1.0})
            ),
            bot(
                12,
                1,
                1,
                west.clone(),
                "mediumTank",
                json!({"support": 1.0})
            ),
            bot(
                13,
                1,
                2,
                west.clone(),
                "mediumTank",
                json!({"support": 1.0})
            ),
            bot(
                14,
                1,
                3,
                west.clone(),
                "mediumTank",
                json!({"support": 1.0})
            ),
            bot(15, 1, 4, west, "SPG", json!({"artillery": 1.0})),
        ]);
        let mut states = json!([
            state(11, 1, -21.0, -442.0),
            state(12, 1, -21.0, -442.0),
            state(13, 1, -21.0, -442.0),
            state(14, 1, -21.0, -442.0),
            state(15, 1, -21.0, -442.0),
        ]);
        let players = json!([]);
        let mut planner = BotPlanner::new();
        planner.build_orders(&manifest, &states, &players, 1.0, None);
        let selected = planner.base_capture[0].bot_ids.clone();
        assert_eq!(selected.len(), MAX_BASE_CAPTURERS);
        assert!(!selected.contains(&15));

        let screen_id = (11..=14).find(|id| !selected.contains(id)).unwrap();
        let screen_route = planner.opening_routes[&screen_id].clone();
        let early = screen_route.waypoints[1];
        states[(screen_id - 11) as usize]["x"] = json!(early.x);
        states[(screen_id - 11) as usize]["z"] = json!(early.z);
        planner.route_states.insert(
            screen_id,
            RouteState {
                index: 1,
                route_id: screen_route.id.clone(),
                join_index: 1,
                join_anchor: early,
            },
        );
        let before_staging = planner.build_orders(&manifest, &states, &players, 2.0, None);
        assert_ne!(
            order(&before_staging, screen_id)["combat_mode"],
            "support_hold"
        );
        assert_eq!(planner.base_capture[0].bot_ids, selected);

        let staging_index = screen_route.waypoints.len() - 2;
        let staging = screen_route.waypoints[staging_index];
        states[(screen_id - 11) as usize]["x"] = json!(staging.x);
        states[(screen_id - 11) as usize]["z"] = json!(staging.z);
        planner.route_states.insert(
            screen_id,
            RouteState {
                index: staging_index,
                route_id: screen_route.id.clone(),
                join_index: staging_index,
                join_anchor: staging,
            },
        );
        let screening = planner.build_orders(&manifest, &states, &players, 3.0, None);
        assert_eq!(order(&screening, screen_id)["combat_mode"], "support_hold");
        assert_eq!(order(&screening, screen_id)["route_index"], staging_index);
        assert_eq!(order(&screening, screen_id)["throttle_override"], 0.0);

        let capturer_id = selected[0];
        let capture_route = planner.opening_routes[&capturer_id].clone();
        let capture_staging_index = capture_route.waypoints.len() - 2;
        let capture_staging = capture_route.waypoints[capture_staging_index];
        states[(capturer_id - 11) as usize]["x"] = json!(capture_staging.x);
        states[(capturer_id - 11) as usize]["z"] = json!(capture_staging.z);
        planner.route_states.insert(
            capturer_id,
            RouteState {
                index: capture_staging_index,
                route_id: capture_route.id.clone(),
                join_index: capture_staging_index,
                join_anchor: capture_staging,
            },
        );
        let capturing = planner.build_orders(&manifest, &states, &players, 4.0, None);
        let enemy_base = tactical_map("02_malinovka").unwrap().bases[1];
        assert_eq!(order(&capturing, capturer_id)["combat_mode"], "route");
        assert_eq!(
            order(&capturing, capturer_id)["move_position"],
            json!({"x": enemy_base.x, "y": 0.0, "z": enemy_base.z})
        );
        assert!(order(&capturing, capturer_id)["throttle_override"].is_null());

        states[(capturer_id - 11) as usize]["alive"] = json!(false);
        planner.build_orders(&manifest, &states, &players, 5.0, None);
        let replacement = &planner.base_capture[0].bot_ids;
        assert_eq!(replacement.len(), MAX_BASE_CAPTURERS);
        assert!(!replacement.contains(&capturer_id));
        assert!(!replacement.contains(&15));
        assert!(selected[1..].iter().all(|id| replacement.contains(id)));
    }

    #[test]
    fn donated_capture_bases_override_static_tactical_coordinates() {
        let west = tactical_route_value("02_malinovka", 1, "west_lake_road");
        let manifest = json!([bot(11, 1, 0, west, "mediumTank", json!({"support": 1.0}))]);
        let states = json!([state(11, 1, -21.0, -442.0)]);
        let bots = BotPlanner::alive_bots(&manifest, &states);
        let mut planner = BotPlanner::new();
        let static_target = planner.capture_target(1, &bots).unwrap();
        assert!(planner.install_capture_bases("02_malinovka", [[-20.0, -400.0], [37.25, 388.5]],));
        let donated = planner.capture_target(1, &bots).unwrap();
        assert_ne!(donated.point, static_target.point);
        assert_eq!(donated.id, "native:02_malinovka:2");
        assert_eq!(donated.point.x, 37.25);
        assert_eq!(donated.point.z, 388.5);
    }

    #[test]
    fn base_defenders_are_near_fast_and_stable() {
        let specs = [
            (11, 90.0, 10.0),
            (12, 180.0, 22.0),
            (13, 60.0, 5.0),
            (14, 400.0, 22.0),
        ];
        let mut manifest = Vec::new();
        let mut states = Vec::new();
        for (id, x, speed) in specs {
            let mut entry = bot(
                id,
                1,
                id - 11,
                route(&format!("lane-{id}"), &[(x, 0.0), (x, 300.0)]),
                "mediumTank",
                json!({}),
            );
            entry["profile"]["speed"] = json!(speed);
            manifest.push(entry);
            states.push(state(id, 1, x, 0.0));
        }
        let manifest = Value::Array(manifest);
        let mut states = Value::Array(states);
        let players = json!([]);
        let mut defense = json!({
            "bases": {"1": [{"id": "1:0", "x": 0.0, "y": 0.0, "z": 0.0}]},
            "states": {"1": {"time_left": 40.0, "invaders": 2}},
            "contributors": {"1": []},
        });
        let mut planner = BotPlanner::new();
        let first = planner.build_orders(&manifest, &states, &players, 1.0, Some(&defense));
        let defenders: Vec<_> = first["orders"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|order| order["combat_mode"] == "base_defense")
            .map(|order| integer(order.get("id"), 0))
            .collect();
        assert_eq!(defenders, vec![11, 12]);
        states[2]["x"] = json!(5.0);
        defense["states"]["1"]["invaders"] = json!(1);
        let again = planner.build_orders(&manifest, &states, &players, 2.0, Some(&defense));
        let defenders: Vec<_> = again["orders"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|order| order["combat_mode"] == "base_defense")
            .map(|order| integer(order.get("id"), 0))
            .collect();
        assert_eq!(defenders, vec![11, 12]);
    }

    #[test]
    fn base_defense_clear_grace_and_crippled_replacement_are_stable() {
        let lane = route("lane", &[(0.0, 0.0), (0.0, 300.0)]);
        let manifest = json!([
            bot(11, 1, 0, lane.clone(), "mediumTank", json!({})),
            bot(12, 1, 1, lane.clone(), "mediumTank", json!({})),
            bot(13, 1, 2, lane.clone(), "mediumTank", json!({})),
            bot(14, 1, 3, lane, "mediumTank", json!({})),
        ]);
        let mut states = json!([
            state(11, 1, 30.0, 0.0),
            state(12, 1, 60.0, 0.0),
            state(13, 1, 90.0, 0.0),
            state(14, 1, 120.0, 0.0),
        ]);
        let players = json!([]);
        let mut defense = json!({
            "bases": {"1": [{"id": "1:0", "x": 0.0, "y": 0.0, "z": 0.0}]},
            "states": {"1": {"time_left": 30.0, "invaders": 2}},
        });
        let mut planner = BotPlanner::new();
        let first = planner.build_orders(&manifest, &states, &players, 1.0, Some(&defense));
        let defender_ids = |payload: &Value| {
            payload["orders"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|order| order["combat_mode"] == "base_defense")
                .map(|order| integer(order.get("id"), 0))
                .collect::<Vec<_>>()
        };
        assert_eq!(defender_ids(&first), vec![11, 12]);
        states[0]["critical"] = json!({"destroyed": ["leftTrackHealth"]});
        let replaced = planner.build_orders(&manifest, &states, &players, 1.5, Some(&defense));
        assert_eq!(defender_ids(&replaced), vec![12, 13]);
        defense["states"]["1"]["invaders"] = json!(0);
        assert_eq!(
            defender_ids(&planner.build_orders(&manifest, &states, &players, 2.0, Some(&defense))),
            vec![12, 13]
        );
        assert_eq!(
            defender_ids(&planner.build_orders(&manifest, &states, &players, 4.9, Some(&defense))),
            vec![12, 13]
        );
        assert!(defender_ids(&planner.build_orders(
            &manifest,
            &states,
            &players,
            5.0,
            Some(&defense)
        ))
        .is_empty());
    }

    #[test]
    fn mirrored_spgs_choose_stable_rear_anchors() {
        let manifest = json!([
            bot(
                11,
                1,
                0,
                route(
                    "field",
                    &[(0.0, 0.0), (0.0, 60.0), (0.0, 160.0), (0.0, 600.0)]
                ),
                "SPG",
                json!({})
            ),
            bot(
                26,
                2,
                0,
                route(
                    "field",
                    &[(0.0, 600.0), (0.0, 540.0), (0.0, 440.0), (0.0, 0.0)]
                ),
                "SPG",
                json!({})
            ),
        ]);
        let mut states = json!([state(11, 1, 0.0, 0.0), state(26, 2, 0.0, 600.0)]);
        let players = json!([]);
        let mut planner = BotPlanner::new();
        let deploying = planner.build_orders(&manifest, &states, &players, 1.0, None);
        assert_eq!(order(&deploying, 11)["route_index"], 1);
        assert_eq!(order(&deploying, 26)["route_index"], 1);
        assert_eq!(order(&deploying, 11)["move_position"]["z"], 60.0);
        assert_eq!(order(&deploying, 26)["move_position"]["z"], 540.0);
        states[0]["z"] = json!(60.0);
        states[1]["z"] = json!(540.0);
        let holding = planner.build_orders(&manifest, &states, &players, 2.0, None);
        assert_eq!(order(&holding, 11)["combat_mode"], "artillery_hold");
        assert_eq!(order(&holding, 26)["combat_mode"], "artillery_hold");
    }

    #[test]
    fn cover_state_holds_during_recent_hit_then_peeks() {
        let (manifest, mut states, players) = base_fixture();
        let known_targets = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(&json!([contact(2, 0.0, 260.0, &[11])]), &known_targets, 1.0);
        let known_bots = BotPlanner::known_bots(&manifest, &states);
        assert_eq!(
            planner.report_affordances(
                &json!([{
                    "bot_id": 11, "target_kind": "human", "target_id": 2,
                    "candidates": [{
                        "id": "rock", "position": {"x": 12.0, "y": 0.0, "z": 0.0},
                        "peek_position": {"x": 18.0, "y": 0.0, "z": 4.0},
                        "travel_distance": 12.0, "route_alignment": 0.8,
                        "enemy_occlusion": 0.9, "exposure": 0.1, "slope": 1.0,
                        "water": 0.0, "ally_congestion": 0.0,
                        "peek_feasible": true, "escape_feasible": true,
                    }],
                }]),
                &known_bots,
                &known_targets,
                1.0
            ),
            1
        );
        planner.report_damage(11, "player", 2, 200, 1.1);
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 1.1, None),
                11
            )["combat_mode"],
            "take_cover"
        );
        states[0]["x"] = json!(12.0);
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 1.2, None),
                11
            )["combat_mode"],
            "cover_hold"
        );
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 5.0, None),
                11
            )["combat_mode"],
            "cover_hold"
        );
        assert_eq!(
            order(
                &planner.build_orders(&manifest, &states, &players, 7.2, None),
                11
            )["combat_mode"],
            "cover_peek"
        );
    }

    #[test]
    fn shell_choice_matches_standard_he_premium_policy() {
        let profile = Profile::parse(Some(&json!({
            "shells": [
                {"index": 0, "kind": "ARMOR_PIERCING", "penetration": 180.0, "damage": 300.0},
                {"index": 1, "kind": "ARMOR_PIERCING_CR", "penetration": 260.0, "damage": 300.0},
                {"index": 2, "kind": "HIGH_EXPLOSIVE", "penetration": 60.0, "damage": 420.0}
            ]
        })));
        let personality = Personality {
            aggression: 0.5,
            ..BotPlanner::personality(11)
        };
        let mut state = BotState {
            ammo_remaining: Some(vec![30, 20, 10]),
            ..BotState::default()
        };
        let make_contact = |armor: f64| Contact {
            id: 2,
            target_kind: TargetKind::Human,
            team: 2,
            visible: true,
            last_seen: 1.0,
            position: Point::default(),
            health: 1000,
            max_health: 1000,
            class_tag: "mediumTank".to_owned(),
            armor,
            shootable_by_bot_ids: BTreeSet::new(),
        };
        assert_eq!(
            BotPlanner::shell_index(&profile, &make_contact(150.0), personality, &state),
            0
        );
        assert_eq!(
            BotPlanner::shell_index(&profile, &make_contact(40.0), personality, &state),
            2
        );
        assert_eq!(
            BotPlanner::shell_index(&profile, &make_contact(210.0), personality, &state),
            1
        );
        state.ammo_remaining = Some(vec![30, 0, 10]);
        assert_eq!(
            BotPlanner::shell_index(&profile, &make_contact(210.0), personality, &state),
            0
        );
    }

    #[test]
    fn contact_pose_changes_do_not_churn_firing_order_revision() {
        let (manifest, states, players) = base_fixture();
        let known = BotPlanner::known_targets(&states, &players);
        let mut planner = BotPlanner::new();
        planner.report_contacts(&json!([contact(2, 0.0, 150.0, &[11])]), &known, 1.0);
        let first = planner.build_orders(&manifest, &states, &players, 1.0, None);
        planner.report_contacts(&json!([contact(2, 0.4, 150.3, &[11])]), &known, 1.1);
        let second = planner.build_orders(&manifest, &states, &players, 1.1, None);
        assert_eq!(first["revision"], second["revision"]);
        assert_ne!(
            order(&first, 11)["aim_position"],
            order(&second, 11)["aim_position"]
        );
    }

    #[test]
    fn deterministic_replay_produces_identical_orders_and_revisions() {
        let (manifest, mut states, players) = base_fixture();
        let known = BotPlanner::known_targets(&states, &players);
        let observations = [
            (1.0, contact(2, 0.0, 240.0, &[11])),
            (1.2, contact(2, 3.0, 235.0, &[11])),
            (3.4, contact(2, -4.0, 210.0, &[11])),
        ];
        let mut left = BotPlanner::new();
        let mut right = BotPlanner::new();
        for (index, (now, observation)) in observations.into_iter().enumerate() {
            if index == 2 {
                states[0]["health"] = json!(700);
            }
            for planner in [&mut left, &mut right] {
                planner.report_contacts(&json!([observation.clone()]), &known, now);
                if index == 1 {
                    planner.report_damage(11, "human", 2, 100, now);
                }
            }
            assert_eq!(
                left.build_orders(&manifest, &states, &players, now, None),
                right.build_orders(&manifest, &states, &players, now, None),
            );
        }
    }

    #[test]
    fn personality_is_stable_and_reset_restores_initial_revision() {
        assert_eq!(
            BotPlanner::personality(11),
            Personality {
                aggression: 0.38,
                caution: 0.39,
                teamwork: 0.77,
                patience: 0.70,
                initiative: 0.80,
                adaptability: 0.60,
                jiggle: 0.68,
            }
        );
        assert_ne!(BotPlanner::personality(11), BotPlanner::personality(12));
        let (manifest, states, players) = base_fixture();
        let mut planner = BotPlanner::new();
        let first = planner.build_orders(&manifest, &states, &players, 1.0, None);
        assert_eq!(first["revision"], 1);
        planner.reset();
        assert_eq!(planner.revision, 0);
        assert_eq!(
            planner.build_orders(&manifest, &states, &players, 1.0, None)["revision"],
            1
        );
    }
}
