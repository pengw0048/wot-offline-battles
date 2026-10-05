#include "offline_simulation_control.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <tuple>

namespace offline_simulation {
namespace control {
namespace {
constexpr double pi = 3.1415926535897932384626433832795;
double clamp(double x, double a, double b) {
  return std::max(a, std::min(b, x));
}
double angle(double a, double b) {
  double x = a - b;
  while (x > pi)
    x -= 2 * pi;
  while (x < -pi)
    x += 2 * pi;
  return x;
}
double distance(const Point &a, const Point &b) {
  double x = a[0] - b[0], z = a[2] - b[2];
  return std::sqrt(x * x + z * z);
}
bool legacy_less(const ActorKey &a, const ActorKey &b) {
  return a.kind != b.kind ? a.kind == ActorKind::Bot : a.id < b.id;
}
double camouflage(const Detection &d, double foliage) {
  double value = ((d.moving ? d.camo_moving : d.camo_still) + d.additive) *
                 std::max(0., d.multiplier);
  if (d.fired)
    value *= clamp(d.shot_factor, 0., 1.);
  return clamp(value + clamp(foliage, 0., .6), 0., .95);
}
double detect_distance(const Detection &d, double foliage) {
  double view = std::max(50., d.view);
  return clamp(view - (view - 50.) * camouflage(d, foliage), 50., 445.);
}
bool radio_link(const ActorMemory &a, double ar, const ActorMemory &b,
                double br) {
  double squared = 0.;
  for (int i = 0; i < 3; ++i) {
    double d = a.sample.pose.position[i] - b.sample.pose.position[i];
    squared += d * d;
  }
  double reach = std::max(0., ar) + std::max(0., br);
  return ar > 0. && br > 0. && squared <= reach * reach;
}
} // namespace
bool PairKey::operator<(const PairKey &b) const {
  return observer != b.observer ? legacy_less(observer, b.observer)
                                : legacy_less(target, b.target);
}
void ObserverOrder::insert_clean(const ActorKey &key, std::uint64_t hash) {
  std::size_t mask = slots_.size() - 1,
              i = static_cast<std::size_t>(hash) & mask;
  std::uint64_t perturb = hash;
  while (slots_[i].state == 1) {
    i = (i * 5 + 1 + perturb) & mask;
    perturb >>= 5;
  }
  slots_[i].state = 1;
  slots_[i].key = key;
  slots_[i].hash = hash;
  ++used_;
  ++fill_;
}
void ObserverOrder::add(const ActorKey &key, std::int64_t signed_hash) {
  std::uint64_t hash = sizeof(void *) == 4
                           ? static_cast<std::uint32_t>(signed_hash)
                           : static_cast<std::uint64_t>(signed_hash);
  std::size_t mask = slots_.size() - 1,
              i = static_cast<std::size_t>(hash) & mask, free = slots_.size();
  std::uint64_t perturb = hash;
  while (slots_[i].state) {
    if (slots_[i].state == 1 && slots_[i].key == key)
      return;
    if (slots_[i].state == 2 && free == slots_.size())
      free = i;
    i = (i * 5 + 1 + perturb) & mask;
    perturb >>= 5;
  }
  if (free != slots_.size())
    i = free;
  else
    ++fill_;
  slots_[i].state = 1;
  slots_[i].key = key;
  slots_[i].hash = hash;
  ++used_;
  if (fill_ * 3 >= slots_.size() * 2) {
    auto old = std::move(slots_);
    std::size_t n = 8;
    while (n <= used_ * 4)
      n *= 2;
    slots_ = std::vector<Slot>(n);
    used_ = fill_ = 0;
    for (const auto &slot : old)
      if (slot.state == 1)
        insert_clean(slot.key, slot.hash);
  }
}
void ObserverOrder::erase(const ActorKey &key) {
  for (auto &s : slots_)
    if (s.state == 1 && s.key == key) {
      s.state = 2;
      --used_;
      return;
    }
}
std::vector<ActorKey> ObserverOrder::keys() const {
  std::vector<ActorKey> r;
  for (const auto &s : slots_)
    if (s.state == 1)
      r.push_back(s.key);
  return r;
}
void ObserverOrder::restore(const std::vector<Slot> &slots) {
  if (slots.size() < 8 || (slots.size() & (slots.size() - 1)))
    throw std::invalid_argument("observer table size");
  std::size_t used = 0, fill = 0;
  std::set<ActorKey> seen;
  for (const auto &slot : slots) {
    if (slot.state < 0 || slot.state > 2)
      throw std::invalid_argument("observer table state");
    if (slot.state)
      ++fill;
    if (slot.state == 1) {
      if (!seen.insert(slot.key).second)
        throw std::invalid_argument("observer duplicate");
      ++used;
    }
  }
  if (fill == slots.size())
    throw std::invalid_argument("observer table full");
  slots_ = slots;
  used_ = used;
  fill_ = fill;
}
std::vector<RadioActor> Store::radio_actors() const {
  std::vector<RadioActor> result;
  for (const auto &entry : radio_ranges_) {
    const auto &a = actors_.at(entry.first);
    bool active = a.source_still && a.config.view_delay >= 0. &&
                  now_ - a.source_still_since >= a.config.view_delay;
    result.push_back({entry.first, a.config.team,
                      radio_positions_.at(entry.first), entry.second,
                      a.config.relay,
                      active ? a.config.view_still : a.config.view_moving});
  }
  return result;
}
PerceptionSnapshot Store::snapshot() const {
  if (frame_open_)
    throw std::logic_error("snapshot while control frame open");
  PerceptionSnapshot result;
  result.actors = actors_;
  result.observations = observations_;
  result.observer_slots = observer_order_.slots();
  result.visibility = visibility_;
  result.team_spots = team_spots_;
  result.waiting = waiting_;
  result.radio = radio_actors();
  result.now = now_;
  return result;
}
void Store::restore(const PerceptionSnapshot &snapshot) {
  if (frame_open_)
    throw std::logic_error("restore while control frame open");
  for (const auto &entry : snapshot.actors)
    if (!actors_.count(entry.first))
      throw std::invalid_argument("restore missing descriptor");
  ObserverOrder order;
  order.restore(snapshot.observer_slots);
  for (const auto &entry : snapshot.actors) {
    auto config = actors_.at(entry.first).config;
    actors_[entry.first] = entry.second;
    actors_[entry.first].config = config;
  }
  observations_ = snapshot.observations;
  observer_order_ = order;
  visibility_ = snapshot.visibility;
  team_spots_ = snapshot.team_spots;
  waiting_ = snapshot.waiting;
  now_ = snapshot.now;
  radio_ranges_.clear();
  radio_positions_.clear();
  for (const auto &row : snapshot.radio) {
    radio_ranges_[row.key] = row.range;
    radio_positions_[row.key] = row.position;
  }
  // Old asynchronous preparation belongs to its old authority lifetime. The
  // next fair cohort resubmits it; completed sampled observations are retained.
  inflight_.clear();
}
void Store::note_source_stillness(const ActorKey &key, double now,
                                  double speed) {
  auto &a = actors_.at(key);
  if (std::abs(speed) > .5)
    a.source_still = false;
  else if (!a.source_still) {
    a.source_still = true;
    a.source_still_since = now;
  }
}
void Store::configure(const std::vector<ActorConfig> &rows) {
  for (const auto &row : rows) {
    if (row.key.id <= 0 || row.team < 1 || row.team > 2 || row.slot < 0 ||
        row.slot > 14)
      throw std::invalid_argument("actor config");
    actors_[row.key].config = row;
  }
}
void Store::update(const std::vector<ActorSample> &rows) {
  for (const auto &row : rows) {
    auto it = actors_.find(row.key);
    if (it == actors_.end())
      throw std::invalid_argument("unconfigured actor");
    it->second.sample = row;
    it->second.has_sample = true;
  }
}
void Store::forget(const ActorKey &key) {
  actors_.erase(key);
  observations_.erase(key);
  observer_order_.erase(key);
  radio_ranges_.erase(key);
  for (auto i = visibility_.begin(); i != visibility_.end();)
    if (i->first.observer == key || i->first.target == key)
      i = visibility_.erase(i);
    else
      ++i;
  for (auto &owner : observations_)
    owner.second.erase(key);
  for (auto i = team_spots_.begin(); i != team_spots_.end();)
    if (i->first.second == key)
      i = team_spots_.erase(i);
    else
      ++i;
  if (key.kind == ActorKind::Bot) {
    forget_driver(key.id);
    forget_traffic(key.id);
  }
}
void Store::forget_driver(std::int64_t id) { drivers_.erase(id); }
void Store::forget_traffic(std::int64_t id) {
  held_.erase(id);
  for (auto i = traffic_.begin(); i != traffic_.end();)
    if (i->first.first == id || i->first.second == id)
      i = traffic_.erase(i);
    else
      ++i;
}
void Store::configure_radio() {
  radio_ranges_.clear();
  radio_positions_.clear();
  for (const auto &key : actor_order_) {
    const auto &a = actors_.at(key);
    if (a.sample.alive || now_ < a.vengeance_until) {
      radio_ranges_[key] = a.config.radio;
      radio_positions_[key] = a.sample.pose.position;
    }
  }
  auto raw = radio_ranges_;
  for (auto &entry : radio_ranges_) {
    const auto &a = actors_.at(entry.first);
    double bonus = 0.;
    for (const auto &other : raw) {
      const auto &b = actors_.at(other.first);
      if (other.first != entry.first && a.config.team == b.config.team &&
          radio_link(a, raw.at(entry.first), b, other.second))
        bonus = std::max(bonus, b.config.relay);
    }
    entry.second *= 1. + bonus;
  }
  for (auto i = observations_.begin(); i != observations_.end();) {
    if (!radio_ranges_.count(i->first)) {
      observer_order_.erase(i->first);
      i = observations_.erase(i);
      continue;
    }
    for (auto j = i->second.begin(); j != i->second.end();)
      if (j->second.deadline <= now_)
        j = i->second.erase(j);
      else
        ++j;
    ++i;
  }
}
bool Store::connected(const ActorKey &a, const ActorKey &b) const {
  if (a == b)
    return true;
  auto ar = radio_ranges_.find(a), br = radio_ranges_.find(b);
  if (ar == radio_ranges_.end() || br == radio_ranges_.end() ||
      actors_.at(a).config.team != actors_.at(b).config.team ||
      ar->second <= 0. || br->second <= 0.)
    return false;
  const auto &left = radio_positions_.at(a), &right = radio_positions_.at(b);
  double squared = 0.;
  for (int i = 0; i < 3; ++i) {
    double d = left[i] - right[i];
    squared += d * d;
  }
  double reach = ar->second + br->second;
  return squared <= reach * reach;
}
void Store::observe(const ActorKey &source, const ActorKey &target,
                    double sampled, double duration, const PoseSample &pose) {
  if (!observations_.count(source))
    observer_order_.add(source, actors_.at(source).config.python_hash);
  auto &entry = observations_[source][target];
  entry.deadline = std::max(entry.deadline, sampled + duration);
  entry.sampled_at = sampled;
  entry.pose = pose;
  entry.direct = true;
  auto &team = team_spots_[{actors_.at(source).config.team, target}];
  team.deadline = std::max(team.deadline, sampled + clamp(duration, 0., 12.));
  team.sampled_at = sampled;
  team.pose = pose;
  events_.push_back({source, target, sampled, duration, pose});
}
void Store::hidden(const ActorKey &s, const ActorKey &t) {
  auto i = observations_.find(s);
  if (i != observations_.end()) {
    auto j = i->second.find(t);
    if (j != i->second.end())
      j->second.direct = false;
  }
}
Contact Store::radio_contact(const ActorKey &s, const ActorKey &t,
                             double now) const {
  Contact result;
  result.target = t;
  double deadline = 0., fresh = 0.;
  bool has_fresh = false;
  const RadioObservation *latest = nullptr;
  for (const auto &owner : observer_order_.keys()) {
    if (!connected(s, owner))
      continue;
    auto i = observations_.find(owner);
    if (i == observations_.end())
      continue;
    auto j = i->second.find(t);
    if (j == i->second.end() || j->second.deadline <= now)
      continue;
    const auto &row = j->second;
    deadline = std::max(deadline, row.deadline);
    if (!latest || row.sampled_at > latest->sampled_at)
      latest = &row;
    if (row.direct && (!has_fresh || row.sampled_at > fresh)) {
      fresh = row.sampled_at;
      has_fresh = true;
    }
  }
  if (latest) {
    result.remaining = deadline - now;
    result.fresh = has_fresh && now - fresh <= .5 + 1e-9;
    result.has_pose = true;
    result.pose = latest->pose;
    result.sampled_at = latest->sampled_at;
    result.visible = true;
  }
  return result;
}
void Store::backfill() {
  while (static_cast<int>(allowed_.size()) < budget_ &&
         query_next_ < query_order_.size()) {
    auto p = query_order_[query_next_++];
    if (!completed_.count(p))
      allowed_.insert(p);
  }
  while (static_cast<int>(prepare_allowed_.size()) < prepare_budget_ &&
         prepare_next_ < prepare_order_.size()) {
    auto p = prepare_order_[prepare_next_++];
    if (!completed_.count(p))
      prepare_allowed_.insert(p);
  }
}
void Store::completed(const PairKey &p) {
  inflight_.erase(p);
  completed_.insert(p);
  allowed_.erase(p);
  prepare_allowed_.erase(p);
  backfill();
}
void Store::begin(const TickToken &token, const std::vector<ActorKey> &order,
                  int budget, bool asynchronous, bool humans, double now) {
  const bool continuing_frame = frame_open_;
  if (token_.generation &&
      ((token.generation != token_.generation) ||
       (token.round != token_.round) || token.sequence <= token_.sequence))
    throw std::invalid_argument("stale control token");
  token_ = token;
  now_ = now;
  actor_order_ = order;
  if (!continuing_frame) {
    budget_ = std::max(0, budget);
    prepare_budget_ = asynchronous ? budget_ : 0;
    asynchronous_ = asynchronous;
  }
  events_.clear();
  frame_open_ = true;
  for (const auto &key : order) {
    auto &a = actors_.at(key);
    if (!a.has_sample)
      throw std::invalid_argument("missing actor sample");
    if (a.lifecycle_initialized && a.last_alive && !a.sample.alive &&
        a.last_alive_effort)
      a.vengeance_until = now_ + 2.;
    if (a.sample.alive) {
      a.vengeance_until = 0.;
      a.last_alive_effort = a.sample.last_effort;
    }
    a.last_alive = a.sample.alive;
    a.lifecycle_initialized = true;
    if (now_ >= a.vengeance_until && !a.sample.alive)
      a.direct_targets.clear();
    if (a.sample.alive) {
      bool moving = std::abs(a.sample.pose.values[9]) > .5;
      if (!continuing_frame) {
        if (moving)
          a.still = false;
        else if (!a.still) {
          a.still = true;
          a.still_since = now_;
        }
      }
      if (key.kind == ActorKind::Human && humans) {
        if (moving)
          a.source_still = false;
        else if (!a.source_still) {
          a.source_still = true;
          a.source_still_since = now_;
        }
      }
    }
  }
  configure_radio();
  // A render callback owns one fair cohort and both query budgets, even
  // when banked physics or burst edges create several simulation slices.
  if (continuing_frame)
    return;
  struct Flags {
    bool human = false, selected = false, fire = false, fresh = false;
  };
  std::map<PairKey, Flags> candidates;
  std::map<PairKey, bool> valid;
  for (const auto &source : order) {
    const auto &s = actors_.at(source);
    if (!s.sample.alive)
      continue;
    for (const auto &target : order) {
      const auto &t = actors_.at(target);
      if (!t.sample.alive || s.config.team == t.config.team)
        continue;
      PairKey p{source, target};
      auto c = visibility_.find(p);
      bool fresh = c == visibility_.end(),
           fire = !fresh && c->second.fire_sequence != t.sample.fire_sequence;
      bool stale = fresh || fire || now_ - c->second.sampled_at >= 1. / 6.;
      valid[p] = stale;
      if (stale &&
          (source.kind == ActorKind::Human ? humans : s.sample.decision_due))
        candidates[p] = {source.kind == ActorKind::Human,
                         s.sample.has_selected && s.sample.selected == target,
                         fire, fresh};
    }
  }
  prior_waiting_.clear();
  parked_.clear();
  std::vector<PairKey> waiting;
  std::set<PairKey> waiting_set;
  for (const auto &p : waiting_)
    if (valid.count(p) && valid.at(p)) {
      prior_waiting_.push_back(p);
      if (candidates.count(p)) {
        waiting.push_back(p);
        waiting_set.insert(p);
      } else
        parked_.push_back(p);
    }
  std::vector<std::vector<PairKey>> cohorts(7);
  for (const auto &p : waiting)
    if (candidates.at(p).human)
      cohorts[0].push_back(p);
  for (const auto &item : candidates) {
    const auto &p = item.first;
    const auto &f = item.second;
    if (f.human) {
      if (!waiting_set.count(p))
        cohorts[1].push_back(p);
    } else if (f.selected)
      cohorts[2].push_back(p);
    else if (f.fire)
      cohorts[3].push_back(p);
    else if (f.fresh)
      cohorts[4].push_back(p);
    else if (!waiting_set.count(p))
      cohorts[6].push_back(p);
  }
  for (const auto &p : waiting) {
    const auto &f = candidates.at(p);
    if (!f.human && !f.selected && !f.fire && !f.fresh)
      cohorts[5].push_back(p);
  }
  order_.clear();
  std::set<PairKey> seen;
  for (const auto &cohort : cohorts)
    for (const auto &p : cohort)
      if (seen.insert(p).second)
        order_.push_back(p);
  for (const auto &p : waiting)
    if (seen.insert(p).second)
      order_.push_back(p);
  for (auto i = inflight_.begin(); i != inflight_.end();)
    if (!valid.count(*i))
      i = inflight_.erase(i);
    else
      ++i;
  query_order_.clear();
  prepare_order_.clear();
  for (const auto &p : order_) {
    if (asynchronous_ && !inflight_.count(p))
      prepare_order_.push_back(p);
    else
      query_order_.push_back(p);
  }
  query_next_ = prepare_next_ = 0;
  allowed_.clear();
  prepare_allowed_.clear();
  preparing_.clear();
  completed_.clear();
  backfill();
}
bool Store::visible(const ActorKey &skey, const ActorKey &tkey,
                    const SightProbe &probe, double &sampled) {
  auto &s = actors_.at(skey);
  auto &t = actors_.at(tkey);
  PairKey p{skey, tkey};
  sampled = now_;
  bool changed = false, fired = false;
  if (t.sample.fire_sequence >= 0) {
    if (!t.fire_initialized || t.sample.fire_sequence < t.fire_sequence) {
      t.fire_initialized = true;
      t.fire_sequence = t.sample.fire_sequence;
      t.fire_until = 0.;
    } else if (t.sample.fire_sequence > t.fire_sequence) {
      t.fire_sequence = t.sample.fire_sequence;
      t.fire_until = now_ + .75;
      changed = true;
    }
    fired = now_ < t.fire_until;
  }
  auto c = visibility_.find(p);
  if (!changed && c != visibility_.end() &&
      c->second.fire_sequence == t.sample.fire_sequence &&
      now_ - c->second.sampled_at < 1. / 6.) {
    sampled = asynchronous_ ? std::min(now_, c->second.sampled_at) : now_;
    return c->second.detected;
  }
  double dist = distance(s.sample.pose.position, t.sample.pose.position);
  bool value = false;
  if (dist <= 50.)
    value = true;
  else if (dist <= 445.) {
    if (!(allowed_.count(p) && budget_ > 0) &&
        !(prepare_allowed_.count(p) && prepare_budget_ > 0))
      return false;
    bool moving = std::abs(t.sample.pose.values[9]) > .5;
    if (moving)
      t.still = false;
    else if (!t.still) {
      t.still = true;
      t.still_since = now_;
    }
    bool binocular = s.source_still && s.config.view_delay >= 0. &&
                     now_ - s.source_still_since >= s.config.view_delay;
    bool net = t.still && (t.config.camo_net_delay < 0. ||
                           now_ - t.still_since >= t.config.camo_net_delay);
    Detection d{dist,
                binocular ? s.config.view_still : s.config.view_moving,
                t.config.camo_still,
                t.config.camo_moving,
                moving,
                fired,
                t.config.camo_add + (net ? t.config.camo_net_add : 0.),
                net ? t.config.camo_net_multiplier : t.config.camo_multiplier,
                t.config.camo_shot};
    if (dist <= detect_distance(d, 0.)) {
      if (prepare_allowed_.count(p) && prepare_budget_ > 0) {
        --prepare_budget_;
        preparing_.insert(p);
      } else if (allowed_.count(p) && budget_ > 0)
        --budget_;
      else
        return false;
      SightReply reply =
          probe(SightRequest{skey, tkey, now_, t.sample.fire_sequence, d});
      if (reply.status == QueryStatus::Pending) {
        inflight_.insert(p);
        if (!preparing_.count(p))
          ++budget_;
        allowed_.erase(p);
        prepare_allowed_.erase(p);
        backfill();
        return false;
      }
      if (reply.has_detection) {
        value = reply.detected;
        sampled = reply.sampled_at;
      } else
        value = reply.status == QueryStatus::Clear &&
                dist <= detect_distance(d, reply.foliage);
    }
  }
  visibility_[p] = {sampled, value, t.sample.fire_sequence};
  completed(p);
  if (visibility_.size() > 1024) {
    std::vector<std::pair<double, PairKey>> old;
    for (const auto &row : visibility_)
      old.push_back({row.second.sampled_at, row.first});
    std::stable_sort(old.begin(), old.end(), [](const auto &a, const auto &b) {
      return a.first < b.first;
    });
    for (std::size_t i = 0; i < 256; ++i)
      visibility_.erase(old[i].second);
  }
  if (!asynchronous_)
    sampled = now_;
  return value;
}
std::vector<Contact> Store::contacts(const ActorKey &source,
                                     const SightProbe &probe) {
  if (!frame_open_)
    throw std::logic_error("control frame closed");
  const auto &s = actors_.at(source);
  note_source_stillness(source, now_, s.sample.pose.values[9]);
  std::vector<Contact> result;
  // Original contact order is all humans followed by bots in roster order.
  for (int phase = 0; phase < 2; ++phase)
    for (const auto &target : actor_order_) {
      const auto &t = actors_.at(target);
      if ((target.kind == ActorKind::Human) != (phase == 0) ||
          target == source || !t.sample.alive || s.config.team == t.config.team)
        continue;
      double sampled;
      bool direct = visible(source, target, probe, sampled);
      if (direct)
        observe(source, target, sampled, 10., t.sample.pose);
      else
        hidden(source, target);
      Contact contact = radio_contact(source, target, now_);
      contact.direct = direct;
      contact.fresh = direct || contact.fresh;
      if (direct) {
        contact.visible = true;
        contact.has_pose = true;
        contact.pose = t.sample.pose;
        contact.sampled_at = sampled;
      }
      result.push_back(contact);
    }
  return result;
}
void Store::observe_humans(const SightProbe &probe) {
  for (const auto &source : actor_order_) {
    if (source.kind != ActorKind::Human)
      continue;
    auto &s = actors_.at(source);
    std::set<ActorKey> direct_targets;
    if (!s.sample.alive && now_ < s.vengeance_until)
      direct_targets = s.direct_targets;
    for (int phase = 0; phase < 2; ++phase)
      for (const auto &target : actor_order_) {
        const auto &t = actors_.at(target);
        if ((target.kind == ActorKind::Human) != (phase == 0) ||
            !t.sample.alive || s.config.team == t.config.team)
          continue;
        double sampled = now_;
        bool direct = s.sample.alive ? visible(source, target, probe, sampled)
                                     : direct_targets.count(target);
        if (!direct) {
          hidden(source, target);
          continue;
        }
        direct_targets.insert(target);
        double duration = 10.;
        if (s.sample.alive && s.sample.designated) {
          double dx = t.sample.pose.position[0] - s.sample.pose.position[0],
                 dz = t.sample.pose.position[2] - s.sample.pose.position[2];
          double gun = (s.sample.pose.mask & (1u << 6))
                           ? s.sample.pose.values[6]
                           : s.sample.pose.values[3];
          if (dx * dx + dz * dz <= .000001 ||
              std::abs(angle(std::atan2(dx, dz), gun)) <= pi / 36. + 1e-9)
            duration = 12.;
        }
        observe(source, target, sampled, duration, t.sample.pose);
      }
    if (s.sample.alive)
      s.direct_targets = direct_targets;
  }
}
std::vector<Observation> Store::take_observations() {
  auto result = std::move(events_);
  events_.clear();
  return result;
}
std::vector<TeamContact> Store::team_contacts() const {
  std::vector<TeamContact> result;
  for (const auto &row : team_spots_)
    if (row.second.deadline > now_)
      result.push_back({row.first.first, row.first.second,
                        std::min(12., row.second.deadline - now_),
                        row.second.pose});
  return result;
}
void Store::finish() {
  if (!frame_open_)
    return;
  std::set<PairKey> unfinished(parked_.begin(), parked_.end());
  for (const auto &p : order_)
    if (!completed_.count(p))
      unfinished.insert(p);
  waiting_.clear();
  std::set<PairKey> seen;
  for (const auto &p : prior_waiting_)
    if (unfinished.count(p) && seen.insert(p).second)
      waiting_.push_back(p);
  for (const auto &p : order_)
    if (unfinished.count(p) && seen.insert(p).second)
      waiting_.push_back(p);
  frame_open_ = false;
}
namespace {
int yaw_key(double yaw) {
  double x = std::fmod(yaw + pi, 2 * pi);
  if (x < 0.)
    x += 2 * pi;
  return static_cast<int>(std::floor(x * 24 / (2 * pi) + .5)) % 24;
}
double fallback_side(const DriverState &s) {
  return ((s.slot + s.recovery_count) & 1) ? 1. : -1.;
}
double failure_penalty(DriverState &s, double yaw) {
  auto i = s.failed_yaws.find(yaw_key(yaw));
  if (i == s.failed_yaws.end())
    return 0.;
  if (i->second <= s.clock) {
    s.failed_yaws.erase(i);
    return 0.;
  }
  return 3. + (i->second - s.clock) / 2.;
}
bool overlap(const Point &a, double ay, double al, double aw, const Point &b,
             double by, double bl, double bw) {
  std::array<double, 2> af{std::sin(ay), std::cos(ay)},
      as{std::cos(ay), -std::sin(ay)}, bf{std::sin(by), std::cos(by)},
      bs{std::cos(by), -std::sin(by)};
  for (const auto &axis : {af, as, bf, bs}) {
    double d = std::abs((b[0] - a[0]) * axis[0] + (b[2] - a[2]) * axis[1]);
    double ar = std::abs(af[0] * axis[0] + af[1] * axis[1]) * al +
                std::abs(as[0] * axis[0] + as[1] * axis[1]) * aw;
    double br = std::abs(bf[0] * axis[0] + bf[1] * axis[1]) * bl +
                std::abs(bs[0] * axis[0] + bs[1] * axis[1]) * bw;
    if (d > ar + br)
      return false;
  }
  return true;
}
bool sweep_blocker(const DriverInput &in, double yaw,
                   const std::vector<DriverBody> &peers, bool reverse,
                   std::int64_t &blocker) {
  double l = std::max(.5, in.half_length), w = std::max(.3, in.half_width),
         reach = l * 1.6, sign = reverse ? -1. : 1.;
  Point p = in.position;
  p[0] += sign * std::sin(yaw) * reach * .5;
  p[2] += sign * std::cos(yaw) * reach * .5;
  for (const auto &b : peers) {
    if ((!reverse && b.alive) || std::abs(b.position[1] - in.position[1]) > 5.)
      continue;
    if (overlap(p, yaw, l + reach * .5, w, b.position, b.yaw, b.half_length,
                b.half_width)) {
      blocker = b.id;
      return true;
    }
  }
  return false;
}
double recovery_side(const DriverInput &in,
                     const std::vector<DriverBody> &peers,
                     const DriverState &s) {
  int negative = 0, positive = 0;
  for (const auto &b : peers) {
    if (std::abs(b.position[1] - in.position[1]) > 5.)
      continue;
    for (double f : {.25, .5, .75, 1.}) {
      if (overlap(in.position, in.yaw - .85 * f, std::max(.5, in.half_length),
                  std::max(.3, in.half_width), b.position, b.yaw, b.half_length,
                  b.half_width))
        ++negative;
      if (overlap(in.position, in.yaw + .85 * f, std::max(.5, in.half_length),
                  std::max(.3, in.half_width), b.position, b.yaw, b.half_length,
                  b.half_width))
        ++positive;
    }
  }
  return negative < positive   ? -1.
         : positive < negative ? 1.
                               : fallback_side(s);
}
bool pivot_fits(const PoseProbe &probe, double yaw, double side) {
  if (!probe)
    return true;
  for (double f : {.25, .5, .75, 1.})
    if (!probe(yaw + side * .85 * f))
      return false;
  return true;
}
} // namespace
DriverState &Store::driver(std::int64_t id, int slot, const Point &position) {
  if (slot < 0 || slot > 14)
    throw std::invalid_argument("driver slot");
  auto it = drivers_.find(id);
  if (it == drivers_.end()) {
    DriverState s;
    s.slot = slot;
    s.last_position = position;
    it = drivers_.emplace(id, s).first;
  } else if (it->second.slot != slot)
    throw std::invalid_argument("driver slot changed");
  return it->second;
}
void Store::remember_failure(std::int64_t id, double yaw, double ttl) {
  auto i = drivers_.find(id);
  if (i == drivers_.end())
    return;
  auto &s = i->second;
  ttl = std::max(.1, ttl);
  s.failed_yaws[yaw_key(yaw)] = s.clock + ttl;
  double offset = s.has_desired ? angle(yaw, s.desired_yaw) : 0.;
  s.escape_side =
      std::abs(offset) >= .10 ? (offset > 0. ? 1. : -1.) : fallback_side(s);
  s.escape_until = s.clock + std::min(2., std::max(.8, ttl));
  s.has_steering = false;
  s.plan_age = 999.;
}
bool Store::wait_for_traffic(std::int64_t id, double elapsed) {
  auto i = drivers_.find(id);
  if (i == drivers_.end())
    return false;
  auto &s = i->second;
  s.traffic_wait_time += std::max(0., elapsed < 0. ? s.last_step : elapsed);
  s.has_traffic_wait = true;
  s.traffic_waiting = s.traffic_wait_time <= 1.5;
  if (s.traffic_waiting) {
    s.stuck_time = 0.;
    s.recovery_time = 0.;
    s.recovery_side = 0.;
  }
  return true;
}
DriveCommand Store::drive(const DriverInput &in,
                          const std::vector<DriverBody> &peers,
                          const DirectionProbe &clear, const PoseProbe &pose) {
  auto &s = driver(in.id, in.slot, in.position);
  double step = std::max(0., in.dt);
  s.traffic_waiting = false;
  s.has_traffic_wait = false;
  s.last_step = step;
  s.clock += step;
  s.steering_age += step;
  s.plan_age += step;
  for (auto i = s.failed_yaws.begin(); i != s.failed_yaws.end();)
    if (i->second <= s.clock)
      i = s.failed_yaws.erase(i);
    else
      ++i;
  double desired = std::atan2(in.target[0] - in.position[0],
                              in.target[2] - in.position[2]),
         heading = std::abs(angle(desired, in.yaw)),
         target_distance = distance(in.position, in.target);
  s.desired_yaw = desired;
  s.has_desired = true;
  DriveCommand result;
  result.target_yaw = in.yaw;
  if (!in.movement_intent || target_distance <= 1.5) {
    if (!in.movement_intent)
      s.traffic_wait_time = 0.;
    else
      s.last_position = in.position;
    s.stuck_time = s.recovery_time = s.recovery_side = 0.;
    s.has_steering = s.has_progress = s.braking = s.coasting = false;
    result.recovery = RecoveryMode::Arrived;
    return result;
  }
  double displacement = distance(in.position, s.last_position);
  bool progress = false;
  if (displacement >= .08 || !s.has_progress ||
      std::abs(angle(desired, s.progress_yaw)) > .12) {
    s.progress_yaw = desired;
    s.has_progress = true;
    s.best_heading_error = heading;
    s.has_heading_error = true;
  } else {
    double e = std::abs(angle(s.progress_yaw, in.yaw));
    if (e + .002 < s.best_heading_error) {
      s.best_heading_error = e;
      progress = true;
    }
  }
  if (displacement >= .08) {
    s.traffic_wait_time = 0.;
    s.last_position = in.position;
    s.stuck_time = 0.;
  } else if (progress) {
    s.traffic_wait_time = 0.;
    s.stuck_time = std::max(0., s.stuck_time - step);
  } else
    s.stuck_time += step;
  double phase = (((s.slot * 7) % 15) + .5) / 15.,
         threshold = 1.8 + phase * .42;
  if (s.recovery_time > 0.) {
    s.recovery_time = std::max(0., s.recovery_time - step);
    if (s.recovery_time == 0.) {
      ++s.recovery_count;
      s.recovery_side = s.stuck_time = 0.;
      s.has_progress = false;
    }
  } else if (s.stuck_time >= threshold) {
    if (s.has_last_clear)
      s.failed_yaws[yaw_key(s.last_clear_yaw)] = s.clock + 2.;
    s.recovery_time = .85 + phase * .28;
    s.recovery_side = recovery_side(in, peers, s);
  }
  if (s.recovery_time > 0.) {
    s.coasting = false;
    double side = s.recovery_side;
    if (side != 1. && side != -1.)
      s.recovery_side = side = recovery_side(in, peers, s);
    double target = in.yaw + side * .85,
           reach = std::max(.5, in.half_length) * 1.6;
    bool reverse_clear = clear(in.yaw + pi, reach);
    std::int64_t blocker = 0;
    bool blocked =
        reverse_clear && sweep_blocker(in, in.yaw, peers, true, blocker);
    if (!reverse_clear || blocked) {
      if (!pivot_fits(pose, in.yaw, side)) {
        if (pivot_fits(pose, in.yaw, -side)) {
          s.recovery_side = side = -side;
          target = in.yaw + side * .85;
        } else {
          result.recovery = RecoveryMode::Blocked;
          result.has_reverse_blocker = blocked;
          result.reverse_blocker = blocker;
          return result;
        }
      }
      result.turn = side;
      result.target_yaw = target;
      result.recovery = RecoveryMode::PivotRecovery;
      return result;
    }
    result.throttle = -.72;
    result.turn = -side;
    result.target_yaw = target;
    result.recovery = RecoveryMode::ReverseTurn;
    for (double f : {.25, .5, .75, 1.})
      if (!clear(in.yaw + pi + side * .85 * f, reach)) {
        result.turn = 0.;
        result.target_yaw = in.yaw;
        break;
      }
    return result;
  }
  double chosen = 0.;
  bool has_chosen = false;
  std::int64_t unused = 0;
  if (s.has_steering && s.plan_age < (s.avoiding ? 1.20 : .35) &&
      std::abs(angle(desired, s.steering_yaw)) < 2.15 &&
      failure_penalty(s, s.steering_yaw) <= 0. && clear(s.steering_yaw, -1.) &&
      !sweep_blocker(in, s.steering_yaw, peers, false, unused)) {
    chosen = s.steering_yaw;
    has_chosen = true;
  }
  if (!has_chosen) {
    std::vector<std::pair<double, double>> candidates;
    for (double offset : {0., .42, -.42, .78, -.78, 1.18, -1.18, 1.55, -1.55}) {
      double candidate = desired + offset,
             score = std::abs(offset) + failure_penalty(s, candidate);
      if (s.escape_until > s.clock && offset * s.escape_side < -.01)
        score += 1.25;
      candidates.push_back({score, candidate});
    }
    std::stable_sort(
        candidates.begin(), candidates.end(),
        [](const auto &a, const auto &b) { return a.first < b.first; });
    for (const auto &c : candidates)
      if (clear(c.second, -1.) &&
          !sweep_blocker(in, c.second, peers, false, unused)) {
        chosen = c.second;
        has_chosen = true;
        s.avoiding = std::abs(angle(chosen, desired)) > .05;
        break;
      }
    s.plan_age = 0.;
  }
  if (!has_chosen) {
    s.stuck_time = std::max(s.stuck_time, threshold);
    s.coasting = false;
    result.recovery = RecoveryMode::Blocked;
    return result;
  }
  s.last_clear_yaw = chosen;
  s.has_last_clear = true;
  if (!s.has_steering || std::abs(angle(chosen, s.steering_yaw)) > .04) {
    s.steering_yaw = chosen;
    s.has_steering = true;
    s.steering_age = 0.;
  }
  double delta = angle(chosen, in.yaw);
  result.turn = clamp(delta / .58, -1., 1.);
  result.throttle = 1.;
  result.target_yaw = chosen;
  result.recovery = s.avoiding ? RecoveryMode::Avoid : RecoveryMode::Drive;
  double grade =
      (in.target[1] - in.position[1]) / std::max(.1, target_distance);
  if ((grade > .10 && std::abs(delta) > .30 && !s.avoiding) ||
      std::abs(delta) > pi * .5 || (!s.avoiding && heading > pi * .5))
    result.throttle = 0.;
  if (in.stop_at_target && !s.avoiding && in.has_stopping_distance) {
    // The scalar ingress key uses the exact embedded Python decimal rounding.
    const auto &key = in.braking_key;
    if (s.braking && s.braking_target != key)
      s.braking = false;
    if (target_distance <=
        1.5 + std::max(0., in.stopping_distance) +
            std::abs(in.speed) * std::max(0., in.decision_horizon)) {
      s.braking = true;
      s.braking_target = key;
    }
    if (s.braking && s.braking_target == key) {
      if (std::abs(in.speed) <= .35 && target_distance > 2.)
        s.braking = false;
      else
        result.throttle = 0.;
    }
  } else if (!in.stop_at_target)
    s.braking = false;
  const std::array<double, 2> coast_key = {in.target[0], in.target[2]};
  if (s.avoiding || in.speed < 0. || in.turn_speed_limit <= 0. ||
      (s.coasting && s.coast_target != coast_key))
    s.coasting = false;
  if (!s.avoiding && in.speed >= 0. && in.turn_speed_limit > 0.) {
    const double dx = in.target[0] - in.position[0],
                 dz = in.target[2] - in.position[2];
    const double side = std::abs(dx * std::cos(in.yaw) - dz * std::sin(in.yaw)),
                 ahead = dx * std::sin(in.yaw) + dz * std::cos(in.yaw);
    if (ahead > 0. && side <= 1.5)
      s.coasting = false;
    else if (side > 1.5 && in.speed > 0.) {
      const double radius = in.speed / in.turn_speed_limit;
      if (radius > 1.5 && std::hypot(side - radius, ahead) < radius - 1.5) {
        s.coasting = true;
        s.coast_target = coast_key;
      }
    }
    if (s.coasting)
      result.throttle = 0.;
  }
  return result;
}
namespace {
using V2 = std::array<double, 2>;
double dot(const V2 &a, const V2 &b) { return a[0] * b[0] + a[1] * b[1]; }
double cross(const V2 &a, const V2 &b) { return a[0] * b[1] - a[1] * b[0]; }
V2 pos(const DriverBody &b) { return {b.position[0], b.position[2]}; }
V2 vel(const DriverBody &b) { return {b.velocity[0], b.velocity[2]}; }
V2 forward(const DriverBody &b) { return {std::sin(b.yaw), std::cos(b.yaw)}; }
V2 side(const DriverBody &b) { return {std::cos(b.yaw), -std::sin(b.yaw)}; }
double radius(const DriverBody &b, const V2 &a) {
  return std::abs(dot(side(b), a)) * b.half_width +
         std::abs(dot(forward(b), a)) * b.half_length;
}
std::pair<V2, double> travel(const DriverBody &b) {
  V2 v = vel(b);
  double speed = std::hypot(v[0], v[1]);
  return {speed > 1e-9 ? V2{v[0] / speed, v[1] / speed} : forward(b), speed};
}
bool same_level(const DriverBody &a, const DriverBody &b) {
  return !a.has_shape || !b.has_shape ||
         std::min(a.position[1] + a.upper_y, b.position[1] + b.upper_y) >
             std::max(a.position[1] + a.lower_y, b.position[1] + b.lower_y);
}
bool contact_time(const DriverBody &a, const DriverBody &b) {
  V2 d{b.position[0] - a.position[0], b.position[2] - a.position[2]},
      v{b.velocity[0] - a.velocity[0], b.velocity[2] - a.velocity[2]};
  double enter = 0., leave = 1.;
  for (const auto &axis : {side(a), forward(a), side(b), forward(b)}) {
    double distance = dot(d, axis), rate = dot(v, axis),
           r = radius(a, axis) + radius(b, axis);
    if (std::abs(rate) <= 1e-9) {
      if (std::abs(distance) >= r)
        return false;
      continue;
    }
    double start = (-r - distance) / rate, end = (r - distance) / rate;
    enter = std::max(enter, std::min(start, end));
    leave = std::min(leave, std::max(start, end));
    if (enter >= leave)
      return false;
  }
  return true;
}
std::tuple<int, double, std::int64_t> arrival(const DriverBody &b,
                                              const V2 &axis, double speed,
                                              const V2 &gate,
                                              double reference) {
  V2 d{gate[0] - b.position[0], gate[1] - b.position[2]};
  double f = dot(d, axis) - radius(b, axis);
  if (f <= 0.)
    return {0, f, b.id};
  double pace = speed > 1e-9 ? speed : reference;
  return {1, pace > 1e-9 ? f / pace : std::numeric_limits<double>::infinity(),
          b.id};
}
bool traffic_begin(const DriverBody &a, const DriverBody &b, double now,
                   const std::map<std::int64_t, double> &held,
                   TrafficLease &lease) {
  if (dot(vel(a), forward(a)) < -1e-9 || dot(vel(b), forward(b)) < -1e-9)
    return false;
  auto at = travel(a), bt = travel(b);
  double alignment = dot(at.first, bt.first);
  if (alignment >= std::cos(.35))
    return false;
  bool contact = contact_time(a, b);
  V2 delta{b.position[0] - a.position[0], b.position[2] - a.position[2]};
  if (alignment <= -std::cos(.60)) {
    double ahead = dot(delta, at.first), second_ahead = -dot(delta, bt.first);
    V2 lateral{at.first[1], -at.first[0]};
    double width = radius(a, lateral) + radius(b, lateral),
           gap = ahead - radius(a, at.first) - radius(b, at.first);
    if (ahead <= 0. || second_ahead <= 0. ||
        std::abs(dot(delta, lateral)) >= width ||
        (!contact && gap > width * .5))
      return false;
    lease.head_on = true;
    lease.axis = at.first;
    lease.targets = {a.yaw + .42, b.yaw + .42};
    return true;
  }
  if (!contact)
    return false;
  double denom = cross(at.first, bt.first);
  if (std::abs(denom) <= 1e-9)
    return false;
  double d = cross(delta, bt.first) / denom;
  V2 gate{a.position[0] + at.first[0] * d, a.position[2] + at.first[1] * d};
  double pace = std::max(at.second, bt.second);
  auto holding = [&](std::int64_t id) {
    auto i = held.find(id);
    return i != held.end() && now - i->second <= 1.5;
  };
  bool a_wins =
      arrival(a, at.first, at.second, gate, holding(a.id) ? pace : 0.) <
      arrival(b, bt.first, bt.second, gate, holding(b.id) ? pace : 0.);
  const auto &w = a_wins ? a : b;
  const auto &l = a_wins ? b : a;
  lease.winner = w.id;
  lease.axis = a_wins ? at.first : bt.first;
  lease.clear_after =
      dot(gate, lease.axis) + radius(w, lease.axis) + radius(l, lease.axis);
  lease.until = now + 1.5;
  return true;
}
bool cleared(const TrafficLease &lease, const DriverBody &a,
             const DriverBody &b) {
  if (!lease.head_on)
    return dot(pos(a.id == lease.winner ? a : b), lease.axis) >
           lease.clear_after;
  V2 delta{b.position[0] - a.position[0], b.position[2] - a.position[2]},
      lateral{lease.axis[1], -lease.axis[0]};
  return dot(delta, lease.axis) <= 0. ||
         std::abs(dot(delta, lateral)) >=
             radius(a, lateral) + radius(b, lateral);
}
} // namespace
DriveCommand Store::adjust_traffic(std::int64_t id, const DriverBody &body,
                                   const DriveCommand &command,
                                   const std::vector<DriverBody> &neighbours,
                                   double now, bool route,
                                   const DirectionProbe &clear) {
  DriveCommand result = command;
  if (!body.alive || command.recovery != RecoveryMode::Drive || !route ||
      command.throttle <= 0. || dot(vel(body), forward(body)) < -1e-9)
    return result;
  std::map<std::int64_t, DriverBody> peers;
  for (const auto &peer : neighbours)
    if (peer.id != id && peer.alive && peer.team == body.team &&
        same_level(body, peer))
      peers[peer.id] = peer;
  for (auto i = traffic_.begin(); i != traffic_.end();)
    if ((i->first.first == id && !peers.count(i->first.second)) ||
        (i->first.second == id && !peers.count(i->first.first)))
      i = traffic_.erase(i);
    else
      ++i;
  for (const auto &entry : peers) {
    const auto &peer = entry.second;
    const auto &first = id < peer.id ? body : peer;
    const auto &second = id < peer.id ? peer : body;
    auto pair = std::make_pair(first.id, second.id);
    auto it = traffic_.find(pair);
    if (it != traffic_.end() && cleared(it->second, first, second)) {
      traffic_.erase(it);
      it = traffic_.end();
    }
    if (it == traffic_.end()) {
      TrafficLease lease;
      if (!traffic_begin(first, second, now, held_, lease))
        continue;
      it = traffic_.emplace(pair, lease).first;
    }
    auto &lease = it->second;
    if (!lease.head_on) {
      if (id != lease.winner && now < lease.until) {
        result.throttle = result.turn = 0.;
        result.traffic = TrafficMode::Yield;
        held_[id] = now;
      }
      continue;
    }
    if (result.traffic == TrafficMode::Yield)
      continue;
    double target = lease.targets[id == first.id ? 0 : 1];
    if (!clear(target, -1.)) {
      if (!lease.has_blocked_until) {
        lease.blocked_until = now + 1.5;
        lease.has_blocked_until = true;
      }
      if (now >= lease.blocked_until && id != second.id)
        continue;
      result.throttle = result.turn = 0.;
      result.target_yaw = body.yaw;
      result.traffic = TrafficMode::HeadOnBlocked;
      held_[id] = now;
      continue;
    }
    double delta = std::fmod(target - body.yaw + pi, 2 * pi);
    if (delta < 0.)
      delta += 2 * pi;
    delta -= pi;
    result.turn = clamp(delta / .58, -1., 1.);
    result.target_yaw = target;
    result.traffic = TrafficMode::HeadOn;
  }
  return result;
}
} // namespace control
} // namespace offline_simulation
