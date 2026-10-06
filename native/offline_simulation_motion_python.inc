#include <limits>
#include <type_traits>
// Included after world helpers and shared simulation_context(). All objects
// are borrowed only during this synchronous GIL-held entry; Store owns numbers.
namespace sim_motion_bridge {
    using namespace offline_simulation;
    using motion::State;
    template<typename T> T motion_number(PyObject*value) {
        const double number=finite_number(value);
        if(std::is_integral<T>::value &&        (number<static_cast<double>(std::numeric_limits<T>::lowest()) ||         number>=static_cast<double>(std::numeric_limits<T>::max())+1.0))         throw Unsupported();
        return static_cast<T>(number);
    }
    ActorKey key(PyObject *value) {
        require_sequence(value,2,true);
        const int kind=index_value(items(value)[0]);
        if(kind<0||kind>1)throw Unsupported();
        return {static_cast<ActorKind>(kind),identity(items(value)[1])};
    }
    PyObject *key_result(ActorKey value){
        OwnedTuple out(2);
        out.set(0,integer_result(static_cast<int>(value.kind)));
        out.set(1,integer_result(value.id));
        return out.release();
    }
    Pose pose(PyObject *value){
        require_sequence(value,6,true);
        Pose out;
        for(int i=0;i<3;++i)out.position[i]=finite_number(items(value)[i]);
        out.yaw=finite_number(items(value)[3]);
        out.pitch=finite_number(items(value)[4]);
        out.roll=finite_number(items(value)[5]);
        return out;
    }
    PyObject *pose_result(const Pose&p){
        OwnedTuple out(6);
        for(int i=0;i<3;++i)out.set(i,float_new(p.position[i]));
        out.set(3,float_new(p.yaw));
        out.set(4,float_new(p.pitch));
        out.set(5,float_new(p.roll));
        return out.release();
    }
#define MOTION_STATE_FIELDS(F) \
 F(terrain_pitch) F(speed) F(turn_speed) F(vertical_speed) F(push_x) F(push_z) F(push_yaw) F(contact_forward) F(last_drive_pitch) F(direction_command) \
 F(drown_check) F(drown_time) F(water_depth) F(overturn_check) F(overturn_time) F(siege_remaining) F(siege_total) \
 F(movement_dir) F(rotation_dir) F(motor_turn) F(hard_grind) F(siege_state) F(overturn_level) F(death_reason) F(team) F(soft_contact_speed)
    State state(PyObject *value){
        require_sequence(value,4,true);
        State out;
        const auto presence=simulation_wide_number(items(value)[3]);
        if(presence<0 || presence>((1LL<<34)-1))throw Unsupported();
        out.presence=static_cast<std::uint64_t>(presence);
        out.pose=pose(items(value)[0]);
        PyObject*row=items(value)[1];
        require_sequence(row,26,true);
        int i=0;
#define READ_STATE(name) out.name=motion_number<decltype(out.name)>(items(row)[i++]);
        MOTION_STATE_FIELDS(READ_STATE)
#undef READ_STATE
        const int flags=index_value(items(value)[2]);
        out.alive=flags&1;
        out.airborne=flags&2;
        out.grounded=flags&4;
        out.service_brake=flags&8;
        out.contact_dynamics=flags&16;
        out.drowning=flags&32;
        out.overturned=flags&64;
        out.rotation_blocked=flags&128;
        out.query_failed=flags&256;
        out.has_soft_contact=flags&512;
        return out;
    }
    PyObject*state_result(const State&s){
        OwnedTuple out(4),row(26);
        int i=0;
#define WRITE_STATE(name) row.set(i++,float_new(static_cast<double>(s.name)));
        MOTION_STATE_FIELDS(WRITE_STATE)
#undef WRITE_STATE
        out.set(0,pose_result(s.pose));
        out.set(1,row.release());
        out.set(2,integer_result((s.alive?1:0)|(s.airborne?2:0)|(s.grounded?4:0)|(s.service_brake?8:0)|(s.contact_dynamics?16:0)|(s.drowning?32:0)|(s.overturned?64:0)|(s.rotation_blocked?128:0)|(s.query_failed?256:0)|(s.has_soft_contact?512:0)));
        out.set(3,simulation_wide_result(s.presence));
        return out.release();
    }
#define MOTION_TUNING_FIELDS(F) \
 F(gravity) F(gravity_factor) F(cohesion) F(power_factor) F(reverse_power) F(engine_min_v) F(drive_traction) F(lng_full_y) F(lng_min_y) F(lng_full) F(lng_min) \
 F(side_full_y) F(side_min_y) F(side_full) F(side_min) F(coh_decay_y) F(coh_decay_factor) F(coh_decay_pow) F(slope_coh_y) F(slope_coh) F(coh_bound) \
 F(steer_resist) F(coast_share) F(hold_tan) F(slide_kinetic) F(slip_tan) F(slip_drag) F(overspeed) F(angular_time) F(speed_rot_cost) \
 F(follow_base) F(follow_min) F(follow_max) F(pitch_limit) F(hard_brake) F(hard_stop) F(hard_entry) F(hard_slide) F(hard_ticks) F(constraint_iterations) \
 F(drown_probe) F(drown_seconds) F(overturn_ignore) F(overturn_death) F(warning_cos) F(danger_cos)
    motion::Tuning tuning(PyObject*value){
        require_sequence(value,2,true);
        PyObject*row=items(value)[0];
        require_sequence(row,46,true);
        motion::Tuning out;
        int i=0;
#define READ_TUNING(name) out.name=motion_number<decltype(out.name)>(items(row)[i++]);
        MOTION_TUNING_FIELDS(READ_TUNING)
#undef READ_TUNING
        PyObject*yaws=items(value)[1];
        require_sequence(yaws,0);
        for(Py_ssize_t n=0;n<size(yaws);++n)out.glancing_yaws.push_back(finite_number(items(yaws)[n]));
        return out;
    }
    motion::Descriptor descriptor(PyObject*value){
        require_sequence(value,6,true);
        motion::Descriptor out;
        out.revision=identity(items(value)[0]);
        PyObject*row=items(value)[1];
        require_sequence(row,14,true);
        auto r=items(row);
        out.mass=finite_number(r[0]);
        out.power=finite_number(r[1]);
        out.forward_limit=finite_number(r[2]);
        out.reverse_limit=finite_number(r[3]);
        out.rotation_speed=finite_number(r[4]);
        for(int i=0;i<3;++i)out.resistance[i]=finite_number(r[5+i]);
        out.friction=finite_number(r[8]);
        out.brake=finite_number(r[9]);
        out.track_center=finite_number(r[10]);
        out.native_power_ratio=finite_number(r[11]);
        out.siege_limit=finite_number(r[12]);
        out.kinetic_speed=finite_number(r[13]);
        out.shape=shape_values(items(value)[2]);
        require_sequence(items(value)[3],6,true);
        r=items(items(value)[3]);
        for(int i=0;i<4;++i)out.bounds[i]=finite_number(r[i]);
        for(int i=0;i<2;++i)out.heights[i]=finite_number(r[i+4]);
        out.water_sensor=point_values(items(value)[4]);
        const int flags=index_value(items(value)[5]);
        out.around_center=flags&1;
        out.detailed_suspension=flags&2;
        return out;
    }
    motion::Command command(PyObject*value){
        require_sequence(value,2,true);
        PyObject*row=items(value)[0];
        require_sequence(row,4,true);
        motion::Command out;
        out.throttle=finite_number(items(row)[0]);
        out.turn=finite_number(items(row)[1]);
        out.slope_pitch=finite_number(items(row)[2]);
        out.travel_sign=finite_number(items(row)[3]);
        const int flags=index_value(items(value)[1]);
        out.path_clear=flags&1;
        out.pose_frozen=flags&2;
        out.generic_collision=flags&4;
        out.exact_collision=flags&8;
        out.allow_shallow=flags&16;
        out.wet_escape=flags&32;
        out.siege_locked=flags&64;
        out.diagnostics=flags&128;
        return out;
    }
    PyObject*drive_trace_result(const motion::Entry&e){
        const auto&t=e.trace;
        OwnedTuple out(10);
        out.set(0,float_new(t.speed));
        out.set(1,float_new(t.pitch));
        out.set(2,float_new(t.throttle));
        out.set(3,integer_result(t.baked_veto));
        out.set(4,integer_result(t.path_clear));
        out.set(5,integer_result(t.frozen));
        out.set(6,integer_result(t.world_status));
        out.set(7,integer_result(t.hard_contact));
        out.set(8,point_result(t.integrated.position));
        out.set(9,float_new(t.world_speed));
        return out.release();
    }
    PyObject*settlement_result(const motion::Entry&e){
        OwnedTuple out(4);
        out.set(0,integer_result((e.vertical_cohort?1:0)|(e.support_blocked?2:0)|
                                (e.pose_rollback?4:0)|(e.ballistic?8:0)|(e.siege_locked?16:0)));
        out.set(1,point_result(e.settled_pose.position));
        out.set(2,point_result(e.after_contacts.position));
        if(e.diagnostics&&e.trace.has_support){
            const auto&t=e.trace;
            OwnedTuple support(6);
            support.set(0,t.centre.present?float_new(t.centre.height):fallback());
            support.set(1,t.highest.present?float_new(t.highest.height):fallback());
            support.set(2,integer_result(t.grounded_before));
            support.set(3,t.has_limit?float_new(t.support_limit):fallback());
            support.set(4,integer_result(t.rise_obstacle));
            support.set(5,integer_result(t.rise_continuous));
            out.set(3,support.release());
        }else out.set(3,fallback());
        return out.release();
    }
    PyObject*contact_result(const offline_contact::Row&r){
        OwnedTuple out(8),correction(2),velocity(2),retained( r.retained_contacts.size()),ram(r.ram_candidates.size());
        out.set(0,integer_result(r.id));
        for(int i=0;i<2;++i){
            correction.set(i,float_new(r.correction[i]));
            velocity.set(i,float_new(r.delta_velocity[i]));
        }
        out.set(1,correction.release());
        out.set(2,velocity.release());
        out.set(3,float_new(r.delta_yaw));
        out.set(4,integer_result(r.has_peers?1:0));
        for(size_t i=0;i<r.retained_contacts.size();++i){
            OwnedTuple pair(2);
            pair.set(0,integer_result(r.retained_contacts[i].first));
            pair.set(1,integer_result(r.retained_contacts[i].second));
            retained.set(i,pair.release());
        }
        out.set(5,integer_result(r.has_active_peer?1:0));
        out.set(6,retained.release());
        for(size_t i=0;i<r.ram_candidates.size();++i){
            const auto&c=r.ram_candidates[i];
            OwnedTuple item(6);
            item.set(0,integer_result(c.other_id));
            item.set(1,point_result(c.impact));
            item.set(2,float_new(c.closing_speed));
            item.set(3,float_new(c.relative_speed));
            item.set(4,integer_result(c.moving_self));
            item.set(5,integer_result(c.moving_other));
            ram.set(i,item.release());
        }
        out.set(7,ram.release());
        return out.release();
    }
    // One synchronous world sweep owns its original engine hits and all
    // departing planes. No row or Python object escapes to a worker thread.
    class WorldFrontier {
        WorldSnapshot snapshot_;
        unsigned recasts_;
        double epsilon_, gradient_;
        double position_[3], departing_[15] = {};
        bool has_departing_ = false;
        struct Plane { double point[3], normal[3]; };
        std::vector<Plane> planes_;
        std::vector<engine_query::Object> hits_;
        bool departing(PyObject *hit) {
            require_sequence(hit, 2);
            double point[3], normal[3];
            session.point(items(hit)[0], point);
            session.point(items(hit)[1], normal);
            if (normal[1] < -.2) return false;
            const double delta[3] = {departing_[0], departing_[2], departing_[1]};
            double outward = 0., length2 = 0.;
            for (unsigned i=0;i<3;++i) {
                outward += delta[i]*normal[i];
                length2 += normal[i]*normal[i];
            }
            const bool drivable = length2 > 0. && normal[1]/std::sqrt(length2) >=
                1./std::sqrt(1.+gradient_*gradient_);
            if (outward < -1.e-8 || (outward <= 1.e-8 && !drivable)) return false;
            double relative[3], local[3], local_delta[3] = {};
            for (unsigned i=0;i<3;++i) relative[i]=point[i]-position_[i];
            if (snapshot_.passive) {
                for (unsigned i=0;i<3;++i) {
                    const double *axis=departing_+6+i*3;
                    local[i]=axis[0]*relative[0]+axis[1]*relative[1]+axis[2]*relative[2];
                    local_delta[i]=axis[0]*delta[0]+axis[1]*delta[1]+axis[2]*delta[2];
                }
            } else {
                if (std::abs(departing_[4])<.1) return false;
                const double sine=std::sin(snapshot_.yaw), cosine=std::cos(snapshot_.yaw);
                local[0]=relative[0]*cosine-relative[2]*sine;
                local[2]=relative[0]*sine+relative[2]*cosine;
                local[1]=(relative[1]-local[0]*departing_[3]-local[2]*departing_[5])/departing_[4];
            }
            const double lower[3]={snapshot_.bounds[0],snapshot_.heights[0],-snapshot_.bounds[2]};
            const double upper[3]={snapshot_.bounds[1],snapshot_.heights[1],snapshot_.bounds[3]};
            bool inside=true;
            for (unsigned i=0;i<3;++i)
                inside=inside && lower[i]-.001<=local[i] && local[i]<=upper[i]+.001;
            if (!inside && snapshot_.passive && outward>1.e-8) {
                double lo=0.,hi=1.;
                for (unsigned i=0;i<3;++i) {
                    if (std::abs(local_delta[i])<1.e-12) {
                        if (!(lower[i]-.001<=local[i] && local[i]<=upper[i]+.001)) { hi=-1.;break; }
                    } else {
                        const double a=(local[i]-upper[i]-.001)/local_delta[i];
                        const double b=(local[i]-lower[i]+.001)/local_delta[i];
                        lo=std::max(lo,std::min(a,b));hi=std::min(hi,std::max(a,b));
                    }
                }
                inside=lo<=hi;
            }
            if (inside) {
                Plane plane;
                for (unsigned i=0;i<3;++i) {plane.point[i]=point[i];plane.normal[i]=normal[i];}
                planes_.push_back(plane);
                return true;
            }
            for (const auto &plane:planes_) {
                double alignment=0.,distance=0.;
                for (unsigned i=0;i<3;++i) {
                    alignment+=normal[i]*plane.normal[i];
                    distance+=(point[i]-plane.point[i])*normal[i];
                }
                if (alignment>=.9999 && std::abs(distance)<=.001) return true;
            }
            return false;
        }
    public:
        engine_query::Session session;
        engine_query::Object filter;
        bool prepared=false;
        WorldFrontier(PyObject *capabilities, PyObject *tuning, const WorldSnapshot &snapshot):
            snapshot_(snapshot), session(capabilities) {
            require_sequence(tuning,3,true);
            const auto budget=identity(items(tuning)[0]);
            epsilon_=finite_number(items(tuning)[1]);gradient_=finite_number(items(tuning)[2]);
            if(budget<0 || budget>1024 || epsilon_<=0. || gradient_<=0.)throw Unsupported();
            recasts_=static_cast<unsigned>(budget);
            auto position=session.vector(snapshot.position);
            session.point(position.get(),position_);
        }
        void departure(const WorldRequest &request) {
            for(unsigned i=0;i<15;++i)departing_[i]=request.auxiliary[i];
            planes_.clear();has_departing_=true;
        }
        PyObject *hit(std::uint64_t handle) {
            if (!handle || handle>hits_.size())throw Unsupported();
            return hits_[handle-1].get();
        }
        void ray(unsigned op,const WorldRequest &request,WorldResponse &response,PyObject *query_filter) {
            auto start=session.vector(request.start),end=session.vector(request.end);
            session.point(start.get(),response.start);session.point(end.get(),response.end);
            auto current=start;
            engine_query::Object result;
            for(unsigned attempt=0;attempt<=recasts_;++attempt) {
                result=session.collide(current.get(),end.get(),query_filter,op==WORLD_GROUND);
                if(op!=WORLD_HORIZONTAL || result.get()==none_object || !has_departing_ ||
                   !(request.flags&1) || !departing(result.get()))break;
                require_sequence(result.get(),1);
                auto remaining=session.subtract(end.get(),items(result.get())[0]);
                const double length=session.length(remaining.get());
                if(length<=epsilon_) {result=engine_query::Object::borrow(none_object);break;}
                auto step=session.scale(remaining.get(),epsilon_/length);
                current=session.add(items(result.get())[0],step.get());
            }
            if(result.get()==none_object)return;
            require_sequence(result.get(),1);
            session.point(items(result.get())[0],response.point);
            if(size(result.get())>1)session.point(items(result.get())[1],response.normal);
            hits_.push_back(std::move(result));
            response.handle=hits_.size();response.status=1;
        }
    };
    class Engine:public motion::Engine {
        std::shared_ptr<SimulationContext> context_;
        PyObject*dispatcher_;
        std::unique_ptr<WorldFrontier> frontier_;
        std::exception_ptr world_error_;
        void live(){
            if(context_->closed.load())throw Unsupported();
        }
        WorldObject query(int opcode,OwnedTuple&rows,std::uint64_t request_count=1){
            NativeProfilePhase bridge(NativePhase::Pack);
            live();
            OwnedTuple args(2);
            args.set(0,integer_result(opcode));
            args.set(1,rows.release());
            WorldObject arguments(args.release());
            PyObject*out=profiled_object_call(NativeCallback::Motion, "motion", opcode, request_count, dispatcher_, arguments.get());
            bridge.input();
            if(context_->closed.load()){
                if(out)decref(out);
                throw Unsupported();
            }
            if(out&&(out->ob_type==tuple_type||out->ob_type==list_type)&&size(out)==1&&items(out)[0]&&items(out)[0]->ob_type==int_type&&index_value(items(out)[0])==-999){
                decref(out);
                throw motion::OperationFailed();
            }
            return WorldObject(out);
        }
        bool poses(int opcode,ActorKey k,const Pose&a,const Pose&b,bool flag){
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(opcode==12?5:4);
            row.set(0,key_result(k));
            row.set(1,pose_result(a));
            row.set(2,pose_result(b));
            row.set(3,integer_result(flag));
            if(opcode==12){
                // Turret construction reads these current orientation fields;
                // positions/yaws are already explicit in a and b.
                const auto&s=context_->motion.snapshot(k);
                OwnedTuple orientation(4);
                orientation.set(0,float_new(s.pose.pitch));
                orientation.set(1,float_new(s.pose.roll));
                orientation.set(2,float_new(s.terrain_pitch));
                orientation.set(3,integer_result(s.presence&1));
                row.set(4,orientation.release());
            }
            auto result=query(opcode,row);
            bridge.input();
            return index_value(result.get())!=0;
        }
        public:  Engine(std::shared_ptr<SimulationContext>context,PyObject*dispatcher):context_(std::move(context)),dispatcher_(dispatcher){
        }
        bool begin_world(ActorKey k,const State&s,const motion::Descriptor&d,const WorldSnapshot&q,bool commit,bool active,bool receipt_allowed)override {
            NativeProfilePhase bridge(NativePhase::Pack);
            frontier_.reset();
            world_error_=nullptr;
            OwnedTuple row(11), shape(6);
            row.set(0,key_result(k));
            Pose p=s.pose;
            p.pitch=q.pitch;
            row.set(1,pose_result(p));
            row.set(2,float_new(q.speed));
            row.set(3,float_new(q.dt));
            row.set(4,q.passive?float_new(q.motion_yaw):fallback());
            row.set(5,integer_result((commit?1:0)|(active?2:0)|(q.airborne?4:0)|(receipt_allowed?8:0)));
            row.set(6,float_new(d.kinetic_speed));
            row.set(7,integer_result(d.revision));
            row.set(8,integer_result(s.rotation_dir));
            row.set(9,float_new(s.turn_speed));
            for(unsigned i=0;i<4;++i)shape.set(i,float_new(q.bounds[i]));
            for(unsigned i=0;i<2;++i)shape.set(i+4,float_new(q.heights[i]));
            row.set(10,shape.release());
            auto result=query(20,row);
            bridge.input();
            if(result.get()->ob_type==int_type)return index_value(result.get())!=0;
            require_sequence(result.get(),2,true);
            try {
                frontier_.reset(new WorldFrontier(items(result.get())[0],items(result.get())[1],q));
            } catch(const engine_query::Failed &) {
                OwnedTuple cleanup(0);query(21,cleanup);
                throw motion::OperationFailed();
            }
            return false;
        }
        int dispatch_world_impl(std::uint32_t op,const WorldRequest*q,std::uint32_t n,WorldResponse*r) {
            NativeProfilePhase bridge(NativePhase::Pack);
            live();
            if(!frontier_)throw Unsupported();
            if(op==WORLD_DEPARTING) {
                if(n!=1)throw Unsupported();
                frontier_->departure(q[0]);return 1;
            }
            if(op==WORLD_HORIZONTAL || op==WORLD_GROUND) {
                bool stopped=false;
                for(size_t i=0;i<n;++i) {
                    if(stopped || (q[i].flags&4)) {
                        for(unsigned j=0;j<3;++j) {r[i].start[j]=q[i].start[j];r[i].end[j]=q[i].end[j];}
                        stopped=stopped || (op==WORLD_GROUND && (q[i].flags&1));
                        continue;
                    }
                    engine_query::Object local_filter;
                    PyObject *filter=frontier_->filter.get();
                    if(!frontier_->prepared) {
                        if(op!=WORLD_GROUND)throw Unsupported();
                        OwnedTuple rows(1);rows.set(0,world_request(WORLD_GROUND,q[i]));
                        auto result=query(7,rows);
                        local_filter=engine_query::Object::borrow(result.get());filter=local_filter.get();
                    }
                    frontier_->ray(op,q[i],r[i],filter);
                    stopped=stopped || (op==WORLD_GROUND && !r[i].status && (q[i].flags&1));
                    live();
                }
                return 1;
            }
            OwnedTuple rows(n);
            for(size_t i=0;i<n;++i) {
                auto value=world_request(op,q[i]);
                if(op==WORLD_RESOLVE || op==WORLD_TRACE) {
                    auto old=items(value)[2];
                    auto hit=frontier_->hit(q[i].handle);++hit->ob_refcnt;
                    items(value)[2]=hit;decref(old);
                }
                rows.set(i,value);
            }
            auto result=query(op,rows,n);
            bridge.input();
            if(op==WORLD_FILTER) {
                frontier_->filter=engine_query::Object::borrow(result.get());frontier_->prepared=true;
                return 1;
            }
            if(op==WORLD_TRACE)return 1;
            if(op!=WORLD_RESOLVE)throw Unsupported();
            require_sequence(result.get(),n,true);
            for(size_t i=0;i<n;++i) {
                r[i].status=index_value(items(result.get())[i]);
                if(r[i].status<0||r[i].status>2)throw Unsupported();
            }
            live();
            return 1;
        }
        int dispatch_world(std::uint32_t op,const WorldRequest*q,std::uint32_t n,WorldResponse*r)override{
            try{
                return dispatch_world_impl(op,q,n,r);
            }
            catch(const Unsupported &){
                // A malformed engine hit is local to this actor operation.
                // Closed contexts still reject the old caller at end_world.
                world_error_=context_->closed.load()?std::current_exception():
                    std::make_exception_ptr(motion::OperationFailed());
                return 0;
            }
            catch(...){
                world_error_=std::current_exception();
                return 0;
            }
        }
        void end_world()override {
            NativeProfilePhase bridge(NativePhase::Pack);
            frontier_.reset();
            auto error=world_error_;world_error_=nullptr;
            OwnedTuple row(0);
            query(21,row);
            if(error) {
                try {std::rethrow_exception(error);}
                catch(const engine_query::Failed &) {throw motion::OperationFailed();}
            }
        }
        motion::CatalogResult catalog(ActorKey k,const State&s,double speed,double yaw,double dt,bool passive,bool commit)override {
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(7);
            row.set(0,key_result(k));
            row.set(1,pose_result(s.pose));
            row.set(2,float_new(speed));
            row.set(3,float_new(yaw));
            row.set(4,float_new(dt));
            row.set(5,integer_result(passive));
            row.set(6,integer_result(commit));
            auto out=query(18,row);
            bridge.input();
            require_sequence(out.get(),2,true);
            return {index_value(items(out.get())[0]),index_value(items(out.get())[1])!=0};
        }
        bool pose_guard(ActorKey k,const Pose&a,const Pose&b,bool hazards)override{
            return poses(11,k,a,b,hazards);
        }
        bool turret_guard(ActorKey k,const Pose&a,const Pose&b)override{
            return poses(12,k,a,b,false);
        }
        bool corridor(ActorKey k,const State&s,double yaw,bool wet,bool shallow)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(6);
            row.set(0,key_result(k));
            row.set(1,pose_result(s.pose));
            row.set(2,float_new(yaw));
            row.set(3,float_new(s.speed));
            row.set(4,integer_result((wet?1:0)|(shallow?2:0)));
            row.set(5,float_new(s.water_depth));
            auto out=query(13,row);
            bridge.input();
            return index_value(out.get())!=0;
        }
        std::vector<motion::Support>ground(ActorKey k,const std::vector<Point>&points)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(2),batch(points.size());
            row.set(0,key_result(k));
            for(size_t i=0;i<points.size();++i)batch.set(i,point_result(points[i]));
            row.set(1,batch.release());
            auto result=query(10,row,points.size());
            bridge.input();
            require_sequence(result.get(),static_cast<Py_ssize_t>(points.size()),true);
            std::vector<motion::Support>out;
            for(Py_ssize_t i=0;i<size(result.get());++i){
                PyObject*v=items(result.get())[i];
                out.push_back({v!=none_object,v==none_object?0.:finite_number(v)});
            }
            return out;
        }
        bool support_path(ActorKey k,const Pose&a,const Pose&b,double y,double dt)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(5);
            row.set(0,key_result(k));
            row.set(1,pose_result(a));
            row.set(2,pose_result(b));
            row.set(3,float_new(y));
            row.set(4,float_new(dt));
            auto result=query(14,row);
            bridge.input();
            return index_value(result.get())!=0;
        }
        double water(ActorKey k,const Pose&p)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(2);
            row.set(0,key_result(k));
            row.set(1,pose_result(p));
            auto result=query(15,row);
            bridge.input();
            return finite_number(result.get());
        }
        bool safe(ActorKey k,const Pose&p)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(2);
            row.set(0,key_result(k));
            row.set(1,pose_result(p));
            auto result=query(16,row);
            bridge.input();
            return index_value(result.get())!=0;
        }
        bool wreck_rotation(ActorKey k,const Pose&a,const Pose&b,double dt,offline_math::Vec movement)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(5),move(2);
            row.set(0,key_result(k));
            row.set(1,pose_result(a));
            row.set(2,pose_result(b));
            row.set(3,float_new(dt));
            move.set(0,float_new(movement.x));
            move.set(1,float_new(movement.z));
            row.set(4,move.release());
            auto result=query(19,row);
            bridge.input();
            return index_value(result.get())!=0;
        }
        motion::EffectReceipt effect(const motion::Effect&e)override {
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(3);
            row.set(0,key_result(e.actor));
            row.set(1,integer_result(e.kind));
            row.set(2,float_new(e.value));
            auto result=query(17,row);
            bridge.input();
            return {state(result.get())};
        }
        void motion_finished(ActorKey k,int status,double initial,double final,bool hard,const Pose&pose,double yaw,bool resolved)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(8);
            row.set(0,key_result(k));
            row.set(1,integer_result(status));
            row.set(2,float_new(initial));
            row.set(3,float_new(final));
            row.set(4,integer_result(hard));
            row.set(5,pose_result(pose));
            row.set(6,float_new(yaw));
            row.set(7,integer_result(resolved));
            query(24,row);
        }
        void begin_contacts(const std::vector<offline_contact::Body>&bodies)override{
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple rows(bodies.size());
            for(size_t i=0;i<bodies.size();++i){
                const auto&b=bodies[i];
                OwnedTuple row(7);
                row.set(0,integer_result(b.id));
                row.set(1,integer_result(b.team));
                row.set(2,integer_result(b.flags));
                Pose p;
                p.position={
                    {b.x,b.y,b.z}
                };
                p.yaw=b.yaw;
                p.pitch=b.pitch;
                p.roll=b.roll;
                row.set(3,pose_result(p));
                const double values[9]={b.mass,b.vx,b.vy,b.vz,b.push_yaw,b.grip[0],b.grip[1],b.traverse_speed,b.traverse_torque};
                row.set(4,world_numbers(values,9));
                row.set(5,world_numbers(b.shape.data(),4));
                row.set(6,integer_result(0));
                rows.set(i,row.release());
            }
            query(23,rows,bodies.size());
        }
        std::vector<std::pair<std::int64_t,std::int64_t>> contact_finalize(ActorKey k,const offline_contact::Row&r)override {
            NativeProfilePhase bridge(NativePhase::Pack);
            OwnedTuple row(2);
            row.set(0,key_result(k));
            row.set(1,contact_result(r));
            auto result=query(22,row);
            bridge.input();
            require_sequence(result.get(),0);
            std::vector<std::pair<std::int64_t,std::int64_t>>out;
            for(Py_ssize_t i=0;i<size(result.get());++i){
                PyObject*v=items(result.get())[i];
                require_sequence(v,2,true);
                out.emplace_back(identity(items(v)[0]),identity(items(v)[1]));
            }
            return out;
        }
    };
}
PyObject*WOT_CDECL sim_motion_configure(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_configure);
    try{
        require_sequence(args,2,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        profile.run(&offline_simulation::motion::Store::configure, &ctx->motion, sim_motion_bridge::tuning(items(args)[1]));
        return int_new(1);
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_install(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_install);
    try{
        require_sequence(args,5,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        const auto d=sim_motion_bridge::descriptor(items(args)[3]);
        if(items(args)[4]==none_object)profile.run(&offline_simulation::motion::Store::install, &ctx->motion, sim_motion_bridge::key(items(args)[1]),sim_motion_bridge::state(items(args)[2]),d,nullptr);
        else{
            const auto siege=sim_motion_bridge::descriptor(items(args)[4]);
            profile.run(&offline_simulation::motion::Store::install, &ctx->motion, sim_motion_bridge::key(items(args)[1]),sim_motion_bridge::state(items(args)[2]),d,&siege);
        }
        return int_new(1);
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_patch(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_patch);
    try{
        require_sequence(args,3,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        profile.run(&offline_simulation::motion::Store::patch, &ctx->motion, sim_motion_bridge::key(items(args)[1]),sim_motion_bridge::state(items(args)[2]));
        return int_new(1);
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_begin(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_begin);
    try{
        require_sequence(args,8,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        PyObject*raw=items(args)[1];
        require_sequence(raw,4,true);
        offline_simulation::TickToken token{identity(items(raw)[0]),identity(items(raw)[1]),identity(items(raw)[2]),simulation_wide_number(items(raw)[3])};
        if(token.round!=ctx->round||token.generation!=ctx->generation)throw Unsupported();
        std::vector<offline_simulation::ActorKey>order;
        raw=items(args)[4];
        require_sequence(raw,0);
        for(Py_ssize_t i=0;i<size(raw);++i)order.push_back(sim_motion_bridge::key(items(raw)[i]));
        raw=items(args)[5];
        require_sequence(raw,0);
        std::vector<offline_contact::Body>humans;
        for(Py_ssize_t i=0;i<size(raw);++i){
            PyObject*value=items(raw)[i];
            require_sequence(value,6,true);
            offline_contact::Body b;
            b.id=identity(items(value)[0]);
            b.team=identity(items(value)[1]);
            b.flags=static_cast<unsigned>(index_value(items(value)[2]));
            const auto p=sim_motion_bridge::pose(items(value)[3]);
            b.x=p.position[0];
            b.y=p.position[1];
            b.z=p.position[2];
            b.yaw=p.yaw;
            b.pitch=p.pitch;
            b.roll=p.roll;
            PyObject*r=items(value)[4];
            require_sequence(r,9,true);
            auto fields=items(r);
            b.mass=finite_number(fields[0]);
            b.vx=finite_number(fields[1]);
            b.vy=finite_number(fields[2]);
            b.vz=finite_number(fields[3]);
            b.push_yaw=finite_number(fields[4]);
            b.grip[0]=finite_number(fields[5]);
            b.grip[1]=finite_number(fields[6]);
            b.traverse_speed=finite_number(fields[7]);
            b.traverse_torque=finite_number(fields[8]);
            b.shape=shape_values(items(value)[5]);
            humans.push_back(b);
        }
        raw=items(args)[6];
        require_sequence(raw,0);
        std::vector<std::int64_t>contact_order;
        for(Py_ssize_t i=0;i<size(raw);++i)contact_order.push_back(identity(items(raw)[i]));
        raw=items(args)[7];
        require_sequence(raw,0);
        std::vector<offline_simulation::ActorKey>settle_order;
        for(Py_ssize_t i=0;i<size(raw);++i)settle_order.push_back(sim_motion_bridge::key(items(raw)[i]));
        profile.run(&offline_simulation::motion::Store::begin_slice, &ctx->motion, token,finite_number(items(args)[2]),finite_number(items(args)[3]),order,std::move(humans),std::move(contact_order),settle_order);
        return int_new(1);
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_prepare(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_prepare);
    try{
        require_sequence(args,3,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        sim_motion_bridge::Engine engine(ctx,items(args)[2]);
        return sim_motion_bridge::state_result(profile.run(&offline_simulation::motion::Store::prepare_actor, &ctx->motion, sim_motion_bridge::key(items(args)[1]),engine));
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_advance(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_advance);
    try{
        require_sequence(args,4,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        sim_motion_bridge::Engine engine(ctx,items(args)[3]);
        const auto key=sim_motion_bridge::key(items(args)[1]);
        const auto command=sim_motion_bridge::command(items(args)[2]);
        const auto&state=profile.run(&offline_simulation::motion::Store::advance_actor, &ctx->motion, key,command,engine);
        if(!command.diagnostics)return sim_motion_bridge::state_result(state);
        OwnedTuple out(2);
        out.set(0,sim_motion_bridge::state_result(state));
        out.set(1,sim_motion_bridge::drive_trace_result(ctx->motion.observation(key)));
        return out.release();
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_settle(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_settle);
    try{
        require_sequence(args,2,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        sim_motion_bridge::Engine engine(ctx,items(args)[1]);
        profile.run(&offline_simulation::motion::Store::settle_roster, &ctx->motion, engine);
        OwnedTuple out(ctx->motion.actors().size());
        size_t i=0;
        for(const auto&item:ctx->motion.actors()){
            OwnedTuple row(3);
            row.set(0,sim_motion_bridge::key_result(item.first));
            row.set(1,sim_motion_bridge::state_result(item.second.state));
            row.set(2,sim_motion_bridge::settlement_result(item.second));
            out.set(i++,row.release());
        }
        return out.release();
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_snapshot(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_snapshot);
    try{
        require_sequence(args,2,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        return sim_motion_bridge::state_result(profile.run(&offline_simulation::motion::Store::snapshot, &ctx->motion, sim_motion_bridge::key(items(args)[1])));
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_patch_fields(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_patch_fields);
    try{
        require_sequence(args,4,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        profile.run(&offline_simulation::motion::Store::patch_fields, &ctx->motion, sim_motion_bridge::key(items(args)[1]),sim_motion_bridge::state(items(args)[3]),static_cast<unsigned>(index_value(items(args)[2])));
        return int_new(1);
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_after_weapon(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_after_weapon);
    try{
        require_sequence(args,3,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        PyObject*row=items(args)[2];
        require_sequence(row,5,true);
        offline_simulation::motion::State input=profile.run(&offline_simulation::motion::Store::snapshot, &ctx->motion, sim_motion_bridge::key(items(args)[1]));
        profile.input();
        input.pose.pitch=finite_number(items(row)[0]);
        input.terrain_pitch=finite_number(items(row)[1]);
        input.alive=index_value(items(row)[2])!=0;
        input.death_reason=index_value(items(row)[3]);
        input.presence &= ~(std::uint64_t(1)<<23);
        if(index_value(items(row)[4]))input.presence |= std::uint64_t(1)<<23;
        profile.run(&offline_simulation::motion::Store::patch_fields, &ctx->motion, sim_motion_bridge::key(items(args)[1]),input,8|4);
        return int_new(1);
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
PyObject*WOT_CDECL sim_motion_descriptor(PyObject*,PyObject*args){
    NATIVE_PROFILE_ENTRY(sim_motion_descriptor);
    try{
        require_sequence(args,5,true);
        auto ctx=simulation_context(identity(items(args)[0]));
        const auto normal=sim_motion_bridge::descriptor(items(args)[2]);
        offline_simulation::motion::Descriptor alternate;
        const bool has=items(args)[3]!=none_object;
        if(has)alternate=sim_motion_bridge::descriptor(items(args)[3]);
        PyObject*row=items(args)[4];
        require_sequence(row,3,true);
        profile.run(&offline_simulation::motion::Store::descriptor_patch, &ctx->motion, sim_motion_bridge::key(items(args)[1]),normal,has?&alternate:nullptr,index_value(items(row)[0]),finite_number(items(row)[1]),finite_number(items(row)[2]));
        return int_new(1);
    }
    catch(const PythonAllocationFailed&){ profile.fail();
        return 0;
    }
    catch(...){ profile.fail();
        return fallback();
    }
}
#define WOT_SIM_MOTION_METHODS \
 {"sim_motion_configure",sim_motion_configure,1,"Install persistent physical tuning."}, \
 {"sim_motion_install",sim_motion_install,1,"Install actor and descriptor once."}, \
 {"sim_motion_patch",sim_motion_patch,1,"Apply an explicit external physical rebase."}, \
 {"sim_motion_begin",sim_motion_begin,1,"Begin one conserved physical slice."}, \
 {"sim_motion_prepare",sim_motion_prepare,1,"Advance ordered physical preparation."}, \
 {"sim_motion_advance",sim_motion_advance,1,"Advance one actor before its weapon boundary."}, \
 {"sim_motion_settle",sim_motion_settle,1,"Settle complete support/contact roster."}, \
 {"sim_motion_patch_fields",sim_motion_patch_fields,1,"Apply selected external fields without resetting the physical clock."}, \
 {"sim_motion_after_weapon",sim_motion_after_weapon,1,"Admit hydraulic/terminal weapon inputs at the actor boundary."}, \
 {"sim_motion_descriptor",sim_motion_descriptor,1,"Adopt descriptor revision without replacing histories."}, \
 {"sim_motion_snapshot",sim_motion_snapshot,1,"Read a committed physical mirror."},
