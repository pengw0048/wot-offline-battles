#ifndef OFFLINE_EXPERIMENT_NAVIGATION_GRAPH_H
#define OFFLINE_EXPERIMENT_NAVIGATION_GRAPH_H

#include <cmath>
#include <cstdint>
#include <memory>
#include <unordered_map>
#include <utility>
#include <vector>

// Shared immutable map storage for A*, corridor tests, route state and motion.
// Handles own C++ data only; neither a Python object nor a caller buffer lives
// here. Index edges are used only by the search, whose endpoints are in bounds.
struct OfflineNavGraph {
    int width, height;
    double cell, ox, oz, grade, heuristic, clearance, shallow, diagonal;
    std::vector<double> heights;
    std::vector<unsigned char> links, hazards;
    std::unordered_map<uint64_t, std::pair<double, double> > failed;
    std::unordered_map<uint64_t, double> hulls;
    std::vector<uint64_t> expired;
    bool valid(int i) const {
        return i >= 0 && i < width * height && std::isfinite(heights[i]);
    }
    double distance(int a, int b) const {
        double dx = a % width - b % width, dz = a / width - b / width;
        return std::sqrt(dx * dx + dz * dz);
    }
};
std::shared_ptr<OfflineNavGraph> offline_navigation_graph(int handle);
#endif
