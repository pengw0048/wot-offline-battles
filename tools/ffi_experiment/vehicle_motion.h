#ifndef OFFLINE_EXPERIMENT_VEHICLE_MOTION_H
#define OFFLINE_EXPERIMENT_VEHICLE_MOTION_H
#include <algorithm>
#include <array>
#include <cmath>

namespace offline_motion {
// Values are read from the current Python vehicle_physics owner at install.
// Configured tuning and descriptor-derived parameters cross once, not per ray.
#define OFFLINE_MOTION_CONSTANTS(X) \
    X(GRAVITY) X(GRAVITY_FACTOR) X(COHESION) X(DRIVE_TRACTION) \
    X(SLOPE_GRIP_LNG_FULL_Y) X(SLOPE_GRIP_LNG_FULL) X(SLOPE_GRIP_LNG_MIN_Y) X(SLOPE_GRIP_LNG_MIN) \
    X(POWER_FACTOR) X(BKWD_POWER_FRACTION) X(ENGINE_MIN_V) X(STEER_RESIST_MULT) \
    X(COH_DECAY_Y) X(COH_DECAY_FACTOR) X(COH_DECAY_POW) X(SLOPE_COH_DECAY_Y) X(SLOPE_COH_DECAY) X(COH_DECAY_BOUND) \
    X(COAST_BRAKE_SHARE) X(SLIDE_KINETIC) X(SLIDE_HOLD_TAN) X(SLIP_THRESHOLD_TAN) X(SLIP_DRAG) \
    X(OVERSPEED_MAX_FACTOR) X(OVERSPEED_BUILD) X(OVERSPEED_DAMP) \
    X(SPEED_AFFECT_ROT_DECREASE) X(ANG_ACCELERATION_TIME) \
    X(HARD_CONTACT_ENTRY_FACTOR) X(HARD_CONTACT_SLIDE_DECAY) X(HARD_CONTACT_BRAKE_DECAY) X(HARD_CONTACT_STOP_SPEED) \
    X(GROUND_PITCH_LIMIT) X(GROUND_FOLLOW_BASE) X(GROUND_FOLLOW_MIN) X(GROUND_FOLLOW_MAX) \
    X(SLIDE_DRAG) X(SLIDE_MAX) X(FALL_SAFE_SPEED) X(FALL_DMG_PER_MS)
struct Tuning {
#define DECLARE(name) double name;
    OFFLINE_MOTION_CONSTANTS(DECLARE)
#undef DECLARE
};
struct Params {
    double mass,powerW,nativePowerRatio,specificFriction,brakeDecel,speedFwd,speedBwd,rotSpd;
    std::array<double,3> terrainResist;
};
inline double clamp(double v,double low,double high){return std::max(low,std::min(high,v));}
inline double cohesion(const Tuning &c,double ny){
    double value=c.COHESION;
    if(ny<c.COH_DECAY_Y)value-=c.COH_DECAY_FACTOR*std::pow(c.COH_DECAY_Y-ny,c.COH_DECAY_POW);
    if(ny<c.SLOPE_COH_DECAY_Y)value-=c.SLOPE_COH_DECAY;
    return value>c.COH_DECAY_BOUND?value:c.COH_DECAY_BOUND;
}
inline double longitudinal_grip(const Tuning &c,double pitch){
    double ny=std::cos(pitch),grip;
    if(ny>=c.SLOPE_GRIP_LNG_FULL_Y)grip=c.SLOPE_GRIP_LNG_FULL;
    else if(ny<=c.SLOPE_GRIP_LNG_MIN_Y)grip=c.SLOPE_GRIP_LNG_MIN;
    else{
        double span=c.SLOPE_GRIP_LNG_FULL_Y-c.SLOPE_GRIP_LNG_MIN_Y;
        double progress=(ny-c.SLOPE_GRIP_LNG_MIN_Y)/span;
        grip=c.SLOPE_GRIP_LNG_MIN+progress*(c.SLOPE_GRIP_LNG_FULL-c.SLOPE_GRIP_LNG_MIN);
    }
    return grip*c.DRIVE_TRACTION;
}
inline double grip_decel(const Tuning &c,double pitch){double ny=std::cos(pitch);return cohesion(c,ny)*c.GRAVITY*(ny>0.1?ny:0.1);}
inline double rolling(const Tuning &c,const Params &p,int terrain,bool steering){
    double f=p.mass*p.specificFriction*c.GRAVITY_FACTOR*p.terrainResist[terrain];
    if(steering)f*=c.STEER_RESIST_MULT;
    return f;
}
inline double engine(const Tuning &c,const Params &p,double v,double throttle,double pitch){
    if(throttle==0)return 0;
    double power=p.powerW*c.POWER_FACTOR*p.nativePowerRatio;
    if(throttle<0)power*=c.BKWD_POWER_FRACTION;
    double f=power/std::max(std::abs(v),c.ENGINE_MIN_V),ny=std::cos(pitch);
    double cap=longitudinal_grip(c,pitch)*p.mass*c.GRAVITY*(ny>0.1?ny:0.1);
    if(f>cap)f=cap;
    return f*throttle;
}
inline double longitudinal(const Tuning &c,const Params &p,double v,double throttle,bool steering,
                           double pitch,double dt,bool airborne=false,int terrain=0,bool handbrake=false){
    if(airborne)return v;
    double grav=c.GRAVITY*std::sin(pitch);
    if(handbrake){
        double grip=grip_decel(c,pitch);
        if(std::abs(v)<0.05)return std::abs(grav)<=grip?0.0:v+(grav-(grav>0?grip:-grip))*dt;
        double accel=grav-(v>0?grip:-grip),next=v+accel*dt;
        return (v>0)!=(next>0)?0.0:next;
    }
    double grip=grip_decel(c,pitch),rr=rolling(c,p,terrain,steering)/p.mass,accel;
    if(throttle!=0){
        double ef=engine(c,p,v,throttle,pitch)/p.mass,ny=std::cos(pitch);
        double climb=longitudinal_grip(c,pitch)*c.GRAVITY*(ny>0.1?ny:0.1);
        bool cannot=throttle*grav<0&&climb<std::abs(grav)+rr;
        if(cannot)ef=0;
        accel=ef+grav;double drag=clamp(v/0.08,-1,1);accel-=rr*drag;
        if(cannot){
            if(std::abs(v)>0.05){double kinetic=c.SLIDE_KINETIC*c.GRAVITY*(ny>0.1?ny:0.1);accel+=v<0?kinetic:-kinetic;}
        }else if((throttle>0&&v<-0.1)||(throttle<0&&v>0.1)){
            double need=-v/dt-accel;accel+=std::abs(need)<grip?need:(need>0?grip:-grip);
        }
    }else{
        if(std::abs(v)<0.02){
            double ny=std::cos(pitch),hold=c.SLIDE_HOLD_TAN*c.GRAVITY*(ny>0.1?ny:0.1);
            if(std::abs(grav)<=hold)return 0;
            accel=grav-(grav>0?hold:-hold);
        }else{
            double sign=v>0?1:-1,downhill=std::max(0.0,std::tan(pitch)*sign),start=0.8*c.SLIDE_HOLD_TAN;
            double fade=std::min(1.0,std::max(0.0,(downhill-start)/(c.SLIDE_HOLD_TAN-start)));
            double resist=rr+c.COAST_BRAKE_SHARE*(1.0-fade)*grip;accel=grav-(v>0?resist:-resist);
        }
    }
    double grade=v>0?-pitch:pitch;
    if(std::abs(v)>0.5&&grade>0){
        double tangent=std::tan(grade);
        if(tangent>c.SLIP_THRESHOLD_TAN){double slip=c.SLIP_DRAG*(tangent-c.SLIP_THRESHOLD_TAN)*c.GRAVITY;accel-=v>0?slip:-slip;}
    }
    double next=v+accel*dt;
    if(throttle==0&&std::abs(grav)<=grip&&v!=0&&(v>0)!=(next>0))next=0;
    double sign=next>=0?1:-1,limit=next>=0?p.speedFwd:p.speedBwd;
    if(std::abs(next)>limit){
        double cap=limit*(c.OVERSPEED_MAX_FACTOR-1.0),previous=std::abs(v)-limit;
        if(previous<0)previous=0;
        double excess=(throttle*sign>0&&grav*sign>0.05)?
            previous+c.OVERSPEED_BUILD*std::sin(std::abs(pitch))*dt:previous-(rr+c.OVERSPEED_DAMP)*dt;
        if(excess<0)excess=0;
        if(excess>cap)excess=cap;
        next=sign*(limit+excess);
    }
    return next;
}
inline double traverse(const Tuning &c,const Params &p,double omega,double steer,double v,double dt,int terrain=0,double intent=0){
    double ratio=std::abs(v)/std::max(p.speedFwd,0.1),modifier=1.0/(1.0+ratio*c.SPEED_AFFECT_ROT_DECREASE);
    double ground=p.terrainResist[0]/p.terrainResist[terrain],maximum=p.rotSpd*modifier*ground;
    double target=steer*(intent<0?-1.0:1.0)*maximum,diff=target-omega,ramp=maximum/c.ANG_ACCELERATION_TIME;
    if(std::abs(diff)<ramp*dt)omega=target;else omega+=ramp*dt*(diff>0?1:-1);
    if(steer==0&&std::abs(omega)<0.01)omega=0;
    return omega;
}
inline std::array<double,3> hard_contact(const Tuning &c,double speed,double dt,bool grinding,bool slide,double yaw){
    dt=std::max(0.0,dt);
    if(!slide){speed*=std::pow(c.HARD_CONTACT_BRAKE_DECAY,dt*60.0);if(std::abs(speed)<c.HARD_CONTACT_STOP_SPEED)speed=0;return {{speed,0,0}};}
    if(!grinding)speed*=c.HARD_CONTACT_ENTRY_FACTOR;
    speed*=std::pow(c.HARD_CONTACT_SLIDE_DECAY,dt*60.0);
    return {{speed,std::sin(yaw)*speed*dt,std::cos(yaw)*speed*dt}};
}
inline double ground_gap(const Tuning &c,double speed,double pitch,double dt){
    dt=std::max(0.0,dt);pitch=clamp(pitch,-c.GROUND_PITCH_LIMIT,c.GROUND_PITCH_LIMIT);
    double tangent=std::max(0.0,speed*std::tan(pitch)*dt),gap=c.GROUND_FOLLOW_BASE+tangent+c.GRAVITY*dt*dt;
    return clamp(gap,c.GROUND_FOLLOW_MIN,c.GROUND_FOLLOW_MAX);
}
inline double launch_speed(double speed,double pitch){double vertical=speed*std::sin(-pitch);return vertical>0?vertical:0.0;}
inline double slope_slide(const Tuning &c,double current,double tangent,double dt){
    double theta=std::atan(tangent);
    if(tangent<=c.SLIDE_HOLD_TAN){current-=c.COHESION*c.GRAVITY*dt;return current>0?current:0.0;}
    current+=(c.GRAVITY*(std::sin(theta)-c.SLIDE_HOLD_TAN*std::cos(theta))-c.SLIDE_DRAG*current)*dt;
    return clamp(current,0,c.SLIDE_MAX);
}
}
#endif
