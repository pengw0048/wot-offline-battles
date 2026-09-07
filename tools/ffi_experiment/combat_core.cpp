// Experimental numeric Bot aiming owner; no Python or engine objects.
#include "combat_core.h"
#include <algorithm>
#include <cmath>
#include <map>
#include <stdexcept>
#include <vector>

namespace {
const double PI = 3.14159265358979323846;
struct Reader {
    double *b; int n, i;
    Reader(double *p, int count) : b(p), n(count), i(1) {}
    double next() {
        if (i >= n || !std::isfinite(b[i])) throw std::invalid_argument("combat buffer");
        return b[i++];
    }
    int integer() {
        double v = next();
        if (v < -1000000 || v > 1000000 || v != std::floor(v))
            throw std::invalid_argument("combat integer");
        return static_cast<int>(v);
    }
    void end() { if (i != n) throw std::invalid_argument("combat width"); }
};
double clamp(double v, double low, double high) { return std::max(low, std::min(high, v)); }
double wrap(double v) {
    while (v > PI) v -= 2.0 * PI;
    while (v < -PI) v += 2.0 * PI;
    return v;
}
struct Vec {
    double x, y, z;
    Vec(double a=0, double b=0, double c=0) : x(a), y(b), z(c) {}
};
Vec vector(Reader &r) { double x=r.next(), y=r.next(), z=r.next(); return Vec(x,y,z); }
Vec rx(Vec v, double a) { double s=std::sin(a),c=std::cos(a); return Vec(v.x,c*v.y-s*v.z,s*v.y+c*v.z); }
Vec ry(Vec v, double a) { double s=std::sin(a),c=std::cos(a); return Vec(c*v.x+s*v.z,v.y,-s*v.x+c*v.z); }
Vec rz(Vec v, double a) { double s=std::sin(a),c=std::cos(a); return Vec(c*v.x-s*v.y,s*v.x+c*v.y,v.z); }
Vec barrel(double yaw,double pitch) { double h=std::cos(pitch); return Vec(std::sin(yaw)*h,-std::sin(pitch),std::cos(yaw)*h); }
typedef std::pair<double,double> Pair;
Pair local_angles(Vec d,double yaw,double pitch,double roll) {
    Vec v=rz(rx(ry(d,-yaw),-pitch),-roll);
    double length=std::sqrt((0.0+v.x*v.x)+v.y*v.y+v.z*v.z);
    if(length<=1e-12) throw std::invalid_argument("zero direction");
    v.x/=length; v.y/=length; v.z/=length;
    return Pair(std::atan2(v.x,v.z),-std::atan2(v.y,std::max(1e-12,std::sqrt(v.x*v.x+v.z*v.z))));
}
std::vector<Pair> roots(Vec s,Vec t,double speed,double gravity,double minimum,double maximum) {
    std::vector<Pair> result;
    double g=std::abs(gravity);
    if(speed<=1.0 || g<=0.01) return result;
    double dx=t.x-s.x,dz=t.z-s.z,h=std::sqrt(dx*dx+dz*dz);
    if(h<=0.1) return result;
    double dy=t.y-s.y, ss=speed*speed;
    double disc=ss*ss-g*(g*h*h+2.0*dy*ss);
    if(disc<0.0) return result;
    double root=std::sqrt(std::max(0.0,disc));
    double ns[]={ss-root,ss+root};
    for(double numerator:ns) {
        double elevation=std::atan(numerator/(g*h)),pitch=-elevation;
        if(pitch<minimum-0.0001 || pitch>maximum+0.0001) continue;
        double hs=speed*std::cos(elevation);
        if(hs<=0.01) continue;
        double time=h/hs;
        if(time<=0.0 || time>20.0) continue;
        if(result.empty() || std::abs(pitch-result.back().first)>0.00001) result.push_back(Pair(pitch,time));
    }
    std::stable_sort(result.begin(),result.end(),[](Pair a,Pair b){return a.second<b.second;});
    return result;
}
bool intercept(Vec s,Vec t,Vec velocity,double speed,double gravity,double minimum,double maximum,
               bool high,double max_time,Vec &aim,Pair &solution) {
    aim=t; max_time=std::max(0.0,max_time);
    for(int i=0;i<5;++i) {
        std::vector<Pair> values=roots(s,aim,speed,gravity,minimum,maximum);
        if(values.empty()) return false;
        solution=high?values.back():values.front();
        if(max_time && solution.second>max_time+1e-9) return false;
        if(i<4) aim=Vec(t.x+velocity.x*solution.second,t.y+velocity.y*solution.second,t.z+velocity.z*solution.second);
    }
    return true;
}
float f32(double v) {
    float f=static_cast<float>(v);
    if(!std::isfinite(f)) throw std::invalid_argument("float32 overflow");
    return f;
}
float add(float a,float b) {return f32(static_cast<double>(a)+b);}
float sub(float a,float b) {return f32(static_cast<double>(a)-b);}
float mul(float a,float b) {return f32(static_cast<double>(a)*b);}
float divide(float a,float b) {
    if(b==0) throw std::invalid_argument("duplicate curve node");
    return f32(static_cast<double>(a)/b);
}
struct Profile {
    int pitch_kind;
    Pair pitch, yaw;
    std::vector<Pair> low,high;
    bool limited, has_static_pitch,has_static_yaw,has_hydraulic,available,enabled;
    double static_pitch,static_yaw,minimum,maximum,speed;
};
std::map<int,Profile> profiles;
int next_profile=1;
float sample(float yaw,const std::vector<Pair> &points) {
    size_t low=0,high=points.size()-1;
    while(high-low>1) {
        size_t middle=(low+high)/2;
        if(yaw>points[middle].first) low=middle; else high=middle;
    }
    float span=sub(points[high].first,points[low].first);
    float fraction=divide(sub(yaw,points[low].first),span);
    return add(mul(points[low].second,sub(1.0f,fraction)),mul(points[high].second,fraction));
}
struct Pose {
    double yaw,pitch,roll,terrain,correction,turret,gun,aim;
    bool moving,engine,tracks,overturned; int siege;
};
bool switching(const Pose &s) { return s.siege==1 || s.siege==3; }
bool limits(const Profile &p,const Pose &s,double yaw,Pair &result) {
    if(p.has_static_pitch && (s.engine || s.overturned || switching(s))) {
        result=Pair(p.static_pitch,p.static_pitch); return true;
    }
    if(!p.pitch_kind) return false;
    if(p.pitch_kind==1) {result=p.pitch;return true;}
    float y=f32(yaw); if(y<0.0f) y=add(y,f32(2.0*PI));
    result=Pair(sample(y,p.low),sample(y,p.high)); return true;
}
bool active(const Profile &p,const Pose &s) {return p.has_hydraulic && p.available && p.enabled && s.siege==2;}
int status(const Profile &p,const Pose &s,Vec d,double correction) {
    Pair a=local_angles(d,s.yaw,s.terrain+correction,s.roll),l;
    if(!limits(p,s,a.first,l)) return 2;
    if(l.first>l.second) throw std::invalid_argument("inverted pitch limits");
    return a.second<l.first-1e-8?-1:(a.second>l.second+1e-8?1:0);
}
Pair correction(const Profile &p,const Pose &s,double yaw,double pitch) {
    Vec d=barrel(yaw,pitch);
    double neutral=clamp(0.0,p.minimum,p.maximum);
    int a=status(p,s,d,neutral);
    if(a==2) return Pair(neutral,0);
    if(a==0) return Pair(neutral,1);
    double edge=a<0?p.minimum:p.maximum;
    int b=status(p,s,d,edge);
    if(b==2 || (a<0 && b<0) || (a>0 && b>0)) return Pair(edge,0);
    double blocked=neutral,feasible=edge;
    for(int i=0;i<48;++i) {
        double candidate=(blocked+feasible)*0.5;
        int c=status(p,s,d,candidate);
        if(c==2) return Pair(edge,0);
        if(c==a) blocked=candidate; else feasible=candidate;
    }
    return Pair(feasible,1);
}
bool reachable(const Profile &p,const Pose &s,double yaw,double pitch) {
    if(active(p,s)) return correction(p,s,yaw,pitch).second!=0.0;
    Pair a=local_angles(barrel(yaw,pitch),s.yaw,s.pitch,s.roll),l;
    return limits(p,s,a.first,l) && l.first-0.0001<=a.second && a.second<=l.second+0.0001;
}
double slew(double current,double desired,double speed,double dt) {
    double step=std::max(0.0,speed)*std::max(0.0,dt),diff=desired-current;
    if(diff>step) return current+step;
    if(diff<-step) return current-step;
    return desired;
}
double gun_step(double current,double desired,const Profile &p,double speed,double dt,double rotation,Pair l) {
    dt=std::max(0.0,dt);speed=std::max(0.0,speed);
    if(speed==0.0) return current;
    if(l.first>l.second) throw std::invalid_argument("inverted gun limits");
    double bounded=clamp(desired,l.first,l.second);
    if(std::abs(current-desired)<1e-6) return bounded;
    double diff=bounded-current,step=speed*dt;
    if(p.has_static_pitch) {
        if(diff*(current-p.static_pitch)<0.0) step*=2.0;
        rotation=std::max(0.0,rotation);
        if(rotation>0.0) step=std::min(step,(std::abs(diff)/rotation)*dt);
    }
    if(diff>step) return current+step;
    if(diff<-step) return current-step;
    return bounded;
}
Pose pose(Reader &r) {
    Pose s;
    s.yaw=r.next();s.pitch=r.next();s.roll=r.next();s.terrain=r.next();
    s.correction=r.next();s.turret=r.next();s.gun=r.next();s.aim=r.next();
    s.moving=r.integer()!=0;s.engine=r.integer()!=0;s.tracks=r.integer()!=0;
    s.overturned=r.integer()!=0;s.siege=r.integer();
    return s;
}
Profile &profile(Reader &r) {
    auto it=profiles.find(r.integer());
    if(it==profiles.end()) throw std::invalid_argument("unknown aiming profile");
    return it->second;
}
void read_curve(Reader &r,std::vector<Pair> &v) {
    int count=r.integer();
    if(count<2 || count>10000) throw std::invalid_argument("curve size");
    for(int i=0;i<count;++i) {double x=f32(r.next()),y=f32(r.next());v.push_back(Pair(x,y));}
}
}
void offline_combat_reset() {profiles.clear();}
int offline_combat_dispatch(double *b,int n) {
    Reader r(b,n);int op=static_cast<int>(b[0]);
    if(op==100) {
        Profile p;
        p.pitch_kind=r.integer();p.pitch.first=r.next();p.pitch.second=r.next();
        if(p.pitch_kind<0 || p.pitch_kind>2) throw std::invalid_argument("pitch kind");
        p.yaw.first=r.next();p.yaw.second=r.next();p.limited=r.integer()!=0;
        p.has_static_pitch=r.integer()!=0;p.static_pitch=r.next();
        p.has_static_yaw=r.integer()!=0;p.static_yaw=r.next();
        p.has_hydraulic=r.integer()!=0;p.available=r.integer()!=0;p.enabled=r.integer()!=0;
        p.minimum=r.next();p.maximum=r.next();p.speed=r.next();
        if(p.has_hydraulic && (p.minimum>p.maximum || p.speed<0)) throw std::invalid_argument("hydraulic params");
        if(p.pitch_kind==2) {read_curve(r,p.low);read_curve(r,p.high);}
        r.end();int id=next_profile++;profiles[id]=p;b[0]=id;return 0;
    }
    if(op==101 || op==103) {
        // 101: one complete low-arc + physical gun reach calculation.
        // 103: standalone intercept parity, including high-arc selection.
        Profile *p=nullptr;Pose s={};
        if(op==101) {p=&profile(r);s=pose(r);}
        Vec start=vector(r),target=vector(r),velocity=vector(r);
        double speed=r.next(),gravity=r.next(),minimum=r.next(),maximum=r.next();
        bool high=r.integer()!=0;double max_time=r.next(),max_distance=r.next();
        int out=r.i;if(n!=out+7) throw std::invalid_argument("intercept output width");
        Vec aim;Pair solution;
        bool valid=intercept(start,target,velocity,speed,gravity,minimum,maximum,high,max_time,aim,solution);
        double yaw=0;
        if(valid) {
            yaw=std::atan2(aim.x-start.x,aim.z-start.z);
            if(op==101 && (speed*solution.second>max_distance+1e-6 || !reachable(*p,s,yaw,solution.first))) valid=false;
        }
        b[out]=valid;
        if(valid) {b[out+1]=aim.x;b[out+2]=aim.y;b[out+3]=aim.z;b[out+4]=solution.first;b[out+5]=solution.second;b[out+6]=yaw;}
        return 0;
    }
    if(op==102) {
        Profile &p=profile(r);Pose s=pose(r);
        double desired_yaw=r.next(),world_pitch=r.next(),dt=r.next();
        bool target=r.integer()!=0;
        double turret_speed=r.next(),gun_speed=r.next();
        int out=r.i;if(n!=out+8) throw std::invalid_argument("gun output width");
        double desired_correction=active(p,s)?correction(p,s,desired_yaw,world_pitch).first:0.0;
        s.correction=p.has_hydraulic?slew(clamp(s.correction,p.minimum,p.maximum),desired_correction,p.speed,dt):0.0;
        s.pitch=s.terrain+s.correction;
        Pair local=local_angles(barrel(desired_yaw,world_pitch),s.yaw,s.pitch,s.roll);
        Pair yaw=p.yaw;
        bool limited=p.limited;
        if(p.has_static_yaw && (s.engine || s.tracks || s.overturned || s.moving || switching(s))) {
            yaw=Pair(p.static_yaw,p.static_yaw);limited=true;
        }
        double relative=limited?clamp(local.first,yaw.first,yaw.second):local.first;
        double step=turret_speed*dt;
        s.turret=wrap(s.turret+clamp(wrap(relative-s.turret),-step,step));
        if(limited) s.turret=clamp(s.turret,yaw.first,yaw.second);
        double rotation=turret_speed>0.0?std::abs(s.turret-local.first)/turret_speed:0.0;
        Pair pitch;bool has_limits=limits(p,s,s.turret,pitch);
        double desired=has_limits?clamp(local.second,pitch.first,pitch.second):s.gun;
        if(has_limits) s.gun=gun_step(s.gun,local.second,p,gun_speed,dt,rotation,pitch);
        Vec d;
        if(std::abs(s.pitch)<=1e-12 && std::abs(s.roll)<=1e-12) d=barrel(wrap(s.yaw+s.turret),s.gun);
        else d=ry(rx(rz(barrel(s.turret,s.gun),s.roll),s.pitch),s.yaw);
        s.aim=std::atan2(d.x,d.z);
        bool aligned=has_limits && target && std::abs(wrap(local.first-s.turret))<=0.06 && std::abs(local.second-s.gun)<=0.04;
        b[out]=s.terrain;b[out+1]=s.correction;b[out+2]=s.pitch;b[out+3]=s.turret;
        b[out+4]=s.gun;b[out+5]=desired;b[out+6]=s.aim;b[out+7]=aligned;
        return 0;
    }
    throw std::invalid_argument("combat opcode");
}
