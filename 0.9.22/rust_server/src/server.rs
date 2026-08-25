//! Single-threaded protocol-v5 waiting-room application.
//!
//! The transport owns socket threads and assigns one process-wide receive
//! sequence. This module is the only owner of mutable room state: every
//! decoded command is applied synchronously in that receive order.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Deserialize;
use serde_json::{json, Map, Value};
use thiserror::Error;

use crate::authority_runtime::{AuthorityRuntime, NativeEntityDonation};
use crate::battle::{
    BattleEngine, BattleEntityView, BattleVehicleInit, FireRuntimeView, PREBATTLE_TICKS,
    TERMINAL_TICK,
};
use crate::battle_loop::{
    BattleLoop, BattleLoopError, BattleLoopOutput, CommandEffect, PlayerBurstSnapshot,
    PlayerFireAuthorityInput,
};
use crate::bot_sim::{BotSimulator, BotSpawn, CriticalState, Vec3 as BotVec3};
use crate::client_replication::{
    encode_battle_events, encode_battle_live, encode_battle_result, encode_battle_start,
    encode_snapshot, BattleEventsFrame, BattleLive as ClientBattleLive, BattleResultFrame,
    BattleResultState, BattleStart as ClientBattleStart, BattleVehicleStatistics, BotManifestEntry,
    BotOrder, BotPersonality, BotProfile, BotRosterEntry, BotRoute, BotShellProfile,
    BotState as ClientBotState, CaptureBaseState, CombatPhase, ContactState as ClientContactState,
    CriticalRevision as ClientCriticalRevision, DestructibleKind as ClientDestructibleKind,
    DestructibleState as ClientDestructibleState, EventRoster, FrameScope, FullManifestLineage,
    PlayerState as ClientPlayerState, Point3, ProjectileWireState, RevisionPayload, RouteWaypoint,
    RulesState, SnapshotFrame, SnapshotManifest, TimingState,
};
use crate::clock::tick_offset;
use crate::combat::{BodyPose, VehicleKey, VehicleKind};
use crate::config::ServerConfig;
use crate::critical_damage::CriticalProfile;
use crate::descriptor::{parse_player_authority_loadout, ParsedDescriptor};
use crate::descriptor_exchange::{
    DescriptorExchange, DescriptorExchangeError, DescriptorGate, DestructibleGate, ExchangeEvent,
    ExchangeFailure, ExchangeStatus,
};
use crate::lan::{
    compatible_hello_protocol, effective_player_fire_factors, effective_ram_inputs, error_message,
    has_required_capabilities, parse_player_hello, roster_message, valid_effective_params,
    welcome_message, EffectiveRamInputs, CLIENT_BUILD_0922, DEFAULT_VEHICLE_0922,
    DESTRUCTIBLE_CATALOG_V5, EFFECTIVE_PARAMS_V1, HE_EXPLOSION_EVIDENCE_V1, HUMAN_RAM_TIMELINE_V1,
    LEAN_SNAPSHOT_MANIFEST_V1, MAP_POOL_0922, MODERN_CLIENT_REQUIRED_CAPABILITIES,
    NATIVE_ORACLE_V1, PLAYER_AMMO_AUTHORITY_V1, PLAYER_AUTHORITY_LOADOUT_V1, PLAYER_ENVIRONMENT_V2,
    PLAYER_FIRE_INTENT_V4, PROJECTILE_LEDGER_V2, RAM_CONTACT_LEDGER_V3, RICOCHET_CONTINUATION_V1,
    SERVER_CAPABILITIES,
};
use crate::lineup::{plan_bot_lineup, validate_exact_lineup, BotVehicleAssignments};
use crate::navgraph::NavGraph;
use crate::planner::BotPlanner;
use crate::player_ammo::{PhysicalBurstDescriptor, PlayerAmmoLedger, PlayerAmmoSnapshot};
use crate::player_environment::{LandingObservationRequest, LandingObservationResult};
use crate::player_equipment::{
    validate_bot_consumable_contracts, BotEquipmentLedger, EquipmentContract,
    PlayerEquipmentLedger, PlayerEquipmentSnapshot,
};
use crate::projectile::{ProjectileStunState, SourceShot};
use crate::protocol::{EntityRef, OracleLineage, OracleV1BatchReply, SimulationScope};
use crate::ram::{PlayerRamLedgerState, PlayerRamProjection, RamDamageProfile, RamShape};
use crate::receipt_store::{ReceiptStore, ReceiptStoreError};
use crate::result::{
    build_receipt_payloads, remaining_bot_winner, ReceiptBuildContext, RemainingBotState,
    ResultActor, ResultBuildError,
};
use crate::room::{
    BattleResult, BattleWinner, BotTierMode, EndpointIdentity, OracleSession, PlayerSession,
    ReadyOutcome, ReceiptPolicy, RoomConfig, RoomError, RoomPhase, RoomState, RoundParticipant,
    StartOutcome, Team,
};
use crate::rules::MapPoint;
use crate::tactical_maps::{
    route_for_slot, routes_for, tactical_map, validate_catalog, TacticalRoute,
};
use crate::transport::{SendHandle, TransportEvent, TransportServer};
use crate::vehicle_overlay::{VehicleOverlayError, VehicleOverlayStore, MAX_OVERLAY_LINE_BYTES};
use crate::wire::{
    ConnectionId, ConnectionRole, Hello, WireError, WireObject, LAN_PROTOCOL_VERSION,
    SIMULATION_WORKER_ROLE,
};

/// Capability advertised by the existing hidden #1513 worker.
///
/// It is admitted as a native-world oracle endpoint internally. It never
/// becomes a player, room host, or old pure-data server authority.
pub const SIMULATION_WORKER_CAPABILITY: &str = "simulation_worker_v1";
pub const SIMULATION_WORKER_ID: i64 = -1;
pub const SERVER_AUTHORITY_ID: i64 = 0;
pub const BOT_CALLSIGNS_0922: &[&str] = &[
    "暗夜猎手",
    "百步穿杨",
    "北方孤狼",
    "不服来战",
    "苍穹之刃",
    "乘风破浪",
    "赤色彗星",
    "此路不通",
    "刀锋战士",
    "东风破",
    "风卷残云",
    "风云再起",
    "钢铁洪流",
    "孤胆英雄",
    "黑色闪电",
    "横扫千军",
    "火力全开",
    "极速狂飙",
    "剑指苍穹",
    "决战到底",
    "雷霆万钧",
    "亮剑",
    "龙行天下",
    "落叶随风",
    "逆风飞翔",
    "怒海狂涛",
    "千里走单骑",
    "秋名山车神",
    "热血战魂",
    "神出鬼没",
    "铁甲雄风",
    "铁骑纵横",
    "无敌小坦克",
    "西北狼",
    "逍遥浪子",
    "一炮入魂",
    "一骑当千",
    "勇往直前",
    "战场幽灵",
    "追风少年",
];

const EVENT_POLL_INTERVAL: Duration = Duration::from_millis(25);
const ERROR_DRAIN_GRACE: Duration = Duration::from_millis(100);
const PREBATTLE_SECONDS: f64 = 15.0;
const BATTLE_DURATION_SECONDS: f64 = 900.0;
const RESULT_RESET_DELAY: Duration = Duration::from_secs(5);
const NATIVE_PREREQUISITE_TIMEOUT: Duration = Duration::from_secs(120);
// Events emitted outside a fixed tick use a reserved ordinal so they cannot
// collide with the bounded in-tick event batch already sent for that tick.
const OUT_OF_BAND_RESULT_ORDINAL: u16 = u16::MAX;

#[derive(Debug, Error)]
pub enum ServerError {
    #[error("could not bind LAN transport: {0}")]
    Bind(#[source] io::Error),
    #[error("invalid room configuration: {0}")]
    RoomConfig(#[source] RoomError),
    #[error("room state invariant failed: {0}")]
    RoomState(#[source] RoomError),
    #[error("durable battle receipt state failed: {0}")]
    ReceiptStore(#[source] ReceiptStoreError),
    #[error("battle loop failed: {0}")]
    BattleLoop(#[source] BattleLoopError),
    #[error("the native-world load barrier completed without both capture bases")]
    MissingCaptureBases,
    #[error("battle lifecycle message requires a connected room host")]
    MissingHost,
    #[error("battle result payload failed: {0}")]
    ResultBuild(#[source] ResultBuildError),
    #[error("client replication payload failed: {0}")]
    ClientReplication(#[from] crate::client_replication::ClientReplicationError),
    #[error("native descriptor exchange failed: {0}")]
    DescriptorExchange(#[from] DescriptorExchangeError),
    #[error("Rust battle authority setup failed: {0}")]
    AuthoritySetup(String),
    #[error("vehicle-data overlay setup failed: {0}")]
    VehicleOverlay(#[source] VehicleOverlayError),
    #[error(transparent)]
    Wire(#[from] WireError),
    #[error("LAN transport event stream closed")]
    EventStreamClosed,
    #[error("receive order gap: expected {expected}, received {received}")]
    ReceiveOrder { expected: u64, received: u64 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PollOutcome {
    Event,
    Timeout,
}

#[derive(Clone)]
enum EndpointSession {
    Player(PlayerSession),
    Oracle(OracleSession),
}

struct ActiveConnection {
    sender: SendHandle,
    session: EndpointSession,
}

struct PendingClose {
    sender: SendHandle,
    deadline: Instant,
}

#[derive(Clone, Debug)]
struct NativeOracleHello {
    capabilities: Vec<String>,
    generation: u64,
}

#[derive(Clone, Copy, Debug)]
struct NativeOracleConnection {
    connection_id: ConnectionId,
    generation: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct NativeWorldEntityRow {
    kind: String,
    logical_id: u32,
    native: EntityRef,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct NativeDestructibleProof {
    native_space_id: u64,
    expected_instances: u64,
    installed_instances: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct NativeWorldReadyWire {
    #[serde(rename = "type")]
    message_type: String,
    protocol: u64,
    round_id: u64,
    authority_epoch: u64,
    oracle_generation: u64,
    entity_revision: u64,
    complete: bool,
    oracle_space: EntityRef,
    entities: Vec<NativeWorldEntityRow>,
    destructibles: NativeDestructibleProof,
    bot_consumables: Vec<EquipmentContract>,
}

#[derive(Clone, Debug, PartialEq)]
struct NativeWorldReady {
    entity_revision: u64,
    donation: NativeEntityDonation,
    destructibles: NativeDestructibleProof,
    bot_consumables: Vec<EquipmentContract>,
}

#[derive(Clone, Debug)]
struct PreparedBot {
    id: u64,
    team: Team,
    slot: usize,
    name: String,
    vehicle: String,
    max_health: u32,
    pose: BodyPose,
    route: BotRoute,
    descriptor: Option<ParsedDescriptor>,
}

impl PreparedBot {
    fn client_roster(&self) -> BotRosterEntry {
        BotRosterEntry {
            id: self.id,
            team: self.team.number(),
            slot: self.slot as u8,
            name: self.name.clone(),
        }
    }

    fn client_profile(&self) -> BotProfile {
        let Some(parsed) = self.descriptor.as_ref() else {
            return BotProfile {
                class_tag: "lightTank".to_owned(),
                dominant_role: "support".to_owned(),
                roles: BTreeMap::from([("support".to_owned(), 1.0)]),
                desired_range: 100.0,
                fire_range: 500.0,
                speed: 20.0,
                armor: 1.0,
                shells: Vec::new(),
            };
        };
        let (class_tag, dominant_role, desired_range) = match parsed.profile.class {
            crate::bot_sim::VehicleClass::LightTank => ("lightTank", "scout", 140.0),
            crate::bot_sim::VehicleClass::MediumTank => ("mediumTank", "support", 180.0),
            crate::bot_sim::VehicleClass::HeavyTank => ("heavyTank", "assault", 120.0),
            crate::bot_sim::VehicleClass::TankDestroyer => ("AT-SPG", "sniper", 280.0),
            crate::bot_sim::VehicleClass::Spg => ("SPG", "artillery", 450.0),
        };
        let fire_range = parsed
            .descriptor
            .gun
            .shells
            .iter()
            .map(|shell| shell.max_distance)
            .fold(0.0_f64, f64::max);
        let shells = parsed
            .descriptor
            .gun
            .shells
            .iter()
            .map(|shell| BotShellProfile {
                index: shell.index as u8,
                kind: shell.kind.clone(),
                penetration: shell.penetration,
                damage: shell.damage,
                speed: shell.speed,
            })
            .collect();
        BotProfile {
            class_tag: class_tag.to_owned(),
            dominant_role: dominant_role.to_owned(),
            roles: BTreeMap::from([(dominant_role.to_owned(), 1.0)]),
            desired_range,
            fire_range,
            speed: parsed.descriptor.physics.forward_speed_limit,
            armor: 1.0,
            shells,
        }
    }

    fn client_route(&self) -> BotRoute {
        self.route.clone()
    }

    fn client_manifest(&self) -> BotManifestEntry {
        BotManifestEntry {
            id: self.id,
            team: self.team.number(),
            slot: self.slot as u8,
            name: self.name.clone(),
            vehicle: self.vehicle.clone(),
            max_health: self.max_health,
            health: self.max_health,
            profile: self.client_profile(),
            route: self.client_route(),
        }
    }

    fn client_order(&self) -> BotOrder {
        let position = Point3 {
            x: self.pose.x,
            y: self.pose.y,
            z: self.pose.z,
        };
        BotOrder {
            id: self.id,
            team: self.team.number(),
            target_id: None,
            target_kind: None,
            aim_position: None,
            face_position: None,
            move_position: position,
            fire_allowed: false,
            combat_mode: "hold".to_owned(),
            throttle_override: Some(0.0),
            desired_range: 100.0,
            fire_range: 500.0,
            route_id: format!("hold-{}", self.id),
            route_index: 0,
            route_anchor: position,
            route_join: false,
            personality: BotPersonality {
                aggression: 0.5,
                caution: 0.5,
                teamwork: 0.5,
                patience: 0.5,
                initiative: 0.5,
                adaptability: 0.5,
                jiggle: 0.0,
            },
            profile: self.client_profile(),
            shell_index: 0,
            cover_id: None,
            defense_base_id: None,
            hull_angle_degrees: None,
        }
    }
}

fn prepared_route_pose(route: &'static TacticalRoute, team: Team) -> (BodyPose, BotRoute) {
    let first = route
        .waypoints
        .first()
        .expect("the tactical catalog rejects empty routes");
    let yaw = route
        .waypoints
        .iter()
        .skip(1)
        .find_map(|waypoint| {
            let dx = waypoint.x - first.x;
            let dz = waypoint.z - first.z;
            (dx.hypot(dz) > 0.001).then(|| dx.atan2(dz))
        })
        .unwrap_or(if team == Team::One {
            0.0
        } else {
            std::f64::consts::PI
        });
    let pose = BodyPose {
        x: first.x,
        y: 0.0,
        z: first.z,
        yaw,
        pitch: 0.0,
        roll: 0.0,
        speed: 0.0,
        aim_yaw: yaw,
        gun_pitch: 0.0,
    };
    let route = BotRoute {
        id: route.id.to_owned(),
        waypoints: route
            .waypoints
            .iter()
            .map(|waypoint| RouteWaypoint {
                x: waypoint.x,
                y: 0.0,
                z: waypoint.z,
                hold: waypoint.hold,
            })
            .collect(),
    };
    (pose, route)
}

fn navigation_spawn_pose(graph: &NavGraph, graph_team: u8, slot: usize) -> Option<BodyPose> {
    let pose = graph.spawn_pose(graph_team, slot)?;
    Some(BodyPose {
        x: pose.x,
        y: pose.y,
        z: pose.z,
        yaw: pose.yaw,
        pitch: 0.0,
        roll: 0.0,
        speed: 0.0,
        aim_yaw: pose.yaw,
        gun_pitch: 0.0,
    })
}

#[derive(Debug)]
struct PreparedRound {
    scope: SimulationScope,
    map_name: String,
    navigation_graph: NavGraph,
    spawn_team_mapping: [u8; 2],
    participants: Vec<RoundParticipant>,
    bots: Vec<PreparedBot>,
    descriptors: BTreeMap<String, ParsedDescriptor>,
    capture_bases: Option<[Vec<MapPoint>; 2]>,
    frozen_result_actors: BTreeMap<VehicleKey, ResultActor>,
    final_result_snapshot: Option<FrozenResultSnapshot>,
}

#[derive(Clone, Debug)]
struct FrozenResultSnapshot {
    actors: Vec<ResultActor>,
    active_player_ids: BTreeSet<u32>,
    statistics: crate::statistics::StatisticsLedger,
    duration_ticks: u64,
    arena_unique_id: u64,
}

impl PreparedRound {
    fn new(
        outcome: StartOutcome,
        config: &RoomConfig,
        map: &str,
        assignments: &BotVehicleAssignments,
        navigation_graph: NavGraph,
    ) -> Result<Self, ServerError> {
        if navigation_graph.map_name() != map {
            return Err(ServerError::AuthoritySetup(
                "selected navigation graph does not match the round map".to_owned(),
            ));
        }
        let tactical = tactical_map(map).ok_or_else(|| {
            ServerError::AuthoritySetup(format!("map {map:?} has no tactical metadata"))
        })?;
        let spawn_team_mapping = navigation_graph
            .resolve_team_mapping([
                [tactical.bases[0].x, tactical.bases[0].z],
                [tactical.bases[1].x, tactical.bases[1].z],
            ])
            .map_err(|error| ServerError::AuthoritySetup(error.to_string()))?;
        let mut bots = Vec::new();
        for team in [Team::One, Team::Two] {
            for slot in 0..config.team_capacity(team) {
                if outcome
                    .participants
                    .iter()
                    .any(|participant| participant.team == team && participant.slot == slot)
                {
                    continue;
                }
                let id = match team {
                    Team::One => slot as u64 + 1,
                    Team::Two => slot as u64 + 16,
                };
                let route = route_for_slot(map, team.number(), slot).ok_or_else(|| {
                    ServerError::AuthoritySetup(format!(
                        "map {map:?} has no tactical route for team {} slot {slot}",
                        team.number()
                    ))
                })?;
                let (_, client_route) = prepared_route_pose(route, team);
                let pose = navigation_spawn_pose(
                    &navigation_graph,
                    spawn_team_mapping[usize::from(team.number() - 1)],
                    slot,
                )
                .ok_or_else(|| {
                    ServerError::AuthoritySetup(format!(
                        "map {map:?} has no navigation spawn for team {} slot {slot}",
                        team.number()
                    ))
                })?;
                let vehicle = assignments
                    .get(&(team.number(), slot as u8))
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "canonical lineup is missing team {} slot {slot}",
                            team.number()
                        ))
                    })?
                    .clone();
                bots.push(PreparedBot {
                    id,
                    team,
                    slot,
                    name: format!(
                        "{}-{:02}",
                        BOT_CALLSIGNS_0922[(id as usize - 1) % BOT_CALLSIGNS_0922.len()],
                        10 + id
                    ),
                    vehicle,
                    max_health: 1_000,
                    pose,
                    route: client_route,
                    descriptor: None,
                });
            }
        }
        Ok(Self {
            scope: outcome.scope,
            map_name: map.to_owned(),
            navigation_graph,
            spawn_team_mapping,
            participants: outcome.participants,
            bots,
            descriptors: BTreeMap::new(),
            capture_bases: None,
            frozen_result_actors: BTreeMap::new(),
            final_result_snapshot: None,
        })
    }

    fn accept_capture_bases(&mut self, bases: [Vec<MapPoint>; 2]) -> Result<(), &'static str> {
        for (server_index, graph_team) in self.spawn_team_mapping.into_iter().enumerate() {
            let expected = self
                .navigation_graph
                .objective_base(graph_team)
                .ok_or("mapped navigation objective is missing")?;
            let [donated] = bases[server_index].as_slice() else {
                return Err("capture base donation does not match the navigation objective");
            };
            if (donated.x - expected[0]).hypot(donated.z - expected[1]) > 1.0 {
                return Err("capture base donation does not match the navigation objective");
            }
        }
        if self
            .capture_bases
            .as_ref()
            .is_some_and(|known| known != &bases)
        {
            return Err("capture bases conflict with the accepted native-world donation");
        }
        self.capture_bases = Some(bases);
        Ok(())
    }

    fn spawn_pose(&self, team: Team, slot: usize) -> Result<BodyPose, ServerError> {
        navigation_spawn_pose(
            &self.navigation_graph,
            self.spawn_team_mapping[usize::from(team.number() - 1)],
            slot,
        )
        .ok_or_else(|| {
            ServerError::AuthoritySetup(format!(
                "map {:?} has no navigation spawn for team {} slot {slot}",
                self.map_name,
                team.number()
            ))
        })
    }

    fn required_vehicle_names(&self) -> BTreeSet<String> {
        self.participants
            .iter()
            .map(|participant| participant.vehicle.clone())
            .chain(self.bots.iter().map(|bot| bot.vehicle.clone()))
            .collect()
    }

    fn install_descriptors(
        &mut self,
        descriptors: &BTreeMap<String, ParsedDescriptor>,
    ) -> Result<(), &'static str> {
        for participant in &mut self.participants {
            let descriptor = descriptors
                .get(&participant.vehicle)
                .ok_or("a round participant descriptor is missing")?;
            participant.max_health = descriptor.descriptor.max_health;
        }
        for bot in &mut self.bots {
            let descriptor = descriptors
                .get(&bot.vehicle)
                .ok_or("a prepared bot descriptor is missing")?
                .clone();
            bot.max_health = descriptor.descriptor.max_health;
            bot.descriptor = Some(descriptor);
        }
        self.descriptors = descriptors.clone();
        if self.bots.is_empty() {
            return Ok(());
        }
        let manifest = serde_json::to_value(
            self.bots
                .iter()
                .map(PreparedBot::client_manifest)
                .collect::<Vec<_>>(),
        )
        .map_err(|_| "the prepared planner manifest cannot be serialized")?;
        let opening = BotPlanner::opening_route_ids(&manifest)
            .ok_or("class-aware opening routes cannot be selected")?;
        for bot in &mut self.bots {
            let route_id = opening
                .get(&bot.id)
                .ok_or("a prepared bot has no opening route")?;
            let route = routes_for(&self.map_name, bot.team.number())
                .and_then(|routes| routes.iter().find(|route| route.id == route_id))
                .ok_or("a selected opening route is not in the tactical catalog")?;
            let (_, client_route) = prepared_route_pose(route, bot.team);
            bot.route = client_route;
        }
        Ok(())
    }

    fn build_bot_simulators(&self) -> Result<Vec<BotSimulator>, ServerError> {
        self.bots
            .iter()
            .map(|bot| {
                let parsed = bot.descriptor.as_ref().ok_or_else(|| {
                    ServerError::AuthoritySetup(format!(
                        "bot {} has no installed vehicle descriptor",
                        bot.id
                    ))
                })?;
                let id = u32::try_from(bot.id).map_err(|_| {
                    ServerError::AuthoritySetup(format!(
                        "bot {} exceeds the simulation identity range",
                        bot.id
                    ))
                })?;
                let mut simulator = BotSimulator::new(
                    parsed.descriptor.clone(),
                    parsed.profile.clone(),
                    BotSpawn {
                        id,
                        team: bot.team.number(),
                        round_id: self.scope.round_id,
                        tick: 0,
                        position: BotVec3::new(bot.pose.x, bot.pose.y, bot.pose.z),
                        yaw: bot.pose.yaw,
                        pitch: bot.pose.pitch,
                        roll: bot.pose.roll,
                        health: bot.max_health,
                        fire_seq: 0,
                        critical: CriticalState::default(),
                    },
                )
                .map_err(|error| {
                    ServerError::AuthoritySetup(format!(
                        "bot {} simulation initialization failed: {error}",
                        bot.id
                    ))
                })?;
                simulator
                    .install_physical_burst(parsed.physical_burst)
                    .map_err(|error| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} physical-burst descriptor installation failed: {error}",
                            bot.id
                        ))
                    })?;
                Ok(simulator)
            })
            .collect()
    }

    fn mounted_shots(&self) -> Result<BTreeMap<VehicleKey, BTreeMap<u8, SourceShot>>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                self.descriptors
                    .get(&participant.vehicle)
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Player,
                                id: u64::from(participant.player_id),
                            },
                            descriptor.mounted_shots.clone(),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has no installed vehicle descriptor",
                            participant.player_id
                        ))
                    })
            })
            .chain(self.bots.iter().map(|bot| {
                bot.descriptor
                    .as_ref()
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Bot,
                                id: bot.id,
                            },
                            descriptor.mounted_shots.clone(),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} has no installed vehicle descriptor",
                            bot.id
                        ))
                    })
            }))
            .collect()
    }

    fn vehicle_extents(&self) -> Result<BTreeMap<VehicleKey, (f64, f64)>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                self.descriptors
                    .get(&participant.vehicle)
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Player,
                                id: u64::from(participant.player_id),
                            },
                            (
                                descriptor.descriptor.half_length,
                                descriptor.descriptor.half_width,
                            ),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has no installed vehicle descriptor",
                            participant.player_id
                        ))
                    })
            })
            .chain(self.bots.iter().map(|bot| {
                bot.descriptor
                    .as_ref()
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Bot,
                                id: bot.id,
                            },
                            (
                                descriptor.descriptor.half_length,
                                descriptor.descriptor.half_width,
                            ),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} has no installed vehicle descriptor",
                            bot.id
                        ))
                    })
            }))
            .collect()
    }

    fn vehicle_ram_shapes(&self) -> Result<BTreeMap<VehicleKey, RamShape>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                self.descriptors
                    .get(&participant.vehicle)
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Player,
                                id: u64::from(participant.player_id),
                            },
                            descriptor.ram_shape,
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has no installed vehicle descriptor",
                            participant.player_id
                        ))
                    })
            })
            .chain(self.bots.iter().map(|bot| {
                bot.descriptor
                    .as_ref()
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Bot,
                                id: bot.id,
                            },
                            descriptor.ram_shape,
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} has no installed vehicle descriptor",
                            bot.id
                        ))
                    })
            }))
            .collect()
    }

    fn vehicle_masses(&self) -> Result<BTreeMap<VehicleKey, f64>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                let inputs = configuration_effective_ram_inputs(&participant.vehicle_configuration)
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} did not donate exact effective ramming inputs",
                            participant.player_id
                        ))
                    })?;
                Ok((
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: u64::from(participant.player_id),
                    },
                    inputs.mass,
                ))
            })
            .chain(self.bots.iter().map(|bot| {
                bot.descriptor
                    .as_ref()
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Bot,
                                id: bot.id,
                            },
                            descriptor.descriptor.physics.mass,
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} has no installed vehicle descriptor",
                            bot.id
                        ))
                    })
            }))
            .collect()
    }

    fn vehicle_ram_profiles(&self) -> Result<BTreeMap<VehicleKey, RamDamageProfile>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                let inputs = configuration_effective_ram_inputs(&participant.vehicle_configuration)
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} did not donate exact effective ramming inputs",
                            participant.player_id
                        ))
                    })?;
                let profile = RamDamageProfile::new(inputs.spall_coefficient, inputs.ramming_bonus)
                    .map_err(|error| {
                        ServerError::AuthoritySetup(format!(
                            "player {} ramming profile is invalid: {error}",
                            participant.player_id
                        ))
                    })?;
                Ok((
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: u64::from(participant.player_id),
                    },
                    profile,
                ))
            })
            .chain(self.bots.iter().map(|bot| {
                bot.descriptor
                    .as_ref()
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Bot,
                                id: bot.id,
                            },
                            descriptor.bot_ramming_profile,
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} has no installed ramming profile",
                            bot.id
                        ))
                    })
            }))
            .collect()
    }

    fn hull_materials(
        &self,
    ) -> Result<BTreeMap<VehicleKey, Vec<crate::combat_rules::MaterialInfo>>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                self.descriptors
                    .get(&participant.vehicle)
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Player,
                                id: u64::from(participant.player_id),
                            },
                            descriptor.hull_materials.clone(),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has no installed hull materials",
                            participant.player_id
                        ))
                    })
            })
            .chain(self.bots.iter().map(|bot| {
                bot.descriptor
                    .as_ref()
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Bot,
                                id: bot.id,
                            },
                            descriptor.hull_materials.clone(),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} has no installed hull materials",
                            bot.id
                        ))
                    })
            }))
            .collect()
    }

    fn critical_profiles(&self) -> Result<BTreeMap<VehicleKey, CriticalProfile>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                self.descriptors
                    .get(&participant.vehicle)
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Player,
                                id: u64::from(participant.player_id),
                            },
                            descriptor.critical_profile.clone(),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has no installed critical profile",
                            participant.player_id
                        ))
                    })
            })
            .chain(self.bots.iter().map(|bot| {
                bot.descriptor
                    .as_ref()
                    .map(|descriptor| {
                        (
                            VehicleKey {
                                kind: VehicleKind::Bot,
                                id: bot.id,
                            },
                            descriptor.critical_profile.clone(),
                        )
                    })
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} has no installed critical profile",
                            bot.id
                        ))
                    })
            }))
            .collect()
    }

    fn planner_manifest(&self) -> Result<Value, ServerError> {
        serde_json::to_value(
            self.bots
                .iter()
                .map(PreparedBot::client_manifest)
                .collect::<Vec<_>>(),
        )
        .map_err(|error| ServerError::AuthoritySetup(error.to_string()))
    }

    fn player_ammo_ledgers(&self) -> Result<BTreeMap<u64, PlayerAmmoLedger>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                let parsed = self.descriptors.get(&participant.vehicle).ok_or_else(|| {
                    ServerError::AuthoritySetup(format!(
                        "player {} has no installed ammunition descriptor",
                        participant.player_id
                    ))
                })?;
                let remaining = configuration_ammo_remaining(&participant.vehicle_configuration)
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} did not donate an exact garage ammunition layout",
                            participant.player_id
                        ))
                    })?;
                let loaded =
                    configuration_ammo_loaded_shell(&participant.vehicle_configuration, &remaining)
                        .ok_or_else(|| {
                            ServerError::AuthoritySetup(format!(
                                "player {} did not donate an exact loaded shell",
                                participant.player_id
                            ))
                        })?;
                let intuition_chances =
                    configuration_intuition_chances(&participant.vehicle_configuration)
                        .ok_or_else(|| {
                            ServerError::AuthoritySetup(format!(
                                "player {} did not donate canonical Loader Intuition inputs",
                                participant.player_id
                            ))
                        })?;
                let ledger = PlayerAmmoLedger::new_exact_loaded_with_intuition(
                    u64::from(participant.player_id),
                    0,
                    &parsed.descriptor,
                    &parsed.profile,
                    remaining,
                    loaded,
                    intuition_chances,
                )
                .map_err(|error| {
                    ServerError::AuthoritySetup(format!(
                        "player {} ammunition initialization failed: {error}",
                        participant.player_id
                    ))
                })?;
                Ok((u64::from(participant.player_id), ledger))
            })
            .collect()
    }

    fn player_burst_descriptors(
        &self,
    ) -> Result<BTreeMap<u64, PhysicalBurstDescriptor>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                self.descriptors
                    .get(&participant.vehicle)
                    .map(|descriptor| (u64::from(participant.player_id), descriptor.physical_burst))
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has no installed physical-burst descriptor",
                            participant.player_id
                        ))
                    })
            })
            .collect()
    }

    fn player_fire_inputs(&self) -> Result<BTreeMap<u64, PlayerFireAuthorityInput>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                let parsed = self.descriptors.get(&participant.vehicle).ok_or_else(|| {
                    ServerError::AuthoritySetup(format!(
                        "player {} has no installed gun-dispersion descriptor",
                        participant.player_id
                    ))
                })?;
                let effective_params = participant
                    .vehicle_configuration
                    .as_object()
                    .and_then(|configuration| configuration.get("effective_params"))
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} did not donate effective gun parameters",
                            participant.player_id
                        ))
                    })?;
                let static_factors =
                    effective_player_fire_factors(effective_params).ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has invalid effective gun parameters",
                            participant.player_id
                        ))
                    })?;
                let crew_factor = effective_params
                    .as_object()
                    .and_then(|fields| fields.get("loadout"))
                    .and_then(Value::as_object)
                    .and_then(|loadout| loadout.get("crew_factor"))
                    .and_then(Value::as_f64)
                    .filter(|value| value.is_finite() && (0.01..=16.0).contains(value))
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has no canonical turret crew factor",
                            participant.player_id
                        ))
                    })?;
                Ok((
                    u64::from(participant.player_id),
                    PlayerFireAuthorityInput {
                        law: parsed.player_fire_law,
                        static_factors,
                        turret_rotation_speed_rad_s: parsed.descriptor.gun.turret_rotation_speed,
                        crew_factor,
                        yaw_limits: parsed.descriptor.gun.yaw_limits,
                    },
                ))
            })
            .collect()
    }

    fn player_equipment_ledgers(
        &self,
    ) -> Result<BTreeMap<u64, PlayerEquipmentLedger>, ServerError> {
        self.participants
            .iter()
            .map(|participant| {
                let effective_params = participant
                    .vehicle_configuration
                    .as_object()
                    .and_then(|configuration| configuration.get("effective_params"))
                    .ok_or_else(|| {
                        ServerError::AuthoritySetup(format!(
                            "player {} did not donate effective equipment parameters",
                            participant.player_id
                        ))
                    })?;
                let ledger = PlayerEquipmentLedger::from_effective_params(
                    u64::from(participant.player_id),
                    0.0,
                    effective_params,
                )
                .map_err(|error| {
                    ServerError::AuthoritySetup(format!(
                        "player {} equipment initialization failed: {error}",
                        participant.player_id
                    ))
                })?;
                Ok((u64::from(participant.player_id), ledger))
            })
            .collect()
    }

    fn spotting_inputs(
        &self,
    ) -> Result<BTreeMap<VehicleKey, crate::descriptor::AuthoritySpottingInput>, ServerError> {
        let mut inputs = BTreeMap::new();
        for participant in &self.participants {
            let loadout =
                configuration_player_authority_loadout(&participant.vehicle_configuration)
                    .map_err(|error| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has an invalid authority loadout: {error}",
                            participant.player_id,
                        ))
                    })?;
            if let Some(input) = loadout.spotting.input() {
                inputs.insert(
                    VehicleKey {
                        kind: VehicleKind::Player,
                        id: u64::from(participant.player_id),
                    },
                    input,
                );
            }
        }
        for bot in &self.bots {
            let parsed = bot.descriptor.as_ref().ok_or_else(|| {
                ServerError::AuthoritySetup(format!(
                    "bot {} has no installed spotting settings",
                    bot.id
                ))
            })?;
            if let Some(input) = parsed.spotting_settings.bot_default.input() {
                inputs.insert(
                    VehicleKey {
                        kind: VehicleKind::Bot,
                        id: bot.id,
                    },
                    input,
                );
            }
        }
        Ok(inputs)
    }

    fn build_engine(&self) -> Result<BattleEngine, ServerError> {
        let mut vehicles = Vec::with_capacity(self.participants.len() + self.bots.len());
        for participant in &self.participants {
            let pose = self.spawn_pose(participant.team, participant.slot)?;
            vehicles.push(BattleVehicleInit {
                key: VehicleKey {
                    kind: VehicleKind::Player,
                    id: u64::from(participant.player_id),
                },
                team: participant.team,
                vehicle: participant.vehicle.clone(),
                health: participant.max_health,
                pose,
                world_pose: false,
            });
        }
        vehicles.extend(self.bots.iter().map(|bot| BattleVehicleInit {
            key: VehicleKey {
                kind: VehicleKind::Bot,
                id: bot.id,
            },
            team: bot.team,
            vehicle: bot.vehicle.clone(),
            health: bot.max_health,
            pose: bot.pose,
            world_pose: false,
        }));
        let [team_one, team_two] = self
            .capture_bases
            .clone()
            .ok_or(ServerError::MissingCaptureBases)?;
        let mut engine = BattleEngine::new(self.scope, vehicles, team_one, team_two)
            .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
        engine
            .install_critical_profiles(self.critical_profiles()?)
            .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
        for participant in &self.participants {
            let loadout =
                configuration_player_authority_loadout(&participant.vehicle_configuration)
                    .map_err(|error| {
                        ServerError::AuthoritySetup(format!(
                            "player {} has an invalid authority loadout: {error}",
                            participant.player_id,
                        ))
                    })?;
            if let Some(input) = loadout.repair.input() {
                engine
                    .install_repair_input(
                        VehicleKey {
                            kind: VehicleKind::Player,
                            id: u64::from(participant.player_id),
                        },
                        input,
                    )
                    .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
            }
        }
        for bot in &self.bots {
            let descriptor = bot.descriptor.as_ref().ok_or_else(|| {
                ServerError::AuthoritySetup(format!(
                    "bot {} has no installed repair settings",
                    bot.id
                ))
            })?;
            if let Some(input) = descriptor.repair_settings.bot_default.input() {
                engine
                    .install_repair_input(
                        VehicleKey {
                            kind: VehicleKind::Bot,
                            id: bot.id,
                        },
                        input,
                    )
                    .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
            }
        }
        Ok(engine)
    }
}

/// The protocol-v5 application and its single mutable [`RoomState`].
pub struct ServerApp {
    config: ServerConfig,
    map: String,
    room: RoomState,
    receipt_store: ReceiptStore,
    descriptor_exchange: DescriptorExchange,
    native_prerequisite_deadline: Option<Instant>,
    pending_start_requested_by: Option<u32>,
    prepared_round: Option<PreparedRound>,
    battle: Option<BattleLoop>,
    result_reset_deadline: Option<Instant>,
    event_roster: Option<EventRoster>,
    client_manifest_lineage: HashMap<ConnectionId, FullManifestLineage>,
    native_oracle: Option<NativeOracleConnection>,
    native_world_ready: Option<NativeWorldReady>,
    vehicle_overlay: VehicleOverlayStore,
    deferred_player_ready: BTreeMap<ConnectionId, WireObject>,
    deferred_oracle_ready: Option<(ConnectionId, WireObject)>,
    transport: TransportServer,
    connections: HashMap<ConnectionId, ActiveConnection>,
    probe_connections: HashMap<ConnectionId, SendHandle>,
    pending_closes: HashMap<ConnectionId, PendingClose>,
    last_recv_seq: u64,
    token_nonce: u64,
    map_selection_seed: u64,
    started_at: Instant,
}

impl ServerApp {
    /// Bind the configured address and initialize an empty room.
    pub fn bind(config: ServerConfig) -> Result<Self, ServerError> {
        validate_catalog().map_err(|error| ServerError::AuthoritySetup(error.to_string()))?;
        let receipt_namespace = process_namespace();
        let room_config = RoomConfig::new(
            config.max_players,
            config.team_sizes[0],
            config.team_sizes[1],
            DEFAULT_VEHICLE_0922,
            receipt_namespace,
        )
        .map_err(ServerError::RoomConfig)?;
        let receipt_path = config
            .receipt_state_path
            .clone()
            .unwrap_or_else(|| default_receipt_state_path(config.port));
        let receipt_store = ReceiptStore::open(receipt_path).map_err(ServerError::ReceiptStore)?;
        let vehicle_overlay = VehicleOverlayStore::load(config.vehicle_overlay_root.as_deref())
            .map_err(ServerError::VehicleOverlay)?;
        // Launcher manifest replies retain the ordinary 256 KiB protocol
        // bound. Only verified member data uses the dedicated large frame.
        let _ = wire(vehicle_overlay.manifest_payload())?;
        let room = RoomState::with_receipts(room_config, receipt_store.room_receipts().cloned())
            .map_err(ServerError::RoomState)?;
        let transport = TransportServer::bind((config.host.as_str(), config.port))
            .map_err(ServerError::Bind)?;
        Ok(Self {
            map: config.map.clone(),
            config,
            room,
            receipt_store,
            descriptor_exchange: DescriptorExchange::new(),
            native_prerequisite_deadline: None,
            pending_start_requested_by: None,
            prepared_round: None,
            battle: None,
            result_reset_deadline: None,
            event_roster: None,
            client_manifest_lineage: HashMap::new(),
            native_oracle: None,
            native_world_ready: None,
            vehicle_overlay,
            deferred_player_ready: BTreeMap::new(),
            deferred_oracle_ready: None,
            transport,
            connections: HashMap::new(),
            probe_connections: HashMap::new(),
            pending_closes: HashMap::new(),
            last_recv_seq: 0,
            token_nonce: 0,
            map_selection_seed: process_random_seed(),
            started_at: Instant::now(),
        })
    }

    pub fn local_addr(&self) -> SocketAddr {
        self.transport.local_addr()
    }

    pub fn config(&self) -> &ServerConfig {
        &self.config
    }

    pub fn room(&self) -> &RoomState {
        &self.room
    }

    pub fn current_map(&self) -> &str {
        &self.map
    }

    /// Process at most one transport event.
    ///
    /// This method is useful to embed the server in a launcher-owned loop and
    /// keeps tests deterministic without introducing another state thread.
    pub fn poll(&mut self, timeout: Duration) -> Result<PollOutcome, ServerError> {
        self.close_due_rejections();
        self.fail_native_prerequisite_if_due()?;
        self.reset_finished_round_if_due()?;
        self.drive_battle()?;
        let timeout = self.timeout_until_round_reset(self.timeout_until_native_prerequisite(
            self.timeout_until_next_battle_tick(self.timeout_until_next_close(timeout)),
        ));
        let outcome = match self.transport.recv_timeout(timeout) {
            Ok(event) => {
                self.handle_event(event)?;
                PollOutcome::Event
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => PollOutcome::Timeout,
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                return Err(ServerError::EventStreamClosed);
            }
        };
        self.drive_battle()?;
        self.fail_native_prerequisite_if_due()?;
        self.reset_finished_round_if_due()?;
        self.close_due_rejections();
        Ok(outcome)
    }

    /// Run until `stop` becomes true.
    pub fn run_until(&mut self, stop: &AtomicBool) -> Result<(), ServerError> {
        while !stop.load(Ordering::Acquire) {
            self.poll(EVENT_POLL_INTERVAL)?;
        }
        Ok(())
    }

    /// Run the listener indefinitely.
    pub fn run(&mut self) -> Result<(), ServerError> {
        loop {
            self.poll(EVENT_POLL_INTERVAL)?;
        }
    }

    fn handle_event(&mut self, event: TransportEvent) -> Result<(), ServerError> {
        match event {
            TransportEvent::Connected {
                connection_id,
                hello,
                sender,
                ..
            } => self.handle_connected(connection_id, hello, sender),
            TransportEvent::Message { envelope } => {
                let expected = self.last_recv_seq.checked_add(1).unwrap_or(u64::MAX);
                if envelope.recv_seq != expected {
                    return Err(ServerError::ReceiveOrder {
                        expected,
                        received: envelope.recv_seq,
                    });
                }
                self.last_recv_seq = envelope.recv_seq;
                self.handle_message(envelope.recv_seq, envelope.connection_id, envelope.message)
            }
            TransportEvent::Disconnected { connection_id, .. } => {
                self.pending_closes.remove(&connection_id);
                self.probe_connections.remove(&connection_id);
                if self.remove_active_connection(connection_id, false)? {
                    self.broadcast_roster()?;
                }
                Ok(())
            }
        }
    }

    fn handle_connected(
        &mut self,
        connection_id: ConnectionId,
        hello: Hello,
        sender: SendHandle,
    ) -> Result<(), ServerError> {
        if self.connections.contains_key(&connection_id)
            || self.probe_connections.contains_key(&connection_id)
            || self.pending_closes.contains_key(&connection_id)
        {
            return self.reject_new(
                connection_id,
                sender,
                "duplicate_connection",
                "connection identity is already active",
            );
        }

        if hello.object().get("role").and_then(Value::as_str) == Some("probe") {
            return self.admit_probe(connection_id, hello, sender);
        }

        match hello.role() {
            ConnectionRole::Player => self.admit_player(connection_id, hello, sender),
            ConnectionRole::SimulationWorker => {
                self.admit_native_oracle(connection_id, hello, sender)
            }
        }
    }

    fn admit_probe(
        &mut self,
        connection_id: ConnectionId,
        hello: Hello,
        sender: SendHandle,
    ) -> Result<(), ServerError> {
        let object = hello.object();
        let valid_build = object
            .get("client_build")
            .and_then(Value::as_str)
            .is_some_and(|value| !value.is_empty() && value.len() <= 128);
        let capabilities: Option<Vec<String>> = object
            .get("capabilities")
            .and_then(Value::as_array)
            .filter(|values| values.len() <= 32)
            .and_then(|values| {
                let parsed: Option<Vec<String>> = values
                    .iter()
                    .map(|value| {
                        value
                            .as_str()
                            .filter(|value| !value.is_empty() && value.len() <= 64)
                            .map(ToOwned::to_owned)
                    })
                    .collect();
                parsed.filter(|parsed| parsed.iter().collect::<BTreeSet<_>>().len() == values.len())
            });
        if !capabilities
            .as_ref()
            .is_some_and(|capabilities| compatible_hello_protocol(&hello, capabilities))
        {
            return self.reject_new(
                connection_id,
                sender,
                "protocol",
                "launcher probe protocol is incompatible",
            );
        }
        if !valid_build
            || !capabilities.as_ref().is_some_and(|capabilities| {
                has_required_capabilities(capabilities, MODERN_CLIENT_REQUIRED_CAPABILITIES)
            })
        {
            return self.reject_new(
                connection_id,
                sender,
                "unsupported_capabilities",
                "launcher probe is incompatible",
            );
        }
        let welcome = wire(json!({
            "type": "welcome",
            "protocol": LAN_PROTOCOL_VERSION,
            "client_build": CLIENT_BUILD_0922,
            "capabilities": capabilities.unwrap_or_default(),
            "server_capabilities": SERVER_CAPABILITIES,
        }))?;
        self.probe_connections.insert(connection_id, sender.clone());
        if sender.offer_reliable(welcome).is_err() {
            self.probe_connections.remove(&connection_id);
            sender.close();
        }
        Ok(())
    }

    fn admit_player(
        &mut self,
        connection_id: ConnectionId,
        hello: Hello,
        sender: SendHandle,
    ) -> Result<(), ServerError> {
        let parsed = match parse_player_hello(&hello) {
            Ok(parsed) => parsed,
            Err(error) => {
                return self.reject_new(connection_id, sender, error.code(), &error.to_string());
            }
        };
        let endpoint =
            EndpointIdentity::from_transport(connection_id, self.next_session_token(connection_id));
        let joined = match self.room.join_player(endpoint, parsed.join) {
            Ok(joined) => joined,
            Err(error) => {
                return self.reject_new(connection_id, sender, error.code(), &error.to_string());
            }
        };
        let welcome = welcome_message(
            &self.room,
            &joined.player,
            &parsed.client_build,
            &parsed.capabilities,
            &self.map,
        )?;
        let session = joined.session.clone();
        self.connections.insert(
            connection_id,
            ActiveConnection {
                sender,
                session: EndpointSession::Player(joined.session),
            },
        );
        if !self.offer_active(connection_id, welcome) {
            self.remove_active_connection(connection_id, false)?;
        }
        self.broadcast_roster()?;
        self.offer_next_receipt(connection_id, &session)
    }

    fn admit_native_oracle(
        &mut self,
        connection_id: ConnectionId,
        hello: Hello,
        sender: SendHandle,
    ) -> Result<(), ServerError> {
        let native = match validate_native_oracle_hello(&hello) {
            Ok(native) => native,
            Err((code, message)) => {
                return self.reject_new(connection_id, sender, code, message);
            }
        };
        let endpoint =
            EndpointIdentity::from_transport(connection_id, self.next_session_token(connection_id));
        let session = match self.room.attach_oracle(endpoint) {
            Ok(session) => session,
            Err(error) => {
                return self.reject_new(connection_id, sender, error.code(), &error.to_string());
            }
        };
        let welcome = self.oracle_welcome(&native.capabilities, native.generation)?;
        self.native_oracle = Some(NativeOracleConnection {
            connection_id,
            generation: native.generation,
        });
        self.native_world_ready = None;
        self.deferred_oracle_ready = None;
        self.connections.insert(
            connection_id,
            ActiveConnection {
                sender,
                session: EndpointSession::Oracle(session),
            },
        );
        if !self.offer_active(connection_id, welcome) {
            self.remove_active_connection(connection_id, false)?;
        }
        self.broadcast_roster()
    }

    fn handle_message(
        &mut self,
        recv_seq: u64,
        connection_id: ConnectionId,
        message: WireObject,
    ) -> Result<(), ServerError> {
        if self.pending_closes.contains_key(&connection_id) {
            return Ok(());
        }
        if self.probe_connections.contains_key(&connection_id) {
            return self.handle_probe_message(connection_id, message);
        }
        let session = match self.connections.get(&connection_id) {
            Some(connection) => connection.session.clone(),
            None => return Ok(()),
        };
        match session {
            EndpointSession::Player(session) => {
                self.handle_player_message(recv_seq, connection_id, &session, message)
            }
            EndpointSession::Oracle(session) => {
                self.handle_oracle_message(connection_id, &session, message)
            }
        }
    }

    fn handle_probe_message(
        &mut self,
        connection_id: ConnectionId,
        message: WireObject,
    ) -> Result<(), ServerError> {
        match message.kind() {
            "vehicle_overlay_query" => {
                let payload = wire(self.vehicle_overlay.manifest_payload())?;
                self.offer_probe(connection_id, payload, false)
            }
            "vehicle_overlay_member" => {
                let Some(member) = message.get("sourceMember").and_then(Value::as_str) else {
                    return self.send_probe_error(
                        connection_id,
                        "unknown_member",
                        "unknown vehicle overlay member",
                    );
                };
                let Some(payload) = self.vehicle_overlay.member_payload(member) else {
                    return self.send_probe_error(
                        connection_id,
                        "unknown_member",
                        "unknown vehicle overlay member",
                    );
                };
                self.offer_probe(connection_id, wire(payload)?, true)
            }
            "leave" => {
                if let Some(sender) = self.probe_connections.remove(&connection_id) {
                    sender.close();
                }
                Ok(())
            }
            other => self.send_probe_error(
                connection_id,
                "unsupported_command",
                &format!("launcher probe command {other:?} is not supported"),
            ),
        }
    }

    fn offer_probe(
        &mut self,
        connection_id: ConnectionId,
        message: WireObject,
        large: bool,
    ) -> Result<(), ServerError> {
        let Some(sender) = self.probe_connections.get(&connection_id).cloned() else {
            return Ok(());
        };
        let offered = if large {
            sender.offer_large_reliable(
                message,
                MAX_OVERLAY_LINE_BYTES,
                MAX_OVERLAY_LINE_BYTES.saturating_add(crate::wire::MAX_LINE_BYTES),
            )
        } else {
            sender.offer_reliable(message)
        };
        if offered.is_err() {
            self.probe_connections.remove(&connection_id);
            sender.close();
        }
        Ok(())
    }

    fn send_probe_error(
        &mut self,
        connection_id: ConnectionId,
        code: &str,
        message: &str,
    ) -> Result<(), ServerError> {
        let error = error_message(code, message)?;
        self.offer_probe(connection_id, error, false)
    }

    fn handle_player_message(
        &mut self,
        recv_seq: u64,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: WireObject,
    ) -> Result<(), ServerError> {
        match message.kind() {
            "ping" => self.reply_pong(connection_id, &message),
            "leave" => {
                if self.remove_active_connection(connection_id, true)? {
                    self.broadcast_roster()?;
                }
                Ok(())
            }
            "select_team" => self.handle_select_team(connection_id, session, &message),
            "set_team_size" => self.handle_set_team_size(connection_id, session, &message),
            "set_bot_tier_mode" => self.handle_set_bot_tier_mode(connection_id, session, &message),
            "select_vehicle" => self.handle_select_vehicle(connection_id, session, &message),
            "descriptor_catalog" => {
                self.handle_descriptor_catalog(connection_id, session, &message)
            }
            "descriptor_bundle" => self.handle_descriptor_bundle(connection_id, session, &message),
            "destructible_map" => self.handle_destructible_map(connection_id, session, &message),
            "start_battle" => self.handle_start(connection_id, session, &message),
            "battle_ready" | "ready" => self.handle_player_ready(connection_id, session, &message),
            "battle_receipt_ack" | "receipt_ack" => {
                self.handle_receipt_ack(connection_id, session, &message)
            }
            "input" | "fire_intent" | "ammo_intent" | "equipment_intent" => {
                self.enqueue_battle_message(recv_seq, connection_id, session, message)
            }
            "landing_observation" => {
                self.handle_landing_observation(recv_seq, connection_id, session, message)
            }
            "leave_battle" => self.handle_leave_battle(connection_id, session, &message),
            other => self.reject_active(
                connection_id,
                "unsupported_command",
                &format!("player command {other:?} is not available in the waiting-room server"),
            ),
        }
    }

    fn handle_oracle_message(
        &mut self,
        connection_id: ConnectionId,
        session: &OracleSession,
        message: WireObject,
    ) -> Result<(), ServerError> {
        match message.kind() {
            "ping" => self.reply_pong(connection_id, &message),
            "leave" => {
                if self.remove_active_connection(connection_id, true)? {
                    self.broadcast_roster()?;
                }
                Ok(())
            }
            "battle_ready" | "ready" => self.handle_oracle_ready(connection_id, session, &message),
            "oracle_world_ready" => {
                self.handle_native_world_ready(connection_id, session, &message)
            }
            "query_reply" => self.handle_oracle_reply(connection_id, &message),
            other => self.reject_active(
                connection_id,
                "unsupported_oracle_command",
                &format!(
                    "native-oracle command {other:?} is not wired into the waiting-room server"
                ),
            ),
        }
    }

    fn handle_native_world_ready(
        &mut self,
        connection_id: ConnectionId,
        _session: &OracleSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        if self.room.phase() != RoomPhase::Loading
            || self.pending_start_requested_by.is_some()
            || !self.descriptor_exchange.snapshot().is_some_and(|snapshot| {
                snapshot.round_id == self.room.round_id()
                    && snapshot.descriptor_gate == DescriptorGate::Complete
                    && matches!(
                        snapshot.destructible_gate,
                        DestructibleGate::Collecting
                            | DestructibleGate::AwaitingInstall
                            | DestructibleGate::Complete
                    )
            })
        {
            return self.send_error(
                connection_id,
                "native_world_not_expected",
                "a native world donation is only accepted at the loading barrier",
            );
        }
        let Some(oracle) = self
            .native_oracle
            .filter(|oracle| oracle.connection_id == connection_id)
        else {
            return self.reject_active(
                connection_id,
                "stale_native_oracle",
                "the native world donation came from a stale oracle connection",
            );
        };
        let Some(prepared) = self.prepared_round.as_ref() else {
            return self.send_error(
                connection_id,
                "native_world_not_expected",
                "no prepared round is waiting for a native world donation",
            );
        };
        let wire: NativeWorldReadyWire = match serde_json::from_value(message.clone().into_value())
        {
            Ok(wire) => wire,
            Err(error) => {
                return self.reject_active(
                    connection_id,
                    "invalid_native_world",
                    &format!("native world donation shape is invalid: {error}"),
                );
            }
        };
        let ready =
            match validate_native_world_ready(wire, prepared, self.room.scope(), oracle.generation)
            {
                Ok(ready) => ready,
                Err(message) => {
                    return self.reject_active(connection_id, "invalid_native_world", &message);
                }
            };
        if let Some(active) = &self.native_world_ready {
            if active == &ready {
                return Ok(());
            }
            return self.reject_active(
                connection_id,
                "conflicting_native_world",
                "the immutable native entity donation changed within one oracle generation",
            );
        }
        self.native_world_ready = Some(ready);
        // The descriptor donor has finished; give the native entity barrier
        // its own full bounded window instead of inheriting time already spent
        // projecting descriptors. Destructible installation may flush every
        // deferred ready and activate the battle, which clears this deadline.
        self.native_prerequisite_deadline = Some(Instant::now() + NATIVE_PREREQUISITE_TIMEOUT);
        self.try_confirm_destructible_install()?;
        Ok(())
    }

    fn handle_oracle_ready(
        &mut self,
        connection_id: ConnectionId,
        session: &OracleSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let scope = match self.checked_scope(message) {
            Ok(scope) => scope,
            Err(error) => return self.send_room_error(connection_id, error),
        };
        if let Err(message_text) = self.accept_capture_bases(message) {
            return self.send_error(connection_id, "invalid_capture_bases", message_text);
        }
        if self.pending_start_requested_by.is_some() {
            return self.send_error(
                connection_id,
                "native_prerequisites_pending",
                "battle_start has not been published for this round",
            );
        }
        if !self.native_world_ready.as_ref().is_some_and(|ready| {
            ready.donation.lineage.round_id == self.room.round_id()
                && ready.donation.lineage.authority_epoch == self.room.authority_epoch()
                && self.native_oracle.is_some_and(|oracle| {
                    oracle.connection_id == connection_id
                        && ready.donation.lineage.oracle_generation == oracle.generation
                })
        }) {
            return self.send_error(
                connection_id,
                "native_world_pending",
                "the hidden native world has not donated a complete fenced entity map",
            );
        }
        if !self.native_prerequisites_ready() {
            return self.defer_oracle_ready(connection_id, message);
        }
        match self.room.mark_oracle_ready(session, scope) {
            Ok(outcome) => self.publish_ready_outcome(outcome),
            Err(error) => self.send_room_error(connection_id, error),
        }
    }

    fn handle_oracle_reply(
        &mut self,
        connection_id: ConnectionId,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        if message.fields().len() != 2 {
            return self.send_error(
                connection_id,
                "invalid_oracle_reply",
                "query_reply contains unexpected envelope fields",
            );
        }
        let Some(payload) = message.get("payload") else {
            return self.send_error(
                connection_id,
                "invalid_oracle_reply",
                "query_reply payload is missing",
            );
        };
        let reply: OracleV1BatchReply = match serde_json::from_value(payload.clone()) {
            Ok(reply) => reply,
            Err(error) => {
                return self.send_error(
                    connection_id,
                    "invalid_oracle_reply",
                    &format!("query_reply payload is invalid: {error}"),
                );
            }
        };
        let Some(battle) = self.battle.as_mut() else {
            return self.send_error(
                connection_id,
                "unexpected_oracle_reply",
                "query_reply arrived without an active battle",
            );
        };
        if let Err(error) = battle.accept_oracle_reply(reply) {
            return self.send_error(connection_id, "invalid_oracle_reply", &error.to_string());
        }
        Ok(())
    }

    fn handle_select_team(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let team = match message.get("team").and_then(exact_u64) {
            Some(1) => Team::One,
            Some(2) => Team::Two,
            _ => {
                return self.send_team_denied(connection_id, "invalid_team", message.get("team"));
            }
        };
        match self.room.select_team(session, team) {
            Ok(_) => self.broadcast_roster(),
            Err(error) => self.send_team_denied(connection_id, error.code(), message.get("team")),
        }
    }

    fn handle_set_team_size(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let team = match message.get("team").and_then(exact_u64) {
            Some(1) => Team::One,
            Some(2) => Team::Two,
            _ => {
                return self.send_team_size_denied(
                    connection_id,
                    "invalid_team",
                    message.get("team"),
                    message.get("size"),
                );
            }
        };
        let size = match message.get("size").and_then(exact_u64) {
            Some(size @ 1..=15) => size as usize,
            _ => {
                return self.send_team_size_denied(
                    connection_id,
                    "invalid_size",
                    message.get("team"),
                    message.get("size"),
                );
            }
        };
        match self.room.set_team_capacity(session, team, size) {
            Ok(_) => self.broadcast_roster(),
            Err(error) => self.send_team_size_denied(
                connection_id,
                error.code(),
                message.get("team"),
                message.get("size"),
            ),
        }
    }

    fn handle_set_bot_tier_mode(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let requested = message.get("mode");
        let Some(mode) = requested
            .and_then(Value::as_str)
            .and_then(BotTierMode::parse)
        else {
            return self.send_bot_tier_mode_denied(connection_id, "invalid_mode", requested);
        };
        match self.room.set_bot_tier_mode(session, mode) {
            Ok(_) => self.broadcast_roster(),
            Err(error) => self.send_bot_tier_mode_denied(connection_id, error.code(), requested),
        }
    }

    fn handle_select_vehicle(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let Some(current) = self.room.player(session.player_id()) else {
            return self.send_room_error(connection_id, RoomError::InvalidPlayerSession);
        };
        let (vehicle, max_health, configuration) = match parse_vehicle_selection(message, &current)
        {
            Ok(selection) => selection,
            Err(code) => return self.send_error(connection_id, code, "invalid vehicle selection"),
        };
        match self
            .room
            .select_vehicle(session, &vehicle, max_health, configuration)
        {
            Ok(true) => self.broadcast_roster(),
            Ok(false) => Ok(()),
            Err(error) => self.send_room_error(connection_id, error),
        }
    }

    fn handle_start(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        if let Err(error) = self.checked_scope(message) {
            return self.send_start_denied(connection_id, error.code());
        }
        let selected_map = match selected_map(
            message.get("map"),
            &self.map,
            self.map_selection_seed,
            self.room.round_id().saturating_add(1),
        ) {
            Some(map) => map,
            None => return self.send_start_denied(connection_id, "invalid_map"),
        };
        let donor_id = session.player_id();
        let Some(catalog) = self.descriptor_exchange.catalog(donor_id) else {
            return self.send_start_denied(connection_id, "lineup_unavailable");
        };
        if validate_exact_lineup(catalog, &self.config.bot_lineup).is_err() {
            return self.send_start_denied(connection_id, "lineup_unavailable");
        }
        if self
            .room
            .players()
            .any(|player| catalog.get(&player.vehicle).is_none())
        {
            return self.send_start_denied(connection_id, "lineup_unavailable");
        }
        let navigation_graph = match self
            .config
            .navigation_graph_directory
            .as_deref()
            .map(|directory| NavGraph::load_from_directory(directory, &selected_map))
        {
            Some(Ok(graph)) => graph,
            Some(Err(_)) | None => {
                return self.send_start_denied(connection_id, "navigation_unavailable");
            }
        };
        let mut staged_room = self.room.clone();
        match staged_room.request_start(session) {
            Ok(outcome) => {
                let assignments = match plan_bot_lineup(
                    catalog,
                    &outcome.participants,
                    staged_room.config(),
                    staged_room.bot_tier_mode(),
                    &self.config.bot_lineup,
                    outcome.scope.round_id,
                    &selected_map,
                ) {
                    Ok(assignments) => assignments,
                    Err(_) => {
                        return self.send_start_denied(connection_id, "lineup_unavailable");
                    }
                };
                let prepared = match PreparedRound::new(
                    outcome.clone(),
                    staged_room.config(),
                    &selected_map,
                    &assignments,
                    navigation_graph,
                ) {
                    Ok(prepared) => prepared,
                    Err(ServerError::AuthoritySetup(_)) => {
                        return self.send_start_denied(connection_id, "navigation_unavailable");
                    }
                    Err(error) => return Err(error),
                };
                let required_names = prepared.required_vehicle_names();
                let request = self.descriptor_exchange.begin_round(
                    outcome.scope.round_id,
                    donor_id,
                    selected_map.clone(),
                    required_names,
                    true,
                )?;
                self.room = staged_room;
                self.map = selected_map;
                self.client_manifest_lineage.clear();
                self.native_world_ready = None;
                self.deferred_player_ready.clear();
                self.deferred_oracle_ready = None;
                self.prepared_round = Some(prepared);
                self.pending_start_requested_by = Some(session.player_id());
                self.native_prerequisite_deadline =
                    Some(Instant::now() + NATIVE_PREREQUISITE_TIMEOUT);
                match request {
                    Some(request) => self.send_nonfatal(connection_id, request),
                    None => self.complete_descriptor_exchange(),
                }
            }
            Err(error) => self.send_start_denied(connection_id, error.code()),
        }
    }

    fn handle_descriptor_catalog(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        match self
            .descriptor_exchange
            .admit_catalog(session.player_id(), message)
        {
            Ok(_) => Ok(()),
            Err(error) => self.send_error(
                connection_id,
                "invalid_descriptor_catalog",
                &error.to_string(),
            ),
        }
    }

    fn handle_descriptor_bundle(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let event = match self
            .descriptor_exchange
            .admit_descriptor_bundle(session.player_id(), message)
        {
            Ok(event) => event,
            Err(error) => {
                self.fail_native_prerequisite(ExchangeFailure::DescriptorProjectionFailed)?;
                return self.send_error(
                    connection_id,
                    "descriptor_projection_failed",
                    &error.to_string(),
                );
            }
        };
        match event {
            ExchangeEvent::Ready | ExchangeEvent::DescriptorSetComplete => {
                self.complete_descriptor_exchange()
            }
            ExchangeEvent::Failed(failure) => self.fail_native_prerequisite(failure),
            _ => Ok(()),
        }
    }

    fn handle_destructible_map(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let event = match self
            .descriptor_exchange
            .admit_destructible_map(session.player_id(), message)
        {
            Ok(event) => event,
            Err(error) => {
                self.fail_native_prerequisite(ExchangeFailure::DestructibleMapIncomplete)?;
                return self.send_error(
                    connection_id,
                    "destructible_map_incomplete",
                    &error.to_string(),
                );
            }
        };
        match event {
            ExchangeEvent::DestructibleMapAssembled { .. } => {
                self.try_confirm_destructible_install()
            }
            ExchangeEvent::Failed(failure) => self.fail_native_prerequisite(failure),
            _ => Ok(()),
        }
    }

    fn try_confirm_destructible_install(&mut self) -> Result<(), ServerError> {
        let Some(snapshot) = self.descriptor_exchange.snapshot() else {
            return Ok(());
        };
        if snapshot.destructible_gate != DestructibleGate::AwaitingInstall {
            return Ok(());
        }
        let Some(native) = self.native_world_ready.as_ref() else {
            return Ok(());
        };
        let donation = self
            .descriptor_exchange
            .destructible_map()
            .ok_or(DescriptorExchangeError::DestructibleMapNotAssembled)?;
        let expected = usize::try_from(native.destructibles.expected_instances).map_err(|_| {
            ServerError::AuthoritySetup(
                "native destructible instance count exceeds this server".to_owned(),
            )
        })?;
        let installed =
            usize::try_from(native.destructibles.installed_instances).map_err(|_| {
                ServerError::AuthoritySetup(
                    "native destructible install count exceeds this server".to_owned(),
                )
            })?;
        if donation.instances.len() != expected {
            return self.fail_native_prerequisite(ExchangeFailure::DestructibleMapIncomplete);
        }
        match self.descriptor_exchange.confirm_destructible_install(
            snapshot.round_id,
            &snapshot.map_name,
            expected,
            installed,
        )? {
            ExchangeEvent::Ready | ExchangeEvent::DestructibleMapInstalled { .. } => {
                self.flush_deferred_ready()
            }
            ExchangeEvent::Failed(failure) => self.fail_native_prerequisite(failure),
            _ => Ok(()),
        }
    }

    fn complete_descriptor_exchange(&mut self) -> Result<(), ServerError> {
        let descriptors = self
            .descriptor_exchange
            .descriptors()
            .cloned()
            .unwrap_or_default();
        self.prepared_round
            .as_mut()
            .ok_or(ServerError::MissingCaptureBases)?
            .install_descriptors(&descriptors)
            .map_err(|message| {
                ServerError::DescriptorExchange(DescriptorExchangeError::InvalidField {
                    path: "$.projections".to_owned(),
                    message: message.to_owned(),
                })
            })?;
        // Validate every participant's connection-scoped equipment donation
        // before publishing battle_start. Activation rebuilds the same frozen
        // ledgers at clock zero and cannot substitute a descriptor donor's kit.
        self.prepared_round
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?
            .player_equipment_ledgers()?;
        // Descriptor projection is only the first native prerequisite. Keep a
        // separate full window for oracle_world_ready and the client-ready
        // barrier so a connected but wedged hidden client cannot leave the
        // room in Loading forever.
        self.native_prerequisite_deadline = Some(Instant::now() + NATIVE_PREREQUISITE_TIMEOUT);
        let Some(requested_by) = self.pending_start_requested_by.take() else {
            return Ok(());
        };
        let scope = self.room.scope();
        let transition = self.battle_start_message(requested_by, scope)?;
        self.broadcast_transition(transition)
    }

    fn fail_native_prerequisite(&mut self, failure: ExchangeFailure) -> Result<(), ServerError> {
        self.fail_loading_prerequisite(failure.wire_code())
    }

    fn fail_loading_prerequisite(&mut self, reason: &str) -> Result<(), ServerError> {
        self.native_prerequisite_deadline = None;
        self.pending_start_requested_by = None;
        self.deferred_player_ready.clear();
        self.deferred_oracle_ready = None;
        if !matches!(self.room.phase(), RoomPhase::Loading | RoomPhase::Battle) {
            return Ok(());
        }
        let result =
            BattleResult::new(BattleWinner::Draw, reason, None).map_err(ServerError::RoomState)?;
        self.finalize_round(result, OUT_OF_BAND_RESULT_ORDINAL)?;
        self.broadcast_roster()
    }

    fn handle_player_ready(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let scope = match self.checked_scope(message) {
            Ok(scope) => scope,
            Err(error) => return self.send_room_error(connection_id, error),
        };
        if let Err(message_text) = self.accept_capture_bases(message) {
            return self.send_error(connection_id, "invalid_capture_bases", message_text);
        }
        if self.pending_start_requested_by.is_some() {
            return self.send_error(
                connection_id,
                "native_prerequisites_pending",
                "battle_start has not been published for this round",
            );
        }
        if !self.native_prerequisites_ready() {
            return self.defer_player_ready(connection_id, message);
        }
        match self.room.mark_player_ready(session, scope) {
            Ok(outcome) => self.publish_ready_outcome(outcome),
            Err(error) => self.send_room_error(connection_id, error),
        }
    }

    fn native_prerequisites_ready(&self) -> bool {
        self.descriptor_exchange.snapshot().is_some_and(|snapshot| {
            snapshot.round_id == self.room.round_id() && snapshot.status == ExchangeStatus::Ready
        })
    }

    fn defer_player_ready(
        &mut self,
        connection_id: ConnectionId,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        match self.deferred_player_ready.get(&connection_id) {
            Some(known) if known == message => Ok(()),
            Some(_) => self.send_error(
                connection_id,
                "conflicting_battle_ready",
                "battle readiness changed before native prerequisites completed",
            ),
            None => {
                self.deferred_player_ready
                    .insert(connection_id, message.clone());
                Ok(())
            }
        }
    }

    fn defer_oracle_ready(
        &mut self,
        connection_id: ConnectionId,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        match self.deferred_oracle_ready.as_ref() {
            Some((known_connection, known))
                if *known_connection == connection_id && known == message =>
            {
                Ok(())
            }
            Some(_) => self.send_error(
                connection_id,
                "conflicting_battle_ready",
                "oracle battle readiness changed before native prerequisites completed",
            ),
            None => {
                self.deferred_oracle_ready = Some((connection_id, message.clone()));
                Ok(())
            }
        }
    }

    fn flush_deferred_ready(&mut self) -> Result<(), ServerError> {
        if !self.native_prerequisites_ready() {
            return Ok(());
        }
        if let Some((connection_id, message)) = self.deferred_oracle_ready.take() {
            let session = self.connections.get(&connection_id).and_then(|connection| {
                match &connection.session {
                    EndpointSession::Oracle(session) => Some(session.clone()),
                    EndpointSession::Player(_) => None,
                }
            });
            if let Some(session) = session {
                self.handle_oracle_ready(connection_id, &session, &message)?;
            }
        }
        let deferred_players = std::mem::take(&mut self.deferred_player_ready);
        for (connection_id, message) in deferred_players {
            let session = self.connections.get(&connection_id).and_then(|connection| {
                match &connection.session {
                    EndpointSession::Player(session) => Some(session.clone()),
                    EndpointSession::Oracle(_) => None,
                }
            });
            if let Some(session) = session {
                self.handle_player_ready(connection_id, &session, &message)?;
            }
        }
        Ok(())
    }

    fn handle_receipt_ack(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let Some(receipt_id) = message
            .get("receipt_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty() && value.len() <= 96)
        else {
            return self.send_error(
                connection_id,
                "invalid_receipt",
                "receipt_id must be a non-empty string of at most 96 bytes",
            );
        };
        let Some(player) = self.room.player(session.player_id()) else {
            return self.send_room_error(connection_id, RoomError::InvalidPlayerSession);
        };
        let Some(receipt) = self
            .room
            .receipt_ledger()
            .find(|receipt| receipt.receipt_id == receipt_id)
        else {
            return self.send_room_error(connection_id, RoomError::ReceiptNotFound);
        };
        if receipt.account_key != player.account_key {
            return self.send_room_error(connection_id, RoomError::ReceiptNotOwned);
        }

        // The ACK is installed durably before the in-memory room forgets it.
        // A persistence failure therefore terminates the server without ever
        // telling the client that an unsafe ACK succeeded.
        self.receipt_store
            .acknowledge(&player.account_key, receipt_id)
            .map_err(ServerError::ReceiptStore)?;
        self.room
            .acknowledge_receipt(session, receipt_id)
            .map_err(ServerError::RoomState)?;
        self.offer_next_receipt(connection_id, session)
    }

    fn publish_ready_outcome(&mut self, outcome: ReadyOutcome) -> Result<(), ServerError> {
        match outcome {
            ReadyOutcome::Waiting { .. } => Ok(()),
            ReadyOutcome::Activated { scope, .. } => {
                self.activate_battle(scope)?;
                self.native_prerequisite_deadline = None;
                let live = self.battle_live_message(scope)?;
                self.broadcast_transition(live)
            }
        }
    }

    fn accept_capture_bases(&mut self, message: &WireObject) -> Result<(), &'static str> {
        let Some(bases) = parse_capture_bases(message)? else {
            return Ok(());
        };
        let prepared = self
            .prepared_round
            .as_mut()
            .ok_or("no round is waiting at the load barrier")?;
        prepared.accept_capture_bases(bases)
    }

    fn activate_battle(&mut self, scope: SimulationScope) -> Result<(), ServerError> {
        if self.battle.is_some() {
            return Ok(());
        }
        let prepared = self
            .prepared_round
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?;
        if prepared.scope != scope {
            return Err(ServerError::RoomState(RoomError::StaleScope {
                expected: scope,
                received: prepared.scope,
            }));
        }
        let native_world = self
            .native_world_ready
            .as_ref()
            .filter(|ready| {
                ready.donation.lineage.round_id == scope.round_id
                    && ready.donation.lineage.authority_epoch == scope.epoch
            })
            .cloned()
            .ok_or_else(|| {
                ServerError::AuthoritySetup(
                    "native world donation is missing at battle activation".to_owned(),
                )
            })?;
        let destructible_map = self
            .descriptor_exchange
            .destructible_map()
            .filter(|donation| donation.round_id == scope.round_id && donation.map_name == self.map)
            .cloned()
            .ok_or_else(|| {
                ServerError::AuthoritySetup(
                    "frozen destructible donation is missing at battle activation".to_owned(),
                )
            })?;
        let mut authority = AuthorityRuntime::new(
            native_world.donation.lineage,
            0,
            native_world.entity_revision,
            prepared.build_bot_simulators()?,
        )
        .map_err(|error| ServerError::AuthoritySetup(error.to_string()))?;
        authority
            .install_navigation_graph(prepared.navigation_graph.clone())
            .map_err(|error| ServerError::AuthoritySetup(error.to_string()))?;
        let capture_bases = prepared
            .capture_bases
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?;
        let canonical_capture_bases = [
            [capture_bases[0][0].x, capture_bases[0][0].z],
            [capture_bases[1][0].x, capture_bases[1][0].z],
        ];
        if !authority
            .planner_mut()
            .install_capture_bases(&prepared.map_name, canonical_capture_bases)
        {
            return Err(ServerError::AuthoritySetup(
                "native capture bases cannot be installed in the planner".to_owned(),
            ));
        }
        let native_destructible_space_id =
            i64::try_from(native_world.destructibles.native_space_id).map_err(|_| {
                ServerError::AuthoritySetup(
                    "native destructible space id exceeds the signed protocol range".to_owned(),
                )
            })?;
        authority
            .donate_native_entities(native_world.donation)
            .map_err(|error| ServerError::AuthoritySetup(error.to_string()))?;
        authority
            .install_destructible_native_space_id(native_destructible_space_id)
            .map_err(|error| ServerError::AuthoritySetup(error.to_string()))?;
        let mounted_shots = prepared.mounted_shots()?;
        let hull_materials = prepared.hull_materials()?;
        let vehicle_extents = prepared.vehicle_extents()?;
        let vehicle_ram_shapes = prepared.vehicle_ram_shapes()?;
        authority
            .install_bot_map_envelopes(&vehicle_ram_shapes)
            .map_err(|error| ServerError::AuthoritySetup(error.to_string()))?;
        let vehicle_masses = prepared.vehicle_masses()?;
        let vehicle_ram_profiles = prepared.vehicle_ram_profiles()?;
        let planner_manifest = prepared.planner_manifest()?;
        let player_ammo = prepared.player_ammo_ledgers()?;
        let player_bursts = prepared.player_burst_descriptors()?;
        let player_fire = prepared.player_fire_inputs()?;
        let player_equipment = prepared.player_equipment_ledgers()?;
        let bot_equipment = prepared
            .bots
            .iter()
            .map(|bot| {
                BotEquipmentLedger::new(bot.id, 0.0, native_world.bot_consumables.clone())
                    .map(|ledger| (bot.id, ledger))
                    .map_err(|error| {
                        ServerError::AuthoritySetup(format!(
                            "bot {} equipment initialization failed: {error}",
                            bot.id
                        ))
                    })
            })
            .collect::<Result<BTreeMap<_, _>, _>>()?;
        let spotting_inputs = prepared.spotting_inputs()?;
        let participant_ids: std::collections::HashSet<_> = prepared
            .participants
            .iter()
            .map(|participant| participant.player_id)
            .collect();
        let mut engine = prepared.build_engine()?;
        for (&connection_id, connection) in &self.connections {
            if matches!(
                connection.session,
                EndpointSession::Player(ref session)
                    if participant_ids.contains(&session.player_id())
            ) {
                engine.add_replication_endpoint(connection_id);
            }
        }
        let event_roster = EventRoster::try_new(
            prepared
                .participants
                .iter()
                .map(|participant| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Player,
                            id: u64::from(participant.player_id),
                        },
                        participant.team.number(),
                    )
                })
                .chain(prepared.bots.iter().map(|bot| {
                    (
                        VehicleKey {
                            kind: VehicleKind::Bot,
                            id: bot.id,
                        },
                        bot.team.number(),
                    )
                })),
        )?;
        let mut battle = BattleLoop::new(engine);
        battle
            .install_authority(
                authority,
                mounted_shots,
                hull_materials,
                vehicle_extents,
                vehicle_ram_shapes,
                vehicle_masses,
                vehicle_ram_profiles,
                planner_manifest,
            )
            .map_err(ServerError::BattleLoop)?;
        battle
            .install_destructible_catalog(destructible_map)
            .map_err(ServerError::BattleLoop)?;
        battle
            .install_player_ammo(player_ammo, player_bursts)
            .map_err(ServerError::BattleLoop)?;
        battle
            .install_player_fire(player_fire)
            .map_err(ServerError::BattleLoop)?;
        battle
            .install_player_equipment(player_equipment)
            .map_err(ServerError::BattleLoop)?;
        battle
            .install_bot_equipment(bot_equipment)
            .map_err(ServerError::BattleLoop)?;
        battle
            .install_spotting_inputs(spotting_inputs)
            .map_err(ServerError::BattleLoop)?;
        self.battle = Some(battle);
        self.event_roster = Some(event_roster);
        Ok(())
    }

    fn enqueue_battle_message(
        &mut self,
        recv_seq: u64,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: WireObject,
    ) -> Result<(), ServerError> {
        let scope = match self.checked_scope(&message) {
            Ok(scope) => scope,
            Err(error) => return self.send_room_error(connection_id, error),
        };
        if self.room.phase() != RoomPhase::Battle {
            return self.send_error(
                connection_id,
                "not_in_battle",
                "battle input is only accepted after battle_live",
            );
        }
        if !self.room.is_round_player_active(session.player_id()) {
            return self.send_error(
                connection_id,
                "player_left_battle",
                "this player has retired from the current round",
            );
        }
        let Some(battle) = self.battle.as_mut() else {
            return self.send_error(
                connection_id,
                "battle_unavailable",
                "the authoritative battle loop is unavailable",
            );
        };
        battle
            .enqueue_player_message(
                recv_seq,
                connection_id,
                u64::from(session.player_id()),
                scope,
                message,
            )
            .map_err(ServerError::BattleLoop)
    }

    fn handle_landing_observation(
        &mut self,
        recv_seq: u64,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: WireObject,
    ) -> Result<(), ServerError> {
        let request = match LandingObservationRequest::parse(&message) {
            Ok(request) => request,
            Err(error) => {
                return self.send_error(connection_id, "invalid_battle_command", &error.to_string())
            }
        };
        let current_scope = self.room.scope();
        let committed_seq = self
            .battle
            .as_ref()
            .and_then(|battle| battle.landing_observation_seq(u64::from(session.player_id())))
            .unwrap_or(0);
        let checked_scope = match self.checked_scope(&message) {
            Ok(scope) => Some(scope),
            Err(RoomError::StaleScope { .. }) => None,
            Err(error) => return self.send_room_error(connection_id, error),
        };
        if request.round_id != current_scope.round_id {
            return self.send_landing_observation_result(
                connection_id,
                LandingObservationResult::rejected(
                    request,
                    current_scope,
                    committed_seq,
                    "stale_authority",
                ),
            );
        }
        if self.room.phase() != RoomPhase::Battle
            || !self.room.is_round_player_active(session.player_id())
            || self.battle.is_none()
        {
            let reason = if request.authority_epoch != current_scope.epoch {
                "stale_authority"
            } else {
                "not_active"
            };
            return self.send_landing_observation_result(
                connection_id,
                LandingObservationResult::rejected(request, current_scope, committed_seq, reason),
            );
        }

        // Keep same-round stale epochs inside the fixed-tick ledger. Its
        // fingerprint check intentionally precedes the authority fence so an
        // exact retry can replay the already committed result, matching the
        // Python authority contract.
        let scope = checked_scope.unwrap_or_else(|| request.scope());
        self.battle
            .as_mut()
            .expect("battle presence was checked above")
            .enqueue_player_message(
                recv_seq,
                connection_id,
                u64::from(session.player_id()),
                scope,
                message,
            )
            .map_err(ServerError::BattleLoop)
    }

    fn send_landing_observation_result(
        &mut self,
        connection_id: ConnectionId,
        result: LandingObservationResult,
    ) -> Result<(), ServerError> {
        self.send_nonfatal(
            connection_id,
            wire(serde_json::to_value(result).map_err(WireError::InvalidJson)?)?,
        )
    }

    fn handle_leave_battle(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
        message: &WireObject,
    ) -> Result<(), ServerError> {
        let scope = match self.checked_scope(message) {
            Ok(scope) => scope,
            Err(error) => return self.send_room_error(connection_id, error),
        };
        let mut staged_room = self.room.clone();
        let outcome = match staged_room.leave_round(session, scope) {
            Ok(outcome) => outcome,
            Err(error) => return self.send_room_error(connection_id, error),
        };
        let player_id = outcome.player_id;
        let battle_activated = outcome.battle_activated;
        if outcome.round_abandoned {
            let result = self.abandoned_round_result()?;
            self.finalize_round_from_room(
                staged_room,
                result,
                OUT_OF_BAND_RESULT_ORDINAL,
                Some(player_id),
            )?;
        } else if self.battle.is_some() {
            let pre_retire = self.capture_result_snapshot(&staged_room)?;
            self.freeze_departing_result_actor(player_id, &pre_retire)?;
            let existing_result = self
                .battle
                .as_ref()
                .and_then(|battle| battle.engine().result().cloned());
            let result = if existing_result.is_some() {
                existing_result
            } else {
                let battle = self.battle.as_mut().expect("the battle was checked above");
                battle
                    .engine_mut()
                    .retire_player(scope, u64::from(player_id))
                    .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
                battle.engine().result().cloned()
            };
            if let Some(result) = result {
                self.install_final_result_snapshot(pre_retire)?;
                self.finalize_round_from_room(
                    staged_room,
                    result,
                    OUT_OF_BAND_RESULT_ORDINAL,
                    None,
                )?;
            } else {
                self.room = staged_room;
            }
        } else {
            self.room = staged_room;
        }
        self.client_manifest_lineage.remove(&connection_id);
        if battle_activated && self.room.phase() != RoomPhase::Finished {
            self.publish_ready_outcome(ReadyOutcome::Activated {
                scope,
                state_revision: self.room.state_revision(),
            })?;
        }
        self.broadcast_roster()
    }

    fn timeout_until_next_battle_tick(&self, requested: Duration) -> Duration {
        self.battle.as_ref().map_or(requested, |battle| {
            battle.timeout_until_next_tick(requested)
        })
    }

    fn timeout_until_round_reset(&self, requested: Duration) -> Duration {
        let now = Instant::now();
        self.result_reset_deadline.map_or(requested, |deadline| {
            requested.min(deadline.saturating_duration_since(now))
        })
    }

    fn timeout_until_native_prerequisite(&self, requested: Duration) -> Duration {
        let now = Instant::now();
        self.native_prerequisite_deadline
            .map(|deadline| deadline.saturating_duration_since(now))
            .map_or(requested, |deadline| requested.min(deadline))
    }

    fn fail_native_prerequisite_if_due(&mut self) -> Result<(), ServerError> {
        let Some(deadline) = self.native_prerequisite_deadline else {
            return Ok(());
        };
        if Instant::now() < deadline {
            return Ok(());
        }
        let snapshot = self.descriptor_exchange.snapshot();
        let native_gates_ready = snapshot.as_ref().is_some_and(|snapshot| {
            snapshot.round_id == self.room.round_id() && snapshot.status == ExchangeStatus::Ready
        });
        if native_gates_ready {
            let reason = if self.native_world_ready.is_some() {
                "battle_ready_timeout"
            } else {
                "native_world_timeout"
            };
            return self.fail_loading_prerequisite(reason);
        }
        if snapshot.as_ref().is_some_and(|snapshot| {
            snapshot.round_id == self.room.round_id()
                && snapshot.descriptor_gate == DescriptorGate::Complete
                && snapshot.destructible_gate == DestructibleGate::AwaitingInstall
        }) && self.native_world_ready.is_none()
        {
            return self.fail_loading_prerequisite("native_world_timeout");
        }
        match self.descriptor_exchange.timeout(self.room.round_id())? {
            ExchangeEvent::Failed(failure) => self.fail_native_prerequisite(failure),
            _ => Ok(()),
        }
    }

    fn reset_finished_round_if_due(&mut self) -> Result<(), ServerError> {
        let Some(deadline) = self.result_reset_deadline else {
            return Ok(());
        };
        if Instant::now() < deadline {
            return Ok(());
        }
        let scope = self.room.scope();
        self.room
            .reset_round(scope)
            .map_err(ServerError::RoomState)?;
        self.prepared_round = None;
        self.battle = None;
        self.event_roster = None;
        self.result_reset_deadline = None;
        self.native_prerequisite_deadline = None;
        self.pending_start_requested_by = None;
        self.client_manifest_lineage.clear();
        self.native_world_ready = None;
        self.deferred_player_ready.clear();
        self.deferred_oracle_ready = None;
        self.broadcast_roster()
    }

    fn drive_battle(&mut self) -> Result<(), ServerError> {
        let server_time_ms = self.server_time_ms();
        let output = match self.battle.as_mut() {
            Some(battle) => match battle.poll(server_time_ms) {
                Ok(output) => output,
                Err(error) => {
                    if let Some(reason) = recoverable_battle_failure(&error) {
                        return self.finalize_recoverable_battle_failure(reason);
                    }
                    return Err(ServerError::BattleLoop(error));
                }
            },
            None => return Ok(()),
        };
        self.publish_battle_output(output)
    }

    fn finalize_recoverable_battle_failure(
        &mut self,
        reason: &'static str,
    ) -> Result<(), ServerError> {
        let result = {
            let battle = self
                .battle
                .as_mut()
                .ok_or(ServerError::MissingCaptureBases)?;
            battle
                .engine_mut()
                .oracle_failed(reason)
                .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
            battle.engine().result().cloned().ok_or_else(|| {
                ServerError::AuthoritySetup("terminal draw was not recorded".to_owned())
            })?
        };
        self.publish_client_snapshots()?;
        self.finalize_round(result, OUT_OF_BAND_RESULT_ORDINAL)
    }

    fn publish_battle_output(&mut self, output: BattleLoopOutput) -> Result<(), ServerError> {
        for rejection in output.rejections {
            self.send_error(rejection.connection_id, rejection.code, &rejection.message)?;
        }
        for effect in output.effects {
            match effect {
                CommandEffect::LandingObservation {
                    connection_id,
                    result,
                    ..
                } => self.send_landing_observation_result(connection_id, result)?,
                CommandEffect::AmmoIntent {
                    connection_id,
                    scope,
                    player_id,
                    intent,
                    outcome,
                } => {
                    let mut result = json!({
                        "type": "ammo_intent_result",
                        "round_id": scope.round_id,
                        "authority_epoch": scope.epoch,
                        "player_id": player_id,
                        "intent_seq": intent.intent_seq,
                        "input_seq": intent.input_seq,
                        "outcome": outcome.wire_kind(),
                    });
                    if let Some(shell_index) = outcome.shell_index() {
                        result
                            .as_object_mut()
                            .expect("ammo intent result is an object")
                            .insert("shell_index".to_owned(), json!(shell_index));
                    }
                    self.send_nonfatal(connection_id, wire(result)?)?;
                }
                _ => {}
            }
        }
        let mut terminal_result = None;
        for request in output.oracle_requests {
            let Some(connection_id) = self.native_oracle.map(|oracle| oracle.connection_id) else {
                if let Some(battle) = self.battle.as_mut() {
                    battle
                        .engine_mut()
                        .oracle_failed("worker_disconnected")
                        .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
                    terminal_result = battle
                        .engine()
                        .result()
                        .cloned()
                        .map(|result| (result, OUT_OF_BAND_RESULT_ORDINAL));
                }
                break;
            };
            self.send_nonfatal(
                connection_id,
                wire(json!({"type": "query_batch", "payload": request}))?,
            )?;
        }
        let mut advanced = false;
        for tick in output.ticks {
            advanced = true;
            if let Some(result) = tick.result.clone() {
                let ordinal = u16::try_from(tick.client_events.len()).map_err(|_| {
                    ServerError::AuthoritySetup(
                        "terminal tick produced too many client events".to_owned(),
                    )
                })?;
                terminal_result = Some((result, ordinal));
            }
            if !tick.client_events.is_empty() {
                let roster = self
                    .event_roster
                    .clone()
                    .ok_or(ServerError::MissingCaptureBases)?;
                let message = encode_battle_events(&BattleEventsFrame {
                    scope: self.client_frame_scope(self.room.scope(), tick.tick),
                    first_ordinal: 0,
                    roster,
                    events: tick.client_events,
                })?;
                self.broadcast_transition(message)?;
            }
            for emission in tick.emissions {
                // BattleEngine's scheduler remains the reliable ordering source,
                // but its generic snapshot shape is internal. Visible #1513
                // clients receive the strict protocol-v5 projection below.
                if emission.message.kind() == "snapshot" {
                    continue;
                }
                let message = emission.message;
                let Some(connection) = self.connections.get(&emission.endpoint_id) else {
                    continue;
                };
                let offered = match emission.delivery {
                    crate::net::DeliveryClass::Reliable => {
                        connection.sender.offer_reliable(message).map(|_| ())
                    }
                    crate::net::DeliveryClass::Snapshot => {
                        connection.sender.offer_snapshot(message).map(|_| ())
                    }
                };
                if offered.is_err() {
                    connection.sender.close();
                }
            }
        }
        if advanced {
            let tick = self
                .battle
                .as_ref()
                .map_or(0, |battle| battle.engine().tick());
            if tick % 2 == 0 || terminal_result.is_some() {
                self.publish_client_snapshots()?;
            }
        }
        if let Some((result, ordinal)) = terminal_result {
            self.finalize_round(result, ordinal)?;
        }
        Ok(())
    }

    fn publish_client_snapshots(&mut self) -> Result<(), ServerError> {
        let snapshot = self.client_snapshot()?;
        let endpoints: Vec<_> = self
            .connections
            .iter()
            .filter_map(|(&connection_id, connection)| match &connection.session {
                EndpointSession::Player(session)
                    if self.room.is_round_player_active(session.player_id()) =>
                {
                    Some(connection_id)
                }
                EndpointSession::Oracle(_) => Some(connection_id),
                _ => None,
            })
            .collect();
        for connection_id in endpoints {
            let (message, reliable) = match self.client_manifest_lineage.get(&connection_id) {
                Some(lineage) => (
                    encode_snapshot(&snapshot, SnapshotManifest::Lean(lineage))?,
                    false,
                ),
                None => {
                    let message = encode_snapshot(&snapshot, SnapshotManifest::Full)?;
                    let lineage = FullManifestLineage::from_full_snapshot(&message)?;
                    self.client_manifest_lineage.insert(connection_id, lineage);
                    (message, true)
                }
            };
            let Some(connection) = self.connections.get(&connection_id) else {
                continue;
            };
            let offered = if reliable {
                connection.sender.offer_reliable(message).map(|_| ())
            } else {
                connection.sender.offer_snapshot(message).map(|_| ())
            };
            if offered.is_err() {
                connection.sender.close();
            }
        }
        Ok(())
    }

    fn finalize_round(
        &mut self,
        result: BattleResult,
        result_ordinal: u16,
    ) -> Result<(), ServerError> {
        self.finalize_round_from_room(self.room.clone(), result, result_ordinal, None)
    }

    fn finalize_round_from_room(
        &mut self,
        base_room: RoomState,
        result: BattleResult,
        result_ordinal: u16,
        retire_after_persist: Option<u32>,
    ) -> Result<(), ServerError> {
        if self.room.phase() == RoomPhase::Finished {
            return Ok(());
        }
        self.freeze_final_result_snapshot(&base_room)?;
        let receipt_payloads = self.build_result_receipts(&result)?;
        let result_message = self.build_battle_result_message(&result, result_ordinal)?;
        let scope = base_room.scope();
        let mut finished_room = base_room;
        finished_room
            .finish_round(scope, result, ReceiptPolicy::Participants(receipt_payloads))
            .map_err(ServerError::RoomState)?;

        // The live room and clients must not observe a result whose receipts
        // failed to reach durable storage. Stage the entire room transition,
        // atomically persist its ledger, then commit the infallible in-memory
        // swap before publishing the already-encoded result.
        self.receipt_store
            .replace_from_room(finished_room.receipt_ledger())
            .map_err(ServerError::ReceiptStore)?;
        self.room = finished_room;
        if let Some(battle) = self.battle.as_mut() {
            if let Some(player_id) = retire_after_persist {
                if battle.engine().result().is_none() {
                    battle
                        .engine_mut()
                        .retire_player(scope, u64::from(player_id))
                        .map_err(|error| ServerError::BattleLoop(BattleLoopError::Battle(error)))?;
                }
            }
            battle.mark_terminal();
        }
        self.publish_battle_result(result_message);
        self.deliver_available_receipts()?;
        self.result_reset_deadline = Some(Instant::now() + RESULT_RESET_DELAY);
        Ok(())
    }

    fn build_battle_result_message(
        &self,
        result: &BattleResult,
        ordinal: u16,
    ) -> Result<Option<WireObject>, ServerError> {
        let Some(battle) = self.battle.as_ref() else {
            return Ok(None);
        };
        let engine = battle.engine();
        let message = encode_battle_result(&BattleResultFrame {
            scope: self.client_frame_scope(engine.scope(), engine.tick()),
            ordinal,
            result: client_battle_result(result, Some(engine.statistics())),
        })?;
        Ok(Some(message))
    }

    fn publish_battle_result(&mut self, message: Option<WireObject>) {
        let Some(message) = message else {
            return;
        };
        let participant_ids: BTreeSet<_> = self
            .prepared_round
            .as_ref()
            .map(|prepared| {
                prepared
                    .participants
                    .iter()
                    .map(|participant| participant.player_id)
                    .collect()
            })
            .unwrap_or_default();
        for connection in self.connections.values() {
            if !matches!(
                &connection.session,
                EndpointSession::Player(session)
                    if participant_ids.contains(&session.player_id())
            ) {
                continue;
            }
            if connection.sender.offer_reliable(message.clone()).is_err() {
                connection.sender.close();
            }
        }
    }

    fn capture_result_snapshot(
        &self,
        room: &RoomState,
    ) -> Result<FrozenResultSnapshot, ServerError> {
        let prepared = self
            .prepared_round
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?;
        let engine_entities: BTreeMap<_, _> = self
            .battle
            .as_ref()
            .map(|battle| {
                battle
                    .engine()
                    .entities()
                    .map(|view| (view.key, view))
                    .collect()
            })
            .unwrap_or_default();
        let active_player_ids: BTreeSet<_> = prepared
            .participants
            .iter()
            .filter(|participant| room.is_round_player_active(participant.player_id))
            .map(|participant| participant.player_id)
            .collect();
        let mut actors = Vec::with_capacity(prepared.participants.len() + prepared.bots.len());
        for participant in &prepared.participants {
            let key = VehicleKey {
                kind: VehicleKind::Player,
                id: u64::from(participant.player_id),
            };
            if let Some(actor) = prepared.frozen_result_actors.get(&key) {
                actors.push(actor.clone());
                continue;
            }
            let state = engine_entities.get(&key);
            let remained = active_player_ids.contains(&participant.player_id);
            actors.push(ResultActor {
                key,
                name: participant.name.clone(),
                vehicle: participant.vehicle.clone(),
                team: participant.team.number(),
                health: state.map_or(if remained { participant.max_health } else { 0 }, |view| {
                    view.combat.health
                }),
                alive: state.map_or(remained, |view| view.combat.alive),
                death_reason: state.map_or(if remained { -1 } else { 0 }, |view| {
                    if view.combat.alive {
                        -1
                    } else {
                        i32::from(view.death_reason)
                    }
                }),
                killer: state.and_then(|view| view.combat.death_attacker),
                team_killer: state.is_some_and(|view| view.combat.team_killer),
                vehicle_tier: prepared
                    .descriptors
                    .get(&participant.vehicle)
                    .map_or(1, |descriptor| descriptor.level),
            });
        }
        for bot in &prepared.bots {
            let key = VehicleKey {
                kind: VehicleKind::Bot,
                id: bot.id,
            };
            let state = engine_entities.get(&key);
            actors.push(ResultActor {
                key,
                name: bot.name.clone(),
                vehicle: bot.vehicle.clone(),
                team: bot.team.number(),
                health: state.map_or(bot.max_health, |view| view.combat.health),
                alive: state.is_none_or(|view| view.combat.alive),
                death_reason: state.map_or(-1, |view| {
                    if view.combat.alive {
                        -1
                    } else {
                        i32::from(view.death_reason)
                    }
                }),
                killer: state.and_then(|view| view.combat.death_attacker),
                team_killer: false,
                vehicle_tier: bot
                    .descriptor
                    .as_ref()
                    .map_or(1, |descriptor| descriptor.level),
            });
        }
        let statistics = self
            .battle
            .as_ref()
            .map(|battle| battle.engine().statistics())
            .cloned()
            .unwrap_or_default();
        let duration_ticks = self
            .battle
            .as_ref()
            .map_or(0, |battle| battle.engine().tick());
        let arena_unique_id =
            (prepared.scope.round_id << 32) | ((unix_time_seconds() as u64) & u64::from(u32::MAX));
        Ok(FrozenResultSnapshot {
            actors,
            active_player_ids,
            statistics,
            duration_ticks,
            arena_unique_id,
        })
    }

    fn freeze_departing_result_actor(
        &mut self,
        player_id: u32,
        snapshot: &FrozenResultSnapshot,
    ) -> Result<(), ServerError> {
        let key = VehicleKey {
            kind: VehicleKind::Player,
            id: u64::from(player_id),
        };
        let actor = snapshot
            .actors
            .iter()
            .find(|actor| actor.key == key)
            .cloned()
            .ok_or(ServerError::ResultBuild(
                ResultBuildError::MissingParticipant(player_id),
            ))?;
        self.prepared_round
            .as_mut()
            .ok_or(ServerError::MissingCaptureBases)?
            .frozen_result_actors
            .entry(key)
            .or_insert(actor);
        Ok(())
    }

    fn freeze_final_result_snapshot(&mut self, room: &RoomState) -> Result<(), ServerError> {
        if self
            .prepared_round
            .as_ref()
            .is_some_and(|prepared| prepared.final_result_snapshot.is_some())
        {
            return Ok(());
        }
        let snapshot = self.capture_result_snapshot(room)?;
        self.prepared_round
            .as_mut()
            .ok_or(ServerError::MissingCaptureBases)?
            .final_result_snapshot = Some(snapshot);
        Ok(())
    }

    fn install_final_result_snapshot(
        &mut self,
        snapshot: FrozenResultSnapshot,
    ) -> Result<(), ServerError> {
        let prepared = self
            .prepared_round
            .as_mut()
            .ok_or(ServerError::MissingCaptureBases)?;
        if prepared.final_result_snapshot.is_none() {
            prepared.final_result_snapshot = Some(snapshot);
        }
        Ok(())
    }

    fn build_result_receipts(
        &self,
        result: &BattleResult,
    ) -> Result<BTreeMap<u32, Value>, ServerError> {
        let prepared = self
            .prepared_round
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?;
        let snapshot = prepared.final_result_snapshot.as_ref().ok_or_else(|| {
            ServerError::AuthoritySetup("result snapshot is not frozen".to_owned())
        })?;
        build_receipt_payloads(ReceiptBuildContext {
            receipt_namespace: &self.room.config().receipt_namespace,
            arena_unique_id: snapshot.arena_unique_id,
            round_id: prepared.scope.round_id,
            map: &self.map,
            duration_ticks: snapshot.duration_ticks,
            result,
            participants: &prepared.participants,
            actors: &snapshot.actors,
            active_player_ids: &snapshot.active_player_ids,
            statistics: &snapshot.statistics,
        })
        .map_err(ServerError::ResultBuild)
    }

    fn deliver_available_receipts(&mut self) -> Result<(), ServerError> {
        let players: Vec<_> = self
            .connections
            .iter()
            .filter_map(|(&connection_id, connection)| match &connection.session {
                EndpointSession::Player(session) => Some((connection_id, session.clone())),
                EndpointSession::Oracle(_) => None,
            })
            .collect();
        for (connection_id, session) in players {
            self.offer_next_receipt(connection_id, &session)?;
        }
        Ok(())
    }

    fn offer_next_receipt(
        &mut self,
        connection_id: ConnectionId,
        session: &PlayerSession,
    ) -> Result<(), ServerError> {
        let Some(receipt) = self
            .room
            .next_receipt(session)
            .map_err(ServerError::RoomState)?
        else {
            return Ok(());
        };
        let message = WireObject::try_from(receipt.payload)?;
        if !self.offer_active(connection_id, message) {
            self.remove_active_connection(connection_id, false)?;
        }
        Ok(())
    }

    fn checked_scope(&self, message: &WireObject) -> Result<SimulationScope, RoomError> {
        let received_round = message.get("round_id").and_then(exact_u64);
        let received_epoch = message.get("authority_epoch").and_then(exact_u64);
        let expected = self.room.scope();
        let received = SimulationScope {
            round_id: received_round.unwrap_or(u64::MAX),
            // Protocol-v5 start/ready messages predate the explicit epoch
            // field. If absent, bind them to the authenticated connection's
            // current epoch; an explicit stale value is always rejected.
            epoch: received_epoch.unwrap_or(expected.epoch),
        };
        if received != expected {
            return Err(RoomError::StaleScope { expected, received });
        }
        Ok(received)
    }

    fn reply_pong(
        &mut self,
        connection_id: ConnectionId,
        request: &WireObject,
    ) -> Result<(), ServerError> {
        let mut fields = Map::new();
        fields.insert(
            "seq".to_owned(),
            request.get("seq").cloned().unwrap_or(Value::Null),
        );
        fields.insert(
            "client_time".to_owned(),
            request.get("client_time").cloned().unwrap_or(Value::Null),
        );
        fields.insert("server_time".to_owned(), json!(unix_time_seconds()));
        let message = WireObject::with_fields("pong", fields)?;
        if !self.offer_active(connection_id, message)
            && self.remove_active_connection(connection_id, false)?
        {
            self.broadcast_roster()?;
        }
        Ok(())
    }

    fn send_team_denied(
        &mut self,
        connection_id: ConnectionId,
        code: &str,
        team: Option<&Value>,
    ) -> Result<(), ServerError> {
        let message = wire(json!({
            "type": "team_denied",
            "protocol": LAN_PROTOCOL_VERSION,
            "round_id": self.room.round_id(),
            "state_revision": self.room.state_revision(),
            "code": code,
            "team": team.cloned().unwrap_or(Value::Null),
            "team_sizes": self.team_sizes_value(),
        }))?;
        self.send_nonfatal(connection_id, message)
    }

    fn send_team_size_denied(
        &mut self,
        connection_id: ConnectionId,
        code: &str,
        team: Option<&Value>,
        size: Option<&Value>,
    ) -> Result<(), ServerError> {
        let message = wire(json!({
            "type": "team_size_denied",
            "protocol": LAN_PROTOCOL_VERSION,
            "round_id": self.room.round_id(),
            "state_revision": self.room.state_revision(),
            "code": code,
            "team": team.cloned().unwrap_or(Value::Null),
            "size": size.cloned().unwrap_or(Value::Null),
            "team_sizes": self.team_sizes_value(),
        }))?;
        self.send_nonfatal(connection_id, message)
    }

    fn send_bot_tier_mode_denied(
        &mut self,
        connection_id: ConnectionId,
        code: &str,
        mode: Option<&Value>,
    ) -> Result<(), ServerError> {
        let message = wire(json!({
            "type": "bot_tier_mode_denied",
            "protocol": LAN_PROTOCOL_VERSION,
            "round_id": self.room.round_id(),
            "state_revision": self.room.state_revision(),
            "code": code,
            "mode": mode.cloned().unwrap_or(Value::Null),
            "bot_tier_mode": self.room.bot_tier_mode().as_str(),
        }))?;
        self.send_nonfatal(connection_id, message)
    }

    fn send_start_denied(
        &mut self,
        connection_id: ConnectionId,
        code: &str,
    ) -> Result<(), ServerError> {
        let message = wire(json!({
            "type": "start_denied",
            "protocol": LAN_PROTOCOL_VERSION,
            "round_id": self.room.round_id(),
            "state_revision": self.room.state_revision(),
            "code": code,
            "players": self.room.player_count(),
        }))?;
        self.send_nonfatal(connection_id, message)
    }

    fn send_room_error(
        &mut self,
        connection_id: ConnectionId,
        error: RoomError,
    ) -> Result<(), ServerError> {
        self.send_error(connection_id, error.code(), &error.to_string())
    }

    fn send_error(
        &mut self,
        connection_id: ConnectionId,
        code: &str,
        message: &str,
    ) -> Result<(), ServerError> {
        let message = error_message(code, message)?;
        self.send_nonfatal(connection_id, message)
    }

    fn send_nonfatal(
        &mut self,
        connection_id: ConnectionId,
        message: WireObject,
    ) -> Result<(), ServerError> {
        if !self.offer_active(connection_id, message)
            && self.remove_active_connection(connection_id, false)?
        {
            self.broadcast_roster()?;
        }
        Ok(())
    }

    fn reject_new(
        &mut self,
        connection_id: ConnectionId,
        sender: SendHandle,
        code: &str,
        message: &str,
    ) -> Result<(), ServerError> {
        let error = error_message(code, message)?;
        self.queue_error_then_close(connection_id, sender, error);
        Ok(())
    }

    fn reject_active(
        &mut self,
        connection_id: ConnectionId,
        code: &str,
        message: &str,
    ) -> Result<(), ServerError> {
        let Some(connection) = self.connections.remove(&connection_id) else {
            return Ok(());
        };
        self.client_manifest_lineage.remove(&connection_id);
        self.deferred_player_ready.remove(&connection_id);
        if self
            .deferred_oracle_ready
            .as_ref()
            .is_some_and(|(known_connection, _)| *known_connection == connection_id)
        {
            self.deferred_oracle_ready = None;
        }
        if self
            .native_oracle
            .is_some_and(|oracle| oracle.connection_id == connection_id)
        {
            self.native_oracle = None;
            self.native_world_ready = None;
        }
        self.detach_session(&connection.session)?;
        let error = error_message(code, message)?;
        self.queue_error_then_close(connection_id, connection.sender, error);
        self.broadcast_roster()
    }

    fn queue_error_then_close(
        &mut self,
        connection_id: ConnectionId,
        sender: SendHandle,
        error: WireObject,
    ) {
        self.queue_message_then_close(connection_id, sender, error);
    }

    fn queue_message_then_close(
        &mut self,
        connection_id: ConnectionId,
        sender: SendHandle,
        message: WireObject,
    ) {
        if sender.offer_reliable(message).is_err() {
            sender.close();
            return;
        }
        // SendHandle::close() clears the current outbox. Give its writer a
        // short bounded chance to pop the error before closing the socket.
        self.pending_closes.insert(
            connection_id,
            PendingClose {
                sender,
                deadline: Instant::now() + ERROR_DRAIN_GRACE,
            },
        );
    }

    fn close_due_rejections(&mut self) {
        let now = Instant::now();
        let due: Vec<_> = self
            .pending_closes
            .iter()
            .filter_map(|(&connection_id, pending)| {
                (pending.deadline <= now).then_some(connection_id)
            })
            .collect();
        for connection_id in due {
            if let Some(pending) = self.pending_closes.remove(&connection_id) {
                pending.sender.close();
            }
        }
    }

    fn timeout_until_next_close(&self, requested: Duration) -> Duration {
        let now = Instant::now();
        self.pending_closes
            .values()
            .map(|pending| pending.deadline.saturating_duration_since(now))
            .min()
            .map_or(requested, |pending| requested.min(pending))
    }

    fn offer_active(&self, connection_id: ConnectionId, message: WireObject) -> bool {
        let Some(connection) = self.connections.get(&connection_id) else {
            return false;
        };
        if connection.sender.offer_reliable(message).is_ok() {
            true
        } else {
            connection.sender.close();
            false
        }
    }

    fn broadcast_roster(&mut self) -> Result<(), ServerError> {
        loop {
            let base = roster_message(&self.room, &self.map)?;
            let message = if self.room.phase() == RoomPhase::Waiting {
                base
            } else {
                with_bot_authority(base, SERVER_AUTHORITY_ID)?
            };
            let mut failed = Vec::new();
            for (&connection_id, connection) in &self.connections {
                if connection.sender.offer_reliable(message.clone()).is_err() {
                    connection.sender.close();
                    failed.push(connection_id);
                }
            }
            if failed.is_empty() {
                return Ok(());
            }
            for connection_id in failed {
                self.remove_active_connection(connection_id, false)?;
            }
            if self.connections.is_empty() {
                return Ok(());
            }
        }
    }

    fn broadcast_transition(&mut self, message: WireObject) -> Result<(), ServerError> {
        let message = with_bot_authority(message, SERVER_AUTHORITY_ID)?;
        let mut failed = Vec::new();
        for (&connection_id, connection) in &self.connections {
            if connection.sender.offer_reliable(message.clone()).is_err() {
                connection.sender.close();
                failed.push(connection_id);
            }
        }
        if failed.is_empty() {
            return Ok(());
        }
        for connection_id in failed {
            self.remove_active_connection(connection_id, false)?;
        }
        self.broadcast_roster()
    }

    fn remove_active_connection(
        &mut self,
        connection_id: ConnectionId,
        close_transport: bool,
    ) -> Result<bool, ServerError> {
        let Some(connection) = self.connections.remove(&connection_id) else {
            return Ok(false);
        };
        self.client_manifest_lineage.remove(&connection_id);
        self.deferred_player_ready.remove(&connection_id);
        if self
            .deferred_oracle_ready
            .as_ref()
            .is_some_and(|(known_connection, _)| *known_connection == connection_id)
        {
            self.deferred_oracle_ready = None;
        }
        if self
            .native_oracle
            .is_some_and(|oracle| oracle.connection_id == connection_id)
        {
            self.native_oracle = None;
            self.native_world_ready = None;
        }
        if close_transport {
            connection.sender.close();
        }
        if matches!(&connection.session, EndpointSession::Player(_)) {
            if let Some(battle) = self.battle.as_mut() {
                battle
                    .engine_mut()
                    .remove_replication_endpoint(connection_id);
            }
        }
        self.detach_session(&connection.session)?;
        Ok(true)
    }

    fn abandoned_round_result(&self) -> Result<BattleResult, ServerError> {
        if let Some(result) = self
            .battle
            .as_ref()
            .and_then(|battle| battle.engine().result().cloned())
        {
            return Ok(result);
        }

        if let Some(engine) = self.battle.as_ref().map(|battle| battle.engine()) {
            for base_team in [Team::One, Team::Two] {
                if engine.rules().state(base_team).points >= 100 {
                    let winner = match base_team {
                        Team::One => Team::Two,
                        Team::Two => Team::One,
                    };
                    return BattleResult::new(
                        BattleWinner::Team(winner),
                        "base captured",
                        Some(base_team),
                    )
                    .map_err(ServerError::RoomState);
                }
            }
        }

        let prepared = self
            .prepared_round
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?;
        let engine_states = self.battle.as_ref().map(|battle| {
            battle
                .engine()
                .entities()
                .map(|entity| (entity.key, entity.combat))
                .collect::<BTreeMap<_, _>>()
        });
        let states = prepared
            .bots
            .iter()
            .map(|bot| {
                let key = VehicleKey {
                    kind: VehicleKind::Bot,
                    id: bot.id,
                };
                let state = engine_states
                    .as_ref()
                    .map(|states| {
                        states.get(&key).ok_or_else(|| {
                            ServerError::AuthoritySetup(format!(
                                "canonical battle state is missing bot {}",
                                bot.id
                            ))
                        })
                    })
                    .transpose()?;
                Ok(RemainingBotState {
                    team: bot.team,
                    health: state.map_or(bot.max_health, |state| state.health),
                    max_health: bot.max_health,
                    alive: state.is_none_or(|state| state.alive),
                })
            })
            .collect::<Result<Vec<_>, ServerError>>()?;
        BattleResult::new(
            remaining_bot_winner(prepared.scope.round_id, states),
            "team_eliminated",
            None,
        )
        .map_err(ServerError::RoomState)
    }

    fn detach_session(&mut self, session: &EndpointSession) -> Result<(), ServerError> {
        match session {
            EndpointSession::Player(session) => {
                let phase = self.room.phase();
                let scope = self.room.scope();
                let was_active = self.room.is_round_player_active(session.player_id());
                let descriptor_failure = match self
                    .descriptor_exchange
                    .donor_disconnected(session.player_id())
                {
                    Some(ExchangeEvent::Failed(failure)) => Some(failure),
                    _ => None,
                };
                let mut staged_room = self.room.clone();
                let outcome = staged_room
                    .disconnect_player(session)
                    .map_err(ServerError::RoomState)?;
                if outcome.round_reset {
                    self.room = staged_room;
                    self.prepared_round = None;
                    self.battle = None;
                    self.event_roster = None;
                    self.result_reset_deadline = None;
                    self.native_prerequisite_deadline = None;
                    self.pending_start_requested_by = None;
                    self.client_manifest_lineage.clear();
                    self.native_world_ready = None;
                    self.deferred_player_ready.clear();
                    self.deferred_oracle_ready = None;
                    return Ok(());
                }
                if let Some(failure) = descriptor_failure {
                    self.room = staged_room;
                    return self.fail_native_prerequisite(failure);
                }
                if phase == RoomPhase::Battle {
                    let engine_result = self
                        .battle
                        .as_ref()
                        .and_then(|battle| battle.engine().result().cloned());
                    let any_active = self.prepared_round.as_ref().is_some_and(|prepared| {
                        prepared.participants.iter().any(|participant| {
                            staged_room.is_round_player_active(participant.player_id)
                        })
                    });
                    if let Some(result) = engine_result {
                        self.finalize_round_from_room(
                            staged_room,
                            result,
                            OUT_OF_BAND_RESULT_ORDINAL,
                            None,
                        )?;
                    } else if !any_active {
                        let result = self.abandoned_round_result()?;
                        self.finalize_round_from_room(
                            staged_room,
                            result,
                            OUT_OF_BAND_RESULT_ORDINAL,
                            was_active.then_some(session.player_id()),
                        )?;
                    } else if was_active && self.battle.is_some() {
                        let pre_retire = self.capture_result_snapshot(&staged_room)?;
                        self.freeze_departing_result_actor(session.player_id(), &pre_retire)?;
                        let result = {
                            let battle =
                                self.battle.as_mut().expect("the battle was checked above");
                            battle
                                .engine_mut()
                                .retire_player(scope, u64::from(session.player_id()))
                                .map_err(|error| {
                                    ServerError::BattleLoop(BattleLoopError::Battle(error))
                                })?;
                            battle.engine().result().cloned()
                        };
                        if let Some(result) = result {
                            self.install_final_result_snapshot(pre_retire)?;
                            self.finalize_round_from_room(
                                staged_room,
                                result,
                                OUT_OF_BAND_RESULT_ORDINAL,
                                None,
                            )?;
                        } else {
                            self.room = staged_room;
                        }
                    } else {
                        self.room = staged_room;
                    }
                } else {
                    self.room = staged_room;
                }
                if outcome.battle_activated && self.room.phase() != RoomPhase::Finished {
                    return self.publish_ready_outcome(ReadyOutcome::Activated {
                        scope,
                        state_revision: self.room.state_revision(),
                    });
                }
                Ok(())
            }
            EndpointSession::Oracle(session) => {
                if matches!(self.room.phase(), RoomPhase::Loading | RoomPhase::Battle) {
                    let result = if let Some(battle) = self.battle.as_mut() {
                        battle
                            .engine_mut()
                            .oracle_failed("worker_disconnected")
                            .map_err(|error| {
                                ServerError::BattleLoop(BattleLoopError::Battle(error))
                            })?;
                        battle.engine().result().cloned().unwrap_or(
                            BattleResult::new(BattleWinner::Draw, "worker_disconnected", None)
                                .map_err(ServerError::RoomState)?,
                        )
                    } else {
                        BattleResult::new(BattleWinner::Draw, "worker_disconnected", None)
                            .map_err(ServerError::RoomState)?
                    };
                    self.finalize_round(result, OUT_OF_BAND_RESULT_ORDINAL)?;
                }
                self.room
                    .detach_oracle(session)
                    .map(|_| ())
                    .map_err(ServerError::RoomState)
            }
        }
    }

    fn oracle_welcome(
        &self,
        capabilities: &[String],
        oracle_generation: u64,
    ) -> Result<WireObject, WireError> {
        wire(json!({
            "type": "welcome",
            "protocol": LAN_PROTOCOL_VERSION,
            "role": SIMULATION_WORKER_ROLE,
            "worker_id": SIMULATION_WORKER_ID,
            "oracle_generation": oracle_generation,
            "client_build": CLIENT_BUILD_0922,
            "capabilities": capabilities,
            "server_capabilities": SERVER_CAPABILITIES,
            "map": self.map,
            "map_pool": MAP_POOL_0922,
            "host_player_id": self.room.host_player_id(),
            "phase": phase_name(self.room.phase()),
            "round_id": self.room.round_id(),
            "state_revision": self.room.state_revision(),
            "bot_authority_id": SERVER_AUTHORITY_ID,
            "authority_epoch": self.room.authority_epoch(),
            "server_time_ms": self.server_time_ms(),
            "team_size": self.room.config().team_capacities[0]
                .max(self.room.config().team_capacities[1]),
            "team_sizes": self.team_sizes_value(),
        }))
    }

    fn battle_start_message(
        &self,
        requested_by: u32,
        scope: SimulationScope,
    ) -> Result<WireObject, ServerError> {
        let prepared = self
            .prepared_round
            .as_ref()
            .filter(|prepared| prepared.scope == scope)
            .ok_or(ServerError::MissingCaptureBases)?;
        let team_sizes = self.client_team_sizes();
        Ok(encode_battle_start(&ClientBattleStart {
            client_build: CLIENT_BUILD_0922.to_owned(),
            scope: self.client_frame_scope(scope, 0),
            recipient_player_id: u64::from(requested_by),
            map: self.map.clone(),
            requested_by: u64::from(requested_by),
            host_player_id: u64::from(self.room.host_player_id().ok_or(ServerError::MissingHost)?),
            delay_seconds: 0.75,
            need_destructible_map: true,
            players: self.client_players(true),
            bots: prepared
                .bots
                .iter()
                .map(PreparedBot::client_roster)
                .collect(),
            team_sizes,
            bot_tier_mode: self.room.bot_tier_mode(),
            bot_lineup: self.config.bot_lineup.clone(),
            bot_manifest: prepared
                .bots
                .iter()
                .map(PreparedBot::client_manifest)
                .collect(),
            bot_order_revision: 0,
            bot_orders: prepared
                .bots
                .iter()
                .map(PreparedBot::client_order)
                .collect(),
            rules: self.client_rules(),
            battle_result: None,
            destructible_revision: 0,
            destructibles: Vec::new(),
        })?)
    }

    fn battle_live_message(&self, scope: SimulationScope) -> Result<WireObject, ServerError> {
        Ok(encode_battle_live(&ClientBattleLive {
            client_build: CLIENT_BUILD_0922.to_owned(),
            scope: self.client_frame_scope(scope, 0),
            countdown_seconds: PREBATTLE_SECONDS,
            battle_duration_seconds: BATTLE_DURATION_SECONDS,
            timing: self.client_timing(0, None),
        })?)
    }

    fn client_frame_scope(&self, scope: SimulationScope, tick: u64) -> FrameScope {
        FrameScope {
            round_id: scope.round_id,
            authority_epoch: scope.epoch.min(i32::MAX as u64),
            server_tick: tick,
            server_time_ms: self.server_time_ms().min(i32::MAX as u64),
            state_revision: self.room.state_revision().min(i32::MAX as u64),
        }
    }

    fn client_team_sizes(&self) -> [u8; 2] {
        [
            self.room.config().team_capacities[0].min(15) as u8,
            self.room.config().team_capacities[1].min(15) as u8,
        ]
    }

    fn client_players(&self, include_outfits: bool) -> Vec<ClientPlayerState> {
        let Some(prepared) = self.prepared_round.as_ref() else {
            return Vec::new();
        };
        let entities: BTreeMap<_, _> = self
            .battle
            .as_ref()
            .map(|battle| {
                battle
                    .engine()
                    .entities()
                    .map(|view| (view.key, view))
                    .collect()
            })
            .unwrap_or_default();
        prepared
            .participants
            .iter()
            .map(|participant| {
                let key = VehicleKey {
                    kind: VehicleKind::Player,
                    id: u64::from(participant.player_id),
                };
                let input_seq = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.engine().player_input_seq(key.id))
                    .unwrap_or(0);
                let up_cosine = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.engine().player_up_cosine(key.id))
                    .unwrap_or(1.0);
                let landing_observation_seq = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.landing_observation_seq(key.id))
                    .unwrap_or(0);
                let critical = self.battle.as_ref().map(|battle| {
                    battle
                        .engine()
                        .client_critical_snapshot(key)
                        .expect("round critical profile invariant")
                });
                let stun = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.engine().projectile_stun_state(key))
                    .cloned();
                let siege = self
                    .battle
                    .as_ref()
                    .map_or((0, 0), |battle| battle.engine().siege_status(key.id));
                let ram = self
                    .battle
                    .as_ref()
                    .map_or_else(PlayerRamProjection::default, |battle| {
                        battle.ram_player_projection(key.id)
                    });
                let player_pair_ram = self
                    .battle
                    .as_ref()
                    .map_or_else(PlayerRamLedgerState::default, |battle| {
                        battle.player_pair_ram_state(key.id)
                    });
                let ammo = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.player_ammo_snapshot(key.id));
                let burst = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.player_burst_snapshot(key.id));
                let equipment = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.player_equipment_snapshot(key.id))
                    .or_else(|| {
                        participant
                            .vehicle_configuration
                            .as_object()
                            .and_then(|configuration| configuration.get("effective_params"))
                            .and_then(|effective_params| {
                                PlayerEquipmentLedger::from_effective_params(
                                    key.id,
                                    0.0,
                                    effective_params,
                                )
                                .ok()
                            })
                            .map(|ledger| ledger.snapshot())
                    });
                client_player_state(
                    participant,
                    prepared
                        .spawn_pose(participant.team, participant.slot)
                        .expect("validated navigation graph covers every room slot"),
                    entities.get(&key),
                    input_seq,
                    up_cosine,
                    landing_observation_seq,
                    include_outfits,
                    critical,
                    stun.as_ref(),
                    siege,
                    ram,
                    player_pair_ram,
                    ammo.as_ref(),
                    burst.as_ref(),
                    equipment.as_ref(),
                )
            })
            .collect()
    }

    fn client_bots(&self) -> Vec<ClientBotState> {
        let Some(prepared) = self.prepared_round.as_ref() else {
            return Vec::new();
        };
        let entities: BTreeMap<_, _> = self
            .battle
            .as_ref()
            .map(|battle| {
                battle
                    .engine()
                    .entities()
                    .map(|view| (view.key, view))
                    .collect()
            })
            .unwrap_or_default();
        let server_time_ms = self.server_time_ms();
        prepared
            .bots
            .iter()
            .map(|bot| {
                let key = VehicleKey {
                    kind: VehicleKind::Bot,
                    id: bot.id,
                };
                let critical = self.battle.as_ref().map(|battle| {
                    battle
                        .engine()
                        .client_critical_snapshot(key)
                        .expect("round critical profile invariant")
                });
                let stun = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.engine().projectile_stun_state(key))
                    .cloned();
                let fire = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.engine().fire_runtime(key, server_time_ms));
                let equipment = self
                    .battle
                    .as_ref()
                    .and_then(|battle| battle.bot_equipment_snapshot(key.id));
                let simulation = self
                    .battle
                    .as_ref()
                    .and_then(BattleLoop::authority)
                    .and_then(|authority| {
                        u32::try_from(bot.id)
                            .ok()
                            .and_then(|bot_id| authority.bot_state(bot_id))
                    });
                client_bot_state(
                    bot,
                    entities.get(&key),
                    critical,
                    stun.as_ref(),
                    fire,
                    equipment.as_deref(),
                    simulation,
                )
            })
            .collect()
    }

    fn client_rules(&self) -> RulesState {
        let state = |team| {
            self.battle
                .as_ref()
                .map(|battle| battle.engine().rules().state(team))
                .unwrap_or_default()
        };
        let convert = |base: crate::rules::BaseCaptureState| CaptureBaseState {
            points: base.points.min(100) as u8,
            time_left: base.time_left_seconds.max(0.0),
            invaders: base.invaders.min(u8::MAX as usize) as u8,
            stopped: base.stopped,
        };
        RulesState {
            team_1: convert(state(Team::One)),
            team_2: convert(state(Team::Two)),
        }
    }

    fn client_destructibles(&self) -> (u64, Vec<ClientDestructibleState>) {
        let Some(engine) = self.battle.as_ref().map(BattleLoop::engine) else {
            return (0, Vec::new());
        };
        let revision = engine.destructibles().revision();
        let entries = engine
            .destructibles()
            .entries()
            .map(|stored| ClientDestructibleState {
                destructible_kind: match stored.receipt.key.kind {
                    crate::destructible::DestructibleKind::Tree => ClientDestructibleKind::Tree,
                    crate::destructible::DestructibleKind::Column => ClientDestructibleKind::Column,
                    crate::destructible::DestructibleKind::Fragile => {
                        ClientDestructibleKind::Fragile
                    }
                    crate::destructible::DestructibleKind::Module => ClientDestructibleKind::Module,
                },
                chunk_id: stored.receipt.key.chunk_id,
                item_index: stored.receipt.key.item_index,
                mat_kind: stored.receipt.key.material_kind,
                x: stored.receipt.x,
                y: stored.receipt.y,
                z: stored.receipt.z,
                fall_yaw: stored.receipt.fall_yaw,
                speed: stored.receipt.speed,
                is_shot: stored.receipt.is_shot,
                revision: stored.revision,
            })
            .collect();
        (revision, entries)
    }

    fn client_timing(&self, tick: u64, result: Option<&BattleResult>) -> TimingState {
        let duration_ms = (BATTLE_DURATION_SECONDS * 1_000.0) as u64;
        if result.is_some() {
            return TimingState {
                phase: CombatPhase::Finished,
                start_in_ms: 0,
                remaining_ms: 0,
                duration_ms,
            };
        }
        if tick < PREBATTLE_TICKS {
            return TimingState {
                phase: CombatPhase::Prebattle,
                start_in_ms: tick_offset(PREBATTLE_TICKS - tick).as_millis() as u64,
                remaining_ms: duration_ms,
                duration_ms,
            };
        }
        TimingState {
            phase: CombatPhase::Battle,
            start_in_ms: 0,
            remaining_ms: tick_offset(TERMINAL_TICK.saturating_sub(tick)).as_millis() as u64,
            duration_ms,
        }
    }

    fn client_snapshot(&self) -> Result<SnapshotFrame, ServerError> {
        let battle = self
            .battle
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?;
        let engine = battle.engine();
        let tick = engine.tick();
        let prepared = self
            .prepared_round
            .as_ref()
            .ok_or(ServerError::MissingCaptureBases)?;
        let (destructible_revision, destructibles) = self.client_destructibles();
        let motion_time_us = tick_offset(tick).as_micros().min(u64::MAX as u128) as u64;
        let result = engine
            .result()
            .map(|result| client_battle_result(result, Some(engine.statistics())));
        Ok(SnapshotFrame {
            scope: self.client_frame_scope(engine.scope(), tick),
            map: self.map.clone(),
            motion_time_us,
            bot_state_time_us: motion_time_us,
            players: self.client_players(false),
            bots: self.client_bots(),
            contacts: battle
                .contact_authority()
                .latest_contacts()
                .iter()
                .map(|contact| ClientContactState {
                    observing_team: contact.observing_team,
                    target_kind: match contact.target.kind {
                        crate::spotting::ContactTargetKind::Human => "human",
                        crate::spotting::ContactTargetKind::Bot => "bot",
                    }
                    .to_owned(),
                    target_id: u64::from(contact.target.id),
                    target_team: contact.target_team,
                    visible: contact.presentation_visible,
                    fresh: contact.visible,
                    time_left: contact.expires_at_us.saturating_sub(motion_time_us) as f64
                        / 1_000_000.0,
                    visible_by_bot_ids: contact
                        .visible_by_bot_ids
                        .iter()
                        .copied()
                        .map(u64::from)
                        .collect(),
                    visible_by_player_ids: contact
                        .visible_by_player_ids
                        .iter()
                        .copied()
                        .map(u64::from)
                        .collect(),
                    shootable_by_bot_ids: contact
                        .shootable_by_bot_ids
                        .iter()
                        .copied()
                        .map(u64::from)
                        .collect(),
                })
                .collect(),
            bot_state_revision: tick.min(i32::MAX as u64),
            bot_manifest: prepared
                .bots
                .iter()
                .map(PreparedBot::client_manifest)
                .collect(),
            bot_orders: RevisionPayload::Include {
                revision: 0,
                values: prepared
                    .bots
                    .iter()
                    .map(PreparedBot::client_order)
                    .collect(),
            },
            rules: self.client_rules(),
            battle_result: result,
            destructibles: RevisionPayload::Include {
                revision: destructible_revision,
                values: destructibles,
            },
            timing: self.client_timing(tick, engine.result()),
            projectile_revision: engine.projectiles().revision(),
            projectiles: engine
                .projectiles()
                .active()
                .values()
                .map(ProjectileWireState::from)
                .collect(),
        })
    }

    fn team_sizes_value(&self) -> Value {
        json!({
            "1": self.room.config().team_capacities[0],
            "2": self.room.config().team_capacities[1],
        })
    }

    fn server_time_ms(&self) -> u64 {
        self.started_at.elapsed().as_millis().min(u64::MAX as u128) as u64
    }

    fn next_session_token(&mut self, connection_id: ConnectionId) -> String {
        self.token_nonce = self.token_nonce.wrapping_add(1);
        let wall_clock = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        format!(
            "rust-{:x}-{:x}-{:x}-{:x}",
            std::process::id(),
            wall_clock,
            connection_id,
            self.token_nonce
        )
    }
}

fn client_player_state(
    participant: &RoundParticipant,
    spawn_pose: BodyPose,
    entity: Option<&BattleEntityView>,
    input_seq: u64,
    up_cosine: f64,
    landing_observation_seq: u64,
    include_outfits: bool,
    critical: Option<(
        crate::client_replication::CriticalPayload,
        ClientCriticalRevision,
    )>,
    stun: Option<&ProjectileStunState>,
    siege: (u8, u32),
    ram: PlayerRamProjection,
    player_pair_ram: PlayerRamLedgerState,
    ammo: Option<&PlayerAmmoSnapshot>,
    burst: Option<&PlayerBurstSnapshot>,
    equipment: Option<&PlayerEquipmentSnapshot>,
) -> ClientPlayerState {
    let pose = entity.map_or(spawn_pose, |view| view.pose);
    let health = entity.map_or(participant.max_health, |view| view.combat.health);
    let alive = entity.is_none_or(|view| view.combat.alive);
    let (death_attacker_kind, death_attacker_id) =
        attacker_fields(entity.and_then(|view| view.combat.death_attacker));
    let (stun_attacker_kind, stun_attacker_id) = attacker_fields(stun.map(|state| state.attacker));
    let (critical_payload, critical_revision, critical_base_revision, critical_ack_seq) = critical
        .map_or((None, 0, 0, 0), |(payload, revision)| {
            let (revision, base_revision, ack_seq) = critical_revision_values(revision);
            (
                Some(serde_json::to_value(payload).expect("typed critical payload serializes")),
                revision,
                base_revision,
                ack_seq,
            )
        });
    let fallback_remaining =
        configuration_ammo_remaining(&participant.vehicle_configuration).unwrap_or_else(|| vec![0]);
    let fallback_loaded =
        configuration_ammo_loaded_shell(&participant.vehicle_configuration, &fallback_remaining)
            .unwrap_or(0);
    ClientPlayerState {
        id: u64::from(participant.player_id),
        name: participant.name.clone(),
        vehicle: participant.vehicle.clone(),
        vehicle_compact_descr: configuration_string(
            &participant.vehicle_configuration,
            "vehicle_compact_descr",
        ),
        team: participant.team.number(),
        slot: participant.slot.min(14) as u8,
        world_pose: entity.is_some_and(|view| view.world_pose),
        spawn_x: spawn_pose.x,
        spawn_z: spawn_pose.z,
        x: pose.x,
        y: pose.y,
        z: pose.z,
        yaw: pose.yaw,
        pitch: pose.pitch,
        roll: pose.roll,
        aim_yaw: pose.aim_yaw,
        gun_pitch: pose.gun_pitch,
        forward: 0.0,
        turn: 0.0,
        speed: if alive { pose.speed } else { 0.0 },
        input_seq,
        landing_observation_seq,
        up_cosine,
        siege_state: siege.0,
        siege_time_left_ms: siege.1,
        fire_seq: entity.map_or(0, |view| view.last_fire_seq),
        shell_index: ammo.map_or(fallback_loaded, |state| state.loaded_shell),
        next_shell_index: ammo.map_or(fallback_loaded, |state| state.next_shell),
        ammo_remaining: ammo.map_or(fallback_remaining, |state| state.remaining.clone()),
        ammo_reload_pending: ammo.is_some_and(|state| state.reload_pending),
        reload_time: ammo.map_or(0.0, |state| state.reload_remaining_seconds),
        reload_duration: ammo.map_or(1.0, |state| state.reload_duration_seconds),
        clip: ammo.map_or(1, |state| state.clip_remaining),
        clip_size: ammo.map_or(1, |state| state.clip_size),
        burst_active: burst.is_some_and(|state| state.active),
        burst_group_seq: burst.map_or(0, |state| state.group_seq),
        burst_count: burst.map_or(0, |state| state.count),
        burst_next_index: burst.map_or(0, |state| state.next_index),
        burst_interval: burst.map_or(0.0, |state| state.interval_seconds),
        burst_time_left: burst.map_or(0.0, |state| state.time_left_seconds),
        burst_shell_index: burst.map_or(fallback_loaded, |state| state.shell_index),
        health,
        max_health: participant.max_health,
        alive,
        death_reason: entity.map_or(0, |view| view.death_reason),
        display_health: health,
        frags: entity.map_or(0, |view| view.combat.frags),
        team_killer: entity.is_some_and(|view| view.combat.team_killer),
        death_attacker_kind,
        death_attacker_id,
        stun_end_server_time_ms: stun.map_or(0, |state| state.end_server_time_ms),
        stun_attacker_kind,
        stun_attacker_id,
        critical_revision,
        critical_base_revision,
        critical_ack_seq,
        equipment_states: equipment.map_or_else(Vec::new, |snapshot| {
            snapshot
                .equipment_states
                .iter()
                .map(|state| serde_json::to_value(state).expect("typed equipment state serializes"))
                .collect()
        }),
        equipment_revision: equipment.map_or(0, |snapshot| snapshot.equipment_revision),
        equipment_intent_seq: equipment.map_or(0, |snapshot| snapshot.equipment_intent_seq),
        equipment_intent_result: equipment.map_or_else(
            || {
                json!({
                    "intent_seq": 0,
                    "accepted": false,
                    "reason": "",
                })
            },
            |snapshot| {
                serde_json::to_value(&snapshot.equipment_intent_result)
                    .expect("typed equipment result serializes")
            },
        ),
        ram_contact_admitted_seq: ram.admitted_sequence,
        ram_contact_resolved_seq: ram.resolved_sequence,
        player_ram_contact_admitted_seq: player_pair_ram.admitted_sequence,
        player_ram_contact_resolved_seq: player_pair_ram.resolved_sequence,
        ram_contact_results: ram.results,
        outfits: include_outfits.then(|| configuration_outfits(&participant.vehicle_configuration)),
        effective_params: include_outfits.then(|| {
            participant
                .vehicle_configuration
                .as_object()
                .and_then(|configuration| configuration.get("effective_params"))
                .cloned()
                .unwrap_or(Value::Null)
        }),
        ram_contact: ram.contacts.last().cloned(),
        ram_contacts: ram.contacts,
        critical: critical_payload,
    }
}

fn client_bot_state(
    bot: &PreparedBot,
    entity: Option<&BattleEntityView>,
    critical: Option<(
        crate::client_replication::CriticalPayload,
        ClientCriticalRevision,
    )>,
    stun: Option<&ProjectileStunState>,
    fire: Option<FireRuntimeView>,
    equipment: Option<&[crate::player_equipment::EquipmentStateSnapshot]>,
    simulation: Option<&crate::bot_sim::BotState>,
) -> ClientBotState {
    let pose = entity.map_or(bot.pose, |view| view.pose);
    let health = entity.map_or(bot.max_health, |view| view.combat.health);
    let alive = entity.is_none_or(|view| view.combat.alive);
    let (death_attacker_kind, death_attacker_id) =
        attacker_fields(entity.and_then(|view| view.combat.death_attacker));
    let (stun_attacker_kind, stun_attacker_id) = attacker_fields(stun.map(|state| state.attacker));
    let (critical_payload, combat_revision, combat_base_revision, combat_ack_seq) = critical
        .map_or((json!({}), 0, 0, 0), |(payload, revision)| {
            let (revision, base_revision, ack_seq) = critical_revision_values(revision);
            (
                serde_json::to_value(payload).expect("typed critical payload serializes"),
                revision,
                base_revision,
                ack_seq,
            )
        });
    let ammo = simulation.map(|state| state.ammo.snapshot());
    ClientBotState {
        id: bot.id,
        team: bot.team.number(),
        slot: bot.slot.min(14) as u8,
        name: bot.name.clone(),
        vehicle: bot.vehicle.clone(),
        world_pose: entity.is_some_and(|view| view.world_pose),
        x: pose.x,
        y: pose.y,
        z: pose.z,
        yaw: pose.yaw,
        pitch: pose.pitch,
        roll: pose.roll,
        aim_yaw: pose.aim_yaw,
        gun_pitch: pose.gun_pitch,
        movement_dir: simulation.map_or_else(
            || {
                if pose.speed > 0.001 {
                    1
                } else if pose.speed < -0.001 {
                    -1
                } else {
                    0
                }
            },
            |state| state.movement_dir,
        ),
        rotation_dir: simulation.map_or(0, |state| state.rotation_dir),
        fire_seq: entity.map_or(0, |view| view.last_fire_seq),
        shell_index: ammo.as_ref().map_or_else(
            || entity.map_or(0, |view| view.shell_index),
            |ammo| ammo.loaded as u8,
        ),
        next_shell_index: ammo.as_ref().map_or_else(
            || entity.map_or(0, |view| view.shell_index),
            |ammo| ammo.next as u8,
        ),
        ammo_remaining: ammo
            .as_ref()
            .map_or_else(Vec::new, |ammo| ammo.remaining.clone()),
        ammo_reload_pending: ammo.is_some_and(|ammo| ammo.reload_pending),
        health,
        max_health: bot.max_health,
        alive,
        frags: entity.map_or(0, |view| view.combat.frags),
        team_killer: false,
        death_attacker_kind,
        death_attacker_id,
        combat_revision,
        combat_base_revision,
        combat_ack_seq,
        combat_fire_elapsed: fire.map_or(0.0, |runtime| runtime.elapsed_seconds),
        combat_fire_timer: fire.map_or(0.0, |runtime| runtime.timer_seconds),
        fire_attacker_kind: fire.map_or_else(String::new, |runtime| {
            match runtime.attacker.kind {
                VehicleKind::Player => "player",
                VehicleKind::Bot => "bot",
            }
            .to_owned()
        }),
        fire_attacker_id: fire.map_or(0, |runtime| runtime.attacker.id),
        stun_end_server_time_ms: stun.map_or(0, |state| state.end_server_time_ms),
        stun_attacker_kind,
        stun_attacker_id,
        equipment_states: equipment.map_or_else(Vec::new, |states| {
            states
                .iter()
                .map(|state| {
                    serde_json::to_value(state).expect("typed bot equipment snapshot serializes")
                })
                .collect()
        }),
        critical: critical_payload,
        shot_yaw: None,
        shot_pitch: None,
        death_reason: entity.map_or(0, |view| view.death_reason),
        display_health: health,
    }
}

fn recoverable_battle_failure(error: &BattleLoopError) -> Option<&'static str> {
    match error {
        BattleLoopError::Tick(crate::sim::TickError::SimulationOverrun { .. }) => {
            Some("simulation_overrun")
        }
        BattleLoopError::Battle(crate::battle::BattleError::OracleUnavailable) => {
            Some("native_oracle_unavailable")
        }
        _ => None,
    }
}

fn client_battle_result(
    result: &BattleResult,
    statistics: Option<&crate::statistics::StatisticsLedger>,
) -> BattleResultState {
    BattleResultState {
        winner: result.winner.number(),
        reason: result.reason.chars().take(64).collect(),
        base_team: result.base_team.map(Team::number).unwrap_or(0),
        vehicle_statistics: statistics.map(|statistics| {
            statistics
                .rows()
                .map(|(actor, row)| BattleVehicleStatistics::from_statistics(actor, row))
                .collect()
        }),
    }
}

fn attacker_fields(attacker: Option<VehicleKey>) -> (String, u64) {
    attacker.map_or_else(
        || (String::new(), 0),
        |key| {
            (
                match key.kind {
                    VehicleKind::Player => "player",
                    VehicleKind::Bot => "bot",
                }
                .to_owned(),
                key.id,
            )
        },
    )
}

fn critical_revision_values(revision: ClientCriticalRevision) -> (u64, u64, u64) {
    match revision {
        ClientCriticalRevision::Player {
            revision,
            base_revision,
            ack_seq,
        }
        | ClientCriticalRevision::Bot {
            revision,
            base_revision,
            ack_seq,
        } => (revision, base_revision, ack_seq),
    }
}

fn configuration_string(configuration: &Value, key: &str) -> String {
    configuration
        .as_object()
        .and_then(|value| value.get(key))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn configuration_outfits(configuration: &Value) -> BTreeMap<u8, String> {
    configuration
        .as_object()
        .and_then(|value| value.get("outfits"))
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|outfits| outfits.iter())
        .filter_map(|(season, encoded)| {
            let season = season.parse::<u8>().ok()?;
            matches!(season, 1 | 2 | 4)
                .then(|| encoded.as_str().map(|value| (season, value.to_owned())))
                .flatten()
        })
        .collect()
}

/// Bind and run a LAN server indefinitely.
pub fn serve(config: ServerConfig) -> Result<(), ServerError> {
    ServerApp::bind(config)?.run()
}

/// Bind and run until an embedding launcher requests shutdown.
pub fn serve_until(config: ServerConfig, stop: Arc<AtomicBool>) -> Result<(), ServerError> {
    ServerApp::bind(config)?.run_until(&stop)
}

fn validate_native_oracle_hello(
    hello: &Hello,
) -> Result<NativeOracleHello, (&'static str, &'static str)> {
    if hello.role() != ConnectionRole::SimulationWorker {
        return Err((
            "unsupported_role",
            "native oracle must use simulation_worker role",
        ));
    }
    if !hello
        .object()
        .get("client_build")
        .and_then(Value::as_str)
        .is_some_and(|value| !value.is_empty() && value.len() <= 128)
    {
        return Err((
            "unsupported_client_build",
            "native oracle build label is invalid",
        ));
    }
    let Some(generation) = hello
        .object()
        .get("oracle_generation")
        .and_then(exact_u64)
        .filter(|value| *value > 0)
    else {
        return Err((
            "invalid_oracle_generation",
            "native oracle generation must be a positive integer",
        ));
    };
    let Some(values) = hello.object().get("capabilities").and_then(Value::as_array) else {
        return Err((
            "unsupported_capabilities",
            "native oracle capabilities must be a list",
        ));
    };
    if values.len() > 32 {
        return Err((
            "unsupported_capabilities",
            "native oracle capability list is too large",
        ));
    }
    let mut capabilities = Vec::with_capacity(values.len());
    for value in values {
        let Some(capability) = value
            .as_str()
            .filter(|value| !value.is_empty() && value.len() <= 64)
        else {
            return Err((
                "unsupported_capabilities",
                "native oracle capability is invalid",
            ));
        };
        if capabilities.iter().any(|existing| existing == capability) {
            return Err((
                "unsupported_capabilities",
                "native oracle capabilities contain duplicates",
            ));
        }
        capabilities.push(capability.to_owned());
    }
    if !compatible_hello_protocol(hello, &capabilities) {
        return Err(("protocol", "native oracle protocol is incompatible"));
    }
    if ![
        SIMULATION_WORKER_CAPABILITY,
        NATIVE_ORACLE_V1,
        PROJECTILE_LEDGER_V2,
        DESTRUCTIBLE_CATALOG_V5,
        LEAN_SNAPSHOT_MANIFEST_V1,
        RAM_CONTACT_LEDGER_V3,
        HUMAN_RAM_TIMELINE_V1,
        HE_EXPLOSION_EVIDENCE_V1,
        PLAYER_FIRE_INTENT_V4,
        PLAYER_ENVIRONMENT_V2,
        EFFECTIVE_PARAMS_V1,
        RICOCHET_CONTINUATION_V1,
        PLAYER_AMMO_AUTHORITY_V1,
        PLAYER_AUTHORITY_LOADOUT_V1,
    ]
    .into_iter()
    .all(|required| capabilities.iter().any(|value| value == required))
    {
        return Err((
            "unsupported_capabilities",
            "native oracle is missing required capabilities",
        ));
    }
    Ok(NativeOracleHello {
        capabilities,
        generation,
    })
}

fn validate_native_world_ready(
    wire: NativeWorldReadyWire,
    prepared: &PreparedRound,
    scope: SimulationScope,
    oracle_generation: u64,
) -> Result<NativeWorldReady, String> {
    if wire.message_type != "oracle_world_ready"
        || wire.protocol != LAN_PROTOCOL_VERSION
        || wire.round_id != scope.round_id
        || wire.authority_epoch != scope.epoch
        || wire.oracle_generation != oracle_generation
        || wire.entity_revision == 0
        || !wire.complete
    {
        return Err("native world donation lineage or completion fence is invalid".to_owned());
    }
    if wire.oracle_space.entity_id <= 0 || wire.oracle_space.generation == 0 {
        return Err("native oracle space reference is invalid".to_owned());
    }
    if wire.destructibles.native_space_id == 0
        || wire.destructibles.expected_instances == 0
        || wire.destructibles.installed_instances != wire.destructibles.expected_instances
    {
        return Err("native destructible installation proof is incomplete".to_owned());
    }
    validate_bot_consumable_contracts(&wire.bot_consumables)
        .map_err(|error| format!("native bot consumable donation is invalid: {error}"))?;

    let expected_humans: BTreeSet<u32> = prepared
        .participants
        .iter()
        .map(|participant| participant.player_id)
        .collect();
    let expected_bots: BTreeSet<u32> = prepared
        .bots
        .iter()
        .map(|bot| {
            u32::try_from(bot.id)
                .map_err(|_| "prepared bot identity exceeds the native oracle range".to_owned())
        })
        .collect::<Result<_, _>>()?;
    if wire.entities.len() != expected_humans.len() + expected_bots.len() {
        return Err("native entity donation does not cover the complete round roster".to_owned());
    }

    let mut humans = BTreeMap::new();
    let mut bots = BTreeMap::new();
    let mut native_ids = BTreeSet::new();
    native_ids.insert(wire.oracle_space.entity_id);
    for row in wire.entities {
        if row.logical_id == 0
            || row.native.entity_id <= 0
            || row.native.generation == 0
            || !native_ids.insert(row.native.entity_id)
        {
            return Err(
                "native entity donation contains an invalid or duplicate entity".to_owned(),
            );
        }
        match row.kind.as_str() {
            "human" if expected_humans.contains(&row.logical_id) => {
                if humans.insert(row.logical_id, row.native).is_some() {
                    return Err("native entity donation contains a duplicate human".to_owned());
                }
            }
            "bot" if expected_bots.contains(&row.logical_id) => {
                if bots.insert(row.logical_id, row.native).is_some() {
                    return Err("native entity donation contains a duplicate bot".to_owned());
                }
            }
            _ => {
                return Err("native entity donation contains an unknown logical vehicle".to_owned());
            }
        }
    }
    if humans.keys().copied().collect::<BTreeSet<_>>() != expected_humans
        || bots.keys().copied().collect::<BTreeSet<_>>() != expected_bots
    {
        return Err("native entity donation does not exactly match the round roster".to_owned());
    }

    Ok(NativeWorldReady {
        entity_revision: wire.entity_revision,
        donation: NativeEntityDonation {
            lineage: OracleLineage {
                round_id: scope.round_id,
                authority_epoch: scope.epoch,
                oracle_generation,
            },
            oracle_space: wire.oracle_space,
            bots,
            humans,
        },
        destructibles: wire.destructibles,
        bot_consumables: wire.bot_consumables,
    })
}

fn parse_vehicle_selection(
    message: &WireObject,
    current: &crate::room::PlayerView,
) -> Result<(String, u32, Value), &'static str> {
    let vehicle = message
        .get("vehicle")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 64)
        .ok_or("invalid_vehicle")?
        .to_owned();
    let max_health = message
        .get("max_health")
        .and_then(exact_u64)
        .filter(|value| *value > 0)
        .map(|value| value.min(100_000) as u32)
        .ok_or("invalid_max_health")?;
    let current_configuration = current.vehicle_configuration.as_object();
    let outfits = message.get("outfits").cloned().unwrap_or_else(|| {
        current_configuration
            .and_then(|value| value.get("outfits"))
            .cloned()
            .unwrap_or_else(|| json!({}))
    });
    let compact = message
        .get("vehicle_compact_descr")
        .cloned()
        .unwrap_or_else(|| {
            current_configuration
                .and_then(|value| value.get("vehicle_compact_descr"))
                .cloned()
                .unwrap_or(Value::String(String::new()))
        });
    let effective_params = message.get("effective_params").cloned().unwrap_or_else(|| {
        if vehicle == current.vehicle {
            current_configuration
                .and_then(|value| value.get("effective_params"))
                .cloned()
                .unwrap_or(Value::Null)
        } else {
            Value::Null
        }
    });
    let ammo_remaining = message.get("ammo_remaining").cloned().unwrap_or_else(|| {
        current_configuration
            .and_then(|value| value.get("ammo_remaining"))
            .cloned()
            .unwrap_or(Value::Null)
    });
    let ammo_loaded_shell = message
        .get("ammo_loaded_shell")
        .cloned()
        .unwrap_or_else(|| {
            current_configuration
                .and_then(|value| value.get("ammo_loaded_shell"))
                .cloned()
                .unwrap_or(Value::Null)
        });
    let player_authority_loadout = message
        .get("player_authority_loadout")
        .cloned()
        .unwrap_or_else(|| {
            if vehicle == current.vehicle {
                current_configuration
                    .and_then(|value| value.get("player_authority_loadout"))
                    .cloned()
                    .unwrap_or(Value::Null)
            } else {
                Value::Null
            }
        });
    validate_vehicle_configuration(
        &outfits,
        &compact,
        &ammo_remaining,
        &ammo_loaded_shell,
        &player_authority_loadout,
        &effective_params,
    )?;
    Ok((
        vehicle,
        max_health,
        json!({
            "outfits": outfits,
            "vehicle_compact_descr": compact,
            "ammo_remaining": ammo_remaining,
            "ammo_loaded_shell": ammo_loaded_shell,
            "player_authority_loadout": player_authority_loadout,
            "effective_params": effective_params,
        }),
    ))
}

fn validate_vehicle_configuration(
    outfits: &Value,
    compact: &Value,
    ammo_remaining: &Value,
    ammo_loaded_shell: &Value,
    player_authority_loadout: &Value,
    effective_params: &Value,
) -> Result<(), &'static str> {
    let outfits = outfits.as_object().ok_or("invalid_vehicle_configuration")?;
    if outfits.len() > 3
        || outfits.iter().any(|(season, value)| {
            !matches!(season.as_str(), "1" | "2" | "4")
                || value
                    .as_str()
                    .is_none_or(|value| value.is_empty() || value.len() > 96 * 1024)
        })
        || compact.as_str().is_none_or(|value| value.len() > 96 * 1024)
        || !valid_ammo_remaining(ammo_remaining)
        || !valid_ammo_loaded_shell(ammo_remaining, ammo_loaded_shell)
        || !valid_effective_params(effective_params)
        || (!player_authority_loadout.is_null()
            && parse_player_authority_loadout(player_authority_loadout).is_err())
    {
        return Err("invalid_vehicle_configuration");
    }
    Ok(())
}

fn valid_ammo_remaining(value: &Value) -> bool {
    if value.is_null() {
        return true;
    }
    let Some(values) = value.as_array() else {
        return false;
    };
    if !(1..=16).contains(&values.len()) {
        return false;
    }
    values
        .iter()
        .try_fold(0_u64, |total, value| {
            total.checked_add(value.as_u64().filter(|count| *count <= 1_000)?)
        })
        .is_some_and(|total| total <= 1_000)
}

fn valid_ammo_loaded_shell(ammo_remaining: &Value, loaded_shell: &Value) -> bool {
    if ammo_remaining.is_null() {
        return loaded_shell.is_null();
    }
    let Some(remaining) = ammo_remaining.as_array() else {
        return false;
    };
    let Some(index) = loaded_shell
        .as_u64()
        .and_then(|value| usize::try_from(value).ok())
    else {
        return false;
    };
    index < remaining.len()
        && (remaining.iter().all(|value| value.as_u64() == Some(0))
            || remaining[index].as_u64().is_some_and(|count| count > 0))
}

fn configuration_ammo_remaining(configuration: &Value) -> Option<Vec<u16>> {
    let values = configuration
        .as_object()?
        .get("ammo_remaining")?
        .as_array()?;
    if !(1..=16).contains(&values.len()) {
        return None;
    }
    let mut total = 0_u64;
    let mut remaining = Vec::with_capacity(values.len());
    for value in values {
        let count = value.as_u64().filter(|count| *count <= 1_000)?;
        total = total.checked_add(count)?;
        if total > 1_000 {
            return None;
        }
        remaining.push(u16::try_from(count).ok()?);
    }
    Some(remaining)
}

fn configuration_ammo_loaded_shell(configuration: &Value, remaining: &[u16]) -> Option<u8> {
    let index = configuration
        .as_object()?
        .get("ammo_loaded_shell")?
        .as_u64()
        .and_then(|value| usize::try_from(value).ok())?;
    if index >= remaining.len()
        || (remaining.iter().any(|count| *count > 0) && remaining[index] == 0)
    {
        return None;
    }
    u8::try_from(index).ok()
}

fn configuration_intuition_chances(configuration: &Value) -> Option<u8> {
    let skills = configuration
        .as_object()?
        .get("effective_params")?
        .as_object()?
        .get("skills")?
        .as_object()?;
    if skills.len() != 2
        || skills.get("deadeye").and_then(Value::as_bool).is_none()
        || !skills.contains_key("intuition_chances")
    {
        return None;
    }
    skills
        .get("intuition_chances")?
        .as_u64()
        .filter(|value| *value <= 16)
        .and_then(|value| u8::try_from(value).ok())
}

fn configuration_effective_ram_inputs(configuration: &Value) -> Option<EffectiveRamInputs> {
    let effective_params = configuration.as_object()?.get("effective_params")?;
    effective_ram_inputs(effective_params)
}

fn configuration_player_authority_loadout(
    configuration: &Value,
) -> Result<crate::descriptor::PlayerAuthorityLoadout, String> {
    let value = configuration
        .as_object()
        .and_then(|fields| fields.get("player_authority_loadout"))
        .filter(|value| !value.is_null())
        .ok_or_else(|| "connection-scoped donation is missing".to_owned())?;
    parse_player_authority_loadout(value).map_err(|error| error.to_string())
}

fn selected_map(
    requested: Option<&Value>,
    current: &str,
    selection_seed: u64,
    round_id: u64,
) -> Option<String> {
    let requested = match requested {
        None | Some(Value::Null) => current,
        Some(Value::String(value)) if !value.is_empty() => value,
        _ => return None,
    };
    if requested == "server_random" {
        if MAP_POOL_0922.is_empty() {
            return None;
        }
        let index = random_map_index(selection_seed, round_id, MAP_POOL_0922.len());
        return Some(MAP_POOL_0922[index].to_owned());
    }
    MAP_POOL_0922
        .contains(&requested)
        .then(|| requested.to_owned())
}

fn random_map_index(seed: u64, round_id: u64, length: usize) -> usize {
    debug_assert!(length > 0);
    let mut value = seed ^ round_id.wrapping_mul(0x9e37_79b9_7f4a_7c15);
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^= value >> 31;
    (value % length as u64) as usize
}

fn parse_capture_bases(message: &WireObject) -> Result<Option<[Vec<MapPoint>; 2]>, &'static str> {
    let Some(raw) = message.get("bases") else {
        return Ok(None);
    };
    let object = raw
        .as_object()
        .ok_or("bases must be an object keyed by team 1 and 2")?;
    let mut parsed = [Vec::new(), Vec::new()];
    for (index, key) in ["1", "2"].into_iter().enumerate() {
        let values = object
            .get(key)
            .and_then(Value::as_array)
            .filter(|values| (1..=4).contains(&values.len()))
            .ok_or("each team must contain between one and four capture points")?;
        for value in values {
            let (x, z) = match value {
                Value::Object(point) => (
                    point.get("x").and_then(Value::as_f64),
                    point.get("z").and_then(Value::as_f64),
                ),
                Value::Array(point) if point.len() >= 2 => (point[0].as_f64(), point[1].as_f64()),
                _ => return Err("capture points must be {x,z} objects or [x,z] arrays"),
            };
            let (Some(x), Some(z)) = (x, z) else {
                return Err("capture point coordinates must be numbers");
            };
            if !x.is_finite()
                || !z.is_finite()
                || !(-2_000.0..=2_000.0).contains(&x)
                || !(-2_000.0..=2_000.0).contains(&z)
            {
                return Err("capture point coordinates are outside the world bounds");
            }
            parsed[index].push(MapPoint::new(x, z));
        }
    }
    Ok(Some(parsed))
}

fn with_bot_authority(message: WireObject, authority_id: i64) -> Result<WireObject, WireError> {
    let kind = message.kind().to_owned();
    let mut fields = message.into_fields();
    fields.insert("bot_authority_id".to_owned(), json!(authority_id));
    WireObject::with_fields(kind, fields)
}

fn phase_name(phase: RoomPhase) -> &'static str {
    match phase {
        RoomPhase::Waiting => "waiting",
        RoomPhase::Loading => "loading",
        RoomPhase::Battle | RoomPhase::Finished => "battle",
    }
}

fn exact_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(number) => number.as_u64(),
        _ => None,
    }
}

fn wire(value: Value) -> Result<WireObject, WireError> {
    WireObject::try_from(value)
}

fn unix_time_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn process_namespace() -> String {
    let wall_clock = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("rust-{:x}-{wall_clock:x}", std::process::id())
}

fn process_random_seed() -> u64 {
    let wall_clock = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    (wall_clock as u64)
        ^ ((wall_clock >> 64) as u64)
        ^ u64::from(std::process::id()).rotate_left(17)
}

fn default_receipt_state_path(port: u16) -> PathBuf {
    let filename = format!("unacked_battle_receipts-{port}.json");
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join(&filename)))
        .unwrap_or_else(|| PathBuf::from(filename))
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
    use base64::Engine;
    use sha2::{Digest, Sha256};
    use std::fs;
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpStream;
    use std::path::Path;
    use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};

    static TEST_STATE_NONCE: AtomicU64 = AtomicU64::new(1);

    fn effective_params_projection(mass: f64, spall: f64, bonus: f64) -> Value {
        json!({
            "version": 1,
            "loadout": {
                "from_client_factors": true,
                "crew_factor": 1.0,
                "dispersion_factor": 1.0,
                "aim_time_factor": 1.0,
                "bloom_move_factor": 1.0,
                "bloom_rotation_factor": 1.0,
                "bloom_turret_factor": 1.0
            },
            "physics": {"mass": mass},
            "spotting": {},
            "ramming": {
                "spall_coefficient": spall,
                "ramming_bonus": bonus
            },
            "ammo": [],
            "camouflage": {},
            "skills": {"deadeye": false, "intuition_chances": 0},
            "crew": {},
            "gun": {},
            "equipment": [],
            "critical": {"activation_targets": []},
        })
    }

    struct TestClient {
        writer: TcpStream,
        reader: BufReader<TcpStream>,
    }

    impl TestClient {
        fn connect(address: SocketAddr, account: &str, name: &str) -> Self {
            Self::connect_with_hello(
                address,
                json!({
                    "type": "hello",
                    "protocol": LAN_PROTOCOL_VERSION,
                    "client_build": CLIENT_BUILD_0922,
                    "capabilities": [
                        PROJECTILE_LEDGER_V2,
                        DESTRUCTIBLE_CATALOG_V5,
                        LEAN_SNAPSHOT_MANIFEST_V1,
                        RAM_CONTACT_LEDGER_V3,
                        HUMAN_RAM_TIMELINE_V1,
                        PLAYER_FIRE_INTENT_V4,
                        PLAYER_ENVIRONMENT_V2,
                        EFFECTIVE_PARAMS_V1,
                        RICOCHET_CONTINUATION_V1,
                        PLAYER_AMMO_AUTHORITY_V1,
                        PLAYER_AUTHORITY_LOADOUT_V1,
                    ],
                    "account_key": account,
                    "name": name,
                    "vehicle": DEFAULT_VEHICLE_0922,
                    "max_health": 90,
                    "ammo_remaining": [51],
                    "ammo_loaded_shell": 0,
                    "player_authority_loadout": {
                        "repair": {"available": false},
                        "spotting": {"available": false},
                    },
                    "effective_params": effective_params_projection(8_000.0, 1.0, 0.0),
                    "outfits": {},
                    "vehicle_compact_descr": "",
                }),
            )
        }

        fn worker(address: SocketAddr) -> Self {
            Self::connect_with_hello(
                address,
                json!({
                    "type": "hello",
                    "protocol": LAN_PROTOCOL_VERSION,
                    "role": SIMULATION_WORKER_ROLE,
                    "client_build": CLIENT_BUILD_0922,
                    "capabilities": [
                        PROJECTILE_LEDGER_V2,
                        DESTRUCTIBLE_CATALOG_V5,
                        LEAN_SNAPSHOT_MANIFEST_V1,
                        RAM_CONTACT_LEDGER_V3,
                        HUMAN_RAM_TIMELINE_V1,
                        HE_EXPLOSION_EVIDENCE_V1,
                        PLAYER_FIRE_INTENT_V4,
                        PLAYER_ENVIRONMENT_V2,
                        EFFECTIVE_PARAMS_V1,
                        RICOCHET_CONTINUATION_V1,
                        PLAYER_AMMO_AUTHORITY_V1,
                        PLAYER_AUTHORITY_LOADOUT_V1,
                        SIMULATION_WORKER_CAPABILITY,
                        NATIVE_ORACLE_V1,
                    ],
                    "oracle_generation": 1,
                }),
            )
        }

        fn connect_with_hello(address: SocketAddr, hello: Value) -> Self {
            let writer = TcpStream::connect(address).unwrap();
            writer
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let reader = BufReader::new(writer.try_clone().unwrap());
            let mut client = Self { writer, reader };
            client.send(hello);
            client
        }

        fn send(&mut self, value: Value) {
            serde_json::to_writer(&mut self.writer, &value).unwrap();
            self.writer.write_all(b"\n").unwrap();
            self.writer.flush().unwrap();
        }

        fn read(&mut self) -> Value {
            let mut line = String::new();
            self.reader.read_line(&mut line).unwrap();
            assert!(!line.is_empty());
            serde_json::from_str(&line).unwrap()
        }

        fn read_until(&mut self, kind: &str) -> Value {
            loop {
                let message = self.read();
                if message["type"] == kind {
                    return message;
                }
            }
        }

        fn read_until_event(&mut self, kind: &str) -> Value {
            loop {
                let message = self.read();
                let Some(events) = message.get("events").and_then(Value::as_array) else {
                    continue;
                };
                if let Some(event) = events.iter().find(|event| event["kind"] == kind) {
                    return event.clone();
                }
            }
        }

        fn assert_no_message(&mut self) {
            self.reader
                .get_ref()
                .set_read_timeout(Some(Duration::from_millis(100)))
                .unwrap();
            let mut line = String::new();
            let error = self.reader.read_line(&mut line).unwrap_err();
            assert!(matches!(
                error.kind(),
                std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
            ));
            self.reader
                .get_ref()
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
        }
    }

    fn app() -> ServerApp {
        let mut config = ServerConfig::default();
        config.host = "127.0.0.1".to_owned();
        config.port = 0;
        config.map = "01_karelia".to_owned();
        config.navigation_graph_directory =
            Some(Path::new(env!("CARGO_MANIFEST_DIR")).join("../navgraphs"));
        config.receipt_state_path = Some(std::env::temp_dir().join(format!(
            "offline-rust-server-test-{}-{}.json",
            std::process::id(),
            TEST_STATE_NONCE.fetch_add(1, AtomicOrdering::Relaxed),
        )));
        ServerApp::bind(config).unwrap()
    }

    fn shipped_graph(map: &str) -> NavGraph {
        NavGraph::load_from_directory(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("../navgraphs"),
            map,
        )
        .unwrap()
    }

    fn shipped_capture_bases(map: &str) -> [Vec<MapPoint>; 2] {
        let graph = shipped_graph(map);
        let tactical = tactical_map(map).unwrap();
        let mapping = graph
            .resolve_team_mapping([
                [tactical.bases[0].x, tactical.bases[0].z],
                [tactical.bases[1].x, tactical.bases[1].z],
            ])
            .unwrap();
        mapping.map(|graph_team| {
            let point = graph.objective_base(graph_team).unwrap();
            vec![MapPoint::new(point[0], point[1])]
        })
    }

    fn shipped_capture_bases_wire(map: &str) -> Value {
        let bases = shipped_capture_bases(map);
        json!({
            "1": [[bases[0][0].x, bases[0][0].z]],
            "2": [[bases[1][0].x, bases[1][0].z]],
        })
    }

    fn process_event(app: &mut ServerApp) {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let now = Instant::now();
            assert!(
                now < deadline,
                "transport event did not arrive before timeout"
            );
            if app.poll(deadline.saturating_duration_since(now)).unwrap() == PollOutcome::Event {
                return;
            }
        }
    }

    fn descriptor_projection() -> Value {
        json!({
            "name": DEFAULT_VEHICLE_0922,
            "level": 1,
            "tags": ["lightTank"],
            "type": {
                "name": DEFAULT_VEHICLE_0922,
                "level": 1,
                "tags": ["lightTank"],
                "crewRoles": [
                    ["commander", "gunner", "radioman", "loader"],
                    ["driver"]
                ]
            },
            "maxHealth": 1000,
            "maxAmmo": 51,
            "gun": {
                "reloadTime": 7.3,
                "clip": [1, 0.0],
                "shotDispersionAngle": 0.0046,
                "aimingTime": 2.3,
                "shotDispersionFactors": {
                    "turretRotation": 0.12,
                    "afterShot": 3.5,
                    "afterShotInBurst": 2.0
                },
                "rotationSpeed": 0.35,
                "turretYawLimits": [-1.2, 1.1],
                "pitchLimits": {"absolute": [-0.35, 0.15]},
                "maxHealth": 54,
                "shots": [{
                    "speed": 700.0,
                    "gravity": 9.81,
                    "maxDistance": 720.0,
                    "piercingPower": [80.0, 60.0],
                    "shell": {
                        "kind": "ARMOR_PIERCING",
                        "caliber": 45.0,
                        "damage": [110.0, 42.0]
                    }
                }]
            },
            "turret": {
                "rotationSpeed": 0.7,
                "turretRotatorHealth": {"maxHealth": 140, "maxRegenHealth": 70},
                "surveyingDeviceHealth": {"maxHealth": 90, "maxRegenHealth": 45}
            },
            "physics": {
                "weight": 8000.0,
                "enginePower": 220000.0,
                "speedLimits": [9.4, 4.0],
                "terrainResistance": [1.1, 1.4, 2.6],
                "rollingFrictionFactors": [1.0, 1.1, 0.9],
                "specificFriction": 0.6867,
                "brakeForce": 64000.0,
                "nativePowerRatio": 1.15
            },
            "chassis": {
                "rotationSpeed": 38.0,
                "shotDispersionFactors": [0.12, 0.10],
                "maxHealth": 170,
                "maxRegenHealth": 130,
                "hullPosition": [0.0, 0.6, 0.0],
                "hitTester": {
                    "bbox": [[-1.5, -0.8, -3.2], [1.5, 0.8, 3.2], null]
                }
            },
            "hull": {
                "hitTester": {"bbox": [[-1.7, -0.2, -3.5], [1.7, 1.4, 3.5], null]},
                "heStructuralArmor": [18.0, 16.0],
                "ammoBayHealth": {"maxHealth": 180, "maxRegenHealth": 120}
            },
            "engine": {
                "maxHealth": 105,
                "maxRegenHealth": 52,
                "fireStartingChance": 0.15
            },
            "fuelTank": {"maxHealth": 100, "maxRegenHealth": 50},
            "radio": {"maxHealth": 60, "maxRegenHealth": 30},
            "miscAttrs": {
                "repairSpeedFactor": 1.0,
                "ammoBayHealthFactor": 1.0,
                "engineHealthFactor": 1.0,
                "fuelTankHealthFactor": 1.0,
                "chassisHealthFactor": 1.0
            },
            "repairSettings": {
                "player": {
                    "available": true,
                    "repairFactor": 0.83,
                    "hasBigKit": true
                },
                "botDefault": {
                    "available": true,
                    "repairFactor": 0.57,
                    "hasBigKit": false
                }
            },
            "rammingSettings": {
                "botDefault": {
                    "spall_coefficient": 1.25,
                    "ramming_bonus": 0.0
                }
            },
            "spottingSettings": {
                "player": {
                    "available": true,
                    "observer": {
                        "baseRangeMetres": 400.0,
                        "miscFactor": 1.1,
                        "crewFactor": 1.08,
                        "binocularFactor": 1.25,
                        "hasBinoculars": true,
                        "binocularDelayUs": 3000000
                    },
                    "target": {
                        "moving": 0.12,
                        "stationary": 0.18,
                        "movingAspect": {"additive": 0.0, "multiplier": 1.0},
                        "stationaryAspect": {"additive": 0.1, "multiplier": 1.0},
                        "hasCamouflageNet": true,
                        "camouflageNetDelayUs": 3000000,
                        "invisibilityFactorAtShot": 0.25
                    }
                },
                "botDefault": {
                    "available": true,
                    "observer": {
                        "baseRangeMetres": 400.0,
                        "miscFactor": 1.0,
                        "crewFactor": 0.95,
                        "binocularFactor": 1.0,
                        "hasBinoculars": false,
                        "binocularDelayUs": 0
                    },
                    "target": {
                        "moving": 0.08,
                        "stationary": 0.14,
                        "movingAspect": {"additive": 0.0, "multiplier": 1.0},
                        "stationaryAspect": {"additive": 0.0, "multiplier": 1.0},
                        "hasCamouflageNet": false,
                        "camouflageNetDelayUs": 0,
                        "invisibilityFactorAtShot": 0.25
                    }
                }
            }
        })
    }

    #[test]
    fn vehicle_selection_preserves_only_actor_scoped_effective_params() {
        let current_effective = effective_params_projection(8_000.0, 1.1, 0.05);
        let current = crate::room::PlayerView {
            player_id: 7,
            account_key: "account-7".to_owned(),
            name: "Tester".to_owned(),
            vehicle: DEFAULT_VEHICLE_0922.to_owned(),
            max_health: 90,
            health: 90,
            team: Team::One,
            slot: 0,
            vehicle_configuration: json!({
                "outfits": {},
                "vehicle_compact_descr": "mounted",
                "ammo_remaining": [51],
                "ammo_loaded_shell": 0,
                "player_authority_loadout": {
                    "repair": {"available": false},
                    "spotting": {"available": false}
                },
                "effective_params": current_effective,
            }),
        };

        let same_vehicle = WireObject::try_from(json!({
            "type": "select_vehicle",
            "vehicle": DEFAULT_VEHICLE_0922,
            "max_health": 90,
        }))
        .unwrap();
        let (_, _, inherited) = parse_vehicle_selection(&same_vehicle, &current).unwrap();
        assert_eq!(
            effective_ram_inputs(&inherited["effective_params"]),
            Some(EffectiveRamInputs {
                mass: 8_000.0,
                spall_coefficient: 1.1,
                ramming_bonus: 0.05,
            })
        );

        let changed_without_donation = WireObject::try_from(json!({
            "type": "select_vehicle",
            "vehicle": "germany:G12_Ltraktor",
            "max_health": 120,
        }))
        .unwrap();
        assert_eq!(
            parse_vehicle_selection(&changed_without_donation, &current),
            Err("invalid_vehicle_configuration")
        );

        let changed_effective = effective_params_projection(9_500.0, 1.4, 0.15);
        let changed = WireObject::try_from(json!({
            "type": "select_vehicle",
            "vehicle": "germany:G12_Ltraktor",
            "max_health": 120,
            "effective_params": changed_effective,
            "player_authority_loadout": {
                "repair": {"available": false},
                "spotting": {"available": false}
            },
        }))
        .unwrap();
        let (_, _, selected) = parse_vehicle_selection(&changed, &current).unwrap();
        assert_eq!(
            effective_ram_inputs(&selected["effective_params"]),
            Some(EffectiveRamInputs {
                mass: 9_500.0,
                spall_coefficient: 1.4,
                ramming_bonus: 0.15,
            })
        );

        let mut invalid_effective = effective_params_projection(9_500.0, 1.4, 0.15);
        invalid_effective["ramming"]["spall_coefficient"] = json!(1.500_001);
        let invalid = WireObject::try_from(json!({
            "type": "select_vehicle",
            "vehicle": "germany:G12_Ltraktor",
            "max_health": 120,
            "effective_params": invalid_effective,
            "player_authority_loadout": {
                "repair": {"available": false},
                "spotting": {"available": false}
            },
        }))
        .unwrap();
        assert_eq!(
            parse_vehicle_selection(&invalid, &current),
            Err("invalid_vehicle_configuration")
        );
    }

    fn donate_catalog(app: &mut ServerApp, player: &mut TestClient) {
        player.send(json!({
            "type": "descriptor_catalog",
            "vehicles": [{
                "name": DEFAULT_VEHICLE_0922,
                "level": 1,
                "tags": ["lightTank"],
            }],
        }));
        process_event(app);
    }

    fn assert_navigation_start_denied(app: &mut ServerApp) {
        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(app);
        player.read();
        player.read();
        donate_catalog(app, &mut player);
        player.send(json!({"type": "start_battle", "round_id": 1}));
        process_event(app);
        let denied = player.read();
        assert_eq!(denied["type"], "start_denied");
        assert_eq!(denied["code"], "navigation_unavailable");
        assert_eq!(app.room().phase(), RoomPhase::Waiting);
        assert!(app.prepared_round.is_none());
    }

    #[test]
    fn missing_or_invalid_navigation_graph_denies_start_before_room_mutation() {
        let mut missing = app();
        missing.config.navigation_graph_directory = None;
        assert_navigation_start_denied(&mut missing);

        let directory = std::env::temp_dir().join(format!(
            "offline-rust-server-invalid-navgraph-{}-{}",
            std::process::id(),
            TEST_STATE_NONCE.fetch_add(1, AtomicOrdering::Relaxed),
        ));
        fs::create_dir_all(&directory).unwrap();
        fs::write(
            directory.join("manifest.json"),
            r#"{"format":"offline-lan-0922-navgraph-manifest","version":2,"game_version":"0.9.22.0.1-cn-1513","maps":[{"map":"01_karelia","file":"01_karelia.json"}]}"#,
        )
        .unwrap();
        fs::write(directory.join("01_karelia.json"), r#"{"format":"wrong"}"#).unwrap();
        let mut invalid = app();
        invalid.config.navigation_graph_directory = Some(directory.clone());
        assert_navigation_start_denied(&mut invalid);
        fs::remove_file(directory.join("01_karelia.json")).unwrap();
        fs::remove_file(directory.join("manifest.json")).unwrap();
        fs::remove_dir(directory).unwrap();
    }

    fn request_start_with_descriptor_only(app: &mut ServerApp, player: &mut TestClient) {
        donate_catalog(app, player);
        player.send(json!({"type": "start_battle", "round_id": 1}));
        process_event(app);
        let request = player.read();
        assert_eq!(request["type"], "descriptor_request");
        let requested = request["names"].clone();
        player.send(json!({
            "type": "descriptor_bundle",
            "round_id": 1,
            "requested": requested,
            "failures": [],
            "complete": true,
            "projections": {"ussr:R11_MS-1": descriptor_projection()},
        }));
        process_event(app);
    }

    fn donate_destructible_map(app: &mut ServerApp, player: &mut TestClient) {
        player.send(json!({
            "type": "destructible_map",
            "round_id": 1,
            "map": "01_karelia",
            "part": 0,
            "parts": 1,
            "unit_vehicle_mass": 15_000.0,
            "resources": {
                "objects/test/fragile": {
                    "destr_type": "fragile",
                    "kinetic_correction": 1.0,
                },
            },
            "instances": [[
                [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                17,
                4,
                25.0,
                null,
                "objects/test/fragile",
            ]],
        }));
        process_event(app);
    }

    fn request_start_with_descriptor(app: &mut ServerApp, player: &mut TestClient) {
        request_start_with_descriptor_only(app, player);
        donate_destructible_map(app, player);
    }

    fn bot_consumables_wire() -> Value {
        let contract = |name: &str,
                        kind: &str,
                        id: u64,
                        tags: Vec<&str>,
                        autoactivate: bool,
                        repair_all: bool| {
            json!({
                "name": name,
                "kind": kind,
                "id": id,
                "compactDescr": 11_000 + id,
                "tags": tags,
                "reuseCount": 1,
                "cooldownSeconds": 90.0,
                "autoactivate": autoactivate,
                "fireStartingChanceFactor": if autoactivate { 0.9 } else { 1.0 },
                "repairAll": repair_all,
                "bonusValue": 0.0,
                "crewLevelIncrease": 0.0,
                "enginePowerFactor": 1.0,
                "turretRotationSpeedFactor": 1.0,
                "engineHpLossPerSecond": 0.0,
                "autoReactionSeconds": 0.0,
            })
        };
        json!([
            contract(
                "autoExtinguishers",
                "extinguisher",
                21,
                Vec::new(),
                true,
                false,
            ),
            contract("largeMedkit", "medkit", 23, vec!["medkit"], false, true),
            contract(
                "largeRepairkit",
                "repairkit",
                25,
                vec!["repairkit"],
                false,
                true,
            ),
        ])
    }

    fn donate_native_world(app: &mut ServerApp, worker: &mut TestClient) {
        let prepared = app.prepared_round.as_ref().unwrap();
        let mut next_entity_id = 10_000_i64;
        let mut entities = Vec::new();
        for participant in &prepared.participants {
            entities.push(json!({
                "kind": "human",
                "logical_id": participant.player_id,
                "native": {
                    "entity_id": next_entity_id,
                    "generation": 1,
                },
            }));
            next_entity_id += 1;
        }
        for bot in &prepared.bots {
            entities.push(json!({
                "kind": "bot",
                "logical_id": bot.id,
                "native": {
                    "entity_id": next_entity_id,
                    "generation": 1,
                },
            }));
            next_entity_id += 1;
        }
        worker.send(json!({
            "type": "oracle_world_ready",
            "protocol": LAN_PROTOCOL_VERSION,
            "round_id": prepared.scope.round_id,
            "authority_epoch": prepared.scope.epoch,
            "oracle_generation": 1,
            "entity_revision": 1,
            "complete": true,
            "oracle_space": {"entity_id": 9_999, "generation": 1},
            "entities": entities,
            "destructibles": {
                "native_space_id": 7,
                "expected_instances": 1,
                "installed_instances": 1,
            },
            "bot_consumables": bot_consumables_wire(),
        }));
        process_event(app);
        assert!(app.native_world_ready.is_some());
    }

    fn enter_live_round(app: &mut ServerApp, worker: &mut TestClient, player: &mut TestClient) {
        request_start_with_descriptor(app, player);
        assert_eq!(player.read()["type"], "battle_start");
        assert_eq!(worker.read()["type"], "battle_start");
        donate_native_world(app, worker);
        player.send(json!({
            "type":"battle_ready", "round_id":1, "authority_epoch":1,
            "bases": shipped_capture_bases_wire("01_karelia"),
        }));
        process_event(app);
        worker.send(json!({
            "type":"battle_ready", "round_id":1, "authority_epoch":1,
        }));
        process_event(app);
        assert_eq!(player.read()["type"], "battle_live");
        assert_eq!(worker.read()["type"], "battle_live");
        assert_eq!(app.room().phase(), RoomPhase::Battle);
    }

    #[test]
    fn loopback_join_sends_welcome_before_roster() {
        let mut app = app();
        let mut client = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);

        let welcome = client.read();
        let roster = client.read();
        assert_eq!(welcome["type"], "welcome");
        assert_eq!(welcome["player_id"], 1);
        assert_eq!(roster["type"], "roster");
        assert_eq!(roster["players"].as_array().unwrap().len(), 1);
        assert_eq!(app.room().player_count(), 1);
    }

    #[test]
    fn capability_complete_probe_negotiates_future_labels_without_joining() {
        let mut app = app();
        let mut probe = TestClient::connect_with_hello(
            app.local_addr(),
            json!({
                "type": "hello",
                "protocol": LAN_PROTOCOL_VERSION + 1,
                "role": "probe",
                "client_build": "future-package-label",
                "capabilities": [
                    PROJECTILE_LEDGER_V2,
                    RICOCHET_CONTINUATION_V1,
                    DESTRUCTIBLE_CATALOG_V5,
                    LEAN_SNAPSHOT_MANIFEST_V1,
                    RAM_CONTACT_LEDGER_V3,
                    HUMAN_RAM_TIMELINE_V1,
                    PLAYER_FIRE_INTENT_V4,
                    PLAYER_ENVIRONMENT_V2,
                    EFFECTIVE_PARAMS_V1,
                ],
            }),
        );
        process_event(&mut app);

        let welcome = probe.read();
        assert_eq!(welcome["type"], "welcome");
        assert_eq!(welcome["client_build"], CLIENT_BUILD_0922);
        assert!(welcome["server_capabilities"]
            .as_array()
            .is_some_and(|capabilities| capabilities
                .iter()
                .any(|value| value == RICOCHET_CONTINUATION_V1)));
        assert!(welcome["server_capabilities"]
            .as_array()
            .is_some_and(|capabilities| capabilities
                .iter()
                .any(|value| { value == crate::vehicle_overlay::VEHICLE_OVERLAY_CAPABILITY })));
        assert_eq!(app.room().player_count(), 0);
        assert_eq!(app.room().host_player_id(), None);
    }

    #[test]
    fn launcher_probe_fetches_the_pinned_vehicle_overlay_without_joining() {
        let nonce = TEST_STATE_NONCE.fetch_add(1, AtomicOrdering::Relaxed);
        let root = std::env::temp_dir().join(format!(
            "offline-rust-server-overlay-{}-{nonce}",
            std::process::id()
        ));
        let overlay_root = root.join("res_mods").join("0.9.22.0.1");
        let member = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml";
        let member_path = member
            .split('/')
            .fold(overlay_root.clone(), |path, part| path.join(part));
        fs::create_dir_all(member_path.parent().unwrap()).unwrap();
        // This crosses the ordinary 256 KiB wire limit and therefore proves
        // that only the bounded overlay response uses the dedicated frame.
        let data = vec![b'x'; crate::wire::MAX_LINE_BYTES];
        fs::write(&member_path, &data).unwrap();
        let checksum = format!("{:x}", Sha256::digest(&data));
        fs::write(
            overlay_root.join("vehicle_overlays.json"),
            serde_json::to_vec(&json!({
                "schema": 1,
                "activeProfile": "Fast MS-1",
                "members": [{
                    "sourceMember": member,
                    "overlaySha256": checksum,
                }],
            }))
            .unwrap(),
        )
        .unwrap();

        let mut config = ServerConfig::default();
        config.host = "127.0.0.1".to_owned();
        config.port = 0;
        config.map = "01_karelia".to_owned();
        config.vehicle_overlay_root = Some(root.clone());
        config.receipt_state_path = Some(root.join("receipts.json"));
        let mut app = ServerApp::bind(config).unwrap();
        let mut probe = TestClient::connect_with_hello(
            app.local_addr(),
            json!({
                "type": "hello",
                "protocol": LAN_PROTOCOL_VERSION,
                "role": "probe",
                "client_build": CLIENT_BUILD_0922,
                "capabilities": [
                    PROJECTILE_LEDGER_V2,
                    RICOCHET_CONTINUATION_V1,
                    DESTRUCTIBLE_CATALOG_V5,
                    LEAN_SNAPSHOT_MANIFEST_V1,
                    RAM_CONTACT_LEDGER_V3,
                    HUMAN_RAM_TIMELINE_V1,
                    PLAYER_FIRE_INTENT_V4,
                    PLAYER_ENVIRONMENT_V2,
                    EFFECTIVE_PARAMS_V1,
                ],
            }),
        );
        process_event(&mut app);
        assert_eq!(probe.read()["type"], "welcome");

        probe.send(json!({"type": "vehicle_overlay_query"}));
        process_event(&mut app);
        let manifest = probe.read();
        assert_eq!(manifest["type"], "vehicle_overlay_manifest");
        assert_eq!(manifest["present"], true);
        assert_eq!(manifest["profile"], "Fast MS-1");

        probe.send(json!({
            "type": "vehicle_overlay_member",
            "sourceMember": member,
        }));
        process_event(&mut app);
        let payload = probe.read();
        assert_eq!(payload["type"], "vehicle_overlay_member_data");
        assert_eq!(
            BASE64_STANDARD
                .decode(payload["data_b64"].as_str().unwrap())
                .unwrap(),
            data
        );
        assert_eq!(app.room().player_count(), 0);

        drop(probe);
        drop(app);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn two_players_keep_first_host_then_re_elect_after_disconnect() {
        let mut app = app();
        let mut first = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        first.read();
        first.read();

        let mut second = TestClient::connect(app.local_addr(), "account-2", "Bob");
        process_event(&mut app);
        let second_welcome = second.read();
        first.read();
        second.read();
        assert_eq!(second_welcome["host_player_id"], 1);
        assert_eq!(app.room().host_player_id(), Some(1));

        drop(first);
        process_event(&mut app);
        let repaired = second.read();
        assert_eq!(repaired["type"], "roster");
        assert_eq!(repaired["host_player_id"], 2);
        assert_eq!(app.room().host_player_id(), Some(2));
        assert_eq!(app.room().player_count(), 1);
    }

    #[test]
    fn waiting_host_can_resize_teams_with_strict_wire_validation() {
        let mut app = app();
        let mut host = TestClient::connect(app.local_addr(), "account-1", "Host");
        process_event(&mut app);
        host.read();
        host.read();

        let mut guest = TestClient::connect(app.local_addr(), "account-2", "Guest");
        process_event(&mut app);
        guest.read();
        host.read();
        guest.read();

        guest.send(json!({"type": "set_team_size", "team": 1, "size": 2}));
        process_event(&mut app);
        let denied = guest.read();
        assert_eq!(denied["type"], "team_size_denied");
        assert_eq!(denied["code"], "host_only");
        assert_eq!(denied["team_sizes"], json!({"1": 15, "2": 15}));

        host.send(json!({"type": "set_team_size", "team": true, "size": 2}));
        process_event(&mut app);
        assert_eq!(host.read()["code"], "invalid_team");
        host.send(json!({"type": "set_team_size", "team": 2, "size": "1"}));
        process_event(&mut app);
        assert_eq!(host.read()["code"], "invalid_size");

        host.send(json!({"type": "set_team_size", "team": 2, "size": 1}));
        process_event(&mut app);
        let host_roster = host.read();
        let guest_roster = guest.read();
        assert_eq!(host_roster["type"], "roster");
        assert_eq!(guest_roster["type"], "roster");
        assert_eq!(host_roster["team_sizes"], json!({"1": 15, "2": 1}));
        assert_eq!(app.room().config().team_capacity(Team::Two), 1);
    }

    #[test]
    fn waiting_host_owns_the_bot_tier_preset_with_typed_denials() {
        let mut app = app();
        let mut host = TestClient::connect(app.local_addr(), "account-1", "Host");
        process_event(&mut app);
        host.read();
        host.read();

        let mut guest = TestClient::connect(app.local_addr(), "account-2", "Guest");
        process_event(&mut app);
        guest.read();
        host.read();
        guest.read();

        guest.send(json!({"type": "set_bot_tier_mode", "mode": "same"}));
        process_event(&mut app);
        let denied = guest.read();
        assert_eq!(denied["type"], "bot_tier_mode_denied");
        assert_eq!(denied["code"], "host_only");
        assert_eq!(denied["mode"], "same");
        assert_eq!(denied["bot_tier_mode"], "random");

        host.send(json!({"type": "set_bot_tier_mode", "mode": true}));
        process_event(&mut app);
        let invalid = host.read();
        assert_eq!(invalid["type"], "bot_tier_mode_denied");
        assert_eq!(invalid["code"], "invalid_mode");
        assert_eq!(invalid["mode"], true);

        host.send(json!({
            "type": "set_bot_tier_mode",
            "mode": "minus1_plus2",
        }));
        process_event(&mut app);
        let host_roster = host.read();
        let guest_roster = guest.read();
        assert_eq!(host_roster["type"], "roster");
        assert_eq!(guest_roster["type"], "roster");
        assert_eq!(host_roster["bot_tier_mode"], "minus1_plus2");
        assert_eq!(app.room().bot_tier_mode(), BotTierMode::Minus1Plus2);
    }

    #[test]
    fn battle_start_uses_the_rust_lineup_and_echoes_exact_launcher_pins() {
        let mut app = app();
        app.config.bot_lineup = vec![crate::config::BotLineupEntry {
            team: 1,
            slot: 1,
            vehicle: DEFAULT_VEHICLE_0922.to_owned(),
        }];
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Host");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();

        player.send(json!({"type": "set_bot_tier_mode", "mode": "same"}));
        process_event(&mut app);
        player.read();
        worker.read();

        request_start_with_descriptor(&mut app, &mut player);
        let player_start = player.read();
        let worker_start = worker.read();
        for start in [&player_start, &worker_start] {
            assert_eq!(start["type"], "battle_start");
            assert_eq!(start["bot_tier_mode"], "same");
            assert_eq!(
                start["bot_lineup"],
                json!([{
                    "team": 1,
                    "slot": 1,
                    "vehicle": DEFAULT_VEHICLE_0922,
                }])
            );
            let pinned = start["bot_manifest"]
                .as_array()
                .unwrap()
                .iter()
                .find(|bot| bot["team"] == 1 && bot["slot"] == 1)
                .unwrap();
            assert_eq!(pinned["vehicle"], DEFAULT_VEHICLE_0922);
        }
    }

    #[test]
    fn ping_round_trips_sequence_and_client_time() {
        let mut app = app();
        let mut client = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        client.read();
        client.read();

        client.send(json!({"type": "ping", "seq": 7, "client_time": 12.5}));
        process_event(&mut app);
        let pong = client.read();
        assert_eq!(pong["type"], "pong");
        assert_eq!(pong["seq"], 7);
        assert_eq!(pong["client_time"], 12.5);
        assert!(pong["server_time"].as_f64().unwrap() > 0.0);
    }

    #[test]
    fn invalid_oracle_replies_do_not_disconnect_the_live_native_oracle() {
        let mut app = app();
        let state_path = app.receipt_store.path().to_path_buf();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        enter_live_round(&mut app, &mut worker, &mut player);

        let oracle = app.native_oracle.expect("native oracle remains attached");
        let malformed = wire(json!({
            "type": "query_reply",
            "payload": {},
            "unexpected": true,
        }))
        .unwrap();
        app.handle_oracle_reply(oracle.connection_id, &malformed)
            .unwrap();
        let malformed_error = worker.read_until("error");
        assert_eq!(malformed_error["code"], "invalid_oracle_reply");

        let unknown_batch = wire(json!({
            "type": "query_reply",
            "payload": {
                "protocol_version": crate::protocol::ORACLE_PROTOCOL_VERSION,
                "round_id": 1,
                "authority_epoch": 1,
                "oracle_generation": oracle.generation,
                "batch_seq": 2_000_000,
                "issued_tick": 0,
                "apply_tick": crate::protocol::ORACLE_PIPELINE_TICKS,
                "world_revision": 1,
                "oracle_frame_seq": 1,
                "results": [],
            },
        }))
        .unwrap();
        app.handle_oracle_reply(oracle.connection_id, &unknown_batch)
            .unwrap();
        let semantic_error = worker.read_until("error");
        assert_eq!(semantic_error["code"], "invalid_oracle_reply");

        assert!(app.connections.contains_key(&oracle.connection_id));
        assert_eq!(
            app.native_oracle.map(|active| active.connection_id),
            Some(oracle.connection_id)
        );
        assert_eq!(app.room().phase(), RoomPhase::Battle);
        assert!(app
            .battle
            .as_ref()
            .is_some_and(|battle| battle.engine().result().is_none()));

        worker.send(json!({"type": "ping", "seq": 9, "client_time": 1.0}));
        process_event(&mut app);
        assert_eq!(worker.read_until("pong")["seq"], 9);

        drop(app);
        let _ = std::fs::remove_file(state_path);
    }

    #[test]
    fn stale_landing_scope_returns_the_typed_result_instead_of_a_generic_error() {
        let mut app = app();
        let mut client = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        client.read();
        client.read();

        client.send(json!({
            "type": "landing_observation",
            "round_id": 1,
            "authority_epoch": 1,
            "observation_seq": 1,
            "input_seq": 1,
            "impact_speed": 20.0,
        }));
        process_event(&mut app);
        let result = client.read();
        assert_eq!(result["type"], "landing_observation_result");
        assert_eq!(result["round_id"], 1);
        assert_eq!(result["authority_epoch"], 0);
        assert_eq!(result["observation_seq"], 1);
        assert_eq!(result["input_seq"], 1);
        assert_eq!(result["committed_seq"], 0);
        assert_eq!(result["accepted"], false);
        assert_eq!(result["reason"], "stale_authority");

        client.send(json!({
            "type": "landing_observation",
            "round_id": 1,
            "authority_epoch": 0,
            "observation_seq": 1,
            "input_seq": 1,
            "impact_speed": 20.0,
        }));
        process_event(&mut app);
        let inactive = client.read();
        assert_eq!(inactive["type"], "landing_observation_result");
        assert_eq!(inactive["accepted"], false);
        assert_eq!(inactive["reason"], "not_active");
    }

    #[test]
    fn landing_command_effect_is_sent_reliably_with_the_exact_result_shape() {
        let mut app = app();
        let mut client = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        client.read();
        client.read();
        let connection_id = *app.connections.keys().next().unwrap();

        app.publish_battle_output(BattleLoopOutput {
            effects: vec![CommandEffect::LandingObservation {
                connection_id,
                player_id: 1,
                result: LandingObservationResult {
                    message_type: "landing_observation_result",
                    round_id: 1,
                    authority_epoch: 0,
                    observation_seq: 2,
                    input_seq: 7,
                    committed_seq: 2,
                    accepted: true,
                    reason: "",
                },
            }],
            ..BattleLoopOutput::default()
        })
        .unwrap();
        let result = client.read();
        assert_eq!(
            result
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect::<BTreeSet<_>>(),
            BTreeSet::from([
                "accepted".to_owned(),
                "authority_epoch".to_owned(),
                "committed_seq".to_owned(),
                "input_seq".to_owned(),
                "observation_seq".to_owned(),
                "reason".to_owned(),
                "round_id".to_owned(),
                "type".to_owned(),
            ]),
        );
        assert_eq!(result["accepted"], true);
        assert_eq!(result["committed_seq"], 2);
    }

    #[test]
    fn ammo_command_effect_is_sent_reliably_with_the_exact_result_shape() {
        let mut app = app();
        let mut client = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        client.read();
        client.read();
        let connection_id = *app.connections.keys().next().unwrap();

        app.publish_battle_output(BattleLoopOutput {
            effects: vec![CommandEffect::AmmoIntent {
                connection_id,
                scope: SimulationScope {
                    round_id: 4,
                    epoch: 2,
                },
                player_id: 1,
                intent: crate::player_ammo::PlayerAmmoIntent {
                    intent_seq: 3,
                    input_seq: 9,
                    action: crate::player_ammo::PlayerAmmoIntentAction::SelectCurrent {
                        shell_index: 1,
                    },
                },
                outcome: crate::player_ammo::PlayerAmmoIntentOutcome::IntuitionLoaded {
                    shell_index: 1,
                },
            }],
            ..BattleLoopOutput::default()
        })
        .unwrap();
        let result = client.read();
        assert_eq!(
            result
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect::<BTreeSet<_>>(),
            BTreeSet::from([
                "authority_epoch".to_owned(),
                "input_seq".to_owned(),
                "intent_seq".to_owned(),
                "outcome".to_owned(),
                "player_id".to_owned(),
                "round_id".to_owned(),
                "shell_index".to_owned(),
                "type".to_owned(),
            ]),
        );
        assert_eq!(result["type"], "ammo_intent_result");
        assert_eq!(result["round_id"], 4);
        assert_eq!(result["authority_epoch"], 2);
        assert_eq!(result["intent_seq"], 3);
        assert_eq!(result["input_seq"], 9);
        assert_eq!(result["outcome"], "intuition_loaded");
        assert_eq!(result["shell_index"], 1);
    }

    #[test]
    fn hidden_worker_is_an_oracle_not_a_player_or_host() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);

        let welcome = worker.read();
        assert_eq!(welcome["role"], SIMULATION_WORKER_ROLE);
        assert_eq!(welcome["worker_id"], SIMULATION_WORKER_ID);
        assert!(welcome["capabilities"]
            .as_array()
            .is_some_and(|capabilities| capabilities
                .iter()
                .any(|value| value == HE_EXPLOSION_EVIDENCE_V1)));
        assert!(welcome["server_capabilities"]
            .as_array()
            .is_some_and(|capabilities| capabilities
                .iter()
                .any(|value| value == HE_EXPLOSION_EVIDENCE_V1)));
        assert!(app.room().oracle_connected());
        assert_eq!(app.room().player_count(), 0);
        assert_eq!(app.room().host_player_id(), None);
    }

    #[test]
    fn hidden_worker_without_he_explosion_evidence_is_rejected() {
        let hello = Hello::try_from(
            wire(json!({
                "type": "hello",
                "protocol": LAN_PROTOCOL_VERSION,
                "role": SIMULATION_WORKER_ROLE,
                "client_build": CLIENT_BUILD_0922,
                "capabilities": [
                    PROJECTILE_LEDGER_V2,
                    DESTRUCTIBLE_CATALOG_V5,
                    LEAN_SNAPSHOT_MANIFEST_V1,
                    RAM_CONTACT_LEDGER_V3,
                    HUMAN_RAM_TIMELINE_V1,
                    PLAYER_FIRE_INTENT_V4,
                    PLAYER_ENVIRONMENT_V2,
                    EFFECTIVE_PARAMS_V1,
                    RICOCHET_CONTINUATION_V1,
                    PLAYER_AMMO_AUTHORITY_V1,
                    PLAYER_AUTHORITY_LOADOUT_V1,
                    SIMULATION_WORKER_CAPABILITY,
                    NATIVE_ORACLE_V1,
                ],
                "oracle_generation": 1,
            }))
            .unwrap(),
        )
        .unwrap();

        assert_eq!(
            validate_native_oracle_hello(&hello).unwrap_err(),
            (
                "unsupported_capabilities",
                "native oracle is missing required capabilities",
            ),
        );
    }

    #[test]
    fn capability_complete_native_oracle_negotiates_future_labels() {
        let hello = Hello::try_from(
            wire(json!({
                "type": "hello",
                "protocol": LAN_PROTOCOL_VERSION + 1,
                "role": SIMULATION_WORKER_ROLE,
                "client_build": "future-worker-label",
                "capabilities": [
                    PROJECTILE_LEDGER_V2,
                    DESTRUCTIBLE_CATALOG_V5,
                    LEAN_SNAPSHOT_MANIFEST_V1,
                    RAM_CONTACT_LEDGER_V3,
                    HUMAN_RAM_TIMELINE_V1,
                    HE_EXPLOSION_EVIDENCE_V1,
                    PLAYER_FIRE_INTENT_V4,
                    PLAYER_ENVIRONMENT_V2,
                    EFFECTIVE_PARAMS_V1,
                    RICOCHET_CONTINUATION_V1,
                    PLAYER_AMMO_AUTHORITY_V1,
                    PLAYER_AUTHORITY_LOADOUT_V1,
                    SIMULATION_WORKER_CAPABILITY,
                    NATIVE_ORACLE_V1,
                ],
                "oracle_generation": 1,
            }))
            .unwrap(),
        )
        .unwrap();

        assert!(validate_native_oracle_hello(&hello).is_ok());
    }

    #[test]
    fn start_and_both_ready_messages_cross_the_loading_barrier() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();

        request_start_with_descriptor(&mut app, &mut player);
        let player_start = player.read();
        let worker_start = worker.read();
        assert_eq!(player_start["type"], "battle_start");
        assert_eq!(player_start["phase"], "loading");
        assert_eq!(player_start["players"][0]["equipment_states"], json!([]));
        assert_eq!(player_start["players"][0]["equipment_revision"], 1);
        assert_eq!(player_start["players"][0]["equipment_intent_seq"], 0);
        assert_eq!(worker_start["bot_authority_id"], SERVER_AUTHORITY_ID);
        assert_eq!(app.room().phase(), RoomPhase::Loading);
        donate_native_world(&mut app, &mut worker);

        player.send(json!({
            "type": "battle_ready",
            "round_id": 1,
            "authority_epoch": 1,
            "bases": shipped_capture_bases_wire("01_karelia"),
        }));
        process_event(&mut app);
        assert_eq!(app.room().phase(), RoomPhase::Loading);

        worker.send(json!({
            "type": "battle_ready",
            "round_id": 1,
            "authority_epoch": 1,
        }));
        process_event(&mut app);
        let player_live = player.read();
        let worker_live = worker.read();
        assert_eq!(player_live["type"], "battle_live");
        assert_eq!(worker_live["type"], "battle_live");
        assert_eq!(app.room().phase(), RoomPhase::Battle);
        assert_eq!(app.native_prerequisite_deadline, None);

        std::thread::sleep(Duration::from_millis(75));
        app.poll(Duration::ZERO).unwrap();
        let full = player.read();
        let worker_full = worker.read_until("snapshot");
        assert_eq!(full["type"], "snapshot");
        assert_eq!(full["bot_authority_id"], SERVER_AUTHORITY_ID);
        assert!(full["server_tick"].as_u64().unwrap() >= 2);
        assert!(full["bot_manifest"].is_array());
        let bot_states = full["bots"].as_array().unwrap();
        assert!(!bot_states.is_empty());
        assert!(bot_states.iter().all(|bot| {
            bot["shell_index"] == 0
                && bot["next_shell_index"] == 0
                && bot["ammo_remaining"].as_array().is_some_and(|remaining| {
                    !remaining.is_empty()
                        && remaining.iter().filter_map(Value::as_u64).sum::<u64>() == 51
                })
                && bot["ammo_reload_pending"] == false
                && bot["equipment_states"].as_array().is_some_and(|states| {
                    states
                        .iter()
                        .map(|state| (&state["equipment"]["name"], &state["equipment"]["id"]))
                        .collect::<Vec<_>>()
                        == vec![
                            (&json!("autoExtinguishers"), &json!(21)),
                            (&json!("largeMedkit"), &json!(23)),
                            (&json!("largeRepairkit"), &json!(25)),
                        ]
                })
        }));
        assert!(full.get("entities").is_none());
        assert!(full.get("tick").is_none());
        assert_eq!(worker_full["server_tick"], full["server_tick"]);

        let full_tick = full["server_tick"].as_u64().unwrap();
        loop {
            app.poll(Duration::from_millis(40)).unwrap();
            let tick = app.battle.as_ref().unwrap().engine().tick();
            if tick > full_tick && tick % 2 == 0 {
                break;
            }
        }
        let lean = player.read();
        let worker_lean = worker.read_until("snapshot");
        assert_eq!(lean["type"], "snapshot");
        assert!(lean.get("bot_manifest").is_none());
        assert!(lean["server_tick"].as_u64().unwrap() > full_tick);
        assert_eq!(worker_lean["server_tick"], lean["server_tick"]);
    }

    #[test]
    fn ready_messages_are_deferred_until_destructible_install_completes() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();

        request_start_with_descriptor_only(&mut app, &mut player);
        assert_eq!(player.read()["need_destructible_map"], true);
        assert_eq!(worker.read()["need_destructible_map"], true);
        donate_native_world(&mut app, &mut worker);

        let player_ready = json!({
            "type": "battle_ready",
            "round_id": 1,
            "authority_epoch": 1,
            "bases": shipped_capture_bases_wire("01_karelia"),
        });
        player.send(player_ready.clone());
        process_event(&mut app);
        player.send(player_ready);
        process_event(&mut app);
        worker.send(json!({
            "type": "battle_ready",
            "round_id": 1,
            "authority_epoch": 1,
        }));
        process_event(&mut app);

        assert_eq!(app.room().phase(), RoomPhase::Loading);
        assert_eq!(app.deferred_player_ready.len(), 1);
        assert!(app.deferred_oracle_ready.is_some());

        donate_destructible_map(&mut app, &mut player);

        assert_eq!(player.read()["type"], "battle_live");
        assert_eq!(worker.read()["type"], "battle_live");
        assert_eq!(app.room().phase(), RoomPhase::Battle);
        assert!(app.deferred_player_ready.is_empty());
        assert!(app.deferred_oracle_ready.is_none());
        assert_eq!(app.native_prerequisite_deadline, None);
    }

    #[test]
    fn equipment_intent_strict_ingress_retry_and_snapshot_cross_the_server_boundary() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        enter_live_round(&mut app, &mut worker, &mut player);

        let valid = json!({
            "type": "equipment_intent",
            "round_id": 1,
            "intent_seq": 1,
            "equipment_id": 41,
            "activation_code": 41,
            "selected": null,
            "requested_active": null,
        });
        let mut malformed = valid.clone();
        malformed["client_verdict"] = json!(true);
        player.send(malformed);
        process_event(&mut app);
        player.send(valid.clone());
        process_event(&mut app);
        player.send(valid);
        process_event(&mut app);

        let mut intent_applied = false;
        for _ in 0..20 {
            app.poll(Duration::from_millis(20)).unwrap();
            intent_applied = app
                .battle
                .as_ref()
                .and_then(|battle| battle.player_equipment_snapshot(1))
                .is_some_and(|state| state.equipment_intent_seq == 1);
            if intent_applied {
                break;
            }
        }
        assert!(
            intent_applied,
            "equipment intent did not reach a terminal result"
        );
        app.publish_client_snapshots().unwrap();

        let rejection = player.read_until("error");
        assert_eq!(rejection["code"], "invalid_battle_command");
        let snapshot = loop {
            let snapshot = player.read_until("snapshot");
            let intent_sequence = snapshot["players"]
                .as_array()
                .and_then(|players| players.iter().find(|state| state["id"] == 1))
                .and_then(|state| state["equipment_intent_seq"].as_u64());
            if intent_sequence == Some(1) {
                break snapshot;
            }
        };
        let player_state = snapshot["players"]
            .as_array()
            .unwrap()
            .iter()
            .find(|state| state["id"] == 1)
            .unwrap();
        assert_eq!(player_state["equipment_states"], json!([]));
        assert_eq!(player_state["equipment_revision"], 1);
        assert_eq!(player_state["equipment_intent_seq"], 1);
        assert_eq!(
            player_state["equipment_intent_result"],
            json!({
                "intent_seq": 1,
                "accepted": false,
                "reason": "vehicle_not_alive",
            })
        );
    }

    #[test]
    fn descriptor_timeout_finishes_loading_instead_of_waiting_forever() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();

        donate_catalog(&mut app, &mut player);
        player.send(json!({"type": "start_battle", "round_id": 1}));
        process_event(&mut app);
        assert_eq!(player.read()["type"], "descriptor_request");
        assert_eq!(app.room().phase(), RoomPhase::Loading);

        app.native_prerequisite_deadline = Some(Instant::now() - Duration::from_millis(1));
        app.fail_native_prerequisite_if_due().unwrap();

        assert_eq!(app.room().phase(), RoomPhase::Finished);
        assert_eq!(
            app.room()
                .battle_result()
                .map(|result| result.reason.as_str()),
            Some("descriptor_timeout")
        );
        assert_eq!(app.native_prerequisite_deadline, None);
    }

    #[test]
    fn native_world_timeout_is_distinct_from_descriptor_timeout() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();

        request_start_with_descriptor(&mut app, &mut player);
        assert_eq!(app.room().phase(), RoomPhase::Loading);
        assert!(app.native_world_ready.is_none());

        app.native_prerequisite_deadline = Some(Instant::now() - Duration::from_millis(1));
        app.fail_native_prerequisite_if_due().unwrap();

        assert_eq!(app.room().phase(), RoomPhase::Finished);
        assert_eq!(
            app.room()
                .battle_result()
                .map(|result| result.reason.as_str()),
            Some("native_world_timeout")
        );
    }

    #[test]
    fn ready_barrier_timeout_is_bounded_after_native_world_donation() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();

        request_start_with_descriptor(&mut app, &mut player);
        donate_native_world(&mut app, &mut worker);
        assert_eq!(app.room().phase(), RoomPhase::Loading);
        assert!(app.native_world_ready.is_some());

        app.native_prerequisite_deadline = Some(Instant::now() - Duration::from_millis(1));
        app.fail_native_prerequisite_if_due().unwrap();

        assert_eq!(app.room().phase(), RoomPhase::Finished);
        assert_eq!(
            app.room()
                .battle_result()
                .map(|result| result.reason.as_str()),
            Some("battle_ready_timeout")
        );
    }

    #[test]
    fn leave_and_invalid_receipt_ack_use_the_authenticated_player_session() {
        let mut app = app();
        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();

        player.send(json!({
            "type": "battle_receipt_ack",
            "receipt_id": "missing:1:1",
        }));
        process_event(&mut app);
        let error = player.read();
        assert_eq!(error["type"], "error");
        assert_eq!(error["code"], "receipt_not_found");
        assert_eq!(app.room().player_count(), 1);

        player.send(json!({"type": "leave"}));
        process_event(&mut app);
        assert_eq!(app.room().player_count(), 0);
        assert_eq!(app.room().host_player_id(), None);
    }

    #[test]
    fn leave_battle_persists_and_delivers_a_receipt_without_closing_tcp() {
        let mut app = app();
        let state_path = app.receipt_store.path().to_path_buf();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        request_start_with_descriptor(&mut app, &mut player);
        player.read();
        worker.read();
        donate_native_world(&mut app, &mut worker);
        player.send(json!({
            "type":"battle_ready", "round_id":1, "authority_epoch":1,
            "bases": shipped_capture_bases_wire("01_karelia"),
        }));
        process_event(&mut app);
        worker.send(json!({
            "type":"battle_ready", "round_id":1, "authority_epoch":1,
        }));
        process_event(&mut app);
        player.read();
        worker.read();

        app.prepared_round
            .as_mut()
            .unwrap()
            .descriptors
            .get_mut(DEFAULT_VEHICLE_0922)
            .unwrap()
            .level = 4;

        player.send(json!({"type":"leave_battle", "round_id":1}));
        process_event(&mut app);
        let result = player.read();
        let receipt = player.read();
        let roster = player.read();
        worker.read();
        assert_eq!(result["type"], "events");
        assert_eq!(result["events"][0]["kind"], "battle_result");
        assert_eq!(result["events"][0]["winner"], 2);
        assert_eq!(result["events"][0]["reason"], "team_eliminated");
        assert_eq!(receipt["type"], "battle_receipt");
        assert_eq!(receipt["premature_leave"], true);
        assert_eq!(receipt["death_reason"], -1);
        assert_eq!(receipt["stats"]["damage_received"], 0);
        let player_result = receipt["public_results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["actor_kind"] == "player" && row["actor_id"] == 1)
            .unwrap();
        assert_eq!(player_result["health"], 1000);
        assert_eq!(player_result["death_reason"], -1);
        let frozen_player = app
            .prepared_round
            .as_ref()
            .unwrap()
            .final_result_snapshot
            .as_ref()
            .unwrap()
            .actors
            .iter()
            .find(|actor| actor.key.kind == VehicleKind::Player)
            .unwrap();
        assert_eq!(frozen_player.vehicle_tier, 4);
        assert_eq!(roster["type"], "roster");
        assert_eq!(app.room().phase(), RoomPhase::Finished);
        assert_eq!(app.room().player_count(), 1);
        assert_eq!(app.receipt_store.len(), 1);

        player.send(json!({
            "type":"battle_receipt_ack",
            "receipt_id":receipt["receipt_id"],
        }));
        process_event(&mut app);
        assert_eq!(app.receipt_store.len(), 0);
        app.result_reset_deadline = Some(Instant::now() - Duration::from_millis(1));
        app.reset_finished_round_if_due().unwrap();
        assert_eq!(app.room().phase(), RoomPhase::Waiting);
        assert_eq!(app.room().round_id(), 2);
        assert!(app.prepared_round.is_none());
        assert!(app.battle.is_none());
        drop(app);
        let _ = std::fs::remove_file(state_path);
    }

    #[test]
    fn battle_disconnect_freezes_before_retire_and_retries_persistence_transactionally() {
        let mut app = app();
        let state_path = app.receipt_store.path().to_path_buf();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        enter_live_round(&mut app, &mut worker, &mut player);

        let player_connection_id = app
            .connections
            .iter()
            .find_map(|(&connection_id, connection)| match &connection.session {
                EndpointSession::Player(session) if session.player_id() == 1 => Some(connection_id),
                _ => None,
            })
            .unwrap();
        let connection = app.connections.remove(&player_connection_id).unwrap();
        let session = connection.session.clone();
        std::fs::write(&state_path, br#"{"schema":1,"protocol":5,"receipts":[]}"#).unwrap();

        let error = app.detach_session(&session).unwrap_err();
        assert!(matches!(
            error,
            ServerError::ReceiptStore(ReceiptStoreError::ConcurrentModification(ref path))
                if path == &state_path
        ));
        assert_eq!(app.room().phase(), RoomPhase::Battle);
        assert!(app.room().is_round_player_active(1));
        let player_state = app
            .battle
            .as_ref()
            .unwrap()
            .engine()
            .entities()
            .find(|entity| entity.key.kind == VehicleKind::Player)
            .unwrap();
        assert!(player_state.combat.alive);
        assert_eq!(player_state.combat.health, 1000);
        assert!(app
            .prepared_round
            .as_ref()
            .unwrap()
            .final_result_snapshot
            .is_some());

        std::fs::remove_file(&state_path).unwrap();
        app.detach_session(&session).unwrap();
        assert_eq!(app.room().phase(), RoomPhase::Finished);
        assert_eq!(app.room().player_count(), 0);
        assert_eq!(
            app.room().battle_result(),
            Some(
                &BattleResult::new(BattleWinner::Team(Team::Two), "team_eliminated", None,)
                    .unwrap()
            )
        );
        assert_eq!(app.receipt_store.len(), 1);
        let receipt = &app.receipt_store.room_receipts().next().unwrap().payload;
        assert_eq!(receipt["premature_leave"], true);
        assert_eq!(receipt["death_reason"], -1);
        assert_eq!(receipt["stats"]["damage_received"], 0);
        let player_result = receipt["public_results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["actor_kind"] == "player" && row["actor_id"] == 1)
            .unwrap();
        assert_eq!(player_result["health"], 1000);
        assert_eq!(player_result["death_reason"], -1);

        drop(connection);
        drop(app);
        std::fs::remove_file(state_path).unwrap();
    }

    #[test]
    fn abandoned_round_preserves_an_existing_engine_result() {
        let mut app = app();
        let state_path = app.receipt_store.path().to_path_buf();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        enter_live_round(&mut app, &mut worker, &mut player);

        app.battle
            .as_mut()
            .unwrap()
            .engine_mut()
            .oracle_failed("existing_terminal_result")
            .unwrap();
        // A tick snapshot may already be queued after battle_live when the
        // parallel suite delays this connection. Make that legal ordering an
        // explicit part of the regression instead of assuming the next frame
        // is the terminal event.
        app.publish_client_snapshots().unwrap();
        player.send(json!({"type":"leave_battle", "round_id":1}));
        process_event(&mut app);

        assert_eq!(
            app.room().battle_result(),
            Some(&BattleResult::new(BattleWinner::Draw, "existing_terminal_result", None).unwrap())
        );
        let result = player.read_until_event("battle_result");
        assert_eq!(result["winner"], 0);
        assert_eq!(result["reason"], "existing_terminal_result");

        drop(app);
        std::fs::remove_file(state_path).unwrap();
    }

    #[test]
    fn terminal_tick_sends_strict_result_before_receipt() {
        let mut app = app();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        request_start_with_descriptor(&mut app, &mut player);
        player.read();
        worker.read();
        donate_native_world(&mut app, &mut worker);
        player.send(json!({
            "type":"battle_ready", "round_id":1, "authority_epoch":1,
            "bases": shipped_capture_bases_wire("01_karelia"),
        }));
        process_event(&mut app);
        worker.send(json!({
            "type":"battle_ready", "round_id":1, "authority_epoch":1,
        }));
        process_event(&mut app);
        player.read();
        worker.read();

        let (tick, result) = {
            let battle = app.battle.as_mut().unwrap();
            battle
                .engine_mut()
                .oracle_failed("native_oracle_timeout")
                .unwrap();
            (
                battle.engine().tick(),
                battle.engine().result().cloned().unwrap(),
            )
        };
        app.publish_battle_output(BattleLoopOutput {
            ticks: vec![crate::battle::BattleTickOutput {
                tick,
                combat_live: false,
                result: Some(result),
                client_events: vec![crate::client_replication::BattleClientEvent::Authority],
                emissions: Vec::new(),
            }],
            ..BattleLoopOutput::default()
        })
        .unwrap();

        let authority = player.read();
        let snapshot = player.read();
        let battle_result = player.read();
        let receipt = player.read();
        assert_eq!(authority["type"], "events");
        assert_eq!(authority["events"][0]["event_id"], "1:0:0");
        assert_eq!(snapshot["type"], "snapshot");
        assert_eq!(battle_result["type"], "events");
        assert_eq!(battle_result["events"][0]["kind"], "battle_result");
        assert_eq!(battle_result["events"][0]["event_id"], "1:0:1");
        assert_eq!(battle_result["events"][0]["winner"], 0);
        assert_eq!(
            battle_result["events"][0]["reason"],
            "native_oracle_timeout"
        );
        assert!(!battle_result["events"][0]["vehicle_statistics"]
            .as_array()
            .unwrap()
            .is_empty());
        assert_eq!(receipt["type"], "battle_receipt");
        assert_eq!(app.room().phase(), RoomPhase::Finished);
    }

    #[test]
    fn receipt_persistence_failure_leaves_live_round_uncommitted_and_unpublished() {
        let mut app = app();
        let state_path = app.receipt_store.path().to_path_buf();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        enter_live_round(&mut app, &mut worker, &mut player);

        let revision = app.room().state_revision();
        std::fs::write(&state_path, br#"{"schema":1,"protocol":5,"receipts":[]}"#).unwrap();
        let error = app
            .finalize_round(
                BattleResult::new(BattleWinner::Draw, "persistence_failure", None).unwrap(),
                7,
            )
            .unwrap_err();
        assert!(matches!(
            error,
            ServerError::ReceiptStore(ReceiptStoreError::ConcurrentModification(ref path))
                if path == &state_path
        ));
        assert_eq!(app.room().phase(), RoomPhase::Battle);
        assert_eq!(app.room().state_revision(), revision);
        assert!(app.room().battle_result().is_none());
        assert_eq!(app.room().receipt_ledger().count(), 0);
        assert_eq!(app.receipt_store.len(), 0);
        assert!(!app.battle.as_ref().unwrap().is_terminal());
        assert!(app.result_reset_deadline.is_none());
        player.assert_no_message();

        drop(app);
        std::fs::remove_file(state_path).unwrap();
    }

    #[test]
    fn successful_finalization_persists_exact_room_ledger_before_delivery() {
        let mut app = app();
        let state_path = app.receipt_store.path().to_path_buf();
        let mut worker = TestClient::worker(app.local_addr());
        process_event(&mut app);
        worker.read();
        worker.read();

        let mut player = TestClient::connect(app.local_addr(), "account-1", "Alice");
        process_event(&mut app);
        player.read();
        player.read();
        worker.read();
        enter_live_round(&mut app, &mut worker, &mut player);

        app.finalize_round(
            BattleResult::new(BattleWinner::Draw, "exact_recovery", None).unwrap(),
            7,
        )
        .unwrap();
        let result = player.read();
        let delivered = player.read();
        assert_eq!(result["type"], "events");
        assert_eq!(result["events"][0]["kind"], "battle_result");
        assert_eq!(result["events"][0]["event_id"], "1:0:7");
        assert_eq!(delivered["type"], "battle_receipt");
        assert_eq!(app.room().phase(), RoomPhase::Finished);
        assert!(app.battle.as_ref().unwrap().is_terminal());

        let live_receipts: Vec<_> = app.room().receipt_ledger().cloned().collect();
        let recovered = ReceiptStore::open(&state_path).unwrap();
        let recovered_receipts = recovered.into_room_receipts();
        assert_eq!(recovered_receipts.len(), 1);
        assert_eq!(recovered_receipts.len(), live_receipts.len());
        assert_eq!(
            recovered_receipts[0].receipt_id,
            live_receipts[0].receipt_id
        );
        assert_eq!(recovered_receipts[0].round_id, live_receipts[0].round_id);
        assert_eq!(recovered_receipts[0].player_id, live_receipts[0].player_id);
        assert_eq!(
            recovered_receipts[0].account_key,
            live_receipts[0].account_key
        );
        assert_eq!(recovered_receipts[0].payload, live_receipts[0].payload);
        assert_eq!(recovered_receipts[0].receipt_id, delivered["receipt_id"]);
        assert_eq!(recovered_receipts[0].payload, delivered);

        drop(app);
        std::fs::remove_file(state_path).unwrap();
    }

    #[test]
    fn only_whole_runtime_failures_are_terminal_draws() {
        assert_eq!(
            recoverable_battle_failure(&BattleLoopError::Tick(
                crate::sim::TickError::SimulationOverrun { lag_ticks: 9 }
            )),
            Some("simulation_overrun")
        );
        assert_eq!(
            recoverable_battle_failure(&BattleLoopError::Battle(
                crate::battle::BattleError::OracleUnavailable
            )),
            Some("native_oracle_unavailable")
        );
        assert_eq!(
            recoverable_battle_failure(&BattleLoopError::Ram(crate::ram::RamError::InvalidBody)),
            None
        );
        assert_eq!(
            recoverable_battle_failure(&BattleLoopError::InvalidHeExplosionEvidence),
            None
        );
    }

    #[test]
    fn server_random_map_selection_is_seeded_and_round_scoped() {
        let request = json!("server_random");
        let selected = (1..=64)
            .map(|round_id| {
                selected_map(Some(&request), "server_random", 0x1513, round_id).unwrap()
            })
            .collect::<BTreeSet<_>>();
        assert!(selected.len() > 1);
        assert!(selected
            .iter()
            .all(|map| MAP_POOL_0922.contains(&map.as_str())));
        assert_eq!(
            selected_map(Some(&json!("04_himmelsdorf")), "server_random", 1, 1),
            Some("04_himmelsdorf".to_owned())
        );
    }

    fn prepared_round_with_bot_descriptor(descriptor: ParsedDescriptor) -> PreparedRound {
        let projection = descriptor_projection();
        let participant = RoundParticipant {
            player_id: 1,
            account_key: "account-1".to_owned(),
            name: "Player 1".to_owned(),
            vehicle: DEFAULT_VEHICLE_0922.to_owned(),
            max_health: 1_000,
            team: Team::One,
            slot: 0,
            vehicle_configuration: json!({
                "player_authority_loadout": {
                    "repair": projection["repairSettings"]["player"].clone(),
                    "spotting": projection["spottingSettings"]["player"].clone(),
                },
            }),
        };
        let config = RoomConfig::new(2, 1, 1, DEFAULT_VEHICLE_0922, "burst-setup-test").unwrap();
        let outcome = StartOutcome {
            scope: SimulationScope {
                round_id: 1,
                epoch: 1,
            },
            state_revision: 1,
            participants: vec![participant],
        };
        let assignments = BTreeMap::from([((2, 0), DEFAULT_VEHICLE_0922.to_owned())]);
        let mut prepared = PreparedRound::new(
            outcome,
            &config,
            "01_karelia",
            &assignments,
            shipped_graph("01_karelia"),
        )
        .unwrap();
        prepared
            .install_descriptors(&BTreeMap::from([(
                DEFAULT_VEHICLE_0922.to_owned(),
                descriptor,
            )]))
            .unwrap();
        prepared
    }

    #[test]
    fn prepared_0922_bots_use_chinese_callsigns() {
        let descriptor = crate::descriptor::parse_projection(&descriptor_projection()).unwrap();
        let prepared = prepared_round_with_bot_descriptor(descriptor);

        assert!(BOT_CALLSIGNS_0922.len() >= 30);
        assert!(prepared
            .bots
            .iter()
            .all(|bot| { bot.name.chars().any(|character| !character.is_ascii()) }));
    }

    #[test]
    fn mapped_graph_spawn_is_shared_by_humans_bots_and_capture_donation() {
        let descriptor = crate::descriptor::parse_projection(&descriptor_projection()).unwrap();
        let mut human_round = prepared_round_with_bot_descriptor(descriptor.clone());
        assert_eq!(human_round.spawn_team_mapping, [2, 1]);

        let graph_team_two = human_round.navigation_graph.spawn_pose(2, 0).unwrap();
        let human_spawn = human_round.spawn_pose(Team::One, 0).unwrap();
        assert_eq!(
            (human_spawn.x, human_spawn.y, human_spawn.z, human_spawn.yaw),
            (
                graph_team_two.x,
                graph_team_two.y,
                graph_team_two.z,
                graph_team_two.yaw,
            )
        );
        let stock_order = [1_u8, 2].map(|graph_team| {
            let point = human_round
                .navigation_graph
                .objective_base(graph_team)
                .unwrap();
            vec![MapPoint::new(point[0], point[1])]
        });
        assert!(human_round.accept_capture_bases(stock_order).is_err());
        let server_order = shipped_capture_bases("01_karelia");
        human_round
            .accept_capture_bases(server_order.clone())
            .unwrap();
        assert_eq!(human_round.capture_bases, Some(server_order));
        let engine = human_round.build_engine().unwrap();
        let human_entity = engine
            .entities()
            .find(|entity| {
                entity.key
                    == VehicleKey {
                        kind: VehicleKind::Player,
                        id: 1,
                    }
            })
            .unwrap();
        assert_eq!(human_entity.pose, human_spawn);

        let config = RoomConfig::new(1, 1, 1, DEFAULT_VEHICLE_0922, "bot-spawn-test").unwrap();
        let outcome = StartOutcome {
            scope: SimulationScope {
                round_id: 2,
                epoch: 1,
            },
            state_revision: 1,
            participants: Vec::new(),
        };
        let assignments = BTreeMap::from([
            ((1, 0), DEFAULT_VEHICLE_0922.to_owned()),
            ((2, 0), DEFAULT_VEHICLE_0922.to_owned()),
        ]);
        let mut bot_round = PreparedRound::new(
            outcome,
            &config,
            "01_karelia",
            &assignments,
            shipped_graph("01_karelia"),
        )
        .unwrap();
        bot_round
            .install_descriptors(&BTreeMap::from([(
                DEFAULT_VEHICLE_0922.to_owned(),
                descriptor,
            )]))
            .unwrap();
        let team_one_bot = bot_round
            .bots
            .iter()
            .find(|bot| bot.team == Team::One && bot.slot == 0)
            .unwrap();
        assert_eq!(team_one_bot.pose, human_spawn);
        let selected_route = routes_for("01_karelia", 1)
            .unwrap()
            .iter()
            .find(|route| route.id == team_one_bot.route.id)
            .unwrap();
        assert_eq!(
            team_one_bot.route.waypoints.len(),
            selected_route.waypoints.len()
        );
        assert_eq!(
            (
                team_one_bot.route.waypoints[0].x,
                team_one_bot.route.waypoints[0].z
            ),
            (selected_route.waypoints[0].x, selected_route.waypoints[0].z),
        );
        assert_ne!(
            (
                team_one_bot.route.waypoints[0].x,
                team_one_bot.route.waypoints[0].z
            ),
            (team_one_bot.pose.x, team_one_bot.pose.z),
            "the exact tactical route stays separate from the graph formation pose",
        );
        bot_round
            .accept_capture_bases(shipped_capture_bases("01_karelia"))
            .unwrap();
        let bot_engine = bot_round.build_engine().unwrap();
        let bot_entity = bot_engine
            .entities()
            .find(|entity| {
                entity.key
                    == VehicleKey {
                        kind: VehicleKind::Bot,
                        id: 1,
                    }
            })
            .unwrap();
        assert_eq!(bot_entity.pose, human_spawn);
    }

    #[test]
    fn prepared_round_installs_each_bot_physical_burst_descriptor() {
        let mut projection = descriptor_projection();
        projection["gun"]["clip"] = json!([5, 2.0]);
        projection["gun"]["burst"] = json!([3, 0.1]);
        let descriptor = crate::descriptor::parse_projection(&projection).unwrap();
        let simulators = prepared_round_with_bot_descriptor(descriptor)
            .build_bot_simulators()
            .unwrap();

        assert_eq!(simulators.len(), 1);
        assert_eq!(
            simulators[0].physical_burst_descriptor(),
            PhysicalBurstDescriptor::new(3, 0.1).unwrap()
        );
    }

    #[test]
    fn bot_simulator_initialization_and_burst_installation_fail_closed() {
        let mut invalid_simulation =
            crate::descriptor::parse_projection(&descriptor_projection()).unwrap();
        invalid_simulation.descriptor.gun.shot_dispersion_angle = 0.0;
        let error = prepared_round_with_bot_descriptor(invalid_simulation)
            .build_bot_simulators()
            .unwrap_err();
        assert!(matches!(
            error,
            ServerError::AuthoritySetup(ref message)
                if message.contains("simulation initialization failed")
        ));

        let mut invalid_burst =
            crate::descriptor::parse_projection(&descriptor_projection()).unwrap();
        invalid_burst.physical_burst = PhysicalBurstDescriptor {
            count: 0,
            interval_seconds: 0.0,
        };
        let error = prepared_round_with_bot_descriptor(invalid_burst)
            .build_bot_simulators()
            .unwrap_err();
        assert!(matches!(
            error,
            ServerError::AuthoritySetup(ref message)
                if message.contains("physical-burst descriptor installation failed")
        ));
    }

    #[test]
    fn prepared_round_uses_actor_ram_inputs_and_descriptor_bot_defaults() {
        let participant = RoundParticipant {
            player_id: 1,
            account_key: "account-1".to_owned(),
            name: "Player 1".to_owned(),
            vehicle: DEFAULT_VEHICLE_0922.to_owned(),
            max_health: 1_000,
            team: Team::One,
            slot: 0,
            vehicle_configuration: json!({
                "effective_params": effective_params_projection(
                    12_345.0, 1.4, 0.12,
                ),
            }),
        };
        let config = RoomConfig::new(2, 1, 1, DEFAULT_VEHICLE_0922, "ram-input-test").unwrap();
        let outcome = StartOutcome {
            scope: SimulationScope {
                round_id: 1,
                epoch: 1,
            },
            state_revision: 1,
            participants: vec![participant],
        };
        let assignments = BTreeMap::from([((2, 0), DEFAULT_VEHICLE_0922.to_owned())]);
        let mut prepared = PreparedRound::new(
            outcome,
            &config,
            "01_karelia",
            &assignments,
            shipped_graph("01_karelia"),
        )
        .unwrap();
        let descriptor = crate::descriptor::parse_projection(&descriptor_projection()).unwrap();
        prepared
            .install_descriptors(&BTreeMap::from([(
                DEFAULT_VEHICLE_0922.to_owned(),
                descriptor,
            )]))
            .unwrap();

        let player = VehicleKey {
            kind: VehicleKind::Player,
            id: 1,
        };
        let bot = VehicleKey {
            kind: VehicleKind::Bot,
            id: 16,
        };
        let masses = prepared.vehicle_masses().unwrap();
        assert_eq!(masses[&player], 12_345.0);
        assert_eq!(masses[&bot], 8_000.0);

        let profiles = prepared.vehicle_ram_profiles().unwrap();
        assert_eq!(profiles[&player].spall_coefficient(), 1.4);
        assert_eq!(profiles[&player].controlled_impact_bonus(), 0.12);
        assert_eq!(profiles[&bot].spall_coefficient(), 1.25);
        assert_eq!(profiles[&bot].controlled_impact_bonus(), 0.0);

        let shapes = prepared.vehicle_ram_shapes().unwrap();
        let expected_shape = RamShape::new(1.5, 3.2, -0.8, 2.0).unwrap();
        assert_eq!(shapes[&player], expected_shape);
        assert_eq!(shapes[&bot], expected_shape);
    }

    #[test]
    fn prepared_round_keeps_each_players_spotting_and_repair_loadout_scoped() {
        let descriptor_value = descriptor_projection();
        let player_one_loadout = json!({
            "repair": descriptor_value["repairSettings"]["player"].clone(),
            "spotting": descriptor_value["spottingSettings"]["player"].clone(),
        });
        let mut player_two_loadout = player_one_loadout.clone();
        player_two_loadout["repair"] = json!({"available": false});
        player_two_loadout["spotting"]["observer"]["baseRangeMetres"] = json!(510.0);
        let participant =
            |player_id: u32, team: Team, slot: usize, loadout: Value| RoundParticipant {
                player_id,
                account_key: format!("account-{player_id}"),
                name: format!("Player {player_id}"),
                vehicle: DEFAULT_VEHICLE_0922.to_owned(),
                max_health: 1_000,
                team,
                slot,
                vehicle_configuration: json!({
                    "player_authority_loadout": loadout,
                }),
            };
        let config = RoomConfig::new(2, 1, 1, DEFAULT_VEHICLE_0922, "loadout-test").unwrap();
        let outcome = StartOutcome {
            scope: SimulationScope {
                round_id: 1,
                epoch: 1,
            },
            state_revision: 1,
            participants: vec![
                participant(1, Team::One, 0, player_one_loadout),
                participant(2, Team::Two, 0, player_two_loadout),
            ],
        };
        let mut prepared = PreparedRound::new(
            outcome,
            &config,
            "01_karelia",
            &BTreeMap::new(),
            shipped_graph("01_karelia"),
        )
        .unwrap();
        let descriptors = [(
            DEFAULT_VEHICLE_0922.to_owned(),
            crate::descriptor::parse_projection(&descriptor_value).unwrap(),
        )]
        .into_iter()
        .collect();
        prepared.install_descriptors(&descriptors).unwrap();
        prepared
            .accept_capture_bases(shipped_capture_bases("01_karelia"))
            .unwrap();

        let spotting = prepared.spotting_inputs().unwrap();
        assert_eq!(spotting.len(), 2);
        assert_eq!(
            spotting[&VehicleKey {
                kind: VehicleKind::Player,
                id: 1,
            }]
                .observer
                .base_range_metres,
            400.0
        );
        assert_eq!(
            spotting[&VehicleKey {
                kind: VehicleKind::Player,
                id: 2,
            }]
                .observer
                .base_range_metres,
            510.0
        );

        let engine = prepared.build_engine().unwrap();
        assert_eq!(
            engine
                .repair_input(VehicleKey {
                    kind: VehicleKind::Player,
                    id: 1,
                })
                .unwrap()
                .repair_factor,
            0.83
        );
        assert_eq!(
            engine.repair_input(VehicleKey {
                kind: VehicleKind::Player,
                id: 2,
            }),
            None
        );
    }

    #[test]
    fn client_player_projection_preserves_ram_ledger_contract() {
        let participant = RoundParticipant {
            player_id: 7,
            account_key: "ram-account".to_owned(),
            name: "Rammer".to_owned(),
            vehicle: DEFAULT_VEHICLE_0922.to_owned(),
            max_health: 100,
            team: Team::One,
            slot: 0,
            vehicle_configuration: json!({}),
        };
        let contact = json!({
            "seq": 2,
            "bot_id": 1,
            "bot_state_revision": 450,
            "presentation_time_us": 15_000_000,
            "x": 1.0,
            "y": 0.0,
            "z": 2.0,
            "yaw": 0.0,
            "vx": 0.0,
            "vz": 10.0,
        });
        let state = client_player_state(
            &participant,
            BodyPose {
                x: 0.0,
                y: 0.0,
                z: -35.0,
                yaw: 0.0,
                pitch: 0.0,
                roll: 0.0,
                speed: 0.0,
                aim_yaw: 0.0,
                gun_pitch: 0.0,
            },
            None,
            4,
            0.125,
            3,
            false,
            None,
            None,
            (0, 0),
            PlayerRamProjection {
                admitted_sequence: 2,
                resolved_sequence: 1,
                contacts: vec![contact.clone()],
                results: vec![json!({"seq": 1, "outcome": "damage"})],
            },
            PlayerRamLedgerState::default(),
            None,
            None,
            None,
        );
        assert_eq!(state.ram_contact_admitted_seq, 2);
        assert_eq!(state.ram_contact_resolved_seq, 1);
        assert_eq!(state.landing_observation_seq, 3);
        assert_eq!(state.up_cosine, 0.125);
        assert_eq!(state.ram_contact, Some(contact.clone()));
        assert_eq!(state.ram_contacts, vec![contact]);
    }
}
