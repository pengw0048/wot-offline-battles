// Included after simulation_context and the owned tuple bridge helpers.
namespace weapon_bridge {
namespace w = offline_simulation::weapons;
using offline_simulation::ActorKey;
using offline_simulation::ActorKind;

int integer(PyObject *value) {
    const double number=finite_number(value);
    if (std::floor(number)!=number || number< -2147483647. || number>2147483647.) throw Unsupported();
    return static_cast<int>(number);
}
std::int64_t wide(PyObject *value) {
    const double number=finite_number(value);
    if (std::floor(number)!=number || std::abs(number)>9007199254740991.) throw Unsupported();
    return static_cast<std::int64_t>(number);
}
ActorKey key(PyObject *kind,PyObject *id) {
    const int type=integer(kind); if(type<0 || type>1) throw Unsupported();
    ActorKey result;result.kind=static_cast<ActorKind>(type);result.id=wide(id);return result;
}
w::GunConfig config(PyObject *object) {
    double p[13];world_array(object,p,13);w::GunConfig result;
    result.fully_aimed_dispersion=p[0];result.after_shot=p[1];result.after_shot_in_burst=p[2];
    result.turret_dispersion_factor=p[3];result.aiming_time=p[4];result.movement_dispersion_factor=p[5];
    result.rotation_dispersion_factor=p[6];result.reload_full=p[7];result.reload_intra=p[8];
    result.clip_size=integer(items(object)[9]);result.shell_count=integer(items(object)[10]);
    result.burst_count=integer(items(object)[11]);result.burst_interval=p[12];result.validate();return result;
}
std::vector<int> integers(PyObject *object) {
    require_sequence(object,0);std::vector<int> result;
    for(Py_ssize_t i=0;i<size(object);++i) result.push_back(integer(items(object)[i]));
    return result;
}
PyObject *integer_rows(const std::vector<int> &values) {
    OwnedTuple result(values.size());for(size_t i=0;i<values.size();++i) result.set(i,int_new(values[i]));return result.release();
}
w::AmmoState ammo(PyObject *object) {
    require_sequence(object,6,true);auto **p=items(object);w::AmmoState result;
    result.categories=integers(p[0]);result.remaining=integers(p[1]);result.loaded=integer(p[2]);result.next=integer(p[3]);
    result.reload_pending=integer(p[4])!=0;result.plan_pending=integer(p[5])!=0;return result;
}
w::BurstState burst(PyObject *object) {
    require_sequence(object,7,true);auto **p=items(object);w::BurstState result;
    result.active=integer(p[0])!=0;result.group_seq=wide(p[1]);result.count=integer(p[2]);
    result.next_index=integer(p[3]);result.interval=finite_number(p[4]);result.time_left=finite_number(p[5]);
    result.shell=integer(p[6]);return result;
}
w::BurstEdge edge(PyObject *object) {
    require_sequence(object,7,true);auto **p=items(object);w::BurstEdge result;
    result.shot_seq=wide(p[0]);result.group_seq=wide(p[1]);result.index=integer(p[2]);result.count=integer(p[3]);
    result.shell=integer(p[4]);result.final_round=integer(p[5])!=0;result.due_offset=finite_number(p[6]);return result;
}
PyObject *edges(const std::vector<w::BurstEdge> &values) {
    OwnedTuple result(values.size());for(size_t i=0;i<values.size();++i) {
        const auto &v=values[i];const double p[]={double(v.shot_seq),double(v.group_seq),double(v.index),
            double(v.count),double(v.shell),v.final_round?1.:0.,v.due_offset};result.set(i,world_numbers(p,7));
    }return result.release();
}
w::Curve curve(PyObject *object) {
    require_sequence(object,2);w::Curve result;
    for(Py_ssize_t i=0;i<size(object);++i) {
        double p[2];world_array(items(object)[i],p,2);
        const std::array<float,2> node={{static_cast<float>(p[0]),static_cast<float>(p[1])}};
        if(!std::isfinite(node[0]) || !std::isfinite(node[1])) throw Unsupported();
        result.push_back(node);
    }return result;
}
w::AimConfig aim_config(PyObject *object) {
    require_sequence(object,7,true);auto **p=items(object);w::AimConfig result;
    result.fixed_pitch=!absent(p[6]);
    if(result.fixed_pitch) world_array(p[6],result.fixed_limits.data(),2);
    else {
        require_sequence(p[0],0);require_sequence(p[1],0);
        if(size(p[0]) || size(p[1])) {result.minimum=curve(p[0]);result.maximum=curve(p[1]);}
    }
    result.turret_speed=finite_number(p[2]);
    result.gun_speed=finite_number(p[3]);result.has_static_pitch=integer(p[4])!=0;result.static_pitch=finite_number(p[5]);
    result.validate();return result;
}
w::AimInput aim_input(PyObject *object) {
    double p[14];world_array(object,p,14);w::AimInput result;
    result.raw_yaw=p[0];result.raw_pitch=p[1];result.dt=p[2];result.target=p[3]!=0.;
    result.limited_yaw=p[4]!=0.;result.valid_pitch=p[5]!=0.;result.minimum_yaw=p[6];result.maximum_yaw=p[7];
    result.crew_factor=p[8];result.turret_factor=p[9];result.gun_factor=p[10];
    result.override_pitch=p[11]!=0.;result.pitch_limits={{p[12],p[13]}};return result;
}
PyObject *snapshot(const w::ActorState &state) {
    const auto &g=state.gun;const auto &a=state.ammo;const auto &b=state.burst;const auto &s=state.aim;
    const double gun[]={double(g.clip),g.elapsed,g.reload_duration,g.intra?1.:0.,g.reload_factor,
        double(g.burst_remaining),g.current_dispersion_factor,g.aiming_start_factor,g.aiming_elapsed,
        g.dispersion,g.motion_dispersion_squared};
    const double clock[]={b.active?1.:0.,double(b.group_seq),double(b.count),double(b.next_index),
        b.interval,b.time_left,double(b.shell)};
    const double aim[]={s.turret_yaw,s.gun_pitch,s.desired_gun_pitch,s.aligned?1.:0.,
        s.limits[0],s.limits[1],s.limits_valid?1.:0.};
    OwnedTuple result(5),inventory(6);inventory.set(0,integer_rows(a.categories));inventory.set(1,integer_rows(a.remaining));
    inventory.set(2,int_new(a.loaded));inventory.set(3,int_new(a.next));inventory.set(4,int_new(a.reload_pending));
    inventory.set(5,int_new(a.plan_pending));result.set(0,world_numbers(gun,11));result.set(1,inventory.release());
    result.set(2,world_numbers(clock,7));result.set(3,world_numbers(aim,7));result.set(4,integer_result(state.fire_seq));return result.release();
}
// Fixed reviewed launch shape, with no opaque Python-owned payload.
w::LaunchRecord launch(PyObject *object,ActorKey owner) {
    require_sequence(object,12,true);auto **p=items(object);w::LaunchRecord result;result.actor=owner;
    result.edge=edge(p[0]);result.launch_time_us=wide(p[1]);double pose[6];world_array(p[2],pose,6);
    result.pose.position={{pose[0],pose[1],pose[2]}};result.pose.yaw=pose[3];result.pose.pitch=pose[4];result.pose.roll=pose[5];
    result.yaw=finite_number(p[3]);result.pitch=finite_number(p[4]);
    require_sequence(p[5],0);for(Py_ssize_t i=0;i<size(p[5]);++i) {
        const int byte=integer(items(p[5])[i]);if(byte<0 || byte>255) throw Unsupported();
        result.class_tag.push_back(static_cast<char>(byte));
    }
    result.has_origin=!absent(p[6]);if(result.has_origin) world_array(p[6],result.origin.data(),3);
    result.artillery=!absent(p[7]);if(result.artillery) world_array(p[7],result.velocity.data(),3);
    double physical[3];world_array(p[8],physical,3);result.gravity=physical[0];result.maximum_distance=physical[1];
    result.maximum_time_ms=wide(items(p[8])[2]);result.has_shells_before=integer(p[9])!=0;result.shells_before=integer(p[10]);
    require_sequence(p[11],0);for(Py_ssize_t i=0;i<size(p[11]);++i) result.proof.push_back(finite_number(items(p[11])[i]));
    if(result.artillery && result.proof.size()!=14) throw Unsupported();
    return result;
}
PyObject *launch_result(const w::LaunchRecord &v) {
    OwnedTuple result(14);result.set(0,int_new(static_cast<int>(v.actor.kind)));result.set(1,integer_result(v.actor.id));
    const auto &e=v.edge;const double edge[]={double(e.shot_seq),double(e.group_seq),double(e.index),double(e.count),
        double(e.shell),e.final_round?1.:0.,e.due_offset};result.set(2,world_numbers(edge,7));
    result.set(3,simulation_wide_result(v.launch_time_us));const double pose[]={v.pose.position[0],v.pose.position[1],v.pose.position[2],v.pose.yaw,v.pose.pitch,v.pose.roll};
    result.set(4,world_numbers(pose,6));result.set(5,float_new(v.yaw));result.set(6,float_new(v.pitch));
    std::vector<int> tag;for(unsigned char byte:v.class_tag) tag.push_back(byte);result.set(7,integer_rows(tag));
    result.set(8,v.has_origin?world_numbers(v.origin.data(),3):fallback());result.set(9,v.artillery?world_numbers(v.velocity.data(),3):fallback());
    const double physical[]={v.gravity,v.maximum_distance,double(v.maximum_time_ms)};result.set(10,world_numbers(physical,3));
    result.set(11,int_new(v.has_shells_before));result.set(12,int_new(v.shells_before));result.set(13,world_numbers(v.proof.data(),v.proof.size()));
    return result.release();
}
}

PyObject *WOT_CDECL sim_weapon_install(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_install);
    try {
        require_sequence(args,7,true);auto **p=items(args);auto ctx=simulation_context(weapon_bridge::wide(p[0]));
        const auto key=weapon_bridge::key(p[1],p[2]);const auto config=weapon_bridge::config(p[3]);const auto ammo=weapon_bridge::ammo(p[4]);
        profile.run(&weapon_bridge::w::Store::install,&ctx->weapons,key,config,ammo,weapon_bridge::wide(p[5]),finite_number(p[6]));
        return weapon_bridge::snapshot(ctx->weapons.at(key));
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_snapshot(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_snapshot);
    try {
        require_sequence(args,3,true);auto **p=items(args);auto ctx=simulation_context(weapon_bridge::wide(p[0]));
        const auto key=weapon_bridge::key(p[1],p[2]);
            profile.output();
        return weapon_bridge::snapshot(ctx->weapons.at(key));
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_remove(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_remove);
    try {
        require_sequence(args,3,true);auto **p=items(args);auto ctx=simulation_context(weapon_bridge::wide(p[0]));
        return int_new(profile.run(&weapon_bridge::w::Store::remove,&ctx->weapons,weapon_bridge::key(p[1],p[2])));
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_command(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_command);
    try {
        require_sequence(args,5,true);auto **p=items(args);auto ctx=simulation_context(weapon_bridge::wide(p[0]));
        const auto key=weapon_bridge::key(p[1],p[2]);const int op=weapon_bridge::integer(p[3]);
        profile.opcode(op);
        require_sequence(p[4],0);auto **v=items(p[4]);auto &state=ctx->weapons.at(key);auto &g=state.gun;
        const auto argc=size(p[4]);double answer=1.;PyObject *special=0;
        const auto arity=[&](int count){if(argc!=count) throw Unsupported();};
        const auto n=[&](int i){return finite_number(v[i]);};const auto z=[&](int i){return weapon_bridge::integer(v[i]);};
        switch(op) {
        case 1:arity(1);
            profile.run(&weapon_bridge::w::GunState::tick,&g,n(0));break;
        case 2:arity(6);
            profile.run(&weapon_bridge::w::GunState::tick_dispersion,&g,n(0),n(1),n(2),n(3),n(4),n(5));break;
        case 3:arity(2);
            profile.run(&weapon_bridge::w::GunState::bloom,&g,n(0),z(1)!=0);break;
        case 4:arity(1);answer=profile.run(&weapon_bridge::w::GunState::rescale,&g,n(0));break;
        case 5:arity(2);answer=profile.run(&weapon_bridge::w::GunState::complete,&g,n(0),z(1));break;
        case 6:arity(0);
            profile.run(&weapon_bridge::w::GunState::require_full,&g);break;
        case 7:arity(2);answer=profile.run(&weapon_bridge::w::GunState::begin,&g,z(0),n(1));break;
        case 8:arity(1);answer=profile.run(&weapon_bridge::w::GunState::consume,&g,z(0)!=0);break;
        case 9:arity(0);answer=profile.run(&weapon_bridge::w::GunState::cancel,&g);break;
        case 10:{arity(9);
            profile.run(&weapon_bridge::w::GunState::restore,&g,weapon_bridge::wide(v[0]),n(1),z(2)!=0,n(3),n(4),n(5),z(6)!=0,z(7),z(8));
            profile.input();
            const auto fire_seq=weapon_bridge::wide(v[0]);
            profile.compute();state.fire_seq=std::max<std::int64_t>(0,fire_seq);break;}
        case 11:arity(1);answer=profile.run(&weapon_bridge::w::GunState::adopt,&g,weapon_bridge::config(v[0]));break;
        case 12:arity(3);answer=profile.run(&weapon_bridge::w::AmmoState::stage,&state.ammo,z(0),z(1)!=0,z(2)!=0);break;
        case 13:arity(1);answer=profile.run(&weapon_bridge::w::AmmoState::consume,&state.ammo,z(0)!=0);break;
        case 14:{arity(1);auto ammo=weapon_bridge::ammo(v[0]);
            profile.compute();ammo.validate(g.config.shell_count);state.ammo=std::move(ammo);break;}
        case 15:arity(4);answer=profile.run(&weapon_bridge::w::BurstState::start,&state.burst,weapon_bridge::wide(v[0]),z(1),n(2),z(3));break;
        case 16:arity(1);special=weapon_bridge::edges(profile.run(&weapon_bridge::w::BurstState::advance,&state.burst,n(0)));break;
        case 17:arity(1);answer=profile.run(&weapon_bridge::w::BurstState::cancel,&state.burst,z(0));break;
        case 18:{arity(2);auto burst=weapon_bridge::burst(v[0]);
            const auto fire_seq=weapon_bridge::wide(v[1]);
            profile.compute();burst.validate(fire_seq);state.burst=burst;break;}
        case 19:arity(3);
            profile.run(&weapon_bridge::w::Store::prepare,&ctx->weapons,key,n(0),n(1),z(2));break;
        case 20:arity(3);answer=profile.run(&weapon_bridge::w::Store::begin,&ctx->weapons,key,z(0),n(1),n(2));break;
        case 21:arity(0);answer=profile.run(&weapon_bridge::w::Store::cancel,&ctx->weapons,key);break;
        case 22:{arity(1);if(z(0)<0 || z(0)>g.config.clip_size) throw Unsupported();
            const int remaining=z(0);
            profile.compute();g.burst_remaining=remaining;break;}
        case 23:{arity(4);const double yaw=n(0);
            profile.compute();state.aim.turret_yaw=yaw;
            profile.input();
            const double pitch=n(1);
            profile.compute();state.aim.gun_pitch=pitch;
            profile.input();
            const double desired=n(2);
            profile.compute();state.aim.desired_gun_pitch=desired;
            profile.input();
            const bool aligned=z(3)!=0;
            profile.compute();state.aim.aligned=aligned;break;}
        case 24:{arity(1);auto config=weapon_bridge::aim_config(v[0]);
            profile.compute();state.aim_config=std::move(config);state.has_aim=true;break;}
        case 25:{arity(7);auto aim=weapon_bridge::aim_input(v[0]);special=weapon_bridge::edges(profile.run(&weapon_bridge::w::Store::after_motion,&ctx->weapons,key,aim,n(1),n(2),n(3),n(4),n(5),z(6)!=0));break;}
        case 26:{arity(1);const auto limits=profile.run(&weapon_bridge::w::pitch_limits,n(0),state.aim_config);special=world_numbers(limits.data(),2);break;}
        case 27:{arity(11);auto copy=g;copy.clip=z(0);copy.elapsed=n(1);copy.reload_duration=n(2);copy.intra=z(3)!=0;
            copy.reload_factor=n(4);copy.burst_remaining=z(5);copy.current_dispersion_factor=n(6);copy.aiming_start_factor=n(7);
            copy.aiming_elapsed=n(8);copy.dispersion=n(9);copy.motion_dispersion_squared=n(10);
            profile.compute();
            if(copy.clip<0 || copy.clip>g.config.clip_size || copy.burst_remaining<0 || copy.burst_remaining>copy.clip ||
                copy.elapsed<0. || copy.reload_duration<0. || copy.reload_factor<0. || copy.current_dispersion_factor<=0. ||
                copy.aiming_start_factor<=0. || copy.aiming_elapsed<0. || copy.dispersion<=0. || copy.motion_dispersion_squared<0.) throw Unsupported();
            g=copy;break;}
        default:throw Unsupported();
        }
        profile.output();
        OwnedTuple result(2);result.set(0,special?special:float_new(answer));result.set(1,weapon_bridge::snapshot(state));return result.release();
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_aim(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_aim);
    try {
        require_sequence(args,4,true);auto **p=items(args);auto ctx=simulation_context(weapon_bridge::wide(p[0]));
        auto &state=ctx->weapons.at(weapon_bridge::key(p[1],p[2]));if(!state.has_aim) throw Unsupported();
        profile.run(&weapon_bridge::w::advance_aim,state.aim,state.aim_config,weapon_bridge::aim_input(p[3]));
        return weapon_bridge::snapshot(state);
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_ballistic(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_ballistic);
    try {
        require_sequence(args,3,true);auto **p=items(args);simulation_context(weapon_bridge::wide(p[0]));
        const int op=weapon_bridge::integer(p[1]);
            profile.opcode(op);require_sequence(p[2],0);auto **v=items(p[2]);
        if(op==0) {
            require_sequence(p[2],9,true);offline_simulation::Point start,target,velocity;
            world_array(v[0],start.data(),3);world_array(v[1],target.data(),3);world_array(v[2],velocity.data(),3);
            const auto solution=profile.run(&weapon_bridge::w::ballistic_intercept,start,target,velocity,finite_number(v[3]),finite_number(v[4]),
                finite_number(v[5]),finite_number(v[6]),weapon_bridge::integer(v[7])!=0,finite_number(v[8]));
            if(!solution.valid) {OwnedTuple empty(0);return empty.release();}
            OwnedTuple row(3);row.set(0,world_numbers(solution.aim.data(),3));row.set(1,float_new(solution.pitch));row.set(2,float_new(solution.time));return row.release();
        }
        if(op==1) {
            require_sequence(p[2],4,true);offline_simulation::Point direction;world_array(v[0],direction.data(),3);
            const auto result=profile.run(&weapon_bridge::w::scatter,direction,finite_number(v[1]),finite_number(v[2]),weapon_bridge::integer(v[3])!=0);
            return world_numbers(result.data(),2);
        }
        if(op==2) {
            require_sequence(p[2],6,true);offline_simulation::Point start;world_array(v[0],start.data(),3);
            const auto result=profile.run(&weapon_bridge::w::ballistic_position,start,finite_number(v[1]),finite_number(v[2]),finite_number(v[3]),finite_number(v[4]),finite_number(v[5]));
            return world_numbers(result.data(),3);
        }
        throw Unsupported();
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_commit(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_commit);
    try {
        require_sequence(args,6,true);auto **p=items(args);auto ctx=simulation_context(weapon_bridge::wide(p[0]));const auto key=weapon_bridge::key(p[1],p[2]);
        const auto launch=weapon_bridge::launch(p[3],key);const double factor=finite_number(p[4]);const bool debit=weapon_bridge::integer(p[5])!=0;
        const bool result=debit?profile.run(&weapon_bridge::w::Store::commit,&ctx->weapons,key,launch,factor):profile.run(&weapon_bridge::w::Store::enqueue,&ctx->weapons,launch);
        OwnedTuple row(2);row.set(0,int_new(result));row.set(1,weapon_bridge::snapshot(ctx->weapons.at(key)));return row.release();
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_pending(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_pending);
    try {
        require_sequence(args,1,true);auto ctx=simulation_context(weapon_bridge::wide(items(args)[0]));
        profile.output();const auto &rows=ctx->weapons.pending();OwnedTuple result(rows.size());for(size_t i=0;i<rows.size();++i) result.set(i,weapon_bridge::launch_result(rows[i]));return result.release();
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}
PyObject *WOT_CDECL sim_weapon_ack(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_weapon_ack);
    try {
        require_sequence(args,4,true);auto **p=items(args);auto ctx=simulation_context(weapon_bridge::wide(p[0]));
        return int_new(profile.run(&weapon_bridge::w::Store::ack,&ctx->weapons,weapon_bridge::key(p[1],p[2]),weapon_bridge::wide(p[3])));
    }catch(const PythonAllocationFailed &){ profile.fail();return 0;}catch(...){ profile.fail();return fallback();}
}

#define WOT_SIM_WEAPON_METHODS \
    {"sim_weapon_install",sim_weapon_install,1,"Install one persistent weapon actor."}, \
    {"sim_weapon_snapshot",sim_weapon_snapshot,1,"Read a weapon observation mirror."}, \
    {"sim_weapon_command",sim_weapon_command,1,"Apply a typed weapon transition or phase."}, \
    {"sim_weapon_remove",sim_weapon_remove,1,"Remove a settled weapon actor."}, \
    {"sim_weapon_aim",sim_weapon_aim,1,"Advance persistent turret and gun state."}, \
    {"sim_weapon_ballistic",sim_weapon_ballistic,1,"Run exact ballistic or frozen-entropy math."}, \
    {"sim_weapon_commit",sim_weapon_commit,1,"Atomically debit and freeze one physical shot."}, \
    {"sim_weapon_pending",sim_weapon_pending,1,"Read immutable pending physical launches."}, \
    {"sim_weapon_ack",sim_weapon_ack,1,"Acknowledge only an actor's pending launch head."},
