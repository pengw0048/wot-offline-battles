// Typed ingress/egress for the persistent control store. Engine callbacks are
// borrowed only for this synchronous call; the store contains no PyObject.
namespace sim_control_bridge {
using namespace offline_simulation;
using namespace offline_simulation::control;
ActorKey key(PyObject *value) {
  require_sequence(value, 2, true);
  const int kind = index_value(items(value)[0]);
  const auto id = identity(items(value)[1]);
  if (kind < 0 || kind > 1 || id <= 0)
    throw Unsupported();
  return {static_cast<ActorKind>(kind), id};
}
PyObject *key(const ActorKey &value) {
  OwnedTuple row(2);
  row.set(0, int_new(static_cast<long>(value.kind)));
  row.set(1, integer_result(value.id));
  return row.release();
}
PoseSample pose(PyObject *value) {
  require_sequence(value, 3, true);
  PoseSample result;
  world_array(items(value)[0], result.position.data(), 3);
  world_array(items(value)[1], result.values.data(), 13);
  result.mask = static_cast<unsigned>(index_value(items(value)[2]));
  if (result.mask > 2047)
    throw Unsupported();
  return result;
}
PyObject *pose(const PoseSample &value) {
  OwnedTuple row(3);
  row.set(0, world_numbers(value.position.data(), 3));
  row.set(1, world_numbers(value.values.data(), 13));
  row.set(2, int_new(value.mask));
  return row.release();
}
std::vector<ActorKey> keys(PyObject *value) {
  require_sequence(value, 0);
  std::vector<ActorKey> result;
  for (Py_ssize_t i = 0; i < size(value); ++i)
    result.push_back(key(items(value)[i]));
  return result;
}
SightProbe sight(const std::shared_ptr<SimulationContext> &context,
                 PyObject *dispatcher) {
  return [context, dispatcher](const SightRequest &request) {
    if (context->closed)
      throw Unsupported();
    OwnedTuple args(6);
    args.set(0, key(request.observer));
    args.set(1, key(request.target));
    args.set(2, float_new(request.now));
    args.set(3, integer_result(request.fire_sequence));
    const auto &d = request.detection;
    double values[] = {d.distance,   d.view,           d.camo_moving,
                       d.camo_still, double(d.moving), double(d.fired),
                       d.additive,   d.multiplier,     d.shot_factor};
    args.set(4, world_numbers(values, 9));
    args.set(5, int_new(0));
    WorldObject arguments(args.release());
    WorldObject output(object_call(dispatcher, arguments.get(), 0));
    if (context->closed)
      throw Unsupported();
    require_sequence(output.get(), 5, true);
    auto row = items(output.get());
    const int status = index_value(row[0]);
    if (status < 0 || status > 3)
      throw Unsupported();
    SightReply reply;
    reply.status = static_cast<QueryStatus>(status);
    reply.has_detection = index_value(row[1]) != 0;
    reply.detected = index_value(row[2]) != 0;
    reply.foliage = finite_number(row[3]);
    reply.sampled_at = finite_number(row[4]);
    return reply;
  };
}
DriverBody body(PyObject *value) {
  require_sequence(value, 4, true);
  auto row = items(value);
  DriverBody result;
  result.id = identity(row[0]);
  world_array(row[1], result.position.data(), 3);
  world_array(row[2], result.velocity.data(), 3);
  double n[8];
  world_array(row[3], n, 8);
  result.team = static_cast<int>(n[0]);
  result.alive = n[1] != 0.;
  result.yaw = n[2];
  result.half_length = n[3];
  result.half_width = n[4];
  result.has_shape = n[5] != 0.;
  result.lower_y = n[6];
  result.upper_y = n[7];
  return result;
}
std::vector<DriverBody> bodies(PyObject *value) {
  require_sequence(value, 0);
  std::vector<DriverBody> result;
  for (Py_ssize_t i = 0; i < size(value); ++i)
    result.push_back(body(items(value)[i]));
  return result;
}
DriveCommand command(PyObject *value) {
  require_sequence(value, 7, true);
  auto row = items(value);
  DriveCommand result;
  result.throttle = finite_number(row[0]);
  result.turn = finite_number(row[1]);
  result.target_yaw = finite_number(row[2]);
  int recovery = index_value(row[3]);
  int traffic = index_value(row[4]);
  if (recovery < 0 || recovery > 5 || traffic < 0 || traffic > 3)
    throw Unsupported();
  result.recovery = static_cast<RecoveryMode>(recovery);
  result.traffic = static_cast<TrafficMode>(traffic);
  result.has_reverse_blocker = index_value(row[5]) != 0;
  result.reverse_blocker = identity(row[6]);
  return result;
}
PyObject *command(const DriveCommand &value) {
  OwnedTuple row(7);
  row.set(0, float_new(value.throttle));
  row.set(1, float_new(value.turn));
  row.set(2, float_new(value.target_yaw));
  row.set(3, int_new(static_cast<int>(value.recovery)));
  row.set(4, int_new(static_cast<int>(value.traffic)));
  row.set(5, int_new(value.has_reverse_blocker));
  row.set(6, integer_result(value.reverse_blocker));
  return row.release();
}
bool probe(const std::shared_ptr<SimulationContext> &context,
           PyObject *dispatcher, int opcode, double yaw, double distance) {
  if (context->closed)
    throw Unsupported();
  OwnedTuple args(3);
  args.set(0, int_new(opcode));
  args.set(1, float_new(yaw));
  args.set(2, float_new(distance));
  WorldObject arguments(args.release());
  WorldObject result(object_call(dispatcher, arguments.get(), 0));
  if (context->closed)
    throw Unsupported();
  return index_value(result.get()) != 0;
}
PyObject *state(const DriverState &s) {
  double n[] = {double(s.recovery_count),
                s.stuck_time,
                s.recovery_time,
                s.recovery_side,
                s.steering_yaw,
                s.steering_age,
                s.plan_age,
                s.clock,
                s.escape_side,
                s.escape_until,
                s.desired_yaw,
                s.progress_yaw,
                s.best_heading_error,
                s.traffic_wait_time,
                s.last_step,
                s.last_clear_yaw};
  unsigned flags =
      unsigned(s.has_steering) | (unsigned(s.avoiding) << 1) |
      (unsigned(s.has_desired) << 2) | (unsigned(s.has_progress) << 3) |
      (unsigned(s.has_last_clear) << 4) | (unsigned(s.traffic_waiting) << 5) |
      (unsigned(s.braking) << 6) | (unsigned(s.has_heading_error) << 7) |
      (unsigned(s.has_traffic_wait) << 8) | (unsigned(s.coasting) << 9);
  OwnedTuple row(7), failed(s.failed_yaws.size());
  size_t i = 0;
  for (const auto &entry : s.failed_yaws) {
    OwnedTuple f(2);
    f.set(0, int_new(entry.first));
    f.set(1, float_new(entry.second));
    failed.set(i++, f.release());
  }
  row.set(0, int_new(s.slot));
  row.set(1, world_numbers(s.last_position.data(), 3));
  row.set(2, world_numbers(n, 16));
  row.set(3, int_new(flags));
  row.set(4, failed.release());
  row.set(5, world_numbers(s.braking_target.data(), 2));
  row.set(6, world_numbers(s.coast_target.data(), 2));
  return row.release();
}
DriverState state(PyObject *value) {
  require_sequence(value, 7, true);
  auto row = items(value);
  DriverState s;
  s.slot = index_value(row[0]);
  world_array(row[1], s.last_position.data(), 3);
  double n[16];
  world_array(row[2], n, 16);
  unsigned f = index_value(row[3]);
  s.recovery_count = static_cast<int>(n[0]);
  s.stuck_time = n[1];
  s.recovery_time = n[2];
  s.recovery_side = n[3];
  s.steering_yaw = n[4];
  s.steering_age = n[5];
  s.plan_age = n[6];
  s.clock = n[7];
  s.escape_side = n[8];
  s.escape_until = n[9];
  s.desired_yaw = n[10];
  s.progress_yaw = n[11];
  s.best_heading_error = n[12];
  s.traffic_wait_time = n[13];
  s.last_step = n[14];
  s.last_clear_yaw = n[15];
  s.has_steering = f & 1;
  s.avoiding = f & 2;
  s.has_desired = f & 4;
  s.has_progress = f & 8;
  s.has_last_clear = f & 16;
  s.traffic_waiting = f & 32;
  s.braking = f & 64;
  s.has_heading_error = f & 128;
  s.has_traffic_wait = f & 256;
  s.coasting = f & 512;
  require_sequence(row[4], 0);
  for (Py_ssize_t i = 0; i < size(row[4]); ++i) {
    auto pair = items(row[4])[i];
    require_sequence(pair, 2, true);
    s.failed_yaws[index_value(items(pair)[0])] = finite_number(items(pair)[1]);
  }
  world_array(row[5], s.braking_target.data(), 2);
  world_array(row[6], s.coast_target.data(), 2);
  return s;
}
} // namespace sim_control_bridge
#define CONTROL_CATCH                                                          \
  catch (const PythonAllocationFailed &) {                                     \
    return 0;                                                                  \
  }                                                                            \
  catch (...) {                                                                \
    return fallback();                                                         \
  }
PyObject *WOT_CDECL sim_control_configure(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 2, true);
    auto context = simulation_context(identity(items(args)[0]));
    auto rows = items(args)[1];
    require_sequence(rows, 0);
    std::vector<offline_simulation::control::ActorConfig> result;
    for (Py_ssize_t i = 0; i < size(rows); ++i) {
      auto row = items(rows)[i];
      require_sequence(row, 3, true);
      offline_simulation::control::ActorConfig c;
      c.key = sim_control_bridge::key(items(row)[0]);
      c.python_hash = identity(items(row)[1]);
      double n[18];
      world_array(items(row)[2], n, 18);
      c.team = int(n[0]);
      c.slot = int(n[1]);
      c.half_length = n[2];
      c.half_width = n[3];
      c.view_moving = n[4];
      c.view_still = n[5];
      c.view_delay = n[6];
      c.camo_still = n[7];
      c.camo_moving = n[8];
      c.camo_add = n[9];
      c.camo_net_add = n[10];
      c.camo_multiplier = n[11];
      c.camo_net_multiplier = n[12];
      c.camo_shot = n[13];
      c.camo_net_delay = n[14];
      c.radio = n[15];
      c.relay = n[16];
      if (n[17] != 0.)
        throw Unsupported();
      result.push_back(c);
    }
    context->control.configure(result);
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_update(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 2, true);
    auto context = simulation_context(identity(items(args)[0]));
    auto rows = items(args)[1];
    require_sequence(rows, 0);
    std::vector<offline_simulation::control::ActorSample> result;
    for (Py_ssize_t i = 0; i < size(rows); ++i) {
      auto row = items(rows)[i];
      require_sequence(row, 5, true);
      auto v = items(row);
      offline_simulation::control::ActorSample s;
      s.key = sim_control_bridge::key(v[0]);
      s.pose = sim_control_bridge::pose(v[1]);
      s.fire_sequence = identity(v[2]);
      int f = index_value(v[3]);
      s.alive = f & 1;
      s.last_effort = f & 2;
      s.designated = f & 4;
      s.decision_due = f & 8;
      s.has_selected = v[4] != none_object;
      if (s.has_selected)
        s.selected = sim_control_bridge::key(v[4]);
      result.push_back(s);
    }
    context->control.update(result);
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_begin(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 7, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    double time = finite_number(v[2]);
    if (std::abs(time) > 9e9)
      throw Unsupported();
    offline_simulation::TickToken token{
        context->round, context->generation, identity(v[1]),
        static_cast<std::int64_t>(time * 1000000.)};
    context->control.begin(token, sim_control_bridge::keys(v[3]),
                           index_value(v[4]), index_value(v[5]) != 0,
                           index_value(v[6]) != 0, time);
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_contacts(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 3, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    auto rows =
        context->control.contacts(sim_control_bridge::key(v[1]),
                                  sim_control_bridge::sight(context, v[2]));
    OwnedTuple result(rows.size());
    for (size_t i = 0; i < rows.size(); ++i) {
      const auto &c = rows[i];
      OwnedTuple row(5);
      row.set(0, sim_control_bridge::key(c.target));
      row.set(1, int_new(int(c.visible) | (int(c.direct) << 1) |
                         (int(c.fresh) << 2)));
      row.set(2, float_new(c.remaining));
      row.set(3, float_new(c.sampled_at));
      row.set(4, c.has_pose ? sim_control_bridge::pose(c.pose) : fallback());
      result.set(i, row.release());
    }
    return result.release();
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_humans(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 2, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    context->control.observe_humans(sim_control_bridge::sight(context, v[1]));
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_observations(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto context = simulation_context(identity(items(args)[0]));
    auto rows = context->control.take_observations();
    OwnedTuple result(rows.size());
    for (size_t i = 0; i < rows.size(); ++i) {
      const auto &o = rows[i];
      OwnedTuple row(5);
      row.set(0, sim_control_bridge::key(o.observer));
      row.set(1, sim_control_bridge::key(o.target));
      row.set(2, float_new(o.sampled_at));
      row.set(3, float_new(o.duration));
      row.set(4, sim_control_bridge::pose(o.pose));
      result.set(i, row.release());
    }
    return result.release();
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_team_contacts(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto context = simulation_context(identity(items(args)[0]));
    auto rows = context->control.team_contacts();
    OwnedTuple result(rows.size());
    for (size_t i = 0; i < rows.size(); ++i) {
      const auto &o = rows[i];
      OwnedTuple row(4);
      row.set(0, int_new(o.team));
      row.set(1, sim_control_bridge::key(o.target));
      row.set(2, float_new(o.remaining));
      row.set(3, sim_control_bridge::pose(o.pose));
      result.set(i, row.release());
    }
    return result.release();
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_finish(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    simulation_context(identity(items(args)[0]))->control.finish();
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_forget(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 2, true);
    simulation_context(identity(items(args)[0]))
        ->control.forget(sim_control_bridge::key(items(args)[1]));
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_drive(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 4, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    require_sequence(v[1], 4, true);
    auto row = items(v[1]);
    offline_simulation::control::DriverInput in;
    in.id = identity(row[0]);
    world_array(row[1], in.position.data(), 3);
    world_array(row[2], in.target.data(), 3);
    double n[14];
    world_array(row[3], n, 14);
    in.slot = int(n[0]);
    in.yaw = n[1];
    in.speed = n[2];
    in.dt = n[3];
    in.half_length = n[4];
    in.half_width = n[5];
    in.movement_intent = n[6] != 0.;
    in.stop_at_target = n[7] != 0.;
    in.has_stopping_distance = n[8] != 0.;
    in.stopping_distance = n[9];
    in.decision_horizon = n[10];
    in.braking_key = {n[11], n[12]};
    in.turn_speed_limit = n[13];
    auto peers = sim_control_bridge::bodies(v[2]);
    auto clear = [&](double yaw, double distance) {
      return sim_control_bridge::probe(context, v[3], 0, yaw, distance);
    };
    auto pose = [&](double yaw) {
      return sim_control_bridge::probe(context, v[3], 1, yaw, -1.);
    };
    auto command = context->control.drive(in, peers, clear, pose);
    OwnedTuple output(2);
    output.set(0, sim_control_bridge::command(command));
    output.set(1, sim_control_bridge::state(
                      context->control.driver(in.id, in.slot, in.position)));
    return output.release();
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_driver_event(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 5, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    auto id = identity(v[1]);
    int event = index_value(v[2]);
    double value = finite_number(v[3]), extra = finite_number(v[4]);
    if (event == 0)
      context->control.remember_failure(id, value, extra);
    else if (event == 1)
      context->control.wait_for_traffic(id, value);
    else if (event == 2) {
      context->control.forget_driver(id);
      return int_new(1);
    } else if (event == 3) {
      context->control.forget_traffic(id);
      return int_new(1);
    } else
      throw Unsupported();
    auto it = context->control.drivers().find(id);
    return it == context->control.drivers().end()
               ? fallback()
               : sim_control_bridge::state(it->second);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_driver_restore(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 3, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    auto state = sim_control_bridge::state(v[2]);
    context->control.driver(identity(v[1]), state.slot, state.last_position) =
        state;
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_traffic(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 7, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    auto body = sim_control_bridge::body(v[1]);
    auto command = sim_control_bridge::command(v[2]);
    auto peers = sim_control_bridge::bodies(v[3]);
    double now = finite_number(v[4]);
    bool route = index_value(v[5]) != 0;
    auto clear = [&](double yaw, double distance) {
      return sim_control_bridge::probe(context, v[6], 0, yaw, distance);
    };
    return sim_control_bridge::command(context->control.adjust_traffic(
        body.id, body, command, peers, now, route, clear));
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_traffic_state(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 2, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    if (v[1] != none_object) {
      require_sequence(v[1], 2, true);
      auto rows = items(v[1]);
      require_sequence(rows[0], 0);
      require_sequence(rows[1], 0);
      std::map<std::pair<std::int64_t, std::int64_t>,
               offline_simulation::control::TrafficLease>
          pairs;
      std::map<std::int64_t, double> held;
      for (Py_ssize_t i = 0; i < size(rows[0]); ++i) {
        auto r = items(rows[0])[i];
        require_sequence(r, 3, true);
        auto fields = items(r);
        double n[10];
        world_array(fields[2], n, 10);
        offline_simulation::control::TrafficLease l;
        l.head_on = n[0] != 0.;
        l.axis = {n[1], n[2]};
        l.targets = {n[3], n[4]};
        l.winner = static_cast<std::int64_t>(n[5]);
        l.clear_after = n[6];
        l.until = n[7];
        l.blocked_until = n[8];
        l.has_blocked_until = n[9] != 0.;
        pairs[{identity(fields[0]), identity(fields[1])}] = l;
      }
      for (Py_ssize_t i = 0; i < size(rows[1]); ++i) {
        auto r = items(rows[1])[i];
        require_sequence(r, 2, true);
        held[identity(items(r)[0])] = finite_number(items(r)[1]);
      }
      context->control.restore_traffic(pairs, held);
      return int_new(1);
    }
    OwnedTuple result(2), pairs(context->control.traffic().size()),
        held(context->control.held().size());
    size_t i = 0;
    for (const auto &entry : context->control.traffic()) {
      const auto &l = entry.second;
      double n[] = {double(l.head_on), l.axis[0],
                    l.axis[1],         l.targets[0],
                    l.targets[1],      double(l.winner),
                    l.clear_after,     l.until,
                    l.blocked_until,   double(l.has_blocked_until)};
      OwnedTuple row(3);
      row.set(0, integer_result(entry.first.first));
      row.set(1, integer_result(entry.first.second));
      row.set(2, world_numbers(n, 10));
      pairs.set(i++, row.release());
    }
    i = 0;
    for (const auto &entry : context->control.held()) {
      OwnedTuple row(2);
      row.set(0, integer_result(entry.first));
      row.set(1, float_new(entry.second));
      held.set(i++, row.release());
    }
    result.set(0, pairs.release());
    result.set(1, held.release());
    return result.release();
  }
  CONTROL_CATCH
}
namespace sim_control_bridge {
PyObject *radio_rows(const std::vector<RadioActor> &rows) {
  OwnedTuple result(rows.size());
  for (size_t i = 0; i < rows.size(); ++i) {
    const auto &r = rows[i];
    OwnedTuple row(6);
    row.set(0, key(r.key));
    row.set(1, int_new(r.team));
    row.set(2, world_numbers(r.position.data(), 3));
    row.set(3, float_new(r.range));
    row.set(4, float_new(r.relay));
    row.set(5, float_new(r.view));
    result.set(i, row.release());
  }
  return result.release();
}
std::vector<RadioActor> radio_rows(PyObject *rows) {
  require_sequence(rows, 0);
  std::vector<RadioActor> result;
  for (Py_ssize_t i = 0; i < size(rows); ++i) {
    auto r = items(rows)[i];
    require_sequence(r, 6, true);
    auto row = items(r);
    RadioActor a;
    a.key = key(row[0]);
    a.team = index_value(row[1]);
    world_array(row[2], a.position.data(), 3);
    a.range = finite_number(row[3]);
    a.relay = finite_number(row[4]);
    a.view = finite_number(row[5]);
    result.push_back(a);
  }
  return result;
}
PyObject *snapshot(const PerceptionSnapshot &s) {
  OwnedTuple result(8), actors(s.actors.size()),
      observations(s.observations.size()), slots(s.observer_slots.size()),
      visibility(s.visibility.size()), teams(s.team_spots.size()),
      waiting(s.waiting.size());
  size_t i = 0;
  for (const auto &entry : s.actors) {
    const auto &a = entry.second;
    OwnedTuple row(5), direct(a.direct_targets.size());
    size_t j = 0;
    for (const auto &t : a.direct_targets)
      direct.set(j++, key(t));
    unsigned flags = unsigned(a.has_sample) | (unsigned(a.still) << 1) |
                     (unsigned(a.fire_initialized) << 2) |
                     (unsigned(a.lifecycle_initialized) << 3) |
                     (unsigned(a.last_alive) << 4) |
                     (unsigned(a.last_alive_effort) << 5) |
                     (unsigned(a.source_still) << 6);
    double times[] = {a.still_since, a.fire_until, a.vengeance_until,
                      a.source_still_since};
    row.set(0, key(entry.first));
    row.set(1, int_new(flags));
    row.set(2, world_numbers(times, 4));
    row.set(3, integer_result(a.fire_sequence));
    row.set(4, direct.release());
    actors.set(i++, row.release());
  }
  i = 0;
  for (const auto &entry : s.observations) {
    OwnedTuple row(2), targets(entry.second.size());
    size_t j = 0;
    for (const auto &target : entry.second) {
      const auto &o = target.second;
      OwnedTuple value(5);
      value.set(0, key(target.first));
      value.set(1, float_new(o.deadline));
      value.set(2, float_new(o.sampled_at));
      value.set(3, pose(o.pose));
      value.set(4, int_new(o.direct));
      targets.set(j++, value.release());
    }
    row.set(0, key(entry.first));
    row.set(1, targets.release());
    observations.set(i++, row.release());
  }
  i = 0;
  for (const auto &s : s.observer_slots) {
    OwnedTuple row(4);
    row.set(0, int_new(s.state));
    row.set(1, s.state == 1 ? key(s.key) : fallback());
    row.set(2, float_new(static_cast<double>(s.hash >> 32)));
    row.set(3, float_new(static_cast<double>(s.hash & 0xffffffffULL)));
    slots.set(i++, row.release());
  }
  i = 0;
  for (const auto &entry : s.visibility) {
    OwnedTuple row(5);
    row.set(0, key(entry.first.observer));
    row.set(1, key(entry.first.target));
    row.set(2, float_new(entry.second.sampled_at));
    row.set(3, int_new(entry.second.detected));
    row.set(4, integer_result(entry.second.fire_sequence));
    visibility.set(i++, row.release());
  }
  i = 0;
  for (const auto &entry : s.team_spots) {
    OwnedTuple row(5);
    row.set(0, int_new(entry.first.first));
    row.set(1, key(entry.first.second));
    row.set(2, float_new(entry.second.deadline));
    row.set(3, float_new(entry.second.sampled_at));
    row.set(4, pose(entry.second.pose));
    teams.set(i++, row.release());
  }
  i = 0;
  for (const auto &p : s.waiting) {
    OwnedTuple row(2);
    row.set(0, key(p.observer));
    row.set(1, key(p.target));
    waiting.set(i++, row.release());
  }
  result.set(0, actors.release());
  result.set(1, observations.release());
  result.set(2, slots.release());
  result.set(3, visibility.release());
  result.set(4, teams.release());
  result.set(5, waiting.release());
  result.set(6, radio_rows(s.radio));
  result.set(7, float_new(s.now));
  return result.release();
}
PerceptionSnapshot snapshot(PyObject *value) {
  require_sequence(value, 8, true);
  auto rows = items(value);
  for (int i = 0; i < 7; ++i)
    require_sequence(rows[i], 0);
  PerceptionSnapshot s;
  s.now = finite_number(rows[7]);
  for (Py_ssize_t i = 0; i < size(rows[0]); ++i) {
    auto row = items(rows[0])[i];
    require_sequence(row, 5, true);
    auto v = items(row);
    auto k = key(v[0]);
    ActorMemory a;
    unsigned f = index_value(v[1]);
    a.has_sample = f & 1;
    a.still = f & 2;
    a.fire_initialized = f & 4;
    a.lifecycle_initialized = f & 8;
    a.last_alive = f & 16;
    a.last_alive_effort = f & 32;
    a.source_still = f & 64;
    double n[4];
    world_array(v[2], n, 4);
    a.still_since = n[0];
    a.fire_until = n[1];
    a.vengeance_until = n[2];
    a.source_still_since = n[3];
    a.fire_sequence = identity(v[3]);
    for (const auto &t : keys(v[4]))
      a.direct_targets.insert(t);
    s.actors[k] = a;
  }
  for (Py_ssize_t i = 0; i < size(rows[1]); ++i) {
    auto row = items(rows[1])[i];
    require_sequence(row, 2, true);
    auto v = items(row);
    auto owner = key(v[0]);
    require_sequence(v[1], 0);
    auto &targets = s.observations[owner];
    for (Py_ssize_t j = 0; j < size(v[1]); ++j) {
      auto record = items(v[1])[j];
      require_sequence(record, 5, true);
      auto r = items(record);
      RadioObservation o;
      o.deadline = finite_number(r[1]);
      o.sampled_at = finite_number(r[2]);
      o.pose = pose(r[3]);
      o.direct = index_value(r[4]) != 0;
      targets[key(r[0])] = o;
    }
  }
  for (Py_ssize_t i = 0; i < size(rows[2]); ++i) {
    auto row = items(rows[2])[i];
    require_sequence(row, 4, true);
    auto v = items(row);
    ObserverOrder::Slot slot;
    slot.state = index_value(v[0]);
    if (slot.state == 1)
      slot.key = key(v[1]);
    double high = finite_number(v[2]), low = finite_number(v[3]);
    if (high < 0. || high > 4294967295. || low < 0. || low > 4294967295. ||
        std::floor(high) != high || std::floor(low) != low)
      throw Unsupported();
    slot.hash = (static_cast<std::uint64_t>(high) << 32) |
                static_cast<std::uint64_t>(low);
    s.observer_slots.push_back(slot);
  }
  for (Py_ssize_t i = 0; i < size(rows[3]); ++i) {
    auto row = items(rows[3])[i];
    require_sequence(row, 5, true);
    auto v = items(row);
    s.visibility[{key(v[0]), key(v[1])}] = {
        finite_number(v[2]), index_value(v[3]) != 0, identity(v[4])};
  }
  for (Py_ssize_t i = 0; i < size(rows[4]); ++i) {
    auto row = items(rows[4])[i];
    require_sequence(row, 5, true);
    auto v = items(row);
    RadioObservation o;
    o.deadline = finite_number(v[2]);
    o.sampled_at = finite_number(v[3]);
    o.pose = pose(v[4]);
    s.team_spots[{index_value(v[0]), key(v[1])}] = o;
  }
  for (Py_ssize_t i = 0; i < size(rows[5]); ++i) {
    auto row = items(rows[5])[i];
    require_sequence(row, 2, true);
    s.waiting.push_back({key(items(row)[0]), key(items(row)[1])});
  }
  s.radio = radio_rows(rows[6]);
  return s;
}
} // namespace sim_control_bridge
PyObject *WOT_CDECL sim_control_snapshot(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 2, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    if (v[1] == none_object)
      return sim_control_bridge::snapshot(context->control.snapshot());
    context->control.restore(sim_control_bridge::snapshot(v[1]));
    return int_new(1);
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_radio(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 5, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    auto a = sim_control_bridge::key(v[2]), b = sim_control_bridge::key(v[3]);
    int op = index_value(v[1]);
    if (op == 1)
      return int_new(context->control.radio_connected(a, b));
    if (op != 0)
      throw Unsupported();
    auto c = context->control.recipient_contact(a, b, finite_number(v[4]));
    OwnedTuple row(3);
    row.set(0, float_new(c.remaining));
    row.set(1, int_new(c.fresh));
    row.set(2, c.has_pose ? sim_control_bridge::pose(c.pose) : fallback());
    return row.release();
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_radio_summaries(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 3, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    auto pairs = v[1];
    require_sequence(pairs, 0);
    const double now = finite_number(v[2]);
    OwnedTuple result(size(pairs));
    for (Py_ssize_t i = 0; i < size(pairs); ++i) {
      auto pair = items(pairs)[i];
      require_sequence(pair, 2, true);
      auto keys = items(pair);
      const auto contact = context->control.recipient_contact(
          sim_control_bridge::key(keys[0]), sim_control_bridge::key(keys[1]), now);
      OwnedTuple row(2);
      row.set(0, float_new(contact.remaining));
      row.set(1, int_new(contact.fresh));
      result.set(i, row.release());
    }
    return result.release();
  }
  CONTROL_CATCH
}
PyObject *WOT_CDECL sim_control_radio_actors(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto context = simulation_context(identity(items(args)[0]));
    return sim_control_bridge::radio_rows(context->control.radio_actors());
  }
  CONTROL_CATCH
}

PyObject *WOT_CDECL sim_control_source_still(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 4, true);
    auto v = items(args);
    auto context = simulation_context(identity(v[0]));
    context->control.note_source_stillness(sim_control_bridge::key(v[1]),
                                           finite_number(v[2]),
                                           finite_number(v[3]));
    return int_new(1);
  }
  CONTROL_CATCH
}

#undef CONTROL_CATCH
#define WOT_SIM_CONTROL_BASE_METHODS                                           \
  {"sim_control_configure", sim_control_configure, 1,                          \
   "Install effective actor control parameters."},                             \
      {"sim_control_update", sim_control_update, 1,                            \
       "Commit copied actor sample deltas."},                                  \
      {"sim_control_begin", sim_control_begin, 1,                              \
       "Begin one ordered perception frame."},                                 \
      {"sim_control_contacts", sim_control_contacts, 1,                        \
       "Advance one observer and return contacts."},                           \
      {"sim_control_humans", sim_control_humans, 1,                            \
       "Advance human direct observations."},                                  \
      {"sim_control_observations", sim_control_observations, 1,                \
       "Consume direct observation events."},                                  \
      {"sim_control_team_contacts", sim_control_team_contacts, 1,              \
       "Export team presentation leases."},                                    \
      {"sim_control_finish", sim_control_finish, 1,                            \
       "Carry unfinished visibility debt."},                                   \
      {"sim_control_forget", sim_control_forget, 1,                            \
       "Forget one actor lifecycle."},                                         \
      {"sim_control_drive", sim_control_drive, 1,                              \
       "Advance the persistent local driver."},                                \
      {"sim_control_driver_event", sim_control_driver_event, 1,                \
       "Apply an explicit driver event."},                                     \
      {"sim_control_driver_restore", sim_control_driver_restore, 1,            \
       "Rebase driver state at authority handoff."},                           \
      {"sim_control_traffic", sim_control_traffic, 1,                          \
       "Advance friendly crossing leases."},                                   \
      {"sim_control_traffic_state", sim_control_traffic_state, 1,              \
       "Rebase crossing state at authority handoff."},

#define WOT_SIM_CONTROL_SOURCE_METHODS                                         \
  {"sim_control_source_still", sim_control_source_still, 1,                    \
   "Sample source stillness at the actor preparation boundary."},

#define WOT_SIM_CONTROL_CHECKPOINT_METHODS                                     \
  {"sim_control_snapshot", sim_control_snapshot, 1,                            \
   "Transfer typed perception state at authority boundaries."},                \
      {"sim_control_radio", sim_control_radio, 1,                              \
       "Read a persistent radio recipient."},                                  \
      {"sim_control_radio_summaries", sim_control_radio_summaries, 1,           \
       "Read ordered recipient leases without remembered poses."},            \
      {"sim_control_radio_actors", sim_control_radio_actors, 1,                \
       "Read configured radio actors."},

#define WOT_SIM_CONTROL_METHODS                                                \
  WOT_SIM_CONTROL_BASE_METHODS WOT_SIM_CONTROL_CHECKPOINT_METHODS              \
      WOT_SIM_CONTROL_SOURCE_METHODS
