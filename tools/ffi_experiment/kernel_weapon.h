#ifndef OFFLINE_EXPERIMENT_KERNEL_WEAPON_H
#define OFFLINE_EXPERIMENT_KERNEL_WEAPON_H
#include "kernel_value.h"

namespace offline_kernel {
// The descriptor producer initializes these once. Every reload, selection,
// burst and bloom transition thereafter belongs to the native Bot owner.
struct Gun {
    int clip = 0, clip_size = 1, shell_count = 1, burst_count = 1, burst_remaining = 0;
    double elapsed = 0, reload_factor = 1, reload_duration = 0, reload_full = 0, reload_intra = 0;
    bool intra = false;
    double movement_factor = 0, rotation_factor = 0, turret_factor = 0;
    double dispersion = 0, fully_aimed = 0, aiming_time = 0, current_factor = 1;
    double aiming_start = 1, aiming_elapsed = 0, motion_squared = 0, after_shot = 0,
           after_in_burst = 0, burst_interval = 0;
    void load(const Value &v) {
        clip = integer(v, sf::clip);
        clip_size = integer(v, sf::clip_size, 1);
        shell_count = integer(v, "shell_count", 1);
        burst_count = integer(v, sf::burst_count, 1);
        burst_remaining = integer(v, "_burst_remaining");
        elapsed = field(v, "elapsed");
        reload_factor = field(v, "reload_factor", 1);
        reload_duration = field(v, sf::reload_duration);
        reload_full = field(v, "reload_full");
        reload_intra = field(v, "reload_intra");
        intra = v.get("reload_kind").text() == "intra";
        movement_factor = field(v, "movement_dispersion_factor");
        rotation_factor = field(v, "rotation_dispersion_factor");
        turret_factor = field(v, "turret_dispersion_factor");
        dispersion = field(v, "dispersion");
        fully_aimed = field(v, "fully_aimed_dispersion");
        aiming_time = field(v, "aiming_time");
        current_factor = field(v, "current_dispersion_factor", 1);
        aiming_start = field(v, "aiming_start_factor", 1);
        aiming_elapsed = field(v, "aiming_elapsed");
        motion_squared = field(v, "motion_dispersion_squared");
        after_shot = field(v, "after_shot");
        after_in_burst = field(v, "after_shot_in_burst");
        burst_interval = field(v, sf::burst_interval);
        if (clip_size < 1 || shell_count < 1 || shell_count > 5 || clip < 0 || clip > clip_size ||
            reload_full <= 0 || reload_intra < 0 || fully_aimed <= 0)
            throw std::invalid_argument("kernel gun producer");
    }
    double duration(double factor = 1) const {
        return reload_duration * (intra ? 1 : std::max(0.0, factor));
    }
    bool ready(double factor = 1) const { return elapsed > duration(factor); }
    double remaining(double factor = 1) const { return std::max(0.0, duration(factor) - elapsed); }
    void tick(double dt) { elapsed += std::max(0.0, dt); }
    void bloom_tick(double dt, double move, double rotation, double turret, double factor = 1,
                    double aim_factor = 1) {
        dt = std::max(0.0, dt);
        double a = std::abs(move) * movement_factor, b = std::abs(rotation) * rotation_factor,
               c = std::abs(turret) * turret_factor;
        motion_squared = a * a + b * b + c * c;
        double ideal = std::max(0.0, factor) * std::sqrt(1.0 + motion_squared);
        if (!std::isfinite(ideal) || ideal <= 0)
            throw std::domain_error("dynamic bot shot dispersion must be positive");
        double time = aiming_time * std::max(0.0, aim_factor), passed = aiming_elapsed + dt;
        double candidate = aiming_start * std::exp(-passed / std::max(time, 0.1));
        if (candidate < ideal) {
            current_factor = aiming_start = ideal;
            aiming_elapsed = 0;
        } else {
            current_factor = candidate;
            aiming_elapsed = passed;
        }
        dispersion = fully_aimed * current_factor;
    }
    void shot_bloom(double factor, bool final_round) {
        double bloom = final_round ? after_shot : after_in_burst;
        double ideal = std::max(0.0, factor) * std::sqrt(1.0 + motion_squared + bloom * bloom);
        if (current_factor < ideal) {
            current_factor = aiming_start = ideal;
            aiming_elapsed = 0;
            dispersion = fully_aimed * ideal;
        }
    }
    bool rescale(double factor) {
        factor = std::max(0.0, factor);
        if (std::abs(factor - reload_factor) <= 1e-9)
            return false;
        double old = duration(reload_factor), next = duration(factor);
        if (old > 0) {
            if (elapsed < old)
                elapsed = next * clamp(elapsed / old, 0, 1);
            else
                elapsed = next + (elapsed - old);
        }
        reload_factor = factor;
        return true;
    }
    // -1: pending, 0: full, 1: intra-clip. Readiness is deliberately strict.
    int complete(double factor, int available = -1) {
        if (!ready(factor))
            return -1;
        if (!intra && clip == 0)
            clip = available < 0 ? clip_size : std::min(clip_size, std::max(0, available));
        return intra ? 1 : 0;
    }
    void require_full() {
        clip = 0;
        intra = false;
        reload_duration = reload_full;
    }
    int shell(int requested) const { return std::max(0, std::min(requested, shell_count - 1)); }
    bool begin_burst(int count, double factor) {
        if (burst_remaining > 0 || !ready(factor))
            return false;
        if (clip <= 0)
            complete(factor);
        count = std::min(count, clip);
        if (count <= 0)
            return false;
        burst_remaining = count;
        return true;
    }
    bool fire_round(bool final_round) {
        if (burst_remaining <= 0 || clip <= 0 || final_round != (burst_remaining == 1))
            return false;
        --clip;
        --burst_remaining;
        if (!final_round)
            return true;
        elapsed = 0;
        intra = clip > 0;
        if (!intra)
            clip = 0;
        reload_duration = intra ? reload_intra : reload_full;
        return true;
    }
    bool cancel_burst() {
        if (burst_remaining <= 0)
            return false;
        burst_remaining = 0;
        elapsed = 0;
        intra = clip > 0;
        reload_duration = intra ? reload_intra : reload_full;
        return true;
    }
    Value snapshot() const {
        Value v = Value::object();
        v[sf::clip] = Value(clip);
        v[sf::clip_size] = Value(clip_size);
        v["shell_count"] = Value(shell_count);
        v[sf::burst_count] = Value(burst_count);
        v["_burst_remaining"] = Value(burst_remaining);
        v["elapsed"] = Value(elapsed);
        v["reload_factor"] = Value(reload_factor);
        v[sf::reload_duration] = Value(reload_duration);
        v["reload_full"] = Value(reload_full);
        v["reload_intra"] = Value(reload_intra);
        v["reload_kind"] = Value(intra ? "intra" : "full");
        v["movement_dispersion_factor"] = Value(movement_factor);
        v["rotation_dispersion_factor"] = Value(rotation_factor);
        v["turret_dispersion_factor"] = Value(turret_factor);
        v["dispersion"] = Value(dispersion);
        v["fully_aimed_dispersion"] = Value(fully_aimed);
        v["aiming_time"] = Value(aiming_time);
        v["current_dispersion_factor"] = Value(current_factor);
        v["aiming_start_factor"] = Value(aiming_start);
        v["aiming_elapsed"] = Value(aiming_elapsed);
        v["motion_dispersion_squared"] = Value(motion_squared);
        v["after_shot"] = Value(after_shot);
        v["after_shot_in_burst"] = Value(after_in_burst);
        v[sf::burst_interval] = Value(burst_interval);
        return v;
    }
};
struct Ammo {
    std::vector<int> quantities;
    Value categories;
    int loaded = 0, next = 0;
    bool reload_pending = false, plan_pending = true;
    void load(const Value &v) {
        const Value &values = v.get("remaining");
        if (values.kind != Value::Array || values.size() < 1 || values.size() > 5)
            throw std::invalid_argument("kernel ammunition producer");
        quantities.clear();
        categories = v.get("categories");
        for (size_t i = 0; i < values.size(); ++i) {
            int n = static_cast<int>(values[i].exact(-1));
            if (n < 0 || n > 1000)
                throw std::invalid_argument("kernel ammunition quantity");
            quantities.push_back(n);
        }
        loaded = integer(v, "loaded");
        next = integer(v, "next");
        reload_pending = flag(v, "reload_pending");
        plan_pending = flag(v, "plan_pending", true);
        if (loaded < 0 || next < 0 || loaded >= static_cast<int>(quantities.size()) ||
            next >= static_cast<int>(quantities.size()))
            throw std::invalid_argument("kernel ammunition selection");
    }
    int fallback() const {
        int first = -1;
        for (size_t i = 0; i < quantities.size(); ++i)
            if (quantities[i] > 0) {
                if (first < 0)
                    first = static_cast<int>(i);
                if (categories.get(std::to_string(i)).text() == "standard")
                    return static_cast<int>(i);
            }
        return first < 0 ? 0 : first;
    }
    int available(int requested) const {
        return requested >= 0 && requested < static_cast<int>(quantities.size()) &&
                       quantities[requested] > 0
                   ? requested
                   : fallback();
    }
    bool stage(int requested, bool ready, bool full) {
        if (!ready)
            return false;
        bool changed = false;
        if (reload_pending) {
            if (full) {
                int selected = available(next);
                if (selected != loaded) {
                    loaded = selected;
                    changed = true;
                }
            }
            reload_pending = false;
            plan_pending = true;
        }
        if (plan_pending) {
            int selected = available(requested);
            if (selected != next) {
                next = selected;
                changed = true;
            }
            plan_pending = false;
        }
        return changed;
    }
    bool can_fire(bool continuing = false) const {
        return loaded >= 0 && loaded < static_cast<int>(quantities.size()) &&
               quantities[loaded] > 0 && (continuing || !reload_pending);
    }
    bool consume(bool continuing = false) {
        if (!can_fire(continuing))
            return false;
        --quantities[loaded];
        next = available(next);
        reload_pending = true;
        plan_pending = false;
        return true;
    }
    int planned() const {
        return next >= 0 && next < static_cast<int>(quantities.size()) ? quantities[next] : 0;
    }
    bool requires_full() const {
        if (loaded < 0 || loaded >= static_cast<int>(quantities.size()) || quantities[loaded] > 0)
            return false;
        for (int n : quantities)
            if (n > 0)
                return true;
        return false;
    }
    Value snapshot() const {
        Value v = Value::object(), q = Value::array();
        for (int n : quantities)
            q.append(Value(n));
        v["remaining"] = q;
        v["categories"] = categories;
        v["shell_count"] = Value(static_cast<int>(quantities.size()));
        v["loaded"] = Value(loaded);
        v["next"] = Value(next);
        v["reload_pending"] = Value(reload_pending);
        v["plan_pending"] = Value(plan_pending);
        return v;
    }
};
struct Subshot {
    int seq, group, index, count, shell;
    bool final;
    double offset;
};
struct Burst {
    bool active = false;
    int group = 0, count = 0, next = 0, shell = 0;
    double interval = 0, left = 0;
    void load(const Value &v) {
        active = flag(v, "active");
        group = integer(v, "group_seq");
        count = integer(v, "count");
        next = integer(v, "next_index");
        shell = integer(v, sf::shell_index);
        interval = field(v, "interval");
        left = field(v, "time_left");
    }
    bool start(int sequence, int wanted, double spacing, int selected) {
        if (active || sequence <= 0 || wanted < 1 || wanted > 64 || selected < 0 ||
            !std::isfinite(spacing) || spacing < 0 || spacing > 10 || (wanted > 1 && spacing <= 0))
            return false;
        active = true;
        group = sequence;
        count = wanted;
        next = 0;
        interval = wanted > 1 ? spacing : 0;
        left = 0;
        shell = selected;
        return true;
    }
    std::vector<Subshot> advance(double dt) {
        std::vector<Subshot> due;
        if (!active || !std::isfinite(dt))
            return due;
        dt = std::max(0.0, dt);
        double offset = std::max(0.0, left);
        left -= dt;
        while (active && left <= 1e-9) {
            int index = next;
            due.push_back(Subshot{group + index, group, index, count, shell, index + 1 >= count,
                                  std::min(dt, offset)});
            ++next;
            if (next >= count) {
                active = false;
                left = 0;
            } else {
                left += interval;
                offset += interval;
            }
        }
        return due;
    }
    bool cancel(int launched = -1) {
        bool changed = active || count > 0;
        if (launched < 0)
            launched = next;
        if (launched < 0 || launched > next)
            return false;
        active = false;
        next = launched;
        if (launched == 0) {
            group = count = shell = 0;
            interval = 0;
        }
        left = 0;
        return changed;
    }
    Value snapshot() const {
        Value v = Value::object();
        v["active"] = Value(active);
        v["group_seq"] = Value(group);
        v["count"] = Value(count);
        v["next_index"] = Value(next);
        v[sf::shell_index] = Value(shell);
        v["interval"] = Value(interval);
        v["time_left"] = Value(left);
        return v;
    }
};
} // namespace offline_kernel
#endif
