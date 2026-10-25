#include "wot/vehicle_control.hpp"

#include <algorithm>
#include <cmath>

namespace wot::offline_vehicle_control {
namespace {

constexpr double gravity_base = 9.81;
constexpr double engine_minimum_speed = 1.5;
constexpr double scroll_cap = 0.995;
constexpr double cohesion_decay_y = 0.969;
constexpr double cohesion_decay_factor = 5.78;
constexpr double cohesion_decay_power = 3.0;
constexpr double longitudinal_full_y = 0.8870108331782217;
constexpr double longitudinal_minimum_y = 0.848048096156426;
constexpr double lateral_full_y = 0.9099612708765432;
constexpr double lateral_minimum_y = 0.8746197071393957;

double gravity(const Tuning& tuning) {
    return gravity_base * tuning.gravity_factor;
}

double slope_grip(double normal_y, double full_y, double minimum_y) {
    if (normal_y >= full_y) return 1.0;
    if (normal_y <= minimum_y) return 0.1;
    const double progress = (normal_y - minimum_y) / (full_y - minimum_y);
    return 0.1 + progress * (1.0 - 0.1);
}

} // namespace

double longitudinal_slope_grip(double slope_pitch, const Tuning& tuning) {
    return slope_grip(std::cos(slope_pitch), longitudinal_full_y,
                       longitudinal_minimum_y) * tuning.drive_traction;
}

double lateral_slope_grip(double normal_y) {
    normal_y = std::max(0.0, std::min(1.0, normal_y));
    return slope_grip(normal_y, lateral_full_y, lateral_minimum_y);
}

double slope_cohesion(double normal_y, const Tuning& tuning) {
    double cohesion = tuning.cohesion;
    if (normal_y < cohesion_decay_y)
        cohesion -= cohesion_decay_factor *
                    std::pow(cohesion_decay_y - normal_y, cohesion_decay_power);
    if (normal_y < tuning.slope_cohesion_decay_y)
        cohesion -= tuning.slope_cohesion_decay;
    return cohesion > tuning.cohesion_decay_bound
        ? cohesion : tuning.cohesion_decay_bound;
}

double grip_deceleration(double slope_pitch, const Tuning& tuning) {
    const double normal_y = std::cos(slope_pitch);
    return slope_cohesion(normal_y, tuning) * gravity(tuning) *
           (normal_y > 0.1 ? normal_y : 0.1);
}

double engine_force(const Parameters& parameters, double speed, double throttle,
                    double slope_pitch, const Tuning& tuning) {
    if (throttle == 0) return 0.0;
    double power = parameters.power_w * tuning.power_factor *
                   parameters.native_power_ratio;
    if (throttle < 0) power *= tuning.backward_power_fraction;
    double force = power / std::max(std::fabs(speed), engine_minimum_speed);
    const double normal_y = std::cos(slope_pitch);
    const double maximum = longitudinal_slope_grip(slope_pitch, tuning) *
                           parameters.mass * gravity(tuning) *
                           (normal_y > 0.1 ? normal_y : 0.1);
    if (force > maximum) force = maximum;
    return force * throttle;
}

double rolling_resist_force(const Parameters& parameters,
                            std::size_t terrain_index, bool steering,
                            const Tuning& tuning) {
    double force = parameters.mass * parameters.specific_friction *
                   tuning.gravity_factor *
                   parameters.terrain_resistance.at(terrain_index);
    if (steering) force *= tuning.steering_resistance_multiplier;
    return force;
}

double brake_force(const Parameters& parameters, bool active,
                   std::size_t terrain_index, double slope_pitch,
                   const Tuning& tuning) {
    const double grip = grip_deceleration(slope_pitch, tuning);
    const double brake = parameters.brake_deceleration < grip
        ? parameters.brake_deceleration : grip;
    if (active) return parameters.mass * brake;
    return rolling_resist_force(parameters, terrain_index, false, tuning) +
           parameters.mass * tuning.coast_brake_share * brake;
}

double longitudinal_step(const Parameters& parameters, double speed,
                         double throttle, bool steering, double slope_pitch,
                         double elapsed, bool airborne,
                         std::size_t terrain_index, bool handbrake,
                         const Tuning& tuning) {
    // Same branches and constants as vehicle_physics.py::longitudinal_step.
    if (airborne) return speed;
    const double g = gravity(tuning);
    const double gravity_acceleration = g * std::sin(slope_pitch);
    if (handbrake && !airborne) {
        const double grip = grip_deceleration(slope_pitch, tuning);
        if (std::fabs(speed) < 0.05) {
            return std::fabs(gravity_acceleration) <= grip ? 0.0
                : speed + (gravity_acceleration -
                    (gravity_acceleration > 0.0 ? grip : -grip)) * elapsed;
        }
        const double acceleration = gravity_acceleration -
                                    (speed > 0.0 ? grip : -grip);
        const double next = speed + acceleration * elapsed;
        if ((speed > 0.0) != (next > 0.0)) return 0.0;
        return next;
    }
    const double grip = grip_deceleration(slope_pitch, tuning);
    const double rolling = rolling_resist_force(parameters, terrain_index,
                                                 steering, tuning) / parameters.mass;
    double acceleration;
    if (throttle != 0) {
        double engine = engine_force(parameters, speed, throttle, slope_pitch,
                                      tuning) / parameters.mass;
        const double normal_y = std::cos(slope_pitch);
        const double maximum_climb = longitudinal_slope_grip(slope_pitch, tuning) *
            g * (normal_y > 0.1 ? normal_y : 0.1);
        const bool cannot_climb = throttle * gravity_acceleration < 0.0 &&
            maximum_climb < std::fabs(gravity_acceleration) + rolling;
        if (cannot_climb) engine = 0.0;
        acceleration = engine + gravity_acceleration;
        double rolling_factor = speed / 0.08;
        if (rolling_factor > 1.0) rolling_factor = 1.0;
        else if (rolling_factor < -1.0) rolling_factor = -1.0;
        acceleration -= rolling * rolling_factor;
        if (cannot_climb) {
            if (std::fabs(speed) > 0.05) {
                const double kinetic = tuning.slide_kinetic * g *
                                       (normal_y > 0.1 ? normal_y : 0.1);
                acceleration += speed < 0.0 ? kinetic : -kinetic;
            }
        } else if ((throttle > 0 && speed < -0.1) ||
                    (throttle < 0 && speed > 0.1)) {
            const double need = -speed / elapsed - acceleration;
            acceleration += std::fabs(need) < grip
                ? need : (need > 0.0 ? grip : -grip);
        }
    } else if (std::fabs(speed) < 0.02) {
        const double normal_y = std::cos(slope_pitch);
        const double hold = tuning.slide_hold_tan * g *
                            (normal_y > 0.1 ? normal_y : 0.1);
        if (std::fabs(gravity_acceleration) <= hold) return 0.0;
        acceleration = gravity_acceleration -
                       (gravity_acceleration > 0.0 ? hold : -hold);
    } else {
        const double motion_sign = speed > 0.0 ? 1.0 : -1.0;
        const double downhill_tangent = std::max(0.0,
            std::tan(slope_pitch) * motion_sign);
        const double fade_start = 0.8 * tuning.slide_hold_tan;
        const double fade = std::min(1.0, std::max(0.0,
            (downhill_tangent - fade_start) / (tuning.slide_hold_tan - fade_start)));
        const double resist = rolling + tuning.coast_brake_share * (1.0 - fade) * grip;
        acceleration = gravity_acceleration - (speed > 0.0 ? resist : -resist);
    }

    // Uphill slip drag applies to both powered and coasting movement.
    const double uphill_grade = speed > 0.0 ? -slope_pitch : slope_pitch;
    if (!airborne && std::fabs(speed) > 0.5 && uphill_grade > 0.0) {
        const double tangent = std::tan(uphill_grade);
        if (tangent > tuning.slip_threshold_tan) {
            const double slip = tuning.slip_drag *
                                (tangent - tuning.slip_threshold_tan) * g;
            acceleration -= speed > 0.0 ? slip : -slip;
        }
    }
    double next = speed + acceleration * elapsed;
    if (throttle == 0 && std::fabs(gravity_acceleration) <= grip && speed != 0.0 &&
        (speed > 0.0) != (next > 0.0)) next = 0.0;

    const double direction = next >= 0.0 ? 1.0 : -1.0;
    const double limit = next >= 0.0 ? parameters.speed_forward : parameters.speed_backward;
    if (std::fabs(next) > limit) {
        const double cap = limit * (tuning.overspeed_max_factor - 1.0);
        double previous_excess = std::fabs(speed) - limit;
        if (previous_excess < 0.0) previous_excess = 0.0;
        double excess;
        if (throttle * direction > 0.0 && gravity_acceleration * direction > 0.05)
            excess = previous_excess + tuning.overspeed_build *
                     std::sin(std::fabs(slope_pitch)) * elapsed;
        else
            excess = previous_excess - (rolling + tuning.overspeed_damping) * elapsed;
        if (excess < 0.0) excess = 0.0;
        if (excess > cap) excess = cap;
        next = direction * (limit + excess);
    }
    return next;
}

double traverse_step(const Parameters& parameters, double angular_speed,
                     double steer_direction, double speed, double elapsed,
                     std::size_t terrain_index, double drive_intent,
                     const Tuning& tuning) {
    const double speed_ratio = std::fabs(speed) / std::max(parameters.speed_forward, 0.1);
    const double rotation_modifier = 1.0 / (1.0 + speed_ratio * tuning.traverse_speed_cost);
    const double terrain_modifier = parameters.terrain_resistance[0] /
                                   parameters.terrain_resistance.at(terrain_index);
    const double maximum_rotation = parameters.rotation_speed * rotation_modifier * terrain_modifier;
    const double intent_sign = drive_intent < 0.0 ? -1.0 : 1.0;
    const double target = steer_direction * intent_sign * maximum_rotation;
    const double difference = target - angular_speed;
    const double ramp = maximum_rotation / tuning.traverse_acceleration_time;
    if (std::fabs(difference) < ramp * elapsed)
        angular_speed = target;
    else
        angular_speed += ramp * elapsed * (difference > 0 ? 1 : -1);
    if (steer_direction == 0 && std::fabs(angular_speed) < 0.01) angular_speed = 0.0;
    return angular_speed;
}

TrackScroll track_scroll(const Parameters& parameters, double speed,
                         double angular_speed) {
    double left = speed - angular_speed * parameters.track_center;
    double right = speed + angular_speed * parameters.track_center;
    const double cap = parameters.speed_forward * scroll_cap;
    if (left > cap) left = cap;
    else if (left < -cap) left = -cap;
    if (right > cap) right = cap;
    else if (right < -cap) right = -cap;
    return {left, right};
}

} // namespace wot::offline_vehicle_control
