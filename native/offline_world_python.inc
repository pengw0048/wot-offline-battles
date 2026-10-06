// The world reducer is synchronous. Python owns live engine hits and effects;
// no Python pointer or callback crosses into the background worker pool.
class WorldObject {
  PyObject *object_;

public:
  explicit WorldObject(PyObject *object) : object_(object) {
    if (!object_)
      throw PythonAllocationFailed();
  }
  ~WorldObject() { decref(object_); }
  WorldObject(const WorldObject &) = delete;
  WorldObject &operator=(const WorldObject &) = delete;
  PyObject *get() const { return object_; }
};

struct WorldContext {
  PyObject *dispatcher;
  std::exception_ptr exception;
};

PyObject *world_numbers(const double *values, size_t count) {
  OwnedTuple result(count);
  for (size_t index = 0; index < count; ++index) {
    if (!std::isfinite(values[index]))
      throw Unsupported();
    result.set(index, float_new(values[index]));
  }
  return result.release();
}

void world_array(PyObject *object, double *values, size_t count) {
  require_sequence(object, static_cast<Py_ssize_t>(count), true);
  for (size_t index = 0; index < count; ++index)
    values[index] = finite_number(items(object)[index]);
}

PyObject *world_request(unsigned opcode, const WorldRequest &request) {
  if (opcode == WORLD_DEPARTING)
    return world_numbers(request.auxiliary, 15);
  const size_t count = opcode == WORLD_FILTER  ? 2
                       : opcode == WORLD_TRACE ? 6
                                               : 3;
  OwnedTuple row(count);
  row.set(0, world_numbers(request.start, 3));
  row.set(1, world_numbers(request.end, 3));
  if (opcode == WORLD_FILTER)
    return row.release();
  if (opcode == WORLD_HORIZONTAL || opcode == WORLD_GROUND) {
    row.set(2, int_new(static_cast<long>(request.flags)));
  } else {
    row.set(2, integer_result(static_cast<int64_t>(request.handle)));
    if (opcode == WORLD_TRACE) {
      const double length = request.auxiliary[1];
      if (length < 0. || length > 30. || std::floor(length) != length)
        throw Unsupported();
      row.set(3, int_new(static_cast<long>(request.reason)));
      row.set(4, (request.flags & 2) ? float_new(request.auxiliary[0])
                                     : fallback());
      row.set(
          5, world_numbers(request.auxiliary + 2, static_cast<size_t>(length)));
    }
  }
  return row.release();
}

int world_dispatch(void *opaque, uint32_t opcode, const WorldRequest *requests,
                   uint32_t count, WorldResponse *responses) {
  WorldContext &context = *static_cast<WorldContext *>(opaque);
  try {
    NativeProfilePhase bridge(NativePhase::Pack);
    OwnedTuple rows(count), arguments(2);
    for (size_t index = 0; index < count; ++index)
      rows.set(index, world_request(opcode, requests[index]));
    arguments.set(0, int_new(static_cast<long>(opcode)));
    arguments.set(1, rows.release());
    // PyObject_Call borrows callable/args and returns a new reference. Both
    // input tuples remain owned across synchronous Python/BigWorld reentry.
    WorldObject args(arguments.release());
    WorldObject output(profiled_object_call(NativeCallback::World, "world",
        static_cast<int>(opcode), count, context.dispatcher, args.get()));
    bridge.input();
    if (opcode == WORLD_FILTER || opcode == WORLD_DEPARTING ||
        opcode == WORLD_TRACE)
      return 1;
    require_sequence(output.get(), count, true);
    for (size_t index = 0; index < count; ++index) {
      PyObject *value = items(output.get())[index];
      WorldResponse &response = responses[index];
      if (opcode == WORLD_RESOLVE) {
        response.status = index_value(value);
        if (response.status < 0 || response.status > 2)
          throw Unsupported();
        continue;
      }
      require_sequence(value, 5, true);
      PyObject **row = items(value);
      world_array(row[3], response.start, 3);
      world_array(row[4], response.end, 3);
      if (row[0] == none_object)
        continue;
      world_array(row[0], response.point, 3);
      world_array(row[1], response.normal, 3);
      const int64_t handle = identity(row[2]);
      if (handle <= 0)
        throw Unsupported();
      response.handle = static_cast<uint64_t>(handle);
      response.status = 1;
    }
    return 1;
  } catch (...) {
    // The reducer catches its own failures, so preserve the Python error
    // or parsing exception separately and rethrow after it has unwound.
    context.exception = std::current_exception();
    return 0;
  }
}

PyObject *WOT_CDECL world_run(PyObject *, PyObject *args) {
    NATIVE_PROFILE_ENTRY(world_run);
  bool entered = false;
  try {
    require_sequence(args, 2, true);
    double values[19];
    world_array(items(args)[0], values, 19);
    WorldSnapshot snapshot = {};
    std::memcpy(snapshot.position, values, 3 * sizeof(double));
    snapshot.yaw = values[3];
    snapshot.speed = values[4];
    snapshot.dt = values[5];
    snapshot.pitch = values[6];
    snapshot.roll = values[7];
    snapshot.motion_yaw = values[8];
    std::memcpy(snapshot.bounds, values + 9, 4 * sizeof(double));
    std::memcpy(snapshot.heights, values + 13, 2 * sizeof(double));
    snapshot.passive = values[15] != 0.;
    snapshot.airborne = values[16] != 0.;
    snapshot.exact_footprint = values[17] != 0.;
    snapshot.supplied_departing = values[18] != 0.;
    WorldContext context = {items(args)[1], std::exception_ptr()};
    entered = true;
    const int result = profile.run(wot_world_run, &snapshot, world_dispatch, &context);
    if (context.exception)
      std::rethrow_exception(context.exception);
    return int_new(result);
  } catch (const PythonAllocationFailed &) { profile.fail();
    // Includes a NULL PyObject_Call result: keep the original PyErr.
    return 0;
  } catch (...) { profile.fail();
    // Returning None authorizes Python replay, which is safe only before
    // the reducer can have invoked a callback with engine side effects.
    return entered ? int_new(-2) : fallback();
  }
}
