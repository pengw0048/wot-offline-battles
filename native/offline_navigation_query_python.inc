// Borrowed capabilities expire with nav_query_run's synchronous GIL owner.
struct NavigationPythonContext {
    PyObject **capabilities;
    PyObject **diagnostic;
    unsigned rays, waters;
};
thread_local NavigationPythonContext *navigation_filter_context = 0;
struct NavigationFilterBinding {
    NavigationPythonContext *previous;
    explicit NavigationFilterBinding(NavigationPythonContext *context)
        : previous(navigation_filter_context) { navigation_filter_context = context; }
    ~NavigationFilterBinding() { navigation_filter_context = previous; }
};
PyObject *WOT_CDECL nav_query_filter(PyObject *, PyObject *args) {
    // Normal #1513 callbacks carry four exact ints. Exact long material uses
    // the original pure filter; no conversion API or guessed type RVA is added.
    if (!sequence(args) || size(args) != 4) return int_new(1);
    PyObject **values = items(args);
    NavigationPythonContext *context = navigation_filter_context;
    for (unsigned index = 0; index < 4; ++index) {
        if (values[index]->ob_type != int_type &&
            (!context || static_cast<const void *>(values[index]->ob_type) != context->capabilities[9])) return int_new(1);
    }
    if (values[0]->ob_type != int_type)
        return object_call(context->capabilities[8], args, 0);
    const long material = read<long>(reinterpret_cast<const char *>(values[0]) + sizeof(PyObject));
    return int_new(material >= 71 && material < 86 ? 0 : 1);
}

WorldObject navigation_call(PyObject *callable, OwnedTuple &arguments) {
    WorldObject args(arguments.release());
    return WorldObject(object_call(callable, args.get(), 0));
}
PyObject *navigation_vector(PyObject *constructor, const double *point) {
    OwnedTuple args(3);
    for (unsigned i = 0; i < 3; ++i) args.set(i, float_new(point[i]));
    WorldObject tuple(args.release());
    PyObject *result = object_call(constructor, tuple.get(), 0);
    if (!result) throw PythonAllocationFailed();
    return result;
}
void navigation_borrow(OwnedTuple &tuple, size_t index, PyObject *object) {
    ++object->ob_refcnt;
    tuple.set(index, object);
}
void navigation_diagnostic_token(PyObject *holder, PyObject *token) {
    PyObject **slot = items(holder);
    ++token->ob_refcnt;
    decref(slot[0]);
    slot[0] = token;
}
void navigation_geometry(NavigationPythonContext &context, PyObject *start, PyObject *end) {
    if (!context.diagnostic) return;
    OwnedTuple a(1), b(1);
    navigation_borrow(a, 0, start); navigation_borrow(b, 0, end);
    WorldObject ax = navigation_call(context.diagnostic[3], a);
    WorldObject bx = navigation_call(context.diagnostic[3], b);
    OwnedTuple key(3), args(2);
    navigation_borrow(key, 0, context.capabilities[5]);
    navigation_borrow(key, 1, ax.get()); navigation_borrow(key, 2, bx.get());
    args.set(0, string_new("native.navigation.ray")); args.set(1, key.release());
    WorldObject ignored = navigation_call(context.diagnostic[2], args);
}

bool navigation_ray(void *opaque, const double *a, const double *b, bool ground,
                    double &height, double &normal) {
    NavigationPythonContext &context = *static_cast<NavigationPythonContext *>(opaque);
    WorldObject start(navigation_vector(context.capabilities[2], a));
    WorldObject end(navigation_vector(context.capabilities[2], b));
    navigation_geometry(context, start.get(), end.get());
    if (context.diagnostic) {
        OwnedTuple empty(0);
        WorldObject token = navigation_call(context.diagnostic[0], empty);
        navigation_diagnostic_token(context.diagnostic[4], token.get());
    }
    OwnedTuple args(5);
    navigation_borrow(args, 0, context.capabilities[5]);
    navigation_borrow(args, 1, start.get()); navigation_borrow(args, 2, end.get());
    navigation_borrow(args, 3, context.capabilities[6]);
    navigation_borrow(args, 4, context.capabilities[4]);
    ++context.rays;
    WorldObject hit = navigation_call(context.capabilities[0], args);
    if (context.diagnostic) {
        OwnedTuple stop_args(2);
        stop_args.set(0, string_new("native.navigation.ray"));
        navigation_borrow(stop_args, 1, items(context.diagnostic[4])[0]);
        WorldObject ignored = navigation_call(context.diagnostic[1], stop_args);
        navigation_diagnostic_token(context.diagnostic[4], none_object);
    }
    if (hit.get() == none_object) return false;
    if (!ground) return true;
    require_sequence(hit.get(), 2);
    OwnedTuple point(1), direction(1);
    navigation_borrow(point, 0, items(hit.get())[0]);
    navigation_borrow(direction, 0, items(hit.get())[1]);
    WorldObject y = navigation_call(context.capabilities[3], point);
    WorldObject ny = navigation_call(context.capabilities[3], direction);
    height = number(y.get()); normal = number(ny.get());
    return true;
}

double navigation_water(void *opaque, double x, double y, double z) {
    NavigationPythonContext &context = *static_cast<NavigationPythonContext *>(opaque);
    if (context.capabilities[1] == none_object) return -1.0;
    const double a[] = {x, y + 20.0, z}, b[] = {x, y - 5.0, z};
    OwnedTuple args(3);
    args.set(0, world_numbers(a, 3));
    args.set(1, world_numbers(b, 3));
    navigation_borrow(args, 2, context.capabilities[7]);
    ++context.waters;
    WorldObject result = navigation_call(context.capabilities[1], args);
    if (result.get() == none_object) return -1.0;
    const double value = number(result.get());
    return value < 0.0 ? -1.0 : 20.0 - value;
}
PyObject *navigation_result(int status, unsigned rays, unsigned waters) {
    OwnedTuple output(3);
    output.set(0, int_new(status)); output.set(1, int_new(rays)); output.set(2, int_new(waters));
    return output.release();
}
PyObject *WOT_CDECL nav_query_run(PyObject *, PyObject *args) {
    bool entered = false;
    NavigationPythonContext context = {};
    try {
        require_sequence(args, 3, true);
        PyObject **row = items(args);
        require_sequence(row[0], 10, true); require_sequence(row[1], 10, true);
        double values[10]; world_array(row[0], values, 10);
        NavigationQuerySnapshot snapshot = {};
        std::memcpy(snapshot.start, values, 3 * sizeof(double));
        std::memcpy(snapshot.end, values + 3, 3 * sizeof(double));
        snapshot.cell_size = values[6]; snapshot.grade_up = values[7];
        snapshot.grade_down = values[8]; snapshot.water_limit = values[9];
        if (snapshot.cell_size < 1.0) throw Unsupported();
        context.capabilities = items(row[1]);
        if (row[2] != none_object) {
            require_sequence(row[2], 5, true);
            context.diagnostic = items(row[2]);
            if (context.diagnostic[4]->ob_type != list_type) throw Unsupported();
            require_sequence(context.diagnostic[4], 1, true);
        }
        NavigationFilterBinding filter(&context);
        const NavigationQueryEngine engine = {&context, navigation_ray, navigation_water};
        entered = true;
        const int status = navigation_query_run(snapshot, engine);
        return navigation_result(status, context.rays, context.waters);
    } catch (const PythonAllocationFailed &) { return 0; }
      catch (...) { return entered ? navigation_result(-1, context.rays, context.waters) : fallback(); }
}
PyObject *WOT_CDECL thread_cpu_seconds(PyObject *, PyObject *args) {
    if (!sequence(args) || size(args)) return fallback();
#ifdef WOT_HOST_PYTHON
    struct timespec stamp;
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &stamp)) return fallback();
    return float_new(static_cast<double>(stamp.tv_sec) + static_cast<double>(stamp.tv_nsec) * 1.0e-9);
#else
    FILETIME creation, exit, kernel, user;
    if (!GetThreadTimes(GetCurrentThread(), &creation, &exit, &kernel, &user)) return fallback();
    ULARGE_INTEGER k, u;
    k.LowPart = kernel.dwLowDateTime; k.HighPart = kernel.dwHighDateTime;
    u.LowPart = user.dwLowDateTime; u.HighPart = user.dwHighDateTime;
    return float_new(static_cast<double>(k.QuadPart + u.QuadPart) * 1.0e-7);
#endif
}
