#pragma once

#include "wot/point_velocity.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace wot {

// Row-vector matrices, with translation in row 3, as in the #1513 client.
struct VehicleMatrix {
    float rows[4][4];
};

VehicleMatrix vehicle_identity_matrix();

struct VehicleLoads {
    Vector3 force{};
    Vector3 torque{};
    Vector3 linear_acceleration{};
    Vector3 angular_acceleration{};
};

struct VehicleMotionSnapshot {
    VehicleMatrix transform{};
    Vector3 linear_velocity{};
    Vector3 angular_velocity{};
};

// Extended rigid-body samples at native +0x1e0..0x2e8. These are distinct
// from the optional recorder used by the base-body integration routine.
struct VehicleBodyInterpolation {
    std::array<VehicleMatrix, 3> transforms{};
    std::array<Vector3, 3> angular_velocities{};
    std::array<Vector3, 3> linear_velocities{};
};

struct VehicleRigidBody {
    // Descriptor/configuration owners must supply the actual mass, inertia,
    // transform and velocities. This kernel supplies no vehicle tuning.
    float inverse_mass = 0;
    VehicleMatrix inverse_body_inertia{};
    VehicleMatrix inverse_world_inertia{};
    VehicleMatrix transform{};
    VehicleMatrix inverse_transform{};
    Vector3 linear_velocity{};
    Vector3 angular_velocity{};
    VehicleLoads loads{};

    // Native +0xf8/+0x104 terms affect this step's pose, not retained velocity.
    Vector3 linear_motion_acceleration{};
    Vector3 angular_motion_acceleration{};
    Vector3 motion_linear_velocity{};
    Vector3 motion_angular_velocity{};
    float last_step = 0;
    std::uint32_t orthonormalization_countdown = 0;

    // #1513 body +0x1c4: the current solver-context body index. The solver
    // assigns it while assembling rows and resets it to -1 after each solve.
    std::int32_t solver_id = -1;

    VehicleBodyInterpolation interpolation;

    bool record_history = false;
    std::array<VehicleMotionSnapshot, 3> history{};
    std::size_t history_next = 0;
};

// Recovered WGVehiclePhysics.applyImpulse/ToCoM arithmetic. Point and impulse
// use the same world frame as transform and inverse_world_inertia.
void apply_vehicle_impulse_to_com(VehicleRigidBody& body, Vector3 impulse);
void apply_vehicle_impulse(VehicleRigidBody& body, Vector3 point,
                           Vector3 impulse);

// #1513 0x00f6ffa0 -> 0x00f70010: integrate forces and pose, then refresh
// inverse transform/inertia. Returns false on the native invalid-speed guard;
// the pose is retained and velocities/loads are cleared in that case.
// Loads persist on successful steps; the native caller clears them separately.
// Motion accelerations are consumed on both success and failure.
// Compile without fast-math/FMA contraction. D3DX transcendental/normalization
// last-bit behavior is not promised by this portable implementation.
bool step_vehicle_rigid_body(VehicleRigidBody& body, float elapsed);
void clear_vehicle_loads(VehicleRigidBody& body);

// #1513 0x00f80870: install the body pose, reset elapsed time, rebuild the
// inverse pose/inertia and reset the three pose samples. Velocities, loads,
// their interpolation samples and the normalization countdown are retained.
void install_vehicle_rigid_body_transform(VehicleRigidBody& body,
                                          const VehicleMatrix& transform);

// The per-body writes of #1513 0x00f85ba0 / 0x00f82f80. The owning vehicle
// separately computes each wheel's point velocity before calling this.
void set_vehicle_rigid_body_velocity(VehicleRigidBody& body, Vector3 linear,
                                     Vector3 angular);

} // namespace wot
