#include "wot/vehicle_dynamics.hpp"

#include <cmath>
#include <iostream>
#include <limits>

namespace {

bool close(float actual, float expected, float tolerance = 1.0e-5f) {
    return std::fabs(actual - expected) <= tolerance;
}

bool close(wot::Vector3 actual, wot::Vector3 expected) {
    return close(actual.x, expected.x) && close(actual.y, expected.y) &&
           close(actual.z, expected.z);
}

wot::VehicleRigidBody body() {
    wot::VehicleRigidBody result;
    result.inverse_mass = 0.5f;
    result.transform = wot::vehicle_identity_matrix();
    result.inverse_transform = result.transform;
    result.inverse_body_inertia = result.transform;
    result.inverse_world_inertia = result.transform;
    result.orthonormalization_countdown = 200;
    return result;
}

bool forces_and_translation() {
    auto b = body();
    b.linear_velocity = {1, -2, 3};
    b.loads.force = {8, -4, 2};
    b.loads.linear_acceleration = {2, 4, -6};
    b.transform.rows[3][0] = 10;
    if (!wot::step_vehicle_rigid_body(b, 0.25f) ||
        !close(b.linear_velocity, {2.5f, -1.5f, 1.75f}) ||
        !close(b.transform.rows[3][0], 10.625f) ||
        !close(b.transform.rows[3][1], -0.375f) ||
        !close(b.transform.rows[3][2], 0.4375f) ||
        !close(b.inverse_transform.rows[3][0], -10.625f) ||
        !close(b.loads.force, {8, -4, 2}) || b.last_step != 0.25f)
        return false;
    wot::clear_vehicle_loads(b);
    return close(b.loads.force, {}) && close(b.loads.linear_acceleration, {});
}

bool torque_and_transient_motion() {
    auto b = body();
    b.inverse_world_inertia.rows[0][0] = 2;
    b.inverse_world_inertia.rows[1][1] = 3;
    b.inverse_world_inertia.rows[2][2] = 4;
    b.loads.torque = {2, -4, 1};
    b.loads.angular_acceleration = {2, 4, -6};
    b.angular_velocity = {1, 2, 3};
    b.linear_motion_acceleration = {4, -8, 12};
    b.angular_motion_acceleration = {2, -4, 6};
    if (!wot::step_vehicle_rigid_body(b, 0.25f) ||
        !close(b.angular_velocity, {2.5f, 0, 2.5f}) ||
        !close(b.motion_angular_velocity, {3, -1, 4}) ||
        !close(b.linear_velocity, {}) ||
        !close(b.motion_linear_velocity, {1, -2, 3}) ||
        !close(b.transform.rows[3][0], 0.25f) ||
        !close(b.transform.rows[3][1], -0.5f) ||
        !close(b.transform.rows[3][2], 0.75f) ||
        !close(b.linear_motion_acceleration, {}) ||
        !close(b.angular_motion_acceleration, {})) return false;

    // A pose-only correction is consumed; it must not become lasting velocity.
    wot::clear_vehicle_loads(b);
    return wot::step_vehicle_rigid_body(b, 0.25f) &&
           close(b.motion_linear_velocity, {}) &&
           close(b.transform.rows[3][0], 0.25f);
}

bool angular_pose_and_inertia() {
    auto b = body();
    b.inverse_body_inertia.rows[0][0] = 2;
    b.inverse_body_inertia.rows[1][1] = 3;
    b.inverse_body_inertia.rows[2][2] = 4;
    b.angular_velocity = {0, 1, 0};
    if (!wot::step_vehicle_rigid_body(b, 1.5707963267948966f)) return false;
    // Positive world yaw rotates the local forward row toward positive X.
    if (!close(b.transform.rows[2][0], 1) ||
        !close(b.transform.rows[2][2], 0) ||
        !close(b.transform.rows[0][2], -1) ||
        !close(b.inverse_world_inertia.rows[0][0], 4) ||
        !close(b.inverse_world_inertia.rows[1][1], 3) ||
        !close(b.inverse_world_inertia.rows[2][2], 2)) return false;

    auto tiny = body();
    tiny.angular_velocity = {0, 1.0e-6f, 0};
    return wot::step_vehicle_rigid_body(tiny, 1) &&
           tiny.transform.rows[2][0] == 0 &&
           tiny.transform.rows[2][2] == 1;
}

bool impulses() {
    auto b = body();
    b.transform.rows[3][0] = 10;
    b.transform.rows[3][1] = 20;
    b.transform.rows[3][2] = 30;
    b.inverse_world_inertia.rows[2][2] = 3;
    wot::apply_vehicle_impulse(b, {10, 22, 30}, {4, 0, 0});
    if (!close(b.linear_velocity, {2, 0, 0}) ||
        !close(b.angular_velocity, {0, 0, -24})) return false;
    wot::apply_vehicle_impulse_to_com(b, {-4, 6, 8});
    if (!close(b.linear_velocity, {0, 3, 4}) ||
        !close(b.angular_velocity, {0, 0, -24})) return false;

    // Off-diagonal inverse inertia: the torque need not share delta-omega's axis.
    b = body();
    b.inverse_world_inertia.rows[2][0] = 2;
    b.inverse_world_inertia.rows[2][1] = -3;
    wot::apply_vehicle_impulse(b, {0, 1, 0}, {1, 0, 0});
    return close(b.angular_velocity, {-2, 3, -1});
}

bool invalid_speed_and_history() {
    for (int mode = 0; mode != 3; ++mode) {
        auto b = body();
        b.record_history = true;
        b.linear_velocity = {1, 2, 3};
        b.loads.force = {2, 4, 6};
        b.transform.rows[3][0] = 12;
        b.linear_motion_acceleration = {1, 2, 3};
        if (mode == 0) {
            b.linear_velocity = {100000, 0, 0};
            b.loads.force = {};
            b.linear_motion_acceleration = {};
        } else if (mode == 1) {
            b.angular_velocity = {0, 10000, 0};
        } else {
            b.loads.torque.x = std::numeric_limits<float>::quiet_NaN();
        }
        if (wot::step_vehicle_rigid_body(b, 1) ||
            !close(b.linear_velocity, {}) || !close(b.angular_velocity, {}) ||
            !close(b.loads.force, {}) || !close(b.loads.torque, {}) ||
            !close(b.linear_motion_acceleration, {}) ||
            b.transform.rows[3][0] != 12 ||
            b.orthonormalization_countdown != 200 || b.history_next != 2 ||
            !close(b.history[1].linear_velocity, {})) return false;
        if (mode == 0 && b.motion_linear_velocity.x != 100000) return false;
        if (mode == 2 && !std::isnan(b.motion_angular_velocity.x)) return false;
    }
    auto b = body();
    b.linear_velocity = {99999, 0, 0};
    return wot::step_vehicle_rigid_body(b, 1);
}

bool orthonormalization_cadence() {
    auto b = body();
    b.orthonormalization_countdown = 0;
    b.transform.rows[2][2] = 2;
    b.transform.rows[1][1] = 3;
    b.record_history = true;
    if (!wot::step_vehicle_rigid_body(b, 0) ||
        !close(b.transform.rows[2][2], 1) ||
        !close(b.transform.rows[0][0], 1) ||
        !close(b.transform.rows[1][1], 1) ||
        b.orthonormalization_countdown != 200 || b.history_next != 1 ||
        b.history[0].transform.rows[2][2] != 2) return false;
    b.transform.rows[2][2] = 2;
    for (int i = 0; i != 200; ++i) {
        if (!wot::step_vehicle_rigid_body(b, 0) ||
            b.transform.rows[2][2] != 2) return false;
    }
    return b.orthonormalization_countdown == 0 &&
           wot::step_vehicle_rigid_body(b, 0) &&
           b.transform.rows[2][2] == 1 && b.history_next == 1;
}

bool pose_install_and_velocity_samples() {
    auto b = body();
    b.loads.force = {3, 4, 5};
    b.last_step = 2;
    b.inverse_body_inertia.rows[0][0] = 2;
    b.inverse_body_inertia.rows[1][1] = 3;
    b.inverse_body_inertia.rows[2][2] = 4;
    wot::set_vehicle_rigid_body_velocity(b, {1, 2, 3}, {4, 5, 6});
    auto pose = wot::vehicle_identity_matrix();
    pose.rows[0][0] = 0;
    pose.rows[0][2] = -1;
    pose.rows[2][0] = 1;
    pose.rows[2][2] = 0;
    pose.rows[3][0] = 7;
    pose.rows[3][1] = 8;
    pose.rows[3][2] = 9;
    wot::install_vehicle_rigid_body_transform(b, pose);
    if (b.last_step != 0 || b.orthonormalization_countdown != 200 ||
        b.solver_id != -1 || !close(b.loads.force, {3, 4, 5}) ||
        !close(b.linear_velocity, {1, 2, 3}) ||
        !close(b.angular_velocity, {4, 5, 6}) ||
        !close(b.inverse_transform.rows[3][0], 9) ||
        !close(b.inverse_transform.rows[3][1], -8) ||
        !close(b.inverse_transform.rows[3][2], -7) ||
        !close(b.inverse_world_inertia.rows[0][0], 4) ||
        !close(b.inverse_world_inertia.rows[1][1], 3) ||
        !close(b.inverse_world_inertia.rows[2][2], 2)) return false;
    for (std::size_t i = 0; i != 3; ++i) {
        if (!close(b.interpolation.transforms[i].rows[3][2], 9) ||
            !close(b.interpolation.linear_velocities[i], {1, 2, 3}) ||
            !close(b.interpolation.angular_velocities[i], {4, 5, 6})) return false;
    }
    return true;
}

} // namespace

int main() {
    if (!forces_and_translation() || !torque_and_transient_motion() ||
        !angular_pose_and_inertia() || !impulses() ||
        !invalid_speed_and_history() || !orthonormalization_cadence() ||
        !pose_install_and_velocity_samples()) {
        std::cerr << "vehicle dynamics regressions failed\n";
        return 1;
    }
    std::cout << "vehicle dynamics regressions passed\n";
    return 0;
}
