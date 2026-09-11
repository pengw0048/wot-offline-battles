#ifndef OFFLINE_EXPERIMENT_KERNEL_UPDATE_H
#define OFFLINE_EXPERIMENT_KERNEL_UPDATE_H
#include "kernel_contacts.h"
#include "kernel_aim.h"
#include "kernel_lanes.h"
#include "kernel_codec.h"

namespace offline_kernel {
template <class Owner> struct Simulation {
    Owner &k;
    Diagnostics diagnostic;
    Value config, camera, edge_signature, pending_ram = Value::array();
    double accumulator = 0, next_publication = 0, next_observation = 0, next_lane = 0,
           next_cover = 0, equipment_now = 0;
    int64_t sample = 0, edge_sample = 0, edge_revision = 0;
    int max_lane_pending = 0, max_lane_age = 0;
    double lane_cycle = 0, lane_now = 0;
    offline_nav::Optional<double> lane_due;
    int control_steps = 0, alive_ticks = 0, slope_cursor = 0, cover_cursor = 0, cover_probes = 0,
        lane_pending = 0;
    double maximum_step = 0;
    struct Decision {
        Value token, command;
        double deadline, now;
        Contacts contacts;
    };
    std::map<int, Decision> decisions;
    std::map<int, int> decision_counts;
    std::map<int, Value> repositions;
    struct CoverJob {
        double ready;
        int id;
        Value source, target, route, allies;
    };
    std::deque<CoverJob> cover_queue;
    Value cover_results = Value::array(), hull_key;
    std::map<int, Value> traffic_bodies;
    std::map<std::pair<int, int>, std::vector<int>> traffic_buckets;
    bool traffic_ready = false;
    explicit Simulation(Owner &owner, const Value &v)
        : k(owner), diagnostic(v, owner.bots), config(v), camera(v.get("camera")),
          accumulator(field(v, "accumulator")), next_publication(field(v, "next_publication")),
          next_observation(field(v, "next_observation")), next_lane(field(v, "next_lane")),
          next_cover(field(v, "next_cover")), equipment_now(field(v, "equipment_now")),
          sample(v.get("sample").exact()), edge_sample(v.get("edge_sample").exact()),
          edge_revision(v.get("edge_revision").exact()) {
        if (!k.driver || !k.perception || !k.aim || !k.contacts || !k.lanes)
            throw std::invalid_argument("kernel update configuration incomplete");
        k.motion->diagnostics = &diagnostic;
        for (int id : k.order)
            if (!k.orders.count(id))
                throw std::invalid_argument("kernel requires mandatory server Bot orders");
    }
    std::vector<int> ordered() const {
        std::vector<int> ids = k.order;
        std::stable_sort(ids.begin(), ids.end(), [&](int a, int b) {
            return std::make_pair(integer(k.bots.at(a)->state, sf::slot),
                                  integer(k.bots.at(a)->state, sf::team, 1)) <
                   std::make_pair(integer(k.bots.at(b)->state, sf::slot),
                                  integer(k.bots.at(b)->state, sf::team, 1));
        });
        return ids;
    }
    int tier(const Value &s) const {
        if (camera.kind == Value::Null)
            return 0;
        double dx = field(s, sf::x) - camera[0].number(), dz = field(s, sf::z) - camera[2].number(),
               d = dx * dx + dz * dz, near = field(config, "near"), far = field(config, "far");
        return d <= near * near ? 0 : d <= far * far ? 1 : 2;
    }
    static Value overlay(const Value &command, const Value &target, const Value &source) {
        Value result = command.copy();
        if (result.get(sf::target_id).kind == Value::Null)
            return result;
        if (target.kind == Value::Null || !flag(target, sf::alive, true)) {
            result["fire_allowed"] = Value(false);
            return result;
        }
        Value p = target.get(sf::position);
        result["aim_position"] = p;
        if (!flag(result, "stable_hull_face")) {
            if (result.get("hull_angle_degrees").kind == Value::Null)
                result["face_position"] = p;
            else {
                double angle = field(result, "hull_angle_degrees") * 0.017453292519943295,
                       dx = p[0].number() - source[0].number(),
                       dz = p[2].number() - source[2].number(), c = std::cos(angle),
                       s = std::sin(angle);
                result["face_position"] =
                    vector3(source[0].number() + dx * c - dz * s, source[1].number(),
                            source[2].number() + dx * s + dz * c);
            }
        }
        if (result.get("combat_mode").text() == "advance_contact")
            result["move_position"] = p;
        return result;
    }
    void clear_reposition(int id) {
        if (repositions.erase(id))
            decisions.erase(id);
    }
    std::pair<Value, bool> reposition(Bot &bot, const Contacts &contacts, double now) {
        int id = integer(bot.state, sf::id);
        auto at = repositions.find(id);
        if (at == repositions.end())
            return {Value(), false};
        Value m = at->second;
        if (now >= field(m, "deadline")) {
            clear_reposition(id);
            return {Value(), true};
        }
        auto target = contacts.lookup.find(integer(m, sf::target_id));
        if (target == contacts.lookup.end() || !flag(target->second, sf::alive, true) ||
            (target->second.has(sf::health) && field(target->second, sf::health) <= 0) ||
            Lanes::distance(bot.state, tuple_target(m.get("destination"))) <=
                field(config, "arrival")) {
            clear_reposition(id);
            return {Value(), false};
        }
        Value order = Value::object();
        for (const char *name : {"target_id", "fire_range", "shell_index"})
            order[name] = m.get(name);
        order["aim_position"] = target_position(target->second);
        order["face_position"] = order.get("aim_position");
        order["move_position"] = m.get("destination");
        order["fire_allowed"] = Value(false);
        order["combat_mode"] = Value("friendly_lane_reposition");
        order["throttle_override"] = Value(.72);
        return {order, false};
    }
    static Value tuple_target(const Value &p) {
        Value t = Value::object();
        t[sf::position] = p;
        return t;
    }
    void mark_reposition(Bot &bot, const Value &command, const Value &target, const Value &launch,
                         const Value &verdict, double now) {
        Value &s = bot.state;
        int id = integer(s, sf::id), blocker = integer(verdict, "blocker_id");
        std::string kind = verdict.get("blocker_kind").text();
        if ((kind != "bot" && kind != "player") || blocker <= 0 ||
            integer(verdict, "blocker_team") != integer(s, sf::team) ||
            (kind == "bot" && blocker == id) || command.get(sf::target_id).kind == Value::Null ||
            verdict.get("blocker_position").kind != Value::Array)
            return;
        const Value &shape = s.get(sf::collision_shape);
        double radius = shape.kind == Value::Array
                            ? std::hypot(shape[0].number(), shape[1].number())
                            : std::hypot(std::max(.3, field(s, sf::half_width, 1.7)),
                                         std::max(.5, field(s, sf::half_length, 3.5))),
               other = field(verdict, "blocker_radius", radius);
        if (!std::isfinite(other) || other <= 0)
            other = radius;
        double clearance = radius + other + field(config, "contact_slop"),
               side = (id + integer(launch, sf::fire_seq)) & 1 ? 1 : -1,
               yaw = field(launch, sf::shot_yaw);
        Value m = Value::object();
        m[sf::target_id] = command.get(sf::target_id);
        m[sf::target_kind] = target.get(sf::kind);
        m["destination"] =
            vector3(field(s, sf::x) + std::cos(yaw) * clearance * side, field(s, sf::y),
                    field(s, sf::z) - std::sin(yaw) * clearance * side);
        m[sf::shell_index] = s.get(sf::shell_index);
        m["fire_range"] = Value(std::max(0.0, field(command, "fire_range")));
        m["deadline"] = Value(now + field(config, "reposition_seconds"));
        repositions[id] = m;
        decisions.erase(id);
    }
    void traffic_snapshot(const Value &supplied, const Value &players) {
        traffic_ready = true;
        traffic_bodies.clear();
        traffic_buckets.clear();
        std::vector<int> insertions;
        for (const Value &raw : elements(supplied))
            if (raw.has(sf::id)) {
                int id = integer(raw, sf::id);
                Value b = raw.copy();
                b[sf::position] = position(raw);
                traffic_bodies[id] = b;
                insertions.push_back(id);
            }
        for (const Value &raw : elements(players)) {
            int id = integer(config, "human_base") + integer(raw, sf::id);
            Value b = Value::object();
            double yaw = field(raw, sf::yaw),
                   speed = flag(raw, sf::alive, true) ? field(raw, sf::speed) : 0;
            const Value &shape = raw.get(sf::_kernel).get("collision").get("shape");
            // The reference traffic snapshot reprojects the supplied human row
            // from x/y/z after its neighbour producer emitted position only.
            b[sf::id] = Value(id);
            b[sf::position] = vector3(0, 0, 0);
            b[sf::team] = Value(integer(raw, sf::team));
            b[sf::yaw] = Value(yaw);
            b[sf::alive] = Value(flag(raw, sf::alive, true));
            b["shape"] = shape;
            b[sf::half_width] = shape[0];
            b[sf::half_length] = shape[1];
            b[sf::velocity] = vector3(std::sin(yaw) * speed, 0, std::cos(yaw) * speed);
            traffic_bodies[id] = b;
            insertions.push_back(id);
        }
        for (int id : k.order) {
            const Value &s = k.bots.at(id)->state;
            Value b = Value::object();
            double yaw = field(s, sf::yaw),
                   speed = flag(s, sf::alive, true) ? field(s, sf::speed) : 0;
            b[sf::id] = Value(id);
            b[sf::position] = position(s);
            b[sf::yaw] = Value(yaw);
            b[sf::team] = Value(integer(s, sf::team));
            b[sf::alive] = Value(flag(s, sf::alive, true));
            b["shape"] = s.get(sf::collision_shape);
            b[sf::half_length] = Value(field(s, sf::half_length, 3.5));
            b[sf::half_width] = Value(field(s, sf::half_width, 1.7));
            b[sf::velocity] = vector3(std::sin(yaw) * speed + field(s, sf::push_x), 0,
                                      std::cos(yaw) * speed + field(s, sf::push_z));
            traffic_bodies[id] = b;
            insertions.push_back(id);
        }
        for (int id : integer_dict_order(insertions, flag(config, "py2"))) {
            Value p = traffic_bodies.at(id).get(sf::position);
            traffic_buckets[{static_cast<int>(std::floor(p[0].number() / 24)),
                             static_cast<int>(std::floor(p[2].number() / 24))}]
                .push_back(id);
        }
    }
    Value neighbours(const Value &state) {
        Value rows = Value::array();
        int id = integer(state, sf::id), x = static_cast<int>(std::floor(field(state, sf::x) / 24)),
            z = static_cast<int>(std::floor(field(state, sf::z) / 24));
        for (int dz = -1; dz <= 1; ++dz)
            for (int dx = -1; dx <= 1; ++dx) {
                auto at = traffic_buckets.find({x + dx, z + dz});
                if (at != traffic_buckets.end())
                    for (int peer : at->second)
                        if (peer != id)
                            rows.append(traffic_bodies.at(peer));
            }
        return rows;
    }
    void static_hulls(const Value &players) {
        if (!k.routes->nav)
            return;
        Value key = Value::array();
        auto add = [&](int id, const Value &s, double length, double width) {
            key.append(tuple_value({Value(id), Value(rounded(field(s, sf::x), 2)),
                                    Value(rounded(field(s, sf::z), 2)),
                                    Value(rounded(field(s, sf::yaw), 3)), Value(rounded(length, 2)),
                                    Value(rounded(width, 2))}));
        };
        for (int id : k.order) {
            const Value &s = k.bots.at(id)->state;
            if (!flag(s, sf::alive, true))
                add(id, s, field(s, sf::half_length, 3.5), field(s, sf::half_width, 1.7));
        }
        for (const Value &s : elements(players))
            if (!flag(s, sf::alive, true)) {
                const Value &shape = s.get(sf::_kernel).get("collision").get("shape");
                if (shape.kind == Value::Array)
                    add(integer(config, "human_base") + integer(s, sf::id), s, shape[1].number(),
                        shape[0].number());
            }
        std::sort(key.data->array.begin(), key.data->array.end(),
                  [](const Value &a, const Value &b) { return a[0].exact() < b[0].exact(); });
        if (key == hull_key)
            return;
        hull_key = key;
        auto &grid = *k.routes->nav->grid;
        ++grid.hull_revision;
        grid.hulls.clear();
        double half = grid.data->cell * .5;
        for (const Value &v : elements(key)) {
            double x = v[1].number(), z = v[2].number(), s = std::sin(v[3].number()),
                   c = std::cos(v[3].number()), length = std::max(.5, v[4].number()),
                   width = std::max(.3, v[5].number()),
                   radius = std::sqrt(length * length + width * width);
            auto first = grid.cell({x - radius, 0, z - radius}),
                 last = grid.cell({x + radius, 0, z + radius});
            for (int cz = first.second; cz <= last.second; ++cz)
                for (int cx = first.first; cx <= last.first; ++cx) {
                    auto p = grid.point({cx, cz}, 0);
                    double dx = x - p.x, dz = z - p.z, extent = half * (std::abs(s) + std::abs(c));
                    if (std::abs(dx) > half + std::abs(s) * length + std::abs(c) * width ||
                        std::abs(dz) > half + std::abs(c) * length + std::abs(s) * width ||
                        std::abs(dx * s + dz * c) > length + extent ||
                        std::abs(dx * c - dz * s) > width + extent)
                        continue;
                    for (int ez = -1; ez <= 1; ++ez)
                        for (int ex = -1; ex <= 1; ++ex)
                            if (ex || ez) {
                                offline_nav::Cell a(cx, cz), b(cx + ex, cz + ez);
                                grid.hulls[a < b ? offline_nav::Edge(a, b)
                                                 : offline_nav::Edge(b, a)] =
                                    field(config, "hull_penalty");
                            }
                }
        }
    }
    bool cover_current(const CoverJob &job, double now) {
        auto bot = k.bots.find(job.id);
        if (bot == k.bots.end())
            return false;
        const Value &s = bot->second->state;
        TeamKey key = Perception::team_key(integer(job.source, sf::team), job.target);
        return flag(s, sf::alive, true) && integer(s, sf::team) == integer(job.source, sf::team) &&
               k.perception->remembered.count(key) && k.perception->remaining(key, now) > 0;
    }
    Value cover(double now, bool refresh, bool collect, bool observation,
                std::vector<CoverJob> jobs) {
        Value completed = observation ? cover_results : Value::array();
        if (observation)
            cover_results = Value::array();
        std::sort(jobs.begin(), jobs.end(),
                  [](const CoverJob &a, const CoverJob &b) { return a.id < b.id; });
        if (collect)
            next_cover = now + field(config, "cover_seconds");
        if (collect && !jobs.empty()) {
            size_t cursor = cover_cursor % jobs.size(),
                   count = std::min<size_t>(integer(config, "cover_jobs"), jobs.size());
            cover_cursor = (cursor + count) % jobs.size();
            for (size_t i = 0; i < count; ++i) {
                CoverJob job = jobs[(cursor + i) % jobs.size()];
                job.ready = now + field(config, "cover_window") * i / count;
                job.allies = Value::array();
                for (int id : k.order) {
                    const Value &s = k.bots.at(id)->state;
                    if (flag(s, sf::alive) && integer(s, sf::team) == integer(job.source, sf::team))
                        job.allies.append(position(s));
                }
                cover_queue.push_back(job);
            }
        }
        if (refresh && !cover_queue.empty() && now + 1e-9 >= cover_queue.front().ready) {
            CoverJob job = cover_queue.front();
            cover_queue.pop_front();
            ++cover_probes;
            if (cover_current(job, now)) {
                job.source = k.bots.at(job.id)->state;
                job.target = job.target.copy();
                offline_kernel::update(job.target, k.perception->remembered.at(Perception::team_key(
                                                       integer(job.source, sf::team), job.target)));
                std::vector<double> args;
                Aim::point(args, job.route);
                args.push_back(job.allies.size());
                for (const Value &p : elements(job.allies))
                    Aim::point(args, p);
                auto reply = k.engine->query(770, job.source, job.target, args);
                if (reply[0]) {
                    Value row = Value::object();
                    row["bot_id"] = Value(job.id);
                    row[sf::target_id] = Value(Perception::id(job.target));
                    row[sf::target_kind] = Value(job.target.get(sf::kind).text("human"));
                    row["candidates"] = engine_token(static_cast<int>(reply[1]));
                    cover_results.append(row);
                }
            }
        }
        return completed;
    }
    void siege_set(Bot &bot, int next, double duration = 0, double total = 0) {
        Value &s = bot.state;
        int id = integer(s, sf::id), before = integer(s, sf::siege_state);
        if (next != 1 && next != 3)
            duration = total = 0;
        s[sf::siege_state] = Value(next);
        s[sf::_siege_time_left] = Value(std::max(0.0, duration));
        s[sf::_siege_transition_total] = Value(std::max(0.0, total));
        s[sf::siege_time_left_ms] =
            Value(duration > 0 ? static_cast<int>(std::ceil(duration * 1000 - 1e-9)) : 0);
        s[sf::siege_transition_total_ms] =
            Value(total > 0 ? static_cast<int>(std::ceil(total * 1000 - 1e-9)) : 0);
        const Value &modes = bot.config.get("siege_modes");
        if (!modes.truth() || (before == 2) == (next == 2))
            return;
        const Value &mode = modes[next == 2 ? 1 : 0];
        Gun candidate;
        candidate.load(mode.get("gun"));
        Gun &g = bot.gun;
        if (candidate.shell_count != g.shell_count || candidate.clip_size != g.clip_size)
            throw std::runtime_error("Siege descriptor changed ammunition contract");
        double dispersion = g.dispersion;
        g.fully_aimed = candidate.fully_aimed;
        g.after_shot = candidate.after_shot;
        g.after_in_burst = candidate.after_in_burst;
        g.burst_count = candidate.burst_count;
        g.burst_interval = candidate.burst_interval;
        g.turret_factor = candidate.turret_factor;
        g.aiming_time = candidate.aiming_time;
        g.movement_factor = candidate.movement_factor;
        g.rotation_factor = candidate.rotation_factor;
        g.reload_full = candidate.reload_full;
        g.reload_intra = candidate.reload_intra;
        g.current_factor = std::max(1.0, dispersion / g.fully_aimed);
        double aiming_time = std::max(g.aiming_time, .1),
               maximum = g.current_factor > 0 && g.current_factor <= 1000
                             ? aiming_time * std::log(1000 / g.current_factor)
                             : 0;
        if (g.aiming_elapsed > maximum) {
            g.aiming_start = std::max(g.current_factor, 1000.0);
            g.aiming_elapsed = maximum;
        } else
            g.aiming_start = g.current_factor * std::exp(g.aiming_elapsed / aiming_time);
        g.dispersion = g.fully_aimed * g.current_factor;
        for (const char *key : {"aim", "motion", "perception"}) {
            bot.config[key] = mode.get(key);
        }
        offline_kernel::update(bot.config, mode.get(sf::health));
        bot.critical.reset(new CriticalConfig(mode.get(sf::critical)));
        offline_kernel::update(s, mode.get("state"));
        k.motion->profiles[id] = Motion::params(mode.get("motion").get("physics"));
        k.motion->flow.caches.erase(id);
        k.driver->coast.erase(id);
        k.traffic.held.erase(id);
        for (auto at = k.traffic.pairs.begin(); at != k.traffic.pairs.end();)
            if (at->first.first == id || at->first.second == id)
                at = k.traffic.pairs.erase(at);
            else
                ++at;
        k.aim->cancel(bot);
        for (const char *key : {"suspension_pitch_velocity", "suspension_roll_velocity",
                                "_suspension_support_vertical_speed"}) {
            s[key] = Value(0.0);
        }
        for (const char *key : {"_suspension_support_gradient", "_spring_ground_memory",
                                "_pseudo_ground_memory", "_suspension_ground_plane"})
            s.erase(key);
    }
    void siege_advance(Bot &bot, double dt) {
        Value &s = bot.state;
        if (!bot.config.get("siege_modes").truth()) {
            siege_set(bot, 0);
            return;
        }
        int current = integer(s, sf::siege_state);
        if (current != 1 && current != 3) {
            s[sf::_siege_time_left] = Value(0.0);
            s[sf::siege_time_left_ms] = Value(0);
            s[sf::_siege_transition_total] = Value(0.0);
            s[sf::siege_transition_total_ms] = Value(0);
            return;
        }
        double left = std::max(0.0, field(s, sf::_siege_time_left) - dt);
        if (left > 1e-9) {
            s[sf::_siege_time_left] = Value(left);
            s[sf::siege_time_left_ms] = Value(static_cast<int>(std::ceil(left * 1000 - 1e-9)));
            return;
        }
        int final = current == 1 ? 2 : 0;
        siege_set(bot, final);
        s[sf::_siege_intent] = Value(final == 2);
        s[sf::_siege_intent_elapsed] = Value(0.0);
    }
    bool siege_intent(Bot &bot, Value &command, const Value &target, double dt) {
        Value &s = bot.state;
        if (!bot.config.get("siege_modes").truth())
            return false;
        if (bot.burst.active) {
            s[sf::_siege_intent_elapsed] = Value(0.0);
            return false;
        }
        int current = integer(s, sf::siege_state);
        bool switching = current == 1 || current == 3;
        if (switching)
            command["fire_allowed"] = Value(false);
        Value goal = command.get("move_position");
        if (goal.kind == Value::Null)
            goal = position(s);
        bool desired =
            target.kind == Value::Object && flag(target, sf::alive, true) &&
            !(flag(command, "movement_intent", std::abs(field(command, "throttle")) > .05) &&
              Lanes::distance(s, tuple_target(goal)) > field(config, "siege_long"));
        if (s.get(sf::_siege_intent).kind == Value::Null || flag(s, sf::_siege_intent) != desired) {
            s[sf::_siege_intent] = Value(desired);
            s[sf::_siege_intent_elapsed] = Value(0.0);
            return switching;
        }
        if ((current == 1 && desired) || (current == 3 && !desired)) {
            s[sf::_siege_intent_elapsed] = Value(0.0);
            return true;
        }
        double elapsed = field(s, sf::_siege_intent_elapsed) + dt;
        s[sf::_siege_intent_elapsed] = Value(elapsed);
        if ((desired && current == 2) || (!desired && current == 0) ||
            elapsed + 1e-9 <
                field(config, desired ? "siege_enable_debounce" : "siege_disable_debounce"))
            return false;
        std::set<std::string> destroyed = names(s.get(sf::critical).get("destroyed"));
        if (destroyed.count("engineHealth")) {
            s[sf::_siege_intent_elapsed] = Value(0.0);
            return false;
        }
        bool yellow = false;
        const Value &devices = s.get(sf::critical).get("devices");
        if (devices.kind == Value::Array)
            for (const Value &v : elements(devices))
                if (v.get(sf::name).text() == "engineHealth")
                    yellow = v.get("state").text() == "critical" ||
                             bot.critical->condition(field(v, "hp"), "engineHealth") == 1;
        const Value &params = bot.config.get("siege_params");
        double total = params[desired ? 0 : 1].number() * (yellow ? params[3].number() : 1),
               left = total;
        int next = desired ? 1 : 3;
        if (switching) {
            double previous_total = field(s, sf::_siege_transition_total);
            if (previous_total <= 0)
                throw std::invalid_argument("Siege transition total missing");
            double remaining =
                std::min(std::max(0.0, field(s, sf::_siege_time_left)), previous_total);
            left = clamp((previous_total - remaining) / previous_total, 0, 1) * total;
            if (left <= 1e-9) {
                next = desired ? 2 : 0;
                left = total = 0;
            }
        }
        siege_set(bot, next, left, total);
        command["fire_allowed"] = Value(false);
        s[sf::_siege_intent_elapsed] = Value(0.0);
        return true;
    }
    void siege_lock() {
        for (const auto &v : k.motion->locked) {
            Bot &bot = *k.bots.at(v.first);
            Value &s = bot.state;
            s[sf::x] = v.second[0];
            s[sf::z] = v.second[1];
            s[sf::yaw] = v.second[2];
            for (const char *key : {"speed", "push_x", "push_z"})
                s[key] = Value(0.0);
            s[sf::movement_dir] = Value(0);
            s[sf::rotation_dir] = Value(0);
            bot.turn_speed = 0;
        }
    }
    void burst(Bot &bot, const Value &target, const Value &solution, double dt, int64_t start,
               int64_t end, double reload, double dispersion,
               const std::set<std::string> &destroyed) {
        if (!bot.burst.active)
            return;
        for (const Subshot &edge : bot.burst.advance(dt)) {
            int64_t time = std::min(end, start + static_cast<int64_t>(std::max(
                                                     0.0, rounded(edge.offset * 1000000, 0))));
            Value &s = bot.state;
            int siege = integer(s, sf::siege_state);
            if (!flag(s, sf::alive) || flag(s, sf::_drowning) || flag(s, sf::_overturned) ||
                destroyed.count("gunHealth") || siege == 1 || siege == 3 ||
                !bot.ammo.can_fire(true)) {
                Launches::cancel(bot, reload);
                break;
            }
            Value preview = k.aim->preview(bot, bot.burst.shell, solution, &edge);
            if (preview.kind == Value::Null ||
                !flag(k.aim->friendly(bot, target, bot.burst.shell, preview, false), "clear") ||
                !k.launches.commit(bot, k.round, reload, dispersion, edge, Value(), preview, time,
                                   Value())) {
                Launches::cancel(bot, reload);
                break;
            }
        }
        publish_burst(bot);
    }
    Value publication(const std::vector<int> &ids, int64_t end) {
        Value rows = Value::array(), signature = Value::array(), bot_edges = Value::array();
        for (int id : ids) {
            Bot &bot = *k.bots.at(id);
            publish_burst(bot);
            bot.state[sf::equipment_states] = bot.equipment_wire(equipment_now);
            rows.append(k.codec.row(bot.state));
            Value scalar = Value::array();
            for (const Value &name : elements(config.get("edge_fields"))) {
                std::string key = name.text();
                scalar.append(tuple_value({Value(bot.state.has(key)), bot.state.get(key)}));
            }
            Value equipment = Value::array();
            for (const Equipment &e : bot.equipment)
                equipment.append(e.edge(equipment_now));
            Value shot = Value::array();
            for (const char *key : {"shot_yaw", "shot_pitch"})
                shot.append(tuple_value({Value(bot.state.has(key)), bot.state.get(key)}));
            bot_edges.append(
                tuple_value({scalar, bot.state.get(sf::ammo_remaining), equipment, shot}));
        }
        Value launches = Value::array(), rams = Value::array();
        for (const Value &v : elements(k.launches.pending))
            launches.append(tuple_value({v.get(sf::id), v.get(sf::fire_seq)}));
        for (const Value &v : elements(pending_ram))
            rams.append(tuple_value(
                {v.get("bot_id"), v.get(sf::target_kind), v.get(sf::target_id), v.get("ram_seq")}));
        signature = tuple_value({bot_edges, launches, rams});
        if (signature != edge_signature) {
            edge_signature = signature;
            edge_sample = end;
            ++edge_revision;
        }
        Value out = Value::object();
        out["type"] = Value("bot_state");
        out["rows"] = rows;
        out["sample_time_us"] = Value(end);
        out["edge_sample_time_us"] = Value(edge_sample);
        out["edge_revision"] = Value(edge_revision);
        if (k.launches.pending.truth())
            out["launches"] = k.launches.pending.copy();
        return out;
    }
    Value slice(double dt, double now, const Value &players, const Value &supplied, bool refresh,
                bool publish) {
        auto &p = *k.perception;
        auto &motion = *k.motion;
        auto &aim = *k.aim;
        auto &lanes = *k.lanes;
        std::vector<int> ids = ordered();
        std::set<int> processed, integrated;
        std::map<int, Value> ticks, settled;
        std::map<int, bool> safe, blocked, ballistic;
        int64_t end = sample + std::max<int64_t>(1, static_cast<int64_t>(rounded(dt * 1000000, 0)));
        if (next_publication <= 0) {
            next_publication = now;
        }
        while (next_publication <= now + 1e-9)
            next_publication += field(config, "control");
        equipment_now += dt;
        k.engine->sample_time = sample;
        k.engine->equipment_time = equipment_now;
        lanes.incoming_budget = refresh ? 1 : 0;
        motion.locked.clear();
        p.start_slice(players, k.order);
        p.lifecycle(now);
        bool observation = publish && refresh && now >= next_observation,
             due_lane = now >= next_lane,
             reserved = refresh && !cover_queue.empty() &&
                        now + 1e-9 >= cover_queue.front().ready &&
                        cover_current(cover_queue.front(), now),
             refresh_lane = refresh && !reserved &&
                            (due_lane || (next_lane > 0 &&
                                          now + field(config, "lane_seconds") + 1e-9 >= next_lane)),
             collect_cover = refresh && publish && cover_queue.empty() && now >= next_cover;
        int final_budget = integer(config, "final_lane_budget"),
            supplemental = integer(config, "lane_budget");
        if (refresh)
            static_hulls(players);
        traffic_ready = false;
        std::set<int> due;
        std::map<int, ActorKey> selected;
        for (int id : ids) {
            auto at = decisions.find(id);
            bool valid =
                at != decisions.end() && at->second.token == k.orders.at(id).get("_kernel_token");
            if (refresh && (!valid || now >= at->second.deadline))
                due.insert(id);
            const Value &order = k.orders.at(id);
            std::string kind = order.get(sf::target_kind).text();
            if ((kind == "bot" || kind == "human") && order.get(sf::target_id).kind != Value::Null)
                selected[id] = ActorKey{{kind == "human" ? 1 : 0, integer(order, sf::target_id)}};
            else if (at != decisions.end()) {
                auto t =
                    at->second.contacts.lookup.find(integer(at->second.command, sf::target_id, -1));
                if (t != at->second.contacts.lookup.end())
                    selected[id] = Perception::key(t->second);
            }
        }
        p.prepare(now, observation || refresh_lane, ids, due, selected);
        if (observation || refresh_lane)
            p.append_humans(now);
        std::map<LaneKey, int> priorities;
        std::vector<CoverJob> jobs;
        for (int id : k.order) {
            Bot &bot = *k.bots.at(id);
            Value &s = bot.state;
            auto cancel = [&]() {
                Launches::cancel(bot, aim.factors.stat(s, *bot.critical, "reload"));
                if (bot.clear_reposition) {
                    clear_reposition(id);
                    bot.clear_reposition = false;
                }
            };
            if (!flag(s, sf::alive)) {
                cancel();
                continue;
            }
            p.note_still(s, now);
            integrated.insert(id);
            bot.advance_critical(dt, now, equipment_now);
            if (!flag(s, sf::alive)) {
                cancel();
                continue;
            }
            double depth = -1;
            if (bot.drowning_due(dt) && flag(config, "has_water")) {
                auto result = k.engine->query(769, s, Value::object());
                depth = result[0] ? result[1] : -1;
            }
            bot.advance_drowning(dt, depth);
            if (!flag(s, sf::alive)) {
                cancel();
                continue;
            }
            bot.advance_overturn(dt);
            if (!flag(s, sf::alive)) {
                cancel();
                continue;
            }
            double siege_yaw = field(s, sf::yaw);
            bool siege_locked =
                integer(s, sf::siege_state) == 1 || integer(s, sf::siege_state) == 3;
            siege_advance(bot, dt);
            Value position_now = position(s);
            ticks[id] = position_now;
            safe[id] = motion.safe(Motion::point(s));
            Contacts contacts;
            Value command;
            bool decision_due = due.count(id) > 0;
            auto cached = decisions.find(id);
            bool valid = cached != decisions.end() &&
                         cached->second.token == k.orders.at(id).get("_kernel_token");
            bool initial_decision = cached == decisions.end();
            double decision_time = initial_decision ? dt : std::max(dt, now - cached->second.now);
            if (valid && !decision_due) {
                command = cached->second.command.copy();
                contacts = cached->second.contacts;
            } else if (!refresh) {
                command = Value::object();
                command["bot_id"] = Value(id);
                command[sf::target_id] = Value();
                for (const char *key : {"aim_position", "face_position", "move_position"})
                    command[key] = position_now;
                command["fire_range"] = Value(0.0);
                command["combat_mode"] = Value("physical_hold");
                command["fire_allowed"] = Value(false);
                command[sf::shell_index] = Value(integer(s, sf::shell_index));
                command["throttle"] = Value(0.0);
                command["turn"] = Value(0.0);
                command["target_yaw"] = s.get(sf::yaw);
                command["recovery_mode"] = Value("physical_hold");
                command["movement_intent"] = Value(false);
            } else {
                contacts = p.contacts(s, now, processed);
                if (!traffic_ready)
                    traffic_snapshot(supplied, players);
                Value state = Value::object();
                for (const char *key : {"id", "slot", "yaw", "speed", "health", "max_health",
                                        "half_length", "half_width"})
                    state[key] = s.get(key);
                state[sf::position] = position_now;
                state[sf::dt] = Value(decision_time);
                state[sf::now] = Value(now);
                state[sf::neighbours] = neighbours(s);
                state[sf::contacts] = contacts.rows;
                state[sf::velocity] = vector3(std::sin(field(s, sf::yaw)) * field(s, sf::speed), 0,
                                              std::cos(field(s, sf::yaw)) * field(s, sf::speed));
                double horizon = field(config, "decision_seconds") *
                                 config.get("decision_tiers")[tier(s)].number();
                state[sf::decision_horizon] = Value(horizon);
                std::string expected = k.orders.at(id).get("combat_mode").text("route");
                state[sf::stopping_distance] =
                    std::abs(field(s, sf::speed)) > .35 && expected != "route" &&
                            expected != "advance"
                        ? Value(k.driver->stopping(
                              bot, cached == decisions.end() ? Value() : cached->second.command))
                        : Value();
                auto override_order = reposition(bot, contacts, now);
                Value order = override_order.first;
                if (order.kind == Value::Null) {
                    order = k.orders.at(id).copy();
                    if (order.get(sf::target_kind).text() == "human" &&
                        order.get(sf::target_id).kind != Value::Null)
                        order[sf::target_id] =
                            Value(integer(config, "human_base") + integer(order, sf::target_id));
                    auto target = contacts.lookup.find(integer(order, sf::target_id, -1));
                    order =
                        overlay(order, target == contacts.lookup.end() ? Value() : target->second,
                                position_now);
                }
                k.driver->probes.clear();
                command = k.driver->drive(bot, state, order,
                                          aim.intents.count(id) || aim.reproofs.count(id));
                command =
                    k.traffic.adjust(id, traffic_bodies.at(id), command, state.get(sf::neighbours),
                                     now, [&](double yaw) { return k.driver->clear(bot, yaw); });
                if (override_order.second)
                    command["fire_allowed"] = Value(false);
                ++decision_counts[id];
                decisions[id] =
                    Decision{k.orders.at(id).get("_kernel_token"), command.copy(),
                             cache_deadline(now, id, horizon, 3, initial_decision), now, contacts};
            }
            command["throttle"] = Value(clamp(field(command, "throttle"), -1, 1));
            if (refresh_lane && !observation)
                for (const Value &t : elements(contacts.rows))
                    if (flag(t, "fresh_visible"))
                        p.team_visible[Perception::team_key(integer(s, sf::team), t)] = true;
            if (observation)
                p.collect(s, contacts, processed);
            Value target;
            auto chosen = contacts.lookup.find(integer(command, sf::target_id, -1));
            if (chosen != contacts.lookup.end())
                target = p.refresh(chosen->second);
            if (target.kind != Value::Null)
                priorities[Lanes::key(s, target)] = flag(command, "fire_allowed") ? 0 : 1;
            command = overlay(command, target, position_now);
            s[sf::target_kind] = target.get(sf::kind);
            s[sf::target_id] = target.get(sf::network_id);
            siege_locked = siege_intent(bot, command, target, dt) || siege_locked;
            double reload = aim.factors.stat(s, *bot.critical, "reload");
            if (!bot.burst.active) {
                bot.gun.rescale(reload);
                bot.gun.tick(dt);
                int completed = bot.gun.complete(reload, bot.ammo.planned());
                bot.ammo.stage(bot.gun.shell(integer(command, sf::shell_index)), completed >= 0,
                               completed == 0);
            }
            publish_ammo(bot);
            bool spg = s.get(sf::profile).get(sf::class_tag).text() == "SPG";
            Value intent, reproof;
            if (spg) {
                if (!flag(command, "fire_allowed"))
                    aim.cancel(bot);
                else {
                    intent = aim.active_intent(bot, target, bot.ammo.loaded, now);
                    reproof = aim.active_reproof(bot, target, bot.ammo.loaded, now);
                }
                Value frozen = intent.kind != Value::Null ? intent.get("solution")
                                                          : reproof.get("hold_solution");
                if (frozen.kind == Value::Object) {
                    command["aim_position"] = frozen.get("aim_position");
                    command["face_position"] = frozen.get("aim_position");
                }
                if (reproof.kind != Value::Null) {
                    command["throttle"] = Value(0.0);
                    command["turn"] = Value(0.0);
                    command["movement_intent"] = Value(false);
                }
            }
            bool escape = k.routes->nav && k.routes->nav->grid->point_hazard(Motion::point(s), 4);
            diagnostic.planner_age = decisions.count(id) ? now - decisions.at(id).now : -1;
            motion.step(bot, command, target, dt, now, decision_due, refresh, siege_locked,
                        siege_yaw, escape);
            publish_ammo(bot);
            auto local = aim.cadenced(bot, target, bot.ammo.loaded, now, refresh,
                                      bot.burst.active || intent.kind != Value::Null ||
                                          reproof.kind != Value::Null);
            command["_ballistic_solution"] = local.first;
            double old_turret = field(s, sf::turret_yaw);
            aim.slew(bot, command, target, dt);
            double turret = std::abs(angle_delta(field(s, sf::turret_yaw) - old_turret)) /
                            std::max(dt, 1e-9),
                   dispersion = aim.factors.stat(s, *bot.critical, "dispersion");
            bot.gun.bloom_tick(dt, std::abs(field(s, sf::speed)), std::abs(bot.turn_speed), turret,
                               dispersion, aim.factors.stat(s, *bot.critical, "aim_time"));
            s[sf::clip_size] = Value(bot.gun.clip_size);
            publish_reload(bot, reload);
            std::set<std::string> destroyed = names(s.get(sf::critical).get("destroyed"));
            burst(bot, target, local.first, dt, sample, end, reload, dispersion, destroyed);
            double distance = target.kind == Value::Null ? 0 : Lanes::distance(s, target),
                   range = std::max(0.0, field(command, "fire_range"));
            bool in_range = target.kind != Value::Null && distance > 1 &&
                            local.first.kind != Value::Null && (range <= 0 || distance < range);
            if (publish && local.second && flag(command, "fire_allowed") && in_range &&
                !flag(s, sf::_drowning) && !flag(s, sf::_overturned) &&
                !destroyed.count("gunHealth") && flag(s, sf::gun_aligned) && !bot.burst.active &&
                bot.gun.ready(reload) && bot.ammo.can_fire() &&
                (intent.kind != Value::Null || reproof.kind != Value::Null ||
                 lanes.clear(s, target, now, false, &final_budget).truth()) &&
                k.gunners.at(id).ready(s, target, now, bot.gun)) {
                Value receipt, preview;
                if (spg)
                    receipt = aim.launch_receipt(bot, target, bot.ammo.loaded, local.first, now);
                else if (aim.direct_matches(bot, target, local.first))
                    preview = aim.preview(bot, bot.ammo.loaded, local.first);
                Value launch = spg ? receipt : preview;
                if (launch.kind != Value::Null) {
                    Value verdict = aim.friendly(bot, target, bot.ammo.loaded, launch, spg);
                    if (flag(verdict, "clear")) {
                        clear_reposition(id);
                        bool fired = k.launches.fire(bot, k.round, reload, dispersion, receipt,
                                                     preview, end, Value());
                        if (fired && spg)
                            aim.cancel(bot);
                    } else {
                        mark_reposition(bot, command, target, launch, verdict, now);
                        if (spg)
                            aim.cancel(bot);
                    }
                }
            }
            std::string mode = command.get("combat_mode").text();
            static const std::set<std::string> cover_modes = {"take_cover",
                                                              "cover_hold",
                                                              "cover_peek",
                                                              "cover_return",
                                                              "under_fire_withdraw",
                                                              "low_health_retreat",
                                                              "crossfire_withdraw"},
                                               fire_modes = {"engage", "advance_contact",
                                                             "jiggle_forward", "jiggle_back"};
            if (collect_cover && target.kind != Value::Null && flag(target, sf::visible) &&
                flag(config, "has_cover") &&
                (cover_modes.count(mode) ||
                 (flag(command, "fire_allowed") && fire_modes.count(mode))))
                jobs.push_back(CoverJob{now, id, s.clone(), target.copy(),
                                        command.get("move_position").kind == Value::Null
                                            ? position_now
                                            : command.get("move_position"),
                                        Value()});
            processed.insert(id);
        }
        if (refresh_lane) {
            lane_pending = lanes.service(now, next_lane, ids, processed, priorities, supplemental);
        }
        if (observation)
            lanes.merge(now);
        lane_diagnostics(now, next_lane, due_lane);
        if (due_lane && refresh_lane && lane_pending <= 0)
            next_lane = now + field(config, "lane_seconds");
        Value affordances = cover(now, refresh, collect_cover, observation, jobs);
        siege_lock();
        std::vector<int> slope;
        for (int id : ids) {
            Bot &bot = *k.bots.at(id);
            if (flag(bot.state, sf::alive, true) && integrated.count(id)) {
                bool was = flag(bot.state, sf::airborne);
                blocked[id] = motion.vertical(bot, dt, ticks.at(id), motion.attempted[id]);
                ballistic[id] = was || flag(bot.state, sf::airborne);
                settled[id] = position(bot.state);
                slope.push_back(id);
            }
        }
        size_t prior = pending_ram.size();
        Value reports = k.contacts->step(players, ids, now, dt);
        for (const Value &v : elements(reports))
            pending_ram.append(v);
        siege_lock();
        if (!publish && pending_ram.size() > prior)
            publish = true;
        for (int id : slope) {
            Bot &bot = *k.bots.at(id);
            Value trace = bot.state.get(sf::_motion_stall_pending);
            if (trace.kind == Value::Object)
                trace["after_contacts"] = position(bot.state);
            bool rollback = false;
            if (!blocked[id] && !ballistic[id] && !flag(bot.state, sf::airborne))
                rollback =
                    motion.guard(bot, Routes::point(ticks.at(id)), safe[id], motion.attempted[id]);
            diagnostic.finish(bot, blocked[id], rollback, settled.at(id), k.contacts->active);
        }
        for (int id : motion.invalidated)
            decisions.erase(id);
        motion.invalidated.clear();
        alive_ticks += slope.size();
        if (refresh && !slope.empty()) {
            size_t start = slope_cursor % slope.size(), visited = 0, sampled = 0;
            while (visited < slope.size() &&
                   sampled < static_cast<size_t>(integer(config, "slope_budget"))) {
                Bot &bot = *k.bots.at(slope[(start + visited) % slope.size()]);
                if (motion.slope(bot, tier(bot.state)))
                    ++sampled;
                ++visited;
            }
            slope_cursor = (start + std::max<size_t>(1, visited)) % slope.size();
        }
        Value outgoing = Value::array();
        if (publish) {
            for (int id : ids)
                k.bots.at(id)->mark_combat();
            outgoing.append(publication(ids, end));
            for (const Value &v : elements(pending_ram))
                outgoing.append(v);
            pending_ram = Value::array();
            if (observation) {
                next_observation = now + field(config, "observation_seconds");
                Value row = Value::object();
                row["type"] = Value("bot_observation");
                row[sf::contacts] = lanes.pack(now);
                row["affordances"] = affordances;
                outgoing.append(row);
            }
        }
        sample = end;
        k.engine->sample_time = sample;
        return outgoing;
    }
    void lane_diagnostics(double now, double cycle, bool due) {
        if (lane_cycle != cycle) {
            lane_cycle = cycle;
            lane_due.reset();
        }
        lane_now = now;
        max_lane_pending = std::max(max_lane_pending, lane_pending);
        if (lane_pending <= 0) {
            lane_due.reset();
            return;
        }
        if (due && !lane_due.has)
            lane_due = cycle > 0 ? cycle : now;
        if (lane_due.has)
            max_lane_age = std::max(
                max_lane_age,
                static_cast<int>(std::max(0.0, rounded((now - lane_due.value) * 1000, 0))));
    }
    Value diagnostics() {
        Value result = Value::object();
        double values[20] = {310, static_cast<double>(k.perception->handle)};
        offline_perception_dispatch(values, 20);
        const char *names[] = {"visibility_queue_depth",
                               "visibility_queue_max_depth",
                               "visibility_oldest_stale_age_ms",
                               "visibility_oldest_stale_max_age_ms",
                               "visibility_admitted",
                               "visibility_completed",
                               "visibility_deferred",
                               "visibility_selected_services",
                               "visibility_fire_services",
                               "visibility_new_services",
                               "visibility_ordinary_services"};
        for (int i = 0; i < 11; ++i)
            result[names[i]] = Value(
                static_cast<int>(i == 2 || i == 3 ? rounded(values[i] * 1000, 0) : values[i]));
        result["alive_bot_ticks"] = Value(alive_ticks);
        result["suspension_param_failures"] = Value(integer(config, "suspension_failures"));
        result["shot_lane_pending_pairs"] = Value(lane_pending);
        result["shot_lane_pending_max_pairs"] = Value(max_lane_pending);
        result["shot_lane_oldest_due_age_ms"] = Value(
            lane_pending > 0 && lane_due.has
                ? static_cast<int>(std::max(0.0, rounded((lane_now - lane_due.value) * 1000, 0)))
                : 0);
        result["shot_lane_oldest_due_max_age_ms"] = Value(max_lane_age);
        result["shot_lane_completed_pairs"] = Value(k.lanes->completed);
        result["shot_lane_budget_deferred_attempts"] = Value(k.lanes->deferred);
        return result;
    }
    static void references(const Value &value, std::set<int> &out) {
        if (value.kind == Value::Object) {
            if (value.has("_engine_token")) {
                out.insert(integer(value, "_engine_token"));
                return;
            }
            value.visit([&](const std::string &, const Value &item) { references(item, out); });
        } else if (value.kind == Value::Array)
            for (const Value &v : elements(value))
                references(v, out);
    }
    Value retained_tokens(const Value &outgoing) {
        std::set<int> refs;
        references(outgoing, refs);
        for (int id : k.order)
            references(k.bots.at(id)->state.get(sf::shot_proof_key), refs);
        for (const auto &v : k.aim->cache)
            references(v.second.solution, refs);
        for (const auto &v : k.aim->intents)
            references(v.second, refs);
        for (const auto &v : k.aim->reproofs)
            references(v.second, refs);
        references(k.launches.pending, refs);
        references(cover_results, refs);
        for (const CoverJob &job : cover_queue) {
            references(job.target, refs);
            references(job.source.get(sf::shot_proof_key), refs);
        }
        Value result = Value::array();
        for (int id : refs)
            result.append(Value(id));
        return result;
    }
    Value update(const Value &input) {
        if (input.has("orders")) {
            std::map<int, Value> accepted;
            for (const Value &row : elements(input.get("orders"))) {
                int id = static_cast<int>(row[0].exact());
                if (!k.bots.count(id) || accepted.count(id))
                    throw std::invalid_argument("kernel order actor");
                accepted[id] = row[1];
            }
            if (accepted.size() != k.bots.size())
                throw std::invalid_argument("kernel requires complete server Bot orders");
            for (const auto &entry : accepted) {
                int id = entry.first;
                const Value &previous = k.orders.at(id), &next = entry.second;
                if (previous.get("_kernel_token") == next.get("_kernel_token"))
                    continue;
                if (!(previous.get("route_id") == next.get("route_id"))) {
                    for (const char *key :
                         {"_route_lane_group", "_route_lane_gate", "_route_lane_origin",
                          "_route_lane_desired", "_route_lane_row_desired", "_route_lane_forward",
                          "_route_lane_segment", "_route_lane_offset", "_route_lane_row",
                          "_route_lane_goal"})
                        k.bots.at(id)->state.erase(key);
                }
                decisions.erase(id);
                k.motion->flow.caches.erase(id);
            }
            k.orders.swap(accepted);
        }
        double dt = std::max(0.0, field(input, sf::dt)), now = field(input, sf::now);
        if (input.has("camera"))
            camera = input.get("camera");
        control_steps = 0;
        maximum_step = 0;
        k.contacts->leases.clear();
        accumulator += dt;
        auto nav = k.routes->nav;
        if (nav)
            nav->begin(dt);
        Value outgoing = Value::array();
        bool begun = false;
        try {
            if (accumulator + 1e-9 >= field(config, "control")) {
                k.motion->begin();
                k.perception->begin();
                begun = true;
                double elapsed = accumulator;
                accumulator = 0;
                bool refresh = true;
                while (elapsed > 1e-12) {
                    double step = std::min(elapsed, field(config, "maximum_step"));
                    for (int id : k.order) {
                        const Burst &b = k.bots.at(id)->burst;
                        if (b.active)
                            step = std::min(step, b.left <= 1e-9 ? 1e-9 : b.left);
                    }
                    elapsed = std::max(0.0, elapsed - step);
                    ++control_steps;
                    maximum_step = std::max(maximum_step, step);
                    Value rows =
                        slice(step, now - elapsed, input.get("players"), input.get(sf::neighbours),
                              refresh, refresh || elapsed <= 1e-12);
                    for (const Value &row : elements(rows))
                        outgoing.append(row);
                    refresh = false;
                }
                k.contacts->finish(k.driver->handle);
                if (flag(config, "has_destructible"))
                    for (int id : ordered()) {
                        const Value &s = k.bots.at(id)->state;
                        if (flag(s, sf::alive) && field(s, sf::health) > 0 &&
                            std::abs(field(s, sf::speed)) >= field(config, "destructible_speed"))
                            k.engine->query(771, s, Value::object());
                    }
            }
        } catch (...) {
            if (begun) {
                k.perception->finish();
                k.motion->finish();
            }
            if (nav)
                nav->frame_open = false;
            throw;
        }
        if (begun) {
            k.perception->finish();
            k.motion->finish();
        }
        if (nav)
            nav->frame_open = false;
        for (Value &row : outgoing.data->array)
            if (row.get("type").text() == "bot_state")
                row["source_batch_horizon_us"] = Value(sample);
        return outgoing;
    }
};
} // namespace offline_kernel
#endif
