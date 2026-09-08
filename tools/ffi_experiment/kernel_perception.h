#ifndef OFFLINE_EXPERIMENT_KERNEL_PERCEPTION_H
#define OFFLINE_EXPERIMENT_KERNEL_PERCEPTION_H
#include "kernel_bot.h"
#include "kernel_engine.h"
#include "kernel_factors.h"
#include "perception_core.h"
#include "query_bridge.h"
#include <array>

namespace offline_kernel {
typedef std::array<int, 2> ActorKey;
typedef std::array<int, 3> TeamKey;
struct Contacts {
    Value rows = Value::array();
    std::map<int, Value> lookup;
};
struct Observation {
    bool visible = false;
    std::set<int> shootable, humans, bots;
    Value target;
};
struct Perception {
    struct Actor {
        int kind, id;
        Value raw, config;
    };
    const Value config;
    Factors factors;
    int handle = 0;
    Engine *engine = nullptr;
    std::map<int, std::unique_ptr<Bot>> &bots;
    std::vector<Actor> roster;
    std::map<ActorKey, size_t> indices;
    std::map<std::pair<size_t, bool>, Value> templates;
    std::map<TeamKey, Value> remembered;
    std::map<TeamKey, bool> team_visible;
    std::map<ActorKey, double> source_still, target_still;
    std::map<ActorKey, std::pair<bool, Value>> projection_cache;
    std::map<ActorKey, Value> profile_cache;
    std::map<int, CriticalConfig> human_critical;
    std::map<int, bool> human_alive;
    std::map<int, Value> human_last_critical;
    std::map<int, double> human_vengeance;
    std::map<int, std::set<ActorKey>> human_direct;
    std::map<TeamKey, Observation> observations;
    std::map<std::array<int, 3>, Value> probe_targets;
    explicit Perception(const Value &v, std::map<int, std::unique_ptr<Bot>> &states)
        : config(v), factors(v.get("factors")), bots(states) {
        std::vector<double> packet = {300};
        for (const char *name :
             {"ttl", "shot_seconds", "proximity", "maximum", "memory", "designated", "budget"})
            packet.push_back(field(v, name));
        call(packet);
        handle = static_cast<int>(packet[0]);
    }
    ~Perception() {
        if (handle) {
            std::vector<double> packet = {312, static_cast<double>(handle)};
            try {
                call(packet);
            } catch (...) {
            }
        }
    }
    static void call(std::vector<double> &packet) {
        offline_perception_dispatch(packet.data(), static_cast<int>(packet.size()));
    }
    void simple(int op) {
        std::vector<double> packet = {static_cast<double>(op), static_cast<double>(handle)};
        call(packet);
    }
    static int kind(const Value &v) { return v.get("kind").text() == "human" ? 1 : 0; }
    static int id(const Value &v) { return integer(v, "network_id", integer(v, "id")); }
    static ActorKey key(const Value &v) { return ActorKey{{kind(v), id(v)}}; }
    static TeamKey team_key(int team, const Value &target) {
        return TeamKey{{team, kind(target), id(target)}};
    }
    static int fire(const Value &v) {
        return v.get("fire_seq").kind == Value::Null ? -1 : std::max(0, integer(v, "fire_seq"));
    }
    void record(std::vector<double> &packet, const Value &state, int identity = -1) {
        Value p = target_position(state);
        packet.push_back(kind(state));
        packet.push_back(identity < 0 ? id(state) : identity);
        packet.push_back(integer(state, "team"));
        for (const Value &v : elements(p))
            packet.push_back(v.number());
        packet.push_back(fire(state));
    }
    void begin() { simple(301); }
    void finish() { simple(303); }
    void start_slice(const Value &players, const std::vector<int> &order) {
        roster.clear();
        indices.clear();
        templates.clear();
        team_visible.clear();
        projection_cache.clear();
        profile_cache.clear();
        human_critical.clear();
        observations.clear();
        probe_targets.clear();
        for (const Value &raw : elements(players)) {
            if (raw.kind != Value::Object || !raw.has("id"))
                continue;
            int identity = integer(raw, "id");
            Actor actor{1, identity, raw, raw.get("_kernel")};
            indices[ActorKey{{1, identity}}] = roster.size();
            roster.push_back(actor);
            human_critical.emplace(identity, CriticalConfig(actor.config.get("critical_config")));
        }
        for (int identity : order) {
            Bot &bot = *bots.at(identity);
            indices[ActorKey{{0, identity}}] = roster.size();
            roster.push_back(Actor{0, identity, bot.state, bot.config.get("perception")});
        }
        std::vector<double> packet = {305, static_cast<double>(handle),
                                      static_cast<double>(roster.size())};
        for (const Actor &actor : roster) {
            packet.push_back(actor.kind);
            packet.push_back(actor.id);
            packet.push_back(integer(actor.raw, "team"));
            packet.push_back(flag(actor.raw, "alive", true));
        }
        call(packet);
    }
    void prepare(double now, bool include_humans, const std::vector<int> &ordered,
                 const std::set<int> &due, const std::map<int, ActorKey> &selected_targets) {
        std::vector<double> packet = {302, static_cast<double>(handle), now, 0};
        int count = 0;
        for (int id : ordered) {
            const Value &state = bots.at(id)->state;
            if (!flag(state, "alive", true))
                continue;
            auto selected = selected_targets.find(id);
            packet.push_back(0);
            packet.push_back(id);
            packet.push_back(integer(state, "team"));
            packet.push_back(fire(state));
            packet.push_back(selected != selected_targets.end() ? selected->second[0] : -1);
            packet.push_back(selected != selected_targets.end() ? selected->second[1] : 0);
            packet.push_back(due.count(id) != 0);
            ++count;
        }
        for (const Actor &actor : roster)
            if (actor.kind == 1 && flag(actor.raw, "alive", true)) {
                packet.push_back(1);
                packet.push_back(actor.id);
                packet.push_back(integer(actor.raw, "team"));
                packet.push_back(fire(actor.raw));
                packet.push_back(-1);
                packet.push_back(0);
                packet.push_back(include_humans);
                ++count;
            }
        packet[3] = count;
        call(packet);
    }
    void note_still(const Value &source, double now) {
        ActorKey identity = key(source);
        if (std::abs(field(source, "speed")) > field(config, "moving_epsilon"))
            source_still.erase(identity);
        else
            source_still.emplace(identity, now);
    }
    Value target(size_t index, bool processed) {
        const Actor &actor = roster.at(index);
        auto cache_key = std::make_pair(index, actor.kind == 0 && processed);
        auto at = templates.find(cache_key);
        if (at != templates.end())
            return at->second;
        Value result =
            select_fields(actor.raw, config.get(actor.kind == 1 ? "human_fields" : "bot_fields"));
        result["kind"] = Value(actor.kind == 1 ? "human" : "bot");
        result["network_id"] = Value(actor.id);
        result["id"] = Value(actor.kind == 1 ? integer(config, "human_base") + actor.id : actor.id);
        result["position"] = position(actor.raw);
        if (actor.kind == 1) {
            result["class_tag"] = actor.config.get("class_tag");
            result["armor"] = actor.config.get("armor");
        }
        templates[cache_key] = result;
        return result;
    }
    Value pose(const Value &target) const {
        Value p = select_fields(target, config.get("pose_fields"));
        p["position"] = position(target);
        for (const char *name : {"x", "y", "z", "yaw", "speed"})
            if (!p.has(name))
                p[name] = Value(0.0);
        return p;
    }
    Value dynamic(const Value &target) const {
        const Value &snapshot = target.get("effective_params"),
                    &projection = snapshot.get("crew").get("dynamic_spotting");
        std::set<std::string> knocked = names(target.get("critical").get("crew_ko"));
        const Value &roster = projection.get("crew");
        unsigned mask = 0;
        for (size_t i = 0; i < roster.size(); ++i)
            if (knocked.erase(roster[i].text()))
                mask |= 1 << i;
        if (!knocked.empty())
            throw std::invalid_argument("kernel human critical roster");
        std::string name =
            std::to_string(mask) + ":" + (flag(target.get("critical"), "fire") ? "1" : "0");
        const Value &row = projection.get("states").get(name);
        if (row.kind != Value::Object)
            throw std::invalid_argument("kernel human dynamic spotting");
        return row;
    }
    Value profile(const Value &target) {
        ActorKey identity = key(target);
        auto at = profile_cache.find(identity);
        if (at != profile_cache.end())
            return at->second;
        Value result;
        if (identity[0] == 0)
            result = bots.at(identity[1])->config.get("perception").get("profile");
        else {
            Value row = dynamic(target), p = target.get("effective_params").get("spotting").copy();
            p["vision_factor"] = Value(field(p, "vision_factor") * field(row, "vision", 1));
            p["camouflage_factor"] =
                Value(field(p, "camouflage_factor") * field(row, "camouflage", 1));
            p["invisibility_moving"] = row.get("invisibility_moving");
            p["invisibility_still"] = row.get("invisibility_still");
            result = Value::array();
            Value base = Value::array();
            base.append(row.get("base_moving"));
            base.append(row.get("base_still"));
            result.append(base);
            result.append(target.get("effective_params").get("camouflage").get("shot_factor"));
            result.append(p);
        }
        profile_cache[identity] = result;
        return result;
    }
    double view(const Value &source, double now) {
        ActorKey identity = key(source);
        auto since = source_still.find(identity);
        if (identity[0] == 0) {
            Bot &bot = *bots.at(identity[1]);
            const Value &v = bot.config.get("perception").get("vision");
            double value = field(source, "view_range", 330);
            if (v.kind == Value::Array)
                value = v[2].kind != Value::Null && since != source_still.end() &&
                                now - since->second >= std::max(0.0, v[2].number())
                            ? v[1].number()
                            : v[0].number();
            return value * factors.vision(factors.stat(source, *bot.critical, "vision"));
        }
        const Actor &actor = roster[indices.at(identity)];
        const Value &snapshot = source.get("effective_params"), &p = snapshot.get("spotting");
        Value row = dynamic(source);
        double damage =
            factors.vision(field(row, "vision", 1) *
                           factors.stat(source, human_critical.at(identity[1]), "vision", false));
        bool binocular = flag(p, "has_binoculars") && since != source_still.end() &&
                         now - since->second >= std::max(0.0, field(p, "binocular_delay", 3));
        double value = std::max(field(config, "proximity"), field(actor.config, "base_view", 330));
        value *= std::max(0.0, field(actor.config, "view_misc", 1) * damage);
        value *= std::max(0.0, field(p, "vision_factor"));
        if (binocular)
            value *= std::max(1.0, field(p, "binocular_factor", 1));
        return std::max(field(config, "proximity"), value);
    }
    Value projection(const Value &target, double now) {
        ActorKey identity = key(target);
        bool moving = std::abs(field(target, "speed")) > field(config, "moving_epsilon");
        auto cached = projection_cache.find(identity);
        if (cached != projection_cache.end() && cached->second.first == moving)
            return cached->second.second;
        Value bundle = profile(target);
        const Value &p = bundle[2];
        double still = 0;
        if (moving)
            target_still.erase(identity);
        else {
            auto at = target_still.emplace(identity, now).first;
            still = std::max(0.0, now - at->second);
        }
        bool ready = still >= std::max(0.0, field(p, "camouflage_net_delay", 3));
        const Value &aspect =
            p.get(moving || (flag(p, "has_camouflage_net") && !ready) ? "invisibility_moving"
                                                                      : "invisibility_still");
        Value result = Value::array();
        result.append(bundle[0][0]);
        result.append(bundle[0][1]);
        result.append(bundle[1]);
        result.append(Value(moving));
        result.append(aspect[0]);
        result.append(aspect[1]);
        projection_cache[identity] = std::make_pair(moving, result);
        return result;
    }
    Value engine_visibility(const Value &source, const Value &target, bool fired) {
        if (engine) {
            auto reply = engine->query(767, source, target, {static_cast<double>(fired)});
            Value out = Value::array();
            out.append(Value(reply[0] != 0));
            out.append(Value(reply[1]));
            return out;
        }
        std::array<double, 32> packet{};
        packet[0] = 750;
        packet[1] = kind(source);
        packet[2] = id(source);
        packet[3] = kind(target);
        packet[4] = id(target);
        packet[5] = fired;
        Value first = position(source), second = target_position(target);
        for (int i = 0; i < 3; ++i) {
            packet[6 + i] = first[i].number();
            packet[9 + i] = second[i].number();
        }
        if (offline_query(packet.data(), packet.size()))
            throw std::runtime_error("kernel visibility engine callback");
        Value result = Value::array();
        result.append(Value(packet[0] != 0));
        result.append(Value(packet[1]));
        return result;
    }
    std::vector<std::array<int, 4>> run(std::vector<double> packet, const Value &source, double now,
                                        const std::set<int> &processed,
                                        const Value &single = Value()) {
        packet.resize(576, 0);
        call(packet);
        bool has_view = false;
        double source_view = 0;
        while (packet[64] != 0) {
            int stage = static_cast<int>(packet[64]), index = static_cast<int>(packet[65]);
            bool phase = packet[66] != 0, fired = packet[67] != 0;
            Value active = single.kind != Value::Null ? single : target(index, phase);
            std::vector<double> reply = {306, static_cast<double>(handle),
                                         static_cast<double>(stage)};
            if (stage == 1)
                record(reply, active);
            else if (stage == 2) {
                if (!has_view) {
                    source_view = view(source, now);
                    has_view = true;
                }
                reply.push_back(source_view);
                Value p = projection(active, now);
                for (const Value &v : elements(p))
                    reply.push_back(v.number());
            } else if (stage == 3) {
                Value result = engine_visibility(source, active, fired);
                reply.push_back(result[0].number());
                reply.push_back(result[1].number());
            } else
                throw std::invalid_argument("kernel perception stage");
            std::copy(reply.begin(), reply.end(), packet.begin());
            call(packet);
        }
        (void)processed;
        std::vector<std::array<int, 4>> rows;
        int count = static_cast<int>(packet[65]);
        for (int i = 0; i < count; ++i) {
            std::array<int, 4> row;
            for (int j = 0; j < 4; ++j)
                row[j] = static_cast<int>(packet[66 + i * 4 + j]);
            rows.push_back(row);
        }
        return rows;
    }
    bool visible(const Value &source, const Value &target, double now) {
        std::vector<double> packet = {307, static_cast<double>(handle)};
        record(packet, source);
        packet.push_back(now);
        record(packet, target);
        auto rows = run(packet, source, now, {}, target);
        return rows.at(0)[1] != 0;
    }
    void renew(TeamKey key, double now, double duration) {
        std::vector<double> packet = {308,
                                      static_cast<double>(handle),
                                      static_cast<double>(key[0]),
                                      static_cast<double>(key[1]),
                                      static_cast<double>(key[2]),
                                      now,
                                      duration};
        call(packet);
    }
    double remaining(TeamKey key, double now) {
        std::vector<double> packet = {309,
                                      static_cast<double>(handle),
                                      static_cast<double>(key[0]),
                                      static_cast<double>(key[1]),
                                      static_cast<double>(key[2]),
                                      now};
        call(packet);
        return packet[0];
    }
    static bool perk(const Value &snapshot, const Value &state, const std::string &wanted) {
        std::set<std::string> knocked = names(state.get("critical").get("crew_ko"));
        for (const Value &member : elements(snapshot.get("crew").get("members"))) {
            if (knocked.count(member.get("instance").text()))
                continue;
            for (const Value &skill : elements(member.get("skills")))
                if (skill.get("name").text() == wanted && flag(skill, "active") &&
                    flag(skill, "enabled") && field(skill, "level") >= 100)
                    return true;
        }
        return false;
    }
    void lifecycle(double now) {
        std::set<int> present;
        for (const Actor &a : roster)
            if (a.kind == 1) {
                present.insert(a.id);
                bool alive = flag(a.raw, "alive", true);
                auto before = human_alive.find(a.id);
                if (before != human_alive.end() && before->second && !alive) {
                    Value prior = Value::object();
                    auto at = human_last_critical.find(a.id);
                    prior["critical"] =
                        at == human_last_critical.end() ? Value::object() : at->second;
                    if (perk(a.raw.get("effective_params"), prior, "radioman_lasteffort"))
                        human_vengeance[a.id] = now + field(config, "last_effort");
                } else if (alive) {
                    const Value &p = a.raw.get("critical");
                    human_last_critical[a.id] =
                        p.kind == Value::Object ? p.copy() : Value::object();
                    human_vengeance.erase(a.id);
                }
                human_alive[a.id] = alive;
            }
        for (auto at = human_alive.begin(); at != human_alive.end();)
            if (!present.count(at->first)) {
                int id = at->first;
                at = human_alive.erase(at);
                human_last_critical.erase(id);
                human_direct.erase(id);
                human_vengeance.erase(id);
                source_still.erase(ActorKey{{1, id}});
            } else
                ++at;
        for (auto at = human_vengeance.begin(); at != human_vengeance.end();)
            if (now >= at->second) {
                human_direct.erase(at->first);
                at = human_vengeance.erase(at);
            } else
                ++at;
    }
    double designated(const Value &source, const Value &target) {
        if (!perk(source.get("effective_params"), source, "gunner_rancorous"))
            return field(config, "memory");
        Value start = position(source), end = target_position(target);
        double dx = end[0].number() - start[0].number(), dz = end[2].number() - start[2].number();
        if (dx * dx + dz * dz <= .000001)
            return field(config, "designated");
        double bearing = std::atan2(dx, dz), yaw = field(source, "aim_yaw", field(source, "yaw"));
        return std::abs(angle(bearing - yaw)) <= .08726646259971647 + 1e-9
                   ? field(config, "designated")
                   : field(config, "memory");
    }
    void append_humans(double now) {
        std::vector<Value> targets;
        for (size_t i = 0; i < roster.size(); ++i)
            if (flag(roster[i].raw, "alive", true))
                targets.push_back(target(i, false));
        for (const Actor &a : roster)
            if (a.kind == 1) {
                Value source = a.raw.copy();
                source["kind"] = Value("human");
                source["network_id"] = Value(a.id);
                source["id"] = Value(a.id);
                int team = integer(source, "team");
                if ((team != 1 && team != 2) || a.id <= 0)
                    throw std::invalid_argument("kernel human observer identity");
                bool alive = flag(source, "alive", true);
                std::set<ActorKey> direct;
                if (alive)
                    note_still(source, now);
                else {
                    auto until = human_vengeance.find(a.id);
                    if (until != human_vengeance.end() && now < until->second)
                        direct = human_direct[a.id];
                }
                for (Value &t : targets) {
                    if (integer(t, "team") == team)
                        continue;
                    ActorKey identity = key(t);
                    TeamKey tk = team_key(team, t);
                    bool seen = alive ? visible(source, t, now) : direct.count(identity) != 0;
                    if (seen && alive)
                        direct.insert(identity);
                    Observation &entry = observations[tk];
                    entry.visible = entry.visible || seen;
                    entry.target = t;
                    if (!seen)
                        continue;
                    entry.humans.insert(a.id);
                    team_visible[tk] = true;
                    Value p = pose(t);
                    remembered[tk] = p;
                    update(entry.target, p);
                    renew(tk, now, alive ? designated(source, t) : field(config, "memory"));
                }
                if (alive)
                    human_direct[a.id] = direct;
            }
    }
    Value refresh(const Value &cached) {
        Value result = cached.copy();
        ActorKey identity = key(cached);
        auto at = indices.find(identity);
        if (at == indices.end())
            return result;
        const Value &live = roster[at->second].raw;
        if (flag(cached, "fresh_visible")) {
            result["position"] = position(live);
            for (const Value &v : elements(config.get("pose_fields"))) {
                std::string name = v.text();
                if (live.has(name))
                    result[name] = live.get(name);
            }
        }
        for (const char *name : {"alive", "health", "max_health", "team"})
            if (live.has(name))
                result[name] = live.get(name);
        return result;
    }
    void collect(const Value &source, const Contacts &contacts, const std::set<int> &processed) {
        std::map<int, Value> live;
        for (const auto &v : contacts.lookup) {
            ActorKey identity = key(v.second);
            std::array<int, 3> pk = {{identity[0], identity[1],
                                      identity[0] == 0 && processed.count(identity[1]) ? 1 : 0}};
            auto at = probe_targets.find(pk);
            if (at == probe_targets.end())
                at = probe_targets.emplace(pk, refresh(v.second)).first;
            live[v.first] = at->second;
        }
        int team = integer(source, "team");
        for (const Value &cached : elements(contacts.rows)) {
            auto at = live.find(integer(cached, "id"));
            Value observed = at == live.end() ? cached : at->second;
            TeamKey tk = team_key(team, observed);
            bool fresh = flag(cached, "fresh_visible");
            team_visible[tk] = fresh || team_visible[tk];
            if (fresh) {
                Value p = pose(observed);
                remembered[tk] = p;
                update(observed, p);
            }
            Observation &entry = observations[tk];
            entry.visible = entry.visible || flag(cached, "visible");
            entry.target = observed;
            if (flag(cached, "direct_visible"))
                entry.bots.insert(integer(source, "id"));
        }
    }
    Contacts contacts(const Value &source, double now, const std::set<int> &processed) {
        std::vector<double> packet = {304, static_cast<double>(handle)};
        record(packet, source, integer(source, "id"));
        packet.push_back(now);
        int team = integer(source, "team");
        for (const Actor &a : roster) {
            packet.push_back(a.kind == 0 && processed.count(a.id));
            packet.push_back(team_visible[TeamKey{{team, a.kind, a.id}}]);
            packet.push_back(flag(a.raw, "alive", true));
            packet.push_back(integer(a.raw, "team"));
        }
        auto results = run(packet, source, now, processed);
        Contacts out;
        for (const auto &row : results) {
            const Actor &a = roster.at(row[0]);
            Value base = target(row[0], a.kind == 0 && processed.count(a.id)), t = base.copy();
            bool visible = row[1] != 0, direct = row[2] != 0, fresh = row[3] != 0;
            TeamKey key = {{team, a.kind, a.id}};
            if (direct)
                team_visible[key] = true;
            if (fresh) {
                Value p = pose(base);
                remembered[key] = p;
                update(t, p);
            } else {
                for (const Value &name : elements(config.get("pose_fields")))
                    t.erase(name.text());
                auto prior = remembered.find(key);
                if (prior == remembered.end()) {
                    t["position"] = point(0, 0, 0);
                    for (const char *name : {"x", "y", "z", "yaw", "speed"})
                        t[name] = Value(0.0);
                    visible = false;
                } else
                    update(t, prior->second);
            }
            t["visible"] = Value(visible);
            t["direct_visible"] = Value(direct);
            t["fresh_visible"] = Value(fresh);
            if (visible)
                out.lookup[integer(t, "id")] = t;
            out.rows.append(t);
        }
        return out;
    }
    Value snapshot() const {
        Value result = Value::object(), poses = Value::array(), source = Value::array(),
              target = Value::array(), visible = Value::array();
        for (const auto &v : remembered) {
            Value row = Value::array();
            for (int n : v.first)
                row.append(Value(n));
            row.append(v.second);
            poses.append(row);
        }
        for (const auto &v : source_still) {
            Value row = Value::array();
            for (int n : v.first)
                row.append(Value(n));
            row.append(Value(v.second));
            source.append(row);
        }
        for (const auto &v : target_still) {
            Value row = Value::array();
            for (int n : v.first)
                row.append(Value(n));
            row.append(Value(v.second));
            target.append(row);
        }
        for (const auto &v : team_visible)
            if (v.second) {
                Value row = Value::array();
                for (int n : v.first)
                    row.append(Value(n));
                visible.append(row);
            }
        result["remembered"] = poses;
        result["source_still"] = source;
        result["target_still"] = target;
        result["team_visible"] = visible;
        return result;
    }
};
} // namespace offline_kernel
#endif
