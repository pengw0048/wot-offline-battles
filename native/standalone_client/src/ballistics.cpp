#include "wot/ballistics.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>

namespace wot {

std::vector<BallisticSolution> ballistic_solutions(
    BallisticVector3 shooter, BallisticVector3 target, double projectile_speed,
    double gravity, double minimum_pitch, double maximum_pitch) {
    const double acceleration = std::abs(gravity);
    if (projectile_speed <= 1.0 || acceleration <= 0.01) {
        return {};
    }
    const double delta_x = target.x - shooter.x;
    const double delta_z = target.z - shooter.z;
    const double horizontal_distance = std::sqrt(
        delta_x * delta_x + delta_z * delta_z);
    if (horizontal_distance <= 0.1) {
        return {};
    }
    const double delta_y = target.y - shooter.y;
    const double speed_sq = projectile_speed * projectile_speed;
    const double discriminant = speed_sq * speed_sq - acceleration *
        (acceleration * horizontal_distance * horizontal_distance +
         2.0 * delta_y * speed_sq);
    if (discriminant < 0.0) {
        return {};
    }
    const double root = std::sqrt(std::max(0.0, discriminant));
    std::vector<BallisticSolution> result;
    for (double numerator : {speed_sq - root, speed_sq + root}) {
        const double elevation = std::atan(
            numerator / (acceleration * horizontal_distance));
        const double pitch = -elevation;
        if (pitch < minimum_pitch - 0.0001 ||
            pitch > maximum_pitch + 0.0001) {
            continue;
        }
        const double horizontal_speed = projectile_speed * std::cos(elevation);
        if (horizontal_speed <= 0.01) {
            continue;
        }
        const double flight_time = horizontal_distance / horizontal_speed;
        if (flight_time <= 0.0 ||
            flight_time > projectile_max_flight_seconds) {
            continue;
        }
        if (result.empty() || std::abs(pitch - result.back().pitch) > 0.00001) {
            result.push_back({pitch, flight_time});
        }
    }
    std::sort(result.begin(), result.end(),
              [](BallisticSolution left, BallisticSolution right) {
                  return left.flight_time < right.flight_time;
              });
    return result;
}

std::optional<BallisticIntercept> ballistic_intercept(
    BallisticVector3 shooter, BallisticVector3 target,
    BallisticVector3 target_velocity, double projectile_speed, double gravity,
    double minimum_pitch, double maximum_pitch, bool prefer_high,
    double max_lead_time) {
    const double maximum_time = std::max(0.0, max_lead_time);
    auto aim = target;
    BallisticSolution solution{};
    for (int iteration = 0; iteration < 4; ++iteration) {
        const auto solutions = ballistic_solutions(
            shooter, aim, projectile_speed, gravity, minimum_pitch, maximum_pitch);
        if (solutions.empty()) {
            return std::nullopt;
        }
        solution = prefer_high ? solutions.back() : solutions.front();
        if (maximum_time && solution.flight_time > maximum_time + 1e-9) {
            return std::nullopt;
        }
        aim = {target.x + target_velocity.x * solution.flight_time,
               target.y + target_velocity.y * solution.flight_time,
               target.z + target_velocity.z * solution.flight_time};
    }
    // Pitch and time must describe the final coordinate, not iteration three.
    const auto solutions = ballistic_solutions(
        shooter, aim, projectile_speed, gravity, minimum_pitch, maximum_pitch);
    if (solutions.empty()) {
        return std::nullopt;
    }
    solution = prefer_high ? solutions.back() : solutions.front();
    if (maximum_time && solution.flight_time > maximum_time + 1e-9) {
        return std::nullopt;
    }
    return BallisticIntercept{aim, solution.pitch, solution.flight_time};
}

BallisticVector3 ballistic_position(
    BallisticVector3 start, double yaw, double pitch, double projectile_speed,
    double gravity, double flight_time) {
    const double time = std::max(0.0, flight_time);
    const double horizontal_speed = std::cos(pitch) * projectile_speed;
    const double velocity_x = std::sin(yaw) * horizontal_speed;
    const double velocity_y = -std::sin(pitch) * projectile_speed;
    const double velocity_z = std::cos(yaw) * horizontal_speed;
    return {start.x + velocity_x * time,
            start.y + velocity_y * time - 0.5 * std::abs(gravity) * time * time,
            start.z + velocity_z * time};
}

std::vector<BallisticVector3> ballistic_path(
    BallisticVector3 start, double yaw, double pitch, double projectile_speed,
    double gravity, double flight_time, double maximum_step) {
    const double duration = std::max(0.0, flight_time);
    if (duration <= 0.0) {
        return {start};
    }
    const double step = std::max(0.02, std::min(0.25, maximum_step));
    const auto count = std::max(std::size_t{1},
        static_cast<std::size_t>(std::ceil(duration / step)));
    std::vector<BallisticVector3> result;
    result.reserve(count + 1);
    for (std::size_t index = 0; index <= count; ++index) {
        result.push_back(ballistic_position(
            start, yaw, pitch, projectile_speed, gravity,
            duration * static_cast<double>(index) / static_cast<double>(count)));
    }
    return result;
}

} // namespace wot
