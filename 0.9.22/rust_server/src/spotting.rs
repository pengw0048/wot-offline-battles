//! Exact, engine-independent spotting and contact law for WoT 0.9.22 #1513.
//!
//! Native line of sight, pair-specific foliage, and barrel-lane clearance are
//! facts donated by the pinned client. This module only combines those facts
//! with descriptor-derived view and camouflage values. Missing or stale native
//! evidence never becomes a guessed clear result.

use std::collections::BTreeSet;

/// Automatic proximity spotting ignores world occlusion in #1513.
pub const PROXIMITY_SPOT_DISTANCE_METRES: f64 = 50.0;
/// Exact `constants.VISIBILITY.MAX_RADIUS` detection ceiling in #1513.
pub const MAX_SPOT_DISTANCE_METRES: f64 = 445.0;
/// Exact `constants.AOI.VEHICLE_CIRCULAR_AOI_RADIUS` presentation boundary.
pub const VEHICLE_AOI_RADIUS_METRES: f64 = 565.0;
/// Deterministic no-skill presentation hold used by the LAN clients.
pub const SPOT_PRESENTATION_MEMORY_US: u64 = 10_000_000;
/// Tactical planner memory from the Python authority.
pub const CONTACT_LAST_KNOWN_MEMORY_US: u64 = 7_000_000;
pub const MOVING_SPEED_EPSILON_METRES_PER_SECOND: f64 = 0.5;
pub const SHOT_CAMOUFLAGE_PENALTY_US: u64 = 750_000;
pub const STILL_DEVICE_DELAY_US: u64 = 3_000_000;

pub const VISIBILITY_CACHE_MIN_US: u64 = 180_000;
pub const VISIBILITY_CACHE_JITTER_US: u64 = 18_000;
pub const VISIBILITY_CACHE_JITTER_BUCKETS: u64 = 11;
pub const SHOT_LANE_CACHE_US: u64 = 200_000;
pub const CONTACT_OBSERVATION_PERIOD_US: u64 = 400_000;
pub const SHOT_LANE_PHASES: u64 = 29;
pub const ORDINARY_SHOT_LANE_RANGE_METRES: f64 = 585.0;
pub const SPG_SHOT_LANE_RANGE_METRES: f64 = 2_500.0;

pub const LOCAL_SPOTTING_UPDATE_US: u64 = 100_000;
pub const LOCAL_SPOTTING_PROBE_US: u64 = 500_000;
pub const LOCAL_SPOTTING_PHASE_BUCKETS: u64 = 5;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ContactTargetKind {
    Human,
    Bot,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct ContactTarget {
    pub kind: ContactTargetKind,
    pub id: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CamouflageAspect {
    pub additive: f64,
    pub multiplier: f64,
}

impl Default for CamouflageAspect {
    fn default() -> Self {
        Self {
            additive: 0.0,
            multiplier: 1.0,
        }
    }
}

/// Descriptor-derived target camouflage plus its canonical timing state.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TargetCamouflage {
    /// Already-composed `computeBaseInvisibility` moving value.
    pub moving: f64,
    /// Already-composed `computeBaseInvisibility` stationary value.
    pub stationary: f64,
    pub moving_aspect: CamouflageAspect,
    pub stationary_aspect: CamouflageAspect,
    pub has_camouflage_net: bool,
    pub camouflage_net_delay_us: u64,
    pub invisibility_factor_at_shot: f64,
    /// Canonical fire time. `None` deliberately means no proved recent shot.
    pub last_fired_at_us: Option<u64>,
}

/// Descriptor-derived observer vision plus its canonical timing state.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ObserverView {
    pub base_range_metres: f64,
    pub misc_factor: f64,
    pub crew_factor: f64,
    pub binocular_factor: f64,
    pub has_binoculars: bool,
    pub binocular_delay_us: u64,
}

/// One native visibility sample for the same frozen observer-target pair.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NativeVisibilityEvidence {
    pub sampled_at_us: u64,
    pub line_of_sight: bool,
    /// `None` is not interpreted as an open field. A proved zero is `Some(0)`.
    pub foliage_bonus: Option<f64>,
    /// Foliage within 15 m of a firing target is transparent. The donor must
    /// therefore state which recent-fire branch this foliage value represents.
    pub evaluated_for_recent_fire: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NativeFiringLaneEvidence {
    pub sampled_at_us: u64,
    pub clear: bool,
}

/// All facts needed for one observer-target evaluation.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ObserverTargetInput {
    pub observer_id: u32,
    pub target: ContactTarget,
    pub now_us: u64,
    pub target_alive: bool,
    pub distance_metres: f64,
    pub observer_speed_metres_per_second: f64,
    pub observer_stationary_since_us: Option<u64>,
    pub view: ObserverView,
    pub target_speed_metres_per_second: f64,
    pub target_stationary_since_us: Option<u64>,
    pub camouflage: TargetCamouflage,
    pub native_visibility: Option<NativeVisibilityEvidence>,
    pub native_firing_lane: Option<NativeFiringLaneEvidence>,
    /// The caller explicitly selects the ordinary or SPG lane envelope.
    pub firing_lane_range_metres: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VisibilityReason {
    TargetDead,
    InvalidInput,
    Proximity,
    BeyondSpottingCeiling,
    ConcealedWithoutGeometry,
    MissingNativeVisibility,
    StaleNativeVisibility,
    NativeLineOfSightBlocked,
    MissingFoliageEvidence,
    FoliageFireStateMismatch,
    Concealed,
    Detected,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VisibilityDecision {
    pub visible: bool,
    pub reason: VisibilityReason,
    pub fired_recently: bool,
    pub binoculars_active: bool,
    pub camouflage_net_active: bool,
    pub effective_view_range_metres: Option<f64>,
    pub effective_camouflage: Option<f64>,
    pub detection_distance_metres: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShootabilityReason {
    TargetDead,
    InvalidInput,
    TeamHidden,
    BeyondLaneRange,
    MissingNativeLane,
    StaleNativeLane,
    NativeLaneBlocked,
    Clear,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ShootabilityDecision {
    pub shootable: bool,
    pub reason: ShootabilityReason,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LastKnownDecision {
    Refresh {
        observed_at_us: u64,
    },
    Retain {
        observed_at_us: u64,
        expires_at_us: u64,
    },
    Forget,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ObserverTargetContactDecision {
    pub observer_id: u32,
    pub target: ContactTarget,
    pub direct_visibility: VisibilityDecision,
    /// Radio spotting is the OR of positive direct samples for this team tick.
    pub team_visible: bool,
    pub firing_lane: ShootabilityDecision,
}

#[derive(Clone, Debug, PartialEq)]
pub struct TeamContactDecision {
    pub target: ContactTarget,
    pub visible: bool,
    pub directly_visible_by_observer_ids: BTreeSet<u32>,
    pub shootable_by_observer_ids: BTreeSet<u32>,
    pub observers: Vec<ObserverTargetContactDecision>,
    pub last_known: LastKnownDecision,
}

#[derive(Clone, Debug, PartialEq)]
pub struct TeamContactInput {
    pub target: ContactTarget,
    pub target_alive: bool,
    pub now_us: u64,
    /// The tactical record's last positive sample, not a negative observation.
    pub previous_last_seen_us: Option<u64>,
    pub observers: Vec<ObserverTargetInput>,
}

pub fn clamp(value: f64, minimum: f64, maximum: f64) -> f64 {
    minimum.max(maximum.min(value))
}

/// #1513 `utils.getCircularVisionRadius`, with the still device gated outside.
pub fn effective_view_range(
    base_range: f64,
    misc_factor: f64,
    crew_factor: f64,
    binocular_factor: f64,
    binocular_active: bool,
) -> f64 {
    let mut result = base_range.max(PROXIMITY_SPOT_DISTANCE_METRES);
    result *= misc_factor.max(0.0);
    result *= crew_factor.max(0.0);
    if binocular_active {
        result *= binocular_factor.max(1.0);
    }
    result.max(PROXIMITY_SPOT_DISTANCE_METRES)
}

/// #1513 `VehicleDescr.computeBaseInvisibility` composition.
pub fn base_camouflage(
    moving_base: f64,
    stationary_base: f64,
    crew_factor: f64,
    invisibility_factor: f64,
    paint_bonus: f64,
) -> (f64, f64) {
    let factor = crew_factor.max(0.0) * invisibility_factor.max(0.0);
    let bonus = paint_bonus.max(0.0);
    (
        moving_base.max(0.0) * factor + bonus,
        stationary_base.max(0.0) * factor + bonus,
    )
}

/// #1513 `utils.getInvisibility`: aspect, then shot factor, then foliage.
pub fn effective_camouflage(
    base: f64,
    aspect: CamouflageAspect,
    shot_factor: f64,
    fired_recently: bool,
    foliage_bonus: f64,
) -> f64 {
    let mut result = (base + aspect.additive) * aspect.multiplier.max(0.0);
    if fired_recently {
        result *= clamp(shot_factor, 0.0, 1.0);
    }
    result += clamp(foliage_bonus, 0.0, 0.60);
    clamp(result, 0.0, 0.95)
}

/// Apply the 50 m floor and 445 m ceiling from #1513.
pub fn detection_distance(view_range: f64, camouflage: f64) -> f64 {
    let view_range = view_range.max(PROXIMITY_SPOT_DISTANCE_METRES);
    let camouflage = clamp(camouflage, 0.0, 0.95);
    let distance = view_range - (view_range - PROXIMITY_SPOT_DISTANCE_METRES) * camouflage;
    clamp(
        distance,
        PROXIMITY_SPOT_DISTANCE_METRES,
        MAX_SPOT_DISTANCE_METRES,
    )
}

pub fn fired_recently(now_us: u64, last_fired_at_us: Option<u64>) -> bool {
    let Some(last_fired_at_us) = last_fired_at_us else {
        return false;
    };
    last_fired_at_us <= now_us
        && now_us < last_fired_at_us.saturating_add(SHOT_CAMOUFLAGE_PENALTY_US)
}

pub fn stationary_device_active(
    now_us: u64,
    speed_metres_per_second: f64,
    stationary_since_us: Option<u64>,
    delay_us: u64,
) -> bool {
    if !speed_metres_per_second.is_finite()
        || speed_metres_per_second.abs() > MOVING_SPEED_EPSILON_METRES_PER_SECOND
    {
        return false;
    }
    let Some(stationary_since_us) = stationary_since_us else {
        return delay_us == 0;
    };
    stationary_since_us <= now_us && now_us - stationary_since_us >= delay_us
}

pub fn visibility_cache_ttl_us(observer_id: u32, target: ContactTarget) -> u64 {
    let bucket =
        (u64::from(observer_id) * 31 + u64::from(target.id) * 17) % VISIBILITY_CACHE_JITTER_BUCKETS;
    VISIBILITY_CACHE_MIN_US + bucket * VISIBILITY_CACHE_JITTER_US
}

pub fn shot_lane_phase_us(observer_id: u32, target: ContactTarget) -> u64 {
    let kind_salt = match target.kind {
        ContactTargetKind::Human => 11,
        ContactTargetKind::Bot => 0,
    };
    let bucket =
        (u64::from(observer_id) * 31 + u64::from(target.id) * 17 + kind_salt) % SHOT_LANE_PHASES;
    bucket * CONTACT_OBSERVATION_PERIOD_US / SHOT_LANE_PHASES
}

pub fn local_spotting_probe_phase_us(target_id: u32) -> u64 {
    (u64::from(target_id) * 17 % LOCAL_SPOTTING_PHASE_BUCKETS) * LOCAL_SPOTTING_UPDATE_US
}

fn finite_input(input: &ObserverTargetInput) -> bool {
    [
        input.distance_metres,
        input.observer_speed_metres_per_second,
        input.view.base_range_metres,
        input.view.misc_factor,
        input.view.crew_factor,
        input.view.binocular_factor,
        input.target_speed_metres_per_second,
        input.camouflage.moving,
        input.camouflage.stationary,
        input.camouflage.moving_aspect.additive,
        input.camouflage.moving_aspect.multiplier,
        input.camouflage.stationary_aspect.additive,
        input.camouflage.stationary_aspect.multiplier,
        input.camouflage.invisibility_factor_at_shot,
        input.firing_lane_range_metres,
    ]
    .iter()
    .all(|value| value.is_finite())
        && input.distance_metres >= 0.0
        && input.firing_lane_range_metres >= 0.0
}

/// Resolve direct visibility for one observer without applying team radio.
pub fn evaluate_observer_visibility(input: &ObserverTargetInput) -> VisibilityDecision {
    let fired_recently = fired_recently(input.now_us, input.camouflage.last_fired_at_us);
    let binoculars_active = input.view.has_binoculars
        && stationary_device_active(
            input.now_us,
            input.observer_speed_metres_per_second,
            input.observer_stationary_since_us,
            input.view.binocular_delay_us,
        );
    let camouflage_net_active = input.camouflage.has_camouflage_net
        && stationary_device_active(
            input.now_us,
            input.target_speed_metres_per_second,
            input.target_stationary_since_us,
            input.camouflage.camouflage_net_delay_us,
        );
    let closed = |reason| VisibilityDecision {
        visible: false,
        reason,
        fired_recently,
        binoculars_active,
        camouflage_net_active,
        effective_view_range_metres: None,
        effective_camouflage: None,
        detection_distance_metres: None,
    };
    if !input.target_alive {
        return closed(VisibilityReason::TargetDead);
    }
    if !finite_input(input) {
        return closed(VisibilityReason::InvalidInput);
    }
    if input.distance_metres <= PROXIMITY_SPOT_DISTANCE_METRES {
        return VisibilityDecision {
            visible: true,
            reason: VisibilityReason::Proximity,
            fired_recently,
            binoculars_active,
            camouflage_net_active,
            effective_view_range_metres: None,
            effective_camouflage: None,
            detection_distance_metres: Some(PROXIMITY_SPOT_DISTANCE_METRES),
        };
    }
    if input.distance_metres > MAX_SPOT_DISTANCE_METRES {
        return closed(VisibilityReason::BeyondSpottingCeiling);
    }

    let view_range = effective_view_range(
        input.view.base_range_metres,
        input.view.misc_factor,
        input.view.crew_factor,
        input.view.binocular_factor,
        binoculars_active,
    );
    let moving =
        input.target_speed_metres_per_second.abs() > MOVING_SPEED_EPSILON_METRES_PER_SECOND;
    let aspect = if moving || (input.camouflage.has_camouflage_net && !camouflage_net_active) {
        input.camouflage.moving_aspect
    } else {
        input.camouflage.stationary_aspect
    };
    let base = if moving {
        input.camouflage.moving
    } else {
        input.camouflage.stationary
    };
    // The Python authority first rejects pairs which cannot be visible even
    // with clear geometry and zero foliage, avoiding needless native rays.
    let minimum_camouflage = effective_camouflage(
        base,
        aspect,
        input.camouflage.invisibility_factor_at_shot,
        fired_recently,
        0.0,
    );
    let maximum_detection_distance = detection_distance(view_range, minimum_camouflage);
    if input.distance_metres > maximum_detection_distance {
        return VisibilityDecision {
            visible: false,
            reason: VisibilityReason::ConcealedWithoutGeometry,
            fired_recently,
            binoculars_active,
            camouflage_net_active,
            effective_view_range_metres: Some(view_range),
            effective_camouflage: Some(minimum_camouflage),
            detection_distance_metres: Some(maximum_detection_distance),
        };
    }

    let Some(native) = input.native_visibility else {
        return VisibilityDecision {
            effective_view_range_metres: Some(view_range),
            effective_camouflage: Some(minimum_camouflage),
            detection_distance_metres: Some(maximum_detection_distance),
            ..closed(VisibilityReason::MissingNativeVisibility)
        };
    };
    let visibility_ttl = visibility_cache_ttl_us(input.observer_id, input.target);
    if native.sampled_at_us > input.now_us || input.now_us - native.sampled_at_us >= visibility_ttl
    {
        return VisibilityDecision {
            effective_view_range_metres: Some(view_range),
            effective_camouflage: Some(minimum_camouflage),
            detection_distance_metres: Some(maximum_detection_distance),
            ..closed(VisibilityReason::StaleNativeVisibility)
        };
    }
    if !native.line_of_sight {
        return VisibilityDecision {
            effective_view_range_metres: Some(view_range),
            effective_camouflage: Some(minimum_camouflage),
            detection_distance_metres: Some(maximum_detection_distance),
            ..closed(VisibilityReason::NativeLineOfSightBlocked)
        };
    }
    let Some(foliage_bonus) = native.foliage_bonus else {
        return VisibilityDecision {
            effective_view_range_metres: Some(view_range),
            effective_camouflage: Some(minimum_camouflage),
            detection_distance_metres: Some(maximum_detection_distance),
            ..closed(VisibilityReason::MissingFoliageEvidence)
        };
    };
    if !foliage_bonus.is_finite() || native.evaluated_for_recent_fire != fired_recently {
        return VisibilityDecision {
            effective_view_range_metres: Some(view_range),
            effective_camouflage: Some(minimum_camouflage),
            detection_distance_metres: Some(maximum_detection_distance),
            ..closed(VisibilityReason::FoliageFireStateMismatch)
        };
    }
    let camouflage = effective_camouflage(
        base,
        aspect,
        input.camouflage.invisibility_factor_at_shot,
        fired_recently,
        foliage_bonus,
    );
    let detected_at = detection_distance(view_range, camouflage);
    let visible = input.distance_metres <= detected_at;
    VisibilityDecision {
        visible,
        reason: if visible {
            VisibilityReason::Detected
        } else {
            VisibilityReason::Concealed
        },
        fired_recently,
        binoculars_active,
        camouflage_net_active,
        effective_view_range_metres: Some(view_range),
        effective_camouflage: Some(camouflage),
        detection_distance_metres: Some(detected_at),
    }
}

/// Resolve one observer's barrel lane after the caller supplies team visibility.
pub fn evaluate_observer_firing_lane(
    input: &ObserverTargetInput,
    team_visible: bool,
) -> ShootabilityDecision {
    let closed = |reason| ShootabilityDecision {
        shootable: false,
        reason,
    };
    if !input.target_alive {
        return closed(ShootabilityReason::TargetDead);
    }
    if !finite_input(input) {
        return closed(ShootabilityReason::InvalidInput);
    }
    if !team_visible {
        return closed(ShootabilityReason::TeamHidden);
    }
    if input.distance_metres > input.firing_lane_range_metres {
        return closed(ShootabilityReason::BeyondLaneRange);
    }
    let Some(native) = input.native_firing_lane else {
        return closed(ShootabilityReason::MissingNativeLane);
    };
    if native.sampled_at_us > input.now_us
        || input.now_us - native.sampled_at_us > SHOT_LANE_CACHE_US
    {
        return closed(ShootabilityReason::StaleNativeLane);
    }
    if !native.clear {
        return closed(ShootabilityReason::NativeLaneBlocked);
    }
    ShootabilityDecision {
        shootable: true,
        reason: ShootabilityReason::Clear,
    }
}

/// Resolve one team's complete contact from observer-specific native facts.
///
/// Positive visibility is relayed across the team for this observation. A
/// negative direct sample remains observer-specific, while every friendly
/// observer still needs its own native firing-lane proof before it may shoot.
pub fn evaluate_team_contact(input: TeamContactInput) -> TeamContactDecision {
    let mut direct = Vec::with_capacity(input.observers.len());
    let mut directly_visible_by_observer_ids = BTreeSet::new();
    for observer in &input.observers {
        let visibility = if observer.target == input.target
            && observer.now_us == input.now_us
            && observer.target_alive == input.target_alive
        {
            evaluate_observer_visibility(observer)
        } else {
            VisibilityDecision {
                visible: false,
                reason: VisibilityReason::InvalidInput,
                fired_recently: false,
                binoculars_active: false,
                camouflage_net_active: false,
                effective_view_range_metres: None,
                effective_camouflage: None,
                detection_distance_metres: None,
            }
        };
        if visibility.visible {
            directly_visible_by_observer_ids.insert(observer.observer_id);
        }
        direct.push(visibility);
    }
    let visible = input.target_alive && !directly_visible_by_observer_ids.is_empty();
    let mut shootable_by_observer_ids = BTreeSet::new();
    let mut observers = Vec::with_capacity(input.observers.len());
    for (observer, direct_visibility) in input.observers.iter().zip(direct) {
        let firing_lane = evaluate_observer_firing_lane(observer, visible);
        if firing_lane.shootable {
            shootable_by_observer_ids.insert(observer.observer_id);
        }
        observers.push(ObserverTargetContactDecision {
            observer_id: observer.observer_id,
            target: observer.target,
            direct_visibility,
            team_visible: visible,
            firing_lane,
        });
    }
    let last_known = last_known_decision(
        input.now_us,
        input.target_alive,
        visible,
        input.previous_last_seen_us,
    );
    TeamContactDecision {
        target: input.target,
        visible,
        directly_visible_by_observer_ids,
        shootable_by_observer_ids,
        observers,
        last_known,
    }
}

pub fn last_known_decision(
    now_us: u64,
    target_alive: bool,
    visible: bool,
    previous_last_seen_us: Option<u64>,
) -> LastKnownDecision {
    if !target_alive {
        return LastKnownDecision::Forget;
    }
    if visible {
        return LastKnownDecision::Refresh {
            observed_at_us: now_us,
        };
    }
    let Some(observed_at_us) = previous_last_seen_us.filter(|seen| *seen <= now_us) else {
        return LastKnownDecision::Forget;
    };
    let expires_at_us = observed_at_us.saturating_add(CONTACT_LAST_KNOWN_MEMORY_US);
    if now_us <= expires_at_us {
        LastKnownDecision::Retain {
            observed_at_us,
            expires_at_us,
        }
    } else {
        LastKnownDecision::Forget
    }
}

pub fn presentation_memory_deadline_us(observed_at_us: u64) -> u64 {
    observed_at_us.saturating_add(SPOT_PRESENTATION_MEMORY_US)
}

pub fn presentation_remembered(now_us: u64, deadline_us: u64) -> bool {
    now_us < deadline_us
}

#[cfg(test)]
mod tests {
    use super::*;

    fn target() -> ContactTarget {
        ContactTarget {
            kind: ContactTargetKind::Human,
            id: 2,
        }
    }

    fn observer(observer_id: u32) -> ObserverTargetInput {
        ObserverTargetInput {
            observer_id,
            target: target(),
            now_us: 1_000_000,
            target_alive: true,
            distance_metres: 100.0,
            observer_speed_metres_per_second: 0.0,
            observer_stationary_since_us: Some(0),
            view: ObserverView {
                base_range_metres: 400.0,
                misc_factor: 1.0,
                crew_factor: 1.0,
                binocular_factor: 1.25,
                has_binoculars: false,
                binocular_delay_us: STILL_DEVICE_DELAY_US,
            },
            target_speed_metres_per_second: 1.0,
            target_stationary_since_us: None,
            camouflage: TargetCamouflage {
                moving: 0.20,
                stationary: 0.30,
                moving_aspect: CamouflageAspect::default(),
                stationary_aspect: CamouflageAspect::default(),
                has_camouflage_net: false,
                camouflage_net_delay_us: STILL_DEVICE_DELAY_US,
                invisibility_factor_at_shot: 0.25,
                last_fired_at_us: None,
            },
            native_visibility: Some(NativeVisibilityEvidence {
                sampled_at_us: 1_000_000,
                line_of_sight: true,
                foliage_bonus: Some(0.0),
                evaluated_for_recent_fire: false,
            }),
            native_firing_lane: Some(NativeFiringLaneEvidence {
                sampled_at_us: 1_000_000,
                clear: true,
            }),
            firing_lane_range_metres: ORDINARY_SHOT_LANE_RANGE_METRES,
        }
    }

    #[test]
    fn ports_base_camouflage_and_operation_order() {
        let (moving, stationary) = base_camouflage(0.288, 0.300, 0.57, 1.0, 0.03);
        assert!((moving - (0.288 * 0.57 + 0.03)).abs() < 1e-12);
        assert!((stationary - (0.300 * 0.57 + 0.03)).abs() < 1e-12);
        let value = effective_camouflage(
            0.30,
            CamouflageAspect {
                additive: 0.10,
                multiplier: 1.0,
            },
            0.25,
            true,
            0.15,
        );
        assert!((value - ((0.30 + 0.10) * 0.25 + 0.15)).abs() < 1e-12);
    }

    #[test]
    fn ports_detection_floor_ceiling_and_proximity_override() {
        assert_eq!(detection_distance(400.0, 0.95), 67.5);
        assert_eq!(detection_distance(400.0, 0.5), 225.0);
        assert_eq!(detection_distance(700.0, 0.0), 445.0);

        let mut input = observer(11);
        input.distance_metres = 50.0;
        input.native_visibility = None;
        let decision = evaluate_observer_visibility(&input);
        assert!(decision.visible);
        assert_eq!(decision.reason, VisibilityReason::Proximity);

        input.distance_metres = 445.01;
        let decision = evaluate_observer_visibility(&input);
        assert!(!decision.visible);
        assert_eq!(decision.reason, VisibilityReason::BeyondSpottingCeiling);
    }

    #[test]
    fn still_devices_arm_at_the_exact_three_second_boundary() {
        let mut input = observer(11);
        input.now_us = 3_999_999;
        input.observer_stationary_since_us = Some(1_000_000);
        input.view.has_binoculars = true;
        input.target_speed_metres_per_second = 0.5;
        input.target_stationary_since_us = Some(1_000_000);
        input.camouflage.has_camouflage_net = true;
        input.native_visibility.as_mut().unwrap().sampled_at_us = input.now_us;
        let before = evaluate_observer_visibility(&input);
        assert!(!before.binoculars_active);
        assert!(!before.camouflage_net_active);

        input.now_us = 4_000_000;
        input.native_visibility.as_mut().unwrap().sampled_at_us = input.now_us;
        let at = evaluate_observer_visibility(&input);
        assert!(at.binoculars_active);
        assert!(at.camouflage_net_active);
        assert_eq!(at.effective_view_range_metres, Some(500.0));

        input.observer_speed_metres_per_second = 0.500_001;
        input.target_speed_metres_per_second = -0.500_001;
        let moving = evaluate_observer_visibility(&input);
        assert!(!moving.binoculars_active);
        assert!(!moving.camouflage_net_active);
    }

    #[test]
    fn recent_fire_penalty_uses_half_open_075_second_window() {
        assert!(fired_recently(1_749_999, Some(1_000_000)));
        assert!(!fired_recently(1_750_000, Some(1_000_000)));
        assert!(!fired_recently(999_999, Some(1_000_000)));

        let mut input = observer(11);
        input.distance_metres = 300.0;
        input.camouflage.last_fired_at_us = Some(900_000);
        input
            .native_visibility
            .as_mut()
            .unwrap()
            .evaluated_for_recent_fire = true;
        let fired = evaluate_observer_visibility(&input);
        assert!(fired.visible);
        assert!(fired.fired_recently);

        input
            .native_visibility
            .as_mut()
            .unwrap()
            .evaluated_for_recent_fire = false;
        let mismatched = evaluate_observer_visibility(&input);
        assert!(!mismatched.visible);
        assert_eq!(
            mismatched.reason,
            VisibilityReason::FoliageFireStateMismatch
        );
    }

    #[test]
    fn missing_native_los_or_foliage_fails_closed() {
        let mut input = observer(11);
        input.native_visibility = None;
        assert_eq!(
            evaluate_observer_visibility(&input).reason,
            VisibilityReason::MissingNativeVisibility
        );

        input.native_visibility = Some(NativeVisibilityEvidence {
            sampled_at_us: input.now_us,
            line_of_sight: false,
            foliage_bonus: None,
            evaluated_for_recent_fire: false,
        });
        assert_eq!(
            evaluate_observer_visibility(&input).reason,
            VisibilityReason::NativeLineOfSightBlocked
        );

        input.native_visibility.as_mut().unwrap().line_of_sight = true;
        assert_eq!(
            evaluate_observer_visibility(&input).reason,
            VisibilityReason::MissingFoliageEvidence
        );
    }

    #[test]
    fn native_visibility_uses_the_exact_pair_cache_deadline() {
        let mut input = observer(11);
        let ttl = visibility_cache_ttl_us(input.observer_id, input.target);
        input.now_us = 10_000_000;
        input.native_visibility.as_mut().unwrap().sampled_at_us = input.now_us - ttl + 1;
        assert!(evaluate_observer_visibility(&input).visible);
        input.native_visibility.as_mut().unwrap().sampled_at_us = input.now_us - ttl;
        let stale = evaluate_observer_visibility(&input);
        assert!(!stale.visible);
        assert_eq!(stale.reason, VisibilityReason::StaleNativeVisibility);
    }

    #[test]
    fn one_spotter_relays_visibility_but_each_shooter_needs_its_own_lane() {
        let mut spotter = observer(11);
        spotter.native_firing_lane.as_mut().unwrap().clear = false;
        let mut shooter = observer(12);
        shooter.native_visibility.as_mut().unwrap().line_of_sight = false;
        shooter.native_firing_lane.as_mut().unwrap().clear = true;

        let decision = evaluate_team_contact(TeamContactInput {
            target: target(),
            target_alive: true,
            now_us: 1_000_000,
            previous_last_seen_us: None,
            observers: vec![spotter, shooter],
        });
        assert!(decision.visible);
        assert_eq!(
            decision.directly_visible_by_observer_ids,
            BTreeSet::from([11])
        );
        assert_eq!(decision.shootable_by_observer_ids, BTreeSet::from([12]));
        assert_eq!(
            decision.last_known,
            LastKnownDecision::Refresh {
                observed_at_us: 1_000_000
            }
        );
        assert!(!decision.observers[1].direct_visibility.visible);
        assert!(decision.observers[1].team_visible);
        assert!(decision.observers[1].firing_lane.shootable);
    }

    #[test]
    fn clear_lane_never_discloses_an_unspotted_target() {
        let mut first = observer(11);
        first.native_visibility.as_mut().unwrap().line_of_sight = false;
        let decision = evaluate_team_contact(TeamContactInput {
            target: target(),
            target_alive: true,
            now_us: 1_000_000,
            previous_last_seen_us: None,
            observers: vec![first],
        });
        assert!(!decision.visible);
        assert!(decision.shootable_by_observer_ids.is_empty());
        assert_eq!(
            decision.observers[0].firing_lane.reason,
            ShootabilityReason::TeamHidden
        );
    }

    #[test]
    fn firing_lane_is_fresh_through_020_seconds_and_range_gated() {
        let mut input = observer(11);
        input.now_us = 1_000_000;
        input.native_visibility.as_mut().unwrap().sampled_at_us = input.now_us;
        input.native_firing_lane.as_mut().unwrap().sampled_at_us =
            input.now_us - SHOT_LANE_CACHE_US;
        assert!(evaluate_observer_firing_lane(&input, true).shootable);
        input.native_firing_lane.as_mut().unwrap().sampled_at_us -= 1;
        assert_eq!(
            evaluate_observer_firing_lane(&input, true).reason,
            ShootabilityReason::StaleNativeLane
        );
        input.native_firing_lane.as_mut().unwrap().sampled_at_us = input.now_us;
        input.distance_metres = ORDINARY_SHOT_LANE_RANGE_METRES + 0.001;
        assert_eq!(
            evaluate_observer_firing_lane(&input, true).reason,
            ShootabilityReason::BeyondLaneRange
        );
    }

    #[test]
    fn last_known_contact_retains_exactly_seven_seconds() {
        assert_eq!(
            last_known_decision(9_000_000, true, false, Some(2_000_000)),
            LastKnownDecision::Retain {
                observed_at_us: 2_000_000,
                expires_at_us: 9_000_000,
            }
        );
        assert_eq!(
            last_known_decision(9_000_001, true, false, Some(2_000_000)),
            LastKnownDecision::Forget
        );
        assert_eq!(
            last_known_decision(3_000_000, false, true, Some(2_000_000)),
            LastKnownDecision::Forget
        );
    }

    #[test]
    fn presentation_memory_is_separate_and_expires_at_ten_seconds() {
        let deadline = presentation_memory_deadline_us(2_000_000);
        assert!(presentation_remembered(deadline - 1, deadline));
        assert!(!presentation_remembered(deadline, deadline));
    }

    #[test]
    fn deterministic_probe_and_lane_phases_match_the_python_formulas() {
        assert_eq!(
            visibility_cache_ttl_us(11, target()),
            VISIBILITY_CACHE_MIN_US + ((11 * 31 + 2 * 17) % 11) as u64 * VISIBILITY_CACHE_JITTER_US
        );
        assert_eq!(
            shot_lane_phase_us(11, target()),
            ((11 * 31 + 2 * 17 + 11) % 29) as u64 * CONTACT_OBSERVATION_PERIOD_US
                / SHOT_LANE_PHASES
        );
        assert_eq!(
            local_spotting_probe_phase_us(2),
            ((2 * 17) % 5) as u64 * LOCAL_SPOTTING_UPDATE_US
        );
    }

    #[test]
    fn non_finite_inputs_fail_closed() {
        let mut input = observer(11);
        input.distance_metres = f64::NAN;
        let visibility = evaluate_observer_visibility(&input);
        assert!(!visibility.visible);
        assert_eq!(visibility.reason, VisibilityReason::InvalidInput);
        let lane = evaluate_observer_firing_lane(&input, true);
        assert!(!lane.shootable);
        assert_eq!(lane.reason, ShootabilityReason::InvalidInput);
    }
}
