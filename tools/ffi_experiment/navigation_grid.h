#ifndef OFFLINE_EXPERIMENT_NAVIGATION_GRID_H
#define OFFLINE_EXPERIMENT_NAVIGATION_GRID_H

#include "navigation_graph.h"
#include <algorithm>
#include <array>
#include <deque>
#include <map>
#include <set>
#include <stdexcept>
#include <tuple>

namespace offline_nav {
const double pi = 3.14159265358979323846;
struct Point {
    double x, y, z;
    Point(double a=0, double b=0, double c=0):x(a),y(b),z(c){}
    bool operator==(const Point &v) const { return x==v.x && y==v.y && z==v.z; }
    bool operator!=(const Point &v) const { return !(*this==v); }
};
using Cell = std::pair<int,int>;
using Edge = std::pair<Cell,Cell>;
using Path = std::vector<Point>;
using Penalties = std::map<Edge,double>;
using TimedPenalties = std::map<Edge,std::pair<double,double> >;
inline double distance(Point a,Point b) {
    double x=a.x-b.x,z=a.z-b.z;return std::sqrt(x*x+z*z);
}
inline double wrap(double v) {
    while(v>pi)v-=2*pi;
    while(v<-pi)v+=2*pi;
    return v;
}
inline Edge edge(Cell a,Cell b) {return a<b?Edge(a,b):Edge(b,a);}
inline std::vector<Cell> line(Cell start,Cell end) {
    std::vector<Cell> cells(1,start);
    int x=start.first,z=start.second,dx=std::abs(end.first-x),dz=std::abs(end.second-z);
    int sx=x<end.first?1:-1,sz=z<end.second?1:-1,error=dx-dz;
    while(x!=end.first||z!=end.second){
        int twice=error*2;
        if(twice>-dz){error-=dz;x+=sx;}
        if(twice<dx){error+=dx;z+=sz;}
        cells.push_back(Cell(x,z));
    }
    return cells;
}
inline bool climb(const Path &path,int first,int last,double grade=0.10,double turn_limit=0.30) {
    if(last-first<2)return true;
    for(int i=first+1;i<last;++i){
        Point before=path[i-1],pivot=path[i],after=path[i+1];
        double ox=after.x-pivot.x,oz=after.z-pivot.z,run=std::sqrt(ox*ox+oz*oz);
        if(run<=0.1||(after.y-pivot.y)/run<=grade)continue;
        double ix=pivot.x-before.x,iz=pivot.z-before.z;
        if(std::abs(ix)+std::abs(iz)<=0.1)continue;
        if(std::abs(wrap(std::atan2(ox,oz)-std::atan2(ix,iz)))>turn_limit)return false;
    }
    return true;
}
inline bool live_climb(Point current,const Path &path,int first,int last) {
    if(last<first)return true;
    Path live(1,current);live.insert(live.end(),path.begin()+first,path.begin()+last+1);
    return climb(live,0,static_cast<int>(live.size())-1);
}

struct Grid {
    std::shared_ptr<OfflineNavGraph> data;
    bool bounded=false;
    std::array<double,4> bounds;
    TimedPenalties failed;
    Penalties hulls;
    int hull_revision=0;
    // A corridor's hazard value -1 is the source's None sentinel. The start
    // cell is excluded from hazard accumulation so a vehicle can leave water.
    std::map<std::pair<Cell,Cell>,std::pair<bool,int> > corridors;
    std::deque<std::pair<Cell,Cell> > corridor_order;
    std::map<Cell,std::pair<double,double> > local_corridors;
    explicit Grid(std::shared_ptr<OfflineNavGraph> graph):data(graph){}
    Cell cell(Point p) const {
        double x=std::floor((p.x-data->ox)/data->cell+0.5);
        double z=std::floor((p.z-data->oz)/data->cell+0.5);
        if(!std::isfinite(x)||!std::isfinite(z)||std::abs(x)>1000000||std::abs(z)>1000000)
            throw std::invalid_argument("navigation coordinate");
        return Cell(static_cast<int>(x),static_cast<int>(z));
    }
    int index(Cell c,bool height=true) const {
        if(c.first<0||c.second<0||c.first>=data->width||c.second>=data->height)return -1;
        int i=c.second*data->width+c.first;
        return height&&!data->valid(i)?-1:i;
    }
    bool inside(Point p) const {
        return !bounded||(bounds[0]<=p.x&&p.x<=bounds[2]&&bounds[1]<=p.z&&p.z<=bounds[3]);
    }
    Point point(Cell c,double y) const {return Point(data->ox+c.first*data->cell,y,data->oz+c.second*data->cell);}
    bool nearest(Cell c,int radius,Cell &result) const {
        if(index(c)>=0){result=c;return true;}
        for(int r=1;r<=radius;++r){
            bool found=false;double best=0;
            for(int z=c.second-r;z<=c.second+r;++z)for(int x=c.first-r;x<=c.first+r;++x){
                if(std::max(std::abs(x-c.first),std::abs(z-c.second))!=r||index(Cell(x,z))<0)continue;
                double dx=x-c.first,dz=z-c.second,d=dx*dx+dz*dz;
                if(!found||d<best){result=Cell(x,z);found=true;best=d;}
            }
            if(found)return true;
        }
        return false;
    }
    bool ground(Point p,double &height) const {
        int i=index(cell(p));if(!inside(p)||i<0)return false;
        height=data->heights[i]/1000.0;return true;
    }
    std::vector<Cell> segment_cells(Point start,Point end,bool height=true) const {
        Cell a=cell(start),b=cell(end);
        if(height&&!nearest(a,2,a))return {};
        if(index(a,height)<0||index(b,height)<0)return {};
        return line(a,b);
    }
    std::vector<Edge> edges(Point a,Point b) const {
        auto cells=line(cell(a),cell(b));std::vector<Edge> out;
        for(size_t i=1;i<cells.size();++i)out.push_back(edge(cells[i-1],cells[i]));
        return out;
    }
    bool linked(Cell a,Cell b) const {
        int ia=index(a),ib=index(b);if(ia<0||ib<0)return false;
        static const int xs[]={-1,0,1,-1,1,-1,0,1},zs[]={-1,-1,-1,0,0,1,1,1};
        for(int i=0;i<8;++i)if(b.first-a.first==xs[i]&&b.second-a.second==zs[i])
            return (data->links[ia]&(1<<i))!=0;
        return false;
    }
    int link_count(Cell c) const {
        int i=index(c),count=0;if(i<0)return 0;
        for(int bit=0;bit<8;++bit)count+=!!(data->links[i]&(1<<bit));
        return count;
    }
    std::pair<bool,int> corridor(Point start,Point end) {
        auto key=std::make_pair(cell(start),cell(end));auto cached=corridors.find(key);
        if(cached!=corridors.end())return cached->second;
        auto cells=segment_cells(start,end);bool clear=!cells.empty();int hazards=clear?0:-1;
        for(size_t i=1;i<cells.size();++i){
            int idx=index(cells[i]);if(idx<0){clear=false;hazards=-1;break;}
            hazards|=data->hazards[idx];
            if(clear&&!linked(cells[i-1],cells[i]))clear=false;
        }
        if(corridors.size()>=2048){corridors.erase(corridor_order.front());corridor_order.pop_front();}
        auto answer=std::make_pair(clear,hazards);corridors[key]=answer;corridor_order.push_back(key);return answer;
    }
    bool hazard(Point a,Point b,int mask) {
        int bits=corridor(a,b).second;return bits<0||(bits&mask)!=0;
    }
    bool motion_hazard(Point a,Point b,int mask) const {
        auto cells=segment_cells(a,b,false);if(cells.empty())return true;
        for(size_t j=1;j<cells.size();++j){int i=index(cells[j],false);if(i<0||(data->hazards[i]&mask))return true;}
        return false;
    }
    bool hazard_cells(Point a,Point b,int mask,std::vector<Cell> &result) const {
        if(index(cell(a))<0)return false;
        auto cells=segment_cells(a,b);if(cells.empty())return false;
        for(size_t j=1;j<cells.size();++j){
            int i=index(cells[j]);if(i<0)return false;
            if(data->hazards[i]&mask)result.push_back(cells[j]);
        }
        return true;
    }
    bool point_hazard(Point p,int mask) const {
        int i=index(cell(p),false);return i>=0&&(data->hazards[i]&mask);
    }
    bool hazard_near(Point p,int radius) const {
        Cell c=cell(p);
        for(int z=c.second-radius;z<=c.second+radius;++z)for(int x=c.first-radius;x<=c.first+radius;++x){
            int i=index(Cell(x,z),false);if(i>=0&&(data->hazards[i]&3))return true;
        }
        return false;
    }
    bool segment_clear(Point a,Point b) {
        if(!inside(b))return false;
        return distance(a,b)<0.25||corridor(a,b).first;
    }
    double timed(Edge e,double now) {
        auto it=failed.find(e);if(it==failed.end())return 0;
        if(now>=it->second.first){failed.erase(it);return 0;}
        return it->second.second;
    }
    double segment_penalty(Point a,Point b,double now) {
        if(failed.empty()&&hulls.empty())return 0;
        double value=0;
        for(Edge e:edges(a,b)){auto h=hulls.find(e);value=std::max(value,std::max(h==hulls.end()?0.0:h->second,timed(e,now)));}
        return value;
    }
    bool dry(Point a,Point b,double now) {
        return segment_penalty(a,b,now)<=0&&!hazard(a,b,4)&&segment_clear(a,b);
    }
    bool path_timed(const Path &path,double now) {
        if(failed.empty())return false;
        for(size_t i=1;i<path.size();++i)for(Edge e:edges(path[i-1],path[i]))if(timed(e,now)>0)return true;
        return false;
    }
    bool path_penalty(const Path &path,const Penalties &penalties) const {
        if(penalties.empty())return false;
        for(size_t i=1;i<path.size();++i)for(Edge e:edges(path[i-1],path[i]))if(penalties.count(e))return true;
        return false;
    }
    bool exposure(const Path &path,double &value) const {
        if(path.empty()){value=0;return true;}
        std::vector<Cell> cells;
        if(path.size()==1){Cell c;if(!nearest(cell(path[0]),2,c))return false;cells.push_back(c);}
        else for(size_t i=1;i<path.size();++i){
            auto segment=segment_cells(path[i-1],path[i]);if(segment.empty())return false;
            size_t begin=!cells.empty()&&cells.back()==segment.front()?1:0;
            cells.insert(cells.end(),segment.begin()+begin,segment.end());
        }
        if(cells.empty())return false;
        int missing=0;for(Cell c:cells)missing+=8-link_count(c);
        value=static_cast<double>(missing)/cells.size();return true;
    }
    bool clearance(const Path &path,int first,int last,double maximum=0.25) const {
        if(last-first<2)return true;
        double original,shortcut;
        return exposure(Path(path.begin()+first,path.begin()+last+1),original)&&
            exposure(Path{path[first],path[last]},shortcut)&&shortcut<=original+maximum;
    }
    double penalty(Cell c,const Path &avoid,bool prefer=false) const {
        double value=prefer?static_cast<double>(8-link_count(c))*data->cell*data->clearance:0;
        int i=index(c);if(i>=0&&(data->hazards[i]&4))value+=data->cell*data->shallow;
        Point p=point(c,0);
        for(Point other:avoid){double d=distance(p,other);if(d<data->cell*1.5)value+=(data->cell*1.5-d)*3.0;}
        return value;
    }
    bool safe_local(Point current,Point goal,double now,const Path &avoid,double side,
                    const Penalties &hard,double minimum,Point &result) {
        double dx=goal.x-current.x,dz=goal.z-current.z;
        if(std::abs(dx)+std::abs(dz)<0.1)return false;
        double desired=std::atan2(dx,dz);side=side>=0?1:-1;
        double offsets[]={0,side*0.45,-side*0.45,side*0.85,-side*0.85,side*1.30,-side*1.30,side*1.75,-side*1.75};
        double distances[]={data->cell*0.78,data->cell*0.52};
        bool found=false;std::pair<double,double> best;
        for(double d:distances)for(double offset:offsets){
            if(std::abs(offset)<std::max(0.0,minimum))continue;
            Point p(current.x+std::sin(desired+offset)*d,current.y,current.z+std::cos(desired+offset)*d);
            if(!ground(p,p.y)||!dry(current,p,now)||path_penalty(Path{current,p},hard))continue;
            double score=distance(p,goal)+std::abs(offset)*3.5+penalty(cell(p),avoid)*2.0;
            auto rank=std::make_pair(score,std::abs(offset));
            if(!found||rank<best){found=true;best=rank;result=p;}
        }
        return found;
    }
    Path smooth(const Path &path,double now,bool prefer,const Penalties &hard) {
        if(path.size()<3)return path;
        Path out(1,path[0]);int i=0,last=static_cast<int>(path.size())-1;
        while(i<last){
            int furthest=std::min(last,i+6);
            while(furthest>i+1){
                if((!prefer||clearance(path,i,furthest))&&climb(path,i,furthest)&&
                        !path_penalty(Path{path[i],path[furthest]},hard)&&dry(path[i],path[furthest],now))break;
                --furthest;
            }
            out.push_back(path[furthest]);i=furthest;
        }
        return out;
    }
    bool pose(Point p,double yaw,double length,double width) const {
        length=std::max(0.5,length);width=std::max(0.3,width);
        double s=std::sin(yaw),c=std::cos(yaw),along[]={-length,0,length},across[]={-width,0,width};
        for(double a:along)for(double b:across)if(index(cell(Point(p.x+s*a+c*b,0,p.z+c*a-s*b)))<0)return false;
        return true;
    }
    bool local_corridor(Point p,std::pair<double,double> &result) {
        Cell base=cell(p);if(index(base)<0)return false;
        auto cached=local_corridors.find(base);if(cached!=local_corridors.end()){result=cached->second;return true;}
        const int xs[]={1,1,0,-1},zs[]={0,1,1,1};double spans[4];int longest=0;
        for(int axis=0;axis<4;++axis){
            int cells=1;
            for(int sign:{1,-1})for(int reach=1;reach<=5;++reach){
                if(index(Cell(base.first+xs[axis]*reach*sign,base.second+zs[axis]*reach*sign))<0)break;
                ++cells;
            }
            spans[axis]=cells*(std::hypot(xs[axis],zs[axis])*data->cell);
            if(spans[axis]>spans[longest])longest=axis;
        }
        result=std::make_pair(std::atan2(static_cast<double>(xs[longest]),static_cast<double>(zs[longest])),spans[(longest+2)%4]);
        local_corridors[base]=result;return true;
    }
    bool search_edge(Edge e,uint64_t &key) const {
        int a=index(e.first,false),b=index(e.second,false);
        if(a<0||b<0)return false;
        if(a>b)std::swap(a,b);
        key=(static_cast<uint64_t>(a)<<32)|static_cast<uint32_t>(b);return true;
    }
    void search_globals(){
        data->failed.clear();data->hulls.clear();data->expired.clear();uint64_t key;
        for(auto item:failed)if(search_edge(item.first,key))data->failed[key]=item.second;
        for(auto item:hulls)if(search_edge(item.first,key))data->hulls[key]=item.second;
    }
    void drain_search_expiry(){
        for(uint64_t key:data->expired){
            int a=static_cast<int>(key>>32),b=static_cast<uint32_t>(key);
            failed.erase(edge(Cell(a%data->width,a/data->width),Cell(b%data->width,b/data->width)));
        }
        data->expired.clear();
    }
};
}
#endif
