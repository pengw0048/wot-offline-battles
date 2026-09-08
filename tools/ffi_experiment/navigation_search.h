#ifndef OFFLINE_EXPERIMENT_NAVIGATION_SEARCH_H
#define OFFLINE_EXPERIMENT_NAVIGATION_SEARCH_H
#include "navigation_graph.h"
#include <unordered_set>

// The complete navigator calls this directly; there is no Python conversion or
// FFI dispatch between a paid search step and native route finalization.
class OfflineNavigationSearch {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    OfflineNavigationSearch(std::shared_ptr<OfflineNavGraph> graph,int start,int goal,
                            int maximum,double now,bool prefer);
    ~OfflineNavigationSearch();
    void inputs(const std::vector<std::pair<double,double> > &avoid,
                const std::unordered_map<uint64_t,double> &local,
                const std::unordered_set<uint64_t> &hard,bool hard_present);
    void step();
    bool done() const;
    int expansions() const;
    const std::vector<int> &path() const;
};
#endif
