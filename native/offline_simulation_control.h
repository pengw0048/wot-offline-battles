#ifndef WOT_OFFLINE_SIMULATION_CONTROL_H
#define WOT_OFFLINE_SIMULATION_CONTROL_H
#include "offline_simulation_types.h"
#include <functional>
#include <map>
#include <set>
#include <vector>

namespace offline_simulation {
namespace control {
// Plain copied records only. Live descriptors, engine objects and callbacks
// remain at the main-thread frontier; none are retained by this store.
struct PoseSample {
  Point position = {};
  // x/y/z, yaw/pitch/roll, aim_yaw/turret_yaw/gun_pitch, speed, velocity XYZ.
  std::array<double, 13> values = {};
  unsigned mask = 0;
};
struct ActorConfig {
  ActorKey key;
  std::int64_t python_hash = 0;
  int team = 0, slot = 0;
  double half_length = 3.5, half_width = 1.7;
  double view_moving = 330., view_still = 330., view_delay = -1.;
  double camo_still = 0., camo_moving = 0.;
  double camo_add = 0., camo_net_add = 0., camo_multiplier = 1.,
         camo_net_multiplier = 1.;
  double camo_shot = 1., camo_net_delay = -1.;
  double radio = 0., relay = 0.;
};
struct ActorSample {
  ActorKey key;
  PoseSample pose;
  std::int64_t fire_sequence = -1;
  bool alive = true, last_effort = false, designated = false;
  bool decision_due = true, has_selected = false;
  ActorKey selected;
};
struct Detection {
  double distance = 0., view = 0., camo_still = 0., camo_moving = 0.;
  bool moving = false, fired = false;
  double additive = 0., multiplier = 1., shot_factor = 1.;
};
struct SightRequest {
  ActorKey observer, target;
  double now = 0.;
  std::int64_t fire_sequence = -1;
  Detection detection;
};
struct SightReply {
  QueryStatus status = QueryStatus::Failed;
  bool has_detection = false, detected = false;
  double foliage = 0., sampled_at = 0.;
};
using SightProbe = std::function<SightReply(const SightRequest &)>;
struct Contact {
  ActorKey target;
  bool visible = false, direct = false, fresh = false, has_pose = false;
  double remaining = 0., sampled_at = 0.;
  PoseSample pose;
};
struct Observation {
  ActorKey observer, target;
  double sampled_at = 0., duration = 0.;
  PoseSample pose;
};
struct TeamContact {
  int team = 0;
  ActorKey target;
  double remaining = 0.;
  PoseSample pose;
};
struct PairKey {
  ActorKey observer, target;
  bool operator<(const PairKey &b) const;
};
struct RadioObservation {
  double deadline = 0., sampled_at = 0.;
  PoseSample pose;
  bool direct = false;
};
struct ActorMemory {
  ActorConfig config;
  ActorSample sample;
  bool has_sample = false, still = false, source_still = false,
       fire_initialized = false;
  bool lifecycle_initialized = false, last_alive = false,
       last_alive_effort = false;
  double still_since = 0., source_still_since = 0., fire_until = 0.,
         vengeance_until = 0.;
  std::int64_t fire_sequence = -1;
  std::set<ActorKey> direct_targets;
};
struct VisibilitySample {
  double sampled_at = 0.;
  bool detected = false;
  std::int64_t fire_sequence = -1;
};
// CPython's observed-key iteration affects equal-time radio pose selection.
// Preserve its insertion/deletion/resize law using the actual client hash.
class ObserverOrder {
public:
  struct Slot {
    int state = 0;
    ActorKey key;
    std::uint64_t hash = 0;
  };

private:
  std::vector<Slot> slots_ = std::vector<Slot>(8);
  std::size_t used_ = 0, fill_ = 0;
  void insert_clean(const ActorKey &, std::uint64_t);

public:
  void add(const ActorKey &, std::int64_t);
  void erase(const ActorKey &);
  std::vector<ActorKey> keys() const;
  const std::vector<Slot> &slots() const { return slots_; }
  void restore(const std::vector<Slot> &);
};
struct DriverBody {
  std::int64_t id = 0;
  int team = 0;
  bool alive = true;
  Point position = {}, velocity = {};
  double yaw = 0., half_length = 3.5, half_width = 1.7;
  bool has_shape = false;
  double lower_y = 0., upper_y = 0.;
};
enum class RecoveryMode {
  Drive,
  Arrived,
  Avoid,
  Blocked,
  ReverseTurn,
  PivotRecovery
};
enum class TrafficMode { None, Yield, HeadOn, HeadOnBlocked };
struct DriveCommand {
  double throttle = 0., turn = 0., target_yaw = 0.;
  RecoveryMode recovery = RecoveryMode::Drive;
  TrafficMode traffic = TrafficMode::None;
  bool has_reverse_blocker = false;
  std::int64_t reverse_blocker = 0;
};
struct DriverInput {
  std::int64_t id = 0;
  int slot = 0;
  Point position = {}, target = {};
  double yaw = 0., speed = 0., dt = 0., half_length = 3.5, half_width = 1.7;
  bool movement_intent = true, stop_at_target = true,
       has_stopping_distance = false;
  double stopping_distance = 0., decision_horizon = 0., turn_speed_limit = 0.;
  std::array<double, 2> braking_key = {};
};
struct DriverState {
  int slot = 0, recovery_count = 0;
  Point last_position = {};
  double stuck_time = 0., recovery_time = 0., recovery_side = 0.;
  double steering_yaw = 0., steering_age = 999., plan_age = 999., clock = 0.;
  bool has_steering = false, avoiding = false;
  double escape_side = 0., escape_until = 0., desired_yaw = 0.,
         progress_yaw = 0.;
  double best_heading_error = 0., traffic_wait_time = 0., last_step = 0.,
         last_clear_yaw = 0.;
  bool has_desired = false, has_progress = false, has_last_clear = false,
       traffic_waiting = false;
  bool has_heading_error = false, has_traffic_wait = true;
  bool braking = false, coasting = false;
  std::array<double, 2> braking_target = {}, coast_target = {};
  std::map<int, double> failed_yaws;
};
using DirectionProbe =
    std::function<bool(double, double)>; // negative distance = unbounded
using PoseProbe = std::function<bool(double)>;
struct TrafficLease {
  bool head_on = false;
  std::array<double, 2> axis = {}, targets = {};
  std::int64_t winner = 0;
  double clear_after = 0., until = 0., blocked_until = 0.;
  bool has_blocked_until = false;
};
struct RadioActor {
  ActorKey key;
  int team = 0;
  Point position = {};
  double range = 0., relay = 0., view = 0.;
};
struct PerceptionSnapshot {
  std::map<ActorKey, ActorMemory> actors;
  std::map<ActorKey, std::map<ActorKey, RadioObservation>> observations;
  std::vector<ObserverOrder::Slot> observer_slots;
  std::map<PairKey, VisibilitySample> visibility;
  std::map<std::pair<int, ActorKey>, RadioObservation> team_spots;
  std::vector<PairKey> waiting;
  std::vector<RadioActor> radio;
  double now = 0.;
};
class Store {
  std::map<ActorKey, ActorMemory> actors_;
  std::vector<ActorKey> actor_order_;
  std::map<ActorKey, double> radio_ranges_;
  std::map<ActorKey, Point> radio_positions_;
  std::map<ActorKey, std::map<ActorKey, RadioObservation>> observations_;
  ObserverOrder observer_order_;
  std::map<PairKey, VisibilitySample> visibility_;
  std::map<std::pair<int, ActorKey>, RadioObservation> team_spots_;
  std::vector<PairKey> waiting_, order_, prior_waiting_, parked_, query_order_,
      prepare_order_;
  std::set<PairKey> allowed_, prepare_allowed_, preparing_, completed_,
      inflight_;
  std::size_t query_next_ = 0, prepare_next_ = 0;
  int budget_ = 0, prepare_budget_ = 0;
  double now_ = 0.;
  bool asynchronous_ = false, frame_open_ = false;
  TickToken token_;
  std::vector<Observation> events_;
  std::map<std::int64_t, DriverState> drivers_;
  std::map<std::pair<std::int64_t, std::int64_t>, TrafficLease> traffic_;
  std::map<std::int64_t, double> held_;
  void configure_radio();
  void backfill();
  void completed(const PairKey &);
  bool visible(const ActorKey &, const ActorKey &, const SightProbe &,
               double &);
  bool connected(const ActorKey &, const ActorKey &) const;
  void observe(const ActorKey &, const ActorKey &, double, double,
               const PoseSample &);
  void hidden(const ActorKey &, const ActorKey &);
  Contact radio_contact(const ActorKey &, const ActorKey &, double) const;

public:
  std::vector<RadioActor> radio_actors() const;
  bool radio_connected(const ActorKey &a, const ActorKey &b) const {
    return connected(a, b);
  }
  Contact recipient_contact(const ActorKey &a, const ActorKey &b,
                            double now) const {
    return radio_contact(a, b, now);
  }
  PerceptionSnapshot snapshot() const;
  void restore(const PerceptionSnapshot &);
  void note_source_stillness(const ActorKey &, double, double);
  void configure(const std::vector<ActorConfig> &);
  void update(const std::vector<ActorSample> &);
  void begin(const TickToken &, const std::vector<ActorKey> &, int, bool, bool,
             double);
  std::vector<Contact> contacts(const ActorKey &, const SightProbe &);
  void observe_humans(const SightProbe &);
  std::vector<Observation> take_observations();
  std::vector<TeamContact> team_contacts() const;
  void finish();
  void forget(const ActorKey &);
  void forget_driver(std::int64_t);
  void forget_traffic(std::int64_t);
  const std::map<std::pair<std::int64_t, std::int64_t>, TrafficLease> &
  traffic() const {
    return traffic_;
  }
  const std::map<std::int64_t, double> &held() const { return held_; }
  void restore_traffic(const std::map<std::pair<std::int64_t, std::int64_t>,
                                      TrafficLease> &pairs,
                       const std::map<std::int64_t, double> &held) {
    traffic_ = pairs;
    held_ = held;
  }
  DriverState &driver(std::int64_t, int, const Point &);
  const std::map<std::int64_t, DriverState> &drivers() const {
    return drivers_;
  }
  void remember_failure(std::int64_t, double, double = 2.);
  bool wait_for_traffic(std::int64_t, double);
  DriveCommand drive(const DriverInput &, const std::vector<DriverBody> &,
                     const DirectionProbe &, const PoseProbe &);
  DriveCommand adjust_traffic(std::int64_t, const DriverBody &,
                              const DriveCommand &,
                              const std::vector<DriverBody> &, double, bool,
                              const DirectionProbe &);
};
} // namespace control
} // namespace offline_simulation
#endif
