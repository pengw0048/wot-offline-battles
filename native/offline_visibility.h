#ifndef WOT_OFFLINE_VISIBILITY_H
#define WOT_OFFLINE_VISIBILITY_H
#include "native_visibility_core.h"
#include <cstdint>
#include <utility>
#include <vector>
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
}
#endif
