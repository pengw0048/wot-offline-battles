#ifndef WOT_OFFLINE_SIMULATION_H
#define WOT_OFFLINE_SIMULATION_H

#include "offline_simulation_types.h"
#include "offline_simulation_control.h"
#include "offline_simulation_motion.h"
#include "offline_simulation_weapons.h"
#include "offline_simulation_navigation.h"
#include <atomic>
#include <memory>

namespace offline_simulation {

// One owner for the native state of one authority lifetime. No member stores
// a Python object or an engine pointer. Engine adapters exist only for the
// duration of a synchronous main-thread call.
struct Context {
    const std::int64_t round;
    const std::int64_t generation;
    std::atomic<bool> closed{false};
    control::Store control;
    motion::Store motion;
    weapons::Store weapons;
    navigation::Store navigation;

    Context(std::int64_t round_id, std::int64_t authority_generation)
        : round(round_id), generation(authority_generation) {}
    Context(const Context &) = delete;
    Context &operator=(const Context &) = delete;
};

// Handles are process-unique and never reused. A synchronous engine callback
// can close the round; callers retain their shared context while unwinding,
// but must not commit further state after observing closed.
std::int64_t open(std::int64_t round, std::int64_t generation);
std::shared_ptr<Context> find(std::int64_t handle);
void close(std::int64_t handle);

}  // namespace offline_simulation

#endif
