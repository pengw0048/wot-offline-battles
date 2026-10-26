#include "wot/vehicle_suspension.hpp"

#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

using namespace wot::offline_vehicle_control;

int main() {
    auto contact=retained_ground_contact({1,2},3.0,{},0.5);
    assert(contact.height==3.0 && contact.memory);
    const auto first=retained_ground_contact({1,2},{},contact.memory,0.5);
    assert(first.height==3.0);
    const auto second=retained_ground_contact({1,2},{},first.memory,0.5);
    assert(!second.height && !second.memory);
    const auto sloped=retained_ground_contact({1.2,2.2},{},contact.memory,0.5,
        std::array<double,2>{{0.5,-0.25}});
    assert(std::fabs(*sloped.height-3.05)<1e-12);
    assert(!retained_ground_contact({1.6,2},{},contact.memory,0.5).height);
    contact=retained_ground_contact({0,0},4.0,{},0.5);
    for (auto point:{std::array<double,2>{{.1,0}},{{.1,.1}},{{0,.1}},{{0,0}},{{-.1,0}}}) {
        contact=retained_ground_contact(point,{},contact.memory,0.5);
        assert(contact.height==4.0);
    }
    assert(!retained_ground_contact({-.1,-.1},{},contact.memory,0.5).height);
    assert(suspension_support_allowed(1,1,1));
    assert(!suspension_support_allowed(1.2,1,1));
    assert(suspension_support_allowed(1.2,.9,1));
    assert(!suspension_support_allowed(1,.5,1));
    assert(!suspension_support_allowed(std::numeric_limits<double>::infinity(),1));
    assert(landing_impact_speed({3,-5,0})==5);
    assert(landing_impact_speed({3,-5,0},SuspensionVector{{0,-1,0}})==5);
    assert(landing_impact_speed({3,-5,0},SuspensionVector{{0,2,0}})==5);
    assert(std::fabs(landing_impact_speed({-3,-4,0},SuspensionVector{{3,4,0}})-5)<1e-12);
    assert(ground_follow_gap(100,0,1.0/60)==ground_follow_gap(0,0,1.0/60));
    assert(ground_follow_gap(10,.5,.1)>ground_follow_gap(-10,.5,.1));
    assert(launch_vertical_speed(10,.5)==0);
    assert(launch_vertical_speed(10,-.5)>0);
    assert(fall_damage(400,10)==0);
    assert(fall_damage(400,-15)==60);
    assert(overturn_level_from_up_cosine(1)==0);
    assert(overturn_level_from_up_cosine(0)==2);
    assert(overturn_level(0,0)==0);
    const auto candidates=hard_contact_candidate_yaws(1);
    assert(candidates[0]==1.55 && std::fabs(candidates[1]-.45)<1e-12 && candidates[2]==2 && candidates[3]==0);
    const auto blocked=hard_contact_step(10,1.0/60);
    assert(std::fabs(blocked.speed-3.5)<1e-12 && blocked.dx==0 && blocked.dz==0);
    const auto slide=hard_contact_step(10,1.0/60,false,0.0);
    assert(std::fabs(slide.speed-5.1)<1e-12 && slide.dx==0);
    const auto grinding=hard_contact_step(10,1.0/60,true,0.0);
    assert(std::fabs(grinding.speed-8.5)<1e-12);
    Parameters p{10000,100000,15,5,.5,{1,1.5,2},.7,15,1};
    const auto held=contact_push_step(p,.001,.001,1,1.0/30);
    assert(held[0]==0 && held[1]==0 && contact_push_is_held(p,.001,.001,1,1.0/30));
    const auto moving=contact_push_step(p,0,10,0,1,true);
    const auto parked=contact_push_step(p,0,10,0,1,false);
    assert(moving[1]>parked[1]);
    assert((contact_push_step(p,3,4,1,0)==std::array<double,2>({3,4})));
    assert(slope_slide_speed(0,.2,.1)==0);
    assert(slope_slide_speed(0,1,.1)>0);
    assert(suspension_slope_slide_speed(0,1,.1)>0);
    SuspensionParameters suspension;
    suspension.mass=10000;suspension.pitch_inertia=10000;suspension.roll_inertia=10000;
    suspension.fixed_step=1.0/60;suspension.constraint_iterations=10;
    SuspensionSpring spring;
    spring.static_compression=.2;spring.max_compression=.4;
    spring.stiffness=10000;spring.damping=1000;spring.max_force=100000;
    suspension.springs.push_back(spring);
    bool rejected=false;
    try { damper_suspension_step(suspension,{},GroundHeights{},.1); }
    catch (const std::invalid_argument&) { rejected=true; }
    assert(rejected);
    const auto air=damper_suspension_step(suspension,{},GroundHeights{std::nullopt},.1);
    assert(air.airborne && air.state.height<0 && air.state.vertical_velocity<0);
    const auto normal_up=ground_normal(0,0);
    assert(normal_up && (*normal_up)[1]==1);
    assert(ground_normal(1e308,-1e308));
    assert(!ground_normal(std::numeric_limits<double>::infinity(),0));
    const auto sampled=sampled_ground_plane(1.2,.8,1.1,.9,1.0,0,2,2,.01);
    assert(sampled && std::fabs(sampled->gradient_x-.1)<1e-12);
    assert(std::fabs(sampled->gradient_z-.2)<1e-12);
    assert(!sampled->center_x && !suspension_plane_height(sampled,0,0));
    assert(!sampled_ground_plane({},.8,1.1,.9,1.0,0,2,2,.01));
    assert(!sampled_ground_plane(1.2,.8,1.1,.9,3.0,0,2,2,.01));
    auto located=*sampled;
    located.center_x=10;located.center_z=20;
    assert(std::fabs(*suspension_plane_height(located,12,23)-1.8)<1e-12);
    assert(std::fabs(*suspension_support_vertical_velocity(located,3,-2)+.1)<1e-12);
    assert(suspension_ground_planes_continuous(located,located,{10,0,20},{12,0,23},0,0));
    auto jumped=located;jumped.center_y+=1;
    assert(!suspension_ground_planes_continuous(located,jumped,{10,0,20},{12,0,23},.1,.1));
    assert(!suspension_ground_planes_continuous({},located,{10,0,20},{12,0,23},.1,.1));
    assert(suspension_path_probe_fractions(1).empty());
    assert(suspension_path_probe_fractions(1.1)==std::vector<double>({.5}));
    assert(suspension_path_probe_fractions(10)==std::vector<double>({.25,.5,.75}));
    std::cout << "Suspension contact, plane, slide, landing and push checks passed\n";
}
