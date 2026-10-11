#include "native_visibility_core.h"
#include <algorithm>
#include <cmath>
#include <limits>

namespace native_visibility {
namespace {
constexpr double kTransparency = 15.0;
constexpr double kFoliageLimit = 0.60;
double clamp(double v, double lo, double hi) { return std::max(lo, std::min(hi, v)); }
Vec3 add(const Vec3& a,const Vec3& b) { return {{a[0]+b[0],a[1]+b[1],a[2]+b[2]}}; }
Vec3 sub(const Vec3& a,const Vec3& b) { return {{a[0]-b[0],a[1]-b[1],a[2]-b[2]}}; }
double dot(const Vec3& a,const Vec3& b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
Vec3 cross(const Vec3& a,const Vec3& b) {
    return {{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}};
}
Vec3 rotate_y(const Vec3& v,double angle) {
    const double s=std::sin(angle),c=std::cos(angle);
    return {{c*v[0]+s*v[2],v[1],-s*v[0]+c*v[2]}};
}
Vec3 transform(const Vec3& v,const Pose& pose) {
    const double sr=std::sin(pose.roll),cr=std::cos(pose.roll);
    const Vec3 rolled={{cr*v[0]-sr*v[1],sr*v[0]+cr*v[1],v[2]}};
    const double sp=std::sin(pose.pitch),cp=std::cos(pose.pitch);
    const Vec3 pitched={{rolled[0],cp*rolled[1]-sp*rolled[2],sp*rolled[1]+cp*rolled[2]}};
    return add(pose.position,rotate_y(pitched,pose.yaw));
}
std::vector<Cell> segment_cells(const Vec3& start,const Vec3& end,double cell_size) {
    cell_size=std::max(1.0,cell_size);
    const double sx=start[0],sz=start[2],ex=end[0],ez=end[2];
    const double dx=ex-sx,dz=ez-sz;
    int x=static_cast<int>(std::floor(sx/cell_size));
    int z=static_cast<int>(std::floor(sz/cell_size));
    const int endx=static_cast<int>(std::floor(ex/cell_size));
    const int endz=static_cast<int>(std::floor(ez/cell_size));
    std::vector<Cell> result;
    std::set<Cell> seen;
    auto append=[&](int a,int b) { const Cell cell(a,b); if(seen.insert(cell).second) result.push_back(cell); };
    auto append_point=[&](double px,double pz) {
        const int a=static_cast<int>(std::floor(px/cell_size));
        const int b=static_cast<int>(std::floor(pz/cell_size));
        const bool ax=std::abs(px-std::round(px/cell_size)*cell_size)<=1e-9;
        const bool bz=std::abs(pz-std::round(pz/cell_size)*cell_size)<=1e-9;
        for(int i=0;i<=(ax?1:0);++i) for(int j=0;j<=(bz?1:0);++j) append(a-i,b-j);
    };
    auto boundaries=[&]() {
        if(std::abs(dx)<=1e-12 && std::abs(sx-std::round(sx/cell_size)*cell_size)<=1e-9) {
            const auto original=result; for(const auto& cell:original) append(cell.first-1,cell.second);
        }
        if(std::abs(dz)<=1e-12 && std::abs(sz-std::round(sz/cell_size)*cell_size)<=1e-9) {
            const auto original=result; for(const auto& cell:original) append(cell.first,cell.second-1);
        }
    };
    append(x,z);
    if(x==endx && z==endz) { boundaries();append_point(sx,sz);append_point(ex,ez);return result; }
    int stepx=0,stepz=0;
    double tx=std::numeric_limits<double>::infinity(),tz=tx,dtx=tx,dtz=tx;
    if(dx>0) {stepx=1;tx=((x+1)*cell_size-sx)/dx;dtx=cell_size/dx;}
    else if(dx<0) {stepx=-1;tx=(x*cell_size-sx)/dx;dtx=-cell_size/dx;}
    if(dz>0) {stepz=1;tz=((z+1)*cell_size-sz)/dz;dtz=cell_size/dz;}
    else if(dz<0) {stepz=-1;tz=(z*cell_size-sz)/dz;dtz=-cell_size/dz;}
    for(;;) {
        const double next=std::min(tx,tz);
        if(next>1.0+1e-12) break;
        if(tx+1e-12<tz) {x+=stepx;tx+=dtx;append(x,z);}
        else if(tz+1e-12<tx) {z+=stepz;tz+=dtz;append(x,z);}
        else {append(x+stepx,z);append(x,z+stepz);x+=stepx;z+=stepz;tx+=dtx;tz+=dtz;append(x,z);}
        if(next>=1.0-1e-12) break;
    }
    boundaries();append_point(sx,sz);append_point(ex,ez);
    return result;
}
bool slab(double origin,double delta,double minimum,double maximum,double& low,double& high) {
    if(std::abs(delta)<=1e-9) return !(origin<minimum || origin>maximum);
    double first=(minimum-origin)/delta,second=(maximum-origin)/delta;
    if(first>second) std::swap(first,second);
    low=std::max(low,first);high=std::min(high,second);
    return low<=high;
}
bool intersects(const Volume& v,const Vec3& start,const Vec3& end) {
    double low=0.0,high=1.0;
    if(v.dynamic) {
        const Vec3 a=sub(start,v.center),b=sub(end,v.center);
        const std::array<Vec3,3> axes={{cross(v.half_axes[1],v.half_axes[2]),cross(v.half_axes[2],v.half_axes[0]),cross(v.half_axes[0],v.half_axes[1])}};
        for(std::size_t i=0;i<3;++i) {
            const double den=dot(axes[i],v.half_axes[i]);
            if(std::abs(den)<=1e-12) return false;
            const double first=dot(a,axes[i])/den,second=dot(b,axes[i])/den;
            if(!slab(first,second-first,-1.0,1.0,low,high)) return false;
        }
        return true;
    }
    const auto& r=v.static_row;
    const double ax=start[0]-r[0],az=start[2]-r[2],bx=end[0]-r[0],bz=end[2]-r[2];
    const double u0=r[4]*ax+r[5]*az,u1=r[4]*bx+r[5]*bz;
    const double w0=r[6]*ax+r[7]*az,w1=r[6]*bx+r[7]*bz;
    return slab(u0,u1-u0,-1.0,1.0,low,high) && slab(w0,w1-w0,-1.0,1.0,low,high) &&
           slab(start[1],end[1]-start[1],r[1],r[3],low,high);
}
double horizontal_distance(const Volume& v,const Vec3& point) {
    const double cx=v.dynamic?v.center[0]:v.static_row[0];
    const double cz=v.dynamic?v.center[2]:v.static_row[2];
    const double dx=point[0]-cx,dz=point[2]-cz;
    const double distance=std::sqrt(dx*dx+dz*dz);
    if(v.dynamic) return std::max(0.0,distance-v.radius);
    const auto& r=v.static_row;
    const double u=r[4]*dx+r[5]*dz,w=r[6]*dx+r[7]*dz;
    if(std::abs(u)<=1.0 && std::abs(w)<=1.0) return 0.0;
    const double det=r[4]*r[7]-r[5]*r[6];
    if(std::abs(det)<=1e-12) return std::max(0.0,distance-r[9]);
    const std::array<std::pair<double,double>,4> signs={{{-1,-1},{1,-1},{1,1},{-1,1}}};
    std::array<std::pair<double,double>,4> corners;
    for(std::size_t i=0;i<4;++i) {
        const double a=signs[i].first,b=signs[i].second;
        corners[i]={(r[7]*a-r[5]*b)/det,(r[4]*b-r[6]*a)/det};
    }
    double nearest=std::numeric_limits<double>::infinity();
    for(std::size_t i=0;i<4;++i) {
        const auto& a=corners[i];const auto& b=corners[(i+1)%4];
        const double ex=b.first-a.first,ez=b.second-a.second;
        const double along=clamp(((dx-a.first)*ex+(dz-a.second)*ez)/(ex*ex+ez*ez),0,1);
        nearest=std::min(nearest,std::hypot(dx-a.first-along*ex,dz-a.second-along*ez));
    }
    return nearest;
}
bool transparent(const Volume& v,const Vec3& point) {
    const double cx=v.dynamic?v.center[0]:v.static_row[0],cz=v.dynamic?v.center[2]:v.static_row[2];
    const double dx=point[0]-cx,dz=point[2]-cz;
    const double distance=std::sqrt(dx*dx+dz*dz);
    if(distance<=kTransparency) return true;
    double radius=v.dynamic?v.radius:v.static_row[9];
    if(!v.dynamic) {
        const auto& r=v.static_row;const double det=r[4]*r[7]-r[5]*r[6];
        if(std::abs(det)>1e-12) radius=std::max(std::hypot(r[7]-r[5],r[4]-r[6]),std::hypot(r[7]+r[5],r[4]+r[6]))/std::abs(det);
    }
    if(distance-radius>kTransparency) return false;
    return horizontal_distance(v,point)<=kTransparency;
}
} // namespace
std::vector<Vec3> vehicle_check_points(const Checkpoints& cp,const Pose& pose,bool observer,std::uint64_t phase) {
    if(!cp.available) return {{{pose.position[0],pose.position[1]+(observer?2.0:1.5),pose.position[2]}}};
    std::vector<Vec3> out;out.reserve(observer?1:6);
    const std::size_t first=observer?static_cast<std::size_t>(phase%2):0,last=observer?first+1:6;
    for(std::size_t i=first;i<last;++i) {
        Vec3 point=cp.points[i];
        if(i==1 && cp.turret_mount_available) point=add(cp.turret_mount,rotate_y(sub(point,cp.turret_mount),cp.static_turret_yaw_present?cp.static_turret_yaw:pose.relative_turret_yaw));
        out.push_back(transform(point,pose));
    }
    return out;
}
double foliage_camouflage_bonus(const FoliageSnapshot& foliage,const Vec3& observer,const Vec3& target,bool fired,const Vec3& start,const Vec3& end) {
    if(!foliage.enabled) return 0.0;
    std::vector<std::size_t> candidates;std::set<std::size_t> seen;
    for(const Cell& cell:segment_cells(start,end,foliage.cell_size)) {
        const auto at=foliage.cells.find(cell);if(at==foliage.cells.end()) continue;
        for(std::size_t id:at->second) if(seen.insert(id).second) candidates.push_back(id);
    }
    double bonus=0;
    for(std::size_t id:candidates) {
        if(id>=foliage.instances.size() || foliage.inactive_instances.count(id)) continue;
        const Volume& volume=foliage.instances[id];
        if(!intersects(volume,start,end) || transparent(volume,observer)) continue;
        const double strength=std::max(0.0,volume.dynamic?volume.strength:volume.static_row[8]);
        if(fired && transparent(volume,target)) continue;
        bonus+=strength;if(bonus>=kFoliageLimit) return kFoliageLimit;
    }
    return std::min(kFoliageLimit,std::max(0.0,bonus));
}
PreparedVisibility prepare_visibility(const PairInput& input,const FoliageSnapshot& foliage) {
    PreparedVisibility out;out.detection=input.detection;
    const auto starts=vehicle_check_points(input.observer_checkpoints,input.observer,true,input.observer_phase);
    const auto ends=vehicle_check_points(input.target_checkpoints,input.target,false);
    out.rays.reserve(starts.size()*ends.size());
    for(const auto& start:starts) for(const auto& end:ends) {
        Ray ray;ray.start=start;ray.end=end;
        ray.foliage_bonus=foliage_camouflage_bonus(foliage,input.observer.position,input.target.position,input.detection.fired_recently,start,end);
        out.rays.push_back(ray);
    }
    return out;
}
bool should_stop(const PreparedVisibility& prepared,std::size_t index,bool clear) {
    return clear && index<prepared.rays.size() && prepared.rays[index].foliage_bonus<=0.0;
}
namespace {
VisibilityResult detection_result(const DetectionInputs& d,bool line_of_sight,double foliage_bonus) {
    VisibilityResult out;out.line_of_sight=line_of_sight;out.foliage_bonus=foliage_bonus;
    double camo=(d.base_camouflage[d.moving?0:1]+d.additive)*std::max(0.0,d.multiplier);
    if(d.fired_recently) camo*=clamp(d.shot_factor,0,1);
    out.camouflage=clamp(camo+clamp(out.foliage_bonus,0,kFoliageLimit),0,.95);
    const double view=std::max(50.0,d.view_range);
    out.detection_distance=clamp(view-(view-50.0)*out.camouflage,50,445);
    const double distance=std::max(0.0,d.distance);
    out.detected=distance<=50 || (out.line_of_sight && distance<=out.detection_distance);
    return out;
}
}
bool can_detect_with_foliage(const DetectionInputs& detection,double foliage_bonus) {
    return detection_result(detection,true,foliage_bonus).detected;
}
VisibilityResult reduce_visibility(const PreparedVisibility& prepared,const std::vector<std::uint8_t>& clear) {
    bool line_of_sight=false;double best=std::numeric_limits<double>::infinity();
    for(std::size_t i=0;i<std::min(clear.size(),prepared.rays.size());++i) if(clear[i]) {
        line_of_sight=true;best=std::min(best,prepared.rays[i].foliage_bonus);if(best<=0.0) break;
    }
    return detection_result(prepared.detection,line_of_sight,line_of_sight?best:0.0);
}
} // namespace native_visibility
