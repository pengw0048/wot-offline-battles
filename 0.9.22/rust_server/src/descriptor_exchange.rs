//! Round-fenced admission for #1513 descriptor and destructible donations.
//!
//! The retail client owns the native item definitions and native destructible
//! identities.  It donates a connection-scoped vehicle catalog, then answers
//! one round-scoped descriptor request and (when requested by `battle_start`)
//! uploads the native destructible map in parts.  This module validates and
//! assembles those wire artifacts; it never makes the donor a simulation
//! authority.  A round that needs native destructibles cannot become ready
//! until the server reports a complete native-world installation.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Number, Value};
use thiserror::Error;

use crate::descriptor::{parse_projection_for, DescriptorError, ParsedDescriptor};
use crate::protocol::RoundId;
use crate::wire::{WireError, WireObject};

pub type DonorId = u32;

pub const MAX_CATALOG_VEHICLES: usize = 1_024;
pub const MAX_CATALOG_TAGS: usize = 32;
pub const MAX_REQUESTED_DESCRIPTORS: usize = 64;
pub const MAX_BUNDLE_PROJECTIONS: usize = 64;
pub const MAX_BUNDLE_FAILURES: usize = 64;
pub const MAX_DESTRUCTIBLE_PARTS: usize = 64;
pub const MAX_DESTRUCTIBLE_INSTANCES_PER_PART: usize = 2_000;
pub const MAX_DESTRUCTIBLE_INSTANCES: usize =
    MAX_DESTRUCTIBLE_PARTS * MAX_DESTRUCTIBLE_INSTANCES_PER_PART;
pub const MAX_DESTRUCTIBLE_RESOURCES: usize = 8_192;
pub const MAX_STRUCTURE_MODULES: usize = 256;

const MAX_VEHICLE_NAME_BYTES: usize = 64;
const MAX_TAG_BYTES: usize = 32;
const MAX_MAP_NAME_BYTES: usize = 96;
const MAX_RESOURCE_NAME_BYTES: usize = 512;
const MAX_ITEM_INDEX: u64 = 1_048_575;
const MAX_UNIT_VEHICLE_MASS: f64 = 1_000_000_000.0;
const MAX_SCALED_HEALTH: f64 = 1_000_000_000_000.0;
const MAX_ARMOR: f64 = 1_000_000_000.0;
const MAX_KINETIC_CORRECTION: f64 = 16.0;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CatalogVehicle {
    pub name: String,
    pub level: u8,
    /// Sorted, duplicate-free #1513 tags.
    pub tags: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DescriptorCatalog {
    vehicles: BTreeMap<String, CatalogVehicle>,
}

impl DescriptorCatalog {
    pub fn len(&self) -> usize {
        self.vehicles.len()
    }

    pub fn is_empty(&self) -> bool {
        self.vehicles.is_empty()
    }

    pub fn get(&self, name: &str) -> Option<&CatalogVehicle> {
        self.vehicles.get(name)
    }

    pub fn vehicles(&self) -> impl ExactSizeIterator<Item = &CatalogVehicle> {
        self.vehicles.values()
    }

    #[cfg(test)]
    pub(crate) fn from_test_vehicles(vehicles: impl IntoIterator<Item = CatalogVehicle>) -> Self {
        Self {
            vehicles: vehicles
                .into_iter()
                .map(|vehicle| (vehicle.name.clone(), vehicle))
                .collect(),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DescriptorGate {
    Collecting,
    Complete,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DestructibleGate {
    NotRequired,
    Collecting,
    AwaitingInstall,
    Complete,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExchangeFailure {
    DescriptorProjectionFailed,
    DescriptorTimeout,
    DescriptorDonorDisconnected,
    DestructibleMapIncomplete,
    DestructibleMapTimeout,
    DestructibleMapDonorDisconnected,
}

impl ExchangeFailure {
    pub fn wire_code(self) -> &'static str {
        match self {
            Self::DescriptorProjectionFailed => "descriptor_projection_failed",
            Self::DescriptorTimeout => "descriptor_timeout",
            Self::DescriptorDonorDisconnected => "descriptor_donor_disconnected",
            Self::DestructibleMapIncomplete => "destructible_map_incomplete",
            Self::DestructibleMapTimeout => "destructible_map_timeout",
            Self::DestructibleMapDonorDisconnected => "destructible_map_donor_disconnected",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExchangeStatus {
    Collecting,
    Ready,
    Failed(ExchangeFailure),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RoundSnapshot {
    pub round_id: RoundId,
    pub donor_id: DonorId,
    pub map_name: String,
    pub descriptor_gate: DescriptorGate,
    pub destructible_gate: DestructibleGate,
    pub descriptor_remaining: usize,
    pub destructible_parts_received: usize,
    pub destructible_parts_expected: Option<usize>,
    pub status: ExchangeStatus,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExchangeEvent {
    DescriptorPartAccepted { remaining: usize },
    DescriptorSetComplete,
    DestructiblePartAccepted { received: usize, expected: usize },
    DestructibleMapAssembled { instances: usize },
    DestructibleMapInstalled { instances: usize },
    Ready,
    Failed(ExchangeFailure),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct DestructibleSignature(pub [i64; 12]);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct DestructibleWireId {
    pub chunk_id: i64,
    pub item_index: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DestructibleResourceKind {
    Tree,
    Column,
    Fragile,
    Structure,
}

impl DestructibleResourceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Tree => "tree",
            Self::Column => "column",
            Self::Fragile => "fragile",
            Self::Structure => "structure",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct DestructibleResource {
    pub kind: DestructibleResourceKind,
    pub kinetic_correction: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DestructibleModule {
    pub scaled_health: f64,
    pub armor: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DestructibleInstance {
    pub signature: DestructibleSignature,
    pub wire: DestructibleWireId,
    pub scaled_health: Option<f64>,
    pub modules: Option<BTreeMap<u16, DestructibleModule>>,
    /// Exact normalized resource key used to bind this wire identity to the
    /// frozen #1513 kinetic law donated in `resources`.
    pub resource_name: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DestructibleMapDonation {
    pub round_id: RoundId,
    pub map_name: String,
    pub unit_vehicle_mass: f64,
    pub resources: BTreeMap<String, DestructibleResource>,
    /// Signature-sorted, with both signature and native wire identity unique.
    pub instances: Vec<DestructibleInstance>,
}

#[derive(Debug, Error)]
pub enum DescriptorExchangeError {
    #[error("expected {expected} message, got {actual}")]
    WrongMessageType {
        expected: &'static str,
        actual: String,
    },
    #[error("{message_type} contains unsupported field {field}")]
    UnknownField {
        message_type: &'static str,
        field: String,
    },
    #[error("{path}: {message}")]
    InvalidField { path: String, message: String },
    #[error("catalog donor {donor_id} is frozen by the active round")]
    CatalogFrozen { donor_id: DonorId },
    #[error("donor {donor_id} has no admitted descriptor catalog")]
    MissingCatalog { donor_id: DonorId },
    #[error("requested vehicle {vehicle} is absent from donor {donor_id}'s catalog")]
    VehicleAbsentFromCatalog { donor_id: DonorId, vehicle: String },
    #[error("round {active_round} is still collecting native prerequisites")]
    RoundStillActive { active_round: RoundId },
    #[error("new round {received} must be newer than {previous}")]
    NonMonotonicRound {
        previous: RoundId,
        received: RoundId,
    },
    #[error("there is no active descriptor exchange round")]
    NoActiveRound,
    #[error("message round {received} does not match active round {expected}")]
    WrongRound {
        expected: RoundId,
        received: RoundId,
    },
    #[error("donor {received} does not match round donor {expected}")]
    WrongDonor {
        expected: DonorId,
        received: DonorId,
    },
    #[error("message is not valid while exchange status is {status:?}")]
    InvalidStatus { status: ExchangeStatus },
    #[error("descriptor donation is already complete")]
    DescriptorAlreadyComplete,
    #[error("destructible donation is not required for this round")]
    DestructibleMapNotRequired,
    #[error("destructible map is already assembled or installed")]
    DestructibleMapAlreadyComplete,
    #[error("descriptor request list does not match the active round")]
    DescriptorRequestMismatch,
    #[error("descriptor projection {vehicle} was donated more than once")]
    DuplicateProjection { vehicle: String },
    #[error("descriptor projection {vehicle} conflicts with its reported failure")]
    ProjectionFailureConflict { vehicle: String },
    #[error("descriptor {vehicle} disagrees with its admitted catalog row")]
    CatalogDescriptorMismatch { vehicle: String },
    #[error("descriptor {vehicle} is invalid: {source}")]
    InvalidDescriptor {
        vehicle: String,
        #[source]
        source: DescriptorError,
    },
    #[error("destructible part {part} was donated more than once")]
    DuplicateDestructiblePart { part: usize },
    #[error("destructible parts disagree on {field}")]
    ConflictingDestructiblePart { field: &'static str },
    #[error("destructible signature was donated more than once")]
    DuplicateDestructibleSignature,
    #[error("destructible native wire identity was donated more than once")]
    DuplicateDestructibleWire,
    #[error("destructible map has not finished assembling")]
    DestructibleMapNotAssembled,
    #[error("native install report is for map {received}, expected {expected}")]
    WrongInstallMap { expected: String, received: String },
    #[error("wire object construction failed: {0}")]
    Wire(#[from] WireError),
}

struct DestructibleAssembly {
    expected_parts: Option<usize>,
    unit_vehicle_mass: Option<f64>,
    resources: BTreeMap<String, DestructibleResource>,
    instances: BTreeMap<DestructibleSignature, DestructibleInstance>,
    wires: BTreeSet<DestructibleWireId>,
    parts_seen: BTreeSet<usize>,
    admitted_parts: BTreeMap<usize, DestructiblePart>,
    completed: Option<DestructibleMapDonation>,
}

#[derive(Clone, Debug, PartialEq)]
struct DestructiblePart {
    parts: usize,
    unit_vehicle_mass: f64,
    resources: BTreeMap<String, DestructibleResource>,
    instances: Vec<DestructibleInstance>,
}

impl DestructibleAssembly {
    fn new() -> Self {
        Self {
            expected_parts: None,
            unit_vehicle_mass: None,
            resources: BTreeMap::new(),
            instances: BTreeMap::new(),
            wires: BTreeSet::new(),
            parts_seen: BTreeSet::new(),
            admitted_parts: BTreeMap::new(),
            completed: None,
        }
    }
}

struct ActiveRound {
    round_id: RoundId,
    donor_id: DonorId,
    map_name: String,
    requested: Vec<String>,
    pending: BTreeSet<String>,
    failures: BTreeSet<String>,
    descriptors: BTreeMap<String, ParsedDescriptor>,
    descriptor_gate: DescriptorGate,
    destructible_gate: DestructibleGate,
    destructibles: DestructibleAssembly,
    status: ExchangeStatus,
}

impl ActiveRound {
    fn refresh_status(&mut self) {
        if matches!(self.status, ExchangeStatus::Failed(_)) {
            return;
        }
        self.status = if self.descriptor_gate == DescriptorGate::Complete
            && matches!(
                self.destructible_gate,
                DestructibleGate::NotRequired | DestructibleGate::Complete
            ) {
            ExchangeStatus::Ready
        } else {
            ExchangeStatus::Collecting
        };
    }

    fn fail(&mut self, failure: ExchangeFailure) -> ExchangeEvent {
        self.status = ExchangeStatus::Failed(failure);
        ExchangeEvent::Failed(failure)
    }

    fn snapshot(&self) -> RoundSnapshot {
        RoundSnapshot {
            round_id: self.round_id,
            donor_id: self.donor_id,
            map_name: self.map_name.clone(),
            descriptor_gate: self.descriptor_gate,
            destructible_gate: self.destructible_gate,
            descriptor_remaining: self.pending.len(),
            destructible_parts_received: self.destructibles.parts_seen.len(),
            destructible_parts_expected: self.destructibles.expected_parts,
            status: self.status,
        }
    }
}

#[derive(Default)]
pub struct DescriptorExchange {
    catalogs: BTreeMap<DonorId, DescriptorCatalog>,
    active: Option<ActiveRound>,
}

impl DescriptorExchange {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn catalog(&self, donor_id: DonorId) -> Option<&DescriptorCatalog> {
        self.catalogs.get(&donor_id)
    }

    pub fn snapshot(&self) -> Option<RoundSnapshot> {
        self.active.as_ref().map(ActiveRound::snapshot)
    }

    pub fn descriptors(&self) -> Option<&BTreeMap<String, ParsedDescriptor>> {
        let round = self.active.as_ref()?;
        (round.descriptor_gate == DescriptorGate::Complete).then_some(&round.descriptors)
    }

    pub fn destructible_map(&self) -> Option<&DestructibleMapDonation> {
        self.active.as_ref()?.destructibles.completed.as_ref()
    }

    /// Admit the connection-scoped `descriptor_catalog` sent after welcome.
    pub fn admit_catalog(
        &mut self,
        donor_id: DonorId,
        message: &WireObject,
    ) -> Result<usize, DescriptorExchangeError> {
        if self.active.as_ref().is_some_and(|round| {
            round.donor_id == donor_id && round.status == ExchangeStatus::Collecting
        }) {
            return Err(DescriptorExchangeError::CatalogFrozen { donor_id });
        }
        let catalog = parse_catalog(message)?;
        let count = catalog.len();
        self.catalogs.insert(donor_id, catalog);
        Ok(count)
    }

    /// Start one native-donation exchange. Required names are sorted on the
    /// generated request, matching the Python server's deterministic order.
    pub fn begin_round(
        &mut self,
        round_id: RoundId,
        donor_id: DonorId,
        map_name: impl Into<String>,
        required_names: impl IntoIterator<Item = String>,
        need_destructible_map: bool,
    ) -> Result<Option<WireObject>, DescriptorExchangeError> {
        if round_id == 0 {
            return Err(invalid("$.round_id", "must be positive"));
        }
        if let Some(active) = self.active.as_ref() {
            if active.status == ExchangeStatus::Collecting {
                return Err(DescriptorExchangeError::RoundStillActive {
                    active_round: active.round_id,
                });
            }
            if round_id <= active.round_id {
                return Err(DescriptorExchangeError::NonMonotonicRound {
                    previous: active.round_id,
                    received: round_id,
                });
            }
        }
        let map_name = map_name.into();
        validate_map_name(&map_name, "$.map")?;
        let catalog = self
            .catalogs
            .get(&donor_id)
            .ok_or(DescriptorExchangeError::MissingCatalog { donor_id })?;

        let mut names = BTreeSet::new();
        for name in required_names {
            validate_vehicle_name(&name, "$.names[]")?;
            if !names.insert(name.clone()) {
                return Err(invalid("$.names", format!("duplicate vehicle {name:?}")));
            }
            if catalog.get(&name).is_none() {
                return Err(DescriptorExchangeError::VehicleAbsentFromCatalog {
                    donor_id,
                    vehicle: name,
                });
            }
        }
        if names.len() > MAX_REQUESTED_DESCRIPTORS {
            return Err(invalid(
                "$.names",
                format!("exceeds {MAX_REQUESTED_DESCRIPTORS} vehicles"),
            ));
        }
        let requested: Vec<_> = names.into_iter().collect();
        let descriptor_gate = if requested.is_empty() {
            DescriptorGate::Complete
        } else {
            DescriptorGate::Collecting
        };
        let destructible_gate = if need_destructible_map {
            DestructibleGate::Collecting
        } else {
            DestructibleGate::NotRequired
        };
        let mut round = ActiveRound {
            round_id,
            donor_id,
            map_name,
            pending: requested.iter().cloned().collect(),
            requested,
            failures: BTreeSet::new(),
            descriptors: BTreeMap::new(),
            descriptor_gate,
            destructible_gate,
            destructibles: DestructibleAssembly::new(),
            status: ExchangeStatus::Collecting,
        };
        round.refresh_status();
        let request = if round.requested.is_empty() {
            None
        } else {
            Some(descriptor_request(round_id, &round.requested)?)
        };
        self.active = Some(round);
        Ok(request)
    }

    pub fn admit_descriptor_bundle(
        &mut self,
        donor_id: DonorId,
        message: &WireObject,
    ) -> Result<ExchangeEvent, DescriptorExchangeError> {
        require_kind(message, "descriptor_bundle")?;
        ensure_fields(
            message,
            "descriptor_bundle",
            &[
                "type",
                "round_id",
                "requested",
                "failures",
                "complete",
                "projections",
            ],
        )?;
        let received_round = exact_round(message.get("round_id"), "$.round_id")?;
        self.active_ref(received_round, donor_id)?;
        let catalog = self
            .catalogs
            .get(&donor_id)
            .cloned()
            .ok_or(DescriptorExchangeError::MissingCatalog { donor_id })?;
        let round = self.active_mut(received_round, donor_id)?;
        if round.descriptor_gate == DescriptorGate::Complete {
            return Err(DescriptorExchangeError::DescriptorAlreadyComplete);
        }

        let requested = string_list(
            message.get("requested"),
            "$.requested",
            1,
            MAX_REQUESTED_DESCRIPTORS,
            validate_vehicle_name,
        )?;
        if requested != round.requested {
            return Err(DescriptorExchangeError::DescriptorRequestMismatch);
        }
        let failures = string_list(
            message.get("failures"),
            "$.failures",
            0,
            MAX_BUNDLE_FAILURES,
            validate_vehicle_name,
        )?;
        let complete = message
            .get("complete")
            .and_then(Value::as_bool)
            .ok_or_else(|| invalid("$.complete", "must be a boolean"))?;
        let projections = message
            .get("projections")
            .and_then(Value::as_object)
            .ok_or_else(|| invalid("$.projections", "must be an object"))?;
        if projections.len() > MAX_BUNDLE_PROJECTIONS {
            return Err(invalid(
                "$.projections",
                format!("exceeds {MAX_BUNDLE_PROJECTIONS} entries"),
            ));
        }

        let wanted: BTreeSet<_> = round.requested.iter().map(String::as_str).collect();
        let failure_set: BTreeSet<_> = failures.iter().map(String::as_str).collect();
        for failure in &failures {
            if !wanted.contains(failure.as_str()) {
                return Err(invalid(
                    "$.failures",
                    format!("unsolicited vehicle {failure:?}"),
                ));
            }
            if round.descriptors.contains_key(failure) {
                return Err(DescriptorExchangeError::ProjectionFailureConflict {
                    vehicle: failure.clone(),
                });
            }
        }

        // Parse the whole bundle before mutating round state.
        let mut admitted = BTreeMap::new();
        for (name, projection) in projections {
            validate_vehicle_name(name, "$.projections.<key>")?;
            if !wanted.contains(name.as_str()) {
                return Err(invalid(
                    "$.projections",
                    format!("unsolicited vehicle {name:?}"),
                ));
            }
            if failure_set.contains(name.as_str()) || round.failures.contains(name) {
                return Err(DescriptorExchangeError::ProjectionFailureConflict {
                    vehicle: name.clone(),
                });
            }
            if round.descriptors.contains_key(name) {
                return Err(DescriptorExchangeError::DuplicateProjection {
                    vehicle: name.clone(),
                });
            }
            let parsed = parse_projection_for(name, projection).map_err(|source| {
                DescriptorExchangeError::InvalidDescriptor {
                    vehicle: name.clone(),
                    source,
                }
            })?;
            let catalog_row = catalog
                .get(name)
                .expect("requested vehicle was catalog-fenced at round start");
            if parsed.level != catalog_row.level || parsed.tags != catalog_row.tags {
                return Err(DescriptorExchangeError::CatalogDescriptorMismatch {
                    vehicle: name.clone(),
                });
            }
            admitted.insert(name.clone(), parsed);
        }

        for failure in failures {
            round.failures.insert(failure);
        }
        for (name, descriptor) in admitted {
            round.pending.remove(&name);
            round.descriptors.insert(name, descriptor);
        }
        if !complete {
            return Ok(ExchangeEvent::DescriptorPartAccepted {
                remaining: round.pending.len(),
            });
        }
        if !round.failures.is_empty() || !round.pending.is_empty() {
            return Ok(round.fail(ExchangeFailure::DescriptorProjectionFailed));
        }
        round.descriptor_gate = DescriptorGate::Complete;
        round.refresh_status();
        Ok(if round.status == ExchangeStatus::Ready {
            ExchangeEvent::Ready
        } else {
            ExchangeEvent::DescriptorSetComplete
        })
    }

    pub fn admit_destructible_map(
        &mut self,
        donor_id: DonorId,
        message: &WireObject,
    ) -> Result<ExchangeEvent, DescriptorExchangeError> {
        require_kind(message, "destructible_map")?;
        ensure_fields(
            message,
            "destructible_map",
            &[
                "type",
                "round_id",
                "map",
                "part",
                "parts",
                "unit_vehicle_mass",
                "resources",
                "instances",
            ],
        )?;
        let received_round = exact_round(message.get("round_id"), "$.round_id")?;
        let round = self.active_mut(received_round, donor_id)?;
        match round.destructible_gate {
            DestructibleGate::NotRequired => {
                return Err(DescriptorExchangeError::DestructibleMapNotRequired)
            }
            DestructibleGate::AwaitingInstall | DestructibleGate::Complete => {
                return Err(DescriptorExchangeError::DestructibleMapAlreadyComplete)
            }
            DestructibleGate::Collecting => {}
        }
        let map_name = required_string(message.get("map"), "$.map")?;
        validate_map_name(map_name, "$.map")?;
        if map_name != round.map_name {
            return Err(invalid(
                "$.map",
                format!("expected {:?}, got {map_name:?}", round.map_name),
            ));
        }
        let part = exact_usize(message.get("part"), "$.part", 0, MAX_DESTRUCTIBLE_PARTS - 1)?;
        let parts = exact_usize(message.get("parts"), "$.parts", 1, MAX_DESTRUCTIBLE_PARTS)?;
        if part >= parts {
            return Err(invalid("$.part", "must be smaller than $.parts"));
        }
        let unit_mass = finite_number(
            message.get("unit_vehicle_mass"),
            "$.unit_vehicle_mass",
            f64::MIN_POSITIVE,
            MAX_UNIT_VEHICLE_MASS,
        )?;
        let parsed_resources = parse_resources(message.get("resources"))?;
        let parsed_instances = parse_instances(message.get("instances"))?;

        let assembly = &mut round.destructibles;
        let admitted_part = DestructiblePart {
            parts,
            unit_vehicle_mass: unit_mass,
            resources: parsed_resources.clone(),
            instances: parsed_instances.clone(),
        };
        if let Some(previous) = assembly.admitted_parts.get(&part) {
            if previous == &admitted_part {
                return Ok(ExchangeEvent::DestructiblePartAccepted {
                    received: assembly.parts_seen.len(),
                    expected: parts,
                });
            }
            return Err(DescriptorExchangeError::DuplicateDestructiblePart { part });
        }
        if let Some(expected) = assembly.expected_parts {
            if expected != parts {
                return Err(DescriptorExchangeError::ConflictingDestructiblePart {
                    field: "parts",
                });
            }
        }
        if let Some(expected) = assembly.unit_vehicle_mass {
            if expected.to_bits() != unit_mass.to_bits() {
                return Err(DescriptorExchangeError::ConflictingDestructiblePart {
                    field: "unit_vehicle_mass",
                });
            }
        }
        let new_resource_count = parsed_resources
            .keys()
            .filter(|name| !assembly.resources.contains_key(*name))
            .count();
        if assembly.resources.len() + new_resource_count > MAX_DESTRUCTIBLE_RESOURCES {
            return Err(invalid(
                "$.resources",
                format!("assembled map exceeds {MAX_DESTRUCTIBLE_RESOURCES} resources"),
            ));
        }
        for (name, resource) in &parsed_resources {
            if let Some(existing) = assembly.resources.get(name) {
                if existing != resource {
                    return Err(DescriptorExchangeError::ConflictingDestructiblePart {
                        field: "resources",
                    });
                }
            }
        }
        if assembly.instances.len() + parsed_instances.len() > MAX_DESTRUCTIBLE_INSTANCES {
            return Err(invalid(
                "$.instances",
                format!("assembled map exceeds {MAX_DESTRUCTIBLE_INSTANCES} instances"),
            ));
        }
        for instance in &parsed_instances {
            if assembly.instances.contains_key(&instance.signature) {
                return Err(DescriptorExchangeError::DuplicateDestructibleSignature);
            }
            if assembly.wires.contains(&instance.wire) {
                return Err(DescriptorExchangeError::DuplicateDestructibleWire);
            }
        }

        assembly.expected_parts = Some(parts);
        assembly.unit_vehicle_mass = Some(unit_mass);
        assembly.parts_seen.insert(part);
        assembly.admitted_parts.insert(part, admitted_part);
        for (name, resource) in parsed_resources {
            assembly.resources.entry(name).or_insert(resource);
        }
        for instance in parsed_instances {
            assembly.wires.insert(instance.wire);
            assembly.instances.insert(instance.signature, instance);
        }
        if assembly.parts_seen.len() != parts {
            return Ok(ExchangeEvent::DestructiblePartAccepted {
                received: assembly.parts_seen.len(),
                expected: parts,
            });
        }
        if assembly.instances.is_empty() {
            return Ok(round.fail(ExchangeFailure::DestructibleMapIncomplete));
        }
        validate_completed_destructible_map(&assembly.resources, assembly.instances.values())?;
        let donation = DestructibleMapDonation {
            round_id: round.round_id,
            map_name: round.map_name.clone(),
            unit_vehicle_mass: assembly
                .unit_vehicle_mass
                .expect("at least one admitted part has unit mass"),
            resources: assembly.resources.clone(),
            instances: assembly.instances.values().cloned().collect(),
        };
        let count = donation.instances.len();
        assembly.completed = Some(donation);
        round.destructible_gate = DestructibleGate::AwaitingInstall;
        Ok(ExchangeEvent::DestructibleMapAssembled { instances: count })
    }

    /// Complete the native gate only after the baked-world installer proves
    /// exact coverage. Merely receiving a JSON map never grants authority.
    pub fn confirm_destructible_install(
        &mut self,
        round_id: RoundId,
        map_name: &str,
        expected_instances: usize,
        installed_instances: usize,
    ) -> Result<ExchangeEvent, DescriptorExchangeError> {
        let round = self.active_mut_round(round_id)?;
        if map_name != round.map_name {
            return Err(DescriptorExchangeError::WrongInstallMap {
                expected: round.map_name.clone(),
                received: map_name.to_owned(),
            });
        }
        if round.destructible_gate != DestructibleGate::AwaitingInstall {
            return Err(DescriptorExchangeError::DestructibleMapNotAssembled);
        }
        let donated = round
            .destructibles
            .completed
            .as_ref()
            .expect("awaiting-install gate has a completed donation")
            .instances
            .len();
        if expected_instances == 0
            || donated != expected_instances
            || installed_instances != expected_instances
        {
            return Ok(round.fail(ExchangeFailure::DestructibleMapIncomplete));
        }
        round.destructible_gate = DestructibleGate::Complete;
        round.refresh_status();
        Ok(if round.status == ExchangeStatus::Ready {
            ExchangeEvent::Ready
        } else {
            ExchangeEvent::DestructibleMapInstalled {
                instances: installed_instances,
            }
        })
    }

    pub fn timeout(&mut self, round_id: RoundId) -> Result<ExchangeEvent, DescriptorExchangeError> {
        let round = self.active_mut_round(round_id)?;
        let failure = if round.descriptor_gate != DescriptorGate::Complete {
            ExchangeFailure::DescriptorTimeout
        } else if !matches!(
            round.destructible_gate,
            DestructibleGate::NotRequired | DestructibleGate::Complete
        ) {
            ExchangeFailure::DestructibleMapTimeout
        } else {
            return Err(DescriptorExchangeError::InvalidStatus {
                status: round.status,
            });
        };
        Ok(round.fail(failure))
    }

    /// Remove a connection-scoped catalog and fail a pending round when its
    /// sole native donor leaves, matching the Python server's hard boundary.
    pub fn donor_disconnected(&mut self, donor_id: DonorId) -> Option<ExchangeEvent> {
        self.catalogs.remove(&donor_id);
        let round = self.active.as_mut()?;
        if round.donor_id != donor_id || round.status != ExchangeStatus::Collecting {
            return None;
        }
        let failure = if round.descriptor_gate != DescriptorGate::Complete {
            ExchangeFailure::DescriptorDonorDisconnected
        } else {
            ExchangeFailure::DestructibleMapDonorDisconnected
        };
        Some(round.fail(failure))
    }

    fn active_mut(
        &mut self,
        round_id: RoundId,
        donor_id: DonorId,
    ) -> Result<&mut ActiveRound, DescriptorExchangeError> {
        let round = self.active_mut_round(round_id)?;
        if donor_id != round.donor_id {
            return Err(DescriptorExchangeError::WrongDonor {
                expected: round.donor_id,
                received: donor_id,
            });
        }
        Ok(round)
    }

    fn active_ref(
        &self,
        round_id: RoundId,
        donor_id: DonorId,
    ) -> Result<&ActiveRound, DescriptorExchangeError> {
        let round = self
            .active
            .as_ref()
            .ok_or(DescriptorExchangeError::NoActiveRound)?;
        if round_id != round.round_id {
            return Err(DescriptorExchangeError::WrongRound {
                expected: round.round_id,
                received: round_id,
            });
        }
        if donor_id != round.donor_id {
            return Err(DescriptorExchangeError::WrongDonor {
                expected: round.donor_id,
                received: donor_id,
            });
        }
        if round.status != ExchangeStatus::Collecting {
            return Err(DescriptorExchangeError::InvalidStatus {
                status: round.status,
            });
        }
        Ok(round)
    }

    fn active_mut_round(
        &mut self,
        round_id: RoundId,
    ) -> Result<&mut ActiveRound, DescriptorExchangeError> {
        let round = self
            .active
            .as_mut()
            .ok_or(DescriptorExchangeError::NoActiveRound)?;
        if round_id != round.round_id {
            return Err(DescriptorExchangeError::WrongRound {
                expected: round.round_id,
                received: round_id,
            });
        }
        if round.status != ExchangeStatus::Collecting {
            return Err(DescriptorExchangeError::InvalidStatus {
                status: round.status,
            });
        }
        Ok(round)
    }
}

pub fn parse_catalog(message: &WireObject) -> Result<DescriptorCatalog, DescriptorExchangeError> {
    require_kind(message, "descriptor_catalog")?;
    ensure_fields(message, "descriptor_catalog", &["type", "vehicles"])?;
    let rows = message
        .get("vehicles")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("$.vehicles", "must be an array"))?;
    if rows.is_empty() || rows.len() > MAX_CATALOG_VEHICLES {
        return Err(invalid(
            "$.vehicles",
            format!("must contain 1..={MAX_CATALOG_VEHICLES} rows"),
        ));
    }
    let mut vehicles = BTreeMap::new();
    for (index, value) in rows.iter().enumerate() {
        let path = format!("$.vehicles[{index}]");
        let row = value
            .as_object()
            .ok_or_else(|| invalid(&path, "must be an object"))?;
        ensure_map_fields(row, "descriptor_catalog row", &["name", "level", "tags"])?;
        let name = required_string(row.get("name"), &format!("{path}.name"))?;
        validate_vehicle_name(name, &format!("{path}.name"))?;
        let level = exact_usize(row.get("level"), &format!("{path}.level"), 1, 10)? as u8;
        let tags = string_list(
            row.get("tags"),
            &format!("{path}.tags"),
            0,
            MAX_CATALOG_TAGS,
            validate_tag,
        )?;
        let mut sorted_tags = tags;
        sorted_tags.sort();
        if vehicles
            .insert(
                name.to_owned(),
                CatalogVehicle {
                    name: name.to_owned(),
                    level,
                    tags: sorted_tags,
                },
            )
            .is_some()
        {
            return Err(invalid(&format!("{path}.name"), "duplicate vehicle"));
        }
    }
    Ok(DescriptorCatalog { vehicles })
}

pub fn descriptor_request(
    round_id: RoundId,
    names: &[String],
) -> Result<WireObject, DescriptorExchangeError> {
    if round_id == 0 {
        return Err(invalid("$.round_id", "must be positive"));
    }
    if names.is_empty() || names.len() > MAX_REQUESTED_DESCRIPTORS {
        return Err(invalid(
            "$.names",
            format!("must contain 1..={MAX_REQUESTED_DESCRIPTORS} vehicles"),
        ));
    }
    let mut previous: Option<&str> = None;
    let mut values = Vec::with_capacity(names.len());
    for name in names {
        validate_vehicle_name(name, "$.names[]")?;
        if previous.is_some_and(|previous| previous >= name.as_str()) {
            return Err(invalid(
                "$.names",
                "must be strictly sorted without duplicates",
            ));
        }
        previous = Some(name);
        values.push(Value::String(name.clone()));
    }
    let mut fields = Map::new();
    fields.insert("round_id".to_owned(), Value::Number(Number::from(round_id)));
    fields.insert("names".to_owned(), Value::Array(values));
    Ok(WireObject::with_fields("descriptor_request", fields)?)
}

fn parse_resources(
    value: Option<&Value>,
) -> Result<BTreeMap<String, DestructibleResource>, DescriptorExchangeError> {
    let resources = value
        .and_then(Value::as_object)
        .ok_or_else(|| invalid("$.resources", "must be an object"))?;
    if resources.len() > MAX_DESTRUCTIBLE_RESOURCES {
        return Err(invalid(
            "$.resources",
            format!("exceeds {MAX_DESTRUCTIBLE_RESOURCES} entries"),
        ));
    }
    let mut parsed = BTreeMap::new();
    for (name, value) in resources {
        validate_resource_name(name, "$.resources.<key>")?;
        let path = format!("$.resources[{name:?}]");
        let fields = value
            .as_object()
            .ok_or_else(|| invalid(&path, "must be an object"))?;
        ensure_map_fields(
            fields,
            "destructible resource",
            &["destr_type", "kinetic_correction"],
        )?;
        let kind = match required_string(fields.get("destr_type"), &format!("{path}.destr_type"))? {
            "tree" => DestructibleResourceKind::Tree,
            "column" => DestructibleResourceKind::Column,
            "fragile" => DestructibleResourceKind::Fragile,
            "structure" => DestructibleResourceKind::Structure,
            _ => {
                return Err(invalid(
                    format!("{path}.destr_type"),
                    "must be tree, column, fragile, or structure",
                ))
            }
        };
        let kinetic_correction = finite_number(
            fields.get("kinetic_correction"),
            &format!("{path}.kinetic_correction"),
            -MAX_KINETIC_CORRECTION,
            MAX_KINETIC_CORRECTION,
        )?;
        parsed.insert(
            name.clone(),
            DestructibleResource {
                kind,
                kinetic_correction,
            },
        );
    }
    Ok(parsed)
}

fn parse_instances(
    value: Option<&Value>,
) -> Result<Vec<DestructibleInstance>, DescriptorExchangeError> {
    let rows = value
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("$.instances", "must be an array"))?;
    if rows.len() > MAX_DESTRUCTIBLE_INSTANCES_PER_PART {
        return Err(invalid(
            "$.instances",
            format!("part exceeds {MAX_DESTRUCTIBLE_INSTANCES_PER_PART} instances"),
        ));
    }
    let mut parsed = Vec::with_capacity(rows.len());
    let mut signatures = BTreeSet::new();
    let mut wires = BTreeSet::new();
    for (index, value) in rows.iter().enumerate() {
        let path = format!("$.instances[{index}]");
        let row = value
            .as_array()
            .filter(|row| row.len() == 6)
            .ok_or_else(|| invalid(&path, "must be a six-item array"))?;
        let raw_signature = row[0]
            .as_array()
            .filter(|signature| signature.len() == 12)
            .ok_or_else(|| invalid(format!("{path}[0]"), "must contain 12 integers"))?;
        let mut signature = [0_i64; 12];
        for (component, value) in raw_signature.iter().enumerate() {
            signature[component] = exact_i64(
                Some(value),
                &format!("{path}[0][{component}]"),
                i32::MIN as i64,
                u32::MAX as i64,
            )?;
        }
        let signature = DestructibleSignature(signature);
        let chunk_id = exact_i64(
            Some(&row[1]),
            &format!("{path}[1]"),
            i32::MIN as i64,
            u32::MAX as i64,
        )?;
        let item_index = exact_usize(
            Some(&row[2]),
            &format!("{path}[2]"),
            0,
            MAX_ITEM_INDEX as usize,
        )? as u32;
        let wire = DestructibleWireId {
            chunk_id,
            item_index,
        };
        let scaled_health = if row[3].is_null() {
            None
        } else {
            Some(finite_number(
                Some(&row[3]),
                &format!("{path}[3]"),
                f64::MIN_POSITIVE,
                MAX_SCALED_HEALTH,
            )?)
        };
        let modules = parse_modules(&row[4], &format!("{path}[4]"))?;
        let resource_name = required_string(Some(&row[5]), &format!("{path}[5]"))?;
        validate_resource_name(resource_name, &format!("{path}[5]"))?;
        if scaled_health.is_some() && modules.is_some() {
            return Err(invalid(
                &path,
                "scaled health and module health are mutually exclusive",
            ));
        }
        if !signatures.insert(signature) {
            return Err(DescriptorExchangeError::DuplicateDestructibleSignature);
        }
        if !wires.insert(wire) {
            return Err(DescriptorExchangeError::DuplicateDestructibleWire);
        }
        parsed.push(DestructibleInstance {
            signature,
            wire,
            scaled_health,
            modules,
            resource_name: resource_name.to_owned(),
        });
    }
    Ok(parsed)
}

fn validate_completed_destructible_map<'a>(
    resources: &BTreeMap<String, DestructibleResource>,
    instances: impl IntoIterator<Item = &'a DestructibleInstance>,
) -> Result<(), DescriptorExchangeError> {
    for instance in instances {
        let path = format!(
            "$.instances[wire=({}, {})]",
            instance.wire.chunk_id, instance.wire.item_index
        );
        let resource = resources.get(&instance.resource_name).ok_or_else(|| {
            invalid(
                format!("{path}[5]"),
                "does not name a donated destructible resource",
            )
        })?;
        let valid_health_shape = match resource.kind {
            DestructibleResourceKind::Structure => {
                instance.scaled_health.is_none() && instance.modules.is_some()
            }
            DestructibleResourceKind::Tree
            | DestructibleResourceKind::Column
            | DestructibleResourceKind::Fragile => {
                instance.scaled_health.is_some() && instance.modules.is_none()
            }
        };
        if !valid_health_shape {
            return Err(invalid(
                path,
                "health payload does not match the linked resource kind",
            ));
        }
    }
    Ok(())
}

fn parse_modules(
    value: &Value,
    path: &str,
) -> Result<Option<BTreeMap<u16, DestructibleModule>>, DescriptorExchangeError> {
    if value.is_null() {
        return Ok(None);
    }
    let values = value
        .as_object()
        .ok_or_else(|| invalid(path, "must be null or an object"))?;
    if values.is_empty() || values.len() > MAX_STRUCTURE_MODULES {
        return Err(invalid(
            path,
            format!("must contain 1..={MAX_STRUCTURE_MODULES} modules"),
        ));
    }
    let mut modules = BTreeMap::new();
    for (raw_kind, value) in values {
        let material_kind = raw_kind
            .parse::<u16>()
            .ok()
            .filter(|kind| kind.to_string() == *raw_kind)
            .ok_or_else(|| invalid(path, format!("invalid material kind {raw_kind:?}")))?;
        let pair = value
            .as_array()
            .filter(|pair| pair.len() == 2)
            .ok_or_else(|| {
                invalid(
                    format!("{path}[{raw_kind:?}]"),
                    "must be [scaled_health, armor]",
                )
            })?;
        let scaled_health = finite_number(
            Some(&pair[0]),
            &format!("{path}[{raw_kind:?}][0]"),
            f64::MIN_POSITIVE,
            MAX_SCALED_HEALTH,
        )?;
        let armor = finite_number(
            Some(&pair[1]),
            &format!("{path}[{raw_kind:?}][1]"),
            0.0,
            MAX_ARMOR,
        )?;
        modules.insert(
            material_kind,
            DestructibleModule {
                scaled_health,
                armor,
            },
        );
    }
    Ok(Some(modules))
}

fn require_kind(
    message: &WireObject,
    expected: &'static str,
) -> Result<(), DescriptorExchangeError> {
    if message.kind() == expected {
        Ok(())
    } else {
        Err(DescriptorExchangeError::WrongMessageType {
            expected,
            actual: message.kind().to_owned(),
        })
    }
}

fn ensure_fields(
    message: &WireObject,
    message_type: &'static str,
    allowed: &[&str],
) -> Result<(), DescriptorExchangeError> {
    ensure_map_fields(message.fields(), message_type, allowed)
}

fn ensure_map_fields(
    fields: &Map<String, Value>,
    message_type: &'static str,
    allowed: &[&str],
) -> Result<(), DescriptorExchangeError> {
    for field in fields.keys() {
        if !allowed.contains(&field.as_str()) {
            return Err(DescriptorExchangeError::UnknownField {
                message_type,
                field: field.clone(),
            });
        }
    }
    Ok(())
}

fn exact_round(value: Option<&Value>, path: &str) -> Result<RoundId, DescriptorExchangeError> {
    exact_u64(value, path, 1, u64::MAX)
}

fn exact_usize(
    value: Option<&Value>,
    path: &str,
    minimum: usize,
    maximum: usize,
) -> Result<usize, DescriptorExchangeError> {
    let value = exact_u64(value, path, minimum as u64, maximum as u64)?;
    usize::try_from(value).map_err(|_| invalid(path, "integer does not fit this server"))
}

fn exact_u64(
    value: Option<&Value>,
    path: &str,
    minimum: u64,
    maximum: u64,
) -> Result<u64, DescriptorExchangeError> {
    let parsed = value
        .and_then(Value::as_u64)
        .filter(|value| (minimum..=maximum).contains(value))
        .ok_or_else(|| invalid(path, format!("must be an integer in {minimum}..={maximum}")))?;
    Ok(parsed)
}

fn exact_i64(
    value: Option<&Value>,
    path: &str,
    minimum: i64,
    maximum: i64,
) -> Result<i64, DescriptorExchangeError> {
    let parsed = value
        .and_then(|value| {
            value
                .as_i64()
                .or_else(|| value.as_u64().and_then(|v| i64::try_from(v).ok()))
        })
        .filter(|value| (minimum..=maximum).contains(value))
        .ok_or_else(|| invalid(path, format!("must be an integer in {minimum}..={maximum}")))?;
    Ok(parsed)
}

fn finite_number(
    value: Option<&Value>,
    path: &str,
    minimum: f64,
    maximum: f64,
) -> Result<f64, DescriptorExchangeError> {
    let parsed = value
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value >= minimum && *value <= maximum)
        .ok_or_else(|| {
            invalid(
                path,
                format!("must be a finite number in {minimum}..={maximum}"),
            )
        })?;
    Ok(parsed)
}

fn required_string<'a>(
    value: Option<&'a Value>,
    path: &str,
) -> Result<&'a str, DescriptorExchangeError> {
    value
        .and_then(Value::as_str)
        .ok_or_else(|| invalid(path, "must be a string"))
}

fn string_list(
    value: Option<&Value>,
    path: &str,
    minimum: usize,
    maximum: usize,
    validator: fn(&str, &str) -> Result<(), DescriptorExchangeError>,
) -> Result<Vec<String>, DescriptorExchangeError> {
    let values = value
        .and_then(Value::as_array)
        .filter(|values| (minimum..=maximum).contains(&values.len()))
        .ok_or_else(|| invalid(path, format!("must contain {minimum}..={maximum} strings")))?;
    let mut seen = BTreeSet::new();
    let mut result = Vec::with_capacity(values.len());
    for (index, value) in values.iter().enumerate() {
        let item_path = format!("{path}[{index}]");
        let text = required_string(Some(value), &item_path)?;
        validator(text, &item_path)?;
        if !seen.insert(text) {
            return Err(invalid(item_path, format!("duplicate string {text:?}")));
        }
        result.push(text.to_owned());
    }
    Ok(result)
}

fn validate_vehicle_name(value: &str, path: &str) -> Result<(), DescriptorExchangeError> {
    if value.is_empty()
        || value.len() > MAX_VEHICLE_NAME_BYTES
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b":_-".contains(&byte))
    {
        return Err(invalid(
            path,
            format!("must contain 1..={MAX_VEHICLE_NAME_BYTES} ASCII vehicle-name bytes"),
        ));
    }
    Ok(())
}

fn validate_tag(value: &str, path: &str) -> Result<(), DescriptorExchangeError> {
    if value.is_empty()
        || value.len() > MAX_TAG_BYTES
        || value.chars().any(char::is_control)
        || value.trim() != value
    {
        return Err(invalid(
            path,
            format!("must contain 1..={MAX_TAG_BYTES} printable bytes"),
        ));
    }
    Ok(())
}

fn validate_map_name(value: &str, path: &str) -> Result<(), DescriptorExchangeError> {
    if value.is_empty()
        || value.len() > MAX_MAP_NAME_BYTES
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    {
        return Err(invalid(
            path,
            format!("must be a 1..={MAX_MAP_NAME_BYTES} byte map identifier"),
        ));
    }
    Ok(())
}

fn validate_resource_name(value: &str, path: &str) -> Result<(), DescriptorExchangeError> {
    if value.is_empty()
        || value.len() > MAX_RESOURCE_NAME_BYTES
        || value.chars().any(char::is_control)
        || value.trim() != value
    {
        return Err(invalid(
            path,
            format!("must be a 1..={MAX_RESOURCE_NAME_BYTES} byte resource path"),
        ));
    }
    Ok(())
}

fn invalid(path: impl Into<String>, message: impl Into<String>) -> DescriptorExchangeError {
    DescriptorExchangeError::InvalidField {
        path: path.into(),
        message: message.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn wire(value: Value) -> WireObject {
        WireObject::try_from(value).unwrap()
    }

    fn projection(name: &str, level: u8, tag: &str) -> Value {
        json!({
            "name": name,
            "level": level,
            "tags": [tag],
            "type": {
                "name": name,
                "level": level,
                "tags": [tag],
                "crewRoles": [["commander"], ["driver"]]
            },
            "maxHealth": 1000,
            "maxAmmo": 45,
            "gun": {
                "reloadTime": 2.3,
                "clip": [1, 0.0],
                "shotDispersionAngle": 0.0046,
                "aimingTime": 2.3,
                "shotDispersionFactors": {
                    "turretRotation": 0.3,
                    "afterShot": 1.5
                },
                "rotationSpeed": 0.7,
                "maxHealth": 54,
                "maxRegenHealth": 27,
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
                "nativePowerRatio": 1.15
            },
            "chassis": {
                "rotationSpeed": 38.0,
                "shotDispersionFactors": [0.14, 0.14],
                "maxHealth": 170,
                "maxRegenHealth": 130,
                "hullPosition": [0.0, 0.6, 0.0],
                "hitTester": {
                    "bbox": [[-1.5, -0.8, -3.2], [1.5, 0.8, 3.2], null]
                }
            },
            "hull": {
                "hitTester": {
                    "bbox": [[-1.7, -0.2, -3.5], [1.7, 1.4, 3.5], null]
                },
                "heStructuralArmor": [],
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
                "player": {"available": false},
                "botDefault": {
                    "available": true,
                    "repairFactor": 0.57,
                    "hasBigKit": false
                }
            },
            "rammingSettings": {
                "botDefault": {
                    "spall_coefficient": 1.0,
                    "ramming_bonus": 0.0
                }
            },
            "spottingSettings": {
                "player": {"available": false},
                "botDefault": {"available": false}
            }
        })
    }

    fn catalog(rows: &[(&str, u8, &str)]) -> WireObject {
        wire(json!({
            "type": "descriptor_catalog",
            "vehicles": rows
                .iter()
                .map(|(name, level, tag)| json!({
                    "name": name,
                    "level": level,
                    "tags": [tag]
                }))
                .collect::<Vec<_>>()
        }))
    }

    fn bundle(
        round_id: RoundId,
        requested: &[&str],
        projections: &[(&str, Value)],
        failures: &[&str],
        complete: bool,
    ) -> WireObject {
        let projections: Map<String, Value> = projections
            .iter()
            .map(|(name, value)| ((*name).to_owned(), value.clone()))
            .collect();
        wire(json!({
            "type": "descriptor_bundle",
            "round_id": round_id,
            "requested": requested,
            "failures": failures,
            "complete": complete,
            "projections": projections
        }))
    }

    fn signature(seed: i64) -> Vec<Value> {
        [seed, 0, 0, 1_000, 0, 0, 0, 1_000, 0, 0, 0, 1_000]
            .into_iter()
            .map(|value| Value::Number(Number::from(value)))
            .collect()
    }

    fn destructible_part(
        round_id: RoundId,
        part: usize,
        parts: usize,
        resources: Value,
        instances: Value,
    ) -> WireObject {
        wire(json!({
            "type": "destructible_map",
            "round_id": round_id,
            "map": "01_karelia",
            "part": part,
            "parts": parts,
            "unit_vehicle_mass": 8000.0,
            "resources": resources,
            "instances": instances
        }))
    }

    fn exchange_with_catalog() -> DescriptorExchange {
        let mut exchange = DescriptorExchange::new();
        exchange
            .admit_catalog(
                7,
                &catalog(&[
                    ("ussr:R11_MS-1", 1, "lightTank"),
                    ("germany:G12_Ltraktor", 1, "lightTank"),
                ]),
            )
            .unwrap();
        exchange
    }

    #[test]
    fn catalog_is_connection_scoped_and_request_is_sorted_and_round_scoped() {
        let mut exchange = DescriptorExchange::new();
        let rows: Vec<_> = (0..680)
            .rev()
            .map(|index| {
                (
                    format!("nation{}:Tank_{index}", index % 10),
                    (index % 10 + 1) as u8,
                    "lightTank".to_owned(),
                )
            })
            .collect();
        let message = wire(json!({
            "type": "descriptor_catalog",
            "vehicles": rows.iter().map(|(name, level, tag)| json!({
                "name": name, "level": level, "tags": [tag]
            })).collect::<Vec<_>>()
        }));
        assert_eq!(exchange.admit_catalog(7, &message).unwrap(), 680);
        assert_eq!(exchange.catalog(7).unwrap().vehicles().len(), 680);

        let request = exchange
            .begin_round(
                9,
                7,
                "01_karelia",
                ["nation2:Tank_2".to_owned(), "nation1:Tank_1".to_owned()],
                false,
            )
            .unwrap()
            .unwrap();
        assert_eq!(
            request.into_value(),
            json!({
                "type": "descriptor_request",
                "round_id": 9,
                "names": ["nation1:Tank_1", "nation2:Tank_2"]
            })
        );
        assert!(matches!(
            exchange.admit_catalog(7, &message),
            Err(DescriptorExchangeError::CatalogFrozen { donor_id: 7 })
        ));
    }

    #[test]
    fn partial_descriptor_bundles_parse_transactionally_and_finish_the_gate() {
        let mut exchange = exchange_with_catalog();
        let request = exchange
            .begin_round(
                11,
                7,
                "01_karelia",
                [
                    "ussr:R11_MS-1".to_owned(),
                    "germany:G12_Ltraktor".to_owned(),
                ],
                false,
            )
            .unwrap()
            .unwrap();
        let requested = ["germany:G12_Ltraktor", "ussr:R11_MS-1"];
        assert_eq!(
            request.get("names"),
            Some(&json!(["germany:G12_Ltraktor", "ussr:R11_MS-1"]))
        );

        let first = bundle(
            11,
            &requested,
            &[(
                "germany:G12_Ltraktor",
                projection("germany:G12_Ltraktor", 1, "lightTank"),
            )],
            &[],
            false,
        );
        assert_eq!(
            exchange.admit_descriptor_bundle(7, &first).unwrap(),
            ExchangeEvent::DescriptorPartAccepted { remaining: 1 }
        );
        assert!(exchange.descriptors().is_none());

        let final_part = bundle(
            11,
            &requested,
            &[("ussr:R11_MS-1", projection("ussr:R11_MS-1", 1, "lightTank"))],
            &[],
            true,
        );
        assert_eq!(
            exchange.admit_descriptor_bundle(7, &final_part).unwrap(),
            ExchangeEvent::Ready
        );
        let descriptors = exchange.descriptors().unwrap();
        assert_eq!(descriptors.len(), 2);
        assert_eq!(exchange.snapshot().unwrap().status, ExchangeStatus::Ready);
    }

    #[test]
    fn stale_unsolicited_and_catalog_mismatched_bundles_do_not_mutate_state() {
        let mut exchange = exchange_with_catalog();
        exchange
            .begin_round(12, 7, "01_karelia", ["ussr:R11_MS-1".to_owned()], false)
            .unwrap();
        let requested = ["ussr:R11_MS-1"];

        let stale = bundle(
            11,
            &requested,
            &[("ussr:R11_MS-1", projection("ussr:R11_MS-1", 1, "lightTank"))],
            &[],
            true,
        );
        assert!(matches!(
            exchange.admit_descriptor_bundle(7, &stale),
            Err(DescriptorExchangeError::WrongRound {
                expected: 12,
                received: 11
            })
        ));

        let unsolicited = bundle(
            12,
            &requested,
            &[(
                "usa:T1_Cunningham",
                projection("usa:T1_Cunningham", 1, "lightTank"),
            )],
            &[],
            true,
        );
        assert!(matches!(
            exchange.admit_descriptor_bundle(7, &unsolicited),
            Err(DescriptorExchangeError::InvalidField { .. })
        ));

        let mismatch = bundle(
            12,
            &requested,
            &[("ussr:R11_MS-1", projection("ussr:R11_MS-1", 2, "lightTank"))],
            &[],
            true,
        );
        assert!(matches!(
            exchange.admit_descriptor_bundle(7, &mismatch),
            Err(DescriptorExchangeError::CatalogDescriptorMismatch { .. })
        ));
        assert_eq!(exchange.snapshot().unwrap().descriptor_remaining, 1);
        assert!(exchange.descriptors().is_none());
    }

    #[test]
    fn explicit_or_terminal_missing_projection_hard_fails_the_round() {
        let mut exchange = exchange_with_catalog();
        exchange
            .begin_round(13, 7, "01_karelia", ["ussr:R11_MS-1".to_owned()], false)
            .unwrap();
        let terminal = bundle(13, &["ussr:R11_MS-1"], &[], &["ussr:R11_MS-1"], true);
        assert_eq!(
            exchange.admit_descriptor_bundle(7, &terminal).unwrap(),
            ExchangeEvent::Failed(ExchangeFailure::DescriptorProjectionFailed)
        );
        assert_eq!(
            exchange.snapshot().unwrap().status,
            ExchangeStatus::Failed(ExchangeFailure::DescriptorProjectionFailed)
        );
        assert_eq!(
            ExchangeFailure::DescriptorProjectionFailed.wire_code(),
            "descriptor_projection_failed"
        );
    }

    #[test]
    fn destructible_parts_can_arrive_out_of_order_but_need_native_install_proof() {
        let mut exchange = exchange_with_catalog();
        assert!(exchange
            .begin_round(14, 7, "01_karelia", [], true)
            .unwrap()
            .is_none());
        let second = destructible_part(
            14,
            1,
            2,
            json!({
                "content/gates/building.model": {
                    "destr_type": "structure",
                    "kinetic_correction": 0.0
                }
            }),
            json!([[signature(2), 7, 1, null, {"73": [50.0, 12.0]},
                "content/gates/building.model"]]),
        );
        assert_eq!(
            exchange.admit_destructible_map(7, &second).unwrap(),
            ExchangeEvent::DestructiblePartAccepted {
                received: 1,
                expected: 2
            }
        );
        let first = destructible_part(
            14,
            0,
            2,
            json!({
                "content/gates/fence.model": {
                    "destr_type": "fragile",
                    "kinetic_correction": 1.0
                }
            }),
            json!([[signature(1), 7, 0, 12.5, null, "content/gates/fence.model"]]),
        );
        assert_eq!(
            exchange.admit_destructible_map(7, &first).unwrap(),
            ExchangeEvent::DestructibleMapAssembled { instances: 2 }
        );
        let snapshot = exchange.snapshot().unwrap();
        assert_eq!(snapshot.status, ExchangeStatus::Collecting);
        assert_eq!(
            snapshot.destructible_gate,
            DestructibleGate::AwaitingInstall
        );
        let map = exchange.destructible_map().unwrap();
        assert_eq!(map.instances.len(), 2);
        assert_eq!(
            map.instances[0].signature,
            DestructibleSignature(signature_array(1))
        );
        assert_eq!(map.resources.len(), 2);

        assert_eq!(
            exchange
                .confirm_destructible_install(14, "01_karelia", 2, 2)
                .unwrap(),
            ExchangeEvent::Ready
        );
        assert_eq!(exchange.snapshot().unwrap().status, ExchangeStatus::Ready);
    }

    #[test]
    fn incomplete_native_install_fails_closed_instead_of_enabling_authority() {
        let mut exchange = exchange_with_catalog();
        exchange.begin_round(15, 7, "01_karelia", [], true).unwrap();
        let part = destructible_part(
            15,
            0,
            1,
            json!({
                "content/gates/fence.model": {
                    "destr_type": "fragile",
                    "kinetic_correction": 0.0
                }
            }),
            json!([[signature(1), 7, 0, 10.0, null, "content/gates/fence.model"]]),
        );
        assert_eq!(
            exchange.admit_destructible_map(7, &part).unwrap(),
            ExchangeEvent::DestructibleMapAssembled { instances: 1 }
        );
        assert_eq!(
            exchange
                .confirm_destructible_install(15, "01_karelia", 2, 1)
                .unwrap(),
            ExchangeEvent::Failed(ExchangeFailure::DestructibleMapIncomplete)
        );
    }

    #[test]
    fn exact_part_retries_are_idempotent_but_conflicts_do_not_partially_commit() {
        let mut exchange = exchange_with_catalog();
        exchange.begin_round(16, 7, "01_karelia", [], true).unwrap();
        let first = destructible_part(
            16,
            0,
            2,
            json!({
                "content/gates/fence.model": {
                    "destr_type": "fragile",
                    "kinetic_correction": 0.0
                }
            }),
            json!([[signature(1), 7, 0, 10.0, null, "content/gates/fence.model"]]),
        );
        exchange.admit_destructible_map(7, &first).unwrap();
        assert_eq!(
            exchange.admit_destructible_map(7, &first).unwrap(),
            ExchangeEvent::DestructiblePartAccepted {
                received: 1,
                expected: 2
            }
        );
        let changed_retry = destructible_part(
            16,
            0,
            2,
            json!({
                "content/gates/fence.model": {
                    "destr_type": "fragile",
                    "kinetic_correction": 0.0
                }
            }),
            json!([[signature(1), 7, 0, 11.0, null, "content/gates/fence.model"]]),
        );
        assert!(matches!(
            exchange.admit_destructible_map(7, &changed_retry),
            Err(DescriptorExchangeError::DuplicateDestructiblePart { part: 0 })
        ));
        let conflict = destructible_part(
            16,
            1,
            2,
            json!({}),
            json!([[signature(2), 7, 0, 20.0, null, "content/gates/fence.model"]]),
        );
        assert!(matches!(
            exchange.admit_destructible_map(7, &conflict),
            Err(DescriptorExchangeError::DuplicateDestructibleWire)
        ));
        let snapshot = exchange.snapshot().unwrap();
        assert_eq!(snapshot.destructible_parts_received, 1);
        assert_eq!(snapshot.destructible_gate, DestructibleGate::Collecting);
    }

    #[test]
    fn timeout_and_donor_disconnect_choose_the_pending_native_gate() {
        let mut exchange = exchange_with_catalog();
        exchange
            .begin_round(17, 7, "01_karelia", ["ussr:R11_MS-1".to_owned()], true)
            .unwrap();
        assert_eq!(
            exchange.timeout(17).unwrap(),
            ExchangeEvent::Failed(ExchangeFailure::DescriptorTimeout)
        );

        exchange.begin_round(18, 7, "01_karelia", [], true).unwrap();
        assert_eq!(
            exchange.donor_disconnected(7),
            Some(ExchangeEvent::Failed(
                ExchangeFailure::DestructibleMapDonorDisconnected
            ))
        );
        assert!(exchange.catalog(7).is_none());
    }

    #[test]
    fn wire_limits_and_unknown_fields_are_strict() {
        let oversized_rows: Vec<_> = (0..=MAX_CATALOG_VEHICLES)
            .map(|index| {
                json!({
                    "name": format!("test:Tank_{index}"),
                    "level": 1,
                    "tags": []
                })
            })
            .collect();
        assert!(matches!(
            parse_catalog(&wire(json!({
                "type": "descriptor_catalog",
                "vehicles": oversized_rows
            }))),
            Err(DescriptorExchangeError::InvalidField { .. })
        ));
        assert!(matches!(
            parse_catalog(&wire(json!({
                "type": "descriptor_catalog",
                "vehicles": [{
                    "name": "ussr:R11_MS-1",
                    "level": 1,
                    "tags": ["lightTank"],
                    "authority": "client"
                }]
            }))),
            Err(DescriptorExchangeError::UnknownField { .. })
        ));

        let rows: Vec<_> = (0..65)
            .map(|index| (format!("test:Tank_{index}"), 1_u8, "lightTank".to_owned()))
            .collect();
        let mut exchange = DescriptorExchange::new();
        exchange
            .admit_catalog(
                7,
                &wire(json!({
                    "type": "descriptor_catalog",
                    "vehicles": rows.iter().map(|(name, level, tag)| json!({
                        "name": name, "level": level, "tags": [tag]
                    })).collect::<Vec<_>>()
                })),
            )
            .unwrap();
        assert!(matches!(
            exchange.begin_round(
                19,
                7,
                "01_karelia",
                rows.iter().map(|(name, _, _)| name.clone()),
                false
            ),
            Err(DescriptorExchangeError::InvalidField { .. })
        ));
    }

    fn signature_array(seed: i64) -> [i64; 12] {
        [seed, 0, 0, 1_000, 0, 0, 0, 1_000, 0, 0, 0, 1_000]
    }
}
