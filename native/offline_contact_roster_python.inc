double contact_number(PyObject *object,Field key,double default_value=0.) {
    PyObject *value=field(object,key);
    return absent(value)?default_value:finite_number(value);
}
bool contact_flag(PyObject *object,Field key,bool default_value) {
    PyObject *value=field(object,key);
    if (!value) return default_value;
    return value!=none_object && finite_number(value)!=0.;
}
PyObject *contact_pair_result(const std::array<double,2> &value) {
    OwnedTuple pair(2);
    for (size_t index=0;index<2;++index) {
        if (!std::isfinite(value[index])) throw Unsupported();
        pair.set(index,float_new(value[index]));
    }
    return pair.release();
}
PyObject *WOT_CDECL contact_roster(PyObject *,PyObject *args) {
    try {
        require_sequence(args,4,true);PyObject **input=items(args);
        require_sequence(input[0],0);require_sequence(input[1],0);require_sequence(input[3],0);
        const double dt=finite_number(input[2]);
        std::vector<int64_t> owners;std::set<int64_t> owner_ids;
        owners.reserve(static_cast<size_t>(size(input[1])));
        for (Py_ssize_t index=0;index<size(input[1]);++index) {
            const auto id=identity(items(input[1])[index]);
            if (!owner_ids.insert(id).second) throw Unsupported();
            owners.push_back(id);
        }
        std::vector<std::pair<int64_t,int64_t>> previous;
        previous.reserve(static_cast<size_t>(size(input[3])));
        for (Py_ssize_t index=0;index<size(input[3]);++index) {
            PyObject *value=items(input[3])[index];require_sequence(value,2,true);
            previous.emplace_back(identity(items(value)[0]),identity(items(value)[1]));
        }
        std::vector<offline_contact::Body> bodies;bodies.reserve(static_cast<size_t>(size(input[0])));
        for (Py_ssize_t index=0;index<size(input[0]);++index) {
            PyObject *value=items(input[0])[index];
            const auto pose=body_values(value,false);offline_contact::Body body;
            body.id=pose.id;body.x=pose.x;body.y=pose.y;body.z=pose.z;
            body.yaw=pose.yaw;body.pitch=pose.pitch;body.roll=pose.roll;body.shape=pose.shape;
            for (double number:{body.x,body.y,body.z,body.yaw,body.pitch,body.roll})
                if (!std::isfinite(number)) throw Unsupported();
            for (double number:body.shape) if (!std::isfinite(number)) throw Unsupported();
            if (body.shape[0]<=0. || body.shape[1]<=0. || body.shape[3]<body.shape[2]) throw Unsupported();
            body.flags=0;
            if (pose.has_y) body.flags|=offline_contact::HasY;
            if (contact_flag(value,ALIVE,true)) body.flags|=offline_contact::Alive;
            if (contact_flag(value,IMMOVABLE,false)) body.flags|=offline_contact::Immovable;
            if (contact_flag(value,IMPULSE,true)) body.flags|=offline_contact::Impulse;
            if (contact_flag(value,POSITION_FIXED,false)) body.flags|=offline_contact::PositionFixed;
            if (!owner_ids.count(body.id)) body.flags|=offline_contact::Player;
            body.mass=contact_number(value,MASS,1.);if (body.mass<=0.) throw Unsupported();
            body.mass=std::max(1.,body.mass);
            body.vx=contact_number(value,VX);body.vy=contact_number(value,VY);body.vz=contact_number(value,VZ);
            body.push_yaw=contact_number(value,PUSH_YAW);
            body.traverse_speed=contact_number(value,TRAVERSE_SPEED);body.traverse_torque=contact_number(value,TRAVERSE_TORQUE);
            PyObject *team=field(value,TEAM);body.team=absent(team)?0:identity(team);
            PyObject *grip=field(value,GRIP);
            if (!absent(grip)) {
                require_sequence(grip,2,true);body.flags|=offline_contact::HasGrip;
                body.grip[0]=finite_number(items(grip)[0]);body.grip[1]=finite_number(items(grip)[1]);
            }
            bodies.push_back(body);
        }
        const auto rows=offline_contact::resolve(bodies,owners,dt,previous);
        OwnedTuple result(rows.size());
        for (size_t index=0;index<rows.size();++index) {
            const auto &value=rows[index];OwnedTuple row(8),retained(value.retained_contacts.size()),ram(value.ram_candidates.size());
            row.set(0,integer_result(value.id));row.set(1,contact_pair_result(value.correction));
            row.set(2,contact_pair_result(value.delta_velocity));
            if (!std::isfinite(value.delta_yaw)) throw Unsupported();
            row.set(3,float_new(value.delta_yaw));row.set(4,int_new(value.has_peers?1:0));row.set(5,int_new(value.has_active_peer?1:0));
            for (size_t at=0;at<value.retained_contacts.size();++at) {
                OwnedTuple pair(2);pair.set(0,integer_result(value.retained_contacts[at].first));
                pair.set(1,integer_result(value.retained_contacts[at].second));retained.set(at,pair.release());
            }
            for (size_t at=0;at<value.ram_candidates.size();++at) {
                const auto &candidate=value.ram_candidates[at];OwnedTuple item(6);
                if (!std::isfinite(candidate.closing_speed) || !std::isfinite(candidate.relative_speed)) throw Unsupported();
                item.set(0,integer_result(candidate.other_id));item.set(1,point_result(candidate.impact));
                item.set(2,float_new(candidate.closing_speed));item.set(3,float_new(candidate.relative_speed));
                item.set(4,int_new(candidate.moving_self?1:0));item.set(5,int_new(candidate.moving_other?1:0));ram.set(at,item.release());
            }
            row.set(6,retained.release());row.set(7,ram.release());result.set(index,row.release());
        }
        return result.release();
    } catch (const PythonAllocationFailed &) {return 0;} catch (...) {return fallback();}
}
