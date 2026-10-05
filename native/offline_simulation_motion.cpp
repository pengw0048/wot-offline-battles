#include "offline_simulation_motion.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>
namespace offline_simulation {
    namespace motion {
        namespace {
            struct CallScope {
                bool&active;
                explicit CallScope(bool&value):active(value){
                    if(active)throw std::logic_error("reentrant physical mutation");
                    active=true;
                }
                ~CallScope(){
                    active=false;
                }
            };
            void present(State&s,int index){
                s.presence|=std::uint64_t(1)<<index;
            }
            void absent(State&s,int index){
                s.presence&=~(std::uint64_t(1)<<index);
            }
            constexpr double PI=3.14159265358979323846;
            double clamp(double x,double a,double b) {
                return std::max(a,std::min(b,x));
            }
            double angle(double x) {
                while(x>PI)x-=2.*PI;
                while(x<-PI)x+=2.*PI;
                return x;
            }
            double retained(double speed,double forced) {
                return speed*forced>0.?std::copysign(std::min(std::abs(speed),std::abs(forced)),speed):0.;
            }
            double interpolate(double x,double lo,double hi,double a,double b) {
                return x>=hi?b:x<=lo?a:a+(x-lo)/(hi-lo)*(b-a);
            }
            double longitudinal_grip(const Tuning&t,double pitch) {
                return interpolate(std::cos(pitch),t.lng_min_y,t.lng_full_y,t.lng_min,t.lng_full)*t.drive_traction;
            }
            double lateral_grip(const Tuning&t,double ny) {
                return interpolate(clamp(ny,0.,1.),t.side_min_y,t.side_full_y,t.side_min,t.side_full);
            }
            double cohesion(const Tuning&t,double ny) {
                double v=t.cohesion;
                if(ny<t.coh_decay_y)v-=t.coh_decay_factor*std::pow(t.coh_decay_y-ny,t.coh_decay_pow);
                if(ny<t.slope_coh_y)v-=t.slope_coh;
                return std::max(v,t.coh_bound);
            }
            double rolling(const Tuning&t,const Descriptor&p,bool steering) {
                return p.friction*t.gravity_factor*p.resistance[0]*(steering?t.steer_resist:1.);
            }
            double grip(const Tuning&t,double pitch) {
                const double ny=std::cos(pitch);
                return cohesion(t,ny)*t.gravity*std::max(ny,.1);
            }
            double motor_force(const Tuning&t,const Descriptor&p,double v,double throttle,double pitch) {
                if(!throttle)return 0.;
                double power=p.power*t.power_factor*p.native_power_ratio;
                if(throttle<0.)power*=t.reverse_power;
                const double force=power/std::max(std::abs(v),t.engine_min_v);
                const double limit=longitudinal_grip(t,pitch)*p.mass*t.gravity*std::max(std::cos(pitch),.1);
                return std::min(force,limit)*throttle;
            }
            double drive_scale(double throttle,bool steering) {
                return steering?1./(1.+std::min(1.,std::abs(throttle))):1.;
            }
            double capped(const Tuning&t,const Descriptor&p,double v) {
                return clamp(v,-p.reverse_limit*t.overspeed,p.forward_limit*t.overspeed);
            }
            double longitudinal(const Tuning&t,const Descriptor&p,double v,double throttle,bool steering,double pitch,double dt,bool airborne,bool brake) {
                if(airborne)return v;
                const double g=t.gravity*std::sin(pitch),hold=grip(t,pitch);
                if(brake&&throttle*v<0.) {
                    const double nv=v+(g-(v>0.?1.:-1.)*std::min(p.brake,hold))*dt;
                    return v*nv<=0.?0.:capped(t,p,nv);
                }
                const double rr=rolling(t,p,steering);
                double acceleration=0.,ef=0.;
                if(throttle!=0.) {
                    ef=motor_force(t,p,v,throttle,pitch)*drive_scale(throttle,steering)/p.mass;
                    const double ny=std::cos(pitch);
                    const bool cant=throttle*g<0.&&longitudinal_grip(t,pitch)*t.gravity*std::max(ny,.1)<std::abs(g)+rr;
                    if(cant)ef=0.;
                    acceleration=ef+g-rr*clamp(v/.08,-1.,1.);
                    if(cant&&std::abs(v)>.05)acceleration+=(v<0.?1.:-1.)*t.slide_kinetic*t.gravity*std::max(ny,.1);
                }
                else if(std::abs(v)<.02) {
                    const double perch=t.hold_tan*t.gravity*std::max(std::cos(pitch),.1);
                    if(std::abs(g)<=perch)return 0.;
                    acceleration=g-(g>0.?perch:-perch);
                }
                else {
                    const double downhill=std::max(0.,std::tan(pitch)*(v>0.?1.:-1.));
                    const double fade=clamp((downhill-.8*t.hold_tan)/(.2*t.hold_tan),0.,1.);
                    const double target=t.coast_share*std::min(p.brake,hold);
                    const double resistance=rr+(1.-fade)*std::max(0.,target-rr);
                    acceleration=g-(v>0.?resistance:-resistance);
                }
                const double grade=v>0.?-pitch:pitch;
                if(std::abs(v)>.5&&grade>0.&&std::tan(grade)>t.slip_tan)         acceleration-=(v>0.?1.:-1.)*t.slip_drag*(std::tan(grade)-t.slip_tan)*t.gravity;
                double nv=v+acceleration*dt;
                if(!throttle&&std::abs(g)<=hold&&v!=0.&&(v>0.)!=(nv>0.))nv=0.;
                if(throttle!=0.&&ef*throttle>0.) {
                    const double sign=throttle>0.?1.:-1.;
                    const double passive=v+(acceleration-ef)*dt;
                    const double room=std::max(0.,(throttle>0.?p.forward_limit:p.reverse_limit)-sign*passive);
                    nv=passive+sign*std::min(std::abs(ef)*dt,room);
                }
                return capped(t,p,nv);
            }
            double traverse(const Tuning&t,const Descriptor&p,double omega,double turn,double v,double dt,double throttle) {
                const double limit=p.rotation_speed/(1.+std::abs(v)/std::max(p.forward_limit,.1)*t.speed_rot_cost);
                const double target=turn*(throttle<0.?-1.:1.)*limit;
                const double diff=target-omega,ramp=limit/t.angular_time;
                if(std::abs(diff)<ramp*dt)omega=target;
                else omega+=ramp*dt*(diff>0.?1.:-1.);
                return !turn&&std::abs(omega)<.01?0.:omega;
            }
            std::array<double,2> push_grip(const Tuning&t,const Descriptor&p,const State&s,bool moving) {
                const double ny=std::cos(s.pose.pitch)*std::cos(s.pose.roll),floor=std::max(ny,.1);
                const double rr=rolling(t,p,false),hold=t.hold_tan*t.gravity*floor;
                return {
                    {moving?rr:std::max(rr,hold),std::max(rr,t.hold_tan*lateral_grip(t,ny)*t.gravity*floor)}
                };
            }
            double bleed(double v,double budget) {
                return budget<=0.?v:v>budget?v-budget:v<-budget?v+budget:0.;
            }
            void friction(const Tuning&t,const Descriptor&p,State&s,double dt) {
                const auto grip=push_grip(t,p,s,s.alive&&(s.speed||s.movement_dir));
                const double sn=std::sin(s.pose.yaw),cs=std::cos(s.pose.yaw);
                const double forward=bleed(s.push_x*sn+s.push_z*cs,grip[0]*dt);
                const double side=bleed(s.push_x*cs-s.push_z*sn,grip[1]*dt);
                s.push_x=forward*sn+side*cs;
                s.push_z=forward*cs-side*sn;
            }
            void wreck_friction(const Tuning&t,const Descriptor&p,State&s,double dt) {
                if(s.airborne||dt<=0.)return;
                const auto grip=push_grip(t,p,s,false);
                const double w=p.shape[0],l=p.shape[1];
                const double inertia=(w*w+l*l)/3.,sn=std::sin(s.pose.yaw),cs=std::cos(s.pose.yaw);
                double v[3]={s.push_x*sn+s.push_z*cs,s.push_x*cs-s.push_z*sn,s.push_yaw};
                struct Row {
                    int axis;
                    double lever,budget,impulse;
                };
                std::vector<Row> rows;
                for(double x:{-w,w})for(double z:{-l/2.,l/2.}) {
                    rows.push_back({0,x,grip[0]*dt/4.,0.});
                    rows.push_back({1,-z,grip[1]*dt/4.,0.});
                }
                for(int pass=0;pass<t.constraint_iterations;++pass) {
                    double largest=0.;
                    for(auto&r:rows) {
                        double delta=-(v[r.axis]+r.lever*v[2])/(1.+r.lever*r.lever/inertia);
                        const double updated=clamp(r.impulse+delta,-r.budget,r.budget);
                        delta=updated-r.impulse;
                        r.impulse=updated;
                        v[r.axis]+=delta;
                        v[2]+=delta*r.lever/inertia;
                        largest=std::max(largest,std::abs(delta));
                    }
                    if(largest<1.e-9)break;
                }
                for(double &value:v)if(std::abs(value)<1.e-9)value=0.;
                s.push_x=v[0]*sn+v[1]*cs;
                s.push_z=v[0]*cs-v[1]*sn;
                s.push_yaw=v[2];
            }
            offline_math::Body geometry(ActorKey k,const Entry&e) {
                const auto&p=e.state.pose;
                return {k.id,true,p.position[0],p.position[1],p.position[2],p.yaw,p.pitch,p.roll,e.descriptor.shape};
            }
            int forward_world(void*raw,std::uint32_t op,const WorldRequest*q,std::uint32_t n,WorldResponse*r) {
                return static_cast<Engine*>(raw)->dispatch_world(op,q,n,r);
            }
        }
        Entry&Store::entry(ActorKey k) {
            auto it=actors_.find(k);
            if(it==actors_.end())throw std::invalid_argument("unknown motion actor");
            return it->second;
        }
        void Store::configure(Tuning t) {
            if(in_slice_)throw std::logic_error("cannot replace tuning during a slice");
            if(!(t.gravity>0.&&t.angular_time>0.&&t.hold_tan>0.&&t.engine_min_v>0.&&t.overspeed>=1.))throw std::invalid_argument("invalid installed physics tuning");
            tuning_=std::move(t);
            configured_=true;
        }
        void Store::install(ActorKey k,State s,Descriptor d,const Descriptor* siege) {
            if(in_slice_)throw std::logic_error("cannot install a descriptor during a slice");
            if(d.detailed_suspension)throw std::invalid_argument("detailed damper is not the released Test8L motion path");
            if(!(d.mass>0.&&d.forward_limit>0.&&d.reverse_limit>0.&&d.resistance[0]>0.&&d.shape[0]>0.&&d.shape[1]>0.))throw std::invalid_argument("invalid installed motion descriptor");
            Entry e;
            e.state=s;
            e.normal_descriptor=d;
            e.has_siege=siege!=nullptr;
            if(siege)e.siege_descriptor=*siege;
            e.descriptor=s.siege_state==2&&siege?*siege:d;
            actors_[k]=e;
        }
        void Store::patch(ActorKey k,const State&s) {
            if(in_call_)throw std::logic_error("external rebase during engine query");
            entry(k).state=s;
        }
        void Store::patch_fields(ActorKey k,const State&input,unsigned mask) {
            if(in_call_)throw std::logic_error("external patch during engine query");
            auto&s=entry(k).state;
            if(mask&1)s.pose=input.pose;
            if(mask&2){
                s.speed=input.speed;
                s.turn_speed=input.turn_speed;
                s.vertical_speed=input.vertical_speed;
                s.push_x=input.push_x;
                s.push_z=input.push_z;
                s.push_yaw=input.push_yaw;
                s.contact_forward=input.contact_forward;
                s.movement_dir=input.movement_dir;
                s.rotation_dir=input.rotation_dir;
                s.service_brake=input.service_brake;
                s.contact_dynamics=input.contact_dynamics;
                s.motor_turn=input.motor_turn;
                for(int i:{1,2,3,4,5,6,7,17,18,19,29,30}){
                    absent(s,i);
                    s.presence|=input.presence&(std::uint64_t(1)<<i);
                }
            }
            if(mask&4){
                s.alive=input.alive;
                s.death_reason=input.death_reason;
                for(int i:{23,26}){
                    absent(s,i);
                    s.presence|=input.presence&(std::uint64_t(1)<<i);
                }
            }
            if(mask&8){
                s.pose.pitch=input.pose.pitch;
                s.terrain_pitch=input.terrain_pitch;
                absent(s,0);
                s.presence|=input.presence&1;
            }
            if(mask&16){
                s.siege_state=input.siege_state;
                s.siege_remaining=input.siege_remaining;
                s.siege_total=input.siege_total;
                auto&e=entry(k);
                e.descriptor=s.siege_state==2&&e.has_siege?e.siege_descriptor:e.normal_descriptor;
            }
        }
        void Store::descriptor_patch(ActorKey k,Descriptor normal,const Descriptor*siege,int mode,double remaining,double total) {
            if(normal.detailed_suspension||(siege&&siege->detailed_suspension))throw std::logic_error("invalid descriptor patch boundary");
            if(!(normal.mass>0.&&normal.forward_limit>0.&&normal.reverse_limit>0.&&normal.shape[0]>0.&&normal.shape[1]>0.))throw std::invalid_argument("invalid descriptor patch");
            auto&e=entry(k);
            e.normal_descriptor=normal;
            e.has_siege=siege!=nullptr;
            if(siege)e.siege_descriptor=*siege;
            for(int i:{15,16,21})present(e.state,i);
            e.state.siege_state=mode;
            e.state.siege_remaining=remaining;
            e.state.siege_total=total;
            e.descriptor=mode==2&&siege?*siege:normal;
        }
        const Entry&Store::observation(ActorKey k)const {
            const auto found=actors_.find(k);
            if(found==actors_.end())throw std::logic_error("unknown motion actor");
            return found->second;
        }
        const State&Store::snapshot(ActorKey k)const {
            return actors_.at(k).state;
        }
        const Descriptor&Store::descriptor(ActorKey k)const{
            return actors_.at(k).descriptor;
        }
        void Store::begin_slice(TickToken token,double dt,double now,const std::vector<ActorKey>&order,std::vector<offline_contact::Body>humans,std::vector<std::int64_t>contact_order,const std::vector<ActorKey>&settle_order) {
            if(!configured_||in_slice_||!(dt>0.&&dt<=.2+1.e-9)||!std::isfinite(now))throw std::logic_error("invalid motion slice boundary");
            if(token_.sequence&& (token.round!=token_.round||token.generation!=token_.generation||token.sequence<=token_.sequence))throw std::logic_error("stale motion slice token");
            std::map<ActorKey,bool>seen;
            for(auto k:order){
                entry(k);
                if(seen[k])throw std::invalid_argument("duplicate motion actor");
                seen[k]=true;
            }
            if(settle_order.size()!=order.size())throw std::invalid_argument("incomplete settlement order");
            std::map<ActorKey,bool>settled;
            for(auto k:settle_order){
                if(!seen[k]||settled[k])throw std::invalid_argument("invalid settlement order");
                settled[k]=true;
            }
            settle_order_=settle_order;
            token_=token;
            dt_=dt;
            now_=now;
            order_=order;
            humans_=std::move(humans);
            contact_order_=std::move(contact_order);
            next_actor_=0;
            effects_.clear();
            contact_rows_.clear();
            in_slice_=true;
            for(auto k:order_) {
                auto&e=entry(k);
                e.prepared=e.advanced=e.siege_locked=false;
                e.tick_pose=e.state.pose;
                e.state.query_failed=false;
                e.support_blocked=e.ballistic=e.vertical_cohort=e.pose_rollback=false;
                e.diagnostics=false;
                e.trace=DriveTrace{};
                e.passive_forward=0.;
            }
        }
        void Store::emit(Engine&engine,ActorKey k,int kind,double value) {
            Effect event{k,kind,value};
            effects_.push_back(event);
            const auto receipt=engine.effect(event);
            entry(k).state=receipt.state;
        }
        void Store::fail(ActorKey k) {
            auto&e=entry(k);
            auto&s=e.state;
            s.query_failed=true;
            s.pose.position=e.tick_pose.position;
            s.pose.yaw=e.tick_pose.yaw;
            s.speed=s.turn_speed=s.push_x=s.push_z=0.;
            s.movement_dir=s.rotation_dir=0;
            e.drive_pending=false;
            e.support_blocked=true;
        }
        const State&Store::prepare_actor(ActorKey k,Engine&engine) {
            CallScope scope(in_call_);
            try{
                return prepare_impl(k,engine);
            }
            catch(const OperationFailed&){
                fail(k);
                return entry(k).state;
            }
        }
        const State&Store::advance_actor(ActorKey k,const Command&command,Engine&engine) {
            CallScope scope(in_call_);
            try{
                return advance_impl(k,command,engine);
            }
            catch(const OperationFailed&){
                fail(k);
                return entry(k).state;
            }
        }
        const State&Store::prepare_impl(ActorKey k,Engine&engine) {
            if(!in_slice_||next_actor_>=order_.size()||order_[next_actor_]!=k)throw std::logic_error("actor preparation order changed");
            auto&e=entry(k);
            auto&s=e.state;
            if(e.prepared)throw std::logic_error("actor already prepared");
            e.prepared=true;
            if(!s.alive){
                e.rollback=s;
                return s;
            }
            e.siege_locked=s.siege_state==1||s.siege_state==3;
            // caller installs actual enum values; switching flags are explicit below.
            if(s.alive) {
                present(s,10);
                s.drown_check+=dt_;
                if(s.drown_check>=tuning_.drown_probe) {
                    present(s,11);
                    present(s,12);
                    present(s,31);
                    const double elapsed=s.drown_check;
                    s.drown_check=0.;
                    s.water_depth=engine.water(k,s.pose);
                    const auto&v=e.descriptor.water_sensor;
                    const double height=(v[0]*std::sin(s.pose.roll)+v[1]*std::cos(s.pose.roll))*std::cos(s.pose.pitch)-v[2]*std::sin(s.pose.pitch);
                    s.drowning=s.water_depth>=0.&&s.water_depth>height;
                    s.drown_time=s.drowning?s.drown_time+elapsed:0.;
                    if(s.drown_time>tuning_.drown_seconds){
                        s.alive=false;
                        s.drowning=false;
                        s.death_reason=5;
                        present(s,23);
                        s.drown_time=tuning_.drown_seconds;
                        emit(engine,k,3);
                    }
                }
                if(s.alive) {
                    const double up=std::cos(s.pose.pitch)*std::cos(s.pose.roll);
                    const int level=up<=tuning_.danger_cos?2:up<=tuning_.warning_cos?1:0;
                    if(!level){
                        for(int index:{13,14,22,32})present(s,index);
                        s.overturn_check=s.overturn_time=0.;
                        s.overturn_level=0;
                        s.overturned=false;
                    }
                    else {
                        present(s,13);
                        s.overturn_check+=dt_;
                        if(s.overturn_check+1.e-6>=tuning_.overturn_ignore) {
                            present(s,14);
                            present(s,22);
                            present(s,32);
                            if(level!=s.overturn_level){
                                s.overturn_level=level;
                                s.overturn_time=0.;
                            }
                            s.overturned=level==2;
                            if(level==2){
                                s.movement_dir=s.rotation_dir=0;
                                s.turn_speed=0.;
                                s.overturn_time+=dt_;
                                if(s.overturn_time+1.e-6>=tuning_.overturn_death){
                                    s.alive=false;
                                    s.death_reason=7;
                                    present(s,23);
                                    s.overturn_time=tuning_.overturn_death;
                                    emit(engine,k,4);
                                }
                            }
                            else s.overturn_time=0.;
                        }
                    }
                }
            }
            if(s.alive) {
                present(s,15);
                present(s,16);
                present(s,21);
                if(!e.has_siege) {
                    e.siege_locked=false;
                    s.siege_state=0;
                    s.siege_remaining=s.siege_total=0.;
                }
                else if(e.siege_locked) {
                    s.siege_remaining=std::max(0.,s.siege_remaining-dt_);
                    if(s.siege_remaining<=1.e-9) {
                        s.siege_state=s.siege_state==1?2:0;
                        s.siege_remaining=s.siege_total=0.;
                        e.descriptor=s.siege_state==2?e.siege_descriptor:e.normal_descriptor;
                        emit(engine,k,6,s.siege_state);
                    }
                }
                else s.siege_remaining=s.siege_total=0.;
            }
            e.tick_pose=s.pose;
            e.rollback=s;
            e.tick_safe=engine.safe(k,s.pose);
            e.attempted_yaw=s.pose.yaw;
            return s;
        }
        std::vector<offline_math::Body>Store::peers(ActorKey owner,bool drive)const {
            std::vector<offline_math::Body>out;
            for(auto k:(drive?order_:settle_order_))if(k!=owner)out.push_back(geometry(k,actors_.at(k)));
            for(const auto&h:humans_)out.push_back({h.id,h.has(offline_contact::HasY),h.x,h.y,h.z,h.yaw,h.pitch,h.roll,h.shape});
            return out;
        }
        int Store::world(ActorKey k,Entry&e,double yaw,double speed,double dt,bool passive,bool commit,Engine&engine) {
            if(!speed||dt<=0.)return 0;
            auto&s=e.state;
            WorldSnapshot q{
            };
            for(int i=0;i<3;++i)q.position[i]=s.pose.position[i];
            q.yaw=s.pose.yaw;
            q.speed=speed;
            q.dt=dt;
            q.pitch=s.terrain_pitch;
            q.roll=s.pose.roll;
            q.passive=passive;
            q.motion_yaw=speed>=0.?yaw:yaw+PI;
            q.airborne=s.airborne;
            for(int i=0;i<4;++i)q.bounds[i]=e.descriptor.bounds[i];
            for(int i=0;i<2;++i)q.heights[i]=e.descriptor.heights[i];
            Pose end=s.pose;
            end.position[0]+=std::sin(yaw)*speed*dt;
            end.position[2]+=std::cos(yaw)*speed*dt;
            if(!engine.turret_guard(k,s.pose,end))return 1;
            const bool active=!passive&&!s.airborne&&s.movement_dir*speed>0.;
            auto run=[&](bool allow_commit,bool receipt_allowed) {
                if(engine.begin_world(k,s,e.descriptor,q,allow_commit,active,receipt_allowed)){
                    engine.end_world();
                    return 3;
                }
                int status;
                try {
                    status=wot_world_run(&q,forward_world,&engine);
                }
                catch(...){
                    engine.end_world();
                    throw;
                }
                engine.end_world();
                if(status<0)throw OperationFailed();
                return status;
            };
            const int before=run(commit,true);
            if(before==3)return 0;
            if(before==1)return 1;
            if(s.airborne)return before==0?0:1;
            const auto catalog=engine.catalog(k,s,speed,q.motion_yaw,dt,passive,commit);
            if(catalog.status<0)return before==0?0:1;
            if(catalog.status==1)return 1;
            if(catalog.accepted){
                const int after=run(false,false);
                if(after!=0&&after!=2)return 1;
            }
            return catalog.status;
        }
        const State&Store::advance_impl(ActorKey k,const Command&command,Engine&engine) {
            if(!in_slice_||next_actor_>=order_.size()||order_[next_actor_]!=k)throw std::logic_error("actor drive order changed");
            auto&e=entry(k);
            if(!e.prepared||e.advanced)throw std::logic_error("actor drive requires preparation once");
            e.advanced=true;
            ++next_actor_;
            auto&s=e.state;
            const auto&p=e.descriptor;
            const auto&t=tuning_;
            if(!s.alive||s.query_failed)return s;
            e.siege_locked=e.siege_locked||command.siege_locked;
            e.diagnostics=command.diagnostics;
            if(e.siege_locked){
                s.speed=s.push_x=s.push_z=s.turn_speed=0.;
                s.movement_dir=s.rotation_dir=0;
            }
            double throttle=(s.overturned||e.siege_locked)?0.:command.throttle;
            double turn=(s.overturned||e.siege_locked)?0.:command.turn;
            present(s,9);
            present(s,7);
            present(s,29);
            absent(s,19);
            s.movement_dir=throttle>.01?1:throttle<-.01?-1:0;
            s.rotation_dir=std::abs(turn)>.01?(turn>0.?1:-1):0;
            s.motor_turn=0;
            s.rotation_blocked=false;
            double omega=e.siege_locked?0.:traverse(t,p,s.turn_speed,turn,s.speed,dt_,throttle);
            const double old=s.pose.yaw;
            double candidate=angle(old+omega*dt_);
            if(std::abs(angle(candidate-old))>1.e-9) {
                auto rotating=geometry(k,e);
                rotating.pitch=rotating.roll=0.;
                auto others=peers(k,true);
                for(auto&body:others)body.pitch=body.roll=0.;
                const double fraction=offline_math::rotation(rotating,candidate,0.,{0.,0.}
                ,others);
                if(fraction<1.){
                    candidate=old+angle(candidate-old)*fraction;
                    omega=0.;
                    s.rotation_blocked=true;
                    present(s,33);
                    present(s,19);
                    s.motor_turn=s.rotation_dir;
                    s.rotation_dir=0;
                }
            }
            Pose turned=s.pose;
            turned.yaw=candidate;
            if(!engine.pose_guard(k,s.pose,turned,false)||(std::abs(angle(candidate-old))>1.e-8&&!engine.turret_guard(k,s.pose,turned))){
                candidate=old;
                omega=0.;
                s.rotation_dir=0;
            }
            s.pose.yaw=candidate;
            s.turn_speed=omega;
            e.attempted_yaw=candidate+(command.travel_sign<0.?PI:0.);
            bool path=command.path_clear;
            const bool corridor_clear=engine.corridor(k,s,e.attempted_yaw,command.wet_escape,command.allow_shallow);
            if(!corridor_clear){
                path=false;
                throttle=0.;
                s.movement_dir=0;
            }
            const double previous=s.speed,forced=retained(previous,s.contact_forward);
            const int direction=throttle>0.?1:throttle<0.?-1:0,prior=s.direction_command>0.?1:s.direction_command<0.?-1:0;
            s.service_brake=direction&&direction*(previous-forced)<0.&&(s.service_brake||direction!=prior);
            s.direction_command=throttle;
            double speed=e.siege_locked?0.:longitudinal(t,p,previous,throttle,std::abs(turn)>.01,command.slope_pitch,dt_,s.airborne,s.service_brake);
            e.passive_forward=forced?retained(speed,speed-longitudinal(t,p,previous-forced,throttle,std::abs(turn)>.01,command.slope_pitch,dt_,s.airborne,s.service_brake)):0.;
            e.trace.speed=speed;
            e.trace.pitch=command.slope_pitch;
            e.trace.throttle=throttle;
            e.trace.baked_veto=!corridor_clear;
            e.trace.path_clear=path;
            e.trace.frozen=command.pose_frozen;
            speed-=e.passive_forward;
            s.contact_forward=0.;
            s.last_drive_pitch=command.slope_pitch;
            bool hard=!path&&command.generic_collision&&!command.exact_collision;
            if(!path){
                if(!hard)speed*=.2;
                s.has_soft_contact=false;
            }
            int status=0;
            bool resolved=false;
            const double impact_speed=speed;
            if(path&&!command.pose_frozen&&std::abs(speed)>.0001) {
                resolved=true;
                status=world(k,e,s.pose.yaw,speed,dt_,false,true,engine);
                if(status==1){
                    hard=true;
                    emit(engine,k,2,impact_speed);
                    emit(engine,k,5,s.pose.yaw+(speed<0.?PI:0.));
                    path=false;
                    s.has_soft_contact=false;
                }
                else if(status==2){
                    path=false;
                    const double contact=s.has_soft_contact?s.soft_contact_speed:speed;
                    speed=std::copysign(std::min(std::abs(contact),std::abs(speed)),speed);
                    s.soft_contact_speed=speed;
                    s.has_soft_contact=true;
                    s.hard_grind=1;
                }
                else{
                    s.has_soft_contact=false;
                }
            }
            bool deflected=false;
            if(hard&&!s.airborne) {
                double chosen=0.;
                bool found=false;
                for(double delta:t.glancing_yaws) {
                    const double yaw=s.pose.yaw+delta;
                    const int probe=world(k,e,yaw,speed,dt_,true,false,engine);
                    if(probe==0||probe==3) {
                        const int committed=world(k,e,yaw,speed,dt_,true,true,engine);
                        if(committed==0||committed==3){
                            chosen=yaw;
                            found=true;
                        }
                        break;
                    }
                }
                if(found){
                    if(!s.hard_grind)speed*=t.hard_entry;
                    speed*=std::pow(t.hard_slide,dt_*60.);
                    s.pose.position[0]+=std::sin(chosen)*speed*dt_;
                    s.pose.position[2]+=std::cos(chosen)*speed*dt_;
                    deflected=true;
                }
                else{
                    speed*=std::pow(t.hard_brake,dt_*60.);
                    if(std::abs(speed)<t.hard_stop)speed=0.;
                }
                s.hard_grind=t.hard_ticks;
            }
            engine.motion_finished(k,status,impact_speed,speed,hard&&!s.airborne,e.tick_pose,                            s.pose.yaw+(impact_speed<0.?PI:0.),resolved);
            s.speed=speed;
            if(s.siege_state==2&&p.siege_limit>0.)s.speed=clamp(s.speed,-p.siege_limit,p.siege_limit);
            if(!deflected&&path&&!command.pose_frozen){
                s.pose.position[0]+=std::sin(s.pose.yaw)*s.speed*dt_;
                s.pose.position[2]+=std::cos(s.pose.yaw)*s.speed*dt_;
                if(std::abs(s.speed)>.0001)s.hard_grind=std::max(0,s.hard_grind-1);
            }
            e.trace.world_status=status;
            e.trace.hard_contact=hard;
            e.trace.integrated=s.pose;
            e.trace.world_speed=s.speed;
            return s;
        }
        bool Store::vertical(ActorKey k,Entry&e,double dt,const Pose&tick,Engine&engine) {
            auto&s=e.state;
            const auto&t=tuning_;
            const State before=s;
            const double tangent=std::max(0.,s.speed*std::tan(clamp(s.last_drive_pitch,-t.pitch_limit,t.pitch_limit))*dt);
            const double gap=clamp(t.follow_base+tangent+t.gravity*dt*dt,t.follow_min,t.follow_max);
            auto centre=engine.ground(k,{s.pose.position});
            if(centre.size()!=1)throw std::runtime_error("invalid ground frontier width");
            Support ground=centre[0],middle=centre[0];
            auto straddled=[&](double follow)->Support {
                const double sn=std::sin(s.pose.yaw),cs=std::cos(s.pose.yaw),w=std::max(.3,e.descriptor.shape[0]),l=std::max(1.5,e.descriptor.shape[1]);
                std::vector<Point>points;
                for(auto offset:std::vector<offline_math::Vec>{
                    {sn*l,cs*l}
                    ,{-sn*l,-cs*l}
                    ,{cs*w,-sn*w}
                    ,{-cs*w,sn*w}
                }){
                    auto p=s.pose.position;
                    p[0]+=offset.x;
                    p[2]+=offset.z;
                    points.push_back(p);
                }
                const auto supports=engine.ground(k,points);
                if(supports.size()!=4)throw std::runtime_error("invalid straddle frontier width");
                Support best;
                for(int pair=0;pair<2;++pair){
                    const auto&a=supports[pair*2],&b=supports[pair*2+1];
                    if(a.present&&b.present&&a.height>=s.pose.position[1]-follow&&b.height>=s.pose.position[1]-follow&&a.height<=s.pose.position[1]+.12&&b.height<=s.pose.position[1]+.12){
                        const double height=std::max(a.height,b.height);
                        if(!best.present||height>best.height)best={true,height};
                    }
                }
                return best;
            };
            if(middle.present&&s.pose.position[1]-middle.height>std::min(gap,t.follow_min)){
                auto bridge=straddled(std::min(gap,t.follow_min));
                if(bridge.present&&bridge.height>middle.height)ground=middle=bridge;
            }
            else if(!middle.present&&!s.grounded){
                const double sn=std::sin(s.pose.yaw),cs=std::cos(s.pose.yaw),l=std::max(1.5,e.descriptor.shape[1]);
                auto front=s.pose.position,back=front;
                front[0]+=sn*l;
                front[2]+=cs*l;
                back[0]-=sn*l;
                back[2]-=cs*l;
                const auto ends=engine.ground(k,{front,back});
                for(auto v:ends)if(v.present&&(!ground.present||v.height>ground.height))ground=v;
            }
            else if(!middle.present)ground=straddled(t.follow_min);
            if(e.diagnostics){
                e.trace.centre=middle;
                e.trace.highest=ground;
                e.trace.grounded_before=s.grounded;
                e.trace.has_support=true;
            }
            bool blocked=false;
            double impact=0.;
            if(ground.present) {
                double max_climb=std::max(.6,std::abs(s.speed)*dt*2.5);
                const bool rise=s.grounded&&middle.present&&middle.height-s.pose.position[1]>std::min(max_climb,.85)+.02;
                auto support_path=[&](){
                    const double dx=s.pose.position[0]-tick.position[0],dz=s.pose.position[2]-tick.position[2],distance=std::hypot(dx,dz),rise=middle.height-tick.position[1];
                    if(distance<=.0001||rise<=0.||rise>distance*.55+.02)return false;
                    const int segments=std::max(2,static_cast<int>(std::ceil(distance/1.5)));
                    if(segments>5)return false;
                    const double maximum=distance/segments*.55+.02;
                    double previous=tick.position[1];
                    for(int i=1;i<segments;++i){
                        const double fraction=static_cast<double>(i)/segments;
                        Point point{
                            {tick.position[0]+dx*fraction,tick.position[1]+rise*fraction,tick.position[2]+dz*fraction}
                        };
                        auto sampled=engine.ground(k,{point});
                        if(sampled.size()!=1||!sampled[0].present||std::abs(sampled[0].height-previous)>maximum)return false;
                        previous=sampled[0].height;
                    }
                    return std::abs(middle.height-previous)<=maximum;
                };
                const bool continuous=rise&&support_path();
                if(continuous)max_climb=std::max(max_climb,std::max(0.,middle.height-tick.position[1]));
                if(e.diagnostics){
                    e.trace.support_limit=max_climb;
                    e.trace.rise_obstacle=rise;
                    e.trace.rise_continuous=continuous;
                    e.trace.has_limit=true;
                }
                const double land=middle.present?middle.height:ground.height;
                if(!s.grounded){
                    s.pose.position[1]=land;
                    s.vertical_speed=0.;
                    s.airborne=false;
                    s.grounded=true;
                }
                else if(rise&&!continuous){
                    s.pose.position=tick.position;
                    s.speed=0.;
                    s.movement_dir=s.rotation_dir=0;
                    s.push_x=s.push_z=0.;
                    s.vertical_speed=0.;
                    s.airborne=false;
                    s.turn_speed=0.;
                    blocked=true;
                }
                else if(s.pose.position[1]<ground.height-.002||(s.pose.position[1]<=ground.height&&s.vertical_speed<=0.)||(!s.airborne&&ground.height>=s.pose.position[1]+s.vertical_speed*dt-t.gravity*dt*dt-.002)){
                    impact=s.airborne?s.vertical_speed:0.;
                    const double y=s.pose.position[1];
                    s.pose.position[1]=y<ground.height?y+std::min(ground.height-y,max_climb):ground.height;
                    s.vertical_speed=dt>0.&&!s.airborne?(s.pose.position[1]-y)/dt:0.;
                    s.airborne=false;
                }
                else {
                    s.airborne=true;
                    const int count=std::min(8,std::max(1,static_cast<int>(std::abs(s.vertical_speed*dt)/.5)+1));
                    const double sub=dt/count;
                    for(int i=0;i<count;++i){
                        s.vertical_speed-=t.gravity*sub;
                        s.pose.position[1]+=s.vertical_speed*sub;
                        if(s.pose.position[1]<=land){
                            impact=s.vertical_speed;
                            s.pose.position[1]=land;
                            s.vertical_speed=0.;
                            s.airborne=false;
                            break;
                        }
                    }
                }
            }
            else if(s.grounded){
                s.airborne=true;
                s.vertical_speed-=t.gravity*dt;
                s.pose.position[1]+=s.vertical_speed*dt;
            }
            else{
                s.vertical_speed=0.;
                s.airborne=false;
            }
            if(!engine.turret_guard(k,before.pose,s.pose)){
                s=before;
                s.speed=0.;
                s.movement_dir=s.rotation_dir=0;
                s.push_x=s.push_z=0.;
                s.turn_speed=0.;
                blocked=true;
                impact=0.;
            }
            if(blocked)emit(engine,k,5,e.attempted_yaw);
            if(impact<0.)emit(engine,k,1,impact);
            return blocked;
        }
        void Store::push(ActorKey k,Entry&e,const offline_contact::Row&r,double dt,bool advance_forward,bool apply_friction,Engine&engine) {
            auto&s=e.state;
            const auto&p=e.descriptor;
            const double old_speed=s.speed;
            const double sn=std::sin(s.pose.yaw),cs=std::cos(s.pose.yaw),forward=s.alive?r.delta_velocity[0]*sn+r.delta_velocity[1]*cs:0.;
            if(r.delta_velocity[0]||r.delta_velocity[1]){
                s.contact_dynamics=true;
                present(s,30);
            }
            present(s,7);
            s.speed+=forward;
            s.contact_forward=retained(s.speed,s.contact_forward+forward);
            s.push_x+=r.delta_velocity[0]-forward*sn;
            s.push_z+=r.delta_velocity[1]-forward*cs;
            if(apply_friction&&!s.airborne)friction(tuning_,p,s,dt);
            offline_math::Vec movement={r.correction[0]+(advance_forward?0.:s.push_x*dt),r.correction[1]+(advance_forward?0.:s.push_z*dt)};
            if(advance_forward){
                movement.x+=sn*forward*dt;
                movement.z+=cs*forward*dt;
            }
            if(!advance_forward&&e.drive_pending&&s.alive){
                s.pose.position[0]=e.tick_pose.position[0];
                s.pose.position[2]=e.tick_pose.position[2];
                movement.x+=e.drive_move.x+sn*forward*dt;
                movement.z+=e.drive_move.z+cs*forward*dt;
                e.drive_pending=false;
            }
            if(movement.x||movement.z){
                const auto body=geometry(k,e);
                const auto roster=peers(k);
                const double fraction=offline_math::translation(body,movement,roster);
                movement=offline_math::slide(body,movement,roster,true,fraction);
            }
            const double distance=std::hypot(movement.x,movement.z);
            Pose end=s.pose;
            end.position[0]+=movement.x;
            end.position[2]+=movement.z;
            if(distance>.0001&&(world(k,e,std::atan2(movement.x,movement.z),distance/std::max(dt,1./120.),std::max(dt,1./120.),true,false,engine)!=0||!engine.pose_guard(k,s.pose,end,false)||!engine.turret_guard(k,s.pose,end))){
                movement={0.,0.};
                s.push_x=s.push_z=0.;
                if(advance_forward){
                    s.speed=old_speed;
                    s.contact_forward=retained(old_speed,s.contact_forward-forward);
                }
            }
            s.pose.position[0]+=movement.x;
            s.pose.position[2]+=movement.z;
            if(advance_forward&&e.drive_pending){
                e.drive_move.x+=movement.x;
                e.drive_move.z+=movement.z;
            }
        }
        void Store::settle_roster(Engine&engine) {
            CallScope scope(in_call_);
            if(!in_slice_||next_actor_!=order_.size())throw std::logic_error("settle before all actor drive/weapon boundaries");
            const std::vector<ActorKey>&sorted=settle_order_;
            for(auto k:sorted){
                auto&e=entry(k);
                if(e.siege_locked){
                    e.state.pose.position[0]=e.tick_pose.position[0];
                    e.state.pose.position[2]=e.tick_pose.position[2];
                    e.state.pose.yaw=e.tick_pose.yaw;
                    e.state.speed=e.state.turn_speed=e.state.push_x=e.state.push_z=0.;
                    e.state.movement_dir=e.state.rotation_dir=0;
                }
            }
            auto guarded=[&](ActorKey k,const auto&operation){
                try{
                    operation();
                }
                catch(const OperationFailed&){
                    fail(k);
                }
            };
            for(auto k:sorted)guarded(k,[&](){
                auto&e=entry(k);
                if(!e.state.alive||e.state.query_failed)return;
                e.vertical_cohort=true;
                const auto driven=e.state.pose;
                const bool airborne=e.state.airborne;
                e.support_blocked=vertical(k,e,dt_,e.tick_pose,engine);
                e.ballistic=airborne||e.state.airborne;
                Pose comparison=e.tick_pose;
                comparison.yaw=e.state.pose.yaw;
                if(!e.support_blocked&&!e.ballistic&&(!engine.pose_guard(k,comparison,e.state.pose,false)||(e.tick_safe&&!engine.safe(k,driven)))){
                    e.pose_rollback=true;
                    e.state.pose.position=e.tick_pose.position;
                    e.state.speed=e.state.vertical_speed=0.;
                    e.state.airborne=false;
                    e.state.movement_dir=e.state.rotation_dir=0;
                    e.state.push_x=e.state.push_z=0.;
                    e.state.turn_speed=0.;
                    emit(engine,k,5,e.attempted_yaw);
                }
                e.settled_pose=e.state.pose;
                (void)driven;
            });
            // Freeze the complete pre-drive X/Z roster once. Height/yaw are settled.
            std::vector<offline_math::Body> predrive;
            for(auto k:sorted){
                auto body=geometry(k,entry(k));
                if(entry(k).state.alive){
                    body.x=entry(k).tick_pose.position[0];
                    body.z=entry(k).tick_pose.position[2];
                }
                predrive.push_back(body);
            }
            for(auto h:humans_)predrive.push_back({h.id,h.has(offline_contact::HasY),h.x,h.y,h.z,h.yaw,h.pitch,h.roll,h.shape});
            for(auto k:sorted){
                auto&e=entry(k);
                if(!e.state.alive)continue;
                auto before=geometry(k,e);
                before.x=e.tick_pose.position[0];
                before.z=e.tick_pose.position[2];
                offline_math::Vec move={e.state.pose.position[0]-before.x,e.state.pose.position[2]-before.z};
                const double fraction=offline_math::translation(before,move,predrive);
                e.drive_pending=fraction<1.||e.state.push_x||e.state.push_z;
                e.drive_move=move;
                const auto allowed=offline_math::slide(before,move,predrive,true,fraction);
                e.state.pose.position[0]=before.x+allowed.x;
                e.state.pose.position[2]=before.z+allowed.z;
            }
            for(auto k:sorted)guarded(k,[&](){
                auto&e=entry(k);
                if(e.state.alive&&!e.state.query_failed&&!e.siege_locked&&e.passive_forward){
                    offline_contact::Row r;
                    r.delta_velocity={
                        {std::sin(e.state.pose.yaw)*e.passive_forward,std::cos(e.state.pose.yaw)*e.passive_forward}
                    };
                    push(k,e,r,dt_,true,false,engine);
                }
            });
            std::vector<offline_contact::Body>bodies;
            std::vector<std::int64_t>owners;
            for(auto k:sorted){
                auto&e=entry(k);
                const auto&s=e.state;
                const auto&p=e.descriptor;
                offline_contact::Body b;
                b.id=k.id;
                b.team=s.team;
                b.flags=offline_contact::HasY|offline_contact::HasGrip|offline_contact::Impulse|(s.alive?offline_contact::Alive:0);
                b.x=s.pose.position[0];
                b.y=s.pose.position[1];
                b.z=s.pose.position[2];
                b.yaw=s.pose.yaw;
                b.pitch=s.pose.pitch;
                b.roll=s.pose.roll;
                b.mass=p.mass;
                b.shape=p.shape;
                b.vx=std::sin(b.yaw)*(s.alive?s.speed:0.)+s.push_x;
                b.vy=s.vertical_speed;
                b.vz=std::cos(b.yaw)*(s.alive?s.speed:0.)+s.push_z;
                b.push_yaw=s.push_yaw;
                const auto g=push_grip(tuning_,p,s,s.alive&&(s.speed||s.movement_dir));
                b.grip[0]=g[0];
                b.grip[1]=g[1];
                const int turn=s.alive?(s.motor_turn?s.motor_turn:s.rotation_dir):0;
                if(turn){
                    b.traverse_speed=traverse(tuning_,p,0.,turn,s.speed,std::max(dt_,tuning_.angular_time),s.movement_dir);
                    const double track=std::abs(s.speed)+std::abs(b.traverse_speed)*p.shape[0];
                    b.traverse_torque=std::abs(motor_force(tuning_,p,track,turn,s.pose.pitch))*drive_scale(s.movement_dir,turn)*p.shape[0];
                }
                bodies.push_back(b);
                owners.push_back(k.id);
                e.state.motor_turn=0;
                absent(e.state,19);
            }
            bodies.insert(bodies.end(),humans_.begin(),humans_.end());
            if(!contact_order_.empty()){
                std::vector<offline_contact::Body>ordered;
                for(auto id:contact_order_){
                    auto found=std::find_if(bodies.begin(),bodies.end(),[&](const offline_contact::Body&b){
                        return b.id==id;
                    });
                    if(found==bodies.end())throw std::logic_error("missing contact body order");
                    ordered.push_back(*found);
                }
                if(ordered.size()!=bodies.size())throw std::logic_error("incomplete contact body order");
                bodies=std::move(ordered);
            }
            try{
                engine.begin_contacts(bodies);
            }
            catch(const OperationFailed&){
            }
            contact_rows_=offline_contact::resolve(bodies,owners,dt_,ram_contacts_);
            ram_contacts_.clear();
            for(auto&r:contact_rows_){
                ActorKey k{ActorKind::Bot,r.id};
                guarded(k,[&](){
                    auto&e=entry(k);
                    const bool active_wreck=e.state.airborne||std::abs(e.state.vertical_speed)>1.e-9||std::abs(e.state.push_yaw)>1.e-9;
                    if(!r.has_peers&&!e.state.push_x&&!e.state.push_z&&!( !e.state.alive&&active_wreck)){
                        e.drive_pending=false;
                        return;
                    }
                    if(!e.state.alive&&!active_wreck&&!e.state.push_x&&!e.state.push_z&&!r.has_active_peer)return;
                    std::vector<std::pair<std::int64_t,std::int64_t>>retained_contacts;
                    try{
                        retained_contacts=engine.contact_finalize(k,r);
                    }
                    catch(const OperationFailed&){
                    }
                    e.state.rotation_blocked=false;
                    absent(e.state,33);
                    if(e.state.alive){
                        if(r.has_peers)push(k,e,r,dt_,false,true,engine);
                        else if(e.state.push_x||e.state.push_z){
                            offline_contact::Row idle;
                            push(k,e,idle,dt_,false,true,engine);
                        }
                        e.drive_pending=false;
                    }
                    else{
                        present(e.state,6);
                        e.state.push_x+=r.delta_velocity[0];
                        e.state.push_z+=r.delta_velocity[1];
                        e.state.push_yaw+=r.delta_yaw;
                        wreck_friction(tuning_,e.descriptor,e.state,dt_);
                        auto response=r;
                        response.delta_velocity={
                            {0.,0.}
                        };
                        const Pose before=e.state.pose;
                        const double old=before.yaw;
                        const bool momentum=e.state.push_x||e.state.push_z||e.state.push_yaw;
                        if(!momentum)response.correction={
                            {0.,0.}
                        };
                        const offline_math::Vec movement{e.state.push_x*dt_+response.correction[0],e.state.push_z*dt_+response.correction[1]};
                        bool coupled=false;
                        if(e.state.push_yaw&&std::hypot(movement.x,movement.z)&&dt_>0.){
                            auto rotating=geometry(k,e);
                            rotating.pitch=rotating.roll=0.;
                            const double fraction=offline_math::rotation(rotating,old+e.state.push_yaw*dt_,0.,movement,peers(k));
                            Pose end=before;
                            end.position[0]+=movement.x*fraction;
                            end.position[2]+=movement.z*fraction;
                            end.yaw=old+e.state.push_yaw*dt_*fraction;
                            const offline_math::Vec allowed{movement.x*fraction,movement.z*fraction};
                            if(fraction>0.&&engine.pose_guard(k,before,end,false)&&engine.turret_guard(k,before,end)&&world(k,e,std::atan2(allowed.x,allowed.z),std::hypot(allowed.x,allowed.z)/dt_,dt_,true,false,engine)==0&&engine.wreck_rotation(k,before,end,dt_,allowed)){
                                e.state.pose=end;
                                e.state.pose.yaw=std::atan2(std::sin(end.yaw),std::cos(end.yaw));
                                if(fraction<1.)e.state.push_yaw=0.;
                                coupled=true;
                            }
                        }
                        if(!coupled){
                            push(k,e,response,dt_,false,false,engine);
                            const double candidate=old+e.state.push_yaw*dt_;
                            auto rotating=geometry(k,e);
                            rotating.pitch=rotating.roll=0.;
                            const double fraction=offline_math::rotation(rotating,candidate,0.,{0.,0.}
                            ,peers(k));
                            Pose end=e.state.pose;
                            end.yaw=old+(candidate-old)*fraction;
                            if(std::abs(candidate-old)>1.e-9){
                                if(engine.pose_guard(k,e.state.pose,end,false)&&engine.turret_guard(k,e.state.pose,end)&&engine.wreck_rotation(k,e.state.pose,end,dt_,{0.,0.}))e.state.pose.yaw=std::atan2(std::sin(end.yaw),std::cos(end.yaw));
                                else e.state.push_yaw=0.;
                                if(fraction<1.)e.state.push_yaw=0.;
                            }
                        }
                        if(vertical(k,e,dt_,before,engine)){
                            e.state.pose.position=before.position;
                            e.state.pose.yaw=old;
                            e.state.push_yaw=0.;
                        }
                        e.state.pose.pitch=std::atan2(std::sin(e.state.pose.pitch),std::cos(e.state.pose.pitch));
                        e.state.pose.roll=std::atan2(std::sin(e.state.pose.roll),std::cos(e.state.pose.roll));
                        e.state.terrain_pitch=std::atan2(std::sin(e.state.terrain_pitch),std::cos(e.state.terrain_pitch));
                    }
                    if(e.state.alive)ram_contacts_.insert(ram_contacts_.end(),retained_contacts.begin(),retained_contacts.end());
                });
            }
            for(auto k:sorted)guarded(k,[&](){
                auto&e=entry(k);
                if(e.siege_locked){
                    e.state.pose.position[0]=e.tick_pose.position[0];
                    e.state.pose.position[2]=e.tick_pose.position[2];
                    e.state.pose.yaw=e.tick_pose.yaw;
                    e.state.speed=e.state.turn_speed=e.state.push_x=e.state.push_z=0.;
                    e.state.movement_dir=e.state.rotation_dir=0;
                }
                e.after_contacts=e.state.pose;
                if(!e.vertical_cohort||e.state.query_failed)return;
                if(std::abs(e.state.pose.position[0]-e.settled_pose.position[0])>1.e-6||std::abs(e.state.pose.position[2]-e.settled_pose.position[2])>1.e-6){
                    Pose rollback=e.state.pose;
                    rollback.position[1]=e.settled_pose.position[1];
                    e.support_blocked=e.support_blocked||vertical(k,e,0.,rollback,engine);
                }
                if(!e.support_blocked&&!e.ballistic&&!e.state.airborne&&!engine.pose_guard(k,e.settled_pose,e.state.pose,false)){
                    e.pose_rollback=true;
                    e.state.pose.position=e.settled_pose.position;
                    e.state.speed=0.;
                    e.state.push_x=e.state.push_z=0.;
                    e.state.turn_speed=0.;
                    emit(engine,k,5,e.attempted_yaw);
                }
            });
            in_slice_=false;
        }
    }
}
