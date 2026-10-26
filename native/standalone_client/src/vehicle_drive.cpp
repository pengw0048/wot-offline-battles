#include "wot/vehicle_drive.hpp"
#include "wot/vehicle_physics.hpp"

#include <cmath>

namespace wot {
namespace {

// MINSS returns its second operand for equality and unordered inputs.
float native_min(float first, float second) {
    return first < second ? first : second;
}

float native_max(float first, float second) {
    return first > second ? first : second;
}

float tangent_speed(Vector3 tangent, Vector3 velocity) {
    return std::fabs((tangent.y * velocity.y + tangent.x * velocity.x) +
                     tangent.z * velocity.z);
}

TrackFrictionCoefficients slip_ellipse(float side_limit, float forward_limit,
                                      float side_speed, float forward_speed) {
    // #1513 0x00f967a0..0x00f9684e. Choosing the larger speed as the divisor
    // also preserves the original zero-slip and small-slip behavior.
    if (!(side_speed + forward_speed > 0.01f))
        return {forward_limit, side_limit};
    const float side_squared = side_limit * side_limit;
    const float forward_squared = forward_limit * forward_limit;
    if (forward_speed > side_speed) {
        const float ratio = side_speed / forward_speed;
        const float norm = std::sqrt((ratio * ratio / side_squared) +
                                     (1.0f / forward_squared));
        const float forward = 1.0f / norm;
        return {forward, forward * ratio};
    }
    const float ratio = forward_speed / side_speed;
    const float norm = std::sqrt((ratio * ratio / forward_squared) +
                                 (1.0f / side_squared));
    const float side = 1.0f / norm;
    return {side * ratio, side};
}

} // namespace

VehicleEngineTorqueSettings build_vehicle_engine_torque_settings(
    const VehicleEngineConfiguration& configuration) {
    // Original Engine::Settings constructor float bits 0x42517084 and
    // 0x43517084. The reader builds this curve before overwriting min/max RPM.
    const float initial_minimum = 52.3598785400390625f;
    const float initial_maximum = 209.43951416015625f;
    return {{{{initial_minimum, configuration.losses[0]},
               {initial_maximum, configuration.losses[1]},
               {initial_maximum * 3.0f, configuration.losses[1] * 6.0f}}},
             configuration.simple_engine_power,
             configuration.start_rpm * 0.10471976f};
}

VehicleEngineTorque prepare_vehicle_engine_torque(
    const VehicleEngineTorqueSettings& settings, float angular_velocity,
    float power_scale, bool use_start_torque) {
    // Match the original PointCurve scan, including endpoint behavior.
    float losses = settings.losses[0].torque;
    // COMISS/JA at 0x00f78b70 admits unordered values into the scan.
    if (!(settings.losses[0].angular_velocity > angular_velocity)) {
        bool interpolated = false;
        for (std::size_t index = 0; index + 1 < settings.losses.size(); ++index) {
            const auto& left = settings.losses[index];
            const auto& right = settings.losses[index + 1];
            if (angular_velocity <= right.angular_velocity) {
                losses = ((angular_velocity - left.angular_velocity) /
                          (right.angular_velocity - left.angular_velocity)) *
                         (right.torque - left.torque) + left.torque;
                interpolated = true;
                break;
            }
        }
        if (!interpolated) losses = settings.losses.back().torque;
    }
    const float speed = native_max(angular_velocity, 31.415928f);
    const float torque = settings.power / speed;
    float available = torque;
    if (use_start_torque) {
        available = native_max(settings.power / settings.start_angular_velocity,
                                torque);
    }
    return {power_scale * available + losses, losses};
}

void accumulate_vehicle_engine_torque(VehicleRigidBody& engine_body,
    VehicleEngineTorque torque, bool running, float throttle,
    float inertial_torque, std::int32_t movement_state) {
    float local_torque = 0;
    if (running) {
        float assistance = 0;
        if (0.0f < throttle && movement_state != 0) assistance = inertial_torque;
        local_torque = (torque.gross * throttle + assistance) - torque.losses;
    }
    const auto& pose = engine_body.transform.rows;
    engine_body.loads.torque.x += local_torque * pose[0][0];
    engine_body.loads.torque.y += local_torque * pose[0][1];
    engine_body.loads.torque.z += local_torque * pose[0][2];
}

TrackFrictionCoefficients track_contact_friction(
    const TrackGroundFriction& ground, const TrackContactMotion& motion,
    bool constant_mode, bool add_handbrake_side_friction,
    float forward_contact_scale, float side_contact_scale) {
    TrackFrictionCoefficients result{ground.forward_friction,
                                     ground.side_friction};
    if (!constant_mode) {
        // The original consumer uses the roller rigid body's current pose
        // and velocity at its contact, rather than the hull's spawn pose.
        const Vector3 velocity = point_velocity(
            motion.contact_point, motion.center_of_mass,
            motion.angular_velocity, motion.linear_velocity);
        result = slip_ellipse(ground.side_friction, ground.forward_friction,
                              tangent_speed(motion.side_tangent, velocity),
                              tangent_speed(motion.forward_tangent, velocity));
        const float constant_ratio = ground.side_friction_constant_ratio;
        result.side = (1.0f - constant_ratio) * result.side +
                       ground.side_friction * constant_ratio;
    }
    if (add_handbrake_side_friction)
        result.side = ground.handbrake_side_friction_addition + result.side;
    result.forward = forward_contact_scale * result.forward;
    result.side = side_contact_scale * result.side;
    return result;
}

TrackReactionLimits track_contact_reaction_limits(
    const TrackReactionParameters& parameters,
    const TrackContactReactions& contact) {
    // #1513 0x00f89098..0x00f89160. Keep the original multiply/divide order.
    const float nominal_reaction =
        ((parameters.full_mass * 0.5f) * parameters.gravity) /
        static_cast<float>(parameters.wheels_per_track);
    const float normal_cap = parameters.normal_force_excess_max * nominal_reaction;
    const float local_reaction = std::fabs(contact.normal_reaction);
    const float dry = native_min(local_reaction, normal_cap) *
                       parameters.wheel_dry_friction_factor;
    const float sinkage = parameters.wheel_sinkage_resist_factor * nominal_reaction;
    TrackReactionLimits result{dry + sinkage, dry + sinkage};
    if (contact.any_track_broken) {
        const float modifier = contact.this_track_broken
            ? parameters.broken_track_friction_modifier
            : parameters.intact_track_friction_modifier;
        result.forward = modifier * result.forward;
    } else if (contact.couple_paired_reactions && contact.paired_contact_present) {
        // #1513 0x00f891bc..0x00f8923b replaces only the forward limit.
        const float pair_mean =
            (std::fabs(contact.paired_normal_reaction) + local_reaction) * 0.5f;
        const float pair_dry = native_min(normal_cap, pair_mean) *
                               parameters.wheel_dry_friction_factor;
        result.forward = pair_dry + sinkage;
    }
    return result;
}

TrackFrictionConstraintLimits track_friction_constraint_limits(
    TrackFrictionCoefficients coefficients, TrackReactionLimits reactions,
    float forward_slope_gain, float side_slope_gain) {
    const float forward = (coefficients.forward * reactions.forward) * forward_slope_gain;
    const float side = (coefficients.side * reactions.side) * side_slope_gain;
    return {-forward, forward, -side, side};
}

} // namespace wot
