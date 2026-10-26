#include "wot/vehicle_constraint_solver.hpp"

#include <cassert>
#include <iostream>
#include <stdexcept>

int main() {
    using namespace wot;
    VehicleRigidBody body;
    body.solver_id=0;
    VehicleConstraintContext c;
    c.nonempty=true;
    c.mid_iterations=1;
    c.iterations=2;
    c.sources.resize(1);
    VehicleSolverBody scratch;
    scratch.body=&body;
    scratch.delta[0]=2;
    scratch.delta[8]=3;
    c.bodies.push_back(scratch);
    VehicleSolverRow row;
    row.coefficients[6]=0.5f;
    row.coefficients[24]=-1;
    row.coefficients[25]=1;
    row.coefficients[12]=1;
    c.groups[0].push_back(row);
    VehicleSolverImpulseGroup aggregate;
    aggregate.source_indices.push_back(0);
    aggregate.active_count=1;
    c.impulse_groups.push_back(aggregate);
    c.active_impulse_groups=1;
    int calls=0;
    c.mid_solve_callbacks.push_back([&] {
        ++calls;
        assert(body.loads.linear_acceleration.x==2.5f);
        assert(c.sources[0].lambda==0.5f);
        assert(c.impulse_groups[0].absolute_impulse==0.5f);
        body.loads.force.x=19;
        body.loads.linear_acceleration.x=101;
    });
    solve_vehicle_constraints(c);
    assert(calls==1);
    assert(body.loads.linear_acceleration.x==2.5f);
    assert(body.linear_motion_acceleration.x==3);
    assert(body.loads.force.x==19);
    assert(c.sources[0].lambda==0.5f);
    const auto capacity=c.groups[0].capacity();
    finish_vehicle_constraints(c);
    assert(body.solver_id==-1);
    assert(!c.nonempty && c.sources.empty() && c.bodies.empty());
    assert(c.active_impulse_groups==0);
    assert(c.groups[0].empty() && c.groups[0].capacity()==capacity);
    assert(c.impulse_groups[0].active_count==0);
    assert(c.impulse_groups[0].absolute_impulse==0);
    finish_vehicle_constraints(c);
    solve_vehicle_constraints(c);
    c.nonempty=true;
    c.iterations=1;
    bool rejected=false;
    try { solve_vehicle_constraints(c); }
    catch (const std::invalid_argument&) { rejected=true; }
    assert(rejected);
    VehicleRigidBody axle_body;
    axle_body.inverse_mass=1;
    for (int i=0;i<4;++i) axle_body.inverse_world_inertia.rows[i][i]=1;
    VehicleConstraintContext axle_context;
    axle_context.elapsed=0.1f;axle_context.inverse_elapsed=10;
    VehicleConstraintAxisDescription axle;
    axle.body_a=&axle_body;
    axle.axis={0,1,0};
    axle.tangents={{{1,0,0},{0,0,1}}};
    axle.lower=-1;axle.upper=1;
    axle.correction=0.5f;axle.dead_zone=0.02f;
    axle.error_mode=2;
    axle.tangent_warm_start={{0.1f,0.2f}};
    axle.limit_warm_start=0.3f;axle.limit_dead_zone=0.04f;
    for (float endpoint : {-2.0f,-1.0f,0.0f,1.0f,2.0f}) {
        axle.endpoint={0,endpoint,0};
        const auto rows=add_vehicle_axis_constraints(axle_context,axle);
        assert(rows[0]==0 && rows[1]==1);
        assert((rows[2]!=-1)==(endpoint < -1));
        assert((rows[3]!=-1)==(endpoint > 1));
        assert(axle_context.sources[0].lambda==0.1f);
        assert(axle_context.sources[1].lambda==0.2f);
        if (rows[2]!=-1 || rows[3]!=-1) {
            const auto& limit=axle_context.sources[2];
            assert(limit.lambda==0.3f);
            assert(limit.geometry[1]==(endpoint > 1 ? -1.0f : 1.0f));
            assert(limit.cache_group==1);
        }
        finish_vehicle_constraints(axle_context);
    }
    VehicleConstraintDescription contact;
    contact.body_a=&axle_body;
    contact.geometry[1]=1;
    contact.lower=0;contact.upper=10;
    const auto normal=add_vehicle_contact(axle_context,contact,0);
    const auto friction=add_vehicle_contact_friction(axle_context,normal,contact,0.7f);
    assert(friction[0]==1 && friction[1]==2);
    assert(axle_context.sources[1].friction_reference==0);
    assert(axle_context.sources[2].friction_reference==0);
    assert(axle_context.sources[1].cache_group==5);
    assert(axle_context.sources[2].cache_group==5);
    assert(axle_context.sources[1].negative_friction==0.7f);
    assert(axle_context.sources[2].positive_friction==0.7f);
    assert(axle_context.sources[1].geometry[0]==0);
    assert(axle_context.sources[2].geometry[0]==-1);
    finish_vehicle_constraints(axle_context);
    std::cout << "Vehicle constraint publication, producers and cleanup checks passed\n";
}
