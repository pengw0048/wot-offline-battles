//! Pure lobby, room, and round lifecycle state.
//!
//! Transport code owns sockets and supplies unpredictable per-connection
//! tokens. This module only validates and fences those identities; it never
//! performs I/O. The native oracle is deliberately represented by a separate
//! endpoint type, so it cannot consume a player slot or become the room host.

use crate::protocol::{Epoch, RoundId, SimulationScope};
use serde::{de, Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fmt;
use thiserror::Error;

pub type PlayerId = u32;
pub type SessionId = u64;

pub const MAX_PLAYERS: usize = 30;
pub const MAX_TEAM_CAPACITY: usize = 15;
pub const MAX_RESULT_RECEIPTS: usize = 256;
pub const MAX_PLAYER_NAME_CHARS: usize = 24;
pub const MAX_VEHICLE_NAME_CHARS: usize = 64;
pub const MAX_ACCOUNT_KEY_BYTES: usize = 64;
pub const MAX_SESSION_TOKEN_BYTES: usize = 256;
pub const MAX_RESULT_REASON_CHARS: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Team {
    One,
    Two,
}

impl Team {
    pub const ALL: [Self; 2] = [Self::One, Self::Two];

    pub const fn number(self) -> u8 {
        match self {
            Self::One => 1,
            Self::Two => 2,
        }
    }

    const fn index(self) -> usize {
        (self.number() - 1) as usize
    }
}

impl TryFrom<u8> for Team {
    type Error = RoomError;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value {
            1 => Ok(Self::One),
            2 => Ok(Self::Two),
            _ => Err(RoomError::InvalidTeam),
        }
    }
}

impl Serialize for Team {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_u8(self.number())
    }
}

impl<'de> Deserialize<'de> for Team {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Team::try_from(u8::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RoomPhase {
    Waiting,
    Loading,
    Battle,
    Finished,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BotTierMode {
    Random,
    Same,
    #[serde(rename = "minus1_0")]
    Minus1Zero,
    #[serde(rename = "0_plus1")]
    ZeroPlus1,
    Minus1Plus2,
}

impl BotTierMode {
    pub const ALL: [Self; 5] = [
        Self::Random,
        Self::Same,
        Self::Minus1Zero,
        Self::ZeroPlus1,
        Self::Minus1Plus2,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Random => "random",
            Self::Same => "same",
            Self::Minus1Zero => "minus1_0",
            Self::ZeroPlus1 => "0_plus1",
            Self::Minus1Plus2 => "minus1_plus2",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        Self::ALL
            .into_iter()
            .find(|candidate| candidate.as_str() == value)
    }

    pub fn admits_tier(self, player_tier: u8, candidate_tier: u8) -> bool {
        match self {
            Self::Random => candidate_tier.abs_diff(player_tier) <= 1,
            Self::Same => candidate_tier == player_tier,
            Self::Minus1Zero => candidate_tier == player_tier || candidate_tier + 1 == player_tier,
            Self::ZeroPlus1 => {
                candidate_tier == player_tier || candidate_tier == player_tier.saturating_add(1)
            }
            Self::Minus1Plus2 => (player_tier.saturating_sub(1)..=player_tier.saturating_add(2))
                .contains(&candidate_tier),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RoomConfig {
    pub max_players: usize,
    pub team_capacities: [usize; 2],
    pub default_vehicle: String,
    /// Unique to one server process and supplied by the process that persists
    /// the ledger. Recovered receipts retain their old namespace; a restarted
    /// process must choose a new one so round counters cannot collide.
    pub receipt_namespace: String,
}

impl RoomConfig {
    pub fn new(
        max_players: usize,
        team_one_capacity: usize,
        team_two_capacity: usize,
        default_vehicle: impl Into<String>,
        receipt_namespace: impl Into<String>,
    ) -> Result<Self, RoomError> {
        if !(1..=MAX_PLAYERS).contains(&max_players) {
            return Err(RoomError::InvalidConfig("max_players"));
        }
        if !(1..=MAX_TEAM_CAPACITY).contains(&team_one_capacity) {
            return Err(RoomError::InvalidConfig("team_one_capacity"));
        }
        if !(1..=MAX_TEAM_CAPACITY).contains(&team_two_capacity) {
            return Err(RoomError::InvalidConfig("team_two_capacity"));
        }
        let default_vehicle = sanitize_vehicle(&default_vehicle.into(), "");
        if default_vehicle.is_empty() {
            return Err(RoomError::InvalidConfig("default_vehicle"));
        }
        let receipt_namespace = receipt_namespace.into();
        if !valid_receipt_component(&receipt_namespace, 64) {
            return Err(RoomError::InvalidConfig("receipt_namespace"));
        }
        Ok(Self {
            max_players,
            team_capacities: [team_one_capacity, team_two_capacity],
            default_vehicle,
            receipt_namespace,
        })
    }

    pub fn team_capacity(&self, team: Team) -> usize {
        self.team_capacities[team.index()]
    }
}

impl Default for RoomConfig {
    fn default() -> Self {
        Self::new(
            MAX_PLAYERS,
            MAX_TEAM_CAPACITY,
            MAX_TEAM_CAPACITY,
            "ussr:R11_MS-1",
            "ephemeral",
        )
        .expect("the built-in room configuration is valid")
    }
}

/// Internal identity assigned by the Rust transport when it accepts a socket.
///
/// This is deliberately separate from [`JoinRequest`]: protocol-v5 clients do
/// not send either field, and the Rust migration must not add a new wire
/// requirement. The transport supplies an unpredictable token and a local
/// monotonically unique session ID before calling the room state machine.
#[derive(Clone, PartialEq, Eq)]
pub struct EndpointIdentity {
    session_id: SessionId,
    token: String,
}

impl EndpointIdentity {
    pub fn from_transport(session_id: SessionId, token: impl Into<String>) -> Self {
        Self {
            session_id,
            token: token.into(),
        }
    }

    pub fn session_id(&self) -> SessionId {
        self.session_id
    }
}

impl fmt::Debug for EndpointIdentity {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EndpointIdentity")
            .field("session_id", &self.session_id)
            .field("token", &"<redacted>")
            .finish()
    }
}

/// Credentials for one currently connected player endpoint.
///
/// The token intentionally has no serde implementation and its `Debug`
/// output is redacted. Wire code should keep it local to the connection.
#[derive(Clone, PartialEq, Eq)]
pub struct PlayerSession {
    player_id: PlayerId,
    session_id: SessionId,
    token: String,
}

impl PlayerSession {
    pub fn player_id(&self) -> PlayerId {
        self.player_id
    }

    pub fn session_id(&self) -> SessionId {
        self.session_id
    }

    pub fn token(&self) -> &str {
        &self.token
    }
}

impl fmt::Debug for PlayerSession {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PlayerSession")
            .field("player_id", &self.player_id)
            .field("session_id", &self.session_id)
            .field("token", &"<redacted>")
            .finish()
    }
}

/// Credentials for the one hidden native-oracle endpoint.
#[derive(Clone, PartialEq, Eq)]
pub struct OracleSession {
    session_id: SessionId,
    token: String,
}

impl OracleSession {
    pub fn session_id(&self) -> SessionId {
        self.session_id
    }

    pub fn token(&self) -> &str {
        &self.token
    }
}

impl fmt::Debug for OracleSession {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("OracleSession")
            .field("session_id", &self.session_id)
            .field("token", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct JoinRequest {
    pub account_key: String,
    pub requested_name: String,
    pub vehicle: String,
    pub max_health: u32,
    pub requested_team: Option<Team>,
    pub vehicle_configuration: Value,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct PlayerView {
    pub player_id: PlayerId,
    pub account_key: String,
    pub name: String,
    pub vehicle: String,
    pub max_health: u32,
    pub health: u32,
    pub team: Team,
    pub slot: usize,
    pub vehicle_configuration: Value,
}

#[derive(Clone, Debug, PartialEq)]
pub struct JoinOutcome {
    pub session: PlayerSession,
    pub player: PlayerView,
    pub host_player_id: Option<PlayerId>,
    pub state_revision: u64,
}

#[derive(Clone)]
struct PlayerState {
    view: PlayerView,
    session_id: SessionId,
    token: String,
    ready_round: Option<RoundId>,
    retired_round: Option<RoundId>,
}

impl PlayerState {
    fn session(&self) -> PlayerSession {
        PlayerSession {
            player_id: self.view.player_id,
            session_id: self.session_id,
            token: self.token.clone(),
        }
    }
}

#[derive(Clone)]
struct OracleState {
    session_id: SessionId,
    token: String,
    ready_scope: Option<SimulationScope>,
}

impl OracleState {
    fn session(&self) -> OracleSession {
        OracleSession {
            session_id: self.session_id,
            token: self.token.clone(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct RoundParticipant {
    pub player_id: PlayerId,
    pub account_key: String,
    pub name: String,
    pub vehicle: String,
    pub max_health: u32,
    pub team: Team,
    pub slot: usize,
    pub vehicle_configuration: Value,
}

impl From<&PlayerState> for RoundParticipant {
    fn from(player: &PlayerState) -> Self {
        Self {
            player_id: player.view.player_id,
            account_key: player.view.account_key.clone(),
            name: player.view.name.clone(),
            vehicle: player.view.vehicle.clone(),
            max_health: player.view.max_health,
            team: player.view.team,
            slot: player.view.slot,
            vehicle_configuration: player.view.vehicle_configuration.clone(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BattleWinner {
    Draw,
    Team(Team),
}

impl BattleWinner {
    pub const fn number(&self) -> u8 {
        match self {
            Self::Draw => 0,
            Self::Team(team) => team.number(),
        }
    }
}

impl Serialize for BattleWinner {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_u8(self.number())
    }
}

impl<'de> Deserialize<'de> for BattleWinner {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        match u8::deserialize(deserializer)? {
            0 => Ok(Self::Draw),
            1 => Ok(Self::Team(Team::One)),
            2 => Ok(Self::Team(Team::Two)),
            value => Err(de::Error::custom(format_args!(
                "winner must be 0, 1, or 2, received {value}"
            ))),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct BattleResult {
    pub winner: BattleWinner,
    pub reason: String,
    pub base_team: Option<Team>,
}

impl BattleResult {
    pub fn new(
        winner: BattleWinner,
        reason: impl Into<String>,
        base_team: Option<Team>,
    ) -> Result<Self, RoomError> {
        let reason = reason.into();
        let length = reason.chars().count();
        if reason.trim().is_empty() || length > MAX_RESULT_REASON_CHARS {
            return Err(RoomError::InvalidResult);
        }
        Ok(Self {
            winner,
            reason,
            base_team,
        })
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ResultReceipt {
    pub receipt_id: String,
    pub round_id: RoundId,
    pub player_id: PlayerId,
    pub account_key: String,
    pub result: BattleResult,
    /// Combat/reward code owns this schema. The room only durably associates
    /// it with the frozen participant and enforces delivery/ACK ownership.
    pub payload: Value,
}

#[derive(Clone, Debug, PartialEq)]
pub enum ReceiptPolicy {
    None,
    Participants(BTreeMap<PlayerId, Value>),
}

#[derive(Clone, Debug, PartialEq)]
pub struct StartOutcome {
    pub scope: SimulationScope,
    pub state_revision: u64,
    pub participants: Vec<RoundParticipant>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReadyOutcome {
    Waiting {
        scope: SimulationScope,
        missing_players: Vec<PlayerId>,
        oracle_ready: bool,
    },
    Activated {
        scope: SimulationScope,
        state_revision: u64,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DisconnectOutcome {
    pub removed_player_id: PlayerId,
    pub host_player_id: Option<PlayerId>,
    /// Loading resets immediately when its final visible participant leaves.
    pub round_reset: bool,
    /// Removing the last unready player can complete the loading barrier.
    pub battle_activated: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LeaveRoundOutcome {
    pub player_id: PlayerId,
    pub battle_activated: bool,
    pub round_abandoned: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OracleDetachOutcome {
    pub round_failed: bool,
    pub scope: SimulationScope,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum RoomError {
    #[error("invalid room configuration: {0}")]
    InvalidConfig(&'static str),
    #[error("room is not accepting lobby changes")]
    NotWaiting,
    #[error("room is not waiting at the loading barrier")]
    NotLoading,
    #[error("round is not active")]
    RoundNotActive,
    #[error("round already has a terminal result")]
    RoundAlreadyFinished,
    #[error("player was not found")]
    PlayerNotFound,
    #[error("stale or invalid player session credentials")]
    InvalidPlayerSession,
    #[error("stale or invalid oracle session credentials")]
    InvalidOracleSession,
    #[error("session identity is invalid")]
    InvalidSession,
    #[error("session identity is already connected")]
    DuplicateSession,
    #[error("session token is invalid")]
    InvalidSessionToken,
    #[error("session token is already connected")]
    DuplicateSessionToken,
    #[error("account key is invalid")]
    InvalidAccountKey,
    #[error("account key is already connected")]
    DuplicateAccountKey,
    #[error("room is full")]
    RoomFull,
    #[error("requested team is full")]
    TeamFull,
    #[error("team capacity must be between one and fifteen")]
    InvalidTeamSize,
    #[error("team capacity cannot shrink below its connected player count")]
    TeamOccupied,
    #[error("team must be one or two")]
    InvalidTeam,
    #[error("Bot tier mode is invalid")]
    InvalidBotTierMode,
    #[error("vehicle selection is invalid")]
    InvalidVehicle,
    #[error("battle result is invalid")]
    InvalidResult,
    #[error("only the current room host may start a round")]
    HostOnly,
    #[error("native oracle is already connected")]
    OracleAlreadyConnected,
    #[error("native oracle is required to start this round")]
    OracleRequired,
    #[error("message belongs to {received:?}, active scope is {expected:?}")]
    StaleScope {
        expected: SimulationScope,
        received: SimulationScope,
    },
    #[error("player {0} was not frozen into this round")]
    NotRoundParticipant(PlayerId),
    #[error("receipt payload is missing for frozen player {0}")]
    MissingReceiptPayload(PlayerId),
    #[error("receipt payload was supplied for nonparticipant player {0}")]
    UnexpectedReceiptPayload(PlayerId),
    #[error("receipt ID is invalid or duplicated")]
    InvalidReceipt,
    #[error("receipt does not exist")]
    ReceiptNotFound,
    #[error("receipt belongs to another account")]
    ReceiptNotOwned,
    #[error("round or authority counter is exhausted")]
    CounterExhausted,
}

impl RoomError {
    /// Stable protocol-facing code. Text remains free to improve independently.
    pub fn code(&self) -> &'static str {
        match self {
            Self::InvalidConfig(_) => "invalid_config",
            Self::NotWaiting => "not_waiting",
            Self::NotLoading => "not_loading",
            Self::RoundNotActive => "round_not_active",
            Self::RoundAlreadyFinished => "round_already_finished",
            Self::PlayerNotFound => "player_not_found",
            Self::InvalidPlayerSession => "invalid_player_session",
            Self::InvalidOracleSession => "invalid_oracle_session",
            Self::InvalidSession => "invalid_session",
            Self::DuplicateSession => "duplicate_session",
            Self::InvalidSessionToken => "invalid_session_token",
            Self::DuplicateSessionToken => "duplicate_session_token",
            Self::InvalidAccountKey => "invalid_account_key",
            Self::DuplicateAccountKey => "duplicate_account_key",
            Self::RoomFull => "full",
            Self::TeamFull => "team_full",
            Self::InvalidTeamSize => "invalid_size",
            Self::TeamOccupied => "team_occupied",
            Self::InvalidTeam => "invalid_team",
            Self::InvalidBotTierMode => "invalid_mode",
            Self::InvalidVehicle => "invalid_vehicle",
            Self::InvalidResult => "invalid_result",
            Self::HostOnly => "host_only",
            Self::OracleAlreadyConnected => "worker_already_connected",
            Self::OracleRequired => "simulation_worker_required",
            Self::StaleScope { .. } => "stale_scope",
            Self::NotRoundParticipant(_) => "not_round_participant",
            Self::MissingReceiptPayload(_) => "missing_receipt_payload",
            Self::UnexpectedReceiptPayload(_) => "unexpected_receipt_payload",
            Self::InvalidReceipt => "invalid_receipt",
            Self::ReceiptNotFound => "receipt_not_found",
            Self::ReceiptNotOwned => "receipt_not_owned",
            Self::CounterExhausted => "counter_exhausted",
        }
    }
}

/// A single trusted-LAN room. All mutating methods are deterministic and
/// synchronous; the server event loop must serialize access to this value.
#[derive(Clone)]
pub struct RoomState {
    config: RoomConfig,
    phase: RoomPhase,
    round_id: RoundId,
    authority_epoch: Epoch,
    state_revision: u64,
    next_player_id: PlayerId,
    players: BTreeMap<PlayerId, PlayerState>,
    player_by_session: HashMap<SessionId, PlayerId>,
    host_player_id: Option<PlayerId>,
    bot_tier_mode: BotTierMode,
    oracle: Option<OracleState>,
    round_participants: BTreeMap<PlayerId, RoundParticipant>,
    battle_result: Option<BattleResult>,
    receipts: VecDeque<ResultReceipt>,
    delivered_by_session: HashMap<SessionId, String>,
}

impl RoomState {
    pub fn new(config: RoomConfig) -> Self {
        Self {
            config,
            phase: RoomPhase::Waiting,
            round_id: 1,
            authority_epoch: 0,
            state_revision: 0,
            next_player_id: 1,
            players: BTreeMap::new(),
            player_by_session: HashMap::new(),
            host_player_id: None,
            bot_tier_mode: BotTierMode::Random,
            oracle: None,
            round_participants: BTreeMap::new(),
            battle_result: None,
            receipts: VecDeque::new(),
            delivered_by_session: HashMap::new(),
        }
    }

    pub fn with_receipts(
        config: RoomConfig,
        receipts: impl IntoIterator<Item = ResultReceipt>,
    ) -> Result<Self, RoomError> {
        let mut room = Self::new(config);
        let mut ids = HashSet::new();
        for receipt in receipts {
            if room.receipts.len() >= MAX_RESULT_RECEIPTS
                || !valid_receipt_component(&receipt.receipt_id, 96)
                || !valid_account_key(&receipt.account_key)
                || !ids.insert(receipt.receipt_id.clone())
            {
                return Err(RoomError::InvalidReceipt);
            }
            room.receipts.push_back(receipt);
        }
        Ok(room)
    }

    pub fn config(&self) -> &RoomConfig {
        &self.config
    }

    pub fn phase(&self) -> RoomPhase {
        self.phase
    }

    pub fn round_id(&self) -> RoundId {
        self.round_id
    }

    pub fn authority_epoch(&self) -> Epoch {
        self.authority_epoch
    }

    pub fn scope(&self) -> SimulationScope {
        SimulationScope {
            round_id: self.round_id,
            epoch: self.authority_epoch,
        }
    }

    pub fn state_revision(&self) -> u64 {
        self.state_revision
    }

    pub fn host_player_id(&self) -> Option<PlayerId> {
        self.host_player_id
    }

    pub fn bot_tier_mode(&self) -> BotTierMode {
        self.bot_tier_mode
    }

    pub fn player_count(&self) -> usize {
        self.players.len()
    }

    pub fn oracle_connected(&self) -> bool {
        self.oracle.is_some()
    }

    pub fn players(&self) -> impl Iterator<Item = PlayerView> + '_ {
        self.players.values().map(|player| player.view.clone())
    }

    pub fn player(&self, player_id: PlayerId) -> Option<PlayerView> {
        self.players
            .get(&player_id)
            .map(|player| player.view.clone())
    }

    pub fn is_round_player_active(&self, player_id: PlayerId) -> bool {
        self.players.get(&player_id).is_some_and(|player| {
            self.round_participants.contains_key(&player_id)
                && player.retired_round != Some(self.round_id)
        })
    }

    pub fn round_participants(&self) -> impl Iterator<Item = &RoundParticipant> {
        self.round_participants.values()
    }

    pub fn battle_result(&self) -> Option<&BattleResult> {
        self.battle_result.as_ref()
    }

    pub fn receipt_ledger(&self) -> impl Iterator<Item = &ResultReceipt> {
        self.receipts.iter()
    }

    pub fn join_player(
        &mut self,
        endpoint: EndpointIdentity,
        request: JoinRequest,
    ) -> Result<JoinOutcome, RoomError> {
        if self.phase != RoomPhase::Waiting {
            return Err(RoomError::NotWaiting);
        }
        self.validate_new_endpoint(endpoint.session_id, &endpoint.token)?;
        if !valid_account_key(&request.account_key) {
            return Err(RoomError::InvalidAccountKey);
        }
        if self
            .players
            .values()
            .any(|player| player.view.account_key == request.account_key)
        {
            return Err(RoomError::DuplicateAccountKey);
        }
        if self.players.len() >= self.config.max_players {
            return Err(RoomError::RoomFull);
        }

        let team = self.choose_team(request.requested_team)?;
        let slot = self.first_free_slot(team).ok_or(RoomError::TeamFull)?;
        let player_id = self.next_player_id;
        self.next_player_id = self
            .next_player_id
            .checked_add(1)
            .ok_or(RoomError::CounterExhausted)?;
        let fallback_name = format!("Player{player_id}");
        let base_name = sanitize_name(&request.requested_name, &fallback_name);
        let name = self.unique_name(base_name);
        let vehicle = sanitize_vehicle(&request.vehicle, &self.config.default_vehicle);
        if vehicle.is_empty() {
            return Err(RoomError::InvalidVehicle);
        }
        let max_health = request.max_health.clamp(1, 100_000);
        let view = PlayerView {
            player_id,
            account_key: request.account_key,
            name,
            vehicle,
            max_health,
            health: max_health,
            team,
            slot,
            vehicle_configuration: request.vehicle_configuration,
        };
        let state = PlayerState {
            view: view.clone(),
            session_id: endpoint.session_id,
            token: endpoint.token,
            ready_round: None,
            retired_round: None,
        };
        let session = state.session();
        self.player_by_session.insert(state.session_id, player_id);
        self.players.insert(player_id, state);
        if self.host_player_id.is_none() {
            self.host_player_id = Some(player_id);
        }
        self.bump_revision()?;
        Ok(JoinOutcome {
            session,
            player: view,
            host_player_id: self.host_player_id,
            state_revision: self.state_revision,
        })
    }

    pub fn disconnect_player(
        &mut self,
        session: &PlayerSession,
    ) -> Result<DisconnectOutcome, RoomError> {
        let player_id = self.authenticate_player(session)?;
        let player = self
            .players
            .remove(&player_id)
            .ok_or(RoomError::PlayerNotFound)?;
        self.player_by_session.remove(&player.session_id);
        self.delivered_by_session.remove(&player.session_id);
        if self.host_player_id == Some(player_id) {
            self.elect_host();
        }
        self.bump_revision()?;

        let round_reset = self.phase == RoomPhase::Loading && self.players.is_empty();
        let mut battle_activated = false;
        if round_reset {
            self.reset_round_internal()?;
        } else if self.phase == RoomPhase::Loading {
            battle_activated = matches!(self.activate_if_ready()?, ReadyOutcome::Activated { .. });
        }
        Ok(DisconnectOutcome {
            removed_player_id: player_id,
            host_player_id: self.host_player_id,
            round_reset,
            battle_activated,
        })
    }

    pub fn select_team(&mut self, session: &PlayerSession, team: Team) -> Result<bool, RoomError> {
        if self.phase != RoomPhase::Waiting {
            return Err(RoomError::NotWaiting);
        }
        let player_id = self.authenticate_player(session)?;
        let current_team = self.players[&player_id].view.team;
        if current_team == team {
            return Ok(false);
        }
        let slot = self.first_free_slot(team).ok_or(RoomError::TeamFull)?;
        let player = self
            .players
            .get_mut(&player_id)
            .ok_or(RoomError::PlayerNotFound)?;
        player.view.team = team;
        player.view.slot = slot;
        self.bump_revision()?;
        Ok(true)
    }

    pub fn set_team_capacity(
        &mut self,
        session: &PlayerSession,
        team: Team,
        capacity: usize,
    ) -> Result<bool, RoomError> {
        if self.phase != RoomPhase::Waiting {
            return Err(RoomError::NotWaiting);
        }
        let player_id = self.authenticate_player(session)?;
        if self.host_player_id != Some(player_id) {
            return Err(RoomError::HostOnly);
        }
        if !(1..=MAX_TEAM_CAPACITY).contains(&capacity) {
            return Err(RoomError::InvalidTeamSize);
        }
        let mut participants: Vec<_> = self
            .players
            .values()
            .filter(|player| player.view.team == team)
            .map(|player| (player.view.slot, player.view.player_id))
            .collect();
        if participants.len() > capacity {
            return Err(RoomError::TeamOccupied);
        }
        if self.config.team_capacity(team) == capacity {
            return Ok(false);
        }

        participants.sort_unstable();
        for (slot, (_, player_id)) in participants.into_iter().enumerate() {
            self.players
                .get_mut(&player_id)
                .expect("the waiting-room team roster was frozen")
                .view
                .slot = slot;
        }
        self.config.team_capacities[team.index()] = capacity;
        self.bump_revision()?;
        Ok(true)
    }

    pub fn set_bot_tier_mode(
        &mut self,
        session: &PlayerSession,
        mode: BotTierMode,
    ) -> Result<bool, RoomError> {
        if self.phase != RoomPhase::Waiting {
            return Err(RoomError::NotWaiting);
        }
        let player_id = self.authenticate_player(session)?;
        if self.host_player_id != Some(player_id) {
            return Err(RoomError::HostOnly);
        }
        if self.bot_tier_mode == mode {
            return Ok(false);
        }
        self.bot_tier_mode = mode;
        self.bump_revision()?;
        Ok(true)
    }

    pub fn select_vehicle(
        &mut self,
        session: &PlayerSession,
        vehicle: &str,
        max_health: u32,
        vehicle_configuration: Value,
    ) -> Result<bool, RoomError> {
        if self.phase != RoomPhase::Waiting {
            return Err(RoomError::NotWaiting);
        }
        let player_id = self.authenticate_player(session)?;
        let vehicle = sanitize_vehicle(vehicle, "");
        if vehicle.is_empty() {
            return Err(RoomError::InvalidVehicle);
        }
        let max_health = max_health.clamp(1, 100_000);
        let player = self
            .players
            .get_mut(&player_id)
            .ok_or(RoomError::PlayerNotFound)?;
        if player.view.vehicle == vehicle
            && player.view.max_health == max_health
            && player.view.vehicle_configuration == vehicle_configuration
        {
            return Ok(false);
        }
        player.view.vehicle = vehicle;
        player.view.max_health = max_health;
        player.view.health = max_health;
        player.view.vehicle_configuration = vehicle_configuration;
        self.bump_revision()?;
        Ok(true)
    }

    pub fn attach_oracle(
        &mut self,
        endpoint: EndpointIdentity,
    ) -> Result<OracleSession, RoomError> {
        if self.phase != RoomPhase::Waiting {
            return Err(RoomError::NotWaiting);
        }
        if self.oracle.is_some() {
            return Err(RoomError::OracleAlreadyConnected);
        }
        self.validate_new_endpoint(endpoint.session_id, &endpoint.token)?;
        self.authority_epoch = self
            .authority_epoch
            .checked_add(1)
            .ok_or(RoomError::CounterExhausted)?;
        let oracle = OracleState {
            session_id: endpoint.session_id,
            token: endpoint.token,
            ready_scope: None,
        };
        let result = oracle.session();
        self.oracle = Some(oracle);
        self.bump_revision()?;
        Ok(result)
    }

    pub fn detach_oracle(
        &mut self,
        session: &OracleSession,
    ) -> Result<OracleDetachOutcome, RoomError> {
        self.authenticate_oracle(session)?;
        self.oracle = None;
        self.authority_epoch = self
            .authority_epoch
            .checked_add(1)
            .ok_or(RoomError::CounterExhausted)?;
        let round_failed = matches!(self.phase, RoomPhase::Loading | RoomPhase::Battle);
        if round_failed {
            self.phase = RoomPhase::Finished;
            self.battle_result = Some(BattleResult {
                winner: BattleWinner::Draw,
                reason: "worker_disconnected".to_owned(),
                base_team: None,
            });
        }
        self.bump_revision()?;
        Ok(OracleDetachOutcome {
            round_failed,
            scope: self.scope(),
        })
    }

    pub fn request_start(&mut self, session: &PlayerSession) -> Result<StartOutcome, RoomError> {
        if self.phase != RoomPhase::Waiting {
            return Err(RoomError::NotWaiting);
        }
        let player_id = self.authenticate_player(session)?;
        if self.host_player_id != Some(player_id) {
            return Err(RoomError::HostOnly);
        }
        if self.oracle.is_none() {
            return Err(RoomError::OracleRequired);
        }

        self.round_participants = self
            .players
            .iter()
            .map(|(&id, player)| (id, RoundParticipant::from(player)))
            .collect();
        for player in self.players.values_mut() {
            player.ready_round = None;
            player.retired_round = None;
        }
        if let Some(oracle) = self.oracle.as_mut() {
            oracle.ready_scope = None;
        }
        self.battle_result = None;
        self.phase = RoomPhase::Loading;
        self.bump_revision()?;
        Ok(StartOutcome {
            scope: self.scope(),
            state_revision: self.state_revision,
            participants: self.round_participants.values().cloned().collect(),
        })
    }

    pub fn mark_player_ready(
        &mut self,
        session: &PlayerSession,
        scope: SimulationScope,
    ) -> Result<ReadyOutcome, RoomError> {
        if self.phase != RoomPhase::Loading {
            return Err(RoomError::NotLoading);
        }
        self.require_scope(scope)?;
        let player_id = self.authenticate_player(session)?;
        if !self.round_participants.contains_key(&player_id) {
            return Err(RoomError::NotRoundParticipant(player_id));
        }
        if self.players[&player_id].retired_round == Some(self.round_id) {
            return Err(RoomError::NotRoundParticipant(player_id));
        }
        self.players
            .get_mut(&player_id)
            .ok_or(RoomError::PlayerNotFound)?
            .ready_round = Some(self.round_id);
        self.activate_if_ready()
    }

    pub fn leave_round(
        &mut self,
        session: &PlayerSession,
        scope: SimulationScope,
    ) -> Result<LeaveRoundOutcome, RoomError> {
        if !matches!(self.phase, RoomPhase::Loading | RoomPhase::Battle) {
            return Err(RoomError::RoundNotActive);
        }
        self.require_scope(scope)?;
        let player_id = self.authenticate_player(session)?;
        if !self.round_participants.contains_key(&player_id) {
            return Err(RoomError::NotRoundParticipant(player_id));
        }
        let player = self
            .players
            .get_mut(&player_id)
            .ok_or(RoomError::PlayerNotFound)?;
        if player.retired_round == Some(self.round_id) {
            return Ok(LeaveRoundOutcome {
                player_id,
                battle_activated: false,
                round_abandoned: self.active_round_player_count() == 0,
            });
        }
        player.retired_round = Some(self.round_id);
        player.ready_round = None;
        self.bump_revision()?;
        let round_abandoned = self.active_round_player_count() == 0;
        let battle_activated = if self.phase == RoomPhase::Loading && !round_abandoned {
            matches!(self.activate_if_ready()?, ReadyOutcome::Activated { .. })
        } else {
            false
        };
        Ok(LeaveRoundOutcome {
            player_id,
            battle_activated,
            round_abandoned,
        })
    }

    pub fn mark_oracle_ready(
        &mut self,
        session: &OracleSession,
        scope: SimulationScope,
    ) -> Result<ReadyOutcome, RoomError> {
        if self.phase != RoomPhase::Loading {
            return Err(RoomError::NotLoading);
        }
        self.require_scope(scope)?;
        self.authenticate_oracle(session)?;
        self.oracle
            .as_mut()
            .ok_or(RoomError::InvalidOracleSession)?
            .ready_scope = Some(scope);
        self.activate_if_ready()
    }

    pub fn finish_round(
        &mut self,
        scope: SimulationScope,
        result: BattleResult,
        receipt_policy: ReceiptPolicy,
    ) -> Result<Vec<ResultReceipt>, RoomError> {
        self.require_scope(scope)?;
        if self.battle_result.is_some() || self.phase == RoomPhase::Finished {
            return Err(RoomError::RoundAlreadyFinished);
        }
        if !matches!(self.phase, RoomPhase::Loading | RoomPhase::Battle) {
            return Err(RoomError::RoundNotActive);
        }
        if result.reason.trim().is_empty()
            || result.reason.chars().count() > MAX_RESULT_REASON_CHARS
        {
            return Err(RoomError::InvalidResult);
        }

        let new_receipts = match receipt_policy {
            ReceiptPolicy::None => Vec::new(),
            ReceiptPolicy::Participants(mut payloads) => {
                for player_id in payloads.keys() {
                    if !self.round_participants.contains_key(player_id) {
                        return Err(RoomError::UnexpectedReceiptPayload(*player_id));
                    }
                }
                let mut receipts = Vec::with_capacity(self.round_participants.len());
                for participant in self.round_participants.values() {
                    let payload = payloads
                        .remove(&participant.player_id)
                        .ok_or(RoomError::MissingReceiptPayload(participant.player_id))?;
                    receipts.push(ResultReceipt {
                        receipt_id: format!(
                            "{}:{}:{}",
                            self.config.receipt_namespace, self.round_id, participant.player_id
                        ),
                        round_id: self.round_id,
                        player_id: participant.player_id,
                        account_key: participant.account_key.clone(),
                        result: result.clone(),
                        payload,
                    });
                }
                receipts
            }
        };

        for receipt in &new_receipts {
            if self
                .receipts
                .iter()
                .any(|existing| existing.receipt_id == receipt.receipt_id)
            {
                return Err(RoomError::InvalidReceipt);
            }
        }
        for receipt in &new_receipts {
            self.receipts.push_back(receipt.clone());
        }
        while self.receipts.len() > MAX_RESULT_RECEIPTS {
            if let Some(removed) = self.receipts.pop_front() {
                self.delivered_by_session
                    .retain(|_, receipt_id| receipt_id != &removed.receipt_id);
            }
        }
        self.battle_result = Some(result);
        self.phase = RoomPhase::Finished;
        self.bump_revision()?;
        Ok(new_receipts)
    }

    pub fn next_receipt(
        &mut self,
        session: &PlayerSession,
    ) -> Result<Option<ResultReceipt>, RoomError> {
        let player_id = self.authenticate_player(session)?;
        let player = self
            .players
            .get(&player_id)
            .ok_or(RoomError::PlayerNotFound)?;
        let receipt = self
            .receipts
            .iter()
            .find(|receipt| receipt.account_key == player.view.account_key)
            .cloned();
        let Some(receipt) = receipt else {
            return Ok(None);
        };
        if self.delivered_by_session.get(&player.session_id) == Some(&receipt.receipt_id) {
            return Ok(None);
        }
        self.delivered_by_session
            .insert(player.session_id, receipt.receipt_id.clone());
        Ok(Some(receipt))
    }

    pub fn acknowledge_receipt(
        &mut self,
        session: &PlayerSession,
        receipt_id: &str,
    ) -> Result<ResultReceipt, RoomError> {
        let player_id = self.authenticate_player(session)?;
        let account_key = self
            .players
            .get(&player_id)
            .ok_or(RoomError::PlayerNotFound)?
            .view
            .account_key
            .clone();
        let index = self
            .receipts
            .iter()
            .position(|receipt| receipt.receipt_id == receipt_id)
            .ok_or(RoomError::ReceiptNotFound)?;
        if self.receipts[index].account_key != account_key {
            return Err(RoomError::ReceiptNotOwned);
        }
        let receipt = self
            .receipts
            .remove(index)
            .expect("the located receipt index remains valid");
        self.delivered_by_session
            .retain(|_, delivered| delivered != receipt_id);
        Ok(receipt)
    }

    pub fn reset_round(&mut self, scope: SimulationScope) -> Result<SimulationScope, RoomError> {
        self.require_scope(scope)?;
        if self.phase != RoomPhase::Finished {
            return Err(RoomError::RoundNotActive);
        }
        self.reset_round_internal()?;
        Ok(self.scope())
    }

    fn activate_if_ready(&mut self) -> Result<ReadyOutcome, RoomError> {
        let scope = self.scope();
        let missing_players: Vec<_> = self
            .players
            .values()
            .filter(|player| {
                self.round_participants.contains_key(&player.view.player_id)
                    && player.retired_round != Some(self.round_id)
                    && player.ready_round != Some(self.round_id)
            })
            .map(|player| player.view.player_id)
            .collect();
        let oracle_ready = self
            .oracle
            .as_ref()
            .is_some_and(|oracle| oracle.ready_scope == Some(scope));
        if self.active_round_player_count() == 0 || !missing_players.is_empty() || !oracle_ready {
            return Ok(ReadyOutcome::Waiting {
                scope,
                missing_players,
                oracle_ready,
            });
        }
        self.phase = RoomPhase::Battle;
        self.bump_revision()?;
        Ok(ReadyOutcome::Activated {
            scope,
            state_revision: self.state_revision,
        })
    }

    fn active_round_player_count(&self) -> usize {
        self.players
            .values()
            .filter(|player| {
                self.round_participants.contains_key(&player.view.player_id)
                    && player.retired_round != Some(self.round_id)
            })
            .count()
    }

    fn reset_round_internal(&mut self) -> Result<(), RoomError> {
        self.round_id = self
            .round_id
            .checked_add(1)
            .ok_or(RoomError::CounterExhausted)?;
        self.phase = RoomPhase::Waiting;
        self.authority_epoch = if self.oracle.is_some() { 1 } else { 0 };
        self.round_participants.clear();
        self.battle_result = None;
        for player in self.players.values_mut() {
            player.view.health = player.view.max_health;
            player.ready_round = None;
            player.retired_round = None;
        }
        if let Some(oracle) = self.oracle.as_mut() {
            oracle.ready_scope = None;
        }
        self.elect_host();
        self.bump_revision()
    }

    fn choose_team(&self, requested: Option<Team>) -> Result<Team, RoomError> {
        if let Some(team) = requested {
            return self
                .first_free_slot(team)
                .map(|_| team)
                .ok_or(RoomError::TeamFull);
        }
        let mut candidates: Vec<_> = Team::ALL
            .into_iter()
            .filter(|team| self.first_free_slot(*team).is_some())
            .collect();
        candidates.sort_by_key(|team| (self.team_player_count(*team), team.number()));
        candidates.into_iter().next().ok_or(RoomError::TeamFull)
    }

    fn team_player_count(&self, team: Team) -> usize {
        self.players
            .values()
            .filter(|player| player.view.team == team)
            .count()
    }

    fn first_free_slot(&self, team: Team) -> Option<usize> {
        let occupied: HashSet<_> = self
            .players
            .values()
            .filter(|player| player.view.team == team)
            .map(|player| player.view.slot)
            .collect();
        (0..self.config.team_capacity(team)).find(|slot| !occupied.contains(slot))
    }

    fn unique_name(&self, base: String) -> String {
        let existing: HashSet<_> = self
            .players
            .values()
            .map(|player| player.view.name.to_lowercase())
            .collect();
        if !existing.contains(&base.to_lowercase()) {
            return base;
        }
        for suffix in 2_u64.. {
            let suffix = format!("-{suffix}");
            let prefix_length = MAX_PLAYER_NAME_CHARS.saturating_sub(suffix.chars().count());
            let prefix: String = base.chars().take(prefix_length.max(1)).collect();
            let candidate = format!("{prefix}{suffix}");
            if !existing.contains(&candidate.to_lowercase()) {
                return candidate;
            }
        }
        unreachable!("the unbounded numeric suffix always produces a unique name")
    }

    fn validate_new_endpoint(
        &self,
        session_id: SessionId,
        session_token: &str,
    ) -> Result<(), RoomError> {
        if session_id == 0 {
            return Err(RoomError::InvalidSession);
        }
        if self.player_by_session.contains_key(&session_id)
            || self
                .oracle
                .as_ref()
                .is_some_and(|oracle| oracle.session_id == session_id)
        {
            return Err(RoomError::DuplicateSession);
        }
        if !valid_session_token(session_token) {
            return Err(RoomError::InvalidSessionToken);
        }
        if self
            .players
            .values()
            .any(|player| player.token == session_token)
            || self
                .oracle
                .as_ref()
                .is_some_and(|oracle| oracle.token == session_token)
        {
            return Err(RoomError::DuplicateSessionToken);
        }
        Ok(())
    }

    fn authenticate_player(&self, session: &PlayerSession) -> Result<PlayerId, RoomError> {
        let player = self
            .players
            .get(&session.player_id)
            .ok_or(RoomError::InvalidPlayerSession)?;
        if player.session_id != session.session_id
            || player.token != session.token
            || self.player_by_session.get(&session.session_id) != Some(&session.player_id)
        {
            return Err(RoomError::InvalidPlayerSession);
        }
        Ok(session.player_id)
    }

    fn authenticate_oracle(&self, session: &OracleSession) -> Result<(), RoomError> {
        let oracle = self
            .oracle
            .as_ref()
            .ok_or(RoomError::InvalidOracleSession)?;
        if oracle.session_id != session.session_id || oracle.token != session.token {
            return Err(RoomError::InvalidOracleSession);
        }
        Ok(())
    }

    fn require_scope(&self, received: SimulationScope) -> Result<(), RoomError> {
        let expected = self.scope();
        if received != expected {
            return Err(RoomError::StaleScope { expected, received });
        }
        Ok(())
    }

    fn elect_host(&mut self) {
        self.host_player_id = self.players.keys().next().copied();
    }

    fn bump_revision(&mut self) -> Result<(), RoomError> {
        self.state_revision = self
            .state_revision
            .checked_add(1)
            .ok_or(RoomError::CounterExhausted)?;
        Ok(())
    }
}

fn sanitize_name(value: &str, fallback: &str) -> String {
    let clean: String = value
        .trim()
        .chars()
        .filter(|character| character.is_alphanumeric() || matches!(character, ' ' | '_' | '-'))
        .take(MAX_PLAYER_NAME_CHARS)
        .collect();
    let lower = clean.to_lowercase();
    if clean.is_empty()
        || matches!(
            lower.as_str(),
            "defaultplayer" | "player" | "offline_player"
        )
    {
        fallback.chars().take(MAX_PLAYER_NAME_CHARS).collect()
    } else {
        clean
    }
}

fn sanitize_vehicle(value: &str, fallback: &str) -> String {
    let clean: String = value
        .trim()
        .chars()
        .filter(|character| character.is_alphanumeric() || matches!(character, ':' | '_' | '-'))
        .take(MAX_VEHICLE_NAME_CHARS)
        .collect();
    if clean.is_empty() {
        fallback
            .chars()
            .filter(|character| character.is_alphanumeric() || matches!(character, ':' | '_' | '-'))
            .take(MAX_VEHICLE_NAME_CHARS)
            .collect()
    } else {
        clean
    }
}

fn valid_account_key(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_ACCOUNT_KEY_BYTES
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn valid_session_token(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_SESSION_TOKEN_BYTES
        && value.bytes().all(|byte| byte.is_ascii_graphic())
}

fn valid_receipt_component(value: &str, maximum_bytes: usize) -> bool {
    !value.is_empty()
        && value.len() <= maximum_bytes
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b':'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn room(team_one: usize, team_two: usize) -> RoomState {
        RoomState::new(RoomConfig::new(30, team_one, team_two, "ussr:R11_MS-1", "server").unwrap())
    }

    fn join_request(index: u64, name: &str, team: Option<Team>) -> JoinRequest {
        JoinRequest {
            account_key: format!("account-{index}"),
            requested_name: name.to_owned(),
            vehicle: "ussr:R11_MS-1".to_owned(),
            max_health: 900,
            requested_team: team,
            vehicle_configuration: json!({"gun": "stock"}),
        }
    }

    fn endpoint(index: u64) -> EndpointIdentity {
        EndpointIdentity::from_transport(index, format!("token-{index}"))
    }

    fn join(room: &mut RoomState, index: u64, name: &str, team: Option<Team>) -> JoinOutcome {
        room.join_player(endpoint(index), join_request(index, name, team))
            .unwrap()
    }

    fn attach_oracle(room: &mut RoomState, index: u64) -> OracleSession {
        room.attach_oracle(EndpointIdentity::from_transport(
            index,
            format!("oracle-token-{index}"),
        ))
        .unwrap()
    }

    fn activate_one_player(
        room: &mut RoomState,
        player: &PlayerSession,
        oracle: &OracleSession,
    ) -> SimulationScope {
        let start = room.request_start(player).unwrap();
        assert!(matches!(
            room.mark_player_ready(player, start.scope).unwrap(),
            ReadyOutcome::Waiting { .. }
        ));
        assert!(matches!(
            room.mark_oracle_ready(oracle, start.scope).unwrap(),
            ReadyOutcome::Activated { .. }
        ));
        start.scope
    }

    #[test]
    fn leave_round_keeps_the_lobby_session_and_is_idempotent() {
        let mut room = room(2, 2);
        let player = join(&mut room, 1, "Alice", Some(Team::One));
        let oracle = attach_oracle(&mut room, 9);
        let scope = activate_one_player(&mut room, &player.session, &oracle);

        let first = room.leave_round(&player.session, scope).unwrap();
        assert!(first.round_abandoned);
        assert!(!room.is_round_player_active(player.player.player_id));
        assert_eq!(room.player_count(), 1);

        let retry = room.leave_round(&player.session, scope).unwrap();
        assert_eq!(retry, first);
        assert_eq!(room.player_count(), 1);
    }

    #[test]
    fn join_balances_teams_and_makes_names_unique() {
        let mut room = room(2, 2);
        let first = join(&mut room, 1, "Player", None);
        let second = join(&mut room, 2, "Alice", None);
        let third = join(&mut room, 3, "alice", None);

        assert_eq!(first.player.name, "Player1");
        assert_eq!(first.player.team, Team::One);
        assert_eq!(second.player.team, Team::Two);
        assert_eq!(third.player.team, Team::One);
        assert_eq!(third.player.name, "alice-2");
        assert_eq!(room.host_player_id(), Some(first.player.player_id));
    }

    #[test]
    fn team_and_winner_keep_existing_numeric_wire_values() {
        assert_eq!(serde_json::to_value(Team::Two).unwrap(), json!(2));
        assert_eq!(
            serde_json::to_value(BattleWinner::Team(Team::One)).unwrap(),
            json!(1)
        );
        assert_eq!(
            serde_json::from_value::<BattleWinner>(json!(0)).unwrap(),
            BattleWinner::Draw
        );
        assert!(serde_json::from_value::<Team>(json!(3)).is_err());
    }

    #[test]
    fn account_session_and_token_are_unique_live_identities() {
        let mut room = room(2, 2);
        let first = join(&mut room, 1, "Alice", None);

        let mut duplicate_account = join_request(2, "Bob", None);
        duplicate_account.account_key = first.player.account_key.clone();
        assert_eq!(
            room.join_player(endpoint(2), duplicate_account)
                .unwrap_err(),
            RoomError::DuplicateAccountKey
        );

        assert_eq!(
            room.join_player(endpoint(1), join_request(2, "Bob", None))
                .unwrap_err(),
            RoomError::DuplicateSession
        );
        assert_eq!(
            room.join_player(
                EndpointIdentity::from_transport(2, first.session.token()),
                join_request(2, "Bob", None),
            )
            .unwrap_err(),
            RoomError::DuplicateSessionToken
        );
    }

    #[test]
    fn explicit_team_capacity_and_waiting_switch_are_enforced() {
        let mut room = room(2, 1);
        let first = join(&mut room, 1, "A", Some(Team::One));
        let second = join(&mut room, 2, "B", Some(Team::Two));
        assert_eq!(
            room.join_player(endpoint(3), join_request(3, "C", Some(Team::Two)))
                .unwrap_err(),
            RoomError::TeamFull
        );
        assert_eq!(
            room.select_team(&first.session, Team::Two).unwrap_err(),
            RoomError::TeamFull
        );
        room.disconnect_player(&second.session).unwrap();
        assert!(room.select_team(&first.session, Team::Two).unwrap());
        assert_eq!(room.player(first.player.player_id).unwrap().slot, 0);
    }

    #[test]
    fn only_the_waiting_host_can_resize_teams_and_shrinking_compacts_slots() {
        let mut room = room(4, 4);
        let host = join(&mut room, 1, "Host", Some(Team::One));
        let second = join(&mut room, 2, "Second", Some(Team::One));
        let guest = join(&mut room, 3, "Guest", Some(Team::Two));

        assert_eq!(
            room.set_team_capacity(&guest.session, Team::One, 3)
                .unwrap_err(),
            RoomError::HostOnly
        );
        assert_eq!(
            room.set_team_capacity(&host.session, Team::One, 1)
                .unwrap_err(),
            RoomError::TeamOccupied
        );
        assert_eq!(
            room.set_team_capacity(&host.session, Team::One, 0)
                .unwrap_err(),
            RoomError::InvalidTeamSize
        );
        assert_eq!(
            room.set_team_capacity(&host.session, Team::One, 16)
                .unwrap_err(),
            RoomError::InvalidTeamSize
        );

        room.disconnect_player(&host.session).unwrap();
        assert_eq!(room.host_player_id(), Some(second.player.player_id));
        assert_eq!(room.player(second.player.player_id).unwrap().slot, 1);
        assert!(room
            .set_team_capacity(&second.session, Team::One, 1)
            .unwrap());
        assert_eq!(room.config().team_capacity(Team::One), 1);
        assert_eq!(room.player(second.player.player_id).unwrap().slot, 0);
        assert!(!room
            .set_team_capacity(&second.session, Team::One, 1)
            .unwrap());
        assert!(room
            .set_team_capacity(&second.session, Team::Two, 5)
            .unwrap());
        assert_eq!(room.config().team_capacity(Team::Two), 5);
    }

    #[test]
    fn only_the_waiting_host_can_select_a_bot_tier_mode() {
        let mut room = room(2, 2);
        let host = join(&mut room, 1, "Host", Some(Team::One));
        let guest = join(&mut room, 2, "Guest", Some(Team::Two));
        assert_eq!(room.bot_tier_mode(), BotTierMode::Random);
        assert_eq!(
            room.set_bot_tier_mode(&guest.session, BotTierMode::Same)
                .unwrap_err(),
            RoomError::HostOnly
        );
        assert!(room
            .set_bot_tier_mode(&host.session, BotTierMode::Minus1Plus2)
            .unwrap());
        assert_eq!(room.bot_tier_mode(), BotTierMode::Minus1Plus2);
        assert!(!room
            .set_bot_tier_mode(&host.session, BotTierMode::Minus1Plus2)
            .unwrap());
    }

    #[test]
    fn bot_tier_modes_match_the_current_main_bands() {
        assert!(BotTierMode::Random.admits_tier(6, 5));
        assert!(BotTierMode::Random.admits_tier(6, 7));
        assert!(!BotTierMode::Random.admits_tier(6, 8));
        assert!(BotTierMode::Same.admits_tier(6, 6));
        assert!(BotTierMode::Minus1Zero.admits_tier(6, 5));
        assert!(!BotTierMode::Minus1Zero.admits_tier(6, 7));
        assert!(BotTierMode::ZeroPlus1.admits_tier(6, 7));
        assert!(BotTierMode::Minus1Plus2.admits_tier(1, 3));
        assert!(!BotTierMode::Minus1Plus2.admits_tier(1, 4));
    }

    #[test]
    fn stale_session_cannot_mutate_a_rejoined_identity() {
        let mut room = room(1, 1);
        let first = join(&mut room, 1, "A", None);
        room.disconnect_player(&first.session).unwrap();
        let second = join(&mut room, 2, "A", None);

        assert_eq!(
            room.select_team(&first.session, Team::Two).unwrap_err(),
            RoomError::InvalidPlayerSession
        );
        assert!(!room
            .select_team(&second.session, second.player.team)
            .unwrap());
    }

    #[test]
    fn oracle_is_not_a_player_or_host_and_survives_reset() {
        let mut room = room(1, 1);
        let oracle = attach_oracle(&mut room, 90);
        assert_eq!(room.player_count(), 0);
        assert_eq!(room.host_player_id(), None);
        assert_eq!(room.authority_epoch(), 1);

        let player = join(&mut room, 1, "A", None);
        let scope = activate_one_player(&mut room, &player.session, &oracle);
        let result = BattleResult::new(BattleWinner::Team(Team::One), "elimination", None).unwrap();
        room.finish_round(scope, result, ReceiptPolicy::None)
            .unwrap();
        let next_scope = room.reset_round(scope).unwrap();

        assert!(room.oracle_connected());
        assert_eq!(room.player_count(), 1);
        assert_eq!(next_scope.round_id, scope.round_id + 1);
        assert_eq!(next_scope.epoch, 1);
        assert_eq!(room.phase(), RoomPhase::Waiting);
    }

    #[test]
    fn only_host_can_start_and_oracle_is_required() {
        let mut room = room(2, 2);
        let host = join(&mut room, 1, "Host", None);
        let guest = join(&mut room, 2, "Guest", None);
        assert_eq!(
            room.request_start(&guest.session).unwrap_err(),
            RoomError::HostOnly
        );
        assert_eq!(
            room.request_start(&host.session).unwrap_err(),
            RoomError::OracleRequired
        );
        attach_oracle(&mut room, 90);
        assert!(room.request_start(&host.session).is_ok());
    }

    #[test]
    fn every_player_and_the_oracle_are_separate_ready_barriers() {
        let mut room = room(2, 2);
        let oracle = attach_oracle(&mut room, 90);
        let first = join(&mut room, 1, "A", None);
        let second = join(&mut room, 2, "B", None);
        let start = room.request_start(&first.session).unwrap();

        let waiting = room.mark_player_ready(&first.session, start.scope).unwrap();
        assert_eq!(
            waiting,
            ReadyOutcome::Waiting {
                scope: start.scope,
                missing_players: vec![second.player.player_id],
                oracle_ready: false,
            }
        );
        assert!(matches!(
            room.mark_oracle_ready(&oracle, start.scope).unwrap(),
            ReadyOutcome::Waiting { .. }
        ));
        assert!(matches!(
            room.mark_player_ready(&second.session, start.scope)
                .unwrap(),
            ReadyOutcome::Activated { .. }
        ));
        assert_eq!(room.phase(), RoomPhase::Battle);
    }

    #[test]
    fn disconnecting_the_last_unready_player_opens_the_barrier() {
        let mut room = room(2, 2);
        let oracle = attach_oracle(&mut room, 90);
        let host = join(&mut room, 1, "Host", None);
        let blocker = join(&mut room, 2, "Blocker", None);
        let start = room.request_start(&host.session).unwrap();
        room.mark_player_ready(&host.session, start.scope).unwrap();
        room.mark_oracle_ready(&oracle, start.scope).unwrap();

        let outcome = room.disconnect_player(&blocker.session).unwrap();

        assert!(outcome.battle_activated);
        assert!(!outcome.round_reset);
        assert_eq!(room.phase(), RoomPhase::Battle);
        assert!(room
            .round_participants()
            .any(|participant| participant.player_id == blocker.player.player_id));
    }

    #[test]
    fn host_re_elects_to_the_lowest_connected_player() {
        let mut room = room(2, 2);
        let first = join(&mut room, 1, "A", None);
        let second = join(&mut room, 2, "B", None);
        let third = join(&mut room, 3, "C", None);

        let outcome = room.disconnect_player(&first.session).unwrap();

        assert_eq!(outcome.host_player_id, Some(second.player.player_id));
        assert_ne!(outcome.host_player_id, Some(third.player.player_id));
    }

    #[test]
    fn stale_round_or_epoch_cannot_cross_a_barrier_or_finish() {
        let mut room = room(1, 1);
        let oracle = attach_oracle(&mut room, 90);
        let player = join(&mut room, 1, "A", None);
        let start = room.request_start(&player.session).unwrap();
        let stale = SimulationScope {
            round_id: start.scope.round_id,
            epoch: start.scope.epoch - 1,
        };
        assert!(matches!(
            room.mark_player_ready(&player.session, stale),
            Err(RoomError::StaleScope { .. })
        ));
        room.mark_player_ready(&player.session, start.scope)
            .unwrap();
        room.mark_oracle_ready(&oracle, start.scope).unwrap();
        assert!(matches!(
            room.finish_round(
                stale,
                BattleResult::new(BattleWinner::Draw, "timeout", None).unwrap(),
                ReceiptPolicy::None,
            ),
            Err(RoomError::StaleScope { .. })
        ));
    }

    #[test]
    fn oracle_loss_fences_epoch_and_finishes_without_receipts() {
        let mut room = room(1, 1);
        let oracle = attach_oracle(&mut room, 90);
        let player = join(&mut room, 1, "A", None);
        let old_scope = activate_one_player(&mut room, &player.session, &oracle);

        let detached = room.detach_oracle(&oracle).unwrap();

        assert!(detached.round_failed);
        assert_eq!(detached.scope.round_id, old_scope.round_id);
        assert_eq!(detached.scope.epoch, old_scope.epoch + 1);
        assert_eq!(room.phase(), RoomPhase::Finished);
        assert_eq!(room.battle_result().unwrap().reason, "worker_disconnected");
        assert_eq!(room.receipt_ledger().count(), 0);
    }

    #[test]
    fn receipts_are_frozen_per_participant_and_acknowledged_by_owner() {
        let mut room = room(2, 2);
        let oracle = attach_oracle(&mut room, 90);
        let first = join(&mut room, 1, "A", None);
        let second = join(&mut room, 2, "B", None);
        let start = room.request_start(&first.session).unwrap();
        room.mark_player_ready(&first.session, start.scope).unwrap();
        room.mark_player_ready(&second.session, start.scope)
            .unwrap();
        room.mark_oracle_ready(&oracle, start.scope).unwrap();
        let payloads = BTreeMap::from([
            (first.player.player_id, json!({"xp": 100})),
            (second.player.player_id, json!({"xp": 50})),
        ]);
        let receipts = room
            .finish_round(
                start.scope,
                BattleResult::new(BattleWinner::Team(Team::One), "elimination", None).unwrap(),
                ReceiptPolicy::Participants(payloads),
            )
            .unwrap();
        assert_eq!(receipts.len(), 2);

        let first_receipt = room.next_receipt(&first.session).unwrap().unwrap();
        assert_eq!(first_receipt.payload, json!({"xp": 100}));
        assert_eq!(room.next_receipt(&first.session).unwrap(), None);
        assert_eq!(
            room.acknowledge_receipt(&second.session, &first_receipt.receipt_id)
                .unwrap_err(),
            RoomError::ReceiptNotOwned
        );
        room.acknowledge_receipt(&first.session, &first_receipt.receipt_id)
            .unwrap();
        assert_eq!(room.next_receipt(&first.session).unwrap(), None);
        assert_eq!(room.receipt_ledger().count(), 1);
    }

    #[test]
    fn unacknowledged_receipt_is_reoffered_only_after_reconnect() {
        let mut room = room(1, 1);
        let oracle = attach_oracle(&mut room, 90);
        let player = join(&mut room, 1, "A", None);
        let scope = activate_one_player(&mut room, &player.session, &oracle);
        room.finish_round(
            scope,
            BattleResult::new(BattleWinner::Draw, "timeout", None).unwrap(),
            ReceiptPolicy::Participants(BTreeMap::from([(
                player.player.player_id,
                json!({"xp": 0}),
            )])),
        )
        .unwrap();
        let receipt = room.next_receipt(&player.session).unwrap().unwrap();
        assert_eq!(room.next_receipt(&player.session).unwrap(), None);
        room.reset_round(scope).unwrap();
        room.disconnect_player(&player.session).unwrap();

        let mut reconnect = join_request(2, "A", None);
        reconnect.account_key = player.player.account_key.clone();
        let rejoined = room.join_player(endpoint(2), reconnect).unwrap();
        assert_eq!(
            room.next_receipt(&rejoined.session)
                .unwrap()
                .unwrap()
                .receipt_id,
            receipt.receipt_id
        );
    }

    #[test]
    fn receipt_payloads_are_validated_before_terminal_state_changes() {
        let mut room = room(1, 1);
        let oracle = attach_oracle(&mut room, 90);
        let player = join(&mut room, 1, "A", None);
        let scope = activate_one_player(&mut room, &player.session, &oracle);

        assert_eq!(
            room.finish_round(
                scope,
                BattleResult::new(BattleWinner::Draw, "timeout", None).unwrap(),
                ReceiptPolicy::Participants(BTreeMap::new()),
            )
            .unwrap_err(),
            RoomError::MissingReceiptPayload(player.player.player_id)
        );
        assert_eq!(room.phase(), RoomPhase::Battle);
        assert!(room.battle_result().is_none());
    }

    #[test]
    fn final_loading_player_disconnect_resets_the_round() {
        let mut room = room(1, 1);
        attach_oracle(&mut room, 90);
        let player = join(&mut room, 1, "A", None);
        let old_scope = room.request_start(&player.session).unwrap().scope;

        let outcome = room.disconnect_player(&player.session).unwrap();

        assert!(outcome.round_reset);
        assert_eq!(room.phase(), RoomPhase::Waiting);
        assert_eq!(room.round_id(), old_scope.round_id + 1);
        assert_eq!(room.authority_epoch(), 1);
    }
}
