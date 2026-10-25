#pragma once

#include "wot/ballistics.hpp"
#include <optional>
#include <vector>

namespace wot {

inline constexpr double projectile_callback_seconds = 0.01;
inline constexpr double projectile_max_substep_seconds = 0.05;
inline constexpr double projectile_max_chord_error_meters = 0.05;
inline constexpr double projectile_broadphase_radius = 15.0;

struct ProjectileSegment {
    BallisticVector3 start;
    BallisticVector3 end;
};

struct ProjectileTimeInterval {
    double start;
    double end;
};

// Pure numerical port of projectile_runtime.py; no collision queries or clocks.
// Vectors have exactly three components by construction. Except for reflection,
// callers supply finite values and finite intermediate arithmetic.
double projectile_range_distance(BallisticVector3 start, BallisticVector3 point,
    std::optional<BallisticVector3> range_origin = std::nullopt);
std::optional<BallisticVector3> ideal_reflection_velocity(
    BallisticVector3 incoming_velocity, BallisticVector3 surface_normal);
BallisticVector3 trajectory_position(BallisticVector3 start,
    BallisticVector3 velocity, BallisticVector3 gravity, double elapsed);
BallisticVector3 lerp3(BallisticVector3 first, BallisticVector3 second,
    double fraction);
ProjectileSegment compensate_segment_for_moving_target(
    BallisticVector3 projectile_start, BallisticVector3 projectile_end,
    BallisticVector3 target_previous, BallisticVector3 target_current,
    double interval_start = 0.0, double interval_end = 1.0);
double point_segment_distance_sq(BallisticVector3 point,
    BallisticVector3 start, BallisticVector3 end);
bool point_in_expanded_segment_bounds(BallisticVector3 point,
    BallisticVector3 start, BallisticVector3 end, double radius);
double parabolic_chord_error(BallisticVector3 gravity, double duration);
double parabolic_chord_error(double gravity, double duration);
double curvature_limited_substep(BallisticVector3 gravity,
    double maximum_step = projectile_max_substep_seconds,
    double maximum_error = projectile_max_chord_error_meters);
double curvature_limited_substep(double gravity,
    double maximum_step = projectile_max_substep_seconds,
    double maximum_error = projectile_max_chord_error_meters);
// The entire elapsed interval is retained, including slow frames. The caller
// must bound the duration so that the resulting interval list fits memory.
std::vector<ProjectileTimeInterval> substep_boundaries(double start_time,
    double end_time, double maximum_step = projectile_max_substep_seconds);

} // namespace wot
