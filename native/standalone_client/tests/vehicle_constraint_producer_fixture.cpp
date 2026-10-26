// Binary stream fixture for running the original SSE reciprocal on x86.
#include "wot/vehicle_constraint_solver.hpp"

#include <cstdio>

using namespace wot;

int main() {
    float input[115];
    while (std::fread(input, sizeof(input), 1, stdin) == 1) {
        VehicleConstraintContext context;
        context.elapsed = input[0];
        context.inverse_elapsed = 1.0f / input[0];
        VehicleRigidBody bodies[2];
        int cursor = 1;
        for (auto& body : bodies) {
            body.inverse_mass = input[cursor++];
            for (auto& row : body.inverse_world_inertia.rows)
                for (auto& value : row) value = input[cursor++];
            for (auto& row : body.transform.rows)
                for (auto& value : row) value = input[cursor++];
            body.linear_velocity = {input[cursor], input[cursor+1], input[cursor+2]};
            cursor += 3;
            body.angular_velocity = {input[cursor], input[cursor+1], input[cursor+2]};
            cursor += 3;
            body.loads.force = {input[cursor], input[cursor+1], input[cursor+2]};
            cursor += 3;
            body.loads.torque = {input[cursor], input[cursor+1], input[cursor+2]};
            cursor += 3;
        }
        VehicleConstraintDescription description;
        for (auto& value : description.geometry) value = input[cursor++];
        description.body_a = &bodies[0];
        description.body_b = input[cursor++] ? &bodies[1] : nullptr;
        description.lower = input[cursor++];
        description.upper = input[cursor++];
        description.error_mode = static_cast<unsigned>(input[cursor++]);
        description.correction = input[cursor++];
        description.dead_zone = input[cursor++];
        description.warm_start = input[cursor++];
        description.softness = input[cursor++];
        description.kind = static_cast<unsigned>(input[cursor++]);
        const float error = input[cursor++];
        const int friction = static_cast<int>(input[cursor++]);
        const bool contact = input[cursor++] != 0;
        if (friction != -1) context.sources.push_back(VehicleSolverSource{});
        const auto id = contact ? add_vehicle_contact(context, description, error, friction)
            : add_vehicle_constraint(context, description, error, friction);
        const auto& source = context.sources[id];
        float output[105] = {};
        int count = 0;
        output[count++] = static_cast<float>(source.cache_group);
        for (auto value : context.groups[source.cache_group][source.cache_index].coefficients)
            output[count++] = value;
        for (auto value : source.jacobian) output[count++] = value;
        for (const auto& body : context.bodies) {
            for (auto value : body.delta) output[count++] = value;
            for (auto value : body.acceleration_prediction) output[count++] = value;
        }
        std::fwrite(output, sizeof(output), 1, stdout);
    }
}
