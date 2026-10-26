#pragma once

#include "wot/vehicle_dynamics.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <vector>

namespace wot {

// The native cache is separate from the native 0x90 constraint record.
// Coefficients retain their producer's layout; converting them to a generic
// Jacobian would erase the one-body, paired and split-impulse contracts.
struct VehicleSolverRow {
    std::array<float, 36> coefficients{};
    std::uint32_t body_a = 0;
    std::uint32_t body_b = 0;
    std::uint32_t source_index = 0;
};
static_assert(sizeof(VehicleSolverRow) == 0x9c, "Native solver row layout");

struct VehicleSolverSource {
    float lambda = 0;                    // original record +0x4c
    std::int32_t friction_reference = -1; // +0x50
    float negative_friction = 0;         // +0x54
    float positive_friction = 0;         // +0x58
    std::array<float, 12> jacobian{};     // original +0x00..0x2c
    VehicleRigidBody* body_a = nullptr;
    VehicleRigidBody* body_b = nullptr;
    std::uint32_t cache_group = 0;
    std::uint32_t cache_index = 0;
    bool contact = false;                // original +0x88 == 1
    std::array<float, 12> geometry{};     // retained contact producer input
};

struct VehicleSolverBody {
    VehicleRigidBody* body = nullptr; // Non-owning; the vehicle owns its body.
    std::array<float, 16> delta{};     // velocity and split-impulse channels
    std::array<float, 6> acceleration_prediction{}; // native scratch +0x44
};

struct VehicleSolverImpulseGroup {
    std::vector<std::uint32_t> source_indices;
    // Native entries may refer to another context's source records.
    const std::vector<VehicleSolverSource>* sources = nullptr;
    std::size_t active_count = 0;
    float absolute_impulse = 0;
};

struct VehicleConstraintContext {
    // Native group offsets: 40,4c,58,64,70,7c,88,94,a0.
    std::array<std::vector<VehicleSolverRow>, 9> groups;
    std::vector<VehicleSolverBody> bodies;
    std::vector<VehicleSolverSource> sources;
    std::vector<VehicleSolverImpulseGroup> impulse_groups;
    // The original optional mid-solve virtual callbacks run after the first
    // load publication and before clearing those published acceleration loads.
    std::vector<std::function<void()>> mid_solve_callbacks;
    float elapsed = 0;
    float inverse_elapsed = 0;
    std::uint32_t mid_iterations = 0;
    std::uint32_t iterations = 2;
    std::size_t active_impulse_groups = 0;
    bool nonempty = false;
};

// The original 0x60 producer description. Geometry is either twelve direct
// Jacobian coefficients, or normal.xyz/pad, pointA.xyz/pad, pointB.xyz/pad.
struct VehicleConstraintDescription {
    std::array<float, 12> geometry{};
    VehicleRigidBody* body_a = nullptr;
    VehicleRigidBody* body_b = nullptr;
    float lower = 0;
    float upper = 0;
    std::uint32_t error_mode = 0; // native +0x44: 0, 1, 2 (split impulse)
    float correction = 0;       // +0x48
    float dead_zone = 0;        // +0x4c
    float warm_start = 0;       // +0x50
    float softness = 0;         // +0x54
    std::uint32_t kind = 0;     // +0x5c: 2 selects unbounded paired cache
};

// f723a0/f71af0 accept direct Jacobians; f72770/f71ed0 derive them from
// contact normals/points. Error and friction reference are explicit original
// call arguments, not inferred from the description's geometry.
std::uint32_t add_vehicle_constraint(VehicleConstraintContext& context,
    const VehicleConstraintDescription& description, float error,
    std::int32_t friction_reference = -1);
std::uint32_t add_vehicle_contact(VehicleConstraintContext& context,
    const VehicleConstraintDescription& description, float error,
    std::int32_t friction_reference = -1);

// f73970/f73a40 derive both tangent constraints from an actual contact.
// The caller supplies the original normal constraint index and coefficient.
std::array<std::uint32_t, 2> add_vehicle_contact_friction(
    VehicleConstraintContext& context, std::uint32_t normal_constraint,
    const VehicleConstraintDescription& contact, float coefficient);

// f71360: two transverse rows plus whichever authored axial limit is active.
struct VehicleConstraintAxisDescription {
    VehicleRigidBody* body_a = nullptr;
    VehicleRigidBody* body_b = nullptr;
    Vector3 origin{};
    Vector3 endpoint{};
    Vector3 axis{};
    std::array<Vector3, 2> tangents{};
    float lower = 0;
    float upper = 0;
    std::uint32_t error_mode = 0;
    float correction = 0;
    float dead_zone = 0;
    std::array<float, 2> tangent_warm_start{};
    float limit_dead_zone = 0;
    float limit_warm_start = 0;
};
std::array<std::int32_t, 4> add_vehicle_axis_constraints(
    VehicleConstraintContext& context, const VehicleConstraintAxisDescription& description);

// #1513 0x00f73c20, including mid-solve publication, friction bounds,
// per-source lambda and rigid-body acceleration writeback. Prepared cache
// coefficients must come from the original constraint producer.
void solve_vehicle_constraints(VehicleConstraintContext& context);

// #1513 0x00f718e0. Retains allocation capacity; removes active solve rows.
void finish_vehicle_constraints(VehicleConstraintContext& context);

} // namespace wot
