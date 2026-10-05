// Cold snapshots contain copied values only; callbacks live for one GIL-held
// call.
namespace sim_navigation_bridge {
namespace n = offline_simulation::navigation;
int integer(PyObject *v) {
  return weapon_bridge::integer(v);
}
std::string text(PyObject *v) {
  require_sequence(v, 0);
  std::string out;
  for (Py_ssize_t i = 0; i < size(v); ++i) {
    const int b = integer(items(v)[i]);
    if (b < 0 || b > 255)
      throw Unsupported();
    out.push_back(static_cast<char>(b));
  }
  return out;
}
PyObject *value(const std::string &s) {
  OwnedTuple out(s.size());
  for (size_t i = 0; i < s.size(); ++i)
    out.set(i, int_new(static_cast<unsigned char>(s[i])));
  return out.release();
}
PyObject *value(double v) {
  return float_new(v);
}
PyObject *value(int v) {
  return int_new(v);
}
PyObject *value(bool v) {
  return int_new(v);
}
n::Point point(PyObject *v) {
  n::Point p;
  world_array(v, p.data(), 3);
  return p;
}
PyObject *value(const n::Point &p) {
  return world_numbers(p.data(), 3);
}
n::Cell cell(PyObject *v) {
  require_sequence(v, 2, true);
  return {{integer(items(v)[0]), integer(items(v)[1])}};
}
PyObject *value(const n::Cell &c) {
  OwnedTuple out(2);
  out.set(0, int_new(c[0]));
  out.set(1, int_new(c[1]));
  return out.release();
}
n::Edge edge(PyObject *v) {
  require_sequence(v, 2, true);
  return {cell(items(v)[0]), cell(items(v)[1])};
}
PyObject *value(const n::Edge &e) {
  OwnedTuple out(2);
  out.set(0, value(e.first));
  out.set(1, value(e.second));
  return out.release();
}
n::Token token(PyObject *v, int depth = 0) {
  if (depth > 32)
    throw Unsupported();
  require_sequence(v, 2, true);
  n::Token t;
  t.type = static_cast<n::Token::Type>(integer(items(v)[0]));
  auto p = items(v)[1];
  if (t.type == n::Token::Number)
    t.number = finite_number(p);
  else if (t.type == n::Token::Text)
    t.text = text(p);
  else if (t.type == n::Token::Tuple) {
    require_sequence(p, 0);
    for (Py_ssize_t i = 0; i < size(p); ++i)
      t.tuple.push_back(token(items(p)[i], depth + 1));
  } else if (t.type != n::Token::None)
    throw Unsupported();
  return t;
}
PyObject *value(const n::Token &t) {
  OwnedTuple out(2);
  out.set(0, int_new(t.type));
  if (t.type == n::Token::Number)
    out.set(1, float_new(t.number));
  else if (t.type == n::Token::Text)
    out.set(1, value(t.text));
  else if (t.type == n::Token::Tuple) {
    OwnedTuple row(t.tuple.size());
    for (size_t i = 0; i < t.tuple.size(); ++i)
      row.set(i, value(t.tuple[i]));
    out.set(1, row.release());
  } else
    out.set(1, fallback());
  return out.release();
}
n::Path path(PyObject *v) {
  require_sequence(v, 0);
  n::Path out;
  for (Py_ssize_t i = 0; i < size(v); ++i)
    out.push_back(point(items(v)[i]));
  return out;
}
PyObject *value(const n::Path &p) {
  OwnedTuple out(p.size());
  for (size_t i = 0; i < p.size(); ++i)
    out.set(i, value(p[i]));
  return out.release();
}
n::BlockedTracker tracker(PyObject *v) {
  require_sequence(v, 5, true);
  auto p = items(v);
  n::BlockedTracker t;
  t.key = edge(p[0]);
  t.count = integer(p[1]);
  t.first_at = finite_number(p[2]);
  t.last_at = finite_number(p[3]);
  t.origin = point(p[4]);
  return t;
}
PyObject *value(const n::BlockedTracker &t) {
  OwnedTuple out(5);
  out.set(0, value(t.key));
  out.set(1, int_new(t.count));
  out.set(2, float_new(t.first_at));
  out.set(3, float_new(t.last_at));
  out.set(4, value(t.origin));
  return out.release();
}
void read_value(PyObject *v, double &out) {
  out = finite_number(v);
}
void read_value(PyObject *v, int &out) {
  out = integer(v);
}
void read_value(PyObject *v, bool &out) {
  out = integer(v) != 0;
}
void read_value(PyObject *v, std::string &out) {
  out = text(v);
}
void read_value(PyObject *v, n::Point &out) {
  out = point(v);
}
void read_value(PyObject *v, n::Token &out) {
  out = token(v);
}
void read_value(PyObject *v, n::BlockedTracker &out) {
  out = tracker(v);
}
template <class T> void read_field(PyObject *v, n::Field<T> &out) {
  require_sequence(v, 1);
  const int status = integer(items(v)[0]);
  if (status == 0)
    out.erase();
  else if (status == 1)
    out.clear();
  else if (status == 2) {
    require_sequence(v, 2, true);
    T item;
    read_value(items(v)[1], item);
    out.set(item);
  } else
    throw Unsupported();
}
template <class T> PyObject *value(const n::Field<T> &f) {
  OwnedTuple out(f.has() ? 2 : 1);
  out.set(0, int_new(!f.present ? 0 : f.null ? 1 : 2));
  if (f.has())
    out.set(1, value(f.value));
  return out.release();
}
#define NAV_BOT_FIELDS(F)                                                      \
  F(last_position)                                                             \
  F(recovery_start)                                                            \
  F(planned_goal) F(macro_progress_position) F(macro_progress_target)          \
      F(last_target) F(macro_escape_target) F(controlled_shallow_target)       \
          F(path_key) F(recovery_key) F(request_key) F(request_path_key)       \
              F(macro_progress_path_key) F(progress_time) F(recovery_until)    \
                  F(planned_at) F(macro_progress_at)                           \
                      F(blocked_step_escalated_until) F(macro_escape_until)    \
                          F(pending_since) F(index) F(recovery) F(             \
                              replan_generation) F(macro_progress_replans)     \
                              F(macro_progress_index) F(blocked_step_replans)  \
                                  F(target_is_terminal) F(replan_active)       \
                                      F(macro_replan_active)                   \
                                          F(navigation_status)                 \
                                              F(blocked_step_tracker)
n::BotState bot(PyObject *v) {
  require_sequence(v, 31, true);
  n::BotState out;
  int i = 0;
#define NAV_READ(field) read_field(items(v)[i++], out.field);
  NAV_BOT_FIELDS(NAV_READ)
#undef NAV_READ
  return out;
}
PyObject *value(const n::BotState &s) {
  OwnedTuple out(31);
  int i = 0;
#define NAV_WRITE(field) out.set(i++, value(s.field));
  NAV_BOT_FIELDS(NAV_WRITE)
#undef NAV_WRITE
  return out.release();
}
n::Search search(PyObject *v) {
  require_sequence(v, 6, true);
  auto p = items(v);
  n::Search s;
  s.id = weapon_bridge::wide(p[0]);
  s.hull_revision = integer(p[1]);
  s.submitted_at = finite_number(p[2]);
  s.done = integer(p[3]) != 0;
  s.result = path(p[4]);
  require_sequence(p[5], 2, true);
  s.status = integer(items(p[5])[0]);
  if (s.status < 0 || s.status > 2)
    throw Unsupported();
  if (!absent(items(p[5])[1]))
    s.last_frame.set(finite_number(items(p[5])[1]));
  return s;
}
PyObject *value(const n::Search &s) {
  OwnedTuple out(6);
  out.set(0, integer_result(s.id));
  out.set(1, int_new(s.hull_revision));
  out.set(2, float_new(s.submitted_at));
  out.set(3, int_new(s.done));
  out.set(4, value(s.result));
  OwnedTuple completion(2);
  completion.set(0, int_new(s.status));
  completion.set(1, s.last_frame.has() ? float_new(s.last_frame.value)
                                       : fallback());
  out.set(5, completion.release());
  return out.release();
}
PyObject *value(const n::Penalties &p) {
  OwnedTuple out(p.size());
  size_t i = 0;
  for (const auto &v : p) {
    OwnedTuple row(2);
    row.set(0, value(v.first));
    row.set(1, float_new(v.second));
    out.set(i++, row.release());
  }
  return out.release();
}
std::map<int, n::Leases> leases(PyObject *v) {
  require_sequence(v, 0);
  std::map<int, n::Leases> out;
  for (Py_ssize_t i = 0; i < size(v); ++i) {
    auto row = items(v)[i];
    require_sequence(row, 2, true);
    int id = integer(items(row)[0]);
    auto edges = items(row)[1];
    require_sequence(edges, 0);
    for (Py_ssize_t j = 0; j < size(edges); ++j) {
      auto e = items(edges)[j];
      require_sequence(e, 3, true);
      out[id][edge(items(e)[0])] = {finite_number(items(e)[1]),
                                    finite_number(items(e)[2])};
    }
  }
  return out;
}
PyObject *value(const std::map<int, n::Leases> &v) {
  OwnedTuple out(v.size());
  size_t i = 0;
  for (const auto &b : v) {
    OwnedTuple row(2), edges(b.second.size());
    row.set(0, int_new(b.first));
    size_t j = 0;
    for (const auto &e : b.second) {
      OwnedTuple item(3);
      item.set(0, value(e.first));
      item.set(1, float_new(e.second.until));
      item.set(2, float_new(e.second.penalty));
      edges.set(j++, item.release());
    }
    row.set(1, edges.release());
    out.set(i++, row.release());
  }
  return out.release();
}
void load(n::Store &out, PyObject *v) {
  require_sequence(v, 9, true);
  auto p = items(v);
  for (int k = 0; k < 8; ++k)
    require_sequence(p[k], 0);
  for (Py_ssize_t i = 0; i < size(p[0]); ++i) {
    auto row = items(p[0])[i];
    require_sequence(row, 4, true);
    auto a = items(row);
    out.paths[token(a[0])] = {std::make_shared<n::Path>(path(a[1])),
                              finite_number(a[2]), integer(a[3])};
  }
  for (Py_ssize_t i = 0; i < size(p[1]); ++i) {
    auto row = items(p[1])[i];
    require_sequence(row, 2, true);
    out.searches[token(items(row)[0])] = search(items(row)[1]);
  }
  for (Py_ssize_t i = 0; i < size(p[2]); ++i) {
    auto row = items(p[2])[i];
    require_sequence(row, 2, true);
    out.bots[integer(items(row)[0])] = bot(items(row)[1]);
  }
  out.failed_edges = leases(p[3]);
  out.macro_edges = leases(p[4]);
  for (Py_ssize_t i = 0; i < size(p[5]); ++i) {
    auto row = items(p[5])[i];
    require_sequence(row, 8, true);
    auto a = items(row);
    n::DirectState s;
    s.path_key = token(a[1]);
    s.target = point(a[2]);
    s.position = point(a[3]);
    s.progress_at = finite_number(a[4]);
    s.replans = integer(a[5]);
    read_field(a[6], s.escape_target);
    read_field(a[7], s.escape_until);
    out.direct[integer(a[0])] = s;
  }
  for (Py_ssize_t i = 0; i < size(p[6]); ++i) {
    auto row = items(p[6])[i];
    require_sequence(row, 2, true);
    out.fallback_modes[integer(items(row)[0])] = text(items(row)[1]);
  }
  for (Py_ssize_t i = 0; i < size(p[7]); ++i) {
    auto row = items(p[7])[i];
    require_sequence(row, 2, true);
    out.fallback_totals[text(items(row)[0])] = integer(items(row)[1]);
  }
  require_sequence(p[8], 16, true);
  auto a = items(p[8]);
  read_field(a[0], out.frame_time);
  read_field(a[1], out.housekeeping_time);
  read_field(a[2], out.automatic_time);
  read_field(a[3], out.next_key);
  out.credit = finite_number(a[4]);
  out.now = finite_number(a[5]);
  out.frame_serial = integer(a[6]);
  out.processed_frame = integer(a[7]);
  out.frame_budget = integer(a[8]);
  out.max_expansions = integer(a[9]);
  out.completed = integer(a[10]);
  out.failed = integer(a[11]);
  out.recovered = integer(a[12]);
  out.frame_open = integer(a[13]) != 0;
  out.installed = integer(a[14]) != 0;
  out.closed = integer(a[15]) != 0;
}
PyObject *snapshot(const n::Store &s) {
  OwnedTuple out(9), paths(s.paths.size()), jobs(s.searches.size()),
      bots(s.bots.size()), direct(s.direct.size()),
      modes(s.fallback_modes.size()), totals(s.fallback_totals.size()),
      timing(16);
  size_t i = 0;
  for (const auto &v : s.paths) {
    OwnedTuple row(4);
    row.set(0, value(v.first));
    row.set(1, value(*v.second.points));
    row.set(2, float_new(v.second.time));
    row.set(3, int_new(v.second.hull_revision));
    paths.set(i++, row.release());
  }
  i = 0;
  for (const auto &v : s.searches) {
    OwnedTuple row(2);
    row.set(0, value(v.first));
    row.set(1, value(v.second));
    jobs.set(i++, row.release());
  }
  i = 0;
  for (const auto &v : s.bots) {
    OwnedTuple row(2);
    row.set(0, int_new(v.first));
    row.set(1, value(v.second));
    bots.set(i++, row.release());
  }
  i = 0;
  for (const auto &v : s.direct) {
    const auto &d = v.second;
    OwnedTuple row(8);
    row.set(0, int_new(v.first));
    row.set(1, value(d.path_key));
    row.set(2, value(d.target));
    row.set(3, value(d.position));
    row.set(4, float_new(d.progress_at));
    row.set(5, int_new(d.replans));
    row.set(6, value(d.escape_target));
    row.set(7, value(d.escape_until));
    direct.set(i++, row.release());
  }
  i = 0;
  for (const auto &v : s.fallback_modes) {
    OwnedTuple row(2);
    row.set(0, int_new(v.first));
    row.set(1, value(v.second));
    modes.set(i++, row.release());
  }
  i = 0;
  for (const auto &v : s.fallback_totals) {
    OwnedTuple row(2);
    row.set(0, value(v.first));
    row.set(1, int_new(v.second));
    totals.set(i++, row.release());
  }
  timing.set(0, value(s.frame_time));
  timing.set(1, value(s.housekeeping_time));
  timing.set(2, value(s.automatic_time));
  timing.set(3, value(s.next_key));
  const double scalar[] = {s.credit,
                           s.now,
                           double(s.frame_serial),
                           double(s.processed_frame),
                           double(s.frame_budget),
                           double(s.max_expansions),
                           double(s.completed),
                           double(s.failed),
                           double(s.recovered),
                           s.frame_open ? 1. : 0.,
                           s.installed ? 1. : 0.,
                           s.closed ? 1. : 0.};
  for (int j = 0; j < 12; ++j)
    timing.set(j + 4, float_new(scalar[j]));
  out.set(0, paths.release());
  out.set(1, jobs.release());
  out.set(2, bots.release());
  out.set(3, value(s.failed_edges));
  out.set(4, value(s.macro_edges));
  out.set(5, direct.release());
  out.set(6, modes.release());
  out.set(7, totals.release());
  out.set(8, timing.release());
  return out.release();
}

std::vector<n::Cell> cells(PyObject *v) {
  require_sequence(v, 0);
  std::vector<n::Cell> out;
  for (Py_ssize_t i = 0; i < size(v); ++i)
    out.push_back(cell(items(v)[i]));
  return out;
}
PyObject *cell_values(const std::set<n::Cell> &v) {
  OwnedTuple out(v.size());
  size_t i = 0;
  for (const auto &c : v)
    out.set(i++, value(c));
  return out.release();
}
void grid_load(n::Grid &g, PyObject *config, PyObject *state) {
  require_sequence(config, 6, true);
  auto c = items(config);
  double numbers[10];
  world_array(c[0], numbers, 10);
  g.origin_x = numbers[0];
  g.origin_z = numbers[1];
  g.cell_size = numbers[2];
  g.width = integer(items(c[0])[3]);
  g.height = integer(items(c[0])[4]);
  g.max_grade_up = numbers[5];
  g.max_grade_down = numbers[6];
  g.baked_max_grade = numbers[7];
  g.heuristic_weight = numbers[8];
  g.obstacle_available = numbers[9] != 0.;
  require_sequence(c[1], 0);
  for (Py_ssize_t i = 0; i < size(c[1]); ++i)
    g.heights_mm.push_back(absent(items(c[1])[i])
                               ? std::numeric_limits<double>::quiet_NaN()
                               : finite_number(items(c[1])[i]));
  for (int k = 2; k < 4; ++k) {
    require_sequence(c[k], 0);
    auto &out = k == 2 ? g.links : g.hazards;
    for (Py_ssize_t i = 0; i < size(c[k]); ++i) {
      const int n = integer(items(c[k])[i]);
      if (n < 0)
        throw Unsupported();
      out.push_back(static_cast<unsigned>(n));
    }
  }
  if (!absent(c[4])) {
    std::array<double, 4> bounds;
    world_array(c[4], bounds.data(), 4);
    g.bounds.set(bounds);
  } else
    g.bounds.clear();
  require_sequence(state, 8, true);
  auto p = items(state);
  g.hull_revision = integer(p[1]);
  if (!absent(p[0])) {
    require_sequence(p[0], 0);
    std::vector<std::array<double, 6>> hulls;
    for (Py_ssize_t i = 0; i < size(p[0]); ++i) {
      std::array<double, 6> h;
      world_array(items(p[0])[i], h.data(), 6);
      hulls.push_back(h);
    }
    g.hull_key.set(hulls);
  } else
    g.hull_key.clear();
  require_sequence(p[2], 0);
  for (Py_ssize_t i = 0; i < size(p[2]); ++i) {
    auto r = items(p[2])[i];
    require_sequence(r, 3, true);
    const auto key = edge(items(r)[0]);
    g.failed_edges[key] = {finite_number(items(r)[1]),
                           finite_number(items(r)[2])};
    g.failed_order.push_back(key);
  }
  require_sequence(p[3], 0);
  for (Py_ssize_t i = 0; i < size(p[3]); ++i) {
    auto r = items(p[3])[i];
    require_sequence(r, 2, true);
    g.static_edges[edge(items(r)[0])] = finite_number(items(r)[1]);
  }
  for (const auto &v : cells(p[4]))
    g.review_cells.insert(v);
  for (const auto &v : cells(p[5]))
    g.review_seeds.insert(v);
  require_sequence(p[6], 0);
  for (Py_ssize_t i = 0; i < size(p[6]); ++i) {
    auto r = items(p[6])[i];
    require_sequence(r, 3, true);
    const auto k = edge(items(r)[0]);
    n::Grid::Corridor v;
    v.clear = integer(items(r)[1]) != 0;
    v.known = !absent(items(r)[2]);
    if (v.known)
      v.hazards = static_cast<unsigned>(integer(items(r)[2]));
    g.corridors[k] = v;
    g.corridor_order.push_back(k);
  }
  require_sequence(p[7], 0);
  for (Py_ssize_t i = 0; i < size(p[7]); ++i) {
    auto r = items(p[7])[i];
    require_sequence(r, 2, true);
    std::array<double, 2> pair;
    world_array(items(r)[1], pair.data(), 2);
    g.local_corridors[cell(items(r)[0])] = pair;
  }
  g.validate();
  g.installed = true;
}
PyObject *grid_snapshot(const n::Grid &g) {
  OwnedTuple out(8), hulls(g.hull_key.has() ? g.hull_key.value.size() : 0),
      failed(g.failed_edges.size()), corridors(g.corridor_order.size()),
      local(g.local_corridors.size());
  size_t i = 0;
  if (g.hull_key.has()) {
    for (const auto &h : g.hull_key.value)
      hulls.set(i++, world_numbers(h.data(), 6));
    out.set(0, hulls.release());
  } else
    out.set(0, fallback());
  out.set(1, int_new(g.hull_revision));
  i = 0;
  for (const auto &v : g.failed_edges) {
    OwnedTuple row(3);
    row.set(0, value(v.first));
    row.set(1, float_new(v.second.until));
    row.set(2, float_new(v.second.penalty));
    failed.set(i++, row.release());
  }
  i = 0;
  for (const auto &key : g.corridor_order) {
    const auto &v = g.corridors.at(key);
    OwnedTuple row(3);
    row.set(0, value(key));
    row.set(1, int_new(v.clear));
    row.set(2, v.known ? int_new(v.hazards) : fallback());
    corridors.set(i++, row.release());
  }
  i = 0;
  for (const auto &v : g.local_corridors) {
    OwnedTuple row(2);
    row.set(0, value(v.first));
    row.set(1, world_numbers(v.second.data(), 2));
    local.set(i++, row.release());
  }
  out.set(2, failed.release());
  out.set(3, value(g.static_edges));
  out.set(4, cell_values(g.review_cells));
  out.set(5, cell_values(g.review_seeds));
  out.set(6, corridors.release());
  out.set(7, local.release());
  return out.release();
}

void async_load(n::AsyncState &out, PyObject *v) {
  if (absent(v))
    return;
  require_sequence(v, 7, true);
  auto p = items(v);
  require_sequence(p[0], 12, true);
  auto scalar = items(p[0]);
  out.context = weapon_bridge::wide(scalar[0]);
  out.next_job_id = weapon_bridge::wide(scalar[1]);
  out.closed = integer(scalar[2]) != 0;
  out.submitted = weapon_bridge::wide(scalar[3]);
  out.completed = weapon_bridge::wide(scalar[4]);
  out.cancelled = weapon_bridge::wide(scalar[5]);
  out.query_count = weapon_bridge::wide(scalar[6]);
  out.expansions = weapon_bridge::wide(scalar[7]);
  out.worker_seconds = finite_number(scalar[8]);
  out.main_seconds = finite_number(scalar[9]);
  out.maximum_age = finite_number(scalar[10]);
  require_sequence(p[1], 0);
  for (Py_ssize_t i = 0; i < size(p[1]); ++i) {
    const auto job = search(items(p[1])[i]);
    out.jobs[job.id] = job;
  }
  require_sequence(p[2], 0);
  for (Py_ssize_t i = 0; i < size(p[2]); ++i) {
    auto raw = items(p[2])[i];
    require_sequence(raw, 2, true);
    const auto id = weapon_bridge::wide(items(raw)[0]);
    auto rows = items(raw)[1];
    require_sequence(rows, 0);
    for (Py_ssize_t j = 0; j < size(rows); ++j) {
      auto row = items(rows)[j];
      require_sequence(row, 3, true);
      offline_navigation::Query q;
      q.query_id = weapon_bridge::wide(items(row)[0]);
      q.job_id = id;
      q.start = point(items(row)[1]);
      q.end = point(items(row)[2]);
      out.queries[id].push_back(q);
    }
  }
  require_sequence(p[3], 0);
  for (Py_ssize_t i = 0; i < size(p[3]); ++i)
    out.order.push_back(weapon_bridge::wide(items(p[3])[i]));
  require_sequence(p[4], 0);
  for (Py_ssize_t i = 0; i < std::min<Py_ssize_t>(6, size(p[4])); ++i)
    out.stats[i] =
        static_cast<std::uint64_t>(weapon_bridge::wide(items(p[4])[i]));
  require_sequence(p[5], 0);
  for (Py_ssize_t i = 0; i < size(p[5]); ++i)
    out.watched.insert(weapon_bridge::wide(items(p[5])[i]));
  out.active = true;
  out.owns = false;
}
PyObject *async_snapshot(const n::AsyncState &s) {
  if (!s.active)
    return fallback();
  OwnedTuple out(7), scalar(12), jobs(s.jobs.size()), queries(s.queries.size()),
      order(s.order.size()), stats(6), watched(s.watched.size());
  const double values[] = {
      double(s.context),     double(s.next_job_id), s.closed ? 1. : 0.,
      double(s.submitted),   double(s.completed),   double(s.cancelled),
      double(s.query_count), double(s.expansions),  s.worker_seconds,
      s.main_seconds,        s.maximum_age,         0.};
  for (int i = 0; i < 12; ++i)
    scalar.set(i, float_new(values[i]));
  size_t i = 0;
  for (const auto &v : s.jobs)
    jobs.set(i++, value(v.second));
  i = 0;
  for (const auto &entry : s.queries) {
    OwnedTuple row(2), queue(entry.second.size());
    row.set(0, integer_result(entry.first));
    size_t j = 0;
    for (const auto &q : entry.second) {
      OwnedTuple item(3);
      item.set(0, integer_result(q.query_id));
      item.set(1, value(q.start));
      item.set(2, value(q.end));
      queue.set(j++, item.release());
    }
    row.set(1, queue.release());
    queries.set(i++, row.release());
  }
  i = 0;
  for (auto id : s.order)
    order.set(i++, integer_result(id));
  for (i = 0; i < 6; ++i)
    stats.set(i, integer_result(s.stats[i]));
  i = 0;
  for (auto id : s.watched)
    watched.set(i++, integer_result(id));
  out.set(0, scalar.release());
  out.set(1, jobs.release());
  out.set(2, queries.release());
  out.set(3, order.release());
  out.set(4, stats.release());
  out.set(5, watched.release());
  out.set(6, int_new(0));
  return out.release();
}
class Engine : public n::Frontier {
  std::shared_ptr<SimulationContext> context_;
  PyObject *dispatcher_;
  n::Grid &grid_;
  WorldObject call(int op, OwnedTuple &payload) {
    if (context_->closed.load())
      throw Unsupported();
    OwnedTuple args(2);
    args.set(0, int_new(op));
    args.set(1, payload.release());
    WorldObject arguments(args.release());
    PyObject *result = object_call(dispatcher_, arguments.get(), 0);
    if (context_->closed.load()) {
      if (result)
        decref(result);
      throw Unsupported();
    }
    return WorldObject(result);
  }

public:
  Engine(std::shared_ptr<SimulationContext> c, PyObject *d, PyObject *unused)
      : context_(std::move(c)), dispatcher_(d),
        grid_(context_->navigation.grid) {
    (void)unused;
  }
  std::pair<bool, bool> proof_receipt(const n::Point &a, const n::Point &b) {
    OwnedTuple row(2);
    row.set(0, value(a));
    row.set(1, value(b));
    auto r = call(30, row);
    require_sequence(r.get(), 2, true);
    return {integer(items(r.get())[0]) != 0, integer(items(r.get())[1]) != 0};
  }
  bool proof(const n::Point &a, const n::Point &b) {
    return proof_receipt(a, b).first;
  }
  n::Grid::Proof proof() {
    return [this](const n::Point &a, const n::Point &b) {
      return this->proof(a, b);
    };
  }
  n::Cell cell(const n::Point &p) override {
    return grid_.cell(p);
  }
  double cell_size() const override {
    return grid_.cell_size;
  }
  int hull_revision() const override {
    return grid_.hull_revision;
  }
  bool hazard(const n::Point &a, const n::Point &b, int mask) override {
    return grid_.hazard(a, b, mask);
  }
  bool hazard_cells(const n::Point &a, const n::Point &b, int mask,
                    std::vector<n::Cell> &out) override {
    return grid_.hazard_cells(a, b, mask, out);
  }
  bool segment(const n::Point &a, const n::Point &b) override {
    return grid_.segment(a, b, proof());
  }
  bool dry(const n::Point &a, const n::Point &b, double time) override {
    return grid_.dry(a, b, time, proof());
  }
  double penalty(const n::Point &a, const n::Point &b, double time) override {
    return grid_.penalty(a, b, time);
  }
  std::vector<n::Edge> edges(const n::Point &a, const n::Point &b) override {
    return grid_.edges(a, b);
  }
  bool first_edge(const n::Point &a, const n::Point &b, n::Edge &out) override {
    const auto rows = grid_.edges(a, b);
    if (rows.empty())
      return false;
    out = rows[0];
    return true;
  }
  bool safe_local(const n::Point &a, const n::Point &b, double time,
                  const std::vector<n::Point> &avoid, double side,
                  const n::Penalties &hard, double angle,
                  n::Point &out) override {
    return grid_.safe_local(a, b, time, avoid, side, hard, angle, out, proof());
  }
  bool path_penalty(const n::Path &p, double time) override {
    return grid_.path_penalty(p, time);
  }
  bool path_hull(const n::Path &p) override {
    return grid_.path_hull(p);
  }
  bool clearance(const n::Path &p, int begin, int end) override {
    return grid_.clearance(p, begin, end);
  }
  bool climb(const n::Point &a, const n::Path &p, int begin, int end) override {
    return n::Grid::live_climb(a, p, begin, end);
  }
  bool review(const n::Point &a, const n::Point &b) override {
    return grid_.review(a, b);
  }
  bool needs_review(const n::Point &a, const n::Point &b) override {
    return grid_.needs_review(a, b);
  }
  void clear_negative() override {
    OwnedTuple row(0);
    call(15, row);
  }
  void frame(bool open) override {
    OwnedTuple row(1);
    row.set(0, int_new(open));
    call(16, row);
  }
  void housekeeping(double time) override {
    grid_.prune(time);
    OwnedTuple row(1);
    row.set(0, float_new(time));
    call(17, row);
  }
  n::Search submit(const n::Point &a, const n::Point &b, double time, int limit,
                   bool prefer, const n::Penalties &hard) override {
    if (context_->navigation.async.active)
      return context_->navigation.async.submit(
          grid_, a, b, time, limit, prefer, hard,
          [this](const char *name, int amount) { count(name, amount); });
    OwnedTuple row(6);
    row.set(0, value(a));
    row.set(1, value(b));
    row.set(2, float_new(time));
    row.set(3, int_new(limit));
    row.set(4, int_new(prefer));
    row.set(5, value(hard));
    auto r = call(18, row);
    return search(r.get());
  }
  std::vector<n::Search> advance(double time, int budget) override {
    if (context_->navigation.async.active)
      return context_->navigation.async.advance(
          time, budget,
          [this](const n::Point &a, const n::Point &b) {
            return proof_receipt(a, b);
          },
          [this](const char *name, int amount) { count(name, amount); });
    OwnedTuple row(2);
    row.set(0, float_new(time));
    row.set(1, int_new(budget));
    auto r = call(19, row);
    require_sequence(r.get(), 0);
    std::vector<n::Search> out;
    for (Py_ssize_t i = 0; i < size(r.get()); ++i)
      out.push_back(search(items(r.get())[i]));
    return out;
  }
  void cancel(const std::vector<std::int64_t> &ids) override {
    if (context_->navigation.async.active) {
      context_->navigation.async.cancel(
          ids, [this](const char *name, int amount) { count(name, amount); });
      return;
    }
    OwnedTuple row(ids.size());
    for (size_t i = 0; i < ids.size(); ++i)
      row.set(i, integer_result(ids[i]));
    call(20, row);
  }
  void close() override {
    if (context_->navigation.async.active) {
      context_->navigation.async.close();
      return;
    }
    OwnedTuple row(0);
    call(21, row);
  }
  std::vector<n::Key>
  container_order(int kind, const std::vector<n::OrderEvent> &events) override {
    OwnedTuple row(2), changes(events.size());
    row.set(0, int_new(kind));
    for (size_t i = 0; i < events.size(); ++i) {
      OwnedTuple item(2);
      item.set(0, value(events[i].first));
      item.set(1, int_new(events[i].second));
      changes.set(i, item.release());
    }
    row.set(1, changes.release());
    auto result = call(40, row);
    require_sequence(result.get(), 0);
    std::vector<n::Key> out;
    for (Py_ssize_t i = 0; i < size(result.get()); ++i)
      out.push_back(token(items(result.get())[i]));
    return out;
  }
  void count(const char *name, int amount) override {
    OwnedTuple row(2);
    row.set(0, string_new(name));
    row.set(1, int_new(amount));
    call(22, row);
  }
};
} // namespace sim_navigation_bridge
PyObject *WOT_CDECL sim_navigation_install(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 5, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    if (ctx->navigation.installed)
      throw Unsupported();
    offline_simulation::navigation::Store state;
    sim_navigation_bridge::load(state, items(args)[1]);
    sim_navigation_bridge::grid_load(state.grid, items(args)[2],
                                     items(args)[3]);
    sim_navigation_bridge::async_load(state.async, items(args)[4]);
    state.installed = true;
    state.async.owns = state.async.active;
    ctx->navigation = std::move(state);
    return int_new(1);
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}
PyObject *WOT_CDECL sim_navigation_snapshot(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    if (!ctx->navigation.installed)
      throw Unsupported();
    for (const auto &v : ctx->navigation.searches)
      if (!v.second.done)
        ctx->navigation.async.watched.insert(v.second.id);
    return sim_navigation_bridge::snapshot(ctx->navigation);
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}
PyObject *WOT_CDECL sim_navigation_remove(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    ctx->navigation.async.owns = false;
    ctx->navigation = offline_simulation::navigation::Store();
    return int_new(1);
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}
PyObject *WOT_CDECL sim_navigation_command(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 5, true);
    auto a = items(args);
    auto ctx = simulation_context(identity(a[0]));
    auto &s = ctx->navigation;
    if (!s.installed || s.closed)
      throw Unsupported();
    sim_navigation_bridge::Engine f(ctx, a[3], a[4]);
    const int op = sim_navigation_bridge::integer(a[1]);
    require_sequence(a[2], 0);
    auto p = items(a[2]);
    const auto arity = [&](int n) {
      if (size(a[2]) != n)
        throw Unsupported();
    };
    const auto number = [&](int i) { return finite_number(p[i]); };
    const auto integer = [&](int i) {
      return sim_navigation_bridge::integer(p[i]);
    };
    const auto point = [&](int i) {
      return sim_navigation_bridge::point(p[i]);
    };
    PyObject *answer = 0;
    switch (op) {
    case 0:
      arity(1);
      s.begin_frame(f, number(0));
      break;
    case 1:
      arity(0);
      s.end_frame(f);
      break;
    case 2:
      arity(1);
      s.tick(f, number(0));
      break;
    case 3: {
      arity(9);
      sim_navigation_bridge::n::Field<sim_navigation_bridge::n::Point> anchor;
      sim_navigation_bridge::n::Field<double> look;
      sim_navigation_bridge::read_field(p[5], anchor);
      sim_navigation_bridge::read_field(p[7], look);
      answer = sim_navigation_bridge::value(s.next_target(
          f, integer(0), point(1), point(2), sim_navigation_bridge::token(p[3]),
          number(4), anchor, sim_navigation_bridge::path(p[6]), look,
          integer(8) != 0));
      break;
    }
    case 4: {
      arity(6);
      sim_navigation_bridge::n::Point out;
      answer = s.observe_direct(f, integer(0), point(1), point(2),
                                sim_navigation_bridge::token(p[3]), number(4),
                                integer(5) != 0, out)
                   ? sim_navigation_bridge::value(out)
                   : fallback();
      break;
    }
    case 5:
      arity(4);
      answer =
          int_new(s.blocked_step(f, integer(0), point(1), point(2), number(3)));
      break;
    case 6:
      arity(2);
      answer = int_new(s.blocked_plan(f, point(0), point(1)));
      break;
    case 7:
      arity(4);
      answer = int_new(
          s.bot_penalized(f, integer(0), point(1), point(2), number(3)));
      break;
    case 8:
      arity(5);
      answer = int_new(s.controlled(f, integer(0), point(1), number(2),
                                    number(3), integer(4) != 0));
      break;
    case 9:
      arity(1);
      answer = int_new(s.terminal(integer(0)));
      break;
    case 10:
      arity(0);
      s.close(f);
      break;
    case 12: {
      arity(2);
      const auto ready = f.advance(number(0), integer(1));
      for (const auto &job : ready)
        for (auto &entry : s.searches)
          if (entry.second.id == job.id) {
            entry.second = job;
            break;
          }
      break;
    }
    case 11: {
      arity(1);
      require_sequence(p[0], 0);
      std::vector<int> ids;
      for (Py_ssize_t i = 0; i < size(p[0]); ++i)
        ids.push_back(sim_navigation_bridge::integer(items(p[0])[i]));
      s.prune_modes(ids);
      break;
    }
    default:
      throw Unsupported();
    }
    OwnedTuple out(1);
    out.set(0, answer ? answer : int_new(1));
    return out.release();
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}

PyObject *WOT_CDECL sim_navigation_grid_snapshot(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    if (!ctx->navigation.installed)
      throw Unsupported();
    return sim_navigation_bridge::grid_snapshot(ctx->navigation.grid);
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}
PyObject *WOT_CDECL sim_navigation_bot_snapshot(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 2, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    if (!ctx->navigation.installed)
      throw Unsupported();
    const auto it = ctx->navigation.bots.find(
        sim_navigation_bridge::integer(items(args)[1]));
    OwnedTuple out(it == ctx->navigation.bots.end() ? 0 : 1);
    if (it != ctx->navigation.bots.end())
      out.set(0, sim_navigation_bridge::value(it->second));
    return out.release();
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}
PyObject *WOT_CDECL sim_navigation_grid_command(PyObject *, PyObject *args) {
  try {
    namespace b = sim_navigation_bridge;
    namespace n = offline_simulation::navigation;
    require_sequence(args, 4, true);
    auto a = items(args);
    auto ctx = simulation_context(identity(a[0]));
    auto &g = ctx->navigation.grid;
    if (!ctx->navigation.installed)
      throw Unsupported();
    b::Engine f(ctx, a[3], none_object);
    const int op = b::integer(a[1]);
    require_sequence(a[2], 0);
    auto p = items(a[2]);
    const auto arity = [&](int n) {
      if (size(a[2]) != n)
        throw Unsupported();
    };
    const auto number = [&](int i) { return finite_number(p[i]); };
    const auto integer = [&](int i) { return b::integer(p[i]); };
    const auto point = [&](int i) { return b::point(p[i]); };
    PyObject *answer = 0;
    switch (op) {
    case 0:
      arity(1);
      answer = b::value(g.cell(point(0)));
      break;
    case 1:
      arity(3);
      answer = int_new(g.hazard(point(0), point(1), integer(2)));
      break;
    case 2: {
      arity(3);
      std::vector<n::Cell> rows;
      if (g.hazard_cells(point(0), point(1), integer(2), rows)) {
        OwnedTuple out(rows.size());
        for (size_t i = 0; i < rows.size(); ++i)
          out.set(i, b::value(rows[i]));
        answer = out.release();
      } else
        answer = fallback();
      break;
    }
    case 3:
      arity(2);
      answer = int_new(g.segment(point(0), point(1), f.proof()));
      break;
    case 4:
      arity(3);
      answer = int_new(g.dry(point(0), point(1), number(2), f.proof()));
      break;
    case 5:
      arity(3);
      answer = float_new(g.penalty(point(0), point(1), number(2)));
      break;
    case 6: {
      arity(2);
      const auto rows = g.edges(point(0), point(1));
      OwnedTuple out(rows.size());
      for (size_t i = 0; i < rows.size(); ++i)
        out.set(i, b::value(rows[i]));
      answer = out.release();
      break;
    }
    case 7: {
      arity(2);
      const auto rows = g.edges(point(0), point(1));
      answer = rows.empty() ? fallback() : b::value(rows[0]);
      break;
    }
    case 8: {
      arity(7);
      n::Penalties hard;
      require_sequence(p[5], 0);
      for (Py_ssize_t i = 0; i < size(p[5]); ++i) {
        auto row = items(p[5])[i];
        require_sequence(row, 2, true);
        hard[b::edge(items(row)[0])] = finite_number(items(row)[1]);
      }
      n::Point out;
      answer = g.safe_local(point(0), point(1), number(2), b::path(p[3]),
                            number(4), hard, number(6), out, f.proof())
                   ? b::value(out)
                   : fallback();
      break;
    }
    case 9:
      arity(2);
      answer = int_new(g.path_penalty(b::path(p[0]), number(1)));
      break;
    case 10:
      arity(1);
      answer = int_new(g.path_hull(b::path(p[0])));
      break;
    case 11:
      arity(4);
      answer = int_new(
          g.clearance(b::path(p[0]), integer(1), integer(2), number(3)));
      break;
    case 12:
      arity(4);
      answer = int_new(
          n::Grid::live_climb(point(0), b::path(p[1]), integer(2), integer(3)));
      break;
    case 13:
      arity(2);
      answer = int_new(g.review(point(0), point(1)));
      break;
    case 14:
      arity(2);
      answer = int_new(g.needs_review(point(0), point(1)));
      break;
    case 17:
      arity(1);
      g.prune(number(0));
      break;
    case 23: {
      arity(2);
      n::Cell out;
      answer = int_new(g.nearest(g.cell(point(0)), integer(1), out));
      break;
    }
    case 24:
      arity(3);
      answer = int_new(g.motion_hazard(point(0), point(1), integer(2)));
      break;
    case 25:
      arity(2);
      answer = int_new(g.point_hazard(point(0), integer(1)));
      break;
    case 26:
      arity(2);
      answer = int_new(g.hazard_near(point(0), integer(1)));
      break;
    case 27: {
      arity(1);
      std::array<double, 2> out;
      answer = g.local_corridor(point(0), out) ? world_numbers(out.data(), 2)
                                               : fallback();
      break;
    }
    case 28:
      arity(4);
      answer = int_new(g.hull_pose(point(0), number(1), number(2), number(3)));
      break;
    case 29: {
      arity(1);
      require_sequence(p[0], 0);
      std::vector<std::array<double, 6>> rows;
      for (Py_ssize_t i = 0; i < size(p[0]); ++i) {
        std::array<double, 6> row;
        world_array(items(p[0])[i], row.data(), 6);
        rows.push_back(row);
      }
      answer = int_new(g.set_hulls(rows));
      break;
    }
    case 31: {
      arity(3);
      double y = 0.;
      answer = g.ground(number(0), number(1), y) ? float_new(y) : fallback();
      break;
    }
    case 32:
      arity(2);
      answer = b::value(g.point(b::cell(p[0]), number(1)));
      break;
    case 33:
      arity(0);
      answer = int_new(g.hull_revision);
      break;
    default:
      throw Unsupported();
    }
    OwnedTuple out(1);
    out.set(0, answer ? answer : int_new(1));
    return out.release();
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}

PyObject *WOT_CDECL sim_navigation_async_snapshot(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    if (!ctx->navigation.installed)
      throw Unsupported();
    return sim_navigation_bridge::async_snapshot(ctx->navigation.async);
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}
PyObject *WOT_CDECL sim_navigation_receipts(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    auto &s = ctx->navigation.async;
    OwnedTuple out(s.settled.size());
    size_t i = 0;
    for (const auto &entry : s.settled) {
      const auto &v = entry.second;
      OwnedTuple row(4);
      row.set(0, sim_navigation_bridge::value(v.job));
      row.set(1, int_new(v.status));
      row.set(2, v.has_frame ? float_new(v.last_frame) : fallback());
      bool retained = false;
      for (const auto &job : ctx->navigation.searches)
        if (job.second.id == entry.first) {
          retained = true;
          break;
        }
      row.set(3, int_new(retained));
      out.set(i++, row.release());
      s.watched.erase(entry.first);
    }
    s.settled.clear();
    return out.release();
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}

PyObject *WOT_CDECL sim_navigation_order_events(PyObject *, PyObject *args) {
  try {
    require_sequence(args, 1, true);
    auto ctx = simulation_context(identity(items(args)[0]));
    OwnedTuple out(2);
    for (int kind = 0; kind < 2; ++kind) {
      auto &events = kind == 0 ? ctx->navigation.cache_order_events
                               : ctx->navigation.search_order_events;
      OwnedTuple rows(events.size());
      for (size_t i = 0; i < events.size(); ++i) {
        OwnedTuple row(2);
        row.set(0, sim_navigation_bridge::value(events[i].first));
        row.set(1, int_new(events[i].second));
        rows.set(i, row.release());
      }
      out.set(kind, rows.release());
      events.clear();
    }
    return out.release();
  } catch (const PythonAllocationFailed &) {
    return 0;
  } catch (...) {
    return fallback();
  }
}

#define WOT_SIM_NAVIGATION_METHODS                                             \
  {"sim_navigation_order_events", sim_navigation_order_events, 1,              \
   "Transfer cold container-order events."},                                   \
      {"sim_navigation_async_snapshot", sim_navigation_async_snapshot, 1,      \
       "Copy native asynchronous navigation ownership."},                      \
      {"sim_navigation_receipts", sim_navigation_receipts, 1,                  \
       "Settle retained Python receipt identities."},                          \
      {"sim_navigation_grid_snapshot", sim_navigation_grid_snapshot, 1,        \
       "Copy native terrain evidence."},                                       \
      {"sim_navigation_bot_snapshot", sim_navigation_bot_snapshot, 1,          \
       "Copy one current navigation consumer."},                               \
      {"sim_navigation_grid_command", sim_navigation_grid_command, 1,          \
       "Run native baked-grid rules."},                                        \
      {"sim_navigation_install", sim_navigation_install, 1,                    \
       "Install copied persistent navigation state."},                         \
      {"sim_navigation_snapshot", sim_navigation_snapshot, 1,                  \
       "Copy persistent navigation state."},                                   \
      {"sim_navigation_remove", sim_navigation_remove, 1,                      \
       "Detach navigation state without cancelling transferred jobs."},        \
      {"sim_navigation_command", sim_navigation_command, 1,                    \
       "Advance native route and recovery state."},
