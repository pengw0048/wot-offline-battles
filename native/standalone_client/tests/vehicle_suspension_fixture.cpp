// C ABI used only by the Python-source differential audit.
#include "wot/vehicle_suspension.hpp"
#include <algorithm>

using namespace wot::offline_vehicle_control;

extern "C" int suspension_fixture(const double* config,const double* springs,
    int spring_count,const double* contacts,int contact_count,const double* ground,
    const unsigned char* ground_valid,const double* pseudo_ground,
    const unsigned char* pseudo_valid,const double* state,double elapsed,
    double support_velocity,double* output) {
    try {
        SuspensionParameters p;
        p.mass=config[0];p.pitch_inertia=config[1];p.roll_inertia=config[2];
        p.fixed_step=config[3];p.constraint_iterations=static_cast<std::size_t>(config[4]);
        GroundHeights heights,pseudo;
        for (int i=0;i<spring_count;++i) {
            const auto* v=springs+i*9;
            SuspensionSpring s;
            s.x=v[0];s.y=v[1];s.z=v[2];s.side=static_cast<ContactSide>(static_cast<int>(v[3]));
            s.static_compression=v[4];s.max_compression=v[5];s.stiffness=v[6];s.damping=v[7];s.max_force=v[8];
            p.springs.push_back(s);
            heights.push_back(ground_valid[i] ? std::optional<double>(ground[i]) : std::nullopt);
        }
        for (int i=0;i<contact_count;++i) {
            const auto* v=contacts+i*5;
            SuspensionPseudoContact c;
            c.x=v[0];c.y=v[1];c.z=v[2];c.side=static_cast<ContactSide>(static_cast<int>(v[3]));c.penetration=v[4];
            p.pseudo_contacts.push_back(c);
            pseudo.push_back(pseudo_valid[i] ? std::optional<double>(pseudo_ground[i]) : std::nullopt);
        }
        const auto result=damper_suspension_step(p,
            {state[0],state[1],state[2],state[3],state[4],state[5]},heights,elapsed,pseudo,support_velocity);
        const auto& s=result.state;
        const double values[]={s.height,s.vertical_velocity,s.pitch,s.pitch_velocity,s.roll,s.roll_velocity,
            static_cast<double>(result.contact_count),result.airborne?1.0:0.0,result.left_flying?1.0:0.0,
            result.right_flying?1.0:0.0,result.contacted_this_step?1.0:0.0,
            static_cast<double>(result.touched_contact_count),result.impact_speed?1.0:0.0,
            result.impact_speed.value_or(0),result.max_compression,result.max_limit_excess};
        std::copy(std::begin(values),std::end(values),output);
        return 0;
    } catch (...) { return -1; }
}

extern "C" void suspension_helpers(const double* v,const double* parameters,double* output) {
    Parameters p{parameters[0],parameters[1],parameters[2],parameters[3],parameters[4],
        {parameters[5],parameters[6],parameters[7]},parameters[8],parameters[9],parameters[10],parameters[11]};
    const auto hard=hard_contact_step(v[0],v[1],v[2]!=0,v[3]!=0 ? std::optional<double>(v[4]) : std::nullopt);
    const auto push=contact_push_step(p,v[8],v[9],v[4],v[1],v[10]!=0,static_cast<std::size_t>(v[11]),v[7]);
    const auto decel=contact_push_deceleration(p,v[10]!=0,static_cast<std::size_t>(v[11]),v[7]);
    const double result[]={hard.speed,hard.dx,hard.dz,slope_slide_speed(v[15],v[5],v[1]),
        suspension_slope_slide_speed(v[15],v[5],v[1]),ground_follow_gap(v[0],v[6],v[1]),
        launch_vertical_speed(v[0],v[6]),landing_impact_speed({v[16],v[17],v[18]},
            v[22]!=0 ? std::optional<SuspensionVector>({{v[19],v[20],v[21]}}) : std::nullopt),
        static_cast<double>(fall_damage(static_cast<int>(v[12]),v[13])),
        static_cast<double>(overturn_level_from_up_cosine(v[14])),push[0],push[1],
        contact_push_is_held(p,v[8],v[9],v[4],v[1],v[10]!=0,static_cast<std::size_t>(v[11]),v[7])?1.0:0.0,
        decel[0],decel[1],suspension_vertical_sweep_drop(v[17],v[1])};
    std::copy(std::begin(result),std::end(result),output);
}

extern "C" int suspension_plane_fixture(const double* points,int count,
    const double* ground,const unsigned char* valid,const double* position,
    double yaw,double pitch,double roll,double limit,double* output) {
    SuspensionParameters p;
    GroundHeights heights;
    for (int i=0;i<count;++i) {
        SuspensionSpring s;s.x=points[i*3];s.y=points[i*3+1];s.z=points[i*3+2];
        p.springs.push_back(s);
        heights.push_back(valid[i] ? std::optional<double>(ground[i]) : std::nullopt);
    }
    const auto plane=suspension_world_ground_plane(p,heights,{position[0],position[1],position[2]},yaw,limit,pitch,roll);
    if (!plane) return 0;
    const double result[]={*plane->center_x,*plane->center_z,plane->center_y,
        plane->gradient_x,plane->gradient_z,plane->normal[0],plane->normal[1],plane->normal[2],
        *plane->max_residual,static_cast<double>(*plane->contact_count),
        *suspension_drive_pitch(plane,yaw),*suspension_support_vertical_velocity(plane,3,-2),
        *suspension_plane_height(plane,position[0]+1,position[2]-2)};
    std::copy(std::begin(result),std::end(result),output);
    return 1;
}
