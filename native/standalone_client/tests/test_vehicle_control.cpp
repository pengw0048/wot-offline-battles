#include "wot/vehicle_control.hpp"

#include <cmath>
#include <iostream>

namespace control = wot::offline_vehicle_control;

namespace {

bool close(double actual, double expected) {
    return std::fabs(actual - expected) <= 1.0e-11 * (1 + std::fabs(expected));
}

control::Parameters parameters() {
    // Existing vehicle_physics._DEFAULTS; vehicle descriptors remain external.
    return {5730, 45.0 * 735.49875, 32.0 / 3.6, 12.0 / 3.6,
            0.6632251157578453, {1.1, 1.4, 2.6}, 0.6867,
            1.3 * (9.81 * 1.25), 1.5, 1.0};
}

bool longitudinal_regressions() {
    struct Case {
        double speed;
        double throttle;
        bool steering;
        double pitch;
        double elapsed;
        bool airborne;
        std::size_t terrain;
        bool handbrake;
        double expected;
    };
    // Captured from the existing Python law: powered/reverse starts, reversing,
    // parked hold/slide, coast, failed climb, uphill slip, overspeed and flight.
    const Case cases[] = {
        {0, 1, false, 0, 1.0 / 60, false, 0, false, 0.08022455824607329},
        {0, -1, false, 0, 1.0 / 60, false, 0, false, -0.08022455824607329},
        {10, -1, false, 0, 0.1, false, 0, false, 8.239251647578534},
        {0, 0, false, 0.2, 0.1, false, 0, false, 0},
        {0, 0, false, 0.7, 0.1, false, 0, false, 0.32102807289641666},
        {10, 0, false, 0.12, 0.1, false, 0, false, 9.333333333333334},
        {10, 0, false, 0.5, 0.1, false, 0, false, 9.333333333333334},
        {0.1, 1, true, -0.6, 0.1, false, 1, false, -1.240099342003009},
        {8, 0, false, -0.65, 0.1, false, 0, false, 3.418990911256298},
        {9.2, 1, false, 0.5, 0.1, false, 0, false, 9.209588510772083},
        {9.2, 0, false, 0, 0.1, false, 0, false, 8.0693975},
        {0.04, 1, false, 1, 0.1, false, 0, true, 0.6778959605632938},
        {1, 1, false, 0, 0.2, false, 0, true, 0},
        {20, 1, true, -0.7, 0.2, true, 2, true, 20},
    };
    const auto p = parameters();
    for (std::size_t index = 0; index != sizeof(cases) / sizeof(cases[0]); ++index) {
        const auto& c = cases[index];
        if (!close(control::longitudinal_step(p, c.speed, c.throttle,
            c.steering, c.pitch, c.elapsed, c.airborne, c.terrain, c.handbrake),
            c.expected)) {
            std::cerr << "longitudinal case " << index << " failed\n";
            return false;
        }
    }
    return true;
}

bool forces_and_grip() {
    const auto p = parameters();
    if (!close(control::engine_force(p, 5, 1, -0.2), 8274.3609375) ||
        !close(control::rolling_resist_force(p, 2, true), 20460.9132) ||
        !close(control::brake_force(p, true, 1, 0.4), 84091.59689989014) ||
        !close(control::brake_force(p, false, 1, 0.4), 61545.422234928585) ||
        control::engine_force(p, 0, 0) != 0) return false;
    const double radians = 3.14159265358979323846 / 180;
    if (!close(control::longitudinal_slope_grip(27.5 * radians), 1) ||
        !close(control::longitudinal_slope_grip(32 * radians), 0.1) ||
        !close(control::lateral_slope_grip(-1), 0.1) ||
        !close(control::lateral_slope_grip(2), 1) ||
        !close(control::slope_cohesion(1), 1.3)) return false;

    auto modified = p;
    modified.native_power_ratio = 0.5;
    if (!close(control::engine_force(modified, 5, 1),
                control::engine_force(p, 5, 1) * 0.5)) return false;
    control::Tuning tuning;
    tuning.gravity_factor = 0.5;
    // Existing apply_tuning changes gravity without silently changing POWER_FACTOR.
    return close(control::engine_force(p, 5, 1, 0, tuning),
                  control::engine_force(p, 5, 1)) &&
           close(control::rolling_resist_force(p, 0, false, tuning),
                  control::rolling_resist_force(p) * (0.5 / 1.25));
}

bool traverse_and_track_scroll() {
    const auto p = parameters();
    if (!close(control::traverse_step(p, 0, 1, 0, 0.01, 0, 1), 0.13264502315156906) ||
        !close(control::traverse_step(p, 0.5, 0, 8, 0.01), 0.36735497684843094) ||
        !close(control::traverse_step(p, 0, 1, 8, 0.1, 2, -1), -0.2805952412821653) ||
        !close(control::traverse_step(p, 0, 1, -8, 0.1, 0, 1), 0.6632251157578453))
        return false;
    const auto tracks = control::track_scroll(p, 3, 1);
    const auto capped = control::track_scroll(p, 100, -100);
    const double cap = p.speed_forward * 0.995;
    return tracks.left == 1.5 && tracks.right == 4.5 &&
           capped.left == cap && capped.right == -cap &&
           cap < p.speed_forward &&
           control::traverse_step(p, 0.005, 0, 0, 0.01) == 0;
}

} // namespace

int main() {
    if (!longitudinal_regressions() || !forces_and_grip() ||
        !traverse_and_track_scroll()) {
        std::cerr << "offline vehicle control regressions failed\n";
        return 1;
    }
    std::cout << "offline vehicle control regressions passed\n";
    return 0;
}
