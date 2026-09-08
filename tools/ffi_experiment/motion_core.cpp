// The copied-motion owner. Physics inputs are pinned to the descriptor/tuning
// producer, and the full update uses these laws directly within C++.
#include "motion_core.h"
#include "vehicle_motion.h"
#include "motion_flow.h"
#include "motion_vertical.h"
#include "navigation_flow.h"
#include <map>
#include <stdexcept>
#include <vector>

namespace {
using namespace offline_motion;
struct Reader {
    double *b;int n,i;
    double number(){if(i>=n||!std::isfinite(b[i]))throw std::invalid_argument("motion packet");return b[i++];}
    int integer(int low=-1000000000,int high=1000000000){double v=number();if(v<low||v>high||v!=std::floor(v))throw std::invalid_argument("motion integer");return static_cast<int>(v);}
    void end(){if(i!=n)throw std::invalid_argument("motion packet width");}
};
struct Profile {int owner;Params params;};
std::map<int,Tuning> tuning;
std::map<int,Profile> profiles;
std::map<int,Flow> flows;
int next_flow=1;
int next_tuning=1,next_profile=1;
const Tuning &get_tuning(int id){auto found=tuning.find(id);if(found==tuning.end())throw std::invalid_argument("motion tuning owner");return found->second;}
const Profile &get_profile(int id){auto found=profiles.find(id);if(found==profiles.end())throw std::invalid_argument("motion profile owner");return found->second;}
}
extern "C" void offline_motion_reset(void){flows.clear();profiles.clear();tuning.clear();}
extern "C" int offline_motion_dispatch(double *b,int n){
    Reader r{b,n,1};int op=static_cast<int>(b[0]);
    if(op==600){
        Tuning value;
#define READ(name) value.name=r.number();
        OFFLINE_MOTION_CONSTANTS(READ)
#undef READ
        r.end();int id=next_tuning++;tuning[id]=value;b[0]=id;return 0;
    }
    if(op==601){
        Profile value;value.owner=r.integer(1);get_tuning(value.owner);Params &p=value.params;
        p.mass=r.number();p.powerW=r.number();p.nativePowerRatio=r.number();p.specificFriction=r.number();p.brakeDecel=r.number();
        p.speedFwd=r.number();p.speedBwd=r.number();p.rotSpd=r.number();for(double &v:p.terrainResist)v=r.number();r.end();
        if(p.mass<=0||p.terrainResist[0]<=0||p.terrainResist[1]<=0||p.terrainResist[2]<=0)throw std::invalid_argument("motion descriptor");
        int id=next_profile++;profiles[id]=value;b[0]=id;return 0;
    }
    if(op==602){
        const Profile &profile=get_profile(r.integer(1));const Params &p=profile.params;const Tuning &c=get_tuning(profile.owner);
        int kind=r.integer(0,4),count=r.integer(0,1000000);std::vector<double> output;
        for(int i=0;i<count;++i){
            if(kind==0){
                double v=r.number(),throttle=r.number();bool steering=r.integer(0,1)!=0;double pitch=r.number(),dt=r.number();
                bool airborne=r.integer(0,1)!=0;int terrain=r.integer(0,2);bool handbrake=r.integer(0,1)!=0;
                if(dt==0&&!airborne&&!handbrake&&((throttle>0&&v<-0.1)||(throttle<0&&v>0.1)))throw std::domain_error("zero motion step");
                output.push_back(longitudinal(c,p,v,throttle,steering,pitch,dt,airborne,terrain,handbrake));
            }else if(kind==1){
                double omega=r.number(),steer=r.number(),v=r.number(),dt=r.number();int terrain=r.integer(0,2);double intent=r.number();
                output.push_back(traverse(c,p,omega,steer,v,dt,terrain,intent));
            }else if(kind==2){
                double speed=r.number(),dt=r.number();bool grinding=r.integer(0,1)!=0,slide=r.integer(0,1)!=0;double yaw=r.number();
                auto result=hard_contact(c,speed,dt,grinding,slide,yaw);output.insert(output.end(),result.begin(),result.end());
            }else if(kind==3){double speed=r.number(),pitch=r.number(),dt=r.number();output.push_back(ground_gap(c,speed,pitch,dt));}
            else{double current=r.number(),tangent=r.number(),dt=r.number();output.push_back(slope_slide(c,current,tangent,dt));}
        }
        r.end();if(output.size()+1>static_cast<size_t>(n))throw std::invalid_argument("motion result capacity");
        for(double value:output)if(!std::isfinite(value))throw std::domain_error("nonfinite physical result");
        b[0]=count;for(size_t i=0;i<output.size();++i)b[i+1]=output[i];return 0;
    }
    if(op==624){
        auto &nav=offline_runtime_navigation(r.integer(1));
        double x=r.number(),y=r.number(),z=r.number();Point position(x,y,z);
        double tx=r.number(),ty=r.number(),tz=r.number();Point tick(tx,ty,tz);
        double yaw=r.number(),length=r.number(),width=r.number();bool safe=r.integer(0,1)!=0;r.end();
        double column=std::round((position.x-nav.grid->data->ox)/nav.grid->data->cell);
        double row=std::round((position.z-nav.grid->data->oz)/nav.grid->data->cell);
        int cell=-1;
        if(column>=0&&column<nav.grid->data->width&&row>=0&&row<nav.grid->data->height)
            cell=static_cast<int>(row)*nav.grid->data->width+static_cast<int>(column);
        b[0]=!boundary(*nav.grid,tick,yaw,position,yaw,length,width)||
            (safe&&(cell<0||(nav.grid->data->hazards[cell]&9)));return 0;
    }
    if(op==625){
        auto owner=flows.find(r.integer(1));if(owner==flows.end())throw std::invalid_argument("contact flow owner");
        Flow &flow=owner->second;int bot=r.integer(0);
        double x=r.number(),y=r.number(),z=r.number(),yaw=r.number(),speed=r.number(),push_x=r.number(),push_z=r.number();
        double length=r.number(),width=r.number(),dx=r.number(),dz=r.number(),cx=r.number(),cz=r.number(),dt=r.number();
        bool advance=r.integer(0,1)!=0,correct=r.integer(0,1)!=0;r.end();
        double s=std::sin(yaw),c=std::cos(yaw),forward=dx*s+dz*c,applied=0;
        if(forward*speed<0){applied=std::abs(forward)>=std::abs(speed)?-speed:forward;speed+=applied;}
        push_x=push_x+dx-applied*s;push_z=push_z+dz-applied*c;
        double mx=(correct?cx:0)+(advance?push_x*dt:0),mz=(correct?cz:0)+(advance?push_z*dt:0);
        double distance=std::sqrt(mx*mx+mz*mz);
        if(distance>0.0001){
            double contact_yaw=std::atan2(mx,mz),contact_speed=distance/std::max(dt,1.0/120.0),relative=contact_yaw-yaw;
            double cs=std::abs(std::cos(relative)),ss=std::abs(std::sin(relative));
            double support=std::max(0.5,length)*cs+std::max(0.3,width)*ss;
            double corridor=std::max(0.5,length)*ss+std::max(0.3,width)*cs;
            auto answer=flow.event(635,bot,{x,y,z,contact_yaw,contact_speed,std::max(1.0,distance+support),corridor,speed});
            if(answer[0]==0)mx=mz=push_x=push_z=0;
        }
        double decay=advance?std::pow(0.90,std::max(0.0,dt)*60.0):1;
        b[0]=x+mx;b[1]=z+mz;b[2]=speed;b[3]=push_x*decay;b[4]=push_z*decay;return 0;
    }
    if(op==623){
        const Profile &profile=get_profile(r.integer(1));double speed=r.number(),pitch=r.number();
        bool steer=r.integer(0,1)!=0;double dt=r.number(),epsilon=r.number();r.end();
        b[0]=stopping_distance(get_tuning(profile.owner),profile.params,speed,pitch,steer,dt,epsilon);return 0;
    }
    if(op==620||op==622){
        auto owner=flows.find(r.integer(1));if(owner==flows.end())throw std::invalid_argument("vertical owner");
        const Tuning &t=get_tuning(r.integer(1));Flow &flow=owner->second;
        int bot=r.integer(0);double x=r.number(),y=r.number(),z=r.number();Point position(x,y,z);
        double yaw=r.number(),length=r.number();
        if(op==622){
            double width=r.number(),pitch=r.number(),roll=r.number();r.end();
            auto result=slope(flow,bot,position,yaw,length,width,pitch,roll);b[0]=result.first;b[1]=result.second;return 0;
        }
        Vertical v;v.bot=bot;v.position=position;v.yaw=yaw;v.half_length=length;
        v.speed=r.number();v.step=r.number();v.vertical=r.number();v.pitch=r.number();v.airborne=r.integer(0,1)!=0;v.grounded=r.integer(0,1)!=0;
        bool tick=r.integer(0,1)!=0;double tx=r.number(),ty=r.number(),tz=r.number();if(tick)v.tick=Point(tx,ty,tz);v.trace=r.integer(0,1)!=0;r.end();
        if(v.step<0||v.step>0.2)throw std::invalid_argument("vertical interval");
        auto result=vertical_step(flow,t,v);std::vector<double> out;put(out,result.position);
        out.insert(out.end(),{result.speed,result.vertical,static_cast<double>(result.airborne),static_cast<double>(result.grounded),
            static_cast<double>(result.blocked),static_cast<double>(result.landed),result.impact});
        put(out,result.highest);put(out,result.centre);out.push_back(result.maximum_climb);out.push_back(result.rise_obstacle);out.push_back(result.rise_continuous);
        if(out.size()>static_cast<size_t>(n))throw std::invalid_argument("vertical output width");
        std::copy(out.begin(),out.end(),b);return 0;
    }
    if(op==610){
        Flow flow;flow.probe_seconds=r.number();r.end();if(flow.probe_seconds<=0)throw std::invalid_argument("motion cadence");
        int id=next_flow++;flows[id]=flow;b[0]=id;return 0;
    }
    if(op==612){int id=r.integer(1);r.end();flows.erase(id);return 0;}
    if(op==611){
        auto owner=flows.find(r.integer(1));if(owner==flows.end())throw std::invalid_argument("motion flow owner");
        const Profile &profile=get_profile(r.integer(1));Flow &flow=owner->second;
        Input in;in.bot=r.integer(0);in.navigation=r.integer(0);in.driver=r.integer(0);
        auto point=[&](){double x=r.number(),y=r.number(),z=r.number();return Point(x,y,z);};
        auto optional=[&](){bool has=r.integer(0,1)!=0;double v=r.number();return has?Optional<double>(v):Optional<double>();};
        in.position=point();in.aim=point();bool move=r.integer(0,1)!=0;Point target=point();if(move)in.move=target;
        in.yaw=r.number();in.speed=r.number();in.turn_speed=r.number();in.half_length=r.number();in.half_width=r.number();
        in.throttle=r.number();in.turn=r.number();in.minimum_yaw=r.number();in.maximum_yaw=r.number();in.mobility=r.number();
        in.step=r.number();in.now=r.number();in.water=r.number();in.tick_siege_yaw=r.number();
        in.recovery=r.integer(0,5);in.grind=r.integer(0);
        in.has_target=r.integer(0,1)!=0;in.movement=r.integer(0,1)!=0;in.mobility_blocked=r.integer(0,1)!=0;
        in.airborne=r.integer(0,1)!=0;in.grounded=r.integer(0,1)!=0;in.siege_locked=r.integer(0,1)!=0;
        in.bake_admitted=r.integer(0,1)!=0;in.baked_escape=r.integer(0,1)!=0;in.decision_due=r.integer(0,1)!=0;
        in.refresh=r.integer(0,1)!=0;in.has_resolver=r.integer(0,1)!=0;in.has_report=r.integer(0,1)!=0;in.grind_present=r.integer(0,1)!=0;
        in.siege_limit=optional();in.destructible_speed=optional();bool seed=r.integer(0,1)!=0;
        Cache seeded;
        if(seed){if(n-r.i!=33)throw std::invalid_argument("motion cache width");Values values{b,r.i};seeded=values.cache();r.i=values.i;}
        r.end();if(in.step<=0||in.step>0.2)throw std::invalid_argument("motion elapsed interval");
        offline_nav::Navigator *nav=in.navigation?&offline_runtime_navigation(in.navigation):nullptr;
        if(seed)flow.caches[in.bot]=seeded;
        Output out=flow.step(in,profile.params,get_tuning(profile.owner),nav);
        std::vector<double> result;put(result,out.position);
        for(double value:{out.yaw,out.speed,out.turn_speed,out.drive_pitch,out.attempted_yaw,
                static_cast<double>(out.movement),static_cast<double>(out.rotation),static_cast<double>(out.grind),
                static_cast<double>(out.grind_present),static_cast<double>(out.hull_aiming)})result.push_back(value);
        put(result,out.destructible_speed);
        bool found=nav&&nav->states.count(in.bot);result.push_back(found);
        if(found){const auto &s=nav->states.at(in.bot);result.push_back(s.status);result.push_back(s.planned_goal.has);put(result,s.planned_goal.value);result.push_back(s.shallow.has);put(result,s.shallow.value);result.push_back(s.terminal);}
        if(result.size()>static_cast<size_t>(n))throw std::invalid_argument("motion output width");
        std::copy(result.begin(),result.end(),b);return 0;
    }
    throw std::invalid_argument("motion operation");
}
