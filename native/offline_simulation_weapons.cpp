#include "offline_simulation_weapons.h"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>
#include <tuple>

namespace offline_simulation { namespace weapons {
namespace {
const double pi=3.14159265358979323846;
void demand(bool value) { if (!value) throw std::invalid_argument("invalid native weapon state"); }
double positive(double value) { demand(std::isfinite(value)); return std::max(0.,value); }
double wrap(double value) {
    demand(std::isfinite(value));
    while (value>pi) value-=pi*2.;
    while (value< -pi) value+=pi*2.;
    return value;
}
double bound(double value,double minimum,double maximum) {
    return std::max(minimum,std::min(maximum,value));
}
float f32(double value) {
    const float result=static_cast<float>(value); demand(std::isfinite(result)); return result;
}
float curve(float yaw,const Curve &points) {
    demand(points.size()>=2);
    size_t lower=0,upper=points.size()-1;
    while (upper-lower>1) {
        const size_t middle=(lower+upper)/2;
        if (yaw>points[middle][0]) lower=middle; else upper=middle;
    }
    const double span=f32(double(points[upper][0])-double(points[lower][0]));
    demand(span!=0.);
    const double fraction=f32(double(f32(double(yaw)-double(points[lower][0])))/span);
    const double left=f32(double(points[lower][1])*double(f32(1.-fraction)));
    const double right=f32(double(points[upper][1])*fraction);
    return f32(left+right);
}
}

void GunConfig::validate() const {
    for (double value:{fully_aimed_dispersion,after_shot,after_shot_in_burst,
         turret_dispersion_factor,aiming_time,movement_dispersion_factor,
         rotation_dispersion_factor,reload_full,reload_intra,burst_interval})
        demand(std::isfinite(value) && value>=0.);
    demand(fully_aimed_dispersion>0. && aiming_time>=.1 && reload_full>0.);
    demand(clip_size>=1 && shell_count>=1 && burst_count>=1 && burst_count<=64);
    demand(burst_interval<=10. && (burst_count==1 || burst_interval>0.));
    demand(clip_size==1 || reload_intra>0.);
}
bool GunConfig::operator==(const GunConfig &v) const {
    return std::tie(fully_aimed_dispersion,after_shot,after_shot_in_burst,
        turret_dispersion_factor,aiming_time,movement_dispersion_factor,
        rotation_dispersion_factor,reload_full,reload_intra,clip_size,
        shell_count,burst_count,burst_interval)==std::tie(v.fully_aimed_dispersion,
        v.after_shot,v.after_shot_in_burst,v.turret_dispersion_factor,v.aiming_time,
        v.movement_dispersion_factor,v.rotation_dispersion_factor,v.reload_full,
        v.reload_intra,v.clip_size,v.shell_count,v.burst_count,v.burst_interval);
}
GunState::GunState(const GunConfig &value):config(value) {
    config.validate(); clip=config.clip_size; reload_duration=config.reload_full;
    dispersion=config.fully_aimed_dispersion;
}
bool GunState::adopt(const GunConfig &value) {
    value.validate(); demand(value.clip_size==config.clip_size && value.shell_count==config.shell_count);
    const bool changed=!(config==value); const double old=dispersion; config=value;
    current_dispersion_factor=std::max(1.,old/config.fully_aimed_dispersion);
    const double aiming=std::max(config.aiming_time,.1),maximum=1000.;
    const double max_elapsed=(current_dispersion_factor>0. && current_dispersion_factor<=maximum)
        ?aiming*std::log(maximum/current_dispersion_factor):0.;
    if (aiming_elapsed>max_elapsed) {
        aiming_start_factor=std::max(current_dispersion_factor,maximum); aiming_elapsed=max_elapsed;
    } else aiming_start_factor=current_dispersion_factor*std::exp(aiming_elapsed/aiming);
    dispersion=config.fully_aimed_dispersion*current_dispersion_factor;
    return changed;
}
void GunState::restore(std::int64_t fire_seq,double factor,bool has_reload,
    double time,double restored_duration,double restored_factor,bool has_clip,
    int restored_clip,int restored_size) {
    GunState result=*this; fire_seq=std::max<std::int64_t>(0,fire_seq);
    if (has_clip) {
        demand(restored_size==config.clip_size && restored_clip>=0 && restored_clip<=restored_size);
        result.clip=restored_clip; result.intra=restored_clip>0 && restored_clip<restored_size;
    } else if (config.clip_size>1) {
        const int used=static_cast<int>(fire_seq%config.clip_size);
        result.clip=used?config.clip_size-used:config.clip_size; result.intra=used!=0;
    } else {result.clip=1; result.intra=false;}
    result.reload_duration=result.intra?config.reload_intra:config.reload_full;
    if (has_reload) {
        demand(std::isfinite(time) && std::isfinite(restored_duration) && std::isfinite(restored_factor));
        demand(restored_factor>0. && restored_duration>0. && time>=0. && time<=restored_duration);
        const double expected=result.duration(restored_factor);
        const double tolerance=std::max(std::max(1.,expected)*1.e-9,2.e-6);
        demand(std::abs(restored_duration-expected)<=tolerance);
        result.elapsed=restored_duration-time; result.reload_factor=restored_factor;
    } else {result.elapsed=0.; result.reload_factor=1.;}
    result.burst_remaining=0;
    if (fire_seq>0) { result.motion_dispersion_squared=0.; result.bloom(factor,true); }
    *this=result;
}
void GunState::tick(double dt) {elapsed+=positive(dt);}
void GunState::tick_dispersion(double dt,double move,double rotation,double turret,double factor,double aim_factor) {
    dt=positive(dt); factor=positive(factor); aim_factor=positive(aim_factor);
    demand(std::isfinite(move) && std::isfinite(rotation) && std::isfinite(turret));
    const double a=std::abs(move)*config.movement_dispersion_factor;
    const double b=std::abs(rotation)*config.rotation_dispersion_factor;
    const double c=std::abs(turret)*config.turret_dispersion_factor;
    const double motion=a*a+b*b+c*c;
    const double ideal=factor*std::sqrt(1.+motion); demand(std::isfinite(ideal) && ideal>0.);
    const double aiming=config.aiming_time*aim_factor,time=aiming_elapsed+dt;
    const double candidate=aiming_start_factor*std::exp(-time/std::max(aiming,.1));
    motion_dispersion_squared=motion;
    if (candidate<ideal) {current_dispersion_factor=ideal; aiming_start_factor=ideal; aiming_elapsed=0.;}
    else {current_dispersion_factor=candidate; aiming_elapsed=time;}
    dispersion=config.fully_aimed_dispersion*current_dispersion_factor;
}
void GunState::bloom(double factor,bool final_round) {
    factor=positive(factor); const double bloom=final_round?config.after_shot:config.after_shot_in_burst;
    const double ideal=factor*std::sqrt(1.+motion_dispersion_squared+bloom*bloom);
    if (current_dispersion_factor<ideal) {
        current_dispersion_factor=ideal; aiming_start_factor=ideal; aiming_elapsed=0.;
        dispersion=config.fully_aimed_dispersion*ideal;
    }
}
double GunState::duration(double factor) const {return reload_duration*(intra?1.:positive(factor));}
double GunState::remaining(double factor) const {return std::max(0.,duration(factor)-elapsed);}
bool GunState::ready(double factor) const {return elapsed>duration(factor);}
bool GunState::rescale(double factor) {
    factor=positive(factor); if (std::abs(factor-reload_factor)<=1.e-9) return false;
    const double before=duration(reload_factor),after=duration(factor);
    if (before>0.) elapsed=elapsed<before?after*bound(elapsed/before,0.,1.):after+(elapsed-before);
    reload_factor=factor; return true;
}
int GunState::complete(double factor,int available) {
    if (!ready(factor)) return -1;
    if (!intra && clip==0) clip=available<0?config.clip_size:std::min(config.clip_size,std::max(0,available));
    return intra?1:0;
}
void GunState::require_full() {clip=0; intra=false; reload_duration=config.reload_full;}
bool GunState::begin(int count,double factor) {
    if (burst_remaining>0 || !ready(factor)) return false;
    if (clip<=0) complete(factor);
    count=std::min(count,clip); if (count<=0) return false;
    burst_remaining=count; return true;
}
bool GunState::consume(bool final_round) {
    if (burst_remaining<=0 || clip<=0 || final_round!=(burst_remaining==1)) return false;
    --clip; --burst_remaining;
    if (!final_round) return true;
    elapsed=0.; intra=clip>0; reload_duration=intra?config.reload_intra:config.reload_full;
    return true;
}
bool GunState::cancel() {
    if (burst_remaining<=0) return false;
    burst_remaining=0; elapsed=0.; intra=clip>0;
    reload_duration=intra?config.reload_intra:config.reload_full; return true;
}

void AmmoState::validate(int count) const {
    demand(int(remaining.size())==count && int(categories.size())==count);
    demand(loaded>=0 && loaded<count && next>=0 && next<count);
    for (int value:remaining) demand(value>=0 && value<=1000);
    for (int value:categories) demand(value>=0 && value<=2);
    if (std::accumulate(remaining.begin(),remaining.end(),0)>0) {
        demand(remaining[next]>0); demand(reload_pending || remaining[loaded]>0);
    }
}
int AmmoState::fallback() const {
    int first=-1;
    for (size_t i=0;i<remaining.size();++i) if (remaining[i]>0) {
        if (first<0) first=int(i);
        if (categories[i]==0) return int(i);
    }
    return first<0?0:first;
}
int AmmoState::available(int requested) const {
    return requested>=0 && requested<int(remaining.size()) && remaining[requested]>0?requested:fallback();
}
bool AmmoState::stage(int requested,bool ready,bool full) {
    if (!ready) return false;
    bool changed=false;
    if (reload_pending) {
        if (full) { const int selected=available(next); if (selected!=loaded) {loaded=selected; changed=true;} }
        reload_pending=false; plan_pending=true;
    }
    if (plan_pending) {
        const int selected=available(requested); if (selected!=next) {next=selected; changed=true;}
        plan_pending=false;
    }
    return changed;
}
bool AmmoState::can_fire(bool continuing) const {
    return loaded>=0 && loaded<int(remaining.size()) && remaining[loaded]>0 && (continuing || !reload_pending);
}
bool AmmoState::consume(bool continuing) {
    if (!can_fire(continuing)) return false;
    --remaining[loaded]; next=available(next); reload_pending=true; plan_pending=false; return true;
}
int AmmoState::planned_rounds() const {return next>=0 && next<int(remaining.size())?remaining[next]:0;}
bool AmmoState::requires_full() const {
    return loaded>=0 && loaded<int(remaining.size()) && remaining[loaded]<=0 &&
        std::accumulate(remaining.begin(),remaining.end(),0)>0;
}

bool BurstState::start(std::int64_t first,int amount,double delay,int selected) {
    if (active || first<=0 || amount<1 || amount>64 || selected<0 || !std::isfinite(delay) ||
        delay<0. || delay>10. || (amount>1 && delay<=0.)) return false;
    active=true; group_seq=first; count=amount; next_index=0; shell=selected;
    interval=amount>1?delay:0.; time_left=0.; return true;
}
std::vector<BurstEdge> BurstState::advance(double dt) {
    std::vector<BurstEdge> result;
    if (!active || !std::isfinite(dt)) return result;
    dt=std::max(0.,dt); double offset=std::max(0.,time_left); time_left-=dt;
    while (active && time_left<=1.e-9) {
        BurstEdge edge; edge.shot_seq=group_seq+next_index; edge.group_seq=group_seq;
        edge.index=next_index; edge.count=count; edge.shell=shell;
        edge.final_round=next_index+1>=count; edge.due_offset=std::min(dt,offset); result.push_back(edge);
        ++next_index;
        if (next_index>=count) {active=false; time_left=0.;}
        else {time_left+=interval; offset+=interval;}
    }
    return result;
}
bool BurstState::cancel(int launched) {
    const bool changed=active || count>0; if (launched<0) launched=next_index;
    if (launched<0 || launched>next_index) return false;
    active=false; next_index=launched;
    if (launched==0) {group_seq=0; count=0; interval=0.; shell=0;}
    time_left=0.; return changed;
}
void BurstState::validate(std::int64_t fire_seq) const {
    demand(group_seq>=0 && count>=0 && count<=64 && next_index>=0 && next_index<=count && shell>=0);
    demand(std::isfinite(interval) && std::isfinite(time_left) && interval>=0. && interval<=10. && time_left>=0.);
    demand(count<=1 || interval>0.);
    demand(!active || (count>=2 && next_index>=1 && next_index<count && time_left<=interval+1.e-9));
    demand(active || time_left==0.);
    demand(count!=0 || (group_seq==0 && next_index==0));
    demand(count==0 || fire_seq==group_seq+next_index-1);
}

void AimConfig::validate() const {
    demand(fixed_pitch || (minimum.size()>=2 && maximum.size()>=2) ||
        (minimum.empty() && maximum.empty()));
    demand(!fixed_pitch || (std::isfinite(fixed_limits[0]) && std::isfinite(fixed_limits[1])));
    for (const auto *points:{&minimum,&maximum}) for (const auto &point:*points)
        demand(std::isfinite(point[0]) && std::isfinite(point[1]));
    demand(std::isfinite(turret_speed) && turret_speed>=0. && std::isfinite(gun_speed) && gun_speed>=0.);
    demand(!has_static_pitch || std::isfinite(static_pitch));
}
std::array<double,2> pitch_limits(double yaw,const AimConfig &config) {
    if(config.fixed_pitch) return config.fixed_limits;
    float value=f32(yaw); if (value<0.) value=f32(double(value)+double(f32(2.*pi)));
    return {{curve(value,config.minimum),curve(value,config.maximum)}};
}
double gun_pitch_step(double current,double desired,bool has_static,double static_pitch,
    double speed,double elapsed,double turret_time,std::array<double,2> limits) {
    speed=positive(speed); elapsed=positive(elapsed); if (speed==0.) return current;
    demand(limits[0]<=limits[1]); const double wanted=bound(desired,limits[0],limits[1]);
    if (std::abs(current-desired)<1.e-6) return wanted;
    const double difference=wanted-current; double limit=speed*elapsed;
    if (has_static) {
        if (difference*(current-static_pitch)<0.) limit*=2.;
        turret_time=positive(turret_time);
        if (turret_time>0.) limit=std::min(limit,(std::abs(difference)/turret_time)*elapsed);
    }
    if (difference>limit) return current+limit;
    if (difference< -limit) return current-limit;
    return wanted;
}
void advance_aim(AimState &state,const AimConfig &config,const AimInput &input) {
    demand(std::isfinite(input.raw_pitch));
    const double desired=input.limited_yaw?bound(input.raw_yaw,input.minimum_yaw,input.maximum_yaw):input.raw_yaw;
    const double speed=config.turret_speed*positive(input.crew_factor)*positive(input.turret_factor);
    const double step=speed*positive(input.dt),difference=wrap(desired-state.turret_yaw);
    double current=wrap(state.turret_yaw+bound(difference,-step,step));
    if (input.limited_yaw) current=bound(current,input.minimum_yaw,input.maximum_yaw);
    const double rotation_time=speed>0.?std::abs(current-input.raw_yaw)/speed:0.;
    double wanted=state.gun_pitch,pitch=state.gun_pitch;
    state.limits_valid=input.valid_pitch;
    if (input.valid_pitch) {
        const auto limits=input.override_pitch?input.pitch_limits:pitch_limits(current,config);
        state.limits=limits;
        wanted=bound(input.raw_pitch,limits[0],limits[1]);
        pitch=gun_pitch_step(state.gun_pitch,input.raw_pitch,config.has_static_pitch,config.static_pitch,
            config.gun_speed*positive(input.gun_factor),input.dt,rotation_time,limits);
    }
    state.turret_yaw=current; state.gun_pitch=pitch; state.desired_gun_pitch=wanted;
    state.aligned=input.valid_pitch && input.target && std::abs(wrap(input.raw_yaw-current))<=.06 &&
        std::abs(input.raw_pitch-pitch)<=.04;
}
std::array<double,2> scatter(const Point &base,double radius,double azimuth,bool normalize_base) {
    demand(std::isfinite(radius) && radius>=0. && std::isfinite(azimuth));
    double length=0.; for(double value:base) {demand(std::isfinite(value));length+=value*value;}
    length=std::sqrt(length); demand(length>1.e-12);
    Point direction=base; if(normalize_base) for(size_t i=0;i<3;++i) direction[i]=base[i]/length;
    const double dx=direction[0],dy=direction[1],dz=direction[2];
    Point reference={{0.,0.,0.}};
    if(std::abs(dx)<=std::abs(dy) && std::abs(dx)<=std::abs(dz)) reference[0]=1.;
    else if(std::abs(dy)<=std::abs(dz)) reference[1]=1.; else reference[2]=1.;
    Point tangent={{dy*reference[2]-dz*reference[1],dz*reference[0]-dx*reference[2],dx*reference[1]-dy*reference[0]}};
    double total=0.;for(double value:tangent) total+=value*value; total=std::sqrt(total);
    for(double &value:tangent) value/=total;
    const Point up={{dy*tangent[2]-dz*tangent[1],dz*tangent[0]-dx*tangent[2],dx*tangent[1]-dy*tangent[0]}};
    const double cosine=std::cos(radius),sine=std::sin(radius);
    for(size_t i=0;i<3;++i) direction[i]=direction[i]*cosine+(tangent[i]*std::cos(azimuth)+up[i]*std::sin(azimuth))*sine;
    const double horizontal=std::sqrt(direction[0]*direction[0]+direction[2]*direction[2]);
    return {{std::atan2(direction[0],direction[2]),std::atan2(direction[1],std::max(1.e-9,horizontal))}};
}

std::vector<std::array<double,2>> ballistic_solutions(const Point &start,const Point &target,
    double speed,double gravity,double minimum,double maximum) {
    std::vector<std::array<double,2>> result; const double acceleration=std::abs(gravity);
    if(speed<=1. || acceleration<=.01) return result;
    const double dx=target[0]-start[0],dz=target[2]-start[2],horizontal=std::sqrt(dx*dx+dz*dz);
    if(horizontal<=.1) return result;
    const double dy=target[1]-start[1],squared=speed*speed;
    const double discriminant=squared*squared-acceleration*(acceleration*horizontal*horizontal+2.*dy*squared);
    if(discriminant<0.) return result;
    const double root=std::sqrt(std::max(0.,discriminant));
    for(double numerator:{squared-root,squared+root}) {
        const double elevation=std::atan(numerator/(acceleration*horizontal)),pitch=-elevation;
        if(pitch<minimum-.0001 || pitch>maximum+.0001) continue;
        const double horizontal_speed=speed*std::cos(elevation); if(horizontal_speed<=.01) continue;
        const double time=horizontal/horizontal_speed; if(time<=0. || time>20.) continue;
        if(result.empty() || std::abs(pitch-result.back()[0])>.00001) result.push_back({{pitch,time}});
    }
    std::stable_sort(result.begin(),result.end(),[](const std::array<double,2> &a,const std::array<double,2> &b){return a[1]<b[1];});
    return result;
}
BallisticSolution ballistic_intercept(const Point &start,const Point &target,const Point &velocity,
    double speed,double gravity,double minimum,double maximum,bool high,double max_lead) {
    BallisticSolution result; Point aim=target; max_lead=positive(max_lead);
    for(int i=0;i<5;++i) {
        const auto roots=ballistic_solutions(start,aim,speed,gravity,minimum,maximum); if(roots.empty()) return result;
        const auto &root=high?roots.back():roots.front(); if(max_lead && root[1]>max_lead+1.e-9) return result;
        if(i==4) {result.valid=true;result.aim=aim;result.pitch=root[0];result.time=root[1];return result;}
        for(size_t j=0;j<3;++j) aim[j]=target[j]+velocity[j]*root[1];
    }
    return result;
}
Point ballistic_position(const Point &start,double yaw,double pitch,double speed,double gravity,double time) {
    time=positive(time);const double horizontal=std::cos(pitch)*speed;
    const double x=std::sin(yaw)*horizontal,y=-std::sin(pitch)*speed,z=std::cos(yaw)*horizontal;
    return {{start[0]+x*time,start[1]+y*time-.5*std::abs(gravity)*time*time,start[2]+z*time}};
}

bool LaunchRecord::operator==(const LaunchRecord &v) const {
    return actor==v.actor && std::tie(edge.shot_seq,edge.group_seq,edge.index,edge.count,edge.shell,
        launch_time_us,pose.position,pose.yaw,pose.pitch,pose.roll,yaw,pitch,has_origin,artillery,
        has_shells_before,origin,velocity,gravity,maximum_distance,maximum_time_ms,shells_before,class_tag,proof)==
        std::tie(v.edge.shot_seq,v.edge.group_seq,v.edge.index,v.edge.count,v.edge.shell,
        v.launch_time_us,v.pose.position,v.pose.yaw,v.pose.pitch,v.pose.roll,v.yaw,v.pitch,v.has_origin,v.artillery,
        v.has_shells_before,v.origin,v.velocity,v.gravity,v.maximum_distance,v.maximum_time_ms,v.shells_before,v.class_tag,v.proof);
}
void Store::install(ActorKey key,const GunConfig &config,const AmmoState &ammo,std::int64_t seq,double factor) {
    demand(key.id>0 && actors_.count(key)==0); ammo.validate(config.shell_count);
    ActorState state(config);state.ammo=ammo;state.fire_seq=std::max<std::int64_t>(0,seq);
    state.gun.restore(seq,factor,false,0.,0.,1.,false,0,0);
    actors_.emplace(key,std::move(state));
}
bool Store::remove(ActorKey key) {
    // A caller must settle accepted launches before removing their owner.
    for(const auto &launch:launches_) if(launch.actor==key) return false;
    return actors_.erase(key)!=0;
}
ActorState &Store::at(ActorKey key) {auto found=actors_.find(key);demand(found!=actors_.end());return found->second;}
const ActorState &Store::at(ActorKey key) const {auto found=actors_.find(key);demand(found!=actors_.end());return found->second;}
void Store::prepare(ActorKey key,double dt,double factor,int requested) {
    auto &state=at(key); if(state.burst.active) return;
    state.gun.rescale(factor);state.gun.tick(dt);
    const int kind=state.gun.complete(factor,state.ammo.planned_rounds());
    const int selected=std::max(0,std::min(requested,state.gun.config.shell_count-1));
    state.ammo.stage(selected,kind>=0,kind==0);
}
std::vector<BurstEdge> Store::after_motion(ActorKey key,const AimInput &input,double move,double rotation,
    double turret,double factor,double aim_factor,bool apply_aim) {
    auto &state=at(key);
    if(apply_aim) {
        demand(state.has_aim);
        const double previous=state.aim.turret_yaw;
        advance_aim(state.aim,state.aim_config,input);
        turret=std::abs(wrap(state.aim.turret_yaw-previous))/std::max(input.dt,1.e-9);
    }
    state.gun.tick_dispersion(input.dt,move,rotation,turret,factor,aim_factor);
    return state.burst.advance(input.dt);
}
bool Store::begin(ActorKey key,int count,double interval,double factor) {
    auto &state=at(key);if(!state.ammo.can_fire()) return false;
    count=std::min(count,std::min(state.ammo.remaining[state.ammo.loaded],state.gun.clip));
    if(count<=0) return false;
    if(!state.burst.start(state.fire_seq+1,count,interval,state.ammo.loaded) || !state.gun.begin(count,factor)) {
        state.burst.cancel(0);return false;
    }
    return true;
}
bool Store::cancel(ActorKey key) {
    auto &state=at(key);if(!state.burst.active && state.gun.burst_remaining<=0) return false;
    const auto used=state.burst.group_seq>0?std::max<std::int64_t>(0,state.fire_seq-state.burst.group_seq+1):0;
    state.burst.cancel(int(std::min<std::int64_t>(used,state.burst.next_index)));
    state.gun.cancel();return true;
}
bool Store::enqueue(const LaunchRecord &launch) {
    demand(launch.actor.id>0 && launch.edge.shot_seq>0 && launch.launch_time_us>=0);
    for(const auto &previous:launches_) if(previous.actor==launch.actor && previous.edge.shot_seq==launch.edge.shot_seq) {
        demand(previous==launch);return false;
    }
    launches_.push_back(launch);return true;
}
bool Store::commit(ActorKey key,const LaunchRecord &launch,double factor) {
    auto &state=at(key);const auto &edge=launch.edge;
    if(key!=launch.actor || edge.shot_seq!=state.fire_seq+1 || edge.shot_seq!=edge.group_seq+edge.index ||
        edge.index<0 || edge.index>=edge.count || edge.shell!=state.ammo.loaded ||
        edge.group_seq!=state.burst.group_seq || edge.count!=state.burst.count ||
        edge.shell!=state.burst.shell || edge.index>=state.burst.next_index ||
        edge.final_round!=(edge.index+1>=edge.count) || !state.ammo.can_fire(edge.index>0)) return false;
    ActorState candidate=state;
    if(!candidate.gun.consume(edge.final_round) || !candidate.ammo.consume(edge.index>0)) return false;
    if(edge.final_round && candidate.ammo.requires_full()) candidate.gun.require_full();
    candidate.fire_seq=edge.shot_seq;candidate.gun.bloom(factor,edge.final_round);
    LaunchRecord frozen=launch;frozen.has_shells_before=true;
    frozen.shells_before=std::accumulate(candidate.ammo.remaining.begin(),candidate.ammo.remaining.end(),0)+1;
    if(!enqueue(frozen)) return false;
    state=std::move(candidate);return true;
}
bool Store::ack(ActorKey key,std::int64_t seq) {
    for(auto it=launches_.begin();it!=launches_.end();++it) if(it->actor==key) {
        if(it->edge.shot_seq!=seq) return false;
        launches_.erase(it);return true;
    }
    return false;
}
} }
