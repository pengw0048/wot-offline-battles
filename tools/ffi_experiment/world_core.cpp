// Complete horizontal sweep calculations. Engine/destruction leaves yield
// ordered numeric requests; recorded answers also support a bulk CPU estimate.
#include "world_core.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <stdexcept>
#include <vector>

namespace {
const int WIDTH=16;
struct Reader {
    double *b; int n,i;
    Reader(double *p,int size):b(p),n(size),i(1){}
    double next(){if(i>=n||!std::isfinite(b[i]))throw std::invalid_argument("world buffer");return b[i++];}
    int integer(){double v=next();if(v!=std::floor(v)||std::abs(v)>1000000000)throw std::invalid_argument("world integer");return static_cast<int>(v);}
    void end(){if(i!=n)throw std::invalid_argument("world width");}
};
struct V {double x,y,z;V(double a=0,double b=0,double c=0):x(a),y(b),z(c){}};
V vec(Reader &r){double x=r.next(),y=r.next(),z=r.next();return V(x,y,z);}
double distance(V a,V b){double x=a.x-b.x,y=a.y-b.y,z=a.z-b.z;return std::sqrt(x*x+y*y+z*z);}
struct Optional {bool has;double value;Optional():has(false),value(0){} Optional(double v):has(true),value(v){}};
struct Plane {double x,y,z,gx,gz;};
struct Answer {int status,id;V point,normal;};
struct Query {int kind,id;V a,b;};
struct Input {V pos;double yaw,speed,hw,back,front,dt,motion,pitch,roll;bool airborne,has_motion;};
struct Lane {double x1,z1,x2,z2,length,px,pz,ps,pc,direction,look;};
struct Hit {double length;V a,b;Answer hit;};
struct Job {
    Input in;std::vector<Answer> answers;std::vector<Query> expected;
    size_t cursor;bool validate;
    Job():cursor(0),validate(false){}
    Answer query(int kind,V a,V b,int id=0){
        Query q={kind,id,a,b};
        if(cursor==answers.size())throw q;
        if(validate){
            const Query &e=expected[cursor];
            if(e.kind!=kind||e.id!=id||e.a.x!=a.x||e.a.y!=a.y||e.a.z!=a.z||e.b.x!=b.x||e.b.y!=b.y||e.b.z!=b.z)
                throw std::invalid_argument("world query geometry/order differs");
        }
        return answers[cursor++];
    }
};
std::map<int,Job> jobs,traces;int next_job=1,next_trace=1;
Input input(Reader &r){
    Input v;v.pos=vec(r);v.yaw=r.next();v.speed=r.next();v.hw=r.next();v.back=r.next();v.front=r.next();
    v.airborne=r.integer()!=0;v.dt=r.next();v.has_motion=r.integer()!=0;v.motion=r.next();v.pitch=r.next();v.roll=r.next();return v;
}
Answer answer(Reader &r){Answer a;a.status=r.integer();a.point=vec(r);a.normal=vec(r);a.id=r.integer();return a;}
V pose_y(const Input &v){if(v.pitch==0&&v.roll==0)return V(0,1,0);double c=std::cos(v.pitch);return V(c*std::sin(v.roll),c*std::cos(v.roll),-std::sin(v.pitch));}
V endpoint(V a,V b,const Input &in){
    double fraction=1,dr=b.x-a.x,df=b.z-a.z;
    if(dr>0)fraction=std::min(fraction,(in.hw-a.x)/dr);else if(dr<0)fraction=std::min(fraction,(-in.hw-a.x)/dr);
    if(df>0)fraction=std::min(fraction,(in.front-a.z)/df);else if(df<0)fraction=std::min(fraction,(-in.back-a.z)/df);
    fraction=std::max(0.0,std::min(1.0,fraction));return V(a.x+dr*fraction,0,a.z+df*fraction);
}
Optional ground(Job &j,double x,double z,double look,Plane p){
    double down=std::max(5.0,look*1.75+1.0),sy=std::min(j.in.pos.y+12.0,p.y+(x-p.x)*p.gx+(z-p.z)*p.gz);
    if(sy<=j.in.pos.y-down)return Optional();
    Answer a=j.query(2,V(x,sy,z),V(x,j.in.pos.y-down,z));return a.status?Optional(a.point.y):Optional();
}
Optional ahead(Job &j,const Lane &l,V local_start,V local_end,V py,double c,double s,Plane p){
    double fx=j.in.pos.x+c*local_end.x+s*local_end.z,fz=j.in.pos.z-s*local_end.x+c*local_end.z;
    Optional a=ground(j,l.x1,l.z1,l.length,p),b=ground(j,fx,fz,l.length,p);
    bool descending=(local_end.x-local_start.x)*py.x+(local_end.z-local_start.z)*py.z<-1e-9;
    if(descending&&a.has&&b.has){
        double support=j.in.pos.y+local_start.x*py.x+local_start.z*py.z;
        if(a.value<support-1e-3)return Optional();
        Optional middle=ground(j,(l.x1+fx)*0.5,(l.z1+fz)*0.5,l.length,p);
        if(!middle.has||middle.value>(a.value+b.value)*0.5+1e-3)return Optional();
    }
    double ix=fx-l.x1,iz=fz-l.z1,dx=l.x2-l.x1,dz=l.z2-l.z1;
    double inside=std::sqrt(std::pow(ix,2)+std::pow(iz,2)),full=std::sqrt(std::pow(dx,2)+std::pow(dz,2));
    if(a.has&&b.has&&inside>1e-6)return Optional(a.value+(b.value-a.value)*full/inside);
    if(a.has&&b.has)return Optional(std::min(a.value,b.value));
    return a.has?a:b;
}
std::pair<V,V> ray(const Input &in,const Lane &l,V a,V b,V py,double height,Optional top){
    double sy=in.pos.y+a.x*py.x+height*py.y+a.z*py.z,ey=in.pos.y+b.x*py.x+height*py.y+b.z*py.z;
    if(top.has)ey=std::min(ey,top.value+height);
    return std::make_pair(V(l.x1,sy,l.z1),V(l.x2,ey,l.z2));
}
bool surface(const Answer &a,double gradient){
    double length=std::pow(a.normal.x*a.normal.x+a.normal.y*a.normal.y+a.normal.z*a.normal.z,0.5);
    return length>0&&a.normal.y/length>=1.0/std::pow(1.0+std::pow(gradient,2),0.5);
}
bool drivable(const std::vector<double> &h,double segment){
    if(h.size()<2||std::abs(h.back()-h.front())<=0.15)return false;
    segment=std::max(0.001,segment);
    for(size_t i=1;i<h.size();++i){double delta=h[i]-h[i-1];if(std::abs(delta)>segment*(delta<0?1.75:1.28))return false;}
    return true;
}
bool matches(const Answer &a,const std::vector<double> &h,double segment,const Lane &l){
    if(h.size()<2||segment<=0)return false;
    double d=l.direction*((a.point.x-l.px)*l.ps+(a.point.z-l.pz)*l.pc);
    if(d<0||d>segment*(h.size()-1))return false;
    size_t index=std::min(h.size()-2,static_cast<size_t>(d/segment));double f=(d-index*segment)/segment;
    return std::abs(a.point.y-(h[index]+(h[index+1]-h[index])*f))<=0.15;
}
bool exact(Job &j,const Answer &a,const Lane &l,Plane p){Optional top=ground(j,a.point.x,a.point.z,l.look,p);return top.has&&std::abs(top.value-a.point.y)<=1e-3;}
std::vector<Lane> lanes(const Input &in,double c,double s){
    std::vector<Lane> out;double ahead=std::abs(in.speed)*in.dt+0.2;if(!in.airborne)ahead=std::max(0.4,ahead);
    if(!in.has_motion){
        double back=in.speed>0?-0.5:0.5,front=in.speed>0?in.front+ahead:-(in.back+ahead);
        double direction=in.speed>=0?1.0:-1.0,look=(in.speed>0?in.front:in.back)+ahead;
        for(double offset:std::array<double,3>{{-in.hw,0,in.hw}}){
            double sx=in.pos.x+c*offset,sz=in.pos.z-s*offset;
            out.push_back(Lane{sx+s*back,sz+c*back,sx+s*front,sz+c*front,std::abs(back)+look,sx,sz,s,c,direction,look});
        }
    }else{
        double ms=std::sin(in.motion),mc=std::cos(in.motion),px=mc,pz=-ms;
        double ru=ms*c-mc*s,fu=ms*s+mc*c,rv=px*c-pz*s,fv=px*s+pz*c;
        std::vector<std::array<double,3> > projected,merged;
        for(auto corner:std::array<std::pair<double,double>,4>{{{-in.hw,-in.back},{in.hw,-in.back},{in.hw,in.front},{-in.hw,in.front}}}){
            double u=ru*corner.first+fu*corner.second,v=rv*corner.first+fv*corner.second;projected.push_back({{v,u,u+ahead}});
        }
        std::vector<double> limits;
        if(ru>1e-9)limits.push_back(in.hw/ru);else if(ru<-1e-9)limits.push_back(-in.hw/ru);
        if(fu>1e-9)limits.push_back(in.front/fu);else if(fu<-1e-9)limits.push_back(-in.back/fu);
        double front=limits.empty()?0:*std::min_element(limits.begin(),limits.end());projected.push_back({{0,-0.5,front+ahead}});
        std::sort(projected.begin(),projected.end());
        for(auto item:projected){
            if(!merged.empty()&&std::abs(item[0]-merged.back()[0])<=1e-7){merged.back()[1]=std::min(merged.back()[1],item[1]);merged.back()[2]=std::max(merged.back()[2],item[2]);}
            else merged.push_back(item);
        }
        for(auto p:merged){
            double x1=in.pos.x+px*p[0]+ms*p[1],z1=in.pos.z+pz*p[0]+mc*p[1];
            double x2=in.pos.x+px*p[0]+ms*p[2],z2=in.pos.z+pz*p[0]+mc*p[2],length=p[2]-p[1];
            out.push_back(Lane{x1,z1,x2,z2,length,x1,z1,ms,mc,1,length});
        }
    }
    return out;
}
int sweep(Job &j){
    const Input &in=j.in;double c=std::cos(in.yaw),s=std::sin(in.yaw);V py=pose_y(in);
    Plane plane={in.pos.x,in.pos.y+1.6*py.y,in.pos.z,c*py.x+s*py.z,-s*py.x+c*py.z};
    std::vector<Lane> all=lanes(in,c,s);double minx=all[0].x1,maxx=minx,minz=all[0].z1,maxz=minz;
    for(const Lane &l:all){minx=std::min(minx,std::min(l.x1,l.x2));maxx=std::max(maxx,std::max(l.x1,l.x2));minz=std::min(minz,std::min(l.z1,l.z2));maxz=std::max(maxz,std::max(l.z1,l.z2));}
    j.query(1,V(minx,in.pos.y+0.6,minz),V(maxx,in.pos.y+1.6,maxz));bool kinetic=false;
    for(Lane l:all){
        double dx=l.x1-in.pos.x,dz=l.z1-in.pos.z,ex=l.x2-in.pos.x,ez=l.z2-in.pos.z;
        V a(dx*c-dz*s,0,dx*s+dz*c),raw(ex*c-ez*s,0,ex*s+ez*c),b=endpoint(a,raw,in);
        bool clamped=(py.x!=0||py.y!=1||py.z!=0)&&(std::abs(b.x-raw.x)>1e-9||std::abs(b.z-raw.z)>1e-9);
        Optional top=py.z!=0?ahead(j,l,a,b,py,c,s,plane):Optional();
        auto lower=ray(in,l,a,b,py,0.6,top);Answer hit=j.query(3,lower.first,lower.second);
        double target=distance(lower.second,lower.first),d=hit.status?distance(hit.point,lower.first):0;
        std::vector<Hit> hits;
        if(hit.status&&d<target){
            std::vector<double> heights;double segment=0,gradient=1.75;Plane p=plane;
            if(surface(hit,gradient))p=Plane{hit.point.x,hit.point.y+1.6,hit.point.z,-hit.normal.x/hit.normal.y,-hit.normal.z/hit.normal.y};
            if(surface(hit,gradient)||clamped){
                segment=l.look/6.0;
                for(int i=0;i<=6;++i){double offset=segment*i;Optional g=ground(j,l.px+l.ps*offset*l.direction,l.pz+l.pc*offset*l.direction,l.look,p);if(!g.has){heights.clear();break;}heights.push_back(g.value);}
                gradient=!heights.empty()&&heights.back()<heights.front()?1.75:1.28;
                if(!heights.empty()&&std::abs(heights.back()-heights.front())>0.15&&!drivable(heights,segment))return 0;
            }
            bool is_ground=surface(hit,gradient);
            if(!is_ground&&clamped&&matches(hit,heights,segment,l))is_ground=exact(j,hit,l,p);
            if(!heights.empty()&&drivable(heights,segment)&&is_ground){
                for(double h:std::array<double,2>{{1.1,1.6}}){
                    auto r=ray(in,l,a,b,py,h,top);Answer upper=j.query(3,r.first,r.second);
                    if(!upper.status||distance(upper.point,r.first)>=target||surface(upper,gradient))continue;
                    if(matches(upper,heights,segment,l)&&exact(j,upper,l,p))continue;
                    return 0;
                }
                continue;
            }
            hits.push_back(Hit{d,lower.first,lower.second,hit});
        }
        for(double h:std::array<double,2>{{1.1,1.6}}){
            auto r=ray(in,l,a,b,py,h,top);Answer upper=j.query(3,r.first,r.second);
            if(!upper.status)continue;
            double length=distance(upper.point,r.first);if(length<target)hits.push_back(Hit{length,r.first,r.second,upper});
        }
        std::stable_sort(hits.begin(),hits.end(),[](const Hit &a,const Hit &b){return a.length<b.length;});
        for(const Hit &h:hits){Answer resolved=j.query(4,h.a,h.b,h.hit.id);if(resolved.status==2)kinetic=true;else if(resolved.status!=1)return 0;}
    }
    return kinetic?2:1;
}
void run(Job &j,double *b,int out){
    j.cursor=0;
    try{int status=sweep(j);if(j.cursor!=j.answers.size())throw std::invalid_argument("unused world answers");b[out]=0;b[out+1]=status;b[out+2]=j.cursor;}
    catch(const Query &q){b[out]=q.kind;b[out+1]=q.a.x;b[out+2]=q.a.y;b[out+3]=q.a.z;b[out+4]=q.b.x;b[out+5]=q.b.y;b[out+6]=q.b.z;b[out+7]=q.id;}
}
}
void offline_world_reset(){jobs.clear();traces.clear();}
int offline_world_dispatch(double *b,int n){
    Reader r(b,n);int op=static_cast<int>(b[0]);
    if(op==400){Job j;j.in=input(r);int out=r.i;if(n!=out+WIDTH)throw std::invalid_argument("world start width");int id=next_job++;jobs[id]=j;run(jobs[id],b,out);b[out+15]=id;return 0;}
    if(op==401){int id=r.integer();auto it=jobs.find(id);if(it==jobs.end())throw std::invalid_argument("world owner");Answer a=answer(r);int out=r.i;if(n!=out+WIDTH)throw std::invalid_argument("world resume width");it->second.answers.push_back(a);run(it->second,b,out);return 0;}
    if(op==402){int id=r.integer();r.end();jobs.erase(id);return 0;}
    if(op==403){
        Job j;j.in=input(r);int count=r.integer();if(count<0||count>10000)throw std::invalid_argument("world tape length");
        for(int i=0;i<count;++i){Query q;q.kind=r.integer();q.a=vec(r);q.b=vec(r);q.id=r.integer();j.expected.push_back(q);j.answers.push_back(answer(r));}
        int expected=r.integer();r.end();j.validate=true;j.cursor=0;int actual=sweep(j);
        if(actual!=expected||j.cursor!=j.answers.size())throw std::invalid_argument("world trace result");
        int id=next_trace++;traces[id]=j;b[0]=id;return 0;
    }
    if(op==404){
        int loops=r.integer();r.end();if(loops<1||loops>10000)throw std::invalid_argument("world loops");
        long long total=0;for(int i=0;i<loops;++i)for(auto &entry:traces){Job &j=entry.second;j.cursor=0;total+=sweep(j);if(j.cursor!=j.answers.size())throw std::invalid_argument("world trace completion");}
        b[0]=total;b[1]=traces.size();return 0;
    }
    throw std::invalid_argument("world opcode");
}
