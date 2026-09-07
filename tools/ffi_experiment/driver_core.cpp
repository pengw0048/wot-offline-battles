// Full LocalDriver state machine. Query answers resume the same operation;
// replay is confined to native calculations and never repeats an engine call.
#include "driver_core.h"
#include <algorithm>
#include <cmath>
#include <map>
#include <stdexcept>
#include <vector>

namespace {
const double PI=3.14159265358979323846;
struct Reader {
    double *b;int n,i;
    Reader(double *v,int size):b(v),n(size),i(1){}
    double next(){if(i>=n || !std::isfinite(b[i])) throw std::invalid_argument("driver buffer");return b[i++];}
    int integer(){double v=next();if(v!=std::floor(v)||std::abs(v)>1000000000) throw std::invalid_argument("driver int");return static_cast<int>(v);}
    void end(){if(i!=n) throw std::invalid_argument("driver width");}
};
double clamp(double v,double a,double b){return std::max(a,std::min(b,v));}
double angle(double a,double b){double v=a-b;while(v>PI)v-=2*PI;while(v<-PI)v+=2*PI;return v;}
struct Vec {double x,y,z;Vec(double a=0,double b=0,double c=0):x(a),y(b),z(c){}};
Vec vec(Reader &r){double a=r.next(),b=r.next(),c=r.next();return Vec(a,b,c);}
double distance(Vec a,Vec b){double x=a.x-b.x,z=a.z-b.z;return std::sqrt(x*x+z*z);}
struct Optional {
    bool present;double value;
    Optional():present(false),value(0){}
    void set(double v){present=true;value=v;}
    void clear(){present=false;}
};
struct State {
    int slot,count;Vec last;double stuck,recovery,side,steering_age,plan_age,phase,clock,escape,escape_until,wait,last_step;
    Optional steering,desired,progress,best,clear_yaw;
    bool obstacle,traffic,traffic_present,braking;double brake_x,brake_z;
    std::map<int,double> failed;
    State():slot(0),count(0),stuck(0),recovery(0),side(0),steering_age(999),plan_age(999),phase(0),clock(0),escape(0),escape_until(0),wait(0),last_step(0),obstacle(false),traffic(false),traffic_present(true),braking(false),brake_x(0),brake_z(0){}
};
struct Neighbour {Vec pos;double yaw,length,width,id;bool has_id,alive;};
struct Input {
    int bot,slot;Vec pos,target;double yaw,speed,dt,length,width,stopping,horizon,key_x,key_z;
    bool moving,has_stopping,stop,pose;
    std::vector<Neighbour> neighbours;
};
// 0=done, 1=direction query (distance -1 means unbounded), 2=pose query.
struct Query {int kind;double yaw,distance;};
struct Result {
    double throttle,turn,yaw,blocker;int mode,blocker_kind;
    Result(double a,double b,double c,int d):throttle(a),turn(b),yaw(c),blocker(0),mode(d),blocker_kind(0){}
};
// Modes: arrived, blocked, pivot_recovery, reverse_turn, avoid, drive.
struct Pending {
    bool active;Input in;State initial;std::vector<bool> answers;size_t cursor;
    Pending():active(false),cursor(0){}
    bool query(int kind,double yaw,double distance=-1){
        if(cursor==answers.size()) throw Query{kind,yaw,distance};
        return answers[cursor++];
    }
};
struct Driver {
    double stuck_seconds,recovery_seconds,failure_ttl;
    std::map<int,State> states;Pending pending;
};
std::map<int,Driver> drivers;int next_driver=1;
int yaw_key(double yaw){
    double turn=PI*2.0,from=std::fmod(yaw+PI,turn);if(from<0)from+=turn;
    return static_cast<int>(std::floor(from*24/turn+0.5))%24;
}
double fallback(const State &s){return (s.slot+s.count)&1?1.0:-1.0;}
double penalty(State &s,double yaw,double ttl){
    int key=yaw_key(yaw);auto it=s.failed.find(key);if(it==s.failed.end())return 0;
    if(it->second<=s.clock){s.failed.erase(it);return 0;}
    return 3.0+(it->second-s.clock)/ttl;
}
bool overlap(Vec a,double ay,double al,double aw,Vec b,double by,double bl,double bw){
    double af[]={std::sin(ay),std::cos(ay)},as[]={std::cos(ay),-std::sin(ay)};
    double bf[]={std::sin(by),std::cos(by)},bs[]={std::cos(by),-std::sin(by)};
    const double *axes[]={af,as,bf,bs};double dx=b.x-a.x,dz=b.z-a.z;
    for(const double *axis:axes){
        double d=std::abs(dx*axis[0]+dz*axis[1]);
        double ra=std::abs(af[0]*axis[0]+af[1]*axis[1])*al+std::abs(as[0]*axis[0]+as[1]*axis[1])*aw;
        double rb=std::abs(bf[0]*axis[0]+bf[1]*axis[1])*bl+std::abs(bs[0]*axis[0]+bs[1]*axis[1])*bw;
        if(d>ra+rb)return false;
    }
    return true;
}
bool static_ahead(const Input &in,double yaw,double length,double width){
    double reach=length*1.6;
    Vec sweep(in.pos.x+std::sin(yaw)*reach*0.5,in.pos.y,in.pos.z+std::cos(yaw)*reach*0.5);
    for(const auto &n:in.neighbours){
        if(n.alive || std::abs(n.pos.y-in.pos.y)>5.0)continue;
        if(overlap(sweep,yaw,length+reach*0.5,width,n.pos,n.yaw,n.length,n.width))return true;
    }
    return false;
}
int reverse_blocker(const Input &in,double length,double width,double &id){
    double reach=length*1.6;
    Vec sweep(in.pos.x-std::sin(in.yaw)*reach*0.5,in.pos.y,in.pos.z-std::cos(in.yaw)*reach*0.5);
    for(const auto &n:in.neighbours){
        if(std::abs(n.pos.y-in.pos.y)>5.0)continue;
        if(overlap(sweep,in.yaw,length+reach*0.5,width,n.pos,n.yaw,n.length,n.width)){
            id=n.has_id?n.id:1.0;return n.has_id?2:1;
        }
    }
    return 0;
}
int occupancy(const Input &in,double direction,double length,double width){
    int occupied=0;
    for(const auto &n:in.neighbours){
        if(std::abs(n.pos.y-in.pos.y)>5.0)continue;
        for(int i=1;i<=4;++i){
            double yaw=in.yaw+direction*0.85*(i*0.25);
            if(overlap(in.pos,yaw,length,width,n.pos,n.yaw,n.length,n.width))++occupied;
        }
    }
    return occupied;
}
double select_side(const State &s,const Input &in,double length,double width){
    int negative=occupancy(in,-1.0,length,width),positive=occupancy(in,1.0,length,width);
    return negative<positive?-1.0:(positive<negative?1.0:fallback(s));
}
bool pivot_fits(Pending &job,double direction){
    if(!job.in.pose)return true;
    for(int i=1;i<=4;++i)if(!job.query(2,job.in.yaw+direction*0.85*(i*0.25)))return false;
    return true;
}
Optional choose(State &s,Driver &d,double desired,double length,double width){
    const double offsets[]={0.0,0.42,-0.42,0.78,-0.78,1.18,-1.18,1.55,-1.55};
    std::vector<std::pair<double,double> > candidates;
    for(double offset:offsets){
        double candidate=desired+offset,score=std::abs(offset)+penalty(s,candidate,d.failure_ttl);
        if(s.escape_until>s.clock && offset*s.escape<-0.01)score+=1.25;
        candidates.push_back(std::make_pair(score,candidate));
    }
    std::stable_sort(candidates.begin(),candidates.end(),[](std::pair<double,double>a,std::pair<double,double>b){return a.first<b.first;});
    Optional result;
    for(auto item:candidates){
        if(d.pending.query(1,item.second) && !static_ahead(d.pending.in,item.second,length,width)){
            s.obstacle=std::abs(angle(item.second,desired))>0.05;result.set(item.second);return result;
        }
    }
    return result;
}
Result drive(Driver &d,State &s){
    Pending &job=d.pending;const Input &in=job.in;
    double step=std::max(0.0,in.dt);
    if(!s.traffic)s.wait=0;
    s.traffic=false;s.traffic_present=false;s.last_step=step;s.clock+=step;
    for(auto it=s.failed.begin();it!=s.failed.end();){if(it->second<=s.clock)it=s.failed.erase(it);else ++it;}
    s.steering_age+=step;s.plan_age+=step;
    double desired=std::atan2(in.target.x-in.pos.x,in.target.z-in.pos.z);
    double error=std::abs(angle(desired,in.yaw));s.desired.set(desired);
    double target_distance=distance(in.pos,in.target);
    if(!in.moving || target_distance<=1.5){
        s.stuck=0;s.recovery=0;s.side=0;s.steering.clear();s.progress.clear();s.braking=false;
        if(in.moving)s.last=Vec(in.pos.x,0,in.pos.z);
        return Result(0,0,in.yaw,0);
    }
    double length=std::max(0.5,in.length),width=std::max(0.3,in.width);
    double displacement=distance(in.pos,s.last);bool progress=false;
    if(displacement>=0.08 || !s.progress.present || std::abs(angle(desired,s.progress.value))>0.12){
        s.progress.set(desired);s.best.set(error);
    }else{
        double pe=std::abs(angle(s.progress.value,in.yaw));
        if(pe+0.002<s.best.value){s.best.set(pe);progress=true;}
    }
    if(displacement>=0.08){s.last=Vec(in.pos.x,0,in.pos.z);s.stuck=0;}
    else if(progress)s.stuck=std::max(0.0,s.stuck-step);
    else s.stuck+=step;
    double threshold=d.stuck_seconds+s.phase*0.42;
    if(s.recovery>0){
        s.recovery=std::max(0.0,s.recovery-step);
        if(s.recovery==0){++s.count;s.side=0;s.stuck=0;s.progress.clear();}
    }else if(s.stuck>=threshold){
        if(s.clear_yaw.present)s.failed[yaw_key(s.clear_yaw.value)]=s.clock+d.failure_ttl;
        s.recovery=d.recovery_seconds+s.phase*0.28;s.side=select_side(s,in,length,width);
    }
    if(s.recovery>0){
        double direction=s.side;
        if(direction!=-1 && direction!=1)s.side=direction=select_side(s,in,length,width);
        double recovery_yaw=in.yaw+direction*0.85,reach=length*1.6;
        bool clear=job.query(1,in.yaw+PI,reach);double blocker=0;
        int blocked=clear?reverse_blocker(in,length,width,blocker):0;
        if(!clear || blocked){
            if(!pivot_fits(job,direction)){
                if(pivot_fits(job,-direction)){s.side=direction=-direction;recovery_yaw=in.yaw+direction*0.85;}
                else{Result r(0,0,in.yaw,1);r.blocker_kind=blocked;r.blocker=blocker;return r;}
            }
            return Result(0,direction,recovery_yaw,2);
        }
        double turn=-direction,target=recovery_yaw;
        for(int i=1;i<=4;++i){
            if(!job.query(1,in.yaw+PI+direction*0.85*(i*0.25),reach)){turn=0;target=in.yaw;break;}
        }
        return Result(-0.72,turn,target,3);
    }
    Optional chosen;
    double hold=s.obstacle?1.20:0.35;
    if(s.steering.present && s.plan_age<hold && std::abs(angle(desired,s.steering.value))<2.15 &&
       penalty(s,s.steering.value,d.failure_ttl)<=0 && job.query(1,s.steering.value) &&
       !static_ahead(in,s.steering.value,length,width))chosen=s.steering;
    if(!chosen.present){chosen=choose(s,d,desired,length,width);s.plan_age=0;}
    if(!chosen.present){s.stuck=std::max(s.stuck,threshold);return Result(0,0,in.yaw,1);}
    s.clear_yaw=chosen;
    if(!s.steering.present || std::abs(angle(chosen.value,s.steering.value))>0.04){s.steering=chosen;s.steering_age=0;}
    double delta=angle(chosen.value,in.yaw),turn=clamp(delta/0.58,-1,1),throttle=1;
    double grade=(in.target.y-in.pos.y)/std::max(0.1,target_distance);
    if(grade>0.10 && std::abs(delta)>0.30 && !s.obstacle)throttle=0;
    else if(std::abs(delta)>PI*0.5 || (!s.obstacle && error>PI*0.5))throttle=0;
    if(in.stop && !s.obstacle && in.has_stopping){
        double braking=std::max(0.0,in.stopping),reaction=std::abs(in.speed)*std::max(0.0,in.horizon);
        if(s.braking && (s.brake_x!=in.key_x || s.brake_z!=in.key_z))s.braking=false;
        if(target_distance<=1.5+braking+reaction){s.braking=true;s.brake_x=in.key_x;s.brake_z=in.key_z;}
        if(s.braking){if(std::abs(in.speed)<=0.35 && target_distance>2.0)s.braking=false;else throttle=0;}
    }else if(!in.stop)s.braking=false;
    return Result(throttle,turn,chosen.value,s.obstacle?4:5);
}
Driver &owner(Reader &r){auto it=drivers.find(r.integer());if(it==drivers.end())throw std::invalid_argument("unknown driver");return it->second;}
void resume(Driver &d,double *b,int out){
    State state=d.pending.initial;d.pending.cursor=0;
    try{
        Result r=drive(d,state);
        d.states[d.pending.in.bot]=state;d.pending.active=false;
        b[out]=0;b[out+1]=r.throttle;b[out+2]=r.turn;b[out+3]=r.yaw;b[out+4]=r.mode;b[out+5]=r.blocker_kind;b[out+6]=r.blocker;
    }catch(const Query &q){b[out]=q.kind;b[out+1]=q.yaw;b[out+2]=q.distance;}
}
void optional(double *b,int &i,Optional v){b[i++]=v.present;b[i++]=v.value;}
}
void offline_driver_reset(){drivers.clear();}
int offline_driver_dispatch(double *b,int n){
    Reader r(b,n);int op=static_cast<int>(b[0]);
    if(op==200){
        Driver d;d.stuck_seconds=std::max(0.4,r.next());d.recovery_seconds=std::max(0.25,r.next());d.failure_ttl=std::max(0.25,r.next());r.end();
        int id=next_driver++;drivers[id]=d;b[0]=id;return 0;
    }
    Driver &d=owner(r);
    if(op==201){
        if(d.pending.active)throw std::invalid_argument("reentrant driver start");
        Input in;in.bot=r.integer();in.slot=r.integer();if(in.slot<0 || in.slot>=15)throw std::invalid_argument("team slot");
        in.pos=vec(r);in.yaw=r.next();in.speed=r.next();in.dt=r.next();in.target=vec(r);
        in.length=r.next();in.width=r.next();in.moving=r.integer()!=0;in.has_stopping=r.integer()!=0;
        in.stopping=r.next();in.stop=r.integer()!=0;in.horizon=r.next();in.pose=r.integer()!=0;in.key_x=r.next();in.key_z=r.next();
        int count=r.integer();if(count<0 || count>10000)throw std::invalid_argument("neighbour count");
        for(int i=0;i<count;++i){
            Neighbour v;v.pos=vec(r);v.yaw=r.next();v.length=r.next();v.width=r.next();v.has_id=r.integer()!=0;v.id=r.next();v.alive=r.integer()!=0;in.neighbours.push_back(v);
        }
        int out=r.i;if(n!=out+7)throw std::invalid_argument("driver result width");
        auto it=d.states.find(in.bot);
        if(it==d.states.end()){
            State s;s.slot=in.slot;s.last=Vec(in.pos.x,0,in.pos.z);s.phase=(((in.slot*7)%15)+0.5)/15.0;it=d.states.insert(std::make_pair(in.bot,s)).first;
        }else if(it->second.slot!=in.slot)throw std::invalid_argument("changed team slot");
        d.pending.in=in;d.pending.initial=it->second;d.pending.answers.clear();d.pending.active=true;
        resume(d,b,out);return 0;
    }
    if(op==202){
        if(!d.pending.active)throw std::invalid_argument("driver is not pending");
        bool answer=r.integer()!=0;int out=r.i;if(n!=out+7)throw std::invalid_argument("driver resume width");
        d.pending.answers.push_back(answer);resume(d,b,out);return 0;
    }
    if(op==207){r.end();d.pending.active=false;return 0;}
    if(d.pending.active)throw std::invalid_argument("driver mutation during query");
    int bot=r.integer();auto it=d.states.find(bot);
    if(op==203){r.end();d.states.erase(bot);return 0;}
    if(op==204){
        bool has_elapsed=r.integer()!=0;double elapsed=r.next();r.end();b[0]=it!=d.states.end();
        if(it!=d.states.end()){
            State &s=it->second;s.traffic=true;s.traffic_present=true;s.wait+=std::max(0.0,has_elapsed?elapsed:s.last_step);
            if(s.wait<=1.5){s.stuck=0;s.recovery=0;s.side=0;}
        }
        return 0;
    }
    if(op==205){
        double yaw=r.next();bool has_ttl=r.integer()!=0;double ttl=r.next();r.end();
        if(it!=d.states.end()){
            State &s=it->second;ttl=std::max(0.1,has_ttl?ttl:d.failure_ttl);
            s.failed[yaw_key(yaw)]=s.clock+ttl;
            double offset=s.desired.present?angle(yaw,s.desired.value):0.0;
            s.escape=std::abs(offset)>=0.10?(offset>0?1.0:-1.0):fallback(s);
            s.escape_until=s.clock+std::min(2.0,std::max(0.8,ttl));s.steering.clear();s.plan_age=999;
        }
        return 0;
    }
    if(op==206){
        if(n!=100)throw std::invalid_argument("driver dump width");
        b[0]=it!=d.states.end();if(it==d.states.end())return 0;
        State &s=it->second;int i=1;
        b[i++]=s.slot;b[i++]=s.last.x;b[i++]=s.last.z;b[i++]=s.stuck;b[i++]=s.recovery;b[i++]=s.count;b[i++]=s.side;
        optional(b,i,s.steering);b[i++]=s.obstacle;b[i++]=s.steering_age;b[i++]=s.plan_age;b[i++]=s.phase;b[i++]=s.clock;
        b[i++]=s.escape;b[i++]=s.escape_until;optional(b,i,s.desired);optional(b,i,s.progress);optional(b,i,s.best);
        b[i++]=s.traffic_present;b[i++]=s.traffic;b[i++]=s.wait;b[i++]=s.last_step;b[i++]=s.braking;b[i++]=s.brake_x;b[i++]=s.brake_z;
        optional(b,i,s.clear_yaw);b[i++]=s.failed.size();for(auto edge:s.failed){b[i++]=edge.first;b[i++]=edge.second;}
        return 0;
    }
    throw std::invalid_argument("driver opcode");
}
