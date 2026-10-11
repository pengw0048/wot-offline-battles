#include "offline_simulation_navigation.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <set>
#include <stdexcept>

namespace offline_simulation {
namespace navigation {
namespace {
const double ARRIVAL = 1.5, OFFSET = 0.42;
double distance(const Point &a, const Point &b) {
  const double x = a[0] - b[0], z = a[2] - b[2];
  return std::sqrt(x * x + z * z);
}
template <class T> Field<T> field(const T &value) {
  Field<T> f;
  f.set(value);
  return f;
}
Token cell_token(const Cell &v) {
  return Token::seq({Token::num(v[0]), Token::num(v[1])});
}
bool advances(const Point &current, const Field<Point> &target,
              const Point &goal) {
  if (!target.has())
    return false;
  const auto &t = target.value;
  if ((t[0] - current[0]) * (goal[0] - current[0]) +
          (t[2] - current[2]) * (goal[2] - current[2]) <=
      0.)
    return false;
  return distance(t, goal) + .20 < distance(current, goal);
}
} // namespace
Token Token::num(double n) {
  Token v;
  v.type = Number;
  v.number = n;
  return v;
}
Token Token::str(const std::string &s) {
  Token v;
  v.type = Text;
  v.text = s;
  return v;
}
Token Token::seq(const std::vector<Token> &v) {
  Token t;
  t.type = Tuple;
  t.tuple = v;
  return t;
}
bool Token::operator==(const Token &o) const {
  return type == o.type && number == o.number && text == o.text &&
         tuple == o.tuple;
}
bool Token::operator<(const Token &o) const {
  if (type != o.type)
    return type < o.type;
  if (type == Number)
    return number < o.number;
  if (type == Text)
    return text < o.text;
  if (type == Tuple)
    return tuple < o.tuple;
  return false;
}
Store::Store() {
  for (const auto *v : {"pending", "safe_direct", "safe_local", "reactive"})
    fallback_totals[v] = 0;
}
std::string Store::kind(const Key &k) {
  return k.type == Token::Tuple && !k.tuple.empty() &&
                 k.tuple[0].type == Token::Text
             ? k.tuple[0].text
             : "";
}
int Store::owner(const Key &k) {
  const auto type = kind(k);
  if ((type == "local" || type == "route_join" || type == "join" ||
       type == "recovery" || type == "continue") &&
      k.tuple.size() > 1 && k.tuple[1].type == Token::Number)
    return int(k.tuple[1].number);
  return -1;
}
bool Store::prefers(const Key &k) {
  return kind(k) == "route" || (kind(k) == "continue" && k.tuple.size() > 3 &&
                                k.tuple[3] == Token::str("route"));
}
Key Store::cache_key(Frontier &f, const Key &k, const Point &g) {
  return Token::seq({k, cell_token(f.cell(g))});
}
Key Store::prefix(const char *name, int bot, const Token &extra, const Key &k) {
  std::vector<Token> values = {Token::str(name), Token::num(bot), extra};
  values.insert(values.end(), k.tuple.begin(), k.tuple.end());
  return Token::seq(values);
}
void Store::accrue(double elapsed) {
  credit = std::min(384., credit + std::max(0., elapsed) * 960.);
  frame_budget = std::min(384, std::max(96, int(credit)));
}
void Store::begin_frame(Frontier &f, double elapsed) {
  ++frame_serial;
  frame_open = true;
  f.frame(true);
  accrue(elapsed);
}
void Store::end_frame(Frontier &f) {
  frame_open = false;
  f.frame(false);
}
void Store::cancel_searches(Frontier &f, const std::vector<Key> &keys) {
  std::vector<std::int64_t> jobs;
  for (const auto &key : keys) {
    const auto it = searches.find(key);
    if (it != searches.end()) {
      jobs.push_back(it->second.id);
      drop_search(key);
    }
  }
  if (!jobs.empty())
    f.cancel(jobs);
}
void Store::cancel_bot(Frontier &f, int bot, const Field<Key> &keep,
                       const std::string &type) {
  std::vector<Key> keys;
  for (const auto &entry : searches) {
    const auto &key = entry.first;
    const auto &route = key.tuple.at(0);
    if (owner(route) == bot && (!keep.has() || key != keep.value) &&
        (type.empty() || kind(route) == type)) {
      f.count("nav_search_superseded");
      keys.push_back(key);
    }
  }
  cancel_searches(f, keys);
}
void Store::close(Frontier &f) {
  if (closed)
    return;
  closed = true;
  f.close();
  for (const auto &entry : searches)
    search_order_events.push_back({entry.first, false});
  searches.clear();
}
void Store::fallback(int bot, const std::string &mode) {
  if (mode.empty() || mode == "safe_direct") {
    auto it = bots.find(bot);
    if (it != bots.end())
      it->second.pending_since.erase();
  }
  const auto it = fallback_modes.find(bot);
  const std::string old = it == fallback_modes.end() ? "" : it->second;
  if (old == mode)
    return;
  if (!old.empty() && mode.empty())
    ++recovered;
  if (mode.empty())
    fallback_modes.erase(bot);
  else {
    fallback_modes[bot] = mode;
    ++fallback_totals[mode];
  }
}
void Store::prune_modes(const std::vector<int> &ids) {
  const std::set<int> active(ids.begin(), ids.end());
  for (auto it = fallback_modes.begin(); it != fallback_modes.end();) {
    if (!active.count(it->first))
      it = fallback_modes.erase(it);
    else
      ++it;
  }
}
Penalties Store::active(std::map<int, Leases> &all, int bot, double time) {
  Penalties result;
  auto it = all.find(bot);
  if (it == all.end())
    return result;
  for (auto edge = it->second.begin(); edge != it->second.end();) {
    if (time >= edge->second.until)
      edge = it->second.erase(edge);
    else {
      result[edge->first] = edge->second.penalty;
      ++edge;
    }
  }
  if (it->second.empty())
    all.erase(it);
  return result;
}
Penalties Store::penalties(int bot, double time) {
  auto values = active(failed_edges, bot, time);
  for (const auto &v : active(macro_edges, bot, time))
    values[v.first] = v.second;
  return values;
}
bool Store::bot_penalized(Frontier &f, int bot, const Point &a, const Point &b,
                          double time) {
  const auto values = penalties(bot, time);
  if (values.empty())
    return false;
  for (const auto &edge : f.edges(a, b))
    if (values.count(edge))
      return true;
  return false;
}
bool Store::path_edges(Frontier &f, const Path &p, const Penalties &values) {
  if (values.empty())
    return false;
  for (size_t i = 1; i < p.size(); ++i)
    for (const auto &edge : f.edges(p[i - 1], p[i]))
      if (values.count(edge))
        return true;
  return false;
}
void Store::finish(Frontier &f, const Key &key, const Search &job,
                   double time) {
  drop_search(key);
  if (!job.result.empty() && job.hull_revision != f.hull_revision() &&
      f.path_hull(job.result)) {
    f.count("nav_search_stale_hull");
    return;
  }
  put_path(key, {std::make_shared<Path>(job.result), time, job.hull_revision});
  if (job.result.empty()) {
    f.count("nav_search_failed");
    ++failed;
  } else {
    f.count("nav_search_completed");
    ++completed;
  }
}
void Store::put_path(const Key &key, CachedPath value) {
  if (!paths.count(key))
    cache_order_events.push_back({key, true});
  paths[key] = std::move(value);
}
void Store::drop_path(const Key &key) {
  if (paths.erase(key))
    cache_order_events.push_back({key, false});
}
void Store::drop_search(const Key &key) {
  if (searches.erase(key))
    search_order_events.push_back({key, false});
}
void Store::flush_orders(Frontier &f) {
  if (cache_order_events.size() > 256) {
    f.container_order(0, cache_order_events);
    cache_order_events.clear();
  }
  if (search_order_events.size() > 256) {
    f.container_order(1, search_order_events);
    search_order_events.clear();
  }
}
void Store::trim(Frontier &f) {
  if (paths.size() <= 96)
    return;
  auto ordered = f.container_order(0, cache_order_events);
  cache_order_events.clear();
  if (ordered.size() != paths.size())
    throw std::runtime_error("navigation cache ordering mismatch");
  std::stable_sort(ordered.begin(), ordered.end(),
                   [this](const Key &a, const Key &b) {
                     return paths.at(a).time < paths.at(b).time;
                   });
  for (size_t i = 0; i < ordered.size() - 80; ++i)
    drop_path(ordered[i]);
}
void Store::advance(Frontier &f, double time) {
  now = time;
  flush_orders(f);
  if (!frame_open && (!automatic_time.has() ||
                      std::abs(time - automatic_time.value) >= .000001)) {
    const double elapsed =
        automatic_time.has() ? std::max(0., time - automatic_time.value) : .10;
    automatic_time.set(time);
    ++frame_serial;
    f.frame(true);
    accrue(elapsed);
  }
  if (processed_frame == frame_serial) {
    f.count("nav_batch_already_processed");
    return;
  }
  processed_frame = frame_serial;
  frame_time.set(time);
  const auto ready = f.advance(time, frame_budget);
  for (const auto &job : ready) {
    for (auto &entry : searches)
      if (entry.second.id == job.id) {
        entry.second = job;
        break;
      }
  }
  std::vector<std::pair<Key, Search>> completed_jobs;
  for (const auto &entry : searches)
    if (entry.second.done)
      completed_jobs.push_back(entry);
  if (completed_jobs.size() > 1) {
    const auto ordered = f.container_order(1, search_order_events);
    search_order_events.clear();
    completed_jobs.clear();
    for (const auto &key : ordered) {
      const auto it = searches.find(key);
      if (it != searches.end() && it->second.done)
        completed_jobs.push_back(*it);
    }
  }
  for (const auto &entry : completed_jobs)
    finish(f, entry.first, entry.second, time);
  trim(f);
}
void Store::tick(Frontier &f, double time) {
  advance(f, time);
  if (!housekeeping_time.has() || time - housekeeping_time.value >= 1.) {
    housekeeping_time.set(time);
    f.housekeeping(time);
    std::vector<int> ids;
    for (const auto &v : failed_edges)
      ids.push_back(v.first);
    for (int id : ids)
      active(failed_edges, id, time);
    ids.clear();
    for (const auto &v : macro_edges)
      ids.push_back(v.first);
    for (int id : ids)
      active(macro_edges, id, time);
  }
}
std::pair<Key, std::shared_ptr<Path>> Store::path(Frontier &f, const Key &route,
                                                  const Point &start,
                                                  const Point &goal,
                                                  double time) {
  const Key key = cache_key(f, route, goal);
  const int bot = owner(route);
  if (bot != -1)
    cancel_bot(f, bot, field(key), kind(route));
  auto cached = paths.find(key);
  if (cached != paths.end()) {
    const auto &p = *cached->second.points;
    const auto hard = penalties(bot, time);
    const bool retired =
        cached->second.hull_revision != f.hull_revision() && f.path_hull(p);
    if (!p.empty() && !retired && !f.path_penalty(p, time) &&
        !path_edges(f, p, hard)) {
      cached->second.time = time;
      cached->second.hull_revision = f.hull_revision();
      f.count("nav_path_cached");
      return {key, cached->second.points};
    }
    if (!p.empty())
      f.count("nav_path_penalty_invalidated");
    else if (time - cached->second.time < 8.) {
      f.count("nav_failed_path_cooldown");
      return {key, cached->second.points};
    } else {
      f.count("nav_failed_path_retry");
      f.clear_negative();
    }
    drop_path(key);
  }
  auto search = searches.find(key);
  if (search == searches.end()) {
    const auto hard = penalties(bot, time);
    bool rejected = false;
    if (!hard.empty())
      for (const auto &edge : f.edges(start, goal))
        if (hard.count(edge)) {
          rejected = true;
          break;
        }
    if (!rejected && f.dry(start, goal, time)) {
      auto p = std::make_shared<Path>(Path{start, goal});
      put_path(key, {p, time, f.hull_revision()});
      f.count("nav_path_direct");
      return {key, p};
    }
    Search job =
        f.submit(start, goal, time, max_expansions, prefers(route), hard);
    searches[key] = job;
    search_order_events.push_back({key, true});
    f.count("nav_search_created");
  } else
    f.count("nav_search_retained");
  advance(f, time);
  cached = paths.find(key);
  if (cached != paths.end())
    return {key, cached->second.points};
  search = searches.find(key);
  if (search != searches.end() && search->second.done) {
    const Search job = search->second;
    finish(f, key, job, time);
    cached = paths.find(key);
    if (cached != paths.end())
      return {key, cached->second.points};
  }
  return {key, std::shared_ptr<Path>()};
}
Point Store::fallback_target(Frontier &f, int bot, const Point &current,
                             const Point &goal, double time, BotState &s,
                             const std::vector<Point> &avoid) {
  if (s.controlled_shallow_target.has()) {
    const auto &ford = s.controlled_shallow_target.value;
    if (distance(current, ford) <= ARRIVAL ||
        bot_penalized(f, bot, current, ford, time) ||
        f.hazard(current, ford, 3) || !f.segment(current, ford))
      s.controlled_shallow_target.erase();
  }
  Point local;
  if (f.safe_local(current, goal, time, avoid, bot % 2 ? 1. : -1.,
                   penalties(bot, time), 0., local)) {
    s.last_target.set(local);
    s.navigation_status.set("safe");
    s.target_is_terminal.set(distance(local, goal) <= ARRIVAL);
    fallback(bot, "safe_local");
    return local;
  }
  const Point selected =
      bot_penalized(f, bot, current, goal, time) ? current : goal;
  s.last_target.set(selected);
  s.navigation_status.set("blocked");
  s.target_is_terminal.set(false);
  fallback(bot, "reactive");
  return selected;
}
Point Store::pending(Frontier &f, int bot, const Point &current,
                     const Point &goal, double time, BotState &s,
                     const std::vector<Point> &avoid, bool allow,
                     bool immediate) {
  if (allow && s.last_target.has()) {
    const auto target = s.last_target.value;
    const bool shallow = f.hazard(current, target, 4);
    if (f.penalty(current, target, time) <= 0. &&
        !bot_penalized(f, bot, current, target, time) &&
        f.segment(current, target) &&
        (!shallow || (s.controlled_shallow_target.has() &&
                      s.controlled_shallow_target.value == target)) &&
        distance(current, target) > ARRIVAL) {
      s.navigation_status.set("pending");
      s.target_is_terminal.set(false);
      fallback(bot, "pending");
      return target;
    }
  }
  if (!s.pending_since.has())
    s.pending_since.set(time);
  Point local;
  if ((immediate || time - s.pending_since.value >= .6) &&
      f.safe_local(current, goal, time, avoid, bot % 2 ? 1. : -1.,
                   penalties(bot, time), 0., local)) {
    s.last_target.set(local);
    s.navigation_status.set("pending");
    s.target_is_terminal.set(false);
    fallback(bot, "safe_local");
    return local;
  }
  s.last_target.set(current);
  s.navigation_status.set("pending");
  s.target_is_terminal.set(false);
  fallback(bot, "pending");
  return current;
}
void Store::reset_macro(BotState &s, const Point &current,
                        const Field<Point> &target, double time) {
  s.macro_progress_at.set(time);
  s.macro_progress_position.set(current);
  s.macro_progress_target = target;
  if (!target.has())
    s.macro_progress_target.clear();
  s.macro_progress_path_key = s.path_key;
  if (!s.path_key.has())
    s.macro_progress_path_key.clear();
  s.macro_progress_index.set(s.index.get(0));
}
bool Store::start_macro(Frontier &f, int bot, BotState &s, const Point &current,
                        const Point &target, double time) {
  Point escape;
  const bool have_escape =
      f.safe_local(current, target, time, {}, bot % 2 ? 1. : -1.,
                   penalties(bot, time), OFFSET, escape);
  Edge key;
  const bool have_edge = f.first_edge(current, target, key);
  if (!have_edge && !have_escape)
    return false;
  if (have_escape) {
    s.macro_escape_target.set(escape);
    s.macro_escape_until.set(time + 4.);
  } else
    macro_edges[bot][key] = {time + 4., 240.};
  s.replan_generation.set(s.replan_generation.get(0) + 1);
  s.macro_progress_replans.set(s.macro_progress_replans.get(0) + 1);
  s.path_key.clear();
  s.index.set(0);
  s.recovery_start.set(current);
  s.replan_active.set(!have_escape);
  s.macro_replan_active.set(true);
  s.pending_since.set(time - .6);
  s.controlled_shallow_target.erase();
  cancel_bot(f, bot);
  reset_macro(s, current, field(target), time);
  return true;
}
void Store::finish_macro(Frontier &f, int bot, BotState &s) {
  macro_edges.erase(bot);
  s.macro_escape_target.erase();
  s.macro_escape_until.erase();
  s.macro_replan_active.set(false);
  s.replan_active.set(false);
  s.recovery_start.clear();
  s.path_key.clear();
  s.index.set(0);
  cancel_bot(f, bot);
}
void Store::observe_macro(Frontier &f, int bot, BotState &s,
                          const Point &current, const Point &goal,
                          double time) {
  const auto target = s.last_target;
  if (s.macro_replan_active.get(false) && !s.macro_escape_target.has() &&
      active(macro_edges, bot, time).empty()) {
    finish_macro(f, bot, s);
    reset_macro(s, current, target, time);
    return;
  }
  if (!target.has() || distance(current, target.value) <= ARRIVAL ||
      distance(current, goal) <= ARRIVAL) {
    reset_macro(s, current, target, time);
    return;
  }
  const bool changed =
      s.macro_progress_path_key.get(Key()) != s.path_key.get(Key()) ||
      s.macro_progress_index.get(0) != s.index.get(0);
  if (!s.macro_progress_target.has() || changed ||
      distance(s.macro_progress_target.value, target.value) > 2.) {
    reset_macro(s, current, target, time);
    return;
  }
  const auto reference = s.macro_progress_position.get(current);
  const double progress =
      distance(reference, target.value) - distance(current, target.value);
  s.macro_progress_target.set(target.value);
  if (progress >= .2) {
    if (s.macro_replan_active.get(false))
      finish_macro(f, bot, s);
    reset_macro(s, current, target, time);
    return;
  }
  if (time - s.macro_progress_at.get(time) >= 12.)
    start_macro(f, bot, s, current, target.value, time);
}
bool Store::observe_direct(Frontier &f, int bot, const Point &current,
                           const Point &goal, const Key &key, double time,
                           bool moving, Point &output) {
  auto it = direct.find(bot);
  if (it == direct.end()) {
    DirectState s;
    s.path_key = key;
    s.target = goal;
    s.position = current;
    s.progress_at = time;
    it = direct.insert({bot, s}).first;
  }
  auto &s = it->second;
  const auto reset = [&]() {
    s.target = goal;
    s.position = current;
    s.progress_at = time;
    s.escape_target.erase();
    s.escape_until.erase();
  };
  if (s.path_key != key || distance(s.target, goal) > 2. || !moving ||
      distance(current, goal) <= ARRIVAL) {
    s.path_key = key;
    reset();
    return false;
  }
  if (s.escape_target.has()) {
    const auto escape = s.escape_target.value;
    if (time < s.escape_until.get(time) &&
        distance(current, escape) > ARRIVAL &&
        !bot_penalized(f, bot, current, escape, time) &&
        f.dry(current, escape, time)) {
      output = escape;
      return true;
    }
    reset();
    return false;
  }
  const double progress =
      distance(s.position, s.target) - distance(current, s.target);
  if (progress >= .2) {
    reset();
    return false;
  }
  if (time - s.progress_at < 12.)
    return false;
  const bool found = f.safe_local(current, goal, time, {}, bot % 2 ? 1. : -1.,
                                  penalties(bot, time), OFFSET, output);
  reset();
  if (!found)
    return false;
  s.escape_target.set(output);
  s.escape_until.set(time + 4.);
  ++s.replans;
  return true;
}
bool Store::blocked_step(Frontier &f, int bot, const Point &current,
                         const Point &target, double time) {
  auto it = bots.find(bot);
  if (it == bots.end() || distance(current, target) <= ARRIVAL)
    return false;
  auto &s = it->second;
  Edge key;
  if (!f.first_edge(current, target, key) ||
      time < s.blocked_step_escalated_until.get(0.))
    return false;
  const bool same = s.blocked_step_tracker.has() &&
                    s.blocked_step_tracker.value.key == key &&
                    time - s.blocked_step_tracker.value.last_at <= .5 &&
                    distance(current, s.blocked_step_tracker.value.origin) <=
                        std::max(1.5, f.cell_size() * .5);
  if (same) {
    ++s.blocked_step_tracker.value.count;
    s.blocked_step_tracker.value.last_at = time;
  } else {
    BlockedTracker tracker;
    tracker.key = key;
    tracker.count = 1;
    tracker.first_at = time;
    tracker.last_at = time;
    tracker.origin = current;
    s.blocked_step_tracker.set(tracker);
  }
  if (s.blocked_step_tracker.value.count < 4 ||
      time - s.blocked_step_tracker.value.first_at < 1.)
    return false;
  failed_edges[bot][key] = {time + 12., 240.};
  s.replan_generation.set(s.replan_generation.get(0) + 1);
  s.blocked_step_replans.set(s.blocked_step_replans.get(0) + 1);
  s.blocked_step_escalated_until.set(time + 2.);
  s.blocked_step_tracker.clear();
  s.path_key.clear();
  s.index.set(0);
  s.recovery_start.set(current);
  s.replan_active.set(true);
  s.macro_replan_active.set(false);
  macro_edges.erase(bot);
  s.macro_escape_target.erase();
  s.macro_escape_until.erase();
  s.controlled_shallow_target.erase();
  cancel_bot(f, bot);
  return true;
}
bool Store::blocked_plan(Frontier &f, const Point &current,
                         const Point &target) {
  if (!f.review(current, target))
    return false;
  std::vector<Key> retired;
  for (const auto &v : paths) {
    const auto &p = *v.second.points;
    for (size_t i = 1; i < p.size(); ++i)
      if (f.needs_review(p[i - 1], p[i])) {
        retired.push_back(v.first);
        break;
      }
  }
  for (const auto &key : retired)
    drop_path(key);
  retired.clear();
  for (const auto &v : searches)
    retired.push_back(v.first);
  cancel_searches(f, retired);
  return true;
}
bool Store::planned_next(Frontier &f, const Point &current, const Path &p,
                         int index, double time, int bot) {
  if (index + 1 >= int(p.size()))
    return false;
  const auto &target = p[index + 1];
  const bool reached = distance(current, p[index]) <= ARRIVAL;
  if ((!reached && !f.climb(current, p, index, index + 1)) ||
      bot_penalized(f, bot, current, target, time) ||
      f.penalty(current, target, time) > 0. || !f.segment(current, target))
    return false;
  return !f.hazard(current, target, 4) || f.hazard(p[index], target, 4);
}
bool Store::planned_current(Frontier &f, const Point &current, const Path &p,
                            int index, double time,
                            const Field<Point> &selected) {
  if (index <= 0 || index >= int(p.size()))
    return false;
  const auto &target = p[index];
  if (((!selected.has() || selected.value != target) &&
       !f.climb(current, p, index - 1, index)) ||
      f.penalty(current, target, time) > 0. || !f.segment(current, target))
    return false;
  return !f.hazard(current, target, 4) || f.hazard(p[index - 1], target, 4);
}
int Store::lookahead(Frontier &f, const Point &current, const Path &p,
                     int index, const Key &route, double time,
                     const Field<double> &distance_limit, int bot) {
  int selected = index;
  const int limit =
      std::min(int(p.size()), index + (distance_limit.has() ? 7 : 3));
  const double horizon =
      distance_limit.has() ? std::max(f.cell_size() * 2., distance_limit.value)
                           : 0.;
  const bool clearance = prefers(route);
  const auto hard = penalties(bot != -1 ? bot : owner(route), time);
  for (int candidate = index + 1; candidate < limit; ++candidate) {
    if (distance_limit.has() && candidate > index + 1 &&
        distance(current, p[candidate]) > horizon)
      break;
    if ((!clearance || f.clearance(p, index, candidate)) &&
        f.climb(current, p, index, candidate) &&
        !path_edges(f, {current, p[candidate]}, hard) &&
        f.dry(current, p[candidate], time))
      selected = candidate;
    else
      break;
  }
  return selected;
}
Point Store::next_target(Frontier &f, int bot, const Point &current, Point goal,
                         const Key &route, double time,
                         const Field<Point> &anchor,
                         const std::vector<Point> &avoid,
                         const Field<double> &look, bool moving) {
  direct.erase(bot);
  tick(f, time);
  auto it = bots.find(bot);
  if (it == bots.end()) {
    BotState s;
    s.last_position.set(current);
    s.progress_time.set(time);
    s.path_key.clear();
    s.index.set(0);
    s.recovery.set(0);
    s.recovery_until.set(0.);
    s.recovery_key.clear();
    s.recovery_start.clear();
    s.request_key.clear();
    s.request_path_key.clear();
    s.planned_goal.clear();
    s.planned_at.set(0.);
    s.navigation_status.set("pending");
    s.target_is_terminal.set(false);
    s.replan_generation.set(0);
    s.replan_active.set(false);
    s.macro_replan_active.set(false);
    s.macro_progress_replans.set(0);
    s.macro_progress_at.set(time);
    s.macro_progress_position.set(current);
    s.macro_progress_target.clear();
    s.macro_progress_path_key.clear();
    s.macro_progress_index.set(0);
    s.blocked_step_replans.set(0);
    s.blocked_step_tracker.clear();
    s.blocked_step_escalated_until.set(0.);
    it = bots.insert({bot, s}).first;
  }
  auto &s = it->second;
  if (s.request_path_key.has() && s.request_path_key.value == route &&
      s.planned_goal.has() &&
      distance(s.planned_goal.value, goal) < f.cell_size() * 2. &&
      time - s.planned_at.get(0.) < 2.)
    goal = s.planned_goal.value;
  else {
    s.request_path_key.set(route);
    s.planned_goal.set(goal);
    s.planned_at.set(time);
  }
  const Key request = cache_key(f, route, goal);
  const bool had = s.request_key.has(),
             changed = !had || s.request_key.value != request,
             transition = had && changed;
  bool allow = true;
  if (changed) {
    f.count(had ? "nav_request_changed" : "nav_request_first");
    allow = advances(current, s.last_target, goal);
    if (!allow)
      s.last_target.erase();
    cancel_bot(f, bot);
    s.request_key.set(request);
    s.path_key.clear();
    s.index.set(0);
    s.last_position.set(current);
    s.progress_time.set(time);
    s.recovery.set(0);
    s.recovery_until.set(0.);
    s.recovery_key.clear();
    s.recovery_start.clear();
    s.replan_active.set(false);
    s.macro_replan_active.set(false);
    macro_edges.erase(bot);
    s.macro_escape_target.erase();
    s.macro_escape_until.erase();
    s.pending_since.erase();
    s.controlled_shallow_target.erase();
    reset_macro(s, current, s.last_target, time);
  } else if (moving)
    observe_macro(f, bot, s, current, goal, time);
  else {
    if (s.macro_replan_active.get(false))
      finish_macro(f, bot, s);
    reset_macro(s, current, s.last_target, time);
  }
  if (s.macro_escape_target.has()) {
    const auto escape = s.macro_escape_target.value;
    if (time < s.macro_escape_until.get(time) &&
        distance(current, escape) > ARRIVAL &&
        !bot_penalized(f, bot, current, escape, time) &&
        f.dry(current, escape, time)) {
      s.controlled_shallow_target.erase();
      s.last_target.set(escape);
      s.navigation_status.set("safe");
      s.target_is_terminal.set(false);
      fallback(bot, "");
      return escape;
    }
    finish_macro(f, bot, s);
    reset_macro(s, current, s.last_target, time);
  }
  if (!s.macro_replan_active.get(false) &&
      distance(current, s.last_position.value) >= 2.) {
    s.last_position.set(current);
    s.progress_time.set(time);
    s.recovery.set(0);
    s.recovery_until.set(0.);
    s.replan_active.set(false);
    s.recovery_start.clear();
  }
  Point start = anchor.get(current);
  if (anchor.has())
    start[1] = current[1];
  Key effective = route;
  if (s.replan_active.get(false)) {
    effective =
        prefix("recovery", bot, Token::num(s.replan_generation.get(0)), route);
    start = s.recovery_start.get(current);
  }
  std::shared_ptr<Path> previous;
  auto cached = paths.find(s.path_key.get(Key()));
  if (cached != paths.end())
    previous = cached->second.points;
  auto result = path(f, effective, start, goal, time);
  Key key = result.first;
  auto points = result.second;
  if (!points || points->empty()) {
    if (f.dry(current, goal, time) &&
        !bot_penalized(f, bot, current, goal, time)) {
      s.controlled_shallow_target.erase();
      s.last_target.set(goal);
      s.navigation_status.set("safe");
      s.target_is_terminal.set(true);
      fallback(bot, "safe_direct");
      return goal;
    }
    if (!points)
      return pending(f, bot, current, goal, time, s, avoid, allow, transition);
    s.path_key.set(key);
    return fallback_target(f, bot, current, goal, time, s, avoid);
  }
  const Field<Key> active_key = s.path_key;
  if (active_key.has() && active_key.value != key) {
    cached = paths.find(active_key.value);
    if (cached != paths.end() && !cached->second.points->empty() &&
        !f.path_penalty(*cached->second.points, time)) {
      key = active_key.value;
      points = cached->second.points;
      cached->second.time = time;
    }
  }
  if (!s.path_key.has() || s.path_key.value != key) {
    s.path_key.set(key);
    int best = 0;
    double best_distance = 1e18;
    for (size_t i = 0; i < points->size(); ++i) {
      const double d = distance(current, (*points)[i]);
      if (d < best_distance) {
        best_distance = d;
        best = int(i);
      }
    }
    s.index.set(best);
  }
  int index = std::min(s.index.get(0), int(points->size()) - 1);
  Field<Point> selected_target;
  if (active_key.has() && active_key.value == key && previous == points &&
      s.last_target.has() && s.last_target.value == (*points)[index])
    selected_target = s.controlled_shallow_target;
  const bool shallow = f.hazard(current, (*points)[index], 4);
  if (f.penalty(current, (*points)[index], time) > 0. ||
      bot_penalized(f, bot, current, (*points)[index], time) ||
      (shallow &&
       !planned_current(f, current, *points, index, time, selected_target)) ||
      !f.segment(current, (*points)[index])) {
    const Key join = prefix("join", bot, cell_token(f.cell(current)), route);
    result = path(f, join, current, goal, time);
    key = result.first;
    if (!result.second)
      return pending(f, bot, current, goal, time, s, avoid, allow, transition);
    if (result.second->empty()) {
      s.path_key.set(key);
      return fallback_target(f, bot, current, goal, time, s, avoid);
    }
    points = result.second;
    s.path_key.set(key);
    s.index.set(0);
    index = 0;
  }
  const double radius = std::min(10., std::max(1.5, f.cell_size() * .55));
  while (index + 1 < int(points->size()) &&
         distance(current, (*points)[index]) < radius &&
         planned_next(f, current, *points, index, time, bot))
    ++index;
  int chosen =
      lookahead(f, current, *points, index, effective, time, look, bot);
  if (chosen == int(points->size()) - 1 &&
      distance(current, (*points)[chosen]) < radius &&
      distance((*points)[chosen], goal) > radius) {
    const Key continuation =
        prefix("continue", bot, cell_token(f.cell(current)), route);
    result = path(f, continuation, current, goal, time);
    if (result.second && !result.second->empty()) {
      points = result.second;
      s.path_key.set(result.first);
      int next_index = 0;
      if (points->size() > 1 && planned_next(f, current, *points, 0, time, bot))
        next_index = 1;
      chosen = lookahead(f, current, *points, next_index, continuation, time,
                         look, bot);
      s.index.set(chosen);
      const Point selected = (*points)[chosen];
      s.last_target.set(selected);
      if (f.hazard(current, selected, 4))
        s.controlled_shallow_target.set(selected);
      else
        s.controlled_shallow_target.erase();
      s.navigation_status.set("safe");
      s.target_is_terminal.set(distance(selected, goal) <= ARRIVAL);
      fallback(bot, "");
      return selected;
    }
    if (!result.second)
      return pending(f, bot, current, goal, time, s, avoid, allow, transition);
    return fallback_target(f, bot, current, goal, time, s, avoid);
  }
  const Point selected = (*points)[chosen];
  if (distance(current, selected) <= ARRIVAL && distance(current, goal) > 15.)
    return fallback_target(f, bot, current, goal, time, s, avoid);
  s.index.set(chosen);
  s.last_target.set(selected);
  if (f.hazard(current, selected, 4))
    s.controlled_shallow_target.set(selected);
  else
    s.controlled_shallow_target.erase();
  s.navigation_status.set("safe");
  s.target_is_terminal.set(distance(selected, goal) <= ARRIVAL);
  fallback(bot, "");
  return selected;
}
bool Store::terminal(int bot) const {
  const auto it = bots.find(bot);
  return it != bots.end() && it->second.target_is_terminal.get(false);
}
bool Store::controlled(Frontier &f, int bot, const Point &current, double yaw,
                       double maximum, bool committed) {
  const auto it = bots.find(bot);
  if (it == bots.end() || !it->second.controlled_shallow_target.has())
    return false;
  const auto &target = it->second.controlled_shallow_target.value;
  const double dx = target[0] - current[0], dz = target[2] - current[2],
               pi = std::acos(-1.);
  if (committed) {
    const double length = std::sqrt(dx * dx + dz * dz);
    if (length < .1 || (std::sin(yaw) * dx + std::cos(yaw) * dz) / length <= .5)
      return false;
  } else {
    if (std::abs(dx) + std::abs(dz) < .1)
      return false;
    double difference = yaw - std::atan2(dx, dz);
    while (difference > pi)
      difference -= pi * 2.;
    while (difference < -pi)
      difference += pi * 2.;
    if (std::abs(difference) > std::max(0., maximum))
      return false;
  }
  const double span = std::max(1., f.cell_size());
  const Point end = {{current[0] + std::sin(yaw) * span, current[1],
                      current[2] + std::cos(yaw) * span}};
  std::vector<Cell> planned, actual;
  if (!f.hazard_cells(current, target, 4, planned) || planned.empty() ||
      !f.hazard_cells(current, end, 4, actual) ||
      f.hazard(current, target, 3) || f.hazard(current, end, 3))
    return false;
  const std::set<Cell> allowed(planned.begin(), planned.end());
  for (const auto &cell : actual)
    if (!allowed.count(cell))
      return false;
  return true;
}
} // namespace navigation
} // namespace offline_simulation

namespace offline_simulation {
namespace navigation {
namespace {
const std::array<Cell, 8> NEIGHBOURS = {{{{-1, -1}},
                                         {{0, -1}},
                                         {{1, -1}},
                                         {{-1, 0}},
                                         {{1, 0}},
                                         {{-1, 1}},
                                         {{0, 1}},
                                         {{1, 1}}}};
Edge edge_key(const Cell &a, const Cell &b) {
  return a < b ? Edge{a, b} : Edge{b, a};
}
std::vector<Cell> line_cells(Cell start, Cell end) {
  int x = start[0], z = start[1];
  const int dx = std::abs(end[0] - x), dz = std::abs(end[1] - z),
            sx = x < end[0] ? 1 : -1, sz = z < end[1] ? 1 : -1;
  int error = dx - dz;
  std::vector<Cell> out = {start};
  while (x != end[0] || z != end[1]) {
    const int twice = error * 2;
    if (twice > -dz) {
      error -= dz;
      x += sx;
    }
    if (twice < dx) {
      error += dx;
      z += sz;
    }
    out.push_back({{x, z}});
  }
  return out;
}
bool covers(double cx, double cz, double extent, double x, double z,
            double sine, double cosine, double length, double width) {
  const double dx = x - cx, dz = z - cz;
  if (std::abs(dx) >
          extent + std::abs(sine) * length + std::abs(cosine) * width ||
      std::abs(dz) >
          extent + std::abs(cosine) * length + std::abs(sine) * width)
    return false;
  const double radius = extent * (std::abs(sine) + std::abs(cosine));
  return std::abs(dx * sine + dz * cosine) <= length + radius &&
         std::abs(dx * cosine - dz * sine) <= width + radius;
}
} // namespace
void Grid::validate() const {
  if (width <= 0 || height <= 0 || cell_size < 1. ||
      !std::isfinite(cell_size) ||
      heights_mm.size() != size_t(width) * height ||
      links.size() != heights_mm.size() || hazards.size() != heights_mm.size())
    throw std::invalid_argument("invalid baked navigation map");
}
Cell Grid::cell(const Point &p) const {
  return {{int(std::floor((p[0] - origin_x) / cell_size + .5)),
           int(std::floor((p[2] - origin_z) / cell_size + .5))}};
}
Point Grid::point(const Cell &c, double y) const {
  return {{origin_x + c[0] * cell_size, y, origin_z + c[1] * cell_size}};
}
int Grid::flat(const Cell &c) const {
  return c[0] < 0 || c[0] >= width || c[1] < 0 || c[1] >= height
             ? -1
             : c[1] * width + c[0];
}
int Grid::index(const Cell &c) const {
  const int i = flat(c);
  return i < 0 || std::isnan(heights_mm[i]) ? -1 : i;
}
bool Grid::nearest(const Cell &c, int maximum, Cell &out) const {
  if (index(c) >= 0) {
    out = c;
    return true;
  }
  bool found = false;
  int best = 0;
  for (int radius = 1; radius <= std::max(0, maximum); ++radius) {
    for (int z = c[1] - radius; z <= c[1] + radius; ++z)
      for (int x = c[0] - radius; x <= c[0] + radius; ++x) {
        if (std::max(std::abs(x - c[0]), std::abs(z - c[1])) != radius ||
            index({{x, z}}) < 0)
          continue;
        const int d = (x - c[0]) * (x - c[0]) + (z - c[1]) * (z - c[1]);
        if (!found || d < best) {
          out = {{x, z}};
          found = true;
          best = d;
        }
      }
    if (found)
      return true;
  }
  return false;
}
bool Grid::inside(double x, double z) const {
  return !bounds.has() || (bounds.value[0] <= x && x <= bounds.value[2] &&
                           bounds.value[1] <= z && z <= bounds.value[3]);
}
bool Grid::ground(double x, double z, double &out) const {
  if (!inside(x, z))
    return false;
  const int i = index(cell({{x, 0., z}}));
  if (i < 0)
    return false;
  out = heights_mm[i] / 1000.;
  return true;
}
std::vector<Cell> Grid::cells(const Point &a, const Point &b,
                              bool support) const {
  Cell start = cell(a), end = cell(b);
  if (support && !nearest(start, 2, start))
    return {};
  if ((support ? (index(start) < 0 || index(end) < 0)
               : (flat(start) < 0 || flat(end) < 0)))
    return {};
  return line_cells(start, end);
}
std::vector<Edge> Grid::edges(const Point &a, const Point &b) const {
  const auto row = line_cells(cell(a), cell(b));
  std::vector<Edge> out;
  for (size_t i = 1; i < row.size(); ++i)
    out.push_back(edge_key(row[i - 1], row[i]));
  return out;
}
Grid::Corridor Grid::corridor(const Point &a, const Point &b) {
  const Edge key = {cell(a), cell(b)};
  const auto cached = corridors.find(key);
  if (cached != corridors.end())
    return cached->second;
  const auto row = cells(a, b);
  Corridor out;
  out.clear = out.known = !row.empty();
  for (size_t i = 1; i < row.size(); ++i) {
    const int next = index(row[i]);
    if (next < 0) {
      out.clear = false;
      out.known = false;
      break;
    }
    out.hazards |= hazards[next];
    if (out.clear) {
      const int previous = index(row[i - 1]);
      const Cell delta = {
          {row[i][0] - row[i - 1][0], row[i][1] - row[i - 1][1]}};
      unsigned bit = 0;
      for (size_t j = 0; j < NEIGHBOURS.size(); ++j)
        if (NEIGHBOURS[j] == delta) {
          bit = 1u << j;
          break;
        }
      if (previous < 0 || !(links[previous] & bit))
        out.clear = false;
    }
  }
  if (corridors.size() >= 2048) {
    corridors.erase(corridor_order.front());
    corridor_order.pop_front();
  }
  corridors[key] = out;
  corridor_order.push_back(key);
  return out;
}
bool Grid::hazard(const Point &a, const Point &b, int mask) {
  const auto c = corridor(a, b);
  return !c.known || (c.hazards & unsigned(mask));
}
bool Grid::motion_hazard(const Point &a, const Point &b, int mask) const {
  const auto row = cells(a, b, false);
  if (row.empty())
    return true;
  for (size_t i = 1; i < row.size(); ++i) {
    const int j = flat(row[i]);
    if (j < 0 || (hazards[j] & unsigned(mask)))
      return true;
  }
  return false;
}
bool Grid::hazard_cells(const Point &a, const Point &b, int mask,
                        std::vector<Cell> &out) const {
  if (index(cell(a)) < 0)
    return false;
  const auto row = cells(a, b);
  if (row.empty())
    return false;
  for (size_t i = 1; i < row.size(); ++i) {
    const int j = index(row[i]);
    if (j < 0)
      return false;
    if (hazards[j] & unsigned(mask))
      out.push_back(row[i]);
  }
  return true;
}
bool Grid::point_hazard(const Point &p, int mask) const {
  const int i = flat(cell(p));
  return i >= 0 && (hazards[i] & unsigned(mask));
}
bool Grid::hazard_near(const Point &p, int radius) const {
  const Cell c = cell(p);
  radius = std::max(0, radius);
  for (int z = c[1] - radius; z <= c[1] + radius; ++z)
    for (int x = c[0] - radius; x <= c[0] + radius; ++x) {
      const int i = flat({{x, z}});
      if (i >= 0 && (hazards[i] & 3u))
        return true;
    }
  return false;
}
bool Grid::segment(const Point &a, const Point &b, const Proof &proof) {
  if (!inside(b[0], b[2]))
    return false;
  if (distance(a, b) < .25)
    return true;
  if (!corridor(a, b).clear)
    return false;
  return !needs_review(a, b) || proof(a, b);
}
bool Grid::dry(const Point &a, const Point &b, double time,
               const Proof &proof) {
  return penalty(a, b, time) <= 0. && !hazard(a, b, 4) && segment(a, b, proof);
}
double Grid::timed(const Edge &key, double time) {
  auto it = failed_edges.find(key);
  if (it == failed_edges.end())
    return 0.;
  if (time >= it->second.until) {
    failed_edges.erase(it);
    return 0.;
  }
  return it->second.penalty;
}
double Grid::penalty(const Point &a, const Point &b, double time) {
  if (failed_edges.empty() && static_edges.empty())
    return 0.;
  double result = 0.;
  for (const auto &edge : edges(a, b)) {
    const auto it = static_edges.find(edge);
    result =
        std::max(result, std::max(it == static_edges.end() ? 0. : it->second,
                                  timed(edge, time)));
  }
  return result;
}
bool Grid::path_penalty(const Path &p, double time) {
  if (failed_edges.empty())
    return false;
  for (size_t i = 1; i < p.size(); ++i)
    for (const auto &edge : edges(p[i - 1], p[i]))
      if (timed(edge, time) > 0.)
        return true;
  return false;
}
bool Grid::path_hull(const Path &p) const {
  if (static_edges.empty())
    return false;
  for (size_t i = 1; i < p.size(); ++i)
    for (const auto &edge : edges(p[i - 1], p[i]))
      if (static_edges.count(edge))
        return true;
  return false;
}
void Grid::prune(double time) {
  for (auto it = failed_edges.begin(); it != failed_edges.end();) {
    if (time >= it->second.until)
      it = failed_edges.erase(it);
    else
      ++it;
  }
  if (failed_edges.size() > 128) {
    std::vector<std::pair<Edge, double>> ordered;
    for (const auto &key : failed_order) {
      const auto it = failed_edges.find(key);
      if (it != failed_edges.end())
        ordered.push_back({key, it->second.until});
    }
    std::stable_sort(
        ordered.begin(), ordered.end(),
        [](const std::pair<Edge, double> &a, const std::pair<Edge, double> &b) {
          return a.second < b.second;
        });
    for (size_t i = 0; i < ordered.size() - 128; ++i)
      failed_edges.erase(ordered[i].first);
  }
}
bool Grid::set_hulls(const std::vector<std::array<double, 6>> &key) {
  if (hull_key.has() && hull_key.value == key)
    return false;
  hull_key.set(key);
  Penalties replacement;
  const double extent = cell_size * .5;
  for (const auto &h : key) {
    const double x = h[1], z = h[2], sine = std::sin(h[3]),
                 cosine = std::cos(h[3]), length = std::max(.5, h[4]),
                 width = std::max(.3, h[5]),
                 radius = std::sqrt(length * length + width * width);
    const Cell first = cell({{x - radius, 0., z - radius}}),
               last = cell({{x + radius, 0., z + radius}});
    for (int cz = first[1]; cz <= last[1]; ++cz)
      for (int cx = first[0]; cx <= last[0]; ++cx) {
        const Point centre = point({{cx, cz}}, 0.);
        if (!covers(centre[0], centre[2], extent, x, z, sine, cosine, length,
                    width))
          continue;
        for (int dz = -1; dz <= 1; ++dz)
          for (int dx = -1; dx <= 1; ++dx)
            if (dx || dz)
              replacement[edge_key({{cx, cz}}, {{cx + dx, cz + dz}})] = 240.;
      }
  }
  if (replacement == static_edges)
    return false;
  ++hull_revision;
  static_edges = std::move(replacement);
  return true;
}
bool Grid::review(const Point &a, const Point &destination) {
  if (!obstacle_available)
    return false;
  const double span = distance(a, destination);
  if (span <= 0.)
    return false;
  const double fraction = std::min(1., cell_size * 8. / span);
  Point b;
  for (int i = 0; i < 3; ++i)
    b[i] = a[i] + (destination[i] - a[i]) * fraction;
  const auto row = cells(a, b, false);
  const size_t before = review_cells.size();
  for (const auto &c : row) {
    if (!review_seeds.insert(c).second)
      continue;
    for (int dx = -8; dx <= 8; ++dx)
      for (int dz = -8; dz <= 8; ++dz) {
        const Cell p = {{c[0] + dx, c[1] + dz}};
        if (flat(p) >= 0)
          review_cells.insert(p);
      }
  }
  return review_cells.size() != before;
}
bool Grid::needs_review(const Point &a, const Point &b) const {
  if (review_cells.empty())
    return false;
  for (const auto &c : cells(a, b, false))
    if (review_cells.count(c))
      return true;
  return false;
}
int Grid::link_count(const Cell &c) const {
  const int i = index(c);
  if (i < 0)
    return 0;
  unsigned mask = links[i] & 255u;
  int count = 0;
  for (; mask; mask >>= 1)
    count += mask & 1u;
  return count;
}
bool Grid::exposure(const Path &p, double &out) const {
  if (p.empty()) {
    out = 0.;
    return true;
  }
  std::vector<Cell> row;
  if (p.size() == 1) {
    Cell c;
    if (!nearest(cell(p[0]), 2, c))
      return false;
    row.push_back(c);
  } else
    for (size_t i = 1; i < p.size(); ++i) {
      auto segment = cells(p[i - 1], p[i]);
      if (segment.empty())
        return false;
      size_t first = !row.empty() && row.back() == segment[0] ? 1 : 0;
      row.insert(row.end(), segment.begin() + first, segment.end());
    }
  if (row.empty())
    return false;
  int missing = 0;
  for (const auto &c : row)
    missing += 8 - link_count(c);
  out = double(missing) / double(row.size());
  return true;
}
bool Grid::clearance(const Path &p, int begin, int end, double increase) const {
  if (end - begin < 2)
    return true;
  double original = 0., shortcut = 0.;
  return exposure(Path(p.begin() + begin, p.begin() + end + 1), original) &&
         exposure({p[begin], p[end]}, shortcut) &&
         shortcut <= original + increase;
}
bool Grid::climb(const Path &p, int begin, int end, double grade_limit,
                 double turn_limit) {
  if (end - begin < 2)
    return true;
  const double pi = std::acos(-1.);
  for (int i = begin + 1; i < end; ++i) {
    const auto &before = p[i - 1], &pivot = p[i], &after = p[i + 1];
    const double ox = after[0] - pivot[0], oz = after[2] - pivot[2],
                 run = std::sqrt(ox * ox + oz * oz);
    if (run <= .1 || (after[1] - pivot[1]) / run <= grade_limit)
      continue;
    const double ix = pivot[0] - before[0], iz = pivot[2] - before[2];
    if (std::abs(ix) + std::abs(iz) <= .1)
      continue;
    double turn = std::atan2(ox, oz) - std::atan2(ix, iz);
    while (turn > pi)
      turn -= pi * 2.;
    while (turn < -pi)
      turn += pi * 2.;
    if (std::abs(turn) > turn_limit)
      return false;
  }
  return true;
}
bool Grid::live_climb(const Point &current, const Path &p, int begin, int end) {
  if (end < begin)
    return true;
  Path live = {current};
  live.insert(live.end(), p.begin() + begin, p.begin() + end + 1);
  return climb(live, 0, int(live.size()) - 1);
}
double Grid::cell_penalty(const Cell &c, const std::vector<Point> &avoid,
                          bool prefer) const {
  double total =
      prefer ? double(std::max(0, 8 - link_count(c))) * cell_size * .20 : 0.;
  const int i = index(c);
  if (i >= 0 && (hazards[i] & 4u))
    total += cell_size * 4.;
  if (avoid.empty())
    return total;
  const Point centre = point(c, 0.);
  for (const auto &p : avoid) {
    const double span = distance(centre, p);
    if (span < cell_size * 1.5)
      total += (cell_size * 1.5 - span) * 3.;
  }
  return total;
}
bool Grid::safe_local(const Point &current, const Point &goal, double time,
                      const std::vector<Point> &avoid, double preference,
                      const Penalties &hard, double minimum, Point &out,
                      const Proof &proof) {
  const double dx = goal[0] - current[0], dz = goal[2] - current[2];
  if (std::abs(dx) + std::abs(dz) < .1)
    return false;
  const double desired = std::atan2(dx, dz), side = preference >= 0. ? 1. : -1.;
  const double offsets[] = {0.,           side * .45,  -side * .45,
                            side * .85,   -side * .85, side * 1.30,
                            -side * 1.30, side * 1.75, -side * 1.75};
  const double spans[] = {cell_size * .78, cell_size * .52};
  bool found = false;
  std::pair<double, double> best;
  for (double span : spans)
    for (double offset : offsets) {
      if (std::abs(offset) < std::max(0., minimum))
        continue;
      const double yaw = desired + offset,
                   x = current[0] + std::sin(yaw) * span,
                   z = current[2] + std::cos(yaw) * span;
      double y = 0.;
      if (!ground(x, z, y))
        continue;
      const Point candidate = {{x, y, z}};
      if (!dry(current, candidate, time, proof))
        continue;
      bool rejected = false;
      if (!hard.empty())
        for (const auto &edge : edges(current, candidate))
          if (hard.count(edge)) {
            rejected = true;
            break;
          }
      if (rejected)
        continue;
      const std::pair<double, double> score = {
          distance(candidate, goal) + std::abs(offset) * 3.5 +
              cell_penalty(cell(candidate), avoid, false) * 2.,
          std::abs(offset)};
      if (!found || score < best) {
        found = true;
        best = score;
        out = candidate;
      }
    }
  return found;
}
bool Grid::local_corridor(const Point &p, std::array<double, 2> &out) {
  const Cell c = cell(p);
  if (index(c) < 0)
    return false;
  const auto it = local_corridors.find(c);
  if (it != local_corridors.end()) {
    out = it->second;
    return true;
  }
  const Cell axes[] = {{{1, 0}}, {{1, 1}}, {{0, 1}}, {{-1, 1}}};
  double spans[4];
  int longest = 0;
  for (int axis = 0; axis < 4; ++axis) {
    const auto &v = axes[axis];
    const double step = std::hypot(v[0], v[1]) * cell_size;
    int count = 1;
    for (int sign : {1, -1})
      for (int reach = 1; reach <= 5; ++reach) {
        if (index({{c[0] + v[0] * reach * sign, c[1] + v[1] * reach * sign}}) <
            0)
          break;
        ++count;
      }
    spans[axis] = count * step;
    if (spans[axis] > spans[longest])
      longest = axis;
  }
  out = {{std::atan2(double(axes[longest][0]), double(axes[longest][1])),
          spans[(longest + 2) % 4]}};
  local_corridors[c] = out;
  return true;
}
bool Grid::hull_pose(const Point &p, double yaw, double length,
                     double width) const {
  length = std::max(.5, length);
  width = std::max(.3, width);
  const double sine = std::sin(yaw), cosine = std::cos(yaw);
  for (double along : {-length, 0., length})
    for (double across : {-width, 0., width})
      if (index(cell({{p[0] + sine * along + cosine * across, 0.,
                       p[2] + cosine * along + (-sine) * across}})) < 0)
        return false;
  return true;
}
} // namespace navigation
} // namespace offline_simulation

namespace offline_simulation {
namespace navigation {
AsyncState::AsyncState(AsyncState &&other) noexcept {
  *this = std::move(other);
}
AsyncState &AsyncState::operator=(AsyncState &&other) noexcept {
  if (this == &other)
    return *this;
  if (active && owns && !closed)
    offline_navigation::close(context);
  context = other.context;
  next_job_id = other.next_job_id;
  active = other.active;
  owns = other.owns;
  closed = other.closed;
  jobs = std::move(other.jobs);
  watched = std::move(other.watched);
  settled = std::move(other.settled);
  queries = std::move(other.queries);
  order = std::move(other.order);
  stats = other.stats;
  submitted = other.submitted;
  completed = other.completed;
  cancelled = other.cancelled;
  query_count = other.query_count;
  expansions = other.expansions;
  worker_seconds = other.worker_seconds;
  main_seconds = other.main_seconds;
  maximum_age = other.maximum_age;
  other.active = false;
  other.owns = false;
  other.context = 0;
  return *this;
}
AsyncState::~AsyncState() {
  if (active && owns && !closed)
    offline_navigation::close(context);
}
Search AsyncState::submit(const Grid &grid, const Point &start,
                          const Point &goal, double time, int limit,
                          bool prefer, const Penalties &hard,
                          const Count &count) {
  if (closed)
    throw std::runtime_error("native navigation context is closed");
  Search job;
  job.id = ++next_job_id;
  job.hull_revision = grid.hull_revision;
  job.submitted_at = time;
  offline_navigation::SearchInput input;
  input.job_id = job.id;
  input.hull_revision = job.hull_revision;
  input.start = start;
  input.goal = goal;
  input.max_expansions = static_cast<unsigned>(limit);
  input.prefer_clearance = prefer;
  Penalties world = grid.static_edges;
  for (const auto &v : grid.failed_edges)
    if (time < v.second.until) {
      const auto it = world.find(v.first);
      world[v.first] =
          std::max(it == world.end() ? 0. : it->second, v.second.penalty);
    }
  for (const auto &v : hard) {
    input.edge_penalties.push_back({v.first.first, v.first.second, v.second});
    input.hard_edges.push_back(v.first);
  }
  for (const auto &v : world)
    input.world_penalties.push_back({v.first.first, v.first.second, v.second});
  input.native_review_cells.assign(grid.review_cells.begin(),
                                   grid.review_cells.end());
  if (!offline_navigation::submit(context, std::move(input)))
    throw std::runtime_error("native navigation job was not accepted");
  jobs[job.id] = job;
  ++submitted;
  count("nav_async_submitted", 1);
  return job;
}
void AsyncState::poll(double time, std::vector<Search> &done,
                      const Count &count) {
  auto result = offline_navigation::poll(context);
  stats = result.stats;
  for (const auto &value : result.completed) {
    auto it = jobs.find(value.job_id);
    queries.erase(value.job_id);
    if (it == jobs.end())
      continue;
    Search job = it->second;
    jobs.erase(it);
    job.done = true;
    job.status = static_cast<int>(value.status);
    job.last_frame.set(time);
    job.result = value.status == 0 ? value.path : Path();
    job.hull_revision = static_cast<int>(value.hull_revision);
    if (watched.count(job.id))
      settled[job.id] = {job, int(value.status), time, true};
    done.push_back(std::move(job));
    ++completed;
    expansions += value.expanded;
    worker_seconds += value.worker_seconds;
    maximum_age =
        std::max(maximum_age, std::max(0., time - done.back().submitted_at));
    count("nav_async_completed", 1);
    count("nav_astar_expansions", static_cast<int>(value.expanded));
  }
  for (const auto &query : result.queries) {
    if (!jobs.count(query.job_id))
      continue;
    auto found = queries.find(query.job_id);
    if (found == queries.end()) {
      queries[query.job_id] = {};
      order.push_back(query.job_id);
    }
    queries[query.job_id].push_back(query);
  }
}
std::vector<Search> AsyncState::advance(double time, int budget,
                                        const Proof &proof,
                                        const Count &count) {
  std::vector<Search> done;
  if (closed)
    return done;
  const auto started = std::chrono::system_clock::now();
  const auto settle_time = [&]() {
    main_seconds += std::max(0., std::chrono::duration<double>(
                                     std::chrono::system_clock::now() - started)
                                     .count());
  };
  try {
    poll(time, done, count);
    std::vector<std::pair<std::int64_t, bool>> answers;
    std::vector<offline_navigation::Query> deferred;
    budget = std::max(0, budget);
    while (budget && !order.empty()) {
      const auto id = order.front();
      order.pop_front();
      auto found = queries.find(id);
      if (found == queries.end() || found->second.empty() || !jobs.count(id)) {
        queries.erase(id);
        continue;
      }
      const auto query = found->second.front();
      found->second.pop_front();
      bool clear = false, unknown = false;
      try {
        const auto answer = proof(query.start, query.end);
        clear = answer.first;
        unknown = answer.second;
      } catch (...) {
        count("nav_async_query_failed", 1);
      }
      if (!clear && unknown) {
        deferred.push_back(query);
        count("nav_async_query_unknown", 1);
      } else if (jobs.count(id))
        answers.push_back({query.query_id, clear});
      --budget;
      if (!found->second.empty())
        order.push_back(id);
      else
        queries.erase(found);
    }
    for (const auto &query : deferred) {
      if (!jobs.count(query.job_id))
        continue;
      auto found = queries.find(query.job_id);
      if (found == queries.end()) {
        queries[query.job_id] = {};
        order.push_back(query.job_id);
      }
      queries[query.job_id].push_back(query);
    }
    if (!answers.empty()) {
      offline_navigation::answer(context, answers);
      query_count += answers.size();
      count("nav_async_queries", static_cast<int>(answers.size()));
    }
    poll(time, done, count);
    settle_time();
    return done;
  } catch (...) {
    settle_time();
    throw;
  }
}
void AsyncState::cancel(const std::vector<std::int64_t> &identifiers,
                        const Count &count) {
  std::vector<std::int64_t> removed;
  for (const auto id : identifiers) {
    auto found = jobs.find(id);
    if (found == jobs.end())
      continue;
    if (watched.count(id)) {
      Search job = found->second;
      job.done = true;
      job.status = 1;
      job.result.clear();
      settled[id] = {job, 1, 0., false};
    }
    jobs.erase(found);
    removed.push_back(id);
    queries.erase(id);
  }
  if (!removed.empty() && !closed) {
    offline_navigation::cancel(context, removed);
    cancelled += removed.size();
    count("nav_async_cancelled", static_cast<int>(removed.size()));
  }
}
void AsyncState::close() {
  if (closed)
    return;
  closed = true;
  for (const auto &entry : jobs)
    if (watched.count(entry.first)) {
      Search job = entry.second;
      job.done = true;
      job.status = 1;
      job.result.clear();
      settled[job.id] = {job, 1, 0., false};
    }
  jobs.clear();
  queries.clear();
  order.clear();
  offline_navigation::close(context);
}
} // namespace navigation
} // namespace offline_simulation
