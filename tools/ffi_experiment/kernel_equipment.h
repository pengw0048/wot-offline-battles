#ifndef OFFLINE_EXPERIMENT_KERNEL_EQUIPMENT_H
#define OFFLINE_EXPERIMENT_KERNEL_EQUIPMENT_H
#include "kernel_value.h"
#include <set>

namespace offline_kernel {
inline std::set<std::string> names(const Value &v) {
    std::set<std::string> result;
    if (v.kind == Value::Null)
        return result;
    for (const Value &item : elements(v))
        result.insert(item.text());
    return result;
}
inline Value name_array(const std::set<std::string> &v) {
    Value result = Value::array();
    for (const std::string &name : v)
        result.append(Value(name));
    return result;
}
struct Equipment {
    Value contract;
    int uses = 0;
    double ready_at = 0, auto_since = 0, ai_since = 0;
    bool active = false, auto_pending = false, ai_pending = false;
    explicit Equipment(const Value &v) : contract(v.get("contract")) {
        if (contract.kind != Value::Object)
            throw std::invalid_argument("kernel equipment contract");
        uses = integer(v, "uses_left");
        ready_at = field(v, "ready_at");
        active = flag(v, "active");
        auto_pending = v.get("_auto_pending_since").kind != Value::Null;
        ai_pending = v.get("_ai_pending_since").kind != Value::Null;
        auto_since = field(v, "_auto_pending_since");
        ai_since = field(v, "_ai_pending_since");
    }
    bool ready(double now) const { return uses != 0 && now >= ready_at; }
    Value effect(const Value &critical, const Value &selected = Value(), bool requested = false,
                 bool stunned = false) const {
        std::string kind = contract.get(sf::kind).text(), selection = selected.text("None");
        Value result = Value::object();
        if (kind == "extinguisher") {
            if (!flag(critical, "fire"))
                return Value();
            result["action"] = Value("extinguish_fire");
            return result;
        }
        if (kind == "repairkit") {
            std::set<std::string> damaged = names(critical.get("destroyed"));
            const Value &devices = critical.get("devices");
            if (devices.kind != Value::Null)
                for (const Value &v : elements(devices)) {
                    std::string name = v.get(sf::name).text(), state = v.get("state").text();
                    if (!name.empty() && (state == "critical" || state == "destroyed"))
                        damaged.insert(name);
                }
            bool all = flag(contract, "repairAll");
            if (damaged.empty() || (!all && !damaged.count(selection)))
                return Value();
            result["action"] = Value("repair_devices");
            result["repairAll"] = Value(all);
            result["selected"] = all ? Value() : Value(selection);
            result["bonusValue"] = Value(field(contract, "bonusValue"));
            return result;
        }
        if (kind == "medkit") {
            std::set<std::string> knocked = names(critical.get("crew_ko"));
            bool all = flag(contract, "repairAll");
            if ((knocked.empty() && !stunned) || (!all && !knocked.count(selection) && !stunned))
                return Value();
            result["action"] = Value("restore_crew");
            result["repairAll"] = Value(all);
            result["selected"] = all ? Value() : Value(selection);
            result["bonusValue"] = Value(field(contract, "bonusValue"));
            result["clearStun"] = Value(stunned);
            return result;
        }
        if (kind == "rpm_limiter") {
            if (requested == active)
                return Value();
            result["action"] = Value("set_rpm_limiter");
            result["active"] = Value(requested);
            result["enginePowerFactor"] = Value(field(contract, "enginePowerFactor", 1));
            result["engineHpLossPerSecond"] = Value(field(contract, "engineHpLossPerSecond"));
            return result;
        }
        return Value();
    }
    Value activate(double now, const Value &critical, const Value &selected = Value(),
                   bool requested = false, bool stunned = false) {
        if (!ready(now))
            return Value();
        Value result = effect(critical, selected, requested, stunned);
        if (result.kind == Value::Null)
            return result;
        if (result.get("action").text() == "set_rpm_limiter")
            active = flag(result, "active");
        else {
            if (uses > 0)
                --uses;
            ready_at = now + std::max(0.0, field(contract, "cooldownSeconds"));
        }
        auto_pending = ai_pending = false;
        return result;
    }
    Value poll_auto(double now, const Value &critical) {
        if (!flag(contract, "autoactivate"))
            return Value();
        if (effect(critical).kind == Value::Null || !ready(now)) {
            auto_pending = false;
            return Value();
        }
        double delay = std::max(0.0, field(contract, "autoReactionSeconds"));
        if (!auto_pending) {
            auto_pending = true;
            auto_since = now;
            if (delay > 0)
                return Value();
        }
        if (now - auto_since + 1e-9 < delay)
            return Value();
        return activate(now, critical);
    }
    Value poll_bot(double now, const Value &critical, bool stunned) {
        std::string kind = contract.get(sf::kind).text();
        if (kind == "extinguisher")
            return poll_auto(now, critical);
        if (kind != "repairkit" && kind != "medkit") {
            ai_pending = false;
            return Value();
        }
        if (effect(critical, Value(), false, stunned).kind == Value::Null || !ready(now)) {
            ai_pending = false;
            return Value();
        }
        if (!ai_pending) {
            ai_pending = true;
            ai_since = now;
            return Value();
        }
        if (now <= ai_since + 1e-9)
            return Value();
        return activate(now, critical, Value(), false, stunned);
    }
    Value snapshot() const {
        Value v = Value::object();
        v["contract"] = contract;
        v["uses_left"] = Value(uses);
        v["ready_at"] = Value(ready_at);
        v["active"] = Value(active);
        v["_auto_pending_since"] = auto_pending ? Value(auto_since) : Value();
        v["_ai_pending_since"] = ai_pending ? Value(ai_since) : Value();
        return v;
    }
    Value wire(double now) const {
        Value v = Value::object();
        v["equipment"] = contract;
        v["usesLeft"] = Value(uses);
        v["cooldownTimeLeft"] = Value(std::max(0.0, ready_at - now));
        v["active"] = Value(active);
        v["autoPendingElapsed"] = auto_pending ? Value(std::max(0.0, now - auto_since)) : Value();
        v["aiPendingElapsed"] = ai_pending ? Value(std::max(0.0, now - ai_since)) : Value();
        return v;
    }
};
inline Value equipment_passives(const std::vector<Equipment> &equipment) {
    double fire = 1, repair = 0, medkit = 0, crew = 0, power = 1, turret = 1, hp = 0;
    for (const Equipment &e : equipment) {
        const Value &c = e.contract;
        std::string kind = c.get(sf::kind).text();
        if (kind == "extinguisher")
            fire *= std::max(0.0, field(c, "fireStartingChanceFactor", 1));
        else if (kind == "repairkit")
            repair += field(c, "bonusValue");
        else if (kind == "medkit")
            medkit += field(c, "bonusValue");
        crew += field(c, "crewLevelIncrease");
        if (kind == "fuel" || (kind == "rpm_limiter" && e.active))
            power *= std::max(0.0, field(c, "enginePowerFactor", 1));
        if (kind == "fuel")
            turret *= std::max(0.0, field(c, "turretRotationSpeedFactor", 1));
        if (kind == "rpm_limiter" && e.active)
            hp += std::max(0.0, field(c, "engineHpLossPerSecond"));
    }
    Value v = Value::object();
    v["fireStartingChanceFactor"] = Value(fire);
    v["repairkitBonusValue"] = Value(repair);
    v["medkitBonusValue"] = Value(medkit);
    v["crewLevelIncrease"] = Value(crew);
    v["enginePowerFactor"] = Value(power);
    v["turretRotationSpeedFactor"] = Value(turret);
    v["engineHpLossPerSecond"] = Value(hp);
    return v;
}
} // namespace offline_kernel
#endif
