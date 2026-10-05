#include "offline_simulation.h"

#include <limits>
#include <map>
#include <mutex>
#include <stdexcept>

namespace offline_simulation {
namespace {

class Contexts {
    std::mutex mutex_;
    std::map<std::int64_t, std::shared_ptr<Context>> values_;
    std::int64_t next_ = 1;

public:
    std::int64_t open(std::int64_t round, std::int64_t generation) {
        if (round < 0 || generation < 0)
            throw std::invalid_argument("Invalid simulation lifetime");
        auto context = std::make_shared<Context>(round, generation);
        std::lock_guard<std::mutex> lock(mutex_);
        // The exact x86 bridge returns a Python int. Retire at that boundary
        // instead of wrapping a handle and reviving an earlier lifetime.
        if (next_ > std::numeric_limits<std::int32_t>::max())
            throw std::overflow_error("Simulation handles exhausted");
        const auto handle = next_++;
        values_.emplace(handle, std::move(context));
        return handle;
    }

    std::shared_ptr<Context> find(std::int64_t handle) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto item = values_.find(handle);
        if (item == values_.end() || item->second->closed.load())
            return {};
        return item->second;
    }

    void close(std::int64_t handle) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto item = values_.find(handle);
        if (item == values_.end()) return;
        item->second->closed.store(true);
        values_.erase(item);
    }
};

Contexts &contexts() {
    // The extension lives for the game process. Do not destroy native state
    // from a Windows DLL-detach callback; round teardown explicitly closes it.
    static auto *value = new Contexts;
    return *value;
}

}  // namespace

std::int64_t open(std::int64_t round, std::int64_t generation) {
    return contexts().open(round, generation);
}

std::shared_ptr<Context> find(std::int64_t handle) {
    return contexts().find(handle);
}

void close(std::int64_t handle) { contexts().close(handle); }

}  // namespace offline_simulation
