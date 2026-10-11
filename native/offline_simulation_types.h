#ifndef WOT_OFFLINE_SIMULATION_TYPES_H
#define WOT_OFFLINE_SIMULATION_TYPES_H

#include <array>
#include <cstdint>

namespace offline_simulation {

using Point = std::array<double, 3>;

enum class ActorKind : std::uint8_t { Human = 0, Bot = 1 };

struct ActorKey {
    ActorKind kind = ActorKind::Bot;
    std::int64_t id = 0;
    bool operator==(const ActorKey &other) const {
        return kind == other.kind && id == other.id;
    }
    bool operator!=(const ActorKey &other) const { return !(*this == other); }
    bool operator<(const ActorKey &other) const {
        return kind != other.kind ? kind < other.kind : id < other.id;
    }
};

// A context belongs to exactly one round and authority lifetime. Actor input
// revisions are separate from the elapsed simulation clock and query samples.
struct TickToken {
    std::int64_t round = 0;
    std::int64_t generation = 0;
    std::int64_t sequence = 0;
    std::int64_t sample_time_us = 0;
};

struct Pose {
    Point position = {};
    double yaw = 0.;
    double pitch = 0.;
    double roll = 0.;
};

// Pending and failure are not physical obstructions or successful proofs.
// Each stage applies its existing rules for retaining a previous receipt.
enum class QueryStatus : std::uint8_t { Clear, Blocked, Pending, Failed, DetectionComplete };

}  // namespace offline_simulation

#endif
