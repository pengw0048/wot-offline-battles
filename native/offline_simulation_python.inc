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

PyObject *WOT_CDECL sim_diag_begin(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 2, true);
        const auto handle = identity(items(args)[0]);
        const auto frame = identity(items(args)[1]);
        simulation_context(handle);
        return offline_simulation::diagnostics::ledger().begin(handle, frame)
            ? int_new(1) : fallback();
    } catch (...) { return fallback(); }
}

PyObject *simulation_entry_profile(const offline_simulation::diagnostics::EntryRow &v) {
    OwnedTuple row(11);
    row.set(0, string_new(v.name)); row.set(1, int_new(v.opcode));
    row.set(2, integer_result(v.calls)); row.set(3, integer_result(v.errors));
    row.set(4, float_new(v.inclusive)); row.set(5, float_new(v.parse));
    row.set(6, float_new(v.body)); row.set(7, float_new(v.pack));
    row.set(8, float_new(v.callbacks)); row.set(9, float_new(v.child_native));
    row.set(10, integer_result(v.reentrant_calls));
    return row.release();
}
PyObject *simulation_callback_profile(const offline_simulation::diagnostics::CallbackRow &v) {
    OwnedTuple row(8);
    row.set(0, string_new(v.name)); row.set(1, int_new(v.opcode));
    row.set(2, integer_result(v.calls)); row.set(3, integer_result(v.rows));
    row.set(4, integer_result(v.errors)); row.set(5, float_new(v.inclusive));
    row.set(6, float_new(v.self)); row.set(7, float_new(v.nested_native));
    return row.release();
}
PyObject *WOT_CDECL sim_diag_end(PyObject *, PyObject *args) {
    try {
        require_sequence(args, 1, true);
        auto &capture = offline_simulation::diagnostics::ledger();
        // End accepts the exact captured handle even after sim_close. No old
        // context is dereferenced while producing this observational result.
        if (!capture.end(identity(items(args)[0]))) return fallback();
        std::size_t counter_count = 0;
        for (const auto &row : capture.counters) counter_count += bool(row.name);
        OwnedTuple out(7), counters(counter_count), entries(capture.entry_count + bool(capture.entry_overflow.calls)),
            callbacks(capture.callback_count + bool(capture.callback_overflow.calls));
        for (std::size_t i = 0; i < capture.entry_count; ++i)
            entries.set(i, simulation_entry_profile(capture.entries[capture.entry_indices[i]]));
        if (capture.entry_overflow.calls)
            entries.set(capture.entry_count, simulation_entry_profile(capture.entry_overflow));
        for (std::size_t i = 0; i < capture.callback_count; ++i)
            callbacks.set(i, simulation_callback_profile(capture.callbacks[capture.callback_indices[i]]));
        if (capture.callback_overflow.calls)
            callbacks.set(capture.callback_count, simulation_callback_profile(capture.callback_overflow));
        out.set(0, integer_result(capture.frame)); out.set(1, float_new(capture.elapsed));
        out.set(2, int_new(capture.deepest)); out.set(3, integer_result(capture.reentries));
        out.set(4, entries.release()); out.set(5, callbacks.release());
        std::size_t index = 0;
        for (const auto &value : capture.counters) if (value.name) {
            OwnedTuple row(4);
            row.set(0, string_new(value.name));
            row.set(1, integer_result(value.observations));
            row.set(2, integer_result(value.sum));
            row.set(3, integer_result(value.maximum));
            counters.set(index++, row.release());
        }
        out.set(6, counters.release());
        return out.release();
    } catch (const PythonAllocationFailed &) { return 0; }
      catch (...) { return fallback(); }
}

PyObject *WOT_CDECL sim_open(PyObject *, PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_open);
    try {
        require_sequence(args, 2, true);
        return integer_result(profile.run(offline_simulation::open,
            identity(items(args)[0]), identity(items(args)[1])));
    } catch (const PythonAllocationFailed &) { profile.fail(); return 0; }
      catch (...) { profile.fail(); return fallback(); }
}

PyObject *WOT_CDECL sim_close(PyObject *, PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_close);
    try {
        require_sequence(args, 1, true);
        profile.run(offline_simulation::close, identity(items(args)[0]));
        return int_new(1);
    } catch (...) { profile.fail(); return fallback(); }
}

PyObject *WOT_CDECL sim_lifetime(PyObject *, PyObject *args) {
    NATIVE_PROFILE_ENTRY(sim_lifetime);
    try {
        require_sequence(args, 1, true);
        const auto context = simulation_context(identity(items(args)[0]));
        profile.output();
        OwnedTuple row(2);
        row.set(0, integer_result(context->round));
        row.set(1, integer_result(context->generation));
        return row.release();
    } catch (const PythonAllocationFailed &) { profile.fail(); return 0; }
      catch (...) { profile.fail(); return fallback(); }
}

#define WOT_SIMULATION_METHODS \
    {"sim_diag_begin", sim_diag_begin, 1, "Start bounded same-thread native boundary diagnostics."}, \
    {"sim_diag_end", sim_diag_end, 1, "Stop diagnostics before packing the captured rows."}, \
    {"sim_open", sim_open, 1, "Create one persistent simulation lifetime."}, \
    {"sim_close", sim_close, 1, "Retire one persistent simulation lifetime."}, \
    {"sim_lifetime", sim_lifetime, 1, "Read the round and authority generation."},
