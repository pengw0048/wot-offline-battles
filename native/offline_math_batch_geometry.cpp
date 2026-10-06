/* The caller retains body ownership and native queries. These three complete
 * synchronous operations retain the Python collision law and roster order.
 */
#include "offline_math_batch.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace offline_math {
namespace {
constexpr double SLOP = .01;
struct Axis { double x, z, ra, rb; };
using Projection = std::array<Axis, 4>;
struct Hit { double x, z, depth; bool valid; };

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
    if (!a.has_y || !b.has_y) return true;
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

Hit signed_overlap(const Body &a,const Body &b) {
    const auto p=projection(a,b); const double dx=a.x-b.x,dz=a.z-b.z;
    Hit best={0.,0.,0.,true};bool first=true;
    for(const auto &axis:p) {
        const double distance=dx*axis.x+dz*axis.z;
        const double depth=axis.ra+axis.rb-std::abs(distance);
        if(first || depth<best.depth) {
            best={distance<0.?-axis.x:axis.x,distance<0.?-axis.z:axis.z,depth,true};first=false;
        }
    }
    return best;
}
Hit orient(Hit h,const Body &a,const Body &b) {
    const double dot=(a.x-b.x)*h.x+(a.z-b.z)*h.z;
    if(std::abs(dot)>1.e-9) return h;
    if(h.x<-1.e-9 || (std::abs(h.x)<=1.e-9 && h.z<0.)) {h.x=-h.x;h.z=-h.z;}
    if(a.id>b.id) {h.x=-h.x;h.z=-h.z;}
    return h;
}
} // namespace

double translation(const Body &body,Vec move,const std::vector<Body> &others) {
    if(std::abs(move.x)+std::abs(move.z)<=1.e-12) return 1.;
    const auto aa=axes(body.yaw);const double radius=std::hypot(body.shape[0],body.shape[1]);
    double fraction=1.;
    for(const Body &other:others) {
        if(body.id==other.id) continue;
        const double reach=radius+std::hypot(other.shape[0],other.shape[1]);
        if(other.x<body.x+std::min(0.,move.x)-reach || other.x>body.x+std::max(0.,move.x)+reach ||
           other.z<body.z+std::min(0.,move.z)-reach || other.z>body.z+std::max(0.,move.z)+reach) continue;
        if(!vertical_overlap(body,other)) continue;
        const double dx=body.x-other.x,dz=body.z-other.z;
        const Hit hit=orient(signed_overlap(body,other),body,other);
        if(hit.depth>=SLOP-1.e-9) {
            if(move.x*hit.x+move.z*hit.z<-1.e-9) fraction=0.;
            continue;
        }
        const auto bb=axes(other.yaw);
        const std::array<Vec,4> directions={{aa[0],aa[1],bb[0],bb[1]}};
        double entry=0.,leave=1.;
        for(Vec axis:directions) {
            double radius_sum=0.;
            for(size_t i=0;i<2;++i) radius_sum+=body.shape[i]*std::abs(axis.x*aa[i].x+axis.z*aa[i].z);
            for(size_t i=0;i<2;++i) radius_sum+=other.shape[i]*std::abs(axis.x*bb[i].x+axis.z*bb[i].z);
            const double axis_radius=radius_sum-SLOP;
            const double offset=dx*axis.x+dz*axis.z,travel=move.x*axis.x+move.z*axis.z;
            if(std::abs(travel)<=1.e-12) {
                if(std::abs(offset)>=axis_radius) {entry=2.;break;}
                continue;
            }
            double first=(-axis_radius-offset)/travel,last=(axis_radius-offset)/travel;
            if(last<first)std::swap(first,last);
            entry=std::max(entry,first);leave=std::min(leave,last);
            if(entry>leave) break;
        }
        if(entry<=leave && leave>=0.) fraction=std::min(fraction,std::max(0.,entry));
    }
    return fraction;
}
Vec slide(Body current,Vec remaining,const std::vector<Body> &others,bool has_first,double first) {
    Vec total={0.,0.};
    for(unsigned segment=0;segment<4;++segment) {
        const double fraction=segment==0 && has_first?first:translation(current,remaining,others);
        const Vec accepted={remaining.x*fraction,remaining.z*fraction};
        current.x+=accepted.x;total.x+=accepted.x;current.z+=accepted.z;total.z+=accepted.z;
        if(fraction>=1.) break;
        remaining={remaining.x*(1.-fraction),remaining.z*(1.-fraction)};
        bool changed=false;
        for(const Body &other:others) {
            if(current.id==other.id || !vertical_overlap(current,other)) continue;
            const Hit hit=orient(signed_overlap(current,other),current,other);
            if(hit.depth<SLOP-1.e-7) continue;
            const double entering=remaining.x*hit.x+remaining.z*hit.z;
            if(entering<-1.e-9) {
                remaining={remaining.x-entering*hit.x,remaining.z-entering*hit.z};changed=true;
            }
        }
        if(!changed || std::hypot(remaining.x,remaining.z)<=1.e-9) break;
    }
    return total;
}
struct RotationSearch {
    const Body &owner,&other;
    Vec move;double delta,pivot,fraction,samples,allowed,lipschitz,linear;
    double depth(double at) const {
        Body current=owner;
        current.yaw=owner.yaw+delta*at;
        current.x=owner.x+pivot*(std::cos(owner.yaw)-std::cos(current.yaw))+move.x*at;
        current.z=owner.z+pivot*(std::sin(current.yaw)-std::sin(owner.yaw))+move.z*at;
        return signed_overlap(current,other).depth;
    }
    bool blocked(uint64_t lo,uint64_t hi,double dl,double dh,uint64_t &out_lo,uint64_t &out_hi) const {
        const double span=static_cast<double>(hi-lo)*fraction/samples;
        if(std::max(dl,dh)+(lipschitz*std::abs(delta)+linear)*span*.5<=allowed+1.e-9) return false;
        if(hi-lo==1) {
            if(dh>allowed+1.e-9) {out_lo=lo;out_hi=hi;return true;}
            return false;
        }
        const uint64_t mid=lo+(hi-lo)/2;
        const double dm=depth(static_cast<double>(mid)*fraction/samples);
        return blocked(lo,mid,dl,dm,out_lo,out_hi) || blocked(mid,hi,dm,dh,out_lo,out_hi);
    }
};
double python_mod(double value,double divisor) {
    double result=std::fmod(value,divisor);
    if(result!=0.) {if((divisor<0.)!=(result<0.))result+=divisor;}
    else result=std::copysign(0.,divisor);
    return result;
}
double rotation(const Body &body,double candidate,double pivot,Vec move,const std::vector<Body> &others) {
    constexpr double PI=3.141592653589793238462643383279502884;
    const double delta=python_mod(candidate-body.yaw+PI,2.*PI)-PI;
    const double radius=std::hypot(body.shape[0],body.shape[1]);
    const double linear=std::hypot(move.x,move.z);
    const double travel=std::abs(delta)*(radius+std::abs(pivot))+linear;
    if(travel<=1.e-9) return 1.;
    const double samples=std::max(1.,std::ceil(travel/(SLOP*.5)));
    // The Python law has no work-budget cap. Only reject an index that this
    // uint64_t search cannot represent, leaving that operation to Python.
    if(!std::isfinite(samples) || samples>=18446744073709551616.)
        throw std::runtime_error("rotation index is not representable");
    double fraction=1.;
    for(const Body &other:others) {
        const double reach=radius+std::hypot(other.shape[0],other.shape[1])+std::abs(pivot)*std::abs(delta)+linear;
        if(square(body.x-other.x)+square(body.z-other.z)>reach*reach || !vertical_overlap(body,other)) continue;
        RotationSearch search={body,other,move,delta,pivot,fraction,samples,0.,0.,linear};
        search.allowed=std::max(SLOP,search.depth(0.));
        search.lipschitz=reach+std::abs(pivot)+std::hypot(body.x-other.x,body.z-other.z);
        uint64_t low_index=0,high_index=0;
        if(search.blocked(0,static_cast<uint64_t>(samples),search.depth(0.),search.depth(fraction),low_index,high_index)) {
            double low=static_cast<double>(low_index)*fraction/samples,high=static_cast<double>(high_index)*fraction/samples;
            while((high-low)*travel>1.e-6) {
                const double middle=(low+high)*.5;
                if(search.depth(middle)>search.allowed+1.e-9) high=middle;else low=middle;
            }
            fraction=low;
        }
    }
    return fraction;
}
} // namespace offline_math
