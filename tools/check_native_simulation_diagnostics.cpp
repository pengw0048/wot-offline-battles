// Deterministic accounting checks; no engine calls or performance assertions.
#include "../native/offline_simulation_diagnostics.h"
#include <cassert>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <thread>
#include <vector>

namespace d = offline_simulation::diagnostics;
namespace {
double stamp = 0.;
unsigned reads = 0;
bool clock_failure = false;
double clock_value() {
    ++reads;
    if (clock_failure) throw std::runtime_error("diagnostic clock unavailable");
    return stamp;
}
}

int main() {
    d::Ledger capture(clock_value);
    {
        d::Entry disabled(d::EntryCode::world_run, "world_run", capture);
        disabled.compute(); disabled.input(); disabled.output();
        d::Callback callback(d::CallbackCode::World, "world", 3, 7, capture);
    }
    assert(reads == 0 && capture.entry_count == 0);
    assert(capture.begin(7, 19));
    assert(!capture.begin(7, 20));
    {
        d::Entry outer(d::EntryCode::world_run, "world_run", capture);
        stamp = 2.; outer.compute();
        stamp = 5.;
        {
            d::Callback callback(d::CallbackCode::World, "world", 3, 7, capture);
            stamp = 7.;
            {
                d::Entry nested(d::EntryCode::sim_lifetime, "sim_lifetime", capture);
                stamp = 8.; nested.compute();
                stamp = 10.; nested.output();
                stamp = 11.;
            }
            stamp = 14.;
        }
        stamp = 17.; outer.output();
        stamp = 20.;
    }
    stamp = 22.;
    assert(!capture.end(8) && capture.active);
    assert(capture.end(7));
    const auto &outer = capture.entry(d::EntryCode::world_run, "world_run", -1);
    const auto &nested = capture.entry(d::EntryCode::sim_lifetime, "sim_lifetime", -1);
    const auto &callback = capture.callback(d::CallbackCode::World, "world", 3);
    assert(outer.calls == 1 && outer.inclusive == 20.);
    assert(outer.parse == 2. && outer.body == 6. && outer.pack == 3.);
    assert(outer.callbacks == 9. && outer.child_native == 0.);
    assert(nested.inclusive == 4. && nested.parse == 1. && nested.body == 2. && nested.pack == 1.);
    assert(nested.reentrant_calls == 1 && capture.reentries == 1 && capture.deepest == 3);
    assert(callback.inclusive == 9. && callback.self == 5. && callback.nested_native == 4. && callback.rows == 7);
    assert(outer.parse + outer.body + outer.pack + nested.inclusive + callback.self == 20.);
    assert(capture.elapsed == 22.);

    // A callback ending its owner discards the incomplete stack. Old scope
    // destruction must not mutate a capture begun after that callback.
    assert(capture.begin(7, 20));
    {
        d::Entry abandoned(d::EntryCode::world_run, "world_run", capture);
        d::Callback interrupted(d::CallbackCode::World, "world", 3, 1, capture);
        assert(!capture.end(7) && !capture.active && !capture.top);
        assert(capture.begin(8, 21));
    }
    assert(capture.end(8) && capture.entry_count == 0 && capture.callback_count == 0);

    assert(capture.begin(7, 22));
    {
        d::Entry entry(d::EntryCode::sim_weapon_command, "sim_weapon_command", capture);
        entry.opcode(19);
        try { entry.run([]() -> int { throw std::runtime_error("business error"); }); }
        catch (const std::runtime_error &error) {
            assert(std::string(error.what()) == "business error"); entry.fail();
        }
    }
    assert(capture.end(7));
    const auto &failed = capture.entry(d::EntryCode::sim_weapon_command, "sim_weapon_command", 19);
    assert(failed.errors == 1 && failed.calls == 1);

    assert(capture.begin(7, 23));
    {
        d::Callback callback_scope(d::CallbackCode::World, "world", 3, 1, capture);
        d::Entry stage(d::EntryCode::contact_solve, "stage.contact_solve", capture);
        stage.compute();
    }
    assert(capture.end(7) && capture.reentries == 0);

    assert(capture.begin(7, 24));
    for (unsigned code = 0; code < 9; ++code) {
        for (int opcode = -1; opcode < 64; ++opcode) {
            d::Entry entry(static_cast<d::EntryCode>(code), "bounded", capture);
            entry.opcode(opcode);
        }
    }
    assert(capture.end(7));
    assert(capture.entry_count == d::maximum_rows && capture.entry_overflow.calls == 585 - 512);
    assert(capture.begin(7, 25) && capture.entry_count == 0 && !capture.entry_overflow.calls);
    {
        std::vector<std::unique_ptr<d::Entry>> stack;
        for (unsigned i = 0; i <= d::maximum_depth; ++i)
            stack.emplace_back(new d::Entry(d::EntryCode::world_run, "world_run", capture));
        while (!stack.empty()) stack.pop_back();
    }
    assert(capture.end(7) && capture.deepest == d::maximum_depth);
    assert(capture.entry_overflow.errors == 1);

    stamp = 50.; assert(capture.begin(7, 26));
    stamp = 49.; assert(capture.now() == 50.);
    stamp = std::numeric_limits<double>::quiet_NaN(); assert(capture.now() == 50.);
    clock_failure = true; assert(capture.now() == 50.);
    assert(capture.end(7) && capture.elapsed == 0.);
    clock_failure = false;

    // Captures never propagate to worker threads.
    assert(d::ledger().begin(3, 1));
    std::thread other([] {
        assert(!d::ledger().active);
        assert(d::ledger().begin(4, 2));
        assert(d::ledger().end(4));
    });
    other.join();
    assert(d::ledger().active && d::ledger().end(3));
    std::cout << "Native diagnostic accounting checks passed\n";
}
