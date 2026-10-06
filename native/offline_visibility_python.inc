// Shared tuple/number helpers are defined by offline_navigation_python.inc.
size_t foliage_index(PyObject *object) {
    const int64_t value=identity(object);if (value<0) throw Unsupported();
    return static_cast<size_t>(value);
}
native_visibility::Volume foliage_volume(PyObject *object) {
    require_sequence(object,2);PyObject **row=items(object);native_visibility::Volume value;
    const int kind=index_value(row[0]);
    if (kind==0) {
        require_sequence(object,2,true);require_sequence(row[1],10,true);
        for (size_t index=0;index<10;++index) value.static_row[index]=finite_number(items(row[1])[index]);
    } else if (kind==1) {
        require_sequence(object,5,true);value.dynamic=true;value.center=point_values(row[1]);
        require_sequence(row[2],3,true);
        for (size_t index=0;index<3;++index) value.half_axes[index]=point_values(items(row[2])[index]);
        value.strength=finite_number(row[3]);value.radius=finite_number(row[4]);
    } else throw Unsupported();
    return value;
}
std::vector<std::pair<native_visibility::Cell,std::vector<size_t>>> foliage_cells(PyObject *object) {
    require_sequence(object,0);
    std::vector<std::pair<native_visibility::Cell,std::vector<size_t>>> out;
    out.reserve(static_cast<size_t>(size(object)));
    for (Py_ssize_t index=0;index<size(object);++index) {
        PyObject *item=items(object)[index];require_sequence(item,3,true);PyObject **row=items(item);
        require_sequence(row[2],0);std::vector<size_t> values;values.reserve(static_cast<size_t>(size(row[2])));
        for (Py_ssize_t at=0;at<size(row[2]);++at) values.push_back(foliage_index(items(row[2])[at]));
        out.emplace_back(native_visibility::Cell(index_value(row[0]),index_value(row[1])),std::move(values));
    }
    return out;
}
std::set<size_t> foliage_inactive(PyObject *object) {
    require_sequence(object,0);std::set<size_t> out;
    for (Py_ssize_t index=0;index<size(object);++index) out.insert(foliage_index(items(object)[index]));
    return out;
}
native_visibility::Checkpoints visibility_checkpoints(PyObject *object) {
    require_sequence(object,3,true);PyObject **row=items(object);native_visibility::Checkpoints value;
    require_sequence(row[0],0);
    if (size(row[0])) {
        require_sequence(row[0],6,true);value.available=true;
        for (size_t index=0;index<6;++index) value.points[index]=point_values(items(row[0])[index]);
    }
    if (row[1]!=none_object) {value.turret_mount_available=true;value.turret_mount=point_values(row[1]);}
    if (row[2]!=none_object) {value.static_turret_yaw_present=true;value.static_turret_yaw=finite_number(row[2]);}
    return value;
}
native_visibility::Pose visibility_pose(PyObject *object) {
    require_sequence(object,5,true);PyObject **row=items(object);native_visibility::Pose value;
    value.position=point_values(row[0]);value.yaw=finite_number(row[1]);value.pitch=finite_number(row[2]);
    value.roll=finite_number(row[3]);value.relative_turret_yaw=finite_number(row[4]);return value;
}
native_visibility::PairInput visibility_pair(PyObject *object) {
    require_sequence(object,6,true);PyObject **row=items(object);native_visibility::PairInput value;
    value.observer_checkpoints=visibility_checkpoints(row[0]);value.target_checkpoints=visibility_checkpoints(row[1]);
    value.observer=visibility_pose(row[2]);value.target=visibility_pose(row[3]);
    const int64_t phase=identity(row[4]);if (phase<0) throw Unsupported();
    value.observer_phase=static_cast<std::uint64_t>(phase);
    require_sequence(row[5],8,true);PyObject **d=items(row[5]);
    value.detection.distance=finite_number(d[0]);value.detection.view_range=finite_number(d[1]);
    require_sequence(d[2],2,true);value.detection.base_camouflage={{finite_number(items(d[2])[0]),finite_number(items(d[2])[1])}};
    value.detection.moving=number(d[3])!=0.;value.detection.fired_recently=number(d[4])!=0.;
    value.detection.additive=finite_number(d[5]);value.detection.multiplier=finite_number(d[6]);
    value.detection.shot_factor=finite_number(d[7]);return value;
}
PyObject *WOT_CDECL vis_open(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_open);
    try {
        require_sequence(args,1,true);PyObject *object=items(args)[0];require_sequence(object,5,true);
        PyObject **row=items(object);native_visibility::FoliageSnapshot snapshot;
        snapshot.enabled=number(row[0])!=0.;snapshot.cell_size=finite_number(row[1]);
        require_sequence(row[2],0);snapshot.instances.reserve(static_cast<size_t>(size(row[2])));
        for (Py_ssize_t index=0;index<size(row[2]);++index) snapshot.instances.push_back(foliage_volume(items(row[2])[index]));
        for (auto &entry:foliage_cells(row[3])) {
            for (size_t index:entry.second) if (index>=snapshot.instances.size()) throw Unsupported();
            snapshot.cells[entry.first]=std::move(entry.second);
        }
        snapshot.inactive_instances=foliage_inactive(row[4]);
        return integer_result(profile.run(offline_visibility::open,std::move(snapshot)));
    } catch (const PythonAllocationFailed &) { profile.fail();return 0;} catch (...) { profile.fail();return fallback();}
}
PyObject *WOT_CDECL vis_update(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_update);
    try {
        require_sequence(args,4,true);PyObject **row=items(args);offline_visibility::Update changes;
        require_sequence(row[1],0);changes.instances.reserve(static_cast<size_t>(size(row[1])));
        for (Py_ssize_t index=0;index<size(row[1]);++index) {
            PyObject *item=items(row[1])[index];require_sequence(item,2,true);
            changes.instances.emplace_back(foliage_index(items(item)[0]),foliage_volume(items(item)[1]));
        }
        changes.cells=foliage_cells(row[2]);changes.inactive=foliage_inactive(row[3]);
        return int_new(profile.run(offline_visibility::update,identity(row[0]),std::move(changes))?1:0);
    } catch (const PythonAllocationFailed &) { profile.fail();return 0;} catch (...) { profile.fail();return fallback();}
}
PyObject *WOT_CDECL vis_submit(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_submit);
    try {
        require_sequence(args,3,true);PyObject **row=items(args);
        return int_new(profile.run(offline_visibility::submit,identity(row[0]),identity(row[1]),visibility_pair(row[2]))?1:0);
    } catch (const PythonAllocationFailed &) { profile.fail();return 0;} catch (...) { profile.fail();return fallback();}
}
PyObject *WOT_CDECL vis_poll(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_poll);
    try {
        require_sequence(args,1,true);const auto data=profile.run(offline_visibility::poll,identity(items(args)[0]));OwnedTuple result(data.size());
        for (size_t index=0;index<data.size();++index) {
            const auto &value=data[index];OwnedTuple row(4),rays(value.rays.size());
            row.set(0,integer_result(value.job_id));row.set(1,string_new(value.status==0?"done":value.status==1?"cancelled":"failed"));
            for (size_t at=0;at<value.rays.size();++at) {
                OwnedTuple ray(3);ray.set(0,point_result(value.rays[at].start));ray.set(1,point_result(value.rays[at].end));
                ray.set(2,float_new(value.rays[at].foliage_bonus));rays.set(at,ray.release());
            }
            row.set(2,rays.release());row.set(3,float_new(value.worker_seconds));result.set(index,row.release());
        }
        return result.release();
    } catch (const PythonAllocationFailed &) { profile.fail();return 0;} catch (...) { profile.fail();return fallback();}
}
PyObject *WOT_CDECL vis_reduce(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_reduce);
    try {
        require_sequence(args,3,true);PyObject **row=items(args);require_sequence(row[2],0);std::vector<std::uint8_t> clear;
        clear.reserve(static_cast<size_t>(size(row[2])));
        for (Py_ssize_t index=0;index<size(row[2]);++index) clear.push_back(number(items(row[2])[index])!=0.);
        native_visibility::VisibilityResult value;
        if (!profile.run(offline_visibility::reduce,identity(row[0]),identity(row[1]),clear,value)) return fallback();
        OwnedTuple result(5);result.set(0,int_new(value.line_of_sight?1:0));result.set(1,float_new(value.foliage_bonus));
        result.set(2,float_new(value.camouflage));result.set(3,int_new(value.detected?1:0));result.set(4,float_new(value.detection_distance));return result.release();
    } catch (const PythonAllocationFailed &) { profile.fail();return 0;} catch (...) { profile.fail();return fallback();}
}
PyObject *WOT_CDECL vis_cancel(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_cancel);
    try {
        require_sequence(args,2,true);PyObject **row=items(args);require_sequence(row[1],0);std::vector<int64_t> jobs;
        for (Py_ssize_t index=0;index<size(row[1]);++index) jobs.push_back(identity(items(row[1])[index]));
        profile.run(offline_visibility::cancel,identity(row[0]),jobs);return int_new(1);
    } catch (...) { profile.fail();return fallback();}
}
PyObject *WOT_CDECL vis_close(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_close);
    try {require_sequence(args,1,true);profile.run(offline_visibility::close,identity(items(args)[0]));return int_new(1);}
    catch (...) { profile.fail();return fallback();}
}

// Actor geometry and both consumer poses are installed once per caller frame.
// The identity serial belongs to the Python cold owner, not to a raw address.
offline_visibility::FrontierActor visibility_frontier_actor(PyObject *object) {
    require_sequence(object,6,true);PyObject **row=items(object);
    require_sequence(row[0],2,true);
    offline_visibility::FrontierActor actor;
    actor.key={{identity(items(row[0])[0]),identity(items(row[0])[1])}};
    if(actor.key[0]<0 || actor.key[0]>1 || actor.key[1]<=0)throw Unsupported();
    const auto serial=identity(row[1]);if(serial<0)throw Unsupported();
    if(serial==0) {
        if(row[2]!=none_object || row[3]!=none_object || row[4]!=none_object)throw Unsupported();
        return actor;
    }
    actor.identity=static_cast<uint64_t>(serial);
    actor.checkpoints=visibility_checkpoints(row[2]);
    actor.observer_pose=visibility_pose(row[3]);actor.target_pose=visibility_pose(row[4]);
    actor.target_available=number(row[5])!=0.;
    return actor;
}
PyObject *WOT_CDECL vis_frontier_frame(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_frontier_frame);
    try {
        require_sequence(args,4,true);PyObject **row=items(args);require_sequence(row[1],0);
        std::vector<offline_visibility::FrontierActor> actors;actors.reserve(size(row[1]));
        for(Py_ssize_t i=0;i<size(row[1]);++i)actors.push_back(visibility_frontier_actor(items(row[1])[i]));
        const auto phase=identity(row[2]);if(phase<0)throw Unsupported();
        return int_new(profile.run(offline_visibility::frontier_frame,identity(row[0]),
            std::move(actors),static_cast<uint64_t>(phase),finite_number(row[3]))?1:0);
    } catch(const PythonAllocationFailed &){profile.fail();return 0;}
      catch(...){profile.fail();return fallback();}
}
PyObject *WOT_CDECL vis_frontier_actor(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_frontier_actor);
    try {
        require_sequence(args,2,true);PyObject **row=items(args);
        return int_new(profile.run(offline_visibility::frontier_actor,identity(row[0]),
                                   visibility_frontier_actor(row[1]))?1:0);
    } catch(const PythonAllocationFailed &){profile.fail();return 0;}
      catch(...){profile.fail();return fallback();}
}

// Called directly by the control bridge. Capabilities are retained only for
// this synchronous invocation; all background records remain plain C++ data.
offline_simulation::control::SightReply visibility_control_sight(
        PyObject *binding,const offline_simulation::control::SightRequest &request) {
    using namespace offline_simulation;
    control::SightReply result;result.sampled_at=request.now;
    try {
        require_sequence(binding,8,true);PyObject **caps=items(binding);
        const auto context=identity(caps[0]);const double tolerance=finite_number(caps[7]);
        engine_query::Session engine(caps[1]);
        native_visibility::DetectionInputs detection;
        detection.distance=request.detection.distance;detection.view_range=request.detection.view;
        detection.base_camouflage={{request.detection.camo_moving,request.detection.camo_still}};
        detection.moving=request.detection.moving;detection.fired_recently=request.detection.fired;
        detection.additive=request.detection.additive;detection.multiplier=request.detection.multiplier;
        detection.shot_factor=request.detection.shot_factor;
        const auto call=[&](PyObject *function,OwnedTuple &args,int opcode) {
            WorldObject arguments(args.release());
            auto result=engine_query::Object(profiled_object_call(NativeCallback::ControlSight,
                "visibility.engine",opcode,1,function,arguments.get()));
            if(result.get()==items(caps[1])[engine_query::Failure])throw engine_query::Failed();
            return result;
        };
        // Engine/contact callbacks in an earlier pair may have changed the
        // foliage ledger. Synchronize it before inspecting any ready receipt.
        OwnedTuple sync_args(0);auto synced=call(caps[6],sync_args,4);
        require_sequence(synced.get(),2,true);
        auto collision_filter=engine_query::Object::borrow(items(synced.get())[0]);
        const double phase_value=std::floor(std::max(0.,finite_number(items(synced.get())[1]))/2000.);
        if(phase_value>=18446744073709551616.0)throw Unsupported();
        const uint64_t observer_phase=static_cast<uint64_t>(phase_value);
        const auto ray=[&](const native_visibility::Ray &row) {
            NativeProfilePhase phase(NativePhase::Pack);
            auto start=engine.vector(row.start.data()),end=engine.vector(row.end.data());
            auto hit=engine.collide(start.get(),end.get(),collision_filter.get());
            phase.input();
            bool clear=hit.get()==none_object;
            if(!clear) {
                require_sequence(hit.get(),1);
                // Keep actual Vector3 subtraction/length and its float32 law.
                auto hit_delta=engine.subtract(items(hit.get())[0],start.get());
                const double a=engine.length(hit_delta.get());
                auto end_delta=engine.subtract(end.get(),start.get());
                const double b=engine.length(end_delta.get());
                clear=a+tolerance>=b;
                if(!clear && caps[3]!=none_object) {
                    OwnedTuple args(4);engine_query::borrow(args,0,caps[4]);
                    engine_query::borrow(args,1,start.get());engine_query::borrow(args,2,end.get());
                    engine_query::borrow(args,3,hit.get());auto ignored=call(caps[3],args,2);
                }
            }
            return clear;
        };
        const offline_visibility::ActorKey observer={{static_cast<int64_t>(request.observer.kind),request.observer.id}};
        const offline_visibility::ActorKey target={{static_cast<int64_t>(request.target.kind),request.target.id}};
        const auto phase=[&]() {return observer_phase;};
        const auto reply=offline_visibility::frontier_detect(context,observer,target,request.now,
                                                           request.fire_sequence,detection,ray,phase,request.world_query);
        // A completed detection is consumed before the generic LOS branch.
        // No omitted checkpoint is published as blocked or given a cover value.
        result.status=reply.status==0?QueryStatus::DetectionComplete:static_cast<QueryStatus>(reply.status);
        result.has_detection=reply.status==0;result.detected=reply.detected;
        result.sampled_at=reply.sampled_at;
    } catch(const engine_query::Failed &) {
        // An attempted engine query never replays through the Python frontier.
        result.status=QueryStatus::Failed;
    } catch(const PythonAllocationFailed &) {
        // Preserve the active Python allocation error for the outer C API.
        throw;
    } catch(...) {
        // A malformed local engine result fails only this observation.
        result.status=QueryStatus::Failed;
    }
    return result;
}

PyObject *WOT_CDECL vis_frontier_snapshot(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(vis_frontier_snapshot);
    try {
        require_sequence(args,1,true);
        const auto value=profile.run(offline_visibility::frontier_snapshot,identity(items(args)[0]));
        OwnedTuple out(8),reasons(value.reasons.size());
        out.set(0,integer_result(value.submitted));out.set(1,integer_result(value.completed));
        out.set(2,integer_result(value.cancelled));out.set(3,integer_result(value.pending));
        out.set(4,float_new(value.worker_seconds));out.set(5,float_new(value.max_completion_age));
        for(size_t i=0;i<value.reasons.size();++i)reasons.set(i,integer_result(value.reasons[i]));
        out.set(6,reasons.release());out.set(7,integer_result(value.requests));return out.release();
    } catch(const PythonAllocationFailed &){profile.fail();return 0;}
      catch(...){profile.fail();return fallback();}
}
