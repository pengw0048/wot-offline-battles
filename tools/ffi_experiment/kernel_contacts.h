#ifndef OFFLINE_EXPERIMENT_KERNEL_CONTACTS_H
#define OFFLINE_EXPERIMENT_KERNEL_CONTACTS_H
#include "kernel_driver.h"

namespace offline_kernel {
struct TankBody {
    Value raw;
    int id, team;
    bool alive, impulse, has_y;
    double x, y, z, yaw, pitch, roll, mass, vx, vy, vz;
    std::array<double, 4> shape;
    explicit TankBody(const Value &v)
        : raw(v), id(integer(v, sf::id, -1)), team(integer(v, sf::team)),
          alive(flag(v, sf::alive, true)), impulse(flag(v, "impulse", true)),
          has_y(v.get(sf::y).kind != Value::Null), x(field(v, sf::x)), y(field(v, sf::y)),
          z(field(v, sf::z)), yaw(field(v, sf::yaw)), pitch(field(v, sf::pitch)),
          roll(field(v, sf::roll)), mass(std::max(1.0, field(v, sf::mass, 1))), vx(field(v, "vx")),
          vy(field(v, "vy")), vz(field(v, "vz")) {
        const Value &s = v.get("shape");
        if (s.kind != Value::Array || s.size() != 4)
            throw std::invalid_argument("kernel tank shape producer");
        for (size_t i = 0; i < 4; ++i)
            shape[i] = s[i].number();
    }
    using V = std::array<double, 2>;
    std::array<V, 2> axes() const {
        double s = std::sin(yaw), c = std::cos(yaw);
        return {{{{c, -s}}, {{s, c}}}};
    }
    double radius(V axis) const {
        auto a = axes();
        return shape[0] * std::abs(axis[0] * a[0][0] + axis[1] * a[0][1]) +
               shape[1] * std::abs(axis[0] * a[1][0] + axis[1] * a[1][1]);
    }
    double radius() const { return std::sqrt(shape[0] * shape[0] + shape[1] * shape[1]); }
    V velocity() const { return {{vx, vz}}; }
    bool same_team(const TankBody &other) const {
        return (team == 1 || team == 2) && team == other.team;
    }
    bool contains(const Value &point, double slop) const {
        if (point.kind != Value::Array || point.size() != 3)
            return false;
        double sy = std::sin(yaw), cy = std::cos(yaw), sp = std::sin(pitch), cp = std::cos(pitch),
               sr = std::sin(roll), cr = std::cos(roll);
        auto rotate = [&](double x, double y, double z) {
            double after_y = cp * y - sp * z, after_z = sp * y + cp * z;
            return std::array<double, 3>{{cy * x + sy * after_z, after_y, -sy * x + cy * after_z}};
        };
        std::array<std::array<double, 3>, 3> axes = {
            {rotate(cr, sr, 0), rotate(-sr, cr, 0), rotate(0, 0, 1)}};
        std::array<double, 3> delta{
            {point[0].number() - x, point[1].number() - y, point[2].number() - z}},
            local{};
        for (size_t i = 0; i < 3; ++i) {
            double sum = 0;
            for (size_t j = 0; j < 3; ++j)
                sum += axes[i][j] * delta[j];
            local[i] = sum;
        }
        return std::isfinite(local[0]) && std::isfinite(local[1]) && std::isfinite(local[2]) &&
               std::abs(local[0]) <= shape[0] + slop && local[1] >= shape[2] - slop &&
               local[1] <= shape[3] + slop && std::abs(local[2]) <= shape[1] + slop;
    }
};
struct TankContacts {
    using V = std::array<double, 2>;
    using Pair = std::pair<int, int>;
    using Contact = std::array<double, 3>;
    using OptionalContact = offline_nav::Optional<Contact>;
    struct Response {
        V correction{{0, 0}}, delta{{0, 0}};
        Value events = Value::array(), diagnostics = Value::array();
        std::set<Pair> contacts;
    };
    std::map<int, std::unique_ptr<Bot>> &bots;
    Motion &motion;
    Engine &engine;
    Value config;
    std::map<Pair, double> cooldowns;
    std::set<Pair> active;
    std::map<int, double> leases;
    std::map<int, int> human_ack;
    std::map<Pair, Value> human_reports;
    std::map<Pair, Value> frame_armors;
    int sequence = 0;
    TankContacts(const Value &c, std::map<int, std::unique_ptr<Bot>> &b, Motion &m, Engine &e)
        : bots(b), motion(m), engine(e), config(c), sequence(integer(c, "sequence")) {}
    static Value array(V v) { return tuple_value({Value(v[0]), Value(v[1])}); }
    static Value array(Contact v) { return vector3(v[0], v[1], v[2]); }
    static Value shape(const TankBody &body) {
        Value out = Value::array();
        for (double v : body.shape)
            out.append(Value(v));
        return out;
    }
    static OptionalContact contact(const TankBody &a, const TankBody &b, bool impact = false) {
        auto axes_a = a.axes(), axes_b = b.axes();
        double dx = a.x - b.x, dz = a.z - b.z, rx = a.vx - (b.alive ? b.vx : 0),
               rz = a.vz - (b.alive ? b.vz : 0);
        offline_nav::Optional<double> best;
        Contact result{};
        for (V axis : {axes_a[0], axes_a[1], axes_b[0], axes_b[1]}) {
            double extent = a.radius(axis) + b.radius(axis), distance = dx * axis[0] + dz * axis[1],
                   overlap = extent - std::abs(distance);
            if (overlap <= 0)
                return {};
            if (!impact) {
                if (!best.has || overlap < best.value) {
                    best = overlap;
                    double sign = distance < 0 ? -1 : 1;
                    result = {{axis[0] * sign, axis[1] * sign, overlap}};
                }
            } else {
                double velocity = rx * axis[0] + rz * axis[1];
                if (std::abs(velocity) <= 1e-9)
                    continue;
                double age =
                    velocity > 0 ? (extent + distance) / velocity : (extent - distance) / -velocity;
                if (age < -1e-9)
                    continue;
                if (!best.has || age < best.value - 1e-9) {
                    best = std::max(0.0, age);
                    double sign = velocity > 0 ? -1 : 1;
                    result = {{axis[0] * sign, axis[1] * sign, overlap}};
                }
            }
        }
        if (!best.has || (impact && result[0] * dx + result[1] * dz <= 1e-9))
            return {};
        if (!impact && std::abs(dx * result[0] + dz * result[1]) <= 1e-9) {
            if (result[0] < -1e-9 || (std::abs(result[0]) <= 1e-9 && result[1] < 0)) {
                result[0] = -result[0];
                result[1] = -result[1];
            }
            if (a.id > b.id) {
                result[0] = -result[0];
                result[1] = -result[1];
            }
        }
        return result;
    }
    static double closing(V a, V b, Contact normal) {
        return std::max(0.0, -((a[0] - b[0]) * normal[0] + (a[1] - b[1]) * normal[1]));
    }
    static std::pair<int, int> damage(double speed, double self_mass, double other_mass,
                                      double self_armor, double other_armor, double self_spall,
                                      double other_spall, double self_bonus, double other_bonus,
                                      bool self_moves, bool other_moves) {
        speed = std::abs(speed);
        double own = std::max(0.0, self_mass) * .001, other = std::max(0.0, other_mass) * .001,
               combined = own + other;
        if (combined <= 0 || speed <= 0)
            return {0, 0};
        double potential = .5 * combined * speed * speed,
               alpha_self = potential * (other / combined),
               alpha_other = potential * (own / combined),
               raw_self = std::max(0.0, .5 * alpha_self - 1.1 * std::max(0.0, self_armor) *
                                                              std::max(1.0, self_spall)),
               raw_other = std::max(0.0, .5 * alpha_other - 1.1 * std::max(0.0, other_armor) *
                                                                std::max(1.0, other_spall));
        self_bonus = clamp(self_bonus, 0, .15);
        other_bonus = clamp(other_bonus, 0, .15);
        if (self_moves) {
            raw_self *= 1 - self_bonus;
            raw_other *= 1 + self_bonus;
        }
        if (other_moves) {
            raw_other *= 1 - other_bonus;
            raw_self *= 1 + other_bonus;
        }
        return {static_cast<int>(raw_other * .25), static_cast<int>(raw_self * .25)};
    }
    static offline_nav::Optional<Contact> ram_inputs(const TankBody &body,
                                                     const Value &provided = Value()) {
        const Value &armor =
            provided.kind != Value::Null ? provided : body.raw.get(sf::contact_armor);
        if (armor.kind == Value::Null)
            return {};
        double nominal = armor.number(-1),
               spall = field(body.raw.get(sf::ram_profile), "spall_coefficient", 1),
               bonus = field(body.raw.get(sf::ram_profile), "ramming_bonus");
        if (nominal < 0 || !std::isfinite(nominal) || spall < 1 || !std::isfinite(spall) ||
            bonus < 0 || bonus > .15 || !std::isfinite(bonus))
            throw std::runtime_error("kernel contact armor producer");
        return Contact{{nominal, spall, bonus}};
    }
    static void encode_body(std::vector<double> &args, const TankBody &b) {
        args.insert(args.end(), {static_cast<double>(b.id),
                                 static_cast<double>(b.raw.get(sf::kind).text() == "player"),
                                 static_cast<double>(b.impulse), b.mass, b.vx, b.vy, b.vz});
        args.insert(args.end(), b.shape.begin(), b.shape.end());
        args.push_back(field(b.raw.get(sf::ram_profile), "spall_coefficient", 1));
        args.push_back(field(b.raw.get(sf::ram_profile), "ramming_bonus"));
    }
    Value armor_probe(const TankBody &a, const TankBody &b, Contact contact) {
        Pair pair(std::min(a.id, b.id), std::max(a.id, b.id));
        auto at = frame_armors.find(pair);
        if (at == frame_armors.end()) {
            std::vector<double> args;
            encode_body(args, a);
            encode_body(args, b);
            args.insert(args.end(), contact.begin(), contact.end());
            auto answer = engine.query(768, a.raw, b.raw, args);
            Value value;
            if (answer[0])
                value = tuple_value({answer[1] ? Value(answer[2]) : Value(),
                                     answer[3] ? Value(answer[4]) : Value()});
            if (a.id > b.id && value.kind == Value::Array)
                std::swap(value[0], value[1]);
            at = frame_armors.insert({pair, value}).first;
        }
        Value result = at->second.copy();
        if (a.id > b.id && result.kind == Value::Array)
            std::swap(result[0], result[1]);
        return result;
    }
    Response resolve(const TankBody &own, const std::vector<TankBody> &others,
                     offline_nav::Optional<double> now, const std::set<Pair> &previous,
                     bool probe) {
        Response result;
        std::set<Pair> overlaps, newly;
        for (const TankBody &other : others) {
            if (other.id == own.id)
                continue;
            if (own.has_y && other.has_y &&
                std::min(own.y + own.shape[3], other.y + other.shape[3]) -
                        std::max(own.y + own.shape[2], other.y + other.shape[2]) <=
                    .02)
                continue;
            double dx = own.x - other.x, dz = own.z - other.z,
                   reach = own.radius() + other.radius() + .25;
            if (dx * dx + dz * dz > reach * reach)
                continue;
            auto c = contact(own, other);
            if (!c.has)
                continue;
            Pair pair(std::min(own.id, other.id), std::max(own.id, other.id));
            overlaps.insert(pair);
            double inverse = 1 / own.mass, other_inverse = other.alive ? 1 / other.mass : 0,
                   total = inverse + other_inverse,
                   correction = std::max(c.value[2] - .01, 0.0) * .95 / total;
            result.correction[0] += c.value[0] * correction * inverse;
            result.correction[1] += c.value[1] * correction * inverse;
            V other_velocity = other.alive ? other.velocity() : V{{0, 0}};
            double normal = (own.vx - other_velocity[0]) * c.value[0] +
                            (own.vz - other_velocity[1]) * c.value[1];
            if (normal < 0 && other.impulse) {
                double impulse = -normal / total;
                result.delta[0] += c.value[0] * impulse * inverse;
                result.delta[1] += c.value[1] * impulse * inverse;
            }
            if (own.same_team(other))
                continue;
            auto impact = contact(own, other, true);
            if (!impact.has)
                continue;
            double speed = closing(own.velocity(), other_velocity, impact.value);
            if (speed <= 0 || !other.alive || !now.has || previous.count(pair))
                continue;
            auto self_inputs = ram_inputs(own), other_inputs = ram_inputs(other);
            if ((!self_inputs.has || !other_inputs.has) && probe) {
                Value answer = armor_probe(own, other, impact.value);
                if (answer.kind != Value::Null) {
                    if (!self_inputs.has)
                        self_inputs = ram_inputs(own, answer[0]);
                    if (!other_inputs.has)
                        other_inputs = ram_inputs(other, answer[1]);
                }
            }
            if (!self_inputs.has || !other_inputs.has) {
                Value v = Value::object();
                v["pair"] = tuple_value({Value(pair.first), Value(pair.second)});
                v["reason"] = Value("contact_armor_unavailable");
                v["missing_self"] = Value(!self_inputs.has);
                v["missing_other"] = Value(!other_inputs.has);
                result.diagnostics.append(v);
                continue;
            }
            auto self = self_inputs.value, peer = other_inputs.value;
            auto hp =
                damage(speed, own.mass, other.mass, self[0], peer[0], self[1], peer[1], self[2],
                       peer[2], own.vx || own.vy || own.vz, other.vx || other.vy || other.vz);
            if (!hp.first && !hp.second)
                continue;
            newly.insert(pair);
            cooldowns[pair] = now.value;
            Value event = Value::object();
            event["pair"] = tuple_value({Value(pair.first), Value(pair.second)});
            event["self_id"] = Value(own.id);
            event["other_id"] = Value(other.id);
            event["self_vehicle"] = Value(own.raw.get(sf::vehicle).text());
            event["other_vehicle"] = Value(other.raw.get(sf::vehicle).text());
            event["mass_self"] = Value(own.mass);
            event["mass_other"] = Value(other.mass);
            event["velocity_self"] = array(own.velocity());
            event["velocity_other"] = array(other.velocity());
            event["velocity_y_self"] = Value(own.vy);
            event["velocity_y_other"] = Value(other.vy);
            event["yaw_self"] = Value(own.yaw);
            event["yaw_other"] = Value(other.yaw);
            event["shape_self"] = shape(own);
            event["shape_other"] = shape(other);
            event["contact_normal"] = array(V{{impact.value[0], impact.value[1]}});
            event["contact_penetration"] = Value(impact.value[2]);
            event["closing_speed"] = Value(speed);
            double rx = own.vx - other.vx, ry = own.vy - other.vy, rz = own.vz - other.vz;
            event["relative_speed"] = Value(std::sqrt(rx * rx + ry * ry + rz * rz));
            event["impact_speed"] = Value(speed);
            event["armor_self"] = Value(self[0]);
            event["armor_other"] = Value(peer[0]);
            event["spall_self"] = Value(self[1]);
            event["spall_other"] = Value(peer[1]);
            event["ramming_bonus_self"] = Value(self[2]);
            event["ramming_bonus_other"] = Value(peer[2]);
            event["damage_to_other"] = Value(hp.first);
            event["damage_to_self"] = Value(hp.second);
            result.events.append(event);
        }
        for (Pair pair : previous)
            if (overlaps.count(pair))
                result.contacts.insert(pair);
        result.contacts.insert(newly.begin(), newly.end());
        return result;
    }
    void apply(Bot &bot, const Response &result, double step, bool advance = true,
               bool correct = true) {
        Value &s = bot.state;
        double yaw = field(s, sf::yaw), speed = field(s, sf::speed),
               forward = result.delta[0] * std::sin(yaw) + result.delta[1] * std::cos(yaw),
               applied = 0;
        if (forward * speed < 0) {
            applied = std::abs(forward) >= std::abs(speed) ? -speed : forward;
            s[sf::speed] = Value(speed + applied);
        }
        double px = field(s, sf::push_x) + result.delta[0] - applied * std::sin(yaw),
               pz = field(s, sf::push_z) + result.delta[1] - applied * std::cos(yaw),
               mx = (correct ? result.correction[0] : 0) + (advance ? px * step : 0),
               mz = (correct ? result.correction[1] : 0) + (advance ? pz * step : 0),
               distance = std::sqrt(mx * mx + mz * mz);
        if (distance > .0001) {
            double direction = std::atan2(mx, mz), velocity = distance / std::max(step, 1.0 / 120),
                   relative = direction - yaw,
                   length = std::max(.5, field(s, sf::half_length, 3.5)),
                   width = std::max(.3, field(s, sf::half_width, 1.7)),
                   support =
                       length * std::abs(std::cos(relative)) + width * std::abs(std::sin(relative)),
                   half_width =
                       length * std::abs(std::sin(relative)) + width * std::abs(std::cos(relative));
            double packet[128] = {635,
                                  static_cast<double>(integer(s, sf::id)),
                                  field(s, sf::x),
                                  field(s, sf::y),
                                  field(s, sf::z),
                                  direction,
                                  velocity,
                                  std::max(1.0, distance + support),
                                  half_width,
                                  field(s, sf::speed)};
            motion.engine_event(packet, 128, s);
            if (!packet[0]) {
                mx = mz = px = pz = 0;
            }
        }
        s[sf::x] = Value(field(s, sf::x) + mx);
        s[sf::z] = Value(field(s, sf::z) + mz);
        double decay = advance ? std::pow(.90, std::max(0.0, step) * 60) : 1;
        s[sf::push_x] = Value(px * decay);
        s[sf::push_z] = Value(pz * decay);
    }
    Value report(int id, int other, int self_damage, int other_damage, int player = 0,
                 int seq = 0) {
        Value v = Value::object();
        int base = integer(config, "human_base");
        v["type"] = Value("bot_ram");
        v["bot_id"] = Value(id);
        v[sf::target_kind] = Value(other >= base ? "human" : "bot");
        v[sf::target_id] = Value(other >= base ? other - base : other);
        v["ram_seq"] = Value(++sequence);
        v["damage_to_bot"] = Value(self_damage);
        v["damage_to_target"] = Value(other_damage);
        if (player) {
            v["ram_contact_player_id"] = Value(player);
            v["ram_contact_seq"] = Value(seq);
        }
        return v;
    }
    bool ack(int player, int seq) {
        if (player <= 0 || seq <= 0 || seq <= human_ack[player])
            return false;
        human_ack[player] = seq;
        for (auto at = human_reports.begin(); at != human_reports.end();)
            if (at->first.first == player && at->first.second <= seq)
                at = human_reports.erase(at);
            else
                ++at;
        return true;
    }
    static Value body(const Value &s, bool human, int base, const Value &collision) {
        int id = integer(s, sf::id);
        Value result = Value::object();
        result[sf::id] = Value(id + (human ? base : 0));
        result[sf::network_id] = Value(id);
        result[sf::kind] = Value(human ? "player" : "bot");
        result[sf::alive] = Value(flag(s, sf::alive, true));
        result[sf::team] = Value(integer(s, sf::team));
        result[sf::vehicle] = Value(s.get(sf::vehicle).text());
        if (human)
            result["impulse"] = Value(false);
        for (const char *key : {"x", "y", "z", "yaw"})
            result[key] = Value(field(s, key));
        result[sf::mass] = Value(human ? field(collision, sf::mass) : field(s, sf::mass, 25000));
        result["shape"] = human ? collision.get("shape") : s.get(sf::collision_shape);
        result[sf::ram_profile] = human ? collision.get(sf::ram_profile) : s.get(sf::ram_profile);
        double speed = flag(s, sf::alive, true) ? field(s, sf::speed) : 0, yaw = field(s, sf::yaw);
        result["vx"] = Value(std::sin(yaw) * speed + (human ? 0 : field(s, sf::push_x)));
        result["vz"] = Value(std::cos(yaw) * speed + (human ? 0 : field(s, sf::push_z)));
        if (!human) {
            result["vy"] = Value(field(s, sf::ram_vy, field(s, sf::vertical_speed)));
            result[sf::pitch] = Value(field(s, sf::pitch));
            result[sf::roll] = Value(field(s, sf::roll));
        }
        return result;
    }
    Value human_receipts(const Value &players, double step, std::set<Pair> &processed,
                         std::set<int> &contacted) {
        std::map<int, std::vector<Value>> entries;
        int base = integer(config, "human_base");
        for (const Value &raw : elements(players)) {
            if (raw.kind != Value::Object || !raw.has(sf::id))
                continue;
            int id = integer(raw, sf::id), resolved = integer(raw, sf::ram_contact_resolved_seq);
            if (resolved > 0)
                ack(id, resolved);
            if (raw.get(sf::ram_contacts).kind == Value::Array) {
                for (const Value &receipt : elements(raw.get(sf::ram_contacts))) {
                    if (receipt.kind != Value::Object)
                        continue;
                    Value row = raw.copy();
                    row[sf::ram_contact] = receipt;
                    row[sf::_ram_contact_bot_state] = receipt.get(sf::_ram_contact_bot_state);
                    entries[id].push_back(row);
                }
            } else
                entries[id].push_back(raw);
        }
        Value reports = Value::array();
        for (auto &group : entries) {
            int player = group.first;
            auto &rows = group.second;
            std::stable_sort(rows.begin(), rows.end(), [](const Value &a, const Value &b) {
                return integer(a.get(sf::ram_contact), "seq", 2147483647) <
                       integer(b.get(sf::ram_contact), "seq", 2147483647);
            });
            for (const Value &raw : rows) {
                const Value &r = raw.get(sf::ram_contact),
                            &historical = raw.get(sf::_ram_contact_bot_state);
                if (r.kind != Value::Object || r.get("seq").kind == Value::Null ||
                    r.get("bot_id").kind == Value::Null)
                    continue;
                int seq = integer(r, "seq"), id = integer(r, "bot_id");
                if (seq <= human_ack[player])
                    continue;
                Pair key(player, seq),
                    pair(std::min(id, base + player), std::max(id, base + player));
                auto cached = human_reports.find(key);
                if (cached != human_reports.end()) {
                    processed.insert(pair);
                    for (const Value &v : elements(cached->second))
                        reports.append(v.copy());
                    break;
                }
                if (historical.kind != Value::Object || !bots.count(id))
                    break;
                Bot &current = *bots.at(id);
                Value receipt_reports = Value::array();
                bool valid =
                    flag(current.state, sf::alive, true) && integer(historical, sf::id, -1) == id;
                for (const char *name : {"x", "y", "z", "yaw", "vx", "vy", "vz", "bot_vx", "bot_vy",
                                         "bot_vz", "presentation_time_us", "contact_x", "contact_y",
                                         "contact_z", "contact_armor_player", "contact_armor_bot",
                                         "contact_normal_x", "contact_normal_z"})
                    valid =
                        valid && r.get(name).kind != Value::Null && std::isfinite(field(r, name));
                if (valid) {
                    Value own = body(historical, false, base, Value());
                    own.erase(sf::kind);
                    own.erase(sf::network_id);
                    own["vx"] = r.get("bot_vx");
                    own["vy"] = r.get("bot_vy");
                    own["vz"] = r.get("bot_vz");
                    Value other = body(raw, true, base, raw.get(sf::_kernel).get("collision"));
                    other.erase(sf::kind);
                    other.erase(sf::network_id);
                    other.erase("impulse");
                    for (const char *name :
                         {"x", "y", "z", "yaw", "vx", "vy", "vz", "pitch", "roll"})
                        other[name] = Value(field(r, name));
                    TankBody a(own), b(other);
                    double player_armor = field(r, "contact_armor_player"),
                           bot_armor = field(r, "contact_armor_bot"),
                           nx = field(r, "contact_normal_x"), nz = field(r, "contact_normal_z"),
                           normal = std::hypot(nx, nz),
                           alignment = nx * (b.x - a.x) + nz * (b.z - a.z),
                           spall = field(r, "contact_spall_player"),
                           bonus = field(r, "contact_bonus_player", -1);
                    Value hit = vector3(field(r, "contact_x"), field(r, "contact_y"),
                                        field(r, "contact_z"));
                    valid = player_armor > 0 && bot_armor > 0 && spall >= 1 && spall <= 1.5 &&
                            bonus >= 0 && bonus <= .15 && normal >= .999 && normal <= 1.001 &&
                            alignment > 1e-6 && !flag(r, "contact_screened_player") &&
                            !flag(r, "contact_screened_bot") && a.contains(hit, .75) &&
                            b.contains(hit, .75);
                    double own_spall = field(own.get(sf::ram_profile), "spall_coefficient", -1),
                           own_bonus = field(own.get(sf::ram_profile), "ramming_bonus", -1);
                    valid = valid && own_spall >= 1 && own_spall <= 1.5 && own_bonus >= 0 &&
                            own_bonus <= .15;
                    if (valid) {
                        Contact direction{{-nx / normal, -nz / normal, 0}};
                        double speed = closing(a.velocity(), b.velocity(), direction);
                        auto hp = a.same_team(b)
                                      ? std::make_pair(0, 0)
                                      : damage(speed, a.mass, b.mass, bot_armor, player_armor,
                                               own_spall, spall, own_bonus, bonus,
                                               a.vx || a.vy || a.vz, b.vx || b.vy || b.vz);
                        Response response = resolve(a, std::vector<TankBody>{b}, {}, {}, false);
                        V before{
                            {field(current.state, sf::push_x), field(current.state, sf::push_z)}};
                        double previous_speed = field(current.state, sf::speed);
                        apply(current, response, step, false, false);
                        if (std::abs(field(current.state, sf::speed) - previous_speed) > .0001 ||
                            std::abs(field(current.state, sf::push_x) - before[0]) > .0001 ||
                            std::abs(field(current.state, sf::push_z) - before[1]) > .0001)
                            contacted.insert(id);
                        if (hp.first || hp.second)
                            receipt_reports.append(
                                report(id, base + player, hp.second, hp.first, player, seq));
                    }
                }
                if (receipt_reports.size() == 0)
                    receipt_reports.append(report(id, base + player, 0, 0, player, seq));
                processed.insert(pair);
                human_reports[key] = receipt_reports;
                for (const Value &v : elements(receipt_reports))
                    reports.append(v.copy());
                break;
            }
        }
        return reports;
    }
    Value step(const Value &players, const std::vector<int> &ordered, double now, double elapsed) {
        int base = integer(config, "human_base");
        std::map<int, TankBody> bodies;
        std::vector<int> insertions;
        std::map<int, double> radii;
        double maximum = 4;
        for (int id : ordered) {
            Value raw = body(bots.at(id)->state, false, base, Value());
            bodies.emplace(id, TankBody(raw));
            insertions.push_back(id);
        }
        for (const Value &raw : elements(players)) {
            if (raw.kind != Value::Object || !raw.has(sf::id))
                continue;
            Value v = body(raw, true, base, raw.get(sf::_kernel).get("collision"));
            int id = integer(v, sf::id);
            bodies.erase(id);
            bodies.emplace(id, TankBody(v));
            insertions.push_back(id);
        }
        for (const auto &v : bodies) {
            double radius = v.second.radius();
            maximum = std::max(maximum, radius);
            radii[v.first] = radius;
        }
        double size = maximum * 2 + 4;
        std::map<std::pair<int, int>, std::vector<int>> buckets;
        for (int id : integer_dict_order(insertions, flag(config, "py2", true))) {
            const TankBody &b = bodies.at(id);
            buckets[{static_cast<int>(std::floor(b.x / size)),
                     static_cast<int>(std::floor(b.z / size))}]
                .push_back(id);
        }
        std::set<Pair> processed, current;
        std::set<int> contacted;
        Value reports = human_receipts(players, elapsed, processed, contacted);
        frame_armors.clear();
        for (int id : ordered) {
            Bot &bot = *bots.at(id);
            if (!flag(bot.state, sf::alive, true))
                continue;
            const TankBody &own = bodies.at(id);
            int x = static_cast<int>(std::floor(own.x / size)),
                z = static_cast<int>(std::floor(own.z / size));
            std::vector<TankBody> others;
            for (int oz = -1; oz <= 1; ++oz)
                for (int ox = -1; ox <= 1; ++ox) {
                    auto bucket = buckets.find({x + ox, z + oz});
                    if (bucket == buckets.end())
                        continue;
                    for (int peer : bucket->second) {
                        if (peer == id || processed.count({std::min(id, peer), std::max(id, peer)}))
                            continue;
                        const TankBody &b = bodies.at(peer);
                        double dx = own.x - b.x, dz = own.z - b.z,
                               reach = radii[id] + radii[peer] + .25;
                        if (dx * dx + dz * dz > reach * reach)
                            continue;
                        others.push_back(b);
                    }
                }
            if (others.empty()) {
                if (field(bot.state, sf::push_x) || field(bot.state, sf::push_z))
                    apply(bot, Response(), elapsed);
                continue;
            }
            std::set<Pair> previous = active;
            previous.insert(current.begin(), current.end());
            Response response = resolve(own, others, now, previous, flag(config, "has_armor"));
            current.insert(response.contacts.begin(), response.contacts.end());
            if (std::abs(response.delta[0]) > .0001 || std::abs(response.delta[1]) > .0001 ||
                std::abs(response.correction[0]) > .0001 ||
                std::abs(response.correction[1]) > .0001)
                contacted.insert(id);
            apply(bot, response, elapsed);
            for (const Value &event : elements(response.events)) {
                if (motion.diagnostics)
                    motion.diagnostics->emit("RAM", event);
                if (integer(event, "other_id") < base)
                    reports.append(report(id, integer(event, "other_id"),
                                          integer(event, "damage_to_self"),
                                          integer(event, "damage_to_other")));
            }
        }
        for (int id : contacted)
            if (elapsed > 0)
                leases[id] += elapsed;
        active = current;
        return reports;
    }
    void finish(int driver) {
        for (const auto &v : leases) {
            double packet[] = {204, static_cast<double>(driver), static_cast<double>(v.first), 1,
                               v.second};
            offline_driver_dispatch(packet, 5);
        }
        leases.clear();
    }
};
} // namespace offline_kernel
#endif
