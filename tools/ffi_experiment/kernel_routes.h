#ifndef OFFLINE_EXPERIMENT_KERNEL_ROUTES_H
#define OFFLINE_EXPERIMENT_KERNEL_ROUTES_H
#include "kernel_motion.h"
#include "navigation_state.h"
#include <tuple>

namespace offline_kernel {
inline Value tuple_value(std::initializer_list<Value> values) {
    Value v = Value::array();
    for (const Value &item : values)
        v.append(item);
    return v;
}
inline std::string python_key(const Value &v) {
    if (v.kind == Value::Null)
        return "None";
    if (v.kind == Value::Boolean)
        return v.truth() ? "True" : "False";
    if (v.kind == Value::String) {
        std::string out = "'";
        for (unsigned char c : v.text()) {
            if (c == '\\' || c == '\'')
                out += '\\';
            out += c;
        }
        return out + "'";
    }
    if (v.kind == Value::Array) {
        std::string out = "(";
        for (size_t i = 0; i < v.size(); ++i) {
            if (i)
                out += ", ";
            out += python_key(v[i]);
        }
        if (v.size() == 1)
            out += ",";
        return out + ")";
    }
    return json(v);
}
struct Routes {
    using Point = offline_nav::Point;
    using Pair = std::pair<double, double>;
    std::map<int, std::unique_ptr<Bot>> &bots;
    std::map<int, Value> &orders;
    offline_nav::Navigator *nav;
    Value config;
    explicit Routes(const Value &c, std::map<int, std::unique_ptr<Bot>> &b, std::map<int, Value> &o)
        : bots(b), orders(o),
          nav(integer(c, "navigation") ? &offline_runtime_navigation(integer(c, "navigation"))
                                       : nullptr),
          config(c) {}
    static Value value(Point p) { return vector3(p.x, p.y, p.z); }
    static Point point(const Value &v, Point fallback = Point()) {
        if (v.kind == Value::Object)
            return Point(field(v, "x", fallback.x), field(v, "y", fallback.y),
                         field(v, "z", fallback.z));
        return v.kind == Value::Array && v.size() == 3
                   ? Point(v[0].number(), v[1].number(), v[2].number())
                   : fallback;
    }
    static std::vector<double> lanes(double offset) {
        if (offset >= 7.5)
            return {10, 5, 0};
        if (offset <= -7.5)
            return {-10, -5, 0};
        if (offset > 0)
            return {5, 0};
        if (offset < 0)
            return {-5, 0};
        return {0};
    }
    static std::vector<double> rows(double offset) {
        return std::abs(offset) < 1e-9 ? std::vector<double>{0}
                                       : std::vector<double>{offset, offset * .5, 0};
    }
    static std::vector<double> layout(int n) {
        if (n <= 1)
            return {0};
        if (n == 2)
            return {-5, 5};
        if (n == 3)
            return {-5, 0, 5};
        if (n == 4)
            return {-10, -5, 5, 10};
        std::vector<double> out;
        for (int i = 0; i < n; ++i)
            out.push_back(-10 + 5 * ((2 * i + 1) * 5 / (2 * n)));
        return out;
    }
    static std::vector<double> row_layout(int n) {
        std::vector<double> out;
        if (n <= 1)
            return {0};
        int count = std::min(5, n);
        for (int i = 0; i < n; ++i) {
            int row =
                n == count
                    ? i
                    : static_cast<int>(rounded(i * static_cast<double>(count - 1) / (n - 1), 0));
            out.push_back(-4 + 8.0 * row / (count - 1));
        }
        return out;
    }
    static bool group_matches(const Value &s, const Value &group) {
        return s.get("_route_lane_group") == group;
    }
    std::vector<Value *> cohort(int id, const Value &group, const Value &gate) {
        std::vector<Value *> out;
        for (const auto &v : bots) {
            Value &s = v.second->state;
            if (!flag(s, "alive", true) || integer(s, "team") != group[0].exact())
                continue;
            Value route_id, peer_gate;
            const Value &prior = s.get("_route_lane_gate");
            if (group_matches(s, group) && prior.kind == Value::Array && prior.size() == 2) {
                route_id = group[1];
                peer_gate = prior;
            } else {
                auto order = orders.find(v.first);
                if (order != orders.end()) {
                    route_id = order->second.get("route_id");
                    if (order->second.get("route_index").kind != Value::Null &&
                        order->second.has("route_join"))
                        peer_gate = tuple_value({Value(integer(order->second, "route_index")),
                                                 Value(flag(order->second, "route_join"))});
                } else if (!orders.empty())
                    continue;
            }
            if (route_id.kind == Value::Null && orders.empty()) {
                route_id = s.get("route").get("id");
                if (s.get("route_index").kind != Value::Null &&
                    s.get("route_join").kind != Value::Null)
                    peer_gate = tuple_value({s.get("route_index"), Value(flag(s, "route_join"))});
                else if (v.first == id)
                    peer_gate = gate;
            }
            if (route_id.text() != group[1].text() || peer_gate != gate)
                continue;
            out.push_back(&s);
        }
        Value *own = &bots.at(id)->state;
        if (std::find(out.begin(), out.end(), own) == out.end())
            out.push_back(own);
        std::sort(out.begin(), out.end(), [](const Value *a, const Value *b) {
            return std::make_pair(integer(*a, "slot"), integer(*a, "id")) <
                   std::make_pair(integer(*b, "slot"), integer(*b, "id"));
        });
        return out;
    }
    static Pair waypoint(const Value &v) {
        return v.kind == Value::Object ? Pair(field(v, "x"), field(v, "z"))
                                       : Pair(v[0].number(), v[1].number());
    }
    static Pair forward(const Value &s, Point goal, const Value &order) {
        const Value &route = s.get("route"), &points = route.get("waypoints");
        int index = integer(order, "route_index");
        if (route.get("id").kind != Value::Null &&
            route.get("id").text() == order.get("route_id").text("direct") && points.size() > 1) {
            int a = index > 0 && index < static_cast<int>(points.size()) ? index - 1 : 0, b = a + 1;
            Pair first = waypoint(points[a]), second = waypoint(points[b]);
            double dx = second.first - first.first, dz = second.second - first.second,
                   length = std::hypot(dx, dz);
            if (length >= .25)
                return {dx / length, dz / length};
        }
        Point anchor = point(order.get("route_anchor"), point(s));
        double dx = goal.x - anchor.x, dz = goal.z - anchor.z, length = std::hypot(dx, dz);
        return length < .25 ? Pair(std::sin(field(s, "yaw")), std::cos(field(s, "yaw")))
                            : Pair(dx / length, dz / length);
    }
    static void clear_lane(Value &s) {
        for (const char *name :
             {"_route_lane_segment", "_route_lane_offset", "_route_lane_row", "_route_lane_goal"})
            s.erase(name);
    }
    Pair binding(int id, const Value &group, Point goal, const Value &order) {
        Value &s = bots.at(id)->state;
        Value gate;
        auto at = orders.find(id);
        if (at != orders.end() && at->second.get("route_id").text() == group[1].text() &&
            at->second.get("route_index").kind != Value::Null && at->second.has("route_join"))
            gate = tuple_value(
                {Value(integer(at->second, "route_index")), Value(flag(at->second, "route_join"))});
        else
            gate = tuple_value(
                {Value(integer(order, "route_index")), Value(flag(order, "route_join"))});
        if (group_matches(s, group)) {
            if (gate[1].truth() && s.get("_route_lane_gate") != gate) {
                s["_route_lane_gate"] = gate;
                Pair f = forward(s, goal, order);
                s["_route_lane_forward"] = tuple_value({Value(f.first), Value(f.second)});
                clear_lane(s);
            }
            return {field(s, "_route_lane_desired"), field(s, "_route_lane_row_desired")};
        }
        std::vector<Value *> peers = cohort(id, group, gate);
        for (Value *peer : peers)
            if (!group_matches(*peer, group) || peer->get("_route_lane_gate") != gate ||
                peer->get("_route_lane_origin").kind == Value::Null)
                (*peer)["_route_lane_origin"] = position(*peer);
        Pair f = forward(s, goal, order);
        auto rank = [&](Value *peer, bool lateral) {
            const Value &p = peer->get("_route_lane_origin");
            return std::make_tuple(p[0].number() * (lateral ? -f.second : f.first) +
                                       p[2].number() * (lateral ? f.first : f.second),
                                   integer(*peer, "slot"), integer(*peer, "id"));
        };
        std::vector<Value *> ordered = peers;
        std::sort(ordered.begin(), ordered.end(),
                  [&](Value *a, Value *b) { return rank(a, true) < rank(b, true); });
        std::vector<double> pattern = layout(ordered.size());
        std::map<double, std::vector<Value *>> members;
        for (size_t i = 0; i < ordered.size(); ++i)
            members[pattern[i]].push_back(ordered[i]);
        std::map<int, Pair> assigned;
        for (auto &lane : members) {
            auto &values = lane.second;
            std::sort(values.begin(), values.end(),
                      [&](Value *a, Value *b) { return rank(a, false) < rank(b, false); });
            auto pattern = row_layout(values.size());
            for (size_t i = 0; i < values.size(); ++i)
                assigned[integer(*values[i], "id")] = Pair(lane.first, pattern[i]);
        }
        std::set<Pair> occupied;
        for (Value *peer : peers)
            if (group_matches(*peer, group) && peer->get("_route_lane_gate") == gate)
                occupied.insert(
                    {field(*peer, "_route_lane_desired"), field(*peer, "_route_lane_row_desired")});
        if (!occupied.empty())
            for (Value *peer : ordered) {
                if (group_matches(*peer, group))
                    continue;
                int peer_id = integer(*peer, "id");
                Pair expected = assigned[peer_id];
                std::vector<Pair> available;
                for (double lane : {-10, -5, 0, 5, 10}) {
                    bool used = false;
                    for (Pair p : occupied)
                        used = used || p.first == lane;
                    if (!used)
                        available.push_back({lane, 0});
                }
                bool reuse = available.empty();
                if (reuse)
                    for (double lane : {-10, -5, 0, 5, 10})
                        for (double row : {-4, -2, 0, 2, 4})
                            if (!occupied.count({lane, row}))
                                available.push_back({lane, row});
                auto score = [&](Pair p) {
                    double nearest = 1e100;
                    for (Pair used : occupied)
                        nearest = std::min(nearest,
                                           (p.first - used.first) * (p.first - used.first) +
                                               (p.second - used.second) * (p.second - used.second));
                    double dx = p.first - expected.first, dz = p.second - expected.second;
                    return std::make_tuple(reuse ? -nearest : 0, dx * dx + dz * dz, std::abs(dx),
                                           std::abs(dz), p.first, p.second);
                };
                Pair selected =
                    available.empty()
                        ? Pair(0, 0)
                        : *std::min_element(available.begin(), available.end(),
                                            [&](Pair a, Pair b) { return score(a) < score(b); });
                assigned[peer_id] = selected;
                occupied.insert(selected);
            }
        for (Value *peer : peers) {
            if (group_matches(*peer, group))
                continue;
            Pair choice = assigned[integer(*peer, "id")];
            (*peer)["_route_lane_group"] = group;
            (*peer)["_route_lane_gate"] = gate;
            (*peer)["_route_lane_desired"] = Value(choice.first);
            (*peer)["_route_lane_row_desired"] = Value(choice.second);
            (*peer)["_route_lane_forward"] = tuple_value({Value(f.first), Value(f.second)});
            clear_lane(*peer);
        }
        return {field(s, "_route_lane_desired"), field(s, "_route_lane_row_desired")};
    }
    static offline_nav::Optional<Pair> leg(const Value &s, Point current, Point goal,
                                           const Value &order, bool joining) {
        if (joining) {
            const Value &v = s.get("_route_lane_forward");
            if (v.size() == 2) {
                double length = std::hypot(v[0].number(), v[1].number());
                if (length >= .25)
                    return Pair(v[0].number() / length, v[1].number() / length);
            }
        }
        Point anchor = point(order.get("route_anchor"), current);
        double dx = goal.x - anchor.x, dz = goal.z - anchor.z, length = std::hypot(dx, dz);
        if (length < .25) {
            dx = goal.x - current.x;
            dz = goal.z - current.z;
            length = std::hypot(dx, dz);
        }
        return length < .25 ? offline_nav::Optional<Pair>()
                            : offline_nav::Optional<Pair>(Pair(dx / length, dz / length));
    }
    bool within(const Value &s, Point p, double yaw) const {
        if (!nav || !nav->grid->bounded)
            return false;
        auto &b = nav->grid->bounds;
        double sine = std::abs(std::sin(yaw)), cosine = std::abs(std::cos(yaw)),
               length = std::max(.5, field(s, "half_length", 3.5)),
               width = std::max(.3, field(s, "half_width", 1.7)),
               x = cosine * width + sine * length, z = sine * width + cosine * length;
        return b[0] + x - p.x <= 1e-6 && p.x + x - b[2] <= 1e-6 && b[1] + z - p.z <= 1e-6 &&
               p.z + z - b[3] <= 1e-6;
    }
    offline_nav::Optional<Point> safe_goal(const Value &s, Point goal, Pair forward, double lateral,
                                           double row, double now) {
        if (std::abs(lateral) < 1e-9 && std::abs(row) < 1e-9)
            return goal;
        if (!nav)
            return {};
        Point candidate(goal.x - forward.second * lateral + forward.first * row, goal.y,
                        goal.z + forward.first * lateral + forward.second * row);
        if (std::hypot(candidate.x - goal.x, candidate.z - goal.z) + 1.5 > 13 + 1e-9)
            return {};
        auto &g = *nav->grid;
        if (!g.ground(candidate, candidate.y) || g.point_hazard(goal, 7) ||
            g.point_hazard(candidate, 7) || g.hazard(goal, candidate, 7) ||
            !g.dry(goal, candidate, now) ||
            !within(s, candidate, std::atan2(forward.first, forward.second)))
            return {};
        return candidate;
    }
    std::pair<Point, Pair> lane_goal(int id, Point current, Point goal, const Value &order,
                                     double now, bool joining) {
        Value &s = bots.at(id)->state;
        Value group =
            tuple_value({Value(integer(s, "team")), Value(order.get("route_id").text("direct"))});
        Pair desired = binding(id, group, goal, order);
        Value segment =
            tuple_value({group[0], group[1], Value(integer(order, "route_index")), Value(joining)});
        if (s.get("_route_lane_segment") != segment) {
            s["_route_lane_segment"] = segment;
            s["_route_lane_offset"] = Value(desired.first);
            s["_route_lane_row"] = Value(joining ? desired.second : 0);
            s.erase("_route_lane_goal");
        }
        auto f = leg(s, current, goal, order, joining);
        auto fallback = [&]() {
            s["_route_lane_offset"] = Value(0.0);
            s["_route_lane_row"] = Value(0.0);
            s["_route_lane_goal"] = value(goal);
            return std::make_pair(goal, Pair(0, 0));
        };
        if (!f.has)
            return fallback();
        bool reject = false;
        if (nav) {
            auto at = nav->states.find(id);
            if (at != nav->states.end() && at->second.status == 2 &&
                s.get("_route_lane_goal").kind != Value::Null && at->second.planned_goal.has)
                reject = offline_nav::distance(point(s.get("_route_lane_goal")),
                                               at->second.planned_goal.value) < .25;
        }
        bool first = true;
        std::set<Pair> seen;
        for (double row : rows(field(s, "_route_lane_row")))
            for (double lane : lanes(field(s, "_route_lane_offset"))) {
                Pair identity(rounded(lane, 6), rounded(row, 6));
                if (!seen.insert(identity).second)
                    continue;
                if (reject && first) {
                    first = false;
                    continue;
                }
                first = false;
                auto candidate = safe_goal(s, goal, f.value, lane, row, now);
                if (!candidate.has)
                    continue;
                s["_route_lane_offset"] = Value(lane);
                s["_route_lane_row"] = Value(row);
                s["_route_lane_goal"] = value(candidate.value);
                return {candidate.value, Pair(rounded(lane * 1000, 0), rounded(row * 1000, 0))};
            }
        return fallback();
    }
    Point lane_target(int id, Point current, Point goal, Point selected, const Value &order,
                      double now) {
        Value &s = bots.at(id)->state;
        double dx = selected.x - current.x, dz = selected.z - current.z;
        if (!nav || std::hypot(dx, dz) < .25)
            return selected;
        Value group =
            tuple_value({Value(integer(s, "team")), Value(order.get("route_id").text("direct"))});
        Pair desired = binding(id, group, goal, order);
        Value segment = tuple_value({group[0], group[1], Value(integer(order, "route_index"))});
        if (s.get("_route_lane_segment") != segment) {
            s["_route_lane_segment"] = segment;
            s["_route_lane_offset"] = Value(desired.first);
        }
        auto at = nav->states.find(id);
        if (at != nav->states.end() && at->second.shallow.has) {
            s["_route_lane_offset"] = Value(0.0);
            return selected;
        }
        Point anchor = point(order.get("route_anchor"), current);
        double route_dx = goal.x - anchor.x, route_dz = goal.z - anchor.z,
               length = std::hypot(route_dx, route_dz);
        if (length < .25) {
            route_dx = dx;
            route_dz = dz;
            length = std::hypot(dx, dz);
        }
        if (length < .25)
            return selected;
        for (double offset : lanes(field(s, "_route_lane_offset"))) {
            if (std::abs(offset) < 1e-9)
                break;
            Point candidate(selected.x - route_dz / length * offset, selected.y,
                            selected.z + route_dx / length * offset);
            double cx = candidate.x - current.x, cz = candidate.z - current.z;
            if (std::hypot(cx, cz) <= 1.5 + 1e-9 || dx * cx + dz * cz <= 1e-9)
                continue;
            auto &g = *nav->grid;
            if (!g.ground(candidate, candidate.y) || nav->penalized(id, current, candidate, now) ||
                g.point_hazard(candidate, 7) || g.hazard(current, candidate, 7) ||
                !g.dry(current, candidate, now) || !within(s, candidate, std::atan2(cx, cz)))
                continue;
            s["_route_lane_offset"] = Value(offset);
            return candidate;
        }
        s["_route_lane_offset"] = Value(0.0);
        return selected;
    }
    offline_nav::Optional<Point> radio_goal(int id, Point current, Point goal, const Value &order) {
        const Value &area = order.get("move_area_bounds");
        if (!nav || area.size() != 4)
            return {};
        Value key = tuple_value({order.get("team_command_id"), area});
        Value &s = bots.at(id)->state;
        const Value &cached = s.get("_radio_ground_goal");
        if (cached.kind == Value::Array && cached.size() == 2 && cached[0] == key)
            return cached[1].kind == Value::Null ? offline_nav::Optional<Point>()
                                                 : offline_nav::Optional<Point>(point(cached[1]));
        auto &g = *nav->grid;
        auto low = g.cell(Point(area[0].number(), current.y, area[1].number())),
             high = g.cell(Point(area[2].number(), current.y, area[3].number()));
        offline_nav::Optional<Point> best;
        std::tuple<bool, double, offline_nav::Cell> score;
        for (int z = low.second; z <= high.second; ++z)
            for (int x = low.first; x <= high.first; ++x) {
                offline_nav::Cell cell(x, z);
                int index = g.index(cell);
                if (index < 0)
                    continue;
                Point p = g.point(cell, g.data->heights[index] / 1000.0);
                if (p.x < area[0].number() || p.x > area[2].number() || p.z < area[1].number() ||
                    p.z > area[3].number() || g.point_hazard(p, 3))
                    continue;
                auto next =
                    std::make_tuple(g.point_hazard(p, 4), offline_nav::distance(p, goal), cell);
                if (!best.has || next < score) {
                    best = p;
                    score = next;
                }
            }
        s["_radio_ground_goal"] = tuple_value({key, best.has ? value(best.value) : Value()});
        return best;
    }
    Point target(int id, Point current, Point goal, const Value &order, Value &decision,
                 bool gun_pending) {
        std::string mode = order.get("combat_mode").text("route");
        bool stop = mode != "route" && mode != "advance";
        if (!nav) {
            decision["navigation_stop_at_target"] = Value(stop);
            return goal;
        }
        if (order.get("move_area_bounds").kind != Value::Null) {
            auto grounded = radio_goal(id, current, goal, order);
            if (!grounded.has) {
                decision["navigation_stop_at_target"] = Value(true);
                return current;
            }
            goal = grounded.value;
        }
        double now = field(decision, "now");
        int route = integer(order, "route_index");
        Value path;
        offline_nav::Optional<Point> anchor;
        if (mode == "base_defense")
            path = tuple_value(
                {Value("local"), Value(id), Value("base_defense"),
                 Value(order.get("defense_base_id").truth() ? order.get("defense_base_id").text()
                                                            : "own_base")});
        else if (mode == "route" || mode == "advance" || mode == "hold") {
            if (flag(order, "route_join") && order.get("route_anchor").kind != Value::Null)
                anchor = point(order.get("route_anchor"));
            Pair lane(0, 0);
            if ((mode == "route" || mode == "advance") && anchor.has) {
                auto selected = lane_goal(id, current, goal, order, now, true);
                goal = selected.first;
                lane = selected.second;
            }
            if (anchor.has)
                path = tuple_value(
                    {Value("route_join"), Value(id), Value(integer(bots.at(id)->state, "team")),
                     Value(order.get("route_id").text("direct")), Value(route),
                     Value(static_cast<int>(lane.first)), Value(static_cast<int>(lane.second))});
            else
                path = tuple_value({Value("route"), Value(integer(bots.at(id)->state, "team")),
                                    Value(order.get("route_id").text("direct")), Value(route)});
        } else
            path = tuple_value({Value("local"), Value(id), Value(mode), order.get("target_id")});
        offline_nav::Identity key;
        key.repr = python_key(path);
        key.kind = path[0].text();
        key.owner = key.kind == "route" ? -1 : id;
        key.prefer = key.kind == "route";
        bool direct = offline_nav::distance(current, goal) <= 15 &&
                      !nav->penalized(id, current, goal, now) && nav->grid->dry(current, goal, now);
        bool movement = (order.get("throttle_override").kind == Value::Null ||
                         field(order, "throttle_override") > 0) &&
                        !offline_driver_waiting(integer(config, "driver"), id) && !gun_pending;
        Point selected;
        if (direct) {
            auto escape = nav->observe_direct(id, current, goal, key, now, movement);
            selected = escape.has ? escape.value : goal;
        } else
            selected = nav->next_target(
                id, current, goal, key, now, anchor, offline_nav::Path(),
                std::max(std::max(1.0, nav->grid->data->cell) * 2,
                         std::abs(field(decision, "speed")) * field(config, "lookahead", 1.5)),
                movement);
        auto at = nav->states.find(id);
        decision["navigation_stop_at_target"] =
            Value(stop && ((direct && selected == goal) ||
                           (!direct && at != nav->states.end() && at->second.terminal)));
        return (mode == "route" || mode == "advance") && !anchor.has
                   ? lane_target(id, current, goal, selected, order, now)
                   : selected;
    }
};
} // namespace offline_kernel
#endif
