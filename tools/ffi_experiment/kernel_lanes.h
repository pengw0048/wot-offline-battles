#ifndef OFFLINE_EXPERIMENT_KERNEL_LANES_H
#define OFFLINE_EXPERIMENT_KERNEL_LANES_H
#include "kernel_perception.h"
#include "kernel_engine.h"

namespace offline_kernel {
typedef std::array<int, 3> LaneKey;
struct LaneReceipt {
    double time;
    bool clear;
};
struct Lanes {
    Value config;
    Perception &perception;
    Engine &engine;
    std::map<LaneKey, LaneReceipt> samples, incoming;
    std::map<LaneKey, double> deadlines;
    std::vector<LaneKey> work;
    std::set<LaneKey> work_set;
    std::set<TeamKey> enqueued;
    std::array<std::vector<int>, 2> sources;
    double cycle = 0;
    bool has_cycle = false, has_sources = false;
    size_t cursor = 0;
    int incoming_budget = 0, completed = 0, deferred = 0, probes = 0;
    Lanes(const Value &v, Perception &p, Engine &e) : config(v), perception(p), engine(e) {}
    static LaneKey key(const Value &source, const Value &target) {
        return LaneKey{{integer(source, "id"), Perception::kind(target), Perception::id(target)}};
    }
    double phase(LaneKey key) const {
        int bucket = (std::abs(key[0]) * 31 + std::abs(key[2]) * 17 + (key[1] == 1 ? 11 : 0)) %
                     integer(config, "phases");
        return static_cast<double>(bucket) / field(config, "phases") * field(config, "refresh");
    }
    double distance_limit(const Value &source) const {
        return field(config, source.get("profile").get("class_tag").text() == "SPG" ? "spg_distance"
                                                                                    : "distance");
    }
    static double distance(const Value &source, const Value &target) {
        Value a = position(source), b = target_position(target);
        double dx = a[0].number() - b[0].number(), dz = a[2].number() - b[2].number();
        return std::sqrt(dx * dx + dz * dz);
    }
    bool enqueue(LaneKey key, double now) {
        auto at = deadlines.find(key);
        if ((at != deadlines.end() && at->second == now) || work_set.count(key))
            return false;
        work.push_back(key);
        work_set.insert(key);
        return true;
    }
    void prepare(double now, const std::vector<int> &ordered) {
        if (!has_cycle || cycle != now) {
            cycle = now;
            has_cycle = true;
            work.clear();
            work_set.clear();
            enqueued.clear();
            has_sources = false;
            cursor = 0;
        }
        std::array<std::vector<int>, 2> live;
        for (int id : ordered) {
            const Value &s = perception.bots.at(id)->state;
            int team = integer(s, "team");
            if ((team == 1 || team == 2) && flag(s, "alive", true))
                live[team - 1].push_back(id);
        }
        if (!has_sources || sources != live) {
            sources = live;
            has_sources = true;
            for (TeamKey target : enqueued)
                if (target[0] == 1 || target[0] == 2)
                    for (int id : live[target[0] - 1])
                        enqueue(LaneKey{{id, target[1], target[2]}}, now);
        }
        for (const auto &v : perception.team_visible)
            if (v.second && !enqueued.count(v.first)) {
                TeamKey target = v.first;
                if (target[0] != 1 && target[0] != 2)
                    continue;
                enqueued.insert(target);
                for (int id : live[target[0] - 1])
                    enqueue(LaneKey{{id, target[1], target[2]}}, now);
            }
    }
    Value clear(const Value &source, const Value &target, double now, bool force,
                int *budget = nullptr, const LaneKey *provided = nullptr,
                double cached_distance = -1) {
        LaneKey k = provided ? *provided : key(source, target);
        double reach = cached_distance < 0 ? distance(source, target) : cached_distance;
        if (reach > distance_limit(source)) {
            samples[k] = LaneReceipt{now, false};
            return Value(false);
        }
        auto cached = samples.find(k);
        if (!force && cached != samples.end() &&
            now - cached->second.time <= field(config, "seconds") + 1e-9)
            return Value(cached->second.clear);
        if (budget) {
            if (*budget <= 0)
                return Value();
            --*budget;
        }
        ++probes;
        auto answer = engine.query(751, source, target);
        bool clear = answer[0] != 0;
        samples[k] = LaneReceipt{now, clear};
        if (samples.size() > 1024) {
            std::vector<std::pair<double, LaneKey>> oldest;
            for (const auto &v : samples)
                oldest.push_back({v.second.time, v.first});
            std::stable_sort(oldest.begin(), oldest.end());
            for (size_t i = 0; i < 256; ++i) {
                samples.erase(oldest[i].second);
                deadlines.erase(oldest[i].second);
            }
        }
        return Value(clear);
    }
    bool refresh(const Value &source, const Value &target, double now, double cycle, int &budget,
                 LaneKey key, double reach) {
        auto deadline = deadlines.find(key);
        if (deadline != deadlines.end() && deadline->second == cycle)
            return false;
        double start = cycle - field(config, "refresh");
        auto cached = samples.find(key);
        if (cached != samples.end() && cached->second.time > start + 1e-9) {
            deadlines[key] = cycle;
            ++completed;
            return false;
        }
        if (now + 1e-9 < start + phase(key))
            return false;
        Value value = clear(source, target, now, true, &budget, &key, reach);
        if (value.kind == Value::Null) {
            ++deferred;
            return false;
        }
        deadlines[key] = cycle;
        ++completed;
        return true;
    }
    bool live(LaneKey key, Value &source, size_t &target_index, TeamKey &tk) {
        auto s = perception.bots.find(key[0]);
        if (s == perception.bots.end() || !flag(s->second->state, "alive", true))
            return false;
        source = s->second->state;
        int team = integer(source, "team");
        auto at = perception.indices.find(ActorKey{{key[1], key[2]}});
        if (at == perception.indices.end())
            return false;
        target_index = at->second;
        const Value &t = perception.roster[target_index].raw;
        if (!flag(t, "alive", true) || integer(t, "team") == team)
            return false;
        tk = TeamKey{{team, key[1], key[2]}};
        return true;
    }
    int service(double now, double cycle, const std::vector<int> &ordered,
                const std::set<int> &processed, const std::map<LaneKey, int> &priorities,
                int &budget) {
        prepare(cycle, ordered);
        if (work_set.empty())
            return 0;
        int maximum = std::max(0, budget), materialized = 0;
        double start = cycle - field(config, "refresh");
        std::set<LaneKey> attempted;
        auto attempt = [&](LaneKey key) {
            if (attempted.count(key) || !work_set.count(key))
                return;
            attempted.insert(key);
            auto completed_at = deadlines.find(key);
            if (completed_at != deadlines.end() && completed_at->second == cycle) {
                work_set.erase(key);
                return;
            }
            Value source;
            size_t index = 0;
            TeamKey tk;
            if (!live(key, source, index, tk)) {
                work_set.erase(key);
                return;
            }
            if (!perception.team_visible[tk])
                return;
            auto cached = samples.find(key);
            if (cached != samples.end() && cached->second.time > start + 1e-9) {
                deadlines[key] = cycle;
                ++completed;
                work_set.erase(key);
                return;
            }
            if (now + 1e-9 < start + phase(key))
                return;
            double reach = distance(source, perception.roster[index].raw);
            bool incoming_due = incoming_budget && flag(config, "has_incoming");
            if (reach > distance_limit(source) && !incoming_due) {
                samples[key] = LaneReceipt{now, false};
                deadlines[key] = cycle;
                ++completed;
                work_set.erase(key);
                return;
            }
            if (materialized >= maximum)
                return;
            Value target = perception.target(index, key[1] == 0 && processed.count(key[2]));
            if (key != Lanes::key(source, target)) {
                work_set.erase(key);
                return;
            }
            if (incoming_due) {
                --incoming_budget;
                ++probes;
                auto result = engine.query(752, source, target);
                if (result[0] != 0)
                    incoming[key] = LaneReceipt{now, result[1] != 0};
            }
            ++materialized;
            refresh(source, target, now, cycle, budget, key, reach);
            if (deadlines.count(key) && deadlines.at(key) == cycle)
                work_set.erase(key);
        };
        std::vector<std::pair<int, LaneKey>> selected;
        for (const auto &v : priorities)
            if (work_set.count(v.first))
                selected.push_back({v.second, v.first});
        std::sort(selected.begin(), selected.end());
        for (const auto &v : selected) {
            if (materialized >= maximum)
                break;
            attempt(v.second);
        }
        if (!work.empty()) {
            size_t start_at = cursor % work.size(), checked = 0;
            while (checked < work.size() && materialized < maximum) {
                attempt(work[(start_at + checked) % work.size()]);
                ++checked;
            }
            cursor = (start_at + checked) % work.size();
        }
        if (work.size() > std::max<size_t>(64, work_set.size() * 2)) {
            work.erase(std::remove_if(work.begin(), work.end(),
                                      [&](LaneKey k) { return !work_set.count(k); }),
                       work.end());
            cursor = work.empty() ? 0 : cursor % work.size();
        }
        int pending = 0;
        for (LaneKey key : work_set) {
            Value source;
            size_t index = 0;
            TeamKey tk;
            if (!live(key, source, index, tk) || !perception.team_visible[tk])
                continue;
            ++pending;
            if (materialized < maximum || attempted.count(key) || now + 1e-9 < start + phase(key))
                continue;
            auto cached = samples.find(key);
            if (cached != samples.end() && cached->second.time > start + 1e-9)
                continue;
            if (distance(source, perception.roster[index].raw) <= distance_limit(source))
                ++deferred;
        }
        return pending;
    }
    void merge(double now) {
        double age = field(config, "refresh") + field(config, "control") + 1e-9;
        for (const auto &v : samples) {
            auto source = perception.bots.find(v.first[0]);
            if (source == perception.bots.end() || !flag(source->second->state, "alive", true) ||
                !v.second.clear || now - v.second.time > age)
                continue;
            TeamKey key = {{integer(source->second->state, "team"), v.first[1], v.first[2]}};
            auto at = perception.observations.find(key);
            if (perception.team_visible[key] && at != perception.observations.end())
                at->second.shootable.insert(v.first[0]);
        }
    }
    Value pack(double now) {
        std::map<TeamKey, std::set<int>> threats;
        double age = field(config, "refresh") + field(config, "control");
        for (auto at = incoming.begin(); at != incoming.end();) {
            auto source = perception.bots.find(at->first[0]);
            if (source == perception.bots.end() || !flag(source->second->state, "alive", true) ||
                now - at->second.time > age) {
                at = incoming.erase(at);
                continue;
            }
            if (at->second.clear)
                threats[TeamKey{
                            {integer(source->second->state, "team"), at->first[1], at->first[2]}}]
                    .insert(at->first[0]);
            ++at;
        }
        for (const auto &v : samples) {
            LaneKey key = v.first;
            if (key[1] != 0 || !v.second.clear || now - v.second.time > age)
                continue;
            auto e = perception.bots.find(key[0]), o = perception.bots.find(key[2]);
            if (e == perception.bots.end() || o == perception.bots.end())
                continue;
            const Value &enemy = e->second->state, &own = o->second->state;
            if (!flag(enemy, "alive", true) || !flag(own, "alive", true) ||
                integer(enemy, "team") == integer(own, "team") ||
                enemy.get("profile").get("class_tag").text() == "SPG")
                continue;
            TeamKey tk = {{integer(own, "team"), 0, key[0]}};
            if (perception.observations.count(tk))
                threats[tk].insert(key[2]);
        }
        Value out = Value::array();
        for (auto &v : perception.observations) {
            TeamKey key = v.first;
            Observation &o = v.second;
            double left = perception.remaining(key, now);
            bool visible = left > 0;
            auto pose = perception.remembered.find(key);
            if (visible && pose == perception.remembered.end()) {
                visible = false;
                left = 0;
            } else if (visible)
                update(o.target, pose->second);
            bool fresh = visible && (!o.humans.empty() || !o.bots.empty());
            Value row = Value::object();
            row["observing_team"] = Value(key[0]);
            row["target_kind"] = Value(key[1] == 1 ? "human" : "bot");
            row["target_id"] = Value(key[2]);
            row["target_team"] = Value(integer(o.target, "team"));
            row["visible"] = Value(visible);
            row["fresh"] = Value(fresh);
            row["time_left"] = Value(rounded(visible ? left : 0, 6));
            auto ids = [](const std::set<int> &ids, bool allowed) {
                Value out = Value::array();
                if (allowed)
                    for (int id : ids)
                        out.append(Value(id));
                return out;
            };
            row["visible_by_player_ids"] = ids(o.humans, visible);
            row["visible_by_bot_ids"] = ids(o.bots, visible);
            row["shootable_by_bot_ids"] = ids(o.shootable, fresh);
            row["threatened_bot_ids"] = ids(threats[key], fresh);
            for (const char *name : {"x", "y", "z"})
                row[name] = Value(field(o.target, name));
            row["health"] = Value(std::max(0, integer(o.target, "health", 1)));
            row["max_health"] = Value(std::max(1, integer(o.target, "max_health", 1)));
            row["class_tag"] =
                o.target.has("class_tag")
                    ? o.target.get("class_tag")
                    : Value(o.target.get("profile").get("class_tag").text("unknown"));
            row["armor"] = Value(
                std::max(0.0, field(o.target, "armor", field(o.target.get("profile"), "armor"))));
            out.append(row);
        }
        return out;
    }
};
} // namespace offline_kernel
#endif
