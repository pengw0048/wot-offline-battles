#ifndef OFFLINE_EXPERIMENT_KERNEL_DRIVER_H
#define OFFLINE_EXPERIMENT_KERNEL_DRIVER_H
#include "kernel_traffic.h"

namespace offline_kernel {
// Body snapshots are built afresh. Integer dictionary iteration affects the
// first already-overlapping neighbour chosen by #1513's Python 2 driver.
inline std::vector<int> integer_dict_order(const std::vector<int> &insertions, bool py2) {
    std::vector<int> insertion;
    std::set<int> seen;
    for (int id : insertions)
        if (seen.insert(id).second)
            insertion.push_back(id);
    if (!py2)
        return insertion;
    std::vector<int> table(8, -1);
    size_t used = 0;
    auto put = [](std::vector<int> &values, int id) {
        size_t mask = values.size() - 1, hash = static_cast<size_t>(id), slot = hash & mask,
               perturb = hash;
        while (values[slot] != -1) {
            slot = (slot * 5 + perturb + 1) & mask;
            perturb >>= 5;
        }
        values[slot] = id;
    };
    for (int id : insertion) {
        put(table, id);
        ++used;
        if (used * 3 >= table.size() * 2) {
            size_t size = 8;
            while (size <= used * 4)
                size *= 2;
            std::vector<int> grown(size, -1);
            for (int v : table)
                if (v != -1)
                    put(grown, v);
            table.swap(grown);
        }
    }
    std::vector<int> result;
    for (int id : table)
        if (id != -1)
            result.push_back(id);
    return result;
}
struct Driver {
    Value config;
    Motion &motion;
    Routes &routes;
    Bot *active = nullptr;
    const OfflineQueryRoute *route = nullptr;
    std::map<std::pair<double, double>, bool> probes;
    struct Coast {
        double speed, pitch;
        bool steering;
        double distance;
    };
    std::map<int, Coast> coast;
    int handle;
    Driver(const Value &c, Motion &m, Routes &r)
        : config(c), motion(m), routes(r), handle(integer(c, "driver")) {}
    static void point(std::vector<double> &out, offline_nav::Point p) {
        out.push_back(p.x);
        out.push_back(p.y);
        out.push_back(p.z);
    }
    bool clear(Bot &bot, double yaw, offline_nav::Optional<double> distance = {}) {
        Value &s = bot.state;
        auto p = Routes::point(s);
        int id = integer(s, "id");
        if (routes.nav) {
            int value = offline_navigation_local_query(
                integer(config, "navigation"), id, 1, p.x, p.y, p.z, yaw,
                field(s, "half_length", 3.5), field(s, "half_width", 1.7),
                field(s, "_water_depth", -1) > .9, flag(config, "bake_admitted"), distance.has,
                distance.value);
            if (value != 2)
                return value != 0;
        }
        auto key =
            std::make_pair(rounded(angle(yaw), 4), distance.has ? rounded(distance.value, 2) : -1);
        auto cached = probes.find(key);
        if (cached != probes.end())
            return cached->second;
        double packet[128] = {610,
                              static_cast<double>(id),
                              p.x,
                              p.y,
                              p.z,
                              yaw,
                              field(s, "speed"),
                              static_cast<double>(distance.has),
                              distance.value,
                              static_cast<double>(distance.has)};
        motion.engine_event(packet, 128, s);
        bool result = packet[0] == 0 || (packet[0] == 2 && packet[7] != 0) ||
                      (packet[0] == 2 ? (packet[3] != 0 && packet[4] == 0 && packet[5] == 0 &&
                                         std::abs(packet[6]) <= .55)
                                      : packet[2] != 0);
        probes[key] = result;
        return result;
    }
    int event(double *packet, int count) {
        int kind = static_cast<int>(packet[0]);
        if ((kind != 1 && kind != 2) || !active)
            return route->forward(packet, count);
        if (kind == 1)
            packet[0] = clear(*active, packet[1],
                              packet[2] < 0 ? offline_nav::Optional<double>()
                                            : offline_nav::Optional<double>(packet[2]));
        else {
            auto p = Routes::point(active->state);
            packet[0] = !routes.nav || routes.nav->grid->pose(
                                           p, packet[1], field(active->state, "half_length", 3.5),
                                           field(active->state, "half_width", 1.7));
        }
        return 0;
    }
    static int callback(void *owner, double *packet, int count) {
        return static_cast<Driver *>(owner)->event(packet, count);
    }
    Value drive(Bot &bot, Value &state, const Value &order, bool gun_pending) {
        int id = integer(bot.state, "id");
        auto current = Routes::point(state.get("position"));
        const Value &raw_aim = order.get("aim_position"), &raw_move = order.get("move_position"),
                    &raw_face = order.get("face_position");
        auto aim = raw_aim.kind == Value::Null ? Routes::point(raw_move, current)
                                               : Routes::point(raw_aim, current),
             move = raw_move.kind == Value::Null ? aim : Routes::point(raw_move, current),
             face = raw_face.kind == Value::Null ? move : Routes::point(raw_face, current);
        auto target = routes.target(id, current, move, order, state, gun_pending);
        std::string mode = order.get("combat_mode").text("route");
        bool stop = flag(state, "navigation_stop_at_target", mode != "route" && mode != "advance"),
             movement = order.get("throttle_override").kind == Value::Null ||
                        field(order, "throttle_override") > 0;
        double requested_x = move.x - current.x, requested_z = move.z - current.z,
               dx = target.x - current.x, dz = target.z - current.z;
        bool wait = movement && requested_x * requested_x + requested_z * requested_z > 225 &&
                    dx * dx + dz * dz <= 1.5 * 1.5;
        double throttle = 0, turn = 0, yaw = field(state, "yaw");
        std::string recovery = "nav_wait";
        if (!wait) {
            std::vector<double> packet = {208, static_cast<double>(handle), static_cast<double>(id),
                                          static_cast<double>(integer(state, "slot"))};
            point(packet, current);
            packet.insert(packet.end(),
                          {field(state, "yaw"), field(state, "speed"), field(state, "dt")});
            point(packet, target);
            const Value &neighbours = state.get("neighbours");
            packet.insert(packet.end(),
                          {field(state, "half_length", 3.5), field(state, "half_width", 1.7),
                           static_cast<double>(movement),
                           static_cast<double>(state.get("stopping_distance").kind != Value::Null),
                           field(state, "stopping_distance"), static_cast<double>(stop),
                           field(state, "decision_horizon"), 1, rounded(target.x, 2),
                           rounded(target.z, 2), static_cast<double>(neighbours.size())});
            for (const Value &peer : elements(neighbours)) {
                point(packet, Routes::point(peer.get("position")));
                packet.insert(packet.end(),
                              {field(peer, "yaw"),
                               field(peer, "half_length", field(state, "half_length", 3.5)),
                               field(peer, "half_width", field(state, "half_width", 1.7)),
                               static_cast<double>(peer.get("id").kind != Value::Null),
                               field(peer, "id"), static_cast<double>(flag(peer, "alive", true))});
            }
            packet.insert(packet.end(),
                          {static_cast<double>(integer(config, "navigation")),
                           static_cast<double>(field(bot.state, "_water_depth", -1) > .9),
                           static_cast<double>(flag(config, "bake_admitted"))});
            size_t offset = packet.size();
            packet.resize(offset + 7, 0);
            active = &bot;
            OfflineQueryRoute scope(callback, this);
            route = &scope;
            try {
                offline_driver_dispatch(packet.data(), packet.size());
            } catch (...) {
                route = nullptr;
                active = nullptr;
                throw;
            }
            route = nullptr;
            active = nullptr;
            if (packet[offset] != 0)
                throw std::runtime_error("native driver did not complete");
            throttle = packet[offset + 1];
            turn = packet[offset + 2];
            yaw = packet[offset + 3];
            const char *modes[] = {"arrived",      "blocked", "pivot_recovery",
                                   "reverse_turn", "avoid",   "drive"};
            recovery = modes[static_cast<int>(packet[offset + 4])];
        }
        double face_x = face.x - current.x, face_z = face.z - current.z;
        if ((recovery == "arrived" || recovery == "nav_wait") &&
            face_x * face_x + face_z * face_z > .01) {
            yaw = std::atan2(face_x, face_z);
            turn = clamp(gun_wrap(yaw - field(state, "yaw")) / .58, -1, 1);
        }
        Value result = Value::object();
        result["bot_id"] = Value(id);
        result["target_id"] = order.get("target_id");
        result["aim_position"] = Routes::value(aim);
        result["face_position"] = Routes::value(face);
        result["fire_range"] = Value(field(order, "fire_range"));
        result["move_position"] = Routes::value(target);
        result["combat_mode"] = Value(mode);
        result["fire_allowed"] = Value(flag(order, "fire_allowed"));
        result["shell_index"] = Value(integer(order, "shell_index"));
        result["throttle"] = Value(throttle);
        result["turn"] = Value(turn);
        result["target_yaw"] = Value(yaw);
        result["recovery_mode"] = Value(recovery);
        result["movement_intent"] = Value(movement);
        if (order.get("hull_angle_degrees").kind != Value::Null)
            result["hull_angle_degrees"] = Value(field(order, "hull_angle_degrees"));
        if (order.get("throttle_override").kind != Value::Null &&
            (recovery == "drive" || recovery == "arrived") &&
            std::abs(gun_wrap(yaw - field(state, "yaw"))) < .65)
            result["throttle"] = Value(clamp(field(order, "throttle_override"), -1, 1));
        return result;
    }
    double stopping(Bot &bot, const Value &command) {
        int id = integer(bot.state, "id");
        double speed = std::abs(field(bot.state, "speed")),
               pitch = field(bot.state, "last_drive_pitch");
        bool steering = std::abs(field(command, "turn")) > .01;
        auto at = coast.find(id);
        if (at != coast.end() && at->second.speed == speed && at->second.pitch == pitch &&
            at->second.steering == steering)
            return at->second.distance;
        double result = 0, current = speed, dt = field(config, "publication_seconds");
        if (current > field(config, "speed_epsilon")) {
            bool settled = false;
            for (int i = 0; i < 4096; ++i) {
                double next =
                    offline_motion::longitudinal(motion.tuning, motion.profiles.at(id), current, 0,
                                                 steering, pitch, dt, false, 0, false);
                if (!std::isfinite(next)) {
                    result = std::numeric_limits<double>::infinity();
                    settled = true;
                    break;
                }
                if (next <= field(config, "speed_epsilon")) {
                    result += std::max(0.0, next) * dt;
                    settled = true;
                    break;
                }
                if (next >= current) {
                    result = std::numeric_limits<double>::infinity();
                    settled = true;
                    break;
                }
                result += next * dt;
                current = next;
            }
            if (!settled)
                result = std::numeric_limits<double>::infinity();
        }
        coast[id] = Coast{speed, pitch, steering, result};
        return result;
    }
};
} // namespace offline_kernel
#endif
