#ifndef OFFLINE_EXPERIMENT_KERNEL_AIM_H
#define OFFLINE_EXPERIMENT_KERNEL_AIM_H
#include "kernel_gunnery.h"
#include "kernel_engine.h"
#include "kernel_factors.h"
#include "combat_core.h"

namespace offline_kernel {
inline double cache_deadline(double now, int id, double interval, int salt, bool initial) {
    interval = std::max(.001, interval);
    double result = now + interval;
    return initial ? result + ((std::abs(id) * 17 + salt * 11) % 29) / 29.0 * interval : result;
}
inline Value engine_token(double token) {
    if (token == 0)
        return Value();
    if (token < 0 || token != std::floor(token) || token > 9007199254740991.0)
        throw std::invalid_argument("kernel engine token");
    Value v = Value::object();
    v["_engine_token"] = Value(static_cast<int64_t>(token));
    return v;
}
inline double engine_token_id(const Value &v) { return field(v, "_engine_token"); }
inline double angle_delta(double v) {
    while (v > 3.141592653589793)
        v -= 6.283185307179586;
    while (v < -3.141592653589793)
        v += 6.283185307179586;
    return v;
}
struct Aim {
    struct Cached {
        Value signature, solution;
        double deadline;
    };
    Value config;
    Engine &engine;
    Factors factors;
    std::map<int, Gunnery> &gunners;
    std::map<int, Cached> cache;
    std::map<int, Value> intents, reproofs;
    int round;
    Aim(const Value &v, Engine &e, std::map<int, Gunnery> &g, int round)
        : config(v), engine(e), factors(v.get("factors")), gunners(g), round(round) {}
    static const Value &profile(const Bot &bot) { return bot.config.get("aim"); }
    static Value physical(const Bot &bot, int shell) {
        const Value &shells = profile(bot).get("shells");
        return shell >= 0 && shell < static_cast<int>(shells.size()) ? shells[shell] : Value();
    }
    static void pose(std::vector<double> &out, const Value &s) {
        std::set<std::string> destroyed = names(s.get("critical").get("destroyed"));
        for (const char *name : {"yaw", "pitch", "roll"})
            out.push_back(field(s, name));
        out.push_back(field(s, "terrain_pitch", field(s, "pitch") - field(s, "suspension_pitch")));
        for (const char *name : {"suspension_pitch", "turret_yaw", "gun_pitch"})
            out.push_back(field(s, name));
        out.push_back(field(s, "aim_yaw", field(s, "yaw")));
        out.push_back(integer(s, "movement_dir") != 0);
        out.push_back(destroyed.count("engineHealth"));
        out.push_back(destroyed.count("leftTrackHealth") || destroyed.count("rightTrackHealth"));
        out.push_back(flag(s, "_overturned"));
        out.push_back(integer(s, "siege_state"));
    }
    static void point(std::vector<double> &out, const Value &v) {
        for (size_t i = 0; i < 3; ++i)
            out.push_back(v[i].number());
    }
    static Value solution(const double *result, const std::string &arc) {
        if (result[0] == 0)
            return Value();
        Value v = Value::object();
        v["aim_position"] = vector3(result[1], result[2], result[3]);
        v["pitch"] = Value(result[4]);
        v["flight_time"] = Value(result[5]);
        v["yaw"] = Value(result[6]);
        v["arc"] = Value(arc);
        return v;
    }
    Value origin(const Bot &bot, int shell, int seq, double yaw, double pitch, double time) {
        auto result =
            engine.query(761, bot.state, Value::object(),
                         {static_cast<double>(shell), static_cast<double>(seq), yaw, pitch, time});
        return result[0] != 0 ? vector3(result[1], result[2], result[3]) : Value();
    }
    Value exact_origin(const Bot &bot, int shell) {
        Value d = world_barrel(bot.state);
        double horizontal =
            std::sqrt(d[0].number() * d[0].number() + d[2].number() * d[2].number());
        return origin(bot, shell, integer(bot.state, "fire_seq") + 1,
                      std::atan2(d[0].number(), d[2].number()),
                      -std::atan2(d[1].number(), std::max(1e-12, horizontal)), 0);
    }
    Value part(const Bot &bot, const Value &target) {
        auto result = engine.query(762, bot.state, target);
        if (result[0] == 0)
            return Value();
        Value v = Value::object();
        v["aim_position"] = vector3(result[1], result[2], result[3]);
        v["aim_token"] = engine_token(result[4]);
        return v;
    }
    bool reachable(const Bot &bot, double yaw, double pitch) {
        std::vector<double> packet = {104, field(profile(bot), "handle")};
        pose(packet, bot.state);
        packet.push_back(yaw);
        packet.push_back(pitch);
        offline_combat_dispatch(packet.data(), packet.size());
        return packet[0] != 0;
    }
    Value intercept(const Bot &bot, const Value &start, const Value &target, const Value &speed,
                    const Value &shell, bool high) {
        std::vector<double> packet = {101, field(profile(bot), "handle")};
        pose(packet, bot.state);
        point(packet, start);
        point(packet, target);
        point(packet, speed);
        packet.insert(packet.end(), {shell[0].number(), shell[1].number(), -1.5707963267948966,
                                     1.5707963267948966, static_cast<double>(high),
                                     field(config, "maximum_time"), shell[2].number()});
        size_t offset = packet.size();
        packet.resize(offset + 7, 0);
        offline_combat_dispatch(packet.data(), packet.size());
        return solution(packet.data() + offset, high ? "high" : "low");
    }
    Value local(const Bot &bot, const Value &target, int shell) {
        Value physics = physical(bot, shell);
        if (target.kind == Value::Null || physics.kind == Value::Null)
            return Value();
        Value start = exact_origin(bot, shell);
        if (start.kind == Value::Null)
            return Value();
        Value aim, token;
        if (flag(config, "has_part")) {
            Value selected = part(bot, target);
            if (selected.kind == Value::Null)
                return Value();
            aim = selected.get("aim_position");
            token = selected.get("aim_token");
        } else {
            aim = target_position(target).copy();
            aim[1] = Value(aim[1].number() + 1);
        }
        Value result = intercept(bot, start, aim, velocity(target), physics, false);
        if (result.kind != Value::Null) {
            result["_origin"] = start;
            result["_aim_token"] = token;
        }
        return result;
    }
    Value signature(const Bot &bot, const Value &target, int shell) {
        Value v = Value::array();
        v.append(Value(target.get("kind").text()));
        v.append(target.has("network_id") ? target.get("network_id") : target.get("id"));
        v.append(Value(flag(target, "alive", true)));
        v.append(Value(shell));
        v.append(profile(bot).get("handle"));
        v.append(Value(integer(bot.state, "siege_state")));
        v.append(Value(integer(bot.state, "fire_seq")));
        return v;
    }
    std::pair<Value, bool> cadenced(Bot &bot, const Value &target, int shell, double now,
                                    bool refresh, bool force = false) {
        int id = integer(bot.state, "id");
        Value key = signature(bot, target, shell);
        auto at = cache.find(id);
        bool fresh = force || at == cache.end() || at->second.signature != key ||
                     (refresh && now + 1e-9 >= at->second.deadline);
        if (!fresh)
            return {at->second.solution, false};
        Value aimed = gunners.at(id).aimed(bot.state, target, now, round, bot.gun);
        Value result = bot.state.get("profile").get("class_tag").text() == "SPG"
                           ? artillery(bot, aimed, shell, now)
                           : local(bot, aimed, shell);
        cache[id] =
            Cached{key, result,
                   cache_deadline(now, id, field(config, "action_seconds"), 13, at == cache.end())};
        return {result, true};
    }
    Value slew(Bot &bot, const Value &command, const Value &target, double dt) {
        Value &s = bot.state;
        const Value &solution = command.get("_ballistic_solution");
        double desired = field(s, "aim_yaw", field(s, "yaw")), pitch = 0, horizontal = 0;
        if (target.kind != Value::Null || solution.kind == Value::Object) {
            Value fallback = target.kind != Value::Null ? target_position(target) : position(s),
                  aim = solution.kind == Value::Object ? solution.get("aim_position")
                                                       : command.get("aim_position");
            if (aim.kind != Value::Array || aim.size() != 3)
                aim = fallback;
            Value origin = solution.get("_origin");
            if (origin.kind != Value::Array || origin.size() != 3)
                origin = exact_origin(bot, integer(s, "shell_index"));
            if (origin.kind == Value::Null) {
                s["gun_aligned"] = Value(false);
                Value result = Value::array();
                result.append(Value(field(s, "aim_yaw")));
                result.append(Value(0.0));
                return result;
            }
            double dx = aim[0].number() - origin[0].number(),
                   dz = aim[2].number() - origin[2].number();
            horizontal = std::sqrt(dx * dx + dz * dz);
            desired = solution.kind == Value::Object ? field(solution, "yaw")
                      : horizontal > .1              ? std::atan2(dx, dz)
                                                     : field(s, "yaw");
            pitch = solution.kind == Value::Object
                        ? field(solution, "pitch")
                        : -std::atan2((aim[1].number() + 1) - origin[1].number(),
                                      std::max(.5, horizontal));
        }
        std::vector<double> packet = {102, field(profile(bot), "handle")};
        pose(packet, s);
        packet.insert(
            packet.end(),
            {desired, pitch, dt, static_cast<double>(target.kind != Value::Null),
             field(profile(bot), "turret_speed") * factors.stat(s, *bot.critical, "turret_speed"),
             field(profile(bot), "gun_speed")});
        size_t offset = packet.size();
        packet.resize(offset + 8, 0);
        offline_combat_dispatch(packet.data(), packet.size());
        const char *names[] = {"terrain_pitch", "suspension_pitch",  "pitch",  "turret_yaw",
                               "gun_pitch",     "desired_gun_pitch", "aim_yaw"};
        for (size_t i = 0; i < 7; ++i)
            s[names[i]] = Value(packet[offset + i]);
        s["gun_aligned"] = Value(packet[offset + 7] != 0);
        Value result = Value::array();
        result.append(Value(desired));
        result.append(Value(horizontal));
        return result;
    }
    bool direct_matches(const Bot &bot, const Value &target, const Value &solution) {
        if (!flag(config, "has_part"))
            return true;
        Value selected = part(bot, target);
        bool matches = selected.kind == Value::Object && solution.kind == Value::Object &&
                       selected.get("aim_token") == solution.get("_aim_token");
        if (!matches)
            cache.erase(integer(bot.state, "id"));
        return matches;
    }
    Value preview(const Bot &bot, int shell, const Value &solution, const Subshot *edge = nullptr) {
        double time = field(solution, "flight_time", -1);
        if (solution.kind != Value::Object && edge) {
            Value p = physical(bot, shell);
            if (p.kind == Value::Null)
                return Value();
            double speed = p[0].number(), maximum = p[2].number();
            if (speed > 0 && maximum > 0)
                time = std::min(field(config, "maximum_time"), maximum / speed);
        }
        if (!std::isfinite(time) || time <= 0 || time > field(config, "maximum_time"))
            return Value();
        int seq = integer(bot.state, "fire_seq") + 1, group = seq, index = 0;
        if (edge) {
            seq = edge->seq;
            group = edge->group;
            index = edge->index;
            if (seq != integer(bot.state, "fire_seq") + 1)
                return Value();
        }
        Value angles = dispersed(
            integer(bot.state, "id"), round, seq, field(bot.state, "aim_yaw"),
            field(bot.state, "gun_pitch"),
            std::max(bot.gun.dispersion,
                     bot.gun.fully_aimed * factors.stat(bot.state, *bot.critical, "dispersion")),
            index, group, dispersal_base(bot.state));
        Value start = origin(bot, shell, seq, angles[0].number(), angles[1].number(), time);
        if (start.kind == Value::Null)
            return Value();
        Value result = Value::object();
        result["fire_seq"] = Value(seq);
        result["shell_index"] = Value(shell);
        result["shot_yaw"] = angles[0];
        result["shot_pitch"] = angles[1];
        result["flight_time"] = Value(time);
        result["origin"] = start;
        return result;
    }
    static Value target_identity(const Value &target) {
        if (target.kind != Value::Object || (!target.has("network_id") && !target.has("id")))
            return Value();
        Value id = target.has("network_id") ? target.get("network_id") : target.get("id");
        if (id.kind == Value::Null)
            return Value();
        Value v = Value::array();
        v.append(Value(target.get("kind").text()));
        v.append(Value(id.exact()));
        return v;
    }
    static Value source_pose(const Value &s) {
        Value v = Value::array();
        for (const char *name : {"x", "y", "z", "yaw", "pitch", "roll", "turret_yaw", "gun_pitch"})
            v.append(Value(field(s, name)));
        return v;
    }
    bool cancel(Bot &bot, bool preserve = false) {
        int id = integer(bot.state, "id");
        bool had = intents.erase(id) > 0, proof = reproofs.count(id) > 0;
        if (!preserve)
            reproofs.erase(id);
        if (had && flag(config, "has_cancel"))
            engine.query(766, bot.state, Value::object());
        return had || proof;
    }
    Value active_reproof(Bot &bot, const Value &target, int shell, double now) {
        int id = integer(bot.state, "id");
        auto at = reproofs.find(id);
        if (at == reproofs.end())
            return Value();
        Value proof = at->second, pose = source_pose(bot.state);
        const Value &before = proof.get("source_pose");
        double moved = 0;
        bool turned = false;
        for (size_t i = 0; i < 3; ++i) {
            double d = pose[i].number() - before[i].number();
            moved += d * d;
        }
        moved = std::sqrt(moved);
        for (size_t i = 3; i < std::min(pose.size(), before.size()); ++i)
            turned = turned || std::abs(angle_delta(pose[i].number() - before[i].number())) > .001;
        bool invalid = target.kind != Value::Object || !flag(target, "alive", true) ||
                       (target.has("health") && field(target, "health") <= 0) ||
                       target_identity(target) != proof.get("target_identity") ||
                       shell != integer(proof, "shell_index") ||
                       integer(bot.state, "fire_seq") + 1 != integer(proof, "fire_seq") ||
                       physical(bot, shell) != proof.get("physical") || moved > .05 || turned;
        if (invalid || now > field(proof, "deadline") + 1e-9) {
            cancel(bot);
            return Value();
        }
        return proof;
    }
    Value active_intent(Bot &bot, const Value &target, int shell, double now) {
        auto at = intents.find(integer(bot.state, "id"));
        if (at == intents.end())
            return Value();
        Value intent = at->second;
        return active_reproof(bot, target, shell, now).kind == Value::Null ? Value() : intent;
    }
    static bool aligned(const Bot &bot, const Value &solution) {
        if (!flag(bot.state, "gun_aligned") || solution.kind != Value::Object)
            return false;
        Value d = world_barrel(bot.state);
        double yaw = std::atan2(d[0].number(), d[2].number()),
               pitch = -std::atan2(d[1].number(),
                                   std::max(1e-12, std::sqrt(d[0].number() * d[0].number() +
                                                             d[2].number() * d[2].number())));
        return std::abs(angle_delta(field(solution, "yaw") - yaw)) <= 1e-7 &&
               std::abs(field(solution, "pitch") - pitch) <= 1e-7;
    }
    Value create_intent(Bot &bot, const Value &target, int shell, const Value &solution,
                        double now) {
        Value physics = physical(bot, shell), identity = target_identity(target);
        if (physics.kind == Value::Null || identity.kind == Value::Null ||
            !flag(target, "alive", true) ||
            (target.has("health") && field(target, "health") <= 0) ||
            std::abs(field(bot.state, "speed")) > .05 || !aligned(bot, solution))
            return Value();
        int id = integer(bot.state, "id"), seq = integer(bot.state, "fire_seq") + 1;
        Value proof = active_reproof(bot, target, shell, now);
        if (proof.kind == Value::Null) {
            proof = Value::object();
            Value source = Value::object();
            source["id"] = Value(id);
            proof["source"] = source;
            proof["source_pose"] = source_pose(bot.state);
            proof["target_identity"] = identity;
            proof["shell_index"] = Value(shell);
            proof["fire_seq"] = Value(seq);
            proof["physical"] = physics;
            proof["proof_latency"] = Value(0.0);
            proof["attempts"] = Value(0);
            proof["created"] = Value(now);
            proof["deadline"] = Value(now + field(config, "intent_seconds"));
            proof["absolute_deadline"] = Value(now + field(config, "total_seconds"));
            reproofs[id] = proof;
        } else if (integer(proof, "attempts"))
            proof["deadline"] =
                Value(std::min(field(proof, "absolute_deadline", field(proof, "deadline")),
                               now + field(config, "reproof_seconds")));
        Value angles = dispersed(
            id, round, seq, field(bot.state, "aim_yaw"), field(bot.state, "gun_pitch"),
            std::max(bot.gun.dispersion,
                     bot.gun.fully_aimed * factors.stat(bot.state, *bot.critical, "dispersion")),
            0, seq, dispersal_base(bot.state));
        Value frozen = solution.copy();
        proof["hold_solution"] = frozen.copy();
        Value intent = Value::object();
        for (const char *name : {"source", "source_pose", "target_identity", "shell_index",
                                 "fire_seq", "physical", "deadline"})
            intent[name] = proof.get(name);
        intent["solution"] = frozen;
        intent["shot_yaw"] = angles[0];
        intent["shot_pitch"] = angles[1];
        intent["created"] = Value(now);
        intents[id] = intent;
        return intent;
    }
    Value reproof_solution(Bot &bot, const Value &target, int shell, const Value &proof) {
        Value physics = physical(bot, shell);
        if (physics.kind == Value::Null || target.kind != Value::Object)
            return Value();
        Value start = exact_origin(bot, shell);
        if (start.kind == Value::Null)
            return Value();
        Value aim = target_position(target).copy(), v = velocity(target);
        aim[1] = Value(aim[1].number() + 1);
        double latency = std::max(0.0, field(proof, "proof_latency"));
        for (size_t i = 0; i < 3; ++i)
            aim[i] = Value(aim[i].number() + v[i].number() * latency);
        std::string arc = proof.get("arc").text();
        if (arc != "low" && arc != "high")
            return Value();
        return intercept(bot, start, aim, v, physics, arc == "high");
    }
    Value artillery(Bot &bot, const Value &target, int shell, double now) {
        Value intent = active_intent(bot, target, shell, now);
        if (intent.kind != Value::Null)
            return intent.get("solution").copy();
        Value proof = active_reproof(bot, target, shell, now);
        if (proof.kind != Value::Null && integer(proof, "attempts"))
            return reproof_solution(bot, target, shell, proof);
        if (!flag(config, "has_solution"))
            return Value();
        auto answer = engine.query(763, bot.state, target, {static_cast<double>(shell), now});
        if (answer[0] == 0 || answer[5] <= 0 || answer[5] > field(config, "maximum_time") ||
            !reachable(bot, answer[6], answer[4]))
            return Value();
        Value result = solution(answer.data(), answer[7] == 1 ? "high" : "low");
        result["_engine_payload"] = engine_token(answer[8]);
        return result;
    }
    Value validated_receipt(const Bot &bot, int shell, int seq, double yaw, double pitch,
                            double time, const std::array<double, 256> &r) {
        Value physics = physical(bot, shell);
        if (r[0] == 0 || physics.kind == Value::Null)
            return Value();
        for (size_t i = 1; i < 16; ++i)
            if (!std::isfinite(r[i]))
                return Value();
        int max_ms = static_cast<int>(r[11]);
        if (static_cast<int>(r[12]) != seq || static_cast<int>(r[13]) != shell || r[7] != yaw ||
            r[8] != pitch || r[14] != time || max_ms <= 0 || max_ms > 20000 ||
            r[9] != physics[1].number() || r[10] != physics[2].number())
            return Value();
        double speed = physics[0].number(), horizontal = std::cos(pitch);
        std::array<double, 3> velocity = {{std::sin(yaw) * horizontal * speed,
                                           std::sin(pitch) * speed,
                                           std::cos(yaw) * horizontal * speed}};
        for (size_t i = 0; i < 3; ++i)
            if (std::abs(r[4 + i] - velocity[i]) > 1e-7)
                return Value();
        Value result = Value::object();
        result["origin"] = vector3(r[1], r[2], r[3]);
        result["velocity"] = vector3(r[4], r[5], r[6]);
        result["shot_yaw"] = Value(r[7]);
        result["shot_pitch"] = Value(r[8]);
        result["gravity"] = Value(r[9]);
        result["max_distance"] = Value(r[10]);
        result["max_time_ms"] = Value(max_ms);
        result["fire_seq"] = Value(seq);
        result["shell_index"] = Value(shell);
        result["flight_time"] = Value(r[14]);
        result["proof_key"] = engine_token(r[15]);
        result["_engine_payload"] = engine_token(r[16]);
        return result;
    }
    bool reject_stale(Bot &bot, const Value &target, int shell, const Value &intent,
                      const Value &receipt, double now) {
        const Value &solution = intent.get("solution"), &intended = solution.get("aim_position");
        if (intended.kind != Value::Array || intended.size() != 3) {
            cancel(bot);
            return true;
        }
        Value p = target_position(target), v = velocity(target);
        double error = 0, time = field(receipt, "flight_time");
        for (size_t i = 0; i < 3; ++i) {
            double projected = p[i].number() + (i == 1 ? 1 : 0) + v[i].number() * time,
                   d = projected - intended[i].number();
            if (!std::isfinite(d)) {
                cancel(bot);
                return true;
            }
            error += d * d;
        }
        error = std::sqrt(error);
        if (error <= field(config, "staleness_metres") + 1e-9)
            return false;
        Value proof = active_reproof(bot, target, shell, now);
        if (proof.kind == Value::Null) {
            cancel(bot);
            return true;
        }
        double latency = std::max(0.0, now - field(intent, "created"));
        proof["proof_latency"] = Value(latency);
        proof["attempts"] = Value(integer(proof, "attempts") + 1);
        proof["deadline"] =
            Value(std::min(field(proof, "absolute_deadline", field(proof, "deadline")),
                           now + field(config, "reproof_seconds")));
        proof["last_proof_latency"] = Value(latency);
        proof["last_aim_staleness"] = Value(error);
        proof["arc"] = Value(solution.get("arc").text());
        cancel(bot, true);
        return true;
    }
    Value launch_receipt(Bot &bot, const Value &target, int shell, const Value &solution,
                         double now) {
        if (!flag(config, "has_launch"))
            return Value();
        Value intent = active_intent(bot, target, shell, now);
        if (intent.kind == Value::Null)
            intent = create_intent(bot, target, shell, solution, now);
        if (intent.kind == Value::Null || !aligned(bot, intent.get("solution")))
            return Value();
        int seq = integer(intent, "fire_seq");
        double yaw = field(intent, "shot_yaw"), pitch = field(intent, "shot_pitch"),
               time = field(intent.get("solution"), "flight_time");
        auto answer = engine.query(
            764, bot.state, target,
            {static_cast<double>(shell), static_cast<double>(seq), yaw, pitch, time, now});
        Value receipt = validated_receipt(bot, shell, seq, yaw, pitch, time, answer);
        return receipt.kind == Value::Null || reject_stale(bot, target, shell, intent, receipt, now)
                   ? Value()
                   : receipt;
    }
    Value friendly(Bot &bot, const Value &target, int shell, const Value &launch, bool artillery) {
        std::vector<double> args = {static_cast<double>(artillery), static_cast<double>(shell),
                                    field(launch, "fire_seq"),      field(launch, "shot_yaw"),
                                    field(launch, "shot_pitch"),    field(launch, "flight_time")};
        point(args, launch.get("origin"));
        if (artillery) {
            point(args, launch.get("velocity"));
            args.push_back(field(launch, "gravity"));
            args.push_back(field(launch, "max_distance"));
            args.push_back(field(launch, "max_time_ms"));
            args.push_back(engine_token_id(launch.get("proof_key")));
            args.push_back(engine_token_id(launch.get("_engine_payload")));
        }
        auto answer = engine.query(765, bot.state, target, args);
        Value result = Value::object();
        result["clear"] = Value(answer[0] != 0);
        if (answer[1] != 0) {
            result["blocker_kind"] = Value(answer[2] == 1 ? "player" : "bot");
            result["blocker_id"] = Value(static_cast<int>(answer[3]));
            result["blocker_team"] = Value(static_cast<int>(answer[4]));
            result["blocker_position"] = vector3(answer[5], answer[6], answer[7]);
            result["blocker_radius"] = Value(answer[8]);
        }
        return result;
    }
};
} // namespace offline_kernel
#endif
