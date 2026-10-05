// Included inside the CPython bridge's private namespace. Only these entry
// points inspect Python objects, synchronously under the calling thread's GIL.
struct PythonAllocationFailed {};
class OwnedTuple {
    PyObject *object_;
public:
    explicit OwnedTuple(size_t count):object_(tuple_new(static_cast<Py_ssize_t>(count))) {
        if (!object_) throw PythonAllocationFailed();
    }
    ~OwnedTuple() {decref(object_);}
    OwnedTuple(const OwnedTuple &)=delete;
    OwnedTuple &operator=(const OwnedTuple &)=delete;
    void set(size_t index,PyObject *value) {
        if (!value) throw PythonAllocationFailed();
        items(object_)[index]=value;
    }
    PyObject *release() {PyObject *value=object_;object_=0;return value;}
};
double finite_number(PyObject *object) {
    const double value=number(object);
    if (!std::isfinite(value)) throw Unsupported();
    return value;
}
int index_value(PyObject *object) {
    const int64_t value=identity(object);
    if (value<std::numeric_limits<int>::min() || value>std::numeric_limits<int>::max()) throw Unsupported();
    return static_cast<int>(value);
}
PyObject *integer_result(int64_t value) {
    if (value<std::numeric_limits<long>::min() || value>std::numeric_limits<long>::max()) throw Unsupported();
    return int_new(static_cast<long>(value));
}
offline_navigation::Point point_values(PyObject *object) {
    require_sequence(object,3,true);PyObject **row=items(object);
    return {{finite_number(row[0]),finite_number(row[1]),finite_number(row[2])}};
}
PyObject *point_result(const offline_navigation::Point &point) {
    OwnedTuple result(3);
    for (size_t index=0;index<3;++index) result.set(index,float_new(point[index]));
    return result.release();
}
std::vector<offline_navigation::EdgePenalty> edge_values(PyObject *object) {
    require_sequence(object,0);std::vector<offline_navigation::EdgePenalty> out;
    out.reserve(static_cast<size_t>(size(object)));
    for (Py_ssize_t index=0;index<size(object);++index) {
        PyObject *item=items(object)[index];require_sequence(item,5,true);PyObject **row=items(item);
        out.push_back({{{index_value(row[0]),index_value(row[1])}},
                       {{index_value(row[2]),index_value(row[3])}},finite_number(row[4])});
    }
    return out;
}
PyObject *WOT_CDECL nav_open(PyObject *,PyObject *args) {
    try {
        require_sequence(args,1,true);PyObject *value=items(args)[0];require_sequence(value,11,true);
        PyObject **row=items(value);offline_navigation::MapInput map;
        map.origin_x=finite_number(row[0]);map.origin_z=finite_number(row[1]);map.cell_size=finite_number(row[2]);
        map.width=index_value(row[3]);map.height=index_value(row[4]);
        if (map.width<=0 || map.height<=0 || static_cast<int64_t>(map.width)*map.height>std::numeric_limits<int>::max()) throw Unsupported();
        const size_t count=static_cast<size_t>(map.width)*static_cast<size_t>(map.height);
        for (unsigned field=5;field<=7;++field) require_sequence(row[field],static_cast<Py_ssize_t>(count),true);
        map.heights.reserve(count);map.links.reserve(count);map.hazards.reserve(count);
        for (size_t index=0;index<count;++index) {
            PyObject *height=items(row[5])[index];
            map.heights.push_back(height==none_object?std::numeric_limits<double>::quiet_NaN():finite_number(height)/1000.);
            map.links.push_back(static_cast<unsigned>(index_value(items(row[6])[index])));
            map.hazards.push_back(static_cast<unsigned>(index_value(items(row[7])[index])));
        }
        if (row[8]!=none_object) {
            require_sequence(row[8],4,true);map.has_bounds=true;
            for (size_t index=0;index<4;++index) map.bounds[index]=finite_number(items(row[8])[index]);
        }
        map.max_grade=finite_number(row[9]);map.heuristic_weight=finite_number(row[10]);
        return integer_result(offline_navigation::open(std::move(map)));
    } catch (const PythonAllocationFailed &) {return 0;} catch (...) {return fallback();}
}
PyObject *WOT_CDECL nav_submit(PyObject *,PyObject *args) {
    try {
        require_sequence(args,12,true);PyObject **row=items(args);offline_navigation::SearchInput input;
        const int64_t context=identity(row[0]);input.job_id=identity(row[1]);
        input.start=point_values(row[2]);input.goal=point_values(row[3]);
        const int maximum=index_value(row[4]);if(maximum<0)throw Unsupported();
        input.max_expansions=static_cast<unsigned>(maximum);input.prefer_clearance=number(row[5])!=0.;
        require_sequence(row[6],0);input.avoid_points.reserve(static_cast<size_t>(size(row[6])));
        for (Py_ssize_t index=0;index<size(row[6]);++index) input.avoid_points.push_back(point_values(items(row[6])[index]));
        input.edge_penalties=edge_values(row[7]);
        require_sequence(row[8],0);
        for (Py_ssize_t index=0;index<size(row[8]);++index) {
            PyObject *item=items(row[8])[index];require_sequence(item,4,true);PyObject **v=items(item);
            input.hard_edges.push_back({{{index_value(v[0]),index_value(v[1])}},{{index_value(v[2]),index_value(v[3])}}});
        }
        input.world_penalties=edge_values(row[9]);require_sequence(row[10],0);
        for (Py_ssize_t index=0;index<size(row[10]);++index) {
            PyObject *item=items(row[10])[index];require_sequence(item,2,true);
            input.native_review_cells.push_back({{index_value(items(item)[0]),index_value(items(item)[1])}});
        }
        input.hull_revision=identity(row[11]);
        return int_new(offline_navigation::submit(context,std::move(input))?1:0);
    } catch (const PythonAllocationFailed &) {return 0;} catch (...) {return fallback();}
}
PyObject *WOT_CDECL nav_poll(PyObject *,PyObject *args) {
    try {
        require_sequence(args,1,true);const auto data=offline_navigation::poll(identity(items(args)[0]));
        OwnedTuple completed(data.completed.size()),queries(data.queries.size()),stats(data.stats.size());
        for (size_t index=0;index<data.completed.size();++index) {
            const auto &value=data.completed[index];OwnedTuple row(6),path(value.path.size());
            for (size_t at=0;at<value.path.size();++at) path.set(at,point_result(value.path[at]));
            row.set(0,integer_result(value.job_id));row.set(1,string_new(value.status==0?"done":value.status==1?"cancelled":"failed"));
            row.set(2,path.release());row.set(3,integer_result(value.hull_revision));
            row.set(4,integer_result(value.expanded));row.set(5,float_new(value.worker_seconds));completed.set(index,row.release());
        }
        for (size_t index=0;index<data.queries.size();++index) {
            const auto &value=data.queries[index];OwnedTuple row(4);
            row.set(0,integer_result(value.query_id));row.set(1,integer_result(value.job_id));
            row.set(2,point_result(value.start));row.set(3,point_result(value.end));queries.set(index,row.release());
        }
        for (size_t index=0;index<data.stats.size();++index) stats.set(index,integer_result(static_cast<int64_t>(data.stats[index])));
        OwnedTuple result(3);result.set(0,completed.release());result.set(1,queries.release());result.set(2,stats.release());return result.release();
    } catch (const PythonAllocationFailed &) {return 0;} catch (...) {return fallback();}
}
PyObject *WOT_CDECL nav_answer(PyObject *,PyObject *args) {
    try {
        require_sequence(args,2,true);PyObject **row=items(args);require_sequence(row[1],0);
        std::vector<std::pair<int64_t,bool>> values;values.reserve(static_cast<size_t>(size(row[1])));
        for (Py_ssize_t index=0;index<size(row[1]);++index) {
            PyObject *item=items(row[1])[index];require_sequence(item,2,true);
            values.emplace_back(identity(items(item)[0]),number(items(item)[1])!=0.);
        }
        offline_navigation::answer(identity(row[0]),values);return int_new(1);
    } catch (...) {return fallback();}
}
PyObject *WOT_CDECL nav_cancel(PyObject *,PyObject *args) {
    try {
        require_sequence(args,2,true);PyObject **row=items(args);require_sequence(row[1],0);std::vector<int64_t> values;
        for (Py_ssize_t index=0;index<size(row[1]);++index) values.push_back(identity(items(row[1])[index]));
        offline_navigation::cancel(identity(row[0]),values);return int_new(1);
    } catch (...) {return fallback();}
}
PyObject *WOT_CDECL nav_close(PyObject *,PyObject *args) {
    try {require_sequence(args,1,true);offline_navigation::close(identity(items(args)[0]));return int_new(1);}
    catch (...) {return fallback();}
}
