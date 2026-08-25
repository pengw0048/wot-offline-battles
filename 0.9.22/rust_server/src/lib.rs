//! Core building blocks for the future Rust LAN authority.

pub mod authority_runtime;
pub mod battle;
pub mod battle_loop;
pub mod bot_sim;
pub mod client_replication;
pub mod clock;
pub mod combat;
pub mod combat_rules;
pub mod config;
pub mod contact_authority;
pub mod critical_damage;
pub mod descriptor;
pub mod descriptor_exchange;
pub mod destructible;
pub mod input;
pub mod lan;
pub mod lineup;
pub mod navgraph;
pub mod net;
pub mod oracle;
pub mod oracle_adapter;
pub mod planner;
pub mod player_ammo;
pub mod player_environment;
pub mod player_equipment;
pub mod player_fire_clock;
pub mod projectile;
pub mod projectile_sim;
pub mod protocol;
pub mod ram;
pub mod receipt_store;
pub mod replication;
pub mod result;
pub mod rewards;
pub mod room;
pub mod rules;
pub mod server;
pub mod sim;
pub mod spotting;
pub mod statistics;
pub mod tactical_maps;
pub mod trace;
pub mod transport;
pub mod validator;
pub mod vehicle_overlay;
pub mod windows_firewall;
pub mod wire;

pub use clock::{FixedStepClock, FixedStepSchedule, TickObservation, TickSlot};
pub use oracle::{
    AppliedOracleBatch, OracleBroker, OracleBrokerError, OracleReplyDisposition,
    OracleReplyDropReason, OracleRequestRegistration, OracleTickOutcome, TimedOutOracleBatch,
};
pub use protocol::*;
pub use trace::{validate_reader, TraceRecord, TraceSummary, TraceValidator};
pub use validator::{
    validate_oracle_v1_reply, validate_oracle_v1_request, OracleV1ValidationError, OracleValidator,
    ValidationError, ValidationOutcome,
};
