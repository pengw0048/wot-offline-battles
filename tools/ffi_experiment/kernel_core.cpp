#include "kernel_core.h"
#include "kernel_bot.h"
#include "kernel_codec.h"
#include "kernel_equipment.h"
#include "kernel_perception.h"
#include "kernel_launch.h"
#include "kernel_lanes.h"
#include "kernel_motion.h"
#include "kernel_aim.h"
#include "kernel_driver.h"
#include "kernel_contacts.h"
#include "kernel_update.h"
#include <cstring>

namespace {
using namespace offline_kernel;
struct Kernel {
    std::map<int, std::unique_ptr<Bot>> bots;
    std::vector<int> order;
    Codec codec;
    std::unique_ptr<Perception> perception;
    std::map<int, Gunnery> gunners;
    Launches launches;
    std::unique_ptr<Engine> engine;
    std::unique_ptr<Lanes> lanes;
    std::unique_ptr<Motion> motion;
    std::unique_ptr<Aim> aim;
    std::map<int, Value> orders;
    std::unique_ptr<Routes> routes;
    std::unique_ptr<Driver> driver;
    Traffic traffic;
    std::unique_ptr<TankContacts> contacts;
    std::unique_ptr<Simulation<Kernel>> simulation;
    int round = 0;
    std::string output;
    explicit Kernel(const Value &config) : codec(config.get("codec")) {
        const Value &states = config.get("bots");
        if (states.kind != Value::Array || states.size() > 30)
            throw std::invalid_argument("kernel roster producer");
        for (const Value &v : states.data->array) {
            int id = integer(v.get("state"), sf::id);
            if (id <= 0 || bots.count(id))
                throw std::invalid_argument("kernel actor identity");
            bots[id].reset(new Bot(v));
            order.push_back(id);
        }
        if (config.has("perception"))
            perception.reset(new Perception(config.get("perception"), bots));
        round = integer(config, "round_id");
        for (int id : order)
            gunners.emplace(id, Gunnery(bots.at(id)->config.get("gunnery")));
        if (config.has("engine"))
            engine.reset(new Engine(config.get("engine")));
        if (config.has("lanes") && perception && engine)
            lanes.reset(new Lanes(config.get("lanes"), *perception, *engine));
        if (config.has("motion") && engine)
            motion.reset(new Motion(config.get("motion"), bots, *engine));
        if (config.has("aim") && engine)
            aim.reset(new Aim(config.get("aim"), *engine, gunners, round));
        if (config.has("orders"))
            for (const Value &v : elements(config.get("orders")))
                orders[static_cast<int>(v[0].exact())] = v[1];
        if (config.has("driver") && motion) {
            routes.reset(new Routes(config.get("driver"), bots, orders));
            driver.reset(new Driver(config.get("driver"), *motion, *routes));
        }
        if (config.has(sf::contacts) && motion && engine)
            contacts.reset(new TankContacts(config.get(sf::contacts), bots, *motion, *engine));
        if (config.has("simulation"))
            simulation.reset(new Simulation<Kernel>(*this, config.get("simulation")));
        if (simulation)
            perception->engine = engine.get();
    }
};
std::map<int, std::unique_ptr<Kernel>> kernels;
int next_kernel = 1;
struct Reader {
    double *b;
    int count, index = 1;
    Reader(double *values, int size) : b(values), count(size) {}
    int integer(int low = 0, int high = 1000000000) {
        if (index >= count || !std::isfinite(b[index]) || b[index] != std::floor(b[index]) ||
            b[index] < low || b[index] > high)
            throw std::invalid_argument("kernel command integer");
        return static_cast<int>(b[index++]);
    }
    Value value() {
        int size = integer(0, 12000000);
        if (size > count - index)
            throw std::invalid_argument("kernel command JSON width");
        std::string s;
        s.reserve(size);
        for (int i = 0; i < size; ++i)
            s.push_back(static_cast<char>(integer(0, 255)));
        return JsonReader(s).read();
    }
    void end() {
        if (index != count)
            throw std::invalid_argument("kernel command width");
    }
};
Kernel &owner(int id) {
    auto at = kernels.find(id);
    if (at == kernels.end())
        throw std::invalid_argument("kernel owner");
    return *at->second;
}
Value subshots(const std::vector<Subshot> &values) {
    Value out = Value::array();
    for (const Subshot &s : values) {
        Value v = Value::object();
        v["shot_seq"] = Value(s.seq);
        v[sf::burst_group_seq] = Value(s.group);
        v[sf::burst_index] = Value(s.index);
        v[sf::burst_count] = Value(s.count);
        v[sf::shell_index] = Value(s.shell);
        v["final"] = Value(s.final);
        v["due_offset"] = Value(s.offset);
        out.append(v);
    }
    return out;
}
Value weapon_action(Bot &bot, const Value &action) {
    const Value &args = action.get("args");
    std::string name = action.get("method").text();
    auto n = [&](size_t i, double fallback = 0) {
        return args.kind == Value::Array && i < args.size() ? args[i].number(fallback) : fallback;
    };
    Gun &g = bot.gun;
    Ammo &a = bot.ammo;
    Burst &b = bot.burst;
    Value result;
    if (name == "gun.tick")
        g.tick(n(0));
    else if (name == "gun.tick_dispersion")
        g.bloom_tick(n(0), n(1), n(2), n(3), n(4, 1), n(5, 1));
    else if (name == "gun.commit_shot_bloom")
        g.shot_bloom(n(0, 1), n(1, 1) != 0);
    else if (name == "gun.rescale_reload")
        result = Value(g.rescale(n(0)));
    else if (name == "gun.duration")
        result = Value(g.duration(n(0, 1)));
    else if (name == "gun.ready")
        result = Value(g.ready(n(0, 1)));
    else if (name == "gun.remaining")
        result = Value(g.remaining(n(0, 1)));
    else if (name == "gun.complete_reload") {
        int value = g.complete(n(0, 1), static_cast<int>(n(1, -1)));
        if (value >= 0)
            result = Value(value ? "intra" : "full");
    } else if (name == "gun.require_full_reload")
        g.require_full();
    else if (name == "gun.shell_index")
        result = Value(g.shell(static_cast<int>(n(0))));
    else if (name == "gun.begin_burst")
        result = Value(g.begin_burst(static_cast<int>(n(0)), n(1, 1)));
    else if (name == "gun.fire_burst_round")
        result = Value(g.fire_round(n(0) != 0));
    else if (name == "gun.fire")
        result = Value(g.begin_burst(1, n(0, 1)) && g.fire_round(true));
    else if (name == "gun.cancel_burst")
        result = Value(g.cancel_burst());
    else if (name == "ammo.stage")
        result = Value(a.stage(static_cast<int>(n(0)), n(1) != 0, n(2, 1) != 0));
    else if (name == "ammo.can_fire")
        result = Value(a.can_fire(n(0) != 0));
    else if (name == "ammo.consume_loaded")
        result = Value(a.consume(n(0) != 0));
    else if (name == "ammo.planned_rounds")
        result = Value(a.planned());
    else if (name == "ammo.loaded_shell_requires_full_reload")
        result = Value(a.requires_full());
    else if (name == "burst.start")
        result = Value(
            b.start(static_cast<int>(n(0)), static_cast<int>(n(1)), n(2), static_cast<int>(n(3))));
    else if (name == "burst.advance")
        result = subshots(b.advance(n(0)));
    else if (name == "burst.cancel")
        result = Value(b.cancel(static_cast<int>(n(0, -1))));
    else
        throw std::invalid_argument("kernel weapon test operation");
    return result;
}
} // namespace
extern "C" void offline_kernel_reset() { kernels.clear(); }
extern "C" int offline_kernel_dispatch(double *b, int count) {
    Reader r(b, count);
    int op = static_cast<int>(b[0]);
    if (op == 700) {
        Value config = r.value();
        r.end();
        std::unique_ptr<Kernel> kernel(new Kernel(config));
        int id = next_kernel++;
        kernels[id] = std::move(kernel);
        b[0] = id;
        return 0;
    }
    int id = r.integer(1);
    Kernel &k = owner(id);
    if (op == 705) {
        r.end();
        kernels.erase(id);
        return 0;
    }
    if (op == 709) {
        if (k.output.size() + 1 > static_cast<size_t>(count))
            throw std::invalid_argument("kernel output capacity");
        b[0] = k.output.size();
        for (size_t i = 0; i < k.output.size(); ++i)
            b[i + 1] = static_cast<unsigned char>(k.output[i]);
        return 0;
    }
    if (op == 719) {
        if (k.output.size() > static_cast<size_t>(count - 1) * sizeof(double))
            throw std::invalid_argument("kernel packed output capacity");
        b[0] = k.output.size();
        std::memcpy(b + 1, k.output.data(), k.output.size());
        return 0;
    }
    if (op == 710) {
        int bot = r.integer(1);
        auto at = k.bots.find(bot);
        if (at == k.bots.end())
            throw std::invalid_argument("kernel weapon actor");
        Value actions = r.value();
        r.end();
        Value outputs = Value::array();
        for (const Value &action : elements(actions)) {
            Value row = Value::object();
            row["result"] = weapon_action(*at->second, action);
            row["state"] = at->second->snapshot();
            outputs.append(row);
        }
        k.output = json(outputs);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 711) {
        Value values = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(values))
            rows.append(k.codec.row(v));
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 712) {
        int bot = r.integer(1);
        auto at = k.bots.find(bot);
        if (at == k.bots.end())
            throw std::invalid_argument("kernel equipment actor");
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &action : elements(actions)) {
            int slot = integer(action, sf::slot);
            auto &equipment = at->second->equipment;
            if (slot < 0 || slot >= static_cast<int>(equipment.size()))
                throw std::invalid_argument("kernel equipment slot");
            Equipment &e = equipment[slot];
            Value result;
            std::string method = action.get("method").text();
            double now = field(action, sf::now);
            const Value &critical = action.get(sf::critical);
            bool stunned = flag(action, "stunned");
            if (method == "ready")
                result = Value(e.ready(now));
            else if (method == "poll_bot")
                result = e.poll_bot(now, critical, stunned);
            else if (method == "poll_auto")
                result = e.poll_auto(now, critical);
            else if (method == "activate")
                result = e.activate(now, critical, action.get("selected"),
                                    flag(action, "requested_active"), stunned);
            else
                throw std::invalid_argument("kernel equipment operation");
            Value row = Value::object(), snapshots = Value::array(), wires = Value::array();
            for (const Equipment &item : equipment) {
                snapshots.append(item.snapshot());
                wires.append(item.wire(now));
            }
            row["result"] = result;
            row["states"] = snapshots;
            row["wires"] = wires;
            row["passives"] = equipment_passives(equipment);
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 713) {
        Value inputs = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(inputs))
            rows.append(Value(rounded(v[0].number(), static_cast<int>(v[1].exact()))));
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 714) {
        int id = r.integer(1);
        auto at = k.bots.find(id);
        if (at == k.bots.end())
            throw std::invalid_argument("kernel critical actor");
        Bot &bot = *at->second;
        Value inputs = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(inputs)) {
            std::string method = v.get("method").text();
            Value result;
            double dt = field(v, sf::dt), now = field(v, sf::now),
                   equipment_now = field(v, "equipment_now");
            if (method == "set") {
                const Value &patch = v.get("state");
                if (patch.kind != Value::Object)
                    throw std::invalid_argument("kernel test patch");
                patch.visit(
                    [&](const std::string &name, const Value &value) { bot.state[name] = value; });
            } else if (method == "critical")
                result =
                    Value(bot.advance_critical(dt, now, equipment_now, flag(v, "record", true),
                                               flag(v, "advance_fire", true), v.get("effects")));
            else if (method == "drowning")
                result = Value(bot.advance_drowning(dt, field(v, "depth", -1)));
            else if (method == "overturn")
                result = Value(bot.advance_overturn(dt));
            else if (method == "publish")
                result = Value(bot.mark_combat());
            else
                throw std::invalid_argument("kernel critical operation");
            Value row = Value::object();
            row["result"] = result;
            row["state"] = bot.state.clone();
            row["sync"] = bot.sync.clone();
            row["turn_speed"] = Value(bot.turn_speed);
            row["clear_reposition"] = Value(bot.clear_reposition);
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 715) {
        if (!k.perception)
            throw std::invalid_argument("kernel perception not configured");
        Perception &p = *k.perception;
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        std::set<int> processed;
        std::map<int, Contacts> collected;
        for (const Value &v : elements(actions)) {
            std::string method = v.get("method").text();
            double now = field(v, sf::now);
            Value result;
            if (method == "begin")
                p.begin();
            else if (method == "finish")
                p.finish();
            else if (method == "slice") {
                p.start_slice(v.get("players"), k.order);
                processed.clear();
            } else if (method == "lifecycle")
                p.lifecycle(now);
            else if (method == "humans")
                p.append_humans(now);
            else if (method == "pack") {
                if (!k.lanes)
                    throw std::invalid_argument("kernel lanes absent");
                result = k.lanes->pack(now);
            } else if (method == "merge") {
                if (!k.lanes)
                    throw std::invalid_argument("kernel lanes absent");
                k.lanes->merge(now);
                result = Value(true);
            } else if (method == "service") {
                if (!k.lanes)
                    throw std::invalid_argument("kernel lanes absent");
                Lanes &lanes = *k.lanes;
                std::vector<int> order = k.order;
                std::stable_sort(order.begin(), order.end(), [&](int a, int b) {
                    return std::make_pair(integer(k.bots.at(a)->state, sf::slot),
                                          integer(k.bots.at(a)->state, sf::team, 1)) <
                           std::make_pair(integer(k.bots.at(b)->state, sf::slot),
                                          integer(k.bots.at(b)->state, sf::team, 1));
                });
                std::map<LaneKey, int> selected;
                for (const Value &row : elements(v.get("selected")))
                    selected[LaneKey{
                        {static_cast<int>(row[0].exact()), static_cast<int>(row[1].exact()),
                         static_cast<int>(row[2].exact())}}] = static_cast<int>(row[3].exact());
                int budget = integer(v, "budget");
                lanes.incoming_budget = integer(v, "incoming_budget");
                int pending =
                    lanes.service(now, field(v, "cycle"), order, processed, selected, budget);
                result = Value::array();
                result.append(Value(pending));
                result.append(Value(budget));
                result.append(Value(lanes.incoming_budget));
            } else if (method == "prepare") {
                std::vector<int> ordered = k.order;
                std::stable_sort(ordered.begin(), ordered.end(), [&](int a, int b) {
                    const Value &x = k.bots.at(a)->state, &y = k.bots.at(b)->state;
                    return std::make_pair(integer(x, sf::slot), integer(x, sf::team, 1)) <
                           std::make_pair(integer(y, sf::slot), integer(y, sf::team, 1));
                });
                std::set<int> due;
                for (const Value &id : elements(v.get("due")))
                    due.insert(static_cast<int>(id.exact()));
                std::map<int, ActorKey> selected;
                for (const Value &row : elements(v.get("selected")))
                    selected[static_cast<int>(row[0].exact())] = ActorKey{
                        {static_cast<int>(row[1].exact()), static_cast<int>(row[2].exact())}};
                p.prepare(now, flag(v, "include_humans"), ordered, due, selected);
            } else {
                int id = integer(v, "bot");
                Bot &bot = *k.bots.at(id);
                if (method == "set")
                    update(bot.state, v.get("state"));
                else if (method == "processed")
                    processed.insert(id);
                else if (method == "still")
                    p.note_still(bot.state, now);
                else if (method == "collect")
                    p.collect(bot.state, collected.at(id), processed);
                else if (method == "contacts") {
                    Contacts contacts = p.contacts(bot.state, now, processed);
                    collected[id] = contacts;
                    result = Value::object();
                    result[sf::contacts] = contacts.rows;
                    Value lookup = Value::object();
                    for (const auto &t : contacts.lookup)
                        lookup[std::to_string(t.first)] = t.second;
                    result["targets"] = lookup;
                } else
                    throw std::invalid_argument("kernel perception test operation");
            }
            Value row = Value::object();
            row["result"] = result;
            row["state"] = flag(v, "snapshot") ? p.snapshot() : Value();
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 716) {
        int id = r.integer(1);
        Bot &bot = *k.bots.at(id);
        Gunnery &gunner = k.gunners.at(id);
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(actions)) {
            std::string method = v.get("method").text();
            Value result;
            double now = field(v, sf::now);
            const Value &target = v.get("target");
            if (method == "set")
                update(bot.state, v.get("state"));
            else if (method == "hold")
                result = gunner.hold(bot.state, target, now);
            else if (method == "error")
                result = gunner.error(bot.state, target, now, k.round);
            else if (method == "aimed")
                result = gunner.aimed(bot.state, target, now, k.round, bot.gun);
            else if (method == "ready")
                result = Value(gunner.ready(bot.state, target, now, bot.gun));
            else if (method == "random") {
                Random rng(static_cast<uint32_t>(field(v, "seed")));
                result = Value::array();
                for (int i = 0; i < integer(v, "count"); ++i)
                    result.append(Value(flag(v, "gauss") ? rng.gauss(0, field(v, "sigma", 1))
                                                         : rng.random()));
            } else if (method == "hash")
                result = Value(static_cast<int64_t>(seed_hash(v.get("text").text())));
            else if (method == "dispersed")
                result = dispersed(id, k.round, integer(v, "seq"), field(v, sf::yaw),
                                   field(v, sf::pitch), field(v, "dispersion"), integer(v, "index"),
                                   integer(v, "group", -1), v.get("base"));
            else
                throw std::invalid_argument("kernel gunnery operation");
            Value row = Value::object();
            row["result"] = result;
            row["hold"] = gunner.record.clone();
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 717) {
        int id = r.integer(1);
        Bot &bot = *k.bots.at(id);
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(actions)) {
            std::string method = v.get("method").text();
            Value result;
            double factor = field(v, "factor", 1), dispersion = field(v, "dispersion_factor", 1);
            int64_t time = v.get("time").exact();
            if (method == "set")
                update(bot.state, v.get("state"));
            else if (method == "weapon")
                result = weapon_action(bot, v.get("action"));
            else if (method == "fire")
                result = Value(k.launches.fire(
                    bot, k.round, factor, dispersion, v.get("receipt"), v.get("preview"), time,
                    v.has("base") ? v.get("base") : dispersal_base(bot.state)));
            else if (method == "cancel")
                result = Value(Launches::cancel(bot, factor));
            else if (method == "ack")
                result = Value(k.launches.ack(integer(v, sf::id, id), integer(v, "seq")));
            else if (method == "advance") {
                result = Value::array();
                for (const Subshot &edge : bot.burst.advance(field(v, sf::dt))) {
                    int64_t at = std::min(
                        v.get("end").exact(),
                        time + std::max<int64_t>(
                                   0, static_cast<int64_t>(rounded(edge.offset * 1000000.0, 0))));
                    Value preview = v.get("preview");
                    if (flag(v, "sequence_preview") && preview.kind == Value::Object) {
                        preview = preview.copy();
                        preview[sf::fire_seq] = Value(edge.seq);
                    }
                    bool accepted = k.launches.commit(
                        bot, k.round, factor, dispersion, edge, v.get("receipt"), preview, at,
                        v.has("base") ? v.get("base") : dispersal_base(bot.state));
                    result.append(Value(accepted));
                    if (!accepted) {
                        Launches::cancel(bot, factor);
                        break;
                    }
                }
                publish_burst(bot);
            } else
                throw std::invalid_argument("kernel launch operation");
            Value row = Value::object();
            row["result"] = result;
            row["state"] = bot.snapshot();
            row["launches"] = k.launches.pending.clone();
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 718) {
        if (!k.motion)
            throw std::invalid_argument("kernel motion absent");
        Motion &m = *k.motion;
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(actions)) {
            std::string method = v.get("method").text();
            Value result, command = v.get("command").copy();
            if (method == "begin")
                m.begin();
            else if (method == "finish")
                m.finish();
            else {
                int id = integer(v, sf::id);
                Bot &bot = *k.bots.at(id);
                if (method == "set") {
                    update(bot.state, v.get("state"));
                    if (v.has("turn_speed"))
                        bot.turn_speed = field(v, "turn_speed");
                } else if (method == "step")
                    m.step(bot, command, v.get("target"), field(v, sf::dt), field(v, sf::now),
                           flag(v, "decision_due"), flag(v, "refresh"), flag(v, "siege_lock"),
                           field(v, "siege_yaw", field(bot.state, sf::yaw)),
                           flag(v, "baked_escape"));
                else if (method == "vertical")
                    result = Value(m.vertical(bot, field(v, sf::dt), v.get("tick"),
                                              field(v, "attempted", field(bot.state, sf::yaw))));
                else if (method == "guard")
                    result =
                        Value(m.guard(bot, Motion::point(v.get("tick"), Motion::point(bot.state)),
                                      flag(v, "safe"), field(v, "attempted")));
                else if (method == "slope")
                    result = Value(m.slope(bot, integer(v, "tier"), flag(v, "allow_ungrounded")));
                else if (method == "landing")
                    result = Value(m.landing(bot, field(v, sf::speed)));
                else
                    throw std::invalid_argument("kernel motion operation");
            }
            Value row = Value::object(), states = Value::object(), turns = Value::object(),
                  waiting = Value::array();
            for (int id : k.order) {
                states[std::to_string(id)] = k.bots.at(id)->state.clone();
                turns[std::to_string(id)] = Value(k.bots.at(id)->turn_speed);
            }
            for (const auto &v : m.waiting) {
                Value pair = Value::array();
                pair.append(Value(v.first));
                pair.append(Value(v.second));
                waiting.append(pair);
            }
            row["result"] = result;
            row["states"] = states;
            row["turns"] = turns;
            row["command"] = command;
            row["waiting"] = waiting;
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 720) {
        if (!k.aim)
            throw std::invalid_argument("kernel aim absent");
        Aim &aim = *k.aim;
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(actions)) {
            int id = integer(v, sf::id);
            Bot &bot = *k.bots.at(id);
            std::string method = v.get("method").text();
            Value result;
            const Value &target = v.get("target");
            double now = field(v, sf::now);
            int shell = integer(v, "shell", integer(bot.state, sf::shell_index));
            if (method == "set")
                update(bot.state, v.get("state"));
            else if (method == "cadenced") {
                auto p =
                    aim.cadenced(bot, target, shell, now, flag(v, "refresh"), flag(v, "force"));
                result = Value::array();
                result.append(p.first);
                result.append(Value(p.second));
            } else if (method == "local")
                result = aim.local(bot, target, shell);
            else if (method == "artillery")
                result = aim.artillery(bot, target, shell, now);
            else if (method == "slew")
                result = aim.slew(bot, v.get("command"), target, field(v, sf::dt));
            else if (method == "matches")
                result = Value(aim.direct_matches(bot, target, v.get("solution")));
            else if (method == "preview")
                result = aim.preview(bot, shell, v.get("solution"));
            else if (method == "intent")
                result = aim.create_intent(bot, target, shell, v.get("solution"), now);
            else if (method == "active")
                result = aim.active_intent(bot, target, shell, now);
            else if (method == "proof")
                result = aim.active_reproof(bot, target, shell, now);
            else if (method == "receipt")
                result = aim.launch_receipt(bot, target, shell, v.get("solution"), now);
            else if (method == "friendly")
                result = aim.friendly(bot, target, shell, v.get("launch"), flag(v, "artillery"));
            else if (method == "cancel")
                result = Value(aim.cancel(bot, flag(v, "preserve")));
            else
                throw std::invalid_argument("kernel aim operation");
            Value row = Value::object();
            row["result"] = result.clone();
            row["state"] = bot.state.clone();
            row["hold"] = k.gunners.at(id).record.clone();
            auto intent = aim.intents.find(id), proof = aim.reproofs.find(id);
            row["intent"] = intent == aim.intents.end() ? Value() : intent->second.clone();
            row["proof"] = proof == aim.reproofs.end() ? Value() : proof->second.clone();
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 721) {
        if (!k.driver)
            throw std::invalid_argument("kernel driver absent");
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(actions)) {
            int id = integer(v, sf::id);
            std::string method = v.get("method").text();
            Value result, decision = v.get("decision").clone();
            if (method == "begin") {
                if (k.routes->nav)
                    k.routes->nav->begin(field(v, sf::dt));
            } else if (method == "end") {
                if (k.routes->nav)
                    k.routes->nav->frame_open = false;
            } else {
                Bot &bot = *k.bots.at(id);
                if (method == "set")
                    update(bot.state, v.get("state"));
                else if (method == "orders") {
                    k.orders.clear();
                    for (const Value &row : elements(v.get("orders")))
                        k.orders[static_cast<int>(row[0].exact())] = row[1];
                } else if (method == "route")
                    result = Routes::value(k.routes->target(
                        id, Routes::point(v.get(sf::position)), Routes::point(v.get("goal")),
                        v.get("order"), decision, flag(v, "pending")));
                else if (method == "binding") {
                    const Value &group = v.get("group");
                    auto p =
                        k.routes->binding(id, group, Routes::point(v.get("goal")), v.get("order"));
                    result = tuple_value({Value(p.first), Value(p.second)});
                } else if (method == "lane_goal") {
                    auto p = k.routes->lane_goal(id, Routes::point(v.get(sf::position)),
                                                 Routes::point(v.get("goal")), v.get("order"),
                                                 field(v, sf::now), flag(v, "joining"));
                    result = tuple_value({Routes::value(p.first),
                                          tuple_value({Value(static_cast<int>(p.second.first)),
                                                       Value(static_cast<int>(p.second.second))})});
                } else if (method == "drive") {
                    k.driver->probes.clear();
                    result = k.driver->drive(bot, decision, v.get("order"), flag(v, "pending"));
                } else if (method == "traffic") {
                    k.driver->probes.clear();
                    result = k.traffic.adjust(
                        id, v.get("body"), v.get("command"), v.get(sf::neighbours),
                        field(v, sf::now), [&](double yaw) { return k.driver->clear(bot, yaw); });
                } else
                    throw std::invalid_argument("kernel driver operation");
            }
            Value row = Value::object(), states = Value::object();
            for (int id : k.order)
                states[std::to_string(id)] = k.bots.at(id)->state.clone();
            row["result"] = result.clone();
            row["states"] = states;
            row["decision"] = decision;
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 722) {
        if (!k.contacts)
            throw std::invalid_argument("kernel contacts absent");
        TankContacts &c = *k.contacts;
        Value actions = r.value();
        r.end();
        Value rows = Value::array();
        for (const Value &v : elements(actions)) {
            std::string method = v.get("method").text();
            Value result;
            if (method == "set")
                update(k.bots.at(integer(v, sf::id))->state, v.get("state"));
            else if (method == "ack")
                result = Value(c.ack(integer(v, "player"), integer(v, "seq")));
            else if (method == "resolve") {
                std::vector<TankBody> others;
                for (const Value &v : elements(v.get("others")))
                    others.emplace_back(v);
                std::set<TankContacts::Pair> previous;
                for (const Value &p : elements(v.get("previous")))
                    previous.insert(
                        {static_cast<int>(p[0].exact()), static_cast<int>(p[1].exact())});
                auto out = c.resolve(TankBody(v.get("body")), others,
                                     v.get(sf::now).kind == Value::Null
                                         ? offline_nav::Optional<double>()
                                         : offline_nav::Optional<double>(field(v, sf::now)),
                                     previous, flag(v, "probe"));
                result = Value::object();
                result["correction"] = TankContacts::array(out.correction);
                result["delta_velocity"] = TankContacts::array(out.delta);
                result["ram_events"] = out.events;
                result["ram_diagnostics"] = out.diagnostics;
                Value pairs = Value::array();
                for (auto p : out.contacts)
                    pairs.append(tuple_value({Value(p.first), Value(p.second)}));
                result[sf::contacts] = pairs;
            } else if (method == "step") {
                std::vector<int> ordered = k.order;
                std::stable_sort(ordered.begin(), ordered.end(), [&](int a, int b) {
                    return std::make_pair(integer(k.bots.at(a)->state, sf::slot),
                                          integer(k.bots.at(a)->state, sf::team, 1)) <
                           std::make_pair(integer(k.bots.at(b)->state, sf::slot),
                                          integer(k.bots.at(b)->state, sf::team, 1));
                });
                result = c.step(v.get("players"), ordered, field(v, sf::now), field(v, sf::dt));
            } else if (method == "finish")
                c.finish(k.driver->handle);
            else
                throw std::invalid_argument("kernel contact operation");
            Value states = Value::object(), cooldowns = Value::array(), pairs = Value::array(),
                  leases = Value::object();
            for (int id : k.order)
                states[std::to_string(id)] = k.bots.at(id)->state.clone();
            for (const auto &v : c.cooldowns)
                cooldowns.append(
                    tuple_value({Value(v.first.first), Value(v.first.second), Value(v.second)}));
            for (auto p : c.active)
                pairs.append(tuple_value({Value(p.first), Value(p.second)}));
            for (const auto &v : c.leases)
                leases[std::to_string(v.first)] = Value(v.second);
            Value row = Value::object();
            row["result"] = result;
            row["states"] = states;
            row["cooldowns"] = cooldowns;
            row[sf::contacts] = pairs;
            row["leases"] = leases;
            rows.append(row);
        }
        k.output = json(rows);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 723) {
        if (!k.simulation)
            throw std::invalid_argument("kernel update absent");
        Value input = r.value();
        r.end();
        Value messages = k.simulation->update(input), result = Value::object();
        result["messages"] = messages;
        result["logs"] = k.simulation->diagnostic.records;
        k.simulation->diagnostic.records = Value::array();
        result["tokens"] = k.simulation->retained_tokens(messages);
        result["sample"] = Value(k.simulation->sample);
        result["equipment_now"] = Value(k.simulation->equipment_now);
        result["steps"] = Value(k.simulation->control_steps);
        result["maximum_step"] = Value(k.simulation->maximum_step);
        k.output = json(result);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 724) {
        r.end();
        Value out = Value::object(), states = Value::object();
        for (int id : k.order)
            states[std::to_string(id)] = k.bots.at(id)->snapshot();
        out["bots"] = states;
        out["pending"] = k.launches.pending;
        k.output = json(out);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 726) {
        r.end();
        if (!k.simulation)
            throw std::invalid_argument("kernel diagnostics absent");
        Value out = Value::object(), counts = Value::object();
        for (const auto &v : k.simulation->decision_counts)
            counts[std::to_string(v.first)] = Value(v.second);
        out["decisions"] = counts;
        out["diagnostics"] = k.simulation->diagnostics();
        Value probes = Value::array();
        for (int v : k.motion->probe_totals)
            probes.append(Value(v));
        out["motion_probes"] = probes;
        out["lane_probes"] = Value(k.lanes->probes);
        out["cover_probes"] = Value(k.simulation->cover_probes);
        k.output = json(out);
        b[0] = k.output.size();
        return 0;
    }
    if (op == 725) {
        int bot = r.integer(1), seq = r.integer(1);
        r.end();
        b[0] = k.launches.ack(bot, seq);
        return 0;
    }
    throw std::invalid_argument("kernel command");
}
