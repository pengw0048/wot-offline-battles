#ifndef OFFLINE_EXPERIMENT_MOTION_FLOW_H
#define OFFLINE_EXPERIMENT_MOTION_FLOW_H
#include "vehicle_motion.h"
#include "navigation_state.h"
#include "driver_core.h"
#include "query_bridge.h"
#include <array>
#include <map>
#include <limits>
#include <cstdint>

namespace offline_motion {
using offline_nav::Point;
using offline_nav::Optional;
struct Receipt {
    int kind=0,id=0,direction=0;bool origin_valid=false;
    Point origin;double yaw=0,leading=0,distance=0;
};
struct Probe {
    int kind=0,id=0;bool truth=false,clear=true,collision=false,water=false,deferred=false,pending=false,copied=false;
    double slope=0;Receipt receipt;
    bool is_clear()const{return kind==2?clear&&!collision&&!water&&std::abs(slope)<=0.55:truth;}
};
struct Cache {
    bool has=false,position_valid=false;Point position;double yaw=0,deadline=0;
    Optional<double> maximum,distance,leading;Probe result;
};
struct Input {
    int bot,navigation,driver;
    Point position,aim;Optional<Point> move;
    double yaw,speed,turn_speed,half_length,half_width,throttle,turn,minimum_yaw,maximum_yaw,mobility,step,now,water;
    double tick_siege_yaw;
    int recovery,grind;
    bool has_target,movement,mobility_blocked,airborne,grounded,siege_locked,bake_admitted,baked_escape;
    bool decision_due,refresh,has_resolver,has_report,grind_present;
    Optional<double> siege_limit,destructible_speed;
};
struct Output {
    Point position;double yaw,speed,turn_speed,drive_pitch=0,attempted_yaw;
    int movement=0,rotation=0,grind;bool hull_aiming=false,grind_present;
    Optional<double> destructible_speed;
    explicit Output(const Input &in):position(in.position),yaw(in.yaw),speed(in.speed),turn_speed(in.turn_speed),attempted_yaw(in.yaw),grind(in.grind),grind_present(in.grind_present),destructible_speed(in.destructible_speed){}
};
inline void put(std::vector<double> &b,Point p){b.push_back(p.x);b.push_back(p.y);b.push_back(p.z);}
inline void put(std::vector<double> &b,Optional<double> v){b.push_back(v.has);b.push_back(v.value);}
inline void put(std::vector<double> &b,const Receipt &r){
    b.push_back(r.kind);b.push_back(r.id);b.push_back(r.origin_valid);put(b,r.origin);
    b.push_back(r.yaw);b.push_back(r.direction);b.push_back(r.leading);b.push_back(r.distance);
}
inline void put(std::vector<double> &b,const Probe &p){
    b.push_back(p.kind);b.push_back(p.id);b.push_back(p.truth);b.push_back(p.clear);b.push_back(p.collision);
    b.push_back(p.water);b.push_back(p.slope);b.push_back(p.deferred);b.push_back(p.pending);put(b,p.receipt);b.push_back(p.copied);
}
inline void put(std::vector<double> &b,const Cache &c){
    b.push_back(c.has);b.push_back(c.position_valid);put(b,c.position);b.push_back(c.yaw);
    put(b,c.maximum);put(b,c.distance);put(b,c.leading);b.push_back(c.deadline);put(b,c.result);
}
struct Values {
    const double *b;int i;
    double next(){double v=b[i++];if(!std::isfinite(v))throw std::invalid_argument("motion query result");return v;}
    int integer(){double v=next();if(std::abs(v)>1000000000||v!=std::floor(v))throw std::invalid_argument("motion query integer");return static_cast<int>(v);}
    Point point(){double x=next(),y=next(),z=next();return Point(x,y,z);}
    Optional<double> optional(){bool has=integer()!=0;double v=next();return has?Optional<double>(v):Optional<double>();}
    Receipt receipt(){Receipt r;r.kind=integer();r.id=integer();r.origin_valid=integer()!=0;r.origin=point();r.yaw=next();r.direction=integer();r.leading=next();r.distance=next();return r;}
    Probe probe(){Probe p;p.kind=integer();p.id=integer();p.truth=integer()!=0;p.clear=integer()!=0;p.collision=integer()!=0;p.water=integer()!=0;p.slope=next();p.deferred=integer()!=0;p.pending=integer()!=0;p.receipt=receipt();p.copied=integer()!=0;return p;}
    Cache cache(){Cache c;c.has=integer()!=0;c.position_valid=integer()!=0;c.position=point();c.yaw=next();c.maximum=optional();c.distance=optional();c.leading=optional();c.deadline=next();c.result=probe();return c;}
};
inline bool contains(const Receipt &r,Point position,double yaw,double speed,double dt){
    if(r.kind!=2||!r.origin_valid||(r.direction!=-1&&r.direction!=1)||r.direction!=(speed<0?-1:1))return false;
    double dx=position.x-r.origin.x,dy=std::abs(position.y-r.origin.y),dz=position.z-r.origin.z;
    double s=std::sin(r.yaw),c=std::cos(r.yaw),forward=dx*s+dz*c,lateral=std::abs(dx*c-dz*s);
    double angle=std::abs(offline_nav::wrap(yaw-r.yaw)),leading=std::max(0.0,r.leading),distance=std::max(0.0,r.distance);
    double reach=std::max(0.4,std::abs(speed)*clamp(dt,0,0.2)+0.2);
    return forward>=-0.0001&&forward+leading+reach<=distance&&dy<=0.0001&&lateral<=0.0001&&angle<=0.00001;
}
inline bool refresh_due(const Receipt &r,Point position,double yaw,double speed,double dt){
    if(!contains(r,position,yaw,speed,dt))return false;
    double dx=position.x-r.origin.x,dz=position.z-r.origin.z;
    double forward=dx*std::sin(r.yaw)+dz*std::cos(r.yaw),reach=std::max(0.4,std::abs(speed)*clamp(dt,0,0.2)+0.2);
    return std::max(0.0,r.distance)-forward-std::max(0.0,r.leading)-reach<=6.0;
}
inline bool covers(const Cache &c,Optional<double> maximum){
    if(!c.has)return false;
    if(!maximum.has)return !c.maximum.has;
    return !c.maximum.has||c.maximum.value+1e-6>=maximum.value;
}
inline bool reusable(const Cache &c,Point position,double yaw,double speed,double now,bool settled,double dt,bool ignore=false){
    if(!c.has||(c.result.kind==2&&c.result.deferred)||!c.position_valid)return false;
    double dx=position.x-c.position.x,dy=std::abs(position.y-c.position.y),dz=position.z-c.position.z;
    double s=std::sin(c.yaw),co=std::cos(c.yaw),forward=dx*s+dz*co,lateral=std::abs(dx*co-dz*s),angle=std::abs(offline_nav::wrap(yaw-c.yaw));
    if(settled)return std::abs(forward)<=0.05&&lateral<=0.05&&dy<=0.05&&angle<=0.005;
    if(!ignore&&now>=c.deadline)return false;
    double lookahead=std::abs(speed)>5?20:15,budget=3.5;
    if(ignore&&c.distance.has&&c.leading.has){double reach=std::max(0.4,std::abs(speed)*clamp(dt,0,0.2)+0.2);budget=std::max(0.0,c.distance.value-std::max(0.5,c.leading.value)-reach);}
    if(forward<-0.1||forward>budget||lateral+lookahead*std::abs(std::sin(angle))>1.0)return false;
    return c.result.receipt.kind==0||contains(c.result.receipt,position,yaw,speed,dt);
}
inline bool boundary(offline_nav::Grid &g,Point before,double before_yaw,Point after,double after_yaw,double length,double width){
    if(!g.bounded||g.bounds[0]>=g.bounds[2]||g.bounds[1]>=g.bounds[3])return true;
    length=std::max(0.5,length);width=std::max(0.3,width);double overflow[2][4];Point positions[]={before,after};double yaws[]={before_yaw,after_yaw};
    for(int i=0;i<2;++i){
        double s=std::abs(std::sin(yaws[i])),c=std::abs(std::cos(yaws[i])),ex=c*width+s*length,ez=s*width+c*length;
        overflow[i][0]=std::max(0.0,g.bounds[0]+ex-positions[i].x);overflow[i][1]=std::max(0.0,positions[i].x+ex-g.bounds[2]);
        overflow[i][2]=std::max(0.0,g.bounds[1]+ez-positions[i].z);overflow[i][3]=std::max(0.0,positions[i].z+ez-g.bounds[3]);
    }
    for(int i=0;i<4;++i)if(overflow[1][i]>overflow[0][i]+1e-6)return false;
    return true;
}
struct Flow {
    std::map<int,Cache> caches;
    double probe_seconds=0.15;
    std::array<double,128> event(int kind,int bot,const std::vector<double> &args={}){
        std::array<double,128> packet{};if(args.size()+2>packet.size())throw std::invalid_argument("motion event width");
        packet[0]=kind;packet[1]=bot;std::copy(args.begin(),args.end(),packet.begin()+2);
        if(offline_query(packet.data(),packet.size()))throw std::runtime_error("motion engine event");
        return packet;
    }
    Probe direction(const Input &in,double yaw,Optional<double> maximum,Optional<double> query_speed={}){
        std::vector<double> args;put(args,in.position);args.push_back(yaw);args.push_back(query_speed.has?query_speed.value:in.speed);put(args,maximum);args.push_back(!query_speed.has);
        auto packet=event(610,in.bot,args);Values values{packet.data(),0};return values.probe();
    }
    Receipt receipt(const Input &in,double yaw,double speed,bool uncached,Optional<double> maximum){
        std::vector<double> args;put(args,in.position);args.push_back(yaw);args.push_back(speed);args.push_back(uncached);put(args,maximum);
        auto packet=event(611,in.bot,args);Values values{packet.data(),0};return values.receipt();
    }
    void cache(int bot,Cache value){std::vector<double> args;put(args,value);auto answer=event(612,bot,args);value.result.truth=answer[0]!=0;value.result.copied=false;caches[bot]=value;}
    void remember(const Input &in,double yaw,bool hard=false){if(in.driver)offline_driver_remember(in.driver,in.bot,yaw,hard,5.0);}
    void invalidate(const Input &in,double yaw){caches[in.bot]=Cache();event(614,in.bot);remember(in,yaw,true);}
    Optional<Point> blocked_target(const Input &in,double yaw,offline_nav::Navigator *nav){
        if(in.move.has&&nav&&nav->grid->data->cell>0){
            double distance=nav->grid->data->cell;
            return Point(in.position.x+std::sin(yaw)*distance,in.position.y,in.position.z+std::cos(yaw)*distance);
        }
        return in.move;
    }
    int resolve(const Input &in,double yaw,double speed,bool passive=false,bool commit=true){
        std::vector<double> args;put(args,in.position);args.push_back(yaw);args.push_back(speed);args.push_back(in.step);args.push_back(in.now);args.push_back(passive);args.push_back(commit);
        auto result=event(615,in.bot,args);double value=result[0];
        if(value!=std::floor(value)||value<0||value>4||(passive&&value==3))throw std::invalid_argument("motion resolver status");
        return static_cast<int>(value);
    }
    Output step(Input in,const Params &p,const Tuning &t,offline_nav::Navigator *nav){
        Output out(in);double throttle=clamp(in.throttle,-1,1),turn=clamp(in.turn,-1,1);
        double ax=in.aim.x-in.position.x,az=in.aim.z-in.position.z,aim_distance=std::sqrt(ax*ax+az*az);
        double desired=aim_distance>0.1?std::atan2(ax,az):in.yaw;
        bool limited=!(in.minimum_yaw<=-offline_nav::pi+0.1&&in.maximum_yaw>=offline_nav::pi-0.1);
        if(in.has_target&&(in.recovery==0||in.recovery==5)&&limited){
            double relative=offline_nav::wrap(desired-in.yaw);
            if(!(in.minimum_yaw+0.04<=relative&&relative<=in.maximum_yaw-0.04)){turn=clamp(relative/0.58,-1,1);throttle=0;out.hull_aiming=true;}
        }
        if(in.siege_locked){throttle=0;turn=0;in.movement=false;in.speed=out.speed=0;in.turn_speed=out.turn_speed=0;}
        if(in.mobility_blocked){throttle=0;turn=0;}else if(std::abs(throttle)>0.01)throttle*=in.mobility;
        double sign=(throttle<0||(std::abs(throttle)<=0.01&&in.speed<0))?-1.0:1.0;
        double travel=in.yaw+(sign<0?offline_nav::pi:0);out.attempted_yaw=travel;
        event(613,in.bot,{0.0,static_cast<double>(out.hull_aiming),static_cast<double>(in.siege_locked),travel});
        Optional<double> maximum;
        if(nav){
            double horizon=std::max(nav->grid->data->cell,std::abs(in.speed)*1.5);
            if(sign>0&&in.move.has&&in.movement&&(in.recovery==0||in.recovery==1)){
                double remaining=std::max(0.5,offline_nav::distance(in.position,in.move.value)-1.5);maximum=std::min(remaining,horizon);
            }else if(sign<0&&in.recovery==3)maximum=horizon;
        }
        Cache previous=caches[in.bot];bool frozen=false;
        bool settled=std::abs(throttle)<=0.01&&std::abs(turn)<=0.01&&std::abs(in.speed)<=0.02&&in.grounded&&!in.airborne;
        bool reuse=(!in.decision_due||covers(previous,maximum))&&reusable(previous,in.position,travel,in.speed,in.now,settled,in.step,!in.refresh);
        Probe previous_result=previous.result;
        if(!previous.has||!previous_result.truth){previous_result=Probe();previous_result.kind=2;previous_result.clear=true;}
        bool geometry=previous_result.kind==2&&previous_result.is_clear()&&covers(previous,maximum)&&reusable(previous,in.position,travel,in.speed,in.now,settled,in.step,true);
        bool catchup_reprobe=!in.refresh&&!reuse&&previous.has&&previous_result.kind==2&&previous_result.is_clear()&&
            (std::abs(throttle)>0.01||std::abs(in.speed)>0.0001||std::abs(turn)>0.01||std::abs(in.turn_speed)>0.01);
        bool hold=!in.refresh&&!reuse&&!catchup_reprobe;Probe probe;
        if(hold){throttle=0;turn=0;frozen=true;}
        else if(!reuse){
            probe=direction(in,travel,maximum);
            if(probe.kind==2&&probe.is_clear()&&!probe.deferred&&std::abs(probe.slope)<=0.01&&
                    (std::abs(throttle)>0.01||std::abs(in.speed)>0.0001)&&std::abs(turn)<=0.01&&std::abs(in.turn_speed)<=0.01&&!in.airborne){
                probe.copied=true;double receipt_speed=std::abs(in.speed);
                if(throttle<0||(std::abs(throttle)<=0.01&&in.speed<0))receipt_speed=-std::max(receipt_speed,0.000001);
                bool contained=previous.has&&previous.result.kind==2&&!previous.result.deferred&&previous.result.is_clear()&&contains(previous.result.receipt,in.position,travel,receipt_speed,in.step);
                if(contained){
                    Receipt proof=previous.result.receipt;
                    if(refresh_due(proof,in.position,travel,receipt_speed,in.step)){
                        Receipt refreshed=receipt(in,travel,receipt_speed,false,maximum);
                        if(refreshed.kind==1){probe.clear=false;probe.collision=true;}
                        else if(refreshed.kind==3)probe.pending=true;
                        else if(contains(refreshed,in.position,travel,receipt_speed,in.step))proof=refreshed;
                    }
                    probe.receipt=proof;
                }else{
                    Receipt proof=receipt(in,travel,receipt_speed,previous_result.receipt.kind!=2,maximum);
                    if(proof.kind==3)probe.pending=true;
                    else if(proof.kind==1){probe.clear=false;probe.collision=true;}
                    else if(proof.kind==2)probe.receipt=proof;
                }
            }
            bool deferred=probe.kind==2&&probe.deferred;
            if(probe.kind!=0&&!deferred){
                Cache next;next.has=next.position_valid=true;next.position=in.position;next.yaw=travel;next.maximum=maximum;
                next.distance=std::min(std::abs(in.speed)>5.0?20.0:15.0,maximum.has?maximum.value:std::numeric_limits<double>::infinity());
                next.leading=std::max(0.5,in.half_length);next.result=probe;
                double phase=(((std::abs(static_cast<int64_t>(in.bot))*17+7*11)%29)+1)/29.0;
                next.deadline=probe.pending?in.now:in.now+probe_seconds*(previous.has?1.0:phase);
                cache(in.bot,next);probe.copied=false;
            }else if(deferred){
                if(geometry)probe=previous_result;
                else{cache(in.bot,Cache());if(previous.has){throttle=0;turn=0;frozen=true;}}
            }else if(previous_result.receipt.kind!=2)cache(in.bot,Cache());
        }else probe=previous.result;
        bool deferred=probe.kind==2&&probe.deferred;
        bool exact=in.has_resolver&&probe.kind==2&&!deferred&&probe.collision&&!probe.water&&std::abs(probe.slope)<=0.55;
        bool clear=(hold||deferred||probe.kind==0||exact||(std::abs(throttle)<=0.01&&std::abs(in.speed)<=0.0001)||in.airborne)?true:probe.is_clear();
        if(!clear){throttle=0;if(!deferred)remember(in,travel);
            if(nav&&!deferred&&!(probe.kind==2&&probe.collision)&&in.move.has)nav->report_blocked(in.bot,in.position,blocked_target(in,travel,nav),in.now);
        }
        int steering=std::abs(turn)>0.01?(turn>0?1:-1):0;
        out.movement=throttle>0.01?1:throttle<-0.01?-1:0;out.rotation=steering;
        std::vector<double> logs{1.0,static_cast<double>(out.movement),static_cast<double>(out.rotation),throttle,turn,static_cast<double>(clear),static_cast<double>(frozen),travel};put(logs,probe);event(613,in.bot,logs);
        double pitch=sign*-std::atan(probe.kind==2?probe.slope:0.0);
        out.turn_speed=in.siege_locked?0.0:traverse(t,p,in.turn_speed,turn,in.speed,in.step,0,throttle);
        out.yaw=offline_nav::wrap(in.yaw+out.turn_speed*in.step);
        if(nav&&!boundary(*nav->grid,in.position,in.yaw,in.position,out.yaw,in.half_length,in.half_width)){out.turn_speed=0;out.yaw=in.yaw;out.rotation=0;}
        out.attempted_yaw=out.yaw+(sign<0?offline_nav::pi:0);
        bool wet=in.baked_escape||in.water>0.90;
        bool shallow=nav&&(nav->shallow_step(in.bot,in.position,out.attempted_yaw)||nav->shallow_step(in.bot,in.position,out.attempted_yaw,0.45,true));
        bool veto=false;
        if(nav&&!wet&&in.bake_admitted&&nav->grid->index(nav->grid->cell(in.position))>=0){
            double distance=std::max(1.0,nav->grid->data->cell);
            Point end(in.position.x+std::sin(out.attempted_yaw)*distance,in.position.y,in.position.z+std::cos(out.attempted_yaw)*distance);
            veto=nav->grid->motion_hazard(in.position,end,shallow?9:13);
        }
        if(veto){clear=false;throttle=0;out.movement=0;}
        double speed=in.siege_locked?0.0:longitudinal(t,p,in.speed,throttle,steering!=0,pitch,in.step,in.airborne,0,false);
        out.drive_pitch=pitch;double drive_speed=speed;bool hard=false,deflected=false;
        if(!clear){if(probe.kind==2&&probe.collision&&!exact)hard=true;else speed*=0.2;out.destructible_speed.reset();}
        std::vector<double> integrated{2.0,out.yaw,out.turn_speed,static_cast<double>(out.rotation),static_cast<double>(out.movement),pitch,drive_speed,throttle,static_cast<double>(veto),static_cast<double>(clear),static_cast<double>(frozen),out.attempted_yaw};
        put(integrated,out.destructible_speed);event(613,in.bot,integrated);
        int status=0;bool resolved=false;double contact=out.destructible_speed.has?out.destructible_speed.value:speed,v0=speed;
        Optional<double> realised_contact_yaw;
        if(clear&&!frozen&&std::abs(speed)>0.0001&&in.has_resolver){
            resolved=true;status=resolve(in,out.yaw,speed);
            if(status!=0&&status!=1){clear=false;
                if(status==2){contact=std::min(std::abs(contact),std::abs(speed));speed=speed<0?-contact:contact;out.destructible_speed=speed;}
                else if(status==3){speed=in.speed;out.destructible_speed.reset();}
                else if(status==4){realised_contact_yaw=out.yaw+(v0<0?offline_nav::pi:0);invalidate(in,realised_contact_yaw.value);hard=true;out.destructible_speed.reset();}
            }else out.destructible_speed.reset();
        }
        if(hard&&!in.airborne){
            Optional<double> slide;const double offsets[]={0.55,-0.55,1.0,-1.0};
            for(double offset:offsets){
                double yaw=out.yaw+offset;bool fits;
                if(in.has_resolver){int value=resolve(in,yaw,speed,true,false);fits=value==0||value==1;}
                else fits=direction(in,yaw,{},speed).is_clear();
                if(fits){if(in.has_resolver){int value=resolve(in,yaw,speed,true,true);if(value==0||value==1)slide=yaw;}else slide=yaw;break;}
            }
            auto response=hard_contact(t,speed,in.step,out.grind>0,slide.has,slide.value);
            speed=response[0];out.position=Point(in.position.x+response[1],in.position.y,in.position.z+response[2]);
            deflected=slide.has;out.grind=4;out.grind_present=true;
            if(nav&&in.move.has)nav->report_blocked(in.bot,in.position,blocked_target(in,realised_contact_yaw.has?realised_contact_yaw.value:travel,nav),in.now);
        }else if(status==2||status==3){out.grind=1;out.grind_present=true;}
        if(resolved&&in.has_report){std::vector<double> args{static_cast<double>(status),v0,speed};put(args,out.destructible_speed);args.push_back(out.grind_present);args.push_back(out.grind);event(616,in.bot,args);}
        if(in.siege_limit.has)speed=clamp(speed,-in.siege_limit.value,in.siege_limit.value);
        out.speed=speed;
        if(!deflected&&clear&&!frozen){out.position.x+=std::sin(out.yaw)*speed*in.step;out.position.z+=std::cos(out.yaw)*speed*in.step;
            if(std::abs(speed)>0.0001){out.grind=std::max(0,out.grind-1);out.grind_present=true;}
        }
        event(617,in.bot,{static_cast<double>(status),static_cast<double>(hard),out.position.x,out.position.y,out.position.z,out.speed});
        return out;
    }
};
}
#endif
