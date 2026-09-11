#ifndef OFFLINE_EXPERIMENT_KERNEL_TRAFFIC_H
#define OFFLINE_EXPERIMENT_KERNEL_TRAFFIC_H
#include "kernel_routes.h"
#include <functional>

namespace offline_kernel {
struct Traffic {
    using V = std::array<double, 2>;
    using Pair = std::pair<int, int>;
    struct Lease {
        bool head = false;
        int winner = 0;
        V axis{{0, 0}};
        double clear_after = 0, until = 0;
        std::map<int, double> targets;
        offline_nav::Optional<double> blocked;
    };
    std::map<Pair, Lease> pairs;
    std::map<int, double> held;
    static double dot(V a, V b) { return a[0] * b[0] + a[1] * b[1]; }
    static double cross(V a, V b) { return a[0] * b[1] - a[1] * b[0]; }
    static V sub(V a, V b) { return {{a[0] - b[0], a[1] - b[1]}}; }
    static V pos(const Value &v) {
        auto p = Routes::point(v.get(sf::position));
        return {{p.x, p.z}};
    }
    static V vel(const Value &v) {
        const Value &p = v.get(sf::velocity);
        return {{p[0].number(), p[2].number()}};
    }
    static std::array<V, 2> axes(const Value &v) {
        double s = std::sin(field(v, sf::yaw)), c = std::cos(field(v, sf::yaw));
        return {{{{c, -s}}, {{s, c}}}};
    }
    static double radius(const Value &v, V axis) {
        const Value &shape = v.get("shape");
        auto a = axes(v);
        double width = shape.kind == Value::Null ? field(v, sf::half_width) : shape[0].number(),
               length = shape.kind == Value::Null ? field(v, sf::half_length) : shape[1].number();
        return std::abs(dot(a[0], axis)) * width + std::abs(dot(a[1], axis)) * length;
    }
    static std::pair<V, double> travel(const Value &v) {
        V velocity = vel(v);
        double speed = std::hypot(velocity[0], velocity[1]);
        return speed > 1e-9 ? std::make_pair(V{{velocity[0] / speed, velocity[1] / speed}}, speed)
                            : std::make_pair(axes(v)[1], 0.0);
    }
    static bool level(const Value &a, const Value &b) {
        const Value &x = a.get("shape"), &y = b.get("shape");
        if (x.kind == Value::Null || y.kind == Value::Null)
            return true;
        double ay = a.get(sf::position)[1].number(), by = b.get(sf::position)[1].number();
        return std::min(ay + x[3].number(), by + y[3].number()) >
               std::max(ay + x[2].number(), by + y[2].number());
    }
    static offline_nav::Optional<double> contact_time(const Value &a, const Value &b) {
        V delta = sub(pos(b), pos(a)), relative = sub(vel(b), vel(a));
        double enter = 0, leave = 1;
        auto first = axes(a), second = axes(b);
        for (V axis : {first[0], first[1], second[0], second[1]}) {
            double distance = dot(delta, axis), rate = dot(relative, axis),
                   extent = radius(a, axis) + radius(b, axis);
            if (std::abs(rate) <= 1e-9) {
                if (std::abs(distance) >= extent)
                    return {};
                continue;
            }
            double start = (-extent - distance) / rate, end = (extent - distance) / rate;
            enter = std::max(enter, std::min(start, end));
            leave = std::min(leave, std::max(start, end));
            if (enter >= leave)
                return {};
        }
        return enter;
    }
    static offline_nav::Optional<V> intersection(const Value &a, const Value &b, V axis_a,
                                                 V axis_b) {
        double denominator = cross(axis_a, axis_b);
        if (std::abs(denominator) <= 1e-9)
            return {};
        V p = pos(a), delta = sub(pos(b), p);
        double distance = cross(delta, axis_b) / denominator;
        return V{{p[0] + axis_a[0] * distance, p[1] + axis_a[1] * distance}};
    }
    bool holding(const Value &v, double now) const {
        auto at = held.find(integer(v, sf::id));
        return at != held.end() && now - at->second <= 1.5;
    }
    static std::tuple<int, double, int> arrival(const Value &v, V axis, double speed, V gate,
                                                double reference) {
        double front = dot(sub(gate, pos(v)), axis) - radius(v, axis);
        if (front <= 0)
            return std::make_tuple(0, front, integer(v, sf::id));
        double pace = speed > 1e-9 ? speed : reference;
        return std::make_tuple(1,
                               pace > 1e-9 ? front / pace : std::numeric_limits<double>::infinity(),
                               integer(v, sf::id));
    }
    offline_nav::Optional<Lease> begin(const Value &a, const Value &b, double now) {
        if (dot(vel(a), axes(a)[1]) < -1e-9 || dot(vel(b), axes(b)[1]) < -1e-9)
            return {};
        auto first = travel(a), second = travel(b);
        double alignment = dot(first.first, second.first);
        if (alignment >= std::cos(.35))
            return {};
        auto contact = contact_time(a, b);
        Lease lease;
        if (alignment <= -std::cos(.60)) {
            V delta = sub(pos(b), pos(a)), side{{first.first[1], -first.first[0]}};
            double ahead = dot(delta, first.first), second_ahead = -dot(delta, second.first),
                   lateral = std::abs(dot(delta, side)), width = radius(a, side) + radius(b, side),
                   gap = ahead - radius(a, first.first) - radius(b, first.first);
            if (ahead <= 0 || second_ahead <= 0 || lateral >= width ||
                (!contact.has && gap > width * .5))
                return {};
            lease.head = true;
            lease.axis = first.first;
            lease.targets[integer(a, sf::id)] = field(a, sf::yaw) + .42;
            lease.targets[integer(b, sf::id)] = field(b, sf::yaw) + .42;
            return lease;
        }
        if (!contact.has)
            return {};
        auto gate = intersection(a, b, first.first, second.first);
        if (!gate.has)
            return {};
        double pace = std::max(first.second, second.second);
        auto rank_a = arrival(a, first.first, first.second, gate.value, holding(a, now) ? pace : 0),
             rank_b =
                 arrival(b, second.first, second.second, gate.value, holding(b, now) ? pace : 0);
        bool won = rank_a <= rank_b;
        const Value &winner = won ? a : b, &loser = won ? b : a;
        V axis = won ? first.first : second.first;
        lease.winner = integer(winner, sf::id);
        lease.axis = axis;
        lease.clear_after = dot(gate.value, axis) + radius(winner, axis) + radius(loser, axis);
        lease.until = now + 1.5;
        return lease;
    }
    static bool cleared(const Lease &lease, const Value &a, const Value &b) {
        if (!lease.head)
            return dot(pos(integer(a, sf::id) == lease.winner ? a : b), lease.axis) >
                   lease.clear_after;
        V delta = sub(pos(b), pos(a)), side{{lease.axis[1], -lease.axis[0]}};
        return dot(delta, lease.axis) <= 0 ||
               std::abs(dot(delta, side)) >= radius(a, side) + radius(b, side);
    }
    Value adjust(int id, const Value &body, const Value &command, const Value &neighbours,
                 double now, const std::function<bool(double)> &clear) {
        Value result = command.copy();
        std::string mode = command.get("combat_mode").text("route");
        if (!flag(body, sf::alive, true) || command.get("recovery_mode").text("drive") != "drive" ||
            (mode != "route" && mode != "advance") || field(command, "throttle") <= 0 ||
            dot(vel(body), axes(body)[1]) < -1e-9)
            return result;
        std::map<int, Value> peers;
        for (const Value &peer : elements(neighbours)) {
            int other = integer(peer, sf::id);
            if (other != id && flag(peer, sf::alive, true) &&
                integer(peer, sf::team) == integer(body, sf::team) && level(body, peer))
                peers[other] = peer;
        }
        for (auto at = pairs.begin(); at != pairs.end();) {
            int a = at->first.first, b = at->first.second;
            if ((a == id && !peers.count(b)) || (b == id && !peers.count(a)))
                at = pairs.erase(at);
            else
                ++at;
        }
        for (const auto &peer : peers) {
            const Value &first = id < peer.first ? body : peer.second,
                        &second = id < peer.first ? peer.second : body;
            Pair pair(integer(first, sf::id), integer(second, sf::id));
            auto at = pairs.find(pair);
            if (at != pairs.end() && cleared(at->second, first, second)) {
                pairs.erase(at);
                at = pairs.end();
            }
            if (at == pairs.end()) {
                auto next = begin(first, second, now);
                if (!next.has)
                    continue;
                at = pairs.insert({pair, next.value}).first;
            }
            Lease &lease = at->second;
            if (!lease.head) {
                if (id != lease.winner && now < lease.until) {
                    result["throttle"] = Value(0.0);
                    result["turn"] = Value(0.0);
                    result["traffic_mode"] = Value("yield");
                    held[id] = now;
                }
                continue;
            }
            if (result.get("traffic_mode").text() == "yield")
                continue;
            double target = lease.targets.at(id);
            bool okay = false;
            try {
                okay = clear(target);
            } catch (...) {
                okay = false;
            }
            if (!okay) {
                if (!lease.blocked.has)
                    lease.blocked = now + 1.5;
                if (now >= lease.blocked.value && id != lease.targets.rbegin()->first)
                    continue;
                result["throttle"] = Value(0.0);
                result["turn"] = Value(0.0);
                result["target_yaw"] = body.get(sf::yaw);
                result["traffic_mode"] = Value("head_on_blocked");
                held[id] = now;
                continue;
            }
            lease.blocked.reset();
            result["turn"] = Value(clamp(angle(target - field(body, sf::yaw)) / .58, -1, 1));
            result["target_yaw"] = Value(target);
            result["traffic_mode"] = Value("head_on");
        }
        return result;
    }
};
} // namespace offline_kernel
#endif
