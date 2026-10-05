#include "offline_contact_roster.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <set>
#include <stdexcept>
#include <unordered_map>
#include <vector>

namespace offline_contact {
namespace {
constexpr double SLOP=.01,PERCENT=.95,PADDING=.25;
struct Vec {double x,z;};
struct Result {int64_t id;double value[5]={0.,0.,0.,0.,0.};};
struct Axis {double x,z,ra,rb;};
using Projection=std::array<Axis,4>;
struct Pair {size_t a,b;Projection projection;};
struct Hit {double x,z,depth;bool valid;};
struct Response {double v[8]={};};
using Angular=std::array<std::array<double,3>,2>;
struct Polygon {std::array<Vec,64> v;size_t count=0;};
double square(double x) { return std::pow(x,2.0); }
std::array<Vec,2> axes(double yaw) {
    const double sine=std::sin(yaw), cosine=std::cos(yaw);
    return {{{cosine,-sine},{sine,cosine}}};
}
std::array<double,2> vertical_interval(const Body &a) {
    const double cp=std::cos(a.pitch), sp=std::sin(a.pitch);
    const double cr=std::cos(a.roll), sr=std::sin(a.roll);
    const double up=cp*cr;
    const double center=a.y+(a.shape[2]+a.shape[3])*.5*up;
    const double extent=(std::abs(cp*sr)*a.shape[0]+std::abs(sp)*a.shape[1]+
                         std::abs(up)*(a.shape[3]-a.shape[2])*.5);
    return {{center-extent,center+extent}};
}
bool vertical_overlap(const Body &a,const Body &b) {
    if (!a.has(HasY) || !b.has(HasY)) return true;
    const auto aa=vertical_interval(a), bb=vertical_interval(b);
    return std::min(aa[1],bb[1])-std::max(aa[0],bb[0])>.02;
}
Projection projection(const Body &a,const Body &b) {
    const auto aa=axes(a.yaw), bb=axes(b.yaw);
    const std::array<Vec,4> directions={{aa[0],aa[1],bb[0],bb[1]}};
    Projection out;
    for(size_t i=0;i<4;++i) {
        const auto n=directions[i];
        const double ra=(a.shape[0]*std::abs(n.x*aa[0].x+n.z*aa[0].z)+
                         a.shape[1]*std::abs(n.x*aa[1].x+n.z*aa[1].z));
        const double rb=(b.shape[0]*std::abs(n.x*bb[0].x+n.z*bb[0].z)+
                         b.shape[1]*std::abs(n.x*bb[1].x+n.z*bb[1].z));
        out[i]={n.x,n.z,ra,rb};
    }
    return out;
}
Hit contact(const Body &a,const Body &b,const Projection &p) {
    const double dx=a.x-b.x,dz=a.z-b.z;
    Hit best={0.,0.,0.,false};
    bool first=true;
    for(const auto &axis:p) {
        const double distance=dx*axis.x+dz*axis.z;
        const double overlap=axis.ra+axis.rb-std::abs(distance);
        if(first || overlap<best.depth) {
            best={distance<0.?-axis.x:axis.x,distance<0.?-axis.z:axis.z,overlap,true};
            first=false;
        }
    }
    if(best.depth<=0.) { best.valid=false; return best; }
    const double center_projection=dx*best.x+dz*best.z;
    if(std::abs(center_projection)<=1.e-9) {
        if(best.x<-1.e-9 || (std::abs(best.x)<=1.e-9 && best.z<0.)) {
            best.x=-best.x; best.z=-best.z;
        }
        if(a.id>b.id) {best.x=-best.x;best.z=-best.z;}
    }
    return best;
}
std::array<double,2> grounded(const Hit &hit,const Body &a,const Body &b,
                              double ia,double ib,double dt) {
    if(dt<=0. || ia==0. || ib==0.) return {{ia,ib}};
    const double va=a.vx*hit.x+a.vz*hit.z, vb=b.vx*hit.x+b.vz*hit.z;
    if(va>=vb) return {{ia,ib}};
    auto holds=[&](const Body &body,double speed,double inverse,double moving_inverse) {
        if(!body.has(HasGrip) || std::abs(speed)>1.e-9) return false;
        const double impulse_speed=(vb-va)*inverse/moving_inverse;
        const double forward=std::abs(hit.x*std::sin(body.yaw)+hit.z*std::cos(body.yaw));
        const double side=std::abs(hit.x*std::cos(body.yaw)-hit.z*std::sin(body.yaw));
        return impulse_speed*forward<=body.grip[0]*dt && impulse_speed*side<=body.grip[1]*dt;
    };
    if(holds(a,va,ia,ib)) return {{0.,ib}};
    if(holds(b,vb,ib,ia)) return {{ia,0.}};
    return {{ia,ib}};
}
Response response(const Hit &h,double ia,double ib,const Body &a,const Body &b,
                  double friction_a,double friction_b) {
    Response r;
    const double sum=ia+ib;
    if(!h.valid || sum<=0.) return r;
    const double correction=std::max(h.depth-SLOP,0.)*PERCENT/sum;
    r.v[0]=h.x*correction*ia; r.v[1]=h.z*correction*ia;
    r.v[4]=-h.x*correction*ib; r.v[5]=-h.z*correction*ib;
    const double relative=(a.vx-b.vx)*h.x+(a.vz-b.vz)*h.z;
    if(relative<0.) {
        const double impulse=-relative/sum;
        r.v[2]=h.x*impulse*ia; r.v[3]=h.z*impulse*ia;
        r.v[6]=-h.x*impulse*ib; r.v[7]=-h.z*impulse*ib;
        const double tx=-h.z,tz=h.x,tangent_sum=friction_a+friction_b;
        double tangent=(a.vx-b.vx)*tx+(a.vz-b.vz)*tz;
        if(std::abs(tangent)<=1.e-9) tangent=0.;
        const double ti=tangent_sum!=0.?std::max(-.3*impulse,std::min(.3*impulse,-tangent/tangent_sum)):0.;
        r.v[2]+=tx*ti*friction_a; r.v[3]+=tz*ti*friction_a;
        r.v[6]-=tx*ti*friction_b; r.v[7]-=tz*ti*friction_b;
    }
    return r;
}
Polygon vertices(const Body &a) {
    Polygon out;
    const double s=std::sin(a.yaw),c=std::cos(a.yaw);
    for(const auto sign:std::array<Vec,4>{{{-1.,-1.},{1.,-1.},{1.,1.},{-1.,1.}}}) {
        out.v[out.count++]={a.x+sign.x*a.shape[0]*c+sign.z*a.shape[1]*s,
                           a.z+sign.x*a.shape[0]*-s+sign.z*a.shape[1]*c};
    }
    return out;
}
Polygon overlap_polygon(const Body &a,const Body &b) {
    Polygon poly=vertices(a), clip=vertices(b);
    for(size_t index=0;index<4;++index) {
        if(!poly.count) return poly;
        const Vec start=clip.v[index],end=clip.v[(index+1)%4];
        const double ex=end.x-start.x,ez=end.z-start.z;
        auto inside=[&](Vec p){return ex*(p.z-start.z)-ez*(p.x-start.x)>=-1.e-7;};
        auto intersection=[&](Vec first,Vec second) {
            const double sx=second.x-first.x,sz=second.z-first.z;
            const double denominator=sx*ez-sz*ex;
            if(std::abs(denominator)<=1.e-12) return second;
            const double ratio=((start.x-first.x)*ez-(start.z-first.z)*ex)/denominator;
            return Vec{first.x+ratio*sx,first.z+ratio*sz};
        };
        Polygon output;
        auto append=[&](Vec p){
            if(output.count==output.v.size()) throw std::runtime_error("polygon capacity");
            output.v[output.count++]=p;
        };
        Vec previous=poly.v[poly.count-1];
        bool previous_inside=inside(previous);
        for(size_t i=0;i<poly.count;++i) {
            const Vec current=poly.v[i]; const bool current_inside=inside(current);
            if(current_inside!=previous_inside) append(intersection(previous,current));
            if(current_inside) append(current);
            previous=current;previous_inside=current_inside;
        }
        poly=output;
    }
    return poly;
}
double inertia(const Body &body) {
    if(body.has(Alive) || body.has(Immovable)) return 0.;
    const double width=body.shape[0];
    const double length=body.shape[1];
    return body.mass*(square(width)+square(length))/3.;
}
Angular angular_response(const Body &a,const Body &b,const Hit &hit,double ia,double ib) {
    const std::array<const Body*,2> bodies={{&a,&b}};
    double inv_i[2];
    for(size_t i=0;i<2;++i) {const double value=inertia(*bodies[i]);inv_i[i]=value!=0.?1./value:0.;}
    const Polygon polygon=overlap_polygon(a,b);
    Angular changes={{{{0.,0.,0.}},{{0.,0.,0.}}}};
    if(!polygon.count) return changes;
    Vec point={0.,0.};
    for(size_t i=0;i<polygon.count;++i) {point.x+=polygon.v[i].x;point.z+=polygon.v[i].z;}
    point.x/=static_cast<double>(polygon.count); point.z/=static_cast<double>(polygon.count);
    const Vec arms[2]={{point.x-a.x,point.z-a.z},{point.x-b.x,point.z-b.z}};
    Angular velocities={{{{a.vx,a.vz,a.push_yaw}},{{b.vx,b.vz,b.push_yaw}}}};
    double normal_impulse=0.;
    for(size_t phase=0;phase<2;++phase) {
        const bool is_normal=phase==0;
        const Vec axis=is_normal?Vec{hit.x,hit.z}:Vec{-hit.z,hit.x};
        const double levers[2]={arms[0].z*axis.x-arms[0].x*axis.z,
                                arms[1].z*axis.x-arms[1].x*axis.z};
        double relative=0.;
        for(size_t i=0;i<2;++i) relative+=(i==0?1.:-1.)*(velocities[i][0]*axis.x+velocities[i][1]*axis.z+velocities[i][2]*levers[i]);
        const double inv[2]={is_normal?ia:(a.has(Immovable)?0.:1./a.mass),
                             is_normal?ib:(b.has(Immovable)?0.:1./b.mass)};
        double linear=0.,rotational=0.;
        for(size_t i=0;i<2;++i) linear+=inv[i];
        for(size_t i=0;i<2;++i) rotational+=inv_i[i]*square(levers[i]);
        const double effective=linear+rotational;
        if(effective<=0. || (is_normal && relative>=0.)) continue;
        double impulse=-relative/effective;
        if(is_normal) normal_impulse=impulse;
        else impulse=std::max(-.3*normal_impulse,std::min(.3*normal_impulse,impulse));
        for(size_t i=0;i<2;++i) {
            const double signed_impulse=i==0?impulse:-impulse;
            const double delta[3]={signed_impulse*axis.x*inv[i],signed_impulse*axis.z*inv[i],signed_impulse*levers[i]*inv_i[i]};
            for(size_t k=0;k<3;++k) {velocities[i][k]+=delta[k];changes[i][k]+=delta[k];}
        }
    }
    return changes;
}
// Four sequential passes update private poses and velocities. Position-fixed
// hulls still move in this private solver; only their published correction is held.
std::vector<Result> solve(std::vector<Body> bodies,double dt) {
    std::stable_sort(bodies.begin(),bodies.end(),[](const Body &a,const Body &b){return a.id<b.id;});
    std::vector<Result> results; results.reserve(bodies.size());
    std::vector<double> radii; radii.reserve(bodies.size());
    for(const Body &b:bodies) {results.push_back(Result{b.id,{0.,0.,0.,0.,0.}});radii.push_back(std::hypot(b.shape[0],b.shape[1]));}
    std::vector<Pair> pairs; pairs.reserve(bodies.size()*(bodies.size()?bodies.size()-1:0)/2);
    for(uint32_t i=0;i<bodies.size();++i) {
        const Body &a=bodies[i];
        for(uint32_t j=i+1;j<bodies.size();++j) {
            const Body &b=bodies[j];
            if(a.has(Player) && b.has(Player)) continue;
            if(!(a.has(Alive)||b.has(Alive)||a.vx||a.vz||b.vx||b.vz||a.push_yaw||b.push_yaw)) continue;
            const double reach=radii[i]+radii[j]+PADDING;
            if(square(a.x-b.x)+square(a.z-b.z)<=reach*reach && vertical_overlap(a,b))
                pairs.push_back(Pair{i,j,projection(a,b)});
        }
    }
    for(unsigned pass=0;pass<4;++pass) {
        for(const Pair &p:pairs) {
            Body &a=bodies[p.a], &b=bodies[p.b];
            const Hit hit=contact(a,b,p.projection);
            if(!hit.valid) continue;
            const double ia=a.has(Immovable)?0.:1./std::max(a.mass,1.),ib=b.has(Immovable)?0.:1./std::max(b.mass,1.);
            const auto mobility=grounded(hit,a,b,ia,ib,dt);
            const Response r=response(hit,mobility[0],mobility[1],a,b,ia,ib);
            const bool has_angular=inertia(a)!=0. || inertia(b)!=0.;
            Angular angular;
            if(has_angular) angular=angular_response(a,b,hit,mobility[0],mobility[1]);
            const bool apply_impulse=a.has(Impulse)&&b.has(Impulse);
            for(size_t k=0;k<2;++k) {
                Body &body=k==0?a:b; Result &result=results[k==0?p.a:p.b];
                const size_t offset=k*4; const double dx=r.v[offset],dz=r.v[offset+1];
                const double dvx=has_angular?angular[k][0]:r.v[offset+2];
                const double dvz=has_angular?angular[k][1]:r.v[offset+3];
                const double dvyaw=has_angular?angular[k][2]:0.;
                if(!body.has(PositionFixed)) {result.value[0]+=dx;result.value[1]+=dz;}
                body.x+=dx;body.z+=dz;
                if(apply_impulse) {
                    result.value[2]+=dvx;result.value[3]+=dvz;
                    body.vx+=dvx;body.vz+=dvz;
                    result.value[4]+=dvyaw;body.push_yaw+=dvyaw;
                }
            }
        }
    }
    return results;
}
Hit signed_overlap(const Body &a,const Body &b) {
    const auto projected=projection(a,b);
    const double dx=a.x-b.x,dz=a.z-b.z;
    Hit best={0.,0.,0.,true};bool first=true;
    for (const auto &axis:projected) {
        const double distance=dx*axis.x+dz*axis.z;
        const double depth=axis.ra+axis.rb-std::abs(distance);
        if (first || depth<best.depth) {
            best={distance<0.?-axis.x:axis.x,distance<0.?-axis.z:axis.z,depth,true};first=false;
        }
    }
    return best;
}
bool traverse_point(const Body &a,const Body &b,const Hit &hit,double omega,Vec &point) {
    auto polygon=overlap_polygon(a,b);
    if (!polygon.count) {
        Body enlarged=b;enlarged.shape[0]+=SLOP;enlarged.shape[1]+=SLOP;
        polygon=overlap_polygon(a,enlarged);
    }
    if (!polygon.count) return false;
    double minimum=0.;
    for (size_t index=0;index<polygon.count;++index) {
        const auto value=polygon.v[index];
        const double score=((value.z-a.z)*hit.x-(value.x-a.x)*hit.z)*omega;
        if (!index || score<minimum) {point=value;minimum=score;}
    }
    return true;
}
// Traverse uses original geometry with the normal solver's velocity/angular
// deltas, matching post_contact_velocity_bodies without publishing corrected poses.
void traverse(const std::vector<Body> &original,double dt,std::vector<Result> &physical) {
    if (dt<=0.) return;
    auto bodies=original;
    std::stable_sort(bodies.begin(),bodies.end(),[](const Body &a,const Body &b){return a.id<b.id;});
    std::vector<Vec> linear(bodies.size(),{0.,0.});
    std::vector<double> angular(bodies.size(),0.),radii;
    radii.reserve(bodies.size());
    for (size_t index=0;index<bodies.size();++index) {
        auto &body=bodies[index];
        body.vx+=physical[index].value[2];body.vz+=physical[index].value[3];
        body.push_yaw+=physical[index].value[4];
        radii.push_back(std::hypot(body.shape[0],body.shape[1]));
    }
    for (size_t ai=0;ai<bodies.size();++ai) {
        const auto &a=bodies[ai];
        const double omega=a.traverse_speed;
        double budget=a.traverse_torque*dt;
        if (!omega || budget<=0. || !a.has(Alive)) continue;
        for (size_t bi=0;bi<bodies.size();++bi) {
            const auto &b=bodies[bi];
            if (ai==bi || !a.has(Impulse) || !b.has(Impulse) || budget<=0.) continue;
            const double reach=radii[ai]+radii[bi]+SLOP;
            if (square(a.x-b.x)+square(a.z-b.z)>reach*reach || !vertical_overlap(a,b)) continue;
            const Hit hit=signed_overlap(a,b);
            if (hit.depth<-SLOP) continue;
            Vec point;
            if (!traverse_point(a,b,hit,omega,point)) continue;
            const double corner_x=point.x-a.x,corner_z=point.z-a.z;
            const double arm=corner_z*hit.x-corner_x*hit.z;
            if (arm*omega>=-1.e-9) continue;
            const double ia=a.has(Immovable)?0.:1./a.mass,ib=b.has(Immovable)?0.:1./b.mass;
            if (ia+ib<=0.) continue;
            double relative=(a.vx+linear[ai].x-b.vx-linear[bi].x)*hit.x+
                            (a.vz+linear[ai].z-b.vz-linear[bi].z)*hit.z;
            const double peer_inertia=inertia(b),inverse_i=peer_inertia?1./peer_inertia:0.;
            const double peer_arm=(a.z+corner_z-b.z)*hit.x-(a.x+corner_x-b.x)*hit.z;
            relative-=(b.push_yaw+angular[bi])*peer_arm;
            const double closing=std::max(0.,-arm*omega-relative);
            const double impulse=std::min(budget/std::abs(arm),closing/(ia+ib+square(peer_arm)*inverse_i));
            budget-=impulse*std::abs(arm);angular[bi]-=impulse*peer_arm*inverse_i;
            for (size_t side=0;side<2;++side) {
                const size_t at=side?bi:ai;
                const auto &body=bodies[at];
                const double inverse=side?ib:ia,sign=side?-1.:1.;
                auto &delta=linear[at];
                const double normal_speed=(body.vx+delta.x)*hit.x+(body.vz+delta.z)*hit.z;
                const double forward=std::abs(hit.x*std::sin(body.yaw)+hit.z*std::cos(body.yaw));
                const double across=std::abs(hit.x*std::cos(body.yaw)-hit.z*std::sin(body.yaw));
                const bool held=body.has(HasGrip) && std::abs(normal_speed)<=1.e-9 &&
                    impulse*inverse*forward<=body.grip[0]*dt && impulse*inverse*across<=body.grip[1]*dt;
                if (!held) {delta.x+=hit.x*sign*impulse*inverse;delta.z+=hit.z*sign*impulse*inverse;}
            }
        }
    }
    for (size_t index=0;index<bodies.size();++index) {
        physical[index].value[2]+=linear[index].x;physical[index].value[3]+=linear[index].z;
        physical[index].value[4]+=angular[index];
    }
}
Hit impact(const Body &a,const Body &b) {
    const auto projected=projection(a,b);
    const double dx=a.x-b.x,dz=a.z-b.z,rvx=a.vx-b.vx,rvz=a.vz-b.vz;
    Hit best={0.,0.,0.,false};double age=0.;
    for (const auto &axis:projected) {
        const double radius=axis.ra+axis.rb;
        const double distance=dx*axis.x+dz*axis.z;
        const double overlap=radius-std::abs(distance);
        if (overlap<=0.) return {0.,0.,0.,false};
        const double velocity=rvx*axis.x+rvz*axis.z;
        if (std::abs(velocity)<=1.e-9) continue;
        const double entry_age=velocity>0.?(radius+distance)/velocity:(radius-distance)/-velocity;
        if (entry_age<-1.e-9) continue;
        if (!best.valid || entry_age<age-1.e-9) {
            age=std::max(0.,entry_age);
            best={velocity>0.?-axis.x:axis.x,velocity>0.?-axis.z:axis.z,overlap,true};
        }
    }
    if (!best.valid || best.x*dx+best.z*dz<=1.e-9) best.valid=false;
    return best;
}
using Cell=std::pair<int64_t,int64_t>;
Cell bucket(const Body &body,double size) {
    const double x=std::floor(body.x/size),z=std::floor(body.z/size);
    // Representation safety only, not a gameplay/map-size restriction.
    if (std::abs(x)>=9.e18 || std::abs(z)>=9.e18) throw std::invalid_argument("contact cell overflow");
    return {static_cast<int64_t>(x),static_cast<int64_t>(z)};
}
}
std::vector<Row> resolve(const std::vector<Body> &bodies,const std::vector<int64_t> &owners,
                         double dt,const std::vector<std::pair<int64_t,int64_t>> &previous) {
    auto physical=solve(bodies,dt);
    traverse(bodies,dt,physical);
    std::unordered_map<int64_t,size_t> input_index,physical_index;
    double maximum_radius=4.;
    std::vector<double> radii;radii.reserve(bodies.size());
    for (size_t index=0;index<bodies.size();++index) {
        if (!input_index.emplace(bodies[index].id,index).second) throw std::invalid_argument("duplicate body");
        physical_index.emplace(physical[index].id,index);
        const auto &shape=bodies[index].shape;
        const double radius=std::sqrt(shape[0]*shape[0]+shape[1]*shape[1]);
        radii.push_back(radius);maximum_radius=std::max(maximum_radius,radius);
    }
    const double cell_size=maximum_radius*2.+4.;
    std::map<Cell,std::vector<size_t>> buckets;
    for (size_t index=0;index<bodies.size();++index) buckets[bucket(bodies[index],cell_size)].push_back(index);
    // Ram candidates use original frozen geometry and velocity, not either
    // physical solver's modified state. Engine armour/damage settlement remains
    // synchronous on the owner thread in this preserved peer order.
    const std::set<std::pair<int64_t,int64_t>> prior(previous.begin(),previous.end());
    std::vector<Row> rows;rows.reserve(owners.size());
    for (int64_t owner:owners) {
        const auto found=input_index.find(owner);
        if (found==input_index.end()) throw std::invalid_argument("missing owner");
        const size_t ai=found->second;
        const auto &a=bodies[ai];const auto &response=physical[physical_index.at(owner)];
        Row row;row.id=owner;
        row.correction={{response.value[0],response.value[1]}};
        row.delta_velocity={{response.value[2],response.value[3]}};row.delta_yaw=response.value[4];
        const auto cell=bucket(a,cell_size);
        for (int oz=-1;oz<=1;++oz) for (int ox=-1;ox<=1;++ox) {
            const auto nearby=buckets.find({cell.first+ox,cell.second+oz});
            if (nearby==buckets.end()) continue;
            for (size_t bi:nearby->second) {
                if (ai==bi) continue;
                const auto &other=bodies[bi];
                const double dx=a.x-other.x,dz=a.z-other.z,reach=radii[ai]+radii[bi]+PADDING;
                if (dx*dx+dz*dz>reach*reach) continue;
                row.has_peers=true;
                if (other.has(Alive) || other.vx || other.vz || other.push_yaw) row.has_active_peer=true;
                if (!a.has(Alive) || !vertical_overlap(a,other)) continue;
                if (!contact(a,other,projection(a,other)).valid) continue;
                const auto pair=std::make_pair(std::min(a.id,other.id),std::max(a.id,other.id));
                if (prior.count(pair)) {row.retained_contacts.push_back(pair);continue;}
                if ((a.team==1 || a.team==2) && a.team==other.team) continue;
                if (!other.has(Alive)) continue;
                Body b=other;
                if (b.has(Immovable)) b.vx=b.vy=b.vz=0.;
                const Hit first=impact(a,b);
                if (!first.valid) continue;
                const double closing=std::max(0.,-((a.vx-b.vx)*first.x+(a.vz-b.vz)*first.z));
                if (closing<=0.) continue;
                const double rvx=a.vx-b.vx,rvy=a.vy-b.vy,rvz=a.vz-b.vz;
                row.ram_candidates.push_back({b.id,{{first.x,first.z,first.depth}},closing,
                    std::sqrt(rvx*rvx+rvy*rvy+rvz*rvz),
                    a.vx!=0. || a.vy!=0. || a.vz!=0.,b.vx!=0. || b.vy!=0. || b.vz!=0.});
            }
        }
        rows.push_back(std::move(row));
    }
    return rows;
}
} // namespace offline_contact
