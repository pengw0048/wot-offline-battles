#include "wot/gun_ballistics.hpp"

#include <cmath>
#include <iostream>
#include <stdexcept>

namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
bool close(float a, float b, float tolerance = 1.0e-6f) {
    return std::fabs(a - b) <= tolerance;
}

void pitch_curves() {
    const wot::GunPitchCurve minimum{{0.f, -.3f}, {2.f, -.4f},
        {4.f, -.2f}, {6.2831855f, -.3f}};
    const wot::GunPitchCurve maximum{{0.f, .1f}, {3.f, .2f}, {6.2831855f, .1f}};
    const auto middle = wot::gun_pitch_limits(1.f, minimum, maximum);
    check(close(middle[0], -.35f) && close(middle[1], .13333334f),
        "minimum and maximum curves must use independent node grids");
    const auto negative = wot::gun_pitch_limits(-1.f, minimum, maximum);
    const auto positive = wot::gun_pitch_limits(6.2831855f - 1.f, minimum, maximum);
    check(negative == positive, "negative yaw must receive one positive revolution");
    const auto endpoint = wot::gun_pitch_limits(6.2831855f, minimum, maximum);
    check(endpoint[0] == -.3f && endpoint[1] == .1f, "last authored endpoint must remain inclusive");
    bool failed = false;
    try { wot::gun_pitch_limits(0.f, {{0.f, 1.f}}, maximum); }
    catch (const std::invalid_argument&) { failed = true; }
    check(failed, "missing curve geometry must fail instead of fabricating limits");
}

void reachable_aims() {
    for (float x : {0.f, 10.f, 100.f, -100.f}) {
        for (float y : {-20.f, 0.f, 20.f}) {
            const wot::GunAimGeometry geometry{{x, y, 300.f}, {0.f, -9.81f, 0.f},
                .2f, 1.5f, -y, std::sqrt(x * x + 90000.f)};
            const auto angles = wot::gun_shot_angles(geometry, 500.f, 9.81f, .1f, .2f);
            const float sine = std::sin(angles[0]), cosine = std::cos(angles[0]);
            const float time = (300.f - (cosine * 1.5f - sine * .2f)) /
                (cosine * std::cos(angles[1]) * 500.f);
            const float error_x = sine * std::cos(angles[1]) * 500.f * time +
                cosine * .2f + sine * 1.5f - x;
            const float error_y = -std::sin(angles[1]) * 500.f * time -
                9.81f * .5f * time * time - y;
            check(std::sqrt(error_x * error_x + error_y * error_y) < .01f,
                "reachable native aim must satisfy its original centimetre residual gate");
        }
    }
    wot::GunAimGeometry near{{0.f, 0.f, .25f}, {0.f, -9.81f, 0.f},
        0.f, 1.f, 0.f, .25f};
    check(wot::gun_shot_angles(near, 500.f, 9.81f, .4f, -.2f) ==
        std::array<float, 2>{.4f, -.2f}, "near-target sentinel must preserve current angles");
    const wot::GunAimGeometry far{{0.f, 0.f, 5000.f}, {0.f, -9.81f, 0.f},
        0.f, 1.f, 0.f, 5000.f};
    const auto unreachable = wot::gun_shot_angles(far, 100.f, 9.81f, 0.f, 0.f);
    check(std::isfinite(unreachable[0]) && std::isfinite(unreachable[1]),
        "unreachable target must retain the recovered finite minimization branch");
}

void trajectory_chords() {
    const auto points = wot::gun_projectile_trajectory({0.f, 0.f, 0.f},
        {0.f, 0.f, 500.f}, {0.f, -9.81f, 0.f}, 1.f, .03f);
    check(points.size() == 8, "native adaptive trajectory subdivision changed");
    check(close(points.back().y, -4.905f) && points.back().z == 500.f,
        "native trapezoid endpoint changed");
    wot::Vector3 previous{0.f, 0.f, 0.f};
    for (auto point : points) {
        check(point.z > previous.z, "trajectory stack must emit increasing flight order");
        const float time = (point.z + previous.z) * .5f / 500.f;
        const float vertical_error = -4.905f * time * time - (point.y + previous.y) * .5f;
        const float dy = point.y - previous.y, dz = point.z - previous.z;
        const float normal_y = dz / std::sqrt(dy * dy + dz * dz);
        check(std::fabs(vertical_error * normal_y) <= .030001f,
            "accepted chord exceeds the actual perpendicular epsilon");
        previous = point;
    }
    const auto vertical = wot::gun_projectile_trajectory({1.f, 2.f, 3.f},
        {0.f, 100.f, 0.f}, {0.f, -9.81f, 0.f}, 1.f, .03f);
    check(vertical.size() == 1, "vertical chord must retain native horizontal-square early acceptance");
    const auto zero = wot::gun_projectile_trajectory({1.f, 2.f, 3.f},
        {0.f, 100.f, 0.f}, {0.f, -9.81f, 0.f}, 0.f, .03f);
    check(zero.size() == 1 && zero[0].x == 1.f && zero[0].y == 2.f && zero[0].z == 3.f,
        "zero interval must emit its endpoint once");
}
} // namespace

int main() {
    try {
        pitch_curves();
        reachable_aims();
        trajectory_chords();
        std::cout << "Recovered gun ballistics checks passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}
