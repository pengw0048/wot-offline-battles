#pragma once

#include <optional>
#include <vector>

namespace wot {

// Python float precision is retained independently of engine float vectors.
struct BallisticVector3 {
    double x;
    double y;
    double z;
};

struct BallisticSolution {
    double pitch;
    double flight_time;
};

struct BallisticIntercept {
    BallisticVector3 aim_point;
    double pitch;
    double flight_time;
};

inline constexpr double projectile_max_flight_seconds = 20.0;
inline constexpr double ballistic_half_pi = 1.57079632679489661923;

// Pure port of offline_lan_0922/ballistics.py. Inputs must be finite numbers
// with finite intermediate arithmetic; path sample counts must fit memory.
// Negative pitch elevates the barrel; either gravity sign means downward.
// Roots are ordered by flight time, low arc first.
std::vector<BallisticSolution> ballistic_solutions(
    BallisticVector3 shooter, BallisticVector3 target, double projectile_speed,
    double gravity, double minimum_pitch = -ballistic_half_pi,
    double maximum_pitch = ballistic_half_pi);

std::optional<BallisticIntercept> ballistic_intercept(
    BallisticVector3 shooter, BallisticVector3 target,
    BallisticVector3 target_velocity, double projectile_speed, double gravity,
    double minimum_pitch = -ballistic_half_pi,
    double maximum_pitch = ballistic_half_pi, bool prefer_high = false,
    double max_lead_time = projectile_max_flight_seconds);

BallisticVector3 ballistic_position(
    BallisticVector3 start, double yaw, double pitch, double projectile_speed,
    double gravity, double flight_time);

std::vector<BallisticVector3> ballistic_path(
    BallisticVector3 start, double yaw, double pitch, double projectile_speed,
    double gravity, double flight_time, double maximum_step = 0.12);

} // namespace wot
