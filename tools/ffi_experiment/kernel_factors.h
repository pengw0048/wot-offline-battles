#ifndef OFFLINE_EXPERIMENT_KERNEL_FACTORS_H
#define OFFLINE_EXPERIMENT_KERNEL_FACTORS_H
#include "kernel_critical.h"

namespace offline_kernel {
struct Factors {
    Value crew_names, crew_rows, module_specs;
    double minimum_vision;
    explicit Factors(const Value &v)
        : crew_names(v.get("crew_names")), crew_rows(v.get("crew_rows")),
          module_specs(v.get("module_specs")), minimum_vision(field(v, "minimum_vision")) {
        if (crew_names.size() != 8)
            throw std::invalid_argument("kernel factor roster");
    }
    double stat(const Value &state, const CriticalConfig &config, const std::string &name,
                bool include_crew = true) const {
        const Value &critical = state.get(sf::critical);
        if (!critical.truth())
            return 1;
        std::set<std::string> knocked = names(critical.get("crew_ko")),
                              destroyed = names(critical.get("destroyed"));
        unsigned mask = 0;
        for (size_t i = 0; i < crew_names.size(); ++i)
            if (knocked.count(crew_names[i].text()))
                mask |= 1 << i;
        double crew = 1;
        if (include_crew && crew_rows.has(name))
            crew = crew_rows.get(name)[mask].number();
        const Value &spec = module_specs.get(name);
        if (spec.kind == Value::Null)
            return crew;
        std::map<std::string, const Value *> records;
        const Value &devices = critical.get("devices");
        if (devices.kind != Value::Null)
            for (const Value &v : elements(devices))
                records[v.get(sf::name).text()] = &v;
        double factor = 1;
        for (const Value &v : elements(spec[0])) {
            std::string device = v.text();
            if (destroyed.count(device)) {
                if (spec[2].kind != Value::Null)
                    factor *= spec[2].number();
                continue;
            }
            auto at = records.find(device);
            if (at == records.end())
                continue;
            const Value &record = *at->second;
            if (record.get("state").text() == "critical" ||
                config.condition(field(record, "hp"), device) == 1)
                factor *= spec[1].number();
        }
        return crew * factor;
    }
    double vision(double value) const { return clamp(value, minimum_vision, 1); }
};
inline Value point(double x, double y, double z) {
    Value v = Value::array(3);
    v.append(Value(x));
    v.append(Value(y));
    v.append(Value(z));
    return v;
}
inline Value position(const Value &v) {
    return point(field(v, sf::x), field(v, sf::y), field(v, sf::z));
}
inline Value target_position(const Value &v) {
    const Value &p = v.get(sf::position);
    return p.kind == Value::Array && p.size() == 3 ? p : position(v);
}
inline Value select_fields(const Value &source, const std::vector<Field> &keys) {
    Value v = Value::record();
    for (Field name : keys) {
        if (source.has(name))
            v[name] = source.get(name);
    }
    return v;
}
inline void update(Value &target, const Value &source) {
    if (source.kind != Value::Object)
        throw std::invalid_argument("kernel mapping update");
    target.assign_fields(source);
}
inline double angle(double v) {
    const double pi = 3.14159265358979323846;
    v = std::fmod(v + pi, 2 * pi);
    if (v < 0)
        v += 2 * pi;
    return v - pi;
}
} // namespace offline_kernel
#endif
