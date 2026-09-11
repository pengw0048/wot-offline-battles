// Compare retained native edge records with the previous nested-tuple oracle.
#include "kernel_publication.h"
#include <cassert>
#include <iostream>

using namespace offline_kernel;

static Value tuple(std::initializer_list<Value> values) {
    Value result = Value::array();
    for (const Value &value : values)
        result.append(value);
    return result;
}

static Value oracle(const Value &fields, const std::vector<Value> &states,
                    const std::vector<std::vector<Equipment>> &equipment,
                    const Value &launches, const Value &rams, double now) {
    Value bots = Value::array(), shots = Value::array(), impacts = Value::array();
    for (size_t i = 0; i < states.size(); ++i) {
        const Value &state = states[i];
        Value scalar = Value::array(), items = Value::array(), angles = Value::array();
        for (const Value &name : elements(fields))
            scalar.append(tuple({Value(state.has(name.text())), state.get(name.text())}));
        for (const Equipment &e : equipment[i])
            items.append(tuple({e.contract, Value(e.uses), Value(e.active), Value(e.ready_at),
                                Value(now >= e.ready_at),
                                e.auto_pending ? Value(e.auto_since) : Value(),
                                e.ai_pending ? Value(e.ai_since) : Value()}));
        for (const char *name : {"shot_yaw", "shot_pitch"})
            angles.append(tuple({Value(state.has(name)), state.get(name)}));
        bots.append(tuple({scalar, state.get(sf::ammo_remaining), items, angles}));
    }
    for (const Value &v : elements(launches))
        shots.append(tuple({v.get(sf::id), v.get(sf::fire_seq)}));
    for (const Value &v : elements(rams))
        impacts.append(tuple({v.get("bot_id"), v.get(sf::target_kind), v.get(sf::target_id),
                              v.get("ram_seq")}));
    return tuple({bots, shots, impacts});
}

int main() {
    Value fields = tuple({Value("id"), Value("health"), Value("custom")});
    PublicationEdges edges(fields);
    Value old, launches = Value::array(), rams = Value::array();
    std::vector<Value> states;
    std::vector<std::vector<Equipment>> equipment;
    double now = 0;
    int cases = 0, changes = 0;
    auto check = [&]() {
        Value expected = oracle(fields, states, equipment, launches, rams, now);
        edges.begin(states.size());
        for (size_t i = 0; i < states.size(); ++i)
            edges.capture(i, states[i], equipment[i], now);
        bool changed = edges.finish(launches, rams);
        assert(changed == (old != expected));
        if (changed) {
            old = expected;
            ++changes;
        }
        ++cases;
        return changed;
    };
    assert(check()); // The first empty publication is still an edge.
    assert(!check());
    for (bool record : {false, true}) {
        states = {record ? Value::record() : Value::object()};
        equipment.resize(1);
        assert(check());
        assert(!check());
        states[0][sf::health] = Value();
        assert(check()); // A present null differs from a missing field.
        states[0].erase(sf::health);
        assert(check());
        states[0][sf::health] = Value(1);
        assert(check());
        states[0][sf::health] = Value(true);
        assert(!check()); // Preserve Value's numeric equality.
        states[0][sf::x] = Value(70);
        now += .01;
        assert(!check()); // Continuous state does not create durable edges.
        states[0]["custom"] = Value("descriptor");
        assert(check());
        auto transition = [&]() {
            assert(check());
            assert(!check());
        };
        states[0][sf::ammo_remaining] = tuple({Value(20)});
        transition();
        states[0][sf::ammo_remaining] = tuple({Value(19)});
        transition();
        states[0][sf::shot_yaw] = Value();
        transition();
        states[0][sf::shot_yaw] = Value(.25);
        transition();
        states[0][sf::shot_pitch] = Value(.1);
        transition();
        Value equipment_config = Value::object();
        equipment_config["contract"] = Value::object();
        equipment[0].emplace_back(equipment_config);
        transition();
        Equipment &item = equipment[0][0];
        item.uses = 2;
        transition();
        item.active = true;
        transition();
        item.ready_at = now + 1;
        transition();
        now += .5;
        assert(!check());
        now = item.ready_at;
        transition();
        item.auto_since = now;
        item.ai_since = now;
        assert(!check()); // Inactive pending timestamps are not edges.
        item.auto_pending = true;
        transition();
        item.auto_since += .5;
        transition();
        item.ai_pending = true;
        transition();
        item.ai_since += .5;
        transition();
        item.contract = JsonReader("{\"kind\":\"repairkit\"}").read();
        transition();
        for (int step = 0; step < 100; ++step) {
            now = step / 10.0;
            states.resize(1 + step % 7, Value::object());
            equipment.resize(states.size());
            for (size_t i = 0; i < states.size(); ++i) {
                states[i] = states[i].copy();
                states[i][sf::id] = Value(static_cast<int>(i));
                states[i][sf::ammo_remaining] = tuple({Value(step % 9), Value(20)});
                if (step % 3) {
                    states[i][sf::shot_yaw] = Value(now);
                    states[i][sf::shot_pitch] = Value();
                } else {
                    states[i].erase(sf::shot_yaw);
                    states[i].erase(sf::shot_pitch);
                }
                equipment[i].clear();
                for (int j = 0; j < step % 4; ++j) {
                    Value config = Value::object();
                    config["contract"] = JsonReader("{\"kind\":\"repairkit\"}").read();
                    Equipment e(config);
                    e.uses = step % 5;
                    e.active = step % 2;
                    e.ready_at = (step + j % 2) / 10.0;
                    e.auto_pending = step % 7;
                    e.auto_since = now - .3;
                    e.ai_pending = step % 11;
                    e.ai_since = now - .5;
                    equipment[i].push_back(e);
                }
            }
            launches = Value::array();
            rams = Value::array();
            for (int j = 0; j < step % 3; ++j) {
                Value event = Value::object();
                event[sf::id] = Value(j);
                event[sf::fire_seq] = Value(step);
                launches.append(event);
                event = Value::object();
                event["bot_id"] = Value(j);
                event[sf::target_kind] = Value(step % 2 ? "bot" : "player");
                event[sf::target_id] = Value(j + 7);
                event["ram_seq"] = Value(step / 2);
                rams.append(event);
            }
            check();
            assert(!check());
            std::reverse(states.begin(), states.end());
            std::reverse(equipment.begin(), equipment.end());
            std::reverse(launches.data->array.begin(), launches.data->array.end());
            std::reverse(rams.data->array.begin(), rams.data->array.end());
            check();
            assert(!check());
        }
        states.clear();
        equipment.clear();
        check();
    }
    std::cout << "Publication edges: " << cases << " ordered oracle comparisons, " << changes
              << " transitions; missing/null, cooldown, rosters, shots and ram parity passed.\n";
}
