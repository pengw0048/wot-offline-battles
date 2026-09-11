#ifndef OFFLINE_EXPERIMENT_KERNEL_BOT_H
#define OFFLINE_EXPERIMENT_KERNEL_BOT_H
#include "kernel_weapon.h"
#include "kernel_critical.h"

namespace offline_kernel {
inline Value canonical_critical(const Value &payload) {
    if (payload.kind != Value::Object || !payload.truth())
        return Value::object();
    Value result = Value::object(), records = Value::array();
    const Value &devices = payload.get("devices");
    if (devices.kind != Value::Null)
        for (const Value &v : elements(devices)) {
            Value row = Value::object();
            double maximum = std::max(1.0, rounded(field(v, "max_hp"), 3));
            row[sf::name] = v.get(sf::name);
            row["hp"] = Value(clamp(rounded(field(v, "hp"), 3), 0, maximum));
            row["max_hp"] = Value(maximum);
            row["state"] = v.get("state");
            records.append(row);
        }
    std::sort(records.data->array.begin(), records.data->array.end(),
              [](const Value &a, const Value &b) {
                  return a.get(sf::name).text() < b.get(sf::name).text();
              });
    result["devices"] = records;
    result["destroyed"] = name_array(names(payload.get("destroyed")));
    result["crew_ko"] = name_array(names(payload.get("crew_ko")));
    result["fire"] = Value(flag(payload, "fire"));
    result["ammo_rack_death"] = Value(flag(payload, "ammo_rack_death"));
    result["events"] = Value::array();
    if (payload.get("crew_roster").truth())
        result["crew_roster"] = payload.get("crew_roster");
    return result;
}
inline Value combat_record(const Value &state) {
    Value v = Value::object();
    v[sf::health] = Value(std::max(0, integer(state, sf::health)));
    v[sf::alive] = Value(flag(state, sf::alive));
    v[sf::critical] = canonical_critical(state.get(sf::critical));
    v[sf::combat_fire_elapsed] = Value(rounded(field(state, sf::combat_fire_elapsed), 6));
    v[sf::combat_fire_timer] = Value(rounded(field(state, sf::combat_fire_timer), 6));
    v[sf::stun_end_server_time_ms] =
        Value(std::max(0, integer(state, sf::stun_end_server_time_ms)));
    return v;
}
struct Bot {
    Value state, sync, config;
    Gun gun;
    Ammo ammo;
    Burst burst;
    std::vector<Equipment> equipment;
    std::unique_ptr<CriticalConfig> critical;
    double turn_speed = 0;
    bool clear_reposition = false;
    explicit Bot(const Value &v)
        : state(v.get("state").as_record()), sync(v.get("sync")), config(v.get("config")) {
        gun.load(v.get("gun"));
        ammo.load(v.get("ammo"));
        burst.load(v.get("burst"));
        if (v.has("equipment"))
            for (const Value &item : elements(v.get("equipment")))
                equipment.emplace_back(item);
        if (v.has("critical_config"))
            critical.reset(new CriticalConfig(v.get("critical_config")));
        turn_speed = field(v, "turn_speed");
    }
    void publish_weapon() {
        state[sf::shell_index] = Value(ammo.loaded);
        state[sf::next_shell_index] = Value(ammo.next);
        state[sf::ammo_reload_pending] = Value(ammo.reload_pending);
        Value quantities = Value::array();
        for (int q : ammo.quantities)
            quantities.append(Value(q));
        state[sf::ammo_remaining] = quantities;
        state[sf::clip] = Value(gun.clip);
        state[sf::clip_size] = Value(gun.clip_size);
        state[sf::reload_time] = Value(gun.remaining(gun.reload_factor));
        state[sf::reload_duration] = Value(gun.duration(gun.reload_factor));
        state[sf::burst_active] = Value(burst.active);
        state[sf::burst_group_seq] = Value(burst.group);
        state[sf::burst_count] = Value(burst.count);
        state[sf::burst_next_index] = Value(burst.next);
        state[sf::burst_shell_index] = Value(burst.shell);
        state[sf::burst_interval] = Value(rounded(burst.interval, 6));
        state[sf::burst_time_left] = Value(rounded(std::max(0.0, burst.left), 6));
    }
    Value equipment_wire(double now) const {
        Value rows = Value::array();
        for (const Equipment &e : equipment)
            rows.append(e.wire(now));
        return rows;
    }
    Value snapshot() const {
        Value v = Value::object();
        v["state"] = state.clone();
        v["gun"] = gun.snapshot();
        v["ammo"] = ammo.snapshot();
        v["burst"] = burst.snapshot();
        return v;
    }
    bool stunned(double equipment_now) const {
        return flag(state, sf::alive) &&
               field(state, sf::_stun_until_equipment_time) > equipment_now + 1e-9;
    }
    void ensure_sync() {
        if (sync.kind == Value::Null) {
            Value signature = combat_signature(state);
            sync = Value::object();
            sync["server_signature"] = signature;
            sync["server_combat"] = combat_record(state);
            sync["published_signature"] = signature;
            sync["pending"] = Value::array();
            sync["next_seq"] = Value(std::max(0, integer(state, sf::combat_ack_seq)));
            sync["acked_seq"] = sync.get("next_seq");
            sync[sf::combat_revision] = Value(std::max(0, integer(state, sf::combat_revision)));
            sync["base_revision"] = Value(std::max(0, integer(state, sf::combat_base_revision)));
            sync["server_tick"] = Value(-1);
            sync["unpublished_steps"] = Value::array();
            sync["authority_handoff_pending"] = Value(false);
        }
        state[sf::combat_revision] = sync.get(sf::combat_revision);
        state[sf::combat_base_revision] = sync.get("base_revision");
        state[sf::combat_ack_seq] = sync.get("acked_seq");
        state[sf::combat_seq] = sync.get("next_seq");
    }
    bool mark_combat() {
        ensure_sync();
        Value signature = combat_signature(state);
        if (signature == sync.get("published_signature"))
            return false;
        int seq = integer(sync, "next_seq") + 1;
        sync["next_seq"] = Value(seq);
        Value pending = Value::object();
        pending["seq"] = Value(seq);
        pending["signature"] = signature;
        pending["combat"] = combat_record(state);
        pending["steps"] = sync.get("unpublished_steps").copy();
        sync["pending"].append(pending);
        sync["unpublished_steps"] = Value::array();
        sync["published_signature"] = signature;
        state[sf::combat_seq] = Value(seq);
        return true;
    }
    bool apply_effect(const Value &effect, double equipment_now, bool strict) {
        if (!critical)
            throw std::invalid_argument("kernel critical configuration absent");
        Critical shadow(state.get(sf::critical), *critical);
        bool clear_stun = flag(effect, "clearStun"), stun_cleared = false;
        int base = integer(effect, "stunBaseEndServerTimeMs");
        if (clear_stun && base <= 0) {
            if (strict)
                throw std::invalid_argument("kernel stun base");
            return false;
        }
        bool changed = shadow.effect(effect);
        if (!changed && !clear_stun) {
            if (strict)
                throw std::runtime_error("kernel equipment effect declined");
            return false;
        }
        if (changed)
            state[sf::critical] = shadow.payload();
        if (effect.get("action").text() == "extinguish_fire") {
            state[sf::combat_fire_elapsed] = Value(0.0);
            state[sf::combat_fire_timer] = Value(0.0);
        }
        if (clear_stun && integer(state, sf::stun_end_server_time_ms) == base) {
            state[sf::stun_end_server_time_ms] = Value(0);
            state[sf::_stun_until_equipment_time] = Value(equipment_now);
            stun_cleared = true;
        }
        return changed || stun_cleared;
    }
    Value poll_equipment(double equipment_now) {
        Value effects = Value::array();
        for (Equipment &e : equipment) {
            Value effect =
                e.poll_bot(equipment_now, state.get(sf::critical), stunned(equipment_now));
            if (effect.kind == Value::Null)
                continue;
            if (flag(effect, "clearStun"))
                effect["stunBaseEndServerTimeMs"] =
                    Value(integer(state, sf::stun_end_server_time_ms));
            apply_effect(effect, equipment_now, true);
            effects.append(effect);
        }
        state[sf::equipment_states] = equipment_wire(equipment_now);
        return effects;
    }
    void death(int reason, int display) {
        state[sf::health] = Value(0);
        state[sf::alive] = Value(false);
        state[sf::display_health] = Value(display);
        state[sf::death_reason] = Value(reason);
        state[sf::speed] = Value(0.0);
        state[sf::movement_dir] = Value(0);
        state[sf::rotation_dir] = Value(0);
        state[sf::target_kind] = Value();
        state[sf::target_id] = Value();
        clear_reposition = true;
    }
    bool advance_critical(double dt, double now, double equipment_now, bool record = true,
                          bool advance_fire = true, const Value &supplied_effects = Value()) {
        const Value payload = state.get(sf::critical);
        if (!payload.truth() && !stunned(equipment_now))
            return false;
        if (!critical)
            throw std::invalid_argument("kernel critical configuration absent");
        Value before = combat_signature(state);
        bool was_fire = flag(payload, "fire");
        ensure_sync();
        Value effects;
        if (supplied_effects.kind == Value::Null)
            effects = record ? poll_equipment(equipment_now) : Value::array();
        else {
            effects = supplied_effects;
            for (const Value &effect : elements(effects))
                apply_effect(effect, equipment_now, false);
        }
        double elapsed =
            rounded(clamp(field(state, sf::combat_fire_elapsed), 0, critical->fire_duration), 6);
        double timer = rounded(clamp(field(state, sf::combat_fire_timer), 0, .999999), 6);
        Critical shadow(state.get(sf::critical), *critical);
        bool repaired = shadow.repair(dt, integer(state, sf::health));
        Value repair_payload = repaired ? shadow.payload() : Value();
        Value prefire = shadow.identity();
        int damage = 0;
        if (advance_fire) {
            double started =
                was_fire ? now - std::min(critical->fire_duration, elapsed + dt) : now - dt;
            damage = shadow.burn(dt, now, started, timer, integer(state, sf::health),
                                 std::max(1, integer(state, sf::max_health,
                                                     std::max(1, integer(state, sf::health)))));
        }
        Value fire_payload = prefire != shadow.identity() ? shadow.payload() : Value();
        if (was_fire && advance_fire && shadow.fire) {
            state[sf::combat_fire_elapsed] =
                Value(rounded(std::min(critical->fire_duration, elapsed + dt), 6));
            state[sf::combat_fire_timer] = Value(rounded(clamp(timer, 0, .999999), 6));
        } else if (!shadow.fire) {
            state[sf::combat_fire_elapsed] = Value(0.0);
            state[sf::combat_fire_timer] = Value(0.0);
        }
        if (fire_payload.kind != Value::Null)
            state[sf::critical] = fire_payload;
        else if (repair_payload.kind != Value::Null)
            state[sf::critical] = repair_payload;
        if (damage > 0) {
            int health = std::max(0, integer(state, sf::health) - damage);
            state[sf::health] = Value(health);
            state[sf::alive] = Value(health > 0);
            state[sf::display_health] = Value(health);
            if (health <= 0) {
                Critical terminal(state.get(sf::critical), *critical);
                state[sf::critical] = terminal.terminal();
                state[sf::combat_fire_elapsed] = Value(0.0);
                state[sf::combat_fire_timer] = Value(0.0);
                death(1, 0);
            }
        }
        bool changed = combat_signature(state) != before;
        if (record &&
            (changed || was_fire || flag(state.get(sf::critical), "fire") || effects.truth())) {
            Value step = Value::array();
            step.append(Value(dt));
            step.append(Value(now));
            step.append(Value(was_fire));
            step.append(effects);
            sync["unpublished_steps"].append(step);
        }
        return changed;
    }
    bool drowning_due(double step) const {
        return flag(state, sf::alive) && field(state, sf::health) > 0 && step > 0 &&
               field(state, sf::_drown_check) + step >= field(config, "BOT_DROWNING_PROBE_SECONDS");
    }
    bool advance_drowning(double step, double depth) {
        if (!flag(state, sf::alive) || field(state, sf::health) <= 0 || step <= 0)
            return false;
        double check = field(state, sf::_drown_check) + step;
        state[sf::_drown_check] = Value(check);
        if (check < field(config, "BOT_DROWNING_PROBE_SECONDS"))
            return false;
        state[sf::_drown_check] = Value(0.0);
        state[sf::_water_depth] = Value(depth);
        int level = 0;
        if (depth >= 0) {
            const Value &offset = config.get("water_offset");
            double pitch = field(state, sf::pitch), roll = field(state, sf::roll);
            double rolled =
                std::sin(roll) * offset[0].number() + std::cos(roll) * offset[1].number();
            double height = std::cos(pitch) * rolled - std::sin(pitch) * offset[2].number();
            level = depth > height ? 2 : 1;
        }
        if (level != 2) {
            state[sf::_drown_time] = Value(0.0);
            state[sf::_drowning] = Value(false);
            return false;
        }
        state[sf::_drowning] = Value(true);
        double elapsed = field(state, sf::_drown_time) + check;
        state[sf::_drown_time] = Value(elapsed);
        if (elapsed <= field(config, "BOT_DROWNING_SECONDS"))
            return false;
        int display = std::max(0, integer(state, sf::health));
        Critical terminal(state.get(sf::critical), *critical);
        state[sf::critical] = terminal.terminal();
        death(integer(config, "BOT_DROWNING_DEATH_REASON"), display);
        state[sf::_drowned] = Value(true);
        state[sf::_drown_time] = config.get("BOT_DROWNING_SECONDS");
        state[sf::_drowning] = Value(false);
        return true;
    }
    bool advance_overturn(double step) {
        if (!flag(state, sf::alive) || field(state, sf::health) <= 0 || step <= 0)
            return false;
        double up =
            clamp(std::cos(field(state, sf::pitch)) * std::cos(field(state, sf::roll)), -1, 1);
        int level = up <= field(config, "BOT_OVERTURN_DANGER_COSINE")    ? 2
                    : up <= field(config, "BOT_OVERTURN_WARNING_COSINE") ? 1
                                                                         : 0;
        if (!level) {
            state[sf::_overturn_check] = Value(0.0);
            state[sf::_overturn_time] = Value(0.0);
            state[sf::_overturn_level] = Value(0);
            state[sf::_overturned] = Value(false);
            return false;
        }
        double check = field(state, sf::_overturn_check) + step;
        state[sf::_overturn_check] = Value(check);
        if (check + .000001 < field(config, "BOT_OVERTURN_IGNORE_SECONDS"))
            return false;
        if (level != integer(state, sf::_overturn_level)) {
            state[sf::_overturn_level] = Value(level);
            state[sf::_overturn_time] = Value(0.0);
        }
        state[sf::_overturned] = Value(level == 2);
        if (level != 2) {
            state[sf::_overturn_time] = Value(0.0);
            return false;
        }
        state[sf::movement_dir] = Value(0);
        state[sf::rotation_dir] = Value(0);
        turn_speed = 0;
        double elapsed = field(state, sf::_overturn_time) + step;
        state[sf::_overturn_time] = Value(elapsed);
        if (elapsed + .000001 < field(config, "BOT_OVERTURN_DEATH_SECONDS"))
            return false;
        Critical terminal(state.get(sf::critical), *critical);
        state[sf::critical] = terminal.terminal();
        death(integer(config, "BOT_OVERTURN_DEATH_REASON"), 0);
        state[sf::_overturn_time] = config.get("BOT_OVERTURN_DEATH_SECONDS");
        return true;
    }
};
} // namespace offline_kernel
#endif
