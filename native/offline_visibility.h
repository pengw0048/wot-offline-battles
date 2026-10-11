#ifndef WOT_OFFLINE_VISIBILITY_H
#define WOT_OFFLINE_VISIBILITY_H
#include "native_visibility_core.h"
#include <cstdint>
#include <utility>
#include <vector>
#include <array>
#include <functional>
namespace offline_visibility {
struct Update {
    std::vector<std::pair<std::size_t,native_visibility::Volume>> instances;
    std::vector<std::pair<native_visibility::Cell,std::vector<std::size_t>>> cells;
    std::set<std::size_t> inactive;
};
struct Completion {
    int64_t job_id=0;
    unsigned status=0; // done, cancelled, failed
    std::vector<native_visibility::Ray> rays;
    double worker_seconds=0.;
};
int64_t open(native_visibility::FoliageSnapshot snapshot);
bool update(int64_t context,Update changes);
bool submit(int64_t context,int64_t job_id,native_visibility::PairInput pair);
std::vector<Completion> poll(int64_t context);
bool reduce(int64_t context,int64_t job_id,const std::vector<std::uint8_t> &clear,
            native_visibility::VisibilityResult &result);
void cancel(int64_t context,const std::vector<int64_t> &jobs);
void close(int64_t context);
// Same-thread frontier. All retained fields are copied numeric data; the ray
// capability is borrowed for one synchronous call and never reaches a worker.
using ActorKey = std::array<int64_t,2>;
struct FrontierActor {
    ActorKey key{};
    uint64_t identity=0;
    bool target_available=true;
    native_visibility::Checkpoints checkpoints;
    native_visibility::Pose observer_pose, target_pose;
};
struct FrontierReply {
    unsigned status=3; // clear=0, blocked=1, pending=2, failed=3
    bool has_detection=false, detected=false;
    double foliage=0., sampled_at=0.;
};
// A completed detection has no implied LOS/foliage result for another consumer.
struct FrontierDetectionReply {
    unsigned status=3; // completed=0, pending=2, failed=3
    bool detected=false;
    double sampled_at=0.;
};
struct FrontierSnapshot {
    uint64_t submitted=0,completed=0,cancelled=0,pending=0,requests=0;
    double worker_seconds=0.,max_completion_age=0.;
    // identity, fire, detection, foliage, age, unrequested, backend.
    std::array<uint64_t,7> reasons{};
};
FrontierSnapshot frontier_snapshot(int64_t context);
using RayOracle = std::function<bool(const native_visibility::Ray &)>;
using PhaseOracle = std::function<uint64_t()>;
bool frontier_frame(int64_t context,std::vector<FrontierActor> actors,
                    uint64_t observer_phase,double now);
bool frontier_actor(int64_t context,FrontierActor actor);
FrontierReply frontier_sight(int64_t context,const ActorKey &observer,
    const ActorKey &target,double now,int64_t fire_sequence,
    const native_visibility::DetectionInputs &detection,const RayOracle &ray,
    const PhaseOracle &phase=PhaseOracle());
FrontierDetectionReply frontier_detect(int64_t context,const ActorKey &observer,
    const ActorKey &target,double now,int64_t fire_sequence,
    const native_visibility::DetectionInputs &detection,const RayOracle &ray,
    const PhaseOracle &phase=PhaseOracle(),bool world_query=true);

}
#endif
