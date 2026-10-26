#include "wot/gun_ballistics.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace wot {
namespace {
constexpr float two_pi = 6.283185482025146484375f;

struct AimSolution {
    float error;
    float yaw;
    float pitch;
    float time;
};

float dot(Vector3 a, Vector3 b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

float length(Vector3 value) { return std::sqrt(dot(value, value)); }

bool finite(Vector3 value) {
    return std::isfinite(value.x) && std::isfinite(value.y) && std::isfinite(value.z);
}

using Matrix3 = std::array<std::array<float, 3>, 3>;

// Native 0x00f476d0 checks exactly zero determinant, without an epsilon.
bool inverse3(const Matrix3& m, Matrix3& inverse) {
    const float cofactor = m[1][1] * m[2][2] - m[1][2] * m[2][1];
    const float determinant = (cofactor * m[0][0] -
        (m[1][0] * m[2][2] - m[2][0] * m[1][2]) * m[0][1]) +
        (m[1][0] * m[2][1] - m[2][0] * m[1][1]) * m[0][2];
    if (determinant == 0.0f) return false;
    const float reciprocal = 1.0f / determinant;
    inverse[0][0] = cofactor * reciprocal;
    inverse[0][1] = (m[2][1] * m[0][2] - m[2][2] * m[0][1]) * reciprocal;
    inverse[0][2] = (m[1][2] * m[0][1] - m[1][1] * m[0][2]) * reciprocal;
    inverse[1][0] = (m[2][0] * m[1][2] - m[1][0] * m[2][2]) * reciprocal;
    inverse[1][1] = (m[0][0] * m[2][2] - m[2][0] * m[0][2]) * reciprocal;
    inverse[1][2] = (m[1][0] * m[0][2] - m[0][0] * m[1][2]) * reciprocal;
    inverse[2][0] = (m[2][1] * m[1][0] - m[2][0] * m[1][1]) * reciprocal;
    inverse[2][1] = (m[2][0] * m[0][1] - m[2][1] * m[0][0]) * reciprocal;
    inverse[2][2] = (m[1][1] * m[0][0] - m[1][0] * m[0][1]) * reciprocal;
    return true;
}

Vector3 transform_normal(Vector3 value, const Matrix3& matrix) {
    return {
        value.x * matrix[0][0] + value.y * matrix[1][0] + value.z * matrix[2][0],
        value.x * matrix[0][1] + value.y * matrix[1][1] + value.z * matrix[2][1],
        value.x * matrix[0][2] + value.y * matrix[1][2] + value.z * matrix[2][2]
    };
}

// Native 0x00f45b00: its initial two-iteration estimate handles the lateral
// muzzle offset and all three components of gravity in vehicle-local space.
AimSolution initial_aim(const GunAimGeometry& g, float speed, int iterations) {
    const float reciprocal = 1.0f / speed;
    float time = length(g.target) * reciprocal;
    const float a = g.gravity.y * -0.5f * reciprocal;
    const float b = reciprocal * g.target.y;
    float correction_x = g.gravity.x * 0.5f * time * time - g.target.x;
    float correction_z = g.gravity.z * 0.5f * time * time - g.target.z;
    AimSolution previous{1.0e8f, 0.0f, 0.0f, time};
    for (int iteration = 0; iteration < iterations; ++iteration) {
        correction_x = correction_x * reciprocal;
        correction_z = correction_z * reciprocal;
        const float yaw = std::atan2(-correction_x, -correction_z);
        const float sine = std::sin(yaw), cosine = std::cos(yaw);
        float component = sine;
        float squared = sine * sine;
        float horizontal = correction_x;
        if (squared <= 0.1f) {
            component = cosine;
            squared = cosine * cosine;
            horizontal = correction_z;
            if (squared < 1.0e-8f) {
                squared = 1.0e-8f;
                component = 0.0001f;
            }
        }
        const float linear = ((a + a) * b - 1.0f) * squared;
        const float quadratic = std::max(squared * a * a, 1.0e-8f);
        const float discriminant = linear * linear -
            (squared * b * b + horizontal * horizontal) * quadratic * 4.0f;
        float time_squared;
        if (discriminant >= 0.0f) {
            const float root = std::sqrt(discriminant);
            time_squared = std::min((root - linear) * (0.5f / quadratic),
                                    (-linear - root) * (0.5f / quadratic));
        } else {
            time_squared = (-0.5f / quadratic) * linear;
        }
        time_squared = std::max(time_squared, 1.0e-8f);
        time = std::sqrt(time_squared);
        const float pitch = std::atan2(-((time_squared * a + b) / time),
                                      -(horizontal / (component * time)));
        correction_x = (sine * g.gun_forward + cosine * g.gun_lateral +
            g.gravity.x * 0.5f * time_squared) - g.target.x;
        correction_z = ((cosine * g.gun_forward - sine * g.gun_lateral) +
            g.gravity.z * 0.5f * time_squared) - g.target.z;
        const float travel = std::cos(pitch) * speed * time;
        const Vector3 residual{
            travel * sine + correction_x,
            (time_squared * g.gravity.y * 0.5f - std::sin(pitch) * speed * time) - g.target.y,
            travel * cosine + correction_z
        };
        const AimSolution current{length(residual), yaw, pitch, time};
        if (current.error < 0.01f) return current;
        if (previous.error < current.error) return previous;
        previous = current;
    }
    return previous;
}

struct AimDifferentials {
    Vector3 residual;
    std::array<Vector3, 3> first;
    std::array<std::array<Vector3, 3>, 3> second;
};

AimDifferentials aim_differentials(const GunAimGeometry& g, float speed,
                                    const AimSolution& value) {
    const float sy = std::sin(value.yaw), cy = std::cos(value.yaw);
    const float sp = std::sin(value.pitch), cp = std::cos(value.pitch);
    const float time = value.time, time_squared = time * time;
    const float vx = sy * speed * cp, vy = -sp * speed, vz = cy * speed * cp;
    const float offset_x = cy * g.gun_lateral + sy * g.gun_forward;
    const float offset_z = cy * g.gun_forward - sy * g.gun_lateral;
    AimDifferentials d{};
    d.residual = {
        (vx * time + offset_x + g.gravity.x * 0.5f * time_squared) - g.target.x,
        (g.gravity.y * 0.5f * time_squared + vy * time) - g.target.y,
        (vz * time + offset_z + g.gravity.z * 0.5f * time_squared) - g.target.z
    };
    d.first[0] = {vz * time + offset_z, 0.0f, -vx * time - offset_x};
    d.first[1] = {-sy * sp * speed * time, -cp * speed * time, -cy * sp * speed * time};
    d.first[2] = {g.gravity.x * time + vx, g.gravity.y * time + vy, g.gravity.z * time + vz};
    d.second[0][0] = {-vx * time - offset_x, 0.0f, -vz * time - offset_z};
    d.second[1][1] = {-vx * time, -vy * time, -vz * time};
    d.second[2][2] = g.gravity;
    d.second[0][1] = d.second[1][0] = {-cy * sp * speed * time, 0.0f, sy * sp * speed * time};
    d.second[0][2] = d.second[2][0] = {vz, 0.0f, -vx};
    d.second[1][2] = d.second[2][1] = {-sy * sp * speed, -cp * speed, -cy * sp * speed};
    return d;
}

// 0x00f47030 solves the three trajectory residuals with their Jacobian;
// 0x00f46570 minimizes the squared residual with its full Hessian. Both retain
// the previous solution if the iteration increases the spatial error.
AimSolution refine_aim(const GunAimGeometry& geometry, float speed,
    AimSolution previous, int iterations, bool minimize) {
    for (int iteration = 0; iteration < iterations; ++iteration) {
        const auto d = aim_differentials(geometry, speed, previous);
        Matrix3 matrix{};
        Vector3 residual = d.residual;
        if (minimize) {
            residual = {2.0f * dot(d.residual, d.first[0]),
                        2.0f * dot(d.residual, d.first[1]),
                        2.0f * dot(d.residual, d.first[2])};
            for (std::size_t row = 0; row < 3; ++row)
                for (std::size_t column = 0; column < 3; ++column)
                    matrix[row][column] = 2.0f *
                        (dot(d.first[row], d.first[column]) +
                         dot(d.residual, d.second[row][column]));
        } else {
            for (std::size_t row = 0; row < 3; ++row)
                matrix[row] = {d.first[row].x, d.first[row].y, d.first[row].z};
        }
        Matrix3 inverse{};
        if (!inverse3(matrix, inverse)) return previous;
        const auto change = transform_normal(residual, inverse);
        AimSolution current{0.0f, previous.yaw - change.x,
            previous.pitch - change.y, previous.time - change.z};
        current.error = length(aim_differentials(geometry, speed, current).residual);
        if (current.error < 0.01f) return current;
        if (previous.error < current.error) return previous;
        previous = current;
    }
    return previous;
}

float sample_pitch_curve(float yaw, const GunPitchCurve& curve) {
    if (curve.size() < 2)
        throw std::invalid_argument("gun pitch curve needs at least two nodes");
    for (const auto& point : curve)
        if (!std::isfinite(point[0]) || !std::isfinite(point[1]))
            throw std::invalid_argument("gun pitch curve nodes must be finite");
    std::size_t low = 0, high = curve.size() - 1;
    while (high - low > 1) {
        const auto middle = (low + high) / 2;
        if (yaw > curve[middle][0]) low = middle;
        else high = middle;
    }
    const float span = curve[high][0] - curve[low][0];
    if (span == 0.0f)
        throw std::invalid_argument("gun pitch curve has a duplicate yaw segment");
    const float fraction = (yaw - curve[low][0]) / span;
    return (1.0f - fraction) * curve[low][1] + curve[high][1] * fraction;
}
} // namespace

std::array<float, 2> gun_pitch_limits(float yaw, const GunPitchCurve& minimum,
                                    const GunPitchCurve& maximum) {
    if (!std::isfinite(yaw))
        throw std::invalid_argument("turret yaw must be finite");
    if (yaw < 0.0f) yaw = yaw + two_pi;
    // Independent binary searches are required: the two curves need not share
    // node counts or yaw positions. The descriptor's absolute pair is unused.
    return {sample_pitch_curve(yaw, minimum), sample_pitch_curve(yaw, maximum)};
}

std::array<float, 2> gun_shot_angles(const GunAimGeometry& geometry, float speed,
    float gravity, float current_yaw, float current_pitch, bool adjust) {
    if (!finite(geometry.target) || !finite(geometry.gravity) ||
        !std::isfinite(geometry.gun_lateral) || !std::isfinite(geometry.gun_forward) ||
        !std::isfinite(geometry.world_height_delta) ||
        !std::isfinite(geometry.world_horizontal_distance) ||
        !std::isfinite(speed) || speed <= 0.0f || !std::isfinite(gravity) ||
        !std::isfinite(current_yaw) || !std::isfinite(current_pitch))
        throw std::invalid_argument("shot angle geometry and positive speed must be finite");
    const float radius = std::sqrt(geometry.gun_lateral * geometry.gun_lateral +
                                   geometry.gun_forward * geometry.gun_forward);
    const float minimum_distance = std::max(0.5f, radius);
    if (!(minimum_distance * minimum_distance < dot(geometry.target, geometry.target)))
        return {current_yaw, current_pitch};
    if (!adjust) {
        const auto result = initial_aim(geometry, speed, 2);
        return {result.yaw, result.pitch};
    }
    int minimize_iterations = 2;
    const float height = geometry.world_height_delta;
    const float vertical = height * gravity;
    const float reach_discriminant = speed * speed + speed * speed + vertical + vertical;
    if (reach_discriminant > 0.0f) {
        const float maximum_time = std::sqrt(reach_discriminant) * (1.0f / gravity);
        const float vertical_share =
            (maximum_time * gravity * maximum_time * 0.5f - height) / (maximum_time * speed);
        const float horizontal_share = 1.0f - vertical_share * vertical_share;
        if (horizontal_share > 0.0f) {
            const float difference = (geometry.world_horizontal_distance -
                std::sqrt(horizontal_share) * speed * maximum_time) - radius;
            if (difference < 0.0f) {
                const auto initial = initial_aim(geometry, speed, 2);
                if (initial.error < 0.01f) return {initial.yaw, initial.pitch};
                const auto solved = refine_aim(geometry, speed, initial, 5, false);
                if (solved.error < 0.01f) return {solved.yaw, solved.pitch};
                // The original repeats the initial estimate before choosing
                // the better seed for the final three Hessian iterations.
                const auto repeated = initial_aim(geometry, speed, 2);
                const auto seed = solved.error < repeated.error ? solved : repeated;
                const auto result = refine_aim(geometry, speed, seed, 3, true);
                return {result.yaw, result.pitch};
            }
            if (difference < 40.0f) minimize_iterations = 3;
        }
    }
    const auto initial = initial_aim(geometry, speed, 2);
    const auto result = refine_aim(geometry, speed, initial, minimize_iterations, true);
    return {result.yaw, result.pitch};
}

std::vector<Vector3> gun_projectile_trajectory(Vector3 begin, Vector3 velocity,
    Vector3 gravity, float time, float epsilon) {
    if (!finite(begin) || !finite(velocity) || !finite(gravity) ||
        !std::isfinite(time) || !std::isfinite(epsilon) || epsilon <= 0.0f)
        throw std::invalid_argument("trajectory time must be finite and epsilon positive");
    struct Interval { Vector3 velocity, begin, end; };
    const Vector3 end_velocity{velocity.x + time * gravity.x,
        velocity.y + gravity.y * time, velocity.z + gravity.z * time};
    const Vector3 end{
        begin.x + time * (velocity.x + end_velocity.x) * 0.5f,
        begin.y + time * (velocity.y + end_velocity.y) * 0.5f,
        begin.z + time * (velocity.z + end_velocity.z) * 0.5f
    };
    std::vector<Interval> stack{{velocity, begin, end}};
    std::vector<Vector3> points;
    while (!stack.empty()) {
        const auto interval = stack.back();
        stack.pop_back();
        const Vector3 delta{interval.end.x - interval.begin.x,
            interval.end.y - interval.begin.y, interval.end.z - interval.begin.z};
        // 0x008a9290 builds cross((-delta.z,0,delta.x), delta), with
        // this exact horizontal-square early acceptance before normalisation.
        Vector3 normal{-delta.x * delta.y,
            delta.x * delta.x + delta.z * delta.z, -delta.y * delta.z};
        if (std::fabs(normal.y) < epsilon) {
            points.push_back(interval.end);
            continue;
        }
        const float inverse_length = 1.0f / length(normal);
        normal = {normal.x * inverse_length, normal.y * inverse_length,
                  normal.z * inverse_length};
        const float denominator = gravity.y * normal.y;
        if (denominator == 0.0f)
            throw std::domain_error("trajectory chord has no vertical-gravity extremum");
        const float extreme_time = -dot(interval.velocity, normal) / denominator;
        const float squared_half_time = extreme_time * extreme_time * 0.5f;
        const Vector3 extreme{
            gravity.x * squared_half_time + interval.velocity.x * extreme_time,
            gravity.y * squared_half_time + interval.velocity.y * extreme_time,
            gravity.z * squared_half_time + interval.velocity.z * extreme_time
        };
        const float deviation = std::fabs(dot(extreme, normal));
        if (!std::isfinite(deviation))
            throw std::domain_error("trajectory chord extremum is not finite");
        if (deviation <= epsilon) {
            points.push_back(interval.end);
            continue;
        }
        const Vector3 split{interval.begin.x + extreme.x,
            interval.begin.y + extreme.y, interval.begin.z + extreme.z};
        if ((split.x == interval.begin.x && split.y == interval.begin.y && split.z == interval.begin.z) ||
            (split.x == interval.end.x && split.y == interval.end.y && split.z == interval.end.z))
            throw std::domain_error("trajectory epsilon is below representable chord progress");
        const Vector3 split_velocity{interval.velocity.x + gravity.x * extreme_time,
            interval.velocity.y + gravity.y * extreme_time,
            interval.velocity.z + gravity.z * extreme_time};
        // LIFO ordering matches the original vector stack: right, then left.
        stack.push_back({split_velocity, split, interval.end});
        stack.push_back({interval.velocity, interval.begin, split});
    }
    return points;
}

} // namespace wot
