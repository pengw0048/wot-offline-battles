#ifndef WOT_OFFLINE_SIMULATION_NAVIGATION_H
#define WOT_OFFLINE_SIMULATION_NAVIGATION_H
#include "offline_navigation.h"
#include <array>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace offline_simulation {
namespace navigation {
using Point = std::array<double, 3>;
using Cell = std::array<int, 2>;
using Edge = std::pair<Cell, Cell>;
using Path = std::vector<Point>;
using Penalties = std::map<Edge, double>;
// Route identities are immutable copied protocol values, never Python objects.
struct Token {
  enum Type { None, Number, Text, Tuple } type = None;
  double number = 0.;
  std::string text;
  std::vector<Token> tuple;
  static Token num(double n);
  static Token str(const std::string &s);
  static Token seq(const std::vector<Token> &v);
  bool operator==(const Token &o) const;
  bool operator!=(const Token &o) const {
    return !(*this == o);
  }
  bool operator<(const Token &o) const;
};
using Key = Token;
using OrderEvent = std::pair<Key, bool>;
template <class T> struct Field {
  bool present = false, null = true;
  T value{};
  bool has() const {
    return present && !null;
  }
  void set(const T &v) {
    present = true;
    null = false;
    value = v;
  }
  void clear() {
    present = true;
    null = true;
    value = T{};
  }
  void erase() {
    present = false;
    null = true;
    value = T{};
  }
  T get(const T &fallback) const {
    return has() ? value : fallback;
  }
};
struct BlockedTracker {
  Edge key;
  int count = 0;
  double first_at = 0., last_at = 0.;
  Point origin{};
};
struct BotState {
  Field<Point> last_position, recovery_start, planned_goal,
      macro_progress_position, macro_progress_target, last_target,
      macro_escape_target, controlled_shallow_target;
  Field<Key> path_key, recovery_key, request_key, request_path_key,
      macro_progress_path_key;
  Field<double> progress_time, recovery_until, planned_at, macro_progress_at,
      blocked_step_escalated_until, macro_escape_until, pending_since;
  Field<int> index, recovery, replan_generation, macro_progress_replans,
      macro_progress_index, blocked_step_replans;
  Field<bool> target_is_terminal, replan_active, macro_replan_active;
  Field<std::string> navigation_status;
  Field<BlockedTracker> blocked_step_tracker;
};
struct DirectState {
  Key path_key;
  Point target{}, position{};
  double progress_at = 0.;
  int replans = 0;
  Field<Point> escape_target;
  Field<double> escape_until;
};
struct Lease {
  double until = 0., penalty = 0.;
};
using Leases = std::map<Edge, Lease>;
struct Search {
  std::int64_t id = 0;
  int hull_revision = 0;
  double submitted_at = 0.;
  bool done = false;
  int status = 0;
  Field<double> last_frame;
  Path result;
};
struct CachedPath {
  std::shared_ptr<Path> points;
  double time = 0.;
  int hull_revision = 0;
};
// This interface is synchronous and lives only for a main-thread entry. The
// Store owns every navigation decision and all persistent route/recovery state.
// The engine adapter owns ordered terrain proofs and asynchronous search
// receipts.
class Frontier {
public:
  virtual ~Frontier() {
  }
  virtual Cell cell(const Point &) = 0;
  virtual double cell_size() const = 0;
  virtual int hull_revision() const = 0;
  virtual bool hazard(const Point &, const Point &, int) = 0;
  virtual bool hazard_cells(const Point &, const Point &, int,
                            std::vector<Cell> &) = 0;
  virtual bool segment(const Point &, const Point &) = 0;
  virtual bool dry(const Point &, const Point &, double) = 0;
  virtual double penalty(const Point &, const Point &, double) = 0;
  virtual std::vector<Edge> edges(const Point &, const Point &) = 0;
  virtual bool first_edge(const Point &, const Point &, Edge &) = 0;
  virtual bool safe_local(const Point &, const Point &, double,
                          const std::vector<Point> &, double, const Penalties &,
                          double, Point &) = 0;
  virtual bool path_penalty(const Path &, double) = 0;
  virtual bool path_hull(const Path &) = 0;
  virtual bool clearance(const Path &, int, int) = 0;
  virtual bool climb(const Point &, const Path &, int, int) = 0;
  virtual bool review(const Point &, const Point &) = 0;
  virtual bool needs_review(const Point &, const Point &) = 0;
  virtual void clear_negative() = 0;
  virtual void frame(bool) = 0;
  virtual void housekeeping(double) = 0;
  virtual Search submit(const Point &, const Point &, double, int, bool,
                        const Penalties &) = 0;
  virtual std::vector<Search> advance(double, int) = 0;
  virtual void cancel(const std::vector<std::int64_t> &) = 0;
  virtual void close() = 0;
  virtual void count(const char *, int = 1) = 0;
  virtual std::vector<Key> container_order(int,
                                           const std::vector<OrderEvent> &) = 0;
};
// Immutable map data and mutable terrain evidence have one native owner.
// The only call-out required by baked decisions is a current engine corridor
// proof. The asynchronous A* worker already owns the corresponding pure search.
struct Grid {
  double origin_x = 0., origin_z = 0., cell_size = 4., max_grade_up = .48,
         max_grade_down = .38, baked_max_grade = .30, heuristic_weight = 1.70;
  int width = 0, height = 0, hull_revision = 0;
  std::vector<double> heights_mm;
  std::vector<unsigned> links, hazards;
  Field<std::array<double, 4>> bounds;
  Field<std::vector<std::array<double, 6>>> hull_key;
  Leases failed_edges;
  std::vector<Edge> failed_order;
  Penalties static_edges;
  std::set<Cell> review_cells, review_seeds;
  struct Corridor {
    bool clear = false, known = false;
    unsigned hazards = 0;
  };
  std::map<Edge, Corridor> corridors;
  std::deque<Edge> corridor_order;
  std::map<Cell, std::array<double, 2>> local_corridors;
  using Proof = std::function<bool(const Point &, const Point &)>;
  bool installed = false, obstacle_available = false;
  void validate() const;
  Cell cell(const Point &) const;
  Point point(const Cell &, double) const;
  int flat(const Cell &) const;
  int index(const Cell &) const;
  bool nearest(const Cell &, int, Cell &) const;
  bool inside(double, double) const;
  bool ground(double, double, double &) const;
  std::vector<Cell> cells(const Point &, const Point &, bool = true) const;
  std::vector<Edge> edges(const Point &, const Point &) const;
  Corridor corridor(const Point &, const Point &);
  bool hazard(const Point &, const Point &, int);
  bool motion_hazard(const Point &, const Point &, int) const;
  bool hazard_cells(const Point &, const Point &, int,
                    std::vector<Cell> &) const;
  bool point_hazard(const Point &, int) const;
  bool hazard_near(const Point &, int) const;
  bool segment(const Point &, const Point &, const Proof &);
  bool dry(const Point &, const Point &, double, const Proof &);
  double timed(const Edge &, double);
  double penalty(const Point &, const Point &, double);
  bool path_penalty(const Path &, double);
  bool path_hull(const Path &) const;
  void prune(double);
  bool set_hulls(const std::vector<std::array<double, 6>> &);
  bool review(const Point &, const Point &);
  bool needs_review(const Point &, const Point &) const;
  int link_count(const Cell &) const;
  bool exposure(const Path &, double &) const;
  bool clearance(const Path &, int, int, double = .25) const;
  static bool climb(const Path &, int, int, double = .10, double = .30);
  static bool live_climb(const Point &, const Path &, int, int);
  double cell_penalty(const Cell &, const std::vector<Point> &, bool) const;
  bool safe_local(const Point &, const Point &, double,
                  const std::vector<Point> &, double, const Penalties &, double,
                  Point &, const Proof &);
  bool local_corridor(const Point &, std::array<double, 2> &);
  bool hull_pose(const Point &, double, double, double) const;
};
struct AsyncState {
  std::int64_t context = 0, next_job_id = 0;
  bool active = false, owns = false, closed = false;
  std::map<std::int64_t, Search> jobs;
  struct Receipt {
    Search job;
    int status = 0;
    double last_frame = 0.;
    bool has_frame = false;
  };
  std::set<std::int64_t> watched;
  std::map<std::int64_t, Receipt> settled;
  std::map<std::int64_t, std::deque<offline_navigation::Query>> queries;
  std::deque<std::int64_t> order;
  std::array<std::uint64_t, 6> stats{};
  std::int64_t submitted = 0, completed = 0, cancelled = 0, query_count = 0,
               expansions = 0;
  double worker_seconds = 0., main_seconds = 0., maximum_age = 0.;
  AsyncState() = default;
  AsyncState(const AsyncState &) = delete;
  AsyncState &operator=(const AsyncState &) = delete;
  AsyncState(AsyncState &&) noexcept;
  AsyncState &operator=(AsyncState &&) noexcept;
  ~AsyncState();
  using Count = std::function<void(const char *, int)>;
  using Proof =
      std::function<std::pair<bool, bool>(const Point &, const Point &)>;
  Search submit(const Grid &, const Point &, const Point &, double, int, bool,
                const Penalties &, const Count &);
  std::vector<Search> advance(double, int, const Proof &, const Count &);
  void cancel(const std::vector<std::int64_t> &, const Count &);
  void close();

private:
  void poll(double, std::vector<Search> &, const Count &);
};
class Store {
public:
  Grid grid;
  AsyncState async;
  std::map<Key, CachedPath> paths;
  std::map<Key, Search> searches;
  std::vector<OrderEvent> cache_order_events, search_order_events;
  std::map<int, BotState> bots;
  std::map<int, Leases> failed_edges, macro_edges;
  std::map<int, DirectState> direct;
  std::map<int, std::string> fallback_modes;
  std::map<std::string, int> fallback_totals;
  Field<double> frame_time, housekeeping_time, automatic_time;
  Field<Key> next_key;
  double credit = 0., now = 0.;
  int frame_serial = 0, processed_frame = -1, frame_budget = 96,
      max_expansions = 4096, completed = 0, failed = 0, recovered = 0;
  bool frame_open = false, installed = false, closed = false;
  Store();
  void begin_frame(Frontier &, double);
  void end_frame(Frontier &);
  void tick(Frontier &, double);
  void close(Frontier &);
  Point next_target(Frontier &, int, const Point &, Point, const Key &, double,
                    const Field<Point> &, const std::vector<Point> &,
                    const Field<double> &, bool);
  bool observe_direct(Frontier &, int, const Point &, const Point &,
                      const Key &, double, bool, Point &);
  bool blocked_step(Frontier &, int, const Point &, const Point &, double);
  bool blocked_plan(Frontier &, const Point &, const Point &);
  bool bot_penalized(Frontier &, int, const Point &, const Point &, double);
  bool controlled(Frontier &, int, const Point &, double, double, bool);
  bool terminal(int) const;
  void prune_modes(const std::vector<int> &);

private:
  static int owner(const Key &);
  static std::string kind(const Key &);
  static bool prefers(const Key &);
  static Key cache_key(Frontier &, const Key &, const Point &);
  static Key prefix(const char *, int, const Token &, const Key &);
  void accrue(double);
  void advance(Frontier &, double);
  void finish(Frontier &, const Key &, const Search &, double);
  void trim(Frontier &);
  void put_path(const Key &, CachedPath);
  void drop_path(const Key &);
  void drop_search(const Key &);
  void flush_orders(Frontier &);
  void cancel_searches(Frontier &, const std::vector<Key> &);
  void cancel_bot(Frontier &, int, const Field<Key> & = Field<Key>(),
                  const std::string & = "");
  Penalties active(std::map<int, Leases> &, int, double);
  Penalties penalties(int, double);
  bool path_edges(Frontier &, const Path &, const Penalties &);
  void fallback(int, const std::string &);
  Point pending(Frontier &, int, const Point &, const Point &, double,
                BotState &, const std::vector<Point> &, bool, bool);
  Point fallback_target(Frontier &, int, const Point &, const Point &, double,
                        BotState &, const std::vector<Point> &);
  void reset_macro(BotState &, const Point &, const Field<Point> &, double);
  bool start_macro(Frontier &, int, BotState &, const Point &, const Point &,
                   double);
  void finish_macro(Frontier &, int, BotState &);
  void observe_macro(Frontier &, int, BotState &, const Point &, const Point &,
                     double);
  std::pair<Key, std::shared_ptr<Path>>
  path(Frontier &, const Key &, const Point &, const Point &, double);
  bool planned_next(Frontier &, const Point &, const Path &, int, double, int);
  bool planned_current(Frontier &, const Point &, const Path &, int, double,
                       const Field<Point> &);
  int lookahead(Frontier &, const Point &, const Path &, int, const Key &,
                double, const Field<double> &, int);
};
} // namespace navigation
} // namespace offline_simulation
#endif
