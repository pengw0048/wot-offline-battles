// A C ABI for the independent x86 oracle audit, not a production adapter.
#include "wot/vehicle_constraint_solver.hpp"

using namespace wot;

extern "C" void fixture(int mid, int iterations, int row_count,
    const int* groups, VehicleSolverRow* rows, int body_count,
    float* scratch, float* loads, int source_count, float* source_data,
    float* aggregate_out, float* mid_out) {
    VehicleConstraintContext context;
    context.nonempty = true;
    context.mid_iterations = mid;
    context.iterations = iterations;
    std::vector<VehicleRigidBody> bodies(body_count);
    for (int i = 0; i < body_count; ++i) {
        auto* input = loads + i * 12;
        auto& body = bodies[i];
        body.loads.linear_acceleration = {input[0], input[1], input[2]};
        body.loads.angular_acceleration = {input[3], input[4], input[5]};
        body.linear_motion_acceleration = {input[6], input[7], input[8]};
        body.angular_motion_acceleration = {input[9], input[10], input[11]};
        VehicleSolverBody entry;
        entry.body = &body;
        for (int j = 0; j < 16; ++j) entry.delta[j] = scratch[i * 16 + j];
        context.bodies.push_back(entry);
    }
    VehicleSolverImpulseGroup sums;
    for (int i = 0; i < source_count; ++i) {
        VehicleSolverSource entry;
        const auto* input = source_data + i * 4;
        entry.lambda = input[0];
        entry.friction_reference = static_cast<std::int32_t>(input[1]);
        entry.negative_friction = input[2];
        entry.positive_friction = input[3];
        context.sources.push_back(entry);
        sums.source_indices.push_back(i);
    }
    for (int i = 0; i < row_count; ++i)
        context.groups[groups[i]].push_back(rows[i]);
    sums.active_count = source_count;
    context.impulse_groups.push_back(sums);
    context.active_impulse_groups = 1;
    context.mid_solve_callbacks.push_back([&] {
        mid_out[0] = bodies[0].loads.linear_acceleration.x;
        mid_out[1] = context.impulse_groups[0].absolute_impulse;
    });
    solve_vehicle_constraints(context);
    *aggregate_out = context.impulse_groups[0].absolute_impulse;
    for (int i = 0; i < body_count; ++i) {
        const auto& body = bodies[i];
        const float result[] = {
            body.loads.linear_acceleration.x, body.loads.linear_acceleration.y,
            body.loads.linear_acceleration.z, body.loads.angular_acceleration.x,
            body.loads.angular_acceleration.y, body.loads.angular_acceleration.z,
            body.linear_motion_acceleration.x, body.linear_motion_acceleration.y,
            body.linear_motion_acceleration.z, body.angular_motion_acceleration.x,
            body.angular_motion_acceleration.y, body.angular_motion_acceleration.z};
        for (int j = 0; j < 12; ++j) loads[i * 12 + j] = result[j];
        for (int j = 0; j < 16; ++j)
            scratch[i * 16 + j] = context.bodies[i].delta[j];
    }
    for (int i = 0; i < source_count; ++i)
        source_data[i * 4] = context.sources[i].lambda;
    int group_offsets[9] = {};
    for (int i = 0; i < row_count; ++i)
        rows[i] = context.groups[groups[i]][group_offsets[groups[i]]++];
}
