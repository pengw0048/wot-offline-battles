#ifndef OFFLINE_EXPERIMENT_KERNEL_LAUNCH_H
#define OFFLINE_EXPERIMENT_KERNEL_LAUNCH_H
#include "kernel_gunnery.h"
#include <numeric>

namespace offline_kernel {
inline void publish_ammo(Bot &bot) {
    Value &s = bot.state;
    s[sf::shell_index] = Value(bot.ammo.loaded);
    s[sf::next_shell_index] = Value(bot.ammo.next);
    s[sf::ammo_reload_pending] = Value(bot.ammo.reload_pending);
    Value q = Value::array();
    for (int n : bot.ammo.quantities)
        q.append(Value(n));
    s[sf::ammo_remaining] = q;
}
inline void publish_burst(Bot &bot) {
    Value &s = bot.state;
    const Burst &b = bot.burst;
    s[sf::burst_active] = Value(b.active);
    s[sf::burst_group_seq] = Value(b.group);
    s[sf::burst_count] = Value(b.count);
    s[sf::burst_next_index] = Value(b.next);
    s[sf::burst_interval] = Value(rounded(b.interval, 6));
    s[sf::burst_time_left] = Value(rounded(std::max(0.0, b.left), 6));
    s[sf::burst_shell_index] = Value(b.shell);
}
inline void publish_reload(Bot &bot, double factor) {
    bot.state[sf::clip] = Value(bot.gun.clip);
    bot.state[sf::reload_time] = Value(bot.gun.remaining(factor));
    bot.state[sf::reload_duration] = Value(bot.gun.duration(factor));
}
inline Value launch_record(const Value &s, int64_t time) {
    if (!s.has(sf::shot_yaw) || !s.has(sf::shot_pitch) || time < 0)
        return Value();
    Value row = Value::object(), pose = Value::array();
    for (const char *name : {"x", "y", "z", "yaw", "pitch", "roll"}) {
        double n = field(s, name);
        if (!std::isfinite(n))
            return Value();
        pose.append(Value(n));
    }
    for (const char *name : {"id", "fire_seq", "shell_index"})
        row[name] = Value(integer(s, name));
    for (const char *name : {"shot_yaw", "shot_pitch"})
        row[name] = s.get(name);
    std::string type = s.get(sf::profile).get(sf::class_tag).text();
    row[sf::class_tag] = Value(type);
    row[sf::burst_group_seq] = Value(integer(s, sf::burst_group_seq, integer(s, sf::fire_seq)));
    row[sf::burst_index] = Value(integer(s, sf::burst_index));
    row[sf::burst_count] = Value(integer(s, sf::burst_count, 1));
    row["launch_time_us"] = Value(time);
    row["launch_pose"] = pose;
    for (const char *name : {"shot_origin", "shells_before_shot"})
        if (s.has(name))
            row[name] = s.get(name);
    if (type == "SPG")
        for (const char *name : {"shot_velocity", "shot_gravity", "shot_max_distance",
                                 "shot_max_time_ms", "shot_proof_key"})
            if (s.has(name))
                row[name] = s.get(name);
    return row;
}
struct Launches {
    Value pending = Value::array();
    std::map<std::pair<int, int>, Value> keys;
    std::map<int, std::vector<std::pair<int, int>>> by_bot;
    bool queue(const Value &launch) {
        std::pair<int, int> key(integer(launch, sf::id), integer(launch, sf::fire_seq));
        if (key.first <= 0 || key.second <= 0)
            throw std::invalid_argument("kernel launch identity");
        auto before = keys.find(key);
        if (before != keys.end()) {
            if (before->second != launch)
                throw std::runtime_error("kernel launch identity changed");
            return false;
        }
        Value frozen = launch.copy();
        pending.append(frozen);
        keys[key] = frozen;
        by_bot[key.first].push_back(key);
        return true;
    }
    bool ack(int id, int seq) {
        std::pair<int, int> key(id, seq);
        auto at = by_bot.find(id);
        if (at == by_bot.end() || at->second.empty() || at->second[0] != key || !keys.count(key))
            return false;
        auto &rows = pending.data->array;
        auto row = std::find_if(rows.begin(), rows.end(), [&](const Value &v) {
            return integer(v, sf::id) == id && integer(v, sf::fire_seq) == seq;
        });
        if (row == rows.end())
            return false;
        rows.erase(row);
        keys.erase(key);
        at->second.erase(at->second.begin());
        if (at->second.empty())
            by_bot.erase(at);
        return true;
    }
    static bool cancel(Bot &bot, double factor) {
        Burst &b = bot.burst;
        Gun &g = bot.gun;
        if (!b.active && g.burst_remaining <= 0)
            return false;
        int launched =
            b.group > 0 ? std::max(0, integer(bot.state, sf::fire_seq) - b.group + 1) : 0;
        launched = std::min(launched, b.next);
        b.cancel(launched);
        g.cancel_burst();
        publish_ammo(bot);
        publish_reload(bot, factor);
        publish_burst(bot);
        return true;
    }
    static Value preview_angles(const Value &preview, int seq) {
        if (integer(preview, sf::fire_seq, -1) != seq)
            return Value();
        const Value &origin = preview.get("origin");
        if (origin.kind != Value::Array || origin.size() != 3 || !preview.has(sf::shot_yaw) ||
            !preview.has(sf::shot_pitch))
            return Value();
        for (const Value &n : elements(origin))
            if (n.kind == Value::Null || !std::isfinite(n.number()))
                return Value();
        double yaw = field(preview, sf::shot_yaw), pitch = field(preview, sf::shot_pitch);
        if (!std::isfinite(yaw) || !std::isfinite(pitch))
            return Value();
        Value result = Value::array();
        result.append(Value(yaw));
        result.append(Value(pitch));
        result.append(origin);
        return result;
    }
    bool commit(Bot &bot, int round, double factor, double dispersion_factor, const Subshot &edge,
                const Value &receipt, const Value &preview, int64_t time, const Value &base) {
        Value &s = bot.state;
        Gun &g = bot.gun;
        Ammo &a = bot.ammo;
        if (flag(s, sf::_drowning) || flag(s, sf::_overturned))
            return false;
        if (edge.seq != integer(s, sf::fire_seq) + 1 || edge.seq != edge.group + edge.index ||
            edge.index < 0 || edge.index >= edge.count || edge.shell != a.loaded)
            return false;
        Value angles;
        if (receipt.kind != Value::Null) {
            if (preview.kind != Value::Null || edge.count != 1 ||
                integer(receipt, sf::fire_seq, -1) != edge.seq)
                return false;
        } else if (preview.kind != Value::Null) {
            angles = preview_angles(preview, edge.seq);
            if (angles.kind == Value::Null)
                return false;
        } else if (base.kind == Value::Null &&
                   (std::abs(field(s, sf::pitch)) > 1e-12 || std::abs(field(s, sf::roll)) > 1e-12))
            return false;
        bool continuing = edge.index > 0;
        if (!a.can_fire(continuing) || !g.fire_round(edge.final))
            return false;
        if (!a.consume(continuing))
            throw std::runtime_error("kernel atomic ammunition changed");
        s[sf::shells_before_shot] =
            Value(std::accumulate(a.quantities.begin(), a.quantities.end(), 1));
        if (edge.final && a.requires_full())
            g.require_full();
        s[sf::fire_seq] = Value(edge.seq);
        s[sf::burst_group_seq] = Value(edge.group);
        s[sf::burst_index] = Value(edge.index);
        s[sf::burst_count] = Value(edge.count);
        for (const char *name : {"shot_origin", "shot_velocity", "shot_gravity",
                                 "shot_max_distance", "shot_max_time_ms", "shot_proof_key"})
            s.erase(name);
        if (receipt.kind != Value::Null) {
            for (const char *name : {"shot_yaw", "shot_pitch"})
                s[name] = receipt.get(name);
            for (const char *name :
                 {"origin", "velocity", "gravity", "max_distance", "max_time_ms", "proof_key"})
                s[std::string("shot_") + name] = receipt.get(name);
        } else if (angles.kind != Value::Null) {
            s[sf::shot_yaw] = angles[0];
            s[sf::shot_pitch] = angles[1];
            s[sf::shot_origin] = angles[2];
        } else {
            Value ray = dispersed(integer(s, sf::id), round, edge.seq, field(s, sf::aim_yaw),
                                  field(s, sf::gun_pitch),
                                  std::max(g.dispersion, g.fully_aimed * dispersion_factor),
                                  edge.index, edge.group, base);
            s[sf::shot_yaw] = ray[0];
            s[sf::shot_pitch] = ray[1];
        }
        g.shot_bloom(dispersion_factor, edge.final);
        publish_reload(bot, factor);
        publish_ammo(bot);
        Value launch = launch_record(s, time);
        if (launch.kind == Value::Null)
            throw std::runtime_error("kernel launch incomplete");
        queue(launch);
        return true;
    }
    bool fire(Bot &bot, int round, double factor, double dispersion_factor, const Value &receipt,
              const Value &preview, int64_t time, const Value &base) {
        if (flag(bot.state, sf::_drowning) || flag(bot.state, sf::_overturned) ||
            !bot.ammo.can_fire())
            return false;
        Gun &g = bot.gun;
        Ammo &a = bot.ammo;
        Burst &b = bot.burst;
        int count = std::max(0, std::min(g.burst_count, std::min(a.quantities[a.loaded], g.clip)));
        double interval = g.burst_interval;
        if (count <= 0)
            return false;
        if (receipt.kind != Value::Null) {
            count = 1;
            interval = 0;
        }
        if (!b.start(integer(bot.state, sf::fire_seq) + 1, count, interval, a.loaded) ||
            !g.begin_burst(count, factor)) {
            b.cancel(0);
            return false;
        }
        Subshot edge = b.advance(0).at(0);
        if (!commit(bot, round, factor, dispersion_factor, edge, receipt, preview, time, base)) {
            g.cancel_burst();
            b.cancel(0);
            return false;
        }
        publish_burst(bot);
        return true;
    }
};
} // namespace offline_kernel
#endif
