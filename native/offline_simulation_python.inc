// Included after the common tuple/number and world-dispatch bridge helpers.
using SimulationContext = offline_simulation::Context;

// Numeric payloads use exactly representable doubles across Win32 CPython's
// 32-bit PyInt boundary. Actor identities and handles keep identity() guards.
std::int64_t simulation_wide_number(PyObject *value) {
    const double number = finite_number(value);
    if (std::floor(number) != number || std::abs(number) > 9007199254740991.)
        throw Unsupported();
    return static_cast<std::int64_t>(number);
}

PyObject *simulation_wide_result(std::int64_t value) {
    if (value < -9007199254740991LL || value > 9007199254740991LL)
        throw Unsupported();
    return float_new(static_cast<double>(value));
}

std::shared_ptr<SimulationContext> simulation_context(std::int64_t handle) {
    auto context = offline_simulation::find(handle);
    if (!context) throw Unsupported();
    return context;
}

PyObject *WOT_CDECL sim_open(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 2, true);
        return integer_result(offline_simulation::open(
            identity(items(args)[0]), identity(items(args)[1])));
    } catch (const PythonAllocationFailed &) { return 0; }
      catch (...) { return fallback(); }
}

PyObject *WOT_CDECL sim_close(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 1, true);
        offline_simulation::close(identity(items(args)[0]));
        return int_new(1);
    } catch (...) { return fallback(); }
}

PyObject *WOT_CDECL sim_lifetime(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 1, true);
        const auto context = simulation_context(identity(items(args)[0]));
        OwnedTuple row(2);
        row.set(0, integer_result(context->round));
        row.set(1, integer_result(context->generation));
        return row.release();
    } catch (const PythonAllocationFailed &) { return 0; }
      catch (...) { return fallback(); }
}

#define WOT_SIMULATION_METHODS \
    {"sim_open", sim_open, 1, "Create one persistent simulation lifetime."}, \
    {"sim_close", sim_close, 1, "Retire one persistent simulation lifetime."}, \
    {"sim_lifetime", sim_lifetime, 1, "Read the round and authority generation."},
