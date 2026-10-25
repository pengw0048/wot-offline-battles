#include "wot/projectile_motion.hpp"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
bool close(double a, double b) {
    return std::abs(a - b) <= 1e-12 * std::max({1.0, std::abs(a), std::abs(b)});
}
bool close(wot::BallisticVector3 a, wot::BallisticVector3 b) {
    return close(a.x, b.x) && close(a.y, b.y) && close(a.z, b.z);
}
void regressions() {
    using namespace wot;
    const BallisticVector3 zero{0, 0, 0};
    require(close(trajectory_position({1, 2, 3}, {100, 10, -20}, {0, -10, 2}, 2),
        {201, 2, -33}), "signed vector gravity");
    require(close(trajectory_position({1, 2, 3}, {100, 10, -20}, {0, -10, 2}, -1),
        {1, 2, 3}), "negative elapsed clamps to zero");
    require(close(projectile_range_distance(zero, {6, 8, 0}), 10),
        "straight range rather than travelled path");
    require(close(projectile_range_distance({100, 100, 100}, {4, 6, 15},
        BallisticVector3{1, 2, 3}), 13), "frozen origin overrides muzzle");
    const auto reflected = ideal_reflection_velocity({3, -4, 12}, {0, 7, 0});
    require(reflected && close(*reflected, {3, 4, 12}), "normal normalization");
    require(!ideal_reflection_velocity({1, 2, 3}, zero) &&
        !ideal_reflection_velocity({1, 2, 3}, {1e-13, 0, 0}) &&
        !ideal_reflection_velocity({1, 2, 3}, {1e308, 0, 0}) &&
        !ideal_reflection_velocity({1, 2, std::numeric_limits<double>::quiet_NaN()}, {0, 1, 0}) &&
        !ideal_reflection_velocity({1, 2, 3}, {0, std::numeric_limits<double>::infinity(), 0}),
        "reflection rejects degenerate, overflow and nonfinite values");
    require(close(lerp3(zero, {8, 4, 2}, -1), zero) &&
        close(lerp3(zero, {8, 4, 2}, 2), {8, 4, 2}), "lerp clamps");
    const auto compensated = compensate_segment_for_moving_target(
        {20, 0, 0}, {30, 0, 0}, zero, {8, 0, 0}, .25, .75);
    require(close(compensated.start, {26, 0, 0}) &&
        close(compensated.end, {32, 0, 0}), "partial frame target compensation");
    require(close(point_segment_distance_sq({5, 2, 0}, zero, {10, 0, 0}), 4) &&
        close(point_segment_distance_sq({15, 2, 0}, zero, {10, 0, 0}), 29) &&
        close(point_segment_distance_sq({-5, 2, 0}, zero, {10, 0, 0}), 29) &&
        close(point_segment_distance_sq({5, 2, 0}, zero, zero), 29),
        "finite and degenerate segment distances");
    require(point_in_expanded_segment_bounds({5, 15, 0}, zero, {10, 0, 0}, 15) &&
        !point_in_expanded_segment_bounds({5, 15.0001, 0}, zero, {10, 0, 0}, 15),
        "expanded AABB boundary");
    require(point_in_expanded_segment_bounds({25, 15, 0}, {10, 0, 0}, zero, 15) &&
        point_segment_distance_sq({25, 15, 0}, zero, {10, 0, 0}) > 225,
        "conservative broadphase allows corners and reversed segments");
    require(!point_in_expanded_segment_bounds({5, .001, 0}, zero, {10, 0, 0}, -1),
        "negative radius clamps to zero");
    require(close(curvature_limited_substep({0, -143, 0}), .05) &&
        close(parabolic_chord_error(143, .05), .0446875), "B4 gravity step");
    for (double gravity : {190.0, 500.0}) {
        const double step = curvature_limited_substep({0, -gravity, 0});
        require(close(step, std::sqrt(.4 / gravity)) &&
            close(parabolic_chord_error({0, -gravity, 0}, step), .05),
            "high gravity limits sagitta");
    }
    require(close(curvature_limited_substep(0, 0, 0), .05) &&
        close(curvature_limited_substep(0, -1, -1), .001) &&
        close(curvature_limited_substep(1e20), .001) &&
        close(parabolic_chord_error(-143, -1), 0), "defaults and lower bounds");
    const auto slow = substep_boundaries(.10, .21, .025);
    double sum = 0, previous = .10;
    for (auto interval : slow) {
        require(close(interval.start, previous) && interval.end > interval.start &&
            interval.end - interval.start <= .025 + 1e-12, "contiguous slow-frame substeps");
        sum += interval.end - interval.start;
        previous = interval.end;
    }
    require(slow.size() == 5 && close(previous, .21) && close(sum, .11),
        "slow frame elapsed time fully retained");
    require(substep_boundaries(1, 0).empty() &&
        substep_boundaries(0, 1e-10).empty() &&
        substep_boundaries(-1, .1, 0).size() == 2 &&
        substep_boundaries(0, .003, -1).size() == 3, "time clamps and defaults");
    for (int fps : {30, 60, 120}) {
        bool crossed = false;
        double total = 0;
        for (int frame = 0; frame < fps; ++frame) {
            const double begin = double(frame) / fps;
            const double end = double(frame + 1) / fps;
            const BallisticVector3 target_previous{50 + 20 * begin, 0, 0};
            const BallisticVector3 target_current{50 + 20 * end, 0, 0};
            for (auto interval : substep_boundaries(begin, end)) {
                total += interval.end - interval.start;
                const auto segment = compensate_segment_for_moving_target(
                    {100 * interval.start, 0, 0}, {100 * interval.end, 0, 0},
                    target_previous, target_current,
                    (interval.start - begin) * fps, (interval.end - begin) * fps);
                crossed = crossed || point_segment_distance_sq(target_current,
                    segment.start, segment.end) <= 1e-12;
            }
        }
        require(crossed && close(total, 1), "frame invariant relative numerical sweep");
    }
}
} // namespace

int main() {
    try {
        regressions();
    } catch (const std::exception& error) {
        std::cerr << "projectile motion regression failed: " << error.what() << '\n';
        return 1;
    }
    std::cout << "projectile motion regressions passed\n";
}
