#ifndef OFFLINE_EXPERIMENT_KERNEL_DIAGNOSTICS_H
#define OFFLINE_EXPERIMENT_KERNEL_DIAGNOSTICS_H
#include "kernel_factors.h"
#include <array>

namespace offline_kernel {
struct Diagnostics {
    Value config, records = Value::array();
    std::map<int, std::unique_ptr<Bot>> &bots;
    std::map<int, std::array<double, 3>> flips;
    double planner_age = -1;
    Diagnostics(const Value &c, std::map<int, std::unique_ptr<Bot>> &b) : config(c), bots(b) {}
    void emit(const std::string &kind, const Value &payload) {
        Value row = Value::object();
        row[sf::kind] = Value(kind);
        row["payload"] = payload;
        records.append(row);
    }
    void begin(Bot &bot, const Value &command, double throttle, double turn, bool clear,
               bool frozen, const Value &probe, double now, int grind) {
        Value &s = bot.state;
        int id = integer(s, sf::id);
        Value p = position(s);
        if (flag(config, "debug")) {
            int direction = integer(s, sf::movement_dir);
            auto at = flips.find(id);
            if (at == flips.end())
                flips[id] = {{static_cast<double>(direction), now, -10}};
            else if (direction != at->second[0]) {
                auto prior = at->second;
                at->second[0] = direction;
                at->second[1] = now;
                if (direction && prior[0] && now - prior[1] <= 2 && now - prior[2] >= 1) {
                    at->second[2] = now;
                    Value row = Value::object();
                    row[sf::id] = Value(id);
                    row["from"] = Value(static_cast<int>(prior[0]));
                    row["to"] = Value(direction);
                    row["elapsed"] = Value(now - prior[1]);
                    row[sf::position] = p;
                    row["path_clear"] = Value(clear);
                    row["probe"] = probe;
                    emit("BOT FLIP", row);
                }
            }
        }
        const Value &prior = s.get(sf::_motion_stall_log);
        bool moved = false;
        if (prior.kind == Value::Array) {
            double dx = p[0].number() - prior[0][0].number(),
                   dz = p[2].number() - prior[0][2].number();
            moved = dx * dx + dz * dz >= .25;
        }
        if (prior.kind == Value::Null || moved) {
            Value row = Value::array();
            row.append(p);
            row.append(Value(now));
            s[sf::_motion_stall_log] = row;
            return;
        }
        if (now - prior[1].number() < 3)
            return;
        Value marker = Value::array();
        marker.append(prior[0]);
        marker.append(Value(now));
        s[sf::_motion_stall_log] = marker;
        Value trace = Value::object();
        trace[sf::id] = Value(id);
        trace[sf::vehicle] = s.get(sf::vehicle);
        trace["native_motion"] = Value(flag(config, "native_motion"));
        trace["start"] = p;
        trace["speed_before"] = Value(field(s, sf::speed));
        trace["requested_throttle"] = Value(throttle);
        trace["turn"] = Value(turn);
        for (const char *key : {"yaw", "pitch", "roll"})
            trace[key] = s.get(key);
        trace["shape"] = s.get(sf::collision_shape);
        s[sf::_motion_stall_pending] = trace;
        Value row = trace.copy();
        row["command"] = command.copy();
        row["water_depth"] = Value(field(s, sf::_water_depth, -1));
        row["path_clear"] = Value(clear);
        row["probe"] = probe;
        row["frozen"] = Value(frozen);
        row[sf::hull_aiming] = Value(flag(s, sf::hull_aiming));
        row["grind"] = Value(grind);
        row["planner_age"] = Value(planner_age);
        emit("BOT STALL", row);
    }
    void finish(Bot &bot, bool support, bool rollback, const Value &settled,
                const std::set<std::pair<int, int>> &contacts) {
        Value &s = bot.state;
        Value trace = s.get(sf::_motion_stall_pending);
        if (trace.kind == Value::Null)
            return;
        s.erase(sf::_motion_stall_pending);
        Value p = position(s), nearby = Value::array(), pairs = Value::array(),
              delta = Value::array();
        int id = integer(s, sf::id);
        for (const auto &v : bots) {
            if (v.first == id)
                continue;
            const Value &other = v.second->state;
            double dx = field(other, sf::x) - p[0].number(),
                   dz = field(other, sf::z) - p[2].number();
            if (dx * dx + dz * dz > 144)
                continue;
            Value row = Value::object();
            row[sf::id] = Value(v.first);
            row[sf::position] = position(other);
            for (const char *key : {"yaw", "speed"})
                row[key] = other.get(key);
            row[sf::alive] = Value(flag(other, sf::alive, true));
            row["shape"] = other.get(sf::collision_shape);
            nearby.append(row);
        }
        for (const auto &pair : contacts) {
            if (pair.first == id || pair.second == id) {
                Value row = Value::array();
                row.append(Value(pair.first));
                row.append(Value(pair.second));
                pairs.append(row);
            }
        }
        delta.append(Value(p[0].number() - settled[0].number()));
        delta.append(Value(p[2].number() - settled[2].number()));
        trace["nearby"] = nearby;
        trace["final"] = p;
        trace["speed_final"] = Value(field(s, sf::speed));
        trace["support_rollback"] = Value(support);
        trace["pose_rollback"] = Value(rollback);
        trace[sf::airborne] = Value(flag(s, sf::airborne));
        trace["post_settle_delta"] = delta;
        trace["contact_pairs"] = pairs;
        emit("BOT MOTION", trace);
    }
};
} // namespace offline_kernel
#endif
