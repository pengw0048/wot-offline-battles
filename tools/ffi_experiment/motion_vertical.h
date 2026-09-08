#ifndef OFFLINE_EXPERIMENT_MOTION_VERTICAL_H
#define OFFLINE_EXPERIMENT_MOTION_VERTICAL_H
#include "motion_flow.h"

namespace offline_motion {
struct Vertical {
    int bot=0,driver=0;
    Point position;Optional<Point> tick;
    double yaw=0,speed=0,half_length=3.5,step=0,vertical=0,pitch=0,attempted=0;
    bool airborne=false,grounded=false,blocked=false,landed=false,trace=false;
    double impact=0;
    Optional<double> highest,centre;
    double maximum_climb=0;
    bool rise_obstacle=false,rise_continuous=false;
};
inline Optional<double> ground(Flow &flow,int bot,double x,double z,double hint){
    auto packet=flow.event(630,bot,{x,z,hint});Values result{packet.data(),0};return result.optional();
}
inline bool continuous_rise(Flow &flow,const Vertical &v){
    if(!v.centre.has||!v.tick.has)return false;
    double dx=v.position.x-v.tick.value.x,dz=v.position.z-v.tick.value.z;
    double distance=std::sqrt(dx*dx+dz*dz),rise=v.centre.value-v.tick.value.y;
    if(distance<=0.0001||rise<=0||rise>distance*0.55+0.02)return false;
    int segments=std::max(2,static_cast<int>(std::ceil(distance/1.5)));if(segments>5)return false;
    double limit=distance/static_cast<double>(segments)*0.55+0.02,previous=v.tick.value.y;
    for(int i=1;i<segments;++i){
        double fraction=static_cast<double>(i)/segments;
        auto support=ground(flow,v.bot,v.tick.value.x+dx*fraction,v.tick.value.z+dz*fraction,v.tick.value.y+rise*fraction);
        if(!support.has||std::abs(support.value-previous)>limit)return false;
        previous=support.value;
    }
    return std::abs(v.centre.value-previous)<=limit;
}
inline Vertical vertical_step(Flow &flow,const Tuning &t,Vertical v){
    v.centre=ground(flow,v.bot,v.position.x,v.position.z,v.position.y);v.highest=v.centre;
    if(!v.centre.has){
        double s=std::sin(v.yaw),c=std::cos(v.yaw),length=std::max(1.5,v.half_length);
        for(double offset:{length,-length}){
            auto support=ground(flow,v.bot,v.position.x+s*offset,v.position.z+c*offset,v.position.y);
            if(support.has&&(!v.highest.has||support.value>v.highest.value))v.highest=support;
        }
    }
    if(v.trace){std::vector<double> args{0.0};put(args,v.highest);put(args,v.centre);args.push_back(v.grounded);flow.event(631,v.bot,args);}
    Optional<double> support=v.centre.has?v.centre:v.highest;
    if(support.has){
        double gap=ground_gap(t,v.speed,v.pitch,v.step);
        v.maximum_climb=std::max(0.6,std::abs(v.speed)*v.step*2.5);
        v.rise_obstacle=v.grounded&&v.centre.has&&v.centre.value-v.position.y>std::min(std::max(0.0,v.maximum_climb),0.85)+0.02;
        if(v.rise_obstacle){
            v.rise_continuous=continuous_rise(flow,v);
            if(v.rise_continuous)v.maximum_climb=std::max(v.maximum_climb,std::max(0.0,v.centre.value-v.tick.value.y));
        }
        if(v.trace)flow.event(631,v.bot,{1.0,v.maximum_climb,static_cast<double>(v.rise_obstacle),static_cast<double>(v.rise_continuous)});
        double com_gap=v.position.y-support.value,landing=v.centre.has?v.centre.value:support.value;
        if(!v.grounded){v.position.y=landing;v.vertical=0;v.airborne=false;v.grounded=true;}
        else if(v.rise_obstacle&&!v.rise_continuous){
            if(v.tick.has)v.position=v.tick.value;
            v.speed=0;v.vertical=0;v.airborne=false;v.blocked=true;
        }else if(v.position.y<=support.value||(com_gap<=gap&&!v.airborne)){
            v.impact=v.airborne?v.vertical:0;
            if(v.position.y<support.value)v.position.y+=std::min(support.value-v.position.y,v.maximum_climb);
            else{v.position.y+=(support.value-v.position.y)*std::min(1.0,v.step*15.0);v.position.y=std::min(v.position.y,support.value+0.12);}
            v.vertical=0;v.airborne=false;v.landed=v.impact<0;
        }else{
            if(!v.airborne)v.vertical=launch_speed(v.speed,v.pitch);
            v.airborne=true;
            int substeps=std::min(8,std::max(1,static_cast<int>(std::abs(v.vertical*v.step)/0.5)+1));
            double dt=v.step/static_cast<double>(substeps);
            for(int i=0;i<substeps;++i){
                v.vertical-=t.GRAVITY*dt;v.position.y+=v.vertical*dt;
                if(v.position.y<=landing){v.impact=v.vertical;v.position.y=landing;v.vertical=0;v.airborne=false;v.landed=true;break;}
            }
        }
    }else if(v.grounded){
        if(!v.airborne)v.vertical=launch_speed(v.speed,v.pitch);
        v.airborne=true;v.vertical-=t.GRAVITY*v.step;v.position.y+=v.vertical*v.step;
    }else{v.vertical=0;v.airborne=false;}
    return v;
}
inline std::pair<double,double> slope(Flow &flow,int bot,Point position,double yaw,double half_length,double half_width,double pitch,double roll){
    double length=std::max(3.0,2*half_length),width=std::max(2.0,2*half_width),s=std::sin(yaw),c=std::cos(yaw);
    auto front=ground(flow,bot,position.x+s*length*0.5,position.z+c*length*0.5,position.y);
    auto rear=ground(flow,bot,position.x-s*length*0.5,position.z-c*length*0.5,position.y);
    auto right=ground(flow,bot,position.x+c*width*0.5,position.z-s*width*0.5,position.y);
    auto left=ground(flow,bot,position.x-c*width*0.5,position.z+s*width*0.5,position.y);
    if(!front.has||!rear.has||!right.has||!left.has)return {pitch,roll};
    double p=-std::atan2(front.value-rear.value,length)*0.9,r=std::atan2(right.value-left.value,width)*0.9;
    double tilt=std::sqrt(p*p+r*r);if(tilt>0.61){double scale=0.61/tilt;p*=scale;r*=scale;}
    return {pitch+(p-pitch)*0.5,roll+(r-roll)*0.5};
}
inline double stopping_distance(const Tuning &t,const Params &p,double speed,double pitch,bool steer,double dt,double epsilon){
    double current=std::abs(speed);if(current<=epsilon)return 0;double distance=0;
    for(int i=0;i<4096;++i){
        double following=longitudinal(t,p,current,0,steer,pitch,dt,false,0,false);
        if(!std::isfinite(following))return std::numeric_limits<double>::infinity();
        if(following<=epsilon)return distance+std::max(0.0,following)*dt;
        if(following>=current)return std::numeric_limits<double>::infinity();
        distance+=following*dt;current=following;
    }
    return std::numeric_limits<double>::infinity();
}
}
#endif
