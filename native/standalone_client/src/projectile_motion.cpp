#include "wot/projectile_motion.hpp"

#include <algorithm>
#include <cmath>

namespace wot {
namespace {
double dot(BallisticVector3 a, BallisticVector3 b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}
BallisticVector3 subtract(BallisticVector3 a, BallisticVector3 b) {
    return {a.x - b.x, a.y - b.y, a.z - b.z};
}
bool finite(BallisticVector3 v) {
    return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z);
}
double step_limit(double step) {
    return std::max(0.001, step == 0.0 ? projectile_max_substep_seconds : step);
}
} // namespace

double projectile_range_distance(BallisticVector3 start, BallisticVector3 point,
    std::optional<BallisticVector3> range_origin) {
    const auto delta = subtract(point, range_origin.value_or(start));
    return std::sqrt(dot(delta, delta));
}

std::optional<BallisticVector3> ideal_reflection_velocity(
    BallisticVector3 incoming_velocity, BallisticVector3 surface_normal) {
    if (!finite(incoming_velocity) || !finite(surface_normal)) return std::nullopt;
    const double length_sq = dot(surface_normal, surface_normal);
    if (!std::isfinite(length_sq) || length_sq <= 1.0e-24) return std::nullopt;
    const double inverse_length = 1.0 / std::sqrt(length_sq);
    const BallisticVector3 unit{surface_normal.x * inverse_length,
        surface_normal.y * inverse_length, surface_normal.z * inverse_length};
    const double projection = dot(incoming_velocity, unit);
    const BallisticVector3 result{
        incoming_velocity.x - 2.0 * projection * unit.x,
        incoming_velocity.y - 2.0 * projection * unit.y,
        incoming_velocity.z - 2.0 * projection * unit.z};
    if (!finite(result)) return std::nullopt;
    return result;
}

BallisticVector3 trajectory_position(BallisticVector3 start,
    BallisticVector3 velocity, BallisticVector3 gravity, double elapsed) {
    const double time = std::max(0.0, elapsed);
    const double half_time_sq = 0.5 * time * time;
    return {start.x + velocity.x * time + gravity.x * half_time_sq,
        start.y + velocity.y * time + gravity.y * half_time_sq,
        start.z + velocity.z * time + gravity.z * half_time_sq};
}

BallisticVector3 lerp3(BallisticVector3 first, BallisticVector3 second,
    double fraction) {
    const double value = std::max(0.0, std::min(1.0, fraction));
    return {first.x + (second.x - first.x) * value,
        first.y + (second.y - first.y) * value,
        first.z + (second.z - first.z) * value};
}

ProjectileSegment compensate_segment_for_moving_target(
    BallisticVector3 projectile_start, BallisticVector3 projectile_end,
    BallisticVector3 target_previous, BallisticVector3 target_current,
    double interval_start, double interval_end) {
    const auto first = lerp3(target_previous, target_current, interval_start);
    const auto second = lerp3(target_previous, target_current, interval_end);
    return {{projectile_start.x + target_current.x - first.x,
             projectile_start.y + target_current.y - first.y,
             projectile_start.z + target_current.z - first.z},
            {projectile_end.x + target_current.x - second.x,
             projectile_end.y + target_current.y - second.y,
             projectile_end.z + target_current.z - second.z}};
}

double point_segment_distance_sq(BallisticVector3 point,
    BallisticVector3 start, BallisticVector3 end) {
    const auto segment = subtract(end, start);
    const auto offset = subtract(point, start);
    const double denominator = dot(segment, segment);
    if (denominator <= 1e-12) return dot(offset, offset);
    const double fraction = std::max(0.0, std::min(1.0,
        dot(offset, segment) / denominator));
    const BallisticVector3 delta{offset.x - segment.x * fraction,
        offset.y - segment.y * fraction, offset.z - segment.z * fraction};
    return dot(delta, delta);
}

bool point_in_expanded_segment_bounds(BallisticVector3 point,
    BallisticVector3 start, BallisticVector3 end, double radius) {
    radius = std::max(0.0, radius);
    const double points[]{point.x, point.y, point.z};
    const double starts[]{start.x, start.y, start.z};
    const double ends[]{end.x, end.y, end.z};
    for (int index = 0; index < 3; ++index) {
        if (points[index] < std::min(starts[index], ends[index]) - radius ||
            points[index] > std::max(starts[index], ends[index]) + radius) return false;
    }
    return true;
}

double parabolic_chord_error(double gravity, double duration) {
    duration = std::max(0.0, duration);
    return std::abs(gravity) * duration * duration / 8.0;
}
double parabolic_chord_error(BallisticVector3 gravity, double duration) {
    return parabolic_chord_error(std::sqrt(dot(gravity, gravity)), duration);
}

double curvature_limited_substep(double gravity, double maximum_step,
    double maximum_error) {
    maximum_step = step_limit(maximum_step);
    maximum_error = std::max(1e-9, maximum_error == 0.0 ?
        projectile_max_chord_error_meters : maximum_error);
    const double magnitude = std::abs(gravity);
    if (magnitude <= 1e-12) return maximum_step;
    const double error_limited = std::sqrt(8.0 * maximum_error / magnitude);
    return std::max(0.001, std::min(maximum_step, error_limited));
}
double curvature_limited_substep(BallisticVector3 gravity, double maximum_step,
    double maximum_error) {
    return curvature_limited_substep(std::sqrt(dot(gravity, gravity)),
        maximum_step, maximum_error);
}

std::vector<ProjectileTimeInterval> substep_boundaries(double start_time,
    double end_time, double maximum_step) {
    start_time = std::max(0.0, start_time);
    end_time = std::max(start_time, end_time);
    maximum_step = step_limit(maximum_step);
    std::vector<ProjectileTimeInterval> intervals;
    double cursor = start_time;
    while (cursor + 1e-9 < end_time) {
        const double next_time = std::min(end_time, cursor + maximum_step);
        intervals.push_back({cursor, next_time});
        cursor = next_time;
    }
    return intervals;
}

} // namespace wot
