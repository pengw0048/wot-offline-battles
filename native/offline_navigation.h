#ifndef WOT_OFFLINE_NAVIGATION_H
#define WOT_OFFLINE_NAVIGATION_H
#include <array>
#include <cstdint>
#include <utility>
#include <vector>
namespace offline_navigation {
using Point = std::array<double, 3>;
using Cell = std::array<int, 2>;
struct EdgePenalty { Cell first, second; double value; };
struct MapInput {
    double origin_x = 0., origin_z = 0., cell_size = 4.;
    int width = 0, height = 0;
    std::vector<double> heights; // Metres; NaN means no baked routing support.
    std::vector<unsigned> links, hazards;
    bool has_bounds = false;
    std::array<double, 4> bounds = {};
    double max_grade = .30, heuristic_weight = 1.70;
};
struct SearchInput {
    int64_t job_id = 0, hull_revision = 0;
    Point start = {}, goal = {};
    unsigned max_expansions = 4096;
    bool prefer_clearance = false;
    std::vector<Point> avoid_points;
    std::vector<EdgePenalty> edge_penalties, world_penalties;
    std::vector<std::pair<Cell, Cell>> hard_edges;
    std::vector<Cell> native_review_cells;
};
struct Query {
    int64_t query_id = 0, job_id = 0;
    Point start = {}, end = {};
};
struct Completion {
    int64_t job_id = 0, hull_revision = 0;
    // 0: done (including an honest empty path), 1: cancelled, 2: failed.
    unsigned status = 0, expanded = 0;
    double worker_seconds = 0.;
    std::vector<Point> path;
};
struct Poll {
    std::vector<Completion> completed;
    std::vector<Query> queries;
    // pending, running, waiting, ready queries, completed undrained, threads.
    std::array<uint64_t, 6> stats = {};
};
int64_t open(MapInput map);
bool submit(int64_t context, SearchInput search);
Poll poll(int64_t context);
void answer(int64_t context, const std::vector<std::pair<int64_t, bool>> &answers);
void cancel(int64_t context, const std::vector<int64_t> &jobs);
void close(int64_t context);
}
#endif
