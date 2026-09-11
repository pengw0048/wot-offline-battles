#ifndef OFFLINE_EXPERIMENT_KERNEL_MOTION_H
#define OFFLINE_EXPERIMENT_KERNEL_MOTION_H
#include "kernel_engine.h"
#include "kernel_diagnostics.h"
#include "kernel_factors.h"
#include "kernel_world.h"
#include "motion_vertical.h"
#include "navigation_flow.h"

namespace offline_kernel {
struct Motion {
    Value config;
    std::map<int, std::unique_ptr<Bot>> &bots;
    Engine &engine;
    Factors factors;
    offline_motion::Flow flow;
    offline_motion::Tuning tuning;
    std::map<int, offline_motion::Params> profiles;
    std::map<int, int> grinds;
    std::map<int, double> attempted;
    std::map<int, Value> locked;
    std::set<int> invalidated;
    Value *command = nullptr;
    const OfflineQueryRoute *route = nullptr;
    int navigation = 0, driver = 0;
    std::vector<std::pair<int, bool>> waiting, frame_waiting;
    std::vector<int> requested;
    std::set<int> requested_set, priority, attempted_receipts, deferred_receipts;
    std::map<int, bool> waiting_initial, request_uncached;
    std::map<int, int> results;
    bool receipt_frame = false, initial_first = false;
    int receipt_budget = 0;
    Diagnostics *diagnostics = nullptr;
    double current_now = 0;
    std::array<int, 5> probe_totals{{0, 0, 0, 0, 0}};
    Motion(const Value &v, std::map<int, std::unique_ptr<Bot>> &states, Engine &e)
        : config(v), bots(states), engine(e), factors(v.get("factors")) {
        const Value &t = v.get("tuning");
#define LOAD_TUNING(name) tuning.name = field(t, #name);
        OFFLINE_MOTION_CONSTANTS(LOAD_TUNING)
#undef LOAD_TUNING
        flow.probe_seconds = field(v, "probe_seconds");
        navigation = integer(v, "navigation");
        driver = integer(v, "driver");
        for (const auto &item : bots) {
            const Value &p = item.second->config.get("motion").get("physics");
            if (p.kind != Value::Object)
                continue;
            profiles[item.first] = params(p);
        }
    }
    static offline_motion::Params params(const Value &v) {
        offline_motion::Params p;
        double *values[] = {&p.mass,       &p.powerW,   &p.nativePowerRatio, &p.specificFriction,
                            &p.brakeDecel, &p.speedFwd, &p.speedBwd,         &p.rotSpd};
        const char *names[] = {"mass",       "powerW",   "nativePowerRatio", "specificFriction",
                               "brakeDecel", "speedFwd", "speedBwd",         "rotSpd"};
        for (size_t i = 0; i < 8; ++i)
            *values[i] = field(v, names[i], i == 2 ? 1 : 0);
        const Value &terrain = v.get("terrainResist");
        if (terrain.kind != Value::Array || terrain.size() != 3)
            throw std::invalid_argument("kernel motion terrain producer");
        for (size_t i = 0; i < 3; ++i)
            p.terrainResist[i] = terrain[i].number();
        if (p.mass <= 0 || p.terrainResist[0] <= 0 || p.terrainResist[1] <= 0 ||
            p.terrainResist[2] <= 0)
            throw std::invalid_argument("kernel motion producer");
        return p;
    }
    static offline_nav::Point point(const Value &v) {
        return offline_nav::Point(field(v, sf::x), field(v, sf::y), field(v, sf::z));
    }
    static offline_nav::Point point(const Value &v, offline_nav::Point fallback) {
        return v.kind == Value::Array && v.size() == 3
                   ? offline_nav::Point(v[0].number(), v[1].number(), v[2].number())
                   : fallback;
    }
    static Value value(offline_nav::Point p) {
        Value v = Value::array();
        v.append(Value(p.x));
        v.append(Value(p.y));
        v.append(Value(p.z));
        return v;
    }
    static void assign(Value &s, offline_nav::Point p) {
        s[sf::x] = Value(p.x);
        s[sf::y] = Value(p.y);
        s[sf::z] = Value(p.z);
    }
    static void destructible(Value &s, bool has, double speed) {
        if (has)
            s[sf::destructible_contact_speed] = Value(speed);
        else
            s.erase(sf::destructible_contact_speed);
    }
    void begin() {
        frame_waiting.clear();
        std::set<int> seen;
        for (const auto &v : waiting) {
            auto at = bots.find(v.first);
            if (!seen.count(v.first) && at != bots.end() &&
                flag(at->second->state, sf::alive, true)) {
                frame_waiting.push_back(v);
                seen.insert(v.first);
            }
        }
        receipt_budget = integer(config, "receipt_budget");
        priority.clear();
        waiting_initial.clear();
        initial_first = false;
        for (const auto &v : frame_waiting) {
            waiting_initial[v.first] = v.second;
            if (v.second)
                initial_first = true;
        }
        for (const auto &v : frame_waiting)
            if ((!initial_first || v.second) &&
                priority.size() < static_cast<size_t>(receipt_budget))
                priority.insert(v.first);
        requested.clear();
        requested_set.clear();
        request_uncached.clear();
        attempted_receipts.clear();
        results.clear();
        deferred_receipts.clear();
        receipt_frame = true;
    }
    void finish() {
        if (!receipt_frame)
            return;
        waiting.clear();
        std::set<int> seen;
        auto append = [&](int id, bool initial) {
            if (!seen.count(id)) {
                waiting.push_back({id, initial});
                seen.insert(id);
            }
        };
        for (const auto &v : frame_waiting)
            if (requested_set.count(v.first) && !attempted_receipts.count(v.first))
                append(v.first, request_uncached[v.first]);
        for (int id : requested)
            if (!attempted_receipts.count(id))
                append(id, request_uncached[id]);
        for (const auto &v : frame_waiting)
            if (deferred_receipts.count(v.first))
                append(v.first, false);
        for (int id : requested)
            if (deferred_receipts.count(id))
                append(id, false);
        receipt_frame = false;
    }
    int engine_event(double *packet, int count, Value source) {
        int kind = static_cast<int>(packet[0]);
        if (kind == 610 || kind == 611 || kind == 635)
            ++probe_totals[4];
        else if (kind == 630)
            ++probe_totals[3];
        if (kind == 615) {
            int id = static_cast<int>(packet[1]);
            auto cache = flow.caches.find(id);
            bool corridor = false, receipt = false;
            double yaw = packet[5], speed = packet[6], dt = packet[7], now = packet[8];
            offline_nav::Point p(packet[2], packet[3], packet[4]);
            if (packet[9] == 0 && speed < 0)
                yaw += 3.141592653589793;
            if (packet[9] != 0 && speed < 0)
                yaw += 3.141592653589793;
            if (cache != flow.caches.end() && cache->second.has && cache->second.result.kind == 2) {
                const auto &c = cache->second;
                const auto &probe = c.result;
                receipt = probe.receipt.kind == 2 &&
                          offline_motion::reusable(c, p, yaw, speed, now, false, dt);
                corridor = probe.receipt.kind == 2
                               ? (!probe.deferred && probe.is_clear() &&
                                  offline_motion::contains(probe.receipt, p, yaw, speed, dt))
                               : probe.pending && offline_motion::reusable(c, p, yaw, speed, now,
                                                                           false, dt, true);
            }
            packet[20] = corridor;
            packet[21] = receipt;
            const Bot &bot = *bots.at(id);
            const Value &world = bot.config.get("motion").get("world");
            if (world.truth()) {
                WorldResolver resolver(engine, source);
                packet[0] = resolver.resolve(source, world, bot.turn_speed, packet);
                return 0;
            }
            source = source.copy();
            source[sf::_kernel_turn_speed] = Value(bot.turn_speed);
        }
        std::vector<double> args(packet, packet + count);
        auto answer = engine.query(760, source, Value::object(), args);
        std::copy(answer.begin(), answer.begin() + count, packet);
        return 0;
    }
    int receipt(double *packet, int count, Value source) {
        if (!flag(config, "has_receipt")) {
            std::fill(packet, packet + 10, 0);
            return 0;
        }
        auto defer = [&]() {
            std::fill(packet, packet + 10, 0);
            packet[0] = 3;
            return 0;
        };
        if (!receipt_frame)
            return defer();
        int id = static_cast<int>(packet[1]);
        if (!requested_set.count(id)) {
            requested.push_back(id);
            requested_set.insert(id);
            auto at = waiting_initial.find(id);
            request_uncached[id] = at == waiting_initial.end() ? packet[7] != 0 : at->second;
        }
        if (attempted_receipts.count(id)) {
            if (results[id] == 1) {
                std::fill(packet, packet + 10, 0);
                packet[0] = 1;
                return 0;
            }
            return defer();
        }
        if ((initial_first && !request_uncached[id]) || (!priority.empty() && !priority.count(id)))
            return defer();
        priority.erase(id);
        if (receipt_budget <= 0)
            return defer();
        --receipt_budget;
        attempted_receipts.insert(id);
        engine_event(packet, count, source);
        results[id] = static_cast<int>(packet[0]);
        if (packet[0] == 3)
            deferred_receipts.insert(id);
        return 0;
    }
    int event(double *packet, int count) {
        int kind = static_cast<int>(packet[0]);
        if (kind < 610 || kind > 635)
            return route ? route->forward(packet, count) : 18;
        int id = static_cast<int>(packet[1]);
        auto at = bots.find(id);
        if (at == bots.end())
            throw std::invalid_argument("kernel motion callback actor");
        Bot &bot = *at->second;
        Value &s = bot.state;
        if (kind == 610 || kind == 630)
            return engine_event(packet, count, s);
        if (kind == 611)
            return receipt(packet, count, s);
        if (kind == 612) {
            offline_motion::Values values{packet, 2};
            auto cache = values.cache();
            const auto &p = cache.result;
            packet[0] =
                cache.has &&
                (p.truth || (p.kind == 2 && p.copied &&
                             (!p.clear || p.collision || p.pending || p.receipt.kind != 0)));
            return 0;
        }
        if (kind == 613) {
            int phase = static_cast<int>(packet[2]);
            if (phase == 0) {
                s[sf::hull_aiming] = Value(packet[3] != 0);
                attempted[id] = packet[5];
                if (packet[4] != 0) {
                    if (command) {
                        (*command)["fire_allowed"] = Value(false);
                        (*command)["throttle"] = Value(0.0);
                        (*command)["turn"] = Value(0.0);
                        (*command)["movement_intent"] = Value(false);
                    }
                    for (const char *name : {"speed", "push_x", "push_z"})
                        s[name] = Value(0.0);
                    s[sf::movement_dir] = Value(0);
                    s[sf::rotation_dir] = Value(0);
                    bot.turn_speed = 0;
                    locked[id] = value(
                        offline_nav::Point(field(s, sf::x), field(s, sf::z), field(s, sf::yaw)));
                }
            } else if (phase == 1) {
                s[sf::movement_dir] = Value(static_cast<int>(packet[3]));
                s[sf::rotation_dir] = Value(static_cast<int>(packet[4]));
                attempted[id] = packet[9];
                if (diagnostics && command) {
                    offline_motion::Values values{packet, 10};
                    auto probe = values.probe();
                    Value v = Value::object();
                    if (probe.kind == 2) {
                        v["clear"] = Value(probe.clear);
                        v["collision"] = Value(probe.collision);
                        v["water"] = Value(probe.water);
                        v["deferred"] = Value(probe.deferred);
                        v["slope"] = Value(probe.slope);
                    }
                    diagnostics->begin(bot, *command, packet[5], packet[6], packet[7] != 0,
                                       packet[8] != 0, v, current_now,
                                       grinds.count(id) ? grinds.at(id) : 0);
                }
            } else if (phase == 2) {
                s[sf::yaw] = Value(packet[3]);
                bot.turn_speed = packet[4];
                s[sf::rotation_dir] = Value(static_cast<int>(packet[5]));
                s[sf::movement_dir] = Value(static_cast<int>(packet[6]));
                s[sf::last_drive_pitch] = Value(packet[7]);
                attempted[id] = packet[13];
                destructible(s, packet[14] != 0, packet[15]);
            } else
                throw std::invalid_argument("kernel motion phase");
            return 0;
        }
        if (kind == 614) {
            invalidated.insert(id);
            s.erase(sf::destructible_contact_speed);
            return 0;
        }
        if (kind == 615) {
            Value source = s;
            if (packet[9] != 0) {
                source = s.copy();
                source[sf::movement_dir] = Value(0);
            }
            return engine_event(packet, count, source);
        }
        if (kind == 616) {
            destructible(s, packet[5] != 0, packet[6]);
            if (packet[7] != 0)
                grinds[id] = static_cast<int>(packet[8]);
            return engine_event(packet, count, s);
        }
        if (kind == 617) {
            Value trace = s.get(sf::_motion_stall_pending);
            if (trace.kind == Value::Object) {
                const char *status[] = {"clear", "crushed", "soft", "cap_crushed", "hard"};
                trace["world_status"] = Value(status[static_cast<int>(packet[2])]);
                trace["hard_contact"] = Value(packet[3] != 0);
                trace["integrated"] = offline_kernel::point(packet[4], packet[5], packet[6]);
                trace["world_speed"] = Value(packet[7]);
            }
            return 0;
        }
        if (kind == 631) {
            Value trace = s.get(sf::_motion_stall_pending);
            if (trace.kind == Value::Object) {
                if (packet[2] == 0) {
                    trace["support_highest"] = packet[3] ? Value(packet[4]) : Value();
                    trace["support_centre"] = packet[5] ? Value(packet[6]) : Value();
                    trace["grounded_before"] = Value(packet[7] != 0);
                } else {
                    trace["support_limit"] = Value(packet[3]);
                    trace["support_rise_obstacle"] = Value(packet[4] != 0);
                    trace["support_rise_continuous"] = Value(packet[5] != 0);
                }
            }
            return 0;
        }
        if (kind == 635) {
            s[sf::speed] = Value(packet[9]);
            return engine_event(packet, count, s);
        }
        return route ? route->forward(packet, count) : 18;
    }
    static int callback(void *owner, double *packet, int count) {
        return static_cast<Motion *>(owner)->event(packet, count);
    }
    struct Scope {
        Motion &motion;
        const OfflineQueryRoute *previous;
        OfflineQueryRoute current;
        explicit Scope(Motion &m) : motion(m), previous(m.route), current(Motion::callback, &m) {
            m.route = &current;
        }
        ~Scope() { motion.route = previous; }
    };
    void invalidate(int id, double yaw) {
        invalidated.insert(id);
        flow.caches.erase(id);
        if (driver)
            offline_driver_remember(driver, id, yaw, true, 5.0);
    }
    void step(Bot &bot, Value &order, const Value &target, double dt, double now, bool decision_due,
              bool refresh, bool siege_lock, double siege_yaw, bool baked_escape) {
        current_now = now;
        Value &s = bot.state;
        const Value &c = bot.config.get("motion");
        int id = integer(s, sf::id);
        offline_motion::Input in;
        in.bot = id;
        in.navigation = navigation;
        in.driver = driver;
        in.position = point(s);
        in.aim = point(order.get("aim_position"), point(target.get(sf::position), in.position));
        if (order.get("move_position").kind != Value::Null)
            in.move = point(order.get("move_position"), in.position);
        in.yaw = field(s, sf::yaw);
        in.speed = field(s, sf::speed);
        in.turn_speed = bot.turn_speed;
        in.half_length = field(s, sf::half_length, 3.5);
        in.half_width = field(s, sf::half_width, 1.7);
        in.throttle = field(order, "throttle");
        in.turn = field(order, "turn");
        const Value &limits = c.get("yaw_limits");
        in.minimum_yaw = limits[0].number();
        in.maximum_yaw = limits[1].number();
        std::set<std::string> destroyed = names(s.get(sf::critical).get("destroyed"));
        in.mobility_blocked = flag(s, sf::_overturned) || destroyed.count("engineHealth") ||
                              destroyed.count("leftTrackHealth") ||
                              destroyed.count("rightTrackHealth");
        in.mobility = !in.mobility_blocked && std::abs(in.throttle) > .01
                          ? factors.stat(s, *bot.critical, "mobility")
                          : 1;
        in.step = dt;
        in.now = now;
        in.water = field(s, sf::_water_depth, -1);
        in.tick_siege_yaw = siege_yaw;
        const std::string recovery = order.get("recovery_mode").text("drive");
        in.recovery = recovery == "drive"            ? 0
                      : recovery == "avoid"          ? 1
                      : recovery == "blocked"        ? 2
                      : recovery == "reverse_turn"   ? 3
                      : recovery == "pivot_recovery" ? 4
                                                     : 5;
        auto grind = grinds.find(id);
        in.grind_present = grind != grinds.end();
        in.grind = in.grind_present ? grind->second : 0;
        in.has_target =
            target.kind != Value::Null && order.get("combat_mode").text() != "base_defense";
        in.movement = flag(order, "movement_intent", true);
        in.airborne = flag(s, sf::airborne);
        in.grounded = flag(s, sf::grounded_once);
        in.siege_locked = siege_lock;
        in.bake_admitted = flag(config, "bake_admitted");
        in.baked_escape = baked_escape;
        in.decision_due = decision_due;
        in.refresh = refresh;
        in.has_resolver = flag(config, "has_resolver");
        in.has_report = flag(config, "has_report");
        if (integer(s, sf::siege_state) == integer(config, "siege_enabled") &&
            c.get("siege_limit").kind != Value::Null)
            in.siege_limit = c.get("siege_limit").number();
        if (s.has(sf::destructible_contact_speed))
            in.destructible_speed = field(s, sf::destructible_contact_speed);
        Scope scope(*this);
        Value *previous = command;
        command = &order;
        offline_motion::Output out(in);
        try {
            out = flow.step(in, profiles.at(id), tuning,
                            navigation ? &offline_runtime_navigation(navigation) : nullptr);
        } catch (...) {
            command = previous;
            throw;
        }
        command = previous;
        assign(s, out.position);
        s[sf::yaw] = Value(out.yaw);
        s[sf::speed] = Value(out.speed);
        bot.turn_speed = out.turn_speed;
        s[sf::last_drive_pitch] = Value(out.drive_pitch);
        attempted[id] = out.attempted_yaw;
        s[sf::movement_dir] = Value(out.movement);
        s[sf::rotation_dir] = Value(out.rotation);
        if (out.grind_present)
            grinds[id] = out.grind;
        destructible(s, out.destructible_speed.has, out.destructible_speed.value);
    }
    int landing(Bot &bot, double speed) {
        Value &s = bot.state;
        double lx = field(s, sf::air_lateral_x), lz = field(s, sf::air_lateral_z),
               lateral = std::sqrt(lx * lx + lz * lz);
        if (lateral > .01)
            s[sf::slide_speed] = Value(std::max(field(s, sf::slide_speed), lateral));
        s[sf::air_lateral_x] = Value(0.0);
        s[sf::air_lateral_z] = Value(0.0);
        speed = std::sqrt(speed * speed + lateral * lateral);
        int maximum = std::max(1, integer(s, sf::max_health, integer(s, sf::health, 1)));
        int damage = speed <= tuning.FALL_SAFE_SPEED
                         ? 0
                         : static_cast<int>(maximum * (speed - tuning.FALL_SAFE_SPEED) *
                                            tuning.FALL_DMG_PER_MS);
        if (damage <= 0)
            return 0;
        int health = std::max(0, integer(s, sf::health, maximum) - damage);
        s[sf::health] = Value(health);
        s[sf::display_health] = Value(health);
        s[sf::alive] = Value(health > 0);
        if (health > 0)
            return damage;
        Critical terminal(s.get(sf::critical), *bot.critical);
        s[sf::critical] = terminal.terminal();
        s[sf::combat_fire_elapsed] = Value(0.0);
        s[sf::combat_fire_timer] = Value(0.0);
        bot.death(3, 0);
        s[sf::push_x] = Value(0.0);
        s[sf::push_z] = Value(0.0);
        bot.turn_speed = 0;
        return damage;
    }
    bool vertical(Bot &bot, double dt, const Value &tick = Value(), double yaw = 0) {
        Value &s = bot.state;
        offline_motion::Vertical v;
        v.bot = integer(s, sf::id);
        v.position = point(s);
        v.yaw = field(s, sf::yaw);
        v.speed = field(s, sf::speed);
        v.half_length = std::max(1.5, field(s, sf::half_length, 3.5));
        v.step = dt;
        v.vertical = field(s, sf::vertical_speed);
        v.pitch = field(s, sf::last_drive_pitch);
        v.airborne = flag(s, sf::airborne);
        v.grounded = flag(s, sf::grounded_once);
        v.trace = s.get(sf::_motion_stall_pending).kind == Value::Object;
        if (v.trace)
            s[sf::_motion_stall_pending]["suspension"] = Value(false);
        if (tick.kind != Value::Null)
            v.tick = point(tick, v.position);
        Scope scope(*this);
        auto result = offline_motion::vertical_step(flow, tuning, v);
        assign(s, result.position);
        s[sf::speed] = Value(result.speed);
        s[sf::vertical_speed] = Value(result.vertical);
        s[sf::airborne] = Value(result.airborne);
        s[sf::grounded_once] = Value(result.grounded);
        if (result.blocked) {
            s[sf::movement_dir] = Value(0);
            s[sf::rotation_dir] = Value(0);
            s[sf::push_x] = Value(0.0);
            s[sf::push_z] = Value(0.0);
            s.erase(sf::destructible_contact_speed);
            bot.turn_speed = 0;
            invalidate(v.bot, yaw);
            return true;
        }
        if (result.landed)
            landing(bot, result.impact);
        return false;
    }
    bool safe(offline_nav::Point p) const {
        if (!navigation)
            return true;
        const auto &grid = *offline_runtime_navigation(navigation).grid;
        double x = std::round((p.x - grid.data->ox) / grid.data->cell),
               z = std::round((p.z - grid.data->oz) / grid.data->cell);
        return x >= 0 && z >= 0 && x < grid.data->width && z < grid.data->height &&
               !(grid.data->hazards[static_cast<int>(z) * grid.data->width + static_cast<int>(x)] &
                 9);
    }
    bool guard(Bot &bot, offline_nav::Point tick, bool was_safe, double attempted_yaw) {
        if (!navigation)
            return false;
        Value &s = bot.state;
        double yaw = field(s, sf::yaw);
        auto &grid = *offline_runtime_navigation(navigation).grid;
        offline_nav::Point current = point(s);
        if (offline_motion::boundary(grid, tick, yaw, current, yaw, field(s, sf::half_length, 3.5),
                                     field(s, sf::half_width, 1.7)) &&
            (!was_safe || safe(current)))
            return false;
        assign(s, tick);
        for (const char *name : {"speed", "push_x", "push_z", "vertical_speed"})
            s[name] = Value(0.0);
        s[sf::movement_dir] = Value(0);
        s[sf::rotation_dir] = Value(0);
        s[sf::airborne] = Value(false);
        invalidate(integer(s, sf::id), attempted_yaw);
        return true;
    }
    bool slope(Bot &bot, int tier, bool allow_ungrounded = false) {
        Value &s = bot.state;
        if (flag(s, sf::airborne) || (!allow_ungrounded && !flag(s, sf::grounded_once)))
            return false;
        const Value &prior = s.get(sf::pose_sample);
        double yaw = field(s, sf::yaw), x = field(s, sf::x), z = field(s, sf::z);
        if (prior.kind == Value::Array && prior.size() == 3 &&
            std::abs(x - prior[0].number()) < config.get("slope_metres")[tier].number() &&
            std::abs(z - prior[1].number()) < config.get("slope_metres")[tier].number() &&
            std::abs(yaw - prior[2].number()) < config.get("slope_radians")[tier].number())
            return false;
        double suspension = field(s, sf::suspension_pitch),
               pitch = field(s, sf::terrain_pitch, field(s, sf::pitch) - suspension);
        Scope scope(*this);
        auto result = offline_motion::slope(
            flow, integer(s, sf::id), point(s), yaw, field(s, sf::half_length, 3.5),
            field(s, sf::half_width, 1.7), pitch, field(s, sf::roll));
        s[sf::terrain_pitch] = Value(result.first);
        s[sf::pitch] = Value(result.first + suspension);
        s[sf::roll] = Value(result.second);
        Value marker = Value::array();
        marker.append(Value(x));
        marker.append(Value(z));
        marker.append(Value(yaw));
        s[sf::pose_sample] = marker;
        return true;
    }
};
} // namespace offline_kernel
#endif
