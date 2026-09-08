#ifndef OFFLINE_EXPERIMENT_KERNEL_CRITICAL_H
#define OFFLINE_EXPERIMENT_KERNEL_CRITICAL_H
#include "kernel_equipment.h"

namespace offline_kernel {
struct CriticalConfig {
    struct Device {
        double maximum, cap, seconds;
        bool has_maximum, has_cap, no_fire_repair;
    };
    std::map<std::string, Device> devices;
    Value roster;
    double fire_duration = 0, fire_fraction = 0, critical_fraction = 0;
    explicit CriticalConfig(const Value &v) : roster(v.get("roster")) {
        for (const Value &raw : elements(v.get("devices"))) {
            Device d;
            d.maximum = field(raw, "maximum");
            d.cap = field(raw, "cap");
            d.seconds = field(raw, "seconds");
            d.has_maximum = raw.get("maximum").kind != Value::Null;
            d.has_cap = raw.get("cap").kind != Value::Null;
            d.no_fire_repair = flag(raw, "no_fire_repair");
            devices[raw.get("name").text()] = d;
        }
        fire_duration = field(v, "fire_duration");
        fire_fraction = field(v, "fire_fraction");
        critical_fraction = field(v, "critical_fraction");
    }
    const Device *device(const std::string &name) const {
        auto at = devices.find(name);
        return at == devices.end() ? nullptr : &at->second;
    }
    int condition(double hp, const std::string &name) const {
        if (hp <= 0)
            return 2;
        const Device *d = device(name);
        return d && d->has_maximum && d->maximum && hp <= d->maximum * critical_fraction ? 1 : 0;
    }
};
struct Critical {
    const CriticalConfig &config;
    std::map<std::string, double> hp;
    std::set<std::string> destroyed, yellow, crew;
    bool fire = false, rack = false;
    explicit Critical(const Value &payload, const CriticalConfig &c) : config(c) {
        const Value &records = payload.get("devices");
        if (records.kind != Value::Null)
            for (const Value &v : elements(records)) {
                std::string name = v.get("name").text();
                if (name.empty())
                    throw std::invalid_argument("kernel critical device");
                hp[name] = std::max(0.0, field(v, "hp"));
            }
        destroyed = names(payload.get("destroyed"));
        crew = names(payload.get("crew_ko"));
        fire = flag(payload, "fire");
        rack = flag(payload, "ammo_rack_death");
        for (const auto &v : hp)
            if (!destroyed.count(v.first) && config.condition(v.second, v.first) == 1)
                yellow.insert(v.first);
    }
    Value identity() const {
        Value v = Value::object(), values = Value::object();
        for (const auto &item : hp)
            values[item.first] = Value(item.second);
        v["hp"] = values;
        v["destroyed"] = name_array(destroyed);
        v["yellow"] = name_array(yellow);
        v["crew"] = name_array(crew);
        v["fire"] = Value(fire);
        v["rack"] = Value(rack);
        return v;
    }
    Value payload() const {
        std::set<std::string> all = destroyed;
        all.insert(yellow.begin(), yellow.end());
        for (const auto &v : hp)
            all.insert(v.first);
        Value result = Value::object(), records = Value::array();
        for (const std::string &name : all) {
            auto at = hp.find(name);
            double health = at == hp.end() ? 0 : at->second;
            const CriticalConfig::Device *d = config.device(name);
            double maximum = d && d->has_maximum ? d->maximum : std::max(1.0, rounded(health, 0));
            maximum = std::max(1.0, rounded(maximum, 3));
            int condition = destroyed.count(name) ? 2
                            : yellow.count(name)  ? 1
                                                  : config.condition(health, name);
            Value v = Value::object();
            v["name"] = Value(name);
            v["hp"] = Value(clamp(rounded(health, 3), 0, maximum));
            v["max_hp"] = Value(maximum);
            v["state"] = Value(condition == 2   ? "destroyed"
                               : condition == 1 ? "critical"
                                                : "normal");
            records.append(v);
        }
        result["devices"] = records;
        result["destroyed"] = name_array(destroyed);
        result["crew_ko"] = name_array(crew);
        result["fire"] = Value(fire);
        result["ammo_rack_death"] = Value(rack);
        result["events"] = Value::array();
        return result;
    }
    bool repair(double dt, int health) {
        if (dt <= 0 || health <= 0)
            return false;
        bool changed = false;
        for (auto &v : hp) {
            if (!destroyed.count(v.first))
                continue;
            const CriticalConfig::Device *d = config.device(v.first);
            if (!d || !d->has_cap || v.second >= d->cap || (fire && d->no_fire_repair))
                continue;
            double next = std::min(d->cap, v.second + (d->cap / std::max(.1, d->seconds)) * dt);
            changed = changed || next != v.second;
            v.second = next;
            if (v.second >= d->cap) {
                destroyed.erase(v.first);
                yellow.insert(v.first);
            }
        }
        return changed;
    }
    void extinguish() {
        fire = false;
        const std::string name = "fuelTankHealth";
        const CriticalConfig::Device *d = config.device(name);
        auto at = hp.find(name);
        if (d && d->has_cap && at != hp.end() && at->second < d->cap) {
            at->second = d->cap;
            destroyed.erase(name);
            yellow.insert(name);
        }
    }
    int burn(double dt, double now, double started, double &timer, int health, int maximum) {
        if (dt <= 0 || !fire || health <= 0)
            return 0;
        double end = started + config.fire_duration;
        double active_start = std::max(now - dt, started), active_end = std::min(now, end);
        timer += std::max(0.0, active_end - active_start);
        int ticks = static_cast<int>(std::floor(timer + 1e-9)), damage = 0;
        if (ticks > 0) {
            timer = std::max(0.0, timer - ticks);
            damage = std::max(1, static_cast<int>(maximum * config.fire_fraction)) * ticks;
        }
        if (now >= end)
            extinguish();
        return damage;
    }
    Value terminal() {
        if (fire)
            extinguish();
        for (const auto &v : config.devices) {
            hp[v.first] = 0;
            destroyed.insert(v.first);
        }
        yellow.clear();
        const auto roster_names = names(config.roster);
        crew.insert(roster_names.begin(), roster_names.end());
        Value result = payload();
        if (config.roster.truth()) {
            result["crew_roster"] = config.roster;
            result["crew_ko"] = config.roster;
        }
        return result;
    }
    bool effect(const Value &effect) {
        Value before = identity();
        std::string action = effect.get("action").text();
        if (action == "extinguish_fire") {
            if (!fire)
                return false;
            extinguish();
        } else if (action == "repair_devices") {
            std::set<std::string> all = destroyed;
            for (const auto &v : hp)
                all.insert(v.first);
            if (!flag(effect, "repairAll")) {
                std::string selected = effect.get("selected").text();
                if (!selected.empty() &&
                    (selected.size() < 6 || selected.substr(selected.size() - 6) != "Health"))
                    selected += "Health";
                if (!all.count(selected))
                    return false;
                all.clear();
                all.insert(selected);
            }
            for (const std::string &name : all) {
                const CriticalConfig::Device *d = config.device(name);
                if (!d || !d->has_maximum)
                    continue;
                auto at = hp.find(name);
                double value = at == hp.end() ? d->maximum : at->second;
                if (destroyed.count(name) || value < d->maximum) {
                    hp[name] = d->maximum;
                    destroyed.erase(name);
                    yellow.erase(name);
                }
            }
        } else if (action == "restore_crew") {
            if (flag(effect, "repairAll"))
                crew.clear();
            else
                crew.erase(effect.get("selected").text());
        } else
            return false;
        return before != identity();
    }
};
inline Value critical_signature(const Value &p) {
    Value result = Value::array();
    if (p.kind != Value::Object || !p.truth())
        return result;
    Value records = Value::array();
    const Value &devices = p.get("devices");
    if (devices.kind != Value::Null)
        for (const Value &v : elements(devices)) {
            Value row = Value::array();
            row.append(v.get("name"));
            row.append(Value(rounded(field(v, "hp"), 3)));
            row.append(Value(rounded(field(v, "max_hp", 1), 3)));
            row.append(Value(v.get("state").text()));
            records.append(row);
        }
    std::sort(records.data->array.begin(), records.data->array.end(),
              [](const Value &a, const Value &b) { return a[0].text() < b[0].text(); });
    Value destroyed = name_array(names(p.get("destroyed"))),
          crew = name_array(names(p.get("crew_ko"))),
          roster = name_array(names(p.get("crew_roster")));
    bool fire = flag(p, "fire"), rack = flag(p, "ammo_rack_death");
    if (!records.truth() && !destroyed.truth() && !crew.truth() && !roster.truth() && !fire &&
        !rack)
        return result;
    result.append(records);
    result.append(destroyed);
    result.append(crew);
    result.append(roster);
    result.append(Value(fire));
    result.append(Value(rack));
    return result;
}
inline Value combat_signature(const Value &state) {
    Value v = Value::array();
    v.append(Value(std::max(0, integer(state, "health"))));
    v.append(Value(flag(state, "alive")));
    v.append(critical_signature(state.get("critical")));
    v.append(Value(rounded(field(state, "combat_fire_elapsed"), 6)));
    v.append(Value(rounded(field(state, "combat_fire_timer"), 6)));
    v.append(Value(std::max(0, integer(state, "stun_end_server_time_ms"))));
    return v;
}
} // namespace offline_kernel
#endif
