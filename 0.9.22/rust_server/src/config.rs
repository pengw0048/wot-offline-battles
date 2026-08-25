use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::PathBuf;
use thiserror::Error;

pub const DEFAULT_HOST: &str = "0.0.0.0";
pub const LOOPBACK_HOST: &str = "127.0.0.1";
pub const DEFAULT_PORT: u16 = 28_782;
pub const DEFAULT_MAX_PLAYERS: usize = 30;
pub const DEFAULT_TEAM_SIZE: usize = 15;
pub const DEFAULT_MAP: &str = "server_random";

pub const LOOPBACK_ONLY_ENV: &str = "WOT_0922_LOOPBACK_ONLY";
pub const TEAM_SIZE_ENV: &str = "WOT_0922_TEAM_SIZE";
pub const TEAM1_SIZE_ENV: &str = "WOT_0922_TEAM1_SIZE";
pub const TEAM2_SIZE_ENV: &str = "WOT_0922_TEAM2_SIZE";
pub const BOT_LINEUP_ENV: &str = "WOT_0922_BOT_LINEUP";
pub const VEHICLE_OVERLAY_ROOT_ENV: &str = "WOT_0922_VEHICLE_OVERLAY_ROOT";

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BotLineupEntry {
    pub team: u8,
    pub slot: u8,
    pub vehicle: String,
}

impl BotLineupEntry {
    pub fn is_valid(&self) -> bool {
        (1..=2).contains(&self.team) && self.slot <= 14 && valid_vehicle_name(&self.vehicle)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub map: String,
    pub max_players: usize,
    pub team_sizes: [usize; 2],
    pub bot_lineup: Vec<BotLineupEntry>,
    pub receipt_state_path: Option<PathBuf>,
    pub vehicle_overlay_root: Option<PathBuf>,
    /// Selected-map v2 graphs. Production derives this from the launcher game
    /// root; tests and offline tools may point directly at a released graph set.
    pub navigation_graph_directory: Option<PathBuf>,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            host: DEFAULT_HOST.to_owned(),
            port: DEFAULT_PORT,
            map: DEFAULT_MAP.to_owned(),
            max_players: DEFAULT_MAX_PLAYERS,
            team_sizes: [DEFAULT_TEAM_SIZE, DEFAULT_TEAM_SIZE],
            bot_lineup: Vec::new(),
            receipt_state_path: None,
            vehicle_overlay_root: None,
            navigation_graph_directory: None,
        }
    }
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum ConfigError {
    #[error("missing value after {option}")]
    MissingValue { option: String },
    #[error("unknown server option {option}")]
    UnknownOption { option: String },
    #[error("{name} must be an integer in {minimum}..={maximum}")]
    InvalidInteger {
        name: String,
        minimum: u64,
        maximum: u64,
    },
    #[error("host must not be empty")]
    EmptyHost,
    #[error("map must not be empty or exceed 96 bytes")]
    InvalidMap,
    #[error("{BOT_LINEUP_ENV} must be a unique list of fully qualified team slots")]
    InvalidBotLineup,
}

impl ServerConfig {
    pub fn from_process(args: impl IntoIterator<Item = String>) -> Result<Self, ConfigError> {
        Self::parse(args, std::env::vars().collect())
    }

    pub fn parse(
        args: impl IntoIterator<Item = String>,
        environment: BTreeMap<String, String>,
    ) -> Result<Self, ConfigError> {
        let mut config = Self::default();
        if environment
            .get(LOOPBACK_ONLY_ENV)
            .is_some_and(|value| value == "1")
        {
            config.host = LOOPBACK_HOST.to_owned();
        }
        let legacy_team_size = environment
            .get(TEAM_SIZE_ENV)
            .map(|value| parse_integer(value, TEAM_SIZE_ENV, 1, 15))
            .transpose()?
            .unwrap_or(DEFAULT_TEAM_SIZE as u64) as usize;
        config.team_sizes = [legacy_team_size, legacy_team_size];
        for (index, name) in [TEAM1_SIZE_ENV, TEAM2_SIZE_ENV].into_iter().enumerate() {
            if let Some(value) = environment.get(name) {
                config.team_sizes[index] = parse_integer(value, name, 1, 15)? as usize;
            }
        }
        if let Some(value) = environment.get(BOT_LINEUP_ENV) {
            config.bot_lineup = parse_bot_lineup(value)?;
        }
        config.vehicle_overlay_root = environment
            .get(VEHICLE_OVERLAY_ROOT_ENV)
            .filter(|value| !value.is_empty())
            .map(PathBuf::from);
        config.navigation_graph_directory = config.vehicle_overlay_root.as_ref().map(|root| {
            root.join("mods")
                .join("configs")
                .join("offline_lan_0922")
                .join("navgraphs")
        });

        let mut args = args.into_iter();
        while let Some(option) = args.next() {
            let mut next_value = || {
                args.next().ok_or_else(|| ConfigError::MissingValue {
                    option: option.clone(),
                })
            };
            match option.as_str() {
                "--host" => config.host = next_value()?,
                "--port" => {
                    config.port =
                        parse_integer(&next_value()?, "--port", 1, u16::MAX as u64)? as u16;
                }
                "--map" => config.map = next_value()?,
                "--max-players" => {
                    config.max_players =
                        parse_integer(&next_value()?, "--max-players", 1, 30)? as usize;
                }
                "--team-size" => {
                    let size = parse_integer(&next_value()?, "--team-size", 1, 15)? as usize;
                    config.team_sizes = [size, size];
                }
                "--team1-size" => {
                    config.team_sizes[0] =
                        parse_integer(&next_value()?, "--team1-size", 1, 15)? as usize;
                }
                "--team2-size" => {
                    config.team_sizes[1] =
                        parse_integer(&next_value()?, "--team2-size", 1, 15)? as usize;
                }
                "--receipt-state" => {
                    config.receipt_state_path = Some(PathBuf::from(next_value()?));
                }
                _ => return Err(ConfigError::UnknownOption { option }),
            }
        }
        if config.host.trim().is_empty() {
            return Err(ConfigError::EmptyHost);
        }
        if config.map.is_empty() || config.map.len() > 96 {
            return Err(ConfigError::InvalidMap);
        }
        Ok(config)
    }
}

fn parse_bot_lineup(encoded: &str) -> Result<Vec<BotLineupEntry>, ConfigError> {
    let value: Value = serde_json::from_str(encoded).map_err(|_| ConfigError::InvalidBotLineup)?;
    let rows = value.as_array().ok_or(ConfigError::InvalidBotLineup)?;
    if rows.len() > 30 {
        return Err(ConfigError::InvalidBotLineup);
    }
    let mut result = Vec::with_capacity(rows.len());
    let mut seen = std::collections::BTreeSet::new();
    for row in rows {
        let entry: BotLineupEntry =
            serde_json::from_value(row.clone()).map_err(|_| ConfigError::InvalidBotLineup)?;
        if !entry.is_valid() || !seen.insert((entry.team, entry.slot)) {
            return Err(ConfigError::InvalidBotLineup);
        }
        result.push(entry);
    }
    result.sort_by_key(|entry| (entry.team, entry.slot));
    Ok(result)
}

fn valid_vehicle_name(value: &str) -> bool {
    if value.len() > 96 || value.matches(':').count() != 1 {
        return false;
    }
    let Some((nation, vehicle)) = value.split_once(':') else {
        return false;
    };
    let mut nation_chars = nation.chars();
    let nation_valid = nation_chars
        .next()
        .is_some_and(|value| value.is_ascii_lowercase())
        && nation_chars
            .all(|value| value.is_ascii_lowercase() || value.is_ascii_digit() || value == '_');
    let mut vehicle_chars = vehicle.chars();
    let vehicle_valid = vehicle_chars
        .next()
        .is_some_and(|value| value.is_ascii_alphanumeric())
        && vehicle_chars
            .all(|value| value.is_ascii_alphanumeric() || matches!(value, '_' | '.' | '-'));
    nation_valid && vehicle_valid
}

fn parse_integer(value: &str, name: &str, minimum: u64, maximum: u64) -> Result<u64, ConfigError> {
    value
        .parse::<u64>()
        .ok()
        .filter(|value| (minimum..=maximum).contains(value))
        .ok_or_else(|| ConfigError::InvalidInteger {
            name: name.to_owned(),
            minimum,
            maximum,
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(values: &[(&str, &str)]) -> BTreeMap<String, String> {
        values
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect()
    }

    #[test]
    fn launcher_environment_preserves_team_and_loopback_contracts() {
        let config = ServerConfig::parse(
            Vec::<String>::new(),
            env(&[
                (LOOPBACK_ONLY_ENV, "1"),
                (TEAM_SIZE_ENV, "12"),
                (TEAM2_SIZE_ENV, "9"),
                (VEHICLE_OVERLAY_ROOT_ENV, "C:\\Games\\World_of_Tanks"),
            ]),
        )
        .unwrap();
        assert_eq!(config.host, LOOPBACK_HOST);
        assert_eq!(config.team_sizes, [12, 9]);
        assert_eq!(
            config.vehicle_overlay_root,
            Some(PathBuf::from("C:\\Games\\World_of_Tanks"))
        );
        assert_eq!(
            config.navigation_graph_directory,
            Some(
                PathBuf::from("C:\\Games\\World_of_Tanks")
                    .join("mods")
                    .join("configs")
                    .join("offline_lan_0922")
                    .join("navgraphs")
            )
        );
    }

    #[test]
    fn explicit_cli_overrides_environment() {
        let config = ServerConfig::parse(
            [
                "--host",
                "192.168.1.5",
                "--port",
                "29000",
                "--team-size",
                "10",
                "--team1-size",
                "8",
                "--map",
                "01_karelia",
            ]
            .into_iter()
            .map(str::to_owned),
            env(&[(TEAM_SIZE_ENV, "15")]),
        )
        .unwrap();
        assert_eq!(config.host, "192.168.1.5");
        assert_eq!(config.port, 29_000);
        assert_eq!(config.team_sizes, [8, 10]);
        assert_eq!(config.map, "01_karelia");
    }

    #[test]
    fn capacities_stay_within_existing_protocol_bounds() {
        assert!(matches!(
            ServerConfig::parse(Vec::<String>::new(), env(&[(TEAM1_SIZE_ENV, "16")])),
            Err(ConfigError::InvalidInteger { .. })
        ));
        assert!(matches!(
            ServerConfig::parse(
                ["--max-players".to_owned(), "0".to_owned()],
                BTreeMap::new()
            ),
            Err(ConfigError::InvalidInteger { .. })
        ));
    }

    #[test]
    fn launcher_exact_lineup_is_strict_sorted_and_unique() {
        let config = ServerConfig::parse(
            Vec::<String>::new(),
            env(&[(
                BOT_LINEUP_ENV,
                r#"[{"team":2,"slot":3,"vehicle":"germany:G12_Ltraktor"},{"team":1,"slot":0,"vehicle":"ussr:R11_MS-1"}]"#,
            )]),
        )
        .unwrap();
        assert_eq!(
            config.bot_lineup,
            vec![
                BotLineupEntry {
                    team: 1,
                    slot: 0,
                    vehicle: "ussr:R11_MS-1".to_owned(),
                },
                BotLineupEntry {
                    team: 2,
                    slot: 3,
                    vehicle: "germany:G12_Ltraktor".to_owned(),
                },
            ]
        );
    }

    #[test]
    fn launcher_exact_lineup_rejects_ambiguous_rows() {
        for encoded in [
            r#"[{"team":2,"slot":3,"vehicle":"G12_Ltraktor"}]"#,
            r#"[{"team":2,"slot":3,"vehicle":"germany:G12_Ltraktor"},{"team":2,"slot":3,"vehicle":"ussr:R11_MS-1"}]"#,
            r#"[{"team":true,"slot":3,"vehicle":"germany:G12_Ltraktor"}]"#,
            r#"[{"team":2,"slot":15,"vehicle":"germany:G12_Ltraktor"}]"#,
            r#"[{"team":2,"slot":3,"vehicle":"germany:G12_Ltraktor","extra":1}]"#,
        ] {
            assert_eq!(
                ServerConfig::parse(Vec::<String>::new(), env(&[(BOT_LINEUP_ENV, encoded)])),
                Err(ConfigError::InvalidBotLineup)
            );
        }
    }
}
