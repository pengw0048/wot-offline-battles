#ifndef OFFLINE_EXPERIMENT_KERNEL_CODEC_H
#define OFFLINE_EXPERIMENT_KERNEL_CODEC_H
#include "kernel_value.h"
#include <array>

namespace offline_kernel {
struct Codec {
    struct Column {
        std::string name;
        int scale;
        bool bounded;
        double minimum, maximum;
    };
    struct Group {
        int flag;
        std::vector<std::string> names;
    };
    std::vector<Column> columns;
    std::vector<Group> groups;
    std::vector<std::string> devices, crew, device_states;
    explicit Codec(const Value &config) {
        for (const Value &raw : elements(config.get("scalars"))) {
            Column c;
            c.name = raw[0].text();
            c.scale = static_cast<int>(raw[1].exact());
            const Value &bounds = config.get("clamps").get(c.name);
            c.bounded = bounds.kind == Value::Array;
            c.minimum = c.bounded ? bounds[0].number() : 0;
            c.maximum = c.bounded ? bounds[1].number() : 0;
            columns.push_back(c);
        }
        for (const Value &raw : elements(config.get("groups"))) {
            Group g;
            g.flag = static_cast<int>(raw[0].exact());
            for (const Value &name : elements(raw[1]))
                g.names.push_back(name.text());
            groups.push_back(g);
        }
        for (const Value &raw : elements(config.get("devices")))
            devices.push_back(raw.text());
        for (const Value &raw : elements(config.get("crew")))
            crew.push_back(raw.text());
        for (const Value &raw : elements(config.get("device_states")))
            device_states.push_back(raw.text());
        if (columns.size() != 37 || groups.size() != 4 || devices.size() != 9 || crew.size() != 8 ||
            device_states.size() != 3)
            throw std::invalid_argument("kernel codec producer");
    }
    static int64_t fixed(double value, int scale, bool bounded = false, double minimum = 0,
                         double maximum = 0) {
        if (!std::isfinite(value))
            throw std::invalid_argument("bot state column is not finite");
        if (bounded)
            value = clamp(value, minimum, maximum);
        double scaled = value * scale;
        if (std::abs(scaled) > 9007199254740991.0)
            throw std::invalid_argument("kernel wire numeric range");
        return scaled >= 0 ? static_cast<int64_t>(std::floor(scaled + 0.5))
                           : -static_cast<int64_t>(std::floor(-scaled + 0.5));
    }
    static int index(const std::vector<std::string> &names, const std::string &name,
                     const char *error) {
        auto at = std::find(names.begin(), names.end(), name);
        if (at == names.end())
            throw std::invalid_argument(error);
        return static_cast<int>(at - names.begin());
    }
    int crew_mask(const Value &names) const {
        int mask = 0;
        if (names.truth()) {
            if (names.kind != Value::Array)
                throw std::invalid_argument("kernel crew list");
            for (const Value &name : names.data->array)
                mask |= 1 << index(crew, name.text(), "unknown crew member");
        }
        return mask;
    }
    static int64_t exact(const Value &v) {
        if (v.kind == Value::Integer)
            return v.integer;
        if (v.kind == Value::Real && std::isfinite(v.real) &&
            std::abs(v.real) <= 9007199254740991.0)
            return static_cast<int64_t>(v.real);
        if (v.kind == Value::String) {
            const std::string &s = v.data->string;
            char *end = nullptr;
            long long n = std::strtoll(s.c_str(), &end, 10);
            if (end != s.c_str()) {
                while (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r')
                    ++end;
                if (!*end)
                    return n;
            }
        }
        throw std::invalid_argument("invalid integer column");
    }
    static double numeric(const Value &v) {
        double n = v.number(std::numeric_limits<double>::quiet_NaN());
        if (!std::isfinite(n))
            throw std::invalid_argument("invalid real column");
        return n;
    }
    Value row(const Value &state) const {
        if (state.kind != Value::Object)
            throw std::invalid_argument("bot state must be a mapping");
        const Value &critical = state.get("critical"), &equipment = state.get("equipment_states"),
                    &ammo = state.get("ammo_remaining");
        bool shot = state.has("shot_yaw");
        if (shot != state.has("shot_pitch"))
            throw std::invalid_argument("bot shot angles must be an atomic pair");
        int flags = 0;
        if (flag(state, "alive", true))
            flags |= 1;
        if (flag(state, "world_pose", true))
            flags |= 2;
        if (flag(state, "ammo_reload_pending"))
            flags |= 4;
        if (flag(state, "burst_active"))
            flags |= 8;
        if (critical.kind == Value::Object) {
            flags |= 128;
            if (flag(critical, "fire"))
                flags |= 16;
            if (flag(critical, "ammo_rack_death"))
                flags |= 32;
        }
        if (equipment.kind != Value::Null)
            flags |= 256;
        if (shot)
            flags |= 64;
        for (const Group &g : groups) {
            size_t count = 0;
            for (const std::string &name : g.names)
                if (state.has(name))
                    ++count;
            if (count == g.names.size())
                flags |= g.flag;
            else if (count)
                throw std::invalid_argument("bot state group is incomplete");
        }
        double movement = field(state, "movement_dir"), rotation = field(state, "rotation_dir");
        if (movement > 0.01)
            flags |= 1024;
        else if (movement < -.01)
            flags |= 2048;
        if (rotation > 0.01)
            flags |= 4096;
        else if (rotation < -.01)
            flags |= 8192;
        Value out = Value::array();
        for (const Column &c : columns) {
            if (c.name == "_flags") {
                out.append(Value(flags));
                continue;
            }
            if (c.name == "shot_yaw" || c.name == "shot_pitch") {
                if (!shot) {
                    out.append(Value(0));
                    continue;
                }
                double value = numeric(state.get(c.name));
                if (c.name == "shot_yaw") {
                    const double pi = 3.14159265358979323846;
                    value = std::fmod(value + pi, 2 * pi);
                    if (value < 0)
                        value += 2 * pi;
                    value -= pi;
                }
                out.append(Value(fixed(value, c.scale, c.bounded, c.minimum, c.maximum)));
                continue;
            }
            const Value v = state.has(c.name) ? state.get(c.name) : Value(0);
            out.append(Value(c.scale ? fixed(numeric(v), c.scale, c.bounded, c.minimum, c.maximum)
                                     : exact(v)));
        }
        if (flags & 512) {
            if (ammo.kind != Value::Array || ammo.size() > 5)
                throw std::invalid_argument("bot carries too many shell types");
            out.append(Value(static_cast<int>(ammo.size())));
            for (const Value &v : ammo.data->array)
                out.append(Value(exact(v)));
        }
        if (critical.kind == Value::Object) {
            std::vector<std::array<int64_t, 4>> records;
            const Value &values = critical.get("devices");
            if (values.truth()) {
                if (values.kind != Value::Array)
                    throw std::invalid_argument("kernel device list");
                for (const Value &v : values.data->array)
                    records.push_back(std::array<int64_t, 4>{
                        {index(devices, v.get("name").text(), "unknown critical device"),
                         fixed(field(v, "hp"), 1000), fixed(field(v, "max_hp", 1), 1000),
                         index(device_states, v.get("state").text("normal"),
                               "unknown critical device state")}});
            }
            std::sort(records.begin(), records.end());
            out.append(Value(static_cast<int>(records.size())));
            for (const auto &v : records)
                for (int64_t n : v)
                    out.append(Value(n));
            out.append(Value(crew_mask(critical.get("crew_ko"))));
            const Value &roster = critical.get("crew_roster");
            out.append(Value(roster.kind == Value::Null ? -1 : crew_mask(roster)));
        }
        if (equipment.kind != Value::Null) {
            if (equipment.kind != Value::Array || equipment.size() > 3)
                throw std::invalid_argument("bot carries too many consumables");
            out.append(Value(static_cast<int>(equipment.size())));
            for (const Value &v : equipment.data->array) {
                if (v.kind != Value::Object)
                    throw std::invalid_argument("kernel equipment record");
                out.append(Value(exact(v.has("usesLeft") ? v.get("usesLeft") : Value(0))));
                out.append(Value(fixed(field(v, "cooldownTimeLeft"), 1000000)));
                out.append(Value(flag(v, "active") ? 1 : 0));
                for (const char *name : {"autoPendingElapsed", "aiPendingElapsed"})
                    out.append(Value(v.get(name).kind == Value::Null
                                         ? int64_t(-1)
                                         : fixed(field(v, name), 1000000)));
            }
        }
        return out;
    }
};
} // namespace offline_kernel
#endif
