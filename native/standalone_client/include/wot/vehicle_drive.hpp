#pragma once

#include "wot/point_velocity.hpp"
#include "wot/vehicle_dynamics.hpp"

#include <array>
#include <cstdint>

namespace wot {

struct VehicleEngineConfiguration;

struct VehicleEngineLossPoint {
    float angular_velocity;
    float torque;
};

// Native Engine::Settings output, separate from the source dictionary's RPMs.
struct VehicleEngineTorqueSettings {
    std::array<VehicleEngineLossPoint, 3> losses;
    float power;
    float start_angular_velocity;
};

struct VehicleEngineTorque {
    float gross;
    float losses;
};

// #1513 Engine::Settings 0x00f7ecd0: the loss-curve abscissae use the
// pre-existing constructor settings before the new RPM keys are assigned.
VehicleEngineTorqueSettings build_vehicle_engine_torque_settings(
    const VehicleEngineConfiguration& configuration);

// #1513 0x00f7ec00 with PointCurve 0x00f78b50 and EngineTorqueCurve
// 0x00f56ea0. Power scale and rotation state are original native inputs.
VehicleEngineTorque prepare_vehicle_engine_torque(
    const VehicleEngineTorqueSettings& settings, float angular_velocity,
    float power_scale, bool use_start_torque);

// #1513 0x00f7e940 adds torque to the engine rigid body. The engine remains
// a real body joined to the wheels by constraint rows; this does not apply a
// substitute longitudinal force to the hull.
void accumulate_vehicle_engine_torque(VehicleRigidBody& engine_body,
    VehicleEngineTorque torque, bool running, float throttle,
    float inertial_torque, std::int32_t movement_state);

// #1513 native ground record fields sideFriction, fwdFriction and
// hbSideFrictionAddition; the chassis supplies sideFrictionConstantRatio.
struct TrackGroundFriction {
    float side_friction;
    float forward_friction;
    float handbrake_side_friction_addition;
    float side_friction_constant_ratio;
};

struct TrackContactMotion {
    Vector3 center_of_mass;
    Vector3 linear_velocity;
    Vector3 angular_velocity;
    Vector3 contact_point;
    Vector3 forward_tangent;
    Vector3 side_tangent;
};

struct TrackFrictionCoefficients {
    float forward;
    float side;
};

// #1513 0x00f88e90 -> 0x00f967a0. Constant mode bypasses the slip ellipse.
// add_handbrake_side_friction is the original conjunction of all tracks
// touching, native movement-state > 0 and a forward/reverse signal.
// The two contact scales are original roller +0x3b0/+0x3ac values.
// No terrain, contact or input state is manufactured here.
TrackFrictionCoefficients track_contact_friction(
    const TrackGroundFriction& ground, const TrackContactMotion& motion,
    bool constant_mode, bool add_handbrake_side_friction,
    float forward_contact_scale, float side_contact_scale);

// #1513 0x00f89090 consumes the actual normal constraint reactions, not an
// invented weight split or a raycast-only contact approximation. The caller
// supplies NORMAL_FORCE_EXCESS_MAX and TRACKED_FRICTION_MODIFIER_* from the
// native physics tuning, plus chassis wheelDryFrictionFactor/sinkage fields.
struct TrackReactionParameters {
    float full_mass;
    float gravity;
    std::uint32_t wheels_per_track;
    float normal_force_excess_max;
    float wheel_dry_friction_factor;
    float wheel_sinkage_resist_factor;
    float broken_track_friction_modifier;
    float intact_track_friction_modifier;
};

struct TrackContactReactions {
    float normal_reaction;
    float paired_normal_reaction;
    bool any_track_broken;
    bool this_track_broken;
    // Original native step flag +0x134 and opposite roller +0x3c8.
    bool couple_paired_reactions;
    bool paired_contact_present;
};

struct TrackReactionLimits {
    float forward;
    float side;
};

TrackReactionLimits track_contact_reaction_limits(
    const TrackReactionParameters& parameters,
    const TrackContactReactions& contact);

struct TrackFrictionConstraintLimits {
    float forward_lower;
    float forward_upper;
    float side_lower;
    float side_upper;
};

// #1513 0x00f89acd..0x00f89b60: bounds written to the two real contact
// constraints. The gain comes from the original roller-slope calculation.
// These bounds do not replace the contact/engine constraint solver with a
// direct force, and do not prove that a vehicle can drive without that solver.
TrackFrictionConstraintLimits track_friction_constraint_limits(
    TrackFrictionCoefficients coefficients, TrackReactionLimits reactions,
    float forward_slope_gain, float side_slope_gain);

} // namespace wot
