//! Typed tactical-route catalog for the pinned #1513 map pool.
//!
//! The static values in this module are a direct transcription of the latest
//! `gui.mods.offline_lan_0922.ai.maps.TACTICAL_MAPS` objects after reviewed
//! route and map-strategy overlays. No Python execution or runtime route
//! synthesis is required by the Rust server.

use std::collections::BTreeSet;
use std::fmt;

pub const TEAM_ONE: u8 = 1;
pub const TEAM_TWO: u8 = 2;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TacticalPoint {
    pub x: f64,
    pub z: f64,
}

impl TacticalPoint {
    fn finite(self) -> bool {
        self.x.is_finite() && self.z.is_finite()
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TacticalBounds {
    pub min_x: f64,
    pub min_z: f64,
    pub max_x: f64,
    pub max_z: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TacticalWaypoint {
    pub x: f64,
    pub z: f64,
    /// The final Python tuple's third component. It marks a tactical gate or
    /// hold point and is deliberately kept separate from route-level `hold`.
    pub hold: bool,
}

impl TacticalWaypoint {
    fn finite(self) -> bool {
        self.x.is_finite() && self.z.is_finite()
    }
}

/// Historical route-level `hold` metadata has two Python shapes in the final
/// catalog. It is retained losslessly even though locomotion consumes the
/// waypoint-level flag.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum RouteHold {
    Unspecified,
    Enabled,
    Point(TacticalWaypoint),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RoleWeights {
    pub brawler: f64,
    pub support: f64,
    pub flanker: f64,
    pub sniper: f64,
    pub scout: f64,
    pub artillery: f64,
}

impl RoleWeights {
    pub fn values(self) -> [f64; 6] {
        [
            self.brawler,
            self.support,
            self.flanker,
            self.sniper,
            self.scout,
            self.artillery,
        ]
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TacticalRoute {
    pub id: &'static str,
    pub capacity: usize,
    pub risk: f64,
    pub role_weights: RoleWeights,
    pub hold: RouteHold,
    pub waypoints: &'static [TacticalWaypoint],
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MatchedTacticalRoute {
    pub map: &'static TacticalMap,
    pub team: u8,
    pub route: &'static TacticalRoute,
}

impl MatchedTacticalRoute {
    pub fn enemy_base(self) -> TacticalPoint {
        self.map.bases[usize::from(self.team == TEAM_ONE)]
    }

    /// Latest-main adds class affinity only to Karelia's reviewed strategy.
    /// The baked waypoints remain authoritative for locomotion; this method
    /// overlays the strategy metadata that is deliberately absent from the
    /// older baked-route payload.
    pub fn class_affinity(self, class_tag: &str) -> Option<f64> {
        if self.map.name != "01_karelia" {
            return None;
        }
        match (self.route.id, class_tag) {
            ("west_ridge", "heavyTank") => Some(0.35),
            ("west_ridge", "mediumTank") => Some(1.00),
            ("west_ridge", "lightTank") => Some(0.10),
            ("west_ridge", "AT-SPG") => Some(0.85),
            ("west_ridge", "SPG") => Some(0.00),
            ("middle_road", "heavyTank") => Some(0.02),
            ("middle_road", "mediumTank") => Some(0.30),
            ("middle_road", "lightTank") => Some(1.00),
            ("middle_road", "AT-SPG") => Some(0.25),
            ("middle_road", "SPG") => Some(0.00),
            ("east_shelf", "heavyTank") => Some(1.00),
            ("east_shelf", "mediumTank") => Some(0.70),
            ("east_shelf", "lightTank") => Some(0.12),
            ("east_shelf", "AT-SPG") => Some(0.35),
            ("east_shelf", "SPG") => Some(0.00),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TacticalMap {
    pub name: &'static str,
    pub bounds: TacticalBounds,
    /// Index zero is team 1 and index one is team 2.
    pub bases: [TacticalPoint; 2],
    /// Index zero is team 1 and index one is team 2.
    pub team_routes: [&'static [TacticalRoute]; 2],
    pub annotation_confidence: Option<&'static str>,
}

impl TacticalMap {
    pub fn base(&self, team: u8) -> Option<TacticalPoint> {
        team_index(team).map(|index| self.bases[index])
    }

    pub fn routes(&self, team: u8) -> Option<&'static [TacticalRoute]> {
        team_index(team).map(|index| self.team_routes[index])
    }

    pub fn total_capacity(&self, team: u8) -> Option<usize> {
        self.routes(team)?
            .iter()
            .try_fold(0usize, |total, route| total.checked_add(route.capacity))
    }

    /// Deterministically assigns a roster slot using route capacities as one
    /// weighted cycle. Slots beyond the advertised capacity repeat the same
    /// cycle, matching the Python planner's fail-open behavior once every lane
    /// is full while remaining independent of registration order.
    pub fn route_for_slot(&self, team: u8, slot: usize) -> Option<&'static TacticalRoute> {
        let routes = self.routes(team)?;
        let total = routes
            .iter()
            .try_fold(0usize, |sum, route| sum.checked_add(route.capacity))?;
        if total == 0 {
            return None;
        }
        let mut ticket = slot % total;
        for route in routes {
            if ticket < route.capacity {
                return Some(route);
            }
            ticket -= route.capacity;
        }
        None
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CatalogValidationError {
    pub map: &'static str,
    pub team: Option<u8>,
    pub route: Option<&'static str>,
    pub reason: &'static str,
}

impl fmt::Display for CatalogValidationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "invalid tactical catalog at map {:?}", self.map)?;
        if let Some(team) = self.team {
            write!(formatter, ", team {team}")?;
        }
        if let Some(route) = self.route {
            write!(formatter, ", route {route:?}")?;
        }
        write!(formatter, ": {}", self.reason)
    }
}

impl std::error::Error for CatalogValidationError {}

pub fn tactical_map(map_name: &str) -> Option<&'static TacticalMap> {
    let short = map_name
        .rsplit(|character| matches!(character, '/' | '\\'))
        .next()
        .unwrap_or("");
    let normalized = if short
        .get(short.len().saturating_sub(4)..)
        .is_some_and(|suffix| suffix.eq_ignore_ascii_case(".xml"))
    {
        &short[..short.len() - 4]
    } else {
        short
    };
    TACTICAL_MAPS
        .iter()
        .find(|map| map.name.eq_ignore_ascii_case(normalized))
}

pub fn routes_for(map_name: &str, team: u8) -> Option<&'static [TacticalRoute]> {
    tactical_map(map_name)?.routes(team)
}

pub fn route_for_slot(map_name: &str, team: u8, slot: usize) -> Option<&'static TacticalRoute> {
    tactical_map(map_name)?.route_for_slot(team, slot)
}

/// Recover the typed catalog record after a route has crossed the JSON
/// planner boundary. Matching every baked waypoint avoids treating a reused
/// route id from another map as the same strategy lane.
pub fn match_route(
    team: u8,
    route_id: &str,
    waypoints: &[(f64, f64)],
) -> Option<MatchedTacticalRoute> {
    let mut matched = None;
    for map in TACTICAL_MAPS {
        let routes = map.routes(team)?;
        for route in routes {
            if route.id != route_id
                || route.waypoints.len() != waypoints.len()
                || !route
                    .waypoints
                    .iter()
                    .zip(waypoints)
                    .all(|(expected, actual)| {
                        (expected.x - actual.0).abs() <= 0.001
                            && (expected.z - actual.1).abs() <= 0.001
                    })
            {
                continue;
            }
            if matched.is_some() {
                return None;
            }
            matched = Some(MatchedTacticalRoute { map, team, route });
        }
    }
    matched
}

pub fn map_names() -> impl ExactSizeIterator<Item = &'static str> {
    TACTICAL_MAPS.iter().map(|map| map.name)
}

pub fn validate_catalog() -> Result<(), CatalogValidationError> {
    if TACTICAL_MAPS.len() != TACTICAL_MAP_NAMES.len() {
        return Err(CatalogValidationError {
            map: "",
            team: None,
            route: None,
            reason: "map-pool cardinality mismatch",
        });
    }
    let mut map_names = BTreeSet::new();
    for (map, expected_name) in TACTICAL_MAPS.iter().zip(TACTICAL_MAP_NAMES) {
        if map.name != *expected_name {
            return Err(validation_error(
                map,
                None,
                None,
                "map-pool order or identity mismatch",
            ));
        }
        if map.name.is_empty()
            || map.name.len() > 64
            || !map.name.is_ascii()
            || map.name.chars().any(char::is_control)
            || !map_names.insert(map.name)
        {
            return Err(validation_error(
                map,
                None,
                None,
                "invalid or duplicate map name",
            ));
        }
        if ![
            map.bounds.min_x,
            map.bounds.min_z,
            map.bounds.max_x,
            map.bounds.max_z,
        ]
        .into_iter()
        .all(f64::is_finite)
            || map.bounds.min_x >= map.bounds.max_x
            || map.bounds.min_z >= map.bounds.max_z
            || !map.bases.into_iter().all(TacticalPoint::finite)
        {
            return Err(validation_error(map, None, None, "invalid bounds or base"));
        }
        if map
            .annotation_confidence
            .is_some_and(|value| value.is_empty() || value.len() > 64 || !value.is_ascii())
        {
            return Err(validation_error(
                map,
                None,
                None,
                "invalid annotation confidence",
            ));
        }
        for team in [TEAM_ONE, TEAM_TWO] {
            let routes = map.routes(team).expect("canonical team");
            if routes.is_empty() {
                return Err(validation_error(map, Some(team), None, "empty team routes"));
            }
            let mut route_ids = BTreeSet::new();
            let mut total_capacity = 0usize;
            for route in routes {
                if route.id.is_empty()
                    || route.id.len() > 64
                    || !route.id.is_ascii()
                    || route.id.chars().any(char::is_control)
                    || !route_ids.insert(route.id)
                {
                    return Err(validation_error(
                        map,
                        Some(team),
                        Some(route),
                        "invalid or duplicate route id",
                    ));
                }
                if route.capacity == 0 || route.capacity > 15 {
                    return Err(validation_error(
                        map,
                        Some(team),
                        Some(route),
                        "route capacity is outside 1..=15",
                    ));
                }
                total_capacity = total_capacity.checked_add(route.capacity).ok_or_else(|| {
                    validation_error(map, Some(team), Some(route), "team capacity overflow")
                })?;
                if !route.risk.is_finite()
                    || !(0.0..=1.0).contains(&route.risk)
                    || !route
                        .role_weights
                        .values()
                        .into_iter()
                        .all(|weight| weight.is_finite() && (0.0..=1.0).contains(&weight))
                {
                    return Err(validation_error(
                        map,
                        Some(team),
                        Some(route),
                        "invalid risk or role weight",
                    ));
                }
                if route.waypoints.is_empty()
                    || route.waypoints.len() > 32
                    || !route
                        .waypoints
                        .iter()
                        .copied()
                        .all(TacticalWaypoint::finite)
                {
                    return Err(validation_error(
                        map,
                        Some(team),
                        Some(route),
                        "empty or non-finite waypoint list",
                    ));
                }
                if matches!(route.hold, RouteHold::Point(point) if !point.finite()) {
                    return Err(validation_error(
                        map,
                        Some(team),
                        Some(route),
                        "non-finite route hold point",
                    ));
                }
            }
            if total_capacity == 0 {
                return Err(validation_error(
                    map,
                    Some(team),
                    None,
                    "zero team capacity",
                ));
            }
        }
        let team_one = map.routes(TEAM_ONE).expect("canonical team");
        let team_two = map.routes(TEAM_TWO).expect("canonical team");
        if team_one.len() != team_two.len() {
            return Err(validation_error(
                map,
                None,
                None,
                "team route count mismatch",
            ));
        }
        for (first, second) in team_one.iter().zip(team_two) {
            if first.id != second.id
                || first.capacity != second.capacity
                || first.risk != second.risk
                || first.role_weights != second.role_weights
            {
                return Err(validation_error(
                    map,
                    None,
                    Some(first),
                    "team route metadata mismatch",
                ));
            }
        }
    }
    Ok(())
}

fn team_index(team: u8) -> Option<usize> {
    match team {
        TEAM_ONE => Some(0),
        TEAM_TWO => Some(1),
        _ => None,
    }
}

fn validation_error(
    map: &TacticalMap,
    team: Option<u8>,
    route: Option<&TacticalRoute>,
    reason: &'static str,
) -> CatalogValidationError {
    CatalogValidationError {
        map: map.name,
        team,
        route: route.map(|route| route.id),
        reason,
    }
}

static ROUTES_01_KARELIA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_ridge",
        capacity: 5,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 0.35,
            support: 0.78,
            flanker: 0.82,
            sniper: 0.72,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -392.0,
                z: -372.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -454.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -475.0,
                z: -199.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -462.0,
                z: -9.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -475.0,
                z: 195.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -458.0,
                z: 292.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -388.0,
                z: 402.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -305.0,
                z: 450.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -88.0,
                z: 482.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 349.0,
                z: 475.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "middle_road",
        capacity: 4,
        risk: 0.56,
        role_weights: RoleWeights {
            brawler: 0.02,
            support: 0.28,
            flanker: 0.50,
            sniper: 0.22,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -345.0,
                z: -312.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 312.0,
                z: 345.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_shelf",
        capacity: 6,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.72,
            flanker: 0.62,
            sniper: 0.25,
            scout: 0.12,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -302.0,
                z: -442.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -121.0,
                z: -482.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 116.0,
                z: -403.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 336.0,
                z: -209.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 486.0,
                z: 181.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 476.0,
                z: 282.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_01_KARELIA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_ridge",
        capacity: 5,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 0.35,
            support: 0.78,
            flanker: 0.82,
            sniper: 0.72,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 349.0,
                z: 475.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -88.0,
                z: 482.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -305.0,
                z: 450.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -388.0,
                z: 402.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -458.0,
                z: 292.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -475.0,
                z: 195.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -462.0,
                z: -9.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -475.0,
                z: -199.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -454.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -392.0,
                z: -372.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "middle_road",
        capacity: 4,
        risk: 0.56,
        role_weights: RoleWeights {
            brawler: 0.02,
            support: 0.28,
            flanker: 0.50,
            sniper: 0.22,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 312.0,
                z: 345.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -345.0,
                z: -312.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "east_shelf",
        capacity: 6,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.72,
            flanker: 0.62,
            sniper: 0.25,
            scout: 0.12,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 476.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 486.0,
                z: 181.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 336.0,
                z: -209.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 116.0,
                z: -403.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -121.0,
                z: -482.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -302.0,
                z: -442.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_02_MALINOVKA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_lake_road",
        capacity: 5,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.22,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -21.0,
                z: -442.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -201.0,
                z: -417.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -355.0,
                z: -269.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -422.0,
                z: -126.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_field",
        capacity: 4,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.86,
            flanker: 0.46,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.18,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 242.0,
                z: -199.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 149.0,
                z: 138.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 52.0,
                z: 216.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -178.0,
                z: 231.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_hill_loop",
        capacity: 5,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.42,
            scout: 0.86,
            artillery: 0.02,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 302.0,
                z: -257.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 427.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 469.0,
                z: 55.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 472.0,
                z: 457.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 176.0,
                z: 482.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 9.0,
                z: 428.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -205.0,
                z: 293.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_02_MALINOVKA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_lake_road",
        capacity: 5,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.22,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -422.0,
                z: -126.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -355.0,
                z: -269.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -201.0,
                z: -417.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -21.0,
                z: -442.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_field",
        capacity: 4,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.86,
            flanker: 0.46,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.18,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -178.0,
                z: 231.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 52.0,
                z: 216.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 149.0,
                z: 138.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 242.0,
                z: -199.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_hill_loop",
        capacity: 5,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.42,
            scout: 0.86,
            artillery: 0.02,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -205.0,
                z: 293.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 9.0,
                z: 428.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 176.0,
                z: 482.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 472.0,
                z: 457.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 469.0,
                z: 55.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 427.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 302.0,
                z: -257.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_04_HIMMELSDORF_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "banana",
        capacity: 6,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.58,
            flanker: 0.3,
            sniper: 0.1,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 190.0,
                z: -74.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 182.0,
                z: -10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 154.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 102.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: 302.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "hill",
        capacity: 4,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.35,
            support: 0.58,
            flanker: 1.0,
            sniper: 0.18,
            scout: 0.72,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 273.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: -186.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 358.0,
                z: -50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: 50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: 178.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 374.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 158.0,
                z: 294.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 4,
        risk: 0.42,
        role_weights: RoleWeights {
            brawler: 0.18,
            support: 0.62,
            flanker: 0.58,
            sniper: 1.0,
            scout: 0.88,
            artillery: 0.12,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -216.0,
                z: -171.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -241.0,
                z: 81.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -221.0,
                z: 191.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -152.0,
                z: 249.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -109.0,
                z: 314.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rear_guard",
        capacity: 2,
        risk: 0.08,
        role_weights: RoleWeights {
            brawler: 0.0,
            support: 0.18,
            flanker: 0.0,
            sniper: 0.28,
            scout: 0.0,
            artillery: 1.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[TacticalWaypoint {
            x: -80.0,
            z: -270.0,
            hold: true,
        }],
    },
];

static ROUTES_04_HIMMELSDORF_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "banana",
        capacity: 6,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.58,
            flanker: 0.3,
            sniper: 0.1,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 138.0,
                z: 302.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 102.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 154.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 182.0,
                z: -10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: -74.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "hill",
        capacity: 4,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.35,
            support: 0.58,
            flanker: 1.0,
            sniper: 0.18,
            scout: 0.72,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 158.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 374.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: 178.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: 50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 358.0,
                z: -50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: -186.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 273.0,
                z: -282.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 4,
        risk: 0.42,
        role_weights: RoleWeights {
            brawler: 0.18,
            support: 0.62,
            flanker: 0.58,
            sniper: 1.0,
            scout: 0.88,
            artillery: 0.12,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -109.0,
                z: 314.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -152.0,
                z: 249.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -221.0,
                z: 191.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -241.0,
                z: 81.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -216.0,
                z: -171.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rear_guard",
        capacity: 2,
        risk: 0.08,
        role_weights: RoleWeights {
            brawler: 0.0,
            support: 0.18,
            flanker: 0.0,
            sniper: 0.28,
            scout: 0.0,
            artillery: 1.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[TacticalWaypoint {
            x: 45.0,
            z: 270.0,
            hold: true,
        }],
    },
];

static ROUTES_05_PROHOROVKA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_ridge",
        capacity: 4,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -282.0,
                z: 416.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -359.0,
                z: 292.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -372.0,
                z: 81.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -355.0,
                z: -326.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -308.0,
                z: -386.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_field",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -13.0,
                z: 258.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 13.0,
                z: 168.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 69.0,
                z: -199.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail_line",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 99.0,
                z: 367.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 272.0,
                z: 125.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 419.0,
                z: 73.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 478.0,
                z: -35.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 496.0,
                z: -316.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 203.0,
                z: -337.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: -371.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_05_PROHOROVKA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_ridge",
        capacity: 4,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -308.0,
                z: -386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -355.0,
                z: -326.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -372.0,
                z: 81.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -359.0,
                z: 292.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -282.0,
                z: 416.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_field",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 69.0,
                z: -199.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 13.0,
                z: 168.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -13.0,
                z: 258.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail_line",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 142.0,
                z: -371.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 203.0,
                z: -337.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 496.0,
                z: -316.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 478.0,
                z: -35.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 419.0,
                z: 73.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 272.0,
                z: 125.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 99.0,
                z: 367.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_06_ENSK_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_city",
        capacity: 7,
        risk: 0.64,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -108.0,
                z: 170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -167.0,
                z: 89.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -165.0,
                z: 23.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -184.0,
                z: -3.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -185.0,
                z: -40.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -178.0,
                z: -165.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -85.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -41.0,
                z: -224.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_field",
        capacity: 7,
        risk: 0.73,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 113.0,
                z: 228.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 183.0,
                z: 177.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 227.0,
                z: 41.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 212.0,
                z: -131.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 192.0,
                z: -187.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 79.0,
                z: -228.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_06_ENSK_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_city",
        capacity: 7,
        risk: 0.64,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -41.0,
                z: -224.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -85.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -178.0,
                z: -165.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -185.0,
                z: -40.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -184.0,
                z: -3.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -165.0,
                z: 23.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -167.0,
                z: 89.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: 170.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_field",
        capacity: 7,
        risk: 0.73,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 79.0,
                z: -228.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 192.0,
                z: -187.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 212.0,
                z: -131.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 227.0,
                z: 41.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 183.0,
                z: 177.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 113.0,
                z: 228.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_07_LAKEVILLE_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_valley",
        capacity: 5,
        risk: 0.63,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -292.0,
                z: 268.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -310.0,
                z: 105.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -294.0,
                z: -44.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -270.0,
                z: -103.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -296.0,
                z: -223.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -302.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -230.0,
                z: -290.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "lake_road",
        capacity: 4,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -112.0,
                z: 260.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -96.0,
                z: 225.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -94.0,
                z: 97.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: 44.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -88.0,
                z: -12.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -84.0,
                z: -92.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -105.0,
                z: -212.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_town",
        capacity: 5,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 266.0,
                z: 265.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 335.0,
                z: 193.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 391.0,
                z: -60.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 349.0,
                z: -237.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 313.0,
                z: -303.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 274.0,
                z: -333.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 98.0,
                z: -343.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_07_LAKEVILLE_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_valley",
        capacity: 5,
        risk: 0.63,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -230.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -302.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -296.0,
                z: -223.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -270.0,
                z: -103.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -294.0,
                z: -44.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -310.0,
                z: 105.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -292.0,
                z: 268.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "lake_road",
        capacity: 4,
        risk: 0.62,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -105.0,
                z: -212.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -84.0,
                z: -92.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -88.0,
                z: -12.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: 44.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -94.0,
                z: 97.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -96.0,
                z: 225.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -112.0,
                z: 260.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_town",
        capacity: 5,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 98.0,
                z: -343.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 274.0,
                z: -333.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 313.0,
                z: -303.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 349.0,
                z: -237.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 391.0,
                z: -60.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 335.0,
                z: 193.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 266.0,
                z: 265.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_08_RUINBERG_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_city",
        capacity: 6,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -234.0,
                z: 140.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -305.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -330.0,
                z: -20.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -297.0,
                z: -73.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: -181.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -81.0,
                z: -223.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_streets",
        capacity: 4,
        risk: 0.67,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 34.0,
                z: 185.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 64.0,
                z: 81.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 84.0,
                z: -44.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 71.0,
                z: -119.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 31.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -17.0,
                z: -267.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_fields",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 138.0,
                z: 295.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 317.0,
                z: 248.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 367.0,
                z: 73.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 362.0,
                z: -130.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 346.0,
                z: -194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 261.0,
                z: -243.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 103.0,
                z: -255.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_08_RUINBERG_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_city",
        capacity: 6,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -81.0,
                z: -223.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: -181.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -297.0,
                z: -73.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -330.0,
                z: -20.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -305.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -234.0,
                z: 140.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_streets",
        capacity: 4,
        risk: 0.67,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -17.0,
                z: -267.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 31.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 71.0,
                z: -119.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 84.0,
                z: -44.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 64.0,
                z: 81.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: 185.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_fields",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 103.0,
                z: -255.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 261.0,
                z: -243.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: -194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 362.0,
                z: -130.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 367.0,
                z: 73.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 317.0,
                z: 248.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: 295.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_10_HILLS_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "southwest_road",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 20.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -76.0,
                z: -192.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -351.0,
                z: -100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -354.0,
                z: 57.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -324.0,
                z: 143.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -228.0,
                z: 239.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_hills",
        capacity: 4,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 82.0,
                z: -138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 44.0,
                z: -100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -73.0,
                z: -28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -78.0,
                z: 76.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -62.0,
                z: 175.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "east_coast",
        capacity: 4,
        risk: 0.77,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 128.0,
                z: -119.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 144.0,
                z: -84.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 211.0,
                z: 4.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 249.0,
                z: 107.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 151.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 18.0,
                z: 253.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -60.0,
                z: 283.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_10_HILLS_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "southwest_road",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -228.0,
                z: 239.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -324.0,
                z: 143.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -354.0,
                z: 57.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -351.0,
                z: -100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -108.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -76.0,
                z: -192.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 20.0,
                z: -282.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_hills",
        capacity: 4,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -62.0,
                z: 175.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -78.0,
                z: 76.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -73.0,
                z: -28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 44.0,
                z: -100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: -138.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_coast",
        capacity: 4,
        risk: 0.77,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -60.0,
                z: 283.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 18.0,
                z: 253.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 151.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 249.0,
                z: 107.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 211.0,
                z: 4.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 144.0,
                z: -84.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 128.0,
                z: -119.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_11_MUROVANKA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_woods",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -211.0,
                z: 328.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -243.0,
                z: 298.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -332.0,
                z: 151.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -388.0,
                z: -49.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -322.0,
                z: -206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: -245.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -151.0,
                z: -317.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_field",
        capacity: 4,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -5.0,
                z: 332.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -5.0,
                z: 28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -18.0,
                z: -122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 19.0,
                z: -256.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -55.0,
                z: -379.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_village",
        capacity: 4,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 72.0,
                z: 369.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 182.0,
                z: 304.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 232.0,
                z: 258.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 300.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -5.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 290.0,
                z: -106.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 162.0,
                z: -259.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 12.0,
                z: -334.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_11_MUROVANKA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_woods",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -151.0,
                z: -317.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: -245.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -322.0,
                z: -206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -388.0,
                z: -49.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -332.0,
                z: 151.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -243.0,
                z: 298.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -211.0,
                z: 328.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_field",
        capacity: 4,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -55.0,
                z: -379.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 19.0,
                z: -256.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -18.0,
                z: -122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -5.0,
                z: 28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -5.0,
                z: 332.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "east_village",
        capacity: 4,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 12.0,
                z: -334.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 162.0,
                z: -259.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 290.0,
                z: -106.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -5.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 300.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 232.0,
                z: 258.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 182.0,
                z: 304.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 72.0,
                z: 369.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_13_ERLENBERG_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_bridge",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 32.0,
                z: 452.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 162.0,
                z: 403.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 306.0,
                z: 305.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 435.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 441.0,
                z: 88.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 406.0,
                z: -206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 395.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 319.0,
                z: -332.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "middle_crossing",
        capacity: 4,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -65.0,
                z: -239.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -105.0,
                z: -69.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -79.0,
                z: 28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -8.0,
                z: 205.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_bridge",
        capacity: 4,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -225.0,
                z: 328.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -399.0,
                z: 288.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -434.0,
                z: -94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -422.0,
                z: -136.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -68.0,
                z: -438.0,
                hold: true,
            },
        ],
    },
];

static ROUTES_13_ERLENBERG_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_bridge",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: 319.0,
                z: -332.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 395.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 406.0,
                z: -206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 441.0,
                z: 88.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 435.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 306.0,
                z: 305.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 162.0,
                z: 403.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 32.0,
                z: 452.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "middle_crossing",
        capacity: 4,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.82,
            flanker: 0.52,
            sniper: 1.0,
            scout: 0.64,
            artillery: 0.12,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -8.0,
                z: 205.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -79.0,
                z: 28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -105.0,
                z: -69.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -65.0,
                z: -239.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "south_bridge",
        capacity: 4,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 0.26,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.4,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Enabled,
        waypoints: &[
            TacticalWaypoint {
                x: -68.0,
                z: -438.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -422.0,
                z: -136.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -434.0,
                z: -94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -399.0,
                z: 288.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -225.0,
                z: 328.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_14_SIEGFRIED_LINE_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_field",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.42,
            scout: 0.86,
            artillery: 0.02,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -1.0,
                z: -416.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -178.0,
                z: -310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -319.0,
                z: -136.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: 11.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -268.0,
                z: 215.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 333.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 32.0,
                z: 400.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "fortification_line",
        capacity: 4,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.86,
            flanker: 0.46,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.18,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 214.0,
                z: -338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 258.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 258.0,
                z: -118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -46.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 314.0,
                z: 62.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: 106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 278.0,
                z: 254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 238.0,
                z: 338.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_city",
        capacity: 6,
        risk: 0.64,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.22,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 349.0,
                z: -398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 452.0,
                z: -319.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 477.0,
                z: -246.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 486.0,
                z: -146.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 484.0,
                z: -99.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 412.0,
                z: 128.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_14_SIEGFRIED_LINE_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_field",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.42,
            scout: 0.86,
            artillery: 0.02,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 32.0,
                z: 400.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 333.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -268.0,
                z: 215.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: 11.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -319.0,
                z: -136.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -178.0,
                z: -310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -1.0,
                z: -416.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "fortification_line",
        capacity: 4,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.86,
            flanker: 0.46,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.18,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 238.0,
                z: 338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 278.0,
                z: 254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: 106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 314.0,
                z: 62.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -46.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 258.0,
                z: -118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 258.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 214.0,
                z: -338.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_city",
        capacity: 6,
        risk: 0.64,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.22,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 412.0,
                z: 128.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 484.0,
                z: -99.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 486.0,
                z: -146.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 477.0,
                z: -246.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 452.0,
                z: -319.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 349.0,
                z: -398.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_17_MUNCHEN_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_streets",
        capacity: 5,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.9,
            support: 0.65,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.22,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -55.0,
            z: 195.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -177.0,
                z: -170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -214.0,
                z: -110.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -214.0,
                z: -22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -205.0,
                z: 44.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -194.0,
                z: 78.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -194.0,
                z: 94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -191.0,
                z: 103.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -198.0,
                z: 156.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -116.0,
                z: 195.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -16.0,
                z: 216.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_rail",
        capacity: 4,
        risk: 0.48,
        role_weights: RoleWeights {
            brawler: 0.25,
            support: 0.72,
            flanker: 0.65,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.12,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 100.0,
            z: 180.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 169.0,
                z: -129.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 261.0,
                z: -3.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 270.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 253.0,
                z: 155.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "center_blocks",
        capacity: 3,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.75,
            support: 1.0,
            flanker: 0.42,
            sniper: 0.22,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 60.0,
            z: 185.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -22.0,
                z: -27.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 9.0,
                z: 35.0,
                hold: true,
            },
        ],
    },
];

static ROUTES_17_MUNCHEN_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_streets",
        capacity: 5,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.9,
            support: 0.65,
            flanker: 0.3,
            sniper: 0.12,
            scout: 0.22,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -80.0,
            z: -190.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -16.0,
                z: 216.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -116.0,
                z: 195.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -198.0,
                z: 156.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -191.0,
                z: 103.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -194.0,
                z: 94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -194.0,
                z: 78.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -205.0,
                z: 44.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -214.0,
                z: -22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -214.0,
                z: -110.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -177.0,
                z: -170.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_rail",
        capacity: 4,
        risk: 0.48,
        role_weights: RoleWeights {
            brawler: 0.25,
            support: 0.72,
            flanker: 0.65,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.12,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -55.0,
            z: -190.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 253.0,
                z: 155.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 261.0,
                z: -3.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 169.0,
                z: -129.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "center_blocks",
        capacity: 3,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.75,
            support: 1.0,
            flanker: 0.42,
            sniper: 0.22,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -80.0,
            z: -175.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 9.0,
                z: 35.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -22.0,
                z: -27.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_18_CLIFF_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_coast",
        capacity: 4,
        risk: 0.58,
        role_weights: RoleWeights {
            brawler: 0.55,
            support: 0.88,
            flanker: 0.6,
            sniper: 0.72,
            scout: 0.48,
            artillery: 0.08,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -285.0,
            z: 390.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -358.0,
                z: -336.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -415.0,
                z: -46.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -325.0,
                z: 355.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_road",
        capacity: 5,
        risk: 0.73,
        role_weights: RoleWeights {
            brawler: 0.92,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.15,
            scout: 0.22,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -240.0,
            z: 385.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 39.0,
                z: -211.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 76.0,
                z: -106.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 29.0,
                z: 171.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -48.0,
                z: 268.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_ridge",
        capacity: 3,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.68,
            flanker: 1.0,
            sniper: 0.66,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -120.0,
            z: 340.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -41.0,
                z: -304.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 282.0,
                z: -87.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: -2.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 266.0,
                z: 140.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -11.0,
                z: 268.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -154.0,
                z: 394.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_18_CLIFF_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_coast",
        capacity: 4,
        risk: 0.58,
        role_weights: RoleWeights {
            brawler: 0.55,
            support: 0.88,
            flanker: 0.6,
            sniper: 0.72,
            scout: 0.48,
            artillery: 0.08,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -290.0,
            z: -420.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -325.0,
                z: 355.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -415.0,
                z: -46.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -336.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_road",
        capacity: 5,
        risk: 0.73,
        role_weights: RoleWeights {
            brawler: 0.92,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.15,
            scout: 0.22,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -280.0,
            z: -420.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -48.0,
                z: 268.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 29.0,
                z: 171.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 76.0,
                z: -106.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 39.0,
                z: -211.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_ridge",
        capacity: 3,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.68,
            flanker: 1.0,
            sniper: 0.66,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -270.0,
            z: -420.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -154.0,
                z: 394.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -11.0,
                z: 268.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: 140.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: -2.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 282.0,
                z: -87.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -41.0,
                z: -304.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_19_MONASTERY_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_field",
        capacity: 4,
        risk: 0.51,
        role_weights: RoleWeights {
            brawler: 0.48,
            support: 0.78,
            flanker: 0.64,
            sniper: 0.86,
            scout: 0.54,
            artillery: 0.1,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -80.0,
            z: 345.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -352.0,
                z: -363.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -448.0,
                z: -169.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -434.0,
                z: 185.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -352.0,
                z: 395.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -131.0,
                z: 415.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "monastery_lane",
        capacity: 5,
        risk: 0.79,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.72,
            flanker: 0.28,
            sniper: 0.1,
            scout: 0.15,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -5.0,
            z: 350.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 34.0,
                z: -250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -110.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: 62.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 22.0,
                z: 282.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_hills",
        capacity: 3,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.34,
            support: 0.68,
            flanker: 1.0,
            sniper: 0.76,
            scout: 0.88,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 55.0,
            z: 350.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 249.0,
                z: -372.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 254.0,
                z: -166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 162.0,
                z: 61.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 286.0,
                z: 328.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 262.0,
                z: 394.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_19_MONASTERY_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_field",
        capacity: 4,
        risk: 0.51,
        role_weights: RoleWeights {
            brawler: 0.48,
            support: 0.78,
            flanker: 0.64,
            sniper: 0.86,
            scout: 0.54,
            artillery: 0.1,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 15.0,
            z: -370.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -131.0,
                z: 415.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -352.0,
                z: 395.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -434.0,
                z: 185.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -448.0,
                z: -169.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -352.0,
                z: -363.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "monastery_lane",
        capacity: 5,
        risk: 0.79,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.72,
            flanker: 0.28,
            sniper: 0.1,
            scout: 0.15,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 20.0,
            z: -370.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 22.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: 62.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -110.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -250.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_hills",
        capacity: 3,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.34,
            support: 0.68,
            flanker: 1.0,
            sniper: 0.76,
            scout: 0.88,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 35.0,
            z: -370.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 262.0,
                z: 394.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 286.0,
                z: 328.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 162.0,
                z: 61.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 254.0,
                z: -166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 249.0,
                z: -372.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_22_SLOUGH_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_ridge",
        capacity: 4,
        risk: 0.57,
        role_weights: RoleWeights {
            brawler: 0.48,
            support: 0.78,
            flanker: 0.72,
            sniper: 0.82,
            scout: 0.5,
            artillery: 0.1,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 345.0,
            z: 400.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 49.0,
                z: -390.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: -296.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -318.0,
                z: -96.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -388.0,
                z: 145.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -334.0,
                z: 305.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "middle_low",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.95,
            support: 0.76,
            flanker: 0.36,
            sniper: 0.15,
            scout: 0.22,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 350.0,
            z: 400.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 242.0,
                z: -343.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 124.0,
                z: -262.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 113.0,
                z: 28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -41.0,
                z: 228.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -248.0,
                z: 382.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "east_ridge",
        capacity: 3,
        risk: 0.69,
        role_weights: RoleWeights {
            brawler: 0.35,
            support: 0.65,
            flanker: 1.0,
            sniper: 0.7,
            scout: 0.94,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 360.0,
            z: 395.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 349.0,
                z: -272.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 379.0,
                z: -76.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 320.0,
                z: 218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 246.0,
                z: 328.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: 388.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_22_SLOUGH_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_ridge",
        capacity: 4,
        risk: 0.57,
        role_weights: RoleWeights {
            brawler: 0.48,
            support: 0.78,
            flanker: 0.72,
            sniper: 0.82,
            scout: 0.5,
            artillery: 0.1,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -385.0,
            z: -405.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -334.0,
                z: 305.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -388.0,
                z: 145.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -318.0,
                z: -96.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: -296.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 49.0,
                z: -390.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "middle_low",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.95,
            support: 0.76,
            flanker: 0.36,
            sniper: 0.15,
            scout: 0.22,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -385.0,
            z: -405.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -248.0,
                z: 382.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -41.0,
                z: 228.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 113.0,
                z: 28.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 124.0,
                z: -262.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 242.0,
                z: -343.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_ridge",
        capacity: 3,
        risk: 0.69,
        role_weights: RoleWeights {
            brawler: 0.35,
            support: 0.65,
            flanker: 1.0,
            sniper: 0.7,
            scout: 0.94,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -380.0,
            z: -405.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 142.0,
                z: 388.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 246.0,
                z: 328.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 320.0,
                z: 218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 379.0,
                z: -76.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 349.0,
                z: -272.0,
                hold: true,
            },
        ],
    },
];

static ROUTES_23_WESTFELD_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_ridge",
        capacity: 3,
        risk: 0.67,
        role_weights: RoleWeights {
            brawler: 0.34,
            support: 0.75,
            flanker: 1.0,
            sniper: 0.82,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 339.0,
            z: 300.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -383.0,
                z: -12.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -274.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 331.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 229.0,
                z: 368.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_village",
        capacity: 5,
        risk: 0.75,
        role_weights: RoleWeights {
            brawler: 0.95,
            support: 0.72,
            flanker: 0.34,
            sniper: 0.12,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 339.0,
            z: 300.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -319.0,
                z: -112.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -25.0,
                z: 18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 32.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 146.0,
                z: 340.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 229.0,
                z: 342.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_fields",
        capacity: 4,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.45,
            support: 0.88,
            flanker: 0.58,
            sniper: 1.0,
            scout: 0.46,
            artillery: 0.16,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 339.0,
            z: 300.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 62.0,
                z: -459.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 462.0,
                z: -409.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 470.0,
                z: 71.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 426.0,
                z: 181.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_23_WESTFELD_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_ridge",
        capacity: 3,
        risk: 0.67,
        role_weights: RoleWeights {
            brawler: 0.34,
            support: 0.75,
            flanker: 1.0,
            sniper: 0.82,
            scout: 0.82,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -300.0,
            z: -340.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 229.0,
                z: 368.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 331.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -274.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -383.0,
                z: -12.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_village",
        capacity: 5,
        risk: 0.75,
        role_weights: RoleWeights {
            brawler: 0.95,
            support: 0.72,
            flanker: 0.34,
            sniper: 0.12,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -300.0,
            z: -340.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 229.0,
                z: 342.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 146.0,
                z: 340.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 32.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -25.0,
                z: 18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -319.0,
                z: -112.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "east_fields",
        capacity: 4,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.45,
            support: 0.88,
            flanker: 0.58,
            sniper: 1.0,
            scout: 0.46,
            artillery: 0.16,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -300.0,
            z: -340.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 426.0,
                z: 181.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 470.0,
                z: 71.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 462.0,
                z: -409.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -459.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_28_DESERT_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_dunes",
        capacity: 3,
        risk: 0.69,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.7,
            flanker: 1.0,
            sniper: 0.76,
            scout: 0.92,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -405.0,
            z: 138.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 451.0,
                z: 11.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 35.0,
                z: 321.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -128.0,
                z: 275.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -248.0,
                z: 63.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "village_road",
        capacity: 5,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.92,
            support: 0.78,
            flanker: 0.36,
            sniper: 0.14,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -405.0,
            z: 138.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 198.0,
                z: -98.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -183.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -55.0,
                z: -171.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -292.0,
                z: 51.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_rocks",
        capacity: 4,
        risk: 0.56,
        role_weights: RoleWeights {
            brawler: 0.5,
            support: 0.86,
            flanker: 0.6,
            sniper: 1.0,
            scout: 0.46,
            artillery: 0.12,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -405.0,
            z: 138.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 439.0,
                z: -332.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 349.0,
                z: -464.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -355.0,
                z: -426.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -462.0,
                z: -319.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -407.0,
                z: -9.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_28_DESERT_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_dunes",
        capacity: 3,
        risk: 0.69,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.7,
            flanker: 1.0,
            sniper: 0.76,
            scout: 0.92,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 373.0,
            z: -179.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -248.0,
                z: 63.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -128.0,
                z: 275.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 35.0,
                z: 321.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 451.0,
                z: 11.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "village_road",
        capacity: 5,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 0.92,
            support: 0.78,
            flanker: 0.36,
            sniper: 0.14,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 373.0,
            z: -179.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -292.0,
                z: 51.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -55.0,
                z: -171.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -183.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 198.0,
                z: -98.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_rocks",
        capacity: 4,
        risk: 0.56,
        role_weights: RoleWeights {
            brawler: 0.5,
            support: 0.86,
            flanker: 0.6,
            sniper: 1.0,
            scout: 0.46,
            artillery: 0.12,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 373.0,
            z: -179.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -407.0,
                z: -9.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -462.0,
                z: -319.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -355.0,
                z: -426.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 349.0,
                z: -464.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 439.0,
                z: -332.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_29_EL_HALLOUF_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "south_valley",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.94,
            support: 0.78,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 410.0,
            z: 270.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 182.0,
                z: -128.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -86.0,
                z: -198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -130.0,
                z: -226.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_bowl",
        capacity: 4,
        risk: 0.77,
        role_weights: RoleWeights {
            brawler: 0.76,
            support: 0.92,
            flanker: 0.48,
            sniper: 0.36,
            scout: 0.42,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 410.0,
            z: 270.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 29.0,
                z: 355.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -181.0,
                z: 262.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -243.0,
                z: 128.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -311.0,
                z: -126.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "north_ridge",
        capacity: 3,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.7,
            flanker: 1.0,
            sniper: 0.92,
            scout: 0.88,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 410.0,
            z: 275.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 32.0,
                z: 406.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -228.0,
                z: 462.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -461.0,
                z: 414.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -449.0,
                z: -56.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -416.0,
                z: -139.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_29_EL_HALLOUF_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "south_valley",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 0.94,
            support: 0.78,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -270.0,
            z: -380.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -130.0,
                z: -226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -86.0,
                z: -198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 182.0,
                z: -128.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "central_bowl",
        capacity: 4,
        risk: 0.77,
        role_weights: RoleWeights {
            brawler: 0.76,
            support: 0.92,
            flanker: 0.48,
            sniper: 0.36,
            scout: 0.42,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -270.0,
            z: -380.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -311.0,
                z: -126.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -243.0,
                z: 128.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -181.0,
                z: 262.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 29.0,
                z: 355.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "north_ridge",
        capacity: 3,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.7,
            flanker: 1.0,
            sniper: 0.92,
            scout: 0.88,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -270.0,
            z: -375.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -416.0,
                z: -139.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -449.0,
                z: -56.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -461.0,
                z: 414.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -228.0,
                z: 462.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 32.0,
                z: 406.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_31_AIRFIELD_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_runway",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.42,
            scout: 0.86,
            artillery: 0.02,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 398.0,
                z: -74.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 406.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 273.0,
                z: 248.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 59.0,
                z: 285.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -270.0,
                z: 201.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -342.0,
                z: -34.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_ridges",
        capacity: 4,
        risk: 0.68,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.86,
            flanker: 0.46,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.18,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 296.0,
                z: 36.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 139.0,
                z: 84.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -30.0,
                z: 82.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -305.0,
                z: -30.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_towns",
        capacity: 5,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.22,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 204.0,
                z: -239.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 90.0,
                z: -289.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: -260.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -160.0,
                z: -235.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -240.0,
                z: -200.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_31_AIRFIELD_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_runway",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.62,
            flanker: 1.0,
            sniper: 0.42,
            scout: 0.86,
            artillery: 0.02,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -342.0,
                z: -34.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -270.0,
                z: 201.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 59.0,
                z: 285.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 273.0,
                z: 248.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 406.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: -74.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_ridges",
        capacity: 4,
        risk: 0.68,
        role_weights: RoleWeights {
            brawler: 0.16,
            support: 0.86,
            flanker: 0.46,
            sniper: 1.0,
            scout: 0.62,
            artillery: 0.18,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -305.0,
                z: -30.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -30.0,
                z: 82.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 139.0,
                z: 84.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 296.0,
                z: 36.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_towns",
        capacity: 5,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.35,
            sniper: 0.22,
            scout: 0.24,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -240.0,
                z: -200.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: -235.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: -260.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 90.0,
                z: -289.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 204.0,
                z: -239.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_33_FJORD_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_ridge",
        capacity: 3,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.7,
            flanker: 1.0,
            sniper: 0.88,
            scout: 0.86,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -365.0,
            z: 110.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 382.0,
                z: 128.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 418.0,
                z: 338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 309.0,
                z: 410.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -88.0,
                z: 348.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -268.0,
                z: 231.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "middle_village",
        capacity: 5,
        risk: 0.8,
        role_weights: RoleWeights {
            brawler: 0.96,
            support: 0.78,
            flanker: 0.32,
            sniper: 0.14,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -365.0,
            z: 110.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 189.0,
                z: -59.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -21.0,
                z: -104.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -205.0,
                z: -58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -252.0,
                z: -35.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_coast",
        capacity: 4,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.46,
            support: 0.88,
            flanker: 0.62,
            sniper: 1.0,
            scout: 0.42,
            artillery: 0.14,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: -365.0,
            z: 105.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: 286.0,
                z: -138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 49.0,
                z: -130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -11.0,
                z: -246.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -105.0,
                z: -378.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -173.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -227.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -343.0,
                z: 25.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_33_FJORD_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_ridge",
        capacity: 3,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.32,
            support: 0.7,
            flanker: 1.0,
            sniper: 0.88,
            scout: 0.86,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 380.0,
            z: -35.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -268.0,
                z: 231.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -88.0,
                z: 348.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 309.0,
                z: 410.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 418.0,
                z: 338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: 128.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "middle_village",
        capacity: 5,
        risk: 0.8,
        role_weights: RoleWeights {
            brawler: 0.96,
            support: 0.78,
            flanker: 0.32,
            sniper: 0.14,
            scout: 0.18,
            artillery: 0.0,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 380.0,
            z: -40.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -252.0,
                z: -35.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -205.0,
                z: -58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -21.0,
                z: -104.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 189.0,
                z: -59.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_coast",
        capacity: 4,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.46,
            support: 0.88,
            flanker: 0.62,
            sniper: 1.0,
            scout: 0.42,
            artillery: 0.14,
        },
        hold: RouteHold::Point(TacticalWaypoint {
            x: 380.0,
            z: -50.0,
            hold: false,
        }),
        waypoints: &[
            TacticalWaypoint {
                x: -343.0,
                z: 25.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -227.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -173.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -105.0,
                z: -378.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -11.0,
                z: -246.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 49.0,
                z: -130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 286.0,
                z: -138.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_34_REDSHIRE_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "east_ridge",
        capacity: 4,
        risk: 0.57,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 368.69,
                z: -269.52,
                hold: false,
            },
            TacticalWaypoint {
                x: 355.0,
                z: -145.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 292.0,
                z: -58.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 205.0,
                z: 35.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 90.0,
                z: 146.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -85.0,
                z: 286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -209.86,
                z: 368.25,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "river_town",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 368.69,
                z: -269.52,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: -245.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 172.0,
                z: -174.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 105.0,
                z: -88.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 23.0,
                z: 8.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -84.0,
                z: 112.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -165.0,
                z: 250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -209.86,
                z: 368.25,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "west_fields",
        capacity: 4,
        risk: 0.49,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 368.69,
                z: -269.52,
                hold: false,
            },
            TacticalWaypoint {
                x: 246.0,
                z: -340.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 88.0,
                z: -366.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -92.0,
                z: -300.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -250.0,
                z: -155.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -325.0,
                z: 32.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -280.0,
                z: 218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -209.86,
                z: 368.25,
                hold: false,
            },
        ],
    },
];

static ROUTES_34_REDSHIRE_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "east_ridge",
        capacity: 4,
        risk: 0.57,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -209.86,
                z: 368.25,
                hold: false,
            },
            TacticalWaypoint {
                x: -85.0,
                z: 286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 90.0,
                z: 146.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 205.0,
                z: 35.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 292.0,
                z: -58.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 355.0,
                z: -145.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 368.69,
                z: -269.52,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "river_town",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -209.86,
                z: 368.25,
                hold: false,
            },
            TacticalWaypoint {
                x: -165.0,
                z: 250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -84.0,
                z: 112.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 23.0,
                z: 8.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 105.0,
                z: -88.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 172.0,
                z: -174.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 270.0,
                z: -245.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 368.69,
                z: -269.52,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "west_fields",
        capacity: 4,
        risk: 0.49,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -209.86,
                z: 368.25,
                hold: false,
            },
            TacticalWaypoint {
                x: -280.0,
                z: 218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -325.0,
                z: 32.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -250.0,
                z: -155.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -92.0,
                z: -300.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 88.0,
                z: -366.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 246.0,
                z: -340.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 368.69,
                z: -269.52,
                hold: false,
            },
        ],
    },
];

static ROUTES_35_STEPPES_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "east_ridge",
        capacity: 4,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 412.0,
                z: -62.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 428.0,
                z: 158.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 318.0,
                z: 330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 222.0,
                z: 391.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_hollow",
        capacity: 5,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 70.0,
                z: -246.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -110.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -121.0,
                z: 31.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -131.0,
                z: 258.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "west_rocks",
        capacity: 4,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -282.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -372.0,
                z: -236.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -418.0,
                z: -70.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -342.0,
                z: 102.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_35_STEPPES_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "east_ridge",
        capacity: 4,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 222.0,
                z: 391.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 318.0,
                z: 330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 428.0,
                z: 158.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 412.0,
                z: -62.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_hollow",
        capacity: 5,
        risk: 0.74,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -131.0,
                z: 258.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -121.0,
                z: 31.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -110.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: -246.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "west_rocks",
        capacity: 4,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -342.0,
                z: 102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -418.0,
                z: -70.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -372.0,
                z: -236.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -282.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_36_FISHING_BAY_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_fields",
        capacity: 5,
        risk: 0.51,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -86.0,
                z: 398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -346.0,
                z: 374.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -442.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -386.0,
                z: 46.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -450.0,
                z: -234.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -350.0,
                z: -358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: -386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -18.0,
                z: -398.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_road",
        capacity: 5,
        risk: 0.69,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -86.0,
                z: 398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: 102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -42.0,
                z: -90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -26.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -18.0,
                z: -398.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "harbor_edge",
        capacity: 3,
        risk: 0.63,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -86.0,
                z: 398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 346.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 242.0,
                z: 122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -54.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 290.0,
                z: -186.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 230.0,
                z: -294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: -338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -18.0,
                z: -398.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_36_FISHING_BAY_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_fields",
        capacity: 5,
        risk: 0.51,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -18.0,
                z: -398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: -386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -350.0,
                z: -358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -450.0,
                z: -234.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -386.0,
                z: 46.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -442.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -346.0,
                z: 374.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -86.0,
                z: 398.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_road",
        capacity: 5,
        risk: 0.69,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -18.0,
                z: -398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -26.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -42.0,
                z: -90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -58.0,
                z: 102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -86.0,
                z: 398.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "harbor_edge",
        capacity: 3,
        risk: 0.63,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -18.0,
                z: -398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: -338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 230.0,
                z: -294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 290.0,
                z: -186.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -54.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 242.0,
                z: 122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 346.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -86.0,
                z: 398.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_37_CAUCASUS_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_pass",
        capacity: 5,
        risk: 0.71,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -378.0,
                z: 370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -294.0,
                z: 130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -418.0,
                z: -138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -386.0,
                z: -322.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -150.0,
                z: -346.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 86.0,
                z: -226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 242.0,
                z: -350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: -402.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_basin",
        capacity: 4,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -378.0,
                z: 370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -298.0,
                z: 230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -200.0,
                z: 240.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -110.0,
                z: 190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -20.0,
                z: 120.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 60.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 140.0,
                z: -10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 120.0,
                z: -80.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 118.0,
                z: -158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 206.0,
                z: -274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: -402.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_road",
        capacity: 3,
        risk: 0.58,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -378.0,
                z: 370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -326.0,
                z: 394.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -122.0,
                z: 422.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: 166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 86.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 366.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: -226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: -402.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_37_CAUCASUS_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_pass",
        capacity: 5,
        risk: 0.71,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 346.0,
                z: -402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 242.0,
                z: -350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 86.0,
                z: -226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -150.0,
                z: -346.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -386.0,
                z: -322.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -418.0,
                z: -138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -294.0,
                z: 130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: 370.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_basin",
        capacity: 4,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 346.0,
                z: -402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 206.0,
                z: -274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 118.0,
                z: -158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 120.0,
                z: -80.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 140.0,
                z: -10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 60.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -20.0,
                z: 120.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -110.0,
                z: 190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -200.0,
                z: 240.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -298.0,
                z: 230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: 370.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_road",
        capacity: 3,
        risk: 0.58,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 346.0,
                z: -402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: -226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 366.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 86.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 186.0,
                z: 166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -122.0,
                z: 422.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -326.0,
                z: 394.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: 370.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_38_MANNERHEIM_LINE_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "east_ridge",
        capacity: 4,
        risk: 0.56,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 398.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: 110.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: -114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 314.0,
                z: -206.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 202.0,
                z: -266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 2.0,
                z: -286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -170.0,
                z: -274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_gorge",
        capacity: 5,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 398.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 314.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 254.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 114.0,
                z: 190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 38.0,
                z: 170.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -62.0,
                z: -26.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -82.0,
                z: -150.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -222.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -306.0,
                z: -298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "west_lakeside",
        capacity: 3,
        risk: 0.64,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 398.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 382.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 86.0,
                z: 398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -30.0,
                z: 446.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -190.0,
                z: 358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -366.0,
                z: 302.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -414.0,
                z: 134.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -322.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: -134.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -306.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_38_MANNERHEIM_LINE_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "east_ridge",
        capacity: 4,
        risk: 0.56,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -338.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -170.0,
                z: -274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 2.0,
                z: -286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 202.0,
                z: -266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 314.0,
                z: -206.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 386.0,
                z: -114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: 110.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: 294.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_gorge",
        capacity: 5,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -338.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -306.0,
                z: -298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -222.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -82.0,
                z: -150.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -62.0,
                z: -26.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 38.0,
                z: 170.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 114.0,
                z: 190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 254.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 314.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: 294.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "west_lakeside",
        capacity: 3,
        risk: 0.64,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -338.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -306.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: -134.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -322.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -414.0,
                z: 134.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -366.0,
                z: 302.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -190.0,
                z: 358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -30.0,
                z: 446.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 86.0,
                z: 398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 382.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: 294.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_44_NORTH_AMERICA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_town",
        capacity: 5,
        risk: 0.73,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -358.0,
                z: -330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -382.0,
                z: 134.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -418.0,
                z: 358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -410.0,
                z: 434.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -162.0,
                z: 438.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 438.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 410.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 302.0,
                z: 362.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_valley",
        capacity: 4,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -358.0,
                z: -330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -150.0,
                z: -310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -78.0,
                z: -350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -362.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: -246.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 426.0,
                z: -102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 442.0,
                z: 206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: 322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 302.0,
                z: 362.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "lake_north_edge",
        capacity: 3,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -358.0,
                z: -330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -282.0,
                z: -154.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -242.0,
                z: 66.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 182.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: 286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 302.0,
                z: 362.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_44_NORTH_AMERICA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_town",
        capacity: 5,
        risk: 0.73,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 302.0,
                z: 362.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 410.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 438.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -162.0,
                z: 438.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -410.0,
                z: 434.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -418.0,
                z: 358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -382.0,
                z: 134.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -330.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_valley",
        capacity: 4,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 302.0,
                z: 362.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: 322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 442.0,
                z: 206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 426.0,
                z: -102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: -246.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 186.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -362.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -78.0,
                z: -350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -150.0,
                z: -310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -330.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "lake_north_edge",
        capacity: 3,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 302.0,
                z: 362.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: 286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 182.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -242.0,
                z: 66.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -282.0,
                z: -154.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -330.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_45_NORTH_AMERICA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_road",
        capacity: 4,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 194.0,
                z: 358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 40.0,
                z: 370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -50.0,
                z: 365.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -146.0,
                z: 374.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -258.0,
                z: 400.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -406.0,
                z: 398.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -466.0,
                z: 222.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -466.0,
                z: 170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -422.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -182.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -342.0,
                z: -326.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "river_crossing",
        capacity: 5,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 194.0,
                z: 358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 14.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -20.0,
                z: 80.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -30.0,
                z: 0.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: -60.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -140.0,
                z: -130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -170.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -186.0,
                z: -234.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -202.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -280.0,
                z: -300.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -342.0,
                z: -326.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_town",
        capacity: 4,
        risk: 0.63,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 194.0,
                z: 358.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 426.0,
                z: 82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 490.0,
                z: -46.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 498.0,
                z: -214.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 490.0,
                z: -274.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 342.0,
                z: -274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: -338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 118.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -62.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -234.0,
                z: -374.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -342.0,
                z: -326.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_45_NORTH_AMERICA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_road",
        capacity: 4,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -342.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -182.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -422.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -466.0,
                z: 170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -466.0,
                z: 222.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -406.0,
                z: 398.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -258.0,
                z: 400.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -146.0,
                z: 374.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -50.0,
                z: 365.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 40.0,
                z: 370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 194.0,
                z: 358.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "river_crossing",
        capacity: 5,
        risk: 0.76,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -342.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -280.0,
                z: -300.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -202.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -186.0,
                z: -234.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -170.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -140.0,
                z: -130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: -60.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -30.0,
                z: 0.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -20.0,
                z: 80.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 14.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 194.0,
                z: 358.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "south_town",
        capacity: 4,
        risk: 0.63,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -342.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -234.0,
                z: -374.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -62.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 118.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: -338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: -274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 490.0,
                z: -274.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 498.0,
                z: -214.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 490.0,
                z: -46.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 426.0,
                z: 82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 194.0,
                z: 358.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_47_CANADA_A_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_hills",
        capacity: 4,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -126.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -250.0,
                z: -242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -410.0,
                z: -38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -442.0,
                z: 86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -410.0,
                z: 262.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -354.0,
                z: 310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -50.0,
                z: 418.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 410.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 166.0,
                z: 370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 214.0,
                z: 330.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_road",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -126.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: -182.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -174.0,
                z: -38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -154.0,
                z: 14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -110.0,
                z: 58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -50.0,
                z: 98.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -58.0,
                z: 214.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 18.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: 330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 214.0,
                z: 330.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_shore",
        capacity: 3,
        risk: 0.54,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -126.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: -286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 54.0,
                z: -234.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 230.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 274.0,
                z: -86.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: 142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 290.0,
                z: 234.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 214.0,
                z: 330.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_47_CANADA_A_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "west_hills",
        capacity: 4,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.42,
            support: 0.64,
            flanker: 1.0,
            sniper: 0.44,
            scout: 0.78,
            artillery: 0.08,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 214.0,
                z: 330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 166.0,
                z: 370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 410.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -50.0,
                z: 418.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -354.0,
                z: 310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -410.0,
                z: 262.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -442.0,
                z: 86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -410.0,
                z: -38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -250.0,
                z: -242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -126.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "central_road",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.62,
            flanker: 0.38,
            sniper: 0.16,
            scout: 0.28,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 214.0,
                z: 330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: 330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 18.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: 214.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -50.0,
                z: 98.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -110.0,
                z: 58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -154.0,
                z: 14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -174.0,
                z: -38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: -182.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -126.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "east_shore",
        capacity: 3,
        risk: 0.54,
        role_weights: RoleWeights {
            brawler: 0.22,
            support: 0.92,
            flanker: 0.55,
            sniper: 1.0,
            scout: 0.52,
            artillery: 0.2,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 214.0,
                z: 330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 290.0,
                z: 234.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: 142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 274.0,
                z: -86.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 230.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 54.0,
                z: -234.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: -286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -126.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_59_ASIA_GREAT_WALL_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "wall_pass",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.2,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -338.0,
                z: 386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -235.0,
                z: 385.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -200.0,
                z: 386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -180.0,
                z: 366.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -176.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: 314.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -136.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -114.0,
                z: 118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -67.0,
                z: -5.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -38.0,
                z: -39.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 166.0,
                z: -108.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 232.0,
                z: -107.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 378.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 402.0,
                z: -186.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 398.0,
                z: -378.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "valley",
        capacity: 5,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 0.8,
            flanker: 0.8,
            sniper: 0.4,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -338.0,
                z: 386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -166.0,
                z: 402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 218.0,
                z: 448.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 296.0,
                z: 422.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 376.0,
                z: 355.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 447.0,
                z: 252.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 469.0,
                z: 151.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 468.0,
                z: 65.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 405.0,
                z: -92.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 433.0,
                z: -269.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 413.0,
                z: -309.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: -378.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "ridge",
        capacity: 5,
        risk: 0.38,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.7,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -338.0,
                z: 386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -418.0,
                z: 126.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -438.0,
                z: 14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -430.0,
                z: -300.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -415.0,
                z: -365.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -370.0,
                z: -400.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -290.0,
                z: -414.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -238.0,
                z: -414.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -62.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 18.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: -414.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: -430.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: -378.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_59_ASIA_GREAT_WALL_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "wall_pass",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.2,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 398.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 402.0,
                z: -186.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 378.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 232.0,
                z: -107.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 166.0,
                z: -108.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -38.0,
                z: -39.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -67.0,
                z: -5.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -114.0,
                z: 118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -108.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -136.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: 314.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -176.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -180.0,
                z: 366.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -200.0,
                z: 386.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -235.0,
                z: 385.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: 386.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "valley",
        capacity: 5,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 0.8,
            flanker: 0.8,
            sniper: 0.4,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 398.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 413.0,
                z: -309.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 433.0,
                z: -269.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 405.0,
                z: -92.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 468.0,
                z: 65.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 469.0,
                z: 151.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 447.0,
                z: 252.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 376.0,
                z: 355.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 296.0,
                z: 422.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 218.0,
                z: 448.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -166.0,
                z: 402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: 386.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "ridge",
        capacity: 5,
        risk: 0.38,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.7,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 398.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: -430.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: -414.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 18.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -62.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -238.0,
                z: -414.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -290.0,
                z: -414.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -370.0,
                z: -400.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -415.0,
                z: -365.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -430.0,
                z: -300.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -438.0,
                z: 14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -418.0,
                z: 126.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: 386.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_63_TUNDRA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "waterfall",
        capacity: 5,
        risk: 0.68,
        role_weights: RoleWeights {
            brawler: 0.8,
            support: 0.6,
            flanker: 0.4,
            sniper: 0.2,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 14.0,
                z: -306.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 22.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -154.0,
                z: -94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -178.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -142.0,
                z: 78.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 74.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -38.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: 282.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "plateau",
        capacity: 5,
        risk: 0.44,
        role_weights: RoleWeights {
            brawler: 0.2,
            support: 0.7,
            flanker: 0.8,
            sniper: 0.8,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 14.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 78.0,
                z: -226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 182.0,
                z: -206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: -142.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 298.0,
                z: -82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 290.0,
                z: 10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: 90.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 306.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 194.0,
                z: 194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 118.0,
                z: 250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: 282.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "village",
        capacity: 5,
        risk: 0.57,
        role_weights: RoleWeights {
            brawler: 0.7,
            support: 1.0,
            flanker: 0.4,
            sniper: 0.5,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 14.0,
                z: -306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -130.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -206.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -334.0,
                z: -170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -370.0,
                z: -70.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -6.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -318.0,
                z: 130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -258.0,
                z: 218.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -178.0,
                z: 254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: 282.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_63_TUNDRA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "waterfall",
        capacity: 5,
        risk: 0.68,
        role_weights: RoleWeights {
            brawler: 0.8,
            support: 0.6,
            flanker: 0.4,
            sniper: 0.2,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -2.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -38.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 74.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -142.0,
                z: 78.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -178.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -154.0,
                z: -94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 22.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 14.0,
                z: -306.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "plateau",
        capacity: 5,
        risk: 0.44,
        role_weights: RoleWeights {
            brawler: 0.2,
            support: 0.7,
            flanker: 0.8,
            sniper: 0.8,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -2.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 118.0,
                z: 250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 194.0,
                z: 194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 306.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 346.0,
                z: 90.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 290.0,
                z: 10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 298.0,
                z: -82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: -142.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 182.0,
                z: -206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 78.0,
                z: -226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 14.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "village",
        capacity: 5,
        risk: 0.57,
        role_weights: RoleWeights {
            brawler: 0.7,
            support: 1.0,
            flanker: 0.4,
            sniper: 0.5,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -2.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -178.0,
                z: 254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -258.0,
                z: 218.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -318.0,
                z: 130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -358.0,
                z: -6.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -370.0,
                z: -70.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -334.0,
                z: -170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -206.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -130.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 14.0,
                z: -306.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_73_ASIA_KOREA_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "temple",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.9,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.2,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -278.0,
                z: -298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -262.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -114.0,
                z: -194.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 94.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 246.0,
                z: 154.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: 266.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "river",
        capacity: 5,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 0.9,
            flanker: 0.8,
            sniper: 0.5,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -278.0,
                z: -298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -282.0,
                z: -158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -322.0,
                z: -90.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -314.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -334.0,
                z: 114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -330.0,
                z: 190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -300.0,
                z: 250.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -254.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -114.0,
                z: 318.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 14.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: 266.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "hills",
        capacity: 5,
        risk: 0.43,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.4,
            flanker: 0.8,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -278.0,
                z: -298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -182.0,
                z: -294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 86.0,
                z: -298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: -202.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 310.0,
                z: -150.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 286.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: 206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 270.0,
                z: 266.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_73_ASIA_KOREA_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "temple",
        capacity: 5,
        risk: 0.66,
        role_weights: RoleWeights {
            brawler: 0.9,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.2,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 270.0,
                z: 266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 246.0,
                z: 154.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 94.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -114.0,
                z: -194.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -262.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: -298.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "river",
        capacity: 5,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 0.9,
            flanker: 0.8,
            sniper: 0.5,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 270.0,
                z: 266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 14.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -114.0,
                z: 318.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -254.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -300.0,
                z: 250.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -330.0,
                z: 190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -334.0,
                z: 114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -314.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -322.0,
                z: -90.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -282.0,
                z: -158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: -298.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "hills",
        capacity: 5,
        risk: 0.43,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.4,
            flanker: 0.8,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 270.0,
                z: 266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: 206.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 286.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 310.0,
                z: -150.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: -202.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 86.0,
                z: -298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -182.0,
                z: -294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: -298.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_83_KHARKIV_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "factory",
        capacity: 5,
        risk: 0.71,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -198.0,
                z: -266.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -138.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: -114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -46.0,
                z: -54.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 2.0,
                z: 10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 42.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 94.0,
                z: 74.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 154.0,
                z: 94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 202.0,
                z: 126.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 230.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 194.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "square",
        capacity: 5,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.7,
            support: 1.0,
            flanker: 0.5,
            sniper: 0.5,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -198.0,
                z: -266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -230.0,
                z: -42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -262.0,
                z: 118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -246.0,
                z: 246.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -130.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -102.0,
                z: 270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -74.0,
                z: 302.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -54.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 222.0,
                z: 226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 194.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 5,
        risk: 0.35,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 0.8,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -198.0,
                z: -266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -46.0,
                z: -278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 178.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -254.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 266.0,
                z: -218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 282.0,
                z: -150.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 306.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 282.0,
                z: -78.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 282.0,
                z: 114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 194.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_83_KHARKIV_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "factory",
        capacity: 5,
        risk: 0.71,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.7,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 250.0,
                z: 194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 230.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 202.0,
                z: 126.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 154.0,
                z: 94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 94.0,
                z: 74.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 42.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 2.0,
                z: 10.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -46.0,
                z: -54.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: -114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -138.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -198.0,
                z: -266.0,
                hold: true,
            },
        ],
    },
    TacticalRoute {
        id: "square",
        capacity: 5,
        risk: 0.61,
        role_weights: RoleWeights {
            brawler: 0.7,
            support: 1.0,
            flanker: 0.5,
            sniper: 0.5,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 250.0,
                z: 194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 222.0,
                z: 226.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -54.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -74.0,
                z: 302.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -102.0,
                z: 270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -130.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -246.0,
                z: 246.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -262.0,
                z: 118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -230.0,
                z: -42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -198.0,
                z: -266.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 5,
        risk: 0.35,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 0.8,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 250.0,
                z: 194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 282.0,
                z: 114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 282.0,
                z: -78.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 306.0,
                z: -106.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 282.0,
                z: -150.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: -218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -254.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 178.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: -282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -46.0,
                z: -278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -198.0,
                z: -266.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_84_WINTER_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_ridge",
        capacity: 5,
        risk: 0.48,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.6,
            flanker: 0.9,
            sniper: 0.9,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -378.0,
                z: -138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -314.0,
                z: -34.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -298.0,
                z: 118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -238.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 398.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -14.0,
                z: 462.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: 446.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: 378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 338.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: 238.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "ice_road",
        capacity: 5,
        risk: 0.58,
        role_weights: RoleWeights {
            brawler: 0.5,
            support: 0.9,
            flanker: 0.7,
            sniper: 0.5,
            scout: 0.6,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -378.0,
                z: -138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -290.0,
                z: -86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -250.0,
                z: -22.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -150.0,
                z: 54.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: 122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: 254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 318.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 286.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: 238.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "town",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -378.0,
                z: -138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -280.0,
                z: -175.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: -230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -80.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 20.0,
                z: -305.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 120.0,
                z: -285.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 200.0,
                z: -255.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 300.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 350.0,
                z: -60.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: 130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: 238.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_84_WINTER_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "north_ridge",
        capacity: 5,
        risk: 0.48,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.6,
            flanker: 0.9,
            sniper: 0.9,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 390.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 338.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: 378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: 446.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -14.0,
                z: 462.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 398.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -238.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -298.0,
                z: 118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -314.0,
                z: -34.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: -138.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "ice_road",
        capacity: 5,
        risk: 0.58,
        role_weights: RoleWeights {
            brawler: 0.5,
            support: 0.9,
            flanker: 0.7,
            sniper: 0.5,
            scout: 0.6,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 390.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 286.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 318.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: 254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 162.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: 122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -150.0,
                z: 54.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -250.0,
                z: -22.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -290.0,
                z: -86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: -138.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "town",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 390.0,
                z: 238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: 130.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 350.0,
                z: -60.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 300.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 200.0,
                z: -255.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 120.0,
                z: -285.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 20.0,
                z: -305.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -80.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: -230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -280.0,
                z: -175.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: -138.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_86_HIMMELSDORF_WINTER_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "banana",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 2.0,
                z: -254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 170.0,
                z: -114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: -86.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 154.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 110.0,
                z: 66.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: 166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: 222.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: 278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: 302.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 350.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "hill",
        capacity: 5,
        risk: 0.75,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.5,
            flanker: 1.0,
            sniper: 0.2,
            scout: 0.8,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 2.0,
                z: -254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 102.0,
                z: -278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: -278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 382.0,
                z: -250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: -198.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 394.0,
                z: -118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 358.0,
                z: -50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: 46.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 162.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 350.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.6,
            flanker: 0.6,
            sniper: 1.0,
            scout: 0.8,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 2.0,
                z: -254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -74.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -130.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -134.0,
                z: -30.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -198.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -190.0,
                z: 166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: 230.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 350.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_86_HIMMELSDORF_WINTER_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "banana",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.6,
            flanker: 0.3,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 70.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: 302.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: 278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: 222.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: 166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 74.0,
                z: 102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 110.0,
                z: 66.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 154.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: -86.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 170.0,
                z: -114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 2.0,
                z: -254.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "hill",
        capacity: 5,
        risk: 0.75,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 0.5,
            flanker: 1.0,
            sniper: 0.2,
            scout: 0.8,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 70.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 162.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: 282.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 386.0,
                z: 46.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 358.0,
                z: -50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 394.0,
                z: -118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 398.0,
                z: -198.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 382.0,
                z: -250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: -278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 102.0,
                z: -278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 2.0,
                z: -254.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.6,
            flanker: 0.6,
            sniper: 1.0,
            scout: 0.8,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 70.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -158.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: 230.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -190.0,
                z: 166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -198.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -134.0,
                z: -30.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -130.0,
                z: -146.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -74.0,
                z: -210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 2.0,
                z: -254.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_92_STALINGRAD_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "city",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -106.0,
                z: -406.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -234.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -294.0,
                z: -186.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -362.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: -102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -366.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: 82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -246.0,
                z: 122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -278.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -294.0,
                z: 206.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -150.0,
                z: 250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -106.0,
                z: 310.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "railway",
        capacity: 5,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 1.0,
            flanker: 0.7,
            sniper: 0.7,
            scout: 0.6,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -106.0,
                z: -406.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -394.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 106.0,
                z: -354.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 198.0,
                z: -330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 107.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 254.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 158.0,
                z: 262.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: 322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -106.0,
                z: 310.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "embankment",
        capacity: 5,
        risk: 0.38,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -106.0,
                z: -406.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -55.0,
                z: -385.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 0.0,
                z: -355.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 30.0,
                z: -335.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -305.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -275.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 52.0,
                z: -230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -180.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 48.0,
                z: -130.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 38.0,
                z: -42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 54.0,
                z: 214.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -6.0,
                z: 298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -106.0,
                z: 310.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_92_STALINGRAD_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "city",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -106.0,
                z: 310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -150.0,
                z: 250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -294.0,
                z: 206.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -278.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -246.0,
                z: 122.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -338.0,
                z: 82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -366.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -378.0,
                z: -102.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -362.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -294.0,
                z: -186.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -234.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -106.0,
                z: -406.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "railway",
        capacity: 5,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 1.0,
            flanker: 0.7,
            sniper: 0.7,
            scout: 0.6,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -106.0,
                z: 310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: 322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: 294.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 158.0,
                z: 262.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 254.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 170.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 107.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -166.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 250.0,
                z: -250.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 198.0,
                z: -330.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 106.0,
                z: -354.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -394.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -106.0,
                z: -406.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "embankment",
        capacity: 5,
        risk: 0.38,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -106.0,
                z: 310.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -6.0,
                z: 298.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 54.0,
                z: 214.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 70.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 38.0,
                z: -42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -94.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 48.0,
                z: -130.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -180.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 52.0,
                z: -230.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -275.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 50.0,
                z: -305.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 30.0,
                z: -335.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 0.0,
                z: -355.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -55.0,
                z: -385.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -106.0,
                z: -406.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_95_LOST_CITY_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "boulevard",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -316.0,
                z: 121.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -160.0,
                z: 100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 0.0,
                z: 20.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 150.0,
                z: -70.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 316.0,
                z: -121.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "park",
        capacity: 5,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 1.0,
            flanker: 0.8,
            sniper: 0.6,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -316.0,
                z: 121.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -220.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -60.0,
                z: -50.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 100.0,
                z: -80.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 316.0,
                z: -121.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "outskirts",
        capacity: 5,
        risk: 0.35,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -316.0,
                z: 121.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -240.0,
                z: 210.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -40.0,
                z: 180.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 160.0,
                z: 30.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 316.0,
                z: -121.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_95_LOST_CITY_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "boulevard",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 316.0,
                z: -121.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: -70.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 0.0,
                z: 20.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -160.0,
                z: 100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -316.0,
                z: 121.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "park",
        capacity: 5,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.3,
            support: 1.0,
            flanker: 0.8,
            sniper: 0.6,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 316.0,
                z: -121.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 100.0,
                z: -80.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -60.0,
                z: -50.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -220.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -316.0,
                z: 121.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "outskirts",
        capacity: 5,
        risk: 0.35,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 316.0,
                z: -121.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 160.0,
                z: 30.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -40.0,
                z: 180.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -240.0,
                z: 210.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -316.0,
                z: 121.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_100_THEPIT_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "rim_west",
        capacity: 5,
        risk: 0.6,
        role_weights: RoleWeights {
            brawler: 0.8,
            support: 0.5,
            flanker: 0.5,
            sniper: 0.3,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -35.0,
                z: -198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -130.0,
                z: -100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -130.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -100.0,
                z: 100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -35.0,
                z: 198.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "pit",
        capacity: 5,
        risk: 0.75,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -35.0,
                z: -198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -50.0,
                z: -90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 0.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 20.0,
                z: 90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -35.0,
                z: 198.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rim_east",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.2,
            support: 0.6,
            flanker: 0.9,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -35.0,
                z: -198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 80.0,
                z: -100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 130.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 80.0,
                z: 100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -35.0,
                z: 198.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_100_THEPIT_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "rim_west",
        capacity: 5,
        risk: 0.6,
        role_weights: RoleWeights {
            brawler: 0.8,
            support: 0.5,
            flanker: 0.5,
            sniper: 0.3,
            scout: 0.3,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -35.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -100.0,
                z: 100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -130.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -130.0,
                z: -100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -35.0,
                z: -198.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "pit",
        capacity: 5,
        risk: 0.75,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -35.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 20.0,
                z: 90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 0.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -50.0,
                z: -90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -35.0,
                z: -198.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rim_east",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.2,
            support: 0.6,
            flanker: 0.9,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -35.0,
                z: 198.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 80.0,
                z: 100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 130.0,
                z: 0.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 80.0,
                z: -100.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -35.0,
                z: -198.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_101_DDAY_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "beach",
        capacity: 5,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.7,
            support: 0.8,
            flanker: 0.5,
            sniper: 0.3,
            scout: 0.4,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 146.0,
                z: -398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 114.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 46.0,
                z: -258.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: -194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -86.0,
                z: -14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 22.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -42.0,
                z: 114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 114.0,
                z: 278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 402.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "village",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 146.0,
                z: -398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 170.0,
                z: 58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 206.0,
                z: 238.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 198.0,
                z: 326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 402.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "cliff",
        capacity: 5,
        risk: 0.42,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.9,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 146.0,
                z: -398.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 442.0,
                z: -86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 474.0,
                z: -22.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 466.0,
                z: 62.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: 326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 350.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 150.0,
                z: 402.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_101_DDAY_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "beach",
        capacity: 5,
        risk: 0.55,
        role_weights: RoleWeights {
            brawler: 0.7,
            support: 0.8,
            flanker: 0.5,
            sniper: 0.3,
            scout: 0.4,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 150.0,
                z: 402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 114.0,
                z: 278.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -42.0,
                z: 114.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 22.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -86.0,
                z: -14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: -194.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 46.0,
                z: -258.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 114.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 146.0,
                z: -398.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "village",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 150.0,
                z: 402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 198.0,
                z: 326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 206.0,
                z: 238.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 170.0,
                z: 58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 174.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 138.0,
                z: -370.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 146.0,
                z: -398.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "cliff",
        capacity: 5,
        risk: 0.42,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.9,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 150.0,
                z: 402.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 350.0,
                z: 350.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: 326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 466.0,
                z: 62.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 474.0,
                z: -22.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 442.0,
                z: -86.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 186.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 146.0,
                z: -398.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_103_RUINBERG_WINTER_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "city",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -82.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 10.0,
                z: -238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -150.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 78.0,
                z: -54.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 42.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: 246.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: 306.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "field",
        capacity: 5,
        risk: 0.38,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.6,
            flanker: 0.9,
            sniper: 1.0,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -82.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 122.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 238.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 366.0,
                z: -222.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 366.0,
                z: 114.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 214.0,
                z: 274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: 306.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 5,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.5,
            support: 1.0,
            flanker: 0.7,
            sniper: 0.5,
            scout: 0.5,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -82.0,
                z: -290.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -82.0,
                z: -218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -210.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -282.0,
                z: -82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -318.0,
                z: 18.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -290.0,
                z: 74.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -170.0,
                z: 202.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -110.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -70.0,
                z: 306.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_103_RUINBERG_WINTER_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "city",
        capacity: 5,
        risk: 0.72,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -70.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: 246.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 42.0,
                z: 138.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 78.0,
                z: -54.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -150.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 10.0,
                z: -238.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -82.0,
                z: -290.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "field",
        capacity: 5,
        risk: 0.38,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.6,
            flanker: 0.9,
            sniper: 1.0,
            scout: 0.9,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -70.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 214.0,
                z: 274.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 326.0,
                z: 210.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 366.0,
                z: 114.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 366.0,
                z: -222.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 238.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 122.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -82.0,
                z: -290.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "rail",
        capacity: 5,
        risk: 0.52,
        role_weights: RoleWeights {
            brawler: 0.5,
            support: 1.0,
            flanker: 0.7,
            sniper: 0.5,
            scout: 0.5,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -70.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -110.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -170.0,
                z: 202.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -290.0,
                z: 74.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -318.0,
                z: 18.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -282.0,
                z: -82.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -210.0,
                z: -142.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -82.0,
                z: -218.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -82.0,
                z: -290.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_112_EIFFEL_TOWER_CTF_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "tower_west",
        capacity: 5,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.9,
            support: 0.7,
            flanker: 0.3,
            sniper: 0.2,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -346.0,
                z: -22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -274.0,
                z: 58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -242.0,
                z: 174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -118.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -6.0,
                z: 354.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 210.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: 90.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 310.0,
                z: 50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: -18.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "center",
        capacity: 5,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -346.0,
                z: -22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -242.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -46.0,
                z: -14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: -14.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 190.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: -18.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "tower_east",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.6,
            flanker: 0.9,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -346.0,
                z: -22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -246.0,
                z: -158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -138.0,
                z: -266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -38.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -22.0,
                z: -350.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 46.0,
                z: -334.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 230.0,
                z: -118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 342.0,
                z: -18.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_112_EIFFEL_TOWER_CTF_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "tower_west",
        capacity: 5,
        risk: 0.65,
        role_weights: RoleWeights {
            brawler: 0.9,
            support: 0.7,
            flanker: 0.3,
            sniper: 0.2,
            scout: 0.2,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 342.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 310.0,
                z: 50.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 266.0,
                z: 90.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 210.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -6.0,
                z: 354.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -118.0,
                z: 306.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -242.0,
                z: 174.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -274.0,
                z: 58.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -346.0,
                z: -22.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "center",
        capacity: 5,
        risk: 0.78,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 342.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: 42.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 190.0,
                z: 38.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 82.0,
                z: -14.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -46.0,
                z: -14.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -242.0,
                z: 22.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -346.0,
                z: -22.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "tower_east",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.6,
            flanker: 0.9,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: 342.0,
                z: -18.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 230.0,
                z: -118.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 126.0,
                z: -270.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 46.0,
                z: -334.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -22.0,
                z: -350.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -38.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -58.0,
                z: -378.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -138.0,
                z: -266.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -246.0,
                z: -158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -346.0,
                z: -22.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_114_CZECH_TEAM_1: &[TacticalRoute] = &[
    TacticalRoute {
        id: "town",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -10.0,
                z: -346.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -206.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -286.0,
                z: -242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -312.0,
                z: -90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -280.0,
                z: 100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -260.0,
                z: 200.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -210.0,
                z: 265.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 305.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: 338.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "valley",
        capacity: 5,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 1.0,
            flanker: 0.8,
            sniper: 0.5,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -10.0,
                z: -346.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 46.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -140.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -25.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 55.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 160.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 30.0,
                z: 220.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 0.0,
                z: 300.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: 338.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "ridge",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -10.0,
                z: -346.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: -318.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -334.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 338.0,
                z: -286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: -166.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 362.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 278.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 146.0,
                z: 354.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -2.0,
                z: 338.0,
                hold: false,
            },
        ],
    },
];

static ROUTES_114_CZECH_TEAM_2: &[TacticalRoute] = &[
    TacticalRoute {
        id: "town",
        capacity: 5,
        risk: 0.7,
        role_weights: RoleWeights {
            brawler: 1.0,
            support: 0.8,
            flanker: 0.2,
            sniper: 0.1,
            scout: 0.1,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -2.0,
                z: 338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -90.0,
                z: 305.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -210.0,
                z: 265.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -260.0,
                z: 200.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -280.0,
                z: 100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -312.0,
                z: -90.0,
                hold: true,
            },
            TacticalWaypoint {
                x: -286.0,
                z: -242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -206.0,
                z: -322.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: -346.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "valley",
        capacity: 5,
        risk: 0.5,
        role_weights: RoleWeights {
            brawler: 0.4,
            support: 1.0,
            flanker: 0.8,
            sniper: 0.5,
            scout: 0.7,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -2.0,
                z: 338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 0.0,
                z: 300.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 30.0,
                z: 220.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 160.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: 55.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -25.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -100.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 62.0,
                z: -140.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 46.0,
                z: -190.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 34.0,
                z: -254.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: -346.0,
                hold: false,
            },
        ],
    },
    TacticalRoute {
        id: "ridge",
        capacity: 5,
        risk: 0.4,
        role_weights: RoleWeights {
            brawler: 0.1,
            support: 0.5,
            flanker: 0.8,
            sniper: 1.0,
            scout: 1.0,
            artillery: 0.0,
        },
        hold: RouteHold::Unspecified,
        waypoints: &[
            TacticalWaypoint {
                x: -2.0,
                z: 338.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 146.0,
                z: 354.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 278.0,
                z: 242.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 362.0,
                z: 158.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 322.0,
                z: -2.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 390.0,
                z: -166.0,
                hold: true,
            },
            TacticalWaypoint {
                x: 338.0,
                z: -286.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 294.0,
                z: -334.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 142.0,
                z: -318.0,
                hold: false,
            },
            TacticalWaypoint {
                x: 58.0,
                z: -326.0,
                hold: false,
            },
            TacticalWaypoint {
                x: -10.0,
                z: -346.0,
                hold: false,
            },
        ],
    },
];

pub const TACTICAL_MAP_NAMES: &[&str] = &[
    "01_karelia",
    "02_malinovka",
    "04_himmelsdorf",
    "05_prohorovka",
    "06_ensk",
    "07_lakeville",
    "08_ruinberg",
    "10_hills",
    "11_murovanka",
    "13_erlenberg",
    "14_siegfried_line",
    "17_munchen",
    "18_cliff",
    "19_monastery",
    "22_slough",
    "23_westfeld",
    "28_desert",
    "29_el_hallouf",
    "31_airfield",
    "33_fjord",
    "34_redshire",
    "35_steppes",
    "36_fishing_bay",
    "37_caucasus",
    "38_mannerheim_line",
    "44_north_america",
    "45_north_america",
    "47_canada_a",
    "59_asia_great_wall",
    "63_tundra",
    "73_asia_korea",
    "83_kharkiv",
    "84_winter",
    "86_himmelsdorf_winter",
    "92_stalingrad",
    "95_lost_city",
    "100_thepit",
    "101_dday",
    "103_ruinberg_winter",
    "112_eiffel_tower_ctf",
    "114_czech",
];

pub static TACTICAL_MAPS: &[TacticalMap] = &[
    TacticalMap {
        name: "01_karelia",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -401.3,
                z: -399.9,
            },
            TacticalPoint { x: 397.6, z: 402.6 },
        ],
        team_routes: [ROUTES_01_KARELIA_TEAM_1, ROUTES_01_KARELIA_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "02_malinovka",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 75.6,
                z: -391.92,
            },
            TacticalPoint {
                x: -372.7,
                z: 108.12,
            },
        ],
        team_routes: [ROUTES_02_MALINOVKA_TEAM_1, ROUTES_02_MALINOVKA_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "04_himmelsdorf",
        bounds: TacticalBounds {
            min_x: -300.0,
            min_z: -300.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: 2.499999,
                z: -252.600143,
            },
            TacticalPoint {
                x: 69.09993,
                z: 348.999969,
            },
        ],
        team_routes: [ROUTES_04_HIMMELSDORF_TEAM_1, ROUTES_04_HIMMELSDORF_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "05_prohorovka",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -125.2,
                z: 448.5,
            },
            TacticalPoint { x: 51.6, z: -447.0 },
        ],
        team_routes: [ROUTES_05_PROHOROVKA_TEAM_1, ROUTES_05_PROHOROVKA_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "06_ensk",
        bounds: TacticalBounds {
            min_x: -300.0,
            min_z: -300.0,
            max_x: 300.0,
            max_z: 300.0,
        },
        bases: [
            TacticalPoint { x: 20.3, z: 249.7 },
            TacticalPoint { x: 19.1, z: -248.7 },
        ],
        team_routes: [ROUTES_06_ENSK_TEAM_1, ROUTES_06_ENSK_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "07_lakeville",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: -169.5,
                z: 319.4,
            },
            TacticalPoint {
                x: -169.5,
                z: -319.0,
            },
        ],
        team_routes: [ROUTES_07_LAKEVILLE_TEAM_1, ROUTES_07_LAKEVILLE_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "08_ruinberg",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint { x: -66.4, z: 306.1 },
            TacticalPoint {
                x: -82.9,
                z: -290.9,
            },
        ],
        team_routes: [ROUTES_08_RUINBERG_TEAM_1, ROUTES_08_RUINBERG_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "10_hills",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: 175.8,
                z: -305.8,
            },
            TacticalPoint {
                x: -236.7,
                z: 329.7,
            },
        ],
        team_routes: [ROUTES_10_HILLS_TEAM_1, ROUTES_10_HILLS_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "11_murovanka",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint { x: 202.8, z: 296.1 },
            TacticalPoint {
                x: -205.0,
                z: -292.8,
            },
        ],
        team_routes: [ROUTES_11_MUROVANKA_TEAM_1, ROUTES_11_MUROVANKA_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "13_erlenberg",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint { x: -146.2, z: -0.1 },
            TacticalPoint { x: 146.4, z: 0.1 },
        ],
        team_routes: [ROUTES_13_ERLENBERG_TEAM_1, ROUTES_13_ERLENBERG_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "14_siegfried_line",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 255.8,
                z: -439.83,
            },
            TacticalPoint {
                x: 283.85,
                z: 434.6,
            },
        ],
        team_routes: [
            ROUTES_14_SIEGFRIED_LINE_TEAM_1,
            ROUTES_14_SIEGFRIED_LINE_TEAM_2,
        ],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "17_munchen",
        bounds: TacticalBounds {
            min_x: -300.0,
            min_z: -300.0,
            max_x: 300.0,
            max_z: 300.0,
        },
        bases: [
            TacticalPoint {
                x: -83.7,
                z: -201.7,
            },
            TacticalPoint { x: 65.3, z: 220.5 },
        ],
        team_routes: [ROUTES_17_MUNCHEN_TEAM_1, ROUTES_17_MUNCHEN_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "18_cliff",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -287.4,
                z: -436.6,
            },
            TacticalPoint {
                x: -251.6,
                z: 434.6,
            },
        ],
        team_routes: [ROUTES_18_CLIFF_TEAM_1, ROUTES_18_CLIFF_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "19_monastery",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint { x: 20.1, z: -387.9 },
            TacticalPoint { x: -0.4, z: 397.4 },
        ],
        team_routes: [ROUTES_19_MONASTERY_TEAM_1, ROUTES_19_MONASTERY_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "22_slough",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -403.7,
                z: -424.1,
            },
            TacticalPoint { x: 383.3, z: 422.8 },
        ],
        team_routes: [ROUTES_22_SLOUGH_TEAM_1, ROUTES_22_SLOUGH_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "23_westfeld",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -300.1,
                z: -339.6,
            },
            TacticalPoint { x: 339.4, z: 299.8 },
        ],
        team_routes: [ROUTES_23_WESTFELD_TEAM_1, ROUTES_23_WESTFELD_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "28_desert",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 373.4855,
                z: -178.9612,
            },
            TacticalPoint {
                x: -405.0387,
                z: 137.5266,
            },
        ],
        team_routes: [ROUTES_28_DESERT_TEAM_1, ROUTES_28_DESERT_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "29_el_hallouf",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 299.256,
                z: 319.406,
            },
            TacticalPoint {
                x: -338.5832,
                z: -319.3074,
            },
        ],
        team_routes: [ROUTES_29_EL_HALLOUF_TEAM_1, ROUTES_29_EL_HALLOUF_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "31_airfield",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 360.65,
                z: -154.44,
            },
            TacticalPoint {
                x: -324.05,
                z: -176.18,
            },
        ],
        team_routes: [ROUTES_31_AIRFIELD_TEAM_1, ROUTES_31_AIRFIELD_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "33_fjord",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint { x: 399.1, z: -42.1 },
            TacticalPoint {
                x: -381.3,
                z: 111.4,
            },
        ],
        team_routes: [ROUTES_33_FJORD_TEAM_1, ROUTES_33_FJORD_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "34_redshire",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 368.69,
                z: -269.52,
            },
            TacticalPoint {
                x: -209.86,
                z: 368.25,
            },
        ],
        team_routes: [ROUTES_34_REDSHIRE_TEAM_1, ROUTES_34_REDSHIRE_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "35_steppes",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 228.22,
                z: -341.93,
            },
            TacticalPoint {
                x: -88.82,
                z: 361.86,
            },
        ],
        team_routes: [ROUTES_35_STEPPES_TEAM_1, ROUTES_35_STEPPES_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "36_fishing_bay",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -84.83,
                z: 397.81,
            },
            TacticalPoint {
                x: -17.02,
                z: -396.11,
            },
        ],
        team_routes: [ROUTES_36_FISHING_BAY_TEAM_1, ROUTES_36_FISHING_BAY_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "37_caucasus",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -376.74,
                z: 371.36,
            },
            TacticalPoint {
                x: 345.8,
                z: -399.46,
            },
        ],
        team_routes: [ROUTES_37_CAUCASUS_TEAM_1, ROUTES_37_CAUCASUS_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "38_mannerheim_line",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 398.14,
                z: 293.87,
            },
            TacticalPoint {
                x: -338.18,
                z: -306.26,
            },
        ],
        team_routes: [
            ROUTES_38_MANNERHEIM_LINE_TEAM_1,
            ROUTES_38_MANNERHEIM_LINE_TEAM_2,
        ],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "44_north_america",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -356.99,
                z: -329.81,
            },
            TacticalPoint {
                x: 300.19,
                z: 363.93,
            },
        ],
        team_routes: [
            ROUTES_44_NORTH_AMERICA_TEAM_1,
            ROUTES_44_NORTH_AMERICA_TEAM_2,
        ],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "45_north_america",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 197.41,
                z: 356.58,
            },
            TacticalPoint {
                x: -343.15,
                z: -327.37,
            },
        ],
        team_routes: [
            ROUTES_45_NORTH_AMERICA_TEAM_1,
            ROUTES_45_NORTH_AMERICA_TEAM_2,
        ],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "47_canada_a",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -126.89,
                z: -305.91,
            },
            TacticalPoint {
                x: 213.12,
                z: 328.11,
            },
        ],
        team_routes: [ROUTES_47_CANADA_A_TEAM_1, ROUTES_47_CANADA_A_TEAM_2],
        annotation_confidence: None,
    },
    TacticalMap {
        name: "59_asia_great_wall",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -410.0,
                z: -350.0,
            },
            TacticalPoint { x: 410.0, z: 350.0 },
        ],
        team_routes: [
            ROUTES_59_ASIA_GREAT_WALL_TEAM_1,
            ROUTES_59_ASIA_GREAT_WALL_TEAM_2,
        ],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "63_tundra",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: -330.0,
                z: -300.0,
            },
            TacticalPoint { x: 330.0, z: 290.0 },
        ],
        team_routes: [ROUTES_63_TUNDRA_TEAM_1, ROUTES_63_TUNDRA_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "73_asia_korea",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -420.0,
                z: -300.0,
            },
            TacticalPoint { x: 400.0, z: 300.0 },
        ],
        team_routes: [ROUTES_73_ASIA_KOREA_TEAM_1, ROUTES_73_ASIA_KOREA_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "83_kharkiv",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: -330.0,
                z: -300.0,
            },
            TacticalPoint { x: 320.0, z: 300.0 },
        ],
        team_routes: [ROUTES_83_KHARKIV_TEAM_1, ROUTES_83_KHARKIV_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "84_winter",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -410.0,
                z: -350.0,
            },
            TacticalPoint { x: 410.0, z: 350.0 },
        ],
        team_routes: [ROUTES_84_WINTER_TEAM_1, ROUTES_84_WINTER_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "86_himmelsdorf_winter",
        bounds: TacticalBounds {
            min_x: -300.0,
            min_z: -300.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: 355.0,
                z: -245.0,
            },
            TacticalPoint { x: 360.0, z: 349.0 },
        ],
        team_routes: [
            ROUTES_86_HIMMELSDORF_WINTER_TEAM_1,
            ROUTES_86_HIMMELSDORF_WINTER_TEAM_2,
        ],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "92_stalingrad",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 450.0,
            max_z: 450.0,
        },
        bases: [
            TacticalPoint {
                x: -400.0,
                z: -350.0,
            },
            TacticalPoint { x: 360.0, z: 310.0 },
        ],
        team_routes: [ROUTES_92_STALINGRAD_TEAM_1, ROUTES_92_STALINGRAD_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "95_lost_city",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: -316.0,
                z: 121.0,
            },
            TacticalPoint {
                x: 316.0,
                z: -121.0,
            },
        ],
        team_routes: [ROUTES_95_LOST_CITY_TEAM_1, ROUTES_95_LOST_CITY_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "100_thepit",
        bounds: TacticalBounds {
            min_x: -200.0,
            min_z: -200.0,
            max_x: 200.0,
            max_z: 200.0,
        },
        bases: [
            TacticalPoint {
                x: -35.0,
                z: -198.0,
            },
            TacticalPoint { x: -35.0, z: 198.0 },
        ],
        team_routes: [ROUTES_100_THEPIT_TEAM_1, ROUTES_100_THEPIT_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "101_dday",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -500.0,
            max_x: 600.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: 150.0,
                z: -403.0,
            },
            TacticalPoint { x: 150.0, z: 400.0 },
        ],
        team_routes: [ROUTES_101_DDAY_TEAM_1, ROUTES_101_DDAY_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "103_ruinberg_winter",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: 270.0,
                z: -330.0,
            },
            TacticalPoint { x: 64.0, z: 333.0 },
        ],
        team_routes: [
            ROUTES_103_RUINBERG_WINTER_TEAM_1,
            ROUTES_103_RUINBERG_WINTER_TEAM_2,
        ],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "112_eiffel_tower_ctf",
        bounds: TacticalBounds {
            min_x: -400.0,
            min_z: -400.0,
            max_x: 400.0,
            max_z: 400.0,
        },
        bases: [
            TacticalPoint {
                x: -300.0,
                z: -300.0,
            },
            TacticalPoint { x: 300.0, z: 300.0 },
        ],
        team_routes: [
            ROUTES_112_EIFFEL_TOWER_CTF_TEAM_1,
            ROUTES_112_EIFFEL_TOWER_CTF_TEAM_2,
        ],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
    TacticalMap {
        name: "114_czech",
        bounds: TacticalBounds {
            min_x: -500.0,
            min_z: -500.0,
            max_x: 500.0,
            max_z: 500.0,
        },
        bases: [
            TacticalPoint {
                x: -380.0,
                z: -300.0,
            },
            TacticalPoint { x: 350.0, z: 300.0 },
        ],
        team_routes: [ROUTES_114_CZECH_TEAM_1, ROUTES_114_CZECH_TEAM_2],
        annotation_confidence: Some("coarse-minimap-bounds"),
    },
];

#[cfg(test)]
mod tests {
    use super::*;

    const EXPECTED_TEAM_CAPACITIES: &[(&str, usize)] = &[
        ("01_karelia", 15),
        ("02_malinovka", 14),
        ("04_himmelsdorf", 16),
        ("05_prohorovka", 14),
        ("06_ensk", 14),
        ("07_lakeville", 14),
        ("08_ruinberg", 15),
        ("10_hills", 13),
        ("11_murovanka", 13),
        ("13_erlenberg", 13),
        ("14_siegfried_line", 15),
        ("17_munchen", 12),
        ("18_cliff", 12),
        ("19_monastery", 12),
        ("22_slough", 12),
        ("23_westfeld", 12),
        ("28_desert", 12),
        ("29_el_hallouf", 12),
        ("31_airfield", 14),
        ("33_fjord", 12),
        ("34_redshire", 13),
        ("35_steppes", 13),
        ("36_fishing_bay", 13),
        ("37_caucasus", 12),
        ("38_mannerheim_line", 12),
        ("44_north_america", 12),
        ("45_north_america", 13),
        ("47_canada_a", 12),
        ("59_asia_great_wall", 15),
        ("63_tundra", 15),
        ("73_asia_korea", 15),
        ("83_kharkiv", 15),
        ("84_winter", 15),
        ("86_himmelsdorf_winter", 15),
        ("92_stalingrad", 15),
        ("95_lost_city", 15),
        ("100_thepit", 15),
        ("101_dday", 15),
        ("103_ruinberg_winter", 15),
        ("112_eiffel_tower_ctf", 15),
        ("114_czech", 15),
    ];

    #[test]
    fn catalog_is_valid_and_complete_for_the_live_map_pool() {
        validate_catalog().unwrap();
        assert_eq!(TACTICAL_MAPS.len(), 41);
        assert_eq!(map_names().collect::<Vec<_>>(), TACTICAL_MAP_NAMES);
        let routes: usize = TACTICAL_MAPS
            .iter()
            .flat_map(|map| map.team_routes)
            .map(<[TacticalRoute]>::len)
            .sum();
        let waypoints: usize = TACTICAL_MAPS
            .iter()
            .flat_map(|map| map.team_routes)
            .flatten()
            .map(|route| route.waypoints.len())
            .sum();
        let waypoint_holds = TACTICAL_MAPS
            .iter()
            .flat_map(|map| map.team_routes)
            .flatten()
            .flat_map(|route| route.waypoints)
            .filter(|waypoint| waypoint.hold)
            .count();
        let mut route_holds = [0usize; 3];
        for hold in TACTICAL_MAPS
            .iter()
            .flat_map(|map| map.team_routes)
            .flatten()
            .map(|route| route.hold)
        {
            route_holds[match hold {
                RouteHold::Unspecified => 0,
                RouteHold::Enabled => 1,
                RouteHold::Point(_) => 2,
            }] += 1;
        }
        assert_eq!(routes, 246);
        assert_eq!(waypoints, 1_832);
        assert_eq!(waypoint_holds, 284);
        assert_eq!(route_holds, [152, 46, 48]);
        assert_eq!(
            TACTICAL_MAPS
                .iter()
                .filter(|map| map.annotation_confidence == Some("coarse-minimap-bounds"))
                .count(),
            13
        );
    }

    #[test]
    fn every_map_and_team_has_the_final_python_capacity() {
        for &(name, expected) in EXPECTED_TEAM_CAPACITIES {
            let map = tactical_map(name).unwrap();
            assert_eq!(
                map.total_capacity(TEAM_ONE),
                Some(expected),
                "{name} team 1"
            );
            assert_eq!(
                map.total_capacity(TEAM_TWO),
                Some(expected),
                "{name} team 2"
            );
        }
    }

    #[test]
    fn team_two_is_the_exact_reverse_except_for_himmelsdorf_rear_guard() {
        for map in TACTICAL_MAPS {
            let team_one = map.routes(TEAM_ONE).unwrap();
            let team_two = map.routes(TEAM_TWO).unwrap();
            assert_eq!(team_one.len(), team_two.len(), "{}", map.name);
            for (first, second) in team_one.iter().zip(team_two) {
                assert_eq!(first.id, second.id, "{}", map.name);
                assert_eq!(first.capacity, second.capacity, "{} {}", map.name, first.id);
                assert_eq!(first.risk, second.risk, "{} {}", map.name, first.id);
                assert_eq!(
                    first.role_weights, second.role_weights,
                    "{} {}",
                    map.name, first.id
                );
                if map.name == "04_himmelsdorf" && first.id == "rear_guard" {
                    assert_eq!(
                        first.waypoints,
                        &[TacticalWaypoint {
                            x: -80.0,
                            z: -270.0,
                            hold: true
                        }]
                    );
                    assert_eq!(
                        second.waypoints,
                        &[TacticalWaypoint {
                            x: 45.0,
                            z: 270.0,
                            hold: true
                        }]
                    );
                    continue;
                }
                assert_eq!(first.waypoints.len(), second.waypoints.len());
                for (forward, reverse) in first.waypoints.iter().zip(second.waypoints.iter().rev())
                {
                    assert_eq!(forward, reverse, "{} {}", map.name, first.id);
                }
            }
        }
    }

    #[test]
    fn capacity_weighted_slot_selection_is_stable() {
        for map in TACTICAL_MAPS {
            for team in [TEAM_ONE, TEAM_TWO] {
                let routes = map.routes(team).unwrap();
                let total = map.total_capacity(team).unwrap();
                let mut counts = vec![0usize; routes.len()];
                for slot in 0..total {
                    let selected = map.route_for_slot(team, slot).unwrap();
                    let repeated = route_for_slot(map.name, team, slot).unwrap();
                    assert!(std::ptr::eq(selected, repeated));
                    let index = routes
                        .iter()
                        .position(|route| std::ptr::eq(route, selected))
                        .unwrap();
                    counts[index] += 1;
                }
                assert_eq!(
                    counts,
                    routes
                        .iter()
                        .map(|route| route.capacity)
                        .collect::<Vec<_>>(),
                    "{} team {}",
                    map.name,
                    team
                );
                assert_eq!(
                    map.route_for_slot(team, total).unwrap().id,
                    routes[0].id,
                    "{} team {}",
                    map.name,
                    team
                );
            }
        }
        assert!(route_for_slot("01_karelia", 0, 0).is_none());
        assert!(route_for_slot("unknown", TEAM_ONE, 0).is_none());
    }

    #[test]
    fn serialized_routes_recover_their_exact_tactical_context() {
        for map in TACTICAL_MAPS {
            for team in [TEAM_ONE, TEAM_TWO] {
                for route in map.routes(team).unwrap() {
                    let waypoints = route
                        .waypoints
                        .iter()
                        .map(|point| (point.x, point.z))
                        .collect::<Vec<_>>();
                    let matched = match_route(team, route.id, &waypoints).unwrap_or_else(|| {
                        panic!("failed to recover {} team {} {}", map.name, team, route.id)
                    });
                    assert!(std::ptr::eq(matched.map, map));
                    assert!(std::ptr::eq(matched.route, route));
                    assert_eq!(matched.team, team);
                }
            }
        }
        assert!(match_route(TEAM_ONE, "west_ridge", &[(0.0, 0.0)]).is_none());
    }

    #[test]
    fn karelia_strategy_metadata_matches_latest_main() {
        let map = tactical_map("01_karelia").unwrap();
        let routes = map.routes(TEAM_ONE).unwrap();
        let west = routes
            .iter()
            .find(|route| route.id == "west_ridge")
            .unwrap();
        let middle = routes
            .iter()
            .find(|route| route.id == "middle_road")
            .unwrap();
        let east = routes
            .iter()
            .find(|route| route.id == "east_shelf")
            .unwrap();
        assert_eq!([west.capacity, middle.capacity, east.capacity], [5, 4, 6]);
        assert_eq!(west.role_weights.support, 0.78);
        assert_eq!(middle.role_weights.scout, 1.0);
        assert_eq!(east.role_weights.brawler, 1.0);

        let matched = MatchedTacticalRoute {
            map,
            team: TEAM_ONE,
            route: middle,
        };
        assert_eq!(matched.class_affinity("lightTank"), Some(1.0));
        assert_eq!(matched.class_affinity("heavyTank"), Some(0.02));
        assert_eq!(matched.enemy_base(), map.bases[1]);
    }

    #[test]
    fn final_python_scalar_aggregates_are_preserved() {
        let milli = |value: f64| (value * 1_000.0).round() as i64;
        let mut capacity_sum = 0i64;
        let mut map_name_bytes = 0i64;
        let mut route_id_bytes = 0i64;
        let mut route_id_ascii = 0i64;
        let mut risk_milli = 0i64;
        let mut role_milli = 0i64;
        let mut waypoint_x_milli = 0i64;
        let mut waypoint_z_milli = 0i64;
        let mut waypoint_abs_x_milli = 0i64;
        let mut waypoint_abs_z_milli = 0i64;
        let mut weighted_route_capacity = 0i64;
        let mut weighted_route_waypoints = 0i64;
        let mut weighted_route_risk = 0i64;
        let mut weighted_route_id_bytes = 0i64;
        let mut weighted_waypoint_x = 0i64;
        let mut weighted_waypoint_z = 0i64;
        let mut weighted_waypoint_hold = 0i64;
        let mut weighted_waypoint_abs_x = 0i64;
        let mut weighted_waypoint_abs_z = 0i64;
        let mut route_ordinal = 0i64;
        let mut waypoint_ordinal = 0i64;

        for map in TACTICAL_MAPS {
            map_name_bytes += map.name.len() as i64;
            for route in map.team_routes.into_iter().flatten() {
                route_ordinal += 1;
                let capacity = route.capacity as i64;
                let id_bytes = route.id.len() as i64;
                let risk = milli(route.risk);
                capacity_sum += capacity;
                route_id_bytes += id_bytes;
                route_id_ascii += route.id.bytes().map(i64::from).sum::<i64>();
                risk_milli += risk;
                role_milli += route
                    .role_weights
                    .values()
                    .into_iter()
                    .map(milli)
                    .sum::<i64>();
                weighted_route_capacity += route_ordinal * capacity;
                weighted_route_waypoints += route_ordinal * route.waypoints.len() as i64;
                weighted_route_risk += route_ordinal * risk;
                weighted_route_id_bytes += route_ordinal * id_bytes;

                for waypoint in route.waypoints {
                    waypoint_ordinal += 1;
                    let x = milli(waypoint.x);
                    let z = milli(waypoint.z);
                    waypoint_x_milli += x;
                    waypoint_z_milli += z;
                    waypoint_abs_x_milli += x.abs();
                    waypoint_abs_z_milli += z.abs();
                    weighted_waypoint_x += waypoint_ordinal * x;
                    weighted_waypoint_z += waypoint_ordinal * z;
                    weighted_waypoint_hold += waypoint_ordinal * i64::from(waypoint.hold);
                    weighted_waypoint_abs_x += waypoint_ordinal * x.abs();
                    weighted_waypoint_abs_z += waypoint_ordinal * z.abs();
                }
            }
        }

        assert_eq!(capacity_sum, 1_122);
        assert_eq!(map_name_bytes, 497);
        assert_eq!(route_id_bytes, 2_362);
        assert_eq!(route_id_ascii, 252_138);
        assert_eq!(risk_milli, 153_520);
        assert_eq!(role_milli, 721_900);
        assert_eq!(waypoint_x_milli, 23_319_980);
        assert_eq!(waypoint_z_milli, 8_392_380);
        assert_eq!(waypoint_abs_x_milli, 386_626_300);
        assert_eq!(waypoint_abs_z_milli, 393_346_620);
        assert_eq!(weighted_route_capacity, 140_769);
        assert_eq!(weighted_route_waypoints, 254_953);
        assert_eq!(weighted_route_risk, 18_288_050);
        assert_eq!(weighted_route_id_bytes, 262_388);
        assert_eq!(weighted_waypoint_x, 31_947_682_250);
        assert_eq!(weighted_waypoint_z, 4_332_662_750);
        assert_eq!(weighted_waypoint_hold, 244_342);
        assert_eq!(weighted_waypoint_abs_x, 334_366_109_250);
        assert_eq!(weighted_waypoint_abs_z, 355_912_996_750);
    }

    #[test]
    fn lookup_matches_python_map_name_normalization() {
        assert_eq!(
            tactical_map(r"spaces\01_KARELIA.XML").map(|map| map.name),
            Some("01_karelia")
        );
        assert_eq!(
            routes_for("maps/114_czech.xml", TEAM_TWO).map(<[TacticalRoute]>::len),
            Some(3)
        );
        assert!(tactical_map("").is_none());
    }
}
