#include "offline_destructible_geometry.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <mutex>
#include <stdexcept>

namespace offline_destructible {
namespace {
constexpr double PI = 3.14159265358979323846;
double dot(const Point &a, const Point &b) {
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
}
Point cross(const Point &a, const Point &b) {
    return {{a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]}};
}
std::array<double, 4> bounds(const Box &box) {
    double x = 0., z = 0.;
    for (const auto &axis : box.axes) { x += std::abs(axis[0]); z += std::abs(axis[2]); }
    return {{box.center[0]-x, box.center[0]+x, box.center[2]-z, box.center[2]+z}};
}
int cell(double value) {
    value = std::floor(value / 8.);
    if (!std::isfinite(value) || value < std::numeric_limits<int>::min() ||
        value >= std::numeric_limits<int>::max()) throw std::invalid_argument("invalid destructible bounds");
    return static_cast<int>(value);
}
template<class Function> void cells(const std::array<double, 4> &b, Function function) {
    const int x0=cell(b[0]), x1=cell(b[1]), z0=cell(b[2]), z1=cell(b[3]);
    for (int x=x0; x<=x1; ++x) for (int z=z0; z<=z1; ++z) function(Cell{x,z});
}
using Vertex = std::pair<double, double>;
std::vector<Vertex> hull(const Box &box) {
    std::vector<Vertex> points{{box.center[0],box.center[2]}};
    for (const auto &axis : box.axes) {
        if (axis[0]*axis[0]+axis[2]*axis[2] <= 1.e-16) continue;
        std::vector<Vertex> next;
        for (const auto &p : points) for (double sign : {-1.,1.})
            next.emplace_back(p.first+sign*axis[0],p.second+sign*axis[2]);
        points.swap(next);
    }
    std::sort(points.begin(),points.end());
    points.erase(std::unique(points.begin(),points.end()),points.end());
    if (points.size()<=2) return points;
    const auto turn=[](const Vertex &o,const Vertex &a,const Vertex &b) {
        return (a.first-o.first)*(b.second-o.second)-(a.second-o.second)*(b.first-o.first);
    };
    std::vector<Vertex> low,high;
    for (const auto &p:points) {
        while(low.size()>=2 && turn(low[low.size()-2],low.back(),p)<=1.e-12) low.pop_back();
        low.push_back(p);
    }
    for (auto i=points.rbegin();i!=points.rend();++i) {
        while(high.size()>=2 && turn(high[high.size()-2],high.back(),*i)<=1.e-12) high.pop_back();
        high.push_back(*i);
    }
    low.pop_back();high.pop_back();low.insert(low.end(),high.begin(),high.end());return low;
}
bool near(double x,double z,const std::vector<Vertex> &poly,double radius) {
    if(poly.empty())return false;
    if(poly.size()==1) {const double dx=x-poly[0].first,dz=z-poly[0].second;return dx*dx+dz*dz<=radius*radius;}
    bool inside=true,have_distance=false;double minimum=0.;
    for(std::size_t i=0;i<poly.size();++i) {
        const auto &a=poly[i],&b=poly[(i+1)%poly.size()];
        const double ex=b.first-a.first,ez=b.second-a.second;
        if(ex*(z-a.second)-ez*(x-a.first)<-1.e-8)inside=false;
        const double length=ex*ex+ez*ez;
        double fraction=length<=1.e-16?0.:((x-a.first)*ex+(z-a.second)*ez)/length;
        fraction=std::max(0.,std::min(1.,fraction));
        const double nx=a.first+ex*fraction,nz=a.second+ez*fraction;
        const double distance=std::pow(x-nx,2.)+std::pow(z-nz,2.);
        if(!have_distance||distance<minimum){minimum=distance;have_distance=true;}
    }
    return inside||(have_distance&&minimum<=radius*radius+1.e-8);
}
std::pair<double,double> trig(double a,double b,double start,double end) {
    if(end<start)std::swap(start,end);
    const auto value=[&](double angle){return a*std::cos(angle)+b*std::sin(angle);};
    double low=value(start),high=value(end);if(high<low)std::swap(high,low);
    const double stationary=std::atan2(b,a);
    const int first=static_cast<int>(std::ceil((start-stationary)/PI));
    const int last=static_cast<int>(std::floor((end-stationary)/PI));
    for(int i=first;i<=last;++i){const double v=value(stationary+i*PI);low=std::min(low,v);high=std::max(high,v);}
    return {low,high};
}
std::mutex stores_mutex;
std::map<std::int64_t,std::shared_ptr<Store>> stores;
std::int64_t next_handle=0;
}

bool intersects(const Box &left,const Box &right) {
    Point delta;for(unsigned i=0;i<3;++i)delta[i]=right.center[i]-left.center[i];
    std::vector<Point> generators=left.axes;generators.insert(generators.end(),right.axes.begin(),right.axes.end());
    for(std::size_t i=0;i<generators.size();++i)for(std::size_t j=i+1;j<generators.size();++j){
        const auto axis=cross(generators[i],generators[j]);const double length=dot(axis,axis);
        if(length<=1.e-16)continue;
        double a=0.,b=0.;for(const auto &g:left.axes)a+=std::abs(dot(axis,g));for(const auto &g:right.axes)b+=std::abs(dot(axis,g));
        if(std::abs(dot(delta,axis))>a+b+1.e-7*std::pow(length,.5))return false;
    }
    return true;
}
Box vehicle_box(const Pose &p) {
    const double cy=std::cos(p.yaw),sy=std::sin(p.yaw),cp=std::cos(p.pitch),sp=std::sin(p.pitch),cr=std::cos(p.roll),sr=std::sin(p.roll);
    const Point right{{cy,sr*cp,-sy}},up{{0.,cr*cp,0.}},forward{{sy,-sp,cy}};
    const double x=(p.minimum[0]+p.maximum[0])*.5,y=(p.minimum[1]+p.maximum[1])*.5,z=(p.minimum[2]+p.maximum[2])*.5;
    const double hx=(p.maximum[0]-p.minimum[0])*.5,hy=(p.maximum[1]-p.minimum[1])*.5,hz=(p.maximum[2]-p.minimum[2])*.5;
    const double angle=p.explicit_motion_yaw?p.motion_yaw:(p.travel>=0.?p.yaw:p.yaw+PI);
    const double distance=std::abs(p.travel),tx=std::sin(angle)*distance,tz=std::cos(angle)*distance;
    Box result;result.center={{p.position[0]+right[0]*x+up[0]*y+forward[0]*z+tx*.5,
        p.position[1]+right[1]*x+up[1]*y+forward[1]*z,
        p.position[2]+right[2]*x+up[2]*y+forward[2]*z+tz*.5}};
    Point a,b,c;for(unsigned i=0;i<3;++i){a[i]=right[i]*hx;b[i]=up[i]*hy;c[i]=forward[i]*hz;}
    result.axes={a,b,c};if(distance)result.axes.push_back({{tx*.5,0.,tz*.5}});return result;
}
std::vector<Box> tree_sweep(const Point &start,double start_yaw,const Point &end,double end_yaw,const Point &minimum,const Point &maximum,double pivot) {
    for(unsigned i=0;i<3;++i)if(minimum[i]>maximum[i])return {};
    const double dx=end[0]-start[0],dy=end[1]-start[1],dz=end[2]-start[2];
    const double distance=std::pow(dx*dx+dz*dz,.5);
    // Python's modulo is nonnegative for the positive divisor.
    double delta=std::fmod(end_yaw-start_yaw+PI,2.*PI);if(delta<0.)delta+=2.*PI;delta-=PI;
    const double count=std::max(1.,std::max(std::ceil(distance/8.),std::ceil(std::abs(delta)/(PI/36.))));
    if(count>128.)return {};
    const int steps=static_cast<int>(count);std::vector<Box> output;
    for(int index=0;index<steps;++index){
        const double t0=double(index)/double(steps),t1=double(index+1)/double(steps);
        Point p0{{start[0]+dx*t0,start[1]+dy*t0,start[2]+dz*t0}},p1{{start[0]+dx*t1,start[1]+dy*t1,start[2]+dz*t1}};
        const double yaw0=start_yaw+delta*t0,yaw1=start_yaw+delta*t1,mid=(yaw0+yaw1)*.5,half=std::abs((yaw1-yaw0)*.5);
        Point low=minimum,high=maximum;bool first=true;
        for(double x:{minimum[0],maximum[0]})for(double z:{minimum[2],maximum[2]}){
            const auto a=trig(x-pivot,z,-half,half),b=trig(z,pivot-x,-half,half);
            if(first){low[0]=a.first+pivot;high[0]=a.second+pivot;low[2]=b.first;high[2]=b.second;first=false;}
            else{low[0]=std::min(low[0],a.first+pivot);high[0]=std::max(high[0],a.second+pivot);low[2]=std::min(low[2],b.first);high[2]=std::max(high[2],b.second);}
        }
        if(pivot){
            const double a=std::cos(start_yaw),b=std::sin(start_yaw),c=std::cos(mid),d=std::sin(mid);
            p0={{start[0]+(a-c)*pivot,start[1],start[2]+(d-b)*pivot}};p1=p0;
        }
        const double x=(low[0]+high[0])*.5,y=(low[1]+high[1])*.5,z=(low[2]+high[2])*.5;
        const double hx=(high[0]-low[0])*.5,hy=(high[1]-low[1])*.5,hz=(high[2]-low[2])*.5,cy=std::cos(mid),sy=std::sin(mid);
        const Point travel{{p1[0]-p0[0],p1[1]-p0[1],p1[2]-p0[2]}};Box box;
        box.center={{p0[0]+cy*x+sy*z+travel[0]*.5,p0[1]+y+travel[1]*.5,p0[2]-sy*x+cy*z+travel[2]*.5}};
        box.axes={{{cy*hx,0.,-sy*hx}},{{0.,hy,0.}},{{sy*hz,0.,cy*hz}},{{travel[0]*.5,travel[1]*.5,travel[2]*.5}}};output.push_back(std::move(box));
    }
    return output;
}
void Store::put(Instance value) {
    std::set<Cell> bins=value.bins;
    remove(value.key);for(auto c:bins)contacts_[c].insert(value.key);reverse_[value.key]=std::move(bins);instances_[value.key]=std::move(value);
}
void Store::remove(Key key) {
    auto at=reverse_.find(key);if(at!=reverse_.end())for(auto c:at->second){auto bin=contacts_.find(c);if(bin!=contacts_.end()){bin->second.erase(key);if(bin->second.empty())contacts_.erase(bin);}}
    reverse_.erase(key);instances_.erase(key);
}
void Store::chunk(std::int64_t id,Chunk value){chunks_[id]=std::move(value);}
void Store::drop_chunk(std::int64_t id){
    chunks_.erase(id);std::vector<Key> removed;for(const auto &entry:instances_)if(entry.first.first==id)removed.push_back(entry.first);for(auto key:removed)remove(key);
}
void Store::baked(std::map<Cell,std::set<Key>> value){baked_=std::move(value);}
CatalogResult Store::catalog(const Box &sweep,const Box *contact) const {
    CatalogResult result;std::set<Key> seen;
    cells(bounds(sweep),[&](Cell cell){
        auto bin=contacts_.find(cell);if(bin==contacts_.end())return;result.had_members=result.had_members||!bin->second.empty();
        for(auto key:bin->second){++result.members;if(!seen.insert(key).second){++result.duplicates;continue;}
            auto at=instances_.find(key);if(at==instances_.end())continue;const auto &instance=at->second;
            std::set<std::pair<int,Point>> distinct;
            for(std::size_t i=0;i<instance.boxes.size();++i){const auto &box=instance.boxes[i];if(!intersects(sweep,box))continue;
                const int material=instance.kind==1?box.material:-1;
                if(!distinct.insert({material,box.center}).second)continue;
                result.candidates.push_back({key,static_cast<int>(i),false});
            }
        }
    });
    std::map<std::pair<Key,int>,Candidate> groups;
    for(auto candidate:result.candidates){const auto &instance=instances_.at(candidate.key);const int material=instance.kind==1?instance.boxes[candidate.box].material:-1;
        auto group=std::make_pair(candidate.key,material);if(groups.count(group))continue;
        if(contact)for(const auto &box:instance.boxes)if((instance.kind!=1||box.material==material)&&intersects(*contact,box)){candidate.contact=true;break;}
        groups.emplace(group,candidate);
    }
    for (const auto &entry : groups) result.grouped.push_back(entry.second);
    return result;
}
std::vector<int> Store::trees(std::int64_t id,const std::vector<Box> &sweeps,double radius) const {
    auto at=chunks_.find(id);if(at==chunks_.end())throw std::invalid_argument("missing streamed chunk");
    const auto &chunk=at->second;std::set<int> seen;std::vector<int> result;
    for(const auto &sweep:sweeps){auto b=bounds(sweep);b[0]-=radius;b[1]+=radius;b[2]-=radius;b[3]+=radius;
        bool prepared=false;std::vector<Vertex> polygon;
        cells(b,[&](Cell c){auto bin=chunk.origins.find(c);if(bin==chunk.origins.end())return;for(int index:bin->second){
            if (seen.count(index)) continue;
            const auto &item=chunk.items.at(index);
            if (!item.tree || !item.named) continue;
            if(!prepared){polygon=hull(sweep);prepared=true;}if(!near(item.position[0],item.position[2],polygon,radius))continue;
            seen.insert(index);result.push_back(index);
        }});
    }
    return result;
}
BodyResult Store::body(std::int64_t id,const Pose &pose,double speed,const Box &sweep) const {
    auto at=chunks_.find(id);if(at==chunks_.end())throw std::invalid_argument("missing streamed chunk");
    const auto &chunk=at->second;BodyResult result;std::set<int> seen;
    const auto hits=trees(id,{sweep},0.);const std::set<int> tree_hits(hits.begin(),hits.end());
    const double sy=std::sin(pose.yaw),cy=std::cos(pose.yaw);
    const std::array<double,4> origin{{pose.position[0]-8.,pose.position[0]+8.,pose.position[2]-8.,pose.position[2]+8.}};
    const auto visit=[&](const std::map<Cell,std::vector<int>> &bins,const std::array<double,4> &b){cells(b,[&](Cell c){
        auto bin=bins.find(c);if(bin==bins.end())return;for(int index:bin->second){
            if (!seen.insert(index).second) continue;
            ++result.nearby;
            const auto &item=chunk.items.at(index);
            if(!item.catalog)result.found_nearby=true;
            const double dx=item.position[0]-pose.position[0],dz=item.position[2]-pose.position[2],radius=8.+item.radius;
            if(dx*dx+dz*dz>radius*radius||item.catalog||std::abs(speed)<1.)continue;
            if(item.tree){if(!tree_hits.count(index))continue;}else{
                const double fwd=dx*sy+dz*cy,lat=dx*cy-dz*sy;
                if(!(pose.minimum[0]<=lat&&lat<=pose.maximum[0]&&pose.minimum[2]<=fwd&&fwd<=pose.maximum[2]))continue;
            }
            result.items.push_back(index);
        }});};
    visit(chunk.origins,origin);visit(chunk.extended,bounds(sweep));return result;
}
std::vector<Key> Store::missing(const Box &sweep) const {
    std::set<Key> result;cells(bounds(sweep),[&](Cell c){auto at=baked_.find(c);if(at!=baked_.end())result.insert(at->second.begin(),at->second.end());});
    return {result.begin(),result.end()};
}
std::int64_t open(){std::lock_guard<std::mutex> lock(stores_mutex);const auto id=++next_handle;stores[id]=std::make_shared<Store>();return id;}
void close(std::int64_t id){std::lock_guard<std::mutex> lock(stores_mutex);stores.erase(id);}
std::shared_ptr<Store> find(std::int64_t id){std::lock_guard<std::mutex> lock(stores_mutex);auto at=stores.find(id);return at==stores.end()?nullptr:at->second;}
}
