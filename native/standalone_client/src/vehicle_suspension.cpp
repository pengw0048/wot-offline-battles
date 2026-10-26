#include "wot/vehicle_suspension.hpp"

#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>
#include <utility>

namespace wot::offline_vehicle_control {
namespace {
constexpr double pi=3.14159265358979323846;
double gravity(const Tuning& tuning) { return 9.81*tuning.gravity_factor; }
struct Rotation { double sp,cp,sr,cr; };
Rotation rotation(double pitch,double roll) {
    return {std::sin(pitch),std::cos(pitch),std::sin(roll),std::cos(roll)};
}
double point_height(const SuspensionState& state,const SuspensionPoint& point,
                    const Rotation& r) {
    return state.height+(r.cp*(r.sr*point.x+r.cr*point.y)-r.sp*point.z);
}
std::array<double,2> height_gradients(const SuspensionPoint& point,const Rotation& r) {
    return {-r.sp*(r.sr*point.x+r.cr*point.y)-r.cp*point.z,
             r.cp*(r.cr*point.x-r.sr*point.y)};
}
using ContactKey=std::pair<bool,std::size_t>;
using Contacts=std::set<ContactKey>;
struct Constraint {
    ContactKey key;
    const SuspensionPoint* point;
    double ground;
    double penetration;
};
std::vector<Constraint> constraints(const SuspensionParameters& p,
    const GroundHeights& grounds,const GroundHeights& pseudo) {
    std::vector<Constraint> result;
    for (std::size_t i=0;i<p.springs.size();++i)
        if (grounds[i]) result.push_back({{false,i},&p.springs[i],*grounds[i],
            p.springs[i].max_compression-p.springs[i].static_compression});
    for (std::size_t i=0;i<p.pseudo_contacts.size();++i)
        if (pseudo[i]) result.push_back({{true,i},&p.pseudo_contacts[i],*pseudo[i],
            p.pseudo_contacts[i].penetration});
    return result;
}
// Python math.fsum's error-free partial sums and final half-even correction.
// The projection groups tied deepest contacts, so ordinary accumulation would
// introduce a spring-order bias that the Python implementation avoids.
double precise_sum(const std::vector<double>& values) {
    std::vector<double> partials;
    for (double x:values) {
        std::size_t count=0;
        for (double y:partials) {
            if (std::fabs(x)<std::fabs(y)) std::swap(x,y);
            const double hi=x+y;
            if (!std::isfinite(hi)) throw std::overflow_error("Suspension sum overflow");
            const double low=y-(hi-x);
            if (low!=0) partials[count++]=low;
            x=hi;
        }
        partials.resize(count);
        partials.push_back(x);
    }
    double hi=0,low=0;
    std::size_t count=partials.size();
    if (count) {
        hi=partials[--count];
        while (count) {
            const double x=hi,y=partials[--count];
            hi=x+y;low=y-(hi-x);
            if (low!=0) break;
        }
        if (count && ((low<0 && partials[count-1]<0) ||
                      (low>0 && partials[count-1]>0))) {
            const double y=low*2,x=hi+y;
            if (y==x-hi) hi=x;
        }
    }
    return hi;
}
Contacts project_limits(const SuspensionParameters& p,SuspensionState& state,
    const std::vector<Constraint>& fixed,double support_velocity,double offset) {
    const double inv_mass=1.0/p.mass,inv_pitch=1.0/p.pitch_inertia,
                 inv_roll=1.0/p.roll_inertia;
    Contacts touched;
    if (fixed.empty()) return touched;
    struct Row { double excess,pitch,roll,denominator; };
    for (std::size_t iteration=0;iteration<p.constraint_iterations;++iteration) {
        const auto r=rotation(state.pitch,state.roll);
        std::vector<Row> rows;
        double maximum=0;
        for (const auto& c:fixed) {
            const double excess=c.ground+offset-point_height(state,*c.point,r)-c.penetration;
            if (excess<=1e-5) continue;
            touched.insert(c.key);
            const auto grad=height_gradients(*c.point,r);
            const double pitch=-grad[0],roll=-grad[1];
            const double denominator=inv_mass+pitch*pitch*inv_pitch+roll*roll*inv_roll;
            if (denominator<=1e-12) continue;
            rows.push_back({excess,pitch,roll,denominator});
            maximum=std::max(maximum,excess);
        }
        if (rows.empty()) break;
        const double tolerance=std::max(1e-10,maximum*1e-8);
        std::array<std::vector<double>,6> sums;
        std::size_t selected=0;
        for (const auto& row:rows) {
            if (maximum-row.excess>tolerance) continue;
            ++selected;
            const double point_velocity=((state.vertical_velocity+
                -row.pitch*state.pitch_velocity)+-row.roll*state.roll_velocity)-support_velocity;
            const double velocity_impulse=point_velocity<0 ? -point_velocity/row.denominator : 0;
            sums[0].push_back(velocity_impulse*inv_mass);
            sums[1].push_back(-velocity_impulse*row.pitch*inv_pitch);
            sums[2].push_back(-velocity_impulse*row.roll*inv_roll);
            const double impulse=row.excess/row.denominator;
            sums[3].push_back(impulse*inv_mass);
            sums[4].push_back(-impulse*row.pitch*inv_pitch);
            sums[5].push_back(-impulse*row.roll*inv_roll);
        }
        const double scale=1.0/static_cast<double>(selected);
        state.vertical_velocity+=precise_sum(sums[0])*scale;
        state.pitch_velocity+=precise_sum(sums[1])*scale;
        state.roll_velocity+=precise_sum(sums[2])*scale;
        state.height+=precise_sum(sums[3])*scale;
        state.pitch+=precise_sum(sums[4])*scale;
        state.roll+=precise_sum(sums[5])*scale;
    }
    double remaining=0;
    const auto r=rotation(state.pitch,state.roll);
    for (const auto& c:fixed) {
        const double excess=c.ground+offset-point_height(state,*c.point,r)-c.penetration;
        if (excess>0) { remaining=std::max(remaining,excess);touched.insert(c.key); }
    }
    if (remaining>0) state.height+=remaining+1e-9;
    return touched;
}
struct ContactSets { Contacts all,left,right; };
ContactSets contact_keys(const SuspensionParameters& p,const SuspensionState& state,
    const GroundHeights& ground,const GroundHeights& pseudo) {
    ContactSets result;
    const auto r=rotation(state.pitch,state.roll);
    auto insert=[&](ContactKey key,ContactSide side) {
        result.all.insert(key);
        if (side==ContactSide::left) result.left.insert(key);
        else if (side==ContactSide::right) result.right.insert(key);
    };
    for (std::size_t i=0;i<p.springs.size();++i) {
        if (!ground[i]) continue;
        const auto& s=p.springs[i];
        if (s.static_compression+*ground[i]-point_height(state,s,r)<=0) continue;
        insert({false,i},s.side);
    }
    for (std::size_t i=0;i<p.pseudo_contacts.size();++i) {
        if (!pseudo[i]) continue;
        const auto& c=p.pseudo_contacts[i];
        if (*pseudo[i]-point_height(state,c,r)<-0.1) continue;
        insert({true,i},c.side);
    }
    return result;
}
bool finite(double value) { return std::isfinite(value); }
double bleed(double value,double budget) {
    if (budget<=0) return value;
    if (value>budget) return value-budget;
    if (value<-budget) return value+budget;
    return 0;
}
template<class Points>
std::vector<std::array<double,2>> world_points(const Points& points,
    SuspensionVector position,double yaw,double pitch,double roll) {
    const double sine=std::sin(yaw),cosine=std::cos(yaw);
    std::vector<std::array<double,2>> result;
    for (const auto& p:points) {
        const auto local=suspension_point_offset(p,pitch,roll);
        result.push_back({position[0]+cosine*local[0]+sine*local[2],
                          position[2]-sine*local[0]+cosine*local[2]});
    }
    return result;
}
} // namespace

SuspensionVector suspension_point_offset(const SuspensionPoint& point,double pitch,double roll) {
    const auto r=rotation(pitch,roll);
    const double y=r.sr*point.x+r.cr*point.y;
    return {r.cr*point.x-r.sr*point.y,r.cp*y-r.sp*point.z,r.sp*y+r.cp*point.z};
}
std::vector<std::array<double,2>> suspension_world_points(const SuspensionParameters& p,
    SuspensionVector position,double yaw,double pitch,double roll) {
    return world_points(p.springs,position,yaw,pitch,roll);
}
std::vector<std::array<double,2>> suspension_pseudo_world_points(const SuspensionParameters& p,
    SuspensionVector position,double yaw,double pitch,double roll) {
    return world_points(p.pseudo_contacts,position,yaw,pitch,roll);
}
double suspension_limit_excess(const SuspensionParameters& p,const SuspensionState& state,
    const GroundHeights& ground) {
    if (ground.size()!=p.springs.size()) throw std::invalid_argument("Suspension ground sample count mismatch");
    double maximum=0;
    const auto r=rotation(state.pitch,state.roll);
    for (std::size_t i=0;i<p.springs.size();++i) {
        if (!ground[i]) continue;
        const auto& s=p.springs[i];
        const double compression=s.static_compression+*ground[i]-point_height(state,s,r);
        maximum=std::max(maximum,compression-s.max_compression);
    }
    return maximum;
}

SuspensionResult damper_suspension_step(const SuspensionParameters& p,
    SuspensionState state,const GroundHeights& ground,double elapsed,
    std::optional<GroundHeights> pseudo_ground,double support_velocity,const Tuning& tuning) {
    if (ground.size()!=p.springs.size()) throw std::invalid_argument("Suspension ground sample count mismatch");
    if (!pseudo_ground) pseudo_ground=GroundHeights(p.pseudo_contacts.size());
    const auto& pseudo=*pseudo_ground;
    if (pseudo.size()!=p.pseudo_contacts.size()) throw std::invalid_argument("Pseudo-contact ground sample count mismatch");
    if (!finite(support_velocity)) throw std::invalid_argument("Support vertical velocity is not finite");
    if (!(p.mass>0 && p.pitch_inertia>0 && p.roll_inertia>0 && p.fixed_step>0))
        throw std::invalid_argument("Suspension mass, inertia and timestep must be positive");
    const auto fixed=constraints(p,ground,pseudo);
    const double total=std::max(0.0,std::min(0.2,elapsed));
    const auto steps=std::min(12,std::max(1,static_cast<int>(std::ceil(total/p.fixed_step))));
    const double step=total/static_cast<double>(steps);
    SuspensionResult result;
    Contacts touched;
    bool transition=false;
    double vertical_acceleration=0,pitch_acceleration=0,roll_acceleration=0;
    auto impact=[&](double speed) {
        result.impact_speed=result.impact_speed ? std::min(*result.impact_speed,speed) : speed;
    };
    for (int substep=0;substep<steps;++substep) {
        const auto r=rotation(state.pitch,state.roll);
        const double force_offset=-support_velocity*std::max(0.0,total-step*static_cast<double>(substep));
        const double projection_offset=-support_velocity*std::max(0.0,total-step*static_cast<double>(substep+1));
        double force=-p.mass*gravity(tuning),pitch_torque=0,roll_torque=0;
        std::size_t contacts=0;
        const double vertical_speed=state.vertical_velocity;
        const double relative_speed=vertical_speed-support_velocity;
        for (std::size_t i=0;i<p.springs.size();++i) {
            if (!ground[i]) continue;
            const auto& s=p.springs[i];
            const double compression=s.static_compression+(*ground[i]+force_offset)-point_height(state,s,r);
            if (compression<=0) continue;
            result.max_compression=std::max(result.max_compression,compression);
            touched.insert({false,i});++contacts;
            if (!transition && relative_speed<0) impact(vertical_speed);
            const auto grad=height_gradients(s,r);
            const double velocity=((state.vertical_velocity+grad[0]*state.pitch_velocity)+
                                  grad[1]*state.roll_velocity)-support_velocity;
            double spring_force=s.stiffness*compression-s.damping*velocity;
            if (compression>s.max_compression) {
                const double excess=compression-s.max_compression;
                spring_force+=s.stiffness*8.0*excess*(1.0+excess/std::max(s.max_compression,0.01));
            }
            spring_force=std::max(0.0,std::min(s.max_force,spring_force));
            force+=spring_force;pitch_torque+=grad[0]*spring_force;roll_torque+=grad[1]*spring_force;
        }
        for (std::size_t i=0;i<p.pseudo_contacts.size();++i) {
            if (!pseudo[i]) continue;
            const double gap=(*pseudo[i]+force_offset)-point_height(state,p.pseudo_contacts[i],r);
            if (gap<-0.1) continue;
            touched.insert({true,i});++contacts;
            if (!transition && relative_speed<0) impact(vertical_speed);
        }
        if (contacts) transition=true;
        if (!contacts) {
            const double decay=std::exp(-4.0*step);
            state.pitch_velocity=std::max(-0.6,std::min(0.6,state.pitch_velocity))*decay;
            state.roll_velocity=std::max(-0.6,std::min(0.6,state.roll_velocity))*decay;
        }
        vertical_acceleration=force/p.mass;
        pitch_acceleration=pitch_torque/p.pitch_inertia;
        roll_acceleration=roll_torque/p.roll_inertia;
        state.vertical_velocity+=vertical_acceleration*step;
        state.pitch_velocity+=pitch_acceleration*step;
        state.roll_velocity+=roll_acceleration*step;
        state.height+=state.vertical_velocity*step;
        state.pitch+=state.pitch_velocity*step;
        state.roll+=state.roll_velocity*step;
        const double before=state.vertical_velocity;
        const auto projected=project_limits(p,state,fixed,support_velocity,projection_offset);
        if (!projected.empty()) {
            if (!transition && before-support_velocity<0) impact(before);
            transition=true;touched.insert(projected.begin(),projected.end());
        }
    }
    const auto final=contact_keys(p,state,ground,pseudo);
    if (!final.all.empty() && !transition) {
        if (state.vertical_velocity-support_velocity<0) result.impact_speed=state.vertical_velocity;
        transition=true;
    }
    touched.insert(final.all.begin(),final.all.end());
    if (!final.all.empty()) {
        if (std::fabs(vertical_acceleration)<0.4 && std::fabs(state.vertical_velocity-support_velocity)<0.15)
            state.vertical_velocity=support_velocity;
        if (std::fabs(pitch_acceleration)<0.35 && std::fabs(state.pitch_velocity)<0.06) state.pitch_velocity=0;
        if (std::fabs(roll_acceleration)<0.35 && std::fabs(state.roll_velocity)<0.06) state.roll_velocity=0;
    }
    result.state=state;result.contact_count=final.all.size();result.airborne=final.all.empty();
    result.left_flying=final.left.empty();result.right_flying=final.right.empty();
    result.contacted_this_step=!touched.empty();result.touched_contact_count=touched.size();
    result.max_limit_excess=std::max(0.0,suspension_limit_excess(p,state,ground));
    const auto r=rotation(state.pitch,state.roll);
    for (std::size_t i=0;i<p.pseudo_contacts.size();++i)
        if (pseudo[i]) result.max_limit_excess=std::max(result.max_limit_excess,
            *pseudo[i]-point_height(state,p.pseudo_contacts[i],r)-p.pseudo_contacts[i].penetration);
    return result;
}

std::optional<SuspensionVector> ground_normal(double gx,double gz) {
    if (!finite(gx) || !finite(gz)) return {};
    const double scale=std::max({1.0,std::fabs(gx),std::fabs(gz)});
    const double x=gx/scale,y=1.0/scale,z=gz/scale;
    const double length=std::sqrt(x*x+y*y+z*z);
    if (length<=0 || !finite(length)) return {};
    const SuspensionVector normal{{-x/length,y/length,-z/length}};
    if (normal[1]<=0) return {};
    return normal;
}
std::optional<GroundPlane> sampled_ground_plane(std::optional<double> front,
    std::optional<double> rear,std::optional<double> right,std::optional<double> left,
    std::optional<double> center,double yaw,double length,double width,double limit) {
    if (!front || !rear || !right || !left || !center) return {};
    for (double value:{*front,*rear,*right,*left,*center,yaw,length,width,limit})
        if (!finite(value)) return {};
    if (length<=0 || width<=0 || limit<0) return {};
    const double longitudinal=(*front+*rear)*0.5,lateral=(*right+*left)*0.5;
    const double residual=std::max({std::fabs(longitudinal-lateral),
        std::fabs(*center-longitudinal),std::fabs(*center-lateral)});
    if (residual>limit) return {};
    const double forward=(*front-*rear)/length,across=(*right-*left)/width;
    const double sine=std::sin(yaw),cosine=std::cos(yaw);
    const double gx=forward*sine+across*cosine,gz=forward*cosine-across*sine;
    const auto normal=ground_normal(gx,gz);
    if (!normal) return {};
    const double tangent=std::hypot(forward,across);
    if (!finite(tangent)) return {};
    double dx=-gx,dz=-gz;
    const double downhill=std::hypot(dx,dz);
    if (downhill>0.001) { dx/=downhill;dz/=downhill; }
    else { dx=0;dz=0; }
    GroundPlane plane;
    plane.center_y=*center;plane.gradient_x=gx;plane.gradient_z=gz;plane.normal=*normal;
    plane.pitch=-std::atan2(*front-*rear,length);
    plane.roll=std::atan2(across,std::hypot(1.0,forward));
    plane.slope_tangent=tangent;plane.up_cosine=(*normal)[1];
    plane.downhill=SuspensionVector{{dx,0,dz}};plane.residual=residual;
    return plane;
}
std::optional<SuspensionLocalPlane> suspension_ground_plane(const SuspensionParameters& p,
    const GroundHeights& ground,std::optional<double> maximum_residual,
    std::optional<std::vector<std::array<double,2>>> sample_points) {
    if (ground.size()!=p.springs.size()) return {};
    if (sample_points && sample_points->size()!=p.springs.size()) return {};
    std::vector<SuspensionVector> rows;
    for (std::size_t i=0;i<p.springs.size();++i) {
        if (!ground[i]) continue;
        const double x=sample_points ? (*sample_points)[i][0] : p.springs[i].x;
        const double z=sample_points ? (*sample_points)[i][1] : p.springs[i].z;
        const double y=*ground[i];
        if (!finite(x) || !finite(z) || !finite(y)) return {};
        rows.push_back({x,z,y});
    }
    if (rows.size()<3) return {};
    double mx=0,mz=0,my=0;
    for (const auto& row:rows) { mx+=row[0];mz+=row[1];my+=row[2]; }
    const double count=static_cast<double>(rows.size());mx/=count;mz/=count;my/=count;
    double xx=0,xz=0,zz=0,xy=0,zy=0;
    for (const auto& row:rows) {
        const double dx=row[0]-mx,dz=row[1]-mz,dy=row[2]-my;
        xx+=dx*dx;xz+=dx*dz;zz+=dz*dz;xy+=dx*dy;zy+=dz*dy;
    }
    const double determinant=xx*zz-xz*xz;
    if (determinant<=1e-8) return {};
    const double gx=(xy*zz-zy*xz)/determinant,gz=(zy*xx-xy*xz)/determinant;
    const double center=my-gx*mx-gz*mz;
    double residual=0;
    for (const auto& row:rows)
        residual=std::max(residual,std::fabs(row[2]-(center+gx*row[0]+gz*row[1])));
    if (maximum_residual && residual>std::max(0.0,*maximum_residual)) return {};
    return SuspensionLocalPlane{center,gx,gz,residual,rows.size()};
}
std::optional<GroundPlane> suspension_world_ground_plane(const SuspensionParameters& p,
    const GroundHeights& ground,SuspensionVector position,double yaw,
    std::optional<double> maximum_residual,double pitch,double roll) {
    std::vector<std::array<double,2>> points;
    for (const auto& spring:p.springs) {
        const auto point=suspension_point_offset(spring,pitch,roll);
        points.push_back({point[0],point[2]});
    }
    const auto local=suspension_ground_plane(p,ground,maximum_residual,points);
    if (!local || !finite(position[0]) || !finite(position[2]) || !finite(yaw)) return {};
    const double sine=std::sin(yaw),cosine=std::cos(yaw);
    const double gx=local->z_gradient*sine+local->x_gradient*cosine;
    const double gz=local->z_gradient*cosine-local->x_gradient*sine;
    if (!finite(local->center_y) || !finite(gx) || !finite(gz) || !finite(local->max_residual)) return {};
    const auto normal=ground_normal(gx,gz);
    if (!normal) return {};
    GroundPlane plane;
    plane.center_x=position[0];plane.center_z=position[2];plane.center_y=local->center_y;
    plane.gradient_x=gx;plane.gradient_z=gz;plane.normal=*normal;
    plane.max_residual=local->max_residual;plane.contact_count=local->contact_count;
    return plane;
}
std::optional<double> suspension_plane_height(std::optional<GroundPlane> plane,double x,double z) {
    if (!plane || !plane->center_x || !plane->center_z) return {};
    for (double value:{*plane->center_x,*plane->center_z,plane->center_y,
                      plane->gradient_x,plane->gradient_z,x,z})
        if (!finite(value)) return {};
    const double height=plane->center_y+plane->gradient_x*(x-*plane->center_x)+
                        plane->gradient_z*(z-*plane->center_z);
    return finite(height) ? std::optional<double>(height) : std::nullopt;
}
std::optional<double> suspension_support_vertical_velocity(std::optional<GroundPlane> plane,double x,double z) {
    if (!plane) return {};
    for (double value:{plane->gradient_x,plane->gradient_z,x,z}) if (!finite(value)) return {};
    return plane->gradient_x*x+plane->gradient_z*z;
}
std::optional<double> suspension_drive_pitch(std::optional<GroundPlane> plane,double yaw) {
    if (!finite(yaw)) return {};
    const auto tangent=suspension_support_vertical_velocity(plane,std::sin(yaw),std::cos(yaw));
    return tangent ? std::optional<double>(-std::atan(*tangent)) : std::nullopt;
}
bool suspension_ground_planes_continuous(std::optional<GroundPlane> previous,
    std::optional<GroundPlane> current,SuspensionVector start,SuspensionVector end,
    double height_tolerance,double gradient_tolerance) {
    for (double value:{start[0],start[2],end[0],end[2],height_tolerance,gradient_tolerance})
        if (!finite(value)) return false;
    if (height_tolerance<0 || gradient_tolerance<0) return false;
    const auto ps=suspension_plane_height(previous,start[0],start[2]);
    const auto cs=suspension_plane_height(current,start[0],start[2]);
    const auto pe=suspension_plane_height(previous,end[0],end[2]);
    const auto ce=suspension_plane_height(current,end[0],end[2]);
    if (!ps || !cs || !pe || !ce) return false;
    if (std::fabs(*ps-*cs)>height_tolerance || std::fabs(*pe-*ce)>height_tolerance) return false;
    const double dx=current->gradient_x-previous->gradient_x,dz=current->gradient_z-previous->gradient_z;
    return dx*dx+dz*dz<=gradient_tolerance*gradient_tolerance+1e-12;
}
std::vector<double> suspension_path_probe_fractions(double distance) {
    distance=std::max(0.0,distance);
    if (!finite(distance)) throw std::invalid_argument("Suspension path distance is not finite");
    const double count=std::ceil(distance)-1;
    const int steps=count>=3 ? 3 : (count<=0 ? 0 : static_cast<int>(count));
    std::vector<double> result;
    for (int i=0;i<steps;++i) result.push_back(static_cast<double>(i+1)/static_cast<double>(steps+1));
    return result;
}

RetainedGroundResult retained_ground_contact(std::array<double,2> point,
    std::optional<double> ground,std::optional<RetainedGroundContact> memory,
    double maximum_distance,std::optional<std::array<double,2>> gradient) {
    const double x=point[0],z=point[1],distance=std::max(0.0,maximum_distance);
    if (!finite(x) || !finite(z) || !finite(distance)) return {};
    if (ground) {
        if (!finite(*ground)) return {};
        return {ground,RetainedGroundContact{x,z,*ground,x,z,0,false}};
    }
    if (!memory) return {};
    auto previous=*memory;
    previous.travelled=std::max(0.0,previous.travelled);
    for (double v:{previous.origin_x,previous.origin_z,previous.height,
                  previous.last_x,previous.last_z,previous.travelled})
        if (!finite(v)) return {};
    const double step_x=x-previous.last_x,step_z=z-previous.last_z;
    const double squared=step_x*step_x+step_z*step_z;
    if (squared<=1e-10) {
        if (previous.last_query_missed) return {};
        previous.last_x=x;previous.last_z=z;previous.last_query_missed=true;
        return {previous.height,previous};
    }
    const double dx=x-previous.origin_x,dz=z-previous.origin_z;
    previous.travelled+=std::sqrt(squared);
    if (dx*dx+dz*dz>distance*distance+1e-12 || previous.travelled>distance+1e-9) return {};
    double height=previous.height;
    if (gradient) {
        if (!finite((*gradient)[0]) || !finite((*gradient)[1])) return {};
        height+=(*gradient)[0]*dx+(*gradient)[1]*dz;
    }
    previous.last_x=x;previous.last_z=z;previous.last_query_missed=true;
    return {height,previous};
}
bool suspension_support_allowed(double height,double normal_y,
    std::optional<double> flat_maximum_y) {
    if (!finite(height) || !finite(normal_y) || normal_y<=0.5) return false;
    if (!flat_maximum_y || height<=*flat_maximum_y+0.01) return true;
    return normal_y<0.995;
}
double suspension_vertical_sweep_drop(double speed,double elapsed,const Tuning& tuning) {
    const double step=std::max(0.0,elapsed);
    return std::max(0.0,-speed*step+gravity(tuning)*step*step);
}
double landing_impact_speed(SuspensionVector velocity,std::optional<SuspensionVector> normal) {
    const double vertical=finite(velocity[1]) ? std::max(0.0,-velocity[1]) : 0;
    if (!normal) return vertical;
    const auto& n=*normal;
    if (!finite(n[0]) || !finite(n[1]) || !finite(n[2]) || n[1]<=0) return vertical;
    const double scale=std::max({std::fabs(n[0]),std::fabs(n[1]),std::fabs(n[2])});
    if (scale<=0) return vertical;
    const double x=n[0]/scale,y=n[1]/scale,z=n[2]/scale;
    const double length=std::sqrt(x*x+y*y+z*z);
    if (length<=0 || !finite(length)) return vertical;
    for (double v:velocity) if (!finite(v)) return vertical;
    const double closing=-(velocity[0]*x+velocity[1]*y+velocity[2]*z)/length;
    return finite(closing) ? std::max(0.0,closing) : vertical;
}
double ground_follow_gap(double speed,double pitch,double elapsed,const Tuning& tuning) {
    const double step=std::max(0.0,elapsed);
    pitch=std::max(-0.96,std::min(0.96,pitch));
    const double drop=std::max(0.0,speed*std::tan(pitch)*step);
    const double gap=0.6+drop+gravity(tuning)*step*step;
    return std::max(0.8,std::min(2.5,gap));
}
double launch_vertical_speed(double speed,double pitch) {
    const double vertical=speed*std::sin(-pitch);
    return vertical>0 ? vertical : 0;
}
double slope_slide_speed(double current,double tangent,double elapsed,const Tuning& tuning) {
    const double theta=std::atan(tangent);
    if (tangent<=tuning.slide_hold_tan) {
        current-=tuning.cohesion*gravity(tuning)*elapsed;
        return current>0 ? current : 0;
    }
    current+=(gravity(tuning)*(std::sin(theta)-tuning.slide_hold_tan*std::cos(theta))-
              tuning.slide_drag*current)*elapsed;
    if (current<0) current=0;
    else if (current>tuning.slide_max) current=tuning.slide_max;
    return current;
}
double suspension_slope_slide_speed(double current,double tangent,double elapsed,const Tuning& tuning) {
    const double theta=std::atan(tangent);
    const double hold=tuning.slide_hold_tan*lateral_slope_grip(std::cos(theta));
    if (tangent<=hold) {
        current-=tuning.cohesion*gravity(tuning)*elapsed;
        return current>0 ? current : 0;
    }
    current+=(gravity(tuning)*(std::sin(theta)-hold*std::cos(theta))-
              tuning.slide_drag*current)*elapsed;
    if (current<0) current=0;
    else if (current>tuning.slide_max) current=tuning.slide_max;
    return current;
}
int fall_damage(int maximum_health,double speed,const Tuning& tuning) {
    const double impact=std::fabs(speed);
    if (impact<=tuning.fall_safe_speed) return 0;
    return static_cast<int>(maximum_health*(impact-tuning.fall_safe_speed)*tuning.fall_damage_per_speed);
}
int overturn_level_from_up_cosine(double up,std::optional<double> warning,std::optional<double> danger) {
    const double warning_y=warning.value_or(std::cos(70.0*pi/180.0));
    const double danger_y=danger.value_or(std::cos(80.0*pi/180.0));
    up=std::max(-1.0,std::min(1.0,up));
    if (up<=danger_y) return 2;
    if (up<=warning_y) return 1;
    return 0;
}
int overturn_level(double pitch,double roll,std::optional<double> warning,std::optional<double> danger) {
    return overturn_level_from_up_cosine(std::cos(pitch)*std::cos(roll),warning,danger);
}
std::array<double,4> hard_contact_candidate_yaws(double yaw) {
    return {yaw+0.55,yaw-0.55,yaw+1.0,yaw-1.0};
}
HardContactResult hard_contact_step(double speed,double elapsed,bool grinding,std::optional<double> yaw) {
    const double dt=std::max(0.0,elapsed);
    if (!yaw) {
        speed*=std::pow(0.35,dt*60.0);
        if (std::fabs(speed)<0.05) speed=0;
        return {speed,0,0};
    }
    if (!grinding) speed*=0.60;
    speed*=std::pow(0.85,dt*60.0);
    return {speed,std::sin(*yaw)*speed*dt,std::cos(*yaw)*speed*dt};
}
std::array<double,2> contact_push_deceleration(const Parameters& p,bool rolling,
    std::size_t terrain,double normal_y,const Tuning& tuning) {
    const double ny=normal_y>0.1 ? normal_y : 0.1;
    const double roll=rolling_resist_force(p,terrain,false,tuning)/(p.mass>1.0 ? p.mass : 1.0);
    const double hold=tuning.slide_hold_tan*gravity(tuning)*ny;
    const double longitudinal=rolling ? roll : (hold>roll ? hold : roll);
    double lateral=tuning.slide_hold_tan*lateral_slope_grip(normal_y)*gravity(tuning)*ny;
    if (lateral<roll) lateral=roll;
    return {longitudinal,lateral};
}
std::array<double,2> contact_push_step(const Parameters& p,double x,double z,double yaw,
    double elapsed,bool rolling,std::size_t terrain,double normal_y,const Tuning& tuning) {
    if (elapsed<=0) return {x,z};
    const auto decel=contact_push_deceleration(p,rolling,terrain,normal_y,tuning);
    const double sine=std::sin(yaw),cosine=std::cos(yaw);
    double forward=x*sine+z*cosine,right=x*cosine-z*sine;
    forward=bleed(forward,decel[0]*elapsed);right=bleed(right,decel[1]*elapsed);
    return {forward*sine+right*cosine,forward*cosine-right*sine};
}
bool contact_push_is_held(const Parameters& p,double x,double z,double yaw,double elapsed,
    bool rolling,std::size_t terrain,double normal_y,const Tuning& tuning) {
    return contact_push_step(p,x,z,yaw,elapsed,rolling,terrain,normal_y,tuning)==std::array<double,2>{{0,0}};
}
} // namespace wot::offline_vehicle_control
