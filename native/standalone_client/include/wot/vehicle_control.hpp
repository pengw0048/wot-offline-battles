#pragma once

#include <array>
#include <cstddef>

namespace wot::offline_vehicle_control {

// Port of offline_lan_0922/vehicle_physics.py's existing offline-mod laws.
// This is not the recovered #1513 native contact/engine solver. All scalar
// arithmetic uses double, matching Python float rather than native binary32.
struct Parameters {
    double mass;
    double power_w;
    double speed_forward;
    double speed_backward;
    double rotation_speed;
    std::array<double, 3> terrain_resistance;
    double specific_friction;
    double brake_deceleration;
    double track_center;
    double native_power_ratio = 1.0;
};

// Existing physics_tuning keys used by the ported laws, with their current
// source defaults. gravity_factor changes gravity independently of power_factor,
// exactly as apply_tuning() does. Vehicle parameters remain caller-owned.
struct Tuning {
    double gravity_factor = 1.25;
    double cohesion = 1.3;
    double drive_traction = 1.0;
    double slip_drag = 10.0;
    double slip_threshold_tan = 0.5205670505517462;
    double power_factor = 1.25;
    double backward_power_fraction = 1.0;
    double traverse_acceleration_time = 0.05;
    double traverse_speed_cost = 0.0;
    double coast_brake_share = 0.65;
    double steering_resistance_multiplier = 1.6;
    double slide_hold_tan = 0.50;
    double slide_kinetic = 0.45;
    double overspeed_max_factor = 1.05;
    double overspeed_damping = 2.0;
    double overspeed_build = 0.20;
    double slope_cohesion_decay = 0.25;
    double slope_cohesion_decay_y = 0.72;
    double cohesion_decay_bound = 0.5;
    double slide_max = 7.0;
    double slide_drag = 1.5;
    double fall_safe_speed = 10.0;
    double fall_damage_per_speed = 0.03;
};

double longitudinal_slope_grip(double slope_pitch, const Tuning& tuning = {});
double lateral_slope_grip(double normal_y);
double slope_cohesion(double normal_y, const Tuning& tuning = {});
double grip_deceleration(double slope_pitch, const Tuning& tuning = {});
double engine_force(const Parameters& parameters, double speed, double throttle,
                    double slope_pitch = 0, const Tuning& tuning = {});
double rolling_resist_force(const Parameters& parameters,
                            std::size_t terrain_index = 0,
                            bool steering = false, const Tuning& tuning = {});
double brake_force(const Parameters& parameters, bool active,
                   std::size_t terrain_index = 0, double slope_pitch = 0,
                   const Tuning& tuning = {});

// BigWorld convention: nose-up slope_pitch is negative. The caller supplies
// the same valid finite parameters/time slices used by the Python integrator;
// this port adds no new speed, slope, throttle or timestep clamps.
double longitudinal_step(const Parameters& parameters, double speed,
                         double throttle, bool steering, double slope_pitch,
                         double elapsed, bool airborne = false,
                         std::size_t terrain_index = 0, bool handbrake = false,
                         const Tuning& tuning = {});
double traverse_step(const Parameters& parameters, double angular_speed,
                     double steer_direction, double speed, double elapsed,
                     std::size_t terrain_index = 0, double drive_intent = 0,
                     const Tuning& tuning = {});

struct TrackScroll {
    double left;
    double right;
};

TrackScroll track_scroll(const Parameters& parameters, double speed,
                         double angular_speed);

} // namespace wot::offline_vehicle_control
