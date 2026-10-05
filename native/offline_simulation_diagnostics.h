#ifndef WOT_OFFLINE_SIMULATION_DIAGNOSTICS_H
#define WOT_OFFLINE_SIMULATION_DIAGNOSTICS_H

#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <utility>

namespace offline_simulation {
namespace diagnostics {

// This observer is local to the synchronous caller's OS thread. It retains
// neither Python/engine objects nor simulation state, and never follows work
// onto the asynchronous pool. Disabled scopes do not read the clock.
enum class EntryCode {
    translate, slide, rotate,
    nav_open, nav_submit, nav_poll, nav_answer, nav_cancel, nav_close,
    vis_open, vis_update, vis_submit, vis_poll, vis_reduce, vis_cancel, vis_close,
    contact_roster, world_run, nav_query_filter, nav_query_run,
    sim_open, sim_close, sim_lifetime,
    sim_control_configure, sim_control_update, sim_control_begin,
    sim_control_contacts, sim_control_humans, sim_control_observations,
    sim_control_team_contacts, sim_control_finish, sim_control_forget,
    sim_control_drive, sim_control_driver_event, sim_control_driver_restore,
    sim_control_traffic, sim_control_traffic_state, sim_control_snapshot,
    sim_control_radio, sim_control_radio_summaries, sim_control_radio_actors,
    sim_control_source_still,
    sim_motion_configure, sim_motion_install, sim_motion_patch, sim_motion_begin,
    sim_motion_prepare, sim_motion_advance, sim_motion_settle,
    sim_motion_snapshot, sim_motion_patch_fields, sim_motion_after_weapon,
    sim_motion_descriptor,
    sim_weapon_install, sim_weapon_snapshot, sim_weapon_remove,
    sim_weapon_command, sim_weapon_aim, sim_weapon_ballistic,
    sim_weapon_commit, sim_weapon_pending, sim_weapon_ack,
    sim_navigation_install, sim_navigation_snapshot, sim_navigation_remove,
    sim_navigation_command, sim_navigation_grid_snapshot,
    sim_navigation_bot_snapshot, sim_navigation_grid_command,
    sim_navigation_async_snapshot, sim_navigation_receipts,
    sim_navigation_order_events,
    motion_predrive_sweep, contact_solve, contact_pair_build, contact_solve_passes,
    Count
};

enum class CallbackCode { Motion, ControlSight, ControlDriver, Navigation,
                          World, NavigationQuery, Count };
enum class Phase { Parse, Body, Pack };
enum class CounterCode { ContactActors, ContactPairs, ContactIsolated,
    ContactIslands, ContactLargestActors, ContactLargestPairs, Count };

constexpr std::size_t opcode_slots = 66; // -1, 0..63, and other (-2).
constexpr std::size_t maximum_rows = 512;
constexpr unsigned maximum_depth = 64;

struct EntryRow {
    const char *name = nullptr;
    int opcode = -1;
    std::uint64_t calls = 0, errors = 0, reentrant_calls = 0;
    double inclusive = 0., parse = 0., body = 0., pack = 0.;
    double callbacks = 0., child_native = 0.;
};
struct CallbackRow {
    const char *name = nullptr;
    int opcode = -1;
    std::uint64_t calls = 0, rows = 0, errors = 0;
    double inclusive = 0., self = 0., nested_native = 0.;
};
struct CounterRow {
    const char *name = nullptr;
    std::uint64_t observations = 0, sum = 0, maximum = 0;
};

inline double steady_seconds() noexcept {
    return std::chrono::duration<double>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

struct Scope;
class Ledger {
public:
    using Clock = double (*)();
    bool active = false;
    Scope *top = nullptr;
    std::int64_t owner = 0, frame = 0;
    unsigned depth = 0, deepest = 0;
    std::uint64_t reentries = 0;
    double started = 0., elapsed = 0.;
    std::array<EntryRow, static_cast<std::size_t>(EntryCode::Count) * opcode_slots> entries{};
    std::array<CallbackRow, static_cast<std::size_t>(CallbackCode::Count) * opcode_slots> callbacks{};
    std::array<std::size_t, maximum_rows> entry_indices{}, callback_indices{};
    std::size_t entry_count = 0, callback_count = 0;
    EntryRow entry_overflow{};
    CallbackRow callback_overflow{};
    std::array<CounterRow, static_cast<std::size_t>(CounterCode::Count)> counters{};

private:
    Clock clock_;
    double last_clock_ = 0.;

public:
    explicit Ledger(Clock clock = steady_seconds) noexcept : clock_(clock) {}
    Ledger(const Ledger &) = delete;
    Ledger &operator=(const Ledger &) = delete;

    double now() noexcept {
        // A diagnostic clock failure freezes accounting instead of changing
        // the business call or replacing an existing Python exception.
        try {
            const double value = clock_();
            if (std::isfinite(value) && value >= last_clock_)
                last_clock_ = value;
        } catch (...) {}
        return last_clock_;
    }
    bool begin(std::int64_t handle, std::int64_t frame_id) noexcept {
        if (active || top || handle <= 0 || frame_id < 0)
            return false;
        for (std::size_t i = 0; i < entry_count; ++i)
            entries[entry_indices[i]] = EntryRow();
        for (std::size_t i = 0; i < callback_count; ++i)
            callbacks[callback_indices[i]] = CallbackRow();
        entry_count = callback_count = 0;
        entry_overflow = EntryRow();
        callback_overflow = CallbackRow();
        entry_overflow.name = "diagnostic.entry_overflow";
        callback_overflow.name = "diagnostic.callback_overflow";
        for (auto &row : counters) row = CounterRow();
        owner = handle;
        frame = frame_id;
        depth = deepest = 0;
        reentries = 0;
        elapsed = 0.;
        started = now();
        active = true;
        return true;
    }
    bool end(std::int64_t handle) noexcept;
    static int normalized_opcode(int opcode) noexcept {
        return opcode >= -1 && opcode <= 63 ? opcode : -2;
    }
    static std::size_t slot(int opcode) noexcept {
        return opcode >= -1 && opcode <= 63 ? std::size_t(opcode + 1) : 65;
    }
    EntryRow &entry(EntryCode code, const char *name, int opcode) noexcept {
        const auto index = static_cast<std::size_t>(code) * opcode_slots + slot(opcode);
        if (index >= entries.size()) return entry_overflow;
        auto &row = entries[index];
        if (!row.name) {
            if (entry_count == maximum_rows) return entry_overflow;
            row.name = name;
            row.opcode = normalized_opcode(opcode);
            entry_indices[entry_count++] = index;
        }
        return row;
    }
    CallbackRow &callback(CallbackCode code, const char *name, int opcode) noexcept {
        const auto index = static_cast<std::size_t>(code) * opcode_slots + slot(opcode);
        if (index >= callbacks.size()) return callback_overflow;
        auto &row = callbacks[index];
        if (!row.name) {
            if (callback_count == maximum_rows) return callback_overflow;
            row.name = name;
            row.opcode = normalized_opcode(opcode);
            callback_indices[callback_count++] = index;
        }
        return row;
    }
};

inline Ledger &ledger() noexcept {
    static thread_local Ledger value;
    return value;
}

inline void count(CounterCode code, const char *name, std::uint64_t value) noexcept {
    auto &capture = ledger();
    if (!capture.active) return;
    auto &row = capture.counters[static_cast<std::size_t>(code)];
    row.name = name;
    ++row.observations;
    row.sum += value;
    if (value > row.maximum) row.maximum = value;
}

struct Scope {
    Ledger *owner = nullptr;
    Scope *parent = nullptr;
    bool callback = false, failed = false, reentrant = false;
    Phase phase = Phase::Parse;
    double started = 0., resumed = 0.;
    double parse = 0., body = 0., pack = 0., children_callback = 0., children_native = 0.;

    explicit Scope(Ledger &value, bool is_callback, bool track_reentry = true) noexcept : callback(is_callback) {
        if (!value.active) return;
        if (value.depth >= maximum_depth) {
            ++value.entry_overflow.calls;
            ++value.entry_overflow.errors;
            return;
        }
        owner = &value;
        parent = value.top;
        const double stamp = value.now();
        if (parent) parent->pause(stamp);
        started = resumed = stamp;
        if (!callback && track_reentry) {
            for (auto *at = parent; at; at = at->parent)
                if (at->callback) { reentrant = true; break; }
            value.reentries += reentrant;
        }
        value.top = this;
        ++value.depth;
        if (value.depth > value.deepest) value.deepest = value.depth;
    }
    Scope(const Scope &) = delete;
    Scope &operator=(const Scope &) = delete;
    void pause(double stamp) noexcept {
        const double elapsed = stamp - resumed;
        if (phase == Phase::Parse) parse += elapsed;
        else if (phase == Phase::Body) body += elapsed;
        else pack += elapsed;
    }
    void set_phase(Phase value) noexcept {
        if (!owner || owner->top != this || phase == value) return;
        const double stamp = owner->now();
        pause(stamp);
        resumed = stamp;
        phase = value;
    }
    double finish() noexcept {
        if (!owner) return 0.;
        const double stamp = owner->now();
        pause(stamp);
        const double elapsed = stamp - started;
        owner->top = parent;
        --owner->depth;
        if (parent) {
            parent->resumed = stamp;
            if (callback) parent->children_callback += elapsed;
            else parent->children_native += elapsed;
        }
        return elapsed;
    }
};

inline bool Ledger::end(std::int64_t handle) noexcept {
    if (!active || handle != owner) return false;
    elapsed = now() - started;
    active = false; // Serialization is outside the captured interval.
    if (top) {
        // A synchronous engine callback may close the round and call end().
        // Discard that incomplete capture and detach every active stack node;
        // later unwinding cannot resurrect it or write into the next capture.
        for (auto *scope = top; scope; scope = scope->parent)
            scope->owner = nullptr;
        top = nullptr;
        depth = 0;
        return false;
    }
    return true;
}

class Entry : public Scope {
    EntryCode code_;
    const char *name_;
    int opcode_ = -1;
public:
    Entry(EntryCode code, const char *name, Ledger &value = ledger()) noexcept
        : Scope(value, false, code < EntryCode::motion_predrive_sweep), code_(code), name_(name) {}
    ~Entry() noexcept {
        if (!owner) return;
        const double elapsed = finish();
        auto &row = owner->entry(code_, name_, opcode_);
        ++row.calls;
        row.errors += failed;
        row.reentrant_calls += reentrant;
        row.inclusive += elapsed;
        row.parse += parse;
        row.body += body;
        row.pack += pack;
        row.callbacks += children_callback;
        row.child_native += children_native;
    }
    void opcode(int value) noexcept { opcode_ = value; }
    void fail() noexcept { failed = true; }
    void input() noexcept { set_phase(Phase::Parse); }
    void output() noexcept { set_phase(Phase::Pack); }
    void compute() noexcept { set_phase(Phase::Body); }
    template <class Function, class... Args>
    decltype(auto) run(Function &&function, Args &&... args) {
        // Argument conversion happens before entering run(). The measured
        // body includes C++ allocation but excludes every profiled callback.
        compute();
        struct Leave {
            Entry &entry;
            ~Leave() noexcept { entry.output(); }
        } leave{*this};
        return std::invoke(std::forward<Function>(function), std::forward<Args>(args)...);
    }
};

class Callback : public Scope {
    CallbackCode code_;
    const char *name_;
    int opcode_;
    std::uint64_t rows_;
public:
    Callback(CallbackCode code, const char *name, int opcode, std::uint64_t rows,
             Ledger &value = ledger()) noexcept
        : Scope(value, true), code_(code), name_(name), opcode_(opcode), rows_(rows) {}
    ~Callback() noexcept {
        if (!owner) return;
        const double elapsed = finish();
        auto &row = owner->callback(code_, name_, opcode_);
        ++row.calls;
        row.rows += rows_;
        row.errors += failed;
        row.inclusive += elapsed;
        row.self += parse + body + pack;
        row.nested_native += children_native;
    }
    void fail() noexcept { failed = true; }
};

// Frontier request construction / response conversion belongs to bridge
// packing / parsing, even when reached from the middle of a native body.
class PhaseScope {
    Scope *scope_ = nullptr;
    Phase previous_ = Phase::Body;
public:
    explicit PhaseScope(Phase phase, Ledger &value = ledger()) noexcept {
        if (!value.active || !value.top || value.top->callback) return;
        scope_ = value.top;
        previous_ = scope_->phase;
        scope_->set_phase(phase);
    }
    ~PhaseScope() noexcept { if (scope_) scope_->set_phase(previous_); }
    void input() noexcept { if (scope_) scope_->set_phase(Phase::Parse); }
};

} // namespace diagnostics
} // namespace offline_simulation

#define NATIVE_PROFILE_ENTRY(name) \
    offline_simulation::diagnostics::Entry profile( \
        offline_simulation::diagnostics::EntryCode::name, #name)

#define NATIVE_PROFILE_STAGE(name) \
    offline_simulation::diagnostics::Entry profile( \
        offline_simulation::diagnostics::EntryCode::name, "stage." #name); \
    profile.compute()

#endif
