// Pure copied geometry only: no Python references or engine capabilities survive
// these synchronous calls. Include after the common owned-tuple helpers.
namespace destructible_bridge {
namespace d = offline_destructible;
std::int64_t wide(PyObject *object) {
    const double value=finite_number(object);
    if(std::floor(value)!=value||std::abs(value)>9007199254740991.)throw Unsupported();
    return static_cast<std::int64_t>(value);
}
d::Key key(PyObject *object) {
    require_sequence(object,2,true);return {wide(items(object)[0]),index_value(items(object)[1])};
}
d::Point point(PyObject *object) {d::Point result;world_array(object,result.data(),3);return result;}
d::Cell cell(PyObject *object) {
    require_sequence(object,2,true);return {index_value(items(object)[0]),index_value(items(object)[1])};
}
d::Box box(PyObject *object) {
    require_sequence(object,2);auto **v=items(object);d::Box result;result.center=point(v[0]);
    require_sequence(v[1],0);if(size(v[1])>4)throw Unsupported();
    for(Py_ssize_t i=0;i<size(v[1]);++i)result.axes.push_back(point(items(v[1])[i]));
    if(size(object)>2)result.material=absent(v[2])?-1:index_value(v[2]);
    return result;
}
std::vector<d::Box> boxes(PyObject *object) {
    require_sequence(object,0);std::vector<d::Box> values;for(Py_ssize_t i=0;i<size(object);++i)values.push_back(box(items(object)[i]));return values;
}
d::Pose pose(PyObject *object) {
    require_sequence(object,8,true);auto **v=items(object);d::Pose result;
    result.position=point(v[0]);result.yaw=finite_number(v[1]);result.pitch=finite_number(v[2]);result.roll=finite_number(v[3]);
    result.travel=finite_number(v[4]);result.explicit_motion_yaw=!absent(v[5]);
    if(result.explicit_motion_yaw)result.motion_yaw=finite_number(v[5]);
    result.minimum=point(v[6]);result.maximum=point(v[7]);
    return result;
}
PyObject *box_result(const d::Box &value) {
    OwnedTuple result(2),axes(value.axes.size());for(std::size_t i=0;i<value.axes.size();++i)axes.set(i,world_numbers(value.axes[i].data(),3));
    result.set(0,world_numbers(value.center.data(),3));result.set(1,axes.release());return result.release();
}
PyObject *indexes(const std::vector<int> &values) {
    OwnedTuple result(values.size());for(std::size_t i=0;i<values.size();++i)result.set(i,int_new(values[i]));return result.release();
}
PyObject *candidates(const std::vector<d::Candidate> &values) {
    OwnedTuple result(values.size());for(std::size_t i=0;i<values.size();++i){const auto &v=values[i];OwnedTuple row(4);
        row.set(0,float_new(double(v.key.first)));row.set(1,int_new(v.key.second));row.set(2,int_new(v.box));row.set(3,int_new(v.contact));result.set(i,row.release());}
    return result.release();
}
std::shared_ptr<d::Store> store(PyObject *object) {auto value=d::find(wide(object));if(!value)throw Unsupported();return value;}
}
PyObject *WOT_CDECL destr_open(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(destr_open);
    try {require_sequence(args,0,true);return integer_result(profile.run(offline_destructible::open));}
    catch(const PythonAllocationFailed &){profile.fail();return 0;}catch(...){profile.fail();return fallback();}
}
PyObject *WOT_CDECL destr_close(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(destr_close);
    try {require_sequence(args,1,true);profile.run(offline_destructible::close,destructible_bridge::wide(items(args)[0]));return int_new(1);}
    catch(const PythonAllocationFailed &){profile.fail();return 0;}catch(...){profile.fail();return fallback();}
}
PyObject *WOT_CDECL destr_update(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(destr_update);
    try {
        namespace d=offline_destructible;namespace b=destructible_bridge;
        require_sequence(args,3,true);auto **v=items(args);auto store=b::store(v[0]);const int op=index_value(v[1]);profile.opcode(op);
        if(op==0){require_sequence(v[2],4,true);auto **row=items(v[2]);d::Instance value;
            value.key=b::key(row[0]);value.kind=index_value(row[1]);if(value.kind<0||value.kind>2)throw Unsupported();value.boxes=b::boxes(row[2]);
            require_sequence(row[3],0);for(Py_ssize_t i=0;i<size(row[3]);++i)value.bins.insert(b::cell(items(row[3])[i]));
            profile.run(&d::Store::put,store.get(),std::move(value));
        }else if(op==1){profile.run(&d::Store::remove,store.get(),b::key(v[2]));
        }else if(op==2){require_sequence(v[2],4,true);auto **row=items(v[2]);const auto id=b::wide(row[0]);d::Chunk chunk;
            require_sequence(row[1],0);
            for(Py_ssize_t i=0;i<size(row[1]);++i){PyObject *raw=items(row[1])[i];require_sequence(raw,6,true);auto **r=items(raw);d::Item item;
                item.index=index_value(r[0]);item.position=b::point(r[1]);item.tree=index_value(r[2])!=0;item.named=index_value(r[3])!=0;
                item.catalog=index_value(r[4])!=0;item.radius=finite_number(r[5]);if(!chunk.items.emplace(item.index,item).second)throw Unsupported();}
            for(int part=2;part<=3;++part){require_sequence(row[part],0);auto &bins=part==2?chunk.origins:chunk.extended;
                for(Py_ssize_t i=0;i<size(row[part]);++i){PyObject *raw=items(row[part])[i];require_sequence(raw,2,true);auto **r=items(raw);auto cell=b::cell(r[0]);
                    require_sequence(r[1],0);std::vector<int> indexes;
                    for(Py_ssize_t j=0;j<size(r[1]);++j){int index=index_value(items(r[1])[j]);if(!chunk.items.count(index))throw Unsupported();indexes.push_back(index);}
                    if(!bins.emplace(cell,std::move(indexes)).second)throw Unsupported();}}
            profile.run(&d::Store::chunk,store.get(),id,std::move(chunk));
        }else if(op==3){profile.run(&d::Store::drop_chunk,store.get(),b::wide(v[2]));
        }else if(op==4){require_sequence(v[2],0);std::map<d::Cell,std::set<d::Key>> bins;
            for(Py_ssize_t i=0;i<size(v[2]);++i){PyObject *raw=items(v[2])[i];require_sequence(raw,2,true);auto **r=items(raw);auto cell=b::cell(r[0]);
                require_sequence(r[1],0);for(Py_ssize_t j=0;j<size(r[1]);++j)bins[cell].insert(b::key(items(r[1])[j]));}
            profile.run(&d::Store::baked,store.get(),std::move(bins));
        }else throw Unsupported();
        return int_new(1);
    }catch(const PythonAllocationFailed &){profile.fail();return 0;}catch(...){profile.fail();return fallback();}
}
PyObject *WOT_CDECL destr_query(PyObject *,PyObject *args) {
    NATIVE_PROFILE_ENTRY(destr_query);
    try {
        namespace d=offline_destructible;namespace b=destructible_bridge;
        require_sequence(args,3,true);auto **v=items(args);auto store=b::store(v[0]);const int op=index_value(v[1]);profile.opcode(op);
        if(op==0){require_sequence(v[2],2,true);auto **row=items(v[2]);const auto sweep=b::box(row[0]);d::Box contact;
            const bool has_contact=!absent(row[1]);if(has_contact)contact=b::box(row[1]);
            const auto value=profile.run(&d::Store::catalog,store.get(),sweep,has_contact?&contact:nullptr);
            OwnedTuple result(5);result.set(0,b::candidates(value.candidates));result.set(1,b::candidates(value.grouped));
            result.set(2,integer_result(value.members));result.set(3,integer_result(value.duplicates));result.set(4,int_new(value.had_members));return result.release();
        }else if(op==1){require_sequence(v[2],4,true);auto **row=items(v[2]);
            const auto value=profile.run(&d::Store::body,store.get(),b::wide(row[0]),b::pose(row[1]),finite_number(row[2]),b::box(row[3]));
            OwnedTuple result(3);result.set(0,b::indexes(value.items));result.set(1,integer_result(value.nearby));result.set(2,int_new(value.found_nearby));return result.release();
        }else if(op==2){require_sequence(v[2],3,true);auto **row=items(v[2]);
            const auto value=profile.run(&d::Store::trees,store.get(),b::wide(row[0]),b::boxes(row[1]),finite_number(row[2]));return b::indexes(value);
        }else if(op==3){const auto value=profile.run(&d::Store::missing,store.get(),b::box(v[2]));OwnedTuple result(value.size());
            for(std::size_t i=0;i<value.size();++i){OwnedTuple row(2);row.set(0,float_new(double(value[i].first)));row.set(1,int_new(value[i].second));result.set(i,row.release());}return result.release();
        }else if(op==4){return b::box_result(profile.run(d::vehicle_box,b::pose(v[2])));
        }else if(op==5){require_sequence(v[2],7,true);auto **row=items(v[2]);
            const auto value=profile.run(d::tree_sweep,b::point(row[0]),finite_number(row[1]),b::point(row[2]),finite_number(row[3]),b::point(row[4]),b::point(row[5]),finite_number(row[6]));
            if(value.empty())return fallback();
            OwnedTuple result(value.size());
            for(std::size_t i=0;i<value.size();++i)result.set(i,b::box_result(value[i]));
            return result.release();
        }else if(op==6){require_sequence(v[2],2,true);auto **row=items(v[2]);const auto sweep=b::box(row[0]);const auto values=b::boxes(row[1]);
            const auto indexes=profile.run([&]{std::vector<int> result;for(std::size_t i=0;i<values.size();++i)if(d::intersects(sweep,values[i]))result.push_back(static_cast<int>(i));return result;});return b::indexes(indexes);
        }
        throw Unsupported();
    }catch(const PythonAllocationFailed &){profile.fail();return 0;}catch(...){profile.fail();return fallback();}
}
#define WOT_DESTRUCTIBLE_METHODS \
    {"destr_open",destr_open,1,"Create one pure streamed-geometry index."}, \
    {"destr_close",destr_close,1,"Retire one streamed-geometry index."}, \
    {"destr_update",destr_update,1,"Apply one typed streamed geometry mutation."}, \
    {"destr_query",destr_query,1,"Run a complete indexed geometry query without engine calls."},
